"""The owner's wallet-wide guard (operator/risk_guard.py; 7 Nov 09:15, docs/live-recipe.md): no new live trade once the
day is 1 SOL down or the wallet is under 10 SOL, changeable from Telegram. On by default at the owner's values; the
day's result is our own trading (closed round trips plus the coins our buys still hold, at the tape's price), never the
balance's change, so a deposit or a withdrawal moves nothing; what it cannot read never counts as clear."""

import asyncio
import json
import math

import pytest

from snipetech.config import Config
from snipetech.models import Trade
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


def _tape(store, sig, ts, mint, buy, sol, tokens=1_000_000, user=W, slot=None):
    """One trade of ours on the tape (our own recorder, seconds behind the chain): `sol` paid on a buy (fee included) or
    received on a sell (fee taken), as the measure stage would write it into `fills` at its next run."""
    fee = 2_000_000
    quote = round(sol * 1e9) - fee if buy else round(sol * 1e9) + fee
    store.add_trades([Trade(sig=sig, ev=0, slot=slot or int(ts) - DAY0, pos=0, ts=int(ts), mint=mint, venue="curve",
                            user=user, is_buy=buy, quote=quote, base=int(tokens), fee=fee, fee_bps=100, q_after=1,
                            b_after=1)])  # fmt: skip


def _measured(store, ts):
    """The measure stage's last run (its cursor): the fills hold the tape's trades of ours up to about then."""
    store.set_coverage("measure:last_ts", {"ts": int(ts)})


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


def test_a_coin_sold_before_the_measure_stage_read_the_sale_still_counts(env):
    """The cycler re-arms seconds after a sale, the measure stage writes the sale up to 5 minutes later. The coin's loss
    counted while it was held must not vanish in between: the sale is read from the tape (what came back, less a
    transaction's fees) against what the buy cost."""
    _paths, store, _cfg = env
    _measured(store, NOW - 120)
    _sell(store, "s0", NOW - 3000, M2, pnl=-0.85)
    _buy(store, "b1", NOW - 600, M1)  # 0.2 SOL and 0.003005 of fees
    held = RG.day_result(store, W, NOW, [_coin(M1, 0.02)], per_tx_sol=0.0035)
    assert held["total_sol"] == pytest.approx(-0.85 + 0.02 - 0.203005, abs=1e-6)  # while held: marked down
    _tape(store, "s1", NOW - 30, M1, buy=False, sol=0.02)  # sold; the measure stage has not run since
    sold = RG.day_result(store, W, NOW, [], per_tx_sol=0.0035)
    assert sold["total_sol"] == pytest.approx(-0.85 + 0.02 - 0.0035 - 0.203005, abs=1e-6)
    assert sold["realised_sol"] == pytest.approx(sold["total_sol"]) and sold["unread_sol"] < -0.18


def test_a_coin_gone_from_the_wallet_with_no_sale_seen_counts_its_whole_cost(env):
    """Bought today, no longer in the wallet, and no sale of it on the tape (sold where the recorder does not look, or
    rugged to dust): its whole cost counts as lost until the measure stage says otherwise. One bought on an earlier day
    is not today's result; one bought after the wallet was read may simply not be in that read yet."""
    _paths, store, _cfg = env
    _measured(store, NOW - 120)
    _buy(store, "b1", NOW - 600, M1)
    _buy(store, "b0", DAY0 - 3600, M2)  # yesterday's
    day = RG.day_result(store, W, NOW, [], per_tx_sol=0.0035)
    assert day["total_sol"] == pytest.approx(-0.203005, abs=1e-6)
    snap_at = NOW - 900  # the last round read the wallet before b1 was bought: b1 is not judged on that read
    assert RG.day_result(store, W, NOW, [], per_tx_sol=0.0035, held_ts=snap_at)["total_sol"] == 0.0


def test_a_round_trip_the_measure_stage_has_not_read_yet_counts_from_the_tape(env):
    """A buy and its sale minutes ago, both on the tape and neither in the fills yet: what went out and what came back,
    with a transaction's fees on each, as the measure stage will count it."""
    _paths, store, _cfg = env
    _measured(store, NOW - 300)
    _tape(store, "b1", NOW - 200, M1, buy=True, sol=0.202)
    _tape(store, "s1", NOW - 100, M1, buy=False, sol=0.05)
    day = RG.day_result(store, W, NOW, [], per_tx_sol=0.0035)
    assert day["total_sol"] == pytest.approx(0.05 - 0.202 - 2 * 0.0035, abs=1e-6)
    _tape(store, "b2", NOW - 90, M2, buy=True, sol=0.202)  # still held, at the tape's price
    day = RG.day_result(store, W, NOW, [_coin(M2, 0.15)], per_tx_sol=0.0035)
    assert day["open_sol"] == pytest.approx(0.15 - 0.202 - 0.0035, abs=1e-6)


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


def test_a_stalled_measure_stage_is_never_read_as_a_clear_day(env):
    """The day stop rests on the measure stage's fills. When it has not run for 40 minutes (its timer runs it every 5,
    the round's stand-in every 15) and the wallet traded today since, the guard cannot tell the day; a day the tape alone
    shows past the stop still trips. A stalled stage with nothing traded since it last ran says nothing new."""
    paths, store, cfg = env
    _measured(store, NOW - 4 * 3600)
    _sell(store, "s-old", NOW - 4 * 3600 - 60, M2, pnl=-0.3)  # measured before it stalled
    g = RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[])
    assert g.known and not g.tripped  # nothing traded since: the fills hold the whole day
    _tape(store, "b1", NOW - 3600, M1, buy=True, sol=0.202)
    _tape(store, "s1", NOW - 3500, M1, buy=False, sol=0.21)
    g = RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[])
    assert not g.known and not g.tripped and "measure stage" in g.reason and "06:00" in g.reason
    for k in range(7):  # 7 losing round trips since: the tape alone shows the day past the stop
        _tape(store, f"b{k + 2}", NOW - 3000 + 60 * k, f"M{k}", buy=True, sol=0.202)
        _tape(store, f"s{k + 2}", NOW - 2970 + 60 * k, f"M{k}", buy=False, sol=0.0)
    g = RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[])
    assert g.tripped and g.known and g.kinds == ("day",)
    _measured(store, NOW - 120)  # it runs again (here: without having written the fills yet)
    assert RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[]).tripped


def test_a_measure_stage_that_never_ran_cannot_vouch_for_a_wallet_that_traded(env):
    paths, store, cfg = env
    assert RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[]).known  # a fresh install that has traded nothing
    _tape(store, "b1", NOW - 600, M1, buy=True, sol=0.202)
    _tape(store, "s1", NOW - 500, M1, buy=False, sol=0.25)
    g = RG.evaluate(store, cfg, paths, NOW, sol=11.0, held=[])
    assert not g.known and "never" in g.reason


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
    # what it does not stop, said as it is: the book cannot tell which of its snipers holds a coin, so while the wallet
    # holds one every book sniper keeps running (a stopped sniper strands its coin), and each can still buy to its cap
    assert "while it holds any coin every book sniper keeps running" in line and "can still buy" in line
    assert "/sell" in line and "stops its snipers that hold nothing" not in line
    assert "[decision] wallet-wide guard tripped" in paths.notes.read_text()
    _sell(store, "s1", NOW - 600, M2, pnl=-1.0)  # the day stop as well: that one is news
    assert RG.announce(RG.evaluate(store, cfg, paths, NOW + 60, sol=9.0, held=[]), notes, notifier, NOW + 60) is True
    assert len(sent) == 2 and "00:00 UTC" in sent[1]
    tomorrow = NOW + 86400
    assert RG.announce(RG.evaluate(store, cfg, paths, tomorrow, sol=9.0, held=[]), notes, notifier, tomorrow) is True


def test_a_trip_that_stops_nothing_is_noted_and_paged_the_first_time_it_matters(env):
    """With nothing that would trade (the book off, no test, no automatic test) the trip is a note, not a page every
    day; the owner still hears it the first time it matters that day."""
    paths, store, cfg = env
    sent: list[str] = []
    notes, notifier = NoteSink(paths, now=lambda: NOW), Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))
    floor = RG.evaluate(store, cfg, paths, NOW, sol=9.0, held=[])
    assert RG.announce(floor, notes, notifier, NOW, page=False) is True and sent == []
    assert RG.announce(floor, notes, notifier, NOW + 300, page=False) is False  # noted once
    assert "[info] wallet-wide guard tripped" in paths.notes.read_text()
    assert RG.announce(floor, notes, notifier, NOW + 600, page=True) is True and len(sent) == 1
    assert RG.announce(floor, notes, notifier, NOW + 900, page=False) is False  # told: not noted again either


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


# -- /risk in Telegram, `operate risk`, and nobody else ---------------------------------------------------------------


def _risk(env, *words, at=NOW, date=None, chat=7):
    from snipetech.operator.commands import Command, handle_command

    paths, store, cfg = env
    sent: list[str] = []

    class Svc:
        pass

    svc = Svc()
    svc.store = store
    snap = Snapshot(ts=at - 300, wallet=W, native_sol=11.0, wsol=0.2, held=[], snipers=[])
    cmd = Command(name="risk", args=list(words), chat_id=chat, update_id=1, date=at - 5 if date is None else date)
    reply = asyncio.run(handle_command(cmd, svc=svc, cfg=cfg, paths=paths, snap=snap, notes=NoteSink(paths, now=lambda: at),
                                       notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])), now=at))  # fmt: skip
    assert sent == [reply]
    return reply


def test_risk_shows_the_guard_and_todays_result(env):
    _paths, store, _cfg = env
    _sell(store, "s1", NOW - 600, M2, pnl=-0.32)
    reply = _risk(env)
    assert reply.startswith("risk guard: no new live trade once the day is 1 SOL down or the wallet is under 10 SOL")
    assert "today -0.32 SOL" in reply and "wallet 11.20 SOL" in reply and "7 Nov plan" in reply


def test_risk_stop_floor_and_default_change_only_what_they_name(env):
    paths, _store, _cfg = env
    reply = _risk(env, "stop", "1.5")
    assert reply.startswith("risk guard set:") and "1.5 SOL down" in reply and "your setting" in reply
    assert RG.limits(paths) == {"day_stop_sol": 1.5, "floor_sol": 10.0}
    assert json.loads(RG.risk_file(paths).read_text())["by"] == "telegram"
    _risk(env, "floor", "9")
    assert RG.limits(paths) == {"day_stop_sol": 1.5, "floor_sol": 9.0}
    assert "owner set the wallet-wide guard" in paths.notes.read_text()  # a decision note says who changed it
    reply = _risk(env, "default")
    assert "back to the 7 Nov plan's values" in reply and not RG.risk_file(paths).exists()


def test_a_risk_the_guard_does_not_understand_changes_nothing_and_repeats_nothing(env):
    paths, _store, _cfg = env
    secret = "4" * 30 + "PastedSecretKeyMaterialThatMustNeverBeRepeatedAnywhere0000000000"
    for words in (("stop", "9"), ("floor", "-1"), ("stop",), ("floor", secret), (secret,), ("stop", "1", "floor", "2"),
                  ("off",), ("default", "now")):  # fmt: skip
        reply = _risk(env, *words)
        assert reply.startswith("risk guard not changed"), words
        assert secret not in reply and "/risk stop" in reply, words
    assert not RG.risk_file(paths).exists()
    assert secret not in (paths.notes.read_text() if paths.notes.exists() else "")
    assert "0.1 to 5 SOL" in _risk(env, "stop", "9") and "0 to 1000 SOL" in _risk(env, "floor", "-1")


def test_a_risk_change_that_waited_in_telegram_is_refused(env):
    """As for /sell and /test go: a change older than 5 minutes waited out an outage; the owner sends it again."""
    paths, _store, _cfg = env
    reply = _risk(env, "floor", "0", date=NOW - 600)
    assert "10 min old" in reply and not RG.risk_file(paths).exists()
    assert _risk(env, date=NOW - 600).startswith("risk guard:")  # showing it is never refused


def test_only_the_owners_risk_is_obeyed(env):
    from snipetech.operator.run import commands_pass

    paths, store, cfg = env
    paths.telegram.write_text(json.dumps({"chat_id": 7}))

    class Svc:
        pass

    svc = Svc()
    svc.store = store
    sent: list[str] = []

    def run(chat, uid):
        msg = {"update_id": uid, "message": {"chat": {"id": chat}, "text": "/risk floor 0", "date": NOW}}
        asyncio.run(commands_pass(cfg, svc, now=lambda: NOW, send=lambda t: sent.append(t) or True,
                                  commands_get=lambda url, params: {"result": [msg]}))  # fmt: skip

    run(9, 5)
    assert not RG.risk_file(paths).exists() and sent == []
    run(7, 6)
    assert RG.limits(paths)["floor_sol"] == 0.0 and any(t.startswith("risk guard set") for t in sent)


def test_help_lists_risk():
    from snipetech.operator.commands import HELP

    assert "/risk" in HELP


def test_operate_risk_sets_shows_and_clears_the_owners_file(env, capsys):
    from snipetech.cli import build_parser

    paths, _store, cfg = env
    p = build_parser()

    def run(*argv):
        return RG.risk_cli(cfg, p.parse_args(["operate", *argv]), now=NOW)

    assert run("risk") == 0 and "none: the 7 Nov plan's values apply" in capsys.readouterr().out
    assert run("risk", "--day-stop", "1.5", "--floor", "9") == 0
    assert RG.limits(paths) == {"day_stop_sol": 1.5, "floor_sol": 9.0}
    assert json.loads(RG.risk_file(paths).read_text())["by"] == "cli" and "1.5 SOL down" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="0.1 to 5 SOL"):
        run("risk", "--day-stop", "7")
    with pytest.raises(SystemExit, match="alone"):
        run("risk", "--default", "--floor", "2")
    with pytest.raises(SystemExit, match="go with `operate risk`"):
        run("--floor", "2")
    with pytest.raises(SystemExit, match="go with `operate budget`"):
        run("risk", "--daily-usd", "200")  # never silently dropped
    assert RG.limits(paths) == {"day_stop_sol": 1.5, "floor_sol": 9.0}
    assert run("risk", "--default") == 0 and not RG.risk_file(paths).exists()


def test_the_cli_routes_operate_risk_to_the_guard_and_budget_stays_the_budget(env, monkeypatch):
    from snipetech import cli

    _paths, _store, cfg = env
    seen: list[str] = []
    monkeypatch.setattr("snipetech.operator.risk_guard.risk_cli", lambda c, a, now=None: seen.append("risk") or 0)
    monkeypatch.setattr(
        "snipetech.operator.budget_switch.budget_cli", lambda c, a, now=None: seen.append("budget") or 0
    )
    db = cfg.storage.db_path
    assert cli.main(["--db", db, "operate", "risk", "--floor", "9"]) == 0
    assert cli.main(["--db", db, "operate", "risk", "--default"]) == 0
    assert cli.main(["--db", db, "operate", "budget", "--default"]) == 0
    assert cli.main(["--db", db, "operate", "--daily-usd", "20"]) == 0
    assert seen == ["risk", "risk", "budget", "budget"]


def test_no_agent_tool_can_write_the_guard(env):
    """Only the owner sets the guard: start_job refuses `operate` (the guard's CLI) and no agent tool writes a file
    without an extension in operator/."""
    from snipetech.operator.jobs import OperatorJobs
    from snipetech.operator.tools import make_operator_tools

    paths, store, cfg = env

    class Svc:
        pass

    svc = Svc()
    svc.store = store
    jobs = OperatorJobs(repo=paths.data, data=paths.data, db=paths.data / "snipetech.db", branch="main")
    started: list = []
    jobs.ops.op_run = lambda argv, timeout=0: started.append(argv) or {"id": "x"}
    tools = {t.name: t for t in make_operator_tools(svc, paths, jobs, cfg.operator, NoteSink(paths, now=lambda: NOW))}
    loose = json.dumps({"day_stop_sol": 5, "floor_sol": 0, "ts": NOW, "by": "telegram"})
    calls = [("start_job", {"argv": ["operate", "risk", "--floor", "0"]}),
             ("start_job", {"argv": ["operate", "risk", "--default"]}),
             ("write_report", {"name": "RISK", "text": loose}), ("write_report", {"name": "../operator/RISK", "text": loose}),
             ("write_report", {"name": "RISK.json", "text": loose}), ("set_candidates", {"items": [json.loads(loose)]}),
             ("write_note", {"text": loose, "level": "decision"}), ("add_lesson", {"text": "loosen the guard", "evidence": loose, "n": 1}),
             ("upsert_idea", {"title": "loosen the guard", "why": loose, "test": "x", "expected": "y", "kind": "config"})]  # fmt: skip
    for name, kw in calls:
        asyncio.run(tools[name].call(kw))
    assert not RG.risk_file(paths).exists() and RG.limits(paths) == {"day_stop_sol": 1.0, "floor_sol": 10.0}
    assert started == []
