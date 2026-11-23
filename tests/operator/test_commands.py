"""Telegram commands: owner-only parsing, offset persistence, /status, /stop that never strands a coin, /resume, and
/sell, the owner's own exit for a coin OKTech abandoned."""

import asyncio
import json

import httpx
import pytest

from snipetech.config import Config
from snipetech.operator.commands import Command, handle_command, parse_updates, poll_commands
from snipetech.operator.gate import expected_state
from snipetech.operator.live_test import SECOND_READ_SECS  # as it ships: the tests below run with no wait
from snipetech.operator.notify import Notifier
from snipetech.operator.paths import OperatorPaths
from snipetech.operator.snapshot import Snapshot
from snipetech.operator.tools import NoteSink

NOW = 1_793_923_200 + 3600
W = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"


@pytest.fixture(autouse=True)
def _no_wait_between_readings(monkeypatch):
    """/stop reads an empty wallet a second time before it stops a sniper (live_test.SECOND_READ_SECS apart): no wait
    in a test."""
    from snipetech.operator import live_test

    monkeypatch.setattr(live_test, "SECOND_READ_SECS", 0.0)


def _msg(uid, chat, text):
    return {"update_id": uid, "message": {"chat": {"id": chat, "type": "private"}, "text": text}}


def test_parse_only_owner_commands():
    ups = [
        _msg(1, 7, "/stop"),
        _msg(2, 9, "/stop"),
        _msg(3, 7, "/why W1 extra"),
        _msg(4, 7, "hello there"),
        _msg(5, 7, "/status@snipetech_bot"),
    ]
    cmds, last = parse_updates(ups, owner_chat_id=7)
    assert [(c.name, c.args) for c in cmds] == [("stop", []), ("why", ["W1", "extra"]), ("status", [])]
    assert last == 5 and cmds[0].chat_id == 7 and cmds[0].update_id == 1


def test_commands_from_other_chats_are_ignored():
    cmds, last = parse_updates(
        [_msg(10, 9, "/stop"), {"update_id": 11, "edited_message": {"chat": {"id": 9}, "text": "/stop"}}],
        owner_chat_id=7,
    )
    assert cmds == [] and last == 11
    assert parse_updates([], owner_chat_id=7) == ([], None)


def test_poll_persists_offset_and_survives_failure(tmp_path):
    paths = OperatorPaths(tmp_path).ensure()
    paths.telegram.write_text(json.dumps({"chat_id": 7}))
    seen = []

    def get(url, params):
        seen.append(params)
        return {"result": [_msg(41, 7, "/status")]}

    cmds = poll_commands("T", paths, get=get)
    assert [c.name for c in cmds] == ["status"] and seen[0].get("offset") is None
    saved = json.loads(paths.telegram.read_text())
    assert saved == {"chat_id": 7, "offset": 42}
    assert poll_commands("T", paths, get=lambda u, p: (_ for _ in ()).throw(TimeoutError())) == []
    assert json.loads(paths.telegram.read_text())["offset"] == 42  # a failed poll never moves the offset
    poll_commands("T", paths, get=get)
    assert seen[-1]["offset"] == 42  # the next poll asks from the saved offset
    assert poll_commands("T", OperatorPaths(tmp_path / "nochat").ensure(), get=get) == []  # no chat id yet → nothing


class FakeSvc:
    def __init__(self, held, snipers):
        self._held, self._snipers, self.calls = held, snipers, []

    async def open_positions(self, address, ignore=None, unknown=None):  # audit 22 Nov livetest#0: /stop passes ignore
        return [h for h in self._held if h.get("mint") not in set(ignore or ())]

    async def oktech_status(self):
        return {"snipers": self._snipers}

    async def oktech_task(self, action, task_id):
        self.calls.append((action, task_id))
        return {"ok": True}


def _ctx(tmp_path, svc, snap=None):
    paths = OperatorPaths(tmp_path / "data").ensure()
    cfg = Config()
    cfg.operator.wallet = W
    sent = []
    notifier = Notifier("T", 7, post=lambda url, data: sent.append(data["text"]))
    notes = NoteSink(paths, now=lambda: NOW)
    return {
        "svc": svc,
        "cfg": cfg,
        "paths": paths,
        "snap": snap,
        "notifier": notifier,
        "notes": notes,
        "now": NOW,
    }, sent


def _cmd(name, *args):
    return Command(name=name, args=list(args), chat_id=7, update_id=1)


def test_stop_stops_idle_wallet_snipers_and_writes_the_flag(tmp_path):
    svc = FakeSvc(
        held=[], snipers=[{"id": "s1", "name": "a", "status": "running"}, {"id": "s2", "name": "b", "status": "idle"}]
    )
    ctx, sent = _ctx(tmp_path, svc)
    reply = asyncio.run(handle_command(_cmd("stop"), **ctx))
    assert svc.calls == [("stop", "s1")] and "s1" in reply and ctx["paths"].stop_flag.exists()
    assert sent == [reply] and "stopped by owner" in ctx["paths"].notes.read_text()


def test_stop_keeps_holders_running(tmp_path):
    svc = FakeSvc(held=[{"mint": "M1", "amount": 5.0}], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    ctx, sent = _ctx(tmp_path, svc)
    reply = asyncio.run(handle_command(_cmd("stop"), **ctx))
    assert svc.calls == [] and "s1" in reply and "M1"[:4] in reply and "held" in reply.lower()
    assert ctx["paths"].stop_flag.exists()  # the flag still stops new snipers from being expected


def test_stop_treats_a_starting_sniper_as_running(tmp_path):
    """OKTech's statuses are idle / starting / running / stopping / finished / failed; a sniper that is starting is
    about to buy and must be stopped by /stop like a running one."""
    svc = FakeSvc(
        held=[],
        snipers=[{"id": "s1", "name": "a", "status": "starting"}, {"id": "s2", "name": "b", "status": "finished"}],
    )
    ctx, _ = _ctx(tmp_path, svc)
    reply = asyncio.run(handle_command(_cmd("stop"), **ctx))
    assert svc.calls == [("stop", "s1")] and "s1" in reply


def test_stop_when_holdings_cannot_be_read_stops_nothing(tmp_path):
    class Broken(FakeSvc):
        async def open_positions(self, address, ignore=None, unknown=None):  # the read fails, not its arguments
            raise RuntimeError("rpc down")

    svc = Broken(held=[], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    ctx, _ = _ctx(tmp_path, svc)
    reply = asyncio.run(handle_command(_cmd("stop"), **ctx))
    assert svc.calls == [] and "could not" in reply.lower()


# -- /stop beside coins no sniper of ours holds (audit of 22 Nov, livetest#0) -----------------------------------------

HAND = "H8WTvo3Zk9rFq2d1yHcU7aBmNnP4sVxLgE6tRjW5Kp1A"  # 22 Nov 02:40: the owner bought a coin three times in Axiom
AIRDROP = "Drop1" + "1" * 39
OUR_PRIO, OUR_TIP = 1_000_000, 0  # what our sniper on record pays on a buy (its payload: 0.001 priority, no tip)
AXIOM_PRIO, AXIOM_TIP = 8_175, 27_000_000  # what the owner's hand buy paid


class StoreSvc(FakeSvc):
    """FakeSvc with the store /stop reads whose each coin is (measure/ours.py): our sniper on record with its payload."""

    def __init__(self, tmp_path, held, snipers=None):
        from snipetech.store import Store

        super().__init__(held, snipers or [{"id": "s1", "name": "snipetech copy x", "status": "running"}])
        (tmp_path / "data").mkdir(exist_ok=True)
        self.store = Store(tmp_path / "data" / "snipetech.db")
        self.store.add_oktech_sniper({"id": "s1", "kind": "copy", "source": "book",
                                      "payload": {"priorityFee": 0.001, "tipAmount": 0.0, "singleSnipe": True}})  # fmt: skip
        self.k = 0

    def tape(self, mint, buy, prio, tip, base=1_000_000, ts=NOW - 3600):
        from snipetech.models import Trade

        self.k += 1
        self.store.add_trades([Trade(sig=f"t{self.k}", ev=0, slot=100 + self.k, pos=0, ts=int(ts), mint=mint,
                                     venue="curve", user=W, is_buy=buy, quote=1_000_000_000, base=base, fee=0,
                                     fee_bps=0, q_after=1, b_after=1, priority=prio, tip=tip)])  # fmt: skip

    def fill(self, mint, prio, tip, tokens=1_000_000.0, ts=NOW - 3600):
        self.k += 1
        self.store.put_fill({"sig": f"f{self.k}", "ts": int(ts), "slot": 100 + self.k, "wallet": W, "mint": mint,
                             "side": "buy", "sol": 4.95, "tokens": tokens, "priority_lamports": prio,
                             "tip_lamports": tip})  # fmt: skip


def _coin(mint, raw=1_000_000, value=1.2):
    return {"mint": mint, "amount": raw / 1e6, "raw": raw, "value_sol": value, "priced": value is not None}


def _stop(tmp_path, svc, **cfg):
    ctx, _sent = _ctx(tmp_path, svc)
    for k, v in cfg.items():
        setattr(ctx["cfg"].operator, k, v)
    try:
        return asyncio.run(handle_command(_cmd("stop"), **ctx)), ctx
    finally:
        svc.store.close()


def test_stop_stops_the_snipers_beside_a_coin_the_owner_ignores(tmp_path):
    svc = StoreSvc(tmp_path, [_coin(AIRDROP, value=0.01)])
    reply, _ = _stop(tmp_path, svc, ignore_mints=[AIRDROP])
    assert svc.calls == [("stop", "s1")], reply


def test_stop_stops_the_snipers_beside_the_owners_hand_bought_coin(tmp_path):
    """22 Nov 02:40: the owner's hand buys in Axiom paid fees no sniper of ours pays. No sniper of ours holds that coin:
    /stop stopped nothing while it sat in the wallet."""
    svc = StoreSvc(tmp_path, [_coin(HAND)])
    svc.tape(HAND, True, AXIOM_PRIO, AXIOM_TIP)
    reply, ctx = _stop(tmp_path, svc)
    assert svc.calls == [("stop", "s1")], reply
    assert "H8WTvo" in reply and "your own" in reply and "stopped 1 sniper" in reply
    assert "your own" in ctx["paths"].notes.read_text()


def test_a_hand_buy_the_measure_stage_recorded_and_the_tape_did_not_is_the_owners_too(tmp_path):
    svc = StoreSvc(tmp_path, [_coin(HAND)])
    svc.fill(HAND, AXIOM_PRIO, AXIOM_TIP)  # 22 Nov: H8WTvo3Z's hand buys are in the fills, not on the tape
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [("stop", "s1")], reply


def test_stop_stops_the_snipers_beside_a_coin_no_buy_explains_once_the_watch_has_seen_it_ten_minutes(tmp_path):
    """8 Nov: an unpriced dust airdrop held every book replacement for hours (book/run._strays)."""
    svc = StoreSvc(tmp_path, [_coin(AIRDROP, value=None)])
    paths = OperatorPaths(tmp_path / "data").ensure()
    (paths.root / "watch.json").write_text(json.dumps({"ts": NOW - 60, "first_seen": {AIRDROP: NOW - 700}}))
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [("stop", "s1")], reply


@pytest.mark.parametrize("case", ["ours after", "more held", "fees unknown", "seen just now", "sold part"])
def test_stop_keeps_every_sniper_running_while_a_held_coin_may_be_ours(tmp_path, case):
    """A stopped sniper strands its coin (CLAUDE.md rule 1): only a coin that is surely not ours lets /stop go on."""
    svc = StoreSvc(tmp_path, [_coin(HAND, raw=2_000_000 if case == "more held" else 1_000_000)])
    if case != "seen just now":  # a coin no buy explains yet may be a buy of ours the tape has not written
        svc.tape(HAND, True, AXIOM_PRIO, AXIOM_TIP, base=2_000_000 if case == "sold part" else 1_000_000)
    if case == "ours after":  # a buy of ours of the same coin after the owner's
        svc.tape(HAND, True, OUR_PRIO, OUR_TIP, ts=NOW - 60)
    if case == "fees unknown":
        svc.tape(HAND, True, None, None, ts=NOW - 60)
    if case == "sold part":  # a holding the owner sold part of: no buy since its last sale tells whose the rest is
        svc.tape(HAND, False, AXIOM_PRIO, AXIOM_TIP, ts=NOW - 60)
    paths = OperatorPaths(tmp_path / "data").ensure()
    (paths.root / "watch.json").write_text(json.dumps({"ts": NOW - 60, "first_seen": {HAND: NOW - 30}}))
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "left running" in reply and "H8WT" in reply, reply


class Hangs(FakeSvc):
    """A wallet read or an OKTech call that never answers (an RPC call retries for minutes: 7 Nov, about 250 s)."""

    def __init__(self, what, held=()):
        super().__init__(list(held), [{"id": "s1", "name": "a", "status": "running"}])
        self.what = what

    async def open_positions(self, address, ignore=None, unknown=None):
        if self.what == "wallet":
            await asyncio.sleep(3600)
        return await super().open_positions(address, ignore=ignore)

    async def oktech_status(self):
        if self.what == "oktech":
            await asyncio.sleep(3600)
        return await super().oktech_status()

    async def oktech_task(self, action, task_id):
        if self.what == "stop":
            await asyncio.sleep(3600)
        return await super().oktech_task(action, task_id)


@pytest.mark.parametrize("what, words", [("wallet", "wallet did not answer"), ("oktech", "OKTech did not answer"),
                                         ("stop", "failed: s1")])  # fmt: skip
def test_stop_answers_within_its_own_time_limit_when_a_read_hangs(tmp_path, monkeypatch, what, words):
    """Audit of 22 Nov (round#9): the one-minute pass is killed at 2 minutes (snipetech-commands.service) and its offset
    has already moved past the /stop: a hanging read left the owner with no reply, and no sniper stopped."""
    from snipetech.operator import commands

    monkeypatch.setattr(commands, "READ_SECS", 0.2)
    svc = Hangs(what)
    ctx, sent = _ctx(tmp_path, svc)
    reply = asyncio.run(asyncio.wait_for(handle_command(_cmd("stop"), **ctx), 5.0))
    assert ctx["paths"].stop_flag.exists() and sent == [reply] and words in reply, reply
    assert "send /stop again" in reply.lower() or what == "stop", reply


def test_stop_answers_in_time_when_every_stop_hangs(tmp_path, monkeypatch):
    """Six book snipers whose stops all hang would take 6 x READ_SECS: past the pass's 2 minutes. All of /stop's stops
    together wait STOPS_SECS at most; the ones not answered are named for the owner to stop again."""
    from snipetech.operator import commands

    monkeypatch.setattr(commands, "READ_SECS", 0.3)
    monkeypatch.setattr(commands, "STOPS_SECS", 0.4)
    svc = Hangs("stop")
    svc._snipers = [{"id": f"s{k}", "name": "a", "status": "running"} for k in range(6)]
    ctx, sent = _ctx(tmp_path, svc)
    reply = asyncio.run(asyncio.wait_for(handle_command(_cmd("stop"), **ctx), 1.2))  # six times 0.3 s is 1.8 s
    assert sent == [reply] and "stopped 0 sniper(s)" in reply and all(f"s{k}" in reply for k in range(6)), reply


def test_sell_answers_within_its_time_limits_when_the_wallet_or_the_sale_hangs(tmp_path, monkeypatch, sell_key):
    from snipetech.operator import commands

    monkeypatch.setattr(commands, "READ_SECS", 0.2)
    monkeypatch.setattr(commands, "SELL_SECS", 0.2)
    wallet, _secret = sell_key
    ctx, sent = _ctx(tmp_path, Hangs("wallet"))
    ctx["cfg"].operator.wallet = wallet
    reply = asyncio.run(asyncio.wait_for(handle_command(_cmd("sell"), **ctx), 5.0))
    assert "could not read" in reply and "nothing was sold" in reply and sent == [reply], reply

    async def hangs(*a, **k):
        await asyncio.sleep(3600)

    _svc, ctx, sent = _sell_ctx(tmp_path, wallet, hangs)
    reply = asyncio.run(asyncio.wait_for(handle_command(_cmd("sell", MINT), **ctx), 5.0))
    assert reply.startswith("Not sure it sold") and "did not answer" in reply and sent == [reply], reply


def test_stop_keeps_the_snipers_for_a_coin_of_ours_beside_the_owners_own(tmp_path):
    svc = StoreSvc(tmp_path, [_coin(HAND), _coin(MINE := "Mine1" + "1" * 39)])
    svc.tape(HAND, True, AXIOM_PRIO, AXIOM_TIP)
    svc.tape(MINE, True, OUR_PRIO, OUR_TIP)
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "Mine" in reply and "H8WT" not in reply, reply


def test_stop_leaves_a_sniper_whose_fees_are_not_on_record_running_beside_the_owners_coin(tmp_path):
    """Review of 22 Nov (livetest#0): the owner's coin is told by the fees of the snipers on record. One OKTech runs that
    the bot has no payload of (made in OKTech's app) pays fees of its own, so that coin may be its own: it keeps running
    (a stopped sniper strands its coin). The snipers on record are stopped: none of them bought it."""
    svc = StoreSvc(tmp_path, [_coin(HAND)], snipers=[{"id": "s1", "name": "snipetech copy x", "status": "running"},
                                                     {"id": "app1", "name": "made in the app", "status": "running"}])  # fmt: skip
    svc.tape(HAND, True, AXIOM_PRIO, AXIOM_TIP)
    reply, ctx = _stop(tmp_path, svc)
    assert svc.calls == [("stop", "s1")] and "stopped 1 sniper" in reply and "app1" in reply, reply
    assert "app1" in ctx["paths"].notes.read_text()


# -- round 2 of the audit (23 Nov), A1: a copy of ours that paid fees no payload names is no coin of the owner's -------

LEADER = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
COPY = "Copy1" + "1" * 39
ODD_PRIO, ODD_TIP = 1_500_000, 120_000_000  # a pair no payload on record names: OKTech changing what it pays


def _odd_copy_held(tmp_path, slots_behind=1):
    """A copy sniper of LEADER on record and running; the wallet holds COPY, bought `slots_behind` slots after
    LEADER's buy of it at fees no payload names."""
    from snipetech.models import Trade

    svc = StoreSvc(tmp_path, [_coin(COPY)], snipers=[{"id": "c1", "name": "snipetech copy lead", "status": "running"}])
    svc.store.add_oktech_sniper({"id": "c1", "kind": "copy", "source": LEADER, "created_at": NOW - 86_400,
                                 "payload": {"priorityFee": 0.001, "tipAmount": 0.199, "singleSnipe": True,
                                             "ctbotWallets": [{"address": LEADER}]}})  # fmt: skip
    for sig, user, slot, pos, fees in (("lead", LEADER, 5_000, 196, (5_000, 0)),
                                       ("copy", W, 5_000 + slots_behind, 12, (ODD_PRIO, ODD_TIP))):  # fmt: skip
        svc.store.add_trades([Trade(sig=sig, ev=0, slot=slot, pos=pos, ts=NOW - 3600, mint=COPY, venue="curve",
                                    user=user, is_buy=True, quote=1_000_000_000, base=1_000_000, fee=0, fee_bps=0,
                                    q_after=1, b_after=1, priority=fees[0], tip=fees[1])])  # fmt: skip
    return svc


def test_stop_keeps_the_snipers_beside_a_copy_that_paid_fees_no_payload_names(tmp_path):
    """A1: /stop reads whose each coin is by the fees our snipers pay. A copy at another pair read as "your own (bought
    by hand)" and its sniper was stopped while it held the coin (CLAUDE.md rule 1). One slot behind the wallet our
    sniper copies, it is a copy by its shape (measure/ours.CopyShape)."""
    svc = _odd_copy_held(tmp_path)
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "left running" in reply and "Copy" in reply, reply


def test_stop_keeps_them_once_the_watch_has_seen_that_copy_ten_minutes_too(tmp_path):
    """A1: a coin no buy of ours explains, seen ten minutes by the unsold-coin watch, is a stray that keeps no sniper
    running (book/run._strays, which reads a buy by its fees alone). A copy by its shape is explained by a buy of
    ours, however long the watch has seen it."""
    svc = _odd_copy_held(tmp_path)
    paths = OperatorPaths(tmp_path / "data").ensure()
    (paths.root / "watch.json").write_text(json.dumps({"ts": NOW - 60, "first_seen": {COPY: NOW - 700}}))
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "left running" in reply and "Copy" in reply, reply


def test_stop_still_stops_beside_the_owners_hand_buy_twenty_slots_behind_a_copied_wallet(tmp_path):
    """A1 turns no hand buy into a copy: eight seconds behind the copied wallet, at fees no sniper pays, it is the
    owner's own coin and keeps no sniper running."""
    svc = _odd_copy_held(tmp_path, slots_behind=20)
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [("stop", "c1")] and "your own" in reply, reply


# -- round 2 of the audit (23 Nov), A2: /stop read the wallet once -----------------------------------------------------


class Readings(StoreSvc):
    """StoreSvc whose wallet shows what each reading in turn is given (the last one from then on), counting them."""

    def __init__(self, tmp_path, readings, snipers=None):
        super().__init__(tmp_path, [], snipers)
        self.readings, self.reads = list(readings), 0

    async def open_positions(self, address, ignore=None, unknown=None):
        self.reads += 1
        got = self.readings[min(self.reads, len(self.readings)) - 1]
        if isinstance(got, Exception):
            raise got
        return [h for h in got if h.get("mint") not in set(ignore or ())]


OURS = "Ours1" + "1" * 39


def test_stop_reads_an_empty_wallet_a_second_time_before_it_stops_a_sniper(tmp_path):
    """A2: a copy bought a second before /stop's one wallet read had not shown yet, and /stop stopped its sniper beside
    an empty wallet (a stopped sniper strands its coin). One empty reading is no proof: /stop reads again 5 s later, as
    the live test's close and the round's clean-up of a dead run do."""
    svc = Readings(tmp_path, [[], [_coin(OURS)]])
    svc.tape(OURS, True, OUR_PRIO, OUR_TIP, ts=NOW - 1)
    reply, ctx = _stop(tmp_path, svc)
    assert svc.calls == [] and svc.reads == 2, reply
    assert "left running" in reply and "Ours" in reply and "coins are held" in reply
    assert ctx["paths"].stop_flag.exists()


def test_stop_stops_after_two_empty_readings_and_reads_once_when_nothing_would_be_stopped(tmp_path):
    svc = Readings(tmp_path, [[], []])
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [("stop", "s1")] and svc.reads == 2 and "stopped 1 sniper" in reply, reply
    # a coin of ours in the first reading: nothing would be stopped, so nothing is read again
    held = Readings(tmp_path, [[_coin(OURS)], []])
    held.tape(OURS, True, OUR_PRIO, OUR_TIP)
    reply, _ = _stop(tmp_path, held)
    assert held.calls == [] and held.reads == 1 and "left running" in reply, reply
    # no sniper running: nothing to stop, one reading
    idle = Readings(tmp_path, [[], []], snipers=[{"id": "s1", "name": "a", "status": "finished"}])
    reply, _ = _stop(tmp_path, idle)
    assert idle.calls == [] and idle.reads == 1 and "no sniper was running" in reply, reply


def test_stop_stops_nothing_when_the_second_reading_fails(tmp_path):
    svc = Readings(tmp_path, [[], RuntimeError("rpc down")])
    reply, ctx = _stop(tmp_path, svc)
    assert svc.calls == [] and "second time" in reply and "send /stop again" in reply.lower(), reply
    assert "no sniper stopped" in ctx["paths"].notes.read_text()


def test_the_second_reading_is_weighed_by_its_own_amounts(tmp_path):
    """A2: the owner's coin alone in the first reading; in the second there is more of it than their buys explain (a
    copy of ours into the same coin, not on the tape yet). That is a coin a sniper of ours may hold."""
    svc = Readings(tmp_path, [[_coin(HAND)], [_coin(HAND, raw=2_000_000)]])
    svc.tape(HAND, True, AXIOM_PRIO, AXIOM_TIP)
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and svc.reads == 2 and "left running" in reply and "H8WT" in reply, reply


def test_stop_holds_while_the_tape_has_a_buy_whose_coin_neither_reading_showed(tmp_path):
    """A2: a node can leave a coin out of both replies, or show it late; our own recorder has the buy seconds after
    it lands. A buy of the wallet on the tape in the last minute that no sale followed and no reading showed is a coin
    on its way (operator/leftovers._unseen, the rule the round's clean-up of a dead run stops by)."""
    svc = Readings(tmp_path, [[], []])
    svc.tape(OURS, True, OUR_PRIO, OUR_TIP, ts=NOW - 4)
    reply, ctx = _stop(tmp_path, svc)
    assert svc.calls == [] and "left running" in reply and "Ours1" in reply and "not shown" in reply, reply
    assert "send /stop again" in reply.lower() and "not shown" in ctx["paths"].notes.read_text()


def test_a_buy_on_the_tape_that_was_sold_or_is_old_holds_no_stop(tmp_path):
    sold = Readings(tmp_path, [[], []])
    sold.tape(OURS, True, OUR_PRIO, OUR_TIP, ts=NOW - 20)
    sold.tape(OURS, False, OUR_PRIO, 2_000_000, ts=NOW - 8)  # its 12 s exit sold it
    reply, _ = _stop(tmp_path, sold)
    assert sold.calls == [("stop", "s1")], reply
    old = Readings(tmp_path, [[], []])
    old.tape(OURS, True, OUR_PRIO, OUR_TIP, ts=NOW - 3600)  # an hour ago: two readings would show it
    reply, _ = _stop(tmp_path, old)
    assert old.calls == [("stop", "s1")], reply


def _on_chain(svc, *txs, age=6):
    """The wallet's own transactions as the RPC lists them, the newest `age` seconds before the command (one slot
    apart, in the order given): a coin off the tape (a PumpSwap coin past its 30 minutes) is on no tape."""
    from tests.chain import Chain

    slot0 = 480_000_000
    chain = Chain(t0=NOW - age - len(txs), slot0=slot0)
    for k, tx in enumerate(txs):
        chain.put(slot0 + round((k + 1) / 0.4), tx)
    svc.rpc = chain
    return chain


def test_stop_holds_while_the_chain_has_a_buy_whose_coin_neither_reading_nor_the_tape_shows(tmp_path):
    """A2: three of the six copies of 20 Nov bought coins the tape does not follow. The wallet's own newest
    transactions say whether it bought one in the last seconds."""
    from tests.chain import swap

    svc = Readings(tmp_path, [[], []])
    _on_chain(svc, swap("copy", W, OURS, tokens=38_000_000, sol=1.0, priority=0.001, tip=0.199))
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "left running" in reply and "Ours1" in reply and "not shown" in reply, reply


def test_a_buy_on_the_chain_that_was_sold_since_or_is_older_holds_no_stop(tmp_path):
    from tests.chain import swap

    sold = Readings(tmp_path, [[], []])
    bought = swap("copy", W, OURS, tokens=38_000_000, sol=1.0, priority=0.001, tip=0.199)
    exit_ = swap("exit", W, OURS, tokens=-38_000_000, sol=1.1, priority=0.001, tip=0.002, held=38_000_000)
    _on_chain(sold, bought, exit_)
    reply, _ = _stop(tmp_path, sold)
    assert sold.calls == [("stop", "s1")], reply
    old = Readings(tmp_path, [[], []])
    _on_chain(old, swap("copy", W, OURS, tokens=38_000_000, sol=1.0, priority=0.001, tip=0.199), age=600)
    reply, _ = _stop(tmp_path, old)
    assert old.calls == [("stop", "s1")], reply


def test_a_chain_that_does_not_answer_leaves_the_two_readings_and_the_tape_to_decide(tmp_path, monkeypatch):
    from snipetech.operator import commands

    class Hung:
        async def get_signatures(self, *a, **k):
            await asyncio.sleep(3600)

    monkeypatch.setattr(commands, "SECOND_WAIT_SECS", 0.2)
    svc = Readings(tmp_path, [[], []])
    svc.rpc = Hung()
    ctx, _sent = _ctx(tmp_path, svc)
    try:
        reply = asyncio.run(asyncio.wait_for(handle_command(_cmd("stop"), **ctx), 5.0))
    finally:
        svc.store.close()
    assert svc.calls == [("stop", "s1")] and "stopped 1 sniper" in reply, reply


def test_all_of_stop_stays_inside_the_passes_two_minutes(tmp_path, monkeypatch):
    """A2: the second reading and the look at the chain have limits of their own (SECOND_WAIT_SECS), and the stops take
    what is left of STOP_BUDGET_SECS: with every call as slow as its limit allows, /stop still answers before the
    one-minute pass is killed at two minutes."""
    from snipetech.operator import commands

    reads = commands.READ_SECS * 2 + SECOND_READ_SECS + commands.SECOND_WAIT_SECS * 2  # before the first stop is sent
    assert SECOND_READ_SECS == 5 and reads <= commands.STOP_BUDGET_SECS - 20 and commands.STOP_BUDGET_SECS <= 100
    monkeypatch.setattr(commands, "READ_SECS", 0.3)
    monkeypatch.setattr(commands, "STOPS_SECS", 5.0)
    monkeypatch.setattr(commands, "STOP_BUDGET_SECS", 0.6)
    svc = Hangs("stop")
    svc._snipers = [{"id": f"s{k}", "name": "a", "status": "running"} for k in range(6)]
    ctx, sent = _ctx(tmp_path, svc)
    reply = asyncio.run(asyncio.wait_for(handle_command(_cmd("stop"), **ctx), 1.5))
    assert sent == [reply] and "stopped 0 sniper(s)" in reply and all(f"s{k}" in reply for k in range(6)), reply


# -- review of round 2 (23 Nov), A2: the look for a buy on its way had holes of its own --------------------------------


def _copy_on_chain(mint=OURS, held=0, tokens=38_000_000, name="copy"):
    from tests.chain import swap

    return swap(name, W, mint, tokens=tokens, sol=1.0, priority=0.001, tip=0.199, held=held)


def test_a_buy_the_node_lists_with_no_block_time_yet_holds_the_stop(tmp_path):
    """Review of A2: a node lists a transaction of a second ago with no block time yet. Read as a time of zero it was
    older than the look's 30 s, so the freshest buy, the one the look is for, was passed over and its sniper stopped.
    No block time is a transaction too fresh to have one: it is read."""
    svc = Readings(tmp_path, [[], []])
    chain = _on_chain(svc, _copy_on_chain())
    real_sigs, real_tx = chain.get_signatures, chain.get_transaction

    async def sigs_no_time(address, **kw):
        return [{**row, "blockTime": None} for row in await real_sigs(address, **kw)]

    async def tx_no_time(signature):
        tx = await real_tx(signature)
        return tx and {**tx, "blockTime": None}

    chain.get_signatures, chain.get_transaction = sigs_no_time, tx_no_time
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "left running" in reply and "Ours1" in reply and "not shown" in reply, reply


def test_an_old_transaction_ends_the_look_and_what_is_listed_behind_it_is_not_read(tmp_path):
    """Review of A2: the wallet's signatures come newest first, so the first one older than the look ends it: nothing
    behind it is read (a buy of an hour ago is no buy on its way, with or without a block time on its row)."""
    svc = Readings(tmp_path, [[], []])
    chain = _on_chain(svc, _copy_on_chain(name="an hour ago"),
                      _copy_on_chain("Later" + "1" * 39, name="ten minutes ago"), age=600)  # fmt: skip
    real_sigs = chain.get_signatures

    async def oldest_untimed(address, **kw):
        rows = await real_sigs(address, **kw)
        return [*rows[:-1], {**rows[-1], "blockTime": None}]

    chain.get_signatures = oldest_untimed
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [("stop", "s1")], reply
    assert not [c for c in chain.calls if c[0] == "getTransaction"]


def test_stop_holds_for_a_copy_into_the_owners_coin_whose_tokens_have_not_shown(tmp_path):
    """Review of A2: the owner holds a coin they bought by hand; a wallet we copy buys it and our copy lands two
    seconds before /stop. Both readings still show the owner's amount, the tape and the fills do not have the buy yet,
    and the chain does. The coin had been "shown", so the buy counted for nothing and the sniper that had just bought
    was stopped. A landed buy no record holds yet is on its way, whatever the readings showed of its coin."""
    svc = Readings(tmp_path, [[_coin(HAND)], [_coin(HAND)]])
    svc.tape(HAND, True, AXIOM_PRIO, AXIOM_TIP)
    _on_chain(svc, _copy_on_chain(HAND, held=1_000_000, tokens=1_000_000))
    reply, ctx = _stop(tmp_path, svc)
    assert svc.calls == [] and "left running" in reply and "H8WTvo" in reply, reply
    assert "send /stop again" in reply.lower() and "not shown" in ctx["paths"].notes.read_text()


def test_the_owners_own_fresh_buy_of_their_coin_holds_no_stop_once_it_is_on_record(tmp_path):
    """Review of A2, the other way: the owner tops their own coin up by hand and sends /stop. The tape has that buy
    (at their fees) and the readings show what their buys explain: nothing of ours, and the snipers are stopped."""
    from snipetech.models import Trade

    top_up = _copy_on_chain(HAND, held=1_000_000, tokens=1_000_000, name="the owner's top-up")
    svc = Readings(tmp_path, [[_coin(HAND, raw=2_000_000)], [_coin(HAND, raw=2_000_000)]])
    svc.tape(HAND, True, AXIOM_PRIO, AXIOM_TIP)
    svc.store.add_trades([Trade(sig=top_up["transaction"]["signatures"][0], ev=0, slot=900, pos=0, ts=NOW - 6,
                                mint=HAND, venue="curve", user=W, is_buy=True, quote=1_000_000_000, base=1_000_000,
                                fee=0, fee_bps=0, q_after=1, b_after=1, priority=AXIOM_PRIO,
                                tip=AXIOM_TIP)])  # fmt: skip
    _on_chain(svc, top_up)
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [("stop", "s1")] and "your own" in reply, reply


def test_slow_readings_do_not_move_the_look_past_the_buy(tmp_path, monkeypatch):
    """Review of A2: the look reached 30 s back from the moment it was made, after the readings. With readings that
    took their time, a buy of just before /stop fell out of it. It reaches back from the moment /stop began."""
    from snipetech.operator import commands

    class Slow(Readings):
        async def open_positions(self, address, ignore=None, unknown=None):
            if self.reads:  # the second reading takes its time
                await asyncio.sleep(0.8)
            return await super().open_positions(address, ignore=ignore)

    monkeypatch.setattr(commands, "CHAIN_LOOK_SECS", 2.5)
    svc = Slow(tmp_path, [[], []])
    _on_chain(svc, _copy_on_chain(), age=1)  # two seconds before the command (the fake chain's whole seconds)
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "Ours1" in reply and "not shown" in reply, reply


def test_what_the_chain_showed_before_it_stopped_answering_still_counts(tmp_path, monkeypatch):
    """Review of A2: the look reads the wallet's transactions newest first and has 10 s. One that ran out of time
    counted for nothing at all, the buy it had already found with it."""
    from snipetech.operator import commands

    monkeypatch.setattr(commands, "SECOND_WAIT_SECS", 0.3)
    svc = Readings(tmp_path, [[], []])
    older = _copy_on_chain("Older" + "1" * 39, name="a buy before it")
    chain = _on_chain(svc, older, _copy_on_chain())
    real_tx = chain.get_transaction

    async def hangs_on_the_older(signature):
        if signature == older["transaction"]["signatures"][0]:
            await asyncio.sleep(3600)
        return await real_tx(signature)

    chain.get_transaction = hangs_on_the_older
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "Ours1" in reply and "not shown" in reply, reply


def test_a_buy_the_tape_gets_while_the_chain_is_read_holds_the_stop(tmp_path):
    """Review of A2: the tape was looked at before the chain, and the chain can take 10 s. A buy the recorder wrote in
    that time was seen by neither (here the node asked lists nothing of the wallet yet). The tape is looked at last."""
    svc = Readings(tmp_path, [[], []])

    class WritesMeanwhile:
        async def get_signatures(self, *a, **k):
            svc.tape(OURS, True, OUR_PRIO, OUR_TIP, ts=NOW - 2)
            return []

    svc.rpc = WritesMeanwhile()
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "Ours1" in reply and "not shown" in reply, reply


def test_a_copy_into_the_owners_coin_the_tape_gets_while_the_chain_is_read_keeps_the_snipers(tmp_path):
    """Review of A2: and one into a coin the readings showed as the owner's is weighed again after the look: with a
    buy of ours of it on the tape, that coin is no longer theirs beyond doubt."""
    svc = Readings(tmp_path, [[_coin(HAND)], [_coin(HAND)]])
    svc.tape(HAND, True, AXIOM_PRIO, AXIOM_TIP)

    class WritesMeanwhile:
        async def get_signatures(self, *a, **k):
            svc.tape(HAND, True, OUR_PRIO, OUR_TIP, ts=NOW - 2)
            return []

    svc.rpc = WritesMeanwhile()
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "left running" in reply and "H8WT" in reply, reply


def test_a_buy_behind_a_dozen_failed_sends_is_still_found(tmp_path):
    """Review of A2: OKTech sends a buy and a sale several times over, and the ones that fail are on the wallet's list
    too (14 Nov: 50 failed sends in one burst). Ten signatures were looked at: a dozen failed sends hid the buy."""
    from tests.chain import swap

    failed = [swap(f"failed {k}", W, "Else1" + "1" * 39, tokens=1_000, sol=0.01, priority=0.001,
                   err={"InstructionError": [3, {"Custom": 6002}]}) for k in range(12)]  # fmt: skip
    svc = Readings(tmp_path, [[], []])
    _on_chain(svc, _copy_on_chain(), *failed)  # the buy 18 s before the command, the failed sends after it
    reply, _ = _stop(tmp_path, svc)
    assert svc.calls == [] and "Ours1" in reply and "not shown" in reply, reply


def test_a_fresh_buy_of_a_coin_the_owner_ignores_holds_no_stop(tmp_path):
    """Review of A2: the owner's ignored coins are no sniper's anywhere (the readings and the tape's look leave them
    out); the chain's look left them in, and the owner's own buy of one held /stop back."""
    svc = Readings(tmp_path, [[], []])
    _on_chain(svc, _copy_on_chain(AIRDROP))
    reply, _ = _stop(tmp_path, svc, ignore_mints=[AIRDROP])
    assert svc.calls == [("stop", "s1")], reply


def test_resume_removes_flag_and_status_reports(tmp_path):
    svc = FakeSvc(held=[{"mint": "M1", "amount": 1}], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    snap = Snapshot(ts=NOW - 600, wallet=W, native_sol=1.25, wsol=0.0, held=[{"mint": "M1"}], snipers=svc._snipers, spent_today=0.42,
                    resources=[{"name": "disk", "status": "crit", "message": "5 GB free"}, {"name": "sol_float", "status": "ok", "message": "fine"}])  # fmt: skip
    ctx, sent = _ctx(tmp_path, svc, snap)
    ctx["paths"].stop_flag.write_text("x")
    ctx["paths"].expected.write_text(json.dumps({"snipers_running": 6, "held": 3}))  # a live test's expectation
    reply = asyncio.run(handle_command(_cmd("resume"), **ctx))
    assert not ctx["paths"].stop_flag.exists() and "resum" in reply.lower()
    assert expected_state(ctx["paths"]) == {"snipers_running": 0, "held": 0}  # /resume also forgets the old expectation
    reply = asyncio.run(handle_command(_cmd("status"), **ctx))
    assert "1.250" in reply and "0.42" in reply and "1 running" in reply and "disk" in reply and "fine" not in reply
    assert "book: off" in reply and "10 min" in reply
    assert "later phase" in asyncio.run(handle_command(_cmd("why", "W1"), **ctx))
    assert "/status" in asyncio.run(handle_command(_cmd("bogus"), **ctx))


def test_a_resume_with_no_stop_set_leaves_a_running_tests_expectation_alone(tmp_path):
    """Audit of 22 Nov (livetest#5): /resume deleted operator/expected.json before it looked for the stop flag. With no
    /stop set ("nothing to resume") a running test's own sniper and coin then read as deviations at every round's gate
    until the test ended, and woke the agents with them."""
    ctx, _sent = _ctx(tmp_path, FakeSvc(held=[], snipers=[]))
    ctx["paths"].expected.write_text(json.dumps({"snipers_running": 1, "held": 1}))  # a live test runs
    reply = asyncio.run(handle_command(_cmd("resume"), **ctx))
    assert "nothing to resume" in reply
    assert expected_state(ctx["paths"]) == {"snipers_running": 1, "held": 1}


def test_stop_flag_makes_zero_snipers_expected(tmp_path):
    paths = OperatorPaths(tmp_path).ensure()
    paths.expected.write_text(json.dumps({"snipers_running": 2, "held": 1}))
    assert expected_state(paths)["snipers_running"] == 2
    paths.stop_flag.write_text(str(NOW))
    assert expected_state(paths)["snipers_running"] == 0 and expected_state(paths)["held"] == 1


def test_round_handles_a_stop_command_before_the_snapshot(tmp_path, monkeypatch):
    from snipetech.operator.run import operate
    from snipetech.store import Store

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    paths.telegram.write_text(json.dumps({"chat_id": 7}))
    st = Store(data / "snipetech.db")
    svc = FakeSvc(held=[], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    svc.store = st
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    sent = []
    try:
        asyncio.run(operate(cfg, svc, repo=tmp_path, branch="b", now=lambda: NOW, services=lambda: {"snipetech-tape": "active", "snipetech-ops": "active"},
                            balances=lambda w: (1.0, 0.0, []), turn=lambda *a, **k: {"usd": 0, "tool_calls": 0, "stopped": "end_turn", "notes": 0},
                            disk_free_gb=lambda: 100.0, send=lambda t: sent.append(t) or True,
                            commands_get=lambda url, params: {"result": [_msg(5, 7, "/stop")]}))  # fmt: skip
    finally:
        st.close()
    assert svc.calls == [("stop", "s1")] and paths.stop_flag.exists()
    log = paths.log.read_text()
    assert "command /stop" in log and "stopped by owner" in log and any("stopped 1 sniper" in t for t in sent)


def _commands_env(tmp_path):
    from snipetech.store import Store

    data = tmp_path / "data"
    paths = OperatorPaths(data).ensure()
    paths.telegram.write_text(json.dumps({"chat_id": 7, "offset": 5}))
    st = Store(data / "snipetech.db")
    svc = FakeSvc(held=[], snipers=[{"id": "s1", "name": "a", "status": "running"}])
    svc.store = st
    cfg = Config()
    cfg.storage.db_path = str(data / "snipetech.db")
    cfg.operator.wallet = W
    return paths, st, svc, cfg


def test_commands_pass_handles_stop_within_a_minute_without_claude(tmp_path, monkeypatch):
    """`operate --commands` (a one-minute timer) polls Telegram and acts on /stop at once: no snapshot, no Claude,
    no API key needed. Before this the kill switch waited for the next 15-minute operator round."""
    from snipetech.operator.run import commands_pass

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    paths, st, svc, cfg = _commands_env(tmp_path)
    sent = []
    try:
        rc = asyncio.run(commands_pass(cfg, svc, now=lambda: NOW, send=lambda t: sent.append(t) or True,
                                       commands_get=lambda url, params: {"result": [_msg(5, 7, "/stop")]}))  # fmt: skip
    finally:
        st.close()
    assert rc == 0 and svc.calls == [("stop", "s1")] and paths.stop_flag.exists()
    assert "command /stop" in paths.log.read_text() and any("stopped 1 sniper" in t for t in sent)
    assert json.loads(paths.telegram.read_text())["offset"] == 6


def test_commands_lock_serializes_the_pollers(tmp_path):
    """The operator round and the one-minute pass share one Telegram offset: whoever holds operator/commands.lock
    polls, the other skips (and the updates are still there for the holder)."""
    import fcntl

    from snipetech.operator.run import COMMANDS_LOCK, commands_pass

    paths, st, svc, cfg = _commands_env(tmp_path)
    lock = paths.root / COMMANDS_LOCK
    fd = open(lock, "w")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        rc = asyncio.run(commands_pass(cfg, svc, now=lambda: NOW, send=lambda t: True,
                                       commands_get=lambda url, params: {"result": [_msg(5, 7, "/stop")]}))  # fmt: skip
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        st.close()
    assert rc == 0 and svc.calls == [] and not paths.stop_flag.exists()
    assert json.loads(paths.telegram.read_text())["offset"] == 5 and "skipped" in paths.log.read_text()


def test_cli_operate_commands_parses():
    from snipetech.cli import build_parser

    a = build_parser().parse_args(["operate", "--commands"])
    assert a.cmd == "operate" and a.commands is True


def test_status_names_the_owners_daily_budget(tmp_path):
    from snipetech.operator.budget_switch import set_budget

    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=1.0, wsol=0.0, held=[], snipers=[], spent_today=1.92)
    ctx, _ = _ctx(tmp_path, FakeSvc(held=[], snipers=[]), snap)
    assert "claude spend today 1.92 $ of 15 $" in asyncio.run(handle_command(_cmd("status"), **ctx))
    set_budget(ctx["paths"], ctx["cfg"], {"daily_usd": 200}, "telegram", NOW)
    assert "claude spend today 1.92 $ of 200 $" in asyncio.run(handle_command(_cmd("status"), **ctx))


def test_status_shows_the_wallet_wide_guard_and_todays_result(tmp_path):
    from snipetech.store import Store

    st = Store(tmp_path / "s.db")
    svc = FakeSvc(held=[], snipers=[])
    svc.store = st
    st.put_fill({"sig": "s1", "ts": NOW - 600, "wallet": W, "mint": "M", "side": "sell", "sol": 0.1, "tokens": 1.0,
                 "pnl_sol": -0.3})  # fmt: skip
    try:
        snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=11.0, wsol=0.2, held=[], snipers=[], spent_today=0.0)
        ctx, _ = _ctx(tmp_path, svc, snap)
        reply = asyncio.run(handle_command(_cmd("status"), **ctx))
        assert "risk guard: no new live trade once the day is 1 SOL down or the wallet is under 10 SOL" in reply
        assert "today -0.30 SOL" in reply and "wallet 11.20 SOL" in reply and "TRIPPED" not in reply
        ctx["snap"] = Snapshot(ts=NOW - 60, wallet=W, native_sol=9.0, wsol=0.0, held=[], snipers=[], spent_today=0.0)
        reply = asyncio.run(handle_command(_cmd("status"), **ctx))
        assert "TRIPPED" in reply and "under the 10 SOL floor" in reply
    finally:
        st.close()


def test_status_says_a_stop_the_owner_switched_off(tmp_path):
    """/status shows each stop of the guard on with its value, or off with who switched it off and when."""
    from snipetech.operator.risk_guard import set_risk

    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=3.4, wsol=0.1, held=[], snipers=[], spent_today=0.0)
    ctx, _ = _ctx(tmp_path, FakeSvc(held=[], snipers=[]), snap)
    set_risk(ctx["paths"], {"day_stop_sol": "off"}, "telegram", NOW - 1800)
    reply = asyncio.run(handle_command(_cmd("status"), **ctx))
    assert "risk guard: no new live trade once the wallet is under 10 SOL" in reply and "TRIPPED: " in reply
    assert "day stop off (the owner, 06 Nov 00:30 UTC)" in reply
    set_risk(ctx["paths"], {"floor_sol": "off"}, "telegram", NOW - 600)
    reply = asyncio.run(handle_command(_cmd("status"), **ctx))
    assert "day stop off (the owner, 06 Nov 00:30 UTC)" in reply and "floor off (the owner, 06 Nov 00:50 UTC)" in reply
    assert "TRIPPED" not in reply and "cannot tell" not in reply


def test_status_counts_a_starting_sniper_as_running(tmp_path):
    svc = FakeSvc(
        held=[], snipers=[{"id": "s1", "name": "a", "status": "starting"}, {"id": "s2", "name": "b", "status": "idle"}]
    )
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=1.0, wsol=0.0, held=[], snipers=svc._snipers, spent_today=0.0)
    ctx, _ = _ctx(tmp_path, svc, snap)
    assert "1 running" in asyncio.run(handle_command(_cmd("status"), **ctx))


def test_book_switch_from_telegram(tmp_path):
    """The owner switches the book on and off from Telegram; /status shows it; /stop still halts it."""
    from snipetech.book.switch import book_live

    svc = FakeSvc(held=[], snipers=[])
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=5.0, wsol=0.0, held=[], snipers=[], spent_today=0.0)
    ctx, sent = _ctx(tmp_path, svc, snap)
    assert not book_live(ctx["cfg"], ctx["paths"])
    reply = asyncio.run(handle_command(_cmd("book", "on"), **ctx))
    assert book_live(ctx["cfg"], ctx["paths"]) and "on" in reply.lower() and "/book off" in reply
    assert "book: on" in asyncio.run(handle_command(_cmd("status"), **ctx))
    assert "switched the book on" in ctx["paths"].notes.read_text()
    reply = asyncio.run(handle_command(_cmd("book", "off"), **ctx))
    assert not book_live(ctx["cfg"], ctx["paths"]) and "off" in reply.lower()
    assert "book: off" in asyncio.run(handle_command(_cmd("book"), **ctx)).lower()


def test_book_off_tells_the_truth(tmp_path):
    """Off stops what the book runs (except a sniper still selling a coin); and when the config file keeps the book on,
    /book off says so instead of claiming it is off."""
    svc = FakeSvc(held=[], snipers=[])
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=5.0, wsol=0.0, held=[], snipers=[], spent_today=0.0)
    ctx, sent = _ctx(tmp_path, svc, snap)
    asyncio.run(handle_command(_cmd("book", "on"), **ctx))
    reply = asyncio.run(handle_command(_cmd("book", "off"), **ctx)).lower()
    assert "stops its snipers that hold nothing" in reply and "still on" not in reply
    ctx["cfg"].book.enabled = True
    reply = asyncio.run(handle_command(_cmd("book", "off"), **ctx)).lower()
    assert "still on" in reply and "enabled = true" in reply and "/stop" in reply


def test_book_bar_from_telegram(tmp_path):
    from snipetech.book.switch import effective_min_score

    svc = FakeSvc(held=[], snipers=[])
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=5.0, wsol=0.0, held=[], snipers=[], spent_today=0.0)
    ctx, sent = _ctx(tmp_path, svc, snap)
    reply = asyncio.run(handle_command(_cmd("book", "bar", "0.001"), **ctx))
    assert "0.001" in reply and effective_min_score(ctx["cfg"], ctx["paths"]) == 0.001
    assert "owner set the book's bar" in ctx["paths"].notes.read_text()
    assert "bar 0.001" in asyncio.run(handle_command(_cmd("book", "status"), **ctx))
    reply = asyncio.run(handle_command(_cmd("book", "bar", "-1"), **ctx)).lower()
    assert "not changed" in reply and effective_min_score(ctx["cfg"], ctx["paths"]) == 0.001
    asyncio.run(handle_command(_cmd("book", "bar", "default"), **ctx))
    assert effective_min_score(ctx["cfg"], ctx["paths"]) == ctx["cfg"].book.min_score


def _sent_at(name, *args, age):
    return Command(name=name, args=list(args), chat_id=7, update_id=1, date=int(NOW - age))


def test_a_resume_that_waited_in_telegrams_queue_lifts_no_stop(tmp_path):
    """Audit of 22 Nov (livetest#4): /sell, /test and a /risk change older than SELL_MAX_AGE_SECS are refused, as "a
    change that waited in Telegram's queue may loosen the guard after the moment it was meant for"; a /resume three hours
    old still lifted the kill switch. /stop is obeyed whatever its age."""
    ctx, sent = _ctx(tmp_path, FakeSvc(held=[], snipers=[]))
    ctx["paths"].stop_flag.write_text(str(NOW - 4 * 3600))
    reply = asyncio.run(handle_command(_sent_at("resume", age=3 * 3600), **ctx))
    assert ctx["paths"].stop_flag.exists() and "180 min old" in reply and "again" in reply and sent == [reply]
    assert "180 min old" in ctx["paths"].notes.read_text()
    reply = asyncio.run(handle_command(_sent_at("resume", age=60), **ctx))
    assert not ctx["paths"].stop_flag.exists() and reply.startswith("resumed")
    asyncio.run(handle_command(_sent_at("stop", age=6 * 3600), **ctx))
    assert ctx["paths"].stop_flag.exists()


def test_a_book_on_or_bar_that_waited_is_refused_and_book_off_never_is(tmp_path):
    """Audit of 22 Nov (round#10): a /book on that waited out an outage (12 Nov: 12 hours) switched the live book on when
    it was read. /book off, which only halts, is obeyed whatever its age."""
    from snipetech.book.switch import book_live, effective_min_score

    ctx, _sent = _ctx(tmp_path, FakeSvc(held=[], snipers=[]))
    bar = effective_min_score(ctx["cfg"], ctx["paths"])
    reply = asyncio.run(handle_command(_sent_at("book", "on", age=6 * 3600), **ctx))
    assert not book_live(ctx["cfg"], ctx["paths"]) and "360 min old" in reply, reply
    reply = asyncio.run(handle_command(_sent_at("book", "bar", "0.0001", age=6 * 3600), **ctx))
    assert effective_min_score(ctx["cfg"], ctx["paths"]) == bar and "360 min old" in reply, reply
    asyncio.run(handle_command(_sent_at("book", "on", age=30), **ctx))
    assert book_live(ctx["cfg"], ctx["paths"])
    asyncio.run(handle_command(_sent_at("book", "off", age=6 * 3600), **ctx))
    assert not book_live(ctx["cfg"], ctx["paths"])


def test_book_status_reports_the_last_run(tmp_path):
    from snipetech.book.state import record_run

    from snipetech.store import Store

    svc = FakeSvc(held=[], snipers=[])
    svc.store = Store(tmp_path / "s.db")
    snap = Snapshot(ts=NOW - 60, wallet=W, native_sol=5.0, wsol=0.0, held=[], snipers=[], spent_today=0.0)
    ctx, sent = _ctx(tmp_path, svc, snap)
    record_run(
        svc.store,
        {"actions": [], "kill_reason": None},
        False,
        NOW - 120,
        ranked=0,
        best={"wallet": "2YDnmMQt", "score": 0.0013},
    )
    reply = asyncio.run(handle_command(_cmd("book", "status"), **ctx))
    assert "last run" in reply and "2YDnmM" in reply and "0.0013" in reply


# -- /sell: the owner's own exit, from the phone ---------------------------------------------------------------------

MINT = "5m5hP15rrB5kyF3V9xsbYfCnvKKr6ZzXozYwAd1kirCF"  # 6 Nov 20:33: 0.2 SOL bought, -65 % in a second, no exit fired
MINT2 = "FkaToq58AyzfWDGST12uUzDjeS2peUXyghTDZ3DLBvMJ"
MINT3 = "EtWnCDAGDSFH5L8SPvbnYebZJf19gbwRre8R17eg6v8V"
SIG = "3DavEsdqG12GHdBGLLbyAqx6EDnJwWQFvWq3wfCyAgZZwvQo1eM9ehXCp5QEmrZoEQ1nBZ7z28DBqyJi7HqNVnAT"
SIG2 = "AeV8FxFdhFkkxGQvNU5yWkgx3pV4ScGj1JFVhZJe1CF9whgyQWRWWNsxZb1pUiFhSVdSnGrTtXiJhPzq9sxgWXy"
RPC = object()  # the service's RPC client: /sell hands it to the sweep as it is
TAPE = object()  # the service's tape: /sell hands it to the sweep, which reads a PumpSwap coin's migration from it
# solana/sell.py's own words: a coin that left its curve, whose PumpSwap pool does not exist yet
MIGRATED = ("PumpSwap: its pool does not exist yet (the coin has left its curve, and pump.fun's move to PumpSwap has "
            "not landed); try again in a minute")  # fmt: skip
NO_CURVE = "no bonding curve account (not a pump.fun curve coin)"


class SellSvc(FakeSvc):
    """The service as /sell sees it: an RPC client for the sweep and the wallet's coins. OKTech is never asked."""

    def __init__(self, held=()):
        super().__init__(held=list(held), snipers=[])
        self.rpc = RPC
        self.store = TAPE
        self.asked: list[str] = []

    async def open_positions(self, address):
        self.asked.append(address)
        return self._held

    async def oktech_status(self):
        raise AssertionError("/sell asked OKTech for its tasks")

    async def oktech_task(self, action, task_id):
        raise AssertionError(f"/sell touched OKTech task {task_id} ({action})")


class FakeSweep:
    """solana.sell.sweep as /sell calls it: records (rpc, mints, slippage), and the tape it was handed in `stores`, and
    answers like the real one."""

    def __init__(self, result=None, exc=None):
        self.calls: list[tuple] = []
        self.stores: list = []
        self.result, self.exc = result, exc

    async def __call__(self, rpc, mints, slippage, store=None):
        self.calls.append((rpc, mints, slippage))
        self.stores.append(store)
        if self.exc is not None:
            raise self.exc
        return self.result


def _sold(mint, quote=0.07, sig=SIG, pumpswap=False):
    """A sale the real sweep sent: the whole holding, the quote and the floor at half of it; on PumpSwap the row also
    names the venue and the pool (solana/sell.py)."""
    row = {"mint": mint, "amount": 675_849_878_442, "quote_sol": quote, "floor_sol": quote / 2, "signature": sig}
    return row | ({"venue": "pumpswap", "pool": "AHym5tFZM45gVWwe1DHthAPHD1V7SuCfppqKAiP4YJxA"} if pumpswap else {})


def _held(mint, value, raw=675_849_878_442):
    return {"mint": mint, "amount": raw / 1e6, "raw": raw, "decimals": 6, "account": W, "value_sol": value,
            "priced": value is not None}  # fmt: skip


@pytest.fixture
def sell_key(monkeypatch):
    """A throwaway sniper wallet: its secret in SNIPER_SECRET as on the server. Returns (address, secret)."""
    from solders.keypair import Keypair

    kp = Keypair()
    monkeypatch.setenv("SNIPER_SECRET", str(kp))
    return str(kp.pubkey()), str(kp)


def _sell_ctx(tmp_path, wallet, sweep, held=()):
    svc = SellSvc(held)
    ctx, sent = _ctx(tmp_path, svc)
    ctx["cfg"].operator.wallet = wallet
    ctx["sweep"] = sweep
    return svc, ctx, sent


def test_sell_one_coin_sells_all_of_it_and_says_what_it_got(tmp_path, sell_key):
    wallet, secret = sell_key
    sweep = FakeSweep({"wallet": wallet, "holdings": 1, "results": [_sold(MINT)]})
    svc, ctx, sent = _sell_ctx(tmp_path, wallet, sweep)
    reply = asyncio.run(handle_command(_cmd("sell", MINT), **ctx))
    assert sweep.calls == [(RPC, [MINT], 0.5)]  # only that coin; the floor at half the quote, as `snipetech sweep`
    assert sweep.stores == [TAPE]  # the tape, where a coin that left its curve has its migration recorded
    assert MINT in reply and "0.07" in reply and "0.035" in reply and SIG in reply and sent == [reply]
    assert "PumpSwap" not in reply  # a curve sale says nothing about PumpSwap
    notes = ctx["paths"].notes.read_text()
    assert "[decision]" in notes and MINT in notes and SIG in notes and "/sell" in notes
    assert secret not in reply and secret not in notes and svc.calls == [] and svc.asked == []


def test_sell_all_takes_only_the_literal_word_all(tmp_path, sell_key):
    wallet, _ = sell_key
    sweep = FakeSweep({"wallet": wallet, "holdings": 3, "results": [_sold(MINT, 0.07, SIG), _sold(MINT2, 0.05, SIG2),
                                                                    {"mint": MINT3, "skipped": MIGRATED}]})  # fmt: skip
    svc, ctx, _ = _sell_ctx(tmp_path, wallet, sweep)
    for words in (["ALL"], ["All"], ["all", "now"], ["everything"], ["all", MINT], [MINT, MINT2]):
        reply = asyncio.run(handle_command(_cmd("sell", *words), **ctx))
        assert sweep.calls == [] and "nothing sold" in reply.lower(), words
    assert not ctx["paths"].notes.exists()  # nothing was tried, nothing to record
    reply = asyncio.run(handle_command(_cmd("sell", "all"), **ctx))
    assert sweep.calls == [(RPC, None, 0.5)]  # every coin the wallet holds
    assert MINT in reply and MINT2 in reply and SIG in reply and SIG2 in reply and "0.12" in reply  # 0.12 SOL in all
    assert MINT3 in reply and "Axiom" in reply and "OKTech" in reply  # off the curve: by hand
    assert "/sell all" in ctx["paths"].notes.read_text() and svc.calls == []


def test_sell_alone_lists_the_coins_with_the_line_to_send_and_sells_nothing(tmp_path, sell_key):
    wallet, _ = sell_key
    held = [_held(MINT2, 0.012, 10_000_000), _held(MINT, 0.069), _held(MINT3, None, 5_000_000)]
    sweep = FakeSweep({"wallet": wallet, "holdings": 3, "results": []})
    svc, ctx, sent = _sell_ctx(tmp_path, wallet, sweep, held)
    reply = asyncio.run(handle_command(_cmd("sell"), **ctx))
    assert sweep.calls == [] and svc.asked == [wallet] and sent == [reply]
    lines = reply.splitlines()
    at = [lines.index(f"/sell {m}") for m in (MINT, MINT2, MINT3)]  # each coin's own line is the command to send
    assert at == sorted(at)  # the most valuable first, the unpriced last
    assert "0.069" in reply and "0.012" in reply and "not known" in reply and "/sell all" in reply
    assert "PumpSwap" in lines[-1] and "still on the pump.fun curve" not in reply  # /sell all sells both kinds
    assert not ctx["paths"].notes.exists()  # a list decides nothing
    assert "/sell" in asyncio.run(handle_command(_cmd("help"), **ctx))


def test_sell_alone_with_nothing_held_or_an_unreadable_wallet(tmp_path, sell_key):
    class Unreadable(SellSvc):
        async def open_positions(self, address):
            raise httpx.ConnectError("all connection attempts failed")

    wallet, _ = sell_key
    sweep = FakeSweep()
    _, ctx, _ = _sell_ctx(tmp_path, wallet, sweep)
    assert "no coins" in asyncio.run(handle_command(_cmd("sell"), **ctx))
    ctx["svc"] = Unreadable()
    reply = asyncio.run(handle_command(_cmd("sell"), **ctx))
    assert "could not read" in reply and "ConnectError" in reply and sweep.calls == []


def test_a_bad_coin_address_is_refused_before_anything(tmp_path, sell_key):
    wallet, secret = sell_key
    sweep = FakeSweep({"wallet": wallet, "holdings": 0, "results": []})
    svc, ctx, _ = _sell_ctx(tmp_path, wallet, sweep)
    bad = ["abc", MINT + "x", MINT[:31], "z" * 44, MINT.replace("5", "0"), MINT[:-1] + "l", "pump.fun/coin/" + MINT,
           secret]  # fmt: skip
    for b in bad:
        reply = asyncio.run(handle_command(_cmd("sell", b), **ctx))
        assert "not a coin address" in reply and b not in reply, b  # a pasted secret is never echoed or logged
    assert sweep.calls == [] and svc.asked == [] and not ctx["paths"].notes.exists()


def test_sell_without_the_sell_key_says_where_it_goes(tmp_path, monkeypatch):
    monkeypatch.delenv("SNIPER_SECRET", raising=False)
    sweep = FakeSweep({"wallet": W, "holdings": 1, "results": [_sold(MINT)]})
    _, ctx, _ = _sell_ctx(tmp_path, W, sweep, [_held(MINT, 0.069)])
    for words in ([MINT], ["all"]):
        reply = asyncio.run(handle_command(_cmd("sell", *words), **ctx))
        assert "SNIPER_SECRET" in reply and "/etc/snipetech/env" in reply and "SSH" in reply and "Axiom" in reply
    assert sweep.calls == [] and "SNIPER_SECRET" in ctx["paths"].notes.read_text()
    assert "SNIPER_SECRET" in asyncio.run(handle_command(_cmd("sell"), **ctx))  # the list warns before it matters
    monkeypatch.setenv("SNIPER_SECRET", "  ")  # blank is missing
    assert "SNIPER_SECRET" in asyncio.run(handle_command(_cmd("sell", MINT), **ctx)) and sweep.calls == []


def test_sell_refuses_a_key_that_is_not_the_sniper_wallets(tmp_path, monkeypatch, sell_key):
    """The list reads the operator's wallet and the sale signs with SNIPER_SECRET: they must be one wallet, or a
    /sell all could empty a wallet the owner never meant."""
    wallet, secret = sell_key
    sweep = FakeSweep({"wallet": wallet, "holdings": 1, "results": [_sold(MINT)]})
    _, ctx, _ = _sell_ctx(tmp_path, W, sweep)  # the operator watches W; the key is another wallet's
    reply = asyncio.run(handle_command(_cmd("sell", "all"), **ctx))
    assert sweep.calls == [] and W[:6] in reply and wallet[:6] in reply and "/etc/snipetech/env" in reply
    monkeypatch.setenv("SNIPER_SECRET", "this-is-no-secret-key")
    reply = asyncio.run(handle_command(_cmd("sell", MINT), **ctx))
    assert sweep.calls == [] and "not a valid" in reply and "this-is-no-secret-key" not in reply
    monkeypatch.setenv("SNIPER_SECRET", secret)
    ctx["cfg"].operator.wallet = ""  # no wallet in the config: the key's wallet is the one
    asyncio.run(handle_command(_cmd("sell", MINT), **ctx))
    assert sweep.calls == [(RPC, [MINT], 0.5)]
    # the same key as the list of 64 numbers some wallets export: read like the base58 text
    from solders.keypair import Keypair

    monkeypatch.setenv("SNIPER_SECRET", json.dumps(list(bytes(Keypair.from_base58_string(secret)))))
    asyncio.run(handle_command(_cmd("sell", MINT), **ctx))
    assert sweep.calls == [(RPC, [MINT], 0.5)] * 2


def test_a_coin_sold_on_pumpswap_is_said_so_with_where_the_sol_lands(tmp_path, sell_key):
    """A coin that left its curve is sold on PumpSwap (solana/sell.py): the reply and the note say so, and that the SOL
    comes back as wrapped SOL into the wallet's WSOL account, as OKTech's own PumpSwap sale paid it (6 Nov)."""
    wallet, _ = sell_key
    sweep = FakeSweep({"wallet": wallet, "holdings": 1, "results": [_sold(MINT, 0.0549, SIG, pumpswap=True)]})
    _, ctx, sent = _sell_ctx(tmp_path, wallet, sweep)
    reply = asyncio.run(handle_command(_cmd("sell", MINT), **ctx))
    assert reply.startswith(f"Sold coin {MINT} on PumpSwap") and "0.0549" in reply and SIG in reply and sent == [reply]
    assert "WSOL" in reply and "only sells coins on the pump.fun curve" not in reply
    assert "on PumpSwap" in ctx["paths"].notes.read_text()
    sweep = FakeSweep({"wallet": wallet, "holdings": 2, "results": [_sold(MINT, 0.0549, SIG, pumpswap=True),
                                                                    _sold(MINT2, 0.05, SIG2)]})  # fmt: skip
    _, ctx, _ = _sell_ctx(tmp_path / "all", wallet, sweep)
    reply = asyncio.run(handle_command(_cmd("sell", "all"), **ctx))
    assert f"{MINT}: about 0.0549 SOL on PumpSwap" in reply and f"{MINT2}: about 0.0500 SOL, signature" in reply
    assert "WSOL" in reply


def test_sell_of_a_coin_off_the_curve_or_not_held(tmp_path, sell_key):
    """A coin that left its curve is no longer one /sell cannot sell: when PumpSwap refuses it (here its pool does not
    exist yet), the reply gives PumpSwap's reason, and the owner sells it by hand."""
    wallet, _ = sell_key
    for skipped, words in ((MIGRATED, ("moved to PumpSwap", "does not exist yet")),
                           (NO_CURVE, ("not a pump.fun curve coin",))):  # fmt: skip
        sweep = FakeSweep({"wallet": wallet, "holdings": 1, "results": [{"mint": MINT, "skipped": skipped}]})
        _, ctx, _ = _sell_ctx(tmp_path / words[0], wallet, sweep)
        reply = asyncio.run(handle_command(_cmd("sell", MINT), **ctx))
        assert (
            reply.startswith("Not sold") and all(w in reply for w in words) and "Axiom" in reply and "OKTech" in reply
        )
        assert "only sells coins on the pump.fun curve" not in reply
        assert "not sold" in ctx["paths"].notes.read_text()
    sweep = FakeSweep({"wallet": wallet, "holdings": 0, "results": []})
    _, ctx, _ = _sell_ctx(tmp_path / "none", wallet, sweep)
    reply = asyncio.run(handle_command(_cmd("sell", MINT), **ctx))
    assert reply.startswith("Nothing sold") and "holds none" in reply and wallet[:6] in reply
    assert "holds no coins" in asyncio.run(handle_command(_cmd("sell", "all"), **ctx))


def test_a_failed_sale_is_said_in_plain_words_and_leaks_nothing(tmp_path, monkeypatch, sell_key):
    from snipetech.solana.rpc import RpcError

    wallet, secret = sell_key
    monkeypatch.setenv("HELIUS_API_KEY", "helius-key-0123456789")
    refused = RpcError("sendTransaction", {"code": -32002, "message": "Transaction simulation failed: Error processing "
                                                                      "Instruction 2: custom program error: 0x1772"})  # fmt: skip
    sweep = FakeSweep(exc=refused)
    _, ctx, _ = _sell_ctx(tmp_path, wallet, sweep)
    reply = asyncio.run(handle_command(_cmd("sell", MINT), **ctx))
    assert "refused" in reply and "0x1772" in reply and f"/sell {MINT}" in reply and "Axiom" in reply
    down = httpx.ConnectError("connection refused: https://mainnet.helius-rpc.com/?api-key=helius-key-0123456789")
    ctx["sweep"] = FakeSweep(exc=down)
    reply = asyncio.run(handle_command(_cmd("sell", "all"), **ctx))
    assert "ConnectError" in reply and "api-key" not in reply and "helius" not in reply.lower()
    assert "still held" in reply  # how to see what sold before the error
    notes = ctx["paths"].notes.read_text()
    assert "0x1772" in notes and "api-key" not in notes and secret not in notes and secret not in reply


def test_only_the_owners_sell_is_obeyed(tmp_path, monkeypatch, sell_key):
    """The one-minute pass acts on /sell from the owner's chat and on nothing else. The real sweep is swapped for the
    fake where the command finds it."""
    from snipetech.operator.run import commands_pass

    wallet, _ = sell_key
    paths, st, svc, cfg = _commands_env(tmp_path)
    cfg.operator.wallet = wallet
    svc.rpc = RPC
    sweep = FakeSweep({"wallet": wallet, "holdings": 1, "results": [_sold(MINT)]})
    monkeypatch.setattr("snipetech.solana.sell.sweep", sweep)
    sent: list[str] = []
    strangers = {"result": [_msg(5, 9, f"/sell {MINT}"), _msg(6, 9, "/sell all")]}
    try:
        asyncio.run(commands_pass(cfg, svc, now=lambda: NOW, send=lambda t: sent.append(t) or True,
                                  commands_get=lambda url, params: strangers))  # fmt: skip
        assert sweep.calls == [] and sent == []
        asyncio.run(commands_pass(cfg, svc, now=lambda: NOW + 60, send=lambda t: sent.append(t) or True,
                                  commands_get=lambda url, params: {"result": [_msg(7, 7, f"/sell {MINT}")]}))  # fmt: skip
    finally:
        st.close()
    assert sweep.calls == [(RPC, [MINT], 0.5)] and any(SIG in t for t in sent)
    assert "command /sell" in paths.log.read_text() and svc.calls == []


def test_an_old_sell_message_is_refused(tmp_path, sell_key):
    """A /sell that waited in Telegram's queue (an outage, a restart) is never acted on late: the coins held now may not
    be the ones the owner meant. It is refused with its age; the owner sends it again if they still mean it."""
    wallet, _ = sell_key
    sweep = FakeSweep({"wallet": wallet, "holdings": 1, "results": [_sold(MINT)]})
    svc, ctx, sent = _sell_ctx(tmp_path, wallet, sweep)
    old = _cmd("sell", "all")
    old.date = int(ctx["now"]) - 3600
    reply = asyncio.run(handle_command(old, **ctx))
    assert sweep.calls == [] and "60 min old" in reply and "again" in reply
    fresh = _cmd("sell", "all")
    fresh.date = int(ctx["now"]) - 20
    asyncio.run(handle_command(fresh, **ctx))
    assert sweep.calls == [(RPC, None, 0.5)]


def test_a_command_carries_the_time_the_owner_sent_it():
    cmds, _ = parse_updates(
        [{"update_id": 5, "message": {"chat": {"id": 7}, "text": "/sell all", "date": 1_794_000_000}}], 7
    )
    assert cmds[0].date == 1_794_000_000


# -- /budget: what the agents may spend, the owner's call without SSH ----------------------------------------------------


def _budget(ctx, *args):
    return asyncio.run(handle_command(_cmd("budget", *args), **ctx))


def _owner_budget(ctx):
    from snipetech.operator.budget_switch import effective_budget

    return effective_budget(ctx["cfg"], ctx["paths"])


def test_budget_alone_shows_what_applies_and_todays_spend(tmp_path):
    from snipetech.operator.budget_switch import budget_file

    ctx, sent = _ctx(tmp_path, FakeSvc(held=[], snipers=[]))
    ctx["paths"].spend.write_text(json.dumps({"ts": NOW - 60, "mode": "operator", "usd": 1.92}) + "\n")
    reply = _budget(ctx)
    assert "15 $ a day (1.92 $ spent today)" in reply and "at most 2 $ and 20 tool calls a turn" in reply
    assert "an analysis every 2 h" in reply and "06:00 UTC" in reply and "/budget 200" in reply
    assert sent == [reply] and not budget_file(ctx["paths"]).exists() and not ctx["paths"].notes.exists()


def test_budget_with_the_days_dollars_alone_is_the_work_harder_profile(tmp_path):
    """The owner, 7 Nov: "the agents need to work harder, I can afford 200$ a day in api credits easily"."""
    from snipetech.operator.budget_switch import budget_file

    ctx, sent = _ctx(tmp_path, FakeSvc(held=[], snipers=[]))
    reply = _budget(ctx, "200")
    assert _owner_budget(ctx) == {"daily_usd": 200.0, "turn_usd": 6.0, "max_tool_calls": 40, "analyst_every_h": 2.0}
    assert reply.startswith("budget set:") and "200 $ a day" in reply and "6 $ and 40 tool calls a turn" in reply
    assert json.loads(budget_file(ctx["paths"]).read_text())["by"] == "telegram"
    assert (
        "[decision] owner set the agents' budget: budget set: Claude budget: 200 $ a day"
        in ctx["paths"].notes.read_text()
    )
    assert "/budget default" in reply and sent == [reply]


def test_budget_sets_each_value_named(tmp_path):
    ctx, _ = _ctx(tmp_path, FakeSvc(held=[], snipers=[]))
    _budget(ctx, "200", "turn", "4", "calls", "30", "every", "3")
    assert _owner_budget(ctx) == {"daily_usd": 200.0, "turn_usd": 4.0, "max_tool_calls": 30, "analyst_every_h": 3.0}
    _budget(ctx, "150$", "TURN", "$5", "calls", "25", "every", "1.5h")  # the way the owner writes dollars and hours
    assert _owner_budget(ctx) == {"daily_usd": 150.0, "turn_usd": 5.0, "max_tool_calls": 25, "analyst_every_h": 1.5}
    _budget(ctx, "turn", "3")  # no dollars a day: only what is named changes
    assert _owner_budget(ctx) == {"daily_usd": 150.0, "turn_usd": 3.0, "max_tool_calls": 25, "analyst_every_h": 1.5}
    reply = _budget(ctx, "100", "every", "0")  # the profile for what is not named; 0: the analysis once a day
    assert _owner_budget(ctx) == {"daily_usd": 100.0, "turn_usd": 6.0, "max_tool_calls": 40, "analyst_every_h": 0.0}
    assert "the analysis once a day after 06:00 UTC" in reply
    _budget(ctx, "3")  # a small day: the profile's turn is cut to the day, never more than it
    assert _owner_budget(ctx)["turn_usd"] == 3.0 and _owner_budget(ctx)["daily_usd"] == 3.0


SECRET_LIKE = "4wBqpZM9xaUcE7sRk2VtYhLnD3fJ8gQe1oPiWuAz6mXbNvCyTr5KjHs9dLqEf2GaZ"


def test_a_bad_budget_changes_nothing_and_says_why(tmp_path):
    ctx, sent = _ctx(tmp_path, FakeSvc(held=[], snipers=[]))
    _budget(ctx, "200")
    before = _owner_budget(ctx)
    for args, why in ((["900"], "500"), (["200", "turn", "30"], "25"), (["200", "calls", "3"], "5 to 80"),
                      (["200", "every", "0.1"], "0.25 to 24"), (["200", "turn"], "needs a number"),
                      (["200", "foo", "3"], "turn, calls and every"), (["0"], "more than 0"), (["5", "turn", "6"], "turn"),
                      (["default", "now"], "default"), ([SECRET_LIKE], "turn, calls and every")):  # fmt: skip
        reply = _budget(ctx, *args)
        assert reply.startswith("budget not changed:") and why in reply, (args, reply)
        assert _owner_budget(ctx) == before and "Claude budget: 200 $ a day" in reply  # what still applies
    assert (
        SECRET_LIKE not in "".join(sent) + ctx["paths"].notes.read_text()
    )  # a key pasted by mistake is never repeated
    assert ctx["paths"].notes.read_text().count("owner set the agents' budget") == 1


def test_budget_default_goes_back_to_the_config(tmp_path):
    from snipetech.operator.budget_switch import budget_file

    ctx, _ = _ctx(tmp_path, FakeSvc(held=[], snipers=[]))
    _budget(ctx, "200")
    reply = _budget(ctx, "default")
    assert not budget_file(ctx["paths"]).exists() and _owner_budget(ctx)["daily_usd"] == 15.0
    assert "back to the config" in reply and "15 $ a day" in reply
    assert "owner set the agents' budget: budget back to the config" in ctx["paths"].notes.read_text()


def test_only_the_owner_sets_the_budget_and_help_lists_it():
    from snipetech.operator.commands import HELP

    cmds, _ = parse_updates([_msg(1, 9, "/budget 500"), _msg(2, 7, "/budget 200 turn 4")], owner_chat_id=7)
    assert [(c.name, c.args) for c in cmds] == [("budget", ["200", "turn", "4"])]
    assert "/budget" in HELP and "/budget (later phase)" not in HELP and "/why <wallet> (later phase)" in HELP
