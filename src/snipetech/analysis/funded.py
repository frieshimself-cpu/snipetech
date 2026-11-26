"""The big-deployer filter of the lab's launch rule (`Trigger.funded`, 26 Nov 2026): a launch counts only when its creator
(or the wallet that signed its create) was paid, before the create, by the creator of a coin that had already passed a
market-cap bar.

Why: the owner's "track funds moving through big deployer wallets and snipe" (24-25 Nov 2026): do wallets that the
creator of a big coin pays launch coins that pay at our landing? The first scripts for it lived in a scratch folder
and went with this session's container on 26 Nov; the question comes back as a filter of the lab's launch rule, so it
is scored at the launch landing with every exit and cost the other launch rules have, and it runs on the server.

No hindsight (the refuters of the first plan, workflow wf_a8b7015a-a49): a creator is big only from the moment one of
his coins first passed the bar, as the bought PumpSwap month records it (`history amm-peaks`, analysis/amm_peaks.py),
and that moment comes at least LEAD_SECS before the create, the payment (a `paid_by_dev` link, events/dev.py) at least
a second before it. The lead is the time a live watcher needs to see the crossing and add the creator to a Web
sniper's roots, which then reads the wallets he paid before (its preload); a payment he makes once he is a root
subscribes its wallet as it lands, so the payment needs no lead of its own. A coin that went big after the month's
last hour (23 Nov) makes no one big: the filter can miss a funded launch, never admit one by hindsight. What it cannot see: the links are what the dev-transfer scan had found when the lab ran, and
the scan reads a creator's payments only in the day around his launches that reached 60 SOL on their curve, some of
them later than the funded launch. A payment it holds was made before the create, but whether a live watcher would
have been following its payer then rests on the scan's choice of creators (never on the funded coin's own outcome).

OKTech's Web mode is the live form (it follows the wallets a root wallet pays and snipes their launches,
docs/oktech-features.md): not built and not tested, so a rule with the filter is research only (`FUNDED_WHY`)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from . import amm_peaks as ap

LINK_KIND = "paid_by_dev"
CHUNK = 400  # addresses one IN (...) read takes
# a creator counts as big for a launch only when his coin passed the bar at least this long before its create: the
# time to see the crossing (GMGN's lists, the tape) and add him to a Web sniper's roots, which then preload the
# wallets he paid. Not measured (no Web sniper of ours has run): five minutes, so that a follow-up coin launched
# minutes after its creator's big one, which no watcher of ours could have caught, is never counted
LEAD_SECS = 300
FUNDED_WHY = ("research only: it buys only launches whose creator was paid before the create by the creator of a big "
              "coin, which no sniper we build can tell (OKTech's Web mode follows the wallets a creator pays and is "
              "the live form: not built, not tested); a finding to learn from, never frozen, never sent")  # fmt: skip
NO_PEAKS = ("a launch rule with funded needs the month's market-cap crossings, and there is no file of them under "
            "history/pumpfundata/pump_amm/peaks: run `snipetech history amm-peaks` first (it reads the bought "
            "PumpSwap month under a lab slot, about 14 minutes)")  # fmt: skip


def bars_words(bars: Iterable[float] = ap.BARS_SOL) -> str:
    """The bars as a refusal names them: "1250, 6250, 12500 or 62500"."""
    names = [f"{b:g}" for b in bars]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def big_since(records: Iterable[Mapping[str, Any]], bars: Iterable[float],
              creator_of: Mapping[str, str] | None = None) -> dict[float, dict[str, float]]:  # fmt: skip
    """For each bar, every creator with a coin past it and when the first of his coins first passed it (the earliest
    over his coins and over the files). A record that names no creator (the vendor's system program, or none) takes the
    one the tape recorded for its coin (`creator_of`); with none there either, it makes no one big."""
    creator_of = creator_of or {}
    out: dict[float, dict[str, float]] = {float(b): {} for b in bars}
    for rec in records:
        who = rec.get("creator")
        if not who or who == ap.NO_CREATOR:
            who = creator_of.get(rec.get("mint") or "")
        if not who or who == ap.NO_CREATOR:
            continue
        for bar, since in out.items():
            t = rec.get(ap.bar_keys(bar)[1])
            if t is not None and float(t) < since.get(who, math.inf):  # NaN is never earlier
                since[who] = float(t)
    return out


def _chunks(items: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(items), CHUNK):
        yield items[i : i + CHUNK]


def tape_creators(store, mints: Iterable[str]) -> dict[str, str]:
    """The creator the tape's launches table records for each of these coins it holds."""
    out: dict[str, str] = {}
    for part in _chunks(sorted({m for m in mints if m})):
        marks = ",".join("?" * len(part))
        for mint, creator in store._q(f"SELECT mint, creator FROM launches WHERE mint IN ({marks})", part):
            if creator:
                out[mint] = creator
    return out


def paid_links(store, payers: Iterable[str]) -> dict[str, tuple[tuple[int, str], ...]]:
    """The wallets these creators paid (`paid_by_dev` links) and when each payer first paid each: {wallet: ((first_ts,
    payer), ...)} in time order, then by payer."""
    got: dict[str, list[tuple[int, str]]] = {}
    for part in _chunks(sorted(set(payers))):
        marks = ",".join("?" * len(part))
        sql = f"SELECT src, dst, first_ts FROM edges WHERE kind = ? AND src IN ({marks})"
        for src, dst, first in store._q(sql, [LINK_KIND, *part]):
            if dst and first is not None:
                got.setdefault(dst, []).append((int(first), src))
    return {dst: tuple(sorted(links)) for dst, links in got.items()}


def funded_by(launch, bars: Iterable[float], funders: Mapping[str, tuple[tuple[int, str], ...]],
              big: Mapping[float, Mapping[str, float]]) -> dict[float, str | None]:  # fmt: skip
    """For each bar, the big creator who funded this launch: of the creators whose coin had passed the bar at least
    LEAD_SECS before the create, the first to pay the coin's creator or the wallet that signed its create, at least a
    second before the create (None: nobody). `launch`: anything with `creator`, `user` and `ts`; None (the create is
    off the tape) is funded by nobody."""
    out: dict[float, str | None] = {float(b): None for b in bars}
    if launch is None:
        return out
    ts = int(launch.ts)
    own = {launch.creator, launch.user}
    paid = sorted({*funders.get(launch.creator or "", ()), *funders.get(launch.user or "", ())})
    for bar in out:
        since = big.get(bar) or {}
        out[bar] = next((src for first, src in paid
                         if first < ts and src not in own and since.get(src, math.inf) <= ts - LEAD_SECS),
                        None)  # fmt: skip
    return out


class Candidate(NamedTuple):
    """A launch on the tape whose creator or signer a big creator paid at some time (`candidate_launches`)."""

    mint: str
    creator: str
    user: str
    ts: int


def candidate_launches(store, spans: Iterable[tuple[float, float]], wallets: Iterable[str]) -> list[Candidate]:
    """The launches inside these stretches of tape (a launch rule fires on one no later than a minute before its
    stretch ends) whose creator or signer is one of `wallets` (the wallets big creators paid): the only coins a lab of
    funded rules alone needs to read. Each once, by time."""
    wallets = sorted(set(wallets))
    seen: dict[str, Candidate] = {}
    for a, b in spans:
        for part in _chunks(wallets):
            marks = ",".join("?" * len(part))
            sql = (f"SELECT mint, creator, user, ts FROM launches WHERE ts BETWEEN ? AND ? "
                   f"AND (creator IN ({marks}) OR user IN ({marks}))")  # fmt: skip
            for mint, creator, user, ts in store._q(sql, [int(a), int(b) - 60, *part, *part]):
                seen.setdefault(mint, Candidate(mint, creator or "", user or "", int(ts)))
    return sorted(seen.values(), key=lambda c: (c.ts, c.mint))


@dataclass(frozen=True)
class FundingWeb:
    """What the lab's funded filter reads, once a lab: the wallets big creators paid ({wallet: ((first_ts, payer),
    ...)}), each bar's big creators and since when, and what was read (`summary`, in the lab's result)."""

    funders: dict[str, tuple[tuple[int, str], ...]]
    big: dict[float, dict[str, float]]
    summary: dict[str, Any]


def funding_web(store, data: Path, bars: Iterable[float]) -> FundingWeb:
    """The creators past each bar and since when, from every file `history amm-peaks` wrote under the data folder
    (`data`), and the wallets they paid, from the store's links. A ValueError in words when there is no such file."""
    paths = ap.peaks_files(data)
    if not paths:
        raise ValueError(NO_PEAKS)
    bars = tuple(sorted({float(b) for b in bars}))
    records = ap.read_peaks(paths)
    nameless = [r.get("mint") for r in records if not r.get("creator") or r.get("creator") == ap.NO_CREATOR]
    big = big_since(records, bars, tape_creators(store, nameless) if nameless else {})
    funders = paid_links(store, set().union(*big.values()) if big else set())
    summary = {"peaks_files": [p.name for p in paths], "coins": len({r.get("mint") for r in records}),
               "big_creators": {f"{b:g}": len(big[b]) for b in bars}, "funded_wallets": len(funders),
               "links": sum(len(v) for v in funders.values())}  # fmt: skip
    return FundingWeb(funders=funders, big=big, summary=summary)
