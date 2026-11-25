"""Copying on bought PumpSwap history (pumpfundata `pump_amm`, 25 Nov): wallets whose buys of coins that left their
curve pay when a copy comes in late and holds minutes to hours. Every search of 22-24 Nov scored pump.fun's newest
coins, where a copy one to three buyers late gives up about 6 % of its stake; a PumpSwap pool is deeper, and the tape
keeps its trades only 30 minutes after the curve, so the bought month is where it can be asked."""

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from snipetech.analysis import amm_follow as af

SOL = af.SOL_MINT
T0 = 1_793_000_000.0  # a whole hour (UTC) for the file names


class Pool:
    """A PumpSwap pool traded by constant product on its reserves, written as the vendor writes it: every row holds
    the reserves BEFORE its trade, a buy's `lamports_amount` includes its fee, a sell's is what the seller got."""

    def __init__(self, addr="POOL1", mint="MINT1pump", x=100_000_000_000, y=200_000_000_000_000, fee=0.01):
        self.addr, self.mint, self.x, self.y, self.fee = addr, mint, x, y, fee
        self.rows: list[dict] = []
        self.n = 0

    def _row(self, kind, slot, ts, user, action=None, tokens=0, lamports=0, fee=0, **extra):
        self.n += 1
        row = {"event_type": kind, "lamports_mint": SOL, "token_mint": self.mint, "pool_address": self.addr,
               "slot_number": slot, "timestamp": float(ts), "signature": f"{self.addr}-{self.n}",
               "virtual_token_reserve": int(self.y), "virtual_lamports_reserve": int(self.x), "action": action,
               "token_amount": int(tokens), "lamports_amount": int(lamports), "fee_lamports": int(fee),
               "fee_token_amount": 0, "user_wallet": user}  # fmt: skip
        row.update(extra)
        self.rows.append(row)
        return row

    def create(self, slot, ts):
        return self._row("create", slot, ts, "creator")

    def buy(self, slot, ts, user, sol):
        lamports = int(sol * 1e9)
        fee = int(lamports * self.fee)
        net = lamports - fee
        tokens = int(self.y * net / (self.x + net))
        row = self._row("swap", slot, ts, user, "buy", tokens, lamports, fee)
        self.x, self.y = self.x + net, self.y - tokens
        return row

    def sell(self, slot, ts, user, tokens):
        gross = int(self.x * tokens / (self.y + tokens))
        fee = int(gross * self.fee)
        row = self._row("swap", slot, ts, user, "sell", tokens, gross - fee, fee)
        self.x, self.y = self.x - gross, self.y + tokens
        return row


def _write(tmp_path, rows, hour_ts=T0):
    import time

    name = time.strftime("pump_amm_%Y-%m-%d_%H.parquet", time.gmtime(hour_ts))
    path = tmp_path / name
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _events(tmp_path, rows, **kw):
    out: list[dict] = []
    stats: dict = {}
    af.follow_amm([_write(tmp_path, rows)], out.append, stats=stats, **kw)
    return out, stats


def _one(out, wallet="lead"):
    """The one copy of `wallet`'s buy (other wallets' first buys of 0.5 SOL or more are copies too)."""
    (e,) = [e for e in out if e["wallet"] == wallet]
    return e


def test_the_files_swap_is_constant_product_on_the_reserves_before_the_trade():
    """Two rows of pump_amm_2026-11-23_20 (read with `history schema` on the server, 25 Nov): the buy got exactly the
    tokens of its net amount against the reserves the row holds, and the sell's gross is the seller's lamports plus
    the fee. So the reserves are the ones before the trade, and the fee is inside a buy's amount."""
    x, y = 496_297_486_912, 40_180_013_856_714
    assert af.buy_tokens(x, y, 7_079_823 - 66_627) == pytest.approx(567_776_984, rel=1e-6)  # a lamport of rounding
    x, y = 104_417_916_193, 173_261_668_415_493
    assert af.sell_gross(x, y, 169_824_650_597) == pytest.approx(101_019_386 + 1_226_958, rel=1e-6)


def test_the_rows_of_one_block_are_put_in_chain_order_by_the_token_reserves():
    p = Pool()
    rows = [p.buy(100, T0, "a", 1.0), p.buy(100, T0, "b", 2.0), p.sell(100, T0, "c", 10**12)]
    order = af.chain_order([2, 0, 1], [r["virtual_token_reserve"] for r in rows],
                           [r["token_amount"] for r in rows], [r["action"] == "buy" for r in rows])  # fmt: skip
    assert order == [0, 1, 2]


def test_a_wallets_first_buy_is_copied_behind_at_most_three_trades_of_its_copy_window(tmp_path):
    p = Pool()
    p.buy(100, T0, "lead", 2.0)
    p.buy(100, T0, "o1", 0.1)
    p.buy(101, T0, "o2", 0.1)
    p.buy(101, T0, "o3", 0.1)
    fourth = p.buy(102, T0 + 1, "o4", 0.1)  # the copy lands before this one: three trades are already between
    p.buy(5000, T0 + 30_000, "late", 0.1)  # one trade after the last horizon, so every exit is known
    e = _one(_events(tmp_path, p.rows)[0])
    assert e["wallet"] == "lead" and e["sol"] == pytest.approx(2.0)
    assert e["px_copy"] == pytest.approx(fourth["virtual_lamports_reserve"] / fourth["virtual_token_reserve"])


def test_a_quiet_pools_copy_lands_right_behind_the_copied_buy(tmp_path):
    p = Pool()
    lead = p.buy(100, T0, "lead", 2.0)
    nxt = p.buy(900, T0 + 170, "o1", 0.1)  # the next trade comes long after the copy window
    p.buy(5000, T0 + 30_000, "late", 0.1)
    e = _one(_events(tmp_path, p.rows)[0])
    assert e["px_copy"] == pytest.approx(nxt["virtual_lamports_reserve"] / nxt["virtual_token_reserve"])
    assert e["px_copy"] > lead["virtual_lamports_reserve"] / lead["virtual_token_reserve"]  # behind his buy


def test_the_slow_entry_is_the_pool_as_it_stands_thirty_seconds_after_the_buy(tmp_path):
    p = Pool()
    p.buy(100, T0, "lead", 2.0)
    p.buy(140, T0 + 10, "o1", 0.5)
    p.buy(230, T0 + 29, "o2", 0.5)
    at30 = p.buy(240, T0 + 31, "o3", 0.5)
    p.buy(5000, T0 + 30_000, "late", 0.1)
    e = _one(_events(tmp_path, p.rows)[0])
    assert e["px_slow"] == pytest.approx(at30["virtual_lamports_reserve"] / at30["virtual_token_reserve"])


def test_timed_exits_sell_into_the_pool_as_it_stands_at_each_horizon_from_the_entry(tmp_path):
    p = Pool()
    p.buy(100, T0, "lead", 2.0)
    p.buy(300, T0 + 59, "o1", 1.0)
    at60 = p.sell(310, T0 + 61, "o2", 10**12)
    at600 = p.buy(3000, T0 + 650, "o3", 1.0)
    p.buy(5000, T0 + 30_000, "late", 0.1)
    e = _one(_events(tmp_path, p.rows)[0])
    assert e["px_copy_60"] == pytest.approx(at60["virtual_lamports_reserve"] / at60["virtual_token_reserve"])
    assert e["px_copy_600"] == pytest.approx(at600["virtual_lamports_reserve"] / at600["virtual_token_reserve"])


def test_the_copy_exit_sells_behind_the_wallets_first_sale_of_the_coin(tmp_path):
    p = Pool()
    p.buy(100, T0, "lead", 2.0)
    p.buy(500, T0 + 100, "o1", 3.0)
    p.sell(1500, T0 + 300, "lead", 10**13)  # his first sale
    behind = p.buy(1600, T0 + 320, "o2", 0.1)  # the next trade after it, outside the copy window
    p.buy(5000, T0 + 30_000, "late", 0.1)
    e = _one(_events(tmp_path, p.rows)[0])
    assert e["sold_after"] == pytest.approx(300)
    assert e["px_copy_sell"] == pytest.approx(behind["virtual_lamports_reserve"] / behind["virtual_token_reserve"])


def test_a_copy_pays_the_pools_fee_both_ways_and_two_transactions_and_its_size_moves_the_price(tmp_path):
    """Nothing trades between the copy's buy and its sale: the round trip costs the fee each way and two
    transactions, whatever the size, because the sale sells into a pool that holds the copy's own buy."""
    p = Pool(fee=0.01)
    p.buy(100, T0, "lead", 2.0)
    p.buy(5000, T0 + 30_000, "late", 0.1)
    e = _one(_events(tmp_path, p.rows)[0])
    for size in af.SIZES:
        expect = size * 0.99 * 0.99 - size - 2 * af.TX_SOL
        assert e[f"copy_{size:g}_600"] == pytest.approx(expect, rel=1e-3)
    # a price that rose about 10 % between the buy and the sale: before the two transactions' fixed cost (3 % of a
    # 0.2 SOL copy, 0.1 % of a 5 SOL one), a 5 SOL copy into a 100 SOL pool gains less a SOL: its own buy moved the
    # price it came in at
    q = Pool(fee=0.01)
    q.buy(100, T0, "lead", 2.0)
    q.buy(200, T0 + 30, "pump", 5.0)
    q.buy(5000, T0 + 30_000, "late", 0.1)
    e = _one(_events(tmp_path, q.rows)[0])
    small, big = (e["copy_0.2_600"] + 2 * af.TX_SOL) / 0.2, (e["copy_5_600"] + 2 * af.TX_SOL) / 5
    assert small > big > 0


def test_only_a_wallets_first_buy_of_a_pool_at_or_above_min_sol_is_copied(tmp_path):
    p = Pool()
    p.buy(100, T0, "w", 0.3)  # below the floor: not a copy, and it does not use up his first buy
    p.buy(200, T0 + 20, "w", 2.0)
    p.buy(300, T0 + 40, "w", 3.0)  # his second buy of the coin: not copied again
    p.buy(5000, T0 + 30_000, "late", 0.1)
    out, _ = _events(tmp_path, p.rows, min_sol=0.5)
    assert [(e["wallet"], e["sol"]) for e in out] == [("w", pytest.approx(2.0))]


def test_pools_quoted_in_another_token_or_taking_fees_in_tokens_are_left_out(tmp_path):
    usdc = Pool(addr="P_USDC", mint="M2pump")
    usdc.buy(100, T0, "a", 2.0)["lamports_mint"] = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    tok = Pool(addr="P_TOK", mint="M3")
    tok.buy(100, T0, "b", 2.0)["fee_token_amount"] = 5
    tok.buy(5000, T0 + 30_000, "late", 0.1)
    out, stats = _events(tmp_path, usdc.rows + tok.rows)
    assert out == [] and stats["token_fee_pools"] == 1


def test_an_event_whose_last_horizon_runs_past_the_files_is_dropped_and_counted(tmp_path):
    p = Pool()
    p.buy(100, T0, "lead", 2.0)
    p.buy(200, T0 + 100, "o1", 0.4)
    out, stats = _events(tmp_path, p.rows)
    assert out == [] and stats["dropped"] == 1


def test_a_pool_that_goes_quiet_settles_at_the_end_of_the_files_with_the_price_it_was_left_at(tmp_path):
    p = Pool()
    p.create(90, T0 - 50)
    p.buy(100, T0, "lead", 2.0)
    p.buy(200, T0 + 100, "o1", 0.5)
    after_last = p.x / p.y
    other = Pool(addr="P2", mint="M2pump")
    other.buy(9000, T0 + 30_000, "clock", 0.1)  # the files run on past the last horizon
    e = _one(_events(tmp_path, p.rows + other.rows)[0])
    assert e["px_copy_21600"] == pytest.approx(after_last, rel=1e-6) and e["age"] == pytest.approx(50)


def test_events_are_kept_in_a_parquet_file_and_read_back(tmp_path):
    p = Pool()
    p.buy(100, T0, "lead", 2.0)
    p.buy(5000, T0 + 30_000, "late", 0.1)
    out, _ = _events(tmp_path, p.rows)
    path = tmp_path / "ev.parquet"
    af.write_events(out, path)
    back = pq.read_table(path).to_pylist()
    assert (
        len(back) == 1 and back[0]["wallet"] == "lead" and back[0]["copy_1_600"] == pytest.approx(out[0]["copy_1_600"])
    )


def _ev(wallet, ts, pnl, **kw):
    row = {"wallet": wallet, "pool": "P", "mint": "M", "ts": float(ts), "sol": 2.0, "pool_sol": 300.0,
           "mcap_sol": 600.0, "age": 7200.0}  # fmt: skip
    for key in af.KEYS:
        row[key] = pnl
    row.update(kw)
    return row


def test_wallets_are_picked_on_the_older_days_and_judged_on_the_newer(tmp_path):
    rows = []
    for i in range(60):
        day = i % 30
        rows.append(_ev("good", T0 + day * 86400 + i, 0.05 + 0.01 * (i % 3)))
        rows.append(_ev("bad", T0 + day * 86400 + i, -0.04))
        rows.append(_ev("lucky", T0 + day * 86400 + i, 0.08 if day < 21 else -0.08))
    path = tmp_path / "ev.parquet"
    af.write_events(rows, path)
    out = af.amm_table(path, split_days=21, min_pick=20, key="copy_1_600")
    assert out["picked"] == 2 and {w["wallet"] for w in out["top"]} == {"good", "lucky"}
    assert out["test"]["trades"] == 36 and out["test"]["sol_per_day"] == pytest.approx(
        (0.06 * 18 - 0.08 * 18) / 9, abs=0.01
    )
    assert out["everyone"]["test_mean"] < 0


def test_groups_of_buys_are_scored_by_size_depth_and_age_and_judged_on_the_newer_days(tmp_path):
    rows = []
    for i in range(400):
        day = i % 30
        rows.append(_ev(f"w{i}", T0 + day * 86400 + i, 0.03, sol=12.0, pool_sol=1500.0))  # big buys, deep pools
        rows.append(_ev(f"v{i}", T0 + day * 86400 + i, -0.02, sol=0.8, pool_sol=40.0))
    path = tmp_path / "ev.parquet"
    af.write_events(rows, path)
    out = af.amm_groups(path, split_days=21, key="copy_1_600", min_n=50)
    best = out["groups"][0]
    assert best["sol"] == "10+" and best["depth"] == "1000+" and best["pick_mean"] == pytest.approx(0.03)
    assert best["test_n"] > 0 and best["test_mean"] == pytest.approx(0.03)


def test_the_commands_keep_the_months_copies_once_and_table_them(tmp_path, monkeypatch, capsys):
    import json
    import time
    from pathlib import Path

    from snipetech import cli

    monkeypatch.delenv("PUMPFUNDATA_API_KEY", raising=False)  # neither command calls the vendor
    db = tmp_path / "data" / "snipetech.db"
    dest = db.parent / "history" / "pumpfundata" / "pump_amm"
    dest.mkdir(parents=True)
    p = Pool()
    p.buy(100, T0, "lead", 2.0)
    p.buy(5000, T0 + 30_000, "late", 0.1)
    _write(dest, p.rows)
    day = time.strftime("%Y-%m-%d", time.gmtime(T0))
    assert cli.main(["--db", str(db), "history", "amm-follow", "--from", day, "--to", day]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (
        out["events"] == 1
        and Path(out["path"]).exists()
        and out["path"].endswith(f"amm_events_{day}_{day}_0.5.parquet")
    )
    args = [
        "--db",
        str(db),
        "history",
        "amm-table",
        "--from",
        day,
        "--to",
        day,
        "--key",
        "copy_1_600",
        "--min-pick",
        "1",
    ]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["key"] == "copy_1_600"
    assert cli.main([*args, "--groups", "--min-n", "1"]) == 0 and "groups" in json.loads(capsys.readouterr().out)
    other = ["--db", str(db), "history", "amm-table", "--from", "2026-01-01", "--to", "2026-01-02"]
    assert cli.main(other) == 2 and "amm-follow" in capsys.readouterr().err  # nothing kept for those days
