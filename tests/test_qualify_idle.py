"""Wallets are looked at while they still trade (20 Nov 2026 audit, the owner's standing rule that the system adapt
within hours). 107,634 of 107,813 candidate verdicts got the same 72 h re-look; 11 of the 29 wallets on the forward
check were picked after a day or more without a trade, and 14 of them no longer trade: their forward check could never
fill, and a pending pick held its actor's place for good. Three rules: a wallet short of copies is looked at again when
its own pace on the tape would have brought them; a wallet that passes every gate is picked only while it still copies;
a forward check with no copy for two days of tape ends and gives up its place."""

import pytest

from snipetech.config import Config
from snipetech.qualify.gates import (
    IDLE_END_HOURS,
    IDLE_PICK_HOURS,
    IDLE_REVIEW_H,
    forward_gate,
    sample_review_hours,
)
from snipetech.qualify.one_actor import standing_wallets
from snipetech.qualify.run import pick_times, run_qualify
from snipetech.store import Store
from tests.synth import Market, addr
from tests.test_qualify_run import NOW, SLOT, STRONG, _row, _svc, _tape

H = 3600
DAY = 86400


def test_the_rules_numbers():
    assert (IDLE_PICK_HOURS, IDLE_END_HOURS, IDLE_REVIEW_H) == (24, 48, 6)
    q = Config().qualify
    spans = [[NOW - 7 * DAY, NOW]]
    # 4 coins over its 8 hours of tape: half a coin an hour, 6 more to 10 take 12 hours
    assert sample_review_hours(4, 10, NOW - 8 * H, spans, q, NOW) == pytest.approx(12.0)
    assert sample_review_hours(9, 10, NOW - 1 * H, spans, q, NOW) == 2.0  # never sooner than the lab can measure it
    assert sample_review_hours(2, 20, NOW - 5 * DAY, spans, q, NOW) == 72.0  # never later than before
    assert sample_review_hours(0, 10, None, spans, q, NOW) == 72.0  # nothing on the tape: as before
    # a hole in the tape is no time the wallet could trade in: 4 coins over 8 hours of tape in 18 clock hours
    holed = [[NOW - 18 * H, NOW - 14 * H], [NOW - 4 * H, NOW]]
    assert sample_review_hours(4, 10, NOW - 18 * H, holed, q, NOW) == pytest.approx(12.0)


def test_a_wallet_short_of_coins_is_looked_at_again_when_its_pace_would_have_brought_them(tmp_path):
    """The 2UgKTq group: its first look said "1 coin, not measured" and came back 72 h later; it traded about 1,800
    times in between and had stopped 50 h before it was measured. FAST bought 3 coins in its first two hours on the
    tape: at that pace the 17 more it needs take about 11 hours (20 without a strong history, since the audit of 22
    Nov, qualify#3; the 7 more to 10 took 4.5 until then), and that is when it is looked at again. SLOW bought 2 in
    five days: 72 h, as before."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["FAST"], coins=3)
        D = addr("dev")
        for k in range(2):
            m = Market(st, addr(f"SLOW:{k}"), D, NOW - 5 * DAY + k * 4 * DAY, SLOT - 2_000_000 + k * 864_000)
            m.buy(D, 1.0, 0)
            m.buy("SLOW", 0.5, 5)
            m.commit()
        st.set_coverage("tape", {"spans": [[NOW - 6 * DAY, NOW]]})
        st.upsert_candidate("FAST", "coinfirst", {}, NOW - 3600)
        st.upsert_candidate("SLOW", "coinfirst", {}, NOW - 3500)
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda ws, pos: [])
        assert out["too_few"] == 2
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        fast, slow = by["FAST"], by["SLOW"]
        assert NOW + 11 * H < fast["next_review"] < NOW + 11.1 * H, (fast["next_review"] - NOW) / H
        assert fast["status"] == "candidate" and "at its pace" in fast["reasons"][-1]
        assert slow["next_review"] == NOW + 72 * H
    finally:
        st.close()


def test_a_wallet_the_lab_found_short_of_copies_is_measured_again_at_its_pace(tmp_path):
    """12 copies over its last 6 hours of tape, 20 needed: 2 an hour, so 4 hours, not 72."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["HALF"])
        st.set_coverage("tape", {"spans": [[NOW - 2 * DAY, NOW]]})
        st.upsert_candidate("HALF", "coinfirst", {}, NOW - 3600)
        copies = [[NOW - 6 * H + 1800 * k, 0.01] for k in range(12)]
        run_qualify(
            svc, svc.cfg, data / "jobs", NOW, lab_runner=lambda ws, pos: [_row("HALF", 12, 0.01, -0.01, copies)]
        )
        v = st.latest_verdicts()[0]
        assert v["status"] == "candidate" and any(r.startswith("n 12 < 20") for r in v["reasons"]), v["reasons"]
        assert v["next_review"] == pytest.approx(NOW + 4 * H, abs=60), (v["next_review"] - NOW) / H
    finally:
        st.close()


def test_a_wallet_every_gate_passes_is_picked_only_while_it_still_copies(tmp_path):
    """IDLE made its 25 good copies and stopped 56 hours ago; BUSY made the same and copied 4 hours ago. BUSY is
    picked; IDLE is not (a pick it can never fill held a place for days): a candidate looked at again in 6 hours,
    with the reason, and it is picked once it copies again."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["IDLE", "BUSY"])
        st.set_coverage("tape", {"spans": [[NOW - 3 * DAY, NOW]]})
        for w in ("IDLE", "BUSY"):
            st.upsert_candidate(w, "solanatracker", STRONG, NOW - 3 * DAY)
        won = [0.03 if k % 4 else -0.01 for k in range(25)]
        copies = {"IDLE": [[NOW - 60 * H + 600 * k, p] for k, p in enumerate(won)],
                  "BUSY": [[NOW - 20 * H + 2400 * k, p] for k, p in enumerate(won)]}  # fmt: skip

        def runner(wallets, pos):
            return [_row(w, 25, 0.02, 0.012, copies[w], 0.75) for w in wallets]

        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        assert out["idle"] == 1  # said in the run's own answer
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        assert by["BUSY"]["status"] == "candidate" and by["BUSY"]["forward"]["status"] == "pending"
        idle = by["IDLE"]
        assert idle["status"] == "candidate" and idle["forward"] is None
        assert idle["next_review"] == NOW + IDLE_REVIEW_H * H
        assert "not picked" in idle["reasons"][-1] and "56 h of tape" in idle["reasons"][-1], idle["reasons"]
        assert pick_times(st, "IDLE") == (None, None) and pick_times(st, "BUSY") == (NOW, NOW)
    finally:
        st.close()


def test_a_forward_check_with_no_copy_for_two_days_ends_and_gives_up_its_place(tmp_path):
    """STALE and LIVE were picked 70 hours ago and each made 5 copies after. STALE's last came 65 hours ago: its check
    ends (a candidate with no forward check, looked at again in 6 hours, no longer holding its actor's place), and a
    pick starts again once it passes while copying. LIVE copied 10 hours ago: it waits on."""
    svc, st, data = _svc(tmp_path)
    try:
        _tape(st, ["STALE", "LIVE"])
        st.set_coverage("tape", {"spans": [[NOW - 4 * DAY, NOW]]})
        picked = NOW - 70 * H
        for w in ("STALE", "LIVE"):
            st.upsert_candidate(w, "solanatracker", STRONG, picked - DAY)
            st.put_verdict({"wallet": w, "computed_at": picked, "landing_pos": "+3x", "n_tape": 25, "mean_sol": 0.02,
                            "lower_sol": 0.01, "score": 0.01, "status": "candidate", "gate": "forward",
                            "next_review": NOW - 60, "forward": {"status": "pending", "picked_at": picked, "n": 0}})  # fmt: skip
        before = [[picked - 10 * H + 1200 * k, 0.03 if k % 4 else -0.01] for k in range(25)]
        after = {"STALE": [[picked + 3600 * k, 0.01] for k in range(1, 6)],
                 "LIVE": [[picked + 3600 * k, 0.01] for k in range(1, 5)] + [[NOW - 10 * H, 0.01]]}  # fmt: skip

        def runner(wallets, pos):
            return [_row(w, 30, 0.02, 0.01, before + after[w], 0.75) for w in wallets]

        run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        by = {v["wallet"]: v for v in st.latest_verdicts()}
        live = by["LIVE"]
        assert live["status"] == "candidate" and live["forward"]["status"] == "pending" and live["forward"]["n"] == 5
        stale = by["STALE"]
        assert stale["status"] == "candidate" and stale["forward"] is None
        assert stale["next_review"] == NOW + IDLE_REVIEW_H * H
        said = stale["reasons"][-1]
        assert said.startswith("forward check ended") and "5 later copies" in said and "65 h of tape" in said, said
        assert pick_times(st, "STALE") == (picked, None) and pick_times(st, "LIVE") == (picked, picked)
        assert standing_wallets(st) == {"LIVE"}
    finally:
        st.close()


def test_idle_is_counted_in_hours_of_tape_and_a_loss_still_retires_first():
    """48 hours of tape without a copy end the check, 47 do not; a hole in the tape is no time the wallet could trade
    in. A wallet that lost since it was first picked is retired for that, idle or not."""
    q = Config().qualify
    v = {"status": "qualified", "reasons": [], "landing_pos": "+3x"}
    picked = NOW - 60 * H

    def gate(last_copy_ago_h, spans, first=None, extra=()):
        copies = [[picked - 5 * H + 600 * k, 0.02] for k in range(25)] + list(extra)
        copies += [[NOW - last_copy_ago_h * H, 0.01]]
        return forward_gate(v, copies, picked, spans, q, NOW, samples=200, first_picked_at=first)

    full = [[NOW - 5 * DAY, NOW]]
    assert gate(48, full)["gate"] == "idle" and gate(47, full)["forward"]["status"] == "pending"
    holed = [[NOW - 5 * DAY, NOW - 30 * H], [NOW - 20 * H, NOW]]  # ten hours of it missing
    assert gate(50, holed)["forward"]["status"] == "pending"
    first = picked - 4 * DAY
    lost = [[first + 600 * k, -0.05] for k in range(1, 30)]
    out = gate(55, full, first=first, extra=lost)
    assert out["status"] == "retired" and out["forward"]["status"] == "failed"


def test_a_store_says_when_a_wallet_first_traded_on_its_tape(tmp_path):
    st = Store(tmp_path / "t.db")
    try:
        assert st.first_trade_ts("NOBODY") is None
        _tape(st, ["W"], coins=3)
        assert st.first_trade_ts("W") == min(r[0] for r in st._q("SELECT ts FROM trades WHERE user='W'"))
    finally:
        st.close()
