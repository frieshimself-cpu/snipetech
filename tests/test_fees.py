"""The fee policy (fees.py): each planned sniper's buy priority, buy tip, sell tip and sell priority from its kind and
the evidence on fills, inside the owner's [fees] bounds. No evidence: the minimums. A higher tip is chosen only where
10+ fills at it landed measurably earlier than 10+ at the lower one. A leader's evidence never reaches another leader.
The strategist may narrow the per-kind bounds, never past the owner's outer ones."""

from __future__ import annotations

import logging

import pytest

from snipetech import fees as F
from snipetech.config import FEE_KINDS, Config

LEADER = "7aPo3n" + "x" * 38
OTHER = "9gBmZ4" + "y" * 38
T0 = 1_793_923_200


def _buy(i: int, tip_sol: float, behind: int, gap: float, leader: str | None = LEADER, prio_sol: float = 0.001) -> dict:
    return {"sig": f"B{tip_sol}-{leader}-{i}", "ts": T0 + i * 60, "side": "buy", "wallet_copied": leader, "mint": f"M{i}",
            "priority_lamports": round(prio_sol * 1e9), "tip_lamports": round(tip_sol * 1e9), "trades_behind": behind,
            "price_gap_pct": gap}  # fmt: skip


def _band(tip_sol: float, behinds: list[int], leader: str | None = LEADER, gap: float = 20.0) -> list[dict]:
    return [_buy(i, tip_sol, b, gap, leader) for i, b in enumerate(behinds)]


def test_owner_bounds_default_to_todays_values_with_the_tip_cap_the_autotune_bound_has():
    cfg = Config()
    assert set(cfg.fees.bounds) == set(FEE_KINDS) == {"copy", "hunt", "launch", "mig", "probe"}
    for kind in FEE_KINDS:
        b = cfg.fees.bounds[kind]
        assert b["buy_priority"] == {"min": 0.001, "max": 0.001}  # charged again on every exit: never raised
        assert b["buy_tip"] == {"min": 0.002, "max": 0.005}
        assert b["sell_tip"] == {"min": 0.002, "max": 0.005}
        assert b["sell_priority"] == {"min": 0.0001, "max": 0.002}  # what each failed twin pays
    assert cfg.fees.tune == cfg.fees.bounds and cfg.fees.tune is not cfg.fees.bounds


def test_no_evidence_gives_the_minimums():
    c = F.choose(Config(), "copy", leader=LEADER)
    assert (c.buy_priority, c.buy_tip, c.sell_tip, c.sell_priority) == (0.001, 0.002, 0.002, 0.0001)
    assert c.kind == "copy" and c.leader == LEADER
    assert "no fills" in c.why["buy_tip"] and "minimum" in c.why["buy_tip"]
    for kind in ("hunt", "launch", "mig", "probe"):
        k = F.choose(Config(), kind)
        assert (k.buy_priority, k.buy_tip, k.sell_tip, k.sell_priority) == (0.001, 0.002, 0.002, 0.0001)


def test_a_higher_tip_is_chosen_only_on_ten_plus_fills_that_landed_measurably_earlier():
    low = _band(0.002, [3, 4, 3, 2, 4, 3, 3, 4, 2, 3, 4, 3])
    high = _band(0.004, [1, 1, 0, 1, 2, 1, 0, 1, 1, 2, 1, 1])
    c = F.choose(Config(), "copy", leader=LEADER, fills=low + high)
    assert c.buy_tip == 0.004 and c.buy_priority == 0.001
    assert "12 fills" in c.why["buy_tip"] and "earlier" in c.why["buy_tip"]
    # nine fills at the higher tip: not enough, whatever they say
    c9 = F.choose(Config(), "copy", leader=LEADER, fills=low + high[:9])
    assert c9.buy_tip == 0.002 and "9 fills" in c9.why["buy_tip"] and "10 needed" in c9.why["buy_tip"]
    # the 6 Nov fee test: paying more landed no earlier -> the minimum
    same = _band(0.02, [3, 4, 3, 2, 4, 3, 3, 4, 2, 3, 4, 3])
    c_same = F.choose(Config(), "copy", leader=LEADER, fills=low + same)
    assert c_same.buy_tip == 0.002 and "no earlier" in c_same.why["buy_tip"]


def test_the_chosen_tip_is_capped_at_the_owners_max():
    low = _band(0.002, [3, 4, 3, 2, 4, 3, 3, 4, 2, 3, 4, 3])
    high = _band(0.02, [1, 1, 0, 1, 2, 1, 0, 1, 1, 2, 1, 1])
    c = F.choose(Config(), "copy", leader=LEADER, fills=low + high)
    assert c.buy_tip == 0.005 and "capped" in c.why["buy_tip"]
    cfg = Config()
    cfg.fees.bounds["copy"]["buy_tip"]["max"] = 0.003
    assert F.choose(cfg, "copy", leader=LEADER, fills=low + high).buy_tip == 0.003


def test_a_leaders_evidence_never_reaches_another_leader():
    low = _band(0.002, [3, 4, 3, 2, 4, 3, 3, 4, 2, 3, 4, 3])
    high = _band(0.004, [1, 1, 0, 1, 2, 1, 0, 1, 1, 2, 1, 1])
    fills = low + high + _band(0.002, [2, 2, 3, 2, 2, 3, 2, 3, 2, 2, 3], leader=OTHER)
    assert F.choose(Config(), "copy", leader=LEADER, fills=fills).buy_tip == 0.004
    other = F.choose(Config(), "copy", leader=OTHER, fills=fills)
    assert other.buy_tip == 0.002 and "11 fills" in other.why["buy_tip"] and "none a higher tip" in other.why["buy_tip"]
    # the kind as a whole (no leader named) pools every copy fill
    assert F.choose(Config(), "copy", fills=fills).buy_tip == 0.004


def test_copy_fills_are_never_evidence_for_a_launch_kind():
    low = _band(0.002, [3, 4, 3, 2, 4, 3, 3, 4, 2, 3, 4, 3])
    high = _band(0.004, [1, 1, 0, 1, 2, 1, 0, 1, 1, 2, 1, 1])
    assert F.choose(Config(), "hunt", fills=low + high).buy_tip == 0.002


def test_priorities_stay_at_the_minimum_whatever_the_fills_say():
    """A buy priority is charged again on every exit and a sell priority on every failed twin: neither is a bid worth
    raising, so the evidence rule applies to the tips only."""
    low = [_buy(i, 0.002, 3, 20.0, prio_sol=0.001) for i in range(12)]
    high = [_buy(20 + i, 0.002, 1, 5.0, prio_sol=0.01) for i in range(12)]
    c = F.choose(Config(), "copy", leader=LEADER, fills=low + high)
    assert c.buy_priority == 0.001 and c.sell_priority == 0.0001


def test_the_floor_raises_a_number_and_says_when_it_is_above_the_owners_max():
    """The book's arm is the floor of a copy's buy bid: the policy may raise the tip above it on evidence, never lower
    it. A floor is an explicit ask (the owner's config, a verdict's bribe): kept, and said so when above the max; the
    strategist's own floor (the control arm's tip) is bounded by [autotune] to the same 0.005."""
    c = F.choose(Config(), "copy", leader=LEADER, floor={"buy_priority": 0.001, "buy_tip": 0.003})
    assert c.buy_tip == 0.003 and "floor" in c.why["buy_tip"]
    high = F.choose(Config(), "copy", leader=LEADER, floor={"buy_tip": 0.02})
    assert high.buy_tip == 0.02 and "above the owner's max" in high.why["buy_tip"]
    # the owner's [oktech] sell numbers are the base every sell starts from
    cfg = Config()
    cfg.oktech.sell_tip_sol, cfg.oktech.sell_priority_sol = 0.0025, 0.0002
    c = F.choose(cfg, "hunt")
    assert (c.sell_tip, c.sell_priority) == (0.0025, 0.0002)


def test_the_strategists_per_kind_bounds_narrow_the_owners_and_never_widen_them():
    cfg = Config()
    cfg.fees.tune["copy"]["buy_tip"] = {"min": 0.003, "max": 0.004}
    assert F.effective_bounds(cfg, "copy")["buy_tip"] == (0.003, 0.004)
    assert F.choose(cfg, "copy", leader=LEADER).buy_tip == 0.003  # the narrowed minimum
    cfg.fees.tune["copy"]["buy_tip"] = {"min": 0.0, "max": 0.05}  # a hand-edited overlay cannot widen
    assert F.effective_bounds(cfg, "copy")["buy_tip"] == (0.002, 0.005)


def test_the_strategist_cannot_move_a_per_kind_bound_past_the_owners_outer_bounds():
    from snipetech.config import never_tunable
    from snipetech.strategist.bounds import check_patch, strategist_may_set

    cfg = Config()
    accepted, refusals = check_patch({"fees.tune.copy.buy_tip.max": 0.004}, cfg, cfg.autotune)
    assert accepted == {"fees.tune.copy.buy_tip.max": 0.004} and refusals == []
    accepted, refusals = check_patch({"fees.tune.copy.buy_tip.max": 0.006}, cfg, cfg.autotune)
    assert accepted == {} and refusals and "0.005" in refusals[0]
    accepted, refusals = check_patch({"fees.tune.copy.sell_priority.min": 0.00005}, cfg, cfg.autotune)
    assert accepted == {} and refusals
    assert strategist_may_set("fees.tune.copy.sell_tip.max", 0.003, cfg)
    assert not strategist_may_set("fees.bounds.copy.sell_tip.max", 0.003, cfg)  # the owner's outer bounds
    assert never_tunable("fees.bounds.copy.buy_tip.max")
    assert not strategist_may_set("fees.tune.copy.buy_priority.max", 0.002, cfg)  # outer max is 0.001


def test_the_overlay_moves_a_per_kind_bound_inside_the_owners_and_ignores_one_outside(tmp_path):
    p = tmp_path / "snipetech.toml"
    Config().save(p)
    overlay = tmp_path / "autotune.toml"
    overlay.write_text("[fees.tune.copy.buy_tip]\nmax = 0.004\n\n[fees.tune.copy.sell_tip]\nmax = 0.02\n")
    cfg = Config.load(p, overlay=False)
    cfg.autotune.overlay = str(overlay)
    cfg.save(p)
    cfg = Config.load(p)
    assert cfg.fees.tune["copy"]["buy_tip"]["max"] == 0.004
    assert cfg.fees.tune["copy"]["sell_tip"]["max"] == 0.005  # outside the owner's bounds: not applied


def test_the_owners_file_merges_fee_bounds_per_number(tmp_path):
    p = tmp_path / "snipetech.toml"
    p.write_text("[fees.bounds.copy.buy_tip]\nmax = 0.004\n")
    cfg = Config.load(p)
    assert cfg.fees.bounds["copy"]["buy_tip"] == {"min": 0.002, "max": 0.004}
    assert cfg.fees.bounds["copy"]["sell_tip"] == {"min": 0.002, "max": 0.005}  # the siblings stay
    assert cfg.fees.tune["copy"]["buy_tip"] == {"min": 0.002, "max": 0.004}  # tune starts from the owner's bounds


def test_explain_says_each_kinds_choice_and_why_with_the_leaders_apart():
    low = _band(0.002, [3, 4, 3, 2, 4, 3, 3, 4, 2, 3, 4, 3])
    high = _band(0.004, [1, 1, 0, 1, 2, 1, 0, 1, 1, 2, 1, 1])
    out = F.explain(Config(), fills=low + high, leaders=[LEADER, OTHER])
    assert set(out["kinds"]) == set(FEE_KINDS)
    assert out["kinds"]["hunt"]["buy_tip"] == 0.002 and out["kinds"]["copy"]["buy_tip"] == 0.004
    by_leader = {r["leader"]: r for r in out["copy_leaders"]}
    assert by_leader[LEADER]["buy_tip"] == 0.004 and by_leader[OTHER]["buy_tip"] == 0.002
    assert "rule" in out and "10" in out["rule"] and "80" in out["rule"]
    assert out["bounds"]["copy"]["buy_tip"] == [0.002, 0.005]


def test_the_choice_words_name_the_fee_and_the_reason():
    c = F.choose(Config(), "copy", leader=LEADER)
    words = c.note()
    assert "0.001 priority + 0.002 tip" in words and "0.002 tip + 0.0001 priority" in words
    assert "copy of 7aPo3n" in words and "minimum" in words


def test_sell_fee_sol_is_a_deprecated_alias_that_sets_both_and_logs(tmp_path, caplog):
    cfg = Config()
    assert cfg.oktech.sell_tip_sol == 0.002 and cfg.oktech.sell_priority_sol == 0.0001
    assert cfg.oktech.sell_fee_sol == 0.002  # readable: the bid
    p = tmp_path / "snipetech.toml"
    p.write_text("[oktech]\nsell_fee_sol = 0.001\n")
    with caplog.at_level(logging.WARNING, logger="snipetech.config"):
        loaded = Config.load(p)
    assert loaded.oktech.sell_tip_sol == 0.001 and loaded.oktech.sell_priority_sol == 0.001
    assert any("sell_fee_sol" in r.message and "deprecated" in r.message for r in caplog.records)
    loaded.save(p)  # written back under the new names
    text = p.read_text()
    assert "sell_tip_sol = 0.001" in text and "sell_priority_sol = 0.001" in text and "sell_fee_sol" not in text
    assert Config.load(p).oktech.sell_priority_sol == 0.001
    cfg.set("oktech.sell_fee_sol", "0.003")
    assert cfg.oktech.sell_tip_sol == 0.003 and cfg.oktech.sell_priority_sol == 0.003


def test_fee_kinds_of_oktech_payloads():
    assert F.kind_of_payload({"mode": "ctbot"}) == "copy"
    assert F.kind_of_payload({"mode": "hunt"}) == "hunt"
    assert F.kind_of_payload({"mode": "mig"}) == "mig"
    assert F.kind_of_payload({"mode": "normal", "monitorProgram": True}) == "launch"
    assert F.kind_of_payload({"mode": "normal", "devWallets": ["x"]}) == "launch"
    assert F.kind_of_payload(None) is None


@pytest.mark.parametrize("kind", FEE_KINDS)
def test_every_kind_has_a_choice_inside_its_bounds(kind):
    c = F.choose(Config(), kind)
    for name, (lo, hi) in F.effective_bounds(Config(), kind).items():
        assert lo - 1e-12 <= getattr(c, name) <= hi + 1e-12
