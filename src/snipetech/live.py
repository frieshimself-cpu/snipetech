"""Live indexer + signal emitter.

* Subscribes to pump.fun program logs (websocket `logsSubscribe`), decodes events from
  the logs, and stores every launch plus each token's first `live_window_secs` of trades.
  Over a day or two this builds the dataset that `top-deployers`, `backtest` and
  `discover-wallets` mine — every serial deployer's full launch history, for free.
* On each new launch it emits a signal with the deployer's verdict (or the verdict of
  sibling wallets funded by the same wallet, for fresh burner deployers).
* Copy targets are followed with one subscription per wallet (or HTTP polling with
  `--poll`), and every buy/sell becomes a signal annotated with the copy verdict.

Signals go to stdout (JSON lines), the `signals` table (served by the HTTP API) and an
optional webhook.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict, defaultdict
from typing import Any

import httpx
import websockets

from .models import Launch, Trade
from .service import Service
from .solana.normalize import normalize
from .solana.programs import LAUNCHLAB_PROGRAM, PUMP_AMM_PROGRAM, PUMP_PROGRAM
from .solana.tx import TxEvents, extract_from_logs

log = logging.getLogger(__name__)

ACTIONS = {
    "SNIPE": "snipe",
    "ALLOW": "snipe",
    "MARGINAL": "snipe_small",
    "AVOID": "skip",
    "BLOCK": "skip",
    "INSUFFICIENT_DATA": "unknown",
    "UNKNOWN": "unknown",
    None: "unknown",
}


class SignalSink:
    def __init__(self, service: Service, webhook: str = "", echo: bool = True):
        self.service = service
        self.webhook = webhook
        self.echo = echo
        self.http = httpx.AsyncClient(timeout=5) if webhook else None

    async def emit(self, kind: str, data: dict[str, Any]) -> None:
        sid = self.service.store.add_signal(kind, data)
        payload = {"id": sid, "kind": kind, "ts": int(time.time()), **data}
        if self.echo:
            print(json.dumps(payload, default=str), flush=True)
        if self.http is not None:
            try:
                await self.http.post(self.webhook, json=payload)
            except httpx.HTTPError as exc:
                log.warning("webhook failed: %s", exc)

    async def close(self) -> None:
        if self.http is not None:
            await self.http.aclose()


class LiveWatcher:
    def __init__(self, service: Service, signals: str = "known", poll: bool = False, firehose: bool = True,
                 tape: bool = False):
        self.svc = service
        self.tape = tape  # record every trade of every token (not just each launch's first minutes)
        self.cfg = service.cfg
        self.store = service.store
        self.sink = SignalSink(service, self.cfg.live.webhook_url)
        self.signals = signals  # "all" | "known" | "none"
        self.poll = poll
        self.firehose = firehose
        self.recent: OrderedDict[str, int] = OrderedDict()  # mint -> launch ts (live window)
        self.slot_pos: dict[int, int] = defaultdict(int)
        self.buffer: list[Trade] = []
        self.fbuffer: list[Trade] = []  # foreign-quoted trades (tape mode)
        self.parked: dict[str, list[tuple[int, dict]]] = {}  # pool -> log notifications waiting for its mints
        self.pools = self.store.pools()
        self.gaps: list[tuple[float, float]] = []
        self.started = time.time()
        self.disconnected_at: float | None = time.time()
        self.closed_launches: list[Launch] = []
        self.due: set[str] = set()
        self.stats: defaultdict[str, int] = defaultdict(int)
        self.span_start: int | None = None  # current gap-free stretch of the firehose
        self.tasks: set[asyncio.Task] = set()  # strong refs so fire-and-forget tasks aren't collected
        self.lookups = asyncio.Semaphore(4)
        self.lookups_waiting = 0

    # -- entry point --------------------------------------------------------------------------

    async def run(self) -> None:
        tasks = [asyncio.create_task(self._maintenance())]
        if self.firehose:
            tasks.append(asyncio.create_task(self._firehose(PUMP_PROGRAM)))
            if self.cfg.live.index_amm:
                tasks.append(asyncio.create_task(self._firehose(PUMP_AMM_PROGRAM)))
            if self.tape:
                tasks.append(asyncio.create_task(self._firehose(LAUNCHLAB_PROGRAM)))
        tasks.append(asyncio.create_task(self._follow_wallets()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self._flush()
            await self.sink.close()

    # -- firehose ------------------------------------------------------------------------------

    async def _firehose(self, program: str) -> None:
        url = self.cfg.rpc.websocket_url()
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, max_size=2**25, ping_interval=20, open_timeout=20) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                        "params": [{"mentions": [program]}, {"commitment": self.cfg.rpc.commitment}],
                    }))  # fmt: skip
                    ack = json.loads(await ws.recv())
                    if "error" in ack:
                        raise RuntimeError(f"logsSubscribe rejected: {ack['error']}")
                    self._connected()
                    log.info("subscribed to %s logs", program[:8])
                    backoff = 1.0
                    async for raw in ws:
                        msg = json.loads(raw)
                        res = (msg.get("params") or {}).get("result")
                        if res:
                            self._on_logs(res["context"]["slot"], res["value"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # reconnect on anything network-shaped
                self._disconnected()
                log.warning("firehose %s dropped (%s); reconnecting in %.0fs", program[:8], exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _connected(self) -> None:
        if self.disconnected_at is not None:
            self.gaps.append((self.disconnected_at, time.time()))
            self.disconnected_at = None
            self.span_start = int(time.time()) + 1

    def _disconnected(self) -> None:
        if self.disconnected_at is None:
            self.disconnected_at = time.time()

    def _on_logs(self, slot: int, value: dict[str, Any]) -> None:
        if value.get("err") is not None:
            return
        events = extract_from_logs(value.get("logs") or [])
        if not events:
            return
        pos = self.slot_pos[slot]
        self.slot_pos[slot] += 1
        if len(self.slot_pos) > 5000:
            for s in sorted(self.slot_pos)[:2500]:
                del self.slot_pos[s]
        # receive time stands in for block time (LaunchLab events carry no timestamp; pump's do)
        tx = TxEvents(value["signature"], slot, int(time.time()), "", events)
        norm = normalize(tx, pos, self.pools, foreign=self.tape)
        if norm.unresolved_pools and self.tape:
            self._park(norm.unresolved_pools, slot, value)
        if norm.pools:
            self.pools.update(norm.pools)
            self.store.add_pools(norm.pools)
        window = self.cfg.storage.live_window_secs
        for launch in norm.launches:
            self.stats["launches"] += 1
            self.store.add_launches([launch])
            self.recent[launch.mint] = launch.ts
            task = asyncio.get_running_loop().create_task(self._on_launch(launch))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        for t in norm.trades:
            lts = self.recent.get(t.mint)
            if self.tape or (lts is not None and t.ts - lts <= window):
                self.buffer.append(t)
                self.stats["trades"] += 1
        if norm.foreign:
            self.fbuffer.extend(norm.foreign)
            self.stats["foreign_trades"] += len(norm.foreign)
        if norm.completions:
            self.store.add_completions(norm.completions)
        if norm.migrations:
            self.store.add_migrations(norm.migrations)
        while self.recent and next(iter(self.recent.values())) < time.time() - window - 60:
            mint, ts = self.recent.popitem(last=False)
            launch = self.store.launch(mint)
            if launch:
                self.closed_launches.append(launch)
        if len(self.buffer) + len(self.fbuffer) >= 500:
            self._flush()

    def _park(self, pools: set[str], slot: int, value: dict[str, Any]) -> None:
        """Logs don't say which token a LaunchLab / PumpSwap pool trades: look the pool up once and
        replay what arrived meanwhile."""
        for pool in pools:
            if pool in self.pools:
                continue  # resolved already; nothing more to learn
            waiting = self.parked.setdefault(pool, [])
            waiting.append((slot, value))
            if len(waiting) == 1:
                task = asyncio.get_running_loop().create_task(self._resolve_pool(pool))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

    async def _resolve_pool(self, pool: str) -> None:
        async with self.lookups:
            try:
                found = await asyncio.wait_for(self.svc.ingestor.resolve_pools({pool}), timeout=10)
            except Exception as exc:  # best effort: the pool's trades stay unrecorded
                log.debug("pool %s lookup failed: %s", pool, exc)
                found = {}
        waiting = self.parked.pop(pool, [])
        if pool in found:
            self.pools.update(found)
            for slot, value in waiting:
                self._on_logs(slot, value)
        else:
            self.stats["unresolved_pool_events"] += len(waiting)

    def _flush(self) -> None:
        if self.buffer:
            self.store.add_trades(self.buffer)
            self.buffer = []
        if self.fbuffer:
            self.store.add_ftrades(self.fbuffer)
            self.fbuffer = []

    # -- signals ---------------------------------------------------------------------------------

    async def _on_launch(self, launch: Launch) -> None:
        # Creator wallet first (stable identity), then the signer, then the signer's funding family.
        order = [launch.deployer] + [a for a in (launch.user,) if a != launch.deployer]
        verdicts = {a: self.svc.deployer_verdict(a) for a in order}

        def decisive(v: dict) -> str | None:
            if v.get("verdict") not in (None, "UNKNOWN", "INSUFFICIENT_DATA"):
                return v["verdict"]
            return (v.get("family") or {}).get("verdict")

        lookup = not any(decisive(v) for v in verdicts.values()) and self.cfg.live.funder_lookup and self.signals != "none"
        if lookup and self.lookups_waiting < 50:  # drop lookups rather than fall behind the chain
            self.lookups_waiting += 1
            try:
                async with self.lookups:
                    await asyncio.wait_for(self.svc.ingestor.find_funder(launch.user, max_pages=1), timeout=8)
                verdicts[launch.user] = self.svc.deployer_verdict(launch.user)
            except Exception:
                self.stats["funder_lookup_failed"] += 1
            finally:
                self.lookups_waiting -= 1
        decided = [d for d in (decisive(verdicts[a]) for a in order) if d]
        verdict = "BLOCK" if "BLOCK" in decided else (decided[0] if decided else None)
        if self.signals == "none" or (self.signals == "known" and verdict is None):
            return
        await self.sink.emit("launch", {
            "mint": launch.mint, "name": launch.name, "symbol": launch.symbol, "deployer": launch.deployer,
            "signer": launch.user, "slot": launch.slot, "mayhem": launch.mayhem,
            "action": ACTIONS.get(verdict, "unknown"), "verdict": verdict, "deployers": verdicts,
        })  # fmt: skip

    async def _follow_wallets(self) -> None:
        """Follow copy targets: websocket per wallet, or HTTP polling."""
        running: dict[str, asyncio.Task] = {}
        while True:
            targets = {m["address"] for m in self.store.list_members("copy_target")}
            for w in targets - running.keys():
                fn = self._poll_wallet if self.poll else self._ws_wallet
                running[w] = asyncio.create_task(fn(w))
            for w in list(running.keys() - targets):
                running.pop(w).cancel()
            await asyncio.sleep(30)

    async def _ws_wallet(self, wallet: str) -> None:
        url = self.cfg.rpc.websocket_url()
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, open_timeout=20) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                        "params": [{"mentions": [wallet]}, {"commitment": self.cfg.rpc.commitment}],
                    }))  # fmt: skip
                    await ws.recv()
                    backoff = 1.0
                    async for raw in ws:
                        res = (json.loads(raw).get("params") or {}).get("result")
                        if res and res["value"].get("err") is None:
                            await self._on_wallet_tx(wallet, [{"signature": res["value"]["signature"], "slot": res["context"]["slot"], "err": None}])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("wallet stream %s dropped (%s)", wallet[:8], exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _poll_wallet(self, wallet: str, every: float = 2.0) -> None:
        last = None
        while True:
            try:
                page = await self.svc.rpc.get_signatures(wallet, until=last, limit=50)
                page = [s for s in page if s.get("err") is None]
                if page:
                    if last is not None:
                        await self._on_wallet_tx(wallet, page)
                    last = page[0]["signature"]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("poll %s failed: %s", wallet[:8], exc)
            await asyncio.sleep(every)

    async def _on_wallet_tx(self, wallet: str, infos: list[dict]) -> None:
        await self.svc.ingestor.ingest(infos)
        verdict = self.store.get_score("copy", wallet) or {"verdict": "UNKNOWN"}
        for info in infos:
            rows = self.store._q(
                "SELECT mint, is_buy, quote, fee, venue, slot FROM trades WHERE sig=? AND user=?", (info["signature"], wallet)
            )
            for mint, is_buy, quote, fee, venue, slot in rows:
                await self.sink.emit("copy", {
                    "wallet": wallet, "mint": mint, "side": "buy" if is_buy else "sell", "sol": (quote + fee) / 1e9,
                    "venue": venue, "slot": slot, "signature": info["signature"],
                    "copy_verdict": verdict.get("verdict"), "recommended": verdict.get("recommended"),
                })  # fmt: skip

    # -- maintenance -------------------------------------------------------------------------------

    async def _maintenance(self) -> None:
        last_prune = 0.0
        while True:
            await asyncio.sleep(min(10, self.cfg.live.rescore_interval_secs))
            self._flush()
            self._mark_coverage()
            if self.tape:
                self._mark_tape()
            now = time.time()
            if now - last_prune > 3600:
                self.store.prune(int(now - self.cfg.storage.retain_days * 86400))
                last_prune = now
            await self._rescore()
            log.info("live: %s", dict(self.stats))

    def _mark_tape(self) -> None:
        """Time ranges in which every trade of every token was recorded (no firehose gap)."""
        if self.disconnected_at is not None or self.span_start is None:
            return
        cov = self.store.coverage("tape")
        spans = [s for s in cov.get("spans", []) if s[0] != self.span_start]
        spans.append([self.span_start, int(time.time()) - 2])
        cov["spans"] = sorted(spans)[-500:]
        self.store.set_coverage("tape", cov)

    def _mark_coverage(self) -> None:
        """A launch's window counts as indexed only if the firehose had no gap during it."""
        window = self.cfg.storage.live_window_secs
        done, self.closed_launches = self.closed_launches, []
        for l in done:
            lo, hi = l.ts - 1, l.ts + window
            gaps = self.gaps + ([(self.disconnected_at, time.time())] if self.disconnected_at else [])
            if any(g0 < hi and g1 > lo for g0, g1 in gaps):
                continue
            cov = self.store.coverage(f"mint:{l.mint}")
            cov["from_ts"] = min(cov.get("from_ts", lo), lo)
            cov["to_ts"] = max(cov.get("to_ts", 0), hi)
            cov["live"] = True
            self.store.set_coverage(f"mint:{l.mint}", cov)
            self.stats["windows_closed"] += 1
            self.due.add(l.user)

    async def _rescore(self) -> None:
        """Re-score deployers whose launch windows just closed, from local data only."""
        watch = {m["address"] for m in self.store.list_members("watch_deployer")}
        due, self.due = self.due, set()
        for dep in due | watch:
            if len(self.store.launches_by_deployer(dep, 2)) < 2:
                continue
            try:
                await self.svc.analyze_deployer(dep, refresh=False, optimize=False)
                self.stats["rescored"] += 1
            except Exception as exc:
                log.warning("rescore %s failed: %s", dep[:8], exc)
