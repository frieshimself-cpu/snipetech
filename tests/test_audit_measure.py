"""The 22 Nov 2026 audit of the measure stage: each test pins one confirmed finding (its id in the test's name and
docstring). The sniper wallet is the owner's too: the owner buys, sells and sends coins out of it by hand, and only
the bot's own trades are the bot's result (measure/ours.py)."""

import asyncio

import pytest

from snipetech.book.state import realised_today
from snipetech.config import Config
from snipetech.measure.run import run_measure
from snipetech.operator import risk_guard as RG
from snipetech.service import Service
from snipetech.solana.txmeta import TxMeta
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_794_045_600  # 7 Nov 2026 10:00 UTC
SLOT = 400_000_000
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"  # the sniper wallet
LEAD, D = addr("audit-lead"), addr("audit-dev")
# the fees of the owner's own hand buys of 22 Nov 02:40 (Axiom): no sniper pays them
OWNER_FEES = (8175, 51_991_825)
BOT_BUY_FEES, BOT_SALE_FEES = (1_000_000, 199_000_000), (1_000_000, 2_000_000)


def _meta(sig, fees):
    priority, tip = fees
    return TxMeta(sig=sig, slot=0, block_time=0, version=0, fee_payer=W, signers=[W], n_signatures=1,
                  fee=5000 + priority, priority_lamports=priority, cu_limit=200_000, cu_price=5000,
                  cu_consumed=60_000,
                  tips=[{"dest": "T", "lamports": tip, "service": "jito"}] if tip else [])  # fmt: skip


def _sniper(st, leader=LEAD, created=NOW - 86400, tip=0.199):
    """A copy sniper of `leader` on record with the payload's fees (0.001 priority, `tip`; a sale 0.0001 + 0.002)."""
    st.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": leader, "name": "copy lead",
                          "config_hash": "h", "created_at": created,
                          "payload": {"ctbotWallets": [{"address": leader}], "priorityFee": 0.001, "tipAmount": tip,
                                      "autoSell": {"sellPriorityFeeSol": 0.0001, "sellTipAmount": 0.002}}})  # fmt: skip


def _svc(tmp_path):
    st = Store(tmp_path / "t.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = W
    return Service(cfg, store=st), st, cfg


def _measure(svc, cfg, metas, now=NOW, since=NOW - 7200):
    st = svc.store

    async def ensure(sigs):
        st.add_tx_meta([metas[s] for s in sigs if s in metas])
        return len(sigs)

    async def no_failed(wallet, since_ts):
        return []

    out = asyncio.run(run_measure(svc, cfg, now, since_ts=since, ensure_meta=ensure, failed_sigs=no_failed))
    st.set_coverage("measure:last_ts", {"ts": now})
    return out


def _trip(buy, sale):
    """What one round trip made: its sale less its buy, less both sides' fees (the payloads' fees)."""
    got = (sale.quote - sale.fee) / 1e9
    paid = (buy.quote + buy.fee) / 1e9
    return got - paid - (sum(BOT_BUY_FEES) + 5000) / 1e9 - (sum(BOT_SALE_FEES) + 5000) / 1e9


# -- measure#0: a sniper's sale never closes the owner's older lot of the same coin ----------------------------------


def _owner_then_bot(tmp_path, owner: str):
    """The owner holds a coin bought by hand (`owner`: "holds", "sells" it by hand later, or "moved" it out of the
    wallet, which is no trade); a copied wallet buys it, our sniper copies it and sells its own coins 12 s later."""
    svc, st, cfg = _svc(tmp_path)
    _sniper(st)
    m = Market(st, addr("audit-coin"), D, NOW - 3000, SLOT)
    m.buy(D, 1.0, 0)
    m.buy(W, 2.0, 10, sig="own-buy")  # the owner's hand buy
    if owner == "moved":
        m.holdings[W] = 0  # the owner sent the coins to another wallet: no trade on the tape
    m.buy(LEAD, 1.0, 100)
    bot = m.buy(W, 1.0, 101, sig="bot-buy")  # our copy
    m.buy(addr("audit-x"), 12.0, 105)  # the price goes up
    bot_sale = m.sell(W, bot.base / m.holdings[W], 131)  # the sniper sells its own coins
    metas = {"own-buy": _meta("own-buy", OWNER_FEES), "bot-buy": _meta("bot-buy", BOT_BUY_FEES),
             bot_sale.sig: _meta(bot_sale.sig, BOT_SALE_FEES)}  # fmt: skip
    owner_sale = None
    if owner == "sells":
        owner_sale = m.sell(W, 1.0, 400)  # the owner sells the rest by hand
        metas[owner_sale.sig] = _meta(owner_sale.sig, (OWNER_FEES[0], 0))
    m.commit()
    _measure(svc, cfg, metas)
    return st, bot, bot_sale, owner_sale


@pytest.mark.parametrize("owner", ["holds", "sells", "moved"])
def test_measure_0_the_snipers_sale_closes_the_bots_own_lot(tmp_path, owner):
    """measure#0: the measure stage paired every sale of the wallet first in first out over every buy, whoever made it,
    so the sniper's sale closed the owner's OLDER lot: its buy_sig was the owner's, the day stop (realised_today) left
    the bot's trip out, and the guard (risk_guard.day_result) kept the bot's buy open, valued on the owner's coins or,
    once they were gone, lost in full. A sale closes the lots of its own maker first (the bot's or the owner's)."""
    st, bot, bot_sale, owner_sale = _owner_then_bot(tmp_path, owner)
    try:
        sale = st.fill(bot_sale.sig)
        assert sale["buy_sig"] == "bot-buy"
        want = _trip(bot, bot_sale)
        assert sale["pnl_sol"] == pytest.approx(want, abs=1e-5)
        assert realised_today(st, W, NOW) == pytest.approx(want, abs=1e-5)
        day = RG.day_result(st, W, NOW, [], per_tx_sol=0.0035)  # the wallet holds none of the coin any more
        assert day["total_sol"] == pytest.approx(want, abs=1e-5)
        if owner_sale is not None:  # the owner's hand sale closes the owner's own lot, and is no result of the bot's
            assert st.fill(owner_sale.sig)["buy_sig"] == "own-buy"
    finally:
        st.close()


def test_measure_0_a_hand_sale_of_a_coin_only_the_bot_bought_stays_the_bots_manual_exit(tmp_path):
    """measure#0: a sale by hand closes the owner's own lots first, and a coin only the bot bought is the bot's
    whoever sells it (5-7 Nov: nine hand sales of the bot's own buys, its 'manual' exits)."""
    svc, st, cfg = _svc(tmp_path)
    try:
        _sniper(st)
        m = Market(st, addr("audit-coin2"), D, NOW - 3000, SLOT)
        m.buy(D, 1.0, 0)
        m.buy(LEAD, 1.0, 100)
        bot = m.buy(W, 1.0, 101, sig="bot-buy2")
        m.buy(addr("audit-y"), 3.0, 105)
        hand_sale = m.sell(W, 1.0, 140)
        m.commit()
        metas = {"bot-buy2": _meta("bot-buy2", BOT_BUY_FEES), hand_sale.sig: _meta(hand_sale.sig, (OWNER_FEES[0], 0))}
        _measure(svc, cfg, metas)
        sale = st.fill(hand_sale.sig)
        assert sale["buy_sig"] == "bot-buy2" and sale["exit_reason"] == "manual"
        assert realised_today(st, W, NOW) == pytest.approx(sale["pnl_sol"])
        assert bot.sig == "bot-buy2"
    finally:
        st.close()


# -- measure#2: a buy long after a copied wallet's buy copies nobody ---------------------------------------------------


def test_measure_2_a_buy_hours_after_a_copied_wallets_buy_copies_nobody(tmp_path):
    """measure#2: classify._leader_buy took a copied wallet's buy of the coin at ANY earlier time, so the owner's hand
    buy 2.8 hours after one got wallet_copied and a place 40 trades behind, and became a live copy of that wallet. A
    copy lands in the copied wallet's block or the next: only a buy in the chain reading's window before ours
    (classify.LEADER_WINDOW_SLOTS) is the one it copied."""
    svc, st, cfg = _svc(tmp_path)
    try:
        _sniper(st)
        m = Market(st, addr("audit-late"), D, NOW - 12_000, SLOT)
        m.buy(D, 1.0, 0)
        m.buy(LEAD, 2.0, 10)  # the copied wallet's buy; our sniper did not copy it
        for k in range(30):
            m.buy(addr(f"audit-o{k}"), 0.3, 20 + k * 800)
        m.buy(W, 1.485, 25_000, sig="hand")  # the owner's hand buy, 2.8 hours later (0.4 s a slot)
        m.buy(LEAD, 1.0, 26_000)  # the copied wallet buys again
        m.buy(W, 1.0, 26_001, sig="copy")  # and our sniper copies that buy
        m.commit()
        metas = {"hand": _meta("hand", (1_000_000, 16_000_000)), "copy": _meta("copy", BOT_BUY_FEES)}
        _measure(svc, cfg, metas, since=NOW - 12_000)
        hand, copy = st.fill("hand"), st.fill("copy")
        assert hand["wallet_copied"] is None and hand["trades_behind"] is None
        assert copy["wallet_copied"] == LEAD and copy["trades_behind"] == 0
    finally:
        st.close()


def test_measure_2_the_copied_buy_is_looked_for_in_the_chain_readings_window():
    """measure#2: the tape reading looks as far back for the copied buy as the chain reading does."""
    from snipetech.measure import chain_landing
    from snipetech.measure.classify import LEADER_WINDOW_SLOTS, classify_trade

    assert chain_landing.LEADER_WINDOW_SLOTS == LEADER_WINDOW_SLOTS
    st = Store(":memory:")
    try:
        m = Market(st, addr("audit-edge"), D, NOW - 4000, SLOT)
        m.buy(D, 1.0, 0)
        m.buy(LEAD, 1.0, 10)
        inside = m.buy(W, 0.2, 10 + LEADER_WINDOW_SLOTS)
        outside = m.buy(W, 0.2, 11 + LEADER_WINDOW_SLOTS)
        m.commit()
        assert classify_trade(st, inside, {LEAD}, None)["wallet_copied"] == LEAD
        assert classify_trade(st, outside, {LEAD}, None)["wallet_copied"] is None
    finally:
        st.close()
