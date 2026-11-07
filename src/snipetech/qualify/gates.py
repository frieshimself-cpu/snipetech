"""Pure gate functions. Each answers one question about a wallet from the tape or its API history;
`verdict_for` turns the answers into a verdict row with a status and a review date."""

from __future__ import annotations

import statistics
import time
from typing import Any

from ..analysis.stats import summarize
from ..config import QualifyConfig
from ..store import Store

PUMP_VENUES = ("curve", "amm")  # pump.fun's bonding curve and PumpSwap; launchlab is Raydium's
HISTORY_ORDER = ("solanatracker", "solanatracker30", "birdeye", "vybe")
# what the book needs from the lab to copy a wallet the way it was scored (analysis/lab.py rank_wallets)
COPY_PLAN_KEYS = ("min_leader_sol", "rebuy_share", "leader_first_sell_p50_s", "leader_one_tx_exit_share",
                  "leader_biggest_sell_p50", "leader_style", "exit_name", "exit_params", "lab_version")  # fmt: skip
# the lab's choice of the wallet's own exit from the menu (rank_wallets with a gate): exit_name / exit_params above are
# then the exit its sniper should run, and these say why (book/policy.exit_for sends it only when the owner allows)
EXIT_CHOICE_KEYS = ("exit_source", "exit_menu", "exit_candidate", "exit_test_n", "exit_gain_sol", "exit_gain_lower")


def copy_plan(lab_row: dict | None) -> dict[str, Any] | None:
    """The lab row's copy plan (None without a lab row). A row without a per-wallet floor still gets one, its fields
    None: a verdict with a lab number always carries a plan, so store.candidates never mistakes it for a verdict of the
    old lab and queues it again and again. The exit choice comes along when the lab made one."""
    if not lab_row:
        return None
    return {k: lab_row.get(k) for k in COPY_PLAN_KEYS} | {k: lab_row[k] for k in EXIT_CHOICE_KEYS if k in lab_row}


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


# A wallet with this share of its first buys or more in a coin's create slot is not copied. Research round 2 (7 Nov):
# the 14:00 search's rows 7zvP2x and 3yrJE1 were artifacts, 10 of 10 and 13 of 20 of their first buys in the create
# slot (the block the coin was made in, full of the creator's bundle), where no copy can land: the lab scored moments
# that live copies never get.
MAX_CREATE_SLOT_SHARE = 0.10


def create_slot_share(store: Store, wallet: str, since_ts: int) -> dict[str, Any] | None:
    """How many of the wallet's first buys since `since_ts` sat in the coin's create slot: {"in_create", "first_buys",
    "share"}, over the first buys the qualify lab scores (lab.KOL_MIN_SOL, 0.1 SOL, or more) in coins whose create the
    tape recorded (elsewhere the create slot is unknown), never in a coin the wallet created (its buy there is the
    creator's, which the lab never scores). None when there is no such first buy."""
    from ..analysis.lab import KOL_MIN_SOL

    rows = store._q(
        "SELECT t.mint, t.slot, t.quote, l.slot, l.user, l.creator FROM trades t JOIN launches l ON l.mint = t.mint "
        "WHERE t.user=? AND t.is_buy=1 AND t.ts>=? ORDER BY t.mint, t.slot, t.pos, t.ev",
        (wallet, int(since_ts)),
    )
    first: dict[str, tuple] = {}
    for r in rows:
        first.setdefault(r[0], r)  # the earliest buy per coin: its first buy there
    kept = [r for r in first.values() if wallet not in (r[4], r[5]) and r[2] >= KOL_MIN_SOL * 1e9]
    if not kept:
        return None
    n = sum(1 for _mint, slot, _q, created, *_ in kept if int(slot) == int(created))
    return {"in_create": n, "first_buys": len(kept), "share": round(n / len(kept), 4)}


# The forward check. Research round 2 (7 Nov): the 27 wallets the verdicts and the 14:00 search rated positive lost
# -0.0147 SOL a copy on the tape recorded after their verdicts (257 copies, lower bound -0.0225, 4 of 5 windows
# negative), while the same method made +0.063 a copy (37) in the 6 h before the 14:30 verdicts: the verdicts caught hot
# streaks that then stopped, and 16 of 24 verdict wallets never bought again within 9-13 h. So a wallet every
# in-sample gate passed qualifies only once it also passes on its own copies on the tape after it was first picked: at
# least FORWARD_MIN_N of them (the copies the in-sample gate asks of a wallet without a strong history) over at least
# FORWARD_MIN_HOURS of later tape, judged by the in-sample gate's own rule (a lower bound above 0 passes, a mean below 0
# retires, anything between waits). Those later copies spread about 0.15 SOL a copy (257 at -0.0147, lower bound
# -0.0225): on a mean above 0 alone such a loser would pass 20 copies one time in three, on the lower bound about one
# time in ten. Until it passes or fails it is "forward pending".
FORWARD_MIN_N = 20
FORWARD_MIN_HOURS = 6.0


def tape_hours_after(spans, since: float, now: float) -> float:
    """Hours of recorded tape between `since` and `now`: the spans the lab scored, holes left out."""
    return sum(max(0.0, min(float(b), float(now)) - max(float(a), float(since))) for a, b in spans) / 3600.0


def forward_gate(v: dict[str, Any], moments: list | None, picked_at: int | None, spans, cfg: QualifyConfig,
                 now: float, conf: float = 0.8, samples: int = 2000) -> dict[str, Any]:  # fmt: skip
    """The verdict after the forward check, for a verdict every in-sample gate passed (status qualified; any other is
    returned as it is). `moments`: the wallet's copies the lab scored, [[the leader's buy ts, SOL a copy made], ...];
    `picked_at`: when it was first picked (qualify.run.first_pick), None = now; `spans`: the tape the lab read. Its
    copies after the pick are its forward copies, and `forward` holds their numbers: {"picked_at", "tape_hours", "n",
    "mean", "lower", "win_rate", "status", "min_n", "min_hours"}.

    The bar (FORWARD_MIN_N, FORWARD_MIN_HOURS): with fewer than 20 forward copies or under 6 h of tape after the pick,
    "pending": the verdict is a candidate the book does not arm, every number of it the lab's, re-checked as often as
    a qualified wallet (review_qualified_h). With both, the in-sample gate's rule on the forward copies: their lower
    bound above 0 keeps it qualified ("passed"); their mean below 0 retires it ("failed": it lost after it was picked);
    between the two it stays pending. Means and bounds count to the lamport. The reason says which, with its numbers."""
    if v.get("status") != "qualified":
        return v
    since = int(now if picked_at is None else picked_at)
    later = [float(p) for ts, p in (moments or []) if float(ts) > since]
    hours = round(tape_hours_after(spans or [], since, now), 2)
    st = summarize(later, conf, samples) if later else {}
    mean = st.get("mean")
    fwd = {"picked_at": since, "tape_hours": hours, "n": len(later), "mean": mean, "lower": st.get("ev_lower"),
           "win_rate": st.get("win_rate"), "status": "pending", "min_n": FORWARD_MIN_N, "min_hours": FORWARD_MIN_HOURS}  # fmt: skip
    out = dict(v, forward=fwd, reasons=list(v.get("reasons") or []))
    when = time.strftime("%d %b %H:%M UTC", time.gmtime(since))
    waits = {"status": "candidate", "gate": "forward", "next_review": int(now) + cfg.review_qualified_h * 3600}
    if len(later) < FORWARD_MIN_N or hours < FORWARD_MIN_HOURS:
        out |= waits
        out["reasons"].append(f"forward pending: {len(later)} later copies (needs {FORWARD_MIN_N}) over {hours:.1f} h of "
                              f"tape since it was picked on {when} (needs {FORWARD_MIN_HOURS:g} h)")  # fmt: skip
    elif round(float(mean), 9) < 0:
        fwd["status"] = "failed"
        out |= {"status": "retired", "gate": "forward", "next_review": int(now) + cfg.review_retired_h * 3600}
        out["reasons"].append(f"loses after it was picked: mean {mean:+.4f} SOL over {len(later)} later copies since "
                              f"{when}")  # fmt: skip
    elif round(float(fwd["lower"]), 9) <= 0:
        out |= waits
        out["reasons"].append(f"forward pending: mean {mean:+.4f} SOL over {len(later)} later copies since {when}, its "
                              f"lower bound {fwd['lower']:+.4f} not above 0 yet")  # fmt: skip
    else:
        fwd["status"] = "passed"
        out["reasons"].append(f"holds after it was picked: mean {mean:+.4f} SOL over {len(later)} later copies since "
                              f"{when}")  # fmt: skip
    return out


def score(lower_sol: float | None, n: int | None, venue: float | None, followers_1s: float | None) -> float:
    if lower_sol is None:
        return 0.0
    n_factor = min(1.0, (n or 0) / 30.0)
    v = 1.0 if venue is None else venue
    crowd = max(0.2, 1.0 - (followers_1s or 0.0) / 10.0)
    return float(lower_sol) * n_factor * v * crowd


def verdict_for(wallet: str, hist: dict, lab_row: dict | None, venue: float | None, crowd: dict, rel_ok: bool,
                rel_reasons: list[str], cfg: QualifyConfig, now: float,
                create: dict | None = None) -> dict[str, Any]:  # fmt: skip
    """The verdict row. Order of the gates: the vetoes (relationships, then the create slot: `create`, from
    create_slot_share, MAX_CREATE_SLOT_SHARE or more of its first buys there; each one that applies is said), then
    venue, then history, then the tape at the landing, then crowding; whatever stops the wallet sets the status and the
    review date."""
    passes, strong, hist_reasons = history_gate(hist, cfg)
    n = int(lab_row["n"]) if lab_row and lab_row.get("n") is not None else 0
    mean = _num(lab_row.get("mean")) if lab_row else None
    lower = _num(lab_row.get("ev_lower")) if lab_row else None
    sc = score(lower, n, venue, crowd.get("followers_1s")) if lab_row else 0.0
    reasons: list[str] = []
    status, gate = "qualified", None  # gate: the one that set the status (keep_armed reads it)
    in_create = bool(create) and float(create.get("share") or 0) >= MAX_CREATE_SLOT_SHARE
    if not rel_ok or in_create:
        status, gate = "retired", "create_slot" if rel_ok else "relationship"
        reasons = [] if rel_ok else list(rel_reasons)
        if in_create:
            reasons.append(f"{create['in_create']} of {create['first_buys']} first buys ({float(create['share']):.0%}) "
                           f"in the coin's create slot, where no copy lands ({MAX_CREATE_SLOT_SHARE:.0%} or more is "
                           "not copied)")  # fmt: skip
    elif lab_row is None:
        status, gate, reasons = "candidate", "lab", ["no tape data"]
    elif venue is not None and venue < cfg.min_venue_share:
        status, gate = "retired", "venue"
        reasons = [f"venue share {venue:.0%} < {cfg.min_venue_share:.0%} (trades mostly off pump.fun)"]
    elif passes is False:
        status, gate, reasons = "retired", "history", ["history: " + "; ".join(hist_reasons)]
    else:
        if passes is None:
            reasons.extend(hist_reasons)  # noted, not a veto: the tape decides
        need = cfg.min_n_tape_strong if strong else cfg.min_n_tape
        if n < need:  # how many copies, the same at any landing
            status, gate = "candidate", "sample"
            reasons.append(f"n {n} < {need} at {cfg.landing_pos}")
        elif mean is not None and mean < 0:  # what they made where they land
            status, gate = "retired", "tape"
            reasons.append(f"loses at {cfg.landing_pos}: mean {mean:+.4f} SOL over {n}")
        elif lower is None or lower <= 0:
            status, gate = "candidate", "tape"
            reasons.append(f"lower bound {lower if lower is None else round(lower, 4)} <= 0 at {cfg.landing_pos}")
        elif crowd.get("gap3_pct") is not None and crowd["gap3_pct"] > cfg.max_gap3_pct:
            status, gate = "candidate", "crowding"
            reasons.append(f"crowded: gap to the 3rd follower {crowd['gap3_pct']:.1f} % > {cfg.max_gap3_pct:g} %")
    hours = {"qualified": cfg.review_qualified_h, "candidate": cfg.review_candidate_h, "retired": cfg.review_retired_h}[
        status
    ]
    return {"wallet": wallet, "computed_at": int(now), "landing_pos": cfg.landing_pos, "n_tape": n, "mean_sol": mean, "lower_sol": lower,
            "hist_days": int(hist["days"]) if hist.get("days") is not None else None, "hist_realized_usd": hist.get("realized_usd"),
            "hist_win_rate": hist.get("win_rate"), "hist_trades": hist.get("trades"), "crowding": crowd.get("followers_1s"),
            "venue_share": venue, "score": sc, "status": status, "next_review": int(now) + hours * 3600, "reasons": reasons,
            "exit": lab_row.get("exit") if lab_row else None, "gap3_pct": crowd.get("gap3_pct"),
            "copy_plan": copy_plan(lab_row), "create_slot": create, "gate": gate}  # fmt: skip


# The gates these rules added or changed (research round 2, 7 Nov): what a copy makes where it lands ("tape": the lab
# moved from the in-slot +6 to +3x), first buys in a create slot, and the forward check. A wallet the book copied before
# them is never stopped by them (keep_armed); every other gate stops it as it always did.
NEW_RULE_GATES = frozenset({"tape", "create_slot", "forward"})
# what a kept verdict says of the new evaluation (`kept_armed["new"]`)
KEPT_NEW_FIELDS = ("status", "landing_pos", "n_tape", "mean_sol", "lower_sol", "score", "reasons")
_NOT_KEPT = ("id", "computed_at", "next_review", "reasons", "forward", "create_slot", "kept_armed", "gate")


def armed_before_rules(prev: dict[str, Any] | None) -> bool:
    """The wallet's latest verdict is one the book may have armed it on before these rules: qualified, with a copy plan
    (the book books nothing else), and either from before them (no forward check) or already kept by keep_armed. Only
    such a wallet is kept while the book copies it."""
    if not prev or prev.get("status") != "qualified" or not prev.get("copy_plan"):
        return False
    return prev.get("forward") is None or bool(prev.get("kept_armed"))


def keep_armed(v: dict[str, Any], prev: dict[str, Any] | None, armed: bool, cfg: QualifyConfig,
               now: float) -> dict[str, Any]:  # fmt: skip
    """The verdict to write for a wallet a copy sniper copies now (`armed`), given its latest verdict before this run
    (`prev`). The book arms only qualified wallets and drops any other; a wallet it copied before these rules (its last
    verdict qualified under the old ones, with a copy plan, or already kept) is not stopped by them: the owner decides
    (research round 2, 7 Nov: keep 7aPo3n armed as a live measurement, judge it at 30 copies).

    When one of NEW_RULE_GATES stops such a wallet, the verdict written is the one the book armed it on (status,
    score, copy plan, landing and numbers, as the book reads them), reviewed as a qualified one, its only reason that it
    no longer qualifies and why; `kept_armed` holds when it was armed on, the score it was armed on ("armed_score"), and
    the new evaluation ("new"). Its forward and create-slot numbers are the new ones. Passing these rules, it gets its
    own verdict with the mark ("kept": False), so it stays kept should it fall back under them while it is copied, but
    never a score under the one it was armed on: the book ranks on the score, and these rules usually score a wallet
    lower where copies land (+3x) than the old ones did at +6, so its own lower score under the book's bar would have
    the book stop it (review of 7 Nov: 0.0018 against the bar's 0.005). Then the verdict says so, and "new_score" is its
    own. Any other wallet, one an old gate stops (the bundle rule, venue, history, copies, crowding) and one no sniper
    copies any more: `v` as it is."""
    if not armed or not armed_before_rules(prev):  # nothing copies it, or it qualified under these rules: they apply
        return v
    mark = prev.get("kept_armed")
    armed_on = int(mark["armed_on"]) if mark else int(prev["computed_at"])
    armed_score = float((mark or {}).get("armed_score", prev.get("score")) or 0.0)
    if v.get("status") == "qualified":
        out = dict(v, kept_armed={"armed_on": armed_on, "armed_score": armed_score, "kept": False})
        own = float(v.get("score") or 0.0)
        if own < armed_score:
            said = (f"ranked at the score it was armed on ({armed_score:.4f}) while the book copies it: these rules score "
                    f"it {own:.4f}; whether to keep copying it, the owner decides")  # fmt: skip
            out["score"], out["reasons"] = armed_score, [*list(v.get("reasons") or []), said]
            out["kept_armed"]["new_score"] = own
        return out
    if v.get("gate") not in NEW_RULE_GATES:
        return v
    why = (v.get("reasons") or ["?"])[-1]  # the gate that stopped it says why last
    said = (f"no longer qualifies under the 7 Nov verdict rules ({why}): kept qualified because the book already copied "
            "it before these rules; whether to stop it, the owner decides")  # fmt: skip
    kept = {k: val for k, val in prev.items() if k not in _NOT_KEPT}
    return kept | {
        "computed_at": int(now), "next_review": int(now) + cfg.review_qualified_h * 3600, "reasons": [said],
        "forward": v.get("forward"), "create_slot": v.get("create_slot"),
        "kept_armed": {"armed_on": armed_on, "armed_score": armed_score, "kept": True,
                       "new": {k: v.get(k) for k in KEPT_NEW_FIELDS}},
    }  # fmt: skip
