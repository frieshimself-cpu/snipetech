"""Round 3 of the 22-23 Nov audit, group "ceiling": the price limit OKTech puts on our own buys.

C1: the measure stage keeps each buy's and failed buy's limit on its fills row (`limit_mcap_sol`) and where the buy
stood against it (`fill_mcap_sol`), and says once a sniper a day when a limit is far from the ceiling the sniper was
sent. The transactions are the sniper wallet's own of the 23 Nov smoke test (tests/fixtures/buy_limits_23nov.json),
served by the test chain as the RPC serves them; nothing here reaches a network.
C2: a wallet rule's `max_fill_mcap` holds the lab and the paper test to that same limit: our own fill at the scored
landing, and a moment over it is a refused send.
C3: a run whose scan of the wallet's signatures failed leaves the measure stage's cursor where it was and says so.
C4: how long an exit can hold is one number, the simulator's own (its timer, an extension, a dead exit's pause, its
parts): the lab checks the tape for it, each exit's own hold too, and the paper run settles by it and says a later
settle time once a rule."""

import asyncio
import json
import time
from pathlib import Path

import pytest

from snipetech.analysis import lab as L
from snipetech.analysis import paper
from snipetech.analysis.lab import collect, parse_trigger, rank
from snipetech.config import Config
from snipetech.measure import run as M
from snipetech.measure.run import run_measure
from snipetech.oktech import COPY_MAX_MCAP_USD
from snipetech.service import Service
from snipetech.sim.exits import ExitParams
from snipetech.solana.buy_limit import BuyLimit
from snipetech.solana.programs import PUMP_PROGRAM, WSOL_MINT
from snipetech.solana.txmeta import PumpIx, TxMeta
from snipetech.store import Store

from .chain import Chain
from .synth import Market, addr

SMOKE = json.loads((Path(__file__).parent / "fixtures" / "buy_limits_23nov.json").read_text())
ME = SMOKE["wallet"]
T0, SLOT0 = 1_795_403_861, 472_788_910  # 23 Nov 2026 03:17:41 UTC: the smoke test's first send
NOW = T0 + 7200  # the test chain runs 0.4 s a slot: its last send lands an hour after the first
LEAD, LEAD2 = addr("Cented"), addr("Decu")
SOL_USD = 69.99768  # the server's own SOL price that morning


def tx(prefix: str) -> dict:
    (one,) = [t for sig, t in SMOKE["txs"].items() if sig.startswith(prefix)]
    return one


def sig(prefix: str) -> str:
    return tx(prefix)["transaction"]["signatures"][0]


class Notes:
    def __init__(self):
        self.lines = []

    def write(self, text, level="info"):
        self.lines.append(text)


def _svc(tmp_path, chain=None):
    st = Store(tmp_path / "t.db")
    cfg = Config()
    cfg.storage.db_path = str(tmp_path / "t.db")
    cfg.operator.wallet = ME
    svc = Service(cfg, store=st)
    if chain is not None:
        svc._rpc = chain
    return svc, st, cfg


def _sniper(st, sid="smoke-1", leader=LEAD, cap=4600, created=T0 - 23, buy=0.02, priority=0.001):
    """A copy sniper as `copylist --max-mcap` makes it: the ceiling in dollars on the copied wallet."""
    wallet = {"address": leader, "minSol": 2.0} | ({"maxMcapUsd": cap} if cap else {})
    st.add_oktech_sniper({"id": sid, "external_id": sid, "kind": "copy", "source": leader,
                          "name": f"snipetech copy {sid} arm:smoke66", "config_hash": "h", "created_at": created,
                          "payload": {"ctbotWallets": [wallet], "buyAmount": buy, "priorityFee": priority,
                                      "tipAmount": 0.002}})  # fmt: skip


def _sol_price(st, usd=SOL_USD, at=T0 - 1200):
    st._exec("INSERT OR REPLACE INTO assets (mint, symbol, decimals, price_usd, updated) VALUES (?,?,?,?,?)",
             (WSOL_MINT, "SOL", 9, usd, int(at)))  # fmt: skip


def _smoke_chain() -> Chain:
    """The six saved sends in the blocks they landed in."""
    chain = Chain(t0=T0, slot0=SLOT0)
    for one in sorted(SMOKE["txs"].values(), key=lambda t: t["slot"]):
        chain.put(one["slot"], {k: v for k, v in one.items() if k not in ("slot", "blockTime")})
    return chain


def test_the_measure_stage_keeps_every_sends_limit_and_where_it_stood(tmp_path):
    """One line of SQL then shows the smoke test as the session had to decode it by hand: the fill under its ceiling,
    the refusals over theirs, and the sends whose ceiling OKTech worked out at $200 a SOL."""
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    notes = Notes()
    try:
        _sniper(st)
        _sol_price(st)
        out = asyncio.run(run_measure(svc, cfg, NOW, since_ts=T0 - 60, notes=notes))
        assert out["buys"] == 1 and out["failed"] == 5
        one_line = "SELECT sig, side, limit_mcap_sol, fill_mcap_sol FROM fills WHERE wallet=? ORDER BY ts"
        shown = {r[0][:8]: r[1:] for r in st._q(one_line, (ME,))}
        assert shown["gXv9uz3u"] == ("buy", pytest.approx(64.607, abs=1e-3), pytest.approx(53.879, abs=1e-3))
        assert shown["4BUCDA8G"] == ("failed", pytest.approx(64.625, abs=1e-3), pytest.approx(67.250, abs=1e-3))
        assert shown["4hC1n7oF"] == ("failed", pytest.approx(23.0, abs=1e-3), pytest.approx(40.490, abs=1e-3))
        assert shown["3Tx3daSi"] == ("failed", pytest.approx(23.0, abs=1e-3), pytest.approx(129.368, abs=1e-3))
        assert shown["4YtcQUzh"] == ("failed", pytest.approx(64.417, abs=1e-3), None)  # failed before the swap ran
        assert shown["5ePdm8Lk"] == ("failed", None, None)  # a sale's failed twin: no buy, no limit
        # a refusal reads in words on its row, and the coin of a buy through two pools is named
        refused = st.fill(sig("4hC1n7oF"))
        assert refused["exit_reason"].startswith("over its price limit: the buy would have filled at 40.5 SOL of "
                                                 "market cap, its limit was 23.0: ")  # fmt: skip
        assert "6042" in refused["exit_reason"] and M.is_refused(refused["exit_reason"])
        assert not M.is_refused(st.fill(sig("4YtcQUzh"))["exit_reason"])
        assert st.fill(sig("4YtcQUzh"))["mint"].startswith("2eWqySqV")
        # what the run says: how many limits it read, how many buys were refused on theirs
        assert out["limits"]["read"] == 5 and out["limits"]["refused"] == 3
        # and the ceiling far from what the sniper was sent, in the output and in the notes, once
        (line,) = out["limits"]["far"]
        assert notes.lines == [line]
        for words in ("OKTech turned the $4,600 market-cap ceiling of sniper snipetech copy smoke-1 arm:smoke66",
                      "into 23.0 SOL of market cap", "a SOL price of $200.00", "ours $70.00", "65.7 SOL was meant",
                      "every pump.fun coin starts at 28 SOL", "could only fail"):  # fmt: skip
            assert words in line, words
        assert st.coverage(M.CEILING_SAID_KEY)["said"] == {"smoke-1": "2026-11-23"}
    finally:
        st.close()


def test_a_send_is_read_once_and_a_second_run_keeps_what_the_first_wrote(tmp_path):
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    notes = Notes()
    try:
        _sniper(st)
        _sol_price(st)
        asyncio.run(run_measure(svc, cfg, NOW, since_ts=T0 - 60, notes=notes))
        before = {s: st.fill(s) for s in SMOKE["txs"]}
        asked = len(chain.calls)
        out = asyncio.run(run_measure(svc, cfg, NOW + 300, since_ts=T0 - 60, notes=notes))
        # no transaction is asked for a second time: each failed buy's row holds its limit, the sale's twin is no buy
        assert [c for c in chain.calls[asked:] if c[0] == "getTransaction"] == []
        assert {s: st.fill(s) for s in SMOKE["txs"]} == before
        assert len(notes.lines) == 1 and out["limits"]["far"] == []  # nothing new was read: nothing is said again
    finally:
        st.close()


def test_a_buy_on_the_tape_gets_its_limit_from_its_fingerprint(tmp_path):
    """A copy the tape holds is a trade in SOL, and its stored fingerprint has the instruction's two numbers: no
    second look at the chain. The fill's own market cap is the SOL the buy spent over the coins the tape says it got."""
    svc, st, cfg = _svc(tmp_path)
    try:
        _sniper(st, created=T0 - 600)
        m = Market(st, addr("coin"), addr("dev"), T0, SLOT0)
        m.buy(addr("dev"), 1.0, 0)
        m.buy(LEAD, 2.0, 2)
        ours = m.buy(ME, 0.02, 2)
        m.commit()
        least = 309_565_217_392  # at least so many coins for 0.02 SOL: 64.6 SOL of market cap
        meta = TxMeta(sig=ours.sig, slot=ours.slot, block_time=ours.ts, version=0, fee_payer=ME, signers=[ME],
                      n_signatures=1, fee=1_005_000, priority_lamports=1_000_000, cu_limit=100_000, cu_price=1,
                      cu_consumed=60_000, pump_ixs=[PumpIx(PUMP_PROGRAM, "buy_exact_quote_in_v3", ME,
                                                           {"spendable_quote_in": 20_000_000, "min_tokens_out": least},
                                                           None, m.mint)])  # fmt: skip

        async def ensure(sigs):
            st.add_tx_meta([meta] if ours.sig in sigs else [])
            return len(sigs)

        async def none(wallet, since):
            return []

        asyncio.run(run_measure(svc, cfg, T0 + 60, since_ts=T0 - 60, ensure_meta=ensure, failed_sigs=none))
        row = st.fill(ours.sig)
        assert row["wallet_copied"] == LEAD and row["limit_mcap_sol"] == pytest.approx(64.607, abs=1e-3)
        assert row["fill_mcap_sol"] == pytest.approx(20_000_000 / ours.base * 1e6, abs=1e-3)
        assert row["fill_mcap_sol"] < row["limit_mcap_sol"]  # it filled, so it stood under its ceiling

        # a later read that finds no fingerprint (a provider that answers nothing) keeps what was read
        st._exec("DELETE FROM tx_meta")

        async def nothing(sigs):
            return 0

        asyncio.run(run_measure(svc, cfg, T0 + 120, since_ts=T0 - 60, ensure_meta=nothing, failed_sigs=none))
        assert st.fill(ours.sig)["limit_mcap_sol"] == row["limit_mcap_sol"]
        assert st.fill(ours.sig)["fill_mcap_sol"] == row["fill_mcap_sol"]
    finally:
        st.close()


# -- the ceiling a sniper was sent against the limit its buy carried ---------------------------------------------------


def _row(sid, leader, cap, created=T0 - 600, removed=None, buy=0.02, name=None):
    wallet = {"address": leader} | ({"maxMcapUsd": cap} if cap else {})
    return {"id": sid, "name": name or f"sniper {sid}", "wallets": {leader}, "created_at": created,
            "removed_at": removed, "payload": {"ctbotWallets": [wallet], "buyAmount": buy}}  # fmt: skip


def _limit(least, lamports=20_000_000):
    return BuyLimit(PUMP_PROGRAM, "buy_exact_quote_in_v3", "COIN", lamports, least, True)


AT_200 = _limit(869_565_217_392)  # $4,600 at $200 a SOL: 23 SOL
AT_71 = _limit(309_565_217_392)  # $4,600 at $71.2 a SOL: 64.6 SOL


def test_a_limit_near_what_the_sniper_was_sent_is_not_said():
    rows = [_row("a", LEAD, 4600)]
    assert M.far_ceiling(AT_71, T0, rows, SOL_USD) is None  # 64.6 SOL against 65.7 meant: 1.7 % apart
    far = M.far_ceiling(AT_200, T0, rows, SOL_USD)
    assert far["sniper"] == "a" and far["sent_usd"] == 4600 and far["limit_sol"] == pytest.approx(23.0)
    assert far["sol_usd"] == pytest.approx(200.0) and far["meant_sol"] == pytest.approx(65.717, abs=1e-3)
    # 15 % is the line: 76 SOL against 65.7 meant is 15.7 % over, 75 SOL is 14.1 %
    assert M.far_ceiling(_limit(round(20_000_000 / 76.0 * 1e6)), T0, rows, SOL_USD) is not None
    assert M.far_ceiling(_limit(round(20_000_000 / 75.0 * 1e6)), T0, rows, SOL_USD) is None


def test_the_ceiling_every_copy_carries_is_no_rule_and_is_never_said():
    """A sniper with no ceiling of its own is sent the $50,000,000 every copy carries (oktech.keep_to_coins): far above
    any coin, and a buy with no real limit beside it is nobody's fault."""
    rows = [_row("plain", LEAD, COPY_MAX_MCAP_USD)]
    assert M.far_ceiling(AT_200, T0, rows, SOL_USD) is None
    assert M.far_ceiling(_limit(1), T0, [_row("a", LEAD, 4600)], SOL_USD) is None  # at least one raw unit: no limit
    assert M.far_ceiling(AT_200, T0, [_row("old", LEAD, None)], SOL_USD) is None  # sent no ceiling at all


def test_the_send_is_laid_at_the_sniper_that_made_it():
    a, b = _row("a", LEAD, 4600), _row("b", LEAD2, 9200)
    # by the wallet it copied, where that is known
    assert M.far_ceiling(AT_200, T0, [a, b], SOL_USD, leader=LEAD)["sniper"] == "a"
    assert M.far_ceiling(AT_200, T0, [a, b], SOL_USD, leader=LEAD2)["sniper"] == "b"
    # the limit of b's own ceiling ($9,200 at $71.2: 129 SOL) is far from a's and near b's: not said of either
    assert M.far_ceiling(_limit(round(20_000_000 / 129.2 * 1e6)), T0, [a, b], SOL_USD) is None
    # else by the size it buys at, then by the nearest ceiling
    big = _row("big", LEAD2, 4600, buy=1.0)
    assert M.far_ceiling(AT_200, T0, [a, big], SOL_USD)["sniper"] == "a"
    assert M.far_ceiling(_limit(43_478_260_869_600, 1_000_000_000), T0, [a, big], SOL_USD)["sniper"] == "big"
    # a sniper that was not alive at the send is never it
    gone = _row("gone", LEAD, 4600, created=T0 - 9000, removed=T0 - 3000)
    assert M.far_ceiling(AT_200, T0, [gone], SOL_USD) is None
    # a copied wallet no sniper with a ceiling follows: the send is another sniper's, and nothing is said
    assert M.far_ceiling(AT_200, T0, [a], SOL_USD, leader=LEAD2) is None


def test_a_far_ceiling_is_said_once_a_sniper_a_day_and_never_on_an_old_price(tmp_path):
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    notes = Notes()
    try:
        _sniper(st)
        _sol_price(st, at=NOW - 3 * 86400)  # a price three days old says nothing about today's
        out = asyncio.run(run_measure(svc, cfg, NOW, since_ts=T0 - 60, notes=notes))
        assert notes.lines == [] and out["limits"]["far"] == []
        assert "SOL price" in out["limits"]["unchecked"] and out["limits"]["read"] == 5
        assert st.fill(sig("4hC1n7oF"))["limit_mcap_sol"] == pytest.approx(23.0)  # the limits are kept all the same
    finally:
        st.close()


def test_a_backfill_of_old_sends_writes_the_limits_and_wakes_nobody(tmp_path):
    """`measure backfill` over the smoke test's window days later fills the columns; a ceiling that was wrong then is
    in its output, and no note of today is written about a sniper of that day."""
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    notes = Notes()
    try:
        _sniper(st)
        _sol_price(st, at=NOW + 3 * 86400 - 600)
        out = asyncio.run(run_measure(svc, cfg, NOW + 3 * 86400, since_ts=T0 - 60, until_ts=NOW, notes=notes,
                                      move_cursor=False, reclassify_arms=True))  # fmt: skip
        assert len(out["limits"]["far"]) == 1 and notes.lines == []
        assert st.coverage(M.CEILING_SAID_KEY) == {}
    finally:
        st.close()


def test_a_failed_buy_is_laid_at_the_sniper_whose_wallet_bought_the_coin_just_before(tmp_path):
    """A failed buy has no place among the coin's trades, and three snipers were alive at once in the smoke test. The
    tape says which followed wallet bought the coin right before the send: that wallet's sniper sent it."""
    from snipetech.models import Trade

    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    notes = Notes()
    try:
        _sniper(st, "of-cented", LEAD, 4600)
        _sniper(st, "of-decu", LEAD2, 9200)
        _sol_price(st)
        coin = "8LXGpgYJL7JkiWcQf6HuJNvnjuo6w2cB1YtHqkU1HLYp"  # the coin of the 03:17:41 send
        st.add_trades([Trade(sig=addr("decu buys"), ev=0, slot=SLOT0, pos=7, ts=T0, mint=coin, venue="curve",
                             user=LEAD2, is_buy=True, quote=3_000_000_000, base=90_000_000_000_000, fee=37_500_000,
                             fee_bps=125, q_after=40_000_000_000, b_after=800_000_000_000_000, r_base_after=0,
                             ix="buy")])  # fmt: skip
        out = asyncio.run(run_measure(svc, cfg, NOW, since_ts=T0 - 60, notes=notes))
        first = out["limits"]["far"][0]
        assert "sniper snipetech copy of-decu arm:smoke66" in first and "$9,200" in first and "send 4hC1n7oF" in first
        assert "131.4 SOL was meant" in first
        # the PumpSwap send of 03:31 names no wallet on the tape: laid at the nearest ceiling alive, the other sniper's
        assert any("of-cented" in line and "send 3Tx3daSi" in line for line in out["limits"]["far"])
        assert sorted(st.coverage(M.CEILING_SAID_KEY)["said"]) == ["of-cented", "of-decu"] and len(notes.lines) == 2
    finally:
        st.close()


def test_a_check_that_fails_never_fails_the_measure_run(tmp_path, monkeypatch):
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    try:
        _sniper(st)
        _sol_price(st)

        def broken(*a, **kw):
            raise RuntimeError("the tape could not be read")

        monkeypatch.setattr(M, "leader_buy_at", broken)
        out = asyncio.run(run_measure(svc, cfg, NOW, since_ts=T0 - 60, notes=Notes()))
        assert out["failed"] == 5 and "the tape could not be read" in out["limits"]["unchecked"]
        assert st.fill(sig("4hC1n7oF"))["limit_mcap_sol"] == pytest.approx(23.0)
    finally:
        st.close()


def test_a_limit_that_cannot_be_read_never_costs_a_fill_its_row(tmp_path, monkeypatch):
    """The limit is a reading beside the fill. A buy whose reading raises (an instruction of a shape nobody has seen)
    keeps no limit and is counted: its fill, its landing and the run's other sends are written as they were before
    there was a limit to read. A copy at 1 SOL that is in no fill is a stray to the book."""

    def unseen(*a, **kw):
        raise RuntimeError("a shape nobody has seen")

    # the landed buy and the failed sends of the smoke test, each read from its transaction
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    try:
        _sniper(st)
        _sol_price(st)
        monkeypatch.setattr(M.buy_limit, "tx_limit", unseen)
        out = asyncio.run(run_measure(svc, cfg, NOW, since_ts=T0 - 60, notes=Notes()))
        assert out["buys"] == 1 and out["failed"] == 5 and "scan_failed" not in out
        landed = st.fill(sig("gXv9uz3u"))
        assert landed["side"] == "buy" and landed["sol"] == pytest.approx(0.02, abs=1e-3)
        assert landed["limit_mcap_sol"] is None and landed["fill_mcap_sol"] is None
        refused = st.fill(sig("4hC1n7oF"))  # the program's own error still says why it failed
        assert refused["side"] == "failed" and refused["limit_mcap_sol"] is None and "6042" in refused["exit_reason"]
        assert out["limits"]["read"] == 0
        assert out["limits"]["unread"].startswith("5 buys whose limit could not be read (")
        assert "RuntimeError: a shape nobody has seen" in out["limits"]["unread"]
    finally:
        st.close()

    # a buy the tape holds, read from its fingerprint
    svc, st, cfg = _svc(tmp_path / "tape")
    try:
        m = Market(st, addr("coin"), addr("dev"), T0, SLOT0)
        m.buy(LEAD, 2.0, 2)
        ours = m.buy(ME, 0.02, 2)
        m.commit()
        meta = TxMeta(sig=ours.sig, slot=ours.slot, block_time=ours.ts, version=0, fee_payer=ME, signers=[ME],
                      n_signatures=1, fee=1_005_000, priority_lamports=1_000_000, cu_limit=100_000, cu_price=1,
                      cu_consumed=60_000, pump_ixs=[PumpIx(PUMP_PROGRAM, "buy_exact_quote_in_v3", ME,
                                                           {"spendable_quote_in": 20_000_000, "min_tokens_out": 5},
                                                           None, m.mint)])  # fmt: skip

        async def ensure(sigs):
            st.add_tx_meta([meta] if ours.sig in sigs else [])
            return len(sigs)

        async def none(wallet, since):
            return []

        monkeypatch.setattr(M.buy_limit, "meta_limit", unseen)
        out = asyncio.run(run_measure(svc, cfg, T0 + 60, since_ts=T0 - 60, ensure_meta=ensure, failed_sigs=none))
        row = st.fill(ours.sig)
        assert out["buys"] == 1 and row["side"] == "buy" and row["limit_mcap_sol"] is None
        assert out["limits"]["unread"].startswith("1 buy whose limit could not be read (")
    finally:
        st.close()


def test_a_send_no_sniper_of_ours_paid_for_is_never_laid_at_one(tmp_path):
    """The owner trades by hand in the sniper wallet, through tools whose limit is their own slippage. Such a send,
    refused or landed, keeps its limit on its row like any other, but it is no sniper's: a limit far from a sniper's
    ceiling is said only of the bot's own sends, told as the measure stage tells them everywhere (measure/ours.py: a
    sniper's fees, or a copy's shape). The snipers on record here pay the live run's 0.0011 SOL priority, and the
    smoke test's sends paid 0.001 with no copied buy on the tape before them: to this record each is a hand send."""
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    notes = Notes()
    try:
        _sniper(st, priority=0.0011)
        _sol_price(st)
        out = asyncio.run(run_measure(svc, cfg, NOW, since_ts=T0 - 60, notes=notes))
        assert out["limits"]["read"] == 5 and out["limits"]["refused"] == 3
        assert st.fill(sig("4hC1n7oF"))["limit_mcap_sol"] == pytest.approx(23.0)  # kept all the same
        assert out["limits"]["far"] == [] and notes.lines == [] and st.coverage(M.CEILING_SAID_KEY) == {}
    finally:
        st.close()


def test_the_runs_own_buy_is_held_to_its_sniper_and_a_hand_buy_before_it_is_not(tmp_path):
    """The live run's snipers pay 0.0011 SOL priority and a 0.002 tip, so that a book sniper's buy can never read as
    the run's. A buy at those fees right behind the followed wallet is the run's, and its limit is held to its
    sniper's ceiling; the owner's hand buy a minute earlier, at the sniper's own size, at the owner's fees and behind
    no followed wallet, is not, however far its slippage puts its limit."""
    svc, st, cfg = _svc(tmp_path)
    notes = Notes()
    try:
        _sniper(st, "run-cented", LEAD, 4600, created=T0 - 600, buy=1.0, priority=0.0011)
        _sol_price(st)
        hand_coin = Market(st, addr("hand coin"), addr("hand dev"), T0, SLOT0)
        hand_coin.buy(addr("hand dev"), 1.0, 0)
        hand = hand_coin.buy(ME, 1.0, 2)
        hand_coin.commit()
        coin = Market(st, addr("copied coin"), addr("copied dev"), T0 + 60, SLOT0 + 150)
        coin.buy(addr("copied dev"), 1.0, 0)
        coin.buy(LEAD, 2.0, 2)
        ours = coin.buy(ME, 1.0, 2)
        coin.commit()

        def meta(t, priority, tip, name, args):
            return TxMeta(sig=t.sig, slot=t.slot, block_time=t.ts, version=0, fee_payer=ME, signers=[ME],
                          n_signatures=1, fee=5_000 + priority, priority_lamports=priority, cu_limit=100_000,
                          cu_price=priority * 10, cu_consumed=60_000,
                          tips=[{"dest": addr("tip"), "lamports": tip, "service": "astralane"}] if tip else [],
                          pump_ixs=[PumpIx(PUMP_PROGRAM, name, ME, args, None, t.mint)])  # fmt: skip

        # the owner's tool: 0.00025 SOL priority, no tip, a limit 20 % over a 100 SOL coin (120 SOL)
        hand_ix = {"spendable_sol_in": 1_000_000_000, "min_tokens_out": 8_333_333_333_333}
        # the run's copy: OKTech worked $4,600 out at $40 a SOL (115 SOL), not ours ($70: 65.7 SOL)
        copy_ix = {"spendable_quote_in": 1_000_000_000, "min_tokens_out": 8_695_652_173_913}
        metas = {hand.sig: meta(hand, 250_000, 0, "buy_exact_sol_in", hand_ix),
                 ours.sig: meta(ours, 1_100_000, 2_000_000, "buy_exact_quote_in_v3", copy_ix)}  # fmt: skip

        async def ensure(sigs):
            st.add_tx_meta([metas[s] for s in sigs if s in metas])
            return len(sigs)

        async def none(wallet, since):
            return []

        out = asyncio.run(run_measure(svc, cfg, T0 + 300, since_ts=T0 - 60, ensure_meta=ensure, failed_sigs=none,
                                      notes=notes))  # fmt: skip
        assert out["limits"]["read"] == 2
        assert st.fill(hand.sig)["limit_mcap_sol"] == pytest.approx(120.0, abs=1e-3)  # its limit is on its row
        (line,) = out["limits"]["far"]
        assert f"send {ours.sig[:8]}" in line and "into 115.0 SOL of market cap" in line and "$40.00" in line
        assert notes.lines == [line]
    finally:
        st.close()


# -- C2: the lab and the paper test gate on our own fill -------------------------------------------------------------
#
# A copy sniper's ceiling is a limit on OUR fill (above), and the lab's `max_mcap` asks the copied buy. `max_fill_mcap`
# asks our own fill at the scored landing: over it the send is refused, no position, and one failed send is charged.

W0 = int(time.time()) - 7500  # an hour of tape before the freeze, an hour after it
FREEZE, RUN = W0 + 3600, W0 + 7200
KOL = addr("c2-followed")
HOLD60 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60.0, sell_on_dev_sell=False)
PLAIN = {"kind": "kol", "group": "wallet", "sol": 1, "max_mcap": 40}  # the copied buy stands at 32.8 SOL: admitted
CEILING = PLAIN | {"max_fill_mcap": 40}


def _c2_coin(st, k: int, ts: int, ahead: float) -> str:
    """A coin the followed wallet buys with 2 SOL after the dev's 0.5 (32.8 SOL of market cap after its buy); two
    other wallets buy `ahead` SOL each right behind it, before a copy at +2x lands; eight buyers then lift the price."""
    m = Market(st, addr(f"c2-coin-{k}"), addr(f"c2-dev-{k}"), ts, 500_000_000 + (ts - W0) * 5 // 2)  # 0.4 s a slot
    m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
    m.buy(KOL, 2.0, 50)
    m.buy(addr(f"c2-first-{k}"), ahead, 51)
    m.buy(addr(f"c2-second-{k}"), ahead, 52)
    for j in range(8):
        m.buy(addr(f"c2-fan-{k}-{j}"), 0.5, 100 + 3 * j)
    m.sell(addr(f"c2-first-{k}"), 1.0, 400)
    m.commit()
    return m.mint


def _fill_at(sol_before: float, buy_sol: float = 1.0) -> float:
    """The market cap at our own fill on a curve that has taken `sol_before` SOL: our SOL, fee included, over our
    coins, times a billion coins."""
    q = (30.0 + sol_before) * 1e9
    b = 30e9 * 1_073_000_000_000_000 / q
    net = buy_sol * 1e9 / 1.0125  # the curve's 1.25 % fee comes out of what we send
    return buy_sol * 1e9 / (b * net / (q + net)) * 1e6


def _c2_world(st, n=24, t0=W0 + 100):
    """Every third coin has two 5 SOL buys between the followed wallet and us; the others two of 0.05 SOL."""
    st.set_label(KOL, "followed", "wallet", "file")
    crowded, quiet = [], []
    for k in range(n):
        big = k % 3 == 1
        (crowded if big else quiet).append(_c2_coin(st, k, t0 + k * 120, 5.0 if big else 0.05))
    return crowded, quiet


def _c2_cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    return cfg


def test_the_fill_ceiling_is_a_wallet_rules_own_field_and_its_label_says_it():
    rule = parse_trigger({"kind": "kol", "group": "wallet", "sol": 1, "max_fill_mcap": 66})
    assert rule.max_fill_mcap == 66.0
    assert rule.label() == ("a tracked wallet (wallet) makes its first buy in the coin, >= 1 SOL, our fill at most "
                            "66 SOL of market cap")  # fmt: skip
    both = parse_trigger(CEILING | {"max_mcap": 65, "max_fill_mcap": 66}).label()
    assert both.endswith("market cap <= 65 SOL, our fill at most 66 SOL of market cap")
    assert parse_trigger(PLAIN).max_fill_mcap == 0.0 and "our fill" not in parse_trigger(PLAIN).label()
    with pytest.raises(ValueError, match="does not apply"):
        parse_trigger({"kind": "hunt", "sol": 4, "window": 1.5, "max_fill_mcap": 66})  # no copy, no ceiling
    with pytest.raises(ValueError, match="between"):
        parse_trigger(PLAIN | {"max_fill_mcap": -1})
    # 23 Nov: OKTech sent 23 SOL, under the 28 SOL every pump.fun coin starts at: such a rule can only be refused
    with pytest.raises(ValueError, match="28 SOL"):
        parse_trigger(PLAIN | {"max_fill_mcap": 23})


def test_a_rule_frozen_before_the_fill_ceiling_keeps_its_id():
    ex = HOLD60.to_dict()
    now = parse_trigger(PLAIN).__dict__
    before = {k: v for k, v in now.items() if k != "max_fill_mcap"}  # as the store holds a rule of 22 Nov
    assert paper.strategy_id(now, ex, "+6", 0, 1.0) == paper.strategy_id(before, ex, "+6", 0, 1.0)
    assert L.Trigger(**before) == parse_trigger(PLAIN)  # and it is read as a rule with no fill ceiling
    assert paper.strategy_id(parse_trigger(CEILING).__dict__, ex, "+6", 0, 1.0) != paper.strategy_id(
        now, ex, "+6", 0, 1.0
    )


def test_a_moment_the_copied_buys_ceiling_admits_is_refused_by_the_fill_ceiling_behind_two_big_buys():
    st = Store(":memory:")
    crowded, quiet = _c2_world(st)
    st.set_coverage("tape", {"spans": [[W0, FREEZE]]})
    data = collect(st, _c2_cfg(), 1.0, 0, "+2x", triggers=[parse_trigger(PLAIN), parse_trigger(CEILING)], mates={},
                   exits=[HOLD60], workers=1)  # fmt: skip
    st.close()
    plain = {r["mint"]: r for r in data["rows"] if r["trigger"] == 0}
    gated = {r["mint"]: r for r in data["rows"] if r["trigger"] == 1}
    # the copied buy's ceiling admits every moment: the coin stood at 32.8 SOL after the followed wallet's buy
    assert sorted(plain) == sorted(gated) == sorted(crowded + quiet) and len(crowded) == 8
    assert all(r["mcap_sol"] == pytest.approx(32.81, abs=0.01) and not r.get("refused") for r in plain.values())
    # our own fill behind two 5 SOL buys stands at 58 SOL: over the ceiling of 40, a refused send
    for mint in crowded:
        r = gated[mint]
        assert r["refused"] is True and r["fill_mcap_sol"] == pytest.approx(_fill_at(12.5), rel=1e-6)
        assert 58.0 < r["fill_mcap_sol"] < 58.5
        # no position: what the send cost, on every exit; a failed send pays its priority and the base fee, no tip
        assert r["pnl"] == [-L.REFUSED_SEND_SOL] and L.REFUSED_SEND_SOL == pytest.approx(0.001005)
        assert "impact" not in r and plain[mint]["fill_mcap_sol"] == r["fill_mcap_sol"]
    # behind two 0.05 SOL buys it stands at 34 SOL: admitted, and traded exactly as without the ceiling
    for mint in quiet:
        r = gated[mint]
        assert not r.get("refused") and r["fill_mcap_sol"] == pytest.approx(_fill_at(2.6), rel=1e-6)
        assert r["pnl"] == plain[mint]["pnl"] and r["impact"] == plain[mint]["impact"]
    # the ranking counts both, and the refusals' cost is in the rule's total
    rows = {r["trigger_spec"]["max_fill_mcap"]: r for r in rank(data, _c2_cfg(), top=10, min_train=15)}
    block = rows[40.0]["fill_ceiling"]
    assert (block["max_fill_mcap"], block["admitted"], block["refused"]) == (40.0, 16, 8)
    assert block["refused_send_sol"] == pytest.approx(0.001005) and "0.001" in block["note"]
    newer = sorted(gated.values(), key=lambda r: r["ts"])[int(24 * 0.6) :]
    assert block["test"]["refused"] == sum(1 for r in newer if r.get("refused")) > 0
    assert block["test"]["admitted"] + block["test"]["refused"] == rows[40.0]["test"]["n"] == len(newer)
    assert rows[40.0]["test"]["total"] == pytest.approx(sum(r["pnl"][0] for r in newer))
    traded = [r["pnl"][0] for r in newer if not r.get("refused")]
    assert block["test"]["admitted_mean"] == pytest.approx(sum(traded) / len(traded))
    assert "fill_ceiling" not in rows[0.0]  # a rule without the ceiling says nothing of it
    # where the rule's moments stood by our own fill, beside the bands by the copied buy's market cap
    by_fill = {b["band"]: b["n"] for b in rows[0.0]["bands"]["fill_mcap_sol"]}
    assert by_fill == {"under 50": 16, "50-100": 8, "100-200": 0, "200-410": 0, "410 and up": 0}


def test_a_frozen_rule_keeps_its_fill_ceiling_and_the_forward_run_refuses_by_it():
    st = Store(":memory:")
    _c2_world(st)
    st.set_coverage("tape", {"spans": [[W0, FREEZE]]})
    out = paper.freeze(st, _c2_cfg(), top=4, buy_sol=1.0, per_tx_sol=0.0035, triggers=[parse_trigger(CEILING)],
                       now=FREEZE, copy_position="+2x")  # fmt: skip
    (frozen,) = out["frozen"]
    assert frozen["trigger"]["max_fill_mcap"] == 40.0 and "our fill at most 40 SOL of market cap" in frozen["label"]
    assert frozen["lab"]["fill_ceiling"]["refused"] == 8
    quiet = [_c2_coin(st, 100 + k, FREEZE + 300 + k * 120, 0.05) for k in range(3)]
    crowded = [_c2_coin(st, 150 + k, FREEZE + 360 + k * 120, 5.0) for k in range(2)]
    st.set_coverage("tape", {"spans": [[W0, RUN]]})
    ran = paper.run(st, _c2_cfg(), now=RUN)
    assert ran["new_trades"] == 5
    trades = {r["mint"]: r for r in st.paper_trades(frozen["id"])}
    assert sorted(trades) == sorted(quiet + crowded)
    for mint in crowded:  # refused: no position, one failed send charged, and the row says why
        t = trades[mint]
        assert t["reason"] == "refused" and t["pnl"] == pytest.approx(-0.001005)
        assert t["data"]["refused"] is True and t["data"]["fill_mcap_sol"] == pytest.approx(_fill_at(12.5), rel=1e-4)
        assert t["exit_ts"] == t["entry_ts"]
    for mint in quiet:
        t = trades[mint]
        assert t["reason"] != "refused" and "refused" not in t["data"]
        assert t["data"]["fill_mcap_sol"] == pytest.approx(_fill_at(2.6), rel=1e-4)
    assert ran["refused_sends"] == {frozen["id"]: 2}
    st.close()


# -- C3: a failed scan must not move the measure stage's cursor --------------------------------------------------------
#
# The book's stray rule (book/run._strays) and the unsold-coin watch (book/watch.py) read "the measure stage ran since
# the coin showed" (the cursor, measure:last_ts) as "the fills would hold our buy of a coin the tape does not follow".
# The stage moved the cursor also when its scan of the wallet's own signatures had failed: nothing off the tape was
# read then. Two such runs, a copy held ten minutes and a kill together called that copy a stray. And three in a row
# moved the window past the trades of the first, which no later run read.


class Down(RuntimeError):
    pass


def _flaky(chain):
    """The chain's signature listing, refusing while `down` holds True."""
    down = [False]
    listing = chain.get_signatures

    async def get_signatures(address, before=None, until=None, limit=1000):
        if down[0]:
            raise Down("the RPC answered nothing")
        return await listing(address, before=before, until=until, limit=limit)

    chain.get_signatures = get_signatures
    return down


def test_a_failed_scan_leaves_the_cursor_where_it_was_and_the_next_run_reads_its_window(tmp_path):
    from snipetech.book.watch import _measured_at

    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    down = _flaky(chain)
    try:
        first = asyncio.run(run_measure(svc, cfg, T0 - 100, notes=Notes()))  # before the first send: nothing yet
        assert "scan_failed" not in first and st.coverage(M.LAST_KEY) == {"ts": T0 - 100}
        down[0] = True
        for k in (1, 2, 3):  # a quarter of an hour without an answer, while a buy lands that the tape does not hold
            out = asyncio.run(run_measure(svc, cfg, T0 - 100 + 600 * k, notes=Notes()))
            assert out["scan_failed"].startswith("Down: the RPC answered nothing") and out["buys"] == 0
            # the record the book's stray rule and the watch read still says: not run since before the buy
            assert st.coverage(M.LAST_KEY) == {"ts": T0 - 100} and _measured_at(st) == T0 - 100
        assert st.fill(sig("gXv9uz3u")) is None  # the buy of T0 + 488 is in no fill yet
        down[0] = False
        back = asyncio.run(run_measure(svc, cfg, T0 + 2400, notes=Notes()))
        # the window starts where the last scan that worked ended (less the overlap), not ten minutes ago
        assert back["window"][0] == T0 - 100 - cfg.measure.overlap_secs and "scan_failed" not in back
        assert st.fill(sig("gXv9uz3u"))["side"] == "buy" and st.fill(sig("4hC1n7oF"))["side"] == "failed"
        assert st.coverage(M.LAST_KEY) == {"ts": T0 + 2400}
    finally:
        st.close()


def test_off_tape_transactions_that_cannot_be_fetched_hold_the_cursor_too(tmp_path):
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    fetch = chain.get_transactions
    down = [True]

    async def get_transactions(signatures):
        if down[0]:
            raise Down("the RPC answered nothing")
        return await fetch(signatures)

    chain.get_transactions = get_transactions
    try:
        asyncio.run(run_measure(svc, cfg, T0 - 100, notes=Notes()))
        out = asyncio.run(run_measure(svc, cfg, T0 + 900, notes=Notes()))  # the signatures are listed, none is read
        assert "our off-tape transactions" in out["scan_failed"] and st.coverage(M.LAST_KEY) == {"ts": T0 - 100}
        down[0] = False
        asyncio.run(run_measure(svc, cfg, T0 + 1200, notes=Notes()))
        assert st.fill(sig("gXv9uz3u"))["side"] == "buy" and st.coverage(M.LAST_KEY) == {"ts": T0 + 1200}
    finally:
        st.close()


def test_a_backfill_never_moves_the_cursor_whatever_its_scan_did(tmp_path):
    chain = _smoke_chain()
    svc, st, cfg = _svc(tmp_path, chain)
    try:
        asyncio.run(M.backfill(svc, cfg, T0 - 60, T0 + 3000, notes=Notes()))
        assert st.coverage(M.LAST_KEY) == {}
    finally:
        st.close()


# -- C4: how long an exit can hold, in one place -----------------------------------------------------------------------
#
# The lab checks that the tape holds a moment for as long as its exits can hold (`lab.exits_horizon`), and the paper run
# scores a moment once its exit has played out (`paper.settle_secs`). Round 2 taught the horizon a timer extension.
# Left: a dead exit's pause, which the simulator's own reach counts (`sim.exits.exits_reach`), and `lab.exit_gap`,
# which still read each exit's own hold as its timer alone.


def _stored(st, **rules):
    """Frozen wallet rules as the store holds them, one for each named exit."""
    trig = parse_trigger({"kind": "kol", "group": "wallet", "sol": 1}).__dict__
    out = {}
    for name, ex in rules.items():
        out[name] = {"id": paper.strategy_id(trig, ex.to_dict(), "+2x", 0, 1.0), "label": name, "trigger": trig,
                     "exit": ex.to_dict(), "position": "+2x", "latency": 0, "buy_sol": 1.0, "per_tx_sol": 0.0035,
                     "from_ts": FREEZE, "lab": {"mean": 0.1, "ev_lower": 0.05}, "landing_asked": True}  # fmt: skip
    st.set_coverage("paper_strategies", {"strategies": list(out.values())})
    return out


TIMER = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=300.0, sell_on_dev_sell=False)
DEAD1 = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300.0, sell_on_dev_sell=False, stale_secs=1.0)
DEAD5 = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300.0, sell_on_dev_sell=False, stale_secs=5.0)


def test_the_horizon_is_the_simulators_own_reach_a_dead_exits_pause_included():
    from types import SimpleNamespace

    from snipetech.analysis.lab import exits_horizon, in_parts, mig_gap
    from snipetech.sim.exits import exit_from_label, exits_reach

    assert exits_horizon([TIMER]) == 300.0 and exits_horizon([DEAD1]) == 301.0  # was 300.0
    # the agents' exit of the 22 Nov review: a 10 s timer whose dead exit waits 158 s can sell 168 s after the buy
    quiet = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10.0, stale_secs=158.0, sell_on_dev_sell=False)
    assert exits_horizon([quiet]) == 168.0  # was 10.0
    ext = exit_from_label("hold12s EXT1%/s@2s+4s<20s")
    for exits in ([TIMER], [DEAD1, DEAD5], [quiet, ext], in_parts([DEAD5, ext], 4, 15.0)):
        assert exits_horizon(exits) == exits_reach(exits)  # one answer to "how long can it hold"
    # so a MIG buy the PumpSwap tape holds for the timer alone is no moment of an exit that waits out a quiet coin
    at = 1_794_000_000
    cov = {"runs": [{"from": at, "to": at + 20, "secs": 1800, "skipped": [], "slot_secs": 0.214}]}
    mig = SimpleNamespace(ts=at)
    waits = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=12.0, stale_secs=10.0, sell_on_dev_sell=False)
    assert mig_gap(cov, None, None, mig, exits_horizon([exit_from_label("hold12s")])) is None
    assert mig_gap(cov, None, None, mig, exits_horizon([waits])) == "end"  # 22 s and a slot: was None


def test_one_exit_of_a_launch_rule_is_checked_for_as_long_as_it_can_hold():
    """`lab.exit_gap`: a lab whose longest exit lacks the PumpSwap tape judges each exit on its own hold. An exit that
    sells in parts, or whose timer extends, or that waits out a quiet coin holds past its timer: it was checked for
    the timer alone and scored on tape that did not hold its later sales."""
    from types import SimpleNamespace

    import numpy as np

    from snipetech.analysis.lab import exit_gap, in_parts
    from snipetech.sim.exits import exit_from_label

    at = 1_794_000_000
    # the PumpSwap tape holds the coin for 30 s after our buy; the coin migrates 8 s after it, inside a 12 s timer
    cov = {"runs": [{"from": at, "to": at + 30, "secs": 1800, "skipped": [], "slot_secs": 0.214}]}
    path = SimpleNamespace(ts=np.array([float(at)]), slot=np.array([1000]), n=1)
    prep = SimpleNamespace(e=0, entry_ts=float(at), off_curve=None)
    mig = SimpleNamespace(ts=at + 8, slot=1040)
    res = SimpleNamespace(sold_on_curve=False, exit_ts=float(at + 12))
    plain = exit_from_label("hold12s")
    assert exit_gap(cov, path, prep, res, plain, mig) is None  # the tape holds the migration through its 12 s
    (parts,) = in_parts([plain], 4, 15.0)  # its last part is due 57 s after the buy
    assert exit_gap(cov, path, prep, res, parts, mig) == "end"  # was None
    ext = exit_from_label("hold12s EXT1%/s@2s+4s<20s")  # its timer can move on to 32 s
    assert exit_gap(cov, path, prep, res, ext, mig) == "end"  # was None
    waits = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=12.0, stale_secs=25.0, sell_on_dev_sell=False)
    assert exit_gap(cov, path, prep, res, waits, mig) == "end"  # its dead exit can sell at 37 s


def test_a_rule_with_a_dead_exit_settles_its_pause_later_and_says_so_once():
    """22 of the 135 stored paper rules have a dead exit on a 300 s timer (23 Nov): each settles 1 to 5 s later now. A
    line a rule in every paper run would be 22 lines every fifteen minutes that say nothing new: the run says a rule's
    settle time once (and again if it changes); the paper report keeps it on the rule's row."""
    assert paper.settle_secs(TIMER) == 420.0 and paper.settle_secs(DEAD1) == 421.0  # was 420.0
    assert paper.settle_secs(DEAD5) == 425.0
    st = Store(":memory:")
    st.set_label(KOL, "followed", "wallet", "file")
    rules = _stored(st, timer=TIMER, dead1=DEAD1, dead5=DEAD5)
    for k in range(3):
        _c2_coin(st, 200 + k, FREEZE + 300 + k * 120, 0.05)
    st.set_coverage("tape", {"spans": [[W0, RUN]]})
    reads = []
    whole = st.active_mints
    st.active_mints = lambda a, b, n=1: reads.append((a, b)) or whole(a, b, n)
    first = paper.run(st, _c2_cfg(), now=RUN)
    assert first["new_trades"] == 9
    assert first["settles_later"] == {rules["dead1"]["id"]: 421.0, rules["dead5"]["id"]: 425.0}
    # the three settle times are one read of the tape: a rule that settles a few seconds later asks no read of its own
    assert len(reads) == 1 and reads[0] == (FREEZE - 5, RUN - 420)
    _c2_coin(st, 210, RUN + 200, 0.05)
    st.set_coverage("tape", {"spans": [[W0, RUN + 1200]]})
    again = paper.run(st, _c2_cfg(), now=RUN + 1200)
    assert again["new_trades"] == 3 and "settles_later" not in again  # said once
    rows = {r["id"]: r for r in paper.report(st, _c2_cfg(), now=RUN + 1200)["strategies"]}
    assert rows[rules["dead5"]["id"]]["settles_after_secs"] == 425.0
    assert "settles_after_secs" not in rows[rules["timer"]["id"]]
    kept = st.coverage(paper.SETTLES_SAID_KEY)["rules"]
    assert kept == {rules["dead1"]["id"]: 421.0, rules["dead5"]["id"]: 425.0}
    # a rule whose settle time is not the one that was said (the code counts more of its exit now) is said again, alone
    st.set_coverage(paper.SETTLES_SAID_KEY, {"rules": {**kept, rules["dead5"]["id"]: 424.0}})
    st.set_coverage("tape", {"spans": [[W0, RUN + 2400]]})
    third = paper.run(st, _c2_cfg(), now=RUN + 2400)
    assert third["new_trades"] == 0 and third["settles_later"] == {rules["dead5"]["id"]: 425.0}
    assert st.coverage(paper.SETTLES_SAID_KEY)["rules"] == kept
    st.close()
