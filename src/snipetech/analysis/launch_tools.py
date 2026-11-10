"""Launch tools by the mark they leave in a create transaction (owner, 10 Nov: "there's a tool called proxima that
people deploy through, and when they do that the launches are usually organised and go high ... is there a way for it
to automatically detect proxima?").

A deploy tool shows in the launch itself, whatever it is called:

- `fee:<wallet>`: a small SOL transfer from the signer to a wallet that is not a known tip account (RM, 6 Nov: 0.0025
  SOL to `moonKfft...`); transfers above `FEE_MAX_SOL` are funding, not a fee;
- `program:<address>`: a top-level program other than pump.fun and the standard ones (a wrapper: OKTech's Launch
  shape reads the same thing);
- `payer:<wallet>`: the wallet that signed and paid when it is not the creator (a tool or a team launching for fresh
  creator wallets);
- `alt:<address>`: an address lookup table the create loads (Proxima, 10 Nov: the owner's coins carry no fee, no
  wrapper and their own creator's signature; the table `Hyif6e...` is all the tool leaves in the transaction).

`tool_table` groups launches by mark and says how far each mark's coins ran on the tape: the share that traded, that
reached 30 SOL in the curve and that graduated, the median peak, and the price change from a block-late entry (the end
of the slot after the create, where a launch snipe lands) to each horizon. A mark seen fewer than `min_launches` times
is not a tool. The marks whose coins run are the ones to target; `coin_marks` reads the coins the owner names, and the
mark they share says which tool is which.
A batch job: one getTransaction per launch (never the live database: the tape copy)."""

from __future__ import annotations

import logging
from bisect import bisect_right
from collections.abc import Callable, Iterable
from statistics import median
from typing import Any

from ..solana.normalize import normalize
from ..solana.tx import extract_from_transaction
from ..solana.txmeta import PROGRAM_LABELS, _account_keys, extract_tx_meta

log = logging.getLogger(__name__)
LAMPORTS = 1_000_000_000
VIRTUAL_SOL = 30.0  # a pump.fun curve's virtual SOL reserve before any buy
FEE_MAX_SOL = 0.05
RAN_SOL = 30.0
GRADUATED_SOL = 84.0
SLOT_SECS = 0.4


def tx_marks(tx: dict, creator: str) -> list[str]:
    """The marks of one create transaction (`getTransaction`, json encoding), sorted."""
    meta = extract_tx_meta(tx)
    keys = _account_keys(tx)
    programs = {keys[ix["programIdIndex"]] for ix in tx["transaction"]["message"].get("instructions", [])}
    marks = {f"program:{p}" for p in programs if p not in PROGRAM_LABELS}
    marks |= {f"fee:{t['dst']}" for t in meta.transfers if 0 < t["lamports"] <= FEE_MAX_SOL * LAMPORTS}
    if meta.fee_payer and meta.fee_payer != creator:
        marks.add(f"payer:{meta.fee_payer}")
    marks |= {f"alt:{t['accountKey']}" for t in tx["transaction"]["message"].get("addressTableLookups") or []}
    return sorted(marks)


async def _first_signature(rpc, mint: str, max_pages: int) -> str | None:
    """The oldest successful transaction that names the mint: its create. None when there is none within `max_pages`
    pages of 1,000 (a coin traded more than that is read from the tape or not at all)."""
    oldest = cursor = None
    for _ in range(max_pages):
        page = await rpc.get_signatures(mint, before=cursor)
        if not page:
            return oldest
        cursor = page[-1]["signature"]
        ok = [s["signature"] for s in page if not s.get("err")]
        oldest = ok[-1] if ok else oldest
    return None


async def coin_marks(rpc, store, mints: Iterable[str], *, max_pages: int = 500) -> dict[str, Any]:
    """The marks of the coins the owner names, one row a coin in the order given, and `shared`: the marks every coin
    found carries (the tool's own). A coin on the tape is read from its recorded create; another from the first
    transaction that named its mint, its creator from that transaction's create event (its signer when unreadable)."""
    coins: list[dict[str, Any]] = []
    for mint in mints:
        launch = store.launch(mint)
        row: dict[str, Any] = {"mint": mint, "symbol": launch.symbol if launch else None, "on_tape": bool(launch),
                               "sig": None, "creator": None, "marks": [], "error": None}  # fmt: skip
        coins.append(row)
        sig = launch.sig if launch else await _first_signature(rpc, mint, max_pages)
        tx = (await rpc.get_transactions([sig])).get(sig) if sig else None
        if tx is None:
            row["error"] = "no create transaction found" if sig is None else "create transaction not returned"
            continue
        row["sig"] = sig
        try:
            creator = (launch.creator or launch.user) if launch else None
            if creator is None:
                made = [x for x in normalize(extract_from_transaction(tx, sig)).launches if x.mint == mint]
                creator = (made[0].creator or made[0].user) if made else _account_keys(tx)[0]
                row["symbol"] = made[0].symbol if made else None
            row["creator"] = creator
            row["marks"] = tx_marks(tx, creator)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            row["error"] = f"unreadable: {exc}"
    found = [set(c["marks"]) for c in coins if c["sig"] and not c["error"]]
    return {"coins": coins, "shared": sorted(set.intersection(*found)) if found else []}


def _outcome(launch, trades: list, horizons: tuple[int, ...]) -> dict[str, Any]:
    slots: list[int] = []
    prices: list[float] = []
    peak = 0.0
    for t in trades:
        if t.venue != "curve":
            continue
        b, q = float(t.b_after or 0), float(t.q_after or 0)
        if b <= 0 or q <= 0:
            continue
        peak = max(peak, q / LAMPORTS - VIRTUAL_SOL)
        if slots and slots[-1] == int(t.slot):
            prices[-1] = q / b
        else:
            slots.append(int(t.slot))
            prices.append(q / b)

    def price_at(slot: int) -> float | None:
        i = bisect_right(slots, slot) - 1
        return prices[i] if i >= 0 else None

    entry = price_at(int(launch.slot) + 1)
    rets = {h: (price_at(int(launch.slot) + h) / entry - 1.0) * 100.0 for h in horizons} if entry else None
    return {"traded": bool(slots), "peak": peak, "rets": rets}


def _row(mark: str, items: list[tuple[Any, dict]], horizons: tuple[int, ...], fee_pct: float) -> dict[str, Any]:
    n = len(items)
    scored = [o["rets"] for _, o in items if o["rets"]]
    row: dict[str, Any] = {
        "mark": mark,
        "launches": n,
        "creators": len({(launch.creator or launch.user) for launch, _ in items}),
        "traded": round(sum(o["traded"] for _, o in items) / n, 3),
        "reached_30": round(sum(o["peak"] >= RAN_SOL for _, o in items) / n, 3),
        "graduated": round(sum(o["peak"] >= GRADUATED_SOL for _, o in items) / n, 3),
        "peak_sol": round(median(o["peak"] for _, o in items), 2),
        "scored": len(scored),
    }
    for h in horizons:
        vals = sorted(r[h] for r in scored)
        row[f"ret_{h}"] = round(sum(vals) / len(vals), 2) if vals else None
        row[f"med_{h}"] = round(median(vals), 2) if vals else None
        row[f"win_{h}"] = round(sum(v > fee_pct for v in vals) / len(vals), 3) if vals else None
    best = sorted(items, key=lambda it: -it[1]["peak"])[:3]
    row["examples"] = [{"mint": launch.mint, "symbol": getattr(launch, "symbol", None), "peak_sol": round(o["peak"], 1)}
                       for launch, o in best]  # fmt: skip
    return row


def tool_table(launch_marks: Iterable[tuple[Any, list[str]]], trades_of: Callable[..., list], *, min_launches: int = 5,
               horizons: tuple[int, ...] = (13, 150), fee_pct: float = 2.5) -> dict[str, Any]:  # fmt: skip
    """Launches grouped by mark (a launch with none is under "none"), marks seen `min_launches` times or more, the ones
    whose coins ran most first. Returns in percent from the end of the slot after the create, gross of `fee_pct`."""
    by: dict[str, list[tuple[Any, dict]]] = {}
    every: list[tuple[Any, dict]] = []
    for launch, marks in launch_marks:
        item = (launch, _outcome(launch, trades_of(launch.mint, int(launch.ts)) or [], horizons))
        every.append(item)
        for mark in marks or ["none"]:
            by.setdefault(mark, []).append(item)
    rows = [_row(m, items, horizons, fee_pct) for m, items in by.items() if len(items) >= min_launches]
    rows.sort(key=lambda r: (-r["reached_30"], -r["launches"], r["mark"]))
    return {"launches": len(every), "min_launches": min_launches, "horizons": list(horizons), "fee_pct": fee_pct,
            "all": _row("all", every, horizons, fee_pct) if every else None, "marks": rows}  # fmt: skip


async def launch_tools(rpc, store, now: float, *, hours: float = 12.0, max_launches: int = 20_000,
                       min_launches: int = 5, chunk: int = 200) -> dict[str, Any]:  # fmt: skip
    """`tool_table` over the launches of the last `hours` (the newest `max_launches`), each one's create transaction
    fetched from the RPC. Launches too fresh for the longest horizon are left out."""
    horizons = (13, 150)
    end = int(now - max(horizons) * SLOT_SECS - 60)
    launches = store.launches_between(int(now - hours * 3600), end)[-max_launches:]
    pairs: list[tuple[Any, list[str]]] = []
    missing = 0
    for i in range(0, len(launches), chunk):
        part = launches[i : i + chunk]
        txs = await rpc.get_transactions([launch.sig for launch in part])
        for launch in part:
            tx = txs.get(launch.sig)
            if tx is None:
                missing += 1
                continue
            try:
                pairs.append((launch, tx_marks(tx, launch.creator or launch.user)))
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                log.warning("launch-tools: %s unreadable: %s", launch.sig, exc)
                missing += 1
    out = tool_table(pairs, store.trades_for_mint, min_launches=min_launches, horizons=horizons)
    out.update({"hours": hours, "window_launches": len(launches), "not_fetched": missing})
    return out
