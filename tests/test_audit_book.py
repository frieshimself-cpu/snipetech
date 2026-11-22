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


MIXED = "Mixd" + "m" * 40  # a coin both the bot and the owner bought
BOT_FEES = (1_000_000, 2_000_000)  # a book sniper's buy: BOOK_PAYLOAD's priority and tip
BOT_SALE_FEES = (1_000_000, 2_000_000)  # what OKTech pays on a book sniper's sale: the buy's priority, the sale's tip


def _tape_trade(st, sig, ts, slot, is_buy, fees, mint=MIXED):
    st.add_trades([Trade(sig=sig + "x" * (88 - len(sig)), ev=0, slot=slot, pos=3, ts=int(ts), mint=mint, venue="curve",
                         user=W, is_buy=is_buy, quote=200_000_000, base=10**12, fee=2_000_000, fee_bps=125,
                         q_after=40 * 10**9, b_after=800 * 10**12, priority=fees[0], tip=fees[1])])  # fmt: skip


def test_book0_review_a_coin_bought_by_both_is_told_by_whose_part_is_left(tmp_path):
    """Review of the book#0 fix: the watch matched each sale of the wallet's to its buys oldest first, whoever paid
    them, then told the coin by the buys left open. The bot bought a coin, the owner bought it too by hand and sold his
    part by hand: his sale closed the bot's older buy, his own buy read open, and the bot's unsold coin was told as "a
    trade of your own", once a day and with no /sell line. The other way round (the owner first, then the bot, whose
    sniper sold its part) the owner's coin was told "Nobody is selling ... Sell it now" every 15 minutes, as before the
    fix. A sale goes to the buys of its own side first (the owner's sale to the owner's buys, the bot's to the bot's)."""
    from snipetech.book.watch import watch_pass

    held = [{"mint": MIXED, "amount": 1e6, "value_sol": 0.2, "priced": True}]
    for k, (first, second, sale, theirs) in enumerate((
        (BOT_FEES, OWNER_FEES, OWNER_FEES, False),  # the bot's part is left: the alert, with its /sell line
        (OWNER_FEES, BOT_FEES, BOT_SALE_FEES, True),  # the owner's part is left: his, once a day
    )):  # fmt: skip
        st, cfg = _env(tmp_path / str(k))
        _book_sniper(st)
        _tape_trade(st, "FIRST", NOW - 1800, 400_500_000, True, first)
        _tape_trade(st, "SECOND", NOW - 1700, 400_500_400, True, second)
        _tape_trade(st, "SALE", NOW - 1600, 400_500_800, False, sale)
        _fresh_tape(st)
        _seen(tmp_path / str(k), MIXED, NOW - 1800)
        sent: list[str] = []
        try:
            hits = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW)
            again = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW + 15 * 60)
            assert hits and bool(hits[0].get("stray")) is theirs and bool(again[0].get("stray")) is theirs, (k, hits)
            if theirs:
                assert len(sent) == 1 and "your own" in sent[0] and "/sell" not in sent[0], sent
            else:
                assert len(sent) == 2 and all("Nobody is selling" in t and t.endswith(f"/sell {MIXED}") for t in sent)
        finally:
            st.close()


# -- book#1: a retire undone by a coin bought while it stopped, and a start again that fails -------------------------

LATE = [{"mint": "Late" + "n" * 40, "amount": 1.0, "value_sol": 0.2, "priced": True}]


class _LateCoin(FakeSvc):
    """The coin shows on the third holdings read: bought while the retire's stop was on its way."""

    async def open_positions(self, address, ignore=()):
        self._reads += 1
        return list(LATE) if self._reads >= 3 else []


class _FlakyStart(_LateCoin):
    """The first start of a sniper fails (a gateway error), the next one goes."""

    async def oktech_task(self, action, task_id):
        if action == "start" and ("start", task_id) not in self.calls:
            self.calls.append((action, task_id))
            raise RuntimeError("502 Bad Gateway")
        return await super().oktech_task(action, task_id)


def _tape_now(st, ts):
    from tests.synth import Market, addr

    m = Market(st, addr(f"beat{ts}"), addr(f"dev-beat{ts}"), int(ts), 401_000_000 + int(ts) % 100_000)
    m.buy(addr("someone"), 0.1, 0)
    m.commit()


def test_book1_a_failed_start_after_an_undone_retire_is_told_and_the_next_run_starts_it(tmp_path, monkeypatch):
    """A retire stops k1 and a coin shows that was bought meanwhile, so k1 is started again to sell it. When that start
    failed (OKTech refused, did not answer, or rate-limited past its retries) the owner was told it was started again,
    and the next run kept the stopped sniper stopped "until the wallet holds nothing": nothing sold the coin. The start
    is tried twice; a failure is told with the coin's /sell line, and the next run starts k1 while the coin is held."""
    from snipetech.book import run as R
    from tests.test_book_run import _made_its_buy

    monkeypatch.setattr(R, "RESTART_PAUSE_SECS", 0.0)
    st, cfg, _ = _made_its_buy(tmp_path)
    svc = _LateCoin(st, snipers=[{"id": "k1", "name": K1, "status": "running"}], cfg=cfg, fail={("start", "k1")})
    sent: list[str] = []
    try:
        asyncio.run(run_book(svc, cfg, NOW, notifier=_notifier(sent), balances=lambda w: (10.0, 0.0, [])))
        assert svc.calls.count(("start", "k1")) == 2 and ("delete", "k1") not in svc.calls, svc.calls
        (told,) = [t for t in sent if "retire" in t and "k1" in t]
        assert "started again" not in told and "failed" in told and told.endswith(f"/sell {LATE[0]['mint']}"), told
        _tape_now(st, NOW + 295)  # the next run five minutes on, the tape current
        nxt = FakeSvc(st, snipers=[{"id": "k1", "name": K1, "status": "idle"}], held=LATE, cfg=cfg)
        asyncio.run(run_book(nxt, cfg, NOW + 300, notifier=_notifier(sent), balances=lambda w: (10.0, 0.0, LATE)))
        assert ("start", "k1") in nxt.calls, nxt.calls
        _tape_now(st, NOW + 595)
        later = FakeSvc(st, snipers=[{"id": "k1", "name": K1, "status": "running"}], held=LATE, cfg=cfg)
        asyncio.run(run_book(later, cfg, NOW + 600, notifier=_notifier(sent), balances=lambda w: (10.0, 0.0, LATE)))
        assert not (st.coverage(R.RESTART_KEY) or {}), "running again: nothing is left to start"
    finally:
        st.close()


def test_book1_a_start_that_goes_on_its_second_try_is_told_as_started_and_leaves_nothing_to_start(
    tmp_path, monkeypatch
):
    from snipetech.book import run as R
    from tests.test_book_run import _made_its_buy

    monkeypatch.setattr(R, "RESTART_PAUSE_SECS", 0.0)
    st, cfg, _ = _made_its_buy(tmp_path)
    svc = _FlakyStart(st, snipers=[{"id": "k1", "name": K1, "status": "running"}], cfg=cfg)
    sent: list[str] = []
    try:
        asyncio.run(run_book(svc, cfg, NOW, notifier=_notifier(sent), balances=lambda w: (10.0, 0.0, [])))
        assert svc.calls.count(("start", "k1")) == 2 and ("delete", "k1") not in svc.calls, svc.calls
        (told,) = [t for t in sent if "retire" in t and "k1" in t]
        assert "started again so it can sell it" in told, told
        assert not (st.coverage(R.RESTART_KEY) or {})
    finally:
        st.close()


def test_book1_the_plan_starts_such_a_sniper_only_while_a_coin_is_held():
    """While a coin is held it is started; with the wallet holding nothing it is retired as any spent sniper is; a
    stopped sniper that made its buy and has no failed start on record stays stopped, as before."""
    from snipetech.book.policy import RESTART_REASON, BookInputs, plan_book
    from snipetech.config import Config

    def plan(restart, holdings):
        inp = BookInputs(equity_sol=10, day_realised_sol=0, day_risk_used_sol=0,
                         qualified=[{"wallet": L1, "score": 0.03, "copy_plan": {"min_leader_sol": 0.3}}], live={},
                         bid_model={}, snipers=[{"id": "k1", "name": K1, "status": "idle"}], holdings=holdings,
                         now=NOW, holdings_known=True, buys_by_sniper={"k1": 1}, native_sol=10, wsol=0,
                         restart=restart)  # fmt: skip
        return [(a.kind, a.reason) for a in plan_book(inp, Config().book).actions if a.sniper_id == "k1"]

    coin = [{"mint": LATE[0]["mint"], "amount": 1.0, "value_sol": 0.2}]
    assert plan({"k1"}, coin) == [("start", RESTART_REASON)]
    assert [k for k, _ in plan({"k1"}, [])] == ["retire"]
    assert [k for k, _ in plan(set(), coin)] == ["keep"]


# -- the manual `oktech guard` (book#2, #3, #4, #5) and `oktech start` (book#4) ----------------------------------------

MINT = "Mint1111111111111111111111111111111111111111"
T22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


class _GuardClient:
    """OKTech as the guard sees it: every start and stop in order, the snipers running unless `finished`."""

    def __init__(self, ids=("a",)):
        self.calls: list[tuple[str, str]] = []
        self.ids, self.finished = list(ids), False

    async def start(self, i):
        self.calls.append(("start", i))
        return {"task": {"status": "running"}}

    async def stop(self, i):
        self.calls.append(("stop", i))
        return {}

    async def tasks(self):
        return [{"id": i, "status": "finished" if self.finished else "running"} for i in self.ids]

    async def close(self):
        pass


def _guard(monkeypatch, sol, coin=lambda t: None, price=None, says=None, tape=None):
    """`oktech guard` on a fake clock (each sleep moves it): the wallet holds `sol(t)` plain SOL and, while `coin(t)`
    gives a number, that many raw units of MINT (token-2022), priced by the tape at `price` lamports a unit (None:
    never priced). `tape`: (when our sniper bought MINT, when it sold it or None) on the tape, which is what tells a
    coin of ours from an airdrop; None: no trade of ours in it at all. The owner's wallet-wide guard says `says` (None:
    clear; "unknown": cannot tell; any other text: tripped with that reason)."""
    from snipetech.config import Config
    from snipetech.operator.risk_guard import Guard
    from snipetech.service import Service
    from snipetech.store import Store

    clock = {"t": float(NOW)}

    async def sleep(secs):
        clock["t"] += secs

    class Rpc:
        async def call(self, method, params):
            if method == "getBalance":
                return {"value": int(sol(clock["t"]) * 1e9)}
            raw = coin(clock["t"])
            if raw is None or params[1]["programId"] != T22:
                return {"value": []}
            info = {"mint": MINT, "tokenAmount": {"uiAmount": raw / 1e6, "amount": str(raw), "decimals": 6}}
            return {"value": [{"pubkey": "acct", "account": {"data": {"parsed": {"info": info}}}}]}

    store = Store(":memory:")
    for i in ("a", "b"):
        store.add_oktech_sniper(
            {"id": i, "kind": "copy", "source": L1, "payload": {"singleSnipe": True, "totalSnipes": 3}}
        )
    if price is not None:
        monkeypatch.setattr(store, "last_price", lambda mint: price)
    for k, ts in enumerate(t for t in tape or () if t is not None):  # our buy, then our sale
        store.add_trades([Trade(sig=f"OURS{k}" + "o" * 80, ev=0, slot=400_600_000 + k, pos=1, ts=int(ts), mint=MINT,
                                venue="curve", user=W, is_buy=k == 0, quote=10**8, base=10**8, fee=0, fee_bps=125,
                                q_after=40 * 10**9, b_after=800 * 10**12)])  # fmt: skip
    svc, client = Service(Config(), store=store), _GuardClient()
    svc._rpc = Rpc()
    svc.cfg.operator.wallet = W

    def risk_guard(address, s, held):
        return Guard(tripped=says not in (None, "unknown"), known=says != "unknown",
                     reason="the guard cannot tell: the fills could not be read" if says == "unknown" else (says or ""),
                     kinds=() if says in (None, "unknown") else ("floor",), day=None, equity_sol=s, day_stop_sol=1.0,
                     floor_sol=10.0, owner_set=False)  # fmt: skip

    monkeypatch.setattr(svc, "_oktech", lambda: client)
    monkeypatch.setattr(svc, "risk_guard", risk_guard)
    monkeypatch.setattr(svc, "stop_flag_set", lambda: False)
    monkeypatch.setattr("snipetech.service.asyncio.sleep", sleep)
    monkeypatch.setattr("snipetech.service.time.time", lambda: clock["t"])
    return svc, store, client, clock


def test_book2_a_curve_buy_from_a_small_wallet_is_no_emergency_and_its_sniper_keeps_selling(monkeypatch):
    """0.255 plain SOL, floor 0.1, one 0.2 SOL curve buy: the plain SOL falls to 0.048 (a curve buy is paid in plain
    SOL) and the coin is worth 0.198. The guard called that an EMERGENCY "below the floor" (its 0.05 SOL reserve on
    the plain SOL), stopped the sniper holding the fresh coin and said to sell it by hand. Equity is 0.246: nothing is
    wrong, nothing is stopped, and at its time limit the guard leaves the sniper running while it holds the coin."""
    bought = NOW + 10
    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 0.255 if t < bought else 0.048,
                                   coin=lambda t: None if t < bought else 198_000_000, price=1.0, tape=(bought, None))  # fmt: skip
    rep = asyncio.run(svc.oktech_guard(["a"], W, 0.1, 2))
    assert ("stop", "a") not in client.calls, rep["reason"]
    assert "EMERGENCY" not in rep["reason"] and rep["reason"].startswith("time limit"), rep["reason"]
    assert not rep["stopped"] and "keep running" in rep["reason"]
    store.close()


def test_book2_a_wallet_too_poor_for_a_sale_is_said_never_an_emergency(monkeypatch):
    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 0.003, coin=lambda t: 900_000_000, price=1.0,
                                   tape=(NOW - 30, None))  # fmt: skip
    rep = asyncio.run(svc.oktech_guard(["a"], W, 0.1, 1))
    assert ("stop", "a") not in client.calls and "EMERGENCY" not in rep["reason"]
    assert sum("a sale may fail" in e for e in rep["events"]) == 1  # said once, not every reading
    store.close()


def test_book2_a_real_floor_breach_never_stops_a_sniper_holding_a_coin(monkeypatch):
    """Rule 1 (CLAUDE.md): a stopped sniper strands its coin. Under the floor with a coin held the guard stopped every
    sniper and said to sell the coin by hand. Now it keeps them running, watches the wallet and stops them once it
    holds nothing (here when the coin is sold, 200 s in); with nothing held it stops them at once."""
    sold = NOW + 200
    svc, store, client, clock = _guard(monkeypatch, sol=lambda t: 1.0 if t < NOW + 10 else 0.3,
                                       coin=lambda t: 100_000_000 if t < sold else None, price=1.0,
                                       tape=(NOW - 30, sold))  # fmt: skip
    stopped_at = []
    stop = client.stop

    async def timed_stop(i):
        stopped_at.append(clock["t"])
        return await stop(i)

    client.stop = timed_stop
    rep = asyncio.run(svc.oktech_guard(["a", "b"], W, 0.5, 5))  # equity 1.1, then 0.4 (0.3 SOL and a 0.1 coin)
    assert "EMERGENCY" in rep["reason"] and "holds nothing" in rep["reason"], rep["reason"]
    assert [c for c in client.calls if c[0] == "stop"] == [("stop", "a"), ("stop", "b")] and rep["stopped"]
    assert min(stopped_at) >= sold, "a sniper was stopped while the wallet still held its coin"
    store.close()
    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 1.0 if t < NOW + 10 else 0.3)  # nothing held
    rep = asyncio.run(svc.oktech_guard(["a", "b"], W, 0.5, 5))
    assert "EMERGENCY" in rep["reason"] and rep["stopped"] and ("stop", "b") in client.calls, rep["reason"]
    store.close()


def test_book5_at_its_time_limit_the_guard_stops_its_snipers_once_the_wallet_holds_nothing(monkeypatch):
    """`oktech guard --max-minutes`: its help said "stop everything after this long", and at its time limit it stopped
    nothing: two capped snipers stayed armed with nobody watching. Now its end stops them once the wallet holds nothing
    they bought; a coin held is watched until it is sold, and one still held then keeps them running, said with its
    /sell line."""
    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 2.0)
    client.ids = ["a", "b"]
    rep = asyncio.run(svc.oktech_guard(["a", "b"], W, 0.5, 1))
    assert rep["reason"].startswith("time limit") and rep["stopped"], rep["reason"]
    assert ("stop", "a") in client.calls and ("stop", "b") in client.calls
    store.close()
    sold = NOW + 60 + 120  # sold by its sniper two minutes after the time limit
    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 2.0, coin=lambda t: 100_000_000 if t < sold else None,
                                   price=1.0, tape=(NOW - 30, sold))  # fmt: skip
    rep = asyncio.run(svc.oktech_guard(["a"], W, 0.5, 1))
    assert rep["stopped"] and "holds nothing" in rep["reason"], rep["reason"]
    store.close()
    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 2.0, coin=lambda t: 100_000_000, price=1.0,
                                   tape=(NOW - 30, None))  # fmt: skip
    rep = asyncio.run(svc.oktech_guard(["a"], W, 0.5, 1))
    assert not rep["stopped"] and ("stop", "a") not in client.calls and "keep running" in rep["reason"], rep["reason"]
    assert any(e.endswith(f"/sell {MINT}") for e in rep["events"]), rep["events"]
    store.close()


def test_book5_a_guard_told_to_stop_stops_its_snipers_on_its_way_out_unless_a_coin_is_held(monkeypatch):
    """A kill or the job's limit (SIGTERM, then SIGKILL 30 s on) ended the guard silently, its snipers armed and nobody
    watching. Told to stop, it now stops them on its way out while the wallet holds nothing, and leaves them running
    beside a coin (a stopped sniper strands its coin)."""
    import signal

    import pytest

    for held in (False, True):
        told = {"done": False}

        def sol(t, told=told):
            if t >= NOW + 30 and not told["done"]:
                told["done"] = True
                assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL, "no handler for the job's stop"
                raise asyncio.CancelledError  # what the handler does: the guard's task is cancelled at its next wait
            return 2.0

        svc, store, client, _ = _guard(monkeypatch, sol=sol, coin=(lambda t: 100_000_000) if held else (lambda t: None),
                                       price=1.0, tape=(NOW - 30, None) if held else None)  # fmt: skip
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(svc.oktech_guard(["a"], W, 0.5, 60))
        assert (("stop", "a") in client.calls) is not held, (held, client.calls)
        store.close()


def test_book3_the_floor_holds_beside_an_airdrop_the_tape_never_prices_and_reads_the_owners_ignore_list(monkeypatch):
    """22 Nov 03:58 to ~09:00: 1,000 tokens of an airdrop the tape never priced sat in the wallet. The guard waited for
    the tape to price any unpriced coin before it called its floor, for as long as the coin stayed, and did not read the
    owner's ignore_mints. Now an unpriced coin holds the floor back GUARD_UNPRICED_WAIT_SECS after the guard first saw
    it, then counts as worth 0 SOL; a coin on the ignore list is no coin at all."""
    from snipetech.book.run import STRAY_AFTER_SECS
    from snipetech.service import Service

    loss = NOW + 10  # the SOL falls from 2.9 to 0.5: a real loss, nothing bought

    def sol(t):
        return 2.9 if t < loss else 0.5

    svc, store, client, _ = _guard(monkeypatch, sol=sol, coin=lambda t: 1_000_000_000)  # never priced
    svc.cfg.operator.ignore_mints = [MINT]
    rep = asyncio.run(svc.oktech_guard(["a"], W, 1.2, 30))
    assert "EMERGENCY" in rep["reason"] and rep["stopped"], rep["reason"]
    assert not any("not priced" in e or "unpriced" in e for e in rep["events"][:-2])  # never waited on
    store.close()
    svc, store, client, clock = _guard(monkeypatch, sol=sol, coin=lambda t: 1_000_000_000)
    stopped_at = []
    stop = client.stop

    async def timed_stop(i):
        stopped_at.append(clock["t"])
        return await stop(i)

    client.stop = timed_stop
    rep = asyncio.run(svc.oktech_guard(["a"], W, 1.2, 30))
    assert "EMERGENCY" in rep["reason"] and "not priced" in rep["reason"], rep["reason"]
    # the floor fired once the wait was over, not at once and not never; the airdrop no buy of ours explains kept the
    # sniper running only until it was a stray, as for the book
    assert rep["stopped"] and NOW + STRAY_AFTER_SECS <= stopped_at[0] <= NOW + Service.GUARD_UNPRICED_WAIT_SECS + 900
    store.close()


def test_book4_neither_the_guard_nor_a_manual_start_arms_a_sniper_while_the_owners_wallet_wide_guard_holds(monkeypatch):
    """The owner's wallet-wide guard (operator/RISK: no new live trade once the day is down by its day stop or the
    wallet is under its floor) holds back the cycle, the book and /test; `oktech guard` and `oktech start` started
    snipers all the same. Now both refuse while it holds or cannot tell, as the cycle does; --force (start) still
    starts, as it does under the /stop flag, and a --watch-only guard starts nothing and reads no guard."""
    import pytest

    from snipetech.service import oktech_tasks

    for says in ("the wallet holds 2.00 SOL (SOL and coins), under the 10 SOL floor", "unknown"):
        svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 2.0, says=says)
        with pytest.raises(SystemExit, match="wallet-wide guard"):
            asyncio.run(svc.oktech_guard(["a"], W, 0.5, 1))
        _results, failures = asyncio.run(oktech_tasks(svc, "start", ["a"]))
        assert client.calls == [] and failures and "wallet-wide guard" in failures[0][1], failures
        store.close()
    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 2.0, says="under the floor")
    _results, failures = asyncio.run(oktech_tasks(svc, "start", ["a"], force=True))
    assert client.calls == [("start", "a")] and not failures
    asyncio.run(svc.oktech_guard(["a"], W, 0.5, 0.1, start=False))
    assert [c for c in client.calls if c[0] == "start"] == [("start", "a")]
    store.close()
    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 2.0)  # the guard is clear: started as before
    _results, failures = asyncio.run(oktech_tasks(svc, "start", ["a"]))
    assert client.calls == [("start", "a")] and not failures
    store.close()


def test_book9_a_coin_on_the_owners_ignore_list_refuses_no_manual_stop_and_holds_no_push_back(monkeypatch):
    """`oktech stop` / `delete` and the push's retires read the holdings without the owner's [operator] ignore_mints,
    so an airdrop the owner said to ignore refused every manual stop, and --force (which strands any real coin too) was
    the only way on. The book, the watch and the cycle all leave such a coin out."""
    from snipetech.oktech import SyncPlan
    from snipetech.service import oktech_tasks

    svc, store, client, _ = _guard(monkeypatch, sol=lambda t: 2.0, coin=lambda t: 1_000_000_000)  # an airdrop
    _results, failures = asyncio.run(oktech_tasks(svc, "stop", ["a"]))
    assert failures and "coin(s) held" in failures[0][1] and client.calls == []  # not ignored: refused, as before
    svc.cfg.operator.ignore_mints = [MINT]
    _results, failures = asyncio.run(oktech_tasks(svc, "stop", ["a"]))
    assert not failures and client.calls == [("stop", "a")], failures
    seen = {}

    async def apply(client, store, sync, wallet, prune=False, held=None):
        seen["held"] = held
        return []

    monkeypatch.setattr("snipetech.oktech.apply", apply)
    asyncio.run(svc.oktech_push({"_sync": SyncPlan()}))
    assert seen["held"] == []  # the ignored coin holds no retire back
    store.close()


# -- book#6: the book's can-pay rule and OKTech's wrapped-SOL top-up ---------------------------------------------------


def test_book6_no_sniper_is_made_whose_curve_copies_the_wrapped_sol_top_up_leaves_unpaid():
    """The wrap test (21 Nov 21:51-22:04, docs/live-recipe.md): OKTech keeps the wallet's wrapped SOL at 1.1 times the
    buy, taken from plain SOL, the whole time a sniper runs, whatever its platforms; a curve buy is paid in plain SOL
    only. With 0.30 plain SOL and none wrapped the book made a 0.2 SOL sniper: OKTech wrapped 0.22 and left 0.08, every
    curve copy failed and spent a shot, and the next run read the 0.22 wrapped as a PumpSwap buy and made another. A
    sniper is made only while one curve copy can be paid beside the top-up."""
    from snipetech.book.policy import BookInputs, plan_book
    from snipetech.config import Config

    def made(native, wsol, snipers=()):
        inp = BookInputs(equity_sol=10, day_realised_sol=0, day_risk_used_sol=0,
                         qualified=[{"wallet": L1, "score": 0.03, "copy_plan": {"min_leader_sol": 0.3}}], live={},
                         bid_model={}, snipers=list(snipers), holdings=[], now=NOW, holdings_known=True,
                         native_sol=native, wsol=wsol)  # fmt: skip
        plan = plan_book(inp, Config().book)
        return [a for a in plan.actions if a.kind == "create"], plan

    creates, plan = made(0.30, 0.0)
    assert not creates and any("cannot pay" in n and "wrapped" in n for n in plan.notes), plan.notes
    assert not made(0.08, 0.22, [{"id": "k1", "name": K1, "status": "finished"}])[0]  # its replacement neither
    assert not made(0.005, 0.5)[0]  # wrapped SOL pays a PumpSwap copy only: every curve copy would fail
    assert made(0.425, 0.0)[0] and not made(0.424, 0.0)[0]  # 0.22 is wrapped from it, 0.205 pays a curve copy
    assert made(0.205, 0.22)[0]  # the top-up is there already: the plain SOL pays the curve copy
    assert made(None, 0.0)[0]  # a balance that was not read says nothing, as before


def test_book6_review_book_status_says_what_plain_sol_one_copy_needs(tmp_path):
    """Review of the book#6 fix: with 0.30 SOL in the wallet and none wrapped the book makes no 0.2 SOL sniper (OKTech
    would wrap 0.22 of it at the start), and /book status said only "the wallet cannot pay a 0.2 SOL buy", which reads
    as false to an owner who sees 0.30 SOL there. It says what one copy needs in plain SOL, and why."""
    from snipetech.book.state import last_run_line
    from tests.test_book_run import _floor_off, _live

    st, cfg = _env(tmp_path, enabled=True)
    _floor_off(tmp_path)
    svc = FakeSvc(st, cfg=cfg)
    try:
        _live(svc, cfg, [], balances=(0.30, 0.0, []))
        assert not [c for c in svc.calls if c[0] == "create"], svc.calls
        line = last_run_line(st)
        assert "cannot pay a 0.2 SOL buy" in line and "0.425 SOL of plain SOL" in line and "OKTech wraps" in line, line
        _live(FakeSvc(st, cfg=cfg), cfg, [], balances=(0.21, 0.22, []))  # the wrapped SOL is there already
        assert "0.425" not in last_run_line(st)  # the copies are made: nothing waits
    finally:
        st.close()


# -- book#7: our own copy that only the measure stage's fills hold -----------------------------------------------------


def test_book7_our_copy_the_tape_does_not_hold_is_watched_as_ours_never_called_an_airdrop(tmp_path):
    """The tape keeps a coin's PumpSwap trades only a while after it leaves its curve, so a copy into an older PumpSwap
    coin is read off the chain into fills by the measure stage (3 of the 19 copy buys since 16 Nov on the server). Left
    unsold, the watch called it "not a coin the book bought ... tell me to ignore it", once a day. A buy of the bot's in
    fills keeps the coin ours: the 15-minute alert, with the wallet it copied and its /sell line. The owner's own buy
    in fills, at fees no sniper of ours pays, keeps the once-a-day line, in words that say it is theirs."""
    from snipetech.book.watch import watch_pass

    coin = "Bivu" + "p" * 40
    st, cfg = _env(tmp_path / "a")
    _book_sniper(st)
    _fresh_tape(st)
    st.put_fill({"sig": "BUYSIG" + "b" * 80, "ts": int(NOW) - 900, "wallet": W, "wallet_copied": L1, "mint": coin,
                 "side": "buy", "sol": 0.05, "tokens": 1e6, "priority_lamports": 1_000_000,
                 "tip_lamports": 2_000_000})  # fmt: skip
    _seen(tmp_path / "a", coin, NOW - 880)
    held = [{"mint": coin, "amount": 1e6, "value_sol": None, "priced": False}]
    sent: list[str] = []
    try:
        hits = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW)
        again = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW + 15 * 60)
        assert not hits[0]["stray"] and hits[0]["leader"] == L1, hits
        assert again[0]["alerted"], again  # said again 15 minutes on, as for any coin of ours nobody sells
        assert len(sent) == 2 and all("Nobody is selling" in t and t.endswith(f"/sell {coin}") for t in sent), sent
        assert f"copying wallet {L1[:6]}" in sent[0] and "not a coin the book bought" not in sent[0]
    finally:
        st.close()
    st, cfg = _env(tmp_path / "b")
    _book_sniper(st)
    _fresh_tape(st)
    _hand_buy_in_fills(st, NOW - 900, mint=coin)
    _seen(tmp_path / "b", coin, NOW - 880)
    sent = []
    try:
        hits = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW)
        assert hits[0]["stray"] and len(sent) == 1 and "your own" in sent[0] and "/sell" not in sent[0], sent
    finally:
        st.close()


def test_book7_review_our_copy_the_fills_show_sold_is_never_told_as_unsold_every_15_minutes(tmp_path):
    """Review of the book#7 fix: any buy of the bot's in fills kept its coin "ours", sold or not. A copy into an older
    PumpSwap coin that OKTech sold (the sale in fills too, paired to the buy) and that left a few tokens the tape cannot
    price, so they never fall under the dust line, was told "Nobody is selling ... Sell it now" every 15 minutes. Our
    buy counts only while our sales paired to it in fills left it more than a few tokens; sold, the coin's line is said
    once a day, in words that say our copy was sold. A part sold (a take-profit stage) is still a coin nobody may be
    selling: the 15-minute alert, as before."""
    from snipetech.book.watch import watch_pass

    coin = "Sold" + "s" * 40
    buy = "BUYSIG" + "b" * 80
    held = [{"mint": coin, "amount": 1.0, "value_sol": None, "priced": False}]
    for k, (sold_share, unsold) in enumerate(((0.999, False), (0.5, True))):
        st, cfg = _env(tmp_path / str(k))
        _book_sniper(st)
        _fresh_tape(st)
        st.put_fill({"sig": buy, "ts": int(NOW) - 900, "wallet": W, "wallet_copied": L1, "mint": coin, "side": "buy",
                     "sol": 0.05, "tokens": 1e9, "priority_lamports": 1_000_000, "tip_lamports": 2_000_000})  # fmt: skip
        st.put_fill({"sig": "SELLSIG" + "s" * 80, "ts": int(NOW) - 880, "wallet": W, "mint": coin, "side": "sell",
                     "sol": 0.05, "tokens": sold_share * 1e9, "priority_lamports": 1_000_000,
                     "tip_lamports": 2_000_000, "buy_sig": buy})  # fmt: skip
        _seen(tmp_path / str(k), coin, NOW - 880)
        sent: list[str] = []
        try:
            hits = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW)
            again = watch_pass(cfg, st, _notifier(sent), W, held, [], NOW + 15 * 60)
            assert bool(hits[0]["stray"]) is not unsold and bool(again[0]["stray"]) is not unsold, (k, hits)
            if unsold:
                assert len(sent) == 2 and all("Nobody is selling" in t and t.endswith(f"/sell {coin}") for t in sent)
            else:
                assert len(sent) == 1 and "our copy of it was sold" in sent[0], sent
                assert "Nobody is selling" not in sent[0] and "not a coin the book bought" not in sent[0], sent
        finally:
            st.close()
