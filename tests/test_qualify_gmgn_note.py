"""How copied a wallet is, said in its verdict and decided by nothing.

GMGN counts the users who follow a wallet (`follow_count`; the wallet we track as Cupseyy: 2,147 on 12 Nov) and tags
it (kol, a trading app, a bot). The verdict says so in its reasons, as information: the owner has set no rule on it,
and a gate needs the owner's rule first. So a verdict with the note and one without are the same verdict: the same
status, gate, score, numbers and review date."""

import pytest

from snipetech import gmgn as G
from snipetech.config import Config
from snipetech.qualify import gates
from snipetech.qualify.run import run_qualify
from tests.test_qualify_run import NOW, _svc, _tape

STRONG = {
    "trades": 300,
    "win_rate": 64.0,
    "realized_usd": 2500.0,
    "max_single_pnl_usd": 500.0,
    "tokens": 120,
    "trading_days": 40,
}
ROWS = {
    "GOOD": {"n": 12, "mean": 0.02, "ev_lower": 0.005, "win_rate": 0.6, "total": 0.24, "exit": "TP+40%"},
    "WEAK": {"n": 12, "mean": 0.02, "ev_lower": 0.005, "win_rate": 0.6, "total": 0.24, "exit": "TP+40%"},
    "BAD": {"n": 30, "mean": -0.01, "ev_lower": -0.02, "win_rate": 0.3, "total": -0.3, "exit": "TP+40%", "mates": 2},
    "PLAIN": {"n": 12, "mean": 0.02, "ev_lower": 0.005, "win_rate": 0.6, "total": 0.24, "exit": "TP+40%"},
}
WALLETS = ("GOOD", "WEAK", "BAD", "PLAIN", "THIN")  # THIN has no buy on the tape: judged without a lab


def _run(tmp_path, name, with_gmgn):
    (tmp_path / name).mkdir()
    svc, st, data = _svc(tmp_path / name)
    try:
        _tape(st, ["GOOD", "WEAK", "BAD", "PLAIN"])
        for w in WALLETS:
            hist = dict(STRONG, win_rate=50.0) if w == "WEAK" else STRONG
            st.upsert_candidate(w, "solanatracker", hist, NOW - 3600)
        if with_gmgn:
            # the discovery source's row for GOOD and for THIN; a look-up (`gmgn wallet`, `tips --gmgn`) for WEAK and
            # BAD; nothing for PLAIN
            st.upsert_candidate(
                "GOOD", "gmgn", {"follow_count": 2147, "tags": ["kol", "padre"], "scanned_at": NOW - 60}, NOW - 60
            )
            st.upsert_candidate("THIN", "gmgn", {"follow_count": 12, "tags": [], "scanned_at": NOW - 60}, NOW - 60)
            G.remember_wallet(st, {"wallet": "W" * 44, "follow_count": 1, "tags": []}, now=NOW - 50)  # somebody else
            st.set_coverage(G.WALLET_KEY + "WEAK", {"ts": NOW - 40, "follow_count": 48070, "tags": ["sandwich_bot"]})
            st.set_coverage(G.WALLET_KEY + "BAD", {"ts": NOW - 40, "follow_count": 0, "tags": []})
        else:  # the same candidates from the same sources, without the two fields
            st.upsert_candidate("GOOD", "gmgn", {"scanned_at": NOW - 60}, NOW - 60)
            st.upsert_candidate("THIN", "gmgn", {"scanned_at": NOW - 60}, NOW - 60)
        runner = lambda wallets, pos: [dict(ROWS[w], wallet=w) for w in wallets if w in ROWS]
        out = run_qualify(svc, svc.cfg, data / "jobs", NOW, lab_runner=runner)
        out.pop("secs", None)
        return out, {v["wallet"]: v for v in st.latest_verdicts()}, {w: st.get_score("qualify", w) for w in WALLETS}
    finally:
        st.close()


def test_the_verdict_says_how_followed_a_wallet_is_and_no_status_changes(tmp_path):
    out_a, with_note, scores = _run(tmp_path, "a", True)
    out_b, without, _ = _run(tmp_path, "b", False)
    assert out_a == out_b and set(with_note) == set(without) == set(WALLETS)
    assert {v["status"] for v in with_note.values()} == {"candidate", "retired"}  # more than one outcome is compared
    for w in WALLETS:
        a, b = dict(with_note[w]), dict(without[w])
        ra, rb = a.pop("reasons"), b.pop("reasons")
        a.pop("id"), b.pop("id")
        assert a == b, w  # status, score, gate numbers, review date, forward check, copy plan: all the same
        note = [r for r in ra if "GMGN" in r]
        assert [r for r in ra if "GMGN" not in r] == rb, w  # every reason the gates gave, in their order
        assert ra[: len(rb)] == rb  # and the note comes after them
        assert len(note) == (0 if w == "PLAIN" else 1), w
    said = {w: next((r for r in with_note[w]["reasons"] if "GMGN" in r), None) for w in WALLETS}
    assert said["GOOD"].startswith("followed by 2,147 GMGN users (GMGN tags: kol, padre)")
    assert "information only" in said["GOOD"] and "no gate" in said["GOOD"]
    assert said["WEAK"].startswith("followed by 48,070 GMGN users (GMGN tags: sandwich_bot)")
    assert said["BAD"].startswith("followed by 0 GMGN users;") and said["THIN"].startswith("followed by 12 GMGN users")
    assert said["PLAIN"] is None
    # kept with the wallet's numbers too, for whoever reads the score record
    assert scores["GOOD"]["gmgn"] == {"follow_count": 2147, "tags": ["kol", "padre"], "ts": NOW - 60}
    assert scores["WEAK"]["gmgn"]["follow_count"] == 48070 and "gmgn" not in scores["PLAIN"]


def test_the_note_takes_the_newer_of_the_discovery_row_and_the_look_up():
    stats = {"gmgn": {"follow_count": 10, "tags": ["kol"], "scanned_at": 1000}}
    assert gates.followed_by(stats, None) == {"follow_count": 10, "tags": ["kol"], "ts": 1000}
    newer = {"follow_count": 25, "tags": ["kol", "axiom"], "ts": 2000}
    assert gates.followed_by(stats, newer) == {"follow_count": 25, "tags": ["kol", "axiom"], "ts": 2000}
    assert gates.followed_by(stats, {"follow_count": 5, "tags": [], "ts": 500})["follow_count"] == 10
    assert gates.followed_by({}, None) is None and gates.followed_by({"gmgn": {"tags": ["kol"]}}, None) is None
    assert gates.followed_note({"follow_count": 1, "tags": []}).startswith("followed by 1 GMGN user;")
    assert gates.followed_note(None) is None


def test_no_gate_and_no_score_reads_the_follow_count():
    """`verdict_for`, `score`, `history_gate`, `forward_gate` and `keep_armed` take no follower count: the note is put
    on the finished verdict."""
    import inspect

    for fn in (gates.verdict_for, gates.score, gates.history_gate, gates.forward_gate, gates.keep_armed,
               gates.normalise_history):  # fmt: skip
        src = inspect.getsource(fn)
        assert "follow_count" not in src and "followed" not in src and "gmgn" not in src.lower(), fn.__name__
    v = {"wallet": "X", "status": "qualified", "score": 0.5, "gate": None, "reasons": ["a", "b"]}
    out = gates.with_followed_note(v, {"follow_count": 2147, "tags": ["kol"], "ts": 1})
    assert out["reasons"][:2] == ["a", "b"] and len(out["reasons"]) == 3 and v["reasons"] == ["a", "b"]
    assert {k: out[k] for k in ("status", "score", "gate")} == {"status": "qualified", "score": 0.5, "gate": None}
    assert gates.with_followed_note(v, None) is v
    assert Config().qualify is not None and not any("follow" in f for f in vars(Config().qualify))  # no setting either
    with pytest.raises(TypeError):
        gates.verdict_for("X", {}, None, None, {}, True, [], Config().qualify, 0, follow_count=1)
