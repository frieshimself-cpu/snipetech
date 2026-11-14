"""The agents' thinking on the record (owner, 9 Nov: "see exactly what each thing is doing ... how they are thinking"):
each turn's reasoning, what it said, each tool it called with what it asked and what came back, one line per turn in
operator/turns.jsonl, secrets scrubbed, the file kept small, the newest turns read back for the live page."""

import json

from snipetech.operator.turnlog import TURNS_MAX_BYTES, TurnLog, read_turns

from .test_turn import Decision, _run, _scripted, env  # noqa: F401 - the turn fixture


def test_a_turn_writes_its_reasons_thinking_words_and_each_tool_with_its_result(env):  # noqa: F811
    handler, _ = _scripted(
        ([{"type": "thinking", "thinking": "The lab lost 0.017 a trade, so I reject I41.", "signature": "sig"},
          {"type": "text", "text": "Writing the result down."},
          {"type": "tool_use", "id": "t1", "name": "write_note", "input": {"text": "I41 lost", "level": "info"}}],
         "tool_use"),
        ([{"type": "text", "text": "Done for this round."}], "end_turn"),
    )  # fmt: skip
    _run(env, Decision(True, "analyst", ["job ended: lab 130222"]), handler)
    turns = read_turns(env["paths"], 10)
    assert len(turns) == 1
    t = turns[0]
    assert t["mode"] == "analyst" and t["reasons"] == ["job ended: lab 130222"] and t["stopped"] == "end_turn"
    kinds = [s["kind"] for s in t["steps"]]
    assert kinds == ["thinking", "say", "tool", "say"]
    tool = t["steps"][2]
    assert tool["name"] == "write_note" and tool["input"]["text"] == "I41 lost" and tool["result"]
    assert "reject I41" in t["steps"][0]["text"]


def test_secrets_never_reach_the_turn_record(env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("HELIUS_API_KEY", "supersecretvalue123")
    log = TurnLog("operator", ["quiet check"], started=1.0)
    log.add_message([{"type": "text", "text": "the key is supersecretvalue123"}])
    log.finish({"usd": 0.1, "tool_calls": 0, "stopped": "end_turn"}, env["paths"], ended=2.0)
    raw = (env["paths"].root / "turns.jsonl").read_text()
    assert "supersecretvalue123" not in raw and "<redacted>" in raw


def test_long_text_is_cut_and_the_file_stays_small(env):  # noqa: F811
    for i in range(40):
        log = TurnLog("operator", [f"round {i}"], started=float(i))
        log.add_message([{"type": "text", "text": "x" * 50_000}])
        log.finish({"usd": 0.0, "tool_calls": 0, "stopped": "end_turn"}, env["paths"], ended=float(i) + 1)
    path = env["paths"].root / "turns.jsonl"
    assert path.stat().st_size <= TURNS_MAX_BYTES
    last = read_turns(env["paths"], 3)
    assert [t["reasons"][0] for t in last] == ["round 37", "round 38", "round 39"]
    assert all(len(s["text"]) < 10_000 for t in last for s in t["steps"])
    assert all(json.loads(line) for line in path.read_text().splitlines())


def test_a_refusal_that_repeats_the_row_before_it_is_not_written(env):  # noqa: F811
    """14 Nov: 199 of the newest 307 rows were turns the API refused for want of credit before one tool call; a
    quarter of an hour after the account ran dry the owner's page showed nothing else. The first refusal of a run
    stays, so the page says why it went quiet; a turn the empty account cut after it had worked keeps its row."""
    paths = env["paths"]
    refused = {"usd": 0.0, "tool_calls": 0, "stopped": "api_error", "error": "400: Your credit balance is too low"}

    def finish(mode, out, said=None, at=1.0):
        log = TurnLog(mode, ["a job of yours ended: j1"], started=at)
        if said:
            log.add_message([{"type": "text", "text": said}])
        log.finish(out, paths, ended=at + 1)

    def rows():
        return [(t["mode"], t["stopped"]) for t in read_turns(paths, 20)]

    finish("scout", {"usd": 0.9, "tool_calls": 3, "stopped": "end_turn"}, "read the lab")
    for i, mode in enumerate(("tester", "fees", "scout", "oktech", "operator")):
        finish(mode, refused, at=10.0 + i)
    assert rows() == [("scout", "end_turn"), ("tester", "api_error")]
    finish("fees", refused | {"usd": 0.4, "tool_calls": 2}, "half a turn, then the account ran dry", at=20.0)
    finish("scout", refused, at=21.0)
    finish("tester", refused, at=22.0)
    assert rows()[2:] == [("fees", "api_error"), ("scout", "api_error")]
    finish("oktech", refused | {"error": "529: Overloaded"}, at=23.0)  # the API says something else: that is news
    finish("oktech", refused | {"error": "529: Overloaded"}, at=24.0)
    finish("scout", {"usd": 0.0, "tool_calls": 0, "stopped": "end_turn"}, at=25.0)  # no refusal: every turn is kept
    finish("scout", {"usd": 0.0, "tool_calls": 0, "stopped": "end_turn"}, at=26.0)
    assert rows()[4:] == [("oktech", "api_error"), ("scout", "end_turn"), ("scout", "end_turn")]
    assert all(json.loads(line) for line in (paths.root / "turns.jsonl").read_text().splitlines())
