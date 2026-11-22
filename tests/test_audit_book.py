"""The audit of 22 Nov 2026, area "book": each finding's reproduction, kept as a test (book#N is the finding's id).

The sniper wallet is the owner's too: they buy in it by hand (22 Nov 02:40 UTC: three hand buys of one coin, 10.4 SOL,
at fees no sniper of ours pays). The runner tells such a coin apart by its fees (measure/ours.py); the book, its buy
count and the unsold-coin watch did not (book#0)."""

import asyncio
import json

from snipetech.book.run import run_book
from snipetech.models import Trade
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from tests.test_book_run import K1, L1, NOW, FakeSvc, W, _coin, _env, _fresh_tape

OWNER_COIN = "Hand" + "h" * 40
OWNER_FEES = (8175, 51_991_825)  # the 22 Nov hand buys' priority and tip, in lamports: no sniper of ours pays them
BOOK_PAYLOAD = {"mode": "ctbot", "ctbotWallets": [{"address": L1}], "priorityFee": 0.001, "tipAmount": 0.002,
                "autoSell": {"sellTipAmount": 0.002, "sellPriorityFeeSol": 0.0001}}  # fmt: skip


def _book_sniper(st, sid="k1", name=K1, created_at=NOW - 3600):
    """The book's sniper on L1, with the payload we sent OKTech: its fees are what tells its buys from the owner's."""
    st.add_oktech_sniper({"id": sid, "external_id": f"e-{sid}", "kind": "copy", "source": L1, "name": name,
                          "config_hash": "h", "payload": BOOK_PAYLOAD, "created_at": created_at})  # fmt: skip


def _hand_buy_on_tape(st, ts, mint=OWNER_COIN, fees=OWNER_FEES, sig="HANDBUY"):
    st.add_trades([Trade(sig=sig + "x" * 80, ev=0, slot=400_500_000, pos=3, ts=int(ts), mint=mint, venue="curve", user=W,
                         is_buy=True, quote=4_950_000_000, base=10**12, fee=60_000_000, fee_bps=125, q_after=40 * 10**9,
                         b_after=800 * 10**12, priority=fees[0], tip=fees[1])])  # fmt: skip


def _hand_buy_in_fills(st, ts, mint=OWNER_COIN):
    """The 22 Nov shape: a buy in a pool the tape does not record, read off the chain by the measure stage."""
    st.put_fill({"sig": "36vtBUj9ki" + "x" * 78, "ts": int(ts), "wallet": W, "wallet_copied": None, "mint": mint,
                 "side": "buy", "sol": 4.95, "tokens": 1e6, "priority_lamports": OWNER_FEES[0],
                 "tip_lamports": OWNER_FEES[1], "sniper_id": None})  # fmt: skip


def _seen(tmp_path, mint, since):
    (OperatorPaths(tmp_path / "data").ensure().root / "watch.json").write_text(
        json.dumps({"first_seen": {mint: since}})
    )


def _notifier(sent):
    return Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))


# -- book#0: a coin the owner bought by hand is no coin of the book's ---------------------------------------------------


def test_book0_a_buy_explains_a_coin_only_when_it_is_the_bots(tmp_path):
    """Only a buy of the bot's explains a held coin: the fees a sniper of ours pays, a sniper or a copied wallet the
    measure stage tied the buy to, or fees nobody recorded. The owner's own buy at other fees explains nothing, and a
    fill of another wallet is no buy of this one."""
    from snipetech.book.run import _bought_by_us

    st, _cfg = _env(tmp_path)
    try:
        assert _bought_by_us(st, W, OWNER_COIN) is False  # nothing bought it at all
        _hand_buy_on_tape(st, NOW - 600)
        assert _bought_by_us(st, W, OWNER_COIN) is True  # no sniper on record: nothing tells a hand buy apart
        _book_sniper(st)
        assert _bought_by_us(st, W, OWNER_COIN) is False  # the owner's fees: not a buy of ours
        _hand_buy_on_tape(st, NOW - 500, fees=(1_000_000, 2_000_000), sig="BOTBUY")  # a sniper's fees
        assert _bought_by_us(st, W, OWNER_COIN) is True
        _hand_buy_on_tape(st, NOW - 500, mint="Unkn" + "u" * 40, fees=(None, None), sig="NOFEES")  # fees not recorded
        assert _bought_by_us(st, W, "Unkn" + "u" * 40) is True
        off = "Fill" + "f" * 40
        _hand_buy_in_fills(st, NOW - 600, mint=off)
        assert _bought_by_us(st, W, off) is False  # the 22 Nov hand buy, read off the chain into fills
        st.put_fill({"sig": "COPY" + "c" * 84, "ts": int(NOW) - 300, "wallet": W, "wallet_copied": L1, "mint": off,
                     "side": "buy", "sol": 0.2, "tokens": 1e6, "priority_lamports": 1_000_000,
                     "tip_lamports": 2_000_000})  # fmt: skip
        assert _bought_by_us(st, W, off) is True  # a copy of ours of the same coin, off the tape
        other = "Othr" + "o" * 40
        st.put_fill({"sig": "ELSE" + "e" * 84, "ts": int(NOW) - 300, "wallet": "Else" + "e" * 40, "mint": other,
                     "side": "buy", "sol": 0.2, "tokens": 1e6, "sniper_id": "k1"})  # fmt: skip
        assert _bought_by_us(st, W, other) is False  # another wallet's fill says nothing about this one's coin
    finally:
        st.close()


def test_book0_the_owners_hand_bought_coin_never_keeps_the_books_snipers_under_a_kill(tmp_path):
    """22 Nov audit: while the owner kept a coin bought by hand, /book off (and the guard, a day stop, /stop) kept every
    book sniper armed "never stop a holder", each still able to buy to its cap with the owner's SOL. A coin whose only
    buy paid fees no sniper of ours pays is the owner's: seen for ten minutes it is no holding of the book's, on the
    tape or only in fills (the 22 Nov hand buys were in a pool the tape does not record)."""
    for k, where in enumerate(("tape", "fills")):
        st, cfg = _env(tmp_path / str(k))  # the book is off: its kill path stops its snipers that hold nothing
        _book_sniper(st)
        (_hand_buy_on_tape if where == "tape" else _hand_buy_in_fills)(st, NOW - 1800)
        _seen(tmp_path / str(k), OWNER_COIN, NOW - 1800)  # held 30 minutes
        held = [{"mint": OWNER_COIN, "amount": 1e6, "value_sol": 10.2, "priced": True}]
        svc, sent = FakeSvc(st, snipers=[{"id": "k1", "name": K1, "status": "running"}], held=held, cfg=cfg), []
        try:
            reads = (1.0, 0.0, held)
            out = asyncio.run(run_book(svc, cfg, NOW, notifier=_notifier(sent), balances=lambda w, r=reads: r))
            assert out["kill_reason"] == "book off", where
            assert ("stop", "k1") in svc.calls, (where, sent)
            assert not any("kept running while the wallet holds a coin" in t for t in sent), where
        finally:
            st.close()


def test_book0_the_owners_coin_neither_holds_back_a_retire_and_the_clean_up_nor_counts_as_a_snipers_buy(tmp_path):
    """The book on: k1 made its relied-on buy (bought and sold behind its leader) and the wallet holds only the
    owner's own coin. k1 is retired and replaced, and a sniper that ended is deleted. The owner's buy is no buy of any
    book sniper: counted for each of them (no leader explains it) it would retire every sniper early."""
    from snipetech.book.run import _buys_by_sniper

    st, cfg = _env(tmp_path, enabled=True)
    _book_sniper(st)
    _coin(st, "c1", NOW - 3000, 400_000_000, 0, 3, leader=L1, sell_slot=10)  # k1's relied-on buy, sold
    _hand_buy_on_tape(st, NOW - 1800)
    _fresh_tape(st)
    _seen(tmp_path, OWNER_COIN, NOW - 1800)
    st.set_coverage("book:ended", {"ids": ["e1"], "ts": int(NOW) - 300})  # the run before saw e1 ended too
    held = [{"mint": OWNER_COIN, "amount": 1e6, "value_sol": 10.2, "priced": True}]
    snipers = [{"id": "k1", "name": K1, "status": "running"},
               {"id": "e1", "name": "snipetech copy book OLDWAL arm:control", "status": "finished"}]  # fmt: skip
    svc = FakeSvc(st, snipers=snipers, held=held, cfg=cfg)
    try:
        assert _buys_by_sniper(st, W, snipers) == {"k1": 1}  # its own buy only, not the owner's
        asyncio.run(run_book(svc, cfg, NOW, notifier=_notifier([]), balances=lambda w: (10.0, 0.0, held)))
        assert ("stop", "k1") in svc.calls and ("delete", "k1") in svc.calls, svc.calls
        assert ("delete", "e1") in svc.calls, svc.calls
    finally:
        st.close()


def test_book0_the_watch_tells_the_owners_own_coin_once_a_day_never_as_nobody_selling_it(tmp_path):
    """The watch sent "Nobody is selling coin X ... Sell it now: /sell X" about the owner's own coin every 15 minutes.
    A coin whose open buys on the tape all paid fees no sniper of ours pays is the owner's: one line a day, in words
    that say so. A buy of the bot's still gets the alert."""
    from snipetech.book.watch import watch_pass

    st, cfg = _env(tmp_path)
    _book_sniper(st)
    _hand_buy_on_tape(st, NOW - 1800)
    _fresh_tape(st)
    _seen(tmp_path, OWNER_COIN, NOW - 1800)
    held = [{"mint": OWNER_COIN, "amount": 1e6, "value_sol": 10.2, "priced": True}]
    sent: list[str] = []
    try:
        hits = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW)
        again = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW + 15 * 60)
        assert hits and hits[0]["stray"] is True and again[0]["stray"] is True
        assert len(sent) == 1 and "Nobody is selling" not in sent[0] and "/sell" not in sent[0], sent
        assert "your own" in sent[0] and OWNER_COIN in sent[0]
        bot = "BotC" + "b" * 40  # the same coin shape bought at a sniper's fees: the alert, as before
        _hand_buy_on_tape(st, NOW - 1800, mint=bot, fees=(1_000_000, 2_000_000), sig="BOTBUY")
        _seen(tmp_path, bot, NOW - 1800)
        hits = watch_pass(cfg, st, _notifier(sent), W, [{"mint": bot, "amount": 1e6, "value_sol": 0.2}], [], NOW)
        assert not hits[0].get("stray")
        assert any("Nobody is selling coin " + bot in t and t.endswith(f"/sell {bot}") for t in sent), sent
    finally:
        st.close()
