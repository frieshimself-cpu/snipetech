"""One definition of 'the wallet holds a coin' for /stop, the book, the snapshot, the guard and the cycler:
WSOL and stablecoins are never positions, a token is a position when its tape value is at least the dust floor,
and a token the tape has never seen is not one (the sniper wallet only buys pump coins, which the tape records)."""

from snipetech.positions import ACTIVE_STATUSES, STABLES, WSOL, filter_positions, is_active

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
COIN, JUNK = "Coin" + "1" * 40, "Junk" + "2" * 40


def _acct(mint, ui, raw, decimals=6):
    return {"mint": mint, "amount": ui, "raw": raw, "decimals": decimals, "account": f"acct-{mint[:4]}"}


def test_stablecoin_dust_and_wsol_are_never_positions():
    assert USDC in STABLES and WSOL.startswith("So1")
    accounts = [_acct(USDC, 0.0006, 600), _acct(WSOL, 0.12, 120_000_000, 9)]
    assert filter_positions(accounts, lambda mint: 1.0) == []


def test_real_coin_is_valued_with_the_tape_price():
    # 675,850 tokens (the one decoded OKTech position) at 1.2e-5 SOL each = 0.0081 SOL: a position
    price = {COIN: 12.0}.get  # lamports per raw unit (1e-6 token): 675,850 tokens = 675_850_000_000 raw
    pos = filter_positions(
        [_acct(COIN, 675_850.0, 675_850_000_000)], lambda m: 1.2e-5 * 1e9 / 1e6 if m == COIN else None
    )
    assert len(pos) == 1 and pos[0]["mint"] == COIN and abs(pos[0]["value_sol"] - 8.1102) < 0.01
    assert price  # noqa: the mapping above documents the units


def test_dust_by_value_is_not_a_position_but_an_unpriced_coin_is():
    """A coin the tape has not priced yet (bought seconds ago, or during a recorder outage) is held: stopping its
    sniper would strand it (the 5 Nov 22:14 incident). It is reported as unpriced and worth nothing for equity."""
    tiny = _acct(COIN, 50.0, 50_000_000)  # 50 tokens at 1e-5 SOL = 0.0005 SOL: below the 0.001 floor
    unknown = _acct(JUNK, 1_000_000.0, 10**12)  # never traded on the tape
    out = filter_positions([tiny, unknown], lambda m: 0.01 if m == COIN else None, unknown=(lst := []))
    assert [a["mint"] for a in out] == [JUNK] and out[0]["value_sol"] is None and out[0]["priced"] is False
    assert [a["mint"] for a in lst] == [JUNK]
    assert filter_positions([tiny], lambda m: 0.01, dust_sol=0.0001)[0]["mint"] == COIN
    priced = filter_positions([_acct(COIN, 1e6, 10**12)], lambda m: 10.0)
    assert priced[0]["priced"] is True and priced[0]["value_sol"] == 10_000.0


def test_ignored_mints_are_left_out():
    assert filter_positions([_acct(COIN, 1e6, 10**12)], lambda m: 10.0, ignore={COIN}) == []


def test_active_statuses_include_starting_and_stopping():
    assert ACTIVE_STATUSES == frozenset({"running", "starting", "stopping"})
    assert is_active("running") and is_active("starting") and is_active("stopping")
    assert not is_active("idle") and not is_active("finished") and not is_active("failed") and not is_active(None)


# -- what the wallet can pay, beside the wrapped SOL OKTech keeps (audit of 22 Nov, second round: B4) -----------------


def test_buys_payable_counts_what_is_left_once_oktech_has_wrapped_its_top_up():
    """The book makes a sniper only while one curve copy can be paid after OKTech has wrapped 1.1 times the buy from
    plain SOL (the wrap test of 21 Nov; curve_buy_beside_topup). `buys_payable`, which the agents' snapshot and the
    round's alerts read, still counted the plain SOL as it stood: with 0.30 SOL and none wrapped it said one curve copy
    and no PumpSwap copy, where a running 0.2 SOL sniper has 0.08 plain SOL (no curve copy) and 0.22 wrapped (one
    PumpSwap copy). Both numbers are what is left once the top-up is made, and `oktech_wraps_sol` says how much that
    is."""
    from snipetech.positions import buys_payable

    def can(native, wsol, wraps, curve, pumpswap):
        return buys_payable(native, wsol, 0.2) == {"size_sol": 0.2, "oktech_wraps_sol": wraps, "curve": curve,
                                                   "pumpswap": pumpswap}  # fmt: skip

    assert can(0.30, 0.0, wraps=0.22, curve=0, pumpswap=1)
    assert can(0.425, 0.0, wraps=0.22, curve=1, pumpswap=1) and buys_payable(0.424, 0.0, 0.2)["curve"] == 0
    # the top-up is there already: nothing more is wrapped, and the plain SOL pays the curve copies
    assert can(0.205, 0.22, wraps=0.0, curve=1, pumpswap=1)
    assert can(0.5, 0.4718, wraps=0.0, curve=2, pumpswap=2)
    # 11 Nov: fees for one buy beside 0.47 wrapped SOL: one PumpSwap copy, no curve copy; under one buy's fees, nothing
    assert can(0.00524, 0.4718, wraps=0.0, curve=0, pumpswap=1)
    assert can(0.000808, 0.4718, wraps=0.0, curve=0, pumpswap=0)
    # less plain SOL than the top-up asks for: all of it is wrapped, and nothing is left for any buy's fees
    assert can(0.10, 0.05, wraps=0.1, curve=0, pumpswap=0)
    assert buys_payable(None, 0.2, 0.2) is None and buys_payable(0.2, None, 0.2) is None  # a balance not read
    assert buys_payable(1.0, 0.0, None)["size_sol"] == 0.2  # no size known: the book's smallest copy


def test_the_books_can_pay_rule_and_the_snapshots_count_are_one_rule():
    """One helper (after_topup): the book makes a sniper exactly when the snapshot says a curve copy can be paid, at
    every balance, and what one copy needs in plain SOL (curve_plain_need) is where that turns."""
    from snipetech.positions import after_topup, buys_payable, curve_buy_beside_topup, curve_plain_need

    for fill in (0.05, 0.2, 1.0):
        for wsol in (0.0, 0.1, 0.22, 0.5, 1.1, 3.0):
            need = curve_plain_need(wsol, fill)
            for native in (0.0, 0.004, 0.08, 0.205, 0.3, 0.424, 0.425, 1.0, 2.2, need - 1e-6, need, need + 1e-6):
                can = buys_payable(native, wsol, fill)
                assert (can["curve"] >= 1) is curve_buy_beside_topup(native, wsol, fill), (fill, wsol, native)
                assert curve_buy_beside_topup(native, wsol, fill) is (native + 1e-9 >= need), (fill, wsol, native)
                plain, wrapped = after_topup(native, wsol, fill)
                assert abs(plain + wrapped - native - wsol) < 1e-12 and plain >= 0 and wrapped >= wsol
    assert abs(curve_plain_need(0.0, 0.2) - 0.425) < 1e-12 and curve_buy_beside_topup(None, 0.0, 0.2) is None


def test_what_a_curve_buy_takes_and_what_can_be_unwrapped_are_the_same_rule_for_every_reader():
    """Round 3 of the 22 Nov audit: three places said what one curve buy needs in plain SOL, each its own way: the
    book and the agents' snapshot (0.205 SOL for a 0.2 SOL copy once OKTech's top-up is wrapped), the conversion of
    wrapped SOL (0.205, the top-up not known to it: it turned back what OKTech wraps again within seconds) and the
    automatic test's wait (0.252: its sniper's own costs). One function a size and its costs (`curve_buy_room`), one
    for the wrapped SOL above what OKTech keeps wrapped (`spare_wsol`: all that a conversion can move for good)."""
    from snipetech import positions as P
    from snipetech.operator import autotest, live_test
    from snipetech.positions import buys_payable, curve_buy_room, curve_plain_need, spare_wsol

    BUY_NATIVE_SOL, WRAP_TOPUP = P.BUY_NATIVE_SOL, P.WRAP_TOPUP

    assert abs(curve_buy_room(0.2) - 0.205) < 1e-12 and abs(curve_buy_room(None) - 0.205) < 1e-12
    assert abs(curve_buy_room(1.0, 0.05) - 1.05) < 1e-12
    for fill in (0.05, 0.2, 1.0):
        room = curve_buy_room(fill)
        assert abs(room - fill - BUY_NATIVE_SOL) < 1e-12
        for wsol in (0.0, 0.1, 0.22, 0.5, 1.1, 3.0):
            spare = spare_wsol(wsol, fill)
            assert abs(spare - max(0.0, wsol - WRAP_TOPUP * fill)) < 1e-12
            # what one copy needs in plain SOL is its room and what OKTech still wraps
            assert abs(curve_plain_need(wsol, fill) - room - max(0.0, WRAP_TOPUP * fill - wsol)) < 1e-12
            # with the spare turned into plain SOL the top-up is still there: every lamport of it pays curve copies
            for native in (0.0, 0.004, 0.2):
                can = buys_payable(native + spare, wsol - spare, fill)
                assert can["oktech_wraps_sol"] == round(min(max(0.0, WRAP_TOPUP * fill - (wsol - spare)),
                                                            native + spare), 6)  # fmt: skip
                if wsol >= WRAP_TOPUP * fill:
                    assert can["oktech_wraps_sol"] == 0 and can["curve"] == int((native + spare + 1e-9) // room)
    # the automatic test's wait is the same sum with its own sniper's costs (fees, failed sends, the account's rent)
    costs = live_test.BUY_FEE_MARGIN_SOL + autotest.RENT_SOL
    for size in (0.05, 0.2, 0.5):
        assert autotest._curve_room(size) == round(curve_buy_room(size, costs), 4)
    assert autotest._curve_room(0.2) == 0.252
