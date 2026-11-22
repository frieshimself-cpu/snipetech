"""How a run ends with a coin held (live-path audit of 22 Nov 2026, findings 3 and 4).

3. Every armed sniper stayed armed while a coin was held, those that bought nothing too: after the 22 Nov 00:54 floor
   end the Decu 3.5 SOL sniper held nothing and stayed armed until stopped by hand, free to buy once more with nobody
   watching. A sniper OKTech reads as still waiting to fire, which copies none of the wallets the held coins' buys
   followed, holds nothing: it is stopped at once.
4. The end read the wallet once: one node reply that left out a held coin stopped the sniper that held it (OKTech's
   auto-sell stops with its sniper) and skipped the sweep. The re-arm already needs two empty readings for this fault.
"""

import itertools

from snipetech.models import Trade
from tests.test_cycle_floor import T0, _run
from tests.test_oktech import SNIPER_IDS, W6

A, B = SNIPER_IDS
LEAD_A = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
LEAD_B = "5Y9pyDgrXkAqfFbvb5YhELc3VQhGVfkHTFpaVR1GFJmb"
_sigs = itertools.count()


def _buy(ts, user, sol, priority=1_000_000, tip=199_000_000, mint="M1", pos=0):
    return Trade(sig=f"t{next(_sigs)}", ev=0, slot=100_000 + int(ts - T0), pos=pos, ts=int(ts), mint=mint,
                 venue="curve", user=user, is_buy=True, quote=round(sol * 1e9), base=1_000_000, fee=0, fee_bps=100,
                 q_after=1, b_after=1, priority=priority, tip=tip)  # fmt: skip


def _payloads():
    """A copies LEAD_A, B copies LEAD_B; both pay the 1 SOL day's fees."""
    return {"priorityFee": 0.001, "tipAmount": 0.199}


def _two_leaders(monkeypatch, coins, *, stop_at, spent, sweep_fails=False, extra=(), **kw):
    """The runner over A and B, each copying its own wallet: LEAD_A buys M1 at 99 s and A's copy follows at 100 s
    (and the trades `extra` after)."""
    from snipetech.store import Store

    real_add = Store.add_oktech_sniper

    def add(self, rec):  # each sniper copies its own leader
        lead = LEAD_A if rec["id"] == A else LEAD_B
        rec = dict(rec, payload=dict(rec["payload"], ctbotWallets=[{"address": lead}]))
        return real_add(self, rec)

    monkeypatch.setattr(Store, "add_oktech_sniper", add)
    tape = [_buy(T0 + 99, LEAD_A, 3.0, priority=50_000, tip=0), _buy(T0 + 100, W6, 1.0, pos=1), *extra]
    return _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, coins=coins, payload=_payloads(),
                tape=tape, stop_at=stop_at, spent=spent, sweep_fails=sweep_fails, **kw)  # fmt: skip


def _stops(client, i, since=T0 + 100):
    """When sniper i was stopped after `since` (the run stops each one once at its start, before arming it)."""
    return [t for kind, j, t in client.timed if kind == "stop" and j == i and t >= since]


def _coin(value=1.0):
    return [{"mint": "M1", "amount": 1.0, "raw": 1_000_000, "value_sol": value, "priced": True}]


def test_at_the_end_a_sniper_that_holds_nothing_is_stopped_at_once(monkeypatch):
    """/stop at 110 s while A's copy is held (104-135 s, its sniper sells it then): B fired at nothing and still waits
    to fire, so it is stopped at the end, not 25 s later once A's coin is gone."""

    def spent(i, now, last_start):  # A fired at 100 s; B never fires
        return i == A and now >= T0 + 101

    rep, client = _two_leaders(monkeypatch, lambda now: _coin() if T0 + 104 <= now < T0 + 135 else [], stop_at=T0 + 110,
                               spent=spent, sweep_after=45)  # fmt: skip
    assert _stops(client, B) and min(_stops(client, B)) < T0 + 120
    assert "/stop" in rep["reason"] and rep["left_running"] == []


def test_a_coin_the_run_cannot_sell_keeps_only_its_own_sniper_running(monkeypatch):
    """The floor ends a run whose copy's coin neither OKTech nor the run's own sale can sell: the sniper that bought it
    stays (a stopped sniper strands its coin); the one that holds nothing does not."""

    def spent(i, now, last_start):
        return i == A and now >= T0 + 101

    rep, client = _two_leaders(monkeypatch, lambda now: _coin() if now >= T0 + 104 else [], stop_at=T0 + 110,
                               spent=spent, sweep_after=20, sweep_fails=True, minutes=40)  # fmt: skip
    assert rep["left_running"] == [A] and _stops(client, B)


def test_one_empty_reply_at_the_end_stops_nothing(monkeypatch):
    """A coin held from 104 s to 200 s, /stop at 110 s, and the end's first reading of the wallet comes back empty (a
    node left the coin out of one reply): no sniper is stopped while the coin is held."""
    flicker = {"done": False}

    def coins(now):
        if not T0 + 104 <= now < T0 + 200:
            return []
        if now >= T0 + 110 and not flicker["done"]:
            flicker["done"] = True
            return []
        return _coin()

    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, coins=coins, stop_at=T0 + 110)
    assert not [t for kind, _i, t in client.timed if kind == "stop" and T0 + 100 <= t < T0 + 200]
    # the watch after the end stops them once the coin has gone (its sniper's own exit)
    assert "left the wallet" in rep["reason"] and rep["left_running"] == []


def test_a_run_told_to_stop_stops_its_spare_snipers_before_it_goes(monkeypatch):
    """Live-path audit finding 6: a runner its job's time limit, a kill or a restart ends (SIGTERM, then SIGKILL 30 s
    later) left every sniper armed with nobody watching. Told to stop while A's copy is held, it stops B, which holds
    nothing, and leaves A (a stopped sniper strands its coin) before it goes."""
    import asyncio

    import pytest

    from tests import test_oktech

    clients, told = [], {"done": False}
    real_init = test_oktech._SpentClient.__init__

    def init(self, *a, **kw):
        real_init(self, *a, **kw)
        clients.append(self)

    monkeypatch.setattr(test_oktech._SpentClient, "__init__", init)

    def coins(now):
        if now >= T0 + 140 and not told["done"]:  # the job's limit: the stop reaches the run at its next wait
            told["done"] = True
            raise asyncio.CancelledError
        return _coin() if now >= T0 + 104 else []

    with pytest.raises(asyncio.CancelledError):
        _two_leaders(monkeypatch, coins, stop_at=None, spent=lambda i, now, last_start: i == A and now >= T0 + 101,
                     sweep_after=600, minutes=30)  # fmt: skip
    (client,) = clients
    assert _stops(client, B, since=T0 + 140) and not _stops(client, A)


def test_one_failed_read_at_the_end_is_read_again_before_the_snipers_are_left_armed(monkeypatch):
    """Audit of 22 Nov, runner finding 2: the end read the wallet once and on any failure left every sniper armed,
    nobody watching (one more buy each: no floor, no fills cap, no time limit). One 429 at the moment /stop ended a run
    with nothing held did it. The main loop and the watch after the end read again on a failure; now the end does."""
    state = {"stopped": False, "failed": 0}

    def coins(now):  # nothing is ever held; /stop at 60 s, and the end's first read of the wallet fails once
        if state["stopped"] and state["failed"] == 0:
            state["failed"] += 1
            raise RuntimeError("HTTP 429 (rate limited)")
        if now >= T0 + 60:
            state["stopped"] = True
        return []

    rep, _client = _run(monkeypatch, lambda now: 10.0, coins=coins, stop_at=T0 + 60, minutes=5)
    assert state["failed"] == 1
    assert rep["left_running"] == [], (rep["reason"], rep["left_running"])
    assert "could not be read" not in rep["reason"]


def test_a_wallet_that_stays_unreadable_at_the_end_leaves_the_snipers_running_within_the_watch(monkeypatch):
    """The end reads again every 5 s, END_WATCH_SECS after the end at most (the job's limit allows the run that much
    past its minutes): then unknown holdings still stop nothing, and it says so."""
    from snipetech.service import END_WATCH_SECS

    reads = []

    def coins(now):
        if now >= T0 + 60:
            reads.append(now)
            raise RuntimeError("HTTP 503")
        return []

    rep, client = _run(monkeypatch, lambda now: 10.0, coins=coins, stop_at=T0 + 60, minutes=5)
    assert "could not be read" in rep["reason"] and sorted(rep["left_running"]) == sorted(client.ids)
    assert len(reads) > 100 and reads[-1] - reads[0] <= END_WATCH_SECS + 10, (len(reads), reads[-1] - reads[0])


def test_one_failed_read_on_the_way_out_is_read_again_within_its_15_s(monkeypatch):
    """Runner finding 2, the way out (`last_stop`, told to stop by the job's limit, a kill or a restart): it has 15 s
    and read again every 3 s while a coin could not be placed, but one failed read ended it at once, every sniper left
    running."""
    import asyncio

    import pytest

    clients, state = _capture_clients(monkeypatch), {"told": False, "failed": 0}

    def coins(now):
        if now >= T0 + 60 and not state["told"]:  # the job's limit reaches the run at its read
            state["told"] = True
            raise asyncio.CancelledError
        if state["told"] and state["failed"] == 0:  # the first read on the way out fails once
            state["failed"] += 1
            raise RuntimeError("HTTP 429 (rate limited)")
        return []

    with pytest.raises(asyncio.CancelledError):
        _run(monkeypatch, lambda now: 10.0, coins=coins, minutes=30)
    (client,) = clients
    assert state["failed"] == 1
    assert {i for kind, i, t in client.timed if kind == "stop" and t >= T0 + 60} == set(SNIPER_IDS)


def test_a_job_limit_shorter_than_the_run_refuses_it_before_anything_is_armed(monkeypatch):
    """Killed mid-run at its job's limit, a runner leaves its snipers armed: 6 h by default against a 24 h run."""
    import pytest

    monkeypatch.setenv("SNIPETECH_JOB_TIMEOUT", str(6 * 3600))
    with pytest.raises(SystemExit, match="time limit"):
        _run(monkeypatch, lambda now: 10.0, minutes=1440, sweep_after=20)


def test_a_second_runner_on_the_same_wallet_is_refused(monkeypatch, tmp_path):
    """Live-path audit finding 7: nothing kept one runner per wallet. A second one swept the first's coins at its own
    --sweep-after, counted its buys as its own, put its losses on its own floor and re-armed its snipers."""
    import fcntl

    import pytest

    from snipetech.operator.paths import OperatorPaths

    lock = OperatorPaths(tmp_path).ensure().root / f"driver-{W6}.lock"
    with open(lock, "a+") as fh:  # the first runner holds it
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit, match="one runner per wallet"):
            _run(monkeypatch, lambda now: 10.0, db_dir=tmp_path)
    rep, _client = _run(monkeypatch, lambda now: 10.0, db_dir=tmp_path, minutes=1)  # free once it has gone
    assert rep["reason"].startswith("time limit")


def test_a_run_never_starts_beside_snipers_it_did_not_arm(monkeypatch):
    """A sniper armed in OKTech beside the run (by hand, by the book, by another test) buys into the same wallet: its
    coins would be swept, counted and floored as the run's. The run names it and starts nothing."""
    import pytest

    with pytest.raises(SystemExit, match="ffff01"):
        _run(monkeypatch, lambda now: 10.0, others=[("6b00664d3601fd350affff01", "running"), ("x2", "finished")])


def _capture_clients(monkeypatch):
    from tests import test_oktech

    clients = []
    real_init = test_oktech._SpentClient.__init__

    def init(self, *a, **kw):
        real_init(self, *a, **kw)
        clients.append(self)

    monkeypatch.setattr(test_oktech._SpentClient, "__init__", init)
    return clients


def test_a_failed_start_stops_what_was_armed_before_the_run_goes(monkeypatch):
    """System audit finding 7: OKTech timing out on the second start raised out of the start loop with the first sniper
    armed, and nobody stopped it. Any error ends the run the way a stop signal does: what strands nothing is stopped."""
    import pytest

    from snipetech.oktech import OktechError
    from tests import test_oktech

    clients = _capture_clients(monkeypatch)
    real_start = test_oktech._SpentClient.start

    async def start(self, i):
        if i == B:
            raise OktechError(504, "NETWORK", "gateway timeout")
        return await real_start(self, i)

    monkeypatch.setattr(test_oktech._SpentClient, "start", start)
    with pytest.raises(OktechError):
        _run(monkeypatch, lambda now: 10.0, minutes=5)
    (client,) = clients
    started = [t for kind, i, t in client.timed if kind == "start" and i == A]
    assert started and [t for kind, i, t in client.timed if kind == "stop" and i == A and t >= started[0]]


def test_a_rearm_whose_start_answer_is_lost_is_still_stopped_at_the_end(monkeypatch):
    """A re-arm whose start OKTech carried out but whose answer never came took that sniper out of the run's count:
    the end stopped only the others and said nothing was left armed."""
    from snipetech.oktech import OktechError
    from tests import test_oktech

    clients = _capture_clients(monkeypatch)
    real_start = test_oktech._SpentClient.start
    lost = {"n": 0}

    async def start(self, i):
        out = await real_start(self, i)  # OKTech starts it ...
        if i == A and self.clock["t"] > T0 + 30 and lost["n"] == 0:
            lost["n"] += 1
            raise OktechError(504, "NETWORK", "the answer was lost")  # ... and the answer never comes
        return out

    monkeypatch.setattr(test_oktech._SpentClient, "start", start)
    rep, _client = _run(monkeypatch, lambda now: 10.0, rearm_after=60, stop_at=T0 + 64, minutes=5)
    (client,) = clients
    lost_at = min(t for kind, i, t in client.timed if kind == "start" and i == A and t > T0 + 30)
    assert [t for kind, i, t in client.timed if kind == "stop" and i == A and t > lost_at]
    assert rep["left_running"] == []


def test_a_sale_that_did_not_go_out_is_not_reported_sold_and_is_tried_again_soon(monkeypatch):
    """System audit finding 9: the run's own sale took any answer for a sale. Twice the PumpSwap pool was not there
    yet ("skipped") and the end said the coin was sold by the run itself; the next try waited 60 s."""
    sweeps = []

    def sol(now):
        return 10.0 if now < T0 + 100 else 9.0

    def coins(now):  # held from 102 s until OKTech's own sale at 160 s
        return [{"mint": "M1", "amount": 1.0, "value_sol": 1.0, "priced": True}] if T0 + 102 <= now < T0 + 160 else []

    def skipped(mint):
        return {"wallet": W6, "holdings": 1, "results": [{"mint": mint, "skipped": "PumpSwap: no pool yet"}]}

    rep, _client = _run(monkeypatch, sol, coins=coins, sweeps=sweeps, sweep_after=20, minutes=4, sweep_answer=skipped)
    times = [t for _m, t in sweeps]
    assert rep["swept"] == [] and len(times) >= 2 and times[1] - times[0] <= 20
    assert not any("sold by the run itself" in e for e in rep["events"])
    assert any("did not go out" in e for e in rep["events"])


def test_a_read_that_never_answers_holds_up_no_tick(monkeypatch):
    """System audit finding 10: one RPC read can retry for minutes, and the floor, the sweep and /stop all waited on
    it. A read that does not answer in time is a failed reading: the next tick comes on time."""
    import snipetech.service as S

    monkeypatch.setattr(S, "READ_SECS", 0.05)
    hung = {"done": False}

    def sol(now):
        return 10.0

    rep, _client = _run(monkeypatch, sol, stop_at=T0 + 60, minutes=5, hang_at=T0 + 30, hung=hung)
    assert hung["done"] and "/stop" in rep["reason"]
    assert any("did not answer" in e for e in rep["events"])


def test_the_runs_own_sale_must_sign_for_the_runs_wallet(monkeypatch):
    """System audit finding 30: the run's own sale signs with the selling key's wallet (SNIPER_SECRET), never the
    run's --address: on another wallet it sold nothing, or another wallet's copy of the same coin, and said swept."""
    import pytest

    with pytest.raises(SystemExit, match="selling key"):
        _run(monkeypatch, lambda now: 10.0, sweep_after=20, seller="Other111111111111111111111111111111111111111")


def test_the_selling_wallet_is_the_selling_keys(monkeypatch):
    """The run's own sale signs with SNIPER_SECRET (solana/sell.py): its wallet, or None with no key or no valid one."""
    from solders.keypair import Keypair

    from snipetech.config import Config
    from snipetech.service import Service
    from snipetech.store import Store

    kp = Keypair()
    svc = Service(Config(), store=Store(":memory:"))
    monkeypatch.setenv("SNIPER_SECRET", str(kp))
    assert svc.selling_wallet() == str(kp.pubkey())
    monkeypatch.setenv("SNIPER_SECRET", "not a key")
    assert svc.selling_wallet() is None
    monkeypatch.delenv("SNIPER_SECRET")
    assert svc.selling_wallet() is None


def test_a_stop_at_the_end_that_fails_once_is_tried_again(monkeypatch):
    """One refused stop at the end (OKTech timing out) left the sniper armed with nobody watching, and the end said to
    stop it with /stop. Each stop at the end is tried three times, two seconds apart."""
    from snipetech.oktech import OktechError
    from tests import test_oktech

    real_stop = test_oktech._SpentClient.stop
    refused = {"n": 0}

    async def stop(self, i):
        if i == A and self.clock["t"] >= T0 + 60 and refused["n"] == 0:
            refused["n"] += 1
            raise OktechError(504, "NETWORK", "gateway timeout")
        return await real_stop(self, i)

    monkeypatch.setattr(test_oktech._SpentClient, "stop", stop)
    rep, _client = _run(monkeypatch, lambda now: 10.0, stop_at=T0 + 60, minutes=5)
    assert refused["n"] == 1 and rep["left_running"] == [] and "with /stop" not in rep["reason"]


def test_a_coin_both_snipers_bought_keeps_both_running_at_the_end(monkeypatch):
    """Review of 22 Nov: the end placed a held coin by the run's newest buy of it only. A copied LEAD_A into M1 at
    100 s and B copied LEAD_B into the same coin at 104 s; OKTech read both "running" (it reads so after a buy: 21 Nov
    04:25, "finished" only 13 s on), and the end stopped A with its part of the coin held. Every buy of the holding
    counts."""
    extra = [_buy(T0 + 103, LEAD_B, 3.0, priority=50_000, tip=0), _buy(T0 + 104, W6, 1.0, pos=1)]
    rep, client = _two_leaders(monkeypatch, lambda now: _coin(2.0) if now >= T0 + 102 else [], stop_at=T0 + 110,
                               spent=lambda i, now, last_start: False, extra=extra)  # fmt: skip
    assert not _stops(client, A) and not _stops(client, B)
    assert "a coin is held" in rep["reason"] and sorted(rep["left_running"]) == sorted(SNIPER_IDS)


def test_a_coin_no_buy_on_the_tape_explains_stops_no_sniper(monkeypatch):
    """Review of 22 Nov: a coin held with no buy of the run's on the tape (a PumpSwap coin the recorder had no trade of,
    as on 20 Nov 17:31, when OKTech still read the sniper that bought it "running") was put down to whichever sniper
    OKTech read as fired. B's send had failed (it reads "finished", nothing bought) and A, which bought the coin, read
    "running": the end stopped A. With no buy to tell which sniper holds the coin, none is stopped."""

    def pay(i):
        return _payloads() | {"ctbotWallets": [{"address": LEAD_A if i == A else LEAD_B}]}

    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0,
                       coins=lambda now: _coin() if now >= T0 + 102 else [], payload=pay, stop_at=T0 + 140,
                       spent=lambda i, now, last_start: i == B and now >= T0 + 90, minutes=5)  # fmt: skip
    assert not _stops(client, A)
    assert "a coin is held" in rep["reason"] and A in rep["left_running"]


def test_a_second_signal_on_the_way_out_does_not_cancel_the_stop(monkeypatch):
    """Review of 22 Nov: `remote kill` signals the job's whole group, and the job's own `timeout` wrapper passes its
    SIGTERM on too, so the runner gets two. The second cancelled the stop on the way out and every sniper stayed armed
    with nobody watching (12 of 12 trials in the reviewer's probe). Once the run is on its way out a signal changes
    nothing."""
    import asyncio
    import os
    import signal

    import pytest

    from tests import test_oktech

    clients, sent = [], {"n": 0}
    real_init, real_sleep = test_oktech._SpentClient.__init__, asyncio.sleep  # (before the run's clock replaces it)

    def init(self, *a, **kw):
        real_init(self, *a, **kw)
        clients.append(self)

    monkeypatch.setattr(test_oktech._SpentClient, "__init__", init)

    def coins(now):
        async def read():
            if now >= T0 + 140 and sent["n"] == 0:  # the kill reaches the run at its read, as above
                sent["n"] = 1
                raise asyncio.CancelledError
            if sent["n"] == 1:  # the stop on the way out reads the wallet, and the second SIGTERM comes now
                sent["n"] = 2
                assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL, "the run's handler is gone"
                os.kill(os.getpid(), signal.SIGTERM)
                await real_sleep(0.05)  # the read takes real time: the signal is handled meanwhile
            return []

        return read()

    with pytest.raises(asyncio.CancelledError):
        _run(monkeypatch, lambda now: 10.0, coins=coins, minutes=30)
    (client,) = clients
    assert sent["n"] == 2 and _stops(client, A, since=T0 + 140) and _stops(client, B, since=T0 + 140)


def test_no_rearm_while_a_buy_of_the_runs_is_on_its_way(monkeypatch):
    """Review of 22 Nov: the tape had the run's buy (a PumpSwap buy paid from wrapped SOL moves no plain SOL) while the
    node had not shown its coin yet, and the re-arm, which counts that buy as a fill, stopped the sniper that had just
    bought it: a stopped sniper strands its coin. No sniper is re-armed while a buy of the run's is on its way."""

    def pay(i):
        return _payloads() | {"ctbotWallets": [{"address": LEAD_A if i == A else LEAD_B}]}

    _rep, client = _run(monkeypatch, lambda now: 10.0, coins=lambda now: _coin() if T0 + 140 <= now < T0 + 200 else [],
                        payload=pay, tape=[_buy(T0 + 99, LEAD_A, 3.0, priority=50_000, tip=0),
                                           _buy(T0 + 100, W6, 1.0, pos=1)], rearm_after=1800, minutes=5)  # fmt: skip
    assert not [t for kind, _i, t in client.timed if kind in ("start", "stop") and T0 + 100 < t < T0 + 140]


def test_a_coin_two_copied_wallets_bought_just_before_us_keeps_both_snipers(monkeypatch):
    """Review of 22 Nov: the end placed our buy behind the nearest copied buy only. LEAD_A and LEAD_B both bought M1
    in the block before our buy, and our buy was A's copy of LEAD_A: placed behind LEAD_B, it was put down to B, and A
    was stopped holding it. Every copied wallet that bought the coin just before our buy counts."""
    extra = [_buy(T0 + 99, LEAD_B, 3.0, priority=50_000, tip=0, pos=2)]  # LEAD_B after LEAD_A, before our buy
    _rep, client = _two_leaders(monkeypatch, lambda now: _coin() if now >= T0 + 102 else [], stop_at=T0 + 110,
                                spent=lambda i, now, last_start: False, extra=extra)  # fmt: skip
    assert not _stops(client, A) and not _stops(client, B)


def test_an_end_right_after_a_buy_the_tape_writes_late_stops_nothing(monkeypatch):
    """Review of 22 Nov: the end read the wallet empty and asked the tape once; a buy paid from wrapped SOL (no fall
    of the plain SOL) that the recorder wrote a few seconds late was not there yet, and the end stopped the sniper whose
    coin was on its way. The end reads for PLACE_SETTLE_SECS at least before it calls the wallet empty."""
    from tests.test_cycle_floor import _run

    def pay(i):
        return _payloads() | {"ctbotWallets": [{"address": LEAD_A if i == A else LEAD_B}]}

    late = [(T0 + 108, _buy(T0 + 99, LEAD_A, 3.0, priority=50_000, tip=0)), (T0 + 108, _buy(T0 + 100, W6, 1.0, pos=1))]
    rep, client = _run(monkeypatch, lambda now: 10.0, coins=lambda now: _coin() if T0 + 113 <= now < T0 + 400 else [],
                       payload=pay, tape=late, stop_at=T0 + 101, minutes=5)  # fmt: skip
    assert not [t for t in _stops(client, A, since=T0 + 100) if t < T0 + 400]  # none while its coin is held
    assert "left the wallet" in rep["reason"]


def test_a_coin_bought_again_by_the_other_sniper_is_not_placed_by_the_old_buy(monkeypatch):
    """Review of 22 Nov: A copied LEAD_A into M1 at 150 s and sold it at 162 s; at 400 s B copied LEAD_B into M1
    again, and the recorder wrote B's buy 9 s late. The end at 405 s placed M1 by A's old buy and stopped B, which held
    it. Only buys since the wallet's last sale of the coin place it, and a holding the tape's buys do not explain yet
    stops nobody."""
    from tests.test_cycle_floor import _run

    def pay(i):
        return _payloads() | {"ctbotWallets": [{"address": LEAD_A if i == A else LEAD_B}]}

    from dataclasses import replace

    sale = replace(_buy(T0 + 162, W6, 1.0, pos=0), is_buy=False)
    tape = [_buy(T0 + 149, LEAD_A, 3.0, priority=50_000, tip=0), _buy(T0 + 150, W6, 1.0, pos=1), sale,
            _buy(T0 + 399, LEAD_B, 3.0, priority=50_000, tip=0), (T0 + 409, _buy(T0 + 400, W6, 1.0, pos=1))]  # fmt: skip

    def coins(now):
        return _coin() if (T0 + 152 <= now < T0 + 162) or now >= T0 + 402 else []

    _rep, client = _run(monkeypatch, lambda now: 10.0, coins=coins, payload=pay, tape=tape, stop_at=T0 + 405,
                        minutes=10)  # fmt: skip
    assert not _stops(client, B, since=T0 + 400)


def _late_copy():
    """LEAD_A buys M1 at 99 s and A's copy follows at 100 s; the recorder writes both at 109 s."""
    return [(T0 + 109, _buy(T0 + 99, LEAD_A, 3.0, priority=50_000, tip=0)), (T0 + 109, _buy(T0 + 100, W6, 1.0, pos=1))]


def _own_leader(i):
    return _payloads() | {"ctbotWallets": [{"address": LEAD_A if i == A else LEAD_B}]}


def test_a_fills_cap_end_without_sweep_after_still_stops_the_spare_sniper(monkeypatch):
    """Review of 22 Nov: without --sweep-after the end read the wallet once and went. --max-fills 1: A's copy shows at
    104 s and the cap ends the run there, 5 s before the recorder writes its buy, so the coin cannot be placed yet, and
    B, which bought nothing, was left armed with nobody watching (one more buy: no floor, no cap, no time limit). The
    watch after the end runs with or without --sweep-after: B is stopped once the buy is on the tape, A once its coin
    is gone, and without --sweep-after the run sells nothing itself."""
    sweeps: list = []
    rep, client = _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0,
                       coins=lambda now: _coin() if T0 + 104 <= now < T0 + 300 else [], payload=_own_leader,
                       tape=_late_copy(), spent=lambda i, now, last_start: i == A and now >= T0 + 101, max_fills=1,
                       minutes=30, sweeps=sweeps)  # fmt: skip
    assert _stops(client, B) and min(_stops(client, B)) < T0 + 130
    assert not [t for t in _stops(client, A) if t < T0 + 300]
    assert rep["left_running"] == [] and sweeps == []


def test_a_stop_signal_just_after_a_copy_the_tape_writes_late_still_stops_the_spare(monkeypatch):
    """Review of 22 Nov: told to stop at 106 s, 2 s after A's copy showed and 3 s before the recorder wrote its buy,
    the way out placed the coin once, could not, and left B armed. It tries again every few seconds while the job's
    30 s grace lasts."""
    import asyncio

    import pytest

    from tests import test_oktech

    clients, told = [], {"done": False}
    real_init = test_oktech._SpentClient.__init__

    def init(self, *a, **kw):
        real_init(self, *a, **kw)
        clients.append(self)

    monkeypatch.setattr(test_oktech._SpentClient, "__init__", init)

    def coins(now):
        if now >= T0 + 106 and not told["done"]:
            told["done"] = True
            raise asyncio.CancelledError
        return _coin() if now >= T0 + 104 else []

    with pytest.raises(asyncio.CancelledError):
        _run(monkeypatch, lambda now: 10.0 if now < T0 + 100 else 9.0, coins=coins, payload=_own_leader,
             tape=_late_copy(), spent=lambda i, now, last_start: i == A and now >= T0 + 101, sweep_after=600,
             minutes=30)  # fmt: skip
    (client,) = clients
    assert _stops(client, B, since=T0 + 106) and not _stops(client, A, since=T0 + 106)
