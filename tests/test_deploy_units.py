from pathlib import Path

from snipetech import ops as O

UNITS = Path(__file__).resolve().parents[1] / "deploy" / "systemd"


def test_operator_units():
    service = (UNITS / "snipetech-operator.service").read_text()
    timer = (UNITS / "snipetech-operator.timer").read_text()
    assert "User=@USER@" in service and "EnvironmentFile=/etc/snipetech/env" in service and "Type=oneshot" in service
    exec_lines = [ln for ln in service.splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_lines) == 1 and " operate" in exec_lines[0] and "oktech" not in exec_lines[0]
    assert "KillMode=process" in service and "Slice=snipetech.slice" in service and "TimeoutStartSec=" in service
    assert "OnCalendar=*:0/15" in timer and "Persistent=true" in timer and "RandomizedDelaySec=60" in timer


def test_units_list_includes_operator():
    assert "snipetech-operator" in O.UNITS and "snipetech-operator.timer" in O.UNITS


def test_setup_enables_the_operator_timer():
    setup = (Path(__file__).resolve().parents[1] / "deploy" / "setup.sh").read_text()
    assert "snipetech-operator.timer" in setup


def test_discover_and_qualify_units():
    for name, cmd, cal in (("discover", " discover run", "OnCalendar=*:07"), ("qualify", " qualify run", "OnCalendar=*:0/30")):
        service = (UNITS / f"snipetech-{name}.service").read_text()
        timer = (UNITS / f"snipetech-{name}.timer").read_text()
        assert "User=@USER@" in service and "EnvironmentFile=/etc/snipetech/env" in service and "Type=oneshot" in service
        exec_lines = [ln for ln in service.splitlines() if ln.startswith("ExecStart=")]
        assert len(exec_lines) == 1 and cmd in exec_lines[0] and "oktech" not in exec_lines[0]
        assert "Slice=snipetech.slice" in service and "TimeoutStartSec=" in service
        assert cal in timer and "Persistent=true" in timer and "RandomizedDelaySec=" in timer
    assert "KillMode=process" in (UNITS / "snipetech-qualify.service").read_text()
    assert {"snipetech-discover", "snipetech-discover.timer", "snipetech-qualify", "snipetech-qualify.timer"} <= set(O.UNITS)
    setup = (Path(__file__).resolve().parents[1] / "deploy" / "setup.sh").read_text()
    assert "snipetech-discover.timer" in setup and "snipetech-qualify.timer" in setup


def test_daily_job_runs_one_lab_not_six():
    service = (UNITS / "snipetech-daily.service").read_text()
    assert service.count("$S $DB lab ") == 1 and "for lat in" not in service and "--position +1" in service
