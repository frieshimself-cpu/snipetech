"""The book never adds a second sniper on one signal (idea I66, 13 Nov 2026).

Wallets one operator buys with are one actor (analysis/actors.py). Qualify names one of them the candidate
(qualify/one_actor.py); the book is the second lock: whatever the verdicts say, it makes no sniper, and starts no
stopped one, for a wallet whose actor already has a sniper. It is a refusal to add and nothing else: a sniper that
exists is kept, replaced and stopped exactly as before (a stopped sniper strands its coin)."""

import asyncio

from snipetech.book.policy import BookInputs, plan_book
from snipetech.book.run import _actor_links, run_book
from snipetech.config import Config
from tests.synth import addr
from tests.test_book_run import NOW, FakeSvc, _env

A, B, C, X = addr("one-actor-a"), addr("one-actor-b"), addr("one-actor-c"), addr("one-actor-x")
PAIR = {A: [B]}  # what qualify keeps: A buys with B


def _q(wallet, score):
    return {"wallet": wallet, "status": "qualified", "score": score, "n_tape": 30, "lower_sol": score,
            "copy_plan": {"min_leader_sol": 0.3, "exit_name": "lab"}}  # fmt: skip


def _sniper(wallet, sid, status="running"):
    return {"id": sid, "name": f"snipetech copy book {wallet[:6]} arm:control", "status": status}


def _inp(**over):
    base = {"equity_sol": 10.0, "day_realised_sol": 0.0, "day_risk_used_sol": 0.0,
            "qualified": [_q(A, 0.03), _q(B, 0.02), _q(C, 0.01)], "live": {}, "bid_model": {}, "snipers": [],
            "holdings": [], "now": NOW, "holdings_known": True, "actors": PAIR}  # fmt: skip
    base.update(over)
    return BookInputs(**base)


def _acts(plan, kind=None):
    return [(a.kind, a.wallet) for a in plan.actions if kind is None or a.kind == kind]


def _why(plan, wallet):
    return next(a.reason for a in plan.actions if a.kind == "skip" and a.wallet == wallet)


def test_two_qualified_wallets_of_one_actor_get_one_sniper():
    cfg = Config()
    plan = plan_book(_inp(), cfg.book)
    assert _acts(plan, "create") == [("create", A), ("create", C)]  # the better ranked of the pair, and the stranger
    assert _why(plan, B).startswith(f"same actor as {A[:6]}") and "one signal, one sniper" in _why(plan, B)
    assert any(B[:6] in n and A[:6] in n for n in plan.notes)
    # the link may be known from either wallet, or through a wallet both buy with that nobody asked about
    for links in ({B: [A]}, {A: [X], B: [X]}):
        assert _acts(plan_book(_inp(actors=links), cfg.book), "create") == [("create", A), ("create", C)]


def test_wallets_nothing_ties_together_are_booked_as_before():
    cfg = Config()
    before = plan_book(_inp(actors={}), cfg.book)
    assert _acts(before) == [("create", A), ("create", B), ("create", C)]
    # links to wallets that are neither ranked nor armed change nothing
    after = plan_book(_inp(actors={A: [addr("far-1")], B: [addr("far-2")]}), cfg.book)
    assert after.actions == before.actions and after.notes == before.notes


def test_a_wallet_is_not_armed_while_another_wallet_of_its_actor_has_a_sniper():
    cfg = Config()
    # B's sniper runs; A, ranked above it, has none: none is made for A, whatever its verdict
    plan = plan_book(_inp(snipers=[_sniper(B, "sb")]), cfg.book)
    assert ("keep", B) in _acts(plan) and _acts(plan, "create") == [("create", C)]
    assert _why(plan, A).startswith(f"same actor as {B[:6]}") and not _acts(plan, "stop")
    # the same while B's sniper is starting, stopping, or stays stopped until it is replaced
    for status in ("starting", "stopping"):
        assert _acts(plan_book(_inp(snipers=[_sniper(B, "sb", status)]), cfg.book), "create") == [("create", C)]
    spent = plan_book(
        _inp(snipers=[_sniper(B, "sb", "idle")], buys_by_sniper={"sb": 1}, holdings_known=False), cfg.book
    )
    assert ("keep", B) in _acts(spent) and _acts(spent, "create") == [("create", C)]


def test_a_sniper_somebody_else_made_for_one_wallet_of_the_actor_counts():
    cfg = Config()
    hand = {"id": "h1", "name": "snipetech copy mine", "status": "running"}
    plan = plan_book(_inp(snipers=[hand], other_copies={B}), cfg.book)
    assert _acts(plan, "create") == [("create", C)]  # B is left to the other sniper, and A is the same signal
    assert _why(plan, A).startswith(f"same actor as {B[:6]}")


def test_two_wallets_of_one_actor_that_both_have_a_sniper_keep_them():
    """Armed before the rule: the book stops neither (a stop strands a coin), with a coin held or without one."""
    cfg = Config()
    both = [_sniper(A, "sa"), _sniper(B, "sb")]
    for held in ([], [{"mint": "M", "amount": 1}]):
        plan = plan_book(_inp(snipers=both, holdings=held), cfg.book)
        assert _acts(plan) == [("keep", A), ("keep", B), ("create", C)]
    # and when B falls out of the ranking, its sniper is treated exactly as before the rule
    gone = plan_book(_inp(snipers=both, qualified=[_q(A, 0.03)]), cfg.book)
    same = plan_book(_inp(snipers=both, qualified=[_q(A, 0.03)], actors={}), cfg.book)
    assert gone.actions == same.actions


def test_when_both_snipers_are_spent_one_replacement_is_made():
    """Each made its buy and the wallet holds nothing: both are retired, as before. While either is still on OKTech it
    is its actor's sniper (a retire can fail, or be undone when a coin appears), so the replacement waits for the next
    run, and then the actor gets one."""
    cfg = Config()
    both = [_sniper(A, "sa"), _sniper(B, "sb")]
    plan = plan_book(_inp(snipers=both, buys_by_sniper={"sa": 1, "sb": 1}), cfg.book)
    assert _acts(plan, "retire") == [("retire", A), ("retire", B)]
    assert _acts(plan, "create") == [("create", C)] and not _acts(plan, "stop")
    assert _why(plan, A).startswith(f"same actor as {B[:6]}") and _why(plan, B).startswith(f"same actor as {A[:6]}")
    after = plan_book(_inp(snipers=[]), cfg.book)  # five minutes on, both gone
    assert _acts(after, "create") == [("create", A), ("create", C)] and _why(after, B).startswith("same actor as")
    # one spent, the other still running: the running one is the actor's sniper, no replacement for the spent one
    plan = plan_book(_inp(snipers=both, buys_by_sniper={"sa": 1}), cfg.book)
    assert _acts(plan, "retire") == [("retire", A)] and ("keep", B) in _acts(plan)
    assert _acts(plan, "create") == [("create", C)] and _why(plan, A).startswith(f"same actor as {B[:6]}")
    # a wallet alone in its actor is replaced in the same run, as it always was
    alone = plan_book(_inp(snipers=[_sniper(C, "sc")], buys_by_sniper={"sc": 1}), cfg.book)
    assert ("retire", C) in _acts(alone) and ("create", C) in _acts(alone)


def test_a_stopped_sniper_of_a_wallet_the_book_no_longer_wants_blocks_nobody():
    """13 Nov: six stopped book snipers stood on OKTech. One of them must not keep its actor unarmed for good."""
    cfg = Config()
    plan = plan_book(_inp(snipers=[_sniper(B, "sb", "idle")], qualified=[_q(A, 0.03), _q(C, 0.01)]), cfg.book)
    assert _acts(plan) == [("create", A), ("create", C)]
    # but one the book stops in this very run still counts: its stop may fail
    plan = plan_book(_inp(snipers=[_sniper(B, "sb")], qualified=[_q(A, 0.03), _q(C, 0.01)]), cfg.book)
    assert ("stop", B[:6]) in _acts(plan) and _acts(plan, "create") == [("create", C)]


def test_a_stopped_sniper_is_not_started_beside_its_actors_running_one_unless_a_coin_is_held():
    cfg = Config()
    two = [_sniper(A, "sa"), _sniper(B, "sb", "idle")]
    plan = plan_book(_inp(snipers=two), cfg.book)
    assert ("keep", A) in _acts(plan) and not _acts(plan, "start") and not _acts(plan, "stop")
    assert _why(plan, B).startswith(f"same actor as {A[:6]}") and "not started" in _why(plan, B)
    assert _acts(plan, "create") == [("create", C)]  # and no new sniper for B either
    # the wallet holds a coin (or could not be read): the stopped sniper may be the one that has to sell it, so it is
    # started as it always was
    for over in ({"holdings": [{"mint": "M", "amount": 1}]}, {"holdings_known": False}, {"tape_holding": True}):
        assert ("start", B) in _acts(plan_book(_inp(snipers=two, **over), cfg.book))
    # two stopped snipers of one actor, nothing held: one is started
    idle = [_sniper(A, "sa", "idle"), _sniper(B, "sb", "idle")]
    plan = plan_book(_inp(snipers=idle), cfg.book)
    assert _acts(plan, "start") == [("start", A)] and _why(plan, B).startswith(f"same actor as {A[:6]}")


def test_the_rule_changes_nothing_under_a_stop():
    cfg = Config()
    both = [_sniper(A, "sa"), _sniper(B, "sb")]
    for over in ({"stopped": True}, {"guard": True}, {"equity_sol": 0.1}):
        assert (
            plan_book(_inp(snipers=both, **over), cfg.book).actions
            == plan_book(_inp(snipers=both, actors={}, **over), cfg.book).actions
        )


def _score(st, wallet, block):
    st.put_score("qualify", wallet, {"verdict": {}, "actor": block})


def test_the_book_reads_each_wallets_actor_from_its_qualify_record(tmp_path):
    from tests.test_book_run import L1, L2

    st, _cfg = _env(tmp_path)
    try:
        assert _actor_links(st, [{"wallet": L1}, {"wallet": L2}], []) == {}  # nothing kept: nothing known
        _score(st, L1, {"mates": [X], "over": [L2], "leads": True})
        _score(st, L2, {"mates": [], "same_as": L1, "why": "x"})
        assert _actor_links(st, [{"wallet": L1}, {"wallet": L2}], []) == {L1: sorted([X, L2]), L2: [L1]}
        # and for the wallet a sniper on OKTech copies, by our own record of it, ranked or not
        st.add_oktech_sniper({"id": "s7", "external_id": "e7", "kind": "copy", "source": A, "name": "n",
                              "config_hash": "h", "payload": {"ctbotWallets": [{"address": A}]}})  # fmt: skip
        _score(st, A, {"mates": [B]})
        assert _actor_links(st, [], [{"id": "s7", "status": "running"}]) == {A: [B]}
        assert _actor_links(st, [], [{"id": "other", "status": "running"}]) == {}
    finally:
        st.close()


def test_a_run_makes_one_sniper_for_two_qualified_wallets_of_one_actor(tmp_path):
    from tests.test_book_run import L1, L2

    st, cfg = _env(tmp_path, enabled=True)
    svc = FakeSvc(st)
    try:
        _score(st, L2, {"mates": [L1]})
        out = asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert out["dry"] is False and out["actions"] == {"create": 1, "skip": 1}
        creates = [c for c in svc.calls if c[0] == "create"]
        assert len(creates) == 1 and creates[0][1] == (f"{L1}:book:0.3:lab",)
        assert any("same actor" in n for n in out["notes"])
        notes = (tmp_path / "data" / "operator" / "notes.md").read_text()
        assert f"[book] {L2[:6]} gets no sniper of its own: same actor as {L1[:6]}" in notes
        asyncio.run(run_book(svc, cfg, NOW + 300, balances=lambda w: (10.0, 0.0, [])))
        assert (tmp_path / "data" / "operator" / "notes.md").read_text().count("gets no sniper of its own") == 1
    finally:
        st.close()


def test_a_run_that_cannot_read_the_actors_adds_no_sniper(tmp_path, monkeypatch):
    """When in doubt the book arms fewer wallets: nothing new starts in a run that could not read who buys with whom,
    and nothing running is touched."""
    from snipetech.book import run as R
    from tests.test_book_run import L1

    st, cfg = _env(tmp_path, enabled=True)
    running = {"id": "s1", "name": f"snipetech copy book {L1[:6]} arm:control", "status": "running"}
    svc = FakeSvc(st, snipers=[running])

    def broken(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(R, "_actor_links", broken)
    try:
        out = asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert svc.calls == [] and "create" not in out["actions"] and out["actions"].get("keep") == 1
        assert "buy together" in (tmp_path / "data" / "operator" / "notes.md").read_text()
    finally:
        st.close()
