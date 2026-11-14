"""A launch rule is scored on every coin the live sniper would buy (14 Nov 2026).

A "serial crew" is one metadata file on 3 or more launches by 2 or more creators (store.crew_uris). From 9 to 14 Nov
the lab and the forward paper test passed over a crew's coins for every launch-type rule (launch, hunt, whale, burst)
unless told otherwise, while the live OKTech sniper bought them: OKTech cannot tell such a coin from any other launch.
Measured on the server on 14 Nov on one hunt rule: 633 of the 1,715 coins it fired on in a week (37 %) were crews'
coins, and they lost. A paper result must not be kinder than the live place (CLAUDE.md rule 2).

Now `Trigger.crew` "" is every coin, "skip" passes over the crews' coins and "only" keeps them alone; "skip" and
"only" are research only. The rules frozen before the change were scored without crews' coins: they keep their id,
their trades and their place, say crew "skip" from now on, and never go live."""

import gzip
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from snipetech import report as R
from snipetech.analysis import paper
from snipetech.analysis.lab import (
    CREW_CHOICES,
    CREW_NOTE,
    Trigger,
    collect,
    crew_fields,
    crew_skips,
    crew_why,
    parse_trigger,
    rank,
    watched,
)
from snipetech.config import Config
from snipetech.operator.jobs import OperatorJobs
from snipetech.sim.exits import ExitParams
from snipetech.store import Store

from .synth import Market, addr

NOW = int(time.time())
START = NOW - 6000  # the first coin's launch; one coin a minute from there
HUNT = Trigger("hunt", sol=2.0, window=2.0, dev_max=1.0)  # fires on every coin of the world at its 2.5 SOL buy
SKIP, ONLY = replace(HUNT, crew="skip"), replace(HUNT, crew="only")
HUNT_JSON = '{"kind": "hunt", "sol": 2, "window": 2, "dev_max": 1}'
SKIP_JSON = '{"kind": "hunt", "sol": 2, "window": 2, "dev_max": 1, "crew": "skip"}'
ONLY_JSON = '{"kind": "hunt", "sol": 2, "window": 2, "dev_max": 1, "crew": "only"}'
TIMER = ExitParams(take_profit=0, stop_loss=0, max_hold_secs=10, sell_on_dev_sell=False)  # sells 10 s after the buy
EXIT = watched(TIMER).to_dict()  # as the lab stores a launch-type rule's exit: with OKTech's watch delay
# The ids the code before this change (commit f70fc7e) gave these rules at the hunt landing, 0.2 SOL a buy: written
# down from that code, so the test fails if a stored rule's id ever moves.
OLD_HUNT_ID, OLD_UNWATCHED_ID, OLD_ONLY_ID, OLD_BOTS_ID = "d4cbfb7ae1", "65b10e527f", "f7cb4dd1e8", "705c545dfb"
OLD_KOL_ID, OLD_MIG_ID = "6369c1a261", "b3e362f611"
COPY_EXIT = EXIT | {"copy_exit_pct": 0.5}  # a wallet rule's exit: with the copy exit every copy sniper is sent
URI = "ipfs://one-file-many-coins"
FIXTURE = Path(__file__).parent / "fixtures" / "paper_strategies_14nov.json.gz"


def world(store: Store, n: int, crew: tuple[int, ...], first: int = 0) -> list[str]:
    """`n` launches a minute apart, each crossing the 2 SOL hunt threshold with a 2.5 SOL buy five blocks in, bought
    up for some seconds and sold off. The coins numbered in `crew` share one metadata file and come from two
    creators taking turns: a serial crew. Returns the crew's coins. `first`: the coins before it are there already
    (a tape that grew)."""
    for k in range(first, n):
        m = Market(store, addr(f"cal-{k}"), addr(f"cal-dev-{k}"), START + k * 60, 40_000_000 + k * 1000)
        m.buy(m.deployer, 0.5, 0, sig=m.create_sig)
        m.buy(addr(f"cal-small-{k}"), 0.2, 2)
        m.buy(addr(f"cal-whale-{k}"), 2.5, 5)  # the crossing trade
        m.buy(addr(f"cal-tail-{k}"), 0.3, 5)
        for j in range(9):
            m.buy(addr(f"cal-rush-{k}-{j}"), 0.5, 6)
        for j in range(6):
            m.buy(addr(f"cal-fan-{k}-{j}"), 0.4, 10 + 3 * j)
        for j in range(6):
            m.sell(addr(f"cal-fan-{k}-{j}"), 1.0, 60 + j * 5)
        m.commit()
    mints = [addr(f"cal-{k}") for k in crew]
    for j, mint in enumerate(mints):
        store._exec("UPDATE launches SET uri=?, creator=? WHERE mint=?", (URI, addr(f"crew-dev-{j % 2}"), mint))
    store.set_coverage("tape", {"spans": [[START - 100, NOW]]})
    return mints


def _cfg() -> Config:
    cfg = Config()
    cfg.verdict.bootstrap_samples = 200
    return cfg


def _lab(store: Store, triggers) -> dict:
    """The lab on the hypotheses `triggers`, each where a hunt buy lands, on the one timer exit."""
    return collect(store, _cfg(), 0.2, 0, "+6", 0.0045, landings=paper.LAUNCH_LANDINGS, triggers=list(triggers),
                   exits=[TIMER], kols={})  # fmt: skip


def _coins(data: dict, ti: int) -> set[str]:
    return {r["mint"] for r in data["rows"] if r["trigger"] == ti}


def _stored(trig: Trigger, sid: str, *, every_coin: bool, from_ts: int = START - 50, exit_params: dict | None = None,
            landing=(1, "+3"), **more) -> dict:  # fmt: skip
    """A frozen rule as `paper freeze` stores it. `every_coin`: frozen by the code since 14 Nov, which scores a
    crew's coins for crew "" and says so on the record; False: frozen before, when "" passed over them."""
    s = {
        "id": sid,
        "label": f"{trig.label()} | timer",
        "trigger": dict(trig.__dict__),
        "exit": dict(exit_params or EXIT),
        "position": landing[1],
        "latency": landing[0],
        "buy_sol": 0.2,
        "per_tx_sol": 0.0045,
        "from_ts": from_ts,
        "lab": {"mean": 0.01, "ev_lower": 0.004, "landing": "+3@1"},
    } | more
    if every_coin:
        s[paper.EVERY_COIN] = True
    return s


def _put(store: Store, strategies: list[dict], superseded: list[dict] | None = None) -> None:
    store.set_coverage("paper_strategies", {"strategies": strategies, "superseded": superseded or []})


# -- the knob ----------------------------------------------------------------------------------------------------------


def test_crew_has_three_values_and_the_default_is_every_coin():
    """ "" is every coin, a serial crew's too (what a live sniper buys); "skip" and "only" are the research choices.
    Each is taken on a launch-type trigger and on no other, and a value that is none of them is refused in words that
    name all three."""
    assert CREW_CHOICES == ("", "skip", "only") and Trigger("hunt").crew == ""
    for kind in ('"launch"', '"hunt", "sol": 4, "window": 1', '"whale", "sol": 2', '"burst", "buyers": 3, "sol": 1'):
        for crew in CREW_CHOICES:
            assert parse_trigger(f'{{"kind": {kind}, "crew": "{crew}"}}').crew == crew
    with pytest.raises(ValueError, match="crew") as exc:
        parse_trigger('{"kind": "launch", "crew": "maybe"}')
    assert all(word in str(exc.value) for word in ('""', '"skip"', '"only"', "every coin", "research only"))
    for crew in ("skip", "only"):
        with pytest.raises(ValueError, match="crew"):
            parse_trigger(f'{{"kind": "kol", "sol": 1, "crew": "{crew}"}}')
        with pytest.raises(ValueError, match="crew"):
            parse_trigger(f'{{"kind": "mig", "window": 10, "crew": "{crew}"}}')


def test_the_label_names_a_rule_scored_without_crews_coins():
    """A rule's name says which coins it was scored on whenever that is not every coin."""
    assert "serial crews' coins" not in HUNT.label() and "serial-crew" not in HUNT.label()
    assert "without serial crews' coins" in SKIP.label()
    assert "serial-crew coins only" in ONLY.label()
    assert len({HUNT.label(), SKIP.label(), ONLY.label()}) == 3


def test_skip_and_only_are_research_only_and_say_why_in_their_own_words():
    """`crew_why`: None for a rule scored on every coin, else why no sniper runs it as scored; from a Trigger or from
    a stored rule's trigger fields. A lab's ranked row carries it as `research_only` / `research_only_why`."""
    assert crew_why(HUNT) is None and crew_why(HUNT.__dict__) is None and crew_fields(HUNT) == {}
    skip, only = crew_why(SKIP), crew_why(ONLY)
    assert "without serial crews' coins" in skip and "OKTech cannot tell" in skip and "37 %" in skip
    assert "serial crews' coins alone" in only and skip != only
    assert crew_why(SKIP.__dict__) == skip and crew_why(ONLY.__dict__) == only
    assert crew_fields(SKIP) == {"research_only": True, "research_only_why": skip}
    assert crew_fields(ONLY) == {"research_only": True, "research_only_why": only}
    for other in (Trigger("kol", sol=1.0), Trigger("mig", window=10.0), Trigger("momentum")):
        assert crew_why(other) is None and crew_fields(other) == {}


def test_a_hunt_on_a_wallet_group_was_always_scored_on_every_coin_and_takes_no_skip():
    """The `bots` exception is what it was: a hunt on a wallet group buys what the group's wallets bought, a serial
    crew's coin or not, as OKTech's known-bot gate does. So "skip" would leave nothing out of it: refused, in words,
    rather than marking a rule research only that is scored as the sniper buys. "only" still limits it."""
    store = Store(":memory:")
    (mint,) = world(store, 1, (0,))
    launch, crews = store.launch(mint), {URI}
    group = replace(HUNT, bots="bundler")
    assert not crew_skips(group, launch, crews) and crew_why(group) is None
    assert not crew_skips(replace(group, crew="only"), launch, crews)
    with pytest.raises(ValueError, match="bots"):
        parse_trigger('{"kind": "hunt", "sol": 2, "window": 2, "bots": "bundler", "crew": "skip"}')
    assert parse_trigger('{"kind": "hunt", "sol": 2, "window": 2, "bots": "bundler", "crew": "only"}').crew == "only"
    store.close()


def test_which_coins_each_choice_passes_over():
    """The one function the lab and the forward run both ask (`crew_skips`): the default passes over nothing, "skip"
    over a crew's coin, "only" over every other coin; a rule that is no launch-type rule passes over nothing."""
    store = Store(":memory:")
    (crew_coin,) = world(store, 2, (0,))
    ours, other, crews = store.launch(crew_coin), store.launch(addr("cal-1")), {URI}
    assert not crew_skips(HUNT, ours, crews) and not crew_skips(HUNT, other, crews)
    assert crew_skips(SKIP, ours, crews) and not crew_skips(SKIP, other, crews)
    assert not crew_skips(ONLY, ours, crews) and crew_skips(ONLY, other, crews)
    for kind in ("launch", "whale", "burst"):
        assert not crew_skips(Trigger(kind), ours, crews) and crew_skips(Trigger(kind, crew="skip"), ours, crews)
    assert not crew_skips(Trigger("kol", sol=1.0), ours, crews)
    assert not crew_skips(SKIP, None, crews)  # a coin whose launch the tape does not hold is nobody's crew coin
    store.close()


# -- the lab and the forward run score the same coins ------------------------------------------------------------------


def test_the_lab_and_the_forward_run_score_the_same_coins_for_each_choice():
    """A crew of three launches on one file by two creators, each crossing the hunt's threshold, among 30 ordinary
    launches. The default rule is scored on all 33 (the coins a live sniper buys), "skip" on the 30 ordinary ones,
    "only" on the crew's 3: in the lab, and in the forward run of the same three rules, coin for coin."""
    store = Store(":memory:")
    crew = set(world(store, 33, (5, 15, 25)))
    assert store.crew_uris(START - 100, NOW) == {URI}
    everyone = {addr(f"cal-{k}") for k in range(33)}
    data = _lab(store, [HUNT, SKIP, ONLY])
    lab = [_coins(data, ti) for ti in range(3)]
    assert lab == [everyone, everyone - crew, crew]
    rules = [_stored(HUNT, "aaaaaaaaaa", every_coin=True), _stored(SKIP, "bbbbbbbbbb", every_coin=False),
             _stored(ONLY, "cccccccccc", every_coin=False)]  # fmt: skip
    _put(store, rules)
    out = paper.run(store, _cfg(), now=NOW)
    forward = [{t["mint"] for t in store.paper_trades(s["id"])} for s in rules]
    assert forward == lab and out["new_trades"] == 33 + 30 + 3
    assert [len(x) for x in forward] == [33, 30, 3]
    store.close()


def test_every_ranked_launch_rule_says_how_many_of_its_moments_are_crews_coins():
    """The lab's `crew` block, per rule: of the default rule's 75 moments 25 are serial crews' coins (a third); the
    "skip" rule says how many such coins it fired on and left out; the "only" rule says it kept those alone. Each
    says the crews are known with hindsight (the lab read its whole window; a live sniper knows nothing). A wallet
    rule has no such block."""
    store = Store(":memory:")
    world(store, 75, tuple(range(0, 75, 3)))
    for k in range(75):
        store.set_label(addr(f"cal-whale-{k}"), f"whale-{k}", "kol")
    data = collect(store, _cfg(), 0.2, 0, "+6", 0.0045, landings=paper.LAUNCH_LANDINGS,
                   triggers=[HUNT, SKIP, ONLY, Trigger("kol", sol=2.0)], exits=[TIMER])  # fmt: skip
    rows = {r["trigger"]: r for r in rank(data, _cfg(), top=10)}
    every, skip, only = rows[HUNT.label()], rows[SKIP.label()], rows[ONLY.label()]
    assert (every["moments"], skip["moments"], only["moments"]) == (75, 50, 25)
    assert every["crew"] == {"moments": 25, "of": 75, "share": pytest.approx(1 / 3), "hindsight": True,
                             "line": every["crew"]["line"]}  # fmt: skip
    assert "25 of its 75 moments (33 %) are serial crews' coins" in every["crew"]["line"]
    assert skip["crew"]["moments"] == 0 and skip["crew"]["share"] == 0 and skip["crew"]["hindsight"] is True
    assert skip["crew"]["left_out"] == 25 and skip["crew"]["left_out_share"] == pytest.approx(1 / 3)
    assert skip["crew"]["fired"] == 75 and "passed over 25 of the 75 coins it fired on" in skip["crew"]["line"]
    assert only["crew"]["moments"] == 25 and only["crew"]["share"] == 1 and "alone" in only["crew"]["line"]
    assert "research_only" not in every
    assert skip["research_only"] is True and skip["research_only_why"] == crew_why(SKIP)
    assert only["research_only"] is True and only["research_only_why"] == crew_why(ONLY)
    kol = next(r for r in rows.values() if r["trigger_spec"]["kind"] == "kol")
    assert "crew" not in kol and "research_only" not in kol
    # the lab's own block: one entry a launch-type trigger, by its name, and the sentence about hindsight
    block = data["crew"]
    assert block["hindsight"] is True and block["note"] == CREW_NOTE and block["files"] == 1
    assert set(block["triggers"]) == {HUNT.label(), SKIP.label(), ONLY.label()}
    assert block["triggers"][HUNT.label()] == every["crew"]
    # the rows of the "skip" rule's left-out coins are no scored moments and no PumpSwap gap
    assert "amm_tape" not in data and all("skipped" not in r for r in data["rows"])
    store.close()


def test_the_lab_command_prints_the_crew_block_as_json_and_as_a_line(tmp_path, capsys):
    """`snipetech lab --trigger ...`: every ranked strategy and every hypothesis carries `crew`, the result carries
    the lab's block, and the text output says it in one line a rule, with the word hindsight."""
    from snipetech import cli

    db = tmp_path / "data" / "snipetech.db"
    db.parent.mkdir(parents=True)
    st = Store(db)
    world(st, 75, tuple(range(0, 75, 3)))
    st.close()
    flags = ["--db", str(db), "lab", "--trigger", HUNT_JSON, "--trigger", SKIP_JSON, "--trigger", ONLY_JSON]
    assert cli.main([*flags, "--json"]) == 0
    res = json.loads(capsys.readouterr().out)
    by_rule = {s["trigger"]: s for s in res["strategies"]}
    assert by_rule[HUNT.label()]["crew"]["moments"] == 25 and by_rule[HUNT.label()]["crew"]["of"] == 75
    assert by_rule[SKIP.label()]["crew"]["left_out"] == 25 and by_rule[SKIP.label()]["research_only"] is True
    hyp = {t["trigger"]: t for t in res["triggers"]}
    assert hyp[HUNT.label()]["crew"]["share"] == pytest.approx(1 / 3) and "research_only" not in hyp[HUNT.label()]
    assert hyp[SKIP.label()]["research_only"] is True and hyp[ONLY.label()]["research_only"] is True
    assert res["crew"]["hindsight"] is True and set(res["crew"]["triggers"]) == set(by_rule)
    assert cli.main(flags) == 0
    text = capsys.readouterr().out
    assert "Serial crews' coins" in text and "hindsight" in text
    assert "25 of its 75 moments (33 %) are serial crews' coins" in text
    assert "research only" in text.lower()


# -- no sniper for a rule that was not scored on every coin ------------------------------------------------------------


def test_the_sniper_builder_refuses_skip_and_only_and_builds_the_default():
    """OKTech cannot tell a serial crew's coin: a rule scored without them ("skip") would buy the coins its record
    leaves out, one scored on them alone ("only") would buy every launch. Neither is built. The default is."""
    from snipetech.oktech import strategy_sniper

    def strat(trig: Trigger, **more) -> dict:
        return {"id": "x", "label": "hunt", "buy_sol": 0.2, "exit": {"stop_loss": 0.2, "max_hold_secs": 5},
                "trigger": dict(trig.__dict__)} | more  # fmt: skip

    built = strategy_sniper(strat(HUNT, every_coin=True))  # a stored record (it has an id), frozen since 14 Nov
    assert built.config["mode"] == "hunt" and built.config["hunt"]["thresholds"]["minTotalSol"] == 2
    with pytest.raises(ValueError, match="without serial crews' coins") as exc:
        strategy_sniper(strat(SKIP))
    assert "OKTech cannot tell" in str(exc.value) and "37 %" in str(exc.value) and "research only" in str(exc.value)
    with pytest.raises(ValueError, match="crew only"):
        strategy_sniper(strat(ONLY))
    # a rule frozen with its lab's own count says its own share, not the general one
    own = strat(SKIP, lab={"crew": {"moments": 0, "of": 50, "left_out": 25, "fired": 75, "left_out_share": 1 / 3}})
    with pytest.raises(ValueError, match="25 of the 75 coins it fired on") as exc:
        strategy_sniper(own)
    assert "33 %" in str(exc.value)
    # a stored record as the code before 14 Nov wrote it (crew "", no mark of a rule scored on every coin), handed
    # over as it is stored: read as it was scored, and refused; the same record frozen since is built
    with pytest.raises(ValueError, match="without serial crews' coins"):
        strategy_sniper(_stored(HUNT, OLD_HUNT_ID, every_coin=False))
    assert strategy_sniper(_stored(HUNT, "aaaaaaaaaa", every_coin=True)).config["mode"] == "hunt"
    assert (
        strategy_sniper(
            _stored(replace(HUNT, bots="bundler"), OLD_BOTS_ID, every_coin=False), bot_wallets=[addr("a bundler")]
        ).config["hunt"]["thresholds"]["requireKnownBot"]
        is True
    )
    for kind in ("whale", "burst", "launch"):  # every launch-type kind, whichever way it is sent
        with pytest.raises(ValueError, match="serial crews"):
            strategy_sniper(strat(Trigger(kind, sol=2.0, buyers=3, window=2.0, crew="skip")))


# -- the agents' jobs --------------------------------------------------------------------------------------------------


def test_the_agents_job_check_takes_crew_skip_and_refuses_a_value_that_is_none():
    """The agents pass triggers as JSON; the job check reads them with the lab's own parser before a job starts. A
    repeated hypothesis of before 14 Nov is asked for as "crew": "skip": accepted, for a lab and for a freeze."""
    for argv in (["lab", "--trigger", SKIP_JSON], ["lab", f"--trigger={ONLY_JSON}"], ["lab", "--trigger", HUNT_JSON],
                 ["paper", "freeze", "--trigger", SKIP_JSON]):  # fmt: skip
        OperatorJobs._check_triggers(argv)
    with pytest.raises(PermissionError, match="crew"):
        OperatorJobs._check_triggers(["lab", "--trigger", '{"kind": "hunt", "sol": 2, "window": 2, "crew": "all"}'])


# -- the ids -----------------------------------------------------------------------------------------------------------


def test_skip_has_the_id_the_default_had_and_the_default_has_a_new_one():
    """How ids are kept: a rule's id is a hash of its trigger. "skip" is hashed as "" was hashed before 14 Nov (left
    out of the hash), so a stored rule that now says "skip" has the id it always had. The default, every coin, is a
    different rule with a different id. "only", a hunt on a wallet group (never passed over a crew's coin) and every
    rule that is no launch-type rule hash as they always did."""
    at = (EXIT, "+3", 1, 0.2)
    assert paper.strategy_id(SKIP.__dict__, *at) == OLD_HUNT_ID
    new = paper.strategy_id(HUNT.__dict__, *at)
    assert new != OLD_HUNT_ID and len(new) == 10
    assert paper.strategy_id(ONLY.__dict__, *at) == OLD_ONLY_ID
    assert paper.strategy_id(SKIP.__dict__, TIMER.to_dict(), "+3", 1, 0.2) == OLD_UNWATCHED_ID
    assert paper.strategy_id(replace(HUNT, bots="bundler").__dict__, *at) == OLD_BOTS_ID
    assert paper.strategy_id(Trigger("kol", sol=1.0).__dict__, COPY_EXIT, "+6", 0, 0.2) == OLD_KOL_ID
    assert paper.strategy_id(Trigger("mig", window=10.0).__dict__, *at) == OLD_MIG_ID
    assert len({new, OLD_HUNT_ID, OLD_ONLY_ID}) == 3
    # a rule's key (one forward record a rule) tells the three apart too
    keys = {paper.rule_key(t.__dict__, "+3", 1, 0.2) for t in (HUNT, SKIP, ONLY)}
    assert len(keys) == 3


def test_a_stored_rule_frozen_before_reads_as_skip_with_its_own_id_before_any_migration():
    """`paper.as_frozen`: a stored launch-type rule that says crew "" and does not carry the mark of a rule scored on
    every coin was frozen before 14 Nov. It reads as "skip" whether or not the store was migrated, so its id (worked
    out again from its own fields) is its own, never the new default's."""
    old = _stored(HUNT, OLD_HUNT_ID, every_coin=False)
    older = _stored(HUNT, OLD_HUNT_ID, every_coin=False)
    del older["trigger"]["crew"]  # frozen before 9 Nov, when a trigger had no such field
    for s in (old, older):
        assert paper.crew_legacy(s)
        seen = paper.as_frozen(s)
        assert seen["trigger"]["crew"] == "skip" and seen["id"] == OLD_HUNT_ID and s["trigger"].get("crew", "") == ""
        assert paper.sid_of(s) == OLD_HUNT_ID and paper.sid_of(seen) == OLD_HUNT_ID
    new = _stored(HUNT, paper.strategy_id(HUNT.__dict__, EXIT, "+3", 1, 0.2), every_coin=True)
    assert not paper.crew_legacy(new) and paper.as_frozen(new) == new and paper.sid_of(new) == new["id"]
    for same in (_stored(Trigger("kol", sol=1.0), OLD_KOL_ID, every_coin=False, landing=(0, "+6"),
                         exit_params=COPY_EXIT),
                 _stored(Trigger("mig", window=10.0), OLD_MIG_ID, every_coin=False),
                 _stored(replace(HUNT, bots="bundler"), OLD_BOTS_ID, every_coin=False),
                 _stored(ONLY, OLD_ONLY_ID, every_coin=False)):  # fmt: skip
        assert not paper.crew_legacy(same) and paper.as_frozen(same) is same and paper.sid_of(same) == same["id"]


def test_the_rules_stored_on_the_server_on_14_nov_keep_their_ids():
    """The 115 rules the server's paper test held on 14 Nov 2026 (78 running, 37 superseded; ids, triggers, exits and
    landings as stored, read with read-only SQL). Each one's id, worked out again from its own fields by today's
    code, is the id it is stored under: before the migration and after it. So is the id of the sniper OKTech runs
    for it (`paper.live_id`, written down from the code before the change). The 73 launch-type rules that could pass
    over a crew's coin (37 running, 36 superseded; not the hunt on the bundlers' wallets) say "skip" afterwards, each
    in its old place with nothing else changed; every other rule is the very record it was. And the same trigger with
    today's default is, for each running one, a rule the store has never held."""
    cov = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    live_ids = cov.pop("live_ids")  # each rule's sniper id (paper.live_id) as the code before the change gave it
    assert (len(cov["strategies"]), len(cov["superseded"]), len(live_ids)) == (78, 37, 115)
    for s in [*cov["strategies"], *cov["superseded"]]:
        if not paper.fed(s):  # a rule another job feeds has an id of that job's making
            assert paper.sid_of(s) == s["id"], s["id"]
        # the sniper OKTech runs for it is the one it was: what the automatic test dropped stays dropped, also for
        # the same rule frozen anew on every coin (one sniper live for both)
        assert paper.live_id(s) == paper.live_id(paper.as_frozen(s)) == live_ids[s["id"]], s["id"]
        if paper.crew_legacy(s):
            anew = s | {"trigger": s["trigger"] | {"crew": ""}, paper.EVERY_COIN: True}
            assert paper.live_id(anew) == live_ids[s["id"]] and paper.sid_of(anew) != s["id"]
    store = Store(":memory:")
    store.set_coverage("paper_strategies", cov)
    moved = paper.migrate_crew(store, now=NOW)
    after = store.coverage("paper_strategies")
    launch_type = ("launch", "hunt", "whale", "burst")
    for name in ("strategies", "superseded"):
        assert [s["id"] for s in after[name]] == [s["id"] for s in cov[name]]  # the same rules in the same order
        for was, new in zip(cov[name], after[name], strict=True):
            trig = was["trigger"]
            if trig.get("kind") in launch_type and not trig.get("bots"):
                assert new["trigger"]["crew"] == "skip" and trig.get("crew", "") == ""
                assert new == was | {"trigger": trig | {"crew": "skip"}}  # nothing else of the record moved
                assert "serial crews' coins" in paper.not_as_live(new)
            else:
                assert new == was
            if not paper.fed(new):
                assert paper.sid_of(new) == new["id"]
    running = [s for s in cov["strategies"] if s["trigger"].get("kind") in launch_type and not s["trigger"].get("bots")]
    older = [s for s in cov["superseded"] if s["trigger"].get("kind") in launch_type]
    assert (len(running), len(older), moved) == (37, 36, 73)
    ids = {s["id"] for name in ("strategies", "superseded") for s in cov[name]}
    for s in running:
        fresh = paper.sid_of(s | {"trigger": s["trigger"] | {"crew": ""}, paper.EVERY_COIN: True})
        assert fresh not in ids
    store.close()


# -- the migration -----------------------------------------------------------------------------------------------------


def _legacy_store(path=":memory:") -> tuple[Store, set[str], dict[str, dict]]:
    """A store as the server's was before the change: 75 launches, every third a serial crew's coin; a hunt rule
    frozen at coin 30 by the old code (crew "", passing over crews' coins), scored forward up to coin 50 with a trade
    on every coin that is no crew's; the same rule frozen once before at the landing no live trade gets
    (superseded); a hunt on the bundlers' wallets, a copy rule and a MIG rule beside it."""
    store = Store(path)
    crew = set(world(store, 75, tuple(range(0, 75, 3))))
    frozen_at = START + 30 * 60 - 20
    rules = {
        "hunt": _stored(HUNT, OLD_HUNT_ID, every_coin=False, from_ts=frozen_at),
        "bots": _stored(replace(HUNT, bots="bundler"), OLD_BOTS_ID, every_coin=False, from_ts=frozen_at),
        "kol": _stored(
            Trigger("kol", sol=1.0),
            OLD_KOL_ID,
            every_coin=False,
            landing=(0, "+6"),
            from_ts=frozen_at,
            exit_params=COPY_EXIT,
        ),
        "mig": _stored(Trigger("mig", window=10.0), OLD_MIG_ID, every_coin=False, from_ts=frozen_at),
    }
    was = _stored(HUNT, "0ld1and1ng", every_coin=False, landing=(0, "+1"), from_ts=frozen_at,
                  superseded_at=frozen_at + 60)  # fmt: skip
    _put(store, list(rules.values()), [was])
    for k in range(30, 50):
        if addr(f"cal-{k}") not in crew:
            store.put_paper_trade({"strategy": OLD_HUNT_ID, "mint": addr(f"cal-{k}"), "trigger_ts": START + k * 60 + 2,
                                   "entry_ts": START + k * 60 + 2, "exit_ts": START + k * 60 + 12, "pnl": 0.01,
                                   "roi": 0.05, "reason": "timer", "data": {"venue": "pumpfun"}})  # fmt: skip
    store.set_coverage("paper", {"done_until": START + 50 * 60 - 20, "last_run": START + 50 * 60})
    return store, crew, rules


def test_the_migration_keeps_a_rules_id_trades_and_place_and_marks_it_research_only():
    """One pass over the stored rules. A launch-type rule frozen before keeps its id, its place in the list and every
    trade, says crew "skip" from then on (what it always did) and is named not as live, with the reason; the rule
    superseded at an old landing likewise. A hunt on a wallet group, a copy rule and a MIG rule are the very records
    they were. The coverage `paper:crew` says the pass ran, when, and which ids it moved."""
    store, _crew, rules = _legacy_store()
    before = store.coverage("paper_strategies")
    trades = store.paper_trades(OLD_HUNT_ID)
    assert len(trades) == 13 and paper.migrate_crew(store, now=NOW) == 2
    after = store.coverage("paper_strategies")
    assert [s["id"] for s in after["strategies"]] == [s["id"] for s in before["strategies"]]
    hunt = after["strategies"][0]
    assert hunt["id"] == OLD_HUNT_ID and hunt["trigger"]["crew"] == "skip"
    # its crew and, so that it is told from the same trigger frozen anew, its name: nothing else of the record moved
    assert hunt == rules["hunt"] | {"trigger": rules["hunt"]["trigger"] | {"crew": "skip"},
                                    "label": f"{HUNT.label()} (without serial crews' coins) | timer"}  # fmt: skip
    assert after["strategies"][1:] == before["strategies"][1:]  # the bundlers' hunt, the copy and the MIG rule
    assert after["superseded"][0]["id"] == "0ld1and1ng" and after["superseded"][0]["trigger"]["crew"] == "skip"
    assert store.paper_trades(OLD_HUNT_ID) == trades
    why = paper.not_as_live(hunt)
    assert "without serial crews' coins" in why and "OKTech cannot tell" in why and "research only" in why
    assert all(paper.not_as_live(s) is None for s in after["strategies"][1:])
    flag = store.coverage(paper.CREW_KEY)
    assert flag["done"] is True and flag["at"] == NOW and flag["moved"] == [OLD_HUNT_ID, "0ld1and1ng"]
    store.close()


def test_the_migration_runs_once():
    """A second pass finds nothing to do and writes nothing: the rules and the flag are what the first pass left,
    whenever it is asked again. A store with nothing frozen is flagged done all the same."""
    store, _crew, _rules = _legacy_store()
    assert paper.migrate_crew(store, now=NOW) == 2
    rules, flag = store.coverage("paper_strategies"), store.coverage(paper.CREW_KEY)
    updated = store._q("SELECT key, updated FROM coverage WHERE key IN ('paper_strategies', ?)", (paper.CREW_KEY,))
    store._exec("UPDATE coverage SET updated = 1 WHERE key IN ('paper_strategies', ?)", (paper.CREW_KEY,))
    assert paper.migrate_crew(store, now=NOW + 3600) == 0 and paper.migrate_crew(store, now=NOW + 7200) == 0
    assert store.coverage("paper_strategies") == rules and store.coverage(paper.CREW_KEY) == flag
    again = store._q("SELECT key, updated FROM coverage WHERE key IN ('paper_strategies', ?)", (paper.CREW_KEY,))
    assert len(updated) == 2 and all(at == 1 for _key, at in again)  # neither record was written again
    store.close()
    empty = Store(":memory:")
    assert paper.migrate_crew(empty, now=NOW) == 0 and empty.coverage(paper.CREW_KEY)["done"] is True
    assert empty.coverage(paper.CREW_KEY)["moved"] == []
    empty.close()


def test_the_forward_run_migrates_first_and_the_old_rule_goes_on_as_it_was_scored():
    """`paper run` on the store as the old code left it: the rule keeps its id and its 13 trades, and the trades the
    run adds are on coins that are no crew's, as every trade of its record is. The report shows it in its old place,
    with its record whole, and says what its trades are not."""
    store, crew, _rules = _legacy_store()
    place = [r["id"] for r in paper.report(store, _cfg(), NOW)["strategies"]]
    out = paper.run(store, _cfg(), now=NOW)
    assert out["new_trades"] > 0 and store.coverage(paper.CREW_KEY)["done"] is True
    trades = store.paper_trades(OLD_HUNT_ID)
    mints = {t["mint"] for t in trades}
    assert len(trades) == 13 + 17 and not mints & crew  # coins 50 to 74 that are no crew's: 17 more
    rep = paper.report(store, _cfg(), NOW)
    assert [r["id"] for r in rep["strategies"]] == place
    row = rep["strategies"][0]
    assert row["id"] == OLD_HUNT_ID and row["n"] == 30
    assert row["exit_note"] == "research only: scored without serial crews' coins" and row["research_only"] is True
    assert "OKTech cannot tell" in rep["research_only_why"][row["exit_note"]]  # the reason, once in the answer
    assert all("exit_note" not in r and "research_only" not in r for r in rep["strategies"][1:])
    store.close()


def test_the_old_rule_is_not_frozen_again_and_the_default_is_a_new_rule_scored_on_every_coin():
    """After the change, a freeze of the old rule's own words ("skip") finds it under test and adds nothing. A freeze
    of the same trigger with today's default is a different rule with a different id: it is frozen beside the old
    one, marked as scored on every coin, and the forward run then gives it the crews' coins the old rule passes
    over. The old rule's record is untouched by all of it."""
    store, crew, _rules = _legacy_store()
    paper.run(store, _cfg(), now=START + 55 * 60)  # migrates; nothing new has settled since the old code's last run
    had = store.paper_trades(OLD_HUNT_ID)
    freeze_at = START + 55 * 60
    again = paper.freeze(store, _cfg(), top=4, triggers=[SKIP], now=freeze_at)
    assert again["frozen"] == [] and again["kept"][0]["id"] == OLD_HUNT_ID
    assert any(OLD_HUNT_ID in x["why"] and "already in the paper test" in x["why"] for x in again["skipped"])
    fresh = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=freeze_at)
    (new,) = fresh["frozen"]
    assert new["id"] != OLD_HUNT_ID and new["trigger"]["crew"] == "" and new[paper.EVERY_COIN] is True
    assert new["id"] == paper.sid_of(new) and paper.not_as_live(new) is None
    assert new["lab"]["crew"]["moments"] == 25 and new["lab"]["crew"]["of"] == 75
    # the old rule is named among the records that are not as live, with the reason
    (stale,) = [x for x in fresh["not_as_live"] if x["id"] == OLD_HUNT_ID]
    assert "without serial crews' coins" in stale["why"] and stale["research_only"] is True
    ids = [s["id"] for s in store.coverage("paper_strategies")["strategies"]]
    assert ids[0] == OLD_HUNT_ID and ids[-1] == new["id"] and len(ids) == len(set(ids)) == 5
    third = paper.freeze(store, _cfg(), top=4, triggers=[HUNT, SKIP], now=freeze_at)
    assert third["frozen"] == []  # both are under test now: one forward record a rule
    paper.run(store, _cfg(), now=NOW)
    assert store.paper_trades(OLD_HUNT_ID)[: len(had)] == had
    old_coins = {t["mint"] for t in store.paper_trades(OLD_HUNT_ID)}
    new_coins = {t["mint"] for t in store.paper_trades(new["id"])}
    later = {addr(f"cal-{k}") for k in range(55, 75)}  # launched from the freeze on
    assert new_coins == later and new_coins & crew == later & crew and len(later & crew) == 6
    assert not old_coins & crew and later - crew <= old_coins
    store.close()


def test_a_rule_frozen_before_at_an_old_landing_moves_to_the_live_one_under_its_rules_old_id():
    """A rule frozen at a landing no live trade gets is moved to the live landing by the forward run, under the id
    its rule has there (`paper.to_live_landing`). For a rule frozen before 14 Nov that id is the one the old code
    would have given it: the rule moves as the rule it was, without serial crews' coins, and its old record is kept
    apart under its old id."""
    store = Store(":memory:")
    crew = set(world(store, 40, tuple(range(0, 40, 4))))
    at_plus_one = "41c50d8b5e"  # the id the code before the change gave this hunt at +1 in the trigger's own slot
    _put(store, [_stored(HUNT, at_plus_one, every_coin=False, landing=(0, "+1"), from_ts=START + 20 * 60 - 20)])
    paper.run(store, _cfg(), now=NOW)
    cov = store.coverage("paper_strategies")
    (live,), (was,) = cov["strategies"], cov["superseded"]
    assert (live["id"], live["was"], live["latency"], live["position"]) == (OLD_HUNT_ID, at_plus_one, 1, "+3")
    assert live["trigger"]["crew"] == "skip" and paper.EVERY_COIN not in live and paper.not_as_live(live)
    assert (
        was["id"] == at_plus_one and was["trigger"]["crew"] == "skip" and (was["latency"], was["position"]) == (0, "+1")
    )
    coins = {t["mint"] for t in store.paper_trades(OLD_HUNT_ID)}
    assert coins == {addr(f"cal-{k}") for k in range(20, 40)} - crew and len(coins) == 15
    store.close()


def test_a_rule_under_another_crew_choice_is_no_twin_that_takes_the_default_rules_place():
    """Two rows that fire on mostly the same moments are one rule under another setting, and only one is frozen. A
    rule's crew choice is no such setting: with crews' coins a sixth of the moments, "skip" and the default share
    more than 80 % of them, and both are frozen, each under its own id."""
    store = Store(":memory:")
    world(store, 72, tuple(range(0, 72, 6)))
    both = paper.freeze(store, _cfg(), top=4, triggers=[SKIP, HUNT], now=START + 40 * 60)
    assert sorted(s["trigger"]["crew"] for s in both["frozen"]) == ["", "skip"] and not both["skipped"]
    store.close()


def test_a_read_only_copy_that_was_never_migrated_still_reads_and_says_the_same(tmp_path):
    """The hourly copy of the database is read-only and may have been taken before the migration. Nothing is written
    to it; every reader still sees the old rule as "skip", under its own id, not as live."""
    db = tmp_path / "copy.db"
    store, _crew, _rules = _legacy_store(db)
    store.close()
    ro = Store(db, readonly=True)
    assert paper.migrate_crew(ro, now=NOW) == 0 and ro.coverage(paper.CREW_KEY) == {}
    seen = paper.frozen(ro)
    assert seen["strategies"][0]["trigger"]["crew"] == "skip" and seen["strategies"][0]["id"] == OLD_HUNT_ID
    assert seen["superseded"][0]["trigger"]["crew"] == "skip"
    assert ro.coverage("paper_strategies")["strategies"][0]["trigger"]["crew"] == ""  # the file is as it was
    rep = paper.report(ro, _cfg(), NOW)
    assert rep["strategies"][0]["id"] == OLD_HUNT_ID and rep["strategies"][0]["n"] == 13
    assert "without serial crews' coins" in rep["strategies"][0]["exit_note"]
    ro.close()


def test_a_rule_an_old_job_writes_after_the_migration_is_never_as_live_and_is_moved_at_the_next_pass():
    """A job that started before a deploy keeps the old code until it ends: a freeze of its own writes a rule that
    says crew "" and was scored without crews' coins, after the migration ran. Such a rule carries no mark of a rule
    scored on every coin, so it reads as "skip" at once, and the next pass writes it so."""
    store, _crew, _rules = _legacy_store()
    assert paper.migrate_crew(store, now=NOW) == 2
    cov = store.coverage("paper_strategies")
    late = _stored(replace(HUNT, sol=3.0), "1a7e1a7e1a", every_coin=False, from_ts=NOW)
    store.set_coverage("paper_strategies", cov | {"strategies": [*cov["strategies"], late]})
    seen = paper.frozen(store)["strategies"][-1]
    assert seen["trigger"]["crew"] == "skip" and "serial crews' coins" in paper.not_as_live(late)
    assert paper.migrate_crew(store, now=NOW + 60) == 1
    assert store.coverage("paper_strategies")["strategies"][-1]["trigger"]["crew"] == "skip"
    flag = store.coverage(paper.CREW_KEY)
    assert flag["at"] == NOW and flag["moved"] == [OLD_HUNT_ID, "0ld1and1ng", "1a7e1a7e1a"] and flag["last"] == NOW + 60
    store.close()


def test_the_default_and_the_old_rule_are_one_sniper_live():
    """OKTech runs one sniper for the rule scored without crews' coins and for the rule scored on every coin: it
    cannot tell them. `paper.live_id` is that sniper's id for both (and for the twin without the watch delay), so
    what the automatic test learnt of the sniper under the old id, a drop above all, holds for the new one."""
    old = paper.as_frozen(_stored(HUNT, OLD_HUNT_ID, every_coin=False))
    new = _stored(HUNT, paper.strategy_id(HUNT.__dict__, EXIT, "+3", 1, 0.2), every_coin=True)
    assert old["id"] != new["id"]
    assert paper.live_id(old) == paper.live_id(new) == OLD_UNWATCHED_ID
    only = _stored(ONLY, OLD_ONLY_ID, every_coin=False)
    assert paper.live_id(only) not in (OLD_UNWATCHED_ID, paper.live_id(new))


# == round 2 (14 Nov, five reviews) ====================================================================================
# -- A. a forward run of the old code after the migration --------------------------------------------------------------


def _old_code_run(store: Store, sid: str, now: float) -> dict:
    """A forward run as the code before 14 Nov does it on this store (a rollback, or a job of the old code that
    outlives a deploy): it passes over serial crews' coins for the rule `sid` whatever its record says, writes none
    of the stored rules, and leaves the run's state without the mark of the code that scores every coin."""
    cov, flag = store.coverage("paper_strategies"), store.coverage(paper.CREW_KEY)
    as_old = [s | {"trigger": s["trigger"] | {"crew": "skip"}} if s["id"] == sid else s for s in cov["strategies"]]
    store.set_coverage("paper_strategies", cov | {"strategies": as_old})
    out = paper.run(store, _cfg(), now=now)
    state = store.coverage("paper")
    store.set_coverage("paper_strategies", cov)
    store.set_coverage(paper.CREW_KEY, flag)
    store.set_coverage("paper", {"done_until": state["done_until"], "last_run": state["last_run"]})
    return out


def _frozen_default(store: Store) -> tuple[dict, set[str]]:
    """75 launches, every third a serial crew's; the store migrated at minute 50, the default hunt frozen at minute
    55 by today's code (X), and one run of today's code over the launches of minutes 55 to 61."""
    crew = set(world(store, 75, tuple(range(0, 75, 3))))
    paper.migrate_crew(store, now=START + 50 * 60)
    (x,) = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=START + 55 * 60)["frozen"]
    paper.run(store, _cfg(), now=START + 62 * 60 + paper.SETTLE_SECS)
    assert {t["mint"] for t in store.paper_trades(x["id"])} == {addr(f"cal-{k}") for k in range(55, 62)}
    return x, crew


def test_a_run_of_old_code_after_the_migration_makes_the_rules_it_scored_research_only():
    """Two reviewers, one proof. `every_coin` says who FROZE a rule, not who scored its forward trades. Old code that
    runs `paper run` after today's code froze a default rule X passes over crews' coins for X and moves the run's
    mark on: X's record is then kinder than its sniper, and nothing in X says so. The run's state carries the mark
    of the code that wrote it; a state without it, written after the migration, is a run of other code: every
    default rule it scored is marked, keeps its id and its `every_coin`, and is research only everywhere, before
    the mark is written (any reader) and after (the next run writes it, and says so)."""
    from snipetech.oktech import strategy_sniper
    from snipetech.operator import autotest, live_test

    store = Store(":memory:")
    x, crew = _frozen_default(store)
    sid = x["id"]
    assert store.coverage("paper")["crew"] == 1  # the state today's code writes
    assert _old_code_run(store, sid, START + 70 * 60 + paper.SETTLE_SECS)["new_trades"] == 5  # 62 to 69, no crew coin
    coins = {t["mint"] for t in store.paper_trades(sid)}
    assert len(coins) == 12 and coins & crew == {addr("cal-57"), addr("cal-60")}  # 63, 66 and 69 are missing
    window = [START + 62 * 60, START + 70 * 60]
    # any reader of the stored rules sees it at once, nothing written yet
    seen = paper.frozen(store)["strategies"][-1]
    assert seen["id"] == sid and seen[paper.EVERY_COIN] is True and seen[paper.OLD_CODE] == window
    assert paper.OLD_CODE not in store.coverage("paper_strategies")["strategies"][-1]
    why = paper.crew_not_as_live(seen)
    assert "left serial crews' coins out" in why and "research only" in why and "freeze" in why.lower()
    assert paper.not_as_live(seen) == why
    row = next(r for r in paper.report(store, _cfg(), NOW)["strategies"] if r["id"] == sid)
    assert row["research_only"] is True and "left serial crews' coins out" in row["exit_note"]
    assert live_test._why_not(row, seen).startswith("is research only")
    with pytest.raises(ValueError, match="left serial crews' coins out"):
        strategy_sniper(seen, 1, "hunt")
    assert autotest.live_ids(store)[sid] == paper.live_id(x)  # the sniper it is does not change
    # the next run of today's code writes the mark, says so, and goes on scoring X on every coin
    out = paper.run(store, _cfg(), now=NOW)
    assert out["old_code_scored"] == {"from": window[0], "until": window[1], "rules": [sid]}
    kept = store.coverage("paper_strategies")["strategies"][-1]
    assert kept["id"] == sid and kept[paper.EVERY_COIN] is True and kept[paper.OLD_CODE] == window
    assert kept == x | {paper.OLD_CODE: window, "label": kept["label"]} and kept["label"] != x["label"]
    assert "in part scored without serial crews' coins" in kept["label"]
    assert store.coverage("paper")["crew"] == 1 and addr("cal-72") in {t["mint"] for t in store.paper_trades(sid)}
    assert not {addr(f"cal-{k}") for k in (63, 66, 69)} & {t["mint"] for t in store.paper_trades(sid)}
    again = paper.run(store, _cfg(), now=NOW + 60)
    assert "old_code_scored" not in again
    assert "left serial crews' coins out" in paper.crew_not_as_live(paper.frozen(store)["strategies"][-1])
    text = R.render_paper("run", out)
    assert "left serial crews' coins out" in text and sid in text
    store.close()


def test_a_rule_scored_in_part_by_old_code_can_be_frozen_again_under_an_id_of_its_own():
    """ "Freeze it again" has to work: one forward record a rule, and the same trigger with the same exit is the same
    id. A record marked as scored in part by old code no longer stands for its rule, and the same rule frozen again
    is a second making of it (`again`: 1), with an id of its own and a record that starts empty; it is the same
    sniper live (paper.live_id), so what the automatic test learnt of it holds."""
    store = Store(":memory:")
    x, _crew = _frozen_default(store)
    _old_code_run(store, x["id"], START + 70 * 60 + paper.SETTLE_SECS)
    paper.run(store, _cfg(), now=NOW)
    old = store.coverage("paper_strategies")["strategies"][-1]
    out = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=NOW)
    (new,) = out["frozen"]
    assert (new["trigger"], new["exit"], new["position"], new["latency"]) == (x["trigger"], x["exit"], "+3", 1)
    assert new["id"] != x["id"] and new[paper.AGAIN] == 1 and new["id"] == paper.sid_of(new)
    assert paper.not_as_live(new) is None and paper.live_id(new) == paper.live_id(x)
    assert store.paper_trades(new["id"]) == [] and len(store.paper_trades(x["id"])) > 12
    assert [s["id"] for s in store.coverage("paper_strategies")["strategies"]] == [x["id"], new["id"]]
    assert store.coverage("paper_strategies")["strategies"][0] == old  # the marked record is as it was
    third = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=NOW + 60)
    assert third["frozen"] == []  # the rule has its one record again
    store.close()


def test_a_store_only_todays_code_ran_on_is_untouched_by_the_old_code_check():
    """Nothing is marked while every run's state carries the mark, however many runs and freezes there were; nor on a
    store never migrated, whose state the old code wrote before the change."""
    store = Store(":memory:")
    x, _crew = _frozen_default(store)
    for now in (START + 70 * 60 + paper.SETTLE_SECS, NOW):
        assert "old_code_scored" not in paper.run(store, _cfg(), now=now)
    (kept,) = store.coverage("paper_strategies")["strategies"]
    assert kept == x and paper.frozen(store)["strategies"] == [x] and paper.not_as_live(kept) is None
    assert {addr(f"cal-{k}") for k in (63, 66, 69)} <= {t["mint"] for t in store.paper_trades(x["id"])}
    assert store.coverage(paper.CREW_KEY)["scored_until"] == store.coverage("paper")["done_until"]
    store.close()
    old, _crew, _rules = _legacy_store()  # the state as the old code left it, before any migration
    assert "crew" not in old.coverage("paper")
    assert "old_code_scored" not in paper.run(old, _cfg(), now=NOW)
    assert all(paper.OLD_CODE not in s for s in old.coverage("paper_strategies")["strategies"])
    old.close()


# -- B3 / B4 / D. what a research record is called, and how often its reason is said ----------------------------------


def test_a_rule_frozen_before_is_told_apart_by_its_name():
    """A migrated rule kept its stored label, and the same trigger frozen anew got the identical one: two rows that
    differed only by id on the live page, in the report, the digest and /test. The old rule's label says "(without
    serial crews' coins)" after its trigger part, as a reader sees it (`as_frozen`) and as the migration writes
    it, once."""
    old = _stored(HUNT, OLD_HUNT_ID, every_coin=False)
    want = f"{HUNT.label()} (without serial crews' coins) | timer"
    assert paper.as_frozen(old)["label"] == want and old["label"] == f"{HUNT.label()} | timer"
    assert paper.as_frozen(paper.as_frozen(old))["label"] == want
    bare = _stored(HUNT, OLD_HUNT_ID, every_coin=False) | {"label": "a rule with no exit part"}
    assert paper.as_frozen(bare)["label"] == "a rule with no exit part (without serial crews' coins)"
    store, _crew, _rules = _legacy_store()
    paper.migrate_crew(store, now=NOW)
    paper.migrate_crew(store, now=NOW + 60)
    stored = store.coverage("paper_strategies")
    assert stored["strategies"][0]["label"] == want and stored["superseded"][0]["label"] == want
    (new,) = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=START + 55 * 60)["frozen"]
    assert "serial crews" not in new["label"] and new["label"].split(" | ")[0] == HUNT.label()
    labels = [r["label"] for r in paper.report(store, _cfg(), NOW)["strategies"]]
    assert len(labels) == len(set(labels))
    store.close()


def test_the_report_and_a_freeze_say_the_long_reason_once_and_each_row_a_short_note():
    """On the server's rules the 444-character reason stood twice in every row (exit_note and crew_note): 40 kB more
    in each paper report. The reason stands once at the top of the answer (`research_only_why`, by its short note);
    a row carries `research_only` and the short note. A freeze's `not_as_live` leaves refuted rules out."""
    store, _crew, _rules = _legacy_store()
    paper.migrate_crew(store, now=NOW)
    cov = store.coverage("paper_strategies")
    gone = _stored(replace(HUNT, sol=9.0), "9e9e9e9e9e", every_coin=False) | {
        "refuted": {"at": NOW - 3600, "trades": 400, "mean_sol": -0.02}
    }
    store.set_coverage("paper_strategies", cov | {"strategies": [*cov["strategies"], gone]})
    rep = paper.report(store, _cfg(), NOW)
    note = "research only: scored without serial crews' coins"
    rows = {r["id"]: r for r in rep["strategies"]}
    for sid in (OLD_HUNT_ID, "9e9e9e9e9e"):
        assert rows[sid]["research_only"] is True and rows[sid]["exit_note"] == note and "crew_note" not in rows[sid]
    assert set(rep["research_only_why"]) == {note}
    assert len(rep["research_only_why"][note]) > 300 and "OKTech cannot tell" in rep["research_only_why"][note]
    assert json.dumps(rep).count("OKTech cannot tell such a coin") == 1
    out = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=START + 55 * 60)
    assert [x["id"] for x in out["not_as_live"]] == [OLD_HUNT_ID]  # the refuted one is not listed
    assert out["not_as_live"][0] == {"id": OLD_HUNT_ID, "label": rows[OLD_HUNT_ID]["label"], "why": note,
                                     "research_only": True}  # fmt: skip
    assert out["research_only_why"] == rep["research_only_why"]
    assert json.dumps(out).count("OKTech cannot tell such a coin") == 1
    store.close()


def test_the_reports_text_lists_research_records_apart_and_the_run_does_not_count_them_as_live():
    """`paper report` listed research-only rules in the table headed "Scored where live trades land", and `paper
    run` counted their new trades "at the live landing". They have a group of their own, as the rules at a place
    asked for have, and the run counts their trades apart."""
    store, _crew, _rules = _legacy_store()
    (new,) = paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=START + 55 * 60)["frozen"]
    out = paper.run(store, _cfg(), now=NOW)
    assert out["new_trades"] == 17 + 20 and out["new_trades_research"] == 17
    line = R.render_paper("run", out).splitlines()[0]
    assert "20 at the live landing" in line and "17 on research records" in line and "no sniper trades them" in line
    text = R.render_paper("report", paper.report(store, _cfg(), NOW))
    live, research = text.split("Research records", 1)
    assert new["id"] in live and OLD_HUNT_ID not in live.split("=" * 72, 1)[1]
    assert research.startswith(": scored without serial crews' coins, or on them alone; no sniper trades them")
    assert OLD_HUNT_ID in research and new["id"] not in research.split("mean / bound", 1)[0]
    assert text.count("OKTech cannot tell such a coin") == 1  # the reason once, under the tables
    only_research, _c, _r = _legacy_store()
    paper.migrate_crew(only_research, now=NOW)
    solo = paper.run(only_research, _cfg(), now=NOW)
    assert solo["new_trades"] == solo["new_trades_research"] == 17
    assert "at the live landing" not in R.render_paper("run", solo).splitlines()[0]
    store.close()
    only_research.close()


def test_the_freeze_text_names_the_research_only_records():
    """What `paper freeze` prints, without --json, for a rule scored without serial crews' coins: its own line, never
    "no longer the record of its rule", and the long reason once."""
    note = "research only: scored without serial crews' coins"
    text = R.render_paper("freeze", {"tape_hours": 1.0, "frozen": [], "kept": [],
                                     "not_as_live": [{"id": "d4cbfb7ae1", "label": "hunt", "why": note,
                                                      "research_only": True},
                                                     {"id": "f98f8abd6a", "label": "hunt 5", "why": note,
                                                      "research_only": True}],
                                     "research_only_why": {note: crew_why(SKIP)}})  # fmt: skip
    assert "research only, never goes live: d4cbfb7ae1, f98f8abd6a: scored without serial crews' coins" in text
    assert "no longer the record of its rule" not in text and text.count("OKTech cannot tell") == 1


def test_the_flag_names_each_moved_rule_once():
    """A stale write-back (a job of the old code writes the rules as it read them) makes the next pass move the same
    rules again: the flag's `moved` list names each id once."""
    store, _crew, _rules = _legacy_store()
    before = store.coverage("paper_strategies")
    assert paper.migrate_crew(store, now=NOW) == 2
    store.set_coverage("paper_strategies", before)  # written back as it was read before the migration
    assert paper.migrate_crew(store, now=NOW + 60) == 2
    assert store.coverage(paper.CREW_KEY)["moved"] == [OLD_HUNT_ID, "0ld1and1ng"]
    store.close()


def test_a_pass_with_nothing_to_do_takes_no_write_lock():
    """Every paper run and every freeze asks the migration first. Once it has run, the question is a plain read: no
    write transaction is opened (it took the database's write lock for 1.3 ms each time)."""
    store, _crew, _rules = _legacy_store()
    writes: list[int] = []
    real = store._write

    def counted(fn, immediate=True):
        writes.append(1)
        return real(fn, immediate)

    store._write = counted
    assert paper.migrate_crew(store, now=NOW) == 2 and len(writes) == 1
    assert paper.migrate_crew(store, now=NOW + 60) == 0 and paper.migrate_crew(store, now=NOW + 120) == 0
    assert len(writes) == 1
    store.close()


def test_a_stored_rule_is_as_live_only_with_the_exact_default_and_its_mark():
    """`crew_not_as_live` passes a stored launch-type rule (not a hunt on a wallet group) only when its crew is
    exactly "" and it carries `every_coin`. Any other value, "Skip" or a number among them, is refused, and the
    sniper builder reads every dict with an id or a freeze time as a stored record."""
    from snipetech.oktech import strategy_sniper

    ok = _stored(HUNT, "aaaaaaaaaa", every_coin=True)
    assert paper.crew_not_as_live(ok) is None
    for crew in ("Skip", "SKIP", " ", "all", 0, 1, None, False):
        odd = ok | {"trigger": ok["trigger"] | {"crew": crew}}
        assert paper.crew_not_as_live(odd), crew
        with pytest.raises(ValueError, match="research only"):
            strategy_sniper(odd)
    no_key = ok | {"trigger": {k: v for k, v in ok["trigger"].items() if k != "crew"}}
    assert paper.crew_not_as_live(no_key) is None  # no crew named, and the mark: every coin
    by_id = {k: v for k, v in _stored(HUNT, OLD_HUNT_ID, every_coin=False).items() if k != "from_ts"}
    with pytest.raises(ValueError, match="without serial crews' coins"):
        strategy_sniper(by_id)  # an id and no freeze time: a stored record all the same
    by_time = {k: v for k, v in _stored(HUNT, OLD_HUNT_ID, every_coin=False).items() if k != "id"}
    with pytest.raises(ValueError, match="without serial crews' coins"):
        strategy_sniper(by_time)
    lab_row = {"trigger": dict(HUNT.__dict__), "exit": {"stop_loss": 0.2, "max_hold_secs": 5}, "buy_sol": 0.2}
    assert strategy_sniper(lab_row).config["mode"] == "hunt"  # no stored record: the trigger's own words decide


# -- E. the scoring's own words ----------------------------------------------------------------------------------------


def test_a_skip_rules_share_is_over_the_coins_it_fired_on():
    """`left_out` counted every crew coin the rule fired on and `of` only scored moments, so at a size where moments
    are left out for size the line said "of the N coins it fired on" with N too small (at 60 SOL here: 3 left out
    "of 3", 100 %). `fired` is the coins the rule fired on (scored, left out for size or for the PumpSwap tape, or
    passed over as a crew's), and the share is over that: the same 3 of 33 at any size, the default rule's own
    share where every moment is scored."""
    store = Store(":memory:")
    world(store, 33, (5, 15, 25))
    for buy in (0.2, 60.0):
        data = collect(store, _cfg(), buy, 0, "+6", 0.0045, landings=paper.LAUNCH_LANDINGS, triggers=[HUNT, SKIP],
                       exits=[TIMER], kols={})  # fmt: skip
        size = data["size"]["triggers"][SKIP.label()]
        assert (size["scored"], sum(size["skipped"].values())) == ((30, 0) if buy < 1 else (0, 30))
        skip = data["crew"]["triggers"][SKIP.label()]
        assert (skip["of"], skip["left_out"], skip["fired"]) == (size["scored"], 3, 33)
        assert skip["left_out_share"] == pytest.approx(3 / 33)
        assert "3 of the 33 coins it fired on (9.1 %)" in skip["line"]
        own = crew_why(SKIP, skip)
        assert "3 of the 33 coins it fired on, 9.1 %" in own and "37 %" not in own
    every = data["crew"]["triggers"].get(HUNT.label())
    assert every is None or every["of"] == 0  # at 60 SOL the default has no scored moment to count crews' coins in
    assert crew_why(SKIP, {"left_out": 3, "of": 30}) == crew_why(SKIP)  # no fired count kept: the general share
    store.close()


def test_forward_a_crew_is_known_only_once_its_third_coin_has_launched_and_the_words_say_so():
    """The lab knows a crew over its whole window; the forward run when it scores. A crew's third launch after the
    rule fired on its first two: the default is scored on the same 33 coins on both sides, whenever the crew became
    one. A "skip" record holds the crew's two earlier coins forward (lab 30, forward 32) and an "only" record lacks
    them (lab 3, forward 1). The reasons say it, and the run says how many coins each rule passed over."""
    from snipetech.analysis.lab import CREW_ONLY_WHY, CREW_SKIP_WHY

    store = Store(":memory:")
    world(store, 20, ())
    for j, k in enumerate((5, 15)):  # two launches on one file: no crew yet
        store._exec("UPDATE launches SET uri=?, creator=? WHERE mint=?", (URI, addr(f"crew-dev-{j}"), addr(f"cal-{k}")))
    mid = START + 19 * 60 + 30 + paper.SETTLE_SECS
    store.set_coverage("tape", {"spans": [[START - 100, mid]]})
    rules = [_stored(HUNT, "aaaaaaaaaa", every_coin=True), _stored(SKIP, "bbbbbbbbbb", every_coin=False),
             _stored(ONLY, "cccccccccc", every_coin=False)]  # fmt: skip
    _put(store, rules)
    first = paper.run(store, _cfg(), now=mid)
    assert first["crew_passed"] == {"cccccccccc": 20}  # no crew is known: "only" passes over every coin
    world(store, 33, (5, 15, 25), first=20)  # thirteen more launches, coin 25 the crew's third
    out = paper.run(store, _cfg(), now=NOW)
    assert out["crew_passed"] == {"bbbbbbbbbb": 1, "cccccccccc": 13}  # coin 19 still traded, and twelve new ones
    forward = [{t["mint"] for t in store.paper_trades(s["id"])} for s in rules]
    lab = [_coins(_lab(store, [HUNT, SKIP, ONLY]), ti) for ti in range(3)]
    every, crew = {addr(f"cal-{k}") for k in range(33)}, {addr(f"cal-{k}") for k in (5, 15, 25)}
    assert lab[0] == forward[0] == every  # the default: the same coins, whenever the crew became one
    assert lab[1] == every - crew and lab[2] == crew  # the lab: hindsight
    assert forward[1] == every - {addr("cal-25")} and forward[2] == {addr("cal-25")}  # forward: once it is known
    said = "a crew is known only once its third coin has launched"
    assert said in CREW_SKIP_WHY and "holds the crews' earlier coins" in CREW_SKIP_WHY
    assert said in CREW_ONLY_WHY and "lacks the crews' earlier coins" in CREW_ONLY_WHY
    assert said in crew_why(SKIP) and said in paper.crew_not_as_live(_stored(HUNT, OLD_HUNT_ID, every_coin=False))
    assert "hindsight" in crew_skips.__doc__ and "its third coin" in crew_skips.__doc__
    store.close()


def test_an_only_rule_passes_over_a_coin_whose_launch_the_tape_does_not_hold():
    """A whale or burst rule fires on a coin whose launch the tape does not hold. With crew "only" such a coin was
    scored (it is no known crew's coin), and the block then said "all 4 of its moments" with 3 of 4 crews' coins.
    For "only", a coin without a known launch is not a crew's coin: passed over, and moments equal `of`."""
    store = Store(":memory:")
    world(store, 8, (0, 1, 2))
    store._exec("DELETE FROM launches WHERE mint=?", (addr("cal-5"),))
    only, skip, plain = (Trigger("whale", sol=2.0, crew=c) for c in ("only", "skip", ""))
    assert crew_skips(only, None, {URI}) and not crew_skips(skip, None, {URI}) and not crew_skips(plain, None, {URI})
    data = collect(store, _cfg(), 0.2, 0, "+6", 0.0045, landings=paper.LAUNCH_LANDINGS, triggers=[only, plain],
                   exits=[TIMER], kols={})  # fmt: skip
    assert _coins(data, 0) == {addr(f"cal-{k}") for k in (0, 1, 2)}
    assert addr("cal-5") in _coins(data, 1)  # the default still scores it: the sniper buys it
    block = data["crew"]["triggers"][only.label()]
    assert block["moments"] == block["of"] == 3 and "all 3 of its moments" in block["line"]
    store.close()


# -- F. promises no test held ------------------------------------------------------------------------------------------


def test_the_id_of_a_rule_frozen_on_every_coin_is_written_down():
    """Every launch-type rule frozen since 14 Nov hangs its record on this id: a change to how the default is hashed
    would move them all, and the suite said of the new id only that it is not the old one."""
    assert paper.strategy_id(HUNT.__dict__, EXIT, "+3", 1, 0.2) == "0a05047ff5"
    assert paper.strategy_id(HUNT.__dict__, EXIT, "+3", 1, 0.2, again=1) != "0a05047ff5"
    assert paper.strategy_id(HUNT.__dict__, EXIT, "+3", 1, 0.2, again=0) == "0a05047ff5"


def test_rescore_prices_a_default_rules_crew_coins_again_and_leaves_a_skip_rules():
    """`paper rescore` asks the same function as the lab and the run. Twelve coins, no crew yet, both rules trade
    every one; three turn out to be one crew's. Priced again: the default rule's twelve are its moments still; the
    "skip" rule would not fire on the three today, so they are left as they were and counted."""
    store = Store(":memory:")
    world(store, 12, ())
    rules = [_stored(HUNT, "aaaaaaaaaa", every_coin=True), _stored(SKIP, "bbbbbbbbbb", every_coin=False)]
    _put(store, rules)
    paper.run(store, _cfg(), now=NOW)
    assert [len(store.paper_trades(s["id"])) for s in rules] == [12, 12]
    for j, k in enumerate((2, 5, 8)):
        store._exec(
            "UPDATE launches SET uri=?, creator=? WHERE mint=?", (URI, addr(f"crew-dev-{j % 2}"), addr(f"cal-{k}"))
        )
    store._exec("UPDATE paper_trades SET data=json_remove(data, '$.model')")  # rows another model priced
    out = paper.rescore(store, _cfg(), ids=["aaaaaaaaaa", "bbbbbbbbbb"], now=NOW)
    by = {s["id"]: s for s in out["strategies"]}
    assert by["aaaaaaaaaa"]["rescored"] == 12 and not by["aaaaaaaaaa"].get("left")
    assert by["bbbbbbbbbb"]["rescored"] == 9 and by["bbbbbbbbbb"]["left"] == {"moment": 3}
    store.close()


def test_a_freeze_on_a_store_never_migrated_migrates_it_and_says_which_rules_it_moved():
    """`paper freeze` asks the migration before it reads a rule: the flag, the one record of which ids were moved, is
    written by the freeze that finds the store as the old code left it."""
    store, _crew, _rules = _legacy_store()
    assert store.coverage(paper.CREW_KEY) == {}
    paper.freeze(store, _cfg(), top=4, triggers=[HUNT], now=START + 55 * 60)
    flag = store.coverage(paper.CREW_KEY)
    assert flag["done"] is True and flag["moved"] == [OLD_HUNT_ID, "0ld1and1ng"]
    store.close()
