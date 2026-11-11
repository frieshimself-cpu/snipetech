"""The tape has no hole where its stream dropped or the recorder restarted: it replays what it missed (11 Nov).

Every restart (each deploy) and every drop of the stream left a hole: 190 of them in six days, 12 s the median, twelve
of 5 to 106 minutes (the server off for its resize on 10 Nov; Helius refusing our key for 33 minutes that evening).
LaserStream replays from a slot (two days back; measured on 11 Nov: a replay begins exactly at the slot asked for, in
order, twenty minutes of chain in 28 s, and a slot's tick never comes before its transactions). The PumpSwap side has
resumed that way since 7 Nov; the tape's own stream started live every time.

Now the tape's stream asks for slot ticks, remembers the newest slot handed over whole (also in the tape's record,
with the chain clock, so a new process knows it), and a new subscription replays from there: the stretch of the tape
goes on without a hole. While it catches up, the record claims the tape only up to where the stream stands on the
chain, never up to the wall clock; a stream that goes silent is restarted; a replay the server refuses starts at the
oldest slot it still has, or live, and the hole is written down.
"""

import asyncio
import contextlib
import time

import pytest

from snipetech import live as L
from snipetech.solana import laserstream as LS
from snipetech.solana.programs import LAUNCHLAB_PROGRAM, PUMP_PROGRAM
from snipetech.solana.tx import RawEvent, TxEvents
from snipetech.store import Store
from tests.synth import addr
from tests.test_live_amm import (
    A1_SLOT,
    A1_TS,
    P2_MINT,
    FakeLaserStream,
    load,
    needs_grpc,
    pump_tx,
    settle,
    until,
    watcher,  # fmt: skip
)

pytestmark = needs_grpc


def p2(slot: int = A1_SLOT, n: int = 0):
    """p2.bin, a pump.fun curve sell (its event's timestamp is A1_TS), as another transaction in another slot."""
    u = load("p2.bin")
    if slot != A1_SLOT or n:
        u.transaction.slot = slot
        sig = (n + 1).to_bytes(64, "little")
        u.transaction.transaction.signature = sig
        u.transaction.transaction.transaction.signatures[0] = sig  # the one a trade is recorded under
    return u


def tick(slot: int):
    from snipetech.solana.ys import geyser_pb2

    return geyser_pb2.SubscribeUpdate(slot=geyser_pb2.SubscribeUpdateSlot(slot=slot, status=1))


def recorder(store: Store | None = None, **kw):
    """The tape recorder over LaserStream, its clock a moment after a1's block, its reconnects immediate."""
    w = watcher(store=store or Store(":memory:"), **kw)
    w.firehose = True
    w.clock = lambda: A1_TS + 1.0
    return w


def drop(sub: dict, why: str = "stream dropped") -> None:
    sub["end"].set_exception(RuntimeError(why))


async def started(w, streams, n: int = 1):
    task = asyncio.create_task(w._laserstream())
    await until(lambda: len(streams.subs) >= n)
    return task


async def stop(task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture(autouse=True)
def _quick_reconnects(monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "test")
    monkeypatch.setattr(L, "TAPE_BACKOFF_SECS", 0.01)


def test_the_tapes_stream_asks_for_slot_ticks_and_starts_live_without_a_record(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = recorder()
        task = await started(w, streams)
        (sub,) = streams.subs
        req = sub["request"]
        f = req.transactions["tape"]
        assert list(f.account_include) == [PUMP_PROGRAM, LAUNCHLAB_PROGRAM] and f.vote is False and f.failed is False
        assert req.slots["tape"].filter_by_commitment and not req.HasField("from_slot")
        await stop(task)

    asyncio.run(go())


def test_a_dropped_stream_resumes_from_its_last_whole_slot_and_the_tape_has_no_hole(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = recorder()
        task = await started(w, streams)
        first = streams.subs[0]
        first["on_connect"]()
        span, gaps = w.span_start, list(w.gaps)
        streams.feed(first, p2(A1_SLOT))
        streams.feed(first, tick(A1_SLOT))  # slot A1_SLOT handed over whole
        streams.feed(first, p2(A1_SLOT + 1, 1))  # a transaction of the next slot, its tick never came
        drop(first)
        await until(lambda: len(streams.subs) == 2)
        second = streams.subs[1]
        assert second["request"].from_slot == A1_SLOT - L.RESUME_BACK_SLOTS
        second["on_connect"]()
        assert w.gaps == gaps and w.disconnected_at is None and w.span_start == span  # the same stretch goes on
        # the replay hands the old slots over again: recorded once
        streams.feed(second, p2(A1_SLOT))
        streams.feed(second, tick(A1_SLOT))
        streams.feed(second, p2(A1_SLOT + 1, 1))
        streams.feed(second, p2(A1_SLOT + 1, 2))  # one the first subscription never sent
        streams.feed(second, tick(A1_SLOT + 1))
        w._flush()
        assert w.stats["tape_replay_dups"] == 2 and w.stats["laserstream_txs"] == 3
        assert len(w.store.trades_for_mint(P2_MINT)) == 3
        assert w.tape_slot == A1_SLOT + 1
        await stop(task)

    asyncio.run(go())


def test_a_new_process_goes_on_from_the_slot_the_last_one_wrote_down(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        w = recorder(st)
        task = await started(w, streams)
        first = streams.subs[0]
        first["on_connect"]()
        w.span_start = A1_TS - 100  # recording since before a1's block
        streams.feed(first, p2(A1_SLOT))
        streams.feed(first, tick(A1_SLOT))
        w.clock = lambda: A1_TS + 20.0
        streams.feed(first, p2(A1_SLOT + 50, 1))
        streams.feed(first, tick(A1_SLOT + 50))
        w._flush()
        w._mark_tape()
        await stop(task)
        cov = st.coverage("tape")
        assert cov["slot"] == A1_SLOT + 50 and cov["head"] == [A1_SLOT + 50, A1_TS]
        (old,) = cov["spans"]

        # the restart, two minutes later
        w2 = recorder(st)
        w2.clock = lambda: A1_TS + 140.0
        task = await started(w2, streams, 2)
        second = streams.subs[1]
        assert second["request"].from_slot == A1_SLOT + 50 - L.RESUME_BACK_SLOTS
        second["on_connect"]()
        assert w2.gaps == [] and w2.span_start == old[0]
        assert w2.head == (A1_SLOT + 50, A1_TS)  # the chain clock from the record: the stream stands there, not at now
        streams.feed(second, p2(A1_SLOT + 50, 1))  # written by the first process: not recorded again
        streams.feed(second, p2(A1_SLOT + 60, 2))
        streams.feed(second, tick(A1_SLOT + 60))
        w2._flush()
        w2._mark_tape()
        assert w2.stats["tape_replay_dups"] == 1
        (span,) = st.coverage("tape")["spans"]  # one stretch, across the restart
        assert span[0] == old[0] and span[1] >= old[1]
        await stop(task)

    asyncio.run(go())


def test_a_record_too_old_to_replay_starts_live_with_a_new_stretch(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        st.set_coverage("tape", {"spans": [[A1_TS - 9000, A1_TS - 8000]], "slot": A1_SLOT, "head": [A1_SLOT, A1_TS]})
        w = recorder(st)
        w.clock = lambda: A1_TS + L.REPLAY_MAX_AGE_SECS + 9000.0
        task = await started(w, streams)
        assert not streams.subs[0]["request"].HasField("from_slot")
        streams.subs[0]["on_connect"]()
        assert w.span_start is not None and w.span_start > A1_TS
        await stop(task)

    asyncio.run(go())


def test_a_replay_the_server_refuses_starts_at_the_oldest_slot_it_has_and_the_hole_is_a_hole(monkeypatch):
    refusal = ("<AioRpcError of RPC that terminated with: status = StatusCode.OUT_OF_RANGE details = \"Requested slot "
               f"{A1_SLOT - 2} is older than the oldest available slot {A1_SLOT + 5000}. Please request a more recent "
               "slot.\"")  # fmt: skip

    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        st.set_coverage("tape", {"spans": [[A1_TS - 900, A1_TS]], "slot": A1_SLOT, "head": [A1_SLOT, A1_TS]})
        w = recorder(st)
        task = await started(w, streams)
        assert streams.subs[0]["request"].from_slot == A1_SLOT - L.RESUME_BACK_SLOTS
        drop(streams.subs[0], refusal)  # before its first message
        await until(lambda: len(streams.subs) == 2)
        second = streams.subs[1]
        assert second["request"].from_slot == A1_SLOT + 5000 + L.REPLAY_FLOOR_MARGIN_SLOTS
        second["on_connect"]()
        late = A1_SLOT + 5000 + L.REPLAY_FLOOR_MARGIN_SLOTS
        streams.feed(second, p2(late, 7))  # its event says A1_TS: where the new stretch starts on the chain clock
        streams.feed(second, tick(late))
        assert w.span_start == A1_TS + 1
        w.clock = lambda: A1_TS + 31.0  # half a minute on: the stream stands at A1_TS + 30
        w.lag_due = 0.0
        w._observe_lag(pump_tx(A1_TS + 30, late + 140))
        streams.feed(second, tick(late + 140))
        w._flush()
        w._mark_tape()
        spans = st.coverage("tape")["spans"]
        assert spans == [[A1_TS - 900, A1_TS], [A1_TS + 1, A1_TS + 29]]  # the hole between them is a hole
        assert w.stats["tape_replay_refused"] == 1
        # a later drop whose replay is refused twice in a row before anything is sent: live
        drop(second)
        await until(lambda: len(streams.subs) == 3)
        assert streams.subs[2]["request"].from_slot == late + 140 - L.RESUME_BACK_SLOTS
        drop(streams.subs[2], refusal)
        await until(lambda: len(streams.subs) == 4)
        assert streams.subs[3]["request"].from_slot == A1_SLOT + 5000 + L.REPLAY_FLOOR_MARGIN_SLOTS
        drop(streams.subs[3], refusal)
        await until(lambda: len(streams.subs) == 5)
        assert not streams.subs[4]["request"].HasField("from_slot")
        await stop(task)

    asyncio.run(go())


def test_while_it_catches_up_the_record_claims_the_tape_up_to_where_the_stream_stands(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        w = recorder(st)
        w.clock = lambda: A1_TS + 600.0  # the stream hands over blocks of ten minutes ago: a replay
        task = await started(w, streams)
        sub = streams.subs[0]
        sub["on_connect"]()
        w.span_start = A1_TS - 100
        streams.feed(sub, p2(A1_SLOT))
        streams.feed(sub, tick(A1_SLOT))
        w._flush()
        w._mark_tape()
        (span,) = st.coverage("tape")["spans"]
        assert span == [A1_TS - 100, A1_TS - 1]  # not A1_TS + 598, the wall clock less two seconds
        await stop(task)

    asyncio.run(go())


def test_a_stream_that_goes_silent_is_not_claimed_and_is_restarted(monkeypatch):
    monkeypatch.setattr(L, "TAPE_STALL_SECS", 0.05)
    monkeypatch.setattr(L, "TAPE_STALL_CHECK_SECS", 0.01)

    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        w = recorder(st)
        w.clock = time.time
        task = await started(w, streams)
        sub = streams.subs[0]
        sub["on_connect"]()
        w.span_start = int(time.time()) - 100
        w.lag = 1.0
        streams.feed(sub, tick(A1_SLOT))
        w._mark_tape()
        (before,) = st.coverage("tape")["spans"]
        await until(lambda: len(streams.subs) == 2)  # no tick for TAPE_STALL_SECS: dropped and subscribed again
        assert sub["cancelled"] and streams.subs[1]["request"].from_slot == A1_SLOT - L.RESUME_BACK_SLOTS
        assert w.stats["tape_stalls"] == 1
        await stop(task)
        return before, st.coverage("tape")["spans"][-1]

    before, after = asyncio.run(go())
    assert after == before


def test_a_transaction_without_a_time_of_its_own_gets_the_chains_time_not_the_clocks(monkeypatch):
    """LaunchLab's events carry no timestamp: the transaction's time was the moment it was received, which in a replay
    is minutes or hours after its block."""
    w = recorder()
    w.clock = lambda: A1_TS + 600.0
    w.head, w.lag = (A1_SLOT, A1_TS), 600.0
    seen: list[TxEvents] = []
    monkeypatch.setattr(w, "_on_tx", lambda ev, index, amm_only=False: seen.append(ev))
    monkeypatch.setattr(L, "extract_from_transaction",
                        lambda txj: TxEvents(addr("ll"), txj["slot"], txj["blockTime"], "",
                                             [RawEvent(LAUNCHLAB_PROGRAM, "LaunchLabTradeEvent", {}, 0)]))  # fmt: skip
    w._ls_tx({"slot": A1_SLOT + 10, "blockTime": int(A1_TS + 600)}, 0)
    (ev,) = seen
    assert A1_TS <= ev.block_time <= A1_TS + 5  # ten slots after the head, about two seconds


def test_a_coins_window_closes_on_the_chains_clock_not_the_wall_clock_while_replaying():
    """In a replay a launch of ten minutes ago is new on the chain clock: its window is open until the stream has
    handed over the fifteen minutes after it."""
    from tests.test_live_batch import create

    w = recorder()
    window = w.cfg.storage.live_window_secs
    w.clock = lambda: A1_TS + 3 * window  # the wall clock: far past the coin's window
    w.lag = 3.0 * window  # the stream stands at A1_TS
    mint = addr("a replayed coin")
    w._on_tx(create(mint, A1_TS - 5), 0)
    assert mint in w.recent and w.closed_launches == []
    w.lag = 2.0 * window - 100  # the stream has handed over the window and the minute after it
    w._on_tx(create(addr("the next coin"), A1_TS + window + 95), 0)
    assert [l.mint for l in w.closed_launches] == [mint]


def test_a_recorder_that_sends_signals_never_replays(monkeypatch):
    """A replayed launch is minutes old: a signal on it would be acted on as if it were new."""

    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        st.set_coverage("tape", {"spans": [[A1_TS - 900, A1_TS]], "slot": A1_SLOT, "head": [A1_SLOT, A1_TS]})
        w = recorder(st)
        w.signals = "known"
        task = await started(w, streams)
        assert not streams.subs[0]["request"].HasField("from_slot")
        streams.subs[0]["on_connect"]()
        gaps = len(w.gaps)
        streams.feed(streams.subs[0], tick(A1_SLOT + 9))
        drop(streams.subs[0])
        await until(lambda: len(streams.subs) == 2)
        assert not streams.subs[1]["request"].HasField("from_slot")
        streams.subs[1]["on_connect"]()
        assert len(w.gaps) == gaps + 1  # what came between the two is a hole, as before
        await stop(task)

    asyncio.run(go())


def test_the_last_position_is_written_down_when_the_recorder_stops(monkeypatch):
    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        w = recorder(st)
        w.cfg.live.rescore_interval_secs = 3600  # no maintenance tick in this test: only the stop writes
        run = asyncio.create_task(w.run())
        await until(lambda: any(s["request"] is not None and "tape" in s["request"].transactions for s in streams.subs))
        sub = next(s for s in streams.subs if "tape" in s["request"].transactions)
        sub["on_connect"]()
        w.span_start = A1_TS - 100  # recording since before a1's block
        streams.feed(sub, p2(A1_SLOT))
        streams.feed(sub, tick(A1_SLOT))
        await settle()
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run
        return st.coverage("tape")

    cov = asyncio.run(go())
    assert cov["slot"] == A1_SLOT and len(cov["spans"]) == 1
    assert LS.SLOT_FINALIZED == 2


def test_a_server_that_turns_the_recorder_away_for_a_while_does_not_cost_it_its_place(monkeypatch):
    """10 Nov 18:17-18:51: "Unable to verify project. Please check your API key and try again" on every subscription
    for 33 minutes, then it worked again. That is no refusal of the replay: the recorder keeps the slot it stood at
    and replays from it once the server lets it in."""
    aborted = ('<AioRpcError of RPC that terminated with: status = StatusCode.ABORTED details = "Unable to verify '
               'project. Please check your API key and try again"')  # fmt: skip

    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = recorder()
        task = await started(w, streams)
        first = streams.subs[0]
        first["on_connect"]()
        span = w.span_start
        streams.feed(first, p2(A1_SLOT))
        streams.feed(first, tick(A1_SLOT))
        drop(first, aborted)
        for n in (2, 3, 4):  # turned away again and again, before any message
            await until(lambda n=n: len(streams.subs) == n)
            assert streams.subs[-1]["request"].from_slot == A1_SLOT - L.RESUME_BACK_SLOTS
            if n < 4:
                drop(streams.subs[-1], aborted)
        streams.subs[-1]["on_connect"]()
        assert w.span_start == span and w.stats["tape_replay_refused"] == 0
        await stop(task)

    asyncio.run(go())


def test_a_replay_that_begins_later_than_asked_is_a_hole_not_a_claim(monkeypatch):
    """Measured on 11 Nov, a replay begins exactly at the slot asked for. Should the server ever begin later, what lies
    between was never handed over: the stretch ends there and a new one starts."""

    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        st.set_coverage("tape", {"spans": [[A1_TS - 900, A1_TS - 1]], "slot": A1_SLOT, "head": [A1_SLOT, A1_TS]})
        w = recorder(st)
        w.clock = lambda: A1_TS + 60.0
        task = await started(w, streams)
        sub = streams.subs[0]
        assert sub["request"].from_slot == A1_SLOT - L.RESUME_BACK_SLOTS
        sub["on_connect"]()
        far = A1_SLOT + L.REPLAY_SLACK_SLOTS + 500
        streams.feed(sub, tick(far))  # its first message: far past the slot asked for
        assert w.stats["tape_replay_short"] == 1 and w.span_start is None
        streams.feed(sub, p2(far + 1, 3))  # the new stretch starts at the first block time measured
        assert w.span_start == A1_TS + 1
        w.clock = lambda: A1_TS + 91.0
        w.lag_due = 0.0
        w._observe_lag(pump_tx(A1_TS + 30, far + 150))
        streams.feed(sub, tick(far + 150))
        w._flush()
        w._mark_tape()
        assert st.coverage("tape")["spans"] == [[A1_TS - 900, A1_TS - 1], [A1_TS + 1, A1_TS + 29]]
        await stop(task)

    asyncio.run(go())


def test_after_a_restart_the_pumpswap_record_claims_nothing_from_before_this_process(monkeypatch):
    """The tape's stretch goes on across a restart (its own stream replays). The PumpSwap side of the coins that had
    left their curve before it is not replayed: this process's run of that record starts where this process did."""
    from snipetech.live import AMM_RECENT_KEY

    async def go():
        streams = FakeLaserStream(monkeypatch)
        st = Store(":memory:")
        st.set_coverage("tape", {"spans": [[A1_TS - 9000, A1_TS - 1]], "slot": A1_SLOT, "head": [A1_SLOT, A1_TS]})
        w = recorder(st)
        w.amm_source = "laserstream"
        w.clock = lambda: A1_TS + 60.0
        task = await started(w, streams)
        sub = streams.subs[0]
        sub["on_connect"]()
        assert w.span_start == A1_TS - 9000
        streams.feed(sub, p2(A1_SLOT + 1, 1))
        w.clock = lambda: A1_TS + 120.0
        w.lag_due = 0.0
        w._observe_lag(pump_tx(A1_TS + 100, A1_SLOT + 500))  # caught up to twenty seconds behind
        w._mark_amm_recent()
        await stop(task)
        return st.coverage(AMM_RECENT_KEY)["runs"]

    (run,) = asyncio.run(go())
    assert run["from"] == A1_TS and run["to"] == A1_TS + 98  # never from A1_TS - 9000, the stretch's own start


def test_the_recorder_measures_how_far_back_a_replay_starts_before_its_first_transaction(monkeypatch):
    """A replay of an outage longer than a coin's window: the first replayed launch must not look long closed because
    the lag still says two seconds, as it did before the drop."""

    async def go():
        streams = FakeLaserStream(monkeypatch)
        w = recorder()
        task = await started(w, streams)
        first = streams.subs[0]
        first["on_connect"]()
        streams.feed(first, p2(A1_SLOT))
        streams.feed(first, tick(A1_SLOT))
        assert w.lag == 1.0
        drop(first)
        w.clock = lambda: A1_TS + 2000.0  # the server is back after half an hour
        await until(lambda: len(streams.subs) == 2)
        streams.subs[1]["on_connect"]()
        assert w.lag >= 1990.0
        await stop(task)

    asyncio.run(go())


# -- the valve during a catch-up ---------------------------------------------------------------------------------------


def test_the_valve_stays_open_while_a_replay_catches_up():
    """11 Nov 07:23 UTC: after an 11-minute standstill the tape's stream replayed what it had missed, 660 s behind the
    chain and gaining minutes a minute. The valve read "behind for 30 s" as "too slow", closed, and the PumpSwap side
    threw away its own replay of the same stretch: a 12-minute hole there (07:12:49-07:25:27), the only one the
    standstill left. A lag that shrinks faster than the clock runs is a recorder catching up, not one that cannot
    keep up: nothing is shed."""
    from snipetech.live import LagValve

    v = LagValve(lag_secs=20, sustain_secs=30, reopen_secs=10)
    t, lag, changes = 1000.0, 660.0, []
    while lag > 0:  # the replay runs at five times the chain's pace: the lag falls four seconds a second
        changes.append(v.observe(lag, t))
        t, lag = t + 1, lag - 4
    assert changes.count(None) == len(changes) and v.is_open


def test_the_valve_still_closes_on_a_recorder_that_gains_too_slowly_or_not_at_all():
    from snipetech.live import LagValve

    v = LagValve(lag_secs=20, sustain_secs=30, reopen_secs=10)
    seen = [v.observe(300 - 0.2 * k, 1000 + k) for k in range(31)]  # a fifth of a second a second: overloaded
    assert seen[-1] == "closed" and not v.is_open
    v = LagValve(lag_secs=20, sustain_secs=30, reopen_secs=10)
    seen = [v.observe(25 + k, 1000 + k) for k in range(31)]  # falling further behind
    assert seen[-1] == "closed"
    # a catch-up that stalls half-way closes it one sustain window after it stalled
    v = LagValve(lag_secs=20, sustain_secs=30, reopen_secs=10)
    for k in range(60):
        assert v.observe(660 - 4 * k, 1000 + k) is None
    stalled = [v.observe(420, 1060 + k) for k in range(61)]
    assert "closed" in stalled and stalled.index("closed") <= 60
