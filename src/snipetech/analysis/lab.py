"""Strategy lab: search the whole recorded market for strategies that pay at *your* speed.

No wallet to copy. Every token on the tape is a candidate, and each strategy is a trigger (when to
buy), a filter (which of those moments), and an exit:

* triggers — `launch` (at the create), `burst` (>= N buyers and >= X SOL bought within W seconds),
  `whale` (a buy of >= X SOL by a wallet that isn't the dev), `kol` (a labeled KOL buys);
  each fires at most once per token, the way a sniper takes one position per token;
* entry — your measured latency after the trigger (OKTech: slots + position in the slot),
  your costs per transaction;
* exits — stale (sell when trading stops), ride winners / cut dips (trailing from the peak, tight
  stop), take-profit / stop-loss, time.

Every trigger x exit is picked on the older part of the tape and judged on the newer part; the
winners are then filtered with the factor engine (what the market looked like at the trigger),
again picked on older and judged on newer moments. Only test numbers count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import Config
from ..sim.exits import Costs, ExitParams, prepare, run_exit
from ..sim.market import MarketPath
from ..store import Store
from .stats import summarize

LAMPORTS = 1e9


@dataclass(frozen=True)
class Trigger:
    kind: str  # launch | burst | whale | kol
    buyers: int = 0
    sol: float = 0.0
    window: float = 0.0

    def label(self) -> str:
        if self.kind == "burst":
            return f"burst >= {self.buyers} buyers & {self.sol:g} SOL in {self.window:g}s"
        if self.kind == "whale":
            return f"buy >= {self.sol:g} SOL by a non-dev"
        if self.kind == "kol":
            return "a KOL buys"
        return "at the launch"


TRIGGERS = (
    [Trigger("launch")]
    + [Trigger("burst", n, s, w) for n in (3, 5, 10) for s in (0.5, 1.0, 3.0) for w in (2.0, 5.0)]
    + [Trigger("whale", sol=s) for s in (1.0, 2.0, 5.0)]
    + [Trigger("kol")]
)


def exit_set() -> list[ExitParams]:
    out = []
    for stale in (1.0, 2.0, 5.0):
        for sl in (0.15, 0.3):
            out.append(ExitParams(take_profit=0, stop_loss=sl, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=stale))
    for trail in (0.1, 0.18, 0.25):
        for act in (0.0, 0.2):
            for hold in (60, 300):
                out.append(ExitParams(take_profit=0, stop_loss=0.15, trailing_stop=trail, trailing_activation=act,
                                      max_hold_secs=hold, sell_on_dev_sell=False))  # fmt: skip
    for tp in (0.3, 1.0):
        for hold in (30, 120):
            out.append(ExitParams(take_profit=tp, stop_loss=0.2, max_hold_secs=hold, sell_on_dev_sell=False))
    for hold in (10, 30, 60):
        out.append(ExitParams(take_profit=0, stop_loss=0, max_hold_secs=hold, sell_on_dev_sell=False))
    return out


def _fire(trig: Trigger, path: MarketPath, launch, kols: set[str]) -> int | None:
    """Index of the trade at which `trig` fires on this token (first time only)."""
    dev = launch.deployers() if launch is not None else set()
    if trig.kind == "launch":
        if launch is None:
            return None
        idx = [i for i in range(path.n) if path.sigs[i] == launch.sig]
        return max(idx) if idx else None
    for i in range(path.n):
        if not path.is_buy[i] or path.users[i] in dev:
            continue
        if trig.kind == "whale" and path.quote[i] >= trig.sol * LAMPORTS:
            return i
        if trig.kind == "kol" and path.users[i] in kols and path.quote[i] >= 0.1 * LAMPORTS:
            return i
        if trig.kind == "burst":
            lo = np.searchsorted(path.ts, path.ts[i] - trig.window, side="left")
            win = [j for j in range(lo, i + 1) if path.is_buy[j] and path.users[j] not in dev]
            if len({path.users[j] for j in win}) >= trig.buyers and sum(path.quote[j] for j in win) >= trig.sol * LAMPORTS:
                return i
    return None


def collect(store: Store, cfg: Config, buy_sol: float, latency_slots: int, position: str = "start",
            per_tx_sol: float = 0.0035, min_trades: int = 5) -> dict[str, Any]:  # fmt: skip
    """Simulate every trigger x exit on every SOL-quoted token fully recorded on the tape."""
    spans = store.tape_spans()
    costs = Costs(per_tx_lamports=per_tx_sol * LAMPORTS)
    exits = exit_set()
    kols = set(store.labels("kol"))
    rows: list[dict] = []  # one per (token, trigger): pnl per exit
    for a, b in spans:
        if b - a < 600:
            continue
        for mint, _count in store.active_mints(a, b, min_trades):
            trades = [t for t in store.trades_for_mint(mint, a, b)]
            if len(trades) < min_trades:
                continue
            launch = store.launch(mint)
            path = MarketPath.build(trades, launch if launch and a <= launch.ts else None)
            if path.n < min_trades:
                continue
            for ti, trig in enumerate(TRIGGERS):
                if trig.kind == "launch" and not (launch and a <= launch.ts <= b - 60):
                    continue
                i = _fire(trig, path, launch if (launch and a <= launch.ts) else None, kols)
                if i is None or path.ts[i] > b - 60:
                    continue
                e = int(path.landing_index(int(path.slot[i]) + latency_slots, position, i))
                prep = prepare(path, e, buy_sol, costs, max(1, latency_slots), position)
                if prep is None:
                    continue
                pnl = [run_exit(prep, ex, costs, end_ts=float(b)).pnl_sol for ex in exits]
                rows.append({"mint": mint, "trigger": ti, "ts": int(path.ts[i]), "order": (int(path.slot[i]), int(i)), "pnl": pnl})
    return {"rows": rows, "exits": exits, "tape_hours": sum(b - a for a, b in spans) / 3600}


def rank(data: dict[str, Any], cfg: Config, top: int = 10, min_train: int = 15) -> list[dict]:
    """Every trigger x exit, picked on the older 60% of each trigger's moments, judged on the newer 40%."""
    rows, exits = data["rows"], data["exits"]
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    hours = max(data["tape_hours"], 1e-6)
    out = []
    for ti, trig in enumerate(TRIGGERS):
        rs = sorted((r for r in rows if r["trigger"] == ti), key=lambda r: r["ts"])
        if len(rs) < min_train + 5:
            continue
        m = np.array([r["pnl"] for r in rs])  # moments x exits
        cut = int(len(rs) * 0.6)
        train_means = m[:cut].mean(axis=0)
        k = int(np.argmax(train_means))
        test = m[cut:, k]
        span_h = max((rs[-1]["ts"] - rs[cut]["ts"]) / 3600, 1e-6)
        st = summarize(test, conf, samples)
        out.append({
            "trigger": trig.label(), "trigger_spec": trig.__dict__, "exit": exits[k].label(), "exit_params": exits[k].to_dict(),
            "moments": len(rs), "per_hour": len(rs) / hours,
            "train_mean": float(train_means[k]), "test": {x: st.get(x) for x in ("n", "mean", "ev_lower", "win_rate", "total")},
            "test_sol_per_day": float(st.get("mean", 0) * len(rs[cut:]) / span_h * 24),
        })  # fmt: skip
    out.sort(key=lambda r: r["test"].get("ev_lower") or -1e9, reverse=True)
    return out[:top]
