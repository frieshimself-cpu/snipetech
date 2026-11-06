"""Fill classification: the copied wallet's buy before ours, trades behind, price gap, position in the slot,
fees from the fingerprint, FIFO round trips."""

from snipetech.measure.classify import arm_for, classify_trade, leaders_for, pair_round_trips
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


def test_leaders_from_snipers_else_labels():
    st = Store(":memory:")
    st.set_label("K1", "kol", "kol")
    st.set_label("T1", "tracked", "wallet")
    st.set_label("C1", "cand:C1", "cand")
    st.set_label("S1", "axiom", "service")
    assert leaders_for(st) == {"K1", "T1", "C1"}
    st.add_oktech_sniper({"id": "s1", "external_id": "e1", "kind": "ctbot", "source": "copylist", "name": "copy LEAD arm:paid", "config_hash": "h",
                          "payload": {"ctbotWallets": [{"address": LEAD, "label": "x", "minSol": 1.0}, {"address": "L2"}]}})  # fmt: skip
    assert leaders_for(st) == {LEAD, "L2"}
    assert arm_for(st, "s1") == "paid" and arm_for(st, "nope") == "control" and arm_for(st, None) == "control"
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
