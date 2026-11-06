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
