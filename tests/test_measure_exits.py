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
    # ride is an exit on the menu (exits_menu.py): its live sells count under its own name; an exit no menu has is custom
    assert preset_name(RIDE) == "ride" and preset_name(TP) == "custom"
    assert preset_name({"ctbotWallets": [{"address": LEAD}]}) is None and preset_name(None) is None
    spec = exit_spec(LAB)
    assert spec["stale_secs"] == 1 and spec["stop_loss_pct"] == 30 and spec["hold_secs"] == [300]
    assert (
        spec["copy_pct"] == 50 and spec["tp_pcts"] == [] and spec["trail_pct"] is None
    )  # the +10000 % stage is a dummy


def test_a_landed_sell_at_another_snipers_priority_is_crossed():
    """The landed variant of a sell pays its sniper's buy priority fee (7 of 7 fee-test sells); an untipped variant
    pays the sell priority (0 since 8 Nov 12:45: the plain second send bids nothing). Anything else came from another
    sniper on the same leader; a sniper created before then still pays 0.002 on that variant, which its own stored
    payload says."""
    assert (1_000_000, 2_000_000) in FEES and (0, 0) in FEES
    assert is_crossed({"priority_lamports": 20_000_000, "tip_lamports": 2_000_000}, LAB) is True
    assert is_crossed({"priority_lamports": 999_999, "tip_lamports": 2_000_000}, LAB) is False
    assert is_crossed({"priority_lamports": 0, "tip_lamports": 0}, LAB) is False
    assert is_crossed({"priority_lamports": 2_000_000, "tip_lamports": 0}, LAB) is True
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


# what closed each of the 12 copy fills OKTech sold on 6 Nov, as the exit audit read them off the tape
LIVE_6NOV = {"au6jPX": "copy_exit", "A7YsCf": "dead_exit", "H4D5mT": "dead_exit", "3BrEjv": "copy_exit", "7bDNtS": "timer",
             "9yCWR1": "copy_exit", "Cp2vvB": "dead_exit", "Cmg7tz": "dead_exit", "7Rwma8": "stop_loss",
             "FqK7tC": "dead_exit", "otdLSd": "copy_exit"}  # fmt: skip


def test_the_6_nov_copy_sells_read_as_the_audit_found_them():
    """Real fills and tape (tests/fixtures/exits_6nov.json.gz): the morning copy test (sells at 0.001, Gfree on
    hold60, the others on lab) and the fee test (lab, sells at 0.002, a 0.001 and a 0.02 sniper on the same leaders).
    All 12 sells get the exit the audit found, and exactly the two that paid the other sniper's priority (9yCW sold
    at 0.001 after the 0.02 sniper bought it, FqK7 at 17:05 the other way round) are crossed."""
    import gzip
    import json
    import time
    from pathlib import Path

    from snipetech.models import Trade

    with gzip.open(Path(__file__).parent / "fixtures" / "exits_6nov.json.gz", "rt") as fh:
        data = json.load(fh)
    gfree = "4yo9CUuTBbds9NFhZd4MzPiZZkUvveXdTnAH8qMsE8ku"
    morning = {w: _sniper("hold60" if w == gfree else "lab", sell_fee=0.001) for w in
               (gfree, "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9", "CgaA9a1JwAXJyfHuvZ7VW8YfTVRkdiT5mjBBSKcg7Rz5")}  # fmt: skip
    control, paid = _sniper(), _sniper(prio=0.02, tip=0.02)
    fees = fee_pairs([*morning.values(), control, paid])
    got, crossed = {}, set()
    for i, c in enumerate(data["cases"]):
        tape = [Trade(sig=r[10] or f"t{i}-{k}", ev=0, slot=r[1], pos=r[2], ts=r[0], mint=c["mint"], venue=r[9], user=r[3],
                      is_buy=bool(r[4]), quote=r[5], base=r[6], fee=0, fee_bps=0, q_after=r[7], b_after=r[8])
                for k, r in enumerate(c["tape"])]  # fmt: skip
        buy, sell = ({k: (int(v) if k in ("ts", "slot", "priority_lamports", "tip_lamports") else v) for k, v in f.items()}
                     for f in (c["buy"], c["sell"]))  # fmt: skip
        spec = morning.get(buy["wallet_copied"]) or (paid if buy["bid_arm"] == "paid" else control)
        name = f"{c['mint'][:6]} {time.strftime('%H:%M', time.gmtime(sell['ts']))}"
        got[name] = classify_exit(sell, buy, tape, buy["wallet_copied"], spec, fees)
        if is_crossed(sell, spec):
            crossed.add(name)
    assert len(got) == 12 and all(label == LIVE_6NOV[name[:6]] for name, label in got.items()), got
    assert crossed == {"9yCWR1 15:52", "FqK7tC 17:05"}


# -- the shadow: every exit on the menu replayed from each live buy (audit item 11) ---------------------------------


def test_the_shadow_replays_the_tape_as_if_we_had_never_traded():
    """Our own trades come out of the coin's tape, and the pool gets back the tokens we held at each later state (its
    product q x b kept), so a replay is never priced on a curve our own buy or sell moved; the stored trades stay as
    they are."""
    from snipetech.measure.exits import tape_without

    m = Tape(Store(":memory:"), addr("without"), D, NOW, SLOT)
    dev, lead = m.buy(D, 1.0, 0), m.buy(LEAD, 2.0, 0)
    ours = m.buy(ME, 0.2, 0)
    a = m.buy(addr("without-a"), 0.5, 1)
    sold = m.sell(ME, 1.0, 2)
    b = m.buy(addr("without-b"), 0.5, 3)
    out = tape_without(m.trades, ME)
    assert [t.sig for t in out] == [dev.sig, lead.sig, a.sig, b.sig] and ours.base == sold.base
    oa, ob = out[2], out[3]
    assert (out[1].q_after, out[1].b_after) == (lead.q_after, lead.b_after)  # before our buy: as recorded
    assert oa.b_after == a.b_after + ours.base  # while we held: our tokens are back in the pool
    assert abs(oa.q_after * oa.b_after - a.q_after * a.b_after) <= oa.b_after
    assert (ob.q_after, ob.b_after) == (b.q_after, b.b_after)  # after our sell we held nothing
    assert m.trades[3] is a and a.b_after == oa.b_after - ours.base  # the recorded trade is untouched


def test_shadow_exits_rescore_a_live_entry_under_every_menu_exit():
    """Six minutes after a live buy, measure replays every exit on the menu from that buy's real entry: the state just
    before our buy, our SOL, the coin's tape as if we had never traded it, the copied wallet's own sales, each exit as
    the live sniper runs it. Here 10 SOL lift the price 70 % in nine seconds of busy trading and are sold back as fast:
    ride sells on the way down, 25 % under the top; lab's dead exit and hold60's timer both sell after the dump."""
    from snipetech.exits_menu import EXIT_MENU_VERSION
    from snipetech.measure.exits import SHADOW_AFTER_SECS, shadow_exits

    assert SHADOW_AFTER_SECS == 360  # the longest hold on the menu (300 s) and a minute of late tape
    st = Store(":memory:")
    try:
        m = Tape(st, addr("shadow"), D, NOW, SLOT)
        m.buy(D, 1.0, 0)
        m.buy(LEAD, 2.0, 0)
        ours = m.buy(ME, 0.2, 0)
        for k in range(1, 11):  # a buy every 4 slots (0.86 s): never the 1 s pause the dead exit waits for
            m.buy(addr(f"shadow-up{k}"), 1.0, 4 * k)
        for k in range(1, 11):  # then they sell, as fast
            m.sell(addr(f"shadow-up{k}"), 1.0, 40 + 4 * k)
        m.sell(ME, 1.0, 90)  # our own live sell: not part of the replay
        m.buy(addr("shadow-late"), 0.05, 300)  # 64 s after the buy
        m.buy(addr("shadow-later"), 0.05, 1450)  # 310 s
        m.commit()
        st.set_coverage("tape", {"spans": [[NOW - 60, NOW + 4000]]})
        buy = {"sig": ours.sig, "ts": ours.ts, "slot": ours.slot, "mint": ours.mint, "side": "buy", "sol": 0.2025,
               "tokens": float(ours.base), "wallet": ME, "wallet_copied": LEAD, "priority_lamports": 1_000_000,
               "tip_lamports": 2_000_000}  # fmt: skip
        assert shadow_exits(st, buy, now=NOW + 300, slot_secs=0.214) == [] and st.exit_shadow([ours.sig]) == []
        rows = shadow_exits(st, buy, now=NOW + 400, slot_secs=0.214)
        by = {r["exit_name"]: r for r in rows}
        assert list(by) == ["lab", "hold60", "ride"] and {r["menu_version"] for r in rows} == {EXIT_MENU_VERSION}
        reasons = {n: r["sim_reason"] for n, r in by.items()}
        assert reasons == {"lab": "dead_exit", "hold60": "timer", "ride": "trailing"}
        assert by["ride"]["sim_pnl_sol"] > 0.03 and by["lab"]["sim_pnl_sol"] < 0 and by["hold60"]["sim_pnl_sol"] < 0
        stored = {r["exit_name"]: r for r in st.exit_shadow([ours.sig])}
        assert {n: (r["sim_pnl_sol"], r["sim_reason"]) for n, r in stored.items()} == {
            n: (r["sim_pnl_sol"], r["sim_reason"]) for n, r in by.items()
        }
        assert st.shadowed_buys(EXIT_MENU_VERSION) == {ours.sig} and st.shadowed_buys("another-menu") == set()
        shadow_exits(st, buy, now=NOW + 500, slot_secs=0.214)  # again: replaced, never doubled
        assert len(st.exit_shadow([ours.sig])) == 3
        # nothing for a buy that copied nobody, or one the tape did not record the whole time after
        assert shadow_exits(st, buy | {"wallet_copied": None}, now=NOW + 400, slot_secs=0.214) == []
        st.set_coverage("tape", {"spans": [[NOW - 60, NOW + 100]]})
        assert shadow_exits(st, buy, now=NOW + 400, slot_secs=0.214) == []
    finally:
        st.close()
