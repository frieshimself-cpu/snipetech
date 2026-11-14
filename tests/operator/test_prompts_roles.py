"""What each research agent is told (operator/prompts.py, ROLE_SYSTEM): the soft layer. The hard layer is its tools
(tests/operator/test_jobs_tools.py): no role has a tool or a command that trades, whatever its instructions say."""

import re

import pytest

from snipetech.operator import prompts as P
from snipetech.operator.gate import Decision
from snipetech.operator.roles import NAMES, ROLE_COMMANDS, ROLES, TABLES
from snipetech.operator.snapshot import Snapshot

NOW = 1_793_923_200 + 3600


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_every_role_has_its_own_instructions():
    assert set(P.ROLE_SYSTEM) == set(ROLES)
    assert P.ROLE_SYSTEM == {"scout": P.SCOUT_SYSTEM, "tester": P.TESTER_SYSTEM, "fees": P.FEES_SYSTEM,
                             "oktech": P.OKTECH_SYSTEM}  # fmt: skip
    for role in ROLES:
        text = _flat(P.ROLE_SYSTEM[role])
        assert text.startswith(f"You are snipetech's {NAMES[role]}"), role
        assert _flat(P.ROLE_RULES) in text  # the shared core, whole
        assert f"operator/{TABLES[role]}.md" in text and f"{role}-journal-" in text
        assert len({P.ROLE_SYSTEM[r] for r in ROLES}) == len(ROLES)


def test_every_role_is_told_it_never_trades():
    for role in ROLES:
        text = _flat(P.ROLE_SYSTEM[role])
        assert "You never trade." in text
        assert "You have no tool and no command that touches OKTech, a sniper, a wallet, a key or money" in text
        assert "never create, start, stop or change a sniper" in text and "never move SOL" in text
        assert "Any live step is the owner's" in text
        assert "the book, the guard, the budget" in text


def test_every_role_names_the_landing_rule():
    for role in ROLES:
        text = _flat(P.ROLE_SYSTEM[role])
        assert "A paper result names its landing" in text and "the bid that buys" in text
        assert "at a better place than its bid buys is not evidence" in text
        assert "companions" in text  # no copy lands between one operator's own wallets
        assert "check that a few trades do not carry it" in text
        assert '"Not measured" is never "refuted"' in text


def test_tester_keeps_the_freeze_rules():
    """The analyst's hypothesis rules, word for word, are the tester's: the same text in both instructions."""
    hyp = P._HYPOTHESES
    assert hyp.startswith("Hypotheses: the system learns by testing its own strategy ideas")
    assert hyp in P.ANALYST_SYSTEM and hyp in P.TESTER_SYSTEM
    for rule in ("A hypothesis whose test lower bound (ev_lower) is above zero at its live landing",
                 "is refuted for now", "means not measured, not refuted", "One forward record a rule",
                 "The forward paper test, never the lab, is the evidence", "ev_lower_capped", "top5_share"):  # fmt: skip
        assert rule in _flat(hyp), rule
    for role in ("scout", "fees", "oktech"):
        assert hyp not in P.ROLE_SYSTEM[role]
    tester = _flat(P.TESTER_SYSTEM)
    # stricter than the lab: two forward readings that do not overlap before a rule is written up for the owner
    assert "two forward readings that do not overlap" in tester and "30 trades, then 30 more" in tester
    assert "fees-bids" in tester and "scout-facts" in tester  # the place and the bid, and the groups, from the others
    assert "size.triggers" in tester and "crowd_free_mean" in tester and '"--same-sol"' in tester  # the size check
    assert '"--copy-position", "bid-"' in tester and '"--recipe"' in tester
    assert "Has to be profitable paper trade first" in tester


def test_what_each_of_the_other_roles_is_told():
    scout, fees = _flat(P.SCOUT_SYSTEM), _flat(P.FEES_SYSTEM)
    assert "never start a lab or a freeze" in scout and "never call a wallet copyable" in scout
    assert "at most 40 a day" in scout and "winners" in scout and "gmgn" in scout and "amm-backfill" in scout
    assert "never follow a copy bot's buys" in scout  # the owner: we are filled above; follow the wallets it follows
    assert "place-bids" in fees and "tips" in fees and "landing" in fees and "in_bid_order" in fees
    assert "A bid belongs in the tip" in fees and "At most one open proposal" in fees
    assert "never start a ladder" in fees and "never change the fee policy" in fees
    assert "outbidding is not landing first" in fees


def test_the_oktech_role_is_told_what_it_reads_writes_and_never_does():
    """The owner's "an agent that masters oktech's features an how to apply them"."""
    text = _flat(P.OKTECH_SYSTEM)
    assert "which of OKTech's features would make a paper-proven rule land better or sell better" in text
    assert "the cheapest test of each" in text
    for doc in ("docs/oktech-playbook.md", "docs/oktech-features.md", "docs/oktech-settings.md"):
        assert doc in text
    assert "operator/oktech-facts.md" in text and "one row a feature" in text
    assert "verified or not, and by which fill" in text
    assert "kind live-test" in text
    for word in ("the wallet", "the size", "the cap", "the duration", "the stop rule"):
        assert word in text, word
    assert "You never trade, and you never create, start, stop or arm a sniper" in text
    assert "you have no tool that could" in text
    assert "never start a lab or a freeze" in text
    for command in ROLE_COMMANDS["oktech"]:
        assert command in text, command
    assert "OKTech playbook (measured on our own live trades" in text  # the measured rules, as the analyst has them


def _snap(**over):
    base = {
        "ts": NOW,
        "wallet": "SniperWalletAddr",
        "native_sol": 1.25,
        "wsol": 0.5,
        "held": [{"mint": "HELDMINT", "amount": 5}],
        "snipers": [{"id": "a", "name": "snipetech copy book X", "status": "running"}],
        "jobs_running": [{"id": "r1", "argv": ["lab", "--trigger", "{}"], "started": NOW - 60}],
        "jobs_recent": [
            {"id": "j1", "argv": ["winners", "--hours", "72"], "exit_code": 0, "ended": NOW - 600},
            {"id": "j2", "argv": ["lab", "--latency", "0"], "exit_code": 0, "ended": NOW - 900},
        ],
    }
    return Snapshot(**(base | over))


def test_role_message_leaves_out_wallet_and_snipers():
    """A role's message carries why it was called, the jobs with who started them, its memory, its own table and where
    the turn stops. What the wallet holds and what a sniper does is not a role's business: neither is in it."""
    decision = Decision(True, "scout", ["a job of yours ended: j1"])
    blocks = P.role_message("scout", _snap(), decision, learned="Your queue, best first:\n- I5 hunt wallets",
                            table="| wallet | who |\n| W1 | Cupsey |", started_by={"j1": "scout", "r1": "tester"},
                            ceiling={"tool_calls": 40, "turn_usd": 5.0, "spent_usd": 12.5,
                                     "share_usd": 100.0})  # fmt: skip
    assert all(b["type"] == "text" and b["text"].strip() for b in blocks)  # the API refuses an empty text block
    text = P.message_text(blocks)
    assert "Turn of the wallet scout. Called because: a job of yours ended: j1." in text
    assert "j1 exit 0 at 6 Nov 00:50, by the scout: winners --hours 72" in text
    assert "j2 exit 0 at 6 Nov 00:45: lab --latency 0" in text  # nobody of the roles started it
    assert text.index("j2 exit 0") < text.index("j1 exit 0")  # the oldest first: the list only grows at its end
    assert "r1, by the tester" in text and "Your queue, best first" in text and "| W1 | Cupsey |" in text
    assert "operator/scout-facts.md" in text
    assert "40 tool calls or 5 $" in text and "12.50 $ of your share of 100 $" in text
    for word in ("SniperWalletAddr", "native_sol", "wsol", "equity", "HELDMINT", "snipers", "1.25", "buys_payable",
                 "book_live", "inbox"):  # fmt: skip
        assert word not in text, word
    empty = P.message_text(P.role_message("fees", _snap(jobs_running=[], jobs_recent=[]),
                                          Decision(True, "fees", ["first turn"])))  # fmt: skip
    assert "not written yet" in empty and "none" in empty


def test_job_line_names_the_role_that_started_it():
    jobs = [{"id": "j1", "argv": ["winners"], "exit_code": 0, "ended": NOW - 600},
            {"id": "j2", "argv": ["lab", "--latency", "0"], "exit_code": 1, "ended": NOW - 900}]  # fmt: skip
    assert P._job_lines(jobs) == ["j1 exit 0 at 00:50: winners", "j2 exit 1 at 00:45: lab --latency 0"]  # as before
    assert P._job_lines(jobs, {"j2": "fees"}) == ["j1 exit 0 at 00:50: winners",
                                                  "j2 exit 1 at 00:45, by the fees: lab --latency 0"]  # fmt: skip


def test_no_model_name_in_a_prompt():
    """No instruction names the model it runs on or its maker: the words of the three configured model names, and the
    SDK's own name, are in none of them."""
    import anthropic

    from snipetech.config import OperatorConfig

    op = OperatorConfig()
    words = {w for model in (op.model, op.analyst_model, op.strategist_model) for w in model.split("-") if w.isalpha()}
    assert len(words) >= 3
    names = re.compile("|".join(sorted(words | {anthropic.__name__})), re.IGNORECASE)
    for role in ROLES:
        assert not names.search(P.ROLE_SYSTEM[role]), role
    assert not names.search(P.ROLE_RULES)
    assert not names.search(P.message_text(P.role_message("tester", _snap(), Decision(True, "tester", ["first turn"]))))


def test_a_role_that_does_not_exist_has_no_message():
    with pytest.raises(KeyError):
        P.role_message("analyst", _snap(), Decision(True, "analyst", ["x"]))


# -- 14 Nov: the opening message, the same bytes from turn to turn ---------------------------------------------------
# Every turn of a role wrote its whole opening message (20-26 thousand tokens, 0.22-0.25 $ of a 0.97 $ turn) to the
# five-minute cache, and a role's turns are 15 minutes or more apart: nothing of it was ever read back. Its first two
# lines held the reason and the time, so two messages in a row shared 42 to 124 characters. Two thirds of it is the list
# of the research jobs that ended in the last day, and 97 % of that list's lines are those of the round before.

H = 3600
DAY0 = 1_793_923_200  # 6 Nov 2026 00:00 UTC, a six-hour boundary


def _ended(n: int, first: float, every: float = 420.0) -> list[dict]:
    """`n` ended research jobs, one every `every` seconds from `first`, as snapshot.recent_jobs gives them: newest
    first."""
    jobs = [{"id": f"20261105-{k:06d}-aaaaaa", "exit_code": 0, "ended": first + k * every,
             "argv": ["lab", "--trigger", f'{{"kind": "hunt", "sol": {k}}}', "--json"]} for k in range(n)]  # fmt: skip
    return jobs[::-1]


def _message(now: float, jobs: list[dict], reason: str = "a job of yours ended: 20261105-000003-aaaaaa", **over):
    kw = {"learned": "What the agents learned so far:\n- lesson one", "table": "| rule | n |\n| hunt 4 | 31 |",
          "started_by": {"20261105-000003-aaaaaa": "tester"}, "record": "Research record (the cloud session's research "
          "rounds):\n- round 3: creator-0 hunts lose on unseen tape",
          "ceiling": {"tool_calls": 80, "turn_usd": 5.0, "spent_usd": 12.34, "share_usd": 175.0}}  # fmt: skip
    snap = _snap(ts=now, jobs_recent=jobs, jobs_running=over.pop("running", []))
    return P.role_message("tester", snap, Decision(True, "tester", [reason]), **(kw | over))


def _marked(blocks: list[dict]) -> list[int]:
    return [i for i, b in enumerate(blocks) if "cache_control" in b]


def test_two_messages_a_round_apart_share_every_leading_block_and_one_block_carries_the_hour_marker():
    """A round later three more jobs have ended, the turn has another reason, time and spend, a lesson is new and the
    table has changed: the blocks in front are the same bytes, so the hour's cache serves them, and everything that
    moved is in the last block. The hour's marker is on the last whole block of the list and never on the last block:
    a marker there with another duration than the request's own five-minute field is refused by the API."""
    now = DAY0 + 30 * H
    first = _message(now, _ended(47, now - 20 * H))
    later = _message(now + 900, _ended(50, now - 20 * H), reason="the fees table changed (operator/fees-bids.md)",
                     learned="What the agents learned so far:\n- lesson one\n- lesson two",
                     table="| rule | n |\n| hunt 4 | 44 |", running=[{"id": "r9", "argv": ["lab", "--json"]}],
                     ceiling={"tool_calls": 80, "turn_usd": 5.0, "spent_usd": 13.31, "share_usd": 175.0})  # fmt: skip
    assert len(first) == 6 and len(later) == 7  # the record and the heading, four and five blocks of ten, the rest
    assert later[: len(first) - 1] == [{k: v for k, v in b.items() if k != "cache_control"} for b in first[:-1]]
    assert first[-1] != later[-1]
    # the API looks back at most 20 blocks from a marker for what an earlier request wrote: the marker moved one block
    assert _marked(later)[0] - _marked(first)[0] == 1
    for blocks in (first, later):
        (at,) = _marked(blocks)
        assert at == len(blocks) - 2 and blocks[at]["cache_control"] == P.HOUR_CACHE
        for block in blocks[1:-1]:
            assert block["text"].count("\n") == P.JOB_BLOCK_LINES and block["text"].endswith("\n")  # whole lines
    text = P.message_text(later)
    lines = [ln for ln in text.splitlines() if ln.startswith("20261105-")]
    assert len(lines) == 50 and lines == sorted(lines)  # every job once, the oldest first
    for moved in ("lesson two", "| hunt 4 | 44 |", "r9: lab"):
        assert moved in later[-1]["text"], moved


def test_the_list_starts_on_a_six_hour_boundary_and_a_job_stays_at_least_a_day():
    """A list of "the last 24 hours" loses its oldest line every few minutes, and a line that leaves the front changes
    every byte behind it. The list starts at a time that moves in six-hour steps: between two steps no line leaves, so
    it only grows at its end, and it covers 24 to 30 hours. A job that ended just before a step is still listed after
    it, for a day at least."""
    from snipetech.operator.snapshot import ROLE_JOBS_HOURS, role_jobs_since

    assert ROLE_JOBS_HOURS == 30
    assert role_jobs_since(DAY0 + 24 * H) == DAY0 and role_jobs_since(DAY0 + 30 * H - 1) == DAY0
    assert role_jobs_since(DAY0 + 30 * H) == DAY0 + 6 * H
    for now in range(DAY0, DAY0 + 48 * H, 977):
        since = role_jobs_since(now)
        assert since % (6 * H) == 0 and 24 * H <= now - since < 30 * H
    edge = DAY0 + 6 * H - 60  # ended a minute before a boundary
    jobs = [{"id": "old", "argv": ["winners"], "exit_code": 0, "ended": DAY0 - 60},
            {"id": "edge", "argv": ["winners"], "exit_code": 0, "ended": edge},
            {"id": "new", "argv": ["winners"], "exit_code": 0, "ended": DAY0 + 29 * H}]  # fmt: skip

    def listed(now: float) -> list[str]:
        text = P.message_text(_message(now, jobs[::-1]))
        return [ln.split()[0] for ln in text.splitlines() if " exit 0 at " in ln]

    assert listed(DAY0 + 30 * H - 60) == ["edge", "new"]  # "old" ended before the list's start (6 Nov 00:00)
    assert listed(DAY0 + 30 * H + 60) == ["new"]  # the start stepped to 06:00: edge ended 24 hours and two minutes ago
    for now in range(edge, edge + 24 * H + 1, 1800):  # whatever steps fall into its first day, it is listed
        assert "edge" in listed(now), now
    head = _message(DAY0 + 30 * H - 60, jobs[::-1])[0]["text"]
    assert "ended since 6 Nov 00:00 UTC, the oldest first" in head  # the heading names the start: it moves with it


def test_a_line_of_the_list_is_the_same_bytes_a_day_apart():
    """A line carries the day its job ended, so it never changes while it is listed: "at 00:50" would be another
    job's line a day later, and a line that changes cannot be read from the cache."""
    job = {"id": "20261106-130000-abcdef", "argv": ["winners", "--hours", "72"], "exit_code": 0, "ended": DAY0 + 13 * H}
    line = "20261106-130000-abcdef exit 0 at 6 Nov 13:00, by the scout: winners --hours 72"
    for now in (DAY0 + 14 * H, DAY0 + 38 * H):  # an hour after it ended, and a day after that
        text = P.message_text(_message(now, [job], started_by={job["id"]: "scout"}))
        assert line in text.splitlines()


def test_nothing_that_changes_every_turn_stands_before_the_last_block():
    """The reason, the time, the dollars spent and the turn's limits are in the last block and nowhere before it, and
    so are the running jobs, the lessons, the queue, the other roles' tables and the role's own table."""
    now = DAY0 + 30 * H + 17 * 60
    blocks = _message(now, _ended(23, now - 20 * H), reason="handed an idea: I77 (by the scout)",
                      running=[{"id": "r9", "argv": ["lab", "--json"]}])  # fmt: skip
    front, last = "".join(b["text"] for b in blocks[:-1]), blocks[-1]["text"]
    moving = ("handed an idea: I77", "Called because", "Time: 2026-11-07 06:17 UTC", "06:17", "12.34", "80 tool calls",
              "175 $", "This turn stops at", "running now", "r9", "lesson one", "| hunt 4 | 31 |",
              "Do your role's turn", "Turn of the")  # fmt: skip
    for word in moving:
        assert word in last and word not in front, word
    assert front.startswith("Research record (the cloud session's research rounds)")  # what a deploy changes, first
    assert front.count(" exit 0 at ") == 20 and last.count(" exit 0 at ") == 3  # two whole blocks; the rest is last
    # the same front whatever the reason, the time of the round, the spend, the memory and the table
    other = _message(now + 600, _ended(23, now - 20 * H), reason="first turn", learned="", table=None,
                     ceiling={"tool_calls": 40, "turn_usd": 2.0, "spent_usd": 0.0, "share_usd": 50.0})  # fmt: skip
    assert "".join(b["text"] for b in other[:-1]) == front


def test_a_short_list_keeps_the_marker_off_the_last_block():
    """With fewer than ten ended jobs no block of the list is whole: the marker stands on the block in front (the
    research record and the heading), and the last block still has none."""
    now = DAY0 + 30 * H
    blocks = _message(now, _ended(4, now - 2 * H))
    assert len(blocks) == 2 and _marked(blocks) == [0] and blocks[0]["cache_control"] == P.HOUR_CACHE
    none = _message(now, [])
    assert len(none) == 2 and _marked(none) == [0] and "\nnone\n" in none[1]["text"]


def test_a_role_that_comes_less_than_once_an_hour_gets_no_hour_marker():
    """The hour's cache is written at twice the input price and the five-minute one at 1.25 times: where the next turn
    comes after the hour has run out the marker only costs. Without it the message is the same text in the same
    blocks."""
    now = DAY0 + 30 * H
    jobs = _ended(47, now - 20 * H)
    cold = _message(now, jobs, hour=False)
    assert _marked(cold) == []
    assert P.message_text(cold) == P.message_text(_message(now, jobs))


def test_the_marker_stays_within_the_apis_look_back_of_the_turn_before():
    """A marker finds what an earlier request wrote only within 20 blocks behind it. The list may hold 20 to 50 blocks:
    what counts is how far the marker moved since the role's last turn, one block for every ten jobs that ended. Up to
    200 jobs between two turns of a role are found; more than that inside one hour has not happened (14 Nov: 168 jobs
    in a day), and would cost one full write of the list."""
    now = DAY0 + 30 * H
    before = _message(now, _ended(230, now - 20 * H, every=60.0))
    after = _message(now + 900, _ended(430, now - 20 * H, every=60.0))
    assert len(before) == 25 and len(after) == 45  # a long list: 23 and 43 whole blocks
    assert _marked(after)[0] - _marked(before)[0] == 20
    assert [b["text"] for b in after[: _marked(before)[0] + 1]] == [b["text"] for b in before[:-1]]


# -- round 2 (14 Nov): the words a role is told say each thing once and agree with what the code does ------------------


def test_the_role_rules_say_how_a_turn_is_written_once_and_as_the_code_does_it():
    rules = _flat(P.ROLE_RULES)
    assert ("(3) Write, in this order: update_table for the rows that changed (no call when none did), then "
            "write_report with your journal's name and a headline. The headline is this turn's note for the owner and "
            "that call is your last: the turn ends when its reply's calls have run, with no write_note and no closing "
            "summary. A call of that reply that was refused or not done as asked is shown to you so you can repair "
            "it.") in rules  # fmt: skip
    assert "Read what answers the question, start the test, then write (step 3)." in rules
    assert "after two in a row your next turn waits 45 minutes" in rules and "waits an hour" not in rules
    # what counts as work since the reviews of 14 Nov (roles._worked): a lesson and a hand-on do, and a role that is
    # handed an idea or whose job ends is called inside the wait
    assert ("A turn that starts no test, closes no idea, records no lesson and hands nothing on should be rare: after "
            "two in a row your next turn waits 45 minutes, unless an idea is handed to you or a job of yours ends. An "
            "idea you open and close in one turn counts only with its lesson.") in rules  # fmt: skip
    assert "Put no reading call into that reply" in rules and "calls you once, at the next round" in rules
    # what the round-1 words said that the code does not do, or said more than once
    for gone in ("one more reply", "never the whole table", "append=true", "rows, drop, headline"):
        assert gone not in rules, gone
    assert rules.count("write_note") == 1 and rules.count("update_table") == 1
    # steps 1 and 2 keep what they said
    read = ("A job you have read from the top after it ended (no key, fields, first, last or skip) does not call you "
            "again")  # fmt: skip
    for kept in (read, "list_ideas shows one line an idea",
                 "a long list in a result is read with fields", "result holds the numbers that decided"):  # fmt: skip
        assert kept in rules, kept
    # the operator and the analyst are told how a job's result reads as read_job_log gives it
    for text in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        assert "the JSON is the last part of the log" not in _flat(text) and "read from the top" in _flat(text)


def test_a_roles_message_ends_with_one_line_on_what_to_do():
    now = DAY0 + 30 * H + 17 * 60
    last = _message(now, [])[-1]["text"]
    lines = last.splitlines()
    assert lines[-1] == "Do your role's turn (steps 1 to 3). Your table: tester-rules. Your journal today: " \
                        "tester-journal-2026-11-07."  # fmt: skip
    assert lines[-2].endswith("; keep the last calls for step 3.")
    assert "append=true" not in last and "headline" not in last
