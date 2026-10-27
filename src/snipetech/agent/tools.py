"""Tools the Claude agent can call. Each returns compact JSON built from real analyses."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from anthropic import beta_async_tool

from ..service import Service, compact_copy, compact_deployer
from ..store import LIST_NAMES


def _round(obj: Any, nd: int = 5) -> Any:
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


def _dump(obj: Any) -> str:
    return json.dumps(_round(obj), default=str)


@contextlib.contextmanager
def _overrides(svc: Service, values: dict[str, Any]):
    """Temporarily apply config overrides for one analysis."""
    old = {}
    try:
        for k, v in values.items():
            if v is not None:
                old[k] = svc.cfg.get(k)
                svc.cfg.set(k, v)
        yield
    finally:
        for k, v in old.items():
            svc.cfg.set(k, v)


def _slim_deployer(rep: dict) -> dict:
    keep = {k: rep.get(k) for k in ("address", "label", "launches_total", "launches_analyzed", "launches_pending_window",
                                    "settings", "funder", "outcomes", "rinse_rate", "dump_rate", "profile", "cadence",
                                    "your_strategy", "pnl_by_outcome", "recent", "patterns", "size_sweep", "latency_sweep",
                                    "competition", "tip_sweep", "trend")}  # fmt: skip
    opt = rep.get("optimizer")
    if opt:
        keep["optimizer"] = {k: opt.get(k) for k in ("strategies_tested", "default", "in_sample_best", "walk_forward", "recommended", "recommended_basis", "top")}
    keep["verdict"] = rep.get("verdict")
    keep["recent_launches"] = (rep.get("launches") or [])[:12]
    return keep


def _slim_copy(rep: dict) -> dict:
    keep = {k: rep.get(k) for k in ("address", "settings", "positions_found", "positions_simulated", "positions_open",
                                    "positions_unsimulated", "note", "target", "copier", "capture_ratio", "execution",
                                    "farming_rate", "latency_sweep", "size_sweep", "filters", "own_exits", "verdict")}  # fmt: skip
    keep["recent_positions"] = (rep.get("positions") or [])[:12]
    return keep


def make_tools(svc: Service) -> list:
    # Overrides mutate the shared config for one analysis; parallel tool calls must not interleave.
    override_lock = asyncio.Lock()

    @beta_async_tool
    async def analyze_deployer(address: str, refresh: bool = True, include_family: bool = False,
                               buy_sol: float | None = None, entry_slot_offset: int | None = None) -> str:  # fmt: skip
        """Analyze a pump.fun deployer wallet: every past launch is replayed with the user's sniper settings to measure
        rinse rate (insiders dumping on snipers), outcome mix, expected PnL per snipe with a bootstrap lower bound,
        a walk-forward-validated exit strategy, size and entry-latency sweeps, and a SNIPE/MARGINAL/AVOID verdict.

        Args:
            address: Deployer wallet (base58).
            refresh: Fetch new on-chain history first (slower); False uses local data only.
            include_family: Also include sibling wallets funded by the same funding wallet.
            buy_sol: Override snipe size for this analysis only.
            entry_slot_offset: Override how many slots after the create tx the user's buy lands, for this analysis only.
        """
        async with override_lock:
            with _overrides(svc, {"snipe.buy_sol": buy_sol, "snipe.entry_slot_offset": entry_slot_offset}):
                rep = await svc.analyze_deployer(address, refresh=refresh, family=include_family)
        return _dump(_slim_deployer(rep))

    @beta_async_tool
    async def analyze_copy_target(wallet: str, days: float = 14.0, size_sol: float | None = None,
                                  latency_slots: int | None = None, refresh: bool = True) -> str:  # fmt: skip
        """Simulate copytrading a wallet: each of its pump.fun / PumpSwap positions is replayed with the user landing
        `latency_slots` after it, paying fees, tips and the slippage other copiers cause. Returns the wallet's own PnL vs
        the copier's PnL, latency and size sweeps, crowding / copier-farming signals, a validated filter and own-exit
        alternative, a projected SOL/month, and a COPY/MARGINAL/AVOID verdict.

        Args:
            wallet: Wallet to evaluate (base58).
            days: Lookback window in days.
            size_sol: Override copy size for this analysis only.
            latency_slots: Override the user's copy latency (slots) for this analysis only.
            refresh: Fetch new on-chain history first.
        """
        overrides = {"copy.size_sol": size_sol, "copy.buy_latency_slots": latency_slots, "copy.sell_latency_slots": latency_slots}
        async with override_lock:
            with _overrides(svc, overrides):
                rep = await svc.analyze_copy(wallet, days, refresh=refresh)
        return _dump(_slim_copy(rep))

    @beta_async_tool
    async def rank_copy_targets(wallets: list[str], days: float = 14.0) -> str:
        """Analyze several copy-target wallets and rank them by the copier's EV lower bound.

        Args:
            wallets: Wallet addresses to compare.
            days: Lookback window in days.
        """
        return _dump(await svc.rank_copy(wallets, days))

    @beta_async_tool
    async def check_mint(mint: str, analyze_deployer_if_unknown: bool = False) -> str:
        """Look up a pump.fun token's launch (deployer, time, mayhem mode) and its deployers' cached verdicts.

        Args:
            mint: Token mint address.
            analyze_deployer_if_unknown: Run a full deployer analysis when there is no cached verdict.
        """
        return _dump(await svc.check_mint(mint, refresh=analyze_deployer_if_unknown))

    @beta_async_tool
    async def audit_my_wallet(wallet: str, days: float = 7.0) -> str:
        """Post-mortem of the user's own sniper/trading wallet: realized PnL by deployer and launch outcome, losses
        by entry delay, which deployers to block, and measured landing latency (calibration for all simulations).

        Args:
            wallet: The user's wallet.
            days: Lookback in days.
        """
        rep = await svc.audit(wallet, days)
        rep["worst_positions"] = rep.get("worst_positions", [])[:5]
        rep["by_deployer"] = rep.get("by_deployer", [])[:15]
        return _dump(rep)

    @beta_async_tool
    async def optimize_snipe_strategy(deployer: str | None = None, days: float = 7.0) -> str:
        """Search ~300 exit rules plus size and entry-latency sweeps, with the winner picked on older launches and
        evaluated on newer unseen ones. Scope: one deployer, or every indexed launch in the last `days`.

        Args:
            deployer: Deployer wallet; omit to optimize across all indexed launches.
            days: Lookback for the all-launches scope.
        """
        return _dump(await svc.optimize_snipe(deployer, days))

    @beta_async_tool
    async def backtest_deployer_rules(days: float = 7.0) -> str:
        """Walk-forward backtest of snipe/skip rules over all indexed launches, versus sniping everything. Rules combine
        deployer history (minimum launches, maximum dump rate, minimum historical PnL) with what each launch shows at
        entry time (dev buy size, same-slot bundle wallets, mayhem mode). Tuned on older launches, reported on newer
        ones. Needs data from `watch` or `index_launches` + backfilled windows.

        Args:
            days: How far back to test.
        """
        rep = await asyncio.to_thread(svc.backtest, days)
        rep["leaderboard"] = rep.get("leaderboard", [])[:8]
        return _dump(rep)

    @beta_async_tool
    async def index_launches(hours: float = 3.0) -> str:
        """Index every pump.fun launch in the last `hours` (creates only — cheap), revealing which wallets deploy
        repeatedly. Takes roughly 1 RPC call per launch (~2,600 launches/hour on mainnet).

        Args:
            hours: How many hours back to index.
        """
        import time as _t

        since = int(_t.time() - hours * 3600)
        res = await svc.ingestor.backfill_launches(since)
        rows = svc.store.deployer_counts(2, since)
        return _dump({"launches_indexed": res.launches, "repeat_deployers": len(rows),
                      "top": [{"deployer": d, "launches": n, "last_ts": last} for d, n, _f, last in rows[:20]]})  # fmt: skip

    @beta_async_tool
    async def top_deployers(min_launches: int = 3, days: float = 30.0, backfill_top: int = 0) -> str:
        """Leaderboard of repeat deployers in local data ranked by EV lower bound under the user's strategy.

        Args:
            min_launches: Minimum launches per deployer.
            days: Lookback in days.
            backfill_top: First fetch early-trade windows for this many of the most active repeat deployers.
        """
        if backfill_top:
            await svc.backfill_repeat_deployers(min_launches, backfill_top, days)
        return _dump(await asyncio.to_thread(svc.top_deployers, min_launches, days, 25))

    @beta_async_tool
    async def discover_copy_candidates(days: float = 3.0, min_positions: int = 5) -> str:
        """Find wallets with consistently profitable round trips in the indexed data — candidates to evaluate with
        analyze_copy_target (their own PnL is not the copier's PnL).

        Args:
            days: Lookback in days.
            min_positions: Minimum closed positions.
        """
        return _dump(await asyncio.to_thread(svc.discover_wallets, days, min_positions, 20))

    @beta_async_tool
    async def profile_sniper(wallet: str, days: float = 3.0, refresh: bool = True) -> str:
        """Reverse-engineer another sniper/trader wallet. EXACT fields read from its transactions: buy size (exact budget
        when its instruction carries one), compute-unit price/limit and priority fee, tips and which landing service got
        them (incl. separate bundle tip txs), bot/platform fees (bps) vs flat fees, router program, pump instruction and
        transaction template, slippage caps, durable nonces. INFERRED from behaviour: entry timing and rank among
        snipers, take-profit / stop-loss / time exits, whether it sells when the dev sells, partial exits, which
        deployers/launches it picks vs skips, related wallets, and its realized PnL.

        Args:
            wallet: The sniper wallet to profile.
            days: Lookback in days.
            refresh: Fetch its on-chain history first.
        """
        rep = await svc.profile_sniper(wallet, days, refresh=refresh)
        rep["recent_positions"] = rep.get("recent_positions", [])[:8]
        return _dump(rep)

    @beta_async_tool
    async def top_snipers(days: float = 3.0, min_snipes: int = 5) -> str:
        """Wallets that repeatedly land in the first slots of indexed launches, ranked by realized PnL, with their
        median bribe (priority + tip) and buy size. Candidates for profile_sniper.

        Args:
            days: Lookback in days.
            min_snipes: Minimum number of launches sniped.
        """
        return _dump(await asyncio.to_thread(svc.top_snipers, days, min_snipes, 20))

    @beta_async_tool
    async def profitable_devs(min_launches: int = 3, days: float = 7.0, backfill_top: int = 0) -> str:
        """Dev wallets that are profitable to snipe: repeat deployers ranked by your EV, with competition per launch,
        the bribe (priority + tip) needed to land first, and the best bribe level with EV after paying it.

        Args:
            min_launches: Minimum launches per deployer.
            days: Lookback in days.
            backfill_top: First fetch and fingerprint trade windows for this many of the most active repeat deployers.
        """
        return _dump(await svc.profitable_devs(min_launches, days, 20, backfill_top))

    @beta_async_tool
    async def label_address(address: str, name: str, kind: str = "service") -> str:
        """Name an address so reports show it: a router program (kind=router), a tip/fee destination (kind=service),
        or a wallet (kind=wallet). Only call when the user asked for it.

        Args:
            address: The address to label.
            name: Display name.
            kind: service, router or wallet.
        """
        return _dump(svc.label(address, name, kind))

    @beta_async_tool
    async def get_settings() -> str:
        """Return the current simulation settings (sizes, latencies, costs, exits, verdict thresholds)."""
        d = svc.cfg.to_dict()
        d["rpc"] = {"max_rps": d["rpc"]["max_rps"], "commitment": d["rpc"]["commitment"]}
        d["api"].pop("api_key", None)
        return _dump(d)

    @beta_async_tool
    async def update_setting(key: str, value: str) -> str:
        """Persistently change one setting in snipetech.toml, e.g. `snipe.buy_sol`, `snipe.entry_slot_offset`,
        `snipe.exit.take_profit`, `costs.tip_sol`, `copy.buy_latency_slots`. Only call when the user asked for it or
        agreed to it.

        Args:
            key: Dotted setting name.
            value: New value.
        """
        if key.startswith(("rpc.", "api.", "agent.")):
            return _dump({"error": "rpc/api/agent settings can only be changed by the user"})
        new = svc.cfg.set(key, value)
        path = svc.cfg.save()
        return _dump({"key": key, "value": new, "saved_to": str(path)})

    @beta_async_tool
    async def manage_list(action: str, address: str | None = None, list_name: str = "block", reason: str = "") -> str:
        """Show or change the lists the sniper and live watcher obey. Lists: allow (always snipe), block (never snipe;
        also blocks wallets funded by a blocked funder), watch_deployer (re-scored live), copy_target (followed live).
        Only add/remove when the user asked for it or agreed to it.

        Args:
            action: One of show, add, remove.
            address: Wallet address (for add/remove).
            list_name: One of allow, block, watch_deployer, copy_target.
            reason: Why (stored with the entry).
        """
        if action == "show":
            return _dump(svc.lists())
        if list_name not in LIST_NAMES or not address:
            return _dump({"error": f"need an address and list_name in {LIST_NAMES}"})
        if action == "add":
            return _dump(svc.list_add(address, list_name, reason))
        if action == "remove":
            return _dump(svc.list_remove(address, list_name))
        return _dump({"error": "action must be show, add or remove"})

    @beta_async_tool
    async def export_rules(path: str = "snipetech-rules.json") -> str:
        """Write the rules file the sniper loads (snipe/skip deployers, copy targets, sizes, exits).

        Args:
            path: Output file path.
        """
        rules = svc.export_rules(path)
        return _dump({"path": path, "snipe_deployers": len(rules["snipe_deployers"]),
                      "skip_deployers": len(rules["skip_deployers"]), "copy_follow": len(rules["copy_follow"])})  # fmt: skip

    @beta_async_tool
    async def cached_verdicts(kind: str = "deployer", limit: int = 50) -> str:
        """List cached verdicts from earlier analyses (no RPC).

        Args:
            kind: deployer or copy.
            limit: Max rows.
        """
        rows = svc.store.scores("copy" if kind == "copy" else "deployer")
        rows.sort(key=lambda r: (r.get("ev_lower_sol") or -1e9), reverse=True)
        return _dump(rows[:limit])

    return [
        analyze_deployer, analyze_copy_target, rank_copy_targets, check_mint, audit_my_wallet, optimize_snipe_strategy,
        backtest_deployer_rules, index_launches, top_deployers, discover_copy_candidates, get_settings, update_setting,
        manage_list, export_rules, cached_verdicts, profile_sniper, top_snipers, profitable_devs, label_address,
    ]  # fmt: skip


__all__ = ["make_tools", "compact_copy", "compact_deployer"]
