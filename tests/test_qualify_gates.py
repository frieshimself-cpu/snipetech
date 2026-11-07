"""Qualify gates: history, venue, crowding, relationships, the score and the verdict matrix (synthetic tape, no network)."""

import pytest

from snipetech.config import Config, QualifyConfig
from snipetech.qualify.gates import (
    create_slot_share,
    crowding,
    forward_gate,
    history_gate,
    keep_armed,
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


def test_verdict_for_keeps_the_copy_plan_and_gap3(cfg):
    from snipetech.qualify.gates import COPY_PLAN_KEYS

    lab = {"n": 40, "mean": 0.017, "ev_lower": 0.004, "win_rate": 0.9, "total": 0.66, "exit": "STALE1s SL-30% hold300s",
           "min_leader_sol": 0.594, "rebuy_share": 0.0, "leader_first_sell_p50_s": 4.0, "leader_one_tx_exit_share": 0.0,
           "leader_biggest_sell_p50": 0.999, "leader_style": "mixed", "exit_name": "lab",
           "exit_params": {"stale_secs": 1.0, "stop_loss": 0.3}, "lab_version": 2, "moments_all": 43}  # fmt: skip
    crowd = {"followers_1s": 1.0, "gap3_pct": 3.5, "n": 40}
    v = verdict_for("G2oZ", {"trades": None}, lab, 1.0, crowd, True, [], cfg, NOW)
    assert v["status"] == "qualified" and v["gap3_pct"] == 3.5
    assert v["copy_plan"] == {k: lab[k] for k in COPY_PLAN_KEYS}
    assert {"min_leader_sol", "exit_name", "exit_params", "leader_style", "rebuy_share", "leader_first_sell_p50_s",
            "leader_one_tx_exit_share", "lab_version"} <= set(v["copy_plan"])  # fmt: skip
    # a lab row without a floor still gets a copy plan (with no floor): a lab verdict always carries one, so the store
    # never takes it for a verdict of the old lab and queues it again (store.candidates' old-lab rule)
    old = {k: lab[k] for k in ("n", "mean", "ev_lower", "win_rate", "total", "exit")}
    plan = verdict_for("G2oZ", {"trades": None}, old, 1.0, crowd, True, [], cfg, NOW)["copy_plan"]
    assert plan is not None and plan["min_leader_sol"] is None and plan["lab_version"] is None
    assert verdict_for("G2oZ", {"trades": None}, None, None, crowd, True, [], cfg, NOW)["copy_plan"] is None


def test_create_slot_share_counts_the_first_buys_that_sat_in_the_coins_create_slot():
    """Research round 2 (7 Nov): the 14:00 search's rows 7zvP2x and 3yrJE1 had 10 of 10 and 13 of 20 of their first
    buys in the coin's create slot (the block the coin was made in), where no copy can land. The share counts the first
    buys the qualify lab scores (0.1 SOL or more), in coins whose create the tape recorded, never the wallet's own
    coins (its buy there is the creator's)."""
    st = Store(":memory:")
    C, D = addr("C"), addr("dev")
    for k in range(10):  # two first buys in the create slot, eight later
        m = Market(st, addr(f"cs{k}"), D, NOW + k * 100, SLOT + k * 250)
        m.buy(D, 1.0, 0, sig=m.create_sig)
        m.buy(C, 0.5, 0 if k < 2 else 5)
        m.commit()
    own = Market(st, addr("cs-own"), C, NOW + 2000, SLOT + 5000)
    own.buy(C, 1.0, 0, sig=own.create_sig)
    own.commit()
    dust = Market(st, addr("cs-dust"), D, NOW + 2100, SLOT + 5250)  # a first buy under 0.1 SOL is no lab moment
    dust.buy(D, 1.0, 0, sig=dust.create_sig)
    dust.buy(C, 0.05, 0)
    dust.buy(C, 0.5, 6)
    dust.commit()
    old = Market(st, addr("cs-old"), D, NOW + 2200, SLOT + 5500)  # its create is not on the tape: slot unknown
    old.buy(D, 1.0, 0, sig=old.create_sig)
    old.buy(C, 0.5, 0)
    old.commit()
    st._exec("DELETE FROM launches WHERE mint=?", (addr("cs-old"),))
    assert create_slot_share(st, C, NOW - 10) == {"in_create": 2, "first_buys": 10, "share": 0.2}
    assert create_slot_share(st, C, NOW + 50) == {"in_create": 1, "first_buys": 9, "share": 0.1111}  # since the tape
    assert create_slot_share(st, addr("nobody"), NOW - 10) is None
    st.close()


def test_a_wallet_with_a_tenth_of_its_first_buys_in_create_slots_is_not_qualified(cfg):
    """10 % or more of its first buys in a coin's create slot: whatever the lab says, its verdict is retired and says
    why (research round 2: drop wallets with 10 %+ of first buys there)."""
    lab = {"n": 25, "mean": 0.02, "ev_lower": 0.004, "win_rate": 0.6, "total": 0.5, "exit": "STALE1s SL-30% hold300s"}
    quiet = {"followers_1s": 0.0, "gap3_pct": 2.0, "n": 25}
    tenth = {"in_create": 3, "first_buys": 30, "share": 0.1}
    v = verdict_for("W", {"trades": None}, lab, 1.0, quiet, True, [], cfg, NOW, create=tenth)
    assert v["status"] == "retired" and v["next_review"] == NOW + 168 * 3600 and v["create_slot"] == tenth
    assert any("3 of 30 first buys" in r and "create slot" in r for r in v["reasons"]), v["reasons"]
    under = {"in_create": 2, "first_buys": 30, "share": 0.0667}
    v = verdict_for("W", {"trades": None}, lab, 1.0, quiet, True, [], cfg, NOW, create=under)
    assert v["status"] == "qualified" and v["create_slot"] == under
    v = verdict_for("W", {"trades": None}, lab, 1.0, quiet, True, [], cfg, NOW)  # nothing known: no veto
    assert v["status"] == "qualified" and v["create_slot"] is None
    v = verdict_for("W", {"trades": None}, lab, 1.0, quiet, False, ["bundle member in 2 launches"], cfg, NOW,
                    create=tenth)  # fmt: skip
    # without fingerprints every create-slot buyer is a bundle member: both vetoes are said, the relationship first
    assert v["status"] == "retired" and v["reasons"][0] == "bundle member in 2 launches"
    assert any("3 of 30 first buys" in r for r in v["reasons"][1:])


def _in_sample_pass(cfg):
    """A verdict every in-sample gate passed at the qualify lab's landing: what the stage wrote as qualified until
    7 Nov."""
    from dataclasses import replace

    lab = {"n": 30, "mean": 0.02, "ev_lower": 0.006, "win_rate": 0.6, "total": 0.6, "exit": "STALE1s SL-30% hold300s",
           "min_leader_sol": 0.3, "exit_name": "lab", "lab_version": 2}  # fmt: skip
    quiet = {"followers_1s": 0.0, "gap3_pct": 2.0, "n": 30}
    v = verdict_for("W", {"trades": None}, lab, 1.0, quiet, True, [], replace(cfg, landing_pos="+3x"), NOW,
                    create={"in_create": 0, "first_buys": 30, "share": 0.0})  # fmt: skip
    assert v["status"] == "qualified"
    return v


PICK = NOW - 10 * 3600  # picked ten hours ago


def _copies(n, pnl, start):
    """`n` copies a minute apart from `start`, each making `pnl` SOL (a number, or a function of its index)."""
    return [[start + 60 * k, float(pnl(k)) if callable(pnl) else float(pnl)] for k in range(n)]


def test_a_wallet_with_no_later_tape_is_forward_pending_and_nothing_else_changes(cfg):
    """Research round 2 (7 Nov): 16 of 24 verdict wallets never bought again within 9-13 h. Picked now, with no tape
    after its pick, a wallet is "forward pending": not qualified, so the book does not arm it, and re-checked as often
    as a qualified wallet; every number of its verdict stays what the lab said."""
    v = _in_sample_pass(cfg)
    out = forward_gate(v, _copies(30, 0.02, NOW - 7200), None, [[NOW - 7200, NOW]], cfg, NOW)
    same = [k for k in v if k not in ("status", "next_review", "reasons", "forward", "gate")]
    assert {k: out[k] for k in same} == {k: v[k] for k in same}
    assert out["status"] == "candidate" and out["next_review"] == NOW + 24 * 3600
    assert out["forward"] == {"picked_at": NOW, "tape_hours": 0.0, "n": 0, "mean": None, "lower": None,
                              "win_rate": None, "status": "pending", "min_n": 20, "min_hours": 6.0,
                              "first_picked_at": NOW, "first_n": 0, "first_mean": None, "off_tape": 0}  # fmt: skip
    assert out["reasons"][:-1] == v["reasons"] and out["reasons"][-1].startswith("forward pending: 0 later copies")
    assert v["status"] == "qualified" and "forward" not in v  # the verdict handed in is left as it was


def test_a_hot_streak_that_stops_after_the_pick_is_not_qualified(cfg):
    """Research round 2: the method made +0.063 a copy (37) in the 6 h before the 14:30 verdicts and -0.0147 (257) on the
    tape after them: the verdicts catch hot streaks that then stop. 20 later copies at a loss: retired, and it says so."""
    v = _in_sample_pass(cfg)
    hot = _copies(30, 0.05, PICK - 6 * 3600) + _copies(20, -0.01, PICK + 3600)
    out = forward_gate(v, hot, PICK, [[PICK - 7 * 3600, NOW]], cfg, NOW)
    assert out["status"] == "retired" and out["next_review"] == NOW + 168 * 3600
    fwd = out["forward"]
    assert (fwd["status"], fwd["n"], fwd["picked_at"], fwd["tape_hours"]) == ("failed", 20, PICK, 10.0)
    assert fwd["mean"] == pytest.approx(-0.01) and fwd["win_rate"] == 0.0
    assert out["reasons"][-1].startswith("loses after it was picked: mean -0.0100 SOL over 20 later copies")


def test_a_wallet_that_holds_up_on_its_later_copies_stays_qualified(cfg):
    v = _in_sample_pass(cfg)
    steady = _copies(30, 0.02, PICK - 6 * 3600) + _copies(25, lambda k: 0.03 if k % 2 else -0.01, PICK + 3600)
    out = forward_gate(v, steady, PICK, [[PICK - 7 * 3600, NOW]], cfg, NOW)
    assert out["status"] == "qualified" and out["next_review"] == v["next_review"]
    assert out["forward"]["status"] == "passed" and out["forward"]["n"] == 25
    assert out["forward"]["mean"] == pytest.approx((12 * 0.03 - 13 * 0.01) / 25)
    assert out["reasons"][-1].startswith("holds after it was picked: mean +0.0092 SOL over 25 later copies")


def test_the_forward_bar_is_the_in_sample_rule_on_twenty_later_copies_over_six_hours_of_later_tape(cfg):
    """The bar: at least 20 of its own copies after the pick (the in-sample gate's count for a wallet without a strong
    history) on at least 6 h of tape after it, then the in-sample gate's own rule on them: a lower bound above 0
    passes, a mean below 0 retires, anything between waits. Research round 2's later copies spread about 0.15 SOL a
    copy (257 at -0.0147, lower bound -0.0225): on a mean above 0 alone such a loser would pass 20 copies one time in
    three, on the lower bound about one in ten."""
    v = _in_sample_pass(cfg)
    few = forward_gate(v, _copies(19, 0.02, PICK + 60), PICK, [[PICK, NOW]], cfg, NOW)
    assert few["status"] == "candidate" and few["forward"]["status"] == "pending" and few["forward"]["n"] == 19
    assert few["reasons"][-1].startswith("forward pending: 19 later copies (needs 20) over 10.0 h")
    short = forward_gate(v, _copies(25, 0.02, PICK + 60), PICK, [[PICK, PICK + 5 * 3600]], cfg, NOW)
    assert short["status"] == "candidate" and short["forward"]["tape_hours"] == 5.0
    holes = [[PICK - 3600, PICK + 3 * 3600], [PICK + 5 * 3600, PICK + 8 * 3600]]  # 6 h of tape in two runs
    assert forward_gate(v, _copies(25, 0.02, PICK + 60), PICK, holes, cfg, NOW)["status"] == "qualified"
    noisy = forward_gate(v, _copies(20, lambda k: 0.05 if k % 2 else -0.04, PICK + 60), PICK, [[PICK, NOW]], cfg, NOW)
    assert noisy["forward"]["mean"] == pytest.approx(0.005) and noisy["forward"]["lower"] <= 0
    assert noisy["status"] == "candidate" and noisy["forward"]["status"] == "pending"
    assert noisy["reasons"][-1].startswith("forward pending: mean +0.0050 SOL over 20 later copies")
    even = forward_gate(v, _copies(20, lambda k: 0.01 if k % 2 else -0.01, PICK + 60), PICK, [[PICK, NOW]], cfg, NOW)
    assert even["status"] == "candidate" and even["forward"]["mean"] == pytest.approx(0.0)  # not above 0, not below
    on_pick = forward_gate(v, _copies(25, 0.02, PICK), PICK, [[PICK, NOW]], cfg, NOW)  # the first one at the pick
    assert on_pick["forward"]["n"] == 24  # a copy at the very second of the pick was in the window it was picked on


ARMED_ON = NOW - 28 * 3600
# the verdict the book armed a wallet on before these rules (7aPo3n, 7 Nov 16:05: n 35, +0.0271, lower bound +0.0049)
OLD_RULES = {"wallet": "W", "computed_at": ARMED_ON, "landing_pos": "+6", "n_tape": 35, "mean_sol": 0.0271,
             "lower_sol": 0.0049, "hist_days": None, "hist_realized_usd": None, "hist_win_rate": None, "hist_trades": None,
             "crowding": 0.0, "venue_share": 1.0, "score": 0.0049, "status": "qualified", "next_review": ARMED_ON + 86400,
             "reasons": ["no history from any source"], "copy_plan": {"min_leader_sol": 0.5, "exit_name": "lab"},
             "gap3_pct": 0.8, "create_slot": None, "forward": None, "kept_armed": None, "id": 7}  # fmt: skip


def test_a_wallet_the_book_copied_before_these_rules_keeps_the_verdict_it_was_armed_on(cfg):
    """E (research round 2, 7 Nov): the book arms only qualified wallets and drops any other, but a wallet it already
    copies is not stopped by these rules; the owner decides (7aPo3n: keep it armed as a live measurement, judge it at 30
    copies). Its verdict stays the one it was armed on, as the book reads it (status, score, copy plan, landing,
    numbers), and says first that it no longer qualifies and why; what these rules found is in `kept_armed`."""
    new = forward_gate(_in_sample_pass(cfg), _copies(30, 0.02, ARMED_ON - 7200), ARMED_ON, [[ARMED_ON, NOW]], cfg, NOW)
    assert new["status"] == "candidate" and new["forward"]["status"] == "pending"
    kept = keep_armed(new, OLD_RULES, True, cfg, NOW)
    book = ["status", "score", "copy_plan", "landing_pos", "n_tape", "mean_sol", "lower_sol", "gap3_pct", "crowding",
            "venue_share"]  # fmt: skip
    assert {k: kept[k] for k in book} == {k: OLD_RULES[k] for k in book}
    assert kept["computed_at"] == NOW and kept["next_review"] == NOW + 24 * 3600 and "id" not in kept
    assert kept["reasons"][0].startswith("no longer qualifies under the 7 Nov verdict rules (forward pending: 0 later")
    assert "the owner decides" in kept["reasons"][0] and kept["reasons"][1:] == []
    assert kept["forward"] == new["forward"] and kept["create_slot"] == new["create_slot"]
    assert kept["kept_armed"] == {"armed_on": ARMED_ON, "armed_score": OLD_RULES["score"], "kept": True,
                                  "new": {k: new[k] for k in ("status", "landing_pos", "n_tape", "mean_sol", "lower_sol",
                                                              "score", "reasons")}}  # fmt: skip


def test_keeping_an_armed_wallet_covers_only_what_these_rules_changed(cfg):
    new = forward_gate(_in_sample_pass(cfg), [], ARMED_ON, [[ARMED_ON, NOW]], cfg, NOW)  # forward pending
    assert keep_armed(new, OLD_RULES, False, cfg, NOW) == new  # nothing copies it: the new verdict as it is
    quiet = {"followers_1s": 0.0, "gap3_pct": 2.0, "n": 30}
    lab = {"n": 30, "mean": 0.02, "ev_lower": 0.006, "win_rate": 0.6, "total": 0.6, "exit": "x"}
    bundle = verdict_for("W", {"trades": None}, lab, 1.0, quiet, False, ["bundle member in 2 launches"], cfg, NOW)
    assert keep_armed(bundle, OLD_RULES, True, cfg, NOW) == bundle  # an old rule stops it, as it did before
    venue = verdict_for("W", {"trades": None}, lab, 0.3, quiet, True, [], cfg, NOW)
    assert keep_armed(venue, OLD_RULES, True, cfg, NOW) == venue
    crowded = verdict_for("W", {"trades": None}, lab, 1.0, {"followers_1s": 3.0, "gap3_pct": 40.0, "n": 30}, True, [],
                          cfg, NOW)  # fmt: skip
    assert keep_armed(crowded, OLD_RULES, True, cfg, NOW) == crowded
    loses = verdict_for("W", {"trades": None}, dict(lab, mean=-0.01, ev_lower=-0.02), 1.0, quiet, True, [], cfg, NOW)
    assert keep_armed(loses, OLD_RULES, True, cfg, NOW)["status"] == "qualified"  # the new landing stops it: kept
    in_create = verdict_for("W", {"trades": None}, lab, 1.0, quiet, True, [], cfg, NOW,
                            create={"in_create": 2, "first_buys": 10, "share": 0.2})  # fmt: skip
    assert keep_armed(in_create, OLD_RULES, True, cfg, NOW)["status"] == "qualified"
    armed_since = dict(OLD_RULES, landing_pos="+3x", forward={"status": "passed"})  # qualified by these rules
    assert keep_armed(new, armed_since, True, cfg, NOW) == new  # the rules it met stop it like any other wallet
    assert keep_armed(new, dict(OLD_RULES, copy_plan=None), True, cfg, NOW) == new  # no plan: the book never booked it
    assert keep_armed(new, None, True, cfg, NOW) == new


def test_a_kept_wallet_stays_kept_while_it_is_copied_even_after_it_once_met_these_rules(cfg):
    pending = forward_gate(_in_sample_pass(cfg), [], ARMED_ON, [[ARMED_ON, NOW]], cfg, NOW)
    kept = keep_armed(pending, OLD_RULES, True, cfg, NOW)
    again = keep_armed(pending, kept, True, cfg, NOW + 86400)  # its next review
    assert again["status"] == "qualified" and again["score"] == OLD_RULES["score"]
    assert again["kept_armed"]["armed_on"] == ARMED_ON and again["kept_armed"]["kept"] is True
    passing = forward_gate(
        _in_sample_pass(cfg), _copies(25, 0.02, ARMED_ON + 60), ARMED_ON, [[ARMED_ON, NOW]], cfg, NOW
    )
    met = keep_armed(passing, kept, True, cfg, NOW + 2 * 86400)
    assert met["status"] == "qualified" and met["landing_pos"] == "+3x" and met["forward"]["status"] == "passed"
    # its own numbers now (its score is above the one it was armed on); the marker goes along
    assert met["kept_armed"] == {"armed_on": ARMED_ON, "armed_score": OLD_RULES["score"], "kept": False}
    later = keep_armed(pending, met, True, cfg, NOW + 3 * 86400)  # it falls back under the rules: still kept
    assert later["status"] == "qualified" and later["landing_pos"] == "+3x" and later["kept_armed"]["kept"] is True
    assert keep_armed(pending, kept, False, cfg, NOW + 86400) == pending  # no longer copied: the rules apply


def test_the_forward_check_leaves_a_wallet_the_in_sample_gates_stopped_alone(cfg):
    v = verdict_for("W", {"trades": None}, {"n": 5, "mean": 0.02, "ev_lower": 0.001, "win_rate": 0.6, "total": 0.1,
                                            "exit": "x"}, 1.0, {"followers_1s": 0.0, "gap3_pct": 2.0, "n": 5}, True,
                    [], cfg, NOW)  # fmt: skip
    assert v["status"] == "candidate"
    assert forward_gate(v, _copies(25, 0.02, PICK + 60), PICK, [[PICK, NOW]], cfg, NOW) == v


def test_a_copied_wallet_that_meets_these_rules_with_a_lower_score_keeps_the_score_it_was_armed_on(cfg):
    """Review of 7 Nov: a wallet the book copies that meets these rules got their score, usually lower at the copy
    landing (+3x) than at +6. Under the book's bar (0.005, or the owner's), the book's own ranking then dropped it and
    stopped its sniper: these rules stopping a wallet already copied, which the owner decides. While the book copies it,
    its score never falls below the one it was armed on; a higher one stands. The verdict says which, with both."""
    from dataclasses import replace

    lab = {"n": 30, "mean": 0.01, "ev_lower": 0.002, "win_rate": 0.6, "total": 0.3, "exit": "x", "min_leader_sol": 0.3,
           "exit_name": "lab", "lab_version": 2}  # fmt: skip
    quiet = {"followers_1s": 0.0, "gap3_pct": 2.0, "n": 30}
    low = verdict_for("W", {"trades": None}, lab, 1.0, quiet, True, [], replace(cfg, landing_pos="+3x"), NOW,
                      create={"in_create": 0, "first_buys": 30, "share": 0.0})  # fmt: skip
    low = forward_gate(low, _copies(25, 0.02, ARMED_ON + 60), ARMED_ON, [[ARMED_ON, NOW]], cfg, NOW)
    assert low["status"] == "qualified" and low["score"] == pytest.approx(0.002)
    armed = dict(OLD_RULES, score=0.01)
    held = keep_armed(low, armed, True, cfg, NOW)
    assert held["status"] == "qualified" and held["score"] == 0.01 and held["landing_pos"] == "+3x"
    assert held["kept_armed"] == {"armed_on": ARMED_ON, "armed_score": 0.01, "kept": False, "new_score": low["score"]}
    assert held["reasons"][-1].startswith("ranked at the score it was armed on (0.0100) while the book copies it")
    assert held["copy_plan"] == low["copy_plan"] and held["forward"] == low["forward"]  # its own numbers otherwise
    again = keep_armed(low, held, True, cfg, NOW + 86400)  # the next review: still the score it was armed on
    assert again["score"] == 0.01 and again["kept_armed"]["armed_score"] == 0.01
    pending = forward_gate(_in_sample_pass(cfg), [], ARMED_ON, [[ARMED_ON, NOW]], cfg, NOW)
    fallen = keep_armed(pending, held, True, cfg, NOW + 2 * 86400)  # under these rules again: the verdict it had
    assert fallen["score"] == 0.01 and fallen["kept_armed"]["kept"] is True
    assert fallen["kept_armed"]["armed_score"] == 0.01
    high = keep_armed(low, dict(OLD_RULES, score=0.001), True, cfg, NOW)  # its own score is the higher: it stands
    assert high["score"] == pytest.approx(0.002) and "new_score" not in high["kept_armed"]
    assert high["reasons"] == low["reasons"]
    assert keep_armed(low, armed, False, cfg, NOW) == low  # no longer copied: its own score


def test_a_wallet_picked_again_is_judged_on_its_copies_since_that_pick_and_must_not_lose_since_its_first(cfg):
    """Review of 7 Nov: the forward window never moved from a wallet's first pick, so a wallet that lost after it and
    qualified again on a new streak counted that streak (the copies that made it qualify again) as its forward copies.
    Now its forward copies are the ones after its latest pick (`picked_at`), and its copies since its first pick
    (`first_picked_at`) must not lose: 20 or more over 6 h or more of tape with a mean below 0 retire it, whatever the
    latest window says. The forward numbers say both, and how many of the copies the tape no longer holds."""
    first = NOW - 30 * 3600
    again = NOW - 8 * 3600  # picked again eight hours ago
    v = _in_sample_pass(cfg)
    lost = _copies(24, -0.03, first + 3600)  # lost on every copy after its first pick
    streak = _copies(10, 0.03, again - 7200)  # the streak that made it qualify again: not forward
    holds = _copies(25, lambda k: 0.03 if k % 2 else -0.01, again + 60)  # after the latest pick it pays
    out = forward_gate(v, lost + streak + holds, again, [[first - 3600, NOW]], cfg, NOW, first_picked_at=first)
    fwd = out["forward"]
    assert out["status"] == "retired" and fwd["status"] == "failed" and out["next_review"] == NOW + 168 * 3600
    assert (fwd["picked_at"], fwd["n"], fwd["first_picked_at"], fwd["first_n"]) == (again, 25, first, 59)
    assert fwd["first_mean"] == pytest.approx((24 * -0.03 + 10 * 0.03 + 12 * 0.03 - 13 * 0.01) / 59)
    assert out["reasons"][-1].startswith("loses since it was first picked")
    recovered = _copies(24, 0.02, first + 3600)  # it paid after its first pick too: only the latest window decides
    ok = forward_gate(v, recovered + streak + holds, again, [[first - 3600, NOW]], cfg, NOW, first_picked_at=first)
    assert ok["status"] == "qualified" and ok["forward"]["status"] == "passed" and ok["forward"]["n"] == 25
    early = forward_gate(v, lost + streak, again, [[first - 3600, NOW]], cfg, NOW, first_picked_at=first)
    assert early["status"] == "retired" and early["forward"]["n"] == 0  # nothing since the latest pick: it still lost
    same = forward_gate(v, holds, again, [[first - 3600, NOW]], cfg, NOW)  # never picked before: one window
    assert same["forward"]["first_picked_at"] == again and same["forward"]["first_n"] == 25
    pruned = forward_gate(v, lost + holds, again, [[again - 3600, NOW]], cfg, NOW, first_picked_at=first)
    assert pruned["forward"]["off_tape"] == 0 and pruned["forward"]["first_n"] == 49  # 24 of them before the tape
    off = forward_gate(v, holds, again - 7200, [[again, NOW]], cfg, NOW)
    assert off["forward"]["off_tape"] == 0
    carried = forward_gate(v, _copies(5, 0.03, again - 3000) + holds, again - 7200, [[again, NOW]], cfg, NOW)
    assert carried["forward"]["off_tape"] == 5 and carried["forward"]["n"] == 30
