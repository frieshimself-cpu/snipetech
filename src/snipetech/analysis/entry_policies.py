"""Which launches of a sniper-rinsing deployer to take.

* streak  — take a launch only after k of its previous launches in a row (same session: launches
  less than an hour apart) were dumped on snipers. A previous launch only counts as dumped once its
  dump had happened by the time the next launch appeared: no hindsight.
* confirm — don't buy at the create: buy once the price is up X% within Y seconds of the launch and
  no insider has sold yet (OKTech: walletTriggers). A rinse usually dumps before it confirms.

Each family is picked on the older launches and judged on the newer ones it never saw.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from ..config import Config
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from .launches import LaunchContext, entry_index
from .optimize import split_indices
from .stats import summarize

SESSION_GAP_SECS = 3600
STREAKS = (0, 1, 2, 3)
CONFIRMS = tuple((x, y) for x in (0.1, 0.25, 0.5, 1.0) for y in (5, 15, 30, 60))


def _pnl(ctx: LaunchContext, e: int, cfg: Config, ex: ExitParams) -> float | None:
    costs = Costs.from_config(cfg.costs)
    prep = prepare(ctx.path, e, cfg.snipe.buy_sol, costs, cfg.snipe.exit_latency_slots, cfg.snipe.entry_position, ctx.insider_sell)
    return None if prep is None else run_exit(prep, ex, costs, end_ts=ctx.window_end).pnl_sol


def streaks(feats: list[dict]) -> list[int]:
    """For each launch: how many launches right before it, in its session, had visibly dumped."""
    out = []
    for i, f in enumerate(feats):
        k, j = 0, i - 1
        while j >= 0 and feats[j + 1]["ts"] - feats[j]["ts"] <= SESSION_GAP_SECS:
            prev = feats[j]
            dump = prev.get("insider_dump_secs")
            if prev["outcome"] not in ("rinse", "rug") or dump is None or prev["ts"] + dump > f["ts"]:
                break
            k, j = k + 1, j - 1
        out.append(k)
    return out


def confirm_entry(ctx: LaunchContext, gain: float, within: float, cfg: Config) -> int | None:
    """Entry index for "buy once it is up `gain` within `within` s, before any insider sells"."""
    p = ctx.path
    create_last = max((i for i in range(p.n) if p.sigs[i] == ctx.launch.sig), default=0)
    if p.b[create_last] <= 0:
        return None
    base = p.q[create_last] / p.b[create_last]
    for i in range(create_last + 1, p.n):
        if p.ts[i] > ctx.launch.ts + within or ctx.insider_sell[i]:
            return None
        if p.b[i] > 0 and p.q[i] / p.b[i] >= base * (1 + gain):
            return int(p.landing_index(int(p.slot[i]) + 1, cfg.snipe.entry_position, i))  # you react a slot later
    return None


def _evaluate(policies: list[tuple[str, dict, Callable[[int], int | None]]], contexts: list[LaunchContext], cfg: Config,
              ex: ExitParams) -> dict[str, Any]:  # fmt: skip
    """policies: (label, params, entry-index-or-None per launch index)."""
    n = len(contexts)
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    mats = []
    for _label, _params, pick in policies:
        vals = np.full(n, np.nan)
        for j, ctx in enumerate(contexts):
            e = pick(j)
            if e is not None:
                v = _pnl(ctx, e, cfg, ex)
                if v is not None:
                    vals[j] = v
        mats.append(vals)
    rows = []
    for (label, params, _), vals in zip(policies, mats):
        taken = vals[~np.isnan(vals)]
        rows.append({"rule": label, **params, "taken": int(len(taken)), "of": n, **_brief(summarize(taken, conf, samples))})
    out: dict[str, Any] = {"rules": rows}
    split = split_indices(n)
    if split is not None:
        train, test = split

        def mean(vals, cols):
            v = vals[cols]
            v = v[~np.isnan(v)]
            return float(v.mean()) if len(v) >= 3 else -np.inf

        k = int(np.argmax([mean(v, train) for v in mats]))
        test_vals = mats[k][test]
        out["walk_forward"] = {"picked": policies[k][0], "params": policies[k][1], "train_launches": len(train),
                               "test_launches": len(test), "test": _brief(summarize(test_vals[~np.isnan(test_vals)], conf, samples)),
                               "test_take_all": _brief(summarize(mats[0][test][~np.isnan(mats[0][test])], conf, samples))}  # fmt: skip
    return out


def _brief(s: dict) -> dict:
    return {k: s.get(k) for k in ("n", "mean", "ev_lower", "total", "win_rate") if k in s}


def entry_policies(feats: list[dict], contexts: list[LaunchContext], cfg: Config, ex: ExitParams) -> dict[str, Any]:
    """Streak and confirmation rules for this deployer, under the exit rule `ex`."""
    default = [entry_index(ctx, cfg.snipe.entry_slot_offset, cfg.snipe.entry_position) for ctx in contexts]
    st = streaks(feats)
    streak_rules = [(f"after {k} dumped launch{'es' if k != 1 else ''} in a row" if k else "every launch", {"after_dumps": k},
                     (lambda j, k=k: default[j] if st[j] >= k else None)) for k in STREAKS]  # fmt: skip
    confirm_rules = [(f"buy once up {x:.0%} within {y}s (no insider sold)", {"gain": x, "within_secs": y},
                      (lambda j, x=x, y=y: confirm_entry(contexts[j], x, y, cfg))) for x, y in CONFIRMS]  # fmt: skip
    every = streak_rules[:1]  # "every launch" is the baseline both families are compared against
    return {
        "streak": _evaluate(streak_rules, contexts, cfg, ex),
        "confirm": _evaluate(every + confirm_rules, contexts, cfg, ex),
        "streak_now": st[-1] if st else 0,
    }
