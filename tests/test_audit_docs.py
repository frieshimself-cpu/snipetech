"""The audit of 22 Nov 2026, area "docs": what the agents are told and what the record says, checked against the code
and the data it rests on."""

from snipetech.operator import prompts as P


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_fee_analyst_counts_a_place_open_only_with_nobody_within_k_places():
    """22 Nov (I603): the fee analyst's table called the place right behind the big wallets "open three times in four",
    counting block place +1 only, and the roadmap built 100 SOL a day on the lab's +0. On the tape a buy of 0.1 SOL or
    more stood within 3 block places behind 65 % of the first buys of Cented, Decu and theo side, and within 3 before
    on 1 in 386: those buyers have the leader's transaction before it runs, and +0 stands ahead of all of them."""
    fees = _flat(P.FEES_SYSTEM)
    assert '"+0" is the price the leader\'s own buy left, ahead of every buyer after it' in fees
    assert "no send that reacts to the landed buy reaches" in fees
    assert "count it open only when no buy of 0.1 SOL or more stands within K block places of the leader" in fees
