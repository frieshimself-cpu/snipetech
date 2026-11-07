"""The book's policy as a pure function. Inputs in, a plan of actions out, every action with its reason.
Order: stops first (equity floor, day stop, daily risk, the owner's wallet-wide guard), then the diff between the wanted
set and the snipers that exist. The book's own stops are the owner's /risk values where the owner set them (BookStops,
book/switch.book_stops): a stop the owner switched off never halts the book.
It never stops a sniper while the wallet holds a coin, and never creates or starts under a stop."""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field

from ..analysis.lab import EXIT_MIN_TEST
from ..config import BookConfig
from ..exits_menu import EXIT_MENU_VERSION, exit_family
from ..oktech import validate_exit
from ..positions import SPENT_STATUSES, is_active

# the book creates its snipers through list_copy_sniper with label "book" and tag "arm:<arm>", so OKTech shows
# "snipetech copy book <wallet6> arm:<arm>"; measure.arm_for reads the same tag
BOOK_NAME_RE = re.compile(r"^snipetech copy book (?P<w6>\S+) arm:(?P<arm>[A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class ExitChoice:
    """The exit a new book sniper gets. `params` None means the preset by name: lab, the very call the book always
    made. `source`: "fixed" ([book] exit_mode fixed), "lab" (per_wallet, lab kept, `reason` says why) or "wallet" (the
    wallet's own exit, `params` in the lab's form, `reason` with the held-out numbers that chose it)."""

    name: str
    params: dict | None
    source: str
    reason: str


FIXED_EXIT = ExitChoice("lab", None, "fixed", "lab for every wallet ([book] exit_mode fixed)")


def _plan_dict(copy_plan: dict | str | None) -> dict | None:
    if isinstance(copy_plan, str):
        try:
            copy_plan = json.loads(copy_plan)
        except ValueError:
            return None
    return copy_plan if isinstance(copy_plan, dict) else None


def _sol(x) -> str:
    return f"{float(x):+.3g}"


def exit_for(copy_plan: dict | str | None, cfg: BookConfig) -> ExitChoice:
    """The exit a wallet's next new sniper gets. With [book] exit_mode "fixed" (the default, and any other word): lab.
    With "per_wallet": the wallet's own exit from its verdict's copy plan only when the lab kept it on unseen moments
    of today's exit menu, its family is in [book] verified_exits and oktech.validate_exit finds nothing unsafe in it;
    otherwise lab, sent by name, with the reason in plain words. Only new snipers get it: a running one is never
    stopped because its verdict's exit changed."""
    if cfg.exit_mode != "per_wallet":
        return FIXED_EXIT
    plan = _plan_dict(copy_plan) or {}
    name, n, gain = plan.get("exit_name"), plan.get("exit_test_n"), plan.get("exit_gain_sol")

    def lab(why: str) -> ExitChoice:
        return ExitChoice("lab", None, "lab", why)

    if plan.get("exit_source") is None:
        return lab("no exit choice in its verdict")
    if plan["exit_source"] != "wallet" or not name or name == "lab":
        if plan.get("exit_candidate"):
            nums = f" ({_sol(gain)} SOL a trade, n {n})" if gain is not None else ""
            return lab(f"{plan['exit_candidate']} did not beat lab on its unseen trades{nums}")
        if n is not None and int(n) < EXIT_MIN_TEST:
            return lab(f"too few unseen trades ({int(n)}) to choose another exit")
        return lab("lab did best for it")
    if plan.get("exit_menu") != EXIT_MENU_VERSION:
        return lab(f"its exit {name} was chosen on another exit menu ({plan.get('exit_menu')})")
    if exit_family(name) not in set(cfg.verified_exits or ()):
        return lab(f"{name} has not been seen working live yet")
    params = plan.get("exit_params") if isinstance(plan.get("exit_params"), dict) else None
    problems = validate_exit(params)
    if problems or params is None:
        return lab(f"{name} cannot be sent: {'; '.join(problems)}")
    lower = plan.get("exit_gain_lower")
    why = f"its own: {_sol(gain or 0)} SOL a trade over lab on {n} unseen trades" + (
        f", at least {_sol(lower)}" if lower is not None else ""
    )
    return ExitChoice(str(name), dict(params), "wallet", why)


@dataclass(frozen=True)
class BookStops:
    """The book's own book-wide stops as they apply: the equity floor, the day stop and the daily cap of SOL bought,
    each None when off, and whose the floor and the day stop are ("config": the config file's [book] value; "owner": the
    owner's /risk, operator/RISK). The server's config file pins every default and changing one needs SSH, which the
    owner does not do (7 Nov), so the owner's word in operator/RISK wins, value by value. The daily cap halts the book
    for the day (it does not size a fill), so it follows the day stop's off: it is None only while the owner's day stop
    is off, never because the owner set the day stop to a number."""

    floor_sol: float | None
    day_stop_sol: float | None
    daily_risk_sol: float | None
    floor_from: str = "config"
    day_from: str = "config"

    @classmethod
    def pinned(cls, b: BookConfig) -> BookStops:
        """The config's [book] values, as the book always applied them."""
        return cls(float(b.equity_floor_sol), float(b.day_stop_sol), float(b.daily_risk_sol))


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
    other_copies: set[str] = field(default_factory=set)  # leaders an active sniper that is not the book's copies
    buys_by_sniper: dict[str, int] = field(default_factory=dict)  # book sniper id -> buys it has made (from the tape)
    # wallets paused on their live losses in earlier runs (coverage book:paused): reason, ts, lab_version
    paused: dict[str, dict] = field(default_factory=dict)
    # the tape shows a buy of ours not yet seen sold (it is ahead of the wallet read by seconds): counts as holding
    tape_holding: bool = False
    holdings_known: bool = False  # the wallet's holdings were read this run: retiring a sniper needs them, and none
    # the owner's wallet-wide guard holds (operator/risk_guard.py: the day 1 SOL down or the wallet under 10 SOL): a
    # kill like the book's own stops, so nothing is created or started, and with the wallet holding nothing every book
    # sniper stops; while it holds any coin (holders None: not traceable) every book sniper is kept, as under the book's
    # own stops, and each can still buy up to max_buys_per_sniper
    guard: bool = False
    # the book's own floor, day stop and daily cap as they apply (book/switch.book_stops: the owner's /risk values where
    # set); None: the config's [book] values
    stops: BookStops | None = None


GUARD_KILL = "wallet-wide guard"  # the kill reason while the owner's guard holds (its own line says why)


OVERLAP_REASON = "another sniper on this OKTech wallet already copies it: one buy per signal per wallet"
RETIRE_REASON = "made its buy and holds nothing: replaced so its spare buys are never needed"


def relied_buys(cfg: BookConfig) -> int:
    """The buys the book relies on per sniper: retire_after_buys, but never the buy that uses up OKTech's cap (DTAU
    and 4siK were that buy, and OKTech managed no exit for either), so at most max_buys_per_sniper - 1, at least 1."""
    return max(1, min(int(cfg.retire_after_buys), int(cfg.max_buys_per_sniper) - 1))


def copied_leaders(rec: dict) -> set[str]:
    """The wallets a sniper copies, from our own record of it (OKTech's task documents carry no config): the payload's
    ctbotWallets, or the source of a copy sniper whose payload does not say."""
    payload = rec.get("payload") or {}
    out = {str(w["address"]) for w in payload.get("ctbotWallets") or [] if isinstance(w, dict) and w.get("address")}
    if not out and rec.get("kind") in ("copy", "vcopy") and rec.get("source"):
        out.add(str(rec["source"]))
    return out


@dataclass
class Action:
    kind: str  # create | start | stop | retire (stop, delete, replace) | keep | skip | kill
    wallet: str | None
    arm: str | None
    fill_sol: float | None
    sniper_id: str | None
    reason: str
    min_sol: float | None = None  # create: the wallet's own floor from its verdict's copy plan (SOL); None = the book's
    exit: ExitChoice | None = None  # create: the exit the new sniper gets (exit_for); a running sniper keeps its own


@dataclass
class BookPlan:
    actions: list[Action] = field(default_factory=list)
    kill_reason: str | None = None
    ranked: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    paused: dict[str, str] = field(default_factory=dict)  # wallets live-paused this run: kept paused by the runner


def _lab_version(q: dict) -> int | None:
    plan = q.get("copy_plan")
    v = plan.get("lab_version") if isinstance(plan, dict) else None
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def still_paused(q: dict, rec: dict | None) -> bool:
    """A wallet paused earlier stays paused until a verdict from a newer lab than the one it was paused under."""
    if not rec:
        return False
    now_v, then_v = _lab_version(q), rec.get("lab_version")
    return not (now_v is not None and (then_v is None or now_v > int(then_v)))


def arm_fee_sol(arm: dict) -> float:
    return float(arm.get("priority") or 0.0) + float(arm.get("tip") or 0.0)


def fill_for(arm: dict, cfg: BookConfig) -> float | None:
    """The smallest fill that keeps the arm's fee at or under max_fee_share, inside [fill_min, fill_max]; None when impossible."""
    need = arm_fee_sol(arm) / cfg.max_fee_share if cfg.max_fee_share > 0 else cfg.fill_min_sol
    fill = max(cfg.fill_min_sol, need)
    if fill > cfg.fill_max_sol + 1e-12:
        return None
    return round(min(fill, cfg.fill_max_sol), 6)


def arm_retired(arm: dict) -> bool:
    """A retired arm stays in the config only so measure can tell its old fills by their fee; the book never uses it."""
    v = arm.get("retired", False)
    return v.strip().lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)


def usable_arms(cfg: BookConfig) -> dict[str, float]:
    return {
        name: f for name, arm in cfg.bid_arms.items() if not arm_retired(arm) and (f := fill_for(arm, cfg)) is not None
    }


def best_arm(bid_model: dict[str, dict], cfg: BookConfig, min_n: int = 30) -> str:
    """The arm that buys the best position once it has min_n fills; control until then."""
    usable = usable_arms(cfg)
    candidates = [(float(m.get("p75_pos")), name) for name, m in bid_model.items()
                  if name in usable and (m.get("n") or 0) >= min_n and m.get("p75_pos") is not None]  # fmt: skip
    if not candidates:
        return "control" if "control" in usable else (next(iter(usable)) if usable else "control")
    return min(candidates)[1]


def plan_floor(copy_plan: dict | str | None) -> float | None:
    """The smallest buy of the leader's to copy, from its verdict's copy plan (min_leader_sol, SOL): it lets a wallet's
    opening buys through and keeps its top-ups out. None for a verdict from before copy plans, or a value that is not
    a positive number."""
    if isinstance(copy_plan, str):
        try:
            copy_plan = json.loads(copy_plan)
        except ValueError:
            return None
    if not isinstance(copy_plan, dict):
        return None
    try:
        v = float(copy_plan.get("min_leader_sol"))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def live_pause_reason(lv: dict | None, cfg: BookConfig) -> str | None:
    """Why a wallet is paused on its live results, or None. After live_kill_n live trips, a wallet whose one-sided upper
    bound (2 x mean - lower) is below zero has lost even on the kindest reading of its copies: it is not copied,
    whatever its paper score, until those trips leave the measure window."""
    if not lv or lv.get("mean_sol") is None or int(lv.get("n") or 0) < cfg.live_kill_n:
        return None
    mean = float(lv["mean_sol"])
    lower = float(lv["lower_sol"]) if lv.get("lower_sol") is not None else mean
    upper = mean + abs(mean - lower)  # = 2 x mean - lower; a row with lower above mean never widens it downwards
    if upper >= 0:
        return None
    return f"live paused: n {int(lv['n'])}, mean {mean:.3f}, upper {upper:.3f}"


def rank_wallets(qualified: list[dict], live: dict[str, dict], cfg: BookConfig) -> list[dict]:
    """Qualified wallets by live mean (after live_n fills) else paper score; paused on live results or below min_score
    dropped; top max_wallets."""
    rows = []
    for q in qualified:
        w = q.get("wallet")
        if not w:
            continue
        lv = live.get(w) or {}
        if live_pause_reason(lv, cfg):
            continue
        if (lv.get("n") or 0) >= cfg.live_n and lv.get("mean_sol") is not None:
            score, basis = float(lv["mean_sol"]), "live"
        else:
            score, basis = float(q.get("score") or 0.0), "paper"
        if score < cfg.min_score:
            continue
        rows.append({"wallet": w, "score": score, "basis": basis, "copy_plan": q.get("copy_plan")})
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
        if arm_retired(arm):
            plan.notes.append(f"{name} arm retired: never assigned (kept only so old fills keep their arm)")
        elif name not in usable:
            need = arm_fee_sol(arm) / cfg.max_fee_share if cfg.max_fee_share > 0 else 0
            plan.notes.append(f"{name} arm skipped: its fee {arm_fee_sol(arm):g} SOL needs a {need:.2f} SOL fill to stay under "
                              f"{cfg.max_fee_share:.0%}; fill_max_sol is {cfg.fill_max_sol:g}")  # fmt: skip
    holding = bool(inp.holdings) or inp.tape_holding
    mine = _book_snipers(inp.snipers)

    def holds(s: dict) -> bool:
        """This sniper may be managing the wallet's coin: the coin's buy copied its leader, or the coin cannot be traced
        to a leader (then every book sniper counts as a holder: never strand a coin)."""
        return holding and (inp.holders is None or s["w6"] in inp.holders)

    # stops: a stop that is off (the owner's /risk, 7 Nov ~12:10 UTC) never halts the book, and with the floor off the
    # equity is not needed (an unknown one says nothing about a floor there is not)
    st = inp.stops or BookStops.pinned(cfg)
    if inp.stopped:
        plan.kill_reason = "owner /stop"
    elif st.floor_sol is not None and inp.equity_sol is None:
        plan.kill_reason = "equity unknown"
    elif st.floor_sol is not None and inp.equity_sol < st.floor_sol:
        plan.kill_reason = "equity below floor"
    elif st.day_stop_sol is not None and inp.day_realised_sol <= -st.day_stop_sol:
        plan.kill_reason = "day stop"
    elif st.daily_risk_sol is not None and inp.day_risk_used_sol >= st.daily_risk_sol:
        plan.kill_reason = "daily risk used"
    elif inp.guard:  # after the book's own stops, so each keeps the reason it always had
        plan.kill_reason = GUARD_KILL
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
    dropped: dict[str, str] = {}  # wallet[:6] -> why its sniper is no longer wanted, when it is more than the ranking
    kept_paused: set[str] = set()
    for q in inp.qualified:
        w = q.get("wallet")
        why = live_pause_reason(inp.live.get(w) if w else None, cfg)
        if w and not why and still_paused(q, inp.paused.get(w)):
            rec = inp.paused[w]
            since = time.strftime("%d %b", time.gmtime(float(rec.get("ts") or inp.now)))
            why = f"paused since {since}: {rec.get('reason') or 'live losses'}; a newer lab's verdict lifts it"
            plan.actions.append(Action("skip", w, None, None, None, why))
            dropped[w[:6]] = why
            kept_paused.add(w)
            continue
        if w and why:
            plan.paused[w] = why
            plan.actions.append(Action("skip", w, None, None, None, why))
            lv = inp.live[w]  # paused means its mean is below zero: the note says the loss as a plain number
            plan.notes.append(
                f"{w[:6]} paused: its {int(lv['n'])} live copies lost {-float(lv['mean_sol']):.3f} SOL each on average; "
                "not copied until those results are older than the measure window, whatever its paper score"
            )
            dropped[w[:6]] = why
    plan.ranked = rank_wallets([q for q in inp.qualified if q.get("wallet") not in kept_paused], inp.live, cfg)
    bookable = []
    for r in plan.ranked:
        if r["wallet"] in inp.other_copies:  # its signals already buy through that sniper; a second adds failed sells
            plan.actions.append(Action("skip", r["wallet"], None, None, None, OVERLAP_REASON))
            plan.notes.append(f"{r['wallet'][:6]} is left to another sniper that already copies it from this OKTech "
                              "wallet: OKTech buys once per signal per wallet")  # fmt: skip
            dropped[r["wallet"][:6]] = OVERLAP_REASON
        else:
            bookable.append(r)
    arms = assign_arms(bookable, inp.bid_model, cfg)
    wanted = {r["wallet"][:6]: (r["wallet"], arms.get(r["wallet"])) for r in bookable}
    seen_w6: set[str] = set()
    # holder attribution is off (run_book passes holders=None): a sniper is retired only while the wallet holds nothing
    flat = inp.holdings_known and not holding
    relied = relied_buys(cfg)
    for s in mine:
        w6, status = s["w6"], str(s.get("status"))
        if w6 in wanted and wanted[w6][1] is not None:
            wallet, arm = wanted[w6]
            if status in ("running", "starting", "idle") and flat and inp.buys_by_sniper.get(s["id"], 0) >= relied:
                # not seen: the same plan creates its replacement. An idle one is replaced too, never restarted with
                # a part-used cap (a retire whose delete failed leaves it idle)
                plan.actions.append(Action("retire", wallet, s["arm"], None, s["id"], RETIRE_REASON))
            elif status == "idle" and inp.buys_by_sniper.get(s["id"], 0) >= relied:
                # never restarted with a part-used cap, and no second sniper for its wallet while it waits
                plan.actions.append(Action("keep", wallet, s["arm"], None, s["id"],
                                           "made its buy: stays stopped until the wallet holds nothing, then replaced"))  # fmt: skip
                seen_w6.add(w6)
            elif status in ("running", "starting") and s["arm"] == arm:
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
                    why = dropped.get(w6, "dropped out of the ranking")
                    plan.actions.append(Action("stop", w6, s["arm"], None, s["id"], why))
    plans = {r["wallet"]: r.get("copy_plan") for r in bookable}
    for w6, (wallet, arm) in wanted.items():
        if w6 in seen_w6:
            continue
        if arm is None:
            plan.actions.append(Action("skip", wallet, None, None, None, "no bid arm can keep its fee under the limit"))
            continue
        # a new sniper gets the wallet's own floor and exit; a running one keeps those it was made with (no churn)
        plan.actions.append(
            Action(
                "create",
                wallet,
                arm,
                usable[arm],
                None,
                f"ranked #{[r['wallet'] for r in plan.ranked].index(wallet) + 1}",
                min_sol=plan_floor(plans.get(wallet)),
                exit=exit_for(plans.get(wallet), cfg),
            )
        )
    return plan
