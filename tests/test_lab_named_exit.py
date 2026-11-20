"""A lab that scores exactly the exits an agent names (the agents' I193 and I174 since 15 Nov, I313 since 19 Nov: "a lab
flag that names the exit to score (e.g. --exit "SL-15% hold300s")"; "a lab flag for a wallet rule without the copy
exit"; "a hold of 12 s"). A lab picks one exit a rule on its older moments and shows only that one, so an exit OKTech
can be shown to run (a stop and a timer, no trailing) was never scored beside the one it picked. An exit is named the
way every lab and record already writes it (ExitParams.label), and read back into the same exit."""

import json

import pytest

from snipetech.analysis.lab import (
    LIVE_EXITS,
    RECIPES,
    TRAIN_SHARE,
    exit_set,
    kind_exits,
    mig_exit_set,
    recipe_exits,
)
from snipetech.config import Config
from snipetech.sim.exits import ExitParams, exit_from_label
from tests.test_recipe import _recipe_db

WALLET_RULE = '{"kind": "kol", "group": "wallet", "sol": 1}'


def _every_exit_the_lab_knows() -> list[ExitParams]:
    out = [*exit_set(), *mig_exit_set(), *LIVE_EXITS.values()]
    for exits in kind_exits(exit_set(), Config()).values():
        out += exits
    for r in RECIPES.values():
        out += recipe_exits(r)
    return out


def test_every_exit_the_lab_knows_reads_back_from_its_name():
    exits = _every_exit_the_lab_knows()
    assert len(exits) > 100
    for ex in exits:
        assert exit_from_label(ex.label()).label() == ex.label(), ex.label()


def test_an_agents_exit_is_read_word_by_word_in_any_order():
    ex = exit_from_label("SL-15% hold300s")
    assert (ex.stop_loss, ex.max_hold_secs, ex.take_profit, ex.trailing_stop) == (0.15, 300.0, 0.0, 0.0)
    assert not ex.sell_on_dev_sell and ex.copy_exit_pct == 0 and ex.watch_secs == 0
    assert exit_from_label("hold300s SL-15% TRAIL25%") == exit_from_label("SL-15% TRAIL25% hold300s")
    assert exit_from_label("SL-15% TRAIL25% hold300s").trailing_stop == 0.25
    assert exit_from_label("hold12s").max_hold_secs == 12.0
    ladder = exit_from_label("TP+20%@50%/+50% SL-15% hold300s copy50% watch1.3s")
    assert ladder.tp_ladder() == [(0.2, 0.5), (0.5, 1.0)] and ladder.copy_exit_pct == 0.5 and ladder.watch_secs == 1.3
    assert ladder.label() == "TP+20%@50%/+50% SL-15% hold300s copy50% watch1.3s"
    stops = exit_from_label("SL-10%@50%/-20%@50% hold300s")
    assert stops.sl_ladder() == [(0.1, 0.5), (0.2, 0.5)] and stops.stop_loss == 0
    mig = exit_from_label("TRAIL10%>10% hold60s MIGSELL50%+5s devexit STALE2s")
    assert (mig.trailing_activation, mig.migration_sell_frac, mig.migration_sell_delay) == (0.1, 0.5, 5.0)
    assert mig.sell_on_dev_sell and mig.stale_secs == 2.0


@pytest.mark.parametrize("words, said", [
    ("SL-15%", "names no timer"),
    ("hold300s sl15", "'sl15'"),
    ("SL-15% SL-20% hold300s", "'SL-20%' twice"),
    ("", "names no timer"),
    ("TP+50%/+20% hold300s", "rising order"),
])  # fmt: skip
def test_an_exit_nobody_can_read_is_refused_with_the_word_that_is_wrong(words, said):
    with pytest.raises(ValueError, match=said):
        exit_from_label(words)


def test_the_lab_scores_exactly_the_exits_named_and_shows_each_on_every_rule(tmp_path, capsys):
    """`--exit` twice: the lab scores those two exits and no other; `named_exits` shows each of them on every rule (all
    its moments and the newer 40 % the ranking judges), whichever the ranking picks. On a wallet rule each carries the
    copy exit and the watch delay every copy sniper has, as every wallet rule's exit does in a lab."""
    from snipetech import cli

    db = _recipe_db(tmp_path)
    argv = ["--db", str(db), "lab", "--trigger", WALLET_RULE, "--hours", "1", "--json",
            "--exit", "hold10s", "--exit", "SL-15% hold300s"]  # fmt: skip
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    named = out["named_exits"]
    assert [n["exit"] for n in named] == ["hold10s", "SL-15% hold300s"]
    moments = {n["scores"][0]["moments"] for n in named}
    assert len(moments) == 1 and moments.pop() > 40  # the same rule's moments, for each exit named
    for n in named:
        (score,) = n["scores"]
        assert score["all"]["n"] == score["moments"]  # every moment scored, on all of them and the newer 40 %
        assert score["newer"]["n"] == score["moments"] - int(score["moments"] * TRAIN_SHARE)
        assert score["exit"].endswith("copy50% watch1.3s")
    assert named[0]["scores"][0]["exit"] == "hold10s copy50% watch1.3s"
    picked = {r["exit"] for r in out["strategies"]}
    assert picked <= {"hold10s copy50% watch1.3s", "SL-15% hold300s copy50% watch1.3s"}
    assert cli.main([a for a in argv if a != "--json"]) == 0  # in words too
    text = capsys.readouterr().out
    assert "Named exit hold10s on every trigger" in text and "Named exit SL-15% hold300s on every trigger" in text


def test_a_wallet_rule_can_be_scored_without_the_copy_exit(tmp_path, capsys):
    from snipetech import cli

    db = _recipe_db(tmp_path)
    base = ["--db", str(db), "lab", "--trigger", WALLET_RULE, "--hours", "1", "--json", "--exit", "hold10s"]
    assert cli.main([*base, "--no-copy-exit"]) == 0
    off = json.loads(capsys.readouterr().out)
    assert off["named_exits"][0]["scores"][0]["exit"] == "hold10s watch1.3s"
    assert off["copy_exit"]["threshold_percent"] == 0  # the output says no copy exit was scored
    assert off["copy_exit"]["note"].startswith("none scored (--no-copy-exit)")
    assert cli.main(base) == 0
    on = json.loads(capsys.readouterr().out)
    assert on["named_exits"][0]["scores"][0]["exit"] == "hold10s copy50% watch1.3s"


def test_a_named_exit_is_refused_before_any_lab_when_it_cannot_be_read_or_meets_a_recipe(tmp_path, capsys):
    from snipetech import cli

    db = _recipe_db(tmp_path)
    assert cli.main(["--db", str(db), "lab", "--trigger", WALLET_RULE, "--exit", "SL-15%", "--json"]) == 2
    assert "names no timer" in capsys.readouterr().err
    assert cli.main(["--db", str(db), "lab", "--recipe", "6z91", "--exit", "hold10s", "--json"]) == 2
    assert "--exit" in capsys.readouterr().err
    assert cli.main(["--db", str(db), "lab", "--trigger", WALLET_RULE, "--no-copy-exit", "--json"]) == 2
    assert "--no-copy-exit" in capsys.readouterr().err  # only with the exits it is for
