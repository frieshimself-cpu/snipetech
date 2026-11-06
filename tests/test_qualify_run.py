"""The qualify runner: due candidates → labels → one lab (never beside another, never more often than the interval)
→ verdicts; wallets without tape data get a 72 h review without a lab."""

import json
import os

import pytest

from snipetech.analysis import lab as L
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
        assert seen["wallets"] == ["BAD", "GOOD", "WEAK"] and seen["pos"] == "+6"  # banned never measured
        assert out == {
            "evaluated": 3,
            "qualified": 1,
            "candidate": 1,
            "retired": 1,
            "deferred": False,
            "no_tape": 0,
            "labelled": 2,
            "lab_wallets": 3,
        }
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        assert (
            by["GOOD"]["status"] == "qualified"
            and by["WEAK"]["status"] == "candidate"
            and by["BAD"]["status"] == "retired"
        )
        assert (
            by["GOOD"]["next_review"] == NOW + 24 * 3600
            and by["GOOD"]["hist_win_rate"] == 64.0
            and by["GOOD"]["venue_share"] == 1.0
        )
        labels = st._q("SELECT address, kind, label FROM labels ORDER BY address")
        assert (
            ("GOOD", "kol", "kol one") in labels
            and ("WEAK", "cand", "cand:WEAK") in labels
            and ("BAD", "cand", "cand:BAD") in labels
        )
        assert st.verdict_counts() == {"banned": 1, "candidate": 1, "qualified": 1, "retired": 1}
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
        assert seen["kw"]["exits"] == [L.live_exits(cfg)["lab"]]  # what book/run.py sends: exit_name="lab"
        assert seen["kw"]["per_tx_sol"] == 0.0035  # no measured round trip yet: the flat cost
        assert row["wallet"] == "W1" and row["exit_name"] == "lab" and row["lab_version"] == L.LAB_VERSION
        assert row["exit_params"] == L.LIVE_EXITS["lab"].to_dict() and row["min_leader_sol"] == cfg.book.min_copy_sol
        # the verdict carries the plan to the book
        st.upsert_candidate("W1", "tape", {"positions": 12}, NOW - 3600)
        run_qualify(svc, cfg, data / "jobs", NOW)
        plan = st.latest_verdicts()[0]["copy_plan"]
        assert plan["exit_name"] == "lab" and plan["min_leader_sol"] == 0.3 and plan["lab_version"] == L.LAB_VERSION
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
        assert any(r.startswith("loses at +6: mean -0.0500 SOL over 25") for r in v["reasons"])
    finally:
        st.close()


def test_the_lab_charges_the_measured_round_trip_once_the_low_bid_has_30_fills():
    from snipetech.qualify.run import lab_tx_cost

    st, cfg = Store(":memory:"), Config()
    try:
        assert lab_tx_cost(st, cfg) == 0.0035
        arm = {"arm": "control", "n": 20, "failed_n": 33, "mean_fee_sol": 0.003005, "mean_overhead_sol": 0.00335,
               "updated_at": NOW}  # fmt: skip
        st.put_bid_model(arm)
        assert lab_tx_cost(st, cfg) == 0.0035  # 20 fills: not enough yet
        st.put_bid_model(arm | {"n": 30})  # buy 0.003005 + sell at the same fees + 1.67 failed twins a trade
        assert lab_tx_cost(st, cfg) == pytest.approx((2 * 0.003005 + 0.00335) / 2)  # 0.0047 a transaction
        st.put_bid_model(arm | {"n": 30, "mean_fee_sol": 0.001, "mean_overhead_sol": 0.0})
        assert lab_tx_cost(st, cfg) == 0.0035  # never below the flat cost
        st.put_bid_model({"arm": "paid", "n": 50, "mean_fee_sol": 0.04, "mean_overhead_sol": 0.02, "updated_at": NOW})
        assert lab_tx_cost(st, cfg) == 0.0035  # only the low bid sets it
    finally:
        st.close()
