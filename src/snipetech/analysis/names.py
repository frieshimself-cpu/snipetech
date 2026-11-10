"""What the owner's wallet tracker names say (Axiom export, 10 Nov: 2,630 wallets, "a lot of them I js track for fun or
are friends"). The list names many wallets of one team alike, 38 "Doji bundle", 31 "AtlasDrill", 46 "Faucet", "COMMUNITY
TT 8" beside "COMMUNITY Bundle 2", so "two known wallets are in" is often one team's bundle. Two readings of a name:

- `category`: the kind of wallet (own, infra, bundle, sniper, dev, insider, top, whale, kol, trader), first match wins;
- `entity`: the part of the name its wallets share, without numbers and words like side, dev, bundle, TT. A label our
  own tools wrote (cand:, found:, st:) or a name with nothing left is its own entity: the address.

A reading of free text the owner typed: it over-merges (every "Crime" is one entity) and misses typos ("Doju Bundle").
Used to count independent known wallets, never to decide a trade on its own."""

from __future__ import annotations

import re

_CATEGORIES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern, re.IGNORECASE))
    for kind, pattern in (
        ("own", r"^\s*(me|my\b.*)\s*$"),
        (
            "infra",
            (
                r"faucet|coinbase|bybit|bitget|gateio|kraken|mexc|^\s*binance\s*$|liquidity pool|meteora pool|"
                r"router|market ?maker"
            ),
        ),
        ("bundle", r"bund"),
        ("sniper", r"snip|snoip|frontr|fromtr|fronturn|scrap|kaching|quant|\bbot\b|copy ?trad"),
        ("dev", r"\bdev(s|v|vv|ver|vur)?\b|deploy"),
        ("insider", r"insider|isnider|cabal|crime|\brug|larp|scam|hack|launder|exploit"),
        ("top", r"\b(tt|th)\s?\d*\b"),
        ("whale", r"whale"),
        ("kol", r"\bkol\b"),
    )
)
_TOOL_LABEL = re.compile(r"^(cand|found|st|fomo|winner|wallet|kol|smart)\b\s*[:+\d]", re.IGNORECASE)
_MONEY = re.compile(r"^\$?[\d.,]+[km]?$", re.IGNORECASE)
_QUALIFIERS = frozenset(
    [
        "side",
        "sidee",
        "sidewallet",
        "dev",
        "devv",
        "devver",
        "bundle",
        "bunde",
        "alt",
        "new",
        "old",
        "main",
        "bags",
        "wallet",
        "wal",
        "walet",
        "tt",
        "th",
        "snipe",
        "sniper",
        "insider",
        "isnider",
        "cabal",
        "crime",
        "whale",
        "kol",
        "chinese",
        "challenge",
        "burner",
        "real",
        "og",
        "the",
        "friend",
        "funder",
        "launch",
        "doxxed",
        "fomo",
        "multi",
        "drained",
        "hacked",
        "confirmed",
        "dao",
        "deployer",
        "deploy",
        "early",
        "fresh",
    ]
)


def category(name: str) -> str:
    """The kind of wallet its tracker name describes; a plain name is a trader."""
    for kind, pattern in _CATEGORIES:
        if pattern.search(name or ""):
            return kind
    return "trader"


def entity(name: str, address: str = "") -> str:
    """The team a tracker name belongs to: the name without numbers and qualifiers, or `address` when nothing is left."""
    text = (name or "").strip().lower()
    if not text or _TOOL_LABEL.match(text):
        return address
    text = re.sub(r"^\d+\s*[|-]\s*", "", text)  # "1 | ohzarke side", "1 - cookerbruski"
    words = [w for w in text.split() if not _MONEY.match(w)]
    tokens = [re.sub(r"\d+$", "", t) for w in words for t in re.sub(r"[^a-z0-9]+", " ", w).split()]
    tokens = [t for t in tokens if t and not t.isdigit()]
    kept = [t for t in tokens if t not in _QUALIFIERS]
    return " ".join(kept or tokens) or address
