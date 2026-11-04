"""The copytrader's decisions, sizing, risk limits and exits, driven by synthetic market trades."""

import asyncio
import time

import pytest

from snipetech.config import CopierConfig
from snipetech.copier import CopyEngine, LeaderTrade, PaperExecutor, TargetSettings
from snipetech.copier.engine import MarketState
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
LEADER = addr("copy-leader")


def market(store: Store, name: str, slot: int) -> Market:
    m = Market(store, addr(f"{name}-mint"), addr(f"{name}-dev"), NOW, slot)
    m.buy(m.deployer, 2.0, 0, sig=m.create_sig)  # the dev's buy at creation (so it can dump later)
    return m


def engine(store: Store, **cfg_over) -> CopyEngine:
    cfg = CopierConfig(**cfg_over)
    return CopyEngine(store, cfg, PaperExecutor(latency_ms=0), {LEADER: TargetSettings.from_config(cfg)})


def run(coro):
    return asyncio.run(coro)


def decisions(store: Store) -> list[tuple[str, str]]:
    return [(d["action"], d["reason"]) for d in reversed(store.copy_decisions())]


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_market_state_prices_like_the_curve():
    st = MarketState("curve", q=30_000_000_000, b=1_073_000_000_000_000, fee_bps=125, ts=0)
    tokens = st.tokens_for(1_000_000_000)
    # pump.fun: 1 SOL (incl. 1.25% fee) at launch buys ~34.1M tokens
    assert 34.0e12 < tokens < 34.2e12
    assert st.sol_for(tokens) < 1_000_000_000  # round trip loses the fees


def test_copies_the_buy_and_mirrors_partial_then_full_sells(store):
    async def go():
        e = engine(store, size_sol=0.2, stop_loss=0.0)
        m = market(store, "cm-1", 1000)
        await e.on_leader_trade(LeaderTrade(LEADER, m.buy(LEADER, 1.0, 1)))
        await e.drain()
        pos = e.positions[m.mint]
        assert pos.cost_lamports > 0.2e9 and pos.tokens > 0
        for k in range(10):  # the crowd pumps it
            await e.on_market_trade(m.buy(addr(f"cm-fan-{k}"), 0.5, 2 + k))
        await e.on_leader_trade(LeaderTrade(LEADER, m.sell(LEADER, 0.5, 20)))
        await e.drain()
        assert pos.sold_tokens == pytest.approx(pos.tokens * 0.5, rel=0.01) and pos.status == "open"
        await e.on_leader_trade(LeaderTrade(LEADER, m.sell(LEADER, 1.0, 21)))
        await e.drain()
        assert pos.status == "closed" and m.mint not in e.positions
        assert pos.realized_lamports > pos.cost_lamports  # bought before the pump
        return e

    run(go())
    acts = decisions(store)
    assert [a for a, _ in acts] == ["buy", "sell", "sell"]
    assert acts[1][1].startswith("mirror: copied wallet sold 50%") and store.copy_positions("closed")


def test_skips_are_logged_with_reasons(store):
    async def go():
        e = engine(store, max_open_positions=1, min_leader_sol=0.1)
        a = market(store, "cs-a", 2000)
        b = market(store, "cs-b", 3000)
        c = market(store, "cs-c", 4000)
        await e.on_leader_trade(LeaderTrade(LEADER, a.buy(LEADER, 0.05, 1)))  # dust
        await e.on_leader_trade(LeaderTrade(LEADER, a.buy(LEADER, 1.0, 2)))
        await e.drain()
        await e.on_leader_trade(LeaderTrade(LEADER, a.buy(LEADER, 1.0, 3)))  # already in
        await e.on_leader_trade(LeaderTrade(LEADER, b.buy(LEADER, 1.0, 1)))  # limit
        store.put_score("deployer", c.deployer, {"verdict": "AVOID"})
        e.cfg.max_open_positions = 5
        await e.on_leader_trade(LeaderTrade(LEADER, c.buy(LEADER, 1.0, 1)))  # bad deployer
        await e.on_leader_trade(LeaderTrade(addr("someone-else"), b.buy(addr("someone-else"), 1.0, 2)))  # not followed
        await e.drain()

    run(go())
    reasons = [r for a, r in decisions(store) if a == "skip"]
    assert reasons[0].startswith("copied buy 0.05") and reasons[0].endswith("below min 0.1")
    assert "already holding this token" in reasons and "max open positions (1)" in reasons
    assert "deployer verdict AVOID" in reasons and len(reasons) == 4


def test_stop_loss_and_cooldown(store):
    async def go():
        e = engine(store, size_sol=0.2, stop_loss=0.15, mirror_sells=False, cooldown_secs=600)
        m = market(store, "cl-1", 5000)
        await e.on_leader_trade(LeaderTrade(LEADER, m.buy(LEADER, 2.0, 1)))
        await e.drain()
        await e.on_leader_trade(LeaderTrade(LEADER, m.sell(LEADER, 1.0, 2)))  # not mirrored
        assert m.mint in e.positions
        await e.on_market_trade(m.sell(m.deployer, 1.0, 3))  # the dev dumps
        await e.drain()
        assert m.mint not in e.positions
        await e.on_leader_trade(LeaderTrade(LEADER, m.buy(LEADER, 1.0, 4)))
        await e.drain()

    run(go())
    acts = decisions(store)
    assert acts[1][0] == "sell" and acts[1][1].startswith("stop loss at")
    assert acts[2] == ("skip", "cooldown after exiting this token")


def test_paper_fill_respects_slippage_after_latency(store):
    async def go():
        cfg = CopierConfig(slippage_bps=200)
        e = CopyEngine(store, cfg, PaperExecutor(latency_ms=50), {LEADER: TargetSettings.from_config(cfg)})
        m = market(store, "cp-1", 6000)
        await e.on_leader_trade(LeaderTrade(LEADER, m.buy(LEADER, 1.0, 1)))
        await e.on_market_trade(m.buy(addr("cp-whale"), 5.0, 1))  # lands before our order: price +>2%
        await e.drain()
        return e

    e = run(go())
    assert not e.positions
    (act, reason) = decisions(store)[-1]
    assert act == "buy_failed" and reason.startswith("slippage")


def test_daily_loss_limit_and_capital_limit(store):
    async def go():
        e = engine(store, size_sol=0.5, max_total_sol=0.8, daily_loss_limit_sol=0.1, stop_loss=0.2, mirror_sells=False)
        a = market(store, "cd-a", 7000)
        b = market(store, "cd-b", 8000)
        await e.on_leader_trade(LeaderTrade(LEADER, a.buy(LEADER, 1.0, 1)))
        await e.drain()
        await e.on_leader_trade(LeaderTrade(LEADER, b.buy(LEADER, 1.0, 1)))  # 0.5 + 0.5 > 0.8
        await e.on_market_trade(a.sell(LEADER, 1.0, 2))
        await e.on_market_trade(a.sell(a.deployer, 1.0, 2))  # stop loss realizes the loss
        await e.drain()
        await e.on_leader_trade(LeaderTrade(LEADER, b.buy(LEADER, 1.0, 3)))
        await e.drain()

    run(go())
    reasons = [r for a, r in decisions(store) if a == "skip"]
    assert reasons[0].startswith("capital limit") and reasons[1].startswith("daily loss limit hit")


def test_positions_survive_a_restart(store):
    async def go():
        e = engine(store, size_sol=0.2)
        m = market(store, "cr-1", 9000)
        await e.on_leader_trade(LeaderTrade(LEADER, m.buy(LEADER, 1.0, 1)))
        await e.drain()
        return m

    m = run(go())
    again = engine(store)
    assert m.mint in again.positions and again.positions[m.mint].held > 0


def test_proportional_sizing_is_clamped():
    s = TargetSettings.from_config(CopierConfig(size_mode="proportional", proportional_ratio=0.1, min_size_sol=0.05, max_size_sol=0.3))
    assert s.size_for(1.0) == pytest.approx(0.1) and s.size_for(0.1) == 0.05 and s.size_for(10) == 0.3
