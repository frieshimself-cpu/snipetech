"""The owner's summary after each post-mortem batch (operator/postmortem.py): the trips, the price move, what the fees
and the duplicates cost, the net, the biggest cause and the one change it suggests, in plain words and SOL, from the
code's breakdown; a change the strategist may make within its bounds says so, anything that needs the owner says so.
No secret, no key, no wallet balance, nothing that changes a setting."""

import re

from snipetech.measure import attribution as A
from snipetech.operator import postmortem as P
from snipetech.store import Store
from tests import loss_7nov as F


def _night() -> list[dict]:
    st = Store(":memory:")
    try:
        F.load(st)
        sells = st.fills(wallet=F.WALLET, since_ts=F.FIRST_BUY_TS - 60, side="sell")
        return [b for b in A.break_down(st, F.WALLET, sells).values() if b]
    finally:
        st.close()


NIGHT = """\
Post-mortem: 3 live round trips, 7 Nov 19:18-19:22 UTC: 1 won, 2 lost.
Price: +0.0294 SOL before any cost (3 rose, 0 fell).
Costs: 0.0412 SOL: pump.fun's fee 0.0152, buy fees 0.0090, sell fees 0.0090, 4 failed duplicate sells 0.0080.
Net: -0.0119 SOL. Both losses made money on the price and lost it to the costs.
Biggest cause: pump.fun's fee, 1.25 % on each buy and each sell (0.0152 SOL). The venue sets it; no setting changes it.
Would have saved the most: OKTech sending each exit once (it signs up to three copies of every sell, and the copies \
that lose the race fail and still pay): 0.0080 SOL on these trips. Needs your yes: it is OKTech's to change, an ask \
to them.
Best change of ours: the sell fee from 0.002 back to 0.001 SOL ([oktech] sell_fee_sol: the landed sell's tip and each \
failed copy's priority fee): 0.0070 SOL on these trips. Needs your yes."""


def test_the_owner_reads_why_the_night_lost_and_what_would_have_saved_the_most():
    assert P.summary_text(_night()) == NIGHT


def test_a_batch_lost_on_the_price_names_the_leader_and_the_analysts_idea():
    """When the price itself took the most, no fee setting is the answer: the summary names the leader whose copies
    lost it and the analyst's top idea of the turn, labelled by who can act on it; the fee change comes after."""
    trips = [
        {"sell_sig": "S1", "buy_sig": "B1", "mint": "M1", "leader": "2YDnAAAAleader", "buy_ts": 1_793_985_720,
         "sell_ts": 1_793_985_726, "venue": "curve", "price_sol": -0.0637, "venue_fee_sol": 0.0041,
         "price_net_sol": -0.0678, "buy_fee_sol": 0.003005, "sell_fee_sol": 0.003005, "dup_fee_sol": 0.002005, "dups": 1,
         "result_sol": -0.075915, "recorded_sol": -0.07391, "fees_known": True, "kind": "lost_on_price", "flips": [],
         "biggest": "price", "tape_check_sol": 0.0, "exit": None,
         "levers": {"single_send": 0.002005, "sell_fee": 0.002}, "bid_over_minimum": None},
    ]  # fmt: skip
    idea = {"id": "I7", "title": "Copy only leaders whose first sell comes after 10 s", "kind": "config",
            "owner_needed": False}  # fmt: skip
    text = P.summary_text(trips, idea=idea)
    assert (
        "Biggest cause: the price fell after we bought (0.0637 SOL); copies of 2YDnAA lost 0.0637 on the price over "
        "1 trip." in text
    )
    assert (
        "Change it suggests: Copy only leaders whose first sell comes after 10 s (the strategist may make this "
        "change within its bounds)." in text
    )
    assert "On the fees: the sell fee from 0.002 back to 0.001 SOL" in text  # ours, not OKTech's, after the idea
    assert "Net: -0.0759 SOL. The loss came from the price itself." in text
    owner = P.summary_text(trips, idea=idea | {"owner_needed": True, "kind": "code"})
    assert "(needs your yes)" in owner
    no_idea = P.summary_text(trips)
    assert "Change it suggests" not in no_idea and "On the fees: the sell fee" in no_idea


def test_the_summary_carries_no_key_no_signature_and_no_wallet_balance():
    text = P.summary_text(_night())
    assert F.WALLET not in text and not re.search(r"[1-9A-HJ-NP-Za-km-z]{40,}", text)
    assert "balance" not in text.lower() and "equity" not in text.lower()
    assert len(text) < 1200


def test_no_trips_no_summary():
    assert P.summary_text([]) is None
