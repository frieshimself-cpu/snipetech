"""Decoder and normalization tests against real mainnet transactions (tests/fixtures/tx)."""

import json
from pathlib import Path

import pytest

from snipetech.solana.base58 import b58decode, b58encode, is_pubkey
from snipetech.solana.normalize import normalize
from snipetech.solana.programs import PUMP_PROGRAM, WSOL_MINT, decode_event, pump_idl
from snipetech.solana.tx import _account_keys, extract_from_logs, extract_from_transaction

FIX = Path(__file__).parent / "fixtures" / "tx"
TXS = {p.stem: json.loads(p.read_text()) for p in sorted(FIX.glob("*.json"))}


def by_prefix(prefix: str) -> tuple[str, dict]:
    sig = next(s for s in TXS if s.startswith(prefix))
    return sig, TXS[sig]


def test_base58_roundtrip():
    raw = bytes(range(32))
    assert b58decode(b58encode(raw)) == raw
    assert b58decode(b58encode(b"\0\0abc")) == b"\0\0abc"
    assert is_pubkey(PUMP_PROGRAM)
    assert not is_pubkey("not-a-key")


def test_idl_discriminators_match_anchor():
    import hashlib

    idl = pump_idl()
    for disc, name in idl.events.items():
        assert hashlib.sha256(f"event:{name}".encode()).digest()[:8] == disc


@pytest.mark.parametrize("sig", sorted(TXS))
def test_cpi_and_log_events_agree(sig):
    tx = TXS[sig]
    cpi = extract_from_transaction(tx, sig).events
    logs = extract_from_logs(tx["meta"]["logMessages"])
    assert cpi, "every fixture carries at least one pump event"
    assert [(e.name, e.fields) for e in cpi] == [(e.name, e.fields) for e in logs]


def test_create_event_fields():
    sig, tx = by_prefix("28fREJXt")
    ev = extract_from_transaction(tx, sig)
    norm = normalize(ev)
    (launch,) = norm.launches
    assert launch.mint == "EzHRAWk4UBVyyztmeCk2EgBx7BBKEeRWMsaXSWZhpump"
    assert launch.user == "2W2kBb9bGN5XeBoVZ1KVP7MxNXZFe2dEntiFQhTSpLhR"
    assert launch.creator == "8LxvrjUyF9wFqMm6uKzexkE1uNRgnJsdQPtAiQLgv4Cr"  # delegated creation
    assert launch.holder_reward and not launch.mayhem
    assert len(norm.trades) == 1 and norm.trades[0].user == launch.user  # dev buy in the create tx

    sig, tx = by_prefix("3uBdchRE")
    assert normalize(extract_from_transaction(tx, sig)).launches[0].mayhem


def _lamport_delta(tx: dict, user: str) -> int:
    keys = _account_keys(tx)
    i = keys.index(user)
    meta = tx["meta"]
    return meta["preBalances"][i] - meta["postBalances"][i] - (meta["fee"] if i == 0 else 0)


@pytest.mark.parametrize("prefix", ["22BtADtf", "1R1aCFeC", "3ph5qZGA"])
def test_curve_trade_cost_matches_wallet_delta(prefix):
    """The trader's wallet moves quote ± fee, plus only small extras (tips, token-account rent)."""
    sig, tx = by_prefix(prefix)
    (t,) = normalize(extract_from_transaction(tx, sig)).trades
    extra = _lamport_delta(tx, t.user) + t.signed_quote
    assert 0 <= extra < 10_000_000


def test_curve_reserves_are_post_trade():
    """Consecutive trades on one curve chain exactly: before(t2) == after(t1)."""
    hist = json.loads((Path(__file__).parent / "fixtures" / "histories.json").read_text())
    trades = hist["EzHRAWk4UBVyyztmeCk2EgBx7BBKEeRWMsaXSWZhpump"]["trades"]
    for a, b in zip(trades, trades[1:]):
        before_q = b["q_after"] - b["quote"] if b["is_buy"] else b["q_after"] + b["quote"]
        assert before_q == a["q_after"]


@pytest.mark.parametrize("prefix", ["3kz7PFEU", "41uRdjyF", "3ErkbBtM", "Q13fDdg5"])
def test_amm_events_use_pre_trade_effective_reserves(prefix):
    sig, tx = by_prefix(prefix)
    ev = extract_from_transaction(tx, sig)
    (raw,) = [e for e in ev.events if e.name in ("BuyEvent", "SellEvent")]
    f = raw.fields
    pool = f["pool"]
    mint, quote = ev.pool_mints[pool]
    assert quote == WSOL_MINT
    pre = {(b["owner"], b["mint"]): int(b["uiTokenAmount"]["amount"]) for b in tx["meta"]["preTokenBalances"]}
    assert f["pool_base_token_reserves"] == pre[(pool, mint)]
    assert f["pool_quote_token_reserves"] == pre[(pool, WSOL_MINT)]
    (t,) = normalize(ev).trades
    assert t.venue == "amm" and t.mint == mint
    q_eff = f["pool_quote_token_reserves"] + f["virtual_quote_reserves"]
    b0 = f["pool_base_token_reserves"]
    if raw.name == "SellEvent":
        assert t.quote == q_eff * f["base_amount_in"] // (b0 + f["base_amount_in"])
        assert _lamport_delta(tx, t.user) == -(t.quote - t.fee)
    elif raw.ix_name == "buy":
        assert t.quote == f["quote_amount_in"]  # constant-product cost of the tokens received
        assert t.quote + t.fee == f["user_quote_amount_in"]


def test_amm_pools_quoted_in_another_token_are_not_sol_trades():
    """A routed swap wrapped BTC -> token A -> token B: neither PumpSwap pool is quoted in SOL, so
    neither hop is a SOL trade (read naively, the second hop looked like an 18 SOL buy)."""
    sig, tx = by_prefix("4iHFAXgL")
    ev = extract_from_transaction(tx, sig)
    bases = {base for base, _ in ev.pool_mints.values()}
    quotes = {quote for _, quote in ev.pool_mints.values()}
    assert len(ev.pool_mints) == 2 and WSOL_MINT not in quotes
    assert any(q.startswith("3NZ9JMVB") for q in quotes) and bases & quotes  # first hop's token pays for the second
    assert normalize(ev).trades == []
    # a stale cache entry claiming SOL quotes does not override the instruction accounts
    stale = {p: (base, WSOL_MINT) for p, (base, _) in ev.pool_mints.items()}
    assert normalize(ev, known_pools=stale).trades == []


def test_borsh_tolerates_older_shorter_events():
    """Older TradeEvents end before the newer appended fields: decode what is there."""
    sig, tx = by_prefix("22BtADtf")
    full = extract_from_logs(tx["meta"]["logMessages"])[0]
    import base64

    line = next(l for l in tx["meta"]["logMessages"] if l.startswith("Program data: "))
    payload = base64.b64decode(line.split(": ", 1)[1])
    short = payload[: 8 + 32 + 8 + 8 + 1 + 32 + 8 + 8 + 8]  # original 2024 layout
    name, fields = decode_event(PUMP_PROGRAM, short)
    assert name == "TradeEvent"
    assert fields["virtual_token_reserves"] == full.fields["virtual_token_reserves"]
    assert "real_sol_reserves" not in fields
