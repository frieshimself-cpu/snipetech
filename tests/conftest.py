import pytest


@pytest.fixture(autouse=True)
def _one_lab_machine(monkeypatch):
    """Tests run as a machine with one lab slot, whatever memory the box they run on has (analysis/lab_lock.py gives a
    24 GB machine two); a test of two slots passes `slots` itself."""
    monkeypatch.setenv("SNIPETECH_LAB_SLOTS", "1")


@pytest.fixture(autouse=True)
def _no_standin_jobs(monkeypatch):
    """A round that is not a dry run starts the loop stages whose timers are inactive as real background jobs; tests
    never do (test_standin checks that the round calls it by patching it again). It lives at the top of tests/ because
    pytest skips a folder's conftest for a file reached again after another folder on the command line."""
    monkeypatch.setattr("snipetech.operator.run.start_standins", lambda jobs, services, now, log=None: [])


@pytest.fixture(autouse=True)
def _no_wait_before_a_book_stop(monkeypatch):
    """Before it stops a sniper the book reads the wallet again, no sooner than book.run.SECOND_READ_SECS (5 s) after
    the run's own reading. Tests never wait; the test of the wait sets its own."""
    monkeypatch.setattr("snipetech.book.run.SECOND_READ_SECS", 0.0)
