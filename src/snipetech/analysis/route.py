"""Who stands right behind a wallet's first buys, and through which route: the Jito bundle of the leader's buy and of
every other buyer within a few block places after it (the route check, roadmap 22 Nov 07:50).

22 Nov: another wallet's buy of 0.1 SOL or more stood within 3 block places after a big wallet's first buy on 65 % of
386 moments, and before it on 1. A buy sent once the leader's buy is seen lands dozens of places later (a block holds
a thousand transactions and more), so whoever stands right behind got there with the leader's transaction in some
bundle or route before it ran. How decides what is worth building: a place anyone can buy in Jito's auction has a
price and wants a bundle client; a seat in a bundle someone else builds around the leader's buy has to be bought from
that someone.

Jito's public explorer (EXPLORER: no key, no Helius credits) answers for a landed transaction the bundle it came in
(`/transaction/{sig}`; 404 "Bundle not found" when none: any other answer is unread) and for a bundle its
transactions in order, who paid its tip, the tip, its first place in the block and the validator (`/bundle/{id}`). It
keeps about a month. The first run (23 Nov, job 20261123-160910-658404) and an independent check of every row: on 103
of the 200 newest moments behind Cented, Decu, theo side and Yugi the closest buyer rode in the bundle that carried
the leader's buy, paying its tip; in the 91 bundles of three, the transaction between the two is the leader's own
landing service's (Axiom's or GMGN's: its fee is a fixed share of the leader's tip to that service), so those senders
build the bundles and the buyers pay to ride in them. The output keeps each bundle's transactions (`txs`) and where
the buyer stands in it (`in_bundle_after`), so whose the ones between are can be read.

What a buyer near the leader is (`KINDS`): "with the leader" (in the bundle that carried the leader's buy; `tipped_by`
says whether the buyer, the leader, one of the leader's companions or another wallet paid its tip), "own bundle" (a
bundle of its own, placed right after the leader's buy), "no bundle" (not through Jito: a validator's own scheduler,
or another relay), "unread" (the explorer gave no answer that can be read). The leader's own buy: "in a bundle" (some
bundle carried it: whose is in its transactions), "no bundle", "unread". A buyer under `near_min_sol` is no buyer; one
of the leader's own companions (chain_landing.known_mates; how many are known is in `mates`) is kept apart and not
looked up (CLAUDE.md rule 2: no copy lands between one operator's wallets).

The moments are the first buys `sends` takes (a leader's first buy of a coin of `min_sol` or more); those with a buyer
near are read newest first, `max_moments` at most (the explorer keeps a month, and its newest moments say what the
route is now), and when the request limit runs out the oldest are the ones left; the rest are said. A moment newer than EXPLORER_LAG_SECS may not be in the explorer yet."""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import Counter
from typing import Any

from .sends import _moments, _tape_end

EXPLORER = "https://bundles.jito.wtf/api/v1/bundles"
PLACES = 3  # block places after the leader's buy where a buyer stands right behind it (sends.NEAR_PLACES)
NEAR_MIN_SOL = 0.1  # the smallest buy that counts there (sends.NEAR_MIN_SOL)
DEFAULT_MAX_MOMENTS = 150
DEFAULT_MAX_REQUESTS = 800
GAP_SECS = 0.3  # between two requests: the explorer is public and keyless
RETRY_SECS = 3.0  # one refusal (429) is waited out this long, once
EXPLORER_LAG_SECS = 300.0  # a moment newer than this may not be in the explorer yet
MATES_DAYS = 7  # the leader's companions are read off this many days of the tape before the window
TOP = 15  # wallets listed in each table
LAMPORTS = 1e9
KINDS = ("with the leader", "own bundle", "no bundle", "unread")
NOT_FOUND = "Bundle not found"  # the explorer's answer for a transaction that came in no bundle (HTTP 404)
LEADER_KINDS = ("in a bundle", "no bundle", "unread")


class Unread(Exception):
    """The explorer gave no answer that can be read (an error, a refusal, the request limit): the lookup is unread."""


class Explorer:
    """Jito's public bundle explorer: the bundle a landed transaction came in (`bundle_of`: None when it came in none),
    and a bundle's transactions in order, who paid its tip, the tip, its first place in the block, the validator and
    the slot (`bundle`). One request at a time, `gap` seconds apart; a refusal (429) is waited out once (`retry`
    seconds); at most `max_requests` requests, every one counted (`requests`), and past that every lookup is Unread."""

    def __init__(self, http, *, max_requests: int = DEFAULT_MAX_REQUESTS, gap: float = GAP_SECS,
                 retry: float = RETRY_SECS, base: str = EXPLORER) -> None:  # fmt: skip
        self.http, self.max_requests, self.gap, self.retry, self.base = http, int(max_requests), gap, retry, base
        self.requests = 0
        self._last = 0.0

    async def _get(self, path: str) -> Any:
        for attempt in range(2):
            if self.requests >= self.max_requests:
                raise Unread(f"the request limit ({self.max_requests})")
            wait = self._last + self.gap - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.requests += 1
            self._last = time.monotonic()
            try:
                r = await self.http.get(f"{self.base}/{path}")
            except Exception as exc:
                raise Unread(f"{type(exc).__name__}") from exc
            if r.status_code == 429 and attempt == 0:
                await asyncio.sleep(self.retry)
                continue
            if r.status_code == 404:  # "none" only in the explorer's own words for it; any other 404 tells nothing
                try:
                    said = r.json()
                except ValueError:
                    said = None
                if isinstance(said, dict) and said.get("error") == NOT_FOUND:
                    return None
                raise Unread(f"HTTP 404 {str(said)[:80]}")
            if r.status_code != 200:
                raise Unread(f"HTTP {r.status_code}")
            try:
                return r.json()
            except ValueError as exc:
                raise Unread("an answer that is not JSON") from exc
        raise Unread("HTTP 429")  # pragma: no cover - the loop returns or raises on its second pass

    async def bundle_of(self, sig: str) -> str | None:
        """The bundle `sig` came in, None when the explorer says it came in none ("Bundle not found"); an answer that
        names no bundle (an empty list, an error) is Unread, never "none" (23 Nov check: it read as "no bundle")."""
        got = await self._get(f"transaction/{sig}")
        if got is None:
            return None
        first = got[0] if isinstance(got, list) and got else got
        bid = (first.get("bundle_id") or first.get("bundleId")) if isinstance(first, dict) else None
        if not bid:
            raise Unread(f"an answer that names no bundle: {str(got)[:80]}")
        return str(bid)

    async def bundle(self, bid: str) -> dict[str, Any]:
        got = await self._get(f"bundle/{bid}")
        b = (got[0] if isinstance(got, list) and got else got) if got else None
        if not isinstance(b, dict):
            raise Unread("no such bundle")
        return {"id": str(bid), "txs": [str(s) for s in b.get("txSignatures") or b.get("transactions") or []],
                "tippers": [str(t) for t in b.get("tippers") or []],
                "tip_sol": round(int(b.get("landedTipLamports") or 0) / LAMPORTS, 9),
                "block_index": b.get("blockIndex"), "validator": b.get("validator"), "slot": b.get("slot")}  # fmt: skip


def tipped_by(tippers: list[str], leader: str, buyer: str, mates: frozenset[str]) -> str:
    """Who paid the tip of the bundle that carried both the leader's buy and `buyer`'s: "the buyer" (it paid to ride
    in it; who built the bundle is in its transactions: 23 Nov, the leaders' own landing services), "the leader", "a
    companion of the leader", "another wallet" or "nobody named"."""
    if buyer in tippers:
        return "the buyer"
    if leader in tippers:
        return "the leader"
    if any(t in mates for t in tippers):
        return "a companion of the leader"
    return "another wallet" if tippers else "nobody named"


def _near(tape, m: dict[str, Any], places: int, near_min_sol: float, mates: frozenset[str]) -> tuple[list, list]:
    """The other wallets' buys of `near_min_sol` or more within `places` block places after the leader's (one per
    transaction, in block order), and its companions' apart."""
    floor = round(near_min_sol * LAMPORTS)
    seen: set[str] = set()
    near, companions = [], []
    rows = sorted((t for t in tape.trades_for_mint(m["mint"], m["ts"] - 2, m["ts"] + 2)
                   if t.is_buy and t.slot == m["slot"] and 0 < t.pos - m["pos"] <= places and t.user != m["leader"]
                   and t.sig != m["sig"] and int(t.quote) >= floor), key=lambda t: (t.pos, t.ev))  # fmt: skip
    for t in rows:
        if t.sig in seen:
            continue
        seen.add(t.sig)
        row = {"wallet": t.user, "place": int(t.pos - m["pos"]), "sol": round(int(t.quote) / LAMPORTS, 4)}
        if t.user in mates:
            companions.append(row)
        else:
            near.append({**row, "sig": t.sig})
    return near, companions


async def _read(explorer, m: dict[str, Any], near: list[dict], mates: frozenset[str], bundles: dict) -> dict:
    """Look up the leader's buy and each buyer near it; the row's route fields."""

    async def bundle(bid: str) -> dict[str, Any]:
        if bid not in bundles:
            bundles[bid] = await explorer.bundle(bid)
        return bundles[bid]

    lead_route, lead_bundle = "unread", None
    try:
        bid = await explorer.bundle_of(m["sig"])
        if bid is None:
            lead_route = "no bundle"
        else:
            lead_bundle = await bundle(bid)
            lead_route = "in a bundle"
    except Unread:
        pass
    out = []
    for n in near:
        kind, extra = "unread", {}
        if lead_bundle is not None and n["sig"] in lead_bundle["txs"]:
            kind = "with the leader"
        else:
            try:
                fb = await explorer.bundle_of(n["sig"])
                if fb is None:
                    kind = "no bundle"
                elif lead_bundle is not None and fb == lead_bundle["id"]:
                    kind = "with the leader"
                else:
                    b = await bundle(fb)
                    if m["sig"] in b["txs"]:  # the leader's own lookup failed, the buyer's bundle carries it
                        kind, lead_route, lead_bundle = "with the leader", "in a bundle", b
                    else:
                        kind, extra = "own bundle", {"bundle": _brief(b)}
            except Unread as exc:
                extra = {"why": str(exc)}
        if kind == "with the leader":
            txs = lead_bundle["txs"]
            after = (txs.index(n["sig"]) - txs.index(m["sig"])) if n["sig"] in txs and m["sig"] in txs else None
            extra = {"tipped_by": tipped_by(lead_bundle["tippers"], m["leader"], n["wallet"], mates),
                     "in_bundle_after": after}  # fmt: skip
        out.append({**n, "kind": kind, **extra})
    return {"leader_route": lead_route, "leader_bundle": _brief(lead_bundle) if lead_bundle else None, "near": out}


def _brief(b: dict[str, Any]) -> dict[str, Any]:
    """A bundle as the output keeps it: its transactions too, so whose the ones between the leader's and a buyer's
    are can be read (23 Nov: the leaders' own landing services')."""
    return {"id": b["id"], "n": len(b["txs"]), "txs": list(b["txs"]), "tippers": b["tippers"], "tip_sol": b["tip_sol"],
            "block_index": b["block_index"], "validator": b["validator"]}  # fmt: skip


def _table(rows: list[tuple[str, float | None]]) -> list[dict[str, Any]]:
    """Wallets by how often they stand there, with the median tip of their bundles when they have one."""
    n: Counter = Counter(w for w, _ in rows)
    tips: dict[str, list[float]] = {}
    for w, tip in rows:
        if tip is not None:
            tips.setdefault(w, []).append(tip)
    out = []
    for w, k in n.most_common(TOP):
        row: dict[str, Any] = {"wallet": w, "n": k}
        if w in tips:
            row["median_tip_sol"] = round(statistics.median(tips[w]), 9)
        out.append(row)
    return out


async def route(tape, explorer, leaders: list[str], now: float, *, hours: float = 72.0, min_sol: float = 1.9,
                places: int = PLACES, near_min_sol: float = NEAR_MIN_SOL, max_moments: int = DEFAULT_MAX_MOMENTS,
                mates: dict[str, frozenset[str] | None] | None = None) -> dict[str, Any]:  # fmt: skip
    """`tape`: the trades read (the hourly copy); `explorer`: Jito's explorer (`Explorer`); `mates`: each leader's
    companions (default: read off the tape, chain_landing.known_mates)."""
    end = min(_tape_end(tape, now), float(now) - EXPLORER_LAG_SECS)
    since = end - float(hours) * 3600
    moments = _moments(tape, list(leaders), since, end, min_sol)
    if mates is None:
        from ..measure.chain_landing import known_mates

        mates = {}
        for lead in dict.fromkeys(leaders):
            try:
                mates[lead] = known_mates(tape, lead, int(since - MATES_DAYS * 86400))
            except Exception:  # noqa: BLE001 - not known: nobody is kept apart for that leader
                mates[lead] = None
    rows: list[dict[str, Any]] = []
    for m in moments:
        near, comps = _near(tape, m, places, near_min_sol, frozenset(mates.get(m["leader"]) or ()))
        rows.append({"leader": m["leader"], "mint": m["mint"], "slot": m["slot"], "pos": m["pos"], "ts": m["ts"],
                     "sol": m["sol"], "sig": m["sig"], "near": near, "companions": comps})  # fmt: skip
    with_buyer = [r for r in rows if r["near"]]
    chosen = {id(r) for r in with_buyer[-max(0, int(max_moments)) :]} if max_moments > 0 else set()
    bundles: dict[str, dict] = {}
    stopped = None
    for r in reversed(rows):  # the newest first: the request limit leaves the oldest
        if not r["near"]:
            r.update(leader_route=None, leader_bundle=None, closest="nobody")
            continue
        limit = getattr(explorer, "max_requests", None)
        if id(r) not in chosen or (limit is not None and explorer.requests >= limit):
            if id(r) in chosen:
                stopped = f"the explorer's request limit ({limit})"
            r.update(leader_route=None, leader_bundle=None, closest="left")
            continue
        r.update(await _read(explorer, r, r["near"], frozenset(mates.get(r["leader"]) or ()), bundles))
        r["closest"] = r["near"][0]["kind"]
    read = [r for r in rows if r["closest"] not in ("nobody", "left")]
    left = sum(1 for r in rows if r["closest"] == "left")
    closest = Counter(r["closest"] for r in read)
    lead_routes = Counter(r["leader_route"] for r in read)
    tippers = Counter(r["near"][0]["tipped_by"] for r in read if r["closest"] == "with the leader")
    first = [r["near"][0] for r in read]
    backrunners = _table([(n["wallet"], r["leader_bundle"]["tip_sol"] if r["leader_bundle"] else None)
                          for r in read for n in r["near"] if n["kind"] == "with the leader"])  # fmt: skip
    own = _table([(n["wallet"], n["bundle"]["tip_sol"]) for r in read for n in r["near"] if n["kind"] == "own bundle"])
    outside = _table([(n["wallet"], None) for n in first if n["kind"] == "no bundle"])
    by_leader: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = by_leader.setdefault(r["leader"], {"moments": 0, "with_buyer": 0, "read": 0, **dict.fromkeys(KINDS, 0)})
        b["moments"] += 1
        b["with_buyer"] += bool(r["near"])
        if r["closest"] in KINDS:
            b["read"] += 1
            b[r["closest"]] += 1
    c = {k: closest.get(k, 0) for k in KINDS}
    lr = {k: lead_routes.get(k, 0) for k in LEADER_KINDS}
    paid = ", ".join(f"by {who} on {n}" for who, n in tippers.most_common())
    verdict = (f"Of {len(rows)} first buys of {min_sol:g} SOL or more by {len(set(leaders))} wallet(s) in {hours:g} h, "
               f"{len(with_buyer)} had another wallet's buy of {near_min_sol:g} SOL or more within {places} block "
               f"places after it (the leaders' own companions apart). Of the {len(read)} read, the closest such buyer "
               f"stood inside the bundle that carried the leader's buy on {c['with the leader']}"
               + (f" (its tip paid {paid})" if paid else "")
               + f", in a Jito bundle of its own on {c['own bundle']}, outside Jito on {c['no bundle']}"
               + (f"; {c['unread']} unread" if c["unread"] else "")
               + f". The leader's buy was inside some Jito bundle on {lr['in a bundle']} of the {len(read)} (who built "
               "a bundle is not told here: its transactions are in moments_list).")  # fmt: skip
    if left:
        verdict += (
            f" {left} moments with a buyer near were left unread"
            + (f" ({stopped})" if stopped else f" (--max-moments {max_moments}: the newest were read)")
            + "."
        )
    known = {lead: (len(mates[lead]) if mates.get(lead) is not None else None) for lead in dict.fromkeys(leaders)}
    return {"leaders": list(leaders), "mates": known, "hours": float(hours), "window": [int(since), int(end)],
            "min_sol": float(min_sol),
            "places": int(places), "near_min_sol": float(near_min_sol), "moments": len(rows),
            "with_buyer": len(with_buyer), "read": len(read), "left": left, "closest": c,
            "all_near": {k: sum(1 for r in read for n in r["near"] if n["kind"] == k) for k in KINDS},
            "leader_route": lr, "with_leader_tipped_by": dict(tippers), "backrunners": backrunners,
            "own_bundle": own, "outside": outside, "by_leader": by_leader,
            "requests": getattr(explorer, "requests", None), "verdict": verdict, "moments_list": rows}  # fmt: skip
