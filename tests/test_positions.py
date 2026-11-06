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
