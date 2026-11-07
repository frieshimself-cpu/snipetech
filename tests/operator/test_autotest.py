"""The owner's standing yes (operator/autotest.py; 7 Nov 09:15, docs/live-recipe.md, "Yes, use this plan"): when a
strategy passes on paper, one live test starts by itself (0.2 SOL a buy, one coin at a time, at most 10 buys, at most 2
hours, stopping 1 SOL below its start); after its 10 trades it goes on only while live is at least half of paper (30 more,
in blocks of 10, the same 1 SOL stop counted from the first block's start), otherwise it stops and the analyst works out
why. One sequence per yes: at its end the switch turns itself off, and only the owner turns it on again."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import snipetech
from snipetech.models import Trade
from snipetech.operator import autotest as AT
from snipetech.operator import live_test as LT
from snipetech.operator.snapshot import Snapshot
from tests.operator import test_live_test as LTT

NOW, W, BEST, SECOND, OK_ID = LTT.NOW, LTT.W, LTT.BEST, LTT.SECOND, LTT.OK_ID
OTHER = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"  # another wallet the config could name
YES = "Yes, use this plan"
CAP = "cap reached: {n} fills since start; the wallet holds nothing, so the snipers it armed are stopped"
HOUR = 3600


@pytest.fixture
def env(tmp_path):
    e = LTT.make_env(tmp_path)
    e.sol = 11.8  # the wallet's SOL as each round's snapshot reads it (plus 0.1 wrapped)
    e.k = 0  # trades put on the tape so far
    yield e
    e.store.close()


def _on(e, **caps):
    """The owner's yes, recorded by the cloud session an hour before the first round, for the sniper wallet it names."""
    return AT.set_autotest(e.paths, True, values=caps, words=YES, at=NOW - HOUR, by="cli", now=NOW - HOUR, wallet=W,
                           oktech_wallet="SNIPER")  # fmt: skip


def _round(e, at, tape=True):
    """One operator round at `at`, with a fresh snapshot of the configured wallet and (unless `tape` is False) a current
    recorder."""
    if tape:
        LTT._tape(e.store, at - 5, k=10_000 + e.k)
        e.k += 1
    e.snap = Snapshot(ts=at, wallet=e.cfg.operator.wallet, native_sol=e.sol, wsol=0.1, held=[], snipers=[])
    return asyncio.run(
        LT.round_step(e.svc, e.cfg, e.paths, e.jobs, at, snap=e.snap, notes=e.notes, notifier=e.notifier)
    )


def _measure(e, trades):
    """The measure stage reads the trades: each buy and the sell that closed it, in `fills`."""
    for sig, ts, mint, pnl, leader in trades:
        e.store.put_fill({"sig": sig, "ts": ts, "wallet": W, "wallet_copied": leader, "mint": mint, "side": "buy",
                          "sol": 0.202, "tokens": 1_000_000.0})  # fmt: skip
        e.store.put_fill({"sig": f"sell-{sig}", "ts": ts + 20, "wallet": W, "mint": mint, "side": "sell",
                          "sol": 0.202 + pnl, "tokens": 1_000_000.0, "pnl_sol": pnl, "buy_sig": sig})  # fmt: skip


def _play(e, pnls, *, reason=None, measured=True, copies=(), report=None):
    """The block running now: its sniper armed, one coin bought and sold for each of `pnls` (the tape has our buys), the
    book's copies from the same wallet meanwhile (`copies`: their pnls), then its cycler ends with its report. Returns
    the trades, for a measure stage that reads them later."""
    t = LT.load(e.paths)
    assert t["state"] == "running", t
    LTT._armed(e)
    start = int(t["started_at"])
    trades = []
    for k, (pnl, leader) in enumerate([(p, None) for p in pnls] + [(p, "Leader" + "L" * 38) for p in copies]):
        sig, ts, mint = f"buy-{start}-{k}", start + 60 + 30 * k, f"Coin{start}x{k}"
        e.store.add_trades([Trade(sig=sig, ev=0, slot=5_000 + e.k, pos=0, ts=ts, mint=mint, venue="curve", user=W,
                                  is_buy=True, quote=200_000_000, base=1_000_000, fee=2_000_000, fee_bps=100, q_after=1,
                                  b_after=1)])  # fmt: skip
        e.k += 1
        trades.append((sig, ts, mint, pnl, leader))
    if measured:
        _measure(e, trades)
    n = len(trades)
    rep = {"start_sol": 11.8, "end_sol": 11.8 + sum(pnls) + sum(copies), "reason": reason or CAP.format(n=n),
           "fills": n, "restarts": 20, "held": [], "left_running": []} | (report or {})  # fmt: skip
    LTT._end_job(e.paths, t["job_id"], 0, rep)
    e.sol += sum(pnls) + sum(copies)
    return trades


def _seq(e):
    return AT.load_seq(e.paths)


def _said(e, words):
    return [s for s in e.sent if words in s]


# -- the owner's switch --------------------------------------------------------------------------------------------


def test_with_no_file_it_is_off_and_the_plan_is_the_owners(env):
    at = AT.read_autotest(env.paths)
    assert at["on"] is False and at["approved"] is None and AT.autotest_file(env.paths) == env.paths.root / "AUTOTEST"
    assert AT.DEFAULTS == {"buy_sol": 0.2, "fills": 10, "minutes": 120, "loss_sol": 1.0, "more_blocks": 3,
                           "continue_at": 0.5}  # fmt: skip
    assert {k: at[k] for k in AT.DEFAULTS} == AT.DEFAULTS


def test_turning_it_on_records_the_owners_words_and_time(env):
    at = _on(env)
    assert at["on"] and at["approved"] == {"words": YES, "at": NOW - HOUR}
    data = json.loads(AT.autotest_file(env.paths).read_text())
    assert data["on"] is True and data["by"] == "cli" and data["ts"] == NOW - HOUR and data["buy_sol"] == 0.2
    with pytest.raises(ValueError, match="words"):
        AT.set_autotest(env.paths, True, words="  ", by="cli", now=NOW, wallet=W, oktech_wallet="SNIPER")
    off = AT.set_autotest(env.paths, False, by="telegram", now=NOW)
    assert off["on"] is False and off["approved"] == {"words": YES, "at": NOW - HOUR}  # kept for the record


@pytest.mark.parametrize(
    "values, words",
    [({"buy_sol": 0.51}, "0.5 SOL"), ({"buy_sol": 0}, "0.5 SOL"), ({"fills": 11}, "10 buys"), ({"fills": 2.5}, "whole"),
     ({"minutes": 181}, "180 minutes"), ({"loss_sol": 2.01}, "2 SOL"), ({"loss_sol": 0}, "2 SOL"),
     ({"more_blocks": 4}, "3 more"), ({"continue_at": 0.49}, "half"), ({"continue_at": 1.01}, "half"),
     ({"buy_sol": True}, "0.5 SOL"), ({"fills": "10"}, "10 buys"), ({"wallet": "x"}, "unknown")],
)  # fmt: skip
def test_a_cap_out_of_bounds_changes_nothing(env, values, words):
    _on(env)
    before = AT.autotest_file(env.paths).read_text()
    with pytest.raises(ValueError, match=words):
        AT.set_autotest(env.paths, True, values=values, words=YES, by="cli", now=NOW)
    assert AT.autotest_file(env.paths).read_text() == before


def test_a_hand_edited_or_broken_file_never_starts_anything_wider(env):
    """A read never raises; a cap out of its bounds falls back to the owner's 7 Nov value alone; without the owner's
    words it is off."""
    f = AT.autotest_file(env.paths)
    yes = {"approved": {"words": YES, "at": NOW}, "wallet": W, "oktech_wallet": "SNIPER"}
    f.write_text(json.dumps({"on": True, "buy_sol": 5, "fills": 40, "minutes": 120, **yes}))
    at = AT.read_autotest(env.paths)
    assert at["on"] and at["buy_sol"] == 0.2 and at["fills"] == 10
    f.write_text(json.dumps({"on": True, "buy_sol": 0.2, "wallet": W, "oktech_wallet": "SNIPER"}))  # no words: no yes
    assert AT.read_autotest(env.paths)["on"] is False
    for wallet in ({"oktech_wallet": "SNIPER"}, {"wallet": "not a wallet", "oktech_wallet": "SNIPER"}, {"wallet": W}):
        f.write_text(json.dumps({"on": True, "approved": yes["approved"], **wallet}))  # a yes names its wallet
        assert AT.read_autotest(env.paths)["on"] is False, wallet
    for text in ("{not json", "[1]", json.dumps({"on": "yes", "approved": {"words": YES}}), ""):
        f.write_text(text)
        assert AT.read_autotest(env.paths)["on"] is False, text


def test_operate_autotest_turns_it_on_and_off_with_the_owners_words(env, capsys):
    from snipetech.cli import build_parser

    p = build_parser()

    def run(*argv):
        return AT.autotest_cli(env.cfg, p.parse_args(["operate", *argv]), now=NOW)

    assert run("autotest") == 0 and "off" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="--approved"):
        run("autotest", "--on", "--wallet", W)
    # the owner's yes names its wallet (rule 1: a wallet, a size, a duration): the session passes the one they named
    with pytest.raises(SystemExit, match="--wallet"):
        run("autotest", "--on", "--approved", YES)
    with pytest.raises(SystemExit, match="9xQeWv"):  # not the sniper wallet the config names: nothing changes
        run("autotest", "--on", "--approved", YES, "--wallet", OTHER)
    assert not AT.autotest_file(env.paths).exists()
    assert run("autotest", "--on", "--approved", YES, "--wallet", W, "--fills", "5", "--minutes", "90") == 0
    at = AT.read_autotest(env.paths)
    assert at["on"] and at["approved"]["words"] == YES and (at["fills"], at["minutes"], at["buy_sol"]) == (5, 90, 0.2)
    assert (at["wallet"], at["oktech_wallet"]) == (W, "SNIPER")
    out = capsys.readouterr().out
    assert "0.2 SOL a buy" in out and W[:6] in out and "SNIPER" in out
    with pytest.raises(SystemExit, match="0.5 SOL"):
        run("autotest", "--on", "--approved", YES, "--wallet", W, "--buy-sol", "0.6")
    with pytest.raises(SystemExit, match="not both"):
        run("autotest", "--on", "--off", "--approved", YES, "--wallet", W)
    with pytest.raises(SystemExit, match="go with `operate autotest"):
        run("--on", "--approved", YES, "--wallet", W)
    with pytest.raises(SystemExit, match="with --on"):
        run("autotest", "--off", "--fills", "3")
    with pytest.raises(SystemExit, match="with --on"):
        run("autotest", "--off", "--wallet", W)
    assert run("autotest", "--off") == 0 and AT.read_autotest(env.paths)["on"] is False


def test_test_auto_on_off_from_telegram_is_the_owners_yes(env):
    shown = LTT._send(env, "auto")  # the plan /test auto on says yes to names the wallet it is for
    assert "off" in shown and W[:6] in shown and "SNIPER" in shown
    reply = LTT._send(env, "auto", "on")
    at = AT.read_autotest(env.paths)
    assert at["on"] and at["approved"]["words"] == "/test auto on" and at["approved"]["at"] == NOW - 5
    assert {k: at[k] for k in AT.DEFAULTS} == AT.DEFAULTS  # always the plan the owner said yes to
    assert (at["wallet"], at["oktech_wallet"]) == (W, "SNIPER")  # the wallet the config names at the owner's yes
    assert "0.2 SOL a buy" in reply and "10 buys" in reply and "120 minutes" in reply and "half of paper" in reply
    assert W[:6] in reply
    assert "/test auto off" in reply and "[decision]" in env.paths.notes.read_text()
    assert "on since" in LTT._send(env, "auto")
    reply = LTT._send(env, "auto", "off")
    assert AT.read_autotest(env.paths)["on"] is False and "off" in reply
    assert "Nothing changed" in LTT._send(env, "auto", "maybe")
    assert "min old" in LTT._send(env, "auto", "on", date=NOW - 600)  # a yes that waited is refused
    assert AT.read_autotest(env.paths)["on"] is False


# -- the automatic request -------------------------------------------------------------------------------------------


def test_nothing_starts_by_itself_unless_every_condition_holds(env):
    LTT._freeze(env.store, BEST, pnl=0.02)
    _round(env, NOW + 60)
    assert LT.load(env.paths) is None and env.svc.created == []  # off: nothing, however good the strategy
    _on(env)
    env.paths.stop_flag.write_text("1")
    _round(env, NOW + 120)
    assert LT.load(env.paths) is None  # the owner's /stop
    env.paths.stop_flag.unlink()
    env.sol = 9.3  # 9.4 SOL: under the wallet-wide guard's 10 SOL floor
    _round(env, NOW + 180)
    assert LT.load(env.paths) is None
    env.sol = 11.8
    env.snap = Snapshot(ts=NOW + 190, wallet=W, native_sol=11.8, wsol=0.1, held=[], snipers=[])
    LTT._send(env, BEST, "go", at=NOW + 200)  # the owner's own test is requested: one test at a time
    owner = LT.load(env.paths)
    assert owner["by"] == "telegram"
    asyncio.run(AT.step(env.svc, env.cfg, env.paths, NOW + 240, snap=env.snap, notes=env.notes, notifier=env.notifier))
    assert LT.load(env.paths) == owner and _seq(env) is None
    assert env.svc.created == [] and env.started == []


def test_nothing_starts_while_no_strategy_passes_on_paper(env):
    LTT._freeze(env.store, BEST, n=60, pnl=0.02)  # 60 forward trades of the 100
    _on(env)
    _round(env, NOW + 60)
    assert LT.load(env.paths) is None and _seq(env) is None


def test_nothing_starts_by_itself_on_a_strategy_scored_below_the_measured_cost(env):
    """The standing yes covers a strategy that passes "at real costs". An analyst's freeze takes the CLI's 0.0035 SOL a
    transaction (0.007 a round trip) while the measure stage measures 0.0094 a round trip: a lower bound of +0.001 there
    is negative at the real cost, and no live test may start on it with no human in the loop."""
    LTT.measured_cost(env.store)
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env)
    _round(env, NOW + 60)
    assert LT.load(env.paths) is None and env.svc.created == [] and _seq(env) is None
    LTT._freeze(env.store, SECOND, pnl=0.01, per_tx=0.0047)  # frozen at the measured cost: it may start
    _round(env, NOW + 960)
    assert LT.load(env.paths)["id"] == SECOND and LT.load(env.paths)["state"] == "running"


def test_the_best_passing_strategy_starts_at_once_with_the_owners_caps(env):
    LTT._freeze(env.store, SECOND, pnl=0.01)
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env, fills=5, minutes=90, loss_sol=0.5)
    _round(env, NOW + 60)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["id"] == BEST and t["by"] == "autotest"  # started by the same round
    assert (t["buy_sol"], t["fills"], t["minutes"], t["floor"]) == (0.2, 5, 90, 11.4)  # 0.5 SOL below 11.9
    assert t["sequence"]["block"] == 1 and t["approved"]["words"] == YES and t["forward"]["mean"] == pytest.approx(0.02)
    (job,) = env.started
    assert job["argv"][job["argv"].index("--max-fills") + 1] == "5"
    assert job["argv"][job["argv"].index("--max-minutes") + 1] == "90"
    (asked,) = _said(env, "Automatic live test")
    assert YES in asked and BEST in asked and "0.2 SOL a buy" in asked and W[:6] in asked and "SNIPER" in asked
    assert _said(env, "Live test started") and "[decision] Automatic live test" in env.paths.notes.read_text()
    seq = _seq(env)
    assert seq["state"] == "running" and seq["strategy"] == BEST and seq["floor"] == 11.4


def test_a_test_runs_at_the_strategys_own_size_never_another(env):
    """The smaller of the two sizes, and never a size the strategy was not scored at: a round trip costs about 0.009
    SOL whatever the size (7 Nov), so a test at another size says nothing about the strategy."""
    LTT._freeze(env.store, BEST, pnl=0.02, buy=0.3)
    _on(env)  # 0.2 SOL a buy
    _round(env, NOW + 60)
    assert LT.load(env.paths) is None and env.svc.created == []
    (why,) = _said(env, "0.3 SOL a buy")
    assert "0.2" in why and "another size" in why
    _round(env, NOW + 960)
    assert len(_said(env, "0.3 SOL a buy")) == 1  # said once, not every round


def test_a_smaller_strategy_is_tested_at_its_own_size(env):
    LTT._freeze(env.store, BEST, pnl=0.02, buy=0.1)
    _on(env)
    _round(env, NOW + 60)
    assert LT.load(env.paths)["buy_sol"] == 0.1


def test_the_standing_yes_is_for_the_wallet_it_names_and_no_other(env):
    """The owner's yes of 7 Nov names sniper wallet 6VFrZ15V... (rule 1: a yes is for a wallet, a size, a duration).
    With the config pointed at another wallet (a fresh sniper wallet, a key rotation), no test starts by itself there,
    whatever that wallet holds; the owner is told once, and a new /test auto on is a yes for the new wallet."""
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env)
    env.cfg.operator.wallet, env.sol = OTHER, 25.0
    _round(env, NOW + 60)
    assert LT.load(env.paths) is None and env.svc.created == [] and _seq(env) is None
    (line,) = _said(env, "another wallet")
    assert W[:6] in line and OTHER[:6] in line and "/test auto on" in line
    _round(env, NOW + 960)
    assert len(_said(env, "another wallet")) == 1 and env.svc.created == []  # said once, not every round
    env.cfg.operator.wallet, env.sol = W, 11.8
    env.cfg.oktech.wallet = "SNIPER2"  # the OKTech wallet the yes was for is SNIPER
    _round(env, NOW + 1860)
    assert LT.load(env.paths) is None and env.svc.created == []
    env.cfg.oktech.wallet = ""  # back to SNIPER, the one the yes names
    _round(env, NOW + 2760)
    assert LT.load(env.paths)["state"] == "running" and LT.load(env.paths)["wallet"] == W
    assert _seq(env)["wallet"] == W and _seq(env)["oktech_wallet"] == "SNIPER"


def test_a_wallet_changed_between_blocks_ends_the_sequence(env):
    """Block 2 would run on the new wallet with block 1's floor: the sequence ends instead."""
    _start(env)
    _play(env, [0.02] * 10)
    env.cfg.operator.wallet, env.sol = OTHER, 25.0
    _round(env, NOW + 60 + 2 * HOUR)
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "ended" and "wallet" in _seq(env)["why"]
    assert AT.read_autotest(env.paths)["on"] is False


def test_the_round_never_starts_an_automatic_request_for_another_wallet(env):
    _start(env)
    t = LT.load(env.paths)
    assert AT.holds(env.paths, t, env.cfg) is None
    assert "wallet" in AT.holds(env.paths, t | {"wallet": OTHER}, env.cfg)
    assert "OKTech wallet" in AT.holds(env.paths, t | {"oktech_wallet": "SNIPER2"}, env.cfg)
    env.cfg.oktech.wallet = "SNIPER2"
    assert "OKTech wallet" in AT.holds(env.paths, t, env.cfg)


# -- the sequence ------------------------------------------------------------------------------------------------------


def _start(e, **caps):
    LTT._freeze(e.store, BEST, pnl=0.02)  # paper: +0.02 SOL a trade
    _on(e, **caps)
    _round(e, NOW + 60)
    assert LT.load(e.paths)["state"] == "running"
    e.sent.clear()


def test_live_at_half_of_paper_goes_on_with_the_next_block_at_the_first_blocks_floor(env):
    _start(env)
    first = LT.load(env.paths)
    _play(env, [0.01] * 10)  # live +0.01: exactly half of paper
    _round(env, NOW + 60 + 2 * HOUR)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["sequence"]["block"] == 2 and t["requested_at"] != first["requested_at"]
    assert (t["id"], t["buy_sol"], t["fills"], t["minutes"]) == (BEST, 0.2, 10, 120)
    assert t["floor"] == first["floor"] == 10.9  # 1 SOL below the first block's start, not below 11.9 + 0.1
    (line,) = _said(env, "block 1")
    assert "+0.0100" in line and "+0.0200" in line and "block 2" in line and "10.90" in line
    assert len(env.svc.created) == 2 and len(env.started) == 2
    history = [json.loads(x) for x in LT.history_path(env.paths).read_text().splitlines()]
    assert [(h.get("sequence") or {}).get("block") for h in history] == [1]


def test_live_below_half_of_paper_stops_the_sequence_and_the_analyst_works_out_why(env):
    _start(env)
    _play(env, [0.0099] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["state"] == "done" and len(env.svc.created) == 1  # no second block
    seq = _seq(env)
    assert seq["state"] == "ended" and "below" in seq["why"]
    (line,) = _said(env, "Automatic live test ended")
    assert "half of paper" in line and "analyst works out why" in line and "+0.0099" in line
    assert "analyst works out why" in env.paths.notes.read_text()
    at = AT.read_autotest(env.paths)
    assert at["on"] is False and at["used"]["sequence"] == seq["id"] and "below" in at["used"]["result"]
    lines = [json.loads(x) for x in LT.history_path(env.paths).read_text().splitlines()]
    assert lines[-1]["kind"] == "sequence" and lines[-1]["id"] == seq["id"] and lines[-1]["trades"] == 10


def test_the_whole_sequence_is_four_blocks_at_most_and_then_the_switch_is_off(env):
    _start(env)
    at = NOW + 60
    for block in range(1, 5):
        assert LT.load(env.paths)["sequence"]["block"] == block
        _play(env, [0.02] * 10)
        at += 2 * HOUR
        _round(env, at)
    assert len(env.svc.created) == 4 and LT.load(env.paths)["state"] == "done"
    seq = _seq(env)
    assert seq["state"] == "ended" and len(seq["blocks"]) == 4 and "all 4 blocks" in seq["why"]
    assert AT.read_autotest(env.paths)["on"] is False
    (line,) = _said(env, "Automatic live test ended")
    assert "40 live trades" in line and "/test auto on" in line
    _round(env, at + 900)  # a strategy still passes; the yes is used: nothing more starts
    assert len(env.svc.created) == 4


def test_each_block_is_judged_on_every_live_trade_of_the_sequence_so_far(env):
    """After block 2 the live mean is that of its 20 trades: 0.03 then 0.0 make 0.015, above half of paper."""
    _start(env)
    _play(env, [0.03] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    _play(env, [0.0] * 10)
    _round(env, NOW + 60 + 4 * HOUR)
    assert LT.load(env.paths)["sequence"]["block"] == 3 and LT.load(env.paths)["state"] == "running"


def test_the_floor_counts_from_the_first_blocks_start_whatever_the_wallet_did_since(env):
    """The whole sequence may lose at most 1 SOL: each block's floor is the first one's. With the wallet down to where
    another block has no room above that floor (a withdrawal, 6 Nov 15:16), the sequence ends."""
    _start(env)
    _play(env, [0.03] * 10)  # the wallet up 0.3 SOL
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["floor"] == 10.9
    _play(env, [0.01] * 10)
    env.sol -= 1.2  # the owner takes 1.2 SOL out: 10.9 SOL left, under the 10.9 floor plus a buy and its fees
    _round(env, NOW + 60 + 4 * HOUR)
    assert len(env.svc.created) == 2 and _seq(env)["state"] == "ended"
    (line,) = _said(env, "Automatic live test ended")
    assert "10.90" in line and "room" in line
    assert AT.read_autotest(env.paths)["on"] is False


@pytest.mark.parametrize(
    "reason, fills",
    [("time limit; the wallet holds nothing, so the snipers it armed are stopped", 6),
     ("stopped by the owner's /stop: no more re-arms; the wallet holds nothing, so the snipers it armed are stopped", 10),
     ("EMERGENCY: equity below floor 10.9 (balance 10.8); stopped re-arming", 4),
     ("the wallet-wide guard holds (today is 1.02 SOL down): no more re-arms", 3)],
)  # fmt: skip
def test_a_block_that_ends_early_ends_the_sequence(env, reason, fills):
    _start(env)
    _play(env, [0.05] * fills, reason=reason)
    _round(env, NOW + 60 + 2 * HOUR)
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "ended"
    (line,) = _said(env, "Automatic live test ended")
    assert "early" in line and reason.split(":")[0].split(";")[0] in line
    assert AT.read_autotest(env.paths)["on"] is False


def test_a_block_that_did_not_start_ends_the_sequence(env):
    _start(env)
    _play(env, [0.02] * 10)
    env.svc.answer = RuntimeError("OKTech answered 502")  # block 2's sniper is not created
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["state"] == "done" and not LT.load(env.paths)["started"]
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert _seq(env)["state"] == "ended" and AT.read_autotest(env.paths)["on"] is False
    (line,) = _said(env, "Automatic live test ended")
    assert "block 2 did not start" in line and "502" in line


def test_the_live_result_is_read_from_the_measure_stages_fills_never_the_balance(env):
    """Deposit-proof: the cycler's start and end balance moved +5 SOL (the owner topped the wallet up mid-block) while
    every trade lost; only our own round trips count."""
    _start(env)
    _play(env, [-0.01] * 10, report={"start_sol": 11.8, "end_sol": 16.7})
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "ended" and len(env.svc.created) == 1
    assert "-0.0100" in _said(env, "Automatic live test ended")[0]


def test_the_books_copies_from_the_same_wallet_are_not_the_tests_trades(env):
    """The book trades from the same wallet and its buys count toward the cycler's cap; its copies (a copied leader in
    the fills) are left out of the test's live result."""
    _start(env)
    _play(env, [0.002] * 7, copies=[0.3] * 3)
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "ended"
    (line,) = _said(env, "Automatic live test ended")
    assert "7 live trades" in line and "+0.0020" in line


def test_the_decision_waits_for_the_measure_stage(env):
    _start(env)
    trades = _play(env, [0.02] * 10, measured=False)
    _round(env, NOW + 60 + 2 * HOUR)  # the block is done; its trades are not in the fills yet
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "running" and AT.read_autotest(env.paths)["on"]
    assert not _said(env, "Automatic live test")
    _measure(env, trades)
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert LT.load(env.paths)["sequence"]["block"] == 2 and len(env.svc.created) == 2


def test_trades_the_measure_stage_never_reads_count_as_losses(env):
    """Past the wait, a buy with no sell in the fills is counted at its whole cost: the wallet held nothing of value when
    the block closed, and a sequence never goes on on numbers it could not see."""
    _start(env)
    _play(env, [0.02] * 10, measured=False)
    for k in range(5):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k)
    assert _seq(env)["state"] == "ended" and len(env.svc.created) == 1
    (line,) = _said(env, "Automatic live test ended")
    assert "10 not measured" in line


def test_a_refusal_that_passes_by_itself_makes_the_next_block_wait(env):
    _start(env)
    _play(env, [0.02] * 10)
    _round(env, NOW + 60 + 2 * HOUR, tape=False)  # the recorder is behind: the next block waits
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "running"
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert LT.load(env.paths)["sequence"]["block"] == 2


def test_a_next_block_that_cannot_start_within_the_hour_ends_the_sequence(env):
    _start(env)
    _play(env, [0.02] * 10)
    for k in range(6):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k, tape=False)
    assert _seq(env)["state"] == "ended" and len(env.svc.created) == 1
    assert "tape" in _said(env, "Automatic live test ended")[0]


def test_the_guard_tripped_between_blocks_ends_the_sequence(env):
    _start(env)
    _play(env, [0.02] * 10)
    LTT._lost(env.store, -1.5, ts=NOW + 60 + HOUR)  # a day 1.3 SOL down
    _round(env, NOW + 60 + 2 * HOUR)
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "ended"
    assert "wallet-wide guard" in _said(env, "Automatic live test ended")[0]


def test_one_sequence_at_a_time_and_one_per_yes(env):
    _start(env)
    LTT._freeze(env.store, SECOND, pnl=0.03)  # a better one appears while block 1 runs
    _round(env, NOW + 60 + 900)
    assert len(env.svc.created) == 1 and LT.load(env.paths)["id"] == BEST
    _play(env, [0.0] * 10)  # below half: the sequence ends
    _round(env, NOW + 60 + 2 * HOUR)
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert len(env.svc.created) == 1 and AT.read_autotest(env.paths)["on"] is False
    first = _seq(env)["id"]
    _on(env)  # the owner's yes again: a new sequence, on the best strategy now
    _round(env, NOW + 60 + 2 * HOUR + 1800)
    assert len(env.svc.created) == 2 and LT.load(env.paths)["id"] == SECOND and _seq(env)["id"] != first


def test_turning_it_off_lets_the_running_block_finish_and_starts_no_more(env):
    _start(env)
    LTT._send(env, "auto", "off", at=NOW + 600)
    _play(env, [0.02] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "ended"
    assert "turned the automatic test off" in _said(env, "Automatic live test ended")[0]


def test_it_cannot_be_turned_on_again_while_a_sequence_still_runs(env):
    """One sequence per yes: turned off mid-block, the sequence ends with that block; a yes sent meanwhile is refused
    (it would start a second sequence with a floor of its own), and is taken once the sequence has ended."""
    _start(env)
    LTT._send(env, "auto", "off", at=NOW + 600)
    reply = LTT._send(env, "auto", "on", at=NOW + 900)
    assert "running" in reply and AT.read_autotest(env.paths)["on"] is False
    with pytest.raises(ValueError, match="running"):
        AT.set_autotest(env.paths, True, words=YES, by="cli", now=NOW + 900, wallet=W, oktech_wallet="SNIPER")
    _play(env, [0.02] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "ended"
    assert "Automatic live test on" in LTT._send(env, "auto", "on", at=NOW + 60 + 2 * HOUR + 60)


def test_a_request_the_owner_turned_off_before_it_started_is_never_started(env):
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env)
    env.snap = Snapshot(ts=NOW + 60, wallet=W, native_sol=11.8, wsol=0.1, held=[], snipers=[])
    asyncio.run(AT.step(env.svc, env.cfg, env.paths, NOW + 60, snap=env.snap, notes=env.notes, notifier=env.notifier))
    assert LT.load(env.paths)["state"] == "requested"  # the step only writes the request
    AT.set_autotest(env.paths, False, by="telegram", now=NOW + 90)
    _round(env, NOW + 120)
    t = LT.load(env.paths)
    assert t["state"] == "done" and not t["started"] and "turned off" in t["error"] and env.svc.created == []


def test_the_round_never_starts_an_automatic_request_beyond_its_blocks_or_its_floor(env):
    """The round's own check of every automatic request (autotest.holds), whatever wrote it: its sequence's newest
    block, at most 1 + more_blocks of them, at the sequence's floor."""
    _start(env)
    t, seq = LT.load(env.paths), _seq(env)
    assert AT.holds(env.paths, t, env.cfg) is None
    assert "floor" in AT.holds(env.paths, t | {"floor": t["floor"] - 1}, env.cfg)
    assert "blocks" in AT.holds(env.paths, t | {"sequence": seq and {"id": seq["id"], "block": 5}}, env.cfg)
    assert "own" in AT.holds(env.paths, t | {"requested_at": t["requested_at"] + 1}, env.cfg)


def test_stop_ends_the_sequence(env):
    _start(env)
    LTT._send(env, name="stop", at=NOW + 600)
    _play(env, [0.02] * 10, reason="stopped by the owner's /stop: no more re-arms")
    LTT._send(env, name="resume", at=NOW + 700)
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "ended" and len(env.svc.created) == 1 and AT.read_autotest(env.paths)["on"] is False


def test_test_shows_the_automatic_block_and_the_switch(env):
    _start(env)
    assert "automatic test, block 1" in LTT._send(env, at=NOW + 600)
    reply = LTT._send(env, "auto", at=NOW + 600)
    assert "running" in reply and "block 1" in reply and BEST in reply


# -- nobody else -------------------------------------------------------------------------------------------------------


def test_no_agent_tool_can_turn_it_on_or_touch_the_sequence(env):
    from snipetech.operator.tools import make_operator_tools

    tools = {t.name: t for t in make_operator_tools(env.svc, env.paths, env.jobs, env.cfg.operator, env.notes)}
    on = json.dumps({"on": True, "buy_sol": 0.5, "fills": 10, "minutes": 180, "approved": {"words": "yes", "at": NOW}})
    calls = [("start_job", {"argv": ["operate", "autotest", "--on", "--approved", "yes"]}),
             ("write_report", {"name": "AUTOTEST", "text": on}), ("write_report", {"name": "../operator/AUTOTEST", "text": on}),
             ("write_report", {"name": "autotest", "text": on}), ("set_candidates", {"items": [json.loads(on)]}),
             ("write_note", {"text": on, "level": "decision"}), ("add_lesson", {"text": "turn it on", "evidence": on, "n": 1})]  # fmt: skip
    for name, kw in calls:
        asyncio.run(tools[name].call(kw))
    assert not AT.autotest_file(env.paths).exists() and not AT.sequence_path(env.paths).exists()
    assert AT.read_autotest(env.paths)["on"] is False and env.started == []


def test_the_agents_are_told_the_standing_yes_and_the_guard_are_the_owners():
    """The soft layer: a turn sees a test the round started by itself as the owner's, and knows the guard is too."""
    from snipetech.operator.prompts import ANALYST_SYSTEM, OPERATOR_SYSTEM

    for system in (OPERATOR_SYSTEM, ANALYST_SYSTEM):
        assert "operator/AUTOTEST" in system and "operator/RISK" in system and "never write" in system


def test_the_agents_code_never_loads_the_automatic_test():
    code = ("import sys, snipetech.operator.tools, snipetech.operator.turn, snipetech.strategist.turn, "
            "snipetech.strategist.run; print('snipetech.operator.autotest' in sys.modules)")  # fmt: skip
    src = str(Path(snipetech.__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env=os.environ | {"PYTHONPATH": src})  # fmt: skip
    assert out.stdout.strip() == "False"
