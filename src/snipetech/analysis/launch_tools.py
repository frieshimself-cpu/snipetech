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

import asyncio
import logging
from bisect import bisect_right
from collections.abc import Callable, Iterable
from statistics import median
from typing import Any

from ..solana.base58 import b58decode
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


COMPUTE_BUDGET = "ComputeBudget111111111111111111111111111111"
SYSTEM = "11111111111111111111111111111111"
FUNDING_READ = "fund:read"  # kept with a launch's marks once its creator's funding was looked up; never a tool
TEAM_READ = "team:read"  # the same for the buyers in its create block
TEAM_MAX = 6  # a Jito bundle is five transactions: the create and four buys; a little room for a second bundle


def funding_mark(tx: dict) -> str | None:
    """`fund:cu<limit>` for a transaction that is one SOL transfer and one setComputeUnitLimit and nothing else: how a
    launch tool's relay funds a wallet (Proxima: limit 1450, from a one-off wallet, minutes before the launch). A
    wallet's own transfer estimates its limit or sets a price too; any other transaction gives None."""
    keys = _account_keys(tx)
    limits: list[int] = []
    transfers = 0
    for ix in tx["transaction"]["message"].get("instructions", []):
        program, data = keys[ix["programIdIndex"]], b58decode(ix["data"])
        if program == COMPUTE_BUDGET and data[:1] == b"\x02":
            limits.append(int.from_bytes(data[1:5], "little"))
        elif program == SYSTEM and int.from_bytes(data[:4], "little") == 2:
            transfers += 1
        else:
            return None
    return f"fund:cu{limits[0]}" if len(limits) == 1 and transfers == 1 else None


def _block_buyers(launch, trades: list) -> list[str]:
    """The other wallets that bought in the create's own block, in the order they landed, `TEAM_MAX` at most: the
    bundle a launch tool sends with the create (and any sniper that made the block; its funding says which it is)."""
    devs = {launch.creator, launch.user}
    out: list[str] = []
    for t in trades:
        if int(t.slot) != int(launch.slot):
            if int(t.slot) > int(launch.slot):
                break
            continue
        if t.is_buy and t.user not in devs and t.user not in out:
            out.append(t.user)
    return out[:TEAM_MAX]


async def _funding(rpc, wallet: str) -> str | None:
    """The funding mark of a wallet's first transaction; None for a wallet with a long history (not made for a launch)
    or whose first transaction is anything else."""
    try:
        sig = await _first_signature(rpc, wallet, 2)
        tx = (await rpc.get_transactions([sig])).get(sig) if sig else None
        return funding_mark(tx) if tx else None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("launch-tools: funding of %s unreadable: %s", wallet, exc)
        return None


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
    devs = {launch.creator, launch.user}
    dev_sol = block_sol = 0.0
    block: set[str] = set()
    for t in trades:
        if t.venue != "curve":
            continue
        if t.is_buy and int(t.slot) == int(launch.slot):
            if t.user in devs:
                dev_sol += t.quote / LAMPORTS
            else:
                block_sol += t.quote / LAMPORTS
                block.add(t.user)
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
    return {"traded": bool(slots), "peak": peak, "rets": rets, "dev_sol": dev_sol, "block_sol": block_sol,
            "block_wallets": len(block)}  # fmt: skip


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


def _bucket(value: float, edges: tuple[float, ...], names: tuple[str, ...]) -> str:
    return names[bisect_right(edges, value)]


# what happened in the create's own block: how many other wallets bought, how much they put in, how much the dev did
SPLITS: dict[str, tuple[tuple[float, ...], tuple[str, ...]]] = {
    "block_wallets": ((0.5, 1.5, 3.5), ("0", "1", "2-3", "4+")),
    "block_sol": ((1e-9, 2.0, 5.0, 10.0), ("0", "<2", "2-5", "5-10", "10+")),
    "dev_sol": ((1e-9, 0.5, 1.0, 2.0), ("0", "<0.5", "0.5-1", "1-2", "2+")),
}


def _has(field: str) -> Callable[[dict | None], str]:
    return lambda meta: "unknown" if not meta or not meta.get("ok", True) else ("yes" if meta.get(field) else "no")


def _x_link(meta: dict | None) -> str:
    return "unknown" if not meta or not meta.get("ok", True) else str(meta.get("x_kind") or "none")


# what the coin's metadata links to (`meta.parse_meta`): a website, the kind of X link, a Telegram
LINK_SPLITS: dict[str, tuple[Callable[[dict | None], str], tuple[str, ...]]] = {
    "website": (_has("website"), ("yes", "no", "unknown")),
    "x_link": (_x_link, ("tweet", "profile", "community", "other", "none", "unknown")),
    "telegram": (_has("telegram"), ("yes", "no", "unknown")),
}


def mark_split(launch_marks: Iterable[tuple[Any, list[str]]], trades_of: Callable[..., list], mark: str, *,
               horizons: tuple[int, ...] = (13, 150), fee_pct: float = 2.5, max_coins: int = 300,
               meta_of: Callable[[str], dict | None] | None = None, min_block_sol: float = 0.0) -> dict[str, Any]:  # fmt: skip
    """One mark's launches split by what happened in the create's own block (`SPLITS`), each bucket scored like a
    `tool_table` row, and `coins`: its newest `max_coins` launches one by one. A broad tool's mark is on junk and on
    organised launches alike (Proxima's table, 10 Nov: one launch in nine); the block's buying tells them apart, and a
    launch snipe a block later has it in view. `min_block_sol` keeps only launches where others put that much in the
    create block; with `meta_of` (the coin's stored metadata) the launches are also split by what the coin links to
    (`LINK_SPLITS`: website, X link, Telegram; "unknown" where the metadata was never fetched)."""
    items = [(launch, _outcome(launch, trades_of(launch.mint, int(launch.ts)) or [], horizons))
             for launch, marks in launch_marks if mark in marks]  # fmt: skip
    items = [it for it in items if it[1]["block_sol"] >= min_block_sol]
    by: dict[str, list[dict[str, Any]]] = {}
    if meta_of is not None:
        metas = {launch.mint: meta_of(launch.mint) for launch, _ in items}
        for key, (label, names) in LINK_SPLITS.items():
            groups: dict[str, list[tuple[Any, dict]]] = {}
            for item in items:
                groups.setdefault(label(metas[item[0].mint]), []).append(item)
            order = [n for n in names if n in groups] + sorted(n for n in groups if n not in names)
            by[key] = [{"bucket": r.pop("mark"), **r} for r in (_row(n, groups[n], horizons, fee_pct) for n in order)]
    for key, (edges, names) in SPLITS.items():
        groups: dict[str, list[tuple[Any, dict]]] = {}
        for item in items:
            groups.setdefault(_bucket(item[1][key], edges, names), []).append(item)
        rows = [_row(name, groups[name], horizons, fee_pct) for name in names if name in groups]
        by[key] = [{"bucket": r.pop("mark"), **r} for r in rows]
    coins = []
    for launch, o in sorted(items, key=lambda it: -int(it[0].ts))[:max_coins]:
        rets = o["rets"] or {}
        coins.append({"mint": launch.mint, "symbol": getattr(launch, "symbol", None), "ts": int(launch.ts),
                      "creator": launch.creator or launch.user, "dev_sol": round(o["dev_sol"], 3),
                      "block_wallets": o["block_wallets"], "block_sol": round(o["block_sol"], 3),
                      "peak_sol": round(o["peak"], 1),
                      **{f"ret_{h}": round(rets[h], 1) if h in rets else None for h in horizons}})  # fmt: skip
    return {"mark": mark, "launches": len(items), "min_block_sol": min_block_sol, "by": by, "coins": coins}


async def launch_tools(rpc, store, now: float, *, hours: float = 12.0, max_launches: int = 20_000,
                       min_launches: int = 5, chunk: int = 200, mark: str | None = None,
                       marks_store=None, funding_of: str | None = None,
                       min_block_sol: float = 0.0) -> dict[str, Any]:  # fmt: skip
    """`tool_table` over the launches of the last `hours` (the newest `max_launches`), each one's create transaction
    fetched from the RPC. Launches too fresh for the longest horizon are left out. With `mark`, also that mark's
    `mark_split` under "split". With `marks_store` (the live database: `store` is the read-only tape copy), a launch's
    marks are kept once read and only launches never read are fetched: a create never changes. With `funding_of` (a
    mark, and a `marks_store`), the creator of each launch carrying that mark has its first transaction read once
    (`funding_mark`), so the launches a tool's relay funded stand apart from the rest of a broad mark; the buyers in
    its create block are read the same way (`team:cu<limit>` when any of them was relay-funded)."""
    horizons = (13, 150)
    end = int(now - max(horizons) * SLOT_SECS - 60)
    launches = store.launches_between(int(now - hours * 3600), end)[-max_launches:]
    known = marks_store.launch_marks_of([launch.mint for launch in launches]) if marks_store is not None else {}
    todo = [launch for launch in launches if launch.mint not in known]
    missing = 0
    for i in range(0, len(todo), chunk):
        part = todo[i : i + chunk]
        txs = await rpc.get_transactions([launch.sig for launch in part])
        fresh: dict[str, list[str]] = {}
        for launch in part:
            tx = txs.get(launch.sig)
            if tx is None:
                missing += 1
                continue
            try:
                fresh[launch.mint] = tx_marks(tx, launch.creator or launch.user)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                log.warning("launch-tools: %s unreadable: %s", launch.sig, exc)
                missing += 1
        known.update(fresh)
        if marks_store is not None:
            marks_store.put_launch_marks(fresh)  # a chunk a write: short, and a scan cut off keeps what it read
    funded = [launch for launch in launches
              if funding_of and marks_store is not None and funding_of in known.get(launch.mint, ())
              and FUNDING_READ not in known[launch.mint]]  # fmt: skip
    for i in range(0, len(funded), 20):
        part = funded[i : i + 20]
        found = await asyncio.gather(*(_funding(rpc, launch.creator or launch.user) for launch in part))
        fresh = {
            launch.mint: [FUNDING_READ, *([mark] if mark else [])] for launch, mark in zip(part, found, strict=True)
        }
        marks_store.put_launch_marks(fresh)
        for mint, added in fresh.items():
            known[mint] = [*known[mint], *added]
    teams = [launch for launch in launches
             if funding_of and marks_store is not None and funding_of in known.get(launch.mint, ())
             and TEAM_READ not in known[launch.mint]]  # fmt: skip
    for i in range(0, len(teams), 10):
        part = teams[i : i + 10]
        buyers = [_block_buyers(launch, store.trades_for_mint(launch.mint, int(launch.ts)) or []) for launch in part]
        found = await asyncio.gather(*(_funding(rpc, wallet) for wallets in buyers for wallet in wallets))
        fresh, at = {}, 0
        for launch, wallets in zip(part, buyers, strict=True):
            got = {m for m in found[at : at + len(wallets)] if m}
            at += len(wallets)
            fresh[launch.mint] = [TEAM_READ, *sorted("team:" + m.split(":", 1)[1] for m in got)]
        marks_store.put_launch_marks(fresh)
        for mint, added in fresh.items():
            known[mint] = [*known[mint], *added]
    notes = (FUNDING_READ, TEAM_READ)
    pairs = [(launch, [m for m in known[launch.mint] if m not in notes]) for launch in launches if launch.mint in known]
    out = tool_table(pairs, store.trades_for_mint, min_launches=min_launches, horizons=horizons)
    out.update(
        {
            "hours": hours,
            "window_launches": len(launches),
            "not_fetched": missing,
            "fetched": len(todo) - missing,
            "funding_read": len(funded),
            "team_read": len(teams),
        }
    )
    if mark:
        metas = store
        if marks_store is not None:  # the metadata of the mark's launches that was never fetched, kept like the marks
            from ..meta import fetch_meta

            await fetch_meta(marks_store, [(launch.mint, launch.uri) for launch, found in pairs
                                           if mark in found and launch.uri])  # fmt: skip
            metas = marks_store
        out["split"] = mark_split(pairs, store.trades_for_mint, mark, horizons=horizons, meta_of=metas.token_meta,
                                  min_block_sol=min_block_sol)  # fmt: skip
    return out
