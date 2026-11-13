"""One forward record for twins in the paper test (idea I109, 13 Nov 2026).

A rule that follows one wallet by name is that wallet's forward record, and wallets of one actor get one record
(tests/test_paper_one_actor.py). Twins (analysis/twins.py) buy the same coins a minute apart and never in one block,
so the companion rule did not see them and `freeze` gave each a record: two chances for one signal to pass by luck.
A rule for the twin of a wallet under test is now left out, and `skipped` says they are twins."""

from snipetech.analysis.lab import Trigger
from snipetech.store import Store
from tests.synth import Market, addr
from tests.test_paper_one_record import COPY30, COPY_AT, NOW, _coins, _data, _freeze, _ids, _row

A, B, LONE = addr("paper-twin-a"), addr("paper-twin-b"), addr("paper-twin-lone")


def _tape(st):
    """A and B buy the same twelve coins, B 60 slots (24 s) behind A and never in its block; LONE buys twelve coins
    of its own."""
    dev = addr("paper-twin-dev")
    for tag, buys in (("pair", [(A, 5), (B, 65)]), ("lone", [(LONE, 5)])):
        for k in range(12):
            m = Market(st, addr(f"paper-twin:{tag}{k}"), dev, NOW - 5000 + 60 * k, 400_000_000 + 500 * k)
            m.buy(dev, 1.0, 0, sig=m.create_sig)
            for w, after in buys:
                m.buy(w, 1.5, after)
            m.commit()


def _rule(wallet, sol=1.0):
    return Trigger("kol", sol=sol, only=wallet)


def test_a_rule_for_the_twin_of_a_wallet_under_test_is_not_frozen(monkeypatch):
    store = Store(":memory:")
    _tape(store)
    ra, rb, rl = _rule(A), _rule(B), _rule(LONE)
    first = _freeze(monkeypatch, store, _data({ra: _coins("a", 30)}), [_row(ra, COPY30, 0.004, COPY_AT)])
    assert len(first["frozen"]) == 1 and first["skipped"] == []
    (a_id,) = _ids(store)
    data = _data({rb: _coins("b", 30), rl: _coins("l", 30)})
    again = _freeze(monkeypatch, store, data, [_row(rb, COPY30, 0.009, COPY_AT), _row(rl, COPY30, 0.002, COPY_AT)],
                    now=NOW + 7200)  # fmt: skip
    assert [s["trigger"]["only"] for s in again["frozen"]] == [LONE]
    (skip,) = again["skipped"]
    assert skip["label"] == f"{rb.label()} | {COPY30.label()}"
    assert B in skip["why"] and A in skip["why"] and a_id in skip["why"] and "same actor" in skip["why"]
    assert "twins" in skip["why"] and "12 of the same coins" in skip["why"] and "one forward record" in skip["why"]
    assert _ids(store)[0] == a_id and len(_ids(store)) == 2  # the record under test is untouched


def test_two_twins_in_one_freeze_give_one_record(monkeypatch):
    store = Store(":memory:")
    _tape(store)
    ra, rb = _rule(A), _rule(B)
    data = _data({ra: _coins("a", 30), rb: _coins("b", 30)})
    out = _freeze(monkeypatch, store, data, [_row(rb, COPY30, 0.009, COPY_AT), _row(ra, COPY30, 0.004, COPY_AT)])
    assert [s["trigger"]["only"] for s in out["frozen"]] == [B]  # the lab's better row keeps the place
    assert [s["label"] for s in out["skipped"]] == [f"{ra.label()} | {COPY30.label()}"]


def test_wallets_that_are_not_twins_each_get_their_record(monkeypatch):
    store = Store(":memory:")
    _tape(store)
    ra, rl = _rule(A), _rule(LONE)
    data = _data({ra: _coins("a", 30), rl: _coins("l", 30)})
    out = _freeze(monkeypatch, store, data, [_row(ra, COPY30, 0.004, COPY_AT), _row(rl, COPY30, 0.002, COPY_AT)])
    assert len(out["frozen"]) == 2 and out["skipped"] == []
