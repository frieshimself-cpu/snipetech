"""The fee-test report: fills of two arms on the same leader signal are paired (same coin, within a few seconds), and the
paired differences say whether paying moved us up; a text rendering the owner can read."""


from snipetech.cli import build_parser
from snipetech.measure.report import fee_test_report, pair_arms, render_fee_test
from snipetech.store import Store

NOW = 1_793_923_200
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


def _fill(sig, ts, mint, arm, *, behind, pos, gap, prio, tip, lead="L1", side="buy", pnl=None):
    return {"sig": sig, "ts": ts, "slot": 1, "pos_in_slot": pos, "slot_txs": 8, "wallet": W, "wallet_copied": lead, "mint": mint, "side": side,
            "sol": 0.2, "tokens": 100.0, "trades_behind": behind, "price_gap_pct": gap, "priority_lamports": prio, "tip_lamports": tip,
            "bid_arm": arm, "pnl_sol": pnl}  # fmt: skip


def _fills():
    c = lambda sig, ts, mint, **k: _fill(sig, ts, mint, "control", prio=1_000_000, tip=2_000_000, **k)
    p = lambda sig, ts, mint, **k: _fill(sig, ts, mint, "paid", prio=20_000_000, tip=20_000_000, **k)
    return [
        c("c1", NOW + 0, "M1", behind=3, pos=4, gap=30.0),
        p("p1", NOW + 1, "M1", behind=1, pos=2, gap=12.0),  # paid earlier
        c("c2", NOW + 100, "M2", behind=2, pos=3, gap=20.0),
        p("p2", NOW + 102, "M2", behind=2, pos=3, gap=21.0),  # tie
        c("c3", NOW + 200, "M3", behind=4, pos=5, gap=40.0),  # control only: no pair
        p("p4", NOW + 300, "M4", behind=0, pos=1, gap=5.0),  # paid only: no pair
        c("c5", NOW + 400, "M5", behind=1, pos=2, gap=10.0),
        p("p5", NOW + 460, "M5", behind=1, pos=2, gap=10.0),  # 60 s apart: not a pair
        _fill(
            "s1", NOW + 50, "M1", "control", behind=None, pos=0, gap=None, prio=1_000_000, tip=0, side="sell", pnl=-0.01
        ),
        _fill("s2", NOW + 51, "M1", "paid", behind=None, pos=0, gap=None, prio=1_000_000, tip=0, side="sell", pnl=0.02),
        _fill("f1", NOW + 2, "M1", "paid", behind=None, pos=None, gap=None, prio=20_000_000, tip=0, side="failed"),
    ]


def test_pair_arms_matches_same_coin_within_the_window():
    out = pair_arms(_fills(), "control", "paid", window_secs=20)
    assert out["n_pairs"] == 2 and {p["mint"] for p in out["pairs"]} == {"M1", "M2"}
    assert (
        out["mean_delta_behind"] == -1.0
        and out["mean_delta_pos"] == -1.0
        and out["paid_earlier"] == 1
        and out["ties"] == 1
    )
    assert out["control_only"] == 2 and out["paid_only"] == 2  # c3, c5 / p4, p5 (c5 and p5 are 60 s apart: not a pair)


def test_fee_test_report_and_rendering():
    st = Store(":memory:")
    try:
        for f in _fills():
            st.put_fill(f)
        rep = fee_test_report(st, W, NOW - 60, arms=("control", "paid"))
        a = rep["arms"]
        assert a["control"]["buys"] == 4 and a["paid"]["buys"] == 4 and a["paid"]["failed"] == 1
        assert a["control"]["realised_sol"] == -0.01 and a["paid"]["realised_sol"] == 0.02
        assert round(a["paid"]["fees_sol"], 6) == round(4 * 0.040005 + 0.020005, 6)
        assert rep["paired"]["n_pairs"] == 2 and rep["verdict"].startswith("paid landed earlier")
        text = render_fee_test(rep)
        assert "paid" in text and "control" in text and "2 paired" in text and "SOL" in text
    finally:
        st.close()


def test_verdict_is_blunt_without_pairs():
    st = Store(":memory:")
    try:
        st.put_fill(_fill("c1", NOW, "M1", "control", behind=3, pos=4, gap=30.0, prio=1_000_000, tip=2_000_000))
        rep = fee_test_report(st, W, NOW - 60)
        assert rep["paired"]["n_pairs"] == 0 and "no paired" in rep["verdict"]
    finally:
        st.close()


def test_cli_measure_report_parses():
    a = build_parser().parse_args(["measure", "report", "--since", "123"])
    assert a.cmd == "measure" and a.action == "report" and a.since == 123
