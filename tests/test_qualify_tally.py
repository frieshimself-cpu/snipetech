"""`qualify tally`: do the wallets that passed qualify's forward check go on making money after it? (agents' I337)

A review on 19 Nov counted the record: of 113 picked wallets 16 ever met the forward bar, and their copies after that
summed to -6.9 SOL over 753 paper copies, 11 of 16 down. Arming a wallet sooner was held back on that count, and
nothing kept it since: the tester had no command that reads it. The tally takes each actor whose forward status first
read "passed" at or after a moment, and sums its recorded copies after the tape that verdict read ended."""

import json

import pytest

from snipetech.qualify import tally as T
from snipetech.store import Store

SINCE = 1_795_114_949  # 19 Nov 2026 19:02 UTC
A, B, C, D, E = (f"{c}" * 44 for c in "ABCDE")


def _verdict(store, wallet, at, status, forward=None):
    store.put_verdict({"wallet": wallet, "computed_at": at, "status": status, "reasons": [], "forward": forward})


def _record(store, wallet, copies, actor=None):
    data = {"record": {"first_picked_at": copies[0][0] - 10 if copies else 0, "copies": copies}}
    if actor is not None:
        data["actor"] = actor
    store.put_score("qualify", wallet, data)


def _world():
    """A passes at SINCE+100 (tape end SINCE+50): two copies after, one before. B passed before SINCE: left out. C is
    A's companion and passed later: one actor with A, counted once (A's copies). D passes, then goes cold. E never
    passes."""
    st = Store(":memory:")
    _verdict(st, A, SINCE + 10, "candidate", {"status": "pending", "tape_end": SINCE})
    _verdict(st, A, SINCE + 100, "qualified", {"status": "passed", "tape_end": SINCE + 50})
    _verdict(st, A, SINCE + 900, "qualified", {"status": "passed", "tape_end": SINCE + 850})
    _record(st, A, [[SINCE + 40, 0.5], [SINCE + 60, 0.03], [SINCE + 7 * 3600, -0.01]], actor={"mates": [C]})
    _verdict(st, B, SINCE - 500, "qualified", {"status": "passed", "tape_end": SINCE - 600})
    _verdict(st, B, SINCE + 200, "qualified", {"status": "passed", "tape_end": SINCE + 150})
    _record(st, B, [[SINCE + 300, 1.0]])
    _verdict(st, C, SINCE + 400, "qualified", {"status": "passed", "tape_end": SINCE + 300})
    _record(st, C, [[SINCE + 350, 0.2], [SINCE + 500, 0.2]], actor={"mates": [A]})
    _verdict(st, D, SINCE + 1000, "qualified", {"status": "passed", "tape_end": SINCE + 1000})
    _verdict(
        st,
        D,
        SINCE + 90_000,
        "candidate",
        {
            "status": "pending",
            "tape_end": SINCE + 89_000,
            "cold": {"n": 8, "mean": -0.02, "upper": -0.001, "since": SINCE},
        },
    )
    _record(st, D, [[SINCE + 2000, -0.05], [SINCE + 88_000, -0.02]])
    _verdict(st, E, SINCE + 50, "candidate", {"status": "pending", "tape_end": SINCE})
    _record(st, E, [[SINCE + 60, 0.4]])
    return st


def test_copies_after_the_first_pass_counted_once_an_actor():
    out = T.tally(_world(), SINCE, SINCE + 100_000)
    rows = {r["wallet"]: r for r in out["actors"]}
    assert set(rows) == {A, D}  # B passed before SINCE, C is A's companion, E never passed
    a = rows[A]
    assert a["members"] == [A, C] and a["passed_at"] == SINCE + 100 and a["from"] == SINCE + 50
    assert (a["n"], a["sum_sol"], a["wins"]) == (2, pytest.approx(0.02), 1)
    assert a["latest_status"] == "qualified" and a["cold"] is False
    d = rows[D]
    assert (d["n"], d["sum_sol"], d["cold"], d["latest_status"]) == (2, pytest.approx(-0.07), True, "candidate")


def test_totals_days_and_verdict():
    out = T.tally(_world(), SINCE, SINCE + 100_000)
    assert (out["actors_n"], out["copies"], out["up"], out["down"]) == (2, 4, 1, 1)
    assert out["sum_sol"] == pytest.approx(-0.05)
    assert out["mean_sol"] == pytest.approx(-0.0125)
    days = {d["day"]: d for d in out["days"]}
    assert sum(d["n"] for d in days.values()) == 4 and list(days)[-1] == "2026-11-20"
    assert out["days"][-1]["running_sol"] == pytest.approx(-0.05)
    assert "2 actors passed the forward check" in out["verdict"] and "-0.050 SOL" in out["verdict"]
    json.dumps(out)


def test_a_wallet_with_no_record_is_said_not_counted():
    st = _world()
    st._exec("DELETE FROM scores WHERE key=?", (D,))
    out = T.tally(st, SINCE, SINCE + 100_000)
    assert [r["wallet"] for r in out["actors"]] == [A] and out["no_record"] == [D]


def test_nothing_passed_says_so():
    st = Store(":memory:")
    out = T.tally(st, SINCE, SINCE + 10)
    assert out["actors_n"] == 0 and out["copies"] == 0 and "no actor passed" in out["verdict"]


def test_forward_tally_is_a_read_the_tester_may_start():
    from snipetech import cli
    from snipetech.operator import roles
    from snipetech.operator.jobs import RESEARCH_COMMANDS
    from snipetech.ops import ALLOWED

    args = cli.build_parser().parse_args(["forward-tally", "--since", "1795114949", "--json"])
    assert (args.cmd, args.since, args.json) == ("forward-tally", 1795114949.0, True)
    assert "forward-tally" in RESEARCH_COMMANDS and "forward-tally" in ALLOWED
    assert "forward-tally" in roles.ROLE_COMMANDS["tester"]
