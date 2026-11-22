"""`paper freeze --exit LABEL`: a rule frozen with the exit named, the one its live sniper runs (22 Nov).

The tester's two freezes of I597 (Cooker, copied the way his live sniper copies him) failed: `paper freeze` took no
`--exit`, so a rule was frozen with the lab's pick of the day, and the forward record judged an exit no sniper runs
(Decu at mcap <=45 was frozen with hold5s beside the live hold12s, I604). With `--exit` the lab scores the exits named
and no other (as `lab --exit`: a wallet rule's exit carries the copy exit and the watch delay), the record keeps the
names, and the names are part of the rule (`rule_key`, as a recipe's name is): the rule with the live exit runs forward
beside the same trigger frozen with the lab's pick."""

import pytest

from snipetech.analysis import paper
from snipetech.config import Config
from snipetech.sim.exits import ExitParams, exit_from_label
from snipetech.store import Store
from tests.test_paper_one_record import COPY30, COPY_AT, KOL, NOW, _coins, _data, _row

COPY12 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=12, sell_on_dev_sell=False, watch_secs=1.3,
                    copy_exit_pct=0.5)  # fmt: skip


def _lab(monkeypatch, ranked, seen=None):
    """The lab as `freeze` runs it: `collect` records the exits it was handed, `rank` gives `ranked`."""

    def collect(*_a, **kw):
        if seen is not None:
            seen.append(kw.get("exits"))
        return _data({KOL: _coins("k", 57)})

    monkeypatch.setattr(paper, "collect", collect)
    monkeypatch.setattr(paper, "rank", lambda d, cfg, top=10, **_: list(ranked))


def test_a_freeze_scores_only_the_exits_named_and_keeps_their_names(monkeypatch):
    store = Store(":memory:")
    seen: list = []
    _lab(monkeypatch, [_row(KOL, COPY12, 0.004, COPY_AT)], seen)
    rep = paper.freeze(store, Config(), top=4, triggers=[KOL], named_exits=["hold12s"], now=NOW)
    assert seen == [[exit_from_label("hold12s")]]
    (s,) = rep["frozen"]
    assert s["exit_named"] == ["hold12s"] and s["exit"]["max_hold_secs"] == 12 and rep["exits_named"] == ["hold12s"]
    store.close()


def test_the_rule_with_the_named_exit_runs_beside_the_labs_pick(monkeypatch):
    """KOL is in the paper test with the lab's pick (hold30s): before this the same rule with hold12s was "already in
    the paper test", one record a rule whichever exit. Named, it is a rule of its own; named again, it is the one under
    test."""
    store = Store(":memory:")
    _lab(monkeypatch, [_row(KOL, COPY30, 0.003, COPY_AT)])
    (pick,) = paper.freeze(store, Config(), top=4, triggers=[KOL], now=NOW)["frozen"]
    _lab(monkeypatch, [_row(KOL, COPY12, 0.004, COPY_AT)])
    rep = paper.freeze(store, Config(), top=4, triggers=[KOL], named_exits=["hold12s"], now=NOW + 60)
    (live,) = rep["frozen"]
    assert live["id"] != pick["id"] and rep["skipped"] == []
    again = paper.freeze(store, Config(), top=4, triggers=[KOL], named_exits=["hold12s"], now=NOW + 120)
    assert again["frozen"] == [] and len(store.coverage("paper_strategies")["strategies"]) == 2
    assert paper.rule_key(KOL.__dict__, "+6", 0, 0.2) != paper.rule_key(KOL.__dict__, "+6", 0, 0.2, named="hold12s")
    store.close()


def test_a_named_exit_goes_with_typed_triggers_and_never_with_a_recipe(monkeypatch):
    from snipetech.analysis.lab import recipe

    store = Store(":memory:")
    _lab(monkeypatch, [])
    with pytest.raises(ValueError, match="--trigger"):
        paper.freeze(store, Config(), top=4, named_exits=["hold12s"], now=NOW)
    with pytest.raises(ValueError, match="recipe"):
        paper.freeze(store, Config(), top=4, recipe=recipe("6z91"), named_exits=["hold12s"], now=NOW)
    store.close()


def test_an_exit_that_cannot_be_read_is_refused_before_any_tape_is_read(monkeypatch):
    store = Store(":memory:")

    def collect(*_a, **_kw):
        raise AssertionError("the tape was read")

    monkeypatch.setattr(paper, "collect", collect)
    with pytest.raises(ValueError, match="cannot read"):
        paper.freeze(store, Config(), top=4, triggers=[KOL], named_exits=["hold12 banana"], now=NOW)
    store.close()


def test_the_command_takes_exit(monkeypatch, tmp_path):
    """`paper freeze --trigger ... --exit hold12s`, as the tester sent it (job 20261122-054622-9ff77e), reaches the
    freeze with the exit named."""
    from snipetech import cli

    got = {}

    def freeze(*_a, **kw):
        got.update(kw)
        return {"frozen": [], "kept": [], "skipped": [], "tape_hours": 0.0, "landing": {}}

    monkeypatch.setattr(paper, "freeze", freeze)
    trig = '{"kind":"kol","group":"big","sol":2,"repeats":"also","max_mcap":200}'
    db = tmp_path / "snipetech.db"
    Store(db).close()
    argv = ["--db", str(db), "paper", "freeze", "--trigger", trig, "--copy-position", "+2x", "--buy-bid", "0.05",
            "--buy", "5", "--exit", "hold12s", "--hours", "168", "--top", "40", "--per-tx", "0.0045", "--json"]  # fmt: skip
    assert cli.main(argv) == 0
    assert got["named_exits"] == ["hold12s"]
