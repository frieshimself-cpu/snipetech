"""The audit of 22 Nov 2026, the round's area: the operator's snapshot and gate, the round, /stop, the restart notice,
Telegram and the digest. Each test pins one confirmed finding (its id in the audit's record: round#N)."""

import asyncio

import pytest

from snipetech.config import Config
from snipetech.operator.commands import Command, handle_command
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import take_snapshot
from snipetech.operator.tools import NoteSink
from snipetech.positions import filter_positions
from snipetech.solana.programs import TOKEN_PROGRAM
from snipetech.store import Store

W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
AIRDROP = "G1PpheQUoGjaqNKggwoVQVXm13u4yqbA6jWyms54RL7"  # 22 Nov 03:58-09:00: 1,000 tokens, no buy, no price
NOW = 1_795_320_000.0
UP = {"snipetech-tape": "active", "snipetech-ops": "active"}


class Rpc:
    """The wallet as the node answers: plain SOL, and one token account of each coin in `coins` (mint -> raw)."""

    def __init__(self, lamports: int, coins: dict[str, int] | None = None, sigs: list[dict] | None = None):
        self.lamports, self.coins, self.sigs, self.calls = lamports, dict(coins or {}), list(sigs or []), []

    async def call(self, method, params=None, final=frozenset()):
        self.calls.append((method, params))
        if method == "getBalance":
            return {"value": self.lamports}
        if method == "getTokenAccountsByOwner":
            if params[1]["programId"] != TOKEN_PROGRAM:
                return {"value": []}
            return {"value": [{"pubkey": f"acct-{m[:4]}", "account": {"data": {"parsed": {"info": {
                "mint": m, "tokenAmount": {"uiAmount": raw / 1e6, "amount": str(raw), "decimals": 6}}}}}}
                for m, raw in self.coins.items()]}  # fmt: skip
        if method == "getSignaturesForAddress":
            return self.sigs
        raise AssertionError(method)


class Svc:
    def __init__(self, store, rpc=None):
        self.store, self.rpc = store, rpc

    async def oktech_status(self):
        return {"snipers": []}


@pytest.fixture
def world(tmp_path):
    st = Store(tmp_path / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    cfg.operator.wallet = W
    yield st, cfg, OperatorPaths(tmp_path).ensure()
    st.close()


# -- round#5: the owner's ignore list reaches the round's snapshot and /stop -----------------------------------------


def test_round5_a_coin_on_the_ignore_list_is_not_held_in_the_snapshot(world):
    """The watch tells the owner to put a stray coin on [operator] ignore_mints, and every other reader of the wallet
    leaves those coins out. The round's snapshot kept it: still '1 coin(s) held' every hour, and the wallet unvalued
    while it had no price (22 Nov: the G1Pphe airdrop)."""
    st, cfg, paths = world
    cfg.operator.ignore_mints = [AIRDROP]
    rpc = Rpc(2_000_000_000, {AIRDROP: 1_000_000_000})
    snap = asyncio.run(take_snapshot(Svc(st, rpc), paths, cfg, prev=None, now=NOW, services=lambda: UP))
    assert snap.held == [] and snap.total_equity == pytest.approx(2.0)
    cfg.operator.ignore_mints = []  # off the list it is held again
    snap = asyncio.run(take_snapshot(Svc(st, rpc), paths, cfg, prev=None, now=NOW, services=lambda: UP))
    assert [h["mint"] for h in snap.held] == [AIRDROP] and snap.total_equity is None


class StopSvc:
    def __init__(self):
        self.calls = []

    async def open_positions(self, address, ignore=None, unknown=None):
        accounts = [{"mint": AIRDROP, "amount": 1000.0, "raw": 1_000_000_000, "decimals": 6}]
        return filter_positions(accounts, lambda m: None, ignore=tuple(ignore or ()))

    async def oktech_status(self):
        return {"snipers": [{"id": "s1", "name": "snipetech copy x", "status": "running"}]}

    async def oktech_task(self, action, task_id):
        self.calls.append((action, task_id))


def _stop(tmp_path, svc, ignore):
    paths = OperatorPaths(tmp_path / "data").ensure()
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    cfg.operator.wallet = W
    cfg.operator.ignore_mints = list(ignore)
    notifier = Notifier("T", 7, post=lambda url, data: None)
    return asyncio.run(handle_command(Command("stop", [], 7, 1, date=int(NOW)), svc=svc, cfg=cfg, paths=paths,
                                      snap=None, notifier=notifier, notes=NoteSink(paths, now=lambda: NOW), now=NOW))  # fmt: skip


def test_round5_stop_stops_a_sniper_when_only_an_ignored_coin_is_held(tmp_path):
    """/stop left every running sniper running 'because coins are held' when the wallet held only a coin the owner had
    put on the ignore list: no sniper manages such a coin (the runner, the book and the guard leave it out)."""
    svc = StopSvc()
    reply = _stop(tmp_path, svc, [AIRDROP])
    assert svc.calls == [("stop", "s1")] and reply.startswith("stopped 1 sniper(s)"), reply
    kept = StopSvc()
    reply = _stop(tmp_path / "again", kept, [])  # a coin not on the list still keeps the sniper that may sell it
    assert kept.calls == [] and "because coins are held" in reply
