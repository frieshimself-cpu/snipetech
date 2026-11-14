"""`snipetech tips --accounts` and `snipetech tips --sample N` (14 Nov).

Two read-only views beside the tip table `tips` prints. `--accounts`: what the tape recorder has learned about the
destinations trades pay (the table `pay_dests`), with a head that says how many of each kind there are, whether the
learned tip accounts are applied to `trades.tip`, and since when the table is filled. `--sample N`: one job that
picks N trades spread over the newest hours of the tape, fetches each transaction from the chain, learns from them in
memory and says how many of the trades the tape holds with tip 0 pay a destination the sample reads as a tip account:
the check of the learning against the chain. Neither writes anything.
"""

import asyncio
import gzip
import json
import random
from pathlib import Path

import pytest

from snipetech import cli
from snipetech.analysis import tip_sample as TS
from snipetech.solana.normalize import normalize
from snipetech.solana.tx import extract_from_transaction
from snipetech.store import Store

TIPS = Path(__file__).parent / "fixtures" / "tips"
NOW = 1_794_640_000


def row(address: str, kind: str | None, n: int, payers: int = 20, **over) -> dict:
    base = {"address": address, "kind": kind, "n": n, "payers": payers, "rho": 0.02, "median_lamports": 1_000_000,
            "median_pct": 0.2, "first_ts": NOW - 7_200, "last_ts": NOW - 60, "samples": "[1,2]"}  # fmt: skip
    return base | over


def learned_db(path: Path) -> Path:
    st = Store(path)
    st.save_pay_dests([
        row("TipAccountOne", "tip", 900, 64),
        row("TipAccountTwo", "tip", 45, 12, first_ts=NOW - 3_600),
        row("FeeAccount", "fee", 5_000, 64, rho=0.98, median_pct=1.0, median_lamports=4_000_000,
            first_ts=NOW - 86_400),
        row("Undecided", None, 300, 30, rho=0.55),
        row("Young", None, 8, 4, rho=0.0),
    ], now=NOW - 600)  # fmt: skip
    st.set_coverage("tape:work", {"at": NOW - 30, "counts": {"pay_dests": 812, "pay_dests_fed": 40_000,
                                                             "pay_dests_saved": 97, "pay_tip_accounts": 2,
                                                             "trades": 160_000}})  # fmt: skip
    st.close()
    return path


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip().startswith("{") else {"text": out})


# ---- tips --accounts -------------------------------------------------------------------------------------------------


def test_accounts_prints_the_learned_table_with_its_head(tmp_path, capsys, monkeypatch):
    db = learned_db(tmp_path / "t.db")
    monkeypatch.setattr(cli, "Service", lambda *a, **k: pytest.fail("tips --accounts opens no service: no RPC"))
    code, out = run(capsys, "--db", str(db), "tips", "--accounts")
    assert code == 0
    head = out["head"]
    assert head["destinations"] == {"tip": 2, "fee": 1, "undecided": 2}
    assert head["learned_tips_applied"] is False  # [live] apply_learned_tips, off by default
    assert head["filled_since_ts"] == NOW - 86_400  # the oldest first payment the table holds
    assert head["newest_payment_ts"] == NOW - 60
    assert head["recorder"] == {"at": NOW - 30, "pay_dests": 812, "pay_dests_fed": 40_000, "pay_dests_saved": 97,
                                "pay_tip_accounts": 2}  # fmt: skip
    assert (
        head["rules"]["min_n"] == 30 and head["rules"]["min_payers"] == 10 and head["rules"]["stored_from_payers"] == 3
    )
    reading = " ".join(head["reading"])
    assert "evidence from amounts" in reading and "not from a service's" in reading  # rule 2, in the output's words
    assert [a["address"] for a in out["accounts"]] == ["FeeAccount", "TipAccountOne", "Undecided", "TipAccountTwo",
                                                       "Young"]  # the most paid first  # fmt: skip
    one = next(a for a in out["accounts"] if a["address"] == "TipAccountOne")
    assert one == {"address": "TipAccountOne", "kind": "tip", "n": 900, "payers": 64, "rho": 0.02,
                   "median_lamports": 1_000_000, "median_sol": 0.001, "median_pct": 0.2, "first_ts": NOW - 7_200,
                   "last_ts": NOW - 60, "tip_since": NOW - 600,
                   "first_seen": "2026-11-14 05:06", "last_seen": "2026-11-14 07:05",
                   "tip_since_utc": "2026-11-14 06:56"}  # fmt: skip
    assert next(a for a in out["accounts"] if a["address"] == "Undecided")["kind"] is None
    assert "samples" not in one  # the table's long tail is not read for this view


def test_accounts_filters_by_kind_and_by_payments_and_the_head_still_counts_them_all(tmp_path, capsys):
    db = learned_db(tmp_path / "t.db")
    code, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "tip", "--min-n", "100")
    assert code == 0 and [a["address"] for a in out["accounts"]] == ["TipAccountOne"]
    assert out["head"]["destinations"] == {"tip": 2, "fee": 1, "undecided": 2} and out["head"]["listed"] == 1
    _, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "undecided")
    assert [a["address"] for a in out["accounts"]] == ["Undecided", "Young"]
    _, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "fee")
    assert [a["address"] for a in out["accounts"]] == ["FeeAccount"]
    with pytest.raises(SystemExit):
        cli.main(["--db", str(db), "tips", "--accounts", "--kind", "banana"])


def test_accounts_says_when_learned_tips_are_applied(tmp_path, capsys):
    db = learned_db(tmp_path / "t.db")
    cfg = tmp_path / "s.toml"
    cfg.write_text("[live]\napply_learned_tips = true\n")
    _, out = run(capsys, "--config", str(cfg), "--db", str(db), "tips", "--accounts")
    assert out["head"]["learned_tips_applied"] is True


def test_accounts_writes_nothing_and_reads_a_database_without_the_table(tmp_path, capsys):
    import sqlite3

    db = tmp_path / "old.db"
    Store(db).close()
    con = sqlite3.connect(db)
    con.execute("DROP TABLE pay_dests")
    con.commit()
    con.close()
    before = db.read_bytes()
    code, out = run(capsys, "--db", str(db), "tips", "--accounts")
    assert code == 0 and out["accounts"] == []
    assert (
        out["head"]["destinations"] == {"tip": 0, "fee": 0, "undecided": 0} and out["head"]["filled_since_ts"] is None
    )
    assert db.read_bytes() == before  # opened read-only: not even the missing table is created
    code = cli.main(["--db", str(tmp_path / "nothing.db"), "tips", "--accounts"])
    assert code == 2 and not (tmp_path / "nothing.db").exists()


def test_the_tip_table_itself_is_parsed_as_before():
    a = cli.build_parser().parse_args(["tips"])
    assert (a.group, a.wallet, a.top, a.gmgn, a.lookups) == ("big", [], 40, False, 6)
    assert a.accounts is False and a.sample is None and a.kind is None and a.min_n == 0
    assert cli._tips_hours(a) == 30.0  # the table's newest hours, as its default always was
    assert cli._tips_hours(cli.build_parser().parse_args(["tips", "--hours", "2"])) == 2.0
    s = cli.build_parser().parse_args(["tips", "--sample", "500"])
    assert cli._tips_hours(s) == TS.SAMPLE_HOURS == 24.0
    assert cli._tips_hours(cli.build_parser().parse_args(["tips", "--sample", "500", "--hours", "6"])) == 6.0


# ---- tips --sample ---------------------------------------------------------------------------------------------------


def real_txs() -> list[tuple[str, dict]]:
    with gzip.open(TIPS / "all115.jsonl.gz", "rt") as f:
        return [(r["tx"]["transaction"]["signatures"][0], r["tx"]) for r in map(json.loads, f)]


def tape_of(path: Path, txs: list[tuple[str, dict]], spread: int = 3_600) -> tuple[Store, int]:
    """A tape holding these real transactions' trades as the recorder wrote them (the tip from the fixed list), their
    times spread evenly over the last `spread` seconds before the newest."""
    st = Store(path)
    pools: dict = {}
    for _ in range(2):  # PumpSwap pools become known from the transactions themselves
        trades = []
        for k, (sig, tx) in enumerate(txs):
            norm = normalize(extract_from_transaction(tx, sig), 0, pools)
            pools.update(norm.pools)
            for t in norm.trades:
                t.ts = NOW - spread + (k * spread) // len(txs)
                trades.append(t)
    st.add_trades(trades)
    return st, NOW


class FakeRpc:
    def __init__(self, txs, missing=(), broken=()):
        self.txs, self.missing, self.broken = dict(txs), set(missing), set(broken)
        self.asked: list[str] = []
        self.now = self.peak = 0

    async def get_transaction(self, signature):
        self.asked.append(signature)
        self.now += 1
        self.peak = max(self.peak, self.now)
        try:
            await asyncio.sleep(0)
            if signature in self.broken:
                raise RuntimeError("the node did not answer")
            return None if signature in self.missing else self.txs.get(signature)
        finally:
            self.now -= 1


def test_the_sample_is_spread_over_the_hours_through_the_time_index(tmp_path):
    st, now = tape_of(tmp_path / "t.db", real_txs())
    try:
        picked = TS.pick(st, now, n=40, hours=1.0)
        assert 30 <= len(picked["rows"]) <= 40  # a slice without a trade gives none
        times = [r["ts"] for r in picked["rows"]]
        assert times == sorted(times) and times[-1] - times[0] > 3_000  # over the whole hour, not its newest minute
        assert len({r["sig"] for r in picked["rows"]}) == len(picked["rows"])  # a transaction once
        assert picked["to_ts"] == max(t for t in times) or picked["to_ts"] >= times[-1]
        assert all("INDEXED BY trades_ts" in sql or "sig = ?" in sql or "sig=?" in sql for sql in TS.SQL)
        plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN " + TS.SLICE_SQL, (0, 1, 1)))
        assert "USING INDEX trades_ts" in plan and "SCAN" not in plan
        row = picked["rows"][0]
        assert set(row) == {"sig", "ts", "quote", "tip", "priority"}
    finally:
        st.close()


def test_a_sample_of_the_real_transactions_reads_each_from_the_chain_and_writes_nothing(tmp_path):
    txs = real_txs()
    st, now = tape_of(tmp_path / "t.db", txs)
    sigs = [s for s, tx in txs if not extract_from_transaction(tx).pays]  # five that pay nobody: gone or failing
    rpc = FakeRpc(txs, missing=sigs[:2], broken=sigs[2:5])
    try:
        writes = []
        st._write = lambda *a, **k: writes.append(1)  # any write would be seen
        out = asyncio.run(TS.tip_sample(st, rpc, now, n=400, hours=1.0))
        assert writes == []
    finally:
        st.close()
    assert rpc.peak <= TS.SAMPLE_CONCURRENCY == 10  # at most ten at a time
    assert len(rpc.asked) == len(set(rpc.asked)) == out["sample"]["picked"]
    s = out["sample"]
    assert s["asked"] == 400 and 90 <= s["picked"] <= 115
    assert s["fetched"] + s["not_found"] + s["fetch_failed"] == s["picked"]
    assert (s["not_found"], s["fetch_failed"]) <= (2, 3) and s["fetch_failed"] >= 1  # counted, not fatal
    assert s["hours"] == 1.0 and s["from_ts"] < s["to_ts"] <= now
    assert out["sample_min_n"] == 5 and out["sample_min_payers"] == 3  # the bars a sample this small allows
    seen = {d["address"]: d for d in out["destinations"]}
    assert all(d["n"] >= TS.SAMPLE_SEEN == 5 for d in seen.values())
    platform = seen[
        "KfHbT5Jrr5SV3CDkQVCLiSNWyVv5DmuVaCzbAHrzMFQ"
    ]  # one router's platform account: a share of the trade
    assert platform["kind"] == "fee" and platform["rho"] > 0.9 and platform["payers"] >= 5
    # Axiom's two fee tiers, each on a rotating account: 1.023 % and 0.987 % of the trade
    assert (seen["2ApLdwLrGayEmxgpLX9BTR47Q2QprfMg5SpjrLeaK8s7"]["kind"],
            seen["2ApLdwLrGayEmxgpLX9BTR47Q2QprfMg5SpjrLeaK8s7"]["median_pct"]) == ("fee", 1.0222)  # fmt: skip
    assert seen["3PvqoztjnRxaAiFmLuEfqZkU4GSbjUareks8S2xCZaTa"]["median_pct"] == 0.9875
    bug = out["tape_tip_zero"]
    assert bug["trades"] >= 75  # most of the sample: recorded with tip 0
    assert bug["pay_an_unlisted_destination"] >= 28  # and a third of them pay somebody the list does not know
    # no tip account is seen five times among a hundred transactions (the services rotate through many accounts):
    # this small sample reads none as a tip, and says how many trades pay only destinations it cannot decide
    assert bug["pay_a_destination_read_as_tip"]["trades"] == 0
    assert bug["pay_only_undecided_destinations"] >= 15
    assert "evidence from amounts" in " ".join(out["reading"])
    assert out["writes"] == "nothing"


def synthetic(n: int = 600, seed: int = 5) -> list[dict]:
    """A market in the shapes the real transactions show: a third of the trades go through a router that takes one
    percent and tips a flat amount to an account the list does not know; a sixth pay only the one percent; a sixth
    tip a listed service (the tape has their tip); the rest pay nobody."""
    rng = random.Random(seed)
    out = []
    for k in range(n):
        quote = int(1e9 * 0.02 * 1000 ** rng.random())
        payer = f"wallet{rng.randrange(60)}"
        kind = k % 6
        pays, tape_tip = [], 0
        if kind in (0, 1):
            pays = [("FEEX", quote // 100), ("TIPX", rng.choice((800_000, 1_000_000, 1_750_000)))]
        elif kind == 2:
            pays = [("FEEX", quote // 100)]
        elif kind == 3:
            tape_tip = 1_000_000
        out.append({"sig": f"sig{k}", "ts": NOW - n + k, "quote": quote, "tape_tip": tape_tip, "payer": payer,
                    "pays": pays, "chain_tip": tape_tip})  # fmt: skip
    return out


def test_the_report_measures_what_the_tape_misses():
    reads = synthetic(600)
    out = TS.report(reads, asked=600, hours=24.0)
    assert out["sample_min_n"] == 6 and out["sample_min_payers"] == 3  # 600 trades: a hundredth of them, half that
    seen = {d["address"]: d for d in out["destinations"]}
    assert (seen["TIPX"]["kind"], seen["TIPX"]["n"]) == ("tip", 200) and seen["TIPX"]["payers"] >= 50
    assert seen["FEEX"]["kind"] == "fee" and seen["FEEX"]["n"] == 300 and seen["FEEX"]["median_pct"] == 1.0
    bug = out["tape_tip_zero"]
    assert bug["trades"] == 500  # all but the hundred that tip a listed service
    assert bug["pay_an_unlisted_destination"] == 300
    assert bug["pay_only_destinations_read_as_fee"] == 100
    assert bug["pay_only_undecided_destinations"] == 0
    tip = bug["pay_a_destination_read_as_tip"]
    assert tip["trades"] == 200 and tip["share_pct"] == 40.0
    assert tip["median_lamports"] == 1_000_000 and tip["p90_lamports"] == 1_750_000
    assert (tip["median_sol"], tip["p90_sol"]) == (0.001, 0.00175)
    assert out["tape_tip_above_zero"]["trades"] == 100


def test_the_sample_allows_lower_bars_and_names_them():
    assert TS.sample_bars(3_000) == (30, 10)  # the recorder's own bars
    assert TS.sample_bars(1_000) == (10, 5)
    assert TS.sample_bars(600) == (6, 3)
    assert TS.sample_bars(100) == (5, 3)  # never under five payments and three wallets
    reads = synthetic(240)
    out = TS.report(reads, asked=240, hours=1.0)
    assert out["sample_min_n"] == 5
    assert "sample_min_n" in " ".join(out["reading"])


def test_a_destination_the_recorder_already_knows_is_compared_with_the_table():
    reads = synthetic(600)
    out = TS.report(reads, asked=600, hours=24.0, table={"TIPX": "tip", "FEEX": "tip", "ELSE": "fee"})
    seen = {d["address"]: d for d in out["destinations"]}
    assert seen["TIPX"]["table_kind"] == "tip" and seen["FEEX"]["table_kind"] == "tip"
    assert out["table"] == {"compared": 2, "agree": 1, "differ": ["FEEX"]}
    # and the measurement by the recorder's table: what the tape would have written with its learned accounts applied
    by_table = out["tape_tip_zero"]["pay_a_destination_the_table_reads_as_tip"]
    assert by_table["trades"] == 300 and by_table["share_pct"] == 60.0  # FEEX counted too: the table has it wrong here
    plain = TS.report(reads, asked=600, hours=24.0)
    assert "pay_a_destination_the_table_reads_as_tip" not in plain["tape_tip_zero"] and "table" not in plain


def test_sample_runs_as_one_job_from_the_command_line(tmp_path, capsys, monkeypatch):
    txs = real_txs()
    st, _now = tape_of(tmp_path / "t.db", txs)
    st.close()
    rpc = FakeRpc(txs)

    class Svc:
        def __init__(self, cfg):
            self.store = Store(cfg.storage.db_path)
            self.tape_store = self.store
            self.rpc = rpc

        async def close(self):
            pass

    monkeypatch.setattr(cli, "Service", Svc)
    monkeypatch.setattr(cli.time, "time", lambda: float(NOW))
    code, out = run(capsys, "--db", str(tmp_path / "t.db"), "tips", "--sample", "200", "--hours", "1")
    assert code == 0 and out["sample"]["asked"] == 200 and out["sample"]["fetched"] == out["sample"]["picked"] > 80
    assert cli.main(["--db", str(tmp_path / "t.db"), "tips", "--sample", "3001"]) == 2  # 3000 at most
    assert cli.main(["--db", str(tmp_path / "t.db"), "tips", "--sample", "0"]) == 2
    assert TS.SAMPLE_MAX == 3_000
