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
    text = P.role_message("scout", _snap(), decision, learned="Your queue, best first:\n- I5 hunt wallets",
                          table="| wallet | who |\n| W1 | Cupsey |", started_by={"j1": "scout", "r1": "tester"},
                          ceiling={"tool_calls": 40, "turn_usd": 5.0, "spent_usd": 12.5, "share_usd": 100.0})  # fmt: skip
    assert text.startswith("Turn of the wallet scout. Called because: a job of yours ended: j1.")
    assert "j1 exit 0 at 00:50, by the scout: winners --hours 72" in text
    assert "j2 exit 0 at 00:45: lab --latency 0" in text  # nobody of the roles started it: as the analyst sees it
    assert "r1, by the tester" in text and "Your queue, best first" in text and "| W1 | Cupsey |" in text
    assert "operator/scout-facts.md" in text
    assert "40 tool calls or 5 $" in text and "12.50 $ of your share of 100 $" in text
    for word in ("SniperWalletAddr", "native_sol", "wsol", "equity", "HELDMINT", "snipers", "1.25", "buys_payable",
                 "book_live", "inbox"):  # fmt: skip
        assert word not in text, word
    empty = P.role_message("fees", _snap(jobs_running=[], jobs_recent=[]), Decision(True, "fees", ["first turn"]))
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
    assert not names.search(P.role_message("tester", _snap(), Decision(True, "tester", ["first turn"])))


def test_a_role_that_does_not_exist_has_no_message():
    with pytest.raises(KeyError):
        P.role_message("analyst", _snap(), Decision(True, "analyst", ["x"]))
