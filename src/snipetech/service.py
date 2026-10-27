"""High-level operations shared by the CLI, HTTP API, live watcher and Claude agent."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

from .analysis.audit import audit_report
from .analysis.sniper import sniper_profile, top_snipers
from .analysis.copytrade import copy_report
from .analysis.deployer import deployer_report
from .analysis.launches import build_context
from .analysis.optimize import optimize_exits, sweep_latency, sweep_sizes
from .analysis.policy import analyzable_launches, discover_wallets, policy_backtest, top_deployers
from .config import Config
from .ingest import Ingestor
from .models import Launch
from .sim.exits import ExitParams
from .solana.base58 import is_pubkey
from .solana.rpc import SolanaRpc
from .store import Store

Progress = Callable[[str], None]


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

    async def fingerprint_early_trades(self, launches: list[Launch]) -> int:
        """Make sure creates and the first slots of trades carry transaction fingerprints
        (fees, tips, routers) — needed to see what competing snipers paid."""
        horizon = self.cfg.snipe.competition_slots
        sigs: list[str] = []
        for l in launches:
            sigs.append(l.sig)
            sigs += [t.sig for t in self.store.trades_for_mint(l.mint, l.ts - 1, l.ts + 60) if t.slot <= l.slot + horizon]
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

    async def rank_copy(self, wallets: list[str], days: float | None = None, refresh: bool = True) -> list[dict]:
        out = []
        for w in wallets:
            try:
                out.append(compact_copy(await self.analyze_copy(w, days, refresh)))
            except Exception as exc:
                out.append({"address": w, "verdict": "ERROR", "error": str(exc)})
        out.sort(key=lambda r: (r.get("ev_lower_sol") or -1e9), reverse=True)
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

    async def profile_sniper(self, wallet: str, days: float = 3.0, refresh: bool = True) -> dict:
        """Reverse-engineer a competing sniper's settings and behaviour from its transactions."""
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
                if launch is None:
                    # Cheap snipe test: a young token has only a few transactions before this wallet's
                    # first trade, and the create is one of them. Older tokens aren't snipes.
                    before = await self.rpc.get_signatures(mint, before=first.sig, limit=40)
                    if len(before) < 40:
                        await ing.ingest(before)
                        launch = self.store.launch(mint)
                if launch and first.ts - launch.ts <= window:
                    # the create through its entry: rank among snipers, insiders, the dev's buy
                    await ing.backfill_mint_window(mint, launch.ts - 1, first.ts)
                    snipes.append((launch, first, last))
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
        self.store.put_score("sniper", wallet, {"address": wallet, "config": report.get("config"), "performance": report.get("performance")})
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
            rep = await asyncio.to_thread(deployer_report, self.store, self.cfg, dep, launches, self.store.funder(dep), None, not quick)
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
        rows.sort(key=lambda r: (order.get(r["verdict"], 5), -(r["ev_lower_at_best_bribe"] if r["ev_lower_at_best_bribe"] is not None else -1e9)))
        return rows[:limit]

    async def fee_market(self, deployer: str | None = None, days: float = 1.0, max_launches: int = 2000, refresh: bool = True) -> dict:
        """What snipers pay to land early — across all indexed launches or one deployer's — and,
        where full trade windows exist, the EV of each bribe level for you."""
        from .analysis.competition import competition_report, tip_sweep
        from .analysis.launches import build_context

        now = int(time.time())
        if deployer:
            launches = self.store.launches_by_deployer(self._check(deployer), max_launches, before_ts=now - 30)
        else:
            launches = self.store.launches_between(int(now - days * 86400), now - 30)[-max_launches:]
        launches = sorted((l for l in launches if self.store.coverage(f"mint:{l.mint}").get("from_ts", 1 << 62) <= l.ts), key=lambda l: l.ts)
        if refresh:
            await self.fingerprint_early_trades(launches)
        contexts = [build_context(self.store, l, self.cfg) for l in launches]
        report = {"scope": deployer or f"all indexed launches, last {days:g} days", "competition": competition_report(self.store, self.cfg, contexts)}
        full = [c for c in contexts if c.window_end <= now and self.store.coverage(f"mint:{c.launch.mint}").get("to_ts", 0) >= c.window_end - 1]
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
            await self.ingestor.backfill_deployer(deployer, self.cfg.snipe.max_launches, self.cfg.snipe.analysis_window_secs)
            launches = sorted(self.store.launches_by_deployer(deployer, self.cfg.snipe.max_launches), key=lambda l: l.ts)
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

    def discover_wallets(self, days: float = 3.0, min_positions: int = 5, limit: int = 30) -> list[dict]:
        return discover_wallets(self.store, self.cfg, int(time.time() - days * 86400), min_positions, limit)

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
            deployers[s["address"]] = {k: s.get(k) for k in ("verdict", "score", "ev_sol", "ev_lower_sol", "rinse_rate", "launches", "recommended", "funder")}
        lists = self.lists()
        for item in lists["block"]:
            deployers.setdefault(item["address"], {})["verdict"] = "BLOCK"
        for item in lists["allow"]:
            deployers.setdefault(item["address"], {})["verdict"] = "ALLOW"
        snipe = sorted(a for a, d in deployers.items() if d.get("verdict") in ("SNIPE", "ALLOW"))
        skip = sorted(a for a, d in deployers.items() if d.get("verdict") in ("AVOID", "BLOCK"))
        copy_targets = {
            s["address"]: {k: s.get(k) for k in ("verdict", "ev_sol", "ev_lower_sol", "recommended", "max_profitable_latency_slots")}
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
