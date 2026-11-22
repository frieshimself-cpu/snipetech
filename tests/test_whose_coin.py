"""Whose a coin in the sniper wallet is, for one run of the runner (measure/ours.RunCoins): the run's (a buy of it paid the
fees the run's snipers pay), the owner's own (their buy paid other fees, or another wallet sent it in), or not known yet.

22 Nov 2026: the owner buys, sells and sends coins by hand in the same wallet (02:40 UTC: three buys of one coin at a
6121-8175 lamport priority and a 0.027-0.052 SOL tip), and the runner sold, counted and waited on every coin there. Our
snipers' buys pay exactly their payload's priority (0.001 SOL on every one since 20 Nov) with its tip, or no tip."""

import asyncio

from snipetech.measure.ours import CHAIN_RETRY_SECS, RunCoins, sniper_marks
from snipetech.models import Trade
from snipetech.store import Store

W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
MINT = "Coin1" + "1" * 39
ACCOUNT = "Acct1" + "1" * 39
SENDER = "Sender" + "2" * 38
NOW = 1_794_045_600.0
RUN = sniper_marks([{"priorityFee": 0.001, "tipAmount": 0.199}])


def _buy(store, sig, ts, priority, tip, user=W):
    """A buy of MINT on the tape, with its transaction's priority fee and tip in lamports as the recorder writes them."""
    store.add_trades([Trade(sig=sig, ev=0, slot=int(ts) - 1_794_000_000, pos=0, ts=int(ts), mint=MINT, venue="curve",
                            user=user, is_buy=True, quote=1_000_000_000, base=1_000_000, fee=10_000_000, fee_bps=100,
                            q_after=1, b_after=1, priority=priority, tip=tip)])  # fmt: skip


def _tx(sig, payer, priority, pre, post):
    """A getTransaction (json) answer: `payer` signs, the wallet's balance of MINT goes from `pre` to `post`."""

    def bal(amount):
        return [{"accountIndex": 1, "mint": MINT, "owner": W, "uiTokenAmount": {"amount": str(amount)}}]

    return {"slot": 5, "blockTime": int(NOW), "transaction": {"signatures": [sig], "message": {
        "accountKeys": [payer, ACCOUNT], "header": {"numRequiredSignatures": 1}, "instructions": []}},
        "meta": {"fee": 5000 + priority, "err": None, "innerInstructions": [],
                 "preTokenBalances": bal(pre) if pre else [], "postTokenBalances": bal(post)}}  # fmt: skip


LEARNED = "Learned" + "3" * 37  # a tip account the recorder learned from amounts, outside the fixed list


def _tx_tipped(sig, payer, priority, tip_to, lamports):
    """A getTransaction answer as `_tx` makes it, and a transfer of `lamports` from the payer to `tip_to`."""
    from snipetech.solana.base58 import b58encode

    tx = _tx(sig, payer, priority, 0, 1_000_000)
    tx["transaction"]["message"]["accountKeys"] += [tip_to, "11111111111111111111111111111111"]
    data = (2).to_bytes(4, "little") + int(lamports).to_bytes(8, "little")
    tx["transaction"]["message"]["instructions"] = [{"programIdIndex": 3, "accounts": [0, 2], "data": b58encode(data)}]
    return tx


class _Rpc:
    """The chain as the runner's RPC reads it: the token account's signatures, newest first, and each transaction."""

    def __init__(self, txs, fail=False):
        self.txs, self.fail, self.reads = txs, fail, 0

    async def get_signatures(self, address, limit=1000, **_kw):
        self.reads += 1
        if self.fail:
            raise RuntimeError("node refused")
        assert address == ACCOUNT
        return [{"signature": s, "err": None} for s in self.txs][:limit]

    async def get_transaction(self, sig):
        return self.txs[sig]


def _coin(account=ACCOUNT):
    coin = {"mint": MINT, "raw": 1_000_000, "value_sol": 1.0, "priced": True}
    return coin | ({"account": account} if account else {})


def _whose(coins, since=NOW - 30, now=NOW):
    return asyncio.run(coins.whose(_coin(), since, now))


def test_a_buy_that_paid_the_runs_fees_is_the_runs():
    store = Store(":memory:")
    _buy(store, "ours", NOW - 3, 1_000_000, 199_000_000)
    assert _whose(RunCoins(store, None, W, RUN)) == "ours"


def test_a_buy_with_the_runs_priority_and_no_tip_is_the_runs_too():
    """22 Nov 00:19:12 a copy went out with no tip: the same priority alone is one of the run's sends."""
    store = Store(":memory:")
    _buy(store, "untipped", NOW - 3, 1_000_000, 0)
    assert _whose(RunCoins(store, None, W, RUN)) == "ours"


def test_a_hand_buy_is_the_owners():
    store = Store(":memory:")
    _buy(store, "hand", NOW - 3, 6121, 26_993_879)
    assert _whose(RunCoins(store, None, W, RUN)) == "owner"


def test_a_buy_with_no_fee_recorded_counts_as_the_runs():
    """Nothing tells a hand trade apart: the coin is the run's, as before."""
    store = Store(":memory:")
    _buy(store, "unknown", NOW - 3, None, None)
    assert _whose(RunCoins(store, None, W, RUN)) == "ours"


def test_with_no_fee_on_record_every_coin_is_the_runs():
    store = Store(":memory:")
    _buy(store, "hand", NOW - 3, 6121, 26_993_879)
    coins = RunCoins(store, None, W, sniper_marks([{"singleSnipe": True}]))
    assert _whose(coins) == "ours" and coins.known(MINT) == "ours"


def test_only_a_buy_since_the_coin_showed_decides_while_the_run_is_on():
    """An old buy of ours in the same coin says nothing about a holding that began now: the tape lags the wallet by
    seconds, so the owner's new buy may not be on it yet. Nothing is decided until it is."""
    store = Store(":memory:")
    _buy(store, "old-ours", NOW - 3600, 1_000_000, 199_000_000)
    coins = RunCoins(store, None, W, RUN)
    assert _whose(coins) is None and coins.known(MINT) is None
    _buy(store, "hand", NOW + 2, 6121, 26_993_879)
    assert _whose(coins, now=NOW + 5) == "owner"


def test_a_buy_of_ours_sold_since_says_nothing_about_the_next_holding():
    """Our copy bought a coin and sold it 12 s later; the owner buys the same coin by hand a minute on, before the
    recorder has written that buy: the buy before our sale is no buy of the holding that began after it."""
    store = Store(":memory:")
    _buy(store, "ours", NOW - 70, 1_000_000, 199_000_000)
    store.add_trades([Trade(sig="our-sale", ev=0, slot=int(NOW - 58) - 1_794_000_000, pos=0, ts=int(NOW - 58),
                            mint=MINT, venue="curve", user=W, is_buy=False, quote=1_100_000_000, base=1_000_000,
                            fee=11_000_000, fee_bps=100, q_after=1, b_after=1, priority=1_000_000,
                            tip=2_000_000)])  # fmt: skip
    coins = RunCoins(store, None, W, RUN)
    assert _whose(coins, since=NOW - 300) is None
    _buy(store, "hand", NOW - 2, 6121, 26_993_879)
    assert _whose(coins, since=NOW - 300, now=NOW + 5) == "owner"


def test_the_newest_buy_decides_for_a_coin_held_before_the_run():
    store = Store(":memory:")
    _buy(store, "old-ours", NOW - 7200, 1_000_000, 199_000_000)
    _buy(store, "hand", NOW - 3600, 8175, 51_991_825)
    assert _whose(RunCoins(store, None, W, RUN), since=0) == "owner"


def test_the_chain_tells_when_the_tape_has_no_buy():
    """A coin bought where the recorder does not look (another venue, a PumpSwap coin migrated long ago) or in a hole of
    the tape: the token account's newest transaction that brought the coin in, and what it paid."""
    store = Store(":memory:")
    hand = {"sale": _tx("sale", W, 1_000_000, 2_000_000, 1_000_000), "buy": _tx("buy", W, 6121, 0, 2_000_000)}
    assert _whose(RunCoins(store, _Rpc(hand), W, RUN)) == "owner"
    ours = {"buy": _tx("buy", W, 1_000_000, 0, 1_000_000)}
    assert _whose(RunCoins(store, _Rpc(ours), W, RUN)) == "ours"


def test_a_coin_another_wallet_sent_in_is_the_owners():
    store = Store(":memory:")
    sent = {"transfer": _tx("transfer", SENDER, 1_000_000, 0, 1_000_000)}
    assert _whose(RunCoins(store, _Rpc(sent), W, RUN)) == "owner"


def test_a_chain_read_that_fails_is_tried_again_a_while_later():
    store = Store(":memory:")
    rpc = _Rpc({}, fail=True)
    coins = RunCoins(store, rpc, W, RUN)
    assert _whose(coins) is None and rpc.reads == 1
    assert _whose(coins, now=NOW + 5) is None and rpc.reads == 1  # not every reading
    rpc.fail, rpc.txs = False, {"buy": _tx("buy", W, 6121, 0, 1_000_000)}
    assert _whose(coins, now=NOW + CHAIN_RETRY_SECS) == "owner" and rpc.reads == 2


def test_a_chain_read_that_never_answers_tells_nothing_yet(monkeypatch):
    """A node that never answers held up the runner's whole reading (22 Nov audit, finding 10): the chain read has a
    limit, and past it nothing is told yet (read again CHAIN_RETRY_SECS later)."""
    import snipetech.measure.ours as O

    class Hung(_Rpc):
        async def get_signatures(self, address, limit=1000, **_kw):
            self.reads += 1
            await asyncio.Event().wait()

    monkeypatch.setattr(O, "CHAIN_READ_SECS", 0.05)
    rpc = Hung({})
    assert _whose(RunCoins(Store(":memory:"), rpc, W, RUN)) is None and rpc.reads == 1


def test_the_chain_read_counts_a_tip_paid_to_a_learned_account(monkeypatch):
    """Review of 22 Nov: the chain read knew only the fixed list of landing services' tip accounts, while the tape reads
    a trade's tip with the accounts the recorder learned too. A hand buy at our snipers' priority (0.001 SOL) whose tip
    went to a learned account read as untipped, one of the run's own marks: the run would have sold the owner's coin."""
    from tests.test_tip_learned import applied

    store = Store(":memory:")
    applied(store, LEARNED)
    hand = {"hand": _tx_tipped("hand", W, 1_000_000, LEARNED, 30_000_000)}
    assert _whose(RunCoins(store, _Rpc(hand), W, RUN)) == "owner"
    ours = {"ours": _tx_tipped("ours", W, 1_000_000, LEARNED, 199_000_000)}  # the run's own tip, to the same account
    assert _whose(RunCoins(store, _Rpc(ours), W, RUN)) == "ours"


def test_a_newest_transaction_the_node_cannot_serve_yet_tells_nothing(monkeypatch):
    """Review of 22 Nov: the chain read skipped a transaction whose answer was null (the node had not served it yet)
    as if it brought nothing in, and took an older holding's buy: the owner's new buy read as the run's after an old
    copy, and the run swept it. Nothing is told until the newest one can be read; older ones before the holding are
    never read as its buy."""
    store = Store(":memory:")
    old = _tx("old", W, 1_000_000, 0, 1_000_000)  # the run's copy of the same coin an hour before
    old["blockTime"] = int(NOW) - 3600
    rpc = _Rpc({"new": None, "old": old})
    coins = RunCoins(store, rpc, W, RUN)
    assert _whose(coins) is None
    rpc.txs = {"old": old}  # the newest is gone from the list: what is left is from before the holding began
    assert _whose(coins, now=NOW + CHAIN_RETRY_SECS) is None


def test_a_coin_with_no_token_account_known_waits_for_the_tape():
    store = Store(":memory:")
    coins = RunCoins(store, _Rpc({"buy": _tx("buy", W, 6121, 0, 1_000_000)}), W, RUN)
    assert asyncio.run(coins.whose(_coin(account=None), NOW - 30, NOW)) is None


def test_a_holding_that_ends_is_forgotten_and_the_next_one_decided_afresh():
    store = Store(":memory:")
    _buy(store, "hand", NOW - 3, 6121, 26_993_879)
    coins = RunCoins(store, None, W, RUN)
    assert _whose(coins) == "owner"
    coins.forget(MINT)
    _buy(store, "ours", NOW + 597, 1_000_000, 199_000_000)
    assert _whose(coins, since=NOW + 570, now=NOW + 600) == "ours"


def test_a_sale_pays_the_buys_priority_so_that_is_a_sale_mark_too():
    """OKTech's sales paid 0.001 SOL priority and their 0.002 tip under a sellPriorityFeeSol of 0.0001 (fills since
    20 Nov); the run's own fallback sale pays 0.001 SOL untipped."""
    marks = sniper_marks([{"priorityFee": 0.001, "tipAmount": 0.199,
                           "autoSell": {"sellPriorityFeeSol": 0.0001, "sellTipAmount": 0.002}}])  # fmt: skip
    assert {(1_000_000, 2_000_000), (100_000, 2_000_000), (1_000_000, 0)} <= marks["sell"]
    assert marks["buy"] == {(1_000_000, 199_000_000), (1_000_000, 0)}
