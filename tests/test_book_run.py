"""The book runner: dry mode writes what it would do and calls nothing; live mode executes stop → create → start with a
Telegram line each; below the equity floor it is dry whatever the config says; the CLI refuses --live when disabled."""

import asyncio

from snipetech.book.run import run_book
from snipetech.book.state import day_accounting, record_run
from snipetech.cli import build_parser
from snipetech.config import Config
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.store import Store

NOW = 1_793_923_200 + 10 * 3600  # 10:00 UTC on 6 Nov
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
L1, L2 = "L1" + "x" * 30, "L2" + "y" * 30


class FakeSvc:
    def __init__(self, store, snipers=None, held=None, fail=(), held_later=None):
        self.store, self._snipers, self._held, self.calls = store, snipers or [], held or [], []
        self._fail, self._held_later, self._reads = set(fail), held_later, 0  # held_later: what later reads see

    async def oktech_status(self):
        return {"snipers": list(self._snipers)}

    async def open_positions(self, address):
        self._reads += 1
        return list(self._held_later if self._held_later is not None and self._reads > 1 else self._held)

    async def oktech_task(self, action, task_id):
        self.calls.append((action, task_id))
        if (action, task_id) in self._fail:
            raise RuntimeError(f"{action} refused")
        return {"ok": True}

    async def oktech_copylist(self, specs, wallet, create=False, buy_sol=0.05, max_buys=5, exit_name="lab", priority_fee=None, tip=None,
                              send_count=None, tag="", compute_units=None, salt=""):  # fmt: skip
        self.calls.append(("create", tuple(specs), wallet, buy_sol, max_buys, priority_fee, tip, send_count, tag))
        self.salts = getattr(self, "salts", []) + [salt]
        return {
            "snipers": [
                {
                    "id": s.split(":")[0],
                    "oktech_id": f"new-{len(self.calls)}-{i}",
                    "status": "idle",
                    "name": f"snipetech copy book {s[:6]} {tag}",
                }
                for i, s in enumerate(specs)
            ]
        }


def _env(tmp_path, **book):
    data = tmp_path / "data"
    OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    cfg.oktech.wallet = "SNIPER"
    for k, v in book.items():
        setattr(cfg.book, k, v)
    for w, score in ((L1, 0.03), (L2, 0.02)):
        st.upsert_candidate(w, "tape", {}, NOW - 3600)
        st.put_verdict({"wallet": w, "computed_at": NOW - 3600, "status": "qualified", "score": score, "next_review": NOW + 86400,
                        "n_tape": 30, "copy_plan": {"min_leader_sol": 0.3, "exit_name": "lab"}})  # fmt: skip
    return st, cfg


def test_day_accounting_and_record_run(tmp_path):
    st, cfg = _env(tmp_path)
    try:
        st.put_fill({"sig": "B1", "ts": NOW - 3600, "wallet": W, "side": "buy", "sol": 0.2})
        st.put_fill({"sig": "S1", "ts": NOW - 1800, "wallet": W, "side": "sell", "sol": 0.15, "pnl_sol": -0.052})
        st.put_fill({"sig": "OLD", "ts": NOW - 20 * 3600, "wallet": W, "side": "buy", "sol": 0.5})  # yesterday
        acc = day_accounting(st, W, NOW, equity_now=3.5)
        assert (
            acc["day"] == "2026-11-06"
            and acc["realised_sol"] == -0.052
            and acc["risk_used_sol"] == 0.2
            and acc["equity_at_day_start"] == 3.5
        )
        acc2 = day_accounting(st, W, NOW + 60, equity_now=3.1)
        assert acc2["equity_at_day_start"] == 3.5  # fixed for the day
        record_run(st, {"kill_reason": None, "actions": [{"kind": "create"}, {"kind": "keep"}]}, dry=True, now=NOW)
        last = st.coverage("book:last_run")
        assert last["dry"] is True and last["actions"] == {"create": 1, "keep": 1} and last["ts"] == NOW
    finally:
        st.close()


def test_a_book_that_is_off_stops_its_own_snipers(tmp_path):
    """/book off: the routine run stops the book's snipers that hold nothing (safety, not new money) and creates
    nothing; an explicit dry preview never acts."""
    st, cfg = _env(tmp_path)  # enabled=false, no BOOK_ON flag
    svc = FakeSvc(st, snipers=[{"id": "s9", "name": "snipetech copy book OLDWAL arm:control", "status": "running"}])
    try:
        preview = asyncio.run(run_book(svc, cfg, NOW, dry=True, balances=lambda w: (10.0, 0.0, [])))
        assert svc.calls == [] and preview["dry"] is True
        out = asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert out["kill_reason"] == "book off" and svc.calls == [("stop", "s9")]
    finally:
        st.close()


def test_while_a_coin_is_held_the_book_stops_none_of_its_snipers(tmp_path):
    """A measured fill can name the wrong leader (a co-buying leader, a failed leader attempt, an older closed position
    in the same coin), so the book never narrows "who holds the coin" from fills: while the wallet holds any coin, it
    keeps every book sniper and tells the owner, since each can still buy up to its cap."""
    st, cfg = _env(tmp_path, enabled=True)
    st.put_fill({"sig": "b1", "ts": NOW - 60, "slot": 1, "wallet": W, "wallet_copied": L1, "mint": "M", "side": "buy",
                 "sol": 0.2, "tokens": 1000.0})  # fmt: skip
    snipers = [{"id": "k1", "name": f"snipetech copy book {L1[:6]} arm:control", "status": "running"},
               {"id": "k2", "name": f"snipetech copy book {L2[:6]} arm:control", "status": "running"}]  # fmt: skip
    svc = FakeSvc(st, snipers=snipers, held=[{"mint": "M", "amount": 3.0, "value_sol": 0.1}])
    sent = []
    try:
        out = asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                                   balances=lambda w: (1.0, 0.0, [{"mint": "M"}])))  # fmt: skip
        assert out["kill_reason"] == "equity below floor" and svc.calls == []
        assert len(sent) == 1 and "2 book sniper(s) kept running" in sent[0] and "up to 3" in sent[0]
    finally:
        st.close()


def test_dry_mode_calls_nothing(tmp_path):
    st, cfg = _env(tmp_path)  # enabled=false
    svc = FakeSvc(st, snipers=[])
    sent = []
    try:
        out = asyncio.run(
            run_book(
                svc,
                cfg,
                NOW,
                notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                balances=lambda w: (10.0, 0.0, []),
            )
        )
        assert out["dry"] is True and out["actions"]["create"] == 2 and "stop" not in out["actions"]
        assert svc.calls == [] and sent == []
        notes = (tmp_path / "data" / "operator" / "notes.md").read_text()
        assert "[book dry] would create" in notes
        out2 = asyncio.run(
            run_book(svc, cfg, NOW + 300, balances=lambda w: (10.0, 0.0, []))
        )  # the same plan again: no new note lines
        assert (tmp_path / "data" / "operator" / "notes.md").read_text() == notes and out2["dry"] is True
    finally:
        st.close()


def test_live_mode_executes_in_order_with_telegram_lines(tmp_path):
    st, cfg = _env(tmp_path, enabled=True)
    svc = FakeSvc(st, snipers=[{"id": "s9", "name": "snipetech copy book OLDWAL arm:control", "status": "running"},
                               {"id": "s1", "name": f"snipetech copy book {L1[:6]} arm:control", "status": "idle"}])  # fmt: skip
    sent = []
    try:
        out = asyncio.run(
            run_book(
                svc,
                cfg,
                NOW,
                notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                balances=lambda w: (10.0, 0.0, []),
            )
        )
        assert out["dry"] is False
        kinds = [c[0] for c in svc.calls]
        assert kinds == ["stop", "create", "start", "start"]  # stop s9, create L2, start the new one and the idle s1
        create = next(c for c in svc.calls if c[0] == "create")
        assert create[1] == (f"{L2}:book:0.3:lab",) and create[2] == "SNIPER" and create[3] == 0.2 and create[4] == 3
        assert create[5] == 0.001 and create[6] == 0.002 and create[7] == 1 and create[8] == "arm:control"
        assert len(sent) == 4 and all("[book]" in t for t in sent)
        assert st.coverage("book:last_run")["dry"] is False
    finally:
        st.close()


def test_forced_dry_below_floor_and_when_holding(tmp_path):
    st, cfg = _env(tmp_path, enabled=True)
    svc = FakeSvc(
        st,
        snipers=[{"id": "s9", "name": "snipetech copy book OLDWAL arm:control", "status": "running"}],
        held=[{"mint": "M", "amount": 3.0}],
    )
    sent = []
    try:
        out = asyncio.run(
            run_book(
                svc,
                cfg,
                NOW,
                notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                balances=lambda w: (1.0, 0.0, [{"mint": "M"}]),
            )
        )
        assert out["dry"] is True and out["kill_reason"] == "equity below floor" and svc.calls == []
        assert any("floor" in t for t in sent)  # a kill is always announced
    finally:
        st.close()


def test_cli_book_parses_and_refuses_live_when_disabled():
    p = build_parser()
    a = p.parse_args(["book", "run"])
    assert a.cmd == "book" and a.action == "run" and a.live is False
    a = p.parse_args(["book", "run", "--live"])
    assert a.live is True


def test_owner_stop_flag_kills_the_book_even_when_live(tmp_path):
    """/stop writes operator/STOP; a live book must not create or start anything while it exists, and stops its own
    non-holding snipers (the kill path)."""
    st, cfg = _env(tmp_path, enabled=True)
    OperatorPaths(tmp_path / "data").stop_flag.write_text(str(NOW))
    svc = FakeSvc(st, snipers=[{"id": "b1", "name": f"snipetech copy book {L1[:6]} arm:control", "status": "running"}])
    sent = []
    try:
        out = asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                                   balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert out["kill_reason"] == "owner /stop" and ("stop", "b1") in svc.calls
        assert not any(c[0] in ("create", "start") for c in svc.calls)
    finally:
        st.close()


def test_book_equity_counts_the_coins_held(tmp_path):
    """The book's own buys move SOL into coins; equity = SOL + WSOL + the tape value of the coins held, or the book
    would trip its own floor after a few fills."""
    st, cfg = _env(tmp_path)
    svc = FakeSvc(st, held=[{"mint": "M", "amount": 1.0, "value_sol": 2.5, "priced": True}])
    try:
        out = asyncio.run(
            run_book(
                svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: None), balances=lambda w: (1.0, 0.0, [])
            )
        )
        assert out["kill_reason"] is None  # 1.0 + 2.5 = 3.5 SOL, above the 3.0 floor
        unpriced = FakeSvc(st, held=[{"mint": "M", "amount": 1.0, "value_sol": None, "priced": False}])
        out2 = asyncio.run(
            run_book(
                unpriced, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: None), balances=lambda w: (1.0, 0.0, [])
            )
        )
        assert out2["kill_reason"] == "equity unknown"  # an unpriced coin: no number to compare with the floor
    finally:
        st.close()


def test_a_banned_wallet_is_never_booked(tmp_path):
    """A wallet banned after it qualified keeps its qualified verdict; the book must not copy it."""
    st, cfg = _env(tmp_path, enabled=True)
    st.set_candidate_status(L1, "banned")
    svc = FakeSvc(st)
    try:
        asyncio.run(
            run_book(
                svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: None), balances=lambda w: (10.0, 0.0, [])
            )
        )
        created = [c for c in svc.calls if c[0] == "create"]
        assert created and all(not any(spec.startswith(L1) for spec in c[1]) for c in created)
    finally:
        st.close()


def test_the_owners_book_switch_makes_the_book_live(tmp_path):
    from snipetech.book.switch import book_switch

    st, cfg = _env(tmp_path)  # [book] enabled is false in the config
    book_switch(OperatorPaths(tmp_path / "data"), True, "test", NOW)
    svc = FakeSvc(st)
    try:
        out = asyncio.run(
            run_book(
                svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: None), balances=lambda w: (10.0, 0.0, [])
            )
        )
        assert out["dry"] is False and any(c[0] == "create" for c in svc.calls)
    finally:
        st.close()


def test_cli_book_switch_parses():
    p = build_parser()
    assert p.parse_args(["book", "on"]).action == "on" and p.parse_args(["book", "off"]).action == "off"
    assert p.parse_args(["book", "status"]).action == "status"


def test_cli_book_off_says_where_the_switch_is_and_whether_it_held(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "data" / "snipetech.db"
    assert cli.main(["--db", str(db), "book", "on"]) == 0
    assert cli.main(["--db", str(db), "book", "off"]) == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "data" / "operator" / "BOOK_ON") in out and "stops its snipers that hold nothing" in out


def test_the_owners_bar_decides_which_wallets_the_book_copies(tmp_path):
    """`book bar 0.025` (or /book bar 0.025 in Telegram) copies only wallets scoring at least that; `default` goes back
    to the config's min_score. Only the owner sets it: the strategist's overlay has no access to this file."""
    from snipetech.book.switch import book_bar, effective_min_score

    st, cfg = _env(tmp_path)  # L1 scores 0.03, L2 0.02; the config's bar is 0.005
    paths = OperatorPaths(tmp_path / "data")
    svc = FakeSvc(st)
    try:
        assert effective_min_score(cfg, paths) == 0.005
        book_bar(paths, 0.025, "test", NOW)
        assert effective_min_score(cfg, paths) == 0.025
        out = asyncio.run(run_book(svc, cfg, NOW, dry=True, balances=lambda w: (10.0, 0.0, [])))
        assert out["ranked"] == 1 and out["actions"].get("create") == 1
        book_bar(paths, None, "test", NOW)
        assert effective_min_score(cfg, paths) == 0.005
    finally:
        st.close()


def test_cli_book_bar_parses():
    a = build_parser().parse_args(["book", "bar", "0.001"])
    assert a.action == "bar" and a.value == "0.001"
    assert build_parser().parse_args(["book", "bar"]).value is None


def test_book_status_says_why_it_is_not_buying(tmp_path):
    """The owner asks "is it running?": the last run's line says what the book saw, e.g. no wallet above the bar and
    the best one it has."""
    from snipetech.book.state import last_run_line
    from snipetech.book.switch import book_bar

    st, cfg = _env(tmp_path, enabled=True)  # L1 0.03, L2 0.02
    paths = OperatorPaths(tmp_path / "data")
    svc = FakeSvc(st)
    try:
        assert "has not run yet" in last_run_line(st)
        book_bar(paths, 0.05, "test", NOW)
        asyncio.run(run_book(svc, cfg, NOW, dry=True, balances=lambda w: (10.0, 0.0, [])))
        line = last_run_line(st)
        assert "no qualified wallet scores at least the bar" in line and L1[:6] in line and "0.03" in line
        book_bar(paths, None, "test", NOW)
        asyncio.run(run_book(svc, cfg, NOW + 300, dry=True, balances=lambda w: (10.0, 0.0, [])))
        assert "2 wallet(s) above the bar" in last_run_line(st)
        asyncio.run(run_book(svc, cfg, NOW + 600, dry=True, balances=lambda w: (1.0, 0.0, [])))
        assert "halted: equity below floor" in last_run_line(st)
    finally:
        st.close()


def test_a_book_that_is_off_does_not_page_the_owner_about_its_floor(tmp_path):
    """Off and below the floor with nothing of its own running: a note, not a Telegram message every day."""
    st, cfg = _env(tmp_path)  # off
    sent = []
    try:
        out = asyncio.run(run_book(FakeSvc(st), cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                                   balances=lambda w: (1.0, 0.0, [])))  # fmt: skip
        assert out["kill_reason"] == "equity below floor" and sent == []
        assert "[book] kill: equity below floor" in (tmp_path / "data" / "operator" / "notes.md").read_text()
    finally:
        st.close()


def test_cli_book_run_live_is_refused_while_the_book_is_off(tmp_path):
    import pytest

    from snipetech import cli

    with pytest.raises(SystemExit, match="refused"):
        cli.main(["--db", str(tmp_path / "data" / "snipetech.db"), "book", "run", "--live"])


def test_an_off_book_note_does_not_swallow_the_live_books_page(tmp_path):
    """Off in the morning below the floor (a note), then /book on: the halt must still reach the owner once."""
    st, cfg = _env(tmp_path)
    sent = []
    note = Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))
    try:
        asyncio.run(run_book(FakeSvc(st), cfg, NOW, notifier=note, balances=lambda w: (1.0, 0.0, [])))
        assert sent == []
        from snipetech.book.switch import book_switch

        book_switch(OperatorPaths(tmp_path / "data"), True, "test", NOW)
        asyncio.run(run_book(FakeSvc(st), cfg, NOW + 300, notifier=note, balances=lambda w: (1.0, 0.0, [])))
        assert len(sent) == 1 and "equity below floor" in sent[0]
    finally:
        st.close()


def test_book_status_says_how_old_the_last_run_is(tmp_path):
    from snipetech.book.state import last_run_line, record_run

    st, cfg = _env(tmp_path)
    try:
        record_run(st, {"actions": [], "kill_reason": "oktech unknown"}, True, NOW - 2 * 3600)
        assert "2 h ago" in last_run_line(st, now=NOW)
    finally:
        st.close()


def test_cli_book_bar_says_where_the_bar_lives(tmp_path, capsys):
    from snipetech import cli

    db = tmp_path / "data" / "snipetech.db"
    assert cli.main(["--db", str(db), "book", "bar", "0.002"]) == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "data" / "operator" / "BOOK_MIN_SCORE") in out and "0.002" in out


L3, L4 = "L3" + "z" * 30, "L4" + "w" * 30


def _record(st, sid, kind, leader, name, created_at=NOW - 3600, removed_at=None):
    """Our local record of a sniper we made: what it copies lives only here (OKTech's tasks carry no config)."""
    payload = {"mode": "ctbot", "ctbotWallets": [{"address": leader}]} if kind != "dev" else {"mode": "normal"}
    st.add_oktech_sniper({"id": sid, "external_id": f"e-{sid}", "kind": kind, "source": leader, "name": name,
                          "config_hash": "h", "payload": payload, "created_at": created_at})  # fmt: skip
    if removed_at is not None:  # remove_oktech_sniper stamps the wall clock; the tests need an exact time
        st._exec("UPDATE oktech_snipers SET removed_at=? WHERE id=?", (removed_at, sid))


def test_other_copies_come_from_active_non_book_copy_snipers(tmp_path):
    """OKTech's task documents carry no config, so what a task copies comes from our own record of it. Only active
    snipers that are not the book's count; a task we did not make is unknown and ignored."""
    from snipetech.book.run import _other_copies

    st, cfg = _env(tmp_path)
    _record(st, "f1", "copy", L1, f"snipetech copy G2oZ {L1[:6]} arm:paid")
    _record(st, "f2", "copy", L2, f"snipetech copy Gfree {L2[:6]}")
    _record(st, "b1", "copy", L3, f"snipetech copy book {L3[:6]} arm:control")
    _record(st, "v1", "vcopy", L4, f"snipetech verdict copy {L4[:6]}")
    _record(st, "d1", "dev", "DEV", "snipetech dev DEVxxx")
    tasks = [{"id": "f1", "name": f"snipetech copy G2oZ {L1[:6]} arm:paid", "status": "running"},
             {"id": "f2", "name": f"snipetech copy Gfree {L2[:6]}", "status": "idle"},  # stopped: buys nothing
             {"id": "b1", "name": f"snipetech copy book {L3[:6]} arm:control", "status": "running"},  # the book's own
             {"id": "v1", "name": f"snipetech verdict copy {L4[:6]}", "status": "starting"},
             {"id": "d1", "name": "snipetech dev DEVxxx", "status": "running"},
             {"id": "x9", "name": "made in the OKTech app", "status": "running"}]  # fmt: skip
    try:
        assert _other_copies(st, tasks) == {L1, L4}
        # the live book leaves L1 to the fee-test sniper that already copies it and books L2 only
        svc = FakeSvc(st, snipers=tasks[:1])
        cfg.book.enabled = True
        asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: None),
                             balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        created = [c[1] for c in svc.calls if c[0] == "create"]
        assert created == [(f"{L2}:book:0.3:lab",)]
    finally:
        st.close()


def _coin(st, name, ts, slot, leader_slot, my_slot, leader=None, sell_slot=None):
    """One coin on the tape: the leader's buy (if any) and our wallet's buy (and sell) at the given slot offsets."""
    from tests.synth import Market, addr

    m = Market(st, addr(name), addr(f"dev-{name}"), ts, slot)
    if leader:
        m.buy(leader, 3.0, leader_slot)
    m.buy(W, 0.2, my_slot)
    if sell_slot is not None:
        m.sell(W, 1.0, sell_slot)
    m.commit()


def test_buys_by_sniper_counts_our_tape_buys_behind_its_leader_since_creation(tmp_path):
    """Each book sniper's buys come from the tape, not from fills (measure lags by minutes): our wallet's buys since
    the sniper was made (a minute of clock grace) in coins its leader bought before us, plus every buy of ours that no
    copied leader explains (its leader's buy failed or is not on the tape): counting high only replaces a sniper early,
    counting low relies on the buy that uses up OKTech's cap. A buy made just before a replacement existed, while its
    predecessor on the same leader was alive, is the predecessor's."""
    from snipetech.book.run import _buys_by_sniper

    st, _cfg = _env(tmp_path)
    t0, s0 = NOW - 3600, 400_000_000
    n1, n2, n3, n4 = (f"snipetech copy book {leader[:6]} arm:control" for leader in (L1, L2, L3, L4))
    _record(st, "k1", "copy", L1, n1, created_at=t0)
    _record(st, "p1", "copy", L2, n2, created_at=t0 - 7200, removed_at=t0 + 1225)
    _record(st, "k2", "copy", L2, n2, created_at=t0 + 1230)  # p1's replacement
    _record(st, "k4", "copy", L3, n3, created_at=t0)
    _coin(st, "c1", t0 + 600, s0, 0, 3, leader=L1, sell_slot=10)  # k1: behind its leader (the sell is no buy)
    _coin(st, "c2", t0 + 900, s0 + 10_000, 0, 25, leader=L1)  # the leader bought 25 slots earlier: still k1's
    _coin(st, "c4", t0 - 300, s0 - 10_000, 0, 2, leader=L1)  # before k1 was made
    _coin(st, "c5", t0 - 30, s0 - 5_000, 0, 2, leader=L1)  # 30 s before, no sniper of ours on L1 then: clock grace
    _coin(st, "c3", t0 + 1200, s0 + 20_000, 0, 2, leader=L2)  # p1's buy, 30 s before k2 was made
    _coin(st, "c6", t0 + 1500, s0 + 30_000, 0, 1, leader=L2)  # k2's own buy
    _coin(st, "c7", t0 + 1600, s0 + 40_000, 0, 2)  # nobody we copy bought it: counted for every book sniper alive
    # k3: no record of ours, so never counted and never retired; k4: spent; x1: not the book's
    tasks = [{"id": "k1", "name": n1, "status": "running"}, {"id": "k2", "name": n2, "status": "idle"},
             {"id": "k3", "name": n4, "status": "running"}, {"id": "k4", "name": n3, "status": "finished"},
             {"id": "x1", "name": f"snipetech copy G2oZ {L1[:6]} arm:paid", "status": "running"}]  # fmt: skip
    try:
        assert _buys_by_sniper(st, W, tasks) == {"k1": 4, "k2": 2}
    finally:
        st.close()


K1 = f"snipetech copy book {L1[:6]} arm:control"


def _made_its_buy(tmp_path, **svc_kw):
    """L1's book sniper k1 made its relied-on buy (sold since); L2 has no sniper yet."""
    st, cfg = _env(tmp_path, enabled=True)
    _record(st, "k1", "copy", L1, K1, created_at=NOW - 3600)
    _coin(st, "c1", NOW - 3000, 400_000_000, 0, 3, leader=L1, sell_slot=10)
    _fresh_tape(st)
    return st, cfg, FakeSvc(st, snipers=[{"id": "k1", "name": K1, "status": "running"}], **svc_kw)


def _fresh_tape(st):
    """Someone else's trade a few seconds ago: the tape is current."""
    from tests.synth import Market, addr

    m = Market(st, addr("heartbeat"), addr("dev-heartbeat"), int(NOW) - 5, 401_000_000)
    m.buy(addr("someone"), 0.1, 0)
    m.commit()


def test_retire_is_stop_then_delete_then_a_create_with_a_fresh_salt(tmp_path):
    """The sniper that made its buy is stopped and deleted (a stopped task reads idle, and restarting it would rely
    on a part-used cap), then replaced in the same run under a salt of this run's second (the 5-minute one would repeat
    the externalId of a sniper replaced within 5 minutes). A failed delete means no replacement this run."""
    st, cfg, svc = _made_its_buy(tmp_path / "a")
    sent = []
    try:
        out = asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                                   balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert out["dry"] is False and out["actions"]["retire"] == 1
        assert [c[0] for c in svc.calls] == ["stop", "delete", "create", "create", "start", "start"]
        assert svc.calls[:2] == [("stop", "k1"), ("delete", "k1")]
        assert [c[1] for c in svc.calls if c[0] == "create"] == [(f"{L1}:book:0.3:lab",), (f"{L2}:book:0.3:lab",)]
        assert svc.salts == [f"g{int(NOW)}"] * 2
        assert any("[book] retire" in t and "k1" in t and "holds nothing" in t for t in sent)
    finally:
        st.close()
    st, cfg, svc = _made_its_buy(tmp_path / "b", fail={("delete", "k1")})
    sent = []
    try:
        asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                             balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert ("delete", "k1") in svc.calls
        assert [c[1] for c in svc.calls if c[0] == "create"] == [(f"{L2}:book:0.3:lab",)]  # nothing replaces k1 yet
        assert any("k1" in t and "could not be deleted" in t and "no replacement" in t for t in sent)
    finally:
        st.close()


def test_a_retire_waits_when_a_coin_is_bought_before_it_runs(tmp_path):
    """The holdings are read again just before the retire: a coin bought since the plan keeps the sniper running."""
    st, cfg, svc = _made_its_buy(tmp_path, held_later=[{"mint": "NEW", "amount": 1.0, "value_sol": 0.2}])
    quiet = Notifier("T", 7, post=lambda u, d: None)
    try:
        asyncio.run(run_book(svc, cfg, NOW, notifier=quiet, balances=lambda w: (10.0, 0.0, [])))
        assert not any(c[1] == "k1" for c in svc.calls if c[0] in ("stop", "delete"))
        assert [c[1] for c in svc.calls if c[0] == "create"] == [(f"{L2}:book:0.3:lab",)]
        cfg.book.enabled = False  # off, with k1 already stopped: no kill, a dry plan that only writes what it would do
        dry = FakeSvc(st, snipers=[{"id": "k1", "name": K1, "status": "idle"}])
        asyncio.run(run_book(dry, cfg, NOW + 60, balances=lambda w: (10.0, 0.0, [])))
        assert dry.calls == []
        assert "[book dry] would retire" in (tmp_path / "data" / "operator" / "notes.md").read_text()
    finally:
        st.close()


def test_create_sends_the_wallets_own_floor(tmp_path, monkeypatch):
    """The sniper copies only the leader's buys of at least its verdict's min_leader_sol, never below [book]
    min_copy_sol; a copy plan without a floor keeps the book's 0.3. The Telegram line says which buys are copied."""
    st, cfg = _env(tmp_path, enabled=True)
    plans = {L1: {"min_leader_sol": 0.6}, L2: {"exit_name": "lab"}}
    stored = st.latest_verdicts

    def with_plans(status=None):
        return [dict(v, copy_plan=plans.get(v["wallet"])) for v in stored(status)]

    monkeypatch.setattr(st, "latest_verdicts", with_plans)
    svc, sent = FakeSvc(st), []
    try:
        asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                             balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert [c[1] for c in svc.calls if c[0] == "create"] == [(f"{L1}:book:0.6:lab",), (f"{L2}:book:0.3:lab",)]
        assert any("[book] create" in t and "0.6 SOL or more" in t for t in sent)
        plans[L1] = {"min_leader_sol": 0.1}  # below the book's own minimum: the minimum holds
        svc2, quiet = FakeSvc(st), Notifier("T", 7, post=lambda u, d: None)
        asyncio.run(run_book(svc2, cfg, NOW + 1, notifier=quiet, balances=lambda w: (10.0, 0.0, [])))
        assert next(c[1] for c in svc2.calls if c[0] == "create") == (f"{L1}:book:0.3:lab",)
    finally:
        st.close()


def test_a_verdict_from_the_old_lab_waits_for_the_fixed_lab(tmp_path):
    """A qualified verdict written before the lab simulated the live sniper (no copy plan) is not copied: that lab
    rated 2YDn at +0.0146 a trade and it lost 5 of 5 live. The run says how many wait for a re-qualify."""
    st, cfg = _env(tmp_path)
    st.upsert_candidate("L3" + "z" * 30, "tape", {}, NOW - 3600)
    st.put_verdict({"wallet": "L3" + "z" * 30, "computed_at": NOW - 3600, "status": "qualified", "score": 0.05,
                    "next_review": NOW + 86400, "n_tape": 30})  # fmt: skip
    svc = FakeSvc(st, snipers=[])
    try:
        out = asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: None),
                                   balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert out["ranked"] == 2  # L1 and L2; the old-lab wallet scores best and is still left out
        assert st.coverage("book:last_run")["stale"] == 1
        from snipetech.book.state import last_run_line

        assert "1 qualified by the old lab" in last_run_line(st, NOW)
    finally:
        st.close()


def test_the_book_watches_for_unsold_coins_unless_the_minute_pass_just_did(tmp_path, monkeypatch):
    """The unmanaged-coin watch runs at the end of every book run (dry, live, or OKTech not answering) unless the
    one-minute pass watched in the last 3 minutes; a failing watch never breaks the book."""
    import snipetech.book.run as R

    st, cfg = _env(tmp_path)
    calls = []
    monkeypatch.setattr(
        R,
        "watch_pass",
        lambda cfg, store, notifier, wallet, holdings, tasks, now, notes=None: calls.append((wallet, tasks)) or [],
    )
    monkeypatch.setattr(R, "watched_within", lambda paths, now, secs=180: False)
    try:
        asyncio.run(run_book(FakeSvc(st, snipers=[]), cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: None),
                             balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert calls == [(W, [])]

        class Down(FakeSvc):
            async def oktech_status(self):
                raise RuntimeError("OKTech down")

        asyncio.run(run_book(Down(st), cfg, NOW + 60, balances=lambda w: (10.0, 0.0, [])))
        assert calls[-1] == (W, None)  # OKTech's tasks unknown: the watch still judges the tape
        monkeypatch.setattr(R, "watched_within", lambda paths, now, secs=180: True)
        asyncio.run(run_book(FakeSvc(st, snipers=[]), cfg, NOW + 120, balances=lambda w: (10.0, 0.0, [])))
        assert len(calls) == 2
        monkeypatch.setattr(R, "watched_within", lambda paths, now, secs=180: False)
        monkeypatch.setattr(R, "watch_pass", lambda *a, **k: 1 / 0)
        out = asyncio.run(run_book(FakeSvc(st, snipers=[]), cfg, NOW + 180, balances=lambda w: (10.0, 0.0, [])))
        assert out["dry"] is True and "watch failed" in (tmp_path / "data" / "operator" / "notes.md").read_text()
    finally:
        st.close()


def test_the_day_stop_counts_the_failed_twin_sells(tmp_path):
    """A sell's failed duplicate sends cost real SOL (about one per sell, 6 Nov): the day's realised result counts them."""
    st, _ = _env(tmp_path)
    try:
        st.put_fill(
            {"sig": "b", "ts": NOW - 60, "slot": 1, "wallet": W, "mint": "M", "side": "buy", "sol": 0.2, "tokens": 1.0}
        )
        st.put_fill({"sig": "s", "ts": NOW - 30, "slot": 2, "wallet": W, "mint": "M", "side": "sell", "sol": 0.19, "tokens": 1.0,
                     "pnl_sol": -0.013, "twin_fee_sol": 0.002005})  # fmt: skip
        assert day_accounting(st, W, NOW)["realised_sol"] == round(-0.013 - 0.002005, 6)
    finally:
        st.close()


def test_tape_holding_sees_a_fresh_unsold_buy(tmp_path):
    """The wallet read lags our newest buy by seconds; the tape has it first. A buy of ours in the last 10 minutes
    that the tape has not seen sold, or any buy of ours in the last minute, counts as holding."""
    from snipetech.book.run import _tape_holding

    st, _ = _env(tmp_path / "a")
    try:
        assert _tape_holding(st, W, NOW) is False
        _coin(st, "old", NOW - 1500, 400_000_000, 0, 1)  # unsold 25 min ago: the wallet read sees it by now
        assert _tape_holding(st, W, NOW) is False
        _coin(st, "sold", NOW - 300, 400_100_000, 0, 1, sell_slot=20)
        assert _tape_holding(st, W, NOW) is False
        _coin(st, "fresh", NOW - 30, 400_200_000, 0, 1)  # 30 s ago, not sold yet
        assert _tape_holding(st, W, NOW) is True
    finally:
        st.close()
    st, _ = _env(tmp_path / "b")
    try:
        _coin(st, "quick", NOW - 40, 400_300_000, 0, 1, sell_slot=10)  # bought and sold within the last minute
        assert _tape_holding(st, W, NOW) is True
    finally:
        st.close()


def test_a_retire_is_undone_when_a_coin_appears_while_it_stops(tmp_path):
    """The holdings are read once more after the stop: a coin bought while the stop was on its way (OKTech can take a
    minute when it rate-limits) gets its sniper started again; nothing is deleted and nothing replaces it this run."""

    class LateCoin(FakeSvc):
        async def open_positions(self, address):
            self._reads += 1
            return [{"mint": "NEW", "amount": 1.0, "value_sol": 0.2}] if self._reads >= 3 else []

    st, cfg, _ = _made_its_buy(tmp_path)
    svc = LateCoin(st, snipers=[{"id": "k1", "name": K1, "status": "running"}])
    sent = []
    try:
        asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                             balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert ("stop", "k1") in svc.calls and ("start", "k1") in svc.calls and ("delete", "k1") not in svc.calls
        assert [c[1] for c in svc.calls if c[0] == "create"] == [(f"{L2}:book:0.3:lab",)]
        assert any("k1" in t and "started again" in t for t in sent)
    finally:
        st.close()


def test_the_wallet_is_read_at_confirmed():
    """The holdings read is never a whole finality window behind our own tape: confirmed, not the node's finalized."""
    from snipetech.service import Service

    calls = []

    class Rpc:
        async def call(self, method, params):
            calls.append((method, params))
            return {"value": []}

    class Fake:
        rpc = Rpc()

    asyncio.run(Service.holdings(Fake(), W))
    reads = [p for m, p in calls if m == "getTokenAccountsByOwner"]
    assert reads and all(p[-1].get("commitment") == "confirmed" for p in reads)


def test_no_new_sniper_while_the_tape_is_behind(tmp_path):
    """Each sniper's buys are counted on the tape: while it is more than two minutes behind, the book creates and
    starts nothing (stops still happen) and says why once a day."""
    st, cfg = _env(tmp_path, enabled=True)
    _coin(st, "c", NOW - 600, 400_000_000, 0, 1, sell_slot=5)  # the newest trade is 10 minutes old
    svc, sent = FakeSvc(st), []
    try:
        asyncio.run(run_book(svc, cfg, NOW, notifier=Notifier("T", 7, post=lambda u, d: sent.append(d["text"])),
                             balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert not [c for c in svc.calls if c[0] in ("create", "start")]
        notes = (tmp_path / "data" / "operator" / "notes.md").read_text()
        assert "tape is" in notes and "behind" in notes
    finally:
        st.close()


def test_two_book_runs_never_overlap(tmp_path):
    """The 5-minute timer and a run started by hand share one lock: the second skips instead of making a second
    sniper for the same leader."""
    from snipetech.book.run import book_lock

    st, cfg = _env(tmp_path, enabled=True)
    svc = FakeSvc(st)
    try:
        with book_lock(tmp_path / "data"):
            out = asyncio.run(run_book(svc, cfg, NOW, balances=lambda w: (10.0, 0.0, [])))
        assert out.get("skipped") and svc.calls == []
    finally:
        st.close()


def test_a_retire_that_cannot_delete_pages_once_a_day(tmp_path):
    st, cfg, svc = _made_its_buy(tmp_path, fail={("delete", "k1")})
    sent = []
    note = Notifier("T", 7, post=lambda u, d: sent.append(d["text"]))
    try:
        for i in range(3):
            asyncio.run(run_book(FakeSvc(st, snipers=[{"id": "k1", "name": K1, "status": "running"}],
                                         fail={("delete", "k1")}), cfg, NOW + i * 300, notifier=note,
                                 balances=lambda w: (10.0, 0.0, [])))  # fmt: skip
        assert sum("could not be deleted" in t for t in sent) == 1
    finally:
        st.close()


def test_a_live_pause_is_remembered_across_runs(tmp_path):
    """The runner keeps each live pause (coverage book:paused) so the wallet stays out after its losing trips leave the
    measure window."""
    st, cfg = _env(tmp_path, enabled=True)
    st.put_wallet_live({"wallet": L1, "n": 5, "mean_sol": -0.058, "lower_sol": -0.075, "last_fill": NOW - 60,
                        "status": "live", "updated_at": NOW})  # fmt: skip
    _fresh_tape(st)
    quiet = Notifier("T", 7, post=lambda u, d: None)
    try:
        asyncio.run(run_book(FakeSvc(st), cfg, NOW, notifier=quiet, balances=lambda w: (10.0, 0.0, [])))
        assert L1 in st.coverage("book:paused")
        st._exec("DELETE FROM wallet_live WHERE wallet=?", (L1,))  # the losing trips left the window
        svc = FakeSvc(st)
        asyncio.run(run_book(svc, cfg, NOW + 600, notifier=quiet, balances=lambda w: (10.0, 0.0, [])))
        assert not any(c[0] == "create" and c[1][0].startswith(L1) for c in svc.calls)
    finally:
        st.close()
