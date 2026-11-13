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


def _tape(st, wallets, coins=12):
    """Each wallet buys early in a dozen coins, so it has the buys a wallet needs to be measured at all (10 at the
    least since 11 Nov: the count before the lab); the tape span covers them."""
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
        assert out.pop("secs").keys() == {"lab", "gates"}  # where the run's time went (11 Nov)
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
            "too_few": 0,
            "unlabelled": 0,
            "lab_wallets": 3,
            "chunks": 1,
            "left_due": 0,
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
        # the stage labels nobody (11 Nov: it labelled every wallet it measured `cand`; the lab is told its wallets)
        assert st._q("SELECT address, kind, label FROM labels ORDER BY address") == [("GOOD", "kol", "kol one")]
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


def test_the_forward_window_starts_at_the_pick_even_one_made_before_these_rules(tmp_path):
    """The 27 wallets of research round 2 were picked by the 04:15 and 14:30 verdicts (7 Nov): their forward window
    starts at their qualified verdict, whatever the rules were then (a later one that passes the in-sample gates too
    does not move it; only one that fails them ends that pick). Both wallets win as much in the window they were picked
    on; one keeps paying after its pick, one loses."""
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
    leader's slot + 10, then a small recovery. Copied right behind the leader (where a live copy lands: 6 of 7 in its
    own slot on 6 Nov, 7aPo3n's three 0-1 trades behind), every copy loses; behind the 3rd buy it entered after the
    dump, 13.9 % under the leader's fill, and won every time. The lab now enters at the worse of the two: the wallet
    loses at +3x as it does right behind the leader, and is retired."""
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


def _row(w, n, mean, lower, moments, win_rate=0.6):
    """The lab's row for `w` with its copies, as the qualify lab hands it over."""
    return {"wallet": w, "n": n, "mean": mean, "ev_lower": lower, "win_rate": win_rate, "total": n * mean, "exit": "x",
            "min_leader_sol": 0.5, "moments": list(moments)}  # fmt: skip


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
        payload = {"mode": "ctbot", "ctbotWallets": [{"address": "STOPPED"}]}
        st.add_oktech_sniper({"id": "s1", "external_id": "e-s1", "kind": "copy", "source": "STOPPED", "name": name,
                              "config_hash": "h", "payload": payload, "created_at": NOW - 20 * 3600})  # fmt: skip
        # the book stopped it 10 h ago: its row is still there, and OKTech reads it idle
        record_copying(st, buying_book_wallets([{"id": "s1", "name": name, "status": "idle"}]), NOW - 120)
        before = [[armed_on - 7200 + 60 * k, 0.03] for k in range(35)]
        runner = lambda ws, pos: [_row(w, 35, 0.03, 0.03, before, win_rate=1.0) for w in ws]
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
        payload = {"mode": "ctbot", "ctbotWallets": [{"address": "HANDW"}]}
        st.add_oktech_sniper({"id": "h1", "external_id": "e-h1", "kind": "vcopy", "source": "HANDW",
                              "name": hand["name"], "config_hash": "h", "payload": payload,
                              "created_at": NOW - 24 * 3600})  # fmt: skip
        record_copying(st, buying_book_wallets([hand]), NOW - 120)
        before = [[picked - 7200 + 60 * k, 0.03] for k in range(35)]
        after = [[picked + 3600 + 900 * k, -0.01] for k in range(24)]
        runner = lambda ws, pos: [_row(w, 59, 0.014, 0.008, before + after) for w in ws]
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        v = st.latest_verdicts()[0]
        assert v["status"] == "retired" and v["forward"]["status"] == "failed" and v["kept_armed"] is None
        acts = _book_plan(svc, st, [dict(hand, status="stopped")])
        assert not any(kind == "create" for kind, _w, _s in acts), acts
    finally:
        st.close()


def test_while_the_books_reading_of_oktech_is_unknown_a_wallet_it_may_copy_waits_for_the_next_run(tmp_path):
    """With no reading of OKTech by the book in the last hour (it has not run since an update, or OKTech did not
    answer), whether the book copies a wallet it armed before the 7 Nov rules is unknown: keeping a wallet nobody copies
    would let the book arm it, dropping one it copies would stop it. Such a wallet's verdict is left as it is and stays
    due; every other wallet is judged as usual, and the run says how many waited."""
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


def test_a_copied_wallet_these_rules_score_under_the_books_bar_keeps_its_sniper(tmp_path):
    """Review of 7 Nov: ARMED, copied by a running book sniper on an old-rules verdict scoring 0.01, meets these rules
    with a lower bound of 0.003 at +3x and 24 small wins since its pick: its own score, 0.0018, is under the book's
    0.005 bar, and the book's unchanged ranking stopped its sniper ("dropped out of the ranking"). It keeps the score it
    was armed on while the book copies it, and the book keeps its sniper."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["ARMED"])
        st.set_coverage("tape", {"spans": [[NOW - 30 * 3600, NOW]]})
        armed_on = NOW - 25 * 3600
        _old_rules_pick(st, "ARMED", armed_on, lower=0.012)
        sniper = {"id": "s1", "name": "snipetech copy book ARMED arm:control", "status": "running"}
        record_copying(st, buying_book_wallets([sniper]), NOW - 120)
        before = [[armed_on - 7200 + 60 * k, 0.004] for k in range(30)]
        after = [[armed_on + 3600 + 900 * k, 0.004 if k % 4 else 0.002] for k in range(24)]
        runner = lambda ws, pos: [_row(w, 54, 0.0037, 0.003, before + after, win_rate=1.0) for w in ws]
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        v = st.latest_verdicts()[0]
        assert v["status"] == "qualified" and v["forward"]["status"] == "passed" and v["landing_pos"] == "+3x"
        assert v["score"] == 0.01 and v["kept_armed"]["new_score"] == pytest.approx(0.0018)
        assert svc.cfg.book.min_score == 0.005
        acts = _book_plan(svc, st, [sniper])
        assert ("keep", "ARMED", "s1") in acts and not any(kind == "stop" for kind, _w, _s in acts), acts
    finally:
        st.close()


def test_a_wallet_picked_once_before_waits_for_copies_after_its_new_pick_like_one_never_picked(tmp_path):
    """Review of 7 Nov: the forward window started at a wallet's first pick and never moved. OLDPICK was picked 4 days
    ago (the old rules) and retired a day later; the tape keeps 3 days. On the same fresh streak as NEWBIE, never
    picked, its 25 streak copies counted as forward (forward n 25 = in-sample n 25) and it qualified while NEWBIE
    waited. Passing the in-sample gates again after a verdict that failed them is a new pick: both now wait for copies
    after it. LOSTPICK lost 24 copies after its first pick 30 h ago; its streak does not undo that: retired."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["OLDPICK", "NEWBIE", "LOSTPICK"])
        st.set_coverage("tape", {"spans": [[NOW - 3 * 86400, NOW]]})  # three days of tape kept
        for w, picked in (("OLDPICK", NOW - 4 * 86400), ("LOSTPICK", NOW - 30 * 3600)):
            st.upsert_candidate(w, "solanatracker", STRONG, picked - 3600)
            for at, status, mean in ((picked, "qualified", 0.02), (picked + 6 * 3600, "retired", -0.01)):
                st.put_verdict({"wallet": w, "computed_at": at, "landing_pos": "+6", "n_tape": 30, "mean_sol": mean,
                                "lower_sol": mean - 0.01, "score": max(0.0, mean - 0.01), "status": status,
                                "next_review": NOW - 60, "copy_plan": {"min_leader_sol": 0.3, "exit_name": "lab",
                                                                       "lab_version": 2}})  # fmt: skip
        st.upsert_candidate("NEWBIE", "solanatracker", STRONG, NOW - 3600)
        streak = [[NOW - 10 * 3600 + 1400 * k, 0.03 if k % 5 else -0.01] for k in range(25)]  # the fresh hot streak
        lost = [[NOW - 29 * 3600 + 600 * k, -0.03] for k in range(24)]

        def runner(wallets, pos):
            return [_row(w, 25, 0.0224, 0.017, (lost if w == "LOSTPICK" else []) + streak, 0.8) for w in wallets]

        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        for w in ("OLDPICK", "NEWBIE"):
            fwd = by[w]["forward"]
            assert by[w]["status"] == "candidate" and fwd["status"] == "pending", (w, by[w]["reasons"])
            assert fwd["picked_at"] == NOW and fwd["n"] == 0
        old = by["OLDPICK"]["forward"]
        assert old["first_picked_at"] == NOW - 4 * 86400 and old["first_n"] == 25  # its streak, kept apart
        assert by["NEWBIE"]["forward"]["first_picked_at"] == NOW
        lostpick = by["LOSTPICK"]
        assert lostpick["status"] == "retired" and lostpick["forward"]["status"] == "failed"
        assert lostpick["forward"]["first_n"] == 49 and lostpick["forward"]["first_mean"] < 0
        assert any(r.startswith("loses since it was first picked") for r in lostpick["reasons"])
    finally:
        st.close()


def test_losses_after_a_pick_still_count_once_the_tape_no_longer_holds_them(tmp_path):
    """Review of 7 Nov: the tape keeps 3-30 days (deploy/bootstrap.sh). A wallet picked at NOW lost on 20 copies after
    its pick and was retired; at its review a week later the tape no longer holds them, and 25 fresh wins alone would
    pass. Each run records the wallet's copies since its first pick with its verdict's working data, and the next run
    takes the ones older than its tape from that record: the losses still count, and it stays retired."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["PRUNED"])
        st.upsert_candidate("PRUNED", "solanatracker", STRONG, NOW - 3600)
        record_copying(st, set(), NOW - 60)
        streak = [[NOW - 5000 + 100 * k, 0.03] for k in range(30)]  # the streak it is picked on
        losses = [[NOW + 3600 + 900 * k, -0.03] for k in range(20)]  # its copies after the pick
        week = NOW + 8 * 86400  # a retired wallet is reviewed a week (review_retired_h) after its verdict
        wins = [[week + 3600 + 600 * k, 0.02] for k in range(25)]  # after the tape dropped the losses
        span = {"now": [NOW - 7200, NOW]}

        def runner(wallets, pos):  # the lab sees only the copies on the tape it reads
            a, b = span["now"]
            moments = [m for m in streak + losses + wins if a <= m[0] <= b]
            return [{"wallet": w, "n": len(moments), "mean": 0.02, "ev_lower": 0.01, "win_rate": 0.7, "total": 0.5,
                     "exit": "x", "min_leader_sol": 0.3, "moments": moments} for w in wallets]  # fmt: skip

        def run(at, a, b):
            span["now"] = [a, b]
            st.set_coverage("tape", {"spans": [[a, b]]})
            record_copying(st, set(), at - 60)
            run_qualify(svc, svc.cfg, data / "jobs", at, lab_runner=runner, early=True)
            return st.latest_verdicts()[0]

        first = run(NOW, NOW - 7200, NOW)
        assert first["status"] == "candidate" and first["forward"]["picked_at"] == NOW
        second = run(NOW + 25 * 3600, NOW - 7200, NOW + 25 * 3600)
        assert second["status"] == "retired" and second["forward"]["status"] == "failed"
        assert second["forward"]["n"] == 20
        later = week + 8 * 3600
        third = run(later, week, later)  # the tape now starts a week after the pick
        fwd = third["forward"]
        assert fwd["picked_at"] == NOW and fwd["n"] == 45 and fwd["off_tape"] == 20, third["reasons"]
        assert third["status"] == "retired" and fwd["status"] == "failed" and fwd["mean"] < 0
        assert len(st.get_score("qualify", "PRUNED")["record"]["copies"]) == 45
    finally:
        st.close()


def test_pick_times_are_the_first_pick_and_the_start_of_the_latest_run_of_picks():
    """A pick is a verdict every in-sample gate passed: qualified before the 7 Nov rules, or one that carries a forward
    check. A verdict that failed one ends the run, a kept verdict included when it carries no forward check (these rules
    stopped it in-sample), so the latest pick starts over at the next pass."""
    from snipetech.qualify.run import pick_times

    st = Store(":memory:")
    try:
        assert pick_times(st, "W") == (None, None)

        def put(at, status, forward=None, kept=None):
            st.put_verdict({"wallet": "W", "computed_at": at, "status": status, "next_review": at + 86400,
                            "forward": forward, "kept_armed": kept})  # fmt: skip

        put(100, "qualified")  # before these rules
        assert pick_times(st, "W") == (100, 100)
        put(200, "candidate", forward={"status": "pending"})
        assert pick_times(st, "W") == (100, 100)
        put(300, "retired", forward={"status": "failed"})  # it passed the in-sample gates and lost after its pick
        assert pick_times(st, "W") == (100, 100)
        put(400, "qualified", kept={"armed_on": 100, "kept": True})  # kept, failing these rules in-sample
        assert pick_times(st, "W") == (100, None)
        put(500, "candidate", forward={"status": "pending"})
        assert pick_times(st, "W") == (100, 500)
        put(600, "candidate")  # too few copies: no forward check
        assert pick_times(st, "W") == (100, None)
    finally:
        st.close()


def test_a_run_says_where_its_time_went(tmp_path):
    """11 Nov: a run of 300 wallets took 2 h 18 min at about one processor, back to back all day, and its output did
    not say whether the lab or the gates read wallet by wallet took the time, nor how many processes the lab ran on.
    Each run now says both, so the slow part is known from the next run on."""
    from snipetech.qualify.run import default_lab_runner

    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["W1"], coins=12)
        st.upsert_candidate("W1", "tape", {"positions": 12}, NOW - 3600)
        svc.cfg.verdict.bootstrap_samples = 50
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW)
        assert out["evaluated"] == 1 and set(out["secs"]) == {"lab", "gates"}
        assert all(v >= 0 for v in out["secs"].values())
        assert out["lab"]["workers"] >= 1 and out["lab"]["moments"] == 12
        assert set(out["lab"]["secs"]) == {"companions", "collect", "rank"}
        assert out["lab"]["mates"] == 0  # how many of the measured wallets were scored behind companions
        run = default_lab_runner(svc, svc.cfg)
        run(["W1"], "+6")
        assert run.info["moments"] == 12
    finally:
        st.close()


# --- 11 Nov: what a run spent its two hours on -----------------------------------------------------------------------
# Of 9,808 verdicts in two days 7,425 had no copy on the tape and 8,835 fewer than 20 (the least that can pass), each
# after a lab and after the crowding read (one read per coin the wallet ever bought) and three more per-wallet reads.


def _lab_row(n=30, mean=0.02, lower=0.005):
    return {"n": n, "mean": mean, "ev_lower": lower, "win_rate": 0.6, "total": n * mean, "exit": "x"}


def test_a_wallet_with_too_few_buys_to_pass_gets_its_verdict_without_the_lab(tmp_path):
    """A wallet needs 10 scored copies at the least (20 without a strong history), and a copy is a coin it bought with
    0.1 SOL or more: one that bought fewer coins than that cannot pass, and a bounded count says so in milliseconds."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["FEW"], coins=3)
        _tape(st, ["MANY"], coins=12)
        st.upsert_candidate("FEW", "coinfirst", {}, NOW - 3600)
        st.upsert_candidate("MANY", "coinfirst", {}, NOW - 3500)
        sent = []
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW,
                          lab_runner=lambda ws, pos: sent.extend(ws) or [dict(_lab_row(), wallet=w) for w in ws])  # fmt: skip
        assert sent == ["MANY"] and out["too_few"] == 1 and out["lab_wallets"] == 1 and out["evaluated"] == 2
        few = next(v for v in st.latest_verdicts() if v["wallet"] == "FEW")
        assert few["status"] == "candidate" and few["next_review"] == NOW + 72 * 3600
        why = "3 coins bought with 0.1 SOL or more on the tape: fewer than the 10 copies a wallet needs at the least"
        assert few["reasons"] == [why + ", not measured"]
    finally:
        st.close()


def test_a_run_screens_ten_times_the_wallets_it_can_measure(tmp_path):
    """The lab measures [qualify] max_wallets; the count before it is cheap, so a run takes ten times as many due
    wallets, gives the ones that cannot pass their verdict and measures the first max_wallets of the rest (the others
    stay due). The backlog of 41 thousand is then read in a day or two, not in two weeks."""
    svc, st, data = _svc(tmp_path)
    try:
        few = [f"FEW{i:02d}" for i in range(15)]
        many = [f"MANY{i}" for i in range(4)]
        _tape(st, few, coins=1)
        _tape(st, many, coins=11)
        for i, w in enumerate(few + many):
            st.upsert_candidate(w, "coinfirst", {}, NOW - 9000 + i)
        svc.cfg.qualify.max_wallets = 2
        sent = []
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW,
                          lab_runner=lambda ws, pos: sent.extend(ws) or [dict(_lab_row(), wallet=w) for w in ws])  # fmt: skip
        assert sent == ["MANY0", "MANY1"] and out["too_few"] == 15 and out["evaluated"] == 17
        left = [c["wallet"] for c in st.candidates(due_before=NOW + 60)]
        assert left == ["MANY2", "MANY3"]  # measurable, not measured this run: still due
    finally:
        st.close()


def test_the_readings_only_a_passing_wallet_needs_are_made_only_for_one(tmp_path, monkeypatch):
    """The crowding read loads a minute of trades for every coin a wallet first bought (thousands for a bot), and it
    ran for all 300 wallets of a run whatever the lab had said of them, which is where the run's two hours went. It
    only decides for a wallet whose lower bound is above zero; the vetoes and the venue only for one with enough
    copies to pass. A wallet the lab gave too few copies, or a losing one, is told so without them."""
    from snipetech.qualify import run as R

    svc, st, data = _svc(tmp_path)
    try:
        wallets = ["PASSES", "LOSES", "THIN"]
        _tape(st, wallets, coins=12)
        for i, w in enumerate(wallets):
            st.upsert_candidate(w, "coinfirst", {}, NOW - 3600 + i)
        rows = {
            "PASSES": _lab_row(30, 0.02, 0.005),
            "LOSES": _lab_row(30, -0.01, -0.02),
            "THIN": _lab_row(4, 0.03, 0.001),
        }
        read: dict[str, list[str]] = {
            "crowding": [],
            "venue_share": [],
            "relationship_gate": [],
            "create_slot_share": [],
        }
        for name in read:
            real = getattr(R, name)
            monkeypatch.setattr(
                R, name, lambda store, w, *a, _n=name, _real=real, **k: read[_n].append(w) or _real(store, w, *a, **k)
            )
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda ws, pos: [dict(rows[w], wallet=w) for w in ws])
        assert read["crowding"] == ["PASSES"]
        assert read["venue_share"] == read["relationship_gate"] == read["create_slot_share"] == ["PASSES", "LOSES"]
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        assert by["PASSES"]["forward"]["status"] == "pending" and by["LOSES"]["status"] == "retired"
        assert by["THIN"]["status"] == "candidate" and by["THIN"]["reasons"][-1].startswith("n 4 < 20")
    finally:
        st.close()


def test_a_wallet_the_book_copies_is_measured_however_few_coins_the_tape_holds(tmp_path):
    """The count before the lab never decides for a wallet that was picked before or is qualified: its review, its
    forward check and whether its sniper is kept are the lab's to say."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["ARMED"], coins=3)
        st.upsert_candidate("ARMED", "tape", {}, NOW - 9000)
        st.put_verdict({"wallet": "ARMED", "computed_at": NOW - 86400, "status": "qualified", "next_review": NOW - 10,
                        "score": 0.01, "n_tape": 30, "copy_plan": {"exit_name": "lab"}})  # fmt: skip
        sent = []
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW,
                          lab_runner=lambda ws, pos: sent.extend(ws) or [dict(_lab_row(), wallet=w) for w in ws])  # fmt: skip
        assert sent == ["ARMED"] and out["too_few"] == 0
    finally:
        st.close()


def test_a_run_measures_its_wallets_in_chunks_and_writes_each_chunks_verdicts_first(tmp_path, monkeypatch):
    """The unit kills a run after three hours and every verdict was written after the one lab of all 300 wallets: a
    run that ran long lost them all, and a wallet at the front of the queue waited two hours for a verdict the lab had
    after minutes. The wallets are measured CHUNK at a time, in the queue's order, each chunk's verdicts written
    before the next chunk's lab."""
    from snipetech.qualify import run as R

    monkeypatch.setattr(R, "CHUNK", 2)
    svc, st, data = _svc(tmp_path)
    try:
        wallets = [f"W{i}" for i in range(5)]
        _tape(st, wallets)
        for i, w in enumerate(wallets):
            st.upsert_candidate(w, "coinfirst", {}, NOW - 9000 + i)
        calls, judged_before = [], []

        def runner(ws, pos):
            calls.append(list(ws))
            judged_before.append(sorted(v["wallet"] for v in st.latest_verdicts()))
            return [dict(_lab_row(), wallet=w) for w in ws]

        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        assert calls == [["W0", "W1"], ["W2", "W3"], ["W4"]]
        assert judged_before == [[], ["W0", "W1"], ["W0", "W1", "W2", "W3"]]
        assert out["evaluated"] == 5 and out["lab_wallets"] == 5 and out["chunks"] == 3 and out["left_due"] == 0
    finally:
        st.close()


def test_a_run_out_of_time_starts_no_new_chunk_and_leaves_the_rest_due(tmp_path, monkeypatch):
    from snipetech.qualify import run as R

    monkeypatch.setattr(R, "CHUNK", 2)
    svc, st, data = _svc(tmp_path)
    try:
        wallets = [f"W{i}" for i in range(5)]
        _tape(st, wallets)
        for i, w in enumerate(wallets):
            st.upsert_candidate(w, "coinfirst", {}, NOW - 9000 + i)
        calls = []
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, budget_secs=0.0,
                          lab_runner=lambda ws, pos: calls.append(list(ws)) or [dict(_lab_row(), wallet=w) for w in ws])  # fmt: skip
        assert calls == [["W0", "W1"]]  # the first chunk always runs
        assert out["evaluated"] == 2 and out["left_due"] == 3 and out["chunks"] == 1
        assert [c["wallet"] for c in st.candidates(due_before=NOW + 60)] == ["W2", "W3", "W4"]
    finally:
        st.close()


def _set_tape(st, wallet, mates, coins=12):
    """`wallet` buys a dozen coins, each time with its companions in the next transactions of its block, then a small
    crowd in the blocks after."""
    D = addr("dev")
    for k in range(coins):
        m = Market(st, addr(f"{wallet}:{k}"), D, NOW - 7000 + k * (3000 // coins), SLOT + k * (7500 // coins))
        m.buy(D, 1.0, 0)
        m.buy(wallet, 0.5, 5)
        for mate in mates:
            m.buy(mate, 0.5, 5)  # the same block, the next places
        for j in range(4):
            m.buy(addr(f"s{k}{j}"), 0.3 + 0.1 * j, 6 + j)
        m.sell(wallet, 1.0, 30)
        m.sell(addr(f"s{k}0"), 1.0, 40)
        m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})


def test_the_qualify_lab_scores_a_wallet_behind_its_own_companions(tmp_path, monkeypatch):
    """11 Nov: the wallets with the best paper numbers were the first of one operator's wallets, and "+3x" counted the
    operator's own next buys as the buyers ahead of us. The qualify lab finds each measured wallet's companions and
    scores its copies behind them; the verdict says so."""
    from snipetech.qualify.run import default_lab_runner

    svc, st, data = _svc(tmp_path)
    try:
        _set_tape(st, "W1", ["M1", "M2"])
        _tape(st, ["W2"], coins=12)  # buys alone
        cfg = svc.cfg
        cfg.verdict.bootstrap_samples = 50
        seen = {}
        real = L.collect

        def spy(*args, **kw):
            seen["kw"] = kw
            return real(*args, **kw)

        monkeypatch.setattr(L, "collect", spy)
        runner = default_lab_runner(svc, cfg)
        rows = {r["wallet"]: r for r in runner(["W1", "W2"], "+3x")}
        assert seen["kw"]["mates"] == {"W1": frozenset({"M1", "M2"})}  # W2 has none: not in the table
        assert rows["W1"]["mates"] == 2 and rows["W2"]["mates"] == 0
        assert runner.info["mates"] == 1 and "companions" in runner.info["secs"]
        # the same copies without the companions named: entered two buys earlier, so they made something else
        monkeypatch.setattr("snipetech.qualify.run.companions", lambda *a, **k: frozenset())
        before = {r["wallet"]: r for r in default_lab_runner(svc, cfg)(["W1", "W2"], "+3x")}
        assert before["W1"]["mean"] != rows["W1"]["mean"] and before["W2"]["mean"] == rows["W2"]["mean"]
        monkeypatch.undo()
        st.upsert_candidate("W1", "tape", {"positions": 12}, NOW - 3600)
        run_qualify(svc, cfg, data / "jobs", NOW)
        (v,) = [x for x in st.latest_verdicts() if x["wallet"] == "W1"]
        assert any("2 wallets of its own" in r for r in v["reasons"])
    finally:
        st.close()


def test_a_wallet_can_be_called_up_for_review_now(tmp_path, capsys):
    """12 Nov: the rule that finds the set a wallet buys with changed (the analyst's I72) and the two wallets it
    matters for were qualified, their next review a day away, while the book copies a qualified wallet. `qualify due
    <wallet>...` puts the review date of a wallet's latest verdict at now: the next qualify lab takes it, the copied
    and the qualified first as the queue orders them. An older verdict of the wallet is left as it was."""
    import time

    from snipetech import cli

    _svc_only, st, data = _svc(tmp_path)
    del _svc_only
    try:
        for w in ("A", "B"):
            st.upsert_candidate(w, "solanatracker", {"trades": 300}, NOW - 9000)
            for age, status in ((7200, "candidate"), (3600, "qualified")):
                st.put_verdict({"wallet": w, "computed_at": NOW - age, "landing_pos": "+3x", "status": status,
                                "next_review": NOW + 86400, "reasons": []})  # fmt: skip
        assert st.candidates(due_before=NOW) == []
        assert st.review_now(["A", "nobody"], NOW) == 1
        assert [c["wallet"] for c in st.candidates(due_before=NOW)] == ["A"]
        old = st._q("SELECT next_review FROM verdicts WHERE wallet='A' ORDER BY computed_at")
        assert [r[0] for r in old] == [NOW + 86400, NOW]  # the latest verdict only
        assert st.review_now([], NOW) == 0
    finally:
        st.close()
    assert cli.main(["--db", str(data / "snipetech.db"), "qualify", "due", "B", "nobody"]) == 0
    assert json.loads(capsys.readouterr().out) == {"due": 1, "asked": 2}
    st = Store(data / "snipetech.db")
    try:
        assert {c["wallet"] for c in st.candidates(due_before=int(time.time()) + 5)} == {"A", "B"}
    finally:
        st.close()
    a = build_parser().parse_args(["qualify", "run", "--max", "50", "--json"])
    assert a.action == "run" and a.wallets == []


def test_qualify_runs_beside_one_lab_on_a_two_slot_machine_and_is_owed_a_slot_when_both_are_taken(tmp_path, monkeypatch):
    """13 Nov evening: the research agents may hold both lab slots while nothing else asks for one. Qualify used to
    give up whenever any lab job ran at all (a rule from the one-slot machine), without saying it wanted a slot: with
    the agents on both slots most of the time it would never have run. Beside one lab it runs (the second slot is
    free); with every slot taken by lab jobs it gives up and leaves the mark the agents give way to."""
    from snipetech.analysis import lab_lock

    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "2")
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["W1"])
        st.upsert_candidate("W1", "tape", {"positions": 3}, NOW - 3600)
        _running_lab(data)
        second = data / "jobs" / "20261106-100001-fedcba.json"
        second.write_text(json.dumps({"id": "20261106-100001-fedcba", "argv": ["lab", "--latency", "1"],
                                      "started": NOW - 50, "ended": None, "exit_code": None, "running": True,
                                      "pid": os.getpid()}))  # fmt: skip
        calls = []
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda wallets, pos: calls.append(wallets) or [])
        assert out["deferred"] is True and "2 lab jobs are running" in out["reason"] and calls == []
        assert (lab_lock.wanted(data, now=NOW) or {}).get("who") == "qualify"  # the next free slot is its
        second.unlink()  # one of the two ended
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda wallets, pos: calls.append(wallets) or [])
        assert out["deferred"] is False and calls == [["W1"]]  # it ran beside the other lab
        assert lab_lock.wanted(data) is None  # and nobody is owed a slot on its account any more
    finally:
        st.close()
