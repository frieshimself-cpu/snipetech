"""The live copy sniper's dead time after each send (23 Nov 2026, the 0.2 SOL test).

The lab copies every buy of the wallet at the size. The live sniper buys once a start (`--snipes 1`) and the runner
arms it again some seconds after its sale or its refusal, so the wallet's buys inside that time go uncopied: after fill
3 (14:39:30) Yugi's sniper came back at 14:40:12, the moment of his next buy, which the paper took (-0.046) and the live
sniper missed. Offline on the ceiling labs of 08:05 the gap took the newer week's three wallets from +6.91 to +5.18 SOL
(dead 30 s after a refusal and after a sale). `lab --rearm SECS` scores only the buys a live sniper takes: after a kept
moment its wallet's sniper is spent for SECS after a refusal, and for the exits' longest hold plus SECS after a fill."""

import pytest

from snipetech.analysis.lab import Trigger, live_takes, rank, rank_wallets
from snipetech.config import Config
from snipetech.sim.exits import ExitParams

HOLD12 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=12, sell_on_dev_sell=False)
RULE = Trigger("kol", sol=0.1, group="cand")


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _row(ts: int, who: str = "W", refused: bool = False, pnl: float = 0.01) -> dict:
    return {"mint": f"m{who}{ts}", "trigger": 0, "ts": ts, "order": (ts, 0), "pnl": [pnl], "who": who,
            "lead_sol": 1.0, "rebuys": [], "first_sell_s": 4.0, "first_sell_frac": 1.0, "max_sell_frac": 1.0,
            **({"refused": True} if refused else {})}  # fmt: skip


def test_a_live_copy_sniper_takes_one_buy_a_start():
    """W's sniper buys at 0 and is armed again 12 s (the hold) + 30 s later: the buy at 20 goes uncopied, the one at 50
    is taken; refused at 100 it is back at 130: 110 goes uncopied, 135 is taken. V's sniper is another one."""
    rs = sorted([_row(0), _row(20), _row(50), _row(100, refused=True), _row(110), _row(135), _row(5, who="V")],
                key=lambda r: r["ts"])  # fmt: skip
    kept = live_takes(rs, rearm_secs=30, hold_secs=12)
    assert [(r["who"], r["ts"]) for r in kept] == [("W", 0), ("V", 5), ("W", 50), ("W", 100), ("W", 135)]
    assert live_takes(rs, rearm_secs=0, hold_secs=12) == rs  # off: every buy, as before


def test_a_rule_with_no_followed_wallet_is_one_sniper():
    """A launch rule's moments name no wallet: they are one sniper's, spent after each of its buys."""
    rs = [{"ts": t, "pnl": [0.0]} for t in (0, 10, 60)]
    assert [r["ts"] for r in live_takes(rs, rearm_secs=30, hold_secs=12)] == [0, 60]


def _data(rows: list[dict], rearm: float | None) -> dict:
    out = {"rows": rows, "exits": [HOLD12], "triggers": [RULE], "tape_hours": 10.0}
    return out | ({"rearm_secs": rearm} if rearm is not None else {})


def test_the_wallet_ranking_scores_only_the_buys_a_live_sniper_takes():
    rows = [_row(1_000 + 20 * k, pnl=0.01 + 0.001 * k) for k in range(36)]  # a buy every 20 s
    (every,) = rank_wallets(_data(rows, None), _cfg(), top=5, min_n=4)
    (live,) = rank_wallets(_data(rows, 30.0), _cfg(), top=5, min_n=4)
    assert every["n"] == 36 and live["n"] == 12  # one in three: each kept buy spends the sniper 42 s
    assert live["mean"] == pytest.approx(sum(0.01 + 0.001 * k for k in range(0, 36, 3)) / 12)


def test_the_strategy_ranking_scores_only_the_buys_a_live_sniper_takes_and_says_so():
    rows = [_row(1_000 + 20 * k) for k in range(60)]
    (every,) = rank(_data(rows, None), _cfg(), top=5, min_train=10)
    (live,) = rank(_data(rows, 30.0), _cfg(), top=5, min_train=10)
    assert every["moments"] == 60 and "rearm" not in every
    assert live["moments"] == 20 and live["rearm"] == {"secs": 30.0, "hold_secs": 12.0, "dropped": 40}


def test_lab_takes_the_dead_time_from_its_flag_and_never_a_negative_one():
    from snipetech import cli

    args = cli.build_parser().parse_args(["lab", "--each", "--rearm", "30", "--json"])
    assert args.rearm == 30.0
    assert cli.build_parser().parse_args(["lab"]).rearm is None  # off unless asked
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["lab", "--rearm", "-5"])
