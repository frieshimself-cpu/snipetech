"""snipetech command line."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import httpx

from . import __version__
from . import report as R
from .config import Config, dump_toml
from .oktech import OktechError
from .service import Service
from .solana.rpc import RpcError
from .store import LIST_NAMES


def _progress(msg: str) -> None:
    print(f"  … {msg}", file=sys.stderr, flush=True)


def _discover(args, cfg) -> int:
    """`snipetech discover run`: build the data clients the environment has keys for, run every source, print the summary."""
    import time

    from .discovery.run import run_discover
    from .discovery.sources import (
        BirdeyeSource,
        CoinFirstBuyersSource,
        DiscoverContext,
        OwnerSource,
        SolanaTrackerSource,
        TapeSource,  # fmt: skip
        VybeSource,
        default_sources,
    )
    from .service import Service

    svc = Service(cfg)
    clients = []
    try:
        ctx = DiscoverContext(svc=svc, cfg=cfg, now=time.time(), log=lambda m: print(m, file=sys.stderr, flush=True))
        if os.environ.get("BIRDEYE_API_KEY"):
            from .birdeye import Birdeye

            ctx.birdeye = Birdeye(
                budget_cu=min(cfg.birdeye.daily_cu, ctx.budgets["birdeye_cu"]), counter=_usage(svc, "birdeye")
            )
            clients.append(ctx.birdeye)
        if os.environ.get("SOLANATRACKER_API_KEY"):
            from .solanatracker import SolanaTracker

            ctx.solanatracker = SolanaTracker(
                budget_requests=ctx.budgets["solanatracker"], counter=_usage(svc, "solanatracker")
            )
            clients.append(ctx.solanatracker)
        if os.environ.get("VYBE_API_KEY"):
            from .vybe import Vybe

            ctx.vybe = Vybe(budget_requests=ctx.budgets["vybe"], counter=_usage(svc, "vybe"))
            clients.append(ctx.vybe)
        if args.sources:
            table = {"tape": TapeSource, "owner": OwnerSource, "solanatracker": lambda: SolanaTrackerSource(90),
                     "solanatracker30": lambda: SolanaTrackerSource(30, pages=3), "birdeye": lambda: BirdeyeSource(top_coins=cfg.birdeye.top_coins),
                     "coinfirst": CoinFirstBuyersSource, "vybe": VybeSource}  # fmt: skip
            sources = [table[n.strip()]() for n in args.sources.split(",") if n.strip()]
        else:
            sources = default_sources(ctx)
        out = run_discover(ctx, sources, pace=not args.sources)  # sources asked for by name look now
        out["sources"] = [s.name for s in sources]
        print(json.dumps(out, indent=1, default=str))
        return 0
    finally:
        for c in clients:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        svc.reset_tape_store()
        svc.store.close()


def _measure(args, cfg) -> int:
    import time

    from .measure.run import backfill, run_measure
    from .service import Service

    svc = Service(cfg)

    if args.action == "report":
        from .measure.report import fee_test_report, render_fee_test

        try:
            rep = fee_test_report(svc.store, cfg.operator.wallet, args.since if args.since is not None else int(time.time() - 86400),
                                  until_ts=args.until)  # fmt: skip
        finally:
            svc.store.close()
        print(json.dumps(rep, indent=1, default=str) if args.json else render_fee_test(rep))
        return 0

    async def go():
        try:
            if args.action == "backfill":
                if args.since is None:
                    raise SystemExit("measure backfill: --since <unix seconds> is required")
                return await backfill(svc, cfg, args.since, args.until or int(time.time()))
            return await run_measure(svc, cfg, time.time(), since_ts=args.since)
        finally:
            await svc.close()
            svc.store.close()

    print(json.dumps(asyncio.run(go()), indent=1, default=str))
    return 0


def _events(args, cfg) -> int:
    import time

    from .events.run import poll_feeds, poll_loop, scan_tape
    from .service import Service

    svc = Service(cfg)
    clients: dict = {}
    try:
        if args.action == "edge":
            from .analysis.lab_lock import lab_lock
            from .events.run import event_edge

            # where in the block after the event the buy lands (the hunt landing unless --landing says another)
            with lab_lock(Path(cfg.storage.db_path).resolve().parent, wait=True):
                out = event_edge(svc.store, cfg, time.time(), landing=args.landing, tape=svc.tape_store)
            print(json.dumps(out, indent=1, default=str))
            return 0
        if args.action == "scan":
            from .events.dev import dev_transfers

            out = scan_tape(svc.store, time.time(), hours=args.hours, tape=svc.tape_store)

            async def dev():
                try:
                    return await dev_transfers(svc, int(time.time() - args.hours * 3600), now=time.time())
                finally:
                    await svc.close()

            out["dev"] = asyncio.run(dev())
            print(json.dumps(out, indent=1))
            return 0
        if os.environ.get("SOLANATRACKER_API_KEY"):
            from .solanatracker import SolanaTracker

            clients["solanatracker"] = SolanaTracker(counter=_usage(svc, "solanatracker"))
        if os.environ.get("BIRDEYE_API_KEY"):
            from .birdeye import Birdeye

            clients["birdeye"] = Birdeye(budget_cu=cfg.birdeye.daily_cu, counter=_usage(svc, "birdeye"))
        if args.raw:
            out = {}
            st = clients.get("solanatracker")
            be = clients.get("birdeye")
            if st is not None:
                out["solanatracker_trending"] = st._get("/tokens/trending/1h", {})
                out["solanatracker_graduating"] = st._get("/tokens/multi/graduating", {})
            if be is not None:
                out["birdeye_trending"] = be._get(
                    "/defi/token_trending", {"sort_by": "rank", "sort_type": "asc", "offset": 0, "limit": 3}, 30
                )
            text = json.dumps(out, indent=1, default=str)
            print(text[:6000])
            return 0
        if args.once:
            print(
                json.dumps(
                    poll_feeds(svc.store, clients, time.time(), log=lambda m: print(m, file=sys.stderr, flush=True)),
                    indent=1,
                )
            )
            return 0
        poll_loop(svc.store, clients, interval=args.interval, log=lambda m: print(m, flush=True))
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        for c in clients.values():
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
        svc.store.close()


async def dump_transaction(svc, signature: str) -> str:
    """One getTransaction result (version 1, json encoding) as text; the way real fixtures reach tests/fixtures/tx/."""
    tx = await svc.rpc.get_transaction(signature)
    return (
        json.dumps(tx, indent=1, default=str)
        if tx is not None
        else json.dumps({"error": "not found", "signature": signature})
    )


def _usage(svc, provider: str):
    """A counter the data clients call after each request; the store's monthly tally feeds the operator's resource checks."""

    def count(n: int) -> None:
        try:
            svc.store.usage_add(provider, int(n))
        except Exception:  # noqa: BLE001 - never let bookkeeping break a query
            pass

    return count


def _usage_from_cfg(cfg, provider: str):
    """Same, for commands that open no Service: a short-lived Store on the configured database."""
    from .store import Store

    def count(n: int) -> None:
        try:
            st = Store(cfg.storage.db_path)
            try:
                st.usage_add(provider, int(n))
            finally:
                st.close()
        except Exception:  # noqa: BLE001
            pass

    return count


def _out(args, data, render=None) -> None:
    if args.json or render is None:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(render(data))


def _position(v: str) -> str:
    if v in ("start", "middle", "end") or (v.startswith("+") and v[1:].isdigit()):
        return v
    raise argparse.ArgumentTypeError("start, middle, end or +N (e.g. +1)")


def _trigger(v: str):
    """--trigger: an agent's own hypothesis, refused with its reason before any lab starts (lab.parse_trigger)."""
    from .analysis.lab import parse_trigger

    try:
        return parse_trigger(v)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _hours(v: str) -> float:
    """--hours: a window of the newest tape; an infinite one would crash the job after it took the lab lock."""
    h = float(v)
    if not 0 < h < float("inf"):
        raise argparse.ArgumentTypeError("a number of hours above 0")
    return h


def _sol_or_off(v: str) -> float | str:
    """operate risk --day-stop / --floor: a number of SOL, or off (the owner's widened yes of 7 Nov ~12:10 UTC: "Just
    make it so live trading never stops"). Anything else is refused without being repeated (it may be a pasted key)."""
    word = str(v).strip().lower()
    if word == "off":
        return "off"
    try:
        return float(word)
    except ValueError:
        raise argparse.ArgumentTypeError("a number of SOL, or off") from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="snipetech", description="Decide what to snipe and who to copy on pump.fun.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--config", help="config file (default ./snipetech.toml or $SNIPETECH_CONFIG)")
    p.add_argument("--db", help="SQLite database path")
    p.add_argument("--rpc", help="Solana RPC HTTP URL")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--profile", action="store_true",
                   help="run the command as it is and write the functions that took the most time to "
                        "jobs/profile-<command>-<time>.log in the data directory (a lab then runs on one process)")  # fmt: skip
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name: str, help: str, json_flag: bool = True) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help, description=help)
        if json_flag:
            sp.add_argument("--json", action="store_true", help="machine-readable output")
        return sp

    sp = cmd("init", "write a snipetech.toml with all defaults", json_flag=False)
    sp.add_argument("--force", action="store_true")

    cmd("status", "database and RPC status")
    sp = cmd("doctor", "check RPC speed, websocket support and agent credentials")
    sp.add_argument("--stream-replay", type=int, default=0, metavar="SLOTS",
                    help="instead: subscribe the tape's stream this many slots back and print the order its "
                         "transactions, slot ticks and block summaries came in (how a recorder may resume)")  # fmt: skip
    sp.add_argument("--secs", type=float, default=20.0, help="how long --stream-replay listens")
    sp.add_argument("--settings", action="store_true",
                    help="instead: what the settings file sets differently from today's defaults (a file written by "
                         "`init` pins the defaults of its day; values that could hold a key are not shown)")  # fmt: skip

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
    sp.add_argument(
        "--proportional",
        type=float,
        metavar="RATIO",
        help="copy RATIO x the wallet's SOL per buy instead of a fixed size",
    )
    sp.add_argument("--latency", type=int, help="your landing latency in slots (buy and sell)")
    sp.add_argument("--no-refresh", action="store_true")

    sp = cmd("copy-rank", "rank several copy targets")
    sp.add_argument("wallets", nargs="*")
    sp.add_argument("--file", help="file with one wallet per line")
    sp.add_argument("--days", type=float)
    sp.add_argument("--no-refresh", action="store_true")
    sp.add_argument("--source", choices=["tape", "birdeye"], default="tape",
                    help="birdeye: a 30-day PnL summary per wallet from Birdeye (30 CU each) instead of replaying RPC history")  # fmt: skip

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
    sp.add_argument(
        "--backfill",
        type=int,
        default=0,
        metavar="K",
        help="first fetch trade windows for the K most active repeat deployers",
    )

    sp = cmd(
        "sniper", "reverse-engineer another sniper: exact fees/tips/slippage/router + inferred entries, exits, filters"
    )
    sp.add_argument("wallet")
    sp.add_argument("--days", type=float, default=3.0)
    sp.add_argument("--no-refresh", action="store_true")
    sp.add_argument(
        "--deep",
        type=int,
        default=0,
        metavar="N",
        help="fetch the whole market during its last N holds (trailing stops etc.; ~hundreds of txs each)",
    )

    sp = cmd("snipers", "leaderboard of wallets that land in the first slots of launches, by realized PnL")
    sp.add_argument("--days", type=float, default=3.0)
    sp.add_argument("--min-snipes", type=int, default=5)
    sp.add_argument("--limit", type=int, default=30)

    sp = cmd(
        "rescore",
        "score again the deployers a launch of whom closed since their last score, from the tape's copy (the recorder "
        "did this beside its streams until 11 Nov)",
    )
    sp.add_argument("--limit-secs", type=float, default=900.0, help="stop starting analyses after this long")
    sp.add_argument("--hours", type=float, default=24.0, help="a launch that closed longer ago makes nobody due")
    sp = cmd("profitable-devs", "dev wallets that are profitable to snipe, with the bribe needed to land early")
    sp.add_argument("--min-launches", type=int, default=3)
    sp.add_argument("--days", type=float, default=7.0)
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument(
        "--backfill",
        type=int,
        default=0,
        metavar="K",
        help="first fetch + fingerprint trade windows for the K most active repeat deployers",
    )
    sp.add_argument(
        "--quick", action="store_true", help="skip the exit optimizer (faster, but verdicts can differ from `deployer`)"
    )

    sp = cmd("fees", "what snipers pay to land early (priority + tips) and the EV of each bribe level for you")
    sp.add_argument("--deployer", help="one deployer's launches instead of everything indexed")
    sp.add_argument("--days", type=float, default=1.0)
    sp.add_argument("--no-refresh", action="store_true")

    sp = cmd("label", "name an address (router, tip/fee destination, wallet, KOL) in reports and studies")
    sp.add_argument("address", nargs="?")
    sp.add_argument("name", nargs="?")
    sp.add_argument(
        "--kind",
        choices=["service", "router", "wallet", "kol", "smart", "dev", "own", "bundler", "cand"],
        default="service",
        help="cand: a wallet picked by hand as a copy candidate; it is queued for qualify ahead of the backlog",
    )
    sp.add_argument(
        "--file", help="bulk: one address per line, optional name after it (e.g. a KOL list with --kind kol)"
    )

    sp = cmd(
        "lab",
        "search the recorded market (`watch --tape`) for strategies that pay at your speed: trigger x exit, out of sample",
    )
    sp.add_argument("--latency", type=int, default=0, help="slots between the trigger and your buy (0 = same slot)")
    sp.add_argument(
        "--position",
        type=_position,
        default="middle",
        help="where in that slot you land: start, middle, end, or +N (N trades in the token ahead of you); launch, "
        "hunt, whale and burst triggers are always scored where a hunt buy lands (a launch a slot after the create)",
    )
    sp.add_argument("--buy", type=float, default=0.2, help="SOL per buy")
    sp.add_argument("--per-tx", type=float, default=None,
                    help="SOL per transaction (priority + tip + base + failed duplicate sends; default: the measured "
                         "cost, half the low bid's measured round trip, the last one measured after a week without "
                         "enough fills, 0.0045 until one is measured, as `paper freeze` charges; the output names the "
                         "cost and where it came from)")  # fmt: skip
    sp.add_argument("--top", type=int, default=10)
    sp.add_argument(
        "--by-wallet",
        action="store_true",
        help="also rank the labeled wallets (kol / tracked list) whose first buy in a coin is worth our exit",
    )
    sp.add_argument("--trigger", action="append", type=_trigger, metavar="JSON",
                    help="run only this trigger (repeatable), an agent's own hypothesis: a JSON object with the "
                         'trigger\'s fields, e.g. \'{"kind": "hunt", "sol": 4, "window": 1.5, "dev_max": 1}\'; each is '
                         "scored where it trades live (launch, hunt, whale and burst where a hunt buy lands, kol and "
                         "momentum at the copy landing, `copy_landing` in the output): --latency / --position do not "
                         "apply to it")  # fmt: skip
    sp.add_argument("--copy-position", metavar="POS",
                    help="with --trigger: score copies (kol, momentum) at this place in the followed wallet's slot "
                         "instead of the measured copy landing: +0 right behind it, +N behind N more trades, +Nx behind "
                         "the N-th buy across slots. A closer place is bought with a bid: charge it with --per-tx")  # fmt: skip
    sp.add_argument("--hours", type=_hours, metavar="N", help="only the newest N hours of tape")

    sp = cmd(
        "sweep",
        "sell every pump.fun coin the sniper wallet holds (SNIPER_SECRET), on its curve or, once it has left it, on "
        "its PumpSwap pool: the exit OKTech can drop",
    )
    sp.add_argument("--mint", action="append", help="only these mints (repeatable)")
    sp.add_argument(
        "--slippage", type=float, default=0.5, help="SOL floor below the quote, 0-1 (default 0.5: a sweeper wants out)"
    )
    sp.add_argument("--dry-run", action="store_true", help="show holdings and quotes, send nothing")

    sp = cmd(
        "follow",
        "who is worth following right after they buy: crowd follow-through, the copy bots racing them, the bribe to land first",
    )
    sp.add_argument("wallets", nargs="*")
    sp.add_argument("--kols", action="store_true", help="every wallet labeled kol (`label --file kols.txt --kind kol`)")
    sp.add_argument("--file", help="wallets, one per line")
    sp.add_argument("--days", type=float, default=2.0)
    sp.add_argument("--no-refresh", action="store_true")

    sp = cmd("oktech", "drive OKTech snipers from snipetech's verdicts: status | plan | push (key in $OKS_KEY)")
    sp.add_argument("action", choices=["status", "plan", "push", "replica", "probe", "strategy", "copylist", "copyaudit", "guard", "cycle", "holdings", "show", "start",
                                       "stop", "delete", "docs"])  # fmt: skip
    sp.add_argument(
        "--allow-uncapped", action="store_true", help="guard: also start snipers without a buy cap (not recommended)"
    )
    sp.add_argument("--watch-only", action="store_true",
                    help="guard: watch snipers that are already running without calling start (after a deploy killed the guard)")  # fmt: skip
    sp.add_argument("--force", action="store_true",
                    help="start: even under the owner's /stop; stop/delete: even while the wallet holds a coin (strands it)")  # fmt: skip
    sp.add_argument(
        "--id",
        action="append",
        help="show/start/stop/delete/guard: the sniper's id (from `oktech status`); guard takes several",
    )
    sp.add_argument("--address", help="guard: the buying wallet's public address (its balance is watched)")
    sp.add_argument(
        "--floor", type=float, help="guard: stop everything if the wallet's balance falls below this many SOL"
    )
    sp.add_argument("--max-minutes", type=float, default=60, help="guard: stop everything after this long")
    sp.add_argument("--max-fills", type=int, default=10, help="cycle: stop re-arming after this many buys")
    sp.add_argument(
        "--rearm-after", type=float, default=60, help="cycle: re-arm an idle sniper after this many seconds"
    )
    sp.add_argument(
        "--ignore-mint",
        action="append",
        help="cycle: a held coin no sniper manages (sold by hand); never counts as open",
    )
    sp.add_argument("--copy-wallet", help="probe: also measure copy speed against this wallet's buys")
    sp.add_argument("--snipes", type=int, default=5, help="probe: buys per speed-test sniper (default 5)")
    sp.add_argument(
        "--via",
        choices=["hunt", "crowd"],
        default="hunt",
        help="strategy: how OKTech gates the big-buy trigger: hunt (fires on every launch, measured) or crowd (buys in the create tx)",
    )
    sp.add_argument(
        "--no-preexec", action="store_true", help="strategy: send buys without OKTech's local pre-execution dry run"
    )
    sp.add_argument(
        "--copy",
        action="append",
        metavar="ADDRESS[:LABEL[:MIN_SOL[:EXIT]]]",
        help="copylist: a wallet to copy (repeatable); only its buys >= MIN_SOL (default 1); EXIT lab|hold60",
    )
    sp.add_argument(
        "--exit", choices=["lab", "hold60"], default="lab", help="copylist: default exit for wallets without one"
    )
    sp.add_argument("--priority", type=float, help="copylist: priority fee in SOL per buy (OKTech minimum 0.001)")
    sp.add_argument("--tip", type=float, help="copylist: tip in SOL per buy (OKTech minimum 0.002)")
    sp.add_argument(
        "--send-count", type=int, help="copylist: extraParams.globalSendCount (1 = send each transaction once)"
    )
    sp.add_argument(
        "--tag", default="", help="copylist: word appended to the sniper names (e.g. arm:control / arm:paid)"
    )
    sp.add_argument("--allow-overlap", action="store_true",
                    help="copylist/push: create even when an active sniper already copies that leader (a deliberate test "
                         "on another OKTech wallet; one wallet buys once per signal)")  # fmt: skip
    sp.add_argument(
        "--compute-units",
        type=int,
        help="copylist: extraParams.computeUnits (OKTech default 150000; our buys use 54-79k)",
    )
    sp.add_argument(
        "--since-minutes", type=float, default=120, help="copyaudit: look at our buys in the last N minutes"
    )
    sp.add_argument(
        "--stale", type=float, default=60, help="strategy: dead exit after this many seconds without a trade (0 = off)"
    )
    sp.add_argument("--only", choices=["copy", "dev"], help="only copy-trading (ctbot) or only dev snipers")
    sp.add_argument("--from-study", metavar="WALLET", help="replica: rebuild this studied wallet's rules as a sniper")
    sp.add_argument(
        "--buy", type=float, help="SOL per buy: replica (default 0.05), probe (0.01), strategy (the frozen size)"
    )
    sp.add_argument(
        "--create", action="store_true", help="replica: create it on OKTech (idle; start it with `oktech guard`)"
    )
    sp.add_argument(
        "--wallet", help="OKTech wallet name that buys (default: [oktech] wallet, else your OKTech default)"
    )
    sp.add_argument(
        "--prune", action="store_true", help="also remove snipetech snipers whose verdict no longer supports them"
    )
    sp.add_argument("--yes", action="store_true", help="push without asking")

    sp = cmd(
        "study",
        "what decides a wallet's trades: every factor at its entries/exits vs. what it passed on (needs `watch --tape`)",
    )
    sp.add_argument("wallet")
    sp.add_argument("--days", type=float, default=1.0)
    sp.add_argument("--negatives", type=int, default=15, help="alternatives sampled per entry")
    sp.add_argument("--no-refresh", action="store_true")
    sp.add_argument(
        "--auto-smart",
        type=int,
        default=0,
        metavar="N",
        help="label the N most consistently profitable wallets in the data as 'smart' first",
    )
    sp.add_argument(
        "--backfill-entries",
        type=int,
        default=0,
        metavar="N",
        help="for its last N entries outside the tape, fetch the market just before each (described, not contrasted)",
    )
    sp.add_argument("--entry-window", type=int, default=120, help="seconds fetched before each backfilled entry")

    sp = cmd("discover-wallets", "find consistently profitable wallets (copy candidates) in indexed data")
    sp.add_argument("--days", type=float, default=3.0)
    sp.add_argument("--min-positions", type=int, default=5)
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("--source", choices=["tape", "birdeye", "solanatracker"], default="tape",
                    help="birdeye: Birdeye gainers + top traders of the tape's hottest coins; solanatracker: the 30/90-day realised-PnL leaderboard")  # fmt: skip
    sp.add_argument("--top-coins", type=int, help="birdeye: hottest coins to pull top traders for (default config)")
    sp.add_argument("--pnl-top", type=int, help="birdeye: candidates that get a 30-day PnL summary (default config)")
    sp.add_argument("--pages", type=int, help="solanatracker: leaderboard pages of 100 (default config)")

    sp = cmd("watch", "live indexer + launch/copy signals (websocket)", json_flag=False)
    sp.add_argument("--signals", choices=["all", "known", "none"], default="known")
    sp.add_argument("--poll", action="store_true", help="poll copy targets over HTTP instead of websockets")
    sp.add_argument("--no-firehose", action="store_true", help="only follow copy targets")
    sp.add_argument(
        "--tape",
        action="store_true",
        help="record every trade of every token (needed by `study` and `lab`; several GB/day of SQLite)",
    )

    sp = cmd("serve", "HTTP API for your sniper", json_flag=False)
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)

    sp = cmd("paper", "forward paper-trading: freeze the lab's best strategies, then score them only on newer tape")
    sp.add_argument("action", choices=["freeze", "run", "report"])
    sp.add_argument("--top", type=int, default=4, help="freeze: how many strategies (positive lower bound only)")
    sp.add_argument(
        "--position",
        type=_position,
        default="+6",
        help="freeze: landing position of the lab (+6: where live copies land; scoring is at the live landing)",
    )
    sp.add_argument("--latency", type=int, default=0, help="freeze: slots after the trigger")
    sp.add_argument("--buy", type=float, default=0.2, help="freeze: SOL per buy")
    sp.add_argument("--per-tx", type=float, default=None,
                    help="freeze: SOL per transaction (default: the measured cost, half the low bid's measured round "
                         "trip, the last one measured after a week without enough fills, 0.0045 until one is measured; "
                         "a strategy scored below it never goes live)")  # fmt: skip
    sp.add_argument("--replace", action="store_true", help="freeze: drop the strategies frozen before")
    sp.add_argument("--trigger", action="append", type=_trigger, metavar="JSON",
                    help="freeze: the lab runs only this trigger (repeatable; a JSON object, as for `lab --trigger`), "
                         "scored where it trades live: --latency / --position do not apply to it")  # fmt: skip
    sp.add_argument("--hours", type=_hours, metavar="N", help="freeze: the lab reads only the newest N hours of tape")
    sp.add_argument("--only-live", action="store_true",
                    help="freeze: keep only strategies the lab scored where they trade live")  # fmt: skip

    sp = cmd(
        "vanity",
        "make a wallet whose address starts/ends with chosen letters (key saved to a private file, never printed)",
        json_flag=False,
    )
    sp.add_argument("--starts-with", default="")
    sp.add_argument("--ends-with", default="")
    sp.add_argument("--exact-case", action="store_true", help="match capitals exactly (much slower)")
    sp.add_argument("--workers", type=int, default=1, help="CPU cores to use (default 1)")
    sp.add_argument("--out", help="directory for the key file (default: <data dir>/.wallets)")

    sp = cmd(
        "ops-serve", "on your server: signed remote-operations endpoint (see deploy/bootstrap.sh)", json_flag=False
    )
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8788)
    sp.add_argument("--repo", help="checkout to update (default: this installation)")
    sp.add_argument("--data", help="data directory (default: the database's directory)")
    sp.add_argument("--keys", help="authorized public keys (default: <repo>/deploy/ops_authorized_keys)")
    sp.add_argument("--branch", help="branch `update` pulls (default: the checkout's current branch)")

    sp = cmd(
        "operate",
        "on your server: one operator round (snapshot, gate, Claude only if something changed)",
        json_flag=False,
    )
    sp.add_argument("--analyst", action="store_true", help="force the daily analyst turn now")
    sp.add_argument(
        "--dry-run", action="store_true", help="print the snapshot and the gate's decision; never call Claude"
    )
    sp.add_argument("--telegram-setup", action="store_true", help="store the owner's Telegram chat id and send a hello")
    sp.add_argument(
        "--commands",
        action="store_true",
        help="only poll Telegram and act on /stop /resume /status /book /sell /budget /test /risk /oktech (the one-minute "
        "timer)",
    )
    sp.add_argument("--repo", help="checkout whose venv runs research jobs (default: this installation)")
    sp.add_argument("--branch", help="branch name recorded on jobs (default: the checkout's)")
    sp.add_argument(
        "--expect",
        metavar="snipers=N,held=M",
        help="write operator/expected.json (what 'all quiet' means during a live test) and exit",
    )
    sp.add_argument("action", nargs="?", choices=["budget", "risk", "autotest", "oktech", "idea"],
                    help="budget: show or set what the agents may spend (operator/BUDGET, the owner's; no SSH needed); "
                         "risk: show or set the wallet-wide guard (operator/RISK, the owner's); autotest: show, or turn "
                         "on or off, the owner's standing yes for a live test that starts by itself "
                         "(operator/AUTOTEST); oktech: show or record what OKTech has confirmed (operator/OKTECH, the "
                         "owner's); idea: hand the server's agents a piece of research (operator/ideas.json), which "
                         "they run on the owner's Anthropic API credits")  # fmt: skip
    for flag in ("id", "status", "title", "why", "test", "expected", "kind", "priority"):
        sp.add_argument(f"--{flag}", dest=f"idea_{flag}",
                        help=f"idea: its {flag}")  # fmt: skip
    sp.add_argument("--dev-max-zero", dest="dev_max_zero",
                    help="oktech: 'confirmed' once OKTech has confirmed that Hunt's maxDevBuySol 0 skips every launch "
                         "whose creator bought anything, with the owner's word in chat and --words (until then no hunt "
                         "with a creator limit of 0 starts live); 'unconfirmed' or an empty value takes it back. Any "
                         "other value changes nothing: if OKTech says 0 means no limit, record nothing")  # fmt: skip
    sp.add_argument("--words", dest="oktech_words",
                    help="oktech --dev-max-zero confirmed: when and how OKTech confirmed it, in words")  # fmt: skip
    sp.add_argument(
        "--daily-usd", type=float, help="budget: dollars a day for all the agents (more than 0, at most 500)"
    )
    sp.add_argument("--turn-usd", type=float, help="budget: dollars a turn (more than 0, at most 25, at most the day)")
    sp.add_argument("--tool-calls", type=int, help="budget: tool calls a turn (5 to 80)")
    sp.add_argument("--analyst-every-h", type=float,
                    help="budget: hours between analyses besides the daily one (0: once a day; else 0.5 to 24)")  # fmt: skip
    sp.add_argument("--default", dest="budget_default", action="store_true",
                    help="budget or risk: remove the owner's file; the config's (budget) or the 7 Nov plan's (risk) "
                         "values apply again")  # fmt: skip
    sp.add_argument("--day-stop", dest="day_stop", type=_sol_or_off,
                    help="risk: no new live trade once the day is this many SOL down (0.1 to 5), or off")  # fmt: skip
    sp.add_argument("--floor", dest="risk_floor", type=_sol_or_off,
                    help="risk: no new live trade once the wallet (SOL and coins) is under this many SOL (0 to 1000), "
                         "or off")  # fmt: skip
    sp.add_argument("--on", dest="autotest_on", action="store_true",
                    help="autotest: turn the owner's standing yes on (needs --approved with the owner's words)")  # fmt: skip
    sp.add_argument("--off", dest="autotest_off", action="store_true", help="autotest: turn it off")
    sp.add_argument("--approved", help="autotest --on: the owner's yes, in their words as said in chat")
    sp.add_argument("--wallet", dest="autotest_wallet",
                    help="autotest --on: the sniper wallet the owner's yes names (the config's [operator] "
                         "wallet)")  # fmt: skip
    sp.add_argument("--buy-sol", dest="buy_sol", type=float, help="autotest --on: SOL a buy (at most 0.5; 0.2)")
    sp.add_argument("--fills", type=float, help="autotest --on: buys a block (1 to 10; 10)")
    sp.add_argument("--minutes", type=float, help="autotest --on: minutes a block (1 to 180; 120)")
    sp.add_argument("--loss-sol", dest="loss_sol", type=float,
                    help="autotest --on: a strategy is dropped this far below its first block's start (at most 2; 1)")  # fmt: skip
    sp.add_argument("--continue-at", dest="continue_at", type=float,
                    help="autotest --on: live must be at least this times paper to go on (0.5 to 1; 0.5)")  # fmt: skip

    sp = cmd(
        "solanatracker", "query SolanaTracker directly: leaderboard, first buyers / traders of a coin, a wallet's PnL"
    )
    sp.add_argument("action", choices=["leaderboard", "first-buyers", "traders", "pnl"])
    sp.add_argument("--days", type=int, default=90)
    sp.add_argument("--mint")
    sp.add_argument("--wallet")
    sp.add_argument("--limit", type=int, default=20)

    sp = cmd("discover", "the learning loop's discover stage: every source's wallets into the candidates table")
    sp.add_argument("action", choices=["run"])
    sp.add_argument(
        "--sources",
        default=None,
        help="comma list: tape,owner,solanatracker,solanatracker30,birdeye,coinfirst,vybe (default: all with a key)",
    )
    sp = cmd(
        "qualify",
        "the learning loop's qualify stage: due candidates through the gates at the measured landing (one lab)",
    )
    sp.add_argument("action", choices=["run"])
    sp.add_argument("--max", type=int, default=None, help="due candidates per lab (default [qualify] max_wallets)")
    sp.add_argument("--early", action="store_true",
                    help="run the lab before [qualify] lab_interval_h (the rotation, when the lab has been idle)")  # fmt: skip
    sp = cmd(
        "snapshot-db",
        "write a consistent read-only copy of the database next to it (snipetech.ro.db) for labs and the analyst: "
        "every three hours, by a run with the time for it",
        json_flag=False,
    )
    sp.add_argument("--limit-secs", type=float, default=None,
                    help="the time this run may take (default 1200, the timer's unit); it copies only if the last "
                         "copy's time fits")  # fmt: skip
    sp.add_argument("--force", action="store_true", help="copy now, however young the last copy is")
    sp = cmd("db", "check the live database (quick_check) or rebuild one table's indexes (reindex)", json_flag=False)
    sp.add_argument("action", choices=["check", "reindex", "salvage", "swap", "backfill", "forget", "retain"])
    sp.add_argument("--table", default="trades")
    sp.add_argument("--days", type=float, default=None,
                    help="retain: the days of tape to keep, when more than the configured ones (never more than the "
                         "disk fits; 0: back to the configured days). Without it: only show the state")  # fmt: skip
    sp = cmd("tx", "print one transaction as the RPC returns it (fixture capture for decoder work)", json_flag=False)
    sp.add_argument("signature")
    sp = cmd(
        "events",
        "the learning loop's events: trending/graduating feeds (poll) and curve crossings from the tape (scan)",
    )
    sp.add_argument("action", choices=["poll", "scan", "edge"])
    sp.add_argument(
        "--landing",
        default=None,
        help="edge: how many trades into the block after the event the buy lands (default: the hunt landing, +3)",
    )
    sp.add_argument("--once", action="store_true", help="poll: one pass instead of the service loop")
    sp.add_argument("--interval", type=float, default=60, help="poll: seconds between passes (service: 60)")
    sp.add_argument(
        "--raw", action="store_true", help="poll: print the first rows of each feed as the API sends them, then exit"
    )
    sp.add_argument("--hours", type=float, default=6.0, help="scan: how far back to look for curve crossings")
    sp = cmd(
        "book",
        "the learning loop's book: which qualified wallets to copy, with what fill and bid arm (dry unless [book] enabled)",
    )
    sp.add_argument("action", choices=["run", "on", "off", "status", "bar", "mcap", "fresh"],
                    help="run: one book pass; on/off: the owner's switch for the live book (no SSH needed); status; "
                         "bar [SOL a trade|default]: the score a wallet needs to be copied; mcap [WALLET SOL|off]: copy "
                         "that wallet's buys only below this market cap in SOL (OKTech maxMcapUsd); fresh WALLET: lift its live pause "
                         "and judge it only on trips after now")  # fmt: skip
    sp.add_argument("value", nargs="?", help="bar: SOL a trade (e.g. 0.001) or 'default'; mcap: the wallet")
    sp.add_argument("cap", nargs="?", help="mcap: the cap in SOL, or off")
    sp.add_argument("--live", action="store_true", help="execute the plan (refused unless the book is switched on)")
    sp = cmd(
        "strategist",
        "the learning loop's strategist: at most one bounded change a day to [book] / [qualify], only after 20 new trips",
        json_flag=False,
    )
    sp.add_argument("action", choices=["run"])
    sp.add_argument("--dry-run", action="store_true", help="show the evidence and the proposal; apply nothing")
    sp.add_argument("--force", action="store_true", help="run even if it ran less than a week ago")
    sp = cmd(
        "research",
        "idle-time research: next (when nothing heavy runs, start the research the pipeline lacks most), search (the "
        "strategy lab at the live copy landing; what pays goes into the paper test), digest (the six-hour Telegram "
        "summary), backlog (what is waiting)",
        json_flag=False,
    )
    sp.add_argument("action", choices=["next", "search", "digest", "backlog"])
    sp.add_argument("--dry-run", action="store_true", help="next: say what it would start; digest: print, send nothing")
    sp = cmd(
        "winners",
        "how the winners win: every wallet's closed round trips in coins launched in the window, ranked by realized "
        "SOL, with its style (slots after the create, hold, pieces sold); reads the tape copy (a research job)",
    )
    sp.add_argument("--hours", type=float, default=72.0)
    sp.add_argument("--min-trips", type=int, default=30, help="closed round trips a wallet needs to be ranked")
    sp.add_argument("--top", type=int, default=200)
    sp.add_argument("--label", type=int, default=0, help="send the N best mid/late traders (no create-slot sniping, "
                    "no insider sales) to qualify's queue as cand")  # fmt: skip
    sp = cmd(
        "playbook",
        "a bot's rules read off the tape: what each first buy came right after, who it follows and how often, its "
        "filters, sizes, bids and exits (reads the tape copy; a research job)",
    )
    sp.add_argument("wallet")
    sp.add_argument("--hours", type=float, default=72.0)
    sp.add_argument("--top", type=int, default=60, help="followed wallets to list")
    sp = cmd(
        "copy-context",
        "what a known wallet's buy is worth by its context: entered a block late and marked at each horizon, split by "
        "how many known wallets were in, the coin's volume, age, hour, the leader's size and the buys later in its own "
        "block (reads the tape copy; a research job)",
    )
    sp.add_argument("--hours", type=float, default=72.0)
    sp.add_argument("--entry-slots", type=int, default=1, help="slots after the leader's slot where we enter")
    sp.add_argument(
        "--behind-leader",
        action="store_true",
        help="enter right behind the leader's buy in its own block (where most live copies land)",
    )
    sp.add_argument("--horizons", default="15,75,150,450", help="slots after the leader's slot where we mark")
    sp.add_argument("--min-sol", type=float, default=0.5, help="the leader's smallest buy that counts")
    sp.add_argument("--groups", default="kol,wallet", help="label kinds whose wallets are leaders")
    sp.add_argument("--any-buyer", action="store_true", help="every wallet's buy of --min-sol is an event")
    sp.add_argument("--by-entity", action="store_true", help="wallets whose tracker names share a team count as one "
                    "known wallet, and the copy lands behind the team's back-to-back buys")  # fmt: skip
    sp.add_argument("--taken-by", default="", help="bots (comma-separated): each row gets the share of its buys one "
                    "of them followed in the same block")  # fmt: skip
    sp = cmd(
        "history",
        "bought history (pumpfundata.com hourly files, one credit each): range | fetch | check against our own tape | "
        "scan the files on disk | table from a kept scan | follow: copy every wallet's first buys; "
        "the key is PUMPFUNDATA_API_KEY in the server's env file",
    )
    sp.add_argument("action", choices=["range", "fetch", "check", "order", "scan", "table", "follow"])
    sp.add_argument("--exchange", choices=["pump_fun", "pump_amm"], default="pump_fun")
    sp.add_argument("--min-block-sol", type=float, default=15.0, help="scan: an organised launch is one where others "
                    "put this much SOL in the create block")  # fmt: skip
    sp.add_argument("--from", dest="from_day", help="fetch: first day, YYYY-MM-DD")
    sp.add_argument("--to", dest="to_day", help="fetch: last day, YYYY-MM-DD (default: the first day)")
    sp.add_argument("--max-files", type=int, default=24, help="fetch: credits this run may spend (one a file)")
    sp.add_argument("--x-max", type=int, default=0, help="scan/table: look up at most this many X accounts the organised "
                    "launches link to (twitterapi.io, paid; kept, so never twice); 0 = none")  # fmt: skip
    sp.add_argument(
        "--min-sol", type=float, default=0.5, help="follow: the smallest first buy that is scored as a copy"
    )
    sp.add_argument(
        "--test-weeks", type=int, default=1, help="follow: the last weeks, on which picked wallets are judged"
    )
    sp.add_argument("--min-pick", type=int, default=30, help="follow: first buys a wallet needs in the picking weeks")
    sp.add_argument("--top", type=int, default=50, help="follow: picked wallets listed one by one")
    sp.add_argument("--max-create-share", type=float, default=1.0, help="follow: leave out wallets with a larger share "
                    "of their buys in the create slot (a copy cannot land there)")  # fmt: skip
    sp.add_argument("--rescan", action="store_true", help="follow: read the files again even if a run was kept")
    sp.add_argument("--walk", action="store_true", help="follow: also pick and judge week by week at rising bars")
    sp.add_argument(
        "--cross",
        help="scan/table: splits to combine, comma-separated (website, x_link, telegram, "
        "block_wallets, block_sol, dev_sol, team, hunt, x_badge, x_followers, x_age); each cell is shown per half of the days",
    )
    sp.add_argument("--min-cell", type=int, default=50, help="scan/table: launches a combined cell needs to be shown")
    sp.add_argument("--wallets", help="scan/table: comma-separated wallets; the split `wallets` is launches with one "
                    "of them in the create block (a hand-picked Hunt list)")  # fmt: skip
    sp.add_argument("--team-clip", type=float, help="scan/table: cap each earlier return of a team at plus/minus this "
                    "many percent before it is averaged")  # fmt: skip
    sp.add_argument("--team-shrink", type=float, default=0.0, help="scan/table: pull a team's record toward the average "
                    "of all launches, as if it had this many more launches at the average")  # fmt: skip
    sp.add_argument("--show-from", help="scan/table: rows hold only launches of this day (YYYY-MM-DD) and later; team "
                    "records still use every launch")  # fmt: skip
    sp.add_argument("--team-min-prior", type=int, default=5, help="scan/table: earlier launches a bundle wallet needs "
                    "for a launch to get its team's record (the split `team`)")  # fmt: skip
    sp.add_argument("--exits", action="store_true", help="scan/table: score the exit rules (time, stop, take, trail) on "
                    "every launch's kept price path, overall and per combined cell")  # fmt: skip
    sp.add_argument("--date", help="check: the day, YYYY-MM-DD")
    sp.add_argument("--hour", type=int, help="check: the hour, 0-23 UTC")
    sp = cmd(
        "launch-tools",
        "deploy tools by the mark they leave in a create transaction (a fee wallet, a wrapper program, a signer that is "
        "not the creator) and how far each mark's coins run (one getTransaction per launch; a research job)",
    )
    sp.add_argument("--hours", type=float, default=12.0)
    sp.add_argument("--max", type=int, default=20_000, help="the newest N launches of the window")
    sp.add_argument("--min-launches", type=int, default=5, help="a mark seen fewer times is not a tool")
    sp.add_argument("--mark", help="also split this mark's launches by who bought in the create's block, and list them")
    sp.add_argument("--block-sol-min", type=float, default=0.0, help="with --mark: only launches where others put this "
                    "much SOL in the create block")  # fmt: skip
    sp.add_argument("--funding-of", help="also read, once, how the creator of each launch with this mark was first "
                    "funded (mark fund:cu<limit>: a tool's relay)")  # fmt: skip
    sp.add_argument("--mint", action="append", default=[], help="instead of the scan: the marks of this coin's create "
                    "(repeat for several; the marks they share are the tool's)")  # fmt: skip
    sp = cmd(
        "leaders-of",
        "the wallets a copy bot buys right after: who bought the same coin up to N slots before its first buy, over "
        "its coins, with how far behind it landed (reads the tape copy; a research job)",
    )
    sp.add_argument("wallet")
    sp.add_argument("--hours", type=float, default=72.0)
    sp.add_argument("--max-slots", type=int, default=5)
    sp.add_argument("--after-slots", type=int, default=0, help="only its first buys this many slots after the create")
    sp = cmd(
        "early-leaders",
        "wallets like 7aPo: their buys in a coin's first slots, copied 3 trades behind, ranked by "
        "what a 0.2 SOL copy made over 30 s (reads the tape copy; a research job the agents may start)",
    )
    sp.add_argument("--hours", type=float, default=72.0)
    sp.add_argument("--min-n", type=int, default=20, help="early buys a wallet needs to be ranked")
    sp.add_argument(
        "--max-slots", type=int, default=20, help="an early buy comes at most this many slots after the create"
    )
    sp.add_argument("--min-sol", type=float, default=0.3, help="the smallest leader buy counted")
    sp.add_argument(
        "--label", type=int, default=0, help="label the N best with a positive lower bound as cand for qualify"
    )
    sp = cmd(
        "dip-buys",
        "buy the dip of a hot coin (owner, 10 Nov): coins that reached 40+ SOL in the curve with 100+ wallets and fell "
        "60 percent from their high within 10 minutes, bought 3 trades later and sold on timers in the lab's simulator "
        "(reads the tape copy; a research job the agents may start)",
    )
    sp.add_argument("--hours", type=float, default=72.0)
    sp.add_argument("--min-peak-sol", type=float, default=40.0, help="SOL in the curve at the coin's high")
    sp.add_argument("--min-buyers", type=int, default=100, help="wallets that had bought by the high")
    sp.add_argument("--fall", type=float, default=0.6, help="how far under its high the price must be (0.6: 60 %%)")
    sp.add_argument("--within", type=float, default=600.0, help="seconds from the high to that price, at most")
    sp.add_argument("--behind", type=int, default=3, help="trades that land between the fall's trade and our buy")
    sp.add_argument("--rebound", type=float, default=0.0,
                    help="wait for the bounce to start: buy only once the price is this far above its lowest since "
                         "the fall (0.1: 10 %%); 0: buy the fall itself")  # fmt: skip
    sp.add_argument("--rebound-within", type=float, default=300.0, help="seconds after the fall the turn may take")
    sp.add_argument("--buy", type=float, default=None, help="SOL a buy (default: the book's size)")
    sp.add_argument("--min-n", type=int, default=30, help="events a split needs to be shown")
    sp = cmd(
        "exitrun",
        "our own exits: run (shadow: watch the coins our snipers bought over their own LaserStream "
        "subscription and record when and at what price our rules would sell, sending nothing) or report (each "
        "decision against OKTech's real sale of the coin)",
    )
    sp.add_argument("action", choices=["run", "report"])
    sp.add_argument("--minutes", type=float, default=60.0, help="run: how long to watch")
    sp = cmd(
        "stopped-wallets",
        "what the wallets one rule stops have made since: every wallet whose paper numbers pass and that only the "
        "crowding rule or the history rule stops, measured in one qualify lab on its copies after that verdict, next "
        "to the wallets that were picked (takes a lab slot, writes no verdict; a research job the agents may start)",
    )
    sp.add_argument("--max", type=int, default=150, help="wallets the lab measures (the picked ones are cut first)")
    sp = cmd(
        "fomo-wallets",
        "FOMO app traders on our tape (FOMO pays their gas from one wallet), busiest first; --label N "
        "labels the N busiest cand so qualify scores them at the live copy landing (a research job the agents may start)",
    )
    sp.add_argument("--days", type=float, default=3.0)
    sp.add_argument("--min-buys", type=int, default=10)
    sp.add_argument("--label", type=int, default=0)
    sp = cmd("measure", "the learning loop's measure stage: our live fills audited from the chain and the tape")
    sp.add_argument("action", choices=["run", "backfill", "report"])
    sp.add_argument(
        "--since", type=int, default=None, help="unix seconds; run: override the cursor; backfill: required"
    )
    sp.add_argument("--until", type=int, default=None, help="unix seconds (backfill)")
    sp = cmd("vybe", "query Vybe directly: the free 1d/7d/30d top-traders board")
    sp.add_argument("--resolution", default="30d", choices=["1d", "7d", "30d"])
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument(
        "--sort",
        default="realizedPnlUsd",
        choices=["realizedPnlUsd", "unrealizedPnlUsd", "winRate", "tradesVolumeUsd", "tradesCount"],
    )
    sp.add_argument("--raw", action="store_true", help="print Vybe's rows unparsed (field names)")
    sp = cmd("birdeye", "query Birdeye directly: credits, gainers, top traders of a coin, a wallet's 30-day PnL")
    sp.add_argument("action", choices=["credits", "gainers", "top", "pnl"])
    sp.add_argument("--period", default="1W", help="gainers: yesterday|today|1W|30d|90d")
    sp.add_argument("--sort", default="PnL", help="gainers: PnL|realized_pnl|unrealized_pnl|trader_score")
    sp.add_argument("--mint", help="top: the coin")
    sp.add_argument("--wallet", help="pnl: the wallet")
    sp.add_argument("--frame", default="7d", help="top: time frame")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--raw", action="store_true", help="print Birdeye's response as it came")

    sp = cmd("remote", "operate snipetech on your server through its ops endpoint", json_flag=False)
    sp.add_argument("--url", default=os.environ.get("SNIPETECH_OPS_URL"), help="ops URL ($SNIPETECH_OPS_URL)")
    sp.add_argument("--key", default=os.environ.get("SNIPETECH_OPS_KEY"), help="private key file ($SNIPETECH_OPS_KEY)")
    sp.add_argument("--wait", action="store_true", help="run/job/update: wait for the job and print its output")
    sp.add_argument("action", choices=["keygen", "pubkey", "status", "run", "job", "jobs", "kill", "ls", "read", "put", "sql",
                                       "logs", "update", "daily"])  # fmt: skip
    sp.add_argument("rest", nargs=argparse.REMAINDER, help="run: a snipetech command; job/kill: id; read/ls: path; "
                    "put: local-file name; sql: query; logs: unit")  # fmt: skip

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
        out["rpc"] = {
            "ok": True,
            "detail": f"slot {slot} in {(time.perf_counter() - t0) * 1000:.0f} ms via {svc.status()['rpc']}",
        }
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
            out["websocket"] = {
                "ok": "result" in ack,
                "detail": "subscriptions work (needed for `watch`)" if "result" in ack else str(ack),
            }
    except Exception as exc:
        out["websocket"] = {
            "ok": False,
            "detail": f"{exc} — `watch` needs a websocket-capable RPC; `watch --poll --no-firehose` works without",
        }
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    out["agent"] = {
        "ok": has_key,
        "detail": "ANTHROPIC_API_KEY found" if has_key else "set ANTHROPIC_API_KEY to use `snipetech agent`",
    }
    if os.environ.get("BIRDEYE_API_KEY"):
        try:
            from .birdeye import Birdeye

            be = Birdeye()
            try:
                cr = await asyncio.to_thread(be.credits)
            finally:
                be.close()
            out["birdeye"] = {"ok": True, "detail": f"{cr['remaining']:,} CU left this cycle ({cr['used']:,} used)"}
        except Exception as exc:  # noqa: BLE001
            out["birdeye"] = {"ok": False, "detail": f"BIRDEYE_API_KEY set but the API answered: {exc}"}
    else:
        out["birdeye"] = {"ok": False, "detail": "BIRDEYE_API_KEY not set (optional: market-wide wallet discovery)"}
    if os.environ.get("SOLANATRACKER_API_KEY"):
        try:
            from .solanatracker import SolanaTracker

            stc = SolanaTracker()
            try:
                rows, _ = await asyncio.to_thread(stc.leaderboard, 30, "realized", 1)
            finally:
                stc.close()
            out["solanatracker"] = {"ok": True, "detail": f"leaderboard answers ({len(rows)} row)"}
        except Exception as exc:  # noqa: BLE001
            out["solanatracker"] = {"ok": False, "detail": f"SOLANATRACKER_API_KEY set but the API answered: {exc}"}
    else:
        out["solanatracker"] = {
            "ok": False,
            "detail": "SOLANATRACKER_API_KEY not set (optional: 90-day wallet leaderboard)",
        }
    if os.environ.get("VYBE_API_KEY"):
        try:
            from .vybe import Vybe

            vy = Vybe(counter=_usage(svc, "vybe"))
            try:
                rows = await asyncio.to_thread(vy.top_traders, "30d", 1)
            finally:
                vy.close()
            out["vybe"] = {"ok": True, "detail": f"top-traders answers ({len(rows)} row)"}
        except Exception as exc:  # noqa: BLE001
            out["vybe"] = {"ok": False, "detail": f"VYBE_API_KEY set but the API answered: {exc}"}
    else:
        out["vybe"] = {"ok": False, "detail": "VYBE_API_KEY not set (optional: free 30-day leaderboard)"}
    out["config"] = {
        "ok": bool(cfg.path and cfg.path.exists()),
        "detail": str(cfg.path)
        if cfg.path and cfg.path.exists()
        else "no snipetech.toml (defaults in use) — run `snipetech init`",
    }
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
        elif c == "doctor" and args.stream_replay:
            from .solana import laserstream as LS
            from .solana.programs import LAUNCHLAB_PROGRAM, PUMP_PROGRAM

            head = await svc.rpc.get_slot()
            out = await LS.probe_replay([PUMP_PROGRAM, LAUNCHLAB_PROGRAM], head, args.stream_replay, args.secs,
                                        endpoint=cfg.live.laserstream_endpoint, commitment=cfg.rpc.commitment)  # fmt: skip
            print(json.dumps(out))
        elif c == "doctor":
            _out(
                args,
                await doctor(svc),
                lambda d: "\n".join(f"{'OK ' if v.get('ok') else 'NO '} {k}: {v.get('detail')}" for k, v in d.items()),
            )
        elif c == "index-launches":
            since = int(time.time() - args.hours * 3600)
            res = await svc.ingestor.backfill_launches(since, args.max)
            rows = svc.store.deployer_counts(2, since)
            data = {"launches_indexed": res.launches, "transactions": res.fetched, "repeat_deployers": len(rows),
                    "top": [{"deployer": d, "launches": n, "first_ts": a, "last_ts": b} for d, n, a, b in rows[:25]]}  # fmt: skip
            _out(
                args,
                data,
                lambda d: (
                    f"Indexed {d['launches_indexed']} launches; {d['repeat_deployers']} wallets launched 2+ times.\n"
                    + R.table(
                        [[x["deployer"], x["launches"], R.ago(x["last_ts"])] for x in d["top"]],
                        ["deployer", "launches", "last"],
                    )
                    + "\nNext: `snipetech top-deployers --backfill 20` or `snipetech deployer <address>`"
                ),
            )
        elif c == "deployer":
            rep = await svc.analyze_deployer(
                args.address, refresh=not args.no_refresh, max_launches=args.launches, family=args.family
            )
            _out(args, rep, R.render_deployer)
        elif c == "mint":
            _out(args, await svc.check_mint(args.mint, refresh=args.analyze))
        elif c == "copy":
            rep = await svc.analyze_copy(args.wallet, args.days, refresh=not args.no_refresh)
            _out(args, rep, R.render_copy)
        elif c == "copy-rank":
            wallets = list(args.wallets)
            if args.file:
                wallets += [
                    w.strip() for w in Path(args.file).read_text().splitlines() if w.strip() and not w.startswith("#")
                ]
            if args.source == "birdeye":
                from .birdeye import Birdeye

                be = Birdeye(budget_cu=cfg.birdeye.daily_cu, counter=_usage(svc, "birdeye"))
                try:
                    rows = []
                    for w in dict.fromkeys(wallets):
                        row = await asyncio.to_thread(be.wallet_pnl, w, "30d")
                        svc.store.put_score("birdeye", w, {"wallet": w, "hist30": row, "scanned_at": int(time.time())})
                        rows.append(row)
                finally:
                    be.close()
                _out(args, rows, R.render_birdeye_pnl)
            else:
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
            _out(
                args,
                await svc.profile_sniper(args.wallet, args.days, refresh=not args.no_refresh, deep=args.deep),
                R.render_sniper,
            )
        elif c == "snipers":
            _out(
                args, await asyncio.to_thread(svc.top_snipers, args.days, args.min_snipes, args.limit), R.render_snipers
            )
        elif c == "rescore":
            print(json.dumps(await svc.rescore_deployers(args.limit_secs, args.hours * 3600)))
        elif c == "profitable-devs":
            _out(
                args,
                await svc.profitable_devs(args.min_launches, args.days, args.limit, args.backfill, args.quick),
                R.render_profitable_devs,
            )
        elif c == "fees":
            rep = await svc.fee_market(args.deployer, args.days, refresh=not args.no_refresh)
            _out(
                args,
                rep,
                lambda d: (
                    f"Fee market — {d['scope']}\n"
                    + "=" * 72
                    + "\n"
                    + "\n".join(R.render_competition(d["competition"], d.get("tip_sweep")))
                ),
            )
        elif c == "label":
            if args.file:
                _out(args, svc.label_file(args.file, args.kind))
            elif args.address and args.name:
                _out(args, svc.label(args.address, args.name, args.kind))
            else:
                raise SystemExit("label: give ADDRESS NAME, or --file")
        elif c == "lab":
            from collections import Counter

            from .analysis import paper
            from .analysis.lab import collect as lab_collect
            from .analysis.lab import label_groups as lab_groups
            from .analysis.lab import rank as lab_rank
            from .analysis.lab import rank_wallets as lab_rank_wallets
            from .analysis.lab_lock import lab_lock
            from .operator.research import recent_tape
            from .qualify.run import lab_tx_cost_info

            copy_pos = paper.copy_landing(svc.store, cfg, time.time())  # where our copies land live (as measured)
            # an agent's hypotheses are scored where each trades live (`paper.live_landing`), so they need no landing
            # of their own: their copies at the copy landing; the grid's copies at --latency / --position
            lat, pos = (paper.hypothesis_landing(copy_pos, args.copy_position) if args.trigger
                        else (args.latency, args.position))  # fmt: skip
            # what a transaction costs live, as `paper freeze`, the qualify lab and the strategy search charge it, unless
            # told: every lab of research round 1 (7 Nov) charged the flat 0.0035 against about 0.0045 live, so every
            # mean was 0.002 SOL a trade too kind
            cost = (
                {"per_tx_sol": args.per_tx, "per_tx_source": "flag", "per_tx_measured_at": None}
                if args.per_tx is not None
                else lab_tx_cost_info(svc.store, cfg)
            )
            per_tx = cost["per_tx_sol"]

            def _collect_locked():  # one lab at a time on the box: queue behind whoever holds the lock
                with lab_lock(Path(cfg.storage.db_path).resolve().parent, wait=True):
                    tape = svc.tape_store if args.hours is None else recent_tape(svc.tape_store, args.hours)
                    # launch-window rules (launch, hunt, whale, burst) run live through OKTech hunt: scored where its
                    # buy lands, wherever --position puts the others, as `paper freeze` and the strategy search do
                    # the trades from the hourly copy, the wallet groups from the live database: the copy has none of
                    # the labels written since it was taken (11 Nov: a rule on wallets labelled minutes before fired on
                    # nothing and said so nowhere)
                    return lab_collect(tape, cfg, args.buy, lat, pos, per_tx, landings=paper.LAUNCH_LANDINGS,
                                       triggers=args.trigger, kols=lab_groups(svc.store))  # fmt: skip

            data = await asyncio.to_thread(_collect_locked)
            top = max(args.top, len(args.trigger)) if args.trigger else args.top  # --top never hides a hypothesis
            ranked = [r | paper.landing_fields(r, copy_pos) for r in lab_rank(data, cfg, top)]
            res = {"tape_hours": data["tape_hours"], "moments": len(data["rows"]), "latency_slots": lat, "position": pos,
                   "copy_landing": copy_pos, "buy_sol": args.buy, **cost, "hours": args.hours,
                   "workers": data.get("workers"), "strategies": ranked}  # fmt: skip
            if data.get("unscored"):  # e.g. MIG with an exit longer than 300 s
                res["unscored"] = data["unscored"]
            if args.trigger:  # each hypothesis, ranked or not: where it was scored and how often it fired
                fired = Counter(r["trigger"] for r in data["rows"])
                res["triggers"] = [{"trigger": t.label(), "moments": fired[ti],
                                    **paper.landing_fields({"landing": data["landing_of"][ti], "trigger_spec": t.__dict__},
                                                           copy_pos),
                                    "ranked": any(r["trigger_spec"] == t.__dict__ for r in ranked)}
                                   for ti, t in enumerate(data["triggers"])]  # fmt: skip
            if data.get("amm_tape"):  # MIG: what the PumpSwap tape let it score, and why the rest was skipped
                res["amm_tape"] = data["amm_tape"]
            if args.by_wallet:
                res["wallets"] = lab_rank_wallets(data, cfg, svc.store.labels(), top=max(args.top, 40))
            _out(args, res, R.render_lab)
        elif c == "sweep":
            from .solana.sell import sweep as _sweep

            try:  # the tape: a coin that left its curve is sold on the PumpSwap pool its migration record names
                _out(
                    args, await _sweep(svc.rpc, args.mint, args.slippage, args.dry_run, store=svc.store), R.render_sweep
                )
            except RuntimeError as exc:
                raise SystemExit(f"sweep: {exc}") from None
        elif c == "follow":
            ws = list(args.wallets)
            if args.file:
                ws += [
                    line.split()[0]
                    for line in Path(args.file).read_text().splitlines()
                    if line.strip() and not line.startswith("#")
                ]
            if args.kols:
                ws += list(svc.store.labels("kol"))
            if not ws:
                raise SystemExit("follow: give wallets, --file or --kols")
            _out(
                args, await svc.follow(list(dict.fromkeys(ws)), args.days, refresh=not args.no_refresh), R.render_follow
            )
        elif c == "paper":
            from .analysis import paper

            if args.action == "freeze":
                from .qualify.run import lab_tx_cost_info

                # the measured cost of a transaction unless told: live_test.eligible refuses a strategy scored below it;
                # each strategy frozen keeps where its cost came from
                cost = (
                    {"per_tx_sol": args.per_tx, "per_tx_source": "flag"}
                    if args.per_tx is not None
                    else lab_tx_cost_info(svc.store, cfg)
                )
                rep = await asyncio.to_thread(paper.freeze, svc.store, cfg, args.top, args.position, args.latency, args.buy,
                                              cost["per_tx_sol"], args.replace, only_live=args.only_live,
                                              triggers=args.trigger, hours=args.hours,
                                              lock_dir=Path(cfg.storage.db_path).resolve().parent,
                                              per_tx_source=cost["per_tx_source"],
                                              tape=lambda: svc.tape_store)  # fmt: skip
            elif args.action == "run":
                rep = await asyncio.to_thread(paper.run, svc.store, cfg)
            else:
                rep = await asyncio.to_thread(paper.report, svc.store, cfg)
            _out(args, rep, lambda r: R.render_paper(args.action, r))
        elif c == "oktech":
            if args.wallet:
                cfg.oktech.wallet = args.wallet
            kinds = (args.only,) if args.only else ("copy", "dev")
            if args.action == "status":
                _out(args, await svc.oktech_status(), R.render_oktech_status)
                return 0
            if args.action == "probe":
                rep = await svc.oktech_probe(args.buy or 0.01, args.snipes, args.copy_wallet,
                                             args.wallet or cfg.oktech.wallet or "SNIPER", create=args.create)  # fmt: skip
                if args.json:
                    print(json.dumps(rep, indent=1, default=str))
                    return 0
                print(
                    f"OKTech speed test on wallet {rep['wallet']} (at most {rep['max_spend_sol']} SOL in buys + fees)"
                )
                for sn in rep["snipers"]:
                    print(f"\n  {sn['name']}" + (f"  [{sn.get('id')}, {sn.get('status')}]" if sn.get("id") else ""))
                    for line in sn["summary"]:
                        print(f"    {line}")
                if not args.create:
                    print("\nNot created. Add --create to make them on OKTech, idle.")
                else:
                    print(
                        "\nOKTech refuses unknown or out-of-range fields, so these settings were accepted as sent; it never"
                        "\nreturns settings to a key (check them on the Sniper page). Start them on a leash with `oktech guard`."
                    )
                return 0
            if args.action == "copyaudit":
                if not (args.copy and args.address):
                    raise SystemExit(
                        "copyaudit: give --address <our wallet> and --copy <leader> (one or more; label/min ignored)"
                    )
                leaders = [s.split(":")[0] for s in args.copy]
                rows = svc.oktech_copy_audit(args.address, int(time.time() - args.since_minutes * 60), leaders)
                if args.json:
                    print(json.dumps(rows, indent=1, default=str))
                    return 0
                if not rows:
                    print("no buys by that wallet in the window")
                    return 0
                print(
                    f"{'mint':10} {'time':8} {'my SOL':>7} {'leader':8} {'lead SOL':>8} {'dslot':>5} {'between':>7} {'gap%':>6}"
                )
                for r in rows:
                    tm = time.strftime("%H:%M:%S", time.gmtime(r["ts"]))
                    if r.get("leader"):
                        print(f"{r['mint'][:8]:10} {tm:8} {r['my_sol']:7.3f} {r['leader'][:8]:8} {r['leader_sol']:8.2f} {r['dslot']:5d} "
                              f"{r['between_buys']:7d} {r['price_gap_pct']:6.1f}")  # fmt: skip
                    else:
                        print(
                            f"{r['mint'][:8]:10} {tm:8} {r['my_sol']:7.3f} {'-':8} {'':>8} {'':>5} {'':>7}  (no copied buy ahead)"
                        )
                return 0
            if args.action == "copylist":
                if not args.copy:
                    raise SystemExit("copylist: give --copy ADDRESS[:LABEL[:MIN_SOL]] (one or more)")
                rep = await svc.oktech_copylist(args.copy, args.wallet or cfg.oktech.wallet or "SNIPER", create=args.create,
                                                buy_sol=args.buy or 0.05, max_buys=args.snipes, exit_name=args.exit,
                                                priority_fee=args.priority, tip=args.tip, send_count=args.send_count, tag=args.tag,
                                                compute_units=args.compute_units, allow_overlap=args.allow_overlap)  # fmt: skip
                if args.json:
                    print(json.dumps(rep, indent=1, default=str))
                    return 0
                for sn in rep["snipers"]:
                    print(f"\n{sn['label']}  {sn['id']}")
                    print(
                        f"  OKTech sniper: {sn['name']}"
                        + (f"  [{sn['oktech_id']}, {sn['status']}]" if sn.get("oktech_id") else "")
                    )
                    for line in sn["summary"]:
                        print(f"    {line}")
                    for n in sn["notes"]:
                        print(f"    note: {n}")
                if not args.create:
                    print(
                        "\nDry run. --create makes them on OKTech, idle; start them with `oktech cycle` or `oktech guard`."
                    )
                return 0
            if args.action == "strategy":
                rep = await svc.oktech_strategy(args.id[0] if args.id else None, args.wallet or cfg.oktech.wallet or "SNIPER",
                                                create=args.create, buy_sol=args.buy, max_buys=args.snipes, via=args.via, preexec=not args.no_preexec, stale=args.stale or None)  # fmt: skip
                if args.json:
                    print(json.dumps(rep, indent=1, default=str))
                    return 0
                for sn in rep["snipers"]:
                    print(f"\n{sn['id']}  {sn['label']}")
                    if sn.get("error"):
                        print(f"  skipped: {sn['error']}")
                        continue
                    print(
                        f"  OKTech sniper: {sn['name']}"
                        + (f"  [{sn['oktech_id']}, {sn['status']}]" if sn.get("oktech_id") else "")
                    )
                    for line in sn["summary"]:
                        print(f"    {line}")
                    for n in sn["notes"]:
                        print(f"    note: {n}")
                if not args.create:
                    print(
                        "\nDry run (--json shows the payloads). --create makes them on OKTech, idle; start them with `oktech guard`."
                    )
                return 0
            if args.action == "holdings":
                if not args.address:
                    raise SystemExit("holdings: give --address")
                unknown: list[dict] = []
                held = await svc.open_positions(args.address, unknown=unknown)
                if args.json:
                    print(json.dumps({"positions": held, "unpriced": unknown}, indent=1))
                else:
                    print(
                        "\n".join(f"{h['mint']}  {h['amount']:,.4f}  ({h['value_sol']:.4f} SOL)" for h in held)
                        or "no coins held"
                    )
                    if unknown:
                        print(
                            "not on the tape (ignored): "
                            + ", ".join(f"{h['mint']} ({h['amount']:,.4f})" for h in unknown)
                        )
                return 0
            if args.action == "cycle":
                if not (args.id and args.address and args.floor is not None):
                    raise SystemExit("cycle: give --id (one or more), --address and --floor")
                rep = await svc.oktech_cycle(
                    args.id,
                    args.address,
                    args.floor,
                    args.max_minutes,
                    args.max_fills,
                    args.rearm_after,
                    args.ignore_mint,
                )
                print(json.dumps(rep, indent=1, default=str) if args.json else "\n".join(rep["events"]))
                return 0
            if args.action == "guard":
                if not (args.id and args.address and args.floor is not None):
                    raise SystemExit("guard: give --id (one or more), --address and --floor")
                rep = await svc.oktech_guard(
                    args.id,
                    args.address,
                    args.floor,
                    args.max_minutes,
                    start=not args.watch_only,
                    allow_uncapped=args.allow_uncapped,
                )
                print(json.dumps(rep, indent=1, default=str) if args.json else "\n".join(rep["events"]))
                return 0
            if args.action == "docs":
                client = svc._oktech()
                try:
                    print(json.dumps(await client.docs(), indent=1, default=str))
                finally:
                    await client.close()
                return 0
            if args.action in ("show", "start", "stop", "delete"):
                if not args.id:
                    raise SystemExit(f"{args.action}: give --id (see `snipetech oktech status`)")
                from .service import oktech_tasks

                results, failures = await oktech_tasks(svc, args.action, args.id, force=getattr(args, "force", False))
                for r in results:
                    print(json.dumps(r, indent=1, default=str))
                for i, err in failures:
                    print(f"{args.action} {i} failed: {err}", file=sys.stderr)
                return 1 if failures else 0
            if args.action == "replica":
                if not args.from_study:
                    raise SystemExit("replica: give --from-study WALLET (run `snipetech study WALLET` first)")
                rep = await svc.oktech_replica(
                    args.from_study, args.buy or 0.05, create=args.create, max_buys=args.snipes
                )
                print(json.dumps(rep, indent=1, default=str) if args.json else R.render_oktech_replica(rep))
                return 0
            plan = await svc.oktech_plan(kinds)
            print(
                R.render_oktech_plan(plan, args.prune)
                if not args.json
                else json.dumps({k: v for k, v in plan.items() if k != "_sync"}, indent=1, default=str)
            )
            if args.action == "push":
                todo = plan["create"] or plan["replace"] or (args.prune and plan["remove"])
                if not todo:
                    print("Nothing to change.")
                    return 0
                if not args.yes and input("Apply these changes to your OKTech account? [y/N] ").strip().lower() != "y":
                    print("Nothing changed.")
                    return 0
                for r in await svc.oktech_push(plan, args.prune, allow_overlap=args.allow_overlap):
                    print(f"  {r['action']:22} {r['name']}  {r.get('id', '')}  {r.get('error', '')}")
        elif c == "study":
            rep = await svc.study(
                args.wallet,
                args.days,
                args.negatives,
                refresh=not args.no_refresh,
                auto_smart=args.auto_smart,
                backfill_entries=args.backfill_entries,
                entry_window=args.entry_window,
            )
            _out(args, rep, R.render_study)
        elif c == "discover-wallets":
            if args.source == "birdeye":
                from .birdeye import Birdeye

                be = Birdeye(budget_cu=cfg.birdeye.daily_cu, counter=_usage(svc, "birdeye"))
                try:
                    rows = await svc.discover_birdeye(be, args.days, args.top_coins, args.pnl_top)
                finally:
                    be.close()
                _out(args, rows[: args.limit] if args.limit else rows, R.render_birdeye_candidates)
            elif args.source == "solanatracker":
                from .solanatracker import SolanaTracker

                stc = SolanaTracker(
                    budget_requests=cfg.solanatracker.daily_requests, counter=_usage(svc, "solanatracker")
                )
                try:
                    rows = await svc.discover_solanatracker(stc, int(args.days) if args.days else None, args.pages)
                finally:
                    stc.close()
                _out(args, rows[: args.limit] if args.limit else rows, R.render_solanatracker_candidates)
            else:
                rows = await svc.discover_real_traders(args.days, args.min_positions, args.limit)
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
                await LiveWatcher(
                    svc, signals=args.signals, poll=args.poll, firehose=not args.no_firehose, tape=args.tape
                ).run()
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


def _ops_serve(args, cfg: Config) -> int:
    import subprocess

    from .ops import Ops, make_server

    repo = Path(args.repo or Path(__file__).resolve().parents[2])
    db = Path(cfg.storage.db_path).resolve()
    branch = args.branch or subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                                           capture_output=True, text=True).stdout.strip() or "main"  # fmt: skip
    ops = Ops(repo=repo, data=Path(args.data or db.parent), db=db,
              keys_file=Path(args.keys or repo / "deploy" / "ops_authorized_keys"), branch=branch)  # fmt: skip
    server = make_server(ops, args.host, args.port)
    print(f"ops endpoint on {args.host}:{args.port} (repo {repo}, branch {branch}, data {ops.data})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _remote(args) -> int:
    from .ops import OpsError, call, keygen, public_line, wait_job

    rest = list(args.rest)
    if rest[:1] == ["--"]:
        rest = rest[1:]
    if args.action in ("keygen", "pubkey"):
        path = rest[0] if rest else args.key
        if not path:
            print("give a key file path (or set SNIPETECH_OPS_KEY)", file=sys.stderr)
            return 2
        print(keygen(path) if args.action == "keygen" else public_line(path))
        return 0
    if not args.url or not args.key:
        print("set SNIPETECH_OPS_URL and SNIPETECH_OPS_KEY (or --url / --key)", file=sys.stderr)
        return 2

    def go(op: str, **kw):
        return call(args.url, args.key, op, **kw)

    def show_job(st: dict) -> None:
        out = st.pop("output", "")
        print(json.dumps(st, default=str))
        if out:
            print(out, end="" if out.endswith("\n") else "\n")

    try:
        a = args.action
        if a in ("run", "update"):
            st = go("run", argv=rest) if a == "run" else go("update")
            if args.wait:
                st = wait_job(args.url, args.key, st["id"])
                show_job(st)
                return 0 if st.get("exit_code") == 0 else 1
            print(json.dumps(st))
        elif a == "job":
            st = wait_job(args.url, args.key, rest[0]) if args.wait else go("job", id=rest[0])
            show_job(st)
        elif a == "kill":
            print(json.dumps(go("kill", id=rest[0])))
        elif a == "read":
            r = go("read", path=rest[0])
            print(r["content"], end="")
        elif a == "ls":
            r = go("ls", path=rest[0] if rest else "")
            for it in r["items"]:
                print(f"{it['bytes']:>12}  {time.strftime('%Y-%m-%d %H:%M', time.gmtime(it['mtime']))}  {it['name']}")
        elif a == "put":
            r = go("write", name=rest[1] if len(rest) > 1 else Path(rest[0]).name, content=Path(rest[0]).read_text())
            print(json.dumps(r))
        elif a == "sql":
            r = go("sql", query=" ".join(rest))
            print("\t".join(r["columns"]))
            for row in r["rows"]:
                print("\t".join("" if v is None else str(v) for v in row))
            if r.get("truncated"):
                print("... (truncated)")
        elif a == "logs":
            print(go("logs", unit=rest[0] if rest else "snipetech-tape")["log"], end="")
        else:
            print(json.dumps(go(a), indent=2, default=str))
    except OpsError as exc:
        print(f"ops error {exc.code}: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"cannot reach {args.url}: {exc}", file=sys.stderr)
        return 1
    return 0


# Research jobs give the processor to the tape recorder first (7 Nov 16:58-17:21: with an events scan and a qualify run
# on four cores going, the recorder fell 25-44 s behind the chain and its coverage mark up to 5 minutes behind; the
# PumpSwap stream dropped for falling behind at 16:41). Lab workers inherit the priority of the job that starts them.
BATCH_COMMANDS = frozenset(
    {
        "lab",
        "qualify",
        "events",
        "research",
        "paper",
        "discover",
        "study",
        "copy-rank",
        "follow",
        "early-leaders",
        "dip-buys",
        "fomo-wallets",
        "stopped-wallets",
        "winners",
        "leaders-of",
        "copy-context",
        "playbook",
        "launch-tools",
        "rescore",
    }
)
BATCH_NICE = 10
SNAPSHOT_NICE = 15  # deploy/systemd/snipetech-snapshot.service: Nice=15


def _copy_hurdle(cfg: Config, store=None) -> tuple[float, dict]:
    """(what a copy of the book's size gives up between its buy and its sale, in percent of the buy; what that is made
    of): pump.fun's fee both ways and two transactions at their measured cost (`qualify.run.lab_tx_cost_info`).

    11 Nov: copy-context, early-leaders and the history tables each took a hurdle of their own off a simulated copy's
    return (2.5 %: pump.fun's fees alone; or 0.009 SOL: the transactions alone). Two transactions at the measured
    0.0042 SOL are 4.2 % of the book's 0.2 SOL copy; with pump.fun's 2.5 % a copy must make 6.7 % to break even.
    `store`: the live database (the measured cost is kept there); opened for the read when not given."""
    from .analysis.context import round_trip_pct
    from .qualify.run import lab_tx_cost_info
    from .store import Store

    own = store is None
    store = Store(cfg.storage.db_path) if own else store
    try:
        cost = lab_tx_cost_info(store, cfg)
    finally:
        if own:
            store.close()
    buy = float(cfg.book.fill_min_sol)
    pump_pct = 2.0 * float(cfg.costs.default_pump_fee_bps) / 100.0
    pct = round_trip_pct(cfg.costs.default_pump_fee_bps, cost["per_tx_sol"], buy)
    return pct, {"buy_sol": buy, "per_tx_sol": cost["per_tx_sol"], "per_tx_source": cost["per_tx_source"],
                 "pump_fees_pct": pump_pct, "tx_pct": pct - pump_pct}  # fmt: skip


def _history(args, cfg: Config) -> int:
    """`snipetech history range | fetch | check`: the bought hourly files, kept under <data>/history/pumpfundata/."""
    import calendar

    import httpx

    from . import pumpfundata as pfd
    from .store import Store

    key = os.environ.get(pfd.KEY_ENV, "")
    if not key:
        print(f"{pfd.KEY_ENV} is not set (the server's env file holds it)", file=sys.stderr)
        return 2
    dest = Path(cfg.storage.db_path).resolve().parent / "history" / "pumpfundata" / args.exchange

    if args.action == "follow":  # no call to the vendor: every wallet's first buys in the files on disk, as copies
        from .analysis.history_follow import Follow, follow_files, follow_table, walk_forward

        lo, hi = args.from_day or "0000", args.to_day or args.from_day or "9999"
        kept = dest / "follows" / f"follow_{lo}_{hi}_{args.min_sol:g}_slot.json"  # _slot: the end-of-block place
        if kept.exists() and not args.rescan:
            agg, span = Follow.load(kept), {"kept": kept.name}
        else:
            paths = [p for p in sorted(dest.glob("*.parquet")) if lo <= p.name.split("_")[-2] <= hi]
            if not paths:
                print(f"history follow: no files on disk for {lo}..{hi}", file=sys.stderr)
                return 2
            first, last = (
                calendar.timegm(time.strptime(p.name.split("_")[-2], "%Y-%m-%d")) for p in (paths[0], paths[-1])
            )
            weeks = -(
                -(last + 86400 - first) // (7 * 86400)
            )  # weeks are counted back from the last day: the last is whole
            agg = Follow(start_ts=last + 86400 - weeks * 7 * 86400)
            stats: dict = {}
            follow_files(paths, agg.add, min_sol=args.min_sol, stats=stats)
            agg.save(kept)
            agg, span = Follow.load(kept), {"from": paths[0].name, "to": paths[-1].name, "scan": stats}
        hurdle, parts = _copy_hurdle(cfg)  # every return below is after it: the whole round trip of a copy of ours
        out = follow_table(agg, test_weeks=args.test_weeks, min_pick=args.min_pick, top=args.top,
                           max_create_share=args.max_create_share, fee_pct=hurdle)  # fmt: skip
        if args.walk:
            out["walk_forward"] = walk_forward(agg, min_pick=args.min_pick, max_create_share=args.max_create_share,
                                               fee_pct=hurdle)  # fmt: skip
        print(json.dumps(span | {"min_sol": args.min_sol, "costs": parts} | out, indent=1, default=str))
        return 0

    if args.action in ("scan", "table"):  # no call to the vendor: the files on disk, and the coins' metadata links
        from . import xaccounts as xa
        from .analysis.history_scan import history_table, load_scan, save_scan, scan_files
        from .meta import fetch_meta

        lo, hi = args.from_day or "0000", args.to_day or args.from_day or "9999"
        kept = dest / "scans" / f"scan_{lo}_{hi}.json"
        if args.action == "table":  # the launches a scan of the same days kept: no file is read again
            if not kept.exists():
                print(
                    f"history table: no scan kept for {lo}..{hi}; run `history scan` with the same days",
                    file=sys.stderr,
                )
                return 2
            records, stats = load_scan(kept)
            span = {"scan": kept.name}
        else:
            paths = [p for p in sorted(dest.glob("*.parquet")) if lo <= p.name.split("_")[-2] <= hi]
            stats = {}
            records = list(scan_files(paths, min_block_sol=min(5.0, args.min_block_sol), stats=stats))
            save_scan(kept, records, stats)
            span = {"from": paths[0].name if paths else None, "to": paths[-1].name if paths else None}
        organised = [r for r in records if r["block_sol"] >= args.min_block_sol and not r.get("bonded_at_once")]
        store = Store(cfg.storage.db_path)
        try:
            fetched = asyncio.run(fetch_meta(store, [(r["mint"], r["uri"]) for r in organised if r["uri"]]))
            handles = sorted({h for r in organised if (h := xa.handle_of(store.token_meta(r["mint"])))})
            x_run = None
            if args.x_max > 0:  # paid lookups: only when asked for, and never more than asked for
                x_key = os.environ.get(xa.KEY_ENV, "")
                if not x_key:
                    print(f"{xa.KEY_ENV} is not set (the server's env file holds it)", file=sys.stderr)
                    return 2

                async def look_up() -> dict:
                    async with httpx.AsyncClient(timeout=30.0) as http:
                        return await xa.fetch_accounts(xa.Client(x_key, http), store, handles, args.x_max)

                x_run = asyncio.run(look_up())
            accounts = store.x_accounts(handles)
            hurdle, parts = _copy_hurdle(cfg, store)  # a "win" is a return above the whole round trip of a copy
            out = history_table(records, store.token_meta, min_block_sol=args.min_block_sol, stats=stats,
                                fee_pct=hurdle,
                                x_of=accounts.get if accounts else None, min_cell=args.min_cell, exits=args.exits,
                                team_min_prior=args.team_min_prior, show_from=args.show_from,
                                team_clip=args.team_clip, team_shrink=args.team_shrink,
                                wallets=tuple(w for w in (args.wallets or "").split(",") if w),
                                cross=tuple(k for k in (args.cross or "").split(",") if k) or None)  # fmt: skip
        finally:
            store.close()
        out.update(
            span | {"metadata_fetched": fetched, "x_handles": len(handles), "x_kept": len(accounts), "x_run": x_run}
        )
        out["costs"] = parts
        print(json.dumps(out, indent=1, default=str))
        return 0

    if args.action == "order":  # no call to the vendor: an hour already on disk against our own tape's order
        if args.date is None or args.hour is None:
            raise SystemExit("history order: --date YYYY-MM-DD --hour H")
        path = dest / pfd.file_name(args.exchange, args.date, args.hour)
        if not path.exists():
            print(f"history order: {path.name} is not on disk (`history check` downloads an hour)", file=sys.stderr)
            return 2
        start = calendar.timegm(time.strptime(f"{args.date} {args.hour:02d}", "%Y-%m-%d %H"))
        store = Store(cfg.storage.db_path)
        try:
            places = store.trade_places_between(
                start - 60, start + 3660, "curve" if args.exchange == "pump_fun" else "amm"
            )
        finally:
            store.close()
        from .analysis.history_scan import Orderer

        names = ["event_type", "signature", "slot_number", "token_mint", "action", "lamports_amount", "fee_lamports",
                 "virtual_lamports_reserve", "virtual_token_reserve", "token_amount"]  # fmt: skip
        rows = pfd.read_rows(path, names)
        report = pfd.order_report(rows, places)
        orderer = Orderer()  # the same hour after the rows are put in chain order: what the scans read
        perm = orderer.order({name: [r.get(name) for r in rows] for name in names})
        ordered = pfd.order_report([rows[i] for i in perm], places)
        keys = ("same_slot_pairs", "same_slot_in_order", "cross_slot_pairs", "cross_slot_in_order")
        print(json.dumps({"date": args.date, "hour": args.hour, "tape_trades": len(places), **report,
                          "after_ordering": {"rows_moved": orderer.moved, **{k: ordered[k] for k in keys},
                                             "chain": ordered["chain"]}}, indent=1))  # fmt: skip
        return 0

    async def go() -> dict:
        async with httpx.AsyncClient(timeout=120.0) as http:
            client = pfd.Client(key, http)
            if args.action == "range":
                on_disk = len(list(dest.parent.glob("*/*.parquet"))) if dest.parent.is_dir() else 0
                return {"ranges": [await client.range(x) for x in pfd.EXCHANGES], "files_on_disk": on_disk}
            if args.action == "fetch":
                if not args.from_day:
                    raise SystemExit("history fetch: --from YYYY-MM-DD")
                have = {p.name for p in dest.glob("*.parquet")} if dest.is_dir() else set()
                hours = pfd.plan_hours(args.from_day, args.to_day or args.from_day, have, args.exchange)
                return await pfd.fetch(client, dest, args.exchange, hours, args.max_files)
            if args.date is None or args.hour is None:
                raise SystemExit("history check: --date YYYY-MM-DD --hour H")
            got = await pfd.fetch(client, dest, args.exchange, [(args.date, args.hour)], 1)
            path = dest / pfd.file_name(args.exchange, args.date, args.hour)
            if not path.exists():
                return {"error": "the hour's file is not on disk", "fetch": got}
            start = calendar.timegm(time.strptime(f"{args.date} {args.hour:02d}", "%Y-%m-%d %H"))
            venue = "curve" if args.exchange == "pump_fun" else "amm"
            store = Store(cfg.storage.db_path)
            try:
                sigs = store.trade_sigs_between(start, start + 3600, venue)
            finally:
                store.close()
            rows = pfd.read_rows(path, ["event_type", "signature"])
            return {"date": args.date, "hour": args.hour, "exchange": args.exchange, "rows": len(rows),
                    "credits_spent": got["downloaded"], **pfd.compare(rows, sigs)}  # fmt: skip

    print(json.dumps(asyncio.run(go()), indent=1, default=str))
    return 0


BATCH_RPS, BATCH_CONCURRENCY = 120.0, 24  # a research job's RPC rate on a paid endpoint (Helius Business: 200 a second)
PAID_RPS = 40  # what the config sets when it finds a paid endpoint's key: the always-on services' rate


def _batch_rpc(cfg: Config, cmd: str) -> None:
    """A batch command on a paid endpoint asks faster than the services that are always on (10 Nov: a scan of the whole
    tape took hours at 40 a second while the plan allowed 200 and showed no rate limit hit). The public endpoint's rate
    and one the owner set in SNIPETECH_RPC_RPS are kept; the limiter still halves the rate on a 429."""
    if cmd not in BATCH_COMMANDS or cfg.rpc.max_rps < PAID_RPS or os.environ.get("SNIPETECH_RPC_RPS"):
        return
    cfg.rpc.max_rps = max(cfg.rpc.max_rps, BATCH_RPS)
    cfg.rpc.max_concurrency = max(cfg.rpc.max_concurrency, BATCH_CONCURRENCY)


def _yield_cpu(cmd: str) -> None:
    """Lower this process's priority when `cmd` is a heavy research job; the recorder, the operator, the book, the
    measure stage and OKTech actions keep theirs. A refused change never stops the job."""
    if cmd not in BATCH_COMMANDS:
        return
    try:
        os.nice(BATCH_NICE)
    except OSError:
        pass


# Set while a command is profiled: the work stays on the one thread a profile sees (`lab.lab_workers` gives one
# process, `work_in_place` runs what would go to a thread where it is called).
PROFILE_ENV = "SNIPETECH_PROFILE"


@contextlib.contextmanager
def work_in_place():
    """While inside: `asyncio.to_thread` runs the function where it is called. A profile sees one thread, and the
    heavy part of several commands (a deployer's report) is handed to another."""
    real = asyncio.to_thread

    async def here(fn, /, *args, **kwargs):
        return fn(*args, **kwargs)

    asyncio.to_thread = here
    try:
        yield
    finally:
        asyncio.to_thread = real


def _profiled(args, argv: list[str] | None) -> int:
    """`--profile`: the command as it is, and where its time went in jobs/profile-<command>-<time>.log (11 Nov: the
    recorder's two real costs showed only in a profile taken on the server, where its tables are large). The command's
    output and exit code are its own; a report that cannot be written never fails it."""
    import cProfile
    import io
    import pstats

    prof = cProfile.Profile()
    os.environ[PROFILE_ENV] = "1"
    t0 = time.time()
    prof.enable()
    try:
        with work_in_place():
            return _main(args)
    finally:
        prof.disable()
        os.environ.pop(PROFILE_ENV, None)
        try:
            cfg = Config.load(args.config)
            data = Path(args.db or cfg.storage.db_path).resolve().parent
            words = [a for a in (argv if argv is not None else sys.argv[1:]) if a != "--profile"]
            text = io.StringIO()
            said = " ".join(words[words.index(args.cmd) :])
            text.write(f"{said}: {time.time() - t0:.1f} s, one process, one thread\n")
            for order in ("tottime", "cumulative"):
                text.write(f"\n== by {order} ==\n")
                pstats.Stats(prof, stream=text).strip_dirs().sort_stats(order).print_stats(60)
            (data / "jobs").mkdir(parents=True, exist_ok=True)
            (data / "jobs" / f"profile-{args.cmd}-{int(t0)}.log").write_text(text.getvalue())
        except Exception as exc:  # noqa: BLE001
            print(f"profile not written: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    from .infra import nightly

    # a step of the nightly unit that fed nothing ends here, on a server whose unit file still runs it
    retired = nightly.retired_reason(list(sys.argv[1:] if argv is None else argv), nightly.current_unit())
    if retired:
        print(retired)
        return 0
    args = build_parser().parse_args(argv)
    if args.profile:
        return _profiled(args, argv)
    return _main(args)


def _main(args) -> int:
    _yield_cpu(args.cmd)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    if args.cmd == "init":
        path = Path(args.config or "snipetech.toml")
        if path.exists() and not args.force:
            print(f"{path} exists (use --force to overwrite)", file=sys.stderr)
            return 1
        path.write_text(dump_toml(Config().to_dict()))
        print(f"wrote {path}")
        return 0
    if args.cmd == "remote":
        return _remote(args)
    cfg = Config.load(args.config)
    _batch_rpc(cfg, args.cmd)
    if args.db:
        cfg.storage.db_path = args.db
    if args.rpc:
        cfg.rpc.http_url = args.rpc
    _apply_overrides(cfg, args)
    if args.cmd == "doctor" and args.settings:
        from .config import settings_drift

        print(json.dumps(settings_drift(cfg.path)))
        return 0
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
    if args.cmd == "ops-serve":
        return _ops_serve(args, cfg)
    if args.cmd == "solanatracker":
        from .solanatracker import SolanaTracker

        stc = SolanaTracker(counter=_usage_from_cfg(cfg, "solanatracker"))
        try:
            if args.action == "leaderboard":
                rows, cursor = stc.leaderboard(days=args.days, limit=args.limit)
                out = {"traders": rows, "next_cursor": cursor}
            elif args.action == "first-buyers":
                out = stc.first_buyers(args.mint, limit=args.limit)
            elif args.action == "traders":
                out = stc.token_traders(args.mint, limit=args.limit)
            else:
                out = stc.wallet_pnl(args.wallet)
        finally:
            stc.close()
        print(json.dumps(out, indent=1, default=str))
        return 0
    if args.cmd == "discover":
        return _discover(args, cfg)
    if args.cmd == "tx":
        svc = Service(cfg)

        async def go():
            try:
                return await dump_transaction(svc, args.signature)
            finally:
                await svc.close()
                svc.store.close()

        print(asyncio.run(go()))
        return 0
    if args.cmd == "db" and args.action == "retain":
        from .infra import retention
        from .store import Store

        store = Store(cfg.storage.db_path)
        try:
            if args.days is not None:
                retention.ask(store, args.days if args.days > 0 else None, time.time(), "db retain")
            print(json.dumps(retention.current(store, cfg.storage.retain_days, time.time())))
        finally:
            store.close()
        return 0
    if args.cmd == "db":
        from .infra.db_repair import backfill, check, forget, reindex, salvage, swap

        act = {"check": lambda p, t: check(p), "reindex": reindex, "salvage": salvage, "swap": swap,
               "backfill": backfill, "forget": forget}[args.action]  # fmt: skip
        out = act(cfg.storage.db_path, args.table)
        if args.action in ("salvage", "swap", "backfill", "forget"):
            print(json.dumps(out))
            return 0 if "error" not in out else 1
        print(json.dumps(out))
        return 0 if out.get("ok", (out.get("after") or {}).get("ok")) else 1
    if args.cmd == "snapshot-db":
        from .infra.snapshot_db import SNAPSHOT_EVERY_SECS, UNIT_LIMIT_SECS, snapshot_db

        try:  # the unit's Nice=15 for the operator's job too, which starts at the ops service's priority
            os.nice(max(0, SNAPSHOT_NICE - os.nice(0)))
        except OSError:
            pass
        # the bare command is the timer's unit, which systemd kills after UNIT_LIMIT_SECS
        limit = args.limit_secs if args.limit_secs else (None if args.force else UNIT_LIMIT_SECS)
        every = 0.0 if args.force else SNAPSHOT_EVERY_SECS
        print(json.dumps(snapshot_db(cfg.storage.db_path, every_secs=every, limit_secs=limit)))
        return 0
    if args.cmd == "events":
        return _events(args, cfg)
    if args.cmd == "book":
        from .book.run import run_book
        from .book.switch import book_bar_reply, book_flag, book_line, book_live, book_off_reply, book_switch
        from .operator.paths import OperatorPaths

        bpaths = OperatorPaths(Path(cfg.storage.db_path).resolve().parent)
        if args.action in ("on", "off"):
            book_switch(bpaths, args.action == "on", "cli", time.time())
            # the switch is a file next to this machine's database: run it on the server (`snipetech remote --wait run
            # book on|off`) or with /book on|off in Telegram
            print(f"switch file {book_flag(bpaths)} on this machine")
            print(f"book on: {book_line(cfg, bpaths)}" if args.action == "on" else book_off_reply(cfg, bpaths))
            return 0
        if args.action == "bar":
            from .book.switch import book_bar_file

            print(f"bar file {book_bar_file(bpaths)} on this machine")
            print(book_bar_reply(cfg, bpaths, args.value, "cli", time.time()))
            return 0
        if args.action == "fresh":
            from .book.switch import book_fresh, book_fresh_file

            if not args.value:
                raise SystemExit("book fresh: give the wallet")
            book_fresh(bpaths, args.value.strip(), "cli", time.time())
            print(f"fresh-start file {book_fresh_file(bpaths)} on this machine")
            print(f"{args.value.strip()[:6]}…: live pause lifted; judged only on its trips from now")
            return 0
        if args.action == "mcap":
            from .book.switch import book_mcap, book_mcap_file, book_mcaps

            print(f"cap file {book_mcap_file(bpaths)} on this machine")
            if args.value:
                if not args.cap:
                    raise SystemExit("book mcap: not changed: give the wallet and a cap in SOL, or off")
                try:
                    value = None if args.cap.strip().lower() == "off" else float(args.cap)
                    book_mcap(bpaths, args.value.strip(), value, "cli", time.time())
                except ValueError as exc:
                    raise SystemExit(f"book mcap: not changed: {exc}") from None
            caps = book_mcaps(bpaths)
            print("market-cap caps: " + (", ".join(f"{w[:6]}… below {v:g} SOL" for w, v in caps.items()) or "none"))
            return 0
        if args.action == "status":
            from .book.state import last_run_line
            from .store import Store

            st = Store(cfg.storage.db_path)
            try:
                last = last_run_line(st, time.time())
            finally:
                st.close()
            print(
                f"book: {'on' if book_live(cfg, bpaths) else 'off'}"
                + (" (halted by /stop)" if bpaths.stop_flag.exists() else "")
                + f"; {last}"
            )
            return 0
        if args.live and not book_live(cfg, bpaths):
            raise SystemExit("book: --live refused: the book is off ([book] enabled false and no /book on)")
        svc = Service(cfg)

        async def go():
            try:
                return await run_book(svc, cfg, time.time(), dry=(False if args.live else None))
            finally:
                await svc.close()
                svc.store.close()

        print(json.dumps(asyncio.run(go()), indent=1, default=str))
        return 0
    if args.cmd == "strategist":
        from .strategist.run import run_strategist

        svc = Service(cfg)

        async def go():
            try:
                return await run_strategist(svc, cfg, time.time(), dry_run=args.dry_run, force=args.force)
            finally:
                await svc.close()
                svc.store.close()

        print(json.dumps(asyncio.run(go()), indent=1, default=str))
        return 0
    if args.cmd == "measure":
        return _measure(args, cfg)
    if args.cmd == "exitrun":
        from .exitrun.run import log_path, run_shadow, shadow_report

        svc = Service(cfg)
        try:
            if args.action == "report":
                out = shadow_report(svc.store, cfg.operator.wallet, log_path(cfg))
            else:
                out = asyncio.run(run_shadow(cfg, svc, args.minutes))
        finally:
            svc.store.close()
        print(json.dumps(out, indent=1, default=str))
        return 0
    if args.cmd == "fomo-wallets":
        from .analysis.fomo_wallets import fomo_wallets

        svc = Service(cfg)
        try:
            rows = fomo_wallets(svc.tape_store, time.time(), days=args.days, min_buys=args.min_buys)
            labelled = []
            for r in rows[: max(0, args.label)]:
                svc.store.set_label(r["wallet"], f"fomo {r['buys']} buys {r['buy_sol']:g} SOL", "cand", "fomo")
                svc.store.upsert_candidate(r["wallet"], "fomo", r, int(time.time()))  # qualify's queue
                labelled.append(r["wallet"])
        finally:
            svc.store.close()
        out = {"days": args.days, "wallets": len(rows), "top": rows[:50], "labelled": labelled}
        print(json.dumps(out, indent=1) if args.json else "\n".join(
            f"{r['wallet']}  {r['buys']} buys  {r['buy_sol']:g} SOL  {r['coins']} coins" for r in rows[:50])
            or "no FOMO wallet with enough buys")  # fmt: skip
        return 0
    if args.cmd == "playbook":
        from .analysis.playbook import playbook

        svc = Service(cfg)
        try:
            out = playbook(svc.tape_store, args.wallet, time.time(), hours=args.hours, top=args.top)
        finally:
            svc.store.close()
        if args.json:
            print(json.dumps(out, indent=1))
            return 0
        print(f"{out['wallet']}: {out['coins']} coins in {out['hours']:g} h")
        for r in out["entries"]:
            print(f"  {r['kind']}: {r['n']} ({r['share']:.0%})  net {r['net_sol']:+.2f} SOL  win {r['win']}  "
                  f"buys {r['buy_sol']['p50']} SOL  first sell after {(r['slots_to_first_sell'] or {}).get('p50')} "
                  f"slots  bid {(r['bid_sol'] or {}).get('p50')}")  # fmt: skip
        for t in out["triggers"][:20]:
            print(f"  follows {t['wallet']} ({t['label'] or 'no label'}): {t['coins']} coins, "
                  f"{t['followed_share']} of that wallet's {t['its_coins']}")  # fmt: skip
        return 0
    if args.cmd == "history":
        return _history(args, cfg)
    if args.cmd == "launch-tools":
        from .analysis.launch_tools import coin_marks, launch_tools

        svc = Service(cfg)

        async def scan():
            try:
                if args.mint:
                    return await coin_marks(svc.rpc, svc.tape_store, args.mint)
                return await launch_tools(svc.rpc, svc.tape_store, time.time(), hours=args.hours,
                                          max_launches=args.max, min_launches=args.min_launches, mark=args.mark,
                                          marks_store=svc.store, funding_of=args.funding_of,
                                          min_block_sol=args.block_sol_min)  # fmt: skip
            finally:
                await svc.close()
                svc.store.close()

        out = asyncio.run(scan())
        if args.json:
            print(json.dumps(out, indent=1))
            return 0
        if args.mint:
            for c in out["coins"]:
                print(f"{c['mint']} {c['symbol'] or '?'}: {', '.join(c['marks']) or c['error'] or 'no mark'}")
            print(f"shared: {', '.join(out['shared']) or 'none'}")
            return 0
        print(f"{out['launches']} launches read of {out['window_launches']} in {out['hours']:g} h; all: "
              f"{out['all']['reached_30']:.1%} reached 30 SOL")  # fmt: skip
        for r in out["marks"]:
            print(f"  {r['mark']}: {r['launches']} launches, {r['creators']} creators, {r['reached_30']:.0%} reached "
                  f"30 SOL, {r['graduated']:.0%} graduated, median peak {r['peak_sol']} SOL, +5 s {r['ret_13']} %")  # fmt: skip
        for key, rows in (out.get("split") or {"by": {}})["by"].items():
            for r in rows:
                print(f"  {args.mark} {key} {r['bucket']}: {r['launches']} launches, {r['reached_30']:.0%} reached 30 "
                      f"SOL, +5 s {r['ret_13']} %, +60 s {r['ret_150']} %")  # fmt: skip
        return 0
    if args.cmd == "copy-context":
        from .analysis.context import copy_context

        svc = Service(cfg)
        try:
            # the hurdle a return must clear: a copy of the book's size, at what a transaction costs live
            hurdle, parts = _copy_hurdle(cfg, svc.store)
            out = copy_context(
                svc.tape_store, time.time(), hours=args.hours, entry_slots=args.entry_slots, min_sol=args.min_sol,
                behind_leader=args.behind_leader, by_entity=args.by_entity, fee_pct=hurdle,
                any_buyer=args.any_buyer, taken_by=tuple(w.strip() for w in args.taken_by.split(",") if w.strip()),
                horizons=tuple(int(h) for h in args.horizons.split(",") if h.strip()),
                kinds=tuple(k.strip() for k in args.groups.split(",") if k.strip()),
            )  # fmt: skip
        finally:
            svc.store.close()
        out["costs"] = parts
        if args.json:
            print(json.dumps(out, indent=1))
            return 0
        print(f"{out['events']} buys of known wallets in {out['coins']} coins, entered {out['entry']}; "
              f"returns in % before {hurdle:.1f} % of costs on a {parts['buy_sol']:g} SOL copy "
              f"({parts['pump_fees_pct']:g} % pump.fun fees, {parts['tx_pct']:.1f} % for two transactions at "
              f"{parts['per_tx_sol']:g} SOL)")  # fmt: skip
        for name, rows in out["by"].items():
            print(f"\n{name}")
            for r in rows:
                print("  " + "  ".join(f"{k} {v}" for k, v in r.items()))
        return 0
    if args.cmd in ("winners", "leaders-of"):
        from .analysis.winners import leaders_of, winners

        svc = Service(cfg)
        try:
            if args.cmd == "winners":
                rows = winners(svc.tape_store, time.time(), hours=args.hours, min_trips=args.min_trips, top=args.top)
                labelled = []
                for r in [r for r in rows if r["pnl_sol"] > 0 and r["oversold_share"] < 0.5 and r["create_slot_share"]
                          < 0.4 and r["median_entry_slots"] > 20][: max(0, args.label)]:  # fmt: skip
                    svc.store.set_label(r["wallet"], f"winner {r['pnl_sol']:+.0f} SOL/{r['trips']}", "cand", "winners")
                    svc.store.upsert_candidate(r["wallet"], "winners", r, int(time.time()))  # qualify's queue
                    labelled.append(r["wallet"])
                out = {"hours": args.hours, "min_trips": args.min_trips, "wallets": rows, "labelled": labelled}
                text = "\n".join(f"{r['wallet']}  {r['pnl_sol']:+.3f} SOL over {r['trips']}  win {r['win_rate']:.0%}  "
                                 f"{r['style']}" for r in rows) or "no wallet has enough closed round trips"  # fmt: skip
            else:
                rows = leaders_of(svc.tape_store, args.wallet, time.time(), hours=args.hours, max_slots=args.max_slots,
                                  after_slots=args.after_slots)  # fmt: skip
                out = {"wallet": args.wallet, "hours": args.hours, "leaders": rows}
                text = "\n".join(f"{r['wallet']}  led {r['coins']} coins  {r['median_slots_behind']} slots / "
                                 f"{r['median_trades_behind']} trades behind  gap {r['median_price_gap_pct']} %"
                                 for r in rows) or "no wallet bought before it"  # fmt: skip
        finally:
            svc.store.close()
        print(json.dumps(out, indent=1) if args.json else text)
        return 0
    if args.cmd == "dip-buys":
        from .analysis.dip_buys import TOP, dip_buys
        from .qualify.run import lab_tx_cost_info

        svc = Service(cfg)
        try:
            cost = lab_tx_cost_info(svc.store, cfg)  # the measured cost of a transaction, as the labs charge it
            buy = float(args.buy if args.buy is not None else cfg.book.fill_min_sol)
            out = dip_buys(svc.tape_store, time.time(), hours=args.hours, min_peak_sol=args.min_peak_sol,
                           min_buyers=args.min_buyers, fall=args.fall, within=args.within, behind=args.behind,
                           buy_sol=buy, per_tx_sol=cost["per_tx_sol"], min_n=args.min_n, rebound=args.rebound,
                           rebound_within=args.rebound_within)  # fmt: skip
        finally:
            svc.store.close()
        rows = out.pop("rows")
        first = str(out["horizons_secs"][0]) if out["horizons_secs"] else ""
        ranked = sorted(rows, key=lambda r: -r["pnl"][first]) if first else []
        out |= {"per_tx_source": cost["per_tx_source"], "best": ranked[:TOP], "worst": ranked[-TOP:][::-1]}
        if args.json:
            print(json.dumps(out, indent=1))
        else:
            print(f"{out['events']} hot falls in {out['hours']:g} h ({out['hot_coins']} coins past the level), "
                  f"{buy:g} SOL a buy, landing {out['landing']}")  # fmt: skip
            for k, st in out["all"].items():
                print(f"  sold after {k} s: n {st['n']}  mean {st['mean_sol']:+.4f}  lower {st['lower_sol']:+.4f}  "
                      f"win {st['win_rate']:.0%}  five best made {st['top5_share']:.0%} of what was won")  # fmt: skip
        return 0
    if args.cmd == "early-leaders":
        from .analysis.early_leaders import early_leaders

        svc = Service(cfg)
        try:
            # a copy of the book's size: pump.fun's fee both ways and two transactions at their measured cost (until
            # 11 Nov a flat 0.009 SOL: the transactions, nothing for pump.fun's 0.005)
            hurdle, tx = _copy_hurdle(cfg, svc.store)
            buy = tx["buy_sol"]
            cost = buy * hurdle / 100.0
            rows = early_leaders(svc.tape_store, time.time(), hours=args.hours, min_n=args.min_n,
                                 max_slots=args.max_slots, min_sol=args.min_sol, buy_sol=buy, cost=cost)  # fmt: skip
            labelled = []
            for r in [r for r in rows if r["lower_sol"] > 0][: max(0, args.label)]:
                svc.store.set_label(
                    r["wallet"], f"early-leader n{r['n']} {r['mean_sol']:+.4f}", "cand", "early-leaders"
                )
                svc.store.upsert_candidate(r["wallet"], "early-leaders", r, int(time.time()))  # qualify's queue
                labelled.append(r["wallet"])
        finally:
            svc.store.close()
        out = {"hours": args.hours, "min_n": args.min_n, "buy_sol": buy, "cost_sol": cost,
               "per_tx_source": tx["per_tx_source"], "wallets": rows, "labelled": labelled}  # fmt: skip
        print(json.dumps(out, indent=1) if args.json else "\n".join(
            f"{r['wallet']}  n {r['n']}  mean {r['mean_sol']:+.4f}  lower {r['lower_sol']:+.4f}  win {r['win_rate']:.0%}"
            for r in rows) or "no wallet has enough early buys")  # fmt: skip
        return 0
    if args.cmd == "research":
        from .operator.research import research_cli

        return research_cli(args, cfg)
    if args.cmd == "stopped-wallets":
        from .qualify.stopped import GROUPS, stopped_wallets_cli

        out = stopped_wallets_cli(cfg, args.max, Path(cfg.storage.db_path).resolve().parent / "jobs")
        if args.json:
            print(json.dumps(out, indent=1, default=str))
            return 0
        if out.get("deferred"):
            print(out["reason"])
            return 0
        print(f"{out['wallets']} wallets at {out['landing']}, {out['per_tx_sol']:g} SOL a transaction "
              f"({out['per_tx_source']}); copies since each wallet's first such verdict")  # fmt: skip
        for g in GROUPS:
            st = out["groups"].get(g)
            if not st:
                continue
            if not st["copies"]:
                print(f"  {g}: {st['wallets']} wallets, no copy since")
                continue
            print(f"  {g}: {st['wallets']} wallets, {st['copies']} copies since  mean {st['mean_sol']:+.4f}  "
                  f"lower {st['lower_sol']:+.4f}  win {st['win_rate']:.0%}  {st['wallets_up']} of "
                  f"{st['wallets_judged']} wallets up  (at the verdict: {st['at_verdict_mean_sol']:+.4f})")  # fmt: skip
        return 0
    if args.cmd == "qualify":
        from .qualify.run import run_qualify_cli

        out = run_qualify_cli(cfg, args.max, Path(cfg.storage.db_path).resolve().parent / "jobs", early=args.early)
        print(json.dumps(out, indent=1, default=str))
        return 0
    if args.cmd == "vybe":
        from .vybe import Vybe

        vy = Vybe(counter=_usage_from_cfg(cfg, "vybe"))
        try:
            fn = vy.top_traders_raw if args.raw else vy.top_traders
            out = fn(resolution=args.resolution, limit=args.limit, sort=args.sort)
        finally:
            vy.close()
        print(json.dumps(out, indent=1, default=str))
        return 0
    if args.cmd == "birdeye":
        from .birdeye import Birdeye

        be = Birdeye(counter=_usage_from_cfg(cfg, "birdeye"))
        try:
            if args.action == "credits":
                out = be.credits()
            elif args.action == "gainers":
                out = (
                    be.gainers_raw(args.period, args.sort, limit=args.limit)
                    if args.raw
                    else be.gainers(args.period, args.sort, limit=args.limit)
                )
            elif args.action == "top":
                out = be.top_traders(args.mint, time_frame=args.frame, limit=min(10, args.limit))
            else:
                out = be.wallet_pnl(args.wallet, "30d")
        finally:
            be.close()
        print(json.dumps(out, indent=1, default=str))
        return 0
    if args.cmd == "operate":
        import subprocess

        from .operator import autotest, budget_switch, oktech_answers, risk_guard
        from .operator.run import operate, telegram_setup_cli

        if args.action == "oktech" or args.dev_max_zero is not None or args.oktech_words is not None:  # first: it
            # refuses another switch's values
            return oktech_answers.oktech_cli(cfg, args)
        if args.action == "idea":
            from .operator.learning import upsert_idea
            from .operator.paths import OperatorPaths as _Paths

            keys = ("id", "status", "title", "why", "test", "expected", "kind", "priority")
            fields = {k: getattr(args, f"idea_{k}") for k in keys}
            fields = {k: v for k, v in fields.items() if v is not None}
            try:
                idea = upsert_idea(_Paths(Path(cfg.storage.db_path).resolve().parent).ensure(), fields, source="cloud")
            except ValueError as exc:
                raise SystemExit(f"operate idea: not added: {exc}") from None
            print(json.dumps({"id": idea.get("id"), "title": idea.get("title"), "status": idea.get("status")}))
            return 0
        if args.action == "risk" or any(v is not None for v in (args.day_stop, args.risk_floor)):
            return risk_guard.risk_cli(cfg, args)
        if (
            args.action == "autotest"
            or args.autotest_on
            or args.autotest_off
            or any(getattr(args, k) is not None for k in ("approved", "autotest_wallet", *autotest.FIELDS))
        ):
            return autotest.autotest_cli(cfg, args)
        if (
            args.action == "budget"
            or args.budget_default
            or any(v is not None for v in (args.daily_usd, args.turn_usd, args.tool_calls, args.analyst_every_h))
        ):
            return budget_switch.budget_cli(cfg, args)
        if args.telegram_setup:
            return telegram_setup_cli(cfg)
        if args.commands:
            from .operator.run import commands_pass

            svc = Service(cfg)

            async def go_commands():
                try:
                    return await commands_pass(cfg, svc)
                finally:
                    await svc.close()
                    svc.store.close()

            return asyncio.run(go_commands())
        if args.expect:
            from .operator.gate import write_expected

            print(json.dumps(write_expected(cfg, args.expect)))
            return 0
        repo = Path(args.repo or Path(__file__).resolve().parents[2])
        branch = args.branch or subprocess.run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                                               capture_output=True, text=True).stdout.strip() or "main"  # fmt: skip
        svc = Service(cfg)
        try:
            return asyncio.run(operate(cfg, svc, analyst=args.analyst, dry_run=args.dry_run, repo=repo, branch=branch))
        finally:
            svc.store.close()
    if args.cmd == "vanity":
        from .vanity import grind

        out = args.out or str(Path(cfg.storage.db_path).resolve().parent / ".wallets")
        r = grind(args.starts_with, args.ends_with, not args.exact_case, max(1, args.workers), out,
                  progress=lambda m: print(m, flush=True))  # fmt: skip
        print(f"\nfound {r['address']} after {r['keys_tried']:,} keys in {r['secs'] / 60:.1f} min")
        print(f"private key saved to {r['file']} (readable only by root and the snipetech user).")
        print(f"on the server: sudo cat {r['file']}   then import it, then: sudo rm {r['file']}")
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
    except OktechError as exc:
        hint = " — check OKS_KEY (a key from OKTech's Sniper API settings)" if exc.status in (401, 403) else ""
        print(f"OKTech: {exc}{hint}", file=sys.stderr)
        return 4
    except (httpx.HTTPError, RpcError) as exc:
        print(
            f"RPC error: {exc}\nEverything fetched so far is saved; re-run to resume. For speed and reliability use a "
            "paid RPC (export HELIUS_API_KEY=... or SNIPETECH_RPC_URL=...) or lower rpc.max_rps.",
            file=sys.stderr,
        )
        return 3
