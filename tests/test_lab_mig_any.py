"""A buy after ANY migration, research only (13 Nov 2026, ideas I86 and I101).

The tester's words: "the lab refused {"kind":"mig","window":0} (13 Nov 05:32, no job started): OKTech's Migration
Window cannot be sent 'any time' ... The lab scores MIG only for coins that migrated within 60 s of their create, and
those lose at 1 SOL. The ordinary migration is not measured." On the server's tape of 12-13 Nov (24 h to 13 Nov 14:20
UTC) 729 coins migrated: 210 within 60 s of their create, 332 after 1 to 10 minutes, 165 later, 22 with no create on
the tape.

So a MIG trigger takes two more forms. `window` 0 is a coin of any age: research only (OKTech has no setting for it),
said in its label and in every ranked row, refused by `paper freeze` and by the sniper builder, and it needs no create
on the tape. A window up to a week is OKTech's own setting with a number nobody has sent it yet (no maximum is
documented): scored as that window, sent as that window, and marked as not confirmed. The landing is the MIG landing
either way, and the windows scored before (10 s, 60 s) keep their labels, their ids and their rows."""

import json
import sys

import pytest

from snipetech import ops as O
from snipetech.analysis import paper
from snipetech.analysis.lab import (
    MIG_WINDOW_MAX_SECS,
    TRIGGERS,
    Trigger,
    _fire,
    collect,
    mig_window_fields,
    parse_trigger,
    rank,
    research_only_why,
)
from snipetech.oktech import strategy_sniper
from snipetech.operator.jobs import OperatorJobs
from snipetech.store import Store

from .test_lab_mig import EXIT, LAB_EXIT, SLOT0, T0, H, _cfg, _graduate, _path, _tape

ANY = Trigger("mig")  # window 0: a coin of any age
ANY_JSON = '{"kind": "mig", "window": 0}'
MIG10, MIG60, MIG600 = (Trigger("mig", window=w) for w in (10.0, 60.0, 600.0))
TEN_MIN = 1500  # slots: 600 s on the synthetic clock (0.4 s a slot)


def _slot(ts: int) -> int:
    return SLOT0 + int((ts - T0) / 0.4)


def _late_world(store: Store, n: int = 40) -> None:
    """`n` coins a minute apart, each bought out and migrated 10 minutes after its create, then bought on PumpSwap."""
    for i in range(n):
        ts = T0 + 600 + 60 * i
        _graduate(store, f"any-w{i}", ts, _slot(ts), at=TEN_MIN)
    _tape(store)


def _mig_lab(store: Store, triggers: list[Trigger]) -> dict:
    return collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=triggers, exits=[EXIT])


# -- the trigger an agent writes -------------------------------------------------------------------------------------


def test_window_0_is_a_migration_of_any_age_and_says_it_is_research_only():
    trig = parse_trigger(ANY_JSON)
    assert trig == ANY and parse_trigger({"kind": "mig", "window": 0.0}) == ANY
    why = research_only_why(trig)
    assert why and "research only" in why and "Migration Window" in why
    assert research_only_why(trig.__dict__) == why  # a stored rule reads the same as the trigger
    label = trig.label()
    assert "research only" in label and "any time after the create" in label and not label.startswith("OKTech MIG")
    assert mig_window_fields(trig) == {"research_only": True, "research_only_why": why}


def test_a_window_is_written_never_left_out():
    # {"kind": "mig"} would otherwise be the research-only rule by an oversight
    with pytest.raises(ValueError, match="Migration Window"):
        parse_trigger({"kind": "mig"})
    with pytest.raises(ValueError, match="Migration Window"):
        parse_trigger({"kind": "mig", "dev_paid": True})


def test_a_window_up_to_a_week_is_oktechs_own_setting_and_says_nobody_has_sent_it():
    trig = parse_trigger({"kind": "mig", "window": 600})
    assert trig == MIG600 and research_only_why(trig) is None
    assert trig.label().startswith("OKTech MIG: buy at the migration, within 600 s of the create")
    assert "not confirmed" in trig.label()
    (note,) = mig_window_fields(trig).values()
    assert list(mig_window_fields(trig)) == ["oktech_unconfirmed"] and "no maximum" in note
    assert parse_trigger({"kind": "mig", "window": MIG_WINDOW_MAX_SECS}).window == 7 * 86400


@pytest.mark.parametrize(
    "spec,why",
    [
        ({"kind": "mig", "window": MIG_WINDOW_MAX_SECS + 1}, "window"),  # past a week
        ({"kind": "mig", "window": -1}, "window"),
        ({"kind": "mig", "window": 600.5}, "whole seconds"),  # OKTech's migrationExpirySec is whole seconds
        ({"kind": "mig", "window": "0"}, "window"),
        ({"kind": "mig", "window": True}, "window"),
        ({"kind": "mig", "window": float("nan")}, "window"),
        ({"kind": "burst", "buyers": 3, "sol": 1, "window": 90}, "window"),  # the wider bound is MIG's alone
        ({"kind": "hunt", "sol": 2, "window": 61}, "window"),
    ],
)
def test_a_window_the_lab_could_not_score_as_meant_is_refused(spec, why):
    with pytest.raises(ValueError, match=why):
        parse_trigger(spec)


def test_the_windows_scored_before_keep_their_labels_their_ids_and_their_place_in_the_grid():
    # as computed at 6b0c932, before the research form existed
    assert MIG10.label() == "OKTech MIG: buy at the migration, within 10 s of the create"
    assert MIG60.label() == "OKTech MIG: buy at the migration, within 60 s of the create"
    paid = Trigger("mig", window=10.0, dev_paid=True)
    assert paid.label() == MIG10.label() + ", creator paid by an earlier coin's creator"
    ids = [paper.strategy_id(t.__dict__, LAB_EXIT, "+3", 1, 0.2) for t in (MIG10, MIG60, ANY)]
    assert ids == ["a7ea734d70", "c4a30b1783", "f04fb6a8f8"]
    for t in (MIG10, MIG60, paid):
        assert research_only_why(t) is None and mig_window_fields(t) == {}
    assert mig_window_fields(Trigger("hunt", sol=2.0, window=0.0)) == {}  # another kind's window says nothing here
    # the search grid holds no research-only rule: what it ranks and freezes every two hours is unchanged
    assert {t.window for t in TRIGGERS if t.kind == "mig"} == {10.0, 60.0}
    assert not [t for t in TRIGGERS if research_only_why(t)]


# -- what it fires on ---------------------------------------------------------------------------------------------


def test_a_migration_10_minutes_after_the_create_is_a_moment_at_any_age_and_not_within_60_s():
    store = Store(":memory:")
    m = _graduate(store, "any-late", T0 + 600, _slot(T0 + 600), at=TEN_MIN)
    path, launch = _path(store, m)
    assert m.migration.ts - launch.ts == 600
    fires = {t.window: _fire(t, path, launch, set(), mig=m.migration) for t in (ANY, MIG10, MIG60, MIG600)}
    assert fires[10.0] is None and fires[60.0] is None
    assert fires[0.0] is not None and fires[0.0] == fires[600.0]
    assert _fire(Trigger("mig", window=599.0), path, launch, set(), mig=m.migration) is None
    # the same state the windowed rule fires at: the last one at or before the migration's slot
    assert path.slot[fires[0.0]] == m.migration.slot


def test_the_lab_scores_any_age_at_the_mig_landing_and_leaves_the_60_s_rule_where_it_was():
    store = Store(":memory:")
    _late_world(store, 3)
    fast = _graduate(store, "any-fast", T0 + 5000, _slot(T0 + 5000), at=5)  # migrates 2 s after its create
    alone = _mig_lab(store, [MIG60])
    data = _mig_lab(store, [MIG60, ANY, MIG600])
    by = {ti: [r for r in data["rows"] if r["trigger"] == ti] for ti in range(3)}
    assert [r["mint"] for r in by[0]] == [fast.mint]  # within 60 s: the fast coin alone
    assert len(by[1]) == 4 and len(by[2]) == 4  # any age, and within 600 s: the three late coins too
    assert {r["landing"] for r in data["rows"]} == {paper.landing_key(*paper.MIG_LANDING)}
    # the same buy at the same place whichever rule found it: nothing is scored better for being research
    key = ("mint", "ts", "order", "pnl", "mig_landing", "impact", "own_rt")
    assert [{k: r[k] for k in key} for r in by[1]] == [{k: r[k] for k in key} for r in by[2]]
    # and the 60 s rule's rows are the ones it has with no research rule beside it
    assert by[0] == alone["rows"]


def test_any_age_needs_no_create_on_the_tape_and_a_window_does():
    """A coin older than the tape at its migration: its create is before the stretch the lab reads. At any age the
    migration is a moment (the tape holds the coin's last curve trade and its PumpSwap trades); a window counts from a
    create the lab cannot see there, so it gives none."""
    store = Store(":memory:")
    old = _graduate(store, "any-old", T0 - 3600, _slot(T0 - 3600), at=12_000)  # migrates 80 minutes after its create
    _tape(store)
    assert store.launch(old.mint).ts < T0 < old.migration.ts
    data = _mig_lab(store, [ANY, Trigger("mig", window=7200.0)])
    assert [(r["trigger"], r["mint"]) for r in data["rows"]] == [(0, old.mint)]
    assert data["rows"][0]["ts"] == old.migration.ts


# -- what the lab's lists say --------------------------------------------------------------------------------------


def test_every_ranked_row_of_the_research_rule_says_so_and_a_windowed_row_does_not():
    store = Store(":memory:")
    _late_world(store)
    data = _mig_lab(store, [ANY, MIG600, MIG60])
    ranked = {r["trigger_spec"]["window"]: r for r in rank(data, _cfg(), top=10, min_train=5)}
    assert set(ranked) == {0.0, 600.0}  # nothing here migrates within 60 s
    why = research_only_why(ANY)
    assert ranked[0.0]["research_only"] is True and ranked[0.0]["research_only_why"] == why
    assert "research only" in ranked[0.0]["trigger"]
    assert "research_only" not in ranked[600.0] and "no maximum" in ranked[600.0]["oktech_unconfirmed"]
    # the same moments, the same numbers: the mark changes no score
    assert ranked[0.0]["test"] == ranked[600.0]["test"] and ranked[0.0]["moments"] == 40


def test_the_lab_command_marks_the_research_rule_in_its_strategies_and_its_triggers(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    store = Store(db)
    _late_world(store)
    store.close()
    argv = ["--db", str(db), "lab", "--trigger", ANY_JSON, "--trigger", '{"kind": "mig", "window": 60}',
            "--per-tx", "0.0045"]  # fmt: skip
    assert cli.main([*argv, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    (row,) = out["strategies"]
    assert row["research_only"] is True and "research only" in row["research_only_why"]
    assert row["landing"] == "+3@1" and row["live_landing"] is True  # the MIG landing, as every MIG rule
    any_age, within_60 = out["triggers"]
    assert any_age["research_only"] is True and any_age["moments"] == 40 and any_age["ranked"] is True
    assert "research_only" not in within_60 and within_60["moments"] == 0
    assert cli.main(argv) == 0  # the text report carries it in the rule's own name
    assert "research only" in capsys.readouterr().out


# -- what never happens to it ----------------------------------------------------------------------------------------


def test_paper_freeze_refuses_the_research_rule_with_the_reason_before_any_lab():
    store = Store(":memory:")  # no tape at all: the refusal comes before anything is read
    with pytest.raises(ValueError, match="research only") as err:
        paper.freeze(store, _cfg(), triggers=[MIG600, ANY], now=T0 + 3 * H)
    assert "cannot be frozen" in str(err.value) and "Migration Window" in str(err.value)
    assert store.coverage("paper_strategies") == {}


def test_paper_freeze_leaves_the_research_rule_out_of_a_lab_it_is_handed_and_says_why():
    store = Store(":memory:")
    _late_world(store)
    data = _mig_lab(store, [ANY, MIG600])
    rep = paper.freeze(store, _cfg(), top=4, now=T0 + 3 * H, data=data)
    assert [s["trigger"] for s in rep["frozen"]] == [MIG600.__dict__]  # the rule OKTech has a setting for
    (left,) = [s for s in rep["skipped"] if "research only" in s["why"]]
    assert left["label"].startswith(ANY.label())
    assert all(s["trigger"]["window"] for s in store.coverage("paper_strategies")["strategies"])


def test_the_freeze_command_refuses_it_in_words(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "snipetech.db"
    store = Store(db)
    _late_world(store, 3)
    store.close()
    assert cli.main(["--db", str(db), "paper", "freeze", "--trigger", ANY_JSON, "--per-tx", "0.0045"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("paper freeze: ") and "research only" in err
    check = Store(db)
    try:
        assert check.coverage("paper_strategies") == {}
    finally:
        check.close()


@pytest.fixture
def jobs(tmp_path) -> OperatorJobs:
    """The agents' job runner with a probe in place of the snipetech binary (as tests/operator/test_jobs_tools.py)."""
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    j = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main", max_jobs=3)
    j.ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                  command=[sys.executable, "-c", "import sys; print('ran', sys.argv[1:])"])  # fmt: skip
    return j


def test_an_agent_can_start_the_lab_the_tester_was_refused_and_not_a_freeze_of_it(jobs):
    """13 Nov 05:32: `lab --trigger {"kind":"mig","window":0}` came back "--trigger refused, no job started". The lab
    runs it now. A freeze of it is refused before any job starts, with the reason, so no job dies on it."""
    for argv in (
        ["paper", "freeze", "--trigger", ANY_JSON],
        ["paper", "freeze", f"--trigger={ANY_JSON}", "--hours", "24"],
    ):
        with pytest.raises(PermissionError, match="no job started.*research only"):
            jobs.start(argv)
    assert not list((jobs.ops.data / "jobs").glob("*.json"))
    assert jobs.start(["lab", "--trigger", ANY_JSON, "--hours", "24", "--json"])["id"]
    assert jobs.start(["paper", "freeze", "--trigger", '{"kind": "mig", "window": 600}'])["id"]


def test_no_sniper_is_built_from_the_research_rule():
    strat = {"id": "m", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": ANY.__dict__}
    with pytest.raises(ValueError, match="research only"):
        strategy_sniper(strat, 1)


def test_a_wide_window_is_sent_as_scored_with_a_note_that_oktech_has_not_confirmed_it():
    strat = {"id": "m", "label": "t", "buy_sol": 0.1, "exit": LAB_EXIT, "trigger": MIG600.__dict__}
    p = strategy_sniper(strat, 1)
    assert p.config["mode"] == "mig" and p.config["migrationExpirySec"] == 600
    assert any("600 s" in n and "no maximum" in n for n in p.notes)
    # the windows scored before carry no such note
    usual = strategy_sniper(strat | {"trigger": MIG60.__dict__}, 1)
    assert not any("no maximum" in n for n in usual.notes)
