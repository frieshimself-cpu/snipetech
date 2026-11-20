"""20 Nov 2026 (the owner: "As long as ur sure we no longer need it"): Birdeye and SolanaTracker are switched off.
Every wallet that ever passed qualify came from our own sources (first buyers of runners, the tape, the winners
lists, early leaders, the owner's picks; one also from GMGN), none from either; their histories cover only their own
finds (798 and 40 of the week's 50,685 candidates, none of them ever qualified); and the events their lists feed lose
as buy signals in the nightly table (trending_birdeye -0.010 SOL a 0.2 SOL trade over 210, graduating -0.012 over
7,202, trending -0.013 over 3,329). Off means no call: no client is made for discovery or the events poll, no credit
row is shown, and a command that names one says it is off. `enabled = true` under [birdeye] or [solanatracker]
brings one back (its key must still be in /etc/snipetech/env)."""

import pytest

from snipetech.config import Config, provider_on

NOW = 1_795_150_800  # 20 Nov 2026 05:00 UTC


def test_both_are_off_by_default_even_with_their_keys(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "be-key")
    monkeypatch.setenv("SOLANATRACKER_API_KEY", "st-key")
    cfg = Config()
    assert not provider_on(cfg, "birdeye") and not provider_on(cfg, "solanatracker")
    cfg.birdeye.enabled = cfg.solanatracker.enabled = True
    assert provider_on(cfg, "birdeye") and provider_on(cfg, "solanatracker")
    monkeypatch.delenv("BIRDEYE_API_KEY")
    assert not provider_on(cfg, "birdeye")  # on, but no key: there is nothing to call


def test_no_client_is_made_for_a_provider_that_is_off(monkeypatch, tmp_path):
    from snipetech import cli
    from snipetech.service import Service

    monkeypatch.setenv("BIRDEYE_API_KEY", "be-key")
    monkeypatch.setenv("SOLANATRACKER_API_KEY", "st-key")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    svc = Service(cfg)
    try:
        assert cli._data_clients(cfg, svc) == {}  # what discovery and the events poll would call: nothing
        cfg.birdeye.enabled = True
        made = cli._data_clients(cfg, svc)
        assert set(made) == {"birdeye"}
        for c in made.values():
            c.close()
    finally:
        svc.store.close()


def test_no_credit_row_for_a_provider_that_is_off(tmp_path):
    from snipetech.operator.budget import Ledger
    from snipetech.operator.resources import check_resources
    from snipetech.store import Store
    from tests.operator.test_gate import snap

    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "snipetech.db")
    st = Store(cfg.storage.db_path)
    try:

        def rows(credits):
            return {r.name for r in check_resources(cfg, st, snap(tape_last_ts=NOW - 5), Ledger(tmp_path / "s.jsonl",
                    cfg.operator.prices), disk_free_gb=lambda: 200.0, birdeye_credits=credits, now=NOW)}  # fmt: skip

        off = rows(lambda: {"used": 1, "remaining": 9})
        assert "birdeye_cu" not in off and "solanatracker_requests" not in off and "vybe_credits" in off
        cfg.birdeye.enabled = cfg.solanatracker.enabled = True
        assert {"birdeye_cu", "solanatracker_requests"} <= rows(lambda: {"used": 1, "remaining": 9})
    finally:
        st.close()


@pytest.mark.parametrize("argv", [["birdeye", "credits"], ["solanatracker", "leaderboard"],
                                  ["discover-wallets", "--source", "birdeye"],
                                  ["discover-wallets", "--source", "solanatracker"]])  # fmt: skip
def test_a_command_that_names_one_says_it_is_off(argv, monkeypatch, tmp_path):
    from snipetech import cli

    monkeypatch.setenv("BIRDEYE_API_KEY", "be-key")
    monkeypatch.setenv("SOLANATRACKER_API_KEY", "st-key")
    with pytest.raises(SystemExit, match="switched off"):
        cli.main(["--db", str(tmp_path / "snipetech.db"), *argv])
