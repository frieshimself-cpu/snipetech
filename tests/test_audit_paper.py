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
