"""The measure runner: our trades since the last run become fills; round trips carry PnL; failed sends are cost;
the bid model and live expectancy update; the measured landing reaches the qualify stage after 30 fills."""

import asyncio
import math

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


def _copy_world(st, coins=3, t0=NOW - 3600):
    """We copy LEAD in `coins` coins: leader buys, two others, we buy 0.2, we sell everything later."""
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


def test_landing_override_after_30_fills(tmp_path):
    svc, st, cfg = _svc(tmp_path)
    try:
        cfg.measure.min_arm_fills = 3
        sigs = _copy_world(st, coins=3)
        metas = {b: _meta(b) for b, _ in sigs}
        out = asyncio.run(
            run_measure(svc, cfg, NOW, since_ts=NOW - 7200, ensure_meta=_ensure(st, metas), failed_sigs=_no_failed)
        )
        assert (
            out["landing_pos"] == "+1" and st.coverage("measure:landing_pos")["pos"] == "+1"
        )  # we were 2nd in our slot (index 1)
        from snipetech.qualify.run import effective_landing_pos

        assert effective_landing_pos(st, cfg.qualify) == "+1"
        st.set_coverage("measure:landing_pos", {})
        assert effective_landing_pos(st, cfg.qualify) == "+6"
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
