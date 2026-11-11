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
    assert ("qualify", "run") in argvs and ("events", "poll", "--once") in argvs
    assert ("snapshot-db", "--limit-secs", "7200") in argvs
    assert ("strategist", "run") in argvs  # every day after 05:10 UTC, not only on Sundays


def test_active_timers_are_left_to_systemd():
    services = {unit: "active" for _, unit, _ in STANDIN if unit}
    assert due_standins(services, [], NOW) == [
        ["snapshot-db", "--limit-secs", "7200"],  # the copy is the round's job since 11 Nov, whatever its timer does
        ["events", "scan"],
        ["paper", "run"],
        ["rescore"],
        ["exitrun", "run", "--minutes", "345"],
        ["research", "next"],
        ["research", "digest"],
    ]  # no unit runs these: always stood in for


def test_recent_or_running_stages_are_not_started_again():
    metas = [_meta(["measure", "run"], NOW - 120), _meta(["discover", "run"], NOW - 4000, running=True),
             _meta(["qualify", "run"], NOW - 600)]  # fmt: skip
    argvs = [tuple(a) for a in due_standins({}, metas, NOW)]
    assert ("measure", "run") not in argvs  # ran 2 min ago
    assert ("discover", "run") not in argvs  # still running
    assert ("qualify", "run") not in argvs  # ran 10 min ago (every 30 min)
    assert ("book", "run") in argvs


def test_the_strategist_stands_in_once_a_day_after_0510():
    """Its timer runs it every day at 05:10 UTC (its own guard calls Claude only on 20 new live round trips); with the
    timer off, the first round after 05:10 starts it, once that day, whenever it ran the day before."""
    day = 1_793_923_200  # Friday 6 Nov 2026, 00:00 UTC
    strat = ("strategist", "run")

    def due(now, *started):
        return strat in [tuple(a) for a in due_standins({}, [_meta(strat, s) for s in started], now)]

    assert not due(day + 5 * 3600) and due(day + 5 * 3600 + 15 * 60) and due(SUNDAY)
    assert not due(NOW, day + 5 * 3600 + 15 * 60)  # it ran this morning
    assert due(day + 86400 + 5 * 3600 + 15 * 60, day + 5 * 3600 + 15 * 60)  # the next morning
    assert due(day + 86400 + 5 * 3600 + 15 * 60, day + 23 * 3600)  # a late run the day before does not shift it
    assert not due(day + 86400 + 4 * 3600, day + 23 * 3600)  # nor make it run before 05:10


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


def test_one_unreadable_job_file_does_not_stop_the_loop(tmp_path):
    import os
    import sys

    from snipetech import ops as O
    from snipetech.operator.standin import start_standins

    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    (data / "jobs" / "truncated.json").write_text("")

    class Jobs:
        ops = O.Ops(
            repo=tmp_path,
            data=data,
            db=data / "x.db",
            keys_file=tmp_path / "k",
            branch="main",
            command=[sys.executable, "-c", "pass"],
        )

    started = []
    Jobs.ops.op_run = lambda argv, timeout=None: started.append(tuple(argv)) or {"id": "x"}
    assert start_standins(Jobs(), {}, NOW) and ("measure", "run") in started
    assert os.path.exists(data / "jobs" / "truncated.json")  # left for a person to look at, never fatal


def test_the_event_scan_stands_in_even_while_the_poll_service_runs():
    """No systemd unit runs `events scan` (snipetech-events only polls the feeds): the round always stands in for it."""
    argvs = [tuple(a) for a in due_standins({"snipetech-events": "active"}, [], NOW)]
    assert ("events", "scan") in argvs and ("events", "poll", "--once") not in argvs


def test_the_snapshot_timer_is_watched():
    from snipetech.operator.snapshot import WATCHED_UNITS

    assert "snipetech-snapshot.timer" in WATCHED_UNITS


def test_old_finished_stage_jobs_are_pruned(tmp_path):
    from snipetech.operator.standin import prune_jobs

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    day = 86400

    def put(jid, argv, ended, running=False):
        (jobs / f"{jid}.json").write_text(json.dumps({"id": jid, "argv": argv, "started": ended - 5, "ended": ended,
                                                      "exit_code": 0, "running": running}))  # fmt: skip
        (jobs / f"{jid}.log").write_text("x")
        (jobs / f"{jid}.exit").write_text("0")

    put("old_measure", ["measure", "run"], NOW - 4 * day)
    put("new_measure", ["measure", "run"], NOW - 1 * day)
    put("old_lab", ["lab", "--latency", "0"], NOW - 9 * day)  # research output: kept
    put("old_guard", ["oktech", "guard"], NOW - 9 * day)  # live-test record: kept
    assert prune_jobs(jobs, NOW, keep_days=3) == 1
    names = {p.name for p in jobs.iterdir()}
    assert "old_measure.json" not in names and "old_measure.log" not in names and "old_measure.exit" not in names
    assert {"new_measure.json", "old_lab.json", "old_guard.log"} <= names


def test_each_stage_runs_under_its_own_time_limit(tmp_path):
    """A hung stage must not hold its slot for the ops service's 6-hour default: measure and the book get minutes."""
    import sys

    from snipetech import ops as O
    from snipetech.operator.standin import start_standins

    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)

    class Jobs:
        ops = O.Ops(
            repo=tmp_path,
            data=data,
            db=data / "x.db",
            keys_file=tmp_path / "k",
            branch="main",
            command=[sys.executable, "-c", "pass"],
        )

    limits = {}
    Jobs.ops.op_run = lambda argv, timeout=6 * 3600: limits.update({" ".join(argv): timeout}) or {"id": "x"}
    start_standins(Jobs(), {}, NOW)
    assert limits["measure run"] <= 30 * 60 and limits["book run"] <= 30 * 60 and limits["qualify run"] <= 3 * 3600
    assert all(t < 6 * 3600 for t in limits.values())


def test_the_forward_paper_test_runs_around_the_clock():
    """Learning never stops when nothing trades: the round scores the frozen strategies on new tape every 30 minutes
    (no systemd unit runs it), quietly when it ends cleanly, under its own time limit."""
    from snipetech.operator.standin import STAGE_LIMIT_SECS, loop_stage

    everything_on = {unit: "active" for _, unit, _ in STANDIN if unit}
    argvs = [tuple(a) for a in due_standins(everything_on, [], NOW)]
    assert ("paper", "run") in argvs
    assert ("paper", "run") not in [tuple(a) for a in due_standins({}, [_meta(["paper", "run"], NOW - 600)], NOW)]
    assert loop_stage(["paper", "run"]) == "paper run" and loop_stage(["paper", "freeze"]) is None
    assert STAGE_LIMIT_SECS["paper run"] <= 3600


def test_idle_time_research_runs_every_round_and_the_digest_every_six_hours():
    """`research next` looks for idle time at every round (a 15-minute round must never skip it for a few seconds of
    timer jitter); the owner's research digest goes out every six hours. Both are quick, under their own limits."""
    from snipetech.operator.standin import STAGE_LIMIT_SECS

    everything_on = {unit: "active" for _, unit, _ in STANDIN if unit}
    nxt, dig = ("research", "next"), ("research", "digest")
    argvs = [tuple(a) for a in due_standins(everything_on, [], NOW)]
    assert nxt in argvs and dig in argvs
    round_ago = [_meta(nxt, NOW - 15 * 60 + 5), _meta(dig, NOW - 2 * 3600)]
    argvs = [tuple(a) for a in due_standins(everything_on, round_ago, NOW)]
    assert nxt in argvs and dig not in argvs
    assert nxt not in [tuple(a) for a in due_standins(everything_on, [_meta(nxt, NOW - 900, running=True)], NOW)]
    assert dig in [tuple(a) for a in due_standins(everything_on, [_meta(dig, NOW - 6 * 3600 - 60)], NOW)]
    assert STAGE_LIMIT_SECS["research next"] <= 15 * 60 and STAGE_LIMIT_SECS["research digest"] <= 15 * 60


def test_research_next_comes_after_every_heavy_stage_of_the_round():
    """Stages start in table order: a qualify or paper run started in the same round is already a running job when
    `research next` looks, so the rotation never starts a second heavy job beside it."""
    from snipetech.operator.research import is_heavy

    order = [argv for argv, _unit, _every in STANDIN]
    nxt = order.index(("research", "next"))
    assert any(is_heavy(argv) for argv in order)
    assert all(i < nxt for i, argv in enumerate(order) if is_heavy(argv))


def test_research_units_are_loop_stages_so_their_end_calls_no_claude():
    """The rotation's units end several times a day: as loop stages they never wake the operator's Claude turn
    (a failure still does, once per stage), and their job files are pruned like every stage's."""
    from snipetech.operator.standin import loop_stage

    assert loop_stage(["research", "search"]) == "research search"
    assert loop_stage(["research", "next"]) == "research next"
    assert loop_stage(["qualify", "run", "--max", "900"]) == "qualify run"
    assert loop_stage(["discover", "run", "--sources", "coinfirst"]) == "discover run"
    assert loop_stage(["paper", "freeze"]) is None and loop_stage(["lab", "--latency", "0"]) is None


def test_the_round_writes_the_database_copy_whatever_the_timer_does():
    """11 Nov: the snapshot timer's unit is killed after 20 minutes and a copy took 13.7, more every day of tape. The
    round starts the copy as a job with two hours (the command itself leaves a young copy alone, and one copy is
    written at a time), so the copy is still written when the unit can no longer finish one."""
    from snipetech.operator.standin import STAGE_LIMIT_SECS

    copy = ("snapshot-db", "--limit-secs", "7200")
    services = {unit: "active" for _, unit, _ in STANDIN if unit}
    assert copy in [tuple(a) for a in due_standins(services, [], NOW)]
    assert STAGE_LIMIT_SECS[" ".join(copy)] >= 7200
    assert copy not in [tuple(a) for a in due_standins(services, [_meta(copy, NOW - 20 * 60)], NOW)]
    assert copy in [tuple(a) for a in due_standins(services, [_meta(copy, NOW - 31 * 60)], NOW)]


def test_the_round_scores_deployers_from_the_copy_now_that_the_recorder_does_not():
    """11 Nov: the recorder scored deployers beside its streams, and each of its writes waited for an analysis's read.
    The round starts `rescore` every hour instead: a job of its own on the tape's copy, never two at once, which finds
    nothing due while the copy is the one it read last."""
    from snipetech.operator.standin import STAGE_LIMIT_SECS, loop_stage
    from snipetech.ops import ALLOWED

    stage = ("rescore",)
    assert stage in [tuple(a) for a in due_standins({}, [], NOW)]
    assert "rescore" in ALLOWED and loop_stage(["rescore"]) == "rescore"
    assert STAGE_LIMIT_SECS["rescore"] <= 30 * 60
    assert stage not in [tuple(a) for a in due_standins({}, [_meta(stage, NOW - 50 * 60)], NOW)]
    assert stage in [tuple(a) for a in due_standins({}, [_meta(stage, NOW - 61 * 60)], NOW)]
    assert stage not in [tuple(a) for a in due_standins({}, [_meta(stage, NOW - 2 * 3600, running=True)], NOW)]


def test_the_shadow_exit_recorder_is_kept_running_by_the_round():
    """`exitrun run` watches the coins our snipers buy and writes down when our own exits would sell them (it sends
    nothing). A job is ended after six hours at the latest, and this one was started again by hand each time: on
    11 Nov nothing recorded from 05:52 to 06:19 while a new copy sniper was armed. The round keeps one running."""
    from snipetech.operator.standin import STAGE_LIMIT_SECS, loop_stage
    from snipetech.ops import ALLOWED

    stage = ("exitrun", "run", "--minutes", "345")
    assert stage in [tuple(a) for a in due_standins({}, [], NOW)]
    assert "exitrun" in ALLOWED and loop_stage(list(stage)) == " ".join(stage)
    assert 345 * 60 + 60 <= STAGE_LIMIT_SECS[" ".join(stage)] < 6 * 3600  # it ends by itself, inside the six hours
    running = _meta(stage, NOW - 3 * 3600, running=True)
    assert stage not in [tuple(a) for a in due_standins({}, [running], NOW)]  # one at a time
    ended = _meta(stage, NOW - 346 * 60)
    assert stage in [tuple(a) for a in due_standins({}, [ended], NOW)]  # started again at the next round


def test_a_stage_due_every_half_hour_runs_every_second_round_not_every_third():
    """The round runs every 15 minutes and starts a stage a few seconds into itself. Two rounds later the stage's last
    start is a few seconds under 30 minutes old: "not due", and it waited a third round. 11 Nov: the forward paper test
    and the copy's check ran every 45 minutes (03:16, 04:00, 04:46, 05:30), the hourly scoring every 75."""
    paper = ("paper", "run")
    two_rounds_ago = NOW - 2 * 15 * 60 + 40  # started 40 s into the round that began 30 minutes before this one
    assert paper in [tuple(a) for a in due_standins({}, [_meta(paper, two_rounds_ago)], NOW)]
    one_round_ago = NOW - 15 * 60 + 40
    assert paper not in [tuple(a) for a in due_standins({}, [_meta(paper, one_round_ago)], NOW)]
    score = ("rescore",)
    assert score in [tuple(a) for a in due_standins({}, [_meta(score, NOW - 4 * 15 * 60 + 40)], NOW)]
    assert score not in [tuple(a) for a in due_standins({}, [_meta(score, NOW - 3 * 15 * 60 + 40)], NOW)]
