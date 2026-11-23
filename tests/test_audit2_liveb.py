"""The second round of the 22-23 Nov 2026 audit, group "liveb" (the book, the automatic live test, the gate, the
strategist's evidence): each test pins one item of the round's brief (B1 to B8, its id in the test's name).

The sniper wallet is the owner's too: the owner buys and sells in it by hand (21-22 Nov: four hand buys of one coin,
1.5 to 4.95 SOL, at fees no sniper of ours pays). Only the bot's own trades and coins are the bot's
(measure/ours.py)."""

import asyncio

from snipetech.config import Config
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store
from tests import test_audit_book as AB  # the first round's book helpers, on their own clock (BT.NOW)
from tests import test_book_run as BT

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
    st.put_fill({"sig": buy, "ts": ts, "slot": 1000 + k, "wallet": W, "wallet_copied": LEAD, "mint": mint,
                 "side": "buy", "sol": 0.2, "tokens": 1e6, "priority_lamports": BOT_BUY_FEES[0],
                 "tip_lamports": BOT_BUY_FEES[1], "bid_arm": "control", "trades_behind": 1, "price_gap_pct": 2.0,
                 "pos_in_slot": 4})  # fmt: skip
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


def test_b1_the_strategist_is_told_its_evidence_leaves_the_owners_hand_trades_out():
    """Its instructions said only that fills can come from snipers the owner started by hand. The owner also trades by
    hand, with no sniper: the strategist is told those trades are in none of its numbers, so it never looks for them
    there or takes the wallet's own moves for the snipers' result."""
    from snipetech.strategist.prompts import STRATEGIST_SYSTEM

    text = " ".join(STRATEGIST_SYSTEM.split())
    assert "The owner also buys and sells by hand in this wallet: those trades are in none of the evidence" in text


# -- B2: the book's kill and retire paths beside a coin that is the owner's own ---------------------------------------

BOUGHT_BEFORE = "Agn" + "a" * 41  # a coin the bot copied and sold yesterday, and the owner bought by hand today
RAW = 10**12  # what one AB._tape_trade buys, in the coin's smallest units


def _owners_coin_the_bot_once_traded(st, raw=RAW, hand_ts=BT.NOW - 1800):
    """The bot's copy of BOUGHT_BEFORE a day ago, sold 12 s later, then the owner's hand buy of it; the wallet's row."""
    AB._tape_trade(st, "BOTBUY", BT.NOW - 86400, 400_100_000, True, AB.BOT_FEES, mint=BOUGHT_BEFORE)
    AB._tape_trade(st, "BOTSALE", BT.NOW - 86388, 400_100_030, False, AB.BOT_SALE_FEES, mint=BOUGHT_BEFORE)
    AB._tape_trade(st, "HANDBUY", hand_ts, 400_500_000, True, AB.OWNER_FEES, mint=BOUGHT_BEFORE)
    return [{"mint": BOUGHT_BEFORE, "amount": raw / 1e6, "raw": raw, "value_sol": 4.9, "priced": True}]


def _book(tmp_path, svc, cfg, sent=None, native=10.0):
    from snipetech.book.run import run_book

    held = list(svc._held)
    return asyncio.run(run_book(svc, cfg, BT.NOW, notifier=AB._notifier([] if sent is None else sent),
                                balances=lambda w: (native, 0.0, held)))  # fmt: skip


def test_b2_a_kill_stops_the_books_snipers_beside_a_coin_the_owner_bought_after_the_bot_sold_out_of_it(tmp_path):
    """R-livetest-2: the book left a coin out of its holdings only when no buy of the bot's, ever, explained it
    (run._strays). A coin our sniper copied and sold yesterday and the owner bought by hand today stayed "held" for as
    long as the owner kept it: /book off, the guard and the day stop kept every book sniper armed beside it, each still
    able to buy to its cap. Every buy of it since the wallet's last sale paid the owner's fees and explains all that is
    held: the owner's own (measure/ours.owner_coins, as /stop reads it), and no holding of the book's."""
    st, cfg = BT._env(tmp_path)  # the book is off: its kill path stops its snipers that hold nothing
    AB._book_sniper(st)
    held = _owners_coin_the_bot_once_traded(st)
    AB._seen(tmp_path, BOUGHT_BEFORE, BT.NOW - 1800)
    svc, sent = BT.FakeSvc(st, snipers=[{"id": "k1", "name": BT.K1, "status": "running"}], held=held, cfg=cfg), []
    try:
        out = _book(tmp_path, svc, cfg, sent)
        assert out["kill_reason"] == "book off" and ("stop", "k1") in svc.calls, (svc.calls, sent)
        assert not any("kept running while the wallet holds a coin" in t for t in sent)
    finally:
        st.close()


def test_b2_the_owners_coin_is_no_holding_from_the_moment_its_buy_is_on_record(tmp_path):
    """A stray no buy explains waits ten minutes of the watch seeing it (a buy of ours may not be written yet). A coin
    whose buy is on record at the owner's fees, the whole holding explained, needs no wait: /stop reads it so at
    once."""
    st, cfg = BT._env(tmp_path)
    AB._book_sniper(st)
    AB._hand_buy_in_fills(st, BT.NOW - 400)  # read off the chain by the measure stage; the watch has not seen it yet
    held = [{"mint": AB.OWNER_COIN, "amount": 1.0, "raw": 1_000_000, "value_sol": 4.9, "priced": True}]
    svc = BT.FakeSvc(st, snipers=[{"id": "k1", "name": BT.K1, "status": "running"}], held=held, cfg=cfg)
    try:
        assert _book(tmp_path, svc, cfg)["kill_reason"] == "book off" and ("stop", "k1") in svc.calls, svc.calls
    finally:
        st.close()


def test_b2_more_of_the_coin_than_the_owners_buys_explain_keeps_every_book_sniper(tmp_path):
    """Rule 1: twice what the owner's hand buy brought in may be a buy of ours the tape has not written yet. Such a
    coin is not the owner's beyond doubt, and under a kill every book sniper stays, as before."""
    st, cfg = BT._env(tmp_path)
    AB._book_sniper(st)
    held = _owners_coin_the_bot_once_traded(st, raw=2 * RAW)
    AB._seen(tmp_path, BOUGHT_BEFORE, BT.NOW - 30)
    svc, sent = BT.FakeSvc(st, snipers=[{"id": "k1", "name": BT.K1, "status": "running"}], held=held, cfg=cfg), []
    try:
        out = _book(tmp_path, svc, cfg, sent)
        assert out["kill_reason"] == "book off" and svc.calls == [], svc.calls
        assert any("1 book sniper(s) kept running while the wallet holds a coin" in t for t in sent), sent
    finally:
        st.close()


def test_b2_a_coin_of_ours_beside_the_owners_own_keeps_every_book_sniper(tmp_path):
    """Only a wallet that holds nothing a sniper of ours may hold lets a kill stop them: with a copy of ours (a
    sniper's fees) beside the owner's coin every book sniper stays, and the owner's line counts them."""
    st, cfg = BT._env(tmp_path)
    AB._book_sniper(st)
    held = _owners_coin_the_bot_once_traded(st)
    ours = "Ours" + "o" * 40
    AB._tape_trade(st, "COPY", BT.NOW - 900, 400_600_000, True, AB.BOT_FEES, mint=ours)
    held.append({"mint": ours, "amount": RAW / 1e6, "raw": RAW, "value_sol": 0.2, "priced": True})
    svc, sent = BT.FakeSvc(st, snipers=[{"id": "k1", "name": BT.K1, "status": "running"}], held=held, cfg=cfg), []
    try:
        out = _book(tmp_path, svc, cfg, sent)
        assert out["kill_reason"] == "book off" and svc.calls == [], svc.calls
        assert any("1 book sniper(s) kept running while the wallet holds a coin" in t for t in sent), sent
    finally:
        st.close()


K2 = f"snipetech copy book {BT.L2[:6]} arm:control"


def test_b2_a_book_sniper_with_no_fee_record_stays_beside_the_owners_coin(tmp_path):
    """The owner's coin is told by the fees of the snipers on record. A book sniper the bot holds no payload of pays
    fees nobody recorded here, so that coin may be its own: under a kill it keeps running (a stopped sniper strands its
    coin), as /stop leaves it (operator/commands._unrecorded), while the sniper on record is stopped."""
    st, cfg = BT._env(tmp_path)
    AB._book_sniper(st)  # k1, with the payload we sent; k2 has no record here
    AB._hand_buy_on_tape(st, BT.NOW - 1800)
    AB._seen(tmp_path, AB.OWNER_COIN, BT.NOW - 1800)
    held = [{"mint": AB.OWNER_COIN, "amount": 1e6, "raw": RAW, "value_sol": 10.2, "priced": True}]
    snipers = [{"id": "k1", "name": BT.K1, "status": "running"}, {"id": "k2", "name": K2, "status": "running"}]
    svc, sent = BT.FakeSvc(st, snipers=snipers, held=held, cfg=cfg), []
    try:
        out = _book(tmp_path, svc, cfg, sent)
        assert out["kill_reason"] == "book off" and svc.calls == [("stop", "k1")], svc.calls
        assert any("1 book sniper(s) kept running" in t for t in sent), sent
    finally:
        st.close()


def test_b2_the_plan_keeps_a_sniper_that_may_hold_the_owners_coin_and_never_retires_it():
    """The policy's part: a sniper named in `may_hold` is kept like a holder under a kill and when its wallet left the
    ranking, and is not retired, though the wallet holds nothing else; every other sniper goes as it always did."""
    from snipetech.book.policy import BookInputs, plan_book

    def plan(snipers, may_hold, qualified=(), **kw):
        inp = BookInputs(equity_sol=10, day_realised_sol=0, day_risk_used_sol=0, qualified=list(qualified), live={},
                         bid_model={}, snipers=snipers, holdings=[], now=BT.NOW, holdings_known=True, native_sol=10,
                         wsol=0, may_hold=may_hold, **kw)  # fmt: skip
        return {a.sniper_id: (a.kind, a.reason) for a in plan_book(inp, Config().book).actions if a.sniper_id}

    two = [{"id": "k1", "name": BT.K1, "status": "running"}, {"id": "k2", "name": K2, "status": "running"}]
    killed = plan(two, {"k2"}, stopped=True)
    assert killed["k1"][0] == "stop" and killed["k2"][0] == "keep" and "never stop a holder" in killed["k2"][1]
    assert "may be its own (no record here of the fees it pays)" in killed["k2"][1]
    dropped = plan(two, {"k2"})  # neither wallet is ranked any more
    assert dropped["k1"][0] == "stop" and dropped["k2"][0] == "keep"
    ranked = [{"wallet": BT.L1, "score": 0.03, "copy_plan": {"min_leader_sol": 0.3}}]
    one = [{"id": "k1", "name": BT.K1, "status": "running"}]
    assert plan(one, set(), ranked, buys_by_sniper={"k1": 1})["k1"][0] == "retire"
    assert plan(one, {"k1"}, ranked, buys_by_sniper={"k1": 1})["k1"][0] == "keep"


def test_b2_the_owners_coin_holds_back_neither_a_retire_nor_the_clean_up(tmp_path):
    """The book on: k1 made its relied-on buy and the wallet holds only a coin the bot sold out of yesterday and the
    owner bought by hand today. Before, that coin held back every replacement and the clean-up for as long as the owner
    kept it. k1 is retired and replaced, and a sniper that ended is deleted."""
    st, cfg = BT._env(tmp_path, enabled=True)
    old = "snipetech copy book OLDWAL arm:control"
    AB._book_sniper(st)
    AB._book_sniper(st, sid="e1", name=old, created_at=BT.NOW - 9000)  # an ended one, on record with its fees
    BT._coin(st, "c1", BT.NOW - 3000, 400_000_000, 0, 3, leader=BT.L1, sell_slot=10)  # k1's relied-on buy, sold
    held = _owners_coin_the_bot_once_traded(st)
    BT._fresh_tape(st)
    AB._seen(tmp_path, BOUGHT_BEFORE, BT.NOW - 1800)
    st.set_coverage("book:ended", {"ids": ["e1"], "ts": int(BT.NOW) - 300})  # the run before saw e1 ended too
    snipers = [{"id": "k1", "name": BT.K1, "status": "running"}, {"id": "e1", "name": old, "status": "finished"}]
    svc = BT.FakeSvc(st, snipers=snipers, held=held, cfg=cfg)
    try:
        _book(tmp_path, svc, cfg)
        assert ("stop", "k1") in svc.calls and ("delete", "k1") in svc.calls, svc.calls
        assert ("delete", "e1") in svc.calls, svc.calls
    finally:
        st.close()


class _MoreLater(BT.FakeSvc):
    """From the third read of the wallet on (the one after a retire's stop) it holds twice as much of the coin: a
    sniper of ours bought it too, and the tape has not written that buy yet."""

    async def open_positions(self, address, ignore=()):
        self._reads += 1
        return [h | ({"raw": 2 * h["raw"]} if self._reads >= 3 else {}) for h in self._held]


def test_b2_a_retire_beside_the_owners_coin_is_undone_when_more_of_that_coin_shows(tmp_path, monkeypatch):
    """Each reading of the wallet is weighed on its own amounts: the owner's coin a sniper of ours bought into while
    the retire's stop went out is no longer explained by the owner's buys, so the sniper is started again and nothing
    is deleted (a stopped sniper sells nothing)."""
    from snipetech.book import run as R

    monkeypatch.setattr(R, "RESTART_PAUSE_SECS", 0.0)
    st, cfg = BT._env(tmp_path, enabled=True)
    AB._book_sniper(st)
    BT._coin(st, "c1", BT.NOW - 3000, 400_000_000, 0, 3, leader=BT.L1, sell_slot=10)
    held = _owners_coin_the_bot_once_traded(st)
    BT._fresh_tape(st)
    svc, sent = _MoreLater(st, snipers=[{"id": "k1", "name": BT.K1, "status": "running"}], held=held, cfg=cfg), []
    try:
        _book(tmp_path, svc, cfg, sent)
        assert svc.calls[:2] == [("stop", "k1"), ("start", "k1")] and ("delete", "k1") not in svc.calls, svc.calls
        assert any("retire" in t and "started again" in t for t in sent), sent
    finally:
        st.close()


def test_b2_the_clean_up_leaves_an_ended_sniper_with_no_fee_record_beside_the_owners_coin(tmp_path):
    """An ended sniper can still be selling its last coin (OKTech counts attempts), so the clean-up deletes none while
    the wallet holds a coin of ours. Beside a coin read as the owner's, one with no fee record may be the one that
    bought it: it is left, and the ended sniper on record is deleted."""
    st, cfg = BT._env(tmp_path, enabled=True)
    old = "snipetech copy book OLDWAL arm:control"
    AB._book_sniper(st, sid="e1", name=old)  # on record, with its fees
    held = _owners_coin_the_bot_once_traded(st)
    BT._fresh_tape(st)
    st.set_coverage("book:ended", {"ids": ["e1", "e2"], "ts": int(BT.NOW) - 300})
    snipers = [{"id": "e1", "name": old, "status": "finished"},
               {"id": "e2", "name": "snipetech copy book GONE22 arm:control", "status": "finished"}]  # fmt: skip
    svc = BT.FakeSvc(st, snipers=snipers, held=held, cfg=cfg)
    try:
        _book(tmp_path, svc, cfg)
        assert ("delete", "e1") in svc.calls and ("delete", "e2") not in svc.calls, svc.calls
    finally:
        st.close()


# -- B3: the gate and the strays the watch says once a day ------------------------------------------------------------


def _round_world(tmp_path):
    """A store with one sniper on record, the config and the operator's paths, as the round's own tests build them."""
    from tests import test_audit_round as AR

    st = Store(tmp_path / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    cfg.operator.wallet = AR.W
    _sniper(st, created=0)
    return AR, st, cfg, OperatorPaths(tmp_path).ensure()


def _wakes(paths, snap, prev, now):
    """The gate's held-coin and unmanaged reasons for this round (the states that buy a paid operator turn)."""
    from snipetech.config import OperatorConfig
    from snipetech.operator.gate import decide

    return [r for r in decide(snap, prev, now, OperatorConfig(), paths).reasons if "held" in r or "unmanaged" in r]


def test_b3_our_sold_copy_with_a_few_unpriced_tokens_left_wakes_no_paid_round(tmp_path):
    """R-book-0: a copy into an older PumpSwap coin, bought and sold off the tape, can leave a few tokens the tape
    cannot price, so they are never dust. The round's snapshot holds them as the bot's (its buy is in the fills), and
    the watch says them once a day as our sold copy. The gate raised "1 coin(s) held", "unmanaged: 1 coin(s) nobody is
    selling" (hourly) and "held over 30 min" for them, each a paid operator turn, for as long as the dust sat there."""
    from snipetech.book.watch import watch_pass

    AR, st, cfg, paths = _round_world(tmp_path)
    try:
        AR.tick(st, 995)
        a = AR.take(st, cfg, paths, None, 1000.0, 10.0, [])
        AR.put_fill(st, "pb", 1200, AR.SWAP, "buy", 3.5, AR.BOT_BUY_FEES, tokens=1e9)
        AR.put_fill(st, "ps", 1212, AR.SWAP, "sell", 3.4, AR.BOT_SELL_FEES, tokens=1e9 - 4000, buy_sig="pb")
        left = AR.unpriced(AR.SWAP) | {"amount": 0.004, "raw": 4000}  # what the sale left: never priced, never dust
        AR.tick(st, 1895)
        b = AR.take(st, cfg, paths, a, 1900.0, 9.9, [left])
        assert AR.whose(b) == {AR.SWAP: "ours"}
        sent: list[str] = []
        note = Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))
        watch_pass(cfg, st, note, AR.W, [left], [], 1900.0)
        AR.tick(st, 2295)
        (hit,) = watch_pass(cfg, st, note, AR.W, [left], [], 2300.0)
        assert hit["stray"] and hit["sold_copy"] and len(sent) == 1 and "our copy of it was sold" in sent[0]
        AR.tick(st, 2795)
        c = AR.take(st, cfg, paths, b, 2800.0, 9.9, [left])
        assert _wakes(paths, c, b, 2800.0) == []
        AR.tick(st, 6395)
        watch_pass(cfg, st, note, AR.W, [left], [], 6300.0)
        d = AR.take(st, cfg, paths, c, 6400.0, 9.9, [left])  # an hour on: no "held over 30 min" either
        assert _wakes(paths, d, c, 6400.0) == [] and len(sent) == 1
    finally:
        st.close()


def test_b3_a_stray_the_watch_calls_the_owners_or_our_sold_copy_is_never_unmanaged(tmp_path):
    """The gate counts a stray as unmanaged only when the round holds its coin as the bot's (a buy of ours on record
    the watch did not see). A hit the watch itself calls the owner's own (a coin both bought: the owner's part is what
    is left) or our sold copy says who holds what is left: nobody is failing to sell it. A plain stray of a coin the
    round holds as the bot's still counts, and so does every coin the watch does not call a stray."""
    from tests import test_audit_round as AR

    paths = OperatorPaths(tmp_path).ensure()
    now = 1_795_321_800.0
    paths.expected.write_text('{"snipers_running": 0, "held": 1}')
    held = [AR.coin(AR.SWAP, 0.05) | {"since": now - 600, "whose": "ours", "ours": True}]
    snap = AR._held_snap(now, held)
    for flags, said in (({"owner": True}, []), ({"sold_copy": True}, []),
                        ({}, ["unmanaged: 1 coin(s) nobody is selling (operator/watch.json)"])):  # fmt: skip
        AR._watch(paths, now, [{"mint": AR.SWAP, "reason": "not_on_tape", "stray": True} | flags])
        assert _wakes(paths, snap, snap, now) == said, flags
    AR._watch(paths, now, [{"mint": AR.SWAP, "reason": "past_timer"}])  # a coin of ours nobody sells: as always
    assert _wakes(paths, snap, snap, now) == ["unmanaged: 1 coin(s) nobody is selling (operator/watch.json)"]


# -- B5: what the watch says of a coin with no buy of ours on the tape ------------------------------------------------

PSWAP = "Pswp" + "p" * 40  # a coin on PumpSwap the tape does not record


def _watch_env(tmp_path, measured):
    """The book's test store with its sniper on record, a current tape, and the measure stage last run at `measured`."""
    st, cfg = BT._env(tmp_path)
    AB._book_sniper(st)
    BT._fresh_tape(st)
    st.set_coverage("measure:last_ts", {"ts": int(measured)})
    return st, cfg


def _copy_fill(st, ts, tokens=1e9, sold=None):
    """Our copy of PSWAP as the measure stage reads it off the chain into fills, and (given `sold`) its paired sale."""
    buy = "PBUY" + "b" * 84
    st.put_fill({"sig": buy, "ts": int(ts), "wallet": W, "wallet_copied": BT.L1, "mint": PSWAP, "side": "buy",
                 "sol": 0.2, "tokens": tokens, "priority_lamports": AB.BOT_FEES[0],
                 "tip_lamports": AB.BOT_FEES[1]})  # fmt: skip
    if sold is not None:
        st.put_fill({"sig": "PSELL" + "s" * 83, "ts": int(ts) + 12, "wallet": W, "mint": PSWAP, "side": "sell",
                     "sol": 0.2, "tokens": sold, "priority_lamports": AB.BOT_SALE_FEES[0],
                     "tip_lamports": AB.BOT_SALE_FEES[1], "buy_sig": buy})  # fmt: skip


def test_b5_a_coin_is_not_called_a_stray_for_sure_before_the_measure_stage_read_past_it(tmp_path):
    """R-round-7: "it is not a coin the book bought, so no sniper of ours will sell it" was said of our own copy into a
    PumpSwap coin the tape never holds. The watch reads such a buy in the measure stage's fills since the first round;
    what was left: said before that stage had run past the coin's first sighting (it was late, or stood still), the
    sentence was still wrong, and for a day. Until then the line says what is known; once the fills hold our buy the
    coin gets the alert with its /sell line, the day's stray line notwithstanding."""
    from snipetech.book.watch import watch_pass

    st, cfg = _watch_env(tmp_path, measured=BT.NOW - 900)  # the measure stage last ran before the coin showed
    AB._seen(tmp_path, PSWAP, BT.NOW - 400)
    held = [{"mint": PSWAP, "amount": 1000.0, "raw": 10**9, "value_sol": None, "priced": False}]
    sent: list[str] = []
    try:
        (hit,) = watch_pass(cfg, st, AB._notifier(sent), W, held, [], BT.NOW)
        assert hit["stray"] and hit["unread"] and len(sent) == 1
        assert "probably not a coin the book bought" in sent[0] and "has not run since it showed" in sent[0], sent
        assert "no sniper of ours will sell it" not in sent[0]
        assert st.events(kind="unmanaged") == []  # a stray is no position of ours for the weekly evidence
        _copy_fill(st, BT.NOW - 430)  # the measure stage runs: the coin is our copy, still open
        st.set_coverage("measure:last_ts", {"ts": int(BT.NOW) + 30})
        (hit,) = watch_pass(cfg, st, AB._notifier(sent), W, held, [], BT.NOW + 60)
        assert not hit["stray"] and len(sent) == 2, sent
        assert "Nobody is selling coin " + PSWAP in sent[1] and sent[1].endswith(f"/sell {PSWAP}")
        assert [e["mint"] for e in st.events(kind="unmanaged")] == [PSWAP]  # our copy nobody sells is one
    finally:
        st.close()


def test_b5_once_the_measure_stage_read_past_it_a_coin_no_record_explains_is_said_for_sure(tmp_path):
    """The airdrop of 22 Nov: neither the tape nor the fills hold a buy of the wallet, the tape is current and the
    measure stage has run since the coin showed. The firm sentence stands, once a day."""
    from snipetech.book.watch import watch_pass

    st, cfg = _watch_env(tmp_path, measured=BT.NOW - 60)
    AB._seen(tmp_path, PSWAP, BT.NOW - 400)
    held = [{"mint": PSWAP, "amount": 1000.0, "raw": 10**9, "value_sol": None, "priced": False}]
    sent: list[str] = []
    try:
        (hit,) = watch_pass(cfg, st, AB._notifier(sent), W, held, [], BT.NOW)
        assert hit["stray"] and not hit["unread"] and len(sent) == 1
        assert "it is not a coin the book bought, so no sniper of ours will sell it" in sent[0], sent
        assert "nor the wallet's own trades read off the chain" in sent[0]
    finally:
        st.close()


def test_b5_our_sold_copy_is_said_so_only_of_what_a_sale_leaves_behind(tmp_path):
    """The gate takes the watch's word that a coin is what our sold copy left (B3). That word is given only for a
    leftover: a few tokens beside what the copy bought. The fills showing our copy sold while the wallet holds a whole
    position of the coin (bought again a moment ago, the measure stage not there yet) is a coin nobody may be selling:
    the alert with its /sell line, as for any buy not on record yet."""
    from snipetech.book.watch import watch_pass

    for k, (raw, leftover) in enumerate(((4000, True), (10**9, False))):
        st, cfg = _watch_env(tmp_path / str(k), measured=BT.NOW - 60)
        _copy_fill(st, BT.NOW - 900, tokens=1e9, sold=1e9 - 4000)
        AB._seen(tmp_path / str(k), PSWAP, BT.NOW - 880)
        held = [{"mint": PSWAP, "amount": raw / 1e6, "raw": raw, "value_sol": None, "priced": False}]
        sent: list[str] = []
        try:
            (hit,) = watch_pass(cfg, st, AB._notifier(sent), W, held, [], BT.NOW)
            assert bool(hit.get("sold_copy")) is leftover and bool(hit["stray"]) is leftover, (k, hit)
            if leftover:
                assert "our copy of it was sold" in sent[0] and "/sell" not in sent[0], sent
            else:
                assert "Nobody is selling coin " + PSWAP in sent[0] and sent[0].endswith(f"/sell {PSWAP}"), sent
        finally:
            st.close()


def test_b5_the_book_calls_no_coin_a_stray_before_the_measure_stage_read_past_it(tmp_path):
    """The same gap where it acts: the book (and /stop) call a coin a stray, no holding of any sniper, once the watch
    has seen it ten minutes and no buy of ours is on record. Our copy into a PumpSwap coin the tape does not hold is on
    record only once the measure stage has read it: while that stage stood still, such a copy held ten minutes was a
    stray, and a kill stopped the sniper holding it (rule 1). No coin is a stray before the measure stage has run past
    its first sighting; once it has, and still no buy of ours explains the coin, it is one."""
    st, cfg = BT._env(tmp_path)  # the book is off: its kill path stops its snipers that hold nothing
    AB._book_sniper(st)
    AB._seen(tmp_path, PSWAP, BT.NOW - 1800)  # held half an hour, no buy of it on the tape or in the fills
    held = [{"mint": PSWAP, "amount": 1000.0, "raw": 10**9, "value_sol": None, "priced": False}]
    k1 = [{"id": "k1", "name": BT.K1, "status": "running"}]
    try:
        st.set_coverage("measure:last_ts", {"ts": int(BT.NOW) - 3600})  # the measure stage last ran before it showed
        svc = BT.FakeSvc(st, snipers=k1, held=held, cfg=cfg)
        _book(tmp_path, svc, cfg)
        assert svc.calls == [], svc.calls  # it may be our copy: every book sniper stays
        st.set_coverage("measure:last_ts", {"ts": int(BT.NOW) - 60})  # it ran since, and found no buy of ours
        svc = BT.FakeSvc(st, snipers=k1, held=held, cfg=cfg)
        _book(tmp_path, svc, cfg)
        assert svc.calls == [("stop", "k1")], svc.calls
    finally:
        st.close()


# -- B8: what the book reviewer left ----------------------------------------------------------------------------------


class _LostAnswer(AB._LateCoin):
    """OKTech starts the sniper, and its answer never arrives (the call is cut off at RESTART_SECS, or the connection
    drops): the caller sees a failure, OKTech's own list shows it running."""

    async def oktech_task(self, action, task_id):
        out = await super().oktech_task(action, task_id)
        if action == "start":
            raise TimeoutError()
        return out


def test_b8_a_start_whose_answer_was_lost_is_not_told_as_a_failed_restart(tmp_path, monkeypatch):
    """R-book-3: a retire undone by a coin starts its sniper again, each try cut off at 45 s. When OKTech started it
    and the answer came too late, the owner was told the start had failed and nothing sold the coin, with a /sell
    line, though the sniper ran. After its tries the book reads OKTech's list once: a sniper it shows running was
    started, and nothing is left to start at the next run."""
    from snipetech.book import run as R

    monkeypatch.setattr(R, "RESTART_PAUSE_SECS", 0.0)
    st, cfg, _ = BT._made_its_buy(tmp_path)
    svc = _LostAnswer(st, snipers=[{"id": "k1", "name": BT.K1, "status": "running"}], cfg=cfg)
    sent: list[str] = []
    try:
        _book(tmp_path, svc, cfg, sent)
        assert ("stop", "k1") in svc.calls and ("delete", "k1") not in svc.calls, svc.calls
        (told,) = [t for t in sent if "retire" in t and "k1" in t]
        assert "started again so it can sell it" in told and "failed" not in told and "/sell" not in told, told
        assert not (st.coverage(R.RESTART_KEY) or {})
    finally:
        st.close()


def test_b8_the_failed_restart_line_promises_only_what_the_next_run_does(tmp_path, monkeypatch):
    """R-book-3: the line told the owner "the next run tries to start it again". The plan starts such a sniper only
    while the book still copies its wallet and no stop holds: running, it also buys for that wallet, so starting one
    the book dropped, or under a stop, is the owner's call (the watch's alert and /sell cover the coin). The line says
    so, and the plan's rule is pinned here."""
    from snipetech.book import run as R
    from snipetech.book.policy import BookInputs, plan_book

    monkeypatch.setattr(R, "RESTART_PAUSE_SECS", 0.0)
    st, cfg, _ = BT._made_its_buy(tmp_path)
    svc = AB._LateCoin(st, snipers=[{"id": "k1", "name": BT.K1, "status": "running"}], cfg=cfg, fail={("start", "k1")})
    sent: list[str] = []
    try:
        _book(tmp_path, svc, cfg, sent)
        (told,) = [t for t in sent if "retire" in t and "k1" in t]
        assert "unless the book no longer copies L1xxxx by then or a stop holds" in told, told
        assert told.endswith(f"/sell {AB.LATE[0]['mint']}")
    finally:
        st.close()

    def plan(qualified, **kw):
        inp = BookInputs(equity_sol=10, day_realised_sol=0, day_risk_used_sol=0, qualified=qualified, live={},
                         bid_model={}, snipers=[{"id": "k1", "name": BT.K1, "status": "idle"}],
                         holdings=[{"mint": AB.LATE[0]["mint"], "amount": 1.0, "value_sol": 0.2}], now=BT.NOW,
                         holdings_known=True, buys_by_sniper={"k1": 1}, native_sol=10, wsol=0, restart={"k1"},
                         **kw)  # fmt: skip
        return [a.kind for a in plan_book(inp, Config().book).actions if a.sniper_id == "k1"]

    ranked = [{"wallet": BT.L1, "score": 0.03, "copy_plan": {"min_leader_sol": 0.3}}]
    assert plan(ranked) == ["start"]  # still copied, a coin held: started so it can sell it
    assert plan([]) == []  # its wallet left the ranking: not started (the owner's call)
    assert plan(ranked, stopped=True) == []  # under the owner's /stop nothing starts
