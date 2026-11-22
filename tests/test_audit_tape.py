"""Audit of 22 Nov 2026, the tape: what a backfill does to the rows the recorder wrote.

A mint backfill (`Ingestor.backfill_mint_window`, run by `copy`, `follow`, `study`, `audit` and `sniper` with their
default refresh) rewrote `trades.pos` of every signature it listed with its ordinal among the coin's transactions of
the slot, so a recorder row at block index 902 became 1, and a transaction that trades two coins gave the second coin
the first coin's ordinal (its order in the block inverted). The recorder never writes `seen_sigs`, so the backfill
also paid a getTransaction for every transaction the tape already held: 15,287, 12,770, 10,033 and 5,152 pump.fun
curve transactions a day on 19-22 Nov, each fetched a second time."""

import asyncio
import json
from pathlib import Path

from snipetech.ingest import Ingestor
from snipetech.models import AMM, Trade
from snipetech.store import Store

TXFX = Path(__file__).parent / "fixtures" / "tx"
S = 472_000_000
TS = 1_795_000_000


def _t(sig: str, mint: str, pos: int, ev: int = 0, venue: str = "curve") -> Trade:
    return Trade(sig=sig, ev=ev, slot=S, pos=pos, ts=TS, mint=mint, venue=venue, user=f"u-{sig}", is_buy=True,
                 quote=1_000_000_000, base=1_000_000, fee=12_500_000, fee_bps=125, q_after=40_000_000_000,
                 b_after=800_000_000_000_000, r_base_after=0, ix="buy")  # fmt: skip


class FakeRpc:
    """getSignaturesForAddress lists `listing` (newest first); getTransaction answers from `txs` and keeps what it
    was asked."""

    def __init__(self, listing: list[dict], txs: dict[str, dict] | None = None):
        self.listing, self.txs, self.asked = listing, txs or {}, []

    async def iter_signatures(self, address, before=None, max_sigs=0, stop_before_ts=None):
        yield self.listing

    async def get_transactions(self, sigs):
        self.asked += list(sigs)
        return {s: self.txs[s] for s in sigs if s in self.txs}


def _listed(*sigs: str, slot: int = S, ts: int = TS) -> list[dict]:
    return [{"signature": s, "slot": slot, "blockTime": ts, "err": None} for s in sigs]


def test_a_mint_backfill_keeps_the_recorders_places_in_the_block_and_fetches_nothing_the_tape_holds():
    st = Store(":memory:")
    # the recorder's rows of block S: coin A at block indices 100 and 902; tx2 also trades coin B, whose other trade
    # stands at index 600
    st.add_trades([_t("tx1", "A", 100), _t("tx2", "A", 902), _t("tx2", "B", 902, ev=1), _t("tx3", "B", 600)])
    rpc = FakeRpc(_listed("tx2", "tx1"))
    res = asyncio.run(Ingestor(st, rpc).backfill_mint_window("A", TS - 1000, TS + 100))
    assert [(t.sig, t.pos) for t in st.trades_for_mint("A")] == [("tx1", 100), ("tx2", 902)]  # was 0 and 1
    assert [t.sig for t in st.trades_for_mint("B")] == ["tx3", "tx2"]  # was inverted
    assert rpc.asked == [] and res.fetched == 0 and res.held == 2  # was two getTransaction calls


def _fixture(prefix: str) -> tuple[str, dict]:
    path = next(TXFX.glob(f"{prefix}*.json"))
    return path.stem, json.loads(path.read_text())


def test_a_transaction_the_backfill_fetches_is_placed_at_its_index_in_the_block():
    """The place a backfill writes is the transaction's own index in its block, the number the recorder writes; the
    listing's order inside the slot stands in only when the answer has no index."""
    (s1, t1), (s2, t2) = _fixture("22BtADtf"), _fixture("2Bh94GYo")  # curve trades of one coin, block indices 315, 406
    del t2["transactionIndex"]
    mint = "5j776Q6okVB672VYMapMqMp47hLRcSAYE9LMqBgdpump"
    listing = [
        {"signature": s, "slot": t["slot"], "blockTime": t["blockTime"], "err": None} for s, t in ((s2, t2), (s1, t1))
    ]
    st = Store(":memory:")
    rpc = FakeRpc(listing, {s1: t1, s2: t2})
    asyncio.run(Ingestor(st, rpc).backfill_mint_window(mint, t1["blockTime"] - 5, t2["blockTime"] + 5))
    places = {t.sig: t.pos for t in st.trades_for_mint(mint)}
    assert places == {s1: 315, s2: 0}  # was 0 for both: each one's ordinal among the coin's transactions of its slot


def test_a_transaction_of_which_only_a_pumpswap_row_is_stored_is_still_fetched():
    """The PumpSwap backfill (amm_backfill.py) stores only some of a transaction's trades (a wallet's own, one coin's)
    and marks nothing seen: such a row is no sign that the tape holds the whole transaction."""
    sig, tx = _fixture("3kz7PFEU")  # a PumpSwap buy
    st = Store(":memory:")
    st.add_trades([Trade(sig=sig, ev=0, slot=tx["slot"], pos=795, ts=tx["blockTime"], mint="M", venue=AMM, user="u",
                         is_buy=True, quote=1, base=1, fee=0, fee_bps=0, q_after=1, b_after=1)])  # fmt: skip
    rpc = FakeRpc(_listed(sig, slot=tx["slot"], ts=tx["blockTime"]), {sig: tx})
    res = asyncio.run(Ingestor(st, rpc).ingest(rpc.listing))
    assert rpc.asked == [sig] and res.fetched == 1 and res.held == 0
