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
    assert m.tips == [{"dest": "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49", "lamports": 1_000_000, "service": "jito"}]
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
