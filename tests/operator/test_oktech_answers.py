"""OKTech's answer on maxDevBuySol 0 (operator/oktech_answers.py) and the gate it lifts (research round 3, priority 1,
docs/research/2026-11-07-round-3.md). A creator-0 rule (a hunt whose trigger has dev_max 0) was scored skipping every
launch whose creator bought anything, and is sent to OKTech as Hunt's maxDevBuySol 0. If OKTech reads that 0 as "no
limit", its sniper runs the plain crowd hunt, which lost 0.0147 SOL a trade over 1,285 trades on tape the research had
not used; OKTech has ignored thresholds before without saying so (5 Nov 22:11). So no such rule starts live, by /test go,
the round's start or the automatic test, until the owner records OKTech's answer. Rules with a creator limit above 0,
copies and anything already running never wait for it."""

import asyncio
import json

import pytest

from snipetech import cli
from snipetech.analysis import paper
from snipetech.config import Config
from snipetech.oktech import strategy_sniper
from snipetech.operator import autotest as AT
from snipetech.operator import live_test as LT
from snipetech.operator import oktech_answers as OA
from snipetech.operator.commands import HELP
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.tools import make_operator_tools
from snipetech.store import Store
from tests.operator import test_autotest as ATT
from tests.operator import test_live_test as LTT

NOW, W, BEST, SECOND = LTT.NOW, LTT.W, LTT.BEST, LTT.SECOND
HOUR = 3600
# the creator-0 hunt the server froze into the paper test at 21:31 on 7 Nov (f94add343f): 10 SOL from 10 wallets within
# 1 s of the launch, and the creator buys nothing
CREATOR0 = {"kind": "hunt", "sol": 10.0, "buyers": 10, "window": 1.0, "dev_max": 0.0}
WAIT = "waits for OKTech to confirm what maxDevBuySol 0 means"
ANSWER = "8 Nov 09:40 UTC, OKTech support in Telegram: maxDevBuySol 0 skips every launch whose creator bought anything"


@pytest.fixture
def env(tmp_path):
    e = LTT.make_env(tmp_path)
    e.sol = 11.8  # the wallet's SOL as each automatic round's snapshot reads it (test_autotest._round)
    e.k = 0
    yield e
    e.store.close()


def _creator0(e, sid=BEST, sol=10.0, pnl=0.02):
    """A creator-0 hunt that passes every other check: 150 forward trades of `pnl` SOL where a live hunt fill lands, at
    the measured cost of a trade, with OKTech's watch delay, settings OKTech takes as they are."""
    return LTT._freeze(e.store, sid, trigger=CREATOR0 | {"sol": sol}, pnl=pnl)


def _confirm(e, at=NOW - HOUR):
    """OKTech's answer as the cloud session records it with the owner's word (`operate oktech --dev-max-zero`)."""
    return OA.set_dev_max_zero(e.paths, ANSWER, by="cli", now=at)


def _waits(e) -> list[str]:
    return [s for s in e.sent if WAIT in s]


# -- /test and /test go --------------------------------------------------------------------------------------------


def test_test_names_the_wait_for_oktechs_answer_and_go_starts_nothing(env):
    """The forward test backs it and every other check passes, yet nothing offers it: /test lists it as the closest with
    the reason in plain words, its own plan says it cannot be tested live, and its go changes nothing."""
    _creator0(env)
    assert LT.eligible(env.store, env.cfg, NOW) == []
    shown = LTT._send(env)
    assert "No hunt strategy qualifies" in shown and BEST in shown and WAIT in shown and "/oktech" in shown
    assert f"/test {BEST} go" not in shown
    one = LTT._send(env, BEST)
    assert one.startswith(f"{BEST} cannot be tested live: it {WAIT}") and f"/test {BEST} go" not in one
    reply = LTT._refused(env, BEST, "go")
    assert WAIT in reply and "0.0147" in reply and "1,285" in reply  # the number that makes it matter


def test_the_rounds_start_check_refuses_a_creator_0_request_already_on_file(env):
    """A request written before this check existed (or before the owner took OKTech's answer back) is checked again by
    the round like every refusal of /test go: it ends with the reason and nothing is created on OKTech."""
    _creator0(env)
    row = next(r for r in paper.report(env.store, env.cfg, now=NOW)["strategies"] if r["id"] == BEST)
    assert LT.write_request(env.paths, LT.request(LT.plan(row, 11.9, W), row, env.cfg, NOW, by="telegram"), NOW)
    LTT._round(env)
    t = LT.load(env.paths)
    assert t["state"] == "done" and not t["started"] and f"{BEST} no longer qualifies: it {WAIT}" in t["error"], t
    assert env.svc.created == [] and env.started == []
    assert any("did not start" in s and WAIT in s for s in env.sent)


def test_once_oktechs_answer_is_recorded_the_same_hunt_starts_from_test_go(env):
    _creator0(env)
    _confirm(env)
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [BEST]
    assert LTT._send(env).splitlines()[-1] == f"/test {BEST} go"
    LTT._send(env, BEST, "go")
    assert LT.load(env.paths)["state"] == "requested"
    LTT._round(env)
    assert LT.load(env.paths)["state"] == "running" and [c["id"] for c in env.svc.created] == [BEST]


# -- the automatic test --------------------------------------------------------------------------------------------


def test_the_automatic_test_never_starts_a_creator_0_hunt_and_tells_the_owner_once_a_day(env):
    """Only the owner can put the question to OKTech, and nothing else would tell them why their standing yes starts
    nothing: one line a day names the strategy, the question and the line that records the answer."""
    _creator0(env)
    ATT._on(env)
    ATT._round(env, NOW + 60)
    assert LT.load(env.paths) is None and AT.load_seq(env.paths) is None
    assert env.svc.created == [] and env.started == []
    (line,) = _waits(env)
    assert line.startswith("Automatic live test") and BEST in line and OA.QUESTION in line
    assert "/oktech dev0 confirmed" in line and "once a day" in line
    ATT._round(env, NOW + 960)  # the next round, the same day: nothing new is said, nothing starts
    assert len(_waits(env)) == 1 and env.svc.created == []


def test_once_recorded_the_automatic_test_starts_it_like_any_other(env):
    _creator0(env)
    _confirm(env)
    ATT._on(env)
    ATT._round(env, NOW + 60)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["id"] == BEST and AT.load_seq(env.paths)["strategy"] == BEST
    assert _waits(env) == []


def test_a_creator_0_hunt_never_holds_back_one_with_a_creator_limit_above_0(env):
    """The better lower bound waits for OKTech; the best strategy that does not wait starts in the same round."""
    _creator0(env, pnl=0.03)
    LTT._freeze(env.store, SECOND, pnl=0.01)  # a creator limit of 1 SOL
    ATT._on(env)
    ATT._round(env, NOW + 60)
    t = LT.load(env.paths)
    assert t["state"] == "running" and t["id"] == SECOND
    (line,) = _waits(env)
    assert BEST in line and SECOND not in line


@pytest.mark.parametrize("dev_max", [1.0, None])
def test_a_hunt_whose_creator_limit_is_above_0_or_unset_never_waits(env, dev_max):
    """Nothing recorded and nothing needed: a creator limit of 1 SOL (OKTech's maxDevBuySol 1, as every hunt has run
    since 5 Nov) or none at all is not the question put to OKTech."""
    LTT._freeze(env.store, BEST, trigger=CREATOR0 | {"dev_max": dev_max}, pnl=0.02)
    assert [r["id"] for r in LT.eligible(env.store, env.cfg, NOW)] == [BEST]
    ATT._on(env)
    ATT._round(env, NOW + 60)
    assert LT.load(env.paths)["state"] == "running" and not [s for s in env.sent if "maxDevBuySol" in s]


def test_taking_the_answer_back_ends_a_creator_0_run_at_its_next_block_and_stops_nothing(env):
    """The block running when the owner takes the answer back runs to its end, its cycler and sniper untouched (a
    stopped sniper strands its coin, rule 1); no next block is requested, and the run ends without a drop, so its live
    record goes with it if the answer is recorded again."""
    _creator0(env)
    _confirm(env)
    ATT._on(env)
    ATT._round(env, NOW + 60)
    assert LT.load(env.paths)["state"] == "running"
    OA.set_dev_max_zero(env.paths, "", by="telegram", now=NOW + 120)  # an empty value: not confirmed
    ATT._round(env, NOW + 960)
    assert LT.load(env.paths)["state"] == "running" and env.svc.stops == [] and len(env.started) == 1
    ATT._play(env, [0.02] * 10)
    ATT._round(env, NOW + 60 + 2 * HOUR)
    seq = AT.load_seq(env.paths)
    assert seq["state"] == "ended" and f"{BEST} no longer qualifies: it {WAIT}" in seq["why"]
    assert not AT.dropped(env.paths) and len(env.svc.created) == 1
    assert any(s.startswith("Automatic live test ended") and WAIT in s for s in env.sent)


# -- the plan's notes ----------------------------------------------------------------------------------------------


def test_the_plans_notes_say_it_while_the_answer_is_not_recorded():
    """`oktech strategy` sends the rule exactly as scored either way (maxDevBuySol 0) and says in its notes that the live
    test and the automatic test wait for OKTech's answer; with the answer recorded it names it instead."""
    strat = {"id": BEST, "label": "creator 0", "buy_sol": 0.2, "trigger": CREATOR0,
             "exit": {"stop_loss": 0.3, "max_hold_secs": 300}}  # fmt: skip
    plain = strategy_sniper(strat, 1)
    assert plain.config["hunt"]["thresholds"]["maxDevBuySol"] == 0
    (note,) = [n for n in plain.notes if "maxDevBuySol 0" in n]
    assert WAIT in note and "0.0147" in note and "/oktech" in note
    confirmed = strategy_sniper(strat, 1, dev_max_zero_confirmed=ANSWER)
    assert confirmed.config == plain.config
    (note,) = [n for n in confirmed.notes if "maxDevBuySol 0" in n]
    assert WAIT not in note and "confirmed" in note and ANSWER in note
    one = strategy_sniper(strat | {"trigger": CREATOR0 | {"dev_max": 1.0}}, 1)
    assert not [n for n in one.notes if "maxDevBuySol" in n]


def test_oktech_strategy_reads_the_owners_record_for_its_note(tmp_path):
    from snipetech.service import Service

    data = tmp_path / "data"
    store = Store(data / "snipetech.db")
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    store.set_coverage("paper_strategies", {"strategies": [
        {"id": BEST, "label": "creator 0", "buy_sol": 0.2, "trigger": CREATOR0,
         "exit": {"stop_loss": 0.3, "max_hold_secs": 300}}]})  # fmt: skip
    svc = Service(cfg, store=store)
    try:
        notes = asyncio.run(svc.oktech_strategy(BEST, "SNIPER"))["snipers"][0]["notes"]
        assert any(WAIT in n for n in notes)
        OA.set_dev_max_zero(OperatorPaths(data), ANSWER, by="cli", now=NOW)
        notes = asyncio.run(svc.oktech_strategy(BEST, "SNIPER"))["snipers"][0]["notes"]
        assert not any(WAIT in n for n in notes) and any(ANSWER in n for n in notes)
    finally:
        store.close()


# -- the record: the owner's, and only theirs ----------------------------------------------------------------------


def test_only_the_owners_words_confirm_it_and_a_read_never_raises(env, monkeypatch):
    f = OA.answers_file(env.paths)
    assert f == env.paths.root / "OKTECH" and OA.dev_max_zero(env.paths) is None  # no file: not confirmed
    for text in ("{not json", "[1]", "", json.dumps({"dev_max_zero": True}), json.dumps({"dev_max_zero": "yes"}),
                 json.dumps({"dev_max_zero": {"words": "   "}}), json.dumps({"dev_max_zero": {"words": 1}}),
                 json.dumps({"dev_max_zero": {"words": ["confirmed"]}})):  # fmt: skip
        f.write_text(text)
        assert OA.dev_max_zero(env.paths) is None, text
    monkeypatch.setenv("OKS_KEY", "oks-test-key-0123456789")
    a = OA.set_dev_max_zero(env.paths, f"  {ANSWER}\n oks-test-key-0123456789 " + "x" * 400, by="cli", at=NOW - 60,
                            now=NOW)  # fmt: skip
    assert a["words"].startswith(f"{ANSWER} <redacted>") and len(a["words"]) <= OA.WORDS_MAX
    assert (a["at"], a["by"]) == (NOW - 60, "cli") and json.loads(f.read_text())["dev_max_zero"]["ts"] == NOW
    assert "oks-test-key" not in f.read_text()
    assert OA.set_dev_max_zero(env.paths, " ", by="cli", now=NOW + 1) is None  # an empty value takes it back
    assert OA.dev_max_zero(env.paths) is None and json.loads(f.read_text())["dev_max_zero"]["ts"] == NOW + 1


def test_operate_oktech_records_the_answer_from_the_cloud_session(env, capsys):
    db = env.cfg.storage.db_path
    assert cli.main(["--db", db, "operate", "oktech"]) == 0
    out = capsys.readouterr().out
    assert "not recorded" in out and OA.QUESTION in out and str(OA.answers_file(env.paths)) in out
    assert cli.main(["--db", db, "operate", "oktech", "--dev-max-zero", ANSWER]) == 0
    a = OA.dev_max_zero(env.paths)
    assert a["words"] == ANSWER and a["by"] == "cli" and ANSWER in capsys.readouterr().out
    with pytest.raises(SystemExit, match="operate oktech"):  # a flag of it with another switch changes nothing
        cli.main(["--db", db, "operate", "risk", "--dev-max-zero", ""])
    with pytest.raises(SystemExit, match="operate oktech"):
        cli.main(["--db", db, "operate", "oktech", "--day-stop", "1"])
    assert OA.dev_max_zero(env.paths)["words"] == ANSWER
    assert cli.main(["--db", db, "operate", "oktech", "--dev-max-zero", ""]) == 0  # empty: not confirmed
    assert OA.dev_max_zero(env.paths) is None and "not recorded" in capsys.readouterr().out


def test_oktech_in_telegram_shows_the_question_and_records_the_owners_word(env):
    assert "/oktech" in HELP
    shown = LTT._send(env, name="oktech")
    assert "not recorded" in shown and OA.QUESTION in shown and "/oktech dev0 confirmed" in shown
    reply = LTT._send(env, "dev0", "confirmed", name="oktech")
    a = OA.dev_max_zero(env.paths)
    assert a["by"] == "telegram" and a["at"] == NOW - 5 and "/oktech dev0 confirmed" in a["words"]
    assert reply.startswith("Recorded") and "/oktech dev0 unconfirmed" in reply
    notes = env.paths.notes.read_text()
    assert "[decision]" in notes and "maxDevBuySol 0" in notes
    assert "confirmed" in LTT._send(env, name="oktech") and "not recorded" not in LTT._send(env, name="oktech")
    secret = "4" * 30 + "PastedSecretKeyMaterialThatMustNeverBeRepeatedAnywhere0000000000"
    other = LTT._send(env, "dev0", secret, name="oktech")
    assert other.startswith("Nothing changed") and secret not in other
    assert secret not in env.paths.notes.read_text() and OA.dev_max_zero(env.paths) == a  # unchanged
    back = LTT._send(env, "dev0", "unconfirmed", name="oktech")
    assert back.startswith("Taken back") and OA.dev_max_zero(env.paths) is None
    assert "not recorded" in LTT._send(env, name="oktech")


def test_no_agent_tool_can_record_oktechs_answer(env):
    """Nothing in the code sets it by itself: the agents' start_job refuses `operate`, and none of their tools writes a
    file without an extension in operator/."""
    tools = {t.name: t for t in make_operator_tools(env.svc, env.paths, env.jobs, env.cfg.operator, env.notes)}
    rec = json.dumps({"dev_max_zero": {"words": ANSWER, "at": NOW, "by": "cli"}})
    calls = [("start_job", {"argv": ["operate", "oktech", "--dev-max-zero", ANSWER]}),
             ("write_report", {"name": "OKTECH", "text": rec}), ("write_report", {"name": "../operator/OKTECH", "text": rec}),
             ("set_candidates", {"items": [json.loads(rec)]}), ("write_note", {"text": rec, "level": "decision"}),
             ("add_lesson", {"text": "OKTech confirmed maxDevBuySol 0", "evidence": rec[:300], "n": 1})]  # fmt: skip
    for name, kw in calls:
        asyncio.run(tools[name].call(kw))
    assert OA.dev_max_zero(env.paths) is None and not OA.answers_file(env.paths).exists()
