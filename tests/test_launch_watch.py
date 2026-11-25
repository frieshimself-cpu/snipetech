"""`snipetech gmgn launch-watch` and `launch-score` (analysis/launch_watch.py): a day of real launches on Base,
Ethereum, BNB Chain or Solana from GMGN's newest coins, each recorded once, its 1 s candles and security read once it
has settled, then what an early buyer would have made at each entry delay and hold, rugs, honeypots and taxes counted.

The watch runs on a fake clock (sleeping moves it; nothing waits) against the stand-in of tests/gmgn_fake.py. The coins
are made up; their rows have the fields of a real trenches row (tests/fixtures/gmgn/real/trenches.json) and their
security the fields of a real answer (tests/fixtures/gmgn/real/token_security.json)."""

import itertools
import json

import httpx
import pytest

from snipetech import cli, gmgn
from snipetech.analysis import launch_watch as LW
from snipetech.gmgn import Gmgn
from tests.gmgn_fake import KEY, PRO, fake_gmgn, fixture, refused

T = 1_794_600_000  # the fake clock's start, unix seconds
S = 1_794_500_000  # the start of a coin in a hand-made scored file
A, B, C, D, E, F, G = ("0x" + two * 20 for two in ("a1", "b2", "c3", "d4", "e5", "f6", "97"))  # coins on Base
MANY = tuple("0x" + f"{k:02x}" * 20 for k in range(16, 40))  # more coins on Base, for a score of many
CREATOR = "0x" + "77" * 20
WETH = "0x4200000000000000000000000000000000000006"
REAL_SECURITY = fixture("real/token_security.json")["data"]
NO_TAX = {"is_honeypot": None, "honeypot": 0, "can_sell": 0, "can_not_sell": 0, "buy_tax": "0", "sell_tax": "0"}


def mints(n, first=0):
    """`n` more coins on Base, numbered from `first` + 1: as many as a score's floor of coins wants."""
    return ["0x" + f"{k:040x}" for k in range(first + 1, first + n + 1)]


def halves(n, tag=1):
    """`n` coins on Base in each half the score splits its coins in (LW.half_of): the first list in half 0, which the
    best pair and the launchpads' order are chosen on, the second in half 1, whose numbers they print. Coins of two
    tags never meet."""
    split: tuple[list, list] = ([], [])
    for k in itertools.count(1):
        m = f"0x{tag:08x}{k:032x}"
        if len(split[LW.half_of(m)]) < n:
            split[LW.half_of(m)].append(m)
        if len(split[0]) == len(split[1]) == n:
            return split


def both_halves(n, tag=1):
    """`n` coins of each half (halves), in one list."""
    first, second = halves(n, tag)
    return [*first, *second]


class FakeTime:
    """The watch's clock and sleep: sleeping moves the clock, nothing waits."""

    def __init__(self, now: float):
        self.now = float(now)

    def time(self) -> float:
        return self.now

    def sleep(self, secs: float) -> None:
        self.now += max(0.0, secs)


# -- the stand-in's answers --------------------------------------------------------------------------------------------


def answer(data):
    return httpx.Response(200, json={"code": 0, "message": "ok", "data": data}, headers=PRO)


def raw_answer(text):
    """GMGN's answer as text, for a number JSON reads as no finite float (1e999 is infinity)."""
    return httpx.Response(200, content=text.encode(), headers={**PRO, "content-type": "application/json"})


def trench(mint, created, *, opened=0, platform="clanker", symbol="TKN"):
    """One of GMGN's newest coins as its trenches answer gives it (the fields of a real row; the values made up)."""
    return {
        "address": mint,
        "symbol": symbol,
        "name": f"{symbol} coin",
        "chain": "base",
        "creator": CREATOR,
        "launchpad": platform,
        "launchpad_platform": platform,
        "exchange": "uniswap_v4",
        "created_timestamp": created,
        "open_timestamp": opened,
        "market_cap": 5200.5,
        "liquidity": 3100.0,
        "volume_24h": 120.0,
        "swaps_24h": 4,
        "price": 5.2e-06,
        "holder_count": 3,
        "top_10_holder_rate": 0.9,
        "rug_ratio": 0,
        "progress": 0,
        "buy_tax": 0,
        "sell_tax": 0,
        "quote_address": WETH,
    }


def polls(*batches):
    """GMGN's trenches answering each poll with the next batch of rows, and the last batch again once they run out."""
    left = list(batches)

    def route(req):
        rows = left.pop(0) if len(left) > 1 else left[0]
        return answer({"new_creation": rows, "near_completion": [], "completed": []})

    return route


def gmgn_candle(ts, o, h, lo, c, usd):
    """A one-second candle as GMGN sends it: milliseconds, prices as text, `volume` the dollars."""
    return {"time": ts * 1000, "open": str(o), "high": str(h), "low": str(lo), "close": str(c), "volume": str(usd),
            "amount": "1000"}  # fmt: skip


def kline(by_mint):
    """GMGN's candles of each coin asked, from (ts, open, high, low, close, dollars); none for any other coin."""

    def route(req):
        return answer({"list": [gmgn_candle(*row) for row in by_mint.get(req.url.params["address"], [])]})

    return route


def security(by_mint):
    """GMGN's security answer of each coin asked (an answer, or a ready response); a real one for any other coin."""

    def route(req):
        got = by_mint.get(req.url.params["address"], REAL_SECURITY)
        return got if isinstance(got, httpx.Response) else answer(got)

    return route


HEALTHY_POOL = {"address": "0x" + "88" * 20, "exchange": "uniswap_v4", "liquidity": "25000.5", "base_reserve": "4.1e9",
                "quote_reserve": "5.2"}  # fmt: skip


def pools(by_mint):
    """GMGN's pool of each coin asked (an answer, or a ready response); one that still holds money for any other."""

    def route(req):
        got = by_mint.get(req.url.params["address"], HEALTHY_POOL)
        return got if isinstance(got, httpx.Response) else answer(got)

    return route


def client(ft, seen, routes, chain="base"):
    return Gmgn(KEY, transport=fake_gmgn(seen, routes), clock=ft.time, sleep=ft.sleep, rate=50, chain=chain)


def watch(tmp_path, ft, routes, seen=None, chain="base", **kw):
    """One launch-watch job on the fake clock (every pool GMGN is asked for still holds money, unless `routes` say
    otherwise); gives its summary."""
    kw.setdefault("every", 60)
    kw.setdefault("settle_secs", 900)
    got = [] if seen is None else seen
    routes = {"/v1/token/pool_info": pools({}), **routes}
    return LW.watch(client(ft, got, routes, chain), tmp_path, clock=ft.time, sleep=ft.sleep, **kw)


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def launches(tmp_path, chain="base"):
    return _lines(tmp_path / "history" / "gmgn_launches" / f"{chain}.jsonl")


def scored(tmp_path, chain="base"):
    return _lines(tmp_path / "history" / "gmgn_launches" / f"{chain}_scored.jsonl")


def asked(seen, path):
    return [r for r in seen if r.url.path == path]


# -- the watch: every coin once, its candles and security once ----------------------------------------------------------


def test_a_launch_is_recorded_once_across_polls_and_across_a_restart(tmp_path):
    ft, seen = FakeTime(T), []
    b_again = "0x" + B[2:].upper()  # the same coin, its address in capitals
    routes = {
        "/v1/trenches": polls(
            [trench(A, T), trench(B, T)], [trench(b_again, T), trench(C, T)], [trench(D, T), trench(E, T)]
        )
    }
    out = watch(tmp_path, ft, routes, seen, minutes=3)
    rows = launches(tmp_path)
    assert [(r["mint"], r["first_seen"], r["chain"]) for r in rows] == [
        (A, T, "base"),
        (B, T, "base"),
        (C, T + 60, "base"),
        (D, T + 120, "base"),
        (E, T + 120, "base"),
    ]
    assert (rows[0]["symbol"], rows[0]["platform"], rows[0]["created_ts"]) == ("TKN", "clanker", T)  # GMGN's coin row
    assert out["ok"] is True and (out["polls"], out["recorded"], out["waiting"], out["scored"]) == (3, 5, 5, 0)
    assert out["polls_all_new"] == 1  # the third poll's rows were all new: coins may have come and gone between polls
    assert len(asked(seen, "/v1/trenches")) == len(seen) == 3  # nothing settled within 3 minutes: nothing else asked
    body = json.loads(seen[0].content)
    assert seen[0].url.params["chain"] == "base" and set(body) == {"version", "new_creation"}
    assert body["new_creation"]["limit"] == 80 and "launchpad_platform" not in body["new_creation"]  # every launchpad
    # a restarted job reads what the last one wrote and goes on from there
    ft2 = FakeTime(T + 600)
    out2 = watch(tmp_path, ft2, {"/v1/trenches": polls([trench(A, T), trench(D, T), trench(F, T + 590)])}, minutes=1)
    assert [r["mint"] for r in launches(tmp_path)] == [A, B, C, D, E, F]
    assert out2["resumed"] == {"seen": 5, "waiting": 5} and out2["recorded"] == 1 and out2["waiting"] == 6


def test_the_taxes_and_the_honeypot_flag_on_a_launchs_first_row_are_kept(tmp_path):
    """Review of 25 Nov: the taxes and the honeypot flag on GMGN's row of a new coin were dropped when the row was
    recorded, and only the security read 900 s later was charged."""
    taxed = dict(trench(A, T - 1000), buy_tax=0.05, sell_tax="0.1", is_honeypot=1)
    routes = {
        "/v1/trenches": polls([taxed, trench(B, T - 1000)]),
        "/v1/market/token_kline": kline({}),
        "/v1/token/security": security({}),
    }
    watch(tmp_path, FakeTime(T), routes, minutes=1)
    a, b = launches(tmp_path)
    assert (a["buy_tax"], a["sell_tax"], a["is_honeypot"]) == (0.05, "0.1", 1)  # as GMGN gave them
    assert (b["buy_tax"], b["sell_tax"]) == (0, 0) and "is_honeypot" not in b  # a real row gives no honeypot flag
    by = {s["mint"]: s for s in scored(tmp_path)}
    assert (by[A]["buy_tax"], by[A]["sell_tax"], by[A]["is_honeypot"]) == (0.05, "0.1", 1)  # and in the coin's line


def test_real_rows_of_gmgns_newest_coins_are_recorded_with_their_taxes(tmp_path):
    real = fixture("real/trenches.json")["data"]
    seen: list = []
    watch(tmp_path, FakeTime(1_794_506_800), {"/v1/trenches": lambda req: answer(real)}, seen, chain="sol", minutes=1)
    rows = launches(tmp_path, "sol")
    assert [r["mint"] for r in rows] == [r["address"] for r in real["new_creation"]]
    assert [(r["symbol"], r["platform"], r["created_ts"], r["open_ts"]) for r in rows] == [
        ("Ryfa", "Pump.fun", 1_794_506_736, 0),
        ("SU", "Pump.fun", 1_794_506_732, 0),
    ]
    assert all((r["buy_tax"], r["sell_tax"], "is_honeypot" in r) == (0, 0, False) for r in rows)
    assert len(seen) == 1 and seen[0].url.params["chain"] == "sol"


def test_a_settled_launch_gets_its_candles_and_security_once(tmp_path):
    ft, seen = FakeTime(T), []
    sec_a = dict(REAL_SECURITY, address=A, buy_tax="0.03", sell_tax="0.05")
    routes = {
        "/v1/trenches": polls([trench(A, T - 1000), trench(B, T, symbol="BEE")]),
        "/v1/market/token_kline": kline(
            {A: [(T - 1000, 1.0, 1.2, 0.9, 1.1, 50.0), (T - 999, 1.1, 1.5, 1.1, 1.4, 20.5)]}
        ),
        "/v1/token/security": security({A: sec_a}),
    }
    out = watch(tmp_path, ft, routes, seen, minutes=16)
    lines = scored(tmp_path)
    assert [s["mint"] for s in lines] == [A, B]
    a, b = lines
    assert (a["chain"], a["start"], a["platform"], a["symbol"], a["first_seen"], a["fetched_at"]) == (
        "base",
        T - 1000,
        "clanker",
        "TKN",
        T,
        T,
    )
    # each candle with the coins it moved too (fourth review of 25 Nov: a close may be its last trade's price, its
    # dollars over its coins on a one-trade candle, which the first real run can check)
    assert a["candles"] == [[T - 1000, 1.0, 1.2, 0.9, 1.1, 50.0, 1000.0], [T - 999, 1.1, 1.5, 1.1, 1.4, 20.5, 1000.0]]
    assert a["security"] == sec_a  # as GMGN sent it
    # B started at T: it is read once it is 900 s old; GMGN had no candle of it, which is no refusal
    assert (b["start"], b["fetched_at"], b["candles"], b["symbol"]) == (T, T + 900, [], "BEE")
    assert "candles_error" not in b and b["security"] == REAL_SECURITY
    candles = [r.url.params for r in asked(seen, "/v1/market/token_kline")]
    assert [(q["address"], q["resolution"], int(q["from"]), int(q["to"])) for q in candles] == [
        (A, "1s", (T - 1000) * 1000, (T - 500) * 1000),
        (B, "1s", T * 1000, (T + 500) * 1000),
    ]
    assert [r.url.params["address"] for r in asked(seen, "/v1/token/security")] == [A, B]
    assert (out["scored"], out["waiting"]) == (2, 0)
    # a restarted job knows both coins are done and asks nothing again
    seen2: list = []
    out2 = watch(tmp_path, FakeTime(T + 2000), routes, seen2, minutes=0)
    assert seen2 == [] and out2["resumed"] == {"seen": 2, "waiting": 0} and len(scored(tmp_path)) == 2


def test_the_pool_of_every_coin_a_copy_bought_is_read_once_when_it_settles(tmp_path):
    """Reviews of 25 Nov: a sale after a coin's last trade is priced at that trade, and a pool its creator emptied
    leaves no trade and no candle. Only a later sale of the copy's worth shows the pool was still there (fourth review),
    and the watch cannot judge that without the copy's size: it reads the pool of every coin a copy bought (one that
    traded before the last entry, 30 s), once, when it settles, as it reads the others; a coin no copy bought (its
    first trade after 30 s) has none read. Every line keeps the coin's last candle time."""
    s = T - 1000
    bars = {
        A: [(s, 1.0, 1.0, 1.0, 1.0, 5.0), (s + 10, 3.0, 3.0, 3.0, 3.0, 5.0)],  # its last trade 10 s in
        B: [(s + k, 1.0, 1.0, 1.0, 1.0, 5.0) for k in (*range(0, 500, 40), 499)],  # a trade every 40 s, to the end
        C: [(s, 1.0, 1.0, 1.0, 1.0, 5.0)],  # its only trade at its start, and GMGN has no pool of it
        D: [(s + k, 1.0, 1.0, 1.0, 1.0, 5.0) for k in (40, 100, 499)],  # its first trade 40 s in: no copy bought it
    }
    gone = dict(HEALTHY_POOL, liquidity="0.02", base_reserve="0", quote_reserve="0")
    routes = {
        "/v1/trenches": polls([trench(m, s) for m in (A, B, C, D)]),
        "/v1/market/token_kline": kline(bars),
        "/v1/token/security": security({}),
        "/v1/token/pool_info": pools({A: gone, C: refused(404, "NOT_FOUND", "no pool")}),
    }
    seen: list = []
    out = watch(tmp_path, FakeTime(T), routes, seen, minutes=3)
    # once each; C's, refused with a 404, asked again in two later rounds as every read is (third review of 25 Nov)
    assert [r.url.params["address"] for r in asked(seen, "/v1/token/pool_info")] == [A, B, C, C, C]
    by = {line["mint"]: line for line in scored(tmp_path)}
    assert (by[A]["last_candle_ts"], by[A]["pool"]) == (s + 10, gone)
    assert (by[B]["last_candle_ts"], by[B]["pool"], "pool" in by[D]) == (s + 499, HEALTHY_POOL, False)
    assert by[C]["last_candle_ts"] == s and "HTTP 404" in by[C]["pool"]["error"] and by[C]["fetched_at"] == T + 120
    assert (out["pools_read"], out["refused"]["pool"], out["scored"]) == (3, 1, 4)


def test_a_pool_read_that_fails_on_the_way_is_asked_again_a_round_later(tmp_path):
    """Third review of 25 Nov: a pool read that failed on the way (a 503 the client's three tries all got) was written
    at once as the coin's pool, and the score lost every stale sale of the coin where its pool held money."""
    stops = [(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0), (T - 990, 3.0, 3.0, 3.0, 3.0, 5.0)]  # its last trade 10 s in
    asks: list = []

    def pool(req):
        asks.append(req)
        return httpx.Response(503, text="upstream unavailable") if len(asks) <= 3 else answer(HEALTHY_POOL)

    routes = {
        "/v1/trenches": polls([trench(A, T - 1000)]),
        "/v1/market/token_kline": kline({A: stops}),
        "/v1/token/security": security({}),
        "/v1/token/pool_info": pool,
    }
    out = watch(tmp_path, FakeTime(T), routes, minutes=3)
    (a,) = scored(tmp_path)
    assert (a["pool"], a["fetched_at"], out["refused"]["pool"]) == (HEALTHY_POOL, T + 60, 0)


@pytest.mark.parametrize("read", ["/v1/market/token_kline", "/v1/token/pool_info"])
def test_a_read_rate_limited_past_the_jobs_end_leaves_its_coin_waiting(tmp_path, read):
    """A rate limit on the candles or the pool read that holds past the job's end writes nothing: the coin waits for
    the next job (the third review of 25 Nov found both returns pinned by no test)."""
    stops = [(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0), (T - 990, 3.0, 3.0, 3.0, 3.0, 5.0)]  # its last trade 10 s in
    limit = httpx.Response(429, json={"code": 429, "error": "RATE_LIMIT_BANNED", "message": "x", "reset_at": T + 5000})
    routes = {
        "/v1/trenches": polls([trench(A, T - 1000)]),
        "/v1/market/token_kline": kline({A: stops}),
        "/v1/token/security": security({}),
        read: lambda req: limit,
    }
    out = watch(tmp_path, FakeTime(T), routes, minutes=2)
    assert (scored(tmp_path), out["waiting"], out["refused"]) == ([], 1, dict.fromkeys(out["refused"], 0))
    assert out["rate_limit_left"] >= 1


def test_the_window_starts_at_the_open_else_the_creation_else_the_first_sighting(tmp_path):
    rows = [trench(A, T - 5000, opened=T - 100), trench(B, T - 200), trench(C, 0)]
    routes = {"/v1/trenches": polls(rows), "/v1/market/token_kline": kline({}), "/v1/token/security": security({})}
    seen: list = []
    watch(tmp_path, FakeTime(T), routes, seen, minutes=1)
    assert not asked(seen, "/v1/market/token_kline")  # none is 900 s past its start yet
    # with no minutes a job only reads what has settled since: it looks for no new coin
    seen2: list = []
    out2 = watch(tmp_path, FakeTime(T + 850), routes, seen2, minutes=0)
    got = [(q["address"], int(q["from"])) for q in (r.url.params for r in asked(seen2, "/v1/market/token_kline"))]
    assert got == [(A, (T - 100) * 1000), (B, (T - 200) * 1000)]
    assert not asked(seen2, "/v1/trenches") and (out2["scored"], out2["waiting"]) == (2, 1)
    seen3: list = []
    watch(tmp_path, FakeTime(T + 950), routes, seen3, minutes=0)
    assert [r.url.params["address"] for r in asked(seen3, "/v1/market/token_kline")] == [C]
    assert [(s["mint"], s["start"]) for s in scored(tmp_path)] == [(A, T - 100), (B, T - 200), (C, T)]
    # third review of 25 Nov: and each line says where its start came from
    assert [s["start_from"] for s in scored(tmp_path)] == ["open", "created", "sighting"]


def test_a_refused_read_is_written_into_the_coins_line_and_the_job_goes_on(tmp_path):
    calls: list = []

    def trenches(req):
        calls.append(req)
        if len(calls) == 1:
            return refused(400, "BAD_REQUEST", "invalid parameter")
        return answer({"new_creation": [trench(A, T - 1000), trench(B, T - 1000), trench(C, T - 1000)]})

    def candles(req):
        if req.url.params["address"] == B:
            return refused(400, "BAD_REQUEST", "invalid address")
        return answer({"list": [gmgn_candle(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0)]})

    routes = {
        "/v1/trenches": trenches,
        "/v1/market/token_kline": candles,
        "/v1/token/security": security({A: refused(422, "UNPROCESSABLE", "unsupported token")}),
    }
    out = watch(tmp_path, FakeTime(T), routes, minutes=2)
    by = {s["mint"]: s for s in scored(tmp_path)}
    assert set(by) == {A, B, C}
    assert "HTTP 422" in by[A]["security"]["error"] and by[A]["candles"]  # the coin's other read is kept
    assert by[B]["candles"] == [] and "BAD_REQUEST" in by[B]["candles_error"]
    assert by[B]["security"] == REAL_SECURITY
    assert "candles_error" not in by[C] and by[C]["security"] == REAL_SECURITY
    assert out["ok"] is True and out["polls"] == 2
    assert out["refused"] == {"poll": 1, "candles": 1, "security": 1, "pool": 0}


def test_no_read_starts_once_its_time_is_over(tmp_path):
    """Third review of 25 Nov: the time was checked once a coin, so with --minutes 0 and reads answering in 25 s a
    coin's security read started 75 s after the end, past the minute's slack. A read whose time is over is not
    started: its coin waits for the next job."""
    folder = tmp_path / "history" / "gmgn_launches"
    folder.mkdir(parents=True)
    rows = [{"mint": m, "platform": "clanker", "created_ts": T - 1000, "first_seen": T - 990, "chain": "base"}
            for m in (A, B, C)]  # fmt: skip
    (folder / "base.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    ft, starts = FakeTime(T), []

    def slow(kind):
        def route(req):
            starts.append((kind, req.url.params["address"], ft.now - T))
            ft.now += 25
            return answer({"list": []} if kind == "candles" else REAL_SECURITY)

        return route

    routes = {"/v1/market/token_kline": slow("candles"), "/v1/token/security": slow("security")}
    out = watch(tmp_path, ft, routes, minutes=0)
    assert starts == [("candles", A, 0), ("security", A, 25), ("candles", B, 50)]  # nothing starts at 75 s
    assert (out["scored"], out["waiting"]) == (1, 2)


def test_a_404_or_a_403_is_asked_again_in_two_later_rounds_before_it_is_written_as_refused(tmp_path):
    """Review of 25 Nov: GMGN's 404 for a coin it has not read yet, and a 403 of its own, were written as refused at
    once, and such a coin was then scored as safe. They are asked again two rounds later, as a server error is. (A 403
    that refuses the key or its plan is the key's: test_a_plan_that_lost_its_one_second_candles_...)"""
    ft = FakeTime(T)
    asks: dict = {"candles": [], "security": []}

    def candles(req):
        asks["candles"].append((req.url.params["address"], ft.now))
        if req.url.params["address"] == B:
            return refused(403, "FORBIDDEN", "this coin's candles are not open")
        return answer({"list": [gmgn_candle(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0)]})

    def sec(req):
        mint = req.url.params["address"]
        asks["security"].append((mint, ft.now))
        if mint == A or (mint == C and len([m for m, _ in asks["security"] if m == C]) == 1):  # C's comes the 2nd time
            return refused(404, "NOT_FOUND", "no such token")
        return answer(REAL_SECURITY)

    routes = {
        "/v1/trenches": polls([trench(m, T - 1000) for m in (A, B, C)]),
        "/v1/market/token_kline": candles,
        "/v1/token/security": sec,
    }
    out = watch(tmp_path, ft, routes, minutes=3)
    by = {s["mint"]: s for s in scored(tmp_path)}
    rounds = [T, T + 60, T + 120]
    assert [t for m, t in asks["security"] if m == A] == rounds and "HTTP 404" in by[A]["security"]["error"]
    assert [t for m, t in asks["candles"] if m == B] == rounds and "FORBIDDEN" in by[B]["candles_error"]
    assert (by[A]["fetched_at"], by[B]["fetched_at"]) == (T + 120, T + 120)  # written in the third round
    assert by[C]["security"] == REAL_SECURITY and by[C]["fetched_at"] == T + 60  # its second ask had it
    assert out["tried_again_later"] == 5 and out["refused"] == {"poll": 0, "candles": 1, "security": 1, "pool": 0}


def test_a_rate_limit_is_waited_out_and_the_read_asked_again(tmp_path):
    ft, calls = FakeTime(T), []

    def sec(req):
        calls.append(ft.now)
        if len(calls) == 1:  # a limit that holds longer than the client waits by itself (10 s)
            return httpx.Response(
                429, json={"code": 429, "error": "RATE_LIMIT_BANNED", "message": "too many", "reset_at": T + 120}
            )
        return answer(REAL_SECURITY)

    routes = {
        "/v1/trenches": polls([trench(A, T - 1000)]),
        "/v1/market/token_kline": kline({}),
        "/v1/token/security": sec,
    }
    out = watch(tmp_path, ft, routes, minutes=3)
    assert calls[0] == T and T + 120 <= calls[1] <= T + 125  # asked again once GMGN's limit ended, not before
    assert scored(tmp_path)[0]["security"] == REAL_SECURITY
    # the limit outlasted the first round's minute: the read was left for a later round, and the next look waited
    assert (out["rate_limit_left"], out["rate_limit_waits"]) == (1, 1)
    assert out["refused"] == {"poll": 0, "candles": 0, "security": 0, "pool": 0}


def test_minutes_is_the_jobs_end_and_a_rate_limit_past_it_is_left_to_the_next_job(tmp_path):
    """Review of 25 Nov: a rate limit was waited out whatever --minutes said (up to 900 s, ten times over), and the
    reads after the end took as long as they took: a watch could run hours past its minutes and past its job limit."""
    ft, asks = FakeTime(T), []

    def sec(req):
        asks.append(ft.now)
        return httpx.Response(
            429, json={"code": 429, "error": "RATE_LIMIT_BANNED", "message": "too many", "reset_at": T + 5000}
        )

    routes = {
        "/v1/trenches": polls([trench(A, T - 1000)]),
        "/v1/market/token_kline": kline({}),
        "/v1/token/security": sec,
    }
    out = watch(tmp_path, ft, routes, minutes=2)
    assert ft.now <= T + 120 + LW.FINAL_SLACK_SECS  # it ended at its minutes, and its last reads' slack at the most
    assert asks == [T]  # asked once: every ask while the limit holds makes it longer
    assert (out["scored"], out["waiting"], scored(tmp_path)) == (0, 1, [])  # the coin is the next job's
    assert out["rate_limit_left"] >= 1 and out["refused"]["security"] == 0


def test_reading_the_settled_coins_gives_way_to_the_next_look_at_new_ones(tmp_path):
    ft, looks = FakeTime(T), []

    def trenches(req):
        looks.append(ft.now)
        return answer({"new_creation": [trench(m, T - 1000) for m in (A, B, C, D, E)]})

    def slow(req):  # each coin's candles take 20 s to come
        ft.now += 20
        return answer({"list": []})

    routes = {"/v1/trenches": trenches, "/v1/market/token_kline": slow, "/v1/token/security": security({})}
    out = watch(tmp_path, ft, routes, minutes=3)
    assert looks == [T, T + 60, T + 120]  # every look on time: the reads stopped when the next look was due
    # C's candles came at 60 s, when the next look was due: its security read was not started, and C was read again in
    # the next round; so was E at 120 s
    assert [s["fetched_at"] for s in scored(tmp_path)] == [T, T + 20, T + 60, T + 80, T + 120] and out["scored"] == 5


def test_the_last_reads_stop_at_the_end_and_a_short_slack(tmp_path):
    folder = tmp_path / "history" / "gmgn_launches"
    folder.mkdir(parents=True)
    rows = [{"mint": m, "platform": "clanker", "created_ts": T - 1000, "first_seen": T - 990, "chain": "base"}
            for m in (A, B, C, D, E)]  # fmt: skip
    (folder / "base.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    ft = FakeTime(T)

    def slow(req):  # each coin's candles take 25 s to come
        ft.now += 25
        return answer({"list": []})

    out = watch(tmp_path, ft, {"/v1/market/token_kline": slow, "/v1/token/security": security({})}, minutes=0)
    assert LW.FINAL_SLACK_SECS == 60
    # A at T, B at T + 25; C's candles at T + 50 come at T + 75, past the minute after the end: its security read is
    # not started, and C, D and E are the next job's
    assert [s["mint"] for s in scored(tmp_path)] == [A, B] and (out["scored"], out["waiting"]) == (2, 3)
    assert ft.now == T + 75


def test_a_number_the_client_cannot_convert_leaves_the_coin_for_later_and_the_job_goes_on(tmp_path):
    """Second review of 25 Nov: GMGN's 1e999 (read as infinity) in a row's holder count or a candle's time raised
    OverflowError in the client's conversion: the job ended, the coin stayed waiting and every later job ended on it.
    A row the client cannot convert is left out and counted; a read that fails in any way but GMGN's refusals is asked
    again a round later, three rounds at most, then written as refused."""
    rows = json.dumps({"code": 0, "data": {"new_creation": [trench(A, T - 1000), trench(B, T - 1000)]}})
    rows = rows.replace('"holder_count": 3', '"holder_count": 1e999', 1)  # A's row
    candles = json.dumps({"code": 0, "data": {"list": [gmgn_candle(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0)]}})
    candles = candles.replace(f'"time": {(T - 1000) * 1000}', '"time": 1e999')
    routes = {
        "/v1/trenches": lambda req: raw_answer(rows),
        "/v1/market/token_kline": lambda req: raw_answer(candles),
        "/v1/token/security": security({}),
    }
    out = watch(tmp_path, FakeTime(T), routes, minutes=3)
    assert out["ok"] is True and [r["mint"] for r in launches(tmp_path)] == [B]
    assert (out["polls"], out["bad_rows"], out["read_errors"], out["tried_again_later"]) == (3, 3, 3, 2)
    (b,) = scored(tmp_path)  # B's candles, asked in three rounds, written as refused in the third
    assert b["candles"] == [] and "OverflowError" in b["candles_error"] and b["fetched_at"] == T + 120
    out = watch(tmp_path, FakeTime(T + 300), routes, minutes=1)  # the next job does not end on it either
    assert out["ok"] is True and out["resumed"] == {"seen": 1, "waiting": 0} and out["bad_rows"] == 1


def test_a_row_the_client_reads_as_nothing_is_counted(tmp_path):
    """Third review of 25 Nov: a row the client converts to nothing (its address under another name, as on a chain no
    real answer was read of) was dropped without a count: 90 of them read as a chain where nothing launched."""
    unnamed = {k: v for k, v in trench(A, T).items() if k != "address"} | {"token_address": A}
    routes = {"/v1/trenches": polls([unnamed, trench(B, T), "not a row"])}
    out = watch(tmp_path, FakeTime(T), routes, minutes=3)
    assert (out["polls"], out["recorded"], out["bad_rows"]) == (3, 1, 6)  # two of its three rows, in each look
    quiet = watch(tmp_path / "quiet", FakeTime(T), {"/v1/trenches": polls([])}, minutes=3)
    assert (quiet["recorded"], quiet["bad_rows"]) == (0, 0)


def test_a_refused_key_ends_the_job_after_five_rounds_and_leaves_the_coin_for_the_next_one(tmp_path):
    """A key GMGN refuses ends the job once the same refusal has come in STOP_ROUNDS rounds in a row with no read of
    its kind answered in between (third review of 25 Nov: one refusal ended it at once, a passing one too)."""
    ft = FakeTime(T)
    routes = {
        "/v1/trenches": polls([trench(A, T - 1000)]),
        "/v1/market/token_kline": lambda req: refused(401, "AUTH_KEY_INVALID", f"api key {KEY} not found"),
    }
    out = watch(tmp_path, ft, routes, minutes=60)
    assert out["ok"] is False and "AUTH_KEY_INVALID" in out["error"] and KEY not in json.dumps(out)
    assert LW.STOP_ROUNDS == 5 and ft.now == T + 240 and out["polls"] == 5  # five rounds, not the hour
    assert out["stop_refusals"] == 5 and scored(tmp_path) == []  # nothing written: no read could have worked
    routes["/v1/market/token_kline"] = kline({})
    out2 = watch(tmp_path, FakeTime(T + 300), routes, minutes=0)
    assert out2["resumed"] == {"seen": 1, "waiting": 1} and out2["scored"] == 1


def test_a_plan_that_lost_its_one_second_candles_writes_no_coin_off_and_ends_the_job(tmp_path):
    """Third review of 25 Nov: when the key's plan lost 1 s candles (403 PLAN_UPGRADE_REQUIRED on every candles read),
    each coin due was written as refused and done, 238 of 328 in two hours, and the job ended ok. A refusal of the
    key's plan is one of the key's now: the coins wait, and five rounds of it end the job, not ok."""
    ft = FakeTime(T)
    plan = refused(403, "PLAN_UPGRADE_REQUIRED", "1s candles need the Pro plan")
    routes = {
        "/v1/trenches": polls([trench(m, T - 1000) for m in (A, B, C)]),
        "/v1/market/token_kline": lambda req: plan,
        "/v1/token/security": security({}),
    }
    out = watch(tmp_path, ft, routes, minutes=60)
    assert out["ok"] is False and "PLAN_UPGRADE_REQUIRED" in out["error"] and ft.now == T + 240
    assert (out["scored"], out["waiting"], out["refused"]["candles"]) == (0, 3, 0)  # no coin written off
    routes["/v1/market/token_kline"] = kline({})  # the plan back: the next job reads them all
    out = watch(tmp_path, FakeTime(T + 3600), routes, minutes=0)
    assert (out["scored"], out["waiting"]) == (3, 0)


def test_a_key_refused_from_the_start_ends_a_short_job_not_ok(tmp_path):
    """A job too short for five rounds whose every read GMGN refused says so: no read could work."""
    refusal = refused(401, "AUTH_KEY_INVALID", "api key not found")
    out = watch(tmp_path, FakeTime(T), {route: refusal for route in ("/v1/trenches", "/v1/market/token_kline")},
                minutes=2)  # fmt: skip
    assert out["ok"] is False and "AUTH_KEY_INVALID" in out["error"] and out["stop_refusals"] == 2


def test_a_passing_refusal_of_the_key_or_of_this_machine_leaves_the_round_and_the_watch_goes_on(tmp_path):
    """Third review of 25 Nov: one AUTH_TIMESTAMP_EXPIRED (a request slowed past GMGN's 5 s window) or one
    AUTH_IP_BLOCKED (a request that left over the wrong route) ended a day-long watch at once, and nothing restarted
    it. Such a refusal now leaves its round; the next round asks again."""
    ft = FakeTime(T)
    blips = [(T + 600, refused(401, "AUTH_TIMESTAMP_EXPIRED", "timestamp expired")),
             (T + 1200, refused(403, "AUTH_IP_BLOCKED", "ip not allowed"))]  # fmt: skip

    def trenches(req):
        if blips and ft.now >= blips[0][0]:
            return blips.pop(0)[1]
        return answer({"new_creation": [trench(A, T - 1000)]})

    routes = {"/v1/trenches": trenches, "/v1/market/token_kline": kline({}), "/v1/token/security": security({})}
    out = watch(tmp_path, ft, routes, minutes=30)
    assert out["ok"] is True and ft.now >= T + 1800  # it ran its half hour
    assert (out["polls"], out["stop_refusals"], out["scored"]) == (28, 2, 1)  # two looks refused, then asked again


def test_a_coins_own_failure_on_the_way_counts_a_round_when_its_kind_answers_in_it(tmp_path):
    """A read that fails on the way counts a failed round against its coin when GMGN answered a read of the same kind
    in that round, before or after it; three such rounds, and the coin's next failure writes it as refused."""
    asks: list = []

    def candles(req):
        mint = req.url.params["address"]
        asks.append(mint)
        if mint == B:
            raise httpx.ConnectError("connection refused", request=req)
        if mint == A and asks.count(A) <= 3:  # the client asks three times on a server error before it gives up
            return httpx.Response(503, text="upstream unavailable")
        return answer({"list": [gmgn_candle(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0)]})

    routes = {
        "/v1/trenches": polls([trench(A, T - 1000), trench(B, T - 1000), trench(C, T - 1000)]),
        "/v1/market/token_kline": candles,  # C's answers in each round, until C is written
        "/v1/token/security": security({C: refused(404, "NOT_FOUND", "no such token")}),
    }
    out = watch(tmp_path, FakeTime(T), routes, minutes=5)
    by = {s["mint"]: s for s in scored(tmp_path)}
    assert by[A]["candles"] == [[T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0, 1000.0]] and by[A]["fetched_at"] == T + 60
    assert "HTTP 404" in by[C]["security"]["error"] and by[C]["fetched_at"] == T + 120  # its third answered refusal
    # B's connection refused in every round: counted in the three rounds C's candles answered in, written in the 4th
    assert by[B]["candles"] == [] and "network" in by[B]["candles_error"] and by[B]["fetched_at"] == T + 180
    assert by[B]["security"] == REAL_SECURITY
    assert (out["tried_again_later"], out["refused"]["candles"], out["scored"]) == (6, 1, 3)


def test_an_outage_counts_no_round_against_the_coins_due_in_it(tmp_path):
    """Third review of 25 Nov: GMGN's candles down for three minutes (503 on every try) wrote every coin due in them
    as refused, and no later job read them again. A round counts against a coin only when a read of its kind
    answered in it: an outage counts none."""
    ft = FakeTime(T)
    coins = MANY[:10]

    def candles(req):
        if ft.now < T + 180:
            return httpx.Response(503, text="upstream unavailable")
        return answer({"list": [gmgn_candle(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0)]})

    routes = {
        "/v1/trenches": polls([trench(m, T - 1000) for m in coins]),
        "/v1/market/token_kline": candles,
        "/v1/token/security": security({}),
    }
    out = watch(tmp_path, ft, routes, minutes=5)
    lines = scored(tmp_path)
    assert len(lines) == 10 and not [x for x in lines if "candles_error" in x]  # all read once GMGN answered again
    assert {x["fetched_at"] for x in lines} == {T + 180}
    assert (out["tried_again_later"], out["outage_waits"], out["refused"]["candles"]) == (0, 30, 0)


def test_one_watch_at_a_time_on_a_chain(tmp_path):
    routes = {"/v1/trenches": polls([trench(A, T)])}
    seen: list = []
    with LW.chain_lock(tmp_path, "base"):
        out = watch(tmp_path, FakeTime(T), routes, seen, minutes=1)
    assert out["ok"] is False and "base" in out["error"] and seen == [] and launches(tmp_path) == []
    with LW.chain_lock(tmp_path, "eth"):  # another chain's watch does not hold this one back
        out = watch(tmp_path, FakeTime(T), routes, minutes=1)
    assert out["ok"] is True and out["recorded"] == 1


def test_a_line_cut_short_by_a_killed_job_is_written_again_on_a_line_of_its_own(tmp_path):
    folder = tmp_path / "history" / "gmgn_launches"
    folder.mkdir(parents=True)

    def row(mint):
        return {"mint": mint, "platform": "clanker", "created_ts": T - 1000, "open_ts": 0, "first_seen": T - 990,
                "chain": "base"}  # fmt: skip

    # killed while writing B's line, and A's line here
    (folder / "base.jsonl").write_text(json.dumps(row(A)) + "\n" + json.dumps(row(C)) + "\n" + '{"mint":"' + B)
    (folder / "base_scored.jsonl").write_text('{"mint":"' + A + '","chain":"base","candles":[[1')
    down = {A}  # A's candles fail on the way while the next job runs

    def candles(req):
        if req.url.params["address"] in down:
            return httpx.Response(503, text="upstream unavailable")
        return answer({"list": [gmgn_candle(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0)]})

    routes = {
        "/v1/trenches": polls([trench(B, T)]),
        "/v1/market/token_kline": candles,
        "/v1/token/security": security({}),
    }
    out = watch(tmp_path, FakeTime(T), routes, minutes=1)
    assert out["resumed"] == {"seen": 2, "waiting": 2}  # A, its line cut, and C; B's own line was cut too
    seen_lines = (folder / "base.jsonl").read_text().splitlines()
    assert len(seen_lines) == 4 and json.loads(seen_lines[3])["mint"] == B  # B again, not glued to the cut line
    scored_lines = (folder / "base_scored.jsonl").read_text().splitlines()
    assert len(scored_lines) == 2 and json.loads(scored_lines[1])["mint"] == C  # C on a line of its own; A waits
    # Review of 25 Nov: at the second restart the cut line is no longer the last, and its head said A was done
    down.clear()
    seen: list = []
    out = watch(tmp_path, FakeTime(T + 200), routes, seen, minutes=0)
    assert out["resumed"] == {"seen": 3, "waiting": 2}  # A, still, and B (900 s past its start only at T + 900)
    assert [r.url.params["address"] for r in asked(seen, "/v1/market/token_kline")] == [A]
    scored_lines = (folder / "base_scored.jsonl").read_text().splitlines()
    whole = [[T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0, 1000.0]]
    assert len(scored_lines) == 3 and json.loads(scored_lines[2])["candles"] == whole
    assert LW.score(tmp_path, "base")["unreadable_lines"] == 1
    seen = []  # the next job reads A and C as done and B as waiting, and asks for nothing yet
    out = watch(tmp_path, FakeTime(T + 300), routes, seen, minutes=0)
    assert out["resumed"] == {"seen": 3, "waiting": 1} and seen == []


def test_a_line_cut_right_after_a_closing_brace_still_names_no_coin(tmp_path):
    """Second review of 25 Nov: a line ends with the security answer and then the pool's, and answers hold dicts in
    dicts, so a line cut by a killed job can end with "}": its head was trusted and the coin never read again. Every
    line now ends with a last key, "end": 1, and only a line that ends with it is read by its head."""
    folder = tmp_path / "history" / "gmgn_launches"
    folder.mkdir(parents=True)
    row = {"mint": A, "platform": "clanker", "created_ts": T - 1000, "first_seen": T - 990, "chain": "base"}
    (folder / "base.jsonl").write_text(json.dumps(row) + "\n")
    line = {"mint": A, "chain": "base", "start": T - 1000, "platform": "clanker", "fetched_at": T - 100,
            "candles": [[T - 1000, 1, 1, 1, 1, 5]], "last_candle_ts": T - 1000,
            "security": dict(NO_TAX, lock_summary={"is_locked": False}), "pool": HEALTHY_POOL, "end": 1}  # fmt: skip
    whole = json.dumps(line, separators=(",", ":"))
    (folder / "base_scored.jsonl").write_text(whole[: whole.index(',"pool"')])  # killed right after the security
    seen: list = []
    routes = {"/v1/market/token_kline": kline({A: [(T - 1000, 1.0, 1, 1, 1, 5)]}), "/v1/token/security": security({})}
    out = watch(tmp_path, FakeTime(T), routes, seen, minutes=0)
    assert out["resumed"] == {"seen": 1, "waiting": 1} and len(asked(seen, "/v1/market/token_kline")) == 1
    lines = (folder / "base_scored.jsonl").read_text().splitlines()
    assert len(lines) == 2 and lines[1].endswith(',"end":1}') and json.loads(lines[1])["mint"] == A
    seen = []  # the next job reads A as done
    out = watch(tmp_path, FakeTime(T + 100), routes, seen, minutes=0)
    assert out["resumed"] == {"seen": 1, "waiting": 0} and seen == []


# -- the score: what an early buyer would have made ---------------------------------------------------------------------


DEEP_POOL = {"liquidity": "1e12"}  # a pool read at settle that pays a stale sale what the same sale shown would get


def coin(mint, candles, sec=None, *, platform="clanker", fetched_at=None, first=None, pool=DEEP_POOL, liquidity=1e12):
    """A line of <chain>_scored.jsonl as the watch writes it: a coin started at S, the dollars of its pool and the taxes
    and flags of its row when first seen (`liquidity`: so deep by default that a copy moves no price; `first`), its
    candles as [ts, open, high, low, close, dollars], its security as GMGN sent it, and its pool as read at settle
    (`pool`: a deep one by default, so a sale no later sale shows is paid as if one did; None: not read)."""
    return {
        "mint": mint,
        "chain": "base",
        "start": S,
        "platform": platform,
        "symbol": "TKN",
        "created_ts": S,
        "liquidity_usd": liquidity,
        # its price on that row: its first priced candle's close
        "price_usd": next((c[4] for c in candles if isinstance(c[4], int | float) and c[4] > 0), 1.0),
        "first_seen": S + 3,
        **(first or {}),
        "fetched_at": S + 900 if fetched_at is None else fetched_at,
        "candles": [list(c) for c in candles],
        "security": dict(NO_TAX) if sec is None else sec,
        **({} if pool is None else {"pool": pool}),
    }


def flat(price, at=0):
    """A one-second candle at S + `at` that opened, peaked, dipped and closed at `price`."""
    return (S + at, price, price, price, price, 1.0)


def to_the_end(candles):
    """A coin's candles and one more at 499 s: a sale of a trillion dollars a hair below the last close. The coin traded
    on to the end of its 500 s, and a copy's sale just before that trade is shown by it (a later sale of at least the
    copy's worth): not stale, the pool read at settle has no say in it."""
    close = candles[-1][4] * (1 - 1e-9)
    return [*candles, (S + 499, close, close, close, close, 1e12)]


def write_scored(tmp_path, coins, chain="base", extra=""):
    folder = tmp_path / "history" / "gmgn_launches"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{chain}_scored.jsonl").write_text("".join(json.dumps(c) + "\n" for c in coins) + extra)


def pair(out, delay, hold):
    """The table's row of one entry delay and hold."""
    return next(r for r in out["table"] if (r["delay"], r["hold"]) == (delay, hold))


def test_a_copy_pays_its_exit_over_its_entry_less_both_taxes_two_swap_fees_and_two_gas_fees(tmp_path):
    candles = to_the_end([flat(1.0), flat(2.0, 10), flat(1.5, 40), flat(1.5, 100)])
    # in at the close of the last candle at or before start + 1 (1.0), out at or before start + 31 (2.0):
    # 2.0 / 1.0 x 0.99 x 0.99 x 0.95 x 0.90 - 1 - 2 x 0.05 / 100: Base's swap fee of 1 percent on each leg.
    kept = 0.99**2 * 0.95 * 0.9
    # the same taxes said as shares of 1 (A) and as percents (B): each value is read on its own
    write_scored(tmp_path, [coin(A, candles, {"buy_tax": "0.05", "sell_tax": "0.1"}),
                            coin(B, candles, {"buy_tax": 5, "sell_tax": "10"})])  # fmt: skip
    out = LW.score(tmp_path, "base", size_usd=100.0, gas_usd=0.05)
    assert pair(out, 2, 30)["coins"] == 2 and pair(out, 2, 30)["mean"] == pytest.approx(2 * kept - 1.001, abs=1e-4)
    assert pair(out, 2, 120)["mean"] == pytest.approx(1.5 * kept - 1.001, abs=1e-4)  # out at start + 40: 1.5
    assert pair(out, 12, 30)["mean"] == pytest.approx(0.75 * kept - 1.001, abs=1e-4)  # in at start + 10: 2.0
    assert (out["security"]["ambiguous_coins"], out["security"]["coins_with_a_tax_above_1"]) == (1, 1)
    assert (out["size_usd"], out["gas_usd"], out["fee_pct"]) == (100.0, 0.05, 1.0)
    write_scored(tmp_path, [coin(A, candles, {"buy_tax": "0.05", "sell_tax": "0.1"})], chain="eth")
    eth = LW.score(tmp_path, "eth")  # Ethereum's gas when none is given: 3 dollars a transaction
    # and its blocks of 12 s: in behind the trades of the block at 10 s (2.0), out behind those at 40 s (1.5)
    assert eth["gas_usd"] == 3.0 and pair(eth, 2, 30)["mean"] == pytest.approx(0.75 * kept - 1.06, abs=1e-4)


def test_each_leg_pays_the_swap_fee_of_its_chain(tmp_path):
    """Review of 25 Nov: no swap fee was charged. A launchpad's pool takes its fee on the buy and again on the sale:
    pump.fun's 1.25 percent on Solana (GMGN's rows of 12 Nov: 0.0125 a side), 1 percent on BNB Chain, Base and
    Ethereum unless --fee-pct says otherwise."""
    up = to_the_end([flat(1.0), flat(2.0, 20)])  # past the block a buy at 2 s lands in, on every chain
    for chain, fee in (("base", 1.0), ("bsc", 1.0), ("eth", 1.0), ("sol", 1.25)):
        write_scored(tmp_path, [coin(A, up)], chain=chain)
        out = LW.score(tmp_path, chain, gas_usd=0.0)
        assert out["fee_pct"] == fee, chain
        assert pair(out, 2, 30)["mean"] == pytest.approx(2.0 * (1 - fee / 100) ** 2 - 1, abs=1e-4), chain
    out = LW.score(tmp_path, "sol", gas_usd=0.0, fee_pct=3)
    assert out["fee_pct"] == 3.0 and pair(out, 2, 30)["mean"] == pytest.approx(2.0 * 0.97**2 - 1, abs=1e-4)
    assert "(1 - fee)^2" in out["reading"] and "1.25 percent" in out["reading"]
    for bad in (-1, 100, "nan"):
        with pytest.raises(ValueError, match="--fee-pct"):
            LW.score(tmp_path, "sol", fee_pct=bad)


def test_a_coin_that_cannot_be_sold_is_all_lost(tmp_path):
    up = to_the_end([flat(1.0), flat(3.0, 10)])
    write_scored(
        tmp_path,
        [
            coin(A, up, {"is_honeypot": "1"}),
            coin(B, up, {"honeypot": 1, "buy_tax": "0", "sell_tax": "0"}),
            coin(C, up, {"can_not_sell": 1}),
            coin(D, up, {"sell_tax": "0.6"}),  # a sale that keeps 40 percent counts as no sale
            coin(F, up, {"sell_tax": "0.5"}),  # and one that keeps half
            # can_sell 0 alone says nothing: the real answer of 12 Nov gave it of a coin that traded
            coin(E, up, dict(NO_TAX, sell_tax="0.49")),
        ],
    )
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    row = pair(out, 2, 30)
    assert row["coins"] == 6 and row["honeypots"] == 5
    assert row["mean"] == pytest.approx((5 * -1.0 + (3.0 * 0.51 - 1)) / 6, abs=1e-4)
    assert out["security"]["honeypots"] == 5


def test_each_leg_pays_the_higher_of_the_flat_fee_and_the_coins_own(tmp_path):
    """Fourth review of 25 Nov: GMGN's real rows of new coins give each coin's own fee (total_buy_tax, total_sell_tax
    and fee_params: pump.fun's 1.25 percent, 1.95 on the coin SU), and the watch threw them away, so a day watched could
    never be scored at its coins' own fees. The watch keeps them with the coin's row, and each leg pays the higher of
    the chain's flat fee and the coin's own (its tax on top: should GMGN's total hold the tax, it is charged twice, on
    the safe side)."""
    rows, _ = LW._new_rows(fixture("real/trenches.json")["data"])
    su = next(r for r in rows if r["symbol"] == "SU")
    assert (su["total_buy_tax"], su["total_sell_tax"]) == (0.0195, 0.0195)
    assert su["fee_params"] == ["2", "0", "19500000", "19500000", "0"]
    up = to_the_end([flat(1.0), flat(2.0, 10)])
    own = {k: su[k] for k in ("total_buy_tax", "total_sell_tax", "fee_params")}
    low = {"total_buy_tax": 0.005, "total_sell_tax": "1.1"}  # both under the flat fee: half a percent, 1.1 percent
    write_scored(tmp_path, [coin(A, up, first=own)], chain="sol")
    out = LW.score(tmp_path, "sol", gas_usd=0.0)
    assert pair(out, 2, 30)["mean"] == pytest.approx(2.0 * (1 - 0.0195) ** 2 - 1, abs=1e-4)
    write_scored(tmp_path, [coin(A, up, first=low)], chain="sol")
    out = LW.score(tmp_path, "sol", gas_usd=0.0)
    assert pair(out, 2, 30)["mean"] == pytest.approx(2.0 * (1 - 0.0125) ** 2 - 1, abs=1e-4)
    assert "total_buy_tax" in out["reading"]


def test_each_leg_pays_the_higher_of_the_launchs_tax_and_the_settled_one(tmp_path):
    """Review of 25 Nov: the taxes on a launch's row when the watch first met it were thrown away, so a tax lowered
    by the time of the settle read was charged at its low. Each leg pays the higher of the two reads, and a coin
    either read says cannot be sold is all lost."""
    up = to_the_end([flat(1.0), flat(2.0, 10)])
    lines = [
        coin(A, up, dict(NO_TAX, buy_tax="0.02", sell_tax="0.1"), first={"buy_tax": 0.05, "sell_tax": 0.03}),
        coin(B, up, dict(NO_TAX), first={"buy_tax": 0, "sell_tax": 0, "is_honeypot": 1}),  # no sale when first seen
        coin(C, up, dict(NO_TAX, sell_tax="0.04"), first={"buy_tax": 0, "sell_tax": 0.04}),
        coin(D, up, dict(NO_TAX), first={"sell_tax": 0.6}),  # a launch tax of 60 percent on a sale: no sale
    ]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    row = pair(out, 2, 30)
    a, c = 2 * 0.95 * 0.9 - 1, 2 * 0.96 - 1  # A: in at the 5 percent seen first, out at the 10 read at settle
    assert (row["coins"], row["honeypots"]) == (4, 2) and row["mean"] == pytest.approx((a - 1 + c - 1) / 4)
    sec = out["security"]
    assert (sec["honeypots"], sec["first_sight"]["honeypots"], sec["taxes_changed"]) == (2, 2, 2)  # A's, D's moved
    assert sec["first_sight"]["fields"]["is_honeypot"] == {"given": 1, "yes": 1}
    assert sec["first_sight"]["fields"]["sell_tax"] == {"given": 4, "above_0": 3}


def test_each_tax_is_read_on_its_own_and_no_coin_changes_how_another_is_read(tmp_path):
    """Reviews of 25 Nov: first the unit of a coin's taxes was guessed coin by coin, then one value above 1 anywhere
    in the file made every tax of it a percent: five sell taxes of 0.6 (60 percent, no sale) read as 0.6 percent once
    a sixth coin's launch row said a buy tax of 3. Each value is read on its own now: 1 or less is a share of 1, above
    1 a percent. A value of 1 or less is so read as the higher of the two taxes it could be."""
    up = to_the_end([flat(1.0), flat(2.0, 10)])
    sixty = [coin(m, up, dict(NO_TAX, sell_tax="0.6")) for m in (A, B, C, D, E)]
    for lines in (sixty, [*sixty, coin(F, up, first={"buy_tax": 3})]):
        write_scored(tmp_path, lines)
        out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
        sec, row = out["security"], pair(out, 2, 30)
        assert (sec["honeypots"], sec["ambiguous_coins"], row["honeypots"]) == (5, 5, 5)
        assert sec["coins_with_a_tax_above_1"] == len(lines) - 5  # F's 3 is 3 percent
        assert row["mean"] == pytest.approx((-5 + (2 * 0.97 - 1) * (len(lines) - 5)) / len(lines), abs=1e-4)
        # what every tax read one way would give, for information only
        assert (sec["honeypots_if_shares"], sec["honeypots_if_percents"]) == (5, 0)


def test_a_copy_that_cannot_be_sold_still_pays_its_gas(tmp_path):
    """Second review of 25 Nov: a honeypot, or a sale lost to an emptied pool, scored -1 flat, while a coin that went
    to zero on Ethereum scored -1.06: each pays the two transactions' gas, as every other copy does."""
    write_scored(tmp_path, [coin(A, [flat(1.0), flat(2.0, 10)], {"is_honeypot": 1}),
                            coin(B, to_the_end([flat(1.0), flat(1e-12, 20)]))], chain="eth")  # fmt: skip
    out = LW.score(tmp_path, "eth", fee_pct=0.0)  # 3 dollars a transaction: 0.06 of a copy of 100
    assert (out["gas_share"], pair(out, 2, 30)["mean"]) == (0.06, pytest.approx(-1.06))
    write_scored(tmp_path, [coin(C, [flat(1.0), flat(3.0, 10)], pool={"liquidity": "0"})], chain="eth")
    assert pair(LW.score(tmp_path, "eth", fee_pct=0.0), 2, 120)["mean"] == pytest.approx(-1.06)  # sold after its pull


def test_the_table_says_how_the_coins_did_without_the_five_best_and_how_many_rugged(tmp_path):
    ends = {A: (10.0, 10.0), B: (5.0, 5.0), C: (2.0, 2.0), D: (1.5, 1.5), E: (1.2, 1.2), F: (0.5, 0.05), G: (0.1, 0.1)}
    coins = [coin(m, to_the_end([flat(1.0), (S + 10, 1.0, max(1.0, c), lo, c, 1.0)])) for m, (c, lo) in ends.items()]
    write_scored(tmp_path, coins)
    row = pair(LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0), 2, 30)  # returns 9, 4, 1, 0.5, 0.2, -0.5 and -0.9
    assert row["coins"] == 7
    assert row["mean"] == pytest.approx(1.9, abs=1e-4) and row["median"] == pytest.approx(0.5, abs=1e-4)
    assert row["share_above_0"] == pytest.approx(5 / 7, abs=1e-4)
    assert row["mean_without_best5"] == pytest.approx(-0.7, abs=1e-4)  # one big winner does not carry it
    assert row["rug_share"] == pytest.approx(2 / 7, abs=1e-4)  # F dipped to 5 percent of its first price, G to 10


def test_no_hold_runs_past_the_500_seconds_of_candles(tmp_path):
    write_scored(tmp_path, [coin(A, [flat(0.5), flat(1.0, 11), flat(2.0, 491), flat(4.0, 492), flat(8.0, 499)])])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    pairs = {(r["delay"], r["hold"]) for r in out["table"]}
    assert len(pairs) == 15 and (30, 480) not in pairs  # 30 + 480 = 510 s: past the candles
    assert [(n["delay"], n["hold"]) for n in out["not_scored"]] == [(30, 480)]
    # in at 12 s at 11 s's close (1.0: nobody traded in its own two seconds), out at 492 s behind the trades of its own
    # block (4.0): never at 499's
    assert pair(out, 12, 480)["mean"] == pytest.approx(3.0)


def test_beside_every_number_a_pessimistic_bound_buys_at_the_high_and_sells_at_the_low(tmp_path):
    """Fourth review of 25 Nov: each round found a new way the fill at a candle's close reads kind (a close is probably
    its last trade's price, not the pool's after it). Beside every number filled at the close behind the landing
    block's trades is a pessimistic bound: the entry at the high of the landing block's candle, each sale at the low
    of the exit block's candle, price impact and every charge as for the number beside it. The reading leads with it."""
    # Solana, a second a block: in at 2 s's candle (close 1.1, high 1.3), out at 32 s's (close 1.5, low 1.2)
    candles = to_the_end([flat(1.0), (S + 2, 1.0, 1.3, 0.9, 1.1, 5.0), (S + 32, 1.5, 1.6, 1.2, 1.5, 5.0)])
    honeypot = coin(B, candles, {"is_honeypot": 1})
    write_scored(tmp_path, [coin(A, candles), honeypot], chain="sol")
    out = LW.score(tmp_path, "sol", gas_usd=0.0, fee_pct=0.0)
    row = pair(out, 2, 30)
    central, bound = 1.5 / 1.1 - 1, 1.2 / 1.3 - 1  # the honeypot is all lost either way
    assert row["mean"] == pytest.approx((central - 1) / 2, abs=1e-4)
    assert row["pessimistic"]["mean"] == pytest.approx((bound - 1) / 2, abs=1e-4)
    assert set(row["pessimistic"]) == {"mean", "mean_se", "median", "share_above_0", "mean_without_best5"}
    stale = pair({"table": out["stale_sales"]}, 2, 30)
    assert set(stale["pessimistic"]) == {"mean", "mean_se"}
    assert out["reading"].startswith("Every number has a pessimistic bound beside it")
    assert "if only the central number pays, a live test at the stated bid decides" in out["reading"]


def test_a_trade_fills_behind_every_trade_of_the_second_or_block_it_lands_in(tmp_path):
    """Third review of 25 Nov: every buy and sale filled at the last close before its moment, as the first trade of
    the second or block it lands in, a place the default gas does not buy (CLAUDE.md rule 2: a result names its
    landing and the bid that buys it). A trade at t now fills behind every trade of its own second, or block: at the
    close of the first candle at or after t that opened within a block of it (Base 2 s); with none, nobody else traded
    in its block, and it fills at the last close before t. A copy still buys only a coin that traded before t."""
    write_scored(tmp_path, [coin(A, to_the_end([flat(1.0), flat(3.0, 2), flat(5.0, 31), flat(9.0, 32)]))])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["mean"] == pytest.approx(2.0)  # in behind 2 s's trades (3.0), out behind 32 s's (9.0)
    write_scored(tmp_path, [coin(A, to_the_end([flat(1.0), flat(2.0, 10)]))])  # nobody else in its blocks
    assert pair(LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0), 2, 30)["mean"] == pytest.approx(1.0)
    write_scored(tmp_path, [coin(B, [flat(1.0, 2), flat(2.0, 20)])])  # its first trade in the second after 2 s
    by_delay = {d["delay"]: (d["with_entry"], d["no_trade_yet"]) for d in LW.score(tmp_path, "base")["by_delay"]}
    assert by_delay == {2: (0, 1), 5: (1, 0), 12: (1, 0), 30: (1, 0)}


def test_the_last_sales_land_and_are_shown_past_the_window_on_ethereum(tmp_path):
    """Fourth review of 25 Nov: on Ethereum the last block inside a coin's 500 s is at +492 s, so every hold of 480 s
    read stale (lost when the pool read failed), even on a coin that traded in every block. The candles are read on
    until the last sale (start + 492 s) has landed and the trade after it is in: two of the chain's blocks past it."""
    # a sale of 500 dollars each block, each a hair below the last: every sale of a copy is shown by the next one
    blocks = [(S + t, 1.0, 1.0, 1.0, 1.0 - t * 1e-6, 500.0) for t in range(0, 517, 12)]
    refused_pool = {"error": "gmgn GET /v1/token/pool_info: HTTP 404 NOT_FOUND: no pool"}
    write_scored(tmp_path, [coin(A, blocks, pool=refused_pool)], chain="eth")
    out = LW.score(tmp_path, "eth", gas_usd=0.0, fee_pct=0.0)
    assert [pair(out, d, 480)["stale_exits"] for d in (2, 5, 12)] == [0, 0, 0]
    assert pair(out, 12, 480)["mean"] == pytest.approx(0.0, abs=1e-3)  # in at +24 s, out at +504 s
    # the watch asks GMGN for the candles that far: on Ethereum 516 s, on Base the 500 s they were
    for chain, secs in (("eth", 516), ("base", 500)):
        seen: list = []
        watch(tmp_path / chain, FakeTime(T), {"/v1/trenches": polls([trench(A, T - 1000)])}, seen, chain=chain,
              minutes=1)  # fmt: skip
        (ask,) = [r.url.params for r in asked(seen, "/v1/market/token_kline")]
        assert int(ask["to"]) - int(ask["from"]) == secs * 1000, chain


def test_a_copy_sent_at_a_blocks_own_time_lands_in_the_next_block(tmp_path):
    """Fourth review of 25 Nov: on a chain of blocks longer than a second, a copy sent at a block's own timestamp
    filled in that block, which was sealed before it was sent (Base builds a block from what reaches it in the two
    seconds before its stamp). It lands in the first block stamped after its moment, a block later at most: sent at
    2 s it buys behind the trades of the block of 4 s, and sold at 32 s, behind those of 34 s."""
    stamped = to_the_end([flat(1.0), flat(3.0, 2), flat(4.0, 4), flat(8.0, 32), flat(6.0, 34)])
    write_scored(tmp_path, [coin(A, stamped)])
    assert pair(LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0), 2, 30)["mean"] == pytest.approx(6.0 / 4.0 - 1)
    # with nobody trading in the block after it, the block stamped at the moment gives the pool's price: its close
    write_scored(tmp_path, [coin(A, to_the_end([flat(1.0), flat(3.0, 2), flat(9.0, 32)]))])
    assert pair(LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0), 2, 30)["mean"] == pytest.approx(2.0)
    # Solana's and BNB Chain's second: a trade of the second it is sent in is a trade of its own block
    write_scored(tmp_path, [coin(A, stamped)], chain="sol")
    assert pair(LW.score(tmp_path, "sol", gas_usd=0.0, fee_pct=0.0), 2, 30)["mean"] == pytest.approx(5 / 3, abs=1e-4)


def test_the_landing_is_behind_the_trades_of_the_block_and_named_in_the_report(tmp_path):
    """Third review of 25 Nov (the reviewer's coins): on Ethereum, blocks of 12 s, a buy sent 2 s after the start was
    priced at the launch block's close, first in the next block for 3 dollars of gas: +60.6 percent at 2/30 where a
    copy behind that block's trades got -14.1. On Base, +46.9 against -2.1. A delay shorter than a block rounds up to
    the next block's candle, and the report says where a copy lands, at which bid. Fourth review of 25 Nov: a copy sent
    at a block's own time (Base 2 s, Ethereum 12 s) lands in the next block, -8.2 and -25.3 percent."""

    def blocks(every, closes):  # a candle a block, the last close to the end of the window
        out, prev = [], closes[0]
        for k in range(500 // every + 1):
            c = closes[min(k, len(closes) - 1)]
            out.append((S + k * every, prev, max(prev, c), min(prev, c), c, 50.0))
            prev = c
        return out

    for chain, every, closes, want in (
        ("eth", 12, [1.0, 1.6, 1.7, 1.5, 1.4], {2: -0.1412, 5: -0.1412, 12: -0.2529}),
        ("base", 2, [1.0, 1.5, 1.6, 1.55, 1.5], {2: -0.0822, 5: -0.0525, 12: -0.0209}),
    ):
        write_scored(tmp_path, [coin(A, blocks(every, closes))], chain=chain)
        out = LW.score(tmp_path, chain, fee_pct=1.0)
        assert {d: pair(out, d, 30)["mean"] for d in want} == pytest.approx(want, abs=1e-4), chain
        assert out["block_secs"] == every and "behind every trade of the second (or the block)" in out["landing"]
        assert "a plain bid, no priority" in out["landing"] and "none is scored" in out["landing"]
        assert "its last trade's price" in out["landing"]  # fourth review: the close, and the bound beside it
    assert "rounds up to the next block's candle" in out["landing"]  # Base's blocks of 2 s


def test_a_missing_or_zero_price_is_skipped_never_divided_by(tmp_path):
    nothing = (None, None, None, None, None)
    write_scored(
        tmp_path,
        [
            coin(A, to_the_end([flat(1.0), (S + 1, *nothing), (S + 2, 0, 0, 0, 0, 1.0), flat(3.0, 20)])),
            coin(B, [(S, 0, 0, 0, 0, 1.0), (S + 30, *nothing)]),  # no price at all: no trade at any delay
            # its first trade 8 s in: none yet at 2 and 5 s (a candle from before its start is no trade of its 500 s)
            coin(C, to_the_end([flat(5.0, -5), flat(1.0, 8), flat(2.0, 20)])),
        ],
    )
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["coins"] == 1 and pair(out, 2, 30)["mean"] == pytest.approx(2.0)  # A: in at 1.0, out 3.0
    assert pair(out, 12, 30)["coins"] == 2 and pair(out, 12, 30)["mean"] == pytest.approx(1.5)  # A 2.0, C 1.0
    by_delay = {d["delay"]: (d["with_entry"], d["no_trade_yet"]) for d in out["by_delay"]}
    assert by_delay == {2: (1, 2), 5: (1, 2), 12: (2, 1), 30: (2, 1)}
    assert out["candles"]["bad_prices_skipped"] == 4 and out["candles"]["without_a_price"] == 1


def test_the_best_pair_is_chosen_without_its_five_best_coins(tmp_path):
    # one coin of the thirty of half 0 goes x101 at 10 s: alone it makes a hold to 30 s look best on the mean. The
    # sale of 50,000 dollars that takes it back to 1.2 at 100 s shows its pool held a copy's worth until then (a trade
    # of cents would show nothing)
    chosen_on, judged_on = halves(30)
    lines = [coin(m, [flat(1.0), flat(x, 10), (S + 100, 1.2, 1.2, 1.2, 1.2, 50_000.0), flat(1.1, 250)])
             for m, x in zip([*chosen_on, *judged_on], (101.0, *[1.0] * 59), strict=True)]  # fmt: skip
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["mean"] == pytest.approx(100 / 60, abs=1e-3) and pair(out, 2, 30)["mean_without_best5"] == 0
    assert (out["best"]["delay"], out["best"]["hold"]) == (2, 120)  # every coin +20 percent
    assert [(p["platform"], p["coins"]) for p in out["by_platform"]] == [("clanker", 30)]  # half 1's


def test_the_best_pair_is_split_by_launchpad(tmp_path):
    lines = [coin(m, to_the_end([flat(1.0), flat(2.0, 10)]), platform="clanker") for m in both_halves(30, 1)]
    lines += [coin(m, to_the_end([flat(1.0), flat(0.5, 10)]), platform="zora") for m in both_halves(30, 2)]
    lines.append(coin(A, to_the_end([flat(1.0), flat(1.5, 10)]), platform=None))
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert (out["best"]["delay"], out["best"]["hold"]) == (2, 30)
    got = {p["platform"]: (p["coins"], p.get("mean"), p.get("too_few_coins")) for p in out["by_platform"]}
    assert got == {
        "clanker": (30, pytest.approx(1.0), None),
        "zora": (30, pytest.approx(-0.5), None),
        "unknown": (1, None, True),  # one coin: no numbers
    }


def test_the_best_pair_and_the_launchpads_are_chosen_on_one_half_and_judged_on_the_other(tmp_path):
    """Third review of 25 Nov: the best pair and the launchpads' order were chosen on the coins they were then judged
    on. In a market with no edge the best pair showed a mean without its five best of -1.0 percent where new coins gave
    -3.6, and the top launchpad -2.0 where they gave -10.7. The coins are split in two by a hash of their address: the
    best pair and the launchpads' order are chosen on half 0, their numbers are half 1's."""
    clanker, zora = halves(30, 1), halves(30, 2)
    rise = {("clanker", 0): 2.0, ("zora", 0): 1.5, ("clanker", 1): 0.5, ("zora", 1): 1.2}  # at 10 s, back at 100 s
    lines = [coin(m, to_the_end([flat(1.0), flat(rise[(name, half)], 10), flat(1.0, 100)]), platform=name)
             for name, split in (("clanker", clanker), ("zora", zora)) for half in (0, 1) for m in split[half]]  # fmt: skip
    newpad = next(m for m in mints(50) if LW.half_of(m) == 0)
    write_scored(tmp_path, [*lines, coin(newpad, to_the_end([flat(1.0), flat(9.0, 10)]), platform="newpad")])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    best = out["best"]
    # chosen on half 0, where 2/30 made +100 and +50 percent; its numbers half 1's: -50 and +20 percent
    assert (best["delay"], best["hold"], best["coins"], best["mean"]) == (2, 30, 60, pytest.approx(-0.15))
    assert best["chosen_on"] == {"half": 0, "coins": 61} and best["numbers_from"] == {"half": 1}
    # the launchpads in their order on half 0, with half 1's numbers: clanker first at -50 percent
    rows = [(p["platform"], p.get("rank"), p["coins"], p.get("mean")) for p in out["by_platform"]]
    assert rows == [("clanker", 1, 30, -0.5), ("zora", 2, 30, 0.2), ("newpad", None, 1, None)]
    assert out["coins_by_half"] == [61, 60] and "half_of" in out["reading"]
    assert LW.half_of(A.upper().replace("0X", "0x")) == LW.half_of(A)  # an address in capitals is the same coin


def test_one_creators_coins_sit_in_one_half_and_count_as_one_in_the_standard_error(tmp_path):
    """Fourth review of 25 Nov: the halves were split by a hash of each coin's address, so one creator's coins (a
    deployer launching dozens a day the same way) fell on both sides: the best pair chosen on that creator's coins of
    half 0 was confirmed by the same creator's coins of half 1, +19.1 percent +/- 3.3 where the same pair split by
    creator read 0.0. The halves are split by a hash of the creator's address (the coin's own when none is given),
    each row says how many creators its copies have, and the standard error is taken over creators: a creator's coins
    move together, and count as one."""
    serial = next(f"0x{k:040x}" for k in range(1, 99) if LW.half_of(f"0x{k:040x}") == 0)  # its coins: all in half 0
    pumped = to_the_end([flat(1.0), flat(1.5, 20), flat(1.5, 40), flat(0.95, 45)])  # its bot pumps them half a minute
    lines = [coin(m, pumped) | {"creator": serial} for m in mints(40, 1000)]
    lines += [coin(m, to_the_end([flat(1.0), flat(0.95, 10)])) for m in mints(80, 2000)]  # 80 creators: -5 percent
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    best, row = out["best"], pair(out, 2, 30)
    assert (best["delay"], best["hold"]) == (12, 30)  # chosen on half 0, its creator's coins up 50 percent
    assert (best["mean"], best["creators"]) == (0.0, best["coins"])  # half 1: the others alone, in after their fall
    assert (row["coins"], row["creators"]) == (120, 81)
    # over creators: the serial creator's 40 copies are one deviation of 40 x (0.5 - mean), not 40 small ones
    mean = (40 * 0.5 - 80 * 0.05) / 120
    spread = (40 * (0.5 - mean)) ** 2 + 80 * (-0.05 - mean) ** 2
    assert row["mean_se"] == pytest.approx((81 / 80 * spread) ** 0.5 / 120, abs=1e-4)
    assert "a hash of their creator's address" in out["reading"] and "over creators" in out["reading"]


def test_too_few_coins_name_no_best_pair_and_give_a_launchpad_no_numbers(tmp_path):
    """Third review of 25 Nov: the report named a best pair, and split it by launchpad, on any number of coins: one
    coin that tripled was the best pair at +200 percent, and a launchpad of one coin got a mean and a median; nothing
    said how sure a number was. A pair needs 30 coins to be called best, a launchpad 30 to get numbers (too few
    coins), and every mean has its standard error beside it."""
    write_scored(tmp_path, [coin(A, to_the_end([flat(1.0), flat(3.0, 25), flat(1.2, 100)]))])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert (out["best"], out["by_platform"]) == (None, []) and "too few coins" in out["no_best"]
    assert (pair(out, 2, 30)["coins"], pair(out, 2, 30)["mean_se"]) == (1, None)  # one copy: no spread to read
    assert "mean_se" in out["reading"] and "30 coins" in out["reading"]
    lines = [coin(m, to_the_end([flat(1.0), flat(1.1 if k % 2 else 1.3, 20)]), platform="clanker")
             for half in halves(40) for k, m in enumerate(half)]  # fmt: skip
    write_scored(tmp_path, [*lines, coin(A, to_the_end([flat(1.0), flat(2.0, 20)]), platform="newpad")])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert out["best"] is not None and "no_best" not in out
    clanker, newpad = out["by_platform"]
    assert newpad == {"platform": "newpad", "coins": 1, "too_few_coins": True}
    # twenty copies at +10 and twenty at +30 percent: a standard deviation of 0.1013, over the square root of 40
    assert (clanker["coins"], clanker["mean"], clanker["mean_se"]) == (40, 0.2, pytest.approx(0.016, abs=1e-4))
    # thirty coins, not thirty in each half: no best pair
    write_scored(tmp_path, [coin(m, to_the_end([flat(1.0), flat(1.2, 20)])) for m in halves(15)[0] + halves(15)[1]])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert out["best"] is None and "in each half" in out["no_best"]


def test_the_report_counts_the_security_fields_gmgn_gave(tmp_path):
    level = [flat(1.0), flat(1.0, 10)]
    write_scored(
        tmp_path,
        [
            coin(A, level, dict(NO_TAX, sell_tax="0.02")),
            coin(B, level, {"is_honeypot": "1"}),
            coin(C, level, {"error": "gmgn GET /v1/token/security: HTTP 404 NOT_FOUND: no such token"}),
            coin(D, level, {}),
        ],
    )
    sec = LW.score(tmp_path, "base")["security"]
    assert (sec["refused"], sec["without_fields"], sec["honeypots"], sec["ambiguous_coins"]) == (1, 1, 1, 1)
    assert sec["fields"] == {
        "is_honeypot": {"given": 1, "yes": 1},
        "honeypot": {"given": 1, "yes": 0},
        "can_not_sell": {"given": 1, "yes": 0},
        "can_sell": {"given": 1, "yes": 0},
        "buy_tax": {"given": 1, "above_0": 0},
        "sell_tax": {"given": 1, "above_0": 1},
    }


def test_a_coin_whose_safety_is_not_known_stays_in_the_table_marked_and_counted(tmp_path):
    """Reviews of 25 Nov: a coin whose security GMGN refused, or answered without one of the fields read here, was
    first scored as a coin that can be sold and pays no tax, then left out of the table, which a buyer at 2 s cannot
    do: the security read comes 900 s after the start. Six known coins at +10 percent and six such coins that crashed
    to -90 percent showed +0.10 where a sniper got -0.40. Such a coin stays in the table at its price, each row counts
    it (security_unknown), and the same coins alone are scored beside the table."""
    rose = [flat(1.0), flat(1.1, 10), flat(1.1, 499)]
    crashed = [flat(1.0), (S + 10, 1.0, 1.0, 0.1, 0.1, 1.0), flat(0.1, 499)]
    refusal = {"error": "gmgn GET /v1/token/security: HTTP 404 NOT_FOUND: no such token"}
    fieldless = {"address": "0x" + "ee" * 20, "renounced_mint": 1, "top_10_holder_rate": 0.4}  # none of those read
    unknown = (refusal, {}, fieldless) * 10
    known, notknown = (both_halves(15, tag) for tag in (1, 2))  # fifteen of each in each half
    lines = [coin(m, rose) for m in known] + [coin(m, crashed, u) for m, u in zip(notknown, unknown, strict=True)]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    row = pair(out, 2, 30)
    assert (row["coins"], row["security_unknown"], row["mean"]) == (60, 30, pytest.approx(-0.4))
    apart = out["security_unknown"]
    assert (apart["coins"], pair(apart, 2, 30)["coins"], pair(apart, 2, 30)["mean"]) == (30, 30, pytest.approx(-0.9))
    assert (out["security"]["refused"], out["security"]["without_fields"]) == (10, 20)
    assert [p["coins"] for p in out["by_platform"]] == [30]  # the best pair split by launchpad: every coin of half 1


def test_a_sale_at_the_coins_last_trade_is_all_lost_unless_its_pool_still_holds_money(tmp_path):
    """Reviews of 25 Nov: a sale after a coin's last trade was priced at that trade, so a pool its creator emptied (no
    trade, no candle) scored as a win, and as no rug. Then only a sale priced from a candle over 60 s old counted, so
    no 30 s hold ever did: coins pumped to 3.0 by 25 s and emptied showed +2.00 at 2/30, the best pair. A sale priced
    from the coin's last trade in its 500 s is paid by the pool read at settle, and lost when it gave none. Only a later
    sale of the copy's worth shows the pool was there (fourth review): D's and E's sales, which no such sale follows,
    are stale too, paid by their pools at settle as their own proven sales would be. Those sales alone are beside the
    main table."""
    stops = [flat(1.0), flat(3.0, 10)]  # its last trade 10 s in
    lines = [
        coin(A, stops, pool={"liquidity": "0.02"}),  # emptied
        coin(B, stops, pool={"liquidity": "25000"}),  # still there: the last price stands
        coin(C, stops, pool={"error": "gmgn GET /v1/token/pool_info: HTTP 404 NOT_FOUND: no pool"}),  # not known
        coin(D, [flat(1.0), *(flat(1.5, t) for t in range(60, 500, 50)), flat(1.5, 499)]),  # trades to the end
        coin(E, [flat(1.0), flat(3.0, 10), flat(3.0, 200), flat(3.0, 499)]),  # quiet from 10 to 200 s
    ]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    # A's two cents pay 3 x 0.01 / 300.01 of the price, B's 25,000 dollars 3 x 12,500 / 12,800; C's pool is not known
    a, b = 3 * 0.01 / 300.01 - 1, 3 * 12_500 / 12_800 - 1
    early = pair(out, 2, 30)  # out at 32 s: A, B and C at their last trade, 10 s in; D at its first, E at 10 s's
    assert (early["coins"], early["stale_exits"], early["mean"]) == (5, 5, pytest.approx((a + b + 1) / 5, abs=1e-4))
    late = pair(out, 2, 120)  # out at 122 s: E at its trade of 10 s, a buy of a dollar after it at 200 s
    assert (late["coins"], late["stale_exits"], late["mean"]) == (5, 5, pytest.approx((a + b + 1.5) / 5, abs=1e-4))
    assert late["rug_share"] == pytest.approx(1 / 5)  # A's pool was emptied: a rug
    stale = pair({"table": out["stale_sales"]}, 2, 120)  # every one alone: C's was lost, its pool unknown
    assert (stale["coins"], stale["mean"], stale["lost"]) == (5, pytest.approx((a + b + 1.5) / 5, abs=1e-4), 1)
    assert out["pools"] == {"coins_with_a_stale_exit": 5, "there": 3, "gone": 1, "unknown": 1}
    # gone is less than the copy's own size: 25,000 dollars is gone for a copy of 50,000
    assert LW.score(tmp_path, "base", size_usd=50_000, gas_usd=0.0, fee_pct=0.0)["pools"]["gone"] == 2
    # the review's coins: pumped to 3.0 by 25 s, then their pool emptied
    pumped = [flat(1.0), flat(1.5, 5), flat(2.5, 15), flat(3.0, 25)]
    write_scored(tmp_path, [coin(m, pumped, pool={"liquidity": "0"}) for m in MANY[:6]])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert [pair(out, d, 30)["mean"] for d in LW.DELAYS] == [-1.0] * 4 and pair(out, 2, 30)["rug_share"] == 1.0


def test_the_stale_sales_are_shown_alone_not_a_table_of_the_coins_that_lived(tmp_path):
    """Third review of 25 Nov: table_without_stale_exits left out every copy sold after its coin's last trade, the
    copies of the coins whose pool was pulled: a table of the coins that lived, which read +10 percent and no rug where
    the buyers got -45 percent and half their coins rugged. The stale sales are shown alone instead: how many, their
    mean, and how many were lost."""
    pulled = [coin(m, [flat(1.0), flat(1.5, 10)], pool={"liquidity": "0"}) for m in MANY[:5]]
    lived = [coin(m, to_the_end([flat(1.0), flat(1.1, 10)])) for m in MANY[5:10]]
    write_scored(tmp_path, pulled + lived)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    row, stale = pair(out, 2, 30), pair({"table": out["stale_sales"]}, 2, 30)
    assert (row["coins"], row["mean"], row["rug_share"], row["stale_exits"]) == (10, -0.45, 0.5, 5)
    assert {k: stale[k] for k in ("coins", "mean", "lost")} == {"coins": 5, "mean": -1.0, "lost": 5}
    assert "table_without_stale_exits" not in out and "stale_sales" in out["reading"]


def test_a_trade_into_an_emptied_pool_shows_no_pool(tmp_path):
    """Third review of 25 Nov: a later trade made every sale before it fresh, even a two-cent sale into an emptied pool
    at a hundred-thousandth of the price, and a one-cent spike in it read +299,900 percent. Only a later sale of at
    least the copy's worth shows the pool (fourth review): the creator's sale of 40,000 dollars into it says the pool
    was there until then."""

    def score_of(candles, liquidity):
        write_scored(tmp_path, [coin(A, candles, pool={"liquidity": liquidity})])
        out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
        assert "only when it is a sale (its close below the sale's price)" in out["reading"]
        return [pair(out, 2, h)["mean"] for h in (30, 120, 300)]

    pumped = [flat(1.0), flat(3.0, 10)]
    pulled = [*pumped, (S + 300, 3.0, 3.0, 1e-5, 1e-5, 0.02)]  # two cents at a hundred-thousandth, at 300 s
    spiked = [*pumped, (S + 120, 3.0, 3000.0, 3.0, 3000.0, 0.01), (S + 300, 3000.0, 3000.0, 1e-5, 1e-5, 0.02)]
    sold_off = [*pumped, (S + 300, 3.0, 3.0, 1e-3, 1e-3, 40_000.0)]  # the creator's sale: 40,000 dollars
    assert score_of(pulled, "0.01") == pytest.approx([-1.0, -1.0, -1.0], abs=1e-4)  # a cent pays nothing
    assert score_of(spiked, "0.01") == pytest.approx([-1.0, -1.0, -1.0], abs=1e-4)
    assert score_of(sold_off, "5") == [2.0, 2.0, pytest.approx(-0.999)]  # sold before it; the last sale at 1e-3


def test_only_a_later_sale_of_the_copys_worth_shows_the_pool(tmp_path):
    """Fourth review of 25 Nov: a later trade showed the pool by its dollars or by trading near the sale's price, but
    a buy puts cash in: a buy of 1,000 dollars into an emptied pool read +200 percent where all was lost, and one more
    cent's trade near a spike's price kept the spike's price. A later trade shows the pool only when it is a sale (its
    close below the sale's) of at least the copy's worth in dollars, near the price or not. Any other sale is paid by
    the pool read at settle, and never more than the same sale shown would get: a pool grown by settle pays nothing
    more."""
    pumped = [flat(1.0), flat(3.0, 10)]
    bought = [*pumped, (S + 300, 3.0, 30_000.0, 3.0, 30_000.0, 1000.0)]  # a buy of 1,000 dollars into the emptied pool
    spiked = [*pumped, (S + 120, 3.0, 3000.0, 3.0, 3000.0, 0.01), (S + 130, 3000.0, 3000.0, 2900.0, 2900.0, 0.01)]
    dumped = [*pumped, (S + 300, 3.0, 3.0, 1e-3, 1e-3, 40_000.0)]  # the creator's sale of 40,000 dollars
    for candles, want in ((bought, -1.0), (spiked, -1.0), (dumped, 2.0)):
        write_scored(tmp_path, [coin(A, candles, pool={"liquidity": "0"})])
        out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
        assert [pair(out, 2, h)["mean"] for h in (30, 120)] == pytest.approx([want, want], abs=1e-4)
    # a pool of 3,000 dollars when first seen: q 1,500, worth 3 x 100 x 1,500 / 1,600 at the sale's price of 3.0;
    # 25,000 dollars read at settle pay a sale no later sale shows no more than a pool grown from 1,500 to 1,500 x √3
    q, kept = 1500.0, 1500.0 / 1600.0
    side, worth = q * 3**0.5, 300.0 * kept
    shown = 3 * kept * side / (side + worth) - 1
    for candles in (dumped, pumped):  # shown by the creator's sale; no trade after 10 s
        write_scored(tmp_path, [coin(A, candles, liquidity=3000.0, pool={"liquidity": "25000"})])
        out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
        assert pair(out, 2, 30)["mean"] == pytest.approx(shown, abs=1e-4)


def test_a_stale_sale_is_paid_by_the_pool_read_at_settle_as_a_constant_product_pool_pays(tmp_path):
    """Reviews of 25 Nov: a stale sale kept its price when the pool held the copy's size, whatever the copy was worth
    at that price (120 dollars sold a copy worth 300); then it kept its whole price once the pool held that worth, and
    lost all of it a cent below. Selling a worth V into a pool of L dollars, half of them on each side (qs), pays
    qs x V / (qs + V): the reviewer's copy of 100 worth 300 gets nothing more at 299.9 or 300 dollars, half its worth at
    600, and 98 percent of it at 25,000. It is lost only when the read gave no liquidity, or none. (The coin's own pool
    counts as there from the copy's size up, not as a rug.)"""
    stops = [flat(1.0), flat(3.0, 10)]  # its last trade 10 s in, at three times the entry
    for liquidity, ret, there in (("120", -0.5, 1), ("299.9", -0.0002, 1), ("300", 0.0, 1), ("600", 0.5, 1),
                                  ("3000", 1.5, 1), ("25000", 1.9297, 1), ("50", -0.7692, 0), ("0", -1.0, 0)):  # fmt: skip
        write_scored(tmp_path, [coin(A, stops, pool={"liquidity": liquidity})])
        out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
        assert (pair(out, 2, 120)["mean"], out["pools"]["there"]) == (pytest.approx(ret, abs=1e-4), there), liquidity
    assert "side / (side + worth)" in out["reading"]
    refusal = {"error": "gmgn GET /v1/token/pool_info: HTTP 404 NOT_FOUND: no pool"}
    write_scored(tmp_path, [coin(A, stops, pool=refusal)])
    assert pair(LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0), 2, 120)["mean"] == -1.0  # not known: lost


def test_a_field_is_given_only_when_it_reads_as_a_tax_or_as_a_yes_or_no(tmp_path):
    """Second review of 25 Nov: a field of -1 counted as given, so an answer of -1 everywhere read as known, sellable
    and untaxed. A tax is given when it reads as a number of 0 or more; a flag when it reads as 0 or 1 (true, false,
    yes, no)."""
    up = [flat(1.0), flat(2.0, 10)]
    minus = dict.fromkeys(("is_honeypot", "honeypot", "can_not_sell", "can_sell", "buy_tax", "sell_tax"), -1)
    some = {"is_honeypot": 2, "honeypot": "no", "can_not_sell": False, "buy_tax": "x", "sell_tax": "0"}
    write_scored(tmp_path, [coin(A, up, minus), coin(B, up, some)])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert (out["security"]["without_fields"], out["security_unknown"]["coins"]) == (1, 1)  # A gave nothing
    assert out["security"]["fields"] == {
        "is_honeypot": {"given": 0, "yes": 0},
        "honeypot": {"given": 1, "yes": 0},
        "can_not_sell": {"given": 1, "yes": 0},
        "can_sell": {"given": 0, "yes": 0},
        "buy_tax": {"given": 0, "above_0": 0},
        "sell_tax": {"given": 1, "above_0": 0},
    }


def test_the_report_says_in_plain_words_what_it_does_not_count(tmp_path):
    """Reviews of 25 Nov: what the score leaves out, printed with every report: how its own price impact is counted,
    each pool's own fee (some Base launchpads take more than 1 percent at launch), the taxes at the moment of an
    entry, every answer of a chain no real answer has been read of, an EVM contract that taxes or blocks only the
    buyers of its first blocks (the security read comes 900 s later), and an EVM sale's approval, a third gas charge."""
    write_scored(tmp_path, [coin(A, to_the_end([flat(1.0), flat(2.0, 10)]))])
    caveats = " ".join(LW.score(tmp_path, "base")["caveats"])
    for words in ("price impact is counted", "one flat number per chain", "first sees the coin",
                  "usually after the entries", "Base, BNB Chain or Ethereum", "unchecked until the first real run",
                  "pool", "above the flat 1 percent", "only the buyers of its first blocks", "900 s after the start",
                  "approval", "a third gas charge", "its last trade's price", "pessimistic bound beside each number",
                  "cut part of its pool", "cannot be known from the candles"):  # fmt: skip
        assert words in caveats, words


def test_a_copy_pays_its_own_price_impact_in_a_constant_product_pool(tmp_path):
    """Third review of 25 Nov: a copy's size entered the score only through its gas, so a bigger --size-usd made every
    row better without limit, even a copy ten times its coin's pool. Each leg now pays its own price impact in a
    constant-product pool holding the liquidity GMGN gave when the watch first saw the coin, half of it on each side
    (q): the buy gets q / (q + size) of its price, the sale qx / (qx + worth), qx = q x sqrt(exit / entry) the side at
    the exit and worth what the copy holds at that price. The reviewer's twenty Ethereum coins, 3,000 dollars each, from
    1.0 to 1.2: +16 percent at any size before, -3 to -97 percent now. A row counts its copies above 5 percent of their
    pool."""
    coins = [coin(m, to_the_end([flat(1.0), flat(1.2, 20)]), liquidity=3000.0) for m in MANY[:20]]
    write_scored(tmp_path, coins, chain="eth")
    for size, want in ((100, -0.0280), (1000, -0.5153), (3000, -0.7754), (30_000, -0.9728)):
        row = pair(LW.score(tmp_path, "eth", size_usd=size, fee_pct=1.0), 2, 30)
        assert row["mean"] == pytest.approx(want, abs=1e-4), size
        assert row["over_5pct_of_pool"] == (0 if size <= 150 else 20), size
    out = LW.score(tmp_path, "eth")
    assert "constant-product" in out["reading"] and "constant-product" in " ".join(out["caveats"])


def test_the_pool_seen_after_the_entry_is_taken_back_to_the_entrys_price(tmp_path):
    """Fourth review of 25 Nov: the pool's size is GMGN's liquidity when the watch first saw the coin, up to a look
    after its launch, but it was charged as the pool at the entry: a winner that rose between its entry and its sighting
    paid too little impact, 2/30 reading +610.5 percent where the same pool taken back to the entry gives +400.0. In
    the score's own constant-product pool the cash side at a price P is the side seen times the square root of P over
    the price on the same row (price_usd)."""
    candles = [flat(1.0), flat(1.0, 2), *(flat(9.0, t) for t in range(30, 500, 10))]  # x9 by 30 s; seen at 9.0
    write_scored(tmp_path, [coin(A, candles, liquidity=30_000.0, first={"price_usd": 9.0})])
    out = LW.score(tmp_path, "base", size_usd=1000.0, gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["mean"] == pytest.approx(4.0, abs=1e-4)  # q 15,000 x √(1 / 9) = 5,000 at the entry
    assert "square root of the price" in " ".join(out["caveats"])
    # a row that gave no price cannot be taken back to the entry: the coin has no pool size there
    write_scored(tmp_path, [coin(A, candles, liquidity=30_000.0, first={"price_usd": None})])
    out = LW.score(tmp_path, "base", size_usd=1000.0, gas_usd=0.0, fee_pct=0.0)
    assert (pair(out, 2, 30)["coins"], out["without_pool_size"]["coins"]) == (0, 1)


def test_a_pools_cash_side_is_at_most_its_coins_market_cap(tmp_path):
    """Fourth review of 25 Nov: GMGN's real rows of new coins give a liquidity of up to eleven times the coin's market
    cap (tests/fixtures/gmgn/real/trenches.json: Ryfa, 34,835 dollars against 3,095). A constant-product pool's cash
    side is worth no more than every coin there is, about the market cap, and the score took half the liquidity: a copy
    of 1,000 dollars read +75.7 percent where at most +15.8. The side is capped at the market cap on the same row, and
    the coins so capped are counted."""
    real = fixture("real/trenches.json")["data"]["new_creation"][0]
    assert (real["liquidity"], real["market_cap"]) == (pytest.approx(34_835.42, abs=0.01), 3095.38)
    up = to_the_end([flat(1.0), flat(2.0, 10)])
    line = coin(A, up, liquidity=34_835.42, first={"market_cap_usd": 3095.38})
    write_scored(tmp_path, [line, coin(B, up, liquidity=3000.0, first={"market_cap_usd": 1e9})], chain="sol")
    out = LW.score(tmp_path, "sol", size_usd=1000.0, gas_usd=0.0, fee_pct=0.0)
    q, b = 3095.38, 1500.0  # A's side capped; B's half of its liquidity, far under its market cap
    capped, uncapped = [2 * x / (x + 1000) * x * 2**0.5 / (x * 2**0.5 + 2000 * x / (x + 1000)) - 1 for x in (q, b)]
    assert pair(out, 2, 30)["mean"] == pytest.approx((capped + uncapped) / 2, abs=1e-4)
    assert out["pool_sizes"]["capped_at_market_cap"] == 1 and "market cap" in out["reading"]


def test_a_coin_without_a_pool_size_stays_in_the_table_at_a_small_pool(tmp_path):
    """Third review of 25 Nov: a coin whose line gives no liquidity that reads as dollars (missing, null, zero, not a
    number, below zero, too big for a float) was scored at no impact; then it was left out of the table, the best pair,
    the launchpads and the delays. Fourth review: those are the coins whose pool was emptied before the watch first saw
    them, so the table was one of the coins that lived: 60 rising coins and 30 rugged ones read +20 percent and no rug
    where every coin a buyer at 2 s bought made -20. Such a coin stays in the table, counted in each row
    (pool_size_unknown), its impact charged at a pool a tenth of the chain's coins were smaller than when first seen;
    the same coins are scored alone beside it."""
    good, rugged = both_halves(30, 1), both_halves(15, 2)
    lines = [coin(m, to_the_end([flat(1.0), flat(1.2, 10)])) for m in good]
    lines += [coin(m, [flat(1.0), flat(3.0, 10)], liquidity=0, pool={"liquidity": "0"}) for m in rugged]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    row = pair(out, 2, 30)
    assert (row["coins"], row["pool_size_unknown"], row["mean"]) == (90, 30, pytest.approx(-0.2, abs=1e-4))
    assert row["rug_share"] == pytest.approx(1 / 3, abs=1e-4) and out["best"]["coins"] == 45
    assert {d["with_entry"] for d in out["by_delay"]} == {90}
    apart = out["without_pool_size"]
    assert (apart["coins"], pair(apart, 2, 30)["coins"], pair(apart, 2, 30)["mean"]) == (30, 30, -1.0)
    # the pool charged: the cash side a tenth of the chain's coins were smaller than when first seen
    up = to_the_end([flat(1.0), flat(2.0, 10)])
    sized = [coin(m, up, liquidity=2000.0 * k) for k, m in enumerate(mints(10), 1)]  # sides of 1,000 to 10,000
    missing = {k: v for k, v in coin(B, up).items() if k != "liquidity_usd"}
    unsized = [coin(m, up, liquidity=v) for m, v in zip((C, D, E, F, G), (None, 0, "x", -5, 10**400), strict=True)]
    write_scored(tmp_path, [*sized, missing, *unsized])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert out["pool_sizes"]["fallback_cash_side"] == 1000.0 and out["pool_sizes"]["without_one"] == 6
    kept = 1000.0 / 1100.0
    at_1000 = 2 * kept * 1000 * 2**0.5 / (1000 * 2**0.5 + 200 * kept) - 1  # the coin of 2,000 dollars pays as much
    apart = pair(out["without_pool_size"], 2, 30)
    assert pair(out, 2, 30)["coins"] == 16 and apart["mean"] == pytest.approx(at_1000, abs=1e-4)
    assert "price impact" in out["without_pool_size"]["why"]
    # a chain where no coin gave a pool size has no pool to charge them at: they are scored apart only
    write_scored(tmp_path, [missing, *unsized])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert (pair(out, 2, 30)["coins"], out["pool_sizes"]["fallback_cash_side"], out["without_pool_size"]["coins"]) == (
        0,
        None,
        6,
    )


def test_a_liquidity_too_small_to_hold_a_cent_is_no_pool_size(tmp_path):
    """Fourth review of 25 Nov: a liquidity of 1e-320 dollars (a number read as finite and above 0) and a price that
    fell far after the entry gave a pool side and a worth that both round to 0: ZeroDivisionError, and every later
    score of the chain failed on that line. A liquidity under a millionth of a dollar is no pool size: such a coin is
    charged at the chain's fallback pool, as a coin that gave none."""
    lines = [coin(m, to_the_end([flat(1.0), flat(2.0, 10)]), liquidity=3000.0) for m in mints(10)]
    tiny = ((1e-320, 1e-9), (1e-300, 1e-300), (1e-12, 1e-30), (5e-7, 1e-9))
    lines += [coin(m, to_the_end([flat(1.0), flat(low, 10)]), liquidity=x) for m, (x, low) in zip(mints(4, 100), tiny)]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert (out["ok"], out["pool_sizes"]["without_one"], out["pool_sizes"]["fallback_cash_side"]) == (True, 4, 1500.0)
    assert pair(out["without_pool_size"], 2, 30)["mean"] == pytest.approx(-1.0, abs=1e-4)


def test_each_table_apart_counts_the_coins_it_holds(tmp_path):
    """Fourth review of 25 Nov: security_unknown.coins counted every coin whose security was unknown, 3 where its table
    held 1: a coin timed from a sighting, or whose candles GMGN refused, is scored in no row of it. pools counted the
    coins of the tables apart too. Each count is of the coins its table holds; pools, of the table's."""
    up, stops = to_the_end([flat(1.0), flat(2.0, 10)]), [flat(1.0), flat(3.0, 10)]  # stops: every sale stale
    refusal = {"error": "gmgn GET /v1/token/security: HTTP 404 NOT_FOUND: no such token"}
    sighted, cut = {"start_from": "sighting"}, {"candles": [], "candles_error": "gmgn GET kline: HTTP 404 NOT_FOUND"}
    lines = [
        coin(A, up, refusal),  # in the table, its security unknown
        coin(B, up, refusal) | sighted,  # timed from a sighting
        coin(C, up, refusal) | cut,  # its candles refused: scored nowhere
        coin(D, stops) | sighted,  # timed from a sighting, every sale stale
        coin(E, stops),  # in the table, every sale stale
        coin(F, up) | sighted | cut,
    ]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    unknown, apart = out["security_unknown"], out["timed_from_sighting"]
    assert (unknown["coins"], pair(unknown, 2, 30)["coins"]) == (1, 1)
    assert (apart["coins"], pair(apart, 2, 30)["coins"]) == (2, 2)
    assert out["pools"]["coins_with_a_stale_exit"] == 1  # E's; D's in the table apart
    assert (out["security"]["refused"], out["candles"]["refused"]) == (3, 2)  # the reads themselves, every coin's


def test_a_coin_timed_from_its_first_sighting_is_scored_apart(tmp_path):
    """Third review of 25 Nov: a coin with no usable open or creation time was timed from when the watch first saw it,
    up to a look after its launch, and nothing said so: its first seconds were never read, and a "2 s" entry priced a
    moment a minute in. Such a coin is left out of the table, the best pair and the delays, counted, and scored apart."""
    up = to_the_end([flat(1.0), flat(2.0, 10)])
    sighted = coin(B, up) | {"created_ts": 0, "start_from": "sighting"}
    unsaid = {k: v for k, v in coin(C, up).items() if k != "created_ts"}  # an older line: its start read as written
    write_scored(tmp_path, [coin(A, up), sighted, unsaid])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["coins"] == 1 and {d["with_entry"] for d in out["by_delay"]} == {1}
    apart = out["timed_from_sighting"]
    assert (apart["coins"], pair(apart, 2, 30)["coins"], pair(apart, 2, 30)["mean"]) == (2, 2, pytest.approx(1.0))


def test_a_number_too_big_for_a_float_reads_as_nothing_and_the_score_goes_on(tmp_path):
    """Third review of 25 Nov: an integer too big for a float (a 401-digit tax or liquidity, written as GMGN sent it)
    raised OverflowError in launch-score, and every later score of the chain failed on it."""
    big = 10**400
    up = to_the_end([flat(1.0), flat(2.0, 10)])
    write_scored(tmp_path, [coin(A, up, dict(NO_TAX, buy_tax=big)), coin(B, up, first={"sell_tax": big}),
                            coin(C, [flat(1.0), flat(2.0, 10)], pool={"liquidity": big}),
                            coin(D, [*up, (S + 20, big, big, big, big, big)])])  # fmt: skip
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert (out["ok"], out["coins"], out["candles"]["bad_prices_skipped"], out["pools"]["unknown"]) == (True, 4, 1, 1)


def test_a_return_too_big_for_a_float_leaves_its_standard_error_unsaid_and_the_score_goes_on(tmp_path):
    """A price of 1e300 after one of 1e-300 is a return no float holds: the standard error, which a number that is not
    finite stops, is left unsaid, and the score goes on (the standard deviation raised, and ended the score)."""
    lines = [coin(A, to_the_end([flat(1e-300), flat(1e300, 10)])), coin(B, to_the_end([flat(1.0), flat(2.0, 10)]))]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    # since the fourth review the copy is left out (candles.returns_left_out): one copy, no spread to read
    assert (out["ok"], pair(out, 2, 30)["coins"], pair(out, 2, 30)["mean_se"]) == (True, 1, None)


def test_a_return_no_float_holds_is_left_out_and_counted(tmp_path):
    """Fourth review of 25 Nov: a price of 1e300 after one of 1e-300 gives a return no float holds (with a price
    impact, inf over inf: nan). It reached the means, chose the best pair on half 0, and the printed report held NaN,
    which is not JSON. Every copy whose return, or its bound, is not a finite number is left out, and counted."""
    shape = to_the_end([flat(1.0), flat(0.5, 10), flat(1.5, 100), flat(1.4, 250)])  # 2/30 loses half; 2/120 +50
    lines = [coin(m, shape) for m in both_halves(30)]
    write_scored(tmp_path, lines)
    clean = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    overflow = next(m for m in mints(100, 10**6) if LW.half_of(m) == 0)  # in the half the best pair is chosen on
    write_scored(tmp_path, [coin(overflow, to_the_end([flat(1e-300), flat(1e300, 10)])), *lines])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    json.loads(json.dumps(out), parse_constant=lambda word: pytest.fail(f"not JSON: {word}"))
    assert out["best"] == clean["best"] and out["table"] == clean["table"]
    assert (pair(out, 2, 30)["coins"], pair(out, 2, 30)["mean"]) == (60, -0.5)
    assert out["candles"]["returns_left_out"] == 15  # every copy of it: its pool's side at 12 and 30 s overflows too


def test_a_hold_that_ends_after_the_candles_were_read_is_not_scored(tmp_path):
    write_scored(tmp_path, [coin(A, [flat(1.0), flat(2.0, 10)], fetched_at=S + 100)])  # read 100 s after its start
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["coins"] == 1  # over by start + 32
    assert (pair(out, 2, 120)["coins"], pair(out, 2, 120)["unsettled"]) == (0, 1)  # start + 122: not yet when read


def test_a_broken_line_and_a_coin_written_twice_are_counted_not_scored(tmp_path):
    line = coin(A, [flat(1.0), flat(2.0, 10)])
    write_scored(tmp_path, [line, line], extra='{"mint": "0xcut", "candles": [[1, 2\n')
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert (out["coins"], out["duplicates"], out["unreadable_lines"]) == (1, 1, 1)
    assert pair(out, 2, 30)["coins"] == 1


# -- the command line ---------------------------------------------------------------------------------------------------


@pytest.fixture
def snipetech(tmp_path, monkeypatch, capsys):
    """Runs `snipetech gmgn ...` against the stand-in, the watch on a fake clock: (exit code, what it printed, the
    requests it made)."""
    monkeypatch.setenv("GMGN_API_KEY", KEY)
    conf = tmp_path / "s.toml"
    conf.write_text("[gmgn]\nweight_per_sec = 50.0\n")
    db = tmp_path / "t.db"
    routes: dict = {}
    monkeypatch.setattr(LW, "time", FakeTime(T))

    def run(*argv):
        seen: list = []
        monkeypatch.setattr(gmgn, "default_transport", lambda: fake_gmgn(seen, routes))
        code = cli.main(["--config", str(conf), "--db", str(db), "gmgn", *argv])
        text = capsys.readouterr().out
        assert KEY not in text
        return code, json.loads(text), seen

    run.routes = routes
    return run


def test_launch_watch_runs_from_the_command_line_on_the_chain_asked(snipetech, tmp_path):
    snipetech.routes.update(
        {
            "/v1/trenches": polls([trench(A, T - 2000)]),
            "/v1/market/token_kline": kline({A: [(T - 2000, 1.0, 1.0, 1.0, 1.0, 5.0)]}),
            "/v1/token/security": security({}),
        }
    )
    argv = ("launch-watch", "--chain", "base", "--minutes", "2", "--every", "30", "--settle-secs", "600", "--json")
    code, out, seen = snipetech(*argv)
    assert code == 0 and out["ok"] is True and out["chain"] == "base"
    assert (out["polls"], out["recorded"], out["scored"]) == (4, 1, 1)
    assert (out["minutes"], out["every_secs"], out["settle_secs"]) == (2.0, 30.0, 600.0)
    folder = tmp_path.resolve() / "history" / "gmgn_launches"  # beside the database
    assert out["files"] == {"launches": str(folder / "base.jsonl"), "scored": str(folder / "base_scored.jsonl")}
    assert [s["mint"] for s in scored(tmp_path)] == [A]
    assert {r.url.params.get("chain") for r in seen} == {"base"}


def test_launch_watch_writes_its_requests_weight_once_a_status_line_not_once_a_request(snipetech, monkeypatch):
    """Third review of 25 Nov: every answered request wrote its weight into the shared database at once, so with
    another process holding the database's write lock each read waited for it (up to 600 s a write), outside every time
    limit of the watch. The watch keeps the count and writes it at each status line (every 15 minutes) and at its
    end."""
    written: list[int] = []
    monkeypatch.setattr(gmgn, "mark_answered", lambda store, weight, now=None: written.append(weight))
    snipetech.routes.update({"/v1/trenches": polls([trench(A, T - 2000)]), "/v1/market/token_kline": kline({}),
                             "/v1/token/security": security({})})  # fmt: skip
    code, out, seen = snipetech("launch-watch", "--chain", "base", "--minutes", "20", "--json")
    assert code == 0 and out["polls"] == 20
    assert len(written) == 2 and sum(written) == sum(gmgn.READ_PATHS[r.url.path] for r in seen)  # 15 min, the end


def test_launch_watch_wants_its_minutes_and_sane_numbers(snipetech):
    # third review of 25 Nov: no ceiling let 1e307 minutes through, an end at infinity: a week at most now
    for argv in ((), ("--minutes", "-1"), ("--minutes", "nan"), ("--minutes", "inf"), ("--minutes", "1e307"),
                 ("--minutes", str(LW.MAX_MINUTES + 1)), ("--minutes", "1", "--every", "0"),
                 ("--minutes", "1", "--settle-secs", "-5")):  # fmt: skip
        code, out, seen = snipetech("launch-watch", "--chain", "base", *argv, "--json")
        assert code == 2 and out["ok"] is False and not seen, argv
    _, out, _ = snipetech("launch-watch", "--chain", "base", "--json")
    assert "--minutes" in out["error"]
    _, out, _ = snipetech("launch-watch", "--chain", "base", "--minutes", "1e307", "--json")
    assert "7 days" in out["error"]


def test_a_wrong_number_is_exit_2_with_no_key_set_too(snipetech, monkeypatch):
    """Third review of 25 Nov: the client was made before the numbers were checked, so with no key set a wrong number
    ended with the missing key's exit 1."""
    monkeypatch.delenv("GMGN_API_KEY")
    wrong = ((), ("--minutes", "-1"), ("--minutes", "1", "--every", "0"), ("--minutes", "1", "--settle-secs", "-5"))
    for argv in wrong:
        code, out, seen = snipetech("launch-watch", "--chain", "base", *argv, "--json")
        assert code == 2 and "GMGN_API_KEY" not in out["error"] and not seen, argv
    code, out, _ = snipetech("launch-watch", "--chain", "base", "--minutes", "1", "--json")
    assert code == 1 and "GMGN_API_KEY" in out["error"]  # good numbers and no key: the key's own refusal


def test_the_size_help_says_a_copy_pays_its_own_price_impact(capsys):
    """Third review of 25 Nov: --size-usd said only a copy's gas was counted against its size; its own price impact in
    its coin's pool is too."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["gmgn", "--help"])
    said = " ".join(capsys.readouterr().out.split())
    assert "its own price impact in the coin's pool, are counted against them" in said


def test_launch_score_reads_the_watched_file_and_needs_no_key(snipetech, tmp_path, monkeypatch):
    write_scored(tmp_path, [coin(A, to_the_end([flat(1.0), flat(2.0, 10)]))])
    monkeypatch.delenv("GMGN_API_KEY")
    argv = ("launch-score", "--chain", "base", "--size-usd", "50", "--gas-usd", "1", "--fee-pct", "0.5", "--json")
    code, out, seen = snipetech(*argv)
    assert code == 0 and out["ok"] is True and not seen
    assert (out["chain"], out["size_usd"], out["gas_usd"], out["fee_pct"]) == ("base", 50.0, 1.0, 0.5)
    assert pair(out, 2, 30)["mean"] == pytest.approx(2.0 / 1.0 * 0.995**2 - 1 - 2 * 1.0 / 50, abs=1e-4)
    code, out, _ = snipetech("launch-score", "--chain", "eth", "--json")  # nothing watched on Ethereum yet
    assert code == 1 and out["ok"] is False and "launch-watch" in out["error"]
    code, out, _ = snipetech("launch-score", "--chain", "base", "--size-usd", "0", "--json")
    assert code == 2 and out["ok"] is False
