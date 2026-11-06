"""The agents' memory between turns: lessons (operator/lessons.md, append-only, dated, each with its evidence and n)
and ideas (operator/ideas.json, written atomically). Code only; nothing here trades or changes a setting."""


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
