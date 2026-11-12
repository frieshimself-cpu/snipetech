"""`snipetech tips`: the bid that outbids the wallets behind each followed wallet, one line a wallet.

The owner's rule: if our fee is not higher than theirs, they buy before us. Since 11 Nov 16:34 UTC the tape holds
every trade's priority fee and tip. For each followed wallet this reads the outsiders in the block of its own first
buys (its companions and our own wallets left out, as `place-bids` leaves them out) and says what the first of them
bid, which bid was above every one of them in half, three quarters and nine tenths of those blocks, and how many
different wallets follow it. Beside that, when asked, GMGN's count of the users who follow the wallet and what its
most regular copiers pay by GMGN's history (a handful of look-ups, kept a day).

Outbidding is not landing first: the playbook found the block's highest bidder first in 56 % of blocks."""

import json

import httpx

from snipetech import cli
from snipetech import gmgn as G
from snipetech.analysis import tips as T
from snipetech.config import Config
from snipetech.gmgn import Gmgn
from snipetech.store import Store
from tests.gmgn_fake import KEY, fake_gmgn
from tests.test_gmgn_real import REAL_ROUTES
from tests.test_place_bids import BOT_A, BOT_B, BOT_C, FAST, LEAD1, LEAD2, LEAD3, MATE, NOW, OURS, RICH, _coin, _store

BIDS = [0.001] * 5 + [0.005] * 3 + [0.02, 0.1]  # the one outsider's bid in each of ten blocks


def _ten_blocks(st, leader=LEAD1, who=BOT_A):
    for bid in BIDS:
        _coin(st, leader, block=((who, 1.0, bid),))


def _row(out, wallet):
    return next(r for r in out["wallets"] if r["wallet"] == wallet)


def test_the_bid_that_outbids_the_wallets_behind_a_followed_wallet():
    st = _store()
    try:
        _ten_blocks(st)
        for _ in range(2):
            _coin(st, LEAD1)  # nobody behind it in its block
        out = T.tips(st, Config(), NOW, group="wallet", hours=2)
    finally:
        st.close()
    r = _row(out, LEAD1)
    assert r["label"] == "tracked one" and r["first_buys"] == 12
    assert r["blocks_with_outsiders"] == 10 and r["share_with_outsiders"] == 0.833
    assert r["first_outsider_bid_sol"] == {"p50": 0.001, "p75": 0.005, "p90": 0.02}
    # the bid of the highest outsider in the block: under it in 5, 8 and 9 of the 10 blocks
    assert r["above_every_outsider_sol"] == {"50": 0.001, "75": 0.005, "90": 0.02}
    assert r["suggested_bid_sol"] == {"50": 0.0011, "90": 0.0201}  # one step above: an equal bid is not a higher one
    assert r["would_have_outbid"] == {"50": 0.5, "90": 0.9}  # checked back on the same blocks
    assert r["distinct_outsiders"] == 1 and r["its_own_bid_sol"] == 0.002
    assert r["top_copiers"] == [{"wallet": BOT_A, "label": None, "blocks": 10, "share": 1.0, "bid_sol": 0.001}]
    assert "0.0011" in r["line"] and "0.0201" in r["line"] and "tracked one" in r["line"]
    assert "10 blocks" in r["line"]
    # the label nobody may read past: a higher bid is not the first place
    assert "outbidding is not landing first" in out["warning"] and "56 %" in out["warning"]
    assert out["hours"] == 2 and out["group"] == "wallet" and out["bids_known"] is True


def test_several_outsiders_a_block_companions_and_our_own_wallets():
    """The first outsider's bid and the block's highest bid are two numbers when the order does not follow the bids.
    The wallet's companion (it buys in the very next transaction) and our own wallets are nobody's competition."""
    st = _store()
    cfg = Config()
    cfg.operator.wallet = OURS
    try:
        for _ in range(6):
            _coin(st, LEAD2, block=((FAST, 2.0, 0.0005), (OURS, 0.2, 0.003), (RICH, 2.0, 0.05)), mate=True)
        out = T.tips(st, cfg, NOW, group="wallet", hours=2)
    finally:
        st.close()
    r = _row(out, LEAD2)
    assert r["blocks_with_outsiders"] == 6 and r["companions"] == 1
    assert r["first_outsider_bid_sol"]["p50"] == 0.0005  # the fast wallet is first on a tenth of the rich one's bid
    assert r["above_every_outsider_sol"]["50"] == 0.05 and r["suggested_bid_sol"]["90"] == 0.0501
    assert r["distinct_outsiders"] == 2 and {c["wallet"] for c in r["top_copiers"]} == {FAST, RICH}
    assert MATE not in json.dumps(r) and OURS not in json.dumps(r)
    # measured in these very blocks: the highest bidder was first in none of them
    assert out["highest_bidder_first"] == {"blocks": 6, "share": 0.0}
    assert "0 %" in out["warning"] or "0%" in out["warning"]


def test_a_named_wallet_is_read_whatever_group_it_is_in_and_the_big_group_is_the_default():
    st = _store()
    try:
        st.set_label(LEAD3, "Cupsey test", "wallet", "file")  # a big name: the default group
        _ten_blocks(st, LEAD3, BOT_B)
        _ten_blocks(st, LEAD1, BOT_C)
        big = T.tips(st, Config(), NOW, hours=2)
        named = T.tips(st, Config(), NOW, wallets=[LEAD1], hours=2)
        nobody = T.tips(st, Config(), NOW, wallets=[LEAD2], hours=2)
    finally:
        st.close()
    assert big["group"] == "big" and [r["wallet"] for r in big["wallets"]] == [LEAD3]
    assert [r["wallet"] for r in named["wallets"]] == [LEAD1] and named["wallets"][0]["blocks_with_outsiders"] == 10
    assert nobody["wallets"] == [{"wallet": LEAD2, "label": "tracked two", "first_buys": 0,
                                  "line": "tracked two (" + LEAD2[:6] + "): no first buy of 0.1 SOL or more on this "
                                          "tape: nothing to read"}]  # fmt: skip


def test_too_few_blocks_get_no_suggestion():
    st = _store()
    try:
        for bid in (0.01, 0.02):
            _coin(st, LEAD1, block=((BOT_A, 1.0, bid),))
        out = T.tips(st, Config(), NOW, group="wallet", hours=2)
    finally:
        st.close()
    r = _row(out, LEAD1)
    assert r["blocks_with_outsiders"] == 2 and r["suggested_bid_sol"] is None
    assert "too few" in r["line"] and str(T.MIN_BLOCKS) in r["line"]


def test_a_tape_without_fee_records_says_so():
    st = _store()
    try:
        for _ in range(6):
            _coin(st, LEAD1, block=((BOT_A, 1.0, 0.01),), known=False)
        out = T.tips(st, Config(), NOW, group="wallet", hours=2)
    finally:
        st.close()
    r = _row(out, LEAD1)
    assert r["first_buys"] == 6 and r["blocks_with_outsiders"] == 0 and r["suggested_bid_sol"] is None


def _client(seen):
    return Gmgn(KEY, transport=fake_gmgn(seen, REAL_ROUTES), sleep=lambda s: None)


def test_gmgn_adds_how_followed_the_wallet_is_and_what_its_copiers_pay_within_a_handful_of_look_ups():
    st = _store()
    seen: list[httpx.Request] = []
    try:
        _ten_blocks(st, LEAD1, BOT_A)
        _ten_blocks(st, LEAD2, BOT_B)
        out = T.tips(st, Config(), NOW, group="wallet", hours=2, gmgn=_client(seen), max_lookups=3)
        r1, r2 = _row(out, LEAD1), _row(out, LEAD2)
        # GMGN's answers here are the real ones of tests/fixtures/gmgn/real/, whatever wallet is asked
        assert r1["gmgn"]["followed_by_gmgn_users"] == 2148 and r1["gmgn"]["tags"] == ["padre"]
        assert r1["top_copiers"][0]["gmgn_bid_sol"] == 0.019999999  # the copier's buys by GMGN's history: the median
        assert r1["top_copiers"][0]["gmgn_buys"] == 21
        assert "followed by 2,148 GMGN users" in r1["line"]
        # three look-ups were allowed: the first wallet's card, its copier's fees, the second wallet's card
        assert len(seen) == 3 and out["gmgn"] == {"look_ups": 3, "from_kept_answers": 0, "not_asked": 1, "cap": 3}
        assert r2["gmgn"]["followed_by_gmgn_users"] == 2148 and "gmgn_bid_sol" not in r2["top_copiers"][0]
        # an hour later the kept answers serve, and the one left over is asked
        seen.clear()
        again = T.tips(st, Config(), NOW + 3600, group="wallet", hours=3, gmgn=_client(seen), max_lookups=3)
        assert len(seen) == 1 and again["gmgn"] == {"look_ups": 1, "from_kept_answers": 3, "not_asked": 0, "cap": 3}
        assert _row(again, LEAD2)["top_copiers"][0]["gmgn_bid_sol"] == 0.019999999
        # a day later everything is asked again
        seen.clear()
        T.tips(st, Config(), NOW + 100_000, group="wallet", hours=30, gmgn=_client(seen), max_lookups=6)
        assert len(seen) == 4
        assert all(r.url.path in ("/v1/user/wallet_stats", "/v1/user/wallet_activity") for r in seen)
    finally:
        st.close()


def test_a_gmgn_refusal_leaves_the_tapes_table_whole():
    st = _store()
    seen: list[httpx.Request] = []
    refused = httpx.Response(403, json={"code": 403, "error": "AUTH_IP_BLOCKED", "message": "ip"})
    routes = dict(REAL_ROUTES, **{"/v1/user/wallet_stats": refused, "/v1/user/wallet_activity": refused})
    try:
        _ten_blocks(st)
        g = Gmgn(KEY, transport=fake_gmgn(seen, routes), sleep=lambda s: None)
        out = T.tips(st, Config(), NOW, group="wallet", hours=2, gmgn=g)
    finally:
        st.close()
    r = _row(out, LEAD1)
    assert r["suggested_bid_sol"] == {"50": 0.0011, "90": 0.0201} and "gmgn" not in r
    assert "AUTH_IP_BLOCKED" in out["gmgn"]["error"] and KEY not in json.dumps(out)
    assert len(seen) == 1  # a refused key is not asked again for every wallet


def test_the_agents_may_start_it_and_the_cli_prints_one_line_a_wallet(tmp_path, capsys):
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED, holds_deploy

    assert "tips" in RESEARCH_COMMANDS and "tips" in ALLOWED and "tips" not in OWNER_COMMANDS
    assert holds_deploy(["tips", "--json"]) and "tips" in cli.BATCH_COMMANDS
    db = tmp_path / "snipetech.db"
    st = _store(db)
    try:
        _ten_blocks(st)
    finally:
        st.close()
    base = ["--db", str(db), "tips", "--group", "wallet", "--hours", "2"]
    assert cli.main([*base, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert _row(out, LEAD1)["suggested_bid_sol"] == {"50": 0.0011, "90": 0.0201} and "gmgn" not in out
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "outbidding is not landing first" in text and "tracked one" in text and "0.0201" in text
    assert cli.main([*base[:2], "tips", "--wallet", LEAD2, "--hours", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["wallets"][0]["first_buys"] == 0
    assert cli.main([*base[:2], "tips", "--group", "nosuchgroup"]) == 2
    assert "group" in capsys.readouterr().err


def test_the_agents_instructions_say_what_tips_is_and_that_gmgns_fees_are_sol():
    import inspect

    from snipetech.operator import tools

    text = " ".join(inspect.getsource(tools).split())
    assert '["tips", "--group", "big", "--hours", "30", "--json"]' in text
    assert "outbidding is not landing first" in text
    assert '["gmgn", "funder", "<wallet>", "--json"]' in text and "proposed companions" in text
    # the fee unit is settled, and the old doubt is gone from the instructions
    assert "in SOL" in text and "check the unit against one trade" not in text
    assert "not checked against a real answer yet" not in text


def test_the_gmgn_cache_is_written_to_the_live_store_when_the_tape_is_a_copy(tmp_path):
    """The trades come from the hourly copy, which is read-only: what GMGN said is kept in the live database."""
    tape, live = _store(tmp_path / "tape.db"), Store(tmp_path / "live.db")
    seen: list[httpx.Request] = []
    try:
        _ten_blocks(tape)
        T.tips(tape, Config(), NOW, group="wallet", hours=2, gmgn=_client(seen), live=live, max_lookups=2)
        assert G.known_wallet(live, LEAD1)["follow_count"] == 2148 and G.known_wallet(tape, LEAD1) is None
        assert live.coverage(G.FEES_KEY + BOT_A)["buys"]["bid_sol"]["median"] == 0.019999999
    finally:
        tape.close()
        live.close()
