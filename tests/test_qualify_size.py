"""The daily wallet test also scores each wallet at the size that pays (21 Nov 2026).

The qualify lab scores every wallet at the book's smallest fill (0.2 SOL) with no bid, where nothing can pay: the
place behind a wallet is worth about a tenth of the SOL put in, and at 0.2 SOL no bid buys it (docs/roadmap.md, the
top section). The edge is at 5 SOL with a 0.2 SOL bid, a market-cap ceiling, in the first two places: Cented and Decu
pay there on their own first buys over a week (labs a5f6b5, 15062c). Beside its verdict's numbers each measured
wallet now carries `size`: its first buys of 0.95 SOL or more under the ceiling a live copy carries (100 SOL of
market cap; no floor, since OKTech's minMcapUsd does not stop a copy in a fresh coin), scored at 5 SOL with the 0.2 bid at 2nd in line (+1x) on the money step's exit
(hold12). It qualifies nothing: the verdict is still the small fill's, and the run says which wallets pay at size."""

from snipetech.qualify.run import default_lab_runner, run_qualify
from tests.test_qualify_run import NOW, STRONG, _row, _svc, _tape


def _world(tmp_path):
    svc, st, data = _svc(tmp_path)
    _tape(st, ["BIG"], coins=12, sol=1.0)  # first buys of 1 SOL: over the 0.95 SOL floor of the size score
    _tape(st, ["SMALL"], coins=12, sol=0.5)  # 0.5 SOL: never a size moment
    svc.cfg.verdict.bootstrap_samples = 50
    return svc, st, data


def test_each_measured_wallet_also_carries_a_score_at_the_size_that_pays(tmp_path):
    svc, st, _data = _world(tmp_path)
    try:
        rows = {r["wallet"]: r for r in default_lab_runner(svc, svc.cfg)(["BIG", "SMALL"], "+6")}
        size = rows["BIG"]["size"]
        assert size["n"] == 12 and size["buy_sol"] == 5.0 and size["bid_sol"] == 0.2 and size["position"] == "+1x"
        assert size["floor_sol"] == 0.95 and size["max_mcap_sol"] == 100.0 and size["exit"].startswith("hold12s")
        assert {"mean", "ev_lower", "win_rate", "total"} <= set(size)
        assert rows["SMALL"]["size"]["n"] == 0  # its 0.5 SOL buys are under the size floor
        assert rows["BIG"]["n"] == 12 and rows["SMALL"]["n"] == 12  # the small fill's own numbers stand beside it
    finally:
        st.close()


def test_the_size_score_has_no_market_cap_floor_because_a_live_copy_has_none(tmp_path):
    """21 Nov 08:02, the floor test: OKTech's minMcapUsd did not stop a copy sniper buying fresh coins at 29 SOL of
    market cap (nor minSol a 0.487 SOL dev buy), so a live copy at size buys a wallet's buys in fresh coins too and
    the paper scores them: no floor by default. One can still be asked for ([qualify] size_min_mcap_sol), and the
    record says which was used."""
    svc, st, _data = _world(tmp_path)
    try:
        (row,) = default_lab_runner(svc, svc.cfg)(["BIG"], "+6")
        assert row["size"]["min_mcap_sol"] == 0.0 and row["size"]["n"] == 12  # its buys in fresh coins count
        svc.cfg.qualify.size_min_mcap_sol = 35.0  # asked for: every buy here is in a fresh coin
        (row,) = default_lab_runner(svc, svc.cfg)(["BIG"], "+6")
        assert row["size"]["min_mcap_sol"] == 35.0 and row["size"]["n"] == 0
        svc.cfg.qualify.size_sol = 0.0  # off: no size score at all
        (row,) = default_lab_runner(svc, svc.cfg)(["BIG"], "+6")
        assert "size" not in row
    finally:
        st.close()


def test_a_size_score_is_said_beside_the_verdict_and_qualifies_nothing(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["W1", "W2"])
        for w in ("W1", "W2"):
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        pays = {"n": 30, "mean": 0.7, "ev_lower": 0.5, "win_rate": 0.7, "total": 21.0, "buy_sol": 5.0}
        thin = {"n": 4, "mean": 2.0, "ev_lower": 0.9, "win_rate": 1.0, "total": 8.0, "buy_sol": 5.0}

        def runner(wallets, pos):  # both lose at the small fill
            return [
                dict(_row("W1", 12, -0.01, -0.02, []), size=pays),
                dict(_row("W2", 12, -0.01, -0.02, []), size=thin),
            ]

        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        assert by["W1"]["status"] != "qualified" and by["W2"]["status"] != "qualified"  # never a gate
        # kept with the wallet's lab numbers in its score record, where the scout and the agents read them
        assert st.get_score("qualify", "W1")["lab"]["size"] == pays
        assert st.get_score("qualify", "W2")["lab"]["size"] == thin
        # the run names the wallets that pay at size on enough copies (4 are too few to say anything)
        assert out["size_pass"] == [{"wallet": "W1", "n": 30, "mean": 0.7, "ev_lower": 0.5}]
    finally:
        st.close()
