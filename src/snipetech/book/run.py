"""`book run`: gather → plan → in dry mode write what it would do, in live mode execute through the Service's OKTech
calls (stop → retire → delete the ended → create → start), one Telegram line per action. Dry is the default and is forced under a kill
reason. A retire stops and deletes a sniper that made its relied-on buy while the wallet holds nothing, and the same
run creates its replacement."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import time
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
from ..positions import SPENT_STATUSES, curve_plain_need, is_active
from ..qualify.non_coin import NON_COIN_REASON, non_coin_traders
from .policy import (
    BOOK_NAME_RE,
    BUYING_STATUSES,
    CANNOT_PAY,
    GUARD_KILL,
    ONE_SNIPER,
    WAITS_FOR_MONEY,
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


# A sniper's leader is read for its ties this long after the sniper was removed. 19 Nov (a review): the clean-up
# deletes ended snipers, and a wallet that no longer qualifies was read only while OKTech listed a sniper of it. Where
# its record was the one tie between two qualified wallets, the delete untied them: two snipers on one signal.
ACTOR_MEMORY_SECS = 14 * 86400


def _actor_links(store, qualified: list[dict], tasks: list[dict], twins: bool = False,
                 now: float | None = None) -> dict[str, list[str]]:  # fmt: skip
    """Which wallets buy together, for the policy (BookInputs.actors): what qualify kept in each wallet's record
    (qualify/one_actor.py: its companions, the wallets it was chosen over, the one it yields to), for the qualified
    wallets and for the leaders our own records name for a sniper OKTech still lists (its tasks carry no config) or
    that was removed in the last ACTOR_MEMORY_SECS (`now`: when that is counted from; None reads the listed alone).
    A wallet whose record ties it to nobody is left out; no record at all says nothing, and the book then books that
    wallet by itself, as before 13 Nov. `twins`: the wallets each record names its twins instead
    (BookInputs.twins; one_actor.twin_links), which the first reading leaves out."""
    from ..qualify.one_actor import actor_block, actor_links, twin_links

    local = {r["id"]: r for r in store.oktech_snipers(active=False)}
    leaders = {w for t in tasks for w in copied_leaders(local.get(t.get("id")) or {})}
    if now is not None:
        leaders |= {w for r in local.values() if (r.get("removed_at") or 0) >= now - ACTOR_MEMORY_SECS
                    for w in copied_leaders(r)}  # fmt: skip
    out: dict[str, list[str]] = {}
    for w in dict.fromkeys([*(str(v["wallet"]) for v in qualified if v.get("wallet")), *sorted(leaders)]):
        links = (twin_links if twins else actor_links)(actor_block(store, w)) - {w}
        if links:
            out[w] = sorted(links)
    return out


def _bought_by_us(store, wallet: str, mint: str, marks: dict | None = None) -> bool:
    """Whether a buy of ours explains holding `mint`: a buy of the bot's by the wallet on the tape, or a buy fill of
    the bot's in the wallet (measure/ours.py: the fees a sniper of ours pays, a sniper or copied wallet the measure stage
    tied it to, or fees nobody recorded; `marks`: fee_marks, read here when not given). The owner's own buy at other
    fees explains nothing (22 Nov audit: three hand buys of one coin held every book sniper armed beside it). Any read
    that fails counts as ours (the coin then keeps equity unknown, as before)."""
    from types import SimpleNamespace

    from ..measure.ours import bot_buy, bot_tape_buy, fee_marks

    if not mint or mint == "unknown":
        return True
    try:
        marks = fee_marks(store) if marks is None else marks
        for priority, tip in store._q("SELECT priority, tip FROM trades WHERE mint=? AND user=? AND is_buy=1",
                                      (mint, wallet)):  # fmt: skip
            if bot_tape_buy(SimpleNamespace(priority=priority, tip=tip), marks):
                return True
        rows = store._q("SELECT sniper_id, wallet_copied, priority_lamports, tip_lamports FROM fills "
                        "WHERE wallet=? AND mint=? AND side='buy'", (wallet, mint))  # fmt: skip
        return any(bot_buy({"sniper_id": s, "wallet_copied": c, "priority_lamports": p, "tip_lamports": t}, marks)
                   for s, c, p, t in rows)  # fmt: skip
    except Exception:  # noqa: BLE001
        return True


STRAY_AFTER_SECS = 600  # a coin no buy of ours explains, seen this long: no tape lag can explain it either


def _strays(store, wallet: str, holdings: list[dict] | None, paths: OperatorPaths, now: float) -> set[str]:
    """Coins in the wallet that no buy of ours explains (an airdrop, the owner's own trade in another app or by hand
    in this wallet) and that the unsold-coin watch first saw STRAY_AFTER_SECS ago or more: no sniper of ours holds
    them, so they never keep a spent sniper from being replaced (8 Nov: a dust airdrop held every replacement for
    hours), nor a book sniper armed under a kill, nor the clean-up waiting. A coin seen just now may be a buy the tape
    has not caught up with, and still counts as held. So does one the measure stage has not run past since it showed:
    our copy into a coin the tape does not hold (an older PumpSwap coin) is on record only in that stage's fills, and
    while it stood still such a copy, held ten minutes, was a stray whose sniper a kill stopped (second round of the
    22 Nov audit). With no record of that stage at all (it never ran) the ten minutes decide alone, as before."""
    from ..measure.ours import fee_marks
    from .watch import _load, _measured_at

    seen = (_load(paths).get("first_seen") or {}) if holdings else {}
    marks = fee_marks(store) if holdings else None
    measured = _measured_at(store) if holdings else None
    out: set[str] = set()
    for h in holdings or []:
        m = str(h.get("mint") or "")
        t = seen.get(m)
        if not m or t is None or now - float(t) < STRAY_AFTER_SECS:
            continue
        if measured is not None and measured < float(t):
            continue  # the fills cannot hold its buy yet
        if not _bought_by_us(store, wallet, m, marks):
            out.add(m)
    return out


def _owners_coins(store, wallet: str, held: list[dict] | None) -> set[str]:
    """The coins among `held` (the wallet's coins as just read, each row with its `raw` amount) that are the owner's own
    beyond doubt (measure/ours.owner_coins: every buy of the coin by the wallet since its last sale of it paid fees no
    sniper of ours pays, and those buys explain all the wallet holds of it). No sniper on record holds them, so they
    keep no book sniper armed under a kill and hold back no retire and no clean-up: the rule /stop got on 22 Nov
    (operator/commands._not_ours). `_strays` alone kept such a coin a holding whenever the bot had ever bought it (a
    copy sold yesterday, the owner's hand buy today), and for ten minutes otherwise. Each reading of the wallet is
    weighed on its own amounts: more of the coin than the owner's buys explain may be a buy of ours not written yet.
    None when the records cannot be read: each coin may be ours."""
    from ..measure.ours import fee_marks, owner_coins

    if store is None or not held:
        return set()
    try:
        return set(owner_coins(store, wallet, held, fee_marks(store)))
    except Exception:  # noqa: BLE001 - not known: each coin may be ours
        return set()


def _no_fee_record(svc, snipers: list[dict]) -> set[str]:
    """The book's snipers among `snipers` (OKTech's tasks) the bot holds no fee record of (no payload of theirs on
    record names a priority: operator/commands._unrecorded, as /stop asks). The owner's coins are told by the fees of
    the snipers on record, so one of these may have bought a coin read as the owner's: it is never stopped, retired or
    deleted beside one. Every one when it cannot be told."""
    mine = [t for t in snipers if t.get("id") is not None and BOOK_NAME_RE.match(str(t.get("name") or ""))]
    try:
        from ..operator.commands import _unrecorded

        return set(_unrecorded(svc, mine))
    except Exception:  # noqa: BLE001 - not known: each may have bought it
        return {str(t["id"]) for t in mine}


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
    record of it (when it was made, what it copies) is not counted, so it is never retired. A buy at fees no sniper of
    ours pays is the owner's own (measure/ours.bot_tape_buy) and is no sniper's (22 Nov audit: counted for every book
    sniper, one hand buy would have retired them all)."""
    from types import SimpleNamespace

    from ..measure.ours import bot_tape_buy, fee_marks

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
    marks = fee_marks(store)
    buys = [(sig, mint, slot, pos, ts) for sig, mint, slot, pos, ts, priority, tip in
            store._q("SELECT DISTINCT sig, mint, slot, pos, ts, priority, tip FROM trades WHERE user=? AND is_buy=1 "
                     "AND ts>=? ORDER BY slot, pos", (wallet, since))
            if bot_tape_buy(SimpleNamespace(priority=priority, tip=tip), marks)]  # fmt: skip
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
    # the coins that are the owner's own beyond doubt hold no sniper on record either (as /stop reads them); a book
    # sniper with no fee record may have bought one, and stays beside it
    owners = _owners_coins(store, wallet, list(holdings or [])) if holdings_known else set()
    unrecorded = _no_fee_record(svc, snipers)
    not_ours = strays | owners
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
    twins: dict[str, list[str]] = {}
    try:  # which wallets buy together, and which are twins: one sniper an actor (policy.actor_members)
        actors: dict[str, list[str]] | None = _actor_links(store, bookable, snipers, now=now)
        twins = _actor_links(store, bookable, snipers, twins=True, now=now)
    except Exception as exc:  # noqa: BLE001 - not known this run: nothing new is added below
        actors = None
        sig = "[book] could not read which wallets buy together"
        if not notes.has_today(sig, now):
            notes.write(f"{sig} ({type(exc).__name__}): no new sniper, start or replacement in such a run", "info")
    non_coin: dict[str, str] | None = {}
    try:  # which of them swap into a dollar coin or wrapped SOL: a copy sniper follows them there (qualify/non_coin.py)
        non_coin = non_coin_traders(store, [str(v["wallet"]) for v in bookable if v.get("wallet")], now)
    except Exception as exc:  # noqa: BLE001 - not known this run: nothing new is added below
        non_coin = None  # a wallet stopped for it would be ranked again and its stopped sniper started
        sig = "[book] could not read which wallets trade a non-coin"
        if not notes.has_today(sig, now):
            notes.write(f"{sig} ({type(exc).__name__}): no new sniper, start or replacement in such a run", "info")
    inp = BookInputs(equity_sol=equity, day_realised_sol=acc["realised_sol"], day_risk_used_sol=acc["risk_used_sol"],
                     qualified=bookable, live=live_rows, actors=actors or {}, twins=twins, non_coin=non_coin or {},
                     bid_model={r["arm"]: r for r in store.bid_model()}, snipers=snipers,
                     holdings=[h for h in holdings or [] if str(h.get("mint") or "") not in not_ours], now=now,
                     stopped=paths.stop_flag.exists(), switched_on=live_mode, holders=None,
                     other_copies=_other_copies(store, snipers), buys_by_sniper=buys, holdings_known=holdings_known,
                     tape_holding=tape_holding, paused=paused_rec,
                     guard=guard is None or guard.tripped or not guard.known,
                     stops=book_stops(cfg, paths),  # the owner's /risk values where set, else [book]'s
                     native_sol=native, wsol=wsol, restart=_restarts(store, snipers),
                     may_hold=unrecorded if owners else set())  # fmt: skip
    # holders=None: while the wallet holds a coin of ours every book sniper is kept. A measured fill can name the wrong
    # leader (a co-buying leader, a failed leader attempt, an older closed position in the same coin), and stopping the
    # sniper that really holds a coin strands it; each kept sniper can add at most max_buys_per_sniper buys.
    plan: BookPlan = plan_book(inp, b)
    if actors is None or non_coin is None:  # a read failed: when in doubt the book adds no sniper
        plan.actions = [a for a in plan.actions if a.kind not in ("create", "start", "retire")]
    for a in plan.actions:  # a qualified wallet the book will not copy at all, once a day a wallet
        if a.kind == "skip" and a.reason.startswith(NON_COIN_REASON):
            sig = f"[book] {(a.wallet or '')[:6]} is not copied"
            if not notes.has_today(sig, now):
                notes.write(f"{sig}: {a.reason}", "decision")
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
    tape_behind = age is not None and age > TAPE_STALE_SECS
    if tape_behind:
        # each sniper's buys and a fresh buy of ours are read on the tape: while it is behind, nothing new starts
        held_back = [a for a in plan.actions if a.kind in ("create", "start", "retire")]
        plan.actions = [a for a in plan.actions if a.kind not in ("create", "start", "retire")]
        sig = "[book] the tape is"
        if held_back and not notes.has_today(sig, now):
            notes.write(f"{sig} {int(age // 60)} min behind: no new sniper, start or replacement until it catches up "
                        f"({len(held_back)} held back)", "info")  # fmt: skip
    # one live run on the wallet at a time: a cycler or a guard started by hand or by a test (the owner's 5 SOL day,
    # 21 Nov) reads every buy of the wallet as its own, so a book sniper armed beside it holds its re-arms back and its
    # buys count against its place rule. Stops of the book's own snipers that hold nothing still go out (safety)
    from ..operator.live_test import _drivers

    try:
        drivers = _drivers(paths)
    except Exception:  # noqa: BLE001 - not known this run: when in doubt the book adds no sniper
        drivers = [{"id": "unknown", "argv": ["a job list that could not be read"]}]
    if drivers:
        held_back = [a for a in plan.actions if a.kind in ("create", "start", "retire")]
        plan.actions = [a for a in plan.actions if a.kind not in ("create", "start", "retire")]
        sig = "[book] a live run drives the wallet"
        if held_back and not notes.has_today(sig, now):
            d = drivers[0]
            notes.write(f"{sig} ({' '.join(str(x) for x in (d.get('argv') or [])[:2])}, job {d.get('id')}): no new "
                        f"sniper, start or replacement until it ends ({len(held_back)} held back)", "info")  # fmt: skip
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
    # the ranked wallets no sniper is made for because the wallet cannot pay a buy (and those of their actors that wait
    # with them), and the ones that have or get a sniper: what the owner is told the book copies (book_reason)
    waits = [a for a in plan.actions if a.kind == "skip" and (CANNOT_PAY in a.reason or WAITS_FOR_MONEY in a.reason)]
    if waits:
        fill = min((float(a.fill_sol) for a in waits if a.fill_sol), default=None)
        seen |= {
            "waiting": len(waits),
            "armed": sum(counts.get(k, 0) for k in ("keep", "create", "start")),
            "waiting_fill": fill,
        }
        if fill and native is not None and wsol is not None:  # what one copy needs in plain SOL (policy.CANNOT_PAY)
            seen["waiting_need"] = round(curve_plain_need(wsol, fill), 4)
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
                await _do(svc, notifier, notes, "stop", a, store, now)
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
            await _do(svc, notifier, notes, "stop", a, store, now)
    not_replaced: set[str] = set()  # wallets whose old sniper is still there: no second one this run
    for a in plan.actions:
        if (
            a.kind == "retire"
            and a.sniper_id
            and not await _retire(svc, wallet, notifier, notes, a, store, now, strays, unrecorded)
        ):
            not_replaced.add(a.wallet or "")
    # the ended ones go before a new one is made: OKTech counts them against the key's 200 (19 Nov)
    await _prune_ended(svc, wallet, notes, snipers, store, now, strays, tape_behind=tape_behind, wanted=plan.wanted,
                       unrecorded=unrecorded)  # fmt: skip
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
                # the same refusal run after run is told once a day (19 Nov: 188 of one line in five hours)
                _tell_once_a_day(
                    store,
                    notifier,
                    notes,
                    f"[book] create {a.wallet[:6]} failed: {type(exc).__name__}: {str(exc)[:120]}",
                    now,
                )
    for sid, a in new_ids:
        try:
            await svc.oktech_task("start", sid)
            notifier.send(notes.write(f"[book] start {a.wallet[:6]} {a.arm} ({sid}) — new sniper", "decision"))
        except Exception as exc:  # noqa: BLE001
            _tell_once_a_day(
                store, notifier, notes, f"[book] start {sid} failed: {type(exc).__name__}: {str(exc)[:120]}", now
            )
    for a in plan.actions:
        if a.kind == "start" and a.sniper_id:
            await _do(svc, notifier, notes, "start", a, store, now)
    record_run(store, plan, False, now, **seen)
    _watch(cfg, store, notifier, notes, wallet, holdings, snipers, now)
    return {
        "dry": False,
        "kill_reason": plan.kill_reason,
        "actions": counts,
        "ranked": len(plan.ranked),
        "notes": plan.notes,
    }


async def _do(svc, notifier: Notifier, notes: NoteSink, action: str, a, store=None, now: float | None = None) -> None:
    """Stop or start one sniper and tell the owner. A failure that repeats run after run (OKTech refuses the same
    stop every five minutes) is told once a day when `store` and `now` are given (`_tell_once_a_day`)."""
    try:
        await svc.oktech_task(action, a.sniper_id)
        notifier.send(
            notes.write(
                f"[book] {action} {(a.wallet or '')[:6]} {a.arm or ''} ({a.sniper_id}) — {a.reason}", "decision"
            )
        )
    except Exception as exc:  # noqa: BLE001
        line = f"[book] {action} {a.sniper_id} failed: {type(exc).__name__}: {str(exc)[:120]}"
        if store is not None and now is not None:
            _tell_once_a_day(store, notifier, notes, line, now)
        else:
            notifier.send(notes.write(line, "decision"))


def _ignored(svc) -> list[str]:
    """The owner's [operator] ignore_mints: coins never counted as held (spam airdrops and the like)."""
    return list(getattr(getattr(getattr(svc, "cfg", None), "operator", None), "ignore_mints", None) or [])


async def _wallet_now(svc, wallet: str, strays: set[str] | frozenset,
                      store=None) -> tuple[list[dict], list[dict]] | None:  # fmt: skip
    """The wallet's coins by its own balances now (the owner's ignored coins left out), in two parts: those a sniper of
    ours may hold, and (given the `store`) those that are the owner's own on this reading's amounts (`_owners_coins`).
    A stray that is not the owner's is in neither. None when the wallet cannot be read."""
    try:
        held = list(await svc.open_positions(wallet, ignore=_ignored(svc)))
    except Exception:  # noqa: BLE001 - unknown holdings
        return None
    theirs = _owners_coins(store, wallet, held)
    own = [p for p in held if str(p.get("mint")) in theirs]
    return [p for p in held if str(p.get("mint")) not in theirs and str(p.get("mint")) not in strays], own


async def _coins_held(svc, wallet: str, strays: set[str] | frozenset, store=None,
                      blind: bool = False) -> list[dict] | None:  # fmt: skip
    """The wallet's coins a sniper of ours may hold now (`_wallet_now`): the strays and the owner's own coins left out;
    None when they cannot be read. `blind`: for a sniper the bot holds no fee record of (`_no_fee_record`), which may
    have bought a coin read as the owner's: those stay in (as /stop keeps such a sniper running beside one)."""
    got = await _wallet_now(svc, wallet, strays, store)
    if got is None:
        return None
    return [*got[0], *got[1]] if blind else got[0]


def _tape_holds(store, wallet: str, now: float) -> bool:
    """`_tape_holding`, with a tape that cannot be read counting as holding."""
    try:
        return bool(store is not None and now and _tape_holding(store, wallet, now))
    except Exception:  # noqa: BLE001
        return True


async def _holds_a_coin(svc, wallet: str, store, now: float, strays: set[str] | frozenset,
                       blind: bool = False) -> bool:  # fmt: skip
    """Whether the wallet holds a coin a sniper of ours may hold now (`_coins_held`; `blind`: a sniper with no fee
    record), by its own balances and by the tape (a buy seconds old); holdings that cannot be read count as held."""
    coins = await _coins_held(svc, wallet, strays, store, blind)
    return coins is None or bool(coins) or _tape_holds(store, wallet, now)


# A retire undone by a coin bought while its stop went out starts the sniper again (a stopped sniper sells nothing):
# this many tries, RESTART_PAUSE_SECS apart, each RESTART_SECS at most (OKTech's client waits out a rate limit for up to
# three minutes a call, and the machine ends the book's unit after four). 22 Nov audit: one try, its failure swallowed,
# and the owner was told it had started.
RESTART_TRIES = 2
RESTART_PAUSE_SECS = 2.0
RESTART_SECS = 45.0
# after the tries failed, OKTech's own list is read once, this long at most: a start whose answer came too late, or a
# second start refused because the first went through, left the sniper running (review of 22 Nov: the owner was told
# the start had failed, with a /sell line, while it ran)
RESTART_CHECK_SECS = 20.0
# the snipers such a start failed for: {sniper id: {"ts": when, "mints": the coins seen then}}. Written before the start
# (a run cut off on its way leaves it for the next one); the plan starts each while the wallet holds a coin
# (BookInputs.restart), and one OKTech lists running again, or not at all, leaves it
RESTART_KEY = "book:restart"


def _restarts(store, snipers: list[dict]) -> set[str]:
    """The snipers RESTART_KEY holds that OKTech still lists stopped (idle, or stopping on its way there); the rest
    leave the record. Nothing when it cannot be read."""
    try:
        rec = dict(store.coverage(RESTART_KEY) or {})
    except Exception:  # noqa: BLE001
        return set()
    stopped = {str(s.get("id")) for s in snipers if str(s.get("status")) in ("idle", "stopping")}
    keep = {sid: v for sid, v in rec.items() if sid in stopped}
    if keep != rec:
        with contextlib.suppress(Exception):
            store.set_coverage(RESTART_KEY, keep)
    return set(keep)


def _mark_restart(store, sid: str, now: float, mints: list[str], pending: bool) -> None:
    with contextlib.suppress(Exception):
        rec = dict(store.coverage(RESTART_KEY) or {})
        if pending:
            rec[sid] = {"ts": int(now), "mints": mints}
        else:
            rec.pop(sid, None)
        store.set_coverage(RESTART_KEY, rec)


async def _start_again(svc, sid: str) -> str | None:
    """Start `sid` again, RESTART_TRIES tries: None once OKTech has started it, else what the last try said. A try that
    fails here may have gone through there (its answer lost or late): after the tries OKTech's list decides, when it
    answers (RESTART_CHECK_SECS) and shows the sniper buying again."""
    said = ""
    for attempt in range(RESTART_TRIES):
        try:
            await asyncio.wait_for(svc.oktech_task("start", sid), RESTART_SECS)
            return None
        except Exception as exc:  # noqa: BLE001 - tried again, then told
            said = f"{type(exc).__name__}: {str(exc)[:120]}".rstrip(": ")
            if attempt + 1 < RESTART_TRIES:
                await asyncio.sleep(RESTART_PAUSE_SECS)
    try:
        tasks = (await asyncio.wait_for(svc.oktech_status(), RESTART_CHECK_SECS)).get("snipers") or []
    except Exception:  # noqa: BLE001 - not known: told as it was seen here, and the next run starts it
        return said
    runs = any(str(t.get("id")) == str(sid) and str(t.get("status")) in BUYING_STATUSES for t in tasks)
    return None if runs else said


TOLD_KEY = "book:told"  # the lines of today that Telegram has taken: {"day": "YYYY-MM-DD", "lines": [...]}


def _tell_once_a_day(store, notifier: Notifier, notes: NoteSink, line: str, now: float) -> None:
    """A line for the owner that every run would send again while its cause lasts (a create OKTech refuses): written
    to the notes once a day, in the words a note holds (NoteSink.stored: the check looked for the raw text and never
    found one with a line break or a trailing space), and sent until Telegram has taken it once that day (the note
    used to be written before the send, so a message that was not taken counted as told)."""
    line = notes.stored(line)
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    try:
        rec = store.coverage(TOLD_KEY) or {}
    except Exception:  # noqa: BLE001 - not known: told again, never lost
        rec = {}
    told = set(rec.get("lines") or []) if rec.get("day") == day else set()
    text = None if notes.has_today(line, now) else notes.write(line, "decision")
    if line in told:
        return
    if notifier.send(text or line):
        with contextlib.suppress(Exception):
            store.set_coverage(TOLD_KEY, {"day": day, "lines": sorted(told | {line})[-100:]})


# 19 Nov: 191 of the key's 200 snipers were the book's own and ended (OKTech: "finished"), and nothing ever deleted
# one: a retire deletes only a sniper the book itself stops after its buy. From 11:40 UTC OKTech refused every new
# sniper ("TASK_LIMIT: This key already has 200 snipers. Delete some before creating more").
PRUNE_PER_RUN = 30
# The longest the clean-up may take in one run, in seconds. The machine ends the book's unit after four minutes, and
# OKTech's client waits out a rate limit for up to three minutes a call: housekeeping never holds up the run's
# creates, starts and watch.
PRUNE_SECS = 45.0
PRUNE_REFUSALS = 3  # this many snipers that OKTech will not delete end the run's clean-up too
# The book's ended snipers as the last run saw them: {"ids": [...], "ts": when}. A sniper is deleted only when the run
# before already saw it ended. OKTech counts a sniper's attempts, so one can read "finished" while its last buy is
# still on its way: five minutes on that buy is in the wallet, and the wallet check keeps its sniper (a review, 19 Nov).
ENDED_KEY = "book:ended"


def _one_sniper_refused(exc: BaseException) -> bool:
    """OKTech answered about this one sniper (it will not delete it): the next one can still be tried. Not so when it
    gave no answer in time, the network failed, it asked us to slow down or its server broke: the next delete would
    meet the same."""
    from ..oktech import OktechError

    return (isinstance(exc, OktechError) and 400 <= int(exc.status or 0) < 500 and exc.status != 429
            and exc.code not in ("NETWORK", "RATE_LIMITED"))  # fmt: skip


async def _prune_ended(svc, wallet: str, notes: NoteSink, snipers: list[dict], store, now: float,
                       strays: set[str] | frozenset = frozenset(), tape_behind: bool = False,
                       wanted: set[str] | None = None,
                       unrecorded: set[str] | frozenset = frozenset()) -> int:  # fmt: skip
    """Delete the book's own snipers that have ended (positions.SPENT_STATUSES: they buy and sell nothing more), and,
    given the wallets the plan keeps a sniper for (`wanted`, first six characters), its stopped ("idle") snipers of any
    other wallet (19 Nov: 8 such tasks were left on the key, one since 9 Nov, a slow leak toward OKTech's 200; a wallet
    that comes back gets a new sniper in a second, its buy counter at zero), the
    oldest first and at most PRUNE_PER_RUN a run; how many went. Only one the run before already saw ended
    (ENDED_KEY), only while the wallet holds nothing a sniper of ours may hold (a wallet that could not be read counts
    as holding; a stray and the owner's own coin are none, `_owners_coins`; beside a coin read as the owner's, never a
    sniper with no fee record, `unrecorded`, which may be the one that bought it) and the tape
    is not behind (`tape_behind`: it could not show a buy of ours that is seconds old). Never a sniper that is not the
    book's by its name, one that still runs or waits, or one OKTech marks failed (that one is kept for someone to look
    at). Housekeeping: written down, never sent to the owner. A sniper OKTech will not delete is passed over
    (PRUNE_REFUSALS of them end the run's clean-up); no answer in PRUNE_SECS, or a failure that is not about the one
    sniper, ends it at once, and the next run goes on with the rest."""
    dropped: set[str] = set()  # stopped snipers of wallets the plan no longer copies
    for s in snipers:
        m = BOOK_NAME_RE.match(str(s.get("name") or ""))
        if s.get("id") and m and wanted is not None and str(s.get("status")) == "idle" and m["w6"] not in wanted:
            dropped.add(str(s["id"]))
    ended = sorted(
        {
            str(s["id"])
            for s in snipers
            if s.get("id") and BOOK_NAME_RE.match(str(s.get("name") or "")) and str(s.get("status")) in SPENT_STATUSES
        }
        | dropped
    )
    try:
        seen = {str(i) for i in (store.coverage(ENDED_KEY) or {}).get("ids") or []}
        store.set_coverage(ENDED_KEY, {"ids": ended, "ts": int(now or 0)})
    except Exception:  # noqa: BLE001 - what the run before saw is not known: nothing is deleted this run
        return 0
    due = [sid for sid in ended if sid in seen]
    if not due or tape_behind:
        return 0
    got = await _wallet_now(svc, wallet, strays, store)
    if got is None or got[0] or _tape_holds(store, wallet, float(now or 0)):
        return 0
    theirs = bool(got[1])
    if theirs:  # beside a coin read as the owner's, a sniper with no fee record may be the one that bought it
        due = [sid for sid in due if sid not in unrecorded]
        if not due:
            return 0
    done, refused, until, gone_dropped = 0, [], time.monotonic() + PRUNE_SECS, 0
    for sid in due[:PRUNE_PER_RUN]:
        try:
            await asyncio.wait_for(svc.oktech_task("delete", sid), max(0.01, until - time.monotonic()))
            done += 1
            gone_dropped += sid in dropped
        except Exception as exc:  # noqa: BLE001 - refused, or no answer in time
            refused.append(f"{type(exc).__name__}: {str(exc)[:120]}".rstrip(": "))
            if not _one_sniper_refused(exc) or len(refused) >= PRUNE_REFUSALS:
                break
    line = (f"[book] deleted {done} of its ended snipers on OKTech ({len(ended) - done} left): each had ended by the "
            "run before and the wallet holds nothing" + (" of ours" if theirs else ""))  # fmt: skip
    if gone_dropped:
        line += f"; {gone_dropped} of them stopped, of a wallet it no longer copies"
    if refused:
        line += f"; {len(refused)} would not go ({refused[0]})"
    if done or not notes.has_today("[book] deleted 0 of its ended snipers", now):
        notes.write(line, "info")
    return done


async def _retire(
    svc,
    wallet: str,
    notifier: Notifier,
    notes: NoteSink,
    a,
    store=None,
    now: float | None = None,
    strays: set[str] | frozenset = frozenset(),
    unrecorded: set[str] | frozenset = frozenset(),
) -> bool:
    """Stop and delete a sniper that made its relied-on buy, once the wallet holds nothing it may hold; True when it is
    gone. The holdings are read before the stop and again after it (with the tape): a coin bought in between gets its
    sniper started again and nothing is deleted (a stopped sniper sells nothing, so its coin would be stranded). That
    start is tried RESTART_TRIES times; when it fails the owner is told so, with the coin's /sell line, and the next run
    starts the sniper while the wallet holds a coin (RESTART_KEY), the book still copies its wallet and no stop holds
    (the plan's rule: running, it also buys). The owner's own coins are none of its coins, each reading weighed on its
    own amounts (`_owners_coins`), unless the bot holds no fee record of it (`unrecorded`)."""
    w6, sid = (a.wallet or "")[:6], a.sniper_id
    now = float(now if now is not None else 0)
    blind = sid in unrecorded  # no fee record of it: a coin read as the owner's may be its own

    if await _holds_a_coin(svc, wallet, store, now, strays, blind):
        notes.write(f"[book] retire {w6} ({sid}) waits: the wallet holds a coin again (or could not be read)", "info")
        return False
    with contextlib.suppress(Exception):  # an idle task may refuse a stop; the delete is what counts
        await svc.oktech_task("stop", sid)
    coins = await _coins_held(svc, wallet, strays, store, blind)
    if coins is None or coins or _tape_holds(store, wallet, now):
        mints = [str(c["mint"]) for c in coins or [] if c.get("mint")]
        if store is not None:
            _mark_restart(store, sid, now, mints, pending=True)
        failed = await _start_again(svc, sid)
        if failed is None:
            if store is not None:
                _mark_restart(store, sid, now, mints, pending=False)
            notifier.send(notes.write(f"[book] retire {w6} ({sid}) undone: a coin appeared while it stopped; started again "
                                      "so it can sell it", "decision"))  # fmt: skip
            return False
        coin = f" ({', '.join(m[:8] for m in mints)})" if mints else ""
        sell = f" Sell it now: /sell {mints[0]}" if len(mints) == 1 else ""
        # the plan starts it again only while the book still copies its wallet and no stop holds (running, it also
        # buys for that wallet): the line promises no more than that (review of 22 Nov)
        notifier.send(notes.write(f"[book] retire {w6} ({sid}) undone: a coin appeared while it stopped{coin}, and its "
                                  f"start after the stop failed ({failed}): nothing sells that coin while it stays "
                                  f"stopped; the next run tries to start it again, unless the book no longer copies "
                                  f"{w6} by then or a stop holds (then /sell, or a start in OKTech, sells it).{sell}",
                                  "decision"))  # fmt: skip
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
