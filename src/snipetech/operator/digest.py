"""The daily digest: one Telegram message after `digest_hour_utc` with the numbers the owner would otherwise ask for.
Sent by code, never by the model; a failed send is retried next round because the marker is written only after it."""

from __future__ import annotations

import time

from ..positions import is_active
from ..store import Store
from .budget import Ledger
from .paths import OperatorPaths
from .snapshot import Snapshot

MAX_CHARS = 1500
LAST_KEY = "digest:last"


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _marker(paths: OperatorPaths, now: float):
    return paths.root / f"digest-{_day(now)}.sent"


def digest_due(paths: OperatorPaths, now: float, hour_utc: int) -> bool:
    secs_into_day = now - int(now // 86400) * 86400
    return secs_into_day >= int(hour_utc) * 3600 and not _marker(paths, now).exists()


def mark_sent(paths: OperatorPaths, now: float, store: Store | None = None, snap: Snapshot | None = None) -> None:
    paths.ensure()
    _marker(paths, now).write_text(str(int(now)))
    if store is not None and snap is not None and snap.equity is not None:
        try:
            store.set_coverage(LAST_KEY, {"ts": int(now), "equity": float(snap.equity)})
        except Exception:  # noqa: BLE001 - the comparison value is a nicety, the digest is not
            pass


def _notes_last_24h(paths: OperatorPaths, now: float) -> tuple[int, str | None]:
    """How many note lines were written in the last 24 h and the last decision line among them."""
    try:
        lines = paths.notes.read_text().splitlines()
    except OSError:
        return 0, None
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 86400))
    recent = [ln for ln in lines if ln.startswith("- ") and ln[2:22] >= since]
    decisions = [ln for ln in recent if "[decision]" in ln]
    last = decisions[-1].split("] ", 1)[-1] if decisions else None
    return len(recent), last


def digest_text(
    snap: Snapshot, resources: list[dict], ledger: Ledger, store: Store, now: float, *, paths: OperatorPaths
) -> str:
    lines = [f"digest {_day(now)}"]
    # Wallet and its 24 h change against yesterday's digest value.
    if snap.equity is None:
        lines.append("wallet: unknown (balances not read)")
    else:
        change = ""
        try:
            last = store.coverage(LAST_KEY)
            if last.get("equity") is not None:
                change = f" ({snap.equity - float(last['equity']):+.3f} since the last digest)"
        except Exception:  # noqa: BLE001
            change = ""
        lines.append(f"wallet: {snap.equity:.3f} SOL{change}, {len(snap.held)} coin(s) held")
    if snap.snipers is None:
        lines.append("snipers: unknown (OKTech not answering)")
    else:
        running = sum(1 for s in snap.snipers if is_active(s.get("status")))
        lines.append(f"snipers: {running} running of {len(snap.snipers)}")
    # Spend.
    try:
        lines.append(
            f"claude: {ledger.spent_today(now):.2f} $ today, {ledger.spent_since(now - 7 * 86400):.2f} $ in 7 days"
        )
    except Exception as exc:  # noqa: BLE001
        lines.append(f"claude spend: could not read ({type(exc).__name__})")
    # Resources not ok.
    bad = [r for r in resources if r.get("status") not in ("ok", None)]
    if bad:
        lines.append(
            "resources: " + "; ".join(f"{r.get('name')} {r.get('status')}: {r.get('message', '')}" for r in bad[:4])
        )
    else:
        lines.append("resources: all ok")
    # The owner's automatic test: the day's blocks live here, never a Telegram line per block (7 Nov ~12:10 UTC).
    try:
        from .autotest import digest_line

        auto = digest_line(paths, now)
        if auto:
            lines.append(auto)
    except Exception as exc:  # noqa: BLE001 - the digest still goes out
        lines.append(f"automatic test: its record could not be read ({type(exc).__name__})")
    # Notes.
    n, last_decision = _notes_last_24h(paths, now)
    if n == 0:
        lines.append("operator: no notes in 24 h")
    else:
        lines.append(f"operator: {n} note(s) in 24 h" + (f"; last decision: {last_decision}" if last_decision else ""))
    # Phase 2 tables, when they exist.
    counts = getattr(store, "verdict_counts", None)
    if callable(counts):
        try:
            vc = counts()
            if vc:
                lines.append("candidates: " + ", ".join(f"{k} {v}" for k, v in sorted(vc.items())))
        except Exception:  # noqa: BLE001
            pass
    text = "\n".join(lines)
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"
