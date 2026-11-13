"""One forward record an actor in the paper test (idea I66, 13 Nov 2026).

A rule that follows one wallet by name (`only`) is that wallet's forward record. Wallets one operator buys with fire
on the same buys (analysis/actors.py): a second rule, for the actor's other wallet, is a second record of one signal
and one more chance to pass by luck. `freeze` leaves it out and says why in `skipped`. Records that already exist are
left as they are: nothing is merged and nothing is dropped."""

from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger
from snipetech.store import Store
from tests.synth import Market, addr
from tests.test_paper_one_record import COPY30, COPY60, COPY_AT, NOW, _coins, _data, _freeze, _ids, _row

A, B, LONE = addr("paper-actor-a"), addr("paper-actor-b"), addr("paper-actor-lone")


def _tape(st):
    """A and B buy the same eight coins in consecutive places of the same block; LONE buys eight coins of its own."""
    dev = addr("paper-actor-dev")
    for tag, block in (("pair", [(A, 200), (B, 201)]), ("lone", [(LONE, 200)])):
        for k in range(8):
            m = Market(st, addr(f"paper-actor:{tag}{k}"), dev, NOW - 5000 + 60 * k, 400_000_000 + 500 * k)
            m.buy(dev, 1.0, 0, sig=m.create_sig)
            for w, pos in block:
                m.buy(w, 1.5, 5).pos = pos
            m.commit()


def _rule(wallet, sol=1.0):
    return Trigger("kol", sol=sol, only=wallet)


def test_a_rule_for_a_second_wallet_of_an_actor_under_test_is_not_frozen(monkeypatch):
    store = Store(":memory:")
    _tape(store)
    ra, rb, rl = _rule(A), _rule(B), _rule(LONE)
    first = _freeze(monkeypatch, store, _data({ra: _coins("a", 30)}), [_row(ra, COPY30, 0.004, COPY_AT)])
    assert len(first["frozen"]) == 1 and first["skipped"] == []
    (a_id,) = _ids(store)
    # two hours on: the actor's other wallet and a stranger, each with a positive lower bound
    data = _data({rb: _coins("b", 30), rl: _coins("l", 30)})
    again = _freeze(monkeypatch, store, data, [_row(rb, COPY30, 0.009, COPY_AT), _row(rl, COPY30, 0.002, COPY_AT)],
                    now=NOW + 7200)  # fmt: skip
    assert [s["trigger"]["only"] for s in again["frozen"]] == [LONE]
    (skip,) = again["skipped"]
    assert skip["label"] == f"{rb.label()} | {COPY30.label()}"
    assert B in skip["why"] and A in skip["why"] and a_id in skip["why"] and "same actor" in skip["why"]
    assert "one forward record" in skip["why"]
    assert _ids(store)[0] == a_id and len(_ids(store)) == 2  # the record under test is untouched


def test_two_wallets_of_one_actor_in_one_freeze_give_one_record(monkeypatch):
    store = Store(":memory:")
    _tape(store)
    ra, rb = _rule(A), _rule(B)
    data = _data({ra: _coins("a", 30), rb: _coins("b", 30)})
    out = _freeze(monkeypatch, store, data, [_row(rb, COPY30, 0.009, COPY_AT), _row(ra, COPY30, 0.004, COPY_AT)])
    assert [s["trigger"]["only"] for s in out["frozen"]] == [B]  # the lab's better row keeps the place
    assert [s["label"] for s in out["skipped"]] == [f"{ra.label()} | {COPY30.label()}"]


def test_other_rules_are_frozen_as_before(monkeypatch):
    store = Store(":memory:")
    _tape(store)
    ra, ra2, group = _rule(A), _rule(A, sol=2.0), Trigger("kol", sol=1.0)
    _freeze(monkeypatch, store, _data({ra: _coins("a", 30)}), [_row(ra, COPY30, 0.004, COPY_AT)])
    # the same wallet at another size is another rule of that wallet, and a group rule names nobody
    data = _data({ra2: _coins("c", 30), group: _coins("g", 30)})
    out = _freeze(monkeypatch, store, data, [_row(ra2, COPY60, 0.003, COPY_AT), _row(group, COPY30, 0.002, COPY_AT)],
                  now=NOW + 7200)  # fmt: skip
    assert len(out["frozen"]) == 2 and out["skipped"] == []


def test_records_both_wallets_already_have_are_left_alone(monkeypatch):
    """Frozen before 13 Nov, each wallet of the pair with a record: both stay, their trades their own."""
    store = Store(":memory:")
    _tape(store)
    ra, rb = _rule(A), _rule(B)
    monkeypatch.setattr(paper, "actor_under_test", lambda *a, **k: None)  # as the code before the rule froze them
    _freeze(monkeypatch, store, _data({ra: _coins("a", 30), rb: _coins("b", 30)}),
            [_row(ra, COPY30, 0.004, COPY_AT), _row(rb, COPY30, 0.009, COPY_AT)])  # fmt: skip
    before = store.coverage("paper_strategies")["strategies"]
    assert len(before) == 2
    monkeypatch.undo()
    again = _freeze(monkeypatch, store, _data({ra: _coins("a", 30), rb: _coins("b", 30)}),
                    [_row(ra, COPY60, 0.004, COPY_AT), _row(rb, COPY60, 0.009, COPY_AT)], now=NOW + 7200)  # fmt: skip
    assert again["frozen"] == [] and store.coverage("paper_strategies")["strategies"] == before
    assert all("already in the paper test" in s["why"] for s in again["skipped"])
