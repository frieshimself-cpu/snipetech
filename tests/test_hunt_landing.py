"""A hunt's landing as a knob of the lab (13 Nov 2026, idea I89).

The tester could not run its test: lab 44595b got `--position +7` with a hunt trigger and still scored the hunt at
+3@1 (a hunt has its own fixed landing), and `--position +7@1` was refused (6808ed). OKTech's live hunt buys landed a
median of 3 trades behind the trade that crossed the threshold, which is the lab's +3@1; on busy coins (15 SOL and more
already in) the only two live fills landed 7 and 11 behind, and lost. A hunt rule that pays only at +3@1 is no
evidence for busy coins.

`lab --hunt-landing +N@D` (and `paper freeze --hunt-landing +N@D`) scores every hunt, whale and burst trigger of the
run D blocks after the block of the crossing trade, behind the first N trades of the coin in that block. Without the
flag nothing moves. Every row names the landing; one that can land ahead of the measured live place says so."""

import json
import sys
import time

import pytest

from snipetech import ops as O
from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger, _fire, collect, entry_at
from snipetech.config import Config
from snipetech.operator.jobs import OperatorJobs
from snipetech.sim.exits import ExitParams
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
HUNT = Trigger("hunt", sol=2.0, window=2.0, dev_max=1.0)  # fires on every busy-world coin at its 2.5 SOL buy
HUNT_JSON, WHALE_JSON = '{"kind": "hunt", "sol": 2, "window": 2, "dev_max": 1}', '{"kind": "whale", "sol": 2.2}'
KOL_JSON, LAUNCH_JSON = '{"kind": "kol", "sol": 2}', '{"kind": "launch"}'
TIMER = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False)  # sells 10 s after the buy
RUSH, LATE = 9, 12  # trades in the block after the crossing trade's, and in the block after that


def busy_world(store: Store, n: int = 40) -> None:
    """A busy launch, a coin a minute. The 2.5 SOL buy crosses a 2 SOL hunt threshold in block 5 with one more buy
    behind it in that block; nine buys of 0.5 SOL fill block 6 and twelve of 0.3 SOL block 7, each lifting the price,
    so a buy 3 trades into block 6 is cheaper than one 7 trades into it, and that is cheaper than one 11 trades into
    block 7. Six more buyers lift the price for eight seconds, then sell."""
    for k in range(n):
        m = Market(store, addr(f"hl-{k}"), addr(f"hl-dev-{k}"), NOW - 5000 + k * 60, 30_000_000 + k * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr(f"hl-small-{k}"), 0.2, 2)
        m.buy(addr(f"hl-whale-{k}"), 2.5, 5)  # the crossing trade: 0.5 + 0.2 + 2.5 SOL within 2 s of the create
        m.buy(addr(f"hl-tail-{k}"), 0.3, 5)
        for j in range(RUSH):
            m.buy(addr(f"hl-rush-{k}-{j}"), 0.5, 6)
        for j in range(LATE):
            m.buy(addr(f"hl-late-{k}-{j}"), 0.3, 7)
        for j in range(6):
            m.buy(addr(f"hl-fan-{k}-{j}"), 0.4, 10 + 3 * j)
        for j in range(6):
            m.sell(addr(f"hl-fan-{k}-{j}"), 1.0, 60 + j * 5)
        m.commit()
    store.set_coverage("tape", {"spans": [[NOW - 5100, NOW]]})


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _busy_db(tmp_path, name: str = "data", kols: bool = False):
    """A database file holding the busy world: 80 coins a minute apart, 56 of them in the tape's newest hour. `kols`:
    every coin's 2.5 SOL buyer is a labeled KOL, so a KOL trigger (a copy) fires on its buy."""
    db = tmp_path / name / "snipetech.db"
    db.parent.mkdir(parents=True)
    st = Store(db)
    try:
        busy_world(st, 80)
        for k in range(80 if kols else 0):
            st.set_label(addr(f"hl-whale-{k}"), f"whale-{k}", "kol")
    finally:
        st.close()
    return db


def _lab(capsys, db, *flags: str) -> dict:
    from snipetech import cli

    assert cli.main(["--db", str(db), "lab", *flags, "--hours", "1", "--json"]) == 0
    return json.loads(capsys.readouterr().out)


# -- the knob's own words ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text,want", [("+7@1", (1, "+7")), ("+11@2", (2, "+11")), ("+3@1", (1, "+3")),
                                       ("+0@0", (0, "+0")), (" +07@01 ", (1, "+7")), ("+99@10", (10, "+99"))])  # fmt: skip
def test_a_hunt_landing_is_read_as_the_lab_prints_it(text, want):
    """ "+N@D", the key every lab row carries ("+3@1"): N trades of the coin ahead of us, D blocks after the crossing
    trade's block. It comes back as (blocks, position), the pair the lab lands every trigger with."""
    assert paper.parse_hunt_landing(text) == want
    assert paper.landing_key(*paper.parse_hunt_landing(text)) == text.strip().replace("+07@01", "+7@1")


@pytest.mark.parametrize("text", ["7@1", "+7", "+7@", "@1", "middle@1", "+7@-1", "+7x@1", "+7@1.5", "", "+7 @1",
                                  "+100@1", "+7@11", "+3@1@1", "bid@0.05", None, "+-3@1"])  # fmt: skip
def test_a_hunt_landing_the_lab_cannot_place_is_refused_in_words(text):
    with pytest.raises(ValueError, match=r"\+N@D") as exc:
        paper.parse_hunt_landing(text)
    assert "+7@1" in str(exc.value)  # the refusal shows one that works


def test_the_default_hunt_landing_is_the_measured_live_place_and_nothing_else_moves():
    """No landing asked: the very map the lab always used. One asked: hunt, whale and burst (all run live through
    OKTech hunt) move together; a launch snipe, a MIG buy and the deployer record stay where they are."""
    assert paper.hunt_landings(None) is paper.LAUNCH_LANDINGS
    assert paper.HUNT_LANDING == (1, "+3")
    moved = paper.hunt_landings((1, "+7"))
    assert {k: moved[k] for k in ("hunt", "whale", "burst")} == dict.fromkeys(("hunt", "whale", "burst"), (1, "+7"))
    for kind in ("launch", "mig", "dev"):
        assert moved[kind] == paper.LAUNCH_LANDINGS[kind], kind
    assert paper.LAUNCH_LANDINGS["hunt"] == (1, "+3")  # the shared map itself is never written to


@pytest.mark.parametrize("landing,vs", [((1, "+3"), "live"), ((1, "+7"), "behind"), ((2, "+11"), "behind"),
                                        ((2, "+3"), "behind"), ((1, "+2"), "ahead"), ((0, "+3"), "ahead"),
                                        ((0, "+50"), "ahead"), ((3, "+0"), "ahead")])  # fmt: skip
def test_a_landing_that_can_be_ahead_of_the_live_place_is_called_ahead(landing, vs):
    """CLAUDE.md rule 2. "behind": at least as many trades and at least as many blocks as +3@1, so on every moment
    the buy is at or behind the live place. Fewer trades, or the crossing trade's own block, can land ahead of where
    live hunt buys landed: such a result is no evidence, and the lab says so."""
    assert paper.hunt_landing_vs_live(landing) == vs


# -- where the buy lands -----------------------------------------------------------------------------------------------


def test_a_hunt_with_a_landing_buys_behind_the_nth_trade_of_the_block_d_later():
    store = Store(":memory:")
    busy_world(store, 1)
    launch = store.launch(addr("hl-0"))
    path = MarketPath.build(store.trades_for_mint(addr("hl-0")), launch)
    i = _fire(HUNT, path, launch, set())
    assert path.users[i] == addr("hl-whale-0")  # the crossing trade

    def lands(text: str) -> tuple[str, int]:
        lat, pos = paper.parse_hunt_landing(text)
        e = entry_at("hunt", path, i, int(path.slot[i]), lat, pos)
        return path.users[e], int(path.slot[e]) - int(path.slot[i])  # the last trade ahead of our buy; blocks later

    assert lands("+3@1") == (addr("hl-rush-0-2"), 1)  # today's place: behind the 3rd trade of the next block
    assert lands("+7@1") == (addr("hl-rush-0-6"), 1)  # behind the 7th trade of the next block
    assert lands("+11@2") == (addr("hl-late-0-10"), 2)  # behind the 11th trade of the block two later
    assert lands("+1@0") == (addr("hl-tail-0"), 0)  # one trade behind the crossing trade, in its own block
    # a block with fewer trades than asked: behind its last trade, never into a later block
    assert lands("+20@1") == (addr(f"hl-rush-0-{RUSH - 1}"), 1)


def test_the_lab_scores_a_hunt_at_the_landing_asked_and_every_row_says_it():
    """The same 40 moments at three landings: each row names its landing and how many trades stood between the
    crossing trade and our buy (`behind`), and the further back the buy, the less the same timer exit makes."""
    store = Store(":memory:")
    busy_world(store)

    def rows(landing):
        data = collect(store, Config(), 0.2, 0, "+6", triggers=[HUNT], exits=[TIMER],
                       landings=paper.hunt_landings(landing))  # fmt: skip
        assert len(data["rows"]) == 40 and data["landing_of"][0] == list(landing or paper.HUNT_LANDING)
        return data["rows"]

    live, seven, eleven = rows(None), rows((1, "+7")), rows((2, "+11"))
    assert {(r["landing"], r["behind"]) for r in live} == {("+3@1", 4)}  # 1 left in the crossing block + 3
    assert {(r["landing"], r["behind"]) for r in seven} == {("+7@1", 8)}
    assert {(r["landing"], r["behind"]) for r in eleven} == {("+11@2", 1 + RUSH + 11)}
    for a, b, c in zip(live, seven, eleven, strict=True):
        assert (a["mint"], a["ts"]) == (b["mint"], b["ts"]) == (c["mint"], c["ts"])  # the same moments
        assert a["pnl"][0] > b["pnl"][0] > c["pnl"][0] > 0  # a dearer entry, the same sale


def test_without_a_landing_the_lab_scores_a_hunt_exactly_as_before():
    """The default is the map itself: the same rows, number for number, as the lab gave before the knob (`behind`
    is the one new field, and it only says where the buy stood)."""
    store = Store(":memory:")
    busy_world(store)
    args = (store, Config(), 0.2, 0, "+6")
    before = collect(*args, triggers=[HUNT, Trigger("whale", sol=2.2)], landings=paper.LAUNCH_LANDINGS)["rows"]
    now = collect(*args, triggers=[HUNT, Trigger("whale", sol=2.2)], landings=paper.hunt_landings(None))["rows"]
    same = collect(*args, triggers=[HUNT, Trigger("whale", sol=2.2)], landings=paper.hunt_landings((1, "+3")))["rows"]
    assert before == now == same and len(now) == 80
    assert {r["landing"] for r in now} == {"+3@1"}


# -- the lab command ---------------------------------------------------------------------------------------------------


def test_the_lab_command_scores_hunt_rules_at_the_landing_asked(tmp_path, capsys):
    """`lab --trigger <hunt> --hunt-landing +7@1`: the hunt and the whale (run live through OKTech hunt) are scored
    7 trades into the block after their trigger; every row says so, says it is not the live place and that it is
    behind it; a launch snipe keeps its own landing; and the result is worse than at +3@1 on the same moments."""
    db = _busy_db(tmp_path)
    flags = ("--trigger", HUNT_JSON, "--trigger", WHALE_JSON, "--trigger", LAUNCH_JSON)
    live, out = _lab(capsys, db, *flags), _lab(capsys, db, *flags, "--hunt-landing", "+7@1")
    rows = {r["trigger_spec"]["kind"]: r for r in out["strategies"]}
    for kind in ("hunt", "whale"):
        r = rows[kind]
        assert (r["landing"], r["live_landing"], r["landing_vs_live"]) == ("+7@1", False, "behind"), kind
        assert "--hunt-landing" in r["landing_note"] and "+3@1" in r["landing_note"]
    assert (rows["launch"]["landing"], rows["launch"]["live_landing"]) == ("start@1", True)
    assert "landing_vs_live" not in rows["launch"]
    assert [(t["landing"], t["live_landing"], t.get("landing_vs_live")) for t in out["triggers"]] == [
        ("+7@1", False, "behind"), ("+7@1", False, "behind"), ("start@1", True, None)]  # fmt: skip
    was = {r["trigger_spec"]["kind"]: r for r in live["strategies"]}
    assert was["hunt"]["moments"] == rows["hunt"]["moments"] == 56
    assert rows["hunt"]["test"]["mean"] < was["hunt"]["test"]["mean"]  # the dearer entry shows in the result
    # the run's own block: the landing, the live one, which way it differs, and where the buys really stood
    hl = out["hunt_landing"]
    assert (hl["landing"], hl["live"], hl["vs_live"], hl["better_than_live"]) == ("+7@1", "+3@1", "behind", False)
    assert hl["trades_ahead"] == 7 and hl["blocks_later"] == 1 and hl["applies_to"] == ["burst", "hunt", "whale"]
    by = {t["trigger"]: t for t in hl["triggers"]}
    assert set(by) == {HUNT.label(), Trigger("whale", sol=2.2).label()}
    assert by[HUNT.label()] == {"trigger": HUNT.label(), "moments": 56, "behind_p50": 8, "behind_min": 8,
                                "behind_max": 8, "short": 0}  # fmt: skip


def test_the_lab_command_without_the_flag_is_what_it_was(tmp_path, capsys):
    """No flag: +3@1, the live place, no new field on any row and no `hunt_landing` block. The flag at +3@1 gives the
    same numbers, and says the landing is the live one."""
    db = _busy_db(tmp_path)
    plain = _lab(capsys, db, "--trigger", HUNT_JSON)
    assert "hunt_landing" not in plain
    (row,) = plain["strategies"]
    assert (row["landing"], row["live_landing"]) == ("+3@1", True)
    assert "landing_vs_live" not in row and "landing_note" not in row
    same = _lab(capsys, db, "--trigger", HUNT_JSON, "--hunt-landing", "+3@1")
    (again,) = same["strategies"]
    assert (again["landing"], again["live_landing"], again["landing_vs_live"]) == ("+3@1", True, "live")
    strip = ("landing_vs_live", "landing_note")
    assert {k: v for k, v in again.items() if k not in strip} == row
    assert same["hunt_landing"]["vs_live"] == "live" and not same["hunt_landing"]["better_than_live"]


@pytest.mark.parametrize("landing", ["+1@1", "+3@0", "+0@0"])
def test_a_landing_ahead_of_the_live_place_is_labelled_on_every_row(tmp_path, capsys, landing):
    """Fewer trades than 3, or the crossing trade's own block: the result may be scored ahead of where live hunt buys
    landed. It runs (research), and every place a reader looks says it is better than the live place."""
    out = _lab(capsys, _busy_db(tmp_path), "--trigger", HUNT_JSON, "--hunt-landing", landing)
    (row,) = out["strategies"]
    assert (row["landing"], row["live_landing"], row["landing_vs_live"]) == (landing, False, "ahead")
    assert "better than the measured live place" in row["landing_note"] and "no evidence" in row["landing_note"]
    hl = out["hunt_landing"]
    assert hl["better_than_live"] is True and hl["vs_live"] == "ahead"
    assert "better than the measured live place" in hl["note"]
    assert out["triggers"][0]["landing_vs_live"] == "ahead"


def test_the_lab_says_when_a_block_was_too_quiet_for_the_landing_asked(tmp_path, capsys):
    """+20@1 on blocks of nine trades: the buy stands behind the block's last trade, 10 behind the crossing trade,
    never 20. The run says on how many moments fewer trades than asked stood ahead of the buy (`short`): there the
    result is kinder than the landing's name."""
    out = _lab(capsys, _busy_db(tmp_path), "--trigger", HUNT_JSON, "--hunt-landing", "+20@1")
    (t,) = out["hunt_landing"]["triggers"]
    assert (t["moments"], t["behind_p50"], t["behind_max"], t["short"]) == (56, 1 + RUSH, 1 + RUSH, 56)
    assert "short" in out["hunt_landing"]["note"]


def test_the_lab_text_names_the_hunt_landing_asked(tmp_path, capsys):
    from snipetech import cli

    db = _busy_db(tmp_path)
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", "--hunt-landing", "+7@1"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "hunt triggers at +7@1" in text
    assert "Hunt landing +7@1, asked with --hunt-landing" in text and "the live place is +3@1" in text
    assert "launch, hunt, whale and burst triggers where a hunt buy lands" not in text  # no longer true of this run
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", "--hunt-landing", "+1@1"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "Better than the measured live place (+3@1" in text and "no evidence that the rule pays live" in text
    # the live place asked for by name is still where they trade live, and the text does not say otherwise
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", "--hunt-landing", "+3@1"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "Scored where they trade live, not at the landing above: hunt triggers at +3@1" in text
    assert "launch, hunt, whale and burst triggers where a hunt buy lands" in text
    assert "Hunt landing +3@1, asked with --hunt-landing" in text and "The measured live place (+3@1)" in text


def test_a_copy_keeps_its_own_landing_beside_a_hunt_landing(tmp_path, capsys):
    out = _lab(capsys, _busy_db(tmp_path, kols=True), "--trigger", KOL_JSON, "--trigger", HUNT_JSON,
               "--hunt-landing", "+7@1")  # fmt: skip
    got = {r["trigger_spec"]["kind"]: (r["landing"], r["live_landing"]) for r in out["strategies"]}
    assert got == {"kol": ("+6@0", True), "hunt": ("+7@1", False)}


def test_the_grid_takes_a_hunt_landing_too(tmp_path, capsys):
    """Without --trigger the grid's hunt, whale and burst rules move; its launch rules and copies do not."""
    out = _lab(capsys, _busy_db(tmp_path, kols=True), "--position", "+1", "--top", "100", "--hunt-landing", "+7@1")
    where = {(r["trigger_spec"]["kind"], r["landing"], r["live_landing"]) for r in out["strategies"]}
    assert where == {("launch", "start@1", True), ("hunt", "+7@1", False), ("whale", "+7@1", False),
                     ("burst", "+7@1", False), ("kol", "+1@0", False)}  # fmt: skip


def test_a_hunt_landing_nothing_in_the_run_could_use_is_refused_before_any_lab(tmp_path, capsys, monkeypatch):
    """A silent no-op is impossible: a run of copies only has no rule the landing applies to, and says so."""
    from snipetech import cli
    from snipetech.analysis import lab

    monkeypatch.setattr(lab, "collect", lambda *a, **k: pytest.fail("a refused landing must not start a lab"))
    argv = ["--db", str(tmp_path / "x.db"), "lab", "--trigger", KOL_JSON, "--trigger", LAUNCH_JSON,
            "--hunt-landing", "+7@1"]  # fmt: skip
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert "--hunt-landing" in err and "hunt, whale or burst" in err


@pytest.mark.parametrize("cmd", [["lab"], ["paper", "freeze"]])
def test_the_command_line_refuses_a_landing_it_cannot_read(cmd, capsys):
    from snipetech import cli

    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args([*cmd, "--hunt-landing", "7@1"])
    assert exc.value.code == 2 and "+N@D" in capsys.readouterr().err
    assert cli.build_parser().parse_args(cmd).hunt_landing is None
    assert cli.build_parser().parse_args([*cmd, "--hunt-landing", "+11@2"]).hunt_landing == (2, "+11")


@pytest.mark.parametrize("cmd", [["lab"], ["paper", "freeze"]])
def test_position_given_a_whole_landing_points_at_the_hunt_flag(cmd, capsys):
    """Job 6808ed (13 Nov 03:45): `--position +7@1` was refused with "start, middle, end or +N" and nothing else."""
    from snipetech import cli

    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args([*cmd, "--position", "+7@1"])
    assert exc.value.code == 2 and "--hunt-landing +N@D" in capsys.readouterr().err


# -- the agents' jobs (operator/jobs.py): refused before a job starts --------------------------------------------------


@pytest.fixture
def jobs(tmp_path) -> OperatorJobs:
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    j = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main", max_jobs=3)
    j.ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                  command=[sys.executable, "-c", "import sys; print('ran', sys.argv[1:])"])  # fmt: skip
    return j


def test_start_refuses_a_hunt_landing_the_lab_could_not_read_before_any_job_starts(jobs):
    """7 Nov 11:01 and 13 Nov 03:45: a flag the command refuses came back as a job id, and the job died on start."""
    bad = (["lab", "--trigger", HUNT_JSON, "--hunt-landing", "7@1", "--json"],
           ["lab", "--trigger", HUNT_JSON, "--hunt-landing=+7", "--json"],
           ["paper", "freeze", "--trigger", HUNT_JSON, "--hunt-landing", "+7@99"],
           ["paper", "freeze", "--hunt-landing", "+7@1"],  # the grid's rules are frozen where they trade live
           ["paper", "freeze", "--trigger", HUNT_JSON, "--hunt-landing", "+7@1", "--only-live"],
           ["lab", "--trigger", HUNT_JSON, "--hunt-landing"])  # fmt: skip
    for argv in bad:
        with pytest.raises(PermissionError, match=r"--hunt-landing refused, no job started: .*(\+N@D|--trigger|live)"):
            jobs.start(argv)
    # the tester's own two tries: a hunt's landing is not --position's to set, and the refusal names the flag that is
    for argv in (["lab", "--trigger", HUNT_JSON, "--position", "+7@1", "--hours", "168", "--json"],
                 ["lab", "--trigger", HUNT_JSON, "--position=+11@2", "--json"]):  # fmt: skip
        with pytest.raises(PermissionError, match=r"--position refused, no job started: .*--hunt-landing \+7@1|\+11@2"):
            jobs.start(argv)
    # a landing with only rules it does not apply to
    with pytest.raises(PermissionError, match="--hunt-landing refused, no job started: .*hunt, whale or burst"):
        jobs.start(["lab", "--trigger", KOL_JSON, "--hunt-landing", "+7@1", "--json"])
    assert not list((jobs.ops.data / "jobs").glob("*.json"))
    # the two the tester asked for pass the check, in either spelling, and the job starts
    for ok in (
        ["lab", "--trigger", HUNT_JSON, "--hunt-landing=+11@2", "--hours", "168", "--json"],
        ["paper", "freeze", "--trigger", HUNT_JSON, "--hunt-landing", "+7@1"],
        ["lab", "--hours", "24", "--hunt-landing", "+7@1", "--json"],  # the grid has hunts
        ["winners", "--position", "a@b"],
    ):  # another command's flags are not this check's
        assert jobs._check_hunt_landing(ok) is None
    assert jobs.start(["lab", "--trigger", HUNT_JSON, "--hunt-landing", "+7@1", "--hours", "168", "--json"])["id"]


# -- paper freeze: the landing is part of the rule ---------------------------------------------------------------------


def test_freeze_keeps_a_hunt_at_the_landing_asked_as_a_rule_of_its_own():
    """`paper freeze --trigger <hunt> --hunt-landing +7@1`: the lab scores the hunt there and the rule is frozen
    there, under an id of its own (`rule_key` holds the landing), so the same hunt at +3@1 is another record and both
    run forward side by side. The forward run scores each at its own landing and never moves the asked one to the
    live place; the report never calls it the live landing, so it cannot go to a live test by itself."""
    store = Store(":memory:")
    busy_world(store, 80)
    freeze_at = NOW - 3000  # the freeze reads the tape so far; the forward run then scores what came after it
    seven = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], hunt_landing=(1, "+7"), now=freeze_at)
    (s7,) = seven["frozen"]
    assert (s7["latency"], s7["position"], s7["lab"]["landing"], s7.get("landing_asked")) == (1, "+7", "+7@1", True)
    assert s7["landing_words"] == ("+7: 7 trades into the slot after the trigger, behind everything in the trigger's "
                                   "own slot (asked for at the freeze: not where live hunt buys landed)")  # fmt: skip
    plain = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=freeze_at)
    (s3,) = plain["frozen"]  # not "already under test": another landing is another rule
    assert (s3["latency"], s3["position"], s3["lab"]["landing"], s3.get("landing_asked")) == (1, "+3", "+3@1", None)
    assert s3["id"] != s7["id"] and paper._rule_of(s3) != paper._rule_of(s7)
    again = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], hunt_landing=(1, "+7"), now=freeze_at)
    assert again["frozen"] == []  # the same landing again is the record that already runs
    # the landing asked for at +3@1 is the plain rule, not a second record of it
    live = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], hunt_landing=paper.HUNT_LANDING, now=freeze_at)
    assert live["frozen"] == []
    paper.run(store, _cfg(), now=NOW)
    kept = {s["id"]: s for s in store.coverage("paper_strategies")["strategies"]}
    assert (kept[s7["id"]]["latency"], kept[s7["id"]]["position"]) == (1, "+7")  # not re-keyed to the live landing
    t7, t3 = store.paper_trades(s7["id"]), store.paper_trades(s3["id"])
    assert t7 and len(t7) == len(t3)
    assert {(t["data"]["landing"], t["data"]["behind"]) for t in t7} == {("+7@1", 8)}
    assert {(t["data"]["landing"], t["data"]["behind"]) for t in t3} == {("+3@1", 4)}
    rows = {r["id"]: r for r in paper.report(store, _cfg(), NOW)["strategies"]}
    assert rows[s7["id"]]["live_landing"] is False and rows[s3["id"]]["live_landing"] is True


def test_freeze_refuses_a_hunt_landing_it_could_not_keep():
    store = Store(":memory:")
    busy_world(store)
    with pytest.raises(ValueError, match="--trigger"):  # the grid's rules are frozen where they trade live
        paper.freeze(store, _cfg(), hunt_landing=(1, "+7"), now=NOW)
    with pytest.raises(ValueError, match="only-live"):
        paper.freeze(store, _cfg(), triggers=[HUNT], hunt_landing=(1, "+7"), only_live=True, now=NOW)
    with pytest.raises(ValueError, match="hunt, whale or burst"):
        paper.freeze(store, _cfg(), triggers=[Trigger("kol", sol=2.0)], hunt_landing=(1, "+7"), now=NOW)


def test_paper_freeze_from_the_command_line_takes_the_hunt_landing(tmp_path, capsys):
    from snipetech import cli

    db = _busy_db(tmp_path)
    argv = ["--db", str(db), "paper", "freeze", "--trigger", HUNT_JSON, "--hours", "1", "--hunt-landing", "+7@1",
            "--json"]  # fmt: skip
    assert cli.main(argv) == 0
    rep = json.loads(capsys.readouterr().out)
    assert [(s["trigger"], s["latency"], s["position"], s["lab"]["landing"], s["landing_asked"])
            for s in rep["frozen"]] == [(HUNT.__dict__, 1, "+7", "+7@1", True)]  # fmt: skip
    assert cli.main(["--db", str(db), "paper", "freeze", "--hunt-landing", "+7@1"]) == 2  # the grid: refused
    assert "--trigger" in capsys.readouterr().err
