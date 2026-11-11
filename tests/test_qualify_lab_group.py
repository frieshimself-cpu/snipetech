"""The qualify lab measures the wallets it is asked about, and no others (11 Nov).

The by-wallet lab was handed the whole `cand` label group with the due wallets added to it. Every wallet qualify ever
measured carries that label: 22,204 on 11 Nov, about 2,500 more a day. So a lab asked about 50 wallets found the coins
any of 24,800 wallets bought (one walk of all their trades for each of the tape's 193 stretches, on one processor),
scored every one of those wallets' first buys, and kept 50 rows: 2 h 18 min a run on 10 Nov, and the first chunk of 50
on 11 Nov was still in the lab after 55 minutes. A wallet's own numbers come from its own buys alone.
"""

import pytest

from snipetech.analysis import lab as L
from snipetech.qualify.run import default_lab_runner
from tests.test_qualify_run import NOW, _svc, _tape

GATE = ("n", "mean", "ev_lower", "win_rate", "total")


def _lab(tmp_path, wallets, cand=()):
    svc, st, _data = _svc(tmp_path)
    _tape(st, wallets, coins=12)
    for w in cand:
        st.set_label(w, "candidate", "cand", "qualify")
    svc.cfg.verdict.bootstrap_samples = 50
    return svc, st


def test_the_lab_scores_only_the_wallets_it_was_asked_about(tmp_path):
    svc, st = _lab(tmp_path, ["ASKED", "OLD1", "OLD2"], cand=["OLD1", "OLD2"])  # two measured on an earlier day
    try:
        run = default_lab_runner(svc, svc.cfg)
        rows = run(["ASKED"], "+6")
        assert [r["wallet"] for r in rows] == ["ASKED"]
        assert run.info["moments"] == 12  # its own twelve first buys: 36 with the two it was not asked about
    finally:
        st.close()


def test_a_wallets_numbers_are_the_same_whoever_else_is_measured_with_it(tmp_path):
    svc, st = _lab(tmp_path, ["A", "B", "C"], cand=["C"])
    try:
        run = default_lab_runner(svc, svc.cfg)
        (alone,) = run(["A"], "+6")
        both = {r["wallet"]: r for r in run(["A", "B"], "+6")}
        assert {k: both["A"][k] for k in GATE} == {k: alone[k] for k in GATE}
        assert both["A"]["moments"] == alone["moments"]
    finally:
        st.close()


def test_a_tracked_or_kol_wallet_that_is_due_is_measured_without_a_cand_label(tmp_path):
    svc, st = _lab(tmp_path, ["KOL"])
    try:
        st.set_label("KOL", "a kol", "kol", "test")
        (row,) = default_lab_runner(svc, svc.cfg)(["KOL"], "+6")
        assert row["wallet"] == "KOL" and row["n"] == 12
        assert st.labels("kol") == {"KOL": "a kol"}
    finally:
        st.close()


def test_the_coins_of_a_wallet_group_are_found_in_one_walk_of_its_trades_not_one_a_stretch(tmp_path, monkeypatch):
    """`_plan` asked, for each of the tape's stretches, which coins the group bought in it: each ask walked every trade
    the group ever made (the wallet index has no time in it). 193 stretches, 193 walks."""
    _, st = _lab(tmp_path, ["A", "B"])
    try:
        spans = [[NOW - 7200 + k * 1200, NOW - 7200 + (k + 1) * 1200 - 60] for k in range(6)]
        st.set_coverage("tape", {"spans": spans})
        want = {(a, b): L._mints_with_buys_by(st, {"A", "B"}, a, b) for a, b in spans}
        assert sum(len(v) for v in want.values()) == 24
        reads: list[str] = []
        q = st._q

        def counted(sql, params=()):
            if "user IN" in sql:
                reads.append(sql)
            return q(sql, params)

        monkeypatch.setattr(st, "_q", counted)
        plan = L._plan(st, st.tape_spans(), {"A", "B"}, 1)
        assert len(reads) == 1
        assert {(a, b): set(mints) for a, b, _secs, mints in plan} == {k: v for k, v in want.items() if v}
    finally:
        st.close()


def test_a_group_of_thousands_is_read_a_few_hundred_wallets_at_a_time(tmp_path, monkeypatch):
    _, st = _lab(tmp_path, ["A"])
    try:
        group = {"A"} | {f"W{k}" for k in range(L.WALLETS_PER_READ * 2)}
        reads: list[int] = []
        q = st._q

        def counted(sql, params=()):
            if "user IN" in sql:
                reads.append(sql.count("?"))
            return q(sql, params)

        monkeypatch.setattr(st, "_q", counted)
        plan = L._plan(st, st.tape_spans(), group, 1)
        assert len(reads) == 3 and max(reads) <= L.WALLETS_PER_READ + 2
        assert sum(len(mints) for _a, _b, _s, mints in plan) == 12
    finally:
        st.close()


def test_a_buy_outside_every_stretch_of_the_tape_brings_no_coin_into_the_lab(tmp_path):
    _, st = _lab(tmp_path, ["A"])
    try:
        st.set_coverage("tape", {"spans": [[NOW - 7200, NOW - 6400]]})  # only the first coins lie inside it
        ((a, b, _secs, mints),) = L._plan(st, st.tape_spans(), {"A"}, 1)
        assert set(mints) == L._mints_with_buys_by(st, {"A"}, int(a), int(b)) and 0 < len(mints) < 12
    finally:
        st.close()


@pytest.mark.parametrize("wallets", [set(), {"NOBODY"}])
def test_a_group_that_bought_nothing_plans_no_coin(tmp_path, wallets):
    _, st = _lab(tmp_path, ["A"])
    try:
        assert L._plan(st, st.tape_spans(), wallets, 1) == []
    finally:
        st.close()


def test_the_stage_labels_no_wallet_it_measures(tmp_path):
    """It labelled every wallet it measured `cand`, for the lab to find them by: 22,204 labels on 11 Nov, each named
    "cand:<address>", in every list of names and in the `cand` group every default lab fires a rule on."""
    from snipetech.qualify.run import run_qualify
    from tests.test_qualify_run import _svc as svc_

    svc, st, data = svc_(tmp_path)
    try:
        _tape(st, ["NEW"], coins=12)
        st.upsert_candidate("NEW", "tape", {"positions": 12}, NOW - 3600)
        svc.cfg.verdict.bootstrap_samples = 50
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW)
        assert out["evaluated"] == 1 and st.latest_verdicts()[0]["n_tape"] == 12
        assert st.labels("cand") == {} and "labelled" not in out
    finally:
        st.close()


def test_the_labels_the_stage_wrote_before_are_removed_and_nobody_elses(tmp_path):
    from snipetech.qualify.run import run_qualify
    from tests.test_qualify_run import _svc as svc_

    svc, st, data = svc_(tmp_path)
    try:
        _tape(st, ["NEW"], coins=12)
        st.upsert_candidate("NEW", "tape", {"positions": 12}, NOW - 3600)
        for k in range(1200):  # more than one batch
            st.set_label(f"OLD{k}", f"cand:OLD{k}", "cand", "qualify")
        st.set_label("PICK", "picked by hand", "cand", "user")
        st.set_label("FOMO", "fomo 12 buys 3 SOL", "cand", "fomo")
        st.set_label("K", "a kol", "kol", "file")
        svc.cfg.verdict.bootstrap_samples = 50
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW)
        assert out["unlabelled"] == 1200
        assert st.labels("cand") == {"PICK": "picked by hand", "FOMO": "fomo 12 buys 3 SOL"}
        assert st.labels("kol") == {"K": "a kol"}
        assert run_qualify(svc, svc.cfg, data / "jobs", NOW + 60, early=True)["unlabelled"] == 0
    finally:
        st.close()


# -- only the coins that can give a moment are loaded (11 Nov, from the lab's own profile on the server) ---------------
# A qualify lab of 50 wallets, profiled: 453 s on one process, half of it database reads. It loaded 16,335 coins
# (10 million trades, each row made into an object, each coin's path built) and scored 2,206 buys. A group's moment is
# a wallet's first buy in a coin of at least the rule's size (0.1 SOL), and the plan took every coin a wallet had
# bought in at any size: a bot's thousands of dust buys. And to list a stretch's coins it first counted every trade of
# the whole stretch (`active_mints`: 113 walks, 75 s), though the group's coins were already known.

from tests.synth import Market, addr
from tests.test_qualify_run import SLOT


def _coin(st, name, buys, at=3500):
    """One more coin on the tape: the dev's buy, then (wallet, SOL, seconds after the launch) buys, a crowd and sells."""
    D = addr("dev")
    m = Market(st, addr(name), D, NOW - at, SLOT + 9000 + (hash(name) % 5000))
    m.buy(D, 1.0, 0)
    for w, sol, t in buys:
        m.buy(w, sol, t)
    for j in range(4):
        m.buy(addr(f"crowd:{name}:{j}"), 0.3 + 0.1 * j, 20 + j)
    m.sell(addr(f"crowd:{name}:0"), 1.0, 40)
    m.commit()
    return addr(name)


def _loads(st, monkeypatch):
    """Counts the coins a lab loads and the walks of a whole stretch it makes."""
    seen = {"coins": [], "walks": 0}
    real_trades, real_active = st.trades_for_mint, st.active_mints

    def trades_for_mint(mint, *a, **k):
        seen["coins"].append(mint)
        return real_trades(mint, *a, **k)

    def active_mints(*a, **k):
        seen["walks"] += 1
        return real_active(*a, **k)

    monkeypatch.setattr(st, "trades_for_mint", trades_for_mint)
    monkeypatch.setattr(st, "active_mints", active_mints)
    return seen


def test_a_coin_no_wallet_of_the_group_bought_at_the_rules_size_is_never_loaded(tmp_path, monkeypatch):
    svc, st = _lab(tmp_path, ["A"])
    try:
        run = default_lab_runner(svc, svc.cfg)
        (before,) = run(["A"], "+6")
        dust = [_coin(st, f"dust{k}", [("A", 0.05, 5)]) for k in range(30)]  # a bot's dust: never a moment
        late = _coin(st, "late", [("A", 0.05, 5), ("A", 0.5, 8)])  # its first buy is dust: no moment, but it is read
        seen = _loads(st, monkeypatch)
        (after,) = run(["A"], "+6")
        assert {k: after[k] for k in GATE} == {k: before[k] for k in GATE} and after["n"] == 12
        assert not set(dust) & set(seen["coins"]), "coins bought only in dust were loaded"
        assert late in seen["coins"] and len(set(seen["coins"])) == 13
    finally:
        st.close()


def test_a_group_lab_does_not_count_every_trade_of_a_stretch_to_list_coins_it_knows(tmp_path, monkeypatch):
    svc, st = _lab(tmp_path, ["A", "B"])
    try:
        seen = _loads(st, monkeypatch)
        rows = default_lab_runner(svc, svc.cfg)(["A", "B"], "+6")
        assert sorted(r["wallet"] for r in rows) == ["A", "B"] and seen["walks"] == 0
        plan = L._plan(st, st.tape_spans(), {"A", "B"}, 1)
        assert [mints for _a, _b, _s, mints in plan] == [sorted(L._mints_with_buys_by(st, {"A", "B"}, NOW - 7200, NOW))]
        L._plan(st, st.tape_spans(), None, 1)  # the whole market: its coins are whatever traded, counted as before
        assert seen["walks"] == 1
    finally:
        st.close()


def test_the_size_asked_of_a_groups_buys_is_the_smallest_any_of_its_rules_takes(tmp_path):
    _, st = _lab(tmp_path, ["A"])
    try:
        small = _coin(st, "small", [("A", 0.2, 5)])
        spans = st.tape_spans()
        assert small in L._plan(st, spans, {"A"}, 1, min_quote=int(0.1 * 1e9))[0][3]
        assert small not in L._plan(st, spans, {"A"}, 1, min_quote=int(0.3 * 1e9))[0][3]
        assert small in L._plan(st, spans, {"A"}, 1)[0][3]  # no size asked: every coin a wallet bought in
        kol = [(0, L.Trigger("kol", sol=1.0, group="cand")), (1, L.Trigger("kol", sol=0.25, group="cand"))]
        assert L.group_floor(kol) == int(0.25 * 1e9)
        assert L.group_floor([*kol, (2, L.Trigger("kol", group="cand"))]) == int(L.KOL_MIN_SOL * 1e9)
        assert L.group_floor([*kol, (3, L.Trigger("event", event="trending"))]) == 0  # an event rule takes any coin
    finally:
        st.close()
