"""`route`: who stands right behind a wallet's first buys, and through which route (the route check, 22 Nov 07:50).

22 Nov: a buy of 0.1 SOL or more stood within 3 block places after a big wallet's first buy on 65 % of the moments and
before it on 1 in 386, so whoever stands there had the leader's transaction before it ran. Jito's public explorer says,
for a landed transaction, the bundle it came in, and for a bundle, its transactions, who paid its tip, the tip and its
place in the block. 23 Nov 16:05, by hand on six moments behind Cented: the 3.457 SOL operator rode inside Cented's own
bundle and paid its tip; four fast bots stood at +1 to +3 with neither buy in a Jito bundle."""

import asyncio
import json

import httpx
import pytest

from snipetech.analysis import route as R
from snipetech.models import Trade
from snipetech.store import Store

LEAD = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
BACK = "5EMECMKDCe75XTmymE1juecfwzRFjhhfiQXiBkE3Ygxn"  # rides in the leader's bundle and pays its tip
FAST = "Fhbh1DTUDKt6qu9zVg8Q5VgnZa1WWWbxBxG6pCoJoggA"  # right behind, outside Jito
OWNB = "9AGtz37mtegPZ4swc1Y7GQi63EQ2zp6LLigUkqukrUsT"  # right behind, in a bundle of its own
MATE = "C7YoWGFuj7ppdCQaaQQN7fiHZWVSrotEX31v9oe3gXgr"  # the leader's companion
NOW = 1_794_045_600
T0 = NOW - 7200


def _t(user, mint, slot, pos, sol, buy=True):
    return Trade(sig=f"{user[:4]}-{mint}-{slot}-{pos}", ev=0, slot=slot, pos=pos, ts=int(T0 + (slot - 1000) * 0.4),
                 mint=mint, venue="curve", user=user, is_buy=buy, quote=round(sol * 1e9), base=1_000_000, fee=0,
                 fee_bps=100, q_after=1, b_after=1)  # fmt: skip


def sig(user, mint, slot, pos):
    return f"{user[:4]}-{mint}-{slot}-{pos}"


class Explorer:
    """The explorer's answers: a transaction's bundle (None: in none), a bundle's record; each request counted."""

    def __init__(self, of=None, bundles=None, fail=()):
        self.of, self.bundles, self.fail = dict(of or {}), dict(bundles or {}), set(fail)
        self.calls: list[tuple[str, str]] = []
        self.requests = 0

    async def bundle_of(self, s):
        self.calls.append(("of", s))
        self.requests += 1
        if s in self.fail:
            raise R.Unread("HTTP 500")
        return self.of.get(s)

    async def bundle(self, bid):
        self.calls.append(("bundle", bid))
        self.requests += 1
        if bid in self.fail:
            raise R.Unread("HTTP 500")
        return self.bundles[bid]


def _bundle(bid, txs, tippers, tip_sol, index):
    return {"id": bid, "txs": list(txs), "tippers": list(tippers), "tip_sol": tip_sol, "block_index": index,
            "validator": "V1", "slot": 0}  # fmt: skip


def _world():
    """LEAD's first buys: M1 (BACK at +2 in the leader's own bundle, tipped by BACK), M2 (FAST at +1, no bundle), M3
    (OWNB at +1 in a bundle of its own), M4 (nobody within 3 places: the next buyer at +5), M5 (only the leader's
    companion at +1), M6 (a dust buy at +1 and FAST at +3, whose lookup fails). M7 is under 1.9 SOL: no moment."""
    store = Store(":memory:")
    store.add_trades([
        _t(LEAD, "M1", 1100, 64, 2.9), _t(BACK, "M1", 1100, 66, 3.457),
        _t(LEAD, "M2", 1200, 10, 4.9), _t(FAST, "M2", 1200, 11, 1.97),
        _t(LEAD, "M3", 1300, 20, 2.9), _t(OWNB, "M3", 1300, 21, 4.9),
        _t(LEAD, "M4", 1400, 30, 2.9), _t(FAST, "M4", 1400, 35, 1.0),
        _t(LEAD, "M5", 1500, 40, 2.9), _t(MATE, "M5", 1500, 41, 2.0),
        _t(LEAD, "M6", 1600, 50, 2.9), _t(FAST, "M6", 1600, 51, 0.01), _t(FAST, "M6", 1600, 53, 2.0),
        _t(LEAD, "M7", 1700, 60, 1.0), _t(FAST, "M7", 1700, 61, 2.0),
    ])  # fmt: skip
    lead1 = sig(LEAD, "M1", 1100, 64)
    back1 = sig(BACK, "M1", 1100, 66)
    own3 = sig(OWNB, "M3", 1300, 21)
    ex = Explorer(
        of={lead1: "B1", own3: "B3"},
        bundles={"B1": _bundle("B1", [lead1, "tip-tx", back1], [BACK], 0.0303, 64),
                 "B3": _bundle("B3", [own3], [OWNB], 0.01, 21)},
        fail={sig(FAST, "M6", 1600, 53)},
    )  # fmt: skip
    return store, ex


def _run(store, ex, **kw):
    kw.setdefault("mates", {LEAD: frozenset({MATE})})
    return asyncio.run(R.route(store, ex, [LEAD], NOW, hours=3, **kw))


def _row(out, mint):
    return next(r for r in out["moments_list"] if r["mint"] == mint)


def test_buyer_inside_the_leaders_own_bundle_paid_its_tip():
    store, ex = _world()
    out = _run(store, ex)
    row = _row(out, "M1")
    assert row["leader_route"] == "in a bundle"
    assert row["leader_bundle"]["tippers"] == [BACK] and row["leader_bundle"]["n"] == 3
    near = row["near"][0]
    assert (near["wallet"], near["place"], near["kind"], near["tipped_by"]) == (BACK, 2, "with the leader", "the buyer")
    # a buyer the leader's bundle lists is not looked up again
    assert ("of", sig(BACK, "M1", 1100, 66)) not in ex.calls


def test_buyer_outside_jito_and_buyer_in_its_own_bundle():
    store, ex = _world()
    out = _run(store, ex)
    m2, m3 = _row(out, "M2"), _row(out, "M3")
    assert m2["leader_route"] == "no bundle" and m2["near"][0]["kind"] == "no bundle"
    own = m3["near"][0]
    assert own["kind"] == "own bundle" and own["bundle"]["tippers"] == [OWNB] and own["bundle"]["tip_sol"] == 0.01


def test_moment_with_nobody_near_and_companions_ask_nothing():
    store, ex = _world()
    out = _run(store, ex)
    assert _row(out, "M4")["closest"] == "nobody" and _row(out, "M4")["near"] == []
    m5 = _row(out, "M5")
    assert m5["closest"] == "nobody" and m5["companions"] == [{"wallet": MATE, "place": 1, "sol": 2.0}]
    asked = {s for _, s in ex.calls}
    assert not any(s.startswith((LEAD[:4] + "-M4", LEAD[:4] + "-M5", MATE[:4])) for s in asked)


def test_dust_is_no_buyer_and_a_failed_lookup_is_unread():
    store, ex = _world()
    out = _run(store, ex)
    m6 = _row(out, "M6")
    assert [n["place"] for n in m6["near"]] == [3]  # the 0.01 SOL buy at +1 is no buyer
    assert m6["near"][0]["kind"] == "unread" and m6["closest"] == "unread"
    assert "M7" not in {r["mint"] for r in out["moments_list"]}  # a 1 SOL first buy is no moment


def test_counts_and_verdict():
    store, ex = _world()
    out = _run(store, ex)
    assert out["moments"] == 6 and out["with_buyer"] == 4
    assert out["closest"] == {"with the leader": 1, "own bundle": 1, "no bundle": 1, "unread": 1}
    assert out["leader_route"] == {"in a bundle": 1, "no bundle": 3, "unread": 0}
    assert out["with_leader_tipped_by"] == {"the buyer": 1}
    assert out["backrunners"] == [{"wallet": BACK, "n": 1, "median_tip_sol": pytest.approx(0.0303)}]
    assert out["own_bundle"] == [{"wallet": OWNB, "n": 1, "median_tip_sol": pytest.approx(0.01)}]
    assert out["outside"] == [{"wallet": FAST, "n": 1}]
    v = out["verdict"]
    assert "6 first buys" in v and "4 had another wallet's buy" in v and "inside the leader's own Jito bundle on 1" in v
    assert "its tip paid by the buyer on 1" in v
    assert "1 unread" in v
    json.dumps(out)  # the job's output is JSON


def test_newest_moments_first_under_the_limit_and_the_rest_said():
    store, ex = _world()
    out = _run(store, ex, max_moments=2)
    read = [r["mint"] for r in out["moments_list"] if r["closest"] not in ("nobody", "left")]
    assert read == ["M3", "M6"]  # the two newest moments with a buyer near, in block order
    assert out["left"] == 2 and "2 moments with a buyer near were left unread" in out["verdict"]
    assert ("of", sig(LEAD, "M1", 1100, 64)) not in ex.calls


def test_leaders_lookup_failing_leaves_only_the_leader_unread():
    store, ex = _world()
    ex.fail.add(sig(LEAD, "M2", 1200, 10))
    ex.fail.add(sig(LEAD, "M1", 1100, 64))
    ex.of[sig(BACK, "M1", 1100, 66)] = "B1"
    out = _run(store, ex)
    m2 = _row(out, "M2")
    assert m2["leader_route"] == "unread" and m2["near"][0]["kind"] == "no bundle"
    # the buyer's own bundle carries the leader's buy: the leader's route is read from it
    m1 = _row(out, "M1")
    assert m1["leader_route"] == "in a bundle" and m1["near"][0]["kind"] == "with the leader"
    assert m1["near"][0]["tipped_by"] == "the buyer"


def test_who_paid_the_leaders_bundle():
    assert R.tipped_by([LEAD], LEAD, BACK, frozenset()) == "the leader"
    assert R.tipped_by([BACK], LEAD, BACK, frozenset()) == "the buyer"
    assert R.tipped_by([MATE], LEAD, BACK, frozenset({MATE})) == "a companion of the leader"
    assert R.tipped_by([OWNB], LEAD, BACK, frozenset()) == "another wallet"
    assert R.tipped_by([], LEAD, BACK, frozenset()) == "nobody named"


# --- the explorer client -----------------------------------------------------------------------------------------


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://explorer.test")


def test_explorer_reads_a_transactions_bundle_and_none_on_404():
    def handler(req):
        if req.url.path.endswith("/transaction/inbundle"):
            return httpx.Response(200, json=[{"bundle_id": "abc"}])
        if req.url.path.endswith("/bundle/abc"):
            return httpx.Response(200, json=[{"bundleId": "abc", "slot": 7, "validator": "V", "tippers": ["T"],
                                              "landedTipLamports": 30_300_000, "blockIndex": 64,
                                              "txSignatures": ["x", "y"]}])  # fmt: skip
        return httpx.Response(404, json={"error": "Bundle not found"})

    async def go():
        async with _client(handler) as http:
            ex = R.Explorer(http, base="https://explorer.test/api/v1/bundles", gap=0)
            assert await ex.bundle_of("inbundle") == "abc"
            assert await ex.bundle_of("loose") is None
            b = await ex.bundle("abc")
            assert b == {"id": "abc", "txs": ["x", "y"], "tippers": ["T"], "tip_sol": 0.0303, "block_index": 64,
                         "validator": "V", "slot": 7}  # fmt: skip
            assert ex.requests == 3

    asyncio.run(go())


def test_explorer_waits_out_one_refusal_then_says_unread():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if req.url.path.endswith("/transaction/busy"):
            return httpx.Response(429) if calls["n"] == 1 else httpx.Response(200, json=[{"bundle_id": "b"}])
        if req.url.path.endswith("/bundle/gone"):
            return httpx.Response(404, json={"error": "Bundle not found"})
        return httpx.Response(500)

    async def go():
        async with _client(handler) as http:
            ex = R.Explorer(http, base="https://explorer.test/api/v1/bundles", gap=0, retry=0)
            assert await ex.bundle_of("busy") == "b"  # a 429 is waited out once
            with pytest.raises(R.Unread, match="500"):
                await ex.bundle_of("broken")
            with pytest.raises(R.Unread, match="no such bundle"):
                await ex.bundle("gone")

    asyncio.run(go())


def test_explorer_stops_at_its_request_limit_without_asking():
    def handler(req):
        return httpx.Response(404)

    async def go():
        async with _client(handler) as http:
            ex = R.Explorer(http, base="https://explorer.test/api/v1/bundles", gap=0, max_requests=1)
            assert await ex.bundle_of("a") is None
            with pytest.raises(R.Unread, match="request limit"):
                await ex.bundle_of("b")
            assert ex.requests == 1

    asyncio.run(go())


def test_route_is_a_research_command_on_the_server():
    from snipetech import cli
    from snipetech.ops import ALLOWED, HOLDS_DEPLOY

    assert "route" in ALLOWED and "route" in HOLDS_DEPLOY and "route" in cli.BATCH_COMMANDS
    args = cli.build_parser().parse_args(["route", "--leader", LEAD, "--hours", "24", "--json"])
    assert (args.cmd, args.leader, args.hours, args.json) == ("route", [LEAD], 24.0, True)


def test_route_with_no_leader_says_what_to_give(tmp_path, capsys):
    from snipetech import cli

    with pytest.raises(SystemExit, match="route: give the wallets"):
        cli.main(["--db", str(tmp_path / "snipetech.db"), "route", "--json"])
