"""The server repairs its own network setup (deploy/netwatch.sh, root, every minute).

12 Nov 2026: the server had no internet for twelve hours while it kept running. On 11 Nov at 07:14 UTC, during an
11-minute freeze, its network manager (systemd-networkd) could not set a route in time and gave up on the network
card ("enp1s0: Failed"). Nothing renewed the address after that; it ran out at 00:31 the next night. A restart twelve
hours later brought it back. One command re-applies the setup (`networkctl reconfigure`); nothing on the machine ran
it. The script runs it when a link's setup has failed or the IPv4 default route is gone, restarts the network manager
if that did not help, and does so once in ten minutes at most."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from snipetech import ops as O

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
SCRIPT = DEPLOY / "netwatch.sh"
OK = "  1 lo     loopback carrier  unmanaged\n  2 enp1s0 ether    routable configured\n"
FAILED = "  1 lo     loopback carrier  unmanaged\n  2 enp1s0 ether    routable failed\n"
ROUTE = "default via 45.77.66.1 dev enp1s0 proto dhcp src 45.77.66.186 metric 100\n"


@pytest.fixture
def box(tmp_path):
    """A machine made of shims: networkctl prints the links file and may heal on reconfigure, ip prints the route
    file, systemctl and sleep only write down that they were called."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    shims = {
        "networkctl": 'echo "networkctl $*" >> "$SHIM_LOG"\n'
                      'if [ "$1" = "list" ]; then cat "$SHIM_LINKS"; fi\n'
                      'if [ "$1" = "reconfigure" ] && [ -f "$SHIM_DIR/heals" ]; then\n'
                      '  cp "$SHIM_DIR/links.after" "$SHIM_LINKS"; cp "$SHIM_DIR/route.after" "$SHIM_ROUTE"\nfi\n',
        "ip": 'if [ "$*" = "-4 route show default" ]; then cat "$SHIM_ROUTE"; fi\n',
        "systemctl": 'echo "systemctl $*" >> "$SHIM_LOG"\n',
        "sleep": 'echo "sleep $*" >> "$SHIM_LOG"\n',
    }  # fmt: skip
    for name, body in shims.items():
        f = bin_ / name
        f.write_text("#!/bin/sh\n" + body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "links").write_text(OK)
    (tmp_path / "route").write_text(ROUTE)
    (tmp_path / "links.after").write_text(OK)
    (tmp_path / "route.after").write_text(ROUTE)
    (tmp_path / "log").write_text("")
    env = {"PATH": f"{bin_}:{os.environ['PATH']}", "SHIM_DIR": str(tmp_path), "SHIM_LOG": str(tmp_path / "log"),
           "SHIM_LINKS": str(tmp_path / "links"), "SHIM_ROUTE": str(tmp_path / "route"),
           "SNIPETECH_NETWATCH_STAMP": str(tmp_path / "stamp")}  # fmt: skip

    def run():
        r = subprocess.run(["/bin/sh", str(SCRIPT)], check=False, env=env, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        return r.stdout, (tmp_path / "log").read_text().splitlines()

    return tmp_path, run


def test_a_healthy_network_is_left_alone(box):
    _tmp, run = box
    out, log = run()
    assert out == "" and all(line.startswith("networkctl list") for line in log) and log


def test_a_link_the_network_manager_gave_up_on_is_reconfigured(box):
    tmp, run = box
    (tmp / "links").write_text(FAILED)
    (tmp / "heals").write_text("")
    out, log = run()
    assert "networkctl reconfigure enp1s0" in log and not [x for x in log if x.startswith("systemctl")]
    assert "gave up on enp1s0" in out and "repaired" in out
    assert (tmp / "links").read_text() == OK


def test_a_link_that_stays_failed_gets_the_network_manager_restarted(box):
    tmp, run = box
    (tmp / "links").write_text(FAILED)  # reconfigure changes nothing
    out, log = run()
    assert "networkctl reconfigure enp1s0" in log
    assert log.index("networkctl reconfigure enp1s0") < log.index("systemctl restart systemd-networkd")
    assert "restarting" in out


def test_a_lost_address_is_repaired_though_no_link_says_failed(box):
    """The state the server was in from 00:31 to 12:30 UTC had the script not been there earlier: the route gone."""
    tmp, run = box
    (tmp / "route").write_text("")
    (tmp / "heals").write_text("")
    out, log = run()
    assert "networkctl reconfigure enp1s0" in log and "networkctl reconfigure lo" not in log
    assert "no IPv4 default route" in out and "repaired" in out


def test_one_repair_in_ten_minutes_at_most(box):
    tmp, run = box
    (tmp / "links").write_text(FAILED)
    run()
    first = (tmp / "log").read_text().count("reconfigure")
    out, _log = run()
    assert out == "" and (tmp / "log").read_text().count("reconfigure") == first == 1
    (tmp / "stamp").write_text("1000")  # the last repair was long ago
    run()
    assert (tmp / "log").read_text().count("reconfigure") == 2


def test_a_machine_without_networkctl_does_nothing(tmp_path):
    env = {"PATH": "/nonexistent", "SNIPETECH_NETWATCH_STAMP": str(tmp_path / "stamp")}
    r = subprocess.run(["/bin/sh", str(SCRIPT)], check=False, env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and not (tmp_path / "stamp").exists()


def _unit(name):
    out, section = {}, ""
    for raw in (DEPLOY / "systemd" / name).read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line and not line.startswith(("#", ";")):
            k, v = line.split("=", 1)
            out[(section, k.strip())] = v.strip()
    return out


def test_it_runs_as_root_every_minute_and_setup_installs_and_starts_it():
    svc, timer = _unit("snipetech-netwatch.service"), _unit("snipetech-netwatch.timer")
    assert svc[("Service", "Type")] == "oneshot"
    assert svc[("Service", "ExecStart")] == "/usr/local/sbin/snipetech-netwatch"
    # root (no User=), and outside snipetech's slice: a squeezed slice must not hold up the repair of the network
    assert ("Service", "User") not in svc and ("Service", "Slice") not in svc
    assert timer[("Timer", "OnUnitActiveSec")] == "1min" and ("Timer", "OnBootSec") in timer
    assert timer[("Install", "WantedBy")] == "timers.target"
    setup = (DEPLOY / "setup.sh").read_text()
    assert 'install -m 755 "$REPO/deploy/netwatch.sh" /usr/local/sbin/snipetech-netwatch' in setup
    start = setup.index("systemctl enable --now snipetech-netwatch.timer")
    assert setup.index("\nsystemctl daemon-reload") < start  # systemd knows the unit before it is started
    assert {"snipetech-netwatch", "snipetech-netwatch.timer"} <= set(O.UNITS)  # `remote status`, `remote logs`
    assert "snipetech-netwatch" not in O.HOLD_UNITS  # it holds no deploy: it runs none of snipetech's code
    assert SCRIPT.read_text().startswith("#!/bin/sh\n")
