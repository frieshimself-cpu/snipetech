from pathlib import Path

import pytest

from snipetech.config import Config
from snipetech.operator.paths import OperatorPaths, research_dir


def test_operator_config_loads_from_toml(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('[operator]\nwallet = "W"\ndaily_usd = 1.5\nprices = { in = 1.0 }\n')
    cfg = Config.load(p)
    assert cfg.operator.wallet == "W" and cfg.operator.daily_usd == 1.5
    assert cfg.operator.prices["in"] == 1.0 and cfg.operator.prices["out"] == 15.0  # merged over defaults
    assert cfg.operator.model == "claude-sonnet-5-5" and cfg.operator.daily_usd != 15.0


def test_operator_config_defaults():
    o = Config().operator
    assert (o.daily_usd, o.turn_usd, o.max_jobs, o.equity_alert_sol, o.tape_stale_secs) == (15.0, 2.0, 3, 0.1, 300)
    assert o.analyst_after_utc == "06:00" and o.analyst_model == "claude-opus-5-5"
    assert o.prices["analyst_out"] == 25.0


def test_paths_and_report_name_guard(tmp_path):
    ps = OperatorPaths(tmp_path).ensure()
    assert ps.root == tmp_path / "operator" and ps.root.is_dir() and ps.jobs == tmp_path / "jobs"
    assert ps.report("analyst-2026-11-06") == ps.root / "analyst-2026-11-06.md"
    assert ps.notes == ps.root / "notes.md" and ps.snapshot_prev == ps.root / "snapshot.prev.json"
    for bad in ("../x", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            ps.report(bad)


def test_the_research_record_is_read_from_the_checkout(tmp_path):
    """docs/research in the checkout the caller names (the operator's --repo); without one, in the repository above the
    package, where the server runs it from (setup.sh installs it editable from /opt/snipetech and `remote update`
    resets that checkout to the branch, docs included). A checkout without it has no record, and that is no error."""
    (tmp_path / "docs" / "research").mkdir(parents=True)
    assert research_dir(tmp_path) == research_dir(str(tmp_path)) == tmp_path / "docs" / "research"
    assert research_dir(tmp_path / "old-checkout") is None
    assert research_dir() == Path(__file__).resolve().parents[2] / "docs" / "research"  # this test's own checkout


def test_an_installed_package_reads_no_research_record(tmp_path, monkeypatch):
    """Outside a checkout (a wheel in site-packages) there is no docs/research of ours, whatever sits near it."""
    import snipetech.operator.paths as P

    site = tmp_path / "lib" / "python3.11" / "site-packages"
    (site.parent / "docs" / "research").mkdir(parents=True)
    monkeypatch.setattr(P, "__file__", str(site / "snipetech" / "operator" / "paths.py"))
    assert P.research_dir() is None
