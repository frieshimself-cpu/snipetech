"""The agents' own strategy hypotheses: a trigger an agent writes is checked first (bounded numbers, only the fields its
kind reads, whole numbers where OKTech needs them, so every hunt hypothesis is one `oktech strategy` can send as
scored), then run alone on the newest tape at the landing where it trades live, and frozen into the forward paper test
only when it pays there."""

import pytest

from snipetech.analysis import paper
from snipetech.analysis.lab import TRIGGERS, Trigger, collect, parse_trigger
from snipetech.config import Config
from snipetech.store import Store

from .test_lab import whale_world

LAB_EXIT = {"stop_loss": 0.3, "max_hold_secs": 300, "stale_secs": 1.0}


@pytest.mark.parametrize(
    "spec,want",
    [
        ('{"kind": "launch", "plain": true}', Trigger("launch", plain=True)),
        ({"kind": "burst", "buyers": 5, "sol": 1, "window": 2}, Trigger("burst", buyers=5, sol=1.0, window=2.0)),
        ({"kind": "whale", "sol": 1.5, "max_age": 2, "plain": True, "solo": True},
         Trigger("whale", sol=1.5, max_age=2.0, plain=True, solo=True)),
        ({"kind": "kol", "sol": 0.5, "group": "cand"}, Trigger("kol", sol=0.5, group="cand")),
        ({"kind": "momentum", "buyers": 30, "sol": 4, "progress": 0.4, "holders": 20, "ret": 0.3},
         Trigger("momentum", buyers=30, sol=4.0, progress=0.4, holders=20, ret=0.3)),
        ('{"kind": "hunt", "sol": 4, "window": 1.5, "max_buyers": 4, "dev_max": 1}',
         Trigger("hunt", sol=4.0, window=1.5, max_buyers=4, dev_max=1.0)),
    ],
)  # fmt: skip
def test_an_agents_trigger_of_every_kind_is_read_as_written(spec, want):
    assert parse_trigger(spec) == want


def test_a_hypothesis_equal_to_a_grid_trigger_gets_its_strategy_id():
    """An agent writes 3 where the grid holds 3.0: both are read as 3.0 SOL, so a hunt written with whole numbers is
    the grid's hunt and is never frozen a second time under another id."""
    mine = parse_trigger('{"kind": "hunt", "sol": 3, "window": 1, "dev_max": 1}')
    grid = Trigger("hunt", sol=3.0, window=1.0, dev_max=1.0)
    assert grid in TRIGGERS
    assert paper.strategy_id(mine.__dict__, LAB_EXIT, "+3", 1, 0.2) == paper.strategy_id(
        grid.__dict__, LAB_EXIT, "+3", 1, 0.2
    )
    assert type(parse_trigger({"kind": "burst", "buyers": 3.0, "sol": 1, "window": 2}).buyers) is int


def test_every_trigger_the_lab_prints_can_be_pasted_back():
    """The lab prints each strategy's trigger_spec (every field, defaults included); an agent that pastes one back gets
    the same trigger and the same strategy id."""
    for t in TRIGGERS:
        back = parse_trigger(dict(t.__dict__))
        assert back == t, t
        assert paper.strategy_id(back.__dict__, LAB_EXIT, "+6", 0, 0.2) == paper.strategy_id(
            t.__dict__, LAB_EXIT, "+6", 0, 0.2
        )


@pytest.mark.parametrize(
    "spec,why",
    [
        ("not json", "JSON"),
        ("[1, 2]", "JSON object"),
        ({"sol": 2}, "kind"),
        ({"kind": ["hunt"]}, "kind"),
        ({"kind": "event", "event": "trending"}, "kind"),  # events need the events table, not a hypothesis
        ({"kind": "whale", "sol": 2, "colour": "red"}, "colour"),
        ({"kind": "whale", "sol": 150}, "sol"),
        ({"kind": "whale", "sol": -1}, "sol"),
        ({"kind": "whale", "sol": "2"}, "sol"),
        ({"kind": "whale", "sol": True}, "sol"),
        ({"kind": "whale", "sol": float("nan")}, "sol"),
        ({"kind": "burst", "buyers": 2.5, "sol": 1, "window": 2}, "buyers"),
        ({"kind": "burst", "buyers": 300, "sol": 1, "window": 2}, "buyers"),
        ({"kind": "burst", "buyers": 3, "sol": 1, "window": 90}, "window"),
        ({"kind": "whale", "sol": 2, "max_age": 601}, "max_age"),
        ({"kind": "momentum", "progress": 1.5}, "progress"),
        ({"kind": "momentum", "holders": 20_000}, "holders"),
        ({"kind": "momentum", "ret": -2}, "ret"),
        ({"kind": "momentum", "ret": 11}, "ret"),
        ({"kind": "whale", "sol": 2, "plain": "yes"}, "plain"),
        ({"kind": "kol", "group": "friends"}, "group"),
        ({"kind": "whale", "sol": 2, "window": 3}, "window"),  # a whale reads no window: the agent meant something else
        ({"kind": "launch", "sol": 2}, "sol"),
        ({"kind": "momentum", "buyers": 20, "max_age": 30}, "max_age"),  # momentum has no age limit
        ({"kind": "hunt", "sol": 2, "window": 1, "max_buyers": 300}, "max_buyers"),
        ({"kind": "hunt", "sol": 2, "window": 1, "dev_max": 101}, "dev_max"),
        ({"kind": "hunt", "sol": 2.5, "window": 1}, "whole"),  # 5 Nov 22:11: OKTech ignored 2.5, bought every launch
        ({"kind": "hunt", "sol": 2, "window": 1, "dev_max": 0.5}, "whole"),
        ({"kind": "hunt", "sol": 2, "window": 1.0005}, "whole"),  # 1000.5 ms
        ({"kind": "hunt", "sol": 2}, "window"),  # OKTech needs a time window
        ({"kind": "hunt", "sol": 2, "window": 1, "plain": True}, "plain"),  # hunt cannot skip mayhem coins
        ({"kind": "hunt", "sol": 2, "window": 1, "solo": True}, "solo"),  # nor see the create transaction
        ({"kind": "hunt", "sol": 2, "window": 1, "buyers": 4, "max_buyers": 3}, "max_buyers"),  # could never fire
    ],
)
def test_a_trigger_the_lab_or_oktech_could_not_run_as_meant_is_refused(spec, why):
    with pytest.raises(ValueError, match=why):
        parse_trigger(spec)


def test_collect_runs_only_the_triggers_it_is_given():
    store = Store(":memory:")
    whale_world(store)  # 40 coins: dev 0.5, a 0.2 buy, then a 2.5 SOL whale 2 s after the create
    mine = [Trigger("whale", sol=2.2), Trigger("hunt", sol=2.0, window=2.0, dev_max=1.0)]
    data = collect(store, Config(), 0.2, 0, "+6", triggers=mine, landings={"hunt": paper.HUNT_LANDING})
    assert data["triggers"] == mine
    by = {ti: sum(r["trigger"] == ti for r in data["rows"]) for ti in range(len(mine))}
    assert by == {0: 40, 1: 40} and len(data["rows"]) == 80  # once per coin each, nothing from the grid
    assert {r["landing"] for r in data["rows"] if r["trigger"] == 1} == {"+3@1"}
