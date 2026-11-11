"""The agents' memory between turns: lessons (operator/lessons.md, append-only, dated, each with its evidence and n)
and ideas (operator/ideas.json, written atomically). Code only; nothing here trades or changes a setting."""

import json

import pytest

from snipetech.operator import learning as L
from snipetech.operator.paths import OperatorPaths

NOW = 1_793_923_200 + 18 * 3600  # Friday 6 Nov 2026, 18:00 UTC
H = 3600


@pytest.fixture
def paths(tmp_path):
    return OperatorPaths(tmp_path / "data").ensure()


def test_a_lesson_is_one_dated_line_with_its_evidence_and_n(paths):
    lesson = L.add_lesson(paths, "Crowded leaders lose at our landing", "4 of 5 losses landed 2-4 trades behind", 5,
                          source="analyst", now=NOW)  # fmt: skip
    assert lesson == {"ts": "2026-11-06T18:00:00Z", "source": "analyst", "n": 5,
                      "text": "Crowded leaders lose at our landing", "evidence": "4 of 5 losses landed 2-4 trades behind"}  # fmt: skip
    lines = (paths.root / "lessons.md").read_text().splitlines()
    assert lines[-1] == ("- 2026-11-06T18:00:00Z [analyst, n=5] Crowded leaders lose at our landing | evidence: "
                         "4 of 5 losses landed 2-4 trades behind")  # fmt: skip


def test_lessons_are_append_only_and_read_back_newest_last(paths):
    for i in range(12):
        L.add_lesson(paths, f"lesson {i}", f"fills {i}", i + 1, source="operator", now=NOW + i * 60)
    before = (paths.root / "lessons.md").read_text()
    L.add_lesson(paths, "lesson 12", "fills 12", 13, source="strategist", now=NOW + 12 * 60)
    after = (paths.root / "lessons.md").read_text()
    assert after.startswith(before)  # nothing earlier is ever rewritten
    last = L.read_lessons(paths, 10)
    assert [x["text"] for x in last] == [f"lesson {i}" for i in range(3, 13)]
    assert last[-1]["source"] == "strategist" and last[-1]["n"] == 13 and last[0]["ts"] == "2026-11-06T18:03:00Z"


def test_a_lesson_needs_text_evidence_and_a_count(paths):
    for text, evidence, n in (("", "x", 3), ("x", "", 3), ("x", "y", 0), ("x", "y", "many")):
        with pytest.raises(ValueError):
            L.add_lesson(paths, text, evidence, n, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="300"):
        L.add_lesson(paths, "x" * 301, "y", 3, source="analyst", now=NOW)
    assert L.read_lessons(paths, 10) == []


def test_a_lesson_never_carries_a_secret(paths, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:telegram-secret-value")
    keypair = "4" * 88  # a 64-byte key (or a whole signature) in base58
    for text in ("key sk-ant-api03-abcdefghij", "token 123456:telegram-secret-value leaked", f"wallet {keypair}",
                 "api_key=abcdefgh12345678", "-----BEGIN PRIVATE KEY-----"):  # fmt: skip
        with pytest.raises(ValueError, match="secret"):
            L.add_lesson(paths, "a lesson", text, 3, source="analyst", now=NOW)
    # a wallet or a mint is public and fine as evidence
    L.add_lesson(
        paths,
        "a lesson",
        "leader 6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at sold into us",
        1,
        source="analyst",
        now=NOW,
    )
    assert len(L.read_lessons(paths, 10)) == 1


def test_the_same_lesson_is_not_written_twice(paths):
    L.add_lesson(paths, "Fees ate the small wins", "6 of 9 wins under 0.009 SOL", 9, source="analyst", now=NOW)
    again = L.add_lesson(paths, "Fees  ate the small wins", "same", 9, source="postmortem", now=NOW + H)
    assert again is None and len(L.read_lessons(paths, 10)) == 1


def test_a_pipe_in_the_text_cannot_break_the_line_format(paths):
    L.add_lesson(paths, "exit a | exit b", "n 3 | n 4", 7, source="analyst", now=NOW)
    (x,) = L.read_lessons(paths, 10)
    assert x["text"] == "exit a / exit b" and x["evidence"] == "n 3 / n 4" and x["n"] == 7


IDEA = {"title": "Copy only leader buys of 0.5 SOL or more", "why": "7 of 9 losses copied buys under 0.3 SOL (n 9)",
        "test": "lab run --by-wallet with min_copy_sol 0.5 on the last 72 h", "expected": "+0.004 SOL a trade",
        "kind": "research"}  # fmt: skip


def test_an_idea_is_stored_with_its_evidence_test_and_owner_flag(paths):
    idea = L.upsert_idea(paths, IDEA, source="analyst", now=NOW)
    assert idea == {"id": "I1", **IDEA, "status": "open", "owner_needed": False, "priority": 3,
                    "created": "2026-11-06T18:00:00Z", "updated": "2026-11-06T18:00:00Z", "source": "analyst"}  # fmt: skip
    assert json.loads((paths.root / "ideas.json").read_text()) == [idea]
    assert not list(paths.root.glob("*.tmp"))  # written to a temporary file and moved into place


def test_an_idea_is_updated_by_id_or_by_its_title_never_duplicated(paths):
    L.upsert_idea(paths, IDEA, source="analyst", now=NOW)
    second = L.upsert_idea(paths, IDEA | {"title": "Re-qualify at +9", "kind": "config"}, source="analyst", now=NOW)
    assert second["id"] == "I2"
    moved = L.upsert_idea(paths, {"id": "I1", "status": "testing", "why": "job 20261106-1810 started"},
                          source="postmortem", now=NOW + H)  # fmt: skip
    assert moved["status"] == "testing" and moved["why"] == "job 20261106-1810 started"
    assert moved["created"] == "2026-11-06T18:00:00Z" and moved["updated"] == "2026-11-06T19:00:00Z"
    assert moved["test"] == IDEA["test"] and moved["source"] == "analyst"  # what was not given is kept
    same_title = L.upsert_idea(paths, {"title": "copy only leader buys of 0.5 SOL or MORE", "priority": 5},
                               source="operator", now=NOW + 2 * H)  # fmt: skip
    assert same_title["id"] == "I1" and same_title["priority"] == 5 and same_title["status"] == "testing"
    assert [i["id"] for i in L.list_ideas(paths, status="all")] == ["I1", "I2"]
    with pytest.raises(ValueError, match="I9"):
        L.upsert_idea(paths, {"id": "I9", "status": "adopted"}, source="analyst", now=NOW)


def test_live_tests_and_code_changes_always_need_the_owner(paths):
    live = L.upsert_idea(paths, IDEA | {"title": "Copy 9WzD live at 0.1 SOL", "kind": "live-test", "owner_needed": False},
                         source="analyst", now=NOW)  # fmt: skip
    code = L.upsert_idea(paths, IDEA | {"title": "Read the leader's sells from the tape", "kind": "code"},
                         source="analyst", now=NOW)  # fmt: skip
    outside = L.upsert_idea(paths, IDEA | {"title": "Fill 2 SOL per copy", "kind": "config", "owner_needed": True},
                            source="analyst", now=NOW)  # fmt: skip
    assert live["owner_needed"] and code["owner_needed"] and outside["owner_needed"]
    again = L.upsert_idea(paths, {"id": live["id"], "owner_needed": False}, source="analyst", now=NOW)
    assert again["owner_needed"] is True


def test_a_new_idea_needs_every_field_and_valid_choices(paths):
    for missing in ("title", "why", "test", "expected", "kind"):
        with pytest.raises(ValueError, match=missing):
            L.upsert_idea(paths, {k: v for k, v in IDEA.items() if k != missing}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="kind"):
        L.upsert_idea(paths, IDEA | {"kind": "trade"}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="status"):
        L.upsert_idea(paths, IDEA | {"status": "done"}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="priority"):
        L.upsert_idea(paths, IDEA | {"priority": 9}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="secret"):
        L.upsert_idea(paths, IDEA | {"why": "key sk-ant-api03-abcdefghij"}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="600"):
        L.upsert_idea(paths, IDEA | {"why": "x" * 601}, source="analyst", now=NOW)
    with pytest.raises(ValueError, match="size"):
        L.upsert_idea(paths, IDEA | {"size": 2.0}, source="analyst", now=NOW)
    assert L.list_ideas(paths, status="all") == []


def test_open_ideas_are_ranked_and_closed_ones_left_out(paths):
    def put(title, at=NOW, **kw):
        return L.upsert_idea(paths, IDEA | {"title": title} | kw, source="analyst", now=at)

    put("low", priority=1)
    put("high but needs the owner", priority=5, kind="live-test")
    put("high", priority=5)
    put("high and being tested", priority=5, status="testing")
    put("done", priority=5, status="adopted")
    put("dropped", priority=5, status="rejected")
    put("high and newer", priority=5, at=NOW + H)
    ranked = [i["title"] for i in L.list_ideas(paths)]
    assert ranked == ["high and being tested", "high and newer", "high", "high but needs the owner", "low"]
    assert [i["title"] for i in L.list_ideas(paths, status="adopted")] == ["done"]


def test_a_broken_ideas_file_is_kept_aside_not_lost(paths):
    (paths.root / "ideas.json").write_text("{not json")
    assert L.list_ideas(paths) == []
    L.upsert_idea(paths, IDEA, source="analyst", now=NOW)
    kept = list(paths.root.glob("ideas.json.broken-*"))
    assert len(kept) == 1 and kept[0].read_text() == "{not json"
    assert [i["id"] for i in L.list_ideas(paths)] == ["I1"]


def test_the_ideas_a_turn_added_or_moved_are_read_back_best_first(paths):
    """The owner's post-mortem summary names the analyst's top idea of that turn: the open ideas updated since it
    started, the ones it added first (best first), then older ones it only moved or changed; ideas it did not touch
    and closed ones are not this turn's. Review, 7 Nov: an older idea moved to testing ranked above the idea the turn
    had just added, so the owner read it as the change the post-mortem suggests."""
    old = L.upsert_idea(paths, IDEA | {"title": "Older idea", "priority": 5}, source="analyst", now=NOW - 3600)
    low = L.upsert_idea(paths, IDEA | {"title": "Low idea", "priority": 2}, source="postmortem", now=NOW + 10)
    top = L.upsert_idea(paths, IDEA | {"title": "Top idea", "priority": 4}, source="postmortem", now=NOW + 20)
    L.upsert_idea(paths, IDEA | {"title": "Closed idea", "status": "rejected"}, source="postmortem", now=NOW + 30)
    assert [i["id"] for i in L.ideas_since(paths, NOW)] == [top["id"], low["id"]]
    L.upsert_idea(paths, {"id": old["id"], "status": "testing"}, source="postmortem", now=NOW + 40)  # moved this turn
    assert [i["id"] for i in L.ideas_since(paths, NOW)] == [top["id"], low["id"], old["id"]]
    assert [i["id"] for i in L.ideas_since(paths, NOW + 25)] == [old["id"]]  # a turn that only moved an idea


def test_an_idea_edited_by_hand_never_breaks_a_turn(paths):
    """Every turn's message is built from ideas.json: an idea someone edited by hand (a field missing, a priority in
    words) still lists, ranks and shows, so a bad edit can never stop the rounds."""
    (paths.root / "ideas.json").write_text(json.dumps([{"id": "I7", "title": "by hand", "priority": "high", "status": "open"},
                                                       {"id": "I8", "status": "open", "priority": 4}]))  # fmt: skip
    assert [i["id"] for i in L.list_ideas(paths)] == ["I8", "I7"]
    text = L.context(paths)
    assert "I7" in text and "by hand" in text and "I8" in text
    assert L.digest_lines(paths, NOW - 6 * H, NOW)[-2:] == ["- I8", "- by hand"]


def test_the_idea_list_is_capped_by_dropping_the_oldest_closed_idea(paths, monkeypatch):
    monkeypatch.setattr(L, "MAX_IDEAS", 3)
    L.upsert_idea(paths, IDEA | {"title": "a", "status": "rejected"}, source="analyst", now=NOW)
    L.upsert_idea(paths, IDEA | {"title": "b"}, source="analyst", now=NOW + 60)
    L.upsert_idea(paths, IDEA | {"title": "c"}, source="analyst", now=NOW + 120)
    L.upsert_idea(paths, IDEA | {"title": "d"}, source="analyst", now=NOW + 180)
    assert [i["title"] for i in L.list_ideas(paths, status="all")] == ["b", "c", "d"]
    with pytest.raises(ValueError, match="3 ideas"):
        L.upsert_idea(paths, IDEA | {"title": "e"}, source="analyst", now=NOW + 240)


def test_every_turn_sees_the_last_ten_lessons_and_the_top_five_open_ideas(paths):
    for i in range(12):
        L.add_lesson(paths, f"lesson L{i:02d}", f"evidence E{i:02d}", i + 1, source="analyst", now=NOW + i)
    for i, prio in enumerate((1, 2, 3, 4, 5, 1, 2)):
        L.upsert_idea(paths, IDEA | {"title": f"plan-{'ABCDEFG'[i]}", "priority": prio}, source="analyst", now=NOW)
    L.upsert_idea(paths, IDEA | {"title": "plan-X", "priority": 5, "status": "rejected"}, source="analyst", now=NOW)
    text = L.context(paths)
    assert "L01" not in text and all(f"L{i:02d}" in text and f"E{i:02d}" in text for i in range(2, 12))
    assert text.index("L02") < text.index("L11")  # newest last
    shown = [p for p in ("plan-E", "plan-D", "plan-C", "plan-B", "plan-G") if p in text]
    assert shown == ["plan-E", "plan-D", "plan-C", "plan-B", "plan-G"]  # best first
    assert text.index("plan-E") < text.index("plan-D") < text.index("plan-G")
    assert "plan-A" not in text and "plan-F" not in text and "plan-X" not in text


def test_every_turn_is_shown_the_ideas_no_job_has_tested_yet(paths):
    """11 Nov: 14 open ideas needed no yes from the owner and no job had ever tested them, two of them the owner's own
    from the day before (the dip buy, the migration snipe on coins that bond at once). The message's five best ideas
    were the ones already under test, the newest first, the same five turn after turn, and 20 of the day's 25
    analyses started no experiment. The untested ones are listed apart: the highest priority first, then the one that
    has waited longest, each with its test."""
    L.upsert_idea(paths, IDEA | {"title": "under test", "priority": 5, "status": "testing"}, source="analyst", now=NOW)
    L.upsert_idea(paths, IDEA | {"title": "dip buy", "priority": 2, "test": "lab momentum on the fall"}, source="cloud",
                  now=NOW - 30 * H)  # fmt: skip
    L.upsert_idea(paths, IDEA | {"title": "instant bond", "priority": 2}, source="cloud", now=NOW - 20 * H)
    L.upsert_idea(paths, IDEA | {"title": "newer and better", "priority": 4}, source="analyst", now=NOW - H)
    L.upsert_idea(paths, IDEA | {"title": "owner decides", "priority": 5, "kind": "live-test"}, source="analyst",
                  now=NOW - 40 * H)  # fmt: skip
    L.upsert_idea(paths, IDEA | {"title": "closed", "status": "rejected"}, source="analyst", now=NOW - 50 * H)
    L.upsert_idea(paths, IDEA | {"title": "fourth in line", "priority": 1}, source="analyst", now=NOW - 60 * H)
    assert [i["title"] for i in L.untested(paths)] == ["newer and better", "dip buy", "instant bond"]  # three at most
    text = L.context(paths)
    block = text[text.index("Not tested yet") :]
    assert block.index("newer and better") < block.index("dip buy") < block.index("instant bond")
    assert "lab momentum on the fall" in block and "open since 2026-11-05" in block  # the dip buy: its test, its wait
    for other in ("under test", "owner decides", "closed", "fourth in line"):
        assert other not in block, other


def test_the_untested_ideas_show_their_tests_whole(paths):
    """What the turn needs from an untested idea is its test: the line has room for the longest one (400 characters),
    where the best-ideas lines above are cut at 240."""
    test = "start_job lab with " + "a kol trigger on the group " * 13 + "END"
    assert 350 < len(test) <= 400
    L.upsert_idea(paths, IDEA | {"title": "long test", "test": test}, source="cloud", now=NOW)
    text = L.context(paths)
    assert test in text[text.index("Not tested yet") :]


def test_with_every_idea_tested_or_the_owners_nothing_is_listed_as_untested(paths):
    L.upsert_idea(paths, IDEA | {"title": "under test", "status": "testing"}, source="analyst", now=NOW)
    L.upsert_idea(paths, IDEA | {"title": "owner decides", "kind": "code"}, source="analyst", now=NOW)
    assert L.untested(paths) == [] and "Not tested yet" not in L.context(paths)


def test_what_every_turn_sees_stays_small(paths):
    for i in range(12):  # words, not one long run of letters (that reads as a key to the secret check)
        L.add_lesson(paths, f"{i:02d} " + "tx " * 96, "ev " * 133, 3, source="analyst", now=NOW + i)
    for i in range(6):
        L.upsert_idea(paths, IDEA | {"title": f"{i} " + "xy " * 36, "why": "wh " * 199, "test": "te " * 133},
                      source="analyst", now=NOW)  # fmt: skip
    text = L.context(paths)
    assert len(text) <= L.CONTEXT_MAX_CHARS and "11 tx tx" in text  # every line cut short, the newest still there


def test_with_nothing_learned_yet_the_turn_is_told_so(paths):
    text = L.context(paths)
    assert "nothing yet" in text and "none yet" in text


def test_the_digest_says_what_was_learned_since_the_last_one_and_what_is_tested_next(paths):
    L.add_lesson(paths, "an old lesson", "e", 2, source="analyst", now=NOW - 7 * H)
    L.add_lesson(paths, "Crowded leaders lose at our landing", "4 of 5 losses 2-4 behind", 5, source="postmortem",
                 now=NOW - 2 * H)  # fmt: skip
    L.upsert_idea(paths, IDEA | {"priority": 4}, source="analyst", now=NOW)
    L.upsert_idea(paths, IDEA | {"title": "Copy 9WzD live at 0.1 SOL", "kind": "live-test", "priority": 5},
                  source="analyst", now=NOW)  # fmt: skip
    lines = L.digest_lines(paths, NOW - 6 * H, NOW)
    assert lines[0] == "What it learned since 12:00 UTC:"
    assert "- Crowded leaders lose at our landing (5 cases)" in lines and not any("old lesson" in x for x in lines)
    i = lines.index("Testing next:")
    assert lines[i + 1 :] == [
        "- Copy 9WzD live at 0.1 SOL (needs your yes)",
        "- Copy only leader buys of 0.5 SOL or more",
    ]


def test_a_quiet_digest_says_nothing_new_was_learned(paths):
    assert L.digest_lines(paths, NOW - 6 * H, NOW) == ["What it learned: nothing new since 12:00 UTC",
                                                        "Testing next: no open ideas yet"]  # fmt: skip


def _sell(st, sig, ts, wallet="W", **kw):
    st.put_fill(
        {"sig": sig, "ts": int(ts), "wallet": wallet, "mint": f"M{sig}", "side": "sell", "sol": 0.19, "tokens": 1.0}
        | kw
    )


def test_new_round_trips_count_from_the_newest_sell_the_last_post_mortem_saw(paths):
    from snipetech.store import Store

    st = Store(":memory:")
    for sig, ts in (("s0", NOW - 30 * H), ("s1", NOW - 5 * H), ("s2", NOW - 4 * H), ("s3", NOW - H)):
        _sell(st, sig, ts)
    st.put_fill({"sig": "b0", "ts": NOW - 2 * H, "wallet": "W", "mint": "M9", "side": "buy", "sol": 0.2, "tokens": 1.0})
    _sell(st, "x0", NOW - 2 * H, wallet="OTHER")
    t = L.trips_since_postmortem(st, "W", paths, NOW)
    assert t == {"n": 3, "since_ts": NOW - 24 * H, "newest_ts": NOW - H}  # never reviewed: the last day counts
    assert L.last_postmortem_ts(paths) is None
    L.mark_postmortem(paths, NOW, t, "postmortem")
    assert L.last_postmortem_ts(paths) == NOW and L.trips_since_postmortem(st, "W", paths, NOW)["n"] == 0
    _sell(st, "s4", NOW - H + 30)  # measured after the post-mortem, sold after the newest sell it saw: new
    later = L.trips_since_postmortem(st, "W", paths, NOW + 600)
    assert later == {"n": 1, "since_ts": NOW - H, "newest_ts": NOW - H + 30}
    L.mark_postmortem(paths, NOW + 600, L.trips_since_postmortem(st, "W", paths, NOW + 600), "analyst")
    L.mark_postmortem(paths, NOW + 900, L.trips_since_postmortem(st, "W", paths, NOW + 900), "analyst")  # none new
    assert L.trips_since_postmortem(st, "W", paths, NOW + 900)["since_ts"] == NOW - H + 30
    st.close()


def test_a_round_trip_reads_as_its_landing_its_exit_its_money_and_the_verdicts_prediction(paths):
    from snipetech.store import Store

    st = Store(":memory:")
    st.put_fill({"sig": "B" * 88, "ts": NOW - 400, "wallet": "W", "wallet_copied": "LEAD", "mint": "M1", "side": "buy", "sol": 0.2,
                 "tokens": 1.0, "trades_behind": 3, "price_gap_pct": 31.0, "pos_in_slot": 4, "bid_arm": "control",
                 "leader_progress": 0.0846})  # fmt: skip
    _sell(st, "S" * 88, NOW - 100, buy_sig="B" * 88, pnl_sol=-0.041, exit_reason="copy_exit", exit_name="lab", crossed=0,
          twin_fee_sol=0.0021)  # fmt: skip
    _sell(st, "old", NOW - 9 * H, pnl_sol=0.01)
    st.upsert_candidate("LEAD", "tape", {}, NOW - 86400)
    forward = {"picked_at": NOW - 30 * H, "tape_hours": 29.0, "n": 21, "mean": 0.003, "lower": 0.0004, "win_rate": 0.52,
               "status": "passed", "min_n": 20, "min_hours": 6.0}  # fmt: skip
    kept = {"armed_on": NOW - 40 * H, "kept": False}  # copied before the 7 Nov rules, meeting them now
    st.put_verdict({"wallet": "LEAD", "computed_at": NOW - 3600, "status": "qualified", "score": 0.004, "n_tape": 30,
                    "mean_sol": 0.009, "lower_sol": 0.002, "landing_pos": "+3x",
                    "copy_plan": {"min_leader_sol": 0.5, "leader_style": "dumper"}, "forward": forward,
                    "kept_armed": kept})  # fmt: skip
    out = L.round_trips(st, "W", NOW - 6 * H)
    s = out["summary"]  # the whole result counts the failed duplicate sell once (7 Nov: it was left out)
    assert (s["trips"], s["wins"], s["losses"], s["pnl_sol"], s["recorded_sol"]) == (1, 0, 1, -0.0431, -0.041)
    (trip,) = out["trips"]
    assert trip["leader"] == "LEAD" and trip["held_secs"] == 300 and trip["trades_behind"] == 3
    assert trip["price_gap_pct"] == 31.0 and trip["exit_reason"] == "copy_exit" and trip["twin_fee_sol"] == 0.0021
    assert trip["leader_progress"] == 0.0846  # where on its curve the leader bought, beside where we landed
    assert trip["verdict"]["mean_sol"] == 0.009 and trip["verdict"]["copy_plan"]["min_leader_sol"] == 0.5
    assert trip["verdict"]["forward"] == forward  # what its copies made after the pick, next to what the trip made
    assert (
        trip["verdict"]["kept_armed"] == kept
    )  # whether it is copied only because the book copied it before the rules
    assert trip["sig"] == "S" * 12 and "B" * 20 not in json.dumps(out)  # short signatures only
    st.close()


def test_the_post_mortem_charges_the_duplicates_of_a_sell_with_no_buy_on_record(paths):
    """Review of 8 Nov: a landed sell whose buy the tape missed has no result (its price is unknown) but paid for its
    failed duplicate sends: the summary's whole result charges them once and says how many sells it could not price."""
    from snipetech.store import Store

    st = Store(":memory:")
    st.put_fill({"sig": "B1", "ts": NOW - 400, "wallet": "W", "mint": "MS1", "side": "buy", "sol": 0.2, "tokens": 1.0})
    _sell(st, "S1", NOW - 300, buy_sig="B1", pnl_sol=-0.01, twin_fee_sol=0.002)
    _sell(st, "S2", NOW - 100, pnl_sol=None, twin_fee_sol=0.00401)  # its buy is not on the tape
    out = L.round_trips(st, "W", NOW - 6 * H)
    s = out["summary"]
    assert (s["trips"], s["wins"], s["losses"]) == (2, 0, 1)  # the unpriced sell is neither a win nor a loss
    assert s["pnl_sol"] == pytest.approx(-0.01 - 0.002 - 0.00401) and s["recorded_sol"] == -0.01
    assert s["unpriced"] == 1 and s["unpriced_dup_sol"] == pytest.approx(0.00401)
    trip = next(t for t in out["trips"] if t["sig"] == "S2")
    assert trip["result_sol"] is None and trip["price_unknown"] is True and trip["twin_fee_sol"] == 0.00401
    st.close()


def test_the_post_mortem_reads_each_trip_broken_into_its_parts_each_cost_once(paths):
    """7 Nov 19:18-19:22, three copies of 7aPo3n: the landed records say -0.0038 SOL; with the four failed duplicate
    sells beside them it is -0.0119, and the post-mortem must see that, the parts behind it, the causes ranked by SOL
    and the change that would have saved the most, computed by code, not by the model."""
    from snipetech.store import Store
    from tests import loss_7nov as F

    st = Store(":memory:")
    F.load(st)
    out = L.round_trips(st, F.WALLET, F.FIRST_BUY_TS - 60)
    s = out["summary"]
    assert (s["trips"], s["wins"], s["losses"]) == (3, 1, 2)
    assert s["pnl_sol"] == pytest.approx(-0.011862) and s["recorded_sol"] == pytest.approx(-0.003842)
    assert round(s["price_sol"], 4) == 0.0294 and round(s["costs_sol"], 4) == 0.0412 and s["duplicates"] == 4
    assert [c["cause"] for c in s["causes"]] == ["venue_fee", "buy_fees", "sell_fees", "duplicates"]
    assert [x["lever"] for x in s["levers"]] == ["single_send", "sell_fee", "exit"]
    trips = {t["mint"][:6]: t for t in out["trips"]}
    first = trips["8RJQ2b"]
    assert first["result_sol"] == pytest.approx(-0.010577) and first["pnl_sol"] == -0.006567
    b = first["breakdown"]
    assert (round(b["price_sol"], 4), b["dups"], b["kind"]) == (0.0044, 2, "lost_to_costs")
    assert trips["EZaNbQ"]["breakdown"]["exit"] == {"ran": "lab", "fired": "dead_exit", "best": "hold60",
                                                    "gain_sol": pytest.approx(0.001984)}  # fmt: skip
    assert "sell_sig" not in b and F.SELLS["8RJQ2b"] not in json.dumps(out)  # short signatures only
    st.close()


def test_a_round_trip_says_what_every_exit_on_the_menu_would_have_made(paths):
    """The post-mortem sees, next to what the trip made, what each exit on the menu would have made from the same entry
    (measure's shadow replay, this menu's version only) and which did best, so it can say whether another exit would
    have done better; a trip whose buy is not replayed yet says so with None."""
    from snipetech.exits_menu import EXIT_MENU_VERSION
    from snipetech.store import Store

    st = Store(":memory:")
    st.put_fill({"sig": "B" * 88, "ts": NOW - 400, "wallet": "W", "wallet_copied": "LEAD", "mint": "M1", "side": "buy",
                 "sol": 0.2, "tokens": 1.0})  # fmt: skip
    _sell(st, "S" * 88, NOW - 100, buy_sig="B" * 88, pnl_sol=-0.041, exit_reason="dead_exit", exit_name="lab")
    _sell(st, "S2", NOW - 90, buy_sig="B2", pnl_sol=0.01, exit_reason="copy_exit", exit_name="lab")
    shadow = [("lab", -0.038, "dead_exit"), ("hold60", -0.05, "timer"), ("ride", 0.012, "trailing")]
    st.put_exit_shadow([{"buy_sig": "B" * 88, "exit_name": n, "menu_version": EXIT_MENU_VERSION, "sim_pnl_sol": p,
                         "sim_reason": r, "computed_at": NOW} for n, p, r in shadow]
                       + [{"buy_sig": "B" * 88, "exit_name": "lab", "menu_version": "copy-menu-v0", "sim_pnl_sol": 9.0,
                           "sim_reason": "timer", "computed_at": NOW}])  # fmt: skip
    trips = {t["sig"]: t for t in L.round_trips(st, "W", NOW - 6 * H)["trips"]}
    t = trips["S" * 12]
    assert t["shadow"] == {"lab": {"pnl_sol": -0.038, "exit": "dead_exit"}, "hold60": {"pnl_sol": -0.05, "exit": "timer"},
                           "ride": {"pnl_sol": 0.012, "exit": "trailing"}}  # fmt: skip
    assert t["shadow_best"] == "ride" and "B" * 20 not in json.dumps(t)
    assert trips["S2"]["shadow"] is None and trips["S2"]["shadow_best"] is None
    st.close()


# -- the tools the turns get ------------------------------------------------------------------------------------------


@pytest.fixture
def toolbox(tmp_path):
    import sys

    from snipetech import ops as O
    from snipetech.config import OperatorConfig
    from snipetech.operator.jobs import OperatorJobs
    from snipetech.operator.tools import NoteSink, make_operator_tools
    from snipetech.store import Store

    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    st = Store(data / "snipetech.db")

    class Svc:
        store = st

    jobs = OperatorJobs(repo=tmp_path, data=data, db=data / "snipetech.db", branch="main")
    jobs.ops = O.Ops(repo=tmp_path, data=data, db=data / "snipetech.db", keys_file=tmp_path / "k", branch="main",
                     command=[sys.executable, "-c", "pass"])  # fmt: skip
    cfg = OperatorConfig(wallet="W")
    tools = {
        t.name: t
        for t in make_operator_tools(Svc(), paths, jobs, cfg, NoteSink(paths, now=lambda: NOW), source="postmortem")
    }
    yield tools, paths, st
    st.close()


def _call(tool, **kw):
    import asyncio

    return asyncio.run(tool.call(kw))


def test_the_operator_and_the_analyst_can_read_and_write_what_was_learned(toolbox):
    tools, paths, _ = toolbox
    assert {"read_lessons", "add_lesson", "list_ideas", "upsert_idea", "read_round_trips"} <= set(tools)
    out = _call(tools["add_lesson"], text="Fees ate the small wins", evidence="6 of 9 wins under 0.009 SOL", n=9)
    assert out.startswith("recorded")
    assert _call(tools["add_lesson"], text="Fees ate the small wins", evidence="again", n=9) == "already recorded"
    (lesson,) = json.loads(_call(tools["read_lessons"], n=5))
    assert lesson["source"] == "postmortem" and lesson["n"] == 9 and lesson["ts"] == "2026-11-06T18:00:00Z"
    assert "Fees ate the small wins" in (paths.root / "lessons.md").read_text()
    assert _call(tools["add_lesson"], text="x", evidence="", n=3).startswith("error: evidence")
    idea = json.loads(_call(tools["upsert_idea"], **IDEA))
    assert idea["id"] == "I1" and idea["source"] == "postmortem"
    moved = json.loads(_call(tools["upsert_idea"], id="I1", status="testing"))
    assert moved["status"] == "testing" and moved["title"] == IDEA["title"]
    assert _call(tools["upsert_idea"], title="half an idea").startswith("error: a new idea needs")
    listed = json.loads(_call(tools["list_ideas"]))
    assert [i["id"] for i in listed["ideas"]] == ["I1"] and listed["counts"] == {"testing": 1}
    assert _call(tools["list_ideas"], status="bogus").startswith("error")


def test_the_post_mortem_reads_each_round_trip_in_one_call(toolbox):
    tools, _, st = toolbox
    st.put_fill({"sig": "B1", "ts": NOW - 400, "wallet": "W", "wallet_copied": "LEAD", "mint": "M1", "side": "buy", "sol": 0.2,
                 "tokens": 1.0, "trades_behind": 3, "price_gap_pct": 31.0})  # fmt: skip
    _sell(st, "S1", NOW - 100, buy_sig="B1", pnl_sol=-0.041, exit_reason="dead_exit")
    _sell(st, "S0", NOW - 30 * H, pnl_sol=0.01)
    out = json.loads(_call(tools["read_round_trips"], hours=6))
    assert out["summary"]["trips"] == 1 and out["trips"][0]["leader"] == "LEAD"
    assert json.loads(_call(tools["read_round_trips"], hours=48))["summary"]["trips"] == 2


def test_read_round_trips_gives_the_analyst_the_breakdown_the_causes_and_the_levers(paths):
    """The analyst gets the night's arithmetic done: each trip's parts, the causes ranked by SOL and the change that
    would have saved the most, with who can make it."""
    from snipetech.operator.tools import make_learning_tools
    from snipetech.store import Store
    from tests import loss_7nov as F

    st = Store(":memory:")
    F.load(st)

    class Svc:
        store = st

    now = F.LAST_SELL_TS + 600
    tools = {t.name: t for t in make_learning_tools(paths, lambda: now, "postmortem", svc=Svc(), wallet=F.WALLET)}
    out = json.loads(_call(tools["read_round_trips"], hours=1))
    s = out["summary"]
    assert s["trips"] == 3 and s["pnl_sol"] == pytest.approx(-0.011862)
    lever = dict(s["levers"][0])
    per_sell = lever.pop("twins_per_sell")  # the twins' cost per trip at each sell priority (the 8 Nov split)
    assert s["causes"][0]["cause"] == "venue_fee" and lever == pytest.approx(
        {"lever": "single_send", "sol": 0.00802, "trips": 2, "who": "oktech", "words": lever["words"]}
    )
    assert per_sell["0.002"]["trips"] == 2 and per_sell["0.002"]["per_trip_sol"] == pytest.approx(0.00401)
    assert {t["breakdown"]["kind"] for t in out["trips"]} == {"won", "lost_to_costs"}


def test_a_post_mortem_cannot_write_the_days_analyst_report(toolbox):
    """The day's analysis counts as done once operator/analyst-<day>.md exists: a post-mortem round writing that name
    would cancel the day's analysis, so its write_report refuses it."""
    tools, paths, _ = toolbox
    assert _call(tools["write_report"], name="analyst-2026-11-06", text="# pm").startswith("error")
    assert not paths.report("analyst-2026-11-06").exists()
    assert (
        _call(tools["write_report"], name="postmortem-2026-11-06-1800", text="# pm")
        == "wrote postmortem-2026-11-06-1800.md"
    )


def test_no_report_can_overwrite_the_lessons_or_the_notes(toolbox):
    """lessons.md is append-only through add_lesson (each line checked for evidence, n, size and secrets): write_report
    writes operator/<name>.md, so it refuses the names of the shared files."""
    tools, paths, _ = toolbox
    _call(tools["add_lesson"], text="Fees ate the small wins", evidence="6 of 9 wins under 0.009 SOL", n=9)
    before = (paths.root / "lessons.md").read_text()
    for name in ("lessons", "notes", "inbox", "LESSONS"):
        assert _call(tools["write_report"], name=name, text="# gone").startswith("error"), name
    assert (paths.root / "lessons.md").read_text() == before


def test_a_failed_trip_count_keeps_the_last_reviewed_sell(paths):
    """When the round could not count the trips, the record of what was already reviewed stays: a later post-mortem
    never reviews (and pays for) the same old trips again."""
    L.mark_postmortem(paths, NOW - 4 * H, {"newest_ts": NOW - 4 * H, "n": 6}, "postmortem")
    L.mark_postmortem(paths, NOW, {"newest_ts": None, "n": 0}, "analyst")
    state = json.loads(L._state_path(paths).read_text())
    assert state["postmortem"]["sell_ts"] == NOW - 4 * H and state["postmortem"]["ts"] == NOW


def test_every_turn_sees_what_the_book_already_does(paths):
    """An idea the owner would only be asked for again (retire the paid arm, pause a losing wallet) is avoided when the
    turn sees the book's current settings first."""
    from snipetech.config import Config

    book = Config().book
    text = L.context(paths, book=book)
    head = text.splitlines()[0]
    assert head.startswith("What the book does now")
    assert "paid arm retired" in head and "paused after 5 losing" in head and "first buy" in head
    assert "exits: lab for every wallet" in head
    book.exit_mode = "per_wallet"
    assert "exits: each wallet's own" in L.context(paths, book=book).splitlines()[0]
    assert not L.context(paths).startswith("What the book does now")  # without the config: as before


# -- the research record: the cloud session's research rounds (docs/research/), shown to every turn ------------------


def _report(d, name, outcome, lessons=None):
    """A round report laid out as the cloud session writes them (docs/research/2026-11-07-round-3.md): the OUTCOME
    paragraph first under ## Report, the owner's part after it, the lab table, one lesson a line, the next angles."""
    d.mkdir(parents=True, exist_ok=True)
    text = (f"# Research round: {name}\n\nThe angles, the screens and the labs of the round.\n\n## Report\n\n"
            f"{outcome}\nIts second line, in the same paragraph.\n\nWHAT THE OWNER CAN DO NOW\n1. Ask OKTech one "
            "question.\n\n## Lab results\n\n| lab | n | mean |\n|---|---|---|\n")  # fmt: skip
    if lessons is not None:
        text += "\n## Lessons\n\n" + "".join(f"- {x}\n" for x in lessons)
    (d / name).write_text(text + "\n## Next angles\n\n- an angle for the next round\n")


INDEX = """# Strategy research rounds

The record of the continuous strategy research the cloud session runs.

Round 1 is the edge hunt of 7 Nov 12:20 UTC (seven fixed angles); round 2 on choose their own.

- [Round 1](2026-11-07-round-1.md), 7 Nov 12:20-16:25: 8 labs, none positive; MIG the one open idea.
- [Round 2](2026-11-07-round-2.md), 7 Nov 16:27-20:48: 5 labs, none past the skeptics' bar; copy verdicts do not
  carry forward (-0.0147 a copy on later tape).
- [Round 3](2026-11-07-round-3.md), 7 Nov 20:50 - 8 Nov 00:00: the creator-0 hunts lose on unseen 4-5 Nov tape.

**Lab numbers from round 1 and from later labs are not comparable.**
"""


def test_every_turn_sees_the_newest_rounds_outcome_and_lessons_and_a_line_for_each_older_round(tmp_path):
    """7 Nov 21:16-23:16 the analyst froze two creator-0 hunts into the paper test and started a lab for a third, while
    round 3 (8 Nov 00:00) showed both lose on unseen tape: no agent read the rounds. A turn now sees the newest round's
    OUTCOME paragraph and lessons, and the index line of each older round (never their own outcome or lessons)."""
    d = tmp_path / "docs" / "research"
    _report(d, "2026-11-07-round-2.md", "OUTCOME: round two found two thin leads.", ["Round two's own lesson"])
    _report(d, "2026-11-07-round-3.md", "OUTCOME: round three refuted the creator-0 hunts on unseen tape.",
            ["Unused old tape settles a lead faster (19 firings, -0.0099 a trade)",
             "A bundle operator's coins need a short exit"])  # fmt: skip
    (d / "README.md").write_text(INDEX)
    text = L.research_record(d)
    lines = text.splitlines()
    assert lines[0].startswith("Research record") and "2026-11-07-round-3.md" in lines[0]
    assert ("OUTCOME: round three refuted the creator-0 hunts on unseen tape. Its second line, in the same "
            "paragraph.") in lines  # fmt: skip
    assert "- Unused old tape settles a lead faster (19 firings, -0.0099 a trade)" in lines
    assert "- A bundle operator's coins need a short exit" in lines
    assert ("- Round 2 (2026-11-07-round-2.md), 7 Nov 16:27-20:48: 5 labs, none past the skeptics' bar; copy verdicts "
            "do not carry forward (-0.0147 a copy on later tape).") in lines  # fmt: skip
    assert (
        "- Round 1 (2026-11-07-round-1.md), 7 Nov 12:20-16:25: 8 labs, none positive; MIG the one open idea." in lines
    )
    assert "round two found two thin leads" not in text and "Round two's own lesson" not in text
    assert "the creator-0 hunts lose on unseen" not in text  # the newest round's own index line: its outcome is there
    for elsewhere in ("Ask OKTech", "an angle for the next round", "edge hunt of", "not comparable", "| lab |"):
        assert elsewhere not in text, elsewhere
    assert text.index("OUTCOME: round three") < text.index("Unused old tape") < text.index("- Round 2 (")
    assert text.index("- Round 2 (") < text.index("- Round 1 (")  # older rounds newest first


def test_round_ten_is_newer_than_round_nine(tmp_path):
    """The newest round is the highest round number compared as a number: by name, round 9 sorts after round 10."""
    d = tmp_path / "research"
    _report(d, "2026-11-09-round-10.md", "OUTCOME: ten.", ["ten's lesson"])
    _report(d, "2026-11-09-round-9.md", "OUTCOME: nine.", ["nine's lesson"])  # written last: the newest file on disk
    text = L.research_record(d)
    assert "OUTCOME: ten." in text and "- ten's lesson" in text.splitlines()
    assert "OUTCOME: nine." not in text and "nine's lesson" not in text


def test_the_research_record_stays_small_and_keeps_each_part(tmp_path):
    """The block is capped and every line is cut like a lesson's; a long round never pushes out the older rounds'
    lines, and what is left out is counted with the file that holds it."""
    d = tmp_path / "research"
    words = "word " * 120  # words, not one long run of letters (that reads as a key to the secret check)
    _report(d, "2026-11-12-round-12.md", "OUTCOME: twelve " + words, [f"L{i:02d} {words}" for i in range(30)])
    (d / "README.md").write_text("# Strategy research rounds\n\n" + "".join(
        f"- [Round {i}](2026-11-{i:02d}-round-{i}.md), {words}\n" for i in range(1, 12)))  # fmt: skip
    text = L.research_record(d)
    lines = text.splitlines()
    assert len(text) <= L.RESEARCH_MAX_CHARS and all(len(x) <= L.CONTEXT_LINE for x in lines)
    assert any(x.startswith("OUTCOME: twelve") for x in lines) and "- L00 word word" in text
    shown = sum(x.startswith("- L") for x in lines)
    assert 0 < shown < 30 and any(f"{30 - shown} more" in x and "2026-11-12-round-12.md" in x for x in lines)
    assert any(x.startswith("- Round 11 (") for x in lines)  # the newest older round keeps its line
    assert not any(x.startswith("- Round 1 (") for x in lines) and "README.md" in lines[-1]


def test_every_line_is_cut_whatever_the_reports_file_name(tmp_path):
    """The lines that carry the report's file name (the head, the count of lessons left out) are cut like the rest."""
    d = tmp_path / "research"
    _report(
        d, f"2026-11-07-{'x' * 200}-round-3.md", "OUTCOME: three.", [f"L{i:02d} " + "word " * 120 for i in range(30)]
    )
    lines = L.research_record(d).splitlines()
    assert lines[1].startswith("OUTCOME: three.") and any("more lessons" in x for x in lines)
    assert all(len(x) <= L.CONTEXT_LINE for x in lines)


def test_no_research_record_gives_no_block_and_never_an_error(tmp_path, paths):
    """A server without docs/research (an installed package, an old checkout), a directory without a round report yet,
    or a report without its sections: no block, and the turn goes on as before."""
    assert L.research_record(None) == "" and L.research_record(tmp_path / "nowhere") == ""
    d = tmp_path / "research"
    d.mkdir()
    (d / "README.md").write_text(INDEX)  # an index without any report
    assert L.research_record(d) == ""
    (d / "README.md").unlink()
    (d / "2026-11-08-round-4.md").write_text("# Research round 4\n\nStill running.\n")
    assert L.research_record(d) == ""
    assert L.context(paths, research=d) == L.context(paths) == L.context(paths, research=tmp_path / "nowhere")


def test_a_missing_section_leaves_out_only_its_part(tmp_path):
    d = tmp_path / "research"
    _report(d, "2026-11-07-round-3.md", "OUTCOME: three.")  # no ## Lessons, no README.md
    assert L.research_record(d).splitlines()[1:] == ["OUTCOME: three. Its second line, in the same paragraph."]
    (d / "2026-11-08-round-4.md").write_text("# Research round 4\n\n## Lessons\n\n- four's lesson\n")  # no ## Report
    text = L.research_record(d)
    assert "- four's lesson" in text.splitlines() and "OUTCOME" not in text


def test_the_research_record_never_carries_a_secret(tmp_path, monkeypatch):
    """The reports are not written through these agents' checked tools: each line goes through the same checks as a
    lesson, and what looks like a secret is blanked, the rest of the line kept."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:telegram-secret-value")
    d = tmp_path / "research"
    _report(d, "2026-11-07-round-3.md", "OUTCOME: the key sk-ant-api03-abcdefghijkl leaked here.",
            ["token 123456:telegram-secret-value in a lesson", f"a keypair {'4' * 88} in another",
             "api_key=abcdefgh12345678 and more"])  # fmt: skip
    text = L.research_record(d)
    for secret in ("sk-ant-api03", "telegram-secret-value", "4" * 80, "abcdefgh12345678"):
        assert secret not in text, secret
    assert "leaked here." in text and "in a lesson" in text and "in another" in text and "and more" in text


def test_the_research_record_follows_the_lessons_and_ideas_on_its_own_cap(paths, tmp_path):
    """The lessons and ideas keep their 4,000 characters and the record its own: a long memory never cuts the record
    off, and a long record never pushes out the newest lesson."""
    for i in range(12):
        L.add_lesson(paths, f"{i:02d} " + "tx " * 96, "ev " * 133, 3, source="analyst", now=NOW + i)
    for i in range(6):
        L.upsert_idea(paths, IDEA | {"title": f"{i} " + "xy " * 36, "why": "wh " * 199, "test": "te " * 133},
                      source="analyst", now=NOW)  # fmt: skip
    d = tmp_path / "research"
    words = "word " * 120
    _report(d, "2026-11-07-round-3.md", "OUTCOME: three " + words, [f"L{i:02d} {words}" for i in range(30)])
    text = L.context(paths, research=d)
    memory, record = text.split("\nResearch record", 1)
    assert memory == L.context(paths) and len(memory) <= L.CONTEXT_MAX_CHARS and "11 tx tx" in memory
    assert len("Research record" + record) <= L.RESEARCH_MAX_CHARS and "OUTCOME: three" in record
