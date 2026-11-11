"""The nightly job runs what something reads (11 Nov).

`snipetech-daily` ran five commands every night at 04:17 UTC and used three to four processor-hours doing it (the most
of any regular job). Three of them fed nothing:

- `lab --latency 0 --position +1`: every rule scored one trade behind the trigger in its own block, a place our copies
  reach only behind a quiet leader (CLAUDE.md rule 2). Its first line on 11 Nov read "+360 SOL a day". Three
  processor-hours, half an hour and more of the one lab lock, a text file. The strategy search (`research search`,
  every two hours) ranks every rule where our buys land and feeds the paper test.
- `follow --kols --days 1`: a ranking of 18 wallets, stored under a name nothing reads, after fetching up to 12,607
  transactions for one of them over the RPC.
- `oktech plan`: a plan of deployer snipers that is never pushed.

Nothing read the report files either: in two days of the agents' turns (140) no `reports/` path appears once. And
while the unit ran, three to four hours a night, the research loop started nothing (one heavy job at a time).

The unit file on the server is a copy of install day and only root can change it, so the three commands retire
themselves when they find they run inside that unit, as old settings do (config.RETIRED_DEFAULTS). By hand, from an
agent or through `remote run` they run as before. `oktech plan` also refreshed the SOL price on the side, which the
book's market-cap cap needs: that is asked for where it is used now (tests/test_oktech.py).
"""

from pathlib import Path

from snipetech.infra import nightly

UNIT_FILE = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "snipetech-daily.service"
DB = ["--db", "/var/lib/snipetech/snipetech.db"]
RETIRED = ("lab --latency 0 --position +1", "follow --kols --days 1", "oktech plan")
KEPT = ("profitable-devs --backfill 20", "events edge")


def test_the_unit_a_process_runs_in_is_read_off_its_cgroup():
    assert nightly.unit_of("0::/snipetech.slice/snipetech-daily.service\n") == "snipetech-daily.service"
    assert nightly.unit_of("0::/snipetech.slice/snipetech-ops.service\n") == "snipetech-ops.service"
    old_style = "12:pids:/system.slice/snipetech-daily.service\n1:name=systemd:/system.slice/snipetech-daily.service\n"
    assert nightly.unit_of(old_style) == "snipetech-daily.service"
    assert nightly.unit_of("0::/user.slice/user-0.slice/session-3.scope\n") is None
    assert nightly.unit_of("") is None


def test_the_steps_nothing_reads_are_retired_inside_the_nightly_unit_and_nowhere_else():
    for step in RETIRED:
        words = [*DB, *step.split()]
        assert nightly.retired_reason(words, nightly.DAILY_UNIT), step
        assert nightly.retired_reason(words, "snipetech-ops.service") is None  # by hand, or an agent's job
        assert nightly.retired_reason(words, "snipetech-operator.service") is None
        assert nightly.retired_reason(words, None) is None  # a terminal
    for step in KEPT:
        assert nightly.retired_reason([*DB, *step.split()], nightly.DAILY_UNIT) is None, step


def test_only_the_nightly_jobs_own_command_is_retired_not_every_lab():
    other = [*DB, "lab", "--latency", "0", "--position", "+1", "--hours", "24"]
    assert nightly.retired_reason(other, nightly.DAILY_UNIT) is None
    assert nightly.retired_reason([*DB, "oktech", "plan", "--json"], nightly.DAILY_UNIT) is None
    assert nightly.retired_reason([], nightly.DAILY_UNIT) is None
    assert nightly.retired_reason(["--db"], nightly.DAILY_UNIT) is None


def test_the_unit_file_runs_the_kept_steps_and_no_retired_one():
    text = UNIT_FILE.read_text()
    for step in RETIRED:
        assert step not in text, step
    for step in KEPT:
        assert step in text, step


def test_a_retired_step_says_why_and_opens_nothing(monkeypatch, capsys, tmp_path):
    from snipetech import cli

    def boom(*a, **k):
        raise AssertionError("a retired step opened the service")

    monkeypatch.setattr(nightly, "current_unit", lambda: nightly.DAILY_UNIT)
    monkeypatch.setattr(cli, "Service", boom)
    db = tmp_path / "x.db"
    for step in RETIRED:
        assert cli.main(["--db", str(db), *step.split()]) == 0
        out = capsys.readouterr().out
        assert "retired from the nightly job" in out and step.split()[0] in out
    assert not db.exists()


def test_outside_the_nightly_unit_the_command_line_is_as_it_was(monkeypatch, capsys):
    from snipetech import cli

    monkeypatch.setattr(nightly, "current_unit", lambda: "snipetech-ops.service")
    assert cli.main(["config", "show"]) == 0
    assert "retired from the nightly job" not in capsys.readouterr().out


def test_status_says_which_unit_answers_it(tmp_path, monkeypatch):
    """The retirement rests on a command finding the unit it runs in. The ops service says its own in `remote status`,
    so one call shows the server's cgroup text reads as expected ("snipetech-ops.service")."""
    import sys

    from snipetech import ops as O

    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    ops = O.Ops(repo=tmp_path, data=data, db=data / "x.db", keys_file=tmp_path / "k", branch="main",
                command=[sys.executable, "-c", "pass"])  # fmt: skip
    monkeypatch.setattr(nightly, "current_unit", lambda: "snipetech-ops.service")
    assert ops.op_status()["unit"] == "snipetech-ops.service"
