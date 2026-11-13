"""Twins are one actor in qualify (idea I109, 13 Nov 2026).

The one-candidate-an-actor rule (tests/test_qualify_one_actor.py) knew one way for two wallets to be one actor: buying
in the same block. FLPjyS and i3Sj7y buy the same coins a minute apart, so each had a verdict, a forward record and,
once it passed, a sniper of its own on the one signal. Twins (analysis/twins.py) are now one actor too: of two that
pass every in-sample gate one is the candidate, and the other says "same actor as <wallet>: one signal, one sniper.
They are twins: ...". Only ever fewer candidates: the companion rule decides first, exactly as before, a wallet a
sniper may copy is never marked, and a wallet without twins gets the verdict and the record it always got."""

import pytest

from snipetech.analysis.actors import ACTOR_MAX
from snipetech.book.policy import BookInputs, buying_book_wallets, plan_book
from snipetech.book.run import _bookable
from snipetech.book.state import record_copying
from snipetech.qualify.one_actor import actor_links, twin_links
from snipetech.qualify.run import run_qualify
from tests.synth import Market, addr
from tests.test_qualify_one_actor import LEAD, TAIL, _armed, _both_under_test, _latest, _row, _runner, _same_as
from tests.test_qualify_one_actor import _tape as _block_tape
from tests.test_qualify_run import NOW, SLOT, STRONG, _svc

ONE, TWO, SOLO = addr("twin-one"), addr("twin-two"), addr("twin-solo")
WORDS = "They are twins: both bought 12 of the same coins (of 12 and 12), 12 of them within a minute of each other. "


def _tape(st, groups=((ONE, TWO),), solo=(SOLO,), coins=12, t0=NOW - 7000, gap=60, tag=""):
    """Each group buys the same `coins` coins, every wallet `gap` slots (24 s) behind the one before it and never in
    its block; each wallet of `solo` buys coins of its own. A small crowd follows each first buy."""
    D = addr("dev")
    for i, group in enumerate([*groups, *[(w,) for w in solo]]):
        for k in range(coins):
            m = Market(st, addr(f"{tag}{group[0]}:{k}"), D, t0 + i * 100 + k * 250, SLOT + i * 250 + k * 625)
            m.buy(D, 1.0, 0)
            buys = [(5 + gap * j, w, 0.5) for j, w in enumerate(group)]
            buys += [(6 + j, addr(f"twin-crowd{i}{k}{j}"), 0.3 + 0.1 * j) for j in range(4)]
            for after, w, sol in sorted(buys):
                m.buy(w, sol, after)
            for w in group:
                m.sell(w, 1.0, 400)
            m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 40 * 3600, NOW]]})


def _block(st, w):
    return st.get_score("qualify", w)["actor"]


def test_of_two_twins_one_is_the_candidate_and_the_other_says_so(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        record_copying(st, set(), NOW - 120)
        for w in (ONE, TWO, SOLO):
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        by = _latest(st)
        (said,) = out["same_actor"]
        yields, holds = said["wallet"], said["same_as"]
        assert {yields, holds} == {ONE, TWO}
        # the one judged first is the candidate, exactly as a wallet alone is; so is the stranger
        for w in (holds, SOLO):
            assert (
                by[w]["status"] == "candidate" and by[w]["forward"]["status"] == "pending" and _same_as(by[w]) is None
            )
        # its twin: a verdict of its own with its numbers, never armable, no forward record, and the reason says how
        assert by[yields]["status"] == "candidate" and by[yields]["forward"] is None
        assert _same_as(by[yields]).startswith(f"same actor as {holds}: one signal, one sniper. {WORDS}Not armed while")
        assert "already under test" in _same_as(by[yields]) and said["how"] == WORDS
        assert (by[yields]["n_tape"], by[yields]["lower_sol"]) == (35, 0.03) and by[yields]["copy_plan"]
        assert _bookable(st) == [] and out["candidate"] == 3 and out["forward_pending"] == 2
        # what the next run, the book and the agents read
        assert _block(st, yields) == {"mates": [], "twins": 1, "twin_with": [holds], "same_as": holds,
                                      "why": said["why"]}  # fmt: skip
        assert twin_links(_block(st, yields)) == {holds} and actor_links(_block(st, yields)) == set()
        assert _block(st, holds) == {"mates": []} == _block(st, SOLO)  # judged before its twin held a place
    finally:
        st.close()


def test_the_candidates_verdict_is_the_one_it_gets_alone(tmp_path):
    def run(sub, candidates, keep=None):
        svc, st, data = _svc(tmp_path / sub)
        try:
            _tape(st)
            record_copying(st, set(), NOW - 120)
            for w in candidates:
                st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
            out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
            who = keep or out["same_actor"][0]["same_as"]
            v = dict(_latest(st)[who])
            v.pop("id")
            return who, v, _block(st, who)
        finally:
            st.close()

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    who, together, block = run("a", [ONE, TWO, SOLO])
    assert (who, together, block) == run("b", [who], keep=who)


def test_a_twin_of_a_wallet_already_under_test_starts_no_forward_record(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        record_copying(st, set(), NOW - 120)
        st.upsert_candidate(TWO, "solanatracker", STRONG, NOW - 3600)
        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        assert _latest(st)[TWO]["forward"]["status"] == "pending"
        later = NOW + 3 * 3600
        record_copying(st, set(), later - 120)
        st.upsert_candidate(ONE, "solanatracker", STRONG, later - 60)
        out = run_qualify(svc, svc.cfg, data / "jobs", later, lab_runner=_runner())
        by = _latest(st)
        assert by[ONE]["status"] == "candidate" and by[ONE]["forward"] is None
        assert _same_as(by[ONE]).startswith(f"same actor as {TWO}: one signal, one sniper. {WORDS}")
        assert "already under test" in _same_as(by[ONE])
        assert by[TWO]["forward"]["status"] == "pending" and _same_as(by[TWO]) is None  # untouched
        assert by[TWO]["computed_at"] == NOW and [x["wallet"] for x in out["same_actor"]] == [ONE]
        assert _bookable(st) == []
    finally:
        st.close()


def test_two_twins_already_under_test_leave_one_candidate_and_both_keep_their_records(tmp_path):
    """The state on the server on 13 Nov: both picked, each with a forward record of its own, both passing."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked, wallets=(ONE, TWO))
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={ONE: after, TWO: after}))
        by = _latest(st)
        (said,) = out["same_actor"]
        yields, leads = said["wallet"], said["same_as"]
        assert {yields, leads} == {ONE, TWO} and [v["wallet"] for v in _bookable(st)] == [leads]
        assert by[leads]["status"] == "qualified" and _same_as(by[leads]) is None
        assert by[yields]["status"] == "candidate" and WORDS in _same_as(by[yields])
        assert by[yields]["forward"]["picked_at"] == picked and by[yields]["forward"]["status"] == "passed"
        assert said["forward"] == "its own record kept" and "lower bound" in said["why"]
        lead = _block(st, leads)
        assert lead["leads"] is True and lead["over"] == [yields] and lead["twin_with"] == [yields]
        assert actor_links(lead) == set() and twin_links(lead) == {yields}  # a twin's tie, not a companion's
        # a day on the choice stands, whichever measures better, and the wallet behind it is still named its twin
        later = NOW + 25 * 3600
        record_copying(st, set(), later - 120)
        runner = _runner(lower={leads: 0.004, yields: 0.05}, moments={ONE: after, TWO: after})
        run_qualify(svc, svc.cfg, data / "jobs", later, lab_runner=runner)
        by = _latest(st)
        assert by[leads]["computed_at"] == later == by[yields]["computed_at"]
        assert [v["wallet"] for v in _bookable(st)] == [leads] and "candidate already" in _same_as(by[yields])
        assert WORDS in _same_as(by[yields]) and twin_links(_block(st, leads)) == {yields}
    finally:
        st.close()


def test_when_the_candidate_stops_passing_its_twin_takes_the_place(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked, wallets=(ONE, TWO))
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={ONE: after, TWO: after}))
        yields, leads = out["same_actor"][0]["wallet"], out["same_actor"][0]["same_as"]
        later = NOW + 25 * 3600
        record_copying(st, set(), later - 120)
        fell = {"wallet": leads, "n": 40, "mean": -0.02, "ev_lower": -0.03, "win_rate": 0.2, "total": -0.8, "exit": "x"}
        lost = [fell, _row(yields, moments=after)]
        run_qualify(
            svc, svc.cfg, data / "jobs", later, lab_runner=lambda ws, pos: [r for r in lost if r["wallet"] in ws]
        )
        by = _latest(st)
        assert by[leads]["status"] == "retired"
        assert by[yields]["status"] == "qualified" and _same_as(by[yields]) is None
        assert by[yields]["forward"]["picked_at"] == picked and [v["wallet"] for v in _bookable(st)] == [yields]
    finally:
        st.close()


def test_the_twin_a_sniper_copies_is_the_candidate_and_keeps_its_sniper(tmp_path):
    """FLPjyS on 13 Nov, 21:50 UTC: qualified, and the book made its sniper. Its twin, a pass away from a sniper of its
    own, yields; the copied wallet's verdict is not touched and the book's plan keeps the one sniper."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        sniper = _armed(st, TWO)
        st.upsert_candidate(ONE, "solanatracker", STRONG, NOW - 3600)
        record_copying(st, buying_book_wallets([sniper]), NOW - 120)
        after = [[NOW - 24 * 3600 + 600 * k, 0.03] for k in range(60)]
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={ONE: after, TWO: after}))
        by = _latest(st)
        assert by[TWO]["status"] == "qualified" and _same_as(by[TWO]) is None
        assert by[ONE]["status"] == "candidate" and by[ONE]["forward"] is None
        assert _same_as(by[ONE]).startswith(f"same actor as {TWO}: one signal, one sniper. {WORDS}")
        assert "a sniper of the book copies it" in _same_as(by[ONE])
        assert [x["wallet"] for x in out["same_actor"]] == [ONE]
        inp = BookInputs(equity_sol=10.0, day_realised_sol=0.0, day_risk_used_sol=0.0, qualified=_bookable(st), live={},
                         bid_model={}, snipers=[sniper], holdings=[], now=NOW)  # fmt: skip
        assert [(a.kind, a.wallet) for a in plan_book(inp, svc.cfg.book).actions] == [("keep", TWO)]
    finally:
        st.close()


def test_a_twin_a_sniper_may_copy_is_never_marked(tmp_path):
    """Both twins qualified and armed before the rule. Marking either would have the book drop a running sniper:
    neither is marked, whatever the book's reading of OKTech says. Their records name each other, for the book."""
    for case, reading in (("both", "both"), ("row-only", "first"), ("unknown", None)):
        (tmp_path / case).mkdir()
        svc, st, data = _svc(tmp_path / case)
        try:
            _tape(st)
            snipers = [_armed(st, ONE), _armed(st, TWO)]
            if reading == "both":
                record_copying(st, buying_book_wallets(snipers), NOW - 120)
            elif reading == "first":
                record_copying(st, buying_book_wallets(snipers[:1]), NOW - 120)
            after = [[NOW - 24 * 3600 + 600 * k, 0.03] for k in range(60)]
            out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={ONE: after, TWO: after}))
            by = _latest(st)
            assert {w: by[w]["status"] for w in (ONE, TWO)} == {ONE: "qualified", TWO: "qualified"}, case
            assert _same_as(by[ONE]) is None and _same_as(by[TWO]) is None and "same_actor" not in out, case
            if reading is not None:  # (unknown: both verdicts stay as they were, nothing is written)
                assert twin_links(_block(st, ONE)) == {TWO} and twin_links(_block(st, TWO)) == {ONE}, case
                assert _block(st, ONE)["twins"] == 1 and "same_as" not in _block(st, ONE), case
        finally:
            st.close()


def test_a_wallet_in_a_crowd_joins_one_actors_worth_of_it_and_says_how_many(tmp_path):
    """Fourteen place-holders trade one signal and a fifteenth wallet comes up. It is one actor with as many of them
    as an actor holds (ACTOR_MAX wallets, itself counted), so it yields; its record says how many twins it has and
    that the actor is larger than shown. Nothing is done to the fourteen on that count."""
    svc, st, data = _svc(tmp_path)
    try:
        crowd = [addr(f"twin-crowd-member-{i}") for i in range(14)]
        new = addr("twin-crowd-newcomer")
        _tape(st, groups=((*crowd, new),), solo=(), gap=8)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked, wallets=crowd, due=dict.fromkeys(crowd, NOW + 3600))
        record_copying(st, set(), NOW - 120)
        st.upsert_candidate(new, "solanatracker", STRONG, NOW - 3600)
        before = {w: v["id"] for w, v in _latest(st).items()}
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        by = _latest(st)
        assert [x["wallet"] for x in out["same_actor"]] == [new]
        assert by[new]["status"] == "candidate" and by[new]["forward"] is None and _bookable(st) == []
        block = _block(st, new)
        assert block["twins"] == 14 and block["capped"] is True
        assert block["twin_with"] == sorted(crowd)[: ACTOR_MAX - 1] and block["same_as"] in block["twin_with"]
        assert {w: by[w]["id"] for w in crowd} == before  # no verdict of theirs was written: not marked, not retired
    finally:
        st.close()


def test_wallets_that_share_only_a_few_coins_get_the_verdicts_they_always_got(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        a, b = addr("twin-apart-a"), addr("twin-apart-b")
        _tape(st, groups=(), solo=(a, b), coins=20)
        _tape(st, groups=((a, b),), solo=(), coins=6, tag="hot:")  # six hot coins both bought, of 26 each
        record_copying(st, set(), NOW - 120)
        for w in (a, b):
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        by = _latest(st)
        assert all(by[w]["forward"]["status"] == "pending" and _same_as(by[w]) is None for w in (a, b))
        assert "same_actor" not in out and out["forward_pending"] == 2
        assert _block(st, a) == {"mates": []} == _block(st, b)
    finally:
        st.close()


def test_companions_are_decided_by_the_companion_rule_as_before(tmp_path):
    """Two wallets of one block are twins by their coins too. The companion rule has them, and its words stand: the
    reason and the record are the ones from before the twin rule."""
    svc, st, data = _svc(tmp_path)
    try:
        _block_tape(st)
        record_copying(st, set(), NOW - 120)
        for w in (TAIL, LEAD):
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        tail = _latest(st)[TAIL]
        assert _same_as(tail).startswith(f"same actor as {LEAD}: one signal, one sniper. Not armed while that wallet")
        assert "twins" not in _same_as(tail) and "how" not in out["same_actor"][0]
        assert _block(st, TAIL) == {"mates": [LEAD], "same_as": LEAD, "why": out["same_actor"][0]["why"]}
        assert _block(st, LEAD) == {"mates": [TAIL]}
    finally:
        st.close()


def _mixed_tape(st, block, later, coins=12, t0=NOW - 7000):
    """`block`: wallets that buy every coin in one block, in consecutive places (the order turned round on every
    fourth coin, so each is a companion of the others); `later`: wallets that buy the same coins 60, 120, ... slots
    after them, never in a block with anyone: twins of the block's wallets and of each other."""
    D = addr("dev")
    for k in range(coins):
        m = Market(st, addr(f"twin-mixed:{k}"), D, t0 + k * 250, SLOT + k * 625)
        m.buy(D, 1.0, 0)
        for j, w in enumerate(block if k % 4 else block[::-1]):
            m.buy(w, 0.5, 5).pos = 200 + j
        for j, w in enumerate(later):
            m.buy(w, 0.5, 65 + 60 * j)
        for w in (*block, *later):
            m.sell(w, 1.0, 400)
        m.commit()
    st.set_coverage("tape", {"spans": [[NOW - 40 * 3600, NOW]]})


def test_what_the_companion_rule_decides_stands_with_a_twin_beside_it(tmp_path):
    """LEAD is under test. TAIL, its companion in the block, comes up: it yields to LEAD in the companion rule's own
    words, whether or not a twin of both holds a place too. The twin is only written into its record."""

    def run(sub, twin):
        (tmp_path / sub).mkdir()
        svc, st, data = _svc(tmp_path / sub)
        try:
            _mixed_tape(st, (LEAD, TAIL), (ONE,))
            picked = NOW - 30 * 3600
            _both_under_test(
                st, picked, wallets=(LEAD, ONE) if twin else (LEAD,), due={LEAD: NOW + 3600, ONE: NOW + 3600}
            )
            record_copying(st, set(), NOW - 120)
            st.upsert_candidate(TAIL, "solanatracker", STRONG, NOW - 3600)
            out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
            v = dict(_latest(st)[TAIL])
            v.pop("id")
            return v, out["same_actor"], _block(st, TAIL)
        finally:
            st.close()

    alone, said_alone, block_alone = run("alone", twin=False)
    beside, said_beside, block_beside = run("beside", twin=True)
    assert beside == alone and said_beside == said_alone and said_alone[0]["same_as"] == LEAD
    assert "twins" not in _same_as(alone) and block_alone == {
        "mates": [LEAD],
        "same_as": LEAD,
        "why": said_alone[0]["why"],
    }
    assert block_beside == block_alone | {"twins": 1, "twin_with": [ONE]}


def test_an_actor_at_the_cap_takes_no_twin_and_keeps_every_tie_it_had(tmp_path):
    """Twelve wallets of one block are an actor at ACTOR_MAX. One more tie would be refused by the grouping and
    could split the twelve; the twin is not joined, the record says `capped`, and the companions decide as before."""
    svc, st, data = _svc(tmp_path)
    try:
        crew = [addr(f"twin-cap-crew-{i}") for i in range(ACTOR_MAX)]
        _mixed_tape(st, tuple(crew), (ONE,))
        picked = NOW - 30 * 3600
        _both_under_test(st, picked, wallets=(crew[0], ONE), due={crew[0]: NOW + 3600, ONE: NOW + 3600})
        record_copying(st, set(), NOW - 120)
        st.upsert_candidate(crew[1], "solanatracker", STRONG, NOW - 3600)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner())
        (said,) = out["same_actor"]
        assert (said["wallet"], said["same_as"]) == (crew[1], crew[0]) and "how" not in said
        block = _block(st, crew[1])
        assert block["same_as"] == crew[0] and block["twins"] == 1 and block["capped"] is True
        assert "twin_with" not in block and sorted(block["mates"]) == sorted(set(crew) - {crew[1]})
    finally:
        st.close()


def test_a_twin_tie_stands_while_the_candidates_record_names_it(tmp_path):
    """The two were twins when one was chosen over the other. Days later their coins differ again. The choice stands
    as a companion's does: the candidate's record still names the wallet behind it, so that wallet stays behind it,
    and the reason says the finding is an earlier run's and gives today's numbers."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st)
        picked = NOW - 30 * 3600
        _both_under_test(st, picked, wallets=(ONE, TWO))
        record_copying(st, set(), NOW - 120)
        after = [[picked + 3600 + 600 * k, 0.03] for k in range(24)]
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments={ONE: after, TWO: after}))
        yields, leads = out["same_actor"][0]["wallet"], out["same_actor"][0]["same_as"]
        _tape(st, groups=(), solo=(ONE, TWO), coins=20, tag="later:")  # twenty coins each of its own since
        later = NOW + 25 * 3600
        record_copying(st, set(), later - 120)
        out = run_qualify(svc, svc.cfg, data / "jobs", later, lab_runner=_runner(moments={ONE: after, TWO: after}))
        by = _latest(st)
        assert [v["wallet"] for v in _bookable(st)] == [leads] and by[yields]["status"] == "candidate"
        said = _same_as(by[yields])  # 12 coins of about 32 each: under half of either list
        assert (
            "They were found to be twins in an earlier run, and that stands: today both bought 12 of the same" in said
        )
        assert " 12 of them within a minute of each other. Not armed while" in said and "(of 3" in said
        assert "twins" not in _block(st, leads) and twin_links(_block(st, leads)) == {yields}
        assert _block(st, yields)["twin_with"] == [leads] and "twins" not in _block(st, yields)
    finally:
        st.close()


def _places(st):
    """The wallets that hold a place: qualified, or under a forward test, and not yielding to another wallet."""
    return {
        w
        for w, v in _latest(st).items()
        if (v["status"] == "qualified" or (v["status"] == "candidate" and v["forward"])) and _same_as(v) is None
    }


@pytest.mark.parametrize(
    "tested, armed, new",
    [
        ((), (), (LEAD, TAIL, ONE, TWO)),  # all four come up in one lab
        ((ONE,), (), (LEAD, TAIL, TWO)),  # a twin is under test, the block's pair and the other twin come up
        ((LEAD, TAIL, ONE, TWO), (), ()),  # all four under test since before either rule
        ((LEAD, ONE), (), (TAIL, TWO)),
        ((TAIL,), (ONE,), (LEAD, TWO)),  # a twin has a sniper
        ((), (LEAD, TWO), (TAIL, ONE)),  # a companion and a twin have snipers
        ((ONE, TWO), (TAIL,), (LEAD,)),
    ],
)
def test_with_the_twin_rule_never_more_wallets_hold_a_place_and_no_armed_one_changes(
    tmp_path, monkeypatch, tested, armed, new
):
    """Seven mixes of a block's pair (companions) and two wallets that buy the same coins a minute later (twins of
    the pair and of each other), judged with the twin rule and without it (the tape then shows no twins): with it
    the wallets holding a place are never more, the armed ones are exactly as without it, and one place is left."""
    from snipetech.qualify import one_actor

    def run(sub, rule):
        (tmp_path / sub).mkdir()
        svc, st, data = _svc(tmp_path / sub)
        try:
            if not rule:
                monkeypatch.setattr(one_actor, "twins", lambda *a, **k: {})
            _mixed_tape(st, (LEAD, TAIL), (ONE, TWO))
            picked = NOW - 30 * 3600
            _both_under_test(st, picked, wallets=tested)
            snipers = [_armed(st, w) for w in armed]
            record_copying(st, buying_book_wallets(snipers), NOW - 120)
            for w in new:
                st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3600)
            after = [[picked + 3600 + 600 * k, 0.03] for k in range(60)]
            everyone = (LEAD, TAIL, ONE, TWO)
            run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=_runner(moments=dict.fromkeys(everyone, after)))
            by = _latest(st)
            return (
                _places(st),
                {w: (by[w]["status"], _same_as(by[w])) for w in armed},
                {v["wallet"] for v in _bookable(st)},
            )
        finally:
            monkeypatch.undo()
            st.close()

    places, armed_now, bookable = run("with", True)
    before, armed_before, bookable_before = run("without", False)
    assert len(places) <= len(before) and len(bookable) <= len(bookable_before)
    assert armed_now == armed_before and all(
        status == "qualified" and said is None for status, said in armed_now.values()
    )
    assert set(armed) <= places and len(places) == max(1, len(armed))  # one signal: one place, or the armed ones'
    assert len(before) >= min(2, 1 + len(armed))  # without it the twins held a place beside the block's pair
