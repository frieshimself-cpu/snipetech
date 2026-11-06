"""Transaction fingerprints against real mainnet transactions."""

import json
from pathlib import Path

from snipetech.solana.txmeta import JITO_TIP_ACCOUNTS, extract_tx_meta

FIX = Path(__file__).parent / "fixtures" / "tx"


def load(prefix: str):
    p = next(FIX.glob(f"{prefix}*.json"))
    return extract_tx_meta(json.loads(p.read_text()), p.stem)


def test_priority_fee_matches_compute_budget():
    m = load("22T8QzXS")
    assert (m.cu_limit, m.cu_price) == (250_000, 200_000)
    assert m.priority_lamports == m.cu_limit * m.cu_price // 1_000_000 == 50_000
    assert m.template == ["cb:limit", "cb:price", "ata:create_idempotent", "pump:buy_v2"]
    (ix,) = m.pump_ixs
    assert ix.name == "buy_v2" and ix.args["max_sol_cost"] == 300_000_000 and ix.router is None


def test_jito_tip_and_bundle_create():
    m = load("2kHVrerh")
    assert m.n_signatures == 4 and len(m.signers) == 4
    assert m.tips == [
        {"dest": "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49", "lamports": 1_000_000, "service": "jito"}
    ]
    assert m.bribe_lamports == m.priority_lamports + 1_000_000
    names = [p.name for p in m.pump_ixs]
    assert names[0] == "create_v2" and names.count("buy_v3") == 2
    buyers = {p.user for p in m.pump_ixs if p.name == "buy_v3"}
    assert buyers <= set(m.signers)  # co-signed bundle buys
    assert len(JITO_TIP_ACCOUNTS) == 8


def test_router_and_platform_fee_are_separated_from_pump_fees():
    m = load("1R1aCFeC")  # a sell routed through a trading bot's program
    (ix,) = m.pump_ixs
    assert ix.name == "sell" and ix.router and ix.router.startswith("GMgnVF")
    # pump's own protocol/creator fees are excluded; what remains is what the trader sent elsewhere
    amounts = sorted(t["lamports"] for t in m.transfers)
    assert amounts == [1_000_000, 2_042_496]


def test_slippage_args_are_decoded():
    m = load("3ph5qZGA")
    (ix,) = m.pump_ixs
    assert ix.name == "buy_exact_sol_in"
    assert ix.args["spendable_sol_in"] == 995_800 and ix.args["min_tokens_out"] == 12_355_897_260


def test_wrapping_sol_is_not_a_payment():
    """transfer -> sync_native -> swap -> close_account: the 0.1 SOL into the signer's temporary wSOL
    account is the trade itself (that account is in neither balance list: created and closed here)."""
    m = load("4bTasFM5")
    assert m.template[4:8] == ["system:transfer", "spl-token:sync_native", "pumpswap:buy_exact_quote_in",
                               "spl-token:close_account"]  # fmt: skip
    assert sorted(t["lamports"] for t in m.transfers) == [1_000_000, 1_556_505]  # 1% platform fee + another fee
    assert all(t["lamports"] < 100_000_000 for t in m.transfers)


def test_byte_array_args_are_stored_as_hex():
    """Some instructions carry `bytes` / `[u8; N]` args; fingerprints must stay JSON-serializable."""
    from snipetech.solana.txmeta import PumpIx, TxMeta, _jsonable

    args = _jsonable({"amount": 5, "memo": b"\x01\xff", "nested": [bytearray(b"\x02")]})
    assert args == {"amount": 5, "memo": "01ff", "nested": ["02"]}
    m = TxMeta("s", 1, 0, 0, "p", ["p"], 1, 5000, 0, None, None, None, [], [], [PumpIx("prog", "buy", "u", args, None)])
    assert json.loads(json.dumps(m.to_dict()))["pump_ixs"][0]["args"]["memo"] == "01ff"


def test_relay_tips_are_recognised_on_oktech_transactions():
    """OKTech tips staked-connection relays, not Jito: Nozomi on the 6 Nov sell, Astralane on a buy, Helius Sender on
    the sweeps. Before this the fingerprint booked them as plain transfers and every fill read tip 0."""
    sell = load("4Pdik2uuPbae")
    assert [(t["lamports"], t["service"]) for t in sell.tips] == [(1_000_000, "nozomi")]
    assert sell.tips[0]["dest"] == "noz6uoYCDijhu1V7cutCpwxNiSovEwLdRHPwmgCGDNo" and sell.transfers == []
    buy = load("xqo6FRzqCxub")
    assert [(t["lamports"], t["service"]) for t in buy.tips] == [(2_000_000, "astralane")]
    from snipetech.solana.txmeta import RELAY_TIP_ACCOUNTS

    # the sweeps (no pump instruction, so not a trade fixture) tipped these Helius Sender accounts
    assert RELAY_TIP_ACCOUNTS["3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT"] == "helius-sender"
    assert RELAY_TIP_ACCOUNTS["4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or"] == "helius-sender"
    assert len(RELAY_TIP_ACCOUNTS) >= 8 + 17 + 10 + 17 + 17 + 2


def test_flash_relay_tip_on_a_6_nov_buy():
    """The FLaSH… vanity accounts received exactly our tipAmount on two 6 Nov buys: a relay nobody's docs name."""
    m = load("48YmS3mVdwXo")
    assert m.pump_ixs and [(t["lamports"], t["service"]) for t in m.tips] == [(2_000_000, "flash")]
    assert m.tips[0]["dest"].startswith("FLaSH") and m.transfers == []


def test_creator_fee_claims_are_decoded_from_real_transactions():
    """Two real claims: one that collected nothing (both vaults empty) and one that collected 18,737 lamports through the
    v2 pump instruction and the PumpSwap coin-creator vault. Claims are not trades (pump_ixs stays empty)."""
    empty = load("5jLo8ERCdTRr")
    assert empty.pump_ixs == [] and [c["name"] for c in empty.claims] == [
        "collect_creator_fee",
        "collect_coin_creator_fee",
    ]
    assert {c["claimer"] for c in empty.claims} == {"3X1FYfhocNoM57jWMmHarRyoLsnGhZHf6omVgVaKNL1a"} and sum(
        c["lamports"] for c in empty.claims
    ) == 0
    assert [c["program"] for c in empty.claims] == ["pump", "pumpswap"]
    real = load("4Jq9WGY127A3")
    assert [c["name"] for c in real.claims] == ["collect_creator_fee_v2", "collect_coin_creator_fee"]
    # a third party signed this claim (a claim bot); the beneficiary is the creator account in both instructions
    assert sum(c["lamports"] for c in real.claims) == 18_737 and real.signers[0] != real.claims[0]["claimer"]
    assert {c["claimer"] for c in real.claims} == {"8o6yYRnEGChhfRHm3zpmXE136Tu2mH7ChMoy741trVvJ"}
    assert real.pump_ixs == [] and all(c["lamports"] >= 0 for c in real.claims)
    from snipetech.solana.txmeta import TxMeta

    again = TxMeta.from_dict(real.to_dict())
    assert again.claims == real.claims  # survives the tx_meta table round trip
