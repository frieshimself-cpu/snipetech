"""Owner, 9 Nov 14:05: "Don't flag, I constantly take out and put money on there." A change in the sniper wallet's
SOL that no trade of ours explains is the owner moving money: never a decision note, never a question to the owner."""

from snipetech.operator.prompts import OPERATOR_SYSTEM


def test_the_agents_treat_an_unexplained_equity_move_as_the_owner_moving_sol():
    text = " ".join(OPERATOR_SYSTEM.split())
    assert "the owner moves SOL in and out of the sniper wallet" in text
    assert "equity moved more than the alert threshold" not in text
    assert "equity moved by our own trades more than the alert threshold" in text


def test_the_agents_know_a_buy_pays_its_fees_in_plain_sol():
    """11 Nov: the first wallet to qualify under the 7 Nov rules made five buys over its floor and our sniper sent
    nothing, twice over (05:09-05:51, 14:12-14:16 UTC): the wallet held 0.0008 SOL beside 0.47 wrapped SOL, and a buy
    pays its priority fee (0.001), its tip (0.002) and a new coin's token account (0.002) in plain SOL. The analyst did
    not know it: nine updates waited for "the sniper's test", then it raised an idea for the owner to priority 5 and
    guessed at OKTech. The wallet is the owner's to fund (7 and 9 Nov: no SOL checks, never asked for SOL), so the
    agents are told the fact: they say it once, in an info clause, and spend no turn on it."""
    from snipetech.operator.prompts import ANALYST_SYSTEM

    for system in (OPERATOR_SYSTEM, ANALYST_SYSTEM):
        text = " ".join(system.split())
        assert "pays its fees, its tip and a new coin's token account in plain SOL" in text
        assert "never in wrapped SOL" in text and "no buy and no failed send" in text
        assert "buy_fees_payable" in text and "no test, no new idea and no new sniper" in text
        assert "Never ask the owner for SOL" in text  # the owner's rule stands


def test_the_snapshot_says_whether_a_buy_can_pay_its_fees():
    """The agents read the wallet's SOL and wrapped SOL side by side and did not draw the conclusion; the snapshot
    draws it: under one buy's fees in plain SOL nothing can be sent, whatever the wrapped SOL."""
    from snipetech.operator.prompts import BUY_NATIVE_SOL, _compact_snapshot
    from snipetech.operator.snapshot import Snapshot

    now = 1_794_400_000
    assert BUY_NATIVE_SOL == 0.005  # 0.001 priority + 0.002 tip + 0.002 for the coin's token account
    low = _compact_snapshot(Snapshot(ts=now, wallet="W", native_sol=0.000808, wsol=0.4718, snipers=[]))
    assert low["buy_fees_payable"] is False and low["native_sol"] == 0.000808 and low["wsol"] == 0.4718
    assert (
        _compact_snapshot(Snapshot(ts=now, wallet="W", native_sol=0.02, wsol=0.0, snipers=[]))["buy_fees_payable"]
        is True
    )
    assert _compact_snapshot(Snapshot(ts=now, wallet="W", snipers=None))["buy_fees_payable"] is None  # balance not read
