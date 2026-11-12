"""Profile the wallets worth following, one by one, from the server's tape (read-only).

Run from the repo with .venv/bin/python; reads SNIPETECH_OPS_URL / SNIPETECH_OPS_KEY (a key PATH). Everything goes
through the ops endpoint's `sql` action (30 s and 500 rows a query, always on an index) into a local SQLite cache
($LEADER_DB, default ./leader_profiles.db), so a second run only reads what is new. Nothing is written to the server.

    tools/leader_profiles.py count                 # every labelled wallet's buys of the last 7 days
    tools/leader_profiles.py pull [--top N] [W..]  # their trades, and the price path after each first buy of a coin
    tools/leader_profiles.py blocks W [W..]        # for the wallets profiled in full: every trade of the coin in the
                                                   # wallet's block and the next 5, and the coin's history before
    tools/leader_profiles.py rank                  # one line a wallet (JSON)
    tools/leader_profiles.py profile W [W..]       # the full profile of a wallet (JSON)

What the numbers are (docs/research/2026-11-12-leader-profiles.md):
- a moment = a wallet's first buy of a coin in the window, 1 SOL or more (MIN_SOL);
- its own entry = its buy and, in the same block, its companions' buys (analysis/companions.py: no copy lands
  between transactions sent together); an outsider = any other buyer behind it;
- place +N = behind the N-th outsider buy after its entry, counted across its block and the next 5 (the lab's
  "+Nx"), never cheaper than right behind it;
- the path = the coin's last price at +1, +2, +5, +20 and +60 seconds (5, 9, 23, 93, 280 blocks of 0.214 s) over the
  price at the place: gross, before pump.fun's fees (2.5 % a round trip), the buyer's own price impact and the bid.
Nothing on import: `main()` does the work."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SLOT_SECS = 0.214  # measured on this tape (docs/research/2026-11-12-winning-bots.md)
DAY_SLOTS = 403_200
HORIZONS = {"1s": 5, "2s": 9, "5s": 23, "20s": 93, "60s": 280, "300s": 1400}  # blocks after the wallet's block
MIN_SOL = 1.0  # a first buy under this is no moment (the rules follow buys of 1 SOL or more)
LAND_SLOTS = 5  # blocks behind the wallet's where a late copy still lands: the crowd is counted over them
MIN_OUTSIDER_SOL = 0.1  # a buy under this behind the wallet is dust, no copy: it takes no place and its bid is no bid
PLACES = (0, 2, 6)
BIDS_FROM = 1794414840  # 11 Nov 2026 16:34 UTC: payer, priority and tip are on the tape from here
HEAVY = 40_000  # trades in 7 days: a machine, never profiled
COLS = "sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote, base, q_after, b_after, payer, priority, tip"
BIG = ("ansem", "gake", "cupsey", "dior", "cooker", "remus", "frank", "resellcalendar", "chairman")  # lab.BIG_NAMES
# the copy bots the owner named and the ones of their kind found on 12 Nov: learnt from, never followed
COPY_BOTS = {
    "6Z91Dgiz9E44bMW11rQuMBjt1NwwPoQGXFNZieGfVtAb", "7iyYn4gshakdEUkF2bfV4pU3oaYBmg9ktJwGW8kQ9z96",
    "Fhbh1DTUDKt6qu9zVg8Q5VgnZa1WWWbxBxG6pCoJoggA", "dKMjeGJ4Mk9S3hxdfTThvZ7exks2xMwWqSM5NvN2CTK",
    "2H6DwrqAsETL1PjrT5KAX3MAELx71FuDkKVZj18PaeJf", "3cbuqqiTmcg8GD48uAaLH1NViQwUj8C1Czx9u3xozpaK",
    "HmR7SxDoPTcYFdktbyU4fqmjkvGgXKXSQ3zXfm9Xj6hj", "88UqCRfa4yQyRCmTvw8UbFsALnn32ducJF9csEbUwqPD",
    "GM8tLq2RRn5d8ArXtbwrLpp3ZV9RBUxSjWxSJstqnfsr", "DDb4vLEkQs78Zs8yxsZuKFAG8C85k5cFmevbMjXxphkK",
    "638PYXz5TuEbPRAhxxsP23KcJxkwTYTNRotVCwS5Yf5x",
}  # fmt: skip


# ---------------------------------------------------------------- pure parts (tests/test_leader_profiles.py)


def is_big(name: str) -> bool:
    flat = "".join(ch for ch in str(name).lower() if ch.isalnum())
    return flat.startswith(BIG)


def price(q: float, b: float) -> float:
    """Lamports a coin unit: the pool's SOL over its coins (a curve's virtual reserves, a pool's real ones)."""
    return q / b if b else 0.0


def mcap_sol(q: float, b: float) -> float:
    """Market cap in SOL of a billion coins with 6 decimals at this price."""
    return price(q, b) * 1e6


def pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = (len(xs) - 1) * p / 100.0
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def capped_mean(xs, cap=1.0):
    """The mean with every value held inside -cap..+cap, so one coin that went up tenfold does not make a wallet."""
    xs = [max(-cap, min(cap, x)) for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


PUMP_FEE = 0.0125  # pump.fun's fee on a buy and on a sale (2.5 % a round trip, docs/research)
SIZES = (2.0, 5.0, 10.0)


def sized_return(q: float, b: float, q_t: float, b_t: float, sol: float) -> tuple[float, float]:
    """What a buy of `sol` SOL into the pool (q, b) returns when sold in one sale into the tape's later pool
    (q_t, b_t), after pump.fun's fee both ways, as a share of the SOL spent: (low, high). High = the others did what
    they did in coins, so the pool is short of the coins we hold and gives our SOL back with the move (the simulator's
    upper bound, sim/market.py); low = sold into the tape's pool as it is, as if our SOL had never been in it. The
    lab's replay lies between the two. Before the bid and the transactions."""
    x = sol * 1e9 * (1 - PUMP_FEE)
    tok = b * x / (q + x)
    k = q_t * b_t
    low = k * (1 / b_t - 1 / (b_t + tok))
    high = k * (1 / (b_t - tok) - 1 / b_t) if tok < 0.5 * b_t else low
    return low * (1 - PUMP_FEE) / (sol * 1e9) - 1, high * (1 - PUMP_FEE) / (sol * 1e9) - 1


def companions_from_blocks(moments: list[dict], wallet: str, places: int = 5, share: float = 0.5, least: int = 5,
                           ahead_share: float = 0.25) -> set[str]:  # fmt: skip
    """analysis/companions.py's rule on blocks already read: `moments` = [{"pos": the wallet's place in the block,
    "buys": [(user, pos), ...] every buy of the coin in that block}]. A companion buys in the next `places`
    transactions behind the wallet on `share` of its first buys (`least` at the least), or is in its block on `share`
    of them, ahead of it on `ahead_share` of the shared blocks and behind it on as many."""
    if len(moments) < least:
        return set()
    near: Counter = Counter()
    shared: Counter = Counter()
    ahead: Counter = Counter()
    for m in moments:
        first: dict[str, int] = {}
        close: set[str] = set()
        for user, at in sorted(m["buys"], key=lambda r: r[1]):
            if user == wallet:
                continue
            first.setdefault(user, at)
            if m["pos"] < at <= m["pos"] + places:
                close.add(user)
        for u in close:
            near[u] += 1
        for u, at in first.items():
            shared[u] += 1
            if at < m["pos"]:
                ahead[u] += 1
    need = max(least, share * len(moments))
    out = {u for u, k in near.items() if k >= need}
    for u, k in shared.items():
        before = ahead[u]
        if k >= need and before >= ahead_share * k and k - before >= ahead_share * k:
            out.add(u)
    return out


def place_states(rows: list[dict], wallet: str, slot: int, pos: int, mates: set[str], skip: set[str] = frozenset(),
                 places=PLACES) -> dict:  # fmt: skip
    """Where a copy lands behind a wallet's buy. `rows`: the coin's trades from the wallet's block on, in order
    (dicts with slot, pos, user, is_buy, q_after, b_after, quote, priority, tip). Returns the wallet's own entry (its
    buy and its companions' buys in its block), the outsiders behind it and the pool at each place: behind the N-th
    outsider buy after the entry, counted across the blocks in `rows`, never cheaper than right behind the entry."""
    own_i = None
    for i, r in enumerate(rows):
        if r["slot"] == slot and r["user"] == wallet and r["is_buy"] and r["pos"] >= pos:
            own_i = i
            break
    if own_i is None:
        return {}
    last_own = own_i
    for j in range(own_i + 1, len(rows)):
        if rows[j]["slot"] != slot:
            break
        if rows[j]["is_buy"] and (rows[j]["user"] in mates or rows[j]["user"] == wallet):
            last_own = j
    after = rows[last_own + 1 :]
    outs = [
        (j, r) for j, r in enumerate(rows[own_i + 1 :], own_i + 1)
        if r["is_buy"] and r["user"] != wallet and r["user"] not in skip
        and r["quote"] >= MIN_OUTSIDER_SOL * 1e9
        and not (r["slot"] == slot and r["user"] in mates)
    ]  # fmt: skip
    floor = rows[last_own]
    at = {}
    for n in places:
        cand = floor
        late = [j for j, _ in outs if j > last_own]
        if n > 0 and late:
            cand = rows[max(last_own, late[min(n, len(late)) - 1])]
        # never cheaper than right behind the entry (a dump after it is not a better place)
        if price(cand["q_after"], cand["b_after"]) < price(floor["q_after"], floor["b_after"]):
            cand = floor
        at[n] = (cand["q_after"], cand["b_after"])
    return {
        "own": rows[own_i],
        "entry_end": floor,
        "at": at,
        "outsiders": [r for _, r in outs],
        "same_block": [r for _, r in outs if r["slot"] == slot],
        "after": after,
    }


def round_trips(trades: list[tuple]) -> list[dict]:
    """A wallet's finished trades from its own rows [(slot, ts, mint, is_buy, quote, base)], oldest first: from a buy
    with no coins held until 99 % of the coins bought are sold. SOL as the tape has it (pump.fun's fee included)."""
    by: dict[str, list] = defaultdict(list)
    for r in trades:
        by[r[2]].append(r)
    out = []
    for mint, rs in by.items():
        held = 0
        cur = None
        for slot, ts, _m, is_buy, quote, base in rs:
            if is_buy:
                if cur is None:
                    cur = {"mint": mint, "slot": slot, "ts": ts, "in": 0, "out": 0, "bought": 0, "buys": 0, "sells": 0,
                           "first_sell": None, "last_sell": None, "first_sell_share": None}  # fmt: skip
                cur["in"] += quote
                cur["bought"] += base
                cur["buys"] += 1
                held += base
            elif cur is not None:
                cur["out"] += quote
                cur["sells"] += 1
                if cur["first_sell"] is None:
                    cur["first_sell"] = slot
                    cur["first_sell_share"] = base / cur["bought"] if cur["bought"] else None
                cur["last_sell"] = slot
                held -= base
                if held <= 0.01 * cur["bought"]:
                    cur["done"] = True
                    out.append(cur)
                    cur, held = None, 0
        if cur is not None:
            cur["done"] = False
            out.append(cur)
    return out


# ---------------------------------------------------------------- the server


class Tape:
    def __init__(self, db_path: str):
        from snipetech.ops import call

        self._call = call
        self.url, self.key = os.environ.get("SNIPETECH_OPS_URL", ""), os.environ.get("SNIPETECH_OPS_KEY", "")
        if not self.url or not self.key:
            raise SystemExit("set SNIPETECH_OPS_URL and SNIPETECH_OPS_KEY (the key's PATH)")
        self.db = sqlite3.connect(db_path, timeout=120)  # several readers may fill one cache
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS labels (address TEXT PRIMARY KEY, name TEXT, kind TEXT);
            CREATE TABLE IF NOT EXISTS act (address TEXT PRIMARY KEY, buys INT, buys1 INT, sol REAL, coins INT,
                sells INT, first_ts INT, last_ts INT);
            CREATE TABLE IF NOT EXISTS heavy (address TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS t (sig TEXT, ev INT, slot INT, pos INT, ts INT, mint TEXT, venue TEXT,
                user TEXT, is_buy INT, quote INT, base INT, q_after INT, b_after INT, payer TEXT, priority INT,
                tip INT, PRIMARY KEY (sig, ev));
            CREATE INDEX IF NOT EXISTS t_ms ON t(mint, slot, pos, ev);
            CREATE INDEX IF NOT EXISTS t_us ON t(user, slot, pos);
            CREATE TABLE IF NOT EXISTS pulled (address TEXT PRIMARY KEY, rows INT);
            CREATE TABLE IF NOT EXISTS path (user TEXT, mint TEXT, slot INT, pos INT, data TEXT,
                PRIMARY KEY (user, mint));
            CREATE TABLE IF NOT EXISTS blocks (mint TEXT, slot INT, PRIMARY KEY (mint, slot));
            CREATE TABLE IF NOT EXISTS hist (user TEXT, mint TEXT, data TEXT, PRIMARY KEY (user, mint));
            """
        )

    def sql(self, query: str, retries: int = 2) -> dict:
        err = None
        for _ in range(retries + 1):
            try:
                return self._call(self.url, self.key, "sql", query=query)
            except Exception as e:
                err = e
                if "interrupted" in str(e) or "syntax" in str(e).lower() or "no such" in str(e):
                    raise
                time.sleep(3)
        raise err

    def rows(self, query: str, retries: int = 2) -> list:
        return self.sql(query, retries)["rows"]

    def meta(self, k: str) -> int:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return int(r[0]) if r else 0

    def window(self) -> tuple[int, int, int, int]:
        """(now ts, now slot, first ts, first slot) of the 7 days, fixed at the first call so every part of a run
        reads the same window."""
        if not self.meta("now_ts"):
            now = self.rows(
                "SELECT max(ts), max(slot) FROM trades INDEXED BY trades_ts WHERE ts > strftime('%s','now') - 600"
            )[0]
            vals = {"now_ts": now[0], "now_slot": now[1], "t0": now[0] - 7 * 86400,
                    "s0": now[1] - 7 * DAY_SLOTS - 30_000}  # fmt: skip
            for k, v in vals.items():
                self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, str(v)))
            self.db.commit()
        return self.meta("now_ts"), self.meta("now_slot"), self.meta("t0"), self.meta("s0")

    # -- count

    def count(self) -> None:
        last = ""
        while True:
            rs = self.rows(
                f"SELECT address, label, kind FROM labels WHERE address > '{last}' ORDER BY address LIMIT 500"
            )
            self.db.executemany("INSERT OR REPLACE INTO labels VALUES (?,?,?)", rs)
            if len(rs) < 500:
                break
            last = rs[-1][0]
        self.db.commit()
        _now, _ns, t0, s0 = self.window()
        todo = [r[0] for r in self.db.execute(
            "SELECT address FROM labels WHERE kind IN ('wallet','kol','cand','smart') AND address NOT IN "
            "(SELECT address FROM act) AND address NOT IN (SELECT address FROM heavy)")]  # fmt: skip
        for i in range(0, len(todo), 40):
            part = todo[i : i + 40]
            vals = ",".join(f"('{a}')" for a in part)
            probe = self.rows(
                f"WITH k(a) AS (VALUES {vals}) SELECT k.a, (SELECT count(*) FROM (SELECT 1 FROM trades INDEXED BY "
                f"trades_user WHERE user = k.a AND slot >= {s0} LIMIT {HEAVY})) FROM k"
            )
            hv = {a for a, n in probe if n >= HEAVY}
            self.db.executemany("INSERT OR IGNORE INTO heavy VALUES (?)", [(a,) for a in hv])
            part = [a for a in part if a not in hv]
            if part:
                vals = ",".join(f"('{a}')" for a in part)
                rs = self.rows(
                    f"WITH k(a) AS (VALUES {vals}) SELECT k.a, sum(t.is_buy), sum(t.is_buy AND t.quote >= 1000000000),"
                    " sum(CASE WHEN t.is_buy THEN t.quote ELSE 0 END)/1e9, count(DISTINCT CASE WHEN t.is_buy THEN"
                    " t.mint END), sum(1-t.is_buy), min(t.ts), max(t.ts) FROM k JOIN trades t INDEXED BY trades_user"
                    f" ON t.user = k.a AND t.slot >= {s0} WHERE t.ts >= {t0} GROUP BY k.a"
                )
                got = {r[0] for r in rs}
                self.db.executemany("INSERT OR REPLACE INTO act VALUES (?,?,?,?,?,?,?,?)", rs)
                self.db.executemany("INSERT OR IGNORE INTO act VALUES (?,0,0,0,0,0,NULL,NULL)",
                                    [(a,) for a in part if a not in got])  # fmt: skip
            self.db.commit()
        print(json.dumps({"labelled": self.db.execute("SELECT count(*) FROM act").fetchone()[0],
                          "with_30_buys_of_1_sol": self.db.execute("SELECT count(*) FROM act WHERE buys1>=30").fetchone()[0],
                          "heavy": [r[0] for r in self.db.execute("SELECT address FROM heavy")]}))  # fmt: skip

    # -- pull

    def pull_trades(self, wallet: str) -> int:
        if self.db.execute("SELECT 1 FROM pulled WHERE address=?", (wallet,)).fetchone():
            return 0
        _now, _ns, t0, s0 = self.window()
        slot, n = s0, 0
        while True:
            rs = self.rows(
                f"SELECT {COLS} FROM trades INDEXED BY trades_user WHERE user='{wallet}' AND slot >= {slot}"
                " ORDER BY slot LIMIT 500"
            )
            self.db.executemany("INSERT OR IGNORE INTO t VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                [r for r in rs if r[4] >= t0])  # fmt: skip
            n += len(rs)
            if len(rs) < 500:
                break
            slot = rs[-1][2] if rs[-1][2] != slot else slot + 1
        self.db.execute("INSERT OR REPLACE INTO pulled VALUES (?,?)", (wallet, n))
        self.db.commit()
        return n

    def moments(self, wallet: str, min_sol: float = MIN_SOL) -> list[tuple]:
        """(mint, slot, pos, quote, venue, ts, q_after, b_after) of the wallet's first buy of each coin in the window,
        when it is `min_sol` or more and the wallet sold none of the coin before it (a coin held from before the
        window is no first buy)."""
        seen: set[str] = set()
        out = []
        for mint, slot, pos, quote, venue, ts, q, b, is_buy in self.db.execute(
            "SELECT mint, slot, pos, quote, venue, ts, q_after, b_after, is_buy FROM t WHERE user=? ORDER BY slot, pos, ev",
            (wallet,),
        ):
            if mint in seen:
                continue
            seen.add(mint)
            if is_buy and quote >= min_sol * 1e9:
                out.append((mint, slot, pos, quote, venue, ts, q, b))
        return out

    def pull_paths(self, wallet: str, batch: int = 20) -> int:
        todo = [m for m in self.moments(wallet)
                if not self.db.execute("SELECT 1 FROM path WHERE user=? AND mint=?", (wallet, m[0])).fetchone()]  # fmt: skip
        n = 0
        for i in range(0, len(todo), batch):
            n += self._paths(wallet, todo[i : i + batch])
        return n

    def _paths(self, wallet: str, part: list[tuple]) -> int:
        vals = ",".join(f"({j},'{m[0]}',{m[1]},{m[2]})" for j, m in enumerate(part))
        tm = "FROM trades INDEXED BY trades_mint WHERE mint = k.m"
        last = "ORDER BY slot DESC, pos DESC, ev DESC LIMIT 1"
        hs = ", ".join(
            f"(SELECT q_after||','||b_after||','||slot {tm} AND slot <= k.s + {h} {last})" for h in HORIZONS.values()
        )
        q = (
            f"WITH k(j, m, s, p) AS (VALUES {vals}) SELECT k.j,"
            f" (SELECT q_after||','||b_after {tm} AND (slot, pos) < (k.s, k.p) {last}),"
            f" {hs},"
            f" (SELECT max(q_after*1.0/b_after)||','||min(q_after*1.0/b_after)||','||count(*)||','||sum(is_buy)||','||"
            f"count(DISTINCT CASE WHEN is_buy THEN user END) {tm} AND slot > k.s AND slot <= k.s + 280),"
            f" (SELECT count(DISTINCT user)||','||count(*) {tm} AND slot = k.s AND pos < k.p AND is_buy = 1),"
            f" (SELECT count(DISTINCT user)||','||count(*) {tm} AND slot = k.s AND pos > k.p AND is_buy = 1),"
            f" (SELECT count(DISTINCT user) {tm} AND slot > k.s AND slot <= k.s + 5 AND is_buy = 1),"
            f" (SELECT count(DISTINCT user) {tm} AND (slot, pos) > (k.s, k.p) AND slot <= k.s + 9 AND is_buy = 1),"
            f" (SELECT q_after||','||b_after {tm} AND slot = k.s {last}),"
            " (SELECT slot||','||ts||','||creator FROM launches WHERE mint = k.m),"
            " (SELECT slot FROM migrations WHERE mint = k.m)"
            " FROM k"
        )
        try:
            rs = self.rows(q, retries=1)
        except Exception as e:  # noqa: BLE001
            if len(part) == 1:
                print("path failed", wallet[:6], part[0][0][:6], str(e)[:80], file=sys.stderr, flush=True)
                return 0
            h = len(part) // 2
            return self._paths(wallet, part[:h]) + self._paths(wallet, part[h:])
        names = ["pre", *HORIZONS, "w60", "ahead", "behind", "next5", "in2s", "block_end", "launch", "migrated"]
        for r in rs:
            m = part[r[0]]
            self.db.execute("INSERT OR REPLACE INTO path VALUES (?,?,?,?,?)",
                            (wallet, m[0], m[1], m[2], json.dumps(dict(zip(names, r[1:], strict=True)))))  # fmt: skip
        self.db.commit()
        return len(rs)

    # -- blocks and history (the wallets profiled in full)

    def pull_blocks(self, wallet: str, batch: int = 12) -> int:
        keys = [(m[0], m[1] + d) for m in self.moments(wallet) for d in range(LAND_SLOTS + 1)]
        keys = [k for k in keys if not self.db.execute("SELECT 1 FROM blocks WHERE mint=? AND slot=?", k).fetchone()]
        n = 0
        for i in range(0, len(keys), batch * (LAND_SLOTS + 1)):
            n += self._blocks(keys[i : i + batch * (LAND_SLOTS + 1)])
        return n

    def _blocks(self, keys: list[tuple]) -> int:
        vals = ",".join(f"('{m}',{s})" for m, s in keys)
        c = ", ".join("t." + x.strip() for x in COLS.split(","))
        try:
            r = self.sql(
                f"WITH k(mint, slot) AS (VALUES {vals}) SELECT {c} FROM k JOIN trades t INDEXED BY trades_mint"
                " ON t.mint = k.mint AND t.slot = k.slot", retries=1
            )  # fmt: skip
        except Exception:  # noqa: BLE001
            r = {"truncated": True, "rows": []}
            if len(keys) == 1:
                return 0
        if r.get("truncated") and len(keys) > 1:
            h = len(keys) // 2
            return self._blocks(keys[:h]) + self._blocks(keys[h:])
        # one block with over 500 trades of one coin: its first 500 are enough for the places behind the wallet
        self.db.executemany("INSERT OR IGNORE INTO t VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r["rows"])
        self.db.executemany("INSERT OR IGNORE INTO blocks VALUES (?,?)", keys)
        self.db.commit()
        return len(r["rows"])

    def pull_hist(self, wallet: str, batch: int = 10) -> int:
        todo = [m for m in self.moments(wallet)
                if not self.db.execute("SELECT 1 FROM hist WHERE user=? AND mint=?", (wallet, m[0])).fetchone()]  # fmt: skip
        n = 0
        for i in range(0, len(todo), batch):
            n += self._hist(wallet, todo[i : i + batch])
        return n

    def _hist(self, wallet: str, part: list[tuple]) -> int:
        """Before the wallet's buy: trades and SOL bought in the coin, and the tracked wallets (labels of kind wallet
        or kol) that had bought it, with the last one's block. Read backwards from the buy, 3,000 trades at most."""
        vals = ",".join(f"({j},'{m[0]}',{m[1]},{m[2]})" for j, m in enumerate(part))
        q = (
            f"WITH k(j, m, s, p) AS (VALUES {vals}) SELECT k.j, count(*), sum(x.is_buy * x.quote) / 1e9,"
            " count(DISTINCT CASE WHEN x.is_buy AND l.address IS NOT NULL THEN x.user END),"
            " max(CASE WHEN x.is_buy AND l.address IS NOT NULL THEN x.slot END),"
            " group_concat(DISTINCT CASE WHEN x.is_buy AND l.address IS NOT NULL THEN x.user END)"
            " FROM k JOIN (SELECT k2.j AS j, t.user AS user, t.is_buy AS is_buy, t.quote AS quote, t.slot AS slot"
            f" FROM k k2 JOIN trades t INDEXED BY trades_mint ON t.mint = k2.m AND (t.slot, t.pos) < (k2.s, k2.p)) x"
            " ON x.j = k.j LEFT JOIN labels l ON l.address = x.user AND l.kind IN ('wallet','kol') GROUP BY k.j"
        )
        try:
            rs = self.rows(q, retries=1)
        except Exception as e:  # noqa: BLE001
            if len(part) == 1:
                print("hist failed", wallet[:6], part[0][0][:6], str(e)[:80], file=sys.stderr, flush=True)
                self.db.execute("INSERT OR REPLACE INTO hist VALUES (?,?,?)", (wallet, part[0][0], json.dumps(None)))
                self.db.commit()
                return 0
            h = len(part) // 2
            return self._hist(wallet, part[:h]) + self._hist(wallet, part[h:])
        got = {r[0]: r[1:] for r in rs}
        for j, m in enumerate(part):
            r = got.get(j, (0, 0.0, 0, None, None))
            self.db.execute("INSERT OR REPLACE INTO hist VALUES (?,?,?)", (wallet, m[0], json.dumps(
                {"trades": r[0], "sol": r[1] or 0.0, "known": r[2], "last_known_slot": r[3],
                 "known_wallets": (r[4] or "").split(",") if r[4] else []})))  # fmt: skip
        self.db.commit()
        return len(part)


# ---------------------------------------------------------------- reading the cache


def _pair(s):
    if not s:
        return None
    a = str(s).split(",")
    return float(a[0]), float(a[1])


def path_rows(db, wallet: str) -> list[dict]:
    """One dict a moment of `wallet` from the cached paths: its buy, the price right behind its own buy and the gross
    return from there to each horizon."""
    out = []
    own = {r[0]: r[1:] for r in db.execute(
        "SELECT mint, min(slot), quote, venue, ts, q_after, b_after FROM t WHERE user=? AND is_buy=1 GROUP BY mint",
        (wallet,))}  # fmt: skip
    for mint, slot, pos, data in db.execute("SELECT mint, slot, pos, data FROM path WHERE user=?", (wallet,)):
        d = json.loads(data)
        o = db.execute(
            "SELECT quote, venue, ts, q_after, b_after FROM t WHERE user=? AND mint=? AND slot=? AND pos=? AND is_buy=1"
            " ORDER BY ev DESC LIMIT 1", (wallet, mint, slot, pos)).fetchone()  # fmt: skip
        if not o or mint not in own:
            continue
        quote, venue, ts, q, b = o
        p0 = price(q, b)
        row = {"mint": mint, "slot": slot, "pos": pos, "ts": ts, "sol": quote / 1e9, "venue": venue, "p0": p0,
               "mcap": mcap_sol(q, b), "q": q, "b": b}  # fmt: skip
        pre = _pair(d.get("pre"))
        row["own_impact"] = p0 / price(*pre) - 1 if pre and pre[1] else None
        for h in HORIZONS:
            v = d.get(h)
            if v:
                a = v.split(",")
                row["r" + h] = price(float(a[0]), float(a[1])) / p0 - 1 if p0 else None
            else:
                row["r" + h] = None
        w = d.get("w60")
        if w and w.split(",")[0] not in ("", "None"):
            a = w.split(",")
            row["peak60"] = float(a[0]) / p0 - 1 if p0 else None
            row["trough60"] = float(a[1]) / p0 - 1 if p0 else None
            row["trades60"], row["buyers60"] = int(a[2]), int(a[4])
        else:
            row["peak60"] = row["trough60"] = 0.0
            row["trades60"] = row["buyers60"] = 0
        row["ahead"] = int(str(d.get("ahead") or "0,0").split(",")[0])
        row["behind"] = int(str(d.get("behind") or "0,0").split(",")[0])
        row["next5"] = int(d.get("next5") or 0)
        row["in2s"] = int(d.get("in2s") or 0)
        la = d.get("launch")
        if la:
            a = la.split(",")
            row["age"] = ts - int(a[1])
            row["create_block"] = int(a[0]) == slot
            row["creator"] = a[2] == wallet
        else:
            row["age"], row["create_block"], row["creator"] = None, False, False
        row["migrated"] = d.get("migrated") is not None and int(d["migrated"]) <= slot
        end = _pair(d.get("block_end"))
        # the last place of the wallet's own block: behind every buyer that landed in it (never cheaper than place 0)
        row["end_gap"] = max(price(*end) / p0 - 1, 0.0) if end and p0 else 0.0
        out.append(row)
    return sorted(out, key=lambda r: r["slot"])


def rank_row(db, wallet: str) -> dict | None:
    rows = path_rows(db, wallet)
    name = db.execute("SELECT name, kind FROM labels WHERE address=?", (wallet,)).fetchone() or ("", "")
    act = db.execute("SELECT buys, buys1, sol, coins, sells FROM act WHERE address=?", (wallet,)).fetchone() or (0,) * 5
    if not rows:
        return None
    curve = [r for r in rows if r["venue"] == "curve" and not r["create_block"]]
    trips = round_trips(db.execute(
        "SELECT slot, ts, mint, is_buy, quote, base FROM t WHERE user=? ORDER BY slot, pos, ev", (wallet,)).fetchall())  # fmt: skip
    done = [x for x in trips if x["done"]]
    hold = [(x["first_sell"] - x["slot"]) * SLOT_SECS for x in trips if x["first_sell"]]

    def blend(r):  # a seconds exit: 40 % sold at 2 s, 30 % at 5 s, 30 % at 20 s
        if None in (r["r2s"], r["r5s"], r["r20s"]):
            return None
        return 0.4 * r["r2s"] + 0.3 * r["r5s"] + 0.3 * r["r20s"]

    def late(r):  # the same exit from the last place of the wallet's block
        x = blend(r)
        return None if x is None else (1 + x) / (1 + r["end_gap"]) - 1

    days = 7.0
    b = [blend(r) for r in curve]
    return {
        "wallet": wallet, "name": name[0], "kind": name[1], "big": is_big(name[0]), "copy_bot": wallet in COPY_BOTS,
        "buys_7d": act[0], "buys_1sol_7d": act[1], "moments": len(rows), "curve_moments": len(curve),
        "curve_moments_a_day": round(len(curve) / days, 1),
        "create_block_share": round(mean([1.0 if r["create_block"] else 0.0 for r in rows]) or 0, 2),
        "behind_another_buy_share": round(mean([1.0 if r["ahead"] else 0.0 for r in rows if not r["create_block"]]) or 0, 2),
        "median_buy_sol": round(pct([r["sol"] for r in rows], 50), 2),
        "median_mcap_sol": round(pct([r["mcap"] for r in curve], 50) or 0, 0),
        "median_age_s": pct([r["age"] for r in curve], 50),
        "median_first_sell_s": round(pct(hold, 50), 1) if hold else None,
        "own_sol_7d": round(sum(x["out"] - x["in"] for x in done) / 1e9, 1), "own_trips": len(done),
        "own_wins": round(mean([1.0 if x["out"] > x["in"] else 0.0 for x in done]) or 0, 2),
        "r2s_median": pct([r["r2s"] for r in curve], 50), "r5s_median": pct([r["r5s"] for r in curve], 50),
        "r20s_median": pct([r["r20s"] for r in curve], 50), "r60s_median": pct([r["r60s"] for r in curve], 50),
        "r2s_mean": capped_mean([r["r2s"] for r in curve]), "r5s_mean": capped_mean([r["r5s"] for r in curve]),
        "r20s_mean": capped_mean([r["r20s"] for r in curve]), "r60s_mean": capped_mean([r["r60s"] for r in curve]),
        "seconds_exit_mean": capped_mean(b), "seconds_exit_win": mean([1.0 if x > 0.025 else 0.0 for x in b if x is not None]),
        "seconds_exit_last_place_mean": capped_mean([late(r) for r in curve]),
        "seconds_exit_last_place_win": mean([1.0 if x > 0.025 else 0.0 for x in (late(r) for r in curve) if x is not None]),
        "end_gap_median": pct([r["end_gap"] for r in curve], 50), "end_gap_mean": mean([r["end_gap"] for r in curve]),
        "ride_60s_last_place_mean": capped_mean([(1 + r["r60s"]) / (1 + r["end_gap"]) - 1 for r in curve if r["r60s"] is not None]),
        "ride_300s_mean": capped_mean([r["r300s"] for r in curve]),
        "peak60_median": pct([r["peak60"] for r in curve], 50),
        "behind_same_block_median": pct([r["behind"] for r in curve], 50),
        "behind_same_block_mean": mean([r["behind"] for r in curve]),
        "buyers_2s_median": pct([r["in2s"] for r in curve], 50),
    }  # fmt: skip


def split(rows: list[dict], key, order=None) -> list[dict]:
    by: dict = defaultdict(list)
    for r in rows:
        k = key(r)
        if k is not None:
            by[k].append(r)
    out = []
    for k in order or sorted(by):
        rs = by.get(k, [])
        if not rs:
            continue
        out.append({"band": k, "n": len(rs)} | {
            f"p{n}_{h}": capped_mean([r.get(f"p{n}_{h}") for r in rs]) for n in PLACES for h in ("2s", "5s", "20s", "60s")
        } | {"p0_5s_median": pct([r.get("p0_5s") for r in rs], 50), "p2_5s_median": pct([r.get("p2_5s") for r in rs], 50),
             "p0_5s_win": mean([1.0 if (r.get("p0_5s") or 0) > 0.025 else 0.0 for r in rs]),
             "outsiders_block": mean([r["n_same_block"] for r in rs])})  # fmt: skip
    return out


def band(x, edges, names):
    if x is None:
        return None
    for e, n in zip(edges, names, strict=False):
        if x < e:
            return n
    return names[-1]


def profile(db, wallet: str, own_wallets: set[str], labels: dict[str, tuple]) -> dict:
    """The full profile of one wallet from the cache (after `pull` and `blocks`)."""
    base = {r["mint"]: r for r in path_rows(db, wallet)}
    paths = {m: json.loads(d) for m, d in db.execute("SELECT mint, data FROM path WHERE user=?", (wallet,))}
    hist = {m: json.loads(d) for m, d in db.execute("SELECT mint, data FROM hist WHERE user=?", (wallet,))}
    keys = ["sig", "ev", "slot", "pos", "ts", "mint", "venue", "user", "is_buy", "quote", "base", "q_after", "b_after",
            "payer", "priority", "tip"]  # fmt: skip
    blocks: dict[str, list[dict]] = {}
    for mint, r in base.items():
        rs = [dict(zip(keys, x, strict=True)) for x in db.execute(
            f"SELECT {COLS} FROM t WHERE mint=? AND slot BETWEEN ? AND ? ORDER BY slot, pos, ev",
            (mint, r["slot"], r["slot"] + LAND_SLOTS))]  # fmt: skip
        blocks[mint] = rs
    mates = companions_from_blocks(
        [{"pos": r["pos"], "buys": [(x["user"], x["pos"]) for x in blocks[m] if x["slot"] == r["slot"] and x["is_buy"]]}
         for m, r in base.items()], wallet) - COPY_BOTS  # fmt: skip
    rows = []
    first_holders: Counter = Counter()
    first_bids, top_bids, all_bids, top_first = [], [], [], []
    holder_bids: dict[str, list] = defaultdict(list)
    holder_sol: dict[str, list] = defaultdict(list)
    for mint, r in base.items():
        st = place_states(blocks[mint], wallet, r["slot"], r["pos"], mates, own_wallets)
        if not st:
            continue
        d = paths[mint]
        r = dict(r)
        for n in PLACES:
            pn = price(*st["at"][n])
            r[f"gap{n}"] = pn / r["p0"] - 1 if r["p0"] else None
            for h in HORIZONS:
                v = d.get(h)
                r[f"p{n}_{h}"] = price(*[float(x) for x in v.split(",")[:2]]) / pn - 1 if v and pn else None
            # at a size: sold 40 % at 2 s, 30 % at 5 s, 30 % at 20 s (the bots' seconds exit), and all at 60 s
            for size in SIZES:
                parts = []
                for h, w in (("2s", 0.4), ("5s", 0.3), ("20s", 0.3), ("60s", 0.0)):
                    v = d.get(h)
                    if not v or not pn:
                        parts = None
                        break
                    qt, bt = (float(x) for x in v.split(",")[:2])
                    parts.append((w, sized_return(*st["at"][n], qt, bt, size)))
                r[f"s{n}_{size:g}"] = None if parts is None else tuple(sum(w * x[i] for w, x in parts) for i in (0, 1))
                r[f"ride{n}_{size:g}"] = None if parts is None else parts[3][1]
        same = st["same_block"]
        r["n_same_block"] = len({x["user"] for x in same})
        r["n_land"] = len({x["user"] for x in st["outsiders"]})
        r["mates_in_block"] = sum(
            1 for x in blocks[mint] if x["slot"] == r["slot"] and x["is_buy"] and x["user"] in mates
        )
        h = hist.get(mint)
        r["known_before"] = h["known"] if h else None
        r["known_gap_s"] = (r["slot"] - h["last_known_slot"]) * SLOT_SECS if h and h.get("last_known_slot") else None
        r["trades_before"] = h["trades"] if h else None
        r["hour"] = time.gmtime(r["ts"]).tm_hour
        followable = r["venue"] == "curve" and not r["create_block"]  # the moments the crowd is counted on
        if same and followable and r["ts"] >= BIDS_FROM:
            bids = [((x["priority"] or 0) + (x["tip"] or 0)) / 1e9 for x in same if x["priority"] is not None]
            if same[0]["priority"] is not None:
                first_bids.append(((same[0]["priority"] or 0) + (same[0]["tip"] or 0)) / 1e9)
                r["first_bid"] = first_bids[-1]
                holder_bids[same[0]["user"]].append(first_bids[-1])
            if bids:
                top_bids.append(max(bids))
                all_bids += bids
            if len(bids) >= 2 and same[0]["priority"] is not None:  # does the highest bid stand first?
                top_first.append(1.0 if first_bids[-1] >= max(bids) else 0.0)
        if same and followable:
            first_holders[same[0]["user"]] += 1
            holder_sol[same[0]["user"]].append(same[0]["quote"] / 1e9)
        rows.append(r)
    rows.sort(key=lambda r: r["slot"])
    curve = [r for r in rows if r["venue"] == "curve" and not r["create_block"]]
    trades = db.execute(
        "SELECT slot, ts, mint, is_buy, quote, base FROM t WHERE user=? ORDER BY slot, pos, ev", (wallet,)).fetchall()  # fmt: skip
    trips = round_trips(trades)
    done = [x for x in trips if x["done"]]
    buys = db.execute(
        "SELECT quote, venue, mint, slot, ts FROM t WHERE user=? AND is_buy=1 ORDER BY slot, pos", (wallet,)
    ).fetchall()
    venues = Counter(v for _q, v, *_ in buys)
    firsts = set()
    topups = 0
    for _q, _v, m, *_ in buys:
        if m in firsts:
            topups += 1
        firsts.add(m)
    days = 7.0
    name = labels.get(wallet, ("", ""))
    out = {
        "wallet": wallet, "name": name[0], "kind": name[1],
        "companions": [{"wallet": m, "name": labels.get(m, ("", ""))[0]} for m in sorted(mates)],
        "activity": {
            "buys": len(buys), "buys_a_day": round(len(buys) / days, 1), "coins": len(firsts),
            "top_ups_share": round(topups / len(buys), 2) if buys else None,
            "buy_sol_p10_25_50_75_90": [round(pct([q / 1e9 for q, *_ in buys], p), 2) for p in (10, 25, 50, 75, 90)],
            "sol_bought": round(sum(q for q, *_ in buys) / 1e9, 0),
            "venues": dict(venues),
            "first_buys_1sol": len(rows), "first_buys_1sol_curve": len(curve),
            "first_buys_1sol_create_block": sum(1 for r in rows if r["create_block"]),
            "first_buys_1sol_after_curve": sum(1 for r in rows if r["venue"] != "curve"),
            "coin_age_s_p25_50_75": [pct([r["age"] for r in curve], p) for p in (25, 50, 75)],
            "mcap_sol_p25_50_75": [round(pct([r["mcap"] for r in curve], p) or 0) for p in (25, 50, 75)],
            "own_price_impact_median": pct([r["own_impact"] for r in curve], 50),
        },
        "selling": {
            "finished_trades": len(done), "open_trades": len(trips) - len(done),
            "own_sol": round(sum(x["out"] - x["in"] for x in done) / 1e9, 1),
            "own_sol_in": round(sum(x["in"] for x in done) / 1e9, 0),
            "wins": round(mean([1.0 if x["out"] > x["in"] else 0.0 for x in done]) or 0, 2),
            "first_sell_s_p10_25_50_75_90": [round(pct([(x["first_sell"] - x["slot"]) * SLOT_SECS for x in trips if x["first_sell"]], p) or 0, 1) for p in (10, 25, 50, 75, 90)],
            "last_sell_s_p25_50_75": [round(pct([(x["last_sell"] - x["slot"]) * SLOT_SECS for x in done], p) or 0, 1) for p in (25, 50, 75)],
            "sells_a_trade_p25_50_75": [pct([x["sells"] for x in done], p) for p in (25, 50, 75)],
            "one_sale_share": round(mean([1.0 if x["sells"] == 1 else 0.0 for x in done]) or 0, 2),
            "first_sale_share_median": pct([x["first_sell_share"] for x in done], 50),
            "sold_within_5s_share": round(mean([1.0 if (x["first_sell"] - x["slot"]) * SLOT_SECS <= 5 else 0.0 for x in trips if x["first_sell"]]) or 0, 2),
        },
        "crowd": {
            "moments": len(curve),
            "outsiders_in_block_p50_75_90": [pct([r["n_same_block"] for r in curve], p) for p in (50, 75, 90)],
            "blocks_with_an_outsider": round(mean([1.0 if r["n_same_block"] else 0.0 for r in curve]) or 0, 2),
            "blocks_with_4_outsiders": round(mean([1.0 if r["n_same_block"] >= 4 else 0.0 for r in curve]) or 0, 2),
            "buyers_next_5_blocks_p50_75": [pct([r["next5"] for r in curve], p) for p in (50, 75)],
            "buyers_in_2s_p50_75": [pct([r["in2s"] for r in curve], p) for p in (50, 75)],
            "gap_place2_median": pct([r["gap2"] for r in curve], 50), "gap_place6_median": pct([r["gap6"] for r in curve], 50),
            "gap_place2_mean": mean([r["gap2"] for r in curve]), "gap_place6_mean": mean([r["gap6"] for r in curve]),
            "bids_blocks": len(first_bids),
            "first_outsider_bid_p50_75_90_max": [pct(first_bids, p) for p in (50, 75, 90, 100)],
            "highest_bid_p50_75_90_max": [pct(top_bids, p) for p in (50, 75, 90, 100)],
            "highest_bidder_first": mean(top_first), "highest_bidder_blocks": len(top_first),
            "all_outsider_bids_p50_75_90": [pct(all_bids, p) for p in (50, 75, 90)], "all_outsider_bids": len(all_bids),
            "first_place_holders": [{"wallet": w, "name": labels.get(w, ("", ""))[0], "blocks": k, "known_bot": w in COPY_BOTS,
                                     "buy_sol_median": pct(holder_sol[w], 50), "bid_median": pct(holder_bids[w], 50),
                                     # each bid seen, lowest first: a bot that sends a tipped and an untipped
                                     # buy has two prices, and a median of the two is neither
                                     "bids": sorted(holder_bids[w]), "bids_seen": len(holder_bids[w])}
                                    for w, k in first_holders.most_common(8)],
        },
        "path": {
            f"place{n}": {h: {"mean": capped_mean([r[f"p{n}_{h}"] for r in curve]), "median": pct([r[f"p{n}_{h}"] for r in curve], 50),
                              "above_2.5pct": mean([1.0 if (r[f"p{n}_{h}"] or 0) > 0.025 else 0.0 for r in curve])}
                          for h in ("1s", "2s", "5s", "20s", "60s", "300s")} for n in PLACES
        } | {"n": len(curve), "peak60_median": pct([r["peak60"] for r in curve], 50), "trough60_median": pct([r["trough60"] for r in curve], 50)},
        "sized": {
            f"place{n}": {
                f"{size:g}": {
                    "seconds_low": capped_mean([r[f"s{n}_{size:g}"][0] for r in curve if r.get(f"s{n}_{size:g}")]),
                    "seconds_high": capped_mean([r[f"s{n}_{size:g}"][1] for r in curve if r.get(f"s{n}_{size:g}")]),
                    "seconds_wins": mean([1.0 if sum(r[f"s{n}_{size:g}"]) > 0 else 0.0 for r in curve
                                          if r.get(f"s{n}_{size:g}")]),
                    "ride60_low": capped_mean([r[f"ride{n}_{size:g}"][0] for r in curve if r.get(f"ride{n}_{size:g}")]),
                    "ride60_high": capped_mean([r[f"ride{n}_{size:g}"][1] for r in curve if r.get(f"ride{n}_{size:g}")]),
                } for size in SIZES
            } for n in PLACES
        },
        "splits": {
            "by_buy_sol": split(curve, lambda r: band(r["sol"], (2, 3, 5), ("1-2", "2-3", "3-5", "5+")), ("1-2", "2-3", "3-5", "5+")),
            "by_age": split(curve, lambda r: band(r["age"], (15, 60, 300, 1800), ("<15s", "15-60s", "1-5m", "5-30m", "30m+")), ("<15s", "15-60s", "1-5m", "5-30m", "30m+")),
            "by_mcap_sol": split(curve, lambda r: band(r["mcap"], (50, 80, 150, 300), ("<50", "50-80", "80-150", "150-300", "300+")), ("<50", "50-80", "80-150", "150-300", "300+")),
            "by_hour_utc": split(curve, lambda r: band(r["hour"], (6, 12, 18), ("00-06", "06-12", "12-18", "18-24")), ("00-06", "06-12", "12-18", "18-24")),
            "by_day_utc": split(curve, lambda r: time.strftime("%d %b", time.gmtime(r["ts"]))),
            "by_known_before": split(curve, lambda r: band(r["known_before"], (1, 2, 4), ("0", "1", "2-3", "4+")), ("0", "1", "2-3", "4+")),
            "by_outsiders_in_block": split(curve, lambda r: band(r["n_same_block"], (1, 2, 4), ("0", "1", "2-3", "4+")), ("0", "1", "2-3", "4+")),
            "after_curve": split([r for r in rows if r["venue"] != "curve"], lambda r: "pumpswap"),
            "create_block": split([r for r in rows if r["create_block"]], lambda r: "create block"),
        },
    }  # fmt: skip
    return out


# ---------------------------------------------------------------- the tables of the document


def _p(x, digits=1):
    return "n/a" if x is None else f"{100 * x:+.{digits}f} %"


def _n(x, digits=1):
    return "n/a" if x is None else f"{x:.{digits}f}"


def _bid(x):
    return "n/a" if x is None else (f"{x:.4f}" if x < 0.01 else f"{x:.3f}" if x < 1 else f"{x:.2f}")


def render(p: dict, gm: dict | None = None) -> str:
    """One wallet's measured tables as Markdown (the judgement around them is written by hand)."""
    a, s, c, path = p["activity"], p["selling"], p["crowd"], p["path"]
    n = path["n"]
    hs = ("1s", "2s", "5s", "20s", "60s", "300s")
    out = [f"`{p['wallet']}` ({p['name']})", ""]
    if gm:
        out += [
            f"GMGN (7 days): {gm.get('follow_count')} followers, {gm.get('buys')} buys and {gm.get('sells')} sells in "
            f"{gm.get('tokens')} coins, profit {gm.get('realized_usd') or 0:,.0f} $, win rate "
            f"{100 * (gm.get('win_rate') or 0):.0f} %, tags {', '.join(gm.get('tags') or []) or 'none'}"
            + (f", X @{gm['twitter']}" if gm.get("twitter") else "")
            + ".",
            "",
        ]
    mates = ", ".join(f"`{m['wallet'][:6]}` {m['name']}".strip() for m in p["companions"]) or "none found"
    out += [f"Companions (the tape rule): {mates}.", ""]
    v = a["venues"]
    tot = sum(v.values()) or 1
    venues = ", ".join(f"{k} {k_n} ({100 * k_n / tot:.0f} %)" for k, k_n in sorted(v.items(), key=lambda kv: -kv[1]))
    out += [
        "| what it buys (our tape, 7 days) | |",
        "|---|---|",
        f"| buys / a day / coins | {a['buys']} / {a['buys_a_day']} / {a['coins']} |",
        f"| top-ups (a buy of a coin it already bought) | {100 * (a['top_ups_share'] or 0):.0f} % of buys |",
        f"| buy size, SOL (10 / 25 / 50 / 75 / 90 %) | {' / '.join(_n(x, 2) for x in a['buy_sol_p10_25_50_75_90'])} |",
        f"| venue of its buys on the tape | {venues} |",
        (
            f"| first buys of 1 SOL or more | {a['first_buys_1sol']}: {a['first_buys_1sol_curve']} on the curve behind "
            f"other trades, {a['first_buys_1sol_create_block']} in the create block, {a['first_buys_1sol_after_curve']} "
            "past the curve |"
        ),
        (
            "| coin age at those curve buys (25 / 50 / 75 %) | "
            f"{' / '.join(_n(x, 0) + ' s' for x in a['coin_age_s_p25_50_75'])} |"
        ),
        f"| market cap after its buy, SOL (25 / 50 / 75 %) | {' / '.join(str(x) for x in a['mcap_sol_p25_50_75'])} |",
        f"| its own buy moves the price (median) | {_p(a['own_price_impact_median'])} |",
        (
            f"| its own trades | {s['finished_trades']} finished, {s['own_sol']:+.1f} SOL on {s['own_sol_in']:.0f} SOL in, "
            f"{100 * s['wins']:.0f} % wins; {s['open_trades']} not finished |"
        ),
        (
            "| first sale after (10 / 25 / 50 / 75 / 90 %) | "
            f"{' / '.join(_n(x, 0) + ' s' for x in s['first_sell_s_p10_25_50_75_90'])} |"
        ),
        (
            "| sales a trade (25 / 50 / 75 %); out in one sale | "
            f"{' / '.join(_n(x, 0) for x in s['sells_a_trade_p25_50_75'])}; {100 * s['one_sale_share']:.0f} % |"
        ),
        "",
        f"| price after its first buy ({n} curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |",
        "|---|---|---|---|---|---|---|",
    ]
    words = {0: "right behind it", 2: "two places back", 6: "six places back"}
    for k in PLACES:
        d = path[f"place{k}"]
        cells = " | ".join(f"{_p(d[h]['mean'])} / {_p(d[h]['median'])}" for h in hs)
        out.append(f"| {words[k]}: mean / median | {cells} |")
        cells = " | ".join(f"{100 * (d[h]['above_2.5pct'] or 0):.0f} %" for h in hs)
        out.append(f"| {words[k]}: above +2.5 % | {cells} |")
    out += [
        "",
        (
            "| a copy at a size, after pump.fun's fees and its own price impact, before the bid (low to high) "
            "| 2 SOL | 5 SOL | 10 SOL |"
        ),
        "|---|---|---|---|",
    ]
    for k in PLACES:
        z = p["sized"][f"place{k}"]
        cells = " | ".join(f"{_p(z[f'{x:g}']['seconds_low'])} to {_p(z[f'{x:g}']['seconds_high'])}" for x in SIZES)
        out.append(f"| {words[k]}, out in seconds (40 % at 2 s, 30 % at 5 s, 30 % at 20 s) | {cells} |")
        cells = " | ".join(f"{_p(z[f'{x:g}']['ride60_low'])} to {_p(z[f'{x:g}']['ride60_high'])}" for x in SIZES)
        out.append(f"| {words[k]}, held 60 s | {cells} |")
    fb, hb = c["first_outsider_bid_p50_75_90_max"], c["highest_bid_p50_75_90_max"]
    holders = "; ".join(
        f"`{h['wallet'][:8]}`{' (' + h['name'] + ')' if h['name'] else ''} {h['blocks']}"
        + (" [known copy bot]" if h["known_bot"] else "")
        + f", buys {_n(h['buy_sol_median'])} SOL"
        + (
            f", bids {_bid(h['bids'][0])} to {_bid(h['bids'][-1])} ({h['bids_seen']} seen)"
            if h["bids_seen"] > 1
            else f", bid {_bid(h['bids'][0])} (1 seen)"
            if h["bids_seen"]
            else ""
        )
        for h in c["first_place_holders"][:6]
    )
    out += [
        "",
        "| the crowd behind it | |",
        "|---|---|",
        (
            "| outsiders in its block (50 / 75 / 90 %) | "
            f"{' / '.join(_n(x, 0) for x in c['outsiders_in_block_p50_75_90'])} |"
        ),
        (
            f"| blocks with an outsider / with four or more | {100 * c['blocks_with_an_outsider']:.0f} % / "
            f"{100 * c['blocks_with_4_outsiders']:.0f} % |"
        ),
        (
            "| buyers in the next 5 blocks (50 / 75 %); in 2 seconds | "
            f"{' / '.join(_n(x, 0) for x in c['buyers_next_5_blocks_p50_75'])}; "
            f"{' / '.join(_n(x, 0) for x in c['buyers_in_2s_p50_75'])} |"
        ),
        (
            "| price two places back over right behind it (median / mean) | "
            f"{_p(c['gap_place2_median'])} / {_p(c['gap_place2_mean'])} |"
        ),
        f"| price six places back (median / mean) | {_p(c['gap_place6_median'])} / {_p(c['gap_place6_mean'])} |",
        (
            f"| first outsider's bid, SOL ({c['bids_blocks']} blocks since 11 Nov 16:34): 50 / 75 / 90 % / highest | "
            f"{' / '.join(_bid(x) for x in fb)} |"
        ),
        f"| highest bid in its block: 50 / 75 / 90 % / highest | {' / '.join(_bid(x) for x in hb)} |",
        (
            f"| every outsider's bid in its block ({c['all_outsider_bids']} buys): 50 / 75 / 90 % | "
            f"{' / '.join(_bid(x) for x in c['all_outsider_bids_p50_75_90'])} |"
        ),
        (
            f"| the highest bidder stood first | {_n(100 * c['highest_bidder_first'], 0) if c['highest_bidder_first'] is not None else 'n/a'} % "
            f"of {c['highest_bidder_blocks']} blocks with two or more known bids |"
        ),
        f"| who is first behind it (blocks, 7 days) | {holders or 'nobody'} |",
        "",
        (
            "| split | band | buys | outsiders | right behind, +5 s | two back, +5 s | two back, +20 s | two back, +60 s "
            "| six back, +5 s |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    titles = {
        "by_buy_sol": "its buy, SOL",
        "by_age": "coin age",
        "by_mcap_sol": "market cap, SOL",
        "by_hour_utc": "hour (UTC)",
        "by_day_utc": "day (UTC)",
        "by_known_before": "tracked wallets already in",
        "by_outsiders_in_block": "outsiders in its block",
        "after_curve": "venue",
        "create_block": "venue",
    }
    for key, title in titles.items():
        for r in p["splits"].get(key, []):
            if r["n"] < 5:  # a band of four buys says nothing
                continue
            out.append(
                f"| {title} | {r['band']} | {r['n']} | {_n(r['outsiders_block'])} | {_p(r['p0_5s'])} | "
                f"{_p(r['p2_5s'])} | {_p(r['p2_20s'])} | {_p(r['p2_60s'])} | {_p(r['p6_5s'])} |"
            )
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- commands


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["count", "pull", "blocks", "rank", "profile", "report"])
    ap.add_argument("wallets", nargs="*")
    ap.add_argument("--top", type=int, default=0, help="pull: also every labelled wallet with 30 buys of 1 SOL")
    ap.add_argument("--gmgn", help="report: a folder of saved `gmgn wallet <w> --json` answers, <wallet>.wallet.json")
    ap.add_argument("--db", default=os.environ.get("LEADER_DB", "leader_profiles.db"))
    a = ap.parse_args(argv)
    if a.cmd in ("rank", "profile", "report"):
        db = sqlite3.connect(a.db)
        labels = {r[0]: (r[1], r[2]) for r in db.execute("SELECT address, name, kind FROM labels")}
        if a.cmd == "rank":
            ws = a.wallets or [r[0] for r in db.execute("SELECT DISTINCT user FROM path")]
            print(json.dumps([r for r in (rank_row(db, w) for w in ws) if r]))
            return 0
        own = {w for w, (_n, k) in labels.items() if k == "own"}
        profs = [profile(db, w, own, labels) for w in a.wallets]
        if a.cmd == "profile":
            print(json.dumps(profs))
        for p in profs if a.cmd == "report" else []:
            gm = Path(a.gmgn) / f"{p['wallet']}.wallet.json" if a.gmgn else None
            print(render(p, json.loads(gm.read_text()).get("stats_7d") if gm and gm.exists() else None))
        return 0
    tape = Tape(a.db)
    if a.cmd == "count":
        tape.count()
        return 0
    ws = list(a.wallets)
    if a.cmd == "pull" and a.top:
        ws += [r[0] for r in tape.db.execute(
            "SELECT address FROM act WHERE buys1 >= 30 AND buys + sells <= 15000 ORDER BY buys1 DESC LIMIT ?", (a.top,))
            if r[0] not in ws]  # fmt: skip
    t0 = time.time()
    for i, w in enumerate(ws):
        if a.cmd == "pull":
            n = tape.pull_trades(w)
            p = tape.pull_paths(w)
            print(i, w[:8], "trades", n, "paths", p, round(time.time() - t0), file=sys.stderr, flush=True)
        else:
            n = tape.pull_blocks(w)
            h = tape.pull_hist(w)
            print(i, w[:8], "block rows", n, "histories", h, round(time.time() - t0), file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
