"""Deployer verdicts, checked forward.

A deployer's verdict (SNIPE, MARGINAL, AVOID) is made from its past launches. Until 11 Nov nothing asked whether it
holds afterwards: the server scored deployers every hour, 82 of those marked SNIPE launched 2,096 coins in a day, and
no agent saw the verdicts, no paper test followed them, no sniper traded them.

When a deployer is scored again (`Service.analyze_deployer`), the launches that began after the data its previous
verdict was made on are that verdict's forward test: no result of theirs was known when it was made. Each is written
to the paper record as one trade, the configured snipe as the analysis simulates it (where a launch buy lands, at the
measured cost of a transaction), under a rule of its own for each verdict. AVOID and MARGINAL are the control: a SNIPE
record no better than theirs says the verdict picks nothing.

The rules are frozen paper strategies like any other (the report, the live page and the agents' snapshot show them)
with two differences: the paper run never scores them (`paper.fed`: their trades come from here), and their trigger
is no lab trigger (kind "dev"), so `oktech strategy` and the live test refuse them. A rule the paper test dropped as
refuted gets no more trades.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..models import PUMPFUN
from ..sim.exits import ExitParams
from ..store import Store
from . import paper
from .deployer import score_basis

KIND = "dev"  # the trigger kind of a rule on deployer verdicts (paper.LAUNCH_LANDINGS says where it lands)
FED_BY = "deployer scores"
VERDICTS = ("SNIPE", "MARGINAL", "AVOID")
WORDS = {"SNIPE": "worth sniping", "MARGINAL": "marginal", "AVOID": "avoid"}


def rule(cfg: Config, verdict: str, basis: str, per_tx_sol: float, now: float) -> dict[str, Any]:
    """The frozen paper strategy the forward launches of one verdict are recorded under."""
    trigger = {"kind": KIND, "verdict": verdict, "basis": basis}
    exit_params = ExitParams.from_config(cfg.snipe.exit).to_dict()
    lat, pos, buy = int(cfg.snipe.entry_slot_offset), str(cfg.snipe.entry_position), float(cfg.snipe.buy_sol)
    label = f"every launch of a deployer marked {WORDS[verdict]} ({verdict}) before it, sniped as the verdict assumes"
    return {
        "id": paper.strategy_id(trigger, exit_params, pos, lat, buy),
        "label": f"{label} ({basis})",
        "trigger": trigger,
        "exit": exit_params,
        "position": pos,
        "latency": lat,
        "buy_sol": buy,
        "per_tx_sol": float(per_tx_sol),
        "from_ts": int(now),
        "frozen_at": int(now),
        "lab": {"mean": None, "ev_lower": None, "landing": None},
        paper.FED: FED_BY,
    }


def _frozen(store: Store, wanted: dict[str, Any]) -> dict[str, Any] | None:
    """The rule as the paper test holds it, added when it is not there yet; None when it was dropped as refuted."""
    cov = store.coverage("paper_strategies")
    strategies = list(cov.get("strategies", []))
    have = next((s for s in strategies if s.get("id") == wanted["id"]), None)
    if have is None:
        store.set_coverage("paper_strategies", cov | {"strategies": [*strategies, wanted]})
        return wanted
    return None if have.get("refuted") else have


def record(store: Store, cfg: Config, prior: dict | None, report: dict, per_tx_sol: float, now: float) -> int:
    """Write the forward launches of `prior`, the deployer's score as it stood before `report` was made: the launches
    of the report that began after the data `prior` was made on. Returns the trades written.

    Nothing is written for a prior score made another way (another landing or method: no verdict), one that does not
    say where its data ended, or one with no verdict to test."""
    if not prior or prior.get("verdict") not in VERDICTS:
        return 0
    basis = score_basis(cfg)
    until = prior.get("data_until")
    if prior.get("basis") != basis or until is None:
        return 0
    forward = [row for row in report.get("launches") or []
               if row.get("pnl_sol") is not None and row.get("ts") is not None and int(row["ts"]) > int(until)]  # fmt: skip
    if not forward:
        return 0
    frozen = _frozen(store, rule(cfg, prior["verdict"], basis, per_tx_sol, now))
    if frozen is None:
        return 0
    landing = paper.landing_key(frozen["latency"], frozen["position"])
    written = 0
    for row in forward:
        hold = row.get("hold_secs")
        written += store.put_paper_trade({
            "strategy": frozen["id"], "mint": row["mint"], "trigger_ts": int(row["ts"]), "entry_ts": float(row["ts"]),
            "exit_ts": None if hold is None else float(row["ts"]) + float(hold), "pnl": float(row["pnl_sol"]),
            "roi": row.get("roi"), "reason": row.get("exit_reason"),
            "data": {"deployer": report.get("address"), "verdict_until": int(until),
                     "ev_lower_then": prior.get("ev_lower_sol"), "launches_then": prior.get("launches"),
                     "landing": landing, "venue": PUMPFUN},
        })  # fmt: skip
    return written
