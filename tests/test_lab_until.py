"""A lab on a fixed window of the tape, and its test trades one by one (14 Nov 2026, the agents' idea I161).

`snipetech lab --hours N` read the newest N hours of the hourly copy, so its window ended wherever that copy ended:
two labs of one rule two hours apart (jobs 0d62a3 and c56cb7, 14 Nov) read windows 3 h apart and gave totals of +12.8
and +20.1 SOL, and nothing in either output said from when to when it had read, where the older moments stopped
picking the exit, or which trades the test numbers rested on. No lab could be run twice on the same trades.

`lab --until T` names the window's end: the lab is then the lab of a tape that ended at T (nothing after T is read:
no trade, launch, migration, PumpSwap coverage or backfilled stretch), `--hours` counts back from T, and two runs give
the same bytes. Every lab says its window and every ranked strategy where its cut fell; `--trades` lists the test
trades of each hypothesis, and two short digests say at a glance whether two runs rest on the same trades."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from snipetech import ops as O
from snipetech.amm_tape import AMM_RECENT_KEY, amm_recent_gap, backfill_key
from snipetech.analysis import companions as C
from snipetech.analysis import lab_window as W
from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger, collect, exit_set, rank
from snipetech.config import Config
from snipetech.operator.jobs import OperatorJobs
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from . import test_bid_landing as BIDS
from .synth import Market, addr
from .test_lab_mig import _graduate, _late_migration

T0 = 1_794_000_000  # 6 Nov 2026 21:20 UTC
H = 3600
END = T0 + 3 * H  # where the tape ends
UNTIL = T0 + 2 * H  # where the window ends
SLOT0 = 500_000_000
HUNT = Trigger("hunt", sol=2.0, window=3.0, dev_max=1.0)
HUNT_JSON = '{"kind": "hunt", "sol": 2, "window": 3, "dev_max": 1}'
WHALE = Trigger("whale", sol=2.2)
WHALE_JSON = '{"kind": "whale", "sol": 2.2}'
LAUNCH = Trigger("launch")
KOL = Trigger("kol", sol=0.5)
MIG = Trigger("mig", window=10.0)
TRIGGERS = [HUNT, WHALE, LAUNCH, KOL, MIG]
LEAK = addr("until-leak")  # traded before UNTIL; the tape's record of its create is stamped after it
SKIP_BEFORE = [T0 + 1000, T0 + 1010]  # a stretch the PumpSwap side skipped before UNTIL, and one after it
SKIP_AFTER = [UNTIL + 400, UNTIL + 500]
BACKFILLED = addr("until-mig-28")  # the last coin to migrate before UNTIL (150 s before it): a backfill holds it


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 50
    return cfg


def _slot(ts: int) -> int:
    return SLOT0 + (ts - T0) * 1000 // 60


def _coin(store: Store, i: int) -> None:
    """A launch a 2.5 SOL buy takes past 2 SOL two seconds after its create (the hunt and the whale rule fire on it),
    six followers in and out within half a minute, and a late buyer in at two minutes and out at four: an exit that
    holds meets trades minutes after the moment."""
    ts = T0 + 300 + 60 * i
    m = Market(store, addr(f"until-{i}"), addr(f"until-dev-{i}"), ts, _slot(ts))
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr(f"until-small-{i}"), 0.2, 2)
    m.buy(addr(f"until-whale-{i}"), 2.5, 5)
    for k in range(6):
        m.buy(addr(f"until-fan-{i}-{k}"), 0.3 + 0.05 * ((i + k) % 5), 5 + k)
    for k in range(6):
        m.sell(addr(f"until-fan-{i}-{k}"), 1.0, 40 + 5 * k)
    m.buy(addr(f"until-late-{i}"), 1.0 + 0.2 * (i % 4), 300)
    m.sell(addr(f"until-late-{i}"), 1.0, 600)
    m.commit()


def _world(store: Store) -> None:
    """Three hours of tape, a launch a minute and a migration every four: what a lab that ends at UNTIL may read, and
    an hour after it that it may not. Among the coins: ones that launch in the minutes before UNTIL and trade past it;
    a coin whose curve completes before UNTIL and migrates after it; a coin that traded before UNTIL whose create the
    tape stamps after it (LEAK); a crew of three launches on one uri whose third comes after UNTIL; a PumpSwap record
    that runs to the tape's end and skipped a stretch after UNTIL; a backfilled stretch that runs past UNTIL."""
    for i in range(170):
        _coin(store, i)
    for j in range(40):
        ts = T0 + 330 + 240 * j
        _graduate(store, f"until-mig-{j}", ts, _slot(ts))
    _late_migration(store, "until-late-mig", UNTIL - 100, _slot(UNTIL - 100), done_at=5, mig_at=500)
    m = Market(store, LEAK, addr("until-leak-dev"), UNTIL + 50, _slot(UNTIL + 50))
    m.buy(m.deployer, 0.5, -1500, sig=m.create_sig)  # 600 s before the create's stamp: 550 s before UNTIL
    m.buy(addr("until-leak-whale"), 2.5, -1495)
    for k in range(6):
        m.buy(addr(f"until-leak-fan-{k}"), 0.4, -1495 + k)
    for k in range(6):
        m.sell(addr(f"until-leak-fan-{k}"), 1.0, -1460 + 5 * k)
    m.commit()
    for i in (10, 20, 150):  # a serial crew: the third launch on the uri makes it one, and comes after UNTIL
        store._exec("UPDATE launches SET uri='ipfs://until-crew' WHERE mint=?", (addr(f"until-{i}"),))
    for i in range(170):  # every coin's whale is a labelled KOL: a copy follows its buy
        store.set_label(addr(f"until-whale-{i}"), f"whale-{i}", "kol")
    for j in range(40):  # and so is each migrated coin's first PumpSwap buyer: a copy that buys on PumpSwap
        store.set_label(addr(f"until-mig-{j}-early-0"), f"early-{j}", "kol")
    store.set_coverage("tape", {"spans": [[T0, END]]})
    store.set_coverage(AMM_RECENT_KEY, {"runs": [{"run": "t", "from": T0, "to": END, "secs": 1800, "slot_secs": 0.4,
                                                  "skipped": [SKIP_BEFORE, SKIP_AFTER]}]})  # fmt: skip
    store.set_coverage(backfill_key(BACKFILLED), {"pools": [], "spans": [[UNTIL - 200, END]]})


def _cut_copy(full: Path, dest: Path) -> None:
    """The database as it stood at UNTIL: every row of `full` stamped at or before UNTIL in the order it was written,
    the labels, and the coverage records as the recorder had written them by then (by hand, not by the code under
    test: the tape and the PumpSwap run end at UNTIL, the stretch skipped later is not there, the backfilled stretch
    stops at UNTIL)."""
    st = Store(dest)
    try:
        with st.lock:
            st.conn.execute("ATTACH DATABASE ? AS src", (str(full),))
            for table in ("trades", "launches", "migrations", "completions"):
                cols = ",".join(r[1] for r in st.conn.execute(f"PRAGMA main.table_info({table})"))
                st.conn.execute(f"INSERT INTO main.{table} ({cols}) SELECT {cols} FROM src.{table} WHERE ts<=? "
                                "ORDER BY rowid", (UNTIL,))  # fmt: skip
            st.conn.execute("INSERT INTO main.labels SELECT * FROM src.labels")
            st.conn.execute("INSERT OR REPLACE INTO main.coverage SELECT * FROM src.coverage")
            st.conn.commit()
            st.conn.execute("DETACH DATABASE src")
        st.set_coverage("tape", {"spans": [[T0, UNTIL]]})
        st.set_coverage(AMM_RECENT_KEY, {"runs": [{"run": "t", "from": T0, "to": UNTIL, "secs": 1800,
                                                   "slot_secs": 0.4, "skipped": [SKIP_BEFORE]}]})  # fmt: skip
        st.set_coverage(backfill_key(BACKFILLED), {"pools": [], "spans": [[UNTIL - 200, UNTIL]]})
    finally:
        st.close()


@pytest.fixture(scope="module")
def dbs(tmp_path_factory) -> tuple[Path, Path]:
    """(the whole tape, the tape as it stood at UNTIL), each in a directory of its own."""
    root = tmp_path_factory.mktemp("lab-until")
    full, cut = root / "full" / "snipetech.db", root / "cut" / "snipetech.db"
    store = Store(full)
    _world(store)
    store.close()
    cut.parent.mkdir()
    _cut_copy(full, cut)
    return full, cut


class _SpansOnly:
    """A window that only clips the spans the lab walks (as `recent_tape` clips their start): every other read goes
    to the store, whatever it holds after the end."""

    def __init__(self, store: Store, until: int):
        self._store, self._until = store, until

    def tape_spans(self):
        return [(int(a), min(int(b), self._until)) for a, b in self._store.tape_spans() if a < self._until]

    def __getattr__(self, name: str):
        return getattr(self._store, name)


def _lab(store, workers: int | None = 1) -> dict:
    return collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=TRIGGERS,
                   exits=exit_set()[:6], workers=workers)  # fmt: skip


def _same(a: dict, b: dict) -> None:
    """Two labs gave the same result: every scored moment, every count of what was left out, the same ranking."""
    for key in ("rows", "tape_hours", "slot_secs", "size", "amm_tape", "mates", "unscored", "window"):
        assert a[key] == b[key], key
    ranked = [json.dumps(rank(d, _cfg(), top=20, trades=True), default=str, sort_keys=True) for d in (a, b)]
    assert ranked[0] == ranked[1]


def _fired(data: dict, trig: Trigger) -> set[str]:
    ti = data["triggers"].index(trig)
    return {r["mint"] for r in data["rows"] if r["trigger"] == ti}


# -- --until: one way to write the end, everything else refused -------------------------------------------------------


@pytest.mark.parametrize(
    "text,want",
    [
        ("1794621630", 1794621630),
        ("2026-11-14T06:00Z", 1794636000),
        ("2026-11-14 06:00", 1794636000),
        ("2026-11-14T06:00:30Z", 1794636030),
        ("2026-11-14 06:00:30", 1794636030),
        (" 2026-11-14T06:00Z ", 1794636000),
    ],
)
def test_until_is_unix_seconds_or_a_utc_time(text, want):
    assert W.parse_until(text) == want
    assert W.parse_until(W.utc(want)) == want  # what an output prints can be pasted back


@pytest.mark.parametrize(
    "text",
    ["", "yesterday", "now", "2026-11-14", "06:00", "2026-11-14T06:00+01:00", "2026-11-14T06:00:00.5Z", "1.79e9",
     "-5", "1794621630.5", "2026-13-40T00:00Z", "2026-11-14T25:00Z", "14 Nov 06:00", "2026-11-14T06Z"],
)  # fmt: skip
def test_until_refuses_anything_else(text):
    with pytest.raises(ValueError, match="unix seconds or UTC"):
        W.parse_until(text)


def test_utc_is_written_the_one_way():
    assert W.utc(1794621630) == "2026-11-14T02:00:30Z"


# -- the window ----------------------------------------------------------------------------------------------------------


def test_hours_count_back_from_the_end_named():
    store = Store(":memory:")
    store.set_coverage("tape", {"spans": [[T0, T0 + H], [T0 + H + 300, END]]})  # a five-minute hole after an hour
    assert W.until_tape(store, UNTIL).tape_spans() == [(T0, T0 + H), (T0 + H + 300, UNTIL)]
    assert W.until_tape(store, UNTIL, hours=0.5).tape_spans() == [(UNTIL - H // 2, UNTIL)]
    assert W.until_tape(store, UNTIL, hours=1.5).tape_spans() == [(T0 + H // 2, T0 + H), (T0 + H + 300, UNTIL)]
    # an end inside the hole: the window ends where the tape before it does, and the hours still count from the end
    assert W.until_tape(store, T0 + H + 100, hours=0.5).tape_spans() == [(T0 + H + 100 - H // 2, T0 + H)]
    assert W.until_tape(store, END).tape_spans() == [(T0, T0 + H), (T0 + H + 300, END)]  # the tape's own end


@pytest.mark.parametrize("until", [END + 1, T0 - 1, END + 86400])
def test_an_end_outside_the_tape_is_refused_with_the_tapes_own_range(until):
    store = Store(":memory:")
    store.set_coverage("tape", {"spans": [[T0, T0 + H], [T0 + H + 300, END]]})
    with pytest.raises(W.OutsideTape) as exc:
        W.until_tape(store, until, hours=1)
    said = str(exc.value)
    assert W.utc(T0) in said and W.utc(END) in said and W.utc(until) in said
    assert str(T0) in said and str(END) in said  # and in unix seconds, to paste into --until


def test_a_store_with_no_tape_has_no_window_to_end():
    with pytest.raises(W.OutsideTape, match="no tape"):
        W.until_tape(Store(":memory:"), UNTIL)


def test_an_end_that_leaves_no_tape_to_read_is_refused_with_the_tapes_range():
    """Round 2, F7: `--until` at the tape's first second passed the range check and gave an empty lab with exit 0
    (`window.from_ts` null, 0 moments). So did hours that end inside a hole of the tape."""
    store = Store(":memory:")
    store.set_coverage("tape", {"spans": [[T0, T0 + H], [T0 + H + 300, END]]})
    for until, hours in ((T0, None), (T0, 1), (T0 + H + 200, 0.02)):  # the first second; 72 s that lie in the hole
        with pytest.raises(W.OutsideTape, match="no tape") as exc:
            W.until_tape(store, until, hours)
        said = str(exc.value)
        assert W.utc(T0) in said and W.utc(END) in said and str(T0) in said and str(END) in said
    assert W.until_tape(store, T0 + 1).tape_spans() == [(T0, T0 + 1)]  # one second of tape is a window


def test_the_pumpswap_record_as_it_stood_keeps_what_an_old_run_skipped_right_after_the_end():
    """Round 2, F5: a run written before the recorder measured its slot time (no `slot_secs`) has its skipped
    stretches read 180 s wider on both sides (amm_tape._skipped). One that starts just after the end reaches back
    before it: the clip dropped it, and a lab with an end scored a PumpSwap moment the same lab without one refuses."""
    old = {"runs": [{"run": "old", "from": T0, "to": END, "secs": 1800, "skipped": [[UNTIL + 100, UNTIL + 150]]}]}
    new = {"runs": [{**old["runs"][0], "slot_secs": 0.4}]}  # a run that measured it: read as written
    far = {"runs": [{**old["runs"][0], "skipped": [[UNTIL + 181, UNTIL + 300]]}]}  # out of the wider reading's reach
    for cov in (old, new, far):
        cut = W.clip_amm_recent(cov, UNTIL)
        assert cut["runs"][0]["to"] == UNTIL
        for start in range(UNTIL - 900, UNTIL, 50):  # every stretch that ends by UNTIL reads as on the whole record
            for end in range(start + 10, UNTIL + 1, 40):
                assert amm_recent_gap(cut, start, end) == amm_recent_gap(cov, start, end), (start, end)
    assert amm_recent_gap(old, UNTIL - 300, UNTIL - 50) == "skipped"  # the stretch reaches back to UNTIL - 80
    assert amm_recent_gap(W.clip_amm_recent(old, UNTIL), UNTIL - 300, UNTIL - 50) == "skipped"
    assert amm_recent_gap(W.clip_amm_recent(new, UNTIL), UNTIL - 300, UNTIL - 50) is None
    assert W.clip_amm_recent(far, UNTIL)["runs"][0]["skipped"] == []
    assert W.clip_amm_recent(new, UNTIL)["runs"][0]["skipped"] == []


# -- nothing after the end is read ---------------------------------------------------------------------------------------


def test_a_lab_that_ends_at_until_is_the_lab_of_a_tape_that_ended_there(dbs):
    """The proof that nothing after `until` leaks in: on the whole tape (an hour of trades, launches and migrations
    after UNTIL, a PumpSwap record and a backfilled stretch that run past it, a create stamped after it) the lab with
    the end named gives, row for row and count for count, the lab of a database that holds nothing after UNTIL."""
    full, cut = (Store(p) for p in dbs)
    try:
        then = _lab(cut)
        windowed = _lab(W.until_tape(full, UNTIL))
        whole = _lab(full)
        spans_only = _lab(_SpansOnly(full, UNTIL))
    finally:
        full.close()
        cut.close()
    _same(windowed, then)
    assert windowed["window"]["from_ts"] == T0 and windowed["window"]["until_ts"] == UNTIL
    assert windowed["tape_hours"] == pytest.approx(2.0) and whole["tape_hours"] == pytest.approx(3.0)
    # every kind of rule fired, so every read was exercised: launch rules, a copy on the curve and on PumpSwap, MIG
    for trig in TRIGGERS:
        assert len(_fired(then, trig)) >= 20, trig.label()
    assert any(addr(f"until-mig-{j}") in _fired(then, KOL) for j in range(28))  # a copy that bought on PumpSwap
    # what the cut is for. The whole tape is another lab:
    assert len(whole["rows"]) > len(then["rows"])
    # a serial crew's coins are skipped, and at UNTIL the crew of three had launched two: both still count
    assert {addr("until-10"), addr("until-20")} <= _fired(then, HUNT)
    assert not {addr("until-10"), addr("until-20")} & _fired(whole, HUNT)
    # and clipping the spans alone is not enough: with the create stamped after UNTIL the coin's path starts at that
    # create, and its trades before UNTIL, a whale buy among them, are no moment at all,
    assert LEAK in _fired(then, WHALE) and LEAK not in _fired(spans_only, WHALE)
    # and the PumpSwap record written after UNTIL scores MIG buys whose exits end after the window does
    late = lambda d: d["amm_tape"]["triggers"][MIG.label()]["skipped"].get("end", 0)
    assert late(then) > 0 and late(spans_only) == 0
    assert len(_fired(spans_only, MIG)) > len(_fired(then, MIG))
    # so does the backfilled stretch that ran on past UNTIL: the copy of the last PumpSwap buy before it
    assert BACKFILLED in _fired(spans_only, KOL) and BACKFILLED not in _fired(then, KOL)


def test_the_workers_of_a_lab_with_an_end_read_no_further_than_it(dbs):
    """A worker opens the database file itself, not the lab's window on it: it is told the end and cuts its own reads."""
    full, cut = (Store(p) for p in dbs)
    try:
        then = _lab(cut)
        parallel = _lab(W.until_tape(full, UNTIL), workers=2)
    finally:
        full.close()
        cut.close()
    assert parallel["workers"] == 2
    _same({**parallel, "workers": 1}, then)


def test_the_cut_reads_of_a_window(dbs):
    """Each read a lab makes of one coin, through the window: no trade, create, migration or block after its end, and
    the PumpSwap record and the backfilled stretches as they stood then."""
    full = Store(dbs[0])
    try:
        view = W.until_tape(full, UNTIL)
        late = addr("until-114")  # created 60 s before UNTIL: its followers sell and its late buyer comes after it
        assert max(t.ts for t in full.trades_for_mint(late)) > UNTIL
        assert max(t.ts for t in view.trades_for_mint(late)) <= UNTIL
        assert view.trades_for_mint(late, T0, END) == full.trades_for_mint(late, T0, UNTIL)
        assert view.trades_for_mint(late, T0, UNTIL - 30) == full.trades_for_mint(late, T0, UNTIL - 30)
        assert full.launch(LEAK) is not None and view.launch(LEAK) is None
        assert view.launch(late) == full.launch(late)
        gone, kept = addr("until-late-mig"), addr("until-mig-3")
        assert full.migration(gone).ts > UNTIL and view.migration(gone) is None
        assert view.migration(kept) == full.migration(kept)
        last = full.trades_for_mint(late)[-1]
        assert full.block_trades(late, last.slot, last.slot) and not view.block_trades(late, last.slot, last.slot)
        (run,) = view.coverage(AMM_RECENT_KEY)["runs"]
        assert (run["from"], run["to"], run["skipped"]) == (T0, UNTIL, [SKIP_BEFORE])
        assert view.coverage("tape") == full.coverage("tape")  # every other record as the store holds it
        assert W.clip_backfill({BACKFILLED: [[UNTIL - 200, END]], "later": [[UNTIL + 5, END]]}, UNTIL) == {
            BACKFILLED: [[UNTIL - 200, UNTIL]]
        }
        # a worker's own connection, told the end, is the same window; told none, it is the store
        assert W.cut_at(full, None) is full and W.cut_at(view, UNTIL) is view
        assert W.cut_at(full, UNTIL).launch(LEAK) is None
        assert view.path == full.path and view.file_id == full.file_id  # what a parallel lab asks of its store
    finally:
        full.close()


def test_a_wallets_companions_are_read_up_to_the_end():
    """The companions a copy is scored behind come from the followed wallet's own buys: with an end named, from its
    buys up to it. A wallet that began buying beside it after the end was nobody's companion then."""
    st = Store(":memory:")
    dev = addr("until-co-dev")
    for k in range(16):
        m = Market(st, addr(f"until-co-{k}"), dev, T0 + 60 * k, SLOT0 + 500 * k)
        m.buy(dev, 1.0, 0, sig=m.create_sig)
        m.buy(addr("until-co-W"), 1.5, 5)
        if k >= 8:  # the second half of its buys: a wallet of its own right behind it
            m.buy(addr("until-co-mate"), 1.5, 5)
        m.commit()
    w, mate = addr("until-co-W"), addr("until-co-mate")
    assert C.companions(st, w, T0 - 10) == frozenset({mate})
    assert C.companions(st, w, T0 - 10, until_ts=T0 + 60 * 7 + 30) == frozenset()
    assert C.companions_of(st, [w], T0 - 10) == {w: frozenset({mate})}
    assert C.companions_of(st, [w], T0 - 10, until_ts=T0 + 60 * 7 + 30) == {}
    st.close()


def test_the_companions_are_read_once_and_up_to_the_end(monkeypatch):
    """Round 2, F2: with an end the lab read every followed wallet's companions over the whole tape, threw the
    answer away and read them again up to the end. One read; without an end it is asked as it always was."""
    from snipetech.analysis import lab

    store = Store(":memory:")
    for i in range(30):
        _coin(store, i)
        store.set_label(addr(f"until-whale-{i}"), f"whale-{i}", "kol")
    store.set_coverage("tape", {"spans": [[T0, T0 + H]]})
    calls = []

    def spy(*args, **kw):
        calls.append((len(args), kw))
        return {}

    monkeypatch.setattr(lab, "companions_of", spy)
    run = lambda tape: collect(tape, _cfg(), 0.2, 0, "+6", triggers=[KOL], exits=exit_set()[:2], workers=1)
    run(W.until_tape(store, T0 + H - 600))
    assert calls == [(3, {"until_ts": T0 + H - 600})]
    calls.clear()
    run(store)
    assert calls == [(3, {})]  # no end: the call of before, argument for argument
    store.close()


def test_a_wallet_of_ours_that_first_bought_after_the_end_was_an_outsider_then():
    """Round 2, F3: at a bid landing our own wallets are nobody's place (`own_wallets`: every wallet a fill of ours
    was measured for). The read had no end: a wallet whose first fill came after the window's end was taken out of
    the buyers ahead of a copy, in a window where nobody could have known it was ours."""
    st = BIDS._store()
    for _ in range(3):  # our wallet bids 0.035 SOL behind the followed wallet and its companion on every coin
        BIDS._coin(st, mate=0.002, block=((BIDS.OURS, 0.2, 0.035, 20),))
    until = BIDS.NOW - 500  # after every coin's last trade
    kw = {"triggers": [BIDS.parse_trigger(BIDS.RULE)], "mates": {BIDS.LEAD: {BIDS.MATE}}, "exits": [BIDS.HOLD],
          "workers": 1, "buy_bid_sol": 0.01}  # fmt: skip
    places = lambda tape: {r["place"] for r in collect(tape, Config(), 2.0, 0, "bid", **kw)["rows"]}
    assert places(st) == places(W.until_tape(st, until)) == {2}  # no fill of ours: the wallet is an outsider ahead
    st.put_fill({"sig": "until-own-late", "ts": until + 100, "wallet": BIDS.OURS, "side": "buy", "sol": 0.2})
    assert places(st) == {1}  # the whole tape knows the wallet is ours
    assert places(W.until_tape(st, until)) == {2}  # at the end it had no fill yet
    st.put_fill({"sig": "until-own-early", "ts": until - 100, "wallet": BIDS.OURS, "side": "buy", "sol": 0.2})
    assert places(W.until_tape(st, until)) == {1}
    assert BIDS.BL.own_wallets(st, Config()) == {BIDS.OURS}
    assert BIDS.BL.own_wallets(st, Config(), until_ts=until - 200) == set()
    st.close()


# -- the lab command -----------------------------------------------------------------------------------------------------


def _run(capsys, db: Path, *argv: str) -> dict:
    from snipetech import cli

    assert cli.main(["--db", str(db), "lab", *argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_the_lab_command_with_until_gives_the_lab_of_a_tape_that_ended_there(dbs, capsys):
    full, cut = dbs
    asked = _run(capsys, full, "--trigger", HUNT_JSON, "--trigger", WHALE_JSON, "--until", str(UNTIL), "--trades")
    then = _run(capsys, cut, "--trigger", HUNT_JSON, "--trigger", WHALE_JSON, "--trades")
    assert asked == then
    assert asked["window"] == {"from_ts": T0, "until_ts": UNTIL, "from_utc": W.utc(T0), "until_utc": W.utc(UNTIL),
                               "tape": "the live database"}  # fmt: skip
    assert asked["tape_hours"] == pytest.approx(2.0)
    # the end written as a time is the same lab
    as_time = _run(capsys, full, "--trigger", HUNT_JSON, "--trigger", WHALE_JSON, "--until", W.utc(UNTIL), "--trades")
    assert as_time == asked


def test_hours_with_until_count_back_from_it(dbs, capsys):
    out = _run(capsys, dbs[0], "--trigger", HUNT_JSON, "--until", str(UNTIL), "--hours", "1")
    assert (out["window"]["from_ts"], out["window"]["until_ts"]) == (UNTIL - H, UNTIL)
    assert out["tape_hours"] == pytest.approx(1.0) and out["hours"] == 1
    newest = _run(capsys, dbs[0], "--trigger", HUNT_JSON, "--hours", "1")  # as before: the tape's newest hour
    assert (newest["window"]["from_ts"], newest["window"]["until_ts"]) == (END - H, END)
    assert newest["strategies"][0]["test_digest"] != out["strategies"][0]["test_digest"]


def test_two_runs_on_one_window_are_the_same_bytes(dbs, capsys):
    """Same end, same arguments, same database file: the same output to the byte, the digests included."""
    from snipetech import cli

    argv = ["--db", str(dbs[0]), "lab", "--trigger", HUNT_JSON, "--trigger", WHALE_JSON, "--until", str(UNTIL),
            "--hours", "1.5", "--buy", "1", "--trades", "--json"]  # fmt: skip
    assert cli.main(argv) == 0
    first = capsys.readouterr().out
    assert cli.main(argv) == 0
    assert capsys.readouterr().out == first
    assert len(json.loads(first)["strategies"]) == 2


def test_without_until_the_lab_reads_the_tape_to_its_end_as_before(dbs, capsys):
    """No end named: the window is the tape's (or its newest hours), and naming the tape's own end changes nothing."""
    plain = _run(capsys, dbs[0], "--trigger", HUNT_JSON)
    assert (plain["window"]["from_ts"], plain["window"]["until_ts"]) == (T0, END)
    assert plain["tape_hours"] == pytest.approx(3.0)
    assert _run(capsys, dbs[0], "--trigger", HUNT_JSON, "--until", str(END)) == plain


OLD_TOP = {"tape_hours", "moments", "latency_slots", "position", "copy_landing", "buy_sol", "per_tx_sol",
           "per_tx_source", "per_tx_measured_at", "hours", "workers", "strategies", "triggers", "amm_tape", "size"}  # fmt: skip
OLD_STRATEGY = {"trigger", "trigger_spec", "exit", "exit_params", "landing", "moments", "exit_moments", "per_hour",
                "train_mean", "test", "test_sol_per_day", "size", "live_landing"}  # fmt: skip
NEW_STRATEGY = {"train", "test_from_ts", "test_from_utc", "test_digest", "window_digest"}


def test_the_output_gains_the_window_and_the_cut_and_nothing_else(dbs, capsys):
    """What a lab without the new flags prints: every field it printed before, the window, and per strategy where its
    cut fell and the two digests. The trades come only when asked for."""
    out = _run(capsys, dbs[1], "--trigger", HUNT_JSON)
    assert set(out) == OLD_TOP | {"window"}
    (s,) = out["strategies"]
    assert set(s) == OLD_STRATEGY | NEW_STRATEGY
    # 114 moments (a launch a minute, the last one a minute before the tape's end is too late to judge): the older 68
    # pick the exit, the newer 46 judge it, and the cut is the 69th moment's second
    assert (s["moments"], s["train"], s["test"]["n"]) == (114, {"n": 68}, 46)
    assert s["test_from_ts"] == T0 + 300 + 60 * 68 + 2 and s["test_from_utc"] == W.utc(s["test_from_ts"])
    assert s["window_digest"] == W.window_digest(T0, UNTIL, 114)
    asked = _run(capsys, dbs[1], "--trigger", HUNT_JSON, "--trades")
    (t,) = asked["strategies"]
    assert set(t) == OLD_STRATEGY | NEW_STRATEGY | {"test_trades", "left_out"}
    assert {k: v for k, v in t.items() if k not in ("test_trades", "left_out")} == s
    assert {k: v for k, v in asked.items() if k != "strategies"} == {k: v for k, v in out.items() if k != "strategies"}


def test_the_trades_of_a_hypothesis_one_by_one(dbs, capsys):
    """`--trades`: each ranked hypothesis lists its test moments on the chosen exit, oldest first, each
    [second, coin, SOL made, whether the score rests on a price outside the model]; the list is what `test` sums, and
    its digest is the digest of the list as printed."""
    out = _run(capsys, dbs[1], "--trigger", HUNT_JSON, "--trigger", WHALE_JSON, "--buy", "1", "--trades")
    assert len(out["strategies"]) == 2
    for s in out["strategies"]:
        trades = s["test_trades"]
        assert len(trades) == s["test"]["n"] and s["left_out"] == []
        assert all(len(t) == 4 and isinstance(t[0], int) and isinstance(t[3], bool) for t in trades)
        assert [t[0] for t in trades] == sorted(t[0] for t in trades) and trades[0][0] == s["test_from_ts"]
        assert all(len(t[1]) >= 32 for t in trades)  # the coin's address in full
        if s["trigger_spec"]["kind"] == "hunt":  # a launch a minute, each one's moment two seconds after its create
            assert [t[1] for t in trades] == [addr(f"until-{(t[0] - T0 - 302) // 60}") for t in trades]
        assert sum(t[2] for t in trades) == pytest.approx(s["test"]["total"], abs=1e-6 * len(trades))
        assert all(t[2] == round(t[2], 6) for t in trades)
        assert sum(t[3] for t in trades) == s["size"]["outside_model"]
        assert s["test_digest"] == W.trades_digest(trades) and len(s["test_digest"]) == 12
        assert s["window_digest"] == W.window_digest(T0, UNTIL, s["moments"])
    a, b = out["strategies"]
    assert a["test_digest"] != b["test_digest"]


def test_trades_are_refused_without_a_trigger_and_for_more_than_eight(dbs, capsys, monkeypatch):
    from snipetech import cli
    from snipetech.analysis import lab

    monkeypatch.setattr(lab, "collect", lambda *a, **k: pytest.fail("a refused lab must not read the tape"))
    base = ["--db", str(dbs[1]), "lab"]
    assert cli.main([*base, "--trades"]) == 2
    assert "--trigger" in capsys.readouterr().err
    assert cli.main([*base, "--recipe", "6z91", "--trades"]) == 2  # a recipe's triggers were not typed with --trigger
    assert "--trigger" in capsys.readouterr().err
    nine = [x for sol in range(2, 11) for x in ("--trigger", json.dumps({"kind": "whale", "sol": sol}))]
    assert cli.main([*base, *nine, "--trades"]) == 2
    err = capsys.readouterr().err
    assert "at most 8" in err and "9" in err


def test_an_end_the_tape_does_not_hold_is_refused_before_any_lab(dbs, capsys, monkeypatch):
    from snipetech import cli
    from snipetech.analysis import lab

    monkeypatch.setattr(lab, "collect", lambda *a, **k: pytest.fail("a refused lab must not read the tape"))
    base = ["--db", str(dbs[0]), "lab", "--trigger", HUNT_JSON]
    for until in (END + 60, T0 - 60):
        assert cli.main([*base, "--until", str(until)]) == 2
        err = capsys.readouterr().err
        assert err.startswith("lab: ") and W.utc(T0) in err and W.utc(END) in err
    for text in ("yesterday", "2026-11-14"):
        with pytest.raises(SystemExit) as exc:
            cli.main([*base, "--until", text])
        assert exc.value.code == 2 and "unix seconds or UTC" in capsys.readouterr().err


def test_an_end_at_the_tapes_first_second_is_refused_by_the_command(dbs, capsys, monkeypatch):
    from snipetech import cli
    from snipetech.analysis import lab

    monkeypatch.setattr(lab, "collect", lambda *a, **k: pytest.fail("a refused lab must not read the tape"))
    assert cli.main(["--db", str(dbs[0]), "lab", "--trigger", HUNT_JSON, "--until", str(T0)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("lab: ") and "no tape" in err and W.utc(T0) in err and W.utc(END) in err


@pytest.fixture
def jobs(tmp_path) -> OperatorJobs:
    """The agents' job starter over a command that only prints its arguments."""
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    j = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main", max_jobs=3)
    j.ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                  command=[sys.executable, "-c", "import sys; print('ran', sys.argv[1:])"])  # fmt: skip
    return j


def test_an_agent_is_refused_a_lab_that_would_die_on_its_window_flags_before_any_job_starts(jobs):
    """Round 2, F1: `lab --until garbage` and `lab --trades` without its triggers came back from the agents' job
    starter as a job id, and the job died on start (7 Nov 11:01 and 13 Nov 03:45 were the same with other flags).
    The starter reads `--until` with the lab's own parser and applies the two `--trades` rules in the lab's words.
    Whether an end is on the tape only the lab can know, once it has opened the tape."""
    for argv in (["lab", "--trigger", HUNT_JSON, "--until", "garbage", "--json"],
                 ["lab", "--trigger", HUNT_JSON, "--until=2026-11-14", "--json"],
                 ["lab", "--trigger", HUNT_JSON, "--until", "1794621630", "--until", "06:00", "--json"],
                 ["lab", "--trigger", HUNT_JSON, "--json", "--until"]):  # fmt: skip
        with pytest.raises(PermissionError, match="--until refused, no job started: .*unix seconds or UTC"):
            jobs.start(argv)
    for argv in (["lab", "--trades", "--json"], ["lab", "--hours", "24", "--trades"],
                 ["lab", "--recipe", "6z91", "--trades", "--json"]):  # fmt: skip
        with pytest.raises(PermissionError, match="--trades refused, no job started: .*typed with --trigger"):
            jobs.start(argv)
    nine = [x for sol in range(2, 11) for x in ("--trigger", json.dumps({"kind": "whale", "sol": sol}))]
    four = [x for sol in range(2, 6) for x in ("--trigger=" + json.dumps({"kind": "whale", "sol": sol}),)]
    for argv, n in (
        (["lab", *nine, "--trades", "--json"], 9),
        (["lab", "--recipe", "6z91", *four, "--trades", "--json"], 9),
    ):  # the recipe's five and four typed
        with pytest.raises(
            PermissionError, match=f"--trades refused, no job started: .*at most 8 rules a run, not {n}"
        ):
            jobs.start(argv)
    assert not list((jobs.ops.data / "jobs").glob("*.json"))
    # the words are the command's own
    from snipetech import cli

    for argv, n in ((["--trades"], None), ([*nine, "--trades"], 9)):
        with pytest.raises(PermissionError) as exc:
            jobs._check_lab_window(["lab", *argv])
        args = cli.build_parser().parse_args(["lab", *argv])
        with pytest.raises(ValueError) as said:
            cli._lab_window_flags(args, args.trigger, Store(":memory:"))
        assert str(exc.value) == f"--trades refused, no job started: {said.value}"
    # what the lab takes passes, in either spelling; another command's flags are not this check's
    for ok in (["lab", "--trigger", HUNT_JSON, "--until", "1794621630", "--hours", "168", "--trades", "--json"],
               ["lab", "--trigger", HUNT_JSON, "--until=2026-11-14T06:00Z", "--json"],
               ["lab", "--recipe", "6z91", *four[:3], "--trades"],  # eight rules
               ["lab", "--hours", "24", "--until", "2026-11-14 06:00", "--json"],
               ["winners", "--until", "garbage", "--trades"]):  # fmt: skip
        assert jobs._check_lab_window(ok) is None
    started = jobs.start(
        ["lab", "--trigger", HUNT_JSON, "--until", "1794621630", "--hours", "168", "--trades", "--json"]
    )
    assert started["id"]


def test_the_lab_says_which_file_it_read(tmp_path, capsys):
    """The lab reads the hourly copy when there is a fresh one, and says so with the time the copy was taken: two
    runs name the same copy or they did not read the same file. A lab with an end reads the copy's tape up to it."""
    from snipetech.infra.snapshot_db import last_copy, snapshot_db

    db = tmp_path / "data" / "snipetech.db"
    db.parent.mkdir()
    store = Store(db)
    for i in range(60):
        _coin(store, i)
    store.set_coverage("tape", {"spans": [[T0, T0 + H]]})
    store.close()
    assert _run(capsys, db, "--trigger", HUNT_JSON)["window"]["tape"] == "the live database"
    snapshot_db(str(db), disk_free=lambda _d: 1e15)
    taken = W.utc(last_copy(db)["started"])
    store = Store(db)  # the recorder goes on after the copy: the live tape is ten minutes longer than the copy's
    store.set_coverage("tape", {"spans": [[T0, T0 + H + 600]]})
    store.close()
    out = _run(capsys, db, "--trigger", HUNT_JSON)
    assert out["window"]["tape"] == f"the hourly copy taken at {taken}"
    assert out["window"]["until_ts"] == T0 + H  # the copy's end, not the live tape's
    ended = _run(capsys, db, "--trigger", HUNT_JSON, "--until", str(T0 + H - 600))
    assert ended["window"]["tape"] == out["window"]["tape"] and ended["window"]["until_ts"] == T0 + H - 600
    # an end the live tape holds and the copy does not yet: refused once the copy is open, with the copy's range
    from snipetech import cli

    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--until", str(T0 + H + 300)]) == 2
    err = capsys.readouterr().err
    assert "the hourly copy" in err and W.utc(T0 + H) in err


def test_the_strategy_search_and_a_freeze_say_their_window_too(tmp_path):
    """Every lab output says its window: the search's record (it reads the newest hours of the tape) and the report
    of a `paper freeze`."""
    from snipetech.operator import research as RS
    from snipetech.service import Service

    data = tmp_path / "data"
    data.mkdir()
    store = Store(data / "snipetech.db")
    try:
        for i in range(60):
            _coin(store, i)
        store.set_coverage("tape", {"spans": [[T0, T0 + H]]})
        cfg = _cfg()
        cfg.storage.db_path = str(data / "snipetech.db")
        want = {"from_ts": T0, "until_ts": T0 + H, "from_utc": W.utc(T0), "until_utc": W.utc(T0 + H),
                "tape": "the live database"}  # fmt: skip
        rep = paper.freeze(store, cfg, top=2, position="+6", triggers=[HUNT], now=T0 + H)
        assert rep["window"] == want
        out = RS.search(Service(cfg, store=store), cfg, data, T0 + H)
        assert out["window"] == want and store.coverage(RS.SEARCH_KEY)["window"] == want
    finally:
        store.close()


def test_the_lab_text_says_its_window(dbs, capsys):
    from snipetech import cli

    assert cli.main(["--db", str(dbs[0]), "lab", "--trigger", HUNT_JSON, "--until", str(UNTIL), "--hours", "1"]) == 0
    text = capsys.readouterr().out
    assert f"Window: {W.utc(UNTIL - H)} to {W.utc(UNTIL)} ({UNTIL - H} to {UNTIL}), read from the live database" in text


# -- rank: the cut, the trades, the digests ------------------------------------------------------------------------------

T20 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=20, sell_on_dev_sell=False)
HOLD300 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300, sell_on_dev_sell=False)


def _ranked(rows: list[dict], window: dict | None = None, **kw) -> list[dict]:
    data = {"rows": rows, "exits": [T20, HOLD300], "triggers": [HUNT], "tape_hours": 10.0,
            "landing_of": {0: [1, "+3"]}}  # fmt: skip
    if window:
        data["window"] = window
    return rank(data, _cfg(), top=10, **kw)


def test_a_test_moment_the_exit_was_not_scored_on_is_listed_with_its_reason():
    """30 moments; the hold was left out on six (it held the coin into a migration the PumpSwap tape lacks), two of
    them among the newer 12, and one newer score rests on a price outside the model."""
    gone = {1, 4, 7, 10, 20, 25}
    rows = [{"mint": f"m{k}", "trigger": 0, "ts": 1_000 + 60 * k, "pnl": [0.01, None if k in gone else 0.02 + 0.001 * k]}
            for k in range(30)]  # fmt: skip
    for k in gone:
        rows[k]["gaps"] = {1: "before"}
    rows[22]["outside"] = {1: "past_curve"}
    rows[23]["outside"] = {0: "fallback"}  # another exit's score: not this one's
    (r,) = _ranked(rows, trades=True)
    assert r["exit"] == HOLD300.label() and (r["train"], r["test"]["n"]) == ({"n": 14}, 10)
    assert r["test_from_ts"] == 1_000 + 60 * 18 and r["test_from_utc"] == "1970-01-01T00:34:40Z"
    assert r["left_out"] == [[1_000 + 60 * 20, "m20", "before"], [1_000 + 60 * 25, "m25", "before"]]
    assert r["test_trades"] == [[1_000 + 60 * k, f"m{k}", round(0.02 + 0.001 * k, 6), k == 22]
                                for k in range(18, 30) if k not in gone]  # fmt: skip
    assert "window_digest" not in r  # rows put together by hand say nothing of a window
    (plain,) = _ranked(rows)  # not asked for: the cut and the digest, no list
    assert "test_trades" not in plain and "left_out" not in plain and plain["test_digest"] == r["test_digest"]
    (w,) = _ranked(rows, window={"from_ts": 5, "until_ts": 9_000})
    assert w["window_digest"] == W.window_digest(5, 9_000, 30)


def test_the_digests_move_with_what_they_stand_for_and_with_nothing_else():
    trades = [[1_000, "mintA", 0.123456, False], [1_060, "mintB", -0.2, True]]
    d = W.trades_digest(trades)
    assert d == W.trades_digest([list(t) for t in trades]) and len(d) == 12 and int(d, 16) >= 0
    assert d == W.trades_digest([[1_000, "mintA", 0.1234561, True], [1_060, "mintB", -0.2000004, False]])  # 6 places
    assert d != W.trades_digest([[1_000, "mintA", 0.123457, False], [1_060, "mintB", -0.2, True]])  # the SOL made
    assert d != W.trades_digest([[1_001, "mintA", 0.123456, False], [1_060, "mintB", -0.2, True]])  # the second
    assert d != W.trades_digest([[1_000, "mintC", 0.123456, False], [1_060, "mintB", -0.2, True]])  # the coin
    assert d != W.trades_digest(trades[:1]) and d != W.trades_digest(trades[::-1])
    assert W.trades_digest([[1, "m", 0.0, False]]) == W.trades_digest([[1, "m", -0.0, False]])
    w = W.window_digest(T0, UNTIL, 1078)
    assert len(w) == 12 and w == W.window_digest(T0, UNTIL, 1078)
    assert len({w, W.window_digest(T0 + 1, UNTIL, 1078), W.window_digest(T0, UNTIL + 1, 1078),
                W.window_digest(T0, UNTIL, 1079)}) == 4  # fmt: skip


def test_a_copy_of_the_world_is_left_alone(dbs, tmp_path):
    """The window writes nothing: a lab with an end leaves the database file as it was."""
    src = tmp_path / "copy.db"
    shutil.copy(dbs[1], src)
    before = src.read_bytes()
    store = Store(src, readonly=True)
    try:
        collect(W.until_tape(store, UNTIL - H), _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS,
                triggers=[HUNT], exits=exit_set()[:2], workers=1)  # fmt: skip
    finally:
        store.close()
    assert src.read_bytes() == before
