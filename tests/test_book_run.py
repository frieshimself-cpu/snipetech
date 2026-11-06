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
    def __init__(self, store, snipers=None, held=None):
        self.store, self._snipers, self._held, self.calls = store, snipers or [], held or [], []

    async def oktech_status(self):
        return {"snipers": list(self._snipers)}

    async def open_positions(self, address):
        return list(self._held)

    async def oktech_task(self, action, task_id):
        self.calls.append((action, task_id))
        return {"ok": True}

    async def oktech_copylist(self, specs, wallet, create=False, buy_sol=0.05, max_buys=5, exit_name="lab", priority_fee=None, tip=None,
                              send_count=None, tag="", compute_units=None, salt=""):  # fmt: skip
        self.calls.append(("create", tuple(specs), wallet, buy_sol, max_buys, priority_fee, tip, send_count, tag))
        self.salts = getattr(self, "salts", []) + [salt]
        return {
            "snipers": [
                {
                    "id": s.split(":")[0],
                    "oktech_id": f"new-{i}",
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
        st.put_verdict(
            {"wallet": w, "computed_at": NOW - 3600, "status": "qualified", "score": score, "next_review": NOW + 86400}
        )
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


def test_dry_mode_calls_nothing(tmp_path):
    st, cfg = _env(tmp_path)  # enabled=false
    svc = FakeSvc(st, snipers=[{"id": "s9", "name": "snipetech copy book OLDWAL arm:control", "status": "running"}])
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
        assert out["dry"] is True and out["actions"]["create"] == 2 and out["actions"]["stop"] == 1
        assert svc.calls == [] and sent == []
        notes = (tmp_path / "data" / "operator" / "notes.md").read_text()
        assert "[book dry] would create" in notes and "would stop" in notes
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
        assert create[1] == (f"{L2}:book:0.3:lab",) and create[2] == "SNIPER" and create[3] == 0.2 and create[4] == 10
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
