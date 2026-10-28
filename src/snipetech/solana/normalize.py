"""Turn decoded pump.fun / PumpSwap events into normalized Launch/Trade records.

Semantics verified against mainnet transactions (see tests/test_decode.py):

* Bonding curve `TradeEvent`: `virtual_*_reserves` are *after* the trade. The trader
  pays `sol_amount + fee + creator_fee` on a buy and receives
  `sol_amount - fee - creator_fee` on a sell (buyback is a share *of* `fee`).
* PumpSwap `BuyEvent`/`SellEvent`: `pool_*_token_reserves` are *before* the trade and
  pricing uses effective quote reserves `pool_quote + virtual_quote_reserves` (signed).
  The LP fee stays in the pool; protocol/creator fees never count as liquidity.
"""

from __future__ import annotations

from ..models import AMM, CURVE, Completion, Launch, Migration, Normalized, Trade
from .programs import SOL_QUOTES, WSOL_MINT
from .tx import RawEvent, TxEvents


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def normalize(
    tx: TxEvents,
    pos: int = 0,
    known_pools: dict[str, tuple[str, str]] | None = None,
    foreign: bool = False,
) -> Normalized:
    """`foreign`: also return trades quoted in another token (DJT, wXRP, PUMP...) in `.foreign`,
    in that token's units. They never appear in `.trades`, which stays SOL-denominated."""
    # the instruction's own accounts are authoritative (some pools are quoted in another token, not SOL)
    known_pools = {**(known_pools or {}), **tx.pool_mints}
    out = Normalized([], [], [], [], {}, set())
    out.pools.update({p: known_pools[p] for p in tx.pool_mints})

    for ev in tx.events:
        name, f = ev.name, ev.fields
        if name == "CreateEvent":
            out.launches.append(_launch(tx, f))
        elif name == "TradeEvent":
            t = _curve_trade(tx, ev, pos)
            if t is not None:
                out.trades.append(t)
            elif foreign:
                out.foreign.append(_foreign_curve_trade(tx, ev, pos))
        elif name == "PostCompleteBuyEvent":
            t = _post_complete_trade(tx, ev, pos)
            if t is not None:
                out.trades.append(t)
        elif name == "CompleteEvent":
            if f.get("quote_mint", WSOL_MINT) in SOL_QUOTES:
                out.completions.append(Completion(f["mint"], tx.slot, f.get("timestamp") or tx.block_time or 0, tx.signature))
        elif name == "CompletePumpAmmMigrationEvent":
            if f.get("quote_mint", WSOL_MINT) in SOL_QUOTES:
                out.migrations.append(
                    Migration(f["mint"], f["pool"], tx.slot, f.get("timestamp") or tx.block_time or 0, tx.signature)
                )
                known_pools[f["pool"]] = (f["mint"], WSOL_MINT)
                out.pools[f["pool"]] = (f["mint"], WSOL_MINT)
        elif name == "CreatePoolEvent":
            out.pools[f["pool"]] = (f["base_mint"], f["quote_mint"])
            known_pools[f["pool"]] = (f["base_mint"], f["quote_mint"])

    # PumpSwap trades need the pool -> mint mapping, which may come from this tx's
    # instruction accounts, a CreatePool/Migration event, or the caller's cache.
    for ev in tx.events:
        if ev.name in ("BuyEvent", "SellEvent"):
            pool = ev.fields["pool"]
            mapping = known_pools.get(pool)
            if mapping is None:
                out.unresolved_pools.add(pool)
                continue
            base_mint, quote_mint = mapping
            if quote_mint not in SOL_QUOTES:
                if foreign:
                    t = _amm_trade(tx, ev, pos, base_mint)
                    t.quote_mint = quote_mint
                    out.foreign.append(t)
                continue
            out.trades.append(_amm_trade(tx, ev, pos, base_mint))
    return out


def _launch(tx: TxEvents, f: dict) -> Launch:
    return Launch(
        mint=f["mint"],
        user=f.get("user", ""),
        creator=f.get("creator") or f.get("user", ""),
        name=f.get("name", ""),
        symbol=f.get("symbol", ""),
        uri=f.get("uri", ""),
        bonding_curve=f.get("bonding_curve", ""),
        slot=tx.slot,
        ts=f.get("timestamp") or tx.block_time or 0,
        sig=tx.signature,
        token_program=f.get("token_program", ""),
        mayhem=bool(f.get("is_mayhem_mode", False)),
        holder_reward=bool(f.get("is_holder_reward", False)),
        quote_mint=f.get("quote_mint", ""),
    )


def _curve_trade(tx: TxEvents, ev: RawEvent, pos: int) -> Trade | None:
    f = ev.fields
    if f.get("quote_mint", WSOL_MINT) not in SOL_QUOTES:
        return None  # USDC / pump-coin paired curve: not SOL-denominated
    return _curve_trade_from(tx, ev, pos, int(f["sol_amount"]), int(f["virtual_sol_reserves"]))


def _curve_trade_from(tx: TxEvents, ev: RawEvent, pos: int, quote: int, q_after: int) -> Trade:
    f = ev.fields
    fee = int(f.get("fee") or 0) + int(f.get("creator_fee") or 0)
    fee_bps = int(f.get("fee_basis_points") or 0) + int(f.get("creator_fee_basis_points") or 0)
    return Trade(
        sig=tx.signature,
        ev=ev.index,
        slot=tx.slot,
        pos=pos,
        ts=f.get("timestamp") or tx.block_time or 0,
        mint=f["mint"],
        venue=CURVE,
        user=f["user"],
        is_buy=bool(f["is_buy"]),
        quote=quote,
        base=int(f["token_amount"]),
        fee=fee,
        fee_bps=fee_bps,
        q_after=q_after,
        b_after=int(f["virtual_token_reserves"]),
        r_base_after=int(f.get("real_token_reserves") or 0),
        ix=f.get("ix_name") or ev.ix_name,
    )


def _foreign_curve_trade(tx: TxEvents, ev: RawEvent, pos: int) -> Trade:
    """A curve quoted in another token: same shape as a SOL trade, amounts in quote-token units."""
    f = ev.fields
    t = _curve_trade_from(tx, ev, pos, int(f.get("quote_amount") or 0), int(f.get("virtual_quote_reserves") or 0))
    t.quote_mint = f["quote_mint"]
    return t


def _post_complete_trade(tx: TxEvents, ev: RawEvent, pos: int) -> Trade | None:
    """The pool part of a synthetic-migration buy (v3 buy that empties the curve)."""
    f = ev.fields
    if f.get("quote_mint", WSOL_MINT) not in SOL_QUOTES:
        return None
    base_out = int(f["base_out"])
    if base_out <= 0:
        return None
    fee = int(f.get("fee") or 0) + int(f.get("creator_fee") or 0)
    fee_bps = int(f.get("fee_basis_points") or 0) + int(f.get("creator_fee_basis_points") or 0)
    return Trade(
        sig=tx.signature,
        ev=ev.index,
        slot=tx.slot,
        pos=pos,
        ts=f.get("timestamp") or tx.block_time or 0,
        mint=f["mint"],
        venue=AMM,
        user=f["user"],
        is_buy=True,
        quote=int(f["quote_in"]),
        base=base_out,
        fee=fee,
        fee_bps=fee_bps,
        q_after=int(f["pool_quote_reserves_after"]),
        b_after=int(f["pool_base_reserves_after"]),
        ix="post_complete_buy",
    )


def _amm_trade(tx: TxEvents, ev: RawEvent, pos: int, mint: str) -> Trade:
    f = ev.fields
    base_before = int(f["pool_base_token_reserves"])
    quote_before = int(f["pool_quote_token_reserves"]) + int(f.get("virtual_quote_reserves") or 0)
    lp_fee = int(f.get("lp_fee") or 0)
    fee = lp_fee + int(f.get("protocol_fee") or 0) + int(f.get("coin_creator_fee") or 0)
    fee_bps = (
        int(f.get("lp_fee_basis_points") or 0)
        + int(f.get("protocol_fee_basis_points") or 0)
        + int(f.get("coin_creator_fee_basis_points") or 0)
    )
    if ev.name == "BuyEvent":
        base = int(f["base_amount_out"])
        # Constant-product quote for the tokens received; exact for every buy flavour.
        quote = _ceil_div(quote_before * base, base_before - base) if base_before > base else int(f["quote_amount_in"])
        q_after, b_after = quote_before + quote + lp_fee, base_before - base
        is_buy = True
    else:
        base = int(f["base_amount_in"])
        quote = int(f["quote_amount_out"])
        q_after, b_after = quote_before - quote + lp_fee, base_before + base
        is_buy = False
    return Trade(
        sig=tx.signature,
        ev=ev.index,
        slot=tx.slot,
        pos=pos,
        ts=f.get("timestamp") or tx.block_time or 0,
        mint=mint,
        venue=AMM,
        user=f["user"],
        is_buy=is_buy,
        quote=quote,
        base=base,
        fee=fee,
        fee_bps=fee_bps,
        q_after=q_after,
        b_after=b_after,
        ix=f.get("ix_name") or ev.ix_name,
    )
