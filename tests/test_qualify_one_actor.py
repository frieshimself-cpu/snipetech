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
