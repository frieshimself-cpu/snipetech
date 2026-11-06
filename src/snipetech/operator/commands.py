"""Owner commands over Telegram: /status /stop /resume. Polled once per round; only the owner's chat is obeyed.
/stop never strands a coin: a running sniper is left alone while the wallet holds anything (it is the only thing that sells)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..book.switch import book_line, book_live, book_off_reply, book_switch
from ..config import Config
from ..positions import is_active
from .notify import API, Notifier, get_updates
from .paths import OperatorPaths
from .snapshot import Snapshot
from .tools import NoteSink

HELP = (
    "commands: /status (wallet, snipers, spend, resources)  /stop (stop snipers that hold nothing; expect none)  /resume  "
    "/book on|off (the live book, no SSH needed)  /why <wallet> and /budget (later phase)"
)


@dataclass
class Command:
    name: str
    args: list[str]
    chat_id: int
    update_id: int


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
                Command(name=name, args=parts[1:], chat_id=int(chat), update_id=int(uid) if isinstance(uid, int) else 0)
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


def _status(cfg: Config, snap: Snapshot | None, paths: OperatorPaths, now: float) -> str:
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
        f"claude spend today {snap.spent_today:.2f} $ of {cfg.operator.daily_usd:.0f} $",
        f"book: {'on' if book_live(cfg, paths) else 'off'}",
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


async def handle_command(cmd: Command, *, svc, cfg: Config, paths: OperatorPaths, snap: Snapshot | None, notifier: Notifier,
                         notes: NoteSink, now: float) -> str:  # fmt: skip
    if cmd.name == "status":
        reply = _status(cfg, snap, paths, now)
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
                reply = ("book on: " + book_line(cfg) + ". /book off or /stop halts it"
                         + (" (the /stop flag is set: it stays halted until /resume)" if paths.stop_flag.exists() else "") + ".")  # fmt: skip
            else:
                notes.write("owner switched the book off (/book off)", "decision")
                reply = book_off_reply(cfg, paths)
        else:
            reply = f"book: {'on' if book_live(cfg, paths) else 'off'}" + (
                " (halted by /stop)" if paths.stop_flag.exists() else ""
            )
    elif cmd.name in ("why", "budget"):
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
