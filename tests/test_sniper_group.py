"""Launch snipers as a group a wallet rule can follow (idea I59, 13 Nov 2026).

The scout: "Hunt on known sniper bots: buy a launch once K proven same-block snipers bought it ... Missing: a label
kind for first-slot snipers that a kol trigger takes as its group, launchers' own wallets told apart."

Three things:

- `winners --snipers` (analysis/launch_snipers.py) finds the wallets whose first buy of a coin is in the coin's
  create block or the two blocks after it on many coins, that made SOL on those coins at their own place with every
  bag they never sold counted at nothing, and that are not a launcher's own wallets. `--label N` labels the best N
  with the kind `sniper`; without it the command only lists.
- a wallet rule's `group` takes `sniper`: {"kind": "kol", "group": "sniper", "known": 2} fires at the second
  labelled sniper's first buy. A hunt's `bots` takes it too (the lab; no sniper is built for it).
- a copy of a buy in a coin's create block never lands in that block: it is placed in the block after it, and the
  lab's answer says that this place is a model, not a measured one.

In these markets a block place is written by hand (`_at`): on the real tape a launcher's bundle stands back to back
behind its create (13 Nov, three create blocks read; in one the create stood at place 42, its four wallets at 43 to
45, and the snipers at 82, 95, 103, 135 and 313 with bids, priority fee and tip together, of 0.008 to 0.27 SOL)."""

import json
import sys
import time

import pytest

from snipetech import cli
from snipetech import ops as O
from snipetech.analysis import launch_snipers as LS
from snipetech.analysis import paper
from snipetech.analysis.lab import (
    SNIPER_GROUP,
    SNIPER_NOTE,
    Trigger,
    _fire,
    collect,
    copy_entry,
    entry_at,
    known_sets,
    label_groups,
    parse_trigger,
    rank,
)
from snipetech.config import Config
from snipetech.operator.jobs import OperatorJobs
from snipetech.sim.exits import Costs, prepare
from snipetech.sim.market import MarketPath
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
A, B, C = addr("sg-sniper-a"), addr("sg-sniper-b"), addr("sg-sniper-c")  # a and b in the create block, c a block later
LOSER, PICKY, LATE = addr("sg-loser"), addr("sg-picky"), addr("sg-late")
TEAM = [addr(f"sg-bundle-{k}") for k in range(3)]  # one launcher's wallets, sent with every create
COSIGN, PAID, ROT, NEAR, FAN1 = (addr(f"sg-{n}") for n in ("cosign", "fee-paid", "rotating", "near", "one-dev-fan"))
SERIAL = addr("sg-serial-dev")
RULE = {"kind": "kol", "group": "sniper", "known": 2}


def _at(trade, place: int, payer: str = ""):
    """The transaction's place in its block (and who paid its fee, when that was not the buyer)."""
    trade.pos, trade.payer = place, payer
    return trade


def launch(st: Store, i: int, *, dev: str | None = None, extras: bool = False, sells: bool = True,
           ts: int | None = None) -> Market:  # fmt: skip
    """One launch. The create's own block: the creator's 1 SOL in the create transaction (place 40), its three bundle
    wallets back to back behind it (41 to 43), then the outsiders, each with a gap: sniper a (90), sniper b (130),
    the loser (200), the picky one (210). A block later sniper c; three blocks later a late buyer, who is no sniper.
    Ten fans lift the price; a, b, c and the bundle sell into them at a profit; the loser sells last, under what it
    paid; the picky one sells only in coin 0 and keeps every other bag. `extras`: four more wallets in the create
    block that belong with the launcher or stand out some other way: one inside the create transaction itself, one
    whose fee the creator paid, one that launches a coin of its own elsewhere, one that the tests give a link to."""
    dev = dev or addr(f"sg-dev-{i}")
    m = Market(st, addr(f"sg-coin-{i}"), dev, ts or NOW - 5000 + i * 100, 80_000_000 + i * 1000)
    _at(m.buy(dev, 1.0, 0, sig=m.create_sig), 40)
    if extras:
        _at(m.buy(COSIGN, 0.5, 0, sig=m.create_sig), 40)
    for k, w in enumerate(TEAM):
        _at(m.buy(w, 1.0, 0), 41 + k)
    _at(m.buy(A, 0.5, 0), 90)
    _at(m.buy(B, 0.5, 0), 130)
    if extras:
        _at(m.buy(PAID, 0.5, 0), 150, payer=dev)
        _at(m.buy(ROT, 0.5, 0), 170)
    _at(m.buy(LOSER, 0.5, 0), 200)
    _at(m.buy(PICKY, 0.5, 0), 210)
    _at(m.buy(C, 0.5, 1), 12)
    _at(m.buy(LATE, 0.5, 3), 5)
    for k in range(10):
        _at(m.buy(addr(f"sg-fan-{i}-{k}"), 0.8, 4 + k), 30)
    if sells:
        for k, w in enumerate([A, B, C, *TEAM, *([COSIGN, PAID, ROT] if extras else [])]):
            _at(m.sell(w, 1.0, 20 + k), 20)
        if i == 0:
            _at(m.sell(PICKY, 1.0, 40), 20)
        for k in range(10):
            _at(m.sell(addr(f"sg-fan-{i}-{k}"), 1.0, 50 + k), 20)
        _at(m.sell(LATE, 1.0, 70), 20)
        _at(m.sell(LOSER, 1.0, 80), 20)
    m.commit()
    return m


def world(st: Store, n: int = 6, **kw) -> list[Market]:
    """`n` launches 100 s apart from 5,000 s ago on, and one trade a minute ago by nobody of interest: the tape goes
    on for more than an hour after the launches, so a bag still held is old enough to be judged."""
    out = [launch(st, i, **kw) for i in range(n)]
    clock = Market(st, addr("sg-clock-coin"), addr("sg-clock-dev"), NOW - 60, 89_000_000)
    clock.buy(clock.deployer, 0.2, 0, sig=clock.create_sig)
    clock.commit()
    st.set_coverage("tape", {"spans": [[NOW - 5100, NOW]]})
    return out


def find(st: Store, **kw) -> dict:
    return LS.launch_snipers(st, NOW, **({"hours": 2, "min_snipes": 5, "samples": 200} | kw))


# -- the finder ------------------------------------------------------------------------------------------------------


def test_wallets_that_snipe_many_launches_and_profit_are_found():
    st = Store(":memory:")
    world(st)
    rep = find(st)
    by = {r["wallet"]: r for r in rep["wallets"]}
    assert set(by) == {A, B, C}
    a, c = by[A], by[C]
    assert (a["snipes"], a["create_block"], a["median_entry_slots"]) == (6, 6, 0)  # in the create block every time
    assert (c["snipes"], c["create_block"], c["median_entry_slots"]) == (6, 0, 1)  # a block later every time
    assert a["creators"] == 6 and a["top_launcher_share"] == round(1 / 6, 3)
    assert a["net_sol"] > 0 and a["lower_sol"] > 0 and a["win_rate"] == 1.0 and a["unsold"] == 0
    assert a["with_launcher"] == 0 and a["with_launcher_share"] == 0.0
    assert a["median_buy_sol"] == pytest.approx(0.5 * 1.0125, abs=1e-3)  # what it paid, the venue's fee in it
    assert [r["wallet"] for r in rep["wallets"]] == sorted(by, key=lambda w: -by[w]["net_sol"])  # best first
    # the late buyer made no first buy within two blocks of a create: never looked at
    assert rep["judged"] == 8 and LATE not in {r["wallet"] for r in rep["launchers"]}
    assert "at its own place" in rep["note"] and "block after" in rep["note"]
    st.close()


def test_a_wallet_that_loses_on_its_snipes_is_not_listed_and_a_bag_never_sold_is_a_loss():
    """The loser sells every coin under what it paid. The picky one sells its one winner and keeps five bags: its
    closed round trips alone are a profit (what `winners` ranks by), and with the SOL its bags cost it is a loss."""
    st = Store(":memory:")
    world(st)
    rep = find(st)
    assert not {LOSER, PICKY} & {r["wallet"] for r in rep["wallets"]}
    assert rep["losing"] == 2
    every = {r["wallet"]: r for r in find(st, proven=False)["wallets"]}
    assert every[LOSER]["net_sol"] < 0 and every[LOSER]["win_rate"] == 0.0
    picky = every[PICKY]
    assert (picky["snipes"], picky["unsold"], picky["win_rate"]) == (6, 5, round(1 / 6, 3))
    assert picky["net_sol"] < 0 < picky["sold_sol"]  # what its one sale made, and all six buys together
    st.close()


def test_a_position_too_young_to_judge_is_not_a_result():
    st = Store(":memory:")
    world(st)
    launch(st, 50, sells=False, ts=NOW - 120)  # two minutes old: nobody has sold, nothing is known yet
    by = {r["wallet"]: r for r in find(st)["wallets"]}
    assert (by[A]["snipes"], by[A]["unsettled"]) == (6, 1)
    assert by[A]["unsold"] == 0
    st.close()


def test_a_snipe_is_a_first_buy_within_the_blocks_asked():
    st = Store(":memory:")
    world(st)
    assert {r["wallet"] for r in find(st, max_slots=0)["wallets"]} == {A, B}  # the create block alone
    assert {r["wallet"] for r in find(st, max_slots=1)["wallets"]} == {A, B, C}
    assert LATE in {r["wallet"] for r in find(st, max_slots=3, proven=False)["wallets"]}  # three blocks after
    assert find(st, min_snipes=7)["wallets"] == []  # six launches are not seven
    st.close()


def test_the_creators_own_bundle_wallets_are_told_apart():
    """The bundle made SOL on every launch and bought in every create block: by its own trades it is the best
    "sniper" of all. It stands back to back behind the create every time, which no outsider does."""
    st = Store(":memory:")
    world(st)
    rep = find(st)
    out = {r["wallet"]: r for r in rep["launchers"]}
    assert set(TEAM) <= set(out) and not set(TEAM) & {r["wallet"] for r in rep["wallets"]}
    row = out[TEAM[2]]  # the third of them: behind the second, which is behind the first, which is behind the create
    assert row["net_sol"] > 0 and (row["snipes"], row["with_launcher"], row["with_launcher_share"]) == (6, 6, 1.0)
    assert row["marks"] == {"behind_create": 6} and "back to back behind the create" in row["why"]
    assert addr("sg-dev-0") not in out and rep["launchers_found"] == 3  # a coin's creator is no buyer of its coin
    st.close()


def test_every_other_mark_of_a_launchers_wallet_tells_it_apart_too():
    st = Store(":memory:")
    coins = world(st, extras=True)
    own = Market(st, addr("sg-rot-own-coin"), ROT, NOW - 4000, 81_000_000)  # the rotating wallet's own launch
    _at(own.buy(ROT, 1.0, 0, sig=own.create_sig), 40)
    own.commit()
    st.upsert_edge(coins[0].deployer, C, "paid_by_dev", NOW - 6000, 2_000_000_000)  # one creator sent c 2 SOL
    st.upsert_edge(coins[1].deployer, C, "paid_by_dev", NOW - 6000, 2_000_000_000)  # and so did another
    rep = find(st)
    out = {r["wallet"]: r for r in rep["launchers"]}
    assert {r["wallet"] for r in rep["wallets"]} == {A, B}
    assert out[COSIGN]["marks"] == {"behind_create": 6} and out[COSIGN]["with_launcher_share"] == 1.0
    assert out[PAID]["marks"] == {"fee_paid": 6} and "its fee paid by the coin's creator" in out[PAID]["why"]
    assert out[ROT]["launched"] == 1 and out[ROT]["marks"] == {} and "launched 1 coin itself" in out[ROT]["why"]
    # two of six coins whose creator had paid it: a third of its snipes are a launcher's own buys
    assert out[C]["marks"] == {"paid_by_creator": 2} and out[C]["with_launcher_share"] == round(2 / 6, 3)
    assert "2 on a coin whose creator had paid it" in out[C]["why"]
    # a link from a creator whose coins it never bought says nothing about its snipes
    other = Store(":memory:")
    world(other)
    other.upsert_edge(addr("sg-some-other-dev"), C, "paid_by_dev", NOW - 6000, 2_000_000_000)
    assert C in {r["wallet"] for r in find(other)["wallets"]}
    other.close()
    st.close()


def test_a_wallet_that_buys_one_creators_launches_is_that_creators():
    """Six launches by one creator. A wallet that buys them all, never back to back behind the create, is still no
    sniper of the market: every snipe is one creator's coin. And the wallet that stands three places behind that
    creator on each of five launches is its companion (analysis.companions), whatever else it buys."""
    st = Store(":memory:")
    world(st)
    for i in range(5):
        m = Market(st, addr(f"sg-serial-coin-{i}"), SERIAL, NOW - 4000 + i * 100, 82_000_000 + i * 1000)
        _at(m.buy(SERIAL, 1.0, 0, sig=m.create_sig), 40)
        _at(m.buy(NEAR, 0.5, 0), 43)  # two places empty between them: not back to back
        _at(m.buy(FAN1, 0.5, 0), 120)
        for k in range(6):
            _at(m.buy(addr(f"sg-sfan-{i}-{k}"), 0.8, 4 + k), 30)
        for k, w in enumerate((NEAR, FAN1)):
            _at(m.sell(w, 1.0, 20 + k), 20)
        m.commit()
    for i in range(6):  # the companion also buys six other creators' launches, far behind the create
        m = Market(st, addr(f"sg-near-coin-{i}"), addr(f"sg-near-dev-{i}"), NOW - 3000 + i * 100, 83_000_000 + i * 1000)
        _at(m.buy(m.deployer, 1.0, 0, sig=m.create_sig), 40)
        _at(m.buy(NEAR, 0.5, 0), 170)
        for k in range(6):
            _at(m.buy(addr(f"sg-nfan-{i}-{k}"), 0.8, 4 + k), 30)
        _at(m.sell(NEAR, 1.0, 20), 20)
        m.commit()
    rep = find(st)
    out = {r["wallet"]: r for r in rep["launchers"]}
    assert not {NEAR, FAN1} & {r["wallet"] for r in rep["wallets"]}
    assert out[FAN1]["top_launcher_share"] == 1.0 and out[FAN1]["marks"] == {}
    assert "100% of its snipes are one launcher's coins" in out[FAN1]["why"]
    assert out[NEAR]["marks"] == {"beside_creator": 5} and out[NEAR]["with_launcher_share"] == round(5 / 11, 3)
    assert out[NEAR]["top_launcher_share"] < 0.5 and "beside the coin's creator" in out[NEAR]["why"]
    st.close()


def test_a_launch_tool_that_signs_for_fresh_creators_is_one_launcher():
    """bwam signs the create for a new creator wallet every time (13 Nov: 528 launches a day, 427 creators). A wallet
    whose snipes are all that tool's launches has six creators and one launcher."""
    st = Store(":memory:")
    world(st)
    tool, fan = addr("sg-launch-tool"), addr("sg-tool-fan")
    for i in range(6):
        m = Market(
            st, addr(f"sg-tool-coin-{i}"), addr(f"sg-tool-client-{i}"), NOW - 4000 + i * 100, 85_000_000 + i * 1000
        )
        m.launch.user = tool  # the tool signs and pays; the creator named in the coin is its client
        st.add_launches([m.launch])
        _at(m.buy(tool, 2.0, 0, sig=m.create_sig), 40)  # and buys in the create transaction, as bwam does
        _at(m.buy(fan, 0.5, 0), 120)
        for k in range(6):
            _at(m.buy(addr(f"sg-tfan-{i}-{k}"), 0.8, 4 + k), 30)
        _at(m.sell(fan, 1.0, 20), 20)
        m.commit()
    rep = find(st)
    out = {r["wallet"]: r for r in rep["launchers"]}
    assert fan not in {r["wallet"] for r in rep["wallets"]} and tool not in out  # the signer is no buyer of its coins
    assert (out[fan]["creators"], out[fan]["top_launcher_share"], out[fan]["marks"]) == (6, 1.0, {})
    assert "one launcher's coins" in out[fan]["why"]
    st.close()


def test_a_dust_buy_is_no_snipe_and_a_real_one_behind_it_still_is():
    """A wallet that sends 0.02 SOL into every launch (6VFrZ1 on the tape of 5 Nov) is nobody a rule follows: a wallet
    rule counts a first buy of 0.1 SOL or more. One that tops a dust buy up within the same blocks is counted at its
    real buy."""
    st = Store(":memory:")
    world(st)
    dust, topped = addr("sg-dust"), addr("sg-dust-then-real")
    for i in range(6):
        m = Market(st, addr(f"sg-dust-coin-{i}"), addr(f"sg-dust-dev-{i}"), NOW - 4000 + i * 100, 86_000_000 + i * 1000)
        _at(m.buy(m.deployer, 1.0, 0, sig=m.create_sig), 40)
        _at(m.buy(dust, 0.02, 0), 100)
        _at(m.buy(topped, 0.02, 0), 140)
        _at(m.buy(topped, 0.5, 1), 20)
        for k in range(6):
            _at(m.buy(addr(f"sg-dfan-{i}-{k}"), 0.8, 4 + k), 30)
        for k, w in enumerate((dust, topped)):
            _at(m.sell(w, 1.0, 20 + k), 20)
        m.commit()
    by = {r["wallet"]: r for r in find(st, proven=False)["wallets"]}
    assert dust not in by
    assert (by[topped]["snipes"], by[topped]["create_block"], by[topped]["median_entry_slots"]) == (6, 0, 1)
    assert by[topped]["median_buy_sol"] == pytest.approx(0.5 * 1.0125, abs=1e-3)  # the dust is no part of the result
    assert by[topped]["net_sol"] > 0 and by[topped]["unsold"] == 0
    st.close()


def test_on_five_real_launches_the_bundles_are_marked_and_the_outsider_in_the_create_block_is_not():
    """Five real launches of 5 Nov (the first seconds of each, block places as recorded). Read by hand: Suncat's
    create stands at place 435 with three buys of 1.4 to 1.6 SOL at 436 to 438; Gogeta's at 72 with three buys in
    one transaction at 73 and one at 74, then an outsider at 158; II's at 616 with two buys at 617 and 618. Bricks and
    RLT have nobody else in the create block but a 0.099 SOL buy 69 places behind RLT's create, which is dust."""
    from .test_lab_hunt import _hunt_fixture

    st, launches = _hunt_fixture()
    newest = max(la.ts for la in launches)
    rep = LS.launch_snipers(st, newest + 3600, hours=24, min_snipes=1, settle_secs=0, proven=False, samples=50)
    rows = {r["wallet"][:6]: r for r in rep["wallets"] + rep["launchers"]}
    bundles = {"FAHMvD", "8NZq6z", "9pnhst", "4TAzuN", "3DT6By", "3sxt4g", "162tKn", "9LMeW9", "9pkcMw"}
    assert {w for w, r in rows.items() if r["marks"]} == bundles
    assert all(rows[w]["marks"] == {"behind_create": 1} and rows[w]["create_block"] == 1 for w in bundles)
    assert rows["H1xshm"]["create_block"] == 1 and rows["H1xshm"]["marks"] == {}  # in Gogeta's create block, 84 back
    assert "7PtVk5" not in rows and "6VFrZ1" not in rows  # under 0.1 SOL: not a buy a wallet rule follows
    assert "bwamJz" not in rows and "2bRoas" not in rows  # the signer and the creator of a coin are no buyers of it
    st.close()


def test_a_wallet_named_as_a_service_a_dev_or_a_bundler_is_never_listed():
    st = Store(":memory:")
    world(st)
    for w, kind in ((A, "bundler"), (B, "own")):
        st.set_label(w, "named", kind, "user")
    rep = find(st, labels=st.label_kinds())
    assert {r["wallet"] for r in rep["wallets"]} == {C}
    assert not {A, B} & {r["wallet"] for r in rep["launchers"]}
    st.close()


# -- the label -------------------------------------------------------------------------------------------------------


def test_the_label_is_written_with_the_kind_and_an_existing_label_of_another_kind_is_kept():
    st = Store(":memory:")
    world(st)
    rows = find(st)["wallets"]
    best, second, third = (r["wallet"] for r in rows)
    st.set_label(second, "a kol the owner named", "kol", "file")
    out = LS.label_snipers(st, rows, 2)
    assert out["labelled"] == [best, third]  # two written; the one with a name of another kind is passed over
    assert out["kept"] == [{"wallet": second, "kind": "kol", "name": "a kol the owner named"}]
    assert st.label_kind(best) == SNIPER_GROUP == "sniper" and st.label_kind(second) == "kol"
    name = st.labels("sniper")[best]
    assert name.startswith(f"{best[:6]}…{best[-4:]} launch sniper") and "6 snipes" in name and "SOL" in name
    assert label_groups(st)["sniper"] == {best, third} and second in label_groups(st)["kol"]
    # a second run keeps the two it wrote (the same kind is written again), and still does not touch the KOL
    again = LS.label_snipers(st, rows, 3)
    assert again["labelled"] == [best, third] and st.label_kind(second) == "kol"
    assert LS.label_snipers(st, rows, 0) == {"labelled": [], "kept": []}  # a dry listing writes nothing
    # a wallet that did not pay is never labelled, however long the list handed over
    every = find(st, proven=False)["wallets"]
    assert {LOSER, PICKY} <= {r["wallet"] for r in every}
    assert LS.label_snipers(st, every, 99)["labelled"] == [best, third]
    st.close()


def test_told_to_a_cand_label_of_our_own_screens_is_replaced_and_a_name_of_the_owners_never():
    st = Store(":memory:")
    world(st)
    rows = find(st)["wallets"]
    best, second, third = (r["wallet"] for r in rows)
    st.set_label(best, "winner bot +30 SOL/40", "cand", "winners")
    st.set_label(second, "tracked by the owner", "wallet", "file")
    st.set_label(third, "6Z91 copy bot (owner: study it)", "smart", "user")
    assert LS.label_snipers(st, rows, 3)["labelled"] == []
    out = LS.label_snipers(st, rows, 3, relabel=True)
    assert out["labelled"] == [best] and st.label_kind(best) == "sniper"
    assert [k["kind"] for k in out["kept"]] == ["wallet", "smart"]
    assert st.label_kind(second) == "wallet" and st.label_kind(third) == "smart"
    st.close()


def test_the_command_lists_without_a_flag_and_labels_with_one(tmp_path, capsys):
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        world(st)
    finally:
        st.close()
    base = ["--db", str(db), "winners", "--snipers", "--hours", "2", "--min-snipes", "5"]
    assert cli.main([*base, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {r["wallet"] for r in out["wallets"]} == {A, B, C} and out["labelled"] == [] and out["kept"] == []
    assert {r["wallet"] for r in out["launchers"]} == set(TEAM)
    assert (out["max_slots"], out["min_snipes"]) == (2, 5)
    st = Store(db)
    assert st.labels("sniper") == {}  # a dry listing
    st.close()
    assert cli.main([*base, "--snipe-slots", "0", "--label", "1", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["labelled"]) == 1 and out["labelled"][0] in (A, B) and out["max_slots"] == 0
    st = Store(db)
    assert set(st.labels("sniper")) == set(out["labelled"])
    assert not st.labels("cand") and not st._q("SELECT 1 FROM candidates")  # nothing goes to qualify's queue
    st.close()
    assert cli.main(base) == 0  # in words
    text = capsys.readouterr().out
    assert A in text and "launcher's own" in text and TEAM[0] in text and LOSER not in text
    # --unproven also lists the wallets that did not pay (to see why a named one is missing); none of them is labelled
    assert cli.main([*base, "--unproven", "--label", "9", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {LOSER, PICKY} <= {r["wallet"] for r in out["wallets"]} and set(out["labelled"]) == {A, B, C}
    # by hand: `label ADDRESS NAME --kind sniper` puts a wallet in the group whatever it was called before
    assert cli.main(["--db", str(db), "label", C, "c, by hand", "--kind", "sniper"]) == 0
    capsys.readouterr()
    st = Store(db)
    assert st.label_kind(C) == "sniper"
    st.close()


def test_the_plain_winners_command_is_what_it_was(tmp_path, capsys):
    db = tmp_path / "snipetech.db"
    st = Store(db)
    try:
        world(st)
    finally:
        st.close()
    assert cli.main(["--db", str(db), "winners", "--hours", "2", "--min-trips", "5", "--label", "9", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "launchers" not in out and out["wallets"][0]["trips"] >= 5
    st = Store(db)
    assert st.labels("sniper") == {}  # without --snipers its labels are `cand`, as before
    st.close()


def test_an_agent_may_start_the_finder_and_a_lab_on_the_group(tmp_path):
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True)
    jobs = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main", max_jobs=3)
    jobs.ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                     command=[sys.executable, "-c", "import sys; print('ran', sys.argv[1:])"])  # fmt: skip
    assert jobs.start(["winners", "--snipers", "--hours", "72", "--json"])["id"]
    with pytest.raises(PermissionError, match="group must be one of kol, wallet, cand, trader, big, sniper"):
        jobs.start(["lab", "--trigger", '{"kind": "kol", "group": "snipers"}', "--hours", "72", "--json"])
    assert jobs.start(["lab", "--trigger", json.dumps(RULE), "--buy", "0.5", "--hours", "72", "--json"])["id"]


# -- the group in a wallet rule -------------------------------------------------------------------------------------


def labelled(st: Store, *wallets: str) -> dict:
    for w in wallets:
        st.set_label(w, "launch sniper", "sniper", "snipers")
    return label_groups(st)


def path_of(st: Store, m: Market) -> MarketPath:
    return MarketPath.build(st.trades_for_mint(m.mint), st.launch(m.mint))


def at(path: MarketPath, wallet: str) -> int:
    return next(k for k in range(path.n) if path.users[k] == wallet and path.is_buy[k])


def test_the_group_is_an_agents_to_name_and_the_label_says_what_it_is():
    trig = parse_trigger(RULE)
    assert (trig.kind, trig.group, trig.known) == ("kol", "sniper", 2)
    assert trig.label() == ("a labelled launch sniper makes its first buy in the coin, >= 0.1 SOL, "
                            "with >= 2 known wallets in")  # fmt: skip
    assert parse_trigger({"kind": "kol", "group": "kol"}).label().startswith("a KOL makes")  # as it always read
    assert parse_trigger({"kind": "kol", "group": "cand"}).label().startswith("a tracked wallet (cand) makes")
    with pytest.raises(ValueError, match="group must be one of kol, wallet, cand, trader, big, sniper, not 'bots'"):
        parse_trigger({"kind": "kol", "group": "bots"})


def test_the_rule_fires_at_the_kth_snipers_first_buy_and_not_before():
    """a (place 90) and b (place 130) buy in the create's own block, c a block later. With 2 known the rule fires at
    b's buy, the second sniper in, though both stand in one block; with 3 at c's; with 4 never."""
    st = Store(":memory:")
    (m,) = world(st, 1)
    kols = labelled(st, A, B, C)
    path, launch_ = path_of(st, m), st.launch(m.mint)

    def fires(**kw) -> int | None:
        return _fire(Trigger("kol", group="sniper", **kw), path, launch_, kols)

    assert fires() == at(path, A)  # the plain rule: the first sniper in
    assert fires(known=2) == at(path, B) and int(path.slot[at(path, B)]) == int(launch_.slot)
    assert fires(known=3) == at(path, C) and int(path.slot[at(path, C)]) == int(launch_.slot) + 1
    assert fires(known=4) is None
    assert fires(known=2, max_age=10) == at(path, B)  # "coins under 10 s old": the age gate of every wallet rule
    # the launcher's bundle bought ahead of them all and is no sniper: it counts for nothing
    assert at(path, TEAM[0]) < at(path, A) and fires(known=3) == at(path, C)
    # with the creator and its bundle labelled too, the creator's own buy still fires nothing (a coin's creator is
    # never followed in its own coin), and the bundle, wrongly labelled, would: which is why the finder leaves it out
    wrong = labelled(st, m.deployer)
    assert _fire(Trigger("kol", group="sniper"), path, launch_, wrong) == at(path, A)
    st.close()


def test_one_sniper_alone_never_makes_two():
    st = Store(":memory:")
    (m,) = world(st, 1)
    kols = labelled(st, A)  # b and c are not labelled: nobody's signal
    path, launch_ = path_of(st, m), st.launch(m.mint)
    assert _fire(Trigger("kol", group="sniper"), path, launch_, kols) == at(path, A)
    assert _fire(Trigger("kol", group="sniper", known=2), path, launch_, kols) is None
    st.close()


def test_known_any_still_counts_the_rules_own_snipers():
    """`known_any` counts the tracked wallets of every group. The snipers are a group of their own beside those, and
    a rule on them counts its own group whether or not it widens the gate; no other group's rule counts them."""
    st = Store(":memory:")
    (m,) = world(st, 1)
    kols = labelled(st, A, B)
    st.set_label(LOSER, "a tracked wallet", "wallet", "file")
    kols = label_groups(st)
    path, launch_ = path_of(st, m), st.launch(m.mint)
    wide = Trigger("kol", group="sniper", known=2, known_any=True)
    assert any(A in g for g in known_sets(wide, kols)) and any(LOSER in g for g in known_sets(wide, kols))
    assert _fire(wide, path, launch_, kols) == at(path, B)
    other = Trigger("kol", group="wallet", known=2, known_any=True)  # the tracked list's rule, as before
    assert not any(A in g for g in known_sets(other, kols))
    assert _fire(other, path, launch_, kols) is None  # the loser is the only tracked wallet in
    st.close()


# -- where a copy of a create-block buy lands -----------------------------------------------------------------------


def test_a_copy_of_a_buy_in_the_create_block_lands_in_the_block_after_it():
    """CLAUDE.md rule 2: launch snipes land a slot after the create. OKTech sees a buy in the create's own block only
    once that block is out, so a copy of b's buy (two more buys behind it in that block) cannot stand among them: the
    N trades ahead of it are counted in the block after the create."""
    st = Store(":memory:")
    (m,) = world(st, 1)
    path = path_of(st, m)
    created = int(st.launch(m.mint).slot)
    b = at(path, B)
    last = max(k for k in range(path.n) if int(path.slot[k]) == created)  # the picky one's buy: the block's last
    assert int(path.slot[b]) == created and last == b + 2 and int(path.slot[last + 1]) == created + 1
    # until 13 Nov: "+1" and "+2" stood inside the create block, behind b
    assert int(path.landing_index(created, "+1", b)) == b + 1 and int(path.landing_index(created, "+2", b)) == last
    assert entry_at("kol", path, b, created, 0, "+0") == last  # first in the block after the create, at the best
    assert entry_at("kol", path, b, created, 0, "+1") == last + 1  # one trade of that block ahead of us (c's buy)
    assert entry_at("kol", path, b, created, 0, "+6") == last + 1  # never past that block's last trade
    assert entry_at("kol", path, b, created, 0, "start") == last
    assert entry_at("kol", path, b, created, 0, "end") == last + 1
    # a's copy lands at the same places: behind the whole create block, not behind a
    assert entry_at("kol", path, at(path, A), created, 0, "+1") == last + 1
    # the landing counted across blocks is what it was: behind the whole create block at the earliest
    assert entry_at("kol", path, b, created, 0, "+1x") == copy_entry(path, b, 1) == last
    assert entry_at("kol", path, b, created, 0, "+3x") == copy_entry(path, b, 3) == last + 1
    st.close()


def test_a_copy_of_any_other_buy_lands_where_it_always_did():
    st = Store(":memory:")
    (m,) = world(st, 1)
    path = path_of(st, m)
    created = int(st.launch(m.mint).slot)
    c, late = at(path, C), at(path, LATE)
    for i, slot in ((c, created + 1), (late, created + 3)):
        for pos in ("+0", "+1", "+6", "start", "end"):
            assert entry_at("kol", path, i, slot, 0, pos) == int(path.landing_index(slot, pos, i))
    b = at(path, B)
    # a landing that names a later block already says where it is; other kinds of rule never came through here
    assert entry_at("kol", path, b, created, 1, "+1") == int(path.landing_index(created + 1, "+1", b))
    assert entry_at("whale", path, b, created, 0, "+1") == b + 1
    # a coin whose create the tape does not hold has no create block to stand behind
    old = MarketPath.build(st.trades_for_mint(m.mint), None)
    k = at(old, B)
    assert entry_at("kol", old, k, created, 0, "+1") == int(old.landing_index(created, "+1", k)) == k + 1
    st.close()


def test_where_the_block_after_the_create_opens_lower_the_copy_pays_the_dearer_price():
    """The bundle dumps in the first transactions of the block after the create. A copy counted six trades into that
    block would buy under what the create block closed at: it is priced where the create block ended, the dearest
    state between there and its place, never the cheaper one."""
    st = Store(":memory:")
    m = Market(st, addr("sg-dump-coin"), addr("sg-dump-dev"), NOW - 3000, 84_000_000)
    _at(m.buy(m.deployer, 1.0, 0, sig=m.create_sig), 40)
    for k, w in enumerate(TEAM):
        _at(m.buy(w, 2.0, 0), 41 + k)
    _at(m.buy(A, 0.5, 0), 90)
    _at(m.buy(B, 0.5, 0), 130)
    for k, w in enumerate(TEAM):
        _at(m.sell(w, 1.0, 1), 3 + k)
    for k in range(4):
        _at(m.buy(addr(f"sg-dump-fan-{k}"), 0.2, 1), 50 + k)
    m.commit()
    path = path_of(st, m)
    created = int(st.launch(m.mint).slot)
    b = at(path, B)
    assert int(path.slot[b + 1]) == created + 1 and not path.is_buy[b + 1]  # b's buy closed the create block
    six = int(path.landing_index(created + 1, "+6", b))
    assert six == b + 6 and path.q[six] / path.b[six] < path.q[b] / path.b[b]
    assert entry_at("kol", path, b, created, 0, "+6") == b
    st.close()


# -- the lab ---------------------------------------------------------------------------------------------------------


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 100
    return cfg


def lab_world(st: Store, n: int = 40) -> list[Market]:
    out = [launch(st, i, ts=NOW - 5000 + i * 100) for i in range(n)]
    st.set_coverage("tape", {"spans": [[NOW - 5100, NOW]]})
    labelled(st, A, B, C)
    return out


def test_the_lab_scores_the_rule_once_a_coin_at_the_second_snipers_buy_a_block_after_the_create():
    st = Store(":memory:")
    coins = lab_world(st)
    rule, plain = parse_trigger(RULE), parse_trigger({"kind": "kol", "group": "sniper"})
    late = parse_trigger({"kind": "kol", "group": "wallet"})
    st.set_label(LATE, "a tracked wallet", "wallet", "file")
    data = collect(st, _cfg(), 0.2, 0, "+1", triggers=[rule, plain, late], workers=1)
    rows = [r for r in data["rows"] if r["trigger"] == 0]
    assert len(rows) == 40 and {r["mint"] for r in rows} == {m.mint for m in coins}
    assert {r["who"] for r in rows} == {B} and {r["landing"] for r in rows} == {"+1@0"}
    assert all(r["create_block"] is True for r in rows)
    assert {r["who"] for r in data["rows"] if r["trigger"] == 1} == {A}
    # a rule whose followed buys are nowhere near a create block carries no such mark
    tracked = [r for r in data["rows"] if r["trigger"] == 2]
    assert len(tracked) == 40 and not any("create_block" in r for r in tracked)
    # the copy was bought one trade into the block after the create: c's buy stood ahead of it, never b's own block
    path = path_of(st, coins[0])
    row = next(r for r in rows if r["mint"] == coins[0].mint)

    def impact(k: int) -> float:  # how far a 0.2 SOL buy at state k moves the price: one number a state
        return float(prepare(path, k, 0.2, Costs()).impact)

    inside, after = at(path, B) + 1, at(path, C)  # one trade behind b in its own block; behind c, a block later
    assert int(path.slot[inside]) == int(path.slot[at(path, B)]) and impact(inside) != pytest.approx(impact(after))
    assert row["impact"] == pytest.approx(impact(after))
    ranked = {r["trigger"]: r for r in rank(data, _cfg(), top=10)}
    mine = ranked[rule.label()]
    assert (mine["moments"], mine["create_block_moments"]) == (40, 40) and mine["trigger_spec"]["group"] == "sniper"
    assert "create_block_moments" not in ranked[late.label()]
    st.close()


def test_a_lab_answer_never_calls_a_sniper_rules_landing_live():
    """Rule 2. No copy of ours was ever measured behind a buy in a create block: the place the lab gives it is a
    model. The answer says so in its own fields; every other wallet rule reads as before."""
    spec = parse_trigger(RULE).__dict__
    got = paper.landing_fields({"landing": [0, "+6"], "trigger_spec": spec}, "+6")
    assert got == {"landing": "+6@0", "live_landing": False, "landing_note": SNIPER_NOTE}
    for words in ("create block", "block after", "not measured", "create_block_moments"):
        assert words in SNIPER_NOTE
    kol = parse_trigger({"kind": "kol", "group": "kol", "known": 2}).__dict__
    assert paper.landing_fields({"landing": [0, "+6"], "trigger_spec": kol}, "+6") == {
        "landing": "+6@0", "live_landing": True}  # fmt: skip
    hunt = parse_trigger({"kind": "hunt", "sol": 2, "window": 2, "bots": "sniper"}).__dict__
    assert paper.landing_fields({"landing": [1, "+3"], "trigger_spec": hunt}, "+6") == {
        "landing": "+3@1", "live_landing": True}  # fmt: skip


def test_the_lab_command_scores_the_rule_and_says_its_place_is_a_model(tmp_path, capsys):
    db = tmp_path / "data" / "snipetech.db"
    db.parent.mkdir(parents=True)
    st = Store(db)
    try:
        lab_world(st)
    finally:
        st.close()
    base = ["--db", str(db), "lab", "--trigger", json.dumps(RULE), "--buy", "0.2", "--hours", "2", "--json"]
    assert cli.main(base) == 0
    out = json.loads(capsys.readouterr().out)
    (mine,) = out["strategies"]
    assert mine["trigger"] == parse_trigger(RULE).label() and mine["moments"] == 40
    assert (mine["landing"], mine["live_landing"], mine["landing_note"]) == ("+6@0", False, SNIPER_NOTE)
    assert mine["create_block_moments"] == 40 and mine["test"]["n"] > 0
    (listed,) = out["triggers"]
    assert (listed["moments"], listed["live_landing"], listed["landing_note"]) == (40, False, SNIPER_NOTE)
    assert out["copy_landing"] == "+6"
    assert cli.main(base[:-1]) == 0  # in words: the line that says where copies land live does not stand alone
    text = capsys.readouterr().out
    assert f"No live landing: {parse_trigger(RULE).label()} (scored at +6@0). {SNIPER_NOTE}" in text


def test_frozen_for_the_forward_paper_test_the_rule_is_never_reported_at_a_live_landing():
    st = Store(":memory:")
    lab_world(st)
    rule, tracked = parse_trigger(RULE), parse_trigger({"kind": "kol", "group": "wallet"})
    st.set_label(LATE, "a tracked wallet", "wallet", "file")
    rep = paper.freeze(st, _cfg(), top=4, now=NOW, triggers=[rule, tracked], buy_sol=0.2)
    ids = {s["trigger"]["group"]: s["id"] for s in rep["frozen"]}
    assert set(ids) == {"sniper", "wallet"}, rep["skipped"]
    rows = {r["id"]: r for r in paper.report(st, _cfg(), now=NOW)["strategies"]}
    assert rows[ids["sniper"]]["live_landing"] is False and rows[ids["sniper"]]["landing_note"] == SNIPER_NOTE
    assert rows[ids["wallet"]]["live_landing"] is True and "landing_note" not in rows[ids["wallet"]]
    st.close()


# -- the hunt that asks for a known bot: the form a sniper can run ---------------------------------------------------


def test_a_hunt_can_ask_for_a_labelled_sniper_among_the_buyers_and_no_sniper_is_built_for_it_yet():
    """OKTech hunt takes our own wallet list (`knownBotWallets` with `requireKnownBot`): a hunt that fires only once
    a labelled sniper bought is the form of this idea a sniper could run, for one sniper in (it has no "K of them").
    The lab scores it; the sniper builder is unchanged and still knows the bundler group alone."""
    from snipetech.oktech import BOT_GROUPS, strategy_sniper

    st = Store(":memory:")
    (m,) = world(st, 1)
    path, launch_ = path_of(st, m), st.launch(m.mint)
    trig = parse_trigger({"kind": "hunt", "sol": 2, "window": 2, "bots": "sniper"})
    assert "a wallet of the group sniper among the buyers" in trig.label()
    assert _fire(trig, path, launch_, labelled(st, A)) == at(path, A)  # 4 SOL are in by then, and now a sniper too
    assert _fire(trig, path, launch_, {"sniper": set()}) is None
    assert _fire(parse_trigger({"kind": "hunt", "sol": 2, "window": 2}), path, launch_, {}) == at(path, TEAM[0])
    with pytest.raises(ValueError, match="bots must be one of bundler, sniper"):
        parse_trigger({"kind": "hunt", "sol": 2, "window": 2, "bots": "snipers"})
    assert BOT_GROUPS == ("bundler",)
    strat = {"id": "x", "label": "hunt on snipers", "buy_sol": 0.2, "exit": {"stop_loss": 0.2, "max_hold_secs": 5},
             "trigger": trig.__dict__}  # fmt: skip
    with pytest.raises(ValueError, match="'sniper' is not a wallet group a hunt can be limited to"):
        strategy_sniper(strat, 5, bot_wallets=[A])
    st.close()
