"""The owner's standing yes for live trading that starts by itself and never stops: operator/AUTOTEST.

The owner's yes (7 Nov 09:15 UTC, docs/live-recipe.md, "Yes, use this plan"): "When a strategy passes on paper (100+ new
trades at the real landing, positive with outliers capped, at real costs), one live test starts straight away: sniper
wallet 6VFrZ15V..., 0.2 SOL a buy, one coin at a time, at most 10 buys, at most 2 hours, stopping on its own 1 SOL below
the starting balance. After 10 trades it continues only if live is at least half of paper". Widened at about 12:10 UTC,
word for word: "ngl ill just keep it at around 5-8 sol that's enough no?" and "Just make it so live trading never
stops" (docs/live-recipe.md, "The owner's yes widened"). Wallet and size are unchanged.

How it runs. Each operator round, in live_test.round_step's order (after a test that ended is closed, before a request
is started): while AUTOTEST is on and no strategy runs, the round writes the /test go request itself (by "autotest") for
the best strategy live_test.eligible offers that this test has not dropped, at no more than the yes's size; live_test's
own start path creates its hunt sniper and runs its cycler in the same round. A strategy then trades block after block,
each of at most `fills` buys and `minutes` minutes, one coin at a time, at its own size (a round trip costs about 0.009
SOL whatever the size, so a test at another size says nothing), with no limit on blocks or hours, and every block at the
first block's floor (the wallet's start less `loss_sol`). After each block, once the strategy has `fills` live round
trips in all (a block that made all its buys is judged at once, as on 09:15), it is judged on the measure stage's fills:
our own round trips only (a deposit or a withdrawal moves nothing; 6 Nov 15:16 the owner moved 2.54 SOL out mid-test),
with the failed sends, read twice and the worse reading judged (block_live: the fills cannot tell the book's copies
from the test's own buys for sure). Live above zero and at least `continue_at` times the forward paper mean: the next
block. Below that, or at its floor (1 SOL down live, a block that ended on the floor, or no room above it for another
buy), the strategy is dropped for good (operator/autotest.json keeps why, with the numbers) and the next one that passes
on paper starts at once with a floor of its own; with none, the round looks again every 15 minutes. It never trades a
strategy without paper evidence: at our landing every Hunt setting loses about 0.007 SOL a trade (7 Nov 08:57).

What holds it. An execution fault never drops a strategy, since it says nothing about the strategy: a cycler that died,
OKTech unreachable or erroring, a coin OKTech left unsold, the wallet unreadable, a measure stage stalled past
MEASURE_WAIT_SECS. It holds new blocks until the fault clears (re-checked each round) and the same strategy goes on; the
owner hears it once when it starts holding and once when it clears. A wallet without room for a buy holds the same way
(said once a day): 7 Nov 10:59 the wallet was emptied. The owner's /stop holds every start while its flag exists (a
running block ends at once, its sniper stopped once the wallet holds nothing; a sniper holding a coin keeps running,
CLAUDE.md rule 1), and /resume lets the same strategy go on if it still passes on paper. The owner's guard, while on and
tripped, holds it too (its own line tells the owner). Nothing else says a line: never one per block or per round; the
day's blocks are in the daily digest. It never turns itself off: only /test auto off or `operate autotest --off` does
(a running block then runs to its end).

The yes is for one sniper wallet (rule 1: a yes is for a wallet, a size, a duration): AUTOTEST records the wallet and
the OKTech wallet it names, and nothing starts by itself, and no strategy goes on, while the config names another.

Only the owner turns it on: `snipetech operate autotest --on --approved "<the owner's words>" --wallet <the wallet they
named>` (the cloud session, with the owner's yes in chat) or /test auto on in Telegram (that message is the yes, for the
wallet /test auto shows); /test auto off or `operate autotest --off` turns it off, and a request it wrote that has not
started is then never started. The agents cannot: their start_job refuses `operate`, and none of their tools writes a
file without an extension in operator/, or a .json other than candidates.json."""

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
from ..solana.base58 import is_pubkey
from . import live_test, risk_guard
from .paths import OperatorPaths
from .tools import NoteSink

BY = "autotest"  # the requests it writes say who wrote them, as /test go's say "telegram"
# the owner's widened yes (7 Nov ~12:10 UTC): live trading never stops. Every yes written since says so; a switch turned
# on under the 09:15 plan (no plan in it) runs this one too, from its next strategy on: it is the owner's latest word
PLAN = "never-stops"
# the plan's numbers, unchanged since 09:15: 0.2 SOL a buy, blocks of 10 buys and 2 hours, a strategy at most 1 SOL below
# its first block's start, live at least half of paper. 09:15 also capped the blocks at 3 more: the widened yes has no
# limit on blocks or hours
DEFAULTS: dict[str, float | int] = {"buy_sol": 0.2, "fills": 10, "minutes": 120, "loss_sol": 1.0, "continue_at": 0.5}
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
        "a strategy is dropped more than 0 and at most 2 SOL below its first block's start",
    ),
    # never looser than the owner's "at least half of paper"
    "continue_at": (lambda v: 0.5 <= v <= 1, "live must be at least half of paper (0.5 to 1 times it)"),
}
FIELDS = tuple(RULES)
WHOLE = ("fills", "minutes")
WORDS_MAX = 300  # the owner's words kept in the file
# after a block closed, how long its judgement waits for the measure stage to record its round trips (it runs every 5
# minutes, or every 15 as the round's stand-in); past it a buy with no sell recorded counts as its whole cost, and a
# stage that has not run past the block at all is a fault that holds the strategy
MEASURE_WAIT_SECS = 45 * 60
# the measure stage reads the tape: a block is judged once it has run this long after the block's last trade (the tape
# is never more than 2 minutes behind while a test runs: live_test._tape_lag), so its last failed sends are in the fills
MEASURE_SETTLE_SECS = 120
MEASURE_KEY = "measure:last_ts"
GRACE_SECS = 60  # our clock against block time, at the start of a block's window
RENT_SOL = (
    0.00204  # the token account a buy opens (2,039,280 lamports): a wallet needs it on top of the buy and its fees
)
# why a next block cannot start (live_test._check's kinds): an execution fault holds the strategy and the owner hears it
# once when it starts and once when it clears; a refusal of these kinds ends the strategy's run without a drop (the
# config names another wallet, it no longer passes on paper, its size changed); any other waits, said in the log only
# (the owner's /stop, the guard, which tells the owner itself, the recorder catching up, the book's coin being sold)
FAULTS = frozenset({"job", "oktech", "sniper", "balance", "coins", "guard-unknown"})
ENDS = frozenset({"wallet", "paper", "size"})
CLOSING_FAULTS = ("oktech", "stop", "wallet")  # a block still closing on these: its sniper may still be armed
# a cycler that ended without its report, refusing at its start (oktech_cycle's own words): what that end is
REFUSED_AT_START = (("at or below the floor", "floor"), ("/stop flag is set", "stop"), ("wallet-wide guard", "guard"),
                    ("behind", "early"), ("the wallet holds", "early"))  # fmt: skip


def autotest_file(paths: OperatorPaths) -> Path:
    return paths.root / "AUTOTEST"


def oktech_wallet_of(cfg: Config) -> str:
    """The OKTech wallet a test of this config buys from (live_test.request writes the same into the request)."""
    return cfg.oktech.wallet or live_test.DEFAULT_OKTECH_WALLET


def _wallets(data: dict) -> tuple[str, str] | None:
    """(the sniper wallet, the OKTech wallet) a yes on file is for, or None when it names no usable pair."""
    w, ok = data.get("wallet"), data.get("oktech_wallet")
    if isinstance(w, str) and is_pubkey(w) and isinstance(ok, str) and ok.strip() and len(ok) <= 64:
        return w, ok.strip()
    return None


def wallet_mismatch(cfg: Config, yes: dict) -> str | None:
    """Why the config's wallets are not the ones a yes (AUTOTEST, or a sequence's record) is for; None when they are."""
    want_w, want_ok = yes.get("wallet"), yes.get("oktech_wallet")
    have_w, have_ok = cfg.operator.wallet or "", oktech_wallet_of(cfg)
    if have_w != want_w:
        return (f"your standing yes is for sniper wallet {str(want_w)[:6]}…, and the config names another wallet, "
                f"{have_w[:6] or 'none'}…")  # fmt: skip
    if have_ok != want_ok:
        return f"your standing yes is for OKTech wallet {want_ok}, and the config names OKTech wallet {have_ok}"
    return None


def sequence_path(paths: OperatorPaths) -> Path:
    """The strategy running now, or the last one, with every strategy dropped so far: written by the round only."""
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
    """The owner (/test auto in the one-minute pass, the CLI) writes AUTOTEST: every read-and-write is done under this
    lock."""
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
    file says so and carries the owner's words and the wallet they are for; `approved` (the owner's words and when),
    `wallet` and `oktech_wallet` (what the yes is for), `plan` (PLAN, or None for a yes of the 09:15 plan, run as PLAN
    all the same), `used` (what the 09:15 plan wrote at a sequence's end). Never raises: no file, or one that cannot be
    read, is off."""
    data = _raw(paths)
    out: dict[str, Any] = dict(DEFAULTS)
    for name in FIELDS:
        v = _sane(name, data.get(name))
        if v is not None:
            out[name] = v
    approved = _approval(data)
    wallets = _wallets(data)
    used = data.get("used") if isinstance(data.get("used"), dict) else None
    plan = data.get("plan") if data.get("plan") == PLAN else None
    return out | {"on": data.get("on") is True and approved is not None and wallets is not None, "approved": approved,
                  "wallet": wallets[0] if wallets else None, "oktech_wallet": wallets[1] if wallets else None,
                  "ts": data.get("ts"), "by": data.get("by"), "used": used, "plan": plan}  # fmt: skip


def _write(paths: OperatorPaths, rec: dict) -> None:
    paths.ensure()
    f = autotest_file(paths)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec))
    os.replace(tmp, f)


def set_autotest(paths: OperatorPaths, on: bool, *, values: dict | None = None, words: str | None = None,
                 at: float | None = None, by: str, now: float | None = None, wallet: str | None = None,
                 oktech_wallet: str | None = None) -> dict[str, Any]:  # fmt: skip
    """Turn the owner's standing yes on for the widened plan (the caps given over the owner's 7 Nov values, the owner's
    `words` and when they said them, the sniper `wallet` and the `oktech_wallet` the yes is for) or off (the file keeps
    its caps, words and wallets for the record). Every value is checked before anything is written: an unknown name, a
    cap out of its bounds, or on without the owner's words or the wallet raises ValueError in plain words and changes
    nothing. A strategy running when the owner says yes again goes on with its floor: the latest yes is the one that
    counts for its next blocks."""
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
            pair = _wallets({"wallet": wallet, "oktech_wallet": oktech_wallet})
            if pair is None:
                raise ValueError("turning it on needs the sniper wallet and the OKTech wallet the owner's yes is for")
            rec = {"on": True, "plan": PLAN, **caps, "approved": {"words": said, "at": int(now if at is None else at)},
                   "wallet": pair[0], "oktech_wallet": pair[1], "ts": int(now), "by": by}  # fmt: skip
        else:
            if values:
                raise ValueError("the caps go with turning it on")
            cur = _raw(paths)
            keep = (*FIELDS, "plan", "approved", "used", "wallet", "oktech_wallet")
            rec = {k: cur[k] for k in keep if k in cur} | {"on": False, "ts": int(now), "by": by}
        _write(paths, rec)
    return read_autotest(paths)


def _label(seq: dict) -> str:
    return f"{seq.get('label') or seq.get('strategy')} ({seq.get('strategy')})"


def on_stop(paths: OperatorPaths, notes: NoteSink, now: float) -> str | None:
    """The owner's /stop, after its flag is written (commands._stop): it holds every start while the flag exists. The
    block running now ends (its cycler reads the flag every few seconds and stops its sniper once the wallet holds
    nothing; a sniper holding a coin keeps running until it is sold, CLAUDE.md rule 1) and no block starts; the switch
    stays on and the strategy is not dropped, so /resume lets it go on. A line for the reply, or None when there is no
    automatic test to speak of."""
    seq = load_seq(paths)
    if seq and seq.get("state") == "running":
        notes.write(f"owner's /stop holds the automatic live test ({_label(seq)}): no block starts until /resume",
                    "decision")  # fmt: skip
        return (f"The automatic live test holds while /stop is set: the block running now ends and its sniper is stopped "
                "once the wallet holds nothing (a sniper holding a coin keeps running until the coin is sold), and no "
                f"block starts. /resume lets it go on with {_label(seq)} if it still passes on paper; /test auto off "
                "turns it off.")  # fmt: skip
    if read_autotest(paths)["on"]:
        return ("The automatic live test starts nothing while /stop holds; after /resume it may start one (/test "
                "auto off turns it off).")  # fmt: skip
    return None


def on_resume(paths: OperatorPaths) -> str | None:
    """The owner's /resume: a line for the reply on what the automatic test does next, or None when it is off."""
    seq = load_seq(paths)
    if seq and seq.get("state") == "running":
        return (f"The automatic live test goes on with {_label(seq)} at the next round, if it still passes on paper "
                "(the same floor as before).")  # fmt: skip
    if read_autotest(paths)["on"]:
        return "The automatic live test may start the best strategy that passes on paper at the next round."
    return None


def is_on(paths: OperatorPaths) -> bool:
    """A live test may start by itself: the switch is on, or a strategy is running."""
    seq = load_seq(paths)
    return read_autotest(paths)["on"] or bool(seq and seq.get("state") == "running")


# -- the record: the strategy running and the ones dropped ------------------------------------------------------------


def load_seq(paths: OperatorPaths) -> dict | None:
    try:
        d = json.loads(sequence_path(paths).read_text())
    except (OSError, ValueError):
        return None
    ok = ("running", "ended", "dropped", "void")
    return d if isinstance(d, dict) and d.get("id") and d.get("state") in ok else None


def save_seq(paths: OperatorPaths, seq: dict) -> None:
    paths.ensure()
    p = sequence_path(paths)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(seq, indent=1, default=str))
    os.replace(tmp, p)


def dropped(paths: OperatorPaths) -> list[dict]:
    """Every strategy the automatic test dropped, oldest first: the record's list, and the drops tests.jsonl keeps (it
    is only ever appended to), so a record that cannot be read never lets a dropped strategy start again."""
    seen: dict[str, dict] = {}
    for d in (load_seq(paths) or {}).get("dropped") or []:
        if isinstance(d, dict) and d.get("strategy"):
            seen.setdefault(str(d["strategy"]), d)
    try:
        lines = live_test.history_path(paths).read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            h = json.loads(line)
        except ValueError:
            continue
        if isinstance(h, dict) and h.get("kind") == "sequence" and h.get("dropped") is True and h.get("strategy"):
            seen.setdefault(str(h["strategy"]), {"strategy": h["strategy"], "label": h.get("label"),
                                                 "why": h.get("why"), "at": h.get("ended_at")})  # fmt: skip
    return sorted(seen.values(), key=lambda d: float(d.get("at") or 0))


def holds(paths: OperatorPaths, t: dict, cfg: Config) -> str | None:
    """Why an automatic request may not start any more (None: it may): the owner's switch is still on, for the wallets
    the request and the config name, its caps still cover the request, and the strategy running names it as its
    newest block, at its floor (live_test._recheck asks). There is no limit on blocks."""
    at = read_autotest(paths)
    if not at["on"]:
        return "the automatic test was turned off since the request"
    if t.get("wallet") != at["wallet"]:
        return f"the request's sniper wallet is not the one your standing yes is for ({str(at['wallet'])[:6]}…)"
    if t.get("oktech_wallet") != at["oktech_wallet"]:
        return f"the request's OKTech wallet is not the one your standing yes is for ({at['oktech_wallet']})"
    mismatch = wallet_mismatch(cfg, at)
    if mismatch:
        return mismatch
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
            or blocks[-1].get("requested_at") != t.get("requested_at")
            or (seq.get("wallet"), seq.get("oktech_wallet")) != (at["wallet"], at["oktech_wallet"])):  # fmt: skip
        return "the request is not the automatic sequence's own"
    try:
        moved = abs(float(t["floor"]) - float(seq["floor"])) > 1e-9  # every block keeps the first block's floor
    except (KeyError, TypeError, ValueError):
        moved = True
    if moved:
        return "the request's floor is not the sequence's"
    return None


# -- a block's live result ---------------------------------------------------------------------------------------------


def _window(t: dict, now: float) -> tuple[int, int, int]:
    """(the block's first second, the second its cycler ended, the second by which every trade of it had landed). The
    cycler's real end comes from its job record (finish_ended keeps it in the report); a cycler that ended with nothing
    held had sold everything by then, else the test's close is when the wallet held nothing."""
    rep = t.get("report") or {}
    start = int(float(t.get("started_at") or now)) - GRACE_SECS
    try:
        job_end: int | None = int(float(rep["job_ended"]))
    except (KeyError, TypeError, ValueError):
        job_end = None
    closed = int(float(t.get("ended_at") or now))
    end = job_end if job_end is not None else int(float(t.get("cycler_ended_at") or closed))
    settled = job_end if job_end is not None and rep.get("held") == [] else closed
    return start, min(end, closed), min(settled, closed)


def block_live(store, wallet: str, t: dict, now: float, *, per_tx_sol: float, failed_fee_sol: float) -> dict[str, Any]:
    """A block's live result from the measure stage's fills, read two ways, for the worse of the two to be judged:
    `own`, the buys the fills do not mark as copying a leader, and `all`, every buy of the wallet while the cycler ran.
    The book trades from the same wallet, and the fills name a copied leader on any buy of ours in a coin one of the
    book's leaders bought earlier, with no time bound and no word of which sniper bought (a hunt buy right behind a
    launch-sniping leader reads as a copy), so neither reading alone can be trusted to be the test's own.

    Each buy's result is that of the sales that closed it (the measure stage's own result for each sale, real fees and
    its failed duplicate sends included), shared out first in first out by tokens as the measure stage pairs them. A buy
    is `measured` once its sales cover its tokens (less 1 % of dust): with a scale-out exit the first sale closes only
    part of it. Each reading also pays the block's failed sends that are no sale's twin (OKTech's buys that fail on
    chain anyway, about 0.001 SOL each: paper does not model them; a twin is already in its sale's result): every one
    for `all`, all but those in the coins of the copies for `own`. Only our own trades are read: a deposit or a
    withdrawal is in none of it."""
    from ..measure.classify import _fill_fee_sol, pair_round_trips
    from ..measure.run import BASE_FEE_LAMPORTS, _sells_a_coin, twin_sells

    start, end, settled = _window(t, now)
    tape: dict[str, dict] = {}
    for tr in store.trades_for_user(wallet, start):
        if tr.is_buy and tr.ts <= end:
            tape.setdefault(tr.sig, {"mint": tr.mint, "cost": (int(tr.quote) + int(tr.fee)) / 1e9 + per_tx_sol})
    rows = store.fills(wallet=wallet, since_ts=start, limit=100_000)
    buys = {f["sig"]: f for f in rows if f.get("side") == "buy"}
    sells = [f for f in rows if f.get("side") == "sell"]
    usable = [b | {"sol": float(b.get("sol") or 0), "tokens": float(b["tokens"])} for b in buys.values()
              if b.get("mint") and float(b.get("tokens") or 0) > 0]  # fmt: skip
    paired = [x | {"sol": float(x.get("sol") or 0), "tokens": float(x.get("tokens") or 0)} for x in sells
              if x.get("mint") and float(x.get("tokens") or 0) > 0 and x.get("pnl_sol") is not None]  # fmt: skip
    trips = pair_round_trips(usable, paired, per_tx_sol)["trips"]
    closed_by_sell: dict[str, float] = {}
    for tp in trips:
        closed_by_sell[tp["sell_sig"]] = closed_by_sell.get(tp["sell_sig"], 0.0) + float(tp["tokens"])
    result_of = {x["sig"]: float(x["pnl_sol"]) - float(x.get("twin_fee_sol") or 0.0) for x in paired}
    closed: dict[str, float] = {}
    result: dict[str, float] = {}
    for tp in trips:  # each sale's own result, shared by tokens among the buys it closed
        share = float(tp["tokens"]) / closed_by_sell[tp["sell_sig"]] if closed_by_sell[tp["sell_sig"]] > 0 else 0.0
        closed[tp["buy_sig"]] = closed.get(tp["buy_sig"], 0.0) + float(tp["tokens"])
        result[tp["buy_sig"]] = result.get(tp["buy_sig"], 0.0) + result_of[tp["sell_sig"]] * share
    out_buys: list[dict] = []
    for sig, tb in tape.items():
        f = buys.get(sig)
        bought = float((f or {}).get("tokens") or 0)
        if f is not None and bought > 0:
            fee = _fill_fee_sol(f)
            cost = float(f.get("sol") or 0) + (per_tx_sol if fee is None else fee)
            got = min(closed.get(sig, 0.0), bought)
            left_cost = cost * (bought - got) / bought
            measured = got >= 0.99 * bought
        else:  # not in the fills yet: what the tape says it cost
            cost, left_cost, measured, got = tb["cost"], tb["cost"], False, 0.0
        out_buys.append({"sig": sig, "mint": tb["mint"], "copy": bool((f or {}).get("wallet_copied")),
                         "result": round(result.get(sig, 0.0), 6), "measured": measured,
                         "left_cost": round(0.0 if measured else left_cost, 6)})  # fmt: skip
    own_mints = {b["mint"] for b in out_buys if not b["copy"]}
    copy_mints = {b["mint"] for b in out_buys if b["copy"]}
    failed = [f for f in store.fills(wallet=wallet, since_ts=start, side="failed", limit=100_000)
              if float(f.get("ts") or 0) <= end
              or (f.get("mint") in own_mints and float(f.get("ts") or 0) <= settled)]  # fmt: skip
    metas = store.tx_meta([f["sig"] for f in failed]) if failed else {}
    twins = twin_sells([f | {"is_sell": _sells_a_coin(metas.get(f["sig"]))} for f in failed], sells)
    charged = []
    for f in failed:
        if f["sig"] in twins:
            continue
        prio = f.get("priority_lamports")
        fee = (int(prio) + BASE_FEE_LAMPORTS) / 1e9 if prio is not None else failed_fee_sol
        charged.append({"sig": f["sig"], "mint": f.get("mint"), "fee": fee})
    return {"buys": out_buys, "failed": charged, "own_mints": sorted(own_mints), "copy_mints": sorted(copy_mints),
            "settled": settled, "copies": sum(1 for b in out_buys if b["copy"])}  # fmt: skip


def _reading(live: dict, own: bool, *, final: bool) -> dict[str, Any]:
    """One reading of a block (see block_live): its trades, their sum with the failed sends it pays, the buys not
    measured yet. `final`: a buy not measured counts what is known of it and its unsold part at its whole cost."""
    buys = [b for b in live["buys"] if not (own and b["copy"])]
    copy_mints = set(live["copy_mints"]) if own else set()
    failed = [f for f in live["failed"] if not (f.get("mint") in copy_mints)]
    total = sum(b["result"] - (b["left_cost"] if final else 0.0) for b in buys)
    total -= sum(f["fee"] for f in failed)
    return {
        "n": len(buys),
        "sum": round(total, 6),
        "unmeasured": sum(1 for b in buys if not b["measured"]),
        "failed": len(failed),
        "failed_sol": round(sum(f["fee"] for f in failed), 6),
    }


def end_kind(t: dict, fills: int, own_mints) -> tuple[str, str]:
    """How a block ended, and the cycler's words for it: "not started"; "coin" (a coin it bought itself is held and
    nothing sells it: the book trades from the same wallet, and its sniper sells its own coin); "complete" (all its
    `fills` buys); "floor" (the cycler's emergency end under the floor, or a start refused there: the strategy is at its
    floor); "time" (its time limit), "stop" (the owner's /stop) or "guard" (the owner's guard): normal ends, its trades
    count; "died" (the cycler ended without its report: killed, or a crash: an execution fault); "early" (any other end,
    counted like a time limit)."""
    if t.get("state") == "expired" or not t.get("started"):
        return "not started", str(t.get("error") or "it did not start")
    held = [str(m) for m in t.get("held_at_end") or [] if str(m) in set(own_mints or ())]
    rep = t.get("report") or {}
    reason = str(rep.get("reason") or "no reason known")
    if held:
        return "coin", f"ended with {', '.join(held)} held and nothing selling it"
    try:
        got: int | None = int(rep.get("fills"))
    except (TypeError, ValueError):
        got = None
    if reason.startswith("cap reached") and got is not None and got >= fills:  # the cycler's words at its cap
        return "complete", reason
    if reason.startswith("EMERGENCY"):
        return "floor", reason
    if reason.startswith("time limit"):
        return "time", reason
    if "/stop" in reason:
        return "stop", reason
    if "wallet-wide guard" in reason:
        return "guard", reason
    if "without its report" in reason or "job record is missing" in reason:
        return next((kind for words, kind in REFUSED_AT_START if words in reason), "died"), reason
    return "early", reason


def _share(x: float) -> str:
    return "half of paper" if abs(float(x) - 0.5) < 1e-9 else f"{float(x):g} times paper"


# -- the words the owner reads ---------------------------------------------------------------------------------------


def _when(ts) -> str:
    try:
        return time.strftime("%d %b %H:%M UTC", time.gmtime(float(ts)))
    except (TypeError, ValueError, OverflowError):
        return "an unknown time"


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def plan_words(c: dict) -> str:
    """The plan a standing yes stands for, from its caps and the wallets it is for (the owner's widened yes)."""
    where = (f"on sniper wallet {c['wallet'][:6]}… (OKTech wallet {c.get('oktech_wallet')}), " if c.get("wallet")
             else "on no wallet yet (the config names no sniper wallet), ")  # fmt: skip
    return (f"live trading by itself {where}at the strategy's own size, {c['buy_sol']:g} SOL a buy at most, in blocks "
            f"of at most {c['fills']} buys one coin at a time and {c['minutes']} minutes, with no limit on blocks or "
            f"hours, every block at the first block's floor ({c['loss_sol']:g} SOL below the wallet's start). Once a "
            f"strategy has {c['fills']} live trades it is judged after every block: it goes on while live is above zero "
            f"and at least {_share(c['continue_at'])}, and is dropped for good below that or at its floor, when the "
            "next strategy that passes on paper takes over. A fault (OKTech, the wallet, the measure stage, a coin "
            "left unsold) holds it until it clears, never a drop. It never turns itself off")  # fmt: skip


def _yes_words(at: dict) -> str:
    a = at.get("approved") or {}
    if at.get("plan") == PLAN:
        return f"your yes of {_when(a.get('at'))}: live trading never stops"
    return f"your yes of {_when(a.get('at'))}, run as your widened plan of about 12:10 UTC: live trading never stops"


def _dropped_words(gone: list[dict]) -> str:
    return "; ".join(f"{d['strategy']} ({str(d.get('why') or 'dropped')[:80]})" for d in gone[-5:])


def _live_now(seq: dict) -> str:
    """The strategy's live result so far against paper, in a few words."""
    tl = _tally(seq)
    paper = seq.get("paper")
    vs = f" vs paper {float(paper):+.4f}" if paper is not None else ""
    if not tl["n"]:
        return f"no live round trip counted yet{vs.replace(' vs', ';')}"
    return f"{tl['n']} live round trips, live {tl['sum'] / tl['n']:+.4f}{vs} a trade"


def _current(seq: dict, paths: OperatorPaths, now: float) -> str:
    """Which block is where: running, closing, requested, or what the next one waits for."""
    blocks = seq.get("blocks") or []
    if not blocks:
        return "its next block not requested yet"
    cur = blocks[-1]
    t = live_test.find(paths, seq["strategy"], cur.get("requested_at"))
    state = (t or {}).get("state")
    if live_test._active(t, now):
        return f"block {cur['n']} {state}"
    return f"block {cur['n']} ended"


def status_line(paths: OperatorPaths, now: float) -> str:
    """The automatic test in one line for /status: on or off, the plan, the strategy running, its blocks and live round
    trips, live against paper, what holds it, and the strategies dropped."""
    at, seq, gone = read_autotest(paths), load_seq(paths), dropped(paths)
    drops = f"; dropped: {_dropped_words(gone)}" if gone else ""
    on = "on" if at["on"] else "off"
    if seq and seq.get("state") == "running":
        hold, waiting = seq.get("hold"), seq.get("waiting")
        why = f"; holds: {hold['why']}" if hold else (f"; waits: {waiting['why']}" if waiting else "")
        n = len(seq.get("blocks") or [])
        return (f"automatic test: {on} ({_yes_words(at)}): {_label(seq)}, {_current(seq, paths, now)}, {n} block"
                f"{'' if n == 1 else 's'} so far, {_live_now(seq)}, floor {float(seq['floor']):.2f} SOL{why}{drops}")  # fmt: skip
    if at["on"]:
        return (f"automatic test: on ({_yes_words(at)}): no strategy running (it starts the best one that passes on "
                f"paper){drops}")  # fmt: skip
    return f"automatic test: off{drops}"


def digest_line(paths: OperatorPaths, now: float) -> str | None:
    """The automatic test's day for the daily digest: its blocks of the last 24 hours by strategy, the strategy running
    with its live result against paper and what holds it, and the strategies dropped that day; None when it is off and
    ran no block."""
    since = now - 86400
    blocks: dict[str, int] = {}
    try:
        lines = live_test.history_path(paths).read_text().splitlines()
    except OSError:
        lines = []
    tests = []
    for line in lines:
        try:
            h = json.loads(line)
        except ValueError:
            continue
        if isinstance(h, dict) and h.get("by") == BY and h.get("state") and h.get("kind") != "sequence":
            tests.append(h)
    cur = live_test.load(paths)
    if cur and cur.get("by") == BY:
        tests.append(cur)
    seen: set[tuple] = set()
    for t in tests:
        key = (t.get("id"), t.get("requested_at"))
        if key in seen or not (t.get("started") or t.get("job_id")):
            continue
        seen.add(key)
        if float(t.get("started_at") or t.get("ended_at") or 0) >= since:
            blocks[str(t.get("id"))] = blocks.get(str(t.get("id")), 0) + 1
    at, seq = read_autotest(paths), load_seq(paths)
    gone = [d for d in dropped(paths) if float(d.get("at") or 0) >= since]
    if not blocks and not at["on"] and not gone:
        return None
    n = sum(blocks.values())
    head = (f"automatic test: {n} block{'' if n == 1 else 's'} in 24 h ("
            + ", ".join(f"{sid} {k}" for sid, k in blocks.items()) + ")") if n else "automatic test: no block in 24 h"  # fmt: skip
    now_words = ""
    if seq and seq.get("state") == "running":
        hold = seq.get("hold")
        now_words = f"; now {_label(seq)}: {_live_now(seq)}" + (f"; holds: {hold['why']}" if hold else "")
    elif at["on"]:
        now_words = "; no strategy running (it waits for one that passes on paper)"
    else:
        now_words = "; off"
    return head + now_words + (f"; dropped: {_dropped_words(gone)}" if gone else "")


def status_words(paths: OperatorPaths, now: float, cfg: Config) -> str:
    """/test auto: the strategy running, or the switch and the plan it stands for (off: the plan /test auto on says yes
    to, for the wallets the config names now), and the strategies dropped."""
    at, seq, gone = read_autotest(paths), load_seq(paths), dropped(paths)
    drops = f" Dropped for good: {_dropped_words(gone)}." if gone else ""
    if seq and seq.get("state") == "running":
        hold, waiting = seq.get("hold"), seq.get("waiting")
        why = f" It holds: {hold['why']}." if hold else (f" Its next block waits: {waiting['why']}." if waiting else "")
        caps = at | {k: seq.get(k) for k in ("wallet", "oktech_wallet")}
        return (f"Automatic live test running ({'on' if at['on'] else 'off: the running block is the last'}): "
                f"{_label(seq)}, {_current(seq, paths, now)}, floor {float(seq['floor']):.2f} SOL for the strategy; "
                f"{_live_now(seq)}.{why} The plan: {plan_words(caps)}. /test shows the block, /stop holds it, /test "
                f"auto off starts no more.{drops}")  # fmt: skip
    if at["on"]:
        return (f"Automatic live test on since {_when(at['approved']['at'])} (your yes: \"{at['approved']['words']}\"). "
                "When a hunt strategy passes on paper (100 forward trades where a live hunt fill lands, at the measured "
                f"cost of a trade, a lower bound above zero, also with each gain capped): {plan_words(at)}. /test auto "
                f"off turns it off; /stop holds it.{drops}")  # fmt: skip
    last = f" The last one: {at['used']['result']}." if at.get("used") and at["used"].get("result") else ""
    here = {"wallet": cfg.operator.wallet or None, "oktech_wallet": oktech_wallet_of(cfg)}
    return (f"Automatic live test off.{last} /test auto on turns it on, and that message is your yes for this plan: "
            f"{plan_words(dict(DEFAULTS) | here)}.{drops}")  # fmt: skip


def command(args: list[str], *, cfg: Config, paths: OperatorPaths, notes: NoteSink, now: float,
            date: int | None = None) -> str:  # fmt: skip
    """/test auto (show it), /test auto on (the owner's own message is the yes for the widened plan, its values as they
    are: the CLI sets others with the owner's words), /test auto off. Nothing else is understood or repeated."""
    words = [w.lower() for w in args]
    if not words:
        return status_words(paths, now, cfg)
    if words == ["on"]:
        if read_autotest(paths)["on"]:
            return "The automatic live test is already on. " + status_words(paths, now, cfg)
        if not cfg.operator.wallet:
            return "Nothing changed: the sniper wallet is not set ([operator] wallet in the config)."
        try:  # the yes is for the wallets /test auto showed: the ones the config names now
            at = set_autotest(paths, True, words="/test auto on", at=date if date is not None else now, by="telegram",
                              now=now, wallet=cfg.operator.wallet, oktech_wallet=oktech_wallet_of(cfg))  # fmt: skip
        except ValueError as exc:
            return f"Nothing changed: {exc}."
        notes.write(f"owner turned the automatic live test on (/test auto on): {plan_words(at)}", "decision")
        return "Automatic live test on. " + status_words(paths, now, cfg)
    if words == ["off"]:
        set_autotest(paths, False, by="telegram", now=now)
        notes.write("owner turned the automatic live test off (/test auto off)", "decision")
        return ("Automatic live test off: no strategy starts by itself any more, and a request it wrote that has not "
                "started never starts. A block running now runs to its end; /stop ends it now.")  # fmt: skip
    return "Nothing changed: send /test auto to see it, /test auto on or /test auto off."


def autotest_cli(cfg: Config, args, now: float | None = None) -> int:
    """`snipetech operate autotest [--on --approved "<the owner's words>" --wallet <address> [caps]] [--off]`: the cloud
    session runs it with the owner's yes in chat, and records the owner's words and the sniper wallet they named (it
    must be the config's [operator] wallet). Prints where the file is and how it stands; a bad value, or a flag of it
    without `autotest`, changes nothing and exits with the reason."""
    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
    values = {k: getattr(args, k) for k in FIELDS if getattr(args, k, None) is not None}
    named = getattr(args, "autotest_wallet", None)
    if args.action != "autotest":
        raise SystemExit("--on, --off, --approved, --wallet and the caps go with `operate autotest`")
    if args.autotest_on and args.autotest_off:
        raise SystemExit("operate autotest: --on or --off, not both")
    if (values or args.approved or named) and not args.autotest_on:
        raise SystemExit("operate autotest: --approved, --wallet and the caps go with --on")
    now = time.time() if now is None else now
    try:
        if args.autotest_on:
            if not str(args.approved or "").strip():
                raise SystemExit('operate autotest --on needs --approved "<the owner\'s words, as they said them>"')
            if not named:
                raise SystemExit("operate autotest --on needs --wallet <the sniper wallet the owner's yes names>")
            if named != cfg.operator.wallet:  # a yes for one wallet never starts a test on another
                raise SystemExit(f"operate autotest: not changed: the owner's yes names wallet {named[:6]}…, and the "
                                 f"config's sniper wallet is {(cfg.operator.wallet or 'none')[:6]}…")  # fmt: skip
            set_autotest(paths, True, values=values, words=args.approved, by="cli", now=now, wallet=named,
                         oktech_wallet=oktech_wallet_of(cfg))  # fmt: skip
        elif args.autotest_off:
            set_autotest(paths, False, by="cli", now=now)
    except ValueError as exc:
        raise SystemExit(f"operate autotest: not changed: {exc}") from None
    print(f"automatic test file {autotest_file(paths)} on this machine")
    print(status_words(paths, now, cfg))
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

    def note(self, text: str) -> None:
        """For the record and the log only: never a line to the owner (a block judged, one going on)."""
        self.notes.write(text, "info")
        self.info(text)

    def info(self, text: str) -> None:
        if self.log is not None:
            self.log(text)


async def step(svc, cfg: Config, paths: OperatorPaths, now: float, *, snap=None, notes: NoteSink | None = None,
               notifier=None, log: Callable[[str], None] | None = None) -> dict | None:  # fmt: skip
    """The automatic test's part of a round (live_test.round_step calls it between closing a test and starting a
    request): the strategy running goes on (its block judged once it ended, the next one requested, a fault held), or
    is dropped or ends; with no strategy running and the owner's yes on, the best one that passes on paper and was
    never dropped starts, in the same round as a drop. Returns the record as it stands, or None."""
    r = _Round(svc, cfg, paths, now, snap, notes or NoteSink(paths, now=lambda: now), notifier, log)
    seq = load_seq(paths)
    if seq is not None and seq.get("state") == "running":
        seq = await _advance(r, seq)
        if seq.get("state") == "running":
            return seq
    at = read_autotest(paths)
    if not at["on"]:
        return seq
    return await _begin(r, at) or seq


def _caps(at: dict) -> dict[str, Any]:
    return {k: at[k] for k in FIELDS}


async def _begin(r: _Round, at: dict) -> dict | None:
    """A strategy's first block, for the best strategy that passes on paper and was never dropped, when every condition
    holds; else a log line and nothing (the next round looks again)."""

    def wait(why: str) -> None:
        r.info(f"automatic test waits: {why}")

    paths, now, store = r.paths, r.now, getattr(r.svc, "store", None)
    wallet = r.cfg.operator.wallet
    if paths.stop_flag.exists():
        return wait("your /stop is in force")
    if live_test.in_progress(paths, now):
        return wait("a live test is in progress")
    if not wallet:
        return wait("the sniper wallet is not set")
    mismatch = wallet_mismatch(r.cfg, at)
    if mismatch:  # rule 1: a yes is for a wallet; the owner hears it once a day, and a new /test auto on fixes it
        sig = "no test starts by itself on another wallet"
        if not r.notes.has_today(sig, now):
            r.say(f"Automatic live test waits: {mismatch}, and {sig} than the one you said yes for. /test auto on is a "
                  "yes for the wallet the config names now.")  # fmt: skip
        return wait(mismatch)
    held_back, why = await risk_guard.tripped(r.svc, r.cfg, paths, now, snap=r.snap)  # cannot tell is never clear
    if held_back:
        return wait(f"the wallet-wide guard: {why}")
    try:
        ok = live_test.eligible(store, r.cfg, now)
    except Exception as exc:  # noqa: BLE001 - the next round reads it again
        return wait(f"the paper test could not be read ({type(exc).__name__})")
    gone = {str(d["strategy"]) for d in dropped(paths)}
    ok = [x for x in ok if x["id"] not in gone]  # a dropped strategy is never started again
    if not ok:
        return wait("no hunt strategy passes on paper" + (" that was not dropped" if gone else " yet"))
    size = float(ok[0].get("buy_sol") or 0)
    if size > float(at["buy_sol"]) + 1e-9:  # the smaller size would be another size than it was scored at
        sig = f"automatic test: {ok[0]['id']} passes on paper at {size:g} SOL a buy"
        if not r.notes.has_today(sig, now):
            r.say(f"{sig}, above the {at['buy_sol']:g} SOL a buy of your standing yes, so it does not start by itself: a "
                  "test at another size than it was scored at says nothing about it (a round trip costs about 0.009 "
                  f"SOL whatever the size). /test {ok[0]['id']} go tests it at its own size, if you say yes to that "
                  "plan.")  # fmt: skip
    fits = [x for x in ok if float(x.get("buy_sol") or 0) <= float(at["buy_sol"]) + 1e-9]
    if not fits:
        return wait(f"{ok[0]['id']} buys {size:g} SOL, above the standing yes's {at['buy_sol']:g}")
    best = fits[0]
    eq = live_test.equity_now(r.snap, now, wallet)
    if eq is None:
        return wait("the wallet's balance is not known this round")
    try:
        p = live_test.plan(best, eq, wallet, fills=int(at["fills"]), minutes=int(at["minutes"]),
                           loss_sol=float(at["loss_sol"]))  # fmt: skip
    except ValueError as exc:
        return wait(f"{best['id']} cannot be traded live: {exc}")
    need = live_test._need(p)
    if eq + 1e-9 < need:  # no room for a first block above a floor of at least 1 SOL: said once a day
        sig = "automatic test: no room in the wallet to start"
        if not r.notes.has_today(sig, now):
            r.say(f"{sig} {p['label']} ({best['id']}): the wallet holds {eq:.2f} SOL and a strategy needs {need:.2f} "
                  "(its floor, never under 1 SOL, a buy and its fees); it starts once the wallet has that (said once a "
                  "day).")  # fmt: skip
        return wait(f"the wallet holds {eq:.2f} SOL, under the {need:.2f} SOL a first block needs")
    before = load_seq(paths)
    seq = {"id": f"seq-{int(now)}", "state": "running", "plan": PLAN, "strategy": best["id"], "label": p["label"],
           "approved": at["approved"], "caps": _caps(at), "wallet": at["wallet"], "oktech_wallet": at["oktech_wallet"],
           "buy_sol": p["buy_sol"], "floor": p["floor"], "start_equity": round(eq, 4), "started_at": int(now),
           "paper": float(best.get("mean") or 0), "blocks": [{"n": 1, "requested_at": int(now)}],
           "dropped": list((before or {}).get("dropped") or [])}  # fmt: skip
    req = live_test.request(p, best, r.cfg, now, by=BY, sequence={"id": seq["id"], "block": 1}, approved=at["approved"])
    save_seq(paths, seq)  # first: the round's own check asks that the request be the sequence's own
    why, _wait, _kind = await live_test._check(r.svc, r.cfg, paths, req, r.snap, now)
    if why is None and not live_test.write_request(paths, req, now):
        why = "another live test was requested a moment ago"
    if why:  # nothing was asked of OKTech: no new record, the last one's back (with every drop in it)
        if before is not None:
            save_seq(paths, before)
        else:
            sequence_path(paths).unlink(missing_ok=True)
        return wait(why)
    r.say(f"Automatic live test, on {_yes_words(at)} (\"{at['approved']['words']}\"): {p['label']} ({best['id']}) "
          f"passes on paper with {int(best['n']):,} forward trades where a live hunt fill lands, at the measured cost "
          f"of a trade, {float(best['mean']):+.4f} SOL a trade, lower bound {float(best['ev_lower']):+.4f}, also with "
          f"each gain capped. It trades live from now on, block after block, on sniper wallet {seq['wallet'][:6]}… "
          f"(OKTech wallet {seq['oktech_wallet']}): {p['buy_sol']:g} SOL a buy, at most {p['fills']} buys one coin at a "
          f"time and {p['minutes']} minutes a block, floor {p['floor']:.2f} SOL ({float(at['loss_sol']):g} SOL below "
          f"the wallet's {eq:.2f}) for every block. Once it has {p['fills']} live trades it goes on while live is above "
          f"zero and at least {_share(at['continue_at'])}, and is dropped for good below that or at its floor. /stop "
          "holds it; /test auto off ends it.")  # fmt: skip
    return seq


async def _advance(r: _Round, seq: dict) -> dict:
    """The strategy running: nothing while its block runs (a block closing on a fault holds); once the block ended,
    its end read once, then judged (once the strategy has enough live trades) and the next block requested, or the
    strategy dropped; a block that did not start is tried again."""
    blocks = seq.setdefault("blocks", [])
    if not blocks:
        return await _next_block(r, seq)
    cur = blocks[-1]
    t = live_test.find(r.paths, seq["strategy"], cur["requested_at"])
    if t is None and cur.get("decision") in ("next", "continue"):  # judged already: its record is not needed any more
        return await _next_block(r, seq)
    if t is None:
        if len(blocks) == 1:  # its request was never written (a round cut short): nothing was bought
            save_seq(r.paths, seq | {"state": "void", "ended_at": int(r.now)})
            r.info("automatic test: the first block's request was never written; the record is void")
            return seq | {"state": "void"}
        blocks.pop()
        save_seq(r.paths, seq)
        r.info(f"automatic test: block {cur['n']}'s record is missing (nothing of it is known); the next is requested")
        return await _next_block(r, seq)
    hold = seq.get("hold")
    if hold and hold.get("kind") != "cycler" and t.get("job_id") and int(cur["n"]) > int(hold.get("after") or 0):
        _clear(r, seq, f"block {cur['n']} started")  # a block that started clears every hold but a dead cycler's
    if live_test._active(t, r.now):
        if t["state"] == "closing" and t.get("closing_kind") in CLOSING_FAULTS:
            return _hold(r, seq, "closing", f"block {cur['n']} has ended and is not closed: {t.get('closing_why')}")
        return seq  # a block that runs ends by itself (its cycler reads the /stop flag every few seconds)
    if not t.get("started"):  # never started: nothing was bought; the next round's start tries again
        blocks.pop()
        save_seq(r.paths, seq)
        if t.get("fault"):
            error = str(t.get("error") or "no reason known").removeprefix("not started: ")
            _hold(r, seq, "start", f"block {cur['n']} did not start: {error}")
        else:
            r.info(f"automatic test: block {cur['n']} did not start ({t.get('error')}); it is requested again")
        return await _next_block(r, seq)
    if cur.get("decision") in ("next", "continue"):
        return await _next_block(r, seq)
    if not isinstance(cur.get("outcome"), dict):  # how it ended, read once
        live = _live(r, t)
        kind, why = end_kind(t, int(t.get("fills") or read_autotest(r.paths)["fills"]), live["own_mints"])
        cur["outcome"] = {"kind": kind, "why": why}
        hold = seq.get("hold")
        if kind == "died":
            _hold(r, seq, "cycler", f"block {cur['n']}'s cycler died ({why})")
        elif kind == "coin":
            coins = [m for m in t.get("held_at_end") or [] if m in set(live["own_mints"])]
            _hold(r, seq, "coin", f"block {cur['n']} {why}", coins=coins)
        elif hold and hold.get("kind") == "cycler" and int(cur["n"]) > int(hold.get("after") or 0):
            _clear(r, seq, f"block {cur['n']} ran to its end")
        save_seq(r.paths, seq)
    if cur["outcome"]["kind"] == "floor":
        _record_live(cur, _live(r, t), counted=False)
        return await _drop(r, seq, f"block {cur['n']} ended on its floor: {cur['outcome']['why']}")
    verdict = await _judge(r, seq, cur, t)
    if verdict is not None:
        return verdict
    return await _next_block(r, seq)


def _live(r: _Round, t: dict) -> dict:
    """block_live for the block `t`, on the wallet it ran on, at the config's costs."""
    return block_live(r.svc.store, str(t.get("wallet") or ""), t, r.now, per_tx_sol=r.cfg.costs.per_tx_sol(),
                      failed_fee_sol=float(r.cfg.costs.priority_fee_sol) + 5000 / 1e9)  # fmt: skip


def _own_trades(seq: dict) -> int:
    return sum(int(b["live"]["n"]) for b in seq.get("blocks") or [] if isinstance(b.get("live"), dict))


async def _judge(r: _Round, seq: dict, cur: dict, t: dict) -> dict | None:
    """A block that ran and ended: None when the next block is to be requested (the strategy has fewer than `fills`
    live round trips in all and the block did not make all its buys, or it was judged and goes on); else the record
    (waiting for the measure stage, holding on it, or dropped)."""
    at = read_autotest(r.paths)
    n, fills, kind = cur["n"], int(at["fills"]), cur["outcome"]["kind"]
    live = _live(r, t)
    paper = float((t.get("forward") or {}).get("mean") or 0)
    seq["paper"] = paper
    earlier = _own_trades(seq) - (int(cur["live"]["n"]) if isinstance(cur.get("live"), dict) else 0)
    if kind != "complete" and earlier + _reading(live, True, final=False)["n"] < fills:
        _record_live(cur, live, counted=False)
        cur["decision"] = "next"
        save_seq(r.paths, seq)
        r.note(f"automatic test: block {n} of {_label(seq)} ended ({kind}); the strategy has "
               f"{_own_trades(seq)} of the {fills} live trades it is judged on, so its next block starts")  # fmt: skip
        return None
    waited = r.now - float(t.get("ended_at") or r.now)
    last = (r.svc.store.coverage(MEASURE_KEY) or {}).get("ts")
    if last is None or float(last) < live["settled"] + MEASURE_SETTLE_SECS:
        # its last sales and failed sends reach the fills at the measure stage's first run after them
        when = "never" if last is None else f"last at {_when(last)}"
        if waited < MEASURE_WAIT_SECS:
            r.info(f"automatic test: block {n} waits for the measure stage to read past its last trade (it ran {when})")
            return seq
        return _hold(r, seq, "measure", f"the measure stage has not read block {n}'s trades within "
                                        f"{MEASURE_WAIT_SECS // 60} minutes of its end (it ran {when}), so the "
                                        "strategy cannot be judged")  # fmt: skip
    open_buys = sum(1 for b in live["buys"] if not b["measured"])
    if open_buys and waited < MEASURE_WAIT_SECS:
        r.info(f"automatic test: block {n} waits for the measure stage ({open_buys} of its buys are not covered by "
               "sales in the fills yet)")  # fmt: skip
        return seq
    _record_live(cur, live, counted=True)
    for b in seq["blocks"][:-1]:  # earlier blocks counted before their trades were measured: read again, for good
        if isinstance(b.get("live"), dict) and b["live"].get("counted"):
            continue
        tb = live_test.find(r.paths, seq["strategy"], b.get("requested_at"))
        if tb is not None and tb.get("started"):
            _record_live(b, _live(r, tb), counted=True)
    tl = _tally(seq)
    if (kind != "complete" and _own_trades(seq) < fills) or tl["n"] == 0:  # copies the tape could not tell, now told
        cur["decision"] = "next"
        save_seq(r.paths, seq)
        return None
    mean = tl["sum"] / tl["n"]
    cont, loss = float(at["continue_at"]), float((seq.get("caps") or {}).get("loss_sol") or DEFAULTS["loss_sol"])
    if tl["sum"] <= -loss + 1e-9:  # 1 SOL down live, whatever was deposited: its floor, read from the fills
        return await _drop(r, seq, f"it is {-tl['sum']:.2f} SOL down live over its {tl['n']} trades, at its floor "
                                   f"({loss:g} SOL below its first block's start)")  # fmt: skip
    if not (paper > 0 and mean > 0 and mean >= cont * paper - 1e-9):
        return await _drop(r, seq, f"live is below {_share(cont)} after block {n}: the analyst works out why")
    cur["decision"] = "continue"
    seq["verdict"] = {"at": int(r.now), "block": n, "n": tl["n"], "mean": round(mean, 6), "paper": paper}
    save_seq(r.paths, seq)
    r.note(f"automatic test: block {n} of {_label(seq)}: live {mean:+.4f} SOL a trade over the {tl['n']} trades so far "
           f"(the measure stage's fills, {tl['sum']:+.3f} SOL{_live_words(seq)}), paper {paper:+.4f}: at least "
           f"{_share(cont)}, so block {n + 1} starts, floor {float(seq['floor']):.2f} SOL")  # fmt: skip
    return None


def _record_live(cur: dict, live: dict, *, counted: bool) -> None:
    """A block's two readings in its record (block_live). `counted`: final, with each buy not measured counted at what
    is known of it and its unsold part at its whole cost; else only what was measured, the rest named as not measured
    yet (read again when the strategy is judged)."""
    own, every = _reading(live, True, final=counted), _reading(live, False, final=counted)
    cur["live"] = {"n": own["n"], "sum": own["sum"], "n_all": every["n"], "sum_all": every["sum"],
                   "unmeasured": every["unmeasured"], "failed": every["failed"], "failed_sol": every["failed_sol"],
                   "copies": live["copies"], "counted": counted}  # fmt: skip


async def _next_block(r: _Round, seq: dict) -> dict:
    """Request the next block of the same strategy, at the latest yes's caps and the first block's floor, once the
    round's start would take it. The owner's off and another wallet end the strategy's run; a strategy that no longer
    passes on paper or whose size changed ends it too (not a drop); a wallet at the floor drops it; a fault holds it;
    anything else waits for the next round."""
    at = read_autotest(r.paths)
    if not at["on"]:
        return await _end(r, seq, "you turned the automatic test off")
    mismatch = wallet_mismatch(r.cfg, seq)
    if mismatch:  # the next block would run on another wallet with this one's floor
        return await _end(
            r, seq, f"the sniper wallet changed since it started: {mismatch.replace('your standing yes', 'it')}"
        )
    mismatch = wallet_mismatch(r.cfg, at)
    if mismatch:  # the owner's latest yes names another wallet: it is no yes for this one any more
        return await _end(r, seq, mismatch)
    if r.paths.stop_flag.exists():
        return _wait(r, seq, "your /stop is in force (/resume lets it go on)")
    store, wallet, sid = r.svc.store, r.cfg.operator.wallet, seq["strategy"]
    try:
        ok, why_not, _rows = live_test._survey(store, r.cfg, r.now)
    except Exception as exc:  # noqa: BLE001 - the next round reads it again
        return _wait(r, seq, f"the paper test could not be read ({type(exc).__name__})")
    row = next((x for x in ok if x["id"] == sid), None)
    if row is None:
        return await _end(r, seq, f"{sid} no longer passes on paper: it {why_not.get(sid, 'is not frozen any more')}")
    size = float(row.get("buy_sol") or 0)
    if abs(size - float(seq["buy_sol"])) > 1e-9:
        return await _end(r, seq, f"{sid}'s size changed since its first block ({float(seq['buy_sol']):g} to {size:g} "
                                  "SOL a buy)")  # fmt: skip
    if size > float(at["buy_sol"]) + 1e-9:
        return await _end(r, seq, f"{sid} buys {size:g} SOL, above the {at['buy_sol']:g} SOL a buy of your yes")
    eq = live_test.equity_now(r.snap, r.now, wallet)
    if eq is None:
        return _hold(r, seq, "balance", "the wallet's balance could not be read this round")
    room = round(size + live_test.BUY_FEE_MARGIN_SOL + RENT_SOL, 4)
    if eq + 1e-9 < room:  # 7 Nov 10:59 the wallet was emptied: that is no result of the strategy's
        return _hold(r, seq, "room", f"the wallet holds {eq:.2f} SOL: no room for a buy of {size:g} SOL with its fees "
                                     f"and rent ({room:.2f} SOL)", daily=True)  # fmt: skip
    loss = float((seq.get("caps") or {}).get("loss_sol") or DEFAULTS["loss_sol"])
    try:
        p = live_test.plan(row, eq, wallet, fills=int(at["fills"]), minutes=int(at["minutes"]), loss_sol=loss,
                           floor=float(seq["floor"]))  # fmt: skip
    except ValueError as exc:
        return await _end(r, seq, f"{sid} cannot be traded live any more: {exc}")
    need = live_test._need(p)
    # the floor is the wallet's SOL and coins, as the cycler reads it: a coin still held counts at the tape's price (one
    # the tape has not priced leaves it unknown, and the next block waits on the coin anyway)
    total = r.snap.total_equity if r.snap is not None and r.snap.held else eq
    if total is not None and total + 1e-9 < need:
        return await _drop(r, seq, f"it is at its floor: the wallet holds {total:.2f} SOL, with no room above its "
                                   f"{p['floor']:.2f} SOL floor for another buy and its fees ({need:.2f})")  # fmt: skip
    if live_test.in_progress(r.paths, r.now):
        return _wait(r, seq, "another live test is in progress")
    n = int(seq["blocks"][-1]["n"]) + 1 if seq["blocks"] else 1
    req = live_test.request(
        p, row, r.cfg, r.now, by=BY, sequence={"id": seq["id"], "block": n}, approved=at["approved"]
    )
    seq["blocks"].append({"n": n, "requested_at": int(r.now)})
    save_seq(r.paths, seq)
    why, _wait_too, kind = await live_test._check(r.svc, r.cfg, r.paths, req, r.snap, r.now)
    if why is None and not live_test.write_request(r.paths, req, r.now):
        why, kind = "another live test was requested a moment ago", "busy"
    if why:
        seq["blocks"].pop()
        return await _refused(r, seq, why, kind)
    seq["waiting"] = None
    save_seq(r.paths, seq)
    return seq


async def _refused(r: _Round, seq: dict, why: str, kind: str | None) -> dict:
    """The round's start refused the next block (live_test._check): end the strategy's run, hold it on a fault, or wait."""
    if kind in ENDS:
        return await _end(r, seq, why)
    if kind in FAULTS:
        return _hold(r, seq, str(kind), why)
    return _wait(r, seq, why)  # a coin of the block's own still held keeps its hold as it is


def _wait(r: _Round, seq: dict, why: str) -> dict:
    """The next block waits on something that passes by itself or is the owner's: said in the log only."""
    since = (seq.get("waiting") or {}).get("since") or int(r.now)
    seq["waiting"] = {"why": why, "since": int(since)}
    save_seq(r.paths, seq)
    r.info(f"automatic test: the next block waits: {why}")
    return seq


def _hold(r: _Round, seq: dict, kind: str, why: str, *, daily: bool = False, coins: list[str] | None = None) -> dict:
    """An execution fault (or a wallet with no room) holds new blocks: the owner hears it once when it starts (`daily`:
    once a day), and once when it clears (_clear); the strategy is not dropped and keeps its floor."""
    day = _day(r.now)
    hold = seq.get("hold")
    if hold and hold.get("kind") == kind:
        if kind != "coin":
            hold["why"] = why
        if daily and hold.get("told_day") != day:
            hold["told_day"] = day
            r.say(_hold_words(seq, hold))
    else:
        after = int(seq["blocks"][-1]["n"]) if seq.get("blocks") else 0
        seq["hold"] = hold = {"kind": kind, "why": why, "since": int(r.now), "after": after, "told_day": day} | (
            {"coins": list(coins)} if coins else {}
        )
        r.say(_hold_words(seq, hold))
    seq["waiting"] = {"why": hold["why"], "since": int(hold["since"])}
    save_seq(r.paths, seq)
    return seq


def _hold_words(seq: dict, hold: dict) -> str:
    what = f"Automatic live test holds {_label(seq)}: {hold['why']}."
    if hold["kind"] == "cycler":
        return (f"{what} An execution fault, not the strategy's: its trades still count and it is not dropped. The next "
                "block is tried at each round, and you hear again once one runs to its end.")  # fmt: skip
    tail = ", said once a day" if hold["kind"] == "room" else ""
    sell = "".join(f" /sell {m} sells {m[:6]}… now." for m in hold.get("coins") or [])
    return (f"{what}{sell} No block starts until it clears (checked every round{tail}); the strategy is not dropped and "
            "goes on at the same floor once it does.")  # fmt: skip


def _clear(r: _Round, seq: dict, what: str) -> None:
    """The owner's one line when what held the strategy has cleared."""
    hold = seq.get("hold")
    if not hold:
        return
    seq["hold"] = None
    seq["waiting"] = None
    save_seq(r.paths, seq)
    why = str(hold.get("why") or "")
    why = why if len(why) <= 160 else why[:159] + "…"
    r.say(f"Automatic live test goes on with {_label(seq)}: what held it has cleared ({why}); {what}.")


def _tally(seq: dict) -> dict[str, Any]:
    """Every block's trades so far, read both ways (block_live), and the worse of the two readings: its trades, their
    SOL, which reading it is, and the failed sends and copies in it."""
    lives = [b["live"] for b in seq.get("blocks") or [] if isinstance(b.get("live"), dict)]
    own_n, own_sum = sum(int(x["n"]) for x in lives), sum(float(x["sum"]) for x in lives)
    all_n = sum(int(x.get("n_all", x["n"])) for x in lives)
    all_sum = sum(float(x.get("sum_all", x["sum"])) for x in lives)
    readings = [(s_ / n_, k, n_, s_) for k, n_, s_ in (("own", own_n, own_sum), ("all", all_n, all_sum)) if n_ > 0]
    _mean, which, n, total = min(readings) if readings else (0.0, "own", 0, 0.0)
    return {"n": n, "sum": round(total, 6), "reading": which, "copies": sum(int(x.get("copies") or 0) for x in lives),
            "failed": sum(int(x.get("failed") or 0) for x in lives),
            "failed_sol": round(sum(float(x.get("failed_sol") or 0) for x in lives), 6)}  # fmt: skip


def _live_words(seq: dict) -> str:
    """What the live numbers include, for the owner's lines: failed sends, the book's copies, buys not measured."""
    tl = _tally(seq)
    lives = [b["live"] for b in seq.get("blocks") or [] if isinstance(b.get("live"), dict)]
    out = f"; {tl['failed']} failed sends included ({tl['failed_sol']:.3f} SOL)" if tl["failed"] else ""
    if tl["copies"] and tl["reading"] == "all":
        out += (f"; the book's {tl['copies']} copies counted in: the worse of two readings, since the fills cannot "
                "tell them for sure from the test's own buys")  # fmt: skip
    elif tl["copies"]:
        out += f"; the book's {tl['copies']} copies left out: the worse of two readings"
    counted = sum(int(x.get("unmeasured") or 0) for x in lives if x.get("counted"))
    later = sum(int(x.get("unmeasured") or 0) for x in lives if not x.get("counted"))
    out += f"; {counted} not measured, counted at their whole cost" if counted else ""
    return out + (f"; {later} more not measured yet" if later else "")


def _summary(seq: dict) -> str:
    tl = _tally(seq)
    n, total = tl["n"], tl["sum"]
    lt = (f"{n} live trades, {total / n:+.4f} SOL a trade (the measure stage's fills, {total:+.3f} SOL in all"
          f"{_live_words(seq)})" if n else f"no live trade judged{_live_words(seq)}")  # fmt: skip
    paper = seq.get("paper")
    k = len(seq.get("blocks") or [])
    return f"{k} block{'' if k == 1 else 's'}, {lt}" + (
        f"; paper {float(paper):+.4f} a trade" if paper is not None else ""
    )


def _history(r: _Round, seq: dict, *, drop: bool) -> None:
    """One `"kind": "sequence"` line in tests.jsonl for each strategy's run, dropped or ended."""
    tl = _tally(seq)
    n, total = tl["n"], tl["sum"]
    live_test._history(r.paths, {"kind": "sequence", "id": seq["id"], "strategy": seq["strategy"],
                                 "label": seq.get("label"), "plan": seq.get("plan"), "approved": seq.get("approved"),
                                 "caps": seq.get("caps"), "wallet": seq.get("wallet"),
                                 "oktech_wallet": seq.get("oktech_wallet"), "floor": seq.get("floor"),
                                 "blocks": seq["blocks"], "trades": n, "live_sol": total,
                                 "live_mean": round(total / n, 6) if n else None, "reading": tl["reading"],
                                 "paper_mean": seq.get("paper"), "dropped": drop, "why": seq.get("why"),
                                 "started_at": seq.get("started_at"), "ended_at": int(r.now)})  # fmt: skip


async def _drop(r: _Round, seq: dict, why: str) -> dict:
    """The strategy is dropped for good: its record with why and the numbers, one line in tests.jsonl, and the owner's
    one line. The next strategy that passes on paper starts in the same round (step), with a floor of its own."""
    tl = _tally(seq)
    entry = {"strategy": seq["strategy"], "label": seq.get("label"), "sequence": seq["id"], "at": int(r.now),
             "why": why, "trades": tl["n"], "live_sol": tl["sum"],
             "live_mean": round(tl["sum"] / tl["n"], 6) if tl["n"] else None, "paper_mean": seq.get("paper"),
             "blocks": len(seq.get("blocks") or []), "reading": tl["reading"]}  # fmt: skip
    seq.update({"state": "dropped", "ended_at": int(r.now), "why": why, "hold": None, "waiting": None,
                "dropped": [*(seq.get("dropped") or []), entry]})  # fmt: skip
    save_seq(r.paths, seq)
    _history(r, seq, drop=True)
    r.say(f"Automatic live test dropped {_label(seq)}: {why}. {_summary(seq)}. The automatic test never starts it "
          "again; the next strategy that passes on paper starts with a floor of its own, now or as soon as one "
          "does.")  # fmt: skip
    return seq


async def _end(r: _Round, seq: dict, why: str) -> dict:
    """The strategy's run ends without a drop (the owner's off, another wallet, no longer on paper, another size): its
    record, one line in tests.jsonl, and the owner's one line. The switch is left as the owner set it."""
    seq.update({"state": "ended", "ended_at": int(r.now), "why": why, "hold": None, "waiting": None})
    save_seq(r.paths, seq)
    _history(r, seq, drop=False)
    tail = ("The automatic test stays on: it starts the best strategy that passes on paper." if read_autotest(r.paths)["on"]
            else "The automatic test is off; /test auto on turns it on again.")  # fmt: skip
    r.say(f"Automatic live test ended: {why}. {_label(seq)}: {_summary(seq)}. {tail}")
    return seq
