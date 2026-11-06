"""Every deciding agent (the 15-minute operator, the daily analyst, the weekly strategist) is told the same measured
OKTech facts, and the stale claims that made a capped sniper's last buy look safe are gone from their instructions."""

import re

from snipetech.operator.prompts import ANALYST_SYSTEM, OPERATOR_SYSTEM
from snipetech.playbook import OKTECH_PLAYBOOK
from snipetech.strategist.prompts import STRATEGIST_SYSTEM


def test_every_agent_gets_the_oktech_playbook():
    for system in (OPERATOR_SYSTEM, ANALYST_SYSTEM, STRATEGIST_SYSTEM):
        assert OKTECH_PLAYBOOK in system
    text = OKTECH_PLAYBOOK.lower()
    assert "one copy sniper per leader" in text and "last buy" in text and "autostop" in text


def test_the_playbook_is_a_short_numbered_list():
    numbers = [int(m) for m in re.findall(r"^(\d+)\. ", OKTECH_PLAYBOOK, flags=re.MULTILINE)]
    assert 8 <= len(numbers) <= 12 and numbers == list(range(1, len(numbers) + 1))


def test_the_agents_no_longer_learn_the_old_gates():
    for system in (OPERATOR_SYSTEM, ANALYST_SYSTEM):
        assert "three gates" not in system and "read_verdicts" in system and "copy_plan" in system


def test_the_strategist_raises_a_bid_only_through_the_tip():
    s = " ".join(STRATEGIST_SYSTEM.lower().split())
    assert "only through the tip" in s and "separate oktech wallets" in s and "20 live" in s
