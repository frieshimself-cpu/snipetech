"""Owner commands over Telegram: /status /stop /resume /book /sell /test /risk. Polled once per round and every minute
(`operate --commands`); only the owner's chat is obeyed.
/stop never strands a coin: a running sniper is left alone while the wallet holds a coin one of ours may hold (it is the only
thing that sells); the owner's ignored coins, the owner's own and strays no buy of ours explains are no sniper's. It
stops none on one reading: the wallet is read twice, and a buy on the tape or the chain whose coin has not shown yet
holds it back.
/sell is the owner's own exit for a coin OKTech abandoned (6 Nov 20:33: a copy bought 0.2 SOL of 5m5h…, the price fell
65 % within a second and no exit fired; DTAU and 4siK the same): it sells from the sniper wallet with SNIPER_SECRET
(solana/sell.py: on the pump.fun curve, or on PumpSwap once the coin has left it, since 7 Nov), never buys, and never
touches an OKTech task.
/test is the owner's own capped live test (live_test.py): it shows the plan, and "/test <id> go" only writes the request
the next operator round starts; nothing here creates or starts a sniper. /test auto is the owner's standing yes
(autotest.py): live trading that starts by itself and never stops; /stop holds it and /resume lets it go on.
/risk is the owner's wallet-wide guard (risk_guard.py): it shows the guard and today's result, and /risk stop, /risk floor
(each with SOL or off) and /risk default change the owner's operator/RISK; nothing the owner sent is repeated.
/oktech is what OKTech has confirmed (oktech_answers.py): until the owner's /oktech dev0 confirmed records that Hunt's
maxDevBuySol 0 skips every launch whose creator bought anything, no creator-0 rule starts live (research round 3).
/renew records when a paid plan ends (resources.renew_reply): the round reminds the owner 3 days before (20 Nov: Helius's
plan ran out unseen on 19 Nov)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ..book.switch import book_bar_reply, book_line, book_live, book_off_reply, book_switch, effective_min_score
from ..config import Config
from ..ops import redact
from ..positions import is_active
from ..solana.base58 import is_pubkey
from . import live_test, oktech_answers, risk_guard
from .budget_switch import budget_reply, effective_daily_usd
from .notify import API, Notifier, get_updates
from .paths import OperatorPaths
from .snapshot import Snapshot
from .tools import NoteSink

HELP = (
    "commands: /status (wallet, snipers, spend, resources)  /stop (stop snipers that hold nothing; expect none)  /resume  "
    "/book on|off (the live book, no SSH needed)  /book bar <SOL a trade>|default (which wallets it copies)  "
    "/sell <coin address> (sell all of that coin now; /sell alone lists the coins held, /sell all sells every one)  "
    "/budget [dollars a day] [turn N] [calls N] [every N hours]|default (what the agents may spend; every N hours is "
    "how often the analyst runs and, only when you name it, the least gap between a research agent's turns)  /budget wake table on|off "
    "(whether another agent's table change alone starts an agent's turn)  /credit <dollars> (what the Anthropic console shows, after a top-up: the count of the credit "
    "starts there; /credit alone says the count)  /renew <service> <date> (when a paid plan ends, as its billing page "
    "says, e.g. /renew helius 2026-12-20: a reminder comes 3 days before; /renew alone lists every date; services: "
    "helius server oktech gmgn birdeye solanatracker; /renew <service> off forgets one)  /test (a capped live test of the best hunt strategy: /test shows its plan, /test <id> go starts "
    "it at the next round, /stop ends it)  /test auto on|off (live trading that starts by itself and never stops: block "
    "after block while live holds up against paper; /stop holds it, /resume lets it go on)  /risk (the wallet-wide "
    "guard: no new live trade once the day is that far down or the wallet under its floor), one per message: "
    "/risk stop <SOL a day>, /risk stop off, /risk floor <SOL>, /risk floor off, /risk default  /oktech (what OKTech "
    "has confirmed: a hunt with a creator limit of 0 starts live only after /oktech dev0 confirmed; /oktech dev0 "
    "unconfirmed takes it back)  /why <wallet> (later phase)"
)


@dataclass
class Command:
    name: str
    args: list[str]
    chat_id: int
    update_id: int
    date: int | None = None  # when the owner sent it (Telegram's message date, unix seconds)


SELL_MAX_AGE_SECS = 300  # a /sell older than this (it waited out an outage) is refused: send it again
# how long /stop and /sell wait for one wallet read or one OKTech call (READ_SECS) and for a whole sale (SELL_SECS): the
# one-minute pass is killed at 2 minutes (snipetech-commands.service) once its Telegram offset has moved past the
# command, and the RPC client retries one call for minutes (7 Nov: about 250 s). Past it the owner hears so and asks again
READ_SECS = 20
SELL_SECS = 60
STOPS_SECS = 40  # all of /stop's stop calls together: six book snipers at READ_SECS each would outlast the pass
# Before /stop stops a sniper it reads the wallet a second time (live_test.SECOND_READ_SECS after the first, as the live
# test's close does) and looks at the chain for a buy on its way; each of the two waits this long at most, and all of
# /stop together STOP_BUDGET_SECS (its stops take what is left of it): still inside the pass's 2 minutes.
SECOND_WAIT_SECS = 10
STOP_BUDGET_SECS = 95
# a transaction of the wallet that landed since this long before /stop began and bought a coin whose tokens no reading
# showed is a coin on its way (as long as the runner waits for one: service.FRESH_HOLD_SECS), looked for among this
# many of its newest signatures: the failed sends are on that list too and are passed over unread (OKTech sends a buy
# and a sale several times over; 14 Nov: 50 failed sends in one burst), and the first one older than the look ends it
CHAIN_LOOK_SECS = 30
CHAIN_LOOK_SIGS = 50


def _waited(cmd: Command, now: float) -> int | None:
    """The minutes a command waited in Telegram's queue (an outage, a restart) when that is past SELL_MAX_AGE_SECS, else
    None."""
    if cmd.date is None or now - cmd.date <= SELL_MAX_AGE_SECS:
        return None
    return int((now - cmd.date) // 60)


def _book_loosens(cmd: Command) -> bool:
    """/book on, or /book bar with a value: the owner's book switch or its bar changed, not only shown. /book off only
    halts."""
    arg = cmd.args[0].lower() if cmd.args else ""
    return arg == "on" or (arg == "bar" and len(cmd.args) > 1)


def _late(exc: BaseException, secs: float, what: str) -> str:
    """An error in plain words for the owner: no answer within `secs`, or the error's kind."""
    return f"{what} did not answer in {secs:.3g} s" if isinstance(exc, TimeoutError) else type(exc).__name__


def parse_updates(updates: list[dict], owner_chat_id: int) -> tuple[list[Command], int | None]:
    """Commands from the owner's chat only, plus the highest update_id seen (so strangers' messages are skipped, not re-read)."""
    cmds: list[Command] = []
    last: int | None = None
    for u in updates:
        uid = u.get("update_id")
        if isinstance(uid, int):
            last = uid if last is None else max(last, uid)
        msg = u.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        text = str(msg.get("text") or "").strip()
        if chat != owner_chat_id or not text.startswith("/"):
            continue
        parts = text.split()
        name = parts[0][1:].split("@", 1)[0].lower()
        if name:
            cmds.append(
                Command(
                    name=name,
                    args=parts[1:],
                    chat_id=int(chat),
                    update_id=int(uid) if isinstance(uid, int) else 0,
                    date=int(msg["date"]) if isinstance(msg.get("date"), int) else None,
                )
            )
    return cmds, last


def poll_commands(token: str, paths: OperatorPaths, get: Callable[[str, dict], dict] | None = None) -> list[Command]:
    """One getUpdates call from the saved offset; the offset moves only after a successful read. Never raises."""
    try:
        state = json.loads(paths.telegram.read_text())
        owner = int(state["chat_id"])
    except (OSError, ValueError, TypeError, KeyError):
        return []
    params: dict = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if state.get("offset") is not None:
        params["offset"] = int(state["offset"])
    try:
        data = (get or get_updates)(f"{API}/bot{token}/getUpdates", params)
        cmds, last = parse_updates(list((data or {}).get("result") or []), owner)
    except Exception:  # noqa: BLE001 - Telegram being down must never fail a round
        return []
    if last is not None:
        state["offset"] = last + 1
        try:
            paths.telegram.write_text(json.dumps(state))
        except OSError:
            pass
    return cmds


def _ago(secs: float) -> str:
    if secs < 90:
        return f"{int(secs)} s ago"
    if secs < 5400:
        return f"{int(secs // 60)} min ago"
    return f"{secs / 3600:.1f} h ago"


def _status(cfg: Config, snap: Snapshot | None, paths: OperatorPaths, now: float, store=None) -> str:
    if snap is None:
        return "no snapshot yet; the next round (within 15 min) will have one"
    running = [s for s in snap.snipers or [] if is_active(s.get("status"))]
    eq = f"{snap.equity:.3f} SOL" if snap.equity is not None else "unknown"
    bad = [r for r in snap.resources if r.get("status") not in ("ok", None)][:3]
    stopped = paths.stop_flag.exists()
    lines = [
        f"snapshot {_ago(now - snap.ts)}" + (" | STOPPED by owner" if stopped else ""),
        f"wallet {eq}, {len(snap.held)} coin(s) held",
        f"snipers: {len(running)} running of {len(snap.snipers or [])}"
        if snap.snipers is not None
        else "snipers: unknown (OKTech not answering)",
        f"claude spend today {snap.spent_today:.2f} $ of {effective_daily_usd(cfg, paths):g} $",
        f"book: {'on' if book_live(cfg, paths) else 'off'}",
        risk_guard.status_line(risk_guard.from_snapshot(store, cfg, paths, now, snap), paths),
        _autotest_line(paths, now),
    ]
    if snap.fills:
        lines.append(f"fills since last round: {len(snap.fills)}")
    if bad:
        lines.append("resources: " + "; ".join(f"{r['name']} {r['status']}: {r.get('message', '')}" for r in bad))
    else:
        lines.append("resources: all ok")
    return "\n".join(lines)


def _autotest_line(paths: OperatorPaths, now: float) -> str:
    """The owner's automatic test for /status (autotest.status_line); its record never breaks /status."""
    try:
        from . import autotest

        return autotest.status_line(paths, now)
    except Exception as exc:  # noqa: BLE001 - /status still answers
        return f"automatic test: its record could not be read ({type(exc).__name__})"


async def _stop(svc, cfg: Config, paths: OperatorPaths, notes: NoteSink, now: float) -> str:
    paths.ensure()
    paths.stop_flag.write_text(str(int(now)))
    # after the flag: a live test not started yet is cancelled now (a /resume before the next round must not bring it
    # back), and a round in the middle of starting one reads the flag after its create and starts nothing
    try:
        test = live_test.on_stop(paths, notes, now)
    except Exception as exc:  # noqa: BLE001 - the kill switch never fails on the test's file; the round reads the flag
        test = (
            f"(the live test's record could not be updated: {type(exc).__name__}; the /stop flag stops it all the same)"
        )
    # the owner's automatic test holds while the flag exists (the widened yes of 7 Nov ~12:10 UTC: /stop holds every
    # start, a running block ends and is disarmed as any test is; /resume lets the same strategy go on)
    try:
        from . import autotest

        auto = autotest.on_stop(paths, notes, now)
    except Exception as exc:  # noqa: BLE001 - the flag still holds every start until /resume
        auto = (f"(the automatic test's record could not be read: {type(exc).__name__}; nothing starts while the "
                "/stop flag is set, and /test auto off turns it off)")  # fmt: skip
    reply = await _stop_snipers(svc, cfg, notes, paths, now)
    return "\n".join(x for x in (reply, test, auto) if x)


HAND_BOUGHT = "your own (bought by hand)"  # why a coin of the owner's keeps no sniper of ours running (_not_ours)


def _not_ours(svc, wallet: str, held: list[dict], paths: OperatorPaths, now: float) -> dict[str, str]:
    """The held coins no sniper of ours holds, each with why in the owner's words: the owner's own (every buy of it
    since its last sale paid fees none of our snipers pays, behind no wallet they copy: measure/ours.owner_coins; 22 Nov
    02:40 the owner bought a coin by hand in this wallet) and strays (no buy of ours explains it and the unsold-coin
    watch has seen it 10 minutes: book/run._strays; 8 Nov a dust airdrop). Stopping a sniper beside them strands
    nothing. A stray is told by its buys' fees alone there: one a buy of ours explains by its shape (a copy right
    behind the copied wallet at fees no payload names, measure/ours.bot_bought) is no stray (23 Nov audit). Empty when
    the store cannot say: every coin then keeps the snipers running, as before."""
    store = getattr(svc, "store", None)
    if store is None or not held:
        return {}
    from ..book.run import _strays
    from ..measure.ours import bot_bought, fee_marks, owner_coins

    try:
        marks = fee_marks(store)
        owners = owner_coins(store, wallet, held, marks)
        strays = {m for m in _strays(store, wallet, held, paths, now) if not bot_bought(store, wallet, m, marks)}
    except Exception:  # noqa: BLE001 - not known: each coin may be ours
        return {}
    return dict.fromkeys(owners, HAND_BOUGHT) | {m: "no buy of ours explains it" for m in strays if m not in owners}


def _unrecorded(svc, running: list[dict]) -> set[str]:
    """The running snipers whose fees the bot has no record of (no payload of theirs on record names a priority: one made
    in OKTech's app). The owner's coins are told by the fees of the snipers on record, so one of these may have bought
    one (review of 22 Nov). Every one when the record cannot be read."""
    from ..measure.ours import sniper_marks

    ids = {str(s.get("id")) for s in running}
    try:
        payloads = {str(r["id"]): r.get("payload") for r in svc.store.oktech_snipers(active=False)}
    except Exception:  # noqa: BLE001 - not known: each may have bought it
        return ids
    return {i for i in ids if not sniper_marks([payloads.get(i)])["buy"]}


async def _landed_buys(svc, wallet: str, since: float, found: list[tuple[str, str]]) -> None:
    """Into `found`, as (coin, signature): the coins the wallet bought in transactions that landed since `since` and
    has not sold since, read off its own newest signatures on the chain, newest first: a coin the tape does not follow
    (a PumpSwap coin past the tape's 30 minutes: three of the six copies of 20 Nov) is on no tape, and a buy of a
    second ago is not on it yet. None of the non-coins (wrapped SOL, a stablecoin). Nothing with no RPC to ask; a
    transaction the node cannot serve yet is skipped. A signature listed with no block time is one too fresh to have
    it, never an old one (review of 23 Nov: read as a time of zero, the freshest buy was passed over): its
    transaction says when. The first one older than `since` ends the look. A sale is read before the buy it followed,
    so what is found holds when the look is cut short."""
    rpc = getattr(svc, "rpc", None)
    if rpc is None:
        return
    from ..measure.chain_landing import read_fill
    from ..positions import NON_COIN_MINTS

    sold: set[str] = set()
    for s in await rpc.get_signatures(wallet, limit=CHAIN_LOOK_SIGS):  # newest first
        when = s.get("blockTime")
        if when is not None and int(when) < since:
            break  # older than the look, and so is every one after it
        if s.get("err") is not None or not s.get("signature"):
            continue
        tx = await rpc.get_transaction(s["signature"])
        if not tx:
            continue
        when = when if when is not None else tx.get("blockTime")
        if when is not None and int(when) < since:
            break
        fill = read_fill(tx, wallet, ours=True)
        if not fill or not fill.get("mint") or fill["mint"] in NON_COIN_MINTS:
            continue
        if fill["side"] == "sell":
            sold.add(fill["mint"])
        elif fill["side"] == "buy" and fill["mint"] not in sold and fill["mint"] not in {m for m, _sig in found}:
            found.append((fill["mint"], str(s["signature"])))


def _on_record(svc, sig: str) -> bool:
    """Whether the tape or the measure stage's fills hold a transaction of the wallet: then the reading of whose each
    coin is (`_not_ours`) has weighed it. False when the store cannot say."""
    store = getattr(svc, "store", None)
    if store is None:
        return False
    try:
        return bool(store._q("SELECT 1 FROM trades WHERE sig=? LIMIT 1", (sig,))
                    or store._q("SELECT 1 FROM fills WHERE sig=? LIMIT 1", (sig,)))  # fmt: skip
    except Exception:  # noqa: BLE001 - not known: the buy may be one nothing has weighed
        return False


async def _on_its_way(svc, cfg: Config, wallet: str, seen: set[str], now: float) -> list[str]:
    """The coins the wallet bought since moments before /stop began (`now`) whose tokens no reading of it showed
    (`seen`: the coins they did show, none of them a sniper's by our records). On the chain (`_landed_buys`, from
    CHAIN_LOOK_SECS before `now`): a coin the readings did not show, or one they showed whose buy neither the tape
    nor the fills hold yet, so nothing has weighed it (review of 23 Nov: a copy into a coin the owner holds, its tokens
    not shown yet, counted for nothing because "the coin was shown"). On the tape, looked at last (the chain can take
    SECOND_WAIT_SECS, and the recorder writes meanwhile): a buy since a minute before `now` that no sale followed
    (operator/leftovers._unseen, the rule the round's clean-up of a dead run stops by). The owner's ignored coins are
    left out of both. A chain that stops answering tells no more than it had: the readings and the tape decide."""
    from .leftovers import _unseen

    landed: list[tuple[str, str]] = []
    with contextlib.suppress(Exception):  # the chain cannot say more now: what it showed before still counts
        await asyncio.wait_for(_landed_buys(svc, wallet, now - CHAIN_LOOK_SECS, landed), SECOND_WAIT_SECS)
    skip = set(cfg.operator.ignore_mints or ())
    coming = [m for m, sig in landed if m not in skip and (m not in seen or not _on_record(svc, sig))]
    return coming + [m for m in _unseen(svc, cfg, wallet, {}, now) if m not in seen and m not in coming]


async def _stop_snipers(svc, cfg: Config, notes: NoteSink, paths: OperatorPaths, now: float) -> str:
    """Stop every sniper OKTech runs, unless the wallet holds a coin one of ours may hold: a stopped sniper strands its
    coin. The coins the owner ignores, the owner's own and strays (_not_ours) are no sniper's: beside only them every
    sniper is stopped (audit of 22 Nov: /stop stopped nothing while the owner's hand-bought coin sat in the wallet), but
    one whose fees the bot has no record of (_unrecorded), which may have bought the owner's coin. One reading with
    nothing of ours in it is no proof (23 Nov audit: a copy bought a second before the read had not shown yet): before
    any stop the wallet is read again live_test.SECOND_READ_SECS later, that reading weighed by its own amounts, and
    no sniper is stopped while the tape or the chain has a buy of the wallet whose tokens neither reading showed
    (`_on_its_way`: since moments before /stop began; the readings are weighed once more after it)."""
    loop = asyncio.get_running_loop()
    began = loop.time()
    ignore = list(cfg.operator.ignore_mints or ())
    try:  # the owner's ignored coins are left out, as the book, the watch and the runner leave them out
        held = await asyncio.wait_for(svc.open_positions(cfg.operator.wallet, ignore=ignore), READ_SECS)
    except TimeoutError:  # the pass must still answer before it is killed (audit of 22 Nov, round#9)
        notes.write(f"stopped by owner: flag set, but the wallet did not answer in {READ_SECS:g} s; no sniper stopped",
                    "decision")  # fmt: skip
        return (f"stop flag set (no new snipers expected), but the wallet did not answer in {READ_SECS:g} s, so no sniper "
                "was stopped: send /stop again.")  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        notes.write(
            f"stopped by owner: flag set, but holdings could not be read ({type(exc).__name__}); no sniper stopped",
            "decision",
        )
        return "stop flag set (no new snipers expected), but I could not read the wallet's holdings, so no sniper was stopped. Try again in a minute."
    try:
        st = await asyncio.wait_for(svc.oktech_status(), READ_SECS)
        running = [s for s in st.get("snipers", []) if is_active(s.get("status"))]
    except Exception as exc:  # noqa: BLE001
        why = _late(exc, READ_SECS, "OKTech")
        notes.write(f"stopped by owner: flag set, but OKTech did not answer ({why})", "decision")
        if isinstance(exc, TimeoutError):
            return f"stop flag set, but {why}; nothing stopped: send /stop again."
        return "stop flag set, but OKTech did not answer; nothing stopped. Try again in a minute."
    if not running:
        notes.write("stopped by owner: flag set, no sniper was running", "decision")
        return "stop flag set; no sniper was running."
    theirs = _not_ours(svc, cfg.operator.wallet, held, paths, now)
    mine = [h for h in held if str(h.get("mint")) not in theirs]
    ids = ", ".join(str(s.get("id")) for s in running)
    if not mine:  # a sniper would be stopped: one reading with nothing of ours is no proof
        try:
            await asyncio.sleep(live_test.SECOND_READ_SECS)
            again = await asyncio.wait_for(svc.open_positions(cfg.operator.wallet, ignore=ignore), SECOND_WAIT_SECS)
        except Exception as exc:  # noqa: BLE001 - unknown holdings never stop a sniper
            why = _late(exc, SECOND_WAIT_SECS, "it")
            notes.write(f"stopped by owner: flag set, but the wallet could not be read a second time ({why}); no "
                        "sniper stopped", "decision")  # fmt: skip
            return (f"stop flag set (no new snipers expected), but I could not read the wallet a second time ({why}), "
                    "and one reading with nothing held is no proof (a coin bought a second ago shows late), so no "
                    "sniper was stopped: send /stop again.")  # fmt: skip
        # weighed by its own amounts: more of an owner's coin than their buys explain may be a buy of ours in between
        theirs = _not_ours(svc, cfg.operator.wallet, again, paths, now)
        mine = [h for h in again if str(h.get("mint")) not in theirs]
        seen = {str(h.get("mint")) for h in (*held, *again)}
        held = again
        # from before /stop began (`now`), however long the readings took: a look back from this moment lost a buy
        # of just before the command behind slow readings (review of 23 Nov)
        coming = [] if mine else await _on_its_way(svc, cfg, cfg.operator.wallet, seen, now)
        if coming:
            coins = ", ".join(f"{m[:6]}…" for m in coming[:5])
            notes.write(f"stopped by owner: flag set; {len(running)} sniper(s) left running: the wallet bought {coins} "
                        "moments ago and what it bought has not shown in it yet", "decision")  # fmt: skip
            return (f"stop flag set, but {len(running)} sniper(s) left running ({ids}): the wallet bought {coins} "
                    "moments ago and what it bought has not shown in the wallet yet. A stopped sniper strands its "
                    "coin; send /stop again in a minute.")  # fmt: skip
        if not mine:  # weighed once more: the tape may have got a buy of ours while the chain was read
            theirs = _not_ours(svc, cfg.operator.wallet, again, paths, now)
            mine = [h for h in again if str(h.get("mint")) not in theirs]
    if mine:
        coins = ", ".join(f"{h.get('mint', '?')[:4]}… ({h.get('amount')})" for h in mine[:5])
        notes.write(
            f"stopped by owner: flag set; {len(running)} sniper(s) left running because coins are held: {coins}",
            "decision",
        )
        return f"stop flag set, but {len(running)} sniper(s) left running ({ids}) because coins are held: {coins}. A stopped sniper strands its coin; stop again once they are sold."
    # an owner's coin is told by the fees of the snipers on record: one the bot has no record of may have bought it
    own = [m for m, why in theirs.items() if why == HAND_BOUGHT]
    unknown = _unrecorded(svc, running) if own else set()
    kept = [str(s.get("id")) for s in running if str(s.get("id")) in unknown]
    running = [s for s in running if str(s.get("id")) not in unknown]
    still = (f"; left running: {', '.join(kept)} (no record here of the fees it pays, so {', '.join(m[:6] + '…' for m in own[:5])} "
             "may be its own: a stopped sniper strands its coin; stop it in OKTech once that is sold)" if kept else "")  # fmt: skip
    stopped, failed = [], []
    until = min(loop.time() + STOPS_SECS, began + STOP_BUDGET_SECS)
    for s in running:
        left = min(READ_SECS, until - loop.time())
        if left <= 0:
            failed.append(f"{s.get('id')} (not asked: OKTech was too slow; send /stop again)")
            continue
        try:
            await asyncio.wait_for(svc.oktech_task("stop", str(s.get("id"))), left)
            stopped.append(str(s.get("id")))
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{s.get('id')} ({_late(exc, left, 'OKTech')})")
    beside = "; ".join(f"{m[:6]}… {why}" for m, why in list(theirs.items())[:5])
    beside = f" (the wallet holds only coins no sniper of ours holds: {beside})" if beside else ""
    notes.write(
        f"stopped by owner: stopped {', '.join(stopped) or 'none'}"
        + (f"; failed {', '.join(failed)}" if failed else "")
        + beside
        + still,
        "decision",
    )
    return (
        f"stopped {len(stopped)} sniper(s): {', '.join(stopped) or 'none'}"
        + (f"; failed: {', '.join(failed)}" if failed else "")
        + f"{beside}{still}. No new snipers are expected until /resume."
    )


# -- /sell: the owner's own exit ---------------------------------------------------------------------------------------

SELL_SLIPPAGE = 0.5  # a sale fails rather than take less than half its quote (as `snipetech sweep`): a coin falling
# fast still sells, and nobody can take more than half of it on the way
LIST_MAX = 20  # coins a reply names one by one (a Telegram message holds 4,000 characters)
BY_HAND = "by hand in Axiom or OKTech"
# where a PumpSwap sale's SOL lands (solana/sell.py builds it from OKTech's own landed PumpSwap sale of 6 Nov, which paid
# into the wallet's WSOL account and left it open)
WSOL_WORDS = ("PumpSwap pays in wrapped SOL: it lands in the wallet's WSOL account, which OKTech trades from and the bot "
              "counts as SOL.")  # fmt: skip


def _sell_key(cfg: Config) -> tuple[str | None, str | None]:
    """(the wallet SNIPER_SECRET signs for, why /sell cannot sell with it, in plain words). The secret is read only to
    check it: nothing of it is ever repeated in a reply, a note or the log."""
    secret = os.environ.get("SNIPER_SECRET", "").strip()
    if not secret:
        return None, (
            "the sell key (SNIPER_SECRET) is not on the server. Put it in /etc/snipetech/env over SSH (never send it in chat)"
        )
    try:
        from ..solana.sell import keypair_from_secret

        wallet = str(keypair_from_secret(secret).pubkey())
    except Exception:  # noqa: BLE001 - whatever is wrong with it, its error text is not passed on
        return None, "the sell key (SNIPER_SECRET) in /etc/snipetech/env is not a valid secret key. Fix it over SSH"
    want = (cfg.operator.wallet or "").strip()
    if want and wallet != want:  # the list reads the operator's wallet: a sale must come from that one
        return wallet, (
            f"the sell key (SNIPER_SECRET) is for wallet {wallet[:6]}…, not the sniper wallet {want[:6]}…. Fix it in "
            "/etc/snipetech/env over SSH"
        )
    return wallet, None


def _why(exc: Exception) -> str:
    """A failed sale in plain words: an RPC error in the node's own words, any other error by its kind only (an HTTP
    error's text carries the RPC address, and with it the provider's key)."""
    from ..solana.rpc import RpcError

    if isinstance(exc, TimeoutError):  # the sale's own limit (SELL_SECS): it may have gone out all the same
        return f"Solana did not answer in {SELL_SECS:g} s"
    if isinstance(exc, RpcError):
        said = (exc.error or {}).get("message") if isinstance(exc.error, dict) else None
        what = "Solana refused the sale" if exc.method == "sendTransaction" else "Solana did not answer"
        return redact(f"{what}: {said or exc}")[:240]
    if isinstance(exc, httpx.HTTPError):
        return f"Solana did not answer ({type(exc).__name__})"
    return type(exc).__name__


def _skip_words(skipped: object) -> str:
    """Why the sweep left a coin, in plain words (its reasons: solana/sell.py, whose PumpSwap reasons are written for
    the owner and start with "PumpSwap: ")."""
    s = str(skipped or "")
    if s.startswith("PumpSwap: "):
        return f"has moved to PumpSwap, and could not be sold there: {s.removeprefix('PumpSwap: ')}"
    if "curve" in s:
        return "is not a pump.fun curve coin"
    return f"was left alone ({s})"


def _on_pumpswap(row: dict) -> bool:
    return row.get("venue") == "pumpswap"


async def _sell_list(svc, cfg: Config) -> str:
    """/sell alone: the coins the sniper wallet holds, each with its value and the exact line that sells it. Sells
    nothing and records nothing."""
    key_wallet, problem = _sell_key(cfg)
    wallet = (cfg.operator.wallet or "").strip() or key_wallet
    warn = f"\nNote: {problem}; until then /sell cannot sell." if problem else ""
    if not wallet:
        return (
            "I do not know the sniper wallet (operator.wallet is empty in the config), so I cannot list its coins."
            + warn
        )
    try:
        held = await asyncio.wait_for(svc.open_positions(wallet), READ_SECS)
    except Exception as exc:  # noqa: BLE001 - the owner hears it; the pass goes on
        failed = f"I could not read the sniper wallet's coins ({_late(exc, READ_SECS, 'it')}); nothing was sold."
        return f"{failed} Try /sell again in a minute.{warn}"
    coins: dict[str, float | None] = {}
    for h in held or []:
        m = h.get("mint")
        if not (isinstance(m, str) and is_pubkey(m)):
            continue
        v = None if h.get("value_sol") is None else float(h["value_sol"])
        if m in coins:  # two token accounts of one coin: one coin, worth both (unknown if either is unpriced)
            v = None if v is None or coins[m] is None else v + coins[m]
        coins[m] = v
    if not coins:
        return f"The sniper wallet {wallet[:6]}… holds no coins: nothing to sell." + warn
    order = sorted(coins.items(), key=lambda kv: (kv[1] is None, -(kv[1] or 0.0)))  # the most valuable first
    lines = [
        f"The sniper wallet {wallet[:6]}… holds {len(order)} coin(s); nothing was sold. To sell one, send its line:"
    ]
    for m, v in order[:LIST_MAX]:
        worth = f"worth about {v:.4f} SOL" if v is not None else "value not known yet (the tape has not priced it)"
        lines += [f"{m[:6]}… {worth}", f"/sell {m}"]
    if len(order) > LIST_MAX:
        lines.append(f"… and {len(order) - LIST_MAX} more.")
    lines.append("/sell all sells every one, on the pump.fun curve or on PumpSwap.")
    return "\n".join(lines) + warn


def _sold_one(mint: str, wallet: str, rows: list[dict], notes: NoteSink) -> str:
    """The reply to /sell <mint> from what the sweep did, and its decision note."""
    sold = [r for r in rows if r.get("signature")]
    if not rows:
        notes.write(
            f"owner's /sell {mint} from Telegram: nothing sold, the wallet {wallet} holds none of it", "decision"
        )
        return f"Nothing sold: the sniper wallet {wallet[:6]}… holds none of coin {mint}."
    if not sold:
        why = _skip_words(rows[0].get("skipped"))
        notes.write(f"owner's /sell {mint} from Telegram: not sold, the coin {why}", "decision")
        return f"Not sold: coin {mint} {why}. Sell it {BY_HAND}."
    quote = sum(float(r.get("quote_sol") or 0) for r in sold)
    floor = sum(float(r.get("floor_sol") or 0) for r in sold)
    sigs = [str(r["signature"]) for r in sold]
    where = " on PumpSwap" if any(_on_pumpswap(r) for r in sold) else ""
    notes.write(
        f"owner sold coin {mint}{where} with /sell from Telegram: sent for about {quote:.4f} SOL (floor {floor:.4f} "
        f"SOL), signature {', '.join(sigs)}",
        "decision",
    )
    head = (
        f"Sold coin {mint}{where} for about {quote:.4f} SOL, its price when sent. If the price falls below {floor:.4f} "
        "SOL before the sale lands, the sale fails and you keep the coin."
    )
    head += f" {WSOL_WORDS}" if where else ""
    tail = "Send /sell in a minute: if the coin is still listed, it did not sell."
    return "\n".join([head, *(f"Signature {s}\nhttps://solscan.io/tx/{s}" for s in sigs), tail])


def _sold_all(wallet: str, rows: list[dict], notes: NoteSink) -> str:
    """The reply to /sell all from what the sweep did, and its decision note."""
    sold = [r for r in rows if r.get("signature")]
    left = [r for r in rows if not r.get("signature")]
    if not rows:
        notes.write(f"owner's /sell all from Telegram: nothing sold, the wallet {wallet} holds no coins", "decision")
        return f"Nothing sold: the sniper wallet {wallet[:6]}… holds no coins."
    quote = sum(float(r.get("quote_sol") or 0) for r in sold)
    if sold:
        head = f"/sell all: sent {len(sold)} sale(s) for about {quote:.4f} SOL in all."
        head += " Each fails rather than take less than half its price."
    else:
        head = f"Nothing sold: /sell could sell none of the {len(left)} coin(s) the wallet holds."
    lines = [head]
    lines += [
        f"{r['mint']}: about {float(r.get('quote_sol') or 0):.4f} SOL{' on PumpSwap' if _on_pumpswap(r) else ''}, "
        f"signature {r['signature']}"
        for r in sold[:LIST_MAX]
    ]
    if len(sold) > LIST_MAX:
        lines.append(f"… and {len(sold) - LIST_MAX} more (each in the notes).")
    if any(_on_pumpswap(r) for r in sold):
        lines.append(WSOL_WORDS)
    if left:
        why = "; ".join(f"{r.get('mint')} {_skip_words(r.get('skipped'))}" for r in left[:LIST_MAX])
        more = f"; and {len(left) - LIST_MAX} more" if len(left) > LIST_MAX else ""
        lines.append(f"Not sold (sell these {BY_HAND}): {why}{more}.")
    if sold:
        lines.append("Send /sell in a minute: a coin still listed did not sell.")
    parts = [f"owner sold with /sell all from Telegram: sent {len(sold)} for about {quote:.4f} SOL"]
    parts += [f"{r['mint']}{' on PumpSwap' if _on_pumpswap(r) else ''} {r['signature']}" for r in sold]
    parts += [f"not sold: {r.get('mint')} {_skip_words(r.get('skipped'))}" for r in left]
    notes.write("; ".join(parts), "decision")
    return "\n".join(lines)


async def _sell(args: list[str], *, svc, cfg: Config, notes: NoteSink, sweep: Callable | None = None) -> str:
    """/sell <mint>: sell all of that coin; /sell all: every coin the wallet holds; /sell alone: list them. Only the
    owner's own message gets here (parse_updates). It sells and never buys, and it asks OKTech nothing."""
    if not args:
        return await _sell_list(svc, cfg)
    send = "Send /sell alone to list the coins held, each with its line to send."
    if len(args) > 1:
        return f"Nothing sold: one coin at a time, as /sell <coin address>, or /sell all for every coin. {send}"
    if args[0] == "all":
        mints, said = None, "/sell all"
    elif is_pubkey(args[0]):
        mints, said = [args[0]], f"/sell {args[0]}"
    else:  # never repeated: a secret key pasted by mistake must not reach the chat or the log again
        return f"Nothing sold: that is not a coin address (one word of 32 to 44 letters and digits). {send}"
    _, problem = _sell_key(cfg)
    if problem:
        notes.write(f"owner's {said} from Telegram: nothing sold, {problem}", "decision")
        return f"Nothing sold: {problem}, then send {said} again. Until then sell it {BY_HAND}."
    if sweep is None:
        from ..solana.sell import sweep
    try:  # the tape: a coin that left its curve is sold on the PumpSwap pool its migration record names
        res = await asyncio.wait_for(sweep(svc.rpc, mints, SELL_SLIPPAGE, store=getattr(svc, "store", None)), SELL_SECS)
    except Exception as exc:  # noqa: BLE001 - the owner hears what happened; the pass goes on
        why = _why(exc)
        notes.write(f"owner's {said} from Telegram stopped with an error ({why}); it may not have sold", "decision")
        if mints is None:
            return (
                f"Not sure what sold: /sell all stopped with an error ({why}). Coins before the error may have sold: "
                "send /sell to see what is still held."
            )
        return (
            f"Not sure it sold: the sale stopped with an error ({why}). Send /sell to see whether the coin is still "
            f"held; if it is, send {said} again or sell it {BY_HAND}."
        )
    wallet = str((res or {}).get("wallet") or "")
    rows = [r for r in (res or {}).get("results") or [] if isinstance(r, dict)]
    return _sold_all(wallet, rows, notes) if mints is None else _sold_one(mints[0], wallet, rows, notes)


async def handle_command(cmd: Command, *, svc, cfg: Config, paths: OperatorPaths, snap: Snapshot | None, notifier: Notifier,
                         notes: NoteSink, now: float, sweep: Callable | None = None) -> str:  # fmt: skip
    """Act on one owner command and send the reply. `sweep`: solana.sell.sweep unless a test hands in a fake."""
    if cmd.name == "status":
        reply = _status(cfg, snap, paths, now, store=getattr(svc, "store", None))
    elif cmd.name == "stop":
        reply = await _stop(svc, cfg, paths, notes, now)
    elif cmd.name in ("resume", "book") and (cmd.name == "resume" or _book_loosens(cmd)) and _waited(cmd, now):
        # the rule of /sell, /test and /risk: one that waited in Telegram's queue would lift the kill switch, or switch
        # the live book on, after the moment it was meant for (audit of 22 Nov). /stop and /book off are obeyed whatever
        # their age. A bar's value is not repeated
        mins, said = _waited(cmd, now), f"/{cmd.name}" + (f" {cmd.args[0].lower()}" if cmd.name == "book" else "")
        notes.write(f"owner's {said} from Telegram was {mins} min old when read: nothing changed", "decision")
        reply = (f"Nothing changed: this {said} is {mins} min old (it waited while the bot was not reading). Send it "
                 "again if you still mean it.")  # fmt: skip
    elif cmd.name == "resume":
        try:
            paths.stop_flag.unlink()
            # whatever a live test expected before the stop no longer holds; with no stop set, a running test's
            # expectation is left as it is (audit of 22 Nov: it was deleted first, and its sniper woke the agents)
            paths.expected.unlink(missing_ok=True)
            notes.write(
                "resumed by owner: stop flag removed; expected state back to none running, none held", "decision"
            )
            reply = "resumed: snipers may run again as planned (expected state reset: any sniper running is reported again)."
            try:  # what the owner's automatic test does next, said in the same reply
                from . import autotest

                auto = autotest.on_resume(paths, now)
            except Exception:  # noqa: BLE001 - the flag is gone all the same
                auto = None
            reply += f"\n{auto}" if auto else ""
        except FileNotFoundError:
            reply = "nothing to resume: no stop flag was set."
    elif cmd.name == "book":
        arg = cmd.args[0].lower() if cmd.args else "status"
        if arg in ("on", "off"):
            book_switch(paths, arg == "on", "telegram", now)
            if arg == "on":
                notes.write("owner switched the book on (/book on)", "decision")
                reply = ("book on: " + book_line(cfg, paths) + ". /book off or /stop halts it"
                         + (" (the /stop flag is set: it stays halted until /resume)" if paths.stop_flag.exists() else "") + ".")  # fmt: skip
            else:
                notes.write("owner switched the book off (/book off)", "decision")
                reply = book_off_reply(cfg, paths)
        elif arg == "bar":
            reply = book_bar_reply(cfg, paths, cmd.args[1] if len(cmd.args) > 1 else None, "telegram", now)
            if len(cmd.args) > 1 and not reply.startswith("bar not changed"):
                notes.write(f"owner set the book's bar: {reply}", "decision")
        else:
            from ..book.state import last_run_line

            store = getattr(svc, "store", None)
            reply = (
                f"book: {'on' if book_live(cfg, paths) else 'off'}, bar {effective_min_score(cfg, paths):g} SOL a trade"
                + (" (halted by /stop)" if paths.stop_flag.exists() else "")
                + (f"; {last_run_line(store, now)}" if store is not None else "")
            )
    elif cmd.name == "sell" and cmd.date is not None and now - cmd.date > SELL_MAX_AGE_SECS:
        # it waited in Telegram's queue (an outage, a restart): the coins held now may not be the ones meant
        mins = int((now - cmd.date) // 60)
        notes.write(
            f"owner's /sell {' '.join(cmd.args)} from Telegram was {mins} min old when read: nothing sold", "decision"
        )
        reply = f"Nothing sold: this /sell is {mins} min old (it waited while the bot was not reading). Send it again if you still mean it."
    elif cmd.name == "sell":
        reply = await _sell(cmd.args, svc=svc, cfg=cfg, notes=notes, sweep=sweep)
    elif cmd.name == "budget":
        reply = budget_reply(cfg, paths, cmd.args, "telegram", now)
        if cmd.args and not reply.startswith("budget not changed"):
            notes.write(f"owner set the agents' budget: {reply}", "decision")
    elif cmd.name == "credit":
        from .budget import Ledger
        from .resources import credit_reply

        reply = credit_reply(cfg, svc.store, Ledger(paths.spend, cfg.operator.prices), cmd.args, now)
        if reply.startswith("recorded"):
            notes.write(f"owner recorded the Anthropic credit (/credit): {reply}", "decision")
    elif cmd.name == "renew":
        from .resources import renew_reply

        reply = renew_reply(svc.store, cmd.args, now)
        if reply.startswith("recorded") or "no end date kept now" in reply:
            notes.write(f"owner recorded a plan end date (/renew): {reply}", "decision")
    elif cmd.name == "risk" and cmd.args and cmd.date is not None and now - cmd.date > SELL_MAX_AGE_SECS:
        # the same rule as /sell: a change that waited in Telegram's queue may loosen the guard after the moment it was
        # meant for. Its words are not repeated
        mins = int((now - cmd.date) // 60)
        reply = (f"risk guard not changed: this /risk is {mins} min old (it waited while the bot was not reading). Send "
                 "it again if you still mean it.")  # fmt: skip
    elif cmd.name == "risk":
        reply = risk_guard.risk_reply(cfg, paths, getattr(svc, "store", None), snap, cmd.args, "telegram", now)
        if reply.startswith(("risk guard set", "risk guard back")):
            notes.write(f"owner set the wallet-wide guard (/risk): {reply}", "decision")
    elif cmd.name == "test" and cmd.date is not None and now - cmd.date > SELL_MAX_AGE_SECS:
        # the same rule as /sell: a yes that waited in Telegram's queue may not fit the wallet or the strategies now.
        # Its words are not repeated: a secret key pasted by mistake must not reach the notes
        mins = int((now - cmd.date) // 60)
        if cmd.args[-1:] == ["go"]:
            notes.write(f"owner's /test go from Telegram was {mins} min old when read: nothing started", "decision")
        reply = (f"Nothing started: this /test is {mins} min old (it waited while the bot was not reading). Send it "
                 "again if you still mean it.")  # fmt: skip
    elif cmd.name == "test":
        reply = await live_test.command(cmd.args, svc=svc, cfg=cfg, paths=paths, snap=snap, notes=notes, now=now,
                                        date=cmd.date)  # fmt: skip
    elif cmd.name == "oktech":
        reply = oktech_answers.command(cmd.args, paths=paths, notes=notes, now=now, date=cmd.date)
    elif cmd.name == "why":
        reply = f"/{cmd.name} is available in a later phase."
    else:
        reply = HELP
    notifier.send(reply)
    return reply


def stopped_since(paths: OperatorPaths) -> float | None:
    try:
        return float(paths.stop_flag.read_text().strip() or 0)
    except (OSError, ValueError):
        return None if not paths.stop_flag.exists() else time.time()
