"""A bot's rules, read off the tape (owner, 10 Nov: "You can literally see on chain exactly what he's doing"). For one
wallet, every coin it bought in the window: what its first buy came right after (a create, a buy in the same block, a
buy a block or two earlier), who that buyer was and how often the bot follows that buyer, how big it buys and what it
bids for the place, and how it sells (how soon, in how many pieces, at what gain). A batch job."""

import time

import pytest

from snipetech.analysis.playbook import playbook
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
BOT, KOL = addr("pb-bot"), addr("pb-kol")


def _copied(st, i, *, bot=True):
    m = Market(st, addr(f"pb-coin-{i}"), addr(f"pb-dev-{i}"), NOW - 3000 + i * 100, 80_000_000 + i * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("pb-someone"), 0.3, 3)
    m.buy(KOL, 2.0, 10)
    if bot:
        first = m.buy(BOT, 3.0, 10)  # the same block, right behind the KOL
        m.buy(addr("pb-crowd"), 4.0, 11)
        m.sell(BOT, 0.5, 15)  # half after 5 slots
        m.sell(BOT, 1.0, 30)  # the rest
        m.commit()
        return first
    m.commit()
    return None


def _sniped(st):
    m = Market(st, addr("pb-snipe"), addr("pb-dev-s"), NOW - 2000, 80_500_000)
    m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
    m.buy(BOT, 1.0, 0)  # in the create block
    m.buy(addr("pb-crowd"), 2.0, 2)
    m.sell(BOT, 1.0, 8)
    m.commit()


def _store():
    st = Store(":memory:")
    st.set_label(KOL, "Gake", "kol", "file")
    firsts = [_copied(st, i) for i in range(3)]
    _copied(st, 3, bot=False)  # a coin the KOL bought and the bot passed on
    _sniped(st)
    return st, firsts


def test_each_first_buy_is_classed_by_what_it_came_right_after():
    st, _ = _store()
    try:
        out = playbook(st, BOT, NOW, hours=2)
        assert out["wallet"] == BOT and out["coins"] == 4
        kinds = {r["kind"]: r for r in out["entries"]}
        assert set(kinds) == {"create block", "same block as a buy"}
        same, snipe = kinds["same block as a buy"], kinds["create block"]
        assert same["n"] == 3 and same["share"] == 0.75 and snipe["n"] == 1
        assert same["closed"] == 3 and same["win"] == 1.0 and same["net_sol"] > 0 and same["per_coin_sol"] > 0
        assert same["buy_sol"]["p50"] == 3.04 and same["sells"]["p50"] == 2  # what it paid, the 1.25 % fee included
        assert same["slots_to_first_sell"]["p50"] == 5 and same["slots_to_last_sell"]["p50"] == 20
        assert snipe["slots_to_first_sell"]["p50"] == 8 and snipe["buy_sol"]["p50"] == 1.01
    finally:
        st.close()


def test_the_wallets_it_follows_are_listed_with_how_often_it_follows_them():
    st, _ = _store()
    try:
        out = playbook(st, BOT, NOW, hours=2)
        lead = out["triggers"][0]
        assert lead["wallet"] == KOL and lead["label"] == "Gake" and lead["coins"] == 3
        assert lead["its_coins"] == 4 and lead["followed_share"] == 0.75  # it passed on one of the KOL's four coins
        assert lead["trades_behind"] == 1 and lead["buy_sol"] == pytest.approx(2.03, abs=0.011) and lead["net_sol"] > 0
        assert out["coverage"] == {"wallets": 1, "top10_share": 1.0, "top50_share": 1.0}
        assert all(r["wallet"] != addr("pb-dev-s") for r in out["triggers"])  # a create is not a wallet it copies
    finally:
        st.close()


def test_its_filters_sizes_and_exits_are_read_from_the_same_trades():
    st, _ = _store()
    try:
        out = playbook(st, BOT, NOW, hours=2)
        f = out["filters"]["same block as a buy"]
        assert f["trigger_sol"]["p5"] == pytest.approx(2.03, abs=0.011) and f["age_slots"]["p50"] == 10
        assert f["buyers_before"]["p50"] == 3 and f["trades_behind"]["p50"] == 1
        assert 2.7 < f["curve_sol"]["p50"] < 2.9  # 0.5 + 0.3 + 2.0 SOL in the curve when it bought
        assert out["sizing"]["common_sol"][0] == [3.04, 3] and out["sizing"]["vs_trigger"]["p50"] == 1.5
        ex = out["exits"]
        assert ex["sold_within_slots"] == {"5": 0.75, "15": 1.0, "40": 1.0, "150": 1.0, "750": 1.0}
        assert 0.45 < ex["first_sell_share"]["p25"] < 0.55 and ex["first_sell_share"]["p95"] == 1.0
        assert ex["gain_at_first_sell_pct"]["p50"] > 0 and ex["trigger_sold_first_share"] == 0.0
    finally:
        st.close()


def test_what_it_bids_for_its_place_comes_from_the_fee_records_the_tape_holds():
    st, firsts = _store()
    try:
        st._exec("INSERT INTO tx_meta (sig, slot, fee_payer, priority, tip, data) VALUES (?,?,?,?,?,?)",
                 (firsts[0].sig, firsts[0].slot, BOT, 30_000_000, 3_000_000, "{}"))  # fmt: skip
        out = playbook(st, BOT, NOW, hours=2)
        assert out["bids"]["known"] == 1 and out["bids"]["of"] == 4 and out["bids"]["sol"]["p50"] == 0.033
        same = next(r for r in out["entries"] if r["kind"] == "same block as a buy")
        assert same["bid_known"] == 1 and same["bid_sol"]["p50"] == 0.033
    finally:
        st.close()


def test_the_agents_may_start_it_and_the_cli_prints_json(tmp_path, capsys):
    import json

    from snipetech import cli
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    assert "playbook" in RESEARCH_COMMANDS and "playbook" in ALLOWED and "playbook" not in OWNER_COMMANDS
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_label(KOL, "Gake", "kol", "file")
        for i in range(3):
            _copied(st, i)
    finally:
        st.close()
    assert cli.main(["--db", str(db), "playbook", BOT, "--hours", "2", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["coins"] == 3 and out["triggers"][0]["wallet"] == KOL
    assert cli.main(["--db", str(db), "playbook", BOT, "--hours", "2"]) == 0
    assert "same block as a buy" in capsys.readouterr().out


def test_a_buy_right_after_the_migration_is_classed_as_one():
    """11 Nov: four bots buy 0.2-0.5 SOL in the first two blocks after one fast migration in ten. Other bots buy in
    those same blocks, so the playbook called every such buy "same block as a buy" and named whoever stood before it
    as the wallet it follows: a migration sniper read as a copy bot. A first buy on the pool within MIG_SLOTS blocks
    of the coin's migration is "after the migration", and nobody is its leader."""
    from snipetech.analysis.playbook import KINDS, MIG_SLOTS

    st = Store(":memory:")
    try:
        for i in range(3):
            m = Market(st, addr(f"pb-mig-{i}"), addr(f"pb-mdev-{i}"), NOW - 3000 + i * 100, 81_000_000 + i * 1000)
            m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
            m.complete(addr(f"pb-bundle-{i}"), 0)  # the launcher's set buys the curve out in the create block
            m.migrate(1)
            m.buy(addr("pb-other-bot"), 0.3, 2)  # another sniper, a block after the migration
            m.buy(BOT, 0.3, 2)  # ours, in the same block
            m.sell(BOT, 1.0, 12)
            m.commit()
        late = Market(st, addr("pb-mig-late"), addr("pb-mdev-late"), NOW - 2000, 81_900_000)
        late.buy(late.deployer, 1.0, 0, sig=late.create_sig)
        late.complete(addr("pb-bundle-late"), 0)
        late.migrate(1)
        late.buy(addr("pb-other-bot"), 0.3, 1 + MIG_SLOTS + 40)
        late.buy(BOT, 0.3, 1 + MIG_SLOTS + 40)  # long after the migration, right behind someone: a copy as before
        late.sell(BOT, 1.0, 1 + MIG_SLOTS + 60)
        late.commit()
        out = playbook(st, BOT, NOW, hours=2)
    finally:
        st.close()
    assert "after the migration" in KINDS
    by = {e["kind"]: e for e in out["entries"]}
    assert by["after the migration"]["n"] == 3 and by["same block as a buy"]["n"] == 1
    assert [t["wallet"] for t in out["triggers"]] == [addr("pb-other-bot")] and out["triggers"][0]["coins"] == 1
    assert out["filters"]["after the migration"]["slots_after_migration"]["p50"] == 1


def test_a_bots_bid_is_read_from_its_trade_row():
    """11 Nov: the tape kept no fee record with a trade, so a bot's bid was known only where a study had fetched its
    transaction: for most bots nowhere. The recorder writes the priority fee and the tip with each trade now; the
    playbook reads them there, and the old fee records where a row has none."""
    st = Store(":memory:")
    try:
        firsts = []
        for i in range(3):
            m = Market(st, addr(f"pb-bid-{i}"), addr(f"pb-bdev-{i}"), NOW - 3000 + i * 100, 82_000_000 + i * 1000)
            m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
            m.buy(KOL, 2.0, 10)
            first = m.buy(BOT, 3.0, 10)
            if i < 2:  # the recorder read this transaction's bid: 0.03 SOL of priority fee, 0.003 SOL to a relay
                first.priority, first.tip = 30_000_000, 3_000_000
            m.sell(BOT, 1.0, 30)
            m.commit()
            firsts.append(first)
        # the third buy's row holds no bid; a study fetched its transaction earlier
        st._exec("INSERT INTO tx_meta (sig, slot, fee_payer, priority, tip, data) VALUES (?,?,?,?,?,?)",
                 (firsts[2].sig, firsts[2].slot, BOT, 10_000_000, 1_000_000, "{}"))  # fmt: skip
        out = playbook(st, BOT, NOW, hours=2)
    finally:
        st.close()
    assert out["bids"]["known"] == 3 and out["bids"]["of"] == 3
    assert out["bids"]["sol"]["p50"] == 0.033 and out["bids"]["sol"]["p5"] == 0.011
