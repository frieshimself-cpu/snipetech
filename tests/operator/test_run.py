import asyncio
import json
import os
import sys

import pytest

from snipetech import ops as O
from snipetech.cli import build_parser
from snipetech.config import Config
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.run import operate
from snipetech.store import Store

NOW = 1_793_923_200 + 3600
ALL_ACTIVE = {"snipetech-tape": "active", "snipetech-ops": "active"}


@pytest.fixture
def tmp_env(tmp_path):
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")

    class Svc:
        store = st

        async def oktech_status(self):
            return {"snipers": [{"id": "a", "name": "x", "status": "idle"}]}

    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = "W"
    calls = []

    def runner(*a, **k):  # stands in for run_turn; must never be reached on a quiet round
        calls.append((a, k))
        return {"usd": 0.0, "tool_calls": 0, "stopped": "end_turn", "notes": 0}

    kw = dict(repo=tmp_path, branch="main", now=lambda: NOW, services=lambda: dict(ALL_ACTIVE), balances=lambda w: (1.0, 0.0, []),
              turn=runner)  # fmt: skip
    yield dict(cfg=cfg, svc=Svc(), paths=paths, calls=calls, kw=kw)
    st.close()


def _op(env, **over):
    kw = env["kw"] | over
    return asyncio.run(operate(env["cfg"], env["svc"], **kw))


def test_second_instance_exits_locked(tmp_env):
    tmp_env["paths"].lock.write_text(str(os.getpid()))
    assert _op(tmp_env, dry_run=True) == 0
    assert "locked" in tmp_env["paths"].log.read_text() and tmp_env["calls"] == []


def test_stale_lock_is_broken(tmp_env):
    tmp_env["paths"].lock.write_text("1")
    os.utime(tmp_env["paths"].lock, (0, 0))
    assert _op(tmp_env, dry_run=True) == 0
    log = tmp_env["paths"].log.read_text()
    assert "locked" not in log and ("quiet" in log or "call" in log) and not tmp_env["paths"].lock.exists()


def test_quiet_round_calls_no_runner(tmp_env):
    _op(tmp_env)
    _op(tmp_env)
    assert tmp_env["calls"] == [] and tmp_env["paths"].snapshot_prev.exists()
    assert tmp_env["paths"].log.read_text().count("quiet") == 2


def test_changed_round_calls_runner(tmp_env):
    _op(tmp_env)
    _op(tmp_env, balances=lambda w: (2.0, 0.0, []))  # equity +1 SOL
    assert len(tmp_env["calls"]) == 1 and "call operator" in tmp_env["paths"].log.read_text()


def test_dry_run_prints_and_never_calls(tmp_env, capsys):
    _op(tmp_env)
    _op(tmp_env, dry_run=True, balances=lambda w: (2.0, 0.0, []))
    out = capsys.readouterr().out
    assert '"equity"' in out and '"reasons"' in out and tmp_env["calls"] == []


def test_analyst_flag_forces_analyst_mode(tmp_env):
    _op(tmp_env, analyst=True)
    assert tmp_env["calls"] and tmp_env["calls"][0][0][4].mode == "analyst"


def test_budget_note_once_per_day(tmp_env):
    tmp_env["paths"].spend.write_text(json.dumps({"ts": NOW - 10, "mode": "operator", "usd": 15.0}) + "\n")
    _op(tmp_env)
    _op(tmp_env, balances=lambda w: (2.0, 0.0, []))
    _op(tmp_env, balances=lambda w: (3.0, 0.0, []))
    assert tmp_env["paths"].notes.read_text().count("budget reached") == 1 and tmp_env["calls"] == []


def test_cli_has_operate_and_ops_allows_it():
    assert "operate" in O.ALLOWED
    a = build_parser().parse_args(["operate", "--dry-run"])
    assert a.dry_run and not a.analyst and not a.telegram_setup
    assert build_parser().parse_args(["operate", "--analyst", "--telegram-setup"]).telegram_setup
