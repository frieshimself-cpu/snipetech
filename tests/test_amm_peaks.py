"""When each coin's market cap first passed each bar on the bought PumpSwap month, and its peak (25 Nov): the labels
the big-deployer funding study needs without hindsight. A creator counts as big only from the moment one of his
coins passed the bar, never from a peak reached after the launch being judged."""

import json
import random
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from snipetech.analysis import amm_peaks as ap

SOL = ap.SOL_MINT
T0 = 1_793_000_000.0  # a whole hour (UTC) for the file names


class Pool:
    """A PumpSwap pool traded by constant product, written as the vendor writes it: every row holds the reserves
    BEFORE its trade, a buy's `lamports_amount` includes its fee, a sell's is what the seller got."""

    def __init__(self, addr="POOL1", mint="MINT1pump", x=200_000_000_000, y=200_000_000_000_000, fee=0.01,
                 creator="DEV1", quote=SOL):  # fmt: skip
        self.addr, self.mint, self.x, self.y, self.fee = addr, mint, x, y, fee
        self.creator, self.quote = creator, quote
        self.rows: list[dict] = []

    def cap(self) -> float:
        """The market cap in SOL as the pool stands: its price times a pump.fun coin's 1e15 raw tokens."""
        return self.x / self.y * 1e15 / 1e9

    def _row(self, kind, slot, ts, action=None, tokens=0, lamports=0, fee=0):
        row = {"event_type": kind, "lamports_mint": self.quote, "token_mint": self.mint, "pool_address": self.addr,
               "slot_number": slot, "timestamp": float(ts), "virtual_token_reserve": int(self.y),
               "virtual_lamports_reserve": int(self.x), "action": action, "token_amount": int(tokens),
               "lamports_amount": int(lamports), "fee_lamports": int(fee), "token_creator": self.creator,
               "user_wallet": "someone"}  # fmt: skip
        self.rows.append(row)
        return row

    def buy(self, slot, ts, sol):
        lamports = int(sol * 1e9)
        fee = int(lamports * self.fee)
        net = lamports - fee
        tokens = int(self.y * net / (self.x + net))
        row = self._row("swap", slot, ts, "buy", tokens, lamports, fee)
        self.x, self.y = self.x + net, self.y - tokens
        return row

    def sell(self, slot, ts, tokens):
        gross = int(self.x * tokens / (self.y + tokens))
        fee = int(gross * self.fee)
        row = self._row("swap", slot, ts, "sell", tokens, gross - fee, fee)
        self.x, self.y = self.x - gross, self.y + tokens
        return row


def _write(folder: Path, rows, hour_ts=T0) -> Path:
    path = folder / time.strftime("pump_amm_%Y-%m-%d_%H.parquet", time.gmtime(hour_ts))
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def test_the_cap_after_a_swap_comes_from_the_reserves_before_it_and_its_own_amounts(tmp_path):
    p = Pool()
    start = p.cap()
    p.buy(100, T0, 30.0)
    after_buy = p.cap()
    p.sell(101, T0 + 1, 10**13)
    after_sell = p.cap()
    (coin,) = ap.scan_peaks([_write(tmp_path, p.rows)], bars=(1.0,)).values()
    assert start < after_buy and after_sell < after_buy
    assert coin["peak_sol"] == pytest.approx(after_buy, rel=1e-9)  # the buy's after-state, not the pool before it
    assert coin["peak_slot"] == 100 and coin["peak_ts"] == T0
    assert coin["n"] == 2 and coin["first_slot"] == 100 and coin["last_ts"] == T0 + 1


def test_each_bar_is_first_passed_at_the_first_swap_that_leaves_the_cap_above_it(tmp_path):
    p = Pool()  # 1,000 SOL of market cap to start
    caps = []
    for slot, sol in ((100, 5.0), (101, 20.0), (103, 1.0), (104, 40.0)):
        p.buy(slot, T0 + slot - 100, sol)
        caps.append((slot, p.cap()))
    p.sell(102, T0 + 2, 3 * 10**13)  # written after slot 104 but earlier on chain: rows need no order
    bar1 = next(s for s, c in caps if c >= 1_100)
    bar2 = next(s for s, c in caps if c >= 1_500)
    (coin,) = ap.scan_peaks([_write(tmp_path, p.rows)], bars=(1_100, 1_500, 10_000)).values()
    assert (coin["s_1100"], coin["s_1500"]) == (bar1, bar2) and bar1 < bar2
    assert coin["t_1100"] == T0 + bar1 - 100
    assert coin["s_10000"] is None and coin["t_10000"] is None  # a bar never passed


def test_rows_of_a_block_in_any_order_give_the_same_labels(tmp_path):
    p = Pool()
    p.buy(200, T0, 10.0)
    p.buy(200, T0, 25.0)
    p.sell(200, T0, 2 * 10**13)
    p.buy(201, T0 + 1, 3.0)
    first = ap.scan_peaks([_write(tmp_path, p.rows)], bars=(1_100, 1_300))
    rows = list(p.rows)
    random.Random(7).shuffle(rows)
    (tmp_path / "shuffled").mkdir()
    assert ap.scan_peaks([_write(tmp_path / "shuffled", rows)], bars=(1_100, 1_300)) == first


def test_pools_quoted_in_another_token_and_events_that_are_not_swaps_are_left_out(tmp_path):
    p = Pool()
    p.buy(100, T0, 1.0)
    p._row("liquidity", 101, T0 + 1, "buy", 10**14, 10**15)  # amounts that are no trade, whatever its side says
    p._row("create", 99, T0 - 1)
    p._row("swap", 102, T0 + 2, None, 10**12, 10**9)  # a swap with no side
    usdc = Pool(addr="POOL2", mint="MINT2pump", x=10**15, quote="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    usdc.buy(100, T0, 1.0)  # an enormous "cap" in another token's units
    out = ap.scan_peaks([_write(tmp_path, p.rows + usdc.rows)], bars=(1.0,))
    assert set(out) == {"MINT1pump"} and out["MINT1pump"]["n"] == 1


def test_a_coin_in_two_files_keeps_its_first_crossing_and_its_overall_peak(tmp_path):
    p = Pool()
    p.buy(100, T0, 20.0)  # passes 1,100 in the first hour
    first_hour = [r | {"token_creator": ap.NO_CREATOR} for r in p.rows]  # the vendor's "no creator" that hour
    p.rows.clear()
    p.buy(9_000, T0 + 3_600, 60.0)  # the peak and 1,500 in the second hour
    a = _write(tmp_path, first_hour, T0)
    b = _write(tmp_path, p.rows, T0 + 3_600)
    (coin,) = ap.scan_peaks([a, b], bars=(1_100, 1_500)).values()
    assert coin["s_1100"] == 100 and coin["s_1500"] == 9_000
    assert coin["peak_slot"] == 9_000 and coin["peak_sol"] == pytest.approx(p.cap(), rel=1e-9)
    assert coin["first_ts"] == T0 and coin["last_ts"] == T0 + 3_600 and coin["n"] == 2
    assert coin["creator"] == "DEV1"  # from the hour that names one


def test_the_coins_creator_and_the_pool_of_its_peak_are_kept(tmp_path):
    a = Pool(addr="POOLA", creator="DEVX")
    b = Pool(addr="POOLB", creator="DEVX")  # the same coin's second pool, where it went higher
    a.buy(100, T0, 5.0)
    b.buy(101, T0 + 1, 50.0)
    (coin,) = ap.scan_peaks([_write(tmp_path, a.rows + b.rows)], bars=(1.0,)).values()
    assert coin["creator"] == "DEVX" and coin["pool"] == "POOLB"


def test_only_coins_whose_peak_reaches_the_floor_are_written_and_the_file_appears_whole(tmp_path):
    big, small = Pool(mint="BIGpump"), Pool(mint="SMALLpump")
    big.buy(100, T0, 40.0)
    small.buy(100, T0, 0.1)
    peaks = ap.scan_peaks([_write(tmp_path, big.rows + small.rows)], bars=(1_100,))
    out = tmp_path / "peaks" / "amm_peaks.jsonl"
    assert ap.write_peaks(peaks, out, min_sol=1_100) == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["mint"] for r in rows] == ["BIGpump"] and not list(out.parent.glob("*.tmp"))


def test_the_command_reads_the_days_files_under_a_lab_slot_and_writes_the_coins_past_the_first_bar(
    tmp_path, monkeypatch, capsys
):
    import contextlib

    from snipetech import cli
    from snipetech.analysis import lab_lock as ll

    held = []

    @contextlib.contextmanager
    def fake_lock(data_dir, *a, **k):
        held.append(Path(data_dir))
        yield None

    monkeypatch.setattr(ll, "lab_lock", fake_lock)
    monkeypatch.delenv("PUMPFUNDATA_API_KEY", raising=False)  # no call to the vendor
    db = tmp_path / "data" / "snipetech.db"
    dest = db.parent / "history" / "pumpfundata" / "pump_amm"
    dest.mkdir(parents=True)
    p = Pool()  # 1,000 SOL of market cap; 30 SOL in takes it past the first bar (1,250 SOL, about 100k USD)
    p.buy(100, T0, 30.0)
    _write(dest, p.rows)
    day = time.strftime("%Y-%m-%d", time.gmtime(T0))
    assert cli.main(["--db", str(db), "history", "amm-peaks", "--from", day, "--to", day]) == 0
    out = json.loads(capsys.readouterr().out)
    assert held == [db.parent.resolve()]
    assert out["coins"] == 1 and out["written"] == 1 and out["bars"][str(ap.BARS_SOL[0])] == 1
    assert Path(out["path"]).exists() and out["path"].endswith(f"amm_peaks_{day}_{day}.jsonl")
    other = ["--db", str(db), "history", "amm-peaks", "--from", "2026-01-01", "--to", "2026-01-02"]
    assert cli.main(other) == 2 and "no PumpSwap files" in capsys.readouterr().err
