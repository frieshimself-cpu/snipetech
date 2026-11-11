"""The paper report computes a strategy's numbers when its trades change, not every time it is read (11 Nov).

The operator's 15-minute round, profiled on the server: 42 s, 39.5 of them in this report. It read every paper trade
(511,121 rows, each one's JSON decoded) and ran the bootstrap twice for each of 36 strategies, plus a read for each of
37 superseded ones. 16 strategies had had a trade in the last two hours (6,099 rows); the two largest, 72,633 trades
each, had been retired for 18 hours, and their bootstrap alone draws a 2,000 x 72,633 table of indexes: the round's
2.3 GB memory peak. The live-test step and the automatic test read the same report.

The numbers are the same to the last digit (the bootstrap is seeded): what is kept is each strategy's result with what
it was computed from (how many trades, the newest, their sum, the confidence, the venue tags).
"""

import time

from snipetech.analysis import paper
from snipetech.config import Config
from snipetech.store import PAPER_COUNTS_SQL, Store

NOW = int(time.time())


def _strategy(sid, kind="kol", **more):
    return {"id": sid, "label": sid, "trigger": {"kind": kind}, "exit": {}, "position": "+3x", "latency": 0,
            "buy_sol": 0.2, "from_ts": NOW - 86400, "lab": {"mean": 0.01, "ev_lower": 0.001, "landing": "+3x"}} | more  # fmt: skip


def _trades(st, sid, pnls, at=NOW - 80000, venue=None):
    for i, p in enumerate(pnls):
        st.put_paper_trade({"strategy": sid, "mint": f"{sid}-{at}-{i}", "trigger_ts": at + i, "entry_ts": at + i + 0.4,
                            "pnl": p, "roi": p / 0.2, "data": {"venue": venue} if venue else {}})  # fmt: skip


def _store():
    st = Store(":memory:")
    st.set_coverage("paper_strategies", {"strategies": [_strategy("a"), _strategy("b"), _strategy("h", kind="hunt")],
                                         "superseded": [_strategy("old")]})  # fmt: skip
    _trades(st, "a", [0.01, -0.02, 0.03, 0.005] * 10)
    _trades(st, "b", [-0.01, 0.002] * 30)
    _trades(st, "h", [0.5, -0.1, -0.1, 0.02] * 8, venue="pumpfun")  # `run` writes each row's launchpad (8 Nov)
    _trades(st, "old", [0.01] * 12)
    st.set_coverage("paper:venues", {"done": True, "tagged": 0, "untold": 0})
    return st


def _counting(monkeypatch, st):
    seen = {"summaries": 0, "reads": []}
    real_summarize, real_read = paper.summarize, st.paper_trades

    def summarize(*a, **k):
        seen["summaries"] += 1
        return real_summarize(*a, **k)

    def paper_trades(strategy=None):
        seen["reads"].append(strategy)
        return real_read(strategy)

    monkeypatch.setattr(paper, "summarize", summarize)
    monkeypatch.setattr(st, "paper_trades", paper_trades)
    return seen


def test_a_second_reading_with_no_new_trade_computes_and_reads_nothing(monkeypatch):
    st = _store()
    try:
        cfg = Config()
        seen = _counting(monkeypatch, st)
        first = paper.report(st, cfg, now=NOW)
        assert seen["summaries"] == 6 and sorted(seen["reads"]) == ["a", "b", "h", "old"]  # two bounds a strategy
        seen["summaries"], seen["reads"] = 0, []
        again = paper.report(st, cfg, now=NOW)
        assert again == first
        assert seen["summaries"] == 0 and seen["reads"] == []
    finally:
        st.close()


def test_a_new_trade_recomputes_that_strategy_alone_and_the_numbers_are_the_uncached_ones(monkeypatch):
    st = _store()
    try:
        cfg = Config()
        paper.report(st, cfg, now=NOW)
        _trades(st, "b", [0.2, 0.2], at=NOW - 100)
        seen = _counting(monkeypatch, st)
        rep = paper.report(st, cfg, now=NOW)
        assert seen["summaries"] == 2 and seen["reads"] == ["b"]
        st.set_coverage(paper.REPORT_CACHE_KEY, {})  # nothing kept: everything from scratch
        assert paper.report(st, cfg, now=NOW) == rep
        by = {s["id"]: s for s in rep["strategies"]}
        assert by["b"]["n"] == 62 and abs(by["b"]["total"] - (30 * -0.008 + 0.4)) < 1e-9
    finally:
        st.close()


def test_another_confidence_or_sample_count_or_buy_size_recomputes(monkeypatch):
    st = _store()
    try:
        cfg = Config()
        paper.report(st, cfg, now=NOW)
        seen = _counting(monkeypatch, st)
        cfg.verdict.confidence = 0.9
        paper.report(st, cfg, now=NOW)
        assert seen["summaries"] == 6  # the superseded one has no bounds: its count and mean stand
        seen["summaries"] = 0
        cov = st.coverage("paper_strategies")
        cov["strategies"][0]["buy_sol"] = 0.5  # the cap on a trade's gain counts in buys
        st.set_coverage("paper_strategies", cov)
        paper.report(st, cfg, now=NOW)
        assert seen["summaries"] == 2
    finally:
        st.close()


def test_a_venue_tag_written_later_recomputes_the_launch_rules(monkeypatch):
    """A launch-type rule's record leaves out trades on another launchpad, by each row's tag (`record`): the tags are
    part of what a result was computed from."""
    st = _store()
    try:
        cfg = Config()
        before = {s["id"]: s for s in paper.report(st, cfg, now=NOW)["strategies"]}
        assert before["h"]["n"] == 32 and before["h"]["other_venue"] == 0
        st.set_paper_venues([("h", f"h-{NOW - 80000}-0", "launchlab")])
        st.set_coverage("paper:venues", {"done": True, "tagged": 1, "untold": 0})
        seen = _counting(monkeypatch, st)
        after = {s["id"]: s for s in paper.report(st, cfg, now=NOW)["strategies"]}
        assert after["h"]["n"] == 31 and after["h"]["other_venue"] == 1
        assert seen["reads"] == ["h"]  # a copy rule has no venue: nothing of a or b is read again
    finally:
        st.close()


def test_a_store_that_cannot_be_written_still_gives_the_report(monkeypatch):
    st = _store()
    try:
        import sqlite3

        def refuse(*a, **k):
            raise sqlite3.OperationalError("attempt to write a readonly database")

        want = paper.report(st, Config(), now=NOW)
        st.set_coverage(paper.REPORT_CACHE_KEY, {})
        monkeypatch.setattr(st, "set_coverage", refuse)
        assert paper.report(st, Config(), now=NOW) == want
    finally:
        st.close()


def test_a_launch_rule_with_a_row_only_the_tape_can_place_is_computed_every_time(monkeypatch):
    """`record` asks the tape for the launchpad of a row that carries none, and the tape forgets coins (retention): the
    answer can change with no paper trade changing, so such a rule's numbers are never kept. (The server has no such
    row: 511,124 of 511,124 carry their launchpad, 11 Nov.)"""
    st = _store()
    try:
        cfg = Config()
        _trades(st, "h", [0.07], at=NOW - 500)  # no launchpad on the row
        paper.report(st, cfg, now=NOW)
        seen = _counting(monkeypatch, st)
        rep = paper.report(st, cfg, now=NOW)
        assert seen["reads"] == ["h"] and seen["summaries"] == 2
        assert {s["id"]: s["n"] for s in rep["strategies"]}["h"] == 33
    finally:
        st.close()


def test_trades_written_while_the_report_reads_are_not_kept_under_the_older_count(monkeypatch):
    """The paper run writes trades while the operator's round reads the report: numbers made from more rows than were
    counted a moment before are given, and not kept."""
    st = _store()
    try:
        cfg = Config()
        real = st.paper_trades

        def read_after_a_write(strategy=None):
            if strategy == "b":
                _trades(st, "b", [0.3], at=NOW - 50)
            return real(strategy)

        monkeypatch.setattr(st, "paper_trades", read_after_a_write)
        racy = {s["id"]: s for s in paper.report(st, cfg, now=NOW)["strategies"]}
        assert racy["b"]["n"] == 61  # what was read
        monkeypatch.setattr(st, "paper_trades", real)
        seen = _counting(monkeypatch, st)
        after = {s["id"]: s for s in paper.report(st, cfg, now=NOW)["strategies"]}
        assert seen["reads"] == ["b"] and after["b"] == racy["b"]
        seen["reads"] = []
        paper.report(st, cfg, now=NOW)
        assert seen["reads"] == []
    finally:
        st.close()


def test_what_each_rules_trades_are_is_read_from_an_index_alone():
    """The count, the newest trigger and the sum of every rule's trades, which say whether its numbers still stand:
    2 s of walking all 511,124 rows through the table on the server; from the index, none is read."""
    st = _store()
    try:
        got = st.paper_counts()
        assert {k: v[:2] for k, v in got.items()} == {"a": [40, NOW - 80000 + 39], "b": [60, NOW - 80000 + 59],
                                                      "h": [32, NOW - 80000 + 31], "old": [12, NOW - 80000 + 11]}  # fmt: skip
        assert abs(got["a"][2] - 0.25) < 1e-9 and abs(got["old"][2] - 0.12) < 1e-9
        plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN " + PAPER_COUNTS_SQL))
        assert "USING COVERING INDEX paper_trades_strategy" in plan, plan
        # a rule's trades in time order come off the same index, with no sort
        plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN SELECT mint, pnl FROM paper_trades WHERE strategy=? "
                                             "ORDER BY trigger_ts", ("a",)))  # fmt: skip
        assert "paper_trades_strategy" in plan and "TEMP B-TREE" not in plan, plan
    finally:
        st.close()


def test_a_database_made_before_the_indexes_gets_them_when_it_is_opened(tmp_path):
    path = tmp_path / "t.db"
    st = Store(path)
    st._exec("DROP INDEX paper_trades_strategy")
    st._exec("DROP INDEX paper_trades_entry")
    st.close()
    st = Store(path)
    try:
        names = {r[0] for r in st._q("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='paper_trades'")}
        assert {"paper_trades_strategy", "paper_trades_entry"} <= names
    finally:
        st.close()


def test_the_newest_paper_trades_and_a_window_of_them_are_found_by_their_time():
    """The live page's 25 newest paper trades (every 10 s while it is open) and the daily report's "paper trades since"
    line both walked the whole table."""
    st = _store()
    try:
        plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN SELECT strategy, mint, entry_ts, exit_ts, pnl, roi, reason "
                                             "FROM paper_trades ORDER BY entry_ts DESC LIMIT 25"))  # fmt: skip
        assert "USING INDEX paper_trades_entry" in plan and "TEMP B-TREE" not in plan, plan
        plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN SELECT strategy, mint, pnl, data FROM paper_trades WHERE "
                                             "entry_ts > ? AND entry_ts <= ?", (1, 2)))  # fmt: skip
        assert "USING INDEX paper_trades_entry" in plan, plan
    finally:
        st.close()


def test_the_kept_numbers_carry_what_the_live_page_shows():
    st = _store()
    try:
        paper.report(st, Config(), now=NOW)
        rows = st.coverage(paper.REPORT_CACHE_KEY)["rows"]
        b = rows["b"]["numbers"]
        assert b["n"] == 60 and abs(b["total"] - 30 * -0.008) < 1e-9
        assert abs(b["mean_roi"] - (-0.008 / 2 / 0.2)) < 1e-9  # the mean return of its trades
        assert b["last_entry"] == NOW - 80000 + 59 + 0.4  # when its newest trade was bought
        assert rows["old"]["numbers"]["n"] == 12  # the superseded ones too: count, mean, total
    finally:
        st.close()
