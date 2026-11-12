"""The owner's budget for the agents without SSH: operator/BUDGET (written by /budget in Telegram or `operate budget`)
wins over the config value by value, inside fixed bounds; a broken or out-of-range file means the config's values."""

import json
import math

import pytest

from snipetech.config import Config, OperatorConfig
from snipetech.operator.budget_switch import (
    budget_file,
    clear_budget,
    effective_analyst_every_h,
    effective_budget,
    effective_daily_usd,
    effective_max_tool_calls,
    effective_turn_usd,
    read_budget,
    set_budget,
)
from snipetech.operator.paths import OperatorPaths

NOW = 1_793_923_200 + 3600  # 2026-11-06 01:00 UTC


@pytest.fixture
def paths(tmp_path):
    return OperatorPaths(tmp_path / "data").ensure()


def _effective(cfg, paths):
    return (effective_daily_usd(cfg, paths), effective_turn_usd(cfg, paths), effective_max_tool_calls(cfg, paths),
            effective_analyst_every_h(cfg, paths))  # fmt: skip


def test_without_the_file_the_configs_values_apply(paths):
    op = OperatorConfig(daily_usd=15.0, turn_usd=2.0, max_tool_calls=20, analyst_every_h=0.0)
    assert _effective(op, paths) == (15.0, 2.0, 20, 0.0)
    cfg = Config()  # the whole config works as well as its [operator] part
    assert _effective(cfg, paths) == (15.0, 2.0, 20, 2.0)
    assert read_budget(paths) == {}


def test_set_budget_writes_the_owners_values_atomically_with_when_and_who(paths):
    out = set_budget(paths, OperatorConfig(), {"daily_usd": 200, "turn_usd": 6, "max_tool_calls": 40, "analyst_every_h": 2},
                     "telegram", NOW)  # fmt: skip
    assert out == {"daily_usd": 200.0, "turn_usd": 6.0, "max_tool_calls": 40, "analyst_every_h": 2.0}
    data = json.loads(budget_file(paths).read_text())
    assert data == {**out, "ts": NOW, "by": "telegram"} and budget_file(paths) == paths.root / "BUDGET"
    assert not list(paths.root.glob("*.tmp"))
    assert _effective(OperatorConfig(), paths) == (200.0, 6.0, 40, 2.0)
    assert isinstance(effective_max_tool_calls(OperatorConfig(), paths), int)


def test_a_value_not_given_keeps_the_files_or_else_the_configs(paths):
    op = OperatorConfig(daily_usd=15.0, turn_usd=2.0, max_tool_calls=20, analyst_every_h=0.0)
    set_budget(paths, op, {"daily_usd": 50}, "cli", NOW)
    assert _effective(op, paths) == (50.0, 2.0, 20, 0.0)  # only the daily budget is the owner's
    op.turn_usd = 3.0  # the config still decides what the file does not name
    assert effective_turn_usd(op, paths) == 3.0
    set_budget(paths, op, {"turn_usd": 4}, "cli", NOW + 60)
    assert _effective(op, paths) == (50.0, 4.0, 20, 0.0) and read_budget(paths) == {"daily_usd": 50.0, "turn_usd": 4.0}


GOOD = {"daily_usd": (0.01, 500), "turn_usd": (0.01, 25), "max_tool_calls": (5, 80), "analyst_every_h": (0, 0.25, 24)}
BAD = {"daily_usd": (0, -1, 501, math.nan, math.inf, "200", True), "turn_usd": (0, 26, -2, math.nan),
       "max_tool_calls": (4, 81, 40.5, True, "40"), "analyst_every_h": (0.1, 0.2, 24.5, -1, math.nan)}  # fmt: skip


@pytest.mark.parametrize("name", sorted(GOOD))
def test_each_value_is_kept_inside_its_bounds(paths, name):
    op = OperatorConfig(daily_usd=500.0, turn_usd=0.01)  # so that no turn here costs more than the day
    for v in GOOD[name]:
        set_budget(paths, op, {name: v}, "cli", NOW)
        assert effective_budget(op, paths)[name] == v
    clear_budget(paths)
    for v in BAD[name]:
        with pytest.raises(ValueError):
            set_budget(paths, op, {name: v}, "cli", NOW)
        assert not budget_file(paths).exists()  # a bad value writes nothing


def test_a_bad_value_changes_nothing_of_what_was_set(paths):
    op = OperatorConfig()
    set_budget(paths, op, {"daily_usd": 200, "turn_usd": 6}, "telegram", NOW)
    before = budget_file(paths).read_text()
    with pytest.raises(ValueError, match="80"):
        set_budget(paths, op, {"daily_usd": 300, "max_tool_calls": 500}, "telegram", NOW + 60)
    with pytest.raises(ValueError, match="unknown"):
        set_budget(paths, op, {"daily_usd": 300, "model": "x"}, "telegram", NOW + 60)
    assert budget_file(paths).read_text() == before


def test_a_turn_never_costs_more_than_the_day(paths):
    op = OperatorConfig(daily_usd=15.0, turn_usd=2.0)
    with pytest.raises(ValueError, match="turn"):
        set_budget(paths, op, {"daily_usd": 3, "turn_usd": 6}, "telegram", NOW)
    set_budget(paths, op, {"daily_usd": 200, "turn_usd": 6}, "telegram", NOW)
    with pytest.raises(ValueError, match="turn"):  # the file's 6 $ turn would cost more than the new 4 $ day
        set_budget(paths, op, {"daily_usd": 4}, "cli", NOW + 60)
    clear_budget(paths)
    with pytest.raises(ValueError, match="turn"):  # nor more than the config's 15 $ day when the file names none
        set_budget(paths, op, {"turn_usd": 20}, "cli", NOW + 60)
    assert not budget_file(paths).exists()


@pytest.mark.parametrize("text", ["not json", "[1, 2]", '"200"', "", "{"])
def test_a_broken_file_means_the_configs_values_and_never_raises(paths, text):
    op = OperatorConfig(daily_usd=15.0, turn_usd=2.0, max_tool_calls=20, analyst_every_h=2.0)
    budget_file(paths).write_text(text)
    assert _effective(op, paths) == (15.0, 2.0, 20, 2.0) and read_budget(paths) == {}


def test_an_out_of_range_value_in_the_file_falls_back_alone(paths):
    """A hand-edited file: each value that is outside its bounds (or not a number) is the config's; the sane ones stay."""
    op = OperatorConfig(daily_usd=15.0, turn_usd=2.0, max_tool_calls=20, analyst_every_h=2.0)
    budget_file(paths).write_text(
        json.dumps({"daily_usd": 9999, "turn_usd": 6, "max_tool_calls": "40", "analyst_every_h": 0.1})
    )
    assert _effective(op, paths) == (15.0, 6.0, 20, 2.0) and read_budget(paths) == {"turn_usd": 6.0}


def test_a_number_too_big_for_a_float_falls_back_too(paths):
    """JSON keeps a 400-digit integer as an int; turning it into a float overflows. The reader still never raises,
    and setting one says why instead of crashing."""
    budget_file(paths).write_text(json.dumps({"daily_usd": 10**400, "max_tool_calls": 10**400}))
    assert _effective(OperatorConfig(), paths) == (15.0, 2.0, 20, 2.0)
    with pytest.raises(ValueError, match="5 to 80"):
        set_budget(paths, OperatorConfig(), {"max_tool_calls": 10**400}, "cli", NOW)


def test_an_unreadable_file_means_the_configs_values(paths):
    budget_file(paths).mkdir()  # reading a directory raises OSError
    assert _effective(OperatorConfig(), paths) == (15.0, 2.0, 20, 2.0)


def test_default_removes_the_file(paths):
    set_budget(paths, OperatorConfig(), {"daily_usd": 200}, "telegram", NOW)
    clear_budget(paths)
    assert not budget_file(paths).exists() and effective_daily_usd(OperatorConfig(), paths) == 15.0
    clear_budget(paths)  # nothing to remove: fine


def test_the_new_cadence_field_is_not_pinned_by_the_servers_old_file(tmp_path):
    """/etc/snipetech/snipetech.toml was written by `init` / `config set` as a dump of every default: each old
    [operator] value there is pinned, but a field added since is not in it, so its default applies on the server."""
    from snipetech.config import dump_toml

    old = Config().to_dict()
    del old["operator"]["analyst_every_h"]
    p = tmp_path / "snipetech.toml"
    p.write_text(dump_toml(old))
    assert "analyst_every_h" not in p.read_text() and "daily_usd = 15.0" in p.read_text()
    assert Config.load(p).operator.analyst_every_h == 2.0 and OperatorConfig().analyst_every_h == 2.0


# -- `snipetech operate budget`: how the cloud session sets it, with the owner's OK in chat (`remote run operate budget`)


def _cli(tmp_path, *args):
    from snipetech import cli

    return cli.main(["--db", str(tmp_path / "data" / "snipetech.db"), "operate", *args])


def test_cli_operate_budget_sets_exactly_what_is_given_and_prints_what_applies(tmp_path, capsys):
    paths = OperatorPaths(tmp_path / "data")
    assert (
        _cli(
            tmp_path, "budget", "--daily-usd", "200", "--turn-usd", "6", "--tool-calls", "40", "--analyst-every-h", "2"
        )
        == 0
    )
    out = capsys.readouterr().out
    assert str(tmp_path / "data" / "operator" / "BUDGET") in out and "Claude budget: 200 $ a day" in out
    assert "at most 6 $ and 40 tool calls a turn, an analysis every 2 h" in out
    assert json.loads(budget_file(paths).read_text())["by"] == "cli"
    assert _cli(tmp_path, "budget", "--turn-usd", "4") == 0  # no profile here: only what is named changes
    assert effective_budget(Config(), paths) == {
        "daily_usd": 200.0,
        "turn_usd": 4.0,
        "max_tool_calls": 40,
        "analyst_every_h": 2.0,
    }
    before = budget_file(paths).read_text()
    assert _cli(tmp_path, "budget") == 0 and "Claude budget: 200 $ a day" in capsys.readouterr().out  # shows only
    assert budget_file(paths).read_text() == before


def test_cli_operate_budget_refuses_a_bad_value_and_goes_back_with_default(tmp_path, capsys):
    paths = OperatorPaths(tmp_path / "data")
    _cli(tmp_path, "budget", "--daily-usd", "200")
    before = budget_file(paths).read_text()
    for bad in (["--daily-usd", "900"], ["--tool-calls", "3"], ["--analyst-every-h", "30"], ["--turn-usd", "201"]):
        with pytest.raises(SystemExit, match="not changed"):
            _cli(tmp_path, "budget", *bad)
        assert budget_file(paths).read_text() == before
    with pytest.raises(SystemExit, match="alone"):
        _cli(tmp_path, "budget", "--default", "--daily-usd", "50")
    with pytest.raises(SystemExit, match="operate budget"):  # a budget flag without the action sets nothing
        _cli(tmp_path, "--daily-usd", "50")
    assert budget_file(paths).read_text() == before
    capsys.readouterr()
    assert _cli(tmp_path, "budget", "--default") == 0
    assert not budget_file(paths).exists() and "the config's values" in capsys.readouterr().out


def test_cli_operate_still_parses_without_the_budget_action():
    from snipetech.cli import build_parser

    a = build_parser().parse_args(["operate", "--dry-run"])
    assert a.action is None and a.dry_run and a.daily_usd is None and not a.budget_default
    a = build_parser().parse_args(["operate", "budget", "--tool-calls", "40", "--default"])
    assert a.action == "budget" and a.tool_calls == 40 and a.budget_default


def test_the_budget_line_says_what_the_alert_rounds_keep(paths):
    """The analyses between the daily ones pause before the day runs out (gate.py); the owner reads where, in dollars,
    and that /budget moves it."""
    from snipetech.operator.budget_switch import budget_line, room_for_extra_analysis

    line = budget_line(OperatorConfig(), paths, 3.2)
    assert "extra analyses pause when less than 6 $ of the day is left, so the alert rounds keep 4 $" in line
    assert room_for_extra_analysis(OperatorConfig(), paths, 9.0)
    assert not room_for_extra_analysis(OperatorConfig(), paths, 9.01)
    set_budget(paths, OperatorConfig(), {"daily_usd": 200, "turn_usd": 6}, "telegram", NOW)
    assert "less than 18 $ of the day is left, so the alert rounds keep 12 $" in budget_line(OperatorConfig(), paths, 0)
    assert room_for_extra_analysis(OperatorConfig(), paths, 182.0)
