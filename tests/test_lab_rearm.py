"""The live copy sniper's dead time after each send (23 Nov 2026, the 0.2 SOL test).

The lab copies every buy of the wallet at the size. The live sniper buys once a start (`--snipes 1`) and the runner
arms it again some seconds after its sale or its refusal, so the wallet's buys inside that time go uncopied: after fill
3 (14:39:30) Yugi's sniper came back at 14:40:12, the moment of his next buy, which the paper took (-0.046) and the live
sniper missed. Offline on the ceiling labs of 08:05 the gap took the newer week's three wallets from +6.91 to +5.18 SOL
(dead 30 s after a refusal and after a sale). `lab --rearm SECS` scores only the buys a live sniper takes: after a kept
moment its wallet's sniper is spent for SECS after a refusal, and for the exits' longest hold plus SECS after a fill."""

import pytest

from snipetech.analysis.lab import REARM_GAP, REARM_ONE_EXIT, Trigger, exit_scores, live_takes, rank, rank_wallets
from snipetech.config import Config
from snipetech.sim.exits import ExitParams

HOLD12 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=12, sell_on_dev_sell=False)
HOLD300 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False)
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


def test_a_rule_that_follows_no_wallet_is_one_sniper_whoever_fired_it():
    """Review of 23 Nov: every lab row names who fired it (`who`: for a launch rule the coin's creator or buyer, one a
    coin), so keyed on it a launch rule was a sniper per coin and lost nothing. Only a wallet rule has a sniper per
    followed wallet; any other rule is one sniper, spent after each of its buys."""
    rs = [{"ts": t, "who": f"creator {t}", "pnl": [0.0]} for t in (0, 10, 60)]
    assert [r["ts"] for r in live_takes(rs, rearm_secs=30, hold_secs=12, per_wallet=False)] == [0, 60]
    assert [r["ts"] for r in live_takes(rs, rearm_secs=30, hold_secs=12)] == [0, 10, 60]  # wallet rule: three snipers


def test_two_buys_in_one_second_are_taken_in_the_chains_order():
    rs = [
        {"ts": 100, "who": "W", "order": (7, 2), "pnl": [0.0]},
        {"ts": 100, "who": "W", "order": (7, 1), "pnl": [0.0]},
    ]
    (kept,) = live_takes(rs, rearm_secs=30, hold_secs=12)
    assert kept["order"] == (7, 1)


def _data(rows: list[dict], rearm: float | None) -> dict:
    out = {"rows": rows, "exits": [HOLD12], "triggers": [RULE], "tape_hours": 10.0}
    return out | ({"rearm_secs": rearm} if rearm is not None else {})


def test_the_wallet_ranking_scores_only_the_buys_a_live_sniper_takes():
    rows = [_row(1_000 + 20 * k, pnl=0.01 + 0.001 * k) for k in range(36)]  # a buy every 20 s
    (every,) = rank_wallets(_data(rows, None), _cfg(), top=5, min_n=4)
    (live,) = rank_wallets(_data(rows, 30.0), _cfg(), top=5, min_n=4)
    assert every["n"] == 36 and live["n"] == 12  # one in three: each kept buy spends the sniper 42 s
    assert live["mean"] == pytest.approx(sum(0.01 + 0.001 * k for k in range(0, 36, 3)) / 12)


def _bursts(n: int) -> list[dict]:
    """A buy, 50 s, a buy, 20 s, a buy, 50 s, ...: with a 12 s hold and 30 s to be armed again, every third buy comes
    inside the dead time (the one 20 s after the last), and those are the losers."""
    ts, out = 1_000, []
    for k in range(n):
        out.append(_row(ts, pnl=0.02 if k % 3 != 2 else -0.05))
        ts += (50, 20, 50)[k % 3]
    return out


def test_the_strategy_ranking_scores_only_the_buys_a_live_sniper_takes_and_says_so():
    rows = _bursts(60)
    (every,) = rank(_data(rows, None), _cfg(), top=5, min_train=10)
    (live,) = rank(_data(rows, 30.0), _cfg(), top=5, min_train=10)
    assert every["moments"] == 60 and "rearm" not in every
    assert live["moments"] == live["exit_moments"] == 40  # every third buy came inside the dead time
    assert live["rearm"] == {"secs": 30.0, "hold_secs": 12.0, "dropped": 20}
    assert live["train_mean"] == pytest.approx(0.02)  # the losers in the dead time were never copied


def test_a_lab_of_several_exits_is_not_cut_to_one_exits_dead_time():
    """Review of 23 Nov: every exit was given the dead time of the lab's longest one (a 300 s hold cut a 12 s timer's
    buys). Each exit's sniper is armed again after its own hold, so --rearm scores one exit at a time and a rule
    scored on several says why it was not cut."""
    data = _data(_bursts(60), 30.0) | {"exits": [HOLD12, HOLD300]}
    for r in data["rows"]:
        r["pnl"] = [r["pnl"][0], r["pnl"][0] - 0.001]
    (two,) = rank(data, _cfg(), top=5, min_train=10)
    assert two["moments"] == 60 and two["rearm"] == {"secs": 30.0, "skipped": REARM_ONE_EXIT}
    (scores,) = exit_scores(data, _cfg(), HOLD12)
    assert scores["moments"] == 60 and scores["rearm"]["skipped"] == REARM_ONE_EXIT


def test_lab_refuses_rearm_with_more_or_fewer_than_one_exit(tmp_path, capsys, monkeypatch):
    from snipetech import cli

    monkeypatch.setenv("SNIPETECH_DB", str(tmp_path / "x.db"))
    for extra in ([], ["--exit", "hold12s", "--exit", "hold30s"]):
        code = cli.main(["lab", "--each", "--rearm", "30", "--json", *extra])
        assert code == 2 and "--rearm scores one exit at a time" in capsys.readouterr().err


def test_a_launch_rule_under_rearm_is_one_sniper():
    rows = [r | {"who": f"creator {k}"} for k, r in enumerate(_bursts(60))]  # each coin fired by its own creator
    data = _data(rows, 30.0) | {"triggers": [Trigger("whale", sol=1.0)]}
    (live,) = rank(data, _cfg(), top=5, min_train=10)
    assert live["moments"] == 40 and live["rearm"]["dropped"] == 20


def test_the_exit_scores_an_agent_reads_honour_the_dead_time():
    """`named_exits` (`exit_scores`) read every buy while the output said only the takes were scored (review of 23
    Nov)."""
    rows = _bursts(60)
    (every,) = exit_scores(_data(rows, None), _cfg(), HOLD12)
    (live,) = exit_scores(_data(rows, 30.0), _cfg(), HOLD12)
    assert every["all"]["n"] == 60 and live["all"]["n"] == 40 and live["moments"] == 40
    assert live["all"]["mean"] == pytest.approx(0.02) and live["rearm"]["dropped"] == 20


def test_a_wallets_buys_under_its_own_floor_never_spend_its_sniper():
    """Review of 23 Nov: the dead time was taken before the wallet's own floor, so a buy under it (never copied live)
    spent the simulated sniper and hid the next buy at the size."""
    rows = []
    for k in range(20):
        rows.append(_row(1_000 + 100 * k) | {"lead_sol": 0.5})  # under the floor: no copy
        rows.append(_row(1_000 + 100 * k + 5) | {"lead_sol": 2.0})  # 5 s later, at the size
    data = _data(sorted(rows, key=lambda r: r["ts"]), 30.0)
    (live,) = rank_wallets(data, _cfg(), top=5, min_n=4, floor=1.0, own_floor=False)
    assert live["n"] == 20


def test_a_buy_its_sniper_did_not_take_is_left_out_in_words():
    rows = _bursts(60)
    (live,) = rank(_data(rows, 30.0), _cfg(), top=5, min_train=10, trades=True)
    assert live["left_out"] and all(why == REARM_GAP for _ts, _mint, why in live["left_out"])


def test_lab_takes_the_dead_time_from_its_flag_and_never_a_negative_one():
    from snipetech import cli

    args = cli.build_parser().parse_args(["lab", "--each", "--rearm", "30", "--json"])
    assert args.rearm == 30.0
    assert cli.build_parser().parse_args(["lab"]).rearm is None  # off unless asked
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["lab", "--rearm", "-5"])
