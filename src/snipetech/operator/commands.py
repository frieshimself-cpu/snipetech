"""Owner commands over Telegram: /status /stop /resume /book /sell /test /risk. Polled once per round and every minute
(`operate --commands`); only the owner's chat is obeyed.
/stop never strands a coin: a running sniper is left alone while the wallet holds anything (it is the only thing that sells).
/sell is the owner's own exit for a coin OKTech abandoned (6 Nov 20:33: a copy bought 0.2 SOL of 5m5h…, the price fell
65 % within a second and no exit fired; DTAU and 4siK the same): it sells from the sniper wallet with SNIPER_SECRET
(solana/sell.py, pump.fun curve coins only), never buys, and never touches an OKTech task.
/test is the owner's own capped live test (live_test.py): it shows the plan, and "/test <id> go" only writes the request
the next operator round starts; nothing here creates or starts a sniper.
/risk is the owner's wallet-wide guard (risk_guard.py): it shows the guard and today's result, and /risk stop, /risk floor
and /risk default change the owner's operator/RISK; nothing the owner sent is repeated."""

from __future__ import annotations

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
from . import live_test, risk_guard
from .budget_switch import budget_reply, effective_daily_usd
from .notify import API, Notifier, get_updates
from .paths import OperatorPaths
from .snapshot import Snapshot
from .tools import NoteSink

HELP = (
    "commands: /status (wallet, snipers, spend, resources)  /stop (stop snipers that hold nothing; expect none)  /resume  "
    "/book on|off (the live book, no SSH needed)  /book bar <SOL a trade>|default (which wallets it copies)  "
    "/sell <coin address> (sell all of that coin now; /sell alone lists the coins held, /sell all sells every one)  "
    "/budget [dollars a day] [turn N] [calls N] [every N hours]|default (what the agents may spend and how often the "
    "analyst runs)  /test (a capped live test of the best hunt strategy: /test shows its plan, /test <id> go starts "
    "it at the next round, /stop ends it)  /risk [stop <SOL a day>|floor <SOL>|default] (the wallet-wide guard: no new "
    "live trade once the day is that far down or the wallet under its floor)  /why <wallet> (later phase)"
)


@dataclass
class Command:
    name: str
    args: list[str]
    chat_id: int
    update_id: int
    date: int | None = None  # when the owner sent it (Telegram's message date, unix seconds)


SELL_MAX_AGE_SECS = 300  # a /sell older than this (it waited out an outage) is refused: send it again


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
    ]
    if snap.fills:
        lines.append(f"fills since last round: {len(snap.fills)}")
    if bad:
        lines.append("resources: " + "; ".join(f"{r['name']} {r['status']}: {r.get('message', '')}" for r in bad))
    else:
        lines.append("resources: all ok")
    return "\n".join(lines)


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
    reply = await _stop_snipers(svc, cfg, notes)
    return f"{reply}\n{test}" if test else reply


async def _stop_snipers(svc, cfg: Config, notes: NoteSink) -> str:
    try:
        held = await svc.open_positions(cfg.operator.wallet)
    except Exception as exc:  # noqa: BLE001
        notes.write(
            f"stopped by owner: flag set, but holdings could not be read ({type(exc).__name__}); no sniper stopped",
            "decision",
        )
        return "stop flag set (no new snipers expected), but I could not read the wallet's holdings, so no sniper was stopped. Try again in a minute."
    try:
        st = await svc.oktech_status()
        running = [s for s in st.get("snipers", []) if is_active(s.get("status"))]
    except Exception as exc:  # noqa: BLE001
        notes.write(f"stopped by owner: flag set, but OKTech did not answer ({type(exc).__name__})", "decision")
        return "stop flag set, but OKTech did not answer; nothing stopped. Try again in a minute."
    if not running:
        notes.write("stopped by owner: flag set, no sniper was running", "decision")
        return "stop flag set; no sniper was running."
    if held:
        coins = ", ".join(f"{h.get('mint', '?')[:4]}… ({h.get('amount')})" for h in held[:5])
        ids = ", ".join(str(s.get("id")) for s in running)
        notes.write(
            f"stopped by owner: flag set; {len(running)} sniper(s) left running because coins are held: {coins}",
            "decision",
        )
        return f"stop flag set, but {len(running)} sniper(s) left running ({ids}) because coins are held: {coins}. A stopped sniper strands its coin; stop again once they are sold."
    stopped, failed = [], []
    for s in running:
        try:
            await svc.oktech_task("stop", str(s.get("id")))
            stopped.append(str(s.get("id")))
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{s.get('id')} ({type(exc).__name__})")
    notes.write(
        f"stopped by owner: stopped {', '.join(stopped) or 'none'}"
        + (f"; failed {', '.join(failed)}" if failed else ""),
        "decision",
    )
    return (
        f"stopped {len(stopped)} sniper(s): {', '.join(stopped) or 'none'}"
        + (f"; failed: {', '.join(failed)}" if failed else "")
        + ". No new snipers are expected until /resume."
    )


# -- /sell: the owner's own exit ---------------------------------------------------------------------------------------

SELL_SLIPPAGE = 0.5  # a sale fails rather than take less than half its quote (as `snipetech sweep`): a coin falling
# fast still sells, and nobody can take more than half of it on the way
LIST_MAX = 20  # coins a reply names one by one (a Telegram message holds 4,000 characters)
BY_HAND = "by hand in Axiom or OKTech"


def _sell_key(cfg: Config) -> tuple[str | None, str | None]:
    """(the wallet SNIPER_SECRET signs for, why /sell cannot sell with it, in plain words). The secret is read only to
    check it: nothing of it is ever repeated in a reply, a note or the log."""
    secret = os.environ.get("SNIPER_SECRET", "").strip()
    if not secret:
        return None, (
            "the sell key (SNIPER_SECRET) is not on the server. Put it in /etc/snipetech/env over SSH (never send it in chat)"
        )
    try:
        from solders.keypair import Keypair

        wallet = str(Keypair.from_base58_string(secret).pubkey())
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

    if isinstance(exc, RpcError):
        said = (exc.error or {}).get("message") if isinstance(exc.error, dict) else None
        what = "Solana refused the sale" if exc.method == "sendTransaction" else "Solana did not answer"
        return redact(f"{what}: {said or exc}")[:240]
    if isinstance(exc, httpx.HTTPError):
        return f"Solana did not answer ({type(exc).__name__})"
    return type(exc).__name__


def _skip_words(skipped: object) -> str:
    """Why the sweep left a coin, in plain words (its reasons: solana/sell.py)."""
    s = str(skipped or "")
    if "migrated" in s or "complete" in s:
        return "has left the pump.fun curve (it moved to PumpSwap)"
    if "curve" in s:
        return "is not a pump.fun curve coin"
    return f"was left alone ({s})"


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
        held = await svc.open_positions(wallet)
    except Exception as exc:  # noqa: BLE001 - the owner hears it; the pass goes on
        failed = f"I could not read the sniper wallet's coins ({type(exc).__name__}); nothing was sold."
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
    lines.append("/sell all sells every one still on the pump.fun curve.")
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
        return f"Not sold: coin {mint} {why}, and /sell only sells coins on the pump.fun curve. Sell it {BY_HAND}."
    quote = sum(float(r.get("quote_sol") or 0) for r in sold)
    floor = sum(float(r.get("floor_sol") or 0) for r in sold)
    sigs = [str(r["signature"]) for r in sold]
    notes.write(
        f"owner sold coin {mint} with /sell from Telegram: sent for about {quote:.4f} SOL (floor {floor:.4f} SOL), "
        f"signature {', '.join(sigs)}",
        "decision",
    )
    head = (
        f"Sold coin {mint} for about {quote:.4f} SOL, its price when sent. If the price falls below {floor:.4f} SOL "
        "before the sale lands, the sale fails and you keep the coin."
    )
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
        f"{r['mint']}: about {float(r.get('quote_sol') or 0):.4f} SOL, signature {r['signature']}"
        for r in sold[:LIST_MAX]
    ]
    if len(sold) > LIST_MAX:
        lines.append(f"… and {len(sold) - LIST_MAX} more (each in the notes).")
    if left:
        why = "; ".join(f"{r.get('mint')} {_skip_words(r.get('skipped'))}" for r in left[:LIST_MAX])
        more = f"; and {len(left) - LIST_MAX} more" if len(left) > LIST_MAX else ""
        lines.append(f"Not sold (sell these {BY_HAND}): {why}{more}.")
    if sold:
        lines.append("Send /sell in a minute: a coin still listed did not sell.")
    parts = [f"owner sold with /sell all from Telegram: sent {len(sold)} for about {quote:.4f} SOL"]
    parts += [f"{r['mint']} {r['signature']}" for r in sold]
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
    try:
        res = await sweep(svc.rpc, mints, SELL_SLIPPAGE)
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
    elif cmd.name == "resume":
        try:
            paths.expected.unlink()  # whatever a live test expected before the stop no longer holds
        except FileNotFoundError:
            pass
        try:
            paths.stop_flag.unlink()
            notes.write(
                "resumed by owner: stop flag removed; expected state back to none running, none held", "decision"
            )
            reply = "resumed: snipers may run again as planned (expected state reset: any sniper running is reported again)."
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
