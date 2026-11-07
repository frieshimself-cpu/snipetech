"""One lab on several cores (7 Nov: the server was resized to more CPUs; a full search on 24 h of tape ran 1.5-2 h on
one core, and only one lab may run at a time). Its coins are spread over worker processes, each with its own read-only
connection to the same database file, and the rows come back in the serial lab's order: the same numbers, sooner."""

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import snipetech
from snipetech import store as S
from snipetech.analysis import lab as L
from snipetech.analysis import paper
from snipetech.analysis.lab import collect, exit_set, label_groups, menu_exits
from snipetech.config import Config
from snipetech.operator.research import recent_tape
from snipetech.store import Store

from .synth import Market, addr
from .test_lab import _shared_coins, whale_world

NOW = int(time.time())
PUMP_QUOTE = "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"
SRC = Path(snipetech.__file__).resolve().parents[1]


def _hunt_coins(store: Store, n: int = 24) -> None:
    """Launches the OKTech hunt trigger reads differently (tests/test_lab_hunt.py): the creator's own buy counted, a
    creator over the hunt's cap, a crowd past its wallet limit, a whale after the window, a coin quoted in another
    token."""
    dev_sol = (0.0, 0.5, 1.0, 3.95)
    for k in range(n):
        quote = PUMP_QUOTE if k % 8 == 7 else ""
        m = Market(store, addr(f"par-hunt-{k}"), addr(f"par-hunt-dev-{k}"), NOW - 5800 + k * 190, 60_000_000 + k * 1000,
                   quote_mint=quote)  # fmt: skip
        if dev_sol[k % 4]:
            m.buy(m.deployer, dev_sol[k % 4], 0, sig=m.create_sig)
        for j in range(k % 5):
            m.buy(addr(f"par-hunt-crowd-{k}-{j}"), 0.2 + 0.1 * j, 1)
        m.buy(addr(f"par-hunt-whale-{k}"), 1.5 + (k % 3), 2 if k % 6 else 10)
        for j in range(5):
            m.buy(addr(f"par-hunt-fan-{k}-{j}"), 0.3, 11 + j)
        m.sell(addr(f"par-hunt-whale-{k}"), 1.0, 30 + k % 7)
        m.commit()


def _world(store: Store) -> None:
    """76 coins on two tape spans: whale coins, hunt coins, and coins two candidates share (each candidate's own first
    buy is a moment of the qualify lab), with a trending event on every third whale coin. Every whale is a candidate
    too, so the qualify lab has work on every coin."""
    whale_world(store)
    _hunt_coins(store)
    _shared_coins(store)
    for who in [f"lab-whale-{i}" for i in range(40)] + [f"par-hunt-whale-{k}" for k in range(24)]:
        store.set_label(addr(who), f"cand:{who}", "cand")
    store.add_events([{"ts": NOW - 5000 + i * 60 + 2, "slot": 20_000_000 + i * 1000 + 5, "kind": "trending",
                       "mint": addr(f"lab-{i}"), "wallet": None, "data": {"rank": 1}, "source": "test"}
                      for i in range(0, 40, 3)])  # fmt: skip
    store.set_coverage("tape", {"spans": [[NOW - 6100, NOW - 3300], [NOW - 3290, NOW]]})


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    return cfg


def _search(store: Store, cfg: Config, workers: int | None) -> dict:
    """The strategy search: the newest tape through `recent_tape`'s window, launch rules where a hunt buy lands."""
    return collect(recent_tape(store, hours=1.5), cfg, 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS,
                   event_kinds=("trending",), exits=exit_set()[:6], workers=workers)  # fmt: skip


def _qualify(store: Store, cfg: Config, workers: int | None) -> dict:
    """The qualify lab: candidates' first buys, every exit of the menu, the labels passed in."""
    kols = label_groups(store)
    return collect(store, cfg, 0.2, 0, "+6", groups=("cand",), exits=list(menu_exits(cfg).values()), kols=kols,
                   workers=workers)  # fmt: skip


def _events(store: Store, cfg: Config, workers: int | None) -> dict:
    """The event-expectancy lab: each event kind as a trigger, no wallet group."""
    return collect(store, cfg, 0.2, 0, "+6", groups=(), event_kinds=("trending",), workers=workers)


@pytest.fixture(scope="module")
def world_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("lab-parallel") / "snipetech.db"
    store = Store(path)
    _world(store)
    store.close()
    return path


@pytest.mark.parametrize(
    "lab,kinds",
    [(_search, {"launch", "burst", "whale", "hunt", "kol", "event"}), (_qualify, {"kol"}), (_events, {"event"})],
    ids=["search", "qualify", "events"],
)
def test_a_lab_on_two_workers_gives_exactly_the_serial_lab(world_db, lab, kinds):
    store = Store(world_db)
    try:
        cfg = _cfg()
        serial = lab(store, cfg, 1)
        parallel = lab(store, cfg, 2)
    finally:
        store.close()
    assert serial["workers"] == 1 and parallel["workers"] == 2
    assert {serial["triggers"][r["trigger"]].kind for r in serial["rows"]} == kinds  # every kind of moment it scores
    assert len({r["mint"] for r in serial["rows"]}) > 10 and len(serial["slot_secs"]) == 2  # many coins, both spans
    assert parallel["rows"] == serial["rows"]  # the same moments in the same order, every pnl float the same
    for key in ("tape_hours", "slot_secs", "landing_of", "exits", "triggers"):
        assert parallel[key] == serial[key], key


def test_the_worker_count_comes_from_the_config_by_default(world_db):
    store = Store(world_db)
    try:
        cfg = _cfg()
        cfg.lab.workers = 2
        assert _events(store, cfg, None)["workers"] == 2
        cfg.lab.workers = 1
        assert _events(store, cfg, None)["workers"] == 1
    finally:
        store.close()


def test_the_config_file_sets_the_lab_workers(tmp_path):
    p = tmp_path / "snipetech.toml"
    p.write_text("[lab]\nworkers = 3\n")
    assert L.lab_workers(Config.load(p, overlay=False).lab.workers, cpus=64) == 3
    # the default: every CPU but two, so the tape recorder and the services keep theirs
    assert L.lab_workers(Config().lab.workers, cpus=6) == 4


def test_auto_workers_leave_two_cpus_and_stop_at_twelve():
    assert [L.lab_workers(0, cpus=c) for c in (1, 2, 3, 4, 6, 14, 64)] == [1, 1, 1, 2, 4, 12, 12]
    assert L.lab_workers(0, cpus=None) >= 1
    assert L.lab_workers(1, cpus=64) == 1 and L.lab_workers(5, cpus=2) == 5  # a set number is kept as it is


def test_an_in_memory_store_runs_the_lab_serially_whatever_the_workers():
    store = Store(":memory:")  # no file another process could open
    _world(store)
    cfg = _cfg()
    asked = _events(store, cfg, 4)
    assert asked["workers"] == 1
    assert asked["rows"] and asked["rows"] == _events(store, cfg, 1)["rows"]


def test_a_small_lab_runs_in_one_process(tmp_path):
    store = Store(tmp_path / "small.db")
    try:
        whale_world(store, n=20)  # a pool's start costs more than twenty coins of work
        assert collect(store, _cfg(), 0.2, 0, "+6", exits=exit_set()[:3], workers=4)["workers"] == 1
    finally:
        store.close()


def test_a_tape_copy_renamed_over_the_labs_file_is_never_read(tmp_path):
    """snapshot-db renames a fresh copy over snipetech.ro.db every hour while a lab may be reading the old one. Workers
    opened on the path would read the new copy beside the plan made from the old one: the lab stays in its own process
    and reads only the file it opened."""
    path = tmp_path / "snipetech.ro.db"
    writer = Store(path)
    _world(writer)
    writer.close()
    cfg = _cfg()
    lab = Store(path)
    before = _events(lab, cfg, 1)
    newer = tmp_path / "snipetech.ro.tmp.db"
    shutil.copy(path, newer)
    con = sqlite3.connect(newer)
    con.execute("DELETE FROM trades WHERE rowid % 3 = 0")  # the newer copy reads differently
    con.commit()
    con.close()
    os.replace(newer, path)
    try:
        after = _events(lab, cfg, 2)
    finally:
        lab.close()
    assert after["workers"] == 1
    assert after["rows"] == before["rows"]


def test_chunks_a_worker_refuses_are_scored_in_the_labs_own_process(world_db, monkeypatch, capfd):
    """A worker that finds another file at the path than the one the lab planned on scores nothing; its chunks are
    scored in the lab's own process, in their place."""
    store = Store(world_db)
    try:
        cfg = _cfg()
        serial = _events(store, cfg, 1)
        real = L._shared_file
        monkeypatch.setattr(L, "_shared_file", lambda st: (real(st)[0], (0, 0)))  # an identity no worker can match
        refused = _events(store, cfg, 2)
    finally:
        store.close()
    assert refused["rows"] == serial["rows"]
    assert "ran in the lab's own process" in capfd.readouterr().err


def test_a_read_only_store_reads_what_the_writer_wrote_and_never_writes(tmp_path):
    path = tmp_path / "t.db"
    writer = Store(path)
    whale_world(writer, n=3)  # in the writer's WAL, not yet checkpointed into the file
    reader = Store(path, readonly=True)
    try:
        assert reader.trades_for_mint(addr("lab-1")) == writer.trades_for_mint(addr("lab-1"))
        assert reader.launch(addr("lab-2")) == writer.launch(addr("lab-2"))
        assert reader.file_id == writer.file_id == S.file_identity(path)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.set_coverage("tape", {"spans": []})
    finally:
        reader.close()
        writer.close()
    with pytest.raises(sqlite3.OperationalError):
        Store(tmp_path / "missing.db", readonly=True)
    assert not (tmp_path / "missing.db").exists()  # a reader never creates a database
    assert S.file_identity(":memory:") is None and Store(":memory:").file_id is None


def test_the_code_identity_follows_the_source_files_and_not_their_bytecode(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "sim").mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""a package"""\n')
    (pkg / "sim" / "exits.py").write_text("STOP = 1\n")
    first = snipetech.code_identity(pkg)
    assert snipetech.code_identity(pkg) == first  # the same files, the same identity
    (pkg / "sim" / "__pycache__").mkdir()
    (pkg / "sim" / "__pycache__" / "exits.cpython-313.pyc").write_bytes(b"\0")  # an import writes bytecode
    (pkg / "dash.html").write_text("<html></html>")
    assert snipetech.code_identity(pkg) == first
    (pkg / "sim" / "exits.py").write_text("STOP = 12\n")  # an update rewrote a module
    second = snipetech.code_identity(pkg)
    assert second != first
    (pkg / "sim" / "market.py").write_text("")  # and added one
    assert snipetech.code_identity(pkg) not in (first, second)
    # this process's own package: nothing has changed under it since it was imported
    assert snipetech.code_identity() == snipetech.CODE_ID


UPDATE_CHILD = '''
import importlib
import json
import sys
from pathlib import Path

from snipetech.analysis import lab as L  # a job imports the lab when it starts
from snipetech.config import Config
from snipetech.store import Store

# a commit that changes how a coin is scored (every pnl reads 0), written into the job's checkout by `remote update`
UPDATE = """

_scored_before_the_update = _coin_rows


def _coin_rows(*args, **kwargs):
    return [r | {"pnl": [0.0] * len(r["pnl"])} for r in _scored_before_the_update(*args, **kwargs)]
"""


def lab(store, workers):
    return L.collect(store, Config(), 0.2, 0, "+6", exits=L.exit_set()[:3], workers=workers)


if __name__ == "__main__":
    db, out = sys.argv[1], Path(sys.argv[2])
    lab_py = Path(L.__file__)
    store = Store(db)
    serial = lab(store, 1)
    start_pool = L._lab_pool

    def pool_after_update(*args, **kwargs):  # the update lands after the lab's own checks, as its workers start
        lab_py.write_text(lab_py.read_text() + UPDATE)
        return start_pool(*args, **kwargs)

    L._lab_pool = pool_after_update
    raced = lab(store, 2)
    L._lab_pool = start_pool
    later = lab(store, 2)  # a lab that starts after the update (its job waited on the lab lock meanwhile)
    importlib.reload(L)  # what the workers would score with: the new code
    new = lab(store, 1)
    store.close()
    out.write_text(json.dumps({
        "lab": str(lab_py), "rows": len(serial["rows"]), "new_code_scores_otherwise": new["rows"] != serial["rows"],
        "raced": [raced["workers"], raced["rows"] == serial["rows"]],
        "later": [later["workers"], later["rows"] == serial["rows"]],
    }))
'''


def test_a_lab_never_lets_its_workers_score_with_code_an_update_put_on_disk(world_db, tmp_path):
    """`remote update` resets the checkout under the jobs the bot started, which keep the code they imported
    (CLAUDE.md); a worker process imports snipetech afresh, from the new files. A lab started after the update stays in
    one process; when the update lands as its workers start, they refuse their chunks and the lab scores them itself.
    Either way the rows are the ones the job's own code gives. Run on a copy of the package, rewritten under a child
    lab process."""
    copy = tmp_path / "src"
    shutil.copytree(SRC / "snipetech", copy / "snipetech", ignore=shutil.ignore_patterns("__pycache__"))
    script = tmp_path / "update_child.py"
    script.write_text(UPDATE_CHILD)
    out = tmp_path / "out.json"
    env = os.environ | {"PYTHONPATH": str(copy)}  # the copy only: the child must not import the checkout's own files
    done = subprocess.run([sys.executable, str(script), str(world_db), str(out)], check=False, cwd=tmp_path,
                          capture_output=True, text=True, timeout=300, env=env)  # fmt: skip
    assert done.returncode == 0, done.stderr
    got = json.loads(out.read_text())
    assert Path(got["lab"]).is_relative_to(copy)  # the child ran the copy, the one the update rewrote
    assert got["rows"] > 50 and got["new_code_scores_otherwise"]
    assert got["raced"] == [2, True]  # a pool started; its workers refused every chunk; the lab scored them itself
    assert "chunks ran in the lab's own process (the snipetech code on disk" in done.stderr
    assert got["later"] == [1, True]  # no pool at all: the code on disk is not the code the job runs
    assert "lab: one process: the snipetech code on disk" in done.stderr


CHILD = """
import sys
import time
from pathlib import Path

from snipetech.analysis import lab as L
from snipetech.store import Store


def hold(marker):
    Path(marker).touch()
    time.sleep(120)


if __name__ == "__main__":
    db, marks = sys.argv[1], Path(sys.argv[2])
    store = Store(db)
    pool = L._lab_pool(db, store.file_id, None, 2)
    for k in range(2):
        pool.submit(hold, str(marks / f"w{k}"))
    time.sleep(120)
"""


def _parents() -> dict[int, int]:
    out = {}
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                out[int(d.name)] = int((d / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
    return out


def _descendants(pid: int) -> set[int]:
    parents, out, frontier = _parents(), set(), {pid}
    while frontier:
        frontier = {p for p, pp in parents.items() if pp in frontier} - out
        out |= frontier
    return out


def _alive(pid: int) -> bool:
    try:
        state = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads the process table from /proc")
def test_workers_exit_when_their_lab_is_killed(tmp_path):
    """A job's time limit sends the lab SIGTERM (coreutils timeout, ops.py), which ends it without shutting its pool
    down; a worker waiting for work would then wait forever (7 Nov probe: two workers and the forkserver were still
    there after their lab died). Each worker exits once its lab is gone."""
    db = tmp_path / "t.db"
    Store(db).close()
    marks = tmp_path / "marks"
    marks.mkdir()
    script = tmp_path / "lab_child.py"
    script.write_text(CHILD)
    env = os.environ | {"PYTHONPATH": os.pathsep.join(p for p in (str(SRC), os.environ.get("PYTHONPATH")) if p)}
    child = subprocess.Popen([sys.executable, str(script), str(db), str(marks)], env=env)
    kids: set[int] = set()
    try:
        deadline = time.monotonic() + 60
        while len(list(marks.iterdir())) < 2 and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        assert len(list(marks.iterdir())) == 2, "both workers started"
        kids = _descendants(child.pid)
        assert len(kids) >= 3  # the forkserver and the two workers
        child.send_signal(signal.SIGTERM)
        child.wait(timeout=30)
        deadline = time.monotonic() + 20
        while any(_alive(p) for p in kids) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not [p for p in kids if _alive(p)]
    finally:
        child.kill()
        child.wait()
        for p in kids:
            try:
                os.kill(p, signal.SIGKILL)
            except OSError:
                pass
