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
        assert "buys_payable" in text and "no test, no new idea and no new sniper" in text
        assert "Never ask the owner for SOL" in text  # the owner's rule stands


def test_the_agents_are_told_a_curve_buy_is_paid_whole_in_plain_sol():
    """11 Nov 18:52 UTC: the wallet held 0.0052 SOL beside 0.47 wrapped SOL, the snapshot said a buy's fees were
    payable, and the first copy of 74xVT9 landed in its block and failed: pump.fun's curve takes the whole buy (0.2
    SOL) from plain SOL ("Transfer: insufficient lamports 2725537, need 200000000"). Wrapped SOL had paid for the last
    live buys (10 Nov) because those coins had left the curve: only PumpSwap is paid in wrapped SOL. The agents and the
    owner had been told a buy needs about 0.005 plain SOL."""
    from snipetech.operator.prompts import ANALYST_SYSTEM

    for system in (OPERATOR_SYSTEM, ANALYST_SYSTEM):
        text = " ".join(system.split())
        assert "on pump.fun's curve the buy itself is paid in plain SOL" in text
        assert "Wrapped SOL pays only for a coin that has left its curve (PumpSwap)" in text
        assert "about 0.005 SOL a buy" not in text  # the wrong short form of 11 Nov
        assert "insufficient lamports" in text  # how such a failed buy reads in fills


def test_the_wallets_fees_are_said_once_a_day_not_in_every_update():
    """11 Nov, 15:00-17:30 UTC: told to "say once" that the wallet cannot pay a buy's fees, the analyst said it once in
    each half-hourly update, report entry and note: five notes in an afternoon on the owner's live page, about a balance
    the owner asked never to be flagged on (9 Nov). Once a day, and not at all when the day's report already says it."""
    from snipetech.operator.prompts import ANALYST_SYSTEM

    for system in (OPERATOR_SYSTEM, ANALYST_SYSTEM):
        text = " ".join(system.split())
        assert "once a day" in text and "already says it" in text
        assert "leave it out of the update, the report and the note" in text
        assert "Say once, in an info clause" not in text


def test_the_snapshot_says_how_many_buys_the_wallet_can_pay_on_each_venue():
    """The agents read the wallet's SOL and wrapped SOL side by side and did not draw the conclusion; the snapshot
    draws it, a venue at a time: a curve buy is paid whole in plain SOL, a PumpSwap buy in wrapped SOL, and each needs
    its fees, tip and token account in plain SOL on top."""
    from snipetech.operator.prompts import BUY_NATIVE_SOL, _compact_snapshot
    from snipetech.operator.snapshot import Snapshot

    now = 1_794_400_000
    assert BUY_NATIVE_SOL == 0.005  # 0.001 priority + 0.002 tip + 0.002 for the coin's token account

    def payable(native, wsol, fill=0.2):
        snap = Snapshot(ts=now, wallet="W", native_sol=native, wsol=wsol, snipers=[], book_fill_sol=fill)
        return _compact_snapshot(snap)["buys_payable"]

    # 11 Nov morning: nothing at all can be sent
    assert payable(0.000808, 0.4718) == {"size_sol": 0.2, "curve": 0, "pumpswap": 0}
    # 11 Nov 18:52: fees for one buy and 0.47 wrapped SOL: one PumpSwap buy, no curve buy (the one that failed)
    assert payable(0.00524, 0.4718) == {"size_sol": 0.2, "curve": 0, "pumpswap": 1}
    assert payable(0.012, 0.4718)["pumpswap"] == 2  # wrapped SOL for two, fees for two
    assert payable(0.5, 0.4718) == {"size_sol": 0.2, "curve": 2, "pumpswap": 2}  # 0.205 a curve buy
    assert payable(0.5, 0.0) == {"size_sol": 0.2, "curve": 2, "pumpswap": 0}
    assert payable(0.41, 0.0, fill=0.2)["curve"] == 2 and payable(0.409, 0.0)["curve"] == 1
    compact = _compact_snapshot(Snapshot(ts=now, wallet="W", snipers=None, book_fill_sol=0.2))
    assert compact["buys_payable"] is None  # balance not read
    assert "buy_fees_payable" not in compact  # the flag that read "payable" while no curve buy could be paid
    # a snapshot from before the field (or a book without a size): the book's smallest copy, 0.2 SOL
    old = _compact_snapshot(Snapshot(ts=now, wallet="W", native_sol=0.5, wsol=0.0, snipers=[]))
    assert old["buys_payable"] == {"size_sol": 0.2, "curve": 2, "pumpswap": 0}


def test_the_snapshot_carries_the_books_copy_size(tmp_path):
    from snipetech.config import Config
    from snipetech.operator import snapshot as snapmod

    assert "book_fill_sol" in snapmod.Snapshot.__dataclass_fields__
    cfg = Config()
    assert snapmod.book_fill_sol(cfg) == cfg.book.fill_min_sol == 0.2
