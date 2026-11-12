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
    for name, cmd, cal in (
        ("discover", " discover run", "OnCalendar=*:07"),
        ("qualify", " qualify run", "OnCalendar=*:0/30"),
    ):
        service = (UNITS / f"snipetech-{name}.service").read_text()
        timer = (UNITS / f"snipetech-{name}.timer").read_text()
        assert (
            "User=@USER@" in service and "EnvironmentFile=/etc/snipetech/env" in service and "Type=oneshot" in service
        )
        exec_lines = [ln for ln in service.splitlines() if ln.startswith("ExecStart=")]
        assert len(exec_lines) == 1 and cmd in exec_lines[0] and "oktech" not in exec_lines[0]
        assert "Slice=snipetech.slice" in service and "TimeoutStartSec=" in service
        assert cal in timer and "Persistent=true" in timer and "RandomizedDelaySec=" in timer
    assert "KillMode=process" in (UNITS / "snipetech-qualify.service").read_text()
    assert {"snipetech-discover", "snipetech-discover.timer", "snipetech-qualify", "snipetech-qualify.timer"} <= set(
        O.UNITS
    )
    setup = (Path(__file__).resolve().parents[1] / "deploy" / "setup.sh").read_text()
    assert "snipetech-discover.timer" in setup and "snipetech-qualify.timer" in setup


def test_daily_job_runs_no_lab_of_its_own():
    """Six labs a night until 7 Nov, one until 11 Nov, none since: the strategy search of the operator's round ranks
    every rule where our buys land, every two hours, and nothing read the nightly lab's file (tests/test_nightly.py)."""
    service = (UNITS / "snipetech-daily.service").read_text()
    assert "$S $DB lab " not in service and "for lat in" not in service


def test_measure_unit_and_timer():
    service = (UNITS / "snipetech-measure.service").read_text()
    timer = (UNITS / "snipetech-measure.timer").read_text()
    assert "User=@USER@" in service and "EnvironmentFile=/etc/snipetech/env" in service and "Type=oneshot" in service
    exec_lines = [ln for ln in service.splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_lines) == 1 and " measure run" in exec_lines[0] and "oktech" not in exec_lines[0]
    assert "TimeoutStartSec=10m" in service and "Slice=snipetech.slice" in service
    assert "OnCalendar=*:0/5" in timer and "Persistent=true" in timer and "RandomizedDelaySec=20" in timer
    assert {"snipetech-measure", "snipetech-measure.timer"} <= set(O.UNITS)
    setup = (Path(__file__).resolve().parents[1] / "deploy" / "setup.sh").read_text()
    assert "snipetech-measure.timer" in setup
    from snipetech.operator.snapshot import ONESHOT_UNITS, WATCHED_UNITS

    assert "snipetech-measure" in ONESHOT_UNITS and "snipetech-measure.timer" in WATCHED_UNITS


def test_book_unit_and_timer():
    service = (UNITS / "snipetech-book.service").read_text()
    timer = (UNITS / "snipetech-book.timer").read_text()
    exec_lines = [ln for ln in service.splitlines() if ln.startswith("ExecStart=")]
    assert (
        len(exec_lines) == 1 and exec_lines[0].rstrip().endswith(" book run") and "--live" not in exec_lines[0]
    )  # dry unless the config enables it
    assert "User=@USER@" in service and "Type=oneshot" in service and "TimeoutStartSec=4m" in service
    assert "OnCalendar=*:0/5" in timer and "RandomizedDelaySec=15" in timer and "Persistent=true" in timer
    assert {"snipetech-book", "snipetech-book.timer"} <= set(O.UNITS)
    from snipetech.operator.snapshot import ONESHOT_UNITS, WATCHED_UNITS

    assert "snipetech-book" in ONESHOT_UNITS and "snipetech-book.timer" in WATCHED_UNITS
    setup = (Path(__file__).resolve().parents[1] / "deploy" / "setup.sh").read_text()
    assert "snipetech-book.timer" in setup


def test_strategist_unit_and_timer():
    service = (UNITS / "snipetech-strategist.service").read_text()
    timer = (UNITS / "snipetech-strategist.timer").read_text()
    exec_lines = [ln for ln in service.splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_lines) == 1 and exec_lines[0].rstrip().endswith(" strategist run")
    assert "OnCalendar=*-*-* 05:10:00 UTC" in timer and "Persistent=true" in timer  # daily; Claude only on new trades
    assert {"snipetech-strategist", "snipetech-strategist.timer"} <= set(O.UNITS)
    assert "snipetech-strategist.timer" in (UNITS.parent / "setup.sh").read_text()


def test_commands_unit_and_timer():
    service = (UNITS / "snipetech-commands.service").read_text()
    timer = (UNITS / "snipetech-commands.timer").read_text()
    exec_lines = [ln for ln in service.splitlines() if ln.startswith("ExecStart=")]
    assert len(exec_lines) == 1 and exec_lines[0].rstrip().endswith(" operate --commands")
    assert "OnCalendar=minutely" in timer
    assert {"snipetech-commands", "snipetech-commands.timer"} <= set(O.UNITS)
    assert "snipetech-commands.timer" in (UNITS.parent / "setup.sh").read_text()


# -- priorities inside the slice, and deploys that leave jobs running ----------------------------------------------
# Every snipetech unit runs in snipetech.slice, which yields to everything else on the machine (4 Nov). Inside it the
# tape recorder comes first: each service is its own cgroup, and cgroups share the CPU by CPUWeight, not by nice (7 Nov:
# the recorder ran 25-45 s behind while labs ran, at the same weight as every batch job). A deploy restarts
# snipetech-ops: its jobs go on (KillMode=process; 7 Nov 07:44 a deploy killed a `research search`), and `remote kill`
# still reaches them by id (ops.op_kill).


def _unit(name: str) -> dict[tuple[str, str], str]:
    out, section = {}, ""
    for raw in (UNITS / name).read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif "=" in line and not line.startswith(("#", ";")):
            k, v = line.split("=", 1)
            out[(section, k.strip())] = v.strip()
    return out


def test_every_service_runs_in_the_slice():
    """Every service that runs snipetech. The network watch is root's shell script (deploy/netwatch.sh, 12 Nov): it
    runs none of snipetech's code and stays outside, so a squeezed slice never holds up the repair of the network."""
    for p in sorted(UNITS.glob("*.service")):
        if p.name == "snipetech-netwatch.service":
            assert ("Service", "Slice") not in _unit(p.name)
            continue
        assert _unit(p.name).get(("Service", "Slice")) == "snipetech.slice", p.name


def test_the_tape_recorder_outweighs_every_other_service_in_the_slice():
    tape = _unit("snipetech-tape.service")
    weight = int(tape[("Service", "CPUWeight")])
    for p in sorted(UNITS.glob("*.service")):
        if p.name != "snipetech-tape.service":
            assert weight >= 5 * int(_unit(p.name).get(("Service", "CPUWeight"), "100")), p.name
    assert int(tape.get(("Service", "Nice"), "0")) <= 0


def test_a_deploy_leaves_the_jobs_of_the_ops_service_running():
    assert _unit("snipetech-ops.service")[("Service", "KillMode")] == "process"


def test_the_slice_still_yields_to_everything_else_on_the_machine():
    assert int(_unit("snipetech.slice")[("Slice", "CPUWeight")]) < 100


def _bytes(value: str) -> int:
    units = {"K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}
    return int(float(value[:-1]) * units[value[-1]]) if value[-1] in units else int(value)


def test_the_slice_lets_snipetech_use_the_machine():
    """7 Nov 19:39, the first reading of the slice (remote status): the 4 Nov caps, set to share the box with a trading
    server (2 cores, MemoryHigh 2600M, MemoryMax 3G), still held on the 6-core, 16 GB machine after the resize. The CPU
    quota had throttled 65,589 of 440,259 periods (12,888 s of CPU denied), memory.high had been hit 4,027,792 times
    with 2.72 GB in use, the recorder spent 9 % of its time waiting on the disk, and no other service on the machine
    held 100 MB. The slice keeps its low weight against everything else (sshd, Caddy come first under contention), so it
    needs no CPU quota. 10 Nov: the limits are shares of the machine's memory, not gigabytes, so a bigger server (the
    16 GB one sat at its 12 GB limit all day and froze once) is used after the owner's one `setup.sh` run, with no
    edit here: 80 % before the slice is squeezed, 88 % at most (16 GB: 13 and 14 GB; 32 GB: 26 and 28 GB)."""
    sl = _unit("snipetech.slice")
    assert ("Slice", "CPUQuota") not in sl
    high, top = sl[("Slice", "MemoryHigh")], sl[("Slice", "MemoryMax")]
    assert high.endswith("%") and top.endswith("%")
    assert 70 <= float(high[:-1]) < float(top[:-1]) <= 90


# -- other jobs on the machine ---------------------------------------------------------------------------------------
# 11 Nov 07:12-07:23 UTC the whole machine stood still: load 71 on 8 cores, the recorder 667 s inside one flush, the
# slice's page cache down from 26 GB to 0.3 GB, the ops channel unreachable. No snipetech job ran. Ubuntu's own
# update job (apt-daily-upgrade.service) held 15.6 GB when the machine answered again and was gone three minutes
# later; 7 Nov has the same 13-minute hole at 07:10. The tape replayed what it missed; the PumpSwap side has a hole.


def test_ubuntus_update_jobs_run_gently():
    """setup.sh gives both update jobs a drop-in: the lowest priority for processor and disk, and their page cache
    given back above 3 GB. No MemoryMax: an update killed half-way leaves packages half installed."""
    setup = (UNITS.parent / "setup.sh").read_text()
    start = setup.index("for unit in apt-daily.service apt-daily-upgrade.service")
    block = setup[start : setup.index("\ndone", start)]
    assert "/etc/systemd/system/$unit.d/snipetech.conf" in block
    for line in ("[Service]", "Nice=19", "CPUWeight=10", "IOWeight=10", "IOSchedulingClass=idle", "MemoryHigh=3G"):
        assert f"\n{line}\n" in block, line
    assert "MemoryMax" not in block
    assert start < setup.index("\nsystemctl daemon-reload")  # the command itself: systemd reads them in the same run


def test_the_slices_memory_is_not_the_first_to_go():
    """MemoryLow: while another job on the machine has memory to give back, the kernel takes it there before it takes
    the slice's below this share (the database's pages the recorder writes into). Best effort: nothing is refused."""
    sl = _unit("snipetech.slice")
    low = sl[("Slice", "MemoryLow")]
    assert low.endswith("%") and 30 <= float(low[:-1]) <= 60
    assert float(low[:-1]) < float(sl[("Slice", "MemoryHigh")][:-1])


def test_ubuntus_updater_never_restarts_a_snipetech_service():
    """11 Nov 06:04:00 UTC: the nightly job (1 h 29 min into its work), a qualify run and an operator round were killed
    with SIGTERM in the same second and started again; the events service was restarted with them; the recorder and
    the ops service, restarted by a deploy two minutes earlier, were left alone. That is needrestart, which Ubuntu's
    unattended upgrade runs after it installs packages: a service whose Python files changed on disk since it started
    counts as outdated and is restarted, and after a deploy that is every job still running the code of before it.
    The nightly job died the same way on 5 Nov (06:44:40) and 7 Nov (07:10:31). snipetech's services are restarted by
    `remote update` when they should be: setup.sh tells needrestart to leave every unit named snipetech-* alone."""
    setup = (UNITS.parent / "setup.sh").read_text()
    assert "/etc/needrestart/conf.d/snipetech.conf" in setup
    assert "$nrconf{override_rc}{qr(^snipetech-)} = 0;" in setup
    # only where needrestart is installed, and written literally (the shell must not expand the Perl)
    block = setup[
        setup.index("if [ -d /etc/needrestart/conf.d ]") : setup.index("/etc/needrestart/conf.d/snipetech.conf")
    ]
    assert "<<'EOF'" in setup[setup.index("/etc/needrestart/conf.d/snipetech.conf") :][:60] and block
