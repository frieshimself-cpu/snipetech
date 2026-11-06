"""The lab's simulator against the 12 copy fills of 6 Nov 2026 that OKTech sold: same exit, same slot, same money.

Each fill is replayed on the tape of its coin with our own trades taken out, entering at the state just before our buy
with the SOL we spent, under the preset the sniper ran (`lab`, or `hold60` for the three Gfree copies). The plan's bar:
the same exit reason and an exit slot within 2 on at least 10 of 12, and the summed proceeds within 5 % of live.
Reached on this tape: 11 of 12 and +0.1 %. The miss is 7bDN: its 60 s timer sold 61.2 s after the buy (286 slots),
the replay's at 281 slots; the 300 s timers of 5 Nov sold at 300 s, so the timer is not shifted to fit one fill.
"""

import gzip
import json
from dataclasses import replace
from pathlib import Path

import pytest

from snipetech.analysis.lab import EXIT_LATENCY_SLOTS, EXIT_POSITION, LIVE_EXITS, leader_sell_fractions
from snipetech.models import Trade
from snipetech.sim.exits import Costs, prepare, run_exit
from snipetech.sim.market import DEFAULT_SLOT_SECS, MarketPath, slot_secs_from

FIXTURES = Path(__file__).parent / "fixtures"
NO_COSTS = Costs(per_tx_lamports=0.0, platform_fee_bps=0.0)  # proceeds are compared before transaction fees


def _load() -> tuple[list[dict], dict[str, list[dict]]]:
    fills = json.loads((FIXTURES / "copy_fills_6nov.json").read_text())["fills"]
    with gzip.open(FIXTURES / "copy_fills_6nov_tape.tsv.gz", "rt") as fh:
        lines = fh.read().splitlines()
    cols = lines[0].split("\t")
    tape: dict[str, list[dict]] = {}
    for line in lines[1:]:
        row = dict(zip(cols, line.split("\t")))
        tape.setdefault(row["mint"], []).append(row)
    return fills, tape


def _path(rows: list[dict], leader: str, slot_secs: float) -> MarketPath:
    trades = [Trade(sig=f"{r['slot']}:{r['pos']}", ev=0, slot=int(r["slot"]), pos=int(r["pos"]), ts=int(r["ts"]),
                    mint=r["mint"], venue="curve", user=leader if r["leader"] == "1" else "", is_buy=r["is_buy"] == "1",
                    quote=int(r["quote"]), base=int(r["base"]), fee=0, fee_bps=125, q_after=int(r["q_after"]),
                    b_after=int(r["b_after"])) for r in rows]  # fmt: skip
    return MarketPath.build(trades, None, slot_secs=slot_secs)


def replay(fill: dict, rows: list[dict], slot_secs: float, watch: bool = True):
    """(prepared position, result) for one fill under the preset it ran."""
    path = _path(rows, fill["leader"], slot_secs)
    buy = fill["buy"]
    before = max((r for r in rows if (int(r["slot"]), int(r["pos"])) < (buy["slot"], buy["pos"])),
                 key=lambda r: (int(r["slot"]), int(r["pos"])))  # fmt: skip
    entry = path.sigs.index(f"{before['slot']}:{before['pos']}")  # the state just before our buy
    ex = LIVE_EXITS[fill["preset"]] if watch else replace(LIVE_EXITS[fill["preset"]], watch_secs=0.0)
    prep = prepare(path, entry, buy["sol"], NO_COSTS, EXIT_LATENCY_SLOTS, EXIT_POSITION,
                   leader_frac=leader_sell_fractions(path, fill["leader"]))  # fmt: skip
    return prep, run_exit(prep, ex, NO_COSTS, end_ts=float(buy["ts"] + 330))


def _score(slot_secs: float, watch: bool = True) -> tuple[int, float, float, str]:
    fills, tape = _load()
    matched, sim, live, lines = 0, 0.0, 0.0, []
    for f in fills:
        prep, r = replay(f, tape[f["mint"]], slot_secs, watch)
        assert prep.held == pytest.approx(f["buy"]["tokens"], rel=1e-6)  # the replay buys the tokens we really got
        ok = r.exit_reason == f["live_exit"] and abs(r.exit_slot - f["sell"]["slot"]) <= 2
        matched += ok
        sim += r.proceeds_sol
        live += f["sell"]["sol"]
        lines.append(f"{f['name']:<16} live {f['live_exit']:<9} +{f['sell']['slot'] - f['buy']['slot']:<4} "
                     f"sim {r.exit_reason:<9} +{r.exit_slot - f['buy']['slot']:<4} {r.proceeds_sol:.6f} vs "
                     f"{f['sell']['sol']:.6f} {'ok' if ok else 'MISS'}")  # fmt: skip
    return matched, sim, live, "\n".join(lines) + f"\nmatched {matched}/{len(fills)}, proceeds {sim:.4f} vs {live:.4f}"


def test_the_tape_runs_at_0_214_s_a_slot():
    _, tape = _load()
    rows = [r for rs in tape.values() for r in rs]
    assert slot_secs_from([int(r["slot"]) for r in rows], [int(r["ts"]) for r in rows]) == pytest.approx(
        0.214, abs=0.002
    )


def test_the_sim_reproduces_the_6_nov_copy_exits():
    _, tape = _load()
    rows = [r for rs in tape.values() for r in rs]
    slot_secs = slot_secs_from([int(r["slot"]) for r in rows], [int(r["ts"]) for r in rows])
    matched, sim, live, report = _score(slot_secs)
    assert matched >= 10, report
    assert abs(sim / live - 1) <= 0.05, report


def test_the_old_simulator_did_not():
    """0.4 s a slot and no watch delay: the dead exit fires on pauses OKTech never sold on, and the sales look richer."""
    matched, sim, live, report = _score(DEFAULT_SLOT_SECS, watch=False)
    assert matched < 10 and sim > live * 1.03, report
