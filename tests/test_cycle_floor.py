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

import pytest

from tests.test_oktech import SNIPER_IDS, W6, _cycle_svc, _SpentClient

T0 = 1_794_045_600.0


def _coin(value=5.0):
    return [{"mint": "M1", "amount": 1.0, "value_sol": value, "priced": True}]


def _run(monkeypatch, sol, *, coins=None, unpriced=None, wsol=None, floor=5.0, minutes=5, stop_at=None, **kw):
    """The runner over two copy snipers on the fake clock, with `sol(now)` SOL in the wallet, the coins `coins(now)`
    lists (priced) and `unpriced(now)` lists (not on the tape), `wsol(now)` wrapped SOL, and the owner's /stop from
    `stop_at` on."""
    clock, flag = {"t": T0}, {"stop": False}
    client = _SpentClient(clock, SNIPER_IDS, lambda i, now, last_start: False)

    def on_sleep(_secs):
        if stop_at is not None and clock["t"] >= stop_at:
            flag["stop"] = True

    svc, store = _cycle_svc(monkeypatch, client, flag, clock=clock, balance=lambda: sol(clock["t"]), coins=coins,
                            unpriced=unpriced, on_sleep=on_sleep)  # fmt: skip
    for i in SNIPER_IDS:
        store.add_oktech_sniper(
            {"id": i, "kind": "copy", "source": "x", "payload": {"singleSnipe": True, "totalSnipes": 1}}
        )

    async def wrapped(address):
        return wsol(clock["t"]) if wsol else 0.0

    monkeypatch.setattr(svc, "wsol_balance", wrapped)
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
