"""GMGN's query API (https://openapi.gmgn.ai): who the known wallets are and what they do. Reading only.

What it gives that the tape and the other sources do not: the wallets GMGN itself tags as KOLs and as smart money, with
their names and X handles; a wallet's profit, win rate and history; a coin's top holders and traders with tags (sniper,
insider, bundler, dev); trending coins and new pairs by launchpad; candles. How every call is made, what it weighs
and what could not be checked without a key: docs/research/2026-11-12-gmgn-api.md.

The key is GMGN_API_KEY in the environment (on the server: /etc/snipetech/env). It goes out in one header and is
scrubbed from every error. A read is not signed: the header, a timestamp and a fresh id are all GMGN asks for. GMGN's
routes that trade, place orders, quote or create coins all need a signature made with the private half of the key
pair, which the owner destroyed on purpose; this module has no code for them and refuses any path that is not on its
short list of reads (`READ_PATHS`). OKTech executes; GMGN never trades for us.

Pace: GMGN counts weight, not requests (Pro: 50 a second for the key and its IP together, each route its own weight).
A client spends `rate` a second (10 by default, so several jobs fit beside each other), waits out a short limit once,
and after a ban sends nothing until GMGN's reset time: a request during a ban makes the ban longer."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

import httpx

BASE = "https://openapi.gmgn.ai"
ENV_KEY = "GMGN_API_KEY"
NO_KEY = f"{ENV_KEY} is not set in /etc/snipetech/env"
# every route this client may call, with the weight GMGN charges for it (its rate limit counts weight)
READ_PATHS: dict[str, int] = {
    "/v1/user/kol": 1,
    "/v1/user/smartmoney": 1,
    "/v1/user/wallet_stats": 3,
    "/v1/user/wallet_profits": 3,
    "/v1/user/wallet_activity": 3,
    "/v1/user/created_tokens": 2,
    "/v1/token/info": 1,
    "/v1/token/security": 1,
    "/v1/token/pool_info": 1,
    "/v1/market/token_top_holders": 5,
    "/v1/market/token_top_traders": 5,
    "/v1/market/token_kline": 2,
    "/v1/market/rank": 3,
    "/v1/market/token_signal": 1,
    "/v1/market/search": 1,
    "/v1/trenches": 2,
    "/v1/callout/global": 3,
    "/v1/trade/gas_price": 1,
}
# a second lock behind the list: a path with one of these words in it is never called, whoever adds it to the list
WRITE_WORDS = frozenset({"swap", "trade", "order", "orders", "quote", "strategy", "cooking", "create", "follow"})
# the one read under GMGN's trade path: its fee presets, a GET with the chain in it and no wallet (the owner's helper,
# 12 Nov: "the only /v1/trade/ route allowed"). It is let past the lock above by its exact path, as a GET only.
FEE_PRESETS = "/v1/trade/gas_price"
# the filter tags GMGN documents for its trending list; one it does not know can switch its default screening off
TREND_FILTERS = frozenset(
    {
        "has_social",
        "not_risk",
        "not_honeypot",
        "verified",
        "locked",
        "renounced",
        "distributed",
        "frozen",
        "burn",
        "token_burnt",
        "creator_hold",
        "creator_close",
        "creator_add_liquidity",
        "creator_remove_liquidity",
        "creator_sell",
        "creator_buy",
        "not_wash_trading",
        "not_social_dup",
        "not_image_dup",
        "is_internal_market",
        "is_out_market",
        "dexscr_update_link",
    }
)
FEE_UNIT = (
    "SOL. priority_sol, tip_sol and bid_sol (the two together) are what the whole transaction paid. GMGN gives a "
    "transaction with two rows (a coin bought with another token) half the fee on each row; added up over the rows "
    "they equal our tape's own fee record to the lamport (68 of 68 transactions of two wallets, 12 Nov 2026). The "
    "tip is whatever went to a landing service (Jito or another: Helius Sender's was checked); a trading app's own "
    "fee is not in it. gas_sol is "
    "priority plus tip plus the 0.000005 SOL every signature costs"
)
SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000
# a tag GMGN gives a wallet that is a bot trading around other people's trades: never a wallet to copy
BOT_TAGS = frozenset({"sandwich_bot"})
PLANS = {5: "free", 20: "plus", 50: "pro"}  # the weight a second each plan gets (the X-RateLimit-Limit header)
RESOLUTIONS = ("1s", "30s", "1m", "5m", "15m", "1h", "4h", "1d")
PERIOD_DAYS = {"1d": 1, "7d": 7, "30d": 30}
RETRY_STATUSES = (500, 502, 503, 504)
WAITABLE = ("RATE_LIMIT_EXCEEDED", "RATE_LIMIT_BANNED")  # a limit that passes; the others never do by waiting
HINTS = {
    "AUTH_KEY_INVALID": "GMGN does not know this key: check GMGN_API_KEY in /etc/snipetech/env",
    "AUTH_IP_BLOCKED": "this machine's address is not on the key's whitelist (GMGN takes IPv4 only)",
    "AUTH_KEY_BLOCKED": "GMGN has blocked this key",
    "AUTH_TIMESTAMP_EXPIRED": "this machine's clock is more than 5 s off GMGN's: check `timedatectl`",
    "AUTH_TIMESTAMP_SKEW": "this machine's clock is more than 5 s off GMGN's: check `timedatectl`",
    "AUTH_SIGNATURE_INVALID": "GMGN wants a signature for this route: it is not a plain read and is not for us",
    "PLAN_UPGRADE_REQUIRED": "the key's plan does not include this route; waiting will not help",
}
ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
KOL_TAGS = frozenset({"kol", "renowned"})  # how GMGN marks a known person's wallet (lists and coin pages)
PUMP_WORDS = ("pump",)


class GmgnError(RuntimeError):
    """A request GMGN refused or that never arrived. `status` is the HTTP status (None when nothing came back),
    `error` GMGN's own code (AUTH_KEY_INVALID, RATE_LIMIT_BANNED, ...), `reset_at` when a limit ends (unix seconds)."""

    def __init__(self, text: str, *, status: int | None = None, error: str | None = None, reset_at: int | None = None):
        super().__init__(text)
        self.status, self.error, self.reset_at = status, error, reset_at


def _num(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _ts(v: Any) -> int:
    """Unix seconds, whether GMGN sent seconds or milliseconds."""
    n = _int(v)
    return n // 1000 if n > 100_000_000_000 else n


def _text(v: Any, limit: int = 60) -> str | None:
    """A name or handle somebody typed on GMGN: one line, printable, short; None when nothing is left."""
    if not isinstance(v, str):
        return None
    clean = "".join(ch for ch in " ".join(v.split()) if ch.isprintable())[:limit].strip()
    return clean or None


def _tags(v: Any) -> list[str]:
    return [str(t) for t in v if isinstance(t, str) and t] if isinstance(v, list) else []


def _rows(data: Any, *keys: str) -> list[dict]:
    """The list of rows in an answer: the answer itself, or what sits under the first of `keys` that holds a list."""
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                data = data[k]
                break
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def is_wallet(address: Any) -> bool:
    return isinstance(address, str) and bool(ADDRESS.match(address))


def default_transport() -> httpx.BaseTransport | None:
    """How requests leave when no transport is given. GMGN answers IPv4 only: bound to an IPv4 address, a connection
    cannot leave over IPv6. Behind a proxy the proxy chooses the route, and the library's own handling of it is kept."""
    if any(os.environ.get(p) for p in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")):
        return None
    return httpx.HTTPTransport(local_address="0.0.0.0")


class Gmgn:
    def __init__(
        self,
        key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        rate: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        timeout: float = 20.0,
        counter: Callable[[int], None] | None = None,
        max_wait: float = 10.0,
    ):
        self._key = key or os.environ.get(ENV_KEY) or ""
        if not self._key:
            raise GmgnError(NO_KEY)
        if transport is None:
            transport = default_transport()
        self.http = httpx.Client(
            base_url=BASE,
            timeout=timeout,
            transport=transport,
            # the content type goes out on a GET too, as GMGN's own client sends it and its call-out page asks
            headers={"X-APIKEY": self._key, "Content-Type": "application/json", "accept": "application/json"},
        )
        self.sleep, self.clock, self.counter, self.max_wait = sleep, clock, counter, float(max_wait)
        self.rate = max(1.0, float(rate))
        self._bucket, self._bucket_at = self.rate, clock()  # weight that may be spent now; refilled at `rate` a second
        self._blocked_until = 0.0  # GMGN's reset time after a limit: nothing is sent before it
        self.calls, self.weight_used = 0, 0  # requests sent; weight of the answered ones
        self.limit: int | None = None  # the plan's weight a second, as the last answer's header said

    def __repr__(self) -> str:
        return f"Gmgn(calls={self.calls}, weight_used={self.weight_used})"

    def close(self) -> None:
        self.http.close()

    # -- transport ---------------------------------------------------------------------------------------------------

    def _scrub(self, text: Any) -> str:
        return str(text).replace(self._key, "<redacted>")

    def _pace(self, weight: int) -> None:
        now = self.clock()
        self._bucket = min(self.rate, self._bucket + max(0.0, now - self._bucket_at) * self.rate)
        self._bucket_at = now
        if self._bucket < weight:
            wait = (weight - self._bucket) / self.rate
            self.sleep(wait)
            self._bucket, self._bucket_at = float(weight), self.clock()
        self._bucket -= weight

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, body: dict | None = None) -> Any:
        """One read: GMGN's `data`. The path must be on the list of reads; anything else is refused unsent."""
        if path == FEE_PRESETS:
            allowed = method == "GET"
        else:
            allowed = (
                path in READ_PATHS
                and method in ("GET", "POST")
                and not set(re.split(r"[/_]", path.lower())) & WRITE_WORDS
            )
        if not allowed:
            raise PermissionError(
                f"gmgn {method} {path}: not a read this client makes (it is read-only: no swap, order or quote)"
            )
        if method == "GET":
            body = None
        weight, where = READ_PATHS[path], f"gmgn {method} {path}"
        asked = {k: v for k, v in (params or {}).items() if v is not None}
        waited = False
        for attempt in range(3):
            left = self._blocked_until - self.clock()
            if left > 0:
                if left > self.max_wait:
                    raise GmgnError(
                        f"{where}: no request sent: GMGN's rate limit holds for another {left:.0f} s, and "
                        "asking before it ends makes it longer",
                        status=429,
                        error="RATE_LIMIT",
                        reset_at=int(self._blocked_until),
                    )
                self.sleep(left + 1.0)
            self._pace(weight)
            # the stamp must be within 5 s of GMGN's clock and the id new on every request, a retry included
            query = dict(asked, timestamp=int(self.clock()), client_id=str(uuid.uuid4()))
            try:
                r = self.http.request(method, path, params=query, json=body)
            except httpx.HTTPError as exc:
                self.calls += 1
                if attempt == 0:
                    self.sleep(0.5)
                    continue
                raise GmgnError(self._scrub(f"{where}: network: {type(exc).__name__}: {exc}")) from None
            self.calls += 1
            if r.headers.get("x-ratelimit-limit"):
                self.limit = _int(r.headers["x-ratelimit-limit"]) or self.limit
            if r.status_code in RETRY_STATUSES and attempt < 2:
                self.sleep(0.5 * (attempt + 1))
                continue
            try:
                answer = r.json()
            except ValueError:
                answer = None
            if not isinstance(answer, dict):
                raise GmgnError(
                    self._scrub(
                        f"{where}: HTTP {r.status_code}, and the answer is not JSON: {' '.join(r.text.split())[:120]}"
                    ),
                    status=r.status_code,
                )
            if r.status_code == 200 and answer.get("code") in (0, "0"):
                self.weight_used += weight
                if self.counter is not None:
                    self.counter(weight)
                data = answer.get("data")
                # the trending route wraps its answer a second time ({"code": 0, "data": {"rank": [...]}, "message":
                # "success"} inside `data`; real answer, 12 Nov): the inner `data` is the answer
                if isinstance(data, dict) and data.get("code") in (0, "0") and isinstance(data.get("data"), dict):
                    data = data["data"]
                return data
            code = _text(str(answer.get("error") or ""), 60)
            said = _text(str(answer.get("message") or ""), 200) or "no message"
            reset = _int(answer.get("reset_at")) or _int(r.headers.get("x-ratelimit-reset"))
            if not reset and r.headers.get("retry-after"):
                reset = int(self.clock()) + _int(r.headers["retry-after"])
            text = f"{where}: HTTP {r.status_code} {code or 'error'}: {said}"
            if r.status_code == 429:
                reset = reset or int(self.clock()) + 60  # no time given: stay away a minute
                self._blocked_until = float(reset)
                wait = max(0.0, reset - self.clock())
                if code in WAITABLE and not waited and wait + 1.0 <= self.max_wait:
                    waited = True
                    self.sleep(wait + 1.0)
                    self._blocked_until = 0.0
                    continue
                raise GmgnError(
                    self._scrub(
                        f"{text}; no request until {time.strftime('%H:%M:%S', time.gmtime(reset))} "
                        f"UTC (in {wait:.0f} s): asking earlier makes the limit longer"
                    ),
                    status=429,
                    error=code,
                    reset_at=reset,
                )
            hint = HINTS.get(code or "")
            if code == "PLAN_UPGRADE_REQUIRED" and answer.get("upgrade_message"):
                hint = f"{hint} ({_text(str(answer['upgrade_message']), 160)})"
            raise GmgnError(self._scrub(text + (f" ({hint})" if hint else "")), status=r.status_code, error=code)
        raise GmgnError(f"{where}: gave up after retries")

    # -- the lists of people -------------------------------------------------------------------------------------------

    @staticmethod
    def _feed_row(t: dict) -> dict | None:
        who = t.get("maker_info") if isinstance(t.get("maker_info"), dict) else {}
        coin = t.get("base_token") if isinstance(t.get("base_token"), dict) else {}
        wallet = t.get("maker") or who.get("address")
        if not is_wallet(wallet):
            return None
        return {
            "wallet": wallet,
            # no wallet on the real lists had GMGN's own `name`; the X display name is what there is
            "name": _text(who.get("name")) or _text(who.get("twitter_name")),
            "twitter": _text(who.get("twitter_username")),
            "tags": _tags(who.get("tags")),
            "side": t.get("side"),
            "mint": t.get("base_address"),
            "symbol": _text(coin.get("symbol"), 24),
            "launchpad": _text(coin.get("launchpad") or t.get("launchpad"), 40),
            "tokens": _num(t.get("base_amount")),
            "usd": _num(t.get("amount_usd")),
            "price_usd": _num(t.get("price_usd")),
            "ts": _ts(t.get("timestamp")),
            "tx": t.get("transaction_hash"),
            "closes": _int(t.get("is_open_or_close")) == 1,
        }

    def _feed(self, path: str, chain: str, limit: int, raw: bool) -> Any:
        data = self._request("GET", path, {"chain": chain, "limit": min(200, max(1, int(limit)))})
        if raw:
            return data
        return [row for row in map(self._feed_row, _rows(data, "list")) if row]

    def kol_feed(self, chain: str = "sol", limit: int = 100, raw: bool = False) -> Any:
        """The newest trades of the wallets GMGN lists as KOLs (known people), newest first, 200 at most. Each row
        names the wallet, its name and X handle when GMGN has them, its tags, the coin and the dollars."""
        return self._feed("/v1/user/kol", chain, limit, raw)

    def smart_money_feed(self, chain: str = "sol", limit: int = 100, raw: bool = False) -> Any:
        """The newest trades of the wallets GMGN's own ranking calls smart money (tag smart_degen), 200 at most."""
        return self._feed("/v1/user/smartmoney", chain, limit, raw)

    # the fields of a statistics answer this client gives a name; any other plain field is kept under `extra`
    STATS_READ = frozenset(
        {
            "realized_profit",
            "unrealized_profit",
            "winrate",
            "total_cost",
            "bought_cost",
            "buy_count",
            "sell_count",
            "buy",
            "sell",
            "pnl",
            "realized_profit_pnl",
            "bought_fee",
            "sold_fee",
            "sold_income",
            "native_balance",
            "last_timestamp",
            "pnl_stat",
            "common",
        }
    )
    PNL_STAT_READ = frozenset({"winrate", "token_num", "avg_holding_period"})
    COMMON_READ = frozenset(
        {
            "name",
            "nick_name",
            "twitter_name",
            "twitter_username",
            "tag",
            "tags",
            "followers_count",
            "twitter_fans_num",
            "follow_count",
            "remark_count",
            "created_at",
            "created_token_count",
            "fund_from",
            "fund_from_address",
            "fund_from_ts",
            "fund_amount",
            "fund_tx_hash",
        }
    )

    @staticmethod
    def _stats_row(d: dict, wallet: str, period: str) -> dict:
        """A real answer (12 Nov) names the fields `buy`, `sell`, `bought_cost`, `total_cost`, `pnl_stat.winrate` and
        `realized_profit_pnl`; GMGN's field table names them `buy_count`, `sell_count`, `winrate`, `pnl`, and those
        are still read should an answer use them. Every number is in dollars except `sol_balance` and `funded_sol`.
        `buy_fees_usd` and `sell_fees_usd` are everything the buys and the sales cost beside the coins: network fee,
        tip and the exchange's cut (on 32 real buys the per-trade costs came to 1.86 % of the dollars bought, the
        total here to 1.89 %). `funded_by` is the wallet its first SOL came from and `funded_by_name` GMGN's name for
        that wallet when it is a service (an exchange, a trading app). A field without a name here is under
        `extra`."""
        ps = d.get("pnl_stat") if isinstance(d.get("pnl_stat"), dict) else {}
        who = d.get("common") if isinstance(d.get("common"), dict) else {}
        buys = _int(d.get("buy_count") if d.get("buy_count") is not None else d.get("buy"))
        sells = _int(d.get("sell_count") if d.get("sell_count") is not None else d.get("sell"))
        realized = _num(d.get("realized_profit"))
        cost = _num(d.get("total_cost") if d.get("total_cost") is not None else d.get("bought_cost"))
        ratio = _num(d.get("pnl") if d.get("pnl") is not None else d.get("realized_profit_pnl"))
        if ratio is None and realized is not None and cost:
            ratio = realized / cost
        win = _num(d.get("winrate") if d.get("winrate") is not None else ps.get("winrate"))
        tags = _tags(who.get("tags")) or ([who["tag"]] if isinstance(who.get("tag"), str) and who.get("tag") else [])
        fans = who.get("twitter_fans_num") if who.get("twitter_fans_num") is not None else who.get("followers_count")
        funder = who.get("fund_from_address")
        extra = {k: v for k, v in d.items() if k not in Gmgn.STATS_READ and not isinstance(v, (dict, list))}
        for part, src, read in (("pnl_stat", ps, Gmgn.PNL_STAT_READ), ("common", who, Gmgn.COMMON_READ)):
            rest = {k: v for k, v in src.items() if k not in read}
            if rest:
                extra[part] = rest
        more = {
            "follow_count": _int(who["follow_count"]) if who.get("follow_count") is not None else None,
            "remark_count": _int(who["remark_count"]) if who.get("remark_count") is not None else None,
            "last_ts": _ts(d["last_timestamp"]) if d.get("last_timestamp") else None,
            "bought_usd": _num(d.get("bought_cost")),
            "sold_usd": _num(d.get("sold_income")),
            "buy_fees_usd": _num(d.get("bought_fee")),
            "sell_fees_usd": _num(d.get("sold_fee")),
            "sol_balance": _num(d.get("native_balance")),
            "funded_by": funder if is_wallet(funder) else None,
            "funded_by_name": _text(who.get("fund_from")),
            "funded_ts": _ts(who["fund_from_ts"]) if who.get("fund_from_ts") else None,
            "funded_sol": _num(who.get("fund_amount")),
            "funding_tx": who.get("fund_tx_hash") or None,
            "extra": extra,
        }
        return more | {
            "wallet": wallet,
            "period": period,
            "days": PERIOD_DAYS.get(period),
            "realized_usd": realized,
            "unrealized_usd": _num(d.get("unrealized_profit")),
            "win_rate": win,
            "cost_usd": cost,
            "buys": buys,
            "sells": sells,
            "trades": buys + sells,
            "pnl_ratio": ratio,
            "tokens": _int(ps["token_num"]) if ps.get("token_num") is not None else None,
            "avg_hold_secs": _int(ps["avg_holding_period"]) if ps.get("avg_holding_period") is not None else None,
            "name": _text(who.get("name")) or _text(who.get("nick_name")) or _text(who.get("twitter_name")),
            "twitter": _text(who.get("twitter_username")),
            "tags": tags,
            "followers": _int(fans) if fans is not None else None,
            "created_at": _ts(who["created_at"]) if who.get("created_at") else None,
            "created_tokens": _int(who["created_token_count"]) if who.get("created_token_count") is not None else None,
        }

    def wallet_stats(self, wallet: str, period: str = "7d", chain: str = "sol", raw: bool = False) -> Any:
        """One wallet's trading over 7d or 30d: realized profit in dollars, win rate (0 to 1), what it spent and what
        its trades cost in fees, how many buys and sells, when it last traded, and who GMGN says it is: name, X
        handle, tags, X followers (`followers`), how many GMGN users follow the wallet (`follow_count`: how copied it
        is) and the wallet that funded it. Unrealized profit is not in this answer (`wallet_profits` has it)."""
        if period not in ("7d", "30d"):
            raise ValueError("period: 7d or 30d")
        data = self._request(
            "GET", "/v1/user/wallet_stats", {"chain": chain, "wallet_address": [wallet], "period": period}
        )
        if raw:
            return data
        if isinstance(data, list):  # asked for one wallet, answered as a batch of one
            data = data[0] if data and isinstance(data[0], dict) else {}
        return self._stats_row(data if isinstance(data, dict) else {}, wallet, period)

    def wallet_profits(self, wallets: Iterable[str], period: str = "7d", chain: str = "sol", raw: bool = False) -> Any:
        """Profit of up to 100 wallets in one request (1d, 7d, 30d or all): realized over the period with its cost,
        buys and sells, unrealized now, and realized over all time."""
        wallets = list(wallets)
        if not 1 <= len(wallets) <= 100:
            raise ValueError("wallet_profits: 1 to 100 wallets a request")
        if period not in ("1d", "7d", "30d", "all"):
            raise ValueError("period: 1d, 7d, 30d or all")
        data = self._request(
            "POST", "/v1/user/wallet_profits", {}, {"chain": chain, "period": period, "wallet_addresses": wallets}
        )
        if raw:
            return data
        return [
            {
                "wallet": r.get("wallet_address"),
                "period": period,
                "realized_usd": _num(r.get("realized_profit")),
                "cost_usd": _num(r.get("realized_profit_cost")),
                "buys": _int(r.get("buy")),
                "sells": _int(r.get("sell")),
                "unrealized_usd": _num(r.get("unrealized_profit")),
                "all_time_realized_usd": _num(r.get("total_realized_profit")),
                "all_time_cost_usd": _num(r.get("total_realized_profit_cost")),
                "all_time_profit_usd": _num(r.get("total_profit")),  # realized and unrealized together
            }
            for r in _rows(data, "list")
            if is_wallet(r.get("wallet_address"))
        ]

    def wallet_activity(
        self,
        wallet: str,
        limit: int = 50,
        token: str | None = None,
        cursor: str | None = None,
        kinds: list[str] | None = None,
        chain: str = "sol",
        raw: bool = False,
    ) -> Any:
        """A wallet's own history, newest first: every buy, sell and transfer with the coin, the dollars, the token it
        was paid with (`quote`; `sol` is the SOL paid or received, None when it was another token), on a sale what the
        sold part had cost, and what the wallet paid to land it: `priority_fee`, `tip_fee` and `gas_sol` in SOL,
        `gas_usd` in dollars. One transaction can be two rows (a coin bought with another token, and that token
        bought): each row then carries half the transaction's fee (`fees` adds them up). `next` is the cursor of the
        following page (None on the last). A page holds 50 rows at most; GMGN answers an ask for more with 20, so
        more is never asked."""
        data = self._request(
            "GET",
            "/v1/user/wallet_activity",
            {
                "chain": chain,
                "wallet_address": wallet,
                "token_address": token,
                "limit": min(50, max(1, int(limit))),
                "cursor": cursor,
                "type": list(kinds) if kinds else None,
            },
        )
        if raw:
            return data
        rows = []
        for a in _rows(data, "activities"):
            coin = a.get("token") if isinstance(a.get("token"), dict) else {}
            paid = a.get("quote_token") if isinstance(a.get("quote_token"), dict) else {}
            quote_mint = a.get("quote_address") or paid.get("token_address") or None
            rows.append(
                {
                    "tx": a.get("tx_hash"),
                    "kind": a.get("event_type") or a.get("type"),
                    "mint": coin.get("address") or coin.get("token_address"),
                    "symbol": _text(coin.get("symbol"), 24),
                    "tokens": _num(a.get("token_amount")),
                    "usd": _num(a.get("cost_usd")),
                    "buy_cost_usd": _num(a.get("buy_cost_usd")),
                    "price_usd": _num(a.get("price_usd")),
                    "quote": _text(paid.get("symbol"), 24),
                    "quote_mint": quote_mint,
                    "quote_amount": _num(a.get("quote_amount")),
                    "sol": _num(a.get("quote_amount")) if quote_mint == SOL_MINT else None,
                    "ts": _ts(a.get("timestamp")),
                    "platform": _text(a.get("launchpad_platform"), 40),
                    "closes": _int(a.get("is_open_or_close")) == 1,
                    "gas_sol": _num(a.get("gas_native")),
                    "gas_usd": _num(a.get("gas_usd")),
                    "exchange_fee_usd": _num(a.get("dex_usd")),
                    "priority_fee": _num(a.get("priority_fee")),
                    "tip_fee": _num(a.get("tip_fee")),
                }
            )
        return {"rows": rows, "next": (data.get("next") or None) if isinstance(data, dict) else None}

    def created_tokens(self, wallet: str, chain: str = "sol") -> Any:
        """The coins a wallet created, as GMGN sends them: `tokens` (100 at most), how many are still on their curve
        (`inner_count`) and how many left it (`open_count`), and its best coin (`creator_ath_info`)."""
        return self._request("GET", "/v1/user/created_tokens", {"chain": chain, "wallet_address": wallet})

    # -- a coin --------------------------------------------------------------------------------------------------------

    def token_info(self, mint: str, chain: str = "sol", raw: bool = False) -> Any:
        """A coin's card: price, liquidity, holders, launchpad and how far its curve is, its creator (and how many
        coins that wallet launched), its links, and how many smart, KOL, sniper, insider and bundler wallets hold it."""
        data = self._request("GET", "/v1/token/info", {"chain": chain, "address": mint})
        if raw or not isinstance(data, dict):
            return data

        def part(name: str) -> dict:
            return data[name] if isinstance(data.get(name), dict) else {}

        price, dev, link, stat, held = part("price"), part("dev"), part("link"), part("stat"), part("wallet_tags_stat")
        pool = part("pool")
        # how many coins the creator launched is `stat.creator_created_count`; `dev.creator_open_count` is how many of
        # them left their curve (the real card of 12 Nov: 3,655 launched, 14 graduated)
        made = stat.get("creator_created_count")
        return {
            "mint": data.get("address") or mint,
            "symbol": _text(data.get("symbol"), 24),
            "name": _text(data.get("name")),
            "price_usd": _num(price.get("price") if price else data.get("price")),
            "liquidity_usd": _num(data.get("liquidity")),
            "holders": _int(data.get("holder_count")),
            "created_ts": _ts(data.get("creation_timestamp")),
            "open_ts": _ts(data.get("open_timestamp")),
            "launchpad": _text(data.get("launchpad"), 40),
            "platform": _text(data.get("launchpad_platform"), 40),
            "curve_progress": _num(data.get("launchpad_progress")),
            "launchpad_status": data.get("launchpad_status"),
            "creator": dev.get("creator_address"),
            "creator_status": dev.get("creator_token_status"),
            "creator_launches": _int(made) if made is not None else None,
            "creator_graduated": _int(dev["creator_open_count"]) if dev.get("creator_open_count") is not None else None,
            "twitter": _text(link.get("twitter_username")),
            "website": _text(link.get("website"), 120),
            "top10_rate": _num(stat.get("top_10_holder_rate")),
            "volume_1h_usd": _num(price.get("volume_1h")),
            "exchange": _text(pool.get("exchange"), 40),
            "pool": pool.get("pool_address") or data.get("biggest_pool_address") or None,
            "quote": _text(pool.get("quote_symbol"), 24),
            "quote_mint": pool.get("quote_address") or None,
            "migrated_ts": _ts(data.get("migrated_timestamp")),
            "wallets": {
                "smart": _int(held.get("smart_wallets")),
                "kol": _int(held.get("renowned_wallets")),
                "sniper": _int(held.get("sniper_wallets")),
                "insider": _int(held.get("rat_trader_wallets")),
                "bundler": _int(held.get("bundler_wallets")),
                "whale": _int(held.get("whale_wallets")),
                "fresh": _int(held.get("fresh_wallets")),
            },
        }

    def token_security(self, mint: str, chain: str = "sol") -> Any:
        """A coin's risk numbers as GMGN sends them: mint and freeze authority given up, top-10 share, rug_ratio,
        wash trading, the share of volume from insiders (rat_trader) and bundlers, how many snipers bought the launch.
        The map of GMGN's skills says rug_ratio may be missing here and present on list rows (trending, new pairs)."""
        return self._request("GET", "/v1/token/security", {"chain": chain, "address": mint})

    def token_pool(self, mint: str, chain: str = "sol") -> Any:
        """The coin's main pool as GMGN sends it: exchange, liquidity, reserves."""
        return self._request("GET", "/v1/token/pool_info", {"chain": chain, "address": mint})

    @staticmethod
    def _holder_row(h: dict) -> dict | None:
        if not is_wallet(h.get("address")) or _int(h.get("addr_type")) == 2:  # 2: an exchange or a pool, not a person
            return None
        funder = h.get("native_transfer") if isinstance(h.get("native_transfer"), dict) else {}
        # the wallet its first SOL came from: `from_address` in a real answer (the documents said `address`)
        funded_by = funder.get("from_address") or funder.get("address")
        balance = _num(h.get("native_balance"))  # lamports in this answer (SOL in a wallet's statistics)
        return {
            "wallet": h["address"],
            "name": _text(h.get("name")),
            "twitter": _text(h.get("twitter_username")),
            "tags": _tags(h.get("tags")),
            "coin_tags": _tags(h.get("maker_token_tags")),
            "pct": _num(h.get("amount_percentage")),
            "usd_value": _num(h.get("usd_value")),
            "profit_usd": _num(h.get("profit")),
            "realized_usd": _num(h.get("realized_profit")),
            "unrealized_usd": _num(h.get("unrealized_profit")),
            "buy_usd": _num(h.get("buy_volume_cur")),
            "sell_usd": _num(h.get("sell_volume_cur")),
            "buys": _int(h.get("buy_tx_count_cur")),
            "sells": _int(h.get("sell_tx_count_cur")),
            "first_ts": _ts(h.get("start_holding_at")),
            "last_ts": _ts(h.get("last_active_timestamp")),
            "funded_by": funded_by if is_wallet(funded_by) else None,
            "funded_by_name": _text(funder.get("name")),
            "funded_ts": _ts(funder["timestamp"]) if funder.get("timestamp") else None,
            "funded_sol": _num(funder.get("amount")),
            "sol_balance": None if balance is None else balance / LAMPORTS,
            "is_new": bool(h.get("is_new")),
            "suspicious": bool(h.get("is_suspicious")),
        }

    def _holders(self, path: str, mint: str, limit: int, order_by: str, tag: str | None, chain: str, raw: bool) -> Any:
        data = self._request(
            "GET",
            path,
            {
                "chain": chain,
                "address": mint,
                "limit": min(100, max(1, int(limit))),
                "order_by": order_by,
                "direction": "desc",
                "tag": tag,
            },
        )
        if raw:
            return data
        return [row for row in map(self._holder_row, _rows(data, "list")) if row]

    def token_holders(
        self,
        mint: str,
        limit: int = 20,
        order_by: str = "amount_percentage",
        tag: str | None = None,
        chain: str = "sol",
        raw: bool = False,
    ) -> Any:
        """A coin's biggest holders (100 at most), pools left out. `tags` are GMGN's labels for the wallet (kol,
        smart_degen, a trading app), `coin_tags` what it did in this coin (sniper, bundler, rat_trader = insider,
        creator, dev_team, top_holder). `tag` asks for one kind only: smart_degen, renowned (KOLs), sniper, rat_trader,
        bundler, dev, fresh_wallet, transfer_in, dex_bot, bluechip_owner."""
        return self._holders("/v1/market/token_top_holders", mint, limit, order_by, tag, chain, raw)

    def token_traders(
        self,
        mint: str,
        limit: int = 20,
        order_by: str = "profit",
        tag: str | None = None,
        chain: str = "sol",
        raw: bool = False,
    ) -> Any:
        """The wallets that made the most on a coin (by `order_by`: profit, unrealized_profit, buy_volume_cur,
        sell_volume_cur, amount_percentage), with the same tags as the holders."""
        return self._holders("/v1/market/token_top_traders", mint, limit, order_by, tag, chain, raw)

    # -- the market ----------------------------------------------------------------------------------------------------

    @staticmethod
    def _coin_row(t: dict) -> dict | None:
        mint = t.get("address")
        if not is_wallet(mint):
            return None
        cap = t.get("usd_market_cap") if t.get("usd_market_cap") is not None else t.get("market_cap")
        created = t.get("created_timestamp") if t.get("created_timestamp") is not None else t.get("creation_timestamp")

        def first(*names: str) -> Any:
            """The trending list and the new-pairs list name the same number differently: the first that is there."""
            return next((t[n] for n in names if t.get(n) is not None), None)

        snipers = t.get("sniper_count")  # the new-pairs rows have none: unknown, not zero
        return {
            "mint": mint,
            "symbol": _text(t.get("symbol"), 24),
            "name": _text(t.get("name")),
            "rank": _int(t.get("rank")),
            "platform": _text(t.get("launchpad_platform"), 40),
            "exchange": _text(t.get("exchange"), 40),
            "market_cap_usd": _num(cap),
            "liquidity_usd": _num(t.get("liquidity")),
            "volume_usd": _num(first("volume", "volume_24h")),
            "price_usd": _num(t.get("price")),
            "change_pct": _num(t.get("price_change_percent")),
            "swaps": _int(first("swaps", "swaps_24h")),
            "holders": _int(t.get("holder_count")),
            "smart_money": _int(t.get("smart_degen_count")),
            "kols": _int(t.get("renowned_count")),
            "created_ts": _ts(created),
            "open_ts": _ts(t.get("open_timestamp")),
            "creator": t.get("creator"),
            "rug_ratio": _num(t.get("rug_ratio")),
            "bundler_rate": _num(first("bundler_rate", "bundler_trader_amount_rate")),
            "insider_rate": _num(t.get("rat_trader_amount_rate")),
            "snipers": None if snipers is None else _int(snipers),
            "top10_rate": _num(t.get("top_10_holder_rate")),
            "curve_progress": _num(t.get("progress")),
            # the token the coin is priced in: SOL, or since late 2026 another token (a tokenised stock, a coin)
            "quote_mint": first("launch_quote_address", "quote_address"),
            "source": "gmgn",
        }

    def trending(
        self,
        interval: str = "1h",
        limit: int = 50,
        order_by: str | None = None,
        platforms: list[str] | None = None,
        filters: list[str] | None = None,
        chain: str = "sol",
        raw: bool = False,
        **ranges: Any,
    ) -> Any:
        """GMGN's trending coins over 1m, 5m, 1h, 6h or 24h (100 at most), each with its launchpad, market cap, volume,
        how many smart-money and KOL wallets hold it and its risk numbers. `platforms` keeps some launchpads
        ("Pump.fun", "pump_mayhem", "pool_pump_amm", ...); `ranges` are GMGN's own bounds (min_marketcap=50000,
        max_created="30m", min_smart_degen_count=3, ...). `filters` are GMGN's screening tags; without any, its own
        defaults for Solana apply (mint and freeze authority given up), and only a documented tag is sent."""
        unknown = sorted(set(filters or ()) - TREND_FILTERS)
        if unknown:
            raise ValueError(f"trending: not a filter tag GMGN documents: {', '.join(unknown)}")
        data = self._request(
            "GET",
            "/v1/market/rank",
            {
                "chain": chain,
                "interval": interval,
                "limit": min(100, max(1, int(limit))),
                "order_by": order_by,
                "direction": "desc" if order_by else None,
                "filters": list(filters) if filters else None,
                "platforms": list(platforms) if platforms else None,
                **ranges,
            },
        )
        if raw:
            return data
        return [row for row in map(self._coin_row, _rows(data, "rank")) if row]

    def new_pairs(
        self,
        kinds: list[str] | None = None,
        platforms: list[str] | None = None,
        limit: int = 80,
        chain: str = "sol",
        raw: bool = False,
    ) -> Any:
        """GMGN's "trenches": the newest coins (`new`), the ones about to leave their curve (`near_completion`) and the
        ones that just did (`completed`), 80 a stage at most."""
        section: dict[str, Any] = {
            "filters": ["offchain", "onchain"],
            "launchpad_platform_v2": True,
            "limit": min(80, max(1, int(limit))),
        }
        if platforms:
            section["launchpad_platform"] = list(platforms)
        body: dict[str, Any] = {"version": "v2"}
        for kind in kinds or ("new_creation", "near_completion", "completed"):
            if kind not in ("new_creation", "near_completion", "completed"):
                raise ValueError("new_pairs: new_creation, near_completion or completed")
            body[kind] = dict(section)
        data = self._request("POST", "/v1/trenches", {"chain": chain}, body)
        if raw or not isinstance(data, dict):
            return data
        # a real answer (12 Nov) names the stages as they were asked for; GMGN's documents had the near-completion
        # stage under `pump`, which is still read should an answer use it. GMGN sent 60 rows a stage whatever `limit`
        # asked (3 was asked): the rows are cut here.
        stages = (
            ("new", ("new_creation",)),
            ("near_completion", ("near_completion", "pump")),
            ("completed", ("completed",)),
        )
        cap = section["limit"]
        return {
            ours: [row for row in map(self._coin_row, _rows(data, *theirs)) if row][:cap] for ours, theirs in stages
        }

    def candles(
        self,
        mint: str,
        resolution: str = "1m",
        start: int | None = None,
        end: int | None = None,
        chain: str = "sol",
        raw: bool = False,
    ) -> Any:
        """A coin's candles, oldest first: prices in dollars, `volume_usd` the dollars traded (GMGN's `volume`),
        `volume_tokens` the coins (GMGN's `amount`; its documents had the two the other way round, a real candle
        settles it: coins times price is the dollars). `start` and `end` are unix seconds (GMGN wants milliseconds:
        converted here). 1s is Pro only, 500 a request."""
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution: one of {', '.join(RESOLUTIONS)}")
        data = self._request(
            "GET",
            "/v1/market/token_kline",
            {
                "chain": chain,
                "address": mint,
                "resolution": resolution,
                "from": None if start is None else int(start) * 1000,
                "to": None if end is None else int(end) * 1000,
            },
        )
        if raw:
            return data
        return [
            {
                "ts": _ts(c.get("time")),
                "open": _num(c.get("open")),
                "high": _num(c.get("high")),
                "low": _num(c.get("low")),
                "close": _num(c.get("close")),
                "volume_usd": _num(c.get("volume")),
                "volume_tokens": _num(c.get("amount")),
            }
            for c in _rows(data, "list")
        ]

    def signals(self, kinds: list[int] | None = None, chain: str = "sol", **bounds: Any) -> Any:
        """GMGN's coin signals as it sends them, newest first, 50 at most: 12 = a smart-money buy, 20 = a KOL buy,
        6 = a price spike, 7 = an all-time high, 10 = bundlers selling, 11 = a community takeover, 18 = a pump.fun
        fee claim. `bounds`: mc_min, mc_max, trigger_mc_min, trigger_mc_max (dollars)."""
        group = {k: v for k, v in dict(bounds, signal_type=list(kinds) if kinds else None).items() if v is not None}
        return self._request("POST", "/v1/market/token_signal", {}, {"chain": chain, "groups": [group]})

    def callouts(self, only_kol: bool = True, limit: int = 50, chain: str = "sol") -> Any:
        """The coins people called on GMGN, newest first, as it sends them (`list`): who called (wallet, X handle,
        KOL or not), the coin, the price then and how many times it has multiplied since, and what the caller still
        holds of it."""
        return self._request(
            "POST",
            "/v1/callout/global",
            {},
            {"chains": [chain], "limit": min(50, max(0, int(limit))), "only_kol": bool(only_kol)},
        )

    def search(self, query: str, chain: str = "sol", raw: bool = False) -> Any:
        """GMGN's search by name, symbol, X handle or address: the wallets it finds (50 at most, each with its X handle
        and tags) and the coins. It is fuzzy: one handle can come back with a fan's wallet beside the real ones
        (`who` sorts them)."""
        text = "".join(ch for ch in " ".join(str(query).split()) if ch.isprintable())
        if not 1 <= len(text) <= 100:
            raise ValueError("search: 1 to 100 characters")
        data = self._request("GET", "/v1/market/search", {"q": text, "chain": chain})
        if raw or not isinstance(data, dict):
            return data
        wallets = [
            {
                "wallet": w["address"],
                "twitter": _text(w.get("twitter_username")),
                "name": _text(w.get("twitter_name")),
                "tags": _tags(w.get("wallet_tags")),
                "followers": _int(w["twitter_fans_num"]) if w.get("twitter_fans_num") is not None else None,
                "follow_count": _int(w["follow_count"]) if w.get("follow_count") is not None else None,
                "remark_count": _int(w["remark_count"]) if w.get("remark_count") is not None else None,
                "last_ts": _ts(w["last_active_time"]) if w.get("last_active_time") else None,
                "sol_balance": _num(w.get("balance")),
                "funded_by_name": _text(w.get("fund_from")),
                "realized_7d_usd": _num(w.get("realized_profit_7d")),
            }
            for w in _rows(data, "wallets")
            if is_wallet(w.get("address")) and w.get("chain", chain) == chain
        ]
        coins = [
            {
                "mint": c["address"],
                "symbol": _text(c.get("symbol"), 24),
                "name": _text(c.get("name")),
                "market_cap_usd": _num(c.get("mcp")),
                "holders": _int(c.get("holder_count")),
                "platform": _text(c.get("launchpad_platform"), 40),
                "creator": c.get("creator"),
                "kols": _int(c.get("kol_count")),
                "created_ts": _ts(c.get("created_at")),
            }
            for c in _rows(data, "coins")
            if is_wallet(c.get("address"))
        ]
        return {"wallets": wallets, "coins": coins}

    def gas_price(self, chain: str = "sol", raw: bool = False) -> Any:
        """GMGN's fee presets: what its own users pay when they pick low, average or high, and its automatic fee with
        and without MEV protection, in SOL (the real presets of 12 Nov are 0.001, 0.005 and 0.01: the very amounts
        wallets' trades carry as priority fee and tip in GMGN's history, which are SOL by our tape). On Solana the
        presets are the `_mixed` fields (the plain ones are a placeholder 1, and so is `last_block`). A read: nothing
        is priced for a wallet and nothing is sent."""
        data = self._request("GET", FEE_PRESETS, {"chain": chain})
        if raw or not isinstance(data, dict):
            return data
        tiers = ("low", "average", "high")
        return {
            **{t: _num(data.get(f"{t}_prio_fee_mixed")) for t in tiers},
            "auto": _num(data.get("auto")),
            "auto_mev": _num(data.get("auto_mev")),
            "secs": {t: _int(data.get(f"{t}_estimate_time")) for t in tiers},
            "sol_usd": _num(data.get("native_token_usd_price")),
            "last_block": _int(data.get("last_block")),
        }


def ping(**client_args: Any) -> dict:
    """Whether the key works, by the lightest read there is (the newest KOL trade, weight 1). Never raises and never
    holds the key: {"ok", "status", "error"} and, when GMGN answered, the plan its rate-limit header names."""
    path = "/v1/user/kol"
    try:
        client = Gmgn(**client_args)
    except GmgnError as exc:
        return {"ok": False, "status": None, "error": str(exc)}
    try:
        rows = client.kol_feed(limit=1)
        return {
            "ok": True,
            "status": 200,
            "error": None,
            "path": path,
            "rows": len(rows),
            "weight_per_sec": client.limit,
            "plan": PLANS.get(client.limit or 0),
        }
    except GmgnError as exc:
        return {"ok": False, "status": exc.status, "error": str(exc)}
    finally:
        client.close()


# -- from rows to people -----------------------------------------------------------------------------------------------


def wallets_from_feeds(feeds: dict[str, list[dict]]) -> list[dict]:
    """One row a wallet from GMGN's trade feeds (`{"kol": rows, "smartmoney": rows}`): who it is (name, X handle, tags,
    the lists it is on, `bot`: GMGN tags it as one) and what it did in the feed (trades, dollars, coins, how many on
    pump.fun), most dollars first. A trade is a transaction: GMGN gives a coin bought with another token two or three
    rows (the coin, the token it was paid with, sometimes SOL; 100 real KOL rows were 49 transactions), and only the
    coin's row is counted (the one on a launchpad, else the largest). `feed_rows` is how many rows GMGN gave."""
    acc: dict[str, dict] = {}
    for list_name, rows in feeds.items():
        legs: dict[tuple, list[dict]] = {}
        for k, t in enumerate(rows):
            legs.setdefault((t["wallet"], t.get("tx") or k), []).append(t)
        for group in legs.values():
            on_pad = [t for t in group if t.get("launchpad")] or group
            t = max(on_pad, key=lambda r: r.get("usd") or 0.0)
            w = acc.setdefault(
                t["wallet"],
                {
                    "wallet": t["wallet"],
                    "name": None,
                    "twitter": None,
                    "tags": set(),
                    "lists": set(),
                    "feed_rows": 0,
                    "feed_trades": 0,
                    "feed_buys": 0,
                    "feed_sells": 0,
                    "feed_buy_usd": 0.0,
                    "feed_sell_usd": 0.0,
                    "mints": set(),
                    "pump_trades": 0,
                    "last_ts": 0,
                },
            )
            w["name"], w["twitter"] = w["name"] or t.get("name"), w["twitter"] or t.get("twitter")
            w["tags"].update(t.get("tags") or [])
            w["lists"].add(list_name)
            side = "buy" if t.get("side") == "buy" else "sell"
            w["feed_rows"] += len(group)
            w["feed_trades"] += 1
            w[f"feed_{side}s"] += 1
            w[f"feed_{side}_usd"] += t.get("usd") or 0.0
            if t.get("mint"):
                w["mints"].add(t["mint"])
            if any(word in (t.get("launchpad") or "").lower() for word in PUMP_WORDS):
                w["pump_trades"] += 1
            w["last_ts"] = max(w["last_ts"], t.get("ts") or 0)
    out = []
    for w in acc.values():
        w["bot"] = bool(BOT_TAGS & w["tags"])
        w["tags"], w["lists"], w["coins"] = sorted(w["tags"]), sorted(w["lists"]), len(w.pop("mints"))
        out.append(w)
    return sorted(out, key=lambda w: -(w["feed_buy_usd"] + w["feed_sell_usd"]))


def is_kol(row: dict) -> bool:
    """Whether GMGN says the wallet is a known person's: it is on the KOL list or carries the tag."""
    return "kol" in (row.get("lists") or []) or bool(KOL_TAGS & set(row.get("tags") or []))


def known_as(row: dict) -> str | None:
    """The name a wallet gets in our labels: GMGN's name for it, else its X handle; None for a wallet nobody named."""
    return row.get("name") or row.get("twitter") or None


def _realized(row: dict) -> float:
    return row["realized_usd"] if row.get("realized_usd") is not None else float("-inf")


def add_profits(
    client: Gmgn,
    rows: list[dict],
    period: str = "30d",
    stats_top: int = 20,
    log: Callable[[str], None] = lambda m: None,
) -> list[dict]:
    """Put each wallet's profit over `period` on its row (one request a hundred wallets), then the win rate, the coins
    traded and GMGN's own name for it on the `stats_top` rows with the most realized profit (one request each).
    Rows come back most realized profit first. A failed request leaves its rows without those numbers; a rate limit
    ends the asking."""
    by = {p["wallet"]: p for p in rows}
    limited = False
    for i in range(0, len(rows), 100):
        try:
            for r in client.wallet_profits([p["wallet"] for p in rows[i : i + 100]], period=period):
                if r["wallet"] in by:
                    by[r["wallet"]].update(
                        {k: v for k, v in r.items() if k != "wallet"},
                        trades=r["buys"] + r["sells"],
                        days=PERIOD_DAYS.get(period),
                    )
        except GmgnError as exc:
            log(f"gmgn profits: {exc}")
            limited = exc.status == 429
            if limited:
                break
    for p in [] if limited else sorted(rows, key=lambda p: -_realized(p))[: max(0, stats_top)]:
        try:
            s = client.wallet_stats(p["wallet"], period=period)
        except GmgnError as exc:
            log(f"gmgn statistics {p['wallet'][:6]}: {exc}")
            if exc.status == 429:
                break
            continue
        for k in (
            "win_rate",
            "tokens",
            "avg_hold_secs",
            "pnl_ratio",
            "followers",
            "follow_count",
            "funded_by",
            "funded_by_name",
            "created_tokens",
            "last_ts",
        ):
            if s.get(k) is not None:
                p[k] = s[k]
        for k in ("realized_usd", "cost_usd", "buys", "sells", "trades", "unrealized_usd", "days", "period"):
            if p.get(k) is None and s.get(k) is not None:  # GMGN's profit list had no row for it
                p[k] = s[k]
        p["name"], p["twitter"] = p.get("name") or s.get("name"), p.get("twitter") or s.get("twitter")
        p["tags"] = sorted(set(p.get("tags") or []) | set(s.get("tags") or []))
    return sorted(rows, key=lambda p: -_realized(p))


def _listed(client: Gmgn, lists: Iterable[str], feed_limit: int, log: Callable[[str], None]) -> dict[str, list[dict]]:
    """GMGN's lists as trade rows. A list that fails is said and left out; when every list fails the error is raised."""
    feeds: dict[str, list[dict]] = {}
    last: GmgnError | None = None
    for name in lists:
        try:
            feeds[name] = (client.kol_feed if name == "kol" else client.smart_money_feed)(limit=feed_limit)
        except GmgnError as exc:
            last = exc
            log(f"gmgn {name} list: {exc}")
    if not feeds and last is not None:
        raise last
    return feeds


def people(
    client: Gmgn,
    lists: Iterable[str] = ("kol", "smartmoney"),
    feed_limit: int = 200,
    period: str = "30d",
    stats_top: int = 20,
    log: Callable[[str], None] = lambda m: None,
) -> list[dict]:
    """The wallets trading on GMGN's lists right now ("kol": known people; "smartmoney": its own ranking of traders),
    one row each: name, X handle, tags, what it did in the feed, its profit over `period` and, for the `stats_top`
    most profitable, its win rate."""
    return add_profits(client, wallets_from_feeds(_listed(client, lists, feed_limit, log)), period, stats_top, log)


COINS_OWN = frozenset({"creator", "dev_team", "dev", "bundler", "rat_trader"})  # what a coin page calls its insiders


def coin_winners(
    client: Gmgn,
    coins: int = 5,
    per_coin: int = 50,
    interval: str = "1h",
    platforms: Iterable[str] | None = ("Pump.fun",),
    mint: str | None = None,
    log: Callable[[str], None] = lambda m: None,
) -> list[dict]:
    """The wallets that made the most on one coin (`mint`) or on GMGN's `coins` hottest coins of the launchpads in
    `platforms` (None: every launchpad): one row a wallet with the dollars it made over those coins, on how many of
    them it won, and its tags. Wallets a coin page marks as the coin's own (creator, dev team), as bundlers or as
    insiders are left out: their profit is not a trade anyone can follow."""
    if mint:
        mints = [mint]
    else:
        hot = client.trending(interval, limit=max(1, coins), order_by="volume", platforms=list(platforms or []) or None)
        mints = [c["mint"] for c in hot][: max(1, coins)]
    acc: dict[str, dict] = {}
    for m in mints:
        try:
            traders = client.token_traders(m, limit=per_coin)
        except GmgnError as exc:
            log(f"gmgn top traders {m[:6]}: {exc}")
            if exc.status == 429:
                break
            continue
        for t in traders:
            if COINS_OWN & set(t["coin_tags"]):
                continue
            w = acc.setdefault(
                t["wallet"],
                {
                    "wallet": t["wallet"],
                    "name": None,
                    "twitter": None,
                    "tags": set(),
                    "coin_tags": set(),
                    "coin_profit_usd": 0.0,
                    "coin_hits": 0,
                    "coin_mints": [],
                },
            )
            w["name"], w["twitter"] = w["name"] or t["name"], w["twitter"] or t["twitter"]
            w["tags"].update(t["tags"])
            w["coin_tags"].update(t["coin_tags"])
            w["coin_profit_usd"] += t["profit_usd"] or 0.0
            w["coin_hits"] += 1 if (t["profit_usd"] or 0.0) > 0 else 0
            w["coin_mints"].append(m)
    for w in acc.values():
        w["tags"], w["coin_tags"] = sorted(w["tags"]), sorted(w["coin_tags"])
    return sorted(acc.values(), key=lambda w: -w["coin_profit_usd"])


def candidates(
    client: Gmgn,
    *,
    feed_limit: int = 200,
    period: str = "30d",
    stats_top: int = 40,
    trending_coins: int = 5,
    traders_per_coin: int = 50,
    max_wallets: int = 300,
    exclude: Iterable[str] = (),
    now: float | None = None,
    log: Callable[[str], None] = lambda m: None,
) -> list[dict]:
    """The wallets GMGN ranks, for the discover stage: everyone trading on its KOL and smart-money lists, plus the
    wallets that made the most on its hottest pump.fun coins, one row each with who it is and its numbers over
    `period` (`trades`, `win_rate`, `realized_usd`, `tokens`, `days`: the names the other sources use). `exclude`
    holds the platform wallets we know. The lab at our landing decides who is copied; this only says who to look at."""
    found = {p["wallet"]: p for p in wallets_from_feeds(_listed(client, ("kol", "smartmoney"), feed_limit, log))}
    if trending_coins > 0:
        try:
            winners = coin_winners(client, coins=trending_coins, per_coin=traders_per_coin, log=log)
        except GmgnError as exc:
            winners = []
            log(f"gmgn hottest coins: {exc}")
        for w in winners:
            if w["coin_profit_usd"] <= 0:
                continue
            row = found.setdefault(
                w["wallet"], {"wallet": w["wallet"], "name": None, "twitter": None, "tags": [], "lists": []}
            )
            row["name"], row["twitter"] = row.get("name") or w["name"], row.get("twitter") or w["twitter"]
            row["tags"] = sorted(set(row["tags"]) | set(w["tags"]))
            row["lists"] = sorted({*row["lists"], "top_traders"})
            row.update({k: w[k] for k in ("coin_tags", "coin_profit_usd", "coin_hits", "coin_mints")})
    skip = set(exclude)
    bots = [w for w, p in found.items() if BOT_TAGS & set(p.get("tags") or [])]
    if bots:  # 3 of the 42 wallets on the real smart-money list of 12 Nov
        log(f"gmgn: {len(bots)} wallets GMGN tags as bots ({', '.join(sorted(BOT_TAGS))}) left out of the candidates")
        skip |= set(bots)
    rows = add_profits(
        client, [p for w, p in found.items() if w not in skip][: max(0, max_wallets)], period, stats_top, log
    )
    stamp = int(time.time() if now is None else now)
    for p in rows:
        p.update(source="gmgn", scanned_at=stamp)
    return rows


def who(client: Gmgn, name: str) -> list[dict]:
    """The wallets GMGN finds for a person's name or X handle. The search is fuzzy, so each row says whether its
    handle is exactly the name asked for (`exact`); those come first, the wallet most GMGN users follow on top (a
    real search for one handle gave five wallets, followed by 74,887, 48,070, 989, 17 and 0 users)."""
    return _by_name(client.search(name)["wallets"], name)


def _by_name(wallets: list[dict], name: str) -> list[dict]:
    asked = str(name).strip().lstrip("@").lower()
    rows = [dict(w, exact=(w["twitter"] or "").lower() == asked) for w in wallets]
    return sorted(rows, key=lambda w: (not w["exact"], -(w.get("follow_count") or 0), -len(w["tags"])))


def _quartiles(values: list[float]) -> dict:
    xs = sorted(values)

    def at(q: float) -> float:
        pos = q * (len(xs) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        return round(xs[lo] + (xs[hi] - xs[lo]) * (pos - lo), 9)

    return {"p25": at(0.25), "median": at(0.5), "p75": at(0.75), "max": xs[-1]} if xs else {}


def _total(rows: list[dict], key: str) -> float | None:
    """A transaction's number from its rows' shares; None when a row came without it."""
    parts = [r[key] for r in rows]
    return None if any(p is None for p in parts) else round(sum(parts), 9)


def transactions(rows: list[dict]) -> list[dict]:
    """A wallet's history rows (`wallet_activity`) as one row a transaction, in the order they came. GMGN gives a
    transaction that swaps twice (a coin bought with another token, and that token bought) two rows and half the
    transaction's fee on each: added up, `priority_sol`, `tip_sol` and `gas_sol` are the transaction's own, as our
    tape records them. The transaction is named after its coin (the row on a launchpad, else the first row)."""
    by: dict[Any, list[dict]] = {}
    for k, a in enumerate(rows):
        by.setdefault(a.get("tx") or k, []).append(a)
    out = []
    for group in by.values():
        coin = next((a for a in group if a.get("platform")), group[0])
        priority, tip = _total(group, "priority_fee"), _total(group, "tip_fee")
        out.append(
            {
                "ts": coin["ts"],
                "kind": coin["kind"],
                "mint": coin["mint"],
                "symbol": coin["symbol"],
                "usd": coin["usd"],
                "rows": len(group),
                "priority_sol": priority,
                "tip_sol": tip,
                "bid_sol": None if priority is None or tip is None else round(priority + tip, 9),
                "gas_sol": _total(group, "gas_sol"),
                "gas_usd": _total(group, "gas_usd"),
                "tx": coin["tx"],
            }
        )
    return out


def fees(client: Gmgn, wallet: str, pages: int = 2) -> dict:
    """What a wallet pays to land its trades, in SOL, from its own history (50 rows a page, newest first): each buy
    and sale as one transaction with its priority fee, its tip and the two together (`bid_sol`), and over the buys and
    over the sales the quartiles of each. The owner's rule: if our fee is not higher than theirs, they buy before us.
    A trade that came without its fees is counted (`n`) and left out of the quartiles (`with_fees`). When more
    history remains after the last page read, the oldest transaction may be missing a row (its fee would read as
    half): it is listed with `cut` and counted nowhere."""
    rows: list[dict] = []
    cursor = None
    for _ in range(max(1, pages)):
        page = client.wallet_activity(wallet, limit=50, cursor=cursor, kinds=["buy", "sell"])
        rows += [a for a in page["rows"] if a["kind"] in ("buy", "sell")]
        cursor = page["next"]
        if not cursor:
            break
    trades = transactions(rows)
    more = bool(cursor)
    if more and trades:
        trades[-1]["cut"] = True

    def side(kind: str) -> dict:
        mine = [t for t in trades if t["kind"] == kind and not t.get("cut")]
        paid = [t for t in mine if t["bid_sol"] is not None]
        out: dict[str, Any] = {"n": len(mine), "with_fees": len(paid)}
        if paid:
            out.update(
                priority_sol=_quartiles([t["priority_sol"] for t in paid]),
                tip_sol=_quartiles([t["tip_sol"] for t in paid]),
                bid_sol=_quartiles([t["bid_sol"] for t in paid]),
            )
        gas = [t["gas_usd"] for t in mine if t["gas_usd"] is not None]
        if gas:
            out["gas_usd"] = _quartiles(gas)
        return out

    return {
        "wallet": wallet,
        "unit": FEE_UNIT,
        "buys": side("buy"),
        "sells": side("sell"),
        "more": more,
        "left_out_at_the_cut": int(more and bool(trades)),
        "trades": trades,
    }


def label_people(store: Any, rows: list[dict]) -> int:
    """Name the wallets GMGN names, the way a tracker file does, so the lab's groups see them: kind `kol` for a known
    person (GMGN's KOL list or tag), kind `wallet` for any other wallet with a name or an X handle. A wallet somebody
    else labelled (the owner's tracker, a service, a tool of ours) keeps that label; only a label this source wrote
    is brought up to date. Each labelled row gets `labeled`: its kind. Returns how many labels were written."""
    written = 0
    for row in rows:
        name = known_as(row)
        if not name or not is_wallet(row.get("wallet")):
            continue
        had = store._q("SELECT label, kind, source FROM labels WHERE address=?", (row["wallet"],))
        if had and had[0][2] != "gmgn":
            continue
        kind = "kol" if is_kol(row) else "wallet"
        row["labeled"] = kind
        if not had or (had[0][0], had[0][1]) != (name, kind):
            store.set_label(row["wallet"], name, kind, "gmgn")
            written += 1
    return written


# -- what GMGN said about one wallet, kept ----------------------------------------------------------------------------

WALLET_KEY = "gmgn:wallet:"  # coverage record: who GMGN last said a wallet is (followers, tags, funder), and when
FEES_KEY = "gmgn:fees:"  # coverage record: what a wallet pays to land its trades (the quartiles of `fees`), and when
PROPOSED_KEY = "gmgn:proposed_companions:"  # coverage record: the wallets a wallet's funder ties it to. A proposal.
KEPT_SECS = 86400  # a kept answer is asked again after a day
COMPANION_LOOKBACK_SECS = 3 * 86400  # the tape the companion rule is read on beside a proposal
_KEPT = (
    "follow_count",
    "remark_count",
    "tags",
    "name",
    "twitter",
    "followers",
    "last_ts",
    "funded_by",
    "funded_by_name",
    "funded_ts",
    "funded_sol",
    "funding_tx",
)
PROPOSAL_NOTE = (
    "A proposal, not a fact: these wallets share a funder with it. Nothing reads this list. A lab scores a copy "
    "behind a wallet's companions as the tape finds them (analysis/companions.py: in its block on half its buys), "
    "and that rule is unchanged; tape_says_companion is what it says of each wallet today."
)
SERVICE_NOTE = (
    "The funder is a service ({name}): an exchange or a trading app funds thousands of wallets, so sharing it ties "
    "no two wallets together. Nobody is proposed."
)
SERVICE_KINDS = ("service", "platform", "router", "exchange")


def remember_wallet(store: Any, row: dict, now: float | None = None) -> dict | None:
    """Keep what a look-up said about a wallet (a `wallet_stats` row, or a row with the same names): how many GMGN
    users follow it, its tags, its name and the wallet that funded it. A row without the wallet's card writes
    nothing. Returns the record kept."""
    wallet = row.get("wallet")
    if not is_wallet(wallet) or (row.get("follow_count") is None and not row.get("funded_by")):
        return None
    rec = {k: row.get(k) for k in _KEPT} | {"ts": int(time.time() if now is None else now)}
    store.set_coverage(WALLET_KEY + wallet, rec)
    return rec


def known_wallet(store: Any, wallet: str, max_age: float | None = None, now: float | None = None) -> dict | None:
    """What GMGN last said about `wallet` (`remember_wallet`), or None: never asked, or asked more than `max_age`
    seconds ago."""
    rec = store.coverage(WALLET_KEY + wallet)
    if not rec or rec.get("ts") is None:
        return None
    if max_age is not None and (time.time() if now is None else now) - float(rec["ts"]) > max_age:
        return None
    return rec


def _same_funder(store: Any, funder_wallet: str) -> dict[str, str]:
    """The wallets known to be funded by `funder_wallet`, each with where that is known from: a kept GMGN look-up,
    a discovery row of GMGN's, or our own funder record (the first payment into a wallet, read from the chain)."""
    out: dict[str, str] = {}
    for w in store.wallets_funded_by(funder_wallet):
        out[w] = "our funder record"
    for (w,) in store._q(
        "SELECT wallet FROM candidates WHERE json_extract(stats, '$.gmgn.funded_by') = ?", (funder_wallet,)
    ):
        out[w] = "gmgn (discovery)"
    for key, data in store._q(
        "SELECT key, data FROM coverage WHERE key >= ? AND key < ?", (WALLET_KEY, WALLET_KEY[:-1] + ";")
    ):
        try:
            if json.loads(data).get("funded_by") == funder_wallet:
                out[key[len(WALLET_KEY) :]] = "gmgn"
        except ValueError:
            continue
    return out


def funder(
    client: Gmgn,
    store: Any,
    wallet: str,
    now: float | None = None,
    mates: Iterable[str] | None = None,
    max_age: float = KEPT_SECS,
) -> dict:
    """The wallet that funded `wallet`, by GMGN (one look-up, or yesterday's kept answer), and the wallets we track
    that the same funder ties to it: the funder itself, the wallets it also funded, the wallets `wallet` funded.
    They are PROPOSED companions: printed, and stored under `PROPOSED_KEY`, and read by nothing. `mates`: the wallet's
    companions by the tape's rule, when the caller has them; each proposal then says whether the tape agrees. A
    funder GMGN or our labels name as a service proposes nobody."""
    now = time.time() if now is None else now
    rec = known_wallet(store, wallet, max_age, now)
    if rec is None:
        rec = remember_wallet(store, client.wallet_stats(wallet, "7d"), now) or {}
    src, name = rec.get("funded_by"), rec.get("funded_by_name")
    labels = store.label_kinds()
    if src and not name and labels.get(src, ("", ""))[0] in SERVICE_KINDS:
        name = labels[src][1]
    out: dict[str, Any] = {
        "wallet": wallet,
        "funded_by": src,
        "funded_by_name": name,
        "funded_sol": rec.get("funded_sol"),
        "funded_ts": rec.get("funded_ts"),
        "funding_tx": rec.get("funding_tx"),
        "asked_ts": rec.get("ts"),
        "proposed": [],
        "untracked_with_the_same_funder": 0,
    }
    if not src:
        out["note"] = "GMGN names no funder for this wallet."
    elif name:
        out["note"] = SERVICE_NOTE.format(name=name)
    else:
        found: dict[str, tuple[str, str]] = {}  # wallet -> (why, known from)
        for w, where in _same_funder(store, wallet).items():
            found[w] = ("this wallet funded it", where)
        for w, where in _same_funder(store, src).items():
            found.setdefault(w, ("same funder", where))
        found[src] = ("it funded this wallet", "gmgn")
        found.pop(wallet, None)
        friends = None if mates is None else set(mates)
        for w, (why, where) in sorted(found.items()):
            kind, label = labels.get(w, (None, None))
            tracked = kind in ("wallet", "kol", "cand") or bool(
                store._q("SELECT 1 FROM candidates WHERE wallet=?", (w,))
            )
            if not tracked:
                out["untracked_with_the_same_funder"] += 1
                continue
            out["proposed"].append(
                {
                    "wallet": w,
                    "label": label,
                    "why": why,
                    "known_from": where,
                    "tape_says_companion": None if friends is None else w in friends,
                }
            )
        out["note"] = PROPOSAL_NOTE
    store.set_coverage(
        PROPOSED_KEY + wallet,
        {"ts": int(now), "funded_by": src, "funded_by_name": name, "proposed": out["proposed"]},
    )
    return out


def _wallet_summary(got: dict) -> dict:
    """The head of `gmgn wallet`: who the wallet is and what it made, in plain names, from the look-up's parts."""

    def ok(part: Any) -> dict:
        return part if isinstance(part, dict) and "error" not in part else {}

    s7, s30, total = ok(got.get("stats_7d")), ok(got.get("stats_30d")), ok(got.get("all_time"))
    card = s7 or s30
    return {
        "name": card.get("name"),
        "twitter": card.get("twitter"),
        "tags": card.get("tags") or [],
        "followed_by_gmgn_users": card.get("follow_count"),
        "x_followers": card.get("followers"),
        "last_active_ts": card.get("last_ts"),
        "sol_balance": card.get("sol_balance"),
        "funded_by": card.get("funded_by"),
        "funded_by_name": card.get("funded_by_name"),
        "funded_sol": card.get("funded_sol"),
        "funded_ts": card.get("funded_ts"),
        "realized_profit_usd": {
            "7d": s7.get("realized_usd"),
            "30d": s30.get("realized_usd"),
            "all_time": total.get("all_time_realized_usd"),
        },
        "win_rate": {"7d": s7.get("win_rate"), "30d": s30.get("win_rate")},
        "trades": {"7d": s7.get("trades"), "30d": s30.get("trades")},
        "fees_paid_usd": {
            p: {"buys": s.get("buy_fees_usd"), "sells": s.get("sell_fees_usd")} for p, s in (("7d", s7), ("30d", s30))
        },
        "units": "profit and fees in dollars (fees: network fee, tip and the exchange's cut together); win_rate is "
        "a share of 1 over the coins traded; sol_balance and funded_sol in SOL; times are unix seconds",
    }


# -- the record of GMGN answering --------------------------------------------------------------------------------------

LAST_OK_KEY = "gmgn:last_ok"  # coverage record: when GMGN last answered a request of ours


def mark_answered(store: Any, weight: int, now: float | None = None) -> None:
    """A client's `counter`: add the weight to the month's tally (provider "gmgn") and note that GMGN answered now."""
    now = time.time() if now is None else now
    store.usage_add("gmgn", int(weight), now=now)
    store.set_coverage(LAST_OK_KEY, {"ts": int(now)})


def _ago(secs: float) -> str:
    return (
        f"{secs / 60:.0f} min ago"
        if secs < 5400
        else f"{secs / 3600:.0f} h ago"
        if secs < 172800
        else f"{secs / 86400:.0f} d ago"
    )


def doctor_row(store: Any, now: float | None = None) -> dict:
    """The doctor's line for GMGN: whether the key is there, whether it works now (one read of weight 1), which plan
    answers, when GMGN last answered and the weight used this month."""
    now = time.time() if now is None else now
    if not os.environ.get(ENV_KEY):
        return {
            "ok": False,
            "detail": f"{ENV_KEY} not set (optional: GMGN's KOL and smart-money wallets, wallet and coin look-ups)",
        }
    last = store.coverage(LAST_OK_KEY).get("ts")
    out = ping(counter=lambda w: mark_answered(store, w, now))
    used = store.usage_month("gmgn", now=now)
    if out["ok"]:
        return {
            "ok": True,
            "detail": f"the key works: {out.get('plan') or 'unknown'} plan ({out.get('weight_per_sec')} "
            f"weight a second); {used:,} weight this month",
        }
    before = f"last answered {_ago(now - last)}" if last else "GMGN has never answered this machine"
    return {"ok": False, "detail": f"{ENV_KEY} set but the API answered: {out['error']}; {before}"}


# -- the command line --------------------------------------------------------------------------------------------------

ACTIONS = (
    "ping",
    "kol",
    "smart-money",
    "top-traders",
    "who",
    "wallet",
    "fees",
    "funder",
    "created",
    "token",
    "trending",
    "new-pairs",
    "candles",
    "gas",
    "signals",
    "callouts",
)
NEEDS_ADDRESS = {
    "wallet": "the wallet's address",
    "fees": "the wallet's address",
    "funder": "the wallet's address",
    "created": "the wallet's address",
    "token": "the coin's mint address",
    "candles": "the coin's mint address",
}
DEFAULT_PLATFORMS = ("Pump.fun",)


def _part(read: Callable[[], Any]) -> Any:
    """One part of an answer made of several reads: a refusal becomes that part's `error`, a rate limit ends it all."""
    try:
        return read()
    except GmgnError as exc:
        if exc.status == 429 or exc.status in (401, None):
            raise
        return {"error": str(exc)}


def action(
    client: Gmgn,
    name: str,
    target: str | None = None,
    *,
    period: str = "30d",
    limit: int | None = None,
    stats: int = 20,
    interval: str = "1h",
    resolution: str = "1m",
    hours: float | None = None,
    platforms: list[str] | None = None,
    tag: str | None = None,
    order_by: str | None = None,
    coins: int = 5,
    kinds: list[str] | None = None,
    pages: int = 2,
    raw: bool = False,
    now: float | None = None,
    log: Callable[[str], None] = lambda m: None,
    store: Any = None,
) -> dict:
    """What `snipetech gmgn <name>` prints. `platforms` None means pump.fun's launchpad; ["all"] means every one.
    `store`: where a wallet's look-up is kept (`remember_wallet`) and a funder's proposal stored; `funder` needs it."""
    lanes = None if platforms and "all" in platforms else list(platforms or DEFAULT_PLATFORMS)
    if name in NEEDS_ADDRESS and not is_wallet(target):
        raise ValueError(f"gmgn {name}: give {NEEDS_ADDRESS[name]} (got {target!r})")
    if name == "who":
        if not target or not target.strip():
            raise ValueError("gmgn who: give a name or an X handle")
        if raw:
            return {"ok": True, "raw": client.search(target, raw=True)}
        found = client.search(target)
        return {"ok": True, "name": target, "wallets": _by_name(found["wallets"], target), "coins": found["coins"]}
    if target is not None and not is_wallet(target):
        raise ValueError(f"gmgn {name}: {target!r} is not an address")
    if name == "fees":
        return {"ok": True, **fees(client, target, pages=pages)}
    if name == "funder":
        if store is None:
            raise ValueError("gmgn funder: needs the database (the proposal is stored there)")
        stamp = time.time() if now is None else now
        mates = None
        try:  # what the tape's own rule says of the wallet: the judge a proposal is put beside
            from .analysis.companions import companions

            mates = companions(store, target, int(stamp) - COMPANION_LOOKBACK_SECS)
        except sqlite3.Error as exc:  # a read that fails leaves the proposals without the tape's word, never unprinted
            log(f"gmgn funder: the tape's companion rule could not be read: {type(exc).__name__}")
        return {"ok": True, **funder(client, store, target, now=stamp, mates=mates)}
    if name == "created":
        made = client.created_tokens(target)
        if raw or not isinstance(made, dict):
            return {"ok": True, "raw": made}
        return {
            "ok": True,
            "wallet": target,
            "on_curve": _int(made.get("inner_count")),
            "left_curve": _int(made.get("open_count")),
            "last_created_ts": _ts(made.get("last_create_timestamp")),
            "best": made.get("creator_ath_info"),
            "coins": _rows(made, "tokens"),
        }
    if name == "gas":
        if raw:
            return {"ok": True, "raw": client.gas_price(raw=True)}
        return {"ok": True, "presets": client.gas_price(), "unit": "SOL (the fee a GMGN user's trade carries)"}
    if name in ("kol", "smart-money"):
        which = "kol" if name == "kol" else "smartmoney"
        if raw:
            read = client.kol_feed if which == "kol" else client.smart_money_feed
            return {"ok": True, "raw": read(limit=limit or 200, raw=True)}
        rows = people(client, (which,), feed_limit=limit or 200, period=period, stats_top=stats, log=log)
        return {"ok": True, "list": which, "period": period, "wallets": rows}
    if name == "top-traders":
        if raw and target:
            return {
                "ok": True,
                "raw": client.token_traders(
                    target, limit=limit or 50, order_by=order_by or "profit", tag=tag, raw=True
                ),
            }
        winners = coin_winners(
            client, coins=coins, per_coin=limit or 50, interval=interval, platforms=lanes, mint=target, log=log
        )
        mints = list(dict.fromkeys(m for w in winners for m in w["coin_mints"]))
        rows = add_profits(client, winners, period=period, stats_top=stats, log=log)
        return {
            "ok": True,
            "coins": [target] if target else mints,
            "period": period,
            "wallets": sorted(rows, key=lambda w: -w["coin_profit_usd"]),
        }
    if name == "wallet":
        parts = {
            "stats_7d": lambda: client.wallet_stats(target, "7d", raw=raw),
            "stats_30d": lambda: client.wallet_stats(target, "30d", raw=raw),
            "all_time": lambda: client.wallet_profits([target], "all", raw=raw),
            "activity": lambda: client.wallet_activity(target, limit=limit or 50, raw=raw),
        }
        got = {k: _part(read) for k, read in parts.items()}
        if raw:
            return {"ok": True, "raw": got}
        if isinstance(got["all_time"], list):
            got["all_time"] = got["all_time"][0] if got["all_time"] else {}
        page = got["activity"]
        if isinstance(page, dict) and "rows" in page:
            got["activity"], got["next"] = page["rows"], page["next"]
        if store is not None:
            for part in (got["stats_7d"], got["stats_30d"]):
                if isinstance(part, dict) and remember_wallet(store, part, now=now):
                    break
        return {"ok": True, "wallet": target, "summary": _wallet_summary(got), **got}
    if name == "token":
        n = limit or 20
        parts = {
            "info": lambda: client.token_info(target, raw=raw),
            "security": lambda: client.token_security(target),
            "holders": lambda: client.token_holders(target, limit=n, tag=tag, raw=raw),
            "traders": lambda: client.token_traders(target, limit=n, order_by=order_by or "profit", tag=tag, raw=raw),
        }
        got = {k: _part(read) for k, read in parts.items()}
        return {"ok": True, "raw": got} if raw else {"ok": True, "mint": target, **got}
    if name == "trending":
        rows = client.trending(interval, limit=limit or 50, order_by=order_by, platforms=lanes, raw=raw)
        return (
            {"ok": True, "raw": rows} if raw else {"ok": True, "interval": interval, "platforms": lanes, "coins": rows}
        )
    if name == "new-pairs":
        out = client.new_pairs(kinds=kinds, platforms=lanes, limit=limit or 80, raw=raw)
        return {"ok": True, "raw": out} if raw else {"ok": True, "platforms": lanes, **out}
    if name == "candles":
        end = int(time.time() if now is None else now) if hours else None
        start = None if end is None else end - int(hours * 3600)
        rows = client.candles(target, resolution, start=start, end=end, raw=raw)
        return (
            {"ok": True, "raw": rows}
            if raw
            else {"ok": True, "mint": target, "resolution": resolution, "candles": rows}
        )
    if name == "signals":
        return {"ok": True, "raw": client.signals([int(k) for k in kinds] if kinds else None)}
    if name == "callouts":
        return {"ok": True, "raw": client.callouts(limit=limit or 50)}
    raise ValueError(f"gmgn: no such action: {name}")
