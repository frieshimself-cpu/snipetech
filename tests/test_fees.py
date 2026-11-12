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
        # the sell tip at what each kind paid before the split: copies 0.002, every other builder 0.001
        assert b["sell_tip"] == {"min": 0.002 if kind == "copy" else 0.001, "max": 0.005}
        assert b["sell_priority"] == {"min": 0.0001, "max": 0.002}  # what each failed twin pays
    assert cfg.fees.tune == cfg.fees.bounds and cfg.fees.tune is not cfg.fees.bounds


def test_no_evidence_gives_the_minimums():
    c = F.choose(Config(), "copy", leader=LEADER)
    assert (c.buy_priority, c.buy_tip, c.sell_tip, c.sell_priority) == (0.001, 0.002, 0.002, 0.0001)
    assert c.kind == "copy" and c.leader == LEADER
    assert "no fills" in c.why["buy_tip"] and "minimum" in c.why["buy_tip"]
    for kind in ("hunt", "launch", "mig", "probe"):
        k = F.choose(Config(), kind)
        assert (k.buy_priority, k.buy_tip, k.sell_tip, k.sell_priority) == (0.001, 0.002, 0.001, 0.0001)


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
    # the owner's [oktech] sell priority is the base every sell starts from; the [oktech] sell tip is the copy kind's
    # base only (it priced the live copies' sells), so it never raises a hunt / launch / MIG / probe sell tip
    cfg = Config()
    cfg.oktech.sell_tip_sol, cfg.oktech.sell_priority_sol = 0.0025, 0.0002
    c = F.choose(cfg, "copy", leader=LEADER)
    assert (c.sell_tip, c.sell_priority) == (0.0025, 0.0002)
    for kind in ("hunt", "launch", "mig", "probe"):
        k = F.choose(cfg, kind)
        assert (k.sell_tip, k.sell_priority) == (0.001, 0.0002), kind


def test_the_strategists_per_kind_bounds_only_cap_the_owners_and_never_raise_a_minimum():
    """A raised minimum would be a raised bid with no fills behind it (the 6 Nov fee test is what the rule forbids), so
    the strategist's tune moves a kind's max only; its min is ignored and the policy's minimum stays the owner's."""
    cfg = Config()
    cfg.fees.tune["copy"]["buy_tip"] = {"min": 0.003, "max": 0.004}
    assert F.effective_bounds(cfg, "copy")["buy_tip"] == (0.002, 0.004)
    assert F.choose(cfg, "copy", leader=LEADER).buy_tip == 0.002  # the owner's minimum, whatever the tune's min says
    cfg.fees.tune["copy"]["buy_tip"] = {"min": 0.0, "max": 0.05}  # a hand-edited overlay cannot widen
    assert F.effective_bounds(cfg, "copy")["buy_tip"] == (0.002, 0.005)
    # a capped max still caps a tip the evidence would raise
    low = _band(0.002, [3, 4, 3, 2, 4, 3, 3, 4, 2, 3, 4, 3])
    high = _band(0.004, [1, 1, 0, 1, 2, 1, 0, 1, 1, 2, 1, 1])
    cfg.fees.tune["copy"]["buy_tip"] = {"min": 0.002, "max": 0.003}
    assert F.choose(cfg, "copy", leader=LEADER, fills=low + high).buy_tip == 0.003


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
    # a minimum is never the strategist's, inside the owner's bounds or not: it would raise a bid with no evidence
    accepted, refusals = check_patch({"fees.tune.launch.buy_tip.min": 0.005}, cfg, cfg.autotune)
    assert accepted == {} and refusals and "not in [autotune] bounds" in refusals[0]
    assert not strategist_may_set("fees.tune.launch.buy_tip.min", 0.003, cfg)
    assert strategist_may_set("fees.tune.copy.sell_tip.max", 0.003, cfg)
    assert not strategist_may_set("fees.bounds.copy.sell_tip.max", 0.003, cfg)  # the owner's outer bounds
    assert never_tunable("fees.bounds.copy.buy_tip.max")
    assert not strategist_may_set("fees.tune.copy.buy_priority.max", 0.002, cfg)  # outer max is 0.001


def test_the_overlay_moves_a_per_kind_bound_inside_the_owners_and_ignores_one_outside(tmp_path):
    p = tmp_path / "snipetech.toml"
    Config().save(p)
    overlay = tmp_path / "autotune.toml"
    overlay.write_text("[fees.tune.copy.buy_tip]\nmin = 0.004\nmax = 0.004\n\n[fees.tune.copy.sell_tip]\nmax = 0.02\n")
    cfg = Config.load(p, overlay=False)
    cfg.autotune.overlay = str(overlay)
    cfg.save(p)
    cfg = Config.load(p)
    assert cfg.fees.tune["copy"]["buy_tip"]["max"] == 0.004
    assert cfg.fees.tune["copy"]["buy_tip"]["min"] == 0.002  # a minimum in the overlay is never applied
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


def test_sell_fee_sol_is_a_deprecated_alias_for_the_sell_tip_only_and_logs(tmp_path, caplog):
    """The server's file may still carry `sell_fee_sol = 0.002` from the 6 Nov audit fix. The alias is the bid (the
    tip) and nothing else: it never puts the sell priority back to what each failed twin paid, so the fix deploys
    whether or not the owner's file was edited. Whichever order the keys come in."""
    cfg = Config()
    assert cfg.oktech.sell_tip_sol == 0.002 and cfg.oktech.sell_priority_sol == 0.0001
    assert cfg.oktech.sell_fee_sol == 0.002  # readable: the bid
    p = tmp_path / "snipetech.toml"
    p.write_text("[oktech]\nsell_fee_sol = 0.001\n")
    with caplog.at_level(logging.INFO, logger="snipetech.config"):
        loaded = Config.load(p)
    assert loaded.oktech.sell_tip_sol == 0.001 and loaded.oktech.sell_priority_sol == 0.0001
    said = [r for r in caplog.records if "sell_fee_sol" in r.message and "deprecated" in r.message]
    # said for whoever asks (--verbose), no warning: the alias is supported as it is, and as a warning it opened the
    # log of every job on the server for three days (11 Nov), where a warning should mean something is wrong
    assert said and all(r.levelno == logging.INFO for r in said)
    loaded.save(p)  # written back under the new names
    text = p.read_text()
    assert "sell_tip_sol = 0.001" in text and "sell_priority_sol = 0.0001" in text and "sell_fee_sol" not in text
    assert Config.load(p).oktech.sell_priority_sol == 0.0001
    cfg.set("oktech.sell_fee_sol", "0.003")
    assert cfg.oktech.sell_tip_sol == 0.003 and cfg.oktech.sell_priority_sol == 0.0001
    # the alias before or after the new keys: the priority the file set stays
    p.write_text("[oktech]\nsell_priority_sol = 0.0002\nsell_fee_sol = 0.002\nsell_tip_sol = 0.0025\n")
    both = Config.load(p)
    assert both.oktech.sell_priority_sol == 0.0002 and both.oktech.sell_tip_sol == 0.0025


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


def test_a_whole_bid_goes_into_the_tip_and_the_priority_stays_at_oktechs_minimum():
    """12 Nov, the tip ladder: OKTech charges a task's buy priority again on every sell and on every failed duplicate
    sell, while a tip is paid once and comes back when a send fails. So a bid of X is priority 0.001 (OKTech's
    minimum) + tip X - 0.001; a bid under the two minimums together (0.003) is refused, never rounded up."""
    assert (F.OKTECH_MIN_PRIORITY_SOL, F.OKTECH_MIN_TIP_SOL) == (0.001, 0.002)
    assert F.bid_in_tip(0.035) == (0.001, 0.034)
    assert F.bid_in_tip(0.101) == (0.001, 0.1)
    assert F.bid_in_tip(0.003) == (0.001, 0.002)  # the two minimums together: the smallest bid there is
    for bad in (0.0029, 0.002, 0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="0.003"):
            F.bid_in_tip(bad)


def test_a_bid_split_between_priority_and_tip_is_sent_with_all_but_the_minimum_in_the_tip():
    """A bid someone wrote as priority + tip (the book's bid arms: [book] bid_arms) is sent as the same total with the
    priority at the minimum. The control arm (0.001 + 0.002) is unchanged; the paid arm (0.02 + 0.02) goes out as
    0.001 + 0.039."""
    assert F.sent_bid(0.001, 0.002) == (0.001, 0.002)
    assert F.sent_bid(0.02, 0.02) == (0.001, 0.039)
    assert F.sent_bid(0.005, 0.03) == (0.001, 0.034)
    assert F.sent_bid(0.001, 0.005) == (0.001, 0.005)
    assert F.sent_bid(0, 0) == (0.001, 0.002)  # nothing configured: OKTech's minimums


def test_a_priority_above_the_minimum_is_warned_about_in_plain_words():
    assert F.priority_warning(0.001, 0.034) is None
    words = F.priority_warning(0.005, 0.03)
    assert "OKTech charges it again on every sell and failed duplicate sell: put the bid in the tip" in words
    assert "0.005" in words and "--bid 0.035" in words and "0.034" in words
