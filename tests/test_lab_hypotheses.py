"""The agents' own strategy hypotheses: a trigger an agent writes is checked first (bounded numbers, only the fields its
kind reads, whole numbers where OKTech needs them, so every hunt hypothesis is one `oktech strategy` can send as
scored), then run alone on the newest tape at the landing where it trades live, and frozen into the forward paper test
only when it pays there."""

import json

import pytest

from snipetech.analysis import paper
from snipetech.analysis.lab import TRIGGERS, Trigger, collect, parse_trigger
from snipetech.config import Config
from snipetech.store import Store

from .test_lab import NOW, whale_world

LAB_EXIT = {"stop_loss": 0.3, "max_hold_secs": 300, "stale_secs": 1.0}
HUNT = Trigger("hunt", sol=2.0, window=2.0, dev_max=1.0)  # fires on every whale-world coin at its 2.5 SOL buy
WHALE = Trigger("whale", sol=2.2)  # not in the grid: an agent's own variant
HUNT_JSON, WHALE_JSON = '{"kind": "hunt", "sol": 2, "window": 2, "dev_max": 1}', '{"kind": "whale", "sol": 2.2}'


@pytest.mark.parametrize(
    "spec,want",
    [
        ('{"kind": "launch", "plain": true}', Trigger("launch", plain=True)),
        ({"kind": "burst", "buyers": 5, "sol": 1, "window": 2, "max_age": 30, "plain": True},
         Trigger("burst", buyers=5, sol=1.0, window=2.0, max_age=30.0, plain=True)),
        ({"kind": "whale", "sol": 1.5, "max_age": 2, "plain": True, "solo": True},
         Trigger("whale", sol=1.5, max_age=2.0, plain=True, solo=True)),
        ({"kind": "kol", "sol": 0.5, "group": "cand", "max_age": 60, "plain": True},
         Trigger("kol", sol=0.5, group="cand", max_age=60.0, plain=True)),
        ({"kind": "momentum", "buyers": 30, "sol": 4, "progress": 0.4, "holders": 20, "ret": 0.3, "plain": True},
         Trigger("momentum", buyers=30, sol=4.0, progress=0.4, holders=20, ret=0.3, plain=True)),
        ('{"kind": "hunt", "sol": 4, "buyers": 2, "window": 1.5, "max_buyers": 4, "dev_max": 1}',
         Trigger("hunt", sol=4.0, buyers=2, window=1.5, max_buyers=4, dev_max=1.0)),
    ],
)  # fmt: skip
def test_an_agents_trigger_of_every_kind_is_read_as_written(spec, want):
    """Each case sets every field its kind reads (TRIGGER_KNOBS), so none of them is refused as not applying."""
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


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def test_freeze_runs_only_the_hypotheses_and_keeps_each_at_its_live_landing():
    """`paper freeze --trigger ... --only-live`: the lab runs only the agent's triggers, an OKTech hunt is scored where
    a hunt buy lands (3 trades into the slot after its trigger), and each one that pays there is frozen there."""
    store = Store(":memory:")
    whale_world(store)  # at their live landings: the whale +0.012 SOL a trade, the hunt +0.007
    rep = paper.freeze(store, _cfg(), top=4, position="+6", latency=0, triggers=[HUNT, WHALE], only_live=True, now=NOW)
    by = {s["trigger"]["kind"]: s for s in rep["frozen"]}
    assert len(rep["frozen"]) == 2
    assert by["hunt"]["trigger"] == HUNT.__dict__ and by["whale"]["trigger"] == WHALE.__dict__
    assert (by["hunt"]["latency"], by["hunt"]["position"], by["hunt"]["lab"]["landing"]) == (1, "+3", "+3@1")
    assert (by["whale"]["latency"], by["whale"]["position"], by["whale"]["lab"]["landing"]) == (0, "+6", "+6@0")
    assert all(s["lab"]["ev_lower"] > 0 for s in rep["frozen"])


def test_freeze_reads_only_the_newest_hours_of_tape():
    store = Store(":memory:")
    whale_world(store, 80)  # a coin a minute; 56 of them launch in the tape's newest hour
    rep = paper.freeze(store, _cfg(), top=4, position="+6", triggers=[WHALE], hours=1, now=NOW)
    (s,) = rep["frozen"]
    assert rep["tape_hours"] == pytest.approx(1.0)
    assert s["lab"]["n"] == 23  # 56 moments: the exit picked on the older 33, judged on the newer 23


# -- the commands the agents start (start_job) -------------------------------------------------------------------------


def _tape_db(tmp_path, n: int = 80):
    """A database file holding the whale world: 80 coins a minute apart, 56 of them in the tape's newest hour."""
    db = tmp_path / "data" / "snipetech.db"
    db.parent.mkdir(parents=True)
    st = Store(db)
    try:
        whale_world(st, n)
    finally:
        st.close()
    return db


def test_the_lab_command_runs_an_agents_triggers_on_the_newest_hours(tmp_path, capsys):
    """`lab --trigger <json> --hours N --json`: only those triggers, on the newest N hours of tape; a hunt where a hunt
    buy lands whatever --position says, every other trigger at --latency / --position; each ranked row says its landing
    and whether that is where it trades live."""
    from snipetech import cli

    db = _tape_db(tmp_path)
    argv = ["--db", str(db), "lab", "--trigger", HUNT_JSON, "--trigger", WHALE_JSON, "--hours", "1", "--position", "+1",
            "--json"]  # fmt: skip
    assert cli.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    rows = {r["trigger_spec"]["kind"]: r for r in out["strategies"]}
    assert set(rows) == {"hunt", "whale"} and rows["whale"]["trigger_spec"] == WHALE.__dict__
    assert (rows["hunt"]["landing"], rows["hunt"]["live_landing"]) == ("+3@1", True)
    assert (rows["whale"]["landing"], rows["whale"]["live_landing"]) == ("+1@0", False)  # copies land at +6, not +1
    for r in rows.values():
        assert r["moments"] == 56 and r["per_hour"] == pytest.approx(56.0)  # only the coins of the newest hour
        assert r["trigger"] and r["exit"] and r["test"]["n"] == 23 and r["test"]["ev_lower"] is not None
    assert out["hours"] == 1 and out["tape_hours"] == pytest.approx(1.0)
    assert [(t["trigger"], t["landing"], t["moments"], t["ranked"]) for t in out["triggers"]] == [
        (HUNT.label(), "+3@1", 56, True),
        (WHALE.label(), "+1@0", 56, True),
    ]


def test_the_lab_text_says_which_rows_are_scored_at_another_landing(tmp_path, capsys):
    from snipetech import cli

    db = _tape_db(tmp_path)
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", "--position", "+1"]) == 0
    text = capsys.readouterr().out
    assert "(+1 of the slot)" in text and "hunt triggers at +3@1" in text


def test_the_lab_command_refuses_a_hypothesis_oktech_could_not_run_before_any_lab(tmp_path, capsys, monkeypatch):
    from snipetech import cli
    from snipetech.analysis import lab

    monkeypatch.setattr(lab, "collect", lambda *a, **k: pytest.fail("a refused hypothesis must not start a lab"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--db", str(tmp_path / "x.db"), "lab", "--trigger", '{"kind": "hunt", "sol": 2.5, "window": 1}'])
    assert exc.value.code == 2 and "whole numbers" in capsys.readouterr().err


@pytest.mark.parametrize("hours", ["0", "-1", "nan", "inf"])
def test_the_lab_command_refuses_hours_that_are_no_window_of_tape(hours, capsys):
    """An infinite window would crash the job after it took the lab lock; any other non-positive one reads no tape."""
    from snipetech import cli

    for cmd in (["lab"], ["paper", "freeze"]):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args([*cmd, "--hours", hours])
        assert exc.value.code == 2 and "hours above 0" in capsys.readouterr().err


def test_paper_freeze_from_the_command_line_freezes_only_the_hypotheses_at_their_live_landing(tmp_path, capsys):
    """At --position +1 the whale is scored 1 trade behind its trigger, where no live copy lands: --only-live keeps it
    out. The hunt is scored where a hunt buy lands whatever --position says, and is frozen there."""
    from snipetech import cli

    db = _tape_db(tmp_path)
    argv = ["--db", str(db), "paper", "freeze", "--trigger", HUNT_JSON, "--trigger", WHALE_JSON, "--hours", "1",
            "--position", "+1", "--only-live", "--json"]  # fmt: skip
    assert cli.main(argv) == 0
    rep = json.loads(capsys.readouterr().out)
    got = [(s["trigger"]["kind"], s["latency"], s["position"], s["lab"]["landing"]) for s in rep["frozen"]]
    assert got == [("hunt", 1, "+3", "+3@1")]
    assert rep["tape_hours"] == pytest.approx(1.0)
    st = Store(db)
    try:
        assert [s["trigger"] for s in st.coverage("paper_strategies")["strategies"]] == [HUNT.__dict__]
    finally:
        st.close()
    # without the new flags `paper freeze` is what it was: the grid, the whole tape, every landing kept
    plain = cli.build_parser().parse_args(["paper", "freeze"])
    assert (plain.trigger, plain.hours, plain.only_live, plain.position) == (None, None, False, "+6")
