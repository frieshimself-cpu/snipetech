"""How the winners win (owner, 9 Nov 20:40: "people are making money doing this, there's a way"): every wallet's closed
round trips on the tape, ranked by realized SOL, with the style that made it (how soon after the create it buys, how long
it holds, in how many pieces it sells); and, for one wallet such as 6Z91, the wallets it buys right after (its leaders).
A batch job: the server agents' SQL stops at 30 s."""

import time

from snipetech.analysis.winners import leaders_of, winners
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())


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


def test_tokens_sold_beyond_what_the_wallet_bought_are_not_counted_as_profit():
    """9 Nov 23:00: the top 'winner' (+9,009 SOL in 3 days, 92 % wins, 36 sells a coin) sold far more tokens than it
    bought: supply handed to it by a creator team. Only the part of the sales its own buys cover is a trading result."""
    from types import SimpleNamespace as N

    launch = N(mint="M", user="dev", creator="dev", slot=100, ts=NOW - 600)
    trades = [N(user="dumper", venue="curve", is_buy=True, quote=1_000_000_000, fee=0, base=1_000, slot=105, ts=NOW - 590),
              N(user="dumper", venue="curve", is_buy=False, quote=3_000_000_000, fee=0, base=10_000, slot=110, ts=NOW - 580)]  # fmt: skip
    store = N(launches_between=lambda a, b: [launch], trades_for_mint=lambda m, *a: trades)
    r = winners(store, NOW, hours=1, min_trips=1)[0]
    assert r["wallet"] == "dumper" and abs(r["pnl_sol"] - (-0.7)) < 1e-9 and r["oversold_share"] == 1.0


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
    from types import SimpleNamespace as N

    launch = N(mint="M", user="dev", creator="dev", slot=100, ts=NOW - 600)
    trades = [N(user="swing", venue="curve", is_buy=True, quote=1_000_000_000, fee=0, base=1_000, slot=105, ts=NOW - 590),
              N(user="swing", venue="amm", is_buy=False, quote=2_000_000_000, fee=0, base=1_000, slot=900, ts=NOW - 300),
              N(user="flip", venue="curve", is_buy=True, quote=1_000_000_000, fee=0, base=1_000, slot=106, ts=NOW - 589),
              N(user="flip", venue="curve", is_buy=False, quote=1_500_000_000, fee=0, base=1_000, slot=110, ts=NOW - 580)]  # fmt: skip
    store = N(launches_between=lambda a, b: [launch], trades_for_mint=lambda m, *a: trades)
    by = {r["wallet"]: r for r in winners(store, NOW, hours=1, min_trips=1)}
    assert by["swing"]["pumpswap_share"] == 1.0 and by["flip"]["pumpswap_share"] == 0.0
