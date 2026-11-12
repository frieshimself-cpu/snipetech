"""What GMGN says about one wallet, kept and put to use: how followed it is, and where its first SOL came from.

GMGN's wallet answer names the wallet that funded it (`common.fund_from_address`; a real answer of 12 Nov: the wallet
we track as Cupseyy got its first 50 SOL from the wallet GMGN lists as @Cupseyy). One operator's wallets often share
a funder, so the funder PROPOSES companions. A proposal is printed and stored and nothing else: the tape's rule
(analysis/companions.py: who buys in the wallet's block, how often and in which order) stays the judge, and no lab,
verdict or copy reads a proposal."""

import inspect
import json

import httpx
import pytest

from snipetech import cli
from snipetech import gmgn as G
from snipetech.analysis import companions as C
from snipetech.store import Store
from tests.gmgn_fake import KEY, fake_gmgn
from tests.test_gmgn_real import CUPSEY, CUPSEY2, REAL_ROUTES, TRUNOEST, real

NOW = 1_794_507_000
OTHER = "suqh5sHtr8HyJ7q8scBimULPkPpA557prMG47xCHQfK"  # a tracked wallet GMGN was asked about before
SIBLING = "J3dnz2QDVfiouCjWTXowtX9zpNzVp9eQdnTa1P1xMArB"  # a wallet our own funder record ties to the same funder
STRANGER = "12XsuLh1kqshe1ZopwnMDcEJL325wMi9hTB1uR86cui9"  # same funder, but nobody we track


def test_the_wallet_look_up_leads_with_who_it_is_in_plain_names():
    out = G.action(real(), "wallet", CUPSEY2)
    s = out["summary"]
    assert s["followed_by_gmgn_users"] == 2148 and s["tags"] == ["padre"] and s["name"] is None
    assert s["last_active_ts"] == 1794506857 and s["sol_balance"] == pytest.approx(3184.3129, abs=1e-4)
    assert s["funded_by"] == CUPSEY and s["funded_sol"] == 50.0
    assert s["realized_profit_usd"]["7d"] == pytest.approx(13952.12, abs=0.01)
    assert s["realized_profit_usd"]["all_time"] == pytest.approx(-37628.28, abs=0.01)
    assert s["win_rate"]["7d"] == pytest.approx(0.3575, abs=1e-4)
    assert s["fees_paid_usd"]["7d"] == {
        "buys": pytest.approx(11020.79, abs=0.01),
        "sells": pytest.approx(5611.40, abs=0.01),
    }
    assert "dollars" in s["units"] and "tip" in s["units"]


def test_what_gmgn_said_about_a_wallet_is_kept_for_a_day():
    st = Store(":memory:")
    try:
        assert G.known_wallet(st, CUPSEY2) is None
        G.remember_wallet(st, real().wallet_stats(CUPSEY2, "7d"), now=NOW)
        rec = G.known_wallet(st, CUPSEY2)
        assert rec["follow_count"] == 2148 and rec["tags"] == ["padre"] and rec["ts"] == NOW
        assert rec["funded_by"] == CUPSEY and rec["funded_sol"] == 50.0 and rec["funded_by_name"] is None
        assert G.known_wallet(st, CUPSEY2, max_age=86400, now=NOW + 3600)["follow_count"] == 2148
        assert G.known_wallet(st, CUPSEY2, max_age=86400, now=NOW + 90000) is None  # a day old: asked again
        # a row without the wallet's card (a profit row, a failed look-up) writes nothing over what is known
        G.remember_wallet(st, {"wallet": CUPSEY2, "follow_count": None, "tags": []}, now=NOW + 5)
        assert G.known_wallet(st, CUPSEY2)["follow_count"] == 2148
    finally:
        st.close()


def _tracked(st: Store) -> None:
    st.set_label(CUPSEY2, "Cupseyy", "wallet", "file")
    st.set_label(CUPSEY, "Cupsey main", "kol", "file")
    st.set_label(OTHER, "tracked other", "wallet", "file")
    st.set_label(SIBLING, "tracked sibling", "wallet", "file")
    # GMGN was asked about OTHER before: the same funder
    G.remember_wallet(st, {"wallet": OTHER, "follow_count": 74887, "tags": ["kol"], "funded_by": CUPSEY}, now=NOW - 500)
    G.remember_wallet(st, {"wallet": STRANGER, "follow_count": 17, "tags": [], "funded_by": CUPSEY}, now=NOW - 400)
    st.set_funder(SIBLING, CUPSEY, amount=2_000_000_000, ts=NOW - 9000, sig="sig-sibling")  # our own funder record


def test_the_funder_proposes_companions_and_changes_nothing():
    st = Store(":memory:")
    try:
        _tracked(st)
        out = G.funder(real(), st, CUPSEY2, now=NOW)
        assert out["wallet"] == CUPSEY2 and out["funded_by"] == CUPSEY and out["funded_by_name"] is None
        assert out["funded_sol"] == 50.0 and out["funded_ts"] == 1785777120
        got = {p["wallet"]: p for p in out["proposed"]}
        # the funder itself is a wallet we track; two tracked wallets share it; the stranger is no wallet of ours
        assert set(got) == {CUPSEY, OTHER, SIBLING}
        assert got[CUPSEY]["label"] == "Cupsey main" and "funded this wallet" in got[CUPSEY]["why"]
        assert "same funder" in got[OTHER]["why"] and got[OTHER]["known_from"] == "gmgn"
        assert got[SIBLING]["known_from"] == "our funder record"
        assert out["untracked_with_the_same_funder"] == 1
        assert "proposal" in out["note"] and "tape" in out["note"]
        # stored as a proposal, and only there
        kept = st.coverage(G.PROPOSED_KEY + CUPSEY2)
        assert kept["ts"] == NOW and sorted(p["wallet"] for p in kept["proposed"]) == sorted(got)
        assert C.companions(st, CUPSEY2, 0) == frozenset()  # the tape's rule: no shared block, no companion
        assert st.funder(CUPSEY2) is None  # our own funder table (the deployer families read it) is not written
        # what the tape's rule says is put beside each proposal when the caller has it
        judged = G.funder(real(), st, CUPSEY2, now=NOW, mates=frozenset({OTHER}))
        said = {p["wallet"]: p["tape_says_companion"] for p in judged["proposed"]}
        assert said == {CUPSEY: False, OTHER: True, SIBLING: False}
    finally:
        st.close()


def test_the_companion_rule_reads_no_proposal():
    src = inspect.getsource(C)
    assert "gmgn" not in src.lower() and "proposed" not in src.lower() and "coverage" not in src


def test_a_funder_that_is_a_service_proposes_nobody():
    """An exchange's or a trading app's wallet funds thousands of wallets: sharing it says nothing."""
    st = Store(":memory:")
    try:
        st.set_label(OTHER, "tracked other", "wallet", "file")
        G.remember_wallet(
            st,
            {
                "wallet": OTHER,
                "follow_count": 1,
                "tags": [],
                "funded_by": "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk",
            },
            now=NOW - 60,
        )
        out = G.funder(real(), st, TRUNOEST, now=NOW)
        assert out["funded_by_name"] == "Coinbase Hot Wallet" and out["proposed"] == []
        assert "service" in out["note"]
    finally:
        st.close()


@pytest.fixture
def gmgn(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GMGN_API_KEY", KEY)
    conf = tmp_path / "s.toml"
    conf.write_text("[gmgn]\nweight_per_sec = 50.0\n")
    db = tmp_path / "t.db"

    def run(*argv):
        seen: list[httpx.Request] = []
        monkeypatch.setattr(G, "default_transport", lambda: fake_gmgn(seen, REAL_ROUTES))
        code = cli.main(["--config", str(conf), "--db", str(db), "gmgn", *argv])
        return code, json.loads(capsys.readouterr().out), seen

    run.db = db
    return run


def test_gmgn_funder_prints_the_funder_and_the_proposals_and_keeps_them(gmgn):
    st = Store(gmgn.db)
    try:
        _tracked(st)
    finally:
        st.close()
    code, out, seen = gmgn("funder", CUPSEY2, "--json")
    assert code == 0 and out["ok"] is True and out["funded_by"] == CUPSEY
    assert {p["wallet"] for p in out["proposed"]} == {CUPSEY, OTHER, SIBLING}
    assert all(p["tape_says_companion"] is False for p in out["proposed"])  # this tape holds no trade of theirs
    assert [r.url.path for r in seen] == ["/v1/user/wallet_stats"]  # one look-up of weight 3
    st = Store(gmgn.db)
    try:
        assert st.coverage(G.PROPOSED_KEY + CUPSEY2)["funded_by"] == CUPSEY
        assert G.known_wallet(st, CUPSEY2)["follow_count"] == 2148  # the look-up is kept
    finally:
        st.close()
    code, out, _ = gmgn("funder", "--json")
    assert code == 2 and "address" in out["error"]


def test_a_wallet_look_up_is_kept_too(gmgn):
    code, out, _ = gmgn("wallet", CUPSEY2, "--json")
    assert code == 0 and out["summary"]["followed_by_gmgn_users"] == 2148
    st = Store(gmgn.db)
    try:
        rec = G.known_wallet(st, CUPSEY2)
        assert rec["follow_count"] == 2148 and rec["funded_by"] == CUPSEY
    finally:
        st.close()
