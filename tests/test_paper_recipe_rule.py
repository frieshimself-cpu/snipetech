"""Two recipes of one trigger are two rules, each with its own forward record (review of 12 Nov).

A recipe is a winning bot's rules by name (lab.RECIPES): its triggers, its size, its bid and, above all, its own exit.
`6z91` and `6z91-quick` have the same five triggers at the same size and bid and differ in the exit alone: the clock
the bot sells its copies on, and the faster one it runs on its launch snipes. A rule was its trigger at a landing and
size, whatever its exit (`paper.rule_key`), so the second recipe frozen found each of its rules "already in the paper
test" under the first one's exit, and the comparison the second recipe exists for never ran forward.

The recipe is now part of a rule whenever one is set. A rule frozen without a recipe has the key it always had, and a
strategy's id (what `paper_trades` is keyed by) never held the rule key: no record on the server changes its id."""

import json
from dataclasses import replace

from snipetech.analysis import paper
from snipetech.analysis.lab import RECIPES, Trigger, recipe
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store
from tests.test_paper_one_record import COPY_AT, HUNT1, KOL, NOW, PARTS10, _coins, _data, _ids, _row
from tests.test_recipe import NOW as WORLD_NOW
from tests.test_recipe import _cfg, _recipe_world

SLOW, QUICK = recipe("6z91"), recipe("6z91-quick")


def _before(trigger: dict, position: str, latency: int, buy_sol: float, bid=0.0, asked=False, parts=False) -> str:
    """`rule_key` as it was until this change."""
    key = [Trigger(**trigger).__dict__, str(position), int(latency), float(buy_sol), float(bid), bool(asked)]
    return json.dumps(key + (["parts"] if parts else []), sort_keys=True, default=str)


def _watched(r) -> ExitParams:
    return replace(r.exit, watch_secs=1.3)


def _freeze_recipe(monkeypatch, store, r, now=NOW, lower=0.05, **kw):
    """`paper freeze --recipe`: a collected lab in which each of the recipe's triggers fired on forty coins of its own
    and ranks with the recipe's exit, which waits OKTech's watch delay as every wallet rule's exit does in a lab
    (lab.WATCHED_KINDS; a record without the delay does not stand for its rule, `paper._sells_as_live`). No bid: a
    freeze takes none with a lab handed over ready-made; the test with the real lab below freezes at the recipes'
    own bid."""
    kw.setdefault("buy_bid_sol", 0.0)
    data = _data({t: _coins(f"r{k}-", 40) for k, t in enumerate(r.triggers)})
    ranked = [_row(t, _watched(r), lower - 0.001 * k, COPY_AT) for k, t in enumerate(r.triggers)]
    monkeypatch.setattr(paper, "rank", lambda d, cfg, top=10, **_: list(ranked))
    return paper.freeze(store, Config(), top=4, position="+6", data=data, recipe=r, now=now, **kw)


def test_the_tables_recipes_this_is_about():
    assert {"6z91", "6z91-quick", "6z91-big", "fhbh1d-big"} <= set(RECIPES)
    # one trigger family, one size, one bid: the exit is all that tells the two apart
    assert SLOW.triggers == QUICK.triggers and (SLOW.buy_sol, SLOW.bid_sol) == (QUICK.buy_sol, QUICK.bid_sol)
    assert (
        SLOW.exit != QUICK.exit
        and paper.sold_in_parts(SLOW.exit.to_dict())
        and paper.sold_in_parts(QUICK.exit.to_dict())
    )


def test_a_rule_without_a_recipe_has_the_key_of_today():
    assert paper.rule_key(KOL.__dict__, "+6", 0, 0.2) == _before(KOL.__dict__, "+6", 0, 0.2)
    assert paper.rule_key(HUNT1.__dict__, "+3", 1, 5.0, 0.03, True) == _before(HUNT1.__dict__, "+3", 1, 5.0, 0.03, True)
    assert paper.rule_key(KOL.__dict__, "+6", 0, 2.0, 0.03, False, True) == _before(
        KOL.__dict__, "+6", 0, 2.0, 0.03, False, True
    )
    for none in (None, ""):  # no recipe, however it is said
        assert paper.rule_key(KOL.__dict__, "+6", 0, 2.0, 0.03, False, True, none) == _before(
            KOL.__dict__, "+6", 0, 2.0, 0.03, False, True
        )
    # a stored rule: with no `recipe` field its rule is the one it was, in parts or not
    s = {"trigger": HUNT1.__dict__, "exit": PARTS10.to_dict(), "position": "+3", "latency": 1, "buy_sol": 0.2}
    assert paper._rule_of(s) == _before(HUNT1.__dict__, "+3", 1, 0.2, parts=True)


def test_two_recipes_one_trigger_two_rule_keys():
    trig = SLOW.triggers[0].__dict__
    at = ("+6", 0, 2.0, 0.03, False, True)
    plain = paper.rule_key(trig, *at)
    slow, quick = paper.rule_key(trig, *at, "6z91"), paper.rule_key(trig, *at, "6z91-quick")
    assert len({plain, slow, quick}) == 3
    assert slow == paper.rule_key(trig, *at, recipe="6z91")
    # a stored rule that names its recipe is that recipe's rule
    s = {"trigger": trig, "exit": SLOW.exit.to_dict(), "position": "+6", "latency": 0, "buy_sol": 2.0,
         "buy_bid_sol": 0.03, "recipe": "6z91"}  # fmt: skip
    assert paper._rule_of(s) == slow
    assert paper._rule_of(s | {"exit": QUICK.exit.to_dict(), "recipe": "6z91-quick"}) == quick
    # a strategy's id has no recipe in it: the trigger, the exit, the landing, the size and the bid, as ever
    assert paper.sid_of(s) == paper.strategy_id(trig, SLOW.exit.to_dict(), "+6", 0, 2.0, 0.03)
    assert paper.sid_of(s) == paper.sid_of({k: v for k, v in s.items() if k != "recipe"})


def test_freezing_one_trigger_under_two_recipes_gives_two_ids_and_two_records(monkeypatch):
    store = Store(":memory:")
    first = _freeze_recipe(monkeypatch, store, SLOW)
    assert len(first["frozen"]) == 5 and first["skipped"] == []
    second = _freeze_recipe(monkeypatch, store, QUICK, now=NOW + 7200)
    # until this change: nothing frozen, five rows "already in the paper test" under the first recipe's exit
    assert second["skipped"] == [] and len(second["frozen"]) == 5
    stored = store.coverage("paper_strategies")["strategies"]
    assert len(stored) == 10 and len(set(_ids(store))) == 10
    by_recipe = {name: {json.dumps(s["trigger"], sort_keys=True): s for s in stored if s["recipe"] == name}
                 for name in ("6z91", "6z91-quick")}  # fmt: skip
    assert set(by_recipe["6z91"]) == set(by_recipe["6z91-quick"]) and len(by_recipe["6z91"]) == 5
    for trig, slow in by_recipe["6z91"].items():
        quick = by_recipe["6z91-quick"][trig]
        assert slow["id"] != quick["id"]  # two ids for the one trigger
        assert (slow["buy_sol"], slow["position"]) == (quick["buy_sol"], quick["position"]) == (2.0, "+6")
        assert ExitParams(**slow["exit"]) == _watched(SLOW) and ExitParams(**quick["exit"]) == _watched(QUICK)
    store.close()


def test_a_recipe_frozen_again_keeps_its_one_record_a_rule(monkeypatch):
    store = Store(":memory:")
    _freeze_recipe(monkeypatch, store, SLOW)
    _freeze_recipe(monkeypatch, store, QUICK, now=NOW + 3600)
    ids = _ids(store)
    for r in (SLOW, QUICK):  # each again, two hours on: every rule is under test, nothing new
        again = _freeze_recipe(monkeypatch, store, r, now=NOW + 7200)
        assert again["frozen"] == [] and _ids(store) == ids


def test_one_trigger_with_one_exit_under_two_recipe_names_is_one_record_and_is_named(monkeypatch):
    """`6z91-big` at 6z91's size and bid (`--recipe 6z91-big --buy 2 --buy-bid 0.03`) is 6z91's own fifth rule, exit
    and all: the same strategy, the same id. It has its one record, and the freeze says where it is."""
    store = Store(":memory:")
    first = _freeze_recipe(monkeypatch, store, SLOW)
    big = recipe("6z91-big")
    assert big.triggers == SLOW.triggers[4:] and big.exit == SLOW.exit
    (same,) = [s for s in first["frozen"] if s["trigger"] == big.triggers[0].__dict__]
    out = _freeze_recipe(monkeypatch, store, big, now=NOW + 3600, buy_sol=SLOW.buy_sol)
    assert out["frozen"] == [] and len(_ids(store)) == 5
    (named,) = out["skipped"]
    assert (
        same["id"] in named["why"]
        and "the recipe 6z91:" in named["why"]
        and "is already in the paper test" in named["why"]
    )
    # the same trigger and exit typed by hand, no recipe named: the same one record, named the same way
    data = _data({big.triggers[0]: _coins("r4-", 40)})
    monkeypatch.setattr(
        paper, "rank", lambda d, cfg, top=10, **_: [_row(big.triggers[0], _watched(big), 0.05, COPY_AT)]
    )
    typed = paper.freeze(store, Config(), top=4, position="+6", data=data, buy_sol=2.0, now=NOW + 3700)
    assert typed["frozen"] == [] and len(_ids(store)) == 5 and same["id"] in typed["skipped"][0]["why"]
    # once that record is refuted, the freeze under the other name says so and takes no place for it
    cov = store.coverage("paper_strategies")
    for s in cov["strategies"]:
        if s["id"] == same["id"]:
            s["refuted"] = {"at": NOW + 3800, "trades": 40, "mean_sol": -0.2}
    store.set_coverage("paper_strategies", cov)
    out = _freeze_recipe(monkeypatch, store, big, now=NOW + 3900, buy_sol=SLOW.buy_sol)
    assert out["frozen"] == [] and "was refuted in the paper test as " + same["id"] in out["skipped"][0]["why"]
    # at its own size and bid it is a rule of its own, as it was before this change
    own = _freeze_recipe(monkeypatch, store, big, now=NOW + 7200)
    assert len(own["frozen"]) == 1 and own["frozen"][0]["recipe"] == "6z91-big" and own["skipped"] == []
    store.close()


def test_a_refuted_recipe_rule_does_not_block_the_other_recipes_rule(monkeypatch):
    store = Store(":memory:")
    _freeze_recipe(monkeypatch, store, SLOW)
    cov = store.coverage("paper_strategies")
    for s in cov["strategies"]:
        s["refuted"] = {"at": NOW + 600, "trades": 40, "mean_sol": -0.2}
    store.set_coverage("paper_strategies", cov)
    again = _freeze_recipe(monkeypatch, store, SLOW, now=NOW + 3600)  # the refuted recipe: not again for a week
    assert again["frozen"] == [] and all("refuted in the paper test" in x["why"] for x in again["skipped"])
    other = _freeze_recipe(monkeypatch, store, QUICK, now=NOW + 7200)  # the other exit has not been tested forward
    assert len(other["frozen"]) == 5 and other["skipped"] == []
    store.close()


def test_two_recipes_frozen_from_a_real_lab_run_forward_side_by_side():
    """The lab itself, on the recipe tests' synthetic market: each recipe's `wallet` rule is frozen with the recipe's
    own exit, and the forward run keeps a record for each."""
    store = Store(":memory:")
    _recipe_world(store)
    slow = paper.freeze(store, _cfg(), top=10, per_tx_sol=0.001, recipe=SLOW, now=WORLD_NOW)
    quick = paper.freeze(store, _cfg(), top=10, per_tx_sol=0.001, recipe=QUICK, now=WORLD_NOW)
    (a,), (b,) = slow["frozen"], quick["frozen"]
    assert a["trigger"] == b["trigger"] and a["id"] != b["id"]
    assert (a["recipe"], b["recipe"]) == ("6z91", "6z91-quick")
    assert ExitParams(**a["exit"]) == _watched(SLOW) and ExitParams(**b["exit"]) == _watched(QUICK)
    # the two run forward from the same moment: every buy the trigger fires on is a trade in each record
    cov = store.coverage("paper_strategies")
    for s in cov["strategies"]:
        s["from_ts"] = WORLD_NOW - 5100
    store.set_coverage("paper_strategies", cov)
    paper.run(store, _cfg(), now=WORLD_NOW + 600)
    counts = dict(store._q("SELECT strategy, COUNT(*) FROM paper_trades GROUP BY strategy"))
    assert set(counts) == {a["id"], b["id"]} and counts[a["id"]] == counts[b["id"]] > 0
    store.close()
