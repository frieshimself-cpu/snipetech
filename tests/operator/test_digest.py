"""Daily digest: one Telegram message a day after the configured hour, with the numbers that matter."""

import asyncio

from snipetech.config import Config
from snipetech.operator.budget import Ledger
from snipetech.operator.digest import digest_due, digest_text, mark_sent
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import Snapshot
from snipetech.operator.tools import NoteSink
from snipetech.store import Store

DAY = 1_793_923_200  # 2026-11-06 00:00 UTC
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


def test_not_due_before_hour_and_once_after(tmp_path):
    paths = OperatorPaths(tmp_path).ensure()
    assert not digest_due(paths, DAY + 6 * 3600 + 59 * 60, hour_utc=7)
    assert digest_due(paths, DAY + 7 * 3600, hour_utc=7)
    assert digest_due(paths, DAY + 23 * 3600, hour_utc=7)
    mark_sent(paths, DAY + 7 * 3600 + 120)
    assert not digest_due(paths, DAY + 8 * 3600, hour_utc=7)
    assert digest_due(paths, DAY + 86400 + 7 * 3600, hour_utc=7)  # the next day is due again


def test_digest_text_mentions_equity_change_spend_and_crit_resources(tmp_path):
    paths = OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")
    try:
        now = DAY + 7 * 3600
        st.set_coverage("digest:last", {"ts": now - 86400, "equity": 1.0})
        led = Ledger(paths.spend, {"in": 3.0, "out": 15.0})
        led.record("operator", {"input_tokens": 100_000, "output_tokens": 10_000}, now - 600)  # 0.45 $
        led.record(
            "operator", {"input_tokens": 100_000, "output_tokens": 10_000}, now - 3 * 86400
        )  # 0.45 $, within 7 d
        led.record("operator", {"input_tokens": 100_000, "output_tokens": 10_000}, now - 10 * 86400)  # outside 7 d
        notes = NoteSink(paths, now=lambda: now - 300)
        notes.write("scan: nothing new", "info")
        notes.write("resource disk: 5 GB free", "decision")
        snap = Snapshot(ts=now - 60, wallet=W, native_sol=1.25, wsol=0.0, held=[{"mint": "M1"}],
                        snipers=[{"id": "s1", "status": "running"}, {"id": "s2", "status": "idle"}], spent_today=0.45)  # fmt: skip
        resources = [
            {"name": "disk", "status": "crit", "message": "5 GB free"},
            {"name": "sol_float", "status": "ok", "message": "fine"},
        ]
        text = digest_text(snap, resources, led, st, now, paths=paths)
        assert "2026-11-06" in text and "1.250 SOL" in text and "+0.250" in text
        assert "0.45 $" in text and "0.90 $" in text
        assert "disk" in text and "fine" not in text
        assert "2 note(s)" in text and "resource disk" in text and "1 running" in text
        assert len(text) < 1500
        mark_sent(paths, now, store=st, snap=snap)
        assert st.coverage("digest:last")["equity"] == 1.25
    finally:
        st.close()


def test_digest_text_without_history_or_notes(tmp_path):
    paths = OperatorPaths(tmp_path / "data").ensure()
    st = Store(tmp_path / "data" / "snipetech.db")
    try:
        snap = Snapshot(ts=DAY, wallet=W)  # no balances read, no snipers
        text = digest_text(snap, [], Ledger(paths.spend, {}), st, DAY + 7 * 3600, paths=paths)
        assert "unknown" in text and "no notes" in text and "all ok" in text
    finally:
        st.close()


def test_round_sends_digest_once_and_only_after_the_hour(tmp_path, monkeypatch):
    from snipetech.operator.run import operate

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")

    class Svc:
        store = st

        async def oktech_status(self):
            return {"snipers": []}

    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    cfg.operator.digest_hour_utc = 7
    sent = []
    clock = {"now": DAY + 6 * 3600}

    def run():
        return asyncio.run(operate(cfg, Svc(), repo=tmp_path, branch="b", now=lambda: clock["now"],
                                   services=lambda: {"snipetech-tape": "active", "snipetech-ops": "active"}, balances=lambda w: (1.0, 0.0, []),
                                   turn=lambda *a, **k: {"usd": 0, "tool_calls": 0, "stopped": "end_turn", "notes": 0},
                                   disk_free_gb=lambda: 100.0, send=lambda t: sent.append(t) or True))  # fmt: skip

    try:
        run()
        assert not any("digest" in t.lower() for t in sent)
        clock["now"] = DAY + 7 * 3600 + 15 * 60
        run()
        run()
        digests = [t for t in sent if t.lower().startswith("digest")]
        assert len(digests) == 1 and "1.000 SOL" in digests[0] and "digest" in paths.log.read_text()
    finally:
        st.close()
