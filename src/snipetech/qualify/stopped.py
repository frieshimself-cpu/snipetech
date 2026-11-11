"""What the wallets one rule stops have made since it stopped them (audit of 11 Nov, open items 4 and 8).

Two of the verdict rules stop wallets whose copies make money on paper where our buys land. The crowding rule: the
median price gap from the wallet's buy to the third buy after it is over `[qualify] max_gap3_pct` (15 %); it rests on
the live fills of 6-7 Nov, two to four buys back at +18 to +43 % behind a crowded leader. The history rule: the
wallet's own record at the data vendors fails a floor (its trades win under 35 % of the time, mostly). On 11 Nov the
five wallets with the best paper numbers were all stopped by the first, and the one with the most copies by the
second. Nothing said whether either rule costs money, because a wallet they stop gets no forward check.

It needs no new record. A verdict is made from the copies before it, so the copies after a wallet's first verdict
that only such a rule stopped are copies nobody picked on: the forward check a picked wallet gets
(`gates.forward_gate`), read here for the wallets that never get one. One qualify lab over those wallets (the qualify
landing, the measured cost of a transaction, the exit the book sends), then each wallet's copies after that verdict.
The wallets that were picked under the same rules (a verdict that carries a forward check: every in-sample rule
passed) are measured the same way as the reference. Wallets that buy in the same second are one bet: a group's pool
counts such a moment once, at the mean of its copies.

What it cannot say: behind a crowded leader the other copiers sell when we sell, and the paper sale does not see
that. A crowded group that loses here is settled; one that wins here still needs live fills, and the owner's yes.

Research only: it takes a lab slot, writes no verdict and changes none."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ..analysis.lab_lock import LabBusy, lab_lock
from ..analysis.stats import summarize
from ..config import Config
from .gates import tape_hours_after
from .run import CHUNK, LabRunner, default_lab_runner, lab_tx_cost_info, qualify_landing

# the question first, the reference last: with more wallets than a run may measure the reference is what is left out
GROUPS = ("crowding", "history", "history+crowding", "picked")
MAX_WALLETS = 150
MIN_WALLET_COPIES = 5  # copies since its verdict before a wallet counts as up or down


def _group(status: str, n: int | None, mean: float | None, lower: float | None, gap3: float | None,
           reasons: list[str], checked: bool, q) -> tuple[str, str] | None:  # fmt: skip
    """(the group one verdict puts its wallet in, the reason it gives), or None: stopped by another rule, or by
    nothing this study follows."""
    if checked:  # every in-sample rule passed, as the rules are since 7 Nov (it carries a forward check)
        return "picked", next((r for r in reasons if r.startswith(("forward", "holds", "loses"))), "picked")
    crowded = next((r for r in reasons if r.startswith("crowded:")), None)
    if status == "candidate" and crowded:  # the last rule in the chain: everything before it passed
        return "crowding", crowded
    history = next((r for r in reasons if r.startswith("history:")), None)
    if status == "retired" and history:
        # the history rule comes before the tape's: its wallets carry their paper numbers and were never held to them
        if (n or 0) < q.min_n_tape or mean is None or mean < 0 or lower is None or lower <= 0:
            return None
        both = gap3 is not None and gap3 > q.max_gap3_pct
        return ("history+crowding" if both else "history"), history
    return None


def find(store, q) -> list[dict[str, Any]]:
    """Every wallet with a verdict this study follows, as of its first one: {"wallet", "group", "since", "why",
    "at_verdict": {"n", "mean_sol", "lower_sol", "gap3_pct"}}, the question's groups first, then by time."""
    rows = store._q(
        "SELECT wallet, computed_at, status, n_tape, mean_sol, lower_sol, gap3_pct, reasons, forward IS NOT NULL "
        "FROM verdicts WHERE lower_sol > 0 OR forward IS NOT NULL ORDER BY computed_at, id"
    )
    first: dict[str, dict[str, Any]] = {}
    for wallet, at, status, n, mean, lower, gap3, reasons, checked in rows:
        if wallet in first:
            continue
        try:
            why = [str(r) for r in json.loads(reasons or "[]")]
        except ValueError:
            why = []
        found = _group(status, n, mean, lower, gap3, why, bool(checked), q)
        if found is None:
            continue
        first[wallet] = {"wallet": wallet, "group": found[0], "since": int(at), "why": found[1],
                         "at_verdict": {"n": n, "mean_sol": mean, "lower_sol": lower, "gap3_pct": gap3}}  # fmt: skip
    return sorted(first.values(), key=lambda r: (GROUPS.index(r["group"]), r["since"], r["wallet"]))


def _pool(rows: list[dict[str, Any]], conf: float, samples: int) -> dict[str, Any]:
    """One group over its wallets. The pool: every moment a wallet of the group was copied since its verdict, in the
    order they happened, a moment several of them share (the same second) once, at the mean of its copies; `copies`
    counts them all, `moments` what the numbers stand on. And the wallets one by one (a pool is its busiest wallet's
    unless that is said): how many have MIN_WALLET_COPIES copies or more, how many of those are up, their mean of
    means, and the largest single wallet's share of everything won or lost."""
    by_moment: dict[int, list[float]] = {}
    for r in rows:
        for ts, p in r["copies"]:
            by_moment.setdefault(int(ts), []).append(p)
    pool = [sum(ps) / len(ps) for _, ps in sorted(by_moment.items())]
    judged = [r for r in rows if len(r["pnls"]) >= MIN_WALLET_COPIES]
    means = [sum(r["pnls"]) / len(r["pnls"]) for r in judged]
    moved = sum(abs(sum(r["pnls"])) for r in rows)
    st = summarize(pool, conf, samples) if pool else {}
    return {
        "wallets": len(rows), "with_copies": sum(1 for r in rows if r["pnls"]),
        "copies": sum(len(r["pnls"]) for r in rows), "moments": len(pool),
        "mean_sol": st.get("mean"), "lower_sol": st.get("ev_lower"), "win_rate": st.get("win_rate"),
        "total_sol": st.get("total"),
        "wallets_judged": len(judged), "wallets_up": sum(1 for m in means if m > 0),
        "wallet_mean_sol": sum(means) / len(means) if means else None,
        "top_wallet_share": max(abs(sum(r["pnls"])) for r in rows) / moved if moved > 0 else None,
        "at_verdict_mean_sol": (sum(r["at_verdict"]["mean_sol"] or 0.0 for r in rows) / len(rows)) if rows else None,
    }  # fmt: skip


def _landing(asked: str | None, qualify: str) -> str:
    """The landing the lab scores at: the qualify lab's, or `asked` ("+Nx": behind the N-th buy after the wallet's,
    across slots) when that is no closer. A closer one is refused: paper at a better place than ours is not evidence."""
    if asked is None:
        return qualify
    m = re.fullmatch(r"\+(\d+)x", asked)
    if not m or int(m.group(1)) < int(qualify.strip("+x")):
        raise ValueError(f"landing {asked!r}: write +Nx with N at least {qualify.strip('+x')} (the qualify lab's "
                         f"{qualify}; nothing is scored closer than our buys land)")  # fmt: skip
    return asked


def stopped_wallets(svc, cfg: Config, jobs_dir: Path, now: float, lab_runner: LabRunner | None = None,
                    max_wallets: int = MAX_WALLETS, wait: bool = True, landing: str | None = None) -> dict[str, Any]:  # fmt: skip
    """The study: `groups` (each of GROUPS pooled), `rows` (each wallet: its verdict's numbers and its copies since)
    and what the lab was (`landing`, `per_tx_sol`). `max_wallets`: how many the lab measures, the reference cut first
    (`left_out` says how many). `wait` False: with every lab slot held, {"deferred": True, "reason"} instead.
    `landing`: score further back than the qualify lab does ("+5x": two more buys between the wallet and us), to see
    what is left when faster bots land ahead; the verdicts' own numbers (`at_verdict`) stay the qualify landing's."""
    store, q = svc.store, cfg.qualify
    landing = _landing(landing, qualify_landing(store, q, now=now))
    found = find(store, q)
    asked, left_out = found[: max(0, int(max_wallets))], max(0, len(found) - max(0, int(max_wallets)))
    cost = lab_tx_cost_info(store, cfg)
    out: dict[str, Any] = {"now": int(now), "landing": landing, "per_tx_sol": cost["per_tx_sol"],
                           "per_tx_source": cost["per_tx_source"], "wallets": len(asked), "left_out": left_out,
                           "min_wallet_copies": MIN_WALLET_COPIES}  # fmt: skip
    if not asked:
        return out | {"groups": {}, "rows": []}
    runner = lab_runner or default_lab_runner(svc, cfg)
    spans = getattr(svc, "tape_store", store).tape_spans()
    moments: dict[str, list] = {}
    started = time.monotonic()
    try:
        with lab_lock(Path(jobs_dir).parent, wait=wait):
            for i in range(0, len(asked), CHUNK):
                chunk = [r["wallet"] for r in asked[i : i + CHUNK]]
                for row in runner(chunk, landing):
                    if row.get("wallet"):
                        moments[row["wallet"]] = row.get("moments") or []
    except LabBusy as exc:
        return out | {"deferred": True, "reason": f"lab slot: {exc}"}
    out["lab_secs"] = round(time.monotonic() - started, 1)
    conf, samples = cfg.verdict.confidence, cfg.verdict.bootstrap_samples
    rows = []
    for r in asked:
        copies = [(float(ts), float(p)) for ts, p in moments.get(r["wallet"], []) if float(ts) > r["since"]]
        pnls = [p for _, p in copies]
        st = summarize(pnls, conf, samples) if pnls else {}
        after = ({"n": len(pnls), "mean_sol": st["mean"], "lower_sol": st["ev_lower"], "win_rate": st["win_rate"],
                  "total_sol": st["total"]} if pnls else {"n": 0})  # fmt: skip
        rows.append(r | {"after": after, "tape_hours": round(tape_hours_after(spans, r["since"], now), 1),
                         "pnls": pnls, "copies": copies})  # fmt: skip
    out["groups"] = {g: _pool([r for r in rows if r["group"] == g], conf, samples)
                     for g in GROUPS if any(r["group"] == g for r in rows)}  # fmt: skip
    out["rows"] = [{k: v for k, v in r.items() if k not in ("pnls", "copies")} for r in rows]
    return out


def stopped_wallets_cli(cfg: Config, max_wallets: int, jobs_dir: Path, landing: str | None = None) -> dict[str, Any]:
    from ..service import Service

    svc = Service(cfg)
    try:
        return stopped_wallets(svc, cfg, jobs_dir, time.time(), max_wallets=max_wallets, landing=landing)
    finally:
        svc.store.close()
