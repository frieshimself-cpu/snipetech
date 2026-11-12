"""The kept trades of a paper rule, priced again by today's simulator.

The paper test keeps what each trade made under the code of its day. On 11 Nov 2026 the simulator stopped overstating
winners at size (tests/test_fill_model.py); the trades a size rule had made before that were old-model, and every run
after it added new-model ones to the same record. `paper rescore` prices the same trades again: the same entry, the
same exit rule, today's pricing. A trade whose moment the tape no longer gives is left as it is, and counted."""

import json

import numpy as np
import pytest

from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger
from snipetech.cli import build_parser
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.sim.market import FILL_MODEL, MarketPath
from snipetech.store import Store

from .synth import Market, addr

T0 = 1_793_923_200  # 6 Nov 2026 00:00 UTC
NOW = T0 + 4 * 3600
SLOT0 = 400_000_000
EXIT = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False)
COPY = Trigger("kol", sol=2.0)


def _coin(store: Store, k: int, ts: int) -> str:
    """A leader's 2.5 SOL buy with eight buys behind it in its slot, then ten buyers who each spend exactly 1 SOL."""
    m = Market(store, addr(f"rs-coin-{k}"), addr(f"rs-dev-{k}"), ts, SLOT0 + k * 10_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"rs-leader-{k}"), 2.5, 3)
    for j in range(8):
        m.buy(addr(f"rs-crowd-{k}-{j}"), 0.3, 3)
    for j in range(10):
        m.buy(addr(f"rs-late-{k}-{j}"), 1.0, 8 + j, ix="buy_exact_quote_in")
    m.commit()
    return m.mint


def _freeze(store: Store, buy_sol: float) -> dict:
    s = {"id": paper.strategy_id(COPY.__dict__, EXIT.to_dict(), "+6", 0, buy_sol), "label": f"copy at {buy_sol} SOL",
         "trigger": COPY.__dict__, "exit": EXIT.to_dict(), "position": "+6", "latency": 0, "buy_sol": buy_sol,
         "per_tx_sol": 0.0035, "from_ts": T0, "lab": {"mean": 0.1, "ev_lower": 0.05}}  # fmt: skip
    have = store.coverage("paper_strategies").get("strategies", [])
    store.set_coverage("paper_strategies", {"strategies": have + [s]})
    return s


def _world() -> tuple[Store, dict, dict]:
    store = Store(":memory:")
    for k in range(3):
        _coin(store, k, T0 + 600 + k * 300)
        store.set_label(addr(f"rs-leader-{k}"), f"leader-{k}", kind="kol")
    store.set_coverage("tape", {"spans": [[T0, NOW]]})
    return store, _freeze(store, 5.0), _freeze(store, 0.2)


def _old_model(monkeypatch):
    """The simulator before 11 Nov 2026: the pool short of exactly what we hold, whatever the later buyers did."""

    def held_out(self, start, held, until=None, own_sells=()):
        return np.full(len(self.q) - start, float(held))

    monkeypatch.setattr(MarketPath, "displacement", held_out)


def _rows(store: Store, sid: str) -> dict[str, dict]:
    return {r["mint"]: r for r in store.paper_trades(sid)}


def _as_before_the_fix(store: Store) -> None:
    store.conn.execute("UPDATE paper_trades SET data=json_remove(data, '$.model')")
    store.conn.commit()


def test_a_run_writes_which_model_priced_each_trade():
    store, big, _small = _world()
    paper.run(store, Config(), now=NOW)
    rows = _rows(store, big["id"])
    assert len(rows) == 3 and all(r["data"]["model"] == FILL_MODEL for r in rows.values())


def test_kept_trades_of_a_size_rule_are_priced_again_and_a_small_rule_is_left_alone(monkeypatch):
    store, big, small = _world()
    with monkeypatch.context() as mp:
        _old_model(mp)
        paper.run(store, Config(), now=NOW)
    _as_before_the_fix(store)
    old_big, old_small = _rows(store, big["id"]), _rows(store, small["id"])
    assert len(old_big) == 3 and len(old_small) == 3

    fresh, big2, _ = _world()  # the same market scored from the start by today's simulator
    paper.run(fresh, Config(), now=NOW)
    want = _rows(fresh, big2["id"])

    out = paper.rescore(store, Config(), now=NOW)
    new_big = _rows(store, big["id"])
    for mint, row in new_big.items():
        assert row["pnl"] == pytest.approx(want[mint]["pnl"], rel=1e-12) and row["pnl"] < old_big[mint]["pnl"]
        assert row["roi"] == pytest.approx(want[mint]["roi"], rel=1e-12)
        assert row["entry_ts"] == old_big[mint]["entry_ts"] and row["trigger_ts"] == old_big[mint]["trigger_ts"]
        assert row["data"]["model"] == FILL_MODEL and row["data"]["pnl_old"] == old_big[mint]["pnl"]
        assert row["data"]["behind"] == old_big[mint]["data"]["behind"]
    assert _rows(store, small["id"]) == old_small  # under 0.5 SOL: not asked for, not touched
    (one,) = out["strategies"]
    assert one["id"] == big["id"] and one["rows"] == 3 and one["rescored"] == 3 and one["left"] == {}
    assert one["total_before"] == pytest.approx(sum(r["pnl"] for r in old_big.values()))
    assert one["total_after"] == pytest.approx(sum(r["pnl"] for r in new_big.values()))
    assert one["total_after"] < one["total_before"]
    # the report's kept numbers follow the record
    rep = {s["id"]: s for s in paper.report(store, Config(), now=NOW)["strategies"]}
    assert rep[big["id"]]["total"] == pytest.approx(one["total_after"]) and rep[big["id"]]["n"] == 3
    # again: nothing left to do
    again = paper.rescore(store, Config(), now=NOW)
    assert again["strategies"][0]["rescored"] == 0 and _rows(store, big["id"]) == new_big
    # a small rule is priced again when it is named
    named = paper.rescore(store, Config(), ids=[small["id"]], now=NOW)
    assert [s["id"] for s in named["strategies"]] == [small["id"]] and named["strategies"][0]["rescored"] == 3
    assert all(r["data"]["model"] == FILL_MODEL for r in _rows(store, small["id"]).values())


def test_a_trade_whose_moment_the_tape_no_longer_gives_is_left_and_counted(monkeypatch):
    store, big, _small = _world()
    with monkeypatch.context() as mp:
        _old_model(mp)
        paper.run(store, Config(), now=NOW)
    _as_before_the_fix(store)
    before = _rows(store, big["id"])
    store.conn.execute("DELETE FROM labels WHERE address=?", (addr("rs-leader-1"),))  # no longer a followed wallet
    store.conn.commit()
    gone = addr("rs-coin-2")
    store.conn.execute("DELETE FROM trades WHERE mint=?", (gone,))  # the tape has forgotten this coin
    store.conn.commit()
    out = paper.rescore(store, Config(), now=NOW)
    (one,) = out["strategies"]
    assert one["rows"] == 3 and one["rescored"] == 1 and sum(one["left"].values()) == 2
    after = _rows(store, big["id"])
    assert after[addr("rs-coin-1")] == before[addr("rs-coin-1")] and after[gone] == before[gone]
    assert after[addr("rs-coin-0")]["data"]["model"] == FILL_MODEL
    assert set(out["why"]) == set(one["left"]) and all(isinstance(v, str) and v for v in out["why"].values())


def test_rescore_is_a_paper_command():
    a = build_parser().parse_args(["paper", "rescore", "--id", "abc", "--id", "def", "--json"])
    assert a.action == "rescore" and a.id == ["abc", "def"]
    b = build_parser().parse_args(["paper", "rescore", "--min-buy", "1"])
    assert b.min_buy == 1.0 and not b.id
    assert json.dumps(paper.rescore(Store(":memory:"), Config(), now=NOW)) is not None  # nothing frozen: says so


def test_the_re_score_reads_in_plain_words_and_is_not_an_agents_job(tmp_path):
    from snipetech.operator.jobs import OperatorJobs
    from snipetech.report import render_paper

    text = render_paper("rescore", {
        "model": FILL_MODEL, "why": {"moment": "the rule no longer fires at that moment"},
        "strategies": [{"id": "b17196e12a", "label": "KOL copy, 5 SOL", "buy_sol": 5.0, "rows": 460, "rescored": 452,
                        "left": {"moment": 8}, "total_before": 22.035, "total_after": 3.5,
                        "mean_before": 0.0479, "mean_after": 0.0076}]})  # fmt: skip
    assert "b17196e12a" in text and "460 trades" in text and "452 priced again" in text
    assert "+22.035 -> +3.500 SOL" in text and "8 the rule no longer fires at that moment" in text
    assert render_paper("rescore", {"strategies": [], "model": FILL_MODEL, "note": "no frozen rule to price again"})
    jobs = OperatorJobs(repo=tmp_path, data=tmp_path, db=tmp_path / "x.db", branch="main")
    with pytest.raises(PermissionError, match="rescore"):  # it rewrites kept records: the cloud session's, by hand
        jobs.start(["paper", "rescore"])
