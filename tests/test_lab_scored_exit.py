"""A wallet row and a recipe's score name the exit their numbers were made with (12 Nov 2026).

Since 12 Nov every exit on a wallet rule waits OKTech's watch delay in the lab (`lab.kind_exits`: a copy sniper's
auto-sell starts watching 1.3 s after the buy). The ranking (`lab.rank`) named the exit with the delay, but the
per-wallet rows (`lab --by-wallet`, `lab.rank_wallets`) and a recipe's own scores (`lab --recipe`, `lab.exit_scores`)
still named the exit as the lab was handed it: "TP+5% SL-30% hold30s" with watch_secs 0, for numbers made with the
exit acting only from 1.3 s on. Someone who froze or replayed the exit as the row named it ran another exit than the
one behind the numbers, one whose take-profit can fire inside the followed wallet's own block. The numbers were right.

The qualify lab hands the lab the menu's exits, which carry the delay already: its rows do not change."""

import pytest

from snipetech.analysis import lab as L
from snipetech.analysis.lab import WATCH_SECS, collect, exit_scores, parse_trigger, rank, rank_wallets, watched
from snipetech.config import Config
from snipetech.sim.exits import ExitParams

from .test_bid_landing import LEAD, LEAD2, RULE, _coin, _store

# two exits of a search, as a lab is handed them: neither waits
TP = ExitParams(take_profit=0.05, stop_loss=0.3, max_hold_secs=30.0, sell_on_dev_sell=False)
HOLD = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=30.0, sell_on_dev_sell=False)
MOMENTUM = {"kind": "momentum", "sol": 1}  # no live sniper times such a rule: its exits act from the buy


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _world(leaders=(LEAD,), coins: int = 24):
    st = _store()
    for k in range(coins):
        _coin(st, leader=leaders[k % len(leaders)])
    return st


def test_a_wallet_row_names_the_exit_its_numbers_were_made_with():
    st = _world()
    data = collect(st, _cfg(), 0.2, 0, "+0", triggers=[parse_trigger(RULE)], mates={}, exits=[TP], workers=1)
    assert data["exits"] == [TP] and data["kind_exits"]["kol"] == [watched(TP)]  # the columns, and what filled them
    (strategy,) = rank(data, _cfg())
    (w,) = rank_wallets(data, _cfg(), {}, floor=0.1)
    assert w["wallet"] == LEAD and w["n"] == 24
    assert w["exit"] == strategy["exit"] == f"{TP.label()} watch{WATCH_SECS:g}s"
    assert w["exit_params"] == strategy["exit_params"] == TP.to_dict() | {"watch_secs": WATCH_SECS}
    assert w["mean"] == pytest.approx(sum(r["pnl"][0] for r in data["rows"]) / 24)  # the same numbers as before
    st.close()


def test_a_wallet_row_judged_on_a_gate_names_the_gate_as_it_was_scored():
    """The gate is looked up among the exits the lab was handed (the columns); the row names it with the delay its
    column was filled with, and so does the exit the wallet's sniper is told to run."""
    st = _world()
    data = collect(st, _cfg(), 0.2, 0, "+0", triggers=[parse_trigger(RULE)], mates={}, exits=[HOLD, TP], workers=1)
    (w,) = rank_wallets(data, _cfg(), {}, floor=0.1, gate=TP)
    assert w["exit"] == watched(TP).label() and w["exit_params"] == watched(TP).to_dict()
    assert w["exit_source"] == "lab" and w["exit_name"] is None  # no exit of the menu
    assert w["mean"] == pytest.approx(sum(r["pnl"][1] for r in data["rows"]) / 24)  # the gate's column
    st.close()


def test_a_recipes_score_names_the_exit_each_trigger_was_scored_with():
    st = _world()
    triggers = [parse_trigger(RULE), parse_trigger(MOMENTUM)]
    data = collect(st, _cfg(), 0.2, 0, "+0", triggers=triggers, mates={}, exits=[HOLD, TP], workers=1)
    wallet, momentum = exit_scores(data, _cfg(), TP)
    assert wallet["moments"] == 24 and wallet["all"]["n"] == 24
    assert wallet["exit"] == f"{TP.label()} watch{WATCH_SECS:g}s"  # a wallet rule: the exit waited OKTech's delay
    assert wallet["exit_params"] == TP.to_dict() | {"watch_secs": WATCH_SECS}
    assert wallet["all"]["mean"] == pytest.approx(sum(r["pnl"][1] for r in data["rows"] if r["trigger"] == 0) / 24)
    assert momentum["exit"] == TP.label() and momentum["exit_params"] == TP.to_dict()  # as given: nothing waited
    st.close()


def test_the_qualify_labs_rows_are_what_they_were():
    """The qualify stage scores each wallet on the menu's exits, read the way the live sniper runs them: they carry
    the watch delay already, so the exits a wallet rule is scored with are the ones the lab was handed, and every
    row names what it named before (the book reads `exit_name` and `exit_params` to build the wallet's sniper)."""
    cfg = _cfg()
    st = _world((LEAD, LEAD2), 32)
    menu = L.menu_exits(cfg)
    kols = L.label_groups(st)
    kols["cand"] = {LEAD, LEAD2}
    data = collect(st, cfg, cfg.book.fill_min_sol, 0, "+0", per_tx_sol=0.0045, groups=("cand",),
                   exits=list(menu.values()), kols=kols, mates={}, workers=1)  # fmt: skip
    assert data["exits"] == list(menu.values()) == data["kind_exits"]["kol"]

    def rows(d):
        return rank_wallets(d, cfg, st.labels(), top=2, min_n=4, wallets=[LEAD, LEAD2], gate=menu["lab"],
                            with_moments=True)  # fmt: skip

    out = rows(data)
    assert {r["wallet"] for r in out} == {LEAD, LEAD2} and {r["n"] for r in out} == {16}
    assert out == rows(data | {"kind_exits": {}})  # named from the exits the lab was handed, as before
    for r in out:
        assert r["exit"] == menu["lab"].label() and r["exit"].endswith(f"watch{WATCH_SECS:g}s")
        assert r["exit_name"] in menu and r["exit_params"] == menu[r["exit_name"]].to_dict()
        assert r["exit_params"]["watch_secs"] == WATCH_SECS and len(r["moments"]) == 16
    st.close()
