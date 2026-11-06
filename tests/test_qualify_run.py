"""The qualify runner: due candidates → labels → one lab (never beside another, never more often than the interval)
→ verdicts; wallets without tape data get a 72 h review without a lab."""

import json
import os

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


def _tape(st, wallets):
    """Each wallet buys early in a few coins so it has tape trades; the tape span covers them."""
    D = addr("dev")
    for i, w in enumerate(wallets):
        for k in range(3):
            m = Market(st, addr(f"{w}:{k}"), D, NOW - 7000 + i * 100 + k * 1000, SLOT + i * 250 + k * 2500)
            m.buy(D, 1.0, 0)
            m.buy(w, 0.5, 5)
            m.buy(addr(f"f{i}{k}"), 0.3, 6)
            m.sell(w, 1.0, 30)
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
