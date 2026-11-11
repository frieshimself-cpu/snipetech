"""A deployer is "SNIPE" only on what a snipe makes where our launch buys land (11 Nov).

The verdict has a second way in: the bribe sweep, "profitable if you pay ~X SOL to land ahead of the other snipers".
The sweep places the buy in the snipers' own slot (its `assumes`: "you land in the same slots as the snipers"), right
behind the insiders when nobody else bids. Our launch buys land a slot after the create (CLAUDE.md rule 2: a result at
a better landing than the bid buys is not evidence). On the server 26 of the 110 SNIPE verdicts scored at our landing
had a lower bound at or under zero there; the newest, GWHLLfLr: EV -0.0226 SOL a snipe, lower bound -0.0746, 23 % wins,
13 launches, "SNIPE" on "+0.0671 for a bribe of ~0 SOL". `oktech plan` planned a 0.5 SOL sniper for each of them.
"""

from snipetech.analysis.deployer import _decide
from snipetech.config import Config
from snipetech.sim.exits import ExitParams


def _report(mean: float, lower: float, sweep_mean: float, sweep_lower: float, sweep_n: int = 6) -> dict:
    return {
        "your_strategy": {"mean": mean, "ev_lower": lower, "win_rate": 0.23, "n": 13},
        "dump_rate": 0.38,
        "rinse_rate": 0.15,
        "recent": {"launches": 5, "dump_rate": 0.4},
        "profile": {"insiders_dumped_before_your_entry": 0.0},
        "cadence": {"hours_since_last_launch": 3.0},
        "tip_sweep": {
            "rows": [],
            "assumes": "you land in the same slots as the snipers; fees order equally fast bots",
            "walk_forward": {
                "picked_bribe_sol": 0.0,
                "test": {"n": sweep_n, "mean": sweep_mean, "ev_lower": sweep_lower},
            },
        },
    }


def test_a_deployer_that_loses_where_we_land_is_not_a_snipe_because_a_first_place_would_pay():
    v = _decide(_report(-0.0226, -0.0746, 0.0671, 0.02), Config(), 13, ExitParams())
    assert v["verdict"] == "AVOID"
    assert v["recommended"]["buy_sol"] == 0.0 and v["recommended"]["bribe_sol"] == 0.0
    said = " ".join(v["reasons"])
    assert "EV -0.0226 SOL/snipe is negative" in said
    # what the sweep found is still said, with where it puts the buy
    assert "+0.0671" in said and "snipers' own slot" in said and "a slot later" in said


def test_a_marginal_deployer_stays_marginal_and_keeps_the_bribe_the_sweep_picked():
    v = _decide(_report(0.01, -0.02, 0.05, 0.02), Config(), 13, ExitParams())
    assert v["verdict"] == "MARGINAL" and v["recommended"]["bribe_sol"] == 0.0 and v["recommended"]["buy_sol"] > 0


def test_a_deployer_that_pays_where_we_land_is_a_snipe_as_before():
    v = _decide(_report(0.108, 0.0445, 0.05, 0.02), Config(), 21, ExitParams())
    assert v["verdict"] == "SNIPE" and "lower bound +0.0445 > 0" in " ".join(v["reasons"])
    assert not any("snipers' own slot" in r for r in v["reasons"])  # nothing to add when it is a snipe already


def test_a_score_made_while_the_bribe_sweep_made_verdicts_is_another_basis():
    """Version 3: stored version-2 scores (the 26 among them) are not planned from, not answered as verdicts, and are
    scored again when their deployer launches (Service.deployer_verdict, rescore_deployers, oktech.build_plan)."""
    from snipetech.analysis.deployer import SCORE_VERSION, score_basis

    assert SCORE_VERSION == 3 and score_basis(Config()) == "1@+3:v3"
