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


def check_resources(cfg: Config, store: Store, snap: Snapshot, ledger: Ledger, *, disk_free_gb: Callable[[], float],
                    birdeye_credits: Callable[[], dict] | None, now: float) -> list[Resource]:  # fmt: skip
    rc = cfg.resources
    out: list[Resource] = []

    # Anthropic credit: what the owner loaded minus what the ledger has spent since they loaded it. The pace is the
    # faster of the week's mean and the last day: once the owner raises the agents' budget (/budget, 7 Nov: 200 $ a day
    # against the 4.44 $ spent on 6 Nov) the spend jumps from one day to the next, and a week's mean hides it for days
    try:
        rec = store.coverage("resources:anthropic_credit")
        credit = float(rec.get("usd", cfg.resources.anthropic_credit_usd))
        set_at = float(rec.get("set_at", 0))
        spent = ledger.spent_since(set_at)
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
            + ". Top up 100 $ at console.anthropic.com, then run: snipetech config set resources.anthropic_credit_usd <new total>"
        )
        out.append(
            Resource(
                "anthropic_credit", round(left, 2), "usd", round(credit * rc.anthropic_alert_pct / 100, 2), status, msg
            )
        )
    except Exception as exc:  # noqa: BLE001
        out.append(Resource("anthropic_credit", None, "usd", None, "unknown", f"could not compute: {exc}"))

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

    # SOL float: what the book needs plus a margin.
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
            status = "crit" if eq < needed else ("warn" if eq < needed * 1.25 else "ok")
            msg = f"{eq:.3f} SOL in the wallet, {needed:.2f} needed" + (
                f"; send {max(0.0, needed - eq):.2f} SOL to {snap.wallet}" if status == "crit" else ""
            )
            out.append(Resource("sol_float", round(eq, 4), "sol", needed, status, msg))
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
        excess = f - 2 * max(sells, landed_sells)
        status = "crit" if excess >= rc.oktech_fail_alert else ("warn" if f > 0 else "ok")
        if status == "ok":
            msg = "no failed transactions in the last hour"
        elif sells:
            msg = (f"{f} failed transactions in the last hour, {sells} sells landed (OKTech's sell twins account for up to {2 * sells})"
                   + ("; the rest needs a look: check OKTech sells and the send count" if status == "crit" else ""))  # fmt: skip
        else:
            msg = f"{f} failed transactions in the last hour; check OKTech sells and the send count"
        out.append(Resource("oktech_failures", f, "tx", rc.oktech_fail_alert, status, msg))
    return out


def alert_lines(resources: list[Resource]) -> list[str]:
    return [f"{r.name}: {r.message}" for r in resources if r.status == "crit"]


def disk_free_gb_at(path: str) -> float:
    import shutil

    return shutil.disk_usage(path).free / 1e9


def status_line(resources: list[Resource]) -> str:
    return " ".join(f"{r.name}={r.status}" for r in resources)
