"""The runner's floor on the owner's 10 SOL day (21 Nov 2026): 10 SOL in the wallet, copies of 5 SOL, 5 SOL max loss.

With a 5 SOL copy the wallet's SOL falls under a floor of 5 at every buy, while the coin bought is worth what was paid.
Two readings made that look like a loss: the SOL balance (read at "processed") shows the buy a second before the
wallet's token account (read at "confirmed") shows the coin, and a coin bought seconds ago may not be priced on the
tape yet (positions.py: "counts for nothing in equity until the tape prices it"; callers must never call an emergency
on it). Either ended the day at its first copy. So a reading under the floor holds the re-arms back and ends the run
only when the next reading agrees, an unpriced coin is no reading at all, and wrapped SOL is the owner's SOL too.

`--max-loss` puts the floor at the wallet at the start (SOL and wrapped SOL) less the owner's number, and
`--wait-for-sol` arms nothing until the wallet holds that much: the owner funds the wallet when the copied wallets
trade, and the run starts then, not when a session next looks."""

import asyncio
import itertools

import pytest

from tests.test_oktech import SNIPER_IDS, W6, _cycle_svc, _SpentClient

T0 = 1_794_045_600.0


def _coin(value=5.0):
    return [{"mint": "M1", "amount": 1.0, "value_sol": value, "priced": True}]


def _run(monkeypatch, sol, *, coins=None, unpriced=None, wsol=None, floor=5.0, minutes=5, stop_at=None, sweeps=None,
         spent=None, sweep_fails=False, payload=None, tape=(), db_dir=None, others=(), **kw):  # fmt: skip
    """The runner over two copy snipers on the fake clock, with `sol(now)` SOL in the wallet, the coins `coins(now)`
    lists (priced) and `unpriced(now)` lists (not on the tape), `wsol(now)` wrapped SOL, and the owner's /stop from
    `stop_at` on. The run's own sales (`sweep_coin`) go to the list `sweeps` as (mint, time), and each fails when
    `sweep_fails`. OKTech reads a sniper "finished" (and refuses to stop it) while `spent(i, now, last_start)`.
    `payload`: more of each sniper's payload (the fees it pays, the wallets it copies). `tape`: trades the recorder
    writes once the clock reaches each one's time, or (time, trade) for one it writes late; with any, a stranger's
    trade each tick keeps the tape current. `db_dir`: the database's folder (the operator's files, the runner's lock);
    `others`: OKTech's other tasks, as (id, status)."""
    from snipetech.models import Trade

    clock, flag = {"t": T0}, {"stop": False}
    client = _SpentClient(clock, SNIPER_IDS, spent or (lambda i, now, last_start: False))
    due = sorted(((t.ts, t) if isinstance(t, Trade) else t for t in tape), key=lambda x: x[0])
    box, ticks = {}, itertools.count()

    def write_tape():
        store_ = box.get("store")
        if store_ is None or not tape:
            return
        while due and due[0][0] <= clock["t"]:
            store_.add_trades([due.pop(0)[1]])
        store_.add_trades([Trade(sig=f"stranger{next(ticks)}", ev=0, slot=500 + int(clock["t"] - T0), pos=9,
                                 ts=int(clock["t"]), mint="ElseMint", venue="curve", user="SomeoneElse", is_buy=True,
                                 quote=1, base=1, fee=0, fee_bps=0, q_after=1, b_after=1)])  # fmt: skip

    def on_sleep(_secs):
        if stop_at is not None and clock["t"] >= stop_at:
            flag["stop"] = True
        write_tape()

    svc, store = _cycle_svc(monkeypatch, client, flag, clock=clock, balance=lambda: sol(clock["t"]), coins=coins,
                            unpriced=unpriced, on_sleep=on_sleep)  # fmt: skip
    box["store"] = store
    if db_dir is not None:  # a database on disk: the operator's folder beside it holds the runner's lock
        svc.cfg.storage.db_path = str(db_dir / "snipetech.db")
        store.path = svc.cfg.storage.db_path
    if others:
        real_tasks = client.tasks

        async def tasks():
            return await real_tasks() + [{"id": i, "status": st} for i, st in others]

        client.tasks = tasks
    write_tape()
    for i in SNIPER_IDS:
        store.add_oktech_sniper({"id": i, "kind": "copy", "source": "x",
                                 "payload": {"singleSnipe": True, "totalSnipes": 1} | (payload or {})})  # fmt: skip

    async def wrapped(address):
        return wsol(clock["t"]) if wsol else 0.0

    async def sweep(mint):
        if sweeps is not None:
            sweeps.append((mint, clock["t"]))
        if sweep_fails:
            raise RuntimeError("the sale failed: slippage")
        return {"sold": mint}

    monkeypatch.setattr(svc, "wsol_balance", wrapped)
    monkeypatch.setattr(svc, "sweep_coin", sweep)
    try:
        rep = asyncio.run(svc.oktech_cycle(SNIPER_IDS, W6, floor, minutes, rearm_after=kw.pop("rearm_after", 1800),
                                           **kw))  # fmt: skip
    finally:
        store.close()
    return rep, client


def _bought_at_100(now):
    """10 SOL; a 5.2 SOL copy at 100 s (the SOL shows it at once), sold at 112 s for 5.15."""
    if now < T0 + 100:
        return 10.0
    return 4.8 if now < T0 + 112 else 9.95


def test_a_buy_the_sol_shows_before_the_coin_is_no_emergency(monkeypatch):
    """The balance (processed) drops 5.2 SOL at the buy; the coin (confirmed) shows a few seconds later. One reading
    in between saw 4.8 SOL and no coin, under the floor of 5: the run ended at its first copy."""

    def coins(now):  # the token account shows the coin 4 s after the SOL left
        return _coin() if T0 + 104 <= now < T0 + 112 else []

    rep, client = _run(monkeypatch, _bought_at_100, coins=coins)
    assert rep["reason"].startswith("time limit"), rep["reason"]
    assert [t for kind, _i, t in client.timed if kind == "start" and t > T0 + 112]  # re-armed after the sale
    assert not [t for _k, _i, t in client.timed if T0 + 100 <= t < T0 + 112]  # and nothing touched while it was held


def test_an_unpriced_coin_is_no_emergency(monkeypatch):
    """A coin bought seconds ago that the tape has not priced counts for nothing in equity (positions.py): 4.8 SOL
    beside it read as under the floor, and the run ended while it was worth 5."""

    def unpriced(now):
        return [{"mint": "M1", "amount": 1.0}] if T0 + 100 <= now < T0 + 112 else []

    rep, client = _run(monkeypatch, _bought_at_100, unpriced=unpriced)
    assert rep["reason"].startswith("time limit"), rep["reason"]
    assert [t for kind, _i, t in client.timed if kind == "start" and t > T0 + 112]


def test_wrapped_sol_counts_for_the_floor(monkeypatch):
    """A sale can come back as wrapped SOL (a PumpSwap coin): 4 SOL beside 6 wrapped is 10, no emergency."""

    def sol(now):
        return 10.0 if now < T0 + 100 else 4.0

    rep, _client = _run(monkeypatch, sol, wsol=lambda now: 6.0 if now >= T0 + 100 else 0.0)
    assert rep["reason"].startswith("time limit"), rep["reason"]


def test_a_real_loss_under_the_floor_still_ends_the_run_and_arms_nothing_after_it(monkeypatch):
    """The copy sold at a loss that takes the wallet under the floor: the two readings agree, the run ends, and no
    sniper is armed again after the sale."""

    def sol(now):
        if now < T0 + 100:
            return 10.0
        return 4.8 if now < T0 + 112 else 4.5

    rep, client = _run(monkeypatch, sol, coins=lambda now: _coin(2.0) if T0 + 104 <= now < T0 + 112 else [])
    assert rep["reason"].startswith("EMERGENCY"), rep["reason"]
    assert not [t for kind, _i, t in client.timed if kind == "start" and t >= T0 + 100]
    assert rep["left_running"] == []  # nothing held at the end: both stopped
    assert any("under the floor" in e for e in rep["events"])


def test_a_sniper_is_not_rearmed_while_its_buy_shows_in_the_sol_only(monkeypatch):
    """The re-arm timer came round as a copy landed: the SOL had left, the coin did not show yet, and the sniper that
    bought was stopped and started (a stopped sniper strands its coin; 5 Nov 22:14 the same for the tape). A drop of the
    SOL with nothing seen held is a buy on its way: no re-arm until a reading shows where it went."""

    def sol(now):
        if now < T0 + 60:
            return 10.0
        return 4.8 if now < T0 + 76 else 9.95

    def coins(now):  # the coin shows 4 s after the SOL left, sold at 76 s
        return _coin() if T0 + 64 <= now < T0 + 76 else []

    rep, client = _run(monkeypatch, sol, coins=coins, floor=1.0, rearm_after=60)
    assert not [t for _k, _i, t in client.timed if T0 + 60 <= t < T0 + 76], client.timed
    assert [t for kind, _i, t in client.timed if kind == "start" and t >= T0 + 76]  # re-armed after the sale
    assert rep["reason"].startswith("time limit")


def test_a_buy_whose_coin_shows_late_never_ends_the_run(monkeypatch):
    """The coins are read at "confirmed", the SOL at "processed": a slow node can show the coin many seconds after the
    SOL left. Until FRESH_HOLD_SECS after the fall no reading of the floor counts (two readings alone ended the run when
    the coin showed after the second one)."""

    def coins(now):  # the coin shows 18 s after the SOL left (three readings), sold at 130 s
        return _coin() if T0 + 118 <= now < T0 + 130 else []

    def sol(now):
        if now < T0 + 100:
            return 10.0
        return 4.8 if now < T0 + 130 else 9.95

    rep, client = _run(monkeypatch, sol, coins=coins)
    assert rep["reason"].startswith("time limit"), rep["reason"]
    assert not [t for _k, _i, t in client.timed if T0 + 100 <= t < T0 + 130]  # nothing touched before it was sold


def test_an_end_while_a_buy_is_on_its_way_leaves_the_snipers_running(monkeypatch):
    """The owner's /stop came as a copy landed, its coin not yet shown. Every end stops the snipers only once the
    wallet holds nothing, and a buy on its way is a coin: the end waits for it to show (a stopped sniper strands its
    coin: here 5 SOL with nobody selling it)."""

    def coins(now):  # bought at 100 s, the coin shows at 112 s and is held from then on
        return _coin() if now >= T0 + 112 else []

    def sol(now):
        return 10.0 if now < T0 + 100 else 4.8

    rep, client = _run(monkeypatch, sol, coins=coins, stop_at=T0 + 103)
    assert "/stop" in rep["reason"] and "coin is held" in rep["reason"], rep["reason"]
    assert sorted(rep["left_running"]) == sorted(client.ids)
    assert not [t for kind, _i, t in client.timed if kind == "stop" and t >= T0 + 100]


def test_max_loss_puts_the_floor_at_the_wallet_at_the_start_less_it(monkeypatch):
    """`--max-loss 5` on 10.4 SOL (beside no wrapped SOL): the floor is 5.4, said at the start; a copy that loses
    5.4 SOL ends the run there."""

    def sol(now):
        if now < T0 + 100:
            return 10.4
        return 5.2 if now < T0 + 112 else 5.0

    rep, client = _run(monkeypatch, sol, coins=lambda now: _coin(4.0) if T0 + 104 <= now < T0 + 112 else [],
                       floor=None, max_loss=5.0)  # fmt: skip
    assert any("floor 5.4 " in e and "max loss" in e for e in rep["events"]), rep["events"][:2]
    assert rep["reason"].startswith("EMERGENCY")
    assert not [t for kind, _i, t in client.timed if kind == "start" and t >= T0 + 100]


def test_max_loss_counts_the_wrapped_sol_at_the_start(monkeypatch):
    rep, _client = _run(monkeypatch, lambda now: 9.9, wsol=lambda now: 0.5, floor=None, max_loss=5.0, minutes=1)
    assert any("floor 5.4 " in e for e in rep["events"]), rep["events"][:2]


def test_the_floor_and_the_max_loss_go_one_at_a_time(monkeypatch):
    for kw in ({"floor": 5.0, "max_loss": 5.0}, {"floor": None}, {"floor": None, "max_loss": 0.0},
               {"floor": None, "max_loss": 5.0, "wait_for_sol": 4.0},
               {"floor": 9.0, "wait_for_sol": 8.0}):  # fmt: skip
        with pytest.raises(SystemExit):
            _run(monkeypatch, lambda now: 10.0, **kw)


def test_the_run_waits_for_the_owners_sol_and_starts_when_it_lands(monkeypatch):
    """The owner funds the wallet when the copied wallets trade. Nothing is armed before the SOL lands; the floor is
    the wallet then less the max loss, and the minutes count from the start, not from the wait."""

    def sol(now):
        return 0.4 if now < T0 + 600 else 10.4

    rep, client = _run(monkeypatch, sol, floor=None, max_loss=5.0, wait_for_sol=9.5, wait_minutes=60, minutes=5)
    touched = [t for _k, _i, t in client.timed]
    assert touched and min(touched) >= T0 + 600  # nothing stopped or started before the SOL came
    assert rep["start_sol"] == pytest.approx(10.4)
    assert any("floor 5.4 " in e for e in rep["events"])
    assert rep["reason"].startswith("time limit")
    assert max(touched) >= T0 + 600 + 5 * 60 - 15  # five minutes from the start
    assert any("waiting" in e and "9.5" in e for e in rep["events"])


def test_no_sol_in_the_wait_arms_nothing(monkeypatch):
    rep, client = _run(monkeypatch, lambda now: 0.4, floor=None, max_loss=5.0, wait_for_sol=9.5, wait_minutes=30)
    assert client.timed == []
    assert "9.5" in rep["reason"] and "nothing armed" in rep["reason"]
    assert rep["fills"] == 0 and rep["left_running"] == [] and rep["start_sol"] is None


def test_one_refused_read_at_the_start_does_not_end_the_run(monkeypatch):
    """The moment the owner's SOL lands is no moment for one refused RPC call to end the run: a read at the start is
    tried again two seconds later."""
    calls = {"n": 0}

    def sol(now):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("429 Too Many Requests")
        return 10.0

    rep, client = _run(monkeypatch, sol, floor=None, max_loss=5.0, minutes=1)
    assert rep["start_sol"] == pytest.approx(10.0) and rep["reason"].startswith("time limit")
    assert any("trying again" in e for e in rep["events"])


def test_the_owners_stop_during_the_wait_arms_nothing(monkeypatch):
    rep, client = _run(monkeypatch, lambda now: 0.4 if now < T0 + 900 else 10.4, floor=None, max_loss=5.0,
                       wait_for_sol=9.5, wait_minutes=60, stop_at=T0 + 300)  # fmt: skip
    assert client.timed == [] and "/stop" in rep["reason"]


def test_a_coin_bought_again_is_a_new_holding_for_the_sweep_and_the_rearm(monkeypatch):
    """Cented and Decu buy the same coin again (repeats are in the paper). The run knew a coin by its first sight: the
    second holding read as held for minutes, so the sweep sold it at once (before its 12 s exit), and with the tape
    behind no buy was seen to re-arm after its sale. A coin that left the wallet and comes back is a new holding."""

    def coins(now):  # M1 bought at 100 s and at 400 s, held 12 s each time
        return _coin() if T0 + 100 <= now < T0 + 112 or T0 + 400 <= now < T0 + 412 else []

    def sol(now):
        if now < T0 + 100 or T0 + 112 <= now < T0 + 400:
            return 10.0
        return 4.8 if now < T0 + 412 else 9.9

    sweeps: list = []
    rep, client = _run(monkeypatch, sol, coins=coins, sweeps=sweeps, sweep_after=45, minutes=10)
    assert sweeps == []  # no holding lasted 45 s
    assert {i for kind, i, t in client.timed if kind == "start" and T0 + 412 <= t < T0 + 445} == set(client.ids)
    assert rep["fills"] == 2


def test_sol_the_owner_takes_out_is_no_loss_of_the_run(monkeypatch):
    """22 Nov 02:13: the owner took 2 SOL out of the wallet ("Took 2 sol out btw that was me"); no coin came in, and the
    run ended on its floor with no fill, as the 1 SOL day of 20 Nov had ended on the owner's own trades. SOL that leaves
    with no coin coming in within FRESH_HOLD_SECS is no buy: the floor moves down with it, said once, and only the
    run's own trades count against the max loss."""

    def sol(now):
        return 10.0 if now < T0 + 100 else 8.0

    rep, _client = _run(monkeypatch, sol, floor=None, max_loss=1.0, minutes=10)
    assert rep["reason"].startswith("time limit"), rep["reason"]
    assert rep["floor_moves"] == [pytest.approx(-2.0)]
    assert any("2.0000 SOL left the wallet" in e and "not a trade" in e for e in rep["events"]), rep["events"]


def test_sol_the_owner_puts_in_raises_the_floor_by_as_much(monkeypatch):
    """A deposit is no profit of the run: 5 SOL put in, then a copy that loses 1.5 under a 1 SOL max loss ends the
    run on its floor, which rose with the deposit (before, the run went on losing the owner's 5 SOL)."""

    def sol(now):
        if now < T0 + 100:
            return 10.0
        return 15.0 if now < T0 + 300 else 13.4  # put in at 100 s; a 1.6 SOL copy at 300 s

    rep, _client = _run(monkeypatch, sol, coins=lambda now: _coin(0.1) if now >= T0 + 304 else [], floor=None,
                        max_loss=1.0, minutes=10)  # fmt: skip
    assert rep["reason"].startswith("EMERGENCY"), rep["reason"]
    assert rep["floor_moves"] == [pytest.approx(5.0)]


def test_a_real_loss_after_the_owner_took_sol_out_still_ends_the_run(monkeypatch):
    """The floor moves with the owner's SOL, not with the run's trades: 2 SOL taken out at 100 s, then a 1.6 SOL copy
    whose coin is worth 0.1 takes the run 1.5 under its max loss of 1, and the run ends there."""

    def sol(now):
        if now < T0 + 100:
            return 10.0
        return 8.0 if now < T0 + 300 else 6.4

    rep, _client = _run(monkeypatch, sol, coins=lambda now: _coin(0.1) if now >= T0 + 304 else [], floor=None,
                        max_loss=1.0, minutes=10)  # fmt: skip
    assert rep["reason"].startswith("EMERGENCY"), rep["reason"]
    assert rep["floor_moves"] == [pytest.approx(-2.0)]
    floor_hit = next(e for e in rep["events"] if "under the floor" in e)
    assert "7.0" in floor_hit, floor_hit  # the floor moved from 9 to 7 with the owner's 2 SOL, then the loss crossed it


def test_a_buy_whose_coin_shows_after_the_wait_takes_the_floor_back(monkeypatch):
    """A slow node can show a coin later than FRESH_HOLD_SECS: the SOL of a 1.1 SOL buy left at 100 s and the coin
    showed at 140 s. The floor moved down at 130 s as for the owner's own move, and goes back up when the coin shows:
    a buy is never the owner's SOL."""

    def sol(now):
        if now < T0 + 100:
            return 10.0
        return 8.9 if now < T0 + 152 else 9.95

    rep, _client = _run(monkeypatch, sol, coins=lambda now: _coin(1.05) if T0 + 140 <= now < T0 + 152 else [],
                        floor=None, max_loss=1.0, minutes=10)  # fmt: skip
    assert rep["reason"].startswith("time limit"), rep["reason"]
    assert rep["floor_moves"] == []
    assert any("was a buy" in e for e in rep["events"]), rep["events"]


def _crash_at_100(sweeps):
    """The 22 Nov 00:54 copy: 10 SOL, a 5.2 SOL buy at 100 s whose coin shows at 104 s worth 5 and is worth 0.1 from
    110 s (the wallet then reads 4.9, under a floor of 5); its sniper never sells it, and it leaves the wallet only when
    the run sells it itself (a sale in `sweeps`)."""

    def sold(now):
        return any(m == "M1" and t <= now for m, t in sweeps)

    def coins(now):
        if now < T0 + 104 or sold(now):
            return []
        return _coin(5.0 if now < T0 + 110 else 0.1)

    def sol(now):
        if now < T0 + 100:
            return 10.0
        return 4.9 if sold(now) else 4.8

    return sol, coins


def test_a_coin_its_sniper_never_sells_is_sold_by_the_run_after_the_floor_ends_it(monkeypatch):
    """22 Nov 00:54: a 3.46 SOL copy fell 60 % in 36 s and the floor ended the run with the coin held; the sniper that
    bought it read "finished" and never sold. The run ended there, its --sweep-after 45 not come yet, and the snipers
    were left running with nobody selling the coin (sold by hand at 00:57, for less). An end with a coin held goes on
    watching: the run sells each coin held past --sweep-after itself, then stops the snipers it armed once the wallet
    holds nothing (OKTech refuses to stop a finished sniper: that one is stopped already)."""
    sweeps: list = []
    sol, coins = _crash_at_100(sweeps)
    rep, client = _run(monkeypatch, sol, coins=coins, sweeps=sweeps, sweep_after=45, minutes=30,
                       spent=lambda i, now, last: i == SNIPER_IDS[0] and now >= T0 + 100)  # fmt: skip
    assert rep["reason"].startswith("EMERGENCY"), rep["reason"]
    assert [m for m, _t in sweeps] == ["M1"] and sweeps[0][1] >= T0 + 104 + 45, sweeps
    ev = rep["events"]
    floor_at = next(k for k, e in enumerate(ev) if "under the floor" in e)
    swept_at = next(k for k, e in enumerate(ev) if e.split(" ", 1)[1].startswith("swept M1"))
    assert floor_at < swept_at  # the floor came first: the sale is the run's, after its end
    assert [s["mint"] for s in rep["swept"]] == ["M1"] and rep["held"] == []
    assert rep["left_running"] == [] and "except" not in rep["reason"], rep["reason"]
    # the other sniper, still waiting to fire, holds nothing: stopped before the sale, once OKTech's word has settled
    assert [t for k, i, t in client.timed if k == "stop" and i == SNIPER_IDS[1] and T0 + 100 < t < sweeps[0][1]]
    assert not [t for kind, _i, t in client.timed if kind == "start" and t >= T0 + 100]  # nothing armed again


def test_a_coin_the_run_cannot_sell_after_its_end_is_left_to_the_snipers(monkeypatch):
    """A sale that fails is tried again each minute, for END_WATCH_SECS after the end at most: then the run ends with
    the coin held and the snipers running (a stopped sniper strands its coin), and says so."""
    from snipetech.service import END_WATCH_SECS, SWEEP_RETRY_SECS

    sweeps: list = []
    sol, coins = _crash_at_100([])  # never sold
    rep, client = _run(monkeypatch, sol, coins=coins, sweeps=sweeps, sweep_after=45, minutes=30, sweep_fails=True)
    assert rep["reason"].startswith("EMERGENCY") and "coin is held" in rep["reason"], rep["reason"]
    assert sorted(rep["left_running"]) == sorted(client.ids) and rep["held"]
    times = [t for _m, t in sweeps]
    assert len(times) >= END_WATCH_SECS // SWEEP_RETRY_SECS - 2, times
    assert all(b - a >= SWEEP_RETRY_SECS for a, b in itertools.pairwise(times))
    assert times[-1] <= T0 + 130 + END_WATCH_SECS  # bounded: the floor ended the run before 130 s
    assert not [t for kind, _i, t in client.timed if kind == "stop" and t >= T0 + 100]


def test_a_coin_its_sniper_sells_after_the_end_stops_the_snipers_then(monkeypatch):
    """The owner's /stop came with a coin held; its sniper sold it 30 s later. Before, the run ended at once and left
    both snipers armed (one still able to buy at the next copy, with nobody watching); now it watches the wallet and
    stops them once it holds nothing."""

    def coins(now):  # bought at 100 s, shown at 104 s, sold by its sniper at 135 s
        return _coin() if T0 + 104 <= now < T0 + 135 else []

    def sol(now):
        if now < T0 + 100:
            return 10.0
        return 4.8 if now < T0 + 135 else 9.9

    sweeps: list = []
    rep, client = _run(monkeypatch, sol, coins=coins, sweeps=sweeps, sweep_after=45, minutes=30, stop_at=T0 + 110)
    assert "/stop" in rep["reason"], rep["reason"]
    assert sweeps == [] and rep["held"] == []
    assert rep["left_running"] == []
    assert {i for kind, i, t in client.timed if kind == "stop" and t >= T0 + 135} == set(client.ids)


def test_the_owners_10_sol_night_end_to_end(monkeypatch):
    """The 5 SOL day as it runs (21 Nov): the runner waits, the owner's 10 SOL lands at 300 s, four copies of 5 SOL.
    1 wins a little (its coin shows 3 s after the SOL left and is unpriced for 5 s more); 2 loses 0.42 while the other
    sniper fires as well and fails for want of SOL; 3 loses 1.1; 4 is a rug that takes the wallet under its floor of
    10.37 - 5 = 5.37 while the coin is still held. Nothing is touched before the SOL lands or while a coin is on its way
    or held; both snipers are re-armed after each sale; the run ends on the floor with the rug's coin held, so both
    snipers are left running to sell it (a stopped sniper strands its coin)."""
    s = T0 + 302  # the first reading of the wait at or past 300 s, and the start

    def sol(now):
        steps = [(T0 + 300, 10.37), (s + 400, 5.16), (s + 412, 10.42), (s + 700, 5.21), (s + 704, 5.209),
                 (s + 712, 10.0), (s + 1000, 4.79), (s + 1012, 8.9), (s + 1300, 3.69)]  # fmt: skip
        out = 0.37
        for at, v in steps:
            if now >= at:
                out = v
        return out

    def coins(now):
        if s + 408 <= now < s + 412:
            return _coin(5.1)
        if s + 702 <= now < s + 712:
            return _coin(4.9)
        if s + 1003 <= now < s + 1012:
            return _coin(4.0)
        return _coin(0.3) if now >= s + 1305 else []

    def unpriced(now):  # copy 1's coin shows at 403 s and the tape prices it from 408 s
        return [{"mint": "M1", "amount": 1.0}] if s + 403 <= now < s + 408 else []

    rep, client = _run(monkeypatch, sol, coins=coins, unpriced=unpriced, floor=None, max_loss=5.0, wait_for_sol=9.5,
                       wait_minutes=60, minutes=30, max_fills=200)  # fmt: skip
    touched = [t for _k, _i, t in client.timed]
    assert min(touched) >= T0 + 300  # nothing before the owner's SOL
    assert any("floor 5.37 (10.3700 at the start less the 5 SOL max loss)" in e for e in rep["events"])
    for a, b in ((s + 400, s + 412), (s + 700, s + 712), (s + 1000, s + 1012)):
        assert not [t for t in touched if a <= t < b], (a - s, b - s)  # never while a coin is on its way or held
    for sale in (s + 412, s + 712, s + 1012):  # both snipers re-armed within half a minute of each sale
        assert {i for kind, i, t in client.timed if kind == "start" and sale <= t < sale + 30} == set(client.ids)
    assert not [t for t in touched if t >= s + 1300]  # the rug: nothing touched, and nothing stopped at the end
    assert rep["reason"].startswith("EMERGENCY") and "coin is held" in rep["reason"], rep["reason"]
    assert sorted(rep["left_running"]) == sorted(client.ids)


def test_cycle_cli_hands_the_max_loss_and_the_wait_to_the_runner(tmp_path, monkeypatch, capsys):
    from snipetech import cli
    from snipetech.service import Service

    seen: list[dict] = []

    async def cycle(self, ids, address, floor_sol, max_minutes, max_fills=10, rearm_after=60.0, ignore_mints=None,
                    **kw):  # fmt: skip
        seen.append({"floor": floor_sol, **kw})
        return {"events": ["done"]}

    monkeypatch.setattr(Service, "oktech_cycle", cycle)
    base = ["--db", str(tmp_path / "snipetech.db"), "oktech", "cycle", "--id", "a", "--address", W6]
    assert cli.main([*base, "--max-loss", "5", "--wait-for-sol", "9.5", "--wait-minutes", "240"]) == 0
    assert seen[0]["floor"] is None and seen[0]["max_loss"] == 5.0 and seen[0]["wait_for_sol"] == 9.5
    assert seen[0]["wait_minutes"] == 240.0
    assert cli.main([*base, "--floor", "0.1"]) == 0
    assert seen[1]["floor"] == 0.1 and seen[1]["max_loss"] is None and seen[1]["wait_for_sol"] is None
    with pytest.raises(SystemExit):
        cli.main(base)  # neither --floor nor --max-loss
    capsys.readouterr()
