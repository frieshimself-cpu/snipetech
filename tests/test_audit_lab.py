"""The lab's findings of the audit of 22 Nov 2026 (area "lab"), each pinned where it was fixed.

lab#0: a lab with --each and "repeats": "also" gives several moments of one coin, seconds apart and sold by the same
exit; their results move together, and the lower bound the agents hold to "above 0" drew them as if each were a coin
of its own (120 moments in 40 coins: a bound twice too tight). Every bound of the lab's tables now draws a coin's
moments together (`stats.bootstrap_mean_bounds` with `clusters`), and the ranked row says how many coins its newer
moments are (`test.coins`).
"""

from __future__ import annotations

import random
import time

import numpy as np

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
