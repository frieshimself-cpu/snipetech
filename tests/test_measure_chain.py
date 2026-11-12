"""The measure stage places a buy the tape cannot place from the chain (12 Nov 2026).

17:04-17:06 UTC: two copies of Cupsey bought a coin the recorder does not hold (not on a pump.fun curve, not a coin
that left one in the last 30 minutes). Measure builds its fills from our trades on the tape, so the two buys were in
no row at all; the only row it wrote was a failed duplicate sell. Now every landed transaction of the wallet that the
tape does not hold is read off the chain: a buy and a sell become fills, and a buy gets its landing (the copied
wallet, the buyers in between and their bids) from the blocks themselves. The path tested is the one
`measure backfill --since ... --until ...` takes on the server: nothing injected but the RPC."""

import asyncio
import json

import pytest

from snipetech.config import Config
from snipetech.measure.run import backfill, run_measure
from snipetech.service import Service
from snipetech.solana.txmeta import TxMeta
from snipetech.store import Store

from .chain import Chain, key, noise, sig, swap
from .synth import Market, addr
from .test_chain_landing import COIN, FAST, LEAD, MATE, ME2, RICH, S, _busy_block

ME = key("our wallet")
T0 = 1_794_502_800  # 12 Nov 2026 17:00 UTC: the window of the server's backfill starts here


def _svc(tmp_path, chain, mates=True):
    st = Store(tmp_path / "t.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = ME
    svc = Service(cfg, store=st)
    svc._rpc = chain
    st.set_label(ME2, "the second sniper wallet", "own")
    if mates:
        _tape_knows_the_companion(st)
    return svc, st, cfg


def _tape_knows_the_companion(st):
    """The copied wallet's companion buys in the very next transaction of its block, coin after coin, on the tape."""
    for k in range(6):
        m = Market(st, addr(f"mate-coin-{k}"), addr(f"mate-dev-{k}"), T0 - 4000 + k * 60, S - 20_000 + k * 200)
        m.buy(m.deployer, 0.5, 0)
        a = m.buy(LEAD, 1.0, 10)
        b = m.buy(MATE, 1.0, 10)
        a.pos, b.pos = 50, 51
        m.commit()
    st.set_coverage("tape", {"spans": [[T0 - 5000, T0 + 2000]]})


def _copy_sniper(st, leader=LEAD, created=T0 - 600):
    st.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": leader,
                          "name": f"snipetech copy Cupseyy {leader[:6]}", "config_hash": "h", "created_at": created,
                          "payload": {"ctbotWallets": [{"address": leader}], "priorityFee": 0.005, "tipAmount": 0.03,
                                      "autoSell": {"sellTipAmount": 0.001, "sellPriorityFeeSol": 0.0001}}})  # fmt: skip


def _ladder_chain() -> tuple[Chain, str]:
    """Our copy of the busy block (tests/test_chain_landing.py), sold 20 s later; OKTech's duplicate of the sell
    fails a slot after it and keeps the buy's priority fee (0.005 SOL)."""
    chain = Chain(t0=T0 + 240, slot0=S)
    ours = _busy_block(chain)
    chain.put(S + 50, swap("sell", ME, COIN, tokens=-38_000_000, sol=0.04, priority=0.005, tip=0.001, held=38_000_000))
    chain.put(S + 51, swap("twin", ME, COIN, tokens=-38_000_000, sol=0.04, priority=0.005, tip=0.001,
                           err={"InstructionError": [3, {"Custom": 6023}]}))  # fmt: skip
    chain.put(S + 60, noise("a transfer of ours", ME))
    return chain, ours


def test_a_backfill_writes_the_copy_of_a_coin_the_tape_does_not_hold(tmp_path):
    chain, ours = _ladder_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    try:
        _copy_sniper(st)
        out = asyncio.run(backfill(svc, cfg, T0, T0 + 1200))
        assert out["buys"] == 1 and out["sells"] == 1 and out["failed"] == 1 and out["trips"] == 1
        assert out["from_chain"] == {"buys": 1, "sells": 1, "placed": 1}
        buy = st.fill(ours)
        assert buy["side"] == "buy" and buy["wallet"] == ME and buy["mint"] == COIN and buy["ts"] == chain.ts(S)
        assert buy["sol"] == pytest.approx(0.05) and buy["tokens"] == 38_000_000 and buy["slot"] == S
        assert buy["priority_lamports"] == 5_000_000 and buy["tip_lamports"] == 30_000_000
        assert buy["landing_service"] == "nozomi" and buy["cu_limit"] == 100_000
        # where it landed: from the blocks, and said so
        assert buy["landing_src"] == "chain" and buy["wallet_copied"] == LEAD
        assert buy["trades_behind"] == 2 and buy["blocks_after"] == 0
        assert buy["price_gap_pct"] == pytest.approx(31.6, abs=0.05)
        assert buy["ahead_bid_max"] == 350_000_000 and buy["ahead_bid_p50"] == 175_550_000
        assert [(b["w"], b["bid"], b["vs"]) for b in json.loads(buy["ahead_bids"])] == [
            (RICH, 350_000_000, "more"), (FAST, 1_100_000, "less")]  # fmt: skip
        assert buy["landing_verdict"] == "speed" and buy["venue"] == f"other:{key('some amm')}"
        assert buy["leader_progress"] is None and buy["pos_in_slot"] is None
        # the sell is a fill too, paired with the buy: the trip's whole result, its failed duplicate in it once
        sell = st.fill(sig("sell"))
        assert sell["side"] == "sell" and sell["sol"] == pytest.approx(0.04) and sell["buy_sig"] == ours
        assert sell["pnl_sol"] == pytest.approx(0.04 - 0.05 - 0.035005 - 0.006005, abs=1e-6)
        assert sell["twin_fee_sol"] == pytest.approx(0.005005)
        twin = st.fill(sig("twin"))
        assert twin["side"] == "failed" and twin["mint"] == COIN and twin["priority_lamports"] == 5_000_000
        assert twin["venue"] == f"other:{key('some amm')}"
        assert st.fill(sig("a transfer of ours")) is None  # no trade: no fill
        # the bid model and the landing table see the chain's numbers like the tape's
        arm = out["arms"][buy["bid_arm"]]
        assert arm["n"] == 1 and arm["mean_gap_pct"] == pytest.approx(31.6, abs=0.05)
        # a second pass over the same window changes nothing and does not read the blocks again
        blocks = len([c for c in chain.calls if c[0] == "getBlock"])
        out2 = asyncio.run(run_measure(svc, cfg, T0 + 1300, since_ts=T0, until_ts=T0 + 1200, move_cursor=False))
        assert out2["from_chain"] == {"buys": 0, "sells": 0, "placed": 0} and len(st.fills()) == 3
        assert len([c for c in chain.calls if c[0] == "getBlock"]) == blocks
        assert st.fill(ours)["trades_behind"] == 2 and st.fill(sig("sell"))["pnl_sol"] == sell["pnl_sol"]
    finally:
        st.close()


def test_a_buy_that_copied_nobody_is_still_a_fill(tmp_path):
    """No copy sniper alive: the buy on a coin off the tape is a fill with what it cost and bid, and no landing."""
    chain, ours = _ladder_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    try:
        out = asyncio.run(backfill(svc, cfg, T0, T0 + 1200))
        buy = st.fill(ours)
        assert out["buys"] == 1 and buy["sol"] == pytest.approx(0.05) and buy["priority_lamports"] == 5_000_000
        assert buy["wallet_copied"] is None and buy["trades_behind"] is None and buy["landing_src"] is None
        assert buy["venue"] == f"other:{key('some amm')}"
        assert not [c for c in chain.calls if c[0] == "getBlock"]
    finally:
        st.close()


def test_a_copy_whose_copied_wallet_bought_no_such_coin_is_marked_as_looked_at(tmp_path):
    chain, ours = _ladder_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    try:
        _copy_sniper(st, leader=key("a wallet that never bought this coin"))
        asyncio.run(backfill(svc, cfg, T0, T0 + 1200))
        buy = st.fill(ours)
        assert buy["landing_src"] == "chain-none" and buy["wallet_copied"] is None and buy["trades_behind"] is None
    finally:
        st.close()


def test_a_coin_on_the_tape_whose_copied_buy_the_tape_missed_is_placed_from_the_chain(tmp_path):
    """The coin is on the tape and our buy is, but the copied wallet's buy is not (the recorder stalled, or it bought
    on a venue the tape does not hold): the tape gives no landing, the chain does; the tape keeps the size."""
    lead, me = addr("tape-gap-lead"), addr("tape-gap-me")
    chain = Chain(t0=T0 + 240, slot0=S)
    st = Store(tmp_path / "t.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = me
    svc = Service(cfg, store=st)
    svc._rpc = chain
    try:
        st.add_oktech_sniper({"id": "cs2", "external_id": "cs2", "kind": "copy", "source": lead, "name": "copy",
                              "config_hash": "h", "payload": {"ctbotWallets": [{"address": lead}]},
                              "created_at": T0 - 600})  # fmt: skip
        m = Market(st, addr("tape-gap-coin"), addr("tape-gap-dev"), chain.ts(S) - 2, S - 5)
        m.buy(m.deployer, 1.0, 0)
        m.buy(addr("tape-gap-other"), 0.5, 5)
        mine = m.buy(me, 0.2, 5)
        m.commit()
        # the same block on the chain: the copied wallet's buy, one outsider, ours (under the tape's own signature)
        chain.put(S, swap("gap-lead", lead, m.mint, tokens=3_000_000_000, sol=3.0, priority=0.002))
        chain.put(S, swap("gap-other", addr("tape-gap-other"), m.mint, tokens=500_000_000, sol=0.5, priority=0.2))
        ours = swap("gap-ours", me, m.mint, tokens=190_000_000, sol=0.2, priority=0.005, tip=0.03)
        ours["transaction"]["signatures"] = [mine.sig]
        chain.put(S, ours)
        meta = TxMeta(sig=mine.sig, slot=S, block_time=0, version=0, fee_payer=me, signers=[me], n_signatures=1,
                      fee=5_005_000, priority_lamports=5_000_000, cu_limit=100_000, cu_price=1, cu_consumed=60_000,
                      tips=[{"dest": "T", "lamports": 30_000_000, "service": "nozomi"}])  # fmt: skip
        st.add_tx_meta([meta])
        out = asyncio.run(run_measure(svc, cfg, chain.ts(S) + 60, since_ts=T0, move_cursor=False))
        buy = st.fill(mine.sig)
        assert out["from_chain"] == {"buys": 0, "sells": 0, "placed": 1}
        assert buy["landing_src"] == "chain" and buy["wallet_copied"] == lead and buy["trades_behind"] == 1
        assert buy["ahead_bid_max"] == 200_000_000 and buy["landing_verdict"] == "fees"
        assert buy["sol"] == pytest.approx(0.2025, abs=1e-4) and buy["pos_in_slot"] is not None  # the tape's own
    finally:
        st.close()


def test_a_buy_the_tape_places_says_so(tmp_path):
    lead, me = addr("tape-ok-lead"), addr("tape-ok-me")
    st = Store(tmp_path / "t.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = me
    svc = Service(cfg, store=st)
    svc._rpc = Chain(t0=T0, slot0=S)
    try:
        st.add_oktech_sniper({"id": "cs3", "external_id": "cs3", "kind": "copy", "source": lead, "name": "copy",
                              "config_hash": "h", "payload": {"ctbotWallets": [{"address": lead}]},
                              "created_at": T0 - 600})  # fmt: skip
        m = Market(st, addr("tape-ok-coin"), addr("tape-ok-dev"), T0 + 100, S)
        m.buy(m.deployer, 1.0, 0)
        m.buy(lead, 1.0, 2)
        m.buy(addr("tape-ok-other"), 0.5, 3)
        mine = m.buy(me, 0.2, 4)
        m.commit()
        asyncio.run(run_measure(svc, cfg, T0 + 300, since_ts=T0, move_cursor=False))
        buy = st.fill(mine.sig)
        assert buy["landing_src"] == "tape" and buy["trades_behind"] == 1 and buy["blocks_after"] is None
        assert not [c for c in svc._rpc.calls if c[0] == "getBlock"]
    finally:
        st.close()


def test_a_tape_row_that_arrives_after_the_chain_reading_keeps_the_chains_landing(tmp_path):
    """The recorder is seconds behind: a fill read from the chain first, then found on the tape with no copied buy
    beside it, keeps where the chain placed it."""
    chain, ours = _ladder_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    try:
        _copy_sniper(st)
        asyncio.run(backfill(svc, cfg, T0, T0 + 1200))
        m = Market(st, COIN, addr("late-dev"), chain.ts(S) - 2, S - 5)
        m.buy(m.deployer, 1.0, 0)
        t = m.buy(ME, 0.05, 5)
        t.sig = ours
        m.commit()
        asyncio.run(run_measure(svc, cfg, T0 + 1300, since_ts=T0, until_ts=T0 + 1200, move_cursor=False))
        buy = st.fill(ours)
        assert buy["landing_src"] == "chain" and buy["trades_behind"] == 2 and buy["wallet_copied"] == LEAD
        assert buy["ahead_bid_max"] == 350_000_000 and buy["pos_in_slot"] is not None  # its place from the tape now
    finally:
        st.close()


def test_companions_the_tape_does_not_know_count_as_buyers(tmp_path):
    """The tape holds none of the copied wallet's buys, so nobody is known to buy with it: the wallet that bought
    right behind it is counted with the outsiders (the one-off `landing` reading says the companions are unknown)."""
    chain, ours = _ladder_chain()
    svc, st, cfg = _svc(tmp_path, chain, mates=False)
    try:
        _copy_sniper(st)
        asyncio.run(backfill(svc, cfg, T0, T0 + 1200))
        buy = st.fill(ours)
        assert buy["trades_behind"] == 3 and [b["w"] for b in json.loads(buy["ahead_bids"])] == [MATE, RICH, FAST]
    finally:
        st.close()
