"""The second round of the audit of 22-23 Nov 2026, group "infra": the small leftovers that have no test file of their
own (the paper report's text, the lab's exit horizon). The tape's retention rule is pinned in tests/test_retention.py,
the recorder's prune over a restart in tests/test_short_writes.py, the job records in tests/test_ops.py and
tests/operator/test_job_records.py, the wallet profiler's window in tests/test_leader_profiles.py, the month's Helius
credits for `sends` in tests/test_sends.py."""

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


def _table_rows(text: str) -> dict[str, str]:
    """Each rule's row in the report's tables by its id (the ids here end d0000000). A note under the tables starts
    with an id too ("id, id: ...") and is no row: read by its first ten characters alone, the row of the first rule
    was that note."""
    return {line[:10]: line for line in text.splitlines() if line[:10].endswith("d0000000") and line[10:12] == "  "}


def test_the_paper_reports_text_marks_a_refuted_rule_in_its_row():
    gone = {"at": 1_794_700_000, "trades": 412, "mean_sol": -0.0213}
    rep, text = _report_text([_stored("a1d0000000", "a rule under test | hold60s"),
                              _stored("b2d0000000", "a rule the test dropped | hold60s", refuted=gone),
                              _stored("c3d0000000", "another under test | hold60s")])  # fmt: skip
    assert [r.get("refuted") for r in rep["strategies"]] == [None, gone, None]  # the JSON rows, since round 1
    row = _table_rows(text)
    assert sorted(row) == ["a1d0000000", "b2d0000000", "c3d0000000"]
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


def test_the_mark_adds_no_blanks_to_the_rows_without_it():
    """Review of 23 Nov: the mark's column is right-aligned like every other, so each row without the word ended in
    blanks as wide as the column (nine characters a row, in a text the agents read whole)."""
    gone = {"at": 1_794_700_000, "trades": 412, "mean_sol": -0.0213}
    _rep, text = _report_text([_stored("a1d0000000", "a rule under test | hold60s"),
                               _stored("b2d0000000", "a rule the test dropped | hold60s", refuted=gone)])  # fmt: skip
    assert [line for line in text.splitlines() if line != line.rstrip()] == []
    row = _table_rows(text)
    assert row["b2d0000000"].endswith("  refuted")  # the word stands under its column's name, as before
    # the row without it ends at the column before, its cells where they were
    assert len(row["a1d0000000"]) == len(row["b2d0000000"]) - len("  refuted") and row["a1d0000000"].endswith(" -")


# -- I5: the lab's exit horizon counts a timer extension ---------------------------------------------------------------
# A lab checks that the tape holds each moment for as long as its exits can hold (`lab.exits_horizon`: the PumpSwap
# tape behind a MIG buy, a copy that lands on PumpSwap, a migration sell). It counted the timer and the parts of a sale
# in parts, not how far a timer extension moves the timer (`ext_max`): an EXT exit could sell up to that long past the
# stretch the tape was checked to hold, on whatever trades the tape happened to have.


def test_the_exit_horizon_reaches_as_far_as_an_extended_timer_can_sell():
    from dataclasses import replace

    from snipetech.analysis.lab import exits_horizon, in_parts
    from snipetech.sim.exits import exit_from_label, exits_reach

    plain = exit_from_label("hold12s")
    ext = exit_from_label("hold12s EXT1%/s@2s+4s<20s")  # while the price climbs its timer moves on, to 32 s at the most
    assert exits_horizon([plain]) == 12.0
    assert exits_horizon([ext]) == 32.0  # was 12.0
    assert exits_horizon([plain, ext]) == 32.0 == exits_reach([plain, ext])  # as far as the simulator reads the path
    # an extension that is off moves no timer, whatever its other fields hold (ext_max is 30 s by default)
    assert plain.ext_max == 30.0 and exits_horizon([replace(ext, ext_velocity=0.0)]) == 12.0
    assert exits_horizon([replace(ext, ext_step=0.0)]) == 12.0
    # the parts of a sale in parts follow the timer where it ended
    assert exits_horizon(in_parts([ext], 4, 15.0)) == 32.0 + 3 * 15.0


def test_a_moment_the_pumpswap_tape_holds_for_the_timer_alone_is_no_moment_of_an_extended_exit():
    from types import SimpleNamespace

    from snipetech.analysis.lab import exits_horizon, mig_gap
    from snipetech.sim.exits import exit_from_label

    at = 1_794_000_000
    # the PumpSwap tape holds the coin for 20 s after its migration (the recorder's run ends there)
    cov = {"runs": [{"from": at, "to": at + 20, "secs": 1800, "skipped": [], "slot_secs": 0.214}]}
    mig = SimpleNamespace(ts=at)
    assert mig_gap(cov, None, None, mig, exits_horizon([exit_from_label("hold12s")])) is None  # 12 s and a slot
    ext = exit_from_label("hold12s EXT1%/s@2s+4s<20s")
    assert mig_gap(cov, None, None, mig, exits_horizon([ext])) == "end"  # up to 32 s: past what the tape holds
