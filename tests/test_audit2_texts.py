"""The audit of 22-23 Nov 2026, second round, group "texts": what the server's agents are told (operator/prompts.py,
the tool and table descriptions of operator/tools.py), and the words in code the first round made stale, each checked
against the merged code and, where it is a number, against the server."""

import inspect

from snipetech.operator import prompts as P
from snipetech.operator import tools as T


def _flat(text: str) -> str:
    return " ".join(text.split())


def _tools_text() -> str:
    """The words of the operator's and the analyst's tools, as their descriptions are written (the docstrings of
    make_operator_tools and of the learning tools)."""
    return _flat(inspect.getsource(T.make_operator_tools) + inspect.getsource(T.make_learning_tools))


def test_a_decision_note_is_for_the_bots_own_coins_only():
    """Round 1 (round#6, docs#2): the gate raises only the bot's coins; the owner's own coin and an airdrop no longer
    reach the operator as held or unmanaged (22 Nov: one airdrop woke 7 turns that paged the owner). The rule the
    operator and the analyst write notes by still said "a coin no sniper is selling" and "a coin held", of any coin,
    while the snapshot's `held` lists every coin in the wallet with whose it is."""
    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        text = _flat(system)
        assert "a coin our snipers bought that no sniper is selling" in text
        assert "a sniper running or a coin of ours held when none should be" in text
        assert "the owner's own coin and one sent in (`ours` false) are never a decision" in text
        assert "a coin no sniper is selling" not in text and "or a coin held when none should be" not in text
    note = _tools_text()
    assert "a coin our snipers bought that no sniper is selling, a sniper running or a coin of ours held" in note
    assert "a wallet clearing all three gates" not in note  # a verdict has had more gates than three since 7 Nov


def test_the_failed_sell_sends_of_a_trip_are_named_for_what_they_hold():
    """Round 1 (measure#7): a sell's twin_fee_sol holds OKTech's failed duplicate sends and, since 22 Nov, the sale's
    own tries that failed before it landed (8MTKG4, 20 Nov: nine curve sales failed on the completed curve before its
    PumpSwap sale). The tools the agents read it with, the post-mortem's instructions, the cause's words and the
    owner's summary line still called the whole sum failed duplicates, OKTech's extra copies."""
    from snipetech.measure.attribution import CAUSE_WORDS
    from snipetech.operator.postmortem import _costs

    assert CAUSE_WORDS["duplicates"].startswith("failed sell sends (OKTech's extra copies of a sell, and a sale's own")
    assert "tries that failed before it landed" in CAUSE_WORDS["duplicates"]
    tools = _tools_text()
    tries = "the sale's own tries that failed before it landed"
    assert f"twin_fee_sol: OKTech's failed duplicate sends and {tries}" in tools  # read_round_trips
    assert "and its own tries that failed before it landed, are beside it in twin_fee_sol" in tools  # read_fills
    analyst = _flat(P.ANALYST_SYSTEM)
    assert f"the failed duplicate sells (among them {tries})" in analyst
    assert "fees and failed duplicate sends" in analyst  # the cause keeps its name
    s = {"venues": ["curve"], "venue_fee_sol": 0, "buy_fees_sol": 0, "sell_fees_sol": 0, "duplicates_sol": 0.054,
         "duplicates": 9, "costs_sol": 0.054}  # fmt: skip
    assert _costs(s) == "Costs: 0.0540 SOL: 9 failed sell sends 0.0540."
    assert _costs(s | {"duplicates": 1}) == "Costs: 0.0540 SOL: 1 failed sell send 0.0540."


def test_a_trips_shadow_is_said_to_hold_the_exit_that_ran_beside_the_menu():
    """Round 1 (measure): the shadow replays the buying sniper's own exit beside the menu when the menu lacks it (a
    hold12 copy), so `shadow` holds a name the menu does not and `shadow_best` can be the exit that ran."""
    assert "what every exit on the menu, and the exit that ran, would have made of the same entry" in _tools_text()
    said = "shadow says what every exit on the menu, and the exit that ran, would have made from the same entry"
    assert said in _flat(P.ANALYST_SYSTEM)


def test_the_agents_are_told_the_owner_trades_by_hand_in_the_wallet_and_how_a_row_says_whose_it_is():
    """Round 1 (measure#1, server#0): the owner buys and sells by hand in the sniper wallet, and since 22 Nov read_fills
    marks each row `ours` and counts the owner's apart, while the bid model and the live expectancy hold the bot's
    trades only. The rules still called `fills` "every buy and sell of the book wallet", and the table's own words for
    a query said nothing: a count over it took the owner's hand trades for the bot's."""
    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        text = _flat(system)
        assert "The owner trades by hand in that wallet too" in text
        assert "each row's `ours` marks the bot's own" in text and "hold the bot's trades only" in text
    assert "the owner's hand trades in it too" in T.SQL_TABLES["fills"] and "`ours`" in T.SQL_TABLES["fills"]


def test_no_text_says_the_twins_pay_the_sell_priority_we_send():
    """Round 1 (docs#3) and its review: OKTech uses no sell priority under the buy's. On the server every sell (23) and
    every failed send (50) from 20 Nov on paid 1,000,000 lamports, the buy priority, under a sell priority of 0.0001
    (read-only SQL, 23 Nov). The measure stage's own words still said the 0.0001 setting "priced the twins", and the
    agents read twins_per_sell as the twins' cost "at each sell priority"."""
    import inspect

    from snipetech.measure import attribution as A
    from snipetech.measure import run as R

    for f in (R.twin_priority, R.twins_per_sell, A.twins_per_trip):
        doc = _flat(f.__doc__)
        assert "buy priority" in doc and "under the buy's" in doc, f.__name__
        for stale in ("priced the twins", "priced the duplicates", "0.0001 after)"):
            assert stale not in doc, (f.__name__, stale)
    trip = _flat(inspect.getsource(A._trip))
    assert "priced the duplicates" not in trip and "the sniper's buy priority since" in trip
    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        text = _flat(system)
        assert "at each sell priority (twins_per_sell)" not in text
        assert "by the sell priority its sniper was sent (twins_per_sell" in text


def test_the_two_days_of_doubled_cost_are_told_at_what_the_stored_records_were_charged():
    """Round 1 (paper#3): the text said every lab and freeze of 12 Nov 17:24 to 14 Nov 18:26 UTC charged 0.0085 to
    0.0093 SOL a transaction. The rules frozen in that window on the server carry per_tx_sol 0.0062 (1 rule), 0.0067
    (4), 0.0074 (1), 0.0085 (18) and 0.0093 (4), rising through the two days (read-only SQL, 23 Nov). And a hunt's
    exit_note can now be the cost's note as well as the crews'."""
    from snipetech.analysis.paper import OVERCHARGED_SOL

    text = _tools_text()
    assert "charged 0.0062 to 0.0093 SOL a transaction, rising through the two days" in text
    assert "0.0085 to 0.0093" not in text and "up to about 0.009 SOL a round trip too much" in text
    assert OVERCHARGED_SOL <= 0.0062  # the lowest of them is one the code calls overcharged
    assert "has another reason: SERIAL CREWS' COINS or THE COST A LAB CHARGES below" in text


def test_the_agents_are_told_what_the_snapshots_paper_rows_and_a_report_rows_marks_mean():
    """Round 1 (paper#0, paper#5): the round's snapshot ranks the rules scored where our trades land live (paper_top)
    apart from those at a place asked for, bought with a bid or with no live place (paper_own_landing), and counts
    the refuted ones it leaves out (paper_refuted); a paper report row says when its rule was refuted and how many of
    its trades rest on a price outside the model. No text named any of them."""
    import time

    from snipetech.analysis import paper
    from snipetech.operator.snapshot import Snapshot

    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM):
        text = _flat(system)
        assert "`paper_top` holds the six biggest totals of the rules scored where our trades land live" in text
        assert "`paper_own_landing`" in text and "never evidence at our landing" in text
        assert "`paper_refuted`" in text and "`paper_research_only`" in text
    for system in (P.ANALYST_SYSTEM, P.ROLE_SYSTEM["tester"]):
        text = _flat(system)
        assert "A report row with `refuted` (when, on how many trades, at what mean)" in text
        assert "`outside_model`" in text and "`outside_why`" in text
    # the names are the snapshot's and the report's own
    rows = [{"id": "live", "total": 1.0, "live_landing": True}, {"id": "bid", "total": 9.0, "live_landing": False},
            {"id": "gone", "total": 5.0, "live_landing": True, "refuted": {"at": 1, "trades": 30, "mean_sol": -0.01}},
            {"id": "old", "total": 7.0, "live_landing": True, "research_only": True}]  # fmt: skip
    snap = Snapshot(ts=time.time(), wallet="W", native_sol=1.0, wsol=0.0, snipers=[])
    snap.paper = {"strategies": rows}
    d = P._compact_snapshot(snap)
    assert [s["id"] for s in d["paper_top"]] == ["live"] and [s["id"] for s in d["paper_own_landing"]] == ["bid"]
    assert d["paper_refuted"] == 1 and d["paper_research_only"] == 1
    report = inspect.getsource(paper.report)
    assert '"outside_model"' in report and '"outside_why"' in report and '["refuted"]' in report
    assert set(paper._outside_count([])) == {"trades", "sol", "of"}


def test_the_trades_table_says_what_a_tip_of_nothing_and_a_backfilled_place_are():
    """Round 1 (tape#4, tape#3). trades.tip counts the trade transaction's own transfers, and the tape receives only
    transactions that touch pump.fun, LaunchLab or a PumpSwap pool: a bundle's tip paid in a transaction of its own
    never reaches it, and a buy near the front with tip 0 read as a free place. And until the fix of 22 Nov, deployed
    23 Nov, a backfill wrote a coin's own order in the block over the place the recorder had written (on the server,
    slot 472096184: coin 4RJjFrkf's three trades at pos 0, 1, 2, every other coin's at 58 to 237). The bid tables'
    reading carries the first caveat since round 1; the table an agent's query reads carried neither."""
    from snipetech.analysis.place_bids import TIP_READING

    words = T.SQL_TABLES["trades"]
    assert "tip is the trade's own transaction's alone" in words
    assert "tip 0 near the front of a block means not known, not free" in words
    assert "never reaches the tape" in words and "never reaches the tape" in TIP_READING  # one caveat, both places
    assert "pos can be the coin's own order in the block (0, 1, 2), not a block place" in words


def test_read_file_says_what_comes_back_of_a_file_too_long_to_give_whole():
    """Round 1 (ops#6): a file longer than read_file hands out comes back as its end, cut at a whole line, with no
    line that says so; "0: the whole file" was all an agent was told."""
    text = _tools_text()
    assert f"of a file over {T.READ_FILE_CHARS:,} characters its end, from a whole line" in text
    assert "of one written newest first its start, with a line that says so" in text


def test_the_sends_help_says_which_failed_sends_its_fees_cover():
    """Round 1 (tools#0): sends fetches only the failed sends that landed within 3 slots of a leader's buy, so fee_sol
    and priority_sol cover those and not every failed send; the result's failed.note says what each sum covers. The
    help still said "what the failed ones paid"."""
    from snipetech.analysis.sends import FEES_NOTE

    text = _tools_text()
    assert "what its failed sends near those buys paid (failed.note says what each sum covers" in text
    assert "what the failed ones paid" not in text and "fetched sends only" in FEES_NOTE


def test_a_dated_report_of_the_research_record_settles_an_angle_as_a_round_does():
    """Round 1 (docs#1): the research record the agents are sent carries the dated reports beside the rounds (the
    dip-buys refutation of 12 Nov among them); the rule against testing a refuted angle again named rounds only."""
    for system in (P.OPERATOR_SYSTEM, P.ANALYST_SYSTEM, *P.ROLE_SYSTEM.values()):
        text = _flat(system)
        assert "Do not re-test an angle a round refuted" in text
        assert "a dated report of the record counts as a round" in text
