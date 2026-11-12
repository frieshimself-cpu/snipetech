"""How the winners win (owner, 9 Nov 20:40: "people are making money doing this, there's a way"): every wallet's closed
round trips on the tape, ranked by realized SOL, with the style that made it (how soon after the create it buys, how long
it holds, in how many pieces it sells); and, for one wallet such as 6Z91, the wallets it buys right after (its leaders).
A batch job: the server agents' SQL stops at 30 s."""

import time
from itertools import pairwise

from snipetech.analysis.winners import leaders_of, winners, winners_report
from snipetech.models import Trade
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
_SEQ = [0]


def _coin(st, i, *, winner_slot=2, loser=True):
    m = Market(st, addr(f"wn-coin-{i}"), addr(f"wn-dev-{i}"), NOW - 3000 + i * 100, 60_000_000 + i * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(addr("leader"), 2.0, winner_slot - 1)  # the wallet the follower copies, one slot ahead of it
    m.buy(addr("winner"), 1.0, winner_slot)
    m.buy(addr("follower"), 0.5, winner_slot + 1)
    m.buy(addr("crowd"), 6.0, winner_slot + 5)
    m.sell(addr("winner"), 0.5, winner_slot + 8)
    m.sell(addr("winner"), 1.0, winner_slot + 12)  # two pieces
    if loser:
        m.buy(addr("loser"), 1.0, winner_slot + 13)
        m.sell(addr("crowd"), 1.0, winner_slot + 14)
        m.sell(addr("loser"), 1.0, winner_slot + 15)
    m.commit()
    return m


def test_the_winners_are_ranked_by_realized_sol_with_their_style():
    st = Store(":memory:")
    try:
        for i in range(3):
            _coin(st, i)
        rows = winners(st, NOW, hours=2, min_trips=2, top=10)
        by = {r["wallet"]: r for r in rows}
        w = by[addr("winner")]
        assert rows[0]["wallet"] == addr("winner") and w["trips"] == 3 and w["pnl_sol"] > 0 and w["win_rate"] == 1.0
        assert w["median_entry_slots"] == 2 and w["create_slot_share"] == 0.0 and w["median_sells"] == 2
        assert w["median_hold_secs"] > 0 and w["median_buy_sol"] > 0.9
        assert by[addr("loser")]["pnl_sol"] < 0 and rows[-1]["pnl_sol"] <= w["pnl_sol"]
        assert addr("wn-dev-0") not in by  # a coin's creator is not a trader of it
    finally:
        st.close()


def test_a_wallets_leaders_are_the_wallets_it_buys_right_after_again_and_again():
    st = Store(":memory:")
    try:
        for i in range(3):
            _coin(st, i, loser=False)
        rows = leaders_of(st, addr("follower"), NOW, hours=2, max_slots=5)
        assert rows[0]["wallet"] == addr("winner") or rows[0]["wallet"] == addr("leader")
        lead = next(r for r in rows if r["wallet"] == addr("leader"))
        assert lead["coins"] == 3 and lead["median_slots_behind"] == 2 and lead["median_price_gap_pct"] > 0
        assert all(r["wallet"] != addr(f"wn-dev-{i}") for r in rows for i in range(3))
    finally:
        st.close()


def test_the_agents_may_start_both_and_the_cli_prints_json(tmp_path, capsys):
    import json

    from snipetech import cli
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    for c in ("winners", "leaders-of"):
        assert c in RESEARCH_COMMANDS and c in ALLOWED and c not in OWNER_COMMANDS
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        for i in range(3):
            _coin(st, i)
    finally:
        st.close()
    assert cli.main(["--db", str(db), "winners", "--hours", "2", "--min-trips", "2", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["wallets"][0]["wallet"] == addr("winner") and out["wallets"][0]["style"]
    assert cli.main(["--db", str(db), "leaders-of", addr("follower"), "--hours", "2", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["wallet"] == addr("follower") and {r["wallet"] for r in out["leaders"]} >= {addr("leader")}


def test_leaders_of_can_look_only_at_the_wallets_later_buys():
    """6Z91 buys ~480 coins a day, most in a coin's first slots beside other snipers: its copying shows only in its
    buys well after the create, so `after_slots` keeps those alone."""
    st = Store(":memory:")
    try:
        for i in range(3):
            _coin(st, i, loser=False)  # follower buys 3 slots after the create
        assert leaders_of(st, addr("follower"), NOW, hours=2, max_slots=5, after_slots=10) == []
        assert leaders_of(st, addr("follower"), NOW, hours=2, max_slots=5, after_slots=3)
    finally:
        st.close()


def _row(user, is_buy, sol, base, slot, secs_ago, venue="curve", mint="M", sig=None):
    """One trade written as the recorder writes it, with no venue fee (the numbers are then easy to check by hand)."""
    _SEQ[0] += 1
    return Trade(sig=sig or addr(f"wn-row-{_SEQ[0]}"), ev=0, slot=slot, pos=_SEQ[0], ts=NOW - secs_ago, mint=addr(mint),
                 venue=venue, user=addr(user), is_buy=is_buy, quote=int(sol * 1e9), base=base, fee=0, fee_bps=0,
                 q_after=30_000_000_000, b_after=10**15)  # fmt: skip


def test_tokens_sold_beyond_what_the_wallet_bought_are_not_counted_as_profit():
    """9 Nov 23:00: the top 'winner' (+9,009 SOL in 3 days, 92 % wins, 36 sells a coin) sold far more tokens than it
    bought: supply handed to it by a creator team. Only the part of the sales its own buys cover is a trading result."""
    st = Store(":memory:")
    try:
        st.add_trades([_row("dumper", True, 1.0, 1_000, 105, 590), _row("dumper", False, 3.0, 10_000, 110, 580)])
        r = winners(st, NOW, hours=1, min_trips=1)[0]
        assert r["wallet"] == addr("dumper") and abs(r["pnl_sol"] - (-0.7)) < 1e-9 and r["oversold_share"] == 1.0
        assert r["style"].startswith("insider")
    finally:
        st.close()


def test_a_wallet_that_mostly_sells_coins_it_never_bought_on_the_tape_is_an_insider():
    """The same team wallet seen the other way: it buys a little once, sells that, and goes on selling coins it never
    bought on the tape. Sales with no buy behind them are never a result; their share marks the wallet."""
    st = Store(":memory:")
    try:
        rows = [_row("team", True, 1.0, 1_000, 105, 590), _row("team", False, 1.5, 1_000, 110, 580)]
        rows += [_row("team", False, 5.0, 50_000, 120 + k, 500 - k, mint=f"handed-{k}") for k in range(3)]
        st.add_trades(rows)
        r = winners(st, NOW, hours=1, min_trips=1)[0]
        assert r["trips"] == 1 and abs(r["pnl_sol"] - 0.5) < 1e-9  # the 15 SOL it was handed are not a trading result
        assert r["unbought_sell_share"] == 0.75 and r["style"].startswith("insider")
    finally:
        st.close()


def test_label_sends_the_best_mid_and_late_traders_to_qualify(tmp_path, capsys):
    """I46: the winners study's genuine mid/late traders (no create-slot sniping, no insider sales) go to qualify's
    queue as cand, first in line like the other screens' picks."""
    import json

    from snipetech import cli
    from snipetech.store import _PRIORITY_SOURCES

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        for i in range(3):
            _coin(st, i, winner_slot=30)
    finally:
        st.close()
    assert cli.main(["--db", str(db), "winners", "--hours", "2", "--min-trips", "2", "--label", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["labelled"] == [addr("winner")]
    st = Store(db)
    try:
        assert addr("winner") in st.labels("cand")
        assert [c["wallet"] for c in st.candidates() if "winners" in c["sources"]] == [addr("winner")]
    finally:
        st.close()
    assert "winners" in _PRIORITY_SOURCES


def test_a_round_trip_sold_on_pumpswap_is_counted_as_one():
    """11 Nov: the tape names a PumpSwap trade's venue "amm" and the study asked for "pumpswap", so `pumpswap_share`
    read 0 for every wallet since the study was written, also for the swing traders who sell after the migration."""
    st = Store(":memory:")
    try:
        st.add_trades([_row("swing", True, 1.0, 1_000, 105, 590), _row("swing", False, 2.0, 1_000, 900, 300, venue="amm"),
                       _row("flip", True, 1.0, 1_000, 106, 589), _row("flip", False, 1.5, 1_000, 110, 580)])  # fmt: skip
        by = {r["wallet"]: r for r in winners(st, NOW, hours=1, min_trips=1)}
        assert by[addr("swing")]["pumpswap_share"] == 1.0 and by[addr("flip")]["pumpswap_share"] == 0.0
    finally:
        st.close()


# ---- finding the winning bots and big wallets by itself (owner, 12 Nov: "you need to find these people, me having to
# give you his wallet is insane") -------------------------------------------------------------------------------------

KOL, COPYBOT, SNIPER, SLOW, BAG, OURS, ROUTER = (
    addr(f"wn-{n}") for n in ("kol", "copybot", "sniper", "slow", "bag", "ours", "router")
)
LABELS = {KOL: ("kol", "Frank"), OURS: ("own", "our sniper"), ROUTER: ("service", "a router")}
COINS = 6


def _bid(t, lamports=3_000_000):
    """The recorder read this transaction's fee record: two thirds priority fee, one third tip."""
    t.priority, t.tip = lamports * 2 // 3, lamports // 3
    return t


def _bot_coin(st, i):
    """One coin of the market the finder is tried on. A launch sniper buys a second after the create and is dumped on
    by the creator: it loses. A known wallet (a KOL) buys 2 SOL; a copy bot buys right behind it in the same block and
    sells a second later into the crowd: it wins. Our own wallet and a router do the same as the copy bot. A slow
    trader buys two of the coins a minute in and sells ten minutes later, higher. A bag holder sells half at a price
    that pays back more than the whole buy and keeps the rest: an open position. Returns the copy bot's and the
    sniper's result in the coin, in SOL, after the venue's fee and what their transactions paid to land."""
    m = Market(st, addr(f"wn-bot-coin-{i}"), addr(f"wn-bot-dev-{i}"), NOW - 3000 + i * 400, 70_000_000 + i * 10_000)
    m.buy(m.deployer, 3.0, 0, sig=m.create_sig)
    s_buy = _bid(m.buy(SNIPER, 0.5, 3))  # one second after the create
    m.sell(m.deployer, 1.0, 4)
    s_sell = _bid(m.sell(SNIPER, 1.0, 6))
    m.buy(KOL, 2.0, 20)
    c_buy = _bid(m.buy(COPYBOT, 0.5, 20))  # the KOL's own block, right behind it
    for who in (OURS, ROUTER):
        m.buy(who, 0.5, 20)
    m.buy(BAG, 0.3, 21)
    m.buy(addr(f"wn-crowd-{i}"), 25.0, 22)
    c_sell = _bid(m.sell(COPYBOT, 1.0, 24))
    for who in (OURS, ROUTER):
        m.sell(who, 1.0, 24)
    m.sell(BAG, 0.5, 25)  # half out for more than the whole buy cost; the other half is still held
    m.sell(KOL, 1.0, 26)
    if i < 2:
        m.buy(SLOW, 1.0, 150)  # a minute after the create, in a block of its own
        m.buy(addr(f"wn-late-{i}"), 10.0, 600)
        m.sell(SLOW, 1.0, 1650)  # ten minutes later
    m.commit()

    def net(b, s):
        return (s.quote - s.fee - b.quote - b.fee - b.priority - b.tip - s.priority - s.tip) / 1e9

    return net(c_buy, c_sell), net(s_buy, s_sell)


def _bot_market(st):
    made = [_bot_coin(st, i) for i in range(COINS)]
    return sum(c for c, _ in made), sum(s for _, s in made)


def test_the_finder_ranks_wallets_by_what_they_made_and_tells_bots_from_traders():
    st = Store(":memory:")
    try:
        copy_sol, sniper_sol = _bot_market(st)
        out = winners_report(st, NOW, hours=2, min_trips=2, labels=LABELS)
        rows = out["wallets"]
        by = {r["wallet"]: r for r in rows}
        days = out["days"]
        # the part of the window the tape read covers (first trade to last), not the two hours asked for
        assert 2000 / 86400 <= days <= 2100 / 86400

        c = by[COPYBOT]
        assert c["kind"] == "bot" and c["trips"] == COINS and c["win_rate"] == 1.0
        assert abs(c["pnl_sol"] - copy_sol) < 1e-4 and copy_sol > 0  # after the venue's fee and its landing fees
        assert abs(c["fees_sol"] - COINS * 2 * 0.003) < 1e-6 and c["fee_known_share"] == 1.0
        assert abs(c["trips_per_day"] * days / COINS - 1) < 0.01 and abs(c["sol_per_day"] * days / copy_sol - 1) < 0.01
        assert c["copy_share"] == 1.0 and c["snipe_share"] == 0.0 and c["median_hold_secs"] <= 2
        assert abs(c["median_buy_sol"] - 0.5 * 1.0125) < 1e-4
        assert 0 < c["lower_sol"] <= c["mean_sol"] and abs(c["mean_sol"] - copy_sol / COINS) < 1e-5
        assert "behind a known wallet" in c["why"]

        s = by[SNIPER]
        assert s["kind"] == "bot" and s["snipe_share"] == 1.0 and s["copy_share"] == 0.0 and s["win_rate"] == 0.0
        assert abs(s["pnl_sol"] - sniper_sol) < 1e-4 and sniper_sol < 0 and s["lower_sol"] < 0

        t = by[SLOW]
        assert t["kind"] == "trader" and t["trips"] == 2 and t["pnl_sol"] > 0 and t["median_hold_secs"] >= 590
        assert t["copy_share"] == 0.0 and t["snipe_share"] == 0.0 and t["fee_known_share"] == 0.0

        # best first by what they made; the loser comes after both winners
        order = [r["wallet"] for r in rows]
        assert order.index(SLOW) < order.index(SNIPER) and order.index(COPYBOT) < order.index(SNIPER)
        assert all(a["pnl_sol"] >= b["pnl_sol"] for a, b in pairwise(rows))
        # an open position is no result: the bag holder took out more than it put in and still has no round trip
        assert BAG not in by
        # our own wallet and a router are left out; a known wallet is ranked like any other, under its name
        assert OURS not in by and ROUTER not in by
        assert by[KOL]["name"] == "Frank" and by[KOL]["label_kind"] == "kol" and c["name"] == ""
        assert out["note"].startswith("Each wallet's own trades")
    finally:
        st.close()


def test_an_open_position_does_not_add_to_a_winners_result():
    st = Store(":memory:")
    try:
        copy_sol, _ = _bot_market(st)
        m = Market(st, addr("wn-bot-coin-open"), addr("wn-bot-dev-open"), NOW - 500, 70_900_000)
        m.buy(m.deployer, 1.0, 0, sig=m.create_sig)
        buy = m.buy(COPYBOT, 0.5, 30)
        m.buy(addr("wn-crowd-open"), 30.0, 31)
        half = m.sell(COPYBOT, 0.5, 32)  # half out at three times the price: more SOL back than the whole buy cost
        m.commit()
        assert half.quote - half.fee > buy.quote + buy.fee
        c = next(r for r in winners(st, NOW, hours=2, min_trips=2, labels=LABELS) if r["wallet"] == COPYBOT)
        assert c["trips"] == COINS and abs(c["pnl_sol"] - copy_sol) < 1e-4
        assert c["open_positions"] == 1 and c["open_sol"] > 0  # shown apart: what it took out of what is still open
    finally:
        st.close()


def test_a_second_buy_after_selling_out_is_a_second_round_trip():
    """A bot goes in and out of one coin many times a day. Each time it is back to nothing is one round trip."""
    st = Store(":memory:")
    try:
        st.add_trades([_row("re", True, 1.0, 1_000, 100, 900), _row("re", False, 1.2, 1_000, 101, 899),
                       _row("re", True, 1.0, 2_000, 200, 800), _row("re", False, 0.7, 1_900, 201, 799),
                       _row("re", False, 0.1, 100, 300, 700)])  # fmt: skip
        r = winners(st, NOW, hours=1, min_trips=2)[0]
        assert r["trips"] == 2 and abs(r["pnl_sol"] - 0.0) < 1e-9 and r["win_rate"] == 0.5 and r["median_sells"] == 1.5
    finally:
        st.close()


def test_a_transaction_with_two_trades_pays_its_landing_fee_once():
    st = Store(":memory:")
    try:
        both = addr("wn-one-tx")
        a, b = _row("arb", True, 1.0, 1_000, 100, 900, sig=both), _row("arb", False, 1.5, 1_000, 100, 900, sig=both)
        b.ev, b.pos = 1, a.pos
        for t in (a, b):
            _bid(t, 30_000_000)
        st.add_trades([a, b])
        r = winners(st, NOW, hours=1, min_trips=1)[0]
        assert abs(r["pnl_sol"] - (0.5 - 0.03)) < 1e-9 and abs(r["fees_sol"] - 0.03) < 1e-9
    finally:
        st.close()


def test_the_filters_keep_the_wallets_that_make_enough_a_day():
    st = Store(":memory:")
    try:
        _bot_market(st)
        base = winners_report(st, NOW, hours=2, min_trips=2, labels=LABELS)
        c = next(r for r in base["wallets"] if r["wallet"] == COPYBOT)
        rich = winners(st, NOW, hours=2, min_trips=2, labels=LABELS, min_sol_per_day=c["sol_per_day"] - 0.01)
        assert COPYBOT in {r["wallet"] for r in rich} and all(r["sol_per_day"] >= c["sol_per_day"] - 0.01 for r in rich)
        assert SNIPER not in {r["wallet"] for r in rich}
        assert SLOW not in {r["wallet"] for r in winners(st, NOW, hours=2, min_trips=3, labels=LABELS)}
        assert len(winners(st, NOW, hours=2, min_trips=2, labels=LABELS, top=1)) == 1
        bots = winners(st, NOW, hours=2, min_trips=2, labels=LABELS, kind="bot")
        assert bots and all(r["kind"] == "bot" for r in bots)
        assert base["judged"] >= len(base["wallets"]) >= 3 and base["trades_read"] > 0
    finally:
        st.close()


def test_the_tape_is_read_twice_by_time_and_never_once_a_wallet_or_a_coin():
    """Cheap by construction: a count of every wallet's trades, then one read of the window, both in ten-minute
    pieces off the time index. The study it replaces asked once for every coin launched in the window (42,000 a day
    on 12 Nov) and the hourly discovery once for each of the 2,000 busiest wallets."""
    st = Store(":memory:")
    try:
        _bot_market(st)
        asked = []
        real = st._q

        def spy(sql, params=()):
            asked.append(" ".join(sql.split()))
            return real(sql, params)

        st._q = spy
        winners(st, NOW, hours=2, min_trips=2, labels=LABELS)
        on_trades = [q for q in asked if " FROM trades" in q]
        pieces = 2 * 3600 // Store.READ_SPAN_SECS + 1
        assert 0 < len(on_trades) <= 2 * pieces
        assert all("user=?" not in q and "mint=?" not in q and "ts>=?" in q for q in on_trades)
    finally:
        st.close()


def test_the_command_prints_a_table_and_labels_the_best_for_qualify(tmp_path, capsys):
    import json

    from snipetech import cli

    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        _bot_market(st)
        for a, (kind, name) in LABELS.items():
            st.set_label(a, name, kind)
    finally:
        st.close()
    argv = ["--db", str(db), "winners", "--hours", "2", "--min-trips", "2"]
    assert cli.main(argv) == 0
    text = capsys.readouterr().out
    assert COPYBOT in text and "bot" in text and "trader" in text and OURS not in text
    assert "own place in the block" in text  # a plain table says at which landing its numbers hold
    assert cli.main([*argv, "--label", "2", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["wallets"][0]["kind"] in ("bot", "trader") and out["min_trips"] == 2
    labelled = out["labelled"]
    assert len(labelled) == 2 and COPYBOT in labelled and SNIPER not in labelled and KOL not in labelled
    st = Store(db)
    try:
        cand = st.labels("cand")
        assert set(labelled) == set(cand) and cand[COPYBOT].startswith("winner bot")
        assert st.labels("kol") == {KOL: "Frank"}  # a wallet that has a name keeps it
        assert {c["wallet"] for c in st.candidates() if "winners" in c["sources"]} == set(labelled)
    finally:
        st.close()
    assert cli.build_parser().parse_args(["winners"]).min_trips == 20
    assert cli.build_parser().parse_args(["winners"]).hours == 72


def test_discovery_asks_the_finder_every_six_hours_and_queues_what_it_finds(tmp_path):
    """Nobody has to start it: the wallet discovery the server runs by itself asks the finder every six hours, and
    the wallets that made SOL with a lower bound above zero wait first in qualify's queue."""
    from snipetech.config import Config
    from snipetech.discovery.run import DiscoverContext, run_discover
    from snipetech.discovery.sources import WinnersSource, default_sources
    from snipetech.operator.research import DISCOVER_SOURCES, SOURCE_NAMES
    from snipetech.service import Service

    st = Store(tmp_path / "t.db")
    try:
        _bot_market(st)
        for a, (kind, name) in LABELS.items():
            st.set_label(a, name, kind)
        svc = Service(Config(), store=st)
        ctx = DiscoverContext(svc=svc, cfg=svc.cfg, now=NOW, log=lambda m: None)
        src = WinnersSource(hours=2, min_trips=2)
        assert src.name == "winners" and src.every_secs == 6 * 3600
        found = {r["wallet"]: r["stats"] for r in src.fetch(ctx)}
        assert {COPYBOT, SLOW} <= set(found) and found[COPYBOT]["kind"] == "bot" and found[SLOW]["kind"] == "trader"
        assert not {SNIPER, OURS, ROUTER, BAG} & set(found)  # a loser, ours, a service, a wallet with no round trip
        assert all(s["lower_sol"] > 0 for s in found.values())
        out = run_discover(ctx, [src])
        assert out["by_source"] == {"winners": len(found)} and out["errors"] == {} and out["banned"] == 0
        assert {c["wallet"] for c in st.candidates() if "winners" in c["sources"]} == set(found)
        defaults = default_sources(ctx)
        assert [s.name for s in defaults][:2] == ["tape", "winners"]
        ours = next(s for s in defaults if s.name == "winners")
        assert (ours.hours, ours.min_trips) == (72.0, 20)
        assert "winners" in DISCOVER_SOURCES and SOURCE_NAMES["winners"] == "winners on our tape"
    finally:
        st.close()


def test_discover_run_takes_the_finder_by_name():
    from snipetech.cli import _discover_sources
    from snipetech.config import Config
    from snipetech.operator.research import DISCOVER_SOURCES

    table = _discover_sources(Config())
    assert set(DISCOVER_SOURCES) == set(table)  # the idle picker never names a source the command lacks
    assert table["winners"]().name == "winners"


def test_the_agents_may_start_the_finder_and_no_deploy_runs_under_it():
    from snipetech.cli import BATCH_COMMANDS
    from snipetech.operator.jobs import RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED, HOLDS_DEPLOY

    assert "winners" in RESEARCH_COMMANDS and "winners" in ALLOWED and "winners" in BATCH_COMMANDS
    assert "winners" in HOLDS_DEPLOY  # it reports numbers: the server refuses a code update while it runs
