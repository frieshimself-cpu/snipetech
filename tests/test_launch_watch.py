"""`snipetech gmgn launch-watch` and `launch-score` (analysis/launch_watch.py): a day of real launches on Base,
Ethereum, BNB Chain or Solana from GMGN's newest coins, each recorded once, its 1 s candles and security read once it
has settled, then what an early buyer would have made at each entry delay and hold, rugs, honeypots and taxes counted.

The watch runs on a fake clock (sleeping moves it; nothing waits) against the stand-in of tests/gmgn_fake.py. The coins
are made up; their rows have the fields of a real trenches row (tests/fixtures/gmgn/real/trenches.json) and their
security the fields of a real answer (tests/fixtures/gmgn/real/token_security.json)."""

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
CREATOR = "0x" + "77" * 20
WETH = "0x4200000000000000000000000000000000000006"
REAL_SECURITY = fixture("real/token_security.json")["data"]
NO_TAX = {"is_honeypot": None, "honeypot": 0, "can_sell": 0, "can_not_sell": 0, "buy_tax": "0", "sell_tax": "0"}


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
    assert a["candles"] == [[T - 1000, 1.0, 1.2, 0.9, 1.1, 50.0], [T - 999, 1.1, 1.5, 1.1, 1.4, 20.5]]
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


def test_a_coin_that_stopped_trading_has_its_pool_read_once_when_it_settles(tmp_path):
    """Review of 25 Nov: a sale after a coin's last trade is priced at that trade, and a pool its creator emptied
    leaves no trade and no candle. A coin that a copy would sell more than 60 s after its last trade has its pool read
    once, when it settles; every line keeps the coin's last candle time."""
    s = T - 1000
    bars = {
        A: [(s, 1.0, 1.0, 1.0, 1.0, 5.0), (s + 10, 3.0, 3.0, 3.0, 3.0, 5.0)],  # its last trade 10 s in
        B: [(s + k, 1.0, 1.0, 1.0, 1.0, 5.0) for k in range(0, 500, 40)],  # a trade every 40 s, to the end
        C: [(s, 1.0, 1.0, 1.0, 1.0, 5.0)],  # its only trade at its start, and GMGN has no pool of it
    }
    gone = dict(HEALTHY_POOL, liquidity="0.02", base_reserve="0", quote_reserve="0")
    routes = {
        "/v1/trenches": polls([trench(m, s) for m in (A, B, C)]),
        "/v1/market/token_kline": kline(bars),
        "/v1/token/security": security({}),
        "/v1/token/pool_info": pools({A: gone, C: refused(404, "NOT_FOUND", "no pool")}),
    }
    seen: list = []
    out = watch(tmp_path, FakeTime(T), routes, seen, minutes=3)
    assert [r.url.params["address"] for r in asked(seen, "/v1/token/pool_info")] == [A, C]  # once each; B never
    by = {line["mint"]: line for line in scored(tmp_path)}
    assert (by[A]["last_candle_ts"], by[A]["pool"]) == (s + 10, gone)
    assert (by[B]["last_candle_ts"], "pool" in by[B]) == (s + 480, False)
    assert by[C]["last_candle_ts"] == s and "HTTP 404" in by[C]["pool"]["error"]  # written as it came, asked once
    assert (out["pools_read"], out["refused"]["pool"], out["scored"]) == (2, 1, 3)


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


def test_a_404_or_a_403_is_asked_again_in_two_later_rounds_before_it_is_written_as_refused(tmp_path):
    """Review of 25 Nov: GMGN's 404 for a coin it has not read yet, and a 403 (a plan's refusal, or a request that left
    over the wrong route), were written as refused at once, and such a coin was then scored as safe. They are asked
    again two rounds later, as a server error is."""
    ft = FakeTime(T)
    asks: dict = {"candles": [], "security": []}

    def candles(req):
        asks["candles"].append((req.url.params["address"], ft.now))
        if req.url.params["address"] == B:
            return refused(403, "PLAN_UPGRADE_REQUIRED", "1s candles need the Pro plan")
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
    assert [t for m, t in asks["candles"] if m == B] == rounds and "PLAN_UPGRADE_REQUIRED" in by[B]["candles_error"]
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
    assert [s["fetched_at"] for s in scored(tmp_path)] == [T, T + 20, T + 40, T + 60, T + 80] and out["scored"] == 5


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
    # A at T, B at T + 25, C at T + 50; at T + 75 the minute after the end is over: D and E are the next job's
    assert [s["mint"] for s in scored(tmp_path)] == [A, B, C] and (out["scored"], out["waiting"]) == (3, 2)
    assert ft.now == T + 75


def test_a_refused_key_ends_the_job_and_leaves_the_coin_for_the_next_one(tmp_path):
    ft = FakeTime(T)
    routes = {
        "/v1/trenches": polls([trench(A, T - 1000)]),
        "/v1/market/token_kline": lambda req: refused(401, "AUTH_KEY_INVALID", f"api key {KEY} not found"),
    }
    out = watch(tmp_path, ft, routes, minutes=5)
    assert out["ok"] is False and "AUTH_KEY_INVALID" in out["error"] and KEY not in json.dumps(out)
    assert ft.now == T and out["polls"] == 1  # it ended at once instead of waiting out its five minutes
    assert scored(tmp_path) == []  # no refusal written: no read could have worked with that key
    routes["/v1/market/token_kline"] = kline({})
    out2 = watch(tmp_path, FakeTime(T + 100), routes, minutes=0)
    assert out2["resumed"] == {"seen": 1, "waiting": 1} and out2["scored"] == 1


def test_a_failed_connection_leaves_the_coin_for_a_later_round_and_three_failed_rounds_write_it(tmp_path):
    asks: list = []

    def candles(req):
        mint = req.url.params["address"]
        asks.append(mint)
        if mint == B:
            raise httpx.ConnectError("connection refused", request=req)
        if asks.count(A) <= 3:  # the client asks three times on a server error before it gives up on a read
            return httpx.Response(503, text="upstream unavailable")
        return answer({"list": [gmgn_candle(T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0)]})

    routes = {
        "/v1/trenches": polls([trench(A, T - 1000), trench(B, T - 1000)]),
        "/v1/market/token_kline": candles,
        "/v1/token/security": security({}),
    }
    out = watch(tmp_path, FakeTime(T), routes, minutes=5)
    by = {s["mint"]: s for s in scored(tmp_path)}
    assert by[A]["candles"] == [[T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0]] and by[A]["fetched_at"] == T + 60  # 2nd round
    assert by[B]["candles"] == [] and "network" in by[B]["candles_error"] and by[B]["fetched_at"] == T + 120  # 3rd
    assert by[B]["security"] == REAL_SECURITY
    assert out["tried_again_later"] == 3 and out["refused"]["candles"] == 1 and out["scored"] == 2


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
    assert len(scored_lines) == 3 and json.loads(scored_lines[2])["candles"] == [[T - 1000, 1.0, 1.0, 1.0, 1.0, 5.0]]
    assert LW.score(tmp_path, "base")["unreadable_lines"] == 1
    seen = []  # the next job reads A and C as done and B as waiting, and asks for nothing yet
    out = watch(tmp_path, FakeTime(T + 300), routes, seen, minutes=0)
    assert out["resumed"] == {"seen": 3, "waiting": 1} and seen == []


# -- the score: what an early buyer would have made ---------------------------------------------------------------------


def coin(mint, candles, sec=None, *, platform="clanker", fetched_at=None, first=None, pool=None):
    """A line of <chain>_scored.jsonl as the watch writes it: a coin started at S, the taxes and flags of its row when
    first seen (`first`), its candles as [ts, open, high, low, close, dollars], its security as GMGN sent it and, when
    given, its pool as read at settle."""
    return {
        "mint": mint,
        "chain": "base",
        "start": S,
        "platform": platform,
        "symbol": "TKN",
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


def write_scored(tmp_path, coins, chain="base", extra=""):
    folder = tmp_path / "history" / "gmgn_launches"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{chain}_scored.jsonl").write_text("".join(json.dumps(c) + "\n" for c in coins) + extra)


def pair(out, delay, hold):
    """The table's row of one entry delay and hold."""
    return next(r for r in out["table"] if (r["delay"], r["hold"]) == (delay, hold))


def test_a_copy_pays_its_exit_over_its_entry_less_both_taxes_two_swap_fees_and_two_gas_fees(tmp_path):
    candles = [flat(1.0), flat(2.0, 10), flat(1.5, 40), flat(1.5, 100)]
    # in at the close of the last candle at or before start + 1 (1.0), out at or before start + 31 (2.0):
    # 2.0 / 1.0 x 0.99 x 0.99 x 0.95 x 0.90 - 1 - 2 x 0.05 / 100: Base's swap fee of 1 percent on each leg.
    kept = 0.99**2 * 0.95 * 0.9
    # the same taxes said as shares of 1, then as percents: a file is read in its own unit
    for sec, unit in (({"buy_tax": "0.05", "sell_tax": "0.1"}, "share"), ({"buy_tax": 5, "sell_tax": "10"}, "percent")):
        write_scored(tmp_path, [coin(A, candles, sec), coin(B, candles, sec)])
        out = LW.score(tmp_path, "base", size_usd=100.0, gas_usd=0.05)
        assert out["security"]["taxes_unit"] == unit and pair(out, 2, 30)["coins"] == 2
        assert pair(out, 2, 30)["mean"] == pytest.approx(2 * kept - 1.001, abs=1e-4)
        assert pair(out, 2, 120)["mean"] == pytest.approx(1.5 * kept - 1.001, abs=1e-4)  # out at start + 40: 1.5
        assert pair(out, 12, 30)["mean"] == pytest.approx(0.75 * kept - 1.001, abs=1e-4)  # in at start + 10: 2.0
    assert (out["size_usd"], out["gas_usd"], out["fee_pct"]) == (100.0, 0.05, 1.0)
    write_scored(tmp_path, [coin(A, candles, {"buy_tax": "0.05", "sell_tax": "0.1"})], chain="eth")
    eth = LW.score(tmp_path, "eth")  # Ethereum's gas when none is given: 3 dollars a transaction
    assert eth["gas_usd"] == 3.0 and pair(eth, 2, 30)["mean"] == pytest.approx(2 * kept - 1.06, abs=1e-4)


def test_each_leg_pays_the_swap_fee_of_its_chain(tmp_path):
    """Review of 25 Nov: no swap fee was charged. A launchpad's pool takes its fee on the buy and again on the sale:
    pump.fun's 1.25 percent on Solana (GMGN's rows of 12 Nov: 0.0125 a side), 1 percent on BNB Chain, Base and
    Ethereum unless --fee-pct says otherwise."""
    up = [flat(1.0), flat(2.0, 10)]
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
    up = [flat(1.0), flat(3.0, 10)]
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


def test_each_leg_pays_the_higher_of_the_launchs_tax_and_the_settled_one(tmp_path):
    """Review of 25 Nov: the taxes on a launch's row when the watch first met it were thrown away, so a tax lowered
    by the time of the settle read was charged at its low. Each leg pays the higher of the two reads, and a coin
    either read says cannot be sold is all lost."""
    up = [flat(1.0), flat(2.0, 10)]
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


def test_the_tax_unit_is_decided_once_for_the_whole_file(tmp_path):
    """Review of 25 Nov: whether a coin's taxes were percents or shares was guessed coin by coin (percents when one of
    its own was above 1), so in one file 0.6 was 60 percent on one coin and 0.6 percent on the next. The unit is now
    decided once for the file: percents when any coin's tax, in either read, is above 1; shares otherwise."""
    up = [flat(1.0), flat(2.0, 10)]
    write_scored(tmp_path, [coin(A, up, dict(NO_TAX, sell_tax="0.6")), coin(B, up, dict(NO_TAX, buy_tax="0.05"))])
    sec = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)["security"]
    assert (sec["taxes_unit"], sec["honeypots"], sec["ambiguous_coins"]) == ("share", 1, 2)  # A's 0.6 is 60 percent
    assert (sec["honeypots_if_shares"], sec["honeypots_if_percents"]) == (1, 0)
    # one coin with a tax above 1, settled or seen first, makes every tax of the file a percent: A's 0.6 is 0.6 percent
    for b in (coin(B, up, dict(NO_TAX, buy_tax="5")), coin(B, up, first={"buy_tax": 5})):
        write_scored(tmp_path, [coin(A, up, dict(NO_TAX, sell_tax="0.6")), b])
        out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
        sec = out["security"]
        assert (sec["taxes_unit"], sec["honeypots"], sec["ambiguous_coins"]) == ("percent", 0, 1)
        assert (sec["honeypots_if_shares"], sec["honeypots_if_percents"]) == (1, 0)
        assert pair(out, 2, 30)["mean"] == pytest.approx((2 * 0.994 - 1 + 2 * 0.95 - 1) / 2)


def test_the_table_says_how_the_coins_did_without_the_five_best_and_how_many_rugged(tmp_path):
    ends = {A: (10.0, 10.0), B: (5.0, 5.0), C: (2.0, 2.0), D: (1.5, 1.5), E: (1.2, 1.2), F: (0.5, 0.05), G: (0.1, 0.1)}
    coins = [coin(m, [flat(1.0), (S + 10, 1.0, max(1.0, c), lo, c, 1.0)]) for m, (c, lo) in ends.items()]
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
    # in at 12 s at the candle of 11 s (1.0), out at 492 s at the candle of 491 s (2.0): never 492's, nor 499's
    assert pair(out, 12, 480)["mean"] == pytest.approx(1.0)


def test_a_trade_fills_at_the_last_candle_closed_by_its_moment_never_the_one_that_opens_then(tmp_path):
    """Review of 25 Nov: a buy 2 s after the start filled at the close of the candle that opens at 2 s, a price made
    in the second after the buy, and a sale likewise. A 1 s candle that opens at t closes at t + 1: a trade at t
    fills at the last candle that opened at t - 1 or before."""
    write_scored(tmp_path, [coin(A, [flat(1.0), flat(3.0, 2), flat(5.0, 31), flat(9.0, 32)])])
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["mean"] == pytest.approx(4.0)  # in at the candle of 0 s (1.0), out at 31 s's (5.0)
    write_scored(tmp_path, [coin(B, [flat(1.0, 2), flat(2.0, 20)])])  # its first trade in the second after 2 s
    by_delay = {d["delay"]: (d["with_entry"], d["no_trade_yet"]) for d in LW.score(tmp_path, "base")["by_delay"]}
    assert by_delay == {2: (0, 1), 5: (1, 0), 12: (1, 0), 30: (1, 0)}


def test_a_missing_or_zero_price_is_skipped_never_divided_by(tmp_path):
    nothing = (None, None, None, None, None)
    write_scored(
        tmp_path,
        [
            coin(A, [flat(1.0), (S + 1, *nothing), (S + 2, 0, 0, 0, 0, 1.0), flat(3.0, 20)]),
            coin(B, [(S, 0, 0, 0, 0, 1.0), (S + 30, *nothing)]),  # no price at all: no trade at any delay
            # its first trade 8 s in: none yet at 2 and 5 s (a candle from before its start is no trade of its 500 s)
            coin(C, [flat(5.0, -5), flat(1.0, 8), flat(2.0, 20)]),
        ],
    )
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["coins"] == 1 and pair(out, 2, 30)["mean"] == pytest.approx(2.0)  # A: in at 1.0, out 3.0
    assert pair(out, 12, 30)["coins"] == 2 and pair(out, 12, 30)["mean"] == pytest.approx(1.5)  # A 2.0, C 1.0
    by_delay = {d["delay"]: (d["with_entry"], d["no_trade_yet"]) for d in out["by_delay"]}
    assert by_delay == {2: (1, 2), 5: (1, 2), 12: (2, 1), 30: (2, 1)}
    assert out["candles"]["bad_prices_skipped"] == 4 and out["candles"]["without_a_price"] == 1


def test_the_best_pair_is_chosen_without_its_five_best_coins(tmp_path):
    # one coin goes x101 at 10 s: alone it makes a hold to 30 s look best on the mean
    lines = [coin(m, [flat(1.0), flat(1.1, 3), flat(x, 10), flat(1.2, 100), flat(1.1, 250)])
             for m, x in zip((A, B, C, D, E, F), (101.0, 1.0, 1.0, 1.0, 1.0, 1.0), strict=True)]  # fmt: skip
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["mean"] == pytest.approx(100 / 6, abs=1e-3) and pair(out, 2, 30)["mean_without_best5"] == 0
    assert (out["best"]["delay"], out["best"]["hold"]) == (2, 120)  # every coin +20 percent
    assert [(p["platform"], p["coins"]) for p in out["by_platform"]] == [("clanker", 6)]


def test_the_best_pair_is_split_by_launchpad(tmp_path):
    write_scored(
        tmp_path,
        [
            coin(A, [flat(1.0), flat(2.0, 10)], platform="clanker"),
            coin(B, [flat(1.0), flat(2.0, 10)], platform="clanker"),
            coin(C, [flat(1.0), flat(0.5, 10)], platform="zora"),
            coin(D, [flat(1.0), flat(0.5, 10)], platform="zora"),
            coin(E, [flat(1.0), flat(1.5, 10)], platform=None),
        ],
    )
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert out["best"]["delay"] in (2, 5)  # five coins: too few to take five out, the median chooses
    got = {p["platform"]: (p["coins"], p["mean"]) for p in out["by_platform"]}
    assert got == {
        "clanker": (2, pytest.approx(1.0)),
        "zora": (2, pytest.approx(-0.5)),
        "unknown": (1, pytest.approx(0.5)),
    }


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
    assert (sec["refused"], sec["without_fields"], sec["honeypots"], sec["taxes_unit"]) == (1, 1, 1, "share")
    assert sec["fields"] == {
        "is_honeypot": {"given": 1, "yes": 1},
        "honeypot": {"given": 1, "yes": 0},
        "can_not_sell": {"given": 1, "yes": 0},
        "can_sell": {"given": 1, "yes": 0},
        "buy_tax": {"given": 1, "above_0": 0},
        "sell_tax": {"given": 1, "above_0": 1},
    }


def test_a_coin_whose_safety_is_not_known_is_scored_apart(tmp_path):
    """Review of 25 Nov: a coin whose security GMGN refused, or answered without one of the fields read here, was
    scored as a coin that can be sold and pays no tax. It is left out of the table and of the best pair, counted, and
    scored in a table of its own."""
    up = [flat(1.0), flat(2.0, 10)]
    lines = [
        coin(A, up),
        coin(B, up, {"error": "gmgn GET /v1/token/security: HTTP 404 NOT_FOUND: no such token"}),
        coin(C, up, {}),
        coin(D, up, {"address": D, "renounced_mint": 1, "top_10_holder_rate": 0.4}),  # none of the fields read
        coin(E, [flat(1.0), flat(0.5, 10)], {"is_honeypot": "1"}),  # known: it cannot be sold
    ]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    assert pair(out, 2, 30)["coins"] == 2 and pair(out, 2, 30)["mean"] == pytest.approx(0.0)  # A +100 %, E all lost
    apart = out["security_unknown"]
    assert apart["coins"] == 3 and pair(apart, 2, 30)["coins"] == 3 and pair(apart, 2, 30)["mean"] == pytest.approx(1.0)
    assert (out["security"]["refused"], out["security"]["without_fields"], out["coins"]) == (1, 2, 5)
    assert [p["coins"] for p in out["by_platform"]] == [2]  # the best pair split by launchpad: the known coins only


def test_a_sale_after_the_coins_last_trade_is_all_lost_unless_its_pool_still_holds_money(tmp_path):
    """Review of 25 Nov: a sale after a coin's last trade was priced at that trade, so a pool its creator emptied (no
    trade, no candle) scored as a win, and as no rug. A sale priced from a candle more than 60 s old is lost unless
    the pool read at settle still held the copy's size; the table without those sales is given beside the main one."""
    stops = [flat(1.0), flat(3.0, 10)]  # its last trade 10 s in
    lines = [
        coin(A, stops, pool={"liquidity": "0.02"}),  # emptied
        coin(B, stops, pool={"liquidity": "25000"}),  # still there: the last price stands
        coin(C, stops, pool={"error": "gmgn GET /v1/token/pool_info: HTTP 404 NOT_FOUND: no pool"}),  # not known
        coin(D, [flat(1.0)] + [flat(1.5, t) for t in range(60, 500, 50)]),  # trades to the end: no stale sale
    ]
    write_scored(tmp_path, lines)
    out = LW.score(tmp_path, "base", gas_usd=0.0, fee_pct=0.0)
    early = pair(out, 2, 30)  # out at 32 s: A, B and C at their trade of 10 s (22 s old), D at its first
    assert (early["coins"], early["stale_exits"]) == (4, 0) and early["mean"] == pytest.approx((2 + 2 + 2 + 0) / 4)
    late = pair(out, 2, 120)  # out at 122 s: A, B and C at a trade 112 s old
    assert (late["coins"], late["stale_exits"]) == (4, 3) and late["mean"] == pytest.approx((-1 + 2 - 1 + 0.5) / 4)
    assert late["rug_share"] == pytest.approx(1 / 4)  # A's pool was emptied: a rug
    fresh = pair({"table": out["table_without_stale_exits"]}, 2, 120)
    assert (fresh["coins"], fresh["mean"]) == (1, pytest.approx(0.5))
    assert out["pools"] == {"coins_with_a_stale_exit": 3, "there": 1, "gone": 1, "unknown": 1}
    # gone is less than the copy's own size: 25,000 dollars is gone for a copy of 50,000
    assert LW.score(tmp_path, "base", size_usd=50_000, gas_usd=0.0, fee_pct=0.0)["pools"]["gone"] == 2


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


def test_launch_watch_wants_its_minutes_and_sane_numbers(snipetech):
    for argv in ((), ("--minutes", "-1"), ("--minutes", "nan"), ("--minutes", "1", "--every", "0"),
                 ("--minutes", "1", "--settle-secs", "-5")):  # fmt: skip
        code, out, seen = snipetech("launch-watch", "--chain", "base", *argv, "--json")
        assert code == 2 and out["ok"] is False and not seen, argv
    _, out, _ = snipetech("launch-watch", "--chain", "base", "--json")
    assert "--minutes" in out["error"]


def test_launch_score_reads_the_watched_file_and_needs_no_key(snipetech, tmp_path, monkeypatch):
    write_scored(tmp_path, [coin(A, [flat(1.0), flat(2.0, 10)])])
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
