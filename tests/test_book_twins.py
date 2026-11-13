"""The book adds no sniper for a wallet whose twin has one (idea I109, 13 Nov 2026).

Twins trade one signal a minute apart (analysis/twins.py). Qualify names one of them the candidate
(qualify/one_actor.py) and keeps who is whose twin in each wallet's record (`twin_with`); the book is the second
lock, as it is for companions (tests/test_book_one_actor.py): whatever the verdicts say, it makes no sniper, and starts
no stopped one, for a wallet while a twin of it has a sniper. A refusal to add and nothing else. Twins are tied two by
two on top of the actors the companions make, so a twin can never undo a tie the book already kept."""

import asyncio

from snipetech.analysis.actors import ACTOR_MAX
from snipetech.book.policy import actor_members, plan_book
from snipetech.book.run import _actor_links, run_book
from snipetech.config import Config
from tests.synth import addr
from tests.test_book_one_actor import PAIR, A, B, C, X, _acts, _inp, _score, _sniper, _why
from tests.test_book_run import NOW, FakeSvc, _env

TWINS = {A: [B]}  # what qualify keeps: A's twin is B


def _creates(plan):
    return [w for kind, w in _acts(plan, "create")]


def test_two_qualified_twins_get_one_sniper():
    cfg = Config()
    plan = plan_book(_inp(actors={}, twins=TWINS), cfg.book)
    assert _creates(plan) == [A, C]  # the better ranked twin, and the stranger
    assert _why(plan, B).startswith(f"same actor as {A[:6]}") and "one signal, one sniper" in _why(plan, B)
    # the tie may be known from either wallet's record
    assert _creates(plan_book(_inp(actors={}, twins={B: [A]}), cfg.book)) == [A, C]


def test_a_wallet_is_not_armed_while_its_twin_has_a_sniper():
    cfg = Config()
    plan = plan_book(_inp(actors={}, twins=TWINS, snipers=[_sniper(B, "sb")]), cfg.book)
    assert ("keep", B) in _acts(plan) and _creates(plan) == [C] and not _acts(plan, "stop")
    assert _why(plan, A).startswith(f"same actor as {B[:6]}")
    # a stopped sniper is not started beside its twin's running one, unless a coin is held
    two = [_sniper(A, "sa"), _sniper(B, "sb", "idle")]
    plan = plan_book(_inp(actors={}, twins=TWINS, snipers=two), cfg.book)
    assert ("keep", A) in _acts(plan) and not _acts(plan, "start") and not _acts(plan, "stop")
    assert ("start", B) in _acts(plan_book(_inp(actors={}, twins=TWINS, snipers=two, holdings_known=False), cfg.book))


def test_two_twins_that_both_have_a_sniper_keep_them():
    cfg = Config()
    both = [_sniper(A, "sa"), _sniper(B, "sb")]
    for held in ([], [{"mint": "M", "amount": 1}]):
        plan = plan_book(_inp(actors={}, twins=TWINS, snipers=both, holdings=held), cfg.book)
        assert _acts(plan) == [("keep", A), ("keep", B), ("create", C)]
    for over in ({"stopped": True}, {"guard": True}, {"equity_sol": 0.1}):  # and nothing changes under a stop
        assert (
            plan_book(_inp(actors={}, twins=TWINS, snipers=both, **over), cfg.book).actions
            == plan_book(_inp(actors={}, snipers=both, **over), cfg.book).actions
        )


def test_wallets_without_twins_are_booked_as_before():
    cfg = Config()
    before = plan_book(_inp(actors={}), cfg.book)
    assert _creates(before) == [A, B, C]
    far = plan_book(_inp(actors={}, twins={A: [addr("far-twin-1")], B: [addr("far-twin-2")]}), cfg.book)
    assert far.actions == before.actions and far.notes == before.notes
    # and the companions' plan is the same with an empty twin list as without one
    assert plan_book(_inp(twins={}), cfg.book).actions == plan_book(_inp(), cfg.book).actions


def test_twins_are_tied_two_by_two():
    """A's twin is B and B's twin is C; A and C share too little to be twins. B gets no sniper beside A's; C does."""
    cfg = Config()
    plan = plan_book(_inp(actors={}, twins={A: [B], B: [C]}), cfg.book)
    assert _creates(plan) == [A, C] and _why(plan, B).startswith(f"same actor as {A[:6]}")


def test_a_twin_never_undoes_a_tie_between_companions():
    """An actor holds ACTOR_MAX wallets at the most and a join past that is refused: grouped together, one tie more
    could split two companions and arm both. Twins are counted on top, so every wallet the companions kept from a
    sniper is still kept from it."""
    cfg = Config()
    crew = [addr(f"twin-crew-{i}") for i in range(ACTOR_MAX - 2)]
    full = {A: [B, *crew]}  # A, B and ten more: an actor at the cap
    assert _creates(plan_book(_inp(actors=full), cfg.book)) == [A, C]
    many = {
        A: [addr(f"twin-of-a-{i}") for i in range(ACTOR_MAX)],
        B: [addr(f"twin-of-b-{i}") for i in range(ACTOR_MAX)],
    }
    for actors in (PAIR, full):
        plan = plan_book(_inp(actors=actors, twins=many), cfg.book)
        assert _creates(plan) == [A, C] and _why(plan, B).startswith(f"same actor as {A[:6]}")
    # the members a wallet had are all still there, with its twins added
    wallets = [A, B, C]
    before, after = actor_members(full, wallets), actor_members(full, wallets, {**many, C: [A]})
    assert all(before[w] <= after[w] for w in before) and after[C] == {C, A} and C in after[A]


def test_the_book_reads_twins_from_the_qualify_record_apart_from_the_companions(tmp_path):
    from tests.test_book_run import L1, L2

    st, _cfg = _env(tmp_path)
    try:
        qualified = [{"wallet": L1}, {"wallet": L2}]
        assert _actor_links(st, qualified, [], twins=True) == {}
        # L1 leads over its twin L2 and buys in one block with X; L2 yields to its twin L1
        _score(st, L1, {"mates": [X], "over": [L2], "leads": True, "twins": 1, "twin_with": [L2]})
        _score(st, L2, {"mates": [], "same_as": L1, "why": "x", "twins": 1, "twin_with": [L1]})
        assert _actor_links(st, qualified, []) == {L1: [X]}  # the companions' ties alone, as before the twin rule
        assert _actor_links(st, qualified, [], twins=True) == {L1: [L2], L2: [L1]}
        # a record from before the twin rule reads as it always did
        _score(st, L1, {"mates": [X], "over": [L2], "leads": True})
        assert _actor_links(st, qualified, []) == {L1: sorted([X, L2])}
    finally:
        st.close()


def test_a_run_makes_one_sniper_for_two_qualified_twins(tmp_path):
    from tests.test_book_run import L1, L2

    st, cfg = _env(tmp_path, enabled=True)
    svc = FakeSvc(st)
    try:
        _score(st, L2, {"mates": [], "twins": 1, "twin_with": [L1]})
        out = asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert out["dry"] is False and out["actions"] == {"create": 1, "skip": 1}
        creates = [c for c in svc.calls if c[0] == "create"]
        assert len(creates) == 1 and creates[0][1] == (f"{L1}:book:0.3:lab",)
        notes = (tmp_path / "data" / "operator" / "notes.md").read_text()
        assert f"[book] {L2[:6]} gets no sniper of its own: same actor as {L1[:6]}" in notes
    finally:
        st.close()


def test_a_run_that_cannot_read_the_twins_adds_no_sniper(tmp_path, monkeypatch):
    from snipetech.book import run as R
    from tests.test_book_run import L1

    st, cfg = _env(tmp_path, enabled=True)
    running = {"id": "s1", "name": f"snipetech copy book {L1[:6]} arm:control", "status": "running"}
    svc = FakeSvc(st, snipers=[running])
    real = R._actor_links

    def broken(*a, twins=False, **k):
        if twins:
            raise RuntimeError("database is locked")
        return real(*a, **k)

    monkeypatch.setattr(R, "_actor_links", broken)
    try:
        out = asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert svc.calls == [] and "create" not in out["actions"] and out["actions"].get("keep") == 1
    finally:
        st.close()
