"""The forward paper test's findings of the audit of 22 Nov 2026 (area "paper"), each pinned by its own tests."""

from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store
from tests.synth import addr
from tests.test_paper_landing import T0, _copy_coin

COPY = Trigger("kol", sol=2.0)
COPY_EXIT = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False, watch_secs=1.3,
                       copy_exit_pct=0.5)  # fmt: skip

# -- paper#1: a freeze's forward record starts after the tape its lab read ------------------------------------------
# `paper freeze` takes its `now` first, then waits for a lab slot, and only then is the tape the lab reads resolved
# (the hourly copy, or the live store when the copy is stale or lacks a deploy's schema). That tape can end after
# `now`: its newest moments, the lab's test part where the rule's lower bound was found, were then scored again as
# forward trades, which are meant to be on tape recorded after the freeze alone.
FREEZE = T0 + 6 * 3600  # when `paper freeze` started (its `now`)


def _hindsight_store(lab_end: int) -> Store:
    store = Store(":memory:")
    for k, ts in enumerate((FREEZE - 3000, FREEZE + 600, FREEZE + 3000)):  # a leader's buy before, during and after
        _copy_coin(store, k, ts)
        store.set_label(addr(f"pl-leader-{k}"), f"leader-{k}", kind="kol")
    store.set_coverage("tape", {"spans": [[T0, lab_end]]})  # the tape as the lab finds it once it holds the lock
    return store


def _rank_seen(seen: dict):
    def rank(data, cfg, top=10, **_):
        seen["lab"] = {(r["mint"], int(r["ts"])) for r in data["rows"]}
        return [{"trigger": COPY.label(), "trigger_spec": COPY.__dict__, "exit": COPY_EXIT.label(),
                 "exit_params": COPY_EXIT.to_dict(), "landing": (0, "+6"), "moments": 2, "exit_moments": 2,
                 "per_hour": 0.3, "train_mean": 0.01,
                 "test": {"n": 2, "mean": 0.02, "ev_lower": 0.01, "win_rate": 1.0, "total": 0.04}}]  # fmt: skip

    return rank


def _forward(store: Store, sid: str) -> set[tuple[str, int]]:
    return {(r["mint"], int(r["trigger_ts"])) for r in store.paper_trades(sid)}


def test_the_forward_record_never_holds_a_moment_the_lab_ranked_the_rule_on(monkeypatch):
    """The auditor's case: the lab, once it held the lock, read a tape that ends half an hour after the freeze began.
    The record starts after that tape, and says which window its lab read."""
    lab_end = FREEZE + 1800
    store = _hindsight_store(lab_end)
    seen: dict = {}
    monkeypatch.setattr(paper, "rank", _rank_seen(seen))
    out = paper.freeze(store, Config(), top=4, now=FREEZE, triggers=[COPY], tape=lambda: store)
    (rule,) = out["frozen"]
    assert out["window"]["until_ts"] == lab_end and rule["lab"]["window"]["until_ts"] == lab_end
    assert rule["from_ts"] == lab_end + 1  # the lab's tape holds its last second: the record starts after it
    store.set_coverage("tape", {"spans": [[T0, FREEZE + 7200]]})  # the tape goes on
    paper.run(store, Config(), now=FREEZE + 7200)
    forward = _forward(store, rule["id"])
    assert forward  # the rule traded forward, on the leader's buy after the lab's tape
    assert not forward & seen["lab"], f"forward trades the lab already scored: {sorted(forward & seen['lab'])}"


def test_a_paper_run_during_the_wait_does_not_bring_the_lab_s_moments_back(monkeypatch):
    """The skeptic's case, on the server's schedule: a paper run while the freeze waited for the lab lock left its mark
    (done_until) past the freeze's `now`, and the lab then read the live store, which ends later still. The first run
    after the freeze scored from that mark, the lab's moment 601 s after the freeze among its trades."""
    store = _hindsight_store(FREEZE + 1800)
    store.set_coverage("paper", {"done_until": FREEZE + 900 - paper.SETTLE_SECS, "last_run": FREEZE + 900,
                                 paper.STATE_MARK: 1})  # fmt: skip
    seen: dict = {}
    monkeypatch.setattr(paper, "rank", _rank_seen(seen))
    (rule,) = paper.freeze(store, Config(), top=4, now=FREEZE, triggers=[COPY], tape=lambda: store)["frozen"]
    store.set_coverage("tape", {"spans": [[T0, FREEZE + 7200]]})
    paper.run(store, Config(), now=FREEZE + 2700)  # the next half-hourly run after the freeze
    assert not _forward(store, rule["id"]) & seen["lab"]


def test_a_lab_on_a_copy_taken_before_the_freeze_starts_the_record_at_the_freeze(monkeypatch):
    """The usual case: the lab reads the hourly copy, whose tape ends before the freeze began. The record starts at the
    freeze, as it always did."""
    store = _hindsight_store(FREEZE - 600)
    store.set_coverage("paper", {"done_until": FREEZE - 1200, "last_run": FREEZE - 780, paper.STATE_MARK: 1})
    seen: dict = {}
    monkeypatch.setattr(paper, "rank", _rank_seen(seen))
    (rule,) = paper.freeze(store, Config(), top=4, now=FREEZE, triggers=[COPY], tape=lambda: store)["frozen"]
    assert rule["from_ts"] == FREEZE and rule["lab"]["window"]["until_ts"] == FREEZE - 600
    store.set_coverage("tape", {"spans": [[T0, FREEZE + 7200]]})
    paper.run(store, Config(), now=FREEZE + 7200)
    forward = _forward(store, rule["id"])
    assert forward and not forward & seen["lab"]


# -- paper#3: a rule frozen at the doubled cost of 12-14 Nov can be frozen again at today's cost ----------------------
# From 12 Nov 17:24 to 14 Nov 18:26 UTC the measured cost of a transaction counted four tip-test buys and one
# incident's failed sends: every freeze charged 0.0062 to 0.0093 SOL a transaction, where one costs about 0.0045. The
# agents are told to freeze such a rule again, and the freeze found it "already in the paper test" (or said nothing,
# with the same exit): its record went on paying the doubled cost for good (24 running records on 22 Nov).
DOUBLED_AT = 1_794_603_688  # 13 Nov 21:01:28 UTC: three wallet rules frozen at 0.008495 SOL a transaction
TODAY = 1_795_347_049  # 22 Nov 11:30 UTC


def _doubled(monkeypatch, store, **kw):
    from tests.test_paper_one_record import COPY30, COPY_AT, KOL, _coins, _data, _freeze, _row

    data = _data({KOL: _coins("k", 57)})
    return _freeze(monkeypatch, store, data, [_row(KOL, COPY30, 0.0013, COPY_AT)], **kw)


def test_a_rule_frozen_at_the_doubled_cost_is_frozen_again_at_todays_cost(monkeypatch):
    store = Store(":memory:")
    (old,) = _doubled(monkeypatch, store, now=DOUBLED_AT, per_tx_sol=0.008495)["frozen"]
    assert paper.overcharged(old)
    again = _doubled(monkeypatch, store, now=TODAY, per_tx_sol=0.0045)  # the agents' "freeze it again"
    (new,) = again["frozen"]
    assert new["per_tx_sol"] == 0.0045 and new[paper.AGAIN] == 1 and new["id"] != old["id"] == paper.sid_of(old)
    assert new["id"] == paper.sid_of(new) and paper.live_id(new) == paper.live_id(old)  # one sniper live
    assert not paper.overcharged(new)
    (stale,) = again["not_as_live"]  # the old record keeps its id and trades, and says what it was charged
    assert stale["id"] == old["id"] and "0.008495 SOL a transaction" in stale["why"]
    row = {r["id"]: r for r in paper.report(store, Config(), TODAY)["strategies"]}[old["id"]]
    assert "0.008495 SOL a transaction" in row["exit_note"] and not row.get("research_only")
    assert _doubled(monkeypatch, store, now=TODAY + 60, per_tx_sol=0.0045)["frozen"] == []  # its one record again
    store.close()


def test_a_refuted_doubled_cost_record_holds_its_rule_only_if_it_lost_more_than_it_was_overcharged(monkeypatch):
    """A record refuted at the doubled cost that lost by more than a round trip was overcharged (each of the four on
    the server: 0.025 to 0.059 SOL a trade, against 0.008 to 0.010) would be refuted at today's cost too and holds its
    rule back for the seven days like any refuted rule; one that lost by less is no refutal of its rule."""
    for mean, held in ((-0.0301, True), (-0.004, False)):
        store = Store(":memory:")
        (old,) = _doubled(monkeypatch, store, now=DOUBLED_AT, per_tx_sol=0.008495)["frozen"]
        cov = store.coverage("paper_strategies")
        cov["strategies"][0]["refuted"] = {"at": TODAY - 86400, "trades": 1246, "mean_sol": mean}
        store.set_coverage("paper_strategies", cov)
        out = _doubled(monkeypatch, store, now=TODAY, per_tx_sol=0.0045)
        if held:
            assert out["frozen"] == [] and "not frozen again for 7 days" in out["skipped"][0]["why"], out
            assert old["id"] in out["skipped"][0]["why"]  # held by the refuted record itself
        else:
            (new,) = out["frozen"]
            assert new[paper.AGAIN] == 1 and new["per_tx_sol"] == 0.0045
        store.close()


def test_only_a_record_frozen_in_those_two_days_at_that_cost_counts_as_overcharged():
    rule = {"id": "x", "trigger": {"kind": "kol", "sol": 1.0}, "per_tx_sol": 0.008495, "from_ts": DOUBLED_AT}
    assert paper.overcharged(rule)
    assert not paper.overcharged(rule | {"per_tx_sol": 0.0045})  # today's cost in that window (none was)
    assert not paper.overcharged(rule | {"from_ts": 1_794_192_400, "per_tx_sol": 0.01})  # 8 Nov, a cost given by hand
    assert not paper.overcharged(rule | {"from_ts": TODAY})  # a measured cost of today, whatever it is
    assert not paper.overcharged(rule | {paper.FED: "dev_forward"})  # a rule another job feeds keeps its record
