"""Fill classification: the copied wallet's buy before ours, trades behind, price gap, position in the slot,
fees from the fingerprint, FIFO round trips."""

from snipetech.measure.classify import arm_for, arm_from_fee, classify_trade, leaders_for, pair_round_trips
from snipetech.models import Trade
from snipetech.solana.txmeta import TxMeta
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000
ME, LEAD, X, Y, D = addr("me"), addr("lead"), addr("x"), addr("y"), addr("dev")


def _world(st):
    m = Market(st, addr("m1"), D, NOW, SLOT)
    m.buy(D, 1.0, 0)
    tl = m.buy(LEAD, 1.0, 2)
    m.buy(X, 0.5, 3)
    m.buy(Y, 0.5, 4)
    tme = m.buy(ME, 0.2, 4)  # same slot as Y, after it
    m.buy(addr("later"), 0.3, 6)
    tsell = m.sell(ME, 1.0, 30)
    m.commit()
    m2 = Market(st, addr("m2"), D, NOW + 1000, SLOT + 2500)
    m2.buy(D, 1.0, 0)
    tfirst = m2.buy(ME, 0.2, 2)  # we are first: nobody to copy
    m2.buy(LEAD, 1.0, 3)
    m2.commit()
    return tl, tme, tsell, tfirst


def test_leaders_are_copy_snipers_only():
    st = Store(":memory:")
    st.set_label("K1", "kol", "kol")
    st.set_label("T1", "tracked", "wallet")
    st.set_label("C1", "cand:C1", "cand")
    st.set_label("S1", "axiom", "service")
    assert leaders_for(st) == set()  # labels alone are not leaders
    st.add_oktech_sniper({"id": "s1", "external_id": "e1", "kind": "copy", "source": "copylist", "name": "copy LEAD arm:paid", "config_hash": "h",
                          "payload": {"ctbotWallets": [{"address": LEAD, "label": "x", "minSol": 1.0}, {"address": "L2"}]}})  # fmt: skip
    # the 4 Nov speed-test probe copied a sniper bot (6Z91…) that itself sits between the leaders and us: a probe row is
    # not a book leader, or every fill behind that bot would be credited to it (audit, 6 Nov: 2 of 6 fills were)
    st.add_oktech_sniper({"id": "p1", "external_id": "e2", "kind": "probe", "source": "6Z91", "name": "speed test: copy 6Z91", "config_hash": "h2",
                          "payload": {"mode": "ctbot", "ctbotWallets": [{"address": "6Z91" + "b" * 40, "label": "speed test leader"}]}})  # fmt: skip
    assert leaders_for(st) == {LEAD, "L2"}
    assert arm_for(st, "s1") == "paid" and arm_for(st, "nope") == "control" and arm_for(st, None) == "control"
    st.close()


ARMS = {"control": {"priority": 0.001, "tip": 0.002}, "paid": {"priority": 0.02, "tip": 0.02}}


def test_arm_from_the_fee_paid_on_chain():
    """Both fee-test arms copy the same leaders from the same wallet and OKTech's task documents carry no fills, so
    the only thing that tells a paid fill from a control fill is the fee it paid: nearest arm by priority fee, tip as
    the tiebreaker (one send in three carries no tip at all)."""
    assert arm_from_fee(20_000_000, 20_000_000, ARMS) == "paid"
    assert arm_from_fee(1_000_000, 2_000_000, ARMS) == "control"
    assert arm_from_fee(20_000_000, 0, ARMS) == "paid"  # untipped paid send
    assert arm_from_fee(1_000_000, 0, ARMS) == "control"
    assert arm_from_fee(None, None, ARMS) == "control" and arm_from_fee(20_000_000, 0, {}) == "control"
    # since 12 Nov an arm is sent with its bid in the tip (priority at OKTech's minimum, fees.sent_bid): a fill of the
    # paid arm then pays 0.001 + 0.039, and is still the paid arm's; the old fills (0.02 + 0.02) stay the paid arm's
    assert arm_from_fee(1_000_000, 39_000_000, ARMS) == "paid"
    assert arm_from_fee(1_000_000, 2_000_000, ARMS) == "control" and arm_from_fee(1_000_000, 0, ARMS) == "control"
    st = Store(":memory:")
    tl, tme, _, _ = _world(st)
    meta = TxMeta(sig=tme.sig, slot=tme.slot, block_time=tme.ts, version=0, fee_payer=ME, signers=[ME], n_signatures=1, fee=5000,
                  priority_lamports=20_000_000, cu_limit=150_000, cu_price=133_333_333, cu_consumed=60_000, tips=[])  # fmt: skip
    assert classify_trade(st, tme, {LEAD}, meta, arms=ARMS)["bid_arm"] == "paid"
    assert classify_trade(st, tme, {LEAD}, meta)["bid_arm"] == "control"  # no arms known: the old default
    assert classify_trade(st, tme, {LEAD}, meta, sniper_id="x", bid_arm="named", arms=ARMS)["bid_arm"] == "named"
    st.close()


def test_tip_shaped_transfers_of_our_own_fills_count_as_tips():
    """The fingerprint keeps unknown destinations as plain transfers (trading bots' platform fees look the same), but
    a fill of ours pays no platform fee on chain: a tip-sized transfer in it is a tip to an uncatalogued relay."""
    from snipetech.measure.classify import fee_fields

    meta = TxMeta(sig="s", slot=1, block_time=1, version=0, fee_payer=ME, signers=[ME], n_signatures=1, fee=5000, priority_lamports=1_000_000,
                  cu_limit=150_000, cu_price=6_666_666, cu_consumed=60_000, tips=[],
                  transfers=[{"src": ME, "dst": "NewRelay" + "1" * 36, "lamports": 2_000_000}, {"src": ME, "dst": "dust", "lamports": 1_000}])  # fmt: skip
    f = fee_fields(meta)
    assert f["tip_lamports"] == 2_000_000 and f["landing_service"] == "relay?"
    meta.tips = [{"dest": "noz", "lamports": 1_000_000, "service": "nozomi"}]
    f = fee_fields(meta)
    assert f["tip_lamports"] == 3_000_000 and f["landing_service"] == "nozomi"


def test_a_copy_fill_records_the_leaders_curve_progress_at_the_leaders_buy():
    """Research round 2 (7 Nov): where a copy lands depends on where on its curve the leader buys (the model's gap was
    +18.5 % on curves under 10 % full, +2.5 % above), and 7aPo3n, buying when the curve was 79-93 % full, was copied 0-1
    trades behind at +0.5-0.8 %. Every copy fill records the leader's curve progress at the leader's buy (the share of
    the 793.1M tokens a curve sells that it had sold), next to trades behind and the price gap; a buy on PumpSwap is 1."""
    st = Store(":memory:")
    _tl, tme, tsell, tfirst = _world(st)
    row = classify_trade(st, tme, {LEAD}, None)
    # 2 SOL into a fresh curve (30 SOL virtual): its virtual token reserve is 1.073e15 * 30 / 32 = 1.0059375e15, so it
    # had sold 67.0625M of its 793.1M tokens
    assert row["leader_progress"] == 0.0846 and row["trades_behind"] == 2 and row["price_gap_pct"] > 0
    assert classify_trade(st, tfirst, {LEAD}, None)["leader_progress"] is None  # nobody bought before us to copy
    assert classify_trade(st, tsell, {LEAD}, None)["leader_progress"] is None  # a sell copies no one
    m = Market(st, addr("m-amm"), D, NOW + 3000, SLOT + 7500)
    m.buy(D, 1.0, 0)
    m.complete(addr("whale"), 2)
    m.migrate(3)
    m.buy(LEAD, 1.0, 5)
    mine = m.buy(ME, 0.2, 6)
    m.commit()
    assert classify_trade(st, mine, {LEAD}, None)["leader_progress"] == 1.0
    st.close()


def test_trades_behind_counts_other_wallets_only():
    """Two arms on one wallet fire on the same leader buy: the second of our own fills must not count the first as a
    trade it landed behind, or the arm that goes second measures worse by construction."""
    st = Store(":memory:")
    m = Market(st, addr("m3"), D, NOW + 5000, SLOT + 9000)
    m.buy(D, 1.0, 0)
    m.buy(LEAD, 1.0, 2)
    m.buy(X, 0.5, 3)
    first = m.buy(ME, 0.2, 4)
    second = m.buy(ME, 0.2, 4)  # same slot, right after our first fill
    m.commit()
    assert classify_trade(st, first, {LEAD}, None)["trades_behind"] == 1
    assert classify_trade(st, second, {LEAD}, None)["trades_behind"] == 1  # X only: our own first fill is not counted
    st.close()


def test_copy_buy_matches_latest_earlier_leader_buy():
    st = Store(":memory:")
    tl, tme, tsell, _ = _world(st)
    meta = TxMeta(sig=tme.sig, slot=tme.slot, block_time=tme.ts, version=0, fee_payer=ME, signers=[ME], n_signatures=1, fee=5000,
                  priority_lamports=1_000_000, cu_limit=200_000, cu_price=5000, cu_consumed=60_000,
                  tips=[{"dest": "T", "lamports": 2_000_000, "service": "jito"}])  # fmt: skip
    row = classify_trade(st, tme, {LEAD}, meta)
    assert row["sig"] == tme.sig and row["side"] == "buy" and row["wallet"] == ME and row["mint"] == addr("m1")
    assert row["wallet_copied"] == LEAD and row["trades_behind"] == 2  # X and Y bought between the leader and us
    assert row["price_gap_pct"] == round(100 * ((tme.q_after / tl.q_after) ** 2 - 1), 1) and row["price_gap_pct"] > 0
    assert row["pos_in_slot"] == 1 and row["slot_txs"] == 2  # Y then us in that slot
    assert row["sol"] == round((tme.quote + tme.fee) / 1e9, 6) and row["tokens"] == tme.base
    assert row["priority_lamports"] == 1_000_000 and row["tip_lamports"] == 2_000_000 and row["cu_limit"] == 200_000
    assert row["cu_used"] == 60_000 and row["landing_service"] == "jito"
    sell = classify_trade(st, tsell, {LEAD}, None)
    assert (
        sell["side"] == "sell"
        and sell["sol"] == round((tsell.quote - tsell.fee) / 1e9, 6)
        and sell["wallet_copied"] is None
    )
    assert sell["priority_lamports"] is None and sell["tip_lamports"] is None and sell["landing_service"] is None
    st.close()


def test_no_leader_before_us_means_no_copy():
    st = Store(":memory:")
    _, _, _, tfirst = _world(st)
    row = classify_trade(st, tfirst, {LEAD}, None)
    assert (
        row["wallet_copied"] is None
        and row["trades_behind"] is None
        and row["price_gap_pct"] is None
        and row["side"] == "buy"
    )
    st.close()


def _b(sig, mint, ts, sol, tokens):
    return {"sig": sig, "mint": mint, "ts": ts, "side": "buy", "sol": sol, "tokens": tokens}


def _s(sig, mint, ts, sol, tokens):
    return {"sig": sig, "mint": mint, "ts": ts, "side": "sell", "sol": sol, "tokens": tokens}


def test_pair_round_trips_fifo_and_partial():
    buys = [_b("B1", "M", 100, 0.2, 1000.0), _b("B2", "M", 200, 0.2, 1000.0), _b("B3", "N", 150, 0.1, 500.0)]
    sells = [_s("S1", "M", 300, 0.15, 500.0), _s("S2", "M", 400, 0.5, 1500.0), _s("S3", "N", 500, 0.2, 500.0)]
    out = pair_round_trips(buys, sells, per_tx_sol=0.001)
    trips = {(t["buy_sig"], t["sell_sig"]): t for t in out["trips"]}
    # S1 closes half of B1: spent 0.1 → received 0.15; S2 closes the other half of B1 (0.1 → 0.1667) and all of B2 (0.2 → 0.3333)
    assert round(trips[("B1", "S1")]["pnl_sol"], 4) == round(0.15 - 0.1 - 0.002, 4)
    assert round(trips[("B1", "S2")]["pnl_sol"], 4) == round(0.5 / 3 - 0.1 - 0.002, 4)
    assert round(trips[("B2", "S2")]["pnl_sol"], 4) == round(0.5 * 2 / 3 - 0.2 - 0.002, 4)
    assert (
        round(trips[("B3", "S3")]["pnl_sol"], 4) == round(0.2 - 0.1 - 0.002, 4)
        and trips[("B3", "S3")]["hold_secs"] == 350
    )
    assert out["unmatched"] == [] and out["open"] == []
    per_sell = out["pnl_by_sell"]
    assert round(per_sell["S2"], 4) == round(trips[("B1", "S2")]["pnl_sol"] + trips[("B2", "S2")]["pnl_sol"], 4)


def test_sell_without_buy_is_left_open():
    out = pair_round_trips([_b("B1", "M", 100, 0.2, 1000.0)], [_s("S0", "N", 50, 0.3, 100.0)], per_tx_sol=0.001)
    assert (
        out["trips"] == []
        and [s["sig"] for s in out["unmatched"]] == ["S0"]
        and [b["sig"] for b in out["open"]] == ["B1"]
    )


def test_verdict_copy_snipers_are_leaders_too():
    st = Store(":memory:")
    st.add_oktech_sniper({"id": "v1", "external_id": "e", "kind": "vcopy", "source": LEAD, "name": "snipetech copy x", "config_hash": "h",
                          "payload": {"ctbotWallets": [{"address": LEAD}]}})  # fmt: skip
    assert leaders_for(st) == {LEAD}
    st.close()


def test_round_trips_charge_the_fees_each_fill_paid():
    """A fill's own priority fee and tip replace the flat per-transaction cost when they are known."""
    b = _b("B1", "M", 100, 0.2, 1000.0) | {"priority_lamports": 20_000_000, "tip_lamports": 20_000_000}
    s = _s("S1", "M", 200, 0.3, 1000.0) | {"priority_lamports": 2_000_000, "tip_lamports": 2_000_000}
    out = pair_round_trips([b], [s], per_tx_sol=0.0012)
    assert round(out["trips"][0]["pnl_sol"], 6) == round(0.3 - 0.2 - (0.040005 + 0.004005), 6)
    flat = pair_round_trips([_b("B1", "M", 100, 0.2, 1000.0)], [_s("S1", "M", 200, 0.3, 1000.0)], per_tx_sol=0.0012)
    assert round(flat["trips"][0]["pnl_sol"], 6) == round(0.3 - 0.2 - 0.0024, 6)  # unknown fees: the flat cost


def test_a_fill_sent_far_above_an_arms_bid_is_not_that_arms_fill():
    """12 Nov 17:24: four buys of a live tip test went out at priority 0.005 + tip 0.03 SOL. The fee is all that tells
    a fill's arm, the nearest arm was the low bid's (0.001 + 0.002), and its measured fee a buy went from 0.003 to
    0.0071 SOL: every lab charged 0.0085 to 0.0093 SOL a transaction for a week instead of about 0.0042. A fill whose
    priority fee or whose whole bid is more than twice the nearest arm's is no arm's fill: it is another bid's."""
    from snipetech.measure.classify import OTHER_BID

    only = {"control": {"priority": 0.001, "tip": 0.002}}
    assert arm_from_fee(5_000_000, 30_000_000, only) == OTHER_BID
    assert arm_from_fee(5_000_000, 0, only) == OTHER_BID  # an untipped send of such a buy, or its failed sell twin
    assert arm_from_fee(1_000_000, 30_000_000, only) == OTHER_BID  # the same bid sent in the tip
    assert arm_from_fee(1_000_000, 2_000_000, only) == "control" and arm_from_fee(1_000_000, 0, only) == "control"
    assert arm_from_fee(700_000, 1_333_333, only) == "control"  # a smaller bid than the arm's is still the low bid
    assert arm_from_fee(2_000_000, 4_000_000, only) == "control"  # up to twice the arm's bid (a setting moved since)
    assert arm_from_fee(2_000_001, 0, only) == OTHER_BID
    # where the retired paid arm is still written (every settings file loads it: config merges [book] bid_arms over the
    # defaults), the test bid is nearest the paid arm as it is SENT (0.001 + 0.039) and is held to that shape: a
    # priority of 0.005 is five times its 0.001, so it is no arm's. The paid arm's own fills stay the paid arm's.
    assert arm_from_fee(5_000_000, 30_000_000, ARMS) == OTHER_BID
    assert arm_from_fee(5_000_000, 0, ARMS) == OTHER_BID
    assert arm_from_fee(20_000_000, 20_000_000, ARMS) == "paid" and arm_from_fee(1_000_000, 39_000_000, ARMS) == "paid"
    assert arm_from_fee(20_000_000, 0, ARMS) == "paid"
    assert arm_from_fee(50_000_000, 100_000_000, ARMS) == OTHER_BID
    assert arm_from_fee(None, None, only) == "control"  # an unknown fee, as before


# -- the copied wallet's own pack is nobody ahead of us (19 Nov 2026) -------------------------------------------------

PAYER, PACK_A, PACK_B, PACK_C = addr("pack payer"), addr("pack a"), addr("pack b"), addr("pack c")


def _pack_tape(st):
    """The tape of 19 Nov in a store (tests/test_lab.py: four transactions back to back at block places 440 to 443,
    fifteen buys, 63 SOL; 8D92Hh is the second buyer of the first, and the first outsider, CWNYbh, buys 2.7 SOL a
    block later)."""
    from tests.test_lab import _pack_19nov

    trades = _pack_19nov()
    st.add_trades(trades)
    assert trades[1].user.startswith("8D92Hh") and trades[1].pos == 440 and trades[15].pos == 443
    return trades


def _our_buy(st, trades, slot, pos):
    """A 0.2 SOL buy of ours at block place `pos` of `slot`, into the pool as the last trade ahead of it left it."""
    before = [t for t in trades if (t.slot, t.pos) < (slot, pos)][-1]
    q = before.q_after + 200_000_000
    b = before.q_after * before.b_after // q
    ours = Trade(sig=f"our copy at {slot}:{pos}", ev=0, slot=slot, pos=pos, ts=before.ts, mint=before.mint,
                 venue="amm", user=ME, is_buy=True, quote=200_000_000, base=before.b_after - b, fee=2_400_000,
                 fee_bps=120, q_after=q, b_after=b)  # fmt: skip
    st.add_trades([ours])
    return ours


def _block(st, name, rows, ours_at):
    """A coin whose block at slot +50 holds `rows` in order, each (wallet, SOL, the transaction it is in or None for
    one of its own, block place), then a 0.2 SOL buy of ours at block place `ours_at`. Returns our trade."""
    m = Market(st, addr(name), D, NOW + 9000, SLOT + 20_000)
    m.buy(D, 1.0, 0)
    for who, sol, tx, place in rows:
        m.buy(who, sol, 50, sig=addr(f"{name}:{tx}") if tx else None).pos = place
    mine = m.buy(ME, 0.2, 50)
    mine.pos = ours_at
    m.commit()
    return mine


def test_the_copied_wallets_own_pack_is_not_counted_as_trades_behind_it():
    """19 Nov: 8D92Hh's buy is the second of four in its transaction, and three more transactions of the same
    operator run right behind it: fifteen buys, 63 SOL, block places 440 to 443. A buy of ours put in the very next
    place of that tape (444) is first behind the wallet, the best place there is. A review found the measure stage
    writes "12 trades behind" for it, where the paper model says none, and the lesson read off such a row is "too
    slow" or "bid more". The pack is the copied wallet's own entry (CLAUDE.md rule 2: one operator's own buys are
    never buyers ahead of us): its buys are left out of trades_behind, and the row says how many they were."""
    st = Store(":memory:")
    trades = _pack_tape(st)
    lead = trades[1]
    ours = _our_buy(st, trades, lead.slot, 444)
    row = classify_trade(st, ours, {lead.user}, None)
    assert row["wallet_copied"] == lead.user and row["price_gap_pct"] > 0
    assert row["trades_behind"] == 0 and row["pack_ahead"] == 12
    st.close()


def test_an_outsider_behind_the_pack_is_still_a_trade_behind():
    """19 Nov, the same block. CWNYbh is no wallet of the pack: it buys 2.7 SOL a block later, alone in its
    transaction. A copy of ours behind that buy is one trade behind the copied wallet (13 before the pack was left
    out): only the pack is left out, never an outsider behind it."""
    st = Store(":memory:")
    trades = _pack_tape(st)
    lead, outsider = trades[1], trades[16]
    assert outsider.user.startswith("CWNYbh") and outsider.slot == lead.slot + 1
    ours = _our_buy(st, trades, outsider.slot, outsider.pos + 1)
    row = classify_trade(st, ours, {lead.user}, None)
    assert row["trades_behind"] == 1 and row["pack_ahead"] == 12
    st.close()


def test_a_pack_wallets_second_buy_in_that_block_is_no_trade_behind_either():
    """19 Nov, as the lab counts a place since that day (lab.copy_entry, pack_buyers): whoever bought in the copied
    wallet's own transaction, the fee payer's small buy ahead of it included, or in a transaction of its pack, is its
    operator. A second buy of such a wallet later in that block is the operator buying again, not an outsider who
    beat us. Here the copied wallet's transaction holds three wallets' buys and a transaction of two follows it; the
    payer and one wallet of the second transaction buy again, then one outsider, then us: 1 trade behind, not 5."""
    st = Store(":memory:")
    rows = [(PAYER, 0.01, "tx1", 100), (LEAD, 2.0, "tx1", 100), (PACK_A, 1.0, "tx1", 100),
            (PACK_B, 1.0, "tx2", 101), (PACK_C, 1.0, "tx2", 101),
            (PAYER, 0.3, None, 110), (PACK_B, 0.3, None, 111), (X, 0.5, None, 112)]  # fmt: skip
    mine = _block(st, "pack-again", rows, 113)
    row = classify_trade(st, mine, {LEAD}, None)
    # left out: the two buys of the second transaction and the two second buys; counted: the outsider alone
    assert row["trades_behind"] == 1 and row["pack_ahead"] == 4
    st.close()


def test_a_buyer_alone_in_the_next_transaction_and_a_transaction_further_on_are_still_counted():
    """19 Nov. What is no pack is counted as before. One wallet's transaction in the very next block place is no
    link: a fast bot stands there as often as the operator's own wallet (analysis.companions tells those apart from
    habit, the pack rule does not). And a transaction of two wallets that is not in the very next place is somebody
    else's. Both rows say that no pack was left out: 0, a known number."""
    st = Store(":memory:")
    fast = _block(st, "fast-bot", [(LEAD, 2.0, None, 100), (X, 0.5, None, 101)], 102)
    row = classify_trade(st, fast, {LEAD}, None)
    assert row["trades_behind"] == 1 and row["pack_ahead"] == 0
    far = _block(st, "far-pair", [(LEAD, 2.0, None, 100), (X, 0.5, "pair", 102), (Y, 0.5, "pair", 102)], 103)
    row = classify_trade(st, far, {LEAD}, None)
    assert row["trades_behind"] == 2 and row["pack_ahead"] == 0
    st.close()


def test_a_fill_that_copied_nobody_says_nothing_of_a_pack():
    """19 Nov. `pack_ahead` is a count only where a copied buy was found. A buy that copied nobody and a sell carry
    None, as they carry no trades_behind: not known is never written as 0."""
    st = Store(":memory:")
    _tl, tme, tsell, tfirst = _world(st)
    assert classify_trade(st, tme, {LEAD}, None)["pack_ahead"] == 0  # copied LEAD, who bought alone
    assert classify_trade(st, tfirst, {LEAD}, None)["pack_ahead"] is None  # nobody bought before us to copy
    assert classify_trade(st, tsell, {LEAD}, None)["pack_ahead"] is None  # a sell copies no one
    st.close()


MATE = addr("mate")


def test_known_companions_of_the_copied_wallet_are_no_trades_behind_on_the_tape_either():
    """CLAUDE.md rule 2: a copied wallet's companions are never buyers ahead of us. The chain reading left them out
    (chain_landing.read_landing: companions_ahead) and the tape reading counted them, so two different counts went
    into one measured landing (a review, 19 Nov: trades_behind=1 behind a known companion). Now the tape reading
    leaves them out as the chain reading does, in the copied buy's block and after it. Companions the tape cannot
    name (None: too few of the wallet's buys) leave the count as it was."""
    st = Store(":memory:")
    m = Market(st, addr("mates"), D, NOW + 20_000, SLOT + 40_000)
    m.buy(D, 1.0, 0)
    m.buy(LEAD, 2.0, 2)
    m.buy(MATE, 1.0, 2)  # alone in its own transaction right behind the copied buy: a companion, no pack
    m.buy(X, 0.5, 2)
    m.buy(MATE, 0.5, 3)  # the companion again, a block later
    mine = m.buy(ME, 0.2, 3)
    m.commit()
    known = classify_trade(st, mine, {LEAD}, None, mates=lambda w: {MATE} if w == LEAD else set())
    assert known["trades_behind"] == 1 and known["pack_ahead"] == 0
    assert classify_trade(st, mine, {LEAD}, None, mates=lambda w: None)["trades_behind"] == 3
    assert classify_trade(st, mine, {LEAD}, None)["trades_behind"] == 3
    st.close()


def test_a_companion_in_the_pack_is_left_out_once():
    """A companion that bought in the copied wallet's pack is one of the pack's buys (pack_ahead counts it, as the
    chain reading lists it under pack_ahead first) and is not taken off trades_behind a second time."""
    st = Store(":memory:")
    rows = [(LEAD, 2.0, "tx1", 100), (MATE, 1.0, "tx1", 100), (PACK_B, 1.0, "tx2", 101), (MATE, 1.0, "tx2", 101),
            (X, 0.5, None, 102)]  # fmt: skip
    mine = _block(st, "mate-in-pack", rows, 103)
    row = classify_trade(st, mine, {LEAD}, None, mates=lambda w: {MATE})
    assert row["trades_behind"] == 1 and row["pack_ahead"] == 2
    st.close()


def test_a_buy_of_ours_inside_the_copied_wallets_pack_is_counted_plainly_and_said():
    """A review, 19 Nov: the lab's premise is that no copy lands inside a pack (between the transactions one operator
    sends together). A fill of ours that stood inside one is the only live evidence against it, so it is not
    explained away: trades_behind counts every buy ahead of it as before, pack_ahead says how many of them were the
    pack's, and landing_verdict says "inside". Here the copied wallet's transaction and one more of its pack run
    ahead of us, and a transaction of two wallets' buys stands right behind ours, where the pack would have gone on
    had our buy not stood there. A one-wallet transaction behind ours is no link, and nothing behind ours is no pack
    left: those buys of ours are behind the pack."""
    st = Store(":memory:")
    pack = [(LEAD, 2.0, "tx1", 100), (PACK_A, 1.0, "tx1", 100), (PACK_B, 1.0, "tx2", 101), (PACK_C, 1.0, "tx2", 101)]
    inside = _block(st, "inside", [*pack, (PAYER, 0.5, "tx3", 103), (X, 0.5, "tx3", 103)], 102)
    row = classify_trade(st, inside, {LEAD}, None)
    assert row["trades_behind"] == 2 and row["pack_ahead"] == 2 and row["landing_verdict"] == "inside"
    alone = _block(st, "alone-behind", [*pack, (X, 0.5, None, 103)], 102)
    row = classify_trade(st, alone, {LEAD}, None)
    assert row["trades_behind"] == 0 and row["pack_ahead"] == 2 and row["landing_verdict"] is None
    behind = _block(st, "behind", pack, 102)
    row = classify_trade(st, behind, {LEAD}, None)
    assert row["trades_behind"] == 0 and row["pack_ahead"] == 2 and row["landing_verdict"] is None
    st.close()
