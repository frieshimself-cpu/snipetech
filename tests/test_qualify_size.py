"""The daily wallet test also scores each wallet at the size that pays (21 Nov 2026).

The qualify lab scores every wallet at the book's smallest fill (0.2 SOL) with no bid, where nothing can pay: the
place behind a wallet is worth about a tenth of the SOL put in, and at 0.2 SOL no bid buys it (docs/roadmap.md, the
top section). The edge is at 5 SOL with a 0.2 SOL bid, a market-cap ceiling, in the first two places: Cented and Decu
pay there on their own first buys over a week (labs a5f6b5, 15062c). Beside its verdict's numbers each measured
wallet now carries `size`: its buys of 0.95 SOL or more, its repeats in a coin too (22 Nov: what a copy sniper sent
minSol 0.95 copies), under the ceiling a live copy carries (100 SOL of market cap; no floor, since OKTech's
minMcapUsd does not stop a copy in a fresh coin), scored at 5 SOL with the 0.2 bid at 2nd in line (+1x) on the money
step's exit (hold12). It qualifies nothing: the verdict is still the small fill's, and the run says which wallets pay
at size."""

from snipetech.qualify import run as R
from snipetech.qualify.run import default_lab_runner, run_qualify, size_record
from tests.synth import Market, addr
from tests.test_qualify_run import NOW, SLOT, STRONG, _row, _svc, _tape


def _repeat_buyer(st, w, coins=12, first=1.0, repeats=(2.0, 2.0, 2.0)):
    """`w` opens each coin with `first` SOL after the creator's buy and a crowd, then buys again with each of
    `repeats`, two small buys after each of its own: a copy sniper sent minSol 0.95 copies every one of its buys."""
    D = addr("dev")
    for k in range(coins):
        m = Market(st, addr(f"{w}:r{k}"), D, NOW - 7000 + k * 250, SLOT + k * 625)
        m.buy(D, 1.0, 0)
        m.buy(w, first, 5)
        for j in range(4):
            m.buy(addr(f"{w}:r{k}:f{j}"), 0.3 + 0.1 * j, 6 + j)
        for j, sol in enumerate(repeats):
            m.buy(w, sol, 12 + 4 * j)
            for i in range(2):
                m.buy(addr(f"{w}:r{k}:a{j}{i}"), 0.3, 13 + 4 * j + i)
        m.sell(w, 1.0, 60)
        m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 7200, NOW]]})


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
        _tape(st, ["W1", "W2", "W3"])
        for w in ("W1", "W2"):
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        st.upsert_candidate("W3", "coinfirst", {"hits": 1}, NOW - 3600)  # no history from any source: it needs 20
        cfg = svc.cfg
        pays = size_record(cfg, {"n": 30, "mean": 0.7, "ev_lower": 0.5, "win_rate": 0.7, "total": 21.0})
        thin = size_record(cfg, {"n": 4, "mean": 2.0, "ev_lower": 0.9, "win_rate": 1.0, "total": 8.0})
        short = size_record(cfg, {"n": 15, "mean": 0.7, "ev_lower": 0.5, "win_rate": 0.7, "total": 10.5})

        def runner(wallets, pos):  # all lose at the small fill
            return [
                dict(_row("W1", 12, -0.01, -0.02, []), size=pays),
                dict(_row("W2", 12, -0.01, -0.02, []), size=thin),
                dict(_row("W3", 25, -0.01, -0.02, []), size=short),
            ]

        out = run_qualify(svc, cfg, data / "jobs", NOW, lab_runner=runner)
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        assert all(by[w]["status"] != "qualified" for w in ("W1", "W2", "W3"))  # never a gate
        # kept with the wallet's lab numbers in its score record, where the scout and the agents read them
        assert st.get_score("qualify", "W1")["lab"]["size"] == pays
        assert st.get_score("qualify", "W2")["lab"]["size"] == thin
        # the run names the wallets that pay at size on the copies each one needs (4 are too few to say anything; 15
        # are too few for a wallet without a strong history, as for its verdict: audit of 22 Nov, qualify#2), each
        # with the size, the bid and the place in line it was scored at (CLAUDE.md rule 2: a paper result names them)
        assert out["size_pass"] == [{"wallet": "W1", "n": 30, "mean": 0.7, "ev_lower": 0.5, "buy_sol": 5.0,
                                     "bid_sol": 0.2, "position": "+1x", "floor_sol": 0.95}]  # fmt: skip
    finally:
        st.close()


def test_the_size_score_takes_every_buy_a_copy_sniper_at_the_size_floor_copies(tmp_path):
    """Audit of 22 Nov (qualify#2): a copy sniper sent minSol 0.95 copies every buy of 0.95 SOL or more, the repeats
    in a coin too (20 Nov: 81 % of Cupseyy's buys of 0.95 SOL or more in a week were repeats), while the size score
    took first buys only, and then each wallet's own floor above its top-ups. TOPS opens 12 coins with 1 SOL and buys
    again three times with 2 SOL: 48 buys a live copy takes. Scored on its first buys it had 12 copies; with the
    repeats and its own floor (above its 1 SOL openers, from its 2 SOL buys) it would have 36. At the size floor, as
    the sniper copies: all 48."""
    svc, st, _data = _svc(tmp_path)
    try:
        _repeat_buyer(st, "TOPS")
        svc.cfg.verdict.bootstrap_samples = 50
        (row,) = default_lab_runner(svc, svc.cfg)(["TOPS"], "+6")
        assert row["size"]["n"] == 48 and row["size"]["floor_sol"] == 0.95
        assert row["n"] == 12  # the verdict's own numbers: the wallet's first buys, as before
    finally:
        st.close()


def test_a_lab_that_never_reads_the_size_score_does_not_make_it(tmp_path, monkeypatch):
    """Audit of 22 Nov (qualify#2): the size score took 268 s of a 900 s qualify lab, and the stopped-wallets study
    paid it on every lab without reading it."""
    from snipetech.qualify import stopped as S

    svc, st, data = _world(tmp_path)
    try:
        made = []
        monkeypatch.setattr(R, "size_scores", lambda *a, **k: made.append(a[3]) or {})
        (row,) = default_lab_runner(svc, svc.cfg, with_size=False)(["BIG"], "+6")
        assert "size" not in row and made == []
        asked = {}
        real = S.default_lab_runner

        def runner(svc_, cfg_, **kw):  # the study's own lab, with what it asked for kept
            asked.update(kw)
            return real(svc_, cfg_, **kw)

        monkeypatch.setattr(S, "default_lab_runner", runner)
        st.put_verdict({"wallet": "BIG", "computed_at": NOW - 3600, "landing_pos": "+3x", "n_tape": 30,
                        "mean_sol": 0.05, "lower_sol": 0.04, "status": "candidate", "next_review": NOW + 86400,
                        "reasons": ["crowded: gap to the 3rd follower 23.7 % > 15 %"], "gap3_pct": 23.7})  # fmt: skip
        out = S.stopped_wallets(svc, svc.cfg, data / "jobs", NOW)
        assert out["wallets"] == 1 and asked == {"with_size": False} and made == []
    finally:
        st.close()
