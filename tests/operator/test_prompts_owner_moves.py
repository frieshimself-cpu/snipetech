"""Owner, 9 Nov 14:05: "Don't flag, I constantly take out and put money on there." A change in the sniper wallet's
SOL that no trade of ours explains is the owner moving money: never a decision note, never a question to the owner."""

from snipetech.operator.prompts import OPERATOR_SYSTEM


def test_the_agents_treat_an_unexplained_equity_move_as_the_owner_moving_sol():
    text = " ".join(OPERATOR_SYSTEM.split())
    assert "the owner moves SOL in and out of the sniper wallet" in text
    assert "equity moved more than the alert threshold" not in text
    assert "equity moved by our own trades more than the alert threshold" in text
