"""The watchdog for coins nobody is selling (plan item 2e). It reads the buying sniper's own exits from its local
payload, our buys and the coin's trades from the tape, and flags a held coin a minute after one of those exits was due
and nothing sold, or once its sniper is gone or unknown and the coin has been held past the timer. It alerts, once per
coin per 15 minutes; it never sells."""

import pytest

from snipetech.book.watch import (
    GRACE_SECS,
    SLOT_SECS,
    WATCH_SECS,
    ExitSpec,
    current_hits,
    due_exits,
    exit_spec,
    stray_line,
    unmanaged,
    watch_pass,
)
from snipetech.config import Config
from snipetech.models import Trade
from snipetech.oktech import list_copy_sniper
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store
from tests.synth import Market, addr

# the coin's first slot: our buy (slot offset 100, 21 s later) lands at 17:09:13 UTC on 6 Nov, as DTAU's did
T0 = 1_793_923_200 + 17 * 3600 + 9 * 60 + 13 - 21
S0 = 380_000_000
W = addr("book wallet")
G = addr("G2oZ leader")
B = 100  # our buy's slot, counted from the coin's first slot


class Market214(Market):
    """Block times at the measured 0.214 s a slot (the synthetic market's default is 0.4)."""

    def _ts(self, slot: int) -> int:
        return self.ts0 + int((slot - self.slot0) * 0.214)


def _sniper(st, leader, sid="paid-g2oz", created=T0 - 3600, **kw):
    """A book sniper exactly as the book builds it (list_copy_sniper), stored locally the way oktech_copylist does."""
    p = list_copy_sniper(leader, "book", 0.3, 0.2, max_buys=2, cfg=Config(), tag="arm:paid", **kw)
    st.add_oktech_sniper({"id": sid, "kind": p.kind, "source": leader, "name": p.name, "payload": p.config,
                          "created_at": created})  # fmt: skip
    return p


def _held(m, ours, value=0.26):
    return {
        "mint": m.mint,
        "amount": ours.base / 1e6,
        "raw": ours.base,
        "decimals": 6,
        "value_sol": value,
        "priced": True,
    }


def dtau(st, *, we_sell_at=None):
    """DTAU on 6 Nov: the leader buys 7.06 SOL and we buy 0.2 one slot later; three small buys, then 21 quiet slots
    from +3; the market trades on while the leader sells 5 % pieces, and at +261 s it sells 99.9 % of its bag in one
    trade. The price never falls 30 % below our entry. Nothing of ours sells unless `we_sell_at` (slots after the buy)."""
    m = Market214(st, addr("DTAU coin"), addr("DTAU dev"), T0, S0)
    m.buy(addr("DTAU dev"), 1.0, 0, sig=m.create_sig)
    for k in range(20):
        m.buy(addr(f"early {k}"), 0.5, 1 + k)
    m.buy(G, 7.06, B - 1)
    ours = m.buy(W, 0.2, B)
    for k in (1, 2, 3):
        m.buy(addr(f"follower {k}"), 0.1, B + k)
    if we_sell_at is not None:
        m.sell(W, 1.0, B + we_sell_at)
    n, slot = 0, B + 24
    while slot < B + 1220:
        n += 1
        if n % 25 == 0:
            m.sell(G, 0.05, slot)
        elif n % 3 == 0:
            m.sell(addr(f"trader {n - 1}"), 0.5, slot)
        else:
            m.buy(addr(f"trader {n}"), 0.12, slot)
        slot += 4
    m.sell(G, 0.999, B + 1220)
    k, slot = 0, B + 1223
    while slot < B + 2700:
        k += 1
        if k % 2:
            m.buy(addr(f"late {k}"), 0.1, slot)
        else:
            m.sell(addr(f"late {k - 1}"), 1.0, slot)
        slot += 3
    m.commit()
    return m, ours


def busy(st, leader, name="busy coin"):
    """A coin that trades every 2 slots at a flat price after `leader` buys and we copy it: no pause, no leader sell,
    no stop-loss; only a timer can make an exit due."""
    m = Market214(st, addr(name), addr(name + " dev"), T0, S0)
    m.buy(addr(name + " dev"), 1.0, 0, sig=m.create_sig)
    m.buy(leader, 2.0, B - 1)
    ours = m.buy(W, 0.2, B)
    for k, slot in enumerate(range(B + 1, B + 2700, 2), start=1):
        trader = addr(f"{name} trader {(k + 1) // 2}")
        if k % 2:
            m.buy(trader, 0.1, slot)
        else:
            m.sell(trader, 1.0, slot)
    m.commit()
    return m, ours


FINISHED = [{"id": "paid-g2oz", "name": "snipetech copy book G2oZle arm:paid", "status": "finished"}]


def test_a_dtau_shaped_position_is_flagged_a_minute_after_its_dead_exit_was_due(tmp_path):
    st = Store(tmp_path / "s.db")
    try:
        _sniper(st, G)
        m, ours = dtau(st)
        held = [_held(m, ours)]
        dead_due = ours.ts + WATCH_SECS + 1.0  # the quiet slots cover OKTech's watch start; staleSeconds 1
        assert unmanaged(st, W, held, FINISHED, dead_due + GRACE_SECS - 2) == []  # OKTech still has its minute
        early = unmanaged(st, W, held, FINISHED, dead_due + GRACE_SECS + 1)
        assert [h["mint"] for h in early] == [m.mint] and [d["exit"] for d in early[0]["due"]] == ["dead_exit"]
        hits = unmanaged(st, W, held, FINISHED, ours.ts + 280)
        assert len(hits) == 1
        h = hits[0]
        assert h["reason"] == "exits_not_acted_on" and h["leader"] == G and h["sniper_id"] == "paid-g2oz"
        assert [d["exit"] for d in h["due"]] == ["dead_exit", "copy_exit"]  # the 300 s timer is not due yet
        assert abs(h["flag_at"] - (ours.ts + 62.5)) < 1.0  # about +62 s: due at +2.5 s, plus OKTech's minute
        assert abs(h["due"][1]["after_secs"] - 261) < 1.0  # the leader's 99.9 % sale, not its 5 % pieces
        assert h["buy_sig"] == ours.sig and h["value_sol"] == 0.26 and abs(h["buy_sol"] - 0.2025) < 1e-9
    finally:
        st.close()


def test_a_sold_or_not_yet_due_position_is_not_flagged(tmp_path):
    st = Store(tmp_path / "s.db")
    try:
        _sniper(st, G)
        # the dead exit fired: our sale 12 slots after the buy; the wallet's balance can still show the coin a moment
        m, ours = dtau(st, we_sell_at=12)
        assert unmanaged(st, W, [_held(m, ours)], FINISHED, ours.ts + 280) == []
        # nothing due: a busy flat coin whose leader holds; its sniper's 300 s timer is the first exit
        running = [{"id": "paid-g2oz", "status": "running"}]
        m2, ours2 = busy(st, G)
        held = [_held(m2, ours2)]
        assert unmanaged(st, W, held, running, ours2.ts + 200) == []
        assert unmanaged(st, W, held, running, ours2.ts + 300 + GRACE_SECS - 1) == []
        late = unmanaged(st, W, held, running, ours2.ts + 300 + GRACE_SECS + 1)
        assert [d["exit"] for d in late[0]["due"]] == ["timer"] and late[0]["reason"] == "exits_not_acted_on"
        assert unmanaged(st, W, [], running, ours2.ts + 1000) == []  # nothing held, nothing to say
    finally:
        st.close()


def test_a_coin_whose_sniper_is_gone_or_unknown_is_flagged(tmp_path):
    st = Store(tmp_path / "s.db")
    try:
        # a sniper with only a stop-loss and the copy exit: on a busy flat coin nothing of its own is ever due, so only
        # whether it still runs decides
        _sniper(st, G, sid="sl-only", exit_params={"stop_loss": 0.3})
        m, ours = busy(st, G)
        held = [_held(m, ours)]
        at = ours.ts + 400
        assert unmanaged(st, W, held, [{"id": "sl-only", "status": "running"}], at) == []
        assert unmanaged(st, W, held, None, at) == []  # OKTech did not answer: no status to judge by
        finished = [{"id": "sl-only", "status": "finished"}]
        assert unmanaged(st, W, held, finished, ours.ts + 300) == []  # not before 360 s
        hits = unmanaged(st, W, held, finished, at)
        assert [h["reason"] for h in hits] == ["sniper_inactive"] and hits[0]["sniper_status"] == "finished"
        gone = unmanaged(st, W, held, [], at)  # deleted on OKTech
        assert [h["reason"] for h in gone] == ["sniper_inactive"] and gone[0]["sniper_status"] == "gone"
        # unknown: no sniper of ours copies the wallet that bought just before us
        m2, ours2 = busy(st, addr("someone else"), name="stray coin")
        held2 = [_held(m2, ours2)]
        assert unmanaged(st, W, held2, [], ours2.ts + 300) == []
        unk = unmanaged(st, W, held2, [], ours2.ts + 400)
        assert [h["reason"] for h in unk] == ["sniper_unknown"] and unk[0]["leader"] is None
        # the tape has no buy of ours for it (the recorder lags or missed it): judged by when the watch first saw it
        lost = addr("unrecorded coin")
        coin = [{"mint": lost, "amount": 1.0, "value_sol": None, "priced": False}]
        assert unmanaged(st, W, coin, [], at, first_seen={lost: at - 100}) == []
        assert [h["reason"] for h in unmanaged(st, W, coin, [], at, first_seen={lost: at - 400})] == ["not_on_tape"]
    finally:
        st.close()


def test_the_buying_sniper_is_the_newest_local_sniper_alive_at_the_buy_copying_the_closest_leader(tmp_path):
    """Two snipers copying one leader from one wallet (the paid and control G2oZ snipers on 6 Nov): the newer is
    named, the other listed. A sniper created after the buy, or deleted before it, did not make it."""
    st = Store(tmp_path / "s.db")
    try:
        m, ours = busy(st, G)
        _sniper(st, G, sid="older", created=ours.ts - 7200)
        _sniper(st, G, sid="newer", created=ours.ts - 3600)
        _sniper(st, G, sid="later", created=ours.ts + 120)  # made two minutes after our buy
        _sniper(st, G, sid="deleted", created=ours.ts - 1800)
        st._exec("UPDATE oktech_snipers SET removed_at=? WHERE id='deleted'", (ours.ts - 600,))
        hits = unmanaged(st, W, [_held(m, ours)], [], ours.ts + 400)  # none of them active on OKTech any more
        assert [(h["sniper_id"], h["also"], h["leader"]) for h in hits] == [("newer", ["older"], G)]
    finally:
        st.close()


def test_two_token_accounts_of_one_coin_are_one_coin(tmp_path):
    st = Store(tmp_path / "s.db")
    try:
        _sniper(st, G)
        m, ours = dtau(st)
        two = [_held(m, ours, value=0.2), _held(m, ours, value=0.06)]
        hits = unmanaged(st, W, two, FINISHED, ours.ts + 280)
        assert [h["mint"] for h in hits] == [m.mint] and abs(hits[0]["value_sol"] - 0.26) < 1e-9
        unpriced = [_held(m, ours, value=0.2), _held(m, ours, value=None)]
        assert unmanaged(st, W, unpriced, FINISHED, ours.ts + 280)[0]["value_sol"] is None
    finally:
        st.close()


H = addr("second leader")


def _sell_tokens(m, user, tokens, slot_offset):
    """Sell exactly `tokens` of `user`'s coins (Market.sell sells a share of all it holds)."""
    out = m.q * tokens // (m.b + tokens)
    m.q, m.b = m.q - out, m.b + tokens
    m.holdings[user] -= tokens
    return m._push(user, m.slot0 + slot_offset, False, out, tokens)


def two_lots(st):
    """Two buys of ours in one coin before the first is sold: we copy G at slot B and H five slots later. Nothing
    trades for 28 slots, then OKTech's dead exit sells exactly the first lot's coins, 33 slots after it. The second
    lot (its sniper's cap-using buy) is never sold; the coin trades on at a flat price."""
    m = Market214(st, addr("two-lot coin"), addr("two-lot dev"), T0, S0)
    m.buy(addr("two-lot dev"), 1.0, 0, sig=m.create_sig)
    for k in range(20):
        m.buy(addr(f"two-lot early {k}"), 0.5, 1 + k)
    m.buy(G, 2.0, B - 1)
    lot1 = m.buy(W, 0.2, B)
    m.buy(H, 2.0, B + 4)
    lot2 = m.buy(W, 0.2, B + 5)
    _sell_tokens(m, W, lot1.base, B + 33)
    for k, slot in enumerate(range(B + 36, B + 2700, 2), start=1):
        trader = addr(f"two-lot trader {(k + 1) // 2}")
        if k % 2:
            m.buy(trader, 0.1, slot)
        else:
            m.sell(trader, 1.0, slot)
    m.commit()
    return m, lot1, lot2


def test_a_second_buy_in_a_coin_is_judged_by_its_own_exits(tmp_path):
    """A sale matched wholly to the first lot does not count as a sale of the second: the second lot's dead exit
    was due at about +2.5 s, so it is flagged about a minute later, as a single DTAU-shaped lot is (not at +360 s)."""
    from snipetech.book.watch import _open_buys

    st = Store(tmp_path / "s.db")
    try:
        _sniper(st, G, sid="copy-g")
        _sniper(st, H, sid="copy-h")
        m, _lot1, lot2 = two_lots(st)
        running = [{"id": "copy-g", "status": "running"}, {"id": "copy-h", "status": "running"}]
        held = [_held(m, lot2)]
        assert unmanaged(st, W, held, running, lot2.ts + 60) == []  # OKTech still has its minute
        hits = unmanaged(st, W, held, running, lot2.ts + 92)
        assert [(h["reason"], h["buy_sig"], h["sniper_id"]) for h in hits] == [
            ("exits_not_acted_on", lot2.sig, "copy-h")
        ]
        assert abs(hits[0]["flag_at"] - (lot2.ts + 62.5)) < 1.0 and hits[0]["due"][0]["exit"] == "dead_exit"
    finally:
        st.close()
    # a sale marks only the lots it took coins from: partly the first, or the first and part of the second
    b1, b2 = _t(0, "us", base=100), _t(5, "us", base=100)
    assert _open_buys([b1, b2, _t(9, "us", False, base=50)]) == [(b1, True), (b2, False)]
    assert _open_buys([b1, b2, _t(9, "us", False, base=150)]) == [(b2, True)]


def test_the_watch_sends_one_line_per_coin_every_15_minutes_and_never_sells(tmp_path):
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    st = Store(cfg.storage.db_path)
    sent: list[str] = []
    notifier = Notifier("T", 7, post=lambda url, data: sent.append(data["text"]))
    paths = OperatorPaths(tmp_path / "data")
    try:
        _sniper(st, G)
        m, ours = dtau(st)
        held = [_held(m, ours, value=0.26)]
        t1 = ours.ts + 280
        hits = watch_pass(cfg, st, notifier, W, held, FINISHED, t1)
        assert len(hits) == 1 and len(sent) == 1
        line = sent[0]
        assert "Nobody is selling" in line and m.mint in line and G[:6] in line and "17:09:13" in line
        assert "no trade for 1 s at 17:09:15" in line and "sold at least 50 % of its coins at 17:13:34" in line
        assert "0.260 SOL" in line and "+28 %" in line
        assert "OKTech or Axiom" in line and f"snipetech remote run sweep --mint {m.mint}" in line
        assert len(watch_pass(cfg, st, notifier, W, held, FINISHED, t1 + 60)) == 1 and len(sent) == 1  # not repeated
        watch_pass(cfg, st, notifier, W, held, FINISHED, t1 + 15 * 60)
        assert len(sent) == 2  # still held 15 minutes later: said again
        assert "Nobody is selling" in paths.notes.read_text()  # a decision note the operator's round reads
        assert [h["mint"] for h in current_hits(paths, t1 + 15 * 60)] == [m.mint]  # what the operator's gate reads
        ev = st.events(kind="unmanaged")
        assert len(ev) == 1 and ev[0]["mint"] == m.mint and ev[0]["wallet"] == G  # one record per position
        assert ev[0]["data"]["reason"] == "exits_not_acted_on"
        assert watch_pass(cfg, st, notifier, W, [], FINISHED, t1 + 16 * 60) == []  # sold by hand
        assert current_hits(paths, t1 + 16 * 60) == [] and len(sent) == 2
    finally:
        st.close()


def test_a_failed_record_neither_repeats_the_alert_nor_is_lost(tmp_path, monkeypatch):
    """The events row is written after the alert and after operator/watch.json: a busy database can delay or fail it
    (the one-minute unit has 2 minutes), but the owner is not told twice and the row is written on a later pass."""
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    st = Store(cfg.storage.db_path)
    sent: list[str] = []
    notifier = Notifier("T", 7, post=lambda url, data: sent.append(data["text"]))
    try:
        _sniper(st, G)
        m, ours = dtau(st)
        held = [_held(m, ours)]
        real = st.add_events

        def killed(rows):  # the unit's 2-minute timeout ends the process while the write waits
            raise SystemExit("timeout")

        def locked(rows):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(st, "add_events", killed)
        with pytest.raises(SystemExit):
            watch_pass(cfg, st, notifier, W, held, FINISHED, ours.ts + 280)
        assert len(sent) == 1
        monkeypatch.setattr(st, "add_events", locked)
        second = watch_pass(cfg, st, notifier, W, held, FINISHED, ours.ts + 340)
        assert len(sent) == 1 and "database is locked" in second[0]["record_error"]  # not told twice
        monkeypatch.setattr(st, "add_events", real)
        watch_pass(cfg, st, notifier, W, held, FINISHED, ours.ts + 400)
        assert len(sent) == 1 and len(st.events(kind="unmanaged")) == 1  # the record, a pass later
    finally:
        st.close()


def test_the_book_can_tell_whether_the_one_minute_watch_is_running(tmp_path):
    """The book's run watches at its end only when the one-minute pass has not (its timer is off or failing)."""
    from snipetech.book.watch import watched_within

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    st = Store(cfg.storage.db_path)
    paths = OperatorPaths(tmp_path / "data")
    try:
        assert not watched_within(paths, T0, 180)  # never ran
        watch_pass(cfg, st, Notifier(None, None), W, [], [], T0)
        assert watched_within(paths, T0 + 120, 180) and not watched_within(paths, T0 + 300, 180)
    finally:
        st.close()


def test_a_coin_the_book_never_bought_is_said_once_a_day_and_an_ignored_coin_never(tmp_path):
    """An airdropped token stays in the wallet: no buy of ours on the tape, never priced. The watch says once a day
    that it is not a coin the book bought (sell it by hand or tell me to ignore it), never every 15 minutes, even when
    it leaves the wallet's holdings for a while and comes back. A coin on [operator] ignore_mints is never said."""
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    st = Store(cfg.storage.db_path)
    sent: list[str] = []
    notifier = Notifier("T", 7, post=lambda url, data: sent.append(data["text"]))
    spam = addr("airdropped spam")
    held = [{"mint": spam, "amount": 1000.0, "raw": 10**9, "decimals": 6, "value_sol": None, "priced": False}]
    try:
        for t in range(T0, T0 + 25 * 3600, 300):  # a pass every 5 minutes for 25 hours
            gone = T0 + 3600 <= t < T0 + 3900  # out of the holdings for a moment (an RPC hiccup, a dust price)
            watch_pass(cfg, st, notifier, W, [] if gone else held, [], t)
        assert len(sent) == 2, f"{len(sent)} alerts in 25 hours"  # when first flagged, then a day later
        assert all(spam in s and "not a coin the book bought" in s and "tell me to ignore it" in s for s in sent)
        assert "remote run sweep" not in sent[0]
        current = stray_line({"mint": spam, "value_sol": 0.0123, "lagging": False})  # the tape is current
        assert "it is not a coin the book bought" in current and "0.012 SOL" in current
        cfg.operator.ignore_mints = [spam]
        assert watch_pass(cfg, st, notifier, W, held, [], T0 + 50 * 3600) == []
        assert len(sent) == 2 and current_hits(OperatorPaths(tmp_path / "data"), T0 + 50 * 3600) == []
    finally:
        st.close()


def test_the_minute_watch_reads_the_holdings_without_the_ignored_coins(tmp_path):
    """watch_wallet asks for the holdings without the owner's ignored coins, so an ignored airdrop neither alerts
    nor makes the one-minute pass ask OKTech for its tasks."""
    import asyncio

    from snipetech.book.watch import watch_wallet

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    cfg.operator.wallet = W
    st = Store(cfg.storage.db_path)
    spam = addr("airdropped spam")
    cfg.operator.ignore_mints = [spam]

    class Svc:
        store = st

        def __init__(self):
            self.ignored, self.status_calls = None, 0

        async def open_positions(self, address, ignore=()):
            self.ignored = list(ignore or ())
            coins = [{"mint": spam, "amount": 1.0, "raw": 10**6, "value_sol": None, "priced": False}]
            return [h for h in coins if h["mint"] not in set(ignore or ())]

        async def oktech_status(self):
            self.status_calls += 1
            return {"snipers": []}

    svc = Svc()
    try:
        assert asyncio.run(watch_wallet(cfg, svc, Notifier(None, None), T0)) == []
        assert svc.ignored == [spam] and svc.status_calls == 0
    finally:
        st.close()


def test_a_coin_off_the_curve_is_sold_by_hand_not_by_sweep(tmp_path):
    from snipetech.models import Completion

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "data" / "snipetech.db")
    st = Store(cfg.storage.db_path)
    sent: list[str] = []
    try:
        _sniper(st, G)
        m, ours = dtau(st)
        st.add_completions([Completion(m.mint, S0 + B + 1300, ours.ts + 300, "c")])
        watch_pass(cfg, st, Notifier("T", 7, post=lambda u, d: sent.append(d["text"])), W, [_held(m, ours)], FINISHED,
                   ours.ts + 400)  # fmt: skip
        assert len(sent) == 1 and "OKTech or Axiom" in sent[0] and "left the pump.fun curve" in sent[0]
        assert "remote run sweep" not in sent[0]  # sweep sells curve coins only
    finally:
        st.close()


def test_exit_spec_reads_the_snipers_own_payload():
    lab = exit_spec(list_copy_sniper(G, "book", 0.3, 0.2, cfg=Config()).config)
    assert lab == ExitSpec(stale_secs=1.0, stop_loss_pct=30.0, timer_secs=300.0, copy_pct=50.0, take_profit_pct=10000.0)
    hold60 = exit_spec(list_copy_sniper(G, "kol", 0.3, 0.05, cfg=Config(), exit_name="hold60").config)
    assert hold60.timer_secs == 60.0 and hold60.stale_secs is None and hold60.copy_pct == 50.0
    assert exit_spec({}) is None and exit_spec(None) is None
    off = {"autoSell": {"enabled": False, "sellAfterSeconds": 300, "sellPercent": 100}}
    assert exit_spec(off) is None


def _t(slot, user="x", is_buy=True, base=10**9, price=1.0):
    return Trade(sig=f"s{slot}{user}{is_buy}", ev=0, slot=slot, pos=0, ts=T0 + int(slot * 0.214), mint="M", venue="curve",
                 user=user, is_buy=is_buy, quote=int(base * price), base=base, fee=0, fee_bps=0,
                 q_after=int(10**12 * price), b_after=10**12)  # fmt: skip


def test_the_dead_exit_counts_quiet_time_only_once_oktech_watches():
    """OKTech ignored a 1.7 s pause that began 0.2 s after a buy (Cmg7): its clock starts about 1.5 s after the buy."""
    spec = ExitSpec(stale_secs=1.0)
    buy = _t(0, user="us")
    early = [_t(1)] + [_t(s) for s in range(9, 60, 2)]  # quiet from slot 1 to slot 9: 0.2 s to 1.9 s
    assert due_exits(buy, spec, early, None, horizon=59 * SLOT_SECS) == []
    late = [_t(s) for s in range(1, 9)] + [_t(s) for s in range(16, 60, 2)]  # quiet from slot 8 (1.7 s) to 16
    assert [(n, round(at, 2)) for n, at in due_exits(buy, spec, late, None, horizon=59 * SLOT_SECS)] == [
        ("dead_exit", 2.71)
    ]
    # a quiet stretch still going on counts only as far as the tape has seen
    assert due_exits(buy, spec, [_t(1)], None, horizon=2.0) == []
    assert due_exits(buy, spec, [_t(1)], None, horizon=3.0) == [("dead_exit", 2.5)]


def test_the_copy_exit_counts_the_leaders_holdings_not_the_trade():
    """The leader bought 2 units before us, sells 0.5 (25 % of its coins), then 1.5 (all of what is left)."""
    spec = ExitSpec(copy_pct=50.0)
    buy = _t(0, user="us")
    tape = [_t(10, "L", False, base=5 * 10**8), _t(20, "L", False, base=15 * 10**8)]
    assert due_exits(buy, spec, tape, "L", leader_bag=2 * 10**9) == [("copy_exit", 20 * SLOT_SECS)]
    assert due_exits(buy, ExitSpec(copy_pct=80.0), tape[:1], "L", leader_bag=2 * 10**9) == []


def test_price_and_timer_exits():
    buy = _t(0, user="us")
    crash = [_t(s, price=1.0) for s in range(1, 20)] + [_t(20, price=0.65)]
    assert due_exits(buy, ExitSpec(stop_loss_pct=30.0), crash, None) == [("stop_loss", 20 * SLOT_SECS)]
    assert due_exits(buy, ExitSpec(timer_secs=300.0), [], None, elapsed=299) == []
    assert due_exits(buy, ExitSpec(timer_secs=300.0), [], None, elapsed=301) == [("timer", 300.0)]
