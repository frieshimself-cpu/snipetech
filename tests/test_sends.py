"""`sends`: a wallet's sends that failed on chain, placed at the first buys of the wallets it follows (22 Nov).

The scout's study of 8r6N8w (I605) found the bot right behind Cented, Decu and theo side on 1 in 10 of their first buys
of 1.9 SOL or more, and could not tell a filter from races it lost: a failed send is not on the tape. The chain has
every send that landed, the failed ones too, each with the fee it paid (the base fee and its bid's priority; its tip is
undone with it). This reads them and places each try."""

import asyncio
import json

import pytest

from snipetech.analysis import sends as S
from snipetech.models import Trade
from snipetech.store import Store

BOT = "8r6N8wnuFwk44LKbmMkoQbFhKjpG1J3E94nKS9Ag3c4W"
LEAD = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
OTHER = "5Y9pyDgrXkAqfFbvb5YhELc3VQhGVfkHTFpaVR1GFJmb"
NOW = 1_794_045_600
T0 = NOW - 3600


def _t(user, mint, slot, pos, sol, buy=True, ts=None):
    return Trade(sig=f"{user[:4]}-{mint}-{slot}-{pos}-{buy}", ev=0, slot=slot, pos=pos,
                 ts=int(ts if ts is not None else T0 + (slot - 1000) * 0.4), mint=mint, venue="curve", user=user,
                 is_buy=buy, quote=round(sol * 1e9), base=1_000_000, fee=0, fee_bps=100, q_after=1, b_after=1)  # fmt: skip


def _failed(sig, slot, mint, fee, err=None):
    """A failed transaction of the bot's as getTransaction returns it: the coin among its accounts, its fee paid."""
    return {"slot": slot, "blockTime": int(T0 + (slot - 1000) * 0.4),
            "meta": {"err": err or {"InstructionError": [3, {"Custom": 6002}]}, "fee": fee, "loadedAddresses": {}},
            "transaction": {"signatures": [sig], "message": {"accountKeys": [BOT, f"curve-{mint}", mint]}}}  # fmt: skip


class Chain:
    """The bot's signatures, newest first, a page a request; and its transactions."""

    def __init__(self, entries, txs):
        self.entries, self.txs = entries, txs
        self.calls: list[tuple] = []
        self.leaders: dict[int, str] | Exception = {}  # slot -> the block's leader (a validator), or the refusal

    async def get_signatures(self, address, before=None, until=None, limit=1000):
        assert address == BOT
        i = 0 if before is None else [e["signature"] for e in self.entries].index(before) + 1
        page = self.entries[i : i + limit]
        self.calls.append(("sigs", len(page)))
        return page

    async def get_transactions(self, sigs):
        self.calls.append(("txs", len(sigs)))
        return {s: self.txs.get(s) for s in sigs}

    async def get_slot_leaders(self, start, limit):
        self.calls.append(("leaders", start, limit))
        if isinstance(self.leaders, Exception):
            raise self.leaders
        return [self.leaders.get(s, "V0") for s in range(start, start + limit)]


def _world():
    """LEAD's first buys: M1 (2.5 SOL, the bot right behind it), M2 (3 SOL, the bot's send failed), M3 (2 SOL, nothing
    from the bot); M4 is too small to be a moment and LEAD's second buy of M1 is no first buy. The bot's failed send at
    slot 1400 names a coin no moment has."""
    store = Store(":memory:")
    store.add_trades([
        _t(LEAD, "M1", 1100, 0, 2.5), _t(BOT, "M1", 1100, 1, 6.0),
        _t(LEAD, "M2", 1200, 0, 3.0), _t(OTHER, "M2", 1200, 1, 1.0),
        _t(LEAD, "M3", 1300, 0, 2.0),
        _t(LEAD, "M4", 1350, 0, 0.5),
        _t(LEAD, "M1", 1360, 0, 2.2),
        _t(OTHER, "ElseMint", 2000, 0, 0.1),
    ])  # fmt: skip
    entries = [  # newest first, as the RPC lists them
        {"signature": "f9", "slot": 1400, "err": {"x": 1}, "blockTime": int(T0 + 400 * 0.4)},
        {"signature": "f2", "slot": 1201, "err": {"x": 1}, "blockTime": int(T0 + 201 * 0.4)},
        {"signature": "ok1", "slot": 1100, "err": None, "blockTime": int(T0 + 100 * 0.4)},
    ]
    txs = {"f2": _failed("f2", 1201, "M2", 5000 + 480_000_000), "f9": _failed("f9", 1400, "M9", 5000 + 1_000)}
    return store, Chain(entries, txs)


def _run(store, chain, **kw):
    return asyncio.run(S.sends(store, store, chain, BOT, [LEAD], NOW, **{"hours": 2.0, **kw}))


def test_each_first_buy_of_a_leader_is_placed_as_landed_failed_or_untried():
    store, chain = _world()
    out = _run(store, chain)
    assert out["moments"] == 3  # M1, M2, M3: M4 is under 1.9 SOL, the second M1 buy is no first buy
    assert out["outcomes"] == {"adjacent": 1, "block": 0, "later": 0, "failed": 1, "none": 1}
    assert out["tried"] == 2 and out["per_leader"][LEAD]["tried"] == 2


def test_a_failed_send_pays_its_priority_and_says_its_error():
    store, chain = _world()
    out = _run(store, chain)
    f = out["failed"]
    assert f["sends"] == 2 and f["fetched"] == 2 and f["placed"] == 1
    assert f["placed_fee_sol"] == pytest.approx(0.480005) and f["fee_sol"] == pytest.approx(0.480005 + 0.000006)
    assert f["errors"] == {json.dumps({"InstructionError": [3, {"Custom": 6002}]}, separators=(",", ":")): 2}


def test_each_outcome_carries_only_the_fees_of_its_own_failed_sends():
    """22 Nov, job 20261122-074139-296636 read "15 with only failed sends (2.0497 SOL in their fees)": the 2.0497 SOL
    was what all 764 failed sends at the 62 tried moments paid, those beside the 46 buys that landed right behind
    included. The record then quoted 0.0008 SOL a failed send, the average over every failed send read, most of them at
    no moment; at the tries a failed send cost 0.0027. Each outcome now carries its own failed sends and their fees,
    and the verdict says what a failed send at a try cost."""
    store = Store(":memory:")
    store.add_trades([
        _t(LEAD, "M1", 1100, 0, 2.5), _t(BOT, "M1", 1100, 1, 6.0),  # landed right behind, two sends lost beside it
        _t(LEAD, "M2", 1200, 0, 3.0),  # only one failed send here
    ])  # fmt: skip
    entries = [
        {"signature": "f2", "slot": 1201, "err": {"x": 1}, "blockTime": int(T0 + 201 * 0.4)},
        {"signature": "f1b", "slot": 1101, "err": {"x": 1}, "blockTime": int(T0 + 101 * 0.4)},
        {"signature": "f1a", "slot": 1100, "err": {"x": 1}, "blockTime": int(T0 + 100 * 0.4)},
        {"signature": "ok1", "slot": 1100, "err": None, "blockTime": int(T0 + 100 * 0.4)},
    ]
    txs = {"f1a": _failed("f1a", 1100, "M1", 1_000_000), "f1b": _failed("f1b", 1101, "M1", 1_000_000),
           "f2": _failed("f2", 1201, "M2", 1_000_000)}  # fmt: skip
    out = _run(store, Chain(entries, txs))
    f = out["failed"]
    assert out["outcomes"]["adjacent"] == 1 and out["outcomes"]["failed"] == 1
    assert f["placed"] == 3 and f["placed_fee_sol"] == pytest.approx(0.003)
    assert f["by_outcome"]["adjacent"] == {"sends": 2, "fee_sol": pytest.approx(0.002)}
    assert f["by_outcome"]["failed"] == {"sends": 1, "fee_sol": pytest.approx(0.001)}
    assert f["by_outcome"]["block"] == f["by_outcome"]["later"] == {"sends": 0, "fee_sol": 0.0}
    assert "1 with only failed sends (0.0010 SOL in their fees)" in out["verdict"]
    assert ("The 3 failed sends at its tries cost 0.0030 SOL, 0.0010 a send, 0.0020 of it beside the buys that "
            "landed.") in out["verdict"]  # fmt: skip


def test_only_the_failed_transactions_are_fetched_and_every_request_is_counted():
    store, chain = _world()
    out = _run(store, chain)
    assert ("txs", 2) in chain.calls and out["credits"] == 3  # one listing, two transactions
    assert store.usage_month(S.USAGE, NOW) == 3


def test_the_credit_limit_stops_before_a_request_that_would_pass_it():
    store, chain = _world()
    out = _run(store, chain, max_credits=2)  # the listing fits, the two transactions do not
    assert out["stopped"] == "credits" and out["failed"]["fetched"] == 0 and out["credits"] == 1


def test_the_window_ends_where_the_tape_ends():
    """A failed send after the tape's last trade has no moment the tape could hold: it is not read at all."""
    store, chain = _world()
    chain.entries.insert(0, {"signature": "late", "slot": 9999, "err": {"x": 1}, "blockTime": NOW + 10})
    out = _run(store, chain)
    assert out["failed"]["sends"] == 2 and out["window"][1] <= NOW


def test_the_command_runs_from_the_command_line(monkeypatch, tmp_path, capsys):
    from snipetech import cli

    got = {}

    async def fake(store, tape, rpc, wallet, leaders, now, **kw):
        got.update(wallet=wallet, leaders=leaders, **kw)
        return {"wallet": wallet, "moments": 0, "tried": 0, "outcomes": {}, "failed": {}, "credits": 0,
                "stopped": None, "verdict": "no moment"}  # fmt: skip

    monkeypatch.setattr(S, "sends", fake)
    db = tmp_path / "snipetech.db"
    Store(db).close()
    argv = ["--db", str(db), "sends", BOT, "--leader", LEAD, "--hours", "72", "--min-sol", "1.9", "--json"]
    assert cli.main(argv) == 0
    assert got["wallet"] == BOT and got["leaders"] == [LEAD] and got["hours"] == 72.0 and got["min_sol"] == 1.9
    assert json.loads(capsys.readouterr().out)["wallet"] == BOT


def test_without_a_leader_the_wallets_its_first_buys_followed_most_are_its_leaders(monkeypatch, tmp_path, capsys):
    from snipetech import cli
    from snipetech.analysis import playbook as P

    got = {}

    async def fake(store, tape, rpc, wallet, leaders, now, **kw):
        got["leaders"] = leaders
        return {"wallet": wallet, "verdict": "x", "per_leader": {}}

    monkeypatch.setattr(S, "sends", fake)
    monkeypatch.setattr(P, "playbook", lambda *a, **kw: {"triggers": [{"wallet": LEAD, "coins": 17},
                                                                       {"wallet": OTHER, "coins": 2}]})  # fmt: skip
    db = tmp_path / "snipetech.db"
    Store(db).close()
    assert cli.main(["--db", str(db), "sends", BOT]) == 0
    assert got["leaders"] == [LEAD]  # a wallet it followed on fewer than 3 coins is no leader


# -- the route check's first step (audit of 22 Nov, goal#1; the fee analyst's I617) ----------------------------------


def test_each_moment_is_listed_with_its_outcome_its_failed_sends_and_who_stood_within_three_places():
    """Before a sender of our own is built: who stands 1-3 block places behind the leaders, and where the bot's failed
    sends fall. 22 Nov, on the tape: a buy of 0.1 SOL or more stood within 3 places after the leader on 65 % of 386
    first buys of Cented, Decu and theo side, within 3 places before it on 1. Every moment is listed with its outcome,
    the bot's failed sends at it (slots after the leader's) and the first other buyer within 3 places, and the verdict
    counts both sides. A buy in the leader's own transaction is no buyer behind it, nor one under 0.1 SOL."""
    store, chain = _world()
    store.add_trades([
        _t(OTHER, "M3", 1300, 2, 0.05),  # too small to count
        Trade(sig=_t(LEAD, "M3", 1300, 0, 2.0).sig, ev=1, slot=1300, pos=0, ts=int(T0 + 300 * 0.4), mint="M3",
              venue="curve", user=OTHER, is_buy=True, quote=round(1.5e9), base=1, fee=0, fee_bps=100, q_after=1,
              b_after=1),  # the leader's own transaction
    ])  # fmt: skip
    out = _run(store, chain)
    rows = {r["mint"]: r for r in out["moments_list"]}
    assert [r["mint"] for r in out["moments_list"]] == ["M1", "M2", "M3"]
    assert rows["M1"]["outcome"] == "adjacent" and rows["M1"]["next_within3"] == {"place": 1, "wallet": BOT, "sol": 6.0}
    assert rows["M2"]["outcome"] == "failed" and rows["M2"]["next_within3"] == {"place": 1, "wallet": OTHER, "sol": 1.0}
    assert rows["M2"]["failed_sends"] == {"n": 1, "fee_sol": 0.480005, "slots_after": [1]}
    assert rows["M3"]["outcome"] == "none" and rows["M3"]["next_within3"] is None
    assert rows["M1"]["leader"] == LEAD and rows["M1"]["slot"] == 1100 and rows["M1"]["sol"] == 2.5
    assert "validator" not in rows["M1"] and not any(c[0] == "leaders" for c in chain.calls)
    assert out["near"] == {"places": 3, "min_sol": 0.1, "taken_after": 2, "taken_before": 0}
    assert ("Another wallet's buy of 0.1 SOL or more stood within 3 block places after the leader on 2 of the 3, "
            "before it on 0.") in out["verdict"]  # fmt: skip


def test_with_validators_each_moment_names_its_block_leader_and_the_moments_split_by_validator():
    """Whether the places right behind are sold by some validators (a back-run auction anyone could join) or held by
    a private route shows in how the taken moments split by the block's leader: getSlotLeaders, one request for up to
    5,000 slots, counted into the month at a credit a request."""
    store, chain = _world()
    chain.leaders = {1100: "V1", 1200: "V2", 1300: "V1"}
    out = _run(store, chain, validators=True)
    assert [r["validator"] for r in out["moments_list"]] == ["V1", "V2", "V1"]
    assert out["by_validator"] == [
        {"validator": "V1", "moments": 2, "taken_after": 1, "tried": 1, "adjacent": 1},
        {"validator": "V2", "moments": 1, "taken_after": 1, "tried": 1, "adjacent": 0},
    ]
    assert ("leaders", 1100, 201) in chain.calls and out["credits"] == 4  # one listing, two transactions, one leaders
    assert out["validators_error"] is None


def test_the_validators_are_read_before_the_failed_sends_so_a_credit_limit_cuts_those():
    """Review of 22 Nov: a 72-hour run behind Cented, Decu and theo side spends the 5,000-credit limit on failed sends
    (job 20261122-074139-296636 stopped at 4,907), and the leaders read after them (about 130 requests for 72 hours)
    found 93 credits left: the later moments would have had no validator. The leaders are read first; the limit then
    cuts the failed sends, which the result already says are counted in part."""
    store, chain = _world()
    chain.leaders = {1100: "V1", 1200: "V2", 1300: "V1"}
    out = _run(store, chain, validators=True, max_credits=3)  # the listing and the leaders fit, the two sends do not
    assert [r["validator"] for r in out["moments_list"]] == ["V1", "V2", "V1"] and out["validators_error"] is None
    assert out["stopped"] == "credits" and out["failed"]["fetched"] == 0 and out["credits"] == 2
    assert [c[0] for c in chain.calls] == ["sigs", "leaders"]


def test_a_leader_lookup_that_fails_leaves_the_validators_unknown_and_says_why():
    """A node that no longer has an epoch's leader schedule refuses: the moments keep their places, the validator is
    unknown, and the result says why rather than failing the job."""
    store, chain = _world()
    chain.leaders = RuntimeError("leader schedule for epoch 1 is unavailable")
    out = _run(store, chain, validators=True)
    assert all(r["validator"] is None for r in out["moments_list"]) and out["by_validator"] == []
    assert "leader schedule for epoch 1 is unavailable" in out["validators_error"]
    assert out["outcomes"]["adjacent"] == 1


def test_the_rpc_reads_the_slot_leaders_of_a_range():
    import httpx

    from snipetech.solana.rpc import SolanaRpc

    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        asked.append((body["method"], body["params"]))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": ["V1", "V2"]})

    async def run():
        rpc = SolanaRpc("http://rpc.test", max_rps=500, max_retries=1)
        await rpc.client.aclose()
        rpc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        out = await rpc.get_slot_leaders(1100, 2)
        await rpc.close()
        return out

    assert asyncio.run(run()) == ["V1", "V2"] and asked == [("getSlotLeaders", [1100, 2])]


def test_the_command_reads_the_validators_only_when_asked(monkeypatch, tmp_path, capsys):
    from snipetech import cli

    got = []

    async def fake(store, tape, rpc, wallet, leaders, now, **kw):
        got.append(kw.get("validators"))
        return {"wallet": wallet, "verdict": "x", "per_leader": {}}

    monkeypatch.setattr(S, "sends", fake)
    db = tmp_path / "snipetech.db"
    Store(db).close()
    assert cli.main(["--db", str(db), "sends", BOT, "--leader", LEAD]) == 0
    assert cli.main(["--db", str(db), "sends", BOT, "--leader", LEAD, "--validators"]) == 0
    assert got == [False, True]
