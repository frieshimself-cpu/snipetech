"""The owner's summary after each post-mortem batch (operator/postmortem.py): the trips, the price move, what the fees
and the duplicates cost, the net, the biggest cause and the one change it suggests, in plain words and SOL, from the
code's breakdown; a change the strategist may make within its bounds says so, anything that needs the owner says so.
No secret, no key, no wallet balance, nothing that changes a setting."""

import re

from snipetech.config import Config
from snipetech.exits_menu import COPY_EXIT_MENU, EXIT_MENU_VERSION
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


T0 = 1_793_985_720  # 6 Nov 17:22 UTC


def _copy(k: int, price: float, leader: str) -> dict:
    """A 0.2 SOL copy at OKTech's minimum bid with a 0.002 sell fee and no duplicate: its parts add up to its result."""
    venue, fee = 0.00499, 0.003005
    result = round(price - venue - 2 * fee, 9)
    return {"sell_sig": f"S{k}", "buy_sig": f"B{k}", "mint": f"M{k}", "leader": leader, "buy_ts": T0 + 100 * k,
            "sell_ts": T0 + 100 * k + 9, "venue": "curve", "price_sol": price, "venue_fee_sol": venue,
            "price_net_sol": round(price - venue, 9), "buy_fee_sol": fee, "sell_fee_sol": fee, "dup_fee_sol": 0.0,
            "dups": 0, "result_sol": result, "recorded_sol": result, "fees_known": True,
            "kind": "won" if result > 0 else "lost_to_costs" if price > 0 else "lost_on_price", "flips": [],
            "biggest": None, "tape_check_sol": 0.0, "exit": None, "levers": {"sell_fee": 0.001},
            "bid_over_minimum": None}  # fmt: skip


def _replayed_copy(k: int, gain: float) -> dict:
    """A copy that rose and lost to the costs, replayed under every exit on the menu: lab ran, hold60 `gain` better."""
    replay = {m.name: -0.01 for m in COPY_EXIT_MENU} | {"hold60": round(-0.01 + gain, 9)}
    return _copy(k, 0.004, "LEADERxxxx") | {"exit": {"ran": "lab", "fired": "dead_exit", "replay": replay}}


WORTH_TESTING = (
    "Worth testing: the hold60 exit on every copy instead of the one that ran (a code change, not a setting). "
    "Measure's replay: better on 9 and worse on 0 of the 9 copies where they differed, +0.0270 SOL over these 9 "
    "copies and +0.0350 over all 13 on record. Hindsight, not a saving. Needs your yes."
)


STORE_WALLET = "WALLET111111111111111111111111111111111111111"


def _stored_copies(st, n: int, gain: float, t0: int) -> None:
    """`n` live copies of one leader in the store from `t0`: bought at 0.2 SOL, sold 9 s later, lab ran, and measure's
    replay of every exit on the menu with hold60 `gain` SOL better than lab. No trade of them on the tape."""
    for k in range(n):
        b, t = f"B{t0}-{k}", t0 + 60 * k
        st.put_fill({"sig": b, "ts": t, "slot": t, "wallet": STORE_WALLET, "wallet_copied": "LEADERxxxx",
                     "mint": f"M{t0}-{k}", "side": "buy", "sol": 0.2, "tokens": 1000.0, "priority_lamports": 1_000_000,
                     "tip_lamports": 2_000_000, "bid_arm": "control", "exit_name": "lab"})  # fmt: skip
        st.put_fill({"sig": f"S{t0}-{k}", "ts": t + 9, "slot": t + 9, "wallet": STORE_WALLET, "mint": f"M{t0}-{k}",
                     "side": "sell", "sol": 0.19, "tokens": 1000.0, "pnl_sol": -0.01601, "twin_fee_sol": 0.0,
                     "buy_sig": b, "exit_reason": "dead_exit"})  # fmt: skip
        st.put_exit_shadow([{"buy_sig": b, "exit_name": m.name, "menu_version": EXIT_MENU_VERSION,
                             "sim_pnl_sol": round(-0.01 + (gain if m.name == "hold60" else 0.0), 6),
                             "sim_reason": "dead_exit", "computed_at": t + 400} for m in COPY_EXIT_MENU])  # fmt: skip


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
    assert "Change it suggests: Copy only leaders whose first sell comes after 10 s (needs your yes)." in text
    assert "On the fees: the sell fee from 0.002 back to 0.001 SOL" in text  # ours, not OKTech's, after the idea
    assert "Net: -0.0759 SOL. The loss came from the price itself." in text
    owner = P.summary_text(trips, idea=idea | {"owner_needed": True, "kind": "code"})
    assert "(needs your yes)" in owner
    no_idea = P.summary_text(trips)
    assert "Change it suggests" not in no_idea and "On the fees: the sell fee" in no_idea


def test_an_idea_is_never_called_the_strategists_when_nothing_checked_its_bounds():
    """Review, 7 Nov: every config idea without owner_needed was labelled the strategist's, but an idea names no key
    or value and the analyst never sees the [autotune] bounds: "Lower the sell fee to 0.001 SOL" came out as the
    strategist's two lines above "the sell fee ... Needs your yes". A config idea needs the owner's yes; only the
    agents' own research is theirs. The strategist's label is left to the levers, which are checked."""
    trips = [_copy(0, -0.06, "2YDnAAAAleader")]
    for title in ("Lower the sell fee to 0.001 SOL", "Switch book.exit_mode to per_wallet",
                  "Raise book.max_wallets to 10"):  # fmt: skip
        text = P.summary_text(trips, idea={"id": "I12", "title": title, "kind": "config", "owner_needed": False})
        assert f"Change it suggests: {title} (needs your yes)." in text, title
        assert "strategist" not in text, title
    research = {"id": "I13", "title": "Score leaders by their first sell", "kind": "research", "owner_needed": False}
    assert "(the agents test it themselves)." in P.summary_text(trips, idea=research)
    assert "(needs your yes)." in P.summary_text(trips, idea=research | {"owner_needed": True})


def test_a_batch_that_lost_on_the_price_says_so_when_one_winner_rose_more():
    """Review, 7 Nov: four copies of one leader fell 0.03 SOL each before any cost and one copy of another rose 0.15.
    The net move is +0.03, but the falls took 0.12 SOL, more than any cost: the owner reads the price as the biggest
    cause and the analyst's idea first, where the summary had named pump.fun's fee and dropped the idea."""
    trips = [_copy(k, -0.03, "2YDnAAAAleader") for k in range(4)] + [_copy(4, 0.15, "CATxxxxleader")]
    idea = {"id": "I9", "title": "Skip leaders that dump within 5 s", "kind": "research", "owner_needed": False}
    lines = P.summary_text(trips, idea=idea).splitlines()
    assert lines[1] == "Price: +0.0300 SOL before any cost (1 rose, 4 fell)."
    assert lines[3] == "Net: -0.0250 SOL. All 4 losses came from the price itself."
    assert lines[4] == (
        "Biggest cause: the price fell after we bought on 4 of the 5 trips (0.1200 SOL); copies of 2YDnAA lost "
        "0.1200 on the price over 4 trips."
    )
    assert lines[5] == "Change it suggests: Skip leaders that dump within 5 s (the agents test it themselves)."
    assert lines[6].startswith("On the fees: the sell fee from 0.002 back to 0.001 SOL")


def test_an_exit_from_a_few_copies_in_hindsight_is_never_offered_to_the_owner():
    """Review, 7 Nov: with the sell fee already at 0.001 the night read "Best change of ours: the hold60 exit on every
    copy ... 0.0020 SOL on these trips. Needs your yes." All of it came from one copy of three, no setting runs one
    exit on every copy, and over the 23 copies on record hold60 had lost 0.35 SOL more than lab. The exit stays with
    the analyst (read_round_trips); the owner's lines leave it out."""
    cfg = Config()
    cfg.oktech.sell_fee_sol = 0.001
    text = P.summary_text(_night(), cfg=cfg)
    assert "hold60" not in text and "Best change of ours" not in text and "exit on every copy" not in text
    no_fee_levers = [t | {"levers": {}} for t in _night()]
    text = P.summary_text(no_fee_levers, cfg=cfg)
    assert "hold60" not in text and text.splitlines()[-1] == (
        "No change in our hands would have saved SOL on these trips; the post-mortem report says why."
    )


def test_an_exit_that_clears_the_books_bar_here_and_on_the_record_is_a_test_never_a_saving():
    """An exit reaches the owner only when it beat the exit that ran on at least EXIT_MIN_TEST copies where the two
    differed, better on most, in this batch and over every copy on record (the book's own bar before a sniper runs
    another exit than lab). It is worded as a test, after the changes that would have saved SOL, with how many copies
    it differed on, and as a code change; never "would have saved"."""
    trips = [_replayed_copy(k, 0.003) for k in range(9)]  # 9 copies, hold60 0.003 better on each
    batch = [t["exit"] for t in trips]
    agrees = batch + [_replayed_copy(20 + k, 0.002)["exit"] for k in range(4)]
    lines = P.summary_text(trips, record=agrees).splitlines()
    assert lines[-2].startswith("Would have saved the most: the sell fee")  # 0.0090 SOL: a saving, ranked first
    assert lines[-1] == WORTH_TESTING
    disagrees = batch + [_replayed_copy(20 + k, -0.005)["exit"] for k in range(12)]
    assert "Worth testing" not in P.summary_text(trips, record=disagrees)
    assert "Worth testing" not in P.summary_text(trips)  # nothing checked against the record: nothing said


def test_the_owner_hears_of_an_exit_only_after_the_whole_record_of_replays_was_read(tmp_path):
    """send_summary reads every copy measure replayed (measure/attribution.py exit_record) before an exit that cleared
    the bar on the batch reaches the owner: the same nine copies read as a test when four earlier ones agree, and not
    at all when twelve earlier ones went the other way."""
    from snipetech.operator.paths import OperatorPaths

    class Svc:
        def __init__(self, store):
            self.store = store

    def run(earlier: int, earlier_gain: float) -> str:
        st = Store(":memory:")
        try:
            _stored_copies(st, earlier, earlier_gain, T0 - 86_400)
            _stored_copies(st, 9, 0.003, T0)
            cfg = Config()
            cfg.operator.wallet = STORE_WALLET
            sent: list[str] = []

            class Notes:
                def write(self, text, level):
                    pass

            class Notifier:
                def send(self, text):
                    sent.append(text)

            trips = {"n": 9, "since_ts": T0 - 1, "newest_ts": T0 + 60 * 8 + 9}
            P.send_summary(Svc(st), cfg, OperatorPaths(tmp_path).ensure(), trips, Notes(), Notifier(), T0 + 3600)
            return sent[0]
        finally:
            st.close()

    assert run(4, 0.002).splitlines()[-1] == WORTH_TESTING
    assert "Worth testing" not in run(12, -0.005)


def test_the_summary_carries_no_key_no_signature_and_no_wallet_balance():
    text = P.summary_text(_night())
    assert F.WALLET not in text and not re.search(r"[1-9A-HJ-NP-Za-km-z]{40,}", text)
    assert "balance" not in text.lower() and "equity" not in text.lower()
    assert len(text) < 1200


def test_no_trips_no_summary():
    assert P.summary_text([]) is None
