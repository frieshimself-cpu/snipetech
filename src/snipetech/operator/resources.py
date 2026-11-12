"""What the owner pays for, checked by code every round: credits, data-API usage, SOL float, disk, failed sends.
A critical row becomes one decision note a day with the exact thing to do."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import Config
from ..store import Store
from .budget import Ledger
from .budget_switch import effective_daily_usd
from .paths import OperatorPaths
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
        rec = store.coverage(CREDIT_KEY)
        credit = float(rec.get("usd", rc.anthropic_credit_usd))
        set_at = float(rec.get("set_at", 0))
        spent = ledger.spent_since(set_at)
        if credit > 0 and spent > credit * CREDIT_OVERRUN:
            return Resource("anthropic_credit", None, "usd", None, "unknown",
                            f"not known: {spent:.0f} $ spent against {credit:.0f} $ recorded, and the agents were "
                            f"answered all the same, so the account held more than was recorded; {CREDIT_FIX}")  # fmt: skip
        left = max(0.0, credit - spent)
        left_pct = 100.0 * left / credit if credit > 0 else None
        per_day = max(ledger.spent_since(now - 7 * 86400) / 7.0, ledger.spent_since(now - 86400))
        days_left = (left / per_day) if per_day > 0 else None
        daily = effective_daily_usd(cfg, OperatorPaths(Path(cfg.storage.db_path).resolve().parent))
        status = _pct_left_status(left_pct, rc.anthropic_alert_pct)
        if days_left is not None and days_left <= rc.anthropic_min_days:
            status = "crit"
        msg = (
            f"{left:.0f} $ of {credit:.0f} $ left"
            + (f", about {days_left:.1f} days at the current pace" if days_left is not None else "")
            + (f", {left / daily:.1f} days if the agents spend their whole {daily:g} $ a day" if daily > 0 else "")
            + f". After a top-up, {CREDIT_FIX}"
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
        return (f"credit not changed: {args[0][:20]!r} is not a number of dollars. Send /credit 180 when the Anthropic "
                "console shows 180 $.")  # fmt: skip
    store.set_coverage(CREDIT_KEY, {"usd": usd, "set_at": now})
    return (f"recorded: {usd:g} $ on the Anthropic account as of now. The agents' spend counts down from it; you hear "
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

    # Birdeye compute units for the billing cycle.
    if birdeye_credits is None:
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

    # Monthly counters we keep ourselves.
    for name, provider, total, alert_pct, unit, url in (
        (
            "solanatracker_requests",
            "solanatracker",
            rc.solanatracker_month_requests,
            rc.solanatracker_alert_pct,
            "requests",
            "https://www.solanatracker.io/data-api",
        ),
        ("vybe_credits", "vybe", rc.vybe_month_credits, rc.vybe_alert_pct, "credits", "https://vybe.fyi"),
    ):
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
    if snap.tape_last_ts is None:
        out.append(Resource("tape", None, "s", TAPE_ALERT_SECS, "unknown", "the tape's newest trade could not be read"))
    else:
        lag = max(0.0, now - float(snap.tape_last_ts))
        late = lag > TAPE_ALERT_SECS
        out.append(Resource("tape", round(lag), "s", TAPE_ALERT_SECS, "crit" if late else "ok",
                            f"the tape is {lag / 60:.0f} min behind: nothing new is recorded. The recorder opens a new "
                            "connection after 3 minutes of refused writes and restarts itself after 10; if it stays behind, "
                            "`snipetech remote update` restarts it" if late else f"the tape is {lag:.0f} s behind"))  # fmt: skip

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


def alert_lines(resources: list[Resource]) -> list[str]:
    return [f"{r.name}: {r.message}" for r in resources if r.status == "crit"]


def disk_free_gb_at(path: str) -> float:
    import shutil

    return shutil.disk_usage(path).free / 1e9


def status_line(resources: list[Resource]) -> str:
    return " ".join(f"{r.name}={r.status}" for r in resources)
