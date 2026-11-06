"""Qualify gates: history, venue, crowding, relationships, the score and the verdict matrix (synthetic tape, no network)."""

import pytest

from snipetech.config import Config, QualifyConfig
from snipetech.qualify.gates import (
    crowding,
    history_gate,
    normalise_history,
    relationship_gate,
    score,
    venue_share,
    verdict_for,
)
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000


@pytest.fixture
def cfg():
    return QualifyConfig()


def test_config_defaults_and_registration():
    c = Config()
    assert c.qualify.landing_pos == "+6" and c.qualify.min_n_tape == 20 and c.qualify.min_n_tape_strong == 10
    assert (
        c.qualify.review_qualified_h == 24 and c.qualify.review_candidate_h == 72 and c.qualify.review_retired_h == 168
    )
    assert c.qualify.lab_interval_h == 2.0 and c.qualify.max_wallets == 300


def test_normalise_history_prefers_solanatracker_then_birdeye_then_vybe():
    st90 = {
        "trades": 300,
        "win_rate": 64.0,
        "realized_usd": 2500.0,
        "max_single_pnl_usd": 500.0,
        "tokens": 120,
        "trading_days": 40,
    }
    be = {"hist30": {"trades": 80, "win_rate": 0.55, "realized_usd": 900.0, "unique_tokens": 30}}
    vy = {"trades": 50, "win_rate": 0.7, "realized_usd": 400.0, "tokens": 20}
    h = normalise_history({"solanatracker": st90, "birdeye": be, "vybe": vy})
    assert (
        h["source"] == "solanatracker"
        and h["trades"] == 300
        and h["win_rate"] == 64.0
        and h["max_single_share"] == pytest.approx(0.2)
    )
    assert h["coins"] == 120 and h["days"] == 40
    h = normalise_history({"birdeye": be, "vybe": vy})
    assert (
        h["source"] == "birdeye"
        and h["win_rate"] == pytest.approx(55.0)
        and h["coins"] == 30
        and h["realized_usd"] == 900.0
    )
    h = normalise_history({"vybe": vy})
    assert h["source"] == "vybe" and h["win_rate"] == pytest.approx(70.0) and h["trades"] == 50
    assert normalise_history({"tape": {"positions": 12}})["trades"] is None


def test_history_gate(cfg):
    ok, strong, why = history_gate(
        {"trades": 300, "win_rate": 64.0, "realized_usd": 2500.0, "max_single_share": 0.2, "coins": 120}, cfg
    )
    assert ok is True and strong is True and why == []
    ok, strong, why = history_gate(
        {"trades": 300, "win_rate": 50.0, "realized_usd": 2500.0, "max_single_share": 0.2, "coins": 120}, cfg
    )
    assert ok is True and strong is False
    ok, strong, why = history_gate(
        {"trades": 10, "win_rate": 64.0, "realized_usd": 2500.0, "max_single_share": 0.9, "coins": 5}, cfg
    )
    assert ok is False and strong is False and any("trades" in r for r in why) and any("single" in r for r in why)
    ok, strong, why = history_gate(
        {"trades": 5000, "win_rate": 64.0, "realized_usd": 100.0, "max_single_share": None, "coins": 5}, cfg
    )
    assert ok is False and any("3000" in r for r in why) and any("realized" in r for r in why)
    ok, strong, why = history_gate({"trades": None}, cfg)
    assert ok is None and strong is False and why == ["no history from any source"]


def _world(store):
    """Three coins. W buys early in each; followers crowd the first coin, nobody follows on the third."""
    W, F1, F2, F3, D = addr("W"), addr("F1"), addr("F2"), addr("F3"), addr("dev")
    m1 = Market(store, addr("m1"), D, NOW, SLOT)
    m1.buy(D, 1.0, 0)
    tw = m1.buy(W, 1.0, 10)
    m1.buy(F1, 0.5, 11)
    m1.buy(F2, 0.5, 12)
    t3 = m1.buy(F3, 0.5, 13)
    m1.buy(addr("late"), 0.5, 200)
    m1.commit()
    m2 = Market(store, addr("m2"), D, NOW + 1000, SLOT + 2500)
    m2.buy(D, 1.0, 0)
    m2.buy(W, 1.0, 10)
    m2.buy(F1, 0.5, 11)
    m2.buy(F2, 0.3, 20)  # 4 s later: not within 1 s
    m2.commit()
    m3 = Market(store, addr("m3"), D, NOW + 2000, SLOT + 5000)
    m3.buy(D, 1.0, 0)
    m3.buy(W, 1.0, 10)
    m3.commit()
    return W, tw, t3


def test_venue_share_and_crowding():
    st = Store(":memory:")
    W, tw, t3 = _world(st)
    assert venue_share(st, addr("nobody"), NOW - 10) is None  # fewer than 5 trades → unknown
    # W has only 3 trades → unknown; add two more on the curve and one off-venue to get a share
    m4 = Market(st, addr("m4"), addr("dev"), NOW + 3000, SLOT + 7500)
    m4.buy(W, 0.2, 5)
    m4.sell(W, 1.0, 6)
    m4.commit()
    st._exec("UPDATE trades SET venue='launchlab' WHERE mint=? AND user=?", (addr("m4"), W))
    assert venue_share(st, W, NOW - 10) == pytest.approx(3 / 5)
    c = crowding(st, W, NOW - 10)
    assert c["n"] == 4 and c["followers_1s"] == 0.5  # per first buy: 3, 1, 0, 0 followers within 1 s → median 0.5
    gap = (t3.q_after / t3.b_after) / (tw.q_after / tw.b_after) - 1
    assert c["gap3_pct"] == pytest.approx(gap * 100)  # only the first coin had three followers; median of one value
    assert crowding(st, addr("nobody"), NOW - 10) == {"followers_1s": None, "gap3_pct": None, "n": 0}
    st.close()


def test_relationship_gate_vetoes_bundle_members():
    st = Store(":memory:")
    B, S, D = addr("bundle"), addr("sniper"), addr("dev")
    for i in range(2):  # B buys in the create slot of two launches (no fingerprints → every slot-0 buyer is bundle)
        m = Market(st, addr(f"c{i}"), D, NOW + i * 1000, SLOT + i * 2500)
        m.buy(D, 1.0, 0)
        m.buy(B, 0.5, 0)
        m.buy(S, 0.5, 3)
        m.commit()
    ok, why = relationship_gate(st, B)
    assert ok is False and any("bundle" in r for r in why) and "2" in " ".join(why)
    ok, why = relationship_gate(st, S)
    assert ok is True and why == []
    st.close()


def test_relationship_gate_reads_edges_table_when_present():
    st = Store(":memory:")
    st.upsert_edge(addr("dev"), addr("paid"), "paid_by_dev", NOW, 10)
    ok, why = relationship_gate(st, addr("paid"))
    assert ok is False and any("paid_by_dev" in r for r in why)
    ok, _ = relationship_gate(st, addr("clean"))
    assert ok is True
    st.close()


def test_score_formula():
    assert score(0.01, 30, 1.0, 0.0) == pytest.approx(0.01)
    assert score(0.01, 15, 1.0, 0.0) == pytest.approx(0.005)
    assert score(0.01, 30, 0.5, 0.0) == pytest.approx(0.005)
    assert score(0.01, 30, 1.0, 5.0) == pytest.approx(0.005)
    assert score(0.01, 30, 1.0, 50.0) == pytest.approx(0.002)  # the crowd factor floors at 0.2
    assert score(0.01, 30, None, None) == pytest.approx(0.01)  # unknown venue / crowd count as neutral
    assert score(None, 30, 1.0, 0.0) == 0.0 and score(-0.01, 30, 1.0, 0.0) == pytest.approx(-0.01)


def test_verdict_status_matrix(cfg):
    strong = {
        "trades": 300,
        "win_rate": 64.0,
        "realized_usd": 2500.0,
        "max_single_share": 0.2,
        "coins": 120,
        "days": 90,
        "source": "solanatracker",
    }
    weak = dict(strong, win_rate=50.0)
    quiet = {"followers_1s": 0.0, "gap3_pct": 2.0, "n": 12}
    row = lambda n, mean=0.02, lower=0.004: {
        "n": n,
        "mean": mean,
        "ev_lower": lower,
        "win_rate": 0.6,
        "total": n * mean,
        "exit": "TP+40% SL-15%",
    }

    v = verdict_for("W", strong, row(10), 0.9, quiet, True, [], cfg, NOW)
    assert v["status"] == "qualified" and v["next_review"] == NOW + 24 * 3600 and v["n_tape"] == 10 and v["score"] > 0
    assert (
        v["hist_win_rate"] == 64.0 and v["hist_trades"] == 300 and v["venue_share"] == 0.9 and v["landing_pos"] == "+6"
    )

    v = verdict_for("W", weak, row(19), 0.9, quiet, True, [], cfg, NOW)
    assert v["status"] == "candidate" and v["next_review"] == NOW + 72 * 3600 and any("19" in r for r in v["reasons"])

    v = verdict_for("W", weak, row(25), 0.9, {"followers_1s": 4.0, "gap3_pct": 20.0, "n": 20}, True, [], cfg, NOW)
    assert v["status"] == "candidate" and any("gap" in r for r in v["reasons"])

    v = verdict_for("W", weak, row(25), 0.9, quiet, True, [], cfg, NOW)
    assert v["status"] == "qualified"

    v = verdict_for("W", strong, row(25), 0.9, quiet, False, ["bundle member in 2 launches"], cfg, NOW)
    assert (
        v["status"] == "retired"
        and v["next_review"] == NOW + 168 * 3600
        and "bundle member in 2 launches" in v["reasons"]
    )

    v = verdict_for("W", strong, row(25), 0.3, quiet, True, [], cfg, NOW)
    assert v["status"] == "retired" and any("venue" in r for r in v["reasons"])

    v = verdict_for(
        "W",
        {"trades": 10, "win_rate": 20.0, "realized_usd": 5.0, "max_single_share": 0.9, "coins": 3},
        row(25),
        0.9,
        quiet,
        True,
        [],
        cfg,
        NOW,
    )
    assert v["status"] == "retired" and any("history" in r or "trades" in r for r in v["reasons"])

    v = verdict_for(
        "W", {"trades": None}, row(25), 0.9, quiet, True, [], cfg, NOW
    )  # tape only, no API history: the tape decides
    assert v["status"] == "qualified" and any("no history" in r for r in v["reasons"])

    v = verdict_for("W", strong, row(25, mean=-0.01, lower=-0.02), 0.9, quiet, True, [], cfg, NOW)
    assert v["status"] == "retired" and any("loses" in r for r in v["reasons"])

    v = verdict_for("W", strong, row(25, mean=0.01, lower=-0.001), 0.9, quiet, True, [], cfg, NOW)
    assert v["status"] == "candidate" and any("lower" in r for r in v["reasons"])

    v = verdict_for("W", strong, None, None, {"followers_1s": None, "gap3_pct": None, "n": 0}, True, [], cfg, NOW)
    assert (
        v["status"] == "candidate"
        and v["reasons"] == ["no tape data"]
        and v["next_review"] == NOW + 72 * 3600
        and v["score"] == 0.0
    )
