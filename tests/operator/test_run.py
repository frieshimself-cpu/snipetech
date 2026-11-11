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


def test_a_dry_round_keeps_nothing_and_the_next_round_is_still_told(tmp_env, capsys):
    """11 Nov: `operate --dry-run` saved its snapshot like a real round, so what it saw (a job that ended, a balance
    that moved, a fill) counted as seen and the next real round was never told: two profiling runs swallowed the events
    of 09:24 and 09:54 UTC. It also wrote "call operator" in the round's log with nobody called."""
    _op(tmp_env)
    capsys.readouterr()
    kept = tmp_env["paths"].snapshot.read_text()
    meta = {"id": "lab1", "argv": ["lab"], "running": False, "started": NOW - 60, "ended": NOW, "exit_code": 0}
    (tmp_env["paths"].jobs / "lab1.json").write_text(json.dumps(meta))
    _op(tmp_env, dry_run=True)
    out = json.loads(capsys.readouterr().out)
    assert out["decision"]["call"] is True and out["decision"]["reasons"] == ["job ended: lab1 (lab, exit 0)"]
    assert tmp_env["paths"].snapshot.read_text() == kept and tmp_env["calls"] == []
    log = tmp_env["paths"].log.read_text().splitlines()
    assert log[-1].endswith("dry run: would call operator: job ended: lab1 (lab, exit 0)")
    _op(tmp_env)  # the real round
    assert len(tmp_env["calls"]) == 1 and tmp_env["paths"].log.read_text().splitlines()[-2].endswith(
        " call operator: job ended: lab1 (lab, exit 0)"
    )


def test_a_dry_round_prints_what_it_sees_when_the_round_would_stay_quiet(tmp_env, capsys):
    _op(tmp_env)
    capsys.readouterr()
    _op(tmp_env, dry_run=True)
    out = json.loads(capsys.readouterr().out)
    assert out["decision"]["call"] is False and out["snapshot"]["wallet"] == "W"
    assert tmp_env["paths"].log.read_text().splitlines()[-1].endswith("dry run: would stay quiet")


def test_analyst_flag_forces_analyst_mode(tmp_env):
    _op(tmp_env, analyst=True)
    assert tmp_env["calls"] and tmp_env["calls"][0][0][4].mode == "analyst"


def test_budget_note_once_per_day(tmp_env):
    tmp_env["paths"].spend.write_text(json.dumps({"ts": NOW - 10, "mode": "operator", "usd": 15.0}) + "\n")
    _op(tmp_env)
    _op(tmp_env, balances=lambda w: (2.0, 0.0, []))
    _op(tmp_env, balances=lambda w: (3.0, 0.0, []))
    assert tmp_env["paths"].notes.read_text().count("budget reached") == 1 and tmp_env["calls"] == []


def test_the_budget_lines_name_the_owners_daily_cap(tmp_env):
    """The log line and the once-a-day note say the cap that applies: the owner's /budget, not the pinned config."""
    from snipetech.operator.budget_switch import set_budget

    set_budget(tmp_env["paths"], tmp_env["cfg"], {"daily_usd": 20}, "telegram", NOW)
    tmp_env["paths"].spend.write_text(json.dumps({"ts": NOW - 10, "mode": "operator", "usd": 21.0}) + "\n")
    _op(tmp_env)
    assert "budget reached: 21.00 $ of 20.00 $ today" in tmp_env["paths"].notes.read_text()
    assert "budget: 21.00 $ of 20.00 $ spent today" in tmp_env["paths"].log.read_text() and tmp_env["calls"] == []


def test_a_turns_own_cap_never_hides_the_days_budget_note(tmp_env):
    """Review, 7 Nov: the day's note was matched on "budget reached", which the turn's "turn budget reached (...)" note
    contains too. Once one turn hit its own cap, the day's cap was reached in silence. A cap the owner raised with
    /budget is announced again when it is reached in its turn; the same cap stays once a day."""
    from snipetech.operator.budget_switch import set_budget

    sent: list[str] = []

    def send(text: str) -> bool:
        sent.append(text)
        return True

    paths = tmp_env["paths"]
    paths.notes.write_text(
        "- 2026-11-06T00:10:00Z [info] turn budget reached (2.31 $ of 2.00) before the round finished\n"
    )
    paths.spend.write_text(json.dumps({"ts": NOW - 10, "mode": "analyst", "usd": 15.10}) + "\n")
    _op(tmp_env, send=send)
    _op(tmp_env, send=send, balances=lambda w: (2.0, 0.0, []))  # the same cap reached again: no second message
    budget = [s for s in sent if "budget reached:" in s]
    assert len(budget) == 1 and "15.10 $ of 15.00 $ today" in budget[0] and "/budget" in budget[0]
    set_budget(paths, tmp_env["cfg"], {"daily_usd": 20}, "telegram", NOW)  # the owner raises the day's cap
    with paths.spend.open("a") as f:
        f.write(json.dumps({"ts": NOW - 5, "mode": "analyst", "usd": 5.0}) + "\n")
    _op(tmp_env, send=send, balances=lambda w: (3.0, 0.0, []))
    budget = [s for s in sent if "budget reached:" in s]
    assert len(budget) == 2 and "20.10 $ of 20.00 $ today" in budget[1] and tmp_env["calls"] == []


def test_the_owners_higher_budget_lets_the_round_call_claude_past_the_configs_cap(tmp_env):
    from snipetech.operator.budget_switch import set_budget

    set_budget(tmp_env["paths"], tmp_env["cfg"], {"daily_usd": 200}, "telegram", NOW)
    tmp_env["paths"].spend.write_text(json.dumps({"ts": NOW - 10, "mode": "operator", "usd": 15.0}) + "\n")
    _op(tmp_env)
    _op(tmp_env, balances=lambda w: (2.0, 0.0, []))  # equity +1 SOL: news
    assert len(tmp_env["calls"]) == 1 and "budget reached" not in tmp_env["paths"].notes.read_text()


def _sells(st, n, start, wallet="W"):
    for i in range(n):
        st.put_fill({"sig": f"{wallet}-{start + i}", "ts": int(start + i * 60), "wallet": wallet, "mint": f"M{start + i}",
                     "side": "sell", "sol": 0.19, "tokens": 1.0, "pnl_sol": -0.01})  # fmt: skip


def test_five_new_live_round_trips_start_one_post_mortem_and_it_is_not_repeated(tmp_env):
    from snipetech.operator import learning

    _sells(tmp_env["svc"].store, 5, NOW - 3600)
    _sells(tmp_env["svc"].store, 3, NOW - 3600, wallet="SOMEONE ELSE")
    _op(tmp_env)
    (call,) = tmp_env["calls"]
    decision = call[0][4]
    assert decision.mode == "postmortem" and any(
        r.startswith("post-mortem due: 5 new live round trips") for r in decision.reasons
    )
    pm = learning.postmortem_state(tmp_env["paths"])
    assert pm == {"ts": NOW, "sell_ts": NOW - 3600 + 240, "n": 5, "mode": "postmortem"}
    _op(tmp_env)  # the same trips: reviewed already
    _sells(tmp_env["svc"].store, 6, NOW - 1800)
    _op(tmp_env)  # six new ones, but the last post-mortem ran under three hours ago
    assert len(tmp_env["calls"]) == 1 and "call postmortem" in tmp_env["paths"].log.read_text()


def test_a_failed_post_mortem_keeps_its_trips_for_the_next_one(tmp_env):
    """An API error must not count as a review of the trips: the next post-mortem (three hours on, or the daily
    analysis) still starts from the same sells; the three-hour spacing holds all the same."""
    from snipetech.operator import learning

    _sells(tmp_env["svc"].store, 5, NOW - 3600)
    failed = lambda *a, **k: {"usd": 0.0, "tool_calls": 0, "stopped": "api_error", "notes": 1}
    _op(tmp_env, turn=failed)
    pm = learning.postmortem_state(tmp_env["paths"])
    assert pm["ts"] == NOW and pm["sell_ts"] is None  # tried now; nothing reviewed yet
    assert learning.trips_since_postmortem(tmp_env["svc"].store, "W", tmp_env["paths"], NOW + 4 * 3600)["n"] == 5
    _op(tmp_env)  # straight after: not again within three hours
    assert tmp_env["calls"] == []


def test_a_post_mortem_is_recorded_only_when_the_turn_is_called(tmp_env, monkeypatch, capsys):
    from snipetech.operator import learning

    _sells(tmp_env["svc"].store, 5, NOW - 3600)
    _op(tmp_env, dry_run=True)
    assert '"postmortem"' in capsys.readouterr().out and learning.postmortem_state(tmp_env["paths"]) == {}
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    _op(tmp_env)
    assert tmp_env["calls"] == [] and learning.postmortem_state(tmp_env["paths"]) == {}


def _night(env, sent):
    """The three 7aPo3n copies of 7 Nov in the store, the book wallet theirs, Telegram captured in `sent`."""
    from tests import loss_7nov as F

    F.load(env["svc"].store)
    env["cfg"].operator.wallet = F.WALLET
    return {"send": lambda text: sent.append(text) or True}


def test_the_owner_gets_the_post_mortems_numbers_once_the_turn_ran(tmp_env):
    """After a turn that reviewed new live round trips (here the analyst's), code sends the owner the batch's summary
    and writes it to the notes; the model's own note is separate."""
    sent: list[str] = []
    _op(tmp_env, analyst=True, **_night(tmp_env, sent))
    assert len(tmp_env["calls"]) == 1
    (summary,) = [s for s in sent if s.startswith("Post-mortem:")]
    assert "3 live round trips" in summary and "Net: -0.0119 SOL." in summary and "0.0080 SOL on these trips" in summary
    notes = tmp_env["paths"].notes.read_text()
    assert "[info] Post-mortem: 3 live round trips" in notes and "Net: -0.0119 SOL." in notes
    _op(tmp_env, analyst=True, send=lambda text: sent.append(text) or True)  # the same trips: reviewed already
    assert len([s for s in sent if s.startswith("Post-mortem:")]) == 1


def test_no_summary_when_the_post_mortem_turn_did_not_run(tmp_env):
    sent: list[str] = []
    failed = lambda *a, **k: {"usd": 0.0, "tool_calls": 0, "stopped": "api_error", "notes": 1}
    _op(tmp_env, analyst=True, turn=failed, **_night(tmp_env, sent))
    assert not [s for s in sent if s.startswith("Post-mortem:")]
    assert "Post-mortem:" not in (tmp_env["paths"].notes.read_text() if tmp_env["paths"].notes.exists() else "")


def test_a_summary_that_cannot_be_built_never_breaks_the_round(tmp_env, monkeypatch):
    from snipetech.operator import postmortem

    def boom(*a, **k):
        raise RuntimeError("tape unreadable")

    monkeypatch.setattr(postmortem, "summary_text", boom)
    sent: list[str] = []
    assert _op(tmp_env, analyst=True, **_night(tmp_env, sent)) == 0
    assert "post-mortem summary: failed: RuntimeError: tape unreadable" in tmp_env["paths"].log.read_text()


def test_cli_has_operate_and_ops_allows_it():
    assert "operate" in O.ALLOWED
    a = build_parser().parse_args(["operate", "--dry-run"])
    assert a.dry_run and not a.analyst and not a.telegram_setup
    assert build_parser().parse_args(["operate", "--analyst", "--telegram-setup"]).telegram_setup


def test_the_round_tells_the_owner_once_when_the_wallet_wide_guard_trips(tmp_env):
    """The owner's guard (7 Nov 09:15: no new live trade once the wallet is under 10 SOL): one decision line when it
    trips, then nothing more every round. With nothing that could trade (the book off, no test) it is only noted."""
    from snipetech.book.switch import book_switch

    sent: list[str] = []
    _op(tmp_env, send=lambda t: sent.append(t) or True)  # a 1 SOL wallet, the book off
    notes = tmp_env["paths"].notes.read_text()
    assert not [t for t in sent if "wallet-wide guard" in t] and "[info] wallet-wide guard tripped" in notes
    book_switch(tmp_env["paths"], True, "test", NOW)  # the book on: a new live trade could start
    for k in range(1, 4):
        _op(tmp_env, now=lambda k=k: NOW + 900 * k, send=lambda t: sent.append(t) or True)
    (told,) = [t for t in sent if "wallet-wide guard tripped" in t]
    assert "1.00 SOL" in told and "10 SOL floor" in told and "/risk" in told
    _op(tmp_env, dry_run=True, now=lambda: NOW + 86400, send=lambda t: sent.append(t) or True)
    assert len([t for t in sent if "wallet-wide guard tripped" in t]) == 1  # a dry run starts nothing, says nothing


def test_the_guards_trip_pages_the_owner_while_the_standing_yes_is_on(tmp_env):
    """The book off and no test, but the owner's automatic test may start one: the trip matters, so it is a page."""
    from snipetech.operator.autotest import set_autotest

    set_autotest(tmp_env["paths"], True, words="Yes, use this plan", by="cli", now=NOW - 60,
                 wallet="6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at", oktech_wallet="SNIPER")  # fmt: skip
    sent: list[str] = []
    _op(tmp_env, send=lambda t: sent.append(t) or True)
    assert len([t for t in sent if "wallet-wide guard tripped" in t]) == 1


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


def _timed_pass(cfg, svc, at, sent):
    """One one-minute pass at `at` (the pass's clock), and how long it really took."""
    import time

    from snipetech.operator.run import commands_pass

    t = time.monotonic()
    rc = asyncio.run(commands_pass(cfg, svc, now=lambda: at, send=lambda x: sent.append(x) or True,
                                   commands_get=lambda url, params: {"result": []}))  # fmt: skip
    return rc, time.monotonic() - t


def test_a_hanging_wallet_read_cannot_hold_up_the_one_minute_pass(tmp_path, monkeypatch):
    """With Helius hanging, one holdings read retried for about 250 s, so every pass ran into the unit's 2-minute
    timeout and the owner's /stop was polled every 2 minutes instead of every minute. The watch has a time bound."""
    import snipetech.operator.run as R

    paths, st, cfg, svc, _m, ours = _watch_env(tmp_path, monkeypatch)
    monkeypatch.setattr(R, "WATCH_TIMEOUT_SECS", 0.5, raising=False)

    async def hangs(address, ignore=()):
        await asyncio.sleep(3)
        return []

    svc.open_positions = hangs
    try:
        rc, took = _timed_pass(cfg, svc, ours.ts + 70, [])
    finally:
        st.close()
    assert rc == 0 and took < 2.0, f"the pass took {took:.1f} s"
    assert "watch: skipped: the wallet or OKTech did not answer within 0.5 s" in paths.log.read_text()


def test_oktech_answering_slowly_leaves_the_coin_to_the_tape(tmp_path, monkeypatch):
    """OKTech's tasks are waited for only a few seconds: past that the coin is judged by the tape and its sniper's
    payload, as when OKTech does not answer at all, instead of the whole watch being cut off by its bound."""
    import snipetech.book.watch as Wt
    import snipetech.operator.run as R

    paths, st, cfg, svc, m, ours = _watch_env(tmp_path, monkeypatch)
    monkeypatch.setattr(Wt, "OKTECH_WAIT_SECS", 0.3, raising=False)
    monkeypatch.setattr(R, "WATCH_TIMEOUT_SECS", 1.0, raising=False)

    async def slow():
        await asyncio.sleep(3)
        return {"snipers": []}

    svc.oktech_status = slow
    sent: list[str] = []
    try:
        rc, took = _timed_pass(cfg, svc, ours.ts + 70, sent)
    finally:
        st.close()
    assert rc == 0 and took < 2.0, f"the pass took {took:.1f} s"
    assert len(sent) == 1 and m.mint in sent[0] and "Nobody is selling" in sent[0]  # the dead exit, from the tape
