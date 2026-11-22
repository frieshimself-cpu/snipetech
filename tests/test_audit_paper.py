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


def test_a_skip_rule_refuted_at_the_doubled_cost_holds_its_default_twin_only_if_it_lost_more_than_it_was_overcharged():
    """Review of paper#3: a rule refuted forward without serial crews' coins holds back the same trigger on every coin
    for seven days (`refuted_skip_twins`), and that hold did not ask what the rule itself is now asked: whether it
    lost by more than it was overcharged. A skip record refuted at the doubled cost that lost less (here -0.004 SOL a
    trade, charged 0.008 too much a round trip) could be frozen again itself, while its default twin was held for a
    refutal that today's cost would not make. Every launch-type rule frozen in those two days is such a skip record
    (on the server six hunts, four refuted, each by more than it was overcharged: they hold as before)."""
    hunt = {"id": "h0d0000000", "label": "a hunt | hold60s", "trigger": {"kind": "hunt", "sol": 5.0, "window": 1.0},
            "exit": {"max_hold_secs": 60.0, "watch_secs": 1.3}, "position": "+3", "latency": 1, "buy_sol": 1.0,
            "per_tx_sol": 0.008495, "from_ts": DOUBLED_AT, "lab": {}}  # fmt: skip
    assert paper.overcharged(hunt) and paper.crew_note(hunt)  # frozen before 14 Nov: scored without crews' coins
    for mean, held in ((-0.0301, True), (-0.004, False)):
        rule = hunt | {"refuted": {"at": TODAY - 86400, "trades": 400, "mean_sol": mean}}
        assert bool(paper.refuted_skip_twins([rule], TODAY)) is held, mean
    # a record charged what a transaction costs is held by any refutal, as before
    plain = hunt | {"per_tx_sol": 0.0045, "refuted": {"at": TODAY - 86400, "trades": 400, "mean_sol": -0.004}}
    assert paper.refuted_skip_twins([plain], TODAY)


def test_only_a_record_frozen_in_those_two_days_at_that_cost_counts_as_overcharged():
    rule = {"id": "x", "trigger": {"kind": "kol", "sol": 1.0}, "per_tx_sol": 0.008495, "from_ts": DOUBLED_AT}
    assert paper.overcharged(rule)
    assert not paper.overcharged(rule | {"per_tx_sol": 0.0045})  # today's cost in that window (none was)
    assert not paper.overcharged(rule | {"from_ts": 1_794_192_400, "per_tx_sol": 0.01})  # 8 Nov, a cost given by hand
    assert not paper.overcharged(rule | {"from_ts": TODAY})  # a measured cost of today, whatever it is
    assert not paper.overcharged(rule | {paper.FED: "dev_forward"})  # a rule another job feeds keeps its record


# -- paper#5: a forward trade says when its sale was priced outside the model ------------------------------------------
# The lab marks a score that rests on a price outside the model (`lab.exit_pnls`: the replay ran past the curve's end
# with our coins out of the pool, for a position of SIZE_FLOOR_SHARE of the pool or more; or our coins became more than
# half the pool's) and counts them. The forward record kept nothing of it: the best hunt's three biggest wins of 22 Nov
# (+23.7 of its +16.22 SOL) were all priced past the curve, and no report could say so.


def test_a_forward_trade_priced_past_the_curve_says_so_and_the_report_counts_it():
    from tests.test_size_honesty import BIG, EXITS, NOW, _world

    store = Store(":memory:")
    t0 = NOW - 5000
    _world(store, t0=t0, run_to=45.0)  # on the first coin the buyers behind us take the curve from 6 to 45 SOL
    rule = Trigger("kol", sol=1.0, only=BIG)  # a copy of the big wallet's buys
    ex = EXITS[1].to_dict()  # held 60 s: it sells after the curve, with our 10 SOL out of it, had ended
    sid = paper.strategy_id(rule.__dict__, ex, "+6", 0, 10.0)
    store.set_coverage("paper_strategies", {"strategies": [
        {"id": sid, "label": f"{rule.label()} | hold60s", "trigger": rule.__dict__, "exit": ex, "position": "+6",
         "latency": 0, "buy_sol": 10.0, "per_tx_sol": 0.0045, "from_ts": t0 - 100, "lab": {}}], "superseded": []})  # fmt: skip
    paper.run(store, Config(), now=t0 + 3000)
    rows = {r["mint"]: r for r in store.paper_trades(sid)}
    first = rows.pop(addr("size-0"))
    assert first["data"]["past_curve"] is True and first["data"]["pool_share"] >= 0.01
    assert rows and all("past_curve" not in r["data"] and "pool_share" in r["data"] for r in rows.values())
    (row,) = paper.report(store, Config(), t0 + 3000)["strategies"]
    assert row["outside_model"] == {"trades": 1, "sol": round(first["pnl"], 6), "of": len(rows) + 1}
    from snipetech import report as R

    text = R.render_paper("report", paper.report(store, Config(), t0 + 3000))
    assert f"{sid}: 1 of its {len(rows) + 1} trades" in text and "rest on a price outside the model" in text
    store.close()


def test_a_trade_written_before_the_mark_existed_is_counted_as_not_known():
    """Rows the run wrote before 22 Nov carry no pool share: the report says on how many trades it knows."""
    store = Store(":memory:")
    sid = "o1d0000000"
    store.set_coverage("paper_strategies", {"strategies": [
        {"id": sid, "label": "a rule | hold60s", "trigger": {"kind": "kol", "sol": 1.0}, "exit": {"max_hold_secs": 60},
         "position": "+6", "latency": 0, "buy_sol": 1.0, "per_tx_sol": 0.0045, "from_ts": 1, "lab": {}}],
        "superseded": []})  # fmt: skip
    for k in range(3):
        store.put_paper_trade({"strategy": sid, "mint": f"m{k}", "trigger_ts": 10 + k, "pnl": 0.1, "data": {}})
    (row,) = paper.report(store, Config(), 100)["strategies"]
    assert row["outside_model"] == {"trades": 0, "sol": 0.0, "of": 0}
    store.close()


# -- paper#6: a freeze's text calls no refuted rule "still running", and its first read shows what it froze -----------
# Every `paper freeze` log ended with one "still running" line a stored rule, the refuted ones too: 132 lines (45 of
# them refuted) in the 22 Kb log of 11:01 on 22 Nov, and an agent's first read of it (the newest lines that fit 12,000
# characters) held no line of what was frozen. Nor did the text say why a row was left out (the tester's note of 07:01).
LONG = " x" * 20


def _freeze_answer(n_kept: int, n_refuted: int, skipped: int = 0) -> dict:
    gone = {"at": 1794326892, "trades": 400, "mean_sol": -0.01}
    kept = [{"id": f"{k:010x}", "label": f"a KOL makes its first buy in the coin, >= 1 SOL | SL-15% hold300s{LONG}",
             **({"refuted": gone} if k < n_refuted else {})} for k in range(n_kept)]  # fmt: skip
    frozen = [{"id": "77c52b46f1", "label": "the wallet CyaE1V buys in the coin, >= 0.95 SOL | hold12s copy50%",
               "lab": {"mean": 0.1665, "ev_lower": 0.1078}, "landing_words": "bid- with a 0.05 SOL bid"}]  # fmt: skip
    left = [{"label": f"rule {k} | hold{k}s{LONG}", "why": "its lower bound in the lab is not positive (-0.0012 SOL a "
             f"trade on {k + 9} newer moments): nothing to test forward"} for k in range(skipped)]  # fmt: skip
    return {"frozen": frozen, "kept": kept, "skipped": left, "tape_hours": 168.0}


def test_a_freeze_calls_no_rule_still_running_and_counts_the_refuted_apart():
    from snipetech.report import render_paper

    text = render_paper("freeze", _freeze_answer(3, 1))
    assert "still running" not in text and "0000000000" not in text
    assert "3 rules already in the paper test (1 refuted, scored no more)" in text


def test_a_freeze_says_why_it_left_each_row_out():
    from snipetech.report import render_paper

    text = render_paper("freeze", _freeze_answer(0, 0, skipped=2))
    assert "left out: rule 1 | hold1s" in text and "its lower bound in the lab is not positive" in text
    many = render_paper("freeze", _freeze_answer(0, 0, skipped=45))
    assert many.count("left out: rule") == 20 and "and 25 more left out (--json lists them)" in many


def test_the_first_read_of_a_freeze_log_shows_what_was_frozen():
    from snipetech.operator.tools import read_job_output
    from snipetech.report import render_paper

    text = render_paper("freeze", _freeze_answer(132, 45, skipped=45))
    first = read_job_output(text)
    assert "77c52b46f1" in first and "Frozen for forward testing" in first, first.splitlines()[0]


# -- paper#4: the forward run asks a wallet rule only about coins one of its wallets traded ----------------------------
# The run called `lab._fire` for every wallet rule on every coin active in its window, a walk over the coin's whole
# history, though a wallet rule fires only on a buy by one of its wallets (`lab.rule_wallets`): on the server 70 wallet
# rules over 1,354 coins and 2.85M trades of history a half-hourly run, about a minute of its 8 to 12. The lab's plan
# reads no such coin (11 Nov); the run now passes over it for that rule, with the same trades.


def test_a_wallet_rule_is_not_walked_over_a_coin_none_of_its_wallets_traded(monkeypatch):
    from snipetech.analysis import lab as L
    from tests.synth import Market

    t0 = 1_795_000_000
    store = Store(":memory:")
    for k in range(20):
        m = Market(store, addr(f"w-coin-{k}"), addr(f"w-dev-{k}"), t0 + 60 + k, 500_000_000 + k * 5_000)
        m.buy(m.deployer, 0.3, 0, sig=m.create_sig)
        if k == 7:
            m.buy(addr("w-leader-0"), 1.5, 1)  # the one coin a followed wallet buys
        for j in range(30):
            m.buy(addr(f"w-crowd-{k}-{j}"), 0.05, 1 + j)
        m.commit()
    store.set_coverage("tape", {"spans": [[t0, t0 + 3 * 3600]]})
    rules = []
    for r in range(5):
        trig = Trigger("kol", sol=1.0, only=addr(f"w-leader-{r}"))
        sid = paper.strategy_id(trig.__dict__, COPY_EXIT.to_dict(), "+6", 0, 1.0)
        rules.append({"id": sid, "label": trig.label(), "trigger": trig.__dict__, "exit": COPY_EXIT.to_dict(),
                      "position": "+6", "latency": 0, "buy_sol": 1.0, "per_tx_sol": 0.0045, "from_ts": t0,
                      "lab": {"mean": 0.1, "ev_lower": 0.05}})  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": rules, "superseded": []})
    walks, real = [], paper._fire

    def fire(trig, path, *a, **k):
        if trig.kind == "kol" and not set(L.rule_wallets(trig, a[1])) & set(path.users):
            walks.append(path.mint)  # a wallet rule asked about a coin none of its wallets traded: it cannot fire
        return real(trig, path, *a, **k)

    monkeypatch.setattr(paper, "_fire", fire)
    out = paper.run(store, Config(), now=t0 + 3 * 3600)
    assert walks == [], f"{len(walks)} wallet-rule walks over coins none of the rule's wallets traded"
    # the trades are the ones the walks gave: the leader's one coin, for its one rule
    assert out["new_trades"] == 1 and [r["mint"] for r in store.paper_trades(rules[0]["id"])] == [addr("w-coin-7")]
