"""Live watcher: feed real fixture logs through the websocket handler (no network)."""

import asyncio
import json
import time
from pathlib import Path

from snipetech.config import Config
from snipetech.live import LiveWatcher
from snipetech.service import Service
from snipetech.store import Store

FIX = Path(__file__).parent / "fixtures" / "tx"


def _notification(path: Path) -> tuple[int, dict]:
    tx = json.loads(path.read_text())
    return tx["slot"], {"signature": path.stem, "err": None, "logs": tx["meta"]["logMessages"]}


def test_live_indexes_launch_and_emits_known_signal(capsys):
    cfg = Config()
    cfg.live.funder_lookup = False
    store = Store(":memory:")
    svc = Service(cfg, store=store)
    create = next(FIX.glob("28fREJXt*.json"))
    creator = "8LxvrjUyF9wFqMm6uKzexkE1uNRgnJsdQPtAiQLgv4Cr"
    store.list_add(creator, "block", "test")

    async def run():
        w = LiveWatcher(svc, signals="known", firehose=False)
        w.recent.clear()
        slot, value = _notification(create)
        w._on_logs(slot, value)
        await asyncio.sleep(0.05)  # let the launch signal task run
        w._flush()
        return w

    w = asyncio.run(run())
    launch = store.launch("EzHRAWk4UBVyyztmeCk2EgBx7BBKEeRWMsaXSWZhpump")
    assert launch is not None and launch.deployer == creator
    # the dev buy inside the create tx is inside the live window, so it is stored
    assert len(store.trades_for_mint(launch.mint)) == 1
    out = capsys.readouterr().out.strip().splitlines()
    sig = json.loads(out[-1])
    assert sig["kind"] == "launch" and sig["action"] == "skip" and sig["verdict"] == "BLOCK"
    assert store.signals_since(0)[0]["mint"] == launch.mint
    assert w.stats["launches"] == 1


def test_live_ignores_trades_of_unknown_old_tokens():
    cfg = Config()
    store = Store(":memory:")
    w = LiveWatcher(Service(cfg, store=store), signals="none", firehose=False)
    trade = next(FIX.glob("22BtADtf*.json"))
    slot, value = _notification(trade)
    w._on_logs(slot, value)
    w._flush()
    assert store.stats()["trades"] == 0  # not a launch we saw: outside the live window


def test_coverage_marked_only_without_gaps():
    cfg = Config()
    store = Store(":memory:")
    w = LiveWatcher(Service(cfg, store=store), signals="none", firehose=False)
    from snipetech.models import Launch

    now = int(time.time())
    ok = Launch("mintA" + "1" * 39, "u", "u", "", "", "", "", 1, now - 2000, "s1")
    bad = Launch("mintB" + "1" * 39, "u", "u", "", "", "", "", 2, now - 1500, "s2")
    store.add_launches([ok, bad])
    w.disconnected_at = None
    w.gaps = [(now - 1000, now - 900)]  # inside bad's window [now-1501, now-600], after ok's ends at now-1100
    w.closed_launches = [ok, bad]
    w._mark_coverage()
    assert store.coverage(f"mint:{ok.mint}").get("live")
    assert not store.coverage(f"mint:{bad.mint}")
