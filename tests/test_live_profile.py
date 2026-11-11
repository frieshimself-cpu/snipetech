"""The recorder profiles itself when asked (11 Nov).

Its work record says 2.7 ms of processor an update on the server; handling one costs 0.1-0.2 ms on the dev box, parsing
it 5-10 us. Nothing outside the process can say where the rest goes, and nothing may be installed on the server. A file
`inputs/profile-tape` in the data directory (`remote put`; its text: the seconds, 60 when empty, 10 to 300) makes the
recorder profile its event loop for that long and write the result to `jobs/tape-profile-<time>.log` (`remote read`).
Without the file nothing is profiled and nothing is written.
"""

import pytest

from snipetech.solana import laserstream as LS
from snipetech.store import Store
from tests.test_live_amm import load, watcher

pytestmark = pytest.mark.skipif(not LS.available(), reason="grpc/protobuf not installed")


def _recorder(tmp_path):
    st = Store(tmp_path / "snipetech.db")
    w = watcher(store=st)
    w.cfg.storage.db_path = str(tmp_path / "snipetech.db")
    now = [1_794_380_000.0]
    w.clock = lambda: now[0]
    return st, w, now


def test_without_the_file_nothing_is_profiled(tmp_path):
    st, w, now = _recorder(tmp_path)
    w._profile_tick()
    now[0] += 600
    w._profile_tick()
    assert w.profiler is None and not (tmp_path / "jobs").exists()
    st.close()


def test_the_file_starts_a_profile_and_the_result_is_written_when_its_time_is_up(tmp_path):
    st, w, now = _recorder(tmp_path)
    flag = tmp_path / "inputs" / "profile-tape"
    flag.parent.mkdir()
    flag.write_text("20\n")
    w._profile_tick()
    assert w.profiler is not None and not flag.exists()  # taken: one profile for one file
    p2 = load("p2.bin")
    for _ in range(50):
        LS.dispatch(p2, w._ls_tx, lambda info, slot: w._ls_tx)  # the recorder's work, on this thread
    now[0] += 19
    w._profile_tick()
    assert w.profiler is not None and not list((tmp_path / "jobs").glob("tape-profile-*.log"))
    now[0] += 1
    w._profile_tick()
    assert w.profiler is None
    (out,) = (tmp_path / "jobs").glob("tape-profile-*.log")
    text = out.read_text()
    assert out.name == f"tape-profile-{int(now[0])}.log"
    assert "20 s of the recorder's event loop" in text
    assert "extract_from_transaction" in text and "to_rpc_json" in text  # where its time went, by function
    assert "tottime" in text and "cumulative" in text  # both orders
    st.close()


def test_the_time_asked_for_is_kept_between_ten_seconds_and_five_minutes(tmp_path):
    st, w, now = _recorder(tmp_path)
    flag = tmp_path / "inputs" / "profile-tape"
    flag.parent.mkdir()
    for text, secs in (("", 60.0), ("3", 10.0), ("9000", 300.0), ("not a number", 60.0)):
        flag.write_text(text)
        w._profile_tick()
        assert w.profile_until == now[0] + secs, text
        now[0] += secs
        w._profile_tick()
        assert w.profiler is None
    st.close()
