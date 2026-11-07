"""The owner's own capped live test, started and ended from Telegram: no SSH, no command line.

`/test` shows the hunt strategies the forward paper test backs where a live hunt fill lands (that is the evidence,
CLAUDE.md rule 2; the lab alone never is) and the plan for the best one: the wallet, the size of a buy, how many buys,
for how long, and the floor. "/test <id> go" is the owner's explicit yes for exactly that plan (rule 1: wallet, size,
duration). It writes a request to operator/TEST.json and nothing else. The next operator round checks every refusal
again, creates the strategy's own hunt sniper on OKTech (idle, one buy a start) and starts one `oktech cycle` job that
keeps it armed (a hunt sniper fires once per start, 5 Nov) until 10 fills, 120 minutes, the floor or the owner's /stop.

Only the round starts it: its unit leaves the jobs it starts running when the round ends and through deploys
(KillMode=process), while the one-minute commands pass kills its children when it exits and the ops service kills its
jobs on every deploy. One test at a time: a test requested, starting or running refuses another, and so does an OKTech
cycler or guard already running. A crash mid-start never leaves a second sniper: "starting" is written before the
create call, and nothing is created or started from "starting" again. /stop cancels a test that has not started and
ends a running one (the cycler stops re-arming; a sniper holding a coin keeps running to sell it). No agent tool
reaches this module: their job allowlist excludes `oktech`, and none of their tools writes TEST.json."""

from __future__ import annotations

import asyncio
import fcntl
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ..config import Config
from ..ops import redact
from .paths import OperatorPaths
from .snapshot import running_jobs
from .tools import NoteSink

MAX_BUY_SOL = 0.5  # the most one buy of a test may be, whatever size the strategy was frozen at
FILLS = 10  # buys a test makes at most: the cycler stops re-arming at the 10th buy of the wallet on the tape
MINUTES = 120  # how long the cycler keeps the sniper armed
MIN_FORWARD_N = 100  # forward paper trades at the real landing a strategy needs before it goes live
REQUEST_TTL_SECS = 30 * 60  # a yes that no round acted on within two rounds is stale: the owner sends it again
# Fees beyond the buys themselves: about 0.009 SOL a live round trip, failed duplicate sells included (6 Nov), and about
# 0.001 SOL a failed buy (OKTech sends those anyway and each spends the shot, 5 Nov). Ten round trips plus one failed
# send a minute for 120 minutes is about 0.2 SOL, so 0.5 leaves room.
FEE_MARGIN_SOL = 0.5
MIN_FLOOR_SOL = 1.0  # the floor is never under this, however small the wallet
SNAPSHOT_MAX_AGE_SECS = 40 * 60  # the balance comes from the last round's snapshot: older than two rounds is unknown
JOB_MARGIN_SECS = 30 * 60  # the cycler's own time limit past its minutes (its last balance reads, a slow RPC)
# how long a check waits for the wallet's coins: the one-minute pass also carries /stop, and one hanging holdings read
# retried for about 250 s (operator/run.py WATCH_TIMEOUT_SECS); past it the owner asks again, the round tries again
WALLET_READ_SECS = 20
SHOWN = 3  # strategies /test lists
STATES = ("requested", "starting", "running", "done", "expired")
ACTIVE = ("requested", "starting", "running")
DEFAULT_OKTECH_WALLET = "SNIPER"  # what `oktech strategy` and `oktech push` buy from when [oktech] wallet is empty
DRIVERS = ("cycle", "guard")  # OKTech jobs that drive a live test: while one runs, no second test starts
NONE_EXPECTED = {"snipers_running": 0, "held": 0}
_ID = re.compile(r"[0-9a-f]{6,16}")  # a frozen strategy's id (paper.strategy_id: ten hex digits)

Log = Callable[[str], None]


# -- the state file ------------------------------------------------------------------------------------------------


def state_path(paths: OperatorPaths) -> Path:
    return paths.root / "TEST.json"


def history_path(paths: OperatorPaths) -> Path:
    """One JSON line per test that ended (done or expired): TEST.json holds only the current one."""
    return paths.root / "tests.jsonl"


def load(paths: OperatorPaths) -> dict | None:
    """The current test, or None (no file, or one that is not a test: never guessed at)."""
    try:
        d = json.loads(state_path(paths).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("state") in STATES and d.get("id") else None


def save(paths: OperatorPaths, test: dict) -> None:
    """Atomic, like book/switch.py: a reader never sees half a file (a temporary file, then a rename over it)."""
    paths.ensure()
    p = state_path(paths)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(test, indent=1, default=str))
    os.replace(tmp, p)


@contextmanager
def _locked(paths: OperatorPaths) -> Iterator[None]:
    """The one-minute pass (a request, /stop) and the round (start, end) both write TEST.json: every read-and-write is
    done under this lock, and never across a call to OKTech or the RPC."""
    paths.ensure()
    fd = os.open(paths.root / "TEST.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing it releases the lock


def _swap(paths: OperatorPaths, ok: Callable[[dict | None], bool], new: dict) -> tuple[bool, dict | None]:
    """Write `new` only if the test on file still passes `ok`; (written, the test that was on file)."""
    with _locked(paths):
        cur = load(paths)
        if not ok(cur):
            return False, cur
        save(paths, new)
        return True, cur


def _put(paths: OperatorPaths, test: dict) -> dict:
    with _locked(paths):
        save(paths, test)
    return test


def _same(t: dict, state: str) -> Callable[[dict | None], bool]:
    """The test `t` (the same request) is still on file, in `state`."""
    return lambda cur: (
        bool(cur) and (cur["id"], cur.get("requested_at"), cur["state"]) == (t["id"], t.get("requested_at"), state)
    )


def _expired(t: dict | None, now: float) -> bool:
    return bool(t) and t["state"] == "requested" and now - float(t.get("requested_at") or 0) > REQUEST_TTL_SECS


def _active(t: dict | None, now: float) -> bool:
    return bool(t) and t["state"] in ACTIVE and not _expired(t, now)


def _history(paths: OperatorPaths, test: dict) -> None:
    paths.ensure()
    with history_path(paths).open("a") as f:
        f.write(json.dumps(test, default=str) + "\n")


def _expect(paths: OperatorPaths, values: dict) -> None:
    """operator/expected.json, what the operator's gate treats as quiet (gate.expected_state): while a test runs, its
    one sniper running and its one coin held are the test, not a deviation."""
    paths.ensure()
    p = paths.expected
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(values))
    os.replace(tmp, p)


# -- which strategies, what plan -----------------------------------------------------------------------------------


def _why_not(row: dict | None, frozen: dict | None) -> str | None:
    """Why a strategy may not go live now, in plain words; None when it may."""
    if row is None or frozen is None:
        return "is not a frozen paper strategy"
    if row.get("trigger_kind") != "hunt":
        return "is not a hunt strategy (OKTech runs only its hunt launch gate exactly as the lab scored it)"
    if not row.get("live_landing"):
        return "was not scored where a live hunt fill lands"
    n = int(row.get("n") or 0)
    if n < MIN_FORWARD_N:
        return f"has {n} forward trades of the {MIN_FORWARD_N} needed"
    lower = row.get("ev_lower")
    if lower is None or float(lower) <= 0:
        return f"has a lower bound of {float(lower or 0):+.4f} SOL a trade, not above zero"
    from ..oktech import strategy_sniper

    try:  # the sniper it would get: OKTech ignores a hunt threshold that is not a whole number (5 Nov 22:11)
        strategy_sniper(frozen | {"buy_sol": row.get("buy_sol")}, 1, "hunt")
    except (ValueError, KeyError, TypeError) as exc:
        return f"cannot be sent to OKTech as it was scored ({exc})"
    return None


def _survey(store, cfg: Config, now: float) -> tuple[list[dict], dict[str, str], list[dict]]:
    """(the eligible strategies, best lower bound first; why each other one is not; every paper report row)."""
    from ..analysis import paper

    rows = paper.report(store, cfg, now=now)["strategies"]
    frozen = {s["id"]: s for s in store.coverage("paper_strategies").get("strategies", []) if isinstance(s, dict)}
    ok, why = [], {}
    for r in rows:
        w = _why_not(r, frozen.get(r["id"]))
        if w is None:
            ok.append(r)
        else:
            why[r["id"]] = w
    return sorted(ok, key=lambda r: -float(r["ev_lower"])), why, rows


def eligible(store, cfg: Config, now: float) -> list[dict]:
    """The frozen paper strategies a live test may run, best lower bound first (paper.report rows): hunt strategies (the
    one kind OKTech runs exactly as the lab scored it), scored at the live landing, with MIN_FORWARD_N forward trades or
    more and a lower bound above zero. The forward test at the real landing is the evidence, never the lab."""
    return _survey(store, cfg, now)[0]


def plan(row: dict, equity_sol: float, wallet: str) -> dict:
    """The live test of one eligible strategy: its own size a buy (refused above MAX_BUY_SOL), FILLS buys at most, one
    coin at a time, MINUTES at most, and a floor the test's own buys cannot reach: the equity less every buy and
    FEE_MARGIN_SOL of fees, never under MIN_FLOOR_SOL, rounded down to the cent. Raises ValueError in plain words."""
    buy = float(row.get("buy_sol") or 0)
    if not buy > 0:
        raise ValueError("it has no size a buy")
    if buy > MAX_BUY_SOL + 1e-9:
        raise ValueError(f"it buys {buy:g} SOL at a time, above the {MAX_BUY_SOL:g} SOL a live test may buy")
    left = float(equity_sol) - FILLS * buy - FEE_MARGIN_SOL
    floor = round(math.floor(max(MIN_FLOOR_SOL, left) * 100 + 1e-6) / 100, 2)
    worst = (f"at most {FILLS} buys of {buy:g} SOL ({FILLS * buy:.2f} SOL in all), one coin at a time; it buys nothing "
             f"more once the wallet (SOL and coins) is below {floor:.2f} SOL")  # fmt: skip
    if left >= floor:
        worst += ", which losing every buy, fees included, does not reach"
    return {"id": row["id"], "label": str(row.get("label") or row["id"]), "buy_sol": buy, "fills": FILLS,
            "minutes": MINUTES, "floor": floor, "wallet": wallet, "equity_sol": round(float(equity_sol), 4),
            "worst": worst}  # fmt: skip


def equity_now(snap, now: float, wallet: str = "") -> float | None:
    """SOL plus wrapped SOL from the last round's snapshot (a test starts only from a wallet that holds no coin, so that
    is the whole wallet); None without a snapshot of this wallet from the last 40 minutes, or one that could not read
    the balances."""
    if (
        snap is None
        or now - float(snap.ts) > SNAPSHOT_MAX_AGE_SECS
        or (wallet and snap.wallet and snap.wallet != wallet)
    ):
        return None
    return snap.equity


def _need(p: dict) -> float:
    return round(float(p["floor"]) + int(p["fills"]) * float(p["buy_sol"]), 6)


def cycle_argv(test: dict, cfg: Config) -> list[str]:
    """The one job a test runs: the cycler with the test's own caps, its report as JSON at the end of its log, and the
    coins the owner told the bot to ignore left out of "held" (the book and the watch leave them out too)."""
    argv = ["oktech", "cycle", "--id", str(test["oktech_id"]), "--address", str(test["wallet"]), "--floor",
            str(float(test["floor"])), "--max-minutes", str(int(test["minutes"])), "--max-fills", str(int(test["fills"])),
            "--json"]  # fmt: skip
    for m in cfg.operator.ignore_mints or ():
        argv += ["--ignore-mint", str(m)]
    return argv


def _drivers(paths: OperatorPaths) -> list[dict]:
    """OKTech cyclers and guards running now (a test's, or one started by hand): one live test at a time."""
    return [j for j in running_jobs(paths.jobs) if list(j.get("argv") or [])[:1] == ["oktech"]
            and list(j.get("argv") or [])[1:2] and j["argv"][1] in DRIVERS]  # fmt: skip


async def _held(svc, cfg: Config, wallet: str) -> list[dict]:
    """The wallet's coins by the book's rule (the owner's ignored coins left out), waited for WALLET_READ_SECS at most:
    raises when they cannot be read in that time (TimeoutError) or at all."""
    ignore = list(cfg.operator.ignore_mints or ())
    return list(await asyncio.wait_for(svc.open_positions(wallet, ignore=ignore), WALLET_READ_SECS) or [])


def _fills_since(store, wallet: str, since: float) -> int | None:
    """The wallet's buys on the tape since `since`: what the cycler counts as fills."""
    try:
        return sum(1 for t in store.trades_for_user(wallet, int(since)) if t.is_buy)
    except Exception:  # noqa: BLE001 - a status line, never a reason to fail
        return None


# -- the words the owner reads ---------------------------------------------------------------------------------------


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.gmtime(ts))


def _coins(held: list[dict]) -> str:
    n = len(held)
    names = ", ".join(f"{str(h.get('mint'))[:6]}…" for h in held[:5])
    return f"{n} coin{'' if n == 1 else 's'} ({names})"


def _plan_words(p: dict, cfg: Config, paths: OperatorPaths) -> list[str]:
    from ..book.switch import book_live

    lines = [
        (f"- what: OKTech's hunt sniper with this strategy's own settings, on wallet {p['wallet'][:6]}… (OKTech wallet "
         f"{cfg.oktech.wallet or DEFAULT_OKTECH_WALLET}), {p['buy_sol']:g} SOL a buy"),
        (f"- caps: at most {p['fills']} buys, one coin at a time, for at most {p['minutes']} minutes; it stops buying "
         f"once the wallet (SOL and coins) is below {p['floor']:.2f} SOL (it holds {p['equity_sol']:.2f} SOL now)"),
        f"- worst case: {p['worst']}",
        ("- /stop ends it at any time; a sniper holding a coin keeps running until the coin is sold, and the unsold-coin "
         "watch and /sell are there for a coin OKTech does not sell (5 Nov: 2 of 11 hunt fills got no sell)"),
    ]  # fmt: skip
    if book_live(cfg, paths):
        lines.append("- the book is on and trades from the same wallet: its buys count toward the cap, and while it "
                     "holds a coin the test waits")  # fmt: skip
    return lines


def _caps(t: dict) -> str:
    return (f"{t['buy_sol']:g} SOL a buy, at most {t['fills']} buys, {t['minutes']} minutes, floor "
            f"{float(t['floor']):.2f} SOL")  # fmt: skip


def _status(svc, t: dict, now: float) -> str:
    """/test while a test is requested, starting or running."""
    head = f"{t.get('label') or t['id']} ({t['id']})"
    if t["state"] == "requested":
        return (f"Live test requested at {_hm(t['requested_at'])} UTC: {head}, {_caps(t)}. It starts at the next "
                f"operator round (within 15 minutes) and expires if no round has started it by "
                f"{_hm(float(t['requested_at']) + REQUEST_TTL_SECS)} UTC. /stop cancels it.")  # fmt: skip
    if t["state"] == "starting":
        return f"Live test starting now: {head}; the round is creating its sniper. {_caps(t)}."
    store = getattr(svc, "store", None)
    fills = _fills_since(store, t["wallet"], t["started_at"]) if store is not None else None
    left = max(0.0, float(t["minutes"]) - (now - float(t["started_at"])) / 60)
    return (f"Live test running since {_hm(t['started_at'])} UTC: {head}, OKTech sniper {t.get('oktech_id')}. Fills so "
            f"far: {'unknown' if fills is None else fills} of {t['fills']}. About {left:.0f} minutes left. It stops "
            f"buying once the wallet is below {float(t['floor']):.2f} SOL. /stop ends it (a sniper holding a coin keeps "
            "running until the coin is sold).")  # fmt: skip


def _last_words(t: dict) -> str:
    """One line on the last test, for /test when none is in progress."""
    head = f"Last live test: {t.get('label') or t['id']} ({t['id']})"
    if t["state"] in ("requested", "expired"):
        return f"{head}: the request expired, no round started it within {REQUEST_TTL_SECS // 60} minutes."
    if not t.get("started"):
        return f"{head}: {t.get('error') or 'not started'}."
    rep = t.get("report") or {}
    return f"{head} ended {_hm(t.get('ended_at') or 0)} UTC: {_result(rep)}."


def _result(rep: dict) -> str:
    fills = rep.get("fills")
    out = f"{'?' if fills is None else fills} fills"
    if rep.get("start_sol") is not None and rep.get("end_sol") is not None:
        a, b = float(rep["start_sol"]), float(rep["end_sol"])
        out += f"; wallet {a:.2f} -> {b:.2f} SOL ({b - a:+.2f})"
    return out + f"; why it ended: {rep.get('reason') or 'not known'}"


def _none_words(rows: list[dict], why: dict[str, str]) -> str:
    def closeness(r: dict) -> tuple[float, int]:
        lower = r.get("ev_lower")
        return (-(float(lower) if lower is not None else -1e9), -int(r.get("n") or 0))

    near = sorted((r for r in rows if r.get("trigger_kind") == "hunt" and r.get("live_landing")), key=closeness)[:SHOWN]
    lines = [(f"No hunt strategy qualifies for a live test yet: one needs at least {MIN_FORWARD_N} forward paper "
              "trades where a live hunt fill lands, and a lower bound above zero.")]  # fmt: skip
    if near:
        lines.append("Closest now:")
        for r in near:
            lines.append(f"{r['id']} {r['label']}: {int(r.get('n') or 0):,} forward trades, mean "
                         f"{float(r.get('mean') or 0):+.4f}, lower bound {float(r.get('ev_lower') or 0):+.4f} "
                         f"({why.get(r['id'], '')})")  # fmt: skip
    else:
        lines.append("None is frozen in the paper test yet.")
    return "\n".join(lines)


# -- /test in Telegram ---------------------------------------------------------------------------------------------


async def command(args: list[str], *, svc, cfg: Config, paths: OperatorPaths, snap, notes: NoteSink, now: float,
                  date: int | None = None) -> str:  # fmt: skip
    """/test: the test in progress, or the strategies that qualify and the best one's plan, ending with the exact line
    to send. /test <id>: that strategy's plan. /test <id> go: the owner's yes for exactly that plan, a request the next
    round starts. Only the owner's chat gets here (commands.parse_updates); a message that waited is refused before."""
    if not args:
        return _overview(svc, cfg, paths, snap, now)
    sid = args[0].lower()
    if not _ID.fullmatch(sid):  # never repeated: a secret key pasted by mistake must not reach the chat or the log
        return ("Nothing started: that is not a strategy id (ten letters and digits, as /test shows them). Send /test "
                "to see them.")  # fmt: skip
    if len(args) == 1:
        return _one(sid, svc, cfg, paths, snap, now)
    if args[1:] != ["go"]:
        return f"Nothing started: to start it, send exactly /test {sid} go"
    return await _go(sid, svc=svc, cfg=cfg, paths=paths, snap=snap, notes=notes, now=now, date=date)


def _read(svc, cfg: Config, now: float) -> tuple[list[dict], dict[str, str], list[dict]] | str:
    """The survey of the paper test, or what to tell the owner when it cannot be read (nothing changes then)."""
    store = getattr(svc, "store", None)
    if store is None:
        return "I cannot read the paper test right now."
    try:
        return _survey(store, cfg, now)
    except Exception as exc:  # noqa: BLE001 - the owner hears it and asks again
        return f"I could not read the paper test ({type(exc).__name__}). Try again in a minute."


def _overview(svc, cfg: Config, paths: OperatorPaths, snap, now: float) -> str:
    t = load(paths)
    if _active(t, now):
        return _status(svc, t, now)
    lines = [_last_words(t)] if t else []
    got = _read(svc, cfg, now)
    if isinstance(got, str):
        return "\n".join([*lines, got])
    ok, why, rows = got
    if not ok:
        return "\n".join([*lines, _none_words(rows, why)])
    lines.append("Hunt strategies the forward paper test backs where a live hunt fill lands, best lower bound first:")
    for r in ok[:SHOWN]:
        lines.append(f"{r['id']} {r['label']}: {int(r['n']):,} forward trades, {float(r['mean']):+.4f} SOL a trade, "
                     f"lower bound {float(r['ev_lower']):+.4f}")  # fmt: skip
    return "\n".join([*lines, *_plan_lines(ok[0], cfg, paths, snap, now)])


def _one(sid: str, svc, cfg: Config, paths: OperatorPaths, snap, now: float) -> str:
    t = load(paths)
    if _active(t, now):
        return _status(svc, t, now)
    got = _read(svc, cfg, now)
    if isinstance(got, str):
        return got
    ok, why, _rows = got
    row = next((r for r in ok if r["id"] == sid), None)
    if row is None:
        return (f"{sid} cannot be tested live: it {why.get(sid, 'is not a frozen paper strategy')}. Send /test to see "
                "the ones that can.")  # fmt: skip
    return "\n".join(_plan_lines(row, cfg, paths, snap, now))


def _plan_lines(row: dict, cfg: Config, paths: OperatorPaths, snap, now: float) -> list[str]:
    """The plan for `row` in words, ending with the exact line that starts it (or why it cannot start now)."""
    wallet = cfg.operator.wallet
    if not wallet:
        return ["No live test is possible: the sniper wallet is not set ([operator] wallet in the config)."]
    eq = equity_now(snap, now, wallet)
    if eq is None:
        return [("I cannot work out its plan yet: the wallet's balance is not known (no round in the last 40 minutes "
                 "read it). Try again after the next round.")]  # fmt: skip
    try:
        p = plan(row, eq, wallet)
    except ValueError as exc:
        return [f"{row['id']} cannot be tested live: {exc}."]
    lines = [f"The plan for {p['id']} ({p['label']}):", *_plan_words(p, cfg, paths)]
    if eq + 1e-9 < _need(p):
        return [*lines, (f"Not possible now: the wallet holds {eq:.2f} SOL and this test needs at least "
                         f"{_need(p):.2f} SOL (the {p['floor']:.2f} SOL floor plus {p['fills']} buys of "
                         f"{p['buy_sol']:g}).")]  # fmt: skip
    return [*lines, "To start it, send this line; the next operator round (within 15 minutes) starts it:",
            f"/test {p['id']} go"]  # fmt: skip


async def _go(sid: str, *, svc, cfg: Config, paths: OperatorPaths, snap, notes: NoteSink, now: float,
              date: int | None) -> str:  # fmt: skip
    """The owner's yes. Every refusal changes nothing and says why; the RPC is asked last, and the request is written
    only if no other test was written meanwhile."""
    no = "Nothing started: "
    t = load(paths)
    if _active(t, now):
        return no + f"a live test is already {t['state']} ({t['id']}). Send /test to see it."
    if paths.stop_flag.exists():
        return no + f"your /stop is in force. Send /resume first, then /test {sid} go again."
    wallet = cfg.operator.wallet
    if not wallet:
        return no + "the sniper wallet is not set ([operator] wallet in the config)."
    got = _read(svc, cfg, now)
    if isinstance(got, str):
        return no + got
    ok, why, _rows = got
    row = next((r for r in ok if r["id"] == sid), None)
    if row is None:
        return no + f"{sid} {why.get(sid, 'is not a frozen paper strategy')}. Send /test to see the ones that qualify."
    eq = equity_now(snap, now, wallet)
    if eq is None:
        return no + ("the wallet's balance is not known right now (no round in the last 40 minutes read it). Try again "
                     "after the next round.")  # fmt: skip
    try:
        p = plan(row, eq, wallet)
    except ValueError as exc:
        return no + f"{sid} cannot be tested live: {exc}."
    if eq + 1e-9 < _need(p):
        return no + (f"the wallet holds {eq:.2f} SOL and this test needs at least {_need(p):.2f} SOL (the "
                     f"{p['floor']:.2f} SOL floor plus {p['fills']} buys of {p['buy_sol']:g}).")  # fmt: skip
    busy = _drivers(paths)
    if busy:
        return no + (f"an OKTech job is running ({busy[0]['id']}: {' '.join(busy[0]['argv'][:2])}): one live test at "
                     "a time.")  # fmt: skip
    try:
        held = await _held(svc, cfg, wallet)
    except Exception as exc:  # noqa: BLE001 - the owner hears it and sends it again
        return no + f"I could not read the wallet's coins ({type(exc).__name__}). Try again in a minute."
    if held:
        return no + (f"the wallet holds {_coins(held)}, and a test starts only from a wallet that holds none (a coin "
                     f"held stops the cycler from re-arming). Send /sell to see them, then /test {sid} go again.")  # fmt: skip
    req = p | {"state": "requested", "oktech_wallet": cfg.oktech.wallet or DEFAULT_OKTECH_WALLET,
               "forward": {k: row.get(k) for k in ("n", "mean", "ev_lower", "landing")},
               "requested_at": int(now), "by": "telegram", "message_date": date}  # fmt: skip
    written, before = _swap(paths, lambda cur: not _active(cur, now), req)
    if not written:
        return no + "another live test was requested a moment ago. Send /test to see it."
    if before and _expired(before, now):  # a yes no round ever acted on: kept in the record as expired
        _history(paths, before | {"state": "expired", "ended_at": int(now), "error": "expired: no round started it"})
    notes.write(f"owner requested a live test from Telegram (/test {sid} go): {p['label']}, {_caps(p)}, wallet "
                f"{wallet}; the next operator round starts it", "decision")  # fmt: skip
    head = (f"Live test requested: {p['label']} ({sid}). It starts at the next operator round, within 15 minutes; if "
            f"no round has started it by {_hm(now + REQUEST_TTL_SECS)} UTC it expires and nothing is bought.")  # fmt: skip
    return "\n".join([head, *_plan_words(p, cfg, paths)])


def on_stop(paths: OperatorPaths, notes: NoteSink, now: float) -> str | None:
    """The owner's /stop, after its flag is written: a test not started yet is cancelled at once (a /resume before the
    next round must not bring it back), and a line for the reply says what happens to one starting or running."""
    t = load(paths)
    if not _active(t, now):
        return None
    if t["state"] == "requested":
        done = t | {"state": "done", "started": False, "ended_at": int(now),
                    "error": "cancelled by the owner's /stop before it started"}  # fmt: skip
        written, cur = _swap(paths, _same(t, "requested"), done)
        if written:
            _history(paths, done)
            notes.write(f"owner's /stop cancelled the live test requested at {_hm(t['requested_at'])} UTC ({t['id']}); "
                        "it had not started", "decision")  # fmt: skip
            return f"The live test you asked for ({t['id']}) is cancelled; it had not started."
        t = cur
        if not _active(t, now):
            return None
    if t["state"] == "starting":
        return f"The live test ({t['id']}) was starting: with /stop set, its cycler does not start, or stops at once."
    return (f"The live test ({t['id']}) stops re-arming within seconds; a sniper holding a coin keeps running until "
            "the coin is sold.")  # fmt: skip


# -- the round starts it, watches it end -----------------------------------------------------------------------------


def _say(text: str, notes: NoteSink, notifier, log: Log | None) -> None:
    """A decision note, the owner's Telegram line, and the operator log, all in the same words."""
    notes.write(text, "decision")
    if notifier is not None:
        notifier.send(redact(text))
    if log is not None:
        log(text.splitlines()[0][:160])


def _end(paths: OperatorPaths, t: dict, now: float, notes: NoteSink, notifier, log: Log | None, *, state: str = "done",
         error: str | None = None, report: dict | None = None) -> dict:  # fmt: skip
    done = t | {"state": state, "ended_at": int(now), "started": bool(t.get("job_id"))}
    if error is not None:
        done["error"] = redact(error)
    if report is not None:
        done["report"] = report
    _put(paths, done)
    _history(paths, done)
    head = f"{t.get('label') or t['id']} ({t['id']})"
    if report is not None:
        mins = (now - float(t.get("started_at") or now)) / 60
        held = report.get("held") or []
        text = f"Live test ended: {head}, after {mins:.0f} minutes. {_result(report)}."
        if held:
            text += (f" Still held: {', '.join(held)}; its sniper keeps running to sell it, and /sell <coin> sells "
                     "it now.")  # fmt: skip
        if t.get("job_id"):
            text += f" The record: jobs/{t['job_id']}.log."
        _say(text, notes, notifier, log)
    elif state == "expired":
        _say(f"Live test request expired: {head}. No round started it within {REQUEST_TTL_SECS // 60} minutes, so "
             f"nothing was bought; send /test {t['id']} go again if you still want it.", notes, notifier, log)  # fmt: skip
    else:
        _say(f"Live test did not start: {head}: {done.get('error')}. Nothing was bought. Send /test to see the plan "
             "again.", notes, notifier, log)  # fmt: skip
    return done


async def _recheck(svc, cfg: Config, paths: OperatorPaths, t: dict, snap, now: float) -> tuple[str | None, bool]:
    """Why the requested test must not start now (None: it may), and whether that may pass by itself before the request
    expires (the balance or the coins could not be read). The request file is checked against the caps too: only
    /test go writes it, and nothing above them is ever started."""
    if paths.stop_flag.exists():
        return "your /stop is in force", False
    try:
        bad = (float(t["buy_sol"]) > MAX_BUY_SOL + 1e-9 or int(t["fills"]) > FILLS or int(t["minutes"]) > MINUTES
               or float(t["floor"]) < MIN_FLOOR_SOL or float(t["buy_sol"]) <= 0)  # fmt: skip
    except (KeyError, TypeError, ValueError):
        bad = True
    if bad:
        return "the request is not one /test go writes (its caps are missing or above the limits)", False
    if not cfg.operator.wallet or t.get("wallet") != cfg.operator.wallet:
        return "the sniper wallet in the config is not the one in the request", False
    try:
        ok, why, _rows = _survey(svc.store, cfg, now)
    except Exception as exc:  # noqa: BLE001 - the next round reads it again
        return f"the paper test could not be read ({type(exc).__name__})", True
    row = next((r for r in ok if r["id"] == t["id"]), None)
    if row is None:
        return f"{t['id']} {why.get(t['id'], 'is not a frozen paper strategy')} any more", False
    if abs(float(row.get("buy_sol") or 0) - float(t["buy_sol"])) > 1e-9:
        return "the strategy's size changed since the request", False
    busy = _drivers(paths)
    if busy:
        return f"an OKTech job is running ({busy[0]['id']}: {' '.join(busy[0]['argv'][:2])})", False
    eq = equity_now(snap, now, t["wallet"])
    if eq is None:
        return "the wallet's balance is not known this round", True
    if eq + 1e-9 < _need(t):
        return f"the wallet holds {eq:.2f} SOL, under the {_need(t):.2f} SOL this test needs", False
    try:
        held = await _held(svc, cfg, t["wallet"])
    except Exception as exc:  # noqa: BLE001 - the next round reads it again
        return f"the wallet's coins could not be read ({type(exc).__name__})", True
    if held:
        return f"the wallet holds {_coins(held)}", False
    return None, False


def _cycler_for(paths: OperatorPaths, oid: str | None) -> dict | None:
    """The job file of the cycler started for sniper `oid`, running or ended, if there is one."""
    if not oid or not paths.jobs.is_dir():
        return None
    for p in sorted(paths.jobs.glob("*.json")):
        try:
            meta = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        argv = [str(a) for a in (meta.get("argv") or [])] if isinstance(meta, dict) else []
        if argv[:2] == ["oktech", "cycle"] and str(oid) in argv and meta.get("id"):
            return meta
    return None


def _resolve_starting(paths: OperatorPaths, t: dict, now: float, notes: NoteSink, notifier, log: Log | None) -> dict:
    """A "starting" test seen by a later round: the round that wrote it stopped in the middle (rounds never overlap:
    operator/lock). Its cycler, if it got that far, is adopted (running or ended: the next round reads its report);
    otherwise the test ends. Nothing is created or started from here: that is what makes a crash mid-start unable to
    leave a second sniper."""
    oid = t.get("oktech_id")
    job = _cycler_for(paths, oid)
    if job is not None:
        running = _put(
            paths, t | {"state": "running", "job_id": job["id"], "started_at": int(job.get("started") or now)}
        )
        _expect(paths, {"snipers_running": 1, "held": 1})
        notes.write(f"live test {t['id']}: the round that started it stopped before writing it down; its cycler "
                    f"{job['id']} runs, so the test is adopted as running", "decision")  # fmt: skip
        if log is not None:
            log(f"adopted the running cycler {job['id']}")
        return running
    why = "the start was cut short (the round stopped in the middle of it), and a start is never tried twice"
    if oid:
        why += f"; its sniper {oid} was created idle on OKTech (it buys nothing)"
    return _end(paths, t, now, notes, notifier, log, error=f"not started: {why}")


async def start_requested(svc, cfg: Config, paths: OperatorPaths, jobs, now: float, *, snap=None,
                          notes: NoteSink | None = None, notifier=None, log: Log | None = None) -> dict | None:  # fmt: skip
    """The operator round's part (each round, before any Claude call). A requested test older than REQUEST_TTL_SECS
    expires. Otherwise every refusal of /test go is checked again; then "starting" is written, the strategy's own
    hunt sniper is created idle (one buy a start), the stop flag is read once more, and ONE `oktech cycle` job is
    started with the test's caps (jobs.ops.op_run: the round's own job runner; the agents' start_job refuses OKTech
    commands). TEST.json then says running, operator/expected.json expects that sniper and its coin, and the owner hears
    it. A failed create starts nothing. Returns the test as it stands, or None when there is no request."""
    t = load(paths)
    if t is None or t["state"] not in ("requested", "starting"):
        return None
    notes = notes or NoteSink(paths, now=lambda: now)
    if t["state"] == "starting":
        return _resolve_starting(paths, t, now, notes, notifier, log)
    if _expired(t, now):
        return _end(paths, t, now, notes, notifier, log, state="expired",
                    error=f"expired: no round started it within {REQUEST_TTL_SECS // 60} minutes")  # fmt: skip
    why, wait = await _recheck(svc, cfg, paths, t, snap, now)
    if why and wait:
        if log is not None:
            log(f"{t['id']} waits for the next round: {why}")
        return t
    if why:
        return _end(paths, t, now, notes, notifier, log, error=f"not started: {why}")
    starting = t | {"state": "starting", "starting_at": int(now)}
    written, cur = _swap(paths, _same(t, "requested"), starting)
    if not written:  # the owner's /stop cancelled it while the checks ran
        return cur
    wallet_name = t.get("oktech_wallet") or cfg.oktech.wallet or DEFAULT_OKTECH_WALLET
    try:  # the salt gives this test a sniper of its own (OKTech refuses a second task with the same externalId)
        res = await svc.oktech_strategy(t["id"], wallet_name, create=True, buy_sol=float(t["buy_sol"]), max_buys=1,
                                        via="hunt", salt=f"test-{t['requested_at']}")  # fmt: skip
        row = next((r for r in (res or {}).get("snipers") or [] if r.get("id") == t["id"]), None) or {}
        oid = row.get("oktech_id")
        if not oid:
            raise RuntimeError(row.get("error") or "OKTech's answer carried no sniper id")
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - SystemExit: `oktech_strategy` found no such strategy
        return _end(paths, starting, now, notes, notifier, log,
                    error=f"not started: the sniper was not created ({type(exc).__name__}: {str(exc)[:200]}); nothing "
                          "was started (a sniper it may have made is idle and buys nothing)")  # fmt: skip
    created = _put(paths, starting | {"oktech_id": str(oid)})
    if paths.stop_flag.exists():  # the owner's /stop came while the sniper was being made: it is idle, it buys nothing
        return _end(paths, created, now, notes, notifier, log,
                    error=f"not started: your /stop came while its sniper {oid} was being created; it was left idle on "
                          "OKTech (it buys nothing)")  # fmt: skip
    try:
        job = jobs.ops.op_run(cycle_argv(created, cfg), timeout=int(created["minutes"]) * 60 + JOB_MARGIN_SECS)
    except Exception as exc:  # noqa: BLE001 - nothing is armed: the sniper stays idle
        return _end(paths, created, now, notes, notifier, log,
                    error=f"not started: the cycler did not start ({type(exc).__name__}); its sniper {oid} is idle on "
                          "OKTech (it buys nothing)")  # fmt: skip
    running = _put(paths, created | {"state": "running", "job_id": job.get("id"), "started_at": int(now)})
    _expect(paths, {"snipers_running": 1, "held": 1})
    _say(f"Live test started: {running['label']} ({running['id']}), OKTech sniper {oid}, kept armed by job "
         f"{running['job_id']}: {_caps(running)}. /test shows how it goes; /stop ends it (a sniper holding a coin keeps "
         "running until the coin is sold).", notes, notifier, log)  # fmt: skip
    return running


def _json_tail(text: str) -> dict | None:
    """The report `oktech cycle --json` prints last: it starts at the last line that is a lone "{"."""
    lines = text.splitlines()
    dec = json.JSONDecoder()
    for i in range(len(lines) - 1, -1, -1):
        if lines[i] != "{":
            continue
        try:
            obj, _end_at = dec.raw_decode("\n".join(lines[i:]))
        except ValueError:
            continue
        return obj if isinstance(obj, dict) else None
    return None


def _cycle_report(paths: OperatorPaths, svc, t: dict, meta: dict) -> dict:
    """What the cycler said at its end (fills, start and end balance, why it ended, coins still held), or, when it
    ended without its report, its exit code, its last words and the fills on the tape."""
    try:
        text = (paths.jobs / f"{t['job_id']}.log").read_text(errors="replace")
    except OSError:
        text = ""
    rep = _json_tail(text)
    if rep is not None:
        held = [str(h.get("mint")) for h in rep.get("held") or [] if isinstance(h, dict) and h.get("mint")]
        return {"fills": rep.get("fills"), "start_sol": rep.get("start_sol"), "end_sol": rep.get("end_sol"),
                "reason": redact(str(rep.get("reason") or ""))[:300], "restarts": rep.get("restarts"), "held": held,
                "exit_code": meta.get("exit_code")}  # fmt: skip
    last = " ".join(x.strip() for x in text.splitlines() if x.strip())[-240:]
    store = getattr(svc, "store", None)
    return {"fills": _fills_since(store, t["wallet"], t["started_at"]) if store is not None else None,
            "start_sol": None, "end_sol": None, "exit_code": meta.get("exit_code"), "held": [],
            "reason": redact(f"the cycler ended without its report (exit {meta.get('exit_code')}): {last or 'no output'}")}  # fmt: skip


def finish_ended(svc, cfg: Config, paths: OperatorPaths, jobs, now: float, *, notes: NoteSink | None = None,
                 notifier=None, log: Log | None = None) -> dict | None:  # fmt: skip
    """A running test whose cycler has ended: TEST.json done with the cycler's report, the owner's summary, and
    operator/expected.json back to none running. None while it still runs (or when no test runs)."""
    t = load(paths)
    if t is None or t["state"] != "running":
        return None
    meta = jobs.ops._meta(str(t["job_id"])) if t.get("job_id") else None
    if meta is not None and meta.get("running"):
        return None
    notes = notes or NoteSink(paths, now=lambda: now)
    report = _cycle_report(paths, svc, t, meta or {}) if meta is not None else {
        "fills": None, "start_sol": None, "end_sol": None, "held": [], "exit_code": None,
        "reason": "the cycler's job record is missing"}  # fmt: skip
    _expect(paths, dict(NONE_EXPECTED))
    return _end(paths, t, now, notes, notifier, log, report=report)


async def round_step(svc, cfg: Config, paths: OperatorPaths, jobs, now: float, *, snap=None,
                     notes: NoteSink | None = None, notifier=None) -> list[str]:  # fmt: skip
    """One round's live-test work, from `operate` (never on a dry run, never from the one-minute pass): end a test
    whose cycler ended, then start (or expire) a request. Returns lines for the operator log."""
    lines: list[str] = []
    finish_ended(svc, cfg, paths, jobs, now, notes=notes, notifier=notifier, log=lines.append)
    await start_requested(svc, cfg, paths, jobs, now, snap=snap, notes=notes, notifier=notifier, log=lines.append)
    return lines
