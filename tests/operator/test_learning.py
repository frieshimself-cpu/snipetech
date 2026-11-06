"""The agents' memory between turns: lessons (operator/lessons.md, append-only, dated, each with its evidence and n)
and ideas (operator/ideas.json, written atomically). Code only; nothing here trades or changes a setting."""

import json

import pytest

from snipetech.operator import learning as L
from snipetech.operator.paths import OperatorPaths

NOW = 1_793_923_200 + 18 * 3600  # Friday 6 Nov 2026, 18:00 UTC
H = 3600


@pytest.fixture
def paths(tmp_path):
    return OperatorPaths(tmp_path / "data").ensure()


def test_a_lesson_is_one_dated_line_with_its_evidence_and_n(paths):
    lesson = L.add_lesson(paths, "Crowded leaders lose at our landing", "4 of 5 losses landed 2-4 trades behind", 5,
                          source="analyst", now=NOW)  # fmt: skip
    assert lesson == {"ts": "2026-11-06T18:00:00Z", "source": "analyst", "n": 5,
                      "text": "Crowded leaders lose at our landing", "evidence": "4 of 5 losses landed 2-4 trades behind"}  # fmt: skip
    lines = (paths.root / "lessons.md").read_text().splitlines()
    assert lines[-1] == ("- 2026-11-06T18:00:00Z [analyst, n=5] Crowded leaders lose at our landing | evidence: "
                         "4 of 5 losses landed 2-4 trades behind")  # fmt: skip


def test_lessons_are_append_only_and_read_back_newest_last(paths):
    for i in range(12):
        L.add_lesson(paths, f"lesson {i}", f"fills {i}", i + 1, source="operator", now=NOW + i * 60)
    before = (paths.root / "lessons.md").read_text()
    L.add_lesson(paths, "lesson 12", "fills 12", 13, source="strategist", now=NOW + 12 * 60)
    after = (paths.root / "lessons.md").read_text()
    assert after.startswith(before)  # nothing earlier is ever rewritten
    last = L.read_lessons(paths, 10)
    assert [x["text"] for x in last] == [f"lesson {i}" for i in range(3, 13)]
    assert last[-1]["source"] == "strategist" and last[-1]["n"] == 13 and last[0]["ts"] == "2026-11-06T18:03:00Z"


def test_a_lesson_needs_text_evidence_and_a_count(paths):
    for text, evidence, n in (("", "x", 3), ("x", "", 3), ("x", "y", 0), ("x", "y", "many")):
        with pytest.raises(ValueError):
            L.add_lesson(paths, text, evidence, n, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="300"):
        L.add_lesson(paths, "x" * 301, "y", 3, source="analyst", now=NOW)
    assert L.read_lessons(paths, 10) == []


def test_a_lesson_never_carries_a_secret(paths, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:telegram-secret-value")
    keypair = "4" * 88  # a 64-byte key (or a whole signature) in base58
    for text in ("key sk-ant-api03-abcdefghij", "token 123456:telegram-secret-value leaked", f"wallet {keypair}",
                 "api_key=abcdefgh12345678", "-----BEGIN PRIVATE KEY-----"):  # fmt: skip
        with pytest.raises(ValueError, match="secret"):
            L.add_lesson(paths, "a lesson", text, 3, source="analyst", now=NOW)
    # a wallet or a mint is public and fine as evidence
    L.add_lesson(
        paths,
        "a lesson",
        "leader 6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at sold into us",
        1,
        source="analyst",
        now=NOW,
    )
    assert len(L.read_lessons(paths, 10)) == 1


def test_the_same_lesson_is_not_written_twice(paths):
    L.add_lesson(paths, "Fees ate the small wins", "6 of 9 wins under 0.009 SOL", 9, source="analyst", now=NOW)
    again = L.add_lesson(paths, "Fees  ate the small wins", "same", 9, source="postmortem", now=NOW + H)
    assert again is None and len(L.read_lessons(paths, 10)) == 1


def test_a_pipe_in_the_text_cannot_break_the_line_format(paths):
    L.add_lesson(paths, "exit a | exit b", "n 3 | n 4", 7, source="analyst", now=NOW)
    (x,) = L.read_lessons(paths, 10)
    assert x["text"] == "exit a / exit b" and x["evidence"] == "n 3 / n 4" and x["n"] == 7


IDEA = {"title": "Copy only leader buys of 0.5 SOL or more", "why": "7 of 9 losses copied buys under 0.3 SOL (n 9)",
        "test": "lab run --by-wallet with min_copy_sol 0.5 on the last 72 h", "expected": "+0.004 SOL a trade",
        "kind": "research"}  # fmt: skip


def test_an_idea_is_stored_with_its_evidence_test_and_owner_flag(paths):
    idea = L.upsert_idea(paths, IDEA, source="analyst", now=NOW)
    assert idea == {"id": "I1", **IDEA, "status": "open", "owner_needed": False, "priority": 3,
                    "created": "2026-11-06T18:00:00Z", "updated": "2026-11-06T18:00:00Z", "source": "analyst"}  # fmt: skip
    assert json.loads((paths.root / "ideas.json").read_text()) == [idea]
    assert not list(paths.root.glob("*.tmp"))  # written to a temporary file and moved into place


def test_an_idea_is_updated_by_id_or_by_its_title_never_duplicated(paths):
    L.upsert_idea(paths, IDEA, source="analyst", now=NOW)
    second = L.upsert_idea(paths, IDEA | {"title": "Re-qualify at +9", "kind": "config"}, source="analyst", now=NOW)
    assert second["id"] == "I2"
    moved = L.upsert_idea(paths, {"id": "I1", "status": "testing", "why": "job 20261106-1810 started"},
                          source="postmortem", now=NOW + H)  # fmt: skip
    assert moved["status"] == "testing" and moved["why"] == "job 20261106-1810 started"
    assert moved["created"] == "2026-11-06T18:00:00Z" and moved["updated"] == "2026-11-06T19:00:00Z"
    assert moved["test"] == IDEA["test"] and moved["source"] == "analyst"  # what was not given is kept
    same_title = L.upsert_idea(paths, {"title": "copy only leader buys of 0.5 SOL or MORE", "priority": 5},
                               source="operator", now=NOW + 2 * H)  # fmt: skip
    assert same_title["id"] == "I1" and same_title["priority"] == 5 and same_title["status"] == "testing"
    assert [i["id"] for i in L.list_ideas(paths, status="all")] == ["I1", "I2"]
    with pytest.raises(ValueError, match="I9"):
        L.upsert_idea(paths, {"id": "I9", "status": "adopted"}, source="analyst", now=NOW)


def test_live_tests_and_code_changes_always_need_the_owner(paths):
    live = L.upsert_idea(paths, IDEA | {"title": "Copy 9WzD live at 0.1 SOL", "kind": "live-test", "owner_needed": False},
                         source="analyst", now=NOW)  # fmt: skip
    code = L.upsert_idea(paths, IDEA | {"title": "Read the leader's sells from the tape", "kind": "code"},
                         source="analyst", now=NOW)  # fmt: skip
    outside = L.upsert_idea(paths, IDEA | {"title": "Fill 2 SOL per copy", "kind": "config", "owner_needed": True},
                            source="analyst", now=NOW)  # fmt: skip
    assert live["owner_needed"] and code["owner_needed"] and outside["owner_needed"]
    again = L.upsert_idea(paths, {"id": live["id"], "owner_needed": False}, source="analyst", now=NOW)
    assert again["owner_needed"] is True


def test_a_new_idea_needs_every_field_and_valid_choices(paths):
    for missing in ("title", "why", "test", "expected", "kind"):
        with pytest.raises(ValueError, match=missing):
            L.upsert_idea(paths, {k: v for k, v in IDEA.items() if k != missing}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="kind"):
        L.upsert_idea(paths, IDEA | {"kind": "trade"}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="status"):
        L.upsert_idea(paths, IDEA | {"status": "done"}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="priority"):
        L.upsert_idea(paths, IDEA | {"priority": 9}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="secret"):
        L.upsert_idea(paths, IDEA | {"why": "key sk-ant-api03-abcdefghij"}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="600"):
        L.upsert_idea(paths, IDEA | {"why": "x" * 601}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="size"):
        L.upsert_idea(paths, IDEA | {"size": 2.0}, source="analyst", now=NOW)
    assert L.list_ideas(paths, status="all") == []


def test_open_ideas_are_ranked_and_closed_ones_left_out(paths):
    def put(title, at=NOW, **kw):
        return L.upsert_idea(paths, IDEA | {"title": title} | kw, source="analyst", now=at)

    put("low", priority=1)
    put("high but needs the owner", priority=5, kind="live-test")
    put("high", priority=5)
    put("high and being tested", priority=5, status="testing")
    put("done", priority=5, status="adopted")
    put("dropped", priority=5, status="rejected")
    put("high and newer", priority=5, at=NOW + H)
    ranked = [i["title"] for i in L.list_ideas(paths)]
    assert ranked == ["high and being tested", "high and newer", "high", "high but needs the owner", "low"]
    assert [i["title"] for i in L.list_ideas(paths, status="adopted")] == ["done"]


def test_a_broken_ideas_file_is_kept_aside_not_lost(paths):
    (paths.root / "ideas.json").write_text("{not json")
    assert L.list_ideas(paths) == []
    L.upsert_idea(paths, IDEA, source="analyst", now=NOW)
    kept = list(paths.root.glob("ideas.json.broken-*"))
    assert len(kept) == 1 and kept[0].read_text() == "{not json"
    assert [i["id"] for i in L.list_ideas(paths)] == ["I1"]


def test_the_idea_list_is_capped_by_dropping_the_oldest_closed_idea(paths, monkeypatch):
    monkeypatch.setattr(L, "MAX_IDEAS", 3)
    L.upsert_idea(paths, IDEA | {"title": "a", "status": "rejected"}, source="analyst", now=NOW)
    L.upsert_idea(paths, IDEA | {"title": "b"}, source="analyst", now=NOW + 60)
    L.upsert_idea(paths, IDEA | {"title": "c"}, source="analyst", now=NOW + 120)
    L.upsert_idea(paths, IDEA | {"title": "d"}, source="analyst", now=NOW + 180)
    assert [i["title"] for i in L.list_ideas(paths, status="all")] == ["b", "c", "d"]
    with pytest.raises(ValueError, match="3 ideas"):
        L.upsert_idea(paths, IDEA | {"title": "e"}, source="analyst", now=NOW + 240)


def test_every_turn_sees_the_last_ten_lessons_and_the_top_five_open_ideas(paths):
    for i in range(12):
        L.add_lesson(paths, f"lesson L{i:02d}", f"evidence E{i:02d}", i + 1, source="analyst", now=NOW + i)
    for i, prio in enumerate((1, 2, 3, 4, 5, 1, 2)):
        L.upsert_idea(paths, IDEA | {"title": f"plan-{'ABCDEFG'[i]}", "priority": prio}, source="analyst", now=NOW)
    L.upsert_idea(paths, IDEA | {"title": "plan-X", "priority": 5, "status": "rejected"}, source="analyst", now=NOW)
    text = L.context(paths)
    assert "L01" not in text and all(f"L{i:02d}" in text and f"E{i:02d}" in text for i in range(2, 12))
    assert text.index("L02") < text.index("L11")  # newest last
    shown = [p for p in ("plan-E", "plan-D", "plan-C", "plan-B", "plan-G") if p in text]
    assert shown == ["plan-E", "plan-D", "plan-C", "plan-B", "plan-G"]  # best first
    assert text.index("plan-E") < text.index("plan-D") < text.index("plan-G")
    assert "plan-A" not in text and "plan-F" not in text and "plan-X" not in text


def test_what_every_turn_sees_stays_small(paths):
    for i in range(12):  # words, not one long run of letters (that reads as a key to the secret check)
        L.add_lesson(paths, f"{i:02d} " + "tx " * 96, "ev " * 133, 3, source="analyst", now=NOW + i)
    for i in range(6):
        L.upsert_idea(paths, IDEA | {"title": f"{i} " + "xy " * 36, "why": "wh " * 199, "test": "te " * 133},
                      source="analyst", now=NOW)  # fmt: skip
    text = L.context(paths)
    assert len(text) <= L.CONTEXT_MAX_CHARS and "11 tx tx" in text  # every line cut short, the newest still there


def test_with_nothing_learned_yet_the_turn_is_told_so(paths):
    text = L.context(paths)
    assert "nothing yet" in text and "none yet" in text


def test_the_digest_says_what_was_learned_since_the_last_one_and_what_is_tested_next(paths):
    L.add_lesson(paths, "an old lesson", "e", 2, source="analyst", now=NOW - 7 * H)
    L.add_lesson(paths, "Crowded leaders lose at our landing", "4 of 5 losses 2-4 behind", 5, source="postmortem",
                 now=NOW - 2 * H)  # fmt: skip
    L.upsert_idea(paths, IDEA | {"priority": 4}, source="analyst", now=NOW)
    L.upsert_idea(paths, IDEA | {"title": "Copy 9WzD live at 0.1 SOL", "kind": "live-test", "priority": 5},
                  source="analyst", now=NOW)  # fmt: skip
    lines = L.digest_lines(paths, NOW - 6 * H, NOW)
    assert lines[0] == "What it learned since 12:00 UTC:"
    assert "- Crowded leaders lose at our landing (5 cases)" in lines and not any("old lesson" in x for x in lines)
    i = lines.index("Testing next:")
    assert lines[i + 1 :] == [
        "- Copy 9WzD live at 0.1 SOL (needs your yes)",
        "- Copy only leader buys of 0.5 SOL or more",
    ]


def test_a_quiet_digest_says_nothing_new_was_learned(paths):
    assert L.digest_lines(paths, NOW - 6 * H, NOW) == ["What it learned: nothing new since 12:00 UTC",
                                                        "Testing next: no open ideas yet"]  # fmt: skip


def _sell(st, sig, ts, wallet="W", **kw):
    st.put_fill(
        {"sig": sig, "ts": int(ts), "wallet": wallet, "mint": f"M{sig}", "side": "sell", "sol": 0.19, "tokens": 1.0}
        | kw
    )


def test_new_round_trips_count_from_the_newest_sell_the_last_post_mortem_saw(paths):
    from snipetech.store import Store

    st = Store(":memory:")
    for sig, ts in (("s0", NOW - 30 * H), ("s1", NOW - 5 * H), ("s2", NOW - 4 * H), ("s3", NOW - H)):
        _sell(st, sig, ts)
    st.put_fill({"sig": "b0", "ts": NOW - 2 * H, "wallet": "W", "mint": "M9", "side": "buy", "sol": 0.2, "tokens": 1.0})
    _sell(st, "x0", NOW - 2 * H, wallet="OTHER")
    t = L.trips_since_postmortem(st, "W", paths, NOW)
    assert t == {"n": 3, "since_ts": NOW - 24 * H, "newest_ts": NOW - H}  # never reviewed: the last day counts
    assert L.last_postmortem_ts(paths) is None
    L.mark_postmortem(paths, NOW, t, "postmortem")
    assert L.last_postmortem_ts(paths) == NOW and L.trips_since_postmortem(st, "W", paths, NOW)["n"] == 0
    _sell(st, "s4", NOW - H + 30)  # measured after the post-mortem, sold after the newest sell it saw: new
    later = L.trips_since_postmortem(st, "W", paths, NOW + 600)
    assert later == {"n": 1, "since_ts": NOW - H, "newest_ts": NOW - H + 30}
    L.mark_postmortem(paths, NOW + 600, L.trips_since_postmortem(st, "W", paths, NOW + 600), "analyst")
    L.mark_postmortem(paths, NOW + 900, L.trips_since_postmortem(st, "W", paths, NOW + 900), "analyst")  # none new
    assert L.trips_since_postmortem(st, "W", paths, NOW + 900)["since_ts"] == NOW - H + 30
    st.close()


def test_a_round_trip_reads_as_its_landing_its_exit_its_money_and_the_verdicts_prediction(paths):
    from snipetech.store import Store

    st = Store(":memory:")
    st.put_fill({"sig": "B" * 88, "ts": NOW - 400, "wallet": "W", "wallet_copied": "LEAD", "mint": "M1", "side": "buy", "sol": 0.2,
                 "tokens": 1.0, "trades_behind": 3, "price_gap_pct": 31.0, "pos_in_slot": 4, "bid_arm": "control"})  # fmt: skip
    _sell(st, "S" * 88, NOW - 100, buy_sig="B" * 88, pnl_sol=-0.041, exit_reason="copy_exit", exit_name="lab", crossed=0,
          twin_fee_sol=0.0021)  # fmt: skip
    _sell(st, "old", NOW - 9 * H, pnl_sol=0.01)
    st.upsert_candidate("LEAD", "tape", {}, NOW - 86400)
    st.put_verdict({"wallet": "LEAD", "computed_at": NOW - 3600, "status": "qualified", "score": 0.004, "n_tape": 30,
                    "mean_sol": 0.009, "lower_sol": 0.002, "landing_pos": "+6",
                    "copy_plan": {"min_leader_sol": 0.5, "leader_style": "dumper"}})  # fmt: skip
    out = L.round_trips(st, "W", NOW - 6 * H)
    assert out["summary"] == {"trips": 1, "wins": 0, "losses": 1, "pnl_sol": -0.041}
    (trip,) = out["trips"]
    assert trip["leader"] == "LEAD" and trip["held_secs"] == 300 and trip["trades_behind"] == 3
    assert trip["price_gap_pct"] == 31.0 and trip["exit_reason"] == "copy_exit" and trip["twin_fee_sol"] == 0.0021
    assert trip["verdict"]["mean_sol"] == 0.009 and trip["verdict"]["copy_plan"]["min_leader_sol"] == 0.5
    assert trip["sig"] == "S" * 12 and "B" * 20 not in json.dumps(out)  # short signatures only
    st.close()
