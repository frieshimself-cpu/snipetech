"""The second round of the audit of 22-23 Nov 2026, group "infra": the small leftovers that have no test file of their
own (the paper report's text, the lab's exit horizon, the month's Helius credits for `sends`). The tape's retention
rule is pinned in tests/test_retention.py, the job records in tests/test_ops.py and tests/operator/test_job_records.py,
the wallet profiler's window in tests/test_leader_profiles.py."""

from snipetech import report as R
from snipetech.analysis import paper
from snipetech.config import Config
from snipetech.store import Store

# -- I4: the paper report's text marks a refuted rule ------------------------------------------------------------------
# The report's rows carry `refuted` since round 1 (when, on how many trades, at what mean), and the dashboard lists
# such rules apart. The text an agent or a session reads (`paper report`) showed a rule the paper test had dropped like
# any rule under test: 45 of the 132 stored rules on 22 Nov.


def _stored(sid: str, label: str, **extra) -> dict:
    return {"id": sid, "label": label, "trigger": {"kind": "kol", "sol": 1.0}, "exit": {"max_hold_secs": 60},
            "position": "+6", "latency": 0, "buy_sol": 1.0, "per_tx_sol": 0.0045, "from_ts": 1, "lab": {}} | extra  # fmt: skip


def _report_text(rules: list[dict]) -> tuple[dict, str]:
    store = Store(":memory:")
    store.set_coverage("paper_strategies", {"strategies": rules, "superseded": []})
    rep = paper.report(store, Config(), 100)
    store.close()
    return rep, R.render_paper("report", rep)


def test_the_paper_reports_text_marks_a_refuted_rule_in_its_row():
    gone = {"at": 1_794_700_000, "trades": 412, "mean_sol": -0.0213}
    rep, text = _report_text([_stored("a1d0000000", "a rule under test | hold60s"),
                              _stored("b2d0000000", "a rule the test dropped | hold60s", refuted=gone),
                              _stored("c3d0000000", "another under test | hold60s")])  # fmt: skip
    assert [r.get("refuted") for r in rep["strategies"]] == [None, gone, None]  # the JSON rows, since round 1
    row = {line[:10]: line for line in text.splitlines() if line[:10] in ("a1d0000000", "b2d0000000", "c3d0000000")}
    assert row["b2d0000000"].split()[-1] == "refuted"
    assert "refuted" not in row["a1d0000000"] and "refuted" not in row["c3d0000000"]
    # and once under the tables, what the word means
    (said,) = [line for line in text.splitlines() if line.startswith("refuted (1 of the 3 rules)")]
    assert "scored no more" in said


def test_a_report_without_a_refuted_rule_reads_as_before():
    _rep, text = _report_text([_stored("a1d0000000", "a rule under test | hold60s")])
    assert "refuted" not in text and "state" not in text
    head = next(line for line in text.splitlines() if line.startswith("id "))
    assert head.split()[-2:] == ["lab", "said"]  # the table's last column, as it was
