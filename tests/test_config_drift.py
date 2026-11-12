"""The server's settings file is `init`'s dump of every default of its day and the owner does not edit it, so a default
changed in the code since never reaches the server (10 Nov: the analyst's model and the price table).

11 Nov: the deployer verdicts were moved to the measured landing ("+3": behind the first three buys of the block after
the create) by changing `[snipe] entry_position`'s default. The server's file said "middle", and the re-scoring job
wrote 3,208 scores at the old landing, each one saying so in its basis ("1@middle:v2").

Two things follow. A value in the file that equals a retired default is not a choice, in any section. And
`doctor --settings` lists what the file sets differently from today's defaults, so the next one is seen, not guessed.
"""

import json
from pathlib import Path

from snipetech.analysis.deployer import score_basis
from snipetech.cli import main
from snipetech.config import Config, settings_drift


def test_the_installers_landing_in_the_servers_file_is_not_a_choice(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[snipe]\nbuy_sol = 0.5\nentry_slot_offset = 1\nentry_position = "middle"\n')
    cfg = Config.load(p)
    assert cfg.snipe.entry_position == "+3" == Config().snipe.entry_position
    assert score_basis(cfg) == "1@+3:v5"  # the basis every stored verdict must have
    p.write_text('[snipe]\nentry_position = "end"\n')  # any other value is somebody's choice and stays
    assert Config.load(p).snipe.entry_position == "end"


def test_the_drift_lists_what_the_file_sets_differently_from_todays_defaults(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(
        "[snipe]\nbuy_sol = 0.5\nentry_slot_offset = 2\n"
        'entry_position = "middle"\nold_knob = 7\n\n'
        "[snipe.exit]\nstop_loss = 0.5\n\n"
        '[rpc]\nhttp_url = "https://rpc.example/?api-key=SECRETSECRET"\nmax_rps = 8\n\n'
        '[operator]\nanalyst_model = "claude-opus-5-5"\nprices = { in = 3.0, analyst_in = 9.0, extra = 1.5 }\n\n'
        "[gone]\nx = 1\n"
    )
    d = Config()
    out = settings_drift(p)
    assert out["path"] == str(p)
    assert out["differs"] == {
        "operator.prices.analyst_in": {"file": 9.0, "default": d.operator.prices["analyst_in"]},
        "operator.prices.extra": {"file": 1.5, "default": None},
        "rpc.http_url": {"hidden": True},  # may hold a key: said to differ, never shown
        "snipe.entry_slot_offset": {"file": 2, "default": 1},
        "snipe.exit.stop_loss": {"file": 0.5, "default": d.snipe.exit.stop_loss},
    }
    # the file's dump of an old default: the loader follows today's
    assert out["retired"] == {
        "operator.analyst_model": {"file": "claude-opus-5-5", "now": d.operator.analyst_model},
        "operator.prices.in": {"file": 3.0, "now": d.operator.prices["in"]},
        "snipe.entry_position": {"file": "middle", "now": "+3"},
    }
    assert out["unknown"] == ["gone.x", "snipe.old_knob"]  # keys no code reads any more
    assert out["same"] == 2  # snipe.buy_sol and rpc.max_rps: set, and equal to today's default
    assert "SECRETSECRET" not in json.dumps(out)


def test_the_drift_of_a_file_that_is_todays_dump_is_empty(tmp_path):
    p = tmp_path / "s.toml"
    Config().save(p)
    out = settings_drift(p)
    assert (out["differs"], out["retired"], out["unknown"]) == ({}, {}, [])
    assert out["same"] > 100
    assert settings_drift(tmp_path / "none.toml") == {
        "path": str(tmp_path / "none.toml"), "differs": {}, "retired": {}, "unknown": [], "same": 0,
    }  # fmt: skip


def test_doctor_settings_prints_the_drift_and_asks_no_network(tmp_path, capsys):
    p = tmp_path / "s.toml"
    p.write_text('[snipe]\nentry_position = "middle"\nrunner_multiple = 3.0\n')
    assert main(["--config", str(p), "--db", str(tmp_path / "t.db"), "doctor", "--settings"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["differs"] == {"snipe.runner_multiple": {"file": 3.0, "default": 2.0}}
    assert out["retired"] == {"snipe.entry_position": {"file": "middle", "now": "+3"}}


# ---- the server's own file -------------------------------------------------------------------------------------------

SERVER_FILE = Path(__file__).parent / "fixtures" / "server_settings_11nov.toml"
# what the owner chose on the server, by a root step; everything else in the file is a default of its day
OWNERS = {
    "book.daily_risk_sol",  # 1000: "live trading never stops" (8 Nov)
    "book.day_stop_sol",  # 1000: the same
    "book.equity_floor_sol",  # 0.5
    "operator.wallet",
}


def test_the_servers_file_differs_from_todays_defaults_only_where_the_owner_chose():
    """The guard for every default changed from now on: this file holds the old value, so the change shows here as a
    difference until the old value is retired (RETIRED_DEFAULTS / RETIRED_BOUNDS in config.py: the server follows the
    new default) or the setting is listed above as the owner's (the server keeps its value)."""
    out = settings_drift(SERVER_FILE)
    assert set(out["differs"]) == OWNERS, (
        "a default changed and the server's file still holds the old value: retire the old value in config.py "
        f"or list the setting as the owner's: {sorted(set(out['differs']) ^ OWNERS)}"
    )
    assert out["unknown"] == []


def test_the_strategists_old_bid_bounds_in_the_servers_file_are_not_in_force():
    """6 Nov 21:02 (09477d9): the control arm's tip may be tuned up to 0.005 SOL, not 0.05, because the fill is the
    arm's fee over max_fee_share: a higher tip makes every buy bigger. The priority fee and the retired paid arm lost
    their rules the same day. The server's file kept all four old rules, so its strategist could still raise the tip
    tenfold."""
    cfg = Config.load(SERVER_FILE, overlay=False)
    assert cfg.autotune.bounds["book.bid_arms.control.tip"] == {"min": 0.002, "max": 0.005}
    for gone in ("book.bid_arms.control.priority", "book.bid_arms.paid.priority", "book.bid_arms.paid.tip"):
        assert gone not in cfg.autotune.bounds
    assert cfg.autotune.bounds == Config().autotune.bounds
    out = settings_drift(SERVER_FILE)
    assert out["retired"]["autotune.bounds.book.bid_arms.control.tip"] == {
        "file": {"min": 0.002, "max": 0.05}, "now": {"min": 0.002, "max": 0.005},
    }  # fmt: skip
    assert out["retired"]["autotune.bounds.book.bid_arms.paid.tip"] == {
        "file": {"min": 0.002, "max": 0.05},
        "now": None,
    }


def test_a_bound_the_owner_sets_is_kept(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[autotune.bounds."book.bid_arms.control.tip"]\nmin = 0.002\nmax = 0.01\n')
    assert Config.load(p, overlay=False).autotune.bounds["book.bid_arms.control.tip"] == {"min": 0.002, "max": 0.01}
    assert settings_drift(p)["differs"] == {
        "autotune.bounds.book.bid_arms.control.tip.max": {"file": 0.01, "default": 0.005}
    }


def test_nothing_explores_on_the_server_either():
    """6 Nov 19:04 (2e399af): the paid arm is retired and nothing explores. The file kept the 0.2 of that morning."""
    assert Config.load(SERVER_FILE, overlay=False).book.explore_share == 0.0
    assert settings_drift(SERVER_FILE)["retired"]["book.explore_share"] == {"file": 0.2, "now": 0.0}


def test_the_owners_limits_stay_as_the_server_has_them():
    cfg = Config.load(SERVER_FILE, overlay=False)
    assert (cfg.book.daily_risk_sol, cfg.book.day_stop_sol, cfg.book.equity_floor_sol) == (1000.0, 1000.0, 0.5)


def test_preconfirmations_are_on_since_the_owners_yes():
    """The server's file held `preconf = false` since the 4 Nov install; the code has asked OKTech for
    preconfirmations since 6 Nov, so no live sniper ever asked. It changes how live buys are placed, so it waited
    for the owner: asked 11 Nov 08:30 UTC, answered yes at 21:50 UTC (the book's next copy snipers, 0.2 SOL a buy, the
    next 20 copies compared with the 24 before). To switch it back: the default in config.py, not the server's file."""
    assert Config.load(SERVER_FILE, overlay=False).oktech.preconf is True
    assert settings_drift(SERVER_FILE)["retired"]["oktech.preconf"] == {"file": False, "now": True}


def test_the_bot_keeps_plain_sol_for_two_curve_buys_since_the_owners_yes():
    """A pump.fun curve buy is paid whole in plain SOL, and OKTech leaves the wallet's SOL wrapped. The owner's yes of
    11 Nov 21:50 UTC: first a 0.05 SOL test, then the bot keeps plain SOL for up to two 0.2 SOL curve buys from the
    wallet's own wrapped SOL. The test ran on 12 Nov 14:56 UTC (docs/live-recipe.md), so the rule is on. The server's
    file holds no value for it: the default here is what the server runs. To switch it off: 0 here."""
    assert Config().book.keep_curve_buys == 2
    assert Config.load(SERVER_FILE, overlay=False).book.keep_curve_buys == 2


def test_the_research_agents_are_off_until_the_default_here_says_on():
    """The four specialised research agents (operator/roles.py; the owner asked for them on 12 Nov) are built and
    switched off: a deploy of their code changes nothing on the server. The server's file holds no value for the
    switch, so the default here is what the server runs. To switch them on, after the review: True here, in this test
    and in the deploy that follows; `/budget every 0` switches them off again without a deploy."""
    assert Config().operator.role_agents is False
    assert Config.load(SERVER_FILE, overlay=False).operator.role_agents is False
    assert "operator.role_agents" not in settings_drift(SERVER_FILE)["differs"]
