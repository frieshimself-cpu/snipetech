"""A paper rule for one leader wallet (12 Nov 2026, docs/research/2026-11-12-leader-profiles.md).

The study profiled twelve leaders one by one and found that Schoen and Dustxbt pay as single wallets, at the landing
our copies already get. Every per-leader rule "needs: only this wallet", and the lab had no such knob: a wallet rule
followed its whole label group. Three things were asked for:

- `only` on a wallet rule: the wallets it follows, by address. The group still says whose first buys the consensus
  gate `known` counts.
- `known_any`: that gate counts tracked wallets of every group ("a tracked wallet already in" was the best split for
  Schoen and Jijo), not only the rule's own.
- dust is no buyer in the bid tables: a buy of 0.00 SOL right behind Cupseyy made his first outsider's bid read 0.034
  SOL where it is 0.305. The bid landing model still counts it: it stood there, and it moves no price.

And, from the owner the same evening ("also keep in mind marketcap volume etc"): a wallet rule can gate on the SOL
traded in the coin before the followed buy (`vol_secs`, `min_vol`, `max_vol`), and every ranked wallet rule says where
its moments paid by market-cap band and by volume band (`bands`).
"""

import json
import time

import pytest

from snipetech import cli
from snipetech.analysis import bid_landing as BL
from snipetech.analysis import lab as L
from snipetech.analysis import paper
from snipetech.analysis import place_bids as PB
from snipetech.analysis import tips as T
from snipetech.analysis.lab import Trigger, collect, parse_trigger
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr
from .test_bid_landing import _N, AUCTION, BOT_A, BOT_B, BOT_C, HOLD, LEAD_AT, NOW, _bid, _coin, _entry

SCHOEN = "5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM"
DUSTXBT = "FzVQSzj8JJr6WMGqbUHzx2XH1KkrfxRrRPv6WcbbZmND"
DECU = "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9"
CUPSEYY = "6DQAGJT7VZPVBsuG4kn3AvpyHCEi7B2RFFvMZdbqQqqP"
A, B = addr("wk-lead-a"), addr("wk-lead-b")  # two wallets of the tracked list
OUT = addr("wk-not-tracked")  # a wallet on no list
KOL = addr("wk-a-kol")  # a labelled KOL: another group than the tracked list
DUST, DUST2 = addr("wk-dust"), addr("wk-dust-2")
RULE = {"kind": "kol", "group": "wallet", "sol": 1}


def _store(path=":memory:"):
    st = Store(path)
    st.set_label(A, "tracked a", "wallet", "file")
    st.set_label(B, "tracked b", "wallet", "file")
    st.set_label(KOL, "a kol", "kol", "file")
    st.set_coverage("tape", {"spans": [[NOW - 7000, NOW]]})
    return st


def _both(st, first, second, first_sol=1.5):
    """A coin `first` buys at slot +20 and `second` thirty slots later (2 SOL), each alone in its block."""
    _N[0] += 1
    k = _N[0]
    m = Market(st, addr(f"wk-coin-{k}"), addr(f"wk-dev-{k}"), NOW - 6000 + (k % 250) * 20, 70_000_000 + k * 1000)
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    crowd = [addr(f"wk-crowd-{k}-{j}") for j in range(4)]
    for j, who in enumerate(crowd):
        m.buy(who, 0.5, 2 + j)
    _bid(m.buy(first, first_sol, 20), 0.002)
    _bid(m.buy(second, 2.0, 50), 0.002)
    for j in range(8):
        _bid(m.buy(addr(f"wk-fan-{k}-{j}"), 0.5, 100 + 3 * j), 0.0001)
    _bid(m.sell(crowd[0], 1.0, 400), 0.0001)
    m.commit()
    return m


def _world(st):
    """Six coins only A buys, five only B buys, three both buy (B first), four a wallet on no list buys."""
    mine = [_coin(st, A, block=AUCTION).mint for _ in range(6)]
    theirs = [_coin(st, B, block=AUCTION).mint for _ in range(5)]
    both = [_both(st, B, A).mint for _ in range(3)]
    other = [_coin(st, OUT).mint for _ in range(4)]
    return mine, theirs, both, other


def _lab(st, *rules, position="+0", **kw):
    kw = {"triggers": [parse_trigger(r) for r in rules], "mates": {}, "exits": [HOLD], "workers": 1} | kw
    return collect(st, Config(), 2.0, 0, position, **kw)


def _fired(data, ti=0):
    return sorted((r["mint"], r["who"]) for r in data["rows"] if r["trigger"] == ti)


# -- `only`: the wallets a rule follows, by address ------------------------------------------------------------------


def test_only_is_the_same_rule_however_the_wallets_are_written():
    one = parse_trigger(RULE | {"only": f"{SCHOEN}, {DUSTXBT},{SCHOEN}"})
    other = parse_trigger(RULE | {"only": f"{DUSTXBT},{SCHOEN}"})
    assert one == other and one.only == f"{SCHOEN},{DUSTXBT}"  # sorted, each once
    assert L.only_wallets(one) == (SCHOEN, DUSTXBT)
    assert parse_trigger(RULE | {"only": [DUSTXBT, SCHOEN]}) == one  # a JSON list reads as the same set
    assert parse_trigger(RULE).only == "" and L.only_wallets(parse_trigger(RULE)) == ()
    ex = HOLD.to_dict()
    assert paper.strategy_id(one.__dict__, ex, "+6", 0, 2.0) == paper.strategy_id(other.__dict__, ex, "+6", 0, 2.0)


def test_only_takes_wallet_addresses_and_nothing_else():
    """Refused at parse, in plain words, before any tape is read: a typed name or half an address would follow
    nobody and the lab would say "0 moments" as if the wallet had not traded."""
    for bad in ("Schoen", SCHOEN[:10], f"{SCHOEN},Dustxbt", ",", " ", f"{SCHOEN};{DUSTXBT}"):
        with pytest.raises(ValueError, match="only"):
            parse_trigger(RULE | {"only": bad})
    with pytest.raises(ValueError, match="only"):
        parse_trigger(RULE | {"only": 5})
    with pytest.raises(ValueError, match="only"):
        parse_trigger(RULE | {"only": [SCHOEN, 5]})
    with pytest.raises(ValueError, match="does not apply"):
        parse_trigger({"kind": "hunt", "sol": 4, "window": 1.5, "only": SCHOEN})  # a wallet rule's knob
    with pytest.raises(ValueError, match="at most"):
        parse_trigger(RULE | {"only": ",".join(addr(f"wk-many-{k}") for k in range(L.ONLY_MAX + 1))})


def test_the_label_names_the_wallets():
    assert parse_trigger(RULE).label() == "a tracked wallet (wallet) makes its first buy in the coin, >= 1 SOL"
    one = parse_trigger(RULE | {"only": SCHOEN, "max_mcap": 300}).label()
    assert one == "the wallet 5hAgYC…84zM makes its first buy in the coin, >= 1 SOL, market cap <= 300 SOL"
    two = parse_trigger(RULE | {"only": f"{SCHOEN},{DUSTXBT}"}).label()
    assert two.startswith("one of 5hAgYC…84zM, FzVQSz…ZmND makes its first buy")
    four = parse_trigger(RULE | {"only": [SCHOEN, DUSTXBT, DECU, CUPSEYY]}).label()
    assert four.startswith("one of 4 wallets (4vw54B…9Ud9, 5hAgYC…84zM, 6DQAGJ…QqqP, FzVQSz…ZmND) makes its first buy")
    # the group is still what the consensus gate counts, and the label says whose buys those are
    gated = parse_trigger({"kind": "kol", "group": "kol", "sol": 1, "known": 2, "only": SCHOEN}).label()
    assert "with >= 2 known wallets in (the group kol and the named)" in gated


def test_rules_frozen_before_the_wallet_knobs_keep_their_ids():
    """A rule's id is its trigger, exit, landing and size. Left off, `only` and `known_any` are no part of it, so no
    rule under test is frozen a second time; set, they make another rule."""
    ex = L.exit_set()[0]
    assert ex.label() == "STALE1s SL-15% hold300s"
    big = parse_trigger({"kind": "kol", "group": "big", "sol": 1}).__dict__
    assert "only" in big and "known_any" in big  # today's record of a trigger carries both, unset
    assert paper.strategy_id(big, ex.to_dict(), "+0", 0, 10.0) == "d88a0bcb3a"  # as tests/test_size_honesty.py
    assert paper.strategy_id(big, ex.to_dict(), "+6", 0, 0.2) == "a6127feaf8"
    hold10 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False).to_dict()
    kol = Trigger("kol", sol=2.0).__dict__
    assert paper.strategy_id(kol, hold10, "+6", 0, 0.2) == "69d1450075"  # as tests/test_paper_own_landing.py
    before = {k: v for k, v in kol.items() if k not in ("only", "known_any")}  # as a rule frozen on 11 Nov stored it
    assert paper.strategy_id(before, hold10, "+6", 0, 0.2) == "69d1450075"
    assert paper.rule_key(before, "+6", 0, 0.2) == paper.rule_key(kol, "+6", 0, 0.2)
    assert paper.strategy_id(kol | {"only": SCHOEN}, hold10, "+6", 0, 0.2) != "69d1450075"
    assert paper.strategy_id(kol | {"known": 2, "known_any": True}, hold10, "+6", 0, 0.2) != paper.strategy_id(
        kol | {"known": 2}, hold10, "+6", 0, 0.2
    )


def test_a_rule_with_only_fires_on_that_wallets_first_buys_alone():
    st = _store()
    mine, theirs, both, other = _world(st)
    data = _lab(st, RULE, RULE | {"only": A}, RULE | {"only": OUT}, RULE | {"only": f"{A},{OUT}"})
    st.close()
    # the group's rule: once a coin, behind the first tracked wallet in (B on the coins both bought)
    assert _fired(data, 0) == sorted([(m, A) for m in mine] + [(m, B) for m in theirs + both])
    # A alone: its own coins, and its own first buy on the coins B was in first
    assert _fired(data, 1) == sorted((m, A) for m in mine + both)
    # a named wallet need not be in the group
    assert _fired(data, 2) == sorted((m, OUT) for m in other)
    assert _fired(data, 3) == sorted([(m, A) for m in mine + both] + [(m, OUT) for m in other])


def test_a_lab_with_only_reads_only_the_coins_those_wallets_bought(monkeypatch):
    st = _store()
    mine, _theirs, both, _other = _world(st)
    read: list[str] = []
    real = Store.trades_for_mint

    def spy(self, mint, *a, **kw):
        read.append(mint)
        return real(self, mint, *a, **kw)

    monkeypatch.setattr(Store, "trades_for_mint", spy)
    data = _lab(st, RULE | {"only": A})
    st.close()
    assert sorted(read) == sorted(mine + both) and len(data["rows"]) == 9


def test_the_named_wallets_companions_are_found_and_nobody_elses(monkeypatch):
    """No copy lands between one operator's wallets (CLAUDE.md rule 2): the lab looks up the companions of the wallets
    the rule follows, in the group or not, and of no other wallet of the group."""
    st = _store()
    for _ in range(6):
        _coin(st, OUT, mate=0.002, block=AUCTION)  # its companion buys in the very next transaction
    for _ in range(6):
        _coin(st, B, block=AUCTION)
    asked: list[list[str]] = []
    real = L.companions_of

    def spy(store, wallets, since):
        asked.append(list(wallets))
        return real(store, wallets, since)

    monkeypatch.setattr(L, "companions_of", spy)
    data = _lab(st, RULE | {"only": OUT}, mates=None)
    st.close()
    assert asked == [[OUT]] and data["mates"] == {"wallets": 1, "found_by": "lab"}
    assert len(data["rows"]) == 6 and {r["mates"] for r in data["rows"]} == {1}


def test_each_wallets_own_moment_and_the_bid_table_follow_only_too():
    """The per-wallet moments the qualify lab and `place-bids --each` use, and the table of place against bid, are
    read for the wallets the rule names: the moments are the ones a lab of the same rule scores."""
    st = _store()
    mine, _theirs, both, _other = _world(st)
    rule = RULE | {"only": A}
    trig = parse_trigger(rule)
    lab = _lab(st, rule)
    once = PB.moments(st, trig, st.tape_spans())
    each = PB.moments(st, trig, st.tape_spans(), each=True)
    group = _lab(st, rule, groups=("wallet",))  # the qualify lab's form: every wallet's own first buy
    table = PB.place_bids(st, Config(), NOW, trigger=rule, hours=2)
    whole = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    st.close()
    want = sorted(mine + both)
    assert sorted(m["mint"] for m in once) == want and {m["leader"] for m in once} == {A}
    assert sorted(m["mint"] for m in each) == want and {m["leader"] for m in each} == {A}
    assert {(m["mint"], m["ts"]) for m in once} == {(r["mint"], r["ts"]) for r in lab["rows"]}
    assert _fired(group) == sorted((m, A) for m in want)
    assert table["moments"] == 9 and table["wallets"] == 1 and [r["wallet"] for r in table["by_wallet"]] == [A]
    assert table["rule"] == trig.label() and A[:6] in table["rule"]
    assert table["overall"]["places"][0]["n"] == 6 and table["overall"]["places"][0]["bid_sol"]["p50"] == 0.03
    assert whole["moments"] == 14 and whole["wallets"] == 2  # the group's rule: every coin a tracked wallet bought


def test_a_copy_of_a_named_wallet_lands_where_its_bid_puts_it():
    st = _store()
    mine, _theirs, _both, _other = _world(st)
    data = _lab(st, RULE | {"only": A}, position="bid", buy_bid_sol=0.004)
    st.close()
    rows = [r for r in data["rows"] if r["mint"] in mine]
    assert len(data["rows"]) == 9 and {r["who"] for r in data["rows"]} == {A}
    assert {r["place"] for r in rows} == {3} and {r["landing"] for r in rows} == {"bid@0.004"}  # two outbid us
    assert data["bid_landing"]["scored"] == 9


def test_the_cli_runs_a_lab_and_a_bid_table_for_one_wallet(tmp_path, capsys):
    db = tmp_path / "snipetech.db"
    st = _store(db)
    try:
        for _ in range(12):
            _coin(st, A, block=AUCTION)
        for _ in range(12):
            _coin(st, B, block=AUCTION)
    finally:
        st.close()
    rule = json.dumps(RULE | {"only": A})
    assert rule.startswith('{"kind": "kol", "group": "wallet", "sol": 1, "only": "') and A in rule  # as typed
    base = ["--db", str(db), "lab", "--buy", "2", "--per-tx", "0.0045", "--copy-position", "+0"]
    assert cli.main([*base, "--trigger", rule, "--trigger", json.dumps(RULE), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    named, group = out["triggers"]
    assert named["moments"] == 12 and group["moments"] == 24
    assert named["trigger"].startswith(f"the wallet {A[:6]}…{A[-4:]} makes its first buy")
    assert cli.main([*base, "--trigger", rule]) == 0
    assert f"{A[:6]}…{A[-4:]}" in capsys.readouterr().out
    with pytest.raises(SystemExit):  # refused by the argument parser, before any tape is read
        cli.main([*base, "--trigger", json.dumps(RULE | {"only": "Schoen"})])
    assert "only" in capsys.readouterr().err
    bids = ["--db", str(db), "place-bids", "--hours", "2"]
    assert cli.main([*bids, "--trigger", rule, "--json"]) == 0
    table = json.loads(capsys.readouterr().out)
    assert table["moments"] == 12 and table["wallets"] == 1 and table["by_wallet"][0]["wallet"] == A
    assert table["trigger"]["only"] == A
    assert cli.main([*bids, "--trigger", json.dumps(RULE | {"only": "Schoen"})]) == 2
    assert "only" in capsys.readouterr().err


# -- `paper freeze`, then `paper run` ---------------------------------------------------------------------------------

T0 = int(time.time()) - 7500  # an hour of tape before the freeze, an hour after it
FREEZE, RUN = T0 + 3600, T0 + 7200
EXIT10 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False)


def _paying(st, k: int, ts: int, leader: str, ahead: str | None = None) -> str:
    """A coin `leader` buys with 2 SOL (`ahead`: after that wallet's 1.5 SOL, thirty slots before); eight buyers then
    lift the price for half a minute, so a copy pays."""
    m = Market(st, addr(f"wkp-coin-{k}"), addr(f"wkp-dev-{k}"), ts, 500_000_000 + (ts - T0) * 5 // 2)  # 0.4 s a slot
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    for j in range(4):
        m.buy(addr(f"wkp-crowd-{k}-{j}"), 0.5, 2 + j)
    if ahead:
        m.buy(ahead, 1.5, 20)
    m.buy(leader, 2.0, 50)
    for j in range(8):
        m.buy(addr(f"wkp-fan-{k}-{j}"), 0.5, 100 + 3 * j)
    m.sell(addr(f"wkp-crowd-{k}-0"), 1.0, 400)
    m.commit()
    return m.mint


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    return cfg


def _freeze(st, rule, **kw):
    kw = {"top": 4, "buy_sol": 0.2, "per_tx_sol": 0.0035, "triggers": [parse_trigger(rule)], "now": FREEZE,
          "copy_position": "+1"} | kw  # fmt: skip
    return paper.freeze(st, _cfg(), **kw)


def test_a_rule_for_one_wallet_is_frozen_and_scored_forward_on_that_wallet_alone():
    st = Store(":memory:")
    st.set_label(A, "tracked a", "wallet", "file")
    st.set_label(B, "tracked b", "wallet", "file")
    for k in range(24):
        _paying(st, k, T0 + 100 + k * 120, A)
        _paying(st, 50 + k, T0 + 160 + k * 120, B)
    st.set_coverage("tape", {"spans": [[T0, FREEZE]]})
    rule = RULE | {"only": A}
    (frozen,) = _freeze(st, rule)["frozen"]
    assert frozen["trigger"]["only"] == A and frozen["trigger"]["group"] == "wallet"
    assert frozen["label"].startswith(f"the wallet {A[:6]}…{A[-4:]} makes its first buy")
    assert frozen["id"] == paper.sid_of(frozen)
    (whole,) = _freeze(st, RULE)["frozen"]  # the group's rule is another rule, with its own record
    assert whole["id"] != frozen["id"] and whole["trigger"]["only"] == ""
    assert whole["id"] == paper.strategy_id({k: v for k, v in whole["trigger"].items() if k != "only"}, whole["exit"],
                                            "+1", 0, 0.2, 0.0, True)  # fmt: skip
    assert _freeze(st, rule)["frozen"] == []  # frozen once

    mine = [_paying(st, 100 + k, FREEZE + 300 + k * 120, A) for k in range(5)]
    theirs = [_paying(st, 150 + k, FREEZE + 360 + k * 120, B) for k in range(5)]
    st.set_coverage("tape", {"spans": [[T0, RUN]]})
    out = paper.run(st, _cfg(), now=RUN)
    assert out["new_trades"] == 15
    assert [r["mint"] for r in st.paper_trades(frozen["id"])] == mine
    assert sorted(r["mint"] for r in st.paper_trades(whole["id"])) == sorted(mine + theirs)
    kept = {s["id"]: s for s in st.coverage("paper_strategies")["strategies"]}
    assert kept[frozen["id"]]["trigger"]["only"] == A  # a later run still follows that wallet alone
    st.close()


# -- `known_any`: the consensus gate counts tracked wallets of every group -----------------------------------------


def test_known_any_counts_the_tracked_wallets_of_every_group():
    """ "A tracked wallet already in" was the best split for Schoen and Jijo. With the rule's own group alone (the 18
    KOLs) it left 7 copies: the wallets already in were mostly on the tracked list, another group."""
    st = _store()
    with_kol = [_both(st, KOL, A).mint for _ in range(5)]  # a KOL is in, then a tracked wallet buys
    with_peer = [_both(st, B, A).mint for _ in range(3)]  # another tracked wallet is in first
    for _ in range(4):
        _both(st, OUT, A)  # a wallet on no list is in first: nobody known
    for _ in range(4):
        _both(st, KOL, A, first_sol=0.05)  # the KOL's buy is dust: no signal (KOL_MIN_SOL)
    second = RULE | {"known": 2}
    data = _lab(st, second, second | {"known_any": True}, second | {"known_any": True, "only": A})
    st.close()
    assert _fired(data, 0) == sorted((m, A) for m in with_peer)  # the tracked list alone: B, then A
    assert _fired(data, 1) == sorted((m, A) for m in with_kol + with_peer)
    assert _fired(data, 2) == _fired(data, 1)
    assert parse_trigger(second).label().endswith("with >= 2 known wallets in")  # as it always read
    assert parse_trigger(second | {"known_any": True}).label().endswith("with >= 2 tracked wallets of any group in")


def test_known_any_reaches_the_bid_table_the_forward_run_and_the_cli(tmp_path, capsys):
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        st.set_label(A, "tracked a", "wallet", "file")
        st.set_label(KOL, "a kol", "kol", "file")
        for k in range(24):
            _paying(st, k, T0 + 100 + k * 120, A, ahead=KOL)
            _paying(st, 50 + k, T0 + 160 + k * 120, A)  # nobody known ahead: no moment
        st.set_coverage("tape", {"spans": [[T0, FREEZE]]})
        rule = RULE | {"known": 2, "known_any": True}
        assert _freeze(st, RULE | {"known": 2})["frozen"] == []  # the tracked list alone: never a second one in
        (frozen,) = _freeze(st, rule)["frozen"]
        assert frozen["trigger"]["known_any"] is True and frozen["lab"]["ev_lower"] > 0
        table = PB.place_bids(st, Config(), FREEZE, trigger=rule, hours=1)
        assert table["moments"] == 24 and table["trigger"] == rule
        mine = [_paying(st, 100 + k, FREEZE + 300 + k * 120, A, ahead=KOL) for k in range(4)]
        for k in range(3):
            _paying(st, 150 + k, FREEZE + 360 + k * 120, A)
        st.set_coverage("tape", {"spans": [[T0, RUN]]})
        assert paper.run(st, _cfg(), now=RUN)["new_trades"] == 4
        assert [r["mint"] for r in st.paper_trades(frozen["id"])] == mine
    finally:
        st.close()
    base = ["--db", str(db), "lab", "--buy", "0.2", "--per-tx", "0.0045", "--copy-position", "+1", "--json"]
    assert cli.main([*base, "--trigger", json.dumps(rule), "--trigger", json.dumps(RULE | {"known": 2})]) == 0
    wide, own = json.loads(capsys.readouterr().out)["triggers"]
    assert wide["moments"] == 28 and own["moments"] == 0 and "tracked wallets of any group" in wide["trigger"]


def test_known_any_goes_with_a_consensus_gate_on_a_wallet_rule():
    assert parse_trigger(RULE | {"known": 2}).known_any is False
    assert parse_trigger(RULE | {"gap": 5, "known_any": True}).known_any is True  # `gap` reads the same wallets
    with pytest.raises(ValueError, match="known_any"):
        parse_trigger(RULE | {"known_any": True})  # nothing to widen: it would be the plain rule under another id
    with pytest.raises(ValueError, match="known_any"):
        parse_trigger(RULE | {"known": 2, "known_any": 1})
    with pytest.raises(ValueError, match="does not apply"):
        parse_trigger({"kind": "whale", "sol": 2, "known_any": True})


# -- the volume gate, and every lab's result by market-cap band and by volume band --------------------------------
#
# The owner, 12 Nov: "also keep in mind marketcap volume etc". A wallet rule can ask how much SOL was traded in the
# coin before the followed buy, and every lab says where its rule's moments paid by market cap and by volume.

BUSY, FAN = addr("wk-busy"), addr("wk-fan")
LEAD_SLOT = 400  # 160 s after the create: the create and the early buys are outside the last minute


def _vol_coin(st, leader, early=0.0, recent=0.0, after=0.0, sells=False):
    """A coin whose creator buys 0.5 SOL; `early` SOL is bought 156 s before the followed buy, `recent` SOL 40 s
    before it (`sells`: and a quarter of those coins sold again 36 s before it), then `leader` buys 2 SOL, `after`
    SOL is bought right behind it in its block and the block after, and eight buyers lift the price."""
    _N[0] += 1
    k = _N[0]
    m = Market(st, addr(f"wkv-coin-{k}"), addr(f"wkv-dev-{k}"), NOW - 6000 + (k % 250) * 20, 70_000_000 + k * 1000)
    rows = [m.buy(m.deployer, 0.5, 0, sig=m.create_sig)]
    if early:
        rows.append(m.buy(addr(f"wkv-early-{k}"), early, 10))
    if recent:
        rows.append(m.buy(BUSY, recent, 300))
    if sells:
        rows.append(m.sell(BUSY, 0.25, 310))
    lead = m.buy(leader, 2.0, LEAD_SLOT)
    if after:
        rows += [m.buy(addr(f"wkv-after-{k}-0"), after / 2, LEAD_SLOT), m.buy(addr(f"wkv-after-{k}-1"), after / 2, 401)]
    for j in range(8):
        rows.append(m.buy(addr(f"wkv-fan-{k}-{j}"), 0.5, 450 + 3 * j))
    rows.append(m.buy(FAN, 0.05, 700))  # the tape goes on past every exit
    for t in [lead, *rows]:
        _bid(t, 0.002)
    m.commit()
    return m


def _traded_before(m, leader, secs=None):
    """SOL traded in the coin before `leader`'s buy (in the last `secs` seconds before it), read off its trades."""
    at = next(j for j, t in enumerate(m.trades) if t.user == leader)
    lead = m.trades[at]
    return sum(t.quote for t in m.trades[:at] if secs is None or t.ts >= lead.ts - secs) / 1e9


def test_a_wallet_rule_can_ask_for_the_volume_before_the_followed_buy():
    full = RULE | {"vol_secs": 60, "min_vol": 5, "max_vol": 50}
    trig = parse_trigger(full)
    assert (trig.vol_secs, trig.min_vol, trig.max_vol) == (60.0, 5.0, 50.0)
    assert trig.label().endswith(">= 1 SOL, 5 to 50 SOL traded in the 60 s before the buy")
    assert parse_trigger(RULE | {"vol_secs": 60, "min_vol": 5}).label().endswith(
        ">= 5 SOL traded in the 60 s before the buy")  # fmt: skip
    assert parse_trigger(RULE | {"max_vol": 20}).label().endswith("<= 20 SOL traded before the buy since the launch")
    assert parse_trigger(RULE).label() == "a tracked wallet (wallet) makes its first buy in the coin, >= 1 SOL"
    with pytest.raises(ValueError, match="max_vol"):
        parse_trigger(RULE | {"min_vol": 50, "max_vol": 5})  # no coin fits
    with pytest.raises(ValueError, match="vol_secs"):
        parse_trigger(RULE | {"vol_secs": 60})  # a window and no limit: the plain rule under another name
    with pytest.raises(ValueError, match="does not apply"):
        parse_trigger({"kind": "hunt", "sol": 4, "window": 1.5, "min_vol": 5})
    with pytest.raises(ValueError, match="min_vol"):
        parse_trigger(RULE | {"min_vol": -1})
    with pytest.raises(ValueError, match="vol_secs"):
        parse_trigger(RULE | {"vol_secs": 1e9, "min_vol": 1})
    ex = HOLD.to_dict()
    plain = paper.strategy_id(parse_trigger(RULE).__dict__, ex, "+6", 0, 2.0)
    assert paper.strategy_id(trig.__dict__, ex, "+6", 0, 2.0) != plain
    before = {k: v for k, v in parse_trigger(RULE).__dict__.items() if k not in ("vol_secs", "min_vol", "max_vol")}
    assert paper.strategy_id(before, ex, "+6", 0, 2.0) == plain  # a rule frozen before the gate keeps its id


def test_the_volume_gate_reads_only_what_was_traded_before_the_followed_buy():
    """Buys and sells together, the followed buy itself not counted, and nothing that came after it: a coin whose
    volume arrives behind the wallet's buy is a quiet coin at the moment the copy is sent."""
    st = _store()
    quiet = _vol_coin(st, A)
    busy = _vol_coin(st, A, recent=10.0, sells=True)
    early = _vol_coin(st, A, early=10.0)  # traded, but more than a minute before the buy
    late = _vol_coin(st, A, after=30.0)  # 30 SOL right behind the wallet: not there when it bought
    assert (
        _traded_before(busy, A, 60) == pytest.approx(10.0 + busy.trades[2].quote / 1e9) and busy.trades[2].quote > 1e9
    )
    assert _traded_before(late, A, 60) == 0 and _traded_before(early, A, 60) == 0
    assert _traded_before(early, A) == 10.5 and _traded_before(quiet, A) == 0.5
    minute = RULE | {"vol_secs": 60}
    data = _lab(st, RULE, minute | {"min_vol": 5}, minute | {"max_vol": 5}, RULE | {"min_vol": 5},
                minute | {"min_vol": 5, "max_vol": 11})  # fmt: skip
    rows = {r["mint"]: r for r in data["rows"] if r["trigger"] == 0}
    assert set(rows) == {m.mint for m in (quiet, busy, early, late)}
    for m in (quiet, busy, early, late):  # each scored row says both, for the bands
        assert rows[m.mint]["vol60_sol"] == pytest.approx(_traded_before(m, A, 60), abs=1e-6)
        path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
        at = next(j for j in range(path.n) if path.users[j] == A)
        assert rows[m.mint]["mcap_sol"] == pytest.approx(path.mcap_sol(at), abs=1e-3)
    assert rows[late.mint]["vol60_sol"] == 0 and rows[busy.mint]["mcap_sol"] > rows[quiet.mint]["mcap_sol"] > 30
    assert _fired(data, 1) == [(busy.mint, A)]
    assert _fired(data, 2) == sorted((m.mint, A) for m in (quiet, early, late))
    assert _fired(data, 3) == sorted((m.mint, A) for m in (busy, early))  # since the launch: the early volume counts
    assert _fired(data, 4) == []  # 10 SOL bought and a quarter sold again: over 11 SOL traded
    # each wallet's own moments (the qualify lab, `place-bids --each`), the bid table and the bid landing agree
    gated = parse_trigger(minute | {"min_vol": 5})
    assert [m["mint"] for m in PB.moments(st, gated, st.tape_spans(), each=True)] == [busy.mint]
    assert [m["mint"] for m in PB.moments(st, gated, st.tape_spans())] == [busy.mint]
    assert _fired(_lab(st, minute | {"min_vol": 5}, groups=("wallet",))) == [(busy.mint, A)]
    at_bid = _lab(st, minute | {"min_vol": 5}, position="bid", buy_bid_sol=0.01)
    assert _fired(at_bid) == [(busy.mint, A)]
    st.close()


def test_a_rule_with_a_volume_gate_is_scored_forward_on_the_coins_that_pass_it():
    st = Store(":memory:")
    st.set_label(A, "tracked a", "wallet", "file")
    for k in range(24):
        _paying(st, k, T0 + 100 + k * 120, A, ahead=BUSY)  # 1.5 SOL more before the buy: 4 SOL traded
        _paying(st, 50 + k, T0 + 160 + k * 120, A)  # 2.5 SOL traded
    st.set_coverage("tape", {"spans": [[T0, FREEZE]]})
    rule = RULE | {"vol_secs": 60, "min_vol": 3}
    (frozen,) = _freeze(st, rule)["frozen"]
    assert (frozen["trigger"]["vol_secs"], frozen["trigger"]["min_vol"]) == (60.0, 3.0)
    assert "3 SOL traded in the 60 s before the buy" in frozen["label"]
    mine = [_paying(st, 100 + k, FREEZE + 300 + k * 120, A, ahead=BUSY) for k in range(4)]
    for k in range(3):
        _paying(st, 150 + k, FREEZE + 360 + k * 120, A)
    st.set_coverage("tape", {"spans": [[T0, RUN]]})
    assert paper.run(st, _cfg(), now=RUN)["new_trades"] == 4
    assert [r["mint"] for r in st.paper_trades(frozen["id"])] == mine
    st.close()


def _band_world(st):
    """Twenty-five moments of one wallet in four kinds of coin: small and lightly traded, mid-sized and busy, large
    and quiet for a minute, and three at 240 SOL of market cap behind 55 SOL of buying."""
    for _ in range(8):
        _vol_coin(st, A, recent=2.0)  # 37 SOL of market cap, 2 SOL traded in the last minute
    for _ in range(8):
        _vol_coin(st, A, recent=10.0)  # 56 SOL, 10 SOL
    for _ in range(6):
        _vol_coin(st, A, early=30.0)  # 121 SOL, nothing in the last minute
    for _ in range(3):
        _vol_coin(st, A, recent=55.0)  # 238 SOL, 55 SOL


def test_every_ranked_wallet_rule_says_where_it_paid_by_market_cap_and_by_volume():
    st = _store()
    _band_world(st)
    data = _lab(st, RULE, {"kind": "whale", "sol": 2})
    ranked = {r["trigger_spec"]["kind"]: r for r in L.rank(data, _cfg(), top=10)}
    st.close()
    assert "bands" not in ranked["whale"]  # a wallet rule's table: the cap after the followed buy
    row = ranked["kol"]
    bands = row["bands"]
    assert row["moments"] == row["exit_moments"] == 25 and bands["moments"] == 25 and bands["min_n"] == 5
    assert "all" in bands["note"] and "hypothesis to test with the gate, not a result" in bands["note"]
    caps, vols = bands["mcap_sol"], bands["vol60_sol"]
    assert [b["band"] for b in caps] == ["under 50", "50-100", "100-200", "200-410", "410 and up"]
    assert [b["band"] for b in vols] == ["0", "up to 5", "5-20", "20-50", "50 and up"]
    assert [b["n"] for b in caps] == [8, 8, 6, 3, 0] and [b["n"] for b in vols] == [6, 8, 8, 0, 3]
    assert caps[3] == {"band": "200-410", "n": 3} and caps[4] == {"band": "410 and up", "n": 0}  # under 5: n only
    assert vols[3] == {"band": "20-50", "n": 0}
    mine = [r for r in data["rows"] if r["trigger"] == 0]
    for b, lo, hi in ((caps[0], 0, 50), (caps[1], 50, 100), (caps[2], 100, 200)):
        pnl = [r["pnl"][0] for r in mine if lo <= r["mcap_sol"] < hi]
        assert set(b) == {"band", "n", "mean", "ev_lower", "win_rate", "total"} and b["n"] == len(pnl)
        assert b["mean"] == pytest.approx(sum(pnl) / len(pnl)) and b["total"] == pytest.approx(sum(pnl))
        assert b["ev_lower"] <= b["mean"] + 1e-12 and b["win_rate"] == sum(1 for x in pnl if x > 0) / len(pnl)
    assert vols[0]["mean"] == pytest.approx(caps[2]["mean"])  # the large coins are the ones quiet for a minute
    assert caps[0]["mean"] > caps[1]["mean"] > caps[2]["mean"] > 0  # the same buying moves a small coin most


def test_the_lab_command_prints_the_bands_under_the_strategy(tmp_path, capsys):
    db = tmp_path / "snipetech.db"
    st = _store(db)
    try:
        _band_world(st)
    finally:
        st.close()
    base = ["--db", str(db), "lab", "--buy", "2", "--per-tx", "0.0045", "--copy-position", "+0", "--trigger",
            json.dumps(RULE)]  # fmt: skip
    assert cli.main([*base, "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)["strategies"]
    assert [b["n"] for b in row["bands"]["mcap_sol"]] == [8, 8, 6, 3, 0]
    assert [b["n"] for b in row["bands"]["vol60_sol"]] == [6, 8, 8, 0, 3]
    assert cli.main(base) == 0
    text = capsys.readouterr().out
    assert "By market cap after the followed buy" in text and "By SOL traded in the 60 s before it" in text
    assert "under 50" in text and "410 and up" in text and "up to 5" in text and "50 and up" in text
    assert "hypothesis to test with the gate, not a result" in text


# -- dust is no buyer in the bid tables -------------------------------------------------------------------------------


def _dusty(k: int, rest=(*AUCTION, (DUST2, 0.01, 2.0, 30))):
    """A block with dust in it. Right behind the wallet a fresh wallet buys 0.004 SOL at a bid of 0.0001 (another
    wallet each coin: one and the same would be read as the followed wallet's companion and left out for that), and
    further down a 0.01 SOL buy bids 2 SOL."""
    return ((addr(f"wk-dust-of-{k}"), 0.004, 0.0001, 1), *rest)


def test_a_dust_buy_is_no_buyer_in_the_place_bids_table():
    """12 Nov: a buy of 0.00 SOL right behind Cupseyy made his first outsider's bid read 0.034 SOL in `place-bids`
    where the study, which leaves dust out, read 0.305. A buy under 0.1 SOL moves no price and competes for nothing:
    it is no first buyer, it holds no place, and its bid is no bid to outbid."""
    assert L.DUST_SOL == L.KOL_MIN_SOL == 0.1 and PB.DUST_SOL is L.DUST_SOL
    st = _store()
    for k in range(6):
        _coin(st, A, block=_dusty(k))
    for k in range(6, 8):
        _coin(st, A, block=_dusty(k, ()), later=((BOT_A, 1.0, 0.01, 2),))  # dust alone in the block
    out = PB.place_bids(st, Config(), NOW, trigger=RULE, hours=2)
    st.close()
    all_ = out["overall"]
    assert out["by_wallet"][0]["mates"] == 0  # nobody was left out as the wallet's companion: the dust floor did it
    assert all_["in_block"] == {"n": 6, "share": 0.75}
    assert all_["first_buyer"] == {"same_block": 0.75, "next_block": 0.0, "2-4_blocks": 0.25, "nobody": 0.0}
    first, second, third = all_["places"]
    assert first["bid_sol"]["p50"] == 0.03 and first["n"] == 6 and first["adjacent"] == 0.0
    assert second["bid_sol"]["p50"] == 0.005 and third["bid_sol"]["p50"] == 0.001
    win = {r["bid_sol"]: (r["above_first"], r["above_all"]) for r in all_["win_at"]}
    assert win[0.01] == (0.25, 0.25) and win[0.05] == (1.0, 1.0)  # 0.05 SOL is above every real bid: not the dust's 2
    assert all_["auction"] == {"blocks": 6, "highest_bid_first": 1.0, "pairs": 18, "in_bid_order": 1.0}
    assert [h["wallet"] for h in out["first_place_holders"]] == [BOT_A]
    assert {r["wallet"] for r in out["by_wallet"][0]["regulars"]} == {BOT_A, BOT_B, BOT_C}
    assert out["by_wallet"][0]["first"]["bid_sol"] == 0.03
    assert out["dust_under_sol"] == 0.1 and any("under 0.1 SOL" in line for line in out["reading"])


def test_a_dust_buy_is_no_bid_to_outbid_in_the_tip_table():
    st = _store()
    for k in range(6):
        _coin(st, A, block=_dusty(k))
    out = T.tips(st, Config(), NOW, group="wallet", hours=2)
    st.close()
    (row,) = out["wallets"]
    assert row["companions"] == 0 and row["blocks_with_outsiders"] == 6
    assert row["first_outsider_bid_sol"] == {"p50": 0.03, "p75": 0.03, "p90": 0.03}
    assert row["above_every_outsider_sol"] == {"50": 0.03, "75": 0.03, "90": 0.03}
    assert row["suggested_bid_sol"] == {"50": 0.0301, "90": 0.0301} and row["distinct_outsiders"] == 3
    assert {c["wallet"] for c in row["top_copiers"]} == {BOT_A, BOT_B, BOT_C}
    assert DUST2 not in json.dumps(out) and not any(addr(f"wk-dust-of-{k}") in json.dumps(out) for k in range(6))
    assert out["dust_under_sol"] == 0.1 and any("under 0.1 SOL" in line for line in out["reading"])


def test_a_wallet_that_buys_dust_first_is_counted_at_its_real_buy():
    st = _store()
    m = _coin(st, A, block=((BOT_B, 0.004, 0.0001, 1), (BOT_A, 3.0, 0.03, 20), (BOT_B, 1.0, 0.005, 21)))
    slot = m.slot0 + 50
    trades = st.block_trades(m.mint, slot, slot + 4)
    lead = next(t for t in trades if t.user == A)
    table = BL.block(trades, lead.sig, min_sol=L.DUST_SOL)
    every = BL.block(trades, lead.sig)
    st.close()

    def seen(read):
        return [(b["user"], b["place"], b["bid"], b["behind"]) for b in read["buyers"]]

    assert seen(table) == [(BOT_A, 1, 0.03, 20), (BOT_B, 2, 0.005, 21)]
    # read without the floor, as the bid landing model reads it: the dust buy is that wallet's first buy
    assert seen(every) == [(BOT_B, 1, 0.0001, 1), (BOT_A, 2, 0.03, 20)]


def test_the_bid_landing_model_still_stands_behind_a_dust_transaction():
    """The landing model is not the table. A dust transaction that landed ahead of us took its place in the block
    whatever it bought: one in the first two transactions behind the wallet is the faster sender no bid beats, and one
    that outbid us stands where a bid like ours did not reach. Leaving it out would put the paper copy at a place
    better than its bid buys. It costs the copy nothing where it stands right behind the wallet (dust moves no price),
    and the place the lab reports counts it, as the chain reader counts it for our real buys (`fills.trades_behind`)."""
    st = Store(":memory:")
    st.set_coverage("tape", {"spans": [[NOW - 7000, NOW]]})
    m = _coin(st, block=((DUST, 0.004, 0.0001, 1), *AUCTION))
    e, info = _entry(st, m, 0.05)  # above every bid of the block: behind the fast dust buy alone
    assert e == LEAD_AT + 1 and info == {"place": 2, "outbid": 0, "fast": 1, "later": 0}
    path = MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))
    assert path.users[e] == DUST
    paid = (path.q[e] / path.b[e]) / (path.q[LEAD_AT] / path.b[LEAD_AT])
    assert 1.0 <= paid < 1.001  # the price right behind the wallet, to a tenth of a percent
    late = _coin(st, block=((BOT_C, 0.5, 0.001, 20), (DUST, 0.004, 0.5, 21)))  # dust that outbid us, down the block
    e, info = _entry(st, late, 0.01)
    assert e == LEAD_AT + 2 and info == {"place": 2, "outbid": 1, "fast": 0, "later": 0}  # priced behind both
    st.close()
