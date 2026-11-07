"""The owner's wallet-wide guard, without SSH. The owner's yes (7 Nov 09:15 UTC, docs/live-recipe.md, "Yes, use this
plan"): "no new live trade once the day is 1 SOL down or the wallet is under 10 SOL, changeable from Telegram".

The day's result is our own trading only: the round trips closed today (UTC) in the measure stage's fills, with their
real fees and failed duplicate sells (book/state.py, the book's own day accounting), plus the coins our own buys still
hold, marked at the tape's price against what those buys cost. Never the change of the wallet's balance: a deposit, a
withdrawal or OKTech wrapping SOL is not a trading result (6 Nov 15:16 the owner moved 2.54 SOL out of the sniper wallet
in the middle of a test, and OKTech wrapped 0.0996 SOL at its start). The floor is the wallet itself: SOL, wrapped SOL
and the coins at the tape's price, the owner's ignored coins left out.

While it is tripped, no new live trade starts: the book creates and starts nothing and stops its own snipers that hold
nothing (a sniper holding a coin is never stopped: a stopped sniper strands its coin, CLAUDE.md rule 1); /test go, the
round's start of a test and the cycler's re-arm refuse; the automatic test requests nothing. What the guard cannot read
never counts as clear. One decision line goes to Telegram when a stop trips (once a day for each of the two), and
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


def _open_lots(store, wallet: str, now: float, per_tx_sol: float, lookback_days: float) -> dict[str, dict[str, float]]:
    """The tokens our own buys still hold by the measure stage's fills, paired first in first out as measure pairs its
    round trips: per coin, the tokens left and what they cost (the buy's SOL and its share of the buy's fees)."""
    from ..measure.classify import _fill_fee_sol, pair_round_trips

    since = int(now - lookback_days * 86400)
    buys = _usable(store.fills(wallet=wallet, since_ts=since, side="buy", limit=100_000))
    sells = _usable(store.fills(wallet=wallet, since_ts=since, side="sell", limit=100_000))
    by_sig = {b["sig"]: b for b in buys}
    out: dict[str, dict[str, float]] = {}
    for lot in pair_round_trips(buys, sells, per_tx_sol)["open"]:
        b = by_sig[lot["sig"]]
        share = float(lot["tokens_left"]) / float(b["tokens"])
        fee = _fill_fee_sol(b)
        o = out.setdefault(lot["mint"], {"tokens": 0.0, "cost_sol": 0.0})
        o["tokens"] += float(lot["tokens_left"])
        o["cost_sol"] += (float(b["sol"]) + (per_tx_sol if fee is None else fee)) * share
    return out


def day_result(store, wallet: str, now: float, held: list[dict], *, per_tx_sol: float,
               lookback_days: float = 7) -> dict[str, Any]:  # fmt: skip
    """Today's trading result in SOL: `realised_sol`, the round trips closed since 00:00 UTC (each sell's result with
    its failed duplicate sends: book/state.realised_today), `open_sol`, the coins in `held` (the wallet's coins by the
    shared rule) that our own buys still hold, at the tape's price against what those buys cost, and `total_sol`.

    Only what both the wallet and the fills see is counted: a coin no buy of ours explains (an airdrop) is no trading
    result, one bought minutes ago that the measure stage has not read yet has no cost known, and one the tape has not
    priced has no value known; each counts for nothing until it is known (`coins`: the coins counted, `unpriced`: the
    held coins with a buy of ours and no price). A coin whose sale the wallet read has not caught up with counts only
    the tokens the fills still hold. A deposit or a withdrawal is in none of these numbers."""
    from ..book.state import realised_today

    realised = realised_today(store, wallet, now)
    lots = _open_lots(store, wallet, now, per_tx_sol, lookback_days)
    coins: dict[str, dict[str, Any]] = {}
    for h in held or []:
        m = h.get("mint")
        if not m or m not in lots:
            continue
        c = coins.setdefault(str(m), {"raw": 0, "value": 0.0, "priced": True})
        c["raw"] += int(h.get("raw") or 0)
        if h.get("priced") is False or h.get("value_sol") is None:
            c["priced"] = False
        else:
            c["value"] += float(h["value_sol"])
    open_sol, counted, unpriced = 0.0, 0, 0
    for m, c in coins.items():
        if not c["priced"]:
            unpriced += 1
            continue
        lot = lots[m]
        held_raw, left = float(c["raw"]), float(lot["tokens"])
        value_share = min(1.0, left / held_raw) if held_raw > 0 else 1.0  # the wallet holds more than our buys explain
        cost_share = min(1.0, held_raw / left) if held_raw > 0 and left > 0 else 1.0  # a sale the fills have not seen
        open_sol += c["value"] * value_share - lot["cost_sol"] * cost_share
        counted += 1
    open_sol = round(open_sol, 6)
    return {"realised_sol": realised, "open_sol": open_sol, "total_sol": round(realised + open_sol, 6),
            "coins": counted, "unpriced": unpriced}  # fmt: skip


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


def _sol(x: float) -> str:
    return f"{x:+.2f}".replace("+0.00", "0.00").replace("-0.00", "0.00")


def evaluate(store, cfg, paths: OperatorPaths, now: float, *, wallet: str | None = None, sol: float | None = None,
             held: list[dict] | None = None) -> Guard:  # fmt: skip
    """The guard from what the caller has read: `sol`, the wallet's SOL and wrapped SOL (None: not known), and `held`,
    its coins by the shared rule (positions.py; None: not known). The day stop holds once today is at least the day
    stop down; the floor once the wallet is under it. Reads the store and the owner's file, nothing else."""
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
                             lookback_days=cfg.measure.lookback_days)  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - the guard says it cannot tell; it never says clear
            unknown.append(f"the fills could not be read ({type(exc).__name__})")
    if day is not None and day["total_sol"] <= -stop + 1e-9:
        kinds.append("day")
        said.append(f"today is {-day['total_sol']:.2f} SOL down ({_sol(day['realised_sol'])} in trades closed today, "
                    f"{_sol(day['open_sol'])} in coins still held), at or past the {stop:g} SOL day stop")  # fmt: skip
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
                 equity_sol=equity, day_stop_sol=stop, floor_sol=floor, owner_set=bool(read_risk(paths)))  # fmt: skip


def from_snapshot(store, cfg, paths: OperatorPaths, now: float, snap) -> Guard:
    """The guard from the last round's snapshot (its SOL, wrapped SOL and coins); a snapshot older than two rounds, of
    another wallet, or one that could not read the balances, leaves the wallet unknown."""
    wallet = cfg.operator.wallet or ""
    fresh = (snap is not None and now - float(snap.ts) <= SNAPSHOT_MAX_AGE_SECS
             and not (wallet and snap.wallet and snap.wallet != wallet))  # fmt: skip
    sol = snap.native_sol + snap.wsol if fresh and snap.native_sol is not None and snap.wsol is not None else None
    held = list(snap.held or []) if fresh and sol is not None else None
    return evaluate(store, cfg, paths, now, sol=sol, held=held)


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
    said = "; ".join(f"{_WORDS[k]} {r}" for k, r in zip(g.kinds, g.reason.split("; "), strict=False) if k in new)
    line = notes.write(f"{ANNOUNCED}: {said}. No new live trade: the book creates and starts nothing and stops its "
                       "snipers that hold nothing (one holding a coin keeps running to sell it), and no live test "
                       f"starts or re-arms; {_until(new, g)}. /risk shows it; /risk stop <SOL> or /risk floor <SOL> "
                       "changes it.", "decision" if page else "info")  # fmt: skip
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
    print(status_line(evaluate(_store(cfg), cfg, paths, now, sol=None, held=None), paths))
    return 0


def _store(cfg):
    """The store for the CLI's line (the day's closed trades); None when it cannot be opened."""
    try:
        from ..store import Store

        return Store(cfg.storage.db_path)
    except Exception:  # noqa: BLE001 - the line then says it cannot tell
        return None
