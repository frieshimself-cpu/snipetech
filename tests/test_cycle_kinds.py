"""What kind of buy each copy of the run's was (the 22 Nov plan, step 3), and what the runner does about it.

OKTech copies more than the paper scores: a copied wallet's own coin launches (it signs the create and buys inside it:
38 of Cooker's 73 buys of 2 SOL or more on 15-21 Nov; a 1 SOL copy of one loses 0.11-0.23 SOL at 12 s, about 0.09 at
5 s), and creates that only name the wallet, signed by someone else (bait: twice in 5.7 armed hours behind Cupseyy).
Each copy is tagged as it reaches the tape:
- own launch: sold at once, never counted for the place stop, and its wallet's sniper rests 3 hours (his launches
  come in bursts: a rest after each copy would have let 11 of his 57 through instead of all 57);
- bait: sold at once and never counted; after 3 in one UTC day that wallet's sniper rests until the next day;
- repeat (the wallet had bought the coin before): only tagged;
- unseen (no buy of a copied wallet before ours on the tape): out of place, as before;
- first: a counted first buy.
"""

import itertools

from snipetech.models import Launch, Trade
from tests.test_cycle_floor import T0, _run
from tests.test_oktech import SNIPER_IDS, W6

A, B = SNIPER_IDS
COOKER = "8deJ9xeUvXSJwicYptA9mHsU2rN2pDx37KWzkDkEXhU6"  # a launcher (A copies him)
CUPSEYY = "6DQAGJT7VZPVBsuG4kn3AvpyHCEi7B2RFFvMZdbqQqqP"  # B copies him
BAITER = "Bait1111111111111111111111111111111111111111"
FRESH = "Fresh111111111111111111111111111111111111111"
FEES = {"priorityFee": 0.001, "tipAmount": 0.049}
_sigs = itertools.count()


def _payload(i):
    return FEES | {"ctbotWallets": [{"address": COOKER if i == A else CUPSEYY}]}


def _trade(ts, user, mint, sol, buy=True, priority=None, tip=None, pos=0):
    return Trade(sig=f"k{next(_sigs)}", ev=0, slot=100_000 + int(ts - T0), pos=pos, ts=int(ts), mint=mint,
                 venue="curve", user=user, is_buy=buy, quote=round(sol * 1e9), base=1_000_000, fee=0, fee_bps=100,
                 q_after=1, b_after=1, priority=priority, tip=tip)  # fmt: skip


def _ours(ts, mint, pos=5):
    return _trade(ts, W6, mint, 1.0, priority=1_000_000, tip=49_000_000, pos=pos)


def _launch(ts, mint, signer, creator):
    return Launch(mint=mint, user=signer, creator=creator, name="x", symbol="X", uri="", bonding_curve="BC" + mint[:6],
                  slot=100_000 + int(ts - T0), ts=int(ts), sig=f"create-{mint}")  # fmt: skip


def _coin(mint, value=1.0):
    return {"mint": mint, "amount": 1.0, "raw": 1_000_000, "value_sol": value, "priced": True}


def _starts(client, i, since):
    return [t for kind, j, t in client.timed if kind == "start" and j == i and t >= since]


def test_a_copy_of_the_wallets_own_launch_is_sold_at_once_and_its_sniper_rests(monkeypatch):
    """Cooker signs a create (under a fresh creator address, as 36 of his 61 were) and buys inside it; A's copy follows
    at 100 s. It is sold at the next reading, long before --sweep-after, never placed, and A is not re-armed for
    3 hours while B goes on."""
    sweeps = []
    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0,
                       coins=lambda now: [_coin("L1")] if T0 + 102 <= now and not sweeps else [],
                       payload=_payload, launches=[_launch(T0 + 98, "L1", COOKER, FRESH)],
                       tape=[_trade(T0 + 98, COOKER, "L1", 5.0, pos=1), _ours(T0 + 100, "L1")], sweeps=sweeps,
                       sweep_after=60, rearm_after=60, place_stop=(2, 3), place_max=2, minutes=10)  # fmt: skip
    assert [m for m, _t in sweeps] == ["L1"] and sweeps[0][1] < T0 + 115
    assert rep["places"] == [] and "place stop" not in rep["reason"]
    assert not _starts(client, A, T0 + 101) and _starts(client, B, T0 + 101)
    assert [c["kind"] for c in rep["copies"]] == ["own launch"]
    assert rep["rests"][A] >= T0 + 100 + 3 * 3600


def test_bait_is_sold_at_once_and_three_in_a_day_rest_the_sniper(monkeypatch):
    """Creates naming Cupseyy as their creator, signed by someone else, with no buy of his: B's copies of them are sold
    at once and never counted for the place stop (with a stop at 1 of 1 the first would have ended the run). After the
    third of the day B rests until 00:00 UTC."""
    sweeps = []
    mints = ["B1", "B2", "B3"]

    def coins(now):
        out = []
        for k, m in enumerate(mints):
            bought = T0 + 100 + 120 * k
            if now >= bought + 2 and m not in [x for x, _t in sweeps]:
                out.append(_coin(m))
        return out

    tape = [x for k, m in enumerate(mints) for x in (_ours(T0 + 100 + 120 * k, m),)]
    launches = [_launch(T0 + 99 + 120 * k, m, BAITER, CUPSEYY) for k, m in enumerate(mints)]
    rep, client = _run(monkeypatch, lambda now: 10.0, coins=coins, payload=_payload, launches=launches, tape=tape,
                       sweeps=sweeps, sweep_after=60, rearm_after=30, place_stop=(1, 1), place_max=2,
                       spent=lambda i, now, last: i == B and any(0 <= now - (T0 + 100 + 120 * k) < 30 for k in range(3)),
                       minutes=12)  # fmt: skip
    assert [m for m, _t in sweeps] == mints and all(t - (T0 + 100 + 120 * k) < 15 for k, (_m, t) in enumerate(sweeps))
    assert rep["places"] == [] and "place stop" not in rep["reason"]
    assert [c["kind"] for c in rep["copies"]] == ["bait"] * 3
    assert not _starts(client, B, T0 + 100 + 240 + 1)
    assert rep["rests"][B] == (int(T0 // 86400) + 1) * 86400


def test_a_repeat_is_only_tagged_and_a_first_buy_counted(monkeypatch):
    """Cupseyy bought R1 at 50 s and again at 99 s; B's copy of the second buy is a repeat: tagged, placed, sold by its
    own exit or the sweep as before, and nobody rests."""
    sweeps = []
    rep, _client = _run(monkeypatch, lambda now: 10.0, coins=lambda now: [_coin("R1")] if T0 + 102 <= now < T0 + 130 else [],
                       payload=_payload,
                       tape=[_trade(T0 + 50, CUPSEYY, "R1", 2.0), _trade(T0 + 99, CUPSEYY, "R1", 2.0, pos=1),
                             _ours(T0 + 100, "R1")],
                       sweeps=sweeps, sweep_after=60, place_stop=(2, 3), place_max=2, minutes=5)  # fmt: skip
    assert sweeps == [] and [c["kind"] for c in rep["copies"]] == ["repeat"]
    assert [p["kind"] for p in rep["places"]] == ["repeat"] and rep["rests"] == {}


def test_a_copy_of_another_wallets_first_buy_in_a_launchers_coin_is_no_own_launch_copy(monkeypatch):
    """Audit of 22 Nov, runner finding 6: the kinds looked at who signed the coin's create before which copied buy ours
    followed. Cooker launched L1 and bought in it; B copied Cupseyy's first buy of L1, and it was taken for a copy of
    Cooker's own launch: sold at once (not at the exit the paper scored), never placed, and Cooker's sniper A rested
    3 hours although it bought nothing."""
    sweeps = []
    first = _trade(T0 + 99, CUPSEYY, "L1", 4.0, pos=1)  # Cupseyy's first buy of the coin Cooker launched at 40 s
    tape = [_trade(T0 + 40, COOKER, "L1", 3.0, pos=1), first, _ours(T0 + 100, "L1")]
    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0,
                        coins=lambda now: [_coin("L1")] if T0 + 102 <= now < T0 + 112 else [], payload=_payload,
                        launches=[_launch(T0 + 40, "L1", COOKER, FRESH)], tape=tape, sweeps=sweeps, sweep_after=60,
                        rearm_after=60, minutes=5)  # fmt: skip
    assert [(c["kind"], c["wallet"]) for c in rep["copies"]] == [("first", CUPSEYY)]
    assert sweeps == [] and A not in rep["rests"]


def test_a_copy_of_another_wallets_buy_in_a_coin_naming_a_copied_wallet_is_no_bait(monkeypatch):
    """Runner finding 6, the named creator: a create naming Cooker, signed by someone else, that Cupseyy then bought;
    B's copy of Cupseyy's buy was bait (Cooker bought nothing) and sold at once. Bait is a copy with no copied buy
    before it."""
    sweeps = []
    rep, _client = _run(monkeypatch, lambda now: 10.0, coins=lambda now: [_coin("N1")] if T0 + 102 <= now < T0 + 112 else [],
                        payload=_payload, launches=[_launch(T0 + 90, "N1", BAITER, COOKER)],
                        tape=[_trade(T0 + 99, CUPSEYY, "N1", 4.0, pos=1), _ours(T0 + 100, "N1")], sweeps=sweeps,
                        sweep_after=60, minutes=5)  # fmt: skip
    assert [(c["kind"], c["wallet"]) for c in rep["copies"]] == [("first", CUPSEYY)] and sweeps == []


def test_a_copy_after_a_launchers_create_with_no_copied_buy_is_still_an_own_launch_copy(monkeypatch):
    """A copied wallet's create with no buy of its own (Cented's empty launches) and ours right after it: no copied buy
    before ours, so the launcher's create is what ours followed, an own launch, as before."""
    rep, _client = _run(monkeypatch, lambda now: 10.0, coins=lambda now: [_coin("E1")] if T0 + 102 <= now < T0 + 112 else [],
                        payload=_payload, launches=[_launch(T0 + 99, "E1", COOKER, FRESH)], tape=[_ours(T0 + 100, "E1")],
                        minutes=5)  # fmt: skip
    assert [(c["kind"], c["wallet"]) for c in rep["copies"]] == [("own launch", COOKER)]


def test_a_rest_an_earlier_run_left_holds_at_the_start(monkeypatch):
    """A run restarted inside the 3 hours does not arm the resting sniper; the other one starts as usual."""

    def before(store):
        store.set_coverage("runner:rests", {A: T0 + 3600, B: T0 - 60})  # A rests another hour; B's rest is over

    rep, client = _run(monkeypatch, lambda now: 10.0, payload=_payload, before=before, rearm_after=60, minutes=3)
    assert not _starts(client, A, T0) and _starts(client, B, T0)
    assert any("rests until" in e for e in rep["events"])


def test_with_another_wallets_selling_key_a_launch_copy_is_left_to_its_sniper(monkeypatch):
    """The run's own sale signs for the selling key's wallet: with that key another wallet's, a copy of an own launch
    is not sold by the run (it would sell that wallet's coin) but left to its sniper's own exit, and said so. The rest
    holds all the same."""
    sweeps = []
    rep, _client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0,
                        coins=lambda now: [_coin("L1")] if T0 + 102 <= now < T0 + 130 else [],
                        payload=_payload, launches=[_launch(T0 + 98, "L1", COOKER, FRESH)],
                        tape=[_trade(T0 + 98, COOKER, "L1", 5.0, pos=1), _ours(T0 + 100, "L1")], sweeps=sweeps,
                        rearm_after=60, minutes=5, seller="Other111111111111111111111111111111111111111")  # fmt: skip
    assert sweeps == [] and [c["kind"] for c in rep["copies"]] == ["own launch"]
    assert any("own exit" in e for e in rep["events"])
    assert rep["rests"][A] >= T0 + 100 + 3 * 3600
