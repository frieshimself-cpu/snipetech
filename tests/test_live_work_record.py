"""The recorder writes down its own work once a minute (coverage `tape:work`).

11 Nov: handling a transaction fell from 771 to 105 us on the dev box, and the server's recorder from 5.5 to 2.9 ms of
processor a trade recorded. Where the remaining 2.9 ms go could not be seen from outside: the counters went to a log
line at a level the service does not print, and nothing counted the updates the two streams hand over, only the ones
kept. Two readings of the record a while apart give the updates a second of each stream, the share dropped, the rows
written, the seconds spent in database calls and the processor seconds per update.
"""

import pytest

from snipetech.live import WORK_EVERY_SECS, WORK_KEY
from snipetech.solana import laserstream as LS
from snipetech.store import Store
from tests.test_live_amm import load, watcher

pytestmark = pytest.mark.skipif(not LS.available(), reason="grpc/protobuf not installed")


def _slot(n: int):
    from snipetech.solana.ys import geyser_pb2

    return geyser_pb2.SubscribeUpdate(slot=geyser_pb2.SubscribeUpdateSlot(slot=n, status=geyser_pb2.SLOT_CONFIRMED))


def _recorder(tmp_path):
    st = Store(tmp_path / "t.db")
    w = watcher(store=st)
    now = [1_794_380_000.0]
    w.clock = lambda: now[0]
    w.meter.cpu = lambda: 12.34
    return st, w, now


def test_the_work_record_counts_what_each_stream_handed_over_and_what_was_written(tmp_path):
    st, w, now = _recorder(tmp_path)
    w.started_at = now[0] - 300
    p2 = load("p2.bin")
    slot = p2.transaction.slot
    for u in (p2, p2, _slot(slot), _slot(slot + 1)):  # the second copy is a replay's duplicate
        LS.dispatch(u, w._ls_tx, w._tape_route, w._tape_tick)
    w._flush()
    w._mark_work()
    rec = st.coverage(WORK_KEY)
    assert rec["at"] == int(now[0]) and rec["up_secs"] == 300 and rec["cpu_secs"] == 12.3
    counts = rec["counts"]
    assert counts["tape_updates"] == 2 and counts["tape_slots"] == 2 and counts["tape_replay_dups"] == 1
    assert counts["laserstream_txs"] == 1 and counts["rows_written"] == st._q("SELECT COUNT(*) FROM trades")[0][0] == 1
    assert rec["db_secs"]["flush"] >= 0.0  # the seconds the event loop sat in each kind of database call, summed
    st.close()


def test_the_work_record_is_written_once_a_minute(tmp_path):
    st, w, now = _recorder(tmp_path)
    w._mark_work()
    first = st.coverage(WORK_KEY)["at"]
    now[0] += WORK_EVERY_SECS - 1
    w._mark_work()
    assert st.coverage(WORK_KEY)["at"] == first
    now[0] += 1
    w._mark_work()
    assert st.coverage(WORK_KEY)["at"] == first + WORK_EVERY_SECS
    st.close()


def test_the_pumpswap_side_counts_its_updates_too(tmp_path):
    st, w, _ = _recorder(tmp_path)
    a1 = load("a1.bin")
    LS.dispatch(a1, w._ls_amm, w._amm_route, w._amm_slot)  # no recent coin's pool: dropped, but handed over
    LS.dispatch(_slot(a1.transaction.slot), w._ls_amm, w._amm_route, w._amm_slot)
    w._mark_work()
    counts = st.coverage(WORK_KEY)["counts"]
    assert counts["amm_updates"] == 1 and counts["amm_slots"] == 1 and counts["amm_dropped"] == 1
    st.close()
