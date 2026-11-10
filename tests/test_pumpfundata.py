"""Bought history: pumpfundata.com's hourly files of every pump.fun trade (owner, 10 Nov: 3,000 credits, one credit an
hourly file). Six days of our own tape make every result "one day, read after the fact"; months of history check a rule
in an hour. A credit is money: a file on disk is never paid for twice, and a run never spends more than it was told."""

import asyncio
from pathlib import Path

from snipetech import pumpfundata as pfd

SAMPLE = Path(__file__).parent / "fixtures" / "pumpfundata" / "sample.parquet"


def test_the_hours_of_a_date_range_leave_out_the_files_already_on_disk():
    have = {pfd.file_name("pump_fun", "2026-11-01", 0), pfd.file_name("pump_fun", "2026-11-02", 23)}
    hours = pfd.plan_hours("2026-11-01", "2026-11-02", have, "pump_fun")
    assert len(hours) == 46 and hours[0] == ("2026-11-01", 1) and hours[-1] == ("2026-11-02", 22)
    assert pfd.file_name("pump_fun", "2026-11-01", 5) == "pump_fun_2026-11-01_05.parquet"


class _Api:
    """Answers each hour with a parquet-looking body; hour 3 has nothing; the rest of the day is out of credits."""

    def __init__(self):
        self.asked: list[tuple[str, str, int]] = []

    async def download(self, exchange, date, hour):
        self.asked.append((exchange, date, hour))
        if hour == 3:
            return 404, b'{"error": "no such file"}'
        if hour >= 6:
            return 402, b'{"error": "no credits"}'
        return 200, b"PAR1" + bytes([hour]) * 8 + b"PAR1"


def test_a_run_downloads_what_is_missing_up_to_its_limit_and_never_pays_twice(tmp_path):
    api = _Api()
    hours = [("2026-11-01", h) for h in range(24)]
    out = asyncio.run(pfd.fetch(api, tmp_path, "pump_fun", hours, max_files=3))
    assert out["downloaded"] == 3 and out["stopped"] == "the run's limit of 3 files"
    assert sorted(p.name for p in tmp_path.iterdir()) == [pfd.file_name("pump_fun", "2026-11-01", h) for h in range(3)]
    again = asyncio.run(pfd.fetch(api, tmp_path, "pump_fun", hours, max_files=10))
    assert [a[2] for a in api.asked] == [0, 1, 2, 3, 4, 5, 6]  # the three on disk were not asked for again
    assert again["downloaded"] == 2 and again["missing"] == [("2026-11-01", 3)] and "402" in again["stopped"]
    assert not list(tmp_path.glob("*.part")) and not (tmp_path / pfd.file_name("pump_fun", "2026-11-01", 3)).exists()


def test_a_body_that_is_not_a_parquet_file_is_not_kept(tmp_path):
    class Html:
        async def download(self, exchange, date, hour):
            return 200, b"<html>maintenance</html>"

    out = asyncio.run(pfd.fetch(Html(), tmp_path, "pump_fun", [("2026-11-01", 0)], max_files=5))
    assert out["downloaded"] == 0 and "not a parquet file" in out["stopped"] and not list(tmp_path.iterdir())


def test_the_vendors_sample_reads_as_trades_with_wallet_slot_signature_and_reserves():
    rows = pfd.read_rows(SAMPLE)
    assert len(rows) == 500 and {r["event_type"] for r in rows} <= {"swap", "create", "bonding_complete"}
    swap = next(r for r in rows if r["event_type"] == "swap")
    assert swap["action"] in ("buy", "sell") and len(swap["signature"]) > 80 and len(swap["user_wallet"]) >= 32
    assert swap["slot_number"] > 400_000_000 and swap["virtual_lamports_reserve"] > 30_000_000_000
    assert swap["lamports_amount"] > 0 and swap["token_mint"].endswith("pump")


def test_a_file_is_checked_trade_for_trade_against_the_same_hour_of_our_tape():
    rows = [{"event_type": "swap", "signature": s} for s in ("a", "b", "b", "c")] + [
        {"event_type": "create", "signature": "z"}
    ]
    out = pfd.compare(rows, {"b", "c", "d"})
    assert out == {"file_trades": 3, "tape_trades": 3, "both": 2, "only_file": 1, "only_tape": 1, "match": 0.5}


def test_the_key_is_scrubbed_from_job_output_and_only_the_cloud_session_may_spend_credits():
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED, REDACT_ENV

    assert pfd.KEY_ENV in REDACT_ENV and "history" in ALLOWED
    assert "history" not in RESEARCH_COMMANDS and "history" not in OWNER_COMMANDS  # the agents never spend credits


def test_the_tape_gives_the_signatures_of_one_venues_trades_in_an_hour():
    import time

    from snipetech.store import Store

    from .synth import Market, addr

    now = int(time.time())
    st = Store(":memory:")
    try:
        m = Market(st, addr("pfd-coin"), addr("pfd-dev"), now - 100, 94_000_000)
        m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
        m.buy(addr("pfd-a"), 1.0, 5)
        m.commit()
        sigs = st.trade_sigs_between(now - 200, now + 200, "curve")
        assert len(sigs) == 2 and m.create_sig in sigs
        assert st.trade_sigs_between(now - 200, now + 200, "amm") == set()
        assert st.trade_sigs_between(now + 100, now + 200, "curve") == set()
    finally:
        st.close()


def test_history_needs_the_key_and_says_so_without_calling_anyone(monkeypatch, capsys):
    from snipetech import cli

    monkeypatch.delenv(pfd.KEY_ENV, raising=False)
    assert cli.main(["history", "range"]) == 2
    assert pfd.KEY_ENV in capsys.readouterr().err


def _swap(sig, slot, mint, action, amount, vs, fee=0):
    return {"event_type": "swap", "signature": sig, "slot_number": slot, "token_mint": mint, "action": action,
            "lamports_amount": amount, "fee_lamports": fee, "virtual_lamports_reserve": vs}  # fmt: skip


def test_the_files_trade_order_is_checked_against_the_tapes_place_in_the_block():
    """10 Nov: a third of a month's trades did not continue the reserves of the row before them. Either the rows of a
    block are not in chain order or the amounts are read wrong; our own tape of the same hour knows every trade's
    place in its block, so it says which."""
    rows = [
        _swap("a", 10, "M", "buy", 100, 1100),
        _swap("c", 10, "M", "buy", 50, 1350),  # the file has c before b; the chain went a, b, c
        _swap("b", 10, "M", "buy", 200, 1300),
        _swap("d", 11, "M", "sell", 90, 1250, fee=10),  # a sell takes out what the seller got plus the fee
        _swap("x", 11, "OTHER", "buy", 5, 1005),
    ]
    tape = {"a": (10, 3, 0), "b": (10, 5, 0), "c": (10, 9, 0), "d": (11, 1, 0)}
    out = pfd.order_report(rows, tape)
    assert out["pairs"] == 3 and out["same_slot_pairs"] == 2 and out["same_slot_in_order"] == 1
    assert out["cross_slot_pairs"] == 1 and out["cross_slot_in_order"] == 1
    assert out["chain"]["pairs"] == 3 and out["chain"]["ok"] == 0  # c after a, b after c, d after b: none continues
    assert out["chain_in_tape_order"]["pairs"] == 3 and out["chain_in_tape_order"]["ok"] == 3  # a, b, c, d does
    assert out["sell_fits"]["amount_plus_fee"] == 1 and out["buy_fits"]["amount"] == 2
