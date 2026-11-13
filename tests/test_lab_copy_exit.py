"""A wallet rule in the lab is sold the way a copy sniper sells it: also when the followed wallet sells (13 Nov 2026).

The audit of 13 Nov: "A lab started with --trigger scores a wallet rule on 61 exits and none of them sells when the
followed wallet sells. Every copy sniper we build is sent that exit switched on (ctbotCopyExit: sell everything once
the copied wallet sells [oktech] copy_exit_threshold_percent of its coins in one sale), and oktech.validate_exit refuses
an exit scored with a different copy-exit setting. So a wallet rule frozen from such a lab is paper-tested with an exit
no copy sniper would run as scored."

Since 13 Nov every exit a lab scores on a wallet rule (kind kol) carries that copy exit, next to OKTech's watch delay
(12 Nov, tests/test_lab_live_cost.py), and its name ends "copy50% watch1.3s". Not a launch, hunt, whale, burst, MIG,
momentum or event rule (no followed wallet), not a recipe's own exit (lab.RECIPES: a bot's exit, research only), and
the qualify lab's menu keeps the copy exit it always had. The forward paper run sells a rule frozen with it the same
way; a wallet rule frozen before keeps its id, its exit and its trades, and no longer stands for its rule.
"""

import json
from dataclasses import replace

import pytest

import snipetech.analysis.lab as lab_module
from snipetech import cli
from snipetech.analysis import paper
from snipetech.analysis.lab import (
    RECIPES,
    WATCH_SECS,
    Trigger,
    collect,
    copy_sold,
    exit_set,
    kind_exits,
    menu_exits,
    rank,
    recipe_exits,
    watched,
)
from snipetech.config import Config
from snipetech.oktech import list_copy_sniper, validate_exit
from snipetech.report import render_paper
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from .synth import Market, addr
from .test_lab import NOW
from .test_lab_hypotheses import HUNT_JSON, _tape_db

HUNT = Trigger("hunt", sol=2.0, window=2.0, dev_max=1.0)  # fires on the 2.5 SOL buy of every coin of the world
KOL = Trigger("kol", sol=2.0)  # a copy of that same buy: the buyer is a labeled KOL
HOLD60 = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=60, sell_on_dev_sell=False)
# the two as a lab scores them: the hunt's exit waits OKTech's watch delay, the wallet rule's also carries the copy exit
HUNT60 = replace(HOLD60, watch_secs=WATCH_SECS)
COPY60 = replace(HOLD60, watch_secs=WATCH_SECS, copy_exit_pct=0.5)
SELLS_AT = 12  # the slot, counted from the create, of the followed wallet's sale: 4 s after our buy lands in slot 2


def _leader_sell_world(store: Store, share: float = 0.5, slot: int = SELLS_AT, n: int = 24) -> None:
    """A coin a minute, 150 slots apart (0.4 s a slot): the creator's 0.5 SOL, a labeled KOL's 2.5 SOL a slot later,
    three buys of 0.3 SOL in the next slot (a hunt buy and a "+3@1" copy land behind them), six buys of 0.8 SOL in
    slots 5 to 10, then the KOL sells `share` of its coins in `slot`, and from slot 15 the six late buyers sell out.
    `slot` 12: after the buying, before the late buyers sell. 3: inside the 1.3 s after our buy, with the buying
    still to come. 1: right behind its own buy, before our buy has landed."""
    for i in range(n):
        m = Market(store, addr(f"ls-{i}"), addr(f"ls-dev-{i}"), NOW - 3000 + i * 60, 70_000_000 + i * 150)
        kol = addr(f"ls-kol-{i}")
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(kol, 2.5, 1)
        if share and slot == 1:
            m.sell(kol, share, slot)
        for k in range(3):
            m.buy(addr(f"ls-early-{i}-{k}"), 0.3, 2)
        if share and 2 < slot < 5:
            m.sell(kol, share, slot)
        for k in range(6):
            m.buy(addr(f"ls-late-{i}-{k}"), 0.8, 5 + k)
        if share and slot > 10:
            m.sell(kol, share, slot)
        for k in range(6):
            m.sell(addr(f"ls-late-{i}-{k}"), 1.0, 15 + k)
        m.commit()
        store.set_label(kol, f"ls-kol-{i}", "kol")
    store.set_coverage("tape", {"spans": [[NOW - 3100, NOW]]})


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _both(store: Store, cfg: Config | None = None, exits=(HOLD60,)) -> tuple[dict, list[dict], list[dict]]:
    """The hunt and the copy of the same buy at the same place (+3@1: behind the three buys of the next slot), under
    the same exits: (the lab, the hunt's rows, the wallet rule's rows), a row a coin."""
    data = collect(store, cfg or _cfg(), 0.2, 1, "+3", landings={"hunt": paper.HUNT_LANDING}, triggers=[HUNT, KOL],
                   exits=list(exits))  # fmt: skip
    hunt, copy = ([r for r in data["rows"] if r["trigger"] == ti] for ti in (0, 1))
    assert len(hunt) == len(copy) == 24 and {r["landing"] for r in data["rows"]} == {"+3@1"}
    assert [r["mint"] for r in hunt] == [r["mint"] for r in copy]
    return data, hunt, copy


def test_a_wallet_rule_is_sold_when_the_followed_wallet_sells_half_its_coins_and_a_hunt_is_not(monkeypatch):
    """The same buy, the same place, the same 60 s timer. The followed wallet sells half its coins 4 s after our buy,
    at the top; the late buyers sell out behind it. The hunt has no followed wallet: it holds to its timer and sells
    into the emptied coin. The copy sells everything a slot after the wallet's sale, before the late buyers do."""
    store = Store(":memory:")
    _leader_sell_world(store)
    data, hunt, copy = _both(store)
    assert data["exits"] == [HOLD60]  # the column, as the lab was handed it
    assert data["kind_exits"]["hunt"] == [HUNT60] and data["kind_exits"]["kol"] == [COPY60]  # what filled it
    for h, c in zip(hunt, copy):
        assert (c["first_sell_s"], c["first_sell_frac"]) == (4.0, 0.5)  # the wallet's sale, 4 s after its own buy
        assert c["pnl"][0] == pytest.approx(0.0246, abs=0.001)  # sold behind the wallet's sale, the buying still in
        assert h["pnl"][0] == pytest.approx(-0.0274, abs=0.001)  # held to 60 s: everyone who bought behind is out
    # the wallet rule as it was scored until 13 Nov: it held through the wallet's sale, like the hunt
    monkeypatch.setattr(lab_module, "COPY_EXIT_KINDS", frozenset())
    data, hunt_before, copy_before = _both(store)
    assert data["kind_exits"]["kol"] == [HUNT60]
    for h, c, was in zip(hunt_before, copy_before, hunt):
        assert c["pnl"][0] == h["pnl"][0] == was["pnl"][0]  # and the hunt's numbers are the ones it has today


def test_the_copy_exit_fires_at_the_threshold_the_snipers_are_sent_and_not_under_it():
    """[oktech] copy_exit_threshold_percent (50) is what every copy sniper builder sends as sellThresholdPercent. A
    sale of 40 % of the wallet's coins is under it: the copy holds on, as the live sniper would. With the setting at
    30 the same sale sells the copy, and the exit's name says which threshold scored it."""
    store = Store(":memory:")
    _leader_sell_world(store, share=0.4)
    data, hunt, copy = _both(store)
    assert all(c["pnl"][0] == h["pnl"][0] < -0.02 for h, c in zip(hunt, copy))  # held to the timer, both
    cfg = _cfg()
    cfg.oktech.copy_exit_threshold_percent = 30.0
    data, hunt, copy = _both(store, cfg)
    assert data["kind_exits"]["kol"] == [replace(COPY60, copy_exit_pct=0.3)]
    assert all(c["pnl"][0] > 0.02 > -0.02 > h["pnl"][0] for h, c in zip(hunt, copy))
    (row,) = [r for r in rank(data, cfg, top=10, min_train=5) if r["trigger_spec"]["kind"] == "kol"]
    assert row["exit"] == "hold60s copy30% watch1.3s"


def test_the_copy_exit_never_sells_before_our_buy_landed_nor_before_oktech_watches():
    """What the simulator does (sim/exits.py, as the qualify lab's exits have been scored since 6 Nov). A sale the
    wallet made before our buy landed is no sale of a position we hold: never followed. OKTech starts watching a
    position 1.3 s after its buy: a sale inside that time, with other trades between it and the start of the watch,
    is not followed either, and the position stays with its other rules (here the timer). Whether OKTech follows
    such a sale once it watches has not been measured on a live fill."""
    for slot in (1, 3):  # right behind the wallet's own buy (our buy lands in slot 2); 0.4 s after our buy
        store = Store(":memory:")
        _leader_sell_world(store, slot=slot)
        _data, hunt, copy = _both(store)
        assert all(c["pnl"][0] == h["pnl"][0] for h, c in zip(hunt, copy)), slot  # held to the timer, as the hunt


def test_the_exit_of_a_wallet_rule_says_it_carries_the_copy_exit_and_no_other_rules_does():
    """The label ends "copy50% watch1.3s", so a row from before 13 Nov ("... watch1.3s") reads as another exit."""
    store = Store(":memory:")
    _leader_sell_world(store)
    triggers = [Trigger("launch"), HUNT, Trigger("whale", sol=2.0), Trigger("burst", 3, 1.0, 2.0), KOL]
    data = collect(store, _cfg(), 0.2, 0, "+6", landings=paper.LAUNCH_LANDINGS, triggers=triggers, exits=[HOLD60])
    ranked = {r["trigger_spec"]["kind"]: r for r in rank(data, _cfg(), top=10, min_train=5)}
    assert set(ranked) == {"launch", "hunt", "whale", "burst", "kol"}
    assert ranked["kol"]["exit"] == "hold60s copy50% watch1.3s"
    assert ranked["kol"]["exit_params"] == HOLD60.to_dict() | {"watch_secs": WATCH_SECS, "copy_exit_pct": 0.5}
    for kind in ("launch", "hunt", "whale", "burst"):  # no followed wallet: the exit they had
        assert ranked[kind]["exit"] == "hold60s watch1.3s", kind
        assert ranked[kind]["exit_params"] == HUNT60.to_dict(), kind
    assert "momentum" not in data["kind_exits"] and "event" not in data["kind_exits"]  # scored as handed over


def test_every_exit_of_the_search_carries_it_on_a_wallet_rule_and_a_recipes_own_exit_does_not():
    """The search set (`exit_set`: the grid's exits and the ladders, what `lab --trigger` and the strategy search
    score) on a wallet rule: every exit with the copy exit at the snipers' threshold and the watch delay. A recipe's
    own exit is a bot's (lab.RECIPES; research only, no sniper of ours is sent it): it keeps the watch delay alone,
    in a recipe's lab (`recipe_exits`) and in a recipe's freeze (the exit alone)."""
    search = exit_set()
    by_kind = kind_exits(search)
    assert by_kind["kol"] == [replace(ex, watch_secs=WATCH_SECS, copy_exit_pct=0.5) for ex in search]
    assert all(ex.label().endswith("copy50% watch1.3s") for ex in by_kind["kol"][:30])  # (a ladder's name goes on)
    for kind in ("launch", "hunt", "whale", "burst"):
        assert by_kind[kind] == [watched(ex) for ex in search], kind
    assert set(by_kind) == {"launch", "hunt", "whale", "burst", "kol"}
    for rec in RECIPES.values():
        scored = dict(zip(recipe_exits(rec), kind_exits(recipe_exits(rec))["kol"]))
        assert scored[rec.exit] == watched(rec.exit) and scored[rec.exit].copy_exit_pct == 0, rec.name
        assert all(ex.copy_exit_pct == 0.5 for handed, ex in scored.items() if handed != rec.exit), rec.name
        assert kind_exits([rec.exit])["kol"] == [watched(rec.exit)], rec.name  # a recipe's freeze


def test_the_qualify_menu_keeps_the_copy_exit_it_has():
    """The qualify lab hands the menu's exits over as a copy sniper runs them (`menu_exits`: the copy exit and the
    watch delay already). Nothing is applied twice: each is scored as it was, at the threshold it was made with."""
    menu = list(menu_exits().values())
    assert all(ex.copy_exit_pct == 0.5 and ex.watch_secs == WATCH_SECS for ex in menu)
    assert kind_exits(menu)["kol"] == menu
    cfg = Config()
    cfg.oktech.copy_exit_threshold_percent = 30.0
    assert kind_exits(menu, cfg)["kol"] == menu  # an exit that names its own threshold keeps it
    assert copy_sold(HUNT60, 0.5) == COPY60 and copy_sold(COPY60, 0.3) == COPY60
    assert copy_sold(HUNT60, 0.0) == HUNT60  # no threshold set: no copy exit to score


def test_the_lab_command_says_which_copy_exit_scored_a_wallet_rule(tmp_path, capsys):
    """`lab --trigger <a wallet rule> --json`, the agents' command: the exit's name carries the copy exit, and
    `copy_exit` says the threshold, which rules carry it and what the number must not be read as. A lab without a
    wallet rule has no such field."""
    db = _tape_db(tmp_path, kols=True)
    assert cli.main(["--db", str(db), "lab", "--trigger", '{"kind": "kol", "sol": 1}', "--trigger", HUNT_JSON,
                     "--hours", "1", "--json"]) == 0  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    by_kind = {s["trigger_spec"]["kind"]: s for s in out["strategies"]}
    assert set(by_kind) == {"kol", "hunt"}
    assert (
        by_kind["kol"]["exit"].endswith("copy50% watch1.3s") and by_kind["kol"]["exit_params"]["copy_exit_pct"] == 0.5
    )
    assert "copy" not in by_kind["hunt"]["exit"] and by_kind["hunt"]["exit_params"]["copy_exit_pct"] == 0
    assert out["copy_exit"]["threshold_percent"] == 50.0 and out["copy_exit"]["kinds"] == ["kol"]
    assert "before 13 Nov" in out["copy_exit"]["note"] and "not measured" in out["copy_exit"]["note"]
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", "--json"]) == 0
    assert "copy_exit" not in json.loads(capsys.readouterr().out)


# -- the forward paper test ---------------------------------------------------------------------------------------


def _frozen(ex: ExitParams, pos: str, **more) -> dict:
    """A wallet rule frozen before the world's first coin, at the copy landing, with this exit."""
    sid = paper.strategy_id(KOL.__dict__, ex.to_dict(), pos, 0, 0.2)
    return {"id": sid, "label": f"{KOL.label()} | {ex.label()}", "trigger": KOL.__dict__, "exit": ex.to_dict(),
            "position": pos, "latency": 0, "buy_sol": 0.2, "per_tx_sol": 0.0035, "from_ts": NOW - 3100,
            "lab": {"mean": 0.01, "ev_lower": 0.005}} | more  # fmt: skip


def test_the_forward_run_sells_a_rule_frozen_with_the_copy_exit_when_the_wallet_sells():
    """Two records of the one wallet rule: frozen on 12 Nov with the watch delay alone, and frozen now with the copy
    exit too. The run scores each with the exit it was frozen with: the old one holds to its timer through the
    wallet's sale, as it always did, and its stored trades are never rewritten; the new one is sold a slot after the
    wallet's sale, and never sooner than OKTech watches."""
    store = Store(":memory:")
    _leader_sell_world(store)
    cfg = _cfg()
    pos = paper.copy_landing(store, cfg, NOW)
    old, new = _frozen(HUNT60, pos), _frozen(COPY60, pos)
    assert old["id"] != new["id"]
    assert old["id"] == paper.strategy_id(KOL.__dict__, HOLD60.to_dict() | {"watch_secs": WATCH_SECS}, pos, 0, 0.2)
    store.set_coverage("paper_strategies", {"strategies": [old, new]})
    paper.run(store, cfg, now=NOW)
    held, sold = store.paper_trades(old["id"]), store.paper_trades(new["id"])
    assert len(held) == len(sold) == 24
    for h, s in zip(held, sold):
        assert h["mint"] == s["mint"] and h["entry_ts"] == s["entry_ts"]
        # the timer sells into the pool as the coin's last trade left it, 8 s after the buy (hold_secs is the time
        # of the trade a sale is priced behind, on the tape's whole-second clock)
        assert h["reason"] == "time" and h["data"]["hold_secs"] == 8
        # the wallet sells 11 slots (4.4 s) after its own buy, which is where this copy is priced: long after OKTech
        # watches. The copy is priced behind the wallet's sale and ahead of every later trade
        assert s["reason"] == "copy_exit" and s["data"]["hold_secs"] == 4 > WATCH_SECS
        assert s["pnl"] > 0.02 > -0.01 > h["pnl"]
    stored = {x["id"]: x for x in store.coverage("paper_strategies")["strategies"]}
    assert stored[old["id"]]["exit"] == HUNT60.to_dict() and stored[new["id"]]["exit"] == COPY60.to_dict()


def test_a_wallet_rule_frozen_without_the_copy_exit_does_not_stand_for_its_rule():
    """As a rule frozen without the watch delay (7 and 12 Nov): it keeps its id and its trades, and the same rule
    frozen again, with the copy exit, is the rule's record. A recipe's rule is its own exit (a bot's), which never
    had the copy exit: it stands. A hunt has no followed wallet."""
    kol = {"kind": "kol", "group": "big", "sol": 1.0}
    assert not paper._sells_as_live({"trigger": kol, "exit": HOLD60.to_dict()})
    assert not paper._sells_as_live({"trigger": kol, "exit": HUNT60.to_dict()})  # the watch delay alone: 12 Nov
    assert paper._sells_as_live({"trigger": kol, "exit": COPY60.to_dict()})
    assert not paper._sells_as_live({"trigger": kol, "exit": replace(HOLD60, copy_exit_pct=0.5).to_dict()})  # no watch
    assert paper._sells_as_live({"trigger": HUNT.__dict__, "exit": HUNT60.to_dict()})
    assert paper._sells_as_live({"trigger": {"kind": "momentum"}, "exit": HOLD60.to_dict()})
    for rec in RECIPES.values():
        frozen = {"trigger": rec.triggers[0].__dict__, "exit": watched(rec.exit).to_dict(), "recipe": rec.name}
        assert paper._sells_as_live(frozen), rec.name
    why = paper.not_as_live(_frozen(HUNT60, "+6"))
    assert "copy exit" in why and "frozen again" in why
    assert "watch delay" in paper.not_as_live({"trigger": HUNT.__dict__, "exit": HOLD60.to_dict()})
    assert paper.not_as_live(_frozen(COPY60, "+6")) is None


def test_the_rule_is_frozen_again_under_the_copy_exit_and_the_freeze_and_the_report_say_why():
    """The wallet rule is under test with the exit of 12 Nov (no copy exit). The lab now picks the same timer with
    the copy exit: until 13 Nov the freeze would have said "already in the paper test, one forward record a rule".
    Now the old record no longer holds the rule's place: the rule is frozen again as a strategy of its own, the old
    one keeps its id and runs on, and the freeze and the report say what its trades are not."""
    store = Store(":memory:")
    _leader_sell_world(store)
    cfg = _cfg()
    pos = paper.copy_landing(store, cfg, NOW)
    old = _frozen(HUNT60, pos)
    store.set_coverage("paper_strategies", {"strategies": [old]})
    data = collect(store, cfg, 0.2, 0, pos, landings=paper.LAUNCH_LANDINGS, triggers=[KOL], exits=[HOLD60])
    rep = paper.freeze(store, cfg, top=4, position=pos, latency=0, buy_sol=0.2, now=NOW, data=data, only_live=True)
    (new,) = rep["frozen"]
    assert new["exit"] == COPY60.to_dict() and new["label"].endswith("| hold60s copy50% watch1.3s")
    assert new["id"] == paper.strategy_id(KOL.__dict__, COPY60.to_dict(), pos, 0, 0.2) != old["id"]
    assert [s["id"] for s in rep["kept"]] == [old["id"]] and rep["skipped"] == []
    (note,) = rep["not_as_live"]
    assert note["id"] == old["id"] and "copy exit" in note["why"]
    rows = {r["id"]: r for r in paper.report(store, cfg, now=NOW)["strategies"]}
    assert "copy exit" in rows[old["id"]]["exit_note"] and "exit_note" not in rows[new["id"]]
    # the owner's text says it too, under the freeze and under the report's table
    text = render_paper("freeze", rep)
    assert f"no longer the record of its rule: {old['id']}: scored without the copy exit" in text
    assert f"{old['id']}: scored without the copy exit" in render_paper("report", paper.report(store, cfg, now=NOW))
    # frozen once more: the new record holds the rule's place, the old one is still kept and still said
    again = paper.freeze(store, cfg, top=4, position=pos, latency=0, buy_sol=0.2, now=NOW, data=data, only_live=True)
    assert again["frozen"] == [] and [s["id"] for s in again["kept"]] == [old["id"], new["id"]]
    assert [n["id"] for n in again["not_as_live"]] == [old["id"]]


# -- the sniper ---------------------------------------------------------------------------------------------------


def test_an_exit_scored_with_the_copy_exit_is_the_one_a_copy_sniper_is_sent():
    """`oktech.validate_exit` holds an exit in the lab's form to the copy exit copy snipers send. An exit a lab now
    scores on a wallet rule passes that check; the same exit as a lab scored it until 13 Nov (copy exit off) is
    refused, which is what the audit found. The sniper builder sends the copy exit once, from the config: the scored
    field adds nothing to the sniper, and the watch delay is how OKTech sells, not a setting."""
    cfg = Config()
    lab_exit = ExitParams(take_profit=0, stop_loss=0.3, max_hold_secs=300, sell_on_dev_sell=False, stale_secs=1.0)
    (scored,) = kind_exits([lab_exit], cfg)["kol"]
    assert validate_exit(scored.to_dict(), cfg) == []
    (problem,) = validate_exit(watched(lab_exit).to_dict(), cfg)
    assert "scored with the copy exit at 0%, but snipers send 50%" in problem
    cfg.oktech.copy_exit_threshold_percent = 30.0  # the setting moved after the lab: refused, never sent as another
    assert any("copy exit at 50%" in p for p in validate_exit(scored.to_dict(), cfg))
    cfg = Config()
    leader = addr("leader")
    plain = list_copy_sniper(leader, "x", 1.0, 0.2, exit_params=lab_exit.to_dict(), cfg=cfg)
    sent = list_copy_sniper(leader, "x", 1.0, 0.2, exit_params=scored.to_dict(), cfg=cfg)
    assert sent.config == plain.config
    assert sent.config["ctbotCopyExit"] == {"enabled": True, "sellThresholdPercent": 50.0, "sellPercent": 100,
                                            "triggerWalletOnly": True, "delayMs": 0}  # fmt: skip
