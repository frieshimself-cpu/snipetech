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

import asyncio
import contextlib
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Config
from .exits_menu import MENU
from .store import Store

KINDS = ("copy", "dev")  # what `oktech plan --only` names; verdict copies are recorded as kind "vcopy"
PUSH_KINDS = {"copy": "vcopy", "dev": "dev"}


class OktechError(Exception):
    def __init__(self, status: int, code: str, message: str, body: dict | None = None):
        super().__init__(f"{code or status}: {message}")
        self.status, self.code, self.message, self.body = status, code, message, body or {}


def _retry_after(value: str | None, default: float = 5.0, cap: float = 60.0) -> float:
    """Retry-After as seconds or as an HTTP date; anything unreadable waits `default`. Never more than `cap`."""
    if not value:
        return default
    try:
        return max(0.0, min(cap, float(value)))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        secs = parsedate_to_datetime(value).timestamp() - time.time()
        return max(1.0, min(cap, secs)) if secs > 0 else 1.0
    except (TypeError, ValueError, IndexError, OverflowError):
        return default


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
            try:
                r = await self.http.request(method, self.base + path, json=body)
            except httpx.HTTPError as exc:  # network errors and timeouts are OKTech failures like any other
                raise OktechError(0, "NETWORK", f"network error: {type(exc).__name__}: {exc}") from exc
            if r.status_code == 429 and attempt < 3:
                await asyncio.sleep(_retry_after(r.headers.get("Retry-After")))
                continue
            if 200 <= r.status_code < 300 and not r.content.strip():
                return {"success": True}  # an empty 2xx answer (a stop or delete with no body) is a success
            try:
                data = r.json()
            except ValueError:
                data = (
                    {"success": r.status_code < 400, "error": r.text[:200]}
                    if r.status_code >= 400
                    else {"success": True, "raw": r.text[:200]}
                )
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

    async def docs(self) -> dict:
        """OKTech's own API reference (modes, fields, defaults, errors), as its Sniper page shows it. Read-only."""
        url = self.base.rsplit("/v1", 1)[0] + "/docs"
        try:
            r = await self.http.get(url)
        except httpx.HTTPError as exc:
            raise OktechError(0, "NETWORK", f"network error: {type(exc).__name__}: {exc}") from exc
        try:
            data = r.json()
        except ValueError:
            data = None
        if r.status_code >= 400:
            d = data if isinstance(data, dict) else {}
            raise OktechError(r.status_code, d.get("code", ""), d.get("error", r.text[:200] or r.reason_phrase), d)
        if data is None:  # a 2xx page that is not JSON (HTML or Markdown): hand it back as it came
            return {"raw": r.text}
        if not isinstance(data, dict):
            return {"docs": data}
        if data.get("success") is False:
            raise OktechError(r.status_code, data.get("code", ""), data.get("error", r.reason_phrase), data)
        return data

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
    salt: str = ""  # generation tag: a replacement for a spent sniper with the same settings needs a new externalId

    @property
    def config_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()[:12]

    @property
    def external_id(self) -> str:
        base = f"st:{self.kind}:{self.source}:{self.config_hash}"
        return f"{base}:{self.salt}" if self.salt else base


def _round(x: float, nd: int = 4) -> float:
    return float(round(x, nd))


def _capped_size(size_sol: float, cfg: Config, notes: list[str]) -> float:
    """A verdict's recommended size, never above [oktech] max_buy_sol (the sweep recommended up to 5 SOL)."""
    size = max(0.001, float(size_sol))
    if size > cfg.oktech.max_buy_sol:
        notes.append(f"size {size:g} SOL capped at {cfg.oktech.max_buy_sol:g} SOL ([oktech] max_buy_sol)")
        size = cfg.oktech.max_buy_sol
    return _round(size)


def _bucket_usd(x: float) -> int:
    """Two significant figures (41,234 -> 41,000)."""
    if x <= 0:
        return 0
    digits = len(str(int(x))) - 2
    step = 10 ** max(0, digits)
    return int(round(x / step) * step)


def execution_params(send_count: int | None = None, compute_units: int | None = None) -> dict[str, Any]:
    """`extraParams` for every sniper we make.

    * seedAccount off: with it on (OKTech's default) every buy goes through an ephemeral account and a second
      transaction sweeps the tokens into the wallet (measured 6 Nov: a sweep per fill, a failed duplicate sweep in 3 of
      6, ~0.004 SOL of rent parked per fill, and a stop-loss that raced the sweep on 5 Nov: pump error 6023, position
      unmanaged for 3.5 min). Off, the tokens sit in the wallet from the first slot.
    * globalSendCount 1 is also OKTech's form default. It does not explain the 2-3 sell transactions per exit seen
      on chain (separate nonce accounts; the losers fail with 6023 and pay their fee): that is an open question for
      OKTech, see docs/oktech-audit-2026-11-06.md.
    * computeUnits only when asked: OKTech's default is 150,000; the leader ranks by fee per CU."""
    out: dict[str, Any] = {
        "globalSendCount": max(1, int(send_count)) if send_count is not None else 1,
        "seedAccount": False,
    }
    if compute_units:
        out["computeUnits"] = int(compute_units)
    return out


def fees(bribe_sol: float | None, tip_share: float, notes: list[str]) -> tuple[float, float]:
    """Split a recommended bribe into OKTech's priority fee and tip (each has a minimum)."""
    bribe = float(bribe_sol or 0)
    tip, prio = max(0.002, bribe * tip_share), max(0.001, bribe * (1 - tip_share))
    if bribe < 0.003:
        notes.append(f"bribe {bribe:g} SOL raised to OKTech's minimums (0.001 priority + 0.002 tip)")
    return _round(prio, 6), _round(tip, 6)


UNREACHABLE_STAGE = {"gainPercent": 10000, "sellPercent": 100, "delaySellAllSeconds": 0}
# OKTech's migration sell: "on migration to a new pool, sell % after delay (100 ms - 1 min)" (its auto-sell form,
# docs/oktech-features.md). It has never fired on one of our live trades.
MIGRATION_SELL_MIN_MS, MIGRATION_SELL_MAX_MS = 100, 60_000


def migration_sell(exit_params: dict | None) -> dict | None:
    """An exit's migration sell (`migration_sell_frac` of what is held, `migration_sell_delay` s after the coin leaves
    its curve) as OKTech's autoSell.migrationSell; None when it is off. Sent exactly as the lab scored it: a share above
    the whole position, or a delay outside the app's 100 ms - 1 min or not a whole number of milliseconds, raises
    instead of going out as an exit nobody scored (the hunt thresholds' lesson: OKTech ignores what it cannot read)."""
    e = exit_params or {}
    frac, delay = float(e.get("migration_sell_frac") or 0), float(e.get("migration_sell_delay") or 0)
    if not math.isfinite(frac) or frac > 1 + 1e-9:
        raise ValueError(f"a migration sell of {frac:g} x the position: OKTech sells at most all of it")
    if frac <= 0:
        return None
    ms = delay * 1000
    if not math.isfinite(ms) or abs(ms - round(ms)) > 1e-6:
        raise ValueError(
            f"OKTech takes the migration sell delay in whole milliseconds: {delay:g} s is not sent as scored"
        )
    if not MIGRATION_SELL_MIN_MS <= round(ms) <= MIGRATION_SELL_MAX_MS:
        raise ValueError(f"OKTech's migration sell waits 100 ms to 60 s after the migration, not {delay:g} s")
    return {"enabled": True, "sellPercent": _round(frac * 100, 2), "delayMs": round(ms)}


def autosell(exit_params: dict | None, notes: list[str], sell_fee_sol: float = 0.001) -> dict | None:
    """snipetech exit parameters (take_profit / stop_loss as fractions, seconds) -> config.autoSell. A take-profit that
    sells part of the position is followed by an unreachable 100 % stage (OKTech's app: "100 % must be last"). Never
    sends autoStop: it switches the exits off on a fast pump (playbook 9). A migration sell goes out as scored
    (`migration_sell`) and is flagged as never seen live."""
    e = exit_params or {}
    tp, frac = float(e.get("take_profit") or 0), float(e.get("tp_sell_fraction") or 1)
    sl, trail, act = (
        float(e.get("stop_loss") or 0),
        float(e.get("trailing_stop") or 0),
        float(e.get("trailing_activation") or 0),
    )
    hold = float(e.get("max_hold_secs") or 0)
    stale = float(e.get("stale_secs") or 0)
    mig = migration_sell(e)
    if not (tp or sl or trail or hold or stale or mig):
        return None
    stages = []
    if tp > 0:
        stages.append(
            {
                "gainPercent": _round(tp * 100, 2),
                "sellPercent": _round(min(100.0, frac * 100), 2),
                "delaySellAllSeconds": 0,
            }
        )
        if stages[-1]["sellPercent"] < 100:  # the rest stays with the timer and the stops, behind an unreachable gain
            stages.append(dict(UNREACHABLE_STAGE))
            notes.append("partial take-profit: a +10000% stage selling 100 % comes last (OKTech: 100 % must be last)")
    elif 0 < hold <= 60:
        stages.append(
            {"gainPercent": None, "sellPercent": 100, "delaySellAllSeconds": 0, "sellAfterMs": int(hold * 1000)}
        )
    else:  # OKTech needs at least one stage; an unreachable gain keeps the timer / stop-loss in charge
        stages.append(dict(UNREACHABLE_STAGE))
        notes.append(
            "no take-profit: exit by stop-loss / timer (one +10000% stage satisfies OKTech's 'at least one stage')"
        )
    out: dict[str, Any] = {
        "enabled": True,
        "stages": stages,
        "sellTipAmount": sell_fee_sol,
        "sellPriorityFeeSol": sell_fee_sol,
    }
    if hold > 0:
        out |= {"sellAfterSeconds": _round(hold, 1), "sellPercent": 100}
    else:
        out |= {"sellPercent": 0}  # no sell-all timer
    if sl > 0:
        out["stopLoss"] = {
            "enabled": True,
            "stages": [{"percentLoss": _round(min(100.0, sl * 100), 2), "sellPercent": 100}],
            "confirmMs": 250,
        }
    if stale > 0:
        out["deadExit"] = {"enabled": True, "staleSeconds": max(1, int(round(stale))), "sellPercent": 100}
    if trail > 0:
        out["trailingStopLoss"] = {"enabled": True, "percentFromPeak": _round(trail * 100, 2), "sellPercent": 100,
                                   "activateAfterGainPercent": _round(act * 100, 2) if act > 0 else None,
                                   "confirmMs": 0}  # a dip from the peak sells at once  # fmt: skip
    if mig:
        out["migrationSell"] = mig
        notes.append(f"migration sell ({mig['sellPercent']:g} % {mig['delayMs']} ms after the coin migrates) has never "
                     "fired on one of our live trades, and OKTech accepts settings it then ignores (playbook fact 8): "
                     "check on the tape that the first coin that migrates while held is sold then")  # fmt: skip
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
        "buyAmount": _capped_size(float(rec["buy_sol"]), cfg, notes), "priorityFee": prio, "tipAmount": tip, "singleSnipe": True,
        "totalSnipes": max(1, min(100, int(cfg.oktech.verdict_max_buys))),
        "extraParams": execution_params(compute_units=cfg.oktech.compute_units),
    }  # fmt: skip
    sell = autosell(rec.get("exit"), notes, sell_fee_sol=cfg.oktech.sell_fee_sol)
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
            wallet["maxMcapUsd"] = _bucket_usd(
                float(filt["max_entry_mcap_sol"]) * sol_usd
            )  # 2 significant figures: a price tick is not a new sniper
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
        "buyAmount": _capped_size(float(rec["size_sol"]), cfg, notes), "priorityFee": prio, "tipAmount": tip,
        "singleSnipe": True, "totalSnipes": max(1, min(100, int(cfg.oktech.verdict_max_buys))), "speedMode": cfg.oktech.jetstream,
        "extraParams": execution_params(compute_units=cfg.oktech.compute_units),
    }  # fmt: skip
    if cfg.oktech.preconf:
        config["preconf"] = True
    own = rec.get("own_exit_params")
    exit_line = "sells when the copied wallet sells"
    if own and rec.get("own_exit_beats_mirroring"):
        sell = autosell(own | {"copy_exit": True}, notes, sell_fee_sol=cfg.oktech.sell_fee_sol)
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
    return PlannedSniper("vcopy", a, f"snipetech verdict copy {a[:6]}", config, summary, notes)


# The exit presets snipers run today, by name (`oktech copylist --exit`; `lab` on every book sniper): the exit menu's
# entries (exits_menu.py), so the lab scores and measure names exactly what is sent. `ride` is on the menu too, scored
# and shadowed, but no sniper runs it until the owner verifies it ([book] verified_exits).
EXITS = {name: dict(MENU[name].params) for name in ("lab", "hold60")}

# What any exit a live sniper is given must have (validate_exit). A price stop is the one exit we have not seen fail on a
# sniper's last buy (DTAU: dead exit, copy exit and timer all silent); one wider than 35 % sells after the crash, not
# before it (5 Nov: a 15 % stop filled at -57 % and -70 % on bundle dumps).
EXIT_MAX_STOP_LOSS = 0.35
EXIT_MAX_HOLD_SECS = 600.0  # the longest hold the lab scores (exit_set, default_grid)
EXIT_MIN_STALE_SECS = 1.0  # OKTech's deadExit takes whole seconds, 1 at least
EXIT_MIN_TAKE_PROFIT = 0.05  # a take-profit under +5 % sells on noise and pays a second transaction to do it


def validate_exit(params: dict | None, cfg: Config | None = None) -> list[str]:
    """What is unsafe about an exit before a live sniper may run it, in plain words; empty when it may be sent: a
    stop-loss of at most 35 %, a sell-all timer of at most 600 s, a dead exit (if any) of 1 s or more, a take-profit
    (if any) of +5 % or more. With the config, an exit in the lab's form (it carries `copy_exit_pct`, the copy exit it
    was scored with) must have the copy exit copy snipers send ([oktech] copy_exit_threshold_percent)."""
    e = params or {}

    def num(key: str) -> float:
        try:
            v = float(e.get(key) or 0)
        except (TypeError, ValueError):
            return float("nan")
        return v if math.isfinite(v) else float("nan")

    out: list[str] = []
    sl, hold, stale, tp = num("stop_loss"), num("max_hold_secs"), num("stale_secs"), num("take_profit")
    if not sl > 0:
        out.append("no stop-loss")
    elif sl > EXIT_MAX_STOP_LOSS + 1e-9:
        out.append(f"stop-loss at -{sl:.0%} is wider than -{EXIT_MAX_STOP_LOSS:.0%}")
    if not hold > 0:
        out.append("no sell-all timer")
    elif hold > EXIT_MAX_HOLD_SECS + 1e-9:
        out.append(f"holds up to {hold:g} s, longer than {EXIT_MAX_HOLD_SECS:g} s")
    if not stale >= 0 or 0 < stale < EXIT_MIN_STALE_SECS - 1e-9:
        out.append(f"dead exit after {stale:g} s: OKTech's shortest is {EXIT_MIN_STALE_SECS:g} s")
    if not tp >= 0 or 0 < tp < EXIT_MIN_TAKE_PROFIT - 1e-9:
        out.append(f"take-profit at +{tp:.0%} sells on noise (at least +{EXIT_MIN_TAKE_PROFIT:.0%})")
    if cfg is not None and e.get("copy_exit_pct") is not None:
        scored, sent = num("copy_exit_pct"), float(cfg.oktech.copy_exit_threshold_percent) / 100.0
        if not abs(scored - sent) <= 1e-9:
            out.append(f"scored with the copy exit at {scored:.0%}, but snipers send {sent:.0%} "
                       "([oktech] copy_exit_threshold_percent)")  # fmt: skip
    return out


def list_copy_sniper(address: str, label: str, min_sol: float, buy_sol: float, max_buys: int = 5,
                     exit_params: dict | None = None, cfg: Config | None = None, exit_name: str = "lab",
                     priority_fee: float | None = None, tip: float | None = None, send_count: int | None = None,
                     tag: str = "", compute_units: int | None = None, salt: str = "") -> PlannedSniper:  # fmt: skip
    """One capped `ctbot` sniper copying a wallet from the lab's by-wallet ranking (the live landing test):
    only its buys >= min_sol, buy_sol per copy, stops after max_buys buys, sells when it sells (copy exit) or on
    the lab's own exit (default: dead exit 1 s, stop-loss 30 %, 300 s). `priority_fee` / `tip` (SOL) are the buy
    bid (the book's arms); sells always bid `[oktech] sell_fee_sol`. `send_count` / `compute_units` go to
    extraParams (see execution_params); `salt` makes a fresh externalId for a replacement with the same settings."""
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
    cu = compute_units if compute_units is not None else cfg.oktech.compute_units
    config["extraParams"] = execution_params(send_count, cu)
    if cfg.oktech.preconf:
        config["preconf"] = True
    sell = autosell(ex | {"copy_exit": True}, notes, sell_fee_sol=cfg.oktech.sell_fee_sol)
    if sell:
        config["autoSell"] = sell
    summary = [f"copies {label} ({address[:6]}…): only its buys >= {min_sol:g} SOL, {buy_sol:g} SOL per copy, stops after "
               f"{config['totalSnipes']} buys", f"exit: when it sells, or {_describe_exit(ex)}",
               f"fees: priority {prio:g} SOL + tip {tip:g} SOL per buy, sells at {cfg.oktech.sell_fee_sol:g} + {cfg.oktech.sell_fee_sol:g} SOL, "
               f"send count {config['extraParams']['globalSendCount']}, no seed account"
               + (f", {cu:,} compute units" if cu else "") + (", preconfs requested" if config.get("preconf") else ""),
               "the test: every fill is checked on the tape for the slot it landed in relative to the copied buy"]  # fmt: skip
    name = f"snipetech copy {label[:18]} {address[:6]}" + (f" {tag}" if tag else "")
    return PlannedSniper("copy", address, name[:60], config, summary, notes, salt=salt)


def _describe_exit(e: dict | None) -> str:
    if not e:
        return "none"
    parts = []
    if e.get("stale_secs"):
        parts.append(f"sell once nothing trades for {e['stale_secs']:g}s")
    if e.get("take_profit"):
        parts.append(
            f"TP +{e['take_profit']:.0%}"
            + (f" ({e.get('tp_sell_fraction', 1):.0%})" if e.get("tp_sell_fraction", 1) < 1 else "")
        )
    if e.get("stop_loss"):
        parts.append(f"SL -{e['stop_loss']:.0%}")
    if e.get("trailing_stop"):
        parts.append(f"trailing {e['trailing_stop']:.0%}")
    if e.get("max_hold_secs"):
        parts.append(f"max hold {e['max_hold_secs']:.0f}s")
    if e.get("migration_sell_frac"):
        parts.append(f"sell {float(e['migration_sell_frac']):.0%} {float(e.get('migration_sell_delay') or 0):g}s after "
                     "the coin migrates")  # fmt: skip
    return ", ".join(parts) or "none"


def build_plan(
    store: Store, cfg: Config, kinds: tuple[str, ...] = KINDS, sol_usd: float | None = None
) -> list[PlannedSniper]:
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
    """What `push` changes. Only its own rows count (verdict copies "vcopy" and dev snipers): snipers made with
    copylist, the book, probes and strategies are never push's to replace or prune."""
    by_source = {(r["kind"], r["source"]): r for r in recorded if r.get("kind") in PUSH_KINDS.values()}
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


async def apply(client: OktechClient, store: Store, plan: SyncPlan, wallet: str | None, prune: bool = False,
                held: list[dict] | None = None) -> list[dict]:  # fmt: skip
    """Create new and replacement snipers; retire an old one only after its replacement exists, and never while the
    wallet holds a coin (`held` = open positions; None = unknown, which also blocks every retire: OKTech's task
    documents do not say which sniper holds what, and a stopped sniper strands its coin)."""
    results: list[dict] = []
    created_for: set[str] = set()  # sources whose replacement exists
    creates = [(None, p) for p in plan.create] + [(old, p) for old, p in plan.replace]
    for i, (old, p) in enumerate(creates):
        if i and i % 18 == 0:
            await asyncio.sleep(61)  # 20 creates a minute per key
        try:
            ans = await client.create(p.name, p.config, wallet, p.external_id)
        except OktechError as exc:
            results.append({"action": "create failed", "name": p.name, "error": str(exc)})
            continue
        task = ans.get("task") or {}
        tid = task.get("id")
        if not tid and ans.get("duplicate"):  # the 409 body carries no task: find it by its externalId
            with contextlib.suppress(OktechError):
                tid = next((t.get("id") for t in await client.tasks() if t.get("externalId") == p.external_id), None)
        if not tid:
            results.append({"action": "create failed", "name": p.name, "error": "no task id in OKTech's answer"})
            continue
        store.add_oktech_sniper({"id": tid, "external_id": p.external_id, "kind": p.kind, "source": p.source, "name": p.name,
                                 "config_hash": p.config_hash, "payload": p.config, "created_at": int(time.time())})  # fmt: skip
        results.append({"action": "kept (already there)" if ans.get("duplicate") else "created", "name": p.name, "id": tid,
                        "status": task.get("status")})  # fmt: skip
        created_for.add(p.source)
    retire = [old for old, p in plan.replace if p.source in created_for] + (plan.remove if prune else [])
    if retire and (held is None or held):
        why = "the wallet holds " + ", ".join(str(h.get("mint"))[:8] for h in held) if held else "holdings unknown"
        for old in retire:
            results.append({"action": f"kept: {why} (a stopped sniper strands its coin); retire it once nothing is held",
                            "name": old.get("name"), "id": old.get("id")})  # fmt: skip
        return results
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
            "totalSnipes": int(snipes), "autoSell": sell, "extraParams": execution_params(), "preconf": True}  # fmt: skip
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
    "extraParams": execution_params(),
    "preexec": {"enabled": True},
    "preconf": True,  # the account is approved; ignored by OKTech where a task type is gated
}


def _whole_hunt(value: Any, name: str) -> int:
    """A hunt threshold as OKTech takes it: a whole number. OKTech ignored 2.5 / 1.0 / null on 5 Nov 22:11 and bought
    every launch as if no threshold were set, so anything else is refused here rather than sent."""
    v = float(value or 0)
    if v < 0 or abs(v - round(v)) > 1e-9:
        raise ValueError(f"OKTech takes hunt thresholds as whole numbers only: {name} = {v:g} would be ignored")
    return round(v)


# The lab scores a MIG rule only on exits that sell within this many seconds of the buy (analysis/lab.py, MIG_CAP: the
# PumpSwap tape must hold every second of it, and research round 1's coins lost when held 900 s); a MIG plan with a
# longer exit would be a rule nobody scored.
MIG_MAX_HOLD_SECS = 300.0


def _whole_secs(value: Any, name: str) -> int:
    """A time OKTech takes in whole seconds (MIG's Migration Window). A fraction would not be the window the lab
    scored, so it is refused here rather than sent."""
    v = float(value or 0)
    if not math.isfinite(v) or v < 0 or abs(v - round(v)) > 1e-9:
        raise ValueError(f"OKTech takes {name} in whole seconds: {v:g} s is not a window it can run as scored")
    return round(v)


def strategy_sniper(
    strategy: dict, max_buys: int = 5, via: str = "hunt", preexec: bool = True, stale: float | None = 60
) -> PlannedSniper:
    """A frozen lab / paper strategy as a `normal` OKTech launch sniper (monitorProgram on pump.fun), a `hunt` sniper
    for the launch-window rules, or a `mig` sniper (OKTech's pump migration sniper) for a MIG rule.

    Most of the lab's big-buy triggers fire within 2 s of the launch (measured on the tape), so they map to
    the launch sniper with OKTech's crowd-buy filter: buy once early buyers have put in >= X SOL."""
    trig, ex = strategy["trigger"], dict(strategy["exit"])
    notes: list[str] = []
    buy = float(strategy.get("buy_sol") or 0.2)
    kind = trig.get("kind")
    config: dict[str, Any] = {"mode": "normal", "platforms": ["pumpfun"], "monitorProgram": True, "buyAmount": _round(buy),
                              "priorityFee": 0.001, "tipAmount": 0.002, "singleSnipe": True,
                              "totalSnipes": max(1, min(100, int(max_buys))), **EXECUTION}  # fmt: skip
    if not preexec:
        config["preexec"] = {"enabled": False}
    if kind == "mig":
        watched, dry = "every pump.fun coin as it moves to PumpSwap", " with OKTech's local dry run on"
    else:
        watched, dry = "every new pump.fun launch", " and only after a local dry run of the create + buy succeeds"
    summary = [f"{watched}, {buy:g} SOL per buy, minimum fees (0.001 priority + 0.002 tip)",
               f"stops buying by itself after {config['totalSnipes']} buys; earlier positions keep their exits, but the buy that uses up the cap can be left with no exit (DTAU, 4siK)",
               "buys straight into the wallet (no seed account)" + (dry if preexec else ", no local dry run")]  # fmt: skip
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
            # `crowdBuys` is a block of OKTech's *shapes* mode (an account grant): the app emits it only with mode
            # "shapes", and under mode "normal" it did not gate anything on 5 Nov (fills every ~10 s = the launch
            # rate). Until the account has shapes, there is no crowd variant.
            raise ValueError(
                "crowd buys gate only in OKTech's shapes mode, which this account does not have: use --via hunt"
            )
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
            thresholds: dict[str, Any] = {
                "minTotalSol": int(math.ceil(min_sol * 1.25)),
                "maxDevBuySol": 1,
                "requireKnownBot": False,
            }
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
    elif kind == "hunt":
        # the lab scored exactly this rule (analysis/lab.py `_hunt`): send it as it was scored, nothing approximated
        config["mode"] = "hunt"
        thresholds = {"minTotalSol": _whole_hunt(trig.get("sol"), "minTotalSol")}
        if trig.get("dev_max") is not None:
            thresholds["maxDevBuySol"] = _whole_hunt(trig["dev_max"], "maxDevBuySol")
        if int(trig.get("buyers") or 0) > 0:
            thresholds["minUniqueBuyers"] = _whole_hunt(trig["buyers"], "minUniqueBuyers")
        if int(trig.get("max_buyers") or 0) > 0:
            thresholds["maxUniqueBuyers"] = _whole_hunt(trig["max_buyers"], "maxUniqueBuyers")
        thresholds["requireKnownBot"] = False
        window_ms = _whole_hunt(float(trig.get("window") or 0) * 1000, "timeWindowMs")
        if window_ms <= 0:
            raise ValueError("a hunt trigger needs its window (timeWindowMs) as a whole number of milliseconds")
        config["hunt"] = {"timeWindowMs": window_ms, "triggerMode": "all", "thresholds": thresholds,
                          "knownBotPrograms": [], "knownBotWallets": [], "knownBotLuts": [], "knownBotAccounts": [],
                          "useKnownBotDefaults": True}  # fmt: skip
        summary.append(f"only after >= {thresholds['minTotalSol']} SOL in total is bought within {window_ms / 1000:g} s of "
                       "the launch, the creator's own buy included"
                       + (f"; skips a launch whose creator bought more than {thresholds['maxDevBuySol']} SOL" if "maxDevBuySol" in thresholds else "")
                       + (f"; needs >= {thresholds['minUniqueBuyers']} wallets" if "minUniqueBuyers" in thresholds else "")
                       + (f"; skips it once more than {thresholds['maxUniqueBuyers']} wallets bought" if "maxUniqueBuyers" in thresholds else "")
                       + " (OKTech hunt, exactly as the lab scored it)")  # fmt: skip
        notes.append("a hunt sniper fires once per start (5 Nov): keep it armed with `oktech cycle`, and keep the "
                     "unsold-coin watch and /sell ready: hunt fills have been left without a sell (HOUSE, CAT)")  # fmt: skip
    elif kind == "mig":
        # the lab scored this rule (analysis/lab.py `_mig`): OKTech MIG buys the coin on PumpSwap once it has migrated,
        # only when the migration came within migrationExpirySec of the create
        config["mode"] = "mig"
        del config["monitorProgram"]  # a normal-mode switch (docs/oktech-features.md, "Normal extras")
        window = _whole_secs(trig.get("window"), "the Migration Window (migrationExpirySec)")
        if not window:
            # the form's default window is 10 s, and how to send "no window" is not known: an any-time rule sent without
            # one would most likely run as the 10 s rule, which the lab scores as a rule of its own
            raise ValueError("an any-time MIG rule cannot be sent as scored: OKTech's Migration Window defaults to 10 s, "
                             "and how to send no window is not known (ask OKTech first)")  # fmt: skip
        if trig.get("dev_paid") is not None:
            # the lab's paid-creator split (lab.dev_paid_earlier): OKTech MIG has no filter for it. Each coin's creator
            # is a fresh wallet, so blockedCreators cannot do it; web.tracking on the paying creators is untested (7 Nov)
            raise ValueError("a MIG rule split on whether an earlier coin's creator paid the coin's creator cannot be "
                             "sent as scored: OKTech MIG has no such filter, and sent without it the rule would run "
                             "as plain MIG")  # fmt: skip
        if float(ex.get("migration_sell_frac") or 0) > 0:
            # a MIG buy lands after its coin migrated: the lab never fires a migration sell on one (sim/exits.py), and
            # whether OKTech would fire it on a coin bought after its migration is not known
            raise ValueError("a MIG exit with a migration sell cannot be sent as scored: a MIG buy comes after the "
                             "migration it would sell into, so the lab never fired it")  # fmt: skip
        hold = float(ex.get("max_hold_secs") or 0)
        if not 0 < hold <= MIG_MAX_HOLD_SECS:
            raise ValueError(f"the lab scores MIG only on exits that sell within {MIG_MAX_HOLD_SECS:g} s of the buy; "
                             f"this one {'holds ' + format(hold, 'g') + ' s' if hold else 'has no timer'}")  # fmt: skip
        config["migrationExpirySec"] = window
        summary.append(f"only coins whose migration comes within {window} s of their create (OKTech MIG's Migration "
                       "Window, as the lab scored it)")  # fmt: skip
        notes.append("MIG has never run on this account, and a feature counts as working only after one live trade "
                     "shows it fired (playbook 8): check the first fill on the tape (a PumpSwap buy behind the first 3 "
                     "PumpSwap trades after the migration's slot, a slot or so after it, where the lab assumes it "
                     "lands) and that its exits fire")  # fmt: skip
        # since 7 Nov (research round 2, build 1) solana/sell.py sells a coin whose curve has completed on its PumpSwap
        # pool; still missing live, out of scope of the MIG lab: operator/live_test.py accepts hunt strategies only
        notes.append("/sell sells these coins on PumpSwap from the sniper wallet, and so does the unsold-coin watch's "
                     "/sell line: every MIG position is on PumpSwap (solana/sell.py, built from OKTech's own landed "
                     "PumpSwap sale). If a position's exits do not fire (hunt fills have been left unsold: HOUSE, CAT), "
                     "send it; a coin PumpSwap's sale refuses (a cashback coin, two pools on record) has to be sold by "
                     "hand in OKTech or Axiom")  # fmt: skip
        notes.append("the automatic test runs hunt strategies only (operator/live_test.py): a MIG strategy that passes "
                     "the paper test is not started by itself")  # fmt: skip
        notes.append("keep the recorder's recent-migrations mode on ([live] amm_recent_secs, 1800, over LaserStream; "
                     "index_amm off, as on the server): it records the PumpSwap fill and its sells, which the measure "
                     "stage and the unsold-coin watch read, and it is the only tape a MIG rule is scored on")  # fmt: skip
    elif kind == "kol":
        raise ValueError("a KOL trigger is a copy (ctbot) sniper, not a launch sniper")
    if trig.get("plain"):
        summary.append("normal coins only: skip mayhem and holder-reward coins (they lose on paper)")
        notes.append("OKTech documents no switch that skips mayhem / holder-reward coins: find it on the Sniper form, "
                     "or accept them (they cost a little on paper)")  # fmt: skip
    ex.setdefault("sell_on_dev_sell", False)
    # a MIG plan is the scored exit exactly (1:1): it sells within MIG_MAX_HOLD_SECS anyway, so no dead exit is added
    if stale and not ex.get("stale_secs") and kind != "mig":
        # a coin with no trades for a minute is dead: OKTech's dead exit frees the (one-shot) sniper instead of
        # holding to the timer. Not part of the paper exit, so it is a cap on hold time, never a widening.
        ex["stale_secs"] = float(stale)
    sell = autosell(ex, notes)
    if sell:
        config["autoSell"] = sell
        summary.append(f"exit: {_describe_exit(ex)}")
    return PlannedSniper("strategy", strategy.get("id") or kind, f"snipetech strategy: {strategy.get('label', kind)}"[:80], config,
                         summary=summary, notes=notes)  # fmt: skip


def replica_from_study(
    study: dict, cfg: Config, buy_sol: float, sol_usd: float | None, max_buys: int = 5
) -> PlannedSniper:
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
                              "priorityFee": prio, "tipAmount": tip, "singleSnipe": True, "totalSnipes": max(1, min(100, int(max_buys))),
                              "extraParams": execution_params(compute_units=cfg.oktech.compute_units)}  # fmt: skip
    summary.append(
        f"capped: stops buying after {config['totalSnipes']} buys; earlier positions keep their exits, but the buy that uses up the cap can be left with no exit (DTAU, 4siK)"
    )
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
        summary.append(
            f"only launches linking a tweet ({tw['chosen_share']:.0%} of its entries vs {tw['others_share']:.0%} of the rest)"
        )
        notes.append("the tweet check reads the token's metadata file: it adds the fetch time to every entry")
    # the burst it enters on
    b5 = num.get("buys_5s") or num.get("uniq_buyers_30s")
    if b5 and b5["auc"] >= 0.7:
        signers = max(2, int(round(b5["chosen_median"] / 2)))
        # OKTech's crowd-buy block (`crowdBuys`) gates only in its shapes mode, which this account does not have; under
        # mode normal it is accepted and ignored (5 Nov: fills at the launch rate), so the burst rule is not sent
        notes.append(f"it enters on a burst (>= {signers} buyers, {b5['factor']} median {b5['chosen_median']:.0f}); OKTech can "
                     "only gate on that in shapes mode (not granted), so this sniper buys launches without the burst rule")  # fmt: skip
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
