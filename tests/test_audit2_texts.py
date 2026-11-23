"""The audit of 22-23 Nov 2026, second round, group "texts": what the server's agents are told (operator/prompts.py,
the tool and table descriptions of operator/tools.py), and the words in code the first round made stale, each checked
against the merged code and, where it is a number, against the server."""

import inspect

from snipetech.operator import prompts as P
from snipetech.operator import tools as T


def _flat(text: str) -> str:
    return " ".join(text.split())


def _tools_text() -> str:
    """The words of the operator's and the analyst's tools, as their descriptions are written (the docstrings of
    make_operator_tools and of the learning tools)."""
    return _flat(inspect.getsource(T.make_operator_tools) + inspect.getsource(T.make_learning_tools))


def test_a_decision_note_is_for_the_bots_own_coins_only():
    """Round 1 (round#6, docs#2): the gate raises only the bot's coins; the owner's own coin and an airdrop no longer
    reach the operator as held or unmanaged (22 Nov: one airdrop woke 7 turns that paged the owner). The rule the
    operator and the analyst write notes by still said "a coin no sniper is selling" and "a coin held", of any coin,
    while the snapshot's `held` lists every coin in the wallet with whose it is."""
    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        text = _flat(system)
        assert "a coin our snipers bought that no sniper is selling" in text
        assert "a sniper running or a coin of ours held when none should be" in text
        assert "the owner's own coin and one sent in (`ours` false) are never a decision" in text
        assert "a coin no sniper is selling" not in text and "or a coin held when none should be" not in text
    note = _tools_text()
    assert "a coin our snipers bought that no sniper is selling, a sniper running or a coin of ours held" in note
    assert "a wallet clearing all three gates" not in note  # a verdict has had more gates than three since 7 Nov
