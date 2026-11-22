"""The audit of 22 Nov 2026, the round's area: the operator's snapshot and gate, the round, /stop, the restart notice,
Telegram and the digest. Each test pins one confirmed finding (its id in the audit's record: round#N)."""

import asyncio
import json

import pytest

from snipetech.config import Config, OperatorConfig
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


# -- round#1: whose a held coin is -----------------------------------------------------------------------------------

COIN = "Coin111111111111111111111111111111111111pump"  # a copy of the bot's
HAND = "Hand111111111111111111111111111111111111pump"  # the owner's own buy
SWAP = "Swap111111111111111111111111111111111111pump"  # a PumpSwap copy the tape never holds
OWNER_FEES = (1_000_000, 32_000_000)  # what the owner's own terminal pays (22 Nov: Axiom's 0.032 SOL tip)
BOT_BUY_FEES = (1_000_000, 199_000_000)  # the sniper's payload below: priorityFee 0.001, tipAmount 0.199


@pytest.fixture
def book(world):
    """`world` with one sniper on record, so that a hand trade can be told from the bot's by its fees."""
    st, cfg, paths = world
    st.add_oktech_sniper({"id": "s1", "external_id": "e1", "kind": "copy", "source": "x", "name": "snipetech copy",
                          "config_hash": "h", "payload": {"priorityFee": 0.001, "tipAmount": 0.199,
                                                          "autoSell": {"sellPriorityFeeSol": 0.0001,
                                                                       "sellTipAmount": 0.002}}})  # fmt: skip
    return st, cfg, paths


def trade(st, sig, ts, mint, is_buy, sol, fees, user=W):
    prio, tip = fees
    st._exec("insert into trades(sig,ev,slot,pos,ts,mint,venue,user,is_buy,quote,base,fee,fee_bps,q_after,b_after,"
             "priority,tip) values (?,0,?,0,?,?,'curve',?,?,?,?,0,0,1000,1000,?,?)",
             (sig, ts, ts, mint, user, int(is_buy), int(sol * 1e9), int(sol * 1e9), prio, tip))  # fmt: skip


def tick(st, ts):
    """Someone else's trade: the tape is current up to `ts`."""
    trade(st, f"tick{ts}", ts, "Other", True, 0.000001, (0, 0), user="someone")


def coin(mint, sol):
    return {"mint": mint, "amount": 1.0, "raw": int(sol * 1e9), "decimals": 6, "value_sol": sol, "priced": True}


def unpriced(mint):
    return {"mint": mint, "amount": 1000.0, "raw": 1_000_000_000, "decimals": 6, "value_sol": None, "priced": False}


def take(st, cfg, paths, prev, now, native, held):
    def balances(w):
        if isinstance(held, Exception):
            raise held
        return native, 0.0, list(held)

    return asyncio.run(take_snapshot(Svc(st), paths, cfg, prev=prev, now=now, services=lambda: UP, balances=balances))


def whose(snap):
    return {h["mint"]: h["whose"] for h in snap.held}


def decide_in(paths, snap, prev, now):
    """The gate's decision, with five coins held being what quiet means (only the equity lines are looked at here)."""
    from snipetech.operator.gate import decide

    paths.expected.write_text(json.dumps({"snipers_running": 0, "held": 5}))
    return decide(snap, prev, now, OperatorConfig(), paths)


def bot_lines(paths, snap, prev, now):
    return [r for r in decide_in(paths, snap, prev, now).reasons if "by the bot's own" in r]


def test_round1_a_hand_buy_minutes_before_the_round_stays_the_owners(book):
    """The coin's buy was looked for only from 5 minutes before the round that first saw it, and rounds are 15 apart:
    a hand buy 6 minutes before it read as nothing told, which counted as the bot's, and paged '+3.000 SOL of it by
    the bot's own'. The buy is looked for since the round before, which last read the wallet."""
    st, cfg, paths = book
    tick(st, 1095)
    a = take(st, cfg, paths, None, 1100.0, 10.0, [])
    trade(st, "hand", 1640, HAND, True, 3.0, OWNER_FEES)
    tick(st, 1995)
    b = take(st, cfg, paths, a, 2000.0, 6.967, [coin(HAND, 3.0)])
    assert whose(b) == {HAND: "owner"} and b.held[0]["ours"] is False
    assert not bot_lines(paths, b, a, 2000.0)


def test_round1_the_rest_of_the_owners_coin_after_a_partial_hand_sale_stays_the_owners(book):
    """No buy came after the owner's own partial sale, so the coin's owner was asked afresh, told nothing, and the rest
    of the coin counted as the bot's (+2.000 SOL paged). An answer holds while the coin stays held."""
    st, cfg, paths = book
    trade(st, "hbuy", 1000, HAND, True, 4.0, OWNER_FEES)
    tick(st, 1095)
    a = take(st, cfg, paths, None, 1100.0, 1.0, [coin(HAND, 4.0)])
    assert whose(a) == {HAND: "owner"}
    trade(st, "hsell", 1500, HAND, False, 2.0, (1_000_000, 22_000_000))
    tick(st, 1995)
    b = take(st, cfg, paths, a, 2000.0, 3.0, [coin(HAND, 2.0)])
    assert whose(b) == {HAND: "owner"} and not bot_lines(paths, b, a, 2000.0)


def test_round1_a_failed_wallet_read_keeps_the_coins_as_last_read(book):
    """One failed balance read left the snapshot with no coins, so the next round dated every coin 'first seen now':
    the owner's buy of hours ago fell outside the window and the coin became the bot's (+3.000, later -3.000). The
    coins as last read are kept (marked stale), with their answers and the time of that read."""
    st, cfg, paths = book
    trade(st, "hbuy", 1000, HAND, True, 3.0, OWNER_FEES)
    tick(st, 1095)
    a = take(st, cfg, paths, None, 1100.0, 7.0, [coin(HAND, 3.0)])
    tick(st, 1995)
    b = take(st, cfg, paths, a, 2000.0, None, RuntimeError("rpc down"))
    assert whose(b) == {HAND: "owner"} and b.held[0]["stale"] is True and b.held_ts == 1100.0
    tick(st, 12_995)
    c = take(st, cfg, paths, b, 13_000.0, 7.0, [coin(HAND, 3.0)])
    assert whose(c) == {HAND: "owner"} and c.held[0]["since"] == 1100.0 and not bot_lines(paths, c, b, 13_000.0)


def test_round1_a_coin_no_buy_brought_in_is_not_the_bots(book):
    """An airdrop, or a coin sent in, has no buy at all: 'nothing told' counted it as the bot's. It is nobody's yet
    (`whose` None) and not the bot's; a coin whose buy only the measure stage's fills hold (a PumpSwap copy past the
    tape's 30 minutes) is the bot's by that buy's fees."""
    st, cfg, paths = book
    tick(st, 1095)
    a = take(st, cfg, paths, None, 1100.0, 10.0, [])
    st.put_fill({"sig": "pswap", "ts": 1500, "wallet": W, "mint": SWAP, "side": "buy", "sol": 0.05, "tokens": 1000.0,
                 "priority_lamports": BOT_BUY_FEES[0], "tip_lamports": BOT_BUY_FEES[1], "venue": "PumpSwap"})  # fmt: skip
    tick(st, 1995)
    b = take(st, cfg, paths, a, 2000.0, 9.9, [unpriced(AIRDROP), coin(SWAP, 0.05)])
    assert whose(b) == {AIRDROP: None, SWAP: "ours"}
    assert [h["ours"] for h in b.held] == [False, True]


def test_round1_a_buy_the_tape_has_not_written_yet_is_told_by_the_next_round(book):
    """The tape runs seconds behind the chain: a copy bought just before a round is in the wallet and not yet on the
    tape. It is nobody's that round, and the next round asks again over the same window, finds the buy and says the
    bot's (an answer is only kept once it is one)."""
    st, cfg, paths = book
    tick(st, 1095)
    a = take(st, cfg, paths, None, 1100.0, 10.0, [])
    tick(st, 1993)
    b = take(st, cfg, paths, a, 2000.0, 6.8, [coin(COIN, 3.5)])
    assert whose(b) == {COIN: None}
    trade(st, "copy", 1996, COIN, True, 3.5, BOT_BUY_FEES)  # written after round B read the tape
    tick(st, 2895)
    c = take(st, cfg, paths, b, 2900.0, 6.8, [coin(COIN, 3.5)])
    assert whose(c) == {COIN: "ours"}


# -- round#0 (and server#1): a coin the tape cannot price, that is not the bot's, does not hold the loss alert back --

BOT_SELL_FEES = (1_000_000, 2_000_000)  # the sniper's sale: the buy's priority and its 0.002 sell tip


def test_round0_a_bot_loss_beside_an_unpriced_airdrop_is_told_that_round(book):
    """The equity block, the bot's own result with it, needed the whole wallet valued: one coin the tape never priced
    (the owner's airdrop, 22 Nov 03:58-09:00) switched the loss alert off while it was held. The bot's result only
    needs the bot's own coins priced; the whole move is said to be unknown."""
    st, cfg, paths = book
    tick(st, 95)
    a = take(st, cfg, paths, None, 100.0, 10.0, [])
    tick(st, 995)
    b = take(st, cfg, paths, a, 1000.0, 10.0, [unpriced(AIRDROP)])
    trade(st, "copy", 1500, COIN, True, 3.5, BOT_BUY_FEES)  # the bot's copy loses 2.2 SOL
    trade(st, "exit", 1512, COIN, False, 1.5, BOT_SELL_FEES)
    tick(st, 1895)
    c = take(st, cfg, paths, b, 1900.0, 7.8, [unpriced(AIRDROP)])
    assert c.total_equity is None
    lines = bot_lines(paths, c, b, 1900.0)
    assert len(lines) == 1 and "-2.203 SOL of it by the bot's own" in lines[0], lines
    assert lines[0].startswith("equity moved (the whole move is not known")
    tick(st, 2795)
    d = take(st, cfg, paths, c, 2800.0, 7.8, [unpriced(AIRDROP)])  # nothing new: the loss was told once
    assert not bot_lines(paths, d, c, 2800.0)


def test_round0_a_coin_of_the_bots_the_tape_has_not_priced_still_waits(book):
    """The bot's own coin without a price leaves its result unknown: the alert waits (the baseline is carried) until the
    coin is priced or sold, as before."""
    st, cfg, paths = book
    tick(st, 95)
    a = take(st, cfg, paths, None, 100.0, 10.0, [])
    trade(st, "copy", 500, COIN, True, 3.5, BOT_BUY_FEES)
    tick(st, 995)
    b = take(st, cfg, paths, a, 1000.0, 6.3, [unpriced(COIN)])
    assert whose(b) == {COIN: "ours"} and not b.bot_valued and not bot_lines(paths, b, a, 1000.0)
    assert b.ref_ours_sol == a.ref_ours_sol and b.bot_cash_since_ref == pytest.approx(-3.700005)
    trade(st, "exit", 1500, COIN, False, 1.0, BOT_SELL_FEES)
    tick(st, 1895)
    c = take(st, cfg, paths, b, 1900.0, 7.3, [])
    lines = bot_lines(paths, c, b, 1900.0)
    assert len(lines) == 1 and "-2.703 SOL of it by the bot's own" in lines[0], lines


# -- docs#2, round#6, server#1: the held-coin states count only the bot's coins --------------------------------------


def _held_snap(now, held):
    from snipetech.operator.snapshot import Snapshot

    return Snapshot(ts=now, services=dict(UP), tape_last_ts=int(now) - 5, wallet=W, native_sol=1.0, wsol=0.054,
                    held=held, snipers=[], fills=[], equity_ref=1.6, ref_ours_sol=0.0, bot_cash_since_ref=0.0,
                    bot_cash=0.0)  # fmt: skip


def _held_reasons(paths, snap, prev, now):
    from snipetech.operator.gate import decide

    return [r for r in decide(snap, prev, now, OperatorConfig(), paths).reasons if "held" in r or "unmanaged" in r]


def _watch(paths, now, hits):
    (paths.root / "watch.json").write_text(json.dumps({"ts": now - 30, "hits": hits}))


def test_docs2_the_owners_own_coin_and_an_airdrop_raise_no_held_state(tmp_path):
    """'N coin(s) held, expected M', 'held over 30 min' and 'unmanaged' counted every coin: on 22 Nov an airdrop woke 7
    operator turns (1.38 $) and the turns paged the owner about it twice, and the owner's own coin held half an hour
    does the same. Only the bot's coins are held for these states."""
    paths = OperatorPaths(tmp_path).ensure()
    now = 1_795_321_800.0
    owners = {"mint": "H8WTvo3Z", "amount": 1000.0, "value_sol": 0.5, "priced": True, "since": now - 7200,
              "whose": "owner", "ours": False}  # fmt: skip
    airdrop = unpriced(AIRDROP) | {"since": now - 3 * 3600, "whose": None, "ours": False}
    _watch(paths, now, [{"mint": AIRDROP, "reason": "not_on_tape", "stray": True}])
    s = _held_snap(now, [owners, airdrop])
    assert _held_reasons(paths, s, s, now) == []
    later = _held_snap(now + 3600, [owners, airdrop])
    assert _held_reasons(paths, later, s, now + 3600) == []
    bots = _held_snap(now, [coin(COIN, 1.0) | {"since": now - 7200, "whose": "ours", "ours": True}])
    assert _held_reasons(paths, bots, bots, now) == ["1 coin(s) held, expected 0", "coin(s) held over 30 min: 1"]


def test_server1_a_stray_counts_as_unmanaged_only_with_a_buy_of_the_bots_on_record(tmp_path):
    """The watch says a coin with no buy of ours on the tape (`stray`) once a day; the gate raised the same hit as
    'unmanaged' every hour. A stray stays out, unless a buy of the bot's is on record after all: a PumpSwap copy the tape
    never holds is a stray to the watch, and the measure stage's fills hold its buy (mark_held: `whose` "ours")."""
    paths = OperatorPaths(tmp_path).ensure()
    now = 1_795_321_800.0
    paths.expected.write_text(json.dumps({"snipers_running": 0, "held": 1}))
    _watch(paths, now, [{"mint": SWAP, "reason": "not_on_tape", "stray": True}])
    legacy = coin(SWAP, 0.05) | {"since": now - 600, "ours": True}  # a coin marked before `whose`: nothing on record
    s = _held_snap(now, [legacy])
    assert _held_reasons(paths, s, s, now) == []
    copy = coin(SWAP, 0.05) | {"since": now - 600, "whose": "ours", "ours": True}  # its buy is in the fills
    s = _held_snap(now, [copy])
    assert _held_reasons(paths, s, s, now) == ["unmanaged: 1 coin(s) nobody is selling (operator/watch.json)"]


# -- round#2: a sale is the bot's when the coin it sells is ------------------------------------------------------------

SWEEP = (1_000_000, 0)  # what /sell (solana/sell.py) pays on any coin


def test_round2_the_owner_selling_a_bot_coin_by_hand_is_no_bot_loss(book):
    """The watch and /sell's reply tell the owner to sell a stuck coin of the bot's by hand. The owner's terminal pays its
    own fees, so the sale read as the owner's: its SOL left out, the bot's coin gone, and the coin's whole worth paged as
    the bot's loss. A sale goes with its coin."""
    st, cfg, paths = book
    trade(st, "buy", 1000, COIN, True, 2.0, BOT_BUY_FEES)
    tick(st, 1095)
    a = take(st, cfg, paths, None, 1100.0, 1.0, [coin(COIN, 2.0)])
    trade(st, "hand", 1500, COIN, False, 2.0, (1_000_000, 22_000_000))
    tick(st, 1995)
    b = take(st, cfg, paths, a, 2000.0, 3.0, [])
    assert [f["ours"] for f in b.fills] == [True] and not bot_lines(paths, b, a, 2000.0)


def test_round2_sell_on_the_owners_own_coin_is_no_bot_gain(book):
    """/sell pays the sweep's fees on any coin, which are among the bot's sale fees: selling the owner's own coin with it
    put its SOL in the bot's result and paged a gain of the coin's whole worth."""
    st, cfg, paths = book
    trade(st, "hbuy", 1000, HAND, True, 2.0, OWNER_FEES)
    tick(st, 1095)
    a = take(st, cfg, paths, None, 1100.0, 1.0, [coin(HAND, 2.0)])
    trade(st, "sweep", 1500, HAND, False, 2.0, SWEEP)
    tick(st, 1995)
    b = take(st, cfg, paths, a, 2000.0, 3.0, [])
    assert [f["ours"] for f in b.fills] == [False] and not bot_lines(paths, b, a, 2000.0)


def test_round2_a_coin_bought_and_sold_between_rounds_is_told_by_its_buy(book):
    """A round trip no snapshot saw held: the sale goes with the wallet's own buy of the coin before it (its fees), and
    only with no buy on record with the sale's own fees."""
    st, cfg, paths = book
    tick(st, 1095)
    a = take(st, cfg, paths, None, 1100.0, 10.0, [])
    trade(st, "b1", 1200, COIN, True, 1.0, BOT_BUY_FEES)
    trade(st, "s1", 1300, COIN, False, 1.0, (1_000_000, 22_000_000))  # the bot's coin, sold by hand
    trade(st, "b2", 1400, HAND, True, 1.0, OWNER_FEES)
    trade(st, "s2", 1500, HAND, False, 1.0, SWEEP)  # the owner's coin, sold with /sell
    trade(st, "s3", 1600, "Gone111111111111111111111111111111111111pump", False, 0.5, SWEEP)  # no buy on record
    tick(st, 1995)
    b = take(st, cfg, paths, a, 2000.0, 9.8, [])
    assert {f["side"] + "@" + str(f["ts"]): f["ours"] for f in b.fills} == {
        "buy@1200": True,
        "sell@1300": True,
        "buy@1400": False,
        "sell@1500": False,
        "sell@1600": True,
    }
    assert set(b.fills[0]) == {"ts", "mint", "side", "sol", "ours"}  # the fills keep their shape


# -- round#11: a refused Telegram send logs no token -------------------------------------------------------------------


def test_round11_a_refused_send_logs_no_token():
    """httpx's error for a 4xx or 5xx names the request's URL, /bot<token>/sendMessage, and the send logged it whole:
    book run, the strategist and `operate oktech` print their log to the unit's journal. It logs the kind and the
    status only."""
    import httpx

    token = "123456789:AAH-fake-token-for-the-test-0123456789"
    lines: list[str] = []

    def refused(url, data):
        httpx.Response(429, request=httpx.Request("POST", url), json={"ok": False}).raise_for_status()

    assert Notifier(token, 42, post=refused, log=lines.append).send("hello") is False
    assert lines == ["[operator] telegram send failed: HTTPStatusError (HTTP 429)"]

    def down(url, data):
        raise httpx.ConnectError(f"cannot reach {url}")

    assert Notifier(token, 42, post=down, log=lines.append).send("hello") is False
    assert lines[-1] == "[operator] telegram send failed: ConnectError" and not any(token in x for x in lines)


# -- round#12: plain SOL at the coins' commitment ----------------------------------------------------------------------


def test_round12_plain_sol_is_read_at_the_coins_commitment(world):
    """getBalance went without a commitment (the node's default, finalized, about 13 s behind) while the coins and the
    wrapped SOL were read at 'confirmed': for 13 s after a trade one side of it counted twice or not at all."""
    from snipetech.operator.snapshot import rpc_balances

    st, _cfg, _paths = world
    rpc = Rpc(1_000_000_000)
    asyncio.run(rpc_balances(Svc(st, rpc), W))
    commitments = {m: p[-1].get("commitment") if isinstance(p[-1], dict) else None
                   for m, p in rpc.calls if m in ("getBalance", "getTokenAccountsByOwner")}  # fmt: skip
    assert commitments == {"getBalance": "confirmed", "getTokenAccountsByOwner": "confirmed"}


# -- round#13: the digest reports no wallet move the owner made --------------------------------------------------------


def test_round13_the_digest_reports_no_wallet_change_the_owner_made(world):
    """'wallet: X SOL (-12.143 since the last digest)' was the whole wallet's move, mostly the owner's own deposits and
    withdrawals (22 Nov 07:00, after the owner took the wallet's SOL out). The owner's rule: never report a balance change
    no trade of ours explains. The line keeps the balance and the coins held."""
    from snipetech.operator.budget import Ledger
    from snipetech.operator.digest import digest_text
    from snipetech.operator.snapshot import Snapshot

    st, _cfg, paths = world
    st.set_coverage("digest:last", {"ts": NOW - 86400, "equity": 12.2})
    snap = Snapshot(ts=NOW, native_sol=0.0027, wsol=0.0538, snipers=[], held=[])
    text = digest_text(snap, [], Ledger(paths.spend, {}), st, NOW, paths=paths)
    assert "since the last digest" not in text and "wallet: 0.057 SOL, 0 coin(s) held" in text, text
