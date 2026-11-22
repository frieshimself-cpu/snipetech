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
