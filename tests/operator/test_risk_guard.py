"""The owner's wallet-wide guard (operator/risk_guard.py; 7 Nov 09:15, docs/live-recipe.md): no new live trade once the
day is 1 SOL down or the wallet is under 10 SOL, changeable from Telegram. On by default at the owner's values; the
day's result is our own trading (closed round trips plus the coins our buys still hold, at the tape's price), never the
balance's change, so a deposit or a withdrawal moves nothing; what it cannot read never counts as clear."""

import asyncio
import json
import math

import pytest

from snipetech.config import Config
from snipetech.operator import risk_guard as RG
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import Snapshot
from snipetech.operator.tools import NoteSink
from snipetech.store import Store

NOW = 1_794_045_600  # 7 Nov 2026 10:00 UTC
DAY0 = NOW - 10 * 3600  # 00:00 UTC
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
M1, M2, AIR = "Coin1" + "1" * 39, "Coin2" + "2" * 39, "Airdrop" + "3" * 37


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    store = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    yield paths, store, cfg
    store.close()


def _buy(store, sig, ts, mint, sol=0.2, tokens=1_000_000, fee_lamports=(1_000_000, 2_000_000)):
    store.put_fill({"sig": sig, "ts": ts, "wallet": W, "mint": mint, "side": "buy", "sol": sol, "tokens": tokens,
                    "priority_lamports": fee_lamports[0], "tip_lamports": fee_lamports[1]})  # fmt: skip


def _sell(store, sig, ts, mint, pnl, tokens=1_000_000, twin=0.0, buy_sig=None):
    store.put_fill({"sig": sig, "ts": ts, "wallet": W, "mint": mint, "side": "sell", "sol": 0.1, "tokens": tokens,
                    "pnl_sol": pnl, "twin_fee_sol": twin, "buy_sig": buy_sig})  # fmt: skip


def _coin(mint, value, raw=1_000_000, priced=True):
    return {"mint": mint, "amount": raw / 1e6, "raw": raw, "decimals": 6, "value_sol": value if priced else None,
            "priced": priced}  # fmt: skip


# -- the owner's file ------------------------------------------------------------------------------------------------


def test_with_no_file_the_guard_is_on_at_the_owners_values(env):
    """The owner's yes (7 Nov 09:15): 1 SOL a day, 10 SOL in the wallet. There is no off: the loosest it goes is a 5
    SOL day stop and a floor of 0."""
    paths, _store, _cfg = env
    assert (RG.DAY_STOP_SOL, RG.FLOOR_SOL) == (1.0, 10.0)
    assert RG.read_risk(paths) == {} and RG.limits(paths) == {"day_stop_sol": 1.0, "floor_sol": 10.0}
    assert RG.risk_file(paths) == paths.root / "RISK"


def test_the_owners_values_are_written_atomically_with_when_and_who(env):
    paths, _store, _cfg = env
    assert RG.set_risk(paths, {"day_stop_sol": 1.5}, "telegram", NOW) == {"day_stop_sol": 1.5}
    assert RG.limits(paths) == {"day_stop_sol": 1.5, "floor_sol": 10.0}
    RG.set_risk(paths, {"floor_sol": 9}, "cli", NOW + 60)  # a value not named keeps the file's
    data = json.loads(RG.risk_file(paths).read_text())
    assert data == {"day_stop_sol": 1.5, "floor_sol": 9.0, "ts": NOW + 60, "by": "cli"}
    assert not list(paths.root.glob("RISK*.tmp"))
    RG.clear_risk(paths)
    assert RG.limits(paths) == {"day_stop_sol": 1.0, "floor_sol": 10.0} and not RG.risk_file(paths).exists()
    RG.clear_risk(paths)  # nothing to remove is fine


@pytest.mark.parametrize(
    "values",
    [{"day_stop_sol": 0.05}, {"day_stop_sol": 5.01}, {"floor_sol": -1}, {"floor_sol": 1000.5}, {"day_stop_sol": math.nan},
     {"floor_sol": math.inf}, {"day_stop_sol": True}, {"floor_sol": "9"}, {"floor_sol": 10**400}, {"guard": 0}],
)  # fmt: skip
def test_a_value_out_of_bounds_changes_nothing(env, values):
    paths, _store, _cfg = env
    RG.set_risk(paths, {"day_stop_sol": 2.0}, "cli", NOW)
    before = RG.risk_file(paths).read_text()
    with pytest.raises(ValueError):
        RG.set_risk(paths, {"floor_sol": 8.0, **values}, "cli", NOW)
    assert RG.risk_file(paths).read_text() == before


def test_a_hand_edited_or_broken_file_never_breaks_the_guard(env):
    """A read never raises; a value out of its bounds falls back to the owner's 7 Nov value alone."""
    paths, _store, _cfg = env
    f = RG.risk_file(paths)
    f.write_text(json.dumps({"day_stop_sol": 50, "floor_sol": 8.5, "ts": NOW, "by": "hand"}))
    assert RG.limits(paths) == {"day_stop_sol": 1.0, "floor_sol": 8.5}
    for text in ("{not json", "[1, 2]", json.dumps({"floor_sol": 10**400}), ""):
        f.write_text(text)
        assert RG.limits(paths) == {"day_stop_sol": 1.0, "floor_sol": 10.0}, text
    f.unlink()
    f.mkdir()  # not even a file
    assert RG.limits(paths) == {"day_stop_sol": 1.0, "floor_sol": 10.0}


# -- the day's result ----------------------------------------------------------------------------------------------


def test_the_day_is_todays_closed_trips_plus_the_coins_our_buys_still_hold_at_the_tapes_price(env):
    """Realised: today's sells with their failed duplicate sends (the book's own day accounting). Open: each coin the
    wallet holds that our own buys still hold, at the tape's price against what those buys cost (SOL and fees). A coin
    no buy of ours explains (an airdrop) is not a trading result."""
    _paths, store, _cfg = env
    _buy(store, "b-old", DAY0 - 3600, M2)  # yesterday's buy, sold today: today's trip
    _sell(store, "s-old", DAY0 + 600, M2, pnl=-0.30, twin=0.01, buy_sig="b-old")
    _sell(store, "s-yday", DAY0 - 600, "Gone" + "9" * 40, pnl=-0.5)  # a sell before 00:00 UTC is yesterday's
    _buy(store, "b1", NOW - 600, M1)  # 0.2 SOL and 0.003005 of fees, still held
    day = RG.day_result(store, W, NOW, [_coin(M1, 0.05), _coin(AIR, 3.0)], per_tx_sol=0.0035)
    assert day["realised_sol"] == pytest.approx(-0.31)
    assert day["open_sol"] == pytest.approx(0.05 - 0.203005, abs=1e-6)
    assert day["total_sol"] == pytest.approx(-0.31 + 0.05 - 0.203005, abs=1e-6)
    assert day["coins"] == 1 and day["unpriced"] == 0


def test_a_part_sold_coin_counts_only_what_is_left(env):
    _paths, store, _cfg = env
    _buy(store, "b1", NOW - 900, M1, sol=0.2, tokens=1_000_000)
    _sell(store, "s1", NOW - 600, M1, pnl=0.05, tokens=500_000, buy_sig="b1")
    day = RG.day_result(store, W, NOW, [_coin(M1, 0.06, raw=500_000)], per_tx_sol=0.0035)
    assert day["realised_sol"] == pytest.approx(0.05)
    assert day["open_sol"] == pytest.approx(0.06 - 0.203005 / 2, abs=1e-6)
    # the wallet read lags the measure stage, or the other way round: only what both see is counted
    late = RG.day_result(store, W, NOW, [_coin(M1, 0.12, raw=1_000_000)], per_tx_sol=0.0035)
    assert late["open_sol"] == pytest.approx(0.06 - 0.203005 / 2, abs=1e-6)


def test_a_coin_bought_before_the_measure_stage_saw_it_is_left_out_not_guessed(env):
    """The fills lag the wallet by a few minutes: a coin with no buy of ours in them has no cost known yet, and an
    unpriced coin no value; both count for nothing until they are known, and are counted as such."""
    _paths, store, _cfg = env
    _buy(store, "b2", NOW - 300, M2)
    day = RG.day_result(store, W, NOW, [_coin(M1, 0.05), _coin(M2, 0.0, priced=False)], per_tx_sol=0.0035)
    assert day["open_sol"] == 0.0 and day["coins"] == 0 and day["unpriced"] == 1


def test_rows_the_measure_stage_left_half_written_are_skipped(env):
    """The book's own tests and old rows carry fills without tokens or a mint: they never break the guard."""
    _paths, store, _cfg = env
    store.put_fill({"sig": "B1", "ts": NOW - 3600, "wallet": W, "side": "buy", "sol": 0.2})
    store.put_fill({"sig": "S1", "ts": NOW - 1800, "wallet": W, "side": "sell", "sol": 0.15, "pnl_sol": -0.052})
    day = RG.day_result(store, W, NOW, [_coin(M1, 0.05)], per_tx_sol=0.0035)
    assert day["realised_sol"] == pytest.approx(-0.052) and day["open_sol"] == 0.0


# -- tripped or not ------------------------------------------------------------------------------------------------


def test_the_day_stop_trips_at_one_sol_down_and_the_floor_under_ten(env):
    paths, store, cfg = env
    _sell(store, "s1", NOW - 600, M2, pnl=-0.99)
    g = RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[])
    assert g.known and not g.tripped and g.reason == "" and g.day["total_sol"] == pytest.approx(-0.99)
    _sell(store, "s2", NOW - 500, M2, pnl=-0.01)
    g = RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[])
    assert g.tripped and g.kinds == ("day",) and "1.00 SOL down" in g.reason and "1 SOL" in g.reason
    assert RG.evaluate(store, cfg, paths, NOW + 14 * 3600, sol=11.0, held=[]).tripped is False  # a new day
    store._exec("DELETE FROM fills")
    g = RG.evaluate(store, cfg, paths, NOW, sol=9.99, held=[])
    assert g.tripped and g.kinds == ("floor",) and "9.99 SOL" in g.reason and "10 SOL" in g.reason
    assert not RG.evaluate(store, cfg, paths, NOW, sol=10.0, held=[]).tripped  # under 10, not at 10
    # the wallet counts its coins at the tape's price; the owner's ignored coins are left out
    assert not RG.evaluate(store, cfg, paths, NOW, sol=9.0, held=[_coin(M1, 1.2)]).tripped
    cfg.operator.ignore_mints = [M1]
    assert RG.evaluate(store, cfg, paths, NOW, sol=9.0, held=[_coin(M1, 1.2)]).tripped


def test_an_open_coin_marked_down_trips_the_day_before_it_is_sold(env):
    paths, store, cfg = env
    _sell(store, "s1", NOW - 900, M2, pnl=-0.85)
    _buy(store, "b1", NOW - 600, M1)
    assert not RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[_coin(M1, 0.15)]).tripped
    g = RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[_coin(M1, 0.04)])
    assert g.tripped and g.kinds == ("day",) and "in coins still held" in g.reason


def test_the_owners_values_from_telegram_are_the_ones_applied(env):
    paths, store, cfg = env
    _sell(store, "s1", NOW - 600, M2, pnl=-1.2)
    assert RG.evaluate(store, cfg, paths, NOW, sol=9.5, held=[]).kinds == ("day", "floor")
    RG.set_risk(paths, {"day_stop_sol": 1.5, "floor_sol": 9.0}, "telegram", NOW)
    g = RG.evaluate(store, cfg, paths, NOW, sol=9.5, held=[])
    assert g.known and not g.tripped and (g.day_stop_sol, g.floor_sol) == (1.5, 9.0) and g.owner_set


def test_a_deposit_or_a_withdrawal_is_not_a_trading_result(env):
    """6 Nov 15:16 the owner took 2.54 SOL out of the sniper wallet in the middle of a test: the day's result moves only
    with our own trades. The floor is about the wallet itself, so a withdrawal under it does stop new trades."""
    paths, store, cfg = env
    _sell(store, "s1", NOW - 600, M2, pnl=-0.4)
    before = RG.evaluate(store, cfg, paths, NOW, sol=12.0, held=[])
    after_out = RG.evaluate(store, cfg, paths, NOW, sol=10.5, held=[])  # 1.5 SOL withdrawn
    after_in = RG.evaluate(store, cfg, paths, NOW, sol=17.0, held=[])  # 5 SOL deposited
    assert before.day == after_out.day == after_in.day and before.day["total_sol"] == pytest.approx(-0.4)
    assert not (before.tripped or after_out.tripped or after_in.tripped)
    _sell(store, "s2", NOW - 300, M2, pnl=-0.7)  # a deposit never hides a day 1.1 SOL down
    assert RG.evaluate(store, cfg, paths, NOW, sol=17.0, held=[]).kinds == ("day",)
    store._exec("DELETE FROM fills")
    assert RG.evaluate(store, cfg, paths, NOW, sol=9.5, held=[]).kinds == ("floor",)  # withdrawn under the floor


def test_what_the_guard_cannot_read_never_counts_as_clear(env):
    paths, store, cfg = env
    for kw in ({"sol": None, "held": []}, {"sol": 11.0, "held": None}):
        g = RG.evaluate(store, cfg, paths, NOW, **kw)
        assert not g.known and not g.tripped and "cannot tell" in g.reason, kw
    # a coin the tape has not priced: the wallet is known to be at least its SOL
    assert RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[_coin(M1, 0, priced=False)]).known
    g = RG.evaluate(store, cfg, paths, NOW, sol=9.0, held=[_coin(M1, 0, priced=False)])
    assert not g.known and not g.tripped
    # a stop that holds is known whatever else could not be read
    _sell(store, "s1", NOW - 600, M2, pnl=-1.1)
    g = RG.evaluate(store, cfg, paths, NOW, sol=None, held=[])
    assert g.tripped and g.known and g.kinds == ("day",)
    cfg.operator.wallet = ""
    assert not RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[]).known

    class Broken:
        def fills(self, *a, **k):
            raise OSError("disk I/O error")

    g = RG.evaluate(Broken(), cfg, paths, NOW, wallet=W, sol=11.0, held=[])
    assert not g.known and not g.tripped and "OSError" in g.reason


def test_tripped_says_yes_or_no_and_reads_the_wallet_when_it_must(env):
    """The one call every start path can make: (True, why) while a stop holds or the guard cannot tell, (False, "")
    when it is clear. Without the numbers in hand it reads the snapshot, or else the wallet itself."""
    paths, store, cfg = env

    class Svc:
        def __init__(self, sol, coins=(), fail=False):
            self.store, self.sol, self.coins, self.fail = store, sol, list(coins), fail

        async def wallet_sol(self, address):
            if self.fail:
                raise RuntimeError("rpc down")
            return self.sol

        async def open_positions(self, address, ignore=None, unknown=None):
            return [c for c in self.coins if c["mint"] not in set(ignore or ())]

    assert asyncio.run(RG.tripped(Svc(12.0), cfg, paths, NOW)) == (False, "")
    yes, why = asyncio.run(RG.tripped(Svc(9.0), cfg, paths, NOW))
    assert yes and "under the 10 SOL floor" in why
    yes, why = asyncio.run(RG.tripped(Svc(12.0, fail=True), cfg, paths, NOW))
    assert yes and "cannot tell" in why and "RuntimeError" in why
    snap = Snapshot(ts=NOW - 300, wallet=W, native_sol=8.0, wsol=0.5, held=[], snipers=[])
    yes, why = asyncio.run(RG.tripped(Svc(50.0), cfg, paths, NOW, snap=snap))  # the snapshot's 8.5 SOL
    assert yes and "8.50 SOL" in why
    old = Snapshot(ts=NOW - 3 * 3600, wallet=W, native_sol=8.0, wsol=0.5, held=[], snipers=[])
    assert asyncio.run(RG.tripped(Svc(50.0), cfg, paths, NOW, snap=old)) == (False, "")  # too old: the wallet read


def test_the_trip_is_told_once_a_day_for_each_stop(env):
    paths, store, cfg = env
    sent: list[str] = []
    notes, notifier = NoteSink(paths, now=lambda: NOW), Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))
    clear = RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[])
    assert RG.announce(clear, notes, notifier, NOW) is False and sent == []
    floor = RG.evaluate(store, cfg, paths, NOW, sol=9.0, held=[])
    assert RG.announce(floor, notes, notifier, NOW) is True
    for k in range(4):  # every round after it: nothing more
        assert (
            RG.announce(RG.evaluate(store, cfg, paths, NOW + 900 * k, sol=9.0, held=[]), notes, notifier, NOW + 900 * k)
            is False
        )
    (line,) = sent
    assert "wallet-wide guard tripped" in line and "9.00 SOL" in line and "/risk" in line
    assert "[decision] wallet-wide guard tripped" in paths.notes.read_text()
    _sell(store, "s1", NOW - 600, M2, pnl=-1.0)  # the day stop as well: that one is news
    assert RG.announce(RG.evaluate(store, cfg, paths, NOW + 60, sol=9.0, held=[]), notes, notifier, NOW + 60) is True
    assert len(sent) == 2 and "00:00 UTC" in sent[1]
    tomorrow = NOW + 86400
    assert RG.announce(RG.evaluate(store, cfg, paths, tomorrow, sol=9.0, held=[]), notes, notifier, tomorrow) is True


def test_the_status_line_says_the_guard_and_todays_result(env):
    paths, store, cfg = env
    _sell(store, "s1", NOW - 600, M2, pnl=-0.32)
    line = RG.status_line(RG.evaluate(store, cfg, paths, NOW, sol=11.2, held=[]))
    assert "1 SOL" in line and "10 SOL" in line and "-0.32" in line and "11.20" in line and "TRIPPED" not in line
    line = RG.status_line(RG.evaluate(store, cfg, paths, NOW, sol=9.2, held=[]))
    assert "TRIPPED" in line and "9.20" in line
    assert "cannot tell" in RG.status_line(RG.evaluate(store, cfg, paths, NOW, sol=None, held=None))


def test_the_service_reads_the_wallets_sol_and_wrapped_sol_together():
    """What the guard calls the wallet's SOL: native SOL plus wrapped SOL (OKTech wraps some at a start: 6 Nov 15:08 the
    SOL balance fell by 0.0996 and the wallet did not)."""
    from snipetech.service import Service

    wsol = "So11111111111111111111111111111111111111112"

    class Rpc:
        async def call(self, method, params):
            if method == "getBalance":
                return {"value": 11_500_000_000}
            token = {"mint": wsol, "tokenAmount": {"uiAmount": 0.25, "decimals": 9, "amount": "250000000"}}
            return {"value": [{"pubkey": "acct", "account": {"data": {"parsed": {"info": token}}}}]}

    store = Store(":memory:")
    svc = Service(Config(), store=store)
    svc._rpc = Rpc()
    try:
        assert asyncio.run(svc.wallet_sol(W)) == pytest.approx(11.5 + 0.25 * 2)  # two token programs, one account each
    finally:
        store.close()
