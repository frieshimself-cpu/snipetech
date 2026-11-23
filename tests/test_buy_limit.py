"""The price limit a buy carries, read from the buy itself (23 Nov 2026).

A copy sniper's market-cap ceiling (`ctbotWallets[].maxMcapUsd`) is no rule about the copied wallet's buy. OKTech turns
it into the least number of coins our own buy must get (`min_tokens_out`), so it is a ceiling on the average price of
OUR fill, fee included, and a buy over it fails on chain. The smoke test of 23 Nov showed it: with $4,600 sent the limit
stood at 64.3 to 64.7 SOL of market cap, and at 23 SOL on the sends OKTech worked out at $200 a SOL.

The transactions are real: tests/fixtures/buy_limits_23nov.json (six of the sniper wallet's sends of that test) and the
mainnet fixtures under tests/fixtures/tx for the older forms of a buy."""

import json
from pathlib import Path

import pytest

from snipetech.solana.buy_limit import ix_limit, mcap_sol, meta_limit, refused_on_limit, tx_limit
from snipetech.solana.programs import PUMP_AMM_PROGRAM, PUMP_PROGRAM
from snipetech.solana.txmeta import extract_tx_meta

FIXTURES = Path(__file__).parent / "fixtures"
SMOKE = json.loads((FIXTURES / "buy_limits_23nov.json").read_text())
ME = SMOKE["wallet"]


def smoke(prefix: str) -> dict:
    (tx,) = [tx for sig, tx in SMOKE["txs"].items() if sig.startswith(prefix)]
    return tx


def mainnet(prefix: str) -> dict:
    (path,) = list((FIXTURES / "tx").glob(f"{prefix}*.json"))
    return json.loads(path.read_text())


def test_the_ceiling_is_the_sol_in_over_the_least_coins_out_times_a_billion_coins():
    # 0.02 SOL for at least 869,565.217392 coins: 23 SOL for the billion coins a pump.fun coin has
    assert mcap_sol(20_000_000, 869_565_217_392) == pytest.approx(23.0, abs=1e-6)
    assert mcap_sol(20_000_000, 309_565_217_392) == pytest.approx(64.607, abs=1e-3)
    assert mcap_sol(20_000_000, 0) is None and mcap_sol(0, 5) is None  # no least number of coins: no ceiling


def test_a_refused_curve_buy_says_its_ceiling_and_where_it_would_have_filled():
    """03:17:41, a new sniper's first minute: the limit was worked out at $200 a SOL, 23 SOL of market cap, under the
    28 SOL every pump.fun coin starts at. The program's log says what the buy would have got ("Left")."""
    lim = tx_limit(smoke("4hC1n7oF"), ME)
    assert lim.program == PUMP_PROGRAM and lim.ix == "buy_exact_quote_in_v3" and lim.exact_in
    assert lim.mint == "8LXGpgYJL7JkiWcQf6HuJNvnjuo6w2cB1YtHqkU1HLYp"
    assert (lim.lamports, lim.tokens) == (20_000_000, 869_565_217_392)
    assert lim.mcap_sol == pytest.approx(23.0, abs=1e-6)
    assert lim.would_get == 493_954_215_795 and lim.would_mcap_sol == pytest.approx(40.49, abs=0.01)


def test_the_nearest_refusal_of_the_test_stood_just_over_its_ceiling():
    lim = tx_limit(smoke("4BUCDA8G"), ME)  # 03:50:13: 67.2 SOL at our fill against a ceiling of 64.6
    assert lim.mcap_sol == pytest.approx(64.62, abs=0.01) and lim.would_mcap_sol == pytest.approx(67.25, abs=0.01)


def test_a_buy_that_landed_carries_its_ceiling_and_no_refusal():
    tx = smoke("gXv9uz3u")
    lim = tx_limit(tx, ME)
    assert lim.mcap_sol == pytest.approx(64.607, abs=1e-3) and lim.would_get is None
    # where it filled, as the limit reads it: the SOL the buy spends over the coins it got (the tape's `base`)
    assert lim.fill_mcap_sol(371_200_971_351) == pytest.approx(53.88, abs=0.01)
    # the stored fingerprint gives the same limit: a buy on the tape needs no second look at the chain
    assert meta_limit(extract_tx_meta(tx), ME, lim.mint) == lim


def test_a_pumpswap_buy_is_read_the_same_way():
    lim = tx_limit(smoke("3Tx3daSi"), ME)  # 03:31:05, error 6040 on pump.fun's AMM
    assert lim.program == PUMP_AMM_PROGRAM and lim.ix == "buy_exact_quote_in_v2"
    assert lim.mint == "8gZLiNbDJASooqeRqwPcakXNoTYzdjEuKgyYGUJhpump"
    assert lim.mcap_sol == pytest.approx(23.0, abs=1e-6)
    assert lim.would_get == 154_598_243_038 and lim.would_mcap_sol == pytest.approx(129.37, abs=0.01)


def test_a_buy_through_two_pools_names_its_coin_and_its_ceiling():
    """03:25:06: a coin quoted in another coin, bought through that coin's PumpSwap pool and its own curve
    (`multi_hop_swap`). The send failed before the swap ran, so there is nothing it would have got; its limit and its
    coin are in the instruction and its accounts."""
    lim = tx_limit(smoke("4YtcQUzh"), ME)
    assert lim.ix == "multi_hop_swap" and lim.exact_in and lim.would_get is None
    assert lim.mint.startswith("2eWqySqV") and (lim.lamports, lim.tokens) == (20_000_000, 310_478_260_870)
    assert lim.mcap_sol == pytest.approx(64.417, abs=1e-3)
    # the fingerprint names neither the coin nor what goes in and out: nothing is read from it
    assert meta_limit(extract_tx_meta(smoke("4YtcQUzh")), ME) is None


def test_a_sale_carries_no_buy_limit_whatever_its_log_says():
    """A failed sale's log has a "Left" and a "Right" too (NotEnoughTokensToSell: 0 against the coins asked for)."""
    tx = smoke("5ePdm8Lk")
    assert any("Left: 0" in line for line in tx["meta"]["logMessages"])
    assert tx_limit(tx, ME) is None and meta_limit(extract_tx_meta(tx), ME) is None


def test_another_wallets_buy_is_not_ours():
    assert tx_limit(smoke("4hC1n7oF"), "11111111111111111111111111111111") is None


@pytest.mark.parametrize(
    ("prefix", "ix", "exact_in", "lamports", "tokens", "cap"),
    [
        # the older buy: so many coins for at most so much SOL; the limit is that SOL over those coins
        ("22T8QzXS", "buy_v2", False, 300_000_000, 9_018_541_829_815, 33.2648),
        ("3iWtVbuj", "buy_v3", False, 283_692_155, 4_215_535_578_598, 67.2968),
        ("3ph5qZGA", "buy_exact_sol_in", True, 995_800, 12_355_897_260, 80.5931),
        ("3kz7PFEU", "buy", False, 173_098_759, 1_625_134_558, 106_513.5),  # PumpSwap, a coin worth millions
        ("4bTasFM5", "buy_exact_quote_in", True, 100_000_000, 8_438_072_100, 11_851.05),
    ],
)
def test_the_older_forms_of_a_buy_on_the_mainnet_fixtures(prefix, ix, exact_in, lamports, tokens, cap):
    tx = mainnet(prefix)
    buyer = next(p.user for p in extract_tx_meta(tx).pump_ixs if p.name == ix)
    lim = tx_limit(tx, buyer)
    assert (lim.ix, lim.exact_in, lim.lamports, lim.tokens) == (ix, exact_in, lamports, tokens)
    assert lim.mcap_sol == pytest.approx(cap, rel=1e-4) and lim.would_get is None


def test_a_buy_with_no_least_number_of_coins_has_no_real_ceiling():
    """Our own landed buys of before 12 Nov asked for at least one raw unit of the coin: no limit in practice."""
    lim = tx_limit(mainnet("48YmS3mV"), ME)
    assert (lim.lamports, lim.tokens) == (50_000_000, 1) and lim.mcap_sol == pytest.approx(5e13)


def test_a_buy_quoted_in_another_coin_carries_no_sol_limit():
    """4iHFAXgL: two PumpSwap buys in one transaction, each paid in another token than SOL. Their numbers are not SOL,
    so no ceiling in SOL is read from them: empty, never a guess."""
    tx = mainnet("4iHFAXgL")
    buyers = {p.user for p in extract_tx_meta(tx).pump_ixs if p.name.startswith("buy")}
    assert len(buyers) == 1 and tx_limit(tx, buyers.pop()) is None


def test_an_instruction_this_code_does_not_know_gives_nothing():
    assert ix_limit(PUMP_PROGRAM, "sell_v3", {"amount": 5, "min_sol_output": 1}) is None
    assert ix_limit(PUMP_PROGRAM, "buy_from_the_future", {"spendable_quote_in": 5, "min_tokens_out": 1}) is None
    assert ix_limit(PUMP_PROGRAM, "buy_exact_quote_in_v3", {"spendable_quote_in": 5}) is None  # an argument short
    assert ix_limit("SomeOtherProgram", "buy", {"amount": 5, "max_sol_cost": 9}) is None


def test_two_buys_of_the_wallet_with_different_limits_are_not_told_apart():
    one = extract_tx_meta(smoke("4hC1n7oF"))
    other = extract_tx_meta(smoke("4BUCDA8G"))
    one.pump_ixs += other.pump_ixs  # a transaction that carried both: which limit a fill had is not known
    assert meta_limit(one, ME) is None
    assert meta_limit(one, ME, "8LXGpgYJL7JkiWcQf6HuJNvnjuo6w2cB1YtHqkU1HLYp").mcap_sol == pytest.approx(23.0)


QUOTE = json.loads((FIXTURES / "quote_mint_23nov.json").read_text())


def test_a_refusal_on_the_buys_own_limit_is_told_from_every_other_failed_send():
    """The runner rests a sniper after a streak of sends that bought nothing (service.FAIL_STREAK_*), but a refusal at
    the market-cap ceiling is the rule's own cost, which the paper charges at every such moment and copies on after
    (lab.REFUSED_SEND_SOL): it must be told from the sends no paper prices. By the program's own log against the buy's
    own least coins, never by the error's number alone: the two programs number their errors apart (6040 is
    PumpSwap's refusal on the least coins and pump.fun's "not enough SOL to cover rent"; 6042 is pump.fun's refusal and
    PumpSwap's "only pump pools in mayhem mode")."""
    assert refused_on_limit(smoke("4hC1n7oF"), ME) and refused_on_limit(smoke("4BUCDA8G"), ME)  # the curve's, 6042
    assert refused_on_limit(smoke("3Tx3daSi"), ME)  # PumpSwap's, 6040
    assert not refused_on_limit(smoke("gXv9uz3u"), ME)  # a buy that landed
    assert not refused_on_limit(smoke("4YtcQUzh"), ME)  # a buy through two pools that failed on a program id
    assert not refused_on_limit(smoke("5ePdm8Lk"), ME)  # a sale that failed: its log has the two sides too
    ((sig, quoted),) = QUOTE["txs"].items()  # 16:33:01: a copy of a buy in a pool quoted in tokenized Nvidia (6009)
    assert sig.startswith("2TicE4GD") and quoted["meta"]["err"] == {"InstructionError": [4, {"Custom": 6009}]}
    assert not refused_on_limit(quoted, QUOTE["wallet"])
    assert not refused_on_limit(smoke("4hC1n7oF"), "SomeoneElse1111111111111111111111111111111")  # not our send
    assert not refused_on_limit(None, ME)  # a transaction the node did not serve
