import asyncio
import json
import os

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
def tmp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # the round pre-checks the key before calling the turn
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


def test_a_loop_stage_ending_between_rounds_keeps_the_round_quiet(tmp_env):
    """The stand-in starts measure every round; its clean end must not wake the paid operator every 15 minutes."""
    _op(tmp_env)
    meta = {"id": "m1", "argv": ["measure", "run"], "running": False, "started": NOW - 60, "ended": NOW, "exit_code": 0}
    (tmp_env["paths"].jobs / "m1.json").write_text(json.dumps(meta))
    _op(tmp_env)
    assert tmp_env["calls"] == []


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


def _watch_env(tmp_path, monkeypatch):
    """A DTAU-shaped coin held by the book wallet, bought by a sniper OKTech reports finished (tests/test_book_watch)."""
    from tests.test_book_watch import FINISHED, G, W, _held, _sniper, dtau

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    _sniper(st, G)
    m, ours = dtau(st)

    class Svc:  # what the one-minute pass may use: holdings and OKTech's task list; nothing that trades
        store = st

        def __init__(self):
            self.held, self.status_calls = [_held(m, ours)], 0

        async def open_positions(self, address, ignore=()):
            assert address == W
            return list(self.held)

        async def oktech_status(self):
            self.status_calls += 1
            return {"snipers": FINISHED}

    return paths, st, cfg, Svc(), m, ours


def test_the_one_minute_pass_alerts_on_a_coin_nobody_is_selling(tmp_path, monkeypatch):
    """`operate --commands` runs the watchdog after the owner's commands: no Claude, OKTech asked only while a coin is
    held, one Telegram line per coin per 15 minutes. It never sells: the service it gets cannot."""
    from snipetech.operator.run import commands_pass

    paths, st, cfg, svc, m, ours = _watch_env(tmp_path, monkeypatch)
    sent: list[str] = []

    def run(t):
        return asyncio.run(commands_pass(cfg, svc, now=lambda: t, send=lambda x: sent.append(x) or True,
                                         commands_get=lambda url, params: {"result": []}))  # fmt: skip

    try:
        assert run(ours.ts + 30) == 0 and sent == []  # bought 30 s ago: its dead exit was due, OKTech has its minute
        assert run(ours.ts + 70) == 0 and len(sent) == 1 and m.mint in sent[0] and "Nobody is selling" in sent[0]
        run(ours.ts + 130)
        assert len(sent) == 1  # a minute later: not repeated
        run(ours.ts + 70 + 15 * 60)
        assert len(sent) == 2  # 15 minutes on and still held: said again
        svc.held, calls = [], svc.status_calls
        run(ours.ts + 2000)
        assert len(sent) == 2 and svc.status_calls == calls  # nothing held: OKTech is not even asked
    finally:
        st.close()
    assert "watch: " in paths.log.read_text()


def test_a_watch_that_cannot_read_the_wallet_never_breaks_the_kill_switch(tmp_path, monkeypatch):
    from snipetech.operator.run import commands_pass

    paths, st, cfg, svc, _m, ours = _watch_env(tmp_path, monkeypatch)

    async def broken(address, ignore=()):
        raise RuntimeError("rpc down")

    svc.open_positions = broken
    try:
        rc = asyncio.run(commands_pass(cfg, svc, now=lambda: ours.ts + 70, send=lambda x: True,
                                       commands_get=lambda url, params: {"result": []}))  # fmt: skip
    finally:
        st.close()
    assert rc == 0 and "watch: skipped: RuntimeError: rpc down" in paths.log.read_text()
