"""Telegram commands: owner-only parsing, offset persistence, /status, /stop that never strands a coin, /resume."""

import asyncio
import json

from snipetech.config import Config
from snipetech.operator.commands import Command, handle_command, parse_updates, poll_commands
from snipetech.operator.gate import expected_state
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import Snapshot
from snipetech.operator.tools import NoteSink

NOW = 1_793_923_200 + 3600
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


def _msg(uid, chat, text):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": "private"}, "text": text}}


def test_parse_only_owner_commands():
    ups = [
        _msg(1, 7, "/stop"),
        _msg(2, 9, "/stop"),
        _msg(3, 7, "/why W1 extra"),
        _msg(4, 7, "hello there"),
        _msg(5, 7, "/status@snipetech_bot"),
    ]
    cmds, last = parse_updates(ups, owner_chat_id=7)
    assert [(c.name, c.args) for c in cmds] == [("stop", []), ("why", ["W1", "extra"]), ("status", [])]
    assert last == 5 and cmds[0].chat_id == 7 and cmds[0].update_id == 1


def test_commands_from_other_chats_are_ignored():
    cmds, last = parse_updates(
        [_msg(10, 9, "/stop"), {"update_id": 11, "edited_message": {"chat": {"id": 9}, "text": "/stop"}}],
        owner_chat_id=7,
    )
    assert cmds == [] and last == 11
    assert parse_updates([], owner_chat_id=7) == ([], None)


def test_poll_persists_offset_and_survives_failure(tmp_path):
    paths = OperatorPaths(tmp_path).ensure()
    paths.telegram.write_text(json.dumps({"chat_id": 7}))
    seen = []

    def get(url, params):
        seen.append(params)
        return {"result": [_msg(41, 7, "/status")]}

    cmds = poll_commands("T", paths, get=get)
    assert [c.name for c in cmds] == ["status"] and seen[0].get("offset") is None
    saved = json.loads(paths.telegram.read_text())
    assert saved == {"chat_id": 7, "offset": 42}
    assert poll_commands("T", paths, get=lambda u, p: (_ for _ in ()).throw(TimeoutError())) == []
    assert json.loads(paths.telegram.read_text())["offset"] == 42  # a failed poll never moves the offset
    poll_commands("T", paths, get=get)
    assert seen[-1]["offset"] == 42  # the next poll asks from the saved offset
    assert poll_commands("T", OperatorPaths(tmp_path / "nochat").ensure(), get=get) == []  # no chat id yet → nothing


class FakeSvc:
    def __init__(self, held, snipers):
        self._held, self._snipers, self.calls = held, snipers, []

    async def open_positions(self, address):
        return self._held

    async def oktech_status(self):
        return {"snipers": self._snipers}

    async def oktech_task(self, action, task_id):
        self.calls.append((action, task_id))
        return {"ok": True}


def _ctx(tmp_path, svc, snap=None):
    paths = OperatorPaths(tmp_path / "data").ensure()
    cfg = Config()
    cfg.operator.wallet = W
    sent = []
    notifier = Notifier("T", 7, post=lambda url, data: sent.append(data["text"]))
    notes = NoteSink(paths, now=lambda: NOW)
    return {
        "svc": svc,
        "cfg": cfg,
        "paths": paths,
        "snap": snap,
        "notifier": notifier,
        "notes": notes,
        "now": NOW,
    }, sent


def _cmd(name, *args):
    return Command(name=name, args=list(args), chat_id=7, update_id=1)


def test_stop_stops_idle_wallet_snipers_and_writes_the_flag(tmp_path):
    svc = FakeSvc(
        held=[], snipers=[{"id": "s1", "name": "a", "status": "running"}, {"id": "s2", "name": "b", "status": "idle"}]
    )
    ctx, sent = _ctx(tmp_path, svc)
    reply = asyncio.run(handle_command(_cmd("stop"), **ctx))
    assert svc.calls == [("stop", "s1")] and "s1" in reply and ctx["paths"].stop_flag.exists()
    assert sent == [reply] and "stopped by owner" in ctx["paths"].notes.read_text()


def test_stop_keeps_holders_running(tmp_path):
    svc = FakeSvc(held=[{"mint": "M1", "amount": 5.0}], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    ctx, sent = _ctx(tmp_path, svc)
    reply = asyncio.run(handle_command(_cmd("stop"), **ctx))
    assert svc.calls == [] and "s1" in reply and "M1"[:4] in reply and "held" in reply.lower()
    assert ctx["paths"].stop_flag.exists()  # the flag still stops new snipers from being expected


def test_stop_treats_a_starting_sniper_as_running(tmp_path):
    """OKTech's statuses are idle / starting / running / stopping / finished / failed; a sniper that is starting is
    about to buy and must be stopped by /stop like a running one."""
    svc = FakeSvc(
        held=[],
        snipers=[{"id": "s1", "name": "a", "status": "starting"}, {"id": "s2", "name": "b", "status": "finished"}],
    )
    ctx, _ = _ctx(tmp_path, svc)
    reply = asyncio.run(handle_command(_cmd("stop"), **ctx))
    assert svc.calls == [("stop", "s1")] and "s1" in reply


def test_stop_when_holdings_cannot_be_read_stops_nothing(tmp_path):
    class Broken(FakeSvc):
        async def open_positions(self, address):
            raise RuntimeError("rpc down")

    svc = Broken(held=[], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    ctx, _ = _ctx(tmp_path, svc)
    reply = asyncio.run(handle_command(_cmd("stop"), **ctx))
    assert svc.calls == [] and "could not" in reply.lower()


def test_resume_removes_flag_and_status_reports(tmp_path):
    svc = FakeSvc(held=[{"mint": "M1", "amount": 1}], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    snap = Snapshot(ts=NOW - 600, wallet=W, native_sol=1.25, wsol=0.0, held=[{"mint": "M1"}], snipers=svc._snipers, spent_today=0.42,
                    resources=[{"name": "disk", "status": "crit", "message": "5 GB free"}, {"name": "sol_float", "status": "ok", "message": "fine"}])  # fmt: skip
    ctx, sent = _ctx(tmp_path, svc, snap)
    ctx["paths"].stop_flag.write_text("x")
    ctx["paths"].expected.write_text(json.dumps({"snipers_running": 6, "held": 3}))  # a live test's expectation
    reply = asyncio.run(handle_command(_cmd("resume"), **ctx))
    assert not ctx["paths"].stop_flag.exists() and "resum" in reply.lower()
    assert expected_state(ctx["paths"]) == {"snipers_running": 0, "held": 0}  # /resume also forgets the old expectation
    reply = asyncio.run(handle_command(_cmd("status"), **ctx))
    assert "1.250" in reply and "0.42" in reply and "1 running" in reply and "disk" in reply and "fine" not in reply
    assert "book: off" in reply and "10 min" in reply
    assert "later phase" in asyncio.run(handle_command(_cmd("why", "W1"), **ctx))
    assert "/status" in asyncio.run(handle_command(_cmd("bogus"), **ctx))


def test_stop_flag_makes_zero_snipers_expected(tmp_path):
    paths = OperatorPaths(tmp_path).ensure()
    paths.expected.write_text(json.dumps({"snipers_running": 2, "held": 1}))
    assert expected_state(paths)["snipers_running"] == 2
    paths.stop_flag.write_text(str(NOW))
    assert expected_state(paths)["snipers_running"] == 0 and expected_state(paths)["held"] == 1


def test_round_handles_a_stop_command_before_the_snapshot(tmp_path, monkeypatch):
    from snipetech.operator.run import operate
    from snipetech.store import Store

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    paths.telegram.write_text(json.dumps({"chat_id": 7}))
    st = Store(data / "snipetech.db")
    svc = FakeSvc(held=[], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    svc.store = st
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    sent = []
    try:
        asyncio.run(operate(cfg, svc, repo=tmp_path, branch="b", now=lambda: NOW, services=lambda: {"snipetech-tape": "active", "snipetech-ops": "active"},
                            balances=lambda w: (1.0, 0.0, []), turn=lambda *a, **k: {"usd": 0, "tool_calls": 0, "stopped": "end_turn", "notes": 0},
                            disk_free_gb=lambda: 100.0, send=lambda t: sent.append(t) or True,
                            commands_get=lambda url, params: {"result": [_msg(5, 7, "/stop")]}))  # fmt: skip
    finally:
        st.close()
    assert svc.calls == [("stop", "s1")] and paths.stop_flag.exists()
    log = paths.log.read_text()
    assert "command /stop" in log and "stopped by owner" in log and any("stopped 1 sniper" in t for t in sent)


def _commands_env(tmp_path):
    from snipetech.store import Store

    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    paths.telegram.write_text(json.dumps({"chat_id": 7, "offset": 5}))
    st = Store(data / "snipetech.db")
    svc = FakeSvc(held=[], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    svc.store = st
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    return paths, st, svc, cfg


def test_commands_pass_handles_stop_within_a_minute_without_claude(tmp_path, monkeypatch):
    """`operate --commands` (a one-minute timer) polls Telegram and acts on /stop at once: no snapshot, no Claude,
    no API key needed. Before this the kill switch waited for the next 15-minute operator round."""
    from snipetech.operator.run import commands_pass

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    paths, st, svc, cfg = _commands_env(tmp_path)
    sent = []
    try:
        rc = asyncio.run(commands_pass(cfg, svc, now=lambda: NOW, send=lambda t: sent.append(t) or True,
                                       commands_get=lambda url, params: {"result": [_msg(5, 7, "/stop")]}))  # fmt: skip
    finally:
        st.close()
    assert rc == 0 and svc.calls == [("stop", "s1")] and paths.stop_flag.exists()
    assert "command /stop" in paths.log.read_text() and any("stopped 1 sniper" in t for t in sent)
    assert json.loads(paths.telegram.read_text())["offset"] == 6


def test_commands_lock_serializes_the_pollers(tmp_path):
    """The operator round and the one-minute pass share one Telegram offset: whoever holds operator/commands.lock
    polls, the other skips (and the updates are still there for the holder)."""
    import fcntl

    from snipetech.operator.run import COMMANDS_LOCK, commands_pass

    paths, st, svc, cfg = _commands_env(tmp_path)
    lock = paths.root / COMMANDS_LOCK
    fd = open(lock, "w")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        rc = asyncio.run(commands_pass(cfg, svc, now=lambda: NOW, send=lambda t: True,
                                       commands_get=lambda url, params: {"result": [_msg(5, 7, "/stop")]}))  # fmt: skip
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        st.close()
    assert rc == 0 and svc.calls == [] and not paths.stop_flag.exists()
    assert json.loads(paths.telegram.read_text())["offset"] == 5 and "skipped" in paths.log.read_text()


def test_cli_operate_commands_parses():
    from snipetech.cli import build_parser

    a = build_parser().parse_args(["operate", "--commands"])
    assert a.cmd == "operate" and a.commands is True


def test_status_counts_a_starting_sniper_as_running(tmp_path):
    svc = FakeSvc(
        held=[], snipers=[{"id": "s1", "name": "a", "status": "starting"}, {"id": "s2", "name": "b", "status": "idle"}]
    )
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=1.0, wsol=0.0, held=[], snipers=svc._snipers, spent_today=0.0)
    ctx, _ = _ctx(tmp_path, svc, snap)
    assert "1 running" in asyncio.run(handle_command(_cmd("status"), **ctx))


def test_book_switch_from_telegram(tmp_path):
    """The owner switches the book on and off from Telegram; /status shows it; /stop still halts it."""
    from snipetech.book.switch import book_live

    svc = FakeSvc(held=[], snipers=[])
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=5.0, wsol=0.0, held=[], snipers=[], spent_today=0.0)
    ctx, sent = _ctx(tmp_path, svc, snap)
    assert not book_live(ctx["cfg"], ctx["paths"])
    reply = asyncio.run(handle_command(_cmd("book", "on"), **ctx))
    assert book_live(ctx["cfg"], ctx["paths"]) and "on" in reply.lower() and "/book off" in reply
    assert "book: on" in asyncio.run(handle_command(_cmd("status"), **ctx))
    assert "switched the book on" in ctx["paths"].notes.read_text()
    reply = asyncio.run(handle_command(_cmd("book", "off"), **ctx))
    assert not book_live(ctx["cfg"], ctx["paths"]) and "off" in reply.lower()
    assert "book: off" in asyncio.run(handle_command(_cmd("book"), **ctx)).lower()


def test_book_off_tells_the_truth(tmp_path):
    """Off stops what the book runs (except a sniper still selling a coin); and when the config file keeps the book on,
    /book off says so instead of claiming it is off."""
    svc = FakeSvc(held=[], snipers=[])
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=5.0, wsol=0.0, held=[], snipers=[], spent_today=0.0)
    ctx, sent = _ctx(tmp_path, svc, snap)
    asyncio.run(handle_command(_cmd("book", "on"), **ctx))
    reply = asyncio.run(handle_command(_cmd("book", "off"), **ctx)).lower()
    assert "stops its snipers that hold nothing" in reply and "still on" not in reply
    ctx["cfg"].book.enabled = True
    reply = asyncio.run(handle_command(_cmd("book", "off"), **ctx)).lower()
    assert "still on" in reply and "enabled = true" in reply and "/stop" in reply


def test_book_bar_from_telegram(tmp_path):
    from snipetech.book.switch import effective_min_score

    svc = FakeSvc(held=[], snipers=[])
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=5.0, wsol=0.0, held=[], snipers=[], spent_today=0.0)
    ctx, sent = _ctx(tmp_path, svc, snap)
    reply = asyncio.run(handle_command(_cmd("book", "bar", "0.001"), **ctx))
    assert "0.001" in reply and effective_min_score(ctx["cfg"], ctx["paths"]) == 0.001
    assert "owner set the book's bar" in ctx["paths"].notes.read_text()
    assert "bar 0.001" in asyncio.run(handle_command(_cmd("book", "status"), **ctx))
    reply = asyncio.run(handle_command(_cmd("book", "bar", "-1"), **ctx)).lower()
    assert "not changed" in reply and effective_min_score(ctx["cfg"], ctx["paths"]) == 0.001
    asyncio.run(handle_command(_cmd("book", "bar", "default"), **ctx))
    assert effective_min_score(ctx["cfg"], ctx["paths"]) == ctx["cfg"].book.min_score
