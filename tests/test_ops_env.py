"""Remote jobs read /etc/snipetech/env as it is now. 7 Nov 18:06: the owner had put a new Anthropic key in the file, and a
round started through the ops service still ran on the old one: the service had loaded the old key at its own start and
the file only added keys the service did not have. The file wins: a changed key reaches the next job without a restart."""

from snipetech import ops


def test_a_key_changed_in_the_file_reaches_the_next_job(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("ANTHROPIC_API_KEY=new-key\nHELIUS_API_KEY=h\n")
    monkeypatch.setattr(ops, "ENV_FILE", f)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "old-key")
    env = ops._job_env()
    assert env["ANTHROPIC_API_KEY"] == "new-key" and env["HELIUS_API_KEY"] == "h"


def test_a_key_missing_from_the_file_keeps_the_services_value(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("# a comment\nHELIUS_API_KEY=h\n")
    monkeypatch.setattr(ops, "ENV_FILE", f)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "service-key")
    assert ops._job_env()["ANTHROPIC_API_KEY"] == "service-key"


def test_an_empty_value_in_the_file_never_blanks_a_key(tmp_path, monkeypatch):
    f = tmp_path / "env"
    f.write_text("ANTHROPIC_API_KEY=\n")
    monkeypatch.setattr(ops, "ENV_FILE", f)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "service-key")
    assert ops._job_env()["ANTHROPIC_API_KEY"] == "service-key"
