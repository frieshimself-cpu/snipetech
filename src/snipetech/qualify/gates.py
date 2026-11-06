"""Pure gate functions. Each answers one question about a wallet from the tape or its API history;
`verdict_for` turns the answers into a verdict row with a status and a review date."""

from __future__ import annotations

import statistics
from typing import Any

from ..config import QualifyConfig
from ..store import Store

PUMP_VENUES = ("curve", "amm")  # pump.fun's bonding curve and PumpSwap; launchlab is Raydium's
HISTORY_ORDER = ("solanatracker", "solanatracker30", "birdeye", "vybe")
# what the book needs from the lab to copy a wallet the way it was scored (analysis/lab.py rank_wallets)
COPY_PLAN_KEYS = ("min_leader_sol", "rebuy_share", "leader_first_sell_p50_s", "leader_one_tx_exit_share",
                  "leader_biggest_sell_p50", "leader_style", "exit_name", "exit_params", "lab_version")  # fmt: skip


def copy_plan(lab_row: dict | None) -> dict[str, Any] | None:
    """The lab row's copy plan, or None when the row predates it (no per-wallet floor)."""
    if not lab_row or lab_row.get("min_leader_sol") is None:
        return None
    return {k: lab_row.get(k) for k in COPY_PLAN_KEYS}


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _pct(v) -> float | None:
    """Win rates arrive as a fraction (Birdeye, Vybe) or a percent (SolanaTracker); return a percent."""
    x = _num(v)
    if x is None:
        return None
    return x * 100.0 if x <= 1.0 else x


def normalise_history(candidate_stats: dict) -> dict[str, Any]:
    """One history dict from whichever source knows the wallet best: SolanaTracker 90 d, then 30 d, Birdeye, Vybe."""
    for src in HISTORY_ORDER:
        s = candidate_stats.get(src)
        if not isinstance(s, dict):
            continue
        if src == "birdeye":
            s = s.get("hist30") if isinstance(s.get("hist30"), dict) else s
            if not s or s.get("error"):
                continue
        trades = _num(s.get("trades"))
        if trades is None:
            continue
        realized = _num(s.get("realized_usd"))
        single = _num(s.get("max_single_pnl_usd"))
        share = (single / realized) if single is not None and realized else None
        if share is None and s.get("max_single_share") is not None:
            share = _num(s.get("max_single_share"))
        return {"source": src, "trades": int(trades), "win_rate": _pct(s.get("win_rate")), "realized_usd": realized,
                "max_single_share": share, "coins": _num(s.get("tokens") if s.get("tokens") is not None else s.get("unique_tokens")),
                "days": _num(s.get("trading_days") if s.get("trading_days") is not None else s.get("days"))}  # fmt: skip
    return {
        "source": None,
        "trades": None,
        "win_rate": None,
        "realized_usd": None,
        "max_single_share": None,
        "coins": None,
        "days": None,
    }


def history_gate(stats: dict, cfg: QualifyConfig) -> tuple[bool | None, bool, list[str]]:
    """(passes, strong, reasons). passes is None when no source has a history for the wallet."""
    trades = stats.get("trades")
    if trades is None:
        return None, False, ["no history from any source"]
    reasons: list[str] = []
    wr, realized, share, coins = (
        stats.get("win_rate"),
        stats.get("realized_usd"),
        stats.get("max_single_share"),
        stats.get("coins"),
    )
    if trades < cfg.min_hist_trades:
        reasons.append(f"history trades {trades} < {cfg.min_hist_trades}")
    if trades > cfg.max_hist_trades:
        reasons.append(f"history trades {trades} > {cfg.max_hist_trades} (bot pace)")
    if wr is None or wr < cfg.min_win_rate:
        reasons.append(f"win rate {wr if wr is None else round(wr, 1)} < {cfg.min_win_rate:g} %")
    if realized is None or realized < cfg.min_realized_usd:
        reasons.append(f"realized {realized if realized is None else round(realized)} $ < {cfg.min_realized_usd:g} $")
    if share is not None and share > cfg.max_single_share:
        reasons.append(f"single coin is {share:.0%} of the profit (> {cfg.max_single_share:.0%})")
    passes = not reasons
    strong = passes and wr is not None and wr >= cfg.strong_win_rate and (coins or 0) >= cfg.strong_coins
    return passes, bool(strong), reasons


def venue_share(store: Store, wallet: str, since_ts: int) -> float | None:
    """Share of the wallet's tape trades on pump.fun venues; None with fewer than 5 trades."""
    rows = store._q("SELECT venue, count(*) FROM trades WHERE user=? AND ts>=? GROUP BY venue", (wallet, int(since_ts)))
    total = sum(n for _, n in rows)
    if total < 5:
        return None
    return sum(n for v, n in rows if v in PUMP_VENUES) / total


def crowding(store: Store, wallet: str, since_ts: int, window_secs: int = 60) -> dict[str, Any]:
    """How crowded the wallet's entries are: per first buy in a coin, distinct other buyers within 1 s and the
    price gap between its buy and the 3rd following buy (within `window_secs`); medians over its buys."""
    firsts = store._q(
        "SELECT mint, min(ts) FROM trades WHERE user=? AND is_buy=1 AND ts>=? GROUP BY mint", (wallet, int(since_ts))
    )
    followers: list[float] = []
    gaps: list[float] = []
    for mint, t0 in firsts:
        trades = sorted(store.trades_for_mint(mint, int(t0) - 1, int(t0) + window_secs), key=lambda t: t.order)
        idx = next((i for i, t in enumerate(trades) if t.user == wallet and t.is_buy and t.ts >= t0), None)
        if idx is None:
            continue
        mine = trades[idx]
        after = [t for t in trades[idx + 1 :] if t.is_buy and t.user != wallet]
        followers.append(float(len({t.user for t in after if t.ts <= mine.ts + 1})))
        if len(after) >= 3 and mine.b_after and after[2].b_after:
            p0 = mine.q_after / mine.b_after
            p3 = after[2].q_after / after[2].b_after
            if p0 > 0:
                gaps.append((p3 / p0 - 1.0) * 100.0)
    return {"followers_1s": statistics.median(followers) if followers else None,
            "gap3_pct": statistics.median(gaps) if gaps else None, "n": len(followers)}  # fmt: skip


def _has_table(store: Store, name: str) -> bool:
    return bool(store._q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def bundle_launch_count(store: Store, wallet: str, limit: int = 50) -> int:
    """In how many launches the wallet was a bundle member (create-slot buyer paid, co-signed or funded by the team;
    without fingerprints every create-slot buyer counts, as the deployer analysis does)."""
    from ..analysis.launches import classify_insiders
    from ..sim.market import MarketPath

    mints = store._q(
        "SELECT DISTINCT t.mint FROM trades t JOIN launches l ON l.mint = t.mint "
        "WHERE t.user=? AND t.is_buy=1 AND t.slot = l.slot AND t.user <> l.user AND t.user <> l.creator LIMIT ?",
        (wallet, int(limit)),
    )
    n = 0
    for (mint,) in mints:
        launch = store.launch(mint)
        if launch is None:
            continue
        trades = store.trades_for_mint(mint, launch.ts - 2, launch.ts + 120)
        try:
            path = MarketPath.build(trades, launch)
            _, bundle, _ = classify_insiders(store, launch, path)
        except Exception:  # noqa: BLE001 - a malformed coin must not block the gate
            continue
        if wallet in bundle:
            n += 1
    return n


def relationship_gate(store: Store, wallet: str) -> tuple[bool, list[str]]:
    """False when the wallet is tied to dev teams: paid by a dev (edges table, phase 5) or a bundle member in >= 2 launches."""
    reasons: list[str] = []
    if _has_table(store, "edges"):
        try:
            rows = store._q("SELECT count(*) FROM edges WHERE dst=? AND kind='paid_by_dev'", (wallet,))
            if rows and rows[0][0]:
                reasons.append(f"paid_by_dev edge(s): {rows[0][0]}")
        except Exception:  # noqa: BLE001
            pass
    n = bundle_launch_count(store, wallet)
    if n >= 2:
        reasons.append(f"bundle member in {n} launches")
    return not reasons, reasons


def score(lower_sol: float | None, n: int | None, venue: float | None, followers_1s: float | None) -> float:
    if lower_sol is None:
        return 0.0
    n_factor = min(1.0, (n or 0) / 30.0)
    v = 1.0 if venue is None else venue
    crowd = max(0.2, 1.0 - (followers_1s or 0.0) / 10.0)
    return float(lower_sol) * n_factor * v * crowd


def verdict_for(wallet: str, hist: dict, lab_row: dict | None, venue: float | None, crowd: dict, rel_ok: bool,
                rel_reasons: list[str], cfg: QualifyConfig, now: float) -> dict[str, Any]:  # fmt: skip
    """The verdict row. Order of the gates: relationships veto, then venue, then history, then the tape at the landing,
    then crowding; whatever stops the wallet sets the status and the review date."""
    passes, strong, hist_reasons = history_gate(hist, cfg)
    n = int(lab_row["n"]) if lab_row and lab_row.get("n") is not None else 0
    mean = _num(lab_row.get("mean")) if lab_row else None
    lower = _num(lab_row.get("ev_lower")) if lab_row else None
    sc = score(lower, n, venue, crowd.get("followers_1s")) if lab_row else 0.0
    reasons: list[str] = []
    status = "qualified"
    if not rel_ok:
        status, reasons = "retired", list(rel_reasons)
    elif lab_row is None:
        status, reasons = "candidate", ["no tape data"]
    elif venue is not None and venue < cfg.min_venue_share:
        status, reasons = (
            "retired",
            [f"venue share {venue:.0%} < {cfg.min_venue_share:.0%} (trades mostly off pump.fun)"],
        )
    elif passes is False:
        status, reasons = "retired", ["history: " + "; ".join(hist_reasons)]
    else:
        if passes is None:
            reasons.extend(hist_reasons)  # noted, not a veto: the tape decides
        need = cfg.min_n_tape_strong if strong else cfg.min_n_tape
        if n < need:
            status = "candidate"
            reasons.append(f"n {n} < {need} at {cfg.landing_pos}")
        elif mean is not None and mean < 0:
            status = "retired"
            reasons.append(f"loses at {cfg.landing_pos}: mean {mean:+.4f} SOL over {n}")
        elif lower is None or lower <= 0:
            status = "candidate"
            reasons.append(f"lower bound {lower if lower is None else round(lower, 4)} <= 0 at {cfg.landing_pos}")
        elif crowd.get("gap3_pct") is not None and crowd["gap3_pct"] > cfg.max_gap3_pct:
            status = "candidate"
            reasons.append(f"crowded: gap to the 3rd follower {crowd['gap3_pct']:.1f} % > {cfg.max_gap3_pct:g} %")
    hours = {"qualified": cfg.review_qualified_h, "candidate": cfg.review_candidate_h, "retired": cfg.review_retired_h}[
        status
    ]
    return {"wallet": wallet, "computed_at": int(now), "landing_pos": cfg.landing_pos, "n_tape": n, "mean_sol": mean, "lower_sol": lower,
            "hist_days": int(hist["days"]) if hist.get("days") is not None else None, "hist_realized_usd": hist.get("realized_usd"),
            "hist_win_rate": hist.get("win_rate"), "hist_trades": hist.get("trades"), "crowding": crowd.get("followers_1s"),
            "venue_share": venue, "score": sc, "status": status, "next_review": int(now) + hours * 3600, "reasons": reasons,
            "exit": lab_row.get("exit") if lab_row else None, "gap3_pct": crowd.get("gap3_pct"),
            "copy_plan": copy_plan(lab_row)}  # fmt: skip
