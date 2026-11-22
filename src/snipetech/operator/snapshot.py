"""The facts, gathered without an LLM: services, jobs, the sniper wallet, OKTech sniper states, fills, spend."""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..jobfiles import jobs_since
from .budget import Ledger
from .paths import OperatorPaths
from .refusals import OKTECH_REFUSED_KEY, RPC_REFUSED_KEY, clear_refusal, note_refusal, refusal_status, scrub

WSOL_MINT = "So11111111111111111111111111111111111111112"
# Long-running services and the timers must be active; the oneshot services (snipetech-daily, snipetech-operator)
# are inactive most of the day by design and only matter when they are "failed".
WATCHED_UNITS = (
    "snipetech-tape", "snipetech-ops", "snipetech-daily.timer", "snipetech-operator.timer",
    "snipetech-discover.timer", "snipetech-qualify.timer", "snipetech-measure.timer", "snipetech-book.timer",
    "snipetech-events", "snipetech-strategist.timer", "snipetech-commands.timer", "snipetech-snapshot.timer",
)  # fmt: skip
ONESHOT_UNITS = (
    "snipetech-daily", "snipetech-operator", "snipetech-discover", "snipetech-qualify",
    "snipetech-measure", "snipetech-book", "snipetech-strategist", "snipetech-commands", "snipetech-snapshot",
)  # fmt: skip


def book_fill_sol(cfg: Config) -> float:
    """What one copy of the book buys: its smallest fill (a wallet's own size is never under it)."""
    return float(cfg.book.fill_min_sol)


@dataclass
class Snapshot:
    ts: float
    services: dict[str, str] = field(default_factory=dict)
    tape_last_ts: int | None = None
    jobs_running: list[dict] = field(default_factory=list)
    jobs_ended: list[dict] = field(default_factory=list)
    jobs_recent: list[dict] = field(default_factory=list)  # research jobs that ended in the last 24 h, newest first
    wallet: str = ""
    native_sol: float | None = None
    wsol: float | None = None
    held: list[dict] = field(default_factory=list)
    snipers: list[dict] | None = None
    fills: list[dict] = field(default_factory=list)
    paper: dict | None = None
    spent_today: float = 0.0
    errors: list[str] = field(default_factory=list)
    reason_ts: dict[str, float] = field(default_factory=dict)  # state reason -> when it last triggered a call
    failed_tx_1h: int | None = None  # failed transactions by the wallet in the last hour (None = could not read)
    verdicts: dict[str, int] = field(default_factory=dict)  # the qualify stage's candidates by effective status
    resources: list[dict] = field(default_factory=list)  # filled by operator.resources after the snapshot
    book_live: bool = False  # the live book was on (config or the owner's /book on) when this snapshot was taken
    book_fill_sol: float | None = None  # what one copy of the book buys: the size buys_payable counts at
    equity_ref: float | None = None  # the last total equity a round could value (carried while a coin is unpriced)
    ok_tx_1h: int | None = None  # landed transactions by the wallet in the last hour (None = could not read)
    fills_unread: bool = False  # the wallet's trades could not be read this round: the gate counts them as the bot's
    bot_since_ref: bool = False  # the bot traded since the round that set equity_ref (carried with it)
    bot_cash: float | None = None  # this round: SOL the bot's own trades took in less what they paid, fees in
    bot_cash_since_ref: float | None = None  # the same since the round that set equity_ref (carried with it)
    ref_ours_sol: float | None = None  # what the bot's own coins were worth in the round that set equity_ref
    held_ts: float | None = None  # when `held` was read from the wallet (a round that could not read it keeps the last)

    @property
    def equity(self) -> float | None:
        if self.native_sol is None or self.wsol is None:
            return None
        return self.native_sol + self.wsol

    @property
    def total_equity(self) -> float | None:
        """SOL + wrapped SOL + the tape value of every coin held; None when a balance or a coin's price is unknown.
        The book's own buys move SOL into coins: this is the equity they do not move."""
        if self.native_sol is None or self.wsol is None:
            return None
        total = self.native_sol + self.wsol
        for h in self.held or []:
            if h.get("priced") is False or h.get("value_sol") is None:
                return None
            total += float(h["value_sol"])
        return total

    @property
    def bot_valued(self) -> bool:
        """Whether this round can value what the bot holds: the balances were read and every coin of the bot's (`ours`)
        has a price. A coin that is not the bot's (an airdrop the tape never priced, the owner's coin on a venue it does
        not record) leaves the whole wallet unvalued, never the bot's own result (audit 22 Nov, round#0)."""
        if self.native_sol is None or self.wsol is None:
            return False
        return all(h.get("priced") is not False and h.get("value_sol") is not None
                   for h in self.held or [] if h.get("ours", True))  # fmt: skip

    def to_json(self) -> str:
        return json.dumps(asdict(self) | {"equity": self.equity}, default=str)

    @classmethod
    def from_json(cls, text: str) -> Snapshot:
        d = json.loads(text)
        d.pop("equity", None)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_metas(jobs_dir: Path, since: float | None = None) -> list[dict]:
    """The job records, the oldest job first. With `since`, only those that can matter to a reader of what happened
    since then: the others are not opened (`jobfiles.jobs_since`; 11 Nov: every reader opened all 1,656)."""
    out = []
    for jid in jobs_since(jobs_dir, since):
        try:
            out.append(json.loads((jobs_dir / f"{jid}.json").read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def ended_since(jobs_dir: Path, since_ts: float, already: set[str], now: float | None = None) -> list[dict]:
    """Jobs that ended at or after `since_ts`. A 'running' meta whose process is gone is reconciled and written back
    (ended = now, exit_code None) so it is reported once and then retires like any other ended job. An ended job with
    no exit file (killed with its wrapper, or from before jobs wrote one) gets one, so that no reader opens its record
    again to learn that it does not run."""
    now = time.time() if now is None else now
    out = []
    for m in _read_metas(jobs_dir, since_ts):
        jid = m.get("id")
        if not jid or jid in already:
            continue
        exit_file = jobs_dir / f"{jid}.exit"
        if m.get("running"):
            if exit_file.exists():
                try:
                    code: int | None = int(exit_file.read_text().strip() or 0)
                except ValueError:
                    code = None
                m = m | {"running": False, "ended": int(exit_file.stat().st_mtime), "exit_code": code}
            elif not _pid_alive(m.get("pid")):
                m = m | {"running": False, "ended": int(now), "exit_code": None, "note": "process gone"}
            else:
                continue
            try:
                (jobs_dir / f"{jid}.json").write_text(json.dumps(m))
            except OSError:
                pass
        if not exit_file.exists():
            try:
                exit_file.write_text("" if m.get("exit_code") is None else f"{m['exit_code']}\n")
                if m.get("ended"):  # dated when the job ended, as the wrapper's own would be
                    os.utime(exit_file, (float(m["ended"]), float(m["ended"])))
            except OSError:
                pass
        ended = m.get("ended")
        if ended is None or ended >= since_ts:
            # `by`: who started it (ops.Job.by); the gate reads it
            out.append({"id": jid, "argv": m.get("argv", []), "exit_code": m.get("exit_code"), "ended": ended,
                        "log": str(jobs_dir / f"{jid}.log")} | {k: m[k] for k in ("note", "by") if m.get(k)})  # fmt: skip
    return out


def recent_jobs(jobs_dir: Path, now: float, hours: float = 24.0) -> list[dict]:
    """Research jobs (lab, follow, copy-rank, ...) that ended in the last `hours`, newest first: the outputs an
    analyst turn must read before starting anything."""
    from .jobs import RESEARCH_COMMANDS

    out = []
    for m in _read_metas(jobs_dir, now - hours * 3600):
        argv = m.get("argv") or []
        ended = m.get("ended")
        if (
            not argv
            or argv[0] not in RESEARCH_COMMANDS
            or m.get("running")
            or ended is None
            or ended < now - hours * 3600
        ):
            continue
        out.append({"id": m.get("id"), "argv": argv, "exit_code": m.get("exit_code"), "ended": ended,
                    "log": str(jobs_dir / f"{m.get('id')}.log")})  # fmt: skip
    return sorted(out, key=lambda j: -(j["ended"] or 0))


# What a research agent's message lists of the ended jobs (prompts.role_message). A list of "the last 24 hours" loses
# its oldest line every few minutes, and a line that leaves the front of a message changes every byte behind it, so
# nothing of it is ever read from the prompt cache (14 Nov: the list was two thirds of a role's opening message, written
# fresh every turn). The list starts at a time that moves in steps of ROLE_JOBS_STEP_SECS: between two steps no line
# leaves, the list only grows at its end, and it covers 24 to 30 hours.
ROLE_JOBS_STEP_SECS = 6 * 3600
ROLE_JOBS_HOURS = 24 + ROLE_JOBS_STEP_SECS / 3600  # what `recent_jobs` is asked for a role's turn: the longest list


def role_jobs_since(now: float) -> float:
    """Where a role's list of ended jobs starts at `now`: the last step of ROLE_JOBS_STEP_SECS (00:00, 06:00, 12:00,
    18:00 UTC) that lies a day or more back. A job is listed for 24 to 30 hours after it ended."""
    return float((now - 86400) // ROLE_JOBS_STEP_SECS * ROLE_JOBS_STEP_SECS)


def running_jobs(jobs_dir: Path) -> list[dict]:
    out = []
    for m in _read_metas(jobs_dir, math.inf):  # a job that runs has no exit file: only those records are opened
        jid = m.get("id")
        if m.get("running") and jid and not (jobs_dir / f"{jid}.exit").exists() and _pid_alive(m.get("pid")):
            out.append({"id": jid, "argv": m.get("argv", []), "started": m.get("started")})
    return out


def systemd_services(units: tuple[str, ...] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for u in units or (WATCHED_UNITS + ONESHOT_UNITS):
        try:
            r = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True, timeout=10)
            out[u] = (r.stdout or r.stderr).strip() or "unknown"
        except Exception as exc:  # noqa: BLE001 - never let a status probe kill the round
            out[u] = f"error: {type(exc).__name__}"
    return out


async def transaction_counts(svc, wallet: str, now: float, window: int = 3600) -> tuple[int | None, int | None]:
    """(failed, landed) transactions of the wallet inside `window` seconds; (None, None) when the RPC does not answer."""
    try:
        sigs = await svc.rpc.call("getSignaturesForAddress", [wallet, {"limit": 50}])
    except Exception:  # noqa: BLE001
        return None, None
    recent = [s for s in sigs or [] if (s.get("blockTime") or 0) >= now - window]
    failed = sum(1 for s in recent if s.get("err") is not None)
    return failed, len(recent) - failed


async def failed_transactions(svc, wallet: str, now: float, window: int = 3600) -> int | None:
    """How many of the wallet's recent transactions failed inside `window` seconds; None when the RPC does not answer."""
    return (await transaction_counts(svc, wallet, now, window))[0]


async def rpc_balances(svc, wallet: str, ignore: tuple[str, ...] = ()) -> tuple[float | None, float | None, list[dict]]:
    """(native SOL, wrapped SOL, open positions) from the RPC; positions by the one shared rule (positions.py), the
    owner's ignored coins (`ignore`: [operator] ignore_mints) left out as every other reader of the wallet does."""
    from ..positions import filter_positions
    from ..solana.programs import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

    native = (await svc.rpc.call("getBalance", [wallet]))["value"] / 1e9
    wsol, accounts = 0.0, []
    for prog in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
        r = await svc.rpc.call("getTokenAccountsByOwner", [wallet, {"programId": prog}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        for a in r["value"]:
            info = a["account"]["data"]["parsed"]["info"]
            ta = info["tokenAmount"]
            amt = float(ta.get("uiAmount") or 0)
            if info["mint"] == WSOL_MINT:
                wsol += amt
                continue
            decimals = int(ta.get("decimals") if ta.get("decimals") is not None else 6)
            raw = int(ta["amount"]) if ta.get("amount") is not None else int(round(amt * 10**decimals))
            accounts.append(
                {"mint": info["mint"], "amount": amt, "raw": raw, "decimals": decimals, "account": a.get("pubkey")}
            )
    held = filter_positions(accounts, svc.store.last_price, ignore=tuple(ignore or ()))
    return native, wsol, held


def trade_cash(is_buy, quote, fee, priority, tip) -> float:
    """The SOL one trade moved for the wallet, fees in: a buy pays its quote, the venue's fee, its bid (priority and
    tip) and the signature; a sale brings its quote less them (as the runner's own result counts them)."""
    fees = int(fee or 0) + int(priority or 0) + int(tip or 0) + 5000
    return (-(int(quote) + fees) if is_buy else int(quote) - fees) / 1e9


def _coin_whose(store, wallet: str, sale: dict, marks: dict[str, frozenset],
                held_before: dict[str, dict]) -> str | None:  # fmt: skip
    """Whose the coin a sale sells is: as the round before held it (its `whose`), else by the wallet's newest buy of it
    before the sale, on the tape (its fees) or in the measure stage's fills (measure/ours.bot_buy); None when nothing
    tells."""
    from ..measure.ours import _paid, bot_buy

    mint = str(sale.get("mint") or "")
    h = held_before.get(mint) or {}
    if h.get("whose") in ("ours", "owner"):
        return h["whose"]
    if "whose" not in h and h.get("ours") is False:  # marked before `whose`: the tape had said the owner's
        return "owner"
    try:
        rows = store._q("SELECT priority, tip FROM trades WHERE user=? AND mint=? AND is_buy=1 AND (slot<? OR (slot=? "
                        "AND pos<?)) ORDER BY slot DESC, pos DESC LIMIT 1",
                        (wallet, mint, sale.get("slot", 0), sale.get("slot", 0), sale.get("pos", 0)))  # fmt: skip
        if rows:
            return "owner" if _paid(rows[0][0], rows[0][1], marks["buy"]) is False else "ours"
        rows = store._q("SELECT sniper_id, priority_lamports, tip_lamports, wallet_copied FROM fills WHERE wallet=? "
                        "AND mint=? AND side='buy' AND ts<=? ORDER BY ts DESC LIMIT 1",
                        (wallet, mint, int(sale.get("ts") or 0)))  # fmt: skip
    except Exception:  # noqa: BLE001 - not readable now: the sale's own fees tell
        return None
    if rows:
        row = dict(zip(("sniper_id", "priority_lamports", "tip_lamports", "wallet_copied"), rows[0], strict=True))
        return "ours" if bot_buy(row, marks) else "owner"
    return None


def mark_ours(store, fills: list[dict], prev: Snapshot | None = None, wallet: str = "") -> list[dict]:
    """Each of the wallet's trades with `ours`: whether the bot made it. A buy by its fees: those our snipers pay
    (measure/ours.py); unknown fees, or no sniper on record, count as the bot's. A sale goes with the coin it sells
    (`_coin_whose`: as the round before held it, else by the wallet's buy of it) and only when nothing tells by its own
    fees (a sniper's, or the run's own sweep): the owner sells the bot's stuck coin by hand as the watch tells them to,
    and /sell pays the sweep's fees on the owner's own coin (audit 22 Nov, round#2). The owner trades by hand in this
    wallet too. `slot` and `pos` (where a sale is on the tape) are read here and not kept."""
    from ..measure.ours import _paid, fee_marks

    marks = fee_marks(store)
    held_before = {str(h["mint"]): h for h in (prev.held if prev is not None else None) or [] if h.get("mint")}
    out = []
    for f in fills:
        side = "buy" if f.get("side") == "buy" else "sell"
        paid = _paid(f.get("priority"), f.get("tip"), marks[side]) if marks[side] else None
        whose = _coin_whose(store, wallet, f, marks, held_before) if side == "sell" and marks["buy"] else None
        ours = whose == "ours" if whose is not None else paid is not False
        out.append({k: v for k, v in f.items() if k not in ("priority", "tip", "slot", "pos")} | {"ours": ours})
    return out


ROUND_SECS = 900  # the operator's rounds run every quarter of an hour (snipetech-operator.timer)
WHOSE_LOOKBACK_SECS = 3 * 86400  # with no earlier read of the wallet, a coin's buy is looked for this far back


def last_read(prev: Snapshot | None) -> float | None:
    """When the wallet's coins were last read before this round: the previous snapshot's `held_ts`, or, for one written
    before that field existed, its own time when it read the balances; None when no round read them."""
    if prev is None:
        return None
    if getattr(prev, "held_ts", None) is not None:
        return float(prev.held_ts)
    return float(prev.ts) if prev.native_sol is not None else None


def fills_whose(store, wallet: str, mint: str, since_ts: float, marks: dict[str, frozenset]) -> str | None:
    """Whose the wallet's holding of `mint` is by the measure stage's fills, which it reads off the chain every five
    minutes and so also holds a buy the tape never does (a PumpSwap copy past the tape's 30 minutes): the wallet's
    newest buy or sale of the coin there since `since_ts`. A buy says "ours" or "owner" by measure's own rule
    (measure/ours.bot_buy); a sale, or nothing, says nothing (None). With no marks every coin is ours."""
    from ..measure.ours import bot_buy

    if not marks["buy"]:
        return "ours"
    try:
        rows = store._q("SELECT side, sniper_id, priority_lamports, tip_lamports, wallet_copied FROM fills WHERE wallet=? "
                        "AND mint=? AND ts>=? AND side IN ('buy', 'sell') ORDER BY ts DESC, slot DESC LIMIT 1",
                        (wallet, mint, int(since_ts)))  # fmt: skip
    except Exception:  # noqa: BLE001 - not readable now: the next round asks again
        return None
    if not rows or rows[0][0] != "buy":
        return None
    _side, sniper, priority, tip, copied = rows[0]
    row = {"sniper_id": sniper, "priority_lamports": priority, "tip_lamports": tip, "wallet_copied": copied}
    return "ours" if bot_buy(row, marks) else "owner"


def mark_held(store, wallet: str, held: list[dict], prev: Snapshot | None = None,
              now: float | None = None) -> list[dict]:  # fmt: skip
    """Each held coin with `whose` it is and `ours` (whose == "ours"), as the runner tells them apart
    (measure/ours.RunCoins): the wallet's newest buy of it since the holding began says "ours" (a sniper's fees, or fees
    nobody recorded) or "owner" (other fees); the tape's buy (measure/ours.tape_whose), else the measure stage's fills
    (`fills_whose`). With neither, nothing tells (None: an airdrop, a coin sent in, a buy not recorded yet), and such a
    coin is not the bot's: 'nothing told' counted as the bot's paged the owner's coins as its gains (audit 22 Nov,
    round#1). An answer holds while the coin stays held (the owner's partial sale left no buy after it, and the rest of
    the coin read as the bot's); a coin nothing told yet is asked again each round, over the same window: from the read
    of the wallet before the coin was first seen (`whose_since`, WHOSE_MARGIN_SECS earlier: a node shows a coin late),
    not from when it was first seen (a hand buy 6 minutes before a round fell outside). The gate wakes the round when a
    coin of the bot's moves, never for the owner's own coin (22 Nov review)."""
    from ..measure.ours import WHOSE_MARGIN_SECS, fee_marks, tape_whose

    marks = fee_marks(store)
    before = {str(h["mint"]): h for h in (prev.held if prev is not None else None) or [] if h.get("mint")}
    last = last_read(prev)
    new_since = (last if last is not None else float(now or 0) - WHOSE_LOOKBACK_SECS) - WHOSE_MARGIN_SECS
    out = []
    for h in held:
        mint = str(h.get("mint") or "")
        old = before.get(mint)
        if old is not None and old.get("whose") in ("ours", "owner"):
            whose, since = old["whose"], old.get("whose_since")
        elif old is not None and "whose" not in old and old.get("ours") is False:
            whose, since = "owner", None  # a snapshot from before `whose`: the tape had said the owner's
        else:
            if old is not None and old.get("whose_since") is not None:
                since = float(old["whose_since"])
            elif old is not None:  # a snapshot from before `whose`: the read before it was first seen, a round earlier
                since = float(h.get("since") or 0) - ROUND_SECS - WHOSE_MARGIN_SECS
            else:
                since = new_since
            whose = (tape_whose(store, wallet, mint, since, marks) or fills_whose(store, wallet, mint, since, marks)
                     if mint else None)  # fmt: skip
        out.append(h | {"whose": whose, "whose_since": since, "ours": whose == "ours"})
    return out


def carry_since(held: list[dict], prev: Snapshot | None, now: float) -> list[dict]:
    """Each held coin keeps the time it was first seen (from the previous snapshot when it was held then too), so
    the gate can tell a stuck position from one the book bought minutes ago."""
    before: dict[str, float] = {}
    for h in (prev.held if prev is not None else None) or []:
        if h.get("mint"):
            before[str(h["mint"])] = float(h.get("since", prev.ts))
    return [h | {"since": before.get(str(h.get("mint")), now)} for h in held]


async def take_snapshot(svc, paths: OperatorPaths, cfg: Config, prev: Snapshot | None, now: float, *,
                        services: Callable[[], dict[str, str]] = systemd_services,
                        balances: Callable[[str], Any] | None = None) -> Snapshot:  # fmt: skip
    wallet = cfg.operator.wallet
    snap = Snapshot(ts=now, wallet=wallet, book_fill_sol=book_fill_sol(cfg))
    since = prev.ts if prev else now - 3600
    already = {j["id"] for j in prev.jobs_ended} if prev else set()

    try:
        snap.services = services()
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"services: {exc}")
    try:
        from ..book.switch import book_live

        snap.book_live = book_live(cfg, paths)
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"book switch: {exc}")
    try:
        snap.jobs_running = running_jobs(paths.jobs)
        snap.jobs_ended = ended_since(paths.jobs, since, already, now)
        snap.jobs_recent = recent_jobs(paths.jobs, now)
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"jobs: {exc}")
    try:
        row = svc.store._q("select max(ts) from trades")
        snap.tape_last_ts = int(row[0][0]) if row and row[0][0] is not None else None
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"tape: {exc}")
    if wallet:
        try:
            # the owner's ignored coins are no holding here either (audit 22 Nov, round#5: the watch offers that list for
            # a stray coin, and the round kept counting it)
            ignore = tuple(str(m) for m in cfg.operator.ignore_mints or ())
            res = balances(wallet) if balances else rpc_balances(svc, wallet, ignore=ignore)
            if hasattr(res, "__await__"):
                res = await res
            snap.native_sol, snap.wsol, held = res
            snap.held = mark_held(getattr(svc, "store", None), wallet, carry_since(list(held or []), prev, now), prev,
                                  now)  # fmt: skip
            snap.held_ts = now
            total = snap.total_equity
            snap.equity_ref = total if total is not None else getattr(prev, "equity_ref", None)
            clear_refusal(getattr(svc, "store", None), RPC_REFUSED_KEY, now)
        except Exception as exc:  # noqa: BLE001
            if refusal_status(exc) is not None:  # Helius turned the key away: an alert once it lasts (resources)
                note_refusal(getattr(svc, "store", None), RPC_REFUSED_KEY, exc, now)
            snap.errors.append(scrub(f"balances: {exc}"))
            snap.equity_ref = (
                getattr(prev, "equity_ref", None) if prev is not None else None
            )  # keep the last valued equity
            # and the coins as last read (`stale`), with their answers and that read's time: a snapshot with none made
            # the next round date every coin "first seen now", and the owner's coin read as the bot's (round#1)
            snap.held = [h | {"stale": True} for h in (prev.held if prev is not None else None) or []]
            snap.held_ts = last_read(prev)
        if balances is None:  # the real RPC path also counts failed transactions; fakes leave it None
            snap.failed_tx_1h, snap.ok_tx_1h = await transaction_counts(svc, wallet, now)
        try:
            rows = svc.store._q("select ts, mint, is_buy, quote, priority, tip, fee, slot, pos from trades where user=? "
                                "and ts>? order by ts", (wallet, since))  # fmt: skip
            snap.fills = mark_ours(svc.store, [
                {"ts": int(ts), "mint": m, "side": "buy" if b else "sell", "sol": round(q / 1e9, 4), "priority": pr,
                 "tip": tp, "slot": sl, "pos": po} for ts, m, b, q, pr, tp, _fe, sl, po in rows], prev, wallet)  # fmt: skip
            snap.bot_cash = round(sum(trade_cash(b, q, fe, pr, tp) for (_ts, _m, b, q, pr, tp, fe, _sl, _po), f
                                      in zip(rows, snap.fills, strict=True) if f.get("ours", True)), 9)  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            snap.errors.append(f"fills: {exc}")
            snap.fills_unread, snap.bot_cash = True, None
        # the gate measures the move from equity_ref, which a round with an unpriced coin carries from an older round:
        # the bot's trades since then are carried with it, or a loss read later with no trade in that round is missed
        ours_now = snap.fills_unread or any(f.get("ours", True) for f in snap.fills)
        snap.bot_since_ref = snap.total_equity is None and (bool(getattr(prev, "bot_since_ref", False)) or ours_now)
        # and the bot's own result since then (the gate's alert, review of 22 Nov): its trades' SOL and its coins' worth,
        # whose baseline moves whenever the bot's own coins are valued, whatever else the wallet holds (round#0)
        if snap.bot_valued:  # this round values the bot's coins: the baseline is this round
            snap.ref_ours_sol = round(sum(float(h["value_sol"]) for h in snap.held if h.get("ours", True)), 9)
            snap.bot_cash_since_ref = 0.0
        else:
            snap.ref_ours_sol = getattr(prev, "ref_ours_sol", None)
            before = getattr(prev, "bot_cash_since_ref", None)
            snap.bot_cash_since_ref = (None if before is None or snap.bot_cash is None
                                       else round(float(before) + snap.bot_cash, 9))  # fmt: skip
    try:
        st = await svc.oktech_status()
        snap.snipers = [
            {"id": t.get("id"), "name": t.get("name"), "status": t.get("status")} for t in st.get("snipers", [])
        ]
        clear_refusal(getattr(svc, "store", None), OKTECH_REFUSED_KEY, now)
    except Exception as exc:  # noqa: BLE001
        if refusal_status(exc) is not None:  # OKTech turned the key away (a lapsed plan): an alert once it lasts
            note_refusal(getattr(svc, "store", None), OKTECH_REFUSED_KEY, exc, now)
        snap.errors.append(scrub(f"oktech: {exc}"))
        snap.snipers = None
    try:
        from ..analysis import paper

        rep = paper.report(svc.store, cfg, now=now)
        snap.paper = {"scored_until": rep.get("scored_until"),
                      "strategies": [{k: s.get(k) for k in ("id", "label", "n", "mean", "ev_lower", "total")}
                                     # a record no sniper would trade says so, in a few words (paper.row_note)
                                     | ({"research_only": True, "exit_note": str(s.get("exit_note") or "")[:120]}
                                        if s.get("research_only") else {})
                                     for s in rep.get("strategies", [])]}  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"paper: {exc}")
    try:
        snap.spent_today = Ledger(paths.spend, cfg.operator.prices).spent_today(now)
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"spend: {exc}")
    try:
        snap.verdicts = svc.store.verdict_counts()
    except Exception as exc:  # noqa: BLE001
        snap.errors.append(f"verdicts: {exc}")
    return snap


def load_prev(paths: OperatorPaths) -> Snapshot | None:
    for p in (paths.snapshot_prev,):
        if p.exists():
            try:
                return Snapshot.from_json(p.read_text())
            except (OSError, json.JSONDecodeError, TypeError):
                return None
    return None


def load_current(paths: OperatorPaths) -> Snapshot | None:
    if paths.snapshot.exists():
        try:
            return Snapshot.from_json(paths.snapshot.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            return None
    return None


def save(snap: Snapshot, paths: OperatorPaths) -> None:
    paths.ensure()
    if paths.snapshot.exists():
        paths.snapshot.replace(paths.snapshot_prev)
    paths.snapshot.write_text(snap.to_json())
