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
            "token_amount": more.get("tokens"),
        })  # fmt: skip

    def buy(self, user, sol, off):
        s = int(sol * SOL)
        out = self.vt * s // (self.vs + s)
        self.vs, self.vt = self.vs + s, self.vt - out
        self._row("swap", self.slot0 + off, user, "buy", s, tokens=out)
        return out

    def sell(self, user, tokens, off):
        s = self.vs * tokens // (self.vt + tokens)
        self.vs, self.vt = self.vs - s, self.vt + tokens
        fee = s // 100  # the vendor's sell amount is what the seller got: the reserves fall by amount + fee
        self._row("swap", self.slot0 + off, user, "sell", s - fee, fee=fee, tokens=tokens)


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


def test_an_exit_rule_is_walked_along_a_launchs_price_path():
    """Most bundled launches lose a little and a few run far (30 days, 10 Nov: the mean is +5 %, the median -3 %), so
    the exit decides the result. The scan keeps each launch's price at the end of every block after the entry, as a
    ratio to the entry price; a rule is walked along it at table time."""
    from snipetech.analysis.history_scan import exit_return

    path = [[1, 1.05], [3, 0.95], [10, 1.40], [30, 1.90], [80, 1.30], [200, 0.60], [900, 0.20]]
    assert exit_return(path, {"slots": 13}) == 40.0  # time only: the price at the last block inside the time
    assert exit_return(path, {"slots": 2}) == 5.0
    assert round(exit_return(path, {"slots": 1500}), 6) == -80.0
    assert round(exit_return(path, {"stop": 0.10, "slots": 1500}), 6) == -40.0  # the stop is seen a block late
    assert round(exit_return(path, {"stop": 0.03, "slots": 1500}), 6) == -5.0  # out at the block that closed under it
    assert round(exit_return(path, {"take": 0.5, "slots": 1500}), 6) == 90.0  # the block closed at +90 %, not at +50 %
    assert round(exit_return(path, {"trail": 0.25, "slots": 1500}), 6) == 30.0  # 25 % under the high of 1.90
    assert (
        round(exit_return(path, {"stop": 0.5, "trail": 0.5, "slots": 100}), 6) == 30.0
    )  # nothing hit: the time ends it
    assert exit_return([], {"slots": 150}) == 0.0  # nobody traded after the entry: in and out at one price


def test_the_scan_keeps_each_organised_launchs_path_and_the_table_scores_exit_rules_on_it(tmp_path):
    st = Store(":memory:")
    try:
        st.put_token_meta("WEBcoin", "u", True, {"website": "https://web.io", "x_kind": "profile", "telegram": ""})
        st.put_token_meta("RUGcoin", "u", True, {"website": "", "x_kind": "tweet", "telegram": ""})
        stats: dict = {}
        recs = {r["mint"]: r for r in scan_files(_two_hours(tmp_path), min_block_sol=15, stats=stats)}
        web, rug = recs["WEBcoin"]["path"], recs["RUGcoin"]["path"]
        assert web[0][0] == 59 and web[0][1] > 1.5  # the crowd's buy, 59 blocks after the entry block
        assert rug[0][0] == 39 and rug[0][1] < 0.6  # the team's dump
        out = history_table(list(recs.values()), st.token_meta, min_block_sol=15, stats=stats, cross=("website",),
                            min_cell=1, exits=True)  # fmt: skip
        cells = {c["cell"]: c for c in out["cross"]}
        yes, no = cells["website=yes"]["exits"], cells["website=no"]["exits"]
        assert yes["t60s"]["ret"] > 50 and no["t60s"]["ret"] < -40 and no["stop15_t10m"]["ret"] < -40
        assert set(yes) == set(out["exits"]) and out["exits"]["t60s"]["n"] == 2
        assert "first_half" in yes["t60s"] and "win" in yes["t60s"]
    finally:
        st.close()


def test_a_launch_is_sorted_by_how_its_bundle_wallets_earlier_launches_did(tmp_path):
    """10 Nov, 30 days: launches bundled by wallets whose earlier launches ran kept running the week after. The scan
    keeps who bought in each create block; the table gives every launch the record its bundle wallets had BEFORE it
    (the mean a-minute-later return of their earlier launches), so the split uses nothing a live rule would not have."""
    rows: list[dict] = []
    for i in range(4):  # one team launches four coins that run; another four that it dumps
        good = Coin(rows, f"GOOD{i}", f"gdev{i}", S0 + 100 + 1000 * i)
        for k in range(4):
            good.buy(f"good-team-{k}", 4.0, 0)
        good.buy("crowd", 20.0, 60)
        good.buy("late", 0.1, 400)
        bad = Coin(rows, f"BAD{i}", f"bdev{i}", S0 + 600 + 1000 * i)
        bags = [bad.buy(f"bad-team-{k}", 4.0, 0) for k in range(4)]
        for k, bag in enumerate(bags):
            bad.sell(f"bad-team-{k}", bag, 40)
        bad.buy("late", 0.1, 400)
    path = _write(tmp_path / "pump_fun_2026-10-27_00.parquet", rows)
    st = Store(":memory:")
    try:
        stats: dict = {}
        recs = list(scan_files([path], min_block_sol=15, stats=stats))
        assert sorted(recs[0]["block_users"]) == [f"good-team-{k}" for k in range(4)]
        out = history_table(recs, st.token_meta, min_block_sol=15, stats=stats, team_min_prior=2)
        team = {r["bucket"]: r for r in out["by"]["team"]}
        assert team["no record"]["launches"] == 4  # each team's first two launches: fewer than two before them
        assert team["25+"]["launches"] == 2 and team["25+"]["ret_150"] > 50  # the good team's third and fourth
        assert team["<-10"]["launches"] == 2 and team["<-10"]["ret_150"] < -40  # the dumping team's third and fourth
        crossed = history_table(recs, st.token_meta, min_block_sol=15, stats=stats, team_min_prior=2,
                                cross=("team",), min_cell=1)  # fmt: skip
        assert {c["cell"] for c in crossed["cross"]} == {"team=no record", "team=25+", "team=<-10"}
    finally:
        st.close()


def test_a_late_entry_and_how_many_teams_stand_behind_a_row_are_shown(tmp_path):
    """The team record's top rows were +13 to +24 % a minute later (30 days, 10 Nov). Two questions before it is a
    rule: is a row many teams or one crew (its distinct bundle wallets, and the share of its launches the commonest
    wallet is in), and what is left when the buy lands a few blocks later than the block after the create."""
    from snipetech.analysis.history_scan import exit_return

    path = [[1, 1.10], [2, 1.20], [5, 1.50], [100, 1.80], [200, 0.90]]
    assert exit_return(path, {"slots": 150}) == 80.0
    assert exit_return(path, {"enter": 2, "slots": 150}) == 50.0  # in at the close two blocks later: 1.80 / 1.20
    assert exit_return(path, {"enter": 4, "slots": 150}) == 50.0  # nothing traded in blocks 3 and 4: still 1.20
    assert (
        round(exit_return(path, {"enter": 2, "stop": 0.2, "slots": 400}), 4) == -25.0
    )  # the stop is from the late entry
    assert exit_return([[9, 2.0]], {"enter": 3, "slots": 150}) == 100.0  # nobody traded before the late entry

    rows: list[dict] = []
    for i in range(4):
        good = Coin(rows, f"GOOD{i}", f"gdev{i}", S0 + 100 + 1000 * i)
        for k in range(4):
            good.buy(f"good-team-{k}", 4.0, 0)
        good.buy("crowd", 20.0, 60)
        good.buy("late", 0.1, 400)
    st = Store(":memory:")
    try:
        stats: dict = {}
        recs = list(
            scan_files([_write(tmp_path / "pump_fun_2026-10-27_00.parquet", rows)], min_block_sol=15, stats=stats)
        )
        out = history_table(recs, st.token_meta, min_block_sol=15, stats=stats, team_min_prior=2, exits=True)
        top = next(r for r in out["by"]["team"] if r["bucket"] == "25+")
        assert top["team_wallets"] == 4 and top["top_wallet_share"] == 1.0  # one crew: the same four wallets every time
        assert out["exits"]["late3_t60s"]["ret"] == out["exits"]["t60s"]["ret"]  # nothing traded in the first blocks
    finally:
        st.close()


def test_a_stop_or_a_take_fills_a_block_after_it_is_seen_and_rows_can_start_at_a_later_day(tmp_path):
    """A stop or a take is seen at a block's close and the sell lands in the next block: the fill is the next block's
    close when the coin traded in it (or the one after), else the price it was seen at. And a rule found on a month
    is judged on later days only: the team records still come from every launch, the rows from `show_from` on."""
    from snipetech.analysis.history_scan import exit_return

    path = [[1, 1.10], [2, 1.60], [3, 1.30], [50, 0.70], [200, 0.40]]
    assert exit_return(path, {"take": 0.5, "slots": 150}) == 30.0  # seen at 1.60, filled a block later at 1.30
    assert (
        exit_return(path, {"stop": 0.2, "slots": 150}) == -30.0
    )  # seen at 0.70; the coin did not trade in the next blocks
    assert exit_return(path, {"take": 0.5, "slots": 150, "fill": 0}) == 60.0  # the old reading, for comparison

    rows1: list[dict] = []
    for i in range(4):
        good = Coin(rows1, f"GOOD{i}", f"gdev{i}", S0 + 100 + 1000 * i)
        for k in range(4):
            good.buy(f"good-team-{k}", 4.0, 0)
        good.buy("crowd", 20.0, 60)
        good.buy("late", 0.1, 400)
    rows2: list[dict] = []
    later = Coin(rows2, "GOOD9", "gdev9", S0 + 216_000 + 100)  # a day of slots later
    for k in range(4):
        later.buy(f"good-team-{k}", 4.0, 0)
    later.buy("crowd", 20.0, 60)
    later.buy("late", 0.1, 400)
    paths = [
        _write(tmp_path / "pump_fun_2026-10-27_00.parquet", rows1),
        _write(tmp_path / "pump_fun_2026-10-28_00.parquet", rows2),
    ]
    st = Store(":memory:")
    try:
        stats: dict = {}
        recs = list(scan_files(paths, min_block_sol=15, stats=stats))
        day2 = max(r["day"] for r in recs)
        out = history_table(recs, st.token_meta, min_block_sol=15, stats=stats, team_min_prior=2, show_from=day2)
        assert out["organised"] == 1 and out["show_from"] == day2
        assert [(r["bucket"], r["launches"]) for r in out["by"]["team"]] == [
            ("25+", 1)
        ]  # its record is the day before's
    finally:
        st.close()


def test_a_wallet_list_refreshed_once_a_day_is_what_a_sniper_can_be_given(tmp_path):
    """OKTech's Hunt buys a launch once a wallet from a list it was given has bought (`knownBotWallets`). The split
    `hunt` is that rule: a launch is "listed" when one of its create-block wallets was, at the last daily refresh
    before it, in five or more scored launches averaging +10 % or better a minute later. The list's size is reported."""
    day = 216_000  # slots
    rows1: list[dict] = []
    for i in range(3):
        good = Coin(rows1, f"GOOD{i}", f"gdev{i}", S0 + 100 + 1000 * i)
        for k in range(4):
            good.buy(f"good-team-{k}", 4.0, 0)
        good.buy("crowd", 20.0, 60)
        good.buy("late", 0.1, 400)
        bad = Coin(rows1, f"BAD{i}", f"bdev{i}", S0 + 600 + 1000 * i)
        bags = [bad.buy(f"bad-team-{k}", 4.0, 0) for k in range(4)]
        for k, bag in enumerate(bags):
            bad.sell(f"bad-team-{k}", bag, 40)
        bad.buy("late", 0.1, 400)
    rows2: list[dict] = []
    mixed = Coin(rows2, "MIXED", "mdev", S0 + day + 100)  # one listed wallet among new ones
    mixed.buy("good-team-0", 4.0, 0)
    for k in range(3):
        mixed.buy(f"new-{k}", 4.0, 0)
    mixed.buy("crowd", 20.0, 60)
    mixed.buy("late", 0.1, 400)
    again = Coin(rows2, "BADAGAIN", "bdev9", S0 + day + 600)
    for k in range(4):
        again.buy(f"bad-team-{k}", 4.0, 0)
    again.buy("late", 0.1, 400)
    paths = [
        _write(tmp_path / "pump_fun_2026-10-27_00.parquet", rows1),
        _write(tmp_path / "pump_fun_2026-10-28_00.parquet", rows2),
    ]
    st = Store(":memory:")
    try:
        stats: dict = {}
        recs = list(scan_files(paths, min_block_sol=15, stats=stats))
        out = history_table(recs, st.token_meta, min_block_sol=15, stats=stats, team_min_prior=3)
        hunt = {r["bucket"]: r for r in out["by"]["hunt"]}
        assert hunt["listed"]["launches"] == 1 and hunt["listed"]["examples"][0]["symbol"] == "MIXE"
        assert hunt["not listed"]["launches"] == 7  # the first day's six (the list is a day old) and the bad team's
        assert out["hunt_list"] == {"wallets": 4, "min_prior": 3, "min_ret": 10.0, "refresh_secs": 86400}
    finally:
        st.close()


def test_rows_stored_out_of_order_are_put_back_in_chain_order_before_anything_is_scored(tmp_path):
    """10 Nov: one launch in eight of the bought month had a price path that jumped tenfold and back. Some hourly
    files do not hold a coin's trades in block order, and none says where in a block a trade was. Every file is
    put in block order first, and a coin's trades inside one block are ordered by the token reserves, which chain
    exactly (a buy takes its tokens out of the reserve, a sell puts them back)."""
    import random

    rows: list[dict] = []
    coin = Coin(rows, "RUNcoin", "dev", S0 + 100)
    coin.buy("dev", 1.0, 0)
    for k in range(4):
        coin.buy(f"team-{k}", 4.0, 0)
    bag = coin.buy("sniper-a", 1.0, 1)
    coin.buy("sniper-b", 2.0, 1)
    coin.sell("sniper-a", bag, 1)  # three trades in the entry block: its close is the sell
    coin.buy("crowd", 10.0, 60)
    coin.sell("sniper-b", coin.vt // 300, 60)
    coin.buy("more", 3.0, 60)
    coin.buy("late", 0.1, 400)
    other = Coin(rows, "OTHERcoin", "odev", S0 + 100)  # another coin's rows in the same blocks stay its own
    for k in range(4):
        other.buy(f"o-team-{k}", 4.0, 0)
    other.buy("late", 0.1, 400)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    good = _write(tmp_path / "a" / "pump_fun_2026-10-27_00.parquet", rows)
    mixed = list(rows)
    random.Random(7).shuffle(mixed)
    bad = tmp_path / "b" / "pump_fun_2026-10-27_00.parquet"
    pq.write_table(pa.Table.from_pylist(mixed), bad)  # neither in block order nor in order inside a block
    s1: dict = {}
    s2: dict = {}
    want = list(scan_files([good], min_block_sol=15, stats=s1))
    got = list(scan_files([bad], min_block_sol=15, stats=s2))
    assert {r["mint"]: r for r in got} == {r["mint"]: r for r in want} and len(want) == 2
    run = next(r for r in want if r["mint"] == "RUNcoin")
    assert run["path"][0][0] == 59 and run["path"][0][1] > 1.2  # one close for block 60: after all three of its trades
    assert s1["breaks"] == 0 and s2["breaks"] == 0 and s1["reordered"] == 0 and s2["reordered"] > 5
