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
ERR = {"InstructionError": [3, {"Custom": 6002}]}  # a failed send's error, in its listing and in its transaction


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
        self.calls: list[tuple[str, int]] = []

    async def get_signatures(self, address, before=None, until=None, limit=1000):
        assert address == BOT
        i = 0 if before is None else [e["signature"] for e in self.entries].index(before) + 1
        page = self.entries[i : i + limit]
        self.calls.append(("sigs", len(page)))
        return page

    async def get_transactions(self, sigs):
        self.calls.append(("txs", len(sigs)))
        return {s: self.txs.get(s) for s in sigs}


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
    entries = [  # newest first, as the RPC lists them; a failed one's listing carries its transaction's own error
        {"signature": "f9", "slot": 1400, "err": ERR, "blockTime": int(T0 + 400 * 0.4)},
        {"signature": "f2", "slot": 1201, "err": ERR, "blockTime": int(T0 + 201 * 0.4)},
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
    # audit 22 Nov (tools#0): "unread" is the outcome of a moment whose sends the credit limit left unread
    assert out["outcomes"] == {"adjacent": 1, "block": 0, "later": 0, "failed": 1, "none": 1, "unread": 0}
    assert out["tried"] == 2 and out["per_leader"][LEAD]["tried"] == 2


def test_a_failed_send_pays_its_priority_and_says_its_error():
    store, chain = _world()
    out = _run(store, chain)
    f = out["failed"]
    # audit 22 Nov (tools#0): f9 landed near no moment, so it is not fetched; its error is read off the listing
    assert f["sends"] == 2 and f["near"] == 1 and f["fetched"] == 1 and f["placed"] == 1
    assert f["placed_fee_sol"] == pytest.approx(0.480005) and f["fee_sol"] == pytest.approx(0.480005)
    assert f["errors"] == {json.dumps(ERR, separators=(",", ":")): 2}


def test_only_the_failed_transactions_near_a_moment_are_fetched_and_every_request_is_counted():
    store, chain = _world()
    out = _run(store, chain)
    # audit 22 Nov (tools#0): f9 (slot 1400) is no try at any moment: only f2 is fetched
    assert ("txs", 1) in chain.calls and out["credits"] == 2  # one listing, one transaction
    assert store.usage_month(S.USAGE, NOW) == 2


def test_the_credit_limit_stops_before_a_request_that_would_pass_it():
    store, chain = _world()
    # audit 22 Nov (tools#0): one transaction is fetched now, so a limit of 1 is what the listing alone fits
    out = _run(store, chain, max_credits=1)  # the listing fits, the transaction does not
    assert out["stopped"] == "credits" and out["failed"]["fetched"] == 0 and out["credits"] == 1
    assert out["outcomes"]["unread"] == 1 and out["outcomes"]["none"] == 1  # M2's try is unread; M3 had no send near


def _cut_world(near_last: bool = True, far: int = 0):
    """Two first buys of LEAD with nothing of the bot's landed: M1 (slot 1100) and M2 (slot 1200), each tried with
    one failed send that names it; `far` newer failed sends name no moment's coin and land near no moment."""
    store = Store(":memory:")
    store.add_trades([_t(LEAD, "M1", 1100, 0, 2.5), _t(LEAD, "M2", 1200, 0, 3.0), _t(OTHER, "Else", 8000, 0, 0.1)])
    entries = [{"signature": f"far{k}", "slot": 1500 + k, "err": ERR, "blockTime": int(T0 + (500 + k) * 0.4)}
               for k in range(far)][::-1]  # fmt: skip
    entries += [{"signature": "f2", "slot": 1201, "err": ERR, "blockTime": int(T0 + 201 * 0.4)},
                {"signature": "f1", "slot": 1101, "err": ERR, "blockTime": int(T0 + 101 * 0.4)}]  # fmt: skip
    txs = {e["signature"]: _failed(e["signature"], e["slot"], "OTHER", 900_000) for e in entries}
    txs |= {"f1": _failed("f1", 1101, "M1", 900_000), "f2": _failed("f2", 1201, "M2", 900_000)}
    return store, Chain(entries, txs)


def test_a_failed_send_that_landed_near_no_moment_is_never_fetched():
    """Audit 22 Nov (tools#0): the listing already gives each send's slot, and only a send that landed within
    TRY_SLOTS of a moment can be a try at it. The first real run paid 4,136 of its 4,907 credits for 4,136
    transactions no moment could hold, and the limit then cut the oldest sends, the ones a moment needed."""
    store, chain = _cut_world(far=50)
    out = _run(store, chain)
    assert sum(n for kind, n in chain.calls if kind == "txs") == 2  # f1 and f2: none of the 50
    assert out["failed"]["sends"] == 52 and out["failed"]["near"] == 2 and out["failed"]["placed"] == 2
    assert out["outcomes"]["failed"] == 2 and out["credits"] == 3 and out["stopped"] is None


def test_the_old_sends_of_a_busy_bot_are_read_inside_the_default_limit():
    """Audit 22 Nov (tools#0), the first run's shape: 6,000 newer failed sends that no moment can hold, then the tries
    at two old moments. The run used to fetch from the newest down, stop at the default 5,000 credits and call both
    moments untried ("none"); it reads the two tries for 7 listing pages and 2 transactions."""
    store, chain = _cut_world(far=6000)
    out = _run(store, chain)
    assert out["outcomes"]["failed"] == 2 and out["outcomes"]["none"] == 0 and out["outcomes"]["unread"] == 0
    assert out["credits"] == 7 + 2 and out["stopped"] is None


def test_a_moment_whose_tries_the_limit_left_unread_reads_unread_not_untried(monkeypatch):
    """Audit 22 Nov (tools#0): when the limit cuts the sends near the moments, the moments whose sends were not read
    are not "none" (nothing of the bot reached the chain): their tries are on chain and in the listing, unread."""
    store, chain = _cut_world()
    monkeypatch.setattr(S, "FETCH_CHUNK", 1)
    out = _run(store, chain, max_credits=2)  # the listing and one transaction: f2, the newer
    assert out["stopped"] == "credits" and out["failed"]["fetched"] == 1
    assert out["outcomes"] == {"adjacent": 0, "block": 0, "later": 0, "failed": 1, "none": 0, "unread": 1}
    assert out["tried"] == 1 and out["per_leader"][LEAD]["unread"] == 1 and out["per_leader"][LEAD]["tried"] == 1
    assert "1 unread" in out["verdict"], out["verdict"]


def test_the_verdict_gives_the_moments_with_only_failed_sends_their_own_fees():
    """Audit 22 Nov (tools#4): "N with only failed sends (X SOL in their fees)" summed the failed tries of every tried
    moment, the landed ones too: the first run's 2.0497 SOL was the fee of all 764 failed sends at its 62 tried moments,
    not of the 15 with failed sends only. M1: the bot landed right behind it after a failed try that paid 0.3 SOL; M2:
    one failed try that paid 0.001 SOL."""
    store = Store(":memory:")
    store.add_trades([_t(LEAD, "M1", 1100, 0, 2.5), _t(BOT, "M1", 1100, 1, 6.0), _t(LEAD, "M2", 1200, 0, 3.0),
                      _t(OTHER, "Else", 2000, 0, 0.1)])  # fmt: skip
    entries = [{"signature": "f2", "slot": 1201, "err": ERR, "blockTime": int(T0 + 201 * 0.4)},
               {"signature": "f1", "slot": 1101, "err": ERR, "blockTime": int(T0 + 101 * 0.4)}]  # fmt: skip
    txs = {"f1": _failed("f1", 1101, "M1", 300_000_000), "f2": _failed("f2", 1201, "M2", 1_000_000)}
    out = _run(store, Chain(entries, txs))
    assert out["outcomes"]["adjacent"] == 1 and out["outcomes"]["failed"] == 1
    assert "1 with only failed sends (0.0010 SOL in their fees)" in out["verdict"], out["verdict"]
    assert "the 2 failed sends at the 2 tried buys paid 0.3010 SOL" in out["verdict"], out["verdict"]
    f = out["failed"]
    assert f["failed_only_fee_sol"] == pytest.approx(0.001) and f["placed_fee_sol"] == pytest.approx(0.301)
    # fee_sol and priority_sol are over the fetched sends, which sit beside all the failed ones: the output says so
    assert "fee_sol and priority_sol" in f["note"] and "fetched" in f["note"]


def test_a_moment_the_listing_never_reached_reads_unread():
    """Audit 22 Nov (tools#0): a limit that stops the listing itself leaves every moment it did not reach unread; a
    landed buy is on the tape and needs no listing."""
    store, chain = _world()
    out = _run(store, chain, max_credits=0)
    assert out["stopped"] == "credits" and out["signatures"] == 0 and out["credits"] == 0
    assert out["outcomes"] == {"adjacent": 1, "block": 0, "later": 0, "failed": 0, "none": 0, "unread": 2}


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
