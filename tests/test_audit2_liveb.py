"""The second round of the 22-23 Nov 2026 audit, group "liveb" (the book, the automatic live test, the gate, the
strategist's evidence): each test pins one item of the round's brief (B1 to B8, its id in the test's name).

The sniper wallet is the owner's too: the owner buys and sells in it by hand (21-22 Nov: four hand buys of one coin,
1.5 to 4.95 SOL, at fees no sniper of ours pays). Only the bot's own trades and coins are the bot's (measure/ours.py)."""

import asyncio

from snipetech.config import Config
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store

NOW = 1_795_500_000  # 24 Nov 2026 06:00 UTC
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"  # the sniper wallet
LEAD = "Lead" + "L" * 40
OWNER_COIN = "H8WTvo" + "h" * 38
OWNER_FEES = (8175, 51_991_825)  # the 22 Nov hand buys' priority and tip, in lamports: no sniper of ours pays them
BOT_BUY_FEES, BOT_SALE_FEES = (1_000_000, 199_000_000), (1_000_000, 2_000_000)
PAYLOAD = {"mode": "ctbot", "ctbotWallets": [{"address": LEAD}], "priorityFee": 0.001, "tipAmount": 0.199,
           "autoSell": {"sellPriorityFeeSol": 0.0001, "sellTipAmount": 0.002}}  # fmt: skip


def _sniper(st, sid="cs1", created=NOW - 7 * 86400):
    """A copy sniper on record with the payload we sent OKTech: its fees tell its trades from the owner's."""
    st.add_oktech_sniper({"id": sid, "external_id": f"e-{sid}", "kind": "copy", "source": LEAD, "name": "copy lead",
                          "config_hash": "h", "payload": PAYLOAD, "created_at": created})  # fmt: skip


def _bot_trip(st, k, ts, pnl=-0.01):
    """One round trip of the bot's in fills: a copy at the sniper's fees and the sale OKTech made of it."""
    buy, mint = f"bot-buy-{k}", f"Coin{k}" + "c" * 38
    st.put_fill({"sig": buy, "ts": ts, "slot": 1000 + k, "wallet": W, "wallet_copied": LEAD, "mint": mint, "side": "buy",
                 "sol": 0.2, "tokens": 1e6, "priority_lamports": BOT_BUY_FEES[0], "tip_lamports": BOT_BUY_FEES[1],
                 "bid_arm": "control", "trades_behind": 1, "price_gap_pct": 2.0, "pos_in_slot": 4})  # fmt: skip
    st.put_fill({"sig": f"bot-sell-{k}", "ts": ts + 12, "slot": 1030 + k, "wallet": W, "mint": mint, "side": "sell",
                 "sol": 0.2 + pnl, "tokens": 1e6, "pnl_sol": pnl, "buy_sig": buy, "bid_arm": "control",
                 "priority_lamports": BOT_SALE_FEES[0], "tip_lamports": BOT_SALE_FEES[1], "exit_reason": "timer",
                 "exit_name": "hold12", "crossed": 0})  # fmt: skip


def _hand_trip(st, ts, pnl=-0.6):
    """The owner's own round trip in the same wallet: a hand buy of 4.95 SOL and its hand sale, as the measure stage
    writes them (paired, the sale a "manual" exit, each filed under the bid arm its fee is nearest to)."""
    st.put_fill({"sig": "hand-buy", "ts": ts, "slot": 5000, "wallet": W, "wallet_copied": None, "mint": OWNER_COIN,
                 "side": "buy", "sol": 4.95, "tokens": 1e6, "priority_lamports": OWNER_FEES[0],
                 "tip_lamports": OWNER_FEES[1], "bid_arm": "paid", "pos_in_slot": 40})  # fmt: skip
    st.put_fill({"sig": "hand-sell", "ts": ts + 3600, "slot": 14000, "wallet": W, "mint": OWNER_COIN, "side": "sell",
                 "sol": 4.95 + pnl, "tokens": 1e6, "pnl_sol": pnl, "buy_sig": "hand-buy", "bid_arm": "paid",
                 "priority_lamports": OWNER_FEES[0], "tip_lamports": 0, "exit_reason": "manual",
                 "crossed": 0})  # fmt: skip


def _cfg(tmp_path):
    cfg = Config()
    cfg.operator.wallet = W
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    OperatorPaths(tmp_path / "data").ensure()
    return cfg


# -- B1: the strategist's trigger and its week's evidence hold the bot's trades only ----------------------------------


def test_b1_the_strategists_trigger_counts_only_the_bots_round_trips(tmp_path):
    """R-measure-3: `new_round_trips` counted every sale of the wallet in fills, the owner's hand sales too, so a hand
    trade could be the 20th "live round trip" that buys a paid turn about settings it says nothing of. Nineteen round
    trips of the bot's and one of the owner's are nineteen."""
    from snipetech.strategist.run import new_round_trips, run_strategist

    cfg = _cfg(tmp_path)
    st = Store(cfg.storage.db_path)
    try:
        _sniper(st)
        for k in range(19):
            _bot_trip(st, k, NOW - 3 * 86400 + 60 * k)
        _hand_trip(st, NOW - 2 * 86400)
        assert new_round_trips(st, cfg, NOW)["n"] == 19
        turns: list = []

        async def turn(*a, **kw):
            turns.append(a)
            return {"patch": {}, "reason": "x", "usd": 0.0, "stopped": "end_turn"}

        class Svc:
            store = st

        out = asyncio.run(run_strategist(Svc(), cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: None), turn=turn))
        assert turns == [] and out["skipped"].startswith("not enough new trades: 19 live round trips")
        _bot_trip(st, 19, NOW - 3600)  # the bot's twentieth: now there is something new to judge
        assert new_round_trips(st, cfg, NOW)["n"] == 20
    finally:
        st.close()


def test_b1_the_weeks_evidence_leaves_the_owners_hand_trades_out(tmp_path):
    """R-measure-3: `week_evidence` built its arms (buys, sells, fees, the realised result, what a round trip costs),
    its exit reasons and its crossed sells from every fill of the wallet. The owner's 4.95 SOL hand buy and its hand
    sale were a "paid" arm with one round trip that lost 0.6 SOL, and a "manual" exit of ours. They are no evidence of
    what a sniper's bid buys or of how our exits close: the bot's rows only (measure/ours.bot_rows)."""
    from snipetech.strategist.evidence import week_evidence

    cfg = _cfg(tmp_path)
    st = Store(cfg.storage.db_path)
    try:
        _sniper(st)
        for k in range(3):
            _bot_trip(st, k, NOW - 3 * 86400 + 60 * k)
        _hand_trip(st, NOW - 2 * 86400)
        ev = week_evidence(st, cfg, NOW)
        assert set(ev["arms"]) == {"control"} and set(ev["round_trip_cost_by_arm"]) == {"control"}
        arm = ev["arms"]["control"]
        assert (arm["buys"], arm["sells"]) == (3, 3) and arm["realised_sol"] == -0.03
        assert ev["exit_reasons"] == {"hold12": {"timer": 3}}  # the owner's hand sale is no "manual" exit of ours
        assert ev["crossed_sells"] == {"crossed": 0, "checked": 3}
    finally:
        st.close()


def test_b1_with_no_sniper_on_record_every_trade_counts_as_before(tmp_path):
    """With no payload on record nothing tells a hand trade apart (measure/ours.py): the trigger and the evidence
    count every fill, as they did."""
    from snipetech.strategist.evidence import week_evidence
    from snipetech.strategist.run import new_round_trips

    cfg = _cfg(tmp_path)
    st = Store(cfg.storage.db_path)
    try:
        for k in range(3):
            _bot_trip(st, k, NOW - 3 * 86400 + 60 * k)
        _hand_trip(st, NOW - 2 * 86400)
        assert new_round_trips(st, cfg, NOW)["n"] == 4
        ev = week_evidence(st, cfg, NOW)
        assert set(ev["arms"]) == {"control", "paid"} and ev["exit_reasons"]["none"] == {"manual": 1}
    finally:
        st.close()
