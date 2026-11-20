"""What the owner pays for, checked by code every round: credits, data-API usage, SOL float, disk, failed sends.
A critical row becomes one decision note a day with the exact thing to do."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..claude_client import route
from ..config import Config
from ..store import Store
from .budget import Ledger
from .budget_switch import effective_daily_usd
from .paths import OperatorPaths
from .refusals import OKTECH_REFUSED_KEY, RPC_REFUSED_KEY, open_refusal
from .snapshot import Snapshot

TAPE_ALERT_SECS = 300  # the tape this far behind is critical (its own stale guards are 60 and 120 s)


@dataclass
class Resource:
    name: str
    value: float | None
    unit: str
    threshold: float | None
    status: str  # ok | warn | crit | unknown
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def _pct_left_status(left_pct: float | None, alert_pct: float) -> str:
    if left_pct is None:
        return "unknown"
    if left_pct <= alert_pct:
        return "crit"
    if left_pct <= alert_pct * 2:
        return "warn"
    return "ok"


# The Anthropic credit is our own count: what the owner recorded as on the account (`/credit <dollars>` in Telegram:
# CREDIT_KEY holds it and when; before any record, the config's figure since the first spend) less what the ledger
# says the agents spent since. Nobody can read the account's balance with the agents' key.
CREDIT_KEY = "resources:anthropic_credit"
CREDIT_OVERRUN = 1.05  # spent this many times what was recorded: the record is wrong, not the account empty
CREDIT_FIX = "send /credit <dollars the Anthropic console shows> (console.anthropic.com) and the count starts there"


# What the API itself says about the account is known, whatever our count says: a turn refused for want of credit
# (13 Nov: twice in one day) records when (REFUSED_KEY: `since` the outage's first refusal, `last` its latest), and the
# account is empty until the agents are answered again. Calls already on their way when the balance ran out are still
# answered seconds after the first refusal: only an answer CREDIT_SETTLE_SECS after the last refusal ends the outage.
REFUSED_KEY = "resources:anthropic_refused"
CREDIT_SETTLE_SECS = 120
CREDIT_TOP_UP = ("add credit at console.anthropic.com (Billing; auto-reload there, when switched on, tops the account "
                 "up by itself)")  # fmt: skip
# 19 Nov: with an OpenRouter key the agents spend OpenRouter's credit (claude_client.route), and every word that sends
# the owner to add or read credit names that place (13 Nov: three top-ups on an organisation the key was not in)
OPENROUTER_TOP_UP = ("add credit at openrouter.ai/settings/credits (Auto Top-Up there, when switched on, refills it "
                     "by itself)")  # fmt: skip
OPENROUTER_FIX = "send /credit <dollars openrouter.ai/settings/credits shows> and the count starts there"


def _through_openrouter() -> bool:
    return route() == "openrouter"


def credit_top_up() -> str:
    return OPENROUTER_TOP_UP if _through_openrouter() else CREDIT_TOP_UP


def credit_fix() -> str:
    return OPENROUTER_FIX if _through_openrouter() else CREDIT_FIX


def _credit_page() -> str:
    """Where the owner reads the figure /credit takes: the account the agents spend from."""
    return "openrouter.ai/settings/credits" if _through_openrouter() else "the Anthropic console"


# What turn.key_organisation puts behind the API's words. It names "the credit", so it is cut off before the words
# are read: with it, every refusal of the direct API (an overload, a rate limit) read as an empty account (19 Nov).
ORG_HINT = " (the server's key belongs to"


# OpenRouter's own names for a spending limit set there (its limits page): the key's credit limit, a budget on the
# workspace, a guardrail's budget. `openrouter_credits` is the account itself and is not one of them.
LIMIT_SOURCES = frozenset({"openrouter_key_limit", "openrouter_workspace_budget", "openrouter_guardrail_budget"})
LIMIT_REACHED = "a spending limit at OpenRouter is reached"  # the words of the note, and what "once a day" looks for


def _limit_source(body: Any) -> str:
    """`limit_source` as a refusal's body gives it: in a top-level `metadata` (the Messages shape) or in the error's."""
    if not isinstance(body, dict):
        return ""
    for holder in (body, body.get("error")):
        meta = holder.get("metadata") if isinstance(holder, dict) else None
        if isinstance(meta, dict) and meta.get("limit_source"):
            return str(meta["limit_source"])
    return ""


def says_limit_reached(words: str, body: Any = None) -> bool:
    """OpenRouter refused because a spending limit set there is used up (the key's own credit limit, a budget), not
    because the account is empty: by the body's `limit_source` where it gives one, else by its words ("Key limit
    exceeded", "Budget limit exceeded", "... budget of $X exceeded", and the 402 that sends to the key's page). Adding
    credit cures none of these: the limit is raised on the key's page, or it resets by itself."""
    source = _limit_source(body)
    if source:
        return source in LIMIT_SOURCES
    said = str(words).split(ORG_HINT)[0].lower()
    return ("key limit exceeded" in said or "budget limit exceeded" in said
            or ("budget of $" in said and "exceeded" in said)
            or ("requires more credits" in said and "key's total limit" in said))  # fmt: skip


def limit_reached_note(words: str, used_today_usd: float | None, day_budget_usd: float | None) -> str:
    """The owner's line for a reached limit: what OpenRouter said, where the limit is raised, and which of the two
    cases it is by our own count of the day: the agents used it up (raise it or wait for its reset), or they used far
    less than their day's budget (then, unless the limit is far below that budget, something else spends on the key:
    look at the activity page and replace the key before raising anything)."""
    head = (f"{LIMIT_REACHED} ({str(words)[:300]}): the agents get no answer until it resets or is raised at "
            "openrouter.ai/settings/keys")  # fmt: skip
    if used_today_usd is None:
        return head
    spent = f"By our own count the agents used {used_today_usd:.2f} $ of credit today"
    if day_budget_usd and used_today_usd < 0.5 * day_budget_usd:
        return (f"{head}. {spent}, far under their {day_budget_usd:g} $ a day: if the limit is not far below that, "
                "something else spends on this key. Look at openrouter.ai/activity before raising anything, and make "
                "a new key if a use there is not ours")  # fmt: skip
    return f"{head}. {spent}"


def says_no_credit(words: str) -> bool:
    """The API's refusal is about the account's credit ("Your credit balance is too low", "must add credits",
    OpenRouter's "Insufficient credits"). Not one: OpenRouter's hold for requests in flight, which passes by itself
    while the balance is there (claude_client restates it as a 429), any other 429, and this code's own hint about
    the key's organisation."""
    said = str(words).split(ORG_HINT)[0].lower()
    if said.startswith("429:"):  # a refusal that asks to be waited out is never an empty account
        return False
    if says_limit_reached(words):  # nor is a limit set at OpenRouter that is used up: credit does not cure it
        return False
    return "credit" in said and "in-flight" not in said and "in flight" not in said


def credit_refused(store: Store, ledger: Ledger, now: float) -> bool:
    """Record that the API refused a turn for want of credit. True when an outage starts with it: the first refusal,
    or the agents were answered since the last one (the owner paid and it ran dry again)."""
    rec = store.coverage(REFUSED_KEY)
    last = float(rec.get("last") or 0)
    starts = not last or ledger.spent_since(last + CREDIT_SETTLE_SECS) > 0
    store.set_coverage(REFUSED_KEY, {"since": now if starts else float(rec.get("since") or now), "last": now})
    return starts


def credit_empty_since(store: Store, ledger: Ledger) -> float | None:
    """When the API began refusing for want of credit, while no turn has been answered since; None otherwise."""
    rec = store.coverage(REFUSED_KEY)
    last = float(rec.get("last") or 0)
    if not last or ledger.spent_since(last + CREDIT_SETTLE_SECS) > 0:
        return None
    return float(rec.get("since") or last)


def credit_row(cfg: Config, store: Store, ledger: Ledger, now: float) -> Resource:
    """The credit left by our count, and how long it lasts. The pace is the faster of the week's mean and the last day:
    once the owner raises the agents' budget (/budget, 7 Nov: 200 $ a day against the 4.44 $ spent on 6 Nov) the spend
    jumps from one day to the next, and a week's mean hides it for days.

    A count that ran out while the agents went on being answered is not an empty account: more was on it than was
    recorded. 11 Nov: no top-up had ever been recorded (the row asked for `config set` on the server, as root), so the
    count stood at "0 $ of 100 $ left" and alarmed every midnight while the agents spent 20 $ a day more. That is said
    as not known, and is no alarm."""
    rc = cfg.resources
    try:
        empty = credit_empty_since(store, ledger)
        if empty is not None:
            who = "OpenRouter" if _through_openrouter() else "the Anthropic API"
            return Resource("anthropic_credit", 0.0, "usd", None, "crit",
                            f"empty since {time.strftime('%H:%M UTC on %-d %b', time.gmtime(empty))}: {who} "
                            "refuses the agents for want of credit, so nothing is analysed until you "
                            f"{credit_top_up()}. Then {credit_fix()}")  # fmt: skip
        rec = store.coverage(CREDIT_KEY)
        credit = float(rec.get("usd", rc.anthropic_credit_usd))
        set_at = float(rec.get("set_at", 0))
        spent = ledger.credit_since(set_at)  # what left the account: through OpenRouter, without the buying fee
        if credit > 0 and spent > credit * CREDIT_OVERRUN:
            return Resource("anthropic_credit", None, "usd", None, "unknown",
                            f"not known: {spent:.0f} $ spent against {credit:.0f} $ recorded, and the agents were "
                            f"answered all the same, so the account held more than was recorded; {credit_fix()}")  # fmt: skip
        left = max(0.0, credit - spent)
        left_pct = 100.0 * left / credit if credit > 0 else None
        per_day = max(ledger.credit_since(now - 7 * 86400) / 7.0, ledger.credit_since(now - 86400))
        days_left = (left / per_day) if per_day > 0 else None
        daily = effective_daily_usd(cfg, OperatorPaths(Path(cfg.storage.db_path).resolve().parent))
        status = _pct_left_status(left_pct, rc.anthropic_alert_pct)
        if days_left is not None and days_left <= rc.anthropic_min_days:
            status = "crit"
        msg = (
            f"{left:.0f} $ of {credit:.0f} $ left"
            + (f", about {days_left:.1f} days at the current pace" if days_left is not None else "")
            + (f", {left / daily:.1f} days if the agents spend their whole {daily:g} $ a day" if daily > 0 else "")
            + f". After a top-up, {credit_fix()}"
        )
        return Resource(
            "anthropic_credit", round(left, 2), "usd", round(credit * rc.anthropic_alert_pct / 100, 2), status, msg
        )
    except Exception as exc:  # noqa: BLE001
        return Resource("anthropic_credit", None, "usd", None, "unknown", f"could not compute: {exc}")


def credit_reply(cfg: Config, store: Store, ledger: Ledger, args: list[str], now: float) -> str:
    """The owner's /credit: alone it says the count; with a number of dollars it records what is on the account now."""
    if not args:
        return "credit: " + credit_row(cfg, store, ledger, now).message
    try:
        usd = float(args[0].replace("$", "").replace(",", ""))
        if not 0 <= usd < 1e7:
            raise ValueError(args[0])
    except ValueError:
        return (f"credit not changed: {args[0][:20]!r} is not a number of dollars. Send /credit 180 when "
                f"{_credit_page()} shows 180 $.")  # fmt: skip
    store.set_coverage(CREDIT_KEY, {"usd": usd, "set_at": now})
    who = "OpenRouter" if _through_openrouter() else "Anthropic"
    return (f"recorded: {usd:g} $ on the {who} account as of now. The agents' spend counts down from it; you hear "
            "when it runs low.")  # fmt: skip


def read_network_links() -> str:
    """`networkctl list` as the machine prints it: one line a link, its setup state last (no root needed)."""
    import subprocess

    cmd = ["networkctl", "list", "--no-legend", "--no-pager"]
    return subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10).stdout


def network_row(links: Callable[[], str]) -> Resource:
    """The machine's network setup. 11 Nov 2026 07:14 UTC: during a freeze the network manager (systemd-networkd)
    gave up on the network card ("enp1s0: Failed") and stopped renewing the server's address; it ran out at 00:31 the
    next night and the server had no internet for twelve hours while it kept running. A link whose setup has failed
    is critical from the moment it fails, hours before the address goes."""
    try:
        rows = [line.split() for line in links().splitlines() if line.split()]
    except Exception as exc:  # noqa: BLE001 - no networkctl on this machine, or it did not answer
        why = f"networkctl could not be read: {type(exc).__name__}: {exc}"
        return Resource("network", None, "", None, "unknown", why)
    if not rows:
        return Resource("network", None, "", None, "unknown", "networkctl listed no link")
    failed = [r[1] for r in rows if len(r) >= 5 and r[-1] == "failed"]
    if not failed:
        return Resource("network", len(rows), "links", None, "ok", "the network setup is in order")
    names = ", ".join(failed)
    return Resource(
        "network", len(failed), "links", 0, "crit",
        f"the server's network setup has failed (its network manager gave up on {names}): its internet address is no "
        "longer renewed and is lost within a day, as on 12 Nov 00:31 UTC. Restart the server in BitLaunch (Restart, "
        f"never Rebuild), or run on it: sudo networkctl reconfigure {failed[0]}",
    )  # fmt: skip


def check_resources(cfg: Config, store: Store, snap: Snapshot, ledger: Ledger, *, disk_free_gb: Callable[[], float],
                    birdeye_credits: Callable[[], dict] | None, now: float,
                    network_links: Callable[[], str] | None = None) -> list[Resource]:  # fmt: skip
    rc = cfg.resources
    out: list[Resource] = []

    out.append(credit_row(cfg, store, ledger, now))

    # Birdeye compute units for the billing cycle; no row while it is switched off (config.provider_on, 20 Nov).
    if not cfg.birdeye.enabled:
        pass
    elif birdeye_credits is None:
        out.append(Resource("birdeye_cu", None, "cu", None, "unknown", "BIRDEYE_API_KEY not set or not probed"))
    else:
        try:
            cr = birdeye_credits()
            used, remaining = int(cr.get("used", 0)), int(cr.get("remaining", 0))
            total = used + remaining
            left_pct = 100.0 * remaining / total if total > 0 else None
            out.append(Resource("birdeye_cu", remaining, "cu", round(total * rc.birdeye_alert_pct / 100), _pct_left_status(left_pct, rc.birdeye_alert_pct),
                                f"{remaining:,} of {total:,} CU left this cycle. Plan page: https://bds.birdeye.so (Package)"))  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            out.append(Resource("birdeye_cu", None, "cu", None, "unknown", f"birdeye credits probe failed: {exc}"))

    # Monthly counters we keep ourselves (SolanaTracker's only while it is switched on: config.provider_on, 20 Nov).
    counters = [
        (
            "solanatracker_requests",
            "solanatracker",
            rc.solanatracker_month_requests,
            rc.solanatracker_alert_pct,
            "requests",
            "https://www.solanatracker.io/data-api",
        ),
        ("vybe_credits", "vybe", rc.vybe_month_credits, rc.vybe_alert_pct, "credits", "https://vybe.fyi"),
    ]
    for name, provider, total, alert_pct, unit, url in [c for c in counters
                                                         if c[1] != "solanatracker" or cfg.solanatracker.enabled]:  # fmt: skip
        try:
            used = store.usage_month(provider, now)
            left = max(0, total - used)
            left_pct = 100.0 * left / total if total > 0 else None
            out.append(Resource(name, left, unit, round(total * alert_pct / 100), _pct_left_status(left_pct, alert_pct),
                                f"{left:,} of {total:,} {unit} left this month. Upgrade or wait for the cycle: {url}"))  # fmt: skip
        except Exception as exc:  # noqa: BLE001
            out.append(Resource(name, None, unit, None, "unknown", f"counter failed: {exc}"))

    # SOL float: what the book could use plus a margin.
    try:
        b = cfg.book
        live = b.enabled
        try:
            from ..book.switch import book_flag

            live = live or book_flag(OperatorPaths(Path(cfg.storage.db_path).resolve().parent)).exists()
        except Exception:  # noqa: BLE001
            pass
        # the fill the book can actually use (an arm whose fee cannot stay under max_fee_share is skipped)
        from ..book.policy import usable_arms

        fills = list(usable_arms(b).values())
        needed = (b.max_wallets * max(fills) * 2 + rc.sol_float_margin) if live and fills else rc.sol_float_margin
        eq = snap.equity
        if eq is None:
            out.append(Resource("sol_float", None, "sol", needed, "unknown", "wallet balance not read this round"))
        else:
            # what is there and what the book could use, for the agents and the live page. Never an alert and never a
            # request: the wallet's SOL is the owner's to move (9 Nov: "Don't flag I constantly take out and put money
            # on there"; until 11 Nov the round still asked for SOL by Telegram every midnight)
            msg = f"{eq:.3f} SOL in the wallet; {needed:.2f} would cover every sniper the book can arm"
            out.append(Resource("sol_float", round(eq, 4), "sol", needed, "ok", msg))
    except Exception as exc:  # noqa: BLE001
        out.append(Resource("sol_float", None, "sol", None, "unknown", f"could not compute: {exc}"))

    # The tape (7 Nov 14:03-15:48: it recorded nothing for 1 h 45 min and only the operator's model would have said so,
    # while the API refused it): code says it, once a day like every critical row.
    refused = provider_refusal(store, now)
    tape_refused = open_refusal(store, TAPE_REFUSED_KEY, now, REFUSAL_CURRENT_SECS) is not None
    if snap.tape_last_ts is None:
        out.append(Resource("tape", None, "s", TAPE_ALERT_SECS, "unknown", "the tape's newest trade could not be read"))
    else:
        lag = max(0.0, now - float(snap.tape_last_ts))
        late = lag > TAPE_ALERT_SECS
        if late and tape_refused:  # the cause is known and is no restart's to fix (the helius row says it)
            why = f"the tape is {lag / 60:.0f} min behind: Helius refuses the recorder (the helius row)"
        elif late:
            why = (f"the tape is {lag / 60:.0f} min behind: nothing new is recorded. The recorder opens a new "
                   "connection after 3 minutes of refused writes and restarts itself after 10; if it stays behind, "
                   "`snipetech remote update` restarts it")  # fmt: skip
        else:
            why = f"the tape is {lag:.0f} s behind"
        out.append(Resource("tape", round(lag), "s", TAPE_ALERT_SECS, "crit" if late else "ok", why))
    if refused is not None:  # a row of its own: said once a day apart from the tape's (run.py: "resource <name>")
        out.append(refused)
    oktech_refused = oktech_refusal(store, now)
    if oktech_refused is not None:
        out.append(oktech_refused)
    # When each paid plan ends: a reminder before, as the owner recorded it (/renew).
    out.extend(renewal_rows(store, now))
    copy_row = db_copy_row(cfg.storage.db_path, now)  # the copy labs read: how old, how slow (never an alert)
    if copy_row is not None:
        out.append(copy_row)

    # The machine's network setup: critical from the moment its manager gives up on a link.
    out.append(network_row(network_links or read_network_links))

    # Disk.
    try:
        free = float(disk_free_gb())
        status = "crit" if free < rc.disk_alert_gb else ("warn" if free < rc.disk_alert_gb * 1.5 else "ok")
        out.append(Resource("disk", round(free, 1), "gb", rc.disk_alert_gb, status,
                            f"{free:.1f} GB free. Resize to the 320 GB plan on Vultr (Resize tab) in a quiet hour" if status != "ok" else f"{free:.1f} GB free"))  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        out.append(Resource("disk", None, "gb", rc.disk_alert_gb, "unknown", f"disk probe failed: {exc}"))

    # Failed sends by the wallet in the last hour (OKTech duplicates and real failures).
    f = snap.failed_tx_1h
    if f is None:
        out.append(
            Resource(
                "oktech_failures",
                None,
                "tx",
                rc.oktech_fail_alert,
                "unknown",
                "could not read the wallet's recent transactions",
            )
        )
    else:
        # OKTech sends every sell 2-3 times and the losers fail (pump error 6023): two failures per landed sell in the
        # hour are its normal fan-out; only what goes beyond that is alarming
        sells = 0
        if cfg.operator.wallet:
            try:
                sells = len(
                    store.fills(wallet=cfg.operator.wallet, since_ts=int(now - 3600), side="sell", limit=10_000)
                )
            except Exception:  # noqa: BLE001
                sells = 0
        # the measure stage may not have written this hour's sells yet: half the landed transactions are sells too
        landed_sells = (snap.ok_tx_1h or 0) // 2 if getattr(snap, "ok_tx_1h", None) is not None else 0
        unpaid, unread = _failed_by_the_wallets_sol(cfg, store, snap, f, now)
        excess = f - 2 * max(sells, landed_sells) - unpaid - unread
        status = "crit" if excess >= rc.oktech_fail_alert else ("warn" if f > 0 else "ok")
        known = []
        if unpaid:
            known.append(f"{unpaid} buy{'' if unpaid == 1 else 's'} the wallet could not pay")
        if unread:
            known.append(f"{unread} not read by the measure stage yet")
        if sells:
            known.append(f"{sells} sells landed (OKTech's sell twins account for up to {2 * sells})")
        if status == "ok":
            msg = "no failed transactions in the last hour"
        elif known:
            msg = (f"{f} failed transactions in the last hour: " + ", ".join(known) if unpaid or unread else
                   f"{f} failed transactions in the last hour, {known[0]}")  # fmt: skip
            msg += "; the rest needs a look: check OKTech sells and the send count" if status == "crit" else ""
        else:
            msg = f"{f} failed transactions in the last hour; check OKTech sells and the send count"
        out.append(Resource("oktech_failures", f, "tx", rc.oktech_fail_alert, status, msg))
    return out


MEASURE_FRESH_SECS = 900.0  # the measure stage runs every five minutes: a last run older than this reads nothing


def _failed_by_the_wallets_sol(cfg: Config, store: Store, snap: Snapshot, failed: int, now: float) -> tuple[int, int]:
    """Of the wallet's `failed` transactions in the last hour: (how many are buys the wallet's own SOL could not pay,
    by the cause the measure stage wrote in fills; how many the measure stage has not read yet and are most likely
    more of the same). Neither raises the alert.

    11 Nov 22:15 UTC: three copies had failed within the hour because a curve buy is paid whole in plain SOL and the
    wallet's SOL was wrapped; this check paged the owner to "check OKTech sells and the send count". Nothing was
    wrong with either, and what the wallet holds is the owner's (9 Nov: never flagged). The second number is for a
    leader's burst (five buys in four minutes): the sends are on the chain before the measure stage, which starts on
    the round's own minute, has written their causes. They wait one round, and only while the round's snapshot says
    the wallet cannot pay a buy on a venue and the measure stage is running; once read, they count for what they are."""
    if not cfg.operator.wallet:
        return 0, 0
    from ..measure.run import LAST_KEY, is_unpaid
    from .prompts import buys_payable

    try:
        rows = store.fills(wallet=cfg.operator.wallet, since_ts=int(now - 3600), side="failed", limit=10_000)
        unpaid = min(int(failed), sum(1 for r in rows if is_unpaid(r.get("exit_reason"))))
        payable = buys_payable(snap.native_sol, snap.wsol, getattr(snap, "book_fill_sol", None))
        short = payable is not None and min(payable["curve"], payable["pumpswap"]) == 0
        last = float(store.coverage(LAST_KEY).get("ts") or 0)
        reading = now - last <= MEASURE_FRESH_SECS
        unread = max(0, int(failed) - len(rows)) if short and reading else 0
    except Exception:  # noqa: BLE001 - a read that fails explains nothing: the alert stands as it was
        return 0, 0
    return unpaid, unread


# The provider turning our key away, as the recorder wrote it down (live.REFUSED_KEY). Current while its last refusal
# is under REFUSAL_CURRENT_SECS old and it was not cleared since; an alert once it has lasted REFUSAL_ALERT_SECS (10 Nov:
# "Unable to verify project" came and went within minutes before it held for 33). Its own name: from 20 Nov 00:24 it
# was called REFUSED_KEY too, which replaced the agents' credit record above, and Helius's suspension was read as the
# agents refused for want of credit (a false alert to the owner at 00:31, agent turns held until 01:31). The RPC's and
# OKTech's refusals are written by the round and the one-minute watch (operator/refusals.py), current for longer: the
# round reads every 15 minutes.
TAPE_REFUSED_KEY = "tape:refused"
REFUSAL_CURRENT_SECS = 600
REFUSAL_ALERT_SECS = 300


def _refusal_row(name: str, records: list[tuple[str, dict]], now: float) -> tuple[float, str, str] | None:
    """(how long, since when as HH:MM UTC, the parts refused with their words) for the open records, or None."""
    if not records:
        return None
    since = min(float(rec["since"]) for _, rec in records)
    parts = "; ".join(f'{what}: "{rec.get("why") or "refused"}"' for what, rec in records)
    return max(0.0, now - since), time.strftime("%H:%M UTC", time.gmtime(since)), parts


def provider_refusal(store: Store, now: float) -> Resource | None:
    """The `helius` row while Helius turns our key away (19 Nov 2026 23:11 UTC: "Project is suspended": the tape
    recorded nothing and every RPC call was refused, and the round said only that the tape was behind and that a
    restart would help, which it cannot): which part (the tape's stream, the RPC), since when, the provider's own
    words and the one place it is fixed. None when nothing is refused now or the records cannot be read."""
    tape = open_refusal(store, TAPE_REFUSED_KEY, now, REFUSAL_CURRENT_SECS)
    rpc = open_refusal(store, RPC_REFUSED_KEY, now)
    got = _refusal_row("helius", [(w, r) for w, r in (("the tape's stream", tape), ("the RPC", rpc)) if r], now)
    if got is None:
        return None
    lasted, when, parts = got
    lost = (("the tape records nothing", tape), ("no job can read the chain or the wallet", rpc))
    effects = " and ".join(what for what, refused in lost if refused)
    msg = (f"Helius refuses our key since {when} ({lasted / 60:.0f} min), {parts}. {effects[0].upper() + effects[1:]}. "
           "Nothing on the server can fix it: log in at dashboard.helius.dev, Billing (most likely the plan's paid "
           "period ended, as on 19 Nov; else a payment that did not go through, the month's credits used up, or a "
           "changed key)" + (". The recorder lets itself back in within a minute of the fix; what it cannot replay "
                             "stays a hole in the tape" if tape else ""))  # fmt: skip
    status = "crit" if lasted >= REFUSAL_ALERT_SECS else "warn"
    return Resource("helius", round(lasted), "s", REFUSAL_ALERT_SECS, status, msg)


def oktech_refusal(store: Store, now: float) -> Resource | None:
    """The `oktech_key` row while OKTech turns our key away (a lapsed plan: its "VIP expiry"). OKTech runs our exits,
    so a sniper holding a coin may not sell; until 20 Nov the round's failed read only woke an agent. None when nothing
    is refused now."""
    rec = open_refusal(store, OKTECH_REFUSED_KEY, now)
    got = _refusal_row("oktech_key", [("its answer", rec)] if rec else [], now)
    if got is None:
        return None
    lasted, when, _parts = got
    msg = (f'OKTech refuses our key since {when} ({lasted / 60:.0f} min): "{rec.get("why") or "refused"}". The book '
           "cannot see, arm or stop its snipers, and a sniper that holds a coin may not sell (/sell <coin> still sells "
           "through Helius). Check the plan (its VIP expiry) on OKTech's site, or OKS_KEY on the server")  # fmt: skip
    status = "crit" if lasted >= REFUSAL_ALERT_SECS else "warn"
    return Resource("oktech_key", round(lasted), "s", REFUSAL_ALERT_SECS, status, msg)


# -- the end date of each paid plan (20 Nov 2026: Helius's plan ran out unseen on 19 Nov, "my subscription ran out") ---
# No provider says when its plan ends (no endpoint we call returns it), so the owner records each date (/renew helius
# 2026-12-20) and the round turns it into a reminder: shown from RENEW_WARN_DAYS out, sent once a day from
# RENEW_ALERT_DAYS out. A date that passed while the provider kept answering was a renewal: it moves on a month
# (RENEW_GRACE_SECS after it, so a lapse that comes a few hours late is still seen as one, by its refusal row).
RENEWALS_KEY = "resources:renewals"
RENEW_WARN_DAYS = 7
RENEW_ALERT_DAYS = 3
RENEW_GRACE_SECS = 86400
RENEW_MAX_DAYS = 400
# name, the plan, where it is renewed, what stops when it lapses
RENEW_SERVICES: dict[str, tuple[str, str, str, str]] = {
    "helius": ("Helius", "Helius plan", "dashboard.helius.dev, Billing", "the tape and every chain read stop"),
    "server": (
        "The server",
        "The server's plan",
        "BitLaunch, Billing (add balance)",
        "everything stops, and a lapsed server can be deleted with its database",
    ),
    "oktech": ("OKTech", "OKTech plan", "OKTech's site (VIP)", "no sniper can buy or sell"),
    "gmgn": ("GMGN", "GMGN plan", "gmgn.ai (API)", "wallet finding loses its best source"),
    "birdeye": ("Birdeye", "Birdeye plan", "bds.birdeye.so (Package)", "its wallet finding and trending feed stop"),
    "solanatracker": (
        "SolanaTracker",
        "SolanaTracker plan",
        "solanatracker.io/data-api",
        "its feeds and leaderboard stop",
    ),
}
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")  # fmt: skip


def parse_day(words: list[str], now: float) -> float | None:
    """00:00 UTC of the day `words` name, as a billing page writes it: 2026-12-20, 20 Dec, Dec 20, 2026, December
    20th. A day without its year is its next one from today. None for anything else."""
    import calendar
    import datetime as dt
    import re

    text = " ".join(words).replace(",", " ").strip().lower()
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    year = month = day = None
    if iso:
        year, month, day = (int(g) for g in iso.groups())
    else:
        for tok in text.split():
            tok = tok.rstrip(".")
            num = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?|(\d{4})", tok)
            if num and num.group(2):
                if year is not None:
                    return None
                year = int(num.group(2))
            elif num:
                if day is not None:
                    return None
                day = int(num.group(1))
            elif len(tok) >= 3 and any(m.startswith(tok) for m in _MONTHS) and month is None:
                month = next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(tok))
            else:
                return None
    if month is None or day is None:
        return None
    today = dt.datetime.fromtimestamp(now, dt.UTC).date()
    try:
        when = dt.date(year if year is not None else today.year, month, day)
        if year is None and when < today:
            when = dt.date(today.year + 1, month, day)
    except ValueError:
        return None
    return float(calendar.timegm(when.timetuple()))


def _day(ts: float) -> str:
    return time.strftime("%-d %b %Y", time.gmtime(ts))


def _days_word(due: float, now: float) -> str:
    n = int(due // 86400) - int(now // 86400)
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    return f"in {n} days" if n > 0 else f"{-n} day{'s' if n < -1 else ''} ago"


def _next_month(ts: float) -> float:
    """The same day and time a month later (the 31st of a short month: its last day)."""
    import calendar

    t = time.gmtime(ts)
    year, month = (t.tm_year + 1, 1) if t.tm_mon == 12 else (t.tm_year, t.tm_mon + 1)
    day = min(t.tm_mday, calendar.monthrange(year, month)[1])
    return float(calendar.timegm((year, month, day, t.tm_hour, t.tm_min, t.tm_sec)))


def _refused(service: str, store: Store, now: float) -> bool:
    """Whether the service turns our key away now, where the code can tell (Helius, OKTech)."""
    if service == "helius":
        return provider_refusal(store, now) is not None
    if service == "oktech":
        return oktech_refusal(store, now) is not None
    return False


def _helius_estimate(store: Store, now: float) -> tuple[float, float] | None:
    """(the estimated end, when Helius let us back in) with no date recorded: a month on, and on each month after,
    from the last suspension that was cleared (the plan renewed). None when none was."""
    try:
        rec = store.coverage(TAPE_REFUSED_KEY) or {}
    except Exception:  # noqa: BLE001
        return None
    cleared = rec.get("cleared")
    if not cleared or "suspended" not in str(rec.get("why") or "").lower():
        return None
    due = _next_month(float(cleared))
    while due + RENEW_GRACE_SECS <= now:
        due = _next_month(due)
    return due, float(cleared)


def renew_reply(store: Store, args: list[str], now: float) -> str:
    """The owner's /renew: alone, every plan's end date; `/renew <service> <date>` records one, `/renew <service> off`
    forgets it. Nothing the owner typed is repeated back."""
    dates = dict(store.coverage(RENEWALS_KEY) or {})
    names = ", ".join(RENEW_SERVICES)
    if not args:
        lines = []
        for svc in RENEW_SERVICES:
            due = (dates.get(svc) or {}).get("due")
            est = _helius_estimate(store, now) if svc == "helius" and due is None else None
            if due is not None:
                lines.append(f"{svc}: {_day(float(due))} ({_days_word(float(due), now)})")
            elif est is not None:
                lines.append(f"{svc}: about {_day(est[0])} ({_days_word(est[0], now)}; estimated)")
            else:
                lines.append(f"{svc}: no date")
        return (f"plan end dates (a reminder comes {RENEW_ALERT_DAYS} days before each): " + "; ".join(lines)
                + ". Send /renew <service> <date>, e.g. /renew server 2026-12-01; /renew <service> off forgets one.")  # fmt: skip
    svc = args[0].lower()
    if svc not in RENEW_SERVICES:
        return f"renew not changed: that is not one of {names}."
    name, plan, _where, _effect = RENEW_SERVICES[svc]
    if len(args) == 1:
        due = (dates.get(svc) or {}).get("due")
        said = f"{_day(float(due))} ({_days_word(float(due), now)})" if due is not None else "no date"
        return f"{svc}: {said}. Send /renew {svc} <date> when its plan ends, e.g. /renew {svc} 2026-12-20."
    if args[1].lower() in ("off", "none", "cancel", "cancelled", "forget"):
        dates.pop(svc, None)
        store.set_coverage(RENEWALS_KEY, dates)
        return f"{name}: no end date kept now, so no reminder."
    due = parse_day(args[1:], now)
    if due is None:
        return (f"renew not changed: that is not a day. Send it as the billing page writes it, e.g. /renew {svc} "
                f"2026-12-20 or /renew {svc} 20 Dec.")  # fmt: skip
    if int(due // 86400) < int(now // 86400):
        return f"renew not changed: {_day(due)} has passed. Send the plan's next end date."
    if due - now > RENEW_MAX_DAYS * 86400:
        return f"renew not changed: {_day(due)} is more than a year away."
    dates[svc] = {"due": due, "set_at": now}
    store.set_coverage(RENEWALS_KEY, dates)
    return (f"recorded: {plan} ends {_day(due)} ({_days_word(due, now)}). A reminder comes {RENEW_ALERT_DAYS} days "
            "before; /renew lists every date.")  # fmt: skip


def renewal_rows(store: Store, now: float) -> list[Resource]:
    """One row a plan with an end date (`renew_<service>`): ok, then warn from RENEW_WARN_DAYS out and crit from
    RENEW_ALERT_DAYS out (said once a day by the round, with where to renew and what stops). A date that passed:
    crit while the provider refuses us; else warn for RENEW_GRACE_SECS, then it moves on a month (a renewal) and
    the row says so. Helius with no date recorded: estimated from its last suspension that was cleared."""
    try:
        dates = dict(store.coverage(RENEWALS_KEY) or {})
    except Exception:  # noqa: BLE001
        return []
    out: list[Resource] = []
    for svc, (name, plan, where, effect) in RENEW_SERVICES.items():
        due = (dates.get(svc) or {}).get("due")
        note = ""
        if due is None and svc == "helius":
            est = _helius_estimate(store, now)
            if est is None:
                continue
            due = est[0]
            note = (f"; estimated a month after Helius let us back in on {_day(est[1])}, so send /renew helius "
                    "<date> for the exact day")  # fmt: skip
            plan_ends = f"{plan} ends about"
        elif due is None:
            continue
        else:
            plan_ends = f"{plan} ends"
        due = float(due)
        if now >= due:  # the day has come
            if _refused(svc, store, now):
                out.append(Resource(f"renew_{svc}", round((due - now) / 86400, 1), "days", RENEW_ALERT_DAYS, "crit",
                                    f"{plan} ended {_day(due)} and {name} refuses our key: renew at {where}, then "
                                    f"send /renew {svc} <its next end date>"))  # fmt: skip
                continue
            if now - due < RENEW_GRACE_SECS or note:
                out.append(Resource(f"renew_{svc}", round((due - now) / 86400, 1), "days", RENEW_ALERT_DAYS, "warn",
                                    f"{plan} end date {_day(due)} has passed and nothing refuses us yet: if it was "
                                    f"renewed, nothing to do; else renew at {where}"))  # fmt: skip
                continue
            old, due = due, _next_month(due)
            while due <= now:
                due = _next_month(due)
            dates[svc] = {"due": due, "set_at": now, "moved_from": old}
            with contextlib.suppress(Exception):  # not written: moved on again next round
                store.set_coverage(RENEWALS_KEY, dates)
            out.append(Resource(f"renew_{svc}", round((due - now) / 86400, 1), "days", RENEW_ALERT_DAYS, "ok",
                                f"{plan} ends {_day(due)} ({_days_word(due, now)}; moved on a month from {_day(old)}, "
                                f"since {name} kept working: send /renew {svc} <date> if it differs)"))  # fmt: skip
            continue
        days = int(due // 86400) - int(now // 86400)
        status = "crit" if days <= RENEW_ALERT_DAYS else ("warn" if days <= RENEW_WARN_DAYS else "ok")
        msg = f"{plan_ends} {_day(due)} ({_days_word(due, now)}{note})"
        if status != "ok":
            msg += f": renew at {where}, or {effect}. If it renews by itself, nothing to do"
        out.append(Resource(f"renew_{svc}", round((due - now) / 86400, 1), "days", RENEW_ALERT_DAYS, status, msg))
    return out


# The read-only copy labs, discovery and the events scan read (infra/snapshot_db.py; 20 Nov server audit: 41 to 69
# minutes a copy on 18-20 Nov, longer each day). One is due every SNAPSHOT_EVERY_SECS and takes up to an hour, so it is
# up to about 4 h old in the normal course; older means one was missed. One that took over COPY_STOP_SECS stops the next
# under the operator's --limit-secs 7200 (snapshot_db.FIT_MARGIN), and five hours later the heavy readers fall back to
# the live file. Said to the agents and in /status, never sent: the owner has nothing to do about it.
DB_COPY_OLD_SECS = 5 * 3600
DB_COPY_SLOW_SECS = 75 * 60
DB_COPY_LIMIT_SECS = 7200  # what the operator's stand-in gives `snapshot-db --limit-secs`


def db_copy_row(db_path, now: float) -> Resource | None:
    """The `db_copy` row: how old the copy labs read is and how long the last one took; warn when it is older than a
    missed copy or the copy grows slow, None when there is no copy (a fresh install) or it cannot be read."""
    from ..infra.snapshot_db import FIT_MARGIN, last_copy

    try:
        rec = last_copy(db_path)
    except Exception:  # noqa: BLE001 - said nothing rather than wrong
        return None
    if not rec.get("started"):
        return None
    age = max(0.0, now - float(rec["started"]))
    took = float(rec.get("secs") or 0)
    stop = DB_COPY_LIMIT_SECS / FIT_MARGIN
    said = [f"the copy labs read is {age / 3600:.1f} h old"] + (
        [f"the last one took {took / 60:.0f} min"] if took else []
    )
    status = "ok"
    if age > DB_COPY_OLD_SECS:
        status = "warn"
        said.append("a copy is due every 3 h, so one was missed: labs and discovery read older data")
    if took > stop:
        status = "warn"
        said.append(f"over {stop / 60:.0f} min, the next copy will not start (labs then fall back to the live file)")
    elif took > DB_COPY_SLOW_SECS:
        status = "warn"
        said.append(f"copies are the machine's main load, and one over {stop / 60:.0f} min stops the next")
    return Resource("db_copy", round(age / 3600, 1), "h", DB_COPY_OLD_SECS / 3600, status, "; ".join(said))


def alert_lines(resources: list[Resource]) -> list[str]:
    return [f"{r.name}: {r.message}" for r in resources if r.status == "crit"]


def disk_free_gb_at(path: str) -> float:
    import shutil

    return shutil.disk_usage(path).free / 1e9


def status_line(resources: list[Resource]) -> str:
    return " ".join(f"{r.name}={r.status}" for r in resources)
