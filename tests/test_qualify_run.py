"""The qualify runner: due candidates → labels → one lab (never beside another, never more often than the interval)
→ verdicts; wallets without tape data get a 72 h review without a lab."""

import json
import os

import pytest

from snipetech.analysis import lab as L
from snipetech.book.policy import buying_book_wallets
from snipetech.book.state import record_copying
from snipetech.cli import build_parser
from snipetech.config import Config
from snipetech.qualify.run import run_qualify
from snipetech.service import Service
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000


def _svc(tmp_path):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "jobs").mkdir(exist_ok=True)
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    return Service(cfg, store=st), st, data


def _tape(st, wallets, coins=3):
    """Each wallet buys early in a few coins so it has tape trades; the tape span covers them."""
    D = addr("dev")
    for i, w in enumerate(wallets):
        for k in range(coins):
            m = Market(
                st,
                addr(f"{w}:{k}"),
                D,
                NOW - 7000 + i * 100 + k * (3000 // coins),
                SLOT + i * 250 + k * (7500 // coins),
            )
            m.buy(D, 1.0, 0)
            m.buy(w, 0.5, 5)
            for j in range(4):  # a small crowd after it, so every coin has the trades a lab needs
                m.buy(addr(f"f{i}{k}{j}"), 0.3 + 0.1 * j, 6 + j)
            m.sell(w, 1.0, 30)
            m.sell(addr(f"f{i}{k}0"), 1.0, 40)
            m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})


def _running_lab(data, pid=None):
    (data / "jobs" / "20261106-100000-abcdef.json").write_text(json.dumps(
        {"id": "20261106-100000-abcdef", "argv": ["lab", "--latency", "0"], "started": NOW - 100, "ended": None, "exit_code": None,
         "running": True, "pid": pid or os.getpid()}))  # fmt: skip


def test_lab_queue_respects_running_lab(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["W1"])
        st.upsert_candidate("W1", "tape", {"positions": 3}, NOW - 3600)
        _running_lab(data)
        calls = []
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda wallets, pos: calls.append(wallets) or [])
        assert out["deferred"] is True and "lab" in out["reason"] and calls == [] and st.latest_verdicts() == []
    finally:
        st.close()


def test_lab_interval_defers_a_second_lab(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["W1", "W2"])
        st.upsert_candidate("W1", "tape", {"positions": 3}, NOW - 3600)
        calls = []
        runner = lambda wallets, pos: (
            calls.append(list(wallets))
            or [
                {"wallet": w, "n": 25, "mean": 0.02, "ev_lower": 0.005, "win_rate": 0.6, "total": 0.5, "exit": "x"}
                for w in wallets
            ]
        )
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        assert out["evaluated"] == 1 and calls == [["W1"]]
        st.upsert_candidate("W2", "tape", {"positions": 3}, NOW)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW + 600, lab_runner=runner)
        assert out["deferred"] is True and "min ago" in out["reason"] and len(calls) == 1
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW + 3 * 3600, lab_runner=runner)
        assert out["deferred"] is False and len(calls) == 2
    finally:
        st.close()


def test_run_writes_verdicts_from_lab_rows(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["GOOD", "WEAK", "BAD"])
        strong = {
            "trades": 300,
            "win_rate": 64.0,
            "realized_usd": 2500.0,
            "max_single_pnl_usd": 500.0,
            "tokens": 120,
            "trading_days": 40,
        }
        st.upsert_candidate("GOOD", "solanatracker", strong, NOW - 3600)
        st.upsert_candidate("WEAK", "solanatracker", dict(strong, win_rate=50.0), NOW - 3600)
        st.upsert_candidate("BAD", "solanatracker", strong, NOW - 3600)
        st.upsert_candidate("BANNED", "solanatracker", strong, NOW - 3600, status="banned")
        st.set_label("GOOD", "kol one", "kol")  # an existing label is never overwritten by the cand label
        rows = {"GOOD": {"n": 12, "mean": 0.02, "ev_lower": 0.005, "win_rate": 0.6, "total": 0.24, "exit": "TP+40%"},
                "WEAK": {"n": 12, "mean": 0.02, "ev_lower": 0.005, "win_rate": 0.6, "total": 0.24, "exit": "TP+40%"},
                "BAD": {"n": 30, "mean": -0.01, "ev_lower": -0.02, "win_rate": 0.3, "total": -0.3, "exit": "TP+40%"}}  # fmt: skip
        seen = {}

        def runner(wallets, pos):
            seen["wallets"], seen["pos"] = sorted(wallets), pos
            return [dict(rows[w], wallet=w) for w in wallets if w in rows]

        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        assert seen["wallets"] == ["BAD", "GOOD", "WEAK"] and seen["pos"] == "+3x"  # banned never measured
        assert out == {
            "evaluated": 3,
            "qualified": 0,
            "candidate": 2,
            "retired": 1,
            "forward_pending": 1,
            "forward_failed": 0,
            "kept_armed": 0,
            "armed_unknown": 0,
            "deferred": False,
            "no_tape": 0,
            "labelled": 2,
            "lab_wallets": 3,
        }
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        # GOOD passed every gate on the tape so far: picked now, it waits for its copies on the tape after now
        assert (
            by["GOOD"]["status"] == "candidate"
            and by["GOOD"]["forward"]["status"] == "pending"
            and by["WEAK"]["status"] == "candidate"
            and by["WEAK"]["forward"] is None
            and by["BAD"]["status"] == "retired"
        )
        assert (
            by["GOOD"]["next_review"] == NOW + 24 * 3600
            and by["GOOD"]["hist_win_rate"] == 64.0
            and by["GOOD"]["venue_share"] == 1.0
            and by["GOOD"]["n_tape"] == 12
        )
        labels = st._q("SELECT address, kind, label FROM labels ORDER BY address")
        assert (
            ("GOOD", "kol", "kol one") in labels
            and ("WEAK", "cand", "cand:WEAK") in labels
            and ("BAD", "cand", "cand:BAD") in labels
        )
        assert st.verdict_counts() == {"banned": 1, "candidate": 2, "retired": 1}
        assert st.coverage("qualify:last_lab") == {"ts": NOW, "wallets": 3}
        # a second run right away: nothing due (reviews are in the future) → no lab, nothing evaluated
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW + 60, lab_runner=runner)
        assert out["evaluated"] == 0 and out["deferred"] is False
    finally:
        st.close()


def test_no_tape_data_defers_without_a_lab(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})
        st.upsert_candidate(
            "NOTAPE", "vybe", {"trades": 80, "win_rate": 0.6, "realized_usd": 900.0, "tokens": 30}, NOW - 3600
        )
        calls = []
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda w, p: calls.append(w) or [])
        assert calls == [] and out["no_tape"] == 1 and out["evaluated"] == 1
        v = st.latest_verdicts()[0]
        assert v["status"] == "candidate" and v["reasons"] == ["no tape data"] and v["next_review"] == NOW + 72 * 3600
        assert st._q("SELECT count(*) FROM labels")[0][0] == 0  # nothing to label, nothing labelled
    finally:
        st.close()


def test_lab_has_a_cand_group_at_a_low_floor(tmp_path):
    st = Store(":memory:")
    st.set_label("C1", "cand:C1", "cand")
    st.set_label("T1", "tracked", "wallet")
    st.set_label("K1", "kol", "kol")
    groups = L.label_groups(st)
    assert groups["cand"] == {"C1", "T1"} and groups["wallet"] == {"T1"} and groups["kol"] == {"K1"}
    cand = [t for t in L.TRIGGERS if t.kind == "kol" and t.group == "cand"]
    assert len(cand) == 1 and cand[0].sol == L.KOL_MIN_SOL
    st.close()


def test_cli_qualify_run_parses():
    a = build_parser().parse_args(["qualify", "run", "--max", "50", "--json"])
    assert a.cmd == "qualify" and a.action == "run" and a.max == 50


def test_collect_can_restrict_to_the_cand_group(tmp_path):
    """The qualify lab only needs the cand trigger: restricting the groups skips every other trigger and every coin
    without a candidate's buy, which is what makes a lab every two hours affordable."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["C1"], coins=12)  # C1 buys in twelve coins (a trigger needs ten moments before it is ranked)
        D = addr("dev")
        for k in range(3):  # three coins nobody tracked trades in
            m = Market(st, addr(f"other:{k}"), D, NOW - 6500 + k * 1000, SLOT + 1000 + k * 2500)
            m.buy(D, 1.0, 0)
            m.buy(addr(f"x{k}"), 1.5, 3)
            m.buy(addr(f"y{k}"), 2.0, 4)
            for j in range(4):
                m.buy(addr(f"z{k}{j}"), 0.4, 5 + j)
            m.sell(addr(f"x{k}"), 1.0, 30)
            m.commit()
        st.set_label("C1", "cand:C1", "cand")
        cfg = svc.cfg
        cfg.verdict.bootstrap_samples = 50
        full = L.collect(st, cfg, 0.2, 0, "+1")
        only = L.collect(st, cfg, 0.2, 0, "+1", groups=("cand",))
        cand_ti = [i for i, t in enumerate(L.TRIGGERS) if t.kind == "kol" and t.group == "cand"][0]
        assert only["rows"] and all(r["trigger"] == cand_ti for r in only["rows"])
        assert {r["mint"] for r in only["rows"]} <= {addr(f"C1:{k}") for k in range(12)}
        assert len({r["trigger"] for r in full["rows"]}) > 1 and len(full["rows"]) > len(only["rows"])
        assert only["tape_hours"] == full["tape_hours"]
        rows = L.rank_wallets(only, cfg, st.labels(), top=10, min_n=1)
        assert [r["wallet"] for r in rows] == ["C1"]
    finally:
        st.close()


def test_the_lab_scores_wallets_with_the_exit_the_book_sends(tmp_path, monkeypatch):
    from snipetech.qualify.run import default_lab_runner

    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["W1"], coins=12)
        st.set_label("W1", "cand:W1", "cand")
        cfg = svc.cfg
        cfg.verdict.bootstrap_samples = 50
        seen = {}
        real = L.collect

        def spy(*args, **kw):
            seen["kw"] = kw
            return real(*args, **kw)

        monkeypatch.setattr(L, "collect", spy)
        (row,) = default_lab_runner(svc, cfg)(["W1"], "+6")
        # every exit on the menu is scored, lab first; the gating numbers are lab's, what book/run.py sends by default
        assert seen["kw"]["exits"] == list(L.menu_exits(cfg).values())
        assert seen["kw"]["exits"][0] == L.live_exits(cfg)["lab"] and row["exit"] == L.live_exits(cfg)["lab"].label()
        assert seen["kw"]["per_tx_sol"] == 0.0045  # nothing measured yet: what a live transaction costs, estimated
        assert row["wallet"] == "W1" and row["exit_name"] == "lab" and row["lab_version"] == L.LAB_VERSION
        assert row["exit_params"] == L.LIVE_EXITS["lab"].to_dict() and row["min_leader_sol"] == cfg.book.min_copy_sol
        assert row["exit_source"] == "lab" and row["exit_test_n"] == 5  # 12 moments: 5 held out, too few for its own
        # the verdict carries the plan to the book
        st.upsert_candidate("W1", "tape", {"positions": 12}, NOW - 3600)
        run_qualify(svc, cfg, data / "jobs", NOW)
        plan = st.latest_verdicts()[0]["copy_plan"]
        assert plan["exit_name"] == "lab" and plan["min_leader_sol"] == 0.3 and plan["lab_version"] == L.LAB_VERSION
        assert plan["exit_source"] == "lab" and plan["exit_menu"] == "copy-menu-v1"
    finally:
        st.close()


def test_the_lab_measures_due_wallets_the_hourly_copy_has_no_cand_label_for(tmp_path):
    """The lab reads its tape from the hourly read-only copy, but run_qualify labels a newly due wallet `cand` in the
    live database seconds before the lab runs, and never relabels a wallet that already has another label (a KOL).
    Both are measured on their first run: the trigger wallets come from the live labels plus the due list."""
    from snipetech.infra.snapshot_db import snapshot_db

    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["NEW", "KOL"], coins=12)
        st.set_label("KOL", "a kol", "kol")
        for w in ("NEW", "KOL"):
            st.upsert_candidate(w, "tape", {"positions": 12}, NOW - 3600)
        snapshot_db(data / "snipetech.db")  # the hourly copy, taken before this run labels anything
        svc.cfg.verdict.bootstrap_samples = 50
        assert svc.tape_store.path != st.path  # the lab reads the copy
        run_qualify(svc, svc.cfg, data / "jobs", NOW)
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        assert {w: by[w]["n_tape"] for w in by} == {"NEW": 12, "KOL": 12}, {w: by[w]["reasons"] for w in by}
        assert st.labels("kol") == {"KOL": "a kol"}  # the KOL keeps its label
    finally:
        svc.reset_tape_store()
        st.close()


def test_a_due_loser_keeps_its_lab_row_behind_a_thousand_better_candidates(tmp_path, monkeypatch):
    """The lab ranks every measured candidate. With more than 1 000 of them, the runner kept the best 1 000 before it
    picked the due wallets, so a due loser lost its lab row and read 'no tape data' instead of 'loses'."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["LOSER"])
        st.upsert_candidate("LOSER", "tape", {"positions": 25}, NOW - 3600)
        cfg = svc.cfg
        cfg.verdict.bootstrap_samples = 20

        def moments(w, n, pnl):
            return [{"mint": f"{w}:{k}", "trigger": 0, "ts": NOW - 7000 + k, "order": (k, 0), "pnl": [pnl], "who": w,
                     "lead_sol": 0.5} for k in range(n)]  # fmt: skip

        rows = [r for k in range(1000) for r in moments(f"WIN{k}", 4, 0.02)] + moments("LOSER", 25, -0.05)
        trig = L.Trigger("kol", sol=L.KOL_MIN_SOL, group="cand")
        lab = {"rows": rows, "exits": [L.live_exits(cfg)["lab"]], "triggers": [trig], "tape_hours": 2.0}
        monkeypatch.setattr(L, "collect", lambda *a, **kw: lab)
        run_qualify(svc, cfg, data / "jobs", NOW)
        v = st.latest_verdicts()[0]
        assert (v["status"], v["n_tape"]) == ("retired", 25), v["reasons"]
        assert any(r.startswith("loses at +3x: mean -0.0500 SOL over 25") for r in v["reasons"])
    finally:
        st.close()


def test_the_lab_charges_the_measured_round_trip_once_the_low_bid_has_30_fills():
    from snipetech.qualify.run import lab_tx_cost

    st, cfg = Store(":memory:"), Config()
    try:
        # nothing measured here yet: about what a live transaction cost when measured on 6 Nov (0.0047), never the flat
        # 0.0035, which is below every live cost measured (review of 7 Nov evening)
        assert lab_tx_cost(st, cfg) == 0.0045
        arm = {"arm": "control", "n": 20, "failed_n": 33, "mean_fee_sol": 0.003005, "mean_overhead_sol": 0.00335,
               "updated_at": NOW}  # fmt: skip
        st.put_bid_model(arm)
        assert lab_tx_cost(st, cfg) == 0.0045  # 20 fills: not enough yet
        st.put_bid_model(arm | {"n": 30})  # buy 0.003005 + sell at the same fees + 1.67 failed twins a trade
        assert lab_tx_cost(st, cfg) == pytest.approx((2 * 0.003005 + 0.00335) / 2)  # 0.0047 a transaction
        st.put_bid_model(arm | {"n": 30, "mean_fee_sol": 0.001, "mean_overhead_sol": 0.0})
        assert lab_tx_cost(st, cfg) == 0.0035  # never below the flat cost
        st.put_bid_model({"arm": "paid", "n": 50, "mean_fee_sol": 0.04, "mean_overhead_sol": 0.02, "updated_at": NOW})
        assert lab_tx_cost(st, cfg) == 0.0035  # only the low bid sets it
    finally:
        st.close()


def test_a_week_with_too_few_low_bid_fills_charges_the_last_measured_cost_and_says_when():
    """Review of 7 Nov evening: the bid model reads the last 7 days of fills, and with no live fills for a week the
    measure stage prunes the low bid's row (7 Nov 10:59 the wallet was emptied; nothing has traded since). Every lab,
    freeze and qualify run then fell back to the flat 0.0035, 0.002 SOL a trade too kind again. They charge the last
    cost the measure stage measured instead (measure:tx_cost), and say that it is and when it was measured."""
    from snipetech.qualify.run import lab_tx_cost, lab_tx_cost_info

    st, cfg = Store(":memory:"), Config()
    try:
        assert lab_tx_cost_info(st, cfg) == {"per_tx_sol": 0.0045, "per_tx_source": "estimate",
                                             "per_tx_measured_at": None}  # fmt: skip
        st.set_coverage("measure:tx_cost", {"per_tx_sol": 0.0047, "arm": "control", "n": 40, "ts": NOW - 8 * 86400})
        assert lab_tx_cost_info(st, cfg) == {"per_tx_sol": 0.0047, "per_tx_source": "last measured",
                                             "per_tx_measured_at": NOW - 8 * 86400}  # fmt: skip
        week = {"arm": "control", "n": 12, "failed_n": 3, "mean_fee_sol": 0.003, "mean_overhead_sol": 0.002,
                "updated_at": NOW}  # fmt: skip
        st.put_bid_model(week)
        assert lab_tx_cost(st, cfg) == 0.0047  # 12 fills this week: still too few, the last measured cost
        st.put_bid_model(week | {"n": 30})  # measured again: this week's own cost
        assert lab_tx_cost_info(st, cfg) == {"per_tx_sol": pytest.approx(0.004), "per_tx_source": "measured",
                                             "per_tx_measured_at": NOW}  # fmt: skip
    finally:
        st.close()


def test_the_rotation_may_run_the_lab_early_when_it_is_idle(tmp_path):
    # 7 Nov 09:30: the rotation started the wallet checks early (no lab for 33 min, 15,603 wallets waiting) and the
    # stage deferred them on its own 2 h interval ("last qualify lab 118 min ago"). --early skips that interval only;
    # a running lab job still defers it (one lab at a time).
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["W1", "W2"])
        st.upsert_candidate("W1", "tape", {"positions": 3}, NOW - 3600)
        calls = []
        runner = lambda wallets, pos: (
            calls.append(list(wallets))
            or [
                {"wallet": w, "n": 25, "mean": 0.02, "ev_lower": 0.005, "win_rate": 0.6, "total": 0.5, "exit": "x"}
                for w in wallets
            ]
        )
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        st.upsert_candidate("W2", "tape", {"positions": 3}, NOW)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW + 600, lab_runner=runner, early=True)
        assert out["deferred"] is False and len(calls) == 2
    finally:
        svc.store.close()


def test_the_command_line_passes_early_through():
    from snipetech import cli

    assert cli.build_parser().parse_args(["qualify", "run", "--early"]).early is True
    assert cli.build_parser().parse_args(["qualify", "run"]).early is False


def test_the_qualify_landing_is_three_buys_behind_the_leader_or_further_back_never_closer():
    """Research round 2 (7 Nov): the qualify lab enters behind the 3rd buy after the leader's, counted across slots
    ("+3x"), where live copies of early-curve leaders landed (2-4 buys behind). The owner's or the strategist's
    [qualify] landing_pos (in-slot, "+N" = N - 3 buys behind: the 6 Nov calibration) and the measure stage's measured
    landing (the 75th percentile of buys behind over 30+ live fills, rounded up) can only move it further back; an
    untimed or week-old measurement is ignored. 7aPo3n's live copies landed 0-1 behind: no closer landing until 10+
    live fills show it (the record), so neither the "+3" choice nor a close measurement moves it forward."""
    from snipetech.config import QualifyConfig
    from snipetech.qualify.run import qualify_landing

    st = Store(":memory:")
    try:
        q = QualifyConfig()
        assert qualify_landing(st, q, now=NOW) == "+3x"
        assert qualify_landing(st, QualifyConfig(landing_pos="+9"), now=NOW) == "+6x"
        assert qualify_landing(st, QualifyConfig(landing_pos="+3"), now=NOW) == "+3x"
        st.set_coverage("measure:landing_pos", {"pos": "+7", "behind_p75": 4.2, "ts": NOW - 3600})
        assert qualify_landing(st, q, now=NOW) == "+5x"
        st.set_coverage("measure:landing_pos", {"pos": "+4", "behind_p75": 1.0, "ts": NOW - 3600})
        assert qualify_landing(st, q, now=NOW) == "+3x"
        st.set_coverage("measure:landing_pos", {"pos": "+7", "behind_p75": 4.2, "ts": NOW - 8 * 86400})
        assert qualify_landing(st, q, now=NOW) == "+3x"
        st.set_coverage("measure:landing_pos", {"pos": "+7", "behind_p75": 4.2})
        assert qualify_landing(st, q, now=NOW) == "+3x"
    finally:
        st.close()


STRONG = {"trades": 300, "win_rate": 64.0, "realized_usd": 2500.0, "max_single_pnl_usd": 500.0, "tokens": 120,
          "trading_days": 40}  # fmt: skip


def _alone_in_its_slot(st, w, coins, t0, s0, tag=""):
    """W buys alone in its slot; three wallets buy 0.7 SOL in the next three slots, then the coin pauses until W sells
    it all. Copied right behind W (the in-slot +6), the copy sells into the pause at the top; copied behind the third
    buy, it is still holding when W dumps. Coins 100 s apart at 0.4 s a slot, like the rest of the tape."""
    D = addr("dev")
    for k in range(coins):
        m = Market(st, addr(f"{w}:{tag}{k}"), D, t0 + k * 100, s0 + k * 250)
        m.buy(D, 1.0, 0, sig=m.create_sig)
        m.buy(w, 0.5, 5)
        for j in range(3):
            m.buy(addr(f"{w}:{tag}{k}:f{j}"), 0.7, 6 + j)
        m.sell(w, 1.0, 40)
        m.commit()


def test_a_wallet_the_in_slot_landing_qualified_loses_where_its_copies_land(tmp_path):
    from snipetech.qualify.run import default_lab_runner

    svc, st, data = _svc(tmp_path)
    try:
        _alone_in_its_slot(st, "LONE", 12, NOW - 6000, SLOT)
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})
        st.upsert_candidate("LONE", "solanatracker", STRONG, NOW - 3600)
        st.set_label("LONE", "cand:LONE", "cand")
        svc.cfg.verdict.bootstrap_samples = 50
        (old,) = default_lab_runner(svc, svc.cfg)(["LONE"], "+6")
        assert old["n"] == 12 and old["mean"] > 0  # right behind the leader, it sells into the pause at the top
        run_qualify(svc, svc.cfg, data / "jobs", NOW)
        v = st.latest_verdicts()[0]
        assert v["landing_pos"] == "+3x" and v["n_tape"] == 12 and v["mean_sol"] < 0
        assert v["status"] == "retired" and any(r.startswith("loses at +3x") for r in v["reasons"]), v["reasons"]
    finally:
        st.close()


def test_a_wallet_that_buys_in_create_slots_is_retired_whatever_the_lab_says(tmp_path):
    """7zvP2x and 3yrJE1 (the 14:00 search, 7 Nov) were create-slot artifacts: 10 of 10 and 13 of 20 first buys in the
    coin's create slot. One of ten here (10 %): fewer than the two launches the bundle rule needs, so only this gate
    stops it; its verdict says so and stores the counts. The other wallet keeps its lab verdict."""
    svc, st, data = _svc(tmp_path)
    try:
        D = addr("dev")
        for w in ("EARLY", "LATER"):
            for k in range(10):
                m = Market(st, addr(f"{w}:cs{k}"), D, NOW - 6000 + k * 100, SLOT + k * 250)
                m.buy(D, 1.0, 0, sig=m.create_sig)
                m.buy(w, 0.5, 0 if (w == "EARLY" and k == 0) else 5)
                for j in range(4):
                    m.buy(addr(f"{w}:cs{k}:f{j}"), 0.3, 6 + j)
                m.commit()
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})
        win = {"n": 25, "mean": 0.02, "ev_lower": 0.005, "win_rate": 0.6, "total": 0.5, "exit": "x"}
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda ws, pos: [dict(win, wallet=w) for w in ws])
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        early = by["EARLY"]
        assert early["status"] == "retired" and early["create_slot"] == {"in_create": 1, "first_buys": 10, "share": 0.1}
        assert len(early["reasons"]) == 1 and early["reasons"][0].startswith("1 of 10 first buys (10%) in the coin's "
                                                                             "create slot")  # fmt: skip
        assert by["LATER"]["status"] != "retired" and by["LATER"]["create_slot"]["in_create"] == 0
        assert not any("create slot" in r for r in by["LATER"]["reasons"])
    finally:
        st.close()


def test_the_forward_window_starts_at_the_first_pick_even_one_made_before_these_rules(tmp_path):
    """The 27 wallets of research round 2 were picked by the 04:15 and 14:30 verdicts (7 Nov): their forward window
    starts at their first qualified verdict, whatever the rules were then, and is never moved by a later one. Both
    wallets win as much in the window they were picked on; one keeps paying after its pick, one loses."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["KEEPS", "STOPS"])
        st.set_coverage("tape", {"spans": [[NOW - 40 * 3600, NOW]]})
        record_copying(st, set(), NOW - 120)  # the book read OKTech two minutes ago: none of its snipers copies them
        picked = NOW - 30 * 3600
        for w in ("KEEPS", "STOPS"):
            st.upsert_candidate(w, "solanatracker", STRONG, picked - 3600)
            st.put_verdict({"wallet": w, "computed_at": picked, "landing_pos": "+6", "n_tape": 30, "mean_sol": 0.02,
                            "lower_sol": 0.006, "score": 0.006, "status": "qualified", "next_review": picked + 86400,
                            "copy_plan": {"min_leader_sol": 0.3, "exit_name": "lab", "lab_version": 2}})  # fmt: skip
        before = [[picked - 3600 + 60 * k, 0.03] for k in range(30)]
        after = {"KEEPS": [[picked + 3600 + 600 * k, 0.03 if k % 3 else -0.01] for k in range(24)],
                 "STOPS": [[picked + 3600 + 600 * k, -0.01 if k % 3 else 0.005] for k in range(24)]}  # fmt: skip

        def runner(wallets, pos):
            return [{"wallet": w, "n": 54, "mean": 0.02, "ev_lower": 0.008, "win_rate": 0.7, "total": 1.08, "exit": "x",
                     "min_leader_sol": 0.3, "moments": before + after[w]} for w in wallets]  # fmt: skip

        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        assert by["KEEPS"]["status"] == "qualified" and by["STOPS"]["status"] == "retired"
        assert by["KEEPS"]["forward"]["picked_at"] == picked == by["STOPS"]["forward"]["picked_at"]
        assert by["KEEPS"]["forward"]["n"] == 24 and by["KEEPS"]["forward"]["tape_hours"] == 30.0
        assert by["STOPS"]["forward"]["status"] == "failed" and by["STOPS"]["forward"]["mean"] < 0
        assert any(r.startswith("loses after it was picked") for r in by["STOPS"]["reasons"])
        assert out["qualified"] == 1 and out["forward_failed"] == 1
        assert "moments" not in st.get_score("qualify", "KEEPS")["lab"]  # the copies were for the check, not to keep
    finally:
        st.close()


def test_a_wallet_the_book_already_copies_is_not_stopped_by_these_rules_and_its_verdict_says_so(tmp_path):
    """E (research round 2, 7 Nov): the owner decides whether a wallet the book already copies goes (7aPo3n: keep it as a
    live measurement, judge it at 30 copies). Both wallets qualified under the old rules a day ago and have no copies
    since; the book copies ARMED. After a run under these rules IDLE waits for its forward copies, while ARMED keeps the
    verdict the book armed it on: the book's own plan, unchanged, keeps its sniper. ARMED's verdict says it no longer
    qualifies, and what these rules found."""
    from snipetech.book.policy import BookInputs, plan_book
    from snipetech.book.run import _bookable

    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["ARMED", "IDLE"])
        st.set_coverage("tape", {"spans": [[NOW - 30 * 3600, NOW]]})
        armed_on = NOW - 25 * 3600
        plan = {"min_leader_sol": 0.5, "exit_name": "lab", "lab_version": 2}
        for w in ("ARMED", "IDLE"):
            st.upsert_candidate(w, "solanatracker", STRONG, armed_on - 3600)
            st.put_verdict({"wallet": w, "computed_at": armed_on, "landing_pos": "+6", "n_tape": 35, "mean_sol": 0.0271,
                            "lower_sol": 0.0049, "score": 0.01, "status": "qualified", "next_review": NOW - 3600,
                            "copy_plan": plan})  # fmt: skip
        name = "snipetech copy book ARMED arm:control"
        st.add_oktech_sniper({"id": "s1", "external_id": "e-s1", "kind": "copy", "source": "ARMED", "name": name,
                              "config_hash": "h", "payload": {"mode": "ctbot", "ctbotWallets": [{"address": "ARMED"}]},
                              "created_at": NOW - 3 * 3600})  # fmt: skip
        # the book's reading of OKTech two minutes ago: its sniper on ARMED is running
        record_copying(st, buying_book_wallets([{"id": "s1", "name": name, "status": "running"}]), NOW - 120)
        before = [[armed_on - 7200 + 60 * k, 0.03] for k in range(35)]  # all before the pick: no forward copy yet
        runner = lambda ws, pos: [{"wallet": w, "n": 35, "mean": 0.03, "ev_lower": 0.03, "win_rate": 1.0, "total": 1.05,
                                   "exit": "x", "min_leader_sol": 0.5, "moments": list(before)} for w in ws]  # fmt: skip
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        idle, armed = by["IDLE"], by["ARMED"]
        assert idle["status"] == "candidate" and idle["forward"]["status"] == "pending" and idle["landing_pos"] == "+3x"
        assert armed["status"] == "qualified" and armed["score"] == 0.01 and armed["copy_plan"] == plan
        assert armed["landing_pos"] == "+6" and armed["kept_armed"]["kept"] is True
        assert armed["kept_armed"]["armed_on"] == armed_on and armed["kept_armed"]["new"]["status"] == "candidate"
        assert armed["forward"]["status"] == "pending" and armed["reasons"][0].startswith("no longer qualifies")
        assert out["qualified"] == 1 and out["kept_armed"] == 1 and out["forward_pending"] == 2
        qualified = _bookable(st)
        assert [v["wallet"] for v in qualified] == ["ARMED"]
        inp = BookInputs(equity_sol=10.0, day_realised_sol=0.0, day_risk_used_sol=0.0, qualified=qualified, live={},
                         bid_model={}, snipers=[{"id": "s1", "name": name, "status": "running"}], holdings=[], now=NOW,
                         holdings_known=True)  # fmt: skip
        actions = [(a.kind, a.sniper_id) for a in plan_book(inp, svc.cfg.book).actions]
        assert ("keep", "s1") in actions and ("stop", "s1") not in actions
    finally:
        st.close()


def _streak(st, w, coins, t0, s0, wins, tag):
    """W buys alone in its slot and three wallets buy 0.3 SOL in the next three slots; the copy lands behind them. A
    winning coin: six 1 SOL buys follow, then a pause, and the dead exit sells at the top. A losing coin: the three
    buyers and the creator sell into the copy, then a pause. W sells it all a while later. Coins 100 s apart at 0.4 s
    a slot (a slot is SLOT + 2.5 a second of the tape: `s0` must agree with `t0`)."""
    D = addr("dev")
    for k in range(coins):
        m = Market(st, addr(f"{w}:{tag}{k}"), D, t0 + k * 100, s0 + k * 250)
        m.buy(D, 1.0, 0, sig=m.create_sig)
        m.buy(w, 0.5, 5)
        ahead = [addr(f"{w}:{tag}{k}:a{j}") for j in range(3)]
        for j, a in enumerate(ahead):
            m.buy(a, 0.3, 6 + j)
        if wins:
            for j in range(6):
                m.buy(addr(f"{w}:{tag}{k}:r{j}"), 1.0, 9 + j)
        else:
            for j, a in enumerate(ahead):
                m.sell(a, 1.0, 9 + j)
            m.sell(D, 1.0, 12)
        m.sell(w, 1.0, 60)
        m.commit()


def test_a_hot_streak_wallet_that_wins_in_its_window_and_loses_after_it_is_not_qualified(tmp_path):
    """Research round 2 (7 Nov), with the real lab: HOT and STEADY win on every copy in the window they are picked on;
    with no tape after that both wait ("forward pending", nothing else of their verdicts changes). A day later HOT has
    lost on 20 copies since its pick, STEADY won on 20: HOT is retired although all its copies together still pay,
    STEADY qualifies."""
    svc, st, data = _svc(tmp_path)
    try:
        svc.cfg.verdict.bootstrap_samples = 200
        svc.cfg.lab.workers = 1
        t0 = NOW - 6000
        for i, w in enumerate(("HOT", "STEADY")):
            _streak(st, w, 25, t0 + 50 * i, SLOT + 125 * i, True, "pick")
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})
        run_qualify(svc, svc.cfg, data / "jobs", NOW)
        first = {v["wallet"]: v for v in st.latest_verdicts()}
        for w in ("HOT", "STEADY"):
            assert first[w]["status"] == "candidate" and first[w]["n_tape"] == 25 and first[w]["lower_sol"] > 0
            assert first[w]["forward"]["status"] == "pending" and first[w]["forward"]["n"] == 0
            assert first[w]["reasons"][-1].startswith("forward pending")
        later = NOW + 3600  # the tape recorded after the pick: a slot is still 2.5 a second of it
        for i, w in enumerate(("HOT", "STEADY")):
            ts = later + 50 * i
            _streak(st, w, 20, ts, SLOT + int((ts - t0) * 2.5), w == "STEADY", "after")
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW + 25 * 3600]]})
        run_qualify(svc, svc.cfg, data / "jobs", NOW + 25 * 3600)
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        hot, steady = by["HOT"], by["STEADY"]
        assert hot["n_tape"] == 45 and hot["mean_sol"] > 0 and hot["lower_sol"] > 0  # all its copies still pay
        assert hot["status"] == "retired" and hot["forward"]["status"] == "failed" and hot["forward"]["n"] == 20
        assert hot["forward"]["picked_at"] == NOW and hot["forward"]["mean"] < 0
        assert steady["status"] == "qualified" and steady["forward"]["status"] == "passed"
        assert steady["forward"]["n"] == 20 and steady["forward"]["lower"] > 0
    finally:
        st.close()


def test_a_copy_that_would_dodge_a_dump_by_waiting_for_three_buys_is_scored_where_live_copies_land(tmp_path):
    """Review of 7 Nov: holders sell in the three slots after the leader's buy, then three small buys come before the
    leader's slot + 10, then a small recovery. Copied right behind the leader (where a live copy lands: 6 of 7 in its own
    slot on 6 Nov, 7aPo3n's three 0-1 trades behind), every copy loses; behind the 3rd buy it entered after the dump,
    13.9 % under the leader's fill, and won every time. The lab now enters at the worse of the two: the wallet loses at
    +3x as it does right behind the leader, and is retired."""
    from snipetech.qualify.run import default_lab_runner

    svc, st, data = _svc(tmp_path)
    try:
        D, W = addr("dev"), "DUMPED"
        for k in range(12):
            m = Market(st, addr(f"{W}:{k}"), D, NOW - 6000 + k * 100, SLOT + k * 250)
            m.buy(D, 1.0, 0, sig=m.create_sig)
            holders = [addr(f"{W}:{k}:h{j}") for j in range(3)]
            for j, h in enumerate(holders):
                m.buy(h, 1.0, 1 + j)
            m.buy(W, 0.5, 5)  # alone in its slot
            for j, h in enumerate(holders):  # the dump, in the three slots after the leader's
                m.sell(h, 1.0, 6 + j)
            for j in range(3):  # three small buys before the leader's slot + 10
                m.buy(addr(f"{W}:{k}:b{j}"), 0.2, 10 + 2 * j)
            for j in range(3):  # a small recovery, then the coin goes quiet
                m.buy(addr(f"{W}:{k}:r{j}"), 1.0, 16 + j)
            m.sell(W, 1.0, 60)
            m.commit()
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})
        st.upsert_candidate(W, "solanatracker", STRONG, NOW - 3600)
        st.set_label(W, f"cand:{W}", "cand")
        svc.cfg.verdict.bootstrap_samples = 200
        run = default_lab_runner(svc, svc.cfg)
        (behind,) = run([W], "+1")
        (across,) = run([W], "+3x")
        assert behind["n"] == across["n"] == 12 and behind["mean"] < 0 and behind["win_rate"] == 0.0
        assert across["mean"] == pytest.approx(behind["mean"]) and across["win_rate"] == 0.0
        run_qualify(svc, svc.cfg, data / "jobs", NOW)
        v = st.latest_verdicts()[0]
        assert v["landing_pos"] == "+3x" and v["status"] == "retired" and v["mean_sol"] < 0, v["reasons"]
        assert any(r.startswith("loses at +3x") for r in v["reasons"])
    finally:
        st.close()


def _old_rules_pick(st, w, at, score=0.01, lower=0.0049):
    """The verdict the book armed a wallet on before the 7 Nov rules (7aPo3n, 16:05: n 35, +0.0271, lower +0.0049)."""
    st.upsert_candidate(w, "solanatracker", STRONG, at - 3600)
    st.put_verdict({"wallet": w, "computed_at": at, "landing_pos": "+6", "n_tape": 35, "mean_sol": 0.0271,
                    "lower_sol": lower, "score": score, "status": "qualified", "next_review": NOW - 3600,
                    "copy_plan": {"min_leader_sol": 0.5, "exit_name": "lab", "lab_version": 2}})  # fmt: skip


def _book_plan(svc, st, snipers):
    """The book's own plan, unchanged, on the verdicts the qualify stage wrote."""
    from snipetech.book.policy import BookInputs, plan_book
    from snipetech.book.run import _bookable

    inp = BookInputs(equity_sol=10.0, day_realised_sol=0.0, day_risk_used_sol=0.0, qualified=_bookable(st), live={},
                     bid_model={}, snipers=snipers, holdings=[], now=NOW, holdings_known=True)  # fmt: skip
    return [(a.kind, a.wallet, a.sniper_id) for a in plan_book(inp, svc.cfg.book).actions]


def test_a_wallet_whose_book_sniper_was_stopped_gets_the_verdict_of_these_rules(tmp_path):
    """Review of 7 Nov: the book's stop never deletes a sniper's local row (only a retire does), so counting rows kept
    a wallet nobody copied at the verdict it was armed on, and the book's own plan then made it a new sniper although
    its forward check was pending. Kept is only a wallet the book's latest reading of OKTech shows one of its own
    snipers running or starting for; a stopped task reads idle."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["STOPPED"])
        st.set_coverage("tape", {"spans": [[NOW - 30 * 3600, NOW]]})
        armed_on = NOW - 25 * 3600
        _old_rules_pick(st, "STOPPED", armed_on)
        name = "snipetech copy book STOPPE arm:control"
        st.add_oktech_sniper({"id": "s1", "external_id": "e-s1", "kind": "copy", "source": "STOPPED", "name": name,
                              "config_hash": "h", "payload": {"mode": "ctbot", "ctbotWallets": [{"address": "STOPPED"}]},
                              "created_at": NOW - 20 * 3600})  # fmt: skip  # stopped 10 h ago, its row still there
        record_copying(st, buying_book_wallets([{"id": "s1", "name": name, "status": "idle"}]), NOW - 120)
        before = [[armed_on - 7200 + 60 * k, 0.03] for k in range(35)]
        runner = lambda ws, pos: [{"wallet": w, "n": 35, "mean": 0.03, "ev_lower": 0.03, "win_rate": 1.0, "total": 1.05,
                                   "exit": "x", "min_leader_sol": 0.5, "moments": list(before)} for w in ws]  # fmt: skip
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        v = st.latest_verdicts()[0]
        assert v["status"] == "candidate" and v["forward"]["status"] == "pending" and v["landing_pos"] == "+3x"
        assert v["kept_armed"] is None and out["kept_armed"] == 0
        for status in ("idle", "stopped"):
            acts = _book_plan(svc, st, [{"id": "s1", "name": name, "status": status}])
            assert not any(kind in ("create", "start") for kind, _w, _s in acts), acts
    finally:
        st.close()


def test_a_sniper_the_owner_made_by_hand_never_keeps_a_wallet_at_its_old_verdict(tmp_path):
    """Review of 7 Nov: a copy sniper the owner made by hand (`oktech push`, a test) on a wallet with an old qualified
    verdict kept it qualified though the book never copied it and all 24 of its copies since the pick lost; the book
    then armed it. Only the book's own snipers count, so it gets the verdict of these rules: retired."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["HANDW"])
        st.set_coverage("tape", {"spans": [[NOW - 30 * 3600, NOW]]})
        picked = NOW - 25 * 3600
        _old_rules_pick(st, "HANDW", picked, lower=0.012)
        hand = {"id": "h1", "name": "snipetech vcopy HANDW test", "status": "running"}
        st.add_oktech_sniper({"id": "h1", "external_id": "e-h1", "kind": "vcopy", "source": "HANDW", "name": hand["name"],
                              "config_hash": "h", "payload": {"mode": "ctbot", "ctbotWallets": [{"address": "HANDW"}]},
                              "created_at": NOW - 24 * 3600})  # fmt: skip
        record_copying(st, buying_book_wallets([hand]), NOW - 120)
        before = [[picked - 7200 + 60 * k, 0.03] for k in range(35)]
        after = [[picked + 3600 + 900 * k, -0.01] for k in range(24)]
        runner = lambda ws, pos: [{"wallet": w, "n": 59, "mean": 0.014, "ev_lower": 0.008, "win_rate": 0.6, "total": 0.8,
                                   "exit": "x", "min_leader_sol": 0.5, "moments": before + after} for w in ws]  # fmt: skip
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        v = st.latest_verdicts()[0]
        assert v["status"] == "retired" and v["forward"]["status"] == "failed" and v["kept_armed"] is None
        acts = _book_plan(svc, st, [dict(hand, status="stopped")])
        assert not any(kind == "create" for kind, _w, _s in acts), acts
    finally:
        st.close()


def test_while_the_books_reading_of_oktech_is_unknown_a_wallet_it_may_copy_waits_for_the_next_run(tmp_path):
    """With no reading of OKTech by the book in the last hour (it has not run since an update, or OKTech did not answer),
    whether the book copies a wallet it armed before the 7 Nov rules is unknown: keeping a wallet nobody copies would
    let the book arm it, dropping one it copies would stop it. Such a wallet's verdict is left as it is and stays due;
    every other wallet is judged as usual, and the run says how many waited."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["OLDQ", "FRESH"])
        st.set_coverage("tape", {"spans": [[NOW - 30 * 3600, NOW]]})
        _old_rules_pick(st, "OLDQ", NOW - 25 * 3600)
        st.upsert_candidate("FRESH", "solanatracker", STRONG, NOW - 3600)
        runner = lambda ws, pos: [{"wallet": w, "n": 35, "mean": 0.03, "ev_lower": 0.03, "win_rate": 1.0, "total": 1.05,
                                   "exit": "x", "min_leader_sol": 0.5, "moments": []} for w in ws]  # fmt: skip
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)  # the book never read OKTech
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        assert out["armed_unknown"] == 1 and out["evaluated"] == 1
        assert by["OLDQ"]["computed_at"] == NOW - 25 * 3600 and by["OLDQ"]["landing_pos"] == "+6"  # untouched
        assert by["FRESH"]["status"] == "candidate" and by["FRESH"]["forward"]["status"] == "pending"
        record_copying(st, set(), NOW - 2 * 3600)  # a reading two hours old says nothing about now
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW + 60, lab_runner=runner, early=True)
        assert out["armed_unknown"] == 1 and st.latest_verdicts("qualified")[0]["computed_at"] == NOW - 25 * 3600
        record_copying(st, set(), NOW + 60)  # the book read OKTech: none of its snipers copies OLDQ
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW + 120, lab_runner=runner, early=True)
        oldq = {v["wallet"]: v for v in st.latest_verdicts()}["OLDQ"]
        assert out["armed_unknown"] == 0 and oldq["status"] == "candidate" and oldq["kept_armed"] is None
    finally:
        st.close()
