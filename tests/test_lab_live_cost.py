"""Every lab scores a rule at what it costs live (research round 1, 7 Nov: "P1: score every lab at live cost and with
OKTech's watch delay", docs/research/2026-11-07-round-1.md).

Every lab of round 1 charged the lab's flat default of 0.0035 SOL a transaction, 0.007 a round trip, against about
0.009 live (six job records read; none set --per-tx, and the research round's lab step never sets one), so every mean
was 0.002 SOL a trade too kind, 0.003 with a partial take profit. The never-stops loop trades whatever passes paper
without asking again, so a lab that flatters a rule is a live risk. `snipetech lab`, the command the agents' hypothesis
labs and the research rounds run, now charges what `paper freeze`, the qualify lab and the strategy search charge: the
measured cost of a transaction (qualify.run.lab_tx_cost: half the low bid's measured round trip, the flat 0.0035
until it is measured). --per-tx still overrides it, and the output says which cost it charged and where it came from.
"""

import json

import pytest

from snipetech import cli
from snipetech.store import Store

from .test_lab import NOW
from .test_lab_hypotheses import HUNT_JSON, _protocol_command, _tape_db


def _measure_low_bid(db, n: int = 40) -> None:
    """The measure stage's low bid (arm "control"): 0.0035 SOL of fees a side and 0.001 of failed duplicate sends a
    round trip, 0.008 SOL in all, so 0.004 a transaction; `n` measured fills (the cost counts from 30)."""
    st = Store(db)
    try:
        st.put_bid_model({"arm": "control", "n": n, "failed_n": 0, "mean_fee_sol": 0.0035, "mean_overhead_sol": 0.001,
                          "updated_at": NOW})  # fmt: skip
    finally:
        st.close()


def _lab(capsys, db, *flags: str) -> dict:
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", *flags, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_the_lab_charges_the_measured_cost_of_a_transaction_by_default(tmp_path, capsys):
    db = _tape_db(tmp_path)
    _measure_low_bid(db)
    out = _lab(capsys, db)
    assert out["per_tx_sol"] == pytest.approx(0.004) and out["per_tx_source"] == "measured"
    told = _lab(capsys, db, "--per-tx", "0.004")
    assert told["strategies"] == out["strategies"]  # the very numbers a lab told to charge 0.004 gives
    assert cli.build_parser().parse_args(["lab"]).per_tx is None  # no flat number hides behind the flag any more


def test_per_tx_still_overrides_the_measured_cost_and_says_so(tmp_path, capsys):
    db = _tape_db(tmp_path)
    _measure_low_bid(db)
    measured = _lab(capsys, db)
    told = _lab(capsys, db, "--per-tx", "0.006")
    assert told["per_tx_sol"] == pytest.approx(0.006) and told["per_tx_source"] == "flag"
    (m,), (t,) = measured["strategies"], told["strategies"]
    assert t["train_mean"] < m["train_mean"] - 0.003  # every exit pays 0.002 more a transaction, two or three of them


def test_before_the_low_bid_is_measured_the_lab_says_it_charged_the_flat_cost(tmp_path, capsys):
    db = _tape_db(tmp_path)
    out = _lab(capsys, db)
    assert out["per_tx_sol"] == pytest.approx(0.0035) and out["per_tx_source"] == "flat"
    _measure_low_bid(db, n=20)  # 20 fills: too few to trust; the flat cost still, and the output says so
    out = _lab(capsys, db)
    assert out["per_tx_sol"] == pytest.approx(0.0035) and out["per_tx_source"] == "flat"


def test_the_analysts_hypothesis_lab_charges_the_measured_cost(tmp_path, capsys):
    """The analyst runs the prompt's own lab command as written, with no --per-tx: it charges the measured cost."""
    db = _tape_db(tmp_path)
    _measure_low_bid(db)
    assert cli.main(["--db", str(db), *_protocol_command("lab", HUNT_JSON)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["per_tx_sol"] == pytest.approx(0.004) and out["per_tx_source"] == "measured"


def test_the_lab_text_says_what_a_transaction_cost(tmp_path, capsys):
    db = _tape_db(tmp_path)
    _measure_low_bid(db)
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1"]) == 0
    text = capsys.readouterr().out
    assert "0.004 SOL a transaction (0.008 a round trip), measured" in text
    assert cli.main(["--db", str(db), "lab", "--trigger", HUNT_JSON, "--hours", "1", "--per-tx", "0.0045"]) == 0
    assert "0.0045 SOL a transaction (0.009 a round trip), as --per-tx says" in capsys.readouterr().out
