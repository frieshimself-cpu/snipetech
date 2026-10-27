"""Config, ingestion (fake RPC over real fixtures), HTTP API, export, CLI parsing."""

import asyncio
import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from snipetech.config import Config, dump_toml
from snipetech.ingest import Ingestor, assign_positions, incoming_sol_transfer
from snipetech.service import Service
from snipetech.store import Store

from .synth import addr, pump_launch, rinse_launch

FIX = Path(__file__).parent / "fixtures" / "tx"
TXS = {p.stem: json.loads(p.read_text()) for p in sorted(FIX.glob("*.json"))}


def test_config_roundtrip(tmp_path, monkeypatch):
    cfg = Config()
    cfg.set("snipe.exit.take_profit", "2.5")
    cfg.set("snipe.exit.sell_on_dev_sell", "false")
    cfg.set("costs.tip_sol", 0.002)
    path = cfg.save(tmp_path / "c.toml")
    again = Config.load(path)
    assert again.snipe.exit.take_profit == 2.5
    assert again.snipe.exit.sell_on_dev_sell is False
    assert again.costs.tip_sol == 0.002
    with pytest.raises(KeyError):
        cfg.set("snipe.nope", 1)
    monkeypatch.setenv("HELIUS_API_KEY", "abc")
    c2 = Config.load(tmp_path / "missing.toml")
    assert "helius" in c2.rpc.http_url and c2.rpc.websocket_url().startswith("wss://")
    assert "[snipe.exit]" in dump_toml(cfg.to_dict())


class FakeRpc:
    """Serves fixture transactions; signature listings are newest-first like the real RPC."""

    def __init__(self, txs: dict[str, dict]):
        self.txs = txs
        self.calls = 0

    async def get_transactions(self, sigs):
        self.calls += len(sigs)
        return {s: self.txs.get(s) for s in sigs}

    async def get_account_data(self, address):
        return None


def test_ingest_fixture_transactions():
    store = Store(":memory:")
    ing = Ingestor(store, FakeRpc(TXS))
    infos = sorted(
        ({"signature": s, "slot": t["slot"], "err": None, "blockTime": t.get("blockTime")} for s, t in TXS.items()),
        key=lambda i: -i["slot"],
    )
    res = asyncio.run(ing.ingest(infos))
    assert res.fetched == len(TXS)
    from snipetech.solana.tx import extract_from_transaction

    creates = sum(1 for s, t in TXS.items() for e in extract_from_transaction(t, s).events if e.name == "CreateEvent")
    assert res.launches == creates == 3
    assert store.stats()["launches"] == 3
    # every trade transaction was fingerprinted
    assert len(store.missing_tx_meta(list(TXS))) == 0
    assert res.trades >= len(TXS) - 2
    # second pass is free: everything is marked seen
    again = asyncio.run(ing.ingest(infos))
    assert again.fetched == 0
    assert store.stats()["pools"] >= 3


def test_assign_positions_orders_within_slot():
    infos = [{"signature": "c", "slot": 5}, {"signature": "b", "slot": 5}, {"signature": "a", "slot": 4}]
    pos = assign_positions(infos)
    assert pos == {"a": (4, 0), "b": (5, 0), "c": (5, 1)}


def test_incoming_sol_transfer_from_real_tx():
    sig = next(iter(TXS))
    tx = TXS[sig]
    keys = tx["transaction"]["message"]["accountKeys"]
    # no transfer *into* the program id itself
    assert incoming_sol_transfer(tx, "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P") is None
    assert isinstance(keys, list)


@pytest.fixture
def api(tmp_path):
    from snipetech.api import make_server

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "api.db")
    cfg.verdict.bootstrap_samples = 300
    cfg.api.api_key = "secret"
    store = Store(cfg.storage.db_path)
    now = int(time.time())
    for i in range(8):
        rinse_launch(store, i, addr("api-rinser"), now - 50_000 + i * 3000, 10_000_000 + i * 10_000)
    svc = Service(cfg, store=store)
    asyncio.run(svc.analyze_deployer(addr("api-rinser"), refresh=False))
    server = make_server(cfg, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", cfg
    server.shutdown()


def _get(url, key="secret", method="GET", body=None):
    req = urllib.request.Request(url, method=method, data=body, headers={"X-API-Key": key})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_api_verdicts_lists_and_rules(api):
    base, cfg = api
    rinser = addr("api-rinser")
    v = _get(f"{base}/v1/deployer/{rinser}")
    assert v["verdict"] == "AVOID" and v["rinse_rate"] == 1.0
    unknown = addr("nobody")
    assert _get(f"{base}/v1/deployer/{unknown}")["verdict"] == "UNKNOWN"
    _get(f"{base}/v1/lists/allow/{unknown}", method="POST", body=b'{"reason": "trusted"}')
    assert _get(f"{base}/v1/deployer/{unknown}")["verdict"] == "ALLOW"
    rules = _get(f"{base}/v1/rules")
    assert rinser in rules["skip_deployers"] and unknown in rules["snipe_deployers"]
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(f"{base}/v1/health", key="wrong")
    assert err.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(f"{base}/v1/deployer/not-an-address")
    assert err.value.code == 400


def test_export_rules_file(tmp_path):
    cfg = Config()
    cfg.verdict.bootstrap_samples = 300
    store = Store(":memory:")
    now = int(time.time())
    for i in range(8):
        pump_launch(store, i, addr("exp-pumper"), now - 60_000 + i * 3000, 20_000_000 + i * 10_000)
    svc = Service(cfg, store=store)
    asyncio.run(svc.analyze_deployer(addr("exp-pumper"), refresh=False))
    out = tmp_path / "rules.json"
    rules = svc.export_rules(out)
    assert json.loads(out.read_text()) == json.loads(json.dumps(rules, default=str))
    assert addr("exp-pumper") in rules["snipe_deployers"]
    assert rules["deployers"][addr("exp-pumper")]["recommended"]["buy_sol"] > 0


def test_cli_parses_every_command():
    from snipetech.cli import build_parser

    p = build_parser()
    a = addr("cli")
    for argv in (["status"], ["deployer", a, "--family", "--json"], ["copy", a, "--latency", "2"], ["copy-rank", a, a],
                 ["audit", a, "--apply-calibration"], ["optimize", "--deployer", a], ["backtest", "--days", "3"],
                 ["top-deployers", "--backfill", "5"], ["discover-wallets"], ["watch", "--poll"], ["serve", "--port", "9"],
                 ["agent", "-p", "hi"], ["list", "add", a, "--list", "block"], ["export", "--out", "x.json"],
                 ["index-launches", "--hours", "1"], ["mint", a], ["config", "set", "snipe.buy_sol", "1"]):  # fmt: skip
        assert p.parse_args(argv).cmd == argv[0]


def test_rpc_waits_out_rate_limits():
    import httpx

    from snipetech.solana.rpc import SolanaRpc

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 123})

    async def run():
        rpc = SolanaRpc("http://rpc.test", max_rps=50, max_retries=1)
        await rpc.client.aclose()
        rpc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        slot = await rpc.get_slot()
        rate = rpc.limiter.rate
        await rpc.close()
        return slot, rate

    slot, rate = asyncio.run(run())
    assert slot == 123 and calls["n"] == 4
    assert rate < 50  # throttled, recovering gradually


class PagingRpc:
    """getSignaturesForAddress over a synthetic newest-first history (no transactions)."""

    def __init__(self, n: int):
        self.history = [{"signature": f"s{i:05d}", "slot": 1000 + i, "blockTime": 1_700_000_000 + i, "err": None}
                        for i in range(n)][::-1]  # fmt: skip

    def add(self, k: int) -> None:
        start = len(self.history)
        new = [{"signature": f"s{i:05d}", "slot": 1000 + i, "blockTime": 1_700_000_000 + i, "err": None}
               for i in range(start, start + k)][::-1]  # fmt: skip
        self.history = new + self.history

    async def get_signatures(self, address, before=None, until=None, limit=1000):
        sigs = [s["signature"] for s in self.history]
        i = sigs.index(before) + 1 if before else 0
        j = sigs.index(until) if until else len(sigs)
        return self.history[i:j][:limit]

    async def get_transactions(self, sigs):
        return {s: None for s in sigs}


def test_backfill_address_is_incremental():
    store = Store(":memory:")
    rpc = PagingRpc(2500)
    ing = Ingestor(store, rpc)
    _, first = asyncio.run(ing.backfill_address("A", max_sigs=1200))
    assert len(first) == 1200 and store.coverage("addr:A")["exhausted"] is False
    _, second = asyncio.run(ing.backfill_address("A", max_sigs=5000))  # resumes older history
    assert len(second) == 1300 and store.coverage("addr:A")["exhausted"] is True
    rpc.add(7)
    _, third = asyncio.run(ing.backfill_address("A", max_sigs=5000))  # only the new ones
    assert [s["signature"] for s in third] == [f"s{i:05d}" for i in range(2506, 2499, -1)]
