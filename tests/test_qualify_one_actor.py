"""One candidate an actor in qualify (idea I66, 13 Nov 2026).

Wallets that buy together passed as separate candidates, each with a verdict, a forward record and, once qualified,
a sniper of its own on the one signal. Now, of the wallets of one actor that pass every in-sample gate, one is the
candidate: the wallet a sniper already copies, else the one already picked, else the one whose buy comes first in the
block. The others get a verdict that says "same actor as <wallet>: one signal, one sniper", which the book never arms,
and start no forward record of their own. No verdict is deleted, the leader's numbers are untouched, and a wallet a
sniper may be copying is never marked (the book would stop its sniper)."""

import pytest

from snipetech.book.policy import buying_book_wallets
from snipetech.book.run import _bookable
from snipetech.book.state import record_copying
from snipetech.qualify.one_actor import SAME_ACTOR
from snipetech.qualify.run import default_lab_runner, run_qualify
from tests.synth import Market, addr
from tests.test_qualify_run import NOW, SLOT, STRONG, _svc

LEAD, TAIL, SOLO = addr("actor-lead"), addr("actor-tail"), addr("actor-solo")
PLAN = {"min_leader_sol": 0.3, "exit_name": "lab", "lab_version": 2}


def _tape(st, pairs=((LEAD, TAIL),), solo=(SOLO,), coins=12, t0=NOW - 7000, ahead=4):
    """Each pair buys the same `coins` coins in the same block, the first of the pair one place ahead of the second on
    nine coins of twelve (`ahead` 2: on six of twelve, so the tape cannot tell who leads); each wallet of `solo` buys
    coins of its own. A small crowd follows in later slots."""
    D = addr("dev")
    for i, group in enumerate([*pairs, *[(w,) for w in solo]]):
        for k in range(coins):
            m = Market(st, addr(f"{group[0]}:{k}"), D, t0 + i * 100 + k * 250, SLOT + i * 250 + k * 625)
            m.buy(D, 1.0, 0)
            order = group if k % ahead else group[::-1]
            for j, w in enumerate(order):
                m.buy(w, 0.5, 5).pos = 200 + j
            for j in range(4):
                m.buy(addr(f"crowd{i}{k}{j}"), 0.3 + 0.1 * j, 6 + j)
            for w in group:
                m.sell(w, 1.0, 30)
            m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 40 * 3600, NOW]]})


def _row(w, lower=0.03, moments=()):
    return {"wallet": w, "n": 35, "mean": 0.03, "ev_lower": lower, "win_rate": 1.0, "total": 1.05, "exit": "x",
            "min_leader_sol": 0.3, "moments": [list(m) for m in moments]}  # fmt: skip


def _runner(lower=None, moments=None):
    return lambda ws, pos: [_row(w, (lower or {}).get(w, 0.03), (moments or {}).get(w, ())) for w in ws]


def _latest(st):
    return {v["wallet"]: v for v in st.latest_verdicts()}


def _same_as(v):
    return next((r for r in v["reasons"] if r.startswith(SAME_ACTOR)), None)


def test_two_wallets_that_buy_together_give_one_candidate_and_the_other_says_so(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        record_copying(st, set(), NOW - 120)
        for w in (TAIL, LEAD, SOLO):  # the follower is first in the queue: the block order decides, not the queue
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        by = _latest(st)
        lead, tail, solo = by[LEAD], by[TAIL], by[SOLO]
        # the leader is the candidate, exactly as a wallet alone is: picked now, its forward check pending
        assert lead["status"] == "candidate" and lead["forward"]["status"] == "pending" and _same_as(lead) is None
        assert solo["status"] == "candidate" and solo["forward"]["status"] == "pending" and _same_as(solo) is None
        # the other wallet of the actor: a verdict of its own, its numbers kept, never armable, no forward record
        assert tail["status"] == "candidate" and tail["forward"] is None
        assert _same_as(tail).startswith(f"same actor as {LEAD}: one signal, one sniper")
        assert (tail["n_tape"], tail["mean_sol"], tail["lower_sol"]) == (35, 0.03, 0.03) and tail["copy_plan"]
        assert tail["next_review"] == NOW + svc.cfg.qualify.review_qualified_h * 3600
        assert _bookable(st) == []  # nothing the book would arm
        assert out["same_actor"] == [{"wallet": TAIL, "same_as": LEAD, "forward": "none started",
                                      "why": out["same_actor"][0]["why"]}]  # fmt: skip
        assert "first in the block on 9 of the 12 coins" in out["same_actor"][0]["why"]
        assert out["candidate"] == 3 and out["forward_pending"] == 2
        # what the next run and the book read: each wallet's actor, kept with its numbers
        block = st.get_score("qualify", TAIL)["actor"]
        assert block["same_as"] == LEAD and LEAD in block["mates"]
        assert TAIL in st.get_score("qualify", LEAD)["actor"]["mates"]
        assert st.get_score("qualify", SOLO)["actor"] == {"mates": []}
    finally:
        st.close()


def test_the_leaders_verdict_is_the_one_it_gets_alone(tmp_path):
    def run(sub, candidates):
        svc, st, data = _svc(tmp_path / sub)
        try:
            _tape(st)
            record_copying(st, set(), NOW - 120)
            for w in candidates:
                st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
            run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
            v = dict(_latest(st)[LEAD])
            v.pop("id")
            return v
        finally:
            st.close()

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert run("a", [TAIL, LEAD, SOLO]) == run("b", [LEAD])


def test_a_second_wallet_of_an_actor_already_under_test_starts_no_forward_record(tmp_path):
    """The follower was picked days ago and is under its forward test. The wallet that leads it in the block comes up
    later: the actor is under test already, so it gets no record of its own, whatever the block order."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        record_copying(st, set(), NOW - 120)
        st.upsert_candidate(TAIL, "solanatracker", STRONG, NOW - 3600)
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        assert _latest(st)[TAIL]["forward"]["status"] == "pending"
        later = NOW + 3 * 3600
        record_copying(st, set(), later - 120)
        st.upsert_candidate(LEAD, "solanatracker", STRONG, later - 60)
        out = run_qualify(svc, svc.cfg, data / "jobs", later, lab_runner=_runner())
        by = _latest(st)
        assert by[LEAD]["status"] == "candidate" and by[LEAD]["forward"] is None
        assert _same_as(by[LEAD]).startswith(f"same actor as {TAIL}: one signal, one sniper")
        assert "already under test" in _same_as(by[LEAD])
        assert by[TAIL]["forward"]["status"] == "pending" and _same_as(by[TAIL]) is None  # untouched
        assert [x["wallet"] for x in out["same_actor"]] == [LEAD]
    finally:
        st.close()


def _both_under_test(st, picked, lower=0.006, wallets=(LEAD, TAIL), due=None):
    """The state before 13 Nov: both wallets of the pair picked, each with a forward record of its own."""
    for w in wallets:
        st.upsert_candidate(w, "solanatracker", STRONG, picked - 3600)
        fwd = {"picked_at": picked, "tape_hours": 1.0, "n": 3, "mean": 0.01, "lower": -0.01, "win_rate": 0.6,
               "status": "pending", "min_n": 20, "min_hours": 6.0, "first_picked_at": picked, "first_n": 3,
               "first_mean": 0.01, "off_tape": 0}  # fmt: skip
        st.put_verdict({"wallet": w, "computed_at": picked, "landing_pos": "+3x", "n_tape": 30, "mean_sol": 0.02,
                        "lower_sol": lower, "score": 0.006, "status": "candidate",
                        "next_review": (due or {}).get(w, picked + 86400),
                        "copy_plan": PLAN, "forward": fwd, "reasons": ["forward pending: 3 later copies"]})  # fmt: skip


def test_two_wallets_already_under_test_keep_their_histories_and_one_stays_the_candidate(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked)
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]  # both passed their forward test meanwhile
        runner = _runner(moments={LEAD: after, TAIL: after})
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        by = _latest(st)
        assert by[LEAD]["status"] == "qualified" and by[LEAD]["forward"]["status"] == "passed"
        assert _same_as(by[LEAD]) is None and [v["wallet"] for v in _bookable(st)] == [LEAD]
        # the other keeps its own forward record, running since its own pick (nothing merged, nothing deleted) ...
        assert by[TAIL]["forward"]["picked_at"] == picked and by[TAIL]["forward"]["n"] == 24
        assert by[TAIL]["forward"]["status"] == "passed"
        assert len(st.get_score("qualify", TAIL)["record"]["copies"]) == 24
        # ... and is not armable: one signal, one sniper
        assert by[TAIL]["status"] == "candidate" and _same_as(by[TAIL]).startswith(f"same actor as {LEAD}: one signal")
        assert out["same_actor"][0]["forward"] == "its own record kept"
        assert out["qualified"] == 1 and out["candidate"] == 1
        assert st.get_score("qualify", LEAD)["actor"]["leads"] is True

    finally:
        st.close()


def test_the_candidate_does_not_change_hands_when_the_lower_bounds_cross(tmp_path):
    """Two wallets under test that buy first as often as each other: the lower bound chooses once (equal: the first by
    address), and the choice is kept when the other one measures better a day on."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ahead=2)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked)
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={LEAD: after, TAIL: after}))
        first, other = sorted([LEAD, TAIL])
        by = _latest(st)
        assert by[first]["status"] == "qualified" and by[other]["status"] == "candidate" and _same_as(by[other])
        assert "lower bound" in _same_as(by[other])
        later = NOW + 25 * 3600
        record_copying(st, set(), later - 120)
        runner = _runner(lower={first: 0.004, other: 0.05}, moments={LEAD: after, TAIL: after})
        run_qualify(svc, svc.cfg, data / "jobs", later, lab_runner=runner)
        by = _latest(st)
        assert by[first]["computed_at"] == later == by[other]["computed_at"]
        assert by[first]["status"] == "qualified" and _same_as(by[first]) is None
        assert by[other]["status"] == "candidate" and "candidate already" in _same_as(by[other])
        assert [v["wallet"] for v in _bookable(st)] == [first]
        assert st.get_score("qualify", first)["actor"]["leads"] is True
    finally:
        st.close()


def test_when_the_candidate_stops_passing_the_other_wallet_takes_its_place(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked)
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={LEAD: after, TAIL: after}))
        assert [v["wallet"] for v in _bookable(st)] == [LEAD]
        later = NOW + 25 * 3600
        record_copying(st, set(), later - 120)
        lost = [{"wallet": LEAD, "n": 40, "mean": -0.02, "ev_lower": -0.03, "win_rate": 0.2, "total": -0.8, "exit": "x"},
                _row(TAIL, moments=after)]  # fmt: skip
        run_qualify(
            svc, svc.cfg, data / "jobs", later, lab_runner=lambda ws, pos: [r for r in lost if r["wallet"] in ws]
        )
        by = _latest(st)
        assert by[LEAD]["status"] == "retired"
        assert by[TAIL]["status"] == "qualified" and _same_as(by[TAIL]) is None  # on its own record, kept all along
        assert by[TAIL]["forward"]["picked_at"] == picked
        assert [v["wallet"] for v in _bookable(st)] == [TAIL]
    finally:
        st.close()


def _armed(st, wallet, status="running", at=NOW - 25 * 3600):
    """`wallet` qualified a day ago and the book made a sniper for it."""
    name = f"snipetech copy book {wallet[:6]} arm:control"
    st.upsert_candidate(wallet, "solanatracker", STRONG, at - 3600)
    st.put_verdict({"wallet": wallet, "computed_at": at, "landing_pos": "+3x", "n_tape": 35, "mean_sol": 0.0271,
                    "lower_sol": 0.0049, "score": 0.01, "status": "qualified", "next_review": NOW - 3600,
                    "copy_plan": PLAN,
                    "forward": {"picked_at": at - 86400, "status": "passed", "n": 24, "mean": 0.02, "lower": 0.01}})  # fmt: skip
    st.add_oktech_sniper({"id": f"s-{wallet[:4]}", "external_id": f"e-{wallet[:4]}", "kind": "copy", "source": wallet,
                          "name": name, "config_hash": "h", "created_at": at,
                          "payload": {"mode": "ctbot", "ctbotWallets": [{"address": wallet}]}})  # fmt: skip
    return {"id": f"s-{wallet[:4]}", "name": name, "status": status}


def test_the_wallet_a_sniper_copies_is_the_actors_candidate_whatever_the_block_order(tmp_path):
    """The book copies the follower. The block's leader passes every gate: it yields, and the copied wallet's verdict
    is not touched by the rule, so the book's plan keeps its sniper."""
    from snipetech.book.policy import BookInputs, plan_book

    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        sniper = _armed(st, TAIL)
        st.upsert_candidate(LEAD, "solanatracker", STRONG, NOW - 3600)
        record_copying(st, buying_book_wallets([sniper]), NOW - 120)
        after = [[NOW - 24 * 3600 + 600 * k, 0.03] for k in range(60)]  # since its pick
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={TAIL: after, LEAD: after}))
        by = _latest(st)
        assert by[TAIL]["status"] == "qualified" and _same_as(by[TAIL]) is None
        assert by[LEAD]["status"] == "candidate" and by[LEAD]["forward"] is None
        assert _same_as(by[LEAD]).startswith(f"same actor as {TAIL}: one signal, one sniper")
        assert "a sniper of the book copies it" in _same_as(by[LEAD])
        assert [x["wallet"] for x in out["same_actor"]] == [LEAD]
        inp = BookInputs(equity_sol=10.0, day_realised_sol=0.0, day_risk_used_sol=0.0, qualified=_bookable(st), live={},
                         bid_model={}, snipers=[sniper], holdings=[], now=NOW)  # fmt: skip
        assert [(a.kind, a.wallet) for a in plan_book(inp, svc.cfg.book).actions] == [("keep", TAIL)]
    finally:
        st.close()


def test_a_wallet_a_sniper_may_copy_is_never_marked(tmp_path):
    """Both wallets of the pair qualified and the book made a sniper for each before 13 Nov. Marking either would have
    the book drop a running sniper: neither is marked, whether the book's reading of OKTech is fresh (both copied),
    says nothing of the second (its sniper row is still there) or is unknown."""
    for case, reading in (("both", "both"), ("row-only", "first"), ("unknown", None)):
        (tmp_path / case).mkdir()
        svc, st, data = _svc(tmp_path / case)
        try:
            _tape(st)
            snipers = [_armed(st, LEAD), _armed(st, TAIL)]
            if reading == "both":
                record_copying(st, buying_book_wallets(snipers), NOW - 120)
            elif reading == "first":
                record_copying(st, buying_book_wallets(snipers[:1]), NOW - 120)
            after = [[NOW - 24 * 3600 + 600 * k, 0.03] for k in range(60)]  # since its pick
            out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={TAIL: after, LEAD: after}))
            by = _latest(st)
            assert {w: by[w]["status"] for w in (LEAD, TAIL)} == {LEAD: "qualified", TAIL: "qualified"}, case
            assert _same_as(by[LEAD]) is None and _same_as(by[TAIL]) is None and "same_actor" not in out, case
        finally:
            st.close()


def test_wallets_without_companions_get_the_verdicts_they_always_got(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        a, b = addr("alone-a"), addr("alone-b")
        _tape(st, pairs=(), solo=(a, b))
        record_copying(st, set(), NOW - 120)
        for w in (a, b):
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        by = _latest(st)
        assert all(by[w]["forward"]["status"] == "pending" and _same_as(by[w]) is None for w in (a, b))
        assert "same_actor" not in out and out["forward_pending"] == 2
    finally:
        st.close()


def test_the_real_lab_hands_its_companions_to_the_rule(tmp_path):
    """The qualify lab reads each wallet's companions to score its copies behind them; the rule takes them from it
    instead of reading the tape a second time."""
    svc, st, _data = _svc(tmp_path)
    try:
        _tape(st)
        svc.cfg.verdict.bootstrap_samples = 50
        run = default_lab_runner(svc, svc.cfg)
        assert run.mates is None
        rows = run([TAIL, LEAD, SOLO], "+3x")
        assert {r["wallet"] for r in rows} == {TAIL, LEAD, SOLO}
        assert run.mates == {LEAD: frozenset({TAIL}), TAIL: frozenset({LEAD}), SOLO: frozenset()}
    finally:
        st.close()


def test_when_the_candidate_falls_in_a_run_of_its_own_the_wallets_behind_it_are_due_at_once(tmp_path):
    """The candidate is reviewed alone and loses its place. The wallet that yielded to it is not due for a day: its
    review date is put at now, and the queue takes a wallet that says "same actor as" with the qualified wallets and
    the due forward checks, ahead of the backlog of wallets never judged."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked)
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={LEAD: after, TAIL: after}))
        assert _same_as(_latest(st)[TAIL]) and _latest(st)[TAIL]["next_review"] == NOW + 24 * 3600
        for i in range(8):
            st.upsert_candidate(addr(f"never-judged-{i}"), "coinfirst", {}, NOW - 5000 + i)
        later = NOW + 3 * 3600
        st.review_now([LEAD], later)
        assert [c["wallet"] for c in st.candidates(due_before=later, limit=1)] == [LEAD]
        record_copying(st, set(), later - 120)
        lost = {"wallet": LEAD, "n": 40, "mean": -0.02, "ev_lower": -0.03, "win_rate": 0.2, "total": -0.8, "exit": "x"}
        seen = []
        run_qualify(svc, svc.cfg, data / "jobs", later, max_wallets=1,
                    lab_runner=lambda ws, pos: seen.extend(ws) or [lost])  # fmt: skip
        assert seen == [LEAD] and _latest(st)[LEAD]["status"] == "retired"
        tail = _latest(st)[TAIL]
        assert tail["next_review"] == later and _same_as(tail)  # the verdict itself is as it was, only due
        assert [c["wallet"] for c in st.candidates(due_before=later + 1, limit=1)] == [TAIL]
        # the next lab, two hours on: it takes the place, on its own forward record
        again = later + 2 * 3600 + 60
        record_copying(st, set(), again - 120)
        run_qualify(svc, svc.cfg, data / "jobs", again, max_wallets=1, lab_runner=_runner(moments={TAIL: after}))
        tail = _latest(st)[TAIL]
        assert tail["status"] == "qualified" and _same_as(tail) is None and tail["forward"]["picked_at"] == picked
    finally:
        st.close()


def test_a_wallet_that_yields_is_reviewed_with_the_qualified_ones_not_behind_the_backlog(tmp_path):
    st = _svc(tmp_path)[1]
    try:
        for i in range(8):
            st.upsert_candidate(addr(f"backlog-{i}"), "coinfirst", {}, NOW - 5000 + i)
        for w, reasons in ((TAIL, ["no history", f"{SAME_ACTOR} {LEAD}: one signal, one sniper. Not armed"]),
                           (SOLO, ["n 12 < 20 at +3x"])):  # fmt: skip
            st.upsert_candidate(w, "tape", {}, NOW - 9000)
            st.put_verdict({"wallet": w, "computed_at": NOW - 86400, "status": "candidate", "next_review": NOW - 60,
                            "score": 0.0, "n_tape": 30, "copy_plan": PLAN, "reasons": reasons})  # fmt: skip
        due = [c["wallet"] for c in st.candidates(due_before=NOW, limit=4)]
        assert due[0] == TAIL and SOLO in due[1:]  # an ordinary review keeps its one slot in four
    finally:
        st.close()


def test_three_wallets_of_one_actor_already_under_test_leave_one_candidate(tmp_path):
    """13 Nov on the server: 5Ah8, 6gRW and FvHw, picked in one run, each under a forward test of its own."""
    svc, st, data = _svc(tmp_path)
    try:
        third = addr("actor-third")
        _tape(st, pairs=((LEAD, TAIL, third),), solo=())
        picked = NOW - 30 * 3600
        _both_under_test(st, picked, wallets=(third, TAIL, LEAD))
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
        out = run_qualify(
            svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments=dict.fromkeys((LEAD, TAIL, third), after))
        )
        by = _latest(st)
        assert [v["wallet"] for v in _bookable(st)] == [LEAD] and _same_as(by[LEAD]) is None
        for w in (TAIL, third):
            assert by[w]["status"] == "candidate" and _same_as(by[w]).startswith(f"same actor as {LEAD}: one signal")
            assert by[w]["forward"]["picked_at"] == picked  # its own record, kept
        assert sorted(x["wallet"] for x in out["same_actor"]) == sorted([TAIL, third])
        assert sorted(st.get_score("qualify", LEAD)["actor"]["over"]) == sorted([TAIL, third])
    finally:
        st.close()


@pytest.mark.parametrize("first, second", [(LEAD, TAIL), (TAIL, LEAD)])
def test_a_pair_under_test_that_is_reviewed_in_two_runs_ends_with_one_candidate(tmp_path, first, second):
    """EGLz and HVM3 on 13 Nov: both under test, reviewed hours apart. Whichever comes up first, the block's leader
    is the candidate once both have been reviewed, and at no time is the other one armable beside it."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked, due={first: NOW - 60, second: NOW + 3 * 3600})
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
        runner = _runner(moments={LEAD: after, TAIL: after})
        seen = []
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda ws, pos: seen.extend(ws) or runner(ws, pos))
        assert seen == [first]
        assert [v["wallet"] for v in _bookable(st)] == ([LEAD] if first == LEAD else [])  # never the follower
        later = NOW + 4 * 3600
        record_copying(st, set(), later - 120)
        run_qualify(svc, svc.cfg, data / "jobs", later, lab_runner=runner)
        by = _latest(st)
        assert [v["wallet"] for v in _bookable(st)] == [LEAD]
        assert by[TAIL]["status"] == "candidate" and _same_as(by[TAIL]).startswith(f"same actor as {LEAD}")
        assert _same_as(by[LEAD]) is None and by[LEAD]["forward"]["picked_at"] == picked
    finally:
        st.close()


# -- packs: wallets whose buys come in one transaction (19 Nov 2026) --------------------------------------------------

P1, P2 = addr("pack-one"), addr("pack-two")
IN_ONE_TX = "They buy in one transaction on 12 of this wallet's {} coins. "


def _pack_tape(st, coins=12, alone=0, crew=False, t0=NOW - 7000):
    """P1 and P2 buy `coins` coins in ONE transaction: one signature and one block place, as the tape writes them
    (19 Nov: 8D92Hh and 3AQ2Ha at place 440 of slot 471417964). `crew`: with a third wallet in that transaction and
    three more transactions of four, four and three wallets back to back behind it, the same fifteen wallets on every
    coin (the shape of that day's pack). `alone`: as many more coins each, bought in one transaction with a wallet
    seen once. A small crowd follows in later blocks."""
    D = addr("dev")
    n = 0

    def coin(tag, txs):
        nonlocal n
        m = Market(st, addr(f"{tag}:{n}"), D, t0 + n * 60, SLOT + n * 150)
        m.buy(D, 1.0, 0)
        for j, tx in enumerate(txs):
            for w in tx:
                m.buy(w, 0.5, 5, sig=addr(f"{tag}:{n}:tx{j}")).pos = 200 + j
        for j in range(4):
            m.buy(addr(f"{tag}-crowd{n}{j}"), 0.3 + 0.1 * j, 6 + j)
        for w in txs[0]:
            m.sell(w, 1.0, 30)
        m.commit()
        n += 1

    links = [[addr(f"pack-crew-{j}{i}") for i in range(size)] for j, size in enumerate((4, 4, 3))] if crew else []
    for _ in range(coins):
        coin("pack", [[P1, P2, *([addr("pack-crew-x")] if crew else [])], *links])
    for who in (P1, P2):
        for _ in range(alone):
            coin(f"pack-alone-{who[:4]}", [[who, addr(f"pack-once-{who[:4]}-{n}")]])
    st.set_coverage("tape", {"spans": [[NOW - 40 * 3600, NOW]]})


def _judge_the_pack(st, svc, data):
    """Both wallets were picked 30 hours ago and both have passed their forward test since: the state in which the
    book made a sniper for each. Returns the run's answer, the wallet that yields and the one that holds the place."""
    picked = NOW - 30 * 3600
    _both_under_test(st, picked, wallets=(P1, P2))
    record_copying(st, set(), NOW - 120)
    after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
    out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={P1: after, P2: after}))
    said = out.get("same_actor") or [{}]
    return out, said[0].get("wallet"), said[0].get("same_as")


def test_two_wallets_that_buy_in_one_transaction_give_one_candidate_and_the_reason_says_so(tmp_path):
    """19 Nov 2026: the book made a sniper each for 3AQ2Ha, 8D92Hh and CD4Taz, three wallets of one operator whose
    buys come in the same packs: three copies on one signal. The companion rule never sees two wallets of one
    transaction (they stand at one block place, and it asks for a place behind). A review that day ran two such
    wallets through this stage on 12 of 12 coins: the twin rule joined them by their lists of coins, in the words
    "They are twins" and "its buy comes first in the block on 12 of the 12 coins they share". They are one actor by
    their packs, and the reason says what the tape shows."""
    from snipetech.qualify.one_actor import actor_links, twin_links

    svc, st, data = _svc(tmp_path)
    try:
        _pack_tape(st)
        out, yields, leads = _judge_the_pack(st, svc, data)
        assert {yields, leads} == {P1, P2} and len(out["same_actor"]) == 1
        by = _latest(st)
        assert by[leads]["status"] == "qualified" and _same_as(by[leads]) is None
        assert [v["wallet"] for v in _bookable(st)] == [leads]
        how = IN_ONE_TX.format(12)
        said = _same_as(by[yields])
        assert by[yields]["status"] == "candidate" and out["same_actor"][0]["how"] == how
        assert said.startswith(f"same actor as {leads}: one signal, one sniper. {how}Not armed while that wallet is")
        assert "comes first" not in said and "twins" not in said
        assert "on 12 of them their first buys came in one transaction" in said  # why the block order chose nobody
        # what the next run and the book read: a tie of two, kept apart from the companions' grouping
        lead = st.get_score("qualify", leads)["actor"]
        assert lead["leads"] is True and lead["over"] == [yields] and lead["pack_with"] == [yields]
        assert twin_links(lead) == {yields} and actor_links(lead) == set() and "twin_with" not in lead
        assert st.get_score("qualify", yields)["actor"]["pack_with"] == [leads]
    finally:
        st.close()


def test_two_wallets_in_one_transaction_on_twelve_coins_of_thirty_each_give_one_candidate(tmp_path):
    """The review's second case, the shape of 19 Nov (the set of wallets changes with every coin): 12 coins bought
    together in one transaction, 18 more each with other wallets. Under half of either list, so no twins; never a
    place behind the other, so no companions: "qualified=2 candidate=0 same_actor=0" and the book planned a sniper
    for each. A pack is counted in coins, not as a share of them."""
    svc, st, data = _svc(tmp_path)
    try:
        _pack_tape(st, alone=18)
        out, yields, leads = _judge_the_pack(st, svc, data)
        assert {yields, leads} == {P1, P2} and out["qualified"] == 1 and out["candidate"] == 1
        assert [v["wallet"] for v in _bookable(st)] == [leads]
        assert _same_as(_latest(st)[yields]).startswith(
            f"same actor as {leads}: one signal, one sniper. {IN_ONE_TX.format(30)}Not armed while"
        )
    finally:
        st.close()


def test_two_wallets_of_one_pack_give_one_candidate_though_their_actor_is_at_the_cap(tmp_path):
    """The review's third case: the pack of 19 Nov with the same fifteen wallets on 12 coins. Each of the two has the
    eleven wallets of the next three transactions as companions, so each alone is an actor of twelve (ACTOR_MAX), the
    join of the two is refused, and the twin rule had no room left either: both qualified, each record read
    {"mates": [11 wallets], "capped": True, "twins": 1}, two snipers. A pack ties two wallets beside the grouping, as
    twins are tied, so no cap can refuse it."""
    from snipetech.analysis.actors import ACTOR_MAX
    from snipetech.book.policy import BookInputs, plan_book
    from snipetech.book.run import _actor_links

    svc, st, data = _svc(tmp_path)
    try:
        _pack_tape(st, crew=True)
        out, yields, leads = _judge_the_pack(st, svc, data)
        assert {yields, leads} == {P1, P2} and out["qualified"] == 1
        lead = st.get_score("qualify", leads)["actor"]
        assert len(lead["mates"]) == ACTOR_MAX - 1 and lead["capped"] is True and lead["pack_with"] == [yields]
        assert _same_as(_latest(st)[yields]).startswith(
            f"same actor as {leads}: one signal, one sniper. {IN_ONE_TX.format(12)}"
        )
        bookable = _bookable(st)
        cfg = svc.cfg
        cfg.book.min_score = 0.0
        plan = plan_book(BookInputs(equity_sol=10.0, day_realised_sol=0.0, day_risk_used_sol=0.0, qualified=bookable,
                                    live={}, bid_model={}, snipers=[], holdings=[], now=NOW, holdings_known=True,
                                    actors=_actor_links(st, bookable, []),
                                    twins=_actor_links(st, bookable, [], twins=True)), cfg.book)  # fmt: skip
        assert [(a.kind, a.wallet) for a in plan.actions] == [("create", leads)]
    finally:
        st.close()


def test_wallets_in_one_transaction_on_four_coins_get_the_verdicts_they_always_got(tmp_path):
    """Under COMPANION_MIN coins bought together nothing is habit (the companion rule's own floor): the two wallets
    are judged apart, each record as it always was."""
    svc, st, data = _svc(tmp_path)
    try:
        _pack_tape(st, coins=4, alone=20)
        out, _yields, _leads = _judge_the_pack(st, svc, data)
        assert "same_actor" not in out and out["qualified"] == 2
        assert st.get_score("qualify", P1)["actor"] == {"mates": []} == st.get_score("qualify", P2)["actor"]
    finally:
        st.close()


def test_a_wallet_of_a_pack_that_a_sniper_may_copy_is_never_marked_and_its_record_names_the_other(tmp_path):
    """The state of 19 Nov: both wallets qualified and the book has a sniper for each. Marking either would have the
    book stop a running sniper, so neither is marked. Each record names the other as a wallet of its packs, which is
    what the book reads to add no further sniper on that signal (book/policy.actor_members)."""
    from snipetech.book.run import _actor_links

    svc, st, data = _svc(tmp_path)
    try:
        _pack_tape(st, alone=18)
        snipers = [_armed(st, P1), _armed(st, P2)]
        record_copying(st, buying_book_wallets(snipers), NOW - 120)
        after = [[NOW - 24 * 3600 + 600 * k, 0.03] for k in range(60)]
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={P1: after, P2: after}))
        by = _latest(st)
        assert {w: by[w]["status"] for w in (P1, P2)} == {P1: "qualified", P2: "qualified"}
        assert _same_as(by[P1]) is None and _same_as(by[P2]) is None and "same_actor" not in out
        assert st.get_score("qualify", P1)["actor"] == {"mates": [], "pack_with": [P2]}
        assert st.get_score("qualify", P2)["actor"] == {"mates": [], "pack_with": [P1]}
        assert _actor_links(st, _bookable(st), [], twins=True) == {P1: [P2], P2: [P1]}
        assert _actor_links(st, _bookable(st), []) == {}  # no tie the companions' grouping could refuse
    finally:
        st.close()


def test_a_companion_of_the_next_transaction_is_decided_by_the_companion_rule_as_before(tmp_path, monkeypatch):
    """A wallet of the transaction right behind is a companion by the habit rule (a place behind, on every buy) and a
    wallet of the pack. The companion rule decides first and its words stand: the reason and the record are the ones
    from before the pack rule, and with no other place-holder left to compare the wallet with, its packs are not
    read."""
    from snipetech.qualify import one_actor

    svc, st, data = _svc(tmp_path)
    try:
        _pack_tape(st, crew=True)
        behind = addr("pack-crew-00")  # the first wallet of the second transaction, under its forward test
        _both_under_test(st, NOW - 30 * 3600, wallets=(behind,), due={behind: NOW + 3600})
        record_copying(st, set(), NOW - 120)
        st.upsert_candidate(P1, "solanatracker", STRONG, NOW - 3600)
        read = []
        monkeypatch.setattr(one_actor, "pack_mates", lambda tape, w, since: read.append(w))
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        (said,) = out["same_actor"]
        assert (said["wallet"], said["same_as"]) == (P1, behind) and "how" not in said and read == []
        assert _same_as(_latest(st)[P1]).startswith(f"same actor as {behind}: one signal, one sniper. Not armed while")
        block = st.get_score("qualify", P1)["actor"]
        assert behind in block["mates"] and block["same_as"] == behind and "pack_with" not in block
    finally:
        st.close()


def test_a_pack_tie_stands_while_the_candidates_record_names_it(tmp_path):
    """The two were of one pack when one was chosen over the other. Days later the tape no longer holds the coins
    they bought together. The choice stands as a twin's and a companion's do: the candidate's record still names the
    wallet behind it, and the reason says the finding is an earlier run's and gives today's numbers."""
    svc, st, data = _svc(tmp_path)
    try:
        _pack_tape(st)
        _out, yields, leads = _judge_the_pack(st, svc, data)
        later = NOW + 25 * 3600
        _pack_tape(st, coins=0, alone=6, t0=later - 3000)  # six newer coins each, bought with other wallets
        st.set_coverage("tape", {"spans": [[NOW - 6000, later]]})  # the coins they bought together are off the tape
        record_copying(st, set(), later - 120)
        after = [[NOW - 29 * 3600 + 600 * k, 0.03] for k in range(24)]
        run_qualify(svc, svc.cfg, data / "jobs", later, lab_runner=_runner(moments={P1: after, P2: after}))
        by = _latest(st)
        assert by[leads]["computed_at"] == later == by[yields]["computed_at"]
        assert [v["wallet"] for v in _bookable(st)] == [leads] and by[yields]["status"] == "candidate"
        assert (
            "They were found to buy in one pack in an earlier run, and that stands: today they buy in one transaction "
            "on 0 of this wallet's 6 coins. Not armed while"
        ) in _same_as(by[yields])
        assert st.get_score("qualify", leads)["actor"]["pack_with"] == [yields]
        assert st.get_score("qualify", yields)["actor"]["pack_with"] == [leads]
    finally:
        st.close()


def _places(st):
    """The wallets that hold a place: qualified, or under a forward test, and not yielding to another wallet."""
    return {w for w, v in _latest(st).items()
            if (v["status"] == "qualified" or (v["status"] == "candidate" and v["forward"])) and _same_as(v) is None}  # fmt: skip


@pytest.mark.parametrize(
    "tested, armed, new",
    [
        ((), (), (P1, P2)),  # both come up in one lab
        ((P1,), (), (P2,)),  # one is under test, the other comes up
        ((P1, P2), (), ()),  # both under test since before the rule
        ((), (P2,), (P1,)),  # one has a sniper
        ((), (P1, P2), ()),  # both have a sniper, as on 19 Nov
    ],
)
def test_with_the_pack_rule_never_more_wallets_hold_a_place_and_no_armed_one_changes(
    tmp_path, monkeypatch, tested, armed, new
):
    """Five mixes of two wallets that buy 12 of their 30 coins in one transaction, judged with the pack rule and
    without it (the tape then shows no packs): with it the wallets that hold a place are never more, the armed ones
    are exactly as without it, and one place is left where no sniper holds more."""
    from snipetech.analysis.companions import Packs
    from snipetech.qualify import one_actor

    def run(sub, rule):
        (tmp_path / sub).mkdir()
        svc, st, data = _svc(tmp_path / sub)
        try:
            if not rule:
                monkeypatch.setattr(one_actor, "pack_mates", lambda *a, **k: Packs())
            _pack_tape(st, alone=18)
            picked = NOW - 30 * 3600
            _both_under_test(st, picked, wallets=tested)
            snipers = [_armed(st, w) for w in armed]
            record_copying(st, buying_book_wallets(snipers), NOW - 120)
            for w in new:
                st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
            after = [[picked + 3600 + 600 * k, 0.03] for k in range(60)]
            run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={P1: after, P2: after}))
            by = _latest(st)
            return _places(st), {w: (by[w]["status"], _same_as(by[w])) for w in armed}
        finally:
            monkeypatch.undo()
            st.close()

    places, armed_now = run("with", True)
    before, armed_before = run("without", False)
    assert len(places) <= len(before) and armed_now == armed_before
    assert all(status == "qualified" and said is None for status, said in armed_now.values())
    assert set(armed) <= places and len(places) == max(1, len(armed))  # one signal: one place, or the armed ones'
    assert len(before) == 2  # without it each of the two held a place


def test_a_wallet_the_candidate_was_chosen_over_stays_a_tie_of_two_in_its_record_when_it_falls(tmp_path):
    """The wallet behind the candidate stops passing and holds no place any more, so later runs no longer compare
    the candidate with it. The candidate's record still names it among the wallets it was chosen over, and still as
    a wallet of its packs: a tie of two, which the companions' grouping (and its cap) must never see as its own."""
    from snipetech.qualify.one_actor import actor_links, twin_links

    svc, st, data = _svc(tmp_path)
    try:
        _pack_tape(st)
        _out, yields, leads = _judge_the_pack(st, svc, data)
        after = [[NOW - 29 * 3600 + 600 * k, 0.03] for k in range(24)]
        fell = {
            "wallet": yields,
            "n": 40,
            "mean": -0.02,
            "ev_lower": -0.03,
            "win_rate": 0.2,
            "total": -0.8,
            "exit": "x",
        }
        seen = []

        def runner(ws, pos):
            seen.append(list(ws))
            return [r for r in (dict(fell), _row(leads, moments=after)) if r["wallet"] in ws]

        for later in (NOW + 25 * 3600, NOW + 28 * 3600):  # the wallet behind falls; then the candidate's next review
            record_copying(st, set(), later - 120)
            st.review_now([leads], later)
            run_qualify(svc, svc.cfg, data / "jobs", later, lab_runner=runner)
        assert seen[1] == [leads] and _latest(st)[yields]["status"] == "retired"
        assert [v["wallet"] for v in _bookable(st)] == [leads]
        block = st.get_score("qualify", leads)["actor"]
        assert block["over"] == [yields] and block["pack_with"] == [yields] and "twin_with" not in block
        assert twin_links(block) == {yields} and actor_links(block) == set()
    finally:
        st.close()


def test_a_banned_wallet_holds_no_place_for_its_actor(tmp_path):
    """Audit of 22 Nov (qualify#1): qualify never looks at a banned wallet again, so its last verdict stays as it was.
    7aPo3n and AG89fn were banned with a kept "qualified" verdict of 12 Nov, and that verdict held their actor's place:
    BXZfyH and CjAJQL passed every in-sample gate and yielded to 7aPo3n ("the actor is already under test with it"),
    with no forward check of their own, though nothing tested 7aPo3n. A banned wallet holds no place (as the book
    copies none), unless a sniper of the book copies it now."""
    from snipetech.qualify.one_actor import OneActor, standing_wallets

    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        record_copying(st, set(), NOW - 120)  # the book read OKTech: nothing copies LEAD any more
        old = NOW - 9 * 86400
        st.upsert_candidate(LEAD, "early-leaders", {}, old - 3600)
        # its last verdict: kept qualified under the old rules while a sniper copied it (as 7aPo3n's of 12 Nov)
        st.put_verdict({"wallet": LEAD, "computed_at": old, "landing_pos": "+3x", "n_tape": 35, "mean_sol": 0.01,
                        "lower_sol": -0.0015, "score": 0.004, "status": "qualified", "next_review": old + 86400,
                        "copy_plan": PLAN, "kept_armed": {"armed_on": old - 86400, "armed_score": 0.004,
                                                          "kept": True}})  # fmt: skip
        st.set_candidate_status(LEAD, "banned")  # then banned: qualify never measures it again
        assert LEAD not in standing_wallets(st)
        assert not OneActor(st, st, NOW - 40 * 3600, set()).holds(LEAD)
        assert OneActor(st, st, NOW - 40 * 3600, {LEAD[:6]}).holds(LEAD)  # one a book sniper copies still holds it
        st.upsert_candidate(TAIL, "solanatracker", STRONG, NOW - 3600)
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        tail = _latest(st)[TAIL]
        assert _same_as(tail) is None and tail["forward"]["status"] == "pending"  # its own forward check
        assert _latest(st)[LEAD]["computed_at"] == old  # the banned wallet's verdict is left as it was
    finally:
        st.close()
