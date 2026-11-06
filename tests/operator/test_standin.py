"""The operator round stands in for loop timers that are not enabled: it starts each due stage as a background job,
never twice at once, never when the timer is active, and never on a dry run."""

import json

from snipetech.operator.standin import STANDIN, due_standins

NOW = 1_793_923_200 + 15 * 3600 + 60  # Friday 6 Nov, 15:01 UTC
SUNDAY = 1_794_096_000 + 6 * 3600  # Sunday 8 Nov, 06:00 UTC


def _meta(argv, started, running=False):
    return {"id": f"j{started}", "argv": list(argv), "started": started, "running": running}


def test_inactive_timers_get_their_stage_started():
    due = due_standins({}, [], NOW)
    argvs = [tuple(a) for a in due]
    assert ("measure", "run") in argvs and ("book", "run") in argvs and ("discover", "run") in argvs
    assert ("qualify", "run") in argvs and ("events", "poll", "--once") in argvs and ("snapshot-db",) in argvs
    assert ("strategist", "run") not in argvs  # Sundays after 05:00 UTC only


def test_active_timers_are_left_to_systemd():
    services = {unit: "active" for _, unit, _ in STANDIN}
    assert due_standins(services, [], NOW) == []


def test_recent_or_running_stages_are_not_started_again():
    metas = [_meta(["measure", "run"], NOW - 120), _meta(["discover", "run"], NOW - 4000, running=True),
             _meta(["qualify", "run"], NOW - 600)]  # fmt: skip
    argvs = [tuple(a) for a in due_standins({}, metas, NOW)]
    assert ("measure", "run") not in argvs  # ran 2 min ago
    assert ("discover", "run") not in argvs  # still running
    assert ("qualify", "run") not in argvs  # ran 10 min ago (every 30 min)
    assert ("book", "run") in argvs


def test_strategist_runs_on_sundays():
    argvs = [tuple(a) for a in due_standins({}, [], SUNDAY)]
    assert ("strategist", "run") in argvs
    argvs = [tuple(a) for a in due_standins({}, [_meta(["strategist", "run"], SUNDAY - 3600)], SUNDAY)]
    assert ("strategist", "run") not in argvs


def test_round_starts_due_stages_but_not_on_a_dry_run(tmp_path, monkeypatch):
    import asyncio

    from snipetech.config import Config
    from snipetech.operator.run import operate
    from snipetech.store import Store

    data = tmp_path / "data"
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = "W"
    started = []
    monkeypatch.setattr(
        "snipetech.operator.run.start_standins", lambda jobs, services, now, log=None: started.append(now) or []
    )

    class Svc:
        store = st

    kw = dict(repo=tmp_path, branch="b", now=lambda: NOW, services=lambda: {"snipetech-tape": "active", "snipetech-ops": "active"},
              balances=lambda w: (1.0, 0.0, []), turn=lambda *a, **k: {"usd": 0, "tool_calls": 0, "stopped": "end_turn", "notes": 0},
              disk_free_gb=lambda: 100.0, send=lambda t: True)  # fmt: skip
    try:
        asyncio.run(operate(cfg, Svc(), dry_run=True, **kw))
        assert started == []
        asyncio.run(operate(cfg, Svc(), **kw))
        assert started == [NOW]
    finally:
        st.close()
    assert json.dumps(started)
