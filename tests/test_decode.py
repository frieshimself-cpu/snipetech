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


def test_foreign_quoted_trades_are_kept_apart():
    """A pump.fun curve quoted in a tokenized stock: not a SOL trade, but kept (in its own units) on request."""
    sig, tx = by_prefix("5a9f4Bh3")
    ev = extract_from_transaction(tx, sig)
    raw = next(e.fields for e in ev.events if e.name == "TradeEvent" and e.fields.get("quote_mint", WSOL_MINT) != WSOL_MINT)
    plain, full = normalize(ev), normalize(ev, foreign=True)
    assert all(t.quote_mint == "" for t in plain.trades + full.trades)
    t = next(t for t in full.foreign if t.mint == raw["mint"])
    assert t.quote_mint == raw["quote_mint"] and t.quote == raw["quote_amount"] and t.q_after == raw["virtual_quote_reserves"]
    assert t.venue == "curve" and t.base == raw["token_amount"]
    # routed wBTC -> token A -> token B: both hops kept as foreign trades, none as SOL trades
    sig, tx = by_prefix("4iHFAXgL")
    hops = normalize(extract_from_transaction(tx, sig), foreign=True)
    assert hops.trades == [] and len(hops.foreign) == 2 and {t.venue for t in hops.foreign} == {"amm"}


def test_foreign_trades_are_stored_separately():
    from snipetech.store import Store

    sig, tx = by_prefix("5a9f4Bh3")
    norm = normalize(extract_from_transaction(tx, sig), foreign=True)
    st = Store(":memory:")
    st.add_trades(norm.trades)
    st.add_ftrades(norm.foreign)
    t = norm.foreign[0]
    assert st.trades_for_mint(t.mint) == []  # the SOL-only view (simulations) never sees them
    (back,) = [x for x in st.market(t.mint, t.ts - 1, t.ts + 1) if x.sig == t.sig and x.ev == t.ev]
    assert back.quote_mint == t.quote_mint and back.quote == t.quote


def test_launchlab_round_trip_decodes_with_trader_and_token():
    """A real Raydium LaunchLab buy and the matching sell: the events name neither token nor trader;
    both come from the instruction accounts. Fees are inside a buy's amount_in and outside a sell's amount_out."""
    from snipetech.solana.normalize import normalize

    trades = {}
    for prefix in ("5dGuy1Eg", "3vHcn4Gn"):
        sig, tx = by_prefix(prefix)
        ev = extract_from_transaction(tx, sig)
        raw = next(e.fields for e in ev.events if e.name == "LaunchLabTradeEvent")
        (t,) = normalize(ev).trades
        fees = raw["protocol_fee"] + raw["platform_fee"] + raw["creator_fee"] + raw["share_fee"]
        assert t.venue == "launchlab" and t.user == "Fhbh1DTUDKt6qu9zVg8Q5VgnZa1WWWbxBxG6pCoJoggA" and t.fee == fees
        assert t.mint == "9CU8Mp1iwX37dvLQKKzXkXZ7TCXKkVjHVSWMjmrx9PqN"
        assert t.q_after - (t.quote if t.is_buy else -t.quote) == raw["virtual_quote"] + raw["real_quote_before"]
        trades[t.is_buy] = (t, raw)
    buy, raw = trades[True]
    assert buy.quote + buy.fee == raw["amount_in"] and buy.base == raw["amount_out"]
    q0, b0 = buy.q_after - buy.quote, buy.b_after + buy.base
    assert abs(b0 * buy.quote // (q0 + buy.quote) - buy.base) <= 1  # constant product on virtual + real reserves
    sell, raw = trades[False]
    assert sell.quote - sell.fee == raw["amount_out"] and sell.base == raw["amount_in"] == buy.base
