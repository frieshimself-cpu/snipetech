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
    kind: str  # launch | burst | whale | kol | momentum
    buyers: int = 0
    sol: float = 0.0
    window: float = 0.0
    progress: float = 0.0  # momentum: bonding-curve progress at least this
    holders: int = 0  # momentum: holders (from the tape) at least this
    ret: float = 0.0  # momentum: price change over 300 s at least this
    max_age: float = 0.0  # whale / burst: only within this many seconds of the launch (0 = any time)
    plain: bool = False  # only normal coins (no mayhem, no holder-reward)
    group: str = "kol"  # kol: which label kind the wallets come from (kol = labeled KOLs, wallet = the tracked list)
    solo: bool = False  # whale: the buy is not inside the create tx and at most 2 wallets bought in the create slot
    event: str = ""  # event: the events-table kind this trigger fires on (first trade of the coin after the event)

    def label(self) -> str:
        base = self._label()
        extra = (
            ([f"within {self.max_age:g}s of launch"] if self.max_age else [])
            + (["normal coins"] if self.plain else [])
            + (["not a create bundle"] if self.solo else [])
        )
        return base + (f" ({', '.join(extra)})" if extra else "")

    def _label(self) -> str:
        if self.kind == "momentum":
            return (f"momentum: >= {self.buyers} buyers/5m, {self.sol:g} SOL bought/30s, curve >= {self.progress:.0%}, "
                    f">= {self.holders} holders, +{self.ret:.0%}/5m")  # fmt: skip
        if self.kind == "burst":
            return f"burst >= {self.buyers} buyers & {self.sol:g} SOL in {self.window:g}s"
        if self.kind == "whale":
            return f"buy >= {self.sol:g} SOL by a non-dev"
        if self.kind == "event":
            return f"event {self.event} (first trade after it)"
        if self.kind == "kol":
            who = "a KOL" if self.group == "kol" else f"a tracked wallet ({self.group})"
            return f"{who} makes its first buy in the coin, >= {self.sol or KOL_MIN_SOL:g} SOL"
        return "at the launch"


KOL_MIN_SOL = 0.1  # a labeled wallet's buy below this is noise, not a signal

TRIGGERS = (
    [Trigger("launch")]
    + [Trigger("burst", n, s, w) for n in (3, 5, 10) for s in (0.5, 1.0, 3.0) for w in (2.0, 5.0)]
    + [Trigger("whale", sol=s) for s in (1.0, 2.0, 5.0)]
    + [Trigger("kol")]
    # the KOLs' small buys are top-ups after a 2-4 SOL first buy (decu: 4 buys/coin): the first buy is the signal
    + [Trigger("kol", sol=1.0)]
    + [Trigger("kol", sol=s, group="wallet") for s in (1.0, 2.0)]
    # the qualify stage's candidates (and the tracked list) at the KOL floor: every first buy >= 0.1 SOL counts
    + [Trigger("kol", sol=KOL_MIN_SOL, group="cand")]
    # forward paper trading: the >= 5 / 2 SOL edge is all in big buys within ~1 s of the launch, on normal coins
    + [Trigger("whale", sol=s, max_age=1.0, plain=True) for s in (2.0, 5.0)]
    # the part of that edge OKTech can reach: the big buy arrives on its own, not as part of a create bundle
    + [Trigger("whale", sol=s, max_age=a, plain=True, solo=True) for s in (1.0, 2.0) for a in (1.0, 5.0)]
    # what the most profitable small-size wallet on the tape (ARu4n5mF) buys: proven coins mid-curve, on momentum
    + [
        Trigger("momentum", buyers=u, sol=v, progress=pr, holders=13, ret=0.2)
        for u in (20, 40)
        for v in (3.0, 6.0)
        for pr in (0.3, 0.5)
    ]
)

CURVE_REAL_TOKENS = 793_100_000 * 10**6  # tokens a pump.fun curve sells before it completes
VIRTUAL_OFFSET = 1_073_000_000_000_000 - CURVE_REAL_TOKENS  # virtual minus real token reserves (constant on the curve)


def _momentum(trig: Trigger, path: MarketPath, dev: set[str]) -> int | None:
    """First non-dev buy at which the coin is proven and moving: unique buyers over 5 min, SOL bought over 30 s,
    curve progress, holders (net buyers on the tape), price change over 5 min, >= 2 buys of 1+ SOL in the last minute."""
    from collections import Counter

    ts, q, b = path.ts, path.q, path.b
    buyq = np.where(path.is_buy, path.quote, 0.0)
    cs = np.concatenate([[0.0], np.cumsum(buyq)])
    big = np.concatenate([[0], np.cumsum(path.is_buy & (path.quote >= LAMPORTS))])
    price = np.divide(q, b, out=np.zeros_like(q), where=b > 0)
    bal: Counter = Counter()
    holders = 0
    win: Counter = Counter()
    uniq, lo = 0, 0
    for i in range(path.n):
        u = path.users[i]
        if u:
            before = bal[u] > 0
            bal[u] += path.base[i] if path.is_buy[i] else -path.base[i]
            holders += int(bal[u] > 0) - int(before)
            if path.is_buy[i]:
                win[u] += 1
                uniq += win[u] == 1
        while ts[lo] < ts[i] - 300:
            if path.is_buy[lo] and path.users[lo]:
                win[path.users[lo]] -= 1
                uniq -= win[path.users[lo]] == 0
            lo += 1
        if not path.is_buy[i] or not u or u in dev:
            continue
        progress = 1.0 if path.amm[i] else 1.0 - (b[i] - VIRTUAL_OFFSET) / CURVE_REAL_TOKENS
        if uniq < trig.buyers or progress < trig.progress or holders < trig.holders:
            continue
        j30 = int(np.searchsorted(ts, ts[i] - 30, side="left"))
        if cs[i + 1] - cs[j30] < trig.sol * LAMPORTS:
            continue
        j60 = int(np.searchsorted(ts, ts[i] - 60, side="left"))
        if big[i + 1] - big[j60] < 2:
            continue
        j300 = int(np.searchsorted(ts, ts[i] - 300, side="left"))
        if price[j300] <= 0 or price[i] / price[j300] - 1 < trig.ret:
            continue
        return i
    return None


def exit_set() -> list[ExitParams]:
    out = []
    for stale in (1.0, 2.0, 5.0):
        for sl in (0.15, 0.3):
            out.append(
                ExitParams(take_profit=0, stop_loss=sl, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=stale)
            )
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
    # the scale-out ladder the best copy bot on the tape (6Z91) runs: bank part at a small gain, trail the rest,
    # dump everything on a pause or a dip below entry
    for tp in (0.1, 0.2):
        for frac in (0.35, 0.5):
            for sl, trail in ((0.08, 0.1), (0.15, 0.1), (0.15, 0.18)):
                for stale in (0.0, 1.0):
                    out.append(ExitParams(take_profit=tp, tp_sell_fraction=frac, stop_loss=sl, trailing_stop=trail,
                                          max_hold_secs=300, sell_on_dev_sell=False, stale_secs=stale))  # fmt: skip
    return out


def _fire(trig: Trigger, path: MarketPath, launch, kols, events: list[tuple] | None = None) -> int | None:
    if trig.kind == "event":
        # events: [(kind, ts, slot)] for this coin; fire on the first trade at or after the earliest event of the kind
        hits = sorted((ts, slot or 0) for kind, ts, slot in (events or []) if kind == trig.event)
        if not hits:
            return None
        ev_ts, ev_slot = hits[0]
        for i in range(1, path.n):
            if path.ts[i] >= ev_ts and path.slot[i] >= ev_slot:
                return i
        return None
    """Index of the trade at which `trig` fires on this token (first time only).
    `kols`: the labeled wallets, a set (the KOL group) or {label kind: set of addresses}."""
    dev = launch.deployers() if launch is not None else set()
    if trig.plain and (launch is None or launch.mayhem or launch.holder_reward):
        return None
    if trig.max_age and launch is None:
        return None
    if trig.kind == "momentum":
        return _momentum(trig, path, dev)
    if trig.kind == "launch":
        if launch is None:
            return None
        idx = [i for i in range(path.n) if path.sigs[i] == launch.sig]
        return max(idx) if idx else None
    group = (kols.get(trig.group) or set()) if isinstance(kols, dict) else kols
    seen: set[str] = set()  # buyers so far: a KOL trigger wants the wallet's *first* buy in the coin, not a top-up
    for i in range(path.n):
        if trig.max_age and path.ts[i] > launch.ts + trig.max_age:
            return None
        if not path.is_buy[i] or path.users[i] in dev:
            continue
        first = path.users[i] not in seen
        seen.add(path.users[i])
        if trig.kind == "whale" and path.quote[i] >= trig.sol * LAMPORTS:
            if trig.solo:
                # live (5 Nov): a >= 2 SOL buy inside the create bundle cannot be followed in its own slot; we land
                # after the whole bundle, at the top, and bundles dump within seconds. Keep only the big buys that
                # arrive on their own: not in the create tx, with at most 2 wallets having bought in the create slot.
                if launch is None or path.sigs[i] == launch.sig:
                    return None
                in_slot = {path.users[j] for j in range(i + 1) if path.slot[j] == launch.slot and path.is_buy[j]
                           and path.users[j] not in dev}  # fmt: skip
                if len(in_slot) > 2:
                    return None
            return i
        if (
            trig.kind == "kol"
            and first
            and path.users[i] in group
            and path.quote[i] >= (trig.sol or KOL_MIN_SOL) * LAMPORTS
        ):
            return i
        if trig.kind == "burst":
            lo = np.searchsorted(path.ts, path.ts[i] - trig.window, side="left")
            win = [j for j in range(lo, i + 1) if path.is_buy[j] and path.users[j] not in dev]
            if (
                len({path.users[j] for j in win}) >= trig.buyers
                and sum(path.quote[j] for j in win) >= trig.sol * LAMPORTS
            ):
                return i
    return None


def label_groups(store: Store) -> dict[str, set[str]]:
    """The wallets each KOL trigger group fires on: `kol` = labeled KOLs, `wallet` = the tracked-wallet list,
    `cand` = the qualify stage's candidates plus the tracked list (measured at the 0.1 SOL floor)."""
    wallet = set(store.labels("wallet"))
    return {"kol": set(store.labels("kol")), "wallet": wallet, "cand": set(store.labels("cand")) | wallet}


def _mints_with_buys_by(store: Store, wallets: set[str], a: int, b: int) -> set[str]:
    """Coins in [a, b] where any of `wallets` bought (chunked IN lists; thousands of candidates are normal)."""
    out: set[str] = set()
    ws = sorted(wallets)
    for i in range(0, len(ws), 500):
        chunk = ws[i : i + 500]
        out |= {
            m
            for (m,) in store._q(
                f"SELECT DISTINCT mint FROM trades WHERE is_buy=1 AND ts BETWEEN ? AND ? AND user IN ({','.join('?' * len(chunk))})",
                [a, b, *chunk],
            )
        }
    return out


def collect(store: Store, cfg: Config, buy_sol: float, latency_slots: int, position: str = "start",
            per_tx_sol: float = 0.0035, min_trades: int = 5, groups: tuple[str, ...] | None = None,
            event_kinds: tuple[str, ...] | None = None) -> dict[str, Any]:  # fmt: skip
    """Simulate every trigger x exit on every SOL-quoted token fully recorded on the tape.
    With `groups` (e.g. ("cand",)) only those label groups' KOL-type triggers run, on the coins their wallets bought:
    the qualify stage's lab, a small fraction of the full one."""
    spans = store.tape_spans()
    costs = Costs(per_tx_lamports=per_tx_sol * LAMPORTS)
    exits = exit_set()
    kols = label_groups(store)
    all_triggers = list(TRIGGERS) + [Trigger("event", event=k) for k in (event_kinds or ())]
    triggers = [(ti, t) for ti, t in enumerate(all_triggers)
                if (groups is None or (t.kind == "kol" and t.group in groups)) and (t.kind != "event" or event_kinds)]  # fmt: skip
    if groups is not None and event_kinds:
        triggers = [
            (ti, t) for ti, t in enumerate(all_triggers) if (t.kind == "kol" and t.group in groups) or t.kind == "event"
        ]
    events_by_mint: dict[str, list[tuple]] = {}
    if event_kinds:
        marks = ",".join("?" * len(event_kinds))
        for kind, ts, slot, mint in store._q(
            f"SELECT kind, ts, slot, mint FROM events WHERE kind IN ({marks})", list(event_kinds)
        ):
            events_by_mint.setdefault(mint, []).append((kind, ts, slot))
    group_wallets = set().union(*(kols.get(g, set()) for g in groups)) if groups else None
    rows: list[dict] = []  # one per (token, trigger): pnl per exit
    for a, b in spans:
        if b - a < 600:
            continue
        wanted = _mints_with_buys_by(store, group_wallets, int(a), int(b)) if group_wallets is not None else None
        if wanted is not None and not wanted:
            continue
        for mint, _count in store.active_mints(a, b, min_trades):
            if wanted is not None and mint not in wanted:
                continue
            trades = [t for t in store.trades_for_mint(mint, a, b)]
            if len(trades) < min_trades:
                continue
            launch = store.launch(mint)
            path = MarketPath.build(trades, launch if launch and a <= launch.ts else None)
            if path.n < min_trades:
                continue
            for ti, trig in triggers:
                if trig.kind == "launch" and not (launch and a <= launch.ts <= b - 60):
                    continue
                i = _fire(trig, path, launch if (launch and a <= launch.ts) else None, kols, events_by_mint.get(mint))
                if i is None or path.ts[i] > b - 60:
                    continue
                e = int(path.landing_index(int(path.slot[i]) + latency_slots, position, i))
                prep = prepare(path, e, buy_sol, costs, max(1, latency_slots), position)
                if prep is None:
                    continue
                pnl = [run_exit(prep, ex, costs, end_ts=float(b)).pnl_sol for ex in exits]
                rows.append({"mint": mint, "trigger": ti, "ts": int(path.ts[i]), "order": (int(path.slot[i]), int(i)), "pnl": pnl,
                             "who": path.users[i]})  # fmt: skip
    return {"rows": rows, "exits": exits, "tape_hours": sum(b - a for a, b in spans) / 3600, "triggers": all_triggers}


def rank(data: dict[str, Any], cfg: Config, top: int = 10, min_train: int = 15) -> list[dict]:
    """Every trigger x exit, picked on the older 60% of each trigger's moments, judged on the newer 40%."""
    rows, exits = data["rows"], data["exits"]
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    hours = max(data["tape_hours"], 1e-6)
    out = []
    for ti, trig in enumerate(data.get("triggers") or TRIGGERS):
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


def rank_wallets(data: dict[str, Any], cfg: Config, labels: dict[str, str] | None = None, top: int = 40,
                 min_n: int = 4) -> list[dict]:  # fmt: skip
    """Per wallet: is its first buy a signal worth our exit? For every KOL trigger, the exit is picked on the older
    60% of the trigger's moments (as `rank` does); each triggering wallet is then scored on all of its moments with
    that exit. Wallets with the same address under several triggers keep the best-covered trigger (lowest threshold)."""
    rows, exits = data["rows"], data["exits"]
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    labels = labels or {}
    best: dict[str, dict] = {}
    for ti, trig in enumerate(data.get("triggers") or TRIGGERS):
        if trig.kind != "kol":
            continue
        rs = sorted((r for r in rows if r["trigger"] == ti), key=lambda r: r["ts"])
        if len(rs) < 10:
            continue
        m = np.array([r["pnl"] for r in rs])
        k = int(np.argmax(m[: int(len(rs) * 0.6)].mean(axis=0)))
        by: dict[str, list[float]] = {}
        for r, p in zip(rs, m[:, k]):
            by.setdefault(r["who"], []).append(float(p))
        for w, pn in by.items():
            if len(pn) < min_n or (w in best and best[w]["n"] >= len(pn)):
                continue
            st = summarize(np.array(pn), conf, samples)
            best[w] = {"wallet": w, "label": labels.get(w), "trigger": trig.label(), "exit": exits[k].label(), "n": len(pn),
                       "mean": st.get("mean"), "ev_lower": st.get("ev_lower"), "win_rate": st.get("win_rate"), "total": st.get("total")}  # fmt: skip
    out = sorted(best.values(), key=lambda r: r["ev_lower"] if r["ev_lower"] is not None else -1e9, reverse=True)
    return out[:top]
