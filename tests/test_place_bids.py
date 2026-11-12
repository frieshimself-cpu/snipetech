"""Which bid lands first behind a followed wallet (audit open item 19; the agents' idea I51).

The trade that pays on paper is the first place behind a known wallet and its companions; nothing pays one place
further back. What decides whether it can be ours is the one number nobody had: which bid, or which speed, takes that
place. The tape holds every trade's priority fee and tip since 11 Nov 16:34 UTC. `place-bids` reads, for every moment
a wallet rule follows, the buyers behind the followed wallet in its own block (its companions left out, as the lab
leaves them out), each one's place, size and bid, and says whether the order follows the bids."""

import json
import time

import pytest

from snipetech.analysis import place_bids as PB
from snipetech.analysis.lab import collect, parse_trigger
from snipetech.config import Config
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
LEAD1, LEAD2, LEAD3 = addr("pbid-lead-1"), addr("pbid-lead-2"), addr("pbid-lead-3")
MATE = addr("pbid-mate")
BOT_A, BOT_B, BOT_C = addr("pbid-bot-a"), addr("pbid-bot-b"), addr("pbid-bot-c")
FAST, RICH = addr("pbid-fast"), addr("pbid-rich")
OURS = addr("pbid-ours")
RULE = {"kind": "kol", "group": "wallet", "sol": 1}
_N = [0]


def _bid(t, sol):
    """The recorder read this transaction's fee record: two thirds priority fee, one third tip."""
    t.priority, t.tip = round(sol * 1e9 * 2 / 3), round(sol * 1e9 / 3)
    return t


def _coin(st, leader, block=(), later=(), lead_bid=0.002, mate=False, known=True, lead_sol=2.0, before=(), at=None):
    """A coin `leader` buys at slot +50. `block`: (wallet, SOL, bid) buys behind it in its own block, in order, twenty
    transactions further down the block; `later`: (wallet, SOL, bid, slots later). `mate`: its companion buys in the
    very next transaction. `known`: the tape has the block's bids. `before`: known wallets that bought earlier.
    `at`: when the coin was created (default: early in the tape, a coin every 20 s)."""
    _N[0] += 1
    k = _N[0]
    m = Market(
        st, addr(f"pbid-coin-{k}"), addr(f"pbid-dev-{k}"), at or NOW - 6000 + (k % 150) * 20, 90_000_000 + k * 1000
    )
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(4):
        m.buy(addr(f"pbid-crowd-{k}-{j}"), 0.2, 2 + j)
    for j, who in enumerate(before):
        t = m.buy(who, 1.5, 20 + j)
        if known:
            _bid(t, lead_bid)
    lead = m.buy(leader, lead_sol, 50)
    lead.pos = 100
    if known:
        _bid(lead, lead_bid)
    if mate:
        t = m.buy(MATE, 1.0, 50)
        t.pos = 101
        if known:
            _bid(t, lead_bid)
    for j, (who, sol, bid) in enumerate(block):
        t = m.buy(who, sol, 50)
        t.pos = 120 + j
        if known and bid is not None:
            _bid(t, bid)
    for who, sol, bid, slots in later:
        t = m.buy(who, sol, 50 + slots)
        if known and bid is not None:
            _bid(t, bid)
    m.sell(addr(f"pbid-crowd-{k}-0"), 1.0, 300)
    m.commit()
    return m


def _store(path=":memory:"):
    st = Store(path)
    for w, name in ((LEAD1, "tracked one"), (LEAD2, "tracked two"), (LEAD3, "tracked three")):
        st.set_label(w, name, "wallet", "file")
    st.set_coverage("tape", {"spans": [[NOW - 7000, NOW]]})
    return st


AUCTION = ((BOT_A, 3.0, 0.03), (BOT_B, 1.0, 0.005), (BOT_C, 0.5, 0.001))
SPEED = ((FAST, 2.0, 0.0005), (RICH, 2.0, 0.05))


def _win(out):
    return {r["bid_sol"]: (r["above_first"], r["above_all"]) for r in out["win_at"]}


def test_the_bid_of_each_place_behind_a_followed_wallet():
    st = _store()
    try:
        for _ in range(8):
            _coin(st, LEAD1, block=AUCTION)
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    assert out["moments"] == 8 and out["scored"] == 8 and out["coins"] == 8 and out["wallets"] == 1
    assert out["rule"] == parse_trigger(RULE).label() and out["hours"] == 2
    all_ = out["overall"]
    assert all_["moments"] == 8 and all_["in_block"] == {"n": 8, "share": 1.0}
    assert all_["leader_bid_sol"]["p50"] == 0.002
    first, second, third = all_["places"]
    assert (first["place"], first["n"], first["bid_known"]) == (1, 8, 8)
    assert first["bid_sol"]["p50"] == 0.03 and first["under"] == {"0.001": 0.0, "0.003": 0.0, "0.01": 0.0}
    assert first["buy_sol"]["p50"] == pytest.approx(3.0375, abs=0.006)  # what it paid, fee included
    assert first["gap_pct"]["p50"] > 0  # the price it paid over the followed wallet's
    assert first["adjacent"] == 0.0  # twenty transactions down the block: not sent with the wallet's own
    assert second["bid_sol"]["p50"] == 0.005 and second["under"] == {"0.001": 0.0, "0.003": 0.0, "0.01": 1.0}
    assert third["bid_sol"]["p50"] == 0.001 and third["under"]["0.003"] == 1.0 and third["under"]["0.001"] == 0.0
    # the order follows the bids: every block's highest bidder is first, every pair in the order of its bids
    assert all_["auction"] == {"blocks": 8, "highest_bid_first": 1.0, "pairs": 24, "in_bid_order": 1.0}
    win = _win(all_)
    assert win[0.001] == (0.0, 0.0) and win[0.01] == (0.0, 0.0) and win[0.05] == (1.0, 1.0)
    assert win[0.03] == (0.0, 0.0)  # an equal bid is not a higher one
    assert all_["first_buyer"] == {"same_block": 1.0, "next_block": 0.0, "2-4_blocks": 0.0, "nobody": 0.0}


def test_a_place_that_speed_takes_shows_as_bids_out_of_order():
    """A wallet's companion (the wallet its operator buys with in the next transaction) is no place a copy takes: it is
    left out, as the lab leaves it out. Behind it a low bid lands ahead of a high one in every block: the place is not
    the bid's. The table says so in two numbers and names who holds the place."""
    st = _store()
    try:
        for _ in range(6):
            _coin(st, LEAD2, block=SPEED, mate=True)
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    all_ = out["overall"]
    first, second = all_["places"]
    assert first["n"] == 6 and first["bid_sol"]["p50"] == 0.0005 and first["under"]["0.003"] == 1.0
    assert second["bid_sol"]["p50"] == 0.05
    assert all_["auction"] == {"blocks": 6, "highest_bid_first": 0.0, "pairs": 6, "in_bid_order": 0.0}
    win = _win(all_)
    assert win[0.001] == (1.0, 0.0) and win[0.03] == (1.0, 0.0) and win[0.1] == (1.0, 1.0)
    holder = out["first_place_holders"][0]
    assert holder["wallet"] == FAST and holder["firsts"] == 6 and holder["share"] == 1.0
    assert holder["bid_sol"] == 0.0005 and holder["behind"] == 1 and holder["label"] is None
    assert MATE not in {h["wallet"] for h in out["first_place_holders"]}
    row = out["by_wallet"][0]
    assert row["wallet"] == LEAD2 and row["label"] == "tracked two" and row["moments"] == 6 and row["mates"] == 1
    assert row["in_block_share"] == 1.0 and row["first"] == {"n": 6, "bid_sol": 0.0005, "under_0.003": 1.0,
                                                             "adjacent": 0.0}  # fmt: skip
    regulars = {r["wallet"]: r for r in row["regulars"]}
    assert set(regulars) == {FAST, RICH} and regulars[FAST]["share"] == 1.0 and regulars[FAST]["place"] == 1
    assert regulars[RICH]["place"] == 2 and regulars[RICH]["bid_sol"] == 0.05


def test_an_empty_block_is_won_by_being_there():
    """Nobody bought behind the wallet in its block: no bid is needed for the first place, only a transaction that is
    there in time. Where the first outsider came later is said in blocks."""
    st = _store()
    try:
        for _ in range(4):
            _coin(st, LEAD3, later=((BOT_A, 1.0, 0.01, 2),))
        _coin(st, LEAD3, later=((BOT_A, 1.0, 0.01, 30),))
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    all_ = out["overall"]
    assert out["scored"] == 5 and all_["in_block"] == {"n": 0, "share": 0.0} and all_["places"] == []
    assert all_["auction"] is None
    assert all_["first_buyer"] == {"same_block": 0.0, "next_block": 0.0, "2-4_blocks": 0.8, "nobody": 0.2}
    assert set(_win(all_).values()) == {(1.0, 1.0)}
    assert out["first_place_holders"] == []


def test_our_own_copies_are_placed_among_the_others_and_left_out_of_their_table():
    """Where our 0.2 SOL copies landed, at the bid they paid, next to what the others paid for the places ahead. Our
    own buy is not a buyer ahead of us: the others' table is the same with and without it."""
    cfg = Config()
    cfg.operator.wallet = OURS
    st = _store()
    try:
        for _ in range(5):
            _coin(st, LEAD1, block=(AUCTION[0], AUCTION[1], (OURS, 0.2, 0.003), AUCTION[2]))
        for _ in range(3):
            _coin(st, LEAD1, block=AUCTION, later=((OURS, 0.2, 0.003, 1),))
        out = PB.place_bids(st, cfg, NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    third = out["overall"]["places"][2]
    assert third["n"] == 8 and third["bid_sol"]["p50"] == 0.001  # the third outsider, never our own buy
    ours = out["ours"]
    assert ours["moments"] == 8 and ours["same_block"] == 5 and ours["bid_sol"]["p50"] == 0.003
    assert ours["place"]["p50"] == 3 and ours["place"]["p95"] == 4  # two outsiders ahead of us, or all three
    assert ours["buy_sol"]["p50"] == 0.2
    assert OURS not in {h["wallet"] for h in out["first_place_holders"]}


def test_moments_the_tape_has_no_bids_for_are_counted_and_not_scored():
    """The recorder writes a trade's fee record since 11 Nov 16:34 UTC: a moment before that has a place and no bid."""
    st = _store()
    try:
        for _ in range(3):
            _coin(st, LEAD1, block=AUCTION)
        for _ in range(2):
            _coin(st, LEAD1, block=AUCTION, known=False)
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    assert out["moments"] == 5 and out["scored"] == 3 and out["bids"]["unknown_moments"] == 2
    assert out["overall"]["places"][0]["n"] == 3
    assert out["bids"]["from_ts"] is not None and out["bids"]["from_ts"] <= NOW


def test_a_follower_whose_bid_the_tape_lacks_is_not_guessed():
    st = _store()
    try:
        for _ in range(3):
            _coin(st, LEAD1, block=((BOT_A, 3.0, None), (BOT_B, 1.0, 0.005)))
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    first = out["overall"]["places"][0]
    assert first["n"] == 3 and first["bid_known"] == 0 and first["bid_sol"] is None and first["under"] is None
    assert out["overall"]["win_at"][0]["moments"] == 0  # no block whose every bid is known: nothing to say
    assert out["overall"]["win_at"][0]["above_first"] is None
    assert out["overall"]["auction"] is None  # one known bid a block: no pair


def test_a_second_buy_by_the_same_wallet_is_no_new_place():
    st = _store()
    try:
        for _ in range(3):
            m = _coin(st, LEAD1, block=((BOT_A, 1.0, 0.03), (BOT_A, 1.0, 0.03), (BOT_B, 1.0, 0.005)))
            assert sum(1 for t in m.trades if t.user == BOT_A) == 2
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    places = out["overall"]["places"]
    assert [p["n"] for p in places] == [3, 3] and places[1]["bid_sol"]["p50"] == 0.005


def test_the_moments_are_the_ones_the_lab_scores():
    """The table is read for the rule a lab then scores at the bid it names, so both must mean the same moments: the
    second known wallet in, 1 to 3 SOL (idea I51), once a coin."""
    rule = {"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "known": 2}
    st = _store()
    try:
        for _ in range(4):
            _coin(st, LEAD1, block=AUCTION)  # the first known wallet in: no moment
        for _ in range(5):
            _coin(st, LEAD2, block=AUCTION, before=(LEAD1,))  # the second: one
        _coin(st, LEAD2, block=AUCTION, before=(LEAD1,), lead_sol=5.0)  # too large a buy: none
        _coin(st, LEAD2, block=AUCTION, before=(LEAD1,), at=NOW - 70)  # in the tape's last minute: not scored
        cfg = Config()
        trig = parse_trigger(rule)
        lab = collect(st, cfg, buy_sol=0.2, latency_slots=0, position="+0", triggers=[trig], workers=1)
        moments = PB.moments(st, trig, st.tape_spans())
        out = PB.place_bids(st, cfg, NOW, trigger=rule, hours=2)
    finally:
        st.close()
    assert len(lab["rows"]) == 5
    assert {(m["mint"], m["ts"]) for m in moments} == {(r["mint"], r["ts"]) for r in lab["rows"]}
    assert {m["leader"] for m in moments} == {LEAD2} and out["moments"] == 5 and out["by_wallet"][0]["wallet"] == LEAD2


def test_each_wallets_own_first_buy_is_a_moment_when_asked_for():
    """A rule fires once a coin, as one sniper takes one position a coin. `each`: every wallet of the group gives its
    own moment in a coin, the way each wallet's own copy sniper copies its own buy; `wallets` keeps the named ones."""
    st = _store()
    try:
        for _ in range(3):
            _coin(st, LEAD2, block=AUCTION, before=(LEAD1,))
        once = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
        each = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2, each=True)
        named = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2, each=True, wallets=[LEAD2])
    finally:
        st.close()
    assert once["moments"] == 3 and {r["wallet"] for r in once["by_wallet"]} == {LEAD1}
    assert each["moments"] == 6 and {r["wallet"] for r in each["by_wallet"]} == {LEAD1, LEAD2}
    assert named["moments"] == 3 and {r["wallet"] for r in named["by_wallet"]} == {LEAD2} and named["each"] is True


def test_an_outsider_between_a_wallet_and_its_companion_is_counted():
    """The lab lands no copy between a wallet and its companions (transactions sent together). The table counts the
    blocks where somebody did buy between them, so the rule is checked against the tape and not only assumed."""
    st = _store()
    try:
        for _ in range(6):
            m = _coin(st, LEAD2, mate=True, block=SPEED)
        assert PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)["overall"]["between_its_wallets"] == 0
        for _ in range(5):  # the wallet, an outsider, then its companion further down the block
            _N[0] += 1
            k = _N[0]
            m = Market(st, addr(f"pbid-coin-{k}"), addr(f"pbid-dev-{k}"), NOW - 6000 + k * 20, 90_000_000 + k * 1000)
            m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
            for j in range(4):
                m.buy(addr(f"pbid-crowd-{k}-{j}"), 0.2, 2 + j)
            lead = _bid(m.buy(LEAD2, 2.0, 50), 0.002)
            lead.pos = 100
            mid = _bid(m.buy(BOT_C, 1.0, 50), 0.02)
            mid.pos = 101
            mate = _bid(m.buy(MATE, 1.0, 50), 0.002)
            mate.pos = 102
            m.sell(addr(f"pbid-crowd-{k}-0"), 1.0, 300)
            m.commit()
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    assert out["overall"]["moments"] == 11 and out["overall"]["between_its_wallets"] == 5
    assert out["by_wallet"][0]["mates"] == 1


def test_a_wallet_seen_once_behind_another_is_no_regular_of_it():
    st = _store()
    try:
        for _ in range(6):
            _coin(st, LEAD1, block=AUCTION)
        _coin(st, LEAD1, block=((RICH, 1.0, 0.05), AUCTION[0], AUCTION[1]))
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    regulars = {r["wallet"]: r for r in out["by_wallet"][0]["regulars"]}
    assert (
        set(regulars) == {BOT_A, BOT_B, BOT_C} and regulars[BOT_A]["moments"] == 7 and regulars[BOT_C]["moments"] == 6
    )
    assert [r["wallet"] for r in out["by_wallet"][0]["regulars"]][-1] == BOT_C  # the most frequent first


def test_only_the_newest_hours_of_tape_are_read():
    """As a lab's --hours: counted back from where the tape ends, so the table and the lab of one rule see one tape."""
    st = _store()
    try:
        for _ in range(3):
            _coin(st, LEAD1, block=AUCTION, at=NOW - 5000)
        for _ in range(2):
            _coin(st, LEAD1, block=AUCTION, at=NOW - 1000)
        assert PB.place_bids(st, Config(), NOW, trigger=RULE, hours=1)["moments"] == 2
        assert PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)["moments"] == 5
    finally:
        st.close()


def test_a_rule_that_follows_no_wallet_is_refused_in_plain_words():
    st = _store()
    try:
        with pytest.raises(ValueError, match="wallet rule"):
            PB.place_bids(st, Config(), NOW, trigger={"kind": "whale", "sol": 2}, hours=2)
        with pytest.raises(ValueError):
            PB.place_bids(st, Config(), NOW, trigger={"kind": "kol", "grop": "wallet"}, hours=2)
    finally:
        st.close()


def test_a_tape_copy_without_fee_records_says_so(monkeypatch):
    st = _store()
    try:
        _coin(st, LEAD1, block=AUCTION)
        monkeypatch.setattr(type(st), "has_columns", lambda self, table, columns: False)
        out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    finally:
        st.close()
    assert out["scored"] == 0 and "no fee records" in out["bids"]["note"] and out["overall"] is None


def test_the_default_rule_is_the_idea_it_was_built_for():
    assert PB.DEFAULT_TRIGGER == {"kind": "kol", "group": "wallet", "sol": 1, "max_sol": 3, "known": 2}
    st = _store()
    try:
        for _ in range(5):
            _coin(st, LEAD2, block=AUCTION, before=(LEAD1,))
        out = PB.place_bids(st, Config(), NOW, hours=2)
    finally:
        st.close()
    assert out["moments"] == 5 and out["trigger"] == PB.DEFAULT_TRIGGER


def test_the_agents_may_start_it_and_the_cli_prints_it(tmp_path, capsys):
    from snipetech import cli
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED, holds_deploy

    assert "place-bids" in RESEARCH_COMMANDS and "place-bids" in ALLOWED and "place-bids" not in OWNER_COMMANDS
    assert holds_deploy(["place-bids", "--json"]) and "place-bids" in cli.BATCH_COMMANDS
    db = tmp_path / "snipetech.db"
    st = _store(db)
    try:
        for _ in range(6):
            _coin(st, LEAD1, block=AUCTION)
    finally:
        st.close()
    base = ["--db", str(db), "place-bids", "--trigger", json.dumps(RULE), "--hours", "2"]
    assert cli.main([*base, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["moments"] == 6 and out["overall"]["places"][0]["bid_sol"]["p50"] == 0.03
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "place 1" in text and "0.03" in text and "follow the bids" in text
    assert cli.main([*base[:2], "place-bids", "--trigger", '{"kind": "whale", "sol": 2}']) == 2
    assert "wallet rule" in capsys.readouterr().err


def test_the_block_is_read_with_its_fee_records():
    st = _store()
    try:
        m = _coin(st, LEAD1, block=AUCTION, later=((RICH, 1.0, 0.05, 2), (FAST, 1.0, 0.01, 9)))
        slot = m.slot0 + 50
        rows = st.block_trades(m.mint, slot, slot + 4)
    finally:
        st.close()
    assert [t.user for t in rows] == [LEAD1, BOT_A, BOT_B, BOT_C, RICH]
    assert rows[1].priority + rows[1].tip == 30_000_000 and rows[0].pos == 100 and rows[1].pos == 120
    assert all(t.is_buy for t in rows) and rows[-1].slot == slot + 2
