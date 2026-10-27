"""The sniper profiler must recover a known configuration from on-chain behaviour."""

import time

import pytest

from snipetech.analysis.sniper import sniper_profile, top_snipers
from snipetech.config import Config
from snipetech.store import Store

from .synth import Market, addr, fingerprint

NOW = int(time.time())
BOT = addr("the-bot")


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def cfg():
    c = Config()
    c.verdict.bootstrap_samples = 300
    return c


def price(m: Market) -> float:
    return m.q / m.b


def bot_world(store: Store, n: int = 12) -> None:
    """BOT: 0.5 SOL, lands 1 slot after create, 0.004 SOL bribe; takes profit at 1.5x,
    or sells one slot after the dev starts dumping."""
    for i in range(n):
        dev = addr(f"bw-dev-{i}")
        m = Market(store, addr(f"bw-mint-{i}"), dev, NOW - 60_000 + i * 1200, 9_000_000 + i * 10_000)
        fingerprint(store, m.buy(dev, 0.8, 0, sig=m.create_sig), 0.001)
        t = m.buy(BOT, 0.5, 1)
        fingerprint(store, t, 0.004)
        entry = (t.quote / t.base)
        if i % 3 == 2:  # rug: dev dumps, bot reacts next slot
            m.sell(dev, 1.0, 6)
            fingerprint(store, m.sell(BOT, 1.0, 7), 0.004)
        else:  # pump until +50%, bot takes profit
            k = 0
            while price(m) < entry * 1.5 * (1 + 0.0125):
                m.buy(addr(f"bw-fan-{i}-{k}"), 0.2, 2 + k // 2)
                k += 1
            fingerprint(store, m.sell(BOT, 1.0, 2 + k // 2), 0.004)
        m.commit()


def test_profile_recovers_the_config(store, cfg):
    bot_world(store)
    rep = sniper_profile(store, cfg, BOT, NOW - 100_000)
    ex, inf = rep["exact"], rep["inferred"]
    assert ex["buy_size"]["mode_sol"] == pytest.approx(0.506, abs=0.002) and ex["buy_size"]["mode_share"] == 1.0
    assert ex["buy_fees"]["tip_sol_mode"] == pytest.approx(0.002) and ex["buy_fees"]["tip_services"] == {"jito": 12}
    assert ex["buy_fees"]["priority_sol_median"] == pytest.approx(0.002)
    assert ex["buy_slippage"]["headroom_mode"] == pytest.approx(0.25, abs=0.01)
    assert inf["entry"]["median_delay_slots"] == 1 and inf["entry"]["creation_slot_share"] == 0
    assert inf["exits"]["take_profit_multiple"] == pytest.approx(1.5, rel=0.05)
    assert inf["exits"]["dev_sold_while_holding"] == 4 and inf["exits"]["reacts_to_dev_sell_share"] == 1.0
    assert inf["exits"]["sells_on_dev_sell"] is True
    assert rep["performance"]["n"] == 12
    cfgblock = rep["config"]
    assert "take profit" in cfgblock and "[inferred]" in cfgblock["take profit"]
    assert "[exact]" in cfgblock["buy tip"]


def test_top_snipers_ranks_the_bot(store, cfg):
    bot_world(store)
    rows = top_snipers(store, cfg, NOW - 100_000, min_snipes=5)
    assert rows[0]["wallet"] == BOT and rows[0]["snipes"] == 12
    assert rows[0]["median_bribe_sol"] == pytest.approx(0.004)


def test_dev_reactions_are_not_mistaken_for_stop_losses(store, cfg):
    bot_world(store)
    x = sniper_profile(store, cfg, BOT, NOW - 100_000)["inferred"]["exits"]
    assert x["stop_loss_multiple"] is None  # its losses were all reactions to the dev dumping
    assert x["time_exit_secs"] is None  # its quick exits were take-profits, not a timer
    assert x["unexplained_exits"] == 0


def test_instruction_args_are_read_per_hop_and_router():
    from snipetech.analysis.sniper import _ix_for, _slippage
    from snipetech.models import Trade
    from snipetech.solana.programs import PUMP_PROGRAM
    from snipetech.solana.txmeta import PumpIx, TxMeta

    def trade(mint, quote, fee, base):
        return Trade("s", 0, 1, 0, NOW, mint, "curve", BOT, True, quote, base, fee, 125, 0, 0)

    router = addr("router")
    hops = [PumpIx(PUMP_PROGRAM, "buy", BOT, {"amount": 5, "max_sol_cost": 100_000_001}, router, addr("hop-a")),
            PumpIx(PUMP_PROGRAM, "buy", BOT, {"amount": 9, "max_sol_cost": 7}, router, addr("hop-b"))]  # fmt: skip
    meta = TxMeta("s", 1, NOW, 0, BOT, [BOT], 1, 5000, 0, None, None, None, [], [], hops)
    t = trade(addr("hop-a"), 98_765_431, 1_234_569, 5)
    ix = _ix_for(meta, t)
    assert ix is hops[0]  # not the first buy by this user, the one for this token
    # a router computed the amount on-chain: the cap is the budget, not a 0% slippage setting
    assert _slippage(ix, t) == {"buy_cap": "router", "exact_budget_sol": 0.1}
    direct = PumpIx(PUMP_PROGRAM, "buy", BOT, {"amount": 5, "max_sol_cost": 100_000_000}, None)
    assert _slippage(direct, t)["buy_cap_vs_fill"] == pytest.approx(0.0)  # a real 0% cap
    unprotected = PumpIx(PUMP_PROGRAM, "buy_exact_sol_in", BOT, {"spendable_sol_in": 100_000_000, "min_tokens_out": 1}, None)
    assert _slippage(unprotected, t)["buy_cap"] == "unlimited"
