"""`snipetech tips --accounts` and `snipetech tips --sample N` (14 Nov).

Two read-only views beside the tip table `tips` prints. `--accounts`: what the tape recorder has learned about the
destinations trades pay (the table `pay_dests`), with a head that says how many of each kind there are, whether the
learned tip accounts are applied to `trades.tip`, and since when the table is filled. `--sample N`: one job that
picks N trades spread over the newest hours of the tape, fetches each transaction from the chain, learns from them in
memory and says how many of the trades the tape holds with tip 0 pay a destination the sample reads as a tip account:
the check of the learning against the chain. Neither writes anything.

Round 2: the table shows the two bars (listed as "tip"; applied to trades' tips) and since when the recorder applies;
the sample draws transactions uniformly (the first version took the first trades of each time slice, which sit at
the head of a block and bid more), says that its unit is a transaction, and gives each share with its count and a
95 % interval.
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
        row("TipAccountOne", "tip", 900, 64, applied=1, tip_streak=7, sample_payers=58),
        row("TipAccountTwo", "tip", 45, 12, first_ts=NOW - 3_600, tip_streak=2, sample_payers=12),
        row("FeeAccount", "fee", 5_000, 64, rho=0.98, median_pct=1.0, median_lamports=4_000_000,
            first_ts=NOW - 86_400, sample_payers=90),
        row("Mixed", "mixed", 700, 64, rho=0.15, sample_payers=70,
            samples=json.dumps({"w": [f"wallet{k:02d}" for k in range(40)],
                                "p": [v for k in range(200) for v in
                                      ((k + 1) * 10_000 if k % 4 == 0 else 37_000_000, (k + 1) * 1_000_000, k % 40)]})),
        row("Undecided", None, 300, 30, rho=0.55, sample_payers=30),
        row("Young", None, 8, 4, rho=0.0, sample_payers=4),
    ], now=NOW - 600)  # fmt: skip
    st.mark_applied(["TipAccountOne"], now=NOW - 500)
    st.set_coverage("tape:learned_tips", {"applied_since": NOW - 500, "accounts": 1})
    st.set_coverage("tape:work", {"at": NOW - 30, "counts": {"pay_dests": 812, "pay_dests_fed": 40_000,
                                                             "pay_dests_saved": 97, "pay_tip_accounts": 1,
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
    assert head["destinations"] == {"tip": 2, "fee": 1, "mixed": 1, "undecided": 2}
    assert head["applied_accounts"] == 1  # of the two listed as tip, one passes the bar for being applied
    assert head["learned_tips_applied"] is True  # [live] apply_learned_tips, on by default since 14 Nov
    off = tmp_path / "off.toml"
    off.write_text("[live]\napply_learned_tips = false\n")
    assert run(capsys, "--config", str(off), "--db", str(db), "tips", "--accounts")[1]["head"]["learned_tips_applied"] is False
    assert (head["applied_since_ts"], head["applied_since"]) == (NOW - 500, "2026-11-14 06:58")  # tape:learned_tips
    assert head["filled_since_ts"] == NOW - 86_400  # the oldest first payment the table holds
    assert head["newest_payment_ts"] == NOW - 60
    assert head["recorder"] == {"at": NOW - 30, "pay_dests": 812, "pay_dests_fed": 40_000, "pay_dests_saved": 97,
                                "pay_tip_accounts": 1}  # fmt: skip
    rules = head["rules"]
    assert (rules["min_n"], rules["min_payers"], rules["stored_from_payers"]) == (30, 10, 3)
    assert (rules["applied_from_wallets_in_sample"], rules["tip_readings_in_a_row"], rules["one_wallets_share"]) == (
        25, 2, "a tenth of the sample")  # fmt: skip
    reading = " ".join(head["reading"])
    assert "evidence from amounts" in reading and "not from a service's" in reading  # rule 2, in the output's words
    assert "tip_learned" in reading and "25 different wallets" in reading and "two flushes in a row" in reading
    # what amounts cannot decide, in plain words
    blind = " ".join(head["not_decided_by_amounts"])
    assert "percentage of the buy reads as fee" in blind and "floor" in blind and "who owns the account" in blind
    assert [a["address"] for a in out["accounts"]] == ["FeeAccount", "TipAccountOne", "Mixed", "Undecided",
                                                       "TipAccountTwo", "Young"]  # the most paid first  # fmt: skip
    one = next(a for a in out["accounts"] if a["address"] == "TipAccountOne")
    assert one == {"address": "TipAccountOne", "kind": "tip", "applied": True, "n": 900, "payers": 64,
                   "sample_payers": 58, "rho": 0.02, "median_lamports": 1_000_000, "median_sol": 0.001,
                   "median_pct": 0.2, "tip_readings_in_a_row": 7, "first_ts": NOW - 7_200, "last_ts": NOW - 60,
                   "tip_since": NOW - 600, "applied_since": NOW - 500, "first_seen": "2026-11-14 05:06",
                   "last_seen": "2026-11-14 07:05", "tip_since_utc": "2026-11-14 06:56",
                   "applied_since_utc": "2026-11-14 06:58"}  # fmt: skip
    two = next(a for a in out["accounts"] if a["address"] == "TipAccountTwo")
    assert (two["kind"], two["applied"], two["applied_since"], two["sample_payers"]) == ("tip", False, None, 12)
    assert next(a for a in out["accounts"] if a["address"] == "Undecided")["kind"] is None
    assert "samples" not in one  # the table's long tail is not read for this view


def test_accounts_filters_by_kind_and_by_payments_and_the_head_still_counts_them_all(tmp_path, capsys):
    db = learned_db(tmp_path / "t.db")
    code, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "tip", "--min-n", "100")
    assert code == 0 and [a["address"] for a in out["accounts"]] == ["TipAccountOne"]
    assert (
        out["head"]["destinations"] == {"tip": 2, "fee": 1, "mixed": 1, "undecided": 2} and out["head"]["listed"] == 1
    )
    _, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "undecided")
    assert [a["address"] for a in out["accounts"]] == ["Undecided", "Young"]
    _, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "fee")
    assert [a["address"] for a in out["accounts"]] == ["FeeAccount"]
    _, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "mixed")
    assert [a["address"] for a in out["accounts"]] == ["Mixed"]
    _, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "applied")
    assert [a["address"] for a in out["accounts"]] == ["TipAccountOne"]
    with pytest.raises(SystemExit):
        cli.main(["--db", str(db), "tips", "--accounts", "--kind", "banana"])


def test_accounts_with_detail_reads_the_samples_of_the_rows_it_lists(tmp_path, capsys):
    """`--detail`: why a row reads as it does, from its stored sample: the share of its payments at one common share
    of the trade, that share, the spread of the trades' sizes and the wallets in the sample with their pairs."""
    db = learned_db(tmp_path / "t.db")
    _, out = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "mixed", "--detail")
    (mixed,) = out["accounts"]
    d = mixed["detail"]
    assert (
        d["pairs"] == 200 and d["common_ratio_share"] == 0.25 and d["common_ratio_pct"] == pytest.approx(1.0, abs=0.02)
    )
    assert d["wallets"] == 40 and d["most_pairs_by_one_wallet"] == 5 and d["quote_spread"] > 3
    _, plain = run(capsys, "--db", str(db), "tips", "--accounts", "--kind", "mixed")
    assert "detail" not in plain["accounts"][0]


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
    assert out["head"]["destinations"] == {"tip": 0, "fee": 0, "mixed": 0, "undecided": 0}
    assert out["head"]["filled_since_ts"] is None and out["head"]["applied_since_ts"] is None
    assert db.read_bytes() == before  # opened read-only: not even the missing table is created
    code = cli.main(["--db", str(tmp_path / "nothing.db"), "tips", "--accounts"])
    assert code == 2 and not (tmp_path / "nothing.db").exists()


def test_the_tip_table_itself_is_parsed_as_before():
    a = cli.build_parser().parse_args(["tips"])
    assert (a.group, a.wallet, a.top, a.gmgn, a.lookups) == ("big", [], 40, False, 6)
    assert a.accounts is False and a.sample is None and a.kind is None and a.min_n == 0 and a.seed is None
    assert a.detail is False
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


def block_tape(path: Path, secs: int = 1_200, per_slot: int = 100, seed: int = 4):
    """A tape written as the recorder writes it (the reviewers' s7_pick.py): in the order the stream hands the
    transactions over, slot after slot, each slot from its first place down, two or three slots a second. The first
    fifth of a block pays an unlisted tip account 60 % of the time, the rest 10 %; one transaction in twenty holds
    three trades. Returns the store and, per signature, (its place in the block, whether it pays, its trades)."""
    rng = random.Random(seed)
    st = Store(path)
    rows, truth, slot, k = [], {}, 0, 0
    for sec in range(secs):
        for _ in range(rng.choice((2, 3))):
            slot += 1
            for pos in range(per_slot):
                k += 1
                sig = f"s{k}"
                legs = 3 if rng.random() < 0.05 else 1
                truth[sig] = (pos, rng.random() < (0.6 if pos < per_slot // 5 else 0.1), legs)
                rows += [(sig, ev, slot, pos, NOW - secs + sec, "mint", "curve", "user", 1, 10**8 + ev, 1, 0, 0, 0, 0,
                          0, None, "payer", 1_000, 0) for ev in range(legs)]  # fmt: skip
    st.conn.executemany("INSERT INTO trades (sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, "
                        "fee_bps, q_after, b_after, r_base_after, ix, payer, priority, tip) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)  # fmt: skip
    st.conn.commit()
    return st, truth


def test_the_sample_draws_transactions_uniformly_not_the_head_of_each_block(tmp_path):
    """D5, the reviewers' reproduction (s7_pick.py). The first version took one of the first 64 trades of each time
    slice: the head of a block, where the bids are highest; on this tape 100 % of its picks came from a block's first
    fifth and it measured 60 % paying an unlisted tip account against 20 % true. Now each transaction of the window
    has the same chance, whatever its place and however many trades it holds."""
    st, truth = block_tape(tmp_path / "t.db")
    try:
        true_share = sum(1 for _p, pays, _l in truth.values() if pays) / len(truth)
        true_multi = sum(1 for _p, _u, legs in truth.values() if legs > 1) / len(truth)
        picked = TS.pick(st, NOW, n=2_000, hours=1 / 3)
        rows = picked["rows"]
        assert len(rows) == 2_000 == len({r["sig"] for r in rows})  # a transaction once
        head = sum(1 for r in rows if truth[r["sig"]][0] < 20) / len(rows)
        share = sum(1 for r in rows if truth[r["sig"]][1]) / len(rows)
        multi = sum(1 for r in rows if truth[r["sig"]][2] > 1) / len(rows)
        assert abs(head - 0.20) < 0.03  # a fifth of the picks from a block's first fifth
        assert abs(share - true_share) < 0.03 and 0.18 < true_share < 0.22
        assert abs(multi - true_multi) < 0.015  # a transaction of three trades is no likelier than one of one
        times = [r["ts"] for r in rows]
        assert times == sorted(times) and times[-1] - times[0] > 1_100  # over the whole window
        assert (picked["unit"], picked["seed"]) == ("transaction", picked["from_ts"])
        assert picked["to_ts"] == NOW - 1 and picked["from_ts"] == NOW - 1 - 1_200
        # the two ends of the window come off the time index; a row by its rowid; a transaction by its signature
        for sql, params in ((TS.FIRST_ROWID_SQL, (0,)), (TS.LAST_ROWID_SQL, (NOW,)), (TS.NEWEST_SQL, ())):
            plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN " + sql, params))
            assert "trades_ts" in plan and "SCAN trades" not in plan.replace("USING COVERING INDEX", "USING INDEX"), (
                plan
            )
        plan = " ".join(r[3] for r in st._q("EXPLAIN QUERY PLAN " + TS.ROW_SQL, (1,)))
        assert "INTEGER PRIMARY KEY" in plan or "rowid" in plan.lower()
        row = rows[0]
        assert set(row) == {"sig", "ts", "quote", "tip", "priority", "tip_learned"}
        assert row["quote"] in (10**8, 10**8 + 2)  # the transaction's largest trade
    finally:
        st.close()


def test_the_same_seed_draws_the_same_sample_and_another_seed_another(tmp_path):
    st, _truth = block_tape(tmp_path / "t.db", secs=300)
    try:
        a = [r["sig"] for r in TS.pick(st, NOW, 300, 1 / 12)["rows"]]
        assert a == [r["sig"] for r in TS.pick(st, NOW, 300, 1 / 12)["rows"]]  # the default seed: the window's start
        b = [r["sig"] for r in TS.pick(st, NOW, 300, 1 / 12, seed=7)["rows"]]
        assert b == [r["sig"] for r in TS.pick(st, NOW, 300, 1 / 12, seed=7)["rows"]] and b != a
        assert TS.pick(st, NOW, 300, 1 / 12, seed=7)["seed"] == 7
    finally:
        st.close()


def test_busy_seconds_are_sampled_by_their_trades_not_by_the_clock(tmp_path):
    """s7b_pick_rate.py: a pick a time slice gave a quiet second and a second of 300 trades the same chance."""
    st = Store(tmp_path / "t.db")
    rows, busy, k = [], {}, 0
    for sec in range(1_800):
        n = 300 if sec % 10 == 0 else 20  # every tenth second holds 300 trades: 62 % of all
        for j in range(n):
            k += 1
            busy[f"s{k}"] = n == 300
            rows.append((f"s{k}", 0, sec, j, NOW - 1_800 + sec, "m", "curve", "u", 1, 10**8, 1, 0, 0, 0, 0, 0, None,
                         "p", 1_000, 0))  # fmt: skip
    st.conn.executemany("INSERT INTO trades (sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote, base, fee, "
                        "fee_bps, q_after, b_after, r_base_after, ix, payer, priority, tip) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)  # fmt: skip
    st.conn.commit()
    try:
        true = sum(busy.values()) / len(busy)
        picked = TS.pick(st, NOW, 1_500, 0.5)["rows"]
        got = sum(busy[r["sig"]] for r in picked) / len(picked)
        assert 0.6 < true < 0.65 and abs(got - true) < 0.04
    finally:
        st.close()


def test_a_window_smaller_than_the_sample_gives_every_transaction_once(tmp_path):
    st, now = tape_of(tmp_path / "t.db", real_txs())
    try:
        picked = TS.pick(st, now, n=400, hours=1.0)
        assert 100 <= len(picked["rows"]) <= 115 == len(real_txs())
        assert len({r["sig"] for r in picked["rows"]}) == len(picked["rows"])
        assert len(picked["rows"]) == st._q("SELECT COUNT(DISTINCT sig) FROM trades")[0][0]  # each, once
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
    assert out["unit"] == "transaction" and s["seed"] == s["from_ts"]
    bug = out["tape_tip_zero"]
    assert bug["transactions"] >= 75  # most of the sample: recorded with tip 0
    unlisted = bug["pay_an_unlisted_destination"]
    assert unlisted["transactions"] >= 28  # and a third of them pay somebody the list does not know
    lo, hi = unlisted["share_ci95_pct"]
    assert lo < unlisted["share_pct"] < hi and hi - lo > 10  # a hundred transactions: a wide interval, and said
    # no tip account is seen five times among a hundred transactions (the services rotate through many accounts):
    # this small sample reads none as a tip, and says how many pay only destinations it cannot decide
    assert bug["pay_a_destination_read_as_tip"]["transactions"] == 0
    assert bug["pay_only_undecided_destinations"]["transactions"] >= 15
    reading = " ".join(out["reading"])
    assert "evidence from amounts" in reading and "true share lies between" not in reading
    assert "is read as a platform's fee" in reading and "one transaction" in reading
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
    assert seen["TIPX"]["sample_payers"] >= 50 and seen["TIPX"]["meets_the_applied_bars"] is False  # 200 pairs
    bug = out["tape_tip_zero"]
    assert bug["transactions"] == 500  # all but the hundred that tip a listed service
    assert bug["pay_an_unlisted_destination"] == {"transactions": 300, "share_pct": 60.0,
                                                  "share_ci95_pct": [55.6, 64.2]}  # fmt: skip
    assert bug["pay_only_destinations_read_as_fee"]["transactions"] == 100
    assert bug["pay_only_undecided_destinations"]["transactions"] == 0
    tip = bug["pay_a_destination_read_as_tip"]
    assert (tip["transactions"], tip["share_pct"], tip["share_ci95_pct"]) == (200, 40.0, [35.8, 44.4])
    assert tip["median_lamports"] == 1_000_000 and tip["p90_lamports"] == 1_750_000
    assert (tip["median_sol"], tip["p90_sol"]) == (0.001, 0.00175)
    assert out["tape_tip_above_zero"]["transactions"] == 100
    assert TS.wilson(0, 0) is None and TS.wilson(0, 50)[0] == 0.0 and TS.wilson(50, 50)[1] == 100.0
    assert TS.wilson(5, 20) == [11.2, 46.9]  # a quarter of twenty: anything from a ninth to nearly a half


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
    table = {"TIPX": {"kind": "tip", "applied": True}, "FEEX": {"kind": "tip", "applied": False},
             "ELSE": {"kind": "fee", "applied": False}}  # fmt: skip
    out = TS.report(reads, asked=600, hours=24.0, table=table)
    seen = {d["address"]: d for d in out["destinations"]}
    assert (seen["TIPX"]["table_kind"], seen["TIPX"]["table_applied"]) == ("tip", True)
    assert (seen["FEEX"]["table_kind"], seen["FEEX"]["table_applied"]) == ("tip", False)
    assert out["table"] == {"both_decided": 2, "agree": 1, "differ": ["FEEX"]}
    # the measurement by the recorder's table: what the tape writes into tip_learned once the switch is on (the
    # applied accounts), and what it would with every account it only lists as tip
    applied = out["tape_tip_zero"]["pay_a_destination_the_table_applies"]
    assert (applied["transactions"], applied["share_pct"], applied["median_lamports"]) == (200, 40.0, 1_000_000)
    assert out["tape_tip_zero"]["pay_a_destination_the_table_lists_as_tip"]["transactions"] == 300
    plain = TS.report(reads, asked=600, hours=24.0)
    assert "pay_a_destination_the_table_applies" not in plain["tape_tip_zero"] and "table" not in plain
    # only destinations both sides decided are compared: one the table has no kind for is no disagreement
    open_table = {"TIPX": {"kind": None, "applied": False}, "FEEX": {"kind": "fee", "applied": False}}
    assert TS.report(reads, asked=600, hours=24.0, table=open_table)["table"] == {"both_decided": 1, "agree": 1,
                                                                                    "differ": []}  # fmt: skip


def test_what_the_tape_already_wrote_as_learned_is_counted(tmp_path):
    reads = synthetic(60)
    for r in reads[:6]:
        r["tape_tip_learned"] = 800_000
        r["tape_tip"] = 800_000
    for r in reads[6:20]:
        r["tape_tip_learned"] = 0
    out = TS.report(reads, asked=60, hours=1.0)
    assert out["tape_tip_learned"] == {"transactions_read_with_learned_accounts": 20, "of_them_with_a_learned_part": 6,
                                       "median_lamports": 800_000}  # fmt: skip


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
    _, again = run(capsys, "--db", str(tmp_path / "t.db"), "tips", "--sample", "50", "--hours", "1", "--seed", "3")
    _, same = run(capsys, "--db", str(tmp_path / "t.db"), "tips", "--sample", "50", "--hours", "1", "--seed", "3")
    assert again["sample"]["seed"] == 3 and again["sample"]["picked"] == 50 and again == same
    assert cli.main(["--db", str(tmp_path / "t.db"), "tips", "--sample", "3001"]) == 2  # 3000 at most
    assert cli.main(["--db", str(tmp_path / "t.db"), "tips", "--sample", "0"]) == 2
    assert TS.SAMPLE_MAX == 3_000
