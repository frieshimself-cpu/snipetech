"""Which exit sold a position, read back from the tape, the sell's fee and the sniper's own settings (the local
`oktech_snipers` payload): copy exit, stop-loss, trailing stop, take-profit, dead exit, timer, or a sale by hand."""

from snipetech.config import Config
from snipetech.measure.exits import classify_exit, exit_spec, fee_pairs, is_crossed, preset_name
from snipetech.oktech import list_copy_sniper
from snipetech.store import Store
from tests.synth import Market, addr

NOW = 1_793_923_200
SLOT = 400_000_000
ME, LEAD, D = addr("me"), addr("lead"), addr("dev")


class Tape(Market):
    """A synthetic coin on the measured clock: 0.214 s per slot (the synthetic default is 0.4)."""

    def _ts(self, slot: int) -> int:
        return self.ts0 + int((slot - self.slot0) * 0.214)


def _sniper(exit_name="lab", exit_params=None, prio=0.001, tip=0.002, sell_fee=0.002) -> dict:
    """The OKTech config a copy sniper on LEAD was created with (what the local table keeps as its payload)."""
    cfg = Config()
    cfg.oktech.sell_fee_sol = sell_fee
    return list_copy_sniper(LEAD, "book", 0.3, 0.2, cfg=cfg, exit_name=exit_name, exit_params=exit_params,
                            priority_fee=prio, tip=tip, tag="arm:control").config  # fmt: skip


LAB = _sniper()
HOLD60 = _sniper("hold60")
RIDE = _sniper(exit_params={"trailing_stop": 0.25, "trailing_activation": 0.2, "stop_loss": 0.3, "max_hold_secs": 300})
TP = _sniper(exit_params={"take_profit": 0.5, "stop_loss": 0.3, "max_hold_secs": 300})
FEES = fee_pairs([LAB])


def _fill(t, prio=1_000_000, tip=2_000_000) -> dict:
    """Our fill as measure stores it: a landed lab sell pays the buy priority 0.001 and the sell tip 0.002."""
    return {"sig": t.sig, "ts": t.ts, "slot": t.slot, "mint": t.mint, "side": "buy" if t.is_buy else "sell",
            "priority_lamports": prio, "tip_lamports": tip}  # fmt: skip


def _open(name: str) -> tuple[Tape, object]:
    """A coin: the dev's buy, the leader's 2 SOL buy, ours (0.2 SOL) right behind it in the same slot."""
    m = Tape(Store(":memory:"), addr(name), D, NOW, SLOT)
    m.buy(D, 1.0, 0)
    m.buy(LEAD, 2.0, 0)
    return m, m.buy(ME, 0.2, 0)


def _label(m, buy, ours, spec=LAB, prio=1_000_000, tip=2_000_000, fees=FEES) -> str:
    return classify_exit(_fill(ours, prio, tip), _fill(buy), m.trades, LEAD, spec, fees)


def _dead(name: str, sell_at: int) -> tuple[Tape, object, object]:
    m, b = _open(name)
    m.buy(addr(f"{name}-f1"), 0.05, 1)
    m.buy(addr(f"{name}-f2"), 0.05, 2)
    return m, b, m.sell(ME, 1.0, sell_at)


def test_classify_copy_dead_stop_timer_and_manual():
    # copy exit: the leader sells its whole bag; our sell lands the slot after (busy trading, no 1 s pause)
    m, b = _open("copy")
    for k in range(1, 10):
        m.buy(addr(f"copy-f{k}"), 0.05, k)
    m.sell(LEAD, 1.0, 10)
    assert _label(m, b, m.sell(ME, 1.0, 11)) == "copy_exit"
    # a leader sell below the 50 % threshold is not the copy exit
    m, b = _open("copy-small")
    for k in range(1, 10):
        m.buy(addr(f"copy-small-f{k}"), 0.05, k)
    m.sell(LEAD, 0.3, 10)
    assert _label(m, b, m.sell(ME, 1.0, 11)) == "unknown"
    # stop-loss: early buyers dump one slot apart and take the price below entry -30 %
    m = Tape(Store(":memory:"), addr("stop"), D, NOW, SLOT)
    m.buy(D, 1.0, 0)
    for k in range(4):
        m.buy(addr(f"stop-x{k}"), 2.0, 0)
    m.buy(LEAD, 2.0, 1)
    b = m.buy(ME, 0.2, 1)
    for k in range(4):
        m.sell(addr(f"stop-x{k}"), 1.0, 2 + k)
    assert _label(m, b, m.sell(ME, 1.0, 7)) == "stop_loss"
    # dead exit: 6 quiet slots (1.28 s at 0.214 s a slot) before our sell; 4 quiet slots (0.86 s) are not enough
    m, b, ours = _dead("dead", 8)
    assert _label(m, b, ours) == "dead_exit"
    m, b, ours = _dead("dead-short", 6)
    assert _label(m, b, ours) == "unknown"
    # timer: a trade every 4 slots (never 1 s quiet) and our sell 300 s after the buy
    m, b = _open("timer")
    for k in range(1, 351):
        m.buy(addr(f"timer-f{k}"), 0.01, 4 * k)
    ours = m.sell(ME, 1.0, 1402)
    assert ours.ts - b.ts == 300 and _label(m, b, ours) == "timer"
    # hold60: no dead exit and no stop-loss, the 60 s timer sold at 61 s (7bDN sold at 61.2 s)
    m, b = _open("hold60")
    m.buy(addr("hold60-f1"), 0.05, 3)
    assert _label(m, b, m.sell(ME, 1.0, 286), spec=HOLD60) == "timer"
    # manual: a sale paying 12,951 + 2,166,730 lamports (4siK's hand sale) on a 0.001 + 0.002 sniper
    m, b, ours = _dead("manual", 8)
    assert _label(m, b, ours, prio=12_951, tip=2_166_730) == "manual"


def test_trailing_and_take_profit_need_their_own_preset():
    # the price runs up ~70 % and gives a quarter back: the trailing stop (25 % from the peak after +20 %)
    m, b = _open("trail")
    for k in range(10):
        m.buy(addr(f"trail-p{k}"), 1.0, 1 + k)
    for k in range(6):
        m.sell(addr(f"trail-p{k}"), 1.0, 11 + k)
    ours = m.sell(ME, 1.0, 17)
    assert _label(m, b, ours, spec=RIDE) == "trailing"
    assert _label(m, b, ours) == "unknown"  # the lab preset has no trailing stop
    # take-profit at +50 %: our sell at the top of the run
    m, b = _open("tp")
    for k in range(10):
        m.buy(addr(f"tp-p{k}"), 1.0, 1 + k)
    assert _label(m, b, m.sell(ME, 1.0, 11), spec=TP) == "take_profit"


def test_an_unknown_sniper_keeps_sold_unless_the_fee_says_by_hand():
    m, b, ours = _dead("nospec", 8)
    assert _label(m, b, ours, spec=None) == "sold"
    assert _label(m, b, ours, spec={"ctbotWallets": [{"address": LEAD}]}) == "sold"  # a sniper with no exit settings
    assert _label(m, b, ours, spec=None, prio=12_951, tip=2_166_730) == "manual"
    # no fee known (fingerprint missing, or no sniper fees on record): never called a hand sale
    assert _label(m, b, ours, prio=None, tip=None) == "dead_exit"
    assert _label(m, b, ours, prio=12_951, tip=2_166_730, fees=set()) == "dead_exit"


def test_the_preset_is_read_back_from_the_snipers_payload():
    assert preset_name(LAB) == "lab" and preset_name(HOLD60) == "hold60"
    assert preset_name(_sniper(sell_fee=0.001, prio=0.02, tip=0.02)) == "lab"  # fees are not part of the preset
    assert preset_name(RIDE) == "custom" and preset_name(TP) == "custom"
    assert preset_name({"ctbotWallets": [{"address": LEAD}]}) is None and preset_name(None) is None
    spec = exit_spec(LAB)
    assert spec["stale_secs"] == 1 and spec["stop_loss_pct"] == 30 and spec["hold_secs"] == [300]
    assert (
        spec["copy_pct"] == 50 and spec["tp_pcts"] == [] and spec["trail_pct"] is None
    )  # the +10000 % stage is a dummy


def test_a_landed_sell_at_another_snipers_priority_is_crossed():
    """The landed variant of a sell pays its sniper's buy priority fee (7 of 7 fee-test sells); an untipped variant
    pays the sell priority. Anything else came from another sniper on the same leader."""
    assert (1_000_000, 2_000_000) in FEES and (2_000_000, 0) in FEES
    assert is_crossed({"priority_lamports": 20_000_000, "tip_lamports": 2_000_000}, LAB) is True
    assert is_crossed({"priority_lamports": 999_999, "tip_lamports": 2_000_000}, LAB) is False
    assert is_crossed({"priority_lamports": 2_000_000, "tip_lamports": 0}, LAB) is False
    assert is_crossed({"priority_lamports": 1_000_000, "tip_lamports": 2_000_000}, _sniper(prio=0.02, tip=0.02)) is True
    assert is_crossed({"priority_lamports": None, "tip_lamports": None}, LAB) is None
    assert is_crossed({"priority_lamports": 20_000_000, "tip_lamports": 0}, None) is None


def test_exit_reasons_are_counted_per_preset_for_the_weeks_evidence():
    from snipetech.measure.exits import crossed_sells, exit_reason_counts

    st = Store(":memory:")
    try:
        rows = [("S1", "lab", "dead_exit", 0), ("S2", "lab", "dead_exit", 0), ("S3", "lab", "copy_exit", 1),
                ("S4", "hold60", "timer", 0), ("S5", None, "sold", None), ("S6", "lab", None, None)]  # fmt: skip
        for i, (sig, name, reason, crossed) in enumerate(rows):
            st.put_fill({"sig": sig, "ts": NOW + i, "wallet": ME, "mint": "M", "side": "sell", "sol": 0.2, "tokens": 1.0,
                         "exit_name": name, "exit_reason": reason, "crossed": crossed})  # fmt: skip
        st.put_fill(
            {"sig": "OLD", "ts": NOW - 86400, "wallet": ME, "side": "sell", "exit_name": "lab", "exit_reason": "manual"}
        )
        st.put_fill(
            {"sig": "F1", "ts": NOW, "wallet": ME, "side": "failed", "exit_reason": "{'InstructionError': 6023}"}
        )
        assert exit_reason_counts(st, NOW - 60) == {"lab": {"dead_exit": 2, "copy_exit": 1}, "hold60": {"timer": 1},
                                                    "none": {"sold": 1}}  # fmt: skip
        assert exit_reason_counts(st, NOW - 2 * 86400)["lab"]["manual"] == 1
        assert crossed_sells(st, NOW - 60) == {"crossed": 1, "checked": 4}
    finally:
        st.close()
