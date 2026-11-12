"""Every lab scores a rule at what it costs live and sells it the way OKTech sells (research round 1, 7 Nov: "P1: score
every lab at live cost and with OKTech's watch delay", docs/research/2026-11-07-round-1.md).

Cost: every lab of round 1 charged the lab's flat default of 0.0035 SOL a transaction, 0.007 a round trip, against
about 0.009 live (six job records read; none set --per-tx, and the research round's lab step never sets one), so every
mean was 0.002 SOL a trade too kind, 0.003 with a partial take profit. The never-stops loop trades whatever passes paper
without asking again, so a lab that flatters a rule is a live risk. `snipetech lab`, the command the agents' hypothesis
labs and the research rounds run, now charges what `paper freeze`, the qualify lab and the strategy search charge: the
measured cost of a transaction (qualify.run.lab_tx_cost: half the low bid's measured round trip; once a week passes
without enough low-bid fills, the last one measured; 0.0045 until one is measured, never the flat 0.0035). --per-tx
still overrides it, and the output says which cost it charged and where it came from.

Watch: OKTech starts watching a coin about 1.3 s after its buy lands (the 6 Nov copy fills replay right with 1.25-1.4 s,
tests/test_lab_replay.py), but the grid's exits on launch-type rules acted from the buy: the chosen exit of round 1's
10/10/1 hunt showed watch_secs 0, and replaying hunt buys with a 2 s delay cost 0 to 0.003 SOL a trade. Every exit on a
launch, hunt, whale or burst rule now waits the watch delay; copies keep the exits they are given.
"""

import json
import time

import pytest

from snipetech import cli
from snipetech.analysis import paper
from snipetech.analysis.lab import WATCH_SECS, Trigger, collect, rank
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from .synth import Market, addr
from .test_lab import NOW
from .test_lab_hypotheses import HUNT_JSON, _protocol_command, _tape_db


def _measure_low_bid(db, n: int = 40) -> None:
    """The measure stage's low bid (arm "control"): 0.0035 SOL of fees a side and 0.001 of failed duplicate sends a
    round trip, 0.008 SOL in all, so 0.004 a transaction; `n` measured fills (the cost counts from 30)."""
    st = Store(db)
    try:
        st.put_bid_model({"arm": "control", "n": n, "failed_n": 0, "mean_fee_sol": 0.0035, "mean_overhead_sol": 0.001,
                          "updated_at": NOW})  # fmt: skip
    finally:
        st.close()


def _lab(capsys, db, *flags: str) -> dict:
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", *flags, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_the_lab_charges_the_measured_cost_of_a_transaction_by_default(tmp_path, capsys):
    db = _tape_db(tmp_path)
    _measure_low_bid(db)
    out = _lab(capsys, db)
    assert out["per_tx_sol"] == pytest.approx(0.004) and out["per_tx_source"] == "measured"
    told = _lab(capsys, db, "--per-tx", "0.004")
    assert told["strategies"] == out["strategies"]  # the very numbers a lab told to charge 0.004 gives
    assert cli.build_parser().parse_args(["lab"]).per_tx is None  # no flat number hides behind the flag any more


def test_per_tx_still_overrides_the_measured_cost_and_says_so(tmp_path, capsys):
    db = _tape_db(tmp_path)
    _measure_low_bid(db)
    measured = _lab(capsys, db)
    told = _lab(capsys, db, "--per-tx", "0.006")
    assert told["per_tx_sol"] == pytest.approx(0.006) and told["per_tx_source"] == "flag"
    (m,), (t,) = measured["strategies"], told["strategies"]
    assert t["train_mean"] < m["train_mean"] - 0.003  # every exit pays 0.002 more a transaction, two or three of them


def test_before_the_low_bid_is_measured_the_lab_charges_a_live_estimate_and_says_so(tmp_path, capsys):
    """Review of 7 Nov evening: before a cost was measured the lab charged the flat 0.0035, below what a live
    transaction costs, and nothing on the server acted on its "flat". It charges 0.0045, about what a live transaction
    cost when it was measured on 6 Nov (0.0047), and says it is an estimate."""
    db = _tape_db(tmp_path)
    out = _lab(capsys, db)
    assert out["per_tx_sol"] == pytest.approx(0.0045) and out["per_tx_source"] == "estimate"
    _measure_low_bid(db, n=20)  # 20 fills: too few to trust; still the estimate, and the output says so
    out = _lab(capsys, db)
    assert out["per_tx_sol"] == pytest.approx(0.0045) and out["per_tx_source"] == "estimate"
    assert out["per_tx_measured_at"] is None
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1"]) == 0
    assert "0.0045 SOL a transaction (0.009 a round trip), estimated" in capsys.readouterr().out


def test_after_a_week_without_fills_the_lab_charges_the_last_measured_cost_and_says_when(tmp_path, capsys):
    """The bid model reads 7 days of fills and a week without trading prunes the low bid's row. The lab then charges the
    last cost the measure stage measured (measure:tx_cost), never an unmeasured one, and says when it was measured."""
    db = _tape_db(tmp_path)
    at = NOW - 8 * 86400
    st = Store(db)
    try:
        st.set_coverage("measure:tx_cost", {"per_tx_sol": 0.0047, "arm": "control", "n": 40, "ts": at})
    finally:
        st.close()
    out = _lab(capsys, db)
    assert (out["per_tx_sol"], out["per_tx_source"], out["per_tx_measured_at"]) == (0.0047, "last measured", at)
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1"]) == 0
    day = time.strftime("%d %b", time.gmtime(at))
    assert f"0.0047 SOL a transaction (0.0094 a round trip), last measured live on {day}" in capsys.readouterr().out


def test_the_analysts_hypothesis_lab_charges_the_measured_cost(tmp_path, capsys):
    """The analyst runs the prompt's own lab command as written, with no --per-tx: it charges the measured cost."""
    db = _tape_db(tmp_path)
    _measure_low_bid(db)
    assert cli.main(["--db", str(db), *_protocol_command("lab", HUNT_JSON)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["per_tx_sol"] == pytest.approx(0.004) and out["per_tx_source"] == "measured"


def test_the_lab_text_says_what_a_transaction_cost(tmp_path, capsys):
    db = _tape_db(tmp_path)
    _measure_low_bid(db)
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1"]) == 0
    text = capsys.readouterr().out
    assert "0.004 SOL a transaction (0.008 a round trip), measured" in text
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", "--per-tx", "0.0045"]) == 0
    assert "0.0045 SOL a transaction (0.009 a round trip), as --per-tx says" in capsys.readouterr().out


# -- OKTech's watch delay on launch-type rules ----------------------------------------------------------------------

HUNT = Trigger("hunt", sol=2.0, window=2.0, dev_max=1.0)  # fires on the 2.5 SOL buy of every pause-world coin
KOL = Trigger("kol", sol=2.0)  # a copy of that same buy: the buyer is a labeled KOL
LAUNCH_TYPE = [Trigger("launch"), HUNT, Trigger("whale", sol=2.0), Trigger("burst", 3, 1.0, 2.0)]
# the exit every book sniper runs, one of the grid's too (exit_set): sells after 1 quiet second, at -30 %, at 300 s
LAB_EXIT = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=1.0)


def _pause_world(store: Store, n: int = 24) -> None:
    """A coin a minute, 150 slots apart, so the lab measures the tape's 0.4 s a slot: the creator's 0.5 SOL, a labeled
    KOL's 2.5 SOL a slot later (a 2 SOL hunt fires on it), three buys of 0.3 SOL in the next slot (a hunt buy lands
    behind them, +3@1), then nothing for 1.2 s, six buys of 0.4 SOL 0.4 s apart, and a long quiet before they sell."""
    for i in range(n):
        m = Market(store, addr(f"pw-{i}"), addr(f"pw-dev-{i}"), NOW - 3000 + i * 60, 70_000_000 + i * 150)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr(f"pw-kol-{i}"), 2.5, 1)
        for k in range(3):
            m.buy(addr(f"pw-early-{i}-{k}"), 0.3, 2)
        for k in range(6):
            m.buy(addr(f"pw-late-{i}-{k}"), 0.4, 5 + k)
        for k in range(6):
            m.sell(addr(f"pw-late-{i}-{k}"), 1.0, 100 + k)
        m.commit()
        store.set_label(addr(f"pw-kol-{i}"), f"pw-kol-{i}", "kol")
    store.set_coverage("tape", {"spans": [[NOW - 3100, NOW]]})


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def test_every_exit_on_a_rule_a_live_sniper_runs_waits_oktechs_watch_delay_a_wallet_rule_too():
    """A wallet rule runs live through an OKTech copy sniper, whose auto-sell starts watching 1.3 s after the buy (the
    delay itself was measured on copy fills). Until 12 Nov a hypothesis lab on a wallet rule scored the search set's
    exits acting from the buy: a take-profit could fire in the followed wallet's own block and sell at the start of
    the next one, which no sniper of ours can do. The analyst, 12 Nov 18:31: "Cupseyy's mean is above what a +30 %
    take-profit can pay at its threshold, so the simulator's fill there needs checking"."""
    store = Store(":memory:")
    _pause_world(store)
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[*LAUNCH_TYPE, KOL],
                   exits=[LAB_EXIT])  # fmt: skip
    assert data["slot_secs"] == [pytest.approx(0.4, abs=0.01)]  # the world runs at the tape's slot time
    assert set(data["kind_exits"]) == {"launch", "hunt", "whale", "burst", "kol"}  # never a momentum or event rule
    ranked = {r["trigger_spec"]["kind"]: r for r in rank(data, _cfg(), top=10, min_train=5)}
    assert set(ranked) == {"launch", "hunt", "whale", "burst", "kol"}
    for kind in ("launch", "hunt", "whale", "burst", "kol"):  # each runs live through an OKTech sniper
        assert ranked[kind]["exit_params"] == LAB_EXIT.to_dict() | {"watch_secs": WATCH_SECS}, kind
        assert ranked[kind]["exit"] == f"{LAB_EXIT.label()} watch{WATCH_SECS:g}s", kind
    assert WATCH_SECS == 1.3  # OKTech's measured watch delay (6 Nov copy fills: 1.25-1.4 s)


def test_a_wallet_rule_frozen_without_the_watch_delay_does_not_stand_for_its_rule():
    """As for a launch-type rule frozen before 7 Nov: it keeps its id and its trades, and the same rule frozen again,
    with the delay, is the rule's record."""
    kol = {"kind": "kol", "group": "big", "sol": 1.0}
    assert not paper._sells_as_live({"trigger": kol, "exit": LAB_EXIT.to_dict()})
    assert paper._sells_as_live({"trigger": kol, "exit": LAB_EXIT.to_dict() | {"watch_secs": WATCH_SECS}})
    assert paper._sells_as_live({"trigger": {"kind": "momentum"}, "exit": LAB_EXIT.to_dict()})  # no sniper times it


def test_an_exit_that_would_fire_inside_the_watch_delay_does_not(monkeypatch):
    """The same buy, the same landing and the same exit. Scored as acting from the buy (a wallet rule until 12 Nov) it
    sells at once: the coin is quiet for 1.2 s right after the buy, and the 1 s dead exit fires at the buy's own price,
    for the fees. An exit that waits OKTech's 1.3 s never counts that pause; it rides the six buys that follow and
    sells when they stop. Both the hunt and the wallet rule are scored that way now."""
    import snipetech.analysis.lab as lab_module

    store = Store(":memory:")
    _pause_world(store)

    def rows_by_trigger():
        data = collect(store, _cfg(), 0.2, 1, "+3", landings={"hunt": paper.HUNT_LANDING}, triggers=[HUNT, KOL],
                       exits=[LAB_EXIT])  # fmt: skip
        by = {ti: [r for r in data["rows"] if r["trigger"] == ti] for ti in (0, 1)}
        assert len(by[0]) == len(by[1]) == 24 and {r["landing"] for r in data["rows"]} == {"+3@1"}
        return by

    by = rows_by_trigger()
    for hunt, copy in zip(by[0], by[1]):
        assert hunt["mint"] == copy["mint"]
        assert hunt["pnl"][0] > 0.01 and copy["pnl"][0] > 0.01  # held through the buying that followed
    monkeypatch.setattr(lab_module, "WATCHED_KINDS", lab_module.LAUNCH_KINDS)  # the wallet rule as it was scored before
    for hunt, copy in zip(*rows_by_trigger().values()):
        assert copy["pnl"][0] == pytest.approx(-0.012, abs=0.002)  # sold at once: 2.5 % in fees, 2 x 0.0035
        assert hunt["pnl"][0] > 0.01


def test_strategies_frozen_before_keep_their_ids_and_a_launch_type_one_frozen_now_is_new():
    """A strategy frozen before the watch delay keeps its id, its exit and its trades: paper strategy ids leave a field
    at its default out (paper.strategy_id), and the paper test scores every strategy with the exit it was frozen with.
    The same hunt rule frozen now carries the watch delay, which is a different exit, so it is a strategy of its own,
    scored with the delay from its own freeze; the old one is never re-keyed or replaced. A wallet rule frozen now
    carries the delay too (12 Nov), so it is new as well, and one frozen before keeps the id it had."""
    store = Store(":memory:")
    _pause_world(store)
    old_id = paper.strategy_id(HUNT.__dict__, LAB_EXIT.to_dict(), "+3", 1, 0.2)
    assert old_id == "0dd52c0607"  # the id this strategy had before the watch delay existed
    old = {"id": old_id, "label": "frozen before", "trigger": HUNT.__dict__, "exit": LAB_EXIT.to_dict(),
           "position": "+3", "latency": 1, "buy_sol": 0.2, "per_tx_sol": 0.0035, "from_ts": NOW - 3100,
           "lab": {"mean": 0.01, "ev_lower": 0.005}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [old]})
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=[HUNT, KOL],
                   exits=[LAB_EXIT])  # fmt: skip
    rep = paper.freeze(store, _cfg(), top=4, position="+6", latency=0, buy_sol=0.2, now=NOW, data=data, only_live=True)
    (new,) = rep["frozen"]  # the hunt pays here with the delay; the copy, six places back, does not
    assert new["exit"]["watch_secs"] == WATCH_SECS and new["id"] == "4f7915097e" != old_id
    assert [s["id"] for s in rep["kept"]] == [old_id]
    paper.run(store, _cfg(), now=NOW)
    strategies = {s["id"]: s for s in store.coverage("paper_strategies")["strategies"]}
    assert set(strategies) == {old_id, new["id"]}
    assert strategies[old_id]["exit"] == LAB_EXIT.to_dict()  # its exit as frozen, never rewritten
    assert len(store.paper_trades(old_id)) == 24  # still scored under its own id
    # one sniper live for both: the automatic test drops them and carries their live record as one, and the old one
    # never goes live (tests/operator: test_live_test.py, test_autotest.py)
    assert paper.live_id(strategies[new["id"]]) == paper.live_id(strategies[old_id]) == old_id
    # a wallet rule frozen before the delay had this id and keeps it; the same rule ranked now carries the delay and
    # would be frozen under an id of its own
    (copy,) = [r for r in rank(data, _cfg(), top=10) if r["trigger_spec"]["kind"] == "kol"]
    assert copy["exit_params"]["watch_secs"] == WATCH_SECS
    assert paper.strategy_id(KOL.__dict__, LAB_EXIT.to_dict(), "+6", 0, 0.2) == "0035d424be"
    assert paper.strategy_id(KOL.__dict__, copy["exit_params"], "+6", 0, 0.2) != "0035d424be"


def test_the_watch_delay_is_how_oktech_sells_not_a_setting_it_is_sent():
    """Research tooling only: a hunt strategy frozen with the watch delay gets exactly the OKTech sniper the same
    strategy got without it, the one the live test would arm (oktech.strategy_sniper reads no watch_secs)."""
    from snipetech.analysis.lab import watched
    from snipetech.oktech import strategy_sniper

    def sniper(exit_params: dict):
        return strategy_sniper({"id": "s", "label": "t", "buy_sol": 0.2, "trigger": HUNT.__dict__, "exit": exit_params},
                               1, "hunt")  # fmt: skip

    before, now = sniper(LAB_EXIT.to_dict()), sniper(watched(LAB_EXIT).to_dict())
    assert now.config == before.config and now.summary == before.summary
