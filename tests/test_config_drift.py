"""The server's settings file is `init`'s dump of every default of its day and the owner does not edit it, so a default
changed in the code since never reaches the server (10 Nov: the analyst's model and the price table).

11 Nov: the deployer verdicts were moved to the measured landing ("+3": behind the first three buys of the block after
the create) by changing `[snipe] entry_position`'s default. The server's file said "middle", and the re-scoring job
wrote 3,208 scores at the old landing, each one saying so in its basis ("1@middle:v2").

Two things follow. A value in the file that equals a retired default is not a choice, in any section. And
`doctor --settings` lists what the file sets differently from today's defaults, so the next one is seen, not guessed.
"""

import json

from snipetech.analysis.deployer import score_basis
from snipetech.cli import main
from snipetech.config import Config, settings_drift


def test_the_installers_landing_in_the_servers_file_is_not_a_choice(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[snipe]\nbuy_sol = 0.5\nentry_slot_offset = 1\nentry_position = "middle"\n')
    cfg = Config.load(p)
    assert cfg.snipe.entry_position == "+3" == Config().snipe.entry_position
    assert score_basis(cfg) == "1@+3:v2"  # the basis every stored verdict must have
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
