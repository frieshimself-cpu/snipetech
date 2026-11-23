"""Round 2 of the 22-23 Nov 2026 audit, group "livea": whose a buy is, and the paths that stop a sniper.

A1. The bot's buys were told from the owner's hand buys by fees alone (measure/ours.py): a buy was the bot's when it
paid the exact (priority, tip) pair of a sniper payload on record, that priority with no tip, or fees nobody recorded.
OKTech already varies what it pays (22 Nov 00:19: a copy went out without its 0.199 tip; the snipers have "smart fees"),
and a copy at a pair no payload names read as the owner's coin everywhere: not counted or sold by the run, its sniper
free to be stopped while it held the coin (CLAUDE.md rule 1). The second signal is the copy's shape: a wallet a copy
sniper of ours follows bought the same coin at a place before ours and at most COPY_SHAPE_SLOTS slots earlier. It only
ever turns "the owner's" into "the bot's".

The shapes are the record's: the copy of 22 Nov 01:16:43 (4ab6NvcS: Cupseyy's buy in slot 472308765, ours one slot on)
and the owner's hand buys of 22 Nov 02:40 (H8WTvo: 6,121 and 8,175 lamports priority, tips of 0.027 to 0.052 SOL, no
copied wallet anywhere near)."""

import asyncio

import pytest

from snipetech.measure.ours import RunCoins, bot_buy, bot_tape_buy, fee_marks, owner_coins, sniper_marks, tape_whose
from snipetech.models import Trade
from snipetech.store import Store

from .chain import Chain, key, swap

W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"  # the sniper wallet, the owner's too
LEAD = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"  # a wallet our copy sniper follows
STRANGER = "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9"  # a wallet no sniper on record follows
MINT = "Coin1" + "1" * 39
NOW, SLOT = 1_795_300_603, 472_308_766  # our copy of 22 Nov 01:16:43 UTC
PAYLOAD = {"singleSnipe": True, "ctbotWallets": [{"address": LEAD}], "priorityFee": 0.001, "tipAmount": 0.199,
           "autoSell": {"sellPriorityFeeSol": 0.0001, "sellTipAmount": 0.002}}  # fmt: skip
PAID = (1_000_000, 199_000_000)  # what the payload's sniper pays on a buy
ODD = (1_500_000, 120_000_000)  # a pair no payload on record names (OKTech changing what it pays)
HAND = (6_121, 26_993_879)  # the owner's hand buy of 22 Nov 02:40


def _store(payload=PAYLOAD, created=NOW - 86_400, removed=None):
    store = Store(":memory:")
    if payload is not None:
        store.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": LEAD, "name": "copy",
                                 "config_hash": "h", "created_at": created, "payload": payload})  # fmt: skip
        if removed is not None:
            store._exec("UPDATE oktech_snipers SET removed_at=? WHERE id='cs1'", (int(removed),))
    return store


def _buy(store, sig, user, slot, pos, fees=(None, None), mint=MINT, base=1_000_000):
    """A buy on the tape with its transaction's priority fee and tip in lamports, as the recorder writes them."""
    t = Trade(sig=sig, ev=0, slot=slot, pos=pos, ts=NOW + round((slot - SLOT) * 0.4), mint=mint, venue="curve",
              user=user, is_buy=True, quote=1_000_000_000, base=base, fee=10_000_000, fee_bps=100, q_after=1,
              b_after=1, priority=fees[0], tip=fees[1])  # fmt: skip
    store.add_trades([t])
    return t


def _ours(store, wallet=W):
    """The wallet's newest buy as a reader of the tape gets it, with its fees."""
    return [t for t in store.trades_for_user(wallet, 0, fees=True) if t.is_buy][-1]


def _fill(t, fees, **more):
    """The measure stage's row of a buy the tape placed."""
    return {"sig": t.sig, "ts": t.ts, "slot": t.slot, "wallet": W, "mint": t.mint, "side": "buy", "sol": 1.01,
            "tokens": float(t.base), "priority_lamports": fees[0], "tip_lamports": fees[1]} | more  # fmt: skip


def _whose(store, marks, since=NOW - 300):
    coin = {"mint": MINT, "raw": 1_000_000, "value_sol": 1.0, "priced": True}
    return asyncio.run(RunCoins(store, None, W, marks).whose(coin, since, NOW + 5))


# -- A1: the copy's shape --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("lead_at,ours_at", [((SLOT - 1, 196), (SLOT, 196)), ((SLOT, 46), (SLOT, 47)),
                                             ((SLOT - 3, 400), (SLOT, 2))],
                         ids=["the next block", "the copied wallet's own block", "three slots on"])  # fmt: skip
def test_a_copy_right_behind_a_copied_wallet_is_the_bots_whatever_fees_it_paid(lead_at, ours_at):
    """A1: a copy that paid a fee pair no payload names read as the owner's coin: in the fills (bot_buy), on the tape
    (bot_tape_buy, tape_whose), to /stop and the live test's close (owner_coins) and to the runner (RunCoins)."""
    store = _store()
    _buy(store, "lead", LEAD, *lead_at, fees=(5_000, 0))
    t = _buy(store, "ours", W, *ours_at, fees=ODD)
    marks = fee_marks(store)
    assert bot_tape_buy(_ours(store), marks)
    assert tape_whose(store, W, MINT, NOW - 300, marks) == "ours"
    assert owner_coins(store, W, [{"mint": MINT, "raw": 1_000_000}], marks) == set()
    assert bot_buy(_fill(t, ODD, wallet_copied=LEAD, trades_behind=3, landing_src="tape"), marks)
    # the runner: the run's own snipers' fees and the wallets they copy
    assert _whose(store, sniper_marks([PAYLOAD], store=store)) == "ours"
    store.close()


def test_the_owners_hand_buy_twenty_slots_behind_a_copied_wallet_stays_the_owners():
    """A1: eight seconds behind a copied wallet is no copy (every copy on record landed in the copied wallet's block
    or the next), though the measure stage names that wallet on the fill (its window is 150 slots)."""
    store = _store()
    _buy(store, "lead", LEAD, SLOT - 20, 196, fees=(5_000, 0))
    t = _buy(store, "hand", W, SLOT, 12, fees=HAND)
    marks = fee_marks(store)
    assert not bot_tape_buy(_ours(store), marks)
    assert tape_whose(store, W, MINT, NOW - 300, marks) == "owner"
    assert owner_coins(store, W, [{"mint": MINT, "raw": 1_000_000}], marks) == {MINT}
    assert not bot_buy(_fill(t, HAND, wallet_copied=LEAD, trades_behind=40, landing_src="tape"), marks)
    assert _whose(store, sniper_marks([PAYLOAD], store=store)) == "owner"
    store.close()


def test_four_slots_behind_is_no_copy_and_a_buy_ahead_of_the_copied_wallet_is_none_either():
    """A1: the window is COPY_SHAPE_SLOTS slots before ours, and the copied buy stands before ours in the block."""
    from snipetech.measure.classify import COPY_SHAPE_SLOTS

    assert COPY_SHAPE_SLOTS == 3
    store = _store()
    _buy(store, "lead", LEAD, SLOT - 4, 196, fees=(5_000, 0))
    _buy(store, "hand", W, SLOT, 12, fees=HAND)
    assert not bot_tape_buy(_ours(store), fee_marks(store))
    store.close()
    store = _store()
    _buy(store, "hand", W, SLOT, 12, fees=HAND)
    _buy(store, "lead", LEAD, SLOT, 13, fees=(5_000, 0))  # the copied wallet bought after it, in the same block
    assert not bot_tape_buy(_ours(store), fee_marks(store))
    store.close()


def test_the_owners_hand_buy_with_no_copied_wallet_near_stays_the_owners():
    """A1: the H8WTvo buys of 22 Nov 02:40. Another wallet's buy right before it, one no sniper follows, changes
    nothing; nor does the copied wallet's buy of another coin."""
    store = _store()
    _buy(store, "stranger", STRANGER, SLOT - 1, 5, fees=(5_000, 0))
    _buy(store, "elsewhere", LEAD, SLOT - 1, 6, fees=(5_000, 0), mint="Coin2" + "2" * 39)
    t = _buy(store, "hand", W, SLOT, 12, fees=HAND)
    marks = fee_marks(store)
    assert not bot_tape_buy(_ours(store), marks)
    assert tape_whose(store, W, MINT, NOW - 300, marks) == "owner"
    assert owner_coins(store, W, [{"mint": MINT, "raw": 1_000_000}], marks) == {MINT}
    assert not bot_buy(_fill(t, HAND, landing_src="chain-none"), marks)
    assert _whose(store, sniper_marks([PAYLOAD], store=store)) == "owner"
    store.close()


def test_with_no_copy_sniper_on_record_nothing_changes():
    """A1: a hunt sniper follows no wallet. Its fees are the only mark, as before: a buy at other fees right behind
    any wallet is the owner's."""
    hunt = {"singleSnipe": True, "priorityFee": 0.001, "tipAmount": 0.199}
    store = _store(payload=hunt)
    _buy(store, "lead", LEAD, SLOT - 1, 196, fees=(5_000, 0))
    t = _buy(store, "hand", W, SLOT, 196, fees=ODD)
    marks = fee_marks(store)
    assert set(marks) == {"buy", "sell"}  # no second signal at all
    assert not bot_tape_buy(_ours(store), marks)
    assert tape_whose(store, W, MINT, NOW - 300, marks) == "owner"
    assert owner_coins(store, W, [{"mint": MINT, "raw": 1_000_000}], marks) == {MINT}
    assert not bot_buy(_fill(t, ODD, landing_src="chain-none"), marks)
    assert _whose(store, sniper_marks([hunt], store=store)) == "owner"
    store.close()
    # and with no sniper on record at all every trade is the bot's, as before
    empty = _store(payload=None)
    _buy(empty, "hand", W, SLOT, 196, fees=HAND)
    assert bot_tape_buy(_ours(empty), fee_marks(empty)) and tape_whose(empty, W, MINT, 0, fee_marks(empty)) == "ours"
    empty.close()


@pytest.mark.parametrize("created,removed", [(NOW + 3_600, None), (NOW - 86_400, NOW - 3_600)],
                         ids=["made an hour after the buy", "removed an hour before it"])  # fmt: skip
def test_a_wallet_no_sniper_followed_at_the_time_makes_no_copy(created, removed):
    """A1: only a sniper alive at the buy can have made it (a minute's grace, as the measure stage names leaders)."""
    store = _store(created=created, removed=removed)
    _buy(store, "lead", LEAD, SLOT - 1, 196, fees=(5_000, 0))
    _buy(store, "hand", W, SLOT, 196, fees=HAND)
    marks = fee_marks(store)
    assert not bot_tape_buy(_ours(store), marks) and tape_whose(store, W, MINT, NOW - 300, marks) == "owner"
    store.close()


def test_a_buy_at_a_snipers_fees_is_the_bots_with_no_copied_wallet_at_all():
    """A1 turns nothing the other way: the fees alone still make a buy the bot's (create-bait copies of 21 Nov 04:07
    and 04:25 stood behind no copied buy)."""
    store = _store()
    t = _buy(store, "bait", W, SLOT, 0, fees=(1_000_000, 0))
    marks = fee_marks(store)
    assert bot_tape_buy(_ours(store), marks) and tape_whose(store, W, MINT, NOW - 300, marks) == "ours"
    assert bot_buy(_fill(t, (1_000_000, 0), landing_src="chain-none"), marks)
    assert owner_coins(store, W, [{"mint": MINT, "raw": 1_000_000}], marks) == set()
    store.close()


def test_a_copy_the_chain_placed_is_the_bots_by_the_blocks_between():
    """A1: three of the 24 copies of 20-22 Nov bought PumpSwap coins the tape no longer held; the measure stage placed
    them from the blocks (landing_src "chain": blocks_after 0, 0 and 1). Such a fill at fees no payload names is a copy
    by the blocks between the copied buy and ours; a buy that landed ahead of the copied wallet's, or long after it,
    is none."""
    store = _store()
    marks = fee_marks(store)
    row = {"sig": "3Ud95jGc", "ts": NOW, "slot": SLOT, "wallet": W, "mint": MINT, "side": "buy", "sol": 0.05,
           "tokens": 1_000_000.0, "priority_lamports": ODD[0], "tip_lamports": ODD[1], "wallet_copied": LEAD,
           "trades_behind": 1, "landing_src": "chain", "venue": "PumpSwap"}  # fmt: skip
    assert bot_buy(row | {"blocks_after": 0, "landing_verdict": "fees"}, marks)
    assert bot_buy(row | {"blocks_after": 1, "landing_verdict": "unclear"}, marks)
    assert not bot_buy(row | {"blocks_after": 0, "landing_verdict": "ahead"}, marks)
    assert not bot_buy(row | {"blocks_after": 40, "landing_verdict": "speed"}, marks)
    assert not bot_buy(row | {"blocks_after": None, "landing_verdict": None}, marks)
    assert not bot_buy(row | {"blocks_after": 0, "landing_verdict": "fees", "wallet_copied": STRANGER}, marks)
    # as /stop and the live test's close read it: the fills hold the buy, the tape does not
    store.put_fill(row | {"blocks_after": 0, "landing_verdict": "fees"})
    assert owner_coins(store, W, [{"mint": MINT, "raw": 1_000_000}], marks) == set()
    # and a row a reader hands over in part (the unsold-coin watch) is read whole by its signature
    assert bot_buy({"sig": "3Ud95jGc", "priority_lamports": ODD[0], "tip_lamports": ODD[1], "wallet_copied": LEAD},
                   marks)  # fmt: skip
    store.close()


def test_a_tape_that_cannot_be_read_tells_nothing_new():
    """A1: the second signal reads the tape. A store that cannot answer leaves the fees to decide alone, as before, for
    a yes or no (bot_buy, bot_tape_buy); where a coin's owner is decided, nothing is told (tape_whose) and the coin is
    not the owner's beyond doubt (owner_coins)."""
    store = _store()
    _buy(store, "lead", LEAD, SLOT - 1, 196, fees=(5_000, 0))
    t = _buy(store, "ours", W, SLOT, 196, fees=ODD)
    marks, trade = fee_marks(store), _ours(store)
    real = store._q

    def broken(sql, params=()):
        if "user IN" in sql:  # the read of the copied wallets' buys
            raise RuntimeError("database is locked")
        return real(sql, params)

    store._q = broken
    assert not bot_tape_buy(trade, marks) and not bot_buy(_fill(t, ODD, wallet_copied=LEAD, landing_src="tape"), marks)
    assert tape_whose(store, W, MINT, NOW - 300, marks) is None
    assert owner_coins(store, W, [{"mint": MINT, "raw": 1_000_000}], marks) == set()
    store.close()


# -- A1: the runner's coin when the tape has no buy of it (the chain's reading) ---------------------------------------

ME, CW = key("our wallet"), key("the copied wallet")  # the fake chain's wallets
COIN = key("a coin on some other program")
S = 480_000_100


def _chain_whose(chain, payload, store=None, now=None, since=None):
    """Whose COIN is to a run whose snipers have `payload`, read `now` (6 s after the buy by default) for a holding
    that began at `since` (5 minutes before, as the runner asks; 0 at a run's start)."""
    store = store or Store(":memory:")
    coins = RunCoins(store, chain, ME, sniper_marks([payload], store=store))
    coin = {"mint": COIN, "raw": 38_000_000, "value_sol": 1.0, "priced": True, "account": key(f"ata {ME} {COIN}")}
    now = chain.ts(S) + 6 if now is None else now
    return asyncio.run(coins.whose(coin, now - 300 if since is None else since, now)), coins


COPY = {"singleSnipe": True, "ctbotWallets": [{"address": CW}], "priorityFee": 0.001, "tipAmount": 0.199}


@pytest.mark.parametrize("our_slot", [S, S + 1], ids=["the copied wallet's own block", "the next block"])
def test_a_copy_off_the_tape_at_fees_no_payload_names_is_the_runs_by_the_blocks(our_slot):
    """A1: a copy into a coin the recorder does not follow (20 Nov 17:31: a PumpSwap coin the tape held no trade of) is
    read off the chain. At fees no payload names it was the owner's; the blocks say it stood right behind the copied
    wallet's buy, as the measure stage reads them (chain_landing.read_landing)."""
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(our_slot, swap("ours", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.0015, tip=0.12))
    whose, coins = _chain_whose(chain, COPY)
    assert whose == "ours" and coins.known(COIN) == "ours"
    # and kept for the run to say once: what it paid, behind whom
    assert [(o["mint"], o["leader"], o["priority"], o["tip"]) for o in coins.odd] == [
        (COIN, CW, 1_500_000, 120_000_000)
    ]


def test_a_hand_buy_off_the_tape_with_no_copied_buy_before_it_is_the_owners_still():
    """A1: the owner's hand buy of a coin off the tape (H8WTvo, on a program the recorder does not read): the copied
    wallet bought nothing near it, and it stays the owner's."""
    chain = Chain()
    chain.put(S - 40, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S, swap("hand", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.000006121, tip=0.026993879))
    whose, _coins = _chain_whose(chain, COPY)
    assert whose == "owner"
    # and with no wallet copied the chain is asked nothing more than before: the fees decide
    chain2 = Chain()
    chain2.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain2.put(S, swap("hand", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.000006121, tip=0.026993879))
    whose, _coins = _chain_whose(chain2, {"singleSnipe": True, "priorityFee": 0.001, "tipAmount": 0.199})
    assert whose == "owner" and not [c for c in chain2.calls if c[0] in ("getBlock", "getBlocks")]


def test_a_chain_that_cannot_place_the_buy_tells_nothing_yet():
    """A1: while the blocks cannot be read, a buy at fees no payload names behind a wallet the run copies is nobody's
    yet (held as the run's, never sold by it: the runner's rule for a coin nothing tells), never the owner's."""
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    chain.put(S, swap("ours", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.0015, tip=0.12))
    real = chain.get_signatures

    async def refuse(address, **kw):
        if address == CW:
            raise RuntimeError("node refused")
        return await real(address, **kw)

    chain.get_signatures = refuse
    whose, coins = _chain_whose(chain, COPY)
    assert whose is None and coins.known(COIN) is None


def test_an_old_buy_off_the_tape_is_told_by_the_measure_stages_fill_not_by_a_long_walk():
    """A1: at a run's start a coin held for hours is read too. The copied wallets' signatures are not walked back that
    far (a big wallet makes thousands of transactions a day, and the read has 20 s): the measure stage has placed the
    buy by then, and its fill says whether it stood behind a copied wallet."""
    chain = Chain()
    chain.put(S, swap("lead", CW, COIN, tokens=3_000_000_000, sol=3.0, priority=0.002, tip=0.001))
    ours = swap("ours", ME, COIN, tokens=38_000_000, sol=1.0, priority=0.0015, tip=0.12)
    chain.put(S, ours)
    sig = ours["transaction"]["signatures"][0]
    row = {"sig": sig, "ts": chain.ts(S), "slot": S, "wallet": ME, "mint": COIN, "side": "buy", "sol": 1.0,
           "tokens": 38_000_000.0, "priority_lamports": 1_500_000, "tip_lamports": 120_000_000, "wallet_copied": CW,
           "trades_behind": 0, "landing_src": "chain", "blocks_after": 0, "landing_verdict": "first"}  # fmt: skip
    late = chain.ts(S) + 7_200
    store = Store(":memory:")
    whose, _coins = _chain_whose(chain, COPY, store=store, now=late, since=0)
    assert whose == "owner"  # nothing on record places it: its fees decide, as before
    assert not [c for c in chain.calls if c[0] == "getSignaturesForAddress" and c[1] == CW]
    store.put_fill(row)
    whose, _coins = _chain_whose(chain, COPY, store=store, now=late, since=0)
    assert whose == "ours"
    store.close()


# -- A1: the measure stage counts such a copy and says it once ---------------------------------------------------------


class _Notes:
    def __init__(self):
        self.lines = []

    def write(self, text, level="info"):
        self.lines.append(text)


def _measured(tmp_path):
    """A copy sniper of LEAD on record; the tape holds a copy of ours one slot behind LEAD's buy at fees no payload
    names, and the owner's hand buy of another coin twenty slots behind LEAD's buy of that one."""
    from snipetech.config import Config
    from snipetech.service import Service
    from snipetech.solana.txmeta import TxMeta

    from .synth import Market, addr

    st = Store(tmp_path / "t.db")
    cfg = Config()
    cfg.storage.db_path, cfg.operator.wallet = str(tmp_path / "t.db"), W
    st.add_oktech_sniper({"id": "cs1", "external_id": "cs1", "kind": "copy", "source": LEAD, "name": "copy lead",
                          "config_hash": "h", "created_at": NOW - 86_400, "payload": PAYLOAD})  # fmt: skip
    dev = addr("a2-dev")
    m = Market(st, addr("a2-copied"), dev, NOW - 3_000, SLOT)
    m.buy(dev, 1.0, 0)
    m.buy(LEAD, 1.0, 100)
    m.buy(W, 1.0, 101, sig="odd-copy")
    m.commit()
    h = Market(st, addr("a2-hand"), dev, NOW - 3_000, SLOT + 5_000)
    h.buy(dev, 1.0, 0)
    h.buy(LEAD, 1.0, 100)
    h.buy(W, 2.5, 120, sig="hand-buy")
    h.commit()

    def meta(sig, fees):
        return TxMeta(sig=sig, slot=0, block_time=0, version=0, fee_payer=W, signers=[W], n_signatures=1,
                      fee=5000 + fees[0], priority_lamports=fees[0], cu_limit=200_000, cu_price=5000,
                      cu_consumed=60_000, tips=[{"dest": "T", "lamports": fees[1], "service": "jito"}])  # fmt: skip

    metas = {"odd-copy": meta("odd-copy", ODD), "hand-buy": meta("hand-buy", HAND)}
    return Service(cfg, store=st), st, cfg, metas


def _measure(svc, cfg, metas, notes, now=NOW + 3_600):
    from snipetech.measure.run import run_measure

    async def ensure(sigs):
        svc.store.add_tx_meta([metas[s] for s in sigs if s in metas])
        return len(sigs)

    async def none(wallet, since_ts):
        return []

    return asyncio.run(run_measure(svc, cfg, now, since_ts=NOW - 7_200, ensure_meta=ensure, failed_sigs=none,
                                   notes=notes))  # fmt: skip


def test_the_measure_stage_counts_a_copy_at_fees_no_payload_names_and_says_it_once(tmp_path):
    """A1: the measure stage's own outputs hold the bot's buys only (bot_buy); a copy at a pair no payload names was
    the owner's there too. It is counted, and the operator's notes are told once, a line a transaction: OKTech may
    have changed what it pays. The owner's hand buy twenty slots behind the copied wallet is neither."""
    svc, st, cfg, metas = _measured(tmp_path)
    try:
        notes = _Notes()
        out = _measure(svc, cfg, metas, notes)
        marks = fee_marks(st)
        copy, hand = st.fill("odd-copy"), st.fill("hand-buy")
        assert copy["wallet_copied"] == LEAD and hand["wallet_copied"] == LEAD  # both inside the measure's 150 slots
        assert bot_buy(copy, marks) and not bot_buy(hand, marks)
        assert (
            sum(r["n"] for r in st.bid_model()) == 1 and sum(b["n"] for b in out["landing_by_progress"].values()) == 1
        )
        said = [x for x in notes.lines if "paid fees no sniper of ours is set to pay" in x]
        assert len(said) == 1, notes.lines
        assert said[0].startswith(
            "[measure] a copy behind CyaE1V… paid fees no sniper of ours is set to pay (priority "
            "0.0015, tip 0.12 SOL): counted as the bot's; OKTech may have changed what it pays"
        )
        again = _Notes()
        _measure(svc, cfg, metas, again, now=NOW + 3_900)
        assert not [x for x in again.lines if "paid fees no sniper" in x]  # once a transaction
    finally:
        st.close()


def test_the_day_guard_counts_such_a_copy_and_never_the_owners_hand_buy(tmp_path):
    """A1, through a reader that only knows `bot_buy` and `fee_marks` (operator/risk_guard.py, the wallet-wide day
    stop): the copy at fees no payload names is a trade of the bot's there too, bought today and gone from the wallet
    with no sale seen, so its whole cost counts; the hand buy twenty slots behind the copied wallet never does."""
    from snipetech.operator import risk_guard

    svc, st, cfg, metas = _measured(tmp_path)
    try:
        _measure(svc, cfg, metas, _Notes())
        copy = st.fill("odd-copy")
        cost = copy["sol"] + (sum(ODD) + 5000) / 1e9
        day = risk_guard.day_result(st, W, NOW + 3_600, [], per_tx_sol=0.0035)
        assert day["total_sol"] == pytest.approx(-cost, abs=1e-6), day
    finally:
        st.close()
