"""`book run`: gather → plan → in dry mode write what it would do, in live mode execute through the Service's OKTech
calls (stop → retire → create → start), one Telegram line per action. Dry is the default and is forced under a kill
reason. A retire stops and deletes a sniper that made its relied-on buy while the wallet holds nothing, and the same
run creates its replacement."""

from __future__ import annotations

import contextlib
import fcntl
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import Config
from ..oktech import validate_exit
from ..operator import risk_guard
from ..operator.notify import Notifier, from_env
from ..operator.paths import OperatorPaths
from ..operator.snapshot import rpc_balances
from ..operator.tools import NoteSink
from ..positions import is_active
from .policy import (
    BOOK_NAME_RE,
    GUARD_KILL,
    ONE_SNIPER,
    BookInputs,
    BookPlan,
    ExitChoice,
    arm_bid,
    buying_book_wallets,
    copied_leaders,
    plan_book,
)
from .state import day_accounting, record_copying, record_run
from .switch import book_live, book_stops, effective_min_score
from .watch import watch_pass, watched_within


def _qualified(store) -> list[dict]:
    """Qualified verdicts of wallets that are not banned (a wallet banned after it qualified keeps its verdict)."""
    banned = {r["wallet"] for r in store.candidates(status="banned", limit=1_000_000)}
    return [v for v in store.latest_verdicts("qualified") if v.get("wallet") not in banned]


def _bookable(store) -> list[dict]:
    """Qualified wallets the book may copy: only verdicts the lab wrote since it simulates the live sniper (they carry
    a copy plan). The older lab rated 2YDn at +0.0146 SOL a trade and it lost 5 of 5 live copies, so its verdicts wait
    for a re-qualify (store.candidates makes them due at once)."""
    return [v for v in _qualified(store) if v.get("copy_plan")]


def _watch(cfg: Config, store, notifier: Notifier, notes: NoteSink, wallet: str, holdings: list[dict] | None,
           tasks: list[dict] | None, now: float) -> None:  # fmt: skip
    """The unmanaged-coin watch at the end of the run, unless the one-minute pass watched in the last 3 minutes. It
    only reports (book/watch.py never sells) and a failure never breaks the book."""
    try:
        if not watched_within(OperatorPaths(Path(cfg.storage.db_path).resolve().parent), now):
            watch_pass(cfg, store, notifier, wallet, list(holdings or []), tasks, now, notes=notes)
    except Exception as exc:  # noqa: BLE001
        notes.write(f"book: the unsold-coin watch failed: {type(exc).__name__}: {str(exc)[:120]}", "info")


def _other_copies(store, tasks: list[dict]) -> set[str]:
    """The leaders an active sniper that is not the book's already copies (OKTech buys once per signal per wallet, so
    a book sniper on the same leader would only add failed sells and crossed exits). OKTech's task documents carry no
    config: what a task copies comes from our own record of it; a task made in the OKTech app is unknown here."""
    local = {r["id"]: r for r in store.oktech_snipers(active=False)}
    out: set[str] = set()
    for t in tasks:
        if not is_active(t.get("status")) or BOOK_NAME_RE.match(str(t.get("name") or "")):
            continue
        out |= copied_leaders(local.get(t.get("id")) or {"kind": t.get("kind"), "source": t.get("source")})
    return out


def _actor_links(store, qualified: list[dict], tasks: list[dict]) -> dict[str, list[str]]:
    """Which wallets buy together, for the policy (BookInputs.actors): what qualify kept in each wallet's record
    (qualify/one_actor.py: its companions, the wallets it was chosen over, the one it yields to), for the qualified
    wallets and for the leaders our own records name for a sniper OKTech still lists (its tasks carry no config).
    A wallet whose record ties it to nobody is left out; no record at all says nothing, and the book then books that
    wallet by itself, as before 13 Nov."""
    from ..qualify.one_actor import actor_block, actor_links

    local = {r["id"]: r for r in store.oktech_snipers(active=False)}
    leaders = {w for t in tasks for w in copied_leaders(local.get(t.get("id")) or {})}
    out: dict[str, list[str]] = {}
    for w in dict.fromkeys([*(str(v["wallet"]) for v in qualified if v.get("wallet")), *sorted(leaders)]):
        links = actor_links(actor_block(store, w)) - {w}
        if links:
            out[w] = sorted(links)
    return out


def _bought_by_us(store, wallet: str, mint: str) -> bool:
    """Whether a buy of ours explains holding `mint`: a buy by the wallet on the tape, or a buy fill of the measure
    stage. Any read that fails counts as ours (the coin then keeps equity unknown, as before)."""
    if not mint or mint == "unknown":
        return True
    try:
        if store._q("SELECT 1 FROM trades WHERE mint=? AND user=? AND is_buy=1 LIMIT 1", (mint, wallet)):
            return True
        return bool(store._q("SELECT 1 FROM fills WHERE mint=? AND side='buy' LIMIT 1", (mint,)))
    except Exception:  # noqa: BLE001
        return True


STRAY_AFTER_SECS = 600  # a coin no buy of ours explains, seen this long: no tape lag can explain it either


def _strays(store, wallet: str, holdings: list[dict] | None, paths: OperatorPaths, now: float) -> set[str]:
    """Coins in the wallet that no buy of ours explains (an airdrop, the owner's own trade in another app) and that
    the unsold-coin watch first saw STRAY_AFTER_SECS ago or more: no sniper of ours holds them, so they never keep a
    spent sniper from being replaced (8 Nov: a dust airdrop held every replacement for hours). A coin seen just now may
    be a buy the tape has not caught up with, and still counts as held."""
    from .watch import _load

    seen = (_load(paths).get("first_seen") or {}) if holdings else {}
    out: set[str] = set()
    for h in holdings or []:
        m = str(h.get("mint") or "")
        t = seen.get(m)
        if m and t is not None and now - float(t) >= STRAY_AFTER_SECS and not _bought_by_us(store, wallet, m):
            out.add(m)
    return out


CREATED_GRACE_SECS = 60  # our clock against block time: a buy just before the recorded creation can still be its own


def _alive(rec: dict, ts: float) -> bool:
    born, gone = rec.get("created_at"), rec.get("removed_at")
    return born is not None and float(born) <= ts and (gone is None or ts <= float(gone))


def _buys_by_sniper(store, wallet: str, tasks: list[dict]) -> dict[str, int]:
    """Buys each book sniper that can still buy (running, starting, idle) has made, read from the tape rather than
    from fills, which lag by minutes. It counts high on purpose: our wallet's buys since the sniper was made (a minute
    of clock grace) in coins its leader bought before us, plus every buy of ours that no leader we copy explains (the
    leader's own buy failed, or the tape missed it). Counting high only replaces a sniper early; counting low relies
    on the buy that uses up OKTech's cap. A buy from before the sniper existed, while another of our snipers on that
    leader was alive, is that one's (a replacement never inherits its predecessor's buy). A sniper without our own
    record of it (when it was made, what it copies) is not counted, so it is never retired."""
    local = {r["id"]: r for r in store.oktech_snipers(active=False)}
    mine: list[tuple[str, str, float]] = []
    for t in tasks:
        book = BOOK_NAME_RE.match(str(t.get("name") or ""))
        if not book or str(t.get("status")) not in ("running", "starting", "idle"):
            continue
        rec = local.get(t.get("id")) or {}
        leaders = sorted(copied_leaders(rec))
        if leaders and rec.get("created_at") is not None:
            mine.append((str(t["id"]), leaders[0], float(rec["created_at"])))
    if not mine:
        return {}
    since = int(min(born for _, _, born in mine) - CREATED_GRACE_SECS)
    buys = store._q("SELECT DISTINCT sig, mint, slot, pos, ts FROM trades WHERE user=? AND is_buy=1 AND ts>=? "
                    "ORDER BY slot, pos", (wallet, since))  # fmt: skip
    every_leader = sorted({ld for r in local.values() for ld in copied_leaders(r)})

    def leaders_before(mint: str, slot: int, pos: int) -> set[str]:
        if not every_leader:
            return set()
        rows = store._q(f"SELECT DISTINCT user FROM trades WHERE mint=? AND is_buy=1 AND user IN ({','.join('?' * len(every_leader))}) "
                        "AND (slot<? OR (slot=? AND pos<?))", (mint, *every_leader, slot, slot, pos))  # fmt: skip
        return {r[0] for r in rows}

    before = {sig: leaders_before(mint, slot, pos) for sig, mint, slot, pos, _ts in buys}
    out: dict[str, int] = {}
    for sid, leader, born in mine:
        others = [r for r in local.values() if r["id"] != sid and leader in copied_leaders(r)]
        n = 0
        for sig, _mint, _slot, _pos, ts in buys:
            if ts < born - CREATED_GRACE_SECS or (ts < born and any(_alive(o, ts) for o in others)):
                continue
            explained_by = before[sig]
            n += leader in explained_by or not explained_by
        out[sid] = n
    return out


PAUSED_KEY = "book:paused"  # wallets paused on their live losses: wallet -> reason, ts, lab_version
TAPE_SETTLE_SECS = 60  # any buy of ours this recent counts as holding (the sniper's leader is active right now)
TAPE_OPEN_SECS = 600  # an unsold buy of ours this recent counts as holding; older ones the wallet read sees by itself
TAPE_STALE_SECS = 120  # the tape further behind than this: no new sniper, start or replacement


def _tape_holding(store, wallet: str, now: float) -> bool:
    """The tape (our own recorder, seconds ahead of the wallet read) shows a buy of ours in the last minute, or one in
    the last 10 minutes it has not seen sold: count it as holding, so no sniper is stopped or retired on a stale read."""
    rows = store._q("SELECT mint, is_buy, base, ts FROM trades WHERE user=? AND ts>=? ORDER BY slot, pos",
                    (wallet, int(now - TAPE_OPEN_SECS)))  # fmt: skip
    bought: dict[str, float] = {}
    sold: dict[str, float] = {}
    for mint, is_buy, base, ts in rows:
        if is_buy:
            if ts >= now - TAPE_SETTLE_SECS:
                return True
            bought[mint] = bought.get(mint, 0.0) + float(base or 0)
        else:
            sold[mint] = sold.get(mint, 0.0) + float(base or 0)
    return any(b > 0 and sold.get(m, 0.0) < 0.99 * b for m, b in bought.items())


def _tape_age(store, now: float) -> float | None:
    """Seconds since the newest trade on the tape; None for an empty tape (a fresh install)."""
    row = store._q("SELECT MAX(ts) FROM trades")
    return None if not row or row[0][0] is None else max(0.0, float(now) - float(row[0][0]))


class BookBusy(RuntimeError):
    pass


@contextlib.contextmanager
def book_lock(data_dir: str | Path):
    """One book run at a time (the 5-minute timer and a run started by hand): two overlapping runs could each make a
    sniper for the same leader."""
    p = Path(data_dir) / "operator" / "book.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p, "a+")  # noqa: SIM115 - held for the whole run
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BookBusy("another book run is in progress") from None
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _floor(b, a) -> float:
    """The smallest leader buy a new sniper copies: the wallet's own floor from its verdict (its opening buys, not its
    top-ups), never below [book] min_copy_sol."""
    return max(float(b.min_copy_sol), float(a.min_sol or 0.0))


def _line(a) -> str:
    bits = [a.kind, (a.wallet or "")[:6], a.arm or "", f"{a.fill_sol:g} SOL" if a.fill_sol else "", a.sniper_id or ""]
    return " ".join(b for b in bits if b) + f" — {a.reason}"


def _sent_exit(a, cfg: Config) -> ExitChoice | None:
    """The exit a create sends: the plan's choice, after the full config's last check of a wallet's own exit (its copy
    exit must still be the one snipers send); lab, sent by name as always, when that check finds a problem."""
    e = getattr(a, "exit", None)
    if e is None or e.source != "wallet":
        return e
    problems = validate_exit(e.params, cfg)
    return ExitChoice("lab", None, "lab", f"{e.name} cannot be sent: {'; '.join(problems)}") if problems else e


def _exit_words(e: ExitChoice | None) -> str:
    """The exit in the owner's line: plain lab in fixed mode; in per_wallet mode, why this exit."""
    if e is None or e.source == "fixed":
        return "exit lab"
    return f"exit {e.name} ({e.reason})"


async def run_book(svc, cfg: Config, now: float, *, notifier: Notifier | None = None, dry: bool | None = None,
                   balances: Callable | None = None, notes: NoteSink | None = None) -> dict[str, Any]:  # fmt: skip
    """One book run under the book lock; a run that finds another in progress does nothing."""
    try:
        with book_lock(Path(cfg.storage.db_path).resolve().parent):
            return await _run_book(svc, cfg, now, notifier=notifier, dry=dry, balances=balances, notes=notes)
    except BookBusy as exc:
        return {"dry": True, "skipped": str(exc), "actions": {}, "ranked": 0}


async def _run_book(svc, cfg: Config, now: float, *, notifier: Notifier | None = None, dry: bool | None = None,
                    balances: Callable | None = None, notes: NoteSink | None = None) -> dict[str, Any]:  # fmt: skip
    store = svc.store
    paths0 = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
    b = replace(cfg.book, min_score=effective_min_score(cfg, paths0))  # the owner's /book bar, if set
    wallet = cfg.operator.wallet
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent).ensure()
    notes = notes or NoteSink(paths, now=lambda: now)
    notifier = notifier or from_env(paths)
    if not wallet:
        return {"error": "no [operator] wallet configured", "dry": True}
    # equity and holdings
    native = wsol = None
    held_from_balances: list[dict] = []
    try:
        res = balances(wallet) if balances else rpc_balances(svc, wallet)
        if hasattr(res, "__await__"):
            res = await res
        native, wsol, held_from_balances = res
    except Exception as exc:  # noqa: BLE001
        notes.write(f"book: balances failed: {type(exc).__name__}: {exc}", "info")
    holdings_known = True
    try:
        holdings = await svc.open_positions(wallet, ignore=cfg.operator.ignore_mints)
    except Exception:  # noqa: BLE001 - unknown holdings count as holding: the policy then stops nothing
        holdings = held_from_balances or [{"mint": "unknown", "amount": 1}]
        holdings_known = False
    # equity = SOL + wrapped SOL + the tape value of the coins held; a coin of ours the tape has not priced makes it
    # unknown (the kill path then creates nothing and stops nothing), never a false floor breach after the book's own
    # buys. An unpriced coin no buy of ours explains (the owner's own trade in another app, a dust airdrop: 8 Nov
    # 17:30-20:40 the book halted three hours on one) counts as worth 0 SOL: that only lowers equity, so the floor
    # still trips and never wrongly holds; it stays a holding, so no sniper is stopped over it
    unpriced = [h for h in holdings or [] if not h.get("priced", True)]
    if unpriced and holdings_known:
        unpriced = [h for h in unpriced if _bought_by_us(store, wallet, str(h.get("mint") or ""))]
    if native is None or wsol is None or unpriced:
        equity = None
    else:
        equity = native + wsol + sum(float(h.get("value_sol") or 0) for h in holdings or [])
    try:
        snipers = list((await svc.oktech_status()).get("snipers") or [])
    except Exception as exc:  # noqa: BLE001
        notes.write(f"book: OKTech did not answer ({type(exc).__name__}); no action this run", "info")
        record_run(store, {"actions": [], "kill_reason": "oktech unknown"}, True, now)
        _watch(cfg, store, notifier, notes, wallet, holdings, None, now)
        return {"dry": True, "kill_reason": "oktech unknown", "actions": {}, "ranked": 0}
    try:  # which wallets its snipers still buy for, whatever this run does next: the qualify stage reads it
        record_copying(store, buying_book_wallets(snipers), now)
    except Exception as exc:  # noqa: BLE001 - never breaks the book; the qualify stage then waits on a stale reading
        notes.write(f"book: could not record which wallets its snipers copy ({type(exc).__name__})", "info")
    acc = day_accounting(store, wallet, now, equity_now=equity)
    live_mode = book_live(cfg, paths) if dry is None else not dry
    act_on_stops = dry is not True  # an explicit dry preview never acts; the routine run always applies its stops
    try:
        buys = _buys_by_sniper(store, wallet, snipers)
    except Exception as exc:  # noqa: BLE001 - no count, no retire: the snipers keep their spare buys this run
        buys = {}
        notes.write(f"book: could not count the snipers' buys on the tape ({type(exc).__name__}); none is replaced "
                    "this run", "info")  # fmt: skip
    try:
        tape_holding = _tape_holding(store, wallet, now)
    except Exception:  # noqa: BLE001 - the tape cannot be read: count it as holding (nothing is stopped or retired)
        tape_holding = True
    # the owner's wallet-wide guard, from what this run read: SOL and wrapped SOL, and the coins (None when unread). A
    # guard that cannot tell is never clear: coins that could not be read leave the book's own floor a SOL balance to
    # pass on, so the guard halts the run (its holders are all kept: unknown coins count as held)
    sol = native + wsol if native is not None and wsol is not None else None
    try:
        guard = risk_guard.evaluate(store, cfg, paths, now, wallet=wallet, sol=sol,
                                    held=list(holdings or []) if holdings_known else None)  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - the guard never breaks the book's own stops
        guard = None
        notes.write(f"book: the wallet-wide guard could not be read ({type(exc).__name__})", "info")
    from .switch import book_fresh_starts

    try:
        strays = _strays(store, wallet, holdings if holdings_known else None, paths, now)
    except Exception:  # noqa: BLE001 - no stray is called while anything cannot be read
        strays = set()
    fresh = book_fresh_starts(paths)
    paused_rec = dict(store.coverage(PAUSED_KEY) or {})
    lifted = [w for w, rec in paused_rec.items() if w in fresh and fresh[w] >= float((rec or {}).get("ts") or 0)]
    if lifted:  # the owner's fresh start lifts a live pause from before it
        for w in lifted:
            paused_rec.pop(w, None)
        store.set_coverage(PAUSED_KEY, paused_rec)
    # a live row computed before a wallet's fresh start still holds its old trips: not read until measure recomputes it
    live_rows = {r["wallet"]: r for r in store.wallet_live()
                 if not (r["wallet"] in fresh and float(r.get("updated_at") or 0) < fresh[r["wallet"]])}  # fmt: skip
    bookable = _bookable(store)
    try:  # which wallets buy together: one sniper an actor (policy.actor_members)
        actors: dict[str, list[str]] | None = _actor_links(store, bookable, snipers)
    except Exception as exc:  # noqa: BLE001 - not known this run: nothing new is added below
        actors = None
        sig = "[book] could not read which wallets buy together"
        if not notes.has_today(sig, now):
            notes.write(f"{sig} ({type(exc).__name__}): no new sniper, start or replacement in such a run", "info")
    inp = BookInputs(equity_sol=equity, day_realised_sol=acc["realised_sol"], day_risk_used_sol=acc["risk_used_sol"],
                     qualified=bookable, live=live_rows, actors=actors or {},
                     bid_model={r["arm"]: r for r in store.bid_model()}, snipers=snipers,
                     holdings=[h for h in holdings or [] if str(h.get("mint") or "") not in strays], now=now,
                     stopped=paths.stop_flag.exists(), switched_on=live_mode, holders=None,
                     other_copies=_other_copies(store, snipers), buys_by_sniper=buys, holdings_known=holdings_known,
                     tape_holding=tape_holding, paused=paused_rec,
                     guard=guard is None or guard.tripped or not guard.known,
                     stops=book_stops(cfg, paths))  # fmt: skip  # the owner's /risk values where set, else [book]'s
    # holders=None: while the wallet holds any coin every book sniper is kept. A measured fill can name the wrong leader
    # (a co-buying leader, a failed leader attempt, an older closed position in the same coin), and stopping the sniper
    # that really holds a coin strands it; each kept sniper can add at most max_buys_per_sniper buys.
    plan: BookPlan = plan_book(inp, b)
    if actors is None:  # who buys with whom could not be read: when in doubt the book adds no sniper
        plan.actions = [a for a in plan.actions if a.kind not in ("create", "start", "retire")]
    for a in plan.actions:  # why a qualified wallet gets no sniper of its own, once a day a wallet
        if a.kind == "skip" and ONE_SNIPER in a.reason:
            sig = f"[book] {(a.wallet or '')[:6]} gets no sniper of its own"
            if not notes.has_today(sig, now):
                notes.write(f"{sig}: {a.reason}", "info")
    if plan.kill_reason == GUARD_KILL and guard is not None:
        # the guard's own line says why the book halts (the book's own stops keep their own line); the owner is paged
        # when the book is on or stops a sniper, an off book with nothing running only notes it
        stopping = act_on_stops and any(a.kind == "stop" for a in plan.actions)
        risk_guard.announce(guard, notes, notifier, now, page=live_mode or stopping)
    if plan.paused:  # remember each live pause: the wallet stays out after its losing trips leave the window
        rec = dict(store.coverage(PAUSED_KEY) or {})
        versions = {v.get("wallet"): (v.get("copy_plan") or {}).get("lab_version") if isinstance(v.get("copy_plan"), dict)
                    else None for v in inp.qualified}  # fmt: skip
        for w, why in plan.paused.items():
            rec.setdefault(w, {"reason": why, "ts": int(now), "lab_version": versions.get(w)})
        store.set_coverage(PAUSED_KEY, rec)
    try:
        age = _tape_age(store, now)
    except Exception:  # noqa: BLE001
        age = None
    if age is not None and age > TAPE_STALE_SECS:
        # each sniper's buys and a fresh buy of ours are read on the tape: while it is behind, nothing new starts
        held_back = [a for a in plan.actions if a.kind in ("create", "start", "retire")]
        plan.actions = [a for a in plan.actions if a.kind not in ("create", "start", "retire")]
        sig = "[book] the tape is"
        if held_back and not notes.has_today(sig, now):
            notes.write(f"{sig} {int(age // 60)} min behind: no new sniper, start or replacement until it catches up "
                        f"({len(held_back)} held back)", "info")  # fmt: skip
    effective_dry = (not live_mode) or plan.kill_reason is not None
    stops = [a for a in plan.actions if a.kind == "stop" and a.sniper_id]
    kept = [a for a in plan.actions if a.kind == "keep" and "never stop a holder" in a.reason]
    # the guard's own line (risk_guard.announce) is only for a stop that holds; a guard that cannot tell (a stalled
    # measure stage, coins that could not be read) halts the book all the same, and this line says why
    blind = plan.kill_reason == GUARD_KILL and (guard is None or not guard.tripped)
    if plan.kill_reason and not notes.has_today(f"[decision] [book] kill: {plan.kill_reason}", now):
        line = (
            f"[book] kill: {plan.kill_reason}"
            + (f" ({guard.reason if guard is not None else 'the guard could not be read'})" if blind else "")
            + (" (stopping its snipers that hold nothing)" if act_on_stops and stops else "")
            + (f"; {len(kept)} book sniper(s) kept running while the wallet holds a coin (each can still buy up to "
               f"{b.max_buys_per_sniper})" if kept else "")
        )  # fmt: skip
        guarded = (
            plan.kill_reason == GUARD_KILL and not blind and not kept
        )  # the guard's own line told the owner; each stop has its own
        if not guarded and (
            live_mode or (act_on_stops and stops) or kept
        ):  # the owner hears it; an off book only notes it
            notifier.send(notes.write(line, "decision"))
        elif not notes.has_today(f"[book] kill: {plan.kill_reason}", now):
            notes.write(line, "info")
    qualified = inp.qualified
    best_q = max(qualified, key=lambda v: float(v.get("score") or 0), default=None)
    seen = {"ranked": len(plan.ranked),
            "best": {"wallet": best_q.get("wallet"), "score": best_q.get("score")} if best_q else None,
            "stale": len(_qualified(store)) - len(qualified)}  # fmt: skip
    counts: dict[str, int] = {}
    for a in plan.actions:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    if effective_dry:
        for a in plan.actions:
            if a.kind in ("create", "start", "stop", "retire"):
                sig = f"[book dry] would {a.kind} {(a.wallet or '')[:6]} {a.arm or ''}".rstrip()
                if not notes.has_today(sig, now):
                    floor = f" on its buys of {_floor(b, a):g} SOL or more" if a.kind == "create" else ""
                    size = f" ({a.fill_sol:g} SOL{floor})" if a.fill_sol else ""
                    exit_ = f", {_exit_words(_sent_exit(a, cfg))}" if a.kind == "create" else ""
                    notes.write(f"{sig}{size}{exit_} — {a.reason}", "info")
        if act_on_stops and plan.kill_reason:  # stops of non-holders are safety, not new money: on or off
            for a in stops:
                await _do(svc, notifier, notes, "stop", a)
        record_run(store, plan, True, now, **seen)
        _watch(cfg, store, notifier, notes, wallet, holdings, snipers, now)
        return {
            "dry": True,
            "kill_reason": plan.kill_reason,
            "actions": counts,
            "ranked": len(plan.ranked),
            "notes": plan.notes,
        }
    # live: stop → retire (stop, delete) → create → start
    for a in plan.actions:
        if a.kind == "stop" and a.sniper_id:
            await _do(svc, notifier, notes, "stop", a)
    not_replaced: set[str] = set()  # wallets whose old sniper is still there: no second one this run
    for a in plan.actions:
        if (
            a.kind == "retire"
            and a.sniper_id
            and not await _retire(svc, wallet, notifier, notes, a, store, now, strays)
        ):
            not_replaced.add(a.wallet or "")
    new_ids: list[tuple[str, Any]] = []
    from .switch import book_mcaps

    caps = book_mcaps(OperatorPaths(Path(cfg.storage.db_path).resolve().parent))
    for a in plan.actions:
        if a.kind == "create" and a.wallet and a.arm:
            if a.wallet in not_replaced:
                notes.write(f"[book] create {a.wallet[:6]} waits: its old sniper is still on OKTech", "info")
                continue
            arm, floor = b.bid_arms.get(a.arm) or {}, _floor(b, a)
            ex = _sent_exit(a, cfg)
            # lab goes by name, exactly the call the book always made; only a wallet's own exit is sent as exit_params
            own = ex is not None and ex.source == "wallet"
            name, extra = (ex.name, {"exit_params": dict(ex.params)}) if own else ("lab", {})
            if caps.get(a.wallet):
                extra["max_mcap_sol"] = caps[a.wallet]  # the owner's cap: only the leader's buys below it
            try:
                # salt = this run's second: a sniper replaced within 5 minutes needs an externalId of its own. The arm
                # is the floor of the buy bid, sent with the bid in the tip and the priority at OKTech's minimum
                # (policy.arm_bid: OKTech charges the buy priority again on every sell): the fee policy (fees.py) may
                # raise the tip for this leader on evidence inside the owner's [fees] bounds, and prices the sells
                # (sell tip + near-zero sell priority)
                rep = await svc.oktech_copylist([f"{a.wallet}:book:{floor:g}:{name}"], cfg.oktech.wallet or "SNIPER", create=True,
                                                buy_sol=a.fill_sol or b.fill_min_sol, max_buys=b.max_buys_per_sniper, exit_name=name,
                                                fee_floor=arm_bid(arm),
                                                send_count=b.send_count, tag=f"arm:{a.arm}",
                                                compute_units=arm.get("cu"), salt=f"g{int(now)}", **extra)  # fmt: skip
                for s in rep.get("snipers", []):
                    if s.get("oktech_id"):
                        new_ids.append((s["oktech_id"], a))
                notifier.send(notes.write(f"[book] create {a.wallet[:6]} {a.arm} {a.fill_sol:g} SOL on its buys of "
                                          f"{floor:g} SOL or more, {_exit_words(ex)} — {a.reason}", "decision"))  # fmt: skip
            except Exception as exc:  # noqa: BLE001
                notifier.send(
                    notes.write(
                        f"[book] create {a.wallet[:6]} failed: {type(exc).__name__}: {str(exc)[:120]}", "decision"
                    )
                )
    for sid, a in new_ids:
        try:
            await svc.oktech_task("start", sid)
            notifier.send(notes.write(f"[book] start {a.wallet[:6]} {a.arm} ({sid}) — new sniper", "decision"))
        except Exception as exc:  # noqa: BLE001
            notifier.send(notes.write(f"[book] start {sid} failed: {type(exc).__name__}: {str(exc)[:120]}", "decision"))
    for a in plan.actions:
        if a.kind == "start" and a.sniper_id:
            await _do(svc, notifier, notes, "start", a)
    record_run(store, plan, False, now, **seen)
    _watch(cfg, store, notifier, notes, wallet, holdings, snipers, now)
    return {
        "dry": False,
        "kill_reason": plan.kill_reason,
        "actions": counts,
        "ranked": len(plan.ranked),
        "notes": plan.notes,
    }


async def _do(svc, notifier: Notifier, notes: NoteSink, action: str, a) -> None:
    try:
        await svc.oktech_task(action, a.sniper_id)
        notifier.send(
            notes.write(
                f"[book] {action} {(a.wallet or '')[:6]} {a.arm or ''} ({a.sniper_id}) — {a.reason}", "decision"
            )
        )
    except Exception as exc:  # noqa: BLE001
        notifier.send(
            notes.write(f"[book] {action} {a.sniper_id} failed: {type(exc).__name__}: {str(exc)[:120]}", "decision")
        )


def _ignored(svc) -> list[str]:
    """The owner's [operator] ignore_mints: coins never counted as held (spam airdrops and the like)."""
    return list(getattr(getattr(getattr(svc, "cfg", None), "operator", None), "ignore_mints", None) or [])


async def _retire(
    svc,
    wallet: str,
    notifier: Notifier,
    notes: NoteSink,
    a,
    store=None,
    now: float | None = None,
    strays: set[str] | frozenset = frozenset(),
) -> bool:
    """Stop and delete a sniper that made its relied-on buy, once the wallet holds nothing; True when it is gone. The
    holdings are read before the stop and again after it (with the tape): a coin bought in between gets its sniper
    started again and nothing is deleted (a stopped sniper sells nothing, so its coin would be stranded)."""
    w6, sid = (a.wallet or "")[:6], a.sniper_id
    now = float(now if now is not None else 0)

    async def held() -> bool:
        try:
            if [p for p in await svc.open_positions(wallet, ignore=_ignored(svc)) if str(p.get("mint")) not in strays]:
                return True
        except Exception:  # noqa: BLE001 - unknown holdings: treat as held
            return True
        try:
            return bool(store is not None and now and _tape_holding(store, wallet, now))
        except Exception:  # noqa: BLE001
            return True

    if await held():
        notes.write(f"[book] retire {w6} ({sid}) waits: the wallet holds a coin again (or could not be read)", "info")
        return False
    with contextlib.suppress(Exception):  # an idle task may refuse a stop; the delete is what counts
        await svc.oktech_task("stop", sid)
    if await held():
        with contextlib.suppress(Exception):
            await svc.oktech_task("start", sid)
        notifier.send(notes.write(f"[book] retire {w6} ({sid}) undone: a coin appeared while it stopped; started again so "
                                  "it can sell it", "decision"))  # fmt: skip
        return False
    try:
        await svc.oktech_task("delete", sid)
    except Exception as exc:  # noqa: BLE001
        line = (f"[book] retire {w6} ({sid}) failed: it could not be deleted ({type(exc).__name__}: {str(exc)[:120]}); "
                "no replacement this run")  # fmt: skip
        if now and notes.has_today(f"[book] retire {w6} ({sid}) failed", now):
            notes.write(line, "info")
        else:
            notifier.send(notes.write(line, "decision"))
        return False
    notifier.send(notes.write(f"[book] retire {w6} {a.arm or ''} ({sid}) — {a.reason}", "decision"))
    return True
