"""The strategist's evidence is built from the tables only; its turn (mocked Messages API) returns a patch and a reason,
"no change" is a recorded outcome, and the turn stops at its own dollar cap."""

import asyncio
import json

import httpx2
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from snipetech.config import Config
from snipetech.operator.budget import Ledger
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.tools import NoteSink
from snipetech.store import Store
from snipetech.strategist.evidence import week_evidence
from snipetech.strategist.turn import run_strategist_turn

NOW = 1_793_923_200 + 5 * 3600
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


def _message(content, stop_reason, usage=None):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None, "usage": usage or {"input_tokens": 10, "output_tokens": 5}}  # fmt: skip


def _client(handler):
    return AsyncAnthropic(api_key="test", http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))


def _scripted(*replies):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        i = len(requests) - 1
        if i < len(replies):
            return httpx2.Response(200, json=_message(*replies[i]))
        return httpx2.Response(200, json=_message([{"type": "text", "text": "done"}], "end_turn"))

    return handler, requests


def _store():
    st = Store(":memory:")
    st.put_fill({"sig": "b1", "ts": NOW - 3600, "slot": 1, "pos_in_slot": 2, "slot_txs": 5, "wallet": W, "wallet_copied": "L1", "mint": "M1",
                 "side": "buy", "sol": 0.2, "tokens": 100.0, "trades_behind": 2, "price_gap_pct": 20.0, "priority_lamports": 1_000_000,
                 "tip_lamports": 2_000_000, "bid_arm": "control"})  # fmt: skip
    st.put_fill({"sig": "s1", "ts": NOW - 3000, "slot": 2, "wallet": W, "mint": "M1", "side": "sell", "sol": 0.21, "tokens": 100.0,
                 "pnl_sol": 0.008, "bid_arm": "control", "buy_sig": "b1"})  # fmt: skip
    st.put_fill({"sig": "f1", "ts": NOW - 2000, "slot": 3, "wallet": W, "side": "failed", "sol": 0.0, "tokens": 0.0, "bid_arm": "paid",
                 "priority_lamports": 20_000_000})  # fmt: skip
    st.put_bid_model({"arm": "control", "n": 1, "failed_n": 0, "mean_pos": 2.0, "p75_pos": 2.0, "mean_gap_pct": 20.0, "mean_fee_sol": 0.003,
                      "updated_at": NOW, "mean_behind": 2.0, "tipped_share": 1.0, "mean_overhead_sol": 0.0})  # fmt: skip
    st.put_wallet_live(
        {
            "wallet": "L1",
            "n": 1,
            "mean_sol": 0.008,
            "lower_sol": 0.008,
            "last_fill": NOW - 3000,
            "status": "warming",
            "updated_at": NOW,
        }
    )
    st.upsert_candidate("L1", "tape", {}, NOW - 86400)
    st.put_verdict({"wallet": "L1", "computed_at": NOW - 3600, "status": "qualified", "score": 0.01, "n_tape": 30, "lower_sol": 0.002,
                    "next_review": NOW + 86400})  # fmt: skip
    st.set_coverage("events:edge", {"rows": [{"kind": "curve_30", "n": 60, "mean": 0.004, "lower": 0.001}]})
    st.add_events([{"ts": NOW - 86400, "slot": NOW - 86400, "kind": "param_change", "wallet": "book.explore_share", "mint": None,
                    "data": {"key": "book.explore_share", "old": 0.2, "new": 0.25, "reason": "test"}, "source": "strategist"}])  # fmt: skip
    return st


def test_week_evidence_is_a_compact_document_from_the_tables():
    st = _store()
    cfg = Config()
    cfg.operator.wallet = W
    ev = week_evidence(st, cfg, NOW)
    assert (
        ev["window_days"] == 7 and ev["arms"]["control"]["buys"] == 1 and ev["arms"]["control"]["realised_sol"] == 0.008
    )
    assert ev["arms"]["paid"]["failed"] == 1 and ev["bid_model"]["control"]["mean_behind"] == 2.0
    assert ev["wallets_live_top"][0]["wallet"] == "L1" and ev["verdict_counts"]["qualified"] == 1
    assert ev["qualified_top"][0]["wallet"] == "L1" and ev["event_edge"]["rows"][0]["kind"] == "curve_30"
    assert ev["param_changes"][0]["key"] == "book.explore_share"
    assert ev["current"]["book"]["fill_max_sol"] == 1.0 and ev["current"]["qualify"]["landing_pos"] == "+6"
    assert "book.explore_share" in ev["bounds"] and "sk-ant" not in json.dumps(ev)
    assert len(json.dumps(ev)) < 20_000
    st.close()


def test_evidence_carries_round_trip_costs_and_live_against_paper():
    """The weekly turn sees what a whole round trip cost per arm (the buy, the landed sell, the failed sends) and each
    wallet's live result next to its paper score, with the 5-copy pause."""
    st = _store()
    st.put_fill({"sig": "s2", "ts": NOW - 2900, "slot": 4, "wallet": W, "mint": "M1", "side": "sell", "sol": 0.0, "tokens": 0.0,
                 "bid_arm": "control", "priority_lamports": 1_000_000, "tip_lamports": 1_000_000})  # fmt: skip
    st.put_wallet_live({"wallet": "L2", "n": 5, "mean_sol": -0.058, "lower_sol": -0.075, "last_fill": NOW - 100,
                        "status": "live", "updated_at": NOW})  # fmt: skip
    cfg = Config()
    cfg.operator.wallet = W
    ev = week_evidence(st, cfg, NOW)
    # control: buy 0.003 + 5000 lamports, sells 0.000005 and 0.002005, over 2 round trips
    assert abs(ev["round_trip_cost_by_arm"]["control"] - (0.003005 + 0.000005 + 0.002005) / 2) < 2e-6
    assert ev["round_trip_cost_by_arm"]["paid"] is None  # a failed send and no round trip
    rows = {r["wallet"]: r for r in ev["wallets_live_vs_paper"]}
    assert rows["L1"]["paper_score"] == 0.01 and rows["L1"]["paused"] is False
    assert rows["L2"]["upper_sol"] == -0.041 and rows["L2"]["paused"] is True and rows["L2"]["paper_score"] is None
    st.close()


def test_evidence_counts_each_sells_failed_duplicates_once_in_the_arms_result():
    """A sell's failed duplicate sends are beside its pnl_sol (twin_fee_sol): the arm's realised result is the whole
    result, each cost once (7 Nov: realised_sol summed pnl_sol alone)."""
    st = _store()  # its sell s1 made +0.008 with no duplicates
    st.put_fill({"sig": "s3", "ts": NOW - 2800, "slot": 6, "wallet": W, "mint": "M3", "side": "sell", "sol": 0.199443,
                 "tokens": 100.0, "pnl_sol": -0.006567, "twin_fee_sol": 0.00401, "bid_arm": "control"})  # fmt: skip
    cfg = Config()
    cfg.operator.wallet = W
    assert week_evidence(st, cfg, NOW)["arms"]["control"]["realised_sol"] == round(0.008 - 0.006567 - 0.00401, 6)
    st.close()


def test_evidence_carries_exit_reasons_crossed_sells_and_unmanaged_coins():
    st = _store()
    st.add_events([{"ts": NOW - 600, "slot": 5, "kind": "unmanaged", "wallet": "L1", "mint": "M9",
                    "data": {"reason": "due_exit", "value_sol": 0.15}, "source": "watch"}])  # fmt: skip
    cfg = Config()
    cfg.operator.wallet = W
    ev = week_evidence(st, cfg, NOW)
    assert "exit_reasons" in ev and "crossed_sells" in ev
    assert ev["unmanaged"] and ev["unmanaged"][0]["mint"] == "M9" and ev["unmanaged"][0]["reason"] == "due_exit"
    st.close()


def _env(tmp_path):
    paths = OperatorPaths(tmp_path / "data").ensure()
    cfg = Config()
    cfg.operator.wallet = W
    ledger = Ledger(paths.spend, cfg.operator.prices)
    notes = NoteSink(paths, now=lambda: NOW)
    return cfg, ledger, notes, paths


def test_turn_returns_patch_and_reason(tmp_path):
    cfg, ledger, notes, paths = _env(tmp_path)
    handler, requests = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "read_evidence", "input": {}}], "tool_use"),
        (
            [
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "propose_patch",
                    "input": {"patch": {"book.explore_share": 0.25}, "reason": "the paid arm needs more fills"},
                }
            ],
            "tool_use",
        ),
        ([{"type": "text", "text": "proposed"}], "end_turn"),
    )
    out = asyncio.run(
        run_strategist_turn(cfg, {"window_days": 7}, ledger, notes, client=_client(handler), now=lambda: NOW)
    )
    assert out["patch"] == {"book.explore_share": 0.25} and out["reason"] == "the paid arm needs more fills"
    assert out["stopped"] == "end_turn" and out["usd"] > 0 and ledger.spent_today(NOW) > 0
    lines = [json.loads(ln) for ln in paths.spend.read_text().splitlines() if ln.strip()]
    assert {ln["mode"] for ln in lines} == {"strategist"}  # its own line in the ledger, priced at its own rates
    expected = 3 * (10 * cfg.operator.prices["strategist_in"] + 5 * cfg.operator.prices["strategist_out"]) / 1e6
    assert (
        abs(ledger.spent_today(NOW) - expected) < 1e-9 and abs(out["usd"] - expected) < 1e-4
    )  # usd is rounded to 4 places
    assert requests[0]["model"] == "claude-opus-5-5" == cfg.operator.strategist_model  # not the analyst's model
    assert requests[0]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert "bounds" in requests[0]["system"].lower()
    assert any(t["name"] == "propose_patch" for t in requests[0]["tools"])


def test_no_change_is_recorded(tmp_path):
    cfg, ledger, notes, paths = _env(tmp_path)
    handler, _ = _scripted(([{"type": "text", "text": "the week is too thin to change anything"}], "end_turn"))
    out = asyncio.run(
        run_strategist_turn(cfg, {"window_days": 7}, ledger, notes, client=_client(handler), now=lambda: NOW)
    )
    assert out["patch"] == {} and out["stopped"] == "end_turn" and "no change" in out["reason"].lower()


def test_turn_budget_stops(tmp_path):
    cfg, ledger, notes, paths = _env(tmp_path)
    cfg.autotune.turn_usd = 0.5
    big = {"input_tokens": 1_000_000, "output_tokens": 0}
    handler, requests = _scripted(
        ([{"type": "tool_use", "id": "t1", "name": "read_evidence", "input": {}}], "tool_use", big),
        (
            [
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "propose_patch",
                    "input": {"patch": {"book.explore_share": 0.25}, "reason": "x"},
                }
            ],
            "tool_use",
            big,
        ),
    )
    out = asyncio.run(
        run_strategist_turn(cfg, {"window_days": 7}, ledger, notes, client=_client(handler), now=lambda: NOW)
    )
    assert out["stopped"] == "turn_usd" and out["patch"] == {} and len(requests) == 1


def test_a_proposal_survives_a_later_budget_stop(tmp_path):
    cfg, ledger, notes, paths = _env(tmp_path)
    cfg.autotune.turn_usd = 0.5
    big = {"input_tokens": 1_000_000, "output_tokens": 0}
    handler, requests = _scripted(
        (
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "propose_patch",
                    "input": {"patch": {"book.explore_share": 0.25}, "reason": "x"},
                }
            ],
            "tool_use",
        ),
        ([{"type": "tool_use", "id": "t2", "name": "read_evidence", "input": {}}], "tool_use", big),
    )
    out = asyncio.run(
        run_strategist_turn(cfg, {"window_days": 7}, ledger, notes, client=_client(handler), now=lambda: NOW)
    )
    assert out["stopped"] == "turn_usd" and out["patch"] == {"book.explore_share": 0.25}


def test_the_strategist_sees_what_was_learned_and_adds_to_it(tmp_path):
    """It reads the analyst's lessons and open ideas in its message and may record a lesson of its own; it cannot
    add or change ideas (that stays with the analyst), and the one-patch rule is unchanged."""
    from snipetech.operator import learning

    cfg, ledger, notes, paths = _env(tmp_path)
    learning.add_lesson(paths, "Crowded leaders lose at our landing", "4 of 5 losses 2-4 behind", 5, source="analyst",
                        now=NOW - 60)  # fmt: skip
    learning.upsert_idea(paths, {"title": "Raise the copy floor to 0.5 SOL", "why": "7 of 9 losses copied small buys",
                                 "test": "lab --by-wallet at min_copy_sol 0.5", "expected": "+0.004 SOL a trade",
                                 "kind": "config"}, source="analyst", now=NOW - 60)  # fmt: skip
    add = {"text": "A lower bar copied more losers", "evidence": "min_score 0.001: 10 fills at -0.045 SOL", "n": 10}
    handler, requests = _scripted(([{"type": "tool_use", "id": "t1", "name": "add_lesson", "input": add}], "tool_use"),
                                  ([{"type": "text", "text": "no change: too few trips at the new bar"}], "end_turn"))  # fmt: skip
    out = asyncio.run(
        run_strategist_turn(cfg, {"window_days": 7}, ledger, notes, client=_client(handler), now=lambda: NOW)
    )
    content = requests[0]["messages"][0]["content"]
    assert "Crowded leaders lose at our landing" in content and "Raise the copy floor to 0.5 SOL" in content
    names = {t["name"] for t in requests[0]["tools"]}
    assert {"read_evidence", "propose_patch", "read_lessons", "list_ideas", "add_lesson"} <= names
    assert "upsert_idea" not in names and out["patch"] == {} and out["stopped"] == "end_turn"
    assert "[strategist, n=10] A lower bar copied more losers" in (paths.root / "lessons.md").read_text()


def test_a_turn_that_must_not_learn_gets_no_add_lesson(tmp_path):
    cfg, ledger, notes, paths = _env(tmp_path)
    handler, requests = _scripted(([{"type": "text", "text": "no change"}], "end_turn"))
    asyncio.run(run_strategist_turn(cfg, {}, ledger, notes, client=_client(handler), now=lambda: NOW, learn=False))
    names = {t["name"] for t in requests[0]["tools"]}
    assert "add_lesson" not in names and "read_lessons" in names


def test_the_strategist_sees_the_research_record_too(tmp_path):
    """The strategist's message carries the research record after the lessons and ideas, from the checkout it runs
    from (the server's units run it from /opt/snipetech, the repository above the package)."""
    cfg, ledger, notes, _ = _env(tmp_path)
    research = tmp_path / "checkout" / "docs" / "research"
    research.mkdir(parents=True)
    (research / "2026-11-07-round-3.md").write_text("# Research round 3\n\n## Report\n\nOUTCOME: no edge at our "
                                                    "landing.\n\n## Lessons\n\n- A close landing is still not an edge\n")  # fmt: skip
    handler, requests = _scripted(([{"type": "text", "text": "no change"}], "end_turn"))
    asyncio.run(run_strategist_turn(cfg, {"window_days": 7}, ledger, notes, client=_client(handler), now=lambda: NOW,
                                    repo=tmp_path / "checkout"))  # fmt: skip
    content = requests[0]["messages"][0]["content"]
    assert "OUTCOME: no edge at our landing." in content and "- A close landing is still not an edge" in content
    assert content.index("Open ideas") < content.index("Research record")


def test_the_strategist_never_turns_a_rounds_build_into_a_patch(tmp_path):
    """The one agent that can change the config by itself is told: what a round refuted is never proposed, and a
    round's builds and owner questions are the cloud session's and the owner's, never a patch."""
    cfg, ledger, notes, _ = _env(tmp_path)
    handler, requests = _scripted(([{"type": "text", "text": "no change"}], "end_turn"))
    asyncio.run(run_strategist_turn(cfg, {}, ledger, notes, client=_client(handler), now=lambda: NOW))
    sysm = requests[0]["system"]
    system = " ".join((sysm if isinstance(sysm, str) else sysm[0]["text"]).split())
    for words in ("research record", "never propose what a round refuted", "builds and its questions for the owner",
                  "belong to the cloud session and the owner", "never turn a proposed build into a patch"):  # fmt: skip
        assert words in system, words
