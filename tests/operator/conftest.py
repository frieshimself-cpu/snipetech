import pytest


@pytest.fixture(autouse=True)
def _no_standin_jobs(monkeypatch):
    """A round that is not a dry run starts the loop stages whose timers are inactive as real background jobs; tests
    never do (test_standin checks that the round calls it by patching it again)."""
    monkeypatch.setattr("snipetech.operator.run.start_standins", lambda jobs, services, now, log=None: [])
