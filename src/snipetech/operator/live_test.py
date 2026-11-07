"""The owner's own capped live test, started and ended from Telegram: no SSH, no command line.

`/test` shows the hunt strategies the forward paper test backs where a live hunt fill lands (that is the evidence,
CLAUDE.md rule 2; the lab alone never is) and the plan for the best one: the wallet, the size of a buy, how many buys,
for how long, and the floor. "/test <id> go" is the owner's explicit yes for exactly that plan (rule 1: wallet, size,
duration). It writes a request to operator/TEST.json and nothing else. The next operator round checks every refusal
again, creates the strategy's own hunt sniper on OKTech (idle, one buy a start) and starts one `oktech cycle` job that
keeps it armed (a hunt sniper fires once per start, 5 Nov) until 10 fills, 120 minutes, the floor or the owner's /stop.

A test ends with nothing left armed. The cycler stops its sniper on the way out while the wallet holds nothing, and
the round checks it on OKTech: a test is done only once its sniper is stopped (or OKTech no longer runs it). While the
wallet holds a coin, that sniper keeps running to sell it (a stopped sniper strands its coin) and the test stays
"closing", its sniper and its coin expected, until a round finds the wallet empty and stops the sniper.

Only the round starts it: its unit leaves the jobs it starts running when the round ends and through deploys
(KillMode=process), while the one-minute commands pass kills its children when it exits and the ops service kills its
jobs on every deploy. One test at a time: a test requested, starting, running or closing refuses another, and so do an
OKTech cycler or guard already running and any sniper active on OKTech that is not the book's. Nothing starts while the
tape is more than two minutes behind: the cap counts the test's buys there. A crash mid-start never leaves a second
sniper: "starting" is written before the create call, and nothing is created or started from "starting" again. /stop
cancels a test that has not started and ends a running one (the cycler stops re-arming; a sniper holding a coin keeps
running to sell it). No agent tool reaches this module: their job allowlist excludes `oktech`, and none of their tools
writes TEST.json."""

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
from ..positions import is_active
from . import risk_guard
from .paths import OperatorPaths
from .snapshot import running_jobs
from .tools import NoteSink

MAX_BUY_SOL = 0.5  # the most one buy of a test may be, whatever size the strategy was frozen at
FILLS = 10  # buys a test makes at most: the cycler stops re-arming at the 10th buy of the wallet on the tape
MINUTES = 120  # how long the cycler keeps the sniper armed
MAX_MINUTES = 180  # the longest an automatic test's block may run (operator/AUTOTEST's bound; /test go plans MINUTES)
MIN_FORWARD_N = 100  # forward paper trades at the real landing a strategy needs before it goes live
REQUEST_TTL_SECS = 30 * 60  # a yes that no round acted on within two rounds is stale: the owner sends it again
# Fees beyond the buys themselves: about 0.009 SOL a live round trip, failed duplicate sells included (6 Nov), and about
# 0.001 SOL a failed buy (OKTech sends those anyway and each spends the shot, 5 Nov). Ten round trips plus one failed
# send a minute for 120 minutes is about 0.2 SOL, so 0.5 leaves room.
# the owner's yes (7 Nov 09:15, docs/live-recipe.md): a test stops on its own once the wallet is this far below the
# balance it started from (the coin it holds then may take it a little further while it sells)
LOSS_SOL = 1.0
BUY_FEE_MARGIN_SOL = 0.05  # one buy's fees and failed duplicate sends above the floor, before a test may start
MIN_FLOOR_SOL = 1.0  # the floor is never under this, however small the wallet
SNAPSHOT_MAX_AGE_SECS = 40 * 60  # the balance comes from the last round's snapshot: older than two rounds is unknown
JOB_MARGIN_SECS = 30 * 60  # the cycler's own time limit past its minutes (its last balance reads, a slow RPC)
# how long a check waits for the wallet's coins: the one-minute pass also carries /stop, and one hanging holdings read
# retried for about 250 s (operator/run.py WATCH_TIMEOUT_SECS); past it the owner asks again, the round tries again
WALLET_READ_SECS = 20
SHOWN = 3  # strategies /test lists
# closing: the cycler has ended, and the test's sniper still runs (it sells the coin the wallet holds, or it could not
# be stopped yet); each round looks again, and the test is done once nothing of it is left armed
STATES = ("requested", "starting", "running", "closing", "done", "expired")
ACTIVE = ("requested", "starting", "running", "closing")
DEFAULT_OKTECH_WALLET = "SNIPER"  # what `oktech strategy` and `oktech push` buy from when [oktech] wallet is empty
DRIVERS = ("cycle", "guard")  # OKTech jobs that drive a live test: while one runs, no second test starts
NONE_EXPECTED = {"snipers_running": 0, "held": 0}
TEST_EXPECTED = {"snipers_running": 1, "held": 1}  # while a test runs or closes: its one sniper and its one coin
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


def in_progress(paths: OperatorPaths, now: float) -> bool:
    """A live test is requested, starting, running or closing (a request no round acted on in time is not)."""
    return _active(load(paths), now)


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


def _why_not(row: dict | None, frozen: dict | None, tx_cost: float = 0.0) -> str | None:
    """Why a strategy may not go live now, in plain words; None when it may. `tx_cost`: the SOL a transaction costs
    live, as the measure stage measures it (qualify.run.lab_tx_cost)."""
    if row is None or frozen is None:
        return "is not a frozen paper strategy"
    if row.get("trigger_kind") != "hunt":
        return "is not a hunt strategy (OKTech runs only its hunt launch gate exactly as the lab scored it)"
    if not row.get("live_landing"):
        return "was not scored where a live hunt fill lands"
    try:
        scored = float(frozen.get("per_tx_sol"))
    except (TypeError, ValueError):
        scored = -1.0
    if not math.isfinite(scored) or scored < float(tx_cost) - 1e-9:
        # the owner's yes covers a strategy that passes "at real costs": `paper freeze` takes 0.0035 SOL a transaction
        # unless told otherwise (and accepts 0), while a live round trip cost about 0.0094 on 6 Nov; trades scored at
        # less are not evidence at the real cost
        at = f"{scored:g} SOL a transaction" if scored >= 0 else "no known cost"
        return (f"was scored at {at}, below the {float(tx_cost):g} SOL a transaction measured live (a round trip about "
                f"{2 * float(tx_cost):.4f}): its forward trades are not at the real cost")  # fmt: skip
    n = int(row.get("n") or 0)
    if n < MIN_FORWARD_N:
        return f"has {n} forward trades of the {MIN_FORWARD_N} needed"
    lower = row.get("ev_lower")
    if lower is None or float(lower) <= 0:
        return f"has a lower bound of {float(lower or 0):+.4f} SOL a trade, not above zero"
    capped = row.get("ev_lower_capped")
    if capped is None or float(capped) <= 0:
        # 7 Nov: three trades made a rule's whole paper profit; with each gain capped at 2.5 x its buy it lost
        return (
            f"rests on a few big trades: with each gain capped at 2.5 times its buy its lower bound is "
            f"{float(capped or 0):+.4f} SOL a trade"
        )
    from ..oktech import strategy_sniper

    try:  # the sniper it would get: OKTech ignores a hunt threshold that is not a whole number (5 Nov 22:11)
        strategy_sniper(frozen | {"buy_sol": row.get("buy_sol")}, 1, "hunt")
    except (ValueError, KeyError, TypeError) as exc:
        return f"cannot be sent to OKTech as it was scored ({exc})"
    return None


def _survey(store, cfg: Config, now: float) -> tuple[list[dict], dict[str, str], list[dict]]:
    """(the eligible strategies, best lower bound first; why each other one is not; every paper report row)."""
    from ..analysis import paper
    from ..qualify.run import lab_tx_cost

    rows = paper.report(store, cfg, now=now)["strategies"]
    frozen = {s["id"]: s for s in store.coverage("paper_strategies").get("strategies", []) if isinstance(s, dict)}
    tx_cost = lab_tx_cost(store, cfg)  # the measured cost of a transaction (the flat 0.0035 until it is measured)
    ok, why = [], {}
    for r in rows:
        w = _why_not(r, frozen.get(r["id"]), tx_cost)
        if w is None:
            ok.append(r)
        else:
            why[r["id"]] = w
    return sorted(ok, key=lambda r: -float(r["ev_lower"])), why, rows


def eligible(store, cfg: Config, now: float) -> list[dict]:
    """The frozen paper strategies a live test may run, best lower bound first (paper.report rows): hunt strategies (the
    one kind OKTech runs exactly as the lab scored it), scored at the live landing and at no less than the measured cost
    of a transaction, with MIN_FORWARD_N forward trades or more and a lower bound above zero, also with each gain
    capped. The forward test at the real landing and the real cost is the evidence, never the lab."""
    return _survey(store, cfg, now)[0]


def plan(row: dict, equity_sol: float, wallet: str, *, fills: int = FILLS, minutes: int = MINUTES,
         loss_sol: float = LOSS_SOL, floor: float | None = None) -> dict:  # fmt: skip
    """The live test of one eligible strategy: its own size a buy (refused above MAX_BUY_SOL), `fills` buys at most (FILLS
    or fewer), one coin at a time, `minutes` at most (MAX_MINUTES or fewer), and a floor `loss_sol` below the equity it
    starts from (the owner's 1 SOL stop), never under MIN_FLOOR_SOL, rounded down to the cent; or `floor` as given (the
    automatic test's later blocks keep the first block's, autotest.py). Raises ValueError in plain words."""
    buy = float(row.get("buy_sol") or 0)
    if not buy > 0:
        raise ValueError("it has no size a buy")
    if buy > MAX_BUY_SOL + 1e-9:
        raise ValueError(f"it buys {buy:g} SOL at a time, above the {MAX_BUY_SOL:g} SOL a live test may buy")
    if not (1 <= int(fills) <= FILLS and 1 <= int(minutes) <= MAX_MINUTES):
        raise ValueError(f"a test makes 1 to {FILLS} buys in 1 to {MAX_MINUTES} minutes")
    since = "its start" if floor is None else "the first block's start"
    if floor is None:
        floor = math.floor(max(MIN_FLOOR_SOL, float(equity_sol) - float(loss_sol)) * 100 + 1e-6) / 100
    floor = round(float(floor), 2)
    worst = (f"at most {int(fills)} buys of {buy:g} SOL, one coin at a time; it buys nothing more once the wallet (SOL "
             f"and coins) is below {floor:.2f} SOL, {float(loss_sol):g} SOL below {since} (the coin it holds then may "
             "take it a little further while it sells)")  # fmt: skip
    return {"id": row["id"], "label": str(row.get("label") or row["id"]), "buy_sol": buy, "fills": int(fills),
            "minutes": int(minutes), "floor": floor, "wallet": wallet, "equity_sol": round(float(equity_sol), 4),
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
    """What the wallet must hold for a test to start: the floor, one buy and its fees."""
    return round(float(p["floor"]) + float(p["buy_sol"]) + BUY_FEE_MARGIN_SOL, 6)


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


def _tape_lag(svc, now: float) -> str | None:
    """Why the tape cannot count a test's buys now, or None. The cycler's cap counts them there, so the book's rule
    holds: nothing starts while it is more than TAPE_STALE_SECS behind. An empty tape is a fresh install, as for the
    book."""
    from ..book.run import TAPE_STALE_SECS, _tape_age

    store = getattr(svc, "store", None)
    if store is None:
        return "the tape cannot be read"
    try:
        age = _tape_age(store, now)
    except Exception as exc:  # noqa: BLE001 - said to the owner; the next try reads it again
        return f"the tape could not be read ({type(exc).__name__})"
    if age is not None and age > TAPE_STALE_SECS:
        return f"the tape (the trade recorder) is {age / 60:.0f} min behind"
    return None


async def _other_snipers(svc) -> list[dict]:
    """The snipers active on OKTech now that are not the book's: an earlier test's left armed, or one started by hand.
    One test at a time is counted there too, not only in our own files: two snipers on one wallet can buy two coins
    at once. The book's snipers trade from the same wallet by design (their buys count toward the cap). Waited for
    WALLET_READ_SECS at most; raises when OKTech does not answer."""
    from ..book.policy import BOOK_NAME_RE

    st = await asyncio.wait_for(svc.oktech_status(), WALLET_READ_SECS)
    return [s for s in (st or {}).get("snipers") or [] if is_active(s.get("status"))
            and not BOOK_NAME_RE.match(str(s.get("name") or ""))]  # fmt: skip


def _sniper_words(s: dict) -> str:
    return f"sniper {s.get('id')} ({str(s.get('name') or 'no name')[:60]}) is {s.get('status')} on OKTech"


async def _sniper_status(svc, oid: str) -> str | None:
    """OKTech's status of one sniper (None: OKTech does not list it), waited for WALLET_READ_SECS at most."""
    st = await asyncio.wait_for(svc.oktech_status(), WALLET_READ_SECS)
    row = next((s for s in (st or {}).get("snipers") or [] if str(s.get("id")) == oid), None)
    return None if row is None else str(row.get("status") or "")


def _real_coins(svc, cfg: Config, rows) -> list[str]:
    """The coins among a cycler's `held` rows by the one shared rule (positions.filter_positions: no stablecoin, no dust
    a sale leaves behind, none of the owner's ignored coins). Cyclers started before 7 Nov listed every token account
    (6 Nov: 0.0006 USDC sat in this wallet), and the owner was told of coins stranded when none were."""
    from ..positions import filter_positions

    rows = [h for h in rows or [] if isinstance(h, dict) and h.get("mint")]
    ignore = tuple(cfg.operator.ignore_mints or ())
    store = getattr(svc, "store", None)
    try:
        kept = filter_positions(rows, store.last_price if store is not None else (lambda m: None), ignore=ignore)
    except Exception:  # noqa: BLE001 - the tape's prices could not be read: each coin unpriced, still a coin
        kept = filter_positions(rows, lambda m: None, ignore=ignore)
    return list(dict.fromkeys(str(h["mint"]) for h in kept))


def _fills_since(store, wallet: str, since: float) -> int | None:
    """The wallet's buys on the tape since `since`: what the cycler counts as fills."""
    try:
        return sum(1 for t in store.trades_for_user(wallet, int(since)) if t.is_buy)
    except Exception:  # noqa: BLE001 - a status line, never a reason to fail
        return None


TEST_GRACE_SECS = 60  # our clock against block time, at the start of a test's window (autotest.GRACE_SECS)


def _bought(store, t: dict, now: float) -> set[str] | None:
    """The coins the wallet bought while the test's cycler ran (its window on the tape), or None when the tape cannot
    be read: the book trades from the same wallet, and only these can be the test's own coins."""
    rep = t.get("report") or {}
    start = float(t.get("started_at") or now) - TEST_GRACE_SECS
    end = rep.get("job_ended") or t.get("cycler_ended_at") or now
    try:
        return {tr.mint for tr in store.trades_for_user(t["wallet"], int(start)) if tr.is_buy and tr.ts <= float(end)}
    except Exception:  # noqa: BLE001 - every coin then counts as possibly the test's, as before
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
        ("- /stop ends it at any time. When it ends, its sniper is stopped as soon as the wallet holds nothing: a sniper "
         "holding a coin keeps running until the coin is sold, and the unsold-coin watch and /sell are there for a coin "
         "OKTech does not sell (5 Nov: 2 of 11 hunt fills got no sell)"),
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
    if (t.get("sequence") or {}).get("block"):  # started by the owner's standing yes (autotest.py)
        head += f", automatic test, block {t['sequence']['block']}"
    if t["state"] == "requested":
        return (f"Live test requested at {_hm(t['requested_at'])} UTC: {head}, {_caps(t)}. It starts at the next "
                f"operator round (within 15 minutes) and expires if no round has started it by "
                f"{_hm(float(t['requested_at']) + REQUEST_TTL_SECS)} UTC. /stop cancels it.")  # fmt: skip
    if t["state"] == "starting":
        return f"Live test starting now: {head}; the round is creating its sniper. {_caps(t)}."
    if t["state"] == "closing":
        return (f"Live test {head} ended at {_hm(t.get('cycler_ended_at') or now)} UTC: {_result(t.get('report') or {})}. "
                f"It is not closed yet: {t.get('closing_why') or 'its sniper is being stopped'}. The round looks again "
                "every 15 minutes, and no other test starts until it is closed.")  # fmt: skip
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
              "trades where a live hunt fill lands, at the cost a trade has live, and a lower bound above zero.")]  # fmt: skip
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
    if args[0].lower() == "auto":  # the owner's standing yes: /test auto, /test auto on, /test auto off
        from . import autotest

        return autotest.command(args[1:], cfg=cfg, paths=paths, notes=notes, now=now, date=date)
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
    return "\n".join([*lines, *_plan_lines(ok[0], cfg, paths, snap, now, getattr(svc, "store", None))])


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
    return "\n".join(_plan_lines(row, cfg, paths, snap, now, getattr(svc, "store", None)))


def _plan_lines(row: dict, cfg: Config, paths: OperatorPaths, snap, now: float, store=None) -> list[str]:
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
                         f"{_need(p):.2f} SOL (the {p['floor']:.2f} SOL floor plus one buy of {p['buy_sol']:g} and "
                         "its fees).")]  # fmt: skip
    g = risk_guard.from_snapshot(store, cfg, paths, now, snap)
    if g.tripped:
        return [*lines, f"Not possible now: the wallet-wide guard holds: {g.reason}. /risk shows it."]
    return [*lines, "To start it, send this line; the next operator round (within 15 minutes) starts it:",
            f"/test {p['id']} go"]  # fmt: skip


async def _go(sid: str, *, svc, cfg: Config, paths: OperatorPaths, snap, notes: NoteSink, now: float,
              date: int | None) -> str:  # fmt: skip
    """The owner's yes. Every refusal changes nothing and says why; the RPC is asked last, and the request is written
    only if no other test was written meanwhile."""
    no = "Nothing started: "
    t = load(paths)
    if _active(t, now) and t["state"] == "closing":
        return no + (f"the last live test ({t['id']}) is still closing: "
                     f"{t.get('closing_why') or 'its sniper is being stopped'}. Send /test to see it.")  # fmt: skip
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
                     f"{p['floor']:.2f} SOL floor plus one buy of {p['buy_sol']:g} and its fees).")  # fmt: skip
    busy = _drivers(paths)
    if busy:
        return no + (f"an OKTech job is running ({busy[0]['id']}: {' '.join(busy[0]['argv'][:2])}): one live test at "
                     "a time.")  # fmt: skip
    lag = _tape_lag(svc, now)
    if lag:
        return no + f"{lag}, and the test's buys are counted on it. Send /test {sid} go again in a few minutes."
    try:
        others = await _other_snipers(svc)
    except Exception as exc:  # noqa: BLE001 - the owner hears it and sends it again
        return no + f"I could not read OKTech's snipers ({type(exc).__name__}). Try again in a minute."
    if others:
        return no + (f"{_sniper_words(others[0])} and is not the book's: one live test at a time (an earlier test's "
                     f"sniper, or one started by hand). /stop stops every sniper that holds nothing; then /resume and "
                     f"send /test {sid} go again.")  # fmt: skip
    try:
        held = await _held(svc, cfg, wallet)
    except Exception as exc:  # noqa: BLE001 - the owner hears it and sends it again
        return no + f"I could not read the wallet's coins ({type(exc).__name__}). Try again in a minute."
    if held:
        return no + (f"the wallet holds {_coins(held)}, and a test starts only from a wallet that holds none (a coin "
                     f"held stops the cycler from re-arming). Send /sell to see them, then /test {sid} go again.")  # fmt: skip
    g = risk_guard.evaluate(svc.store, cfg, paths, now, wallet=wallet, sol=eq, held=held)
    if g.tripped:
        return no + f"the wallet-wide guard holds: {g.reason}. /risk shows it; /risk stop or /risk floor changes it."
    if not g.known:
        return no + f"{g.reason}. Try again in a minute."
    if not write_request(paths, request(p, row, cfg, now, by="telegram", message_date=date), now):
        return no + "another live test was requested a moment ago. Send /test to see it."
    notes.write(f"owner requested a live test from Telegram (/test {sid} go): {p['label']}, {_caps(p)}, wallet "
                f"{wallet}; the next operator round starts it", "decision")  # fmt: skip
    head = (f"Live test requested: {p['label']} ({sid}). It starts at the next operator round, within 15 minutes; if "
            f"no round has started it by {_hm(now + REQUEST_TTL_SECS)} UTC it expires and nothing is bought.")  # fmt: skip
    return "\n".join([head, *_plan_words(p, cfg, paths)])


def request(p: dict, row: dict, cfg: Config, now: float, *, by: str, **extra) -> dict:
    """The request for plan `p` of paper row `row`, as /test go writes it and the automatic test (by "autotest") too:
    the plan, the OKTech wallet, the forward numbers it was started on, when and by whom."""
    return p | {"state": "requested", "oktech_wallet": cfg.oktech.wallet or DEFAULT_OKTECH_WALLET,
                "forward": {k: row.get(k) for k in ("n", "mean", "ev_lower", "landing")},
                "requested_at": int(now), "by": by} | extra  # fmt: skip


def write_request(paths: OperatorPaths, req: dict, now: float) -> bool:
    """Write `req` only while no test is requested, starting, running or closing; False when another was written
    meanwhile. A request no round ever acted on is kept in the record as expired."""
    written, before = _swap(paths, lambda cur: not _active(cur, now), req)
    if written and before and _expired(before, now):
        _history(paths, before | {"state": "expired", "ended_at": int(now), "error": "expired: no round started it"})
    return written


def find(paths: OperatorPaths, sid: str, requested_at) -> dict | None:
    """The test of strategy `sid` requested at `requested_at`: TEST.json while it is the current one, else its last line
    in tests.jsonl; None when neither has it."""
    t = load(paths)
    if t and t["id"] == sid and t.get("requested_at") == requested_at:
        return t
    try:
        lines = history_path(paths).read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            h = json.loads(line)
        except ValueError:
            continue
        if isinstance(h, dict) and h.get("id") == sid and h.get("requested_at") == requested_at and h.get("state"):
            return h
    return None


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
    if t["state"] == "closing":
        return (f"The live test ({t['id']}) has already ended; its sniper is stopped as soon as the wallet holds "
                "nothing.")  # fmt: skip
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
         error: str | None = None, text: str | None = None) -> dict:  # fmt: skip
    """The test's last record (TEST.json, one line in tests.jsonl) and the owner's line: `text` for a test that ran,
    else why it expired or did not start."""
    done = t | {"state": state, "ended_at": int(now), "started": bool(t.get("job_id"))}
    if error is not None:
        done["error"] = redact(error)
    _put(paths, done)
    _history(paths, done)
    head = f"{t.get('label') or t['id']} ({t['id']})"
    if text is not None:
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
    /test go and the automatic test write it, and nothing above them is ever started. An automatic request starts only
    while the owner's standing yes still covers it (autotest.holds)."""
    if paths.stop_flag.exists():
        return "your /stop is in force", False
    auto = t.get("by") == "autotest"
    try:
        bad = (float(t["buy_sol"]) > MAX_BUY_SOL + 1e-9 or int(t["fills"]) > FILLS
               or int(t["minutes"]) > (MAX_MINUTES if auto else MINUTES)
               or float(t["floor"]) < MIN_FLOOR_SOL or float(t["buy_sol"]) <= 0)  # fmt: skip
    except (KeyError, TypeError, ValueError):
        bad = True
    if bad:
        return "the request is not one /test go writes (its caps are missing or above the limits)", False
    if auto:
        from . import autotest

        why = autotest.holds(paths, t, cfg)
        if why:
            return why, False
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
    lag = _tape_lag(svc, now)
    if lag:  # the recorder catching up is the usual end of it
        return lag, True
    try:
        others = await _other_snipers(svc)
    except Exception as exc:  # noqa: BLE001 - the next round reads it again
        return f"OKTech's snipers could not be read ({type(exc).__name__})", True
    if others:
        return f"{_sniper_words(others[0])} and is not the book's", False
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
    g = risk_guard.evaluate(svc.store, cfg, paths, now, wallet=t["wallet"], sol=eq, held=held)
    if g.tripped:  # the day stop lifts at 00:00 UTC and the floor with a deposit, long after this request expires
        return f"the wallet-wide guard holds: {g.reason}", False
    if not g.known:
        return g.reason, True
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
        _expect(paths, dict(TEST_EXPECTED))
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
    _expect(paths, dict(TEST_EXPECTED))
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


def _cycle_report(paths: OperatorPaths, svc, cfg: Config, t: dict, meta: dict) -> dict:
    """What the cycler said at its end (fills, start and end balance, why it ended, the coins it saw held), or, when it
    ended without its report, its exit code, its last words and the fills on the tape (`held` None: not known then).
    `job_ended`: when its job ended by the job record (the round that notices it comes up to 15 minutes later), None
    when that is not known."""
    try:
        text = (paths.jobs / f"{t['job_id']}.log").read_text(errors="replace")
    except OSError:
        text = ""
    rep = _json_tail(text)
    ended = meta.get("ended") if isinstance(meta.get("ended"), (int, float)) else None
    if rep is not None:
        return {"fills": rep.get("fills"), "start_sol": rep.get("start_sol"), "end_sol": rep.get("end_sol"),
                "reason": redact(str(rep.get("reason") or ""))[:300], "restarts": rep.get("restarts"),
                "held": _real_coins(svc, cfg, rep.get("held")), "exit_code": meta.get("exit_code"),
                "job_ended": ended}  # fmt: skip
    last = " ".join(x.strip() for x in text.splitlines() if x.strip())[-240:]
    store = getattr(svc, "store", None)
    return {"fills": _fills_since(store, t["wallet"], t["started_at"]) if store is not None else None,
            "start_sol": None, "end_sol": None, "exit_code": meta.get("exit_code"), "held": None, "job_ended": ended,
            "reason": redact(f"the cycler ended without its report (exit {meta.get('exit_code')}): {last or 'no output'}")}  # fmt: skip


def _ended_words(t: dict, now: float) -> str:
    """The first line on a test whose cycler has ended: how long it ran and what its cycler reported."""
    mins = (now - float(t.get("started_at") or now)) / 60
    return (
        f"Live test ended: {t.get('label') or t['id']} ({t['id']}), after {mins:.0f} minutes. {_result(t['report'])}."
    )


def _record(t: dict) -> str:
    return f" The record: jobs/{t['job_id']}.log." if t.get("job_id") else ""


def _closing(paths: OperatorPaths, t: dict, now: float, notes: NoteSink, notifier, log: Log | None, kind: str,
             why: str) -> dict:  # fmt: skip
    """The cycler has ended and the test's sniper may still run: the test stays closing, its sniper and its coin
    expected (the gate stays quiet about them), and the next round looks again. The owner hears it when the cycler
    ends and whenever the reason changes (a coin sold, then a stop refused); a round that finds it unchanged only logs."""
    first = t["state"] == "running"
    closing = t | {"state": "closing", "closing_kind": kind, "closing_why": why,
                   "cycler_ended_at": t.get("cycler_ended_at") or int(now)}  # fmt: skip
    _put(paths, closing)
    _expect(paths, dict(TEST_EXPECTED))
    if first:
        _say(f"{_ended_words(t, now)} It is not closed yet: {why}. The round looks again every 15 minutes; /stop stops "
             f"every sniper that holds nothing.{_record(t)}", notes, notifier, log)  # fmt: skip
    elif kind != t.get("closing_kind"):
        _say(f"Live test {t.get('label') or t['id']} ({t['id']}) is still closing: {why}. The next round looks again.",
             notes, notifier, log)  # fmt: skip
    elif log is not None:
        log(f"{t['id']} still closing: {why}")
    return closing


async def _close(svc, cfg: Config, paths: OperatorPaths, t: dict, now: float, notes: NoteSink, notifier,
                 log: Log | None) -> dict:  # fmt: skip
    """One look at a test whose cycler has ended (see finish_ended): done when nothing of it is left armed, else
    closing. Only the test's own sniper is ever stopped, and only while the wallet holds nothing (a stopped sniper
    strands its coin, rule 1)."""
    oid = str(t.get("oktech_id") or "")
    try:
        held = await _held(svc, cfg, t["wallet"])
    except Exception as exc:  # noqa: BLE001 - unknown holdings never stop a sniper
        return _closing(paths, t, now, notes, notifier, log, "wallet", f"the wallet's coins could not be read "
                        f"({type(exc).__name__}), so its sniper {oid} is not stopped yet and may still be armed")  # fmt: skip
    status, unread = None, None
    if oid:
        try:
            status = await _sniper_status(svc, oid)
        except Exception as exc:  # noqa: BLE001 - the next round asks again
            unread = type(exc).__name__
    mints = list(dict.fromkeys(str(h.get("mint")) for h in held))
    if held and (unread or is_active(status)):
        return _closing(paths, t, now, notes, notifier, log, "held",
                        f"the wallet still holds {', '.join(mints)}: its sniper keeps running until the wallet holds "
                        "nothing (a stopped sniper strands its coin), then the round stops it. /sell <coin> sells one "
                        "now")  # fmt: skip
    if unread:
        return _closing(paths, t, now, notes, notifier, log, "oktech",
                        f"OKTech did not answer ({unread}), so its sniper {oid} is not stopped yet and may still be "
                        "armed")  # fmt: skip
    stopped = False
    if is_active(status):  # the wallet holds nothing here: stopping its own sniper strands nothing
        try:
            await asyncio.wait_for(svc.oktech_task("stop", oid), WALLET_READ_SECS)
            stopped = True
        except Exception as exc:  # noqa: BLE001 - the next round tries again
            return _closing(paths, t, now, notes, notifier, log, "stop",
                            f"its sniper {oid} could not be stopped ({type(exc).__name__}), so it may still be armed")  # fmt: skip
    sniper = f"its sniper {oid} is stopped: it buys nothing more" if stopped else f"its sniper {oid} is not running"
    bought = _bought(svc.store, t, now) if held and getattr(svc, "store", None) is not None else None
    own = [m for m in mints if bought is None or m in bought]
    others = [m for m in mints if m not in own]
    if own:  # its sniper is no longer running, so nothing of the test sells these
        sniper += f"; the wallet still holds {', '.join(own)} and nothing sells it: /sell <coin> sells it now"
    if others:  # bought outside the test's window: another sniper's (the book's), which manages it
        sniper += f"; the wallet also holds {', '.join(others)}, which this test did not buy"
    _expect(paths, dict(NONE_EXPECTED))
    if t["state"] == "running":
        text = f"{_ended_words(t, now)} {sniper[0].upper()}{sniper[1:]}.{_record(t)}"
    else:
        text = f"Live test closed: {t.get('label') or t['id']} ({t['id']}); {sniper}."
    done = t | {"sniper_stopped": stopped, "held_at_end": mints, "held_own": own}
    for k in ("closing_kind", "closing_why"):
        done.pop(k, None)
    return _end(paths, done, now, notes, notifier, log, text=text)


async def finish_ended(svc, cfg: Config, paths: OperatorPaths, jobs, now: float, *, notes: NoteSink | None = None,
                       notifier=None, log: Log | None = None) -> dict | None:  # fmt: skip
    """A running test whose cycler has ended, or one closing. Nothing of a test may outlive it armed: at the time limit
    (the usual end) its sniper has just been re-armed and has not fired, and a cycler that dies (a reboot, a kill)
    leaves it armed too, free to buy past the minutes the owner approved with nobody watching. So the test is done
    (TEST.json with the cycler's report, the owner's summary, operator/expected.json back to none running) only once
    the wallet holds nothing and its own sniper is stopped, or OKTech no longer runs it. While the wallet holds a coin
    that sniper keeps running to sell it and the test stays closing. None while the cycler runs (or no test does)."""
    t = load(paths)
    if t is None or t["state"] not in ("running", "closing"):
        return None
    if t["state"] == "running":
        meta = jobs.ops._meta(str(t["job_id"])) if t.get("job_id") else None
        if meta is not None and meta.get("running"):
            return None
        report = _cycle_report(paths, svc, cfg, t, meta) if meta is not None else {
            "fills": None, "start_sol": None, "end_sol": None, "held": [], "exit_code": None,
            "reason": "the cycler's job record is missing"}  # fmt: skip
        t = t | {"report": report, "cycler_ended_at": int(now)}
    notes = notes or NoteSink(paths, now=lambda: now)
    return await _close(svc, cfg, paths, t, now, notes, notifier, log)


async def round_step(svc, cfg: Config, paths: OperatorPaths, jobs, now: float, *, snap=None,
                     notes: NoteSink | None = None, notifier=None) -> list[str]:  # fmt: skip
    """One round's live-test work, from `operate` (never on a dry run, never from the one-minute pass): close a test
    whose cycler ended, then the owner's automatic test (autotest.py: it judges a block that ended and may write the
    next request, or the first one), then start (or expire) a request, so an automatic request starts in the same
    round. Returns lines for the operator log."""
    from . import autotest

    lines: list[str] = []
    notes = notes or NoteSink(paths, now=lambda: now)
    await finish_ended(svc, cfg, paths, jobs, now, notes=notes, notifier=notifier, log=lines.append)
    try:
        await autotest.step(svc, cfg, paths, now, snap=snap, notes=notes, notifier=notifier, log=lines.append)
    except Exception as exc:  # noqa: BLE001 - the owner's own /test go still starts; the owner hears it once a day
        lines.append(redact(f"automatic test step failed: {type(exc).__name__}: {exc}"))
        if not notes.has_today("automatic test step failed", now):
            text = notes.write(f"automatic test step failed: {type(exc).__name__}: {str(exc)[:160]}; the next round "
                               "looks again", "decision")  # fmt: skip
            if notifier is not None:
                notifier.send(text)
    await start_requested(svc, cfg, paths, jobs, now, snap=snap, notes=notes, notifier=notifier, log=lines.append)
    return lines
