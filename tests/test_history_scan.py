"""The website rule on bought history: "a bundled launch (others put 15+ SOL in the create block) with a website" was
+22 % a minute later on one day of our own tape. The scan reads the vendor's hourly files in order and scores every
launch the same way `launch-tools` scores the tape, so the rule is checked on weeks, day by day."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from snipetech.analysis.history_scan import history_table, scan_files
from snipetech.store import Store

SOL = 1_000_000_000
V0_SOL, V0_TOK = 30 * SOL, 1_073_000_000_000_000
T0, S0 = 1_793_000_000, 460_000_000  # an hour's first second and slot


class Coin:
    """Writes one coin's rows the way the vendor does: a create row, then swaps with the reserves after each."""

    def __init__(self, rows, mint, dev, slot, uri="https://x/meta.json", quote=None):
        self.rows, self.mint, self.dev, self.slot0 = rows, mint, dev, slot
        self.vs, self.vt, self.n, self.quote = V0_SOL, V0_TOK, 0, quote
        self._row("create", slot, dev, None, 0, name=mint, symbol=mint[:4].upper(), uri=uri)

    def _row(self, kind, slot, user, action, lamports, **more):
        self.n += 1
        self.rows.append({
            "event_type": kind, "signature": f"{self.mint}-{self.n}", "slot_number": slot,
            "timestamp": float(T0 + int((slot - S0) * 0.4)), "token_mint": self.mint, "token_creator": self.dev,
            "action": action, "user_wallet": user, "lamports_amount": lamports, "virtual_token_reserve": self.vt,
            "virtual_lamports_reserve": self.vs, "real_lamports_reserve": self.vs - V0_SOL,
            "name": more.get("name"), "symbol": more.get("symbol"), "uri": more.get("uri"), "lamports_mint": self.quote,
            "fee_lamports": more.get("fee", lamports // 100 if lamports else None),
        })  # fmt: skip

    def buy(self, user, sol, off):
        s = int(sol * SOL)
        out = self.vt * s // (self.vs + s)
        self.vs, self.vt = self.vs + s, self.vt - out
        self._row("swap", self.slot0 + off, user, "buy", s)
        return out

    def sell(self, user, tokens, off):
        s = self.vs * tokens // (self.vt + tokens)
        self.vs, self.vt = self.vs - s, self.vt + tokens
        fee = s // 100  # the vendor's sell amount is what the seller got: the reserves fall by amount + fee
        self._row("swap", self.slot0 + off, user, "sell", s - fee, fee=fee)


def _write(path: Path, rows: list[dict]) -> Path:
    rows = sorted(rows, key=lambda r: r["slot_number"])  # stable: a coin's rows keep their order inside a slot
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def _two_hours(tmp_path):
    first, second = [], []
    web = Coin(first, "WEBcoin", "dev-web", S0 + 100)  # organised, has a website, runs
    web.buy("dev-web", 1.0, 0)
    for i in range(4):
        web.buy(f"web-team-{i}", 4.0, 0)
    web.buy("crowd", 20.0, 60)
    rug = Coin(first, "RUGcoin", "dev-rug", S0 + 200)  # organised, no website, dumped
    rug.buy("dev-rug", 1.0, 0)
    bags = [rug.buy(f"rug-team-{i}", 4.0, 0) for i in range(4)]
    rug.buy("sniper", 1.0, 1)
    for i, bag in enumerate(bags):
        rug.sell(f"rug-team-{i}", bag, 40)
    solo = Coin(first, "SOLOcoin", "dev-solo", S0 + 300)  # nobody else in its create block
    solo.buy("dev-solo", 0.5, 0)
    solo.buy("late", 1.0, 5)
    usd = Coin(first, "USDcoin", "dev-usd", S0 + 400, quote="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    usd.buy("dev-usd", 1.0, 0)  # not priced in SOL: left out
    web.rows = second  # the coin keeps trading in the next hour's file: its peak is followed across files
    web.buy("whale", 40.0, 9_500)
    return [
        _write(tmp_path / "pump_fun_2026-10-27_00.parquet", first),
        _write(tmp_path / "pump_fun_2026-10-27_01.parquet", second),
    ]


def test_every_launch_in_the_files_is_scored_like_the_tape_and_followed_across_files(tmp_path):
    recs = {r["mint"]: r for r in scan_files(_two_hours(tmp_path), min_block_sol=0)}
    assert set(recs) == {"WEBcoin", "RUGcoin", "SOLOcoin"}  # the coin not priced in SOL is left out
    web, rug, solo = recs["WEBcoin"], recs["RUGcoin"], recs["SOLOcoin"]
    assert web["block_wallets"] == 4 and round(web["block_sol"]) == 16 and round(web["dev_sol"], 1) == 1.0
    assert web["rets"][150] > 50 and web["peak_sol"] > 75 and web["uri"] == "https://x/meta.json"
    assert rug["block_wallets"] == 4 and rug["rets"][150] < -40 and rug["peak_sol"] < 20
    assert solo["block_wallets"] == 0 and solo["block_sol"] == 0
    assert web["day"] == "2026-10-27" or len(web["day"]) == 10
    kept = list(scan_files(_two_hours(tmp_path), min_block_sol=15))
    assert {r["mint"] for r in kept} == {"WEBcoin", "RUGcoin"}  # only the organised ones are kept one by one


def test_the_table_says_how_organised_launches_did_with_and_without_a_website_and_by_day(tmp_path):
    st = Store(":memory:")
    try:
        st.put_token_meta("WEBcoin", "u", True, {"website": "https://web.io", "x_kind": "profile", "telegram": ""})
        st.put_token_meta("RUGcoin", "u", True, {"website": "", "x_kind": "tweet", "telegram": ""})
        stats: dict = {}
        recs = list(scan_files(_two_hours(tmp_path), min_block_sol=15, stats=stats))
        out = history_table(recs, st.token_meta, min_block_sol=15, stats=stats)
        assert stats["launches"] == 3 and out["launches"] == 3 and out["organised"] == 2 and out["files"] == 2
        web = {r["bucket"]: r for r in out["by"]["website"]}
        assert web["yes"]["launches"] == 1 and web["yes"]["ret_150"] > 50 and web["yes"]["reached_30"] == 1.0
        assert web["no"]["launches"] == 1 and web["no"]["ret_150"] < -40
        day = out["by_day"][0]
        assert (
            day["website"] == 1
            and day["website_ret_150"] > 50
            and day["no_website"] == 1
            and day["no_website_ret_150"] < -40
        )
        assert out["order_breaks"] == 0.0  # the rows were in chain order: each trade's reserves follow the one before
    finally:
        st.close()


def test_a_launch_whose_bundle_bought_the_whole_curve_is_counted_apart_not_scored(tmp_path):
    """5-9 Oct on the bought files: the median organised launch showed exactly 0 % a minute later. Those are coins
    whose own bundle fills the curve in the create block (85 SOL): there is nothing left to buy a block later, a
    sniper's buy fails. They are counted (`bonded_at_once`) and left out of every row."""
    rows: list[dict] = []
    full = Coin(rows, "FULLcoin", "dev-full", S0 + 100)
    full.buy("dev-full", 5.0, 0)
    for i in range(4):
        full.buy(f"full-team-{i}", 20.5, 0)  # 87 SOL in the create block: the curve is complete
    part = Coin(rows, "PARTcoin", "dev-part", S0 + 200)
    part.buy("dev-part", 1.0, 0)
    for i in range(4):
        part.buy(f"part-team-{i}", 8.0, 0)
    part.buy("crowd", 10.0, 60)
    part.buy("late", 1.0, 400)
    path = _write(tmp_path / "pump_fun_2026-10-27_00.parquet", rows)
    st = Store(":memory:")
    try:
        stats: dict = {}
        recs = list(scan_files([path], min_block_sol=15, stats=stats))
        assert {r["mint"]: r["bonded_at_once"] for r in recs} == {"FULLcoin": True, "PARTcoin": False}
        out = history_table(recs, st.token_meta, min_block_sol=15, stats=stats)
        assert out["organised"] == 1 and out["bonded_at_once"] == 1
        sol = {r["bucket"]: r["launches"] for r in out["by"]["block_sol"]}
        assert sol == {"25-50": 1} and out["order_breaks"] == 0.0
    finally:
        st.close()


def test_the_table_also_splits_by_the_x_account_behind_the_coin_when_accounts_were_looked_up(tmp_path):
    """Owner, 10 Nov: "like X is gold verified". The coin's link names an account; the account's badge, followers and
    age are rows of the same table. A coin with no account link, and an account never looked up, each get their own."""
    st = Store(":memory:")
    try:
        st.put_token_meta("WEBcoin", "u", True, {"website": "https://web.io", "x_kind": "profile", "x_handle": "acme"})
        st.put_token_meta("RUGcoin", "u", True, {"website": "", "x_kind": "community", "x_community": "9"})
        st.put_x_account({"handle": "acme", "found": True, "badge": "gold", "followers": 52_000,
                          "created_ts": T0 - 3 * 365 * 86400})  # fmt: skip
        stats: dict = {}
        recs = list(scan_files(_two_hours(tmp_path), min_block_sol=15, stats=stats))
        out = history_table(
            recs, st.token_meta, min_block_sol=15, stats=stats, x_of=lambda h: st.x_accounts([h]).get(h)
        )
        badge = {r["bucket"]: r for r in out["by"]["x_badge"]}
        assert badge["gold"]["launches"] == 1 and badge["gold"]["ret_150"] > 50
        assert badge["no account"]["launches"] == 1 and badge["no account"]["ret_150"] < -40
        assert [r["bucket"] for r in out["by"]["x_followers"]] == ["10k-100k", "no account"]
        assert [r["bucket"] for r in out["by"]["x_age"]] == ["1y+", "no account"]
        plain = history_table(recs, st.token_meta, min_block_sol=15, stats=stats)
        assert "x_badge" not in plain["by"]  # no lookups asked for: no rows of unknowns
    finally:
        st.close()


def test_a_scan_is_kept_on_disk_so_the_table_is_made_again_without_reading_the_files(tmp_path):
    """16 days of files take 20 minutes to read (10 Nov). The launches and the counts are saved once; a new split or a
    new threshold is then a table over the saved launches."""
    from snipetech.analysis.history_scan import load_scan, save_scan

    st = Store(":memory:")
    try:
        st.put_token_meta("WEBcoin", "u", True, {"website": "https://web.io", "x_kind": "profile"})
        stats: dict = {}
        recs = list(scan_files(_two_hours(tmp_path), min_block_sol=5, stats=stats))
        first = history_table(recs, st.token_meta, min_block_sol=15, stats=stats)
        save_scan(tmp_path / "scan.json", recs, stats)
        recs2, stats2 = load_scan(tmp_path / "scan.json")
        assert history_table(recs2, st.token_meta, min_block_sol=15, stats=stats2) == first
        assert recs2[0]["rets"].keys() == recs[0]["rets"].keys()  # the horizons are numbers again, not text
    finally:
        st.close()


def test_features_are_crossed_and_each_cell_is_shown_for_each_half_of_the_days(tmp_path):
    """30 days, 10 Nov: a website alone is +1.3 % a minute later, a Telegram link +5.7 %, a dev buy of 0.5-2 SOL +5 %.
    Whether they add up is a cross of them; and a cell found among dozens is luck unless it pays in both halves of the
    days, so every cell carries its first-half and second-half return."""
    rows1: list[dict] = []
    rows2: list[dict] = []
    for day, rows in enumerate((rows1, rows2)):
        for i in range(3):
            good = Coin(rows, f"GOOD{day}{i}", f"gdev{day}{i}", S0 + 100 + 50 * i)  # website, dev buys 0.7 SOL, runs
            good.buy(f"gdev{day}{i}", 0.7, 0)
            for k in range(4):
                good.buy(f"g{day}{i}-team-{k}", 4.0, 0)
            good.buy("crowd", 20.0, 60)
            good.buy("late", 0.1, 400)
            bad = Coin(rows, f"BAD{day}{i}", f"bdev{day}{i}", S0 + 110 + 50 * i)  # website, no dev buy, dumped
            bags = [bad.buy(f"b{day}{i}-team-{k}", 4.0, 0) for k in range(4)]
            for k, bag in enumerate(bags):
                bad.sell(f"b{day}{i}-team-{k}", bag, 40)
            bad.buy("late", 0.1, 400)
    paths = [_write(tmp_path / "pump_fun_2026-10-27_00.parquet", rows1)]
    for r in rows2:  # the second day's coins a day later
        r["slot_number"] += 216_000
        r["timestamp"] += 86_400
    paths.append(_write(tmp_path / "pump_fun_2026-10-28_00.parquet", rows2))
    st = Store(":memory:")
    try:
        stats: dict = {}
        recs = list(scan_files(paths, min_block_sol=15, stats=stats))
        for r in recs:
            st.put_token_meta(r["mint"], "u", True, {"website": "https://w.io", "x_kind": "profile", "telegram": ""})
        out = history_table(
            recs, st.token_meta, min_block_sol=15, stats=stats, cross=("website", "dev_sol"), min_cell=2
        )
        assert [c["cell"] for c in out["cross"]] == ["website=yes, dev_sol=0.5-1", "website=yes, dev_sol=0"]
        good, bad = out["cross"]  # cells are listed best first, by the worse of their two halves
        assert good["launches"] == 6 and good["ret_150"] > 50 and good["first_half"] > 50 and good["second_half"] > 50
        assert good["first_half_n"] == 3 and good["second_half_n"] == 3
        assert bad["launches"] == 6 and bad["first_half"] < -40 and bad["second_half"] < -40
        small = history_table(
            recs, st.token_meta, min_block_sol=15, stats=stats, cross=("website", "dev_sol"), min_cell=7
        )
        assert small["cross"] == []  # a cell with too few launches is not shown
        assert "cross" not in history_table(recs, st.token_meta, min_block_sol=15, stats=stats)
    finally:
        st.close()
