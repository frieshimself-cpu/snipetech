"""The lab's findings of the audit of 22 Nov 2026 (area "lab"), each pinned where it was fixed.

lab#0: a lab with --each and "repeats": "also" gives several moments of one coin, seconds apart and sold by the same
exit; their results move together, and the lower bound the agents hold to "above 0" drew them as if each were a coin
of its own (120 moments in 40 coins: a bound twice too tight). Every bound of the lab's tables now draws a coin's
moments together (`stats.bootstrap_mean_bounds` with `clusters`), and the ranked row says how many coins its newer
moments are (`test.coins`).

docs#0: a wallet rule that takes every buy of its wallet ("repeats": "also", what a live copy sniper copies) fires once
a coin without --each, at the first buy at the size, and the forward test holds one trade a coin: 17 records labelled
"every buy" held the first buy alone. A lab, a place-bids table and a freeze of such a rule without --each are refused
in words, before any tape is read and before a job starts; the records frozen so read as research only.
"""

from __future__ import annotations

import json
import random
import time

import numpy as np
import pytest

from snipetech.analysis.lab import (
    bands,
    choose_wallet_exit,
    collect,
    each_groups,
    exit_scores,
    parse_trigger,
    rank,
    rank_wallets,
)
from snipetech.analysis.stats import bootstrap_mean_bounds
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
LEAD = addr("audit-lab-lead")
HOLD = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=30.0, sell_on_dev_sell=False)
REPEATS = {"kind": "kol", "group": "wallet", "sol": 0.9, "repeats": "also", "only": LEAD}


def _repeat_world(st: Store, coins: int = 40, seed: int = 3) -> None:
    """`coins` coins the followed wallet buys three times within two seconds: three moments a coin. On about half of
    them buyers come behind (the coin runs), on the rest the early holders sell into the copies."""
    rnd = random.Random(seed)
    st.set_label(LEAD, "tracked one", "wallet", "file")
    for k in range(coins):
        m = Market(st, addr(f"al-coin-{k}"), addr(f"al-dev-{k}"), NOW - 9000 + k * 200, 95_000_000 + k * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        for j in range(4):
            m.buy(addr(f"al-early-{k}-{j}"), 0.5, 2 + j)
        for r in range(3):
            m.buy(LEAD, 1.0, 20 + 2 * r)
        if rnd.random() < 0.55:
            for j in range(10):
                m.buy(addr(f"al-fan-{k}-{j}"), 0.6, 30 + 3 * j)
        else:
            for j in range(4):
                m.sell(addr(f"al-early-{k}-{j}"), 1.0, 30 + 3 * j)
        m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 9500, NOW]]})


def _repeat_lab():
    st = Store(":memory:")
    _repeat_world(st)
    cfg = Config()
    trig = parse_trigger(REPEATS)
    data = collect(st, cfg, 0.2, 0, "+0", 0.0045, triggers=[trig], kols={"wallet": {LEAD}}, exits=[HOLD], mates={},
                   workers=1, groups=each_groups([trig]))  # fmt: skip
    st.close()
    return cfg, data, sorted(data["rows"], key=lambda r: r["ts"])


def _bounds(rows, cfg, clustered=True):
    pnl = [float(r["pnl"][0]) for r in rows]
    keys = [r["mint"] for r in rows] if clustered else None
    return bootstrap_mean_bounds(pnl, cfg.verdict.confidence, cfg.verdict.bootstrap_samples, clusters=keys)


def test_the_ranked_bound_draws_a_coins_moments_together():
    cfg, data, rows = _repeat_lab()
    assert len(rows) == 120 and len({r["mint"] for r in rows}) == 40
    (row,) = rank(data, cfg, top=5)
    newer = rows[int(len(rows) * 0.6) :]
    test = row["test"]
    assert test["n"] == len(newer) and test["coins"] == len({r["mint"] for r in newer}) < test["n"]
    lo, _hi = _bounds(newer, cfg)
    each_lo, _ = _bounds(newer, cfg, clustered=False)
    assert test["ev_lower"] == lo
    # the moments drawn one by one: the bound was 1.5 to 2 times too close to the mean
    assert test["mean"] - lo > 1.4 * (test["mean"] - each_lo)


def test_named_exits_and_bands_draw_a_coins_moments_together():
    cfg, data, rows = _repeat_lab()
    (scores,) = exit_scores(data, cfg, HOLD)
    newer = rows[int(len(rows) * 0.6) :]
    assert scores["all"]["ev_lower"] == _bounds(rows, cfg)[0]
    assert scores["newer"]["ev_lower"] == _bounds(newer, cfg)[0]
    assert scores["all"]["coins"] == 40 and scores["newer"]["coins"] == len({r["mint"] for r in newer})
    split = bands(rows, 0, cfg.verdict.confidence, cfg.verdict.bootstrap_samples)
    checked = 0
    for band in split["mcap_sol"]:
        if band.get("ev_lower") is None:
            continue
        mine = [r for r in rows if band["band"] == _mcap_band_name(r["mcap_sol"])]
        assert band["ev_lower"] == _bounds(mine, cfg)[0]
        checked += 1
    assert checked


def _mcap_band_name(mcap: float) -> str:
    from snipetech.analysis.lab import MCAP_BAND_NAMES, _mcap_band

    return MCAP_BAND_NAMES[_mcap_band(float(mcap))]


def test_a_wallets_score_draws_its_moments_of_one_coin_together():
    cfg, data, rows = _repeat_lab()
    (row,) = [w for w in rank_wallets(data, cfg, {}, top=5) if w["wallet"] == LEAD]
    assert row["n"] == 120
    assert row["ev_lower"] == _bounds(rows, cfg)[0] < _bounds(rows, cfg, clustered=False)[0]


def test_a_wallets_own_exit_is_judged_on_its_coins():
    """The gain of a wallet's own exit over the gate, on its newer moments: drawn by coin when the moments say which
    coin each was."""
    rng = np.random.default_rng(4)
    coins = np.repeat(np.arange(20), 3)
    gain = np.repeat(rng.normal(0.01, 0.05, 20), 3) + rng.normal(0.0, 0.005, 60)
    pnl = np.stack([np.zeros(60), gain], axis=1)
    keys = [f"c{c}" for c in coins]
    one = choose_wallet_exit(pnl, 0, None, conf=0.8, samples=2000)
    by_coin = choose_wallet_exit(pnl, 0, None, conf=0.8, samples=2000, clusters=keys)
    cut = int(60 * 0.6)
    assert by_coin["lower"] == bootstrap_mean_bounds(gain[cut:], 0.8, 2000, clusters=keys[cut:])[0]
    assert by_coin["lower"] < one["lower"] and by_coin["gain"] == one["gain"]


# -- docs#0: a rule that takes every buy is scored with --each, never once a coin ------------------------------------

REPEATS_JSON = json.dumps(REPEATS)


def test_a_lab_without_each_refuses_a_rule_that_takes_every_buy(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    st = Store(db)
    _repeat_world(st, coins=25)
    st.close()
    base = ["--db", str(db), "lab", "--trigger", REPEATS_JSON, "--buy", "0.2", "--per-tx", "0.0045",
            "--copy-position", "+0", "--exit", "hold30s", "--json"]  # fmt: skip
    assert cli.main(base) == 2
    err = capsys.readouterr().err
    assert "--each" in err and "once a coin" in err
    assert cli.main([*base, "--each"]) == 0  # every buy at the size, as the copy sniper takes them
    out = json.loads(capsys.readouterr().out)
    assert out["each"] is True and out["moments"] == 75


def test_place_bids_without_each_refuses_a_rule_that_takes_every_buy():
    from snipetech.analysis.place_bids import place_bids

    st = Store(":memory:")
    _repeat_world(st, coins=6)
    kw = {"hours": 48, "kols": {"wallet": {LEAD}}, "labels": {}, "own": ()}
    with pytest.raises(ValueError, match="--each"):
        place_bids(st, Config(), NOW, trigger=REPEATS, **kw)
    assert place_bids(st, Config(), NOW, trigger=REPEATS, each=True, **kw)["moments"] == 18
    st.close()


def test_the_job_runner_refuses_it_before_a_job_starts():
    from snipetech.operator.jobs import OperatorJobs

    refused = (
        ["lab", "--trigger", REPEATS_JSON],
        ["lab", f"--trigger={REPEATS_JSON}", "--hours", "24"],
        ["place-bids", "--trigger", REPEATS_JSON],
        ["paper", "freeze", "--trigger", REPEATS_JSON],
    )
    for argv in refused:
        with pytest.raises(PermissionError, match="no job started.*--each"):
            OperatorJobs._check_triggers(argv)
    OperatorJobs._check_triggers(["lab", "--each", "--trigger", REPEATS_JSON])  # what a copy sniper takes: scored
    OperatorJobs._check_triggers(["place-bids", "--each", "--trigger", REPEATS_JSON])
    OperatorJobs._check_triggers(["lab", "--trigger", json.dumps(REPEATS | {"repeats": ""})])  # first buys: as ever


def test_paper_freeze_refuses_a_rule_that_takes_every_buy(monkeypatch):
    from snipetech.analysis import paper

    def no_tape(*_a, **_k):
        raise AssertionError("the refusal comes before any tape is read")

    monkeypatch.setattr(paper, "collect", no_tape)
    st = Store(":memory:")
    with pytest.raises(ValueError, match="one trade a coin.*lab --each"):
        paper.freeze(st, Config(), triggers=[parse_trigger(REPEATS)], now=NOW)
    st.close()


def _record(sid: str, repeats: str) -> dict:
    from snipetech.analysis.lab import kind_exits

    ex = kind_exits([HOLD], Config())["kol"][0]  # watched, with the copy exit: the copy sniper's own sale
    trig = parse_trigger(REPEATS | {"repeats": repeats})
    return {"id": sid, "label": f"{trig.label()} | {ex.label()}", "trigger": dict(trig.__dict__), "exit": ex.to_dict(),
            "position": "+2x", "latency": 0, "buy_sol": 1.0, "per_tx_sol": 0.0045, "from_ts": NOW - 3600,
            "lab": {"mean": 0.01, "ev_lower": 0.004, "landing": "+2x@0"}}  # fmt: skip


def test_a_record_frozen_with_repeats_is_research_only():
    from snipetech import dash
    from snipetech.analysis import paper

    every, first = _record("rep1", "also"), _record("first1", "")
    assert paper.exit_not_as_live(every) is None  # its exit is the copy sniper's: the trades are what is not
    why = paper.not_as_live(every)
    assert "one trade a coin" in why and "every buy" in why and "lab --each" in why
    assert paper.not_as_live(first) is None
    st = Store(":memory:")
    st.set_coverage("paper_strategies", {"strategies": [every, first], "superseded": []})
    rep = paper.report(st, Config(), NOW)
    rows = {r["id"]: r for r in rep["strategies"]}
    assert rows["rep1"]["research_only"] is True and rows["rep1"]["exit_note"] == paper.REPEATS_NOTE
    assert "research_only" not in rows["first1"] and "exit_note" not in rows["first1"]
    assert "every buy" in rep["research_only_why"][paper.REPEATS_NOTE]
    assert dash._research_only([every, first]) == {"rep1"}
    st.close()


# -- lab#2: SOL a day over the newer moments' whole window, the taped hours of it -------------------------------------

DAY = 86400


def _spd_coin(st: Store, k: int, ts: int) -> None:
    m = Market(st, addr(f"spd-coin-{k}"), addr(f"spd-dev-{k}"), ts, 90_000_000 + k * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(LEAD, 2.0, 20)
    for j in range(8):
        m.buy(addr(f"spd-fan-{k}-{j}"), 0.5, 40 + 3 * j)
    m.commit()


def test_sol_a_day_counts_the_test_window_to_its_end_not_to_the_last_moment():
    """12 older moments over five days, then 8 newer ones within an hour, then quiet to the end of the tape, with half
    a day the tape did not record. Divided by the time to the last moment, the 8 trades read as a pace kept for days
    (59 times the window's own rate on the auditor's probe)."""
    st = Store(":memory:")
    st.set_label(LEAD, "tracked one", "wallet", "file")
    start = NOW - 7 * DAY
    for k in range(12):
        _spd_coin(st, k, start + 3600 + k * int(5 * DAY / 12))
    burst = start + 5 * DAY + 7200
    for k in range(12, 20):
        _spd_coin(st, k, burst + (k - 12) * 400)
    gap = (burst + 6 * 3600, burst + 18 * 3600)  # twelve hours the recorder was down
    st.set_coverage("tape", {"spans": [[start, gap[0]], [gap[1], NOW]]})
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    trig = parse_trigger({"kind": "kol", "group": "wallet", "sol": 1, "only": LEAD})
    data = collect(st, cfg, 0.2, 0, "+0", 0.0045, triggers=[trig], kols={"wallet": {LEAD}}, exits=[HOLD], mates={},
                   workers=1)  # fmt: skip
    st.close()
    (r,) = rank(data, cfg, top=5, min_train=5)
    assert r["test"]["n"] == 8 and data["window"]["until_ts"] == NOW
    taped = (gap[0] - r["test_from_ts"]) + (NOW - gap[1])  # seconds of tape from the first newer moment to the end
    assert r["test_sol_per_day"] == pytest.approx(r["test"]["total"] / (taped / DAY), rel=1e-9)
    burst_rate = r["test"]["total"] / ((data["rows"][-1]["ts"] - r["test_from_ts"]) / DAY)
    assert abs(burst_rate) > 20 * abs(r["test_sol_per_day"])  # what it read before: the burst's pace, kept all along
