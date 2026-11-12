"""A hypothesis frozen at a landing and with a bid of its own (12 Nov 2026).

The server's analyst was blocked: `paper freeze` took no `--copy-position` and no `--buy-bid`, so a rule that pays one
place behind the followed wallet at a 0.05 SOL bid could only be frozen at the copy landing (+6), "where the rule
loses". A hypothesis is now frozen at the place it was asked for (+0, +N, +Nx, or `bid` / `bid-`, the place the bid
buys) with the bid charged once on every forward buy. The rule stores both, the forward run scores it there, the
report names both, and no output calls it where our copies land live. A rule frozen before keeps its id."""

import json
import time

import pytest

from snipetech.analysis import bid_landing as BL
from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger, parse_trigger
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from .synth import Market, addr

T0 = int(time.time()) - 7500  # the tape: an hour before the freeze, an hour after it, all of it behind us
FREEZE = T0 + 3600
RUN = FREEZE + 3600
SLOT0 = 500_000_000
LEAD, MATE = addr("po-lead"), addr("po-mate")
BOT_A, BOT_B, BOT_C = addr("po-bot-a"), addr("po-bot-b"), addr("po-bot-c")
RULE = {"kind": "kol", "group": "wallet", "sol": 1}
EXIT = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False)
AUCTION = ((BOT_A, 3.0, 0.03), (BOT_B, 1.0, 0.005), (BOT_C, 0.5, 0.001))


def _bid(t, sol):
    t.priority, t.tip = round(sol * 1e9 * 2 / 3), round(sol * 1e9 / 3)
    return t


def _coin(st, k: int, ts: int, known: bool = True, block=AUCTION) -> str:
    """The followed wallet buys 2 SOL with its companion in the next transaction; three outsiders buy behind them in
    the block at bids of 0.03, 0.005 and 0.001 SOL; then eight buyers lift the price for half a minute."""
    m = Market(st, addr(f"po-coin-{k}"), addr(f"po-dev-{k}"), ts, SLOT0 + k * 10_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(4):
        m.buy(addr(f"po-crowd-{k}-{j}"), 0.5, 2 + j)
    rows = [m.buy(LEAD, 2.0, 50), m.buy(MATE, 1.0, 50)]
    rows[0].pos, rows[1].pos = 100, 101
    for j, (who, sol, bid) in enumerate(block):
        t = m.buy(who, sol, 50)
        t.pos = 120 + j
        if known:
            _bid(t, bid)
    for j in range(8):
        rows.append(m.buy(addr(f"po-fan-{k}-{j}"), 0.5, 100 + 3 * j))
    rows.append(m.sell(addr(f"po-crowd-{k}-0"), 1.0, 400))
    if known:
        for t in rows:
            _bid(t, 0.002)
    m.commit()
    return m.mint


def _tape(st: Store, coins: int = 24) -> None:
    """The tape a lab reads at the freeze: `coins` moments of the followed wallet in the hour before it."""
    st.set_label(LEAD, "tracked one", "wallet", "file")
    for k in range(coins):
        _coin(st, k, T0 + 100 + k * 120)
    st.set_coverage("tape", {"spans": [[T0, FREEZE]]})


def _forward(st: Store, coins: int = 5, unknown: int = 0) -> list[str]:
    """The tape recorded after the freeze: `coins` more moments (`unknown` of them without fee records)."""
    mints = [_coin(st, 100 + k, FREEZE + 300 + k * 120, known=k >= unknown) for k in range(coins)]
    st.set_coverage("tape", {"spans": [[T0, RUN]]})
    return mints


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    return cfg


def _freeze(st, **kw):
    kw = {"top": 4, "buy_sol": 0.2, "per_tx_sol": 0.0035, "triggers": [parse_trigger(RULE)], "now": FREEZE} | kw
    return paper.freeze(st, _cfg(), **kw)


def test_a_rule_frozen_before_keeps_its_id():
    """`strategy_id`: a field added later counts only when it is set."""
    kol = Trigger("kol", sol=2.0).__dict__
    assert paper.strategy_id(kol, EXIT.to_dict(), "+6", 0, 0.2) == "69d1450075"  # as before 12 Nov
    assert paper.strategy_id(kol, EXIT.to_dict(), "+1", 0, 2.0) == "40e6b1bcd5"
    assert paper.strategy_id(kol, EXIT.to_dict(), "+6", 0, 0.2, 0.0, False) == "69d1450075"
    plain = paper.strategy_id(kol, EXIT.to_dict(), "+1", 0, 0.2)
    ids = {plain, paper.strategy_id(kol, EXIT.to_dict(), "+1", 0, 0.2, asked=True),
           paper.strategy_id(kol, EXIT.to_dict(), "+1", 0, 0.2, 0.05, True),
           paper.strategy_id(kol, EXIT.to_dict(), "+1", 0, 0.2, 0.1, True),
           paper.strategy_id(kol, EXIT.to_dict(), "bid-", 0, 0.2, 0.05, True),
           paper.strategy_id(kol, EXIT.to_dict(), "bid", 0, 0.2, 0.05, True)}  # fmt: skip
    assert len(ids) == 6  # another landing or another bid is another rule
    old = {"trigger": kol, "exit": EXIT.to_dict(), "position": "+6", "latency": 0, "buy_sol": 0.2}
    assert paper.sid_of(old) == paper.live_id(old) == "69d1450075" and not paper.pinned(old) and paper.bid_of(old) == 0


def test_a_hypothesis_is_frozen_and_scored_forward_at_the_place_and_bid_asked_for():
    st = Store(":memory:")
    _tape(st)
    rep = _freeze(st, copy_position="+1", buy_bid_sol=0.01)
    (rule,) = rep["frozen"]
    assert (rule["position"], rule["latency"], rule["buy_bid_sol"], rule["landing_asked"]) == ("+1", 0, 0.01, True)
    assert rule["lab"]["landing"] == "+1@0" and rule["lab"]["ev_lower"] > 0
    assert (
        rule["id"] == paper.sid_of(rule) == paper.strategy_id(rule["trigger"], rule["exit"], "+1", 0, 0.2, 0.01, True)
    )
    assert rule["id"] != paper.strategy_id(rule["trigger"], rule["exit"], "+1", 0, 0.2)
    assert rule["landing_words"].startswith("+1 with a 0.01 SOL bid") and rep["asked"]["copy_position"] == "+1"
    assert rep["asked"]["buy_bid_sol"] == 0.01
    # the same rule at the same place without the bid is another rule, with its own id and record
    (twin,) = _freeze(st, copy_position="+1")["frozen"]
    assert twin["id"] != rule["id"] and twin["landing_asked"] is True and "buy_bid_sol" not in twin
    assert twin["exit"] == rule["exit"]
    # ... and frozen once: asking again freezes nothing new
    assert _freeze(st, copy_position="+1", buy_bid_sol=0.01)["frozen"] == []

    mints = _forward(st)
    out = paper.run(st, _cfg(), now=RUN)
    assert out["new_trades"] == 10 and out["landing"]["copies"] == "+6"
    kept = st.coverage("paper_strategies")
    assert [s["id"] for s in kept["strategies"]] == [rule["id"], twin["id"]] and not kept.get("superseded")
    paid, free = st.paper_trades(rule["id"]), st.paper_trades(twin["id"])
    assert [r["mint"] for r in paid] == mints == [r["mint"] for r in free]
    for a, b in zip(paid, free):
        # one outsider's buy ahead of us behind the wallet and its companion; the bid charged once on the buy
        assert a["data"]["landing"] == "+1@0" and a["data"]["behind"] == 2 and a["data"]["mates"] == 1
        assert a["data"]["buy_bid_sol"] == 0.01 and "buy_bid_sol" not in b["data"]
        assert a["pnl"] == pytest.approx(b["pnl"] - 0.01, abs=1e-9)
        assert a["data"]["entry_mcap_sol"] == b["data"]["entry_mcap_sol"]

    rows = {r["id"]: r for r in paper.report(st, _cfg(), now=RUN)["strategies"]}
    row = rows[rule["id"]]
    assert row["landing"].startswith("+1 with a 0.01 SOL bid: up to 1 trades behind the trigger")
    assert "not where our copies land live" in row["landing"] and row["live_landing"] is False
    assert row["buy_bid_sol"] == 0.01 and row["landing_asked"] is True and row["n"] == 5
    assert rows[twin["id"]]["landing"].startswith("+1: up to 1 trades") and rows[twin["id"]]["live_landing"] is False
    assert rows[twin["id"]]["total"] == pytest.approx(row["total"] + 0.05, abs=1e-9)
    from snipetech import report as R

    text = R.render_paper("report", paper.report(st, _cfg(), now=RUN))
    assert "+1 with a 0.01 SOL bid" in text and "not where our copies land live" in text
    assert "+1 with a 0.01 SOL bid" in R.render_paper("freeze", rep)
    # a later run moves neither to the copy landing, and prices the kept trades again with the bid still charged
    paper.run(st, _cfg(), now=RUN + 1800)
    assert [s["id"] for s in st.coverage("paper_strategies")["strategies"]] == [rule["id"], twin["id"]]
    st.update_paper_trades([r | {"data": r["data"] | {"model": "an older one"}} for r in paid])
    again = paper.rescore(st, _cfg(), ids=[rule["id"]], now=RUN + 1800)
    assert again["strategies"][0]["rescored"] == 5
    assert again["strategies"][0]["total_after"] == pytest.approx(again["strategies"][0]["total_before"], abs=1e-9)
    st.close()


def test_the_plain_freeze_of_the_same_rule_stays_the_live_landing_rule():
    """Without a landing or a bid of its own a hypothesis is frozen where our copies land, under the id it always
    had; asking for today's copy landing by name is the same rule."""
    st = Store(":memory:")
    _tape(st)
    (plain,) = _freeze(st)["frozen"]
    assert (plain["position"], plain["latency"]) == ("+6", 0) and not paper.pinned(plain)
    assert "landing_asked" not in plain and "buy_bid_sol" not in plain and "landing_words" not in plain
    assert plain["id"] == paper.strategy_id(plain["trigger"], plain["exit"], "+6", 0, 0.2)
    assert _freeze(st, copy_position="+6")["frozen"] == []  # the rule already under test
    (bid,) = _freeze(st, copy_position="+6", buy_bid_sol=0.02)["frozen"]  # the live place, with a bid: its own rule
    assert bid["id"] != plain["id"] and bid["buy_bid_sol"] == 0.02 and "landing_asked" not in bid and paper.pinned(bid)
    rows = {r["id"]: r for r in paper.report(st, _cfg(), now=FREEZE)["strategies"]}
    assert rows[plain["id"]]["live_landing"] is True and rows[plain["id"]]["landing"].startswith("+6: up to 6")
    assert rows[bid["id"]]["live_landing"] is False
    assert rows[bid["id"]]["landing"].startswith("+6 with a 0.02 SOL bid: up to 6 trades behind the trigger")
    assert "a bid charged on every buy: not where our copies land live" in rows[bid["id"]]["landing"]
    # the measured copy landing moves back: the plain rule follows it, the one with a bid stays as it was frozen
    st.set_coverage("measure:landing_pos", {"pos": "+9", "ts": RUN - 3600})
    _forward(st)
    paper.run(st, _cfg(), now=RUN)
    kept = st.coverage("paper_strategies")
    assert {(s["position"], paper.bid_of(s)) for s in kept["strategies"]} == {("+9", 0.0), ("+6", 0.02)}
    assert [s["id"] for s in kept["superseded"]] == [plain["id"]]
    st.close()


def test_a_hypothesis_is_frozen_and_scored_forward_at_the_place_its_bid_buys():
    st = Store(":memory:")
    _tape(st)
    rep = _freeze(st, copy_position="bid-", buy_bid_sol=0.004)
    (rule,) = rep["frozen"]
    assert (rule["position"], rule["latency"], rule["buy_bid_sol"], rule["landing_asked"]) == ("bid-", 0, 0.004, True)
    assert rule["lab"]["landing"] == "bid-@0.004" and rule["lab"]["places"]["third_or_later"] == 1.0
    assert rule["landing_words"].startswith("bid- with a 0.004 SOL bid: where that bid puts the copy")
    assert "one buy further back" in rule["landing_words"]
    assert rep["bid_landing"]["landing"] == "bid-@0.004" and rep["bid_landing"]["scored"] == 24
    mints = _forward(st, coins=6, unknown=2)
    out = paper.run(st, _cfg(), now=RUN)
    assert out["new_trades"] == 4 and out["skipped"] == {rule["id"]: {BL.NO_BIDS: 2}}
    assert "no fee record" in out["why"][BL.NO_BIDS]
    rows = st.paper_trades(rule["id"])
    assert [r["mint"] for r in rows] == mints[2:]
    for r in rows:
        # 0.03 and 0.005 outbid us, 0.001 does not; the cautious copy stands behind that third buy as well
        assert r["data"]["landing"] == "bid-@0.004" and r["data"]["place"] == 4 and r["data"]["behind"] == 4
        assert r["data"]["buy_bid_sol"] == 0.004
    (row,) = paper.report(st, _cfg(), now=RUN)["strategies"]
    assert row["live_landing"] is False and row["landing"].startswith("bid- with a 0.004 SOL bid")
    assert [s["id"] for s in st.coverage("paper_strategies")["strategies"]] == [rule["id"]]  # never moved to +6
    # `bid`, one place closer, is another rule
    (close,) = _freeze(st, copy_position="bid", buy_bid_sol=0.004, now=RUN)["frozen"]
    assert close["id"] != rule["id"] and close["lab"]["places"]["third_or_later"] == 1.0
    st.close()


def test_selling_in_parts_can_be_frozen_too():
    st = Store(":memory:")
    _tape(st)
    rep = _freeze(st, copy_position="+1", buy_bid_sol=0.01, sell_parts=(3, 5.0))
    (rule,) = rep["frozen"]
    assert rule["exit"]["sell_parts"] == 3 and rule["exit"]["sell_part_secs"] == 5.0
    assert rep["asked"]["sell_parts"] == {"parts": 3, "secs": 5.0}
    (whole,) = _freeze(st, copy_position="+1", buy_bid_sol=0.01)["frozen"] or [None]  # one record a rule: not again
    assert whole is None
    _forward(st)
    assert paper.run(st, _cfg(), now=RUN)["new_trades"] == 5
    st.close()


def test_a_landing_or_bid_of_its_own_is_refused_where_it_cannot_be_frozen():
    st = Store(":memory:")
    _tape(st, coins=3)
    with pytest.raises(ValueError, match="--trigger"):
        paper.freeze(st, _cfg(), copy_position="+1", now=FREEZE)
    with pytest.raises(ValueError, match="--trigger"):
        paper.freeze(st, _cfg(), buy_bid_sol=0.05, now=FREEZE)
    with pytest.raises(ValueError, match="--buy-bid"):
        _freeze(st, copy_position="bid-")
    with pytest.raises(ValueError, match="wallet rule"):
        _freeze(st, copy_position="bid", buy_bid_sol=0.05, triggers=[parse_trigger({"kind": "momentum", "sol": 1})])
    with pytest.raises(ValueError, match="bid"):
        _freeze(st, copy_position="first")
    with pytest.raises(ValueError, match="only-live"):
        _freeze(st, copy_position="+1", only_live=True)
    assert st.coverage("paper_strategies") == {}  # nothing was frozen by any of them
    st.close()


def test_the_cli_freezes_a_hypothesis_at_a_landing_and_bid_of_its_own(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        _tape(st)
    finally:
        st.close()
    base = ["--db", str(db), "paper", "freeze", "--trigger", json.dumps(RULE), "--buy", "0.2", "--per-tx", "0.0035"]
    assert cli.main([*base, "--copy-position", "+1", "--buy-bid", "0.01", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    (rule,) = out["frozen"]
    assert (rule["position"], rule["buy_bid_sol"], rule["landing_asked"]) == ("+1", 0.01, True)
    assert cli.main([*base, "--copy-position", "bid-", "--buy-bid", "0.004", "--sell-parts", "2"]) == 0
    text = capsys.readouterr().out
    assert "bid- with a 0.004 SOL bid" in text and "not where our copies land live" in text
    assert cli.main([*base[:4], "--copy-position", "+1"]) == 2  # no --trigger
    assert "--trigger" in capsys.readouterr().err
    assert cli.main([*base, "--copy-position", "bid"]) == 2  # a bid landing with no bid
    assert "--buy-bid" in capsys.readouterr().err
    assert cli.main([*base, "--sell-parts", "40"]) == 2
    st = Store(db)
    try:
        frozen = st.coverage("paper_strategies")["strategies"]
        assert [(s["position"], paper.bid_of(s)) for s in frozen] == [("+1", 0.01), ("bid-", 0.004)]
        assert frozen[1]["exit"]["sell_parts"] == 2
    finally:
        st.close()
