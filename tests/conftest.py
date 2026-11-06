import pytest


@pytest.fixture(autouse=True)
def _no_standin_jobs(monkeypatch):
    """A round that is not a dry run starts the loop stages whose timers are inactive as real background jobs; tests
    never do (test_standin checks that the round calls it by patching it again). It lives at the top of tests/ because
    pytest skips a folder's conftest for a file reached again after another folder on the command line."""
    monkeypatch.setattr("snipetech.operator.run.start_standins", lambda jobs, services, now, log=None: [])
