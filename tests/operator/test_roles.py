"""The specialised research agents (docs/superpowers/specs/2026-11-12-specialised-agents-design.md): who they are, what
each may start and use, how the day's dollars are split, and when a role's turn runs. Nothing here trades: a role is a
shorter list of the analyst's research tools, never a longer one."""

import asyncio
import json
import os

import pytest

from snipetech.config import Config
from snipetech.operator import learning
from snipetech.operator import roles as R
from snipetech.operator.budget import Ledger
from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_ACTIONS, RESEARCH_COMMANDS, OperatorJobs
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store

ALLOWED_TODAY = set(RESEARCH_COMMANDS) | {" ".join(a) for a in RESEARCH_ACTIONS}
# every command that could trade, move SOL, switch the book or change a setting, whoever lists it
NEVER = {"oktech", "sweep", "unwrap", "book", "operate", "strategist", "config", "sniper", "snipers", "vanity",
         "qualify", "measure", "discover"}  # fmt: skip


def test_the_five_roles():
    """The fifth, the exit analyst (the owner, 21 Nov 2026 ~23:35 UTC: "make sure you use momentum asw, find the best
    possible config for that, even add an agent for that ... if it's only upping we can hold a bit too but if it's js
    a crash we get out asap", then "Literally add an agent for that idc if costs are higher")."""
    assert R.ROLES == ("scout", "tester", "fees", "oktech", "exits")
    assert set(R.ROLE_COMMANDS) == set(R.ROLE_TOOLS) == set(R.SHARES) == set(R.TABLES) == set(R.ROLES)
    assert set(R.NAMES) == set(R.ROLES) and R.NAMES["exits"] == "exit analyst"


def test_the_exit_analyst_scores_named_exits_at_a_named_place_and_freezes_nothing():
    """Its question is when a copy sells: every lab it starts names the exits it scores (`--exit`), so a lab of a rule's
    own (the tester's) or of a place (the fee analyst's) stays theirs. It reads our landings and which buys pay, and
    never freezes (the tester's), studies a wallet (the scout's) or starts anything that reaches OKTech."""
    exits = R.ROLE_COMMANDS["exits"]
    assert exits == {"lab", "landing", "copy-context"}
    assert R.ROLE_NEEDS[("exits", "lab")] == ("--exit",)
    assert not exits & {"paper", "winners", "place-bids", "oktech", "label"}
    assert {"read_fills", "read_round_trips"} <= R.ROLE_TOOLS["exits"]
    assert "set_candidates" not in R.ROLE_TOOLS["exits"] and "read_fee_policy" not in R.ROLE_TOOLS["exits"]
    assert R.TABLES["exits"] == "exits-best"
    assert R.ROLE_DOCS["exits"] == ("docs/oktech-features.md", "docs/live-recipe.md")
    assert "exits" not in R.TABLE_DEAF  # the tester's rules and the fee analyst's places are its subject too


def test_every_role_command_is_a_research_command_and_never_an_owners():
    for role in R.ROLES:
        assert R.ROLE_COMMANDS[role], role
        assert R.ROLE_COMMANDS[role] <= ALLOWED_TODAY, (role, sorted(R.ROLE_COMMANDS[role] - ALLOWED_TODAY))
        first_words = {c.split()[0] for c in R.ROLE_COMMANDS[role]}
        assert not first_words & OWNER_COMMANDS and not first_words & NEVER, role


def test_every_research_command_has_a_role():
    """A command the analyst may start today and no role may would stop being used the day the roles are switched on:
    the list a new command is added to (jobs.RESEARCH_COMMANDS, RESEARCH_ACTIONS) fails here until it has a role."""
    given = set().union(*R.ROLE_COMMANDS.values())
    assert given == ALLOWED_TODAY, sorted(ALLOWED_TODAY ^ given)


def test_who_may_start_what():
    scout, tester, fees, oktech, exits = (R.ROLE_COMMANDS[r] for r in R.ROLES)
    assert {"winners", "gmgn", "history amm-backfill", "leaders-of", "playbook", "label"} <= scout
    assert {"lab", "paper", "dip-buys", "study"} <= tester
    assert {"place-bids", "tips", "landing", "lab"} <= fees
    assert "lab" not in scout and "paper" not in scout  # the scout never starts a lab or a freeze
    assert "paper" not in fees  # only the tester freezes a rule
    assert not {"winners", "follow", "discover-wallets", "label"} & tester  # the tester starts no wallet study
    assert not {"lab", "paper"} & oktech  # the OKTech analyst scores nothing
    assert "lab" in exits and "paper" not in exits  # the exit analyst scores exits and freezes nothing


def test_the_oktech_role_only_reads():
    """The owner's "an agent that masters oktech's features": it reads our fills and landings and what the other roles
    measured, and writes down what each feature would change. It starts no lab and no freeze, and no command of its
    own could reach OKTech: the command `oktech` (the one that creates, starts and stops snipers) is the owner's."""
    oktech = R.ROLE_COMMANDS["oktech"]
    assert oktech == {"landing", "copy-context", "playbook", "launch-tools"}
    assert not oktech & {"lab", "paper", "oktech", "label", "history amm-backfill"}
    assert "set_candidates" not in R.ROLE_TOOLS["oktech"]
    assert {"read_fills", "read_fee_policy", "read_round_trips"} <= R.ROLE_TOOLS["oktech"]


def test_shares_leave_the_alert_reserve():
    """The exit analyst's tenth comes out of the tester's share, whose exit work it takes over; the reserve for the
    alert rounds, the daily analysis and the post-mortems is untouched."""
    assert R.SHARES == {"scout": 0.20, "tester": 0.25, "fees": 0.20, "oktech": 0.10, "exits": 0.10}
    assert R.RESERVE_SHARE == 0.15
    assert abs(sum(R.SHARES.values()) + R.RESERVE_SHARE - 1.0) < 1e-9
    assert R.ROLE_TURN_USD == 5.0


def test_role_tools_exist_today(tmp_path):
    """A role gets a shorter list of the tools the analyst has today and not one more."""
    from snipetech.config import OperatorConfig
    from snipetech.operator.jobs import OperatorJobs
    from snipetech.operator.paths import OperatorPaths
    from snipetech.operator.tools import NoteSink, make_operator_tools
    from snipetech.store import Store

    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")

    class Svc:
        store = st

    jobs = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main")
    today = {t.name for t in make_operator_tools(Svc(), paths, jobs, OperatorConfig(wallet="W"), NoteSink(paths))}
    st.close()
    for role in R.ROLES:
        # update_table (14 Nov) is the one tool a role has and the analyst has not: rows of the role's own table
        assert R.ROLE_TOOLS[role] - {"update_table"} <= today, (role, sorted(R.ROLE_TOOLS[role] - today))
        assert R.SHARED_TOOLS <= R.ROLE_TOOLS[role]
    assert [r for r in R.ROLES if "set_candidates" in R.ROLE_TOOLS[r]] == ["scout"]
    assert [r for r in R.ROLES if "read_events" in R.ROLE_TOOLS[r]] == ["tester"]
    assert "read_verdicts" not in R.ROLE_TOOLS["fees"] and "read_fills" not in R.ROLE_TOOLS["scout"]


def test_a_role_hands_work_to_another_roles_queue():
    assert R.HANDOFF["scout"] == ("tester", "fees") and R.HANDOFF["tester"] == ("fees",)
    assert R.HANDOFF["fees"] == ("tester",) and R.HANDOFF["oktech"] == ("tester",)
    # a better exit becomes a forward record (the tester's freeze); an OKTech feature it needs, its cheapest test
    assert R.HANDOFF["exits"] == ("tester", "oktech")
    for role, to in R.HANDOFF.items():
        assert role not in to and set(to) <= set(R.ROLES)


# -- when a role's turn runs: `role_due`, and one turn of it: `run_role` (`snipetech operate agent --role <role>`) -----

NOW = 1_793_923_200 + 12 * 3600  # Friday 6 Nov 2026, 12:00 UTC
H = 3600


@pytest.fixture
def box(tmp_path):
    """A data directory as the server has one, the agents switched on, and a turn that calls no model: `turn` records
    what it was asked and may be given something to do (`box.acts`: called with the turn's jobs object)."""
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    (data / "jobs").mkdir()
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.role_agents = True
    cfg.operator.wallet = "W"

    class Svc:
        store = st

    class Box:
        pass

    b = Box()
    b.cfg, b.svc, b.paths, b.store, b.data, b.calls, b.acts, b.usd = cfg, Svc(), paths, st, data, [], [], 0.5

    def turn(cfg, svc, paths, snap, decision, ledger, notifier, notes, *, jobs, now, role, **kw):
        b.calls.append({"role": role, "mode": decision.mode, "reasons": list(decision.reasons), "snap": snap})
        ledger.record(role, {"output_tokens": int(b.usd * 25_000)}, now())  # 40 $ a million output tokens
        for act in b.acts:
            act(jobs)
        notes.write(f"{role}: did its turn", "info")
        return {"usd": b.usd, "tool_calls": 3, "stopped": "end_turn", "notes": 1}

    def run(role, now=NOW, **kw):
        kw.setdefault("turn", turn)
        kw.setdefault("client", object())  # a stand-in: no API key is asked for, no model is called
        return asyncio.run(R.run_role(cfg, b.svc, role, repo=tmp_path, branch="main", now=lambda: now, **kw))

    b.run = run
    yield b
    st.close()


def _idea(paths, role, title=None, by="cloud", now=NOW, **over):
    fields = {"title": title or f"an idea of the {role}", "why": "w", "test": "t", "expected": "e", "kind": "research",
              "role": role, **over}  # fmt: skip
    return learning.upsert_idea(paths, fields, source=by, now=now)["id"]


def _job(data, jid, argv, started, ended=None, pid=None):
    meta = {"id": jid, "argv": argv, "started": started, "ended": ended, "exit_code": None if ended is None else 0,
            "running": ended is None, "pid": pid}  # fmt: skip
    (data / "jobs" / f"{jid}.json").write_text(json.dumps(meta))
    if ended is not None:
        (data / "jobs" / f"{jid}.exit").write_text("0")
        os.utime(data / "jobs" / f"{jid}.exit", (ended, ended))


def _state(paths, role, **values):
    return R.update_state(paths, role, **values)


def _due(box, role, now=NOW):
    return R.role_due(role, box.paths, now, R.read_state(box.paths).get(role, {}), R.job_metas(box.paths, now),
                      store=box.store)  # fmt: skip


def test_a_roles_first_turn_is_due(box):
    assert _due(box, "scout") == (True, "first turn")


def test_role_skips_when_nothing_is_new(box):
    _state(box.paths, "scout", last=NOW - 900)
    assert _due(box, "scout") == (False, "nothing new")
    out = box.run("scout")
    assert out["ran"] is False and out["why"] == "nothing new" and box.calls == []
    assert box.paths.log.read_text().strip().endswith("agent scout: skip: nothing new")
    assert Ledger(box.paths.spend, {}).spent_today(NOW) == 0.0  # a skipped turn costs nothing
    assert R.read_state(box.paths)["scout"]["last"] == NOW - 900  # and is no turn


def test_role_runs_when_its_own_job_ended(box):
    idea = _idea(box.paths, "scout", status="testing", now=NOW - 2 * H)
    _state(box.paths, "scout", last=NOW - 900)
    R.record_test(box.paths, NOW - 1000, "scout", idea, "j1", ["winners", "--json"])
    R.record_test(box.paths, NOW - 1000, "tester", "I9", "j2", ["lab", "--json"])
    _job(box.data, "j1", ["winners", "--json"], NOW - 1000, pid=os.getpid())  # it runs
    _job(box.data, "j2", ["lab", "--json"], NOW - 1000, ended=NOW - 300)  # the tester's: not the scout's news
    assert _due(box, "scout") == (False, "nothing new")
    _job(box.data, "j1", ["winners", "--json"], NOW - 1000, ended=NOW - 300)
    assert _due(box, "scout") == (True, "a job of yours ended: j1")
    assert _due(box, "tester")[1] == "first turn"


def test_role_runs_for_an_untested_idea(box):
    _state(box.paths, "fees", last=NOW - 900)
    idea = _idea(box.paths, "fees", now=NOW - 2 * H)  # in its queue for two hours, never tested
    assert _due(box, "fees") == (True, f"not tested yet: {idea}")
    # while a job of its own runs it waits for the result instead of starting the next test
    R.record_test(box.paths, NOW - 600, "fees", idea, "j1", ["tips", "--json"])
    _job(box.data, "j1", ["tips", "--json"], NOW - 600, pid=os.getpid())
    assert _due(box, "fees") == (False, "nothing new")
    # an idea that needs the owner's yes is not the role's to test
    learning.upsert_idea(box.paths, {"id": idea, "owner_needed": True}, source="cloud", now=NOW - 2 * H)
    (box.data / "jobs" / "j1.json").unlink()
    assert _due(box, "fees") == (False, "nothing new")


def test_role_runs_when_handed_something(box):
    for role in ("tester", "scout"):
        _state(box.paths, role, last=NOW - 900)
    handed = _idea(box.paths, "tester", "score copies of this group", by="scout", status="testing", now=NOW - 300)
    assert _due(box, "tester") == (True, f"handed an idea: {handed} (by the scout)")
    assert _due(box, "scout") == (False, "nothing new")  # what it handed on is not its own news
    _state(box.paths, "tester", last=NOW - 100, stalled_told=NOW - 100)  # its last turn saw it and left it in testing
    assert _due(box, "tester") == (False, "nothing new")  # seen by its last turn
    table = box.paths.root / "fees-bids.md"
    table.write_text("| place | bid |\n")
    os.utime(table, (NOW - 50, NOW - 50))
    assert _due(box, "tester") == (True, "the fees table changed (operator/fees-bids.md)")
    _state(box.paths, "tester", last=NOW - 40)  # its turn has seen that table
    own = box.paths.root / "tester-rules.md"
    own.write_text("| rule |\n")
    os.utime(own, (NOW - 10, NOW - 10))
    assert _due(box, "tester") == (False, "nothing new")  # its own table is not its news
    _state(box.paths, "scout", last=NOW - 30)
    assert _due(box, "scout") == (True, "the tester table changed (operator/tester-rules.md)")


def test_role_runs_on_its_own_news(box):
    """The scout: a wallet qualified or was banned. The tester: the strategy search ended, or a paper rule reached one
    of its first two readings (30 trades, then 30 more). The fee analyst and the OKTech analyst: a new fill of ours.

    14 Nov: the scout's news compared all four verdict counts and the candidate count moved at every reading (76,496,
    76,594, 77,125, 77,091: discover brings wallets every hour); the tester's fired at every multiple of 30 of each
    of 104 paper rules. Either would have started a turn every round once nothing else did."""
    for role in R.ROLES:
        _state(box.paths, role, last=NOW - 900, seen=R.seen(role, box.store))
        assert _due(box, role) == (False, "nothing new")
    box.store.upsert_candidate("W1", "tape", {}, NOW - 100)  # one candidate more, one retired: counts that always move
    box.store.upsert_candidate("W0", "tape", {}, NOW - 100, status="retired")
    assert _due(box, "scout") == (False, "nothing new")
    box.store.upsert_candidate("W2", "tape", {}, NOW - 100, status="qualified")
    assert _due(box, "scout") == (True, "the qualified or the banned wallets changed (read_verdicts)")
    _state(box.paths, "scout", seen=R.seen("scout", box.store))
    assert _due(box, "scout") == (False, "nothing new")
    box.store.set_candidate_status("W1", "banned")
    assert _due(box, "scout") == (True, "the qualified or the banned wallets changed (read_verdicts)")
    _job(box.data, "s1", ["research", "search"], NOW - 2000, ended=NOW - 100)
    assert _due(box, "tester") == (True, "the strategy search ended: s1")
    (box.data / "jobs" / "s1.json").unlink()

    def trades(n):
        for i in range(n):
            box.store.put_paper_trade({"strategy": "S1", "mint": f"M{i}", "trigger_ts": NOW - 500 + i, "pnl": 0.01})
        return _due(box, "tester")

    assert trades(29) == (False, "nothing new")
    assert trades(30) == (True, "a paper rule reached its next reading: S1 (30 trades)")
    _state(box.paths, "tester", seen=R.seen("tester", box.store))
    assert trades(59) == (False, "nothing new")
    assert trades(61) == (True, "a paper rule reached its next reading: S1 (61 trades)")
    _state(box.paths, "tester", seen=R.seen("tester", box.store))
    assert trades(95) == (False, "nothing new")  # the third reading and every later one: the paper report's, no turn's
    _state(box.paths, "tester", seen=R.seen("tester", box.store))
    assert trades(125) == (False, "nothing new")
    box.store.put_fill({"sig": "B1", "ts": NOW - 100, "wallet": "W", "mint": "M", "side": "buy", "sol": 0.2})
    assert _due(box, "fees") == (True, "a new fill of ours (read_fills)")
    assert _due(box, "oktech") == (True, "a new fill of ours (read_fills)")


def test_a_research_record_reaching_its_next_reading_is_no_news_for_the_tester(box):
    """A rule scored without serial crews' coins (every launch-type rule frozen before 14 Nov) is no rule a sniper
    trades: its 30th trade wakes nobody. The same for a rule frozen since."""
    hunt = {"kind": "hunt", "sol": 5.0, "window": 1.0}
    box.store.set_coverage("paper_strategies", {"strategies": [
        {"id": "OLD", "label": "old", "trigger": hunt, "exit": {"watch_secs": 1.3}, "from_ts": NOW - 9000},
        {"id": "NEW", "label": "new", "trigger": hunt | {"sol": 6.0}, "exit": {"watch_secs": 1.3},
         "from_ts": NOW - 9000, "every_coin": True}], "superseded": []})  # fmt: skip
    _state(box.paths, "tester", last=NOW - 900, seen=R.seen("tester", box.store))
    for i in range(30):
        box.store.put_paper_trade({"strategy": "OLD", "mint": f"M{i}", "trigger_ts": NOW - 500 + i, "pnl": 0.01})
    assert _due(box, "tester") == (False, "nothing new")
    for i in range(30):
        box.store.put_paper_trade({"strategy": "NEW", "mint": f"N{i}", "trigger_ts": NOW - 500 + i, "pnl": 0.01})
    assert _due(box, "tester") == (True, "a paper rule reached its next reading: NEW (30 trades)")


def test_role_runs_after_three_quiet_hours(box):
    _state(box.paths, "oktech", last=NOW - 3 * H + 600)
    assert _due(box, "oktech") == (False, "nothing new")
    _state(box.paths, "oktech", last=NOW - 3 * H + 200)  # within the round's slack of three hours
    assert _due(box, "oktech") == (True, "three quiet hours since its last turn")


def test_role_waits_after_two_turns_without_a_test(box):
    """Two turns in a row that did no work (no test started, no idea closed, no lesson, nothing handed on): the next
    one waits 45 minutes from the last turn's start, unless a job of its own has ended or an idea was handed to it.
    45 minutes, not an hour: with rounds a quarter of an hour apart an hour's wait started the next turn 60 or 75
    minutes on, just past the hour cache's lifetime, and the whole list was paid for again at the hour's price."""
    assert R.IDLE_WAIT_SECS == 45 * 60 and R.IDLE_WHY.startswith("waits 45 minutes after two turns ")
    _idea(box.paths, "scout", "one", now=NOW - 5 * H)
    assert box.run("scout", now=NOW)["ran"] and R.read_state(box.paths)["scout"]["idle"] == 1
    assert box.run("scout", now=NOW + 900)["ran"] and R.read_state(box.paths)["scout"]["idle"] == 2
    out = box.run("scout", now=NOW + 1800)
    assert out == {"role": "scout", "ran": False, "why": R.IDLE_WHY}
    assert len(box.calls) == 2
    idea = learning.list_ideas(box.paths, "all")[0]["id"]
    R.record_test(box.paths, NOW + 100, "scout", idea, "j1", ["winners"])
    _job(box.data, "j1", ["winners"], NOW + 100, ended=NOW + 1700)
    assert box.run("scout", now=NOW + 1800)["why"] == "a job of yours ended: j1"  # its own result is always read
    last = NOW + 1800
    assert R.read_state(box.paths)["scout"]["idle"] == 3
    # the rounds of 14 Nov came 873 to 928 seconds apart: the third round after the turn is on time, never the fourth
    assert box.run("scout", now=last + 909)["why"] == R.IDLE_WHY
    assert box.run("scout", now=last + 909 + 873)["why"] == R.IDLE_WHY
    assert box.run("scout", now=last + 909 + 873 + 873)["ran"]  # 44 min 15 s after its start: the round's slack


def test_the_idle_wait_never_holds_an_idea_handed_to_the_role(box):
    """14 Nov: inside its wait nothing but an ended job called a role, not even the owner's question handed to it by
    the cloud session; the wait was compared without the round's slack and often lasted 75 minutes."""
    _state(box.paths, "oktech", last=NOW, idle=2, seen={})
    table = box.paths.root / "scout-facts.md"
    table.write_text("| wallet |\n")
    os.utime(table, (NOW + 60, NOW + 60))
    assert _due(box, "oktech", NOW + 909) == (False, R.IDLE_WHY)  # a table change waits
    asked = _idea(box.paths, "oktech", "the owner asks: which exit sells this rule's coin", by="cloud", now=NOW + 120)
    assert _due(box, "oktech", NOW + 909) == (True, f"handed an idea: {asked} (by the cloud)")


def test_a_turn_that_starts_a_test_or_closes_an_idea_is_not_idle(box):
    idea = _idea(box.paths, "scout", now=NOW - 5 * H)
    _state(box.paths, "scout", last=NOW - 900, idle=1)
    box.acts.append(lambda jobs: R.record_test(box.paths, NOW, "scout", idea, "j9", ["winners"]))
    assert box.run("scout")["ran"] and R.read_state(box.paths)["scout"]["idle"] == 0
    box.acts[:] = [lambda jobs: learning.upsert_idea(box.paths, {"id": idea, "status": "rejected"}, source="scout",
                                                      now=NOW + 3 * H, role="scout")]  # fmt: skip
    _state(box.paths, "scout", idle=1)
    assert box.run("scout", now=NOW + 3 * H)["ran"] and R.read_state(box.paths)["scout"]["idle"] == 0


def test_an_idea_opened_and_closed_in_one_turn_does_not_reset_the_idle_count(box):
    """14 Nov: one role opened an idea of its own and closed it in the same turn 20 times of 22, started no test, and
    so never reached the idle wait: every round a turn. An idea that was in the queue before the turn began and is
    closed in it still counts as work."""

    def open_and_close(at):
        def act(jobs):
            new = _idea(box.paths, "oktech", f"a study of my own at {at}", by="oktech", now=at + 60)
            learning.upsert_idea(box.paths, {"id": new, "status": "adopted"}, source="oktech", now=at + 120,
                                 role="oktech")  # fmt: skip

        return [act]

    box.acts[:] = open_and_close(NOW)
    assert box.run("oktech", now=NOW)["ran"] and R.read_state(box.paths)["oktech"]["idle"] == 1
    box.acts[:] = open_and_close(NOW + 3 * H)
    assert box.run("oktech", now=NOW + 3 * H)["ran"] and R.read_state(box.paths)["oktech"]["idle"] == 2
    table = box.paths.root / "scout-facts.md"
    table.write_text("| wallet |\n")
    os.utime(table, (NOW + 3 * H + 600, NOW + 3 * H + 600))
    box.acts[:] = []
    assert box.run("oktech", now=NOW + 3 * H + 900) == {"role": "oktech", "ran": False, "why": R.IDLE_WHY}
    # an older idea closed in a turn is work: the count starts again
    older = _idea(box.paths, "oktech", "handed by the scout", by="scout", now=NOW + 3 * H + 1000)
    box.acts[:] = [lambda jobs: learning.upsert_idea(box.paths, {"id": older, "status": "rejected"}, source="oktech",
                                                      now=NOW + 5 * H + 60, role="oktech")]  # fmt: skip
    assert box.run("oktech", now=NOW + 5 * H)["ran"] and R.read_state(box.paths)["oktech"]["idle"] == 0


def test_a_lesson_or_a_hand_on_is_work(box):
    """The review of 14 Nov: with "an idea opened and closed in one turn is no work" alone, 15 of the OKTech agent's 26
    turns would have been skipped, and those turns wrote 12 lessons and handed 2 ideas on: less research, not waste.
    A turn is idle only when it started no job, closed no idea that was there before it, added no lesson and handed
    nothing to another role."""
    started = NOW

    def after(act):
        for name in ("ideas.json", "tests.jsonl", "lessons.md"):
            if (box.paths.root / name).exists():
                (box.paths.root / name).write_text("")
        old = _idea(box.paths, "oktech", "an older idea of its own", by="oktech", now=started - H)
        act(old)
        return R._idle_after(box.paths, "oktech", started, 1)

    def own(title="mine now"):
        new = _idea(box.paths, "oktech", title, by="oktech", now=started + 30)
        learning.upsert_idea(box.paths, {"id": new, "status": "adopted"}, source="oktech", now=started + 60,
                             role="oktech")  # fmt: skip

    def lesson(by="oktech", at=started + 30, text="a lesson of this turn"):
        learning.add_lesson(box.paths, text, "evidence", 12, source=by, now=at)

    assert after(lambda old: None) == 2  # nothing at all (a start_job that was refused leaves nothing either)
    assert after(lambda old: own()) == 2  # only an idea of its own opened and closed
    assert after(lambda old: lesson()) == 0
    assert after(lambda old: (own(), lesson())) == 0  # the self-set study that taught something
    assert after(lambda old: _idea(box.paths, "tester", "for the tester", by="oktech", now=started + 30)) == 0
    assert after(lambda old: lesson(by="scout")) == 2  # another role's lesson is not this role's work
    assert after(lambda old: lesson(at=started - 60, text="a lesson of the turn before")) == 2
    assert after(lambda old: _idea(box.paths, "tester", "by the scout", by="scout", now=started + 30)) == 2
    assert after(lambda old: _idea(box.paths, "tester", "handed before", by="oktech", now=started - 60)) == 2
    assert after(lambda old: learning.upsert_idea(box.paths, {"id": old, "status": "adopted"}, source="oktech",
                                                  now=started + 60, role="oktech")) == 0  # fmt: skip
    assert after(lambda old: R.record_test(box.paths, started + 30, "oktech", old, "j8", ["landing", "--json"])) == 0
    assert after(lambda old: R.join_test(box.paths, started + 30, "oktech", old, "j9", ["landing", "--json"])) == 0
    # through a turn: the count a lesson leaves is 0, whatever it was
    _state(box.paths, "oktech", last=NOW + 3 * H - 900, idle=1)
    box.acts[:] = [lambda jobs: learning.add_lesson(box.paths, "landing +2 pays", "n 31", 31, source="oktech",
                                                    now=NOW + 3 * H + 60)]  # fmt: skip
    table = box.paths.root / "scout-facts.md"
    table.write_text("| wallet |\n")
    os.utime(table, (NOW + 3 * H - 60, NOW + 3 * H - 60))
    assert box.run("oktech", now=NOW + 3 * H)["ran"] and R.read_state(box.paths)["oktech"]["idle"] == 0


# -- starts that buy nothing (14 Nov: 660 of 667 decisions started a turn of about a dollar) ---------------------------


def test_a_job_the_role_read_after_it_ended_is_not_news(box):
    """34 of 127 jobs ended inside the turn that started them and were read there; the next round still started a turn
    "a job of yours ended: <that job>" (12 turns, 9.35 $). A read while the job still ran is no read of its result."""
    idea = _idea(box.paths, "scout", status="testing", now=NOW - 2 * H)
    _state(box.paths, "scout", last=NOW - 900)
    for jid in ("j1", "j2"):
        R.record_test(box.paths, NOW - 800, "scout", idea, jid, ["winners", "--json", jid])
    _job(box.data, "j1", ["winners", "--json", "j1"], NOW - 800, ended=NOW - 700)  # ended inside the turn
    _job(box.data, "j2", ["winners", "--json", "j2"], NOW - 800, pid=os.getpid())  # still runs
    assert _due(box, "scout") == (True, "a job of yours ended: j1")
    assert R.job_over(box.paths, "j1") and not R.job_over(box.paths, "j2") and not R.job_over(box.paths, "none")
    R.note_job_read(box.paths, "scout", "j1", NOW - 650, '{"wallets": 3}')
    assert R.jobs_read(box.paths, "scout") == {"j1"} and R.jobs_read(box.paths, "tester") == set()
    assert _due(box, "scout") == (False, "nothing new")
    _job(box.data, "j2", ["winners", "--json", "j2"], NOW - 800, ended=NOW - 100)
    assert _due(box, "scout") == (True, "a job of yours ended: j2")  # only the one it has not read
    for nothing in ("", "  \n", "error: this log is not one JSON object: read its last lines, without key"):
        R.note_job_read(box.paths, "scout", "j2", NOW - 50, nothing)  # an answer that showed nothing is no read
    assert _due(box, "scout") == (True, "a job of yours ended: j2")
    # the record sits beside the state, holds a day, and never grows past it
    assert "j1" not in (box.paths.root / "agents.json").read_text()
    assert json.loads((box.paths.root / R.READ_FILE).read_text()) == {"scout": {"j1": NOW - 650}}
    R.note_job_read(box.paths, "tester", "j2", NOW + 86400, "its result")
    R.note_job_read(box.paths, "scout", "j2", NOW + 86400, "its result")
    assert R.jobs_read(box.paths, "scout") == {"j2"} and R.jobs_read(box.paths, "tester") == {"j2"}
    assert R.read_state(box.paths)["scout"]["last"] == NOW - 900  # the state is as it was


def test_a_read_in_a_turn_that_did_not_end_by_itself_is_no_read(box):
    """A turn the API refused mid-way, one cut at a cap, and one that failed did not finish with what they read: the
    job stays "a job of yours ended" for the next round. Only a turn that ended by itself has read it."""
    idea = _idea(box.paths, "scout", status="testing", now=NOW - 2 * H)

    def reads(jid, stopped):
        def turn(cfg, svc, paths, snap, decision, ledger, notifier, notes, *, jobs, now, role, **kw):
            R.record_test(paths, now() + 10, role, idea, jid, ["winners", "--json", jid])
            _job(box.data, jid, ["winners", "--json", jid], now() + 10, ended=now() + 40)  # short: over in the turn
            R.note_job_read(paths, role, jid, now() + 60, "its result")
            if stopped == "boom":
                raise RuntimeError("boom")
            return {"usd": 0.3, "tool_calls": 2, "stopped": stopped, "notes": 0}

        return turn

    waits = _idea(box.paths, "scout", "one that waits, so every round has a reason", now=NOW - 5 * H)
    _state(box.paths, "scout", last=NOW - 900)
    cut = (("j1", "api_error"), ("j2", "boom"), ("j3", "tool_calls"), ("j4", "wall_time"))
    for k, (jid, stopped) in enumerate(cut):
        at = NOW + k * 900
        box.run("scout", now=at, turn=reads(jid, stopped))
        assert len(box.paths.log.read_text().splitlines()) >= 2 * (k + 1)  # the turn was started
        assert R.jobs_read(box.paths, "scout") == set(), stopped
        why = _due(box, "scout", at + 900)[1]
        assert why.startswith("a job of yours ended: ") and jid in why, stopped  # told at the next round
    box.run("scout", now=NOW + 4 * 900, turn=reads("j5", "end_turn"))
    assert R.jobs_read(box.paths, "scout") == {"j5"}
    assert _due(box, "scout", NOW + 5 * 900) == (True, f"not tested yet: {waits}")  # j5 is read: no news


def test_a_label_job_is_never_a_job_of_yours_ended(box):
    """`label` writes one name and prints it: there is nothing to read. One that failed is still told."""
    idea = _idea(box.paths, "scout", status="testing", now=NOW - 2 * H)
    _state(box.paths, "scout", last=NOW - 900)
    label = ["label", "W1", "GMGN smart money, 30 d +15k USD", "--kind", "cand"]
    R.record_test(box.paths, NOW - 800, "scout", idea, "l1", label)
    _job(box.data, "l1", label, NOW - 800, ended=NOW - 790)
    assert _due(box, "scout")[1].startswith(f"your idea {idea} is in testing")  # called for the idea, not the label
    _state(box.paths, "scout", stalled_told=NOW - 900)
    assert _due(box, "scout") == (False, "nothing new")
    R.record_test(box.paths, NOW - 800, "scout", idea, "l2", label)
    _job(box.data, "l2", label, NOW - 800, ended=NOW - 790)
    (box.data / "jobs" / "l2.exit").write_text("1")
    os.utime(box.data / "jobs" / "l2.exit", (NOW - 790, NOW - 790))
    assert _due(box, "scout") == (True, "a job of yours ended: l2")


def _refused(box):
    """A turn the API refuses for want of credit, as turn.run_turn records and returns one."""
    from snipetech.operator import resources

    def turn(cfg, svc, paths, snap, decision, ledger, notifier, notes, *, jobs, now, role, **kw):
        box.calls.append({"role": role, "refused": True})
        resources.credit_refused(svc.store, ledger, now())
        return {"usd": 0.0, "tool_calls": 0, "stopped": "api_error", "notes": 0,
                "error": "400: Your credit balance is too low to access the Anthropic API."}  # fmt: skip

    return turn


def test_while_the_account_is_empty_one_role_a_round_asks(box):
    """13 and 14 Nov: 471 turns were started against an empty account, four processes a round each building its whole
    message to be refused. One role a round asks, each in its turn; the others skip in a few words. The first turn
    that is answered ends it."""
    refused = _refused(box)
    assert box.run("scout", turn=refused)["stopped"] == "api_error"  # the account runs dry: the refusal records it
    asked = []
    for k in range(1, len(R.ROLES) + 1):
        box.calls.clear()
        outs = {r: box.run(r, now=NOW + k * 900, turn=refused) for r in R.ROLES}
        (asker,) = [c["role"] for c in box.calls]
        asked.append(asker)
        for r in R.ROLES:
            if r != asker:
                assert outs[r] == {"role": r, "ran": False,
                                   "why": f"the account is empty: the {asker} asks this round"}  # fmt: skip
    assert sorted(asked) == sorted(R.ROLES)  # each role is tried in turn
    assert f"agent {asked[0]}: turn: first turn" in box.paths.log.read_text()
    n = len(R.ROLES)  # each round one role asks and every other skips in a few words
    assert box.paths.log.read_text().count("skip: the account is empty: the ") == n * (n - 1)
    # the role whose round it is has nothing new: the next one that has asks, so a round is never lost
    k = 5
    first = R.ROLES[(R.ROLES.index(asked[-1]) + 1) % len(R.ROLES)]  # the turn goes round by who asked last
    _state(box.paths, first, last=NOW + k * 900 - 60, seen=R.seen(first, box.store))
    box.calls.clear()
    outs = {r: box.run(r, now=NOW + k * 900, turn=refused) for r in R.ROLES}
    assert outs[first]["why"] == "nothing new" and len(box.calls) == 1 and box.calls[0]["role"] != first
    _state(box.paths, first, last=None)
    # the owner adds credit: the role that asks is answered, which ends it at once for the roles that start after it
    k = 6
    at = (R.ROLES.index(box.calls[0]["role"]) + 1) % len(R.ROLES)
    box.calls.clear()
    outs = {r: box.run(r, now=NOW + k * 900) for r in R.ROLES}
    assert [r for r in R.ROLES if outs[r]["ran"]] == list(R.ROLES[at:]) == [c["role"] for c in box.calls]
    assert all(outs[r]["why"].startswith("the account is empty") for r in R.ROLES[:at])
    box.calls.clear()
    outs = {r: box.run(r, now=NOW + 7 * 900) for r in R.ROLES}  # and the next round is a round like any other
    assert "the account is empty" not in json.dumps(outs) and [c["role"] for c in box.calls] == list(R.ROLES[:at])


def _round(box, order, at, turn_of):
    """One round with the four roles' processes deciding in `order`, each while the one that asked before it is still
    waiting for the API's answer (the four start in the same second: 14 Nov). Returns who asked."""
    asked = []

    async def proc(i):
        if i == len(order):
            return
        went = []

        async def turn(cfg, svc, paths, snap, decision, ledger, notifier, notes, *, jobs, now, role, **kw):
            went.append(role)
            asked.append(role)
            await proc(i + 1)  # the others decide while this request is on its way
            return turn_of(svc, ledger, now, role)

        await R.run_role(box.cfg, box.svc, order[i], repo=box.data.parent, branch="main", now=lambda: at(order[i]),
                         turn=turn, client=object())  # fmt: skip
        if not went:
            await proc(i + 1)

    asyncio.run(proc(0))
    return asked


def test_one_role_asks_whichever_process_decides_first_and_while_its_request_is_on_its_way(box):
    """The review of 14 Nov: the four processes decide in the same second. With each one asking its own clock who is
    first, a role whose request was still on its way was no longer "due" to the next process, which then asked too
    (1.71 askers a round over every order, up to all four), and two processes on two sides of a quarter-hour could
    each point at the other. The first to decide writes down who asks (operator/agents-asker.json, under the state's
    lock); the others read it."""
    import itertools

    from snipetech.operator import resources

    def refused(svc, ledger, now, role):
        resources.credit_refused(svc.store, ledger, now())
        return {"usd": 0.0, "tool_calls": 0, "stopped": "api_error", "notes": 0,
                "error": "400: Your credit balance is too low to access the Anthropic API."}  # fmt: skip

    resources.credit_refused(box.store, Ledger(box.paths.spend, box.cfg.operator.prices), NOW)
    for r in R.ROLES:
        _state(box.paths, r, last=NOW - 4 * H, seen=R.seen(r, box.store))
    seen = []
    for k, order in enumerate(itertools.permutations(R.ROLES), start=1):
        asked = _round(box, order, lambda role, k=k: NOW + k * 900, refused)
        assert len(asked) == 1, (order, asked)
        seen.append(asked[0])
    n = len(R.ROLES)
    assert all(seen[i + 1] == R.ROLES[(R.ROLES.index(seen[i]) + 1) % n] for i in range(len(seen) - 1))  # in turn
    # two processes on two sides of a quarter of an hour: still one, and the same one for all five
    edge = (NOW // 900 + 40) * 900
    asked = _round(box, R.ROLES, lambda role: edge + (2 if role in R.ROLES[:2] else -2), refused)
    assert len(asked) == 1
    # a record of an older round names nobody for this one
    record = json.loads((box.paths.root / R.ASKER_FILE).read_text())
    assert record["role"] == asked[0] and abs(record["ts"] - edge) <= 2


def test_a_table_change_alone_starts_no_turn_when_the_owner_says_so(box):
    """Every role rewrites its table every turn, which made the other three due: one unbroken chain while there was
    credit (32 of 105 turns, 31 $, ran only for that). `[operator] wake_on_table` is true as it always was; off, the
    other tables still come in every opening message, and a handed idea, a fill and the three-hour look still wake."""
    assert Config().operator.wake_on_table is True
    _state(box.paths, "oktech", last=NOW - 900, seen={})
    _state(box.paths, "fees", last=NOW - 900, seen={})
    table = box.paths.root / "scout-facts.md"
    table.write_text("| wallet |\n")
    os.utime(table, (NOW - 50, NOW - 50))
    assert _due(box, "fees") == (True, "the scout table changed (operator/scout-facts.md)")  # as today
    assert R.start_rules(box.cfg, box.paths)["wake_on_table"] is True
    box.cfg.operator.wake_on_table = False
    rules = R.start_rules(box.cfg, box.paths)
    assert rules["wake_on_table"] is False

    def due(role, now=NOW):
        return R.role_due(role, box.paths, now, R.read_state(box.paths).get(role, {}), R.job_metas(box.paths, now),
                          store=box.store, **rules)  # fmt: skip

    assert due("oktech") == (False, "nothing new")
    out = box.run("oktech")
    assert out == {"role": "oktech", "ran": False, "why": "nothing new"} and box.calls == []
    assert due("oktech", NOW - 900 + 3 * H) == (True, "three quiet hours since its last turn")
    handed = _idea(box.paths, "oktech", "which feature sells this rule's coin", by="tester", now=NOW - 300)
    assert due("oktech") == (True, f"handed an idea: {handed} (by the tester)")
    learning.upsert_idea(box.paths, {"id": handed, "status": "rejected"}, source="oktech", now=NOW - 200, role="oktech")
    box.store.put_fill({"sig": "B1", "ts": NOW - 100, "wallet": "W", "mint": "M", "side": "buy", "sol": 0.2})
    assert due("oktech") == (True, "a new fill of ours (read_fills)")
    # the owner's switch, without a deploy: a file beside the budget, either way
    box.cfg.operator.wake_on_table = True
    assert R.set_wake_table(box.paths, "off") == {"wake_on_table": "off", "by": "the file operator/WAKE_TABLE"}
    assert R.start_rules(box.cfg, box.paths)["wake_on_table"] is False
    _state(box.paths, "tester", last=NOW - 900, seen=R.seen("tester", box.store))
    assert box.run("tester") == {"role": "tester", "ran": False, "why": "nothing new"}
    box.cfg.operator.wake_on_table = False
    R.set_wake_table(box.paths, "on")
    assert R.start_rules(box.cfg, box.paths)["wake_on_table"] is True
    assert box.run("tester")["why"] == "the scout table changed (operator/scout-facts.md)"
    with pytest.raises(ValueError, match="on or off"):
        R.set_wake_table(box.paths, "maybe")


def test_another_roles_table_never_wakes_the_oktech_analyst(box):
    """20 Nov audit (19 Nov 16:15 to 20 Nov 01:37): the scout's table woke the OKTech analyst 19 times (8.86 $) and
    those turns started no job; 9 of its own notes said nothing was new (no fill since 12 Nov). Its subject is our
    fills and OKTech's features: a fill, an idea handed to it, a job of its own and the three-hour look still wake
    it. The same table still wakes the fee analyst (14 turns, 10 jobs started) and the tester's the scout (11, 12)."""
    _state(box.paths, "oktech", last=NOW - 900, seen={})
    _state(box.paths, "fees", last=NOW - 900, seen={})
    for name in ("scout-facts.md", "tester-rules.md", "fees-bids.md"):
        table = box.paths.root / name
        table.write_text("| row |\n")
        os.utime(table, (NOW - 50, NOW - 50))
    assert _due(box, "oktech") == (False, "nothing new")
    assert _due(box, "fees") == (True, "the scout table changed (operator/scout-facts.md)")
    handed = _idea(box.paths, "oktech", "which feature sells this rule's coin", by="tester", now=NOW - 300)
    assert _due(box, "oktech") == (True, f"handed an idea: {handed} (by the tester)")


def test_the_least_gap_between_a_roles_turns_is_the_owners_every(box):
    """Until 14 Nov the owner's `/budget every` only switched the roles on and a role turned every round. It is the
    least gap between a role's turns now. The server's file says 0.25 h, a round: nothing changes until the owner
    changes it, and without a value of the owner's there is no gap at all (the config's 2 h was never meant for the
    agents). Where the owner's value is the default 2 h (`/budget 200` writes it with its profile) a job of its own
    that ended and that it has not read still calls a role at once: the test, read, next test chain keeps its pace."""
    from snipetech.operator.budget_switch import set_budget

    idea = _idea(box.paths, "tester", status="testing", now=NOW - 2 * H)
    table = box.paths.root / "scout-facts.md"
    table.write_text("| wallet |\n")

    def due(last_ago, now=NOW):
        # the idea in testing was told a moment ago: it is not what these calls are about
        _state(box.paths, "tester", last=now - last_ago, seen=R.seen("tester", box.store), stalled_told=now)
        os.utime(table, (now - 10, now - 10))
        return R.role_due("tester", box.paths, now, R.read_state(box.paths)["tester"], R.job_metas(box.paths, now),
                          store=box.store, **R.start_rules(box.cfg, box.paths))  # fmt: skip

    changed = (True, "the scout table changed (operator/scout-facts.md)")
    assert R.start_rules(box.cfg, box.paths) == {"wake_on_table": True, "every_h": None, "every_default": False}
    assert box.cfg.operator.analyst_every_h == 2.0 and due(60) == changed  # the config's value is nobody's choice
    set_budget(box.paths, box.cfg, {"analyst_every_h": 0.25}, "cli", NOW)  # the server's file today
    assert R.start_rules(box.cfg, box.paths) == {"wake_on_table": True, "every_h": 0.25, "every_default": False}
    for ago in (60, 600, 750, 884, 907):  # however close the rounds: as it was
        assert due(ago) == changed
    set_budget(box.paths, box.cfg, {"analyst_every_h": 1}, "telegram", NOW)
    assert due(907) == (False, "not yet: its last turn was 15 minutes ago (the owner's every 1 h)")
    assert due(2 * 900 + 7) == (False, "not yet: its last turn was 30 minutes ago (the owner's every 1 h)")
    assert due(H - 200) == changed  # a round starts seconds into its quarter: this close counts as on time
    due(907)
    not_yet = "not yet: its last turn was 15 minutes ago (the owner's every 1 h)"
    assert box.run("tester") == {"role": "tester", "ran": False, "why": not_yet}
    assert box.paths.log.read_text().strip().endswith("skip: not yet: its last turn was 15 minutes ago (the owner's "
                                                      "every 1 h)")  # fmt: skip
    # a job of its own ended: at the owner's own value it waits like everything else
    R.record_test(box.paths, NOW - 800, "tester", idea, "j1", ["lab", "--json"])
    _job(box.data, "j1", ["lab", "--json"], NOW - 800, ended=NOW - 300)
    assert due(907) == (False, "not yet: its last turn was 15 minutes ago (the owner's every 1 h)")
    assert due(H) == (True, "a job of yours ended: j1")
    # where the owner's value is the default 2 h the unread job calls at once; a table and a hand-on wait
    set_budget(box.paths, box.cfg, {"analyst_every_h": 2}, "telegram", NOW)
    assert R.start_rules(box.cfg, box.paths)["every_default"] is True
    assert due(907) == (True, "a job of yours ended: j1")
    R.note_job_read(box.paths, "tester", "j1", NOW - 100, "read")
    assert due(907) == (False, "not yet: its last turn was 15 minutes ago (the owner's every 2 h)")
    assert due(2 * H - 100) == changed
    # no first turn waits, and `every 0` means what it meant: the agents are off
    _state(box.paths, "tester", last=None)
    assert R.role_due("tester", box.paths, NOW, {}, [], **R.start_rules(box.cfg, box.paths)) == (True, "first turn")
    set_budget(box.paths, box.cfg, {"analyst_every_h": 0}, "telegram", NOW)
    assert box.run("tester")["why"] == "the research agents are off (the owner's /budget every 0)"


def test_an_idea_in_testing_with_no_job_running_calls_its_role_once(box):
    """The review of 14 Nov: a role starts a short job, reads it after it ended in the same turn and ends the turn with
    the idea still in testing; the next job of the chain came in the next turn. A job read after it ended calls nobody
    any more, an idea in testing is not "not tested yet", and a table that did not change wakes nobody: the role was
    not called for three hours. It is called once, at the next round; a turn that then does nothing about it is not
    called for it again."""
    idea = _idea(box.paths, "scout", "who leads this wallet", by="scout", now=NOW - 2 * H, status="testing")
    _state(box.paths, "scout", last=NOW - 900, idle=0, seen=R.seen("scout", box.store))
    R.record_test(box.paths, NOW - 850, "scout", idea, "j1", ["gmgn", "wallet", "W1", "--json"])
    _job(box.data, "j1", ["gmgn", "wallet", "W1", "--json"], NOW - 850, pid=os.getpid())
    assert _due(box, "scout") == (False, "nothing new")  # its job runs: it waits for the result
    _job(box.data, "j1", ["gmgn", "wallet", "W1", "--json"], NOW - 850, ended=NOW - 840)
    R.note_job_read(box.paths, "scout", "j1", NOW - 800, '{"ok": true}')  # read in the turn that started it
    stalled = f"your idea {idea} is in testing and no job of yours runs: start its next job or close it with its result"
    assert _due(box, "scout") == (True, stalled)
    off = R.role_due("scout", box.paths, NOW, R.read_state(box.paths)["scout"], R.job_metas(box.paths, NOW),
                     store=box.store, wake_on_table=False)  # fmt: skip
    assert off == (True, stalled)  # with the owner's table switch off as well
    # an idea that waits for the owner's yes is not the role's to move
    learning.upsert_idea(box.paths, {"id": idea, "owner_needed": True}, source="cloud", now=NOW - 700)
    assert _due(box, "scout") == (False, "nothing new")
    learning.upsert_idea(box.paths, {"id": idea, "owner_needed": False}, source="cloud", now=NOW - 700)
    # the turn it is called for does nothing about it: not called for it again
    out = box.run("scout")
    assert out["ran"] and out["why"] == stalled and R.read_state(box.paths)["scout"]["stalled_told"] == NOW
    for later in (900, 1800):
        assert _due(box, "scout", NOW + later) == (False, "nothing new")
    # ... until the idle wait has passed since it was told (the review of 14 Nov, second round: with the owner's
    # table switch off nothing else called the role for three hours while its idea stood still)
    assert _due(box, "scout", NOW + 2700) == (True, stalled)
    # a later turn starts its next job: when that one is read too and nothing runs, the role is called again, once
    t = NOW + 3 * H
    box.acts[:] = [lambda jobs: (R.record_test(box.paths, t + 10, "scout", idea, "j2", ["winners", "--json"]),
                                 _job(box.data, "j2", ["winners", "--json"], t + 10, ended=t + 40),
                                 R.note_job_read(box.paths, "scout", "j2", t + 60, "its result"))]  # fmt: skip
    assert box.run("scout", now=NOW + 3 * H)["ran"] and "stalled_told" not in R.read_state(box.paths)["scout"]
    assert _due(box, "scout", NOW + 3 * H + 900) == (True, stalled)
    # a turn that closes the idea leaves nothing to call for
    box.acts[:] = [lambda jobs: learning.upsert_idea(box.paths, {"id": idea, "status": "adopted"}, source="scout",
                                                    now=NOW + 3 * H + 960, role="scout")]  # fmt: skip
    assert box.run("scout", now=NOW + 3 * H + 900)["ran"]
    assert _due(box, "scout", NOW + 3 * H + 1800) == (False, "nothing new")


def test_a_stalled_idea_is_told_again_after_the_idle_wait_with_the_table_switch_off(box):
    """With `wake_on_table` off, a turn called for a stalled idea that only writes a lesson left the mark standing:
    the role was not called again for three hours with its idea still in testing and no job running. The mark holds
    for the idle wait (45 minutes less the round's slack), then the stall is told once more."""
    idea = _idea(box.paths, "scout", "who leads this wallet", by="scout", now=NOW - 2 * H, status="testing")
    _state(box.paths, "scout", last=NOW - 900, idle=0, seen=R.seen("scout", box.store))
    stalled = f"your idea {idea} is in testing and no job of yours runs: start its next job or close it with its result"
    box.acts[:] = [lambda jobs: learning.add_lesson(box.paths, "the wallet leads nobody", "n 12", 12, source="scout",
                                                    now=NOW + 60)]  # fmt: skip
    assert box.run("scout")["why"] == stalled
    state = R.read_state(box.paths)["scout"]
    assert state["stalled_told"] == NOW and state["idle"] == 0  # a lesson is work; the idea still stands

    def off(later):
        return R.role_due("scout", box.paths, NOW + later, R.read_state(box.paths)["scout"],
                          R.job_metas(box.paths, NOW + later), store=box.store, wake_on_table=False)  # fmt: skip

    assert off(900) == (False, "nothing new") and off(1800) == (False, "nothing new")
    assert off(2700 - R.SLACK_SECS) == (True, stalled) and off(2700) == (True, stalled)


def test_a_stalled_idea_is_told_twice_and_then_left_to_the_three_hour_look(box):
    """The final review of 14 Nov: told again every 45 minutes for as long as the idea stands, the scout, the tester
    and the fee analyst were each called 32 times on a quiet day instead of 8 (their ideas wait for forward trades and
    say so). The stall is told at the next round and once more after the idle wait; then only a turn that starts a
    job or closes an idea brings the call back. A mark that is no number (a hand edit) never raises."""
    idea = _idea(box.paths, "scout", "who leads this wallet", by="scout", now=NOW - 2 * H, status="testing")
    _state(box.paths, "scout", last=NOW - 900, idle=0, seen=R.seen("scout", box.store), stalled_told="yes")
    stalled = f"your idea {idea} is in testing and no job of yours runs: start its next job or close it with its result"
    assert _due(box, "scout") == (True, stalled)  # a mark nobody can read is no mark
    assert box.run("scout")["why"] == stalled and R.read_state(box.paths)["scout"]["stalled_n"] == 1
    assert _due(box, "scout", NOW + 1800) == (False, "nothing new")
    assert box.run("scout", now=NOW + 2700)["why"] == stalled
    state = R.read_state(box.paths)["scout"]
    assert state["stalled_n"] == 2 and state["stalled_told"] == NOW + 2700
    for later in range(900, 3 * H - 600, 900):  # every round until the three-hour look: not for the idea
        due, why = _due(box, "scout", NOW + 2700 + later)
        assert not why.startswith(R.STALLED) and (not due or later >= 3 * H - R.SLACK_SECS), (later, why)
    assert _due(box, "scout", NOW + 2700 + 3 * H)[1] == "three quiet hours since its last turn"
    # a turn that starts a job clears the count: the next stall is told again, twice
    t = NOW + 2700 + 3 * H
    box.acts[:] = [lambda jobs: (R.record_test(box.paths, t + 10, "scout", idea, "j2", ["winners", "--json"]),
                                 _job(box.data, "j2", ["winners", "--json"], t + 10, ended=t + 40),
                                 R.note_job_read(box.paths, "scout", "j2", t + 60, "its result"))]  # fmt: skip
    assert box.run("scout", now=t)["ran"]
    state = R.read_state(box.paths)["scout"]
    assert "stalled_n" not in state and "stalled_told" not in state
    assert _due(box, "scout", t + 900) == (True, stalled)


def test_a_job_a_cut_turn_was_called_for_is_told_again_once(box):
    """A turn whose last reply stopped at the output cap forgets what it read (it did not finish with it), but its
    start time is kept as the role's last turn: the job it was called for ended before that and was never told again.
    The next round tells it once more; a second cut turn does not tell it a third time, and a job that ended before
    the turn before is not brought back."""
    idea = _idea(box.paths, "scout", status="testing", by="scout", now=NOW - 5 * H)
    _state(box.paths, "scout", last=NOW - 1800, seen=R.seen("scout", box.store))
    for jid, ended in (("old", NOW - 3600), ("before", NOW - 60)):
        R.record_test(box.paths, NOW - 4000, "scout", idea, jid, ["winners", "--json", "--hours", jid])
        _job(box.data, jid, ["winners", "--json", "--hours", jid], NOW - 4000, ended=ended)

    def cut(cfg, svc, paths, snap, decision, ledger, notifier, notes, *, jobs, now, role, **kw):
        R.note_job_read(paths, role, "before", now(), "its result")
        return {"usd": 0.2, "tool_calls": 2, "stopped": "end_turn", "notes": 1, "cut": "max_tokens"}

    assert box.run("scout", turn=cut)["why"] == "a job of yours ended: before"
    state = R.read_state(box.paths)["scout"]
    assert state["last"] == NOW and state["retell_from"] == NOW - 1800 and not R.jobs_read(box.paths, "scout")
    assert _due(box, "scout", NOW + 900) == (True, "a job of yours ended: before")  # "old" ended before that turn
    # cut again: the job is not told a third time
    assert box.run("scout", now=NOW + 900, turn=cut)["why"] == "a job of yours ended: before"
    assert R.read_state(box.paths)["scout"]["retell_from"] == NOW
    assert not _due(box, "scout", NOW + 1800)[1].startswith("a job of yours ended")
    # a turn that ends well leaves no mark
    _state(box.paths, "scout", last=NOW - 1800, retell_from=NOW - 3000)
    assert box.run("scout", now=NOW + 1800)["ran"] and "retell_from" not in R.read_state(box.paths)["scout"]


def test_an_idea_for_the_cloud_session_or_the_owner_opened_in_the_turn_is_work(box):
    """A turn that finds a tool missing and writes the idea of kind code for it (or a live test for the owner) handed
    its result on, as one that hands an idea to another role: it is not a turn without work."""
    started = NOW
    for kind in ("code", "live-test", "config"):
        learning.upsert_idea(box.paths, {"title": f"a {kind} idea", "why": "w", "test": "t", "expected": "e",
                                         "kind": kind}, source="oktech", now=started + 30)  # fmt: skip
        assert R._worked(box.paths, "oktech", started)["handed"], kind
        assert not R._worked(box.paths, "oktech", started + 60)["handed"]  # opened before that turn began
        assert not R._worked(box.paths, "scout", started)["handed"]  # somebody else's
        started += 100
    # its own research idea, opened and left in its own queue, is still not work
    own = _idea(box.paths, "oktech", "one for itself", by="oktech", now=started + 30)
    assert not R._worked(box.paths, "oktech", started)["handed"]
    # ... until a later turn does what its rules say for a test its commands cannot run: writes what is missing into
    # the idea and sets its kind to code, which hands it to the cloud session
    started += 100
    learning.upsert_idea(box.paths, {"id": own, "test": "needs a command that lists a wallet's tips", "kind": "code"},
                         source="oktech", now=started + 30, role="oktech")  # fmt: skip
    assert R._worked(box.paths, "oktech", started)["handed"]
    started += 100
    learning.upsert_idea(box.paths, {"id": own, "why": "said again"}, source="oktech", now=started + 30, role="oktech")
    assert not R._worked(box.paths, "oktech", started)["handed"]  # an update of an idea already handed on is none


def test_ideas_for_nobodys_queue_do_not_keep_a_role_busy_for_ever(box):
    """Nobody's queue (kind code, config, live-test) has no limit, so one such idea a turn kept a role "not idle" for
    good: 96 turns and 96 open ideas in a day. An idea opened already closed is no hand-on, and while more than
    NOBODY_OPEN of the role's ideas wait there, another one is no work."""
    started = NOW

    def add(n, **over):
        return learning.upsert_idea(box.paths, {"title": f"missing tool {n}", "why": "w", "test": "t", "expected": "e",
                                                "kind": "code", **over}, source="oktech", now=started + 30)  # fmt: skip

    add(0, status="rejected")
    assert not R._worked(box.paths, "oktech", started)["handed"]
    for n in range(1, R.NOBODY_OPEN + 1):
        started += 100
        add(n)
        assert R._worked(box.paths, "oktech", started)["handed"], n
    started += 100
    last = add(R.NOBODY_OPEN + 1)
    assert not R._worked(box.paths, "oktech", started)["handed"]
    # the cloud session closes two: the next one counts again
    for iid in ("I2", "I3"):
        learning.upsert_idea(box.paths, {"id": iid, "status": "adopted"}, source="cloud", now=started + 40)
    started += 100
    add(R.NOBODY_OPEN + 2)
    assert last and R._worked(box.paths, "oktech", started)["handed"]


def test_a_job_that_was_killed_or_lost_in_a_restart_is_told(box, tmp_path):
    """`remote kill` signals the job's whole group: the wrapper that writes the exit file dies with it, and the
    process that started the job is long gone. The record then never got an end time and nobody was called: not the
    role that started it, not one that waits for it. Its end is when its log was last written."""
    import subprocess
    import sys
    import time

    from snipetech import ops as O

    data = box.data
    code = ("import sys;from pathlib import Path;from snipetech import ops as O;"
            f"o=O.Ops(repo=Path({str(tmp_path)!r}),data=Path({str(data)!r}),db=Path({str(data / 'snipetech.db')!r}),"
            f"keys_file=Path({str(tmp_path / 'k')!r}),branch='main',"
            "command=[sys.executable,'-c','import time;time.sleep(120)']);"
            "print(o.op_run(['winners','--json'])['id']);import os;sys.stdout.flush();os._exit(0)")  # fmt: skip
    ran = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True,
                         env=os.environ | {"PYTHONDONTWRITEBYTECODE": "1"})  # fmt: skip
    jid = ran.stdout.strip().splitlines()[-1]
    t0 = time.time()
    idea = _idea(box.paths, "scout", "a wallet study", by="scout", now=t0 - H, status="testing")
    waits = _idea(box.paths, "fees", "the same study, for its bid", by="fees", now=t0 - H, status="testing")
    R.record_test(box.paths, t0, "scout", idea, jid, ["winners", "--json"])
    R.join_test(box.paths, t0, "fees", waits, jid, ["winners", "--json"])
    for r in ("scout", "fees"):
        _state(box.paths, r, last=int(t0) - 5, seen=R.seen(r, box.store))
    ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main")
    assert not R.job_over(box.paths, jid)
    ops.op_kill(jid)
    for _ in range(100):
        if R.job_over(box.paths, jid):
            break
        time.sleep(0.1)
    assert R.job_over(box.paths, jid) and not (data / "jobs" / f"{jid}.exit").exists()
    (meta,) = [m for m in R.job_metas(box.paths, time.time()) if m["id"] == jid]
    log_written = (data / "jobs" / f"{jid}.log").stat().st_mtime
    assert meta["running"] is False and meta["ended"] == log_written and meta["exit_code"] is None
    for r in ("scout", "fees"):
        later = time.time() + 900
        state = R.read_state(box.paths)[r]
        due = R.role_due(r, box.paths, later, state, R.job_metas(box.paths, later), store=box.store)
        assert due == (True, f"a job of yours ended: {jid}"), (r, due)


def test_a_good_read_stays_when_a_later_turn_that_read_the_job_again_is_cut(box):
    """The first read of a finished job is the one that counts: a later turn that looks at the same job again and is
    then refused or cut forgets its own reads, not the good one of the turn before."""
    R.note_job_read(box.paths, "scout", "j1", NOW - 900, "its result")
    R.note_job_read(box.paths, "scout", "j1", NOW + 60, "the same result, looked at again")
    R.note_job_read(box.paths, "scout", "j2", NOW + 60, "read for the first time in the turn that is cut")
    assert json.loads((box.paths.root / R.READ_FILE).read_text())["scout"] == {"j1": NOW - 900, "j2": NOW + 60}
    R.forget_reads(box.paths, "scout", NOW)
    assert R.jobs_read(box.paths, "scout") == {"j1"}


def test_a_turn_cut_at_the_output_cap_did_not_finish_with_what_it_read(box):
    """A reply cut at max_tokens ends the turn with `stopped` "end_turn" (what it is to every other reader); run_turn
    says so with "cut", and the turn's reads are forgotten as after any turn that did not end by itself."""
    idea = _idea(box.paths, "scout", status="testing", now=NOW - 2 * H)
    R.record_test(box.paths, NOW - 5, "scout", idea, "j1", ["winners", "--json"])
    _job(box.data, "j1", ["winners", "--json"], NOW - 5, ended=NOW + 50)

    def turn(cut):
        def t(cfg, svc, paths, snap, decision, ledger, notifier, notes, *, jobs, now, role, **kw):
            R.note_job_read(paths, role, "j1", now() + 60, "its result")
            return {"usd": 0.3, "tool_calls": 2, "stopped": "end_turn", "notes": 1} | ({"cut": cut} if cut else {})

        return t

    box.run("scout", now=NOW, turn=turn("max_tokens"))
    assert R.jobs_read(box.paths, "scout") == set()
    assert _due(box, "scout", NOW + 900) == (True, "a job of yours ended: j1")
    box.run("scout", now=NOW + 900, turn=turn(None))
    assert R.jobs_read(box.paths, "scout") == {"j1"}


def test_one_role_never_runs_twice_at_once(box):
    with R.role_lock(box.paths, "scout") as held:
        assert held
        out = box.run("scout")
        assert out == {"role": "scout", "ran": False, "why": "its last turn is still running"} and box.calls == []
        assert box.run("tester")["ran"]  # another role's turn runs beside it
    assert box.run("scout")["ran"]


def test_role_turn_never_takes_the_rounds_lock(box):
    """The round's lock is the round's: a role's turn runs while a round does (alerts, the book and the kill switch
    keep their minutes), and leaves that lock as it found it."""
    box.paths.lock.write_text(str(os.getpid()))
    assert box.run("fees")["ran"]
    assert box.paths.lock.read_text() == str(os.getpid())
    box.paths.lock.unlink()
    assert box.run("oktech")["ran"] and not box.paths.lock.exists()


def test_role_over_its_share_skips_and_says_so_once_a_day(box):
    led = Ledger(box.paths.spend, box.cfg.operator.prices)
    led.record("oktech", {"output_tokens": 40_000}, NOW - H)  # 1.6 $ of the 1.5 $ that is 10 % of the config's 15 $
    out = box.run("oktech")
    assert out["ran"] is False and out["why"] == "its share of the day is spent (1.60 $ of 1.50 $)" and box.calls == []
    assert box.run("oktech", now=NOW + 900)["ran"] is False
    notes = box.paths.notes.read_text()
    assert notes.count("oktech: its share of the day is spent") == 1 and "[info]" in notes  # once a day, no page
    assert box.paths.log.read_text().count("agent oktech: skip: its share of the day is spent") == 2
    assert box.run("scout")["ran"]  # another role's share is its own
    led.record("analyst", {"output_tokens": 300_000}, NOW)  # 12 $ more: the day is inside the last 15 %
    out = box.run("tester")
    assert out["ran"] is False and out["why"].startswith("the day is inside its reserve")
    assert box.run("oktech", now=NOW + 86400)["ran"]  # a new day, a new share


def test_run_role_runs_one_turn_and_writes_it_down(box):
    idea = _idea(box.paths, "tester", now=NOW - H)
    _job(box.data, "r1", ["lab", "--json"], NOW - 60, pid=os.getpid())
    _job(box.data, "e1", ["winners", "--json"], NOW - 2 * H, ended=NOW - H)
    _job(box.data, "m1", ["measure", "run"], NOW - 60, pid=os.getpid())
    out = box.run("tester")
    assert out == {"role": "tester", "ran": True, "why": "first turn", "usd": 0.5, "tool_calls": 3,
                   "stopped": "end_turn", "notes": 1}  # fmt: skip
    (call,) = box.calls
    assert call["role"] == call["mode"] == "tester" and call["reasons"] == ["first turn"]
    snap = call["snap"]  # what a role's turn is shown: the jobs, and nothing of the wallet or OKTech
    assert sorted(j["id"] for j in snap.jobs_running) == ["m1", "r1"] and [j["id"] for j in snap.jobs_recent] == ["e1"]
    assert (
        snap.native_sol is None and snap.wsol is None and snap.snipers is None and snap.held == [] and snap.wallet == ""
    )
    state = R.read_state(box.paths)["tester"]
    assert state["last"] == NOW and state["reason"] == "first turn" and state["usd"] == 0.5 and state["idle"] == 1
    assert state["stopped"] == "end_turn" and state["turns"] == 1 and state["day"] == "2026-11-06"
    assert state["spent_usd"] == 0.5 and state["share_usd"] == 3.75  # today's dollars of its 25 % of 15 $
    log = box.paths.log.read_text().splitlines()
    assert log[0] == "2026-11-06T12:00:00Z agent tester: turn: first turn"
    assert log[1].startswith('2026-11-06T12:00:00Z agent tester: done: {"usd": 0.5, "tool_calls": 3')
    assert Ledger(box.paths.spend, {}).spent_today_by(NOW) == pytest.approx({"tester": 0.5})
    assert idea in [i["id"] for i in learning.untested(box.paths, role="tester")]
    assert box.run("tester", now=NOW + 900)["why"] == f"not tested yet: {idea}"  # the untested idea calls it again


def test_run_role_does_nothing_while_the_agents_are_off(box):
    """Off by default, and off again with the owner's `/budget every 0`: no turn, no state, no log line, no spend."""
    from snipetech.operator.budget_switch import set_budget

    box.cfg.operator.role_agents = False
    assert box.run("scout") == {
        "role": "scout",
        "ran": False,
        "why": "the research agents are off ([operator] role_agents)",
    }
    box.cfg.operator.role_agents = True
    set_budget(box.paths, box.cfg, {"analyst_every_h": 0}, "telegram", NOW)
    assert box.run("scout")["why"] == "the research agents are off (the owner's /budget every 0)"
    assert box.calls == [] and not (box.paths.root / "agents.json").exists() and not box.paths.log.exists()
    with pytest.raises(ValueError, match="no such role"):
        box.run("analyst")


def test_a_dry_run_says_whether_a_turn_is_due_and_keeps_nothing(box):
    _idea(box.paths, "scout", now=NOW - H)
    out = box.run("scout", dry_run=True)
    assert out == {"role": "scout", "ran": False, "why": "dry run", "on": True, "due": True, "reason": "first turn"}
    box.cfg.operator.role_agents = False
    assert box.run("scout", dry_run=True)["on"] is False
    assert box.calls == [] and not (box.paths.root / "agents.json").exists() and not box.paths.log.exists()


def test_a_role_turn_that_fails_is_a_log_line_and_the_next_round_tries_again(box):
    def broken(*a, **kw):
        raise RuntimeError("boom")

    out = box.run("scout", turn=broken)
    assert out["ran"] is False and out["failed"] is True and "RuntimeError: boom" in out["why"]
    assert "agent scout: turn failed: RuntimeError: boom" in box.paths.log.read_text()
    assert box.run("scout", now=NOW + 900)["ran"]  # its lock is free again


def test_without_an_api_key_no_turn_starts(box, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    out = box.run("scout", client=None)
    assert out["ran"] is False and out["why"] == "no ANTHROPIC_API_KEY in the environment" and box.calls == []


def test_week_counts_tests_freezes_and_forward_passes(box):
    """More tests are not more winners: the digest says how many tests the agents started in the last seven days, how
    many rules were frozen for the forward paper test, and how many rules pass it now (30 or more forward trades, total
    and lower bound above zero), read from what the paper report last worked out."""
    week = 7 * 86400
    assert R.week_counts(box.paths, NOW, box.store) == {"tests": 0, "frozen": 0, "passed": 0}
    R.record_test(box.paths, NOW - week - 60, "tester", "I1", "old", ["lab"])
    for i in range(3):
        R.record_test(box.paths, NOW - i * 3600, "tester" if i else "scout", "I1", f"j{i}", ["lab"])
    kol, dev = {"kind": "kol"}, {"kind": "dev"}
    ex = {"watch_secs": 1.3, "copy_exit_pct": 0.5}  # an exit that sells as a copy sniper's does
    old = {"watch_secs": 1.3}  # frozen before 13 Nov: it held on when the followed wallet sold
    box.store.set_coverage("paper_strategies", {"strategies": [
        {"id": "A", "from_ts": NOW - 86400, "trigger": kol, "exit": ex},
        {"id": "B", "from_ts": NOW - 2 * 86400, "trigger": kol, "exit": ex},
        {"id": "C", "from_ts": NOW - 30 * 86400, "trigger": kol, "exit": ex},
        {"id": "D", "from_ts": NOW - 86400, "trigger": dev},
        {"id": "F", "from_ts": NOW - 30 * 86400, "trigger": kol, "exit": old},
    ], "superseded": [{"id": "E", "from_ts": NOW - 3 * 86400, "trigger": kol, "exit": ex}]})  # fmt: skip
    box.store.set_coverage("paper:report", {"rows": {
        "A": {"numbers": {"n": 41, "total": 1.2, "ev_lower": 0.004}},  # passes
        "B": {"numbers": {"n": 29, "total": 2.0, "ev_lower": 0.01}},  # too few trades yet
        "C": {"numbers": {"n": 300, "total": 0.9, "ev_lower": -0.001}},  # the lower bound is not above zero
        "D": {"numbers": {"n": 400, "total": 5.0, "ev_lower": 0.01}},  # a deployer-verdict rule: no lab froze it
        "F": {"numbers": {"n": 500, "total": 9.0, "ev_lower": 0.02}},  # not what a sniper would trade: never "passed"
    }})  # fmt: skip
    assert R.week_counts(box.paths, NOW, box.store) == {"tests": 3, "frozen": 3, "passed": 1}
    assert R.week_counts(box.paths, NOW) == {"tests": 3, "frozen": None, "passed": None}  # no store: only the ledger


def test_the_research_digest_names_the_agents_only_while_they_are_on(box):
    from snipetech.operator import research

    _idea(box.paths, "scout", "who copies Cupsey")
    on = research.digest_text(box.store, box.cfg, box.paths, NOW, NOW - 6 * H, [])
    assert "Testing next, one for each research agent:\n- scout: who copies Cupsey" in on
    assert "This week: 0 tests started, 0 rules frozen" in on
    box.cfg.operator.role_agents = False
    off = research.digest_text(box.store, box.cfg, box.paths, NOW, NOW - 6 * H, [])
    assert "Testing next:\n- who copies Cupsey" in off and "research agent" not in off and "This week" not in off


def test_switched_off_the_server_runs_as_it_did(box):
    """The switch in one place. With the default config (what the server runs after a deploy of this code: its file
    holds no value for `role_agents`) the round starts the stages it started before, the gate gives the analyst its
    turns between the daily ones, a job that ended calls the operator whoever is on the ledger, the budget line and
    the digest name no agent, a role's command does nothing, and the analyst's tools are the sixteen it had. The
    deploy guard is in tests/test_ops.py: no `operate agent` job exists while they are off."""
    from snipetech.operator.budget_switch import budget_line
    from snipetech.operator.gate import analyst_reason, decide, mark_analyst_ran
    from snipetech.operator.snapshot import Snapshot
    from snipetech.operator.standin import STANDIN, due_standins
    from snipetech.operator.tools import NoteSink, make_operator_tools

    cfg = Config()
    cfg.storage.db_path = box.cfg.storage.db_path
    assert cfg.operator.role_agents is False and R.on(cfg, box.paths) is False
    assert R.off_reason(cfg, box.paths) == "the research agents are off ([operator] role_agents)"
    assert [tuple(a) for a in due_standins({}, [], NOW)] == [row[0] for row in STANDIN]
    box.paths.report("analyst-2026-11-06").write_text("x")
    mark_analyst_ran(box.paths, NOW - 2 * H)
    assert analyst_reason(box.paths, NOW, cfg.operator) == "analysis due: every 2 h, the last one 2.0 h ago"
    R.record_test(box.paths, NOW - 600, "tester", "I1", "t1", ["lab"])  # a ledger left from a day they were on
    snap = Snapshot(ts=NOW, services={}, tape_last_ts=NOW - 5, snipers=[], native_sol=1.0, wsol=0.0,
                    jobs_ended=[{"id": "t1", "argv": ["lab"], "exit_code": 0}])  # fmt: skip
    assert "job ended: t1 (lab, exit 0)" in decide(snap, snap, NOW, cfg.operator, box.paths).reasons
    assert "research agents" not in budget_line(cfg, box.paths, 0.0)
    assert not any("research agent" in x for x in learning.digest_lines(box.paths, NOW - 6 * H, NOW))
    box.cfg.operator.role_agents = False
    assert box.run("scout")["ran"] is False and box.calls == [] and not (box.paths.root / "agents.json").exists()
    jobs = OperatorJobs(repo=box.data.parent, data=box.data, db=box.data / "snipetech.db", branch="main")
    tools = make_operator_tools(box.svc, box.paths, jobs, cfg.operator, NoteSink(box.paths), source="analyst")
    start = next(t for t in tools if t.name == "start_job")
    assert len(tools) == 16 and "idea" not in start.to_dict()["input_schema"]["properties"]


def test_the_agent_command_runs_one_role_and_asks_for_one(box, capsys):
    from snipetech import cli

    db = ["--db", str(box.data / "snipetech.db")]
    with pytest.raises(SystemExit, match="--role"):
        cli.main([*db, "operate", "agent"])
    with pytest.raises(SystemExit, match="--role"):
        cli.main([*db, "operate", "agent", "--role", "analyst"])
    # the config the command loads has the agents off: it says so and starts nothing
    assert cli.main([*db, "operate", "agent", "--role", "scout"]) == 0
    assert json.loads(capsys.readouterr().out) == {"role": "scout", "ran": False,
                                                   "why": "the research agents are off ([operator] role_agents)"}  # fmt: skip


def test_the_agents_are_switched_on_and_off_by_a_file_without_a_deploy(box, capsys):
    """12 Nov: the switch in the settings is off by default, and a default cannot be flipped without a deploy (and
    half the round's tests rest on it). As the book and the automatic test have, the agents have a file:
    `operate agent --switch on` writes operator/ROLES_ON, `--switch off` removes it. The file or the setting switches
    them on; the owner's `/budget every 0` still switches them off either way. With them on, four roles share a
    limit of five research jobs."""
    from snipetech import cli

    db = ["--db", str(box.data / "snipetech.db")]
    assert R.on(box.cfg, box.paths) is True  # this box has the setting on
    box.cfg.operator.role_agents = False
    assert R.on(box.cfg, box.paths) is False
    assert cli.main([*db, "operate", "agent", "--switch", "on"]) == 0
    assert json.loads(capsys.readouterr().out) == {"agents": "on", "by": "the file operator/ROLES_ON"}
    assert (box.paths.root / R.SWITCH).exists() and R.on(box.cfg, box.paths) is True
    assert cli.main([*db, "operate", "agent", "--role", "scout", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["on"] is True
    assert cli.main([*db, "operate", "agent", "--switch", "off"]) == 0
    assert json.loads(capsys.readouterr().out) == {"agents": "off", "by": "the file operator/ROLES_ON is gone"}
    assert not (box.paths.root / R.SWITCH).exists() and R.on(box.cfg, box.paths) is False
    with pytest.raises(SystemExit):
        cli.main([*db, "operate", "agent", "--switch", "maybe"])
    assert R.ROLE_MAX_JOBS == 5
