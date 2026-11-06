"""The book's policy as a pure function. Inputs in, a plan of actions out, every action with its reason.
Order: stops first (equity floor, day stop, daily risk), then the diff between the wanted set and the snipers that exist.
It never stops a sniper while the wallet holds a coin, and never creates or starts under a stop."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..config import BookConfig
from ..positions import SPENT_STATUSES, is_active

# the book creates its snipers through list_copy_sniper with label "book" and tag "arm:<arm>", so OKTech shows
# "snipetech copy book <wallet6> arm:<arm>"; measure.arm_for reads the same tag
BOOK_NAME_RE = re.compile(r"^snipetech copy book (?P<w6>\S+) arm:(?P<arm>[A-Za-z0-9_-]+)")


@dataclass
class BookInputs:
    equity_sol: float | None
    day_realised_sol: float
    day_risk_used_sol: float
    qualified: list[dict]  # latest verdicts with status qualified
    live: dict[str, dict]  # wallet_live rows by wallet
    bid_model: dict[str, dict]  # bid_model rows by arm
    snipers: list[dict]  # OKTech tasks: id, name, status
    holdings: list[dict]
    now: float
    stopped: bool = False  # the owner's /stop flag is set: kill mode until /resume
    switched_on: bool = True  # the owner's switch ([book] enabled or /book on); off = stop the book's own snipers
    holders: set[str] | None = None  # leaders (first 6 chars) whose buys the held coins copied; None = not traceable


@dataclass
class Action:
    kind: str  # create | start | stop | keep | skip | kill
    wallet: str | None
    arm: str | None
    fill_sol: float | None
    sniper_id: str | None
    reason: str


@dataclass
class BookPlan:
    actions: list[Action] = field(default_factory=list)
    kill_reason: str | None = None
    ranked: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def arm_fee_sol(arm: dict) -> float:
    return float(arm.get("priority") or 0.0) + float(arm.get("tip") or 0.0)


def fill_for(arm: dict, cfg: BookConfig) -> float | None:
    """The smallest fill that keeps the arm's fee at or under max_fee_share, inside [fill_min, fill_max]; None when impossible."""
    need = arm_fee_sol(arm) / cfg.max_fee_share if cfg.max_fee_share > 0 else cfg.fill_min_sol
    fill = max(cfg.fill_min_sol, need)
    if fill > cfg.fill_max_sol + 1e-12:
        return None
    return round(min(fill, cfg.fill_max_sol), 6)


def usable_arms(cfg: BookConfig) -> dict[str, float]:
    return {name: f for name, arm in cfg.bid_arms.items() if (f := fill_for(arm, cfg)) is not None}


def best_arm(bid_model: dict[str, dict], cfg: BookConfig, min_n: int = 30) -> str:
    """The arm that buys the best position once it has min_n fills; control until then."""
    usable = usable_arms(cfg)
    candidates = [(float(m.get("p75_pos")), name) for name, m in bid_model.items()
                  if name in usable and (m.get("n") or 0) >= min_n and m.get("p75_pos") is not None]  # fmt: skip
    if not candidates:
        return "control" if "control" in usable else (next(iter(usable)) if usable else "control")
    return min(candidates)[1]


def rank_wallets(qualified: list[dict], live: dict[str, dict], cfg: BookConfig) -> list[dict]:
    """Qualified wallets by live mean (after live_n fills) else paper score; below min_score dropped; top max_wallets."""
    rows = []
    for q in qualified:
        w = q.get("wallet")
        if not w:
            continue
        lv = live.get(w) or {}
        if (lv.get("n") or 0) >= cfg.live_n and lv.get("mean_sol") is not None:
            score, basis = float(lv["mean_sol"]), "live"
        else:
            score, basis = float(q.get("score") or 0.0), "paper"
        if score < cfg.min_score:
            continue
        rows.append({"wallet": w, "score": score, "basis": basis})
    rows.sort(key=lambda r: (-r["score"], r["wallet"]))
    return rows[: max(0, int(cfg.max_wallets))]


def assign_arms(ranked: list[dict], bid_model: dict[str, dict], cfg: BookConfig) -> dict[str, str]:
    """Deterministic: the best arm for most wallets, the alternative for the last ceil(explore_share × n) of the ranking."""
    usable = usable_arms(cfg)
    if not usable:
        return {}
    best = best_arm(bid_model, cfg)
    if best not in usable:
        best = next(iter(usable))
    others = [a for a in usable if a != best]
    n_explore = math.ceil(cfg.explore_share * len(ranked)) if others and cfg.explore_share > 0 else 0
    out = {}
    for i, r in enumerate(ranked):
        out[r["wallet"]] = others[0] if i >= len(ranked) - n_explore else best
    return out


def _book_snipers(snipers: list[dict]) -> list[dict]:
    out = []
    for s in snipers:
        m = BOOK_NAME_RE.match(str(s.get("name") or ""))
        if m:
            out.append({**s, "w6": m.group("w6"), "arm": m.group("arm")})
    return out


def plan_book(inp: BookInputs, cfg: BookConfig) -> BookPlan:
    plan = BookPlan()
    usable = usable_arms(cfg)
    for name, arm in cfg.bid_arms.items():
        if name not in usable:
            need = arm_fee_sol(arm) / cfg.max_fee_share if cfg.max_fee_share > 0 else 0
            plan.notes.append(f"{name} arm skipped: its fee {arm_fee_sol(arm):g} SOL needs a {need:.2f} SOL fill to stay under "
                              f"{cfg.max_fee_share:.0%}; fill_max_sol is {cfg.fill_max_sol:g}")  # fmt: skip
    holding = bool(inp.holdings)
    mine = _book_snipers(inp.snipers)

    def holds(s: dict) -> bool:
        """This sniper may be managing the wallet's coin: the coin's buy copied its leader, or the coin cannot be traced
        to a leader (then every book sniper counts as a holder: never strand a coin)."""
        return holding and (inp.holders is None or s["w6"] in inp.holders)

    # stops
    if inp.stopped:
        plan.kill_reason = "owner /stop"
    elif inp.equity_sol is None:
        plan.kill_reason = "equity unknown"
    elif inp.equity_sol < cfg.equity_floor_sol:
        plan.kill_reason = "equity below floor"
    elif inp.day_realised_sol <= -cfg.day_stop_sol:
        plan.kill_reason = "day stop"
    elif inp.day_risk_used_sol >= cfg.daily_risk_sol:
        plan.kill_reason = "daily risk used"
    elif not inp.switched_on and any(is_active(s.get("status")) for s in mine):
        plan.kill_reason = "book off"  # a running copy sniper keeps buying to its cap until it is stopped
    if plan.kill_reason:
        plan.actions.append(Action("kill", None, None, None, None, plan.kill_reason))
        for s in mine:
            if is_active(s.get("status")):
                if holds(s):
                    plan.actions.append(
                        Action(
                            "keep",
                            s["w6"],
                            s["arm"],
                            None,
                            s["id"],
                            f"{plan.kill_reason}, but the wallet holds a coin: never stop a holder",
                        )
                    )
                else:
                    plan.actions.append(Action("stop", s["w6"], s["arm"], None, s["id"], plan.kill_reason))
        return plan
    plan.ranked = rank_wallets(inp.qualified, inp.live, cfg)
    arms = assign_arms(plan.ranked, inp.bid_model, cfg)
    wanted = {r["wallet"][:6]: (r["wallet"], arms.get(r["wallet"])) for r in plan.ranked}
    seen_w6: set[str] = set()
    for s in mine:
        w6, status = s["w6"], str(s.get("status"))
        if w6 in wanted and wanted[w6][1] is not None:
            wallet, arm = wanted[w6]
            if status in ("running", "starting") and s["arm"] == arm:
                plan.actions.append(Action("keep", wallet, arm, None, s["id"], f"ranked and {status} on its arm"))
                seen_w6.add(w6)
            elif status in ("running", "starting") and s["arm"] != arm:
                plan.actions.append(
                    Action(
                        "keep", wallet, s["arm"], None, s["id"], f"ranked; arm changes to {arm} when this one is spent"
                    )
                )
                seen_w6.add(w6)
            elif status == "stopping":
                plan.actions.append(
                    Action("keep", wallet, s["arm"], None, s["id"], "stopping; replaced once it has stopped")
                )
                seen_w6.add(w6)
            elif status == "failed":
                # OKTech could not start it; recreating it every run would loop. Someone has to look at it.
                plan.actions.append(
                    Action(
                        "skip",
                        wallet,
                        s["arm"],
                        None,
                        s["id"],
                        "failed on OKTech: not recreated until someone looks at it",
                    )
                )
                plan.notes.append(
                    f"{wallet[:6]} sniper {s['id']} failed on OKTech; the book leaves it and does not recreate it"
                )
                seen_w6.add(w6)
            elif status in SPENT_STATUSES:
                plan.actions.append(Action("skip", wallet, s["arm"], None, s["id"], "spent; a new sniper replaces it"))
            elif status == "idle":
                plan.actions.append(Action("start", wallet, s["arm"], None, s["id"], "ranked and idle"))
                seen_w6.add(w6)
        else:
            if status == "stopping":
                plan.actions.append(Action("keep", w6, s["arm"], None, s["id"], "already stopping"))
            elif is_active(status):
                if holds(s):
                    plan.actions.append(
                        Action(
                            "keep",
                            w6,
                            s["arm"],
                            None,
                            s["id"],
                            "dropped out of the ranking, but the wallet holds a coin: never stop a holder",
                        )
                    )
                else:
                    plan.actions.append(Action("stop", w6, s["arm"], None, s["id"], "dropped out of the ranking"))
    for w6, (wallet, arm) in wanted.items():
        if w6 in seen_w6:
            continue
        if arm is None:
            plan.actions.append(Action("skip", wallet, None, None, None, "no bid arm can keep its fee under the limit"))
            continue
        plan.actions.append(
            Action(
                "create",
                wallet,
                arm,
                usable[arm],
                None,
                f"ranked #{[r['wallet'] for r in plan.ranked].index(wallet) + 1}",
            )
        )
    return plan
