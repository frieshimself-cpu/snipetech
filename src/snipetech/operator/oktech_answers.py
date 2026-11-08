"""What OKTech has answered about how its sniper reads a setting, recorded once by the owner: operator/OKTECH.

One answer so far, and the reason the file exists (research round 3, priority 1, docs/research/2026-11-07-round-3.md):
does Hunt's maxDevBuySol 0 skip every launch whose creator bought anything, or does 0 mean "no limit"? Every creator-0
rule (a hunt whose trigger has dev_max 0) was scored the first way and is sent to OKTech as maxDevBuySol 0. Read the
second way, its sniper runs the plain crowd hunt, which lost 0.0147 SOL a trade over 1,285 trades on 4-5 Nov tape the
research had not used (with a creator buy of 1 SOL or less, 0.0135 over 486), and OKTech has ignored thresholds before
without saying so (5 Nov 22:11: sent fractions and nulls, it bought every launch as if none were set). Two such rules sat
in the forward paper test on 7 Nov evening (f94add343f, 7d6b4302ff). So until the answer is recorded here, live_test
refuses a creator-0 rule for /test go, the round's start and the automatic test (live_test._why_not), and `oktech
strategy` says so in the plan's notes. Rules with a creator limit above 0 and copies never wait for it.

The owner records it: `/oktech dev0 confirmed` in Telegram (the owner's chat only), or the cloud session with the owner's
word in chat, `snipetech operate oktech --dev-max-zero "<when and how OKTech answered>"`. Written like operator/RISK and
operator/AUTOTEST: JSON, atomically, with when it was said, when it was written and who wrote it; a read never raises.
Only words count: no file, a file that cannot be read, or empty words mean not confirmed, and `/oktech dev0 unconfirmed`
or an empty --dev-max-zero takes it back. Nothing in the code writes it by itself: the agents' start_job refuses
`operate`, and none of their tools writes a file without an extension in operator/."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from ..ops import redact
from .paths import OperatorPaths

DEV_MAX_ZERO = "dev_max_zero"  # the answer's key in operator/OKTECH
WORDS_MAX = 300  # the words kept: when and how OKTech answered
# the question for OKTech, as research round 3 put it to the owner ("What the owner can do now", 2)
QUESTION = "In Hunt, does maxDevBuySol 0 skip every launch whose creator bought anything, or does 0 mean no limit?"
WAIT = "waits for OKTech to confirm what maxDevBuySol 0 means"
# why a creator-0 rule never starts live while the answer is not recorded, as the owner reads it in Telegram (/test,
# /test go, the round's start). The number is research round 3's control on unseen tape: the same 10 SOL / 10 wallets /
# 1 s hunt with no creator limit, at the live landing and the measured cost
DEV0_WHY = (f"{WAIT}: the rule skips every launch whose creator bought anything, and if OKTech reads 0 as no limit its "
            "sniper buys the plain crowd hunt instead, which lost 0.0147 SOL a trade over 1,285 trades on tape the "
            "research had not used (research round 3). /oktech says what to ask OKTech and how to record their "
            "answer")  # fmt: skip
CONFIRM = "/oktech dev0 confirmed"
UNCONFIRM = "/oktech dev0 unconfirmed"
WHO = {"telegram": "you in Telegram", "cli": "the cloud session, with your word"}


def answers_file(paths: OperatorPaths) -> Path:
    return paths.root / "OKTECH"


def _raw(paths: OperatorPaths) -> dict:
    try:
        data = json.loads(answers_file(paths).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _time(value: Any) -> int | None:
    """A unix time as written, or None (a 400-digit integer overflows a float; JSON also reads NaN)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return int(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def dev_max_zero(paths: OperatorPaths) -> dict[str, Any] | None:
    """OKTech's recorded answer that Hunt's maxDevBuySol 0 skips every launch whose creator bought anything: {"words",
    "at" (when it was said), "by"}; None while it is not recorded (no file, one that cannot be read, words that are not
    text or are empty). Never raises."""
    rec = _raw(paths).get(DEV_MAX_ZERO)
    if not isinstance(rec, dict) or not isinstance(rec.get("words"), str):
        return None
    words = " ".join(rec["words"].split())[:WORDS_MAX]
    if not words:
        return None
    by = rec.get("by")
    return {"words": words, "at": _time(rec.get("at")), "by": by if isinstance(by, str) else None}


def set_dev_max_zero(paths: OperatorPaths, words: str, *, by: str, at: float | None = None,
                     now: float | None = None) -> dict[str, Any] | None:  # fmt: skip
    """Record OKTech's answer in the owner's `words` (when and how OKTech said it: on one line, secrets redacted, at most
    WORDS_MAX characters), or take it back with empty words. Returns what is recorded now (dev_max_zero). Atomic, like
    every switch of the owner's: a temporary file, then a rename over it, so a reader never sees half a file."""
    now = time.time() if now is None else now
    said = redact(" ".join(str(words or "").split()))[:WORDS_MAX]  # redacted before it is cut: no half secret is kept
    rec = {"words": said, "at": int(now if at is None else at), "ts": int(now), "by": by}
    paths.ensure()
    f = answers_file(paths)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(_raw(paths) | {DEV_MAX_ZERO: rec}))
    os.replace(tmp, f)
    return dev_max_zero(paths)


def _when(ts: float | None) -> str:
    return time.strftime("%d %b %H:%M UTC", time.gmtime(float(ts))) if ts is not None else "at a time not known"


def status_words(paths: OperatorPaths) -> str:
    """/oktech and `operate oktech`: what is recorded, or the question to put to OKTech and how to record its answer."""
    a = dev_max_zero(paths)
    if a:
        who = WHO.get(a["by"] or "", a["by"] or "someone")
        return (f"OKTech's answer on maxDevBuySol 0: confirmed, recorded {_when(a['at'])} by {who}: \"{a['words']}\". "
                "Hunt strategies whose creator limit is 0 may start live like any other: /test shows them, and the "
                f"automatic test starts one only if it passes on paper. {UNCONFIRM} takes it back.")  # fmt: skip
    return ("OKTech's answer on maxDevBuySol 0: not recorded. No hunt strategy whose creator limit is 0 starts live "
            "(neither /test go nor the automatic test) until OKTech confirms what 0 means: read as no limit, its sniper "
            "would buy the plain crowd hunt, which lost 0.0147 SOL a trade over 1,285 trades on tape the research had "
            f"not used. Ask OKTech: \"{QUESTION}\" If they say 0 skips every launch whose creator bought anything, send "
            f"{CONFIRM} (and give the cloud session their words for the record). If they say 0 means no limit, send "
            "nothing: those strategies cannot run on OKTech as they were scored.")  # fmt: skip


def command(args: list[str], *, paths: OperatorPaths, notes, now: float, date: int | None = None) -> str:
    """/oktech (what is recorded), /oktech dev0 confirmed (the owner's word that OKTech confirmed it; the record keeps
    the message's own date), /oktech dev0 unconfirmed (take it back). Only the owner's chat gets here
    (commands.parse_updates). Nothing else is understood or repeated: a secret key pasted by mistake must not reach the
    chat or the notes. A message that waited in Telegram's queue still counts, unlike /risk and /test: it records what
    OKTech said, which is no less true later, and it starts nothing by itself."""
    words = [w.lower() for w in args]
    at = date if date is not None else now
    if not words:
        return status_words(paths)
    if words == ["dev0", "confirmed"]:
        set_dev_max_zero(paths, f"{CONFIRM}: the owner's word that OKTech confirmed Hunt's maxDevBuySol 0 skips every "
                                "launch whose creator bought anything", by="telegram", at=at, now=now)  # fmt: skip
        notes.write(f"owner recorded OKTech's answer ({CONFIRM}): maxDevBuySol 0 skips every launch whose creator "
                    "bought anything, so hunt strategies whose creator limit is 0 may start live", "decision")  # fmt: skip
        return (f"Recorded: OKTech confirmed that maxDevBuySol 0 skips every launch whose creator bought anything (your "
                f"/oktech of {_when(at)}). Hunt strategies whose creator limit is 0 may now start live like any other: "
                "/test shows them, and the automatic test starts one only if it passes on paper. Give the cloud session "
                f"OKTech's own words for the record. {UNCONFIRM} takes it back.")  # fmt: skip
    if words == ["dev0", "unconfirmed"]:
        set_dev_max_zero(paths, "", by="telegram", at=at, now=now)
        notes.write(f"owner took back OKTech's answer on maxDevBuySol 0 ({UNCONFIRM}): no hunt strategy whose creator "
                    "limit is 0 starts live until it is recorded again", "decision")  # fmt: skip
        return ("Taken back: OKTech's answer on maxDevBuySol 0 is not recorded any more. No hunt strategy whose creator "
                "limit is 0 starts live until it is recorded again. A test running now runs to its end (its sniper is "
                "left alone), and the automatic test asks for no next block of one.")  # fmt: skip
    return (f"Nothing changed: send /oktech to see it, {CONFIRM} once OKTech has confirmed that maxDevBuySol 0 skips "
            f"every launch whose creator bought anything, or {UNCONFIRM} to take that back.")  # fmt: skip


def oktech_cli(cfg, args, now: float | None = None) -> int:
    """`snipetech operate oktech [--dev-max-zero "<when and how OKTech answered>"]`: the cloud session records OKTech's
    answer with the owner's word in chat (`remote --wait run operate oktech --dev-max-zero ...`); an empty value takes
    it back. Prints where the file is and what is recorded. --dev-max-zero with another action, or another switch's
    value with this one, changes nothing and exits with the reason."""
    from .autotest import FIELDS

    paths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
    if args.action != "oktech":
        raise SystemExit("--dev-max-zero goes with `operate oktech`")
    values = ("day_stop", "risk_floor", "daily_usd", "turn_usd", "tool_calls", "analyst_every_h", "approved",
              "autotest_wallet", *FIELDS)  # fmt: skip
    if any(getattr(args, k, None) is not None for k in values) or any(
        getattr(args, k, False) for k in ("budget_default", "autotest_on", "autotest_off")
    ):
        raise SystemExit("operate oktech takes --dev-max-zero only: the other values go with `operate budget`, "
                         "`operate risk` or `operate autotest`")  # fmt: skip
    if args.dev_max_zero is not None:
        set_dev_max_zero(paths, args.dev_max_zero, by="cli", now=now)
    print(f"OKTech's answers file {answers_file(paths)} on this machine")
    print(status_words(paths))
    return 0
