"""A wallet that trades a dollar coin or wrapped SOL is not copied (14 Nov 2026).

OKTech's copy mode cannot refuse a coin by its address. FvHwFe, qualified at 06:00 and armed by the book at 06:05,
swaps into USDC on PumpSwap about 70 times a day: at 06:41 the copy followed one such swap (a buy that could not be
paid, then 49 failed sells in 22 seconds, 0.05 SOL of priority fees). The tape holds those trades, so the book reads
them: a qualified wallet with a non-coin trade in the last week gets no sniper, and the sniper it has is stopped the
way any wallet that left the ranking is (never while our wallet holds a coin: a stopped sniper strands it)."""

import asyncio

from snipetech.book.policy import plan_book
from snipetech.book.run import run_book
from snipetech.config import Config
from snipetech.models import Trade
from snipetech.positions import NON_COIN_MINTS, WSOL
from snipetech.qualify.non_coin import NON_COIN_DAYS, NON_COIN_REASON, non_coin_traders, not_a_coin_trader
from tests.synth import addr
from tests.test_book_one_actor import A, B, C, _acts, _inp, _sniper, _why
from tests.test_book_run import L1, L2, NOW, FakeSvc, _env, _fresh_tape
from tests.test_qualify_run import NOW as NOW_Q

USDC = next(m for m, name in NON_COIN_MINTS.items() if name == "USDC")
WORDS = "trades USDC (70 trades on the tape in the last 7 days)"


def _swap(wallet, mint, ts, i, is_buy=True):
    return Trade(sig=f"sig-{wallet[:4]}-{mint[:4]}-{i}", ev=0, slot=1000 + i, pos=0, ts=int(ts), mint=mint, venue="amm",
                 user=wallet, is_buy=is_buy, quote=1_000_000_000, base=1_000_000, fee=0, fee_bps=0,
                 q_after=10**12, b_after=10**12)  # fmt: skip


def test_a_wallet_that_trades_a_non_coin_gets_no_sniper():
    cfg = Config()
    plan = plan_book(_inp(actors={}, non_coin={A: WORDS}), cfg.book)
    assert _acts(plan, "create") == [("create", B), ("create", C)]
    assert _why(plan, A).startswith(NON_COIN_REASON) and WORDS in _why(plan, A)
    assert A not in [r["wallet"] for r in plan.ranked]
    assert any(A[:6] in n and "USDC" in n for n in plan.notes)
    # nobody trades one: the book is what it was
    before = plan_book(_inp(actors={}), cfg.book)
    after = plan_book(_inp(actors={}, non_coin={}), cfg.book)
    assert after.actions == before.actions and after.notes == before.notes


def test_its_running_sniper_is_stopped_only_while_our_wallet_holds_nothing():
    cfg = Config()
    plan = plan_book(_inp(actors={}, non_coin={A: WORDS}, snipers=[_sniper(A, "sa")]), cfg.book)
    stop = next(a for a in plan.actions if a.kind == "stop")
    assert (stop.wallet, stop.sniper_id) == (A[:6], "sa") and stop.reason.startswith(NON_COIN_REASON)
    assert ("create", A) not in _acts(plan) and ("start", A) not in _acts(plan)
    # a coin is held, or the holdings could not be read: the sniper may be the one that has to sell it
    for over in ({"holdings": [{"mint": "M", "amount": 1}]}, {"tape_holding": True}):
        held = plan_book(_inp(actors={}, non_coin={A: WORDS}, snipers=[_sniper(A, "sa")], **over), cfg.book)
        assert not _acts(held, "stop") and ("keep", A[:6]) in _acts(held)
        assert "never stop a holder" in next(a.reason for a in held.actions if a.kind == "keep")
    # a stopped one is never started again, a spent one never replaced
    for status in ("idle", "finished"):
        again = plan_book(_inp(actors={}, non_coin={A: WORDS}, snipers=[_sniper(A, "sa", status)]), cfg.book)
        assert not _acts(again, "start") and ("create", A) not in _acts(again)


def test_the_tape_says_which_wallets_trade_a_non_coin(tmp_path):
    st, _cfg = _env(tmp_path)
    try:
        coin = addr("a-real-coin")
        st.add_trades([
            *[_swap(L1, USDC, NOW - 3600 - i, i, is_buy=i % 2 == 0) for i in range(5)],
            _swap(L1, coin, NOW - 100, 50),
            _swap(L2, coin, NOW - 100, 51),
            _swap(L2, WSOL, NOW - (NON_COIN_DAYS + 1) * 86400, 52),  # older than the window: not counted
            _swap(B, WSOL, NOW - 60, 53),
        ])  # fmt: skip
        got = non_coin_traders(st, [L1, L2, B, C], NOW)
        assert set(got) == {L1, B}
        assert "USDC" in got[L1] and "5 trades" in got[L1] and "3 of them buys" in got[L1]
        assert "wrapped SOL" in got[B] and "1 trade " in got[B]
        assert non_coin_traders(st, [], NOW) == {}
    finally:
        st.close()


def test_a_run_makes_no_sniper_for_a_wallet_that_trades_usdc_and_says_so_once(tmp_path):
    st, cfg = _env(tmp_path, enabled=True)
    svc = FakeSvc(st)
    try:
        st.add_trades([_swap(L1, USDC, NOW - 600 - i, i) for i in range(3)])
        _fresh_tape(st)
        out = asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert out["dry"] is False and out["actions"] == {"skip": 1, "create": 1}
        creates = [c for c in svc.calls if c[0] == "create"]
        assert len(creates) == 1 and creates[0][1][0].startswith(L2)
        notes = (tmp_path / "data" / "operator" / "notes.md").read_text()
        assert f"[book] {L1[:6]} is not copied: {NON_COIN_REASON}" in notes and "USDC" in notes
        asyncio.run(run_book(svc, cfg, NOW + 300, balances=lambda w: (10.0, 0.0, [])))
        assert (tmp_path / "data" / "operator" / "notes.md").read_text().count("is not copied") == 1
    finally:
        st.close()


def test_a_run_stops_the_sniper_of_a_wallet_that_trades_usdc(tmp_path):
    st, cfg = _env(tmp_path, enabled=True)
    running = [{"id": "s1", "name": f"snipetech copy book {L1[:6]} arm:control", "status": "running"}]
    svc = FakeSvc(st, snipers=running)
    try:
        st.add_trades([_swap(L1, USDC, NOW - 600, 1)])
        _fresh_tape(st)
        asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert ("stop", "s1") in svc.calls
        assert not any(c[0] == "create" and c[1][0].startswith(L1) for c in svc.calls)
    finally:
        st.close()


def test_a_run_that_cannot_read_the_tape_for_it_books_as_before_and_says_so(tmp_path, monkeypatch):
    from snipetech.book import run

    st, cfg = _env(tmp_path, enabled=True)
    svc = FakeSvc(st)

    def broken(*a, **kw):
        raise RuntimeError("no such table")

    monkeypatch.setattr(run, "non_coin_traders", broken)
    try:
        out = asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert out["actions"] == {"create": 2}
        notes = (tmp_path / "data" / "operator" / "notes.md").read_text()
        assert "[book] could not read which wallets trade a non-coin" in notes
    finally:
        st.close()


def test_qualify_does_not_qualify_a_wallet_that_trades_a_non_coin():
    cfg = Config()
    v = {"wallet": A, "status": "qualified", "reasons": ["holds after it was picked"], "next_review": NOW + 86400}
    out = not_a_coin_trader(v, WORDS, cfg.qualify, NOW)
    assert out["status"] == "candidate" and out["gate"] == "non_coin"
    assert out["reasons"][-1].startswith(NON_COIN_REASON) and WORDS in out["reasons"][-1]
    assert out["next_review"] == NOW + cfg.qualify.review_qualified_h * 3600
    assert v["status"] == "qualified"  # the verdict passed in is not changed
    # nothing known of it, or a verdict that is not qualified: the verdict itself
    assert not_a_coin_trader(v, None, cfg.qualify, NOW) is v
    retired = {"wallet": A, "status": "retired", "reasons": ["x"]}
    assert not_a_coin_trader(retired, WORDS, cfg.qualify, NOW) is retired


def test_a_qualify_run_leaves_a_wallet_that_trades_usdc_a_candidate_with_the_reason(tmp_path):
    from snipetech.qualify.run import run_qualify
    from tests.test_qualify_one_actor import _latest, _runner
    from tests.test_qualify_run import STRONG, _svc
    from tests.test_qualify_twins import SOLO, _tape

    after = [[NOW_Q - 24 * 3600 + 600 * k, 0.03] for k in range(60)]
    for swaps, status in ((0, "qualified"), (3, "candidate")):
        (tmp_path / f"swaps-{swaps}").mkdir()
        svc, st, data = _svc(tmp_path / f"swaps-{swaps}")
        try:
            _tape(st, groups=())
            st.upsert_candidate(SOLO, "solanatracker", STRONG, NOW_Q - 30 * 3600)
            run_qualify(svc, svc.cfg, data / "jobs", NOW_Q - 25 * 3600, lab_runner=_runner())  # picked a day ago
            st.add_trades([_swap(SOLO, USDC, NOW_Q - 600 - i, 9000 + i) for i in range(swaps)])
            run_qualify(svc, svc.cfg, data / "jobs", NOW_Q, lab_runner=_runner(moments={SOLO: after}))
            v = _latest(st)[SOLO]
            assert v["status"] == status, v["reasons"]
            if swaps:
                assert v["reasons"][-1].startswith(NON_COIN_REASON)
                assert "USDC (3 trades" in v["reasons"][-1]
        finally:
            st.close()
