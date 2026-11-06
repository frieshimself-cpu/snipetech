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
    assert {ln["mode"] for ln in lines} == {"strategist"}  # its own line in the ledger, priced at the analyst rates
    expected = 3 * (10 * cfg.operator.prices["analyst_in"] + 5 * cfg.operator.prices["analyst_out"]) / 1e6
    assert abs(ledger.spent_today(NOW) - expected) < 1e-9 and abs(out["usd"] - expected) < 1e-4  # usd is rounded to 4 places
    assert requests[0]["model"] == cfg.operator.analyst_model and "bounds" in requests[0]["system"].lower()
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
