"""The runner stops re-arming once our copies land too far back in line (21 Nov 2026).

The 5 SOL recipe pays in the first two places behind the copied wallet and loses behind its third buyer
(docs/roadmap.md: at 2nd in line Cented +0.79 and Decu +0.61 a copy, behind the 3rd buy -0.26 and -0.33). The 1 SOL
day kept "2 of the first 3 fills further back than the paper's place: no more re-arms" by hand. `oktech cycle
--place-stop 2/3 --place-max 1` keeps it by itself: each of our buys is placed on the tape the way the measure stage
places it (`measure.classify.classify_trade`: the copied wallet's companions and its own pack are no buyers ahead),
and a buy with no buy of a copied wallet before it (create-bait, 21 Nov 04:07 and 04:25) is out of place too."""

import asyncio
from dataclasses import replace

import pytest

from snipetech.models import Trade
from tests.test_oktech import SNIPER_IDS, W6, _cycle_svc, _SpentClient

T0 = 1_794_045_600.0
LEAD = "Lead1111111111111111111111111111111111111111"
MATE = "Mate1111111111111111111111111111111111111111"


def _buy(sig, slot, pos, ts, mint, user, sol=1.0):
    return Trade(sig=sig, ev=0, slot=slot, pos=pos, ts=int(ts), mint=mint, venue="curve", user=user, is_buy=True,
                 quote=round(sol * 1e9), base=1_000_000, fee=10_000_000, fee_bps=100, q_after=40 * 10**9,
                 b_after=900 * 10**15)  # fmt: skip


def _place_run(monkeypatch, fills, *, copies=True, mates=None, coin_value=5.0, **kw):
    """The cycler over two copy snipers of LEAD on the fake clock. Each fill (at secs after the start, the buyers
    between LEAD's buy and ours, whether LEAD bought at all, the companions among those buyers) puts its block on the
    tape when the clock reaches it, and the coin is held 12 s (its timer exit) from then."""
    clock = {"t": T0}
    sched = []  # (time, trades) still to reach the tape
    for k, (at, ahead, leader, mates_ahead) in enumerate(fills):
        mint, slot, rows = f"M{k}", 2_000 + 10 * k, []
        if leader:
            rows.append(_buy(f"lead{k}", slot, 0, T0 + at, mint, LEAD, sol=5.0))
        for j in range(ahead):
            who = MATE if j < mates_ahead else f"Other{k}x{j}"
            rows.append(_buy(f"ahead{k}x{j}", slot, 1 + j, T0 + at, mint, who))
        rows.append(_buy(f"ours{k}", slot, 1 + ahead, T0 + at, mint, W6, sol=5.0))
        sched.append((T0 + at, rows))
        # its timer's sale 12 s on, on the tape as the recorder writes it (the run's own trades count it)
        sale = replace(_buy(f"sold{k}", slot + 30, 0, T0 + at + 12, mint, W6, sol=5.0), is_buy=False)
        sched.append((T0 + at + 12, [sale]))
    box: dict = {}

    def on_sleep(_secs):
        for item in [s for s in sched if s[0] <= clock["t"]]:
            sched.remove(item)
            box["store"].add_trades(item[1])

    def coins(now):
        return [{"mint": f"M{k}", "amount": 1.0, "value_sol": coin_value, "priced": True}
                for k, (at, *_rest) in enumerate(fills) if T0 + at <= now < T0 + at + 12]  # fmt: skip

    client = _SpentClient(clock, SNIPER_IDS, lambda i, now, last_start: False)
    svc, store = _cycle_svc(monkeypatch, client, {"stop": False}, clock=clock, coins=coins, on_sleep=on_sleep)
    box["store"] = store
    wallets = [{"address": LEAD, "minSol": 0.95}] if copies else []
    for i in SNIPER_IDS:
        payload = {"singleSnipe": True, "totalSnipes": 1, **({"ctbotWallets": wallets} if copies else {})}
        store.add_oktech_sniper({"id": i, "kind": "copy" if copies else "strategy", "source": "x", "payload": payload})
    monkeypatch.setattr("snipetech.measure.chain_landing.known_mates",
                        lambda tape, leader, since: frozenset(mates) if mates and leader == LEAD else None)  # fmt: skip
    rep = asyncio.run(svc.oktech_cycle(SNIPER_IDS, W6, 1.0, kw.pop("minutes", 30), rearm_after=1800, **kw))
    return rep, client, store


def test_two_of_three_copies_too_far_back_stop_the_rearms(monkeypatch):
    # 3 buyers ahead (out), first behind it (in), 2 ahead (out): the second miss of three ends the re-arms
    fills = [(100, 3, True, 0), (300, 0, True, 0), (500, 2, True, 0), (700, 0, True, 0)]
    rep, client, store = _place_run(monkeypatch, fills, place_stop=(2, 3), place_max=1)
    assert [p["trades_behind"] for p in rep["places"]] == [3, 0, 2]
    assert [p["bad"] for p in rep["places"]] == [True, False, True]
    assert "place stop" in rep["reason"] and "2 of the last 3" in rep["reason"]
    last_sale = T0 + 500 + 12
    assert not [t for kind, _i, t in client.timed if kind == "start" and t > last_sale]  # no re-arm after it
    assert rep["left_running"] == []  # nothing held at the end: both stopped
    lines = [e for e in rep["events"] if "behind" in e and "copy" in e]
    assert len(lines) == 3 and "M0" in lines[0] and "3" in lines[0]
    store.close()


def test_without_the_flag_nothing_is_placed_and_the_run_goes_on(monkeypatch):
    fills = [(100, 3, True, 0), (300, 3, True, 0), (500, 3, True, 0)]
    rep, _client, store = _place_run(monkeypatch, fills, minutes=12)
    assert rep["places"] == [] and rep["reason"].startswith("time limit")
    store.close()


def test_a_copy_no_copied_wallet_bought_before_is_out_of_place(monkeypatch):
    """Create-bait: a coin made with the copied wallet's address in its create, bought by our copy sniper, with no buy
    of the wallet on the tape before ours (21 Nov 04:07:57 and 04:25:22). Out of place, and said so."""
    fills = [(100, 0, False, 0), (300, 0, False, 0)]
    rep, _client, store = _place_run(monkeypatch, fills, place_stop=(2, 3), place_max=1)
    assert [p["leader"] for p in rep["places"]] == [None, None] and all(p["bad"] for p in rep["places"])
    assert "place stop" in rep["reason"]
    assert any("no buy of a copied wallet" in e for e in rep["events"])
    store.close()


def test_a_copy_of_the_copied_wallets_own_coin_with_no_first_buy_is_no_miss(monkeypatch):
    """Cented created 38 coins from 7 to 21 Nov, 17 of them with no dev buy (no buy at all in their first minute).
    A copy sniper that buys such a create has no copied buy before it, but that is no place: the copied wallet signed
    the create itself. A create signed by anybody else that puts the copied wallet in the coin's creator field is
    create-bait. Since the kinds of copy (22 Nov plan, step 3) neither counts for the place stop: both are sold at
    once, an own launch rests its wallet's sniper 3 hours, and bait has its own limit (3 a day)."""
    from snipetech.models import Launch

    fills = [(100, 0, False, 0), (300, 0, False, 0), (500, 0, False, 0)]
    sigs = {"M0": "lead-create-0", "M1": "lead-create-1", "M2": "bait-create-2"}
    signers = {"M0": LEAD, "M1": LEAD, "M2": "BaitDev1111111111111111111111111111111111111"}
    real = _place_run.__globals__["_cycle_svc"]

    def wrapped(*a, **k):
        svc, store = real(*a, **k)
        store.add_launches([Launch(mint=m, user=signers[m], creator=LEAD, name="x", symbol="x", uri="", bonding_curve="",
                                   slot=2_000 + 10 * k, ts=int(T0 + at), sig=sigs[m])
                            for k, (m, at) in enumerate(zip(sigs, (100, 300, 500), strict=True))])  # fmt: skip
        return svc, store

    monkeypatch.setitem(_place_run.__globals__, "_cycle_svc", wrapped)
    rep, _client, store = _place_run(monkeypatch, fills, place_stop=(2, 3), place_max=1, minutes=12)
    assert rep["places"] == [] and [c["kind"] for c in rep["copies"]] == ["own launch", "own launch", "bait"]
    assert rep["reason"].startswith("time limit")  # nothing counted against the place rule: it holds
    assert sum("own launch" in e for e in rep["events"]) == 2
    store.close()


def test_the_copied_wallets_companions_are_no_buyers_ahead(monkeypatch):
    """Rule 2 of CLAUDE.md: a copied wallet's companions are never buyers ahead of us. Two of the three buys between
    LEAD's and ours are its companion's: one outsider ahead, in place at --place-max 1."""
    fills = [(100, 3, True, 2), (300, 3, True, 2), (500, 3, True, 2)]
    rep, _client, store = _place_run(monkeypatch, fills, mates={MATE}, place_stop=(2, 3), place_max=1, minutes=12)
    assert [p["trades_behind"] for p in rep["places"]] == [1, 1, 1] and not any(p["bad"] for p in rep["places"])
    assert rep["reason"].startswith("time limit")
    store.close()


def test_the_place_stop_needs_copy_snipers_and_a_sane_rule(monkeypatch):
    """A hunt sniper copies nobody, so there is no place to read: refused before any sniper is touched. A rule asking
    for more misses than fills it looks at is refused too."""
    with pytest.raises(SystemExit, match="copy"):
        _place_run(monkeypatch, [], copies=False, place_stop=(2, 3), place_max=1)
    with pytest.raises(SystemExit, match="place"):
        _place_run(monkeypatch, [], place_stop=(4, 3), place_max=1)


def test_a_duplicate_buy_in_the_same_block_is_placed_once(monkeypatch):
    """OKTech sends a buy more than once; when two of them land in the same block they are one copy, not two misses."""
    from dataclasses import replace

    fills = [(100, 3, True, 0)]

    def twice(store_add):
        def add(rows):
            ours = [r for r in rows if r.user == W6]
            dupes = [replace(r, sig=r.sig + "dup", pos=r.pos + 1) for r in ours]
            store_add(rows + dupes)

        return add

    real = _place_run.__globals__["_cycle_svc"]

    def wrapped(*a, **k):
        svc, store = real(*a, **k)
        store.add_trades = twice(store.add_trades)
        return svc, store

    monkeypatch.setitem(_place_run.__globals__, "_cycle_svc", wrapped)
    # both buys landed: the wallet holds both buys' tokens, worth twice one copy
    rep, _client, store = _place_run(monkeypatch, fills, place_stop=(2, 3), place_max=1, minutes=4, coin_value=10.0)
    assert len(rep["places"]) == 1 and rep["places"][0]["trades_behind"] == 3
    assert rep["reason"].startswith("time limit")  # one miss, not two: the rule has not tripped
    store.close()


def test_a_run_mixing_a_copy_sniper_with_one_that_copies_nobody_is_refused(monkeypatch):
    """A hunt or strategy sniper's buy has no copied buy before it and would read as create-bait: --place-stop runs
    only over copy snipers, and says which sniper is not one."""
    from tests.test_oktech import _CycleClient

    clock = {"t": T0}
    svc, store = _cycle_svc(monkeypatch, _CycleClient(), {"stop": False}, clock=clock)
    store.add_oktech_sniper({"id": SNIPER_IDS[0], "kind": "copy", "source": "x",
                             "payload": {"singleSnipe": True, "ctbotWallets": [{"address": LEAD}]}})  # fmt: skip
    store.add_oktech_sniper({"id": SNIPER_IDS[1], "kind": "strategy", "source": "x", "payload": {"singleSnipe": True}})
    with pytest.raises(SystemExit, match=SNIPER_IDS[1][-6:]):
        asyncio.run(svc.oktech_cycle(SNIPER_IDS, W6, 1.0, 5, place_stop=(2, 3), place_max=1))
    store.close()


def test_an_error_in_the_place_reading_never_ends_the_run_unwatched(monkeypatch):
    """The runner must always reach its end, where it stops the snipers it armed when nothing is held: a fault in the
    place reading is said once, the rule reads nothing, and the run goes on to its own end."""
    import snipetech.measure.classify as C

    def broken(*a, **k):
        raise KeyError("boom")

    monkeypatch.setattr(C, "classify_trade", broken)
    fills = [(100, 3, True, 0), (300, 3, True, 0)]
    rep, _client, store = _place_run(monkeypatch, fills, place_stop=(2, 3), place_max=1, minutes=8)
    assert rep["places"] == [] and rep["reason"].startswith("time limit") and rep["left_running"] == []
    assert sum("could not be placed" in e for e in rep["events"]) == 2  # each buy said once, never every tick
    store.close()


def test_a_fault_outside_the_place_reader_is_said_once_and_the_run_still_ends_disarmed(monkeypatch):
    """Not only the place reader: anything in the place rule that fails is caught by the cycler, said once, and the
    rule switched off for the rest of the run, which goes on to its own end and stops the snipers it armed."""
    from types import SimpleNamespace

    from snipetech.store import Store

    real = Store.trades_for_user
    odd = [SimpleNamespace(is_buy=True)]  # a row the rule cannot read: no signature, no time
    monkeypatch.setattr(Store, "trades_for_user",
                        lambda self, user, since_ts=0, fees=False: odd if user == W6 else real(self, user, since_ts,
                                                                                               fees))  # fmt: skip
    rep, _client, store = _place_run(monkeypatch, [], place_stop=(2, 3), place_max=1, minutes=4)
    assert rep["reason"].startswith("time limit") and rep["left_running"] == [] and rep["places"] == []
    assert sum("place rule" in e and "off" in e for e in rep["events"]) == 1
    store.close()


def test_cycle_cli_hands_the_place_stop_to_the_cycler(tmp_path, monkeypatch, capsys):
    from snipetech import cli
    from snipetech.service import Service

    seen: list[dict] = []

    async def cycle(self, ids, address, floor_sol, max_minutes, max_fills=10, rearm_after=60.0, ignore_mints=None,
                    **kw):  # fmt: skip
        seen.append(kw)
        return {"events": ["done"]}

    monkeypatch.setattr(Service, "oktech_cycle", cycle)
    base = ["--db", str(tmp_path / "snipetech.db"), "oktech", "cycle", "--id", "a", "--address", W6, "--floor", "0.1"]
    assert cli.main([*base, "--place-stop", "2/3", "--place-max", "1"]) == 0
    assert cli.main(base) == 0
    assert seen[0]["place_stop"] == (2, 3) and seen[0]["place_max"] == 1
    assert seen[1]["place_stop"] is None
    for bad in ("3", "x/3", "0/3", "4/3"):
        with pytest.raises(SystemExit):
            cli.main([*base, "--place-stop", bad])
    capsys.readouterr()
