"""Round 3 of the 22-23 Nov audit, group "ceiling": the price limit OKTech puts on our own buys.

C1: the measure stage keeps each buy's and failed buy's limit on its fills row (`limit_mcap_sol`) and where the buy
stood against it (`fill_mcap_sol`), and says once a sniper a day when a limit is far from the ceiling the sniper was
sent. The transactions are the sniper wallet's own of the 23 Nov smoke test (tests/fixtures/buy_limits_23nov.json),
served by the test chain as the RPC serves them; nothing here reaches a network.
C2: a wallet rule's `max_fill_mcap` holds the lab and the paper test to that same limit: our own fill at the scored
landing, and a moment over it is a refused send.
C3: a run whose scan of the wallet's signatures failed leaves the measure stage's cursor where it was and says so."""

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


def _sniper(st, sid="smoke-1", leader=LEAD, cap=4600, created=T0 - 23, buy=0.02):
    """A copy sniper as `copylist --max-mcap` makes it: the ceiling in dollars on the copied wallet."""
    wallet = {"address": leader, "minSol": 2.0} | ({"maxMcapUsd": cap} if cap else {})
    st.add_oktech_sniper({"id": sid, "external_id": sid, "kind": "copy", "source": leader,
                          "name": f"snipetech copy {sid} arm:smoke66", "config_hash": "h", "created_at": created,
                          "payload": {"ctbotWallets": [wallet], "buyAmount": buy, "priorityFee": 0.001,
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
        shown = {r[0][:8]: r[1:] for r in st._q(
            "SELECT sig, side, limit_mcap_sol, fill_mcap_sol FROM fills WHERE wallet=? ORDER BY ts", (ME,))}  # fmt: skip
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
