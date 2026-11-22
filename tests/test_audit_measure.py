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


# -- runner#4: the owner's own tool pays the snipers' 0.001 SOL priority -----------------------------------------------


def _payload(prio, tip):
    return {"priorityFee": prio, "tipAmount": tip, "autoSell": {"sellTipAmount": 0.002, "sellPriorityFeeSol": 0.0001}}


def test_runner_4_a_hand_buy_at_a_snipers_priority_is_the_owners_whatever_wallet_it_follows():
    """runner#4: bot_buy counted a buy with a copied wallet on it and a sniper's priority as the bot's, whatever its
    tip. The owner's own tool pays the same 0.001 SOL priority (2Y2StBPj, 21 Nov 23:07: 1,000,000 priority and a
    16,000,000 tip, no sniper armed), and the guard then counted the owner's trade, a coin sent out as lost in full. A
    buy is the bot's by the whole (priority, tip) pair one of our snipers pays, as on the tape (bot_tape_buy)."""
    from snipetech.measure.ours import bot_buy, bot_tape_buy, sniper_marks

    marks = sniper_marks([_payload(0.001, t) for t in (0.002, 0.049, 0.199, 0.299)] + [_payload(0.005, 0.03)])
    hand = {"sig": "2Y2StBPj", "side": "buy", "priority_lamports": 1_000_000, "tip_lamports": 16_000_000}
    cented = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
    assert bot_buy(hand, marks) is False
    assert bot_buy(hand | {"wallet_copied": cented}, marks) is False

    class T:
        priority, tip = 1_000_000, 16_000_000

    assert bot_tape_buy(T(), marks) is False
    # every pair a sniper's payload pays on a buy (its tip, or none) is the bot's, copied wallet or not
    for prio, tip in ((1_000_000, 2_000_000), (1_000_000, 49_000_000), (1_000_000, 199_000_000), (1_000_000, 0),
                      (1_000_000, 299_000_000), (5_000_000, 30_000_000)):  # fmt: skip
        assert bot_buy(hand | {"priority_lamports": prio, "tip_lamports": tip, "wallet_copied": cented}, marks)
        assert bot_buy(hand | {"priority_lamports": prio, "tip_lamports": tip}, marks)


# -- measure#2 / server#0: measure's own outputs hold the bot's trades only -------------------------------------------

HAND_FEES = (6121, 26_993_879)  # the owner's hand buy of 22 Nov 02:40 (H8WTvo), a bid no sniper pays


def _copies_and_a_hand_trip(tmp_path):
    """Three copies of LEAD by our sniper at its payload's fees, and a round trip by hand (the owner's fees) in a coin
    LEAD bought 10 slots before: inside the window, so the tape reading puts it behind LEAD like a copy."""
    from snipetech.exits_menu import EXIT_MENU_VERSION

    svc, st, cfg = _svc(tmp_path)
    t0 = NOW - 5400
    _sniper(st, created=t0 - 3600, tip=0.002)
    metas = {}
    for k in range(3):
        m = Market(st, addr(f"audit-c{k}"), D, t0 + k * 600, SLOT + k * 1500)
        m.buy(D, 1.0, 0)
        m.buy(LEAD, 1.0, 2)
        m.buy(addr(f"audit-x{k}"), 0.5, 3)
        tb = m.buy(W, 0.2, 4)
        ts = m.sell(W, 1.0, 30)
        m.commit()
        metas[tb.sig], metas[ts.sig] = _meta(tb.sig, (1_000_000, 2_000_000)), _meta(ts.sig, BOT_SALE_FEES)
    m = Market(st, addr("audit-hand"), D, t0 + 300, SLOT + 9000)
    m.buy(D, 1.0, 0)
    m.buy(LEAD, 1.0, 2)
    m.buy(addr("audit-hx"), 0.5, 5)
    hb = m.buy(W, 2.5, 12)  # the owner's hand buy, 4 s after LEAD's
    hs = m.sell(W, 1.0, 400)
    m.commit()
    metas[hb.sig], metas[hs.sig] = _meta(hb.sig, HAND_FEES), _meta(hs.sig, (HAND_FEES[0], 0))
    # a failed send of the owner's (Axiom's 4,500 lamports, no tip) and one of a sniper's sale (its 0.001, untipped)
    for sig, prio in (("owner-failed", 4500), ("bot-failed", 1_000_000)):
        st.put_fill({"sig": sig, "ts": t0 + 900, "slot": SLOT + 9500, "wallet": W, "side": "failed", "sol": 0.0,
                     "tokens": 0.0, "mint": addr("audit-hand"), "bid_arm": "control", "priority_lamports": prio,
                     "tip_lamports": 0})  # fmt: skip
    # a dust "buy" of USDC another wallet paid for, as the chain reading wrote one before 22 Nov (3kA7a2QS)
    st.put_fill({"sig": "dust", "ts": t0 + 950, "slot": SLOT + 9600, "wallet": W, "side": "buy", "sol": 0.0,
                 "tokens": 100.0, "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "priority_lamports": 50,
                 "tip_lamports": 0, "venue": "other", "landing_src": "chain-none"})  # fmt: skip
    st.set_coverage("tape", {"spans": [[t0 - 600, NOW + 600]]})  # the tape recorded the whole time: buys are replayed
    cfg.measure.min_arm_fills = 3
    out = _measure(svc, cfg, metas, since=NOW - 9000)
    return st, out, hb, hs, st.shadowed_buys(EXIT_MENU_VERSION)


def test_server_0_the_owners_hand_trip_shapes_none_of_measures_outputs(tmp_path):
    """measure#2 / server#0: run_measure fed every buy and sale of the wallet to the bid model (the book's arm choice,
    qualify's cost a transaction), wallet_live (the book's pause and rank), the measured landing, the landings by curve
    progress and the exit shadow. 22 Nov: the owner's three H8WTvo hand buys were 3 of the 'paid' arm's 8 fills. The
    hand trip's rows are still written; only the bot's trades are counted."""
    from snipetech.measure.ours import bot_buy, fee_marks

    st, out, hb, hs, shadowed = _copies_and_a_hand_trip(tmp_path)
    try:
        hand = st.fill(hb.sig)
        assert hand is not None and st.fill(hs.sig)["buy_sig"] == hb.sig  # written and paired, as every fill is
        assert hand["wallet_copied"] == LEAD and not bot_buy(hand, fee_marks(st))
        assert sum(r["n"] for r in st.bid_model()) == 3
        assert sum(r["failed_n"] for r in st.bid_model()) == 1  # the sniper's failed send; the owner's is no arm's cost
        assert out["non_coin_buys"]["n"] == 0  # no sniper bought USDC: no alert
        assert {r["wallet"]: r["n"] for r in st.wallet_live()} == {LEAD: 3}
        assert out["trips"] == 3
        assert st.coverage("measure:landing_pos")["n"] == 3
        assert sum(b["n"] for b in out["landing_by_progress"].values()) == 3
        assert hb.sig not in shadowed and len(shadowed) == 3
    finally:
        st.close()


def test_measure_2_the_fee_policys_evidence_is_the_bots_own_fills():
    """measure#2: fees.evidence_fills gave the fee policy every buy and sale of the wallet; an owner's hand buy right
    after a copied wallet's (wallet_copied on it) was evidence for that wallet's copy sniper's tip. Only the bot's buys
    and sales are (measure/ours.py)."""
    from snipetech import fees

    st = Store(":memory:")
    try:
        _sniper(st, tip=0.002)
        base = {"wallet": W, "mint": "M", "tokens": 1_000_000}
        st.put_fill(base | {"sig": "bot-b", "ts": NOW - 900, "side": "buy", "sol": 0.2, "wallet_copied": LEAD,
                            "trades_behind": 2, "priority_lamports": 1_000_000, "tip_lamports": 2_000_000})  # fmt: skip
        st.put_fill(base | {"sig": "bot-s", "ts": NOW - 888, "side": "sell", "sol": 0.19, "buy_sig": "bot-b",
                            "pnl_sol": -0.01, "priority_lamports": 1_000_000, "tip_lamports": 2_000_000})  # fmt: skip
        hand = {"priority_lamports": HAND_FEES[0], "tip_lamports": HAND_FEES[1]}
        st.put_fill(base | hand | {"sig": "hand-b", "ts": NOW - 600, "side": "buy", "sol": 2.5, "wallet_copied": LEAD,
                                   "trades_behind": 0})  # fmt: skip
        st.put_fill(base | {"sig": "hand-s", "ts": NOW - 500, "side": "sell", "sol": 2.4, "buy_sig": "hand-b",
                            "pnl_sol": -0.1, "priority_lamports": HAND_FEES[0], "tip_lamports": 0})  # fmt: skip
        assert sorted(r["sig"] for r in fees.evidence_fills(st, W, NOW)) == ["bot-b", "bot-s"]
    finally:
        st.close()


# -- measure#1 / agents#3 / docs#5: the post-mortem, its summary, the agents' fill tools and their wake ---------------

M1, M2 = "Coin1" + "1" * 39, "Coin2" + "2" * 39


def _bot_and_hand_trips():
    """A copy of the bot's (-0.1 SOL) and a coin the owner bought and sold by hand (-0.5 SOL, the owner's fees)."""
    st = Store(":memory:")
    _sniper(st, tip=0.049)
    st.put_fill({"sig": "bot-b", "ts": NOW - 3000, "wallet": W, "mint": M1, "side": "buy", "sol": 1.0,
                 "tokens": 1_000_000, "priority_lamports": 1_000_000, "tip_lamports": 49_000_000,
                 "wallet_copied": LEAD})  # fmt: skip
    st.put_fill({"sig": "bot-s", "ts": NOW - 2988, "wallet": W, "mint": M1, "side": "sell", "sol": 0.953,
                 "tokens": 1_000_000, "priority_lamports": 1_000_000, "tip_lamports": 2_000_000, "pnl_sol": -0.1,
                 "buy_sig": "bot-b", "twin_fee_sol": 0.0})  # fmt: skip
    st.put_fill({"sig": "hand-b", "ts": NOW - 1800, "wallet": W, "mint": M2, "side": "buy", "sol": 4.95,
                 "tokens": 2_000_000, "priority_lamports": 6121, "tip_lamports": 52_000_000})  # fmt: skip
    st.put_fill({"sig": "hand-s", "ts": NOW - 1200, "wallet": W, "mint": M2, "side": "sell", "sol": 4.5,
                 "tokens": 2_000_000, "priority_lamports": 6121, "tip_lamports": 0, "pnl_sol": -0.5,
                 "buy_sig": "hand-b", "twin_fee_sol": 0.0})  # fmt: skip
    return st


def test_measure_1_the_post_mortem_reviews_the_bots_trips_only(tmp_path):
    """measure#1: learning.trips_since_postmortem (the trigger of a paid analyst turn), learning.round_trips (the
    agents' read_round_trips) and postmortem.send_summary (the owner's Telegram lines) read every sale of the wallet:
    the owner heard "2 live round trips ... Net: -0.6000 SOL" for one bot copy at -0.1 and a hand trade at -0.5."""
    from snipetech.operator import learning
    from snipetech.operator import postmortem as P
    from snipetech.operator.paths import OperatorPaths

    st = _bot_and_hand_trips()
    try:
        paths = OperatorPaths(tmp_path).ensure()
        assert realised_today(st, W, NOW) == pytest.approx(-0.1)  # the day stop already left the owner's trip out
        trips = learning.trips_since_postmortem(st, W, paths, NOW)
        assert trips["n"] == 1 and trips["newest_ts"] == NOW - 2988
        out = learning.round_trips(st, W, NOW - 7200, cfg=Config())
        assert out["summary"]["trips"] == 1 and out["summary"]["pnl_sol"] == pytest.approx(-0.1)
        assert [t["sig"] for t in out["trips"]] == ["bot-s"]

        class Svc:
            store = st

        class Sink:
            def __init__(self):
                self.lines = []

            def write(self, text, level="info"):
                self.lines.append(text)

            def send(self, text):
                self.lines.append(text)

        cfg = Config()
        cfg.operator.wallet = W
        notes, notifier = Sink(), Sink()
        whole = {"n": 2, "since_ts": NOW - 7200, "newest_ts": NOW}  # a count taken before the fix: both sales
        text = P.send_summary(Svc(), cfg, paths, whole, notes, notifier, started=NOW)
        assert text.startswith("Post-mortem: 1 live round trip,") and "Net: -0.1000 SOL" in text
    finally:
        st.close()


def test_agents_3_read_fills_counts_the_bots_fills_and_the_owners_apart(tmp_path):
    """agents#3 / measure#1: read_fills (the fee, OKTech and exit analysts' "truth for the live landing") counted
    every row: the four H8WTvo hand buys were "buys" of ours. Each row says whose it is (`ours`); the summary is the
    bot's, the owner's rows are counted apart."""
    import json

    from snipetech.config import OperatorConfig
    from snipetech.operator.jobs import OperatorJobs
    from snipetech.operator.paths import OperatorPaths
    from snipetech.operator.tools import NoteSink, make_operator_tools

    paths = OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")
    try:
        src = _bot_and_hand_trips()
        for r in src.fills(limit=100):
            st.put_fill(r)
        for rec in src.oktech_snipers(active=False):
            st.add_oktech_sniper(rec)
        src.close()

        class Svc:
            store = st

        jobs = OperatorJobs(repo=tmp_path, data=tmp_path / "data", db=tmp_path / "data" / "snipetech.db",
                            branch="main", max_jobs=3)  # fmt: skip
        tools = {t.name: t for t in make_operator_tools(Svc(), paths, jobs, OperatorConfig(),
                                                        NoteSink(paths, now=lambda: NOW))}  # fmt: skip
        out = json.loads(asyncio.run(tools["read_fills"].call({"hours": 24 * 3650})))  # every fill on record
        assert {f["sig"]: f["ours"] for f in out["fills"]} == {"bot-b": True, "bot-s": True, "hand-b": False,
                                                                "hand-s": False}  # fmt: skip
        assert out["summary"] == {"buys": 1, "sells": 1, "failed": 0, "pnl_sol": -0.1, "unpriced": 0}
        assert out["owner"] == {"buys": 1, "sells": 1, "failed": 0}
    finally:
        st.close()


@pytest.mark.parametrize("role", ["oktech", "fees", "exits"])
def test_agents_3_the_owners_hand_fills_are_no_news_of_ours(role):
    """agents#3 / docs#5: roles._news woke the fee, OKTech and exit analysts on any fill since their last turn: 22 Nov
    03:00:21 the OKTech analyst's turn (1.49 $) was called by the owner's three H8WTvo hand buys alone. Only a fill of
    the bot's is news; a sniper's fill still is."""
    from snipetech.operator import roles

    st = Store(":memory:")
    try:
        _sniper(st, created=NOW - 7200)
        last = NOW - 600
        for k, (sol, prio, tip) in enumerate(((4.95, 8175, 51_991_825), (2.475, 6121, 26_993_879))):
            st.put_fill({"sig": f"hand{k}", "wallet": W, "mint": M2, "side": "buy", "sol": sol, "ts": last + 60 + k,
                         "priority_lamports": prio, "tip_lamports": tip})  # fmt: skip
        st.put_fill({"sig": "owner-failed", "wallet": W, "mint": M2, "side": "failed", "sol": 0.0, "ts": last + 90,
                     "priority_lamports": 4500, "tip_lamports": 0})  # fmt: skip
        assert roles._news(role, st, {"seen": {}}, float(last), []) is None
        st.put_fill({"sig": "bot-failed", "wallet": W, "mint": M1, "side": "failed", "sol": 0.0, "ts": last + 120,
                     "priority_lamports": 1_000_000, "tip_lamports": 199_000_000})  # fmt: skip
        assert roles._news(role, st, {"seen": {}}, float(last), []) == "a new fill of ours (read_fills)"
    finally:
        st.close()


# -- measure#6: a replayed sale pays a sale's fee, the buy's bid is paid once ------------------------------------------


class _Tape(Market):
    def _ts(self, slot: int) -> int:
        return self.ts0 + int((slot - self.slot0) * 0.214)


def _shadow_world(st):
    m = _Tape(st, addr("audit-shadow"), D, NOW, SLOT)
    m.buy(D, 1.0, 0)
    m.buy(LEAD, 2.0, 0)
    ours = m.buy(W, 0.2, 0)
    for k in range(1, 11):
        m.buy(addr(f"audit-up{k}"), 1.0, 4 * k)
    for k in range(1, 11):
        m.sell(addr(f"audit-up{k}"), 1.0, 40 + 4 * k)
    m.sell(W, 1.0, 90)
    m.buy(addr("audit-late"), 0.05, 300)
    m.buy(addr("audit-later"), 0.05, 1450)
    m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 60, NOW + 4000]]})
    return {"sig": ours.sig, "ts": ours.ts, "slot": ours.slot, "mint": ours.mint, "side": "buy", "sol": 0.2025,
            "tokens": float(ours.base), "wallet": W, "wallet_copied": LEAD, "priority_lamports": 1_000_000,
            "tip_lamports": 2_000_000}  # fmt: skip


def test_measure_6_a_bigger_buy_tip_lowers_each_replay_by_the_tip_once():
    """measure#6: shadow_exits charged every replayed SALE the buy's whole fee, its tip included: each replay of a
    copy bought at a 0.199 tip read 0.197 SOL too low (more for an exit that sells in parts), where its live sales paid
    0.001 + 0.002. A replayed sale pays what the buying sniper's sale pays (the buy's priority and its sell tip), the
    buy's bid is paid once (sim.exits.Costs.entry_bid_lamports)."""
    from snipetech.measure.exits import shadow_exits

    st = Store(":memory:")
    try:
        buy = _shadow_world(st)
        low = {r["exit_name"]: r["sim_pnl_sol"] for r in shadow_exits(st, buy, now=NOW + 400, slot_secs=0.214)}
        tipped = buy | {"tip_lamports": 199_000_000}
        high = {r["exit_name"]: r["sim_pnl_sol"] for r in shadow_exits(st, tipped, now=NOW + 400, slot_secs=0.214)}
        assert low and set(low) == set(high)
        assert all(abs(low[n] - high[n] - 0.197) < 1e-6 for n in low), {n: low[n] - high[n] for n in low}
    finally:
        st.close()


def test_measure_6_a_replay_made_at_the_old_costs_is_made_again_once(tmp_path):
    """measure#6: the replays on record were made at the old costs; each is made again once, newest first (a marker of
    the costs' version, not a new menu version, which would reset the verdicts' exits too)."""
    from snipetech.exits_menu import EXIT_MENU_VERSION
    from snipetech.measure.run import SHADOW_VERSION, SHADOW_VERSION_KEY, shadow_new_buys

    st, cfg = _svc(tmp_path)[1:]
    try:
        buy = _shadow_world(st)
        st.put_fill(buy)
        st.put_exit_shadow([{"buy_sig": buy["sig"], "exit_name": "lab", "menu_version": EXIT_MENU_VERSION,
                             "sim_pnl_sol": -9.0, "sim_reason": "timer", "computed_at": NOW + 100}])  # fmt: skip
        assert shadow_new_buys(st, cfg, W, NOW + 400, NOW - 3600) == 1
        assert st.coverage(SHADOW_VERSION_KEY)["version"] == SHADOW_VERSION
        assert {r["exit_name"]: r["sim_pnl_sol"] for r in st.exit_shadow([buy["sig"]])}["lab"] != -9.0
        assert shadow_new_buys(st, cfg, W, NOW + 500, NOW - 3600) == 0  # once
    finally:
        st.close()


# -- measure#5: the live exit hold12 has its name and its replay -------------------------------------------------------


def _hold12_payload():
    from snipetech.oktech import list_copy_sniper

    return list_copy_sniper(LEAD, "Decu", 2.0, 3.5, cfg=Config(), exit_name="hold12").config


def test_measure_5_the_live_hold12_exit_is_named():
    """measure#5: preset_name knew only oktech.EXITS and the menu (lab, hold60, ride): every live copy since 20 Nov
    23:03 ran hold12 (oktech.named_exit "holdN", the money step's exit) and was recorded as 'custom'."""
    from snipetech.measure.exits import preset_name

    assert preset_name(_hold12_payload()) == "hold12"
    timer = {"autoSell": {"enabled": True, "sellAfterSeconds": 30, "sellPercent": 50}}  # half at 30 s: no holdN
    assert preset_name(timer) == "custom"


def test_measure_5_the_exit_that_ran_is_replayed_and_compared(tmp_path):
    """measure#5: the shadow replayed the menu only, so the exit that ran (hold12) was never replayed: _exit_block had
    no replay of it, _copy_exits dropped every such copy, and the exit lever, exit_record and the post-mortem's exit
    comparison held none of the size copies. A buy named 'custom' before is named again, its sales too."""
    from snipetech.exits_menu import COPY_EXIT_MENU
    from snipetech.measure.attribution import exit_record

    svc, st, cfg = _svc(tmp_path)
    try:
        payload = _hold12_payload()
        st.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": LEAD, "name": "copy lead",
                              "config_hash": "h", "created_at": NOW - 86400, "payload": payload})  # fmt: skip
        buy = _shadow_world(st)
        metas = {buy["sig"]: _meta(buy["sig"], (1_000_000, 2_000_000))}
        sale = next(t for t in st.trades_for_mint(buy["mint"]) if t.user == W and not t.is_buy)
        metas[sale.sig] = _meta(sale.sig, BOT_SALE_FEES)
        _measure(svc, cfg, metas, now=NOW + 400, since=NOW - 600)
        assert st.fill(buy["sig"])["exit_name"] == "hold12" and st.fill(sale.sig)["exit_name"] == "hold12"
        replays = {r["exit_name"] for r in st.exit_shadow([buy["sig"]])}
        assert replays == {m.name for m in COPY_EXIT_MENU} | {"hold12"}
        record = exit_record(st, W)
        assert len(record) == 1 and record[0]["ran"] == "hold12" and record[0]["gain_sol"] is not None
        # a buy recorded as 'custom' before the name existed is named again by the next run, and so are its sales
        st.put_fill(st.fill(buy["sig"]) | {"exit_name": "custom"})
        st.put_fill(st.fill(sale.sig) | {"exit_name": "custom"})
        _measure(svc, cfg, metas, now=NOW + 500, since=NOW + 450)
        assert st.fill(buy["sig"])["exit_name"] == "hold12" and st.fill(sale.sig)["exit_name"] == "hold12"
    finally:
        st.close()
