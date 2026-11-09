"""FOMO app traders, found on our own tape (owner, 8 Nov: "make sure it checks all fomo wallets"). FOMO pays its users'
network fees from one wallet, so a trade whose transaction that wallet paid for is a FOMO user's trade; the busiest
users are labelled cand so qualify scores them at the live copy landing."""

import time

from snipetech.analysis.fomo_wallets import FOMO_FEE_PAYER, fomo_wallets
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())


def test_fomo_users_are_the_buyers_whose_transactions_fomo_paid_for():
    st = Store(":memory:")
    try:
        m = Market(st, addr("fw-coin"), addr("fw-dev"), NOW - 3600, 40_000_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        sigs = []
        for k in range(12):
            sigs.append(m.buy(addr("fomo-user"), 0.2, 2 + k).sig)
        for k in range(3):
            sigs.append(m.buy(addr("fomo-small"), 0.1, 30 + k).sig)
        m.buy(addr("not-fomo"), 1.0, 50)
        m.commit()
        st._exec(
            "CREATE TABLE IF NOT EXISTS tx_meta (sig TEXT PRIMARY KEY, slot INTEGER NOT NULL, fee_payer TEXT, "
            "priority INTEGER, tip INTEGER, data TEXT NOT NULL)"
        )
        for s in sigs:
            st._exec("INSERT INTO tx_meta (sig, slot, fee_payer, priority, tip, data) VALUES (?,?,?,0,0,'{}')",
                     (s, 40_000_010, FOMO_FEE_PAYER))  # fmt: skip
        rows = fomo_wallets(st, NOW, days=1, min_buys=10)
        assert [r["wallet"] for r in rows] == [addr("fomo-user")]
        assert rows[0]["buys"] == 12 and abs(rows[0]["buy_sol"] - 2.4) < 1e-6
        assert {r["wallet"] for r in fomo_wallets(st, NOW, days=1, min_buys=1)} == {
            addr("fomo-user"),
            addr("fomo-small"),
        }
    finally:
        st.close()


def test_cli_labels_the_busiest_fomo_wallets_as_candidates_and_the_agents_may_run_it(tmp_path, capsys):
    import json

    from snipetech import cli
    from snipetech.operator.jobs import RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    assert "fomo-wallets" in RESEARCH_COMMANDS and "fomo-wallets" in ALLOWED
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        m = Market(st, addr("fw-coin2"), addr("fw-dev2"), NOW - 3600, 41_000_000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        sigs = [m.buy(addr("fomo-a"), 0.3, 2 + k).sig for k in range(10)]
        m.commit()
        for s in sigs:
            st._exec("INSERT INTO tx_meta (sig, slot, fee_payer, priority, tip, data) VALUES (?,?,?,0,0,'{}')",
                     (s, 41_000_010, FOMO_FEE_PAYER))  # fmt: skip
    finally:
        st.close()
    assert cli.main(["--db", str(db), "fomo-wallets", "--days", "1", "--min-buys", "10", "--label", "5", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["labelled"] == [addr("fomo-a")]
    st = Store(db)
    try:
        assert addr("fomo-a") in st.labels("cand")
        (c,) = [c for c in st.candidates() if c["wallet"] == addr("fomo-a")]  # in qualify's queue, never evaluated
        assert "fomo" in c["sources"] and c["status"] == "candidate"
    finally:
        st.close()


def test_screened_wallets_jump_qualifys_queue_ahead_of_older_discovery_candidates():
    """9 Nov: a 900-wallet qualify run never reached the 84 FOMO wallets: never-evaluated candidates go oldest first,
    and thousands of older discovery candidates sat ahead. The owner's and the screens' wallets go first."""
    st = Store(":memory:")
    try:
        for i in range(5):
            st.upsert_candidate(addr(f"old-{i}"), "solanatracker", {}, NOW - 86400 + i)
        st.upsert_candidate(addr("fomo-new"), "fomo", {}, NOW)
        st.upsert_candidate(addr("early-new"), "early-leaders", {}, NOW)
        st.upsert_candidate(addr("owner-pick"), "owner", {}, NOW)
        first3 = {c["wallet"] for c in st.candidates(due_before=NOW + 1, limit=3)}
        assert first3 == {addr("fomo-new"), addr("early-new"), addr("owner-pick")}
    finally:
        st.close()
