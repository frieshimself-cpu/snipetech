"""The owner's wallet-wide guard, without SSH. The owner's yes (7 Nov 09:15 UTC, docs/live-recipe.md, "Yes, use this
plan"): "no new live trade once the day is 1 SOL down or the wallet is under 10 SOL, changeable from Telegram".

The day's result is our own trading only: the round trips closed today (UTC) in the measure stage's fills, with their
real fees and failed duplicate sells (book/state.py, the book's own day accounting), plus the coins our own buys still
hold, marked at the tape's price against what those buys cost. The fills lag the tape by up to 5 minutes and the cycler
re-arms seconds after a sale, so the wallet's trades the fills do not have yet are counted from the tape, and a coin our
buys of today hold by the fills that has left the wallet with no sale seen counts its whole cost as lost. A measure
stage that has not run for 40 minutes while the wallet trades makes the day one the guard cannot tell. Never the change
of the wallet's balance: a deposit, a withdrawal or OKTech wrapping SOL is not a trading result (6 Nov 15:16 the owner
moved 2.54 SOL out of the sniper wallet in the middle of a test, and OKTech wrapped 0.0996 SOL at its start). The floor
is the wallet itself: SOL, wrapped SOL and the coins at the tape's price, the owner's ignored coins left out.

While it is tripped, no new live trade starts: the book creates and starts nothing and stops its own snipers once the
wallet holds nothing; /test go, the round's start of a test and the cycler's re-arm refuse; the automatic test requests
nothing. What it does not stop: while the wallet holds any coin every book sniper keeps running, because the book cannot
tell which one holds it (book/run.py passes holders=None: a fill can name the wrong leader) and a stopped sniper strands
its coin (CLAUDE.md rule 1), and each can still buy up to [book] max_buys_per_sniper; the trip line says so, and
/sell <coin> lets the book stop them. What the guard cannot read never counts as clear. One decision line goes to Telegram when a stop trips (once a day for each of the two), and
/status shows the guard.

operator/RISK holds the owner's values (budget_switch.py's pattern: written atomically as JSON with when and who, a read
never raises, a value out of its bounds falls back to the owner's 7 Nov value alone). With no file the guard is on at
the owner's values; there is no off. Only the owner writes it: `/risk stop 1.5`, `/risk floor 9` or `/risk default` in
Telegram, or `snipetech operate risk --day-stop N --floor N --default` (the cloud session, with the owner's yes in
chat). The agents' start_job refuses `operate`, and none of their tools writes a file without an extension in
operator/."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import OperatorPaths

DAY_STOP_SOL = 1.0  # the owner's: no new live trade once the day is this far down
FLOOR_SOL = 10.0  # the owner's: ... or once the wallet (SOL and coins) is under this
DEFAULTS: dict[str, float] = {"day_stop_sol": DAY_STOP_SOL, "floor_sol": FLOOR_SOL}
# each value's bounds, in the words the owner reads when one is refused: a typo can neither switch the guard off nor
# make it stop everything for good (a day stop of 0.1 SOL still lets a day start)
RULES = {
    "day_stop_sol": (lambda v: 0.1 <= v <= 5, "the day stop is 0.1 to 5 SOL"),
    "floor_sol": (lambda v: 0 <= v <= 1000, "the floor is 0 to 1000 SOL"),
}
FIELDS = tuple(RULES)
SNAPSHOT_MAX_AGE_SECS = 40 * 60  # the last round's snapshot: older than two rounds, the wallet is read instead
SETTLE_SECS = 60  # a buy this recent may not be in a wallet read yet (book/run.py TAPE_SETTLE_SECS)
# the measure stage writes our trades into the fills every 5 minutes (every 15 as the round's stand-in): a last run older
# than two rounds, with trades of ours on the tape today since, means the fills cannot vouch for the day
MEASURE_KEY = "measure:last_ts"
MEASURE_STALE_SECS = 40 * 60
WALLET_READ_SECS = 20  # how long a wallet read may take before the guard says it cannot tell (live_test's rule)
ANNOUNCED = "wallet-wide guard tripped"  # the decision note's words: one a day for each stop
_WORDS = {"day": "day stop:", "floor": "floor:"}  # how each stop is named in that note


def risk_file(paths: OperatorPaths) -> Path:
    return paths.root / "RISK"


def _sane(name: str, value: Any) -> float | None:
    """`value` as a number inside its bounds, else None. Never raises (a 400-digit integer overflows a float)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        v = float(value)
    except OverflowError:
        return None
    return v if math.isfinite(v) and RULES[name][0](v) else None


def read_risk(paths: OperatorPaths) -> dict[str, float]:
    """The owner's values that are numbers inside their bounds; {} without a file or with one that cannot be read."""
    try:
        data = json.loads(risk_file(paths).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for name in FIELDS:
        v = _sane(name, data.get(name))
        if v is not None:
            out[name] = v
    return out


def limits(paths: OperatorPaths) -> dict[str, float]:
    """Each value from the owner's file when it is there and sane, else the owner's 7 Nov value."""
    return DEFAULTS | read_risk(paths)


def set_risk(paths: OperatorPaths, values: dict[str, Any], by: str, now: float | None = None) -> dict[str, float]:
    """Write the owner's `values` over what the file holds and return what it holds now. Every value is checked before
    anything is written: an unknown name or a value out of its bounds raises ValueError in plain words (never repeating
    the value: a secret pasted by mistake must not reach the chat) and changes nothing."""
    unknown = sorted(set(values) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown guard value {', '.join(unknown)} (known: {', '.join(FIELDS)})")
    new = read_risk(paths)
    for name, value in values.items():
        v = _sane(name, value)
        if v is None:
            raise ValueError(RULES[name][1])
        new[name] = v
    paths.ensure()
    f = risk_file(paths)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({**new, "ts": int(time.time() if now is None else now), "by": by}))
    tmp.replace(f)
    return new


def clear_risk(paths: OperatorPaths) -> None:
    """Back to the owner's 7 Nov values (`/risk default`, `operate risk --default`)."""
    try:
        risk_file(paths).unlink(missing_ok=True)
    except IsADirectoryError:
        pass


def _set_at(paths: OperatorPaths) -> str:
    try:
        ts = json.loads(risk_file(paths).read_text())["ts"]
        return time.strftime(" since %d %b %H:%M UTC", time.gmtime(float(ts)))
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return ""


# -- the day's result -----------------------------------------------------------------------------------------------


def _usable(rows: list[dict]) -> list[dict]:
    """Fills the pairing can read: a mint, a time, SOL and tokens (the measure stage writes them all; an old or
    hand-written row without them is skipped, never a reason to fail)."""
    out = []
    for r in rows:
        try:
            if r.get("mint") and r.get("sig") and r.get("ts") is not None and float(r.get("tokens") or 0) > 0:
                out.append(r | {"sol": float(r.get("sol") or 0), "tokens": float(r["tokens"])})
        except (TypeError, ValueError):
            continue
    return out


def _lots(store, wallet: str, now: float, per_tx_sol: float, lookback_days: float) -> tuple[dict, list]:
    """(the lots our own buys still hold by the measure stage's fills, per coin: [bought at, tokens left, what they
    cost], paired first in first out as measure pairs its round trips (the buy's SOL and its share of the buy's fees);
    the wallet's trades on the tape that the fills do not have yet, oldest first)."""
    from ..measure.classify import _fill_fee_sol, pair_round_trips

    since = int(now - lookback_days * 86400)
    rows = store.fills(wallet=wallet, since_ts=since, limit=100_000)
    measured = {str(r.get("sig")) for r in rows if r.get("sig")}
    buys = _usable([r for r in rows if r.get("side") == "buy"])
    sells = _usable([r for r in rows if r.get("side") == "sell"])
    by_sig = {b["sig"]: b for b in buys}
    lots: dict[str, list[list[float]]] = {}
    for lot in pair_round_trips(buys, sells, per_tx_sol)["open"]:
        b = by_sig[lot["sig"]]
        share = float(lot["tokens_left"]) / float(b["tokens"])
        fee = _fill_fee_sol(b)
        cost = (float(b["sol"]) + (per_tx_sol if fee is None else fee)) * share
        lots.setdefault(str(lot["mint"]), []).append([float(b["ts"]), float(lot["tokens_left"]), cost])
    unread = sorted((t for t in store.trades_for_user(wallet, since) if t.sig not in measured and t.mint),
                    key=lambda t: (t.ts, t.slot, t.pos))  # fmt: skip
    return lots, unread


def day_result(store, wallet: str, now: float, held: list[dict], *, per_tx_sol: float, lookback_days: float = 7,
               held_ts: float | None = None, ignore=()) -> dict[str, Any]:  # fmt: skip
    """Today's trading result in SOL: `realised_sol`, the round trips closed since 00:00 UTC, `open_sol`, the coins in
    `held` (the wallet's coins by the shared rule, read at `held_ts`, now by default) that our own buys still hold, at
    the tape's price against what those buys cost, and `total_sol`.

    The measure stage's fills are the record (each sell's result with its failed duplicate sends: book/state.
    realised_today), but they lag the tape by up to 5 minutes, and the cycler re-arms seconds after a sale: a coin's
    loss counted while it was held must not vanish the moment it is sold. So the wallet's trades on the tape that the
    fills do not have yet are counted from the tape too (what went out on a buy, what came back on a sale, a
    transaction's fees on each), and a lot our buys still hold by the fills that the wallet no longer holds, with no
    sale of it seen, counts its whole cost as lost if it was bought today (sold where the recorder does not look, or
    rugged to dust) until the measure stage says otherwise (`unread_sol`: the part of the day that rests on those).

    Only what is known is counted: a coin no buy of ours explains (an airdrop) is no trading result, one the tape has not
    priced has no value known (`unpriced`), and a coin with a buy after the wallet was read (a minute of grace) is not
    judged on that read; each counts for nothing until it is known (`coins`: the held coins counted). The owner's
    ignored coins are left out. A deposit or a withdrawal is in none of these numbers. `pending`: today's trades of
    ours on the tape that the fills do not have yet."""
    from ..book.state import realised_today

    day_start = int(now // 86400) * 86400
    seen_at = float(now if held_ts is None else held_ts) - SETTLE_SECS
    skip = set(ignore or ())
    realised = realised_today(store, wallet, now)
    lots, unread = _lots(store, wallet, now, per_tx_sol, lookback_days)
    sells: dict[str, list] = {}
    for t in unread:
        if t.mint in skip:
            continue
        if t.is_buy:
            cost = (int(t.quote) + int(t.fee)) / 1e9 + per_tx_sol
            lots.setdefault(t.mint, []).append([float(t.ts), float(t.base), cost])
        elif float(t.base) > 0:
            sells.setdefault(t.mint, []).append(t)
    for ls in lots.values():
        ls.sort(key=lambda lot: lot[0])
    unread_sol = 0.0
    for m, ss in sells.items():  # the sales the fills do not have yet close the oldest lots first, as measure will
        for t in ss:
            left, got = float(t.base), (int(t.quote) - int(t.fee)) / 1e9 - per_tx_sol
            for lot in lots.get(m, []):
                if left <= 0:
                    break
                if lot[1] <= 0 or lot[0] > t.ts:
                    continue
                take = min(lot[1], left)
                cost = lot[2] * take / lot[1]
                if t.ts >= day_start:  # a sale before today is an earlier day's trip
                    unread_sol += got * take / float(t.base) - cost
                lot[1] -= take
                lot[2] -= cost
                left -= take
    coins: dict[str, dict[str, Any]] = {}
    for h in held or []:
        m = h.get("mint")
        if not m or m in skip:
            continue
        c = coins.setdefault(str(m), {"raw": 0, "value": 0.0, "priced": True})
        c["raw"] += int(h.get("raw") or 0)
        if h.get("priced") is False or h.get("value_sol") is None:
            c["priced"] = False
        else:
            c["value"] += float(h["value_sol"])
    open_sol, counted, unpriced = 0.0, 0, 0
    for m, ls in lots.items():
        if m in skip:
            continue
        ls = [lot for lot in ls if lot[1] > 1e-9]
        left = sum(lot[1] for lot in ls)
        if left <= 0 or any(lot[0] > seen_at for lot in ls):  # nothing left, or a buy the wallet read may not show yet
            continue
        cost = sum(lot[2] for lot in ls)
        c = coins.get(m)
        if c is not None and not c["priced"]:
            unpriced += 1
            continue
        held_raw = float(c["raw"]) if c is not None else 0.0
        if c is not None and held_raw <= 0:  # a holding without its raw amount: it is our lot, all of it
            held_raw = left
        ours = min(held_raw, left)
        if c is not None:  # the wallet holds more than our buys explain: only our tokens' value is ours
            open_sol += c["value"] * (ours / held_raw if held_raw > 0 else 1.0) - cost * ours / left
            counted += 1
        gone = left - ours  # tokens our buys hold by the fills that the wallet does not, with no sale of them seen
        if gone > 1e-9:
            today = sum(lot[2] for lot in ls if lot[0] >= day_start) / cost if cost > 0 else 0.0
            unread_sol -= cost * gone / left * today
    open_sol = round(open_sol, 6)
    realised_all = round(realised + unread_sol, 6)
    pending = sum(1 for t in unread if t.ts >= day_start and t.mint not in skip)
    return {"realised_sol": realised_all, "open_sol": open_sol, "total_sol": round(realised_all + open_sol, 6),
            "coins": counted, "unpriced": unpriced, "unread_sol": round(unread_sol, 6), "pending": pending}  # fmt: skip


# -- tripped or not --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Guard:
    """The guard as it stands: `tripped` while a stop holds (no new live trade); `known` when both stops could be read
    or one of them holds; `kinds` the stops that hold ("day", "floor"); `reason` why it is tripped, or what could not be
    read, in plain words ("" when it is clear)."""

    tripped: bool
    known: bool
    reason: str
    kinds: tuple[str, ...]
    day: dict[str, Any] | None
    equity_sol: float | None  # SOL, wrapped SOL and the priced coins (a lower bound while a coin is unpriced)
    day_stop_sol: float
    floor_sol: float
    owner_set: bool
    reasons: tuple[str, ...] = ()  # each stop's own words, in the order of `kinds`


def _sol(x: float) -> str:
    return f"{x:+.2f}".replace("+0.00", "0.00").replace("-0.00", "0.00")


def _hm(ts) -> str:
    try:
        return time.strftime("%H:%M UTC", time.gmtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return "an unknown time"


def _measure_lag(store, now: float, day: dict) -> str | None:
    """Why the fills cannot vouch for today, or None: the measure stage has not run for MEASURE_STALE_SECS (or never),
    and the tape holds trades of ours today that the fills do not have. What the tape shows is counted all the same
    (day_result); this says that the stage that records it has stopped, so the guard cannot tell."""
    if not day.get("pending"):
        return None
    last = (store.coverage(MEASURE_KEY) or {}).get("ts")
    if last is not None and now - float(last) <= MEASURE_STALE_SECS:
        return None
    n = int(day["pending"])
    since = "has never read the wallet's trades" if last is None else f"has not read the fills since {_hm(last)}"
    return f"the measure stage {since}, and {n} of today's trades of the wallet are not in them"


def evaluate(store, cfg, paths: OperatorPaths, now: float, *, wallet: str | None = None, sol: float | None = None,
             held: list[dict] | None = None, held_ts: float | None = None) -> Guard:  # fmt: skip
    """The guard from what the caller has read: `sol`, the wallet's SOL and wrapped SOL (None: not known), and `held`,
    its coins by the shared rule (positions.py; None: not known), read at `held_ts` (now by default). The day stop holds
    once today is at least the day stop down; the floor once the wallet is under it. A measure stage that has stopped
    writing the fills while the wallet trades makes the day one the guard cannot tell. Reads the store and the owner's
    file, nothing else."""
    lim = limits(paths)
    stop, floor = lim["day_stop_sol"], lim["floor_sol"]
    wallet = wallet if wallet is not None else (cfg.operator.wallet or "")
    ignore = set(cfg.operator.ignore_mints or ())
    coins = None if held is None else [h for h in held if h.get("mint") and h.get("mint") not in ignore]
    kinds: list[str] = []
    said: list[str] = []
    unknown: list[str] = []
    day = None
    if not wallet:
        unknown.append("no sniper wallet is set ([operator] wallet)")
    elif coins is None:
        unknown.append("the wallet's coins are not known")
    else:
        try:
            day = day_result(store, wallet, now, coins, per_tx_sol=cfg.costs.per_tx_sol(),
                             lookback_days=cfg.measure.lookback_days, held_ts=held_ts, ignore=ignore)  # fmt: skip
            lag = _measure_lag(store, now, day)
            if lag:
                unknown.append(lag)
        except Exception as exc:  # noqa: BLE001 - the guard says it cannot tell; it never says clear
            unknown.append(f"the fills could not be read ({type(exc).__name__})")
    if day is not None and day["total_sol"] <= -stop + 1e-9:
        kinds.append("day")
        unread = (f", {_sol(day['unread_sol'])} of it in trades the measure stage has not written yet"
                  if abs(day.get("unread_sol") or 0) >= 0.005 else "")  # fmt: skip
        said.append(f"today is {-day['total_sol']:.2f} SOL down ({_sol(day['realised_sol'])} in trades closed today"
                    f"{unread}, {_sol(day['open_sol'])} in coins still held), at or past the {stop:g} SOL day stop")  # fmt: skip
    equity = None
    if sol is None:
        unknown.append("the wallet's balance is not known")
    elif coins is not None:
        equity = round(
            float(sol) + sum(float(h.get("value_sol") or 0) for h in coins if h.get("priced") is not False), 6
        )
        unpriced = any(h.get("priced") is False or h.get("value_sol") is None for h in coins)
        if equity < floor - 1e-9:
            if unpriced:  # the coin the tape has not priced may lift it over the floor
                unknown.append(f"the wallet holds {equity:.2f} SOL and a coin the tape has not priced")
            else:
                kinds.append("floor")
                said.append(f"the wallet holds {equity:.2f} SOL (SOL and coins), under the {floor:g} SOL floor")
    tripped = bool(kinds)
    reason = "; ".join(said) if tripped else ("the guard cannot tell: " + "; ".join(unknown) if unknown else "")
    return Guard(tripped=tripped, known=tripped or not unknown, reason=reason, kinds=tuple(kinds), day=day,
                 equity_sol=equity, day_stop_sol=stop, floor_sol=floor, owner_set=bool(read_risk(paths)),
                 reasons=tuple(said))  # fmt: skip


def from_snapshot(store, cfg, paths: OperatorPaths, now: float, snap) -> Guard:
    """The guard from the last round's snapshot (its SOL, wrapped SOL and coins); a snapshot older than two rounds, of
    another wallet, or one that could not read the balances, leaves the wallet unknown."""
    wallet = cfg.operator.wallet or ""
    fresh = (snap is not None and now - float(snap.ts) <= SNAPSHOT_MAX_AGE_SECS
             and not (wallet and snap.wallet and snap.wallet != wallet))  # fmt: skip
    sol = snap.native_sol + snap.wsol if fresh and snap.native_sol is not None and snap.wsol is not None else None
    held = list(snap.held or []) if fresh and sol is not None else None
    return evaluate(store, cfg, paths, now, sol=sol, held=held, held_ts=float(snap.ts) if held is not None else None)


async def read_wallet(svc, cfg, wallet: str) -> tuple[float, list[dict]]:
    """(SOL and wrapped SOL, the coins by the shared rule) read now, WALLET_READ_SECS at most; raises otherwise."""
    ignore = list(cfg.operator.ignore_mints or ())
    sol = await asyncio.wait_for(svc.wallet_sol(wallet), WALLET_READ_SECS)
    held = await asyncio.wait_for(svc.open_positions(wallet, ignore=ignore), WALLET_READ_SECS)
    return float(sol), list(held or [])


async def tripped(svc, cfg, paths: OperatorPaths, now: float, *, snap=None, sol: float | None = None,
                  held: list[dict] | None = None) -> tuple[bool, str]:  # fmt: skip
    """(True, why) while a stop holds or the guard cannot tell, (False, "") when it is clear: what every start path
    asks. The wallet comes from `sol` and `held` when given, else the snapshot when it is fresh, else a read now."""
    store = getattr(svc, "store", None)
    g = None
    if sol is None and held is None and snap is not None:
        g = from_snapshot(store, cfg, paths, now, snap)
        if g.equity_sol is None and not g.tripped:  # too old, another wallet, or no balance in it: read the wallet
            g = None
    if g is None and sol is None and held is None:
        try:
            sol, held = await read_wallet(svc, cfg, cfg.operator.wallet or "")
        except Exception as exc:  # noqa: BLE001 - cannot tell is never clear
            return True, f"the guard cannot tell: the wallet could not be read ({type(exc).__name__})"
    if g is None:
        g = evaluate(store, cfg, paths, now, sol=sol, held=held)
    return (True, g.reason) if g.tripped or not g.known else (False, "")


# -- the words the owner reads ---------------------------------------------------------------------------------------


def rule_words(g: Guard) -> str:
    return f"no new live trade once the day is {g.day_stop_sol:g} SOL down or the wallet is under {g.floor_sol:g} SOL"


def status_line(g: Guard, paths: OperatorPaths | None = None) -> str:
    """The guard in one line for /status and /risk: the rule, whose values, today's result and the wallet."""
    whose = (f"your setting{_set_at(paths)}" if paths is not None else "your setting") if g.owner_set else (
        "the 7 Nov plan's values")  # fmt: skip
    head = f"risk guard: {rule_words(g)} ({whose})"
    facts = []
    if g.day is not None:
        d = g.day
        facts.append(f"today {_sol(d['total_sol'])} SOL ({_sol(d['realised_sol'])} closed, {_sol(d['open_sol'])} in "
                     f"coins held)")  # fmt: skip
    if g.equity_sol is not None:
        facts.append(f"wallet {g.equity_sol:.2f} SOL")
    line = head + (f"; {', '.join(facts)}" if facts else "")
    if g.tripped:
        return f"{line}. TRIPPED: {g.reason}"
    if not g.known:
        return f"{line}. {g.reason[0].upper()}{g.reason[1:]}: nothing new starts until it can"
    return line


def _until(kinds: tuple[str, ...], g: Guard) -> str:
    parts = []
    if "day" in kinds:
        parts.append("the day stop lifts at 00:00 UTC")
    if "floor" in kinds:
        parts.append(f"the floor once the wallet is back at {g.floor_sol:g} SOL")
    return " and ".join(parts)


def announce(g: Guard, notes, notifier, now: float, *, page: bool = True) -> bool:
    """One decision line, to Telegram as well, when a stop trips: once a day for each of the two stops, never every
    round (the book and the round both call it; the shared notes file says whether it was told today). With `page`
    False (nothing would trade: an off book, no test, no automatic test) it is only noted, and the owner still hears it
    the first time it matters that day. True when written."""

    def told(k: str) -> bool:
        return notes.has_today(f"[decision] {ANNOUNCED}", now, _WORDS[k]) or (
            not page and notes.has_today(f"[info] {ANNOUNCED}", now, _WORDS[k])
        )

    new = tuple(k for k in g.kinds if not told(k))
    if not g.tripped or not new:
        return False
    said = "; ".join(f"{_WORDS[k]} {r}" for k, r in zip(g.kinds, g.reasons, strict=True) if k in new)
    line = notes.write(f"{ANNOUNCED}: {said}. No new live trade starts: the book creates and starts nothing, and it stops "
                       "its snipers once the wallet holds nothing (while it holds any coin every book sniper keeps "
                       "running, since the book cannot tell which one holds it and a stopped sniper strands its coin, "
                       "and each can still buy up to its cap: /sell <coin> sells the coin); no live test starts or "
                       f"re-arms; {_until(new, g)}. /risk shows it; /risk stop <SOL> or /risk floor <SOL> changes it.",
                       "decision" if page else "info")  # fmt: skip
    if page and notifier is not None:
        notifier.send(line)
    return True


# -- /risk in Telegram and `operate risk` ----------------------------------------------------------------------------

USAGE = "Send /risk to see it, /risk stop <SOL a day>, /risk floor <SOL> or /risk default."
_NAMES = {"stop": "day_stop_sol", "floor": "floor_sol"}


def risk_reply(cfg, paths: OperatorPaths, store, snap, args: list[str], by: str, now: float) -> str:
    """/risk (show the guard and today's result), /risk stop 1.5, /risk floor 9, /risk default. Nothing else is
    understood, and nothing the owner sent is repeated (a secret key pasted by mistake must not reach the chat)."""
    words = [w.lower() for w in args]

    def line() -> str:
        return status_line(from_snapshot(store, cfg, paths, now, snap), paths)

    if not words:
        return line()
    if words == ["default"]:
        clear_risk(paths)
        return "risk guard back to the 7 Nov plan's values: " + line()
    if len(words) == 2 and words[0] in _NAMES:
        try:
            value = float(words[1].removesuffix("sol"))
        except ValueError:
            return f"risk guard not changed: {words[0]} needs a number of SOL after it. {USAGE} {line()}"
        try:
            set_risk(paths, {_NAMES[words[0]]: value}, by, now)
        except ValueError as exc:
            return f"risk guard not changed: {exc}. {USAGE} {line()}"
        return "risk guard set: " + line()
    return f"risk guard not changed: that is not a /risk I know. {USAGE} {line()}"


def risk_cli(cfg, args, now: float | None = None) -> int:
    """`snipetech operate risk [--day-stop N] [--floor N] [--default]`: sets exactly the values given (the cloud session
    names each value the owner agreed to in chat: `remote --wait run operate risk ...`). Prints where the file is and
    the guard that applies; a bad value, or a guard flag without `risk`, changes nothing and exits with the reason."""
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
    values = {k: v for k, v in (("day_stop_sol", args.day_stop), ("floor_sol", args.risk_floor)) if v is not None}
    if args.action != "risk":
        raise SystemExit("--day-stop and --floor go with `operate risk`")
    if any(getattr(args, k, None) is not None for k in ("daily_usd", "turn_usd", "tool_calls", "analyst_every_h")):
        raise SystemExit("--daily-usd, --turn-usd, --tool-calls and --analyst-every-h go with `operate budget`")
    if args.budget_default and values:
        raise SystemExit("operate risk: --default goes alone (it removes the owner's guard file)")
    now = time.time() if now is None else now
    if args.budget_default:
        clear_risk(paths)
    elif values:
        try:
            set_risk(paths, values, "cli", now)
        except ValueError as exc:
            raise SystemExit(f"operate risk: not changed: {exc}") from None
    there = risk_file(paths).exists()
    print(
        f"guard file {risk_file(paths)} on this machine" + ("" if there else " (none: the 7 Nov plan's values apply)")
    )
    from .snapshot import load_current

    store = _store(cfg)
    try:  # the wallet as the last round read it, as /risk shows it
        print(status_line(from_snapshot(store, cfg, paths, now, load_current(paths)), paths))
    finally:
        if store is not None:
            store.close()
    return 0


def _store(cfg):
    """The store for the CLI's line (the day's closed trades); None when it cannot be opened."""
    try:
        from ..store import Store

        return Store(cfg.storage.db_path)
    except Exception:  # noqa: BLE001 - the line then says it cannot tell
        return None
