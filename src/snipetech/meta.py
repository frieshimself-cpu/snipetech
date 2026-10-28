"""Token metadata and social links: the launch tool, and the tweet / X account a token points at.

The metadata JSON a token's URI points to carries its socials. An X link to a status gives the
tweet id, and tweet ids embed their creation time (snowflake), so "launched N seconds after the
tweet" needs no X API. Engagement (likes, the author's followers) does need an X data source.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .store import Store

# pump.fun's own gateway serves what pump.fun pinned (fast); the others serve the rest of IPFS
IPFS_GATEWAYS = ("https://pump.mypinata.cloud/ipfs/", "https://4everland.io/ipfs/", "https://ipfs.filebase.io/ipfs/")
TWITTER_EPOCH_MS = 1288834974657
_IPFS = re.compile(r"(?:ipfs://|/ipfs/)([A-Za-z0-9]{46,})")
_STATUS = re.compile(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{6,25})", re.I)
_COMMUNITY = re.compile(r"(?:twitter\.com|x\.com)/i/communities/(\d+)", re.I)
_PROFILE = re.compile(r"(?:twitter\.com|x\.com)/(?!i/|search|home|intent)([A-Za-z0-9_]{1,15})/?(?:\?.*)?$", re.I)
_DEPLOYED = re.compile(r"(?:deployed|launched|created) (?:using|with|via|on) (https?://)?([\w.-]+)", re.I)


def resolve_uri(uri: str) -> list[str]:
    """URLs to try for a metadata URI. Public IPFS gateways (ipfs.io, dweb.link...) stopped serving
    files directly, so any CID is routed through gateways that still do."""
    m = _IPFS.search(uri or "")
    return [g + m.group(1) for g in IPFS_GATEWAYS] if m else [uri]


def _site(host: str) -> str:
    """metadata.j7tracker.io -> j7tracker.io"""
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host.lower()


def tweet_time(tweet_id: int | str) -> float:
    """Creation time (unix seconds) encoded in an X/Twitter status id."""
    return ((int(tweet_id) >> 22) + TWITTER_EPOCH_MS) / 1000


def _first(data: dict, *keys: str) -> str:
    ext = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    for k in keys:
        for src in (data, ext):
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def parse_socials(data: dict, uri: str = "") -> dict[str, Any]:
    """Social links, the X link's kind (tweet / community / profile) and the launch tool."""
    twitter = _first(data, "twitter", "x", "twitter_url")
    out: dict[str, Any] = {
        "twitter": twitter, "telegram": _first(data, "telegram", "tg"), "website": _first(data, "website", "web", "url"),
        "x_kind": None, "x_handle": None, "tweet_id": None, "tweet_ts": None,
        "description_len": len(data.get("description") or ""),
        "has_image": bool(data.get("image")),
    }  # fmt: skip
    if twitter:
        if m := _STATUS.search(twitter):
            out |= {"x_kind": "tweet", "x_handle": m.group(1).lower(), "tweet_id": m.group(2), "tweet_ts": tweet_time(m.group(2))}
        elif m := _COMMUNITY.search(twitter):
            out |= {"x_kind": "community", "x_community": m.group(1)}
        elif m := _PROFILE.search(twitter):
            out |= {"x_kind": "profile", "x_handle": m.group(1).lower()}
        else:
            out["x_kind"] = "other"
    host = urlparse(uri).netloc.lower()
    created = _first(data, "createdOn", "created_on", "platform")
    deployed = _DEPLOYED.search(data.get("description") or "")
    out["platform"] = _site(
        deployed.group(2) if deployed
        else host if host and not any(g in host for g in ("ipfs", "pinata", "arweave", "irys"))
        else urlparse(created).netloc or created or "ipfs"
    )  # fmt: skip
    return out


async def fetch_meta(store: Store, items: list[tuple[str, str]], concurrency: int = 8, timeout: float = 8.0) -> int:
    """Fetch and cache metadata for (mint, uri) pairs not fetched yet. Returns how many were fetched."""
    todo = [(m, u) for m, u in dict(items).items() if u and store.token_meta(m) is None]
    if not todo:
        return 0
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"}) as http:

        async def one(mint: str, uri: str) -> None:
            data = None
            async with sem:
                for url in resolve_uri(uri):
                    try:
                        r = await http.get(url)
                        data = r.json() if r.status_code == 200 else None
                    except (httpx.HTTPError, ValueError):
                        data = None
                    if isinstance(data, dict):
                        break
            if isinstance(data, dict):
                store.put_token_meta(mint, uri, True, parse_socials(data, uri))
            else:
                store.put_token_meta(mint, uri, False, {"platform": _site(urlparse(uri).netloc) or None})

        await asyncio.gather(*(one(m, u) for m, u in todo))
    return len(todo)


WSOL = "So11111111111111111111111111111111111111112"


async def ensure_assets(store: Store, rpc_url: str, mints: list[str], max_age: int = 3600) -> None:
    """Symbol, decimals and USD price of quote assets (and SOL), via the DAS `getAsset` method
    (Helius and other DAS-enabled RPCs). Used to value trades quoted in tokens other than SOL."""
    now = time.time()
    todo = [m for m in dict.fromkeys([WSOL, *mints]) if not (a := store.asset(m)) or now - (a.get("updated") or 0) > max_age]
    if not todo:
        return
    async with httpx.AsyncClient(timeout=20) as http:
        for m in todo:
            try:
                r = await http.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": "getAsset", "params": {"id": m}})
                res = r.json().get("result") or {}
            except (httpx.HTTPError, ValueError):
                continue
            info = res.get("token_info") or {}
            md = (res.get("content") or {}).get("metadata") or {}
            store.put_asset(m, info.get("symbol") or md.get("symbol") or "", info.get("decimals"),
                            (info.get("price_info") or {}).get("price_per_token"))  # fmt: skip
