"""The owner's summary after each post-mortem batch: the trips, the price move, what the fees and the duplicates cost,
the net, the biggest cause and the one change it suggests, in plain words and SOL.

Code sends it once the analyst's turn over those trips ran (operator/run.py), to Telegram as the round sends its other
code lines, and writes it to the notes. The numbers are measure/attribution.py's, never the model's: on 7 Nov the
post-mortem left the arithmetic to the model and the owner never saw a summary ("just make it understand why it lost
and how to do better", 22:05). The change is the one that would have saved the most on these trips, labelled with who
can make it; when the price itself took the most, no fee setting is the answer and the analyst's top idea of the turn
comes first. It reads and reports only: no secret, no key, no wallet balance (the owner: "no there's no sol checks idc
ab it"), nothing that changes a setting."""

from __future__ import annotations

import time
from typing import Any

from ..measure.attribution import break_down, levers, totals
from ..ops import redact
from . import learning
from .paths import OperatorPaths

WHO = {
    "strategist": "The strategist may make this change within its bounds.",
    "owner": "Needs your yes.",
    "oktech": "Needs your yes: it is OKTech's to change, an ask to them.",
}


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


def _day(tm: time.struct_time) -> str:
    return f"{tm.tm_mday} {time.strftime('%b', tm)}"


def _when(trips: list[dict]) -> str:
    """From the first buy to the last sell, e.g. 7 Nov 19:18-19:22 UTC."""
    ends = [int(t["sell_ts"]) for t in trips if t.get("sell_ts") is not None]
    starts = [int(t["buy_ts"]) for t in trips if t.get("buy_ts") is not None] or ends
    if not ends:
        return "recently"
    a, b = time.gmtime(min(starts)), time.gmtime(max(ends))
    if _day(a) == _day(b):
        return f"{_day(a)} {time.strftime('%H:%M', a)}-{time.strftime('%H:%M', b)} UTC"
    return f"{_day(a)} {time.strftime('%H:%M', a)} - {_day(b)} {time.strftime('%H:%M', b)} UTC"


def _every(n: int) -> str:
    return "The loss" if n == 1 else "Both losses" if n == 2 else f"All {n} losses"


def _losses(s: dict[str, Any]) -> str:
    """Which losses made money on the price and lost it to the costs, and which came from the price itself."""
    lost, to_costs, on_price = s["losses"], s["lost_to_costs"], s["lost_on_price"]
    if not lost:
        return ""
    if to_costs == lost:
        return f" {_every(lost)} made money on the price and lost it to the costs."
    if on_price == lost:
        return f" {_every(lost)} came from the price itself."
    parts = [f"{to_costs} made money on the price and lost it to the costs"] if to_costs else []
    parts += [f"{on_price} came from the price itself"] if on_price else []
    if lost - to_costs - on_price:
        parts.append(f"{lost - to_costs - on_price} could not be split (no trade on the tape)")
    return f" Of the {lost} losses, " + "; ".join(parts) + "."


def _costs(s: dict[str, Any]) -> str:
    venue = "the venue's fee" if set(s["venues"]) - {"curve"} else "pump.fun's fee"
    parts = [f"{venue} {s['venue_fee_sol']:.4f}"] if s["venue_fee_sol"] else []
    parts += [f"buy fees {s['buy_fees_sol']:.4f}"] if s["buy_fees_sol"] else []
    parts += [f"sell fees {s['sell_fees_sol']:.4f}"] if s["sell_fees_sol"] else []
    if s["duplicates_sol"]:
        n = s["duplicates"]
        count = f"{n} failed duplicate {_plural(n, 'sell')}" if n else "failed duplicate sells"
        parts.append(f"{count} {s['duplicates_sol']:.4f}")
    return f"Costs: {s['costs_sol']:.4f} SOL: " + ", ".join(parts) + "." if parts else "Costs: none."


def _cause(s: dict[str, Any]) -> str:
    if not s["causes"]:
        return "Biggest cause: none."
    c = s["causes"][0]
    if c["cause"] != "price":
        tail = " The venue sets it; no setting changes it." if c["cause"] == "venue_fee" else ""
        return f"Biggest cause: {c['words']} ({c['sol']:.4f} SOL).{tail}"
    line = f"Biggest cause: the price fell after we bought ({c['sol']:.4f} SOL)"
    w = s.get("worst_leader")
    if w:
        line += (f"; copies of {str(w['leader'])[:6]} lost {abs(w['price_sol']):.4f} on the price over {w['trips']} "
                 f"{_plural(w['trips'], 'trip')}")  # fmt: skip
    return line + "."


def _lever(prefix: str, lv: dict[str, Any]) -> str:
    return f"{prefix}: {lv['words']}: {lv['sol']:.4f} SOL on these trips. {WHO[lv['who']]}"


def _idea_label(idea: dict[str, Any]) -> str:
    """Who acts on the analyst's idea: the owner for anything flagged so (live tests and code always are), the
    strategist for a config change inside its bounds (its overlay refuses one outside them), the agents for research."""
    if idea.get("owner_needed"):
        return "needs your yes"
    if idea.get("kind") == "config":
        return "the strategist may make this change within its bounds"
    if idea.get("kind") == "research":
        return "the agents test it themselves"
    return "needs your yes"


def summary_text(trips: list[dict], cfg: Any = None, idea: dict[str, Any] | None = None) -> str | None:
    """The owner's lines for a batch of breakdowns (measure/attribution.py break_down), or None for none. `idea`: the
    analyst's top idea of the turn (learning.ideas_since), named first when the price itself took the most."""
    trips = [t for t in trips if t]
    if not trips:
        return None
    s = totals(trips)
    ranked = levers(trips, cfg=cfg)
    n = s["trips"]
    price = float(s["price_sol"] or 0.0) + float(s.get("unsplit_price_net_sol") or 0.0)
    unsplit = f" ({s['unsplit']} of them after the venue's fee: no trade on the tape)" if s["unsplit"] else ""
    lines = [
        f"Post-mortem: {n} live round {_plural(n, 'trip')}, {_when(trips)}: {s['wins']} won, {s['losses']} lost.",
        f"Price: {price:+.4f} SOL before any cost ({s['rose']} rose, {s['fell']} fell){unsplit}.",
        _costs(s),
        f"Net: {s['result_sol']:+.4f} SOL." + _losses(s),
        _cause(s),
    ]
    ours = next((x for x in ranked if x["who"] in ("strategist", "owner")), None)
    idea_line = f"Change it suggests: {idea.get('title') or idea.get('id')} ({_idea_label(idea)})." if idea else None
    change: list[str] = []
    if s["causes"] and s["causes"][0]["cause"] == "price":  # no fee setting fixes the price: the analyst's idea first
        change += [idea_line] if idea_line else []
        fee = ours or (ranked[0] if ranked else None)
        change += [_lever("On the fees", fee)] if fee else []
    elif ranked:
        change.append(_lever("Would have saved the most", ranked[0]))
        if ranked[0]["who"] == "oktech" and ours:
            change.append(_lever("Best change of ours", ours))
    elif idea_line:
        change.append(idea_line)
    lines += change or ["No change in our hands would have saved SOL on these trips; the post-mortem report says why."]
    return redact("\n".join(lines))


def send_summary(svc, cfg, paths: OperatorPaths, trips: dict[str, Any], notes, notifier, started: float) -> str | None:
    """Send the owner the summary of the trips one post-mortem reviewed (`trips` from learning.trips_since_postmortem:
    the book wallet's sells after since_ts up to newest_ts) and write it to the notes; the text, or None when there is
    nothing to say. `started`: when the turn started, for the ideas it added (learning.ideas_since)."""
    wallet = cfg.operator.wallet
    if not wallet or not trips.get("n") or trips.get("newest_ts") is None:
        return None
    since = int(trips["since_ts"]) if trips.get("since_ts") is not None else 0
    sells = [s for s in svc.store.fills(wallet=wallet, since_ts=since + 1, side="sell", limit=100_000)
             if int(s["ts"]) <= int(trips["newest_ts"])]  # fmt: skip
    parts = break_down(svc.store, wallet, sells, per_tx_sol=cfg.costs.per_tx_sol(),
                       lookback_days=cfg.measure.lookback_days)  # fmt: skip
    ideas = learning.ideas_since(paths, started)
    text = summary_text([b for b in parts.values() if b], cfg=cfg, idea=ideas[0] if ideas else None)
    if text is None:
        return None
    notes.write(text, "info")
    notifier.send(text)
    return text
