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
