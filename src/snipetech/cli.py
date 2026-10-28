"""snipetech command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time
from pathlib import Path

import httpx

from . import __version__
from . import report as R
from .config import Config, dump_toml
from .service import Service
from .solana.rpc import RpcError
from .store import LIST_NAMES


def _progress(msg: str) -> None:
    print(f"  … {msg}", file=sys.stderr, flush=True)


def _out(args, data, render=None) -> None:
    if args.json or render is None:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(render(data))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="snipetech", description="Decide what to snipe and who to copy on pump.fun.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", help="config file (default ./snipetech.toml or $SNIPETECH_CONFIG)")
    p.add_argument("--db", help="SQLite database path")
    p.add_argument("--rpc", help="Solana RPC HTTP URL")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name: str, help: str, json_flag: bool = True) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help, description=help)
        if json_flag:
            sp.add_argument("--json", action="store_true", help="machine-readable output")
        return sp

    sp = cmd("init", "write a snipetech.toml with all defaults", json_flag=False)
    sp.add_argument("--force", action="store_true")

    cmd("status", "database and RPC status")
    cmd("doctor", "check RPC speed, websocket support and agent credentials")

    sp = cmd("config", "show / get / set configuration", json_flag=False)
    sp.add_argument("action", choices=["show", "get", "set"])
    sp.add_argument("key", nargs="?")
    sp.add_argument("value", nargs="?")

    sp = cmd("index-launches", "index every pump.fun launch (creates only) to find serial deployers cheaply")
    sp.add_argument("--hours", type=float, default=6.0)
    sp.add_argument("--max", type=int, default=200_000, help="max creates to fetch")

    sp = cmd("deployer", "should you snipe this deployer? (rinse rate, EV, best exits)")
    sp.add_argument("address")
    sp.add_argument("--launches", type=int, help="max launches to analyze")
    sp.add_argument("--family", action="store_true", help="include sibling wallets funded by the same wallet")
    sp.add_argument("--no-refresh", action="store_true", help="use local data only")
    _snipe_overrides(sp)

    sp = cmd("mint", "look up a launch and its deployer's verdict")
    sp.add_argument("mint")
    sp.add_argument("--analyze", action="store_true", help="analyze the deployer if it has no verdict yet")

    sp = cmd("copy", "is copytrading this wallet worth it for you?")
    sp.add_argument("wallet")
    sp.add_argument("--days", type=float)
    sp.add_argument("--size", type=float, help="copy size in SOL")
    sp.add_argument("--proportional", type=float, metavar="RATIO", help="copy RATIO x the wallet's SOL per buy instead of a fixed size")
    sp.add_argument("--latency", type=int, help="your landing latency in slots (buy and sell)")
    sp.add_argument("--no-refresh", action="store_true")

    sp = cmd("copy-rank", "rank several copy targets")
    sp.add_argument("wallets", nargs="*")
    sp.add_argument("--file", help="file with one wallet per line")
    sp.add_argument("--days", type=float)
    sp.add_argument("--no-refresh", action="store_true")

    sp = cmd("audit", "post-mortem of your own sniper wallet + latency calibration")
    sp.add_argument("wallet")
    sp.add_argument("--days", type=float, default=7.0)
    sp.add_argument("--apply-calibration", action="store_true", help="write measured latency into the config")
    sp.add_argument("--no-refresh", action="store_true")

    sp = cmd("optimize", "search entry/exit/size for one deployer or all indexed launches")
    sp.add_argument("--deployer")
    sp.add_argument("--days", type=float, default=7.0)
    _snipe_overrides(sp)

    sp = cmd("backtest", "walk-forward test of deployer-selection rules over indexed launches")
    sp.add_argument("--days", type=float, default=7.0)
    _snipe_overrides(sp)

    sp = cmd("top-deployers", "leaderboard of repeat deployers in the local data")
    sp.add_argument("--min-launches", type=int, default=3)
    sp.add_argument("--days", type=float, default=30.0)
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--backfill", type=int, default=0, metavar="K", help="first fetch trade windows for the K most active repeat deployers")

    sp = cmd("sniper", "reverse-engineer another sniper: exact fees/tips/slippage/router + inferred entries, exits, filters")
    sp.add_argument("wallet")
    sp.add_argument("--days", type=float, default=3.0)
    sp.add_argument("--no-refresh", action="store_true")
    sp.add_argument("--deep", type=int, default=0, metavar="N",
                    help="fetch the whole market during its last N holds (trailing stops etc.; ~hundreds of txs each)")

    sp = cmd("snipers", "leaderboard of wallets that land in the first slots of launches, by realized PnL")
    sp.add_argument("--days", type=float, default=3.0)
    sp.add_argument("--min-snipes", type=int, default=5)
    sp.add_argument("--limit", type=int, default=30)

    sp = cmd("profitable-devs", "dev wallets that are profitable to snipe, with the bribe needed to land early")
    sp.add_argument("--min-launches", type=int, default=3)
    sp.add_argument("--days", type=float, default=7.0)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--backfill", type=int, default=0, metavar="K", help="first fetch + fingerprint trade windows for the K most active repeat deployers")
    sp.add_argument("--quick", action="store_true", help="skip the exit optimizer (faster, but verdicts can differ from `deployer`)")

    sp = cmd("fees", "what snipers pay to land early (priority + tips) and the EV of each bribe level for you")
    sp.add_argument("--deployer", help="one deployer's launches instead of everything indexed")
    sp.add_argument("--days", type=float, default=1.0)
    sp.add_argument("--no-refresh", action="store_true")

    sp = cmd("label", "name an address (router, tip/fee destination, wallet, KOL) in reports and studies")
    sp.add_argument("address", nargs="?")
    sp.add_argument("name", nargs="?")
    sp.add_argument("--kind", choices=["service", "router", "wallet", "kol", "smart"], default="service")
    sp.add_argument("--file", help="bulk: one address per line, optional name after it (e.g. a KOL list with --kind kol)")

    sp = cmd("study", "what decides a wallet's trades: every factor at its entries/exits vs. what it passed on (needs `watch --tape`)")
    sp.add_argument("wallet")
    sp.add_argument("--days", type=float, default=1.0)
    sp.add_argument("--negatives", type=int, default=15, help="alternatives sampled per entry")
    sp.add_argument("--no-refresh", action="store_true")
    sp.add_argument("--auto-smart", type=int, default=0, metavar="N",
                    help="label the N most consistently profitable wallets in the data as 'smart' first")
    sp.add_argument("--backfill-entries", type=int, default=0, metavar="N",
                    help="for its last N entries outside the tape, fetch the market just before each (described, not contrasted)")
    sp.add_argument("--entry-window", type=int, default=120, help="seconds fetched before each backfilled entry")

    sp = cmd("discover-wallets", "find consistently profitable wallets (copy candidates) in indexed data")
    sp.add_argument("--days", type=float, default=3.0)
    sp.add_argument("--min-positions", type=int, default=5)
    sp.add_argument("--limit", type=int, default=30)

    sp = cmd("watch", "live indexer + launch/copy signals (websocket)", json_flag=False)
    sp.add_argument("--signals", choices=["all", "known", "none"], default="known")
    sp.add_argument("--poll", action="store_true", help="poll copy targets over HTTP instead of websockets")
    sp.add_argument("--no-firehose", action="store_true", help="only follow copy targets")
    sp.add_argument("--tape", action="store_true",
                    help="record every trade of every token (needed by `study`; ~1 GB/day of SQLite)")

    sp = cmd("serve", "HTTP API for your sniper", json_flag=False)
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)

    sp = cmd("agent", "chat with the Claude strategy agent", json_flag=False)
    sp.add_argument("-p", "--prompt", help="one-shot question instead of an interactive session")

    sp = cmd("list", "manage allow/block/watch/copy-target lists")
    sp.add_argument("action", choices=["add", "remove", "show"])
    sp.add_argument("address", nargs="?")
    sp.add_argument("--list", dest="list_name", choices=LIST_NAMES, default="block")
    sp.add_argument("--reason", default="")

    sp = cmd("export", "write the rules file your sniper loads (verdicts, lists, sizes, exits)", json_flag=False)
    sp.add_argument("--out", default="snipetech-rules.json")
    return p


async def doctor(svc: Service) -> dict:
    """Connectivity and capability checks for a first run."""
    import os

    import websockets

    from .ingest import MINT_AUTHORITY

    out: dict[str, dict] = {}
    cfg = svc.cfg
    t0 = time.perf_counter()
    try:
        slot = await svc.rpc.get_slot()
        out["rpc"] = {"ok": True, "detail": f"slot {slot} in {(time.perf_counter() - t0) * 1000:.0f} ms via {svc.status()['rpc']}"}
    except Exception as exc:
        out["rpc"] = {"ok": False, "detail": f"{exc} — set HELIUS_API_KEY or SNIPETECH_RPC_URL"}
        return out
    try:
        sigs = await svc.rpc.get_signatures(MINT_AUTHORITY, limit=12)
        t0 = time.perf_counter()
        txs = await svc.rpc.get_transactions([s["signature"] for s in sigs[:12]])
        dt = time.perf_counter() - t0
        ok = sum(1 for t in txs.values() if t)
        out["transactions"] = {"ok": ok == len(txs), "detail": f"{ok}/{len(txs)} recent creates fetched in {dt:.1f}s "
                               f"(~{len(txs) / max(dt, 1e-3):.1f} tx/s; paid RPCs do 20-200)"}  # fmt: skip
    except Exception as exc:
        out["transactions"] = {"ok": False, "detail": str(exc)}
    try:
        async with websockets.connect(cfg.rpc.websocket_url(), open_timeout=10) as ws:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slotSubscribe"}))
            ack = json.loads(await asyncio.wait_for(ws.recv(), 10))
            out["websocket"] = {"ok": "result" in ack, "detail": "subscriptions work (needed for `watch`)" if "result" in ack else str(ack)}
    except Exception as exc:
        out["websocket"] = {"ok": False, "detail": f"{exc} — `watch` needs a websocket-capable RPC; `watch --poll --no-firehose` works without"}
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    out["agent"] = {"ok": has_key, "detail": "ANTHROPIC_API_KEY found" if has_key else "set ANTHROPIC_API_KEY to use `snipetech agent`"}
    out["config"] = {"ok": bool(cfg.path and cfg.path.exists()), "detail": str(cfg.path) if cfg.path and cfg.path.exists() else "no snipetech.toml (defaults in use) — run `snipetech init`"}
    out["database"] = {"ok": True, "detail": f"{svc.store.path}: {svc.store.stats()}"}
    return out


def _snipe_overrides(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--buy", type=float, help="snipe size in SOL")
    sp.add_argument("--entry-slots", type=int, help="slots after create your buy lands")
    sp.add_argument("--window", type=int, help="analysis window in seconds")


def _apply_overrides(cfg: Config, args) -> None:
    if getattr(args, "buy", None) is not None:
        cfg.snipe.buy_sol = args.buy
    if getattr(args, "entry_slots", None) is not None:
        cfg.snipe.entry_slot_offset = args.entry_slots
    if getattr(args, "window", None) is not None:
        cfg.snipe.analysis_window_secs = args.window
    if getattr(args, "size", None) is not None:
        cfg.copy.size_sol = args.size
    if getattr(args, "proportional", None) is not None:
        cfg.copy.size_mode, cfg.copy.proportional_ratio = "proportional", args.proportional
    if getattr(args, "latency", None) is not None:
        cfg.copy.buy_latency_slots = cfg.copy.sell_latency_slots = args.latency


async def _run(args, cfg: Config) -> int:
    svc = Service(cfg, progress=_progress)
    try:
        c = args.cmd
        if c == "status":
            _out(args, svc.status(), lambda d: "\n".join(f"{k}: {v}" for k, v in d.items()))
        elif c == "doctor":
            _out(args, await doctor(svc), lambda d: "\n".join(f"{'OK ' if v.get('ok') else 'NO '} {k}: {v.get('detail')}" for k, v in d.items()))
        elif c == "index-launches":
            since = int(time.time() - args.hours * 3600)
            res = await svc.ingestor.backfill_launches(since, args.max)
            rows = svc.store.deployer_counts(2, since)
            data = {"launches_indexed": res.launches, "transactions": res.fetched, "repeat_deployers": len(rows),
                    "top": [{"deployer": d, "launches": n, "first_ts": a, "last_ts": b} for d, n, a, b in rows[:25]]}  # fmt: skip
            _out(args, data, lambda d: f"Indexed {d['launches_indexed']} launches; {d['repeat_deployers']} wallets launched 2+ times.\n"
                 + R.table([[x["deployer"], x["launches"], R.ago(x["last_ts"])] for x in d["top"]], ["deployer", "launches", "last"])
                 + "\nNext: `snipetech top-deployers --backfill 20` or `snipetech deployer <address>`")
        elif c == "deployer":
            rep = await svc.analyze_deployer(args.address, refresh=not args.no_refresh, max_launches=args.launches, family=args.family)
            _out(args, rep, R.render_deployer)
        elif c == "mint":
            _out(args, await svc.check_mint(args.mint, refresh=args.analyze))
        elif c == "copy":
            rep = await svc.analyze_copy(args.wallet, args.days, refresh=not args.no_refresh)
            _out(args, rep, R.render_copy)
        elif c == "copy-rank":
            wallets = list(args.wallets)
            if args.file:
                wallets += [w.strip() for w in Path(args.file).read_text().splitlines() if w.strip() and not w.startswith("#")]
            _out(args, await svc.rank_copy(wallets, args.days, refresh=not args.no_refresh), R.render_rank)
        elif c == "audit":
            rep = await svc.audit(args.wallet, args.days, refresh=not args.no_refresh)
            if args.apply_calibration and rep["calibration"].get("recommended"):
                for k, v in rep["calibration"]["recommended"].items():
                    cfg.set(k, v)
                print(f"calibration written to {cfg.save()}", file=sys.stderr)
            _out(args, rep, R.render_audit)
        elif c == "optimize":
            _out(args, await svc.optimize_snipe(args.deployer, args.days), R.render_optimize)
        elif c == "backtest":
            _out(args, await asyncio.to_thread(svc.backtest, args.days), R.render_backtest)
        elif c == "top-deployers":
            if args.backfill:
                await svc.backfill_repeat_deployers(args.min_launches, args.backfill, args.days)
            rows = await asyncio.to_thread(svc.top_deployers, args.min_launches, args.days, args.limit)
            _out(args, rows, R.render_top_deployers)
        elif c == "sniper":
            _out(args, await svc.profile_sniper(args.wallet, args.days, refresh=not args.no_refresh, deep=args.deep), R.render_sniper)
        elif c == "snipers":
            _out(args, await asyncio.to_thread(svc.top_snipers, args.days, args.min_snipes, args.limit), R.render_snipers)
        elif c == "profitable-devs":
            _out(args, await svc.profitable_devs(args.min_launches, args.days, args.limit, args.backfill, args.quick), R.render_profitable_devs)
        elif c == "fees":
            rep = await svc.fee_market(args.deployer, args.days, refresh=not args.no_refresh)
            _out(args, rep, lambda d: f"Fee market — {d['scope']}\n" + "=" * 72 + "\n"
                 + "\n".join(R.render_competition(d["competition"], d.get("tip_sweep"))))
        elif c == "label":
            if args.file:
                _out(args, svc.label_file(args.file, args.kind))
            elif args.address and args.name:
                _out(args, svc.label(args.address, args.name, args.kind))
            else:
                raise SystemExit("label: give ADDRESS NAME, or --file")
        elif c == "study":
            rep = await svc.study(args.wallet, args.days, args.negatives, refresh=not args.no_refresh, auto_smart=args.auto_smart,
                                  backfill_entries=args.backfill_entries, entry_window=args.entry_window)
            _out(args, rep, R.render_study)
        elif c == "discover-wallets":
            rows = await asyncio.to_thread(svc.discover_wallets, args.days, args.min_positions, args.limit)
            _out(args, rows, R.render_wallets)
        elif c == "watch":
            from .live import LiveWatcher

            targets = len(svc.store.list_members("copy_target"))
            how = "HTTP polling" if args.poll else cfg.rpc.websocket_url().split("?")[0]
            what = "copy targets only" if args.no_firehose else "pump.fun launches + copy targets"
            print(f"watching {what} ({targets} copy targets) via {how} — Ctrl+C to stop", file=sys.stderr)
            # SIGTERM (systemd, docker stop) shuts down like Ctrl+C so buffered trades are flushed.
            main = asyncio.current_task()
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(signum, main.cancel)
            try:
                await LiveWatcher(svc, signals=args.signals, poll=args.poll, firehose=not args.no_firehose, tape=args.tape).run()
            except asyncio.CancelledError:
                print("stopped", file=sys.stderr)
        elif c == "list":
            if args.action == "show":
                _out(args, svc.lists(), lambda d: "\n".join(
                    f"[{name}]\n" + ("\n".join(f"  {m['address']}  {m['reason'] or ''}" for m in items) or "  (empty)")
                    for name, items in d.items()))  # fmt: skip
            elif not args.address:
                print("address required", file=sys.stderr)
                return 2
            elif args.action == "add":
                _out(args, svc.list_add(args.address, args.list_name, args.reason))
            else:
                _out(args, svc.list_remove(args.address, args.list_name))
        elif c == "export":
            rules = svc.export_rules(args.out)
            print(f"wrote {args.out}: {len(rules['snipe_deployers'])} snipe, {len(rules['skip_deployers'])} skip, "
                  f"{len(rules['copy_follow'])} copy targets")  # fmt: skip
        return 0
    finally:
        await svc.close()
        svc.store.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.cmd == "init":
        path = Path(args.config or "snipetech.toml")
        if path.exists() and not args.force:
            print(f"{path} exists (use --force to overwrite)", file=sys.stderr)
            return 1
        path.write_text(dump_toml(Config().to_dict()))
        print(f"wrote {path}")
        return 0
    cfg = Config.load(args.config)
    if args.db:
        cfg.storage.db_path = args.db
    if args.rpc:
        cfg.rpc.http_url = args.rpc
    _apply_overrides(cfg, args)
    if args.cmd == "config":
        if args.action == "show":
            print(dump_toml(cfg.to_dict()))
        elif args.action == "get":
            print(cfg.get(args.key))
        else:
            cfg.set(args.key, args.value)
            print(f"{args.key} = {cfg.get(args.key)}  (saved to {cfg.save()})")
        return 0
    if args.cmd == "serve":
        from .api import serve

        serve(cfg, args.host, args.port)
        return 0
    if args.cmd == "agent":
        from .agent.runner import run_agent

        return asyncio.run(run_agent(cfg, args.prompt))
    try:
        return asyncio.run(_run(args, cfg))
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (httpx.HTTPError, RpcError) as exc:
        print(
            f"RPC error: {exc}\nEverything fetched so far is saved; re-run to resume. For speed and reliability use a "
            "paid RPC (export HELIUS_API_KEY=... or SNIPETECH_RPC_URL=...) or lower rpc.max_rps.",
            file=sys.stderr,
        )
        return 3
