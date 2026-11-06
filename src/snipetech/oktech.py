"""OKTech Sniper API: snipetech decides, OKTech's snipers buy and sell.

From snipetech's own verdicts:
* copy targets with a COPY / COPY_FILTERED verdict -> one `ctbot` sniper each: copies that wallet's
  buys (only buys at least as big as the analysis says are worth copying, below its market-cap
  ceiling), sells when it sells, plus the optimized own exits where those beat mirroring;
* deployers with a SNIPE verdict -> one `normal` sniper each: buys their next launches with the
  recommended size, bribe (priority fee + tip) and exits.

`plan` shows what would change; `push` creates / replaces / removes snipers to match. Every sniper
carries an `externalId` derived from its settings, so pushing twice never duplicates one. The key
is read from $OKS_KEY and only ever sent in the Authorization header. A key without "Allow
starting" creates snipers idle: start them on the Sniper page.
"""

from __future__ import annotations

import math

import asyncio
import contextlib
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Config
from .store import Store

KINDS = ("copy", "dev")


class OktechError(Exception):
    def __init__(self, status: int, code: str, message: str, body: dict | None = None):
        super().__init__(f"{code or status}: {message}")
        self.status, self.code, self.message, self.body = status, code, message, body or {}


class OktechClient:
    def __init__(self, base_url: str, key: str | None = None, timeout: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:  # fmt: skip
        key = key if key is not None else os.environ.get("OKS_KEY", "")
        if not key:
            raise OktechError(401, "NO_KEY", "set OKS_KEY to your OKTech Sniper API key")
        self.base = base_url.rstrip("/")
        self.http = httpx.AsyncClient(timeout=timeout, transport=transport, headers={"Authorization": f"Bearer {key}"})

    async def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(4):
            r = await self.http.request(method, self.base + path, json=body)
            if r.status_code == 429 and attempt < 3:
                await asyncio.sleep(float(r.headers.get("Retry-After") or 5))
                continue
            try:
                data = r.json()
            except ValueError:
                data = {"success": False, "error": r.text[:200]}
            if r.status_code >= 400 or data.get("success") is False:
                raise OktechError(r.status_code, data.get("code", ""), data.get("error", r.reason_phrase), data)
            return data
        raise OktechError(429, "RATE_LIMITED", "still rate limited after retries")

    async def status(self) -> dict:
        return await self._req("GET", "/status")

    async def wallets(self) -> dict:
        return await self._req("GET", "/wallets")

    async def tasks(self) -> list[dict]:
        return (await self._req("GET", "/tasks")).get("tasks", [])

    async def create(self, name: str, config: dict, wallet: str | None = None, external_id: str | None = None,
                     start: bool = False) -> dict:  # fmt: skip
        body: dict[str, Any] = {"name": name, "config": config}
        if wallet:
            body["wallet"] = wallet
        if external_id:
            body["externalId"] = external_id
        if start:
            body["start"] = True
        try:
            return await self._req("POST", "/tasks", body)
        except OktechError as exc:
            if exc.code == "DUPLICATE_EXTERNAL_ID":  # same settings pushed before: that sniper is ours already
                return exc.body | {"duplicate": True}
            raise

    async def stop(self, task_id: str) -> dict:
        return await self._req("POST", f"/tasks/{task_id}/stop")

    async def start(self, task_id: str) -> dict:
        return await self._req("POST", f"/tasks/{task_id}/start")

    async def get(self, task_id: str) -> dict:
        return await self._req("GET", f"/tasks/{task_id}")

    async def delete(self, task_id: str) -> dict:
        return await self._req("DELETE", f"/tasks/{task_id}")

    async def close(self) -> None:
        await self.http.aclose()


# -- translating snipetech's recommendations into OKTech settings ----------------------------------


@dataclass
class PlannedSniper:
    kind: str  # copy | dev
    source: str  # the wallet copied / the deployer sniped
    name: str
    config: dict
    summary: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # what OKTech can't express, or clamps

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()[:12]

    @property
    def external_id(self) -> str:
        return f"st:{self.kind}:{self.source}:{self.config_hash}"


def _round(x: float, nd: int = 4) -> float:
    return float(round(x, nd))


def fees(bribe_sol: float | None, tip_share: float, notes: list[str]) -> tuple[float, float]:
    """Split a recommended bribe into OKTech's priority fee and tip (each has a minimum)."""
    bribe = float(bribe_sol or 0)
    tip, prio = max(0.002, bribe * tip_share), max(0.001, bribe * (1 - tip_share))
    if bribe < 0.003:
        notes.append(f"bribe {bribe:g} SOL raised to OKTech's minimums (0.001 priority + 0.002 tip)")
    return _round(prio, 6), _round(tip, 6)


def autosell(exit_params: dict | None, notes: list[str], sell_fee_sol: float = 0.001) -> dict | None:
    """snipetech exit parameters (take_profit / stop_loss as fractions, seconds) -> config.autoSell."""
    e = exit_params or {}
    tp, frac = float(e.get("take_profit") or 0), float(e.get("tp_sell_fraction") or 1)
    sl, trail, act = float(e.get("stop_loss") or 0), float(e.get("trailing_stop") or 0), float(e.get("trailing_activation") or 0)
    hold = float(e.get("max_hold_secs") or 0)
    stale = float(e.get("stale_secs") or 0)
    if not (tp or sl or trail or hold or stale):
        return None
    stages = []
    if tp > 0:
        stages.append({"gainPercent": _round(tp * 100, 2), "sellPercent": _round(min(100.0, frac * 100), 2), "delaySellAllSeconds": 0})
    elif 0 < hold <= 60:
        stages.append({"gainPercent": None, "sellPercent": 100, "delaySellAllSeconds": 0, "sellAfterMs": int(hold * 1000)})
    else:  # OKTech needs at least one stage; an unreachable gain keeps the timer / stop-loss in charge
        stages.append({"gainPercent": 10000, "sellPercent": 100, "delaySellAllSeconds": 0})
        notes.append("no take-profit: exit by stop-loss / timer (one +10000% stage satisfies OKTech's 'at least one stage')")
    out: dict[str, Any] = {"enabled": True, "stages": stages, "sellTipAmount": sell_fee_sol, "sellPriorityFeeSol": sell_fee_sol}
    if hold > 0:
        out |= {"sellAfterSeconds": _round(hold, 1), "sellPercent": 100}
    else:
        out |= {"sellPercent": 0}  # no sell-all timer
    if sl > 0:
        out["stopLoss"] = {"enabled": True, "stages": [{"percentLoss": _round(min(100.0, sl * 100), 2), "sellPercent": 100}], "confirmMs": 250}
    if stale > 0:
        out["deadExit"] = {"enabled": True, "staleSeconds": max(1, int(round(stale))), "sellPercent": 100}
    if trail > 0:
        out["trailingStopLoss"] = {"enabled": True, "percentFromPeak": _round(trail * 100, 2), "sellPercent": 100,
                                   "activateAfterGainPercent": _round(act * 100, 2) if act > 0 else None,
                                   "confirmMs": 0}  # a dip from the peak sells at once  # fmt: skip
    if e.get("sell_on_dev_sell") and not e.get("copy_exit"):
        notes.append("'sell when the dev sells' has no OKTech equivalent on this sniper; not applied")
    return out


def dev_sniper(score: dict, cfg: Config) -> PlannedSniper | None:
    """A SNIPE-verdict deployer -> a `normal` sniper on its launches."""
    rec = score.get("recommended") or {}
    if score.get("verdict") != "SNIPE" or not rec.get("buy_sol"):
        return None
    notes: list[str] = []
    prio, tip = fees(rec.get("bribe_sol"), cfg.oktech.tip_share, notes)
    config: dict[str, Any] = {
        "mode": "normal", "platforms": ["pumpfun"], "devWallets": [score["address"]],
        "buyAmount": _round(max(0.001, float(rec["buy_sol"]))), "priorityFee": prio, "tipAmount": tip, "singleSnipe": False,
    }  # fmt: skip
    sell = autosell(rec.get("exit"), notes)
    if sell:
        config["autoSell"] = sell
    if cfg.oktech.preconf:
        config["preconf"] = True
    a = score["address"]
    rule = rec.get("entry_rule") or {}
    entry_line = "buys at the launch"
    if rule.get("kind") == "confirm":
        # wait for the price to confirm before buying: rinses dump before they confirm
        config["walletTriggers"] = {"enabled": True, "configs": [{
            "wallet": a, "stages": [{"percentGain": _round(rule["gain"] * 100, 2), "withinMs": int(rule["within_secs"] * 1000)}],
            "maxWaitMs": int(rule["within_secs"] * 1000), "abortOnDropPct": 25}]}  # fmt: skip
        entry_line = f"waits: buys only once its launch is up {rule['gain']:.0%} within {rule['within_secs']}s"
    elif rule.get("kind") == "streak":
        entry_line = f"should only buy after {rule['after_dumps']} of its launches in a row dumped"
        notes.append("OKTech can't count dumps in a row: start this sniper only when snipetech says the streak is on "
                     "(automatic once your key may start snipers)")  # fmt: skip
    return PlannedSniper("dev", a, f"snipetech dev {a[:6]}", config, [
        f"buys every launch by {a} ({score.get('launches', 0)} analyzed, EV {score.get('ev_sol') or 0:+.4f} SOL, "
        f"bound {score.get('ev_lower_sol') or 0:+.4f})",
        f"{config['buyAmount']} SOL per buy, priority {prio} + tip {tip} SOL; {entry_line}",
        f"exits: {_describe_exit(rec.get('exit'))}",
    ], notes)  # fmt: skip


def copy_sniper(score: dict, cfg: Config, sol_usd: float | None) -> PlannedSniper | None:
    """A COPY / COPY_FILTERED copy target -> a `ctbot` sniper copying that wallet."""
    rec = score.get("recommended") or {}
    if score.get("verdict") not in ("COPY", "COPY_FILTERED") or not rec.get("size_sol"):
        return None
    notes: list[str] = []
    filt = rec.get("filters") or {}
    a = score["address"]
    wallet: dict[str, Any] = {"address": a, "label": f"snipetech {score['verdict']}",
                              "minSol": _round(max(float(filt.get("min_target_buy_sol") or 0), cfg.copier.min_leader_sol))}  # fmt: skip
    if filt.get("max_entry_mcap_sol"):
        if sol_usd:
            wallet["maxMcapUsd"] = round(float(filt["max_entry_mcap_sol"]) * sol_usd)
        else:
            notes.append("max entry market cap filter skipped: no SOL/USD price available")
    if filt.get("max_crowd_buyers") is not None:
        notes.append(f"'at most {filt['max_crowd_buyers']} other buyers' filter has no OKTech equivalent; not applied")
    prio, tip = fees(None, cfg.oktech.tip_share, notes)
    config: dict[str, Any] = {
        "mode": "ctbot", "ctbotWallets": [wallet], "ctbotMinWallets": cfg.oktech.copy_min_wallets,
        "ctbotTimeWindowMs": cfg.oktech.copy_time_window_ms,
        "ctbotCopyExit": {"enabled": True, "sellThresholdPercent": cfg.oktech.copy_exit_threshold_percent, "sellPercent": 100,
                          "triggerWalletOnly": True, "delayMs": 0},
        "buyAmount": _round(max(0.001, float(rec["size_sol"]))), "priorityFee": prio, "tipAmount": tip,
        "singleSnipe": False, "speedMode": cfg.oktech.jetstream,
    }  # fmt: skip
    if cfg.oktech.preconf:
        config["preconf"] = True
    own = rec.get("own_exit_params")
    exit_line = "sells when the copied wallet sells"
    if own and rec.get("own_exit_beats_mirroring"):
        sell = autosell(own | {"copy_exit": True}, notes)
        if sell:
            config["autoSell"] = sell
            exit_line += f", or earlier on its own exits ({_describe_exit(own)}), which did better on unseen positions"
    lat = score.get("max_profitable_latency_slots")
    summary = [
        f"copies {a}: {score.get('positions', 0)} positions analyzed, copier EV {score.get('ev_sol') or 0:+.4f} SOL "
        f"(bound {score.get('ev_lower_sol') or 0:+.4f}), ~{score.get('positions_per_day') or 0:.1f} trades/day",
        f"{config['buyAmount']} SOL per copy; only its buys >= {wallet['minSol']} SOL"
        + (f" below ${wallet['maxMcapUsd']:,} market cap" if "maxMcapUsd" in wallet else ""),
        exit_line,
    ]
    if lat is not None:
        summary.append(f"profitable only if OKTech lands within {lat} slot(s) of its buys")
    return PlannedSniper("copy", a, f"snipetech copy {a[:6]}", config, summary, notes)


EXITS = {"lab": {"stale_secs": 1.0, "stop_loss": 0.30, "max_hold_secs": 300},  # the lab's usual best exit for copies
         "hold60": {"max_hold_secs": 60}}  # the KOL "first buy, hold 60 s" rows (Pengu, Gfree)


def list_copy_sniper(address: str, label: str, min_sol: float, buy_sol: float, max_buys: int = 5,
                     exit_params: dict | None = None, cfg: Config | None = None, exit_name: str = "lab",
                     priority_fee: float | None = None, tip: float | None = None, send_count: int | None = None,
                     tag: str = "") -> PlannedSniper:  # fmt: skip
    """One capped `ctbot` sniper copying a wallet from the lab's by-wallet ranking (the live landing test):
    only its buys >= min_sol, buy_sol per copy, stops after max_buys buys, sells when it sells (copy exit) or on
    the lab's own exit (default: dead exit 1 s, stop-loss 30 %, 300 s). `priority_fee` / `tip` (SOL) override
    OKTech's minimums for the in-slot fee test; `send_count` sets extraParams.globalSendCount (OKTech sends every
    transaction several times by default; the extra copies fail and still pay their fee)."""
    cfg = cfg or Config()
    notes: list[str] = []
    prio, tip_amount = fees(None, cfg.oktech.tip_share, notes)
    if priority_fee is not None:
        prio = _round(max(0.001, float(priority_fee)), 4)
    if tip is not None:
        tip_amount = _round(max(0.002, float(tip)), 4)
    tip = tip_amount
    ex = dict(exit_params or EXITS[exit_name])
    ex.setdefault("sell_on_dev_sell", False)
    config: dict[str, Any] = {
        "mode": "ctbot",
        "ctbotWallets": [{"address": address, "label": f"snipetech {label}"[:40], "minSol": _round(min_sol, 3)}],
        "ctbotMinWallets": 1, "ctbotTimeWindowMs": cfg.oktech.copy_time_window_ms,
        "ctbotCopyExit": {"enabled": True, "sellThresholdPercent": cfg.oktech.copy_exit_threshold_percent, "sellPercent": 100,
                          "triggerWalletOnly": True, "delayMs": 0},
        "buyAmount": _round(max(0.001, buy_sol)), "priorityFee": prio, "tipAmount": tip,
        "singleSnipe": True, "totalSnipes": max(1, min(100, int(max_buys))), "speedMode": cfg.oktech.jetstream,
    }  # fmt: skip
    if send_count is not None:
        config["extraParams"] = {"globalSendCount": max(1, int(send_count))}
    sell = autosell(ex | {"copy_exit": True}, notes, sell_fee_sol=max(prio, tip))
    if sell:
        config["autoSell"] = sell
    summary = [f"copies {label} ({address[:6]}…): only its buys >= {min_sol:g} SOL, {buy_sol:g} SOL per copy, stops after "
               f"{config['totalSnipes']} buys", f"exit: when it sells, or {_describe_exit(ex)}",
               f"fees: priority {prio:g} SOL + tip {tip:g} SOL per buy" + (f", send count {config['extraParams']['globalSendCount']}" if send_count is not None else ""),
               "the test: every fill is checked on the tape for the slot it landed in relative to the copied buy"]  # fmt: skip
    name = f"snipetech copy {label[:18]} {address[:6]}" + (f" {tag}" if tag else "")
    return PlannedSniper("copy", address, name[:60], config, summary, notes)


def _describe_exit(e: dict | None) -> str:
    if not e:
        return "none"
    parts = []
    if e.get("stale_secs"):
        parts.append(f"sell once nothing trades for {e['stale_secs']:g}s")
    if e.get("take_profit"):
        parts.append(f"TP +{e['take_profit']:.0%}" + (f" ({e.get('tp_sell_fraction', 1):.0%})" if e.get("tp_sell_fraction", 1) < 1 else ""))
    if e.get("stop_loss"):
        parts.append(f"SL -{e['stop_loss']:.0%}")
    if e.get("trailing_stop"):
        parts.append(f"trailing {e['trailing_stop']:.0%}")
    if e.get("max_hold_secs"):
        parts.append(f"max hold {e['max_hold_secs']:.0f}s")
    return ", ".join(parts) or "none"


def build_plan(store: Store, cfg: Config, kinds: tuple[str, ...] = KINDS, sol_usd: float | None = None) -> list[PlannedSniper]:
    """Snipers that snipetech's current verdicts call for, best first, capped at max_snipers."""
    out: list[tuple[float, PlannedSniper]] = []
    if "copy" in kinds:
        for s in store.scores("copy"):
            p = copy_sniper(s, cfg, sol_usd)
            if p:
                out.append((float(s.get("ev_lower_sol") or 0), p))
    if "dev" in kinds:
        for s in store.scores("deployer"):
            if "block" in store.lists_for(s["address"]):
                continue
            p = dev_sniper(s, cfg)
            if p:
                out.append((float(s.get("ev_lower_sol") or 0), p))
    out.sort(key=lambda t: -t[0])
    return [p for _, p in out[: cfg.oktech.max_snipers]]


@dataclass
class SyncPlan:
    create: list[PlannedSniper] = field(default_factory=list)
    keep: list[tuple[dict, PlannedSniper]] = field(default_factory=list)
    replace: list[tuple[dict, PlannedSniper]] = field(default_factory=list)  # settings changed
    remove: list[dict] = field(default_factory=list)  # verdict no longer supports it


def diff(planned: list[PlannedSniper], recorded: list[dict]) -> SyncPlan:
    by_source = {(r["kind"], r["source"]): r for r in recorded}
    out = SyncPlan()
    wanted = set()
    for p in planned:
        wanted.add((p.kind, p.source))
        rec = by_source.get((p.kind, p.source))
        if rec is None:
            out.create.append(p)
        elif rec["config_hash"] == p.config_hash:
            out.keep.append((rec, p))
        else:
            out.replace.append((rec, p))
    out.remove = [r for k, r in by_source.items() if k not in wanted]
    return out


async def apply(client: OktechClient, store: Store, plan: SyncPlan, wallet: str | None, prune: bool = False) -> list[dict]:
    """Create new / replaced snipers, then retire the ones they replace (and, with prune, the obsolete)."""
    results = []
    creates = plan.create + [p for _, p in plan.replace]
    for i, p in enumerate(creates):
        if i and i % 18 == 0:
            await asyncio.sleep(61)  # 20 creates a minute per key
        ans = await client.create(p.name, p.config, wallet, p.external_id)
        task = ans.get("task") or {}
        store.add_oktech_sniper({"id": task["id"], "external_id": p.external_id, "kind": p.kind, "source": p.source, "name": p.name,
                                 "config_hash": p.config_hash, "payload": p.config, "created_at": int(time.time())})  # fmt: skip
        results.append({"action": "kept (already there)" if ans.get("duplicate") else "created", "name": p.name, "id": task["id"],
                        "status": task.get("status")})  # fmt: skip
    retire = [old for old, _ in plan.replace] + (plan.remove if prune else [])
    for old in retire:
        with contextlib.suppress(OktechError):  # it may already be idle; the delete is what counts
            await client.stop(old["id"])
        try:
            await client.delete(old["id"])
            results.append({"action": "removed", "name": old["name"], "id": old["id"]})
        except OktechError as exc:
            if exc.code != "NOT_FOUND":
                results.append({"action": "remove failed", "name": old["name"], "id": old["id"], "error": str(exc)})
                continue
        store.remove_oktech_sniper(old["id"])
    return results


def speedtest_snipers(buy_sol: float, snipes: int, copy_wallet: str | None) -> list[PlannedSniper]:
    """Two capped snipers that measure where OKTech lands, at the minimum fees:

    * launch — buys the next `snipes` pump.fun launches the moment they appear (vs. the create tx);
    * copy   — copies the next `snipes` buys of `copy_wallet` (vs. that wallet's own tx).
    Each sells everything 10 s later (or on a 40% drop; the copy also when the copied wallet sells),
    so almost all of the SOL comes back; the cost is the fees.
    """
    sell = {"enabled": True, "sellPercent": 100, "sellAfterSeconds": 10, "sellTipAmount": 0.001, "sellPriorityFeeSol": 0.001,
            "stages": [{"gainPercent": 10000, "sellPercent": 100, "delaySellAllSeconds": 0}],  # OKTech needs >= 1 stage; the timer rules
            "stopLoss": {"enabled": True, "stages": [{"percentLoss": 40, "sellPercent": 100}], "confirmMs": 0}}  # fmt: skip
    base = {"buyAmount": _round(buy_sol), "priorityFee": 0.001, "tipAmount": 0.002, "singleSnipe": True,
            "totalSnipes": int(snipes), "autoSell": sell}  # fmt: skip
    out = [PlannedSniper("probe", "launch", f"snipetech speed test: next {snipes} pump.fun launches",
                         {"mode": "normal", "platforms": ["pumpfun"], "monitorProgram": True, **base},
                         summary=[f"buys {buy_sol:g} SOL of each of the next {snipes} pump.fun launches, then stops",
                                  "sells 100% after 10 s (or at -40%)", "measures: slots and position after the create"])]  # fmt: skip
    if copy_wallet:
        out.append(PlannedSniper("probe", copy_wallet, f"snipetech speed test: copy {copy_wallet[:6]} x{snipes}", {
            "mode": "ctbot", "ctbotWallets": [{"address": copy_wallet, "label": "speed test leader", "minSol": 0.1}],
            "ctbotMinWallets": 1, "ctbotTimeWindowMs": 10000, "ctbotRetriggerCooldownMs": 15000,
            "ctbotCopyExit": {"enabled": True, "sellThresholdPercent": 50, "sellPercent": 100, "triggerWalletOnly": True, "delayMs": 0},
            "speedMode": True, **base},
            summary=[f"copies the next {snipes} buys (>= 0.1 SOL) of {copy_wallet} with {buy_sol:g} SOL, then stops",
                     "sells when it sells, after 10 s at the latest (or at -40%)", "measures: slots and position after its buy"]))  # fmt: skip
    return out


# Execution settings learned from the first live batch (5 Nov 19:46 UTC, sniper 6aecdcd5):
# * seedAccount (OKTech default on) buys through an ephemeral account and sweeps afterwards; a stop-loss that fired 2 s
#   after the buy sold from the main wallet, which held 0 tokens -> pump.fun 6023 NotEnoughTokensToSell, position stuck
#   for 3.5 min. Off: the tokens sit in the buying wallet from the first slot.
# * ~10 buy attempts in 35 s failed on-chain with pump.fun 6010 AccountTypeNotSupported (v3 buy on a coin whose token
#   account was never initialised), each paying the priority fee, and each counted toward totalSnipes. preexec runs the
#   create + our buy locally first and does not send a buy that fails there.
EXECUTION: dict[str, Any] = {
    "extraParams": {"seedAccount": False},
    "preexec": {"enabled": True},
}


def strategy_sniper(strategy: dict, max_buys: int = 5, via: str = "hunt", preexec: bool = True,
                    stale: float | None = 60) -> PlannedSniper:
    """A frozen lab / paper strategy as a `normal` OKTech launch sniper (monitorProgram on pump.fun).

    Most of the lab's big-buy triggers fire within 2 s of the launch (measured on the tape), so they map to
    the launch sniper with OKTech's crowd-buy filter: buy once early buyers have put in >= X SOL."""
    trig, ex = strategy["trigger"], dict(strategy["exit"])
    notes: list[str] = []
    buy = float(strategy.get("buy_sol") or 0.2)
    config: dict[str, Any] = {"mode": "normal", "platforms": ["pumpfun"], "monitorProgram": True, "buyAmount": _round(buy),
                              "priorityFee": 0.001, "tipAmount": 0.002, "singleSnipe": True,
                              "totalSnipes": max(1, min(100, int(max_buys))), **EXECUTION}  # fmt: skip
    if not preexec:
        config["preexec"] = {"enabled": False}
    summary = [f"every new pump.fun launch, {buy:g} SOL per buy, minimum fees (0.001 priority + 0.002 tip)",
               f"stops buying by itself after {config['totalSnipes']} buys and keeps selling until those positions close",
               "buys straight into the wallet (no seed account)" + (" and only after a local dry run of the create + buy succeeds" if preexec else ", no local dry run")]  # fmt: skip
    kind = trig.get("kind")
    if kind in ("whale", "burst"):
        min_sol = float(trig.get("sol") or 0)
        buyers = 1 if kind == "whale" else int(trig.get("buyers") or 2)
        # whale triggers mostly fire within 2 s of the launch (measured on the tape); bursts carry their own window;
        # a strategy limited to the first N s after the launch (max_age) uses exactly that
        if trig.get("max_age"):
            window_ms = int(float(trig["max_age"]) * 1000)
        else:
            window_ms = 2000 if kind == "whale" else int(float(trig.get("window") or 2) * 1000)
        if via == "crowd":
            # the create transaction itself carries the buys (bundled launch): OKTech's crowd-buy filter reads them
            # before the create lands. Measured 5 Nov: hunt fired on launches with no such buy (0 of 3 probe fills);
            # the tape says the bundled subset is the profitable part of the >= 2 SOL / first-second trigger.
            config["crowdBuys"] = {"enabled": True, "minSigners": 2, "minTotalQuote": _round(min_sol, 3), "platforms": ["pumpfun"],
                                   "signal": False, "buy": True}  # fmt: skip
            summary.append(f"only launches whose create transaction already carries >= {min_sol:g} SOL of buys (OKTech crowd buys)")
            notes.append("crowdBuys fires at the create: verify on-chain that the create's own buys sum to >= the amount")
        else:
            # triggerMode "all": every set threshold must hold (with "any" the 1-buyer rule fired on the first buy, live)
            # Hunt is its own sniper mode in the OKTech app ([S] tab): the `hunt` block only applies with mode "hunt".
            # Our 5 Nov probes sent it under mode "normal", which is one reason they bought every launch.
            config["mode"] = "hunt"
            # Hunt sums every first-second buy, the creator's included; our rule is one non-dev buy >= min_sol. On the
            # tape (5 Nov, 4 h, 500 candidates) total >= 1.25x with dev <= 1 SOL keeps 79% of our triggers at 86%
            # precision; total >= min_sol alone is 66% precision (the dev's SOL counts).
            # whole numbers only and no null fields: the 22:11 batch sent 2.5 / 1.0 / nulls and bought launches that
            # failed both tests (dev 3.95 SOL; only a 1.38 SOL dev buy ahead) as if no threshold were set
            thresholds: dict[str, Any] = {"minTotalSol": int(math.ceil(min_sol * 1.25)), "maxDevBuySol": 1, "requireKnownBot": False}
            if buyers > 1:
                thresholds["minUniqueBuyers"] = int(buyers)
            if trig.get("solo"):
                # the lab's `solo` whale: a lone big buy on a quiet launch. Hunt's "skip launches with more distinct
                # buyers than this" is the closest live expression (it cannot see whether the buy was in the create tx)
                thresholds["maxUniqueBuyers"] = 2
            config["hunt"] = {"timeWindowMs": int(window_ms), "triggerMode": "all", "thresholds": thresholds,
                              "knownBotPrograms": [], "knownBotWallets": [], "knownBotLuts": [], "knownBotAccounts": [],
                              "useKnownBotDefaults": True}  # fmt: skip
            summary.append(f"only after >= {thresholds['minTotalSol']} SOL in total is bought (dev's own buy <= 1 SOL) by >= {buyers} "
                           f"wallet{'s' if buyers > 1 else ''} within {window_ms / 1000:g} s of the launch (OKTech hunt)"
                           + (", and by at most 2 distinct wallets (a lone buy on a quiet launch)" if trig.get("solo") else ""))  # fmt: skip
            notes.append("hunt is an approved-accounts mode ([S] tab); the 5 Nov probes also sent it under mode normal and "
                         "bought every launch: verify on the tape that every fill had the big buy in front of it")  # fmt: skip
    elif kind == "kol":
        raise ValueError("a KOL trigger is a copy (ctbot) sniper, not a launch sniper")
    if trig.get("plain"):
        summary.append("normal coins only: skip mayhem and holder-reward coins (they lose on paper)")
        notes.append("OKTech documents no switch that skips mayhem / holder-reward coins: find it on the Sniper form, "
                     "or accept them (they cost a little on paper)")  # fmt: skip
    ex.setdefault("sell_on_dev_sell", False)
    if stale and not ex.get("stale_secs"):
        # a coin with no trades for a minute is dead: OKTech's dead exit frees the (one-shot) sniper instead of
        # holding to the timer. Not part of the paper exit, so it is a cap on hold time, never a widening.
        ex["stale_secs"] = float(stale)
    sell = autosell(ex, notes)
    if sell:
        config["autoSell"] = sell
        summary.append(f"exit: {_describe_exit(ex)}")
    return PlannedSniper("strategy", strategy.get("id") or kind, f"snipetech strategy: {strategy.get('label', kind)}"[:80], config,
                         summary=summary, notes=notes)  # fmt: skip


def replica_from_study(study: dict, cfg: Config, buy_sol: float, sol_usd: float | None) -> PlannedSniper:
    """Rebuild a studied wallet's rules (`snipetech study`) as a `normal` OKTech sniper.

    Only rules the study found discriminating are mapped: what it buys (non-SOL-quoted curves,
    tweet-linked launches, market-cap range), the burst it enters on, and how it exits (when
    trading pauses, its typical hold). Everything else is left to OKTech's defaults."""
    wallet = study["wallet"]
    entry, exit_ = study.get("entry") or {}, study.get("exit") or {}
    num = {r["factor"]: r for r in entry.get("numeric", [])}
    xnum = {r["factor"]: r for r in exit_.get("numeric", [])}
    cats = entry.get("categorical", [])
    prof = {d["factor"]: d for d in study.get("entry_profile", [])}
    notes: list[str] = []
    summary = [f"rules of {wallet} from {study.get('entries', 0)} entries vs {study.get('skipped_samples', 0)} alternatives "
               f"({study.get('tape_hours', 0):.1f} h of tape)"]  # fmt: skip
    prio, tip = fees(None, cfg.oktech.tip_share, notes)
    config: dict[str, Any] = {"mode": "normal", "platforms": ["pumpfun"], "monitorProgram": True, "buyAmount": _round(buy_sol),
                              "priorityFee": prio, "tipAmount": tip, "singleSnipe": False}  # fmt: skip
    # what it buys: the quote asset
    sol_share = sum(c["chosen_share"] for c in cats if c["factor"] == "quote" and c["value"] == "SOL")
    quoted = [c for c in cats if c["factor"] == "quote" and c["value"] != "SOL"]
    if quoted and sol_share < 0.1:
        config["pumpFilters"] = {"enabled": True, "nonSolQuoteOnly": True, "cashbackOnly": False, "sharingCheck": True}
        config["pumpAutoSwap"] = True
        summary.append("only pump.fun curves quoted in non-SOL assets (" + ", ".join(c["value"] for c in quoted[:5])
                       + "), bought with SOL through OKTech's swap server")  # fmt: skip
    # tweet-linked launches
    tw = next((c for c in cats if c["factor"] == "x_kind" and c["value"] == "tweet"), None)
    if tw and tw["chosen_share"] >= 0.6 and (tw["lift"] or 0) >= 1.5:
        config["metadataFilter"] = {"enabled": True, "fetchOffChain": True, "matchMode": "all", "allowOnError": False,
                                    "timeoutMs": 1500, "fetchRetries": 2, "retryDelayMs": 100,
                                    "conditions": [{"variable": "twitter", "operator": "contains", "value": "/status/", "action": "allow"}]}  # fmt: skip
        summary.append(f"only launches linking a tweet ({tw['chosen_share']:.0%} of its entries vs {tw['others_share']:.0%} of the rest)")
        notes.append("the tweet check reads the token's metadata file: it adds the fetch time to every entry")
    # the burst it enters on
    b5 = num.get("buys_5s") or num.get("uniq_buyers_30s")
    if b5 and b5["auc"] >= 0.7:
        signers = max(2, int(round(b5["chosen_median"] / 2)))
        config["crowdBuys"] = {"enabled": True, "minSigners": signers, "minTotalQuote": 0, "platforms": ["pumpfun"], "signal": False, "buy": True}
        summary.append(f"enters on a burst: >= {signers} buyers ({b5['factor']} median {b5['chosen_median']:.0f} at its entries)")
        if "vol_5s" in num:
            notes.append(f"its burst also needs >= {num['vol_5s']['threshold']:.2f} SOL in 5s; OKTech's crowd minimum is in "
                         "the quote asset, which differs per token here, so it is left at 0")  # fmt: skip
    # market-cap range
    mc = prof.get("mcap_sol")
    if mc and sol_usd:
        config["maxMcapUsd"] = round(mc["p90"] * sol_usd)
        summary.append(f"max market cap ${config['maxMcapUsd']:,} (90% of its entries were below {mc['p90']:.0f} SOL)")
    # exits
    stale = xnum.get("secs_since_last_trade")
    hold = xnum.get("hold_s")
    sell: dict[str, Any] = {"enabled": True, "stages": [{"gainPercent": 20, "sellPercent": 50, "delaySellAllSeconds": 0}],
                            "sellTipAmount": 0.001, "sellPriorityFeeSol": 0.001,
                            "stopLoss": {"enabled": True, "stages": [{"percentLoss": 20, "sellPercent": 100}], "confirmMs": 250}}  # fmt: skip
    if stale and stale["direction"] == ">=" and stale["others_pass"] <= 0.05:
        secs = max(1, int(round(stale["threshold"])))
        sell["deadExit"] = {"enabled": True, "staleSeconds": secs, "sellPercent": 100}
        summary.append(f"sells everything once nothing has traded for {secs}s ({stale['chosen_pass']:.0%} of its sells, "
                       f"{stale['others_pass']:.0%} of the moments it kept holding)")  # fmt: skip
    sell_all = max(5, int(round((hold or {}).get("chosen_median") or 0)) * 8)
    sell |= {"sellAfterSeconds": sell_all, "sellPercent": 100}
    summary.append(f"also: half at +20%, all at -20%, everything after {sell_all}s (its median hold is "
                   f"{(hold or {}).get('chosen_median', 0):.0f}s)")  # fmt: skip
    notes.append("the +20% / -20% / timer exits are safety rails, not inferred from the wallet")
    config["autoSell"] = sell
    return PlannedSniper("replica", wallet, f"snipetech replica {wallet[:6]}", config, summary, notes)
