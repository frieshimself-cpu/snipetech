"""Fill classification: the copied wallet's buy before ours, trades behind, price gap, position in the slot,
fees from the fingerprint, FIFO round trips."""

from snipetech.measure.classify import arm_for, arm_from_fee, classify_trade, leaders_for, pair_round_trips
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
