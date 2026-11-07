"""The measure runner: our trades since the last run become fills; round trips carry PnL; failed sends are cost;
the bid model and live expectancy update; the measured landing reaches the qualify stage after 30 fills."""

import asyncio
import math

import pytest

from snipetech.cli import build_parser
from snipetech.config import Config
from snipetech.measure.run import backfill, run_measure, wallet_live_rows
from snipetech.service import Service
from snipetech.solana.txmeta import TxMeta
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000
ME, LEAD, D = addr("me"), addr("lead"), addr("dev")


def _svc(tmp_path):
    st = Store(tmp_path / "t.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = ME
    return Service(cfg, store=st), st, cfg


def _copy_row(st, leader=LEAD, created=NOW - 3 * 86400, removed=None, sid="cs1"):
    """A copy sniper on `leader` in the local table, alive from `created` (and until `removed`)."""
    st.add_oktech_sniper({"id": sid, "external_id": sid, "kind": "copy", "source": leader, "name": f"snipetech copy x {leader[:6]}",
                          "config_hash": "h", "payload": {"ctbotWallets": [{"address": leader}]}, "created_at": created})  # fmt: skip
    if removed is not None:
        st._exec("UPDATE oktech_snipers SET removed_at=? WHERE id=?", (removed, sid))


def _copy_world(st, coins=3, t0=NOW - 3600, with_sniper=True):
    """We copy LEAD in `coins` coins: leader buys, two others, we buy 0.2, we sell everything later."""
    if with_sniper:
        _copy_row(st, created=t0 - 3600)
    sigs = []
    for k in range(coins):
        m = Market(st, addr(f"c{k}"), D, t0 + k * 600, SLOT + k * 1500)
        m.buy(D, 1.0, 0)
        m.buy(LEAD, 1.0, 2)
        m.buy(addr(f"x{k}"), 0.5, 3)
        m.buy(addr(f"y{k}"), 0.5, 4)
        tb = m.buy(ME, 0.2, 4)
        m.buy(addr(f"z{k}"), 2.0, 6)  # price goes up before we sell
        ts = m.sell(ME, 1.0, 30)
        m.commit()
        sigs.append((tb.sig, ts.sig))
    st.set_label(LEAD, "lead", "kol")
    return sigs


def _meta(sig, priority=1_000_000, tip=2_000_000):
    return TxMeta(sig=sig, slot=0, block_time=0, version=0, fee_payer=ME, signers=[ME], n_signatures=1, fee=5000, priority_lamports=priority,
                  cu_limit=200_000, cu_price=5000, cu_consumed=60_000, tips=[{"dest": "T", "lamports": tip, "service": "jito"}] if tip else [])  # fmt: skip


def _ensure(st, sigs_to_meta):
    async def ensure(sigs):
        st.add_tx_meta([sigs_to_meta[s] for s in sigs if s in sigs_to_meta])
        return len(sigs)

    return ensure


async def _no_failed(wallet, since):
    return []


def test_run_writes_fills_and_trips_from_a_synthetic_tape(tmp_path):
    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st)
        metas = {b: _meta(b) for b, _ in sigs}
        out = asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert out["buys"] == 3 and out["sells"] == 3 and out["failed"] == 0 and out["trips"] == 3
        buys = st.fills(wallet=ME, side="buy")
        assert all(b["wallet_copied"] == LEAD and b["trades_behind"] == 2 and b["price_gap_pct"] > 0 for b in buys)
        assert all(
            b["priority_lamports"] == 1_000_000 and b["tip_lamports"] == 2_000_000 and b["bid_arm"] == "control"
            for b in buys
        )
        sells = st.fills(wallet=ME, side="sell")
        assert all(
            s["pnl_sol"] is not None and s["pnl_sol"] > 0 and s["exit_reason"] == "sold" and s["buy_sig"] for s in sells
        )
        bm = {r["arm"]: r for r in st.bid_model()}
        assert bm["control"]["n"] == 3 and bm["control"]["failed_n"] == 0 and bm["control"]["mean_gap_pct"] > 0
        assert bm["control"]["mean_fee_sol"] == round((1_000_000 + 2_000_000 + 5000) / 1e9, 6)
        live = st.wallet_live()
        assert (
            len(live) == 1
            and live[0]["wallet"] == LEAD
            and live[0]["n"] == 3
            and live[0]["status"] == "warming"
            and live[0]["mean_sol"] > 0
        )
        assert st.coverage("measure:last_ts")["ts"] == NOW and out["landing_pos"] is None
        # a second run over the same window changes nothing (idempotent by signature)
        out2 = asyncio.run(
            run_measure(svc, cfg, NOW + 60, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert out2["buys"] == 3 and len(st.fills()) == 6
    finally:
        st.close()


def test_duplicate_sends_count_as_cost(tmp_path):
    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st, coins=1)
        metas = {sigs[0][0]: _meta(sigs[0][0]), "F1": _meta("F1", 1_000_000, 0), "F2": _meta("F2", 1_000_000, 0)}

        async def failed(wallet, since):
            assert wallet == ME
            return [{"signature": "F1", "slot": SLOT + 4, "blockTime": NOW - 3598, "err": {"InstructionError": [3, "Custom"]}},
                    {"signature": "F2", "slot": SLOT + 4, "blockTime": NOW - 3598, "err": {"InstructionError": [3, "Custom"]}}]  # fmt: skip

        out = asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=failed)
        )
        assert out["failed"] == 2 and out["buys"] == 1
        f = st.fills(side="failed")
        assert len(f) == 2 and all(
            r["sol"] == 0 and r["priority_lamports"] == 1_000_000 and r["wallet"] == ME for r in f
        )
        assert st.bid_model()[0]["failed_n"] == 2 and st.bid_model()[0]["n"] == 1
    finally:
        st.close()


def test_wallet_live_decay():
    trips = [
        {"wallet_copied": "W", "pnl_sol": 0.02, "sell_ts": NOW},
        {"wallet_copied": "W", "pnl_sol": -0.01, "sell_ts": NOW - 14 * 86400},
    ]
    rows = wallet_live_rows(trips, NOW, half_life_days=7.0)
    w = rows[0]
    # the 14-day-old loss weighs a quarter: weighted mean = (0.02*1 + -0.01*0.25) / 1.25
    assert w["wallet"] == "W" and w["n"] == 2 and math.isclose(w["mean_sol"], (0.02 - 0.0025) / 1.25, rel_tol=1e-9)
    assert w["lower_sol"] < w["mean_sol"] and w["status"] == "warming" and w["last_fill"] == NOW
    many = [{"wallet_copied": "W", "pnl_sol": 0.01, "sell_ts": NOW - i} for i in range(10)]
    assert wallet_live_rows(many, NOW, 7.0)[0]["status"] == "live"


def test_live_landings_are_read_by_the_leaders_curve_progress():
    """The record needs live landings by where the leader bought on its curve (research round 2: use a closer landing
    for late-curve leaders only once 10+ live fills show it). Copy buys only, in four bands, each with how many, and the
    median trades behind and price gap."""
    from snipetech.measure.run import landing_by_progress

    def buy(progress, behind, gap, copied="L"):
        return {"side": "buy", "wallet_copied": copied, "leader_progress": progress, "trades_behind": behind,
                "price_gap_pct": gap}  # fmt: skip

    buys = [buy(0.02, 2, 30.0), buy(0.08, 4, 40.0), buy(0.3, 3, 5.0), buy(0.85, 0, 0.5), buy(0.93, 1, 0.8),
            buy(0.79, 1, None), buy(1.0, 3, 0.3), buy(None, 9, 99.0), buy(0.5, 9, 99.0, copied=None)]  # fmt: skip
    assert landing_by_progress(buys) == {
        "under 10%": {"n": 2, "trades_behind_p50": 3.0, "price_gap_pct_p50": 35.0},
        "10-60%": {"n": 1, "trades_behind_p50": 3.0, "price_gap_pct_p50": 5.0},
        "60-100%": {"n": 3, "trades_behind_p50": 1.0, "price_gap_pct_p50": 0.65},
        "PumpSwap": {"n": 1, "trades_behind_p50": 3.0, "price_gap_pct_p50": 0.3},
    }
    assert landing_by_progress([]) == {}


def test_the_run_writes_each_copy_fills_leader_progress_and_reports_the_landings_by_it(tmp_path):
    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st)
        out = asyncio.run(run_measure(svc, cfg, NOW, since_ts=NOW - 7200,
                                      ensure_meta=_ensure(st, {b: _meta(b) for b, _ in sigs}), failed_sigs=_no_failed))  # fmt: skip
        assert {st.fill(b)["leader_progress"] for b, _ in sigs} == {0.0846}  # LEAD buys after 2 SOL in, every coin
        assert out["landing_by_progress"] == {"under 10%": {"n": 3, "trades_behind_p50": 2.0,
                                                            "price_gap_pct_p50": st.fill(sigs[0][0])["price_gap_pct"]}}  # fmt: skip
    finally:
        st.close()


def test_landing_override_after_30_fills(tmp_path):
    svc, st, cfg = _svc(tmp_path)
    try:
        cfg.measure.min_arm_fills = 3
        sigs = _copy_world(st, coins=3)
        metas = {b: _meta(b) for b, _ in sigs}
        out = asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        # measured: p75 of trades behind the leader is 2 (X and Y), plus the calibration offset of 3 (the 6 Nov copy
        # test: 2-4 trades behind matched the lab's +6 price gaps) -> "+5"
        assert out["landing_pos"] == "+5" and st.coverage("measure:landing_pos")["pos"] == "+5"
        assert (
            st.coverage("measure:landing_pos")["behind_p75"] == 2 and st.coverage("measure:landing_pos")["pos_p75"] == 1
        )
        from snipetech.qualify.run import effective_landing_pos

        # the measured landing never moves the labs closer than the configured one (rule 2: paper results at a
        # better landing are not evidence); it can only push them further back
        assert effective_landing_pos(st, cfg.qualify) == "+6"
        st.set_coverage("measure:landing_pos", {"pos": "+9", "ts": NOW})
        assert effective_landing_pos(st, cfg.qualify, now=NOW) == "+9"
        st.set_coverage("measure:landing_pos", {})
        assert effective_landing_pos(st, cfg.qualify) == "+6"
    finally:
        st.close()


def test_stale_wallet_live_rows_are_removed(tmp_path):
    """A wallet that stops being a leader (the 4 Nov probe's bot) must not keep a live-expectancy row."""
    svc, st, cfg = _svc(tmp_path)
    try:
        st.put_wallet_live({"wallet": "STALE", "n": 2, "mean_sol": -0.005, "lower_sol": -0.01, "last_fill": NOW - 86400, "status": "warming",
                            "updated_at": NOW - 86400})  # fmt: skip
        sigs = _copy_world(st, coins=2)
        asyncio.run(
            run_measure(
                svc,
                cfg,
                NOW,
                since_ts=NOW - 7200,
                ensure_meta=_ensure(st, {b: _meta(b) for b, _ in sigs}),
                failed_sigs=_no_failed,
            )
        )
        assert [r["wallet"] for r in st.wallet_live()] == [LEAD]
    finally:
        st.close()


def test_backfill_does_not_move_last_ts(tmp_path):
    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st, coins=2, t0=NOW - 20_000)
        metas = {b: _meta(b) for b, _ in sigs}
        st.set_coverage("measure:last_ts", {"ts": NOW - 100})
        out = asyncio.run(
            backfill(svc, cfg, NOW - 21_000, NOW - 15_000, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert out["buys"] == 2 and st.coverage("measure:last_ts")["ts"] == NOW - 100
    finally:
        st.close()


def test_cli_measure_parses():
    p = build_parser()
    a = p.parse_args(["measure", "run"])
    assert a.cmd == "measure" and a.action == "run" and a.since is None
    a = p.parse_args(["measure", "backfill", "--since", "1793940000", "--until", "1793947000"])
    assert a.action == "backfill" and a.since == 1793940000 and a.until == 1793947000


def test_failed_sends_take_their_arm_from_the_fee_and_count_as_overhead(tmp_path):
    """A failed twin (the second or third send of a sell, or a failed buy) pays its priority fee: it is booked on the arm
    whose bid it carried, and the bid model shows the overhead per landed buy next to the fee."""
    svc, st, cfg = _svc(tmp_path)
    sigs = _copy_world(st, coins=2)
    metas = {b: _meta(b, priority=20_000_000, tip=20_000_000) for b, _ in sigs} | {
        s_: _meta(s_, priority=1_000_000, tip=0) for _, s_ in sigs
    }
    failed = [{"signature": "FAIL1", "blockTime": NOW - 1800, "slot": SLOT + 100, "err": {"InstructionError": [3, {"Custom": 6023}]}},
              {"signature": "FAIL2", "blockTime": NOW - 1700, "slot": SLOT + 200, "err": {"InstructionError": [3, {"Custom": 6023}]}}]  # fmt: skip
    metas["FAIL1"] = _meta("FAIL1", priority=20_000_000, tip=0)
    metas["FAIL2"] = _meta("FAIL2", priority=1_000_000, tip=0)

    async def failed_sigs(wallet, since):
        return failed

    out = asyncio.run(run_measure(svc, cfg, NOW, ensure_meta=_ensure(st, metas), failed_sigs=failed_sigs))
    assert out["arms"]["paid"]["n"] == 2 and out["arms"]["paid"]["failed_n"] == 1
    assert out["arms"]["control"]["n"] == 0 and out["arms"]["control"]["failed_n"] == 1
    assert st.fill("FAIL1")["bid_arm"] == "paid" and st.fill("FAIL2")["bid_arm"] == "control"
    model = {r["arm"]: r for r in st.bid_model()}
    assert model["paid"]["tipped_share"] == 1.0 and model["paid"]["mean_behind"] == 2.0
    assert model["paid"]["mean_overhead_sol"] == round(
        (20_000_000 + 5000) / 1e9 / 2, 6
    )  # one failed twin over two buys
    assert model["paid"]["mean_fee_sol"] == round((20_000_000 + 20_000_000 + 5000) / 1e9, 6)
    st.close()


def test_a_failed_transfer_another_wallet_paid_for_is_not_our_failed_send(tmp_path):
    """7 Nov 17:50: SOL came into the sniper wallet from several wallets in one second, and the senders that tried to
    send their whole balance failed (InsufficientFundsForRent on their own account). The wallet's history lists those
    failed transactions, but another wallet signed and paid for them: they are not our failed sends and cost us
    nothing. A row an earlier run wrote for one goes; a failed send we paid for stays."""
    svc, st, cfg = _svc(tmp_path)
    try:
        sender = addr("sender")
        failed = [{"signature": "IN1", "blockTime": NOW - 1800, "slot": SLOT + 100, "err": {"InsufficientFundsForRent": {"account_index": 0}}},
                  {"signature": "OURS", "blockTime": NOW - 1700, "slot": SLOT + 200, "err": {"InstructionError": [3, {"Custom": 6023}]}}]  # fmt: skip
        metas = {"IN1": TxMeta(sig="IN1", slot=SLOT + 100, block_time=NOW - 1800, version=0, fee_payer=sender, signers=[sender],
                               n_signatures=1, fee=5000, priority_lamports=4500, cu_limit=450, cu_price=10_000, cu_consumed=450, tips=[]),
                 "OURS": _meta("OURS", priority=1_000_000, tip=0)}  # fmt: skip
        st.put_fill({"sig": "IN1", "ts": NOW - 1800, "slot": SLOT + 100, "wallet": ME, "side": "failed", "sol": 0.0,
                     "tokens": 0.0, "bid_arm": "control", "priority_lamports": 4500})  # fmt: skip  # an earlier run's row

        async def failed_sigs(wallet, since):
            return failed

        out = asyncio.run(run_measure(svc, cfg, NOW, ensure_meta=_ensure(st, metas), failed_sigs=failed_sigs))
        assert st.fill("IN1") is None
        assert st.fill("OURS")["side"] == "failed"
        assert out["failed"] == 1
    finally:
        st.close()


def test_sells_inherit_the_arm_of_the_buy_they_close(tmp_path):
    """Sells bid the same fixed fee on every arm, so the fee cannot tell the arms apart: a sell belongs to the arm of
    the buy lot it closes (FIFO), and so does its realised PnL."""
    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st, coins=2)
        metas = {b: _meta(b, priority=20_000_000, tip=20_000_000) for b, _ in sigs} | {
            s_: _meta(s_, priority=1_000_000, tip=0) for _, s_ in sigs
        }
        asyncio.run(run_measure(svc, cfg, NOW, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed))
        for _, s_ in sigs:
            row = st.fill(s_)
            assert row["bid_arm"] == "paid" and row["pnl_sol"] is not None and row["buy_sig"] in {b for b, _ in sigs}
    finally:
        st.close()


def test_reclassifying_an_old_window_keeps_the_recorded_pnl(tmp_path):
    """A backfill of a window older than lookback_days re-reads the trades; it must not wipe the PnL that an earlier
    run wrote on those sells."""
    svc, st, cfg = _svc(tmp_path)
    try:
        cfg.measure.lookback_days = 0.01  # 14 minutes
        t0 = NOW - 3 * 86400
        sigs = _copy_world(st, coins=2, t0=t0)
        metas = {b: _meta(b) for b, _ in sigs} | {s_: _meta(s_, tip=0) for _, s_ in sigs}
        asyncio.run(backfill(svc, cfg, t0 - 60, t0 + 3600, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed))
        before = {s_: st.fill(s_)["pnl_sol"] for _, s_ in sigs}
        assert all(v is not None for v in before.values())
        asyncio.run(backfill(svc, cfg, t0 - 60, t0 + 3600, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed))
        assert {s_: st.fill(s_)["pnl_sol"] for _, s_ in sigs} == before
    finally:
        st.close()


def test_leaders_are_the_copy_snipers_alive_at_the_fill(tmp_path):
    """A buy is credited to a wallet only if a copy sniper on it existed at that moment; labels alone are not leaders
    (the old fallback to every labelled wallet credited strategy fills to whichever candidate bought before us)."""
    svc, st, cfg = _svc(tmp_path)
    try:
        cfg.measure.min_arm_fills = 2
        sigs = _copy_world(st, coins=2, with_sniper=False)  # LEAD is labelled kol, but no sniper copies it
        metas = {b: _meta(b) for b, _ in sigs}
        run = lambda t: asyncio.run(
            run_measure(svc, cfg, t, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        run(NOW)
        assert [st.fill(b)["wallet_copied"] for b, _ in sigs] == [None, None]
        _copy_row(st, created=NOW - 3300)  # created between the two coins (buys at NOW-3599 and NOW-2999)
        out = run(NOW + 60)
        assert [st.fill(b)["wallet_copied"] for b, _ in sigs] == [None, LEAD]
        assert out["landing_pos"] is None  # one buy with a leader is not the two the landing needs
        _copy_row(st, created=NOW - 7200, removed=NOW - 3500, sid="cs1")  # alive for the first coin only
        run(NOW + 120)
        assert [st.fill(b)["wallet_copied"] for b, _ in sigs] == [LEAD, None]
    finally:
        st.close()


def test_failed_sell_twin_belongs_to_the_arm_of_the_coin_it_sells(tmp_path):
    """Sell twins bid the fixed sell fee on both arms, so the fee cannot place them: a failed sell belongs to the arm
    that bought the coin it was selling."""
    from snipetech.solana.txmeta import PumpIx

    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st, coins=1)
        b, s_ = sigs[0]
        twin = _meta("TWIN", priority=2_000_000, tip=0)
        twin.pump_ixs = [PumpIx("pump", "sell", ME, {}, None, addr("c0"))]
        metas = {
            b: _meta(b, priority=20_000_000, tip=20_000_000),
            s_: _meta(s_, priority=2_000_000, tip=0),
            "TWIN": twin,
        }

        async def failed(wallet, since):
            return [
                {
                    "signature": "TWIN",
                    "blockTime": NOW - 3580,
                    "slot": SLOT + 40,
                    "err": {"InstructionError": [3, {"Custom": 6023}]},
                }
            ]

        asyncio.run(run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=failed))
        row = st.fill("TWIN")
        assert row["bid_arm"] == "paid" and row["mint"] == addr("c0")
    finally:
        st.close()


def test_a_fill_without_a_fingerprint_is_retried(tmp_path):
    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st, coins=1)
        b, _ = sigs[0]
        asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, {}), failed_sigs=_no_failed)
        )
        assert st.fill(b)["priority_lamports"] is None and st.fill(b)["bid_arm"] == "control"
        metas = {b: _meta(b, priority=20_000_000, tip=20_000_000)}
        asyncio.run(
            run_measure(svc, cfg, NOW + 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )  # cursor past the trade
        assert st.fill(b)["priority_lamports"] == 20_000_000 and st.fill(b)["bid_arm"] == "paid"
    finally:
        st.close()


def test_a_fingerprinted_fill_keeps_its_arm_when_the_arms_change(tmp_path):
    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st, coins=1)
        b, _ = sigs[0]
        metas = {b: _meta(b, priority=20_000_000, tip=20_000_000)}
        asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert st.fill(b)["bid_arm"] == "paid"
        cfg.book.bid_arms = {"control": {"priority": 0.001, "tip": 0.002}, "rich": {"priority": 0.02, "tip": 0.02}}
        asyncio.run(
            run_measure(svc, cfg, NOW + 60, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert st.fill(b)["bid_arm"] == "paid"  # the arm it bid at fill time
    finally:
        st.close()


def test_stale_bid_model_rows_and_an_old_landing_are_dropped(tmp_path):
    from snipetech.qualify.run import effective_landing_pos

    svc, st, cfg = _svc(tmp_path)
    try:
        st.put_bid_model({"arm": "gone", "n": 5, "failed_n": 0, "updated_at": NOW - 86400})
        sigs = _copy_world(st, coins=1)
        metas = {b: _meta(b) for b, _ in sigs}
        asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert [r["arm"] for r in st.bid_model()] == ["control"]
        st.set_coverage("measure:landing_pos", {"pos": "+9", "ts": NOW - 30 * 86400})
        assert (
            effective_landing_pos(st, cfg.qualify, now=NOW) == "+6"
        )  # a month-old measurement no longer moves the labs
        st.set_coverage("measure:landing_pos", {"pos": "+9"})
        assert effective_landing_pos(st, cfg.qualify, now=NOW) == "+6"  # no timestamp: not trusted
    finally:
        st.close()


def test_the_low_bids_last_measured_cost_outlives_the_bid_models_week(tmp_path):
    """The bid model reads the last 7 days of fills, so a week without trading empties it and its rows are pruned (7 Nov
    10:59 the wallet was emptied; nothing has traded since). The low bid's cost of a transaction, whenever it is
    measured, is kept apart (measure:tx_cost), and the labs charge it once the week has too few fills, never an
    unmeasured cost (review of 7 Nov evening: they fell back to the flat 0.0035, 0.002 SOL a trade too kind)."""
    from snipetech.qualify.run import lab_tx_cost_info

    svc, st, cfg = _svc(tmp_path)
    try:
        cfg.measure.min_arm_fills = 3
        sigs = _copy_world(st, coins=3)
        metas = {b: _meta(b, priority=3_000_000) for b, _ in sigs}  # 0.003 priority + 0.002 tip + base: 0.005005 a buy
        asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert lab_tx_cost_info(st, cfg) == {"per_tx_sol": 0.005005, "per_tx_source": "measured",
                                             "per_tx_measured_at": NOW}  # fmt: skip
        assert st.coverage("measure:tx_cost") == {"per_tx_sol": 0.005005, "arm": "control", "n": 3, "ts": NOW}
        later = NOW + 8 * 86400  # a week and a day without a fill
        asyncio.run(run_measure(svc, cfg, later, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed))
        assert st.bid_model() == []  # nothing in the window: every row pruned
        assert lab_tx_cost_info(st, cfg) == {"per_tx_sol": 0.005005, "per_tx_source": "last measured",
                                             "per_tx_measured_at": NOW}  # fmt: skip
    finally:
        st.close()


def test_twin_fees_go_to_the_nearest_landed_sell_of_the_same_coin():
    """A failed send carrying a sell of the same coin within 3 s of a landed sell is that sell's twin and costs its
    priority fee + 5,000 lamports; failed buys, other coins, sends further away and unread sends are not twins."""
    from snipetech.measure.run import twin_fees_by_sell

    sells = [
        {"sig": "S1", "ts": NOW, "slot": SLOT, "mint": "M1"},
        {"sig": "S2", "ts": NOW + 2, "slot": SLOT + 9, "mint": "M1"},
        {"sig": "S3", "ts": NOW, "slot": SLOT, "mint": "M2"},
    ]
    failed = [
        {"sig": "F1", "ts": NOW, "slot": SLOT, "mint": "M1", "priority_lamports": 2_000_000, "is_sell": True},
        {"sig": "F2", "ts": NOW + 3, "slot": SLOT + 14, "mint": "M1", "priority_lamports": 2_000_000, "is_sell": True},
        {"sig": "F3", "ts": NOW + 4, "slot": SLOT + 19, "mint": "M2", "priority_lamports": 2_000_000, "is_sell": True},
        {"sig": "F4", "ts": NOW, "slot": SLOT, "mint": "M2", "priority_lamports": 2_000_000, "is_sell": False},
        {"sig": "F5", "ts": NOW, "slot": SLOT, "mint": "M3", "priority_lamports": 2_000_000, "is_sell": True},
        {"sig": "F6", "ts": NOW, "slot": SLOT, "mint": None, "priority_lamports": None, "is_sell": False},
    ]
    out = twin_fees_by_sell(failed, sells)
    assert out == pytest.approx({"S1": 0.002005, "S2": 0.002005})
    assert twin_fees_by_sell(failed, sells, window_s=5)["S3"] == pytest.approx(0.002005)
    from snipetech.measure.run import twin_sells  # which sale each twin is (a live test's block charges the others)

    assert twin_sells(failed, sells) == {"F1": "S1", "F2": "S2"}


def test_a_twin_is_charged_to_the_sell_before_it_never_to_a_later_one():
    """A failed twin lands in its winner's slot or up to 5 slots after it, never before. Lot 1 (copying A) is sold at
    slot S; its losing variant fails at S+4, a second later; lot 2 (copying B) is sold at S+6 in that same second. The
    0.020005 SOL twin is lot 1's, although lot 2's sell is nearer in seconds. Without slots the seconds decide."""
    from snipetech.measure.run import twin_fees_by_sell

    sells = [
        {"sig": "A", "ts": NOW, "slot": SLOT, "mint": "M"},
        {"sig": "B", "ts": NOW + 1, "slot": SLOT + 6, "mint": "M"},
    ]
    twin = {"sig": "F", "ts": NOW + 1, "slot": SLOT + 4, "mint": "M", "priority_lamports": 20_000_000, "is_sell": True}
    assert twin_fees_by_sell([twin], sells) == pytest.approx({"A": 0.020005})
    later = [dict(sells[0], slot=None), dict(sells[1], ts=NOW + 3, slot=None)]
    assert twin_fees_by_sell([dict(twin, ts=NOW + 2, slot=None)], later) == pytest.approx({"A": 0.020005})


def test_failed_sell_twins_are_charged_to_their_trip(tmp_path):
    """OKTech sends every exit as up to three variants; the losers fail and still pay their priority fee + 5,000
    lamports. Two failed 0.002 twins of a landed sell cost 0.00401 SOL: that goes on the sell row and into the copied
    wallet's live expectancy (the sell's own PnL keeps only the trip's own fees)."""
    from snipetech.solana.txmeta import PumpIx

    svc, st, cfg = _svc(tmp_path)
    try:
        sigs = _copy_world(st, coins=1)
        b, s_ = sigs[0]
        metas = {b: _meta(b), s_: _meta(s_)}

        def run(failed):
            return asyncio.run(
                run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=failed)
            )

        run(_no_failed)
        before, pnl = st.wallet_live()[0]["mean_sol"], st.fill(s_)["pnl_sol"]
        sell = st.fill(s_)
        assert sell["twin_fee_sol"] == 0.0
        sends = []
        for k, (name, ix) in enumerate((("TWIN1", "sell"), ("TWIN2", "sell"), ("FAILBUY", "buy"))):
            m = _meta(name, priority=2_000_000, tip=0)
            m.pump_ixs = [PumpIx("pump", ix, ME, {}, None, addr("c0"))]
            metas[name] = m
            sends.append({"signature": name, "blockTime": sell["ts"] + min(k, 1), "slot": sell["slot"] + k,
                          "err": {"InstructionError": [3, {"Custom": 6023}]}})  # fmt: skip

        async def failed(wallet, since):
            return sends

        out = run(failed)
        assert out["failed"] == 3
        assert st.fill(s_)["twin_fee_sol"] == pytest.approx(0.00401)
        assert st.fill(s_)["pnl_sol"] == pnl  # the trip's own fees only; the twins are kept apart
        assert st.wallet_live()[0]["mean_sol"] == pytest.approx(before - 0.00401, abs=1e-6)
    finally:
        st.close()


def _book_sniper(st, sid="bk1", priority=0.001, tip=0.002, exit_name="lab"):
    """A book copy sniper on LEAD as the local table keeps it: the OKTech config it was created with."""
    from snipetech.oktech import list_copy_sniper

    p = list_copy_sniper(LEAD, "book", 0.3, 0.2, cfg=Config(), exit_name=exit_name, priority_fee=priority, tip=tip,
                         tag=f"arm:{sid}")  # fmt: skip
    st.add_oktech_sniper({"id": sid, "external_id": sid, "kind": "copy", "source": LEAD, "name": p.name,
                          "config_hash": p.config_hash, "payload": p.config, "created_at": NOW - 86400})  # fmt: skip


def _exit_world(st):
    """Two coins we copied from LEAD: one sold by the dead exit (6 quiet slots after the last trade), one by the copy
    exit (LEAD sells its whole bag, we sell the slot after)."""
    out = {}
    m = Market(st, addr("dead"), D, NOW - 3600, SLOT)
    m.buy(D, 1.0, 0)
    m.buy(LEAD, 2.0, 0)
    b = m.buy(ME, 0.2, 0)
    m.buy(addr("dead-f1"), 0.05, 1)
    m.buy(addr("dead-f2"), 0.05, 2)
    out["dead"] = (b.sig, m.sell(ME, 1.0, 8).sig)
    m.commit()
    m = Market(st, addr("copy"), D, NOW - 3000, SLOT + 1500)
    m.buy(D, 1.0, 0)
    m.buy(LEAD, 2.0, 0)
    b = m.buy(ME, 0.2, 0)
    for k in range(1, 10):
        m.buy(addr(f"copy-f{k}"), 0.05, k)
    m.sell(LEAD, 1.0, 10)
    out["copy"] = (b.sig, m.sell(ME, 1.0, 11).sig)
    m.commit()
    return out


def test_sell_rows_carry_the_exit_that_fired_and_the_preset(tmp_path):
    """Every sell says which exit closed it (not just 'sold') and the preset its sniper ran; a later run never
    rewrites a label, even when the local record of the sniper changes."""
    svc, st, cfg = _svc(tmp_path)
    try:
        _book_sniper(st)
        coins = _exit_world(st)
        metas = {s: _meta(s) for pair in coins.values() for s in pair}  # 0.001 priority + 0.002 tip, buys and sells

        def run(t):
            return asyncio.run(
                run_measure(svc, cfg, t, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
            )

        out = run(NOW)
        (db, ds), (cb, cs) = coins["dead"], coins["copy"]
        assert st.fill(db)["exit_name"] == "lab" and st.fill(cb)["exit_name"] == "lab"
        assert st.fill(ds)["exit_reason"] == "dead_exit" and st.fill(cs)["exit_reason"] == "copy_exit"
        assert st.fill(ds)["exit_name"] == "lab" and st.fill(ds)["crossed"] == 0
        assert out["exits"] == {"lab": {"dead_exit": 1, "copy_exit": 1}} and out["crossed"] == 0
        _book_sniper(st, exit_name="hold60")  # the same sniper id, now recorded with the 60 s preset
        run(NOW + 60)
        assert st.fill(ds)["exit_reason"] == "dead_exit" and st.fill(cs)["exit_reason"] == "copy_exit"
        assert st.fill(db)["exit_name"] == "lab" and st.fill(ds)["exit_name"] == "lab"
    finally:
        st.close()


def test_every_copy_buy_is_shadowed_once_six_minutes_after_it(tmp_path):
    """measure replays every exit on the menu from each copy buy of ours once the buy is 6 minutes old and the tape
    recorded the whole time after it (the exit_shadow table), once per buy and menu version; a younger buy waits for
    a later run."""
    from snipetech.exits_menu import EXIT_MENU_VERSION

    svc, st, cfg = _svc(tmp_path)
    try:
        _book_sniper(st)
        coins = _exit_world(st)  # bought an hour and 50 minutes ago
        m = Market(st, addr("young"), D, NOW - 200, SLOT + 3000)
        m.buy(D, 1.0, 0)
        m.buy(LEAD, 2.0, 0)
        young = m.buy(ME, 0.2, 0)
        m.buy(addr("young-f1"), 0.05, 1)
        m.commit()
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})
        metas = {s: _meta(s) for pair in coins.values() for s in pair} | {young.sig: _meta(young.sig)}

        def run(t):
            return asyncio.run(
                run_measure(svc, cfg, t, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
            )

        assert run(NOW)["shadowed"] == 2
        rows = st.exit_shadow()
        assert {r["buy_sig"] for r in rows} == {coins["dead"][0], coins["copy"][0]} and len(rows) == 6
        assert {r["menu_version"] for r in rows} == {EXIT_MENU_VERSION}
        assert {r["exit_name"] for r in rows} == {"lab", "hold60", "ride"}
        assert run(NOW + 60)["shadowed"] == 0 and len(st.exit_shadow()) == 6  # once per buy
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW + 400]]})
        assert run(NOW + 400)["shadowed"] == 1 and young.sig in {r["buy_sig"] for r in st.exit_shadow()}
    finally:
        st.close()


def test_a_sell_paying_another_snipers_priority_is_flagged_crossed(tmp_path):
    """Two snipers copy LEAD from one OKTech wallet. A coin bought at the 0.001 sniper's fee and sold at the 0.02
    sniper's priority was sold by the other sniper: the sell is flagged crossed, counted, and noted once."""
    svc, st, cfg = _svc(tmp_path)
    try:
        _book_sniper(st, "low", 0.001, 0.002)
        _book_sniper(st, "high", 0.02, 0.02)
        coins = _exit_world(st)
        (db, ds), (cb, cs) = coins["dead"], coins["copy"]
        metas = {db: _meta(db), cb: _meta(cb), cs: _meta(cs), ds: _meta(ds, priority=20_000_000, tip=2_000_000)}

        def run(t):
            return asyncio.run(
                run_measure(svc, cfg, t, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
            )

        out = run(NOW)
        assert st.fill(ds)["crossed"] == 1 and st.fill(cs)["crossed"] == 0
        assert (
            st.fill(ds)["exit_reason"] == "dead_exit"
        )  # another sniper's fee is still a sniper's sell, not a hand sale
        assert st.fill(ds)["bid_arm"] == "control" and out["crossed"] == 1
        notes = (tmp_path / "operator" / "notes.md").read_text()
        assert notes.count("another sniper") == 1 and addr("dead")[:6] in notes
        run(NOW + 60)
        assert (tmp_path / "operator" / "notes.md").read_text().count("another sniper") == 1
    finally:
        st.close()


def test_an_exit_is_read_only_once_the_sells_fee_is_known(tmp_path):
    """The label is written once, and the fee decides whether a sale was by hand: a sell whose fingerprint is not
    in yet stays 'sold' and is read on a later run, when the fingerprint has arrived."""
    svc, st, cfg = _svc(tmp_path)
    try:
        _book_sniper(st)
        (db, ds), _ = _exit_world(st).values()
        metas = {db: _meta(db)}
        asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert st.fill(ds)["exit_reason"] == "sold" and st.fill(ds)["pnl_sol"] is not None
        metas[ds] = _meta(ds, priority=12_951, tip=2_166_730)  # the owner's hand sale (4siK's fees)
        asyncio.run(run_measure(svc, cfg, NOW + 60, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed))
        assert st.fill(ds)["exit_reason"] == "manual" and st.fill(ds)["crossed"] == 0
    finally:
        st.close()


def test_a_hand_sale_is_judged_against_the_snipers_alive_at_the_sale(tmp_path):
    """A sale by hand at 0.001 priority + 0.001 tip. The book sniper alive at the sale sells at 0.001 + 0.002 or 0.002
    untipped, so no sniper of ours paid that: the sale is 'manual'. A morning copy sniper removed hours before (it sold
    at 0.001 + 0.001) stays in the table, and must not hide the hand sale as the dead exit it happens to look like."""
    from snipetech.oktech import list_copy_sniper

    svc, st, cfg = _svc(tmp_path)
    try:
        _book_sniper(st)
        morning = Config()
        morning.oktech.sell_fee_sol = 0.001  # the 6 Nov morning copy snipers
        leader = addr("morning leader")
        p = list_copy_sniper(leader, "kol", 0.3, 0.05, cfg=morning, priority_fee=0.001, tip=0.002)
        row = {"id": "am1", "external_id": "am1", "kind": "copy", "source": leader, "name": p.name,
               "config_hash": p.config_hash, "payload": p.config, "created_at": NOW - 2 * 86400}  # fmt: skip
        st.add_oktech_sniper(row)
        st._exec("UPDATE oktech_snipers SET removed_at=? WHERE id=?", (NOW - 20 * 3600, "am1"))
        (db, ds), _ = _exit_world(st).values()
        metas = {db: _meta(db), ds: _meta(ds, priority=1_000_000, tip=1_000_000)}
        asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert st.fill(ds)["exit_reason"] == "manual"
    finally:
        st.close()
