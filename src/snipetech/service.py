"""High-level operations shared by the CLI, HTTP API, live watcher and Claude agent."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .analysis.audit import audit_report
from .analysis.copytrade import copy_report
from .analysis.deployer import deployer_report
from .analysis.launches import build_context
from .analysis.optimize import optimize_exits, sweep_latency, sweep_sizes
from .analysis.policy import analyzable_launches, discover_wallets, policy_backtest, top_deployers
from .analysis.sniper import sniper_profile, top_snipers
from .analysis.study import collect as collect_study
from .analysis.study import study_report
from .config import Config
from .ingest import Ingestor
from .models import Launch
from .sim.exits import ExitParams
from .solana.base58 import is_pubkey
from .solana.rpc import SolanaRpc
from .store import Store

Progress = Callable[[str], None]


class OverlapError(ValueError):
    """A create refused because an active sniper already copies that leader (OKTech buys once per signal per wallet;
    a second sniper only adds failed sells at its own fee and crossed exits)."""


OVERLAP_HINT = ("one buy per signal per wallet; a second sniper only adds failed sells. Stop and delete that one "
                "first, or pass --allow-overlap for a deliberate test on another OKTech wallet")  # fmt: skip


def _planned_leaders(p) -> set[str]:
    """The wallets a planned sniper would copy (none for a dev or launch sniper)."""
    from .book.policy import copied_leaders

    return copied_leaders({"kind": p.kind, "source": p.source, "payload": p.config})


def _refused(p, leader: str, sid: str) -> dict:
    return {"action": "refused (overlap)", "name": p.name, "id": sid,
            "error": f"sniper {sid} already copies {leader[:6]}…: {OVERLAP_HINT}"}  # fmt: skip


def compact_deployer(report: dict) -> dict:
    v = report.get("verdict", {})
    return {
        "address": report["address"],
        "verdict": v.get("verdict"),
        "score": v.get("score"),
        "ev_sol": v.get("ev_sol"),
        "ev_lower_sol": v.get("ev_lower_sol"),
        "win_rate": v.get("win_rate"),
        "rinse_rate": v.get("rinse_rate"),
        "dump_rate": v.get("dump_rate"),
        "launches": report.get("launches_analyzed", 0),
        "last_launch_ts": (report.get("cadence") or {}).get("last_launch_ts"),
        "recommended": v.get("recommended"),
        "reasons": v.get("reasons", []),
        "warnings": v.get("warnings", []),
        "funder": (report.get("funder") or {}).get("funder"),
    }


def compact_copy(report: dict) -> dict:
    v = report.get("verdict", {})
    return {
        "address": report["address"],
        "verdict": v.get("verdict"),
        "ev_sol": v.get("ev_sol"),
        "ev_lower_sol": v.get("ev_lower_sol"),
        "win_rate": v.get("win_rate"),
        "positions": v.get("positions", 0),
        "positions_per_day": v.get("positions_per_day"),
        "projected_monthly_sol": v.get("projected_monthly_sol"),
        "max_profitable_latency_slots": v.get("max_profitable_latency_slots"),
        "target_mean_pnl": (report.get("target") or {}).get("mean"),
        "farming_rate": report.get("farming_rate"),
        "recommended": v.get("recommended"),
        "reasons": v.get("reasons", []),
        "warnings": v.get("warnings", []),
    }


class Service:
    def __init__(self, cfg: Config, store: Store | None = None, progress: Progress | None = None):
        self.cfg = cfg
        self.store = store or Store(cfg.storage.db_path)
        self.progress = progress or (lambda msg: None)
        self._rpc: SolanaRpc | None = None
        self._tape_store: Store | None = None

    TAPE_COPY_MAX_AGE_SECS = 2 * 3600

    @property
    def tape_store(self) -> Store:
        """The store the heavy tape readers (labs, the analyst) should read: the hourly read-only copy next to the
        database (`snipetech.ro.db`, written by `snapshot-db`) when it exists and is under two hours old, else the
        live store. Writes (verdicts, coverage, fills) always go to `self.store`."""
        if self._tape_store is None:
            self._tape_store = self.store
            try:
                from .infra.snapshot_db import RO_SUFFIX

                live = Path(self.store.path)
                ro = live.with_name(live.stem + RO_SUFFIX)
                if (
                    self.store.path != ":memory:"
                    and ro.exists()
                    and time.time() - ro.stat().st_mtime < self.TAPE_COPY_MAX_AGE_SECS
                ):
                    self._tape_store = Store(ro)
            except OSError:
                self._tape_store = self.store
        return self._tape_store

    def stop_flag_set(self) -> bool:
        """The owner's /stop flag (operator/STOP next to the database): no start path runs while it exists."""
        try:
            from .operator.paths import OperatorPaths

            return OperatorPaths(Path(self.cfg.storage.db_path).resolve().parent).stop_flag.exists()
        except Exception:  # noqa: BLE001
            return False

    def risk_guard(self, address: str, sol: float | None, held: list[dict] | None):
        """The owner's wallet-wide guard for `address` now (operator/RISK next to the database: no new live trade once
        the day is 1 SOL down or the wallet is under 10 SOL, each stop on unless the owner switched it off), from its
        SOL and wrapped SOL and its coins as read."""
        from .operator import risk_guard
        from .operator.paths import OperatorPaths

        paths = OperatorPaths(Path(self.cfg.storage.db_path).resolve().parent)
        return risk_guard.evaluate(self.store, self.cfg, paths, time.time(), wallet=address, sol=sol, held=held)

    def risk_guard_reads_balance(self) -> bool:
        """Whether the owner's guard needs the wallet's SOL: only while its floor is on (7 Nov ~12:10 UTC the owner
        could switch it off). A guard that needs nothing never says it cannot tell."""
        from .operator import risk_guard
        from .operator.paths import OperatorPaths

        paths = OperatorPaths(Path(self.cfg.storage.db_path).resolve().parent)
        return risk_guard.limits(paths)["floor_sol"] is not None

    def reset_tape_store(self) -> None:
        if self._tape_store is not None and self._tape_store is not self.store:
            self._tape_store.close()
        self._tape_store = None

    @property
    def rpc(self) -> SolanaRpc:
        if self._rpc is None:
            self._rpc = SolanaRpc.from_config(self.cfg)
        return self._rpc

    @property
    def ingestor(self) -> Ingestor:
        return Ingestor(self.store, self.rpc, self.progress)

    async def close(self) -> None:
        if self._rpc is not None:
            await self._rpc.close()
            self._rpc = None
        self.reset_tape_store()

    @staticmethod
    def _check(address: str) -> str:
        address = address.strip()
        if not is_pubkey(address):
            raise ValueError(f"not a Solana address: {address!r}")
        return address

    # -- deployers -------------------------------------------------------------------------

    async def analyze_deployer(
        self,
        address: str,
        refresh: bool = True,
        max_launches: int | None = None,
        family: bool = False,
        optimize: bool = True,
    ) -> dict:
        address = self._check(address)
        limit = max_launches or self.cfg.snipe.max_launches
        window = self.cfg.snipe.analysis_window_secs
        if refresh:
            await self.ingestor.backfill_deployer(address, limit, window)
        funder = None
        if self.cfg.live.funder_lookup:
            funder = self.store.funder(address)
            if refresh and funder is None:
                try:
                    funder = await self.ingestor.find_funder(address)
                except Exception as exc:  # funding lookups are best-effort
                    self.progress(f"funder lookup failed: {exc}")
        closed_before = int(time.time()) - window
        launches = self.store.launches_by_deployer(address, limit, before_ts=closed_before)
        if refresh:
            await self.fingerprint_early_trades(launches)
        launches += [l for l in self.store.launches_by_deployer(address) if l.ts > closed_before]
        label = None
        if family and funder and funder.get("funder"):
            siblings = [w for w in self.store.wallets_funded_by(funder["funder"]) if w != address]
            for sib in siblings:
                if refresh:
                    await self.ingestor.backfill_deployer(sib, limit, window)
                launches += self.store.launches_by_deployer(sib, limit, before_ts=closed_before)
            launches = list({l.mint: l for l in launches}.values())
            label = f"family of {funder['funder']} ({len(siblings) + 1} wallets)"
        report = await asyncio.to_thread(
            deployer_report, self.store, self.cfg, address, launches, funder, label, optimize
        )
        self.store.put_score("deployer", address, compact_deployer(report))
        return report

    def _known_between(self, mint: str, start_ts: int, end_ts: int) -> bool:
        """Is every trade on `mint` between the two times in the store?"""
        cov = self.store.coverage(f"mint:{mint}")
        return cov.get("from_ts", 1 << 62) <= start_ts and cov.get("to_ts", 0) >= end_ts

    async def fingerprint_early_trades(self, launches: list[Launch]) -> int:
        """Make sure creates and the first slots of trades carry transaction fingerprints
        (fees, tips, routers) — needed to see what competing snipers paid."""
        horizon = self.cfg.snipe.competition_slots
        sigs: list[str] = []
        for l in launches:
            sigs.append(l.sig)
            sigs += [
                t.sig for t in self.store.trades_for_mint(l.mint, l.ts - 1, l.ts + 60) if t.slot <= l.slot + horizon
            ]
        return await self.ingestor.ensure_tx_meta(list(dict.fromkeys(sigs)))

    def deployer_verdict(self, address: str) -> dict:
        """Fast path for the sniper: cached verdict + list overrides, no RPC."""
        address = self._check(address)
        lists = self.store.lists_for(address)
        cached = self.store.get_score("deployer", address) or {"address": address, "verdict": "UNKNOWN"}
        if "block" in lists:
            cached = cached | {"verdict": "BLOCK", "override": "block list"}
        elif "allow" in lists:
            cached = cached | {"verdict": "ALLOW", "override": "allow list"}
        else:
            funder = self.store.funder(address)
            if cached.get("verdict") in (None, "UNKNOWN") and funder and funder.get("funder"):
                fam = self._family_verdict(funder["funder"], exclude=address)
                if fam:
                    cached = cached | {"family": fam}
        cached["lists"] = lists
        return cached

    def _family_verdict(self, funder: str, exclude: str) -> dict | None:
        if "block" in self.store.lists_for(funder):
            return {"funder": funder, "verdict": "BLOCK", "override": "funder on block list"}
        best = None
        for w in self.store.wallets_funded_by(funder):
            if w == exclude:
                continue
            s = self.store.get_score("deployer", w)
            if s and s.get("verdict") not in (None, "UNKNOWN", "INSUFFICIENT_DATA"):
                if best is None or (s.get("launches") or 0) > (best.get("launches") or 0):
                    best = s | {"sibling": w}
        return {"funder": funder, **best} if best else None

    async def launch_for_mint(self, mint: str) -> Launch | None:
        mint = self._check(mint)
        launch = self.store.launch(mint)
        if launch:
            return launch
        # Walk to the oldest signatures of the mint: the create transaction is first.
        cursor, oldest = None, []
        for _ in range(20):
            page = await self.rpc.get_signatures(mint, before=cursor)
            if not page:
                break
            oldest, cursor = page, page[-1]["signature"]
            if len(page) < 1000:
                break
        if oldest:
            await self.ingestor.ingest(oldest[-10:])
        return self.store.launch(mint)

    async def check_mint(self, mint: str, refresh: bool = False) -> dict:
        launch = await self.launch_for_mint(mint)
        if launch is None:
            return {"mint": mint, "error": "create transaction not found"}
        verdicts = {}
        for addr in launch.deployers():
            if refresh and not self.store.get_score("deployer", addr):
                await self.analyze_deployer(addr)
            verdicts[addr] = self.deployer_verdict(addr)
        return {"mint": mint, "launch": launch.to_dict(), "deployers": verdicts}

    # -- copytrading ------------------------------------------------------------------------

    async def analyze_copy(self, wallet: str, days: float | None = None, refresh: bool = True) -> dict:
        wallet = self._check(wallet)
        days = days or self.cfg.copy.lookback_days
        since = int(time.time() - days * 86400)
        if refresh:
            await self.ingestor.backfill_wallet_trades(wallet, since, self.cfg.copy.max_signatures)
        report = await asyncio.to_thread(copy_report, self.store, self.cfg, wallet, since)
        self.store.put_score("copy", wallet, compact_copy(report))
        return report

    async def follow(self, wallets: list[str], days: float = 2.0, refresh: bool = True,
                     max_secs_per_wallet: float = 900) -> list[dict]:  # fmt: skip
        """Who is worth following: the crowd after their buys, the copy bots racing them (and what they
        pay), and the copy replay at 0 / 1 slot. Each wallet is scored and saved as soon as it is done
        (`list` / `export` see it even if the run is cut short); a wallet whose history takes longer than
        `max_secs_per_wallet` to fetch is scored on what was fetched."""
        from .analysis.follow import follow_rank

        since = int(time.time() - days * 86400)
        wallets = [self._check(w) for w in wallets]
        labels = self.store.labels()  # any labeled kind names the row (kol, wallet, smart, ...)
        rows = []
        for i, w in enumerate(wallets, 1):
            self.progress(f"follow {i}/{len(wallets)}: {labels.get(w) or w[:8]}")
            if refresh:
                try:
                    await asyncio.wait_for(self._follow_fetch(w, since), max_secs_per_wallet)
                except TimeoutError:
                    self.progress(f"{w[:8]}: fetch capped at {max_secs_per_wallet:.0f}s, scoring what was fetched")
                except Exception as exc:  # one wallet's history failing shouldn't stop the ranking
                    self.progress(f"{w[:8]}: {exc}")
            row = (await asyncio.to_thread(follow_rank, self.store, self.cfg, [w], since, labels))[0]
            self.store.put_score("follow", w, row)
            rows.append(row)
        rows.sort(
            key=lambda r: (r.get("copy_pnl_next_slot") is not None, r.get("copy_pnl_next_slot") or -1e9), reverse=True
        )
        return rows

    async def _follow_fetch(self, w: str, since: int) -> None:
        from .analysis.copytrade import build_cases

        await self.ingestor.backfill_wallet_trades(w, since, self.cfg.copy.max_signatures)
        # what the racers paid: fingerprint the buys right after each of its buys
        sigs = []
        for c in (await asyncio.to_thread(build_cases, self.store, w, since))[0]:
            p, i0 = c.path, c.idxs[0]
            sigs += [
                p.sigs[j] for j in range(i0 + 1, p.n) if p.slot[j] <= p.slot[i0] + 1 and p.is_buy[j] and p.users[j] != w
            ][:5]
        await self.ingestor.ensure_tx_meta(list(dict.fromkeys(sigs)))

    async def rank_copy(self, wallets: list[str], days: float | None = None, refresh: bool = True) -> list[dict]:
        out = []
        for w in wallets:
            try:
                out.append(compact_copy(await self.analyze_copy(w, days, refresh)))
            except Exception as exc:
                out.append({"address": w, "verdict": "ERROR", "error": str(exc)})
        out.sort(key=lambda r: r.get("ev_lower_sol") or -1e9, reverse=True)
        return out

    # -- own wallet --------------------------------------------------------------------------

    async def audit(self, wallet: str, days: float = 7.0, refresh: bool = True) -> dict:
        wallet = self._check(wallet)
        since = int(time.time() - days * 86400)
        if refresh:
            ing = self.ingestor
            await ing.backfill_address(wallet, max_sigs=self.cfg.copy.max_signatures, since_ts=since)
            spans: dict[str, list[int]] = {}
            for t in self.store.trades_for_user(wallet, since):
                lo_hi = spans.setdefault(t.mint, [t.ts, t.ts])
                lo_hi[0], lo_hi[1] = min(lo_hi[0], t.ts), max(lo_hi[1], t.ts)
            self.progress(f"auditing {len(spans)} tokens")
            for mint, (lo, hi) in spans.items():
                await ing.backfill_mint_window(mint, lo - 600, hi + 60)
                launch = self.store.launch(mint)
                if launch:
                    await ing.backfill_mint_window(mint, launch.ts - 1, launch.ts + self.cfg.snipe.analysis_window_secs)
        return await asyncio.to_thread(audit_report, self.store, self.cfg, wallet, since)

    # -- other snipers ------------------------------------------------------------------------

    async def profile_sniper(self, wallet: str, days: float = 3.0, refresh: bool = True, deep: int = 0) -> dict:
        """Reverse-engineer a competing sniper's settings and behaviour from its transactions.
        `deep`: also fetch the whole market while it held its last N positions (exit rules
        against the price path, e.g. trailing stops); costs a few hundred transactions each."""
        wallet = self._check(wallet)
        since = int(time.time() - days * 86400)
        if refresh:
            ing = self.ingestor
            window = self.cfg.snipe.analysis_window_secs
            await ing.backfill_address(wallet, max_sigs=self.cfg.copy.max_signatures, since_ts=since)
            spans: dict[str, list] = {}
            for t in self.store.trades_for_user(wallet, since):
                spans.setdefault(t.mint, [t, t])[1] = t
            self.progress(f"locating launches for {len(spans)} tokens")
            snipes = []
            for mint, (first, last) in spans.items():
                launch = self.store.launch(mint)
                if not (launch and self._known_between(mint, launch.ts, first.ts)):
                    # Cheap snipe test: a young token has only a few transactions before this wallet's
                    # first trade, and the create is one of them. Older tokens aren't snipes; for those
                    # the trades just before its entry are enough (who it bought after, entry context).
                    before = await self.rpc.get_signatures(mint, before=first.sig, limit=40)
                    await ing.ingest(before if len(before) < 40 else before[:20])
                    launch = self.store.launch(mint) if len(before) < 40 else None
                if launch and first.ts - launch.ts <= window:
                    # the create through its entry: rank among snipers, insiders, the dev's buy
                    await ing.backfill_mint_window(mint, launch.ts - 1, first.ts)
                    snipes.append((launch, first, last))
            if deep:
                held = sorted((v for v in spans.values() if not v[1].is_buy), key=lambda v: -v[1].ts)[:deep]
                for i, (first, last) in enumerate(held, 1):
                    self.progress(f"deep: market during hold {i}/{len(held)}")
                    await ing.backfill_mint_window(first.mint, first.ts, last.ts)
            await self.fingerprint_early_trades([launch for launch, _, _ in snipes])
            # Dev/bundle sells while it held, from the insiders' own histories: a handful of
            # transactions instead of every trade on a hot token.
            for launch, first, last in snipes:
                if last.is_buy:
                    continue  # no exits to attribute
                for insider in build_context(self.store, launch, self.cfg).insiders - {wallet}:
                    await ing.backfill_address_window(insider, first.ts, last.ts, before=last.sig)
            await ing.ensure_tx_meta([t.sig for t in self.store.trades_for_user(wallet, since)])
            if self.cfg.live.funder_lookup and self.store.funder(wallet) is None:
                try:
                    await ing.find_funder(wallet)
                except Exception as exc:
                    self.progress(f"funder lookup failed: {exc}")
        report = await asyncio.to_thread(sniper_profile, self.store, self.cfg, wallet, since)
        self.store.put_score(
            "sniper",
            wallet,
            {"address": wallet, "config": report.get("config"), "performance": report.get("performance")},
        )
        return report

    def top_snipers(self, days: float = 3.0, min_snipes: int = 5, limit: int = 30) -> list[dict]:
        return top_snipers(self.store, self.cfg, int(time.time() - days * 86400), min_snipes, limit)

    async def profitable_devs(
        self, min_launches: int = 3, days: float = 7.0, limit: int = 20, backfill: int = 0, quick: bool = False
    ) -> list[dict]:
        """Repeat deployers ranked by what sniping them earns you, incl. the bribe to land early.
        Runs the same full analysis as `deployer` (so verdicts agree) unless `quick`."""
        since = int(time.time() - days * 86400)
        if backfill:
            await self.backfill_repeat_deployers(min_launches, backfill, days)
        rows = []
        window = self.cfg.snipe.analysis_window_secs
        for dep, count, _first, _last in self.store.deployer_counts(min_launches, since)[: limit * 3]:
            launches = [l for l in self.store.launches_by_deployer(dep, self.cfg.snipe.max_launches, before_ts=int(time.time()) - window)
                        if self.store.coverage(f"mint:{l.mint}").get("from_ts", 1 << 62) <= l.ts]  # fmt: skip
            if len(launches) < min_launches:
                continue
            if backfill:
                await self.fingerprint_early_trades(launches)
            rep = await asyncio.to_thread(
                deployer_report, self.store, self.cfg, dep, launches, self.store.funder(dep), None, not quick
            )
            self.store.put_score("deployer", dep, compact_deployer(rep))
            v = rep["verdict"]
            sweep = rep.get("tip_sweep") or {}
            best = max(sweep.get("rows") or [{}], key=lambda r: r.get("ev_lower", -1e9))
            comp = rep.get("competition") or {}
            r1 = next((x for x in comp.get("bribe_to_rank") or [] if x["rank"] == 1), {})
            rows.append({
                "deployer": dep, "launches": count, "analyzed": rep["launches_analyzed"], "verdict": v["verdict"],
                "ev_sol": v.get("ev_sol"), "ev_lower_sol": v.get("ev_lower_sol"), "dump_rate": rep.get("dump_rate"),
                "snipers_per_launch": comp.get("median_snipers"), "bribe_first_p75_sol": r1.get("p75_sol"),
                "best_bribe_sol": best.get("bribe_sol"), "ev_at_best_bribe": best.get("mean_pnl"),
                "ev_lower_at_best_bribe": best.get("ev_lower"), "recommended": v.get("recommended"),
            })  # fmt: skip
        order = {"SNIPE": 0, "MARGINAL": 1, "INSUFFICIENT_DATA": 2, "AVOID": 3, "UNKNOWN": 4}
        rows.sort(
            key=lambda r: (
                order.get(r["verdict"], 5),
                -(r["ev_lower_at_best_bribe"] if r["ev_lower_at_best_bribe"] is not None else -1e9),
            )
        )
        return rows[:limit]

    async def fee_market(
        self, deployer: str | None = None, days: float = 1.0, max_launches: int = 2000, refresh: bool = True
    ) -> dict:
        """What snipers pay to land early — across all indexed launches or one deployer's — and,
        where full trade windows exist, the EV of each bribe level for you."""
        from .analysis.competition import competition_report, tip_sweep
        from .analysis.launches import build_context

        now = int(time.time())
        if deployer:
            launches = self.store.launches_by_deployer(self._check(deployer), max_launches, before_ts=now - 30)
        else:
            launches = self.store.launches_between(int(now - days * 86400), now - 30)[-max_launches:]
        launches = sorted(
            (l for l in launches if self.store.coverage(f"mint:{l.mint}").get("from_ts", 1 << 62) <= l.ts),
            key=lambda l: l.ts,
        )
        if refresh:
            await self.fingerprint_early_trades(launches)
        contexts = [build_context(self.store, l, self.cfg) for l in launches]
        report = {
            "scope": deployer or f"all indexed launches, last {days:g} days",
            "competition": competition_report(self.store, self.cfg, contexts),
        }
        full = [
            c
            for c in contexts
            if c.window_end <= now and self.store.coverage(f"mint:{c.launch.mint}").get("to_ts", 0) >= c.window_end - 1
        ]
        if len(full) >= 3:
            report["tip_sweep"] = tip_sweep(self.store, self.cfg, full, ExitParams.from_config(self.cfg.snipe.exit))
            report["tip_sweep_launches"] = len(full)
        return report

    def label(self, address: str, label: str, kind: str = "service") -> dict:
        self.store.set_label(self._check(address), label, kind)
        return {"address": address, "label": label, "kind": kind}

    # -- research ------------------------------------------------------------------------------

    async def optimize_snipe(self, deployer: str | None = None, days: float = 7.0, max_launches: int = 3000) -> dict:
        if deployer:
            deployer = self._check(deployer)
            await self.ingestor.backfill_deployer(
                deployer, self.cfg.snipe.max_launches, self.cfg.snipe.analysis_window_secs
            )
            launches = sorted(
                self.store.launches_by_deployer(deployer, self.cfg.snipe.max_launches), key=lambda l: l.ts
            )
            launches = [l for l in launches if l.ts + self.cfg.snipe.analysis_window_secs <= time.time()]
        else:
            now = int(time.time())
            launches = analyzable_launches(self.store, self.cfg, int(now - days * 86400), now)[-max_launches:]
        contexts = [build_context(self.store, l, self.cfg) for l in launches]
        if len(contexts) < 3:
            return {"error": f"only {len(contexts)} analyzable launches", "launches": len(contexts)}
        opt = optimize_exits(contexts, self.cfg)
        opt.pop("_matrix_rows", None)
        rec = ExitParams(**opt["recommended"]["exit"])
        opt["size_sweep"] = sweep_sizes(contexts, self.cfg, rec)
        opt["latency_sweep"] = sweep_latency(contexts, self.cfg, rec)
        opt["launches"] = len(contexts)
        opt["scope"] = deployer or f"all indexed launches, last {days:g} days"
        return opt

    async def backfill_repeat_deployers(self, min_launches: int = 3, top_k: int = 20, days: float = 30.0) -> int:
        """Fetch early-trade windows for the most active repeat deployers' known launches."""
        since = int(time.time() - days * 86400)
        rows = self.store.deployer_counts(min_launches, since)[:top_k]
        window = self.cfg.snipe.analysis_window_secs
        count = 0
        for dep, _n, _first, _last in rows:
            launches = self.store.launches_by_deployer(dep, self.cfg.snipe.max_launches)
            self.progress(f"{dep[:8]}…: backfilling {len(launches)} launch windows")
            for l in launches:
                if l.ts + window <= time.time():
                    await self.ingestor.backfill_mint_window(l.mint, l.ts - 1, l.ts + window)
                    count += 1
        return count

    def backtest(self, days: float = 7.0) -> dict:
        now = int(time.time())
        return policy_backtest(self.store, self.cfg, int(now - days * 86400), now)

    def top_deployers(self, min_launches: int = 3, days: float = 30.0, limit: int = 30) -> list[dict]:
        return top_deployers(self.store, self.cfg, min_launches, int(time.time() - days * 86400), limit)

    async def study(self, wallet: str, days: float = 1.0, negatives: int = 15, refresh: bool = True,
                    auto_smart: int = 0, backfill_entries: int = 0, entry_window: int = 120) -> dict:  # fmt: skip
        """Every factor at each of a wallet's entries and exits vs. the alternatives it passed on.
        Needs the market tape (`watch --tape`) for the period it traded in."""
        from .meta import ensure_assets, fetch_meta

        wallet = self._check(wallet)
        since = int(time.time() - days * 86400)
        if refresh:
            await self.ingestor.backfill_address(wallet, max_sigs=self.cfg.copy.max_signatures, since_ts=since)
        if auto_smart:
            # wallets with consistently profitable round trips in the same data, as a "smart money" factor
            for r in self.discover_wallets(days, 5, auto_smart):
                if r["wallet"] != wallet and (r.get("ev_lower") or 0) > 0:
                    self.store.set_label(r["wallet"], "smart", "smart", "auto")
        if backfill_entries:
            # entries outside the tape: fetch each token's market for `entry_window` seconds before the entry
            spans = self.store.tape_spans()
            from .analysis.study import routed_out

            firsts = {}
            for t in routed_out(self.store.user_market(wallet, since)):
                firsts.setdefault(t.mint, t)
            todo = [t for t in firsts.values() if t.is_buy and not any(a <= t.ts - 300 and t.ts <= b for a, b in spans)]
            todo = sorted(todo, key=lambda t: -t.ts)[:backfill_entries]
            for i, t in enumerate(todo, 1):
                self.progress(f"entry window {i}/{len(todo)}")
                await self.ingestor.backfill_mint_window(t.mint, t.ts - entry_window, t.ts)
        # first pass picks the moments; then fetch what they need (metadata, quote-asset prices) and score
        rows = (await asyncio.to_thread(collect_study, self.store, wallet, since, negatives))["rows"]
        mints = {r["_mint"] for rs in rows.values() for r in rs}
        launches = [l for l in (self.store.launch(m) for m in mints) if l and l.uri]
        self.progress(f"metadata for {len(launches)} tokens")
        await fetch_meta(self.store, [(l.mint, l.uri) for l in launches])
        await ensure_assets(self.store, self.cfg.rpc.http_url, self.store.quote_mints(sorted(mints)))
        report = await asyncio.to_thread(study_report, self.store, wallet, since, negatives)
        self.store.put_score("study", wallet, {k: v for k, v in report.items() if k != "entry_rows"})
        return report

    # -- OKTech ------------------------------------------------------------------------------------

    def _oktech(self):
        from .oktech import OktechClient

        return OktechClient(self.cfg.oktech.base_url)

    async def oktech_plan(self, kinds: tuple[str, ...] = ("copy", "dev")) -> dict:
        """The OKTech snipers snipetech's current verdicts call for, vs. the ones it already made."""
        from .meta import WSOL, ensure_assets
        from .oktech import PUSH_KINDS, build_plan, diff

        try:
            await ensure_assets(self.store, self.cfg.rpc.http_url, [])  # SOL/USD for market-cap filters
        except Exception as exc:  # DAS isn't on every RPC; the plan says what it couldn't convert
            self.progress(f"SOL price lookup failed: {exc}")
        sol_usd = (self.store.asset(WSOL) or {}).get("price_usd")
        planned = build_plan(self.store, self.cfg, kinds, sol_usd)
        own = {PUSH_KINDS[k] for k in kinds if k in PUSH_KINDS}
        d = diff(planned, [r for r in self.store.oktech_snipers() if r["kind"] in own])

        def show(p):
            return {"kind": p.kind, "source": p.source, "name": p.name, "summary": p.summary, "notes": p.notes,
                    "external_id": p.external_id, "config": p.config}  # fmt: skip

        return {
            "wallet": self.cfg.oktech.wallet or "default", "sol_usd": sol_usd, "_sync": d,
            "create": [show(p) for p in d.create], "replace": [show(p) | {"old_id": r["id"]} for r, p in d.replace],
            "keep": [show(p) | {"id": r["id"]} for r, p in d.keep], "remove": [{"id": r["id"], "name": r["name"]} for r in d.remove],
        }  # fmt: skip

    async def oktech_push(self, plan: dict, prune: bool = False, allow_overlap: bool = False) -> list[dict]:
        """Apply a plan. Retires nothing while the sniper wallet holds a coin (or its holdings cannot be read). A new
        copy sniper whose leader an active sniper already copies is refused (unless `allow_overlap`); so is a
        replacement while the sniper it replaces is still active and cannot be retired in the same push."""
        from .oktech import SyncPlan, apply

        held: list[dict] | None = None
        if self.cfg.operator.wallet:
            try:
                held = await self.open_positions(self.cfg.operator.wallet)
            except Exception as exc:  # noqa: BLE001 - unknown holdings block every retire
                self.progress(f"holdings unknown ({exc}): nothing will be retired")
        client = self._oktech()
        try:
            sync, refused = plan["_sync"], []
            if not allow_overlap and (sync.create or sync.replace):
                copiers = await self._active_copiers(client)
                active = {i for ids in copiers.values() for i in ids}

                def clash(p, old_id: str | None = None) -> list[tuple[str, str]]:
                    return [
                        (lead, i) for lead in sorted(_planned_leaders(p)) for i in copiers.get(lead, []) if i != old_id
                    ]

                creates, replaces = [], []
                for p in sync.create:
                    if hit := clash(p):
                        refused.append(_refused(p, *hit[0]))
                    else:
                        creates.append(p)
                for old, p in sync.replace:
                    if hit := clash(p, old["id"]):
                        refused.append(_refused(p, *hit[0]))
                    elif old["id"] in active and held != []:  # the old one keeps copying until it can be retired
                        refused.append({"action": "refused (overlap)", "name": p.name, "id": old["id"],
                                        "error": "replaced once the wallet holds nothing: until then the old sniper keeps copying"})  # fmt: skip
                    else:
                        replaces.append((old, p))
                sync = SyncPlan(create=creates, keep=sync.keep, replace=replaces, remove=sync.remove)
            return refused + await apply(client, self.store, sync, self.cfg.oktech.wallet or "SNIPER", prune, held=held)
        finally:
            await client.close()

    async def _active_copiers(self, client) -> dict[str, list[str]]:
        """Leader -> ids of the active snipers on the account (the book's or not) that already copy it. OKTech's task
        documents carry no config, so what a task copies comes from our own record of it."""
        from .book.policy import copied_leaders
        from .positions import is_active

        local = {r["id"]: r for r in self.store.oktech_snipers(active=False)}
        out: dict[str, list[str]] = {}
        for t in await client.tasks():
            if is_active(t.get("status")):
                for leader in copied_leaders(local.get(t.get("id")) or {}):
                    out.setdefault(leader, []).append(str(t.get("id")))
        return out

    async def oktech_replica(self, wallet: str, buy_sol: float, create: bool = False, max_buys: int = 5) -> dict:
        """A studied wallet's rules as an OKTech sniper; with `create`, made on OKTech (idle)."""
        from .meta import WSOL, ensure_assets
        from .oktech import SyncPlan, apply, replica_from_study

        wallet = self._check(wallet)
        study = self.store.get_score("study", wallet)
        if not study or "entry" not in study:
            raise SystemExit(
                f"no study with a contrast for {wallet}: run `snipetech study {wallet}` with the tape running"
            )
        try:
            await ensure_assets(self.store, self.cfg.rpc.http_url, [])
        except Exception as exc:
            self.progress(f"SOL price lookup failed: {exc}")
        p = replica_from_study(
            study, self.cfg, buy_sol, (self.store.asset(WSOL) or {}).get("price_usd"), max_buys=max_buys
        )
        out = {"name": p.name, "summary": p.summary, "notes": p.notes, "config": p.config, "external_id": p.external_id}
        if create:
            client = self._oktech()
            try:
                out["result"] = await apply(
                    client, self.store, SyncPlan(create=[p]), self.cfg.oktech.wallet or "SNIPER", held=[]
                )
            finally:
                await client.close()
        return out

    async def oktech_probe(
        self, buy_sol: float, snipes: int, copy_wallet: str | None, wallet: str, create: bool
    ) -> dict:
        """The speed-test snipers; with `create`, made on OKTech idle (start them with `oktech start`)."""
        from .oktech import speedtest_snipers

        if copy_wallet:
            copy_wallet = self._check(copy_wallet)
        planned = speedtest_snipers(buy_sol, snipes, copy_wallet)
        out = {"wallet": wallet, "snipers": [{"name": p.name, "summary": p.summary, "config": p.config} for p in planned],
               "max_spend_sol": round(len(planned) * snipes * (buy_sol + 0.003 + 0.002), 4)}  # fmt: skip
        if create:
            client = self._oktech()
            try:
                for p, row in zip(planned, out["snipers"]):
                    ans = await client.create(p.name, p.config, wallet, p.external_id)
                    task = ans.get("task") or {}
                    self.store.add_oktech_sniper({"id": task["id"], "external_id": p.external_id, "kind": p.kind, "source": p.source,
                                                  "name": p.name, "config_hash": p.config_hash, "payload": p.config})  # fmt: skip
                    row |= {"id": task["id"], "status": task.get("status"), "duplicate": bool(ans.get("duplicate"))}
            finally:
                await client.close()
        return out

    async def holdings(self, address: str) -> list[dict]:
        """Every token the wallet holds (both token programs), WSOL aside: mint, amount (ui), raw, decimals, account.
        Raises if the RPC can't answer: never 'nothing held' by mistake."""
        from .positions import WSOL
        from .solana.programs import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

        out = []
        for prog in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            r = await self.rpc.call(
                "getTokenAccountsByOwner",
                [address, {"programId": prog}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
            for a in r["value"]:
                info = a["account"]["data"]["parsed"]["info"]
                ta = info["tokenAmount"]
                amt = float(ta.get("uiAmount") or 0)
                decimals = int(ta.get("decimals") if ta.get("decimals") is not None else 6)
                raw = int(ta["amount"]) if ta.get("amount") is not None else int(round(amt * 10**decimals))
                if amt > 0 and info["mint"] != WSOL:
                    out.append(
                        {"mint": info["mint"], "amount": amt, "raw": raw, "decimals": decimals, "account": a["pubkey"]}
                    )
        return out

    async def open_positions(self, address: str, ignore: list[str] | tuple[str, ...] | None = None,
                             unknown: list[dict] | None = None) -> list[dict]:  # fmt: skip
        """The coins the wallet really holds (see positions.py): valued at the tape's last price, dust and stablecoins
        out, unpriced tokens reported in `unknown`. The one rule every stop decision uses."""
        from .positions import filter_positions

        return filter_positions(
            await self.holdings(address), self.store.last_price, ignore=tuple(ignore or ()), unknown=unknown
        )

    async def wsol_balance(self, address: str) -> float:
        from .positions import WSOL
        from .solana.programs import TOKEN_2022_PROGRAM, TOKEN_PROGRAM

        total = 0.0
        for prog in (TOKEN_PROGRAM, TOKEN_2022_PROGRAM):
            r = await self.rpc.call(
                "getTokenAccountsByOwner",
                [address, {"programId": prog}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
            for a in r["value"]:
                info = a["account"]["data"]["parsed"]["info"]
                if info["mint"] == WSOL:
                    total += float(info["tokenAmount"].get("uiAmount") or 0)
        return total

    async def wallet_sol(self, address: str) -> float:
        """SOL plus wrapped SOL: the part of the wallet that is not a coin. The wallet-wide guard's floor counts both
        (OKTech wraps SOL at a start: 6 Nov 15:08 the SOL balance fell by 0.0996 and the wallet did not)."""
        b = (await self.rpc.call("getBalance", [address, {"commitment": "processed"}]))["value"] / 1e9
        return b + await self.wsol_balance(address)

    async def equity(self, address: str, ignore: list[str] | None = None) -> tuple[float, float, list[dict]]:
        """(equity in SOL = SOL + wrapped SOL + the tape value of the priced positions, the SOL balance, the positions).
        An unpriced position (`priced` False) counts for nothing here; callers must not call an emergency on it."""
        b = (await self.rpc.call("getBalance", [address, {"commitment": "processed"}]))["value"] / 1e9
        wsol = await self.wsol_balance(address)
        held = await self.open_positions(address, ignore=ignore)
        return b + wsol + sum(float(h.get("value_sol") or 0) for h in held), b, held

    async def oktech_guard(self, ids: list[str], address: str, floor_sol: float, max_minutes: float, start: bool = True,
                           allow_uncapped: bool = False, native_reserve_sol: float = 0.05) -> dict:  # fmt: skip
        """Start capped snipers and watch the wallet. A running sniper is the only thing that sells its positions,
        so this never stops one except in an emergency, and then lists exactly what is left to sell. The emergency is
        *equity* (SOL + the tape value of the coins held) below `floor_sol`, or the SOL balance below
        `native_reserve_sol` (nothing left to pay sells with): buying coins moves SOL into positions, only losing
        money moves equity. At `max_minutes` it stops watching and leaves them running (they are capped).
        Stops that fail are reported as such, never as "stopped"."""
        from .positions import is_active

        address = self._check(address)
        if start and self.stop_flag_set():
            raise SystemExit("the owner's /stop flag is set: /resume first; nothing started")
        mine = {r["id"]: r for r in self.store.oktech_snipers()}
        uncapped = [i for i in ids if not ((mine.get(i) or {}).get("payload") or {}).get("singleSnipe")]
        if uncapped and not allow_uncapped:
            raise SystemExit(f"refusing to start {uncapped}: not capped (singleSnipe + totalSnipes) or not made by snipetech. "
                             "A capped sniper stops buying by itself; its last buy can be left with no exit, so watch it.")  # fmt: skip
        client = self._oktech()
        events: list[str] = []

        def log(msg: str) -> None:
            line = f"{time.strftime('%H:%M:%S', time.gmtime())} {msg}"
            events.append(line)
            self.progress(line)

        async def balance() -> float:
            return (await self.rpc.call("getBalance", [address, {"commitment": "processed"}]))["value"] / 1e9

        reason = (
            "time limit: still running (capped; its exits run, but a last buy can be left unsold: check the wallet)"
        )
        stopped, stop_failed = False, []
        try:
            b0 = await balance()
            eq0, _, held0 = await self.equity(address)
            log(f"wallet {address[:6]}… holds {b0:.4f} SOL, equity {eq0:.4f} SOL ({len(held0)} coin(s)); emergency floor "
                f"{floor_sol} SOL equity / {native_reserve_sol} SOL balance; watching {max_minutes:g} min")  # fmt: skip
            if eq0 <= floor_sol:
                raise SystemExit(f"equity {eq0:.4f} is already at or below the floor {floor_sol}")
            if start:
                for i in ids:
                    try:
                        ans = await client.start(i)
                        log(f"started {i}: {ans.get('task', {}).get('status', ans)}")
                    except Exception as exc:  # the others are started and watched all the same
                        log(f"start {i} FAILED ({exc}); watching the rest")
            t0, low = time.time(), b0
            while time.time() - t0 < max_minutes * 60:
                await asyncio.sleep(2)
                try:
                    b = await balance()
                except Exception as exc:  # an RPC hiccup is no reason to touch the snipers
                    log(f"balance check failed ({exc}); still watching")
                    continue
                if b < low - 0.005:
                    log(f"balance {b:.4f} SOL")
                low = min(low, b)
                if b < floor_sol or b < native_reserve_sol:
                    # the SOL balance alone says nothing while coins are held: value them
                    try:
                        eq, _, held = await self.equity(address)
                    except Exception as exc:
                        log(f"equity check failed ({exc}); still watching")
                        continue
                    unpriced = [h for h in held if not h.get("priced", True)]
                    if unpriced:
                        log(f"balance {b:.4f} SOL below the floor but {len(unpriced)} unpriced coin(s) held "
                            f"({', '.join(str(h['mint'])[:8] for h in unpriced)}): waiting for the tape to price them, no emergency")  # fmt: skip
                        continue
                    if eq < floor_sol or b < native_reserve_sol:
                        reason = f"EMERGENCY: equity {eq:.4f} SOL (balance {b:.4f}) below the floor {floor_sol}"
                        for i in ids:
                            try:
                                await client.stop(i)
                            except Exception as exc:
                                stop_failed.append(i)
                                log(f"stop {i} FAILED ({exc})")
                        stopped = not stop_failed
                        reason += (
                            f"; NOT stopped: {stop_failed} (stop them by hand)" if stop_failed else "; stopped buying"
                        )
                        break
                if int(time.time() - t0) % 20 < 2:
                    try:
                        states = {t["id"]: t.get("status") for t in await client.tasks() if t["id"] in ids}
                    except Exception as exc:  # OKTech not answering is no reason to stop watching
                        log(f"OKTech status check failed ({exc}); still watching")
                        continue
                    if states and not any(is_active(st) for st in states.values()):
                        reason = f"all finished on their own ({states})"
                        break
            log(reason)
            b1 = await balance()
            try:
                eq1, _, held = await self.equity(address)
            except Exception as exc:
                eq1, held = b1, []
                log(f"final holdings check failed ({exc}): list them with `oktech holdings`")
            log(f"wallet {b0:.4f} -> {b1:.4f} SOL ({b1 - b0:+.4f}); equity {eq0:.4f} -> {eq1:.4f}")
            if held:
                what = ("STILL HELD, sell these by hand (the stopped sniper no longer manages them)" if stopped or stop_failed
                        else "still held (the sniper is managing them)")  # fmt: skip
                log(
                    what
                    + ": "
                    + ", ".join(
                        f"{h['mint']} ({h['amount']:,.0f}, {(h['value_sol'] if h.get('value_sol') is not None else 0):.4f} SOL{'' if h.get('value_sol') is not None else ', unpriced'})"
                        for h in held
                    )
                )
            else:
                log("no coins held")
            return {"start_sol": b0, "end_sol": b1, "equity_sol": eq1, "reason": reason, "stopped": stopped,
                    "stop_failed": stop_failed, "held": held, "events": events}  # fmt: skip
        finally:
            await client.close()

    async def oktech_cycle(self, ids: list[str], address: str, floor_sol: float, max_minutes: float, max_fills: int = 10,
                           rearm_after: float = 60.0, ignore_mints: list[str] | None = None) -> dict:  # fmt: skip
        """Keep one-shot hunt snipers armed. Measured 5 Nov: a hunt sniper fires once per start and then never again,
        so after its position has closed (nothing held in the wallet) the sniper is stopped and started again. A sniper
        is never restarted while the wallet holds tokens (a stopped sniper stops selling). Re-arming also happens when a
        sniper has been armed for `rearm_after` seconds without a fill (a failed send also spends the shot), and never
        while the tape is more than the book's TAPE_STALE_SECS behind: the fills are counted on it (it does not start
        then either).

        The run ends at `max_fills` buys since it started, counted as the larger of the wallet's buys on the tape and
        the coins seen in the wallet (with the recorder missing our buys the tape alone counted none: a probe made 118
        buys under a cap of 10), on the emergency floor, at the time limit, at the owner's /stop (read every tick), or
        once the owner's wallet-wide guard holds (operator/RISK: the day 1 SOL down or the wallet under 10 SOL; read at
        the start and before each re-arm; a guard that cannot tell holds that re-arm back, never the run).
        Every end leaves nothing armed: the snipers this run armed are stopped when the wallet holds nothing (one left
        armed buys once more at the next launch that qualifies, past the time limit or under the floor, with nobody
        watching); a coin held, or a wallet that cannot be read, keeps them running to sell it (a stopped sniper strands
        its coin). The report's `held` is the wallet's coins by the shared rule (positions.py), and `left_running` the
        snipers this run armed and did not stop."""
        from .book.run import TAPE_STALE_SECS, _tape_age

        address = self._check(address)
        if self.stop_flag_set():
            raise SystemExit("the owner's /stop flag is set: /resume first; nothing cycled")
        mine = {r["id"]: r for r in self.store.oktech_snipers()}
        uncapped = [i for i in ids if not ((mine.get(i) or {}).get("payload") or {}).get("singleSnipe")]
        if uncapped:
            raise SystemExit(f"refusing to cycle {uncapped}: not capped or not made by snipetech")
        client = self._oktech()
        events: list[str] = []

        def log(msg: str) -> None:
            line = f"{time.strftime('%H:%M:%S', time.gmtime())} {msg}"
            events.append(line)
            self.progress(line)

        async def balance() -> float:
            return (await self.rpc.call("getBalance", [address, {"commitment": "processed"}]))["value"] / 1e9

        def fills_since(ts: float) -> int:
            """The wallet's buys on the tape since `ts` (0 when the tape cannot be read: then nothing is re-armed)."""
            try:
                return sum(1 for t in self.store.trades_for_user(address, int(ts)) if t.is_buy)
            except Exception:  # noqa: BLE001 - tape_lag says so, and the coins seen in the wallet still count
                return 0

        def tape_lag() -> str | None:
            """Why the tape cannot count this run's fills now, or None. An empty tape is a fresh install, as for the
            book."""
            try:
                age = _tape_age(self.store, time.time())
            except Exception as exc:  # noqa: BLE001
                return f"the tape could not be read ({type(exc).__name__})"
            return f"the tape is {age:.0f} s behind" if age is not None and age > TAPE_STALE_SECS else None

        async def open_positions() -> list[dict]:
            # the wallet's actual holdings are the truth (the tape lags a few seconds: on 5 Nov 22:14 a sniper was
            # restarted 5 s after its buy because the fill was not on the tape yet, and the coin was left unmanaged);
            # ignore_mints: positions no sniper manages (to be sold by hand)
            return await self.open_positions(address, ignore=ignore_mints)

        async def guard_says(held: list[dict]) -> tuple[bool | None, str]:
            """The owner's wallet-wide guard now (operator/RISK: the day 1 SOL down or the wallet under 10 SOL), read at
            the start and at each re-arm like the /stop flag: (True, why) while it holds, (None, why) when it cannot
            tell, (False, "") when it is clear."""
            try:  # the SOL only while the floor is on: with the owner's guard off nothing is read for it
                sol = await self.wallet_sol(address) if self.risk_guard_reads_balance() else None
                g = self.risk_guard(address, sol, held)
            except Exception as exc:  # noqa: BLE001 - cannot tell is never clear
                return None, f"the guard cannot tell ({type(exc).__name__})"
            if g.tripped:
                return True, g.reason
            return (None, g.reason) if not g.known else (False, "")

        async def disarm(why: str) -> tuple[str, list[dict] | None]:
            """How every run ends: with nothing held the snipers this run armed are stopped (a stopped sniper with
            nothing held strands nothing, and one left armed can still buy once); a coin held or an unreadable wallet
            keeps them running to sell. (The reason with what was done, the coins held or None when unreadable.)"""
            try:
                held = await open_positions()
            except Exception as exc:  # noqa: BLE001 - unknown holdings never stop a sniper
                log(f"holdings check at the end failed ({exc})")
                return (
                    f"{why}; the wallet could not be read, so the snipers are left running (one may hold a coin)",
                    None,
                )
            if held:
                return (
                    f"{why}; a coin is held, so the snipers keep running to sell it (a stopped sniper strands its coin)",
                    held,
                )
            failed = []
            for i in [i for i in ids if i in armed]:
                try:
                    await client.stop(i)
                    armed.discard(i)
                except Exception as exc:  # noqa: BLE001 - said in the reason: the owner stops it by hand
                    failed.append(i)
                    log(f"stop of {i[:8]} at the end failed: {exc}")
            return f"{why}; the wallet holds nothing, so the snipers it armed are stopped" + (
                f" except {', '.join(failed)} (stop them with /stop)" if failed else ""
            ), held

        t0 = time.time()
        reason, restarts = "time limit", 0
        armed_at: dict[str, float] = {}
        armed: set[str] = set()  # the snipers this run started and has not stopped since
        seen: set[str] = set()  # the coins the wallet has held since the start: each one is at least one buy

        def fills_now() -> int:
            return max(fills_since(t0), len(seen))

        try:
            b0 = await balance()
            held0 = await open_positions()  # before any sniper is touched: a stopped sniper strands its coin
            if held0:
                raise SystemExit("the wallet holds " + ", ".join(f"{h['mint']} ({h.get('value_sol', 0):.4f} SOL)" for h in held0)
                                 + ": not cycling (stopping a sniper strands its coin). Sell by hand, or pass --ignore MINT "
                                 "for a position no sniper manages.")  # fmt: skip
            log(
                f"wallet {address[:6]}… holds {b0:.4f} SOL; floor {floor_sol}; cap {max_fills} fills; {max_minutes:g} min"
            )
            if b0 <= floor_sol:
                raise SystemExit(f"balance {b0:.4f} is at or below the floor {floor_sol}")
            lag = tape_lag()
            if lag:  # the fills cap is counted on the tape: a run that cannot count them does not start
                raise SystemExit(f"{lag} (more than {TAPE_STALE_SECS} s): not cycling, its fills are counted there")
            blocked, why = await guard_says(held0)
            if blocked is not False:  # cannot tell is never clear, as for the reads above
                raise SystemExit(
                    f"the wallet-wide guard {'holds' if blocked else 'cannot be read'} ({why}): not cycling"
                )
            for i in ids:
                with contextlib.suppress(Exception):  # a sniper left running from an earlier run has spent its shot
                    await client.stop(i)
                    await asyncio.sleep(1.0)
                ans = await client.start(i)
                armed_at[i] = time.time()
                armed.add(i)
                log(f"started {i[:8]}: {ans.get('task', {}).get('status', ans)}")
            low, paused, unsure = b0, False, False
            while time.time() - t0 < max_minutes * 60:
                await asyncio.sleep(5)
                if self.stop_flag_set():
                    reason = "stopped by the owner's /stop: no more re-arms"
                    break
                try:
                    b = await balance()
                    held = await open_positions()
                except Exception as exc:
                    log(f"rpc check failed ({exc}); still watching")
                    continue
                seen.update(str(h["mint"]) for h in held if h.get("mint"))
                if b < low - 0.005:
                    log(f"balance {b:.4f} SOL")
                low = min(low, b)
                if b < floor_sol and b + sum(float(h.get("value_sol") or 0) for h in held) < floor_sol:
                    reason = f"EMERGENCY: equity below floor {floor_sol} (balance {b:.4f}); stopped re-arming"
                    break
                fills = fills_now()
                if fills >= max_fills:
                    reason = f"cap reached: {fills} fills since start"
                    break
                if held:
                    continue  # a position is open: never restart now
                lag = tape_lag()
                if lag:  # a re-arm now could buy without being counted
                    if not paused:
                        log(f"{lag}: no re-arm until it catches up (the fills are counted there)")
                    paused = True
                    continue
                if paused:
                    log("the tape has caught up: re-arming again")
                    paused = False
                now = time.time()
                guarded = False
                for i in ids:
                    since = now - armed_at.get(i, now)
                    if since < 20:  # a fresh shot may have just been fired and not reached the tape or the RPC yet
                        continue
                    if fills_since(armed_at.get(i, now)) >= 1 or since >= rearm_after:
                        blocked, why = await guard_says(held)
                        if blocked:  # the owner's guard holds: this run re-arms nothing more, as at /stop
                            reason, guarded = f"the wallet-wide guard holds ({why}): no more re-arms", True
                            break
                        if blocked is None:  # an unreadable wallet says nothing about the day: wait, never re-arm
                            if not unsure:
                                log(f"{why}: no re-arm until it can tell")
                            unsure = True
                            break
                        unsure = False
                        try:
                            await client.stop(i)
                            armed.discard(i)
                            await asyncio.sleep(1.5)
                            if self.stop_flag_set():  # the owner's /stop came in the pause: it wins, the next tick ends
                                log(f"/stop came while {i[:8]} was being re-armed: left stopped")
                                break
                            ans = await client.start(i)
                            armed_at[i] = time.time()
                            armed.add(i)
                            restarts += 1
                            log(f"re-armed {i[:8]} after {since:.0f}s (fills so far {fills}): "
                                f"{ans.get('task', {}).get('status', ans)}")  # fmt: skip
                        except Exception as exc:
                            log(f"re-arm of {i[:8]} failed: {exc}")
                        break  # one restart per tick keeps the rate limits and the state readable
                if guarded:
                    break
            reason, held = await disarm(reason)
            log(reason)
            try:
                b1: float | None = await balance()
            except Exception as exc:  # noqa: BLE001 - the report still says what was done
                b1 = None
                log(f"final balance check failed ({exc})")
            fills = fills_now()
            moved = f"{b0:.4f} -> {b1:.4f} SOL ({b1 - b0:+.4f})" if b1 is not None else f"{b0:.4f} -> unknown"
            log(f"wallet {moved}; {fills} fills; {restarts} re-arms")
            if held:
                log("still held (the running snipers manage them): "
                    + ", ".join(f"{h['mint']} ({float(h.get('amount') or 0):,.0f})" for h in held))  # fmt: skip
            elif held is not None:
                log("no coins held")
            return {"start_sol": b0, "end_sol": b1, "reason": reason, "fills": fills, "restarts": restarts,
                    "held": held or [], "left_running": sorted(armed), "events": events}  # fmt: skip
        finally:
            await client.close()

    def oktech_copy_audit(self, address: str, since_ts: int, leaders: list[str]) -> list[dict]:
        """Where each of our copy buys landed relative to the copied wallet's buy on the same mint: slots behind,
        trades in between, and the price gap. The number the copy-landing test is for."""
        address = self._check(address)
        rows = []
        for t in self.store.trades_for_user(address, since_ts):
            if not t.is_buy:
                continue
            q = (
                "SELECT user, slot, pos, quote, q_after FROM trades WHERE mint=? AND is_buy=1 AND user IN (%s) "
                "AND (slot<? OR (slot=? AND pos<?)) ORDER BY slot DESC, pos DESC LIMIT 1" % ",".join("?" * len(leaders))
            )
            lead = self.store._q(q, (t.mint, *leaders, t.slot, t.slot, t.pos))
            if not lead:
                rows.append({"mint": t.mint, "ts": t.ts, "my_sol": t.quote / 1e9, "leader": None})
                continue
            user, lslot, lpos, lquote, lq = lead[0]
            between = self.store._q(
                "SELECT COUNT(*) FROM trades WHERE mint=? AND is_buy=1 AND (slot>? OR (slot=? AND pos>?)) AND (slot<? OR (slot=? AND pos<?))",
                (t.mint, lslot, lslot, lpos, t.slot, t.slot, t.pos),
            )[0][0]
            rows.append(
                {
                    "mint": t.mint,
                    "ts": t.ts,
                    "my_sol": t.quote / 1e9,
                    "leader": user,
                    "leader_sol": lquote / 1e9,
                    "dslot": t.slot - lslot,
                    "between_buys": int(between),
                    "price_gap_pct": round(100 * ((t.q_after / lq) ** 2 - 1), 1),
                }
            )
        return rows

    async def oktech_copylist(self, specs: list[str], wallet: str, create: bool = False, buy_sol: float = 0.05,
                              max_buys: int = 5, exit_name: str = "lab", priority_fee: float | None = None,
                              tip: float | None = None, send_count: int | None = None, tag: str = "",
                              compute_units: int | None = None, salt: str = "", allow_overlap: bool = False,
                              exit_params: dict | None = None) -> dict:  # fmt: skip
        """Capped copy snipers for a hand-picked wallet list: each spec is ADDRESS[:LABEL[:MIN_SOL[:EXIT]]] (EXIT lab|hold60).
        With `create`, a leader that an active sniper already copies (or that is listed twice) is refused before anything
        is made, naming that sniper, unless `allow_overlap`. `exit_params`: the exit itself instead of a preset's name
        (the book's per-wallet exit, book/policy.exit_for), for every spec."""
        from .oktech import list_copy_sniper

        out = []
        client = self._oktech() if create else None
        try:
            if client is not None and not allow_overlap:
                leaders = [self._check(spec.split(":")[0]) for spec in specs]
                twice = sorted({a for a in leaders if leaders.count(a) > 1})
                if twice:
                    raise OverlapError(f"refused: {twice[0][:6]}… is listed twice: {OVERLAP_HINT}")
                copiers = await self._active_copiers(client)
                hits = [(a, copiers[a][0]) for a in leaders if a in copiers]
                if hits:
                    a, sid = hits[0]
                    raise OverlapError(f"refused: sniper {sid} already copies {a[:6]}…: {OVERLAP_HINT}")
            for spec in specs:
                parts = spec.split(":")
                address = self._check(parts[0])
                label = parts[1] if len(parts) > 1 and parts[1] else address[:6]
                min_sol = float(parts[2]) if len(parts) > 2 and parts[2] else 1.0
                ex_name = parts[3] if len(parts) > 3 and parts[3] else exit_name
                p = list_copy_sniper(address, label, min_sol, buy_sol, max_buys, cfg=self.cfg, exit_name=ex_name,
                                     priority_fee=priority_fee, tip=tip, send_count=send_count, tag=tag,
                                     compute_units=compute_units, salt=salt, exit_params=exit_params)  # fmt: skip
                row = {
                    "id": address,
                    "label": label,
                    "name": p.name,
                    "summary": p.summary,
                    "notes": p.notes,
                    "config": p.config,
                }
                if client is not None:
                    ans = await client.create(p.name, p.config, wallet, p.external_id)
                    task = ans.get("task") or {}
                    self.store.add_oktech_sniper({"id": task["id"], "external_id": p.external_id, "kind": p.kind, "source": p.source,
                                                  "name": p.name, "config_hash": p.config_hash, "payload": p.config})  # fmt: skip
                    row |= {"oktech_id": task["id"], "status": task.get("status")}
                out.append(row)
        finally:
            if client is not None:
                await client.close()
        return {"snipers": out}

    async def oktech_strategy(self, strategy_id: str | None, wallet: str, create: bool = False, buy_sol: float | None = None,
                              max_buys: int = 5, via: str = "hunt", preexec: bool = True, stale: float | None = 60,
                              salt: str = "") -> dict:  # fmt: skip
        """The OKTech sniper for frozen paper strategies (all of them, or one); with `create`, made idle on OKTech.
        `salt` gives the sniper an externalId of its own (each live test's own sniper: OKTech refuses a second task
        with the same externalId, and without a salt that id comes from the strategy's settings alone)."""
        from .oktech import strategy_sniper

        frozen = self.store.coverage("paper_strategies").get("strategies", [])
        picked = [s for s in frozen if strategy_id in (None, s["id"])]
        if not picked:
            raise SystemExit(f"no frozen strategy {strategy_id or ''}: see `snipetech paper report`")
        out = []
        client = self._oktech() if create else None
        try:
            for s in picked:
                if buy_sol:
                    s = s | {"buy_sol": buy_sol}
                try:
                    p = strategy_sniper(s, max_buys, via, preexec, stale)
                except ValueError as exc:
                    out.append({"id": s["id"], "label": s["label"], "error": str(exc)})
                    continue
                p.salt = salt
                row = {
                    "id": s["id"],
                    "label": s["label"],
                    "name": p.name,
                    "summary": p.summary,
                    "notes": p.notes,
                    "config": p.config,
                }
                if client is not None:
                    ans = await client.create(p.name, p.config, wallet, p.external_id)
                    task = ans.get("task") or {}
                    self.store.add_oktech_sniper({"id": task["id"], "external_id": p.external_id, "kind": p.kind, "source": p.source,
                                                  "name": p.name, "config_hash": p.config_hash, "payload": p.config})  # fmt: skip
                    row |= {"oktech_id": task["id"], "status": task.get("status")}
                out.append(row)
        finally:
            if client is not None:
                await client.close()
        return {"wallet": wallet, "snipers": out}

    async def oktech_task(self, action: str, task_id: str) -> dict:
        from .oktech import OktechError

        client = self._oktech()
        try:
            if action == "start":
                return await client.start(task_id)
            if action == "stop":
                return await client.stop(task_id)
            if action == "delete":
                try:
                    ans = await client.delete(task_id)
                except OktechError as exc:
                    if exc.code != "NOT_FOUND":
                        raise
                    self.store.remove_oktech_sniper(task_id)  # gone on OKTech already: the local row retires too
                    return {"id": task_id, "not_found": True, "removed_locally": True}
                self.store.remove_oktech_sniper(task_id)
                return ans
            return await client.get(task_id)
        finally:
            await client.close()

    async def oktech_status(self) -> dict:
        client = self._oktech()
        try:
            status, wallets, tasks = await client.status(), await client.wallets(), await client.tasks()
        finally:
            await client.close()
        mine = {r["id"]: r for r in self.store.oktech_snipers()}
        return {"status": status, "wallets": [w["name"] for w in wallets.get("wallets", [])],
                "snipers": [t | {"source": (mine.get(t["id"]) or {}).get("source"), "kind": (mine.get(t["id"]) or {}).get("kind")}
                            for t in tasks]}  # fmt: skip

    def label_file(self, path: str, kind: str) -> dict:
        """Bulk labels: one address per line, optionally followed by a name (comma or whitespace)."""
        n = 0
        for line in Path(path).read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.replace(",", " ").split()
            self.store.set_label(self._check(parts[0]), " ".join(parts[1:]) or kind, kind, "file")
            n += 1
        return {"labeled": n, "kind": kind}

    def discover_wallets(self, days: float = 3.0, min_positions: int = 5, limit: int = 30) -> list[dict]:
        return discover_wallets(self.store, self.cfg, int(time.time() - days * 86400), min_positions, limit)

    def hottest_mints(self, since_ts: int, limit: int) -> list[str]:
        """Coins whose curve reached the most SOL since `since_ts` — where early wallets made the money."""
        rows = self.store._q("select mint, max(q_after) peak from trades where ts > ? and venue = 'curve' group by mint order by peak desc limit ?",
                             (since_ts, limit))  # fmt: skip
        return [m for m, _ in rows]

    async def discover_solanatracker(self, client, days: int | None = None, pages: int | None = None) -> list[dict]:
        """Whole-market copy candidates from SolanaTracker's realised-PnL leaderboard (30/90 d), paged, minus bots,
        exchanges and labelled platform wallets; stored under score kind 'solanatracker'. The tape labs decide."""
        from .solanatracker import human_candidates

        sc = self.cfg.solanatracker
        days = sc.days if days is None else int(days)
        pages = sc.pages if pages is None else pages
        exclude = set(self.store.labels("service")) | set(self.store.labels("platform"))
        rows: list[dict] = []
        cursor = None
        for _ in range(max(1, pages)):
            page, cursor = await asyncio.to_thread(
                lambda c=cursor: client.leaderboard(
                    days=days, limit=100, cursor=c, max_single_token_pct=sc.max_single_token_pct
                )
            )
            rows += page
            if not cursor:
                break
        cands = [r for r in human_candidates(rows, max_trades=sc.max_trades, min_win_rate=sc.min_win_rate, max_single_share=sc.max_single_share,
                                             min_realized_usd=sc.min_realized_usd) if r["wallet"] not in exclude]  # fmt: skip
        now = int(time.time())
        for c in cands:
            c["source"], c["days"], c["scanned_at"], c["requests_used"] = (
                "solanatracker",
                days,
                now,
                getattr(client, "requests", None),
            )
            self.store.put_score("solanatracker", c["wallet"], c)
        return cands

    async def discover_birdeye(self, client, days: float = 7.0, top_coins: int | None = None, pnl_top: int | None = None,
                               now: float | None = None) -> list[dict]:  # fmt: skip
        """Market-wide copy candidates from Birdeye: its gainer lists plus the top traders of the coins our tape saw
        pump, minus known platform wallets, each with a 30-day PnL summary. Every row is stored under score kind
        'birdeye'; the tape labs at +1/+6 remain the gates that decide."""
        from .birdeye import BudgetExceeded, candidates_from_birdeye

        bc = self.cfg.birdeye
        now = time.time() if now is None else now
        top_coins = bc.top_coins if top_coins is None else top_coins
        pnl_top = bc.pnl_top if pnl_top is None else pnl_top
        exclude = set(self.store.labels("service")) | set(self.store.labels("platform"))
        gainers: list[dict] = []
        for period in bc.gainers_periods:
            try:
                # realised PnL, not total: the "PnL" board is dominated by wallets sitting on unrealised bags
                gainers += await asyncio.to_thread(
                    lambda p=period: client.gainers(period=p, sort_by="realized_pnl", limit=100)
                )
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - one source failing must not sink the scan
                self.progress(f"birdeye gainers {period}: {exc}")
        top: list[list[dict]] = []
        for mint in self.hottest_mints(int(now - days * 86400), top_coins):
            try:
                top.append(
                    await asyncio.to_thread(
                        lambda m=mint: client.top_traders(
                            m, time_frame=bc.top_trader_frame, sort_by="realized_pnl", limit=10
                        )
                    )
                )
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001
                self.progress(f"birdeye top traders {mint[:8]}: {exc}")
        cands = candidates_from_birdeye(gainers, top, exclude=exclude, min_trades=bc.min_trades)
        for c in cands[:pnl_top]:
            try:
                c["hist30"] = await asyncio.to_thread(lambda w=c["wallet"]: client.wallet_pnl(w, duration="30d"))
            except BudgetExceeded:
                break
            except Exception as exc:  # noqa: BLE001
                c["hist30"] = {"error": str(exc)[:120]}
            c["scanned_at"] = int(now)
            self.store.put_score("birdeye", c["wallet"], c)
        cu = getattr(client, "used_cu", None)
        for c in cands:
            c["cu_used"] = cu
        return cands

    async def own_funds_share(self, wallet: str, sample: int = 8) -> float | None:
        """Share of a wallet's recent transactions in which its own SOL or tokens move. A platform's execution
        wallet signs everybody's trades through a program but holds none of the money (share ~0): its 'PnL' is
        other people's."""
        sigs = [s for s in await self.rpc.get_signatures(wallet, limit=25) if not s.get("err")][:sample]
        if not sigs:
            return None
        txs = [t for t in (await self.rpc.get_transactions([s["signature"] for s in sigs])).values() if t]
        moved = 0
        for t in txs:
            meta = t.get("meta") or {}
            msg = t["transaction"]["message"]
            loaded = meta.get("loadedAddresses") or {}
            keys = list(msg["accountKeys"]) + loaded.get("writable", []) + loaded.get("readonly", [])
            sol_moved = False
            if wallet in keys:
                i = keys.index(wallet)
                delta = (
                    meta["postBalances"][i] - meta["preBalances"][i] + (meta.get("fee", 0) if i == 0 else 0)
                )  # paying the fee isn't trading
                sol_moved = abs(delta) > 100_000  # > 0.0001 SOL beyond the fee

            pre, post = ({b["mint"]: int(b["uiTokenAmount"]["amount"]) for b in (meta.get(side) or []) if b.get("owner") == wallet}
                         for side in ("preTokenBalances", "postTokenBalances"))  # fmt: skip
            tok = any(pre.get(m, 0) != post.get(m, 0) for m in set(pre) | set(post))
            moved += bool(sol_moved or tok)
        return moved / len(txs) if txs else None

    async def discover_real_traders(self, days: float = 3.0, min_positions: int = 5, limit: int = 30) -> list[dict]:
        """discover_wallets, minus execution wallets that trade other people's money (checked on-chain)."""
        rows = await asyncio.to_thread(self.discover_wallets, days, min_positions, limit + 10)
        out = []
        for r in rows:
            try:
                share = await self.own_funds_share(r["wallet"])
            except Exception as exc:  # unknown stays in, flagged
                self.progress(f"{r['wallet'][:8]}: own-funds check failed ({exc})")
                share = None
            r["own_funds_share"] = share
            if share is not None and share < 0.5:
                self.progress(
                    f"{r['wallet'][:8]}: dropped — its own funds move in {share:.0%} of its transactions (execution wallet)"
                )
                continue
            out.append(r)
        return out[:limit]

    # -- lists & export ---------------------------------------------------------------------------

    def list_add(self, address: str, list_name: str, reason: str = "") -> dict:
        self.store.list_add(self._check(address), list_name, reason)
        return {"address": address, "list": list_name, "reason": reason}

    def list_remove(self, address: str, list_name: str | None = None) -> dict:
        self.store.list_remove(address, list_name)
        return {"address": address, "removed_from": list_name or "all lists"}

    def lists(self) -> dict[str, list[dict]]:
        from .store import LIST_NAMES

        return {name: self.store.list_members(name) for name in LIST_NAMES}

    def export_rules(self, path: str | Path | None = None) -> dict:
        """A single JSON file your sniper can load: who to snipe/skip, sizes and exits."""
        deployers = {}
        for s in self.store.scores("deployer"):
            deployers[s["address"]] = {
                k: s.get(k)
                for k in (
                    "verdict",
                    "score",
                    "ev_sol",
                    "ev_lower_sol",
                    "rinse_rate",
                    "launches",
                    "recommended",
                    "funder",
                )
            }
        lists = self.lists()
        for item in lists["block"]:
            deployers.setdefault(item["address"], {})["verdict"] = "BLOCK"
        for item in lists["allow"]:
            deployers.setdefault(item["address"], {})["verdict"] = "ALLOW"
        snipe = sorted(a for a, d in deployers.items() if d.get("verdict") in ("SNIPE", "ALLOW"))
        skip = sorted(a for a, d in deployers.items() if d.get("verdict") in ("AVOID", "BLOCK"))
        copy_targets = {
            s["address"]: {
                k: s.get(k)
                for k in ("verdict", "ev_sol", "ev_lower_sol", "recommended", "max_profitable_latency_slots")
            }
            for s in self.store.scores("copy")
        }
        blocked_funders = [i["address"] for i in lists["block"]]
        rules = {
            "generated_at": int(time.time()),
            "snipe_deployers": snipe,
            "skip_deployers": skip,
            "blocked_funders": blocked_funders,
            "deployers": deployers,
            "copy_targets": copy_targets,
            "copy_follow": sorted(a for a, d in copy_targets.items() if d.get("verdict") in ("COPY", "COPY_FILTERED")),
            "default_snipe": {
                "buy_sol": self.cfg.snipe.buy_sol,
                "exit": ExitParams.from_config(self.cfg.snipe.exit).to_dict(),
            },
        }
        if path:
            Path(path).write_text(json.dumps(rules, indent=2, default=str))
        return rules

    def status(self) -> dict[str, Any]:
        return {"db": self.store.path, "rpc": _redact(self.cfg.rpc.http_url), "counts": self.store.stats()}


def _redact(url: str) -> str:
    if "api-key=" in url:
        head, _, _ = url.partition("api-key=")
        return head + "api-key=***"
    return url


async def oktech_tasks(
    svc: Service, action: str, ids: list[str], force: bool = False
) -> tuple[list[dict], list[tuple[str, str]]]:
    """`action` on every id, continuing past failures: (answers as {id, answer}, failures as (id, reason)).
    `start` is refused while the owner's /stop flag is set; `stop` and `delete` are refused while the sniper wallet
    holds a coin (a stopped sniper strands it) unless `force`."""
    results: list[dict] = []
    failures: list[tuple[str, str]] = []
    if action == "start" and not force and svc.stop_flag_set():
        return [], [(i, "the owner's /stop flag is set: /resume first (or --force)") for i in ids]
    if action in ("stop", "delete") and not force and svc.cfg.operator.wallet:
        try:
            held = await svc.open_positions(svc.cfg.operator.wallet)
        except Exception as exc:  # noqa: BLE001 - unknown holdings: refuse, like /stop
            return [], [
                (i, f"holdings could not be read ({type(exc).__name__}): nothing {action}ped; --force to override")
                for i in ids
            ]
        if held:
            coins = ", ".join(f"{str(h.get('mint'))[:8]}" for h in held[:5])
            return [], [
                (i, f"coin(s) held ({coins}): a stopped sniper strands its coin; sell first or pass --force")
                for i in ids
            ]
    for i in ids:
        try:
            results.append({"id": i, "answer": await svc.oktech_task(action, i)})
        except Exception as exc:  # noqa: BLE001 - one bad id must not leave the others untouched
            failures.append((i, str(exc)))
    return results, failures
