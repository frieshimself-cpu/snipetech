"""Where a live buy of ours landed, read from the chain itself (12 Nov 2026).

The tip ladder behind Cupsey started at 17:03 UTC and its first two copies bought a coin the tape does not hold (not
on a pump.fun curve, not a coin that left one in the last 30 minutes): the measure stage wrote nothing about where
they landed. The owner: "make sure the fill analysis is accurate, because if someone is using more fees than us then
they get in faster". The chain reader takes one buy of ours and the wallet it copied and reads the blocks between the
two: who bought the coin in between, at what bid, who tried and failed. It reads balances, never a swap instruction,
so it works on any program."""

import asyncio
import json
from pathlib import Path

import pytest

from snipetech.measure import chain_landing as CL
from snipetech.solana.programs import PUMP_AMM_PROGRAM, PUMP_PROGRAM

from .chain import NOZOMI, Chain, key, noise, sig, swap

FIX = Path(__file__).parent / "fixtures"
ME, ME2, LEAD, MATE = key("our wallet"), key("our second wallet"), key("the copied wallet"), key("its companion")
RICH, FAST, LOSER = key("a bot that bids high"), key("a bot that bids low"), key("a bot whose buy failed")
COIN = key("a coin on some other program")
S = 480_000_100


def _run(coro):
    return asyncio.run(coro)


def _busy_block(chain: Chain, slot: int = S, our_slot: int | None = None, mine: dict | None = None) -> str:
    """The copied wallet buys 3 SOL of COIN; behind it in the block: its companion, a bot at a higher bid than ours,
    a bot whose buy fails, a bot at a lower bid than ours, our second wallet, then our own buy (0.005 priority + 0.03
    tip, the ladder's first arm), then a later buyer. Returns our buy's signature."""
    chain.put(slot, noise("n0", key("somebody")))
    chain.put(slot, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(slot, swap("mate", MATE, COIN, tokens=900_000_000, sol=1.0, priority=0.002))
    chain.put(slot, noise("n1", key("somebody else")))
    chain.put(slot, swap("rich", RICH, COIN, tokens=1_800_000_000, sol=2.0, priority=0.05, tip=0.3))
    chain.put(slot, swap("loser", LOSER, COIN, tokens=500_000_000, sol=0.5, priority=0.01, tip=0.02,
                         err={"InstructionError": [4, {"Custom": 6002}]}))  # fmt: skip
    chain.put(slot, swap("fast", FAST, COIN, tokens=400_000_000, sol=0.5, priority=0.0001, tip=0.001))
    chain.put(slot, swap("ours2", ME2, COIN, tokens=40_000_000, sol=0.05, priority=0.001, tip=0.002))
    ours = mine or swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03)
    chain.put(our_slot or slot, ours)
    chain.put(our_slot or slot, swap("late", key("a late buyer"), COIN, tokens=300_000_000, sol=0.4, priority=0.2))
    return ours["transaction"]["signatures"][0]


def test_our_buy_is_placed_behind_the_copied_wallet_in_its_own_block():
    chain = Chain()
    ours = _busy_block(chain)
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    assert r["wallet"] == ME and r["mint"] == COIN and r["side"] == "buy" and not r["failed"]
    assert r["slot"] == S and r["ts"] == chain.ts(S)
    # what we paid and got, off our own balances; the fees are the ones the fills table keeps
    assert r["sol"] == pytest.approx(0.05) and r["tokens"] == 38_000_000
    assert r["priority_lamports"] == 5_000_000 and r["tip_lamports"] == 30_000_000 and r["our_bid_sol"] == 0.035
    # the copied wallet's buy: found in its signatures, in the same block
    assert r["leader"] == LEAD and r["leader_buy"]["sig"] == sig("lead") and r["leader_buy"]["slot"] == S
    assert r["leader_buy"]["sol"] == pytest.approx(3.0) and r["leader_buy"]["bid_sol"] == 0.003
    assert r["blocks_after"] == 0 and r["slots_after"] == 0 and r["complete"]
    # two outsiders bought between the wallet and us: the place counts them, not the companion, not our own wallet,
    # not the buy that failed
    assert r["place"] == 2
    ahead = r["buyers_ahead"]
    assert [b["wallet"] for b in ahead] == [RICH, FAST]
    assert [b["bid_sol"] for b in ahead] == [0.35, 0.0011]
    assert [b["sol"] for b in ahead] == [pytest.approx(2.0), pytest.approx(0.5)]
    assert [b["against_ours"] for b in ahead] == ["more", "less"]
    assert r["highest_bid_ahead_sol"] == 0.35 and r["median_bid_ahead_sol"] == pytest.approx(0.17555)
    # the owner's thesis asked of this fill: one buyer ahead bid less than we did and still got in first
    assert r["bid_more"] == 1 and r["bid_less"] == 1 and r["verdict"] == "speed"
    assert [c["wallet"] for c in r["companions_ahead"]] == [MATE] and r["companions"] == "known"
    assert [c["wallet"] for c in r["own_ahead"]] == [ME2]
    # who tried: the failed buy between the wallet and us, with what it bid
    assert [(f["wallet"], f["bid_sol"]) for f in r["failed_ahead"]] == [(LOSER, 0.03)]
    # our price against the wallet's, from the pool's own side of each buy (SOL in over coins out)
    lead_price, our_price = 3.0 * 0.99 / 3_000_000_000, 0.05 * 0.99 / 38_000_000
    assert r["price_gap_pct"] == pytest.approx((our_price / lead_price - 1) * 100, abs=0.05)
    assert r["price_from"] == "pool"
    assert r["venue"] == "other" and r["program"] == key("some amm")


def test_a_buy_two_blocks_later_counts_the_blocks_that_exist_and_every_buyer_in_between():
    chain = Chain()
    chain.put(S + 1, swap("mid", key("a buyer a block later"), COIN, tokens=100_000_000, sol=0.2, priority=0.004))
    ours = _busy_block(chain, our_slot=S + 3)  # no block at S + 2: the leader of that slot produced none
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    assert r["blocks_after"] == 2 and r["slots_after"] == 3 and r["complete"] and r["blocks_read"] == 3
    assert [b["wallet"] for b in r["buyers_ahead"]] == [RICH, FAST, key("a buyer a block later")]
    assert [b["blocks_after"] for b in r["buyers_ahead"]] == [0, 0, 1]
    assert r["place"] == 3 and r["verdict"] == "speed"
    assert ("getBlock", S + 2) not in chain.calls


def test_a_gap_of_more_than_five_blocks_is_said_and_the_count_is_a_floor():
    chain = Chain()
    for k in range(1, 9):
        chain.put(S + k, swap(f"mid{k}", key(f"buyer {k}"), COIN, tokens=100_000_000, sol=0.2, priority=0.004))
    ours = _busy_block(chain, our_slot=S + 9)
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    assert r["blocks_after"] == 9 and not r["complete"]
    assert r["blocks_read"] == CL.MAX_BLOCKS and r["blocks_unread"] == 5
    assert len([c for c in chain.calls if c[0] == "getBlock"]) == CL.MAX_BLOCKS
    # the leader's block and the three after it, and our own: the buyers of the unread blocks are not counted
    assert r["place"] == 5 and "at least" in CL.render([r])


def test_first_behind_the_wallet_and_every_buyer_ahead_bidding_more():
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    first = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert first["place"] == 0 and first["buyers_ahead"] == [] and first["verdict"] == "first"
    assert first["highest_bid_ahead_sol"] is None and first["median_bid_ahead_sol"] is None

    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("rich", RICH, COIN, tokens=1_800_000_000, sol=2.0, priority=0.05, tip=0.3))
    chain.put(S, swap("rich2", key("another high bidder"), COIN, tokens=900_000_000, sol=1.0, priority=0.04))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    fees = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert fees["place"] == 2 and fees["bid_more"] == 2 and fees["bid_less"] == 0 and fees["verdict"] == "fees"


def test_a_payment_nobody_catalogued_leaves_the_comparison_open():
    """A buyer ahead of us paid 0.2 SOL to an account that is no known landing service: a tip to a relay nobody listed,
    or a trading bot's own fee. Its known bid is under ours, its bid with that payment over: neither thesis is said."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("odd", FAST, COIN, tokens=400_000_000, sol=0.5, priority=0.001, other=0.2))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    b = r["buyers_ahead"][0]
    assert b["bid_sol"] == 0.001 and b["other_sol"] == 0.2 and b["against_ours"] == "unclear"
    assert b["sol"] == pytest.approx(0.5)  # the payment is no part of what it paid for the coins
    assert r["verdict"] == "unclear" and r["bid_unclear"] == 1


def test_the_venue_is_named_when_it_is_one_we_know():
    for program, native, name in ((PUMP_PROGRAM, True, "pump.fun curve"), (PUMP_AMM_PROGRAM, False, "PumpSwap")):
        chain = Chain()
        chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, program=program, native=native))
        chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03, program=program,
                          native=native))  # fmt: skip
        r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=None, own={ME}))
        assert r["venue"] == name and r["program"] == program
        assert r["sol"] == pytest.approx(0.05) and r["price_from"] == "pool"
        assert r["companions"] == "unknown"  # the tape knows nothing of this wallet's companions


def test_the_copied_wallets_buy_is_found_when_the_rpc_wants_its_own_signature_as_the_cursor():
    chain = Chain(foreign_before=False)
    ours = _busy_block(chain)
    chain.put(S + 40, noise("later", LEAD))
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    assert r["leader_buy"]["sig"] == sig("lead") and r["place"] == 2


def test_no_buy_of_the_copied_wallet_is_said_and_our_own_side_is_still_read():
    chain = Chain()
    chain.put(S - 500, swap("old", LEAD, COIN, tokens=3_000_000_000, sol=3.0))  # long before: not what we copied
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["leader_buy"] is None and r["place"] is None and r["blocks_after"] is None and r["verdict"] is None
    assert "no buy" in r["leader_note"] and LEAD[:6] in r["leader_note"]
    assert r["sol"] == pytest.approx(0.05) and r["our_bid_sol"] == 0.035 and r["venue"] == "other"
    assert not [c for c in chain.calls if c[0] == "getBlock"]


def test_a_copy_that_landed_ahead_of_the_wallet_it_copied_is_said():
    """With preconfirmations a copy can be sent on a buy still in flight and land before it."""
    chain = Chain()
    chain.put(S, swap("ours", ME, COIN, tokens=60_000_000, sol=0.05, priority=0.005, tip=0.03))
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["leader_buy"]["sig"] == sig("lead") and r["ahead_of_leader"] and r["place"] == 0
    assert r["blocks_after"] == 0 and r["buyers_ahead"] == [] and r["verdict"] == "ahead"
    assert r["price_gap_pct"] < 0


def test_several_copied_wallets_the_one_that_bought_the_coin_is_the_leader():
    chain = Chain()
    ours = _busy_block(chain)
    other = key("another copied wallet")
    chain.put(S - 3, noise("x", other))
    r = _run(CL.read_landing(chain, ours, [other, LEAD], mates={MATE}, own={ME, ME2}))
    assert r["leader"] == LEAD and r["place"] == 2


def test_a_failed_send_of_ours_is_read_as_failed():
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03,
                      err={"InstructionError": [4, {"Custom": 1}]}))  # fmt: skip
    r = _run(CL.read_landing(chain, sig("ours"), LEAD, mates=set(), own={ME}))
    assert r["failed"] and r["priority_lamports"] == 5_000_000 and r["leader_buy"] is None
    assert _run(CL.read_landing(chain, sig("nothing"), LEAD))["error"] == "transaction not found"


def test_one_wallets_side_of_a_transaction_another_wallet_paid_for():
    """A buy whose fee another wallet paid (FOMO pays its traders' gas): the buyer is the signer whose coins went up,
    and the fee is no part of what it paid for them."""
    tx = swap("fomo", FAST, COIN, tokens=400_000_000, sol=0.5, priority=0.002, payer=key("a gas wallet"))
    tx |= {"slot": S, "blockTime": 1}
    fill = CL.read_fill(tx, FAST)
    assert fill["side"] == "buy" and fill["sol"] == pytest.approx(0.5) and fill["tokens"] == 400_000_000
    assert CL.read_fill(tx, key("a gas wallet")) is None  # it traded nothing
    assert CL.buyers(tx, COIN) == [FAST]


def test_our_real_pumpswap_buy_of_10_nov_reads_as_the_tape_recorded_it():
    """A real getTransaction answer: 0.2 SOL of wrapped SOL into a PumpSwap pool through OKTech, 0.001 priority and a
    0.002 tip to Helius Sender, a new token account (its rent is no part of the buy)."""
    tx = json.loads((FIX / "pumpswap_buy_landed_10nov.json").read_text())
    me = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
    fill = CL.read_fill(tx, me)
    assert fill["side"] == "buy" and fill["mint"] == "4RK13gb4Kpxrk5jgAd8kUw3FRCJmbw7sH1765Gokpump"
    assert fill["sol"] == 0.2 and fill["tokens"] == 167_448_120_010
    assert fill["priority_lamports"] == 1_000_000 and fill["tip_lamports"] == 2_000_000
    assert fill["landing_service"] == "helius-sender" and fill["venue"] == "PumpSwap"
    assert fill["pool_sol"] == pytest.approx(0.199950593) and fill["slot"] == 467_886_134


@pytest.mark.parametrize("prefix,side", [("48YmS3mVdw", "buy"), ("xqo6FRzqCx", "buy"), ("4Pdik2uuPb", "sell")])
def test_our_real_curve_trades_read_off_their_balances_match_their_pump_events(prefix, side):
    """Real pump.fun curve trades of ours (6 Nov): what the balances say we paid or got is what the trade event says
    (the quote and pump.fun's fees), to the lamport, without reading the event."""
    from snipetech.solana.tx import extract_from_transaction

    tx = json.loads(next((FIX / "tx").glob(f"{prefix}*.json")).read_text())
    me = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"
    ev = next(e for e in extract_from_transaction(tx).events if e.name == "TradeEvent").fields
    fees = int(ev.get("fee") or 0) + int(ev.get("creator_fee") or 0)
    want = ev["sol_amount"] + fees if side == "buy" else ev["sol_amount"] - fees
    fill = CL.read_fill(tx, me)
    assert fill["side"] == side and fill["mint"] == ev["mint"] and fill["tokens"] == ev["token_amount"]
    assert round(fill["sol"] * 1e9) == want
    # the pool's side is the curve's own account: the quote, and the fees pump.fun keeps inside it until they are
    # claimed (the creator's, half of its own): the same share on every trade, so a price ratio is not moved by them
    assert fill["venue"] == "pump.fun curve" and fill["pool_sol"] == pytest.approx(ev["sol_amount"] / 1e9, rel=0.01)
    assert fill["price_from"] == "pool"


def test_the_fills_columns_a_chain_reading_gives():
    chain = Chain()
    ours = _busy_block(chain)
    r = _run(CL.read_landing(chain, ours, LEAD, mates={MATE}, own={ME, ME2}))
    cols = CL.fill_fields(r)
    assert cols["landing_src"] == "chain" and cols["wallet_copied"] == LEAD
    assert cols["trades_behind"] == 2 and cols["price_gap_pct"] == r["price_gap_pct"] and cols["blocks_after"] == 0
    assert cols["ahead_bid_max"] == 350_000_000 and cols["ahead_bid_p50"] == 175_550_000
    assert cols["landing_verdict"] == "speed" and cols["venue"] == f"other:{key('some amm')}"
    assert json.loads(cols["ahead_bids"]) == [{"w": RICH, "bid": 350_000_000, "sol": 2.0, "vs": "more"},
                                              {"w": FAST, "bid": 1_100_000, "sol": 0.5, "vs": "less"}]  # fmt: skip


def test_every_buy_of_a_wallet_in_a_window_and_the_table():
    chain = Chain()
    ours = _busy_block(chain)
    coin2 = key("a second coin")
    chain.put(S + 200, swap("lead-b", LEAD, coin2, tokens=2_000_000_000, sol=2.0, priority=0.002))
    chain.put(S + 200, swap("ours-b", ME, coin2, tokens=20_000_000, sol=0.05, priority=0.005, tip=0.1))
    chain.put(S + 300, swap("ours-b-sell", ME, coin2, tokens=-20_000_000, sol=0.06, priority=0.005, tip=0.001,
                            held=20_000_000))  # fmt: skip
    chain.put(S + 400, noise("ours-transfer", ME))
    rows = _run(CL.wallet_landings(chain, ME, LEAD, since_ts=chain.ts(S) - 60, until_ts=chain.ts(S + 500),
                                   mates={MATE}, own={ME, ME2}))  # fmt: skip
    assert [r["sig"] for r in rows] == [ours, sig("ours-b")]  # oldest first; the sell and the transfer are no buys
    assert rows[1]["place"] == 0 and rows[1]["our_bid_sol"] == 0.105 and rows[1]["verdict"] == "first"
    # the sell that closed the second buy: what it brought back, less every fee of the two
    assert rows[1]["sold_sol"] == pytest.approx(0.06) and rows[0]["sold_sol"] is None
    assert rows[1]["result_sol"] == pytest.approx(0.06 - 0.05 - 0.105 - 0.006 - 2 * 5e-6)
    text = CL.render(rows)
    lines = text.splitlines()
    assert "blocks after" in lines[0] and "buyers ahead" in lines[0] and "highest" in lines[0]
    row = next(ln for ln in lines if COIN[:6] in ln)
    assert "0.035" in row and "0.35" in row and f"{RICH[:6]}…: 0.35" in row and f"{FAST[:6]}…: 0.0011" in row
    assert "speed" in row
    assert NOZOMI not in text


def test_failed_sends_of_ours_on_a_coin_are_counted_against_its_buy():
    """OKTech sends every exit more than once and the copies that lose fail and keep their priority fee: a failed
    send of ours that names the coin after the buy is that buy's cost."""
    chain = Chain()
    chain.put(S, swap("lead", LEAD, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=0.05, priority=0.005, tip=0.03))
    chain.put(S + 50, swap("sell", ME, COIN, tokens=-38_000_000, sol=0.04, priority=0.005, tip=0.001, held=38_000_000))
    chain.put(S + 51, swap("twin", ME, COIN, tokens=-38_000_000, sol=0.04, priority=0.005, tip=0.001,
                           err={"InstructionError": [3, {"Custom": 6023}]}))  # fmt: skip
    rows = _run(CL.wallet_landings(chain, ME, LEAD, since_ts=chain.ts(S) - 60, until_ts=chain.ts(S + 500), own={ME}))
    assert len(rows) == 1 and rows[0]["failed_sends"] == 1 and rows[0]["failed_cost_sol"] == pytest.approx(0.005005)
    assert rows[0]["result_sol"] == pytest.approx(0.04 - 0.05 - 0.035 - 0.006 - 2 * 5e-6 - 0.005005)
    assert "1 failed sends of ours" in CL.render(rows)


def test_the_rpc_client_asks_for_whole_blocks_and_for_the_blocks_that_exist():
    """getBlock with every transaction in full (their balances and instructions, no rewards), the version raised when
    the node says a transaction needs it, None for a slot that has no block; getBlocks for the slots that have one."""
    import httpx

    from snipetech.solana.rpc import SolanaRpc

    asked = []

    def answer(**kw) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, **kw})

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        asked.append((body["method"], body["params"]))
        if body["method"] == "getBlocks":
            return answer(result=[S, S + 1, S + 3])
        slot, opts = body["params"]
        if slot == S + 2:
            return answer(error={"code": -32007, "message": f"Slot {slot} was skipped, or missing due to ledger jump"})
        if opts["maxSupportedTransactionVersion"] < 1:
            return answer(error={"code": -32015, "message": "Transaction version (1) is not supported by the "
                                 'requesting client. Please try the request again with the following configuration '
                                 'parameter: "maxSupportedTransactionVersion": 1'})  # fmt: skip
        return answer(result={"blockTime": 7, "transactions": []})

    async def run():
        rpc = SolanaRpc("http://rpc.test", max_rps=500, max_retries=1, commitment="processed")
        await rpc.client.aclose()
        rpc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        rpc.max_tx_version = 0
        out = await rpc.get_block(S), await rpc.get_block(S + 2), await rpc.get_blocks(S, S + 3), rpc.max_tx_version
        await rpc.close()
        return out

    block, skipped, slots, version = asyncio.run(run())
    assert block == {"blockTime": 7, "transactions": []} and skipped is None and slots == [S, S + 1, S + 3]
    assert version == 1
    blocks = [params for method, params in asked if method == "getBlock"]
    assert [p[0] for p in blocks] == [S, S, S + 2]  # the version raised once; a slot without a block asked once
    opts = blocks[1][1]
    assert opts["transactionDetails"] == "full" and opts["rewards"] is False and opts["encoding"] == "json"
    assert opts["commitment"] == "confirmed"  # a block is never asked for at "processed": the node refuses that
    assert asked[-1] == ("getBlocks", [S, S + 3, {"commitment": "confirmed"}])


def test_the_landing_command_reads_one_buy_or_a_window_and_the_agents_may_start_it(tmp_path, capsys, monkeypatch):
    import time

    from snipetech import cli
    from snipetech.operator.jobs import OWNER_COMMANDS, RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED
    from snipetech.service import Service
    from snipetech.store import Store

    assert "landing" in ALLOWED and "landing" in RESEARCH_COMMANDS and "landing" in cli.BATCH_COMMANDS
    assert "landing" not in OWNER_COMMANDS
    chain = Chain(t0=int(time.time()) - 600, slot0=S)
    ours = _busy_block(chain)
    monkeypatch.setattr(Service, "rpc", property(lambda self: chain))
    db = tmp_path / "snipetech.db"
    st = Store(db)
    st.set_label(ME2, "the second sniper wallet", "own")
    st.set_label(RICH, "the rich bot", "wallet")
    st.close()
    base = ["--db", str(db), "landing", "--leader", LEAD]
    assert cli.main([*base, "--sig", ours, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    (row,) = out["rows"]
    # an empty tape knows nothing of the copied wallet's companions: said, and the wallet behind it counts as a buyer
    assert row["companions"] == "unknown" and [b["wallet"] for b in row["buyers_ahead"]] == [MATE, RICH, FAST]
    assert row["buyers_ahead"][1]["label"] == "the rich bot" and [o["wallet"] for o in row["own_ahead"]] == [ME2]
    assert row["our_bid_sol"] == 0.035 and row["highest_bid_ahead_sol"] == 0.35 and row["verdict"] == "speed"
    # every buy of a wallet in the last minutes, as the table
    assert cli.main([*base, "--wallet", ME, "--since-minutes", "30"]) == 0
    text = capsys.readouterr().out
    assert "buyers ahead" in text and f"{COIN[:6]}…" in text and "0.35" in text and "companions are unknown" in text
    # no --leader: the wallets our copy snipers copied when each buy was made
    st = Store(db)
    st.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": LEAD, "name": "copy",
                          "config_hash": "h", "payload": {"ctbotWallets": [{"address": LEAD}]},
                          "created_at": int(time.time()) - 3600})  # fmt: skip
    st.close()
    assert cli.main(["--db", str(db), "landing", "--wallet", ME, "--since-minutes", "30", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["leader"] == LEAD
    assert cli.main(["--db", str(db), "landing", "--sig", ours, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["leader_buy"]["sig"] == sig("lead")
    # the wallet is the operator's own when none is given; without one, and without a signature, nothing to read
    assert cli.main(["--db", str(db), "landing", "--leader", LEAD, "--since-minutes", "30"]) == 2
    assert "--sig" in capsys.readouterr().err
    a = cli.build_parser().parse_args(["landing", "--sig", "X", "--leader", "A", "--leader", "B"])
    assert a.leader == ["A", "B"] and a.since_minutes == 60.0
