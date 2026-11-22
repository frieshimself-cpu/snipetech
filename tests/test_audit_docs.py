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


def test_the_operator_and_the_analyst_are_told_what_the_research_record_holds():
    """docs#1: the record now carries the newest round's outcome and lessons, or its line of the index, then a line
    for each other report, newest first: the dated reports of 9-12 Nov too, not only the rounds older than the
    newest."""
    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        text = _flat(system)
        assert "one line for each other report, newest first, as many as fit" in text
        assert "one line for each older round" not in text


STALE_SELL_PRIORITY = ("what each twin pays", "what each failed twin pays", "the sell priority on every failed twin",
                       "twins 100,000 lamports priority", "the sell priority is near zero")  # fmt: skip


def test_no_agent_text_says_the_failed_twins_pay_the_sell_priority():
    """docs#3: config.py and docs/oktech-playbook.md (1d) were corrected on 12 Nov: OKTech does not charge the sell
    priority we send; 18 of 19 landed sells and 25 of 28 failed twins of 8-12 Nov paid the sniper's buy priority, and
    every sell (23) and failed send (50) since 20 Nov paid 1,000,000 lamports, the buy's. The playbook every deciding
    agent reads, the fee policy's rule and reasons, read_fee_policy and the strategist still said the sell priority is
    near zero and is what each failed twin pays: a cap on it changes nothing, and twins were priced at a tenth."""
    import inspect

    from snipetech import config, oktech
    from snipetech import fees as F
    from snipetech.config import Config
    from snipetech.operator import tools as T
    from snipetech.playbook import OKTECH_PLAYBOOK
    from snipetech.strategist.prompts import STRATEGIST_SYSTEM

    assert "OKTech does not charge this number on a sell" in _flat(inspect.getsource(config))  # the 12 Nov fix
    texts = {
        "playbook": OKTECH_PLAYBOOK,
        "strategist": STRATEGIST_SYSTEM,
        "fees.RULE": F.RULE,
        "the sell priority's reason": F.choose(Config(), "copy").why["sell_priority"],
        "read_fee_policy": inspect.getsource(T.make_operator_tools),
        "oktech.autosell": oktech.autosell.__doc__,
    }
    for who, text in texts.items():
        for words in STALE_SELL_PRIORITY:
            assert words not in _flat(text), (who, words)
    for who in ("playbook", "strategist", "fees.RULE", "the sell priority's reason", "oktech.autosell"):
        assert "buy priority" in _flat(texts[who]) and "not used" in _flat(texts[who]), who
