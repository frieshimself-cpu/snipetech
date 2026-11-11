"""The round keeps the wallet able to pay curve buys by turning its own wrapped SOL back (the owner's yes of 11 Nov:
"keeps plain SOL for up to two 0.2 SOL curve buys whenever the wallet has it in wrapped form"). Off until
`[book] keep_curve_buys` is set; never while the book is off or the kill switch is set; six times a day at most."""

import asyncio
import json

from snipetech.config import Config
from snipetech.operator import plain_sol as P
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import Snapshot
from snipetech.operator.tools import NoteSink
from snipetech.solana.unwrap import FEE_LAMPORTS

NOW = 1_794_436_000  # 11 Nov 2026 22:26:40 UTC
SOL = 1_000_000_000


def _world(tmp_path, keep=2, native=0.001224377, wsol=0.471817213, live=True):
    cfg = Config()
    cfg.book.keep_curve_buys = keep
    cfg.operator.wallet = "W" * 44
    paths = OperatorPaths(tmp_path / "data").ensure()
    snap = Snapshot(ts=NOW, wallet=cfg.operator.wallet, native_sol=native, wsol=wsol, book_live=live, book_fill_sol=0.2)
    notes = NoteSink(paths, now=lambda: NOW)
    calls: list[int] = []

    async def convert(amount: int) -> dict:
        calls.append(amount)
        return {"amount_sol": amount / SOL, "signature": "SIGCONVERT1234567890", "confirmed": True,
                "after": {"plain_sol": round(native + amount / SOL - 0.000011, 9), "wrapped_sol": round(wsol - amount / SOL, 9)}}  # fmt: skip

    return cfg, paths, snap, notes, calls, convert


def _run(cfg, paths, snap, notes, convert):
    return asyncio.run(P.keep_plain_sol(cfg, paths, snap, NOW, notes, convert=convert))


def test_it_is_off_until_the_owner_s_setting_says_how_many_buys(tmp_path):
    cfg, paths, snap, notes, calls, convert = _world(tmp_path, keep=0)
    assert Config().book.keep_curve_buys == 0  # the 0.05 SOL test comes first
    assert _run(cfg, paths, snap, notes, convert) is None and calls == []
    assert not paths.notes.exists() or paths.notes.read_text() == ""


def test_a_wallet_that_cannot_pay_a_curve_buy_gets_two_buys_of_plain_sol(tmp_path):
    cfg, paths, snap, notes, calls, convert = _world(tmp_path)
    out = _run(cfg, paths, snap, notes, convert)
    # two buys of 0.205 SOL, less what is already plain, and the conversion's own fee on top
    assert calls == [2 * 205_000_000 - 1_224_377 + FEE_LAMPORTS]
    assert out["signature"] == "SIGCONVERT1234567890"
    # the round's snapshot says what the wallet holds now: the agents read it this round
    assert snap.native_sol == out["after"]["plain_sol"] and snap.wsol == out["after"]["wrapped_sol"]
    note = paths.notes.read_text()
    assert "[info]" in note and "0.4088 wrapped SOL into plain SOL" in note and "SIGCONVERT12" in note
    rows = json.loads((paths.root / P.STATE).read_text())
    assert len(rows) == 1 and rows[0]["signature"] == "SIGCONVERT1234567890" and rows[0]["ts"] == NOW


def test_a_wallet_that_can_pay_one_curve_buy_is_left_alone(tmp_path):
    cfg, paths, snap, notes, calls, convert = _world(tmp_path, native=0.21, wsol=0.5)
    assert _run(cfg, paths, snap, notes, convert) is None and calls == []


def test_nothing_is_converted_while_the_book_is_off_the_kill_switch_is_set_or_the_wallet_was_not_read(tmp_path):
    cfg, paths, snap, notes, calls, convert = _world(tmp_path, live=False)
    assert _run(cfg, paths, snap, notes, convert) is None
    cfg, paths, snap, notes, calls2, convert = _world(tmp_path)
    paths.stop_flag.write_text("stop")
    assert _run(cfg, paths, snap, notes, convert) is None
    paths.stop_flag.unlink()
    snap.native_sol = None
    assert _run(cfg, paths, snap, notes, convert) is None
    assert calls == [] and calls2 == []


def test_six_conversions_a_day_at_most_and_the_seventh_is_said_once(tmp_path):
    cfg, paths, snap, notes, calls, convert = _world(tmp_path)
    day = NOW - NOW % 86400
    rows = [{"ts": day + 600 * i, "amount_sol": 0.4, "signature": f"S{i}"} for i in range(6)]
    rows.append({"ts": day - 3600, "amount_sol": 0.4, "signature": "YESTERDAY"})  # another day's does not count
    (paths.root / P.STATE).write_text(json.dumps(rows))
    out = _run(cfg, paths, snap, notes, convert)
    assert calls == [] and "6 conversions today" in out["held"]
    again = _run(cfg, paths, snap, notes, convert)
    assert calls == [] and "held" in again
    assert paths.notes.read_text().count("6 conversions today") == 1  # said once, not every round


def test_a_refusal_is_recorded_and_said_once_a_day(tmp_path):
    cfg, paths, snap, notes, _calls, _convert = _world(tmp_path)
    why = ["the wallet's key (SNIPER_SECRET) is not on the server"]

    async def refuse(amount: int) -> dict:
        return {"amount_sol": amount / SOL, "refused": why[0]}

    out = _run(cfg, paths, snap, notes, refuse)
    assert "not on the server" in out["refused"] and snap.native_sol == 0.001224377  # nothing changed
    _run(cfg, paths, snap, notes, refuse)
    text = paths.notes.read_text()
    assert text.count("could not turn wrapped SOL into plain SOL") == 1 and "not on the server" in text
    # the same refusal round after round is one row that counts, not 96 rows a day
    (row,) = json.loads((paths.root / P.STATE).read_text())
    assert "not on the server" in row["refused"] and "signature" not in row and row["times"] == 2
    assert row["ts"] == NOW and row["last_ts"] == NOW
    why[0] = "the chain refused it in simulation: AccountNotFound"  # another reason is another row
    _run(cfg, paths, snap, notes, refuse)
    rows = json.loads((paths.root / P.STATE).read_text())
    assert [r.get("times", 1) for r in rows] == [2, 1] and "AccountNotFound" in rows[1]["refused"]


def test_a_send_the_chain_did_not_confirm_in_time_still_counts_and_the_wallet_is_read_again(tmp_path):
    cfg, paths, snap, notes, _calls, _convert = _world(tmp_path)

    async def unseen(amount: int) -> dict:  # sent, not seen within a minute: the balances are what the chain says now
        return {"amount_sol": amount / SOL, "signature": "SIGUNSEEN1234567890", "confirmed": None,
                "after": {"plain_sol": 0.001213377, "wrapped_sol": 0.471817213}}  # fmt: skip

    out = _run(cfg, paths, snap, notes, unseen)
    assert out["signature"] and (snap.native_sol, snap.wsol) == (0.001213377, 0.471817213)
    assert "not confirmed" in paths.notes.read_text()
    (row,) = json.loads((paths.root / P.STATE).read_text())
    assert row["signature"] == "SIGUNSEEN1234567890" and row["confirmed"] is None  # one of the day's six


def test_a_running_sniper_is_reason_enough_while_the_book_is_off(tmp_path):
    """The crew sniper buys on the curve whether the book is on or not: what matters is that something of ours can
    buy. A sniper that is not running, or a list that could not be read, is no reason."""
    cfg, paths, snap, notes, calls, convert = _world(tmp_path, live=False)
    snap.snipers = [{"id": "a", "name": "crew", "status": "idle"}, {"id": "b", "name": "old", "status": "stopped"}]
    assert _run(cfg, paths, snap, notes, convert) is None and calls == []
    snap.snipers = None
    assert _run(cfg, paths, snap, notes, convert) is None and calls == []
    snap.snipers = [{"id": "a", "name": "crew", "status": "running"}]
    assert _run(cfg, paths, snap, notes, convert)["signature"] and len(calls) == 1


def test_a_refused_conversion_does_not_count_toward_the_day_s_six(tmp_path):
    cfg, paths, snap, notes, calls, convert = _world(tmp_path)
    day = NOW - NOW % 86400
    (paths.root / P.STATE).write_text(json.dumps([{"ts": day + i, "refused": "x"} for i in range(9)]))
    assert _run(cfg, paths, snap, notes, convert)["signature"] and len(calls) == 1


def test_an_error_in_the_step_never_breaks_the_round(tmp_path):
    cfg, paths, snap, notes, _calls, _convert = _world(tmp_path)

    async def boom(amount: int) -> dict:
        raise RuntimeError("SNIPER_SECRET is not set")

    out = _run(cfg, paths, snap, notes, boom)
    assert "SNIPER_SECRET is not set" in out["refused"]


def test_the_agents_are_told_whose_act_a_conversion_is():
    """native_sol up and wsol down by the same amount is the round's own conversion: not the owner's move (which they
    never flag anyway) and not a trade."""
    from snipetech.operator.prompts import ANALYST_SYSTEM, OPERATOR_SYSTEM

    for system in (OPERATOR_SYSTEM, ANALYST_SYSTEM):
        text = " ".join(system.split())
        assert "keep_curve_buys" in text and "not the owner's move and not a trade" in text
