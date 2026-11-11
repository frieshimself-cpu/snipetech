"""`stopped-wallets`: what the wallets one rule stops have made since it stopped them (audit of 11 Nov, open items 4
and 8). A verdict is made from the copies before it, so a wallet's copies after its first verdict that only the
crowding rule or the history rule stopped are copies nobody picked on. The study reads them from one lab and writes
no verdict."""

import json

import pytest

from snipetech import ops
from snipetech.analysis.lab_lock import LabBusy
from snipetech.cli import BATCH_COMMANDS, build_parser
from snipetech.config import Config
from snipetech.operator.jobs import RESEARCH_COMMANDS
from snipetech.qualify import stopped as S
from snipetech.service import Service
from snipetech.store import Store

NOW = 1_794_400_000
DAY = 86_400
T0 = NOW - 2 * DAY  # when the verdicts below were made


def _svc(tmp_path):
    data = tmp_path / "data"
    (data / "jobs").mkdir(parents=True, exist_ok=True)
    st = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    st.set_coverage("tape", {"spans": [[NOW - 6 * DAY, NOW]]})
    return Service(cfg, store=st), st, data


def _verdict(st, wallet, at, status, reasons, n=40, mean=0.05, lower=0.04, gap3=None, forward=None, kept=None,
             win_rate=None):  # fmt: skip
    st.put_verdict({"wallet": wallet, "computed_at": at, "landing_pos": "+3x", "n_tape": n, "mean_sol": mean,
                    "lower_sol": lower, "status": status, "next_review": at + DAY, "reasons": reasons,
                    "gap3_pct": gap3, "forward": forward, "kept_armed": kept, "hist_win_rate": win_rate})  # fmt: skip


CROWDED = ["no history from any source", "crowded: gap to the 3rd follower 23.7 % > 15 %"]
WIN_RATE = ["history: win rate 23.7 < 35 %"]


def _lab(copies):
    """A lab that answers with each asked wallet's copies: {wallet: [[the leader's buy ts, SOL the copy made], ...]}."""
    calls = []

    def run(wallets, position):
        calls.append((list(wallets), position))
        return [{"wallet": w, "n": len(copies[w]), "mean": 0.0, "ev_lower": 0.0, "moments": [list(c) for c in copies[w]]}
                for w in wallets if w in copies]  # fmt: skip

    run.calls = calls
    return run


def _study(svc, data, lab, **kw):
    return S.stopped_wallets(svc, svc.cfg, data / "jobs", NOW, lab_runner=lab, **kw)


def test_a_wallet_only_the_crowding_rule_stops_is_followed_from_that_verdict(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "W1", T0, "candidate", CROWDED, n=40, mean=0.05, lower=0.04, gap3=23.7)
        before = [[T0 - 3600 * k, 0.05] for k in range(1, 6)]  # what the verdict was made from
        after = [[T0 + 3600 * k, -0.02] for k in range(1, 7)]  # what nobody picked on
        out = _study(svc, data, _lab({"W1": before + after}))
        g = out["groups"]["crowding"]
        assert (g["wallets"], g["copies"], g["moments"]) == (1, 6, 6)
        assert g["mean_sol"] == pytest.approx(-0.02) and g["win_rate"] == 0.0 and g["lower_sol"] < 0
        assert g["total_sol"] == pytest.approx(-0.12)
        (row,) = out["rows"]
        assert (row["wallet"], row["group"], row["since"]) == ("W1", "crowding", T0)
        assert row["at_verdict"] == {"n": 40, "mean_sol": 0.05, "lower_sol": 0.04, "gap3_pct": 23.7}
        assert row["after"]["n"] == 6 and row["after"]["mean_sol"] == pytest.approx(-0.02)
        assert row["why"] == "crowded: gap to the 3rd follower 23.7 % > 15 %"
    finally:
        st.close()


def test_the_first_such_verdict_counts_not_a_later_one(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "W1", T0, "candidate", CROWDED, gap3=23.7)
        _verdict(st, "W1", T0 + DAY, "candidate", CROWDED, gap3=31.0)  # reviewed again: the same rule, a day on
        copies = [[T0 + 3600, 0.03], [T0 + DAY + 3600, 0.01]]
        out = _study(svc, data, _lab({"W1": copies}))
        assert out["rows"][0]["since"] == T0 and out["rows"][0]["after"]["n"] == 2
    finally:
        st.close()


def test_the_history_rule_counts_only_with_paper_numbers_that_pass(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "HIST", T0, "retired", WIN_RATE, n=75, mean=0.0264, lower=0.0222, gap3=9.0, win_rate=23.7)
        _verdict(st, "BOTH", T0, "retired", WIN_RATE, n=30, mean=0.02, lower=0.01, gap3=20.3, win_rate=32.0)
        _verdict(st, "LOSES", T0, "retired", WIN_RATE, n=75, mean=-0.01, lower=-0.02, win_rate=20.0)
        _verdict(st, "THIN", T0, "retired", WIN_RATE, n=12, mean=0.03, lower=0.01, win_rate=20.0)
        copies = {w: [[T0 + 60, 0.01]] for w in ("HIST", "BOTH", "LOSES", "THIN")}
        lab = _lab(copies)
        out = _study(svc, data, lab)
        assert {r["wallet"]: r["group"] for r in out["rows"]} == {"HIST": "history", "BOTH": "history+crowding"}
        assert sorted(lab.calls[0][0]) == ["BOTH", "HIST"]  # the two others are not measured at all
        assert out["groups"]["history"]["wallets"] == 1 and out["groups"]["history+crowding"]["wallets"] == 1
    finally:
        st.close()


def test_a_wallet_another_rule_stops_is_not_followed(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "TAPE", T0, "retired", ["loses at +3x: mean -0.0100 SOL over 71"], mean=-0.01, lower=-0.02)
        _verdict(st, "VENUE", T0, "retired", ["venue share 0% < 50% (trades mostly off pump.fun)"])
        _verdict(st, "BUNDLE", T0, "retired", ["bundle member in 21 launches"])
        _verdict(st, "FEW", T0, "candidate", ["n 12 < 20 at +3x"], n=12)
        lab = _lab({})
        out = _study(svc, data, lab)
        assert out["rows"] == [] and lab.calls == [] and out["wallets"] == 0
    finally:
        st.close()


def test_picked_wallets_are_the_reference_from_their_first_pick(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        fwd = {"picked_at": T0, "n": 0, "status": "pending"}
        _verdict(st, "PICK", T0, "candidate", ["forward pending: 0 later copies"], forward=fwd, gap3=4.0)
        _verdict(st, "PICK", T0 + DAY, "qualified", ["holds after it was picked"], forward=fwd | {"status": "passed"})
        # kept by the book from before the rules: it never passed them, so it is no pick
        _verdict(st, "KEPT", T0, "qualified", ["no longer qualifies: kept"], kept={"kept": True}, lower=0.005)
        # qualified under the rules before 7 Nov (no forward check existed): another landing, another bar, no reference
        _verdict(st, "OLD", T0 - 5 * DAY, "qualified", [], lower=0.01)
        lab = _lab({"PICK": [[T0 - 60, 0.05], [T0 + 60, 0.02], [T0 + DAY + 60, -0.01]]})
        out = _study(svc, data, lab)
        assert lab.calls[0][0] == ["PICK"]
        (row,) = out["rows"]
        assert (row["wallet"], row["group"], row["since"], row["after"]["n"]) == ("PICK", "picked", T0, 2)
        assert out["groups"]["picked"]["copies"] == 2
    finally:
        st.close()


def test_a_wallet_keeps_the_group_of_its_first_such_verdict(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "W1", T0, "candidate", CROWDED, gap3=23.7)
        _verdict(st, "W1", T0 + DAY, "candidate", ["forward pending"], forward={"picked_at": T0 + DAY}, gap3=12.0)
        out = _study(svc, data, _lab({"W1": [[T0 + 60, 0.01], [T0 + DAY + 60, 0.03]]}))
        (row,) = out["rows"]
        assert (row["group"], row["since"], row["after"]["n"]) == ("crowding", T0, 2)
    finally:
        st.close()


def test_a_group_is_pooled_over_its_wallets_and_says_how_many_are_up(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        for w in ("A", "B", "C"):
            _verdict(st, w, T0, "candidate", CROWDED, gap3=30.0)
        copies = {  # no two of them in the same second
            "A": [[T0 + 60 * k, 0.03] for k in range(1, 7)],  # six copies, up
            "B": [[T0 + 60 * k + 7, -0.01] for k in range(1, 7)],  # six copies, down
            "C": [[T0 + 13, 0.5]],  # one copy: in the pool, too few to be counted as a wallet that is up
        }
        out = _study(svc, data, _lab(copies))
        g = out["groups"]["crowding"]
        assert (g["wallets"], g["with_copies"], g["copies"], g["moments"]) == (3, 3, 13, 13)
        assert g["mean_sol"] == pytest.approx((6 * 0.03 - 6 * 0.01 + 0.5) / 13)
        assert (g["wallets_judged"], g["wallets_up"]) == (2, 1)  # judged: MIN_WALLET_COPIES or more copies since
        assert g["wallet_mean_sol"] == pytest.approx(0.01)  # each judged wallet counts once: (0.03 - 0.01) / 2
        assert g["top_wallet_share"] == pytest.approx(0.5 / (0.18 + 0.06 + 0.5))  # of everything won or lost
    finally:
        st.close()


def test_two_wallets_copies_of_one_moment_count_once(tmp_path):
    """Wallets that buy the same coins in the same second are one bet, not two: the crowded wallets come in sets (four
    of the best five were scored in the same run with 40 to 43 copies each). A pool that counted each would claim
    twice the evidence it has."""
    svc, st, data = _svc(tmp_path)
    try:
        for w in ("A", "B"):
            _verdict(st, w, T0, "candidate", CROWDED, gap3=30.0)
        copies = {
            "A": [[T0 + 100, 0.04], [T0 + 200, -0.02]],
            "B": [[T0 + 100, 0.02], [T0 + 300, 0.03]],
        }  # T0 + 100: both bought in that second
        out = _study(svc, data, _lab(copies))
        g = out["groups"]["crowding"]
        assert (g["copies"], g["moments"]) == (4, 3)
        assert g["mean_sol"] == pytest.approx((0.03 - 0.02 + 0.03) / 3)  # the shared moment at the two copies' mean
        assert g["total_sol"] == pytest.approx(0.04)
    finally:
        st.close()


def test_the_lab_measures_only_those_wallets_at_the_qualify_landing_in_chunks(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        for i in range(S.CHUNK + 5):
            _verdict(st, f"W{i:03d}", T0 + i, "candidate", CROWDED, gap3=20.0)
        _verdict(st, "OTHER", T0, "retired", ["bundle member in 21 launches"])
        lab = _lab({f"W{i:03d}": [[T0 + 3600, 0.01]] for i in range(S.CHUNK + 5)})
        out = _study(svc, data, lab)
        assert [len(w) for w, _ in lab.calls] == [S.CHUNK, 5]
        assert {pos for _, pos in lab.calls} == {"+3x"} and out["landing"] == "+3x"
        assert "OTHER" not in {w for ws, _ in lab.calls for w in ws}
        assert out["wallets"] == S.CHUNK + 5 and out["per_tx_sol"] > 0
    finally:
        st.close()


def test_the_question_comes_before_the_reference_when_there_are_too_many(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        for i in range(4):
            _verdict(st, f"P{i}", T0 - 100 + i, "candidate", ["forward pending"], forward={"picked_at": T0})
        _verdict(st, "CROWD", T0, "candidate", CROWDED, gap3=20.0)
        _verdict(st, "HIST", T0, "retired", WIN_RATE, n=75, lower=0.02, gap3=5.0)
        lab = _lab({})
        out = _study(svc, data, lab, max_wallets=3)
        asked = lab.calls[0][0]
        assert len(asked) == 3 and {"CROWD", "HIST"} <= set(asked)
        assert out["left_out"] == 3  # said, never silently dropped
    finally:
        st.close()


def test_no_verdict_is_written_or_changed(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "W1", T0, "candidate", CROWDED, gap3=23.7)
        rows = st._q("SELECT * FROM verdicts ORDER BY id")
        scores = st._q("SELECT COUNT(*) FROM scores")
        _study(svc, data, _lab({"W1": [[T0 + 60, 0.01]]}))
        assert st._q("SELECT * FROM verdicts ORDER BY id") == rows
        assert st._q("SELECT COUNT(*) FROM scores") == scores
    finally:
        st.close()


def test_a_wallet_the_lab_has_no_copies_for_is_listed_with_none(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "QUIET", T0, "candidate", CROWDED, gap3=23.7)
        out = _study(svc, data, _lab({}))
        (row,) = out["rows"]
        assert row["after"] == {"n": 0}
        g = out["groups"]["crowding"]
        assert (g["wallets"], g["with_copies"], g["copies"]) == (1, 0, 0) and g["mean_sol"] is None
    finally:
        st.close()


def test_with_every_lab_slot_held_it_says_so_and_measures_nothing(tmp_path, monkeypatch):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "W1", T0, "candidate", CROWDED, gap3=23.7)

        def busy(*a, **k):
            raise LabBusy("every lab slot is held")

        monkeypatch.setattr(S, "lab_lock", busy)
        lab = _lab({"W1": [[T0 + 60, 0.01]]})
        out = _study(svc, data, lab, wait=False)
        assert out["deferred"] is True and "lab slot" in out["reason"] and lab.calls == []
    finally:
        st.close()


def test_the_command_is_wired_as_a_research_job():
    args = build_parser().parse_args(["stopped-wallets", "--max", "40", "--json"])
    assert args.cmd == "stopped-wallets" and args.max == 40 and args.json is True
    assert "stopped-wallets" in ops.ALLOWED and "stopped-wallets" in RESEARCH_COMMANDS
    assert "stopped-wallets" in BATCH_COMMANDS  # it runs a lab: below the recorder


def test_the_output_is_plain_json(tmp_path):
    svc, st, data = _svc(tmp_path)
    try:
        _verdict(st, "W1", T0, "candidate", CROWDED, gap3=23.7)
        out = _study(svc, data, _lab({"W1": [[T0 + 60, 0.01], [T0 + 120, -0.03]]}))
        assert json.loads(json.dumps(out))["groups"]["crowding"]["copies"] == 2
    finally:
        st.close()
