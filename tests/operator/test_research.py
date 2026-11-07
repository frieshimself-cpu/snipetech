"""Idle-time research and the six-hour digest: when nothing heavy runs, the rotation starts the one research unit the
pipeline lacks most (measured from the candidates, the verdicts, the coverage marks and the job files); never beside a
heavy job, never a trade. Every six hours the owner gets the result in plain words."""

import json

import pytest

from snipetech.config import Config
from snipetech.operator import research as RS
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store

NOW = 1_793_923_200 + 15 * 3600  # Friday 6 Nov 2026, 15:00 UTC
H = 3600
W = [c * 6 + "x" * 38 for c in "ABCDEFGHJK"]  # wallets whose short form (first six characters) differs


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.verdict.bootstrap_samples = 200
    yield st, cfg, data
    st.close()


def _verdict(st, wallet, computed_at, status, n=0, mean=None, lower=None, next_review=None, score=0.0, reasons=()):
    st.put_verdict({"wallet": wallet, "computed_at": int(computed_at), "landing_pos": "+6", "n_tape": n, "mean_sol": mean,
                    "lower_sol": lower, "score": score, "status": status, "next_review": next_review,
                    "reasons": list(reasons)})  # fmt: skip


def _meta(argv, started, running=False, ended=None, exit_code=0, jid=None):
    return {"id": jid or f"j{started}-{'-'.join(argv[:2])}", "argv": list(argv), "started": started, "running": running,
            "ended": None if running else (ended if ended is not None else started + 60),
            "exit_code": None if running else exit_code}  # fmt: skip


def _pipeline(st):
    """Two wallets never checked (2 days and 1 hour), one verdict overdue for a review, one fresh qualified wallet, one
    banned wallet that never counts."""
    st.upsert_candidate(W[0], "coinfirst", {}, NOW - 48 * H)
    st.upsert_candidate(W[1], "tape", {}, NOW - 1 * H)
    st.upsert_candidate(W[2], "tape", {}, NOW - 5 * 24 * H)
    _verdict(st, W[2], NOW - 4 * 24 * H, "candidate", n=12, mean=0.004, lower=-0.002, next_review=NOW - 24 * H,
             reasons=["n 12 < 20 at +6"])  # fmt: skip
    st.upsert_candidate(W[3], "solanatracker", {}, NOW - 3 * 24 * H)
    _verdict(st, W[3], NOW - 1 * H, "qualified", n=40, mean=0.017, lower=0.004, next_review=NOW + 23 * H, score=0.004)
    st.upsert_candidate(W[4], "tape", {}, NOW - 10 * H, status="banned")


def test_the_backlog_counts_waiting_wallets_due_reviews_old_verdicts_and_yield_per_source(env):
    st, cfg, _ = env
    _pipeline(st)
    b = RS.measure_backlog(st, cfg, NOW)
    assert b["waiting"]["new"] == 2 and round(b["waiting"]["new_oldest_h"]) == 48
    assert b["waiting"]["due"] == 1 and round(b["waiting"]["due_oldest_h"]) == 24
    assert b["verdicts"]["total"] == 2 and b["verdicts"]["older_than_3d"] == 1
    assert b["sources"]["tape"] == {"wallets": 2, "checked": 1, "qualified": 0, "promising": 1, "new_24h": 1}
    assert b["sources"]["solanatracker"]["qualified"] == 1 and b["sources"]["coinfirst"]["checked"] == 0
    assert "banned" not in json.dumps(b["sources"])  # the banned wallet is nowhere in the counts
    assert b["last"]["qualify_lab"] is None and b["last"]["search"] is None


def test_waiting_wallets_come_first_once_the_qualify_lab_may_run_again(env):
    st, cfg, _ = env
    _pipeline(st)
    st.set_coverage("qualify:last_lab", {"ts": NOW - 3 * H, "wallets": 300})
    first = RS.plan(RS.measure_backlog(st, cfg, NOW), [], cfg, NOW)[0]
    assert first["unit"] == "qualify" and first["argv"] == ["qualify", "run", "--max", "300"]
    assert first["limit"] <= 3 * H and "3 waiting" in first["why"]
    st.set_coverage("qualify:last_lab", {"ts": NOW - 30 * 60, "wallets": 300})  # its own 2 h interval: it would defer
    assert "qualify" not in [o["unit"] for o in RS.plan(RS.measure_backlog(st, cfg, NOW), [], cfg, NOW)]


def test_a_long_queue_gets_a_bigger_batch_but_never_more_than_the_cap(env):
    st, cfg, _ = env
    for i in range(1200):
        st.upsert_candidate(f"Q{i:05d}" + "x" * 38, "coinfirst", {}, NOW - 6 * H)
    first = RS.plan(RS.measure_backlog(st, cfg, NOW), [], cfg, NOW)[0]
    assert first["argv"] == ["qualify", "run", "--max", str(RS.MAX_QUALIFY_BATCH)]


def test_the_strategy_search_runs_every_six_hours(env):
    # 7 Nov: the owner wants profit as fast as possible and the server has the cores; new launch rules (OKTech hunt)
    # need fresh numbers several times a day, not once
    st, cfg, _ = env
    units = lambda: [o["unit"] for o in RS.plan(RS.measure_backlog(st, cfg, NOW), [], cfg, NOW)]
    assert "search" in units()  # never ran
    st.set_coverage(RS.SEARCH_KEY, {"ts": NOW - 3 * H})
    assert "search" not in units()
    st.set_coverage(RS.SEARCH_KEY, {"ts": NOW - 7 * H})
    assert "search" in units()


def test_discovery_runs_only_when_the_pipeline_has_run_dry_and_where_it_paid(env):
    st, cfg, _ = env
    st.set_coverage(RS.SEARCH_KEY, {"ts": NOW - H})
    st.upsert_candidate(W[5], "coinfirst", {}, NOW - 10 * H)
    _verdict(st, W[5], NOW - 9 * H, "qualified", n=30, mean=0.01, lower=0.002, next_review=NOW + 15 * H)
    st.upsert_candidate(W[6], "vybe", {}, NOW - 10 * H)
    _verdict(st, W[6], NOW - 9 * H, "retired", n=30, mean=-0.01, lower=-0.02, next_review=NOW + 100 * H)
    (only,) = RS.plan(RS.measure_backlog(st, cfg, NOW), [], cfg, NOW)  # nothing waiting, nothing new for 10 h
    assert only["unit"] == "discover" and only["argv"] == ["discover", "run", "--sources", "coinfirst"]
    st.upsert_candidate(W[7], "tape", {}, NOW - 30 * 60)  # a wallet waiting again: qualify matters, not discovery
    assert "discover" not in [o["unit"] for o in RS.plan(RS.measure_backlog(st, cfg, NOW), [], cfg, NOW)]


@pytest.mark.parametrize("busy", [
    {"metas": [_meta(["lab", "--latency", "0", "--position", "+6"], NOW - 600, running=True)]},
    {"metas": [_meta(["qualify", "run"], NOW - 600, running=True)]},
    {"metas": [_meta(["paper", "run"], NOW - 60, running=True)]},
    {"metas": [_meta(["research", "search"], NOW - 600, running=True)]},
    {"metas": [_meta(["events", "edge"], NOW - 600, running=True)]},
    {"services": {"snipetech-qualify": "activating"}},
    {"services": {"snipetech-daily": "activating"}},
    {"lab_held": True},
])  # fmt: skip
def test_nothing_starts_beside_a_heavy_job(env, busy):
    st, cfg, _ = env
    _pipeline(st)
    started = []
    out = RS.run_next(st, cfg, NOW, metas=busy.get("metas", []), services=busy.get("services", {}),
                      lab_held=busy.get("lab_held", False), start=lambda argv, limit: started.append(argv))  # fmt: skip
    assert started == [] and out["started"] is None and out["busy"]


def test_light_jobs_do_not_count_and_the_most_valuable_unit_starts_under_its_own_limit(env):
    st, cfg, _ = env
    _pipeline(st)
    light = [_meta(["measure", "run"], NOW - 60, running=True), _meta(["book", "run"], NOW - 30, running=True),
             _meta(["oktech", "guard"], NOW - 3000, running=True), _meta(["research", "next"], NOW - 5, running=True)]  # fmt: skip
    calls = []
    out = RS.run_next(st, cfg, NOW, metas=light, services={"snipetech-qualify": "inactive"}, lab_held=False,
                      start=lambda argv, limit: calls.append((argv, limit)) or {"id": "job1"})  # fmt: skip
    assert calls == [(["qualify", "run", "--max", "300"], RS.UNITS["qualify"]["limit"])]
    assert out["started"]["unit"] == "qualify" and out["started"]["job"] == "job1"


def test_a_unit_that_just_started_or_failed_waits_its_turn(env):
    st, cfg, _ = env
    failed = [_meta(["research", "search"], NOW - 2 * H, exit_code=1)]
    assert "search" not in [o["unit"] for o in RS.plan(RS.measure_backlog(st, cfg, NOW), failed, cfg, NOW)]
    old_failure = [_meta(["research", "search"], NOW - 8 * H, exit_code=124)]
    assert "search" in [o["unit"] for o in RS.plan(RS.measure_backlog(st, cfg, NOW), old_failure, cfg, NOW)]
    _pipeline(st)
    just = [_meta(["qualify", "run"], NOW - 10 * 60)]  # the stage itself started ten minutes ago
    assert "qualify" not in [o["unit"] for o in RS.plan(RS.measure_backlog(st, cfg, NOW), just, cfg, NOW)]


def test_a_dry_run_says_what_it_would_start_and_starts_nothing(env):
    st, cfg, _ = env
    _pipeline(st)
    out = RS.run_next(st, cfg, NOW, metas=[], services={}, lab_held=False, start=lambda *a: pytest.fail("started"),
                      dry_run=True)  # fmt: skip
    assert out["would_start"]["unit"] == "qualify" and out["started"] is None and out["backlog"]["waiting"]["new"] == 2


def test_when_nothing_can_start_it_says_what_waits_and_until_when(env):
    st, cfg, _ = env
    _pipeline(st)
    st.set_coverage(RS.SEARCH_KEY, {"ts": NOW - 3 * H})
    st.set_coverage("qualify:last_lab", {"ts": NOW - 80 * 60, "wallets": 300})  # the qualify lab may run in 40 min
    out = RS.run_next(st, cfg, NOW, metas=[], services={}, lab_held=False, start=lambda *a: pytest.fail("started"))
    assert out["started"] is None
    assert "3 wallets wait for the qualify lab, allowed again in 40 min" in out["idle"]
    assert "strategy search ran 3 h ago, next in 3 h" in out["idle"]


def test_nothing_pending_means_nothing_started(env):
    st, cfg, _ = env
    st.set_coverage(RS.SEARCH_KEY, {"ts": NOW - H})
    st.upsert_candidate(W[0], "tape", {}, NOW - 30 * 60)  # discovery is alive
    _verdict(st, W[0], NOW - 10 * 60, "candidate", n=3, next_review=NOW + 70 * H)
    out = RS.run_next(st, cfg, NOW, metas=[], services={}, lab_held=False, start=lambda *a: pytest.fail("started"))
    assert out["started"] is None and out["idle"]


def test_the_rotation_never_starts_anything_that_trades(env):
    st, cfg, _ = env
    _pipeline(st)
    seen = {o["argv"][0] for o in RS.plan(RS.measure_backlog(st, cfg, NOW), [], cfg, NOW)}
    st.set_coverage(RS.SEARCH_KEY, {"ts": NOW - H})
    seen |= {u["argv"][0] for u in RS.UNITS.values()}
    assert seen <= {"qualify", "research", "discover"}
    assert not seen & {"oktech", "book", "sweep", "operate", "copy"}


def test_heavy_job_list_and_the_lab_lock(tmp_path):
    from snipetech.analysis.lab_lock import lab_lock

    assert RS.is_heavy(["lab", "--latency", "0"]) and RS.is_heavy(["qualify", "run", "--max", "900"])
    assert RS.is_heavy(["research", "search"]) and RS.is_heavy(["events", "edge"]) and RS.is_heavy(["copy-rank"])
    assert not RS.is_heavy(["measure", "run"]) and not RS.is_heavy(["events", "scan"])
    assert not RS.is_heavy(["research", "next"]) and not RS.is_heavy(["oktech", "guard"])
    assert RS.lab_held(tmp_path) is False
    with lab_lock(tmp_path, wait=False):
        assert RS.lab_held(tmp_path) is True
    assert RS.lab_held(tmp_path) is False


def test_the_search_ranks_at_the_live_copy_landing_and_paper_tests_what_pays_there(env, monkeypatch):
    from snipetech.service import Service
    from tests.test_lab import whale_world

    st, cfg, data = env
    whale_world(st)
    locks = []
    real = RS._lab_lock

    def spy(path, wait=True):
        locks.append((str(path), wait))
        return real(path, wait=wait)

    monkeypatch.setattr(RS, "_lab_lock", spy)
    out = RS.search(Service(cfg, store=st), cfg, data, NOW)
    assert locks == [(str(data), True)]  # one lab at a time: it queues behind any other lab
    cov = st.coverage(RS.SEARCH_KEY)
    assert cov["ts"] == NOW and cov["landing"] == "+6" and cov == json.loads(json.dumps(out))
    assert cov["strategies"] and all("at the launch" not in s["trigger"] for s in cov["strategies"])
    # launch-window rules (hunt, whale, burst) are scored in the same pass where a hunt buy lands, copies at +6
    from snipetech.analysis import paper

    hunt_key = paper.landing_key(*paper.HUNT_LANDING)
    for s in cov["strategies"]:
        launchy = s["trigger"].startswith(("OKTech hunt", "buy >=", "burst"))
        assert s["landing"] == (hunt_key if launchy else "+6@0"), s["trigger"]
    frozen = st.coverage("paper_strategies")["strategies"]
    assert frozen
    for s in frozen:
        assert (s["latency"], s["position"]) == paper.live_landing(s["trigger"]["kind"], "+6")
    assert [f["id"] for f in cov["frozen"]] == [s["id"] for s in frozen]


def test_the_search_reads_only_the_newest_tape_so_its_cost_stays_flat(env):
    """The tape keeps growing for weeks; a search over all of it would run longer every day and hold the lab lock
    (and the qualify lab behind it) for hours. It reads the newest SEARCH_TAPE_H hours only."""
    st, _cfg, _ = env
    st.set_coverage("tape", {"spans": [[NOW - 10 * 24 * H, NOW - 9 * 24 * H], [NOW - 100 * H, NOW]]})
    window = RS.recent_tape(st, 72)
    assert window.tape_spans() == [(NOW - 72 * H, NOW)]
    assert window.labels() == st.labels() and window.launch("x") is None  # everything else reads the store itself
    st.set_coverage("tape", {"spans": [[NOW - 10 * H, NOW]]})
    assert RS.recent_tape(st, 72).tape_spans() == [(NOW - 10 * H, NOW)]


def _digest_world(st, data):
    _pipeline(st)
    for i, (status, n, mean, lower) in enumerate([("qualified", 40, 0.017, 0.004), ("candidate", 54, 0.006, 0.0002),
                                                  ("retired", 25, -0.01, -0.02)]):  # fmt: skip
        st.upsert_candidate(W[5 + i], "coinfirst", {}, NOW - 2 * H)
        _verdict(st, W[5 + i], NOW - H, status, n=n, mean=mean, lower=lower, next_review=NOW + 20 * H, score=lower)
    st.set_coverage(RS.SEARCH_KEY, {
        "ts": NOW - 2 * H, "landing": "+6", "tape_hours": 52.0, "moments": 300_000, "frozen": [],
        "strategies": [{"trigger": "a tracked wallet's first buy >= 1 SOL", "exit": "SL-15% hold60s", "n": 800,
                        "mean": -0.003, "ev_lower": -0.006, "win_rate": 0.2}],
        "wallets": []})  # fmt: skip
    st.set_coverage("book:last_run", {"ts": NOW - 300, "dry": False, "kill_reason": None, "actions": {}, "ranked": 0,
                                      "best": {"wallet": W[8], "score": 0.0013}})  # fmt: skip
    metas = [_meta(["qualify", "run", "--max", "300"], NOW - 4 * H), _meta(["research", "search"], NOW - 3 * H),
             _meta(["paper", "run"], NOW - H), _meta(["paper", "run"], NOW - 2 * H),
             _meta(["discover", "run"], NOW - 5 * H, exit_code=1), _meta(["measure", "run"], NOW - 60),
             _meta(["research", "next"], NOW - 900), _meta(["lab", "--latency", "0", "--position", "+6"], NOW - 5 * H),
             _meta(["oktech", "guard"], NOW - 2 * H, running=True), _meta(["lab", "--by-wallet"], NOW - 9 * H)]  # fmt: skip
    return OperatorPaths(data).ensure(), metas


def test_the_digest_says_what_ran_what_was_checked_the_best_wallets_and_why_the_book_waits(env):
    st, cfg, data = env
    paths, metas = _digest_world(st, data)
    (paths.root / "BOOK_ON").write_text("{}")
    text = RS.digest_text(st, cfg, paths, NOW, NOW - 6 * H, metas)
    assert len(text) <= RS.MAX_CHARS
    assert "wallet checks once" in text and "strategy search once" in text and "forward paper test twice" in text
    assert "wallet discovery failed" in text  # a failure is said, not hidden
    assert "lab once" in text and "--latency" not in text  # research started by hand counts, in its own name
    assert "measure run" not in text and "research next" not in text and "guard" not in text  # not research
    assert "Wallets checked: 4" in text and "2 passed every check" in text and "1 dropped" in text
    assert "2 wallets never checked" in text and "1 due for a re-check" in text
    assert W[3][:6] in text and "+0.0170" in text and "+0.0040" in text and "on 40 of its buys" in text
    assert W[7][:6] not in text  # dropped wallets are not "best"
    assert "first buyers of runners 3" in text  # sources in words
    assert "Strategy search at +6" in text and "-0.0030" in text
    assert "Book: on but not trading" in text and "0.005" in text and W[8][:6] in text
    assert "Next:" in text


def test_the_digest_says_what_was_learned_and_what_is_tested_next(env):
    """Every six hours the owner also reads what the agents learned since the last digest and the ideas they test
    next; an idea that needs money or a change outside the bounds says it waits for the owner's yes."""
    from snipetech.operator import learning

    st, cfg, data = env
    paths, metas = _digest_world(st, data)
    (paths.root / "BOOK_ON").write_text("{}")
    learning.add_lesson(paths, "an older lesson", "e", 2, source="analyst", now=NOW - 8 * H)
    learning.add_lesson(paths, "Crowded leaders lose at our landing", "4 of 5 losses landed 2-4 behind", 5,
                        source="postmortem", now=NOW - 2 * H)  # fmt: skip
    learning.upsert_idea(paths, {"title": "Copy 9WzD live at 0.1 SOL", "why": "+0.02 a trade on 40 paper trades",
                                 "test": "a 0.1 SOL live measure over 20 copies", "expected": "+0.01 SOL a trade",
                                 "kind": "live-test", "priority": 5}, source="analyst", now=NOW - H)  # fmt: skip
    learning.upsert_idea(paths, {"title": "Re-qualify the top 10 at +9", "why": "crowded landings", "test": "a lab at +9",
                                 "expected": "fewer losers copied", "kind": "research"}, source="analyst", now=NOW - H)  # fmt: skip
    text = RS.digest_text(st, cfg, paths, NOW, NOW - 6 * H, metas + [_meta(["strategist", "run"], NOW - 4 * H)])
    assert len(text) <= RS.MAX_CHARS and "Next:" in text
    assert "strategist once" in text and "weekly" not in text  # it runs every day now
    assert "What it learned since 09:00 UTC:\n- Crowded leaders lose at our landing (5 cases)\n" in text
    assert "older lesson" not in text  # learned before this digest's window
    assert "Testing next:\n- Copy 9WzD live at 0.1 SOL (needs your yes)\n- Re-qualify the top 10 at +9" in text
    assert text.index("Book:") < text.index("What it learned") < text.index("Testing next:")
    quiet = RS.digest_text(st, cfg, OperatorPaths(data / "elsewhere").ensure(), NOW, NOW - 6 * H, metas)
    assert "What it learned: nothing new since 09:00 UTC" in quiet and "Testing next: no open ideas yet" in quiet


def test_a_full_digest_still_ends_with_what_was_learned(env):
    """The learning lines come last: a busy six hours (three long lessons, three long ideas) must not cut them off."""
    from snipetech.operator import learning

    st, cfg, data = env
    paths, metas = _digest_world(st, data)
    for i in range(4):
        learning.add_lesson(paths, f"lesson {i} " + "about crowded landings " * 12, "evidence " * 40, 9, source="analyst",
                            now=NOW - H + i)  # fmt: skip
        learning.upsert_idea(paths, {"title": f"idea {i} " + "copy floor " * 10, "why": "w", "test": "t", "expected": "e",
                                     "kind": "research"}, source="analyst", now=NOW - H)  # fmt: skip
    text = RS.digest_text(st, cfg, paths, NOW, NOW - 6 * H, metas)
    assert len(text) <= RS.MAX_CHARS and not text.endswith("…")
    assert "- and 1 more in operator/lessons.md" in text and text.splitlines()[-1].startswith("- idea 2 copy floor")


def test_with_the_timers_on_the_digest_reads_what_ran_from_the_store(env):
    """systemd runs qualify and discovery without a job file: the digest still says they ran, from what they wrote; a
    wallet checked without any trade on our tape is said apart from the ones measured; when nothing can start, it says
    what waits and until when."""
    st, cfg, data = env
    paths = OperatorPaths(data).ensure()
    st.upsert_candidate(W[0], "coinfirst", {}, NOW - 2 * H)
    _verdict(st, W[0], NOW - H, "candidate", n=0, next_review=NOW + 71 * H, reasons=["no tape data"])
    st.upsert_candidate(W[1], "tape", {}, NOW - 2 * H)
    _verdict(st, W[1], NOW - H, "candidate", n=8, mean=0.002, lower=-0.001, next_review=NOW + 71 * H)
    st.set_coverage("qualify:last_lab", {"ts": NOW - H, "wallets": 2})
    st.set_coverage(RS.SEARCH_KEY, {"ts": NOW - 30 * H})
    text = RS.digest_text(st, cfg, paths, NOW, NOW - 6 * H, [_meta(["research", "search"], NOW - 3 * H, running=True)])
    assert "Ran: strategy search once, wallet checks, wallet discovery" in text
    assert "Wallets checked: 2 (2 need more evidence; 1 of them not yet trading on our tape)" in text
    assert "Next: nothing to start yet (no wallet waits for a check; strategy search already running" in text


def test_the_book_line_says_why_it_is_or_is_not_trading(env):
    st, cfg, data = env
    paths = OperatorPaths(data).ensure()
    assert "off" in RS.book_reason(st, cfg, paths) and "/book on" in RS.book_reason(st, cfg, paths)
    (paths.root / "BOOK_ON").write_text("{}")
    assert "has not run" in RS.book_reason(st, cfg, paths)
    st.set_coverage("book:last_run", {"ts": NOW, "kill_reason": "equity 2.1 below the 3 SOL floor", "ranked": 0})
    assert "halted" in RS.book_reason(st, cfg, paths) and "2.1" in RS.book_reason(st, cfg, paths)
    st.set_coverage("book:last_run", {"ts": NOW, "kill_reason": None, "ranked": 2, "dry": False, "actions": {}})
    assert "copying 2 wallet" in RS.book_reason(st, cfg, paths)
    paths.stop_flag.write_text("x")
    assert "/stop" in RS.book_reason(st, cfg, paths)


def test_the_digest_goes_by_telegram_and_its_window_moves_only_after_a_send(env):
    from snipetech.operator.notify import Notifier

    st, cfg, data = env
    paths, metas = _digest_world(st, data)
    sent = []
    ok = Notifier("token", 42, post=lambda url, body: sent.append(body["text"]))
    out = RS.send_digest(st, cfg, paths, NOW, metas, notifier=ok)
    assert out["sent"] and sent == [out["text"]] and st.coverage(RS.DIGEST_KEY)["ts"] == NOW

    def down(url, body):
        raise RuntimeError("telegram down")

    later = NOW + 6 * H
    out = RS.send_digest(st, cfg, paths, later, metas, notifier=Notifier("token", 42, post=down, log=lambda m: None))
    assert not out["sent"] and st.coverage(RS.DIGEST_KEY)["ts"] == NOW  # the next one covers this window too
    assert "15:00-21:00" in out["text"]
    out = RS.send_digest(st, cfg, paths, later, metas, notifier=ok, dry_run=True)
    assert not out["sent"] and len(sent) == 1 and st.coverage(RS.DIGEST_KEY)["ts"] == NOW
    out = RS.send_digest(st, cfg, paths, later, metas, notifier=Notifier(None, None))
    assert not out["sent"] and st.coverage(RS.DIGEST_KEY)["ts"] == later  # nowhere to send: printed to its job log


def test_the_research_command_is_registered_and_allowed_on_the_job_runner():
    from snipetech import ops as O
    from snipetech.cli import build_parser

    for action in ("next", "search", "digest", "backlog"):
        assert build_parser().parse_args(["research", action]).action == action
    assert build_parser().parse_args(["research", "next", "--dry-run"]).dry_run
    assert "research" in O.ALLOWED


def test_research_next_dry_run_from_the_command_line_starts_nothing(env, monkeypatch, capsys):
    from snipetech import ops as O
    from snipetech.cli import main

    st, cfg, _ = env
    _pipeline(st)
    monkeypatch.setattr(RS, "systemd_services", lambda units=None: {u: "inactive" for u in units or ()})
    monkeypatch.setattr(O.Ops, "op_run", lambda self, argv, timeout=0: pytest.fail(f"started {argv}"))
    assert main(["--db", cfg.storage.db_path, "research", "next", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["would_start"]["unit"] == "qualify" and out["started"] is None


def test_the_strategy_search_never_holds_the_lab_for_hours():
    """On 6 Nov the first search over 72 h of tape held the lab lock for over 77 minutes while the wallet checks the
    book waits for queued behind it. The search reads the newest 24 h and is stopped after 2 hours at most."""
    assert RS.SEARCH_TAPE_H == 24.0 and RS.UNITS["search"]["limit"] <= 2 * 3600


def test_a_job_lost_to_a_deploy_is_retried_soon_but_not_in_a_loop(env):
    # 7 Nov 07:44: a `research search` started at 07:35 died with a deploy ("process gone", no exit code); the 6 h
    # gap between starts would have kept the next search away until 13:35 with nothing to show for the lost one
    st, cfg, _ = env
    units = lambda metas: [o["unit"] for o in RS.plan(RS.measure_backlog(st, cfg, NOW), metas, cfg, NOW)]
    lost = lambda started, ended: _meta(["research", "search"], started, ended=ended, exit_code=None)
    assert "search" not in units([lost(NOW - 40 * 60, NOW - 20 * 60)])  # lost 20 min ago: a short pause first
    assert "search" in units([lost(NOW - 50 * 60, NOW - 40 * 60)])  # lost 40 min ago: run it again
    # lost twice within the back-off: something kills it, not a deploy; wait like a failure
    assert "search" not in units([lost(NOW - 3 * H, NOW - 2 * H), lost(NOW - 50 * 60, NOW - 40 * 60)])
    assert "search" not in units([_meta(["research", "search"], NOW - 50 * 60)])  # a finished one keeps the full gap
