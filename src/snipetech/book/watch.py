"""The watchdog for coins nobody is selling (plan item 2e).

OKTech sometimes never manages the buy that uses up a sniper's `totalSnipes`: on 6 Nov DTAU was bought as the paid
G2oZ sniper's last buy, a 21-slot quiet gap began 3 slots later, G2oZ sold 99.9 % of its bag at +261 s, the 300 s timer
passed, and nothing sold it until the owner did at +559 s (4siK on 5 Nov was the same). This module notices such a
coin about a minute after its first exit was due and tells the owner. It never sells: a sale from here is this repo
signing a trade, which only the owner can allow (CLAUDE.md rule 1).

For every coin the wallet holds (the holdings the book uses: `Service.open_positions`), it takes our open buys from
the tape (`trades.user` = our wallet, `is_buy` = 1; the tape, not `fills`, so the measure stage's lag does not delay
it), the local sniper that made each one (the one whose leader, `payload.ctbotWallets[0].address`, bought the coin in
the 20 slots before), and that sniper's own exits from its local payload. A coin is flagged when

* one of those exits was due (dead exit, copy exit, stop-loss, take-profit, trailing stop, timer) and nothing of ours
  sold within `GRACE_SECS` after it;
* the buying sniper is unknown, or no longer active on OKTech, and the coin has been held more than 360 s;
* the tape lags, or the sniper's exits are unknown: the coin is held past its timer (300 s by default) plus a minute.

It runs from the one-minute pass (`operate --commands`), and at the end of the book's run when that timer is off. A
flagged coin gets one Telegram decision line, repeated every 15 minutes while it is held, and one `events` row (kind
"unmanaged") for the weekly evidence. A coin with no buy of ours on the tape at all (an airdropped token) is not a coin
the book bought: it gets one line a day, never every 15 minutes. The coins on the owner's [operator] ignore_mints list
are not watched at all. operator/watch.json keeps the current hits; the operator's gate reads them."""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..models import Trade
from ..operator.paths import OperatorPaths
from ..positions import is_active

if TYPE_CHECKING:
    from ..config import Config
    from ..operator.notify import Notifier
    from ..store import Store

# Inside a coin the slots are the clock: block times are whole seconds. 0.214 s a slot is the value measured on 5-6
# Nov (block time regressed on slot every hour; six 300 s timers landed 1,395-1,411 slots after their buys). Kept here
# on purpose: the simulator's own constant (sim/market.py) is being changed separately.
SLOT_SECS = 0.214
WATCH_SECS = 1.5  # OKTech starts watching a position 1-2 s after the buy (it ignored a 1.7 s pause from +0.2 s, Cmg7)
GRACE_SECS = 60  # an exit not acted on within a minute is not OKTech being a little slow (its sells land in 0-2 slots)
DEFAULT_TIMER_SECS = 300  # every preset the book sends sells everything by 300 s
UNKNOWN_HOLD_SECS = DEFAULT_TIMER_SECS + GRACE_SECS  # a coin no active sniper of ours is behind
REPEAT_SECS = 15 * 60  # one line per coin per 15 minutes while it is held
STRAY_REPEAT_SECS = 86400  # a coin with no buy of ours on the tape (an airdrop): one line a day while it is held
LEADER_WINDOW_SLOTS = 20  # our copy lands within this many slots of the leader's buy (plan item 2b uses the same)
LOOKBACK_SECS = 3 * 86400  # our buys are looked up this far back
WINDOW_SLOTS = int(15 * 60 / SLOT_SECS) + 1  # a buy's exits are looked for in its first 15 minutes
MAX_ROWS = 20_000  # trades read per position, at most
TAPE_LAG_SECS = 60  # the tape's newest trade older than this: the tape lags
OKTECH_WAIT_SECS = 8  # OKTech's tasks waited for this long at most; past it the tape and the payloads judge the coin
OPEN_SHARE = 0.01  # a buy with less than 1 % of its tokens left is closed (a full sale leaves a few tokens behind)
STATE_FILE = "watch.json"  # in operator/: current hits, when each coin was first seen and last reported
HIT_MAX_AGE_SECS = 30 * 60  # the gate ignores hits the watch wrote longer ago than this
EXIT_ORDER = ("dead_exit", "copy_exit", "stop_loss", "trailing_stop", "take_profit", "timer")
REASONS = ("exits_not_acted_on", "sniper_inactive", "sniper_unknown", "past_timer", "not_on_tape")
_COLS = "sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, fee_bps, q_after, b_after"


@dataclass(frozen=True)
class ExitSpec:
    """The exits a sniper was created with, read from its payload (oktech.autosell and the copy exit)."""

    stale_secs: float | None = None  # autoSell.deadExit.staleSeconds
    stop_loss_pct: float | None = None  # autoSell.stopLoss: the smallest stage percentLoss
    timer_secs: float | None = None  # autoSell.sellAfterSeconds (with a sellPercent), or a stage's sellAfterMs
    copy_pct: float | None = None  # ctbotCopyExit.sellThresholdPercent
    take_profit_pct: float | None = None  # autoSell.stages: the smallest gainPercent
    trail_pct: float | None = None  # autoSell.trailingStopLoss.percentFromPeak
    trail_after_pct: float | None = None  # its activateAfterGainPercent (None: armed at once)


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _on(d: Any) -> bool:
    return isinstance(d, dict) and d.get("enabled", True) is not False


def exit_spec(payload: dict | None) -> ExitSpec | None:
    """The exits in a sniper's local payload; None when it has none (its exits are then unknown)."""
    p = payload if isinstance(payload, dict) else {}
    sell = p.get("autoSell") if _on(p.get("autoSell")) else {}
    de, sl, tr = sell.get("deadExit"), sell.get("stopLoss"), sell.get("trailingStopLoss")
    stale = _num(de.get("staleSeconds")) if _on(de) else None
    losses = [_num(s.get("percentLoss")) for s in (sl.get("stages") or []) if isinstance(s, dict)] if _on(sl) else []
    timers = [_num(sell.get("sellAfterSeconds"))] if (_num(sell.get("sellPercent")) or 0) > 0 else []
    gains: list[float | None] = []
    for s in sell.get("stages") or []:
        if isinstance(s, dict):
            timers.append((_num(s.get("sellAfterMs")) or 0) / 1000)
            gains.append(_num(s.get("gainPercent")))
    trail = _num(tr.get("percentFromPeak")) if _on(tr) else None
    ce = p.get("ctbotCopyExit")

    def low(xs: list[float | None]) -> float | None:
        return min((x for x in xs if x is not None and x > 0), default=None)

    spec = ExitSpec(stale_secs=stale if stale and stale > 0 else None, stop_loss_pct=low(losses),
                    timer_secs=low(timers), copy_pct=_num(ce.get("sellThresholdPercent")) if _on(ce) else None,
                    take_profit_pct=low(gains), trail_pct=trail if trail and trail > 0 else None,
                    trail_after_pct=_num(tr.get("activateAfterGainPercent")) if trail and trail > 0 else None)  # fmt: skip
    return None if spec == ExitSpec() else spec


def due_exits(buy: Trade, spec: ExitSpec | None, tape_after: list[Trade], leader: str | None, *, leader_bag: int = 0,
              elapsed: float | None = None, horizon: float | None = None) -> list[tuple[str, float]]:  # fmt: skip
    """The exits the sniper's own spec made due after our buy: (name, seconds after the buy), earliest first.

    `tape_after`: the coin's trades after our buy, in tape order. `leader_bag`: the leader's tokens just before our
    buy (its trades after it are in `tape_after`). `elapsed`: wall-clock seconds since the buy, for the timer.
    `horizon`: seconds after the buy up to which the tape is complete, for a quiet stretch that is still going on.

    * dead exit: a quiet stretch of `stale_secs` counted from the later of the previous trade and OKTech's watch start;
    * copy exit: the leader's first single sale of at least `copy_pct` % of the coins it held then;
    * stop-loss / take-profit / trailing stop: the price after a trade against our buy's price, once OKTech watches;
    * timer: `timer_secs` after the buy."""
    if spec is None:
        return []
    out: dict[str, float] = {}
    until = min((x for x in (elapsed, horizon) if x is not None), default=None)
    entry = buy.quote / buy.base if buy.base else 0.0
    bag, prev, peak = max(0, int(leader_bag)), 0.0, 0.0
    armed = spec.trail_after_pct is None
    for t in tape_after:
        at = (t.slot - buy.slot) * SLOT_SECS
        if until is not None and at > until:
            break  # the tape is not complete that far yet
        if spec.stale_secs and "dead_exit" not in out:
            quiet_from = max(prev, WATCH_SECS)
            if at - quiet_from >= spec.stale_secs:
                out["dead_exit"] = quiet_from + spec.stale_secs
        prev = at
        if leader is not None and t.user == leader:
            if t.is_buy:
                bag += int(t.base)
            else:
                share = t.base / bag if bag > 0 else 1.0  # coins bought before the tape began: count it as all of them
                if spec.copy_pct is not None and "copy_exit" not in out and share * 100 >= spec.copy_pct - 1e-9:
                    out["copy_exit"] = max(at, WATCH_SECS)
                bag = max(0, bag - int(t.base))
        if at < WATCH_SECS or not entry or not t.b_after:
            continue
        px = t.q_after / t.b_after / entry  # the price against our buy's
        if spec.stop_loss_pct and "stop_loss" not in out and px <= 1 - spec.stop_loss_pct / 100:
            out["stop_loss"] = at
        if spec.take_profit_pct and "take_profit" not in out and px >= 1 + spec.take_profit_pct / 100:
            out["take_profit"] = at
        if spec.trail_pct and "trailing_stop" not in out:
            peak = max(peak, px)
            armed = armed or peak >= 1 + (spec.trail_after_pct or 0) / 100
            if armed and px <= peak * (1 - spec.trail_pct / 100):
                out["trailing_stop"] = at
    if spec.stale_secs and "dead_exit" not in out and until is not None:
        quiet_from = max(prev, WATCH_SECS)  # the quiet stretch still going on at the end of what the tape has seen
        if until - quiet_from >= spec.stale_secs:
            out["dead_exit"] = quiet_from + spec.stale_secs
    if spec.timer_secs and elapsed is not None and elapsed >= spec.timer_secs:
        out["timer"] = spec.timer_secs
    return sorted(out.items(), key=lambda kv: (kv[1], EXIT_ORDER.index(kv[0])))


# -- reading the tape and the local snipers ------------------------------------------------------------------------


def _is_mint(m: Any) -> bool:
    return isinstance(m, str) and 32 <= len(m) <= 44


def _trades(store: Store, sql: str, params: tuple) -> list[Trade]:
    return [Trade(sig=r[0], ev=r[1], slot=r[2], pos=r[3], ts=r[4], mint=r[5], venue=r[6], user=r[7], is_buy=bool(r[8]),
                  quote=r[9], base=r[10], fee=r[11], fee_bps=r[12], q_after=r[13], b_after=r[14])
            for r in store._q(sql, params)]  # fmt: skip


def _open_buys(ours: list[Trade]) -> list[tuple[Trade, bool]]:
    """Our buys in a coin that still hold tokens once our sales are matched to them oldest first, each with whether a
    sale of ours took tokens from it. A sale matched wholly to an older buy leaves a younger one unsold: that buy (a
    sniper's cap-using second lot) is judged by its own exits."""
    lots: list[list] = []  # [buy, tokens left, sold since]
    for t in ours:
        if t.is_buy:
            lots.append([t, int(t.base), False])
            continue
        left = int(t.base)
        for lot in lots:
            take = min(lot[1], left)
            if take > 0:
                lot[1], left, lot[2] = lot[1] - take, left - take, True
    return [(b, sold) for b, rest, sold in lots if rest > b.base * OPEN_SHARE]


def _leader(payload: dict) -> str | None:
    ws = payload.get("ctbotWallets") or []
    first = ws[0] if isinstance(ws, list) and ws else None
    a = first.get("address") if isinstance(first, dict) else first
    return a if isinstance(a, str) and a else None


def _buying_sniper(store: Store, mint: str, buy: Trade) -> dict | None:
    """The local copy sniper behind our buy: its leader bought the coin in the 20 slots before it (the closest such
    leader buy wins; among the snipers alive then that copy that leader, the newest). None when no sniper of ours
    explains the buy. The coin's few buyers come first; only the payloads that name one of them are parsed."""
    rows = store._q("SELECT slot, pos, user FROM trades WHERE mint=? AND is_buy=1 AND slot>=? AND slot<=?",
                    (mint, buy.slot - LEADER_WINDOW_SLOTS, buy.slot))  # fmt: skip
    buyers = sorted({(s, p, u) for s, p, u in rows if (s, p) < (buy.slot, buy.pos) and u != buy.user}, reverse=True)
    users = list(dict.fromkeys(u for _, _, u in buyers))[:50]  # closest first
    if not users:
        return None
    named = " OR ".join("payload LIKE ?" for _ in users)  # a prefilter: the leader is checked exactly below
    snipers = store._q(f"SELECT id, payload, created_at FROM oktech_snipers WHERE created_at <= ? "
                       f"AND (removed_at IS NULL OR removed_at >= ?) AND ({named})",
                       (int(buy.ts) + 60, int(buy.ts) - 60, *(f"%{u}%" for u in users)))  # fmt: skip
    by_leader: dict[str, list[dict]] = {}
    for sid, payload, created in snipers:
        try:
            p = json.loads(payload or "{}")
        except ValueError:
            continue
        lead = _leader(p) if isinstance(p, dict) else None
        if lead in users:
            by_leader.setdefault(lead, []).append(
                {"id": str(sid), "payload": p, "created_at": created or 0, "leader": lead}
            )
    for user in users:
        if user in by_leader:
            cands = sorted(by_leader[user], key=lambda r: r["created_at"], reverse=True)
            return cands[0] | {"also": [r["id"] for r in cands[1:]]}
    return None


def _tape_last(store: Store, now: float) -> int | None:
    rows = store._q("SELECT ts FROM trades WHERE ts<=? ORDER BY ts DESC LIMIT 1", (int(now),))
    return int(rows[0][0]) if rows else None


def _position(store: Store, mint: str, buy: Trade, sold_after: bool, status: dict[str, str] | None, now: float,
              tape_last: int | None, lagging: bool, grace_s: float) -> dict:  # fmt: skip
    sniper = _buying_sniper(store, mint, buy)
    spec = exit_spec(sniper["payload"]) if sniper else None
    lead = sniper["leader"] if sniper else None
    due: list[tuple[str, float]] = []
    if spec is not None:
        after = _trades(store, f"SELECT {_COLS} FROM trades WHERE mint=? AND slot>=? AND slot<=? AND ts<=? "
                               "ORDER BY slot, pos, ev LIMIT ?", (mint, buy.slot, buy.slot + WINDOW_SLOTS, int(now), MAX_ROWS))  # fmt: skip
        after = [t for t in after if (t.slot, t.pos, t.ev) > (buy.slot, buy.pos, buy.ev)]
        bag = 0
        if spec.copy_pct is not None and lead:
            # the coin's index (`+user` keeps SQLite off the leader's trades in every other coin)
            bag = int(store._q("SELECT COALESCE(SUM(CASE WHEN is_buy=1 THEN base ELSE -base END), 0) FROM trades "
                               "WHERE mint=? AND +user=? AND (slot, pos) < (?, ?)",
                               (mint, lead, buy.slot, buy.pos))[0][0] or 0)  # fmt: skip
        due = due_exits(buy, spec, after, lead, leader_bag=bag, elapsed=now - buy.ts,
                        horizon=None if tape_last is None else tape_last - buy.ts)  # fmt: skip
    rules: list[tuple[float, str]] = []
    if due and not sold_after:
        rules.append((buy.ts + due[0][1] + grace_s, "exits_not_acted_on"))
    sniper_status = None
    if sniper is None:
        rules.append((buy.ts + UNKNOWN_HOLD_SECS, "sniper_unknown"))
    elif status is not None:  # OKTech answered: a sniper missing from its tasks was deleted
        sniper_status = status.get(sniper["id"], "gone")
        if not is_active(sniper_status):
            rules.append((buy.ts + UNKNOWN_HOLD_SECS, "sniper_inactive"))
    timer = spec.timer_secs if spec else None
    if spec is None or timer or lagging:
        rules.append((buy.ts + (timer or DEFAULT_TIMER_SECS) + grace_s, "past_timer"))
    flag_at, reason = min(rules, key=lambda r: (r[0], REASONS.index(r[1]))) if rules else (math.inf, None)
    return {
        "mint": mint, "reason": reason, "flag_at": flag_at, "start": float(buy.ts), "held_secs": now - buy.ts,
        "bought_at": float(buy.ts), "buy_sig": buy.sig, "buy_slot": int(buy.slot), "buy_sol": (buy.quote + buy.fee) / 1e9,
        "leader": lead, "sniper_id": sniper["id"] if sniper else None, "also": sniper["also"] if sniper else [],
        "sniper_status": sniper_status, "timer_secs": timer, "lagging": lagging,
        "due": [{"exit": n, "after_secs": round(at, 3), "at": buy.ts + at, "what": _what(n, spec, lead)} for n, at in due],
    }  # fmt: skip


def unmanaged(store: Store, wallet: str, holdings: list[dict], tasks: list[dict] | None, now: float,
              grace_s: float = GRACE_SECS, *, first_seen: dict[str, float] | None = None) -> list[dict]:  # fmt: skip
    """The held coins nobody is selling, as of `now` (see the module docstring for the rules). `holdings`: the
    wallet's coins (mint, value_sol). `tasks`: OKTech's tasks (id, status), or None when OKTech did not answer (then a
    sniper's status is not judged). `first_seen`: when the watch first saw each coin held, for a coin whose buy is
    not on the tape (absent: now). Reads only: the tape and the local snipers, bounded per coin."""
    coins: dict[str, dict] = {}
    for h in holdings or []:
        m = h.get("mint")
        if not _is_mint(m):
            continue
        if m in coins:  # two token accounts of one coin: one coin, worth both (unknown if either is unpriced)
            a, b = coins[m].get("value_sol"), h.get("value_sol")
            coins[m] = coins[m] | {"value_sol": None if a is None or b is None else float(a) + float(b)}
        else:
            coins[m] = h
    if not coins or not wallet:
        return []
    status = (
        None if tasks is None else {str(t.get("id")): str(t.get("status")) for t in tasks if t.get("id") is not None}
    )
    tape_last = _tape_last(store, now)
    lagging = tape_last is None or now - tape_last > TAPE_LAG_SECS
    out = []
    for mint, h in coins.items():
        # our wallet's index (`+mint`, `+ts`): a coin can have 100,000 trades, the book wallet trades a few a day
        ours = _trades(store, f"SELECT {_COLS} FROM trades WHERE user=? AND +mint=? AND +ts>=? AND +ts<=? "
                              "ORDER BY slot, pos, ev LIMIT 2000", (wallet, mint, int(now - LOOKBACK_SECS), int(now)))  # fmt: skip
        lots = _open_buys(ours)
        if lots:
            hit = min((_position(store, mint, b, sold, status, now, tape_last, lagging, grace_s) for b, sold in lots),
                      key=lambda x: x["flag_at"])  # fmt: skip
        else:  # no open buy of ours on the tape explains the coin: the buy is not recorded yet, or is older
            start = max([float((first_seen or {}).get(mint, now))] + [float(t.ts) for t in ours])
            # stray: no buy of ours at all on the tape, so not a coin the book bought (an airdrop): said once a day
            hit = {"mint": mint, "reason": "not_on_tape", "flag_at": start + UNKNOWN_HOLD_SECS, "start": start,
                   "held_secs": now - start, "bought_at": None, "buy_sig": None, "buy_slot": None, "buy_sol": None,
                   "leader": None, "sniper_id": None, "also": [], "sniper_status": None, "timer_secs": None,
                   "lagging": lagging, "due": [], "stray": not any(t.is_buy for t in ours)}  # fmt: skip
        if now >= hit["flag_at"]:
            out.append(hit | {"value_sol": h.get("value_sol")})
    return out


# -- telling the owner -----------------------------------------------------------------------------------------------


def _what(name: str, spec: ExitSpec | None, leader: str | None) -> str:
    s = spec or ExitSpec()
    return {
        "dead_exit": f"no trade for {s.stale_secs or 0:g} s",
        "copy_exit": f"{f'{leader[:6]}…' if leader else 'the copied wallet'} sold at least {s.copy_pct or 0:g} % of its coins",
        "stop_loss": f"the price fell {s.stop_loss_pct or 0:g} % below our buy",
        "take_profit": f"the price rose {s.take_profit_pct or 0:g} % above our buy",
        "trailing_stop": f"the price fell {s.trail_pct or 0:g} % from its peak",
        "timer": f"its {s.timer_secs or 0:g} s timer",
    }.get(name, name)


def _hms(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(ts))


def _dur(secs: float) -> str:
    if secs < 90:
        return f"{int(secs)} s"
    if secs < 5400:
        return f"{secs / 60:.1f} min"
    return f"{secs / 3600:.1f} h"


def alert_line(hit: dict, *, migrated: bool = False) -> str:
    """One decision line for the owner: the coin, the leader, which exits were due and when, its value now, how to
    sell it, and last the Telegram command that sells it (a coin still on the curve). Plain words; nothing here sells:
    only the owner's own /sell does."""
    mint = hit["mint"]
    head = f"[watch] Nobody is selling coin {mint}"
    if hit.get("bought_at") is not None:
        bought = f"bought {_hms(hit['bought_at'])} UTC"
        bought += f" for {hit['buy_sol']:.3f} SOL" if hit.get("buy_sol") else ""
        bought += f", copying wallet {hit['leader'][:6]}…" if hit.get("leader") else ""
        head += f" ({bought})"
    due = "; ".join(f"{d['what']} at {_hms(d['at'])}" for d in hit.get("due") or [])
    reason = hit.get("reason")
    if reason == "sniper_inactive":
        why = f"The sniper that bought it ({hit.get('sniper_id')}) is {hit.get('sniper_status')} on OKTech, so nothing will sell it"
        why += f"; its exits were due: {due}" if due else ""
    elif reason == "sniper_unknown":
        why = "No sniper of ours is known to have bought it, so nothing may be selling it"
    elif reason == "not_on_tape":
        why = "The tape shows no buy of ours still open for it (the buy may not be recorded yet), so nothing may be selling it"
    elif reason == "past_timer":
        why = f"It is past its {hit.get('timer_secs') or DEFAULT_TIMER_SECS:g} s timer and nothing sold it"
        why += " (the tape is behind, so its other exits could not be checked)" if hit.get("lagging") else ""
    else:
        why = f"Its own exits were due and nothing sold: {due}"
    value = hit.get("value_sol")
    worth = f"held {_dur(hit.get('held_secs') or 0)}"
    if value is None:
        worth += ", value not known yet (the tape has not priced it)"
    else:
        worth += f", worth about {value:.3f} SOL now"
        worth += f" ({(value / hit['buy_sol'] - 1) * 100:+.0f} %)" if hit.get("buy_sol") else ""
    worth = f"{worth[0].upper()}{worth[1:]}"
    if migrated:
        how = "It has left the pump.fun curve, so sell it by hand in OKTech or Axiom (/sell only sells curve coins)"
        return f"{head}. {why}. {worth}. {how}. Nothing sells it unless you do."
    # the last line is the owner's Telegram command that sells it (operator/commands.py): nothing after the address,
    # so the line copied from the alert is the command
    return (
        f"{head}. {why}. {worth}. Nothing sells it unless you do (by hand in OKTech or Axiom, or from here).\n"
        f"Sell it now: /sell {mint}"
    )


def stray_line(hit: dict) -> str:
    """The once-a-day line for a held coin with no buy of ours on the tape (an airdropped token): not a coin the book
    bought, so no sniper of ours sells it. Plain words; nothing here sells."""
    if hit.get("lagging"):
        what = ("it is probably not a coin the book bought (the tape is behind, so a buy of ours may not be recorded "
                "yet), so nothing may be selling it")  # fmt: skip
    else:
        what = "it is not a coin the book bought, so no sniper of ours will sell it"
    value = hit.get("value_sol")
    worth = (
        "Its value is not known (the tape has not priced it)" if value is None else f"It is worth about {value:.3f} SOL"
    )
    return (
        f"[watch] Coin {hit['mint']} is in the wallet, but the tape shows no buy of ours for it: {what}. {worth}. Sell "
        "it by hand, or tell me to ignore it and it goes on the ignore list in the settings. I will say this once a "
        "day while it is held."
    )


def _migrated(store: Store, mint: str) -> bool:
    try:
        return store.completion(mint) is not None or store.migration(mint) is not None
    except Exception:  # noqa: BLE001 - only the wording depends on it
        return False


def state_path(paths: OperatorPaths) -> Path:
    return paths.root / STATE_FILE


def _load(paths: OperatorPaths) -> dict:
    try:
        d = json.loads(state_path(paths).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _save(paths: OperatorPaths, state: dict) -> None:
    p = state_path(paths)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, default=str))
    os.replace(tmp, p)


def watched_within(paths: OperatorPaths, now: float, secs: float = 180) -> bool:
    """A watch pass ran in the last `secs`: the book's run need not watch at its end (the one-minute pass does)."""
    ts = _num(_load(paths).get("ts"))
    return ts is not None and now - ts <= secs


def current_hits(paths: OperatorPaths, now: float, max_age: float = HIT_MAX_AGE_SECS) -> list[dict]:
    """The coins the last watch pass flagged, if it ran within `max_age` (the operator's gate reads this)."""
    st = _load(paths)
    ts = _num(st.get("ts"))
    if ts is None or now - ts > max_age:
        return []
    return [h for h in st.get("hits") or [] if isinstance(h, dict) and h.get("mint")]


def watch_pass(cfg: Config, store: Store, notifier: Notifier, wallet: str, holdings: list[dict],
               tasks: list[dict] | None, now: float, *, notes=None) -> list[dict]:  # fmt: skip
    """One watch over the wallet's holdings: returns the flagged coins (each with `alerted`), sends at most one
    Telegram decision line per coin per 15 minutes while it is held (one a day for a coin with no buy of ours on the
    tape), records one `events` row per flagged position, and writes operator/watch.json. It never sells. The coins on
    [operator] ignore_mints are left out. The book's run can call it at its end with the holdings and the OKTech tasks
    it already has; `notes` is a NoteSink (default: operator/notes.md next to the database)."""
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    state = _load(paths)
    ignored = {str(m) for m in cfg.operator.ignore_mints or ()}
    holdings = [h for h in holdings or [] if str(h.get("mint")) not in ignored]
    held = {str(h["mint"]) for h in holdings if _is_mint(h.get("mint"))}
    seen = {m: float(ts) for m, ts in (state.get("first_seen") or {}).items() if m in held}
    for m in held:
        seen.setdefault(m, float(now))
    hits = unmanaged(store, wallet, holdings, tasks, now, first_seen=seen) if held else []
    alerted = {m: float(ts) for m, ts in (state.get("alerted") or {}).items() if m in held}
    # a coin with no buy of ours is said once a day, and kept for the day even while it is out of the holdings: a coin
    # whose value hovers at the dust line, or one missed holdings read, does not make it news again
    strays = {m: float(ts) for m, ts in (state.get("strays") or {}).items() if now - float(ts) < STRAY_REPEAT_SECS}
    recorded = [k for k in state.get("recorded") or [] if str(k).split("|", 1)[0] in held]
    if hits and notes is None:
        from ..operator.tools import NoteSink

        notes = NoteSink(paths, now=lambda: now)
    for h in hits:
        if h.get("stray"):
            h["alerted"] = h["mint"] not in strays
            if h["alerted"]:
                notifier.send(notes.write(stray_line(h), "decision"))
                strays[h["mint"]] = float(now)
            continue
        h["alerted"] = now - alerted.get(h["mint"], -math.inf) >= REPEAT_SECS
        if h["alerted"]:
            notifier.send(notes.write(alert_line(h, migrated=_migrated(store, h["mint"])), "decision"))
            alerted[h["mint"]] = float(now)

    def save() -> None:
        _save(paths, {"ts": float(now), "wallet": wallet, "first_seen": seen, "alerted": alerted, "strays": strays,
                      "recorded": recorded, "hits": hits})  # fmt: skip

    save()  # before the database write below, which can wait on a busy database: an alert sent is never sent twice
    new = [h for h in hits if f"{h['mint']}|{h.get('buy_sig') or int(h['start'])}" not in recorded]
    for h in new:
        data = {k: h.get(k) for k in ("reason", "due", "sniper_id", "sniper_status", "buy_sig", "buy_sol", "value_sol",
                                      "flag_at", "start")}  # fmt: skip
        try:
            store.add_events([{"ts": int(now), "kind": "unmanaged", "mint": h["mint"], "wallet": h.get("leader"),
                               "slot": h["buy_slot"] if h.get("buy_slot") is not None else int(h["start"]),
                               "data": data, "source": "watch"}])  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - the record is retried next pass; the alert already went out
            h["record_error"] = f"{type(exc).__name__}: {exc}"[:200]
        else:
            recorded.append(f"{h['mint']}|{h.get('buy_sig') or int(h['start'])}")
    if new:
        save()
    return hits


async def watch_wallet(cfg: Config, svc, notifier: Notifier, now: float, *, notes=None) -> list[dict]:
    """The one-minute pass's watch: the wallet's holdings by the book's rule (Service.open_positions, without the
    owner's ignored coins), OKTech's tasks only while a coin is held (for `OKTECH_WAIT_SECS` at most), then
    `watch_pass`. Raises when the holdings cannot be read (the caller logs it)."""
    wallet = cfg.operator.wallet
    if not wallet:
        return []
    holdings = list(await svc.open_positions(wallet, ignore=cfg.operator.ignore_mints) or [])
    tasks = None
    if any(_is_mint(h.get("mint")) for h in holdings):
        try:
            tasks = list((await asyncio.wait_for(svc.oktech_status(), OKTECH_WAIT_SECS)).get("snipers") or [])
        except Exception:  # noqa: BLE001 - OKTech not answering: the tape and the payloads still judge the coin
            tasks = None
    return watch_pass(cfg, svc.store, notifier, wallet, holdings, tasks, now, notes=notes)
