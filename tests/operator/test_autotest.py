"""The owner's standing yes (operator/autotest.py). The 09:15 plan of 7 Nov ("Yes, use this plan"): when a strategy
passes on paper, one live test starts by itself (0.2 SOL a buy, one coin at a time, at most 10 buys, at most 2 hours,
stopping 1 SOL below its start) and goes on only while live is at least half of paper. Widened at about 12:10 UTC
(docs/live-recipe.md, "The owner's yes widened: live trading never stops"): blocks without end while live holds up; a
strategy below half of paper, or at its floor, is dropped for good and the next one that passes on paper takes over; an
execution fault holds new blocks until it clears and never drops a strategy; nothing turns it off but the owner."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import snipetech
from snipetech.analysis import paper
from snipetech.models import Trade
from snipetech.operator import autotest as AT
from snipetech.operator import live_test as LT
from snipetech.operator.risk_guard import set_risk
from snipetech.operator.snapshot import Snapshot
from tests.operator import test_live_test as LTT

NOW, W, BEST, SECOND, OK_ID = LTT.NOW, LTT.W, LTT.BEST, LTT.SECOND, LTT.OK_ID
OTHER = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"  # another wallet the config could name
YES = "Yes, use this plan"
CAP = "cap reached: {n} fills since start; the wallet holds nothing, so the snipers it armed are stopped"
TIME = "time limit; the wallet holds nothing, so the snipers it armed are stopped"
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


def _guard_off(e, at=NOW - HOUR):
    """The owner's /risk stop off and /risk floor off (the widened yes: no day stop and no wallet floor)."""
    set_risk(e.paths, {"day_stop_sol": "off", "floor_sol": "off"}, "telegram", at)


def _round(e, at, tape=True, measure=True, readable=True, wsol=0.1):
    """One operator round at `at`, with a fresh snapshot of the configured wallet (unless `readable` is False: the RPC
    did not answer) and its coins (e.svc.held), (unless `tape` is False) a current recorder and (unless `measure` is
    False) a measure stage that ran a minute before the round. `wsol`: the wrapped SOL the wallet holds."""
    if tape:
        LTT._tape(e.store, at - 5, k=10_000 + e.k)
        e.k += 1
    if measure:
        e.store.set_coverage("measure:last_ts", {"ts": int(at) - 60})
    sol, wsol = (e.sol, wsol) if readable else (None, None)
    e.snap = Snapshot(ts=at, wallet=e.cfg.operator.wallet, native_sol=sol, wsol=wsol, held=list(e.svc.held), snipers=[])
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


def _ours(e, sig, ts, mint, buy, sol, tokens=1_000_000):
    """One trade of ours on the tape: `sol` paid on a buy (its fee included) or received on a sale (its fee taken)."""
    fee = 2_000_000
    e.store.add_trades([Trade(sig=sig, ev=0, slot=5_000 + e.k, pos=0, ts=int(ts), mint=mint, venue="curve", user=W,
                              is_buy=buy, quote=round(sol * 1e9) + (-fee if buy else fee), base=int(tokens), fee=fee,
                              fee_bps=100, q_after=1, b_after=1)])  # fmt: skip
    e.k += 1


def _cycler_ended(e, t, rep, at, code=0, log=None):
    """The cycler's job ends at `at` with its report (or, with `log`, without one): its exit file is dated then, as the
    ops runner writes it."""
    LTT._end_job(e.paths, t["job_id"], code, rep, log=log)
    os.utime(e.paths.jobs / f"{t['job_id']}.exit", (at, at))


def _trade(e, pnls, *, measured=True, copies=()):
    """The block running now: its sniper armed, one coin bought and sold for each of `pnls` (the tape has our buys and
    our sales), the book's copies from the same wallet meanwhile (`copies`: their pnls, a copied leader named in the
    fills). Returns (the block, its trades, when its last sale was)."""
    t = LT.load(e.paths)
    assert t["state"] == "running", t
    LTT._armed(e)
    start = int(t["started_at"])
    trades = []
    for k, (pnl, leader) in enumerate([(p, None) for p in pnls] + [(p, "Leader" + "L" * 38) for p in copies]):
        sig, ts, mint = f"buy-{start}-{k}", start + 60 + 30 * k, f"Coin{start}x{k}"
        _ours(e, sig, ts, mint, True, 0.202)
        _ours(e, f"sell-{sig}", ts + 20, mint, False, 0.202 + pnl)
        trades.append((sig, ts, mint, pnl, leader))
    if measured:
        _measure(e, trades)
    e.sol += sum(pnls) + sum(copies)
    return t, trades, start + 60 + 30 * max(len(trades) - 1, 0) + 20


def _play(e, pnls, *, reason=None, measured=True, copies=(), report=None):
    """The block running now trades `pnls` (see _trade), then its cycler ends half a minute after the last sale with its
    report. Returns the trades, for a measure stage that reads them later."""
    t, trades, last = _trade(e, pnls, measured=measured, copies=copies)
    n = len(trades)
    rep = {"start_sol": 11.8, "end_sol": 11.8 + sum(pnls) + sum(copies), "reason": reason or CAP.format(n=n),
           "fills": n, "restarts": 20, "held": [], "left_running": []} | (report or {})  # fmt: skip
    _cycler_ended(e, t, rep, last + 30)
    return trades


def _seq(e):
    return AT.load_seq(e.paths)


def _said(e, words):
    return [s for s in e.sent if words in s]


def _start(e, **caps):
    LTT._freeze(e.store, BEST, pnl=0.02)  # paper: +0.02 SOL a trade
    _on(e, **caps)
    _round(e, NOW + 60)
    assert LT.load(e.paths)["state"] == "running"
    e.sent.clear()


# -- the owner's switch --------------------------------------------------------------------------------------------


def test_with_no_file_it_is_off_and_the_plan_is_the_owners(env):
    at = AT.read_autotest(env.paths)
    assert at["on"] is False and at["approved"] is None and AT.autotest_file(env.paths) == env.paths.root / "AUTOTEST"
    # the widened yes (7 Nov ~12:10): blocks of 10 buys and 120 minutes at 0.2 SOL, a strategy dropped 1 SOL down or
    # below half of paper; no number of blocks (there is no limit on them)
    assert AT.DEFAULTS == {"buy_sol": 0.2, "fills": 10, "minutes": 120, "loss_sol": 1.0, "continue_at": 0.5}
    assert {k: at[k] for k in AT.DEFAULTS} == AT.DEFAULTS
    assert AT.PLAN == "never-stops" and at["plan"] is None


def test_turning_it_on_records_the_owners_words_and_time(env):
    at = _on(env)
    assert at["on"] and at["approved"] == {"words": YES, "at": NOW - HOUR} and at["plan"] == "never-stops"
    data = json.loads(AT.autotest_file(env.paths).read_text())
    assert data["on"] is True and data["by"] == "cli" and data["ts"] == NOW - HOUR and data["buy_sol"] == 0.2
    assert data["plan"] == "never-stops"
    with pytest.raises(ValueError, match="words"):
        AT.set_autotest(env.paths, True, words="  ", by="cli", now=NOW, wallet=W, oktech_wallet="SNIPER")
    off = AT.set_autotest(env.paths, False, by="telegram", now=NOW)
    assert off["on"] is False and off["approved"] == {"words": YES, "at": NOW - HOUR}  # kept for the record


@pytest.mark.parametrize(
    "values, words",
    [({"buy_sol": 0.51}, "0.5 SOL"), ({"buy_sol": 0}, "0.5 SOL"), ({"fills": 11}, "10 buys"), ({"fills": 2.5}, "whole"),
     ({"minutes": 181}, "180 minutes"), ({"loss_sol": 2.01}, "2 SOL"), ({"loss_sol": 0}, "2 SOL"),
     ({"more_blocks": 3}, "unknown"), ({"continue_at": 0.49}, "half"), ({"continue_at": 1.01}, "half"),
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
    assert (at["wallet"], at["oktech_wallet"]) == (W, "SNIPER") and at["plan"] == "never-stops"
    out = capsys.readouterr().out
    assert "0.2 SOL a buy" in out and W[:6] in out and "SNIPER" in out and "no limit on blocks" in out
    with pytest.raises(SystemExit, match="0.5 SOL"):
        run("autotest", "--on", "--approved", YES, "--wallet", W, "--buy-sol", "0.6")
    with pytest.raises(SystemExit):  # the widened plan has no number of blocks to set
        run("autotest", "--on", "--approved", YES, "--wallet", W, "--more-blocks", "3")
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
    assert "off" in shown and W[:6] in shown and "SNIPER" in shown and "never turns itself off" in shown
    reply = LTT._send(env, "auto", "on")
    at = AT.read_autotest(env.paths)
    assert at["on"] and at["approved"]["words"] == "/test auto on" and at["approved"]["at"] == NOW - 5
    assert {k: at[k] for k in AT.DEFAULTS} == AT.DEFAULTS and at["plan"] == "never-stops"  # the widened plan
    assert (at["wallet"], at["oktech_wallet"]) == (W, "SNIPER")  # the wallet the config names at the owner's yes
    assert "0.2 SOL a buy" in reply and "10 buys" in reply and "120 minutes" in reply and "half of paper" in reply
    assert W[:6] in reply and "no limit on blocks" in reply and "dropped" in reply
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
    env.sol = 9.3  # 9.4 SOL: under the wallet-wide guard's 10 SOL floor (on until the owner switches it off)
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
    """The standing yes covers a strategy that passes "at real costs". An analyst's freeze took the CLI's 0.0035 SOL a
    transaction (0.007 a round trip) until 7 Nov while the measure stage measures 0.0094 a round trip: a lower bound of
    +0.001 there is negative at the real cost, and no live test may start on it with no human in the loop."""
    LTT.measured_cost(env.store)
    LTT._freeze(env.store, BEST, pnl=0.02, per_tx=0.0035)
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
    (asked,) = env.sent  # the owner hears that a strategy starts; the block's own start is only in the notes
    assert asked.startswith("Automatic live test") and YES in asked and BEST in asked and "0.2 SOL a buy" in asked
    assert W[:6] in asked and "SNIPER" in asked and "block after block" in asked
    notes = env.paths.notes.read_text()
    assert "[decision] Automatic live test" in notes and "Live test started" in notes
    seq = _seq(env)
    assert seq["state"] == "running" and seq["strategy"] == BEST and seq["floor"] == 11.4 and seq["plan"] == AT.PLAN


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


def test_a_strategy_above_the_yes_size_is_refused_and_the_best_one_that_fits_starts(env):
    """Live trading never stops: a better strategy scored at 0.3 SOL a buy is refused as before (told once a day), and
    the best one scored at the yes's size or less trades instead."""
    LTT._freeze(env.store, BEST, pnl=0.03, buy=0.3)
    LTT._freeze(env.store, SECOND, pnl=0.01)
    _on(env)
    _round(env, NOW + 60)
    t = LT.load(env.paths)
    assert t["id"] == SECOND and t["buy_sol"] == 0.2 and t["state"] == "running"
    (why,) = _said(env, "0.3 SOL a buy")
    assert BEST in why and "another size" in why


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


def test_a_wallet_changed_between_blocks_ends_the_strategys_run_and_nothing_starts_elsewhere(env):
    """Block 2 would run on the new wallet with block 1's floor: the strategy's run ends instead (not a drop: nothing
    says it is a bad strategy), the automatic test stays on, and nothing starts by itself on the other wallet."""
    _start(env)
    _play(env, [0.02] * 10)
    env.cfg.operator.wallet, env.sol = OTHER, 25.0
    _round(env, NOW + 60 + 2 * HOUR)
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "ended" and "wallet" in _seq(env)["why"]
    assert _seq(env).get("dropped") in (None, []) and AT.read_autotest(env.paths)["on"] is True
    assert _said(env, "Automatic live test ended") and _said(env, "another wallet")


def test_the_round_never_starts_an_automatic_request_for_another_wallet(env):
    _start(env)
    t = LT.load(env.paths)
    assert AT.holds(env.paths, t, env.cfg) is None
    assert "wallet" in AT.holds(env.paths, t | {"wallet": OTHER}, env.cfg)
    assert "OKTech wallet" in AT.holds(env.paths, t | {"oktech_wallet": "SNIPER2"}, env.cfg)
    env.cfg.oktech.wallet = "SNIPER2"
    assert "OKTech wallet" in AT.holds(env.paths, t, env.cfg)


# -- block after block -------------------------------------------------------------------------------------------------


def test_live_at_half_of_paper_goes_on_with_the_next_block_at_the_first_blocks_floor(env):
    _start(env)
    first = LT.load(env.paths)
    _play(env, [0.01] * 10)  # live +0.01: exactly half of paper
    _round(env, NOW + 60 + 2 * HOUR)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["sequence"]["block"] == 2 and t["requested_at"] != first["requested_at"]
    assert (t["id"], t["buy_sol"], t["fills"], t["minutes"]) == (BEST, 0.2, 10, 120)
    assert t["floor"] == first["floor"] == 10.9  # 1 SOL below the first block's start, not below 11.9 + 0.1
    assert env.sent == []  # never a line per block: the start, a drop, a hold and the day's digest are what is said
    notes = env.paths.notes.read_text()
    assert "block 1" in notes and "+0.0100" in notes and "+0.0200" in notes  # the record has the judgement
    assert len(env.svc.created) == 2 and len(env.started) == 2
    history = [json.loads(x) for x in LT.history_path(env.paths).read_text().splitlines()]
    assert [(h.get("sequence") or {}).get("block") for h in history] == [1]


def test_blocks_never_run_out_while_live_holds_up(env):
    """No limit on blocks or hours: eight blocks at paper's +0.02, each judged on every live trade so far and each
    followed by the next, all at the first block's floor, with no line to the owner for any of them."""
    _start(env)
    at = NOW + 60
    for block in range(1, 9):
        t = LT.load(env.paths)
        assert t["sequence"]["block"] == block and t["floor"] == 10.9
        _play(env, [0.02] * 10)
        at += 2 * HOUR
        _round(env, at)
    seq = _seq(env)
    assert seq["state"] == "running" and len(seq["blocks"]) == 9 and LT.load(env.paths)["sequence"]["block"] == 9
    assert [b.get("decision") for b in seq["blocks"][:8]] == ["continue"] * 8
    assert AT.read_autotest(env.paths)["on"] is True and env.sent == []


def test_each_block_is_judged_on_every_live_trade_of_the_sequence_so_far(env):
    """After block 2 the live mean is that of its 20 trades: 0.03 then 0.0 make 0.015, above half of paper."""
    _start(env)
    _play(env, [0.03] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    _play(env, [0.0] * 10)
    _round(env, NOW + 60 + 4 * HOUR)
    assert LT.load(env.paths)["sequence"]["block"] == 3 and LT.load(env.paths)["state"] == "running"


def test_a_block_that_ends_on_its_time_limit_is_normal_and_the_next_one_starts(env):
    """A block that ends on its time limit is normal: the strategy is judged only once it has 10 live round trips in
    all, so until then its next block starts at once, the same strategy at the same floor; from then on it is judged
    after every block."""
    _start(env)
    _play(env, [0.0] * 4, reason=TIME)  # 4 trades at 0: judged now, it would be dropped
    _round(env, NOW + 60 + 2 * HOUR)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["sequence"]["block"] == 2 and t["floor"] == 10.9
    assert _seq(env)["blocks"][0]["decision"] == "next" and env.sent == []
    _play(env, [0.03] * 6, reason=TIME)  # 10 in all: (4 x 0 + 6 x 0.03) / 10 = +0.018, above half of paper
    _round(env, NOW + 60 + 4 * HOUR)
    assert LT.load(env.paths)["sequence"]["block"] == 3 and _seq(env)["blocks"][1]["decision"] == "continue"
    _play(env, [-0.05] * 2, reason=TIME)  # 12 in all: (0.18 - 0.10) / 12 = +0.0067, below half of paper
    _round(env, NOW + 60 + 6 * HOUR)
    assert _seq(env)["state"] == "dropped" and len(env.svc.created) == 3


def test_live_below_half_of_paper_drops_the_strategy_and_the_automatic_test_stays_on(env):
    _start(env)
    _play(env, [0.0099] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["state"] == "done" and len(env.svc.created) == 1  # no second block
    seq = _seq(env)
    assert seq["state"] == "dropped" and "below" in seq["why"]
    (d,) = seq["dropped"]
    assert d["strategy"] == BEST and d["trades"] == 10 and d["live_mean"] == pytest.approx(0.0099)
    assert d["paper_mean"] == pytest.approx(0.02) and "below" in d["why"] and d["at"] == NOW + 60 + 2 * HOUR
    (line,) = env.sent
    assert line.startswith("Automatic live test dropped") and "half of paper" in line and BEST in line
    assert "+0.0099" in line and "+0.0200" in line and "never starts it again" in line
    assert "analyst works out why" in env.paths.notes.read_text()
    at = AT.read_autotest(env.paths)
    assert at["on"] is True and at["used"] is None  # it never turns itself off
    lines = [json.loads(x) for x in LT.history_path(env.paths).read_text().splitlines()]
    assert lines[-1]["kind"] == "sequence" and lines[-1]["dropped"] is True and lines[-1]["trades"] == 10


def test_a_dropped_strategy_is_never_started_again_and_it_waits_for_another(env):
    """Dropped, BEST still passes on paper: the automatic test never starts it again (not later that day, not after the
    owner's off and on the next day), says nothing more while it waits, and starts the next strategy that passes as soon
    as one does, with a floor of its own."""
    _start(env)
    _play(env, [0.0] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "dropped"
    env.sent.clear()
    for k in range(1, 5):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k)
    assert len(env.svc.created) == 1 and env.sent == [] and AT.read_autotest(env.paths)["on"] is True
    day2 = NOW + 86400
    LTT._send(env, "auto", "off", at=day2)
    LTT._send(env, "auto", "on", at=day2 + 60)
    _round(env, day2 + 120)
    assert len(env.svc.created) == 1
    env.sol = 12.4
    LTT._freeze(env.store, SECOND, pnl=0.01)
    _round(env, day2 + 1020)
    t = LT.load(env.paths)
    assert t["id"] == SECOND and t["state"] == "running" and t["floor"] == 11.5  # 12.5 less 1 SOL: its own floor
    assert [d["strategy"] for d in _seq(env)["dropped"]] == [BEST]  # the record carries the drop with it


TWIN_EXIT = {"stop_loss": 0.3, "max_hold_secs": 300}  # LTT._freeze's exit, with its watch delay left out


def _twin(watch: float) -> str:
    """The id of LTT.HUNT with LTT._freeze's exit and `watch` s of watch delay where a hunt fill lands: one rule, frozen
    before the lab gave launch-type exits OKTech's watch delay (0) or since (1.3), or with another delay."""
    return paper.strategy_id(LTT.HUNT, TWIN_EXIT | ({"watch_secs": watch} if watch else {}), "+3", 1, 0.2)


def test_a_rule_dropped_under_its_old_id_never_comes_back_with_the_watch_delay(env):
    """Review of 7 Nov evening. A hunt rule frozen before the watch delay and the same rule frozen since are two
    strategies (two ids) but one OKTech sniper, which reads no watch delay. A drop is the verdict on that sniper's live
    trades, so a rule the automatic test dropped under its old id is never armed again under its new one."""
    old, new = _twin(0), _twin(1.3)
    LTT._freeze(env.store, old, trigger=dict(LTT.HUNT), pnl=0.03, watch=0)  # still in the paper test, as on the server
    # dropped before this change: its line in tests.jsonl as the code then wrote it, with no word of a twin
    LT._history(env.paths, {"kind": "sequence", "id": "seq-1", "strategy": old, "label": "hunt 2 SOL", "dropped": True,
                            "why": "live below half of paper", "blocks": [], "ended_at": NOW - HOUR})  # fmt: skip
    LTT._freeze(env.store, new, trigger=dict(LTT.HUNT), pnl=0.02)  # passes on paper with OKTech's watch delay
    _on(env)
    _round(env, NOW + 60)
    assert LT.load(env.paths) is None and env.svc.created == []
    LTT._freeze(env.store, SECOND, pnl=0.01)  # another rule
    _round(env, NOW + 960)
    assert LT.load(env.paths)["id"] == SECOND


def test_a_strategy_dropped_with_the_watch_delay_holds_for_every_twin_once_its_record_is_gone(env):
    """The drop keeps the rule it was the verdict on (paper.live_id), so a twin with another watch delay never starts
    either, even after `paper freeze --replace` took the dropped strategy's own record out of the paper test."""
    new = _twin(1.3)
    LTT._freeze(env.store, new, trigger=dict(LTT.HUNT), pnl=0.02)
    _on(env)
    _round(env, NOW + 60)
    assert LT.load(env.paths)["id"] == new
    _play(env, [0.005] * 10)  # below half of +0.02: dropped
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "dropped" and AT.dropped(env.paths)[-1]["live_id"] == _twin(0)
    env.store.set_coverage("paper_strategies", {"strategies": [], "superseded": []})
    longer = _twin(2.0)
    LTT._freeze(env.store, longer, trigger=dict(LTT.HUNT), pnl=0.03, watch=2.0)  # the same sniper live
    LTT._freeze(env.store, SECOND, pnl=0.01)
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert LT.load(env.paths)["id"] == SECOND


def test_a_rule_frozen_again_with_the_watch_delay_carries_its_old_ids_live_record(env):
    """A run that ends without a drop leaves its live record to the strategy's next run (_carried). Under its new id the
    rule is the same sniper, so it starts with that record, never afresh: 6 trades at -0.1 SOL under the old id leave it
    0.4 SOL of its 1 SOL, its floor that far below the wallet."""
    old, new = _twin(0), _twin(1.3)
    live = {"n": 6, "sum": -0.6, "n_all": 6, "sum_all": -0.6, "unmeasured": 0, "failed": 0, "failed_sol": 0.0,
            "copies": 0}  # fmt: skip
    LT._history(env.paths, {"kind": "sequence", "id": "seq-1", "strategy": old, "label": "hunt 2 SOL", "dropped": False,
                            "why": "you turned the automatic test off", "ended_at": NOW - HOUR,
                            "blocks": [{"n": 1, "requested_at": NOW - 3 * HOUR, "floor": 10.9, "live": live}]})  # fmt: skip
    LTT._freeze(env.store, new, trigger=dict(LTT.HUNT), pnl=0.02)
    _on(env)
    _round(env, NOW + 60)
    seq, t = _seq(env), LT.load(env.paths)
    assert seq["strategy"] == new and t["state"] == "running"
    assert seq["carried"]["n"] == 6 and AT._tally(seq)["sum"] == pytest.approx(-0.6)
    assert t["floor"] == 11.5  # 11.9 SOL less the 0.4 left of its 1 SOL


def test_the_next_strategy_after_a_drop_starts_at_once_with_its_own_floor(env):
    LTT._freeze(env.store, SECOND, pnl=0.01)
    _start(env)  # BEST, at +0.02 the better of the two
    assert LT.load(env.paths)["id"] == BEST
    _play(env, [0.005] * 10)  # below half of +0.02: dropped
    _round(env, NOW + 60 + 2 * HOUR)
    t = LT.load(env.paths)
    assert t["id"] == SECOND and t["state"] == "running" and t["sequence"]["block"] == 1
    assert t["floor"] == 10.95  # 11.95 SOL now, less 1 SOL: SECOND's own floor, not BEST's 10.90
    seq = _seq(env)
    assert seq["strategy"] == SECOND and seq["floor"] == 10.95 and [d["strategy"] for d in seq["dropped"]] == [BEST]
    assert len(_said(env, "Automatic live test dropped")) == 1 and len(_said(env, "Automatic live test, on")) == 1
    assert len(env.sent) == 2


EMERGENCY = ("EMERGENCY: equity below floor 10.9 (balance 10.4); stopped re-arming; the wallet holds nothing, so the "
             "snipers it armed are stopped")  # fmt: skip


def test_a_block_that_ends_on_its_floor_is_judged_on_the_strategys_own_trades(env):
    """The cycler stops re-arming once the wallet is under the block's floor, and the wallet is the owner's too: taken
    under it by a 1 SOL withdrawal mid-block, the strategy is 0.4 SOL down by the fills, not at its floor, and goes on,
    its next floor 1 SOL lower with the wallet (review of 7 Nov: the cycler's floor end dropped it for good). At its floor
    by its own trades, 1 SOL down by the fills, it is dropped."""
    _guard_off(env)
    _start(env)
    _play(env, [-0.1] * 4, reason=EMERGENCY)
    env.sol -= 1.0  # the owner took 1 SOL out while the block ran
    _round(env, NOW + 60 + 2 * HOUR)
    t, seq = LT.load(env.paths), _seq(env)
    assert seq["state"] == "running" and t["sequence"]["block"] == 2 and t["state"] == "running"
    assert t["floor"] == 9.9 and seq["blocks"][0]["outcome"]["kind"] == "floor"  # 10.5 SOL, 0.6 left of its 1 SOL
    assert env.sent == []
    _play(env, [-0.2] * 3, reason=EMERGENCY)
    _round(env, NOW + 60 + 4 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "dropped" and "1.00 SOL down live" in seq["why"] and "floor" in seq["why"]
    (line,) = _said(env, "Automatic live test dropped")
    assert "7 live trades" in line and len(env.svc.created) == 2


def test_a_strategy_one_sol_down_live_is_dropped_before_its_tenth_trade_whatever_was_deposited(env):
    """The 1 SOL a strategy may lose is read from the fills after every block, whatever its number of trades (review of
    7 Nov: it was read only once the strategy had 10). A block that ends on its time limit with 6 trades at -0.2 SOL is
    1.2 SOL down live; the owner topped the wallet up by 3 SOL meanwhile (they keep it at 5-8 SOL), so the wallet is far
    above any floor. Dropped there, with no block 2."""
    _guard_off(env)
    _start(env)
    _play(env, [-0.2] * 6, reason=TIME)
    env.sol += 3.0
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "dropped" and "1.20 SOL down live" in seq["why"] and "floor" in seq["why"]
    assert len(env.svc.created) == 1
    (line,) = _said(env, "Automatic live test dropped")
    assert "6 live trades" in line and "-0.2000" in line


def test_buys_the_fills_call_copies_count_toward_the_strategys_loss(env):
    """Every buy of the test lands right behind a launch-sniping leader the book copies, so the fills name a copied
    leader on each (block_live): none counts toward the 10 trades the half-of-paper judgement needs (they may be the
    book's), and the loss limit reads the lower of the two readings' sums, so there they count. A time-limited block of
    5 such buys at -0.2 SOL is 1 SOL down live: dropped, though the owner keeps the wallet topped up."""
    _guard_off(env)
    _start(env)
    _play(env, [], copies=[-0.2] * 5, reason=TIME)
    env.sol += 1.0
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "dropped" and "1.00 SOL down live" in seq["why"] and len(env.svc.created) == 1


def test_failed_sends_alone_count_toward_the_strategys_loss(env):
    """OKTech sends buys that fail on chain anyway (5 Nov: about 0.001 SOL each, and each spends the shot). Blocks that
    land no buy at all still pay them, and they count toward the strategy's 1 SOL: two blocks of 500 failed sends and no
    fill make 1 SOL, and the strategy is dropped (review of 7 Nov: with no buy they counted as nothing). The owner keeps
    the wallet topped up, so its balance says nothing."""
    _guard_off(env)
    _start(env)
    for block in (1, 2):
        t = LT.load(env.paths)
        assert t["state"] == "running" and t["sequence"]["block"] == block
        LTT._armed(env)
        start = int(t["started_at"])
        for k in range(500):
            env.store.put_fill({"sig": f"failed-{block}-{k}", "ts": start + 60 + k, "wallet": W, "side": "failed",
                                "sol": 0.0, "tokens": 0.0, "mint": f"Launch{block}x{k}", "priority_lamports": 995_000,
                                "tip_lamports": 0})  # fmt: skip
        rep = {"start_sol": env.sol, "end_sol": env.sol - 0.5, "reason": TIME, "fills": 0, "restarts": 120, "held": [],
               "left_running": []}  # fmt: skip
        _cycler_ended(env, t, rep, start + 700)
        _round(env, NOW + 60 + 2 * HOUR * block)
    seq = _seq(env)
    assert seq["state"] == "dropped" and "1.00 SOL down live" in seq["why"] and len(env.svc.created) == 2


def test_a_strategy_one_sol_down_live_is_dropped_whatever_was_deposited(env):
    """Its floor is 1 SOL below its first block's start; read from the fills, a deposit that keeps the wallet far above
    that floor never hides a strategy that is 1 SOL down."""
    _start(env, fills=5)
    _play(env, [-0.21] * 5)  # -1.05 SOL in 5 trades
    env.sol += 3.0  # the owner tops the wallet up
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "dropped" and "1.05 SOL down live" in seq["why"] and "floor" in seq["why"]


def test_a_withdrawal_never_drops_a_strategy_and_its_floor_moves_with_the_wallet(env):
    """A strategy may lose 1 SOL by its own trades: each block's floor is the wallet it starts from less what is left of
    that 1 SOL by the fills. The owner takes 1.2 SOL out between blocks (6 Nov 15:16 they took 2.54 SOL out mid-test, 7
    Nov 11:30 and 11:45 more): that says nothing about the strategy, which goes on with its floor 1.2 SOL lower (review
    of 7 Nov: it was dropped for good)."""
    _start(env)
    _play(env, [0.03] * 10)  # +0.3 SOL: the wallet 12.2
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["floor"] == 10.9  # 12.2 less the 1.3 SOL left: as the first block's
    _play(env, [0.01] * 10)  # +0.1: 12.3
    env.sol -= 1.2  # 11.1 SOL left: under the old 10.90 floor plus a buy and its fees
    _round(env, NOW + 60 + 4 * HOUR)
    t, seq = LT.load(env.paths), _seq(env)
    assert seq["state"] == "running" and t["sequence"]["block"] == 3 and t["state"] == "running"
    assert t["floor"] == 9.7 and seq["floor"] == 9.7  # 11.1 SOL less the 1.4 SOL left of its 1 SOL
    assert not _said(env, "Automatic live test dropped") and AT.read_autotest(env.paths)["on"] is True


def test_a_block_whose_cycler_would_refuse_its_floor_holds_and_never_loops(env):
    """The cycler reads the wallet's SOL without its wrapped SOL and refuses to start at or below its floor. With 1.5 SOL
    of the wallet wrapped, the next block's floor (10.90, from the wallet's 12.1 SOL in all) is above the 10.6 SOL the
    cycler would read: no block is asked for (each would make a sniper that never runs), the wallet holds it like one
    with no room, said once a day, and the block starts once the SOL is unwrapped."""
    _start(env)
    _play(env, [0.02] * 10)  # 12.0 SOL and 0.1 wrapped
    env.sol -= 1.4  # OKTech wrapped 1.4 SOL more
    _round(env, NOW + 60 + 2 * HOUR, wsol=1.5)
    _round(env, NOW + 60 + 2 * HOUR + 900, wsol=1.5)
    seq = _seq(env)
    assert len(env.svc.created) == 1 and seq["state"] == "running" and seq["hold"]["kind"] == "room"
    (held,) = _said(env, "Automatic live test holds")
    assert "wrapped" in held and "10.90" in held
    env.sol += 1.4
    _round(env, NOW + 60 + 2 * HOUR + 1800)
    t = LT.load(env.paths)
    assert t["sequence"]["block"] == 2 and t["state"] == "running" and t["floor"] == 10.9


def test_the_books_loss_never_drops_a_strategy_that_never_traded(env):
    """The first block's request is written; the book buys a coin before the round starts it, so it does not start (a
    quiet wait), and that coin then sells 0.9 SOL down. The wallet is 11.0 SOL, under the first request's 10.90 floor
    plus a buy and its fees: the strategy, which never traded, is not at its floor (review of 7 Nov: it was dropped for
    good); its first block starts with its floor 1 SOL below the wallet it starts from."""
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env)
    env.snap = Snapshot(ts=NOW + 60, wallet=W, native_sol=11.8, wsol=0.1, held=[], snipers=[])
    LTT._tape(env.store, NOW + 55, k=99_999)
    env.store.set_coverage("measure:last_ts", {"ts": NOW})
    asyncio.run(AT.step(env.svc, env.cfg, env.paths, NOW + 60, snap=env.snap, notes=env.notes, notifier=env.notifier))
    assert LT.load(env.paths)["state"] == "requested"
    book = "BookCoin" + "B" * 36
    env.svc.held = [{"mint": book, "amount": 5.0, "raw": 5_000_000, "decimals": 6, "value_sol": 0.9, "priced": True}]
    env.sol = 10.0  # its SOL went into the book's coin
    _round(env, NOW + 120)
    assert not LT.load(env.paths)["started"] and _seq(env)["state"] == "running"
    env.svc.held, env.sol = [], 10.9  # the book's coin sold 0.9 SOL down
    _round(env, NOW + 1020)
    t, seq = LT.load(env.paths), _seq(env)
    assert seq["state"] == "running" and t["state"] == "running" and t["sequence"]["block"] == 1
    assert t["floor"] == 10.0 and not _said(env, "Automatic live test dropped")


def test_a_coin_held_between_blocks_counts_toward_the_floor(env):
    """The floor is the wallet's SOL and coins, as the cycler reads it: with the book's coin bought for 1 SOL between
    blocks, the SOL alone is 1 SOL lower and the wallet is not; the strategy is not dropped, the next block waits
    quietly for the coin to be sold, and then starts at the floor it had."""
    _start(env)
    _play(env, [0.02] * 10)  # 12.1 SOL; the floor 10.90, a block needs 11.15
    LTT._armed(env, status="finished")  # the block's hunt sniper is spent
    env.sol -= 1.0
    book = "BookCoin" + "B" * 36
    env.svc.held = [{"mint": book, "amount": 5.0, "raw": 5_000_000, "decimals": 6, "value_sol": 1.0, "priced": True}]
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "running" and seq["blocks"][0]["decision"] == "continue" and seq["waiting"]
    assert not seq.get("hold") and env.sent == [] and len(env.svc.created) == 1  # a quiet wait: no fault, no drop
    env.sol, env.svc.held = env.sol + 1.0, []  # the book's sniper sold it
    _round(env, NOW + 60 + 2 * HOUR + 900)
    t = LT.load(env.paths)
    assert t["sequence"]["block"] == 2 and t["state"] == "running" and t["floor"] == 10.9


def test_the_live_result_is_read_from_the_measure_stages_fills_never_the_balance(env):
    """Deposit-proof: the cycler's start and end balance moved +5 SOL (the owner topped the wallet up mid-block) while
    every trade lost; only our own round trips count."""
    _start(env)
    _play(env, [-0.01] * 10, report={"start_sol": 11.8, "end_sol": 16.7})
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "dropped" and len(env.svc.created) == 1
    assert "-0.0100" in _said(env, "Automatic live test dropped")[0]


def test_the_books_copies_from_the_same_wallet_are_not_the_tests_trades(env):
    """The book trades from the same wallet and its buys count toward the cycler's cap; its copies (a copied leader in
    the fills) are left out of the test's live result, and out of the 10 trades of its own it is judged on: block 1's
    seven and block 2's three."""
    _start(env)
    _play(env, [0.002] * 7, copies=[0.3] * 3)
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "running" and LT.load(env.paths)["sequence"]["block"] == 2
    _play(env, [0.002] * 3, reason=TIME)
    _round(env, NOW + 60 + 4 * HOUR)
    assert _seq(env)["state"] == "dropped"
    (line,) = _said(env, "Automatic live test dropped")
    assert "10 live trades" in line and "+0.0020" in line


def test_a_hunt_buy_the_fills_name_a_copy_still_counts_against_the_test(env):
    """The measure stage names a copied leader on any buy of ours in a coin a wallet the book copies bought first, with
    no time bound and no word of which sniper bought: a book sniper never deleted copies a launch sniper L, L buys the
    launch a slot before the test's hunt buy, and that buy rugs. Left out as the book's, the strategy reads +0.012 a
    trade (above half of paper); it really made -0.0073. Once it has 10 trades the fills do not call copies, it is
    judged on the worse of the two readings: those, and every buy of the wallet in its blocks."""
    _start(env)
    _play(env, [0.012] * 9, copies=[-0.2])
    _round(env, NOW + 60 + 2 * HOUR)  # nine of its own: not judged yet
    assert _seq(env)["state"] == "running" and LT.load(env.paths)["sequence"]["block"] == 2
    _play(env, [0.012], reason=TIME)
    _round(env, NOW + 60 + 4 * HOUR)
    assert len(env.svc.created) == 2 and _seq(env)["state"] == "dropped" and "below" in _seq(env)["why"]
    (line,) = _said(env, "Automatic live test dropped")
    assert "-0.0073" in line and "11 live trades" in line and "copies" in line


def test_a_block_full_of_the_books_buys_is_not_judged_on_two_of_the_strategys_own(env):
    """The book trades from the same wallet and the cycler's cap counts its buys (review of 7 Nov): block 1 reaches its
    10 fills with two buys of the test's own and eight copies of the book's. The strategy is judged once it has 10 live
    round trips of its own, whatever ended the block, never on two: its next block starts, whether the book's copies won
    (the test's own two then read +0.001, under half of paper) or lost (every buy of the wallet then reads -0.002)."""
    _start(env)
    _play(env, [-0.004, 0.006], copies=[0.01] * 8)
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "running" and LT.load(env.paths)["sequence"]["block"] == 2
    _play(env, [0.03, 0.03], copies=[-0.01] * 8)
    _round(env, NOW + 60 + 4 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "running" and LT.load(env.paths)["sequence"]["block"] == 3
    assert [b["decision"] for b in seq["blocks"][:2]] == ["next", "next"] and env.sent == []


def test_a_book_coin_held_when_the_test_closes_does_not_end_a_complete_block(env):
    """Block 1 made its 10 buys and sold each; its hunt sniper is spent. When the round closes the test, the book holds
    a coin of its own copy, bought after the block. The block is complete, nothing holds the automatic test as a fault
    (the book's sniper sells its own coin), and the next block waits quietly for the wallet to hold nothing."""
    _start(env)
    _play(env, [0.02] * 10)
    LTT._armed(env, status="finished")
    book = "BookCoin" + "B" * 36
    env.svc.held = [{"mint": book, "amount": 5.0, "raw": 5_000_000, "decimals": 6, "value_sol": 0.19, "priced": True}]
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert (
        seq["state"] == "running" and seq["blocks"][0]["decision"] == "continue" and AT.read_autotest(env.paths)["on"]
    )
    assert env.sent == [] and not seq.get("hold")
    (closed,) = [x for x in env.paths.notes.read_text().splitlines() if "Live test ended" in x]
    assert "nothing sells" not in closed and "did not buy" in closed
    env.svc.held = []  # the book's sniper sold its coin
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert LT.load(env.paths)["sequence"]["block"] == 2 and LT.load(env.paths)["state"] == "running"


def test_a_buy_whose_sales_cover_only_part_of_it_waits_for_the_rest(env):
    """With a scale-out exit (sell 35-50 % at +10-20 %, trail the rest) a buy is closed by two sales. The measure stage
    may have written the first only when the round closes the test: judging then counts half a trade. A buy counts as
    measured once its sales cover its tokens; until then the decision waits."""
    _start(env)
    t = LT.load(env.paths)
    LTT._armed(env)
    start = int(t["started_at"])
    for k in range(10):
        sig, ts, mint = f"buy-{start}-{k}", start + 60 + 30 * k, f"Coin{start}x{k}"
        _ours(env, sig, ts, mint, True, 0.202)
        env.store.put_fill({"sig": sig, "ts": ts, "wallet": W, "mint": mint, "side": "buy", "sol": 0.202,
                            "tokens": 1_000_000.0})  # fmt: skip
        if k < 9:  # nine at +0.005, measured
            _ours(env, f"sell-{sig}", ts + 20, mint, False, 0.207)
            env.store.put_fill({"sig": f"sell-{sig}", "ts": ts + 20, "wallet": W, "mint": mint, "side": "sell",
                                "sol": 0.207, "tokens": 1_000_000.0, "pnl_sol": 0.005, "buy_sig": sig})  # fmt: skip
        else:  # the last: its take-profit half measured (+0.06), its stop-loss half sold but not measured yet
            _ours(env, f"tp-{sig}", ts + 5, mint, False, 0.161, tokens=500_000)
            env.store.put_fill({"sig": f"tp-{sig}", "ts": ts + 5, "wallet": W, "mint": mint, "side": "sell", "sol": 0.161,
                                "tokens": 500_000.0, "pnl_sol": 0.06, "buy_sig": sig})  # fmt: skip
            _ours(env, f"sl-{sig}", ts + 25, mint, False, 0.051, tokens=500_000)
    _cycler_ended(env, t, {"start_sol": 11.8, "end_sol": 11.8, "reason": CAP.format(n=10), "fills": 10, "restarts": 20,
                           "held": [], "left_running": []}, start + 400)  # fmt: skip
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "running" and "decision" not in seq["blocks"][0] and len(env.svc.created) == 1
    sig = f"buy-{start}-9"  # the measure stage writes the stop-loss half: -0.05
    env.store.put_fill({"sig": f"sl-{sig}", "ts": start + 60 + 270 + 25, "wallet": W, "mint": f"Coin{start}x9",
                        "side": "sell", "sol": 0.051, "tokens": 500_000.0, "pnl_sol": -0.05, "buy_sig": sig})  # fmt: skip
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert _seq(env)["state"] == "dropped" and len(env.svc.created) == 1  # +0.0055 a trade: under half of +0.02
    assert "+0.0055" in _said(env, "Automatic live test dropped")[0]


def test_failed_buy_sends_count_against_the_live_mean(env):
    """OKTech sends buys that fail on chain anyway (5 Nov: about 10 per 3 fills, about 0.001 SOL each), and the measure
    stage records them with their fees. Paper does not model them: they are what the live comparison is for. Ten round
    trips at +0.011 and 40 failed buy sends at 0.001 SOL make +0.007 a trade, under half of paper's +0.02."""
    _start(env)
    start = int(LT.load(env.paths)["started_at"])
    _play(env, [0.011] * 10)
    for k in range(40):
        env.store.put_fill({"sig": f"failed-{k}", "ts": start + 100 + k, "wallet": W, "side": "failed", "sol": 0.0,
                            "tokens": 0.0, "mint": f"Launch{k}", "priority_lamports": 995_000, "tip_lamports": 0,
                            "exit_reason": "{'InstructionError': [3, {'Custom': 6010}]}"})  # fmt: skip
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "dropped" and len(env.svc.created) == 1
    (line,) = _said(env, "Automatic live test dropped")
    assert "+0.0070" in line and "40 failed" in line


def test_a_failed_sell_twin_is_charged_once(env):
    """A losing variant of a landed sale is already in that sale's result (twin_fee_sol); it is not charged again."""
    from snipetech.solana.txmeta import PumpIx, TxMeta

    _start(env)
    start = int(LT.load(env.paths)["started_at"])
    trades = _play(env, [0.01] * 10)
    _sig, ts, mint, _pnl, _leader = trades[0]
    env.store.put_fill({"sig": "twin-1", "ts": ts + 21, "slot": None, "wallet": W, "side": "failed", "sol": 0.0,
                        "tokens": 0.0, "mint": mint, "priority_lamports": 5_000_000, "tip_lamports": 0})  # fmt: skip
    meta = TxMeta(sig="twin-1", slot=0, block_time=ts + 21, version=0, fee_payer=W, signers=[W], n_signatures=1, fee=5000,
                  priority_lamports=5_000_000, cu_limit=None, cu_price=None, cu_consumed=None,
                  pump_ixs=[PumpIx("pump", "sell", W, {}, None, mint)])  # fmt: skip
    env.store.add_tx_meta([meta])
    assert start  # the block's window holds the twin
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["sequence"]["block"] == 2  # +0.01 a trade: exactly half of paper, the twin not charged


def test_the_failed_duplicates_of_a_sale_with_no_buy_on_record_are_charged_to_its_block(env):
    """Review of 8 Nov: the tape missed our buy (a recorder stall), so the measure stage could not pair the sale and it
    has no result; its two failed duplicate sends were still paid for. They were skipped as twins of a sale whose
    result holds them, and so counted nowhere: the block now pays them, once (a paired sale's twin is still in its
    sale's result only: test_a_failed_sell_twin_is_charged_once)."""
    from snipetech.solana.txmeta import PumpIx, TxMeta

    start = NOW - HOUR
    t = {"started_at": start, "ended_at": NOW, "report": {"held": [], "job_ended": NOW - 60, "reason": TIME}}
    rows = [{"sig": "lost-sell", "ts": start + 120, "slot": 7_000, "wallet": W, "mint": "Lost", "side": "sell",
             "sol": 0.19, "tokens": 1000.0, "pnl_sol": None, "twin_fee_sol": 0.00401}]  # fmt: skip
    for k in range(2):
        rows.append({"sig": f"dup-{k}", "ts": start + 120 + k, "slot": 7_000 + k, "wallet": W, "side": "failed",
                     "sol": 0.0, "tokens": 0.0, "mint": "Lost", "priority_lamports": 2_000_000, "tip_lamports": 0})  # fmt: skip
    for r in rows:
        env.store.put_fill(r)
    env.store.add_tx_meta([TxMeta(sig=f"dup-{k}", slot=7_000 + k, block_time=start + 120 + k, version=0, fee_payer=W,
                                  signers=[W], n_signatures=1, fee=5000, priority_lamports=2_000_000, cu_limit=None,
                                  cu_price=None, cu_consumed=None, pump_ixs=[PumpIx("pump", "sell", W, {}, None, "Lost")])
                           for k in range(2)])  # fmt: skip
    live = AT.block_live(env.store, W, t, NOW, per_tx_sol=0.0035, failed_fee_sol=0.001)
    assert sorted(f["sig"] for f in live["failed"]) == ["dup-0", "dup-1"]
    assert sum(f["fee"] for f in live["failed"]) == pytest.approx(0.00401)
    assert AT._reading(live, own=False, final=True)["sum"] == pytest.approx(-0.00401)


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
    the block closed, and a strategy never goes on on numbers it could not see."""
    _start(env)
    _play(env, [0.02] * 10, measured=False)
    for k in range(5):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k)
    assert _seq(env)["state"] == "dropped" and len(env.svc.created) == 1
    (line,) = _said(env, "Automatic live test dropped")
    assert "10 not measured" in line


def test_a_buy_counted_at_its_whole_cost_counts_its_sale_once_the_measure_stage_reads_it(env):
    """A buy whose coin left the wallet with no sale read counts its whole cost 45 minutes after its block (the wallet
    held nothing of value then). When the measure stage writes its sale later, every later judgement counts the sale
    instead (review of 7 Nov: a block counted was never read again). Block 1: nine trades at +0.04 and a tenth sold at
    +0.04 whose sale is read only during block 2; block 2: ten at 0. On the sale, +0.02 a trade: it goes on."""
    _start(env)
    t, _trades, last = _trade(env, [0.04] * 9)
    sig, mint = "buy-read-late", "Late" + "L" * 40
    _ours(env, sig, last + 10, mint, True, 0.202)
    _ours(env, f"sell-{sig}", last + 30, mint, False, 0.242)
    env.store.put_fill({"sig": sig, "ts": last + 10, "wallet": W, "mint": mint, "side": "buy", "sol": 0.202,
                        "tokens": 1_000_000.0})  # fmt: skip
    env.sol += 0.04
    _cycler_ended(env, t, {"start_sol": 11.8, "end_sol": 12.2, "reason": CAP.format(n=10), "fills": 10, "restarts": 20,
                           "held": [], "left_running": []}, last + 60)  # fmt: skip
    for k in range(4):  # 45 minutes and its sale is not read: its whole cost counts, +0.0155 a trade, above half
        _round(env, NOW + 60 + 2 * HOUR + 900 * k)
    seq = _seq(env)
    assert seq["blocks"][0]["live"]["unmeasured"] == 1 and seq["blocks"][0]["decision"] == "continue"
    _play(env, [0.0] * 10)
    env.store.put_fill({"sig": f"sell-{sig}", "ts": last + 30, "wallet": W, "mint": mint, "side": "sell", "sol": 0.242,
                        "tokens": 1_000_000.0, "pnl_sol": 0.04, "buy_sig": sig})  # fmt: skip
    _round(env, NOW + 60 + 4 * HOUR + 2700)
    seq = _seq(env)
    assert seq["state"] == "running" and seq["blocks"][1]["decision"] == "continue"  # (0.4 + 0) / 20 = +0.02
    assert seq["blocks"][0]["live"]["sum"] == pytest.approx(0.4) and seq["blocks"][0]["live"]["unmeasured"] == 0


def test_a_strategys_live_record_goes_with_it_when_its_run_ends_and_it_starts_again(env):
    """Block 1 ends on its time limit with 6 trades at -0.1 (-0.6 SOL live: not judged, under 10). For one round the
    strategy is not in the paper test (a re-freeze, its lower bound flickering): its run ends, not dropped. When it
    passes again it starts with the same live record (review of 7 Nov: it started afresh, its loss forgotten): 0.4 SOL
    left of its 1 SOL, its floor that far below the wallet, and judged on every live trade it made."""
    _start(env)
    _play(env, [-0.1] * 6, reason=TIME)
    frozen = env.store.coverage("paper_strategies")
    env.store.set_coverage("paper_strategies", {"strategies": [], "superseded": []})
    _round(env, NOW + 60 + 2 * HOUR)
    assert _seq(env)["state"] == "ended" and not AT.dropped(env.paths)
    env.store.set_coverage("paper_strategies", frozen)
    _round(env, NOW + 60 + 2 * HOUR + 900)
    seq, t = _seq(env), LT.load(env.paths)
    assert seq["state"] == "running" and seq["strategy"] == BEST and t["state"] == "running"
    assert AT._tally(seq)["n"] == 6 and AT._tally(seq)["sum"] == pytest.approx(-0.6)
    assert t["floor"] == 10.9 and seq["carried"]["blocks"] == 1  # 11.3 SOL, 0.4 left of its 1 SOL
    assert "6 live round trips" in AT.status_line(env.paths, NOW + 60 + 2 * HOUR + 960)
    _play(env, [0.0] * 3, reason=TIME)
    _round(env, NOW + 60 + 4 * HOUR + 900)
    seq = _seq(env)
    assert seq["state"] == "running" and AT._own_trades(seq) == 9
    _play(env, [-0.02], reason=TIME)
    _round(env, NOW + 60 + 6 * HOUR + 900)
    seq = _seq(env)
    assert seq["state"] == "dropped" and "below" in seq["why"]  # 10 trades in all at -0.062: judged on every one
    (line,) = _said(env, "Automatic live test dropped")
    assert "10 live trades" in line and "-0.0620" in line


def test_a_refusal_that_passes_by_itself_makes_the_next_block_wait(env):
    _start(env)
    _play(env, [0.02] * 10)
    _round(env, NOW + 60 + 2 * HOUR, tape=False)  # the recorder is behind: the next block waits
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "running"
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert LT.load(env.paths)["sequence"]["block"] == 2


def test_a_refusal_that_passes_by_itself_waits_as_long_as_it_takes(env):
    """The recorder two hours behind: the next block waits the whole time (it never ends the strategy's run, and the
    recorder is not the automatic test's to report), then starts once the tape is current."""
    _start(env)
    _play(env, [0.02] * 10)
    for k in range(8):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k, tape=False)
    assert _seq(env)["state"] == "running" and len(env.svc.created) == 1 and env.sent == []
    assert "tape" in _seq(env)["waiting"]["why"]
    _round(env, NOW + 60 + 2 * HOUR + 900 * 8)
    assert LT.load(env.paths)["sequence"]["block"] == 2


def test_the_owners_guard_holds_new_blocks_while_it_is_on_and_tripped(env):
    """The guard is the owner's (on until they switch it off): while it holds, no block starts. That is no fault of the
    strategy: no drop, no line of the automatic test's own (the guard tells the owner itself), and the same strategy
    goes on once the owner switches the stop off."""
    _start(env)
    _play(env, [0.02] * 10)
    LTT._lost(env.store, -1.5, ts=NOW + 60 + HOUR)  # a day 1.3 SOL down
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert len(env.svc.created) == 1 and seq["state"] == "running" and "wallet-wide guard" in seq["waiting"]["why"]
    assert not _said(env, "Automatic live test")
    set_risk(env.paths, {"day_stop_sol": "off"}, "telegram", NOW + 60 + 2 * HOUR + 300)  # the owner's /risk stop off
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert LT.load(env.paths)["sequence"]["block"] == 2 and LT.load(env.paths)["state"] == "running"


def test_one_strategy_at_a_time(env):
    """A better strategy that passes while a block runs does not start beside it."""
    _start(env)
    LTT._freeze(env.store, SECOND, pnl=0.03)
    _round(env, NOW + 60 + 900)
    assert len(env.svc.created) == 1 and LT.load(env.paths)["id"] == BEST and _seq(env)["strategy"] == BEST


# -- what holds it: execution faults, never a drop -----------------------------------------------------------------------


def test_oktech_failing_to_create_a_block_holds_and_the_same_strategy_goes_on(env):
    """OKTech erroring is an execution fault: the strategy is not dropped, the owner hears once that it holds and once
    that it goes on, and each round tries again (never a line per try)."""
    _start(env)
    _play(env, [0.02] * 10)
    env.svc.answer = RuntimeError("OKTech answered 502")  # block 2's sniper is not created
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["state"] == "done" and not LT.load(env.paths)["started"]
    for k in range(1, 4):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k)
    (held,) = _said(env, "Automatic live test holds")
    assert "502" in held and "not dropped" in held and BEST in held
    seq = _seq(env)
    assert seq["state"] == "running" and AT.read_autotest(env.paths)["on"] and len(env.svc.created) == 5
    assert not _said(env, "Live test did not start")  # the per-try lines are only notes
    env.svc.answer = None
    _round(env, NOW + 60 + 2 * HOUR + 900 * 4)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["sequence"]["block"] == 2 and t["floor"] == 10.9
    _round(env, NOW + 60 + 2 * HOUR + 900 * 5)
    (cleared,) = _said(env, "Automatic live test goes on")
    assert BEST in cleared and len(_said(env, "Automatic live test holds")) == 1 and len(env.sent) == 2


def test_a_first_block_that_did_not_start_takes_its_floor_from_the_wallet_it_starts_from(env):
    """OKTech refuses the first block's sniper (a fault: it holds), and the owner tops the wallet up by 3 SOL before it
    starts. The block that starts has its floor 1 SOL below the wallet it starts from, not below the wallet of the first
    request (review of 7 Nov: the deposit was 3 SOL more the strategy could lose before its cycler stopped it)."""
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env)
    env.svc.answer = RuntimeError("OKTech answered 502")
    _round(env, NOW + 60)
    assert not LT.load(env.paths)["started"] and _seq(env)["floor"] == 10.9
    env.sol += 3.0
    env.svc.answer = None
    _round(env, NOW + 960)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["sequence"]["block"] == 1 and t["floor"] == 13.9
    assert _seq(env)["floor"] == 13.9 and _seq(env)["state"] == "running"


def test_the_cycler_dying_never_drops_the_strategy(env):
    """A reboot (7 Nov: the resize) or a kill takes the cycler down mid-block with no report: an execution fault, never a
    trading result. The block's trades still count, the owner hears it once, no block starts in the round that finds it
    (review of 7 Nov: the next one started at once), one is tried at the next round, and once one runs to its end the
    owner hears that once."""
    _start(env)
    t, _trades, last = _trade(env, [0.02] * 3)
    _cycler_ended(env, t, None, last + 30, code=137, log="Killed\n")
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "running" and seq["blocks"][0]["outcome"]["kind"] == "died"
    (held,) = env.sent
    assert "cycler" in held and "died" in held and "not dropped" in held and "next round" in held
    assert len(env.svc.created) == 1 and LT.load(env.paths)["state"] == "done"
    _round(env, NOW + 60 + 2 * HOUR + 900)  # the one try
    t2 = LT.load(env.paths)
    assert t2["sequence"]["block"] == 2 and t2["state"] == "running" and t2["floor"] == 10.9
    _play(env, [0.02] * 7, reason=TIME)  # it runs to its end: 10 trades in all, judged and going on
    _round(env, NOW + 60 + 4 * HOUR + 900)
    (cleared,) = _said(env, "Automatic live test goes on")
    assert "cycler" in cleared and LT.load(env.paths)["sequence"]["block"] == 3 and len(env.sent) == 2


def test_a_cycler_that_dies_again_holds_new_blocks_for_hours_and_a_deploy_tries_at_once(env, monkeypatch):
    """A crash loop after a bad deploy: every cycler dies minutes after it armed its sniper, which stays armed with
    nobody watching (no floor, no /stop, no guard) until the next round closes the block. One block is tried at the
    next round; after a second death in a row none starts for 4 hours, then 8 after a third, and so on (a day at most),
    unless a deploy changes the code (its fix may be in), which tries at once. The owner hears it once (review of 7 Nov:
    every round made a new sniper and a new cycler)."""
    _start(env)

    def dies(at):  # the block running now: its cycler dies ten minutes before the round at `at`
        t = LT.load(env.paths)
        assert t["state"] == "running", t
        LTT._armed(env)
        _cycler_ended(env, t, None, at - 600, code=1, log="Traceback (most recent call last):\nNameError: x\n")
        _round(env, at)

    at = NOW + 60 + 900
    dies(at)
    at += 900
    _round(env, at)  # the one try: block 2
    assert len(env.started) == 2
    at += 900
    dies(at)  # block 2's cycler dies too
    second = at
    for _k in range(14):  # three and a half hours: no block starts
        at += 900
        _round(env, at)
    assert len(env.started) == 2 and len(env.svc.created) == 2 and _seq(env)["state"] == "running"
    (held,) = _said(env, "Automatic live test holds")
    assert "cycler" in held and "4 hours" in held and "deploy" in held and len(env.sent) == 1
    assert "2 times in a row" in _seq(env)["hold"]["why"] and "tried at" in _seq(env)["waiting"]["why"]
    _round(env, second + 4 * HOUR + 900)
    assert len(env.started) == 3  # 4 hours on: tried again
    third = second + 5 * HOUR + 900
    dies(third)  # and it dies again: 8 hours now
    _round(env, third + 900)
    _round(env, third + 1800)
    assert len(env.started) == 3
    monkeypatch.setattr(AT, "CODE_ID", "the code after a deploy")
    _round(env, third + 2700)
    assert len(env.started) == 4 and LT.load(env.paths)["state"] == "running" and len(env.sent) == 1


def test_a_cycler_that_cannot_start_leaves_an_idle_sniper_a_round_at_most_and_backs_off(env, monkeypatch):
    """Review of 7 Nov: when the server cannot start the cycler (a full disk, say), each round's automatic request made a
    new hunt sniper on OKTech and left it idle, about 96 a day. A start fault that left a sniper idle is tried again at
    the next round but one (never in the round that found it), and after a second in a row none for 4 hours, doubling, a
    day at most, as a dead cycler; a deploy tries at once. The owner hears it once."""
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env)

    def cannot_start(argv, timeout=0):
        raise RuntimeError("no space left on device")

    env.jobs.ops.op_run = cannot_start
    at = NOW + 60
    _round(env, at)  # the first block: its sniper is created, its cycler cannot start
    assert len(env.svc.created) == 1 and not LT.load(env.paths)["started"]
    at += 900
    _round(env, at)  # finds it: nothing in this round
    assert len(env.svc.created) == 1
    at += 900
    _round(env, at)  # one more try
    assert len(env.svc.created) == 2
    at += 900
    _round(env, at)  # finds the second in a row
    second = at
    for _k in range(14):  # three and a half hours on: nothing more is created
        at += 900
        _round(env, at)
    assert len(env.svc.created) == 2 and len(env.started) == 0
    (held,) = _said(env, "Automatic live test holds")
    assert "did not start" in held and "tried at" in _seq(env)["waiting"]["why"]
    _round(env, second + 4 * HOUR + 900)
    assert len(env.svc.created) == 3  # 4 hours on: tried again, and it fails again
    _round(env, second + 4 * HOUR + 1800)  # finds the third in a row
    _round(env, second + 4 * HOUR + 2700)  # 8 hours now
    assert len(env.svc.created) == 3
    monkeypatch.setattr(AT, "CODE_ID", "the code after a deploy")
    _round(env, second + 4 * HOUR + 3600)  # a deploy tries at once
    assert len(env.svc.created) == 4 and len(_said(env, "Automatic live test holds")) == 1


def test_an_oktech_create_that_fails_leaves_nothing_idle_and_is_tried_each_round(env):
    """A create OKTech refuses leaves no sniper behind, so it holds without the back-off: every round tries again,
    whatever the number in a row."""
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env)
    env.svc.answer = RuntimeError("OKTech answered 502")
    at = NOW + 60
    for _k in range(8):
        _round(env, at)
        at += 900
    assert len(env.svc.created) == 8  # tries (the fake records each): one a round, never backed off
    seq = _seq(env)
    assert len(_said(env, "Automatic live test holds")) == 1 and seq["hold"]["kind"] == "start"
    assert "a deploy" not in str((seq.get("waiting") or {}).get("why", ""))
    env.svc.answer = None
    _round(env, at)
    assert LT.load(env.paths)["state"] == "running"


def test_the_owners_resume_tries_a_block_again_after_its_cycler_died_twice(env):
    """After a second death in a row no block starts for hours; the owner's /stop then /resume is their word to try
    again (review of 7 Nov), and the /resume reply says so."""
    _start(env)
    at = NOW + 60
    for _death in range(2):
        t = LT.load(env.paths)
        LTT._armed(env)
        _cycler_ended(env, t, None, at + 300, code=1, log="Traceback (most recent call last):\nNameError: x\n")
        at += 900
        _round(env, at)  # finds the death
        at += 900
        _round(env, at)  # judged: one more block after the first death, none after the second
    assert len(env.started) == 2 and "tried at" in _seq(env)["waiting"]["why"]
    LTT._send(env, name="stop", at=at + 60)
    reply = LTT._send(env, name="resume", at=at + 120)
    assert "goes on with" in reply and "died 2 times in a row" in reply and "tries one block" in reply
    _round(env, at + 900)
    assert len(env.started) == 3 and LT.load(env.paths)["state"] == "running"


def test_a_buy_the_sniper_made_after_its_cycler_died_counts_against_the_strategy(env):
    """A cycler that dies leaves its sniper armed until the round closes the block; a buy it makes then is the
    strategy's own and counts (review of 7 Nov: the block's window ended when the cycler died)."""
    _guard_off(env)
    _start(env)
    t, _trades, last = _trade(env, [0.02] * 3)
    died = last + 30
    _cycler_ended(env, t, None, died, code=137, log="Killed\n")
    sig, mint = "buy-after-death", "AfterDeath" + "A" * 34
    _ours(env, sig, died + 60, mint, True, 0.202)
    _ours(env, f"sell-{sig}", died + 80, mint, False, 0.012)
    _measure(env, [(sig, died + 60, mint, -0.19, None)])
    env.sol -= 0.19
    _round(env, NOW + 60 + 2 * HOUR)
    _round(env, NOW + 60 + 2 * HOUR + 900)
    live = _seq(env)["blocks"][0]["live"]
    assert live["n"] == 4 and live["sum"] == pytest.approx(-0.13)


STUCK = "Stuck" + "S" * 39


def _stuck(e, pnls):
    """The block running now buys and sells a coin for each of `pnls`, then OKTech leaves its next coin unsold: its
    sniper is spent and nothing sells it. Its cycler ends on its time limit a minute later with the coin held."""
    t, _trades, last = _trade(e, pnls)
    _ours(e, "buy-stuck", last + 10, STUCK, True, 0.202)
    e.store.put_fill({"sig": "buy-stuck", "ts": last + 10, "wallet": W, "mint": STUCK, "side": "buy", "sol": 0.202,
                      "tokens": 1_000_000.0})  # fmt: skip
    coin = {"mint": STUCK, "amount": 1.0, "raw": 1_000_000, "decimals": 6, "value_sol": 0.05, "priced": True}
    e.svc.held = [coin]
    LTT._armed(e, status="finished")  # its sniper is spent: it sells nothing more
    _cycler_ended(e, t, {"start_sol": 11.8, "end_sol": 11.7, "reason": TIME + " (a coin is held)", "fills": len(pnls) + 1,
                         "restarts": 3, "held": [coin], "left_running": []}, last + 60)  # fmt: skip


def _sold(e, buy_sig, mint, ts, pnl):
    """The owner's /sell sells the coin at `ts` for `pnl`: the sale on the tape, and in the fills once the measure stage
    has read it."""
    e.svc.held = []
    _ours(e, f"sell-{buy_sig}", ts, mint, False, 0.202 + pnl)
    e.store.put_fill({"sig": f"sell-{buy_sig}", "ts": ts, "wallet": W, "mint": mint, "side": "sell", "sol": 0.202 + pnl,
                      "tokens": 1_000_000.0, "pnl_sol": pnl, "buy_sig": buy_sig})  # fmt: skip


def test_a_coin_oktech_left_unsold_holds_until_it_is_sold_or_gone(env):
    """The block's own coin left in the wallet with its sniper stopped, nothing selling it: the owner hears it once with
    the line that sells it, no block starts while it is held, and the same strategy goes on once it is sold (its sale is
    what counts) or gone."""
    _start(env)
    _stuck(env, [0.02] * 3)
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "running" and seq["blocks"][0]["outcome"]["kind"] == "coin"
    (held,) = env.sent
    assert STUCK in held and f"/sell {STUCK}" in held and "not dropped" in held
    for k in range(1, 3):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k)
    assert len(env.svc.created) == 1 and len(env.sent) == 1  # held: no block, nothing said again
    _sold(env, "buy-stuck", STUCK, NOW + 60 + 2 * HOUR + 900 * 3 - 300, -0.15)  # the owner's /sell sold it
    _round(env, NOW + 60 + 2 * HOUR + 900 * 3)
    assert LT.load(env.paths)["sequence"]["block"] == 2 and LT.load(env.paths)["state"] == "running"
    assert _seq(env)["blocks"][0]["live"]["sum"] == pytest.approx(0.06 - 0.15)  # the sale is what counts
    _round(env, NOW + 60 + 2 * HOUR + 900 * 4)
    (cleared,) = _said(env, "Automatic live test goes on")
    assert STUCK[:6] in cleared and len(env.sent) == 2


def test_a_coin_oktech_left_unsold_is_never_counted_at_its_whole_cost_while_it_is_held(env):
    """A coin OKTech left unsold is an execution fault: it holds, never drops (review of 7 Nov: 45 minutes after the
    block it was counted at its whole cost). The strategy has 10 trades at +0.012, above half of paper's +0.02, and goes
    on; block 2 leaves its third coin unsold and the owner does not /sell it for an hour and a half. Counted at its whole
    cost, the strategy would read -0.0047 a trade and be dropped for good; it holds instead, and once /sell sells the
    coin the sale is what counts: +0.0118 a trade, and block 3 starts."""
    _start(env)
    _play(env, [0.012] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["sequence"]["block"] == 2
    _stuck(env, [0.012] * 2)
    at = NOW + 60 + 4 * HOUR
    for k in range(7):
        _round(env, at + 900 * k)
    seq = _seq(env)
    assert seq["state"] == "running" and seq["hold"]["kind"] == "coin" and len(env.svc.created) == 2
    assert "decision" not in seq["blocks"][1] and len(_said(env, "Automatic live test holds")) == 1
    _sold(env, "buy-stuck", STUCK, at + 900 * 7 - 300, 0.01)
    _round(env, at + 900 * 7)
    t, seq = LT.load(env.paths), _seq(env)
    assert t["sequence"]["block"] == 3 and t["state"] == "running" and seq["blocks"][1]["decision"] == "continue"
    assert seq["blocks"][1]["live"]["sum"] == pytest.approx(0.024 + 0.01) and not _said(
        env, "Automatic live test dropped"
    )


def test_a_coin_gone_with_no_sale_counts_its_whole_cost_once_it_has_been_gone_45_minutes(env):
    """When the block's own coin leaves the wallet with no sale read (it went to dust, or was moved out), its buy counts
    its whole cost once 45 minutes have passed since a round last saw it held (a sale the owner's /sell made has time to
    reach the fills), and the strategy goes on with its next block."""
    _start(env)
    _stuck(env, [0.012] * 3)
    at = NOW + 60 + 2 * HOUR
    for k in range(3):  # held: rounds at +0, +15 and +30 minutes
        _round(env, at + 900 * k)
    env.svc.held = []  # gone, and no sale
    for k in range(3, 5):  # +45 and +60 minutes: 15 and 30 minutes since a round last saw it held
        _round(env, at + 900 * k)
    assert len(env.svc.created) == 1 and "decision" not in _seq(env)["blocks"][0]
    _round(env, at + 900 * 5)  # 45 minutes since it was last seen
    t, seq = LT.load(env.paths), _seq(env)
    assert t["sequence"]["block"] == 2 and t["state"] == "running" and seq["blocks"][0]["decision"] == "next"
    assert seq["blocks"][0]["live"]["unmeasured"] == 1 and seq["blocks"][0]["live"]["sum"] < 0.036 - 0.2


def test_oktech_not_answering_while_a_block_closes_holds_and_clears(env):
    """At the block's end OKTech does not answer, so its sniper may still be armed: the owner hears it once; the block
    closes once OKTech answers, and the next one starts."""
    _start(env)
    _play(env, [0.02] * 10)
    env.svc.oktech_down = True
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["state"] == "closing"
    (held,) = env.sent
    assert held.startswith("Automatic live test holds") and "may still be armed" in held
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert len(env.sent) == 1 and len(env.svc.created) == 1
    env.svc.oktech_down = False
    _round(env, NOW + 60 + 2 * HOUR + 1800)
    assert LT.load(env.paths)["sequence"]["block"] == 2 and LT.load(env.paths)["state"] == "running"
    _round(env, NOW + 60 + 2 * HOUR + 2700)
    assert len(_said(env, "Automatic live test goes on")) == 1 and len(env.sent) == 2


@pytest.mark.parametrize("what, words", [("balance", "balance"), ("coins", "coins"), ("oktech", "OKTech")])
def test_an_unreadable_wallet_or_oktech_holds_the_next_block_and_clears(env, what, words):
    """Block 1 has closed and waits for the measure stage; when it is judged, the wallet's balance (the round's
    snapshot), its coins (read before a start) or OKTech's snipers cannot be read: the next block holds, the owner
    hears it once, and once it can be read the same strategy goes on at its floor."""
    _start(env)
    _play(env, [0.02] * 10)
    env.store.set_coverage("measure:last_ts", {"ts": NOW + 60 + 100})  # behind the block's last trade
    _round(env, NOW + 60 + 2 * HOUR, measure=False)  # it closes, and waits for the measure stage
    assert LT.load(env.paths)["state"] == "done" and env.sent == []
    env.svc.unreadable, env.svc.oktech_down = what == "coins", what == "oktech"
    for k in (1, 2):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k, readable=what != "balance")
    assert LT.load(env.paths)["sequence"]["block"] == 1 and len(env.svc.created) == 1  # no block 2
    (held,) = _said(env, "Automatic live test holds")
    assert words in held and "not dropped" in held and _seq(env)["state"] == "running"
    env.svc.unreadable = env.svc.oktech_down = False
    _round(env, NOW + 60 + 2 * HOUR + 900 * 3)
    t = LT.load(env.paths)
    assert t["sequence"]["block"] == 2 and t["state"] == "running" and t["floor"] == 10.9
    _round(env, NOW + 60 + 2 * HOUR + 900 * 4)
    assert len(_said(env, "Automatic live test goes on")) == 1 and len(env.sent) == 2


def test_the_measure_stage_stalled_past_its_wait_holds_and_judging_goes_on_when_it_runs(env):
    """The failed sends of a block's last minutes reach the fills at the measure stage's next run: a block is judged once
    that stage has run after its last trade. A stage stalled for 45 minutes after the block ended is an execution fault:
    it holds the strategy (never judged on numbers that were never read, never dropped for it), and judging goes on
    when the stage runs again."""
    _start(env)
    _play(env, [0.02] * 10)
    env.store.set_coverage("measure:last_ts", {"ts": NOW + 60 + 100})  # it last ran before the block's last sale
    _round(env, NOW + 60 + 2 * HOUR, measure=False)
    assert _seq(env)["state"] == "running" and "decision" not in _seq(env)["blocks"][0] and env.sent == []
    for k in range(1, 5):
        _round(env, NOW + 60 + 2 * HOUR + 900 * k, measure=False)
    (held,) = _said(env, "Automatic live test holds")
    assert "measure stage" in held and _seq(env)["state"] == "running" and len(env.svc.created) == 1
    _round(env, NOW + 60 + 2 * HOUR + 900 * 5)  # it runs again
    assert LT.load(env.paths)["sequence"]["block"] == 2
    _round(env, NOW + 60 + 2 * HOUR + 900 * 6)
    assert len(_said(env, "Automatic live test goes on")) == 1


def test_a_wallet_without_room_for_a_buy_holds_and_is_never_a_drop(env):
    """7 Nov 10:59 the wallet was emptied. With no room for a buy (SOL under the buy, its fees and rent) the next block
    holds, said once a day, and the strategy is not dropped; once SOL is back, the same strategy goes on, its floor the
    wallet it starts from less the 1.2 SOL left of its 1 SOL (its +0.2 SOL live included)."""
    _guard_off(env)
    _start(env)
    _play(env, [0.02] * 10)
    env.sol = 0.1  # 0.2 SOL in all
    _round(env, NOW + 60 + 2 * HOUR)
    (held,) = _said(env, "Automatic live test holds")
    assert "no room for a buy" in held and "0.20 SOL" in held and _seq(env)["state"] == "running"
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert len(_said(env, "Automatic live test holds")) == 1 and len(env.svc.created) == 1  # once a day
    _round(env, NOW + 60 + 2 * HOUR + 86400)  # the next day: said again
    assert len(_said(env, "Automatic live test holds")) == 2 and _seq(env)["state"] == "running"
    env.sol = 11.8
    _round(env, NOW + 60 + 2 * HOUR + 86400 + 900)
    t = LT.load(env.paths)
    assert t["sequence"]["block"] == 2 and t["state"] == "running" and t["floor"] == 10.7  # 11.9 SOL less 1.2


# -- only the owner turns it off; /stop holds it ---------------------------------------------------------------------


def test_it_never_turns_itself_off(env):
    """A drop, days of waiting with nothing to trade: the switch stays on and nothing marks it used; only the owner's
    /test auto off turns it off."""
    _start(env)
    _play(env, [-0.01] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    for day in range(1, 4):
        _round(env, NOW + 60 + 2 * HOUR + day * 86400)
    at = AT.read_autotest(env.paths)
    assert at["on"] is True and at["used"] is None and AT.is_on(env.paths)
    LTT._send(env, "auto", "off", at=NOW + 4 * 86400)
    assert AT.read_autotest(env.paths)["on"] is False


def test_turning_it_off_lets_the_running_block_finish_and_starts_no_more(env):
    _start(env)
    LTT._send(env, "auto", "off", at=NOW + 600)
    _play(env, [0.02] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    assert len(env.svc.created) == 1 and _seq(env)["state"] == "ended"
    (line,) = _said(env, "Automatic live test ended")
    assert "turned the automatic test off" in line and "/test auto on" in line
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert len(env.svc.created) == 1


def test_turning_it_on_again_before_the_block_ends_lets_the_same_strategy_go_on(env):
    """Turned off mid-block and on again before the block ends: the latest yes is the one that counts, the strategy's run
    goes on, and it keeps its floor."""
    _start(env)
    first = _seq(env)
    LTT._send(env, "auto", "off", at=NOW + 600)
    reply = LTT._send(env, "auto", "on", at=NOW + 900)
    assert "Automatic live test on" in reply and AT.read_autotest(env.paths)["on"] is True
    _play(env, [0.02] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    t = LT.load(env.paths)
    assert t["sequence"]["block"] == 2 and t["floor"] == 10.9 and _seq(env)["id"] == first["id"]
    assert t["approved"]["words"] == "/test auto on"  # the block carries the latest yes


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


def test_the_round_never_starts_an_automatic_request_below_its_floor_or_not_its_own(env):
    """The round's own check of every automatic request (autotest.holds), whatever wrote it: its sequence's newest
    block, at the sequence's floor. There is no limit on blocks."""
    _start(env)
    t, seq = LT.load(env.paths), _seq(env)
    assert AT.holds(env.paths, t, env.cfg) is None
    assert "floor" in AT.holds(env.paths, t | {"floor": t["floor"] - 1}, env.cfg)
    assert AT.holds(env.paths, t | {"sequence": {"id": seq["id"], "block": 50}}, env.cfg) is None
    assert "own" in AT.holds(env.paths, t | {"requested_at": t["requested_at"] + 1}, env.cfg)


def test_stop_holds_the_automatic_test_and_resume_lets_the_same_strategy_go_on(env):
    """/stop holds every start while its flag exists: the block running ends (its cycler reads the flag and stops its
    sniper once the wallet holds nothing), no block starts, the strategy is not dropped and the switch stays on. /resume
    lets the same strategy go on at its floor. Both replies say so."""
    _start(env)
    reply = LTT._send(env, name="stop", at=NOW + 600)
    assert "holds while /stop is set" in reply and "/resume" in reply and BEST in reply
    _play(env, [0.02] * 3, reason="stopped by the owner's /stop: no more re-arms; the wallet holds nothing, so the "
                                  "snipers it armed are stopped")  # fmt: skip
    _round(env, NOW + 60 + 2 * HOUR)
    seq = _seq(env)
    assert seq["state"] == "running" and len(env.svc.created) == 1 and AT.read_autotest(env.paths)["on"] is True
    assert seq["blocks"][0]["outcome"]["kind"] == "stop" and "/stop" in seq["waiting"]["why"]
    _round(env, NOW + 60 + 2 * HOUR + 900)
    assert len(env.svc.created) == 1  # still /stop: nothing starts
    reply = LTT._send(env, name="resume", at=NOW + 60 + 2 * HOUR + 1000)
    assert "goes on with" in reply and BEST in reply
    _round(env, NOW + 60 + 2 * HOUR + 1800)
    t = LT.load(env.paths)
    assert t["id"] == BEST and t["state"] == "running" and t["sequence"]["block"] == 2 and t["floor"] == 10.9
    assert not _said(env, "Automatic live test")  # the replies said it: no line of its own


def test_stop_between_blocks_holds_and_resume_goes_on(env):
    """Block 1 reached its cap; the owner sends /stop, then /resume, both before the next round: the strategy goes on
    with block 2 (the 09:15 plan ended the sequence here)."""
    _start(env)
    _play(env, [0.02] * 10)
    LTT._send(env, name="stop", at=NOW + 60 + 2 * HOUR - 300)
    LTT._send(env, name="resume", at=NOW + 60 + 2 * HOUR - 120)
    _round(env, NOW + 60 + 2 * HOUR)
    assert LT.load(env.paths)["sequence"]["block"] == 2 and _seq(env)["state"] == "running"


def test_stop_with_no_sequence_running_holds_the_standing_yes_until_resume(env):
    """With no strategy running /stop changes nothing in the owner's switch: nothing starts while the flag is set, the
    reply says so, and after /resume the standing yes starts a strategy as before."""
    LTT._freeze(env.store, BEST, pnl=0.02)
    _on(env)
    reply = LTT._send(env, name="stop", at=NOW + 30)
    assert "automatic live test starts nothing" in reply and "/test auto off" in reply
    _round(env, NOW + 60)
    assert LT.load(env.paths) is None and AT.read_autotest(env.paths)["on"]
    LTT._send(env, name="resume", at=NOW + 120)
    _round(env, NOW + 960)
    assert LT.load(env.paths)["state"] == "running" and LT.load(env.paths)["by"] == "autotest"


def test_an_automatic_test_on_from_the_0915_plan_runs_the_widened_plan(env):
    """The owner's latest yes: a switch turned on under the 09:15 plan (no plan named, a cap of 3 more blocks) runs the
    widened plan from its next strategy on, and a run that plan started keeps its floor and goes on past its 4 blocks."""
    LTT._freeze(env.store, BEST, pnl=0.02)
    old = {"on": True, "buy_sol": 0.2, "fills": 10, "minutes": 120, "loss_sol": 1.0, "more_blocks": 3,
           "continue_at": 0.5, "approved": {"words": YES, "at": NOW - HOUR}, "wallet": W, "oktech_wallet": "SNIPER",
           "ts": NOW - HOUR, "by": "cli"}  # fmt: skip
    AT.autotest_file(env.paths).write_text(json.dumps(old))
    assert AT.read_autotest(env.paths)["on"] and AT.read_autotest(env.paths)["plan"] is None
    _round(env, NOW + 60)
    seq = _seq(env)
    keep = ("id", "state", "strategy", "label", "approved", "wallet", "oktech_wallet", "buy_sol", "start_equity",
            "started_at", "blocks")  # fmt: skip
    old_seq = {k: seq[k] for k in keep} | {"caps": {**AT.DEFAULTS, "more_blocks": 3}, "floor": 10.5}  # as 09:15 wrote
    AT.save_seq(env.paths, old_seq)
    at = NOW + 60
    for _block in range(5):
        _play(env, [0.02] * 10)
        at += 2 * HOUR
        _round(env, at)
    t = LT.load(env.paths)
    assert (
        t["sequence"]["block"] == 6 and t["state"] == "running" and t["floor"] == 10.5 and _seq(env)["id"] == seq["id"]
    )


# -- what the owner reads ------------------------------------------------------------------------------------------------


def test_test_shows_the_automatic_block_and_the_switch(env):
    _start(env)
    assert "automatic test, block 1" in LTT._send(env, at=NOW + 600)
    reply = LTT._send(env, "auto", at=NOW + 600)
    assert "running" in reply and "block 1" in reply and BEST in reply


def test_status_shows_the_automatic_test_what_holds_it_and_what_was_dropped(env):
    _start(env)
    reply = LTT._send(env, name="status", at=NOW + 600)
    assert "automatic test: on" in reply and BEST in reply and "block 1" in reply and "never stops" in reply
    assert "paper +0.0200" in reply
    _play(env, [0.02] * 10)
    env.svc.answer = RuntimeError("OKTech answered 502")
    _round(env, NOW + 60 + 2 * HOUR)
    _round(env, NOW + 60 + 2 * HOUR + 900)
    reply = LTT._send(env, name="status", at=NOW + 60 + 2 * HOUR + 960)
    assert "holds: block 2 did not start" in reply and "10 live round trips" in reply and "+0.0200" in reply
    env.svc.answer = None
    _round(env, NOW + 60 + 2 * HOUR + 1800)
    _play(env, [-0.05] * 10)
    _round(env, NOW + 60 + 4 * HOUR)
    reply = LTT._send(env, name="status", at=NOW + 60 + 4 * HOUR + 60)
    assert "automatic test: on" in reply and f"dropped: {BEST}" in reply and "no strategy running" in reply


def test_the_daily_digest_has_the_days_blocks(env):
    from snipetech.operator.budget import Ledger
    from snipetech.operator.digest import digest_text

    _start(env)
    _play(env, [0.02] * 10)
    _round(env, NOW + 60 + 2 * HOUR)
    _play(env, [0.02] * 10)
    _round(env, NOW + 60 + 4 * HOUR)
    at = NOW + 60 + 4 * HOUR + 60
    text = digest_text(env.snap, [], Ledger(env.paths.spend, env.cfg.operator.prices), env.store, at, paths=env.paths)
    (line,) = [x for x in text.splitlines() if x.startswith("automatic test")]
    assert "3 blocks" in line and BEST in line and "20 live round trips" in line and "+0.0200" in line


# -- nobody else -------------------------------------------------------------------------------------------------------


def test_no_agent_tool_can_turn_it_on_or_touch_the_sequence(env):
    from snipetech.operator.tools import make_operator_tools

    tools = {t.name: t for t in make_operator_tools(env.svc, env.paths, env.jobs, env.cfg.operator, env.notes)}
    on = json.dumps({"on": True, "buy_sol": 0.5, "fills": 10, "minutes": 180, "approved": {"words": "yes", "at": NOW},
                     "plan": "never-stops", "wallet": W, "oktech_wallet": "SNIPER"})  # fmt: skip
    seq = json.dumps({"id": "seq-1", "state": "running", "strategy": BEST, "floor": 0.0, "blocks": [], "dropped": []})
    calls = [("start_job", {"argv": ["operate", "autotest", "--on", "--approved", "yes", "--wallet", W]}),
             ("write_report", {"name": "AUTOTEST", "text": on}), ("write_report", {"name": "../operator/AUTOTEST", "text": on}),
             ("write_report", {"name": "autotest", "text": on}), ("write_report", {"name": "autotest.json", "text": seq}),
             ("set_candidates", {"items": [json.loads(on)]}),
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
        assert "/stop holds" in system and "switch either stop off" in system


def test_the_agents_code_never_loads_the_automatic_test():
    code = ("import sys, snipetech.operator.tools, snipetech.operator.turn, snipetech.strategist.turn, "
            "snipetech.strategist.run; print('snipetech.operator.autotest' in sys.modules)")  # fmt: skip
    src = str(Path(snipetech.__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env=os.environ | {"PYTHONPATH": src})  # fmt: skip
    assert out.stdout.strip() == "False"
