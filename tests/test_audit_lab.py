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


# -- lab#1: --each and a consensus gate ------------------------------------------------------------------------------
# --each gives every followed wallet's own buys, as its own copy sniper copies them (ctbotWallets [w], ctbotMinWallets
# 1); a consensus gate counts the other wallets in the coin, which only a sniper over many wallets does, once a coin.
# `_first_buys` applies no such gate (as documented), so the rule's label named a gate its moments never had.

W1, W2 = addr("al-w1"), addr("al-w2")
CONSENSUS = ({"known": 2}, {"gap": 5}, {"teams": True}, {"known": 2, "known_any": True})


def _two_wallets(st: Store, coins: int = 12) -> None:
    """W1 buys every coin first; W2 follows on half of them: the second known wallet in."""
    st.set_label(W1, "tracked one", "wallet", "file")
    st.set_label(W2, "tracked two", "wallet", "file")
    for k in range(coins):
        m = Market(st, addr(f"al2-coin-{k}"), addr(f"al2-dev-{k}"), NOW - 6000 + k * 200, 80_000_000 + k * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(W1, 2.0, 20)
        if k % 2 == 0:
            m.buy(W2, 2.0, 40)
        for j in range(6):
            m.buy(addr(f"al2-fan-{k}-{j}"), 0.5, 60 + 3 * j)
        m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 7000, NOW]]})


def test_each_refuses_a_rule_with_a_consensus_gate():
    for gate in CONSENSUS:
        trig = parse_trigger({"kind": "kol", "group": "wallet", "sol": 1} | gate)
        with pytest.raises(ValueError, match="--each") as exc:
            each_groups([trig])
        assert next(iter(gate)) in str(exc.value)  # the gate it would have dropped, by name
    assert each_groups([parse_trigger({"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3})]) == ("wallet",)


def test_place_bids_each_reads_every_first_buy_and_says_so():
    from snipetech.analysis.place_bids import DEFAULT_TRIGGER, place_bids

    st = Store(":memory:")
    _two_wallets(st)
    kw = {"hours": 48, "kols": {"wallet": {W1, W2}}, "labels": {}, "own": ()}
    each = place_bids(st, Config(), NOW, each=True, **kw)
    assert each["moments"] == 18 and "known" not in each["trigger"]  # every wallet's own first buy: 12 + 6
    assert "known wallets in" not in each["rule"] and "<= 3 SOL" in each["rule"]
    once = place_bids(st, Config(), NOW, **kw)  # the one-a-coin table keeps its rule: the second known wallet in
    assert once["trigger"] == DEFAULT_TRIGGER and once["moments"] == 6 and "known wallets in" in once["rule"]
    with pytest.raises(ValueError, match="--each"):
        place_bids(st, Config(), NOW, trigger=DEFAULT_TRIGGER, each=True, **kw)
    st.close()


def test_the_job_runner_refuses_each_with_a_consensus_gate():
    from snipetech.operator.jobs import OperatorJobs

    known = json.dumps({"kind": "kol", "group": "wallet", "sol": 1, "known": 2})
    for argv in (["lab", "--each", "--trigger", known], ["place-bids", "--each", "--trigger", known]):
        with pytest.raises(PermissionError, match="no job started.*--each"):
            OperatorJobs._check_triggers(argv)
    OperatorJobs._check_triggers(["lab", "--trigger", known])  # one sniper over the group: once a coin, as ever
    OperatorJobs._check_triggers(["place-bids", "--each"])  # the default rule, read wallet by wallet


# -- lab#4: the event edge reads the coins with an event, and an event fires in its own stretch of tape only --------


class _Counting:
    """The store, counting the coins whose trades the lab reads."""

    def __init__(self, store):
        self._s, self.read = store, []

    def trades_for_mint(self, mint, a=None, b=None):
        self.read.append(mint)
        return self._s.trades_for_mint(mint, a, b)

    def __getattr__(self, name):
        return getattr(self._s, name)


def _event(st: Store, kind: str, ts: int, slot: int, mint: str, tag: str) -> None:
    st._exec("INSERT INTO events (kind, ts, slot, mint, data, dedupe) VALUES (?,?,?,?,?,?)",
             (kind, ts, slot, mint, json.dumps({}), tag))  # fmt: skip


def test_the_event_edge_reads_only_the_coins_an_event_names():
    st = Store(":memory:")
    for k in range(60):
        m = Market(st, addr(f"ev-coin-{k}"), addr(f"ev-dev-{k}"), NOW - 6000 + k * 60, 99_000_000 + k * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        for j in range(8):
            m.buy(addr(f"ev-fan-{k}-{j}"), 0.5, 2 + 3 * j)
        m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 7000, NOW]]})
    for k in (3, 40):  # two coins carry an event of the kind the edge scores
        _event(st, "graduating", NOW - 6000 + k * 60 + 2, 99_000_000 + k * 1000 + 5, addr(f"ev-coin-{k}"), f"ev-{k}")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    seen = _Counting(st)
    data = collect(seen, cfg, 0.2, 1, "+3", 0.0045, groups=(), event_kinds=("graduating",), exits=[HOLD], workers=1)
    assert sorted(set(seen.read)) == sorted([addr("ev-coin-3"), addr("ev-coin-40")])  # 2 coins read, not 60
    assert {r["mint"] for r in data["rows"]} == {addr("ev-coin-3"), addr("ev-coin-40")}
    st.close()


def test_an_event_fires_in_its_own_stretch_of_tape_only():
    """A coin whose event lies in an earlier stretch of tape (or before the tape) and that trades on in a later one
    fired again there, at its first trade of that stretch: a moment hours after the event."""
    st = Store(":memory:")
    t0 = NOW - 20000
    m = Market(st, addr("st-coin"), addr("st-dev"), t0, 99_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(10):
        m.buy(addr(f"st-fan-{j}"), 0.5, 2 + 3 * j)
    for j in range(10):  # two hours later, after a hole in the tape: a second stretch
        m.buy(addr(f"st-late-{j}"), 0.3, 20000 + 3 * j)
    m.commit()
    st.set_coverage("tape", {"spans": [[t0 - 10, t0 + 1200], [t0 + 7000, NOW]]})
    _event(st, "graduating", t0 + 2, 99_000_000 + 5, addr("st-coin"), "st-1")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    data = collect(st, cfg, 0.2, 1, "+3", 0.0045, groups=(), event_kinds=("graduating",), exits=[HOLD], workers=1)
    assert [r["ts"] - t0 < 60 for r in data["rows"]] == [True]  # the one moment, a few seconds after the event
    st.close()


def test_an_event_that_names_no_coin_is_no_coin_of_the_plan():
    """The events table holds rows with no coin (its `mint` may be empty). Such an event fires on no coin; planned
    among the coins of its stretch (review of 22 Nov), sorting them in mint order stopped the whole event edge."""
    st = Store(":memory:")
    m = Market(st, addr("nm-coin"), addr("nm-dev"), NOW - 6000, 98_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(8):
        m.buy(addr(f"nm-fan-{j}"), 0.5, 2 + 3 * j)
    m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 7000, NOW]]})
    _event(st, "graduating", NOW - 6000 + 2, 98_000_000 + 5, addr("nm-coin"), "nm-1")
    _event(st, "graduating", NOW - 5000, 98_002_500, None, "nm-none")
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    data = collect(st, cfg, 0.2, 1, "+3", 0.0045, groups=(), event_kinds=("graduating",), exits=[HOLD], workers=1)
    st.close()
    assert [r["mint"] for r in data["rows"]] == [addr("nm-coin")]


# -- goal#2: a copy that reacts to the leader's landed buy, K block places after it ----------------------------------
# +0, +1x and the qualify landing +6 count trades, whenever they came; a reacting sender's place is set by time, which
# on the chain is how many block places after the leader its transaction lands. "react:K": behind every trade of the
# coin up to K places after the leader's own transaction, behind the whole block when K runs past its end.


def _block_path():
    """State 0: before; slot 100: the leader at block place 488 (state 1), a follower at 490 (state 2), another at 494
    (state 3); slot 101: the next buyer at place 7 (state 4). Places as on the tape of slot 471375858 (Decu at 488, a
    3.5 SOL buyer at 490, another at 494)."""
    from snipetech.sim.market import MarketPath

    p = MarketPath.__new__(MarketPath)
    p.slot = np.array([99, 100, 100, 100, 101])
    p.pos = np.array([-1, 488, 490, 494, 7])
    p.n = 5
    return p


def test_plus0_stands_ahead_of_the_follower_two_places_behind():
    assert int(_block_path().landing_index(100, "+0", 1)) == 1  # today's +0: the leader's own state


def test_react_k_counts_block_places_after_the_leader():
    p = _block_path()
    assert int(p.landing_index(100, "react:1", 1)) == 1  # place 489: nobody of the coin between
    assert int(p.landing_index(100, "react:2", 1)) == 2  # place 490: behind the follower
    assert int(p.landing_index(100, "react:15", 1)) == 3  # place 503: behind both followers
    assert int(p.landing_index(100, "react:5000", 1)) == 3  # past the block's end: behind its last trade
    # in a later block: behind its first K places (0 to K-1), so its buyer at place 7 is ahead from react:8 on
    assert int(p.landing_index(101, "react:8", 1)) == 4 and int(p.landing_index(101, "react:7", 1)) == 3


def test_paper_and_the_lab_take_react_k():
    from snipetech.analysis.paper import hypothesis_landing, landing_text

    assert hypothesis_landing("+6", "react:15") == (0, "react:15")
    for bad in ("react:", "react:x", "react:-1", "react15"):
        with pytest.raises(ValueError, match="react:K"):
            hypothesis_landing("+6", bad)
    assert "15 block places after" in landing_text(0, "react:15")


def test_a_copy_lands_k_block_places_after_the_leader_and_never_among_its_own():
    from snipetech.analysis.lab import entry_at
    from snipetech.sim.market import MarketPath

    st = Store(":memory:")
    m = Market(st, addr("rk-coin"), addr("rk-dev"), NOW - 3000, 70_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(3):
        m.buy(addr(f"rk-early-{j}"), 0.3, 2 + j)
    for who, sol, place in ((W1, 2.0, 488), (addr("rk-near"), 3.5, 490), (addr("rk-far"), 1.0, 494)):
        m.buy(who, sol, 10).pos = place
    m.buy(addr("rk-next"), 1.0, 11).pos = 7
    m.commit()
    path = MarketPath.build(st.trades_for_mint(addr("rk-coin")), st.launch(addr("rk-coin")))
    st.close()
    i = path.users.index(W1)

    def behind(position, mates=None):
        return path.users[entry_at("kol", path, i, int(path.slot[i]), 0, position, mates)]

    assert behind("+0") == W1  # right behind the leader, ahead of the follower two places behind
    assert behind("react:1") == W1 and behind("react:2") == addr("rk-near") and behind("react:6") == addr("rk-far")
    assert behind("react:5000") == addr("rk-far")  # the block's end, never the next block
    assert behind("react:1", frozenset({addr("rk-near")})) == addr("rk-near")  # never among the leader's own wallets


def test_a_lab_scores_copies_at_react_k():
    st = Store(":memory:")
    _two_wallets(st)
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    trig = parse_trigger({"kind": "kol", "group": "wallet", "sol": 1, "only": W1})
    data = collect(st, cfg, 0.2, 0, "react:15", 0.0045, triggers=[trig], kols={"wallet": {W1, W2}}, exits=[HOLD],
                   mates={}, workers=1)  # fmt: skip
    st.close()
    assert len(data["rows"]) == 12 and {r["landing"] for r in data["rows"]} == {"react:15@0"}


# -- lab#5: a position's exits read the path only as far as they can sell -------------------------------------------
# prepare() built every per-state array from the entry to the end of the coin's path and every exit compared and
# accumulated over all of it, though no exit holds past its timer: 637 ms a moment at 200,000 later trades against
# 11 ms at 2,000. The exits are handed the path cut a margin past the latest state they can sell at, every index the
# same (`sim.exits.held_path`); every result must be the very number the whole path gives.

LONG_EXITS = [
    ExitParams(take_profit=0.1, stop_loss=0.2, max_hold_secs=60, momentum_hold=2.0, momentum_lookback=3.0,
               momentum_max_secs=30.0, sell_on_dev_sell=False),
    ExitParams(take_profit=0, stop_loss=0.25, max_hold_secs=30, ext_velocity=0.5, ext_step=5.0, ext_max=30.0,
               ext_lookback=3.0, ext_cancel=0.1, sell_on_dev_sell=False),
    ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=120, sell_parts=4, sell_part_secs=15.0,
               sell_on_dev_sell=False),
    ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_parts=3, sell_part_secs=20.0, first_part=0.5,
               flat_below=0.95, sell_on_dev_sell=False),
    ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, tp_stages=((0.2, 0.25), (0.5, 0.25)),
               sl_stages=((0.1, 0.3),), trailing_stop=0.2, sell_on_dev_sell=False),
    ExitParams(take_profit=0, stop_loss=0, max_hold_secs=290, stale_secs=5.0, sell_on_dev_sell=False),
]  # fmt: skip


def _long_tail(st: Store, k: int, migrate: bool = False, quiet: bool = False) -> tuple[str, object]:
    """A coin the followed wallet buys, then trades for an hour: buys of an exact amount of SOL (which the replay walks
    beside a position) and of tokens, sales by those buyers, the followed wallet selling half; with `quiet` nothing
    trades for eight minutes from just before the longest exit's timer; with `migrate` the curve completes and the
    coin trades on PumpSwap from four minutes in."""
    rnd = random.Random(100 + k)
    mint = addr(f"lt-coin-{k}")
    m = Market(st, mint, addr(f"lt-dev-{k}"), NOW - 40000 + k * 5000, 30_000_000 + k * 100_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(6):
        m.buy(addr(f"lt-early-{k}-{j}"), 0.4, 2 + j)
    m.buy(LEAD, 1.5, 20)
    slot, held = 21, []

    def busy(until: int, step: int) -> None:
        nonlocal slot
        while slot < until:
            if rnd.random() < 0.6 or not held:
                who = addr(f"lt-{k}-{slot}")
                m.buy(who, rnd.uniform(0.05, 0.6), slot, ix="buy_exact_sol_in" if rnd.random() < 0.5 else None)
                held.append(who)
            else:
                m.sell(rnd.choice(held), rnd.choice((0.5, 1.0)), slot)
            slot += rnd.randint(1, step)

    busy(120, 3)
    m.sell(LEAD, 0.5, slot)
    slot += 2
    busy(600, 4)
    if migrate:
        m.complete(addr(f"lt-done-{k}"), slot)
        m.migrate(slot + 3)
        slot += 5
    if quiet:
        slot += 1200
    busy(slot + 9000, 3)
    m.commit()
    return mint, m.migration


def test_the_exits_read_the_path_only_as_far_as_they_can_sell_and_score_the_same():
    from snipetech.analysis.lab import leader_sell_fractions
    from snipetech.sim.exits import Costs, exits_reach, held_path, prepare, run_exit
    from snipetech.sim.market import MarketPath

    st = Store(":memory:")
    coins = [_long_tail(st, 0), _long_tail(st, 1, quiet=True), _long_tail(st, 2, migrate=True),
             _long_tail(st, 3, migrate=True, quiet=True)]  # fmt: skip
    exits = [*exit_set_and_copy(), *LONG_EXITS]
    reach = exits_reach(exits)
    costs = Costs(per_tx_lamports=0.0045e9, entry_bid_lamports=0.01e9)
    checked = cut = 0
    for mint, mig in coins:
        path = MarketPath.build(st.trades_for_mint(mint), st.launch(mint))
        frac = leader_sell_fractions(path, LEAD)
        lead = path.users.index(LEAD)
        for e in (lead + 2, lead + 40, path.n // 3):  # behind the leader, later on, deep in the hour
            held = held_path(path, e, reach)
            cut += held.n < path.n
            for sol in (0.2, 5.0):
                kw = {"leader_frac": frac, "migration_slot": None if mig is None else mig.slot}
                whole = prepare(path, e, sol, costs, 1, "start", **kw)
                part = prepare(held, e, sol, costs, 1, "start", **kw | {"leader_frac": frac[: held.n]})
                for ex in exits:
                    a, b = run_exit(whole, ex, costs), run_exit(part, ex, costs)
                    assert (a.pnl_sol, a.exit_reason, a.exit_idx, a.hold_secs, a.sells, a.sold_on_curve, a.fallback,
                            a.past_curve, a.peak_multiple, a.trough_multiple) == (
                        b.pnl_sol, b.exit_reason, b.exit_idx, b.hold_secs, b.sells, b.sold_on_curve, b.fallback,
                        b.past_curve, b.peak_multiple, b.trough_multiple), (mint, e, sol, ex.label())  # fmt: skip
                    checked += 1
    st.close()
    assert cut == 12 and checked == 12 * 2 * len(exits)  # every position was handed a shorter path


def exit_set_and_copy():
    """The lab's search set, and the same with OKTech's watch delay and the copy exit, as a wallet rule scores it."""
    from snipetech.analysis.lab import exit_set, kind_exits

    exits = exit_set()
    return [*exits, *kind_exits(exits, Config())["kol"]]


def test_a_lab_on_long_tailed_coins_is_the_same_with_the_cut(monkeypatch):
    from snipetech.analysis import lab as L

    st = Store(":memory:")
    for k, (mig, quiet) in enumerate(((False, False), (False, True), (True, False))):
        _long_tail(st, k, migrate=mig, quiet=quiet)
    st.set_label(LEAD, "tracked one", "wallet", "file")
    st.set_coverage("tape", {"spans": [[NOW - 50000, NOW]]})
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    trig = parse_trigger({"kind": "kol", "group": "wallet", "sol": 1, "only": LEAD})

    def lab():
        return collect(st, cfg, 2.0, 0, "+0", 0.0045, triggers=[trig], kols={"wallet": {LEAD}}, mates={}, workers=1)

    sizes = []
    real = L.held_path
    monkeypatch.setattr(L, "held_path", lambda path, e, secs: sizes.append(real(path, e, secs).n - path.n) or real(
        path, e, secs))  # fmt: skip
    cut = lab()
    monkeypatch.setattr(L, "held_path", lambda path, e, secs: path)
    whole = lab()
    st.close()
    # (the migrated coin's moment is left out whole: this store has no PumpSwap tape of its migration)
    assert len(cut["rows"]) == 2 and sizes and all(d < 0 for d in sizes)  # each moment had a shorter path
    assert cut["rows"] == whole["rows"]


def test_a_dead_exit_is_handed_the_path_its_sale_lands_on():
    """A dead exit fires on the last trade before its timer once nothing trades for its pause, and its sale lands that
    long after that trade: "STALE158s hold10s" sells 158 s after its timer's last trade, past the timer and the margin
    the cut kept (review of 22 Nov). Read on the cut path, the sale's landing against the curve missed the coin's
    completion two slots after the quiet stretch. The measured slot time here is 2.5 % short of the 0.4 s these blocks
    ran at: the pause is read on the block clock, its sale's slot on the slot clock."""
    from snipetech.sim.exits import Costs, exits_reach, held_path, prepare, run_exit
    from snipetech.sim.market import MarketPath

    st = Store(":memory:")
    mint = addr("dead-exit-coin")
    m = Market(st, mint, addr("dead-exit-dev"), NOW - 9000, 41_000_000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(LEAD, 1.5, 20)
    for s in range(22, 42, 2):  # a buy every 0.8 s until 8 s after the copy
        m.buy(addr(f"dead-exit-{s}"), 0.3, s)
    m.buy(addr("dead-exit-wake"), 0.3, 441)  # 160 s of nothing, then the coin wakes up
    m.complete(addr("dead-exit-done"), 443)  # and completes its curve two slots later
    m.migrate(445, on_tape=False)  # (no migration on the tape: the sale is placed by the curve's end alone)
    for s in range(446, 2400, 4):
        m.buy(addr(f"dead-exit-late-{s}"), 0.2, s)
    m.commit()
    path = MarketPath.build(st.trades_for_mint(mint), st.launch(mint), slot_secs=0.39)
    st.close()
    e = path.users.index(LEAD) + 1
    ex = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10.0, stale_secs=158.0, sell_on_dev_sell=False)
    costs = Costs(per_tx_lamports=0.0045e9)
    held = held_path(path, e, exits_reach([ex]))
    a = run_exit(prepare(path, e, 0.2, costs, 1, "start"), ex, costs)
    b = run_exit(prepare(held, e, 0.2, costs, 1, "start"), ex, costs)
    assert a.exit_reason == "stale" and a.sold_on_curve is False  # it landed after the curve completed
    assert held.n < path.n  # still a cut: the hour after it is not read
    assert (b.pnl_sol, b.exit_reason, b.exit_idx, b.exit_slot, b.sold_on_curve) == (
        a.pnl_sol, a.exit_reason, a.exit_idx, a.exit_slot, a.sold_on_curve)  # fmt: skip
