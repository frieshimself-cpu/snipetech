"""The owner's standing yes for a live test that starts by itself: operator/AUTOTEST.

The owner's yes (7 Nov 09:15 UTC, docs/live-recipe.md, "Yes, use this plan"): "When a strategy passes on paper (100+ new
trades at the real landing, positive with outliers capped, at real costs), one live test starts straight away: sniper
wallet 6VFrZ15V..., 0.2 SOL a buy, one coin at a time, at most 10 buys, at most 2 hours, stopping on its own 1 SOL below
the starting balance. After 10 trades it continues only if live is at least half of paper (then 30 more trades, the same
size, the same 1 SOL stop counted from the first test's start); otherwise it stops and the analyst works out why."

How it runs. Each operator round, in live_test.round_step's order (after a test that ended is closed, before a request
is started): while AUTOTEST is on and no sequence runs, the round writes the /test go request itself (by "autotest") for
the best strategy live_test.eligible offers, once every refusal of the round's start holds no more, the owner's
wallet-wide guard included; live_test's own start path then creates the strategy's hunt sniper and runs its cycler in
the same round. One strategy per sequence, at the size it was scored at (never another: a round trip costs about 0.009
SOL whatever the size). When a block has made all its buys, its live result is read from the measure stage's fills: our
own round trips only, so a deposit, a withdrawal or OKTech wrapping SOL moves nothing (the cycler's start and end balance
would: 6 Nov 15:16 the owner moved 2.54 SOL out mid-test). The live mean per trade over every trade of the sequence so far
is compared with the strategy's forward paper mean: at least `continue_at` times it, and above zero, requests the next
block of the same strategy with the same caps and the first block's floor (the whole sequence can lose at most
`loss_sol`); else the sequence stops and the analyst works out why. At most `more_blocks` more blocks. A block that ends
early (its time limit, its floor, /stop, the guard) or does not start ends the sequence. At its end AUTOTEST turns itself
off (used, with the result), the owner gets one summary, and a new sequence needs the owner's yes again.

Only the owner turns it on: `snipetech operate autotest --on --approved "<the owner's words>"` (the cloud session, with the
owner's yes in chat) or /test auto on in Telegram (that message is the yes); /test auto off or `operate autotest --off`
turns it off, and a request it wrote that has not started is then never started. The agents cannot: their start_job
refuses `operate`, and none of their tools writes a file without an extension in operator/, or a .json other than
candidates.json."""

from __future__ import annotations

import fcntl
import json
import math
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..ops import redact
from . import live_test, risk_guard
from .paths import OperatorPaths
from .tools import NoteSink

BY = "autotest"  # the requests it writes say who wrote them, as /test go's say "telegram"
# the owner's plan of 7 Nov 09:15: 0.2 SOL a buy, 10 buys a block, 2 hours, the whole sequence at most 1 SOL below the
# first block's start, 30 more trades in 3 blocks of 10, each one only while live is at least half of paper
DEFAULTS: dict[str, float | int] = {"buy_sol": 0.2, "fills": 10, "minutes": 120, "loss_sol": 1.0, "more_blocks": 3,
                                    "continue_at": 0.5}  # fmt: skip
# each cap's bounds, in the words the owner reads when one is refused (a typo can never reach more than these)
RULES: dict[str, tuple[Callable[[float], bool], str]] = {
    "buy_sol": (lambda v: 0 < v <= live_test.MAX_BUY_SOL, "a buy is more than 0 and at most 0.5 SOL"),
    "fills": (lambda v: 1 <= v <= live_test.FILLS and v == int(v), "a block is 1 to 10 buys, a whole number"),
    "minutes": (
        lambda v: 1 <= v <= live_test.MAX_MINUTES and v == int(v),
        "a block runs 1 to 180 minutes, a whole number",
    ),
    "loss_sol": (
        lambda v: 0 < v <= 2,
        "the sequence stops more than 0 and at most 2 SOL below the first block's start",
    ),
    "more_blocks": (lambda v: 0 <= v <= 3 and v == int(v), "at most 3 more blocks, a whole number"),
    # never looser than the owner's "at least half of paper"
    "continue_at": (lambda v: 0.5 <= v <= 1, "live must be at least half of paper (0.5 to 1 times it)"),
}
FIELDS = tuple(RULES)
WHOLE = ("fills", "minutes", "more_blocks")
WORDS_MAX = 300  # the owner's words kept in the file
# after a block closed, how long its judgement waits for the measure stage to record its round trips (it runs every 5
# minutes, or every 15 as the round's stand-in); past it a buy with no sell recorded counts as its whole cost
MEASURE_WAIT_SECS = 45 * 60
# how long the next block may wait on a refusal that passes by itself (the recorder behind, OKTech not answering, the
# book's coin being sold) before the sequence ends
CONTINUE_WAIT_SECS = 60 * 60
GRACE_SECS = 60  # our clock against block time, at the start of a block's window


def autotest_file(paths: OperatorPaths) -> Path:
    return paths.root / "AUTOTEST"


def sequence_path(paths: OperatorPaths) -> Path:
    """The sequence in progress, or the last one: written by the round only."""
    return paths.root / "autotest.json"


# -- the owner's switch ----------------------------------------------------------------------------------------------


def _sane(name: str, value: Any) -> float | int | None:
    """`value` as the cap's number when it is a number inside its bounds, else None. Never raises."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        v = float(value)
    except OverflowError:
        return None
    if not math.isfinite(v) or not RULES[name][0](v):
        return None
    return int(v) if name in WHOLE else v


@contextmanager
def _locked(paths: OperatorPaths) -> Iterator[None]:
    """The owner (/test auto in the one-minute pass, the CLI) and the round (turning it off at a sequence's end) both
    write AUTOTEST: every read-and-write is done under this lock."""
    paths.ensure()
    fd = os.open(paths.root / "AUTOTEST.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _raw(paths: OperatorPaths) -> dict:
    try:
        data = json.loads(autotest_file(paths).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _approval(data: dict) -> dict | None:
    a = data.get("approved")
    if not isinstance(a, dict):
        return None
    words = " ".join(str(a.get("words") or "").split())[:WORDS_MAX]
    return {"words": words, "at": a.get("at")} if words else None


def read_autotest(paths: OperatorPaths) -> dict[str, Any]:
    """The owner's switch: each cap that is a number inside its bounds, else the owner's 7 Nov value; `on` only while the
    file says so and carries the owner's words; `approved` (the owner's words and when), `used` (the last sequence's
    result). Never raises: no file, or one that cannot be read, is off."""
    data = _raw(paths)
    out: dict[str, Any] = dict(DEFAULTS)
    for name in FIELDS:
        v = _sane(name, data.get(name))
        if v is not None:
            out[name] = v
    approved = _approval(data)
    used = data.get("used") if isinstance(data.get("used"), dict) else None
    return out | {"on": data.get("on") is True and approved is not None, "approved": approved, "ts": data.get("ts"),
                  "by": data.get("by"), "used": used}  # fmt: skip


def _write(paths: OperatorPaths, rec: dict) -> None:
    paths.ensure()
    f = autotest_file(paths)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, f)


def set_autotest(paths: OperatorPaths, on: bool, *, values: dict | None = None, words: str | None = None,
                 at: float | None = None, by: str, now: float | None = None) -> dict[str, Any]:  # fmt: skip
    """Turn the owner's standing yes on (the caps given over the owner's 7 Nov values, the owner's `words` and when
    they said them) or off (the file keeps its caps and words for the record). Every value is checked before anything is
    written: an unknown name, a cap out of its bounds, or on without the owner's words raises ValueError in plain words
    and changes nothing."""
    now = time.time() if now is None else now
    values = dict(values or {})
    unknown = sorted(set(values) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown automatic test value {', '.join(unknown)} (known: {', '.join(FIELDS)})")
    caps: dict[str, Any] = dict(DEFAULTS)
    for name, value in values.items():
        v = _sane(name, value)
        if v is None:
            raise ValueError(RULES[name][1])
        caps[name] = v
    with _locked(paths):
        if on:
            said = redact(" ".join(str(words or "").split()))[:WORDS_MAX]
            if not said:
                raise ValueError("turning it on needs the owner's words: their yes, as they said it")
            seq = load_seq(paths)
            if seq and seq.get("state") == "running":  # one sequence per yes: a second would have a floor of its own
                raise ValueError(f"a sequence is still running on your earlier yes ({seq.get('label')}, block "
                                 f"{seq['blocks'][-1]['n']}); it ends by itself (or with /stop), then turn it on "
                                 "again")  # fmt: skip
            rec = {"on": True, **caps, "approved": {"words": said, "at": int(now if at is None else at)},
                   "ts": int(now), "by": by}  # fmt: skip
        else:
            if values:
                raise ValueError("the caps go with turning it on")
            cur = _raw(paths)
            rec = {k: cur[k] for k in (*FIELDS, "approved", "used") if k in cur} | {
                "on": False,
                "ts": int(now),
                "by": by,
            }
        _write(paths, rec)
    return read_autotest(paths)


def _turn_off_used(paths: OperatorPaths, seq: dict, result: str, now: float) -> bool:
    """At a sequence's end: off, with what it did. Only the yes the sequence ran on: when the owner said yes again
    meanwhile, that one is a new yes for a new sequence and stays."""
    with _locked(paths):
        cur = _raw(paths)
        if _approval(cur) != seq.get("approved"):
            return False
        rec = {k: cur[k] for k in (*FIELDS, "approved") if k in cur}
        _write(paths, rec | {"on": False, "ts": int(now), "by": BY,
                             "used": {"sequence": seq["id"], "ended_at": int(now), "result": result[:600]}})  # fmt: skip
    return True


def is_on(paths: OperatorPaths) -> bool:
    """A live test may start by itself: the switch is on, or a sequence is running."""
    seq = load_seq(paths)
    return read_autotest(paths)["on"] or bool(seq and seq.get("state") == "running")


# -- the sequence's record ---------------------------------------------------------------------------------------------


def load_seq(paths: OperatorPaths) -> dict | None:
    try:
        d = json.loads(sequence_path(paths).read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("id") and d.get("state") in ("running", "ended", "dropped") else None


def save_seq(paths: OperatorPaths, seq: dict) -> None:
    paths.ensure()
    p = sequence_path(paths)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(seq, indent=1, default=str))
    os.replace(tmp, p)


def holds(paths: OperatorPaths, t: dict) -> str | None:
    """Why an automatic request may not start any more (None: it may): the owner's switch is still on, its caps still
    cover the request, and the sequence in progress names it as its newest block (live_test._recheck asks)."""
    at = read_autotest(paths)
    if not at["on"]:
        return "the automatic test was turned off since the request"
    try:
        above = (float(t["buy_sol"]) > float(at["buy_sol"]) + 1e-9 or int(t["fills"]) > int(at["fills"])
                 or int(t["minutes"]) > int(at["minutes"]))  # fmt: skip
    except (KeyError, TypeError, ValueError):
        above = True
    if above:
        return "the request is above the automatic test's caps"
    seq, own = load_seq(paths), t.get("sequence") or {}
    blocks = (seq or {}).get("blocks") or []
    if (not seq or seq.get("state") != "running" or seq.get("id") != own.get("id") or not blocks
            or blocks[-1].get("requested_at") != t.get("requested_at")):  # fmt: skip
        return "the request is not the automatic sequence's own"
    try:
        beyond = int(own.get("block") or 0) > 1 + int((seq.get("caps") or at)["more_blocks"])
        moved = abs(float(t["floor"]) - float(seq["floor"])) > 1e-9  # the whole sequence keeps the first block's floor
    except (KeyError, TypeError, ValueError):
        beyond = moved = True
    if beyond:
        return "the request is beyond the automatic test's blocks"
    if moved:
        return "the request's floor is not the sequence's"
    return None


# -- a block's live result ---------------------------------------------------------------------------------------------


def block_live(store, wallet: str, t: dict, now: float) -> dict[str, Any]:
    """A block's own round trips from the measure stage's fills. Its buys are the wallet's buys on the tape while its
    cycler ran (what the cycler counted); a buy the fills show copying a leader is the book's (it trades from the same
    wallet) and is left out; each other buy's result is that of the sells that closed it, real fees and failed duplicate
    sends included. `trades`: those results; `unmeasured`: buys the fills have no sell for yet, with their cost;
    `copies`: the book's buys left out. Only our own trades are read: a deposit or a withdrawal is in none of it."""
    start = int(float(t.get("started_at") or now)) - GRACE_SECS
    end = int(float(t.get("cycler_ended_at") or t.get("ended_at") or now))
    tape: dict[str, float] = {}
    for tr in store.trades_for_user(wallet, start):
        if tr.is_buy and tr.ts <= end:
            tape.setdefault(tr.sig, (int(tr.quote) + int(tr.fee)) / 1e9)
    buys = {f["sig"]: f for f in store.fills(wallet=wallet, since_ts=start, side="buy", limit=100_000)}
    result: dict[str, float] = {}
    for s in store.fills(wallet=wallet, since_ts=start, side="sell", limit=100_000):
        b = s.get("buy_sig")
        if b and s.get("pnl_sol") is not None:
            result[b] = result.get(b, 0.0) + float(s["pnl_sol"]) - float(s.get("twin_fee_sol") or 0.0)
    trades: list[float] = []
    unmeasured: list[dict] = []
    copies = 0
    for sig, cost in tape.items():
        f = buys.get(sig)
        if f is not None and f.get("wallet_copied"):
            copies += 1
        elif f is not None and sig in result:
            trades.append(round(result[sig], 6))
        else:
            paid = float(f["sol"]) if f is not None and f.get("sol") is not None else cost
            unmeasured.append({"sig": sig, "cost_sol": round(paid, 6)})
    return {"trades": trades, "unmeasured": unmeasured, "copies": copies}


def _outcome(t: dict, fills: int) -> tuple[str, str]:
    """("complete", why it ended) for a block that made all its buys, else ("early", why) or ("not started", why)."""
    if t.get("state") == "expired" or not t.get("started"):
        return "not started", str(t.get("error") or "it did not start")
    held = [str(m) for m in t.get("held_at_end") or []]
    rep = t.get("report") or {}
    reason = str(rep.get("reason") or "no reason known")
    if held:
        return "early", f"it ended with {', '.join(held)} held and nothing selling it (/sell sells it)"
    try:
        got: int | None = int(rep.get("fills"))
    except (TypeError, ValueError):
        got = None
    if got is None or got < fills or not reason.startswith("cap reached"):  # the cycler's words at its cap
        return "early", f"it ended after {'?' if got is None else got} of {fills} buys: {reason}"
    return "complete", reason


def _share(x: float) -> str:
    return "half of paper" if abs(float(x) - 0.5) < 1e-9 else f"{float(x):g} times paper"


# -- the words the owner reads ---------------------------------------------------------------------------------------


def _when(ts) -> str:
    try:
        return time.strftime("%d %b %H:%M UTC", time.gmtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return "an unknown time"


def plan_words(c: dict) -> str:
    """The plan a standing yes stands for, from its caps."""
    return (f"one test starts by itself at the next round: {c['buy_sol']:g} SOL a buy, at most {c['fills']} buys one coin "
            f"at a time, for at most {c['minutes']} minutes, stopping {c['loss_sol']:g} SOL below the wallet's start; "
            f"after its {c['fills']} trades it goes on in blocks of {c['fills']} (at most {c['more_blocks']} more) only "
            f"while live is at least {_share(c['continue_at'])}, the whole sequence never more than {c['loss_sol']:g} "
            "SOL below the first block's start")  # fmt: skip


def status_words(paths: OperatorPaths, now: float) -> str:
    """/test auto: the sequence running, or the switch and the plan it stands for."""
    at, seq = read_autotest(paths), load_seq(paths)
    if seq and seq.get("state") == "running":
        cur, caps = seq["blocks"][-1], seq.get("caps") or at
        return (f"Automatic live test running: {seq.get('label')} ({seq['strategy']}), block {cur['n']} of at most "
                f"{1 + int(caps['more_blocks'])}, floor {float(seq['floor']):.2f} SOL for the whole sequence; "
                f"{plan_words(caps)}. /test shows the block, /stop ends it, /test auto off starts no more.")  # fmt: skip
    if at["on"]:
        return (f"Automatic live test on since {_when(at['approved']['at'])} (your yes: \"{at['approved']['words']}\"). "
                f"When a hunt strategy passes on paper (100 forward trades where a live hunt fill lands, at the measured "
                f"cost of a trade, a lower bound above zero, also with each gain capped), {plan_words(at)}. /test auto "
                "off turns it off; /stop ends a test now.")  # fmt: skip
    last = f" The last one: {at['used']['result']}." if at.get("used") and at["used"].get("result") else ""
    return (f"Automatic live test off.{last} /test auto on turns it on, and that message is your yes for this plan: "
            f"{plan_words(dict(DEFAULTS))}.")  # fmt: skip


def command(args: list[str], *, cfg: Config, paths: OperatorPaths, notes: NoteSink, now: float,
            date: int | None = None) -> str:  # fmt: skip
    """/test auto (show it), /test auto on (the owner's own message is the yes for the 7 Nov plan, its values as they
    are: the CLI sets others with the owner's words), /test auto off. Nothing else is understood or repeated."""
    words = [w.lower() for w in args]
    if not words:
        return status_words(paths, now)
    if words == ["on"]:
        if read_autotest(paths)["on"]:
            return "The automatic live test is already on. " + status_words(paths, now)
        try:
            at = set_autotest(paths, True, words="/test auto on", at=date if date is not None else now, by="telegram",
                              now=now)  # fmt: skip
        except ValueError as exc:
            return f"Nothing changed: {exc}."
        notes.write(f"owner turned the automatic live test on (/test auto on): {plan_words(at)}", "decision")
        return "Automatic live test on. " + status_words(paths, now)
    if words == ["off"]:
        set_autotest(paths, False, by="telegram", now=now)
        notes.write("owner turned the automatic live test off (/test auto off)", "decision")
        return ("Automatic live test off: no test starts by itself any more, and a request it wrote that has not started "
                "never starts. A test running now runs to its end; /stop ends it now.")  # fmt: skip
    return "Nothing changed: send /test auto to see it, /test auto on or /test auto off."


def autotest_cli(cfg: Config, args, now: float | None = None) -> int:
    """`snipetech operate autotest [--on --approved "<the owner's words>" [caps]] [--off]`: the cloud session runs it with
    the owner's yes in chat, and records the owner's words. Prints where the file is and how it stands; a bad value, or
    a flag of it without `autotest`, changes nothing and exits with the reason."""
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
    values = {k: getattr(args, k) for k in FIELDS if getattr(args, k, None) is not None}
    if args.action != "autotest":
        raise SystemExit("--on, --off, --approved and the caps go with `operate autotest`")
    if args.autotest_on and args.autotest_off:
        raise SystemExit("operate autotest: --on or --off, not both")
    if (values or args.approved) and not args.autotest_on:
        raise SystemExit("operate autotest: --approved and the caps go with --on")
    now = time.time() if now is None else now
    try:
        if args.autotest_on:
            if not str(args.approved or "").strip():
                raise SystemExit('operate autotest --on needs --approved "<the owner\'s words, as they said them>"')
            set_autotest(paths, True, values=values, words=args.approved, by="cli", now=now)
        elif args.autotest_off:
            set_autotest(paths, False, by="cli", now=now)
    except ValueError as exc:
        raise SystemExit(f"operate autotest: not changed: {exc}") from None
    print(f"automatic test file {autotest_file(paths)} on this machine")
    print(status_words(paths, now))
    return 0


# -- the round's part ------------------------------------------------------------------------------------------------


@dataclass
class _Round:
    svc: Any
    cfg: Config
    paths: OperatorPaths
    now: float
    snap: Any
    notes: NoteSink
    notifier: Any
    log: Callable[[str], None] | None

    def say(self, text: str) -> None:
        """A decision note, the owner's Telegram line and the operator log, in the same words."""
        live_test._say(text, self.notes, self.notifier, self.log)

    def info(self, text: str) -> None:
        if self.log is not None:
            self.log(text)


async def step(svc, cfg: Config, paths: OperatorPaths, now: float, *, snap=None, notes: NoteSink | None = None,
               notifier=None, log: Callable[[str], None] | None = None) -> dict | None:  # fmt: skip
    """The automatic test's part of a round (live_test.round_step calls it between closing a test and starting a
    request): judge the running sequence's block once it ended and request the next one, or end the sequence; or, with
    no sequence running and the owner's yes on, start one. Returns the sequence as it stands, or None."""
    r = _Round(svc, cfg, paths, now, snap, notes or NoteSink(paths, now=lambda: now), notifier, log)
    seq = load_seq(paths)
    if seq is not None and seq.get("state") == "running":
        return await _advance(r, seq)
    at = read_autotest(paths)
    return await _begin(r, at) if at["on"] else None


async def _begin(r: _Round, at: dict) -> dict | None:
    """A new sequence for the best strategy that passes on paper, when every condition holds; else a log line and
    nothing (the next round looks again)."""

    def wait(why: str) -> None:
        r.info(f"automatic test waits: {why}")

    paths, now, store = r.paths, r.now, getattr(r.svc, "store", None)
    wallet = r.cfg.operator.wallet
    if paths.stop_flag.exists():
        return wait("your /stop is in force")
    if live_test.in_progress(paths, now):
        return wait("a live test is in progress")
    held_back, why = await risk_guard.tripped(r.svc, r.cfg, paths, now, snap=r.snap)  # cannot tell is never clear
    if held_back:
        return wait(f"the wallet-wide guard: {why}")
    if not wallet:
        return wait("the sniper wallet is not set")
    try:
        ok = live_test.eligible(store, r.cfg, now)
    except Exception as exc:  # noqa: BLE001 - the next round reads it again
        return wait(f"the paper test could not be read ({type(exc).__name__})")
    if not ok:
        return wait("no hunt strategy passes on paper yet")
    best = ok[0]
    size = float(best.get("buy_sol") or 0)
    if size > float(at["buy_sol"]) + 1e-9:  # the smaller size would be another size than it was scored at
        sig = f"automatic test: {best['id']} passes on paper at {size:g} SOL a buy"
        if not r.notes.has_today(sig, now):
            r.say(f"{sig}, above the {at['buy_sol']:g} SOL a buy of your standing yes, so no test starts by itself: a "
                  "test at another size than it was scored at says nothing about it (a round trip costs about 0.009 "
                  f"SOL whatever the size). /test {best['id']} go tests it at its own size, if you say yes to that "
                  "plan.")  # fmt: skip
        return wait(f"{best['id']} buys {size:g} SOL, above the standing yes's {at['buy_sol']:g}")
    eq = live_test.equity_now(r.snap, now, wallet)
    if eq is None:
        return wait("the wallet's balance is not known this round")
    try:
        p = live_test.plan(best, eq, wallet, fills=int(at["fills"]), minutes=int(at["minutes"]),
                           loss_sol=float(at["loss_sol"]))  # fmt: skip
    except ValueError as exc:
        return wait(f"{best['id']} cannot be tested live: {exc}")
    seq = {"id": f"seq-{int(now)}", "state": "running", "strategy": best["id"], "label": p["label"],
           "approved": at["approved"], "caps": {k: at[k] for k in FIELDS}, "buy_sol": p["buy_sol"], "floor": p["floor"],
           "start_equity": round(eq, 4), "started_at": int(now), "blocks": [{"n": 1, "requested_at": int(now)}]}  # fmt: skip
    req = live_test.request(p, best, r.cfg, now, by=BY, sequence={"id": seq["id"], "block": 1}, approved=at["approved"])
    before = load_seq(paths)
    save_seq(paths, seq)  # first: the round's own check asks that the request be the sequence's own
    why, _passes = await live_test._recheck(r.svc, r.cfg, paths, req, r.snap, now)
    if why is None and not live_test.write_request(paths, req, now):
        why = "another live test was requested a moment ago"
    if why:  # nothing was asked of OKTech: no sequence, the last one's record back
        if before is not None:
            save_seq(paths, before)
        else:
            sequence_path(paths).unlink(missing_ok=True)
        return wait(why)
    caps = seq["caps"]
    r.say(f"Automatic live test, on your standing yes of {_when(at['approved']['at'])} (\"{at['approved']['words']}\"): "
          f"{p['label']} ({best['id']}) passes on paper with {int(best['n']):,} forward trades where a live hunt fill "
          f"lands, at the measured cost of a trade, {float(best['mean']):+.4f} SOL a trade, lower bound "
          f"{float(best['ev_lower']):+.4f}, also with each gain capped. Block 1 of at most {1 + int(caps['more_blocks'])} starts now: {p['buy_sol']:g} SOL a buy, at "
          f"most {p['fills']} buys one coin at a time, {p['minutes']} minutes, floor {p['floor']:.2f} SOL "
          f"({float(caps['loss_sol']):g} SOL below the wallet's {eq:.2f}) for the whole sequence; it goes on only while "
          f"live is at least {_share(caps['continue_at'])}. /stop ends it; /test auto off starts no more.")  # fmt: skip
    return seq


async def _advance(r: _Round, seq: dict) -> dict:
    """The running sequence: nothing while its block runs; once the block ended, judge it, then request the next block
    or end the sequence."""
    cur = seq["blocks"][-1]
    t = live_test.find(r.paths, seq["strategy"], cur["requested_at"])
    if t is None:
        if len(seq["blocks"]) == 1:  # its request was never written (a round cut short): nothing was bought
            save_seq(r.paths, seq | {"state": "dropped", "ended_at": int(r.now)})
            r.info("automatic test: the first block's request was never written; the sequence is dropped")
            return seq
        return await _end(r, seq, f"block {cur['n']}'s record is missing")
    if live_test._active(t, r.now):
        return seq
    if cur.get("decision") != "continue":
        verdict = await _judge(r, seq, cur, t)
        if verdict is not None:
            return verdict
    return await _next_block(r, seq)


async def _judge(r: _Round, seq: dict, cur: dict, t: dict) -> dict | None:
    """The block that ended: the sequence ends (early, not started, below paper, all blocks done, the owner's switch,
    /stop or the guard), waits for the measure stage, or goes on (None: the next block is to be requested)."""
    caps, n = seq["caps"], cur["n"]
    kind, why = _outcome(t, int(caps["fills"]))
    store, wallet = r.svc.store, r.cfg.operator.wallet
    if kind == "not started":
        return await _end(r, seq, f"block {n} did not start: {why}")
    live = block_live(store, wallet, t, r.now)
    paper = float((t.get("forward") or {}).get("mean") or 0)
    seq["paper"] = paper
    if kind == "early":
        cur["live"] = {"n": len(live["trades"]), "sum": round(sum(live["trades"]), 6),
                       "unmeasured": len(live["unmeasured"]), "counted": False, "copies": live["copies"]}  # fmt: skip
        return await _end(r, seq, f"block {n} ended early: {why}")
    if live["unmeasured"] and r.now - float(t.get("ended_at") or r.now) < MEASURE_WAIT_SECS:
        r.info(f"automatic test: block {n} waits for the measure stage ({len(live['unmeasured'])} of its buys have no "
               "sell in the fills yet)")  # fmt: skip
        return seq
    values = list(live["trades"]) + [-u["cost_sol"] for u in live["unmeasured"]]
    cur["live"] = {"n": len(values), "sum": round(sum(values), 6), "unmeasured": len(live["unmeasured"]),
                   "counted": True, "copies": live["copies"]}  # fmt: skip
    total_n, total_sum = _tally(seq)
    if total_n == 0:
        return await _end(r, seq, f"block {n} had no live trade of the test's own to judge")
    mean = total_sum / total_n
    cont = float(caps["continue_at"])
    if not (paper > 0 and mean > 0 and mean >= cont * paper - 1e-9):
        return await _end(r, seq, f"live is below {_share(cont)} after block {n}: the analyst works out why")
    if len(seq["blocks"]) >= 1 + int(caps["more_blocks"]):
        return await _end(r, seq, f"all {len(seq['blocks'])} blocks done, live at least {_share(cont)} throughout")
    stop = _hard_stop(r, seq)
    if stop:
        return await _end(r, seq, stop)
    cur["decision"] = "continue"
    seq["waiting_since"] = None
    save_seq(r.paths, seq)
    r.say(f"Automatic live test, block {n} of {seq['label']} ({seq['strategy']}) made all its {caps['fills']} buys: live "
          f"{mean:+.4f} SOL a trade over the {total_n} trades of the sequence so far (the measure stage's fills, "
          f"{total_sum:+.3f} SOL{_unmeasured_words(seq)}), paper {paper:+.4f}: at least {_share(cont)}, so block "
          f"{n + 1} of at most {1 + int(caps['more_blocks'])} starts now, the same {seq['buy_sol']:g} SOL a buy, floor "
          f"{float(seq['floor']):.2f} SOL for the whole sequence.")  # fmt: skip
    return None


def _hard_stop(r: _Round, seq: dict) -> str | None:
    """What ends a sequence between blocks whatever else holds: the owner's switch off or changed, /stop, the guard."""
    at = read_autotest(r.paths)
    if not at["on"]:
        return "you turned the automatic test off"
    if at["approved"] != seq.get("approved"):
        return "your standing yes changed since the sequence started (a new yes starts a new sequence)"
    if r.paths.stop_flag.exists():
        return "your /stop"
    g = risk_guard.from_snapshot(getattr(r.svc, "store", None), r.cfg, r.paths, r.now, r.snap)
    if g.tripped:
        return f"the wallet-wide guard holds: {g.reason}"
    return None


async def _next_block(r: _Round, seq: dict) -> dict:
    """Request the next block of the same strategy, same caps, the first block's floor, once the round's start would
    take it; a refusal that may pass by itself waits up to CONTINUE_WAIT_SECS, any other ends the sequence."""
    stop = _hard_stop(r, seq)
    if stop:
        return await _end(r, seq, stop)
    store, wallet, sid, caps = r.svc.store, r.cfg.operator.wallet, seq["strategy"], seq["caps"]
    try:
        ok, why_not, _rows = live_test._survey(store, r.cfg, r.now)
    except Exception as exc:  # noqa: BLE001 - the next round reads it again
        return await _wait_next(r, seq, f"the paper test could not be read ({type(exc).__name__})")
    row = next((x for x in ok if x["id"] == sid), None)
    if row is None:
        return await _end(r, seq, f"{sid} no longer passes on paper: it {why_not.get(sid, 'is not frozen any more')}")
    if abs(float(row.get("buy_sol") or 0) - float(seq["buy_sol"])) > 1e-9:
        return await _end(r, seq, f"{sid}'s size changed since the first block")
    eq = live_test.equity_now(r.snap, r.now, wallet)
    if eq is None:
        return await _wait_next(r, seq, "the wallet's balance is not known this round")
    try:
        p = live_test.plan(row, eq, wallet, fills=int(caps["fills"]), minutes=int(caps["minutes"]),
                           loss_sol=float(caps["loss_sol"]), floor=float(seq["floor"]))  # fmt: skip
    except ValueError as exc:
        return await _end(r, seq, f"{sid} cannot be tested live any more: {exc}")
    need = live_test._need(p)
    if eq + 1e-9 < need:
        return await _end(r, seq, f"the wallet holds {eq:.2f} SOL: no room for another block above the sequence's "
                                  f"{p['floor']:.2f} SOL floor (it needs {need:.2f}: the floor, a buy and its fees)")  # fmt: skip
    if live_test.in_progress(r.paths, r.now):
        return await _wait_next(r, seq, "another live test is in progress")
    n = int(seq["blocks"][-1]["n"]) + 1
    req = live_test.request(
        p, row, r.cfg, r.now, by=BY, sequence={"id": seq["id"], "block": n}, approved=seq["approved"]
    )
    seq["blocks"].append({"n": n, "requested_at": int(r.now)})
    save_seq(r.paths, seq)
    why, _passes = await live_test._recheck(r.svc, r.cfg, r.paths, req, r.snap, r.now)
    if why is None and not live_test.write_request(r.paths, req, r.now):
        why = "another live test was requested a moment ago"
    if why:
        seq["blocks"].pop()
        return await _wait_next(r, seq, why)
    seq["waiting_since"] = None
    save_seq(r.paths, seq)
    return seq


async def _wait_next(r: _Round, seq: dict, why: str) -> dict:
    since = float(seq.get("waiting_since") or r.now)
    seq["waiting_since"] = int(since)
    if r.now - since >= CONTINUE_WAIT_SECS:
        return await _end(r, seq, f"the next block could not start within {CONTINUE_WAIT_SECS // 60} minutes: {why}")
    save_seq(r.paths, seq)
    r.info(f"automatic test: the next block waits: {why}")
    return seq


def _tally(seq: dict) -> tuple[int, float]:
    """(trades, SOL) over every block judged so far."""
    lives = [b["live"] for b in seq["blocks"] if isinstance(b.get("live"), dict)]
    return sum(int(x["n"]) for x in lives), round(sum(float(x["sum"]) for x in lives), 6)


def _unmeasured_words(seq: dict) -> str:
    lives = [b["live"] for b in seq["blocks"] if isinstance(b.get("live"), dict)]
    counted = sum(int(x.get("unmeasured") or 0) for x in lives if x.get("counted"))
    later = sum(int(x.get("unmeasured") or 0) for x in lives if not x.get("counted"))
    out = f"; {counted} not measured, counted at their whole cost" if counted else ""
    return out + (f"; {later} more not measured yet" if later else "")


def _summary(seq: dict) -> str:
    n, total = _tally(seq)
    lt = (f"{n} live trades, {total / n:+.4f} SOL a trade (the measure stage's fills, {total:+.3f} SOL in all"
          f"{_unmeasured_words(seq)})" if n else f"no live trade judged{_unmeasured_words(seq)}")  # fmt: skip
    paper = seq.get("paper")
    return f"{len(seq['blocks'])} block(s), {lt}" + (
        f"; paper {float(paper):+.4f} a trade" if paper is not None else ""
    )


async def _end(r: _Round, seq: dict, why: str) -> dict:
    """The sequence's end: its record, one line in tests.jsonl, the owner's switch off (used, with the result), and the
    owner's one summary."""
    seq.update({"state": "ended", "ended_at": int(r.now), "why": why})
    n, total = _tally(seq)
    summary = _summary(seq)
    save_seq(r.paths, seq)
    live_test._history(r.paths, {"kind": "sequence", "id": seq["id"], "strategy": seq["strategy"],
                                 "label": seq.get("label"), "approved": seq.get("approved"), "caps": seq.get("caps"),
                                 "floor": seq.get("floor"), "blocks": seq["blocks"], "trades": n, "live_sol": total,
                                 "live_mean": round(total / n, 6) if n else None, "paper_mean": seq.get("paper"),
                                 "why": why, "started_at": seq.get("started_at"), "ended_at": int(r.now)})  # fmt: skip
    _turn_off_used(r.paths, seq, f"{why}; {summary}", r.now)
    r.say(f"Automatic live test ended: {why}. {seq.get('label')} ({seq['strategy']}): {summary}. The automatic test is "
          "now off (used); a new one needs your yes again: /test auto on.")  # fmt: skip
    return seq
