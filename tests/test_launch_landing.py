"""Launch-window rules are scored where a live launch buy lands (7 Nov).

The forward paper test scored "a non-dev buys >= 1 SOL in the first second" at the copy landing: six trades behind the
trigger in its own slot. Its three best trades (+11 SOL of its +10.6 SOL) were coins whose create slot held a
22-wallet bundle of 115-150 SOL that completed the curve in that slot (BRUH below): the paper bought inside the bundle
and sold after the coin had moved to PumpSwap at 12-23x. OKTech can only run such a rule through Hunt, which buys a
slot or two after its trigger (5 Nov hunt fills), and its pump.fun buy fails on a curve that has completed."""

import csv
from pathlib import Path

from snipetech.analysis import paper
from snipetech.analysis.lab import Trigger, _fire, collect, curve_done
from snipetech.config import Config
from snipetech.models import Launch, Trade
from snipetech.sim.market import MarketPath
from snipetech.store import Store

FX = Path(__file__).parent / "fixtures"
BRUH = "kxQofyaYD6dv8x7zELCkJAcCUYErjE5njVA2rUdpump"
SOLO = Trigger("whale", sol=1.0, max_age=1.0, plain=True, solo=True)  # the paper rule 9b2bd6f664


def _bruh() -> tuple[Store, Launch]:
    with open(FX / "graduating_bundle_7nov_launch.tsv", newline="") as fh:
        (r,) = list(csv.DictReader(fh, delimiter="\t"))
    launch = Launch(r["mint"], r["user"], r["creator"], r["name"], r["symbol"], r["uri"], r["bonding_curve"],
                    int(r["slot"]), int(r["ts"]), r["sig"], r["token_program"], r["mayhem"] == "1",
                    r["holder_reward"] == "1", r["quote_mint"])  # fmt: skip
    with open(FX / "graduating_bundle_7nov_tape.tsv", newline="") as fh:
        trades = [
            Trade(sig=t["sig"], ev=int(t["ev"]), slot=int(t["slot"]), pos=int(t["pos"]), ts=int(t["ts"]), mint=t["mint"],
                  venue=t["venue"], user=t["user"], is_buy=t["is_buy"] == "1", quote=int(t["quote"]), base=int(t["base"]),
                  fee=int(t["fee"]), fee_bps=int(t["fee_bps"]), q_after=int(t["q_after"]), b_after=int(t["b_after"]),
                  r_base_after=int(t["r_base_after"] or 0), ix=t["ix"])
            for t in csv.DictReader(fh, delimiter="\t")
        ]  # fmt: skip
    store = Store(":memory:")
    store.add_launches([launch])
    store.add_trades(trades)
    return store, launch


def test_launch_window_rules_land_where_oktech_hunt_buys():
    for kind in ("hunt", "whale", "burst"):
        assert paper.live_landing(kind, "+6") == paper.HUNT_LANDING, kind
    assert paper.live_landing("launch", "+6") == paper.LAUNCH_LANDING
    assert paper.live_landing("kol", "+6") == (0, "+6")  # a copy of a known wallet still lands in its slot
    # one map for every launch-type rule (and OKTech MIG, tests/test_lab_mig.py), so a lab pass scores each where it
    # lands live
    # and, since 11 Nov, the record of deployer verdicts (analysis/dev_forward.py): the deployer analysis's own landing
    assert paper.LAUNCH_LANDINGS == {"launch": paper.LAUNCH_LANDING, "hunt": paper.HUNT_LANDING,
                                     "whale": paper.HUNT_LANDING, "burst": paper.HUNT_LANDING,
                                     "mig": paper.MIG_LANDING, "dev": paper.HUNT_LANDING}  # fmt: skip


def test_a_coin_that_completes_its_curve_in_the_create_slot_is_no_launch_trade():
    store, launch = _bruh()
    path = MarketPath.build(store.trades_for_mint(BRUH), launch)
    i = _fire(SOLO, path, launch, set())
    assert i is not None and path.slot[i] == launch.slot  # the paper rule fires inside the create slot
    old = int(path.landing_index(int(path.slot[i]), "+6", i))
    assert not curve_done(path, old)  # the copy landing bought inside the bundle, on the curve
    lat, pos = paper.HUNT_LANDING
    e = int(path.landing_index(int(path.slot[i]) + lat, pos, i))
    assert curve_done(path, e)  # a slot later the curve has completed: OKTech's pump.fun buy fails


def test_the_lab_and_the_paper_test_give_no_trade_on_a_completed_curve():
    store, launch = _bruh()
    store.set_coverage("tape", {"spans": [[launch.ts - 700, launch.ts + 800]]})
    data = collect(store, Config(), 0.2, 0, "+6", min_trades=1, landings=paper.LAUNCH_LANDINGS)
    kinds = {ti: t.kind for ti, t in enumerate(data["triggers"])}
    assert not [r for r in data["rows"] if kinds[r["trigger"]] in ("whale", "hunt", "burst", "launch")]
    strategy = {"id": "solo", "label": SOLO.label(), "trigger": SOLO.__dict__, "exit": {"stop_loss": 0.3,
                "max_hold_secs": 300, "stale_secs": 1.0, "sell_on_dev_sell": False}, "position": "+6", "latency": 0,
                "buy_sol": 0.2, "per_tx_sol": 0.0035, "from_ts": launch.ts - 60, "lab": {}}  # fmt: skip
    store.set_coverage("paper_strategies", {"strategies": [strategy]})
    paper.run(store, Config(), now=launch.ts + 800, min_trades=1)
    (live,) = store.coverage("paper_strategies")["strategies"]
    assert (live["latency"], live["position"]) == paper.HUNT_LANDING  # re-keyed to where a hunt buy lands
    assert store.paper_trades(live["id"]) == []  # and there it is no trade, not +23x
