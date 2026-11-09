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
