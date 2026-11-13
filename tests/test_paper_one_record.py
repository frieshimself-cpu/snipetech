"""One forward record a rule.

11 Nov: the strategy search runs every two hours and froze its four best rows each time, whatever was already under
test. The lab picks each trigger's exit on the older part of its moments, and two hours later the pick is another: the
5 SOL hunt (at most 2 wallets, creator 1 SOL or less) went in at 10:15 holding 10 s and again at 12:45 holding 60 s,
both times twice, because the 1 s and the 2 s window fire on the same buys (44 and 44 moments). Four records of one
rule in 2.5 hours, the 1 SOL KOL copy twice. Each is one more chance for the rule to pass forward by luck (a hunt
that passes goes live by itself) and one more strategy for every paper run to score.

A rule is its trigger at a landing and size. It has one record at a time: the exit it was frozen with. No daily limit
on new rules (owner, 9 Nov: paper-test whatever, at any size, until something pays)."""

from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger
from snipetech.config import Config
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

NOW = 1_794_400_000
DAY = 86400
HUNT_AT = (1, "+3")  # where a hunt buy lands
COPY_AT = (0, "+6")  # where a copy lands
HOLD10 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False, watch_secs=1.3)
HOLD60 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False, watch_secs=1.3)
# a wallet rule's exits wait OKTech's watch delay too, as the lab gives them since 12 Nov (lab.WATCHED_KINDS), and
# since 13 Nov they sell when the followed wallet sells half its coins, as every copy sniper does (lab.COPY_EXIT_KINDS)
COPY30 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=30, sell_on_dev_sell=False, watch_secs=1.3,
                    copy_exit_pct=0.5)  # fmt: skip
COPY60 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False, watch_secs=1.3,
                    copy_exit_pct=0.5)  # fmt: skip
HUNT1 = Trigger("hunt", sol=5.0, window=1.0, max_buyers=2, dev_max=1.0)
HUNT2 = Trigger("hunt", sol=5.0, window=2.0, max_buyers=2, dev_max=1.0)  # fires on the same buys as HUNT1
CROWD = Trigger("hunt", sol=10.0, buyers=10, window=1.0)
KOL = Trigger("kol", sol=1.0)


def _coins(tag: str, n: int) -> list[str]:
    return [f"{tag}{k}" for k in range(n)]


def _data(moments: dict[Trigger, list[str]]) -> dict:
    """A collected lab: each trigger with the coins it fired on (one moment a coin, the same buy for the same coin)."""
    triggers = list(moments)
    when: dict[str, int] = {}  # a coin's firing buy: the same second whichever trigger it fires
    rows = [{"mint": m, "trigger": ti, "ts": when.setdefault(m, NOW - 7200 + len(when)), "pnl": [0.01]}
            for ti, t in enumerate(triggers) for m in moments[t]]  # fmt: skip
    return {"rows": rows, "triggers": triggers, "tape_hours": 72.0}


def _row(trig: Trigger, ex: ExitParams, lower: float, landing: tuple[int, str]) -> dict:
    """One ranked lab row, as lab.rank gives it."""
    return {"trigger": trig.label(), "trigger_spec": trig.__dict__, "exit": ex.label(), "exit_params": ex.to_dict(),
            "landing": landing, "moments": 44, "exit_moments": 44, "per_hour": 0.6, "train_mean": 0.01,
            "test": {"n": 18, "mean": 0.02, "ev_lower": lower, "win_rate": 0.5, "total": 0.36}}  # fmt: skip


def _freeze(monkeypatch, store, data, ranked, now=NOW, **kw):
    monkeypatch.setattr(paper, "rank", lambda d, cfg, top=10, **_: list(ranked))
    return paper.freeze(store, Config(), top=kw.pop("top", 4), position="+6", data=data, only_live=True, now=now, **kw)


def _ids(store) -> list[str]:
    return [s["id"] for s in store.coverage("paper_strategies")["strategies"]]


def test_a_rule_in_the_paper_test_is_not_frozen_again_when_the_lab_picks_it_another_exit(monkeypatch):
    store = Store(":memory:")
    data = _data({HUNT1: _coins("a", 44), KOL: _coins("k", 57)})
    first = _freeze(
        monkeypatch, store, data, [_row(HUNT1, HOLD10, 0.0097, HUNT_AT), _row(KOL, COPY30, 0.0013, COPY_AT)]
    )
    assert [s["exit"]["max_hold_secs"] for s in first["frozen"]] == [10, 30] and first["skipped"] == []
    hunt_id, kol_id = _ids(store)
    # the next search, two hours later: the same two rules, each with the exit its older moments pick now
    again = _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD60, 0.0071, HUNT_AT), _row(KOL, COPY60, 0.0029, COPY_AT)],
                    now=NOW + 7200)  # fmt: skip
    assert again["frozen"] == [] and _ids(store) == [hunt_id, kol_id]
    assert [s["label"] for s in again["skipped"]] == [
        f"{HUNT1.label()} | {HOLD60.label()}",
        f"{KOL.label()} | {COPY60.label()}",
    ]
    assert hunt_id in again["skipped"][0]["why"] and kol_id in again["skipped"][1]["why"]
    assert "already in the paper test" in again["skipped"][0]["why"]


def test_the_same_row_again_is_neither_frozen_nor_reported_as_skipped(monkeypatch):
    """The search finds its frozen rules again every two hours with the exit they have: nothing to say."""
    store = Store(":memory:")
    data = _data({HUNT1: _coins("a", 44)})
    _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD10, 0.0097, HUNT_AT)])
    again = _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD10, 0.0090, HUNT_AT)], now=NOW + 7200)
    assert again["frozen"] == [] and again["skipped"] == [] and len(_ids(store)) == 1


def test_two_rules_that_fire_on_the_same_moments_are_one_rule_and_take_one_place(monkeypatch):
    """The 1 s and the 2 s window of a hunt that needs 5 SOL from at most two wallets fire on the same buys (11 Nov: 44
    and 44 moments, bounds +0.00974 and +0.00968). The better row is frozen; the other is the same rule under another
    setting and takes no place among the top, so the next distinct rule is not crowded out."""
    store = Store(":memory:")
    a = _coins("a", 44)
    data = _data({HUNT1: a, HUNT2: a[:43] + ["z9"], CROWD: _coins("c", 30)})
    out = _freeze(monkeypatch, store, data, top=2, ranked=[
        _row(HUNT1, HOLD10, 0.00974, HUNT_AT), _row(HUNT2, HOLD10, 0.00968, HUNT_AT), _row(CROWD, HOLD60, 0.004, HUNT_AT)])  # fmt: skip
    assert [s["trigger"]["window"] for s in out["frozen"]] == [1.0, 1.0]
    assert [s["trigger"]["sol"] for s in out["frozen"]] == [5.0, 10.0]
    (skip,) = out["skipped"]
    assert (
        skip["label"].startswith(HUNT2.label()) and "the same moments" in skip["why"] and HUNT1.label() in skip["why"]
    )


def test_a_twin_of_a_rule_already_under_test_is_not_frozen_later(monkeypatch):
    """The twin stays out at the next search too: the rule under test is in the search's grid, so the lab knows the
    moments it fires on."""
    store = Store(":memory:")
    a = _coins("a", 44)
    data = _data({HUNT1: a, HUNT2: a})
    _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD10, 0.0097, HUNT_AT)])
    out = _freeze(monkeypatch, store, data, [_row(HUNT2, HOLD60, 0.0062, HUNT_AT)], now=NOW + 7200)
    assert out["frozen"] == [] and "the same moments" in out["skipped"][0]["why"] and len(_ids(store)) == 1


def test_a_rule_of_another_kind_on_the_same_moments_takes_no_ones_place(monkeypatch):
    """A whale rule can fire on the very buys a hunt fires on. The hunt is the rule OKTech runs as scored and the one
    kind a live test may start: the whale's record must never be the reason the hunt has none."""
    store = Store(":memory:")
    a = _coins("a", 44)
    whale = Trigger("whale", sol=5.0)
    data = _data({whale: a, HUNT1: a})
    out = _freeze(monkeypatch, store, data, [_row(whale, HOLD60, 0.02, HUNT_AT), _row(HUNT1, HOLD10, 0.0097, HUNT_AT)])
    assert [s["trigger"]["kind"] for s in out["frozen"]] == ["whale", "hunt"] and out["skipped"] == []


def test_rules_that_share_only_some_moments_are_two_rules(monkeypatch):
    store = Store(":memory:")
    a = _coins("a", 40)
    data = _data({HUNT1: a, CROWD: a[:20] + _coins("c", 20)})  # half of each rule's moments are the other's
    out = _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD10, 0.0097, HUNT_AT), _row(CROWD, HOLD60, 0.004, HUNT_AT)])
    assert len(out["frozen"]) == 2 and out["skipped"] == []


def test_a_rule_refuted_forward_rests_a_week_and_takes_no_place(monkeypatch):
    """A rule the forward test refuted (300 trades, three standard errors below zero) is not put back with another
    exit the same day: the lab ranked it top before, and was wrong. Its place goes to the next rule. After a week the
    market may have changed, and the lab may send it again."""
    store = Store(":memory:")
    data = _data({HUNT1: _coins("a", 44), CROWD: _coins("c", 30)})
    _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD10, 0.0097, HUNT_AT)])
    cov = store.coverage("paper_strategies")
    cov["strategies"][0]["refuted"] = {"at": NOW + DAY, "trades": 312, "mean_sol": -0.0131}
    store.set_coverage("paper_strategies", cov)
    ranked = [_row(HUNT1, HOLD60, 0.0071, HUNT_AT), _row(CROWD, HOLD60, 0.004, HUNT_AT)]
    soon = _freeze(monkeypatch, store, data, ranked, now=NOW + 2 * DAY, top=1)
    assert [s["trigger"]["sol"] for s in soon["frozen"]] == [10.0]  # the refuted rule took no place among the top 1
    (skip,) = soon["skipped"]
    assert "refuted" in skip["why"] and "312" in skip["why"] and "-0.0131" in skip["why"]
    later = _freeze(monkeypatch, store, data, ranked, now=NOW + 9 * DAY, top=1)
    assert [(s["trigger"]["sol"], s["exit"]["max_hold_secs"]) for s in later["frozen"]] == [(5.0, 60)]


def test_another_size_is_another_rule(monkeypatch):
    """Owner, 9 Nov: paper-test a lead at any size and fee. The same trigger at 0.5 SOL a buy is its own record."""
    store = Store(":memory:")
    data = _data({HUNT1: _coins("a", 44)})
    _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD10, 0.0097, HUNT_AT)])
    out = _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD60, 0.02, HUNT_AT)], now=NOW + 7200, buy_sol=0.5)
    assert [s["buy_sol"] for s in out["frozen"]] == [0.5] and len(_ids(store)) == 2


def test_no_daily_limit_on_new_rules(monkeypatch):
    store = Store(":memory:")
    rules = [Trigger("hunt", sol=float(s), window=1.0) for s in range(3, 11)]
    data = _data({t: _coins(f"r{i}x", 30) for i, t in enumerate(rules)})
    for k in range(2):  # two searches the same day, four new rules each
        out = _freeze(monkeypatch, store, data, [_row(t, HOLD10, 0.01, HUNT_AT) for t in rules[k * 4 : k * 4 + 4]],
                      now=NOW + k * 7200)  # fmt: skip
        assert len(out["frozen"]) == 4
    assert len(_ids(store)) == 8


def test_a_rule_frozen_before_a_trigger_field_existed_is_the_same_rule(monkeypatch):
    """A strategy frozen on 7 Nov stores the trigger fields of that day. The same rule from today's lab carries every
    field added since at its default: one rule, one record."""
    store = Store(":memory:")
    old_trigger = {"buyers": 0, "kind": "whale", "sol": 5.0, "window": 0.0}
    whale = Trigger("whale", sol=5.0)
    old = {"id": paper.strategy_id(old_trigger, HOLD10.to_dict(), "+3", 1, 0.2), "label": "old", "trigger": old_trigger,
           "exit": HOLD10.to_dict(), "position": "+3", "latency": 1, "buy_sol": 0.2, "per_tx_sol": 0.0045,
           "from_ts": NOW - 4 * DAY}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [old]})
    out = _freeze(monkeypatch, store, _data({whale: _coins("w", 40)}), [_row(whale, HOLD60, 0.01, HUNT_AT)])
    assert out["frozen"] == [] and old["id"] in out["skipped"][0]["why"]


def test_a_rule_another_job_feeds_is_no_rule_of_the_lab(monkeypatch):
    """The deployer-verdict records (kind dev) carry no lab trigger: they never stand in a lab rule's way."""
    store = Store(":memory:")
    fed = {"id": "dev1234567", "label": "deployer verdicts", "trigger": {"kind": "dev", "verdict": "SNIPE", "basis": "1@+3:v4"},
           "exit": HOLD10.to_dict(), "position": "+3", "latency": 1, "buy_sol": 0.5, "per_tx_sol": 0.0042,
           "from_ts": NOW - DAY, paper.FED: "deployer scores"}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [fed]})
    out = _freeze(monkeypatch, store, _data({HUNT1: _coins("a", 44)}), [_row(HUNT1, HOLD10, 0.0097, HUNT_AT)])
    assert len(out["frozen"]) == 1 and out["skipped"] == [] and len(_ids(store)) == 2


# -- an exit sold in parts is a rule of its own (12 Nov, review) ------------------------------------------------------

PARTS10 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False, watch_secs=1.3,
                     sell_parts=4, sell_part_secs=15.0)  # fmt: skip
PARTS60 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False, watch_secs=1.3,
                     sell_parts=2, sell_part_secs=5.0)  # fmt: skip


def test_an_exit_in_parts_does_not_take_the_place_of_the_exit_a_sniper_can_run(monkeypatch):
    """An agent freezes a hunt with a sale in parts (research only: no sniper is sent it). The strategy search then
    finds the same hunt with an exit OKTech runs: that one is the rule that can reach a live test, and it was skipped
    as "already in the paper test" for as long as the record in parts stood."""
    from snipetech.oktech import validate_exit

    store = Store(":memory:")
    data = _data({HUNT1: _coins("a", 44)})
    (parts,) = _freeze(monkeypatch, store, data, [_row(HUNT1, PARTS10, 0.0097, HUNT_AT)])["frozen"]
    assert any("parts" in why for why in validate_exit(parts["exit"]))
    again = _freeze(monkeypatch, store, data, [_row(HUNT1, HOLD10, 0.0071, HUNT_AT)], now=NOW + 7200)
    (plain,) = again["frozen"]
    assert again["skipped"] == [] and plain["exit"] == HOLD10.to_dict() and _ids(store) == [parts["id"], plain["id"]]
    assert not any("parts" in why for why in validate_exit(plain["exit"]))
    # each of the two has its one record: another exit of either kind is the rule already under test
    third = _freeze(monkeypatch, store, data, now=NOW + 14400,
                    ranked=[_row(HUNT1, HOLD60, 0.006, HUNT_AT), _row(HUNT1, PARTS60, 0.005, HUNT_AT)])  # fmt: skip
    assert third["frozen"] == [] and len(_ids(store)) == 2
    assert plain["id"] in third["skipped"][0]["why"] and parts["id"] in third["skipped"][1]["why"]
    # and the other way round: the runnable record first, the one in parts beside it
    other = Store(":memory:")
    _freeze(monkeypatch, other, data, [_row(HUNT1, HOLD10, 0.0071, HUNT_AT)])
    beside = _freeze(monkeypatch, other, data, [_row(HUNT1, PARTS10, 0.0097, HUNT_AT)], now=NOW + 7200)
    assert len(beside["frozen"]) == 1 and beside["skipped"] == []


def test_a_rule_without_parts_keeps_its_rule_key_and_every_id_stays():
    import json

    def before(trigger: dict, position: str, latency: int, buy_sol: float, bid=0.0, asked=False) -> str:
        """`rule_key` as it was until the review."""
        return json.dumps([Trigger(**trigger).__dict__, str(position), int(latency), float(buy_sol), float(bid),
                           bool(asked)], sort_keys=True, default=str)  # fmt: skip

    assert paper.rule_key(KOL.__dict__, "+6", 0, 0.2) == before(KOL.__dict__, "+6", 0, 0.2)
    assert paper.rule_key(HUNT1.__dict__, "+3", 1, 5.0, 0.03, True) == before(HUNT1.__dict__, "+3", 1, 5.0, 0.03, True)
    s = {"trigger": HUNT1.__dict__, "exit": HOLD10.to_dict(), "position": "+3", "latency": 1, "buy_sol": 0.2}
    assert paper._rule_of(s) == before(HUNT1.__dict__, "+3", 1, 0.2)
    in_parts = s | {"exit": PARTS10.to_dict()}
    assert paper._rule_of(in_parts) != paper._rule_of(s)
    assert paper._rule_of(in_parts) == paper._rule_of(s | {"exit": PARTS60.to_dict()})  # whatever the parts
    # the two fields that shape a sale in parts are no sniper's either (oktech.validate_exit refuses each alone)
    assert paper._rule_of(s | {"exit": HOLD10.to_dict() | {"first_part": 0.4}}) == paper._rule_of(in_parts)
    assert paper._rule_of(s | {"exit": HOLD10.to_dict() | {"flat_below": 0.5}}) == paper._rule_of(in_parts)
    # a strategy's id never had the rule key in it
    assert paper.sid_of(s) == paper.strategy_id(HUNT1.__dict__, HOLD10.to_dict(), "+3", 1, 0.2)
    # (a wallet rule as it was frozen before the watch delay and the copy exit: the id it had then)
    before_both = COPY30.to_dict() | {"watch_secs": 0.0, "copy_exit_pct": 0.0, "max_hold_secs": 10}
    assert paper.strategy_id(Trigger("kol", sol=2.0).__dict__, before_both, "+6", 0,
                             0.2) == "69d1450075"  # as tests/test_paper_own_landing


# -- every rule asked for by name is frozen or named (12 Nov, review) -------------------------------------------------


def test_every_rule_of_a_recipe_is_frozen_or_named_whatever_top_says(monkeypatch):
    """`paper freeze --recipe 6z91` at the command's own --top 4: the recipe has five rules, and the fifth was neither
    frozen nor named in `skipped`."""
    from snipetech.analysis.lab import recipe

    r = recipe("6z91")
    assert len(r.triggers) == 5
    store = Store(":memory:")
    data = _data({t: _coins(f"r{k}-", 40) for k, t in enumerate(r.triggers)})
    ranked = [_row(t, r.exit, 0.05 - 0.001 * k, COPY_AT) for k, t in enumerate(r.triggers)]
    monkeypatch.setattr(paper, "rank", lambda d, cfg, top=10, **_: list(ranked))
    rep = paper.freeze(store, Config(), top=4, position="+6", data=data, recipe=r, buy_bid_sol=0.0, now=NOW)
    named = {x["label"].split(" | ")[0] for x in rep["skipped"]}
    assert {s["label"].split(" | ")[0] for s in rep["frozen"]} | named == {t.label() for t in r.triggers}
    assert len(rep["frozen"]) == 5 and rep["skipped"] == []


def test_a_typed_rule_cut_by_top_is_named(monkeypatch):
    store = Store(":memory:")
    kols = [Trigger("kol", sol=float(k)) for k in (1, 2, 3)]
    data = _data({t: _coins(f"t{k}-", 40) for k, t in enumerate(kols)})
    ranked = [_row(t, COPY30, 0.05 - 0.01 * k, COPY_AT) for k, t in enumerate(kols)]
    out = _freeze(monkeypatch, store, data, ranked, top=2, triggers=kols)
    assert [s["trigger"]["sol"] for s in out["frozen"]] == [1.0, 2.0]
    (cut,) = out["skipped"]
    assert cut["label"] == f"{kols[2].label()} | {COPY30.label()}" and "--top 2" in cut["why"]
    # the strategy search's grid names no row it cut: its rows are the lab's whole table, every two hours
    grid = _freeze(monkeypatch, Store(":memory:"), data, ranked, top=2)
    assert len(grid["frozen"]) == 2 and grid["skipped"] == []
