"""The history backfill (`snipetech history amm-backfill`, 12 Nov 2026): a big wallet's PumpSwap buys in older coins and
the pool's trades around each, fetched from the RPC into the tape's own tables, with a coverage record of the stretches
held whole. Real PumpSwap transactions and moved copies of them (tests/amm_chain.py), served page by page to the repo's
own RPC client."""

import asyncio
import copy
import json
import os

import pytest

from snipetech import amm_backfill as B
from snipetech import cli
from snipetech.amm_tape import AMM_RECENT_KEY, backfill_covers, backfill_key, backfill_spans
from snipetech.models import Migration, Trade
from snipetech.solana.normalize import normalize
from snipetech.solana.programs import WSOL_MINT
from snipetech.solana.tx import extract_from_transaction
from snipetech.store import Store

from .amm_chain import SLOT_SECS, Chain, moved, real, sig_of
from .synth import addr
from .test_short_writes import Guard

R, R_TX = real("3kz7PFEU")  # a 0.164 SOL PumpSwap buy
W = "9S14MKMPh5qRzdmsn9UMoYhPrSjVA7g7EHoy33YtHp11"  # the wallet that made it (and paid for it)
P = "E1H3zhuQq3im8TfR8PpHuxs8Q6L7XkL9FzcGjEKJseih"  # its pool
(M, _Q), = [mints for _pool, mints in extract_from_transaction(R_TX, R).pool_mints.items()]  # fmt: skip
T, S, IDX = R_TX["blockTime"], R_TX["slot"], R_TX["transactionIndex"]
SMALL, SMALL_TX = real("3ErkbBtM")  # a 0.051 SOL buy in another pool: under the size asked for
P2 = "9s8aqA4TvgeWi2bhxn6jCaZH3vSuZeaGXZsBiyR1CWAC"
(M2, _Q2), = extract_from_transaction(SMALL_TX, SMALL).pool_mints.values()  # fmt: skip
CURVE, CURVE_TX = real("1R1aCFeC")  # a pump.fun curve trade
U1, U2 = addr("someone"), addr("someone else")
NOW = T + 5 * 3600
SPAN = [T - 7200, T + 6 * 3600]
STRETCH = [T - B.BEFORE_SECS, T + 3600 + B.TAIL_SECS]


def slot_at(ts: int) -> int:
    return S + round((ts - T) / SLOT_SECS)


def plain(tx: dict, name: str, ts: int, index: int = 3) -> tuple[str, dict]:
    """A copy of any transaction at another time (a curve trade, a failed one): nothing in it is read as PumpSwap."""
    out = copy.deepcopy(tx)
    out["transaction"]["signatures"] = [sig_of(name)]
    out["slot"], out["blockTime"], out["transactionIndex"] = slot_at(ts), ts, index
    return sig_of(name), out


def tape_row(store: Store, name: str, ts: int) -> str:
    """A trade the recorder wrote at `ts` (any coin's): its signature is a place on the chain to list back from."""
    sig = sig_of(name)
    store.add_trades([Trade(sig=sig, ev=0, slot=slot_at(ts), pos=1, ts=ts, mint=addr("a curve coin"), venue="curve",
                            user=U1, is_buy=True, quote=10**8, base=10**12, fee=10**6, fee_bps=125,
                            q_after=31 * 10**9, b_after=10**15)])  # fmt: skip
    return sig


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The wallet's history, newest first: a curve trade (T+500), a failed transaction (T+100), the real PumpSwap buy
    (T), a small PumpSwap buy in another pool (T-1000), a trade from before the tape (T-8000). The pool's: a trade
    after the stretch (T+3700), a failed one (T+3100), trades at T+3000, in the buy's own slot right behind it, the
    buy, T-30, and one before the stretch (T-100). The tape holds a curve trade at T+3640."""
    monkeypatch.setattr(B, "PAGE", 2)  # two signatures a page: every listing is paged
    monkeypatch.setattr(B, "FETCH_CHUNK", 2)
    chain = Chain()
    names = {}
    chain.add(R, R_TX, W, P)
    for name, ts, index, user in (("p-early", T - 100, 4, U1), ("p-before", T - 30, 9, U1),
                                  ("p-later", T + 3000, 2, U2), ("p-after", T + 3700, 5, U2)):  # fmt: skip
        names[name] = chain.add(*moved(R_TX, name, slot_at(ts), ts, index, user), P)
    names["p-same-slot"] = chain.add(*moved(R_TX, "p-same-slot", S, T, IDX + 1, U2), P)
    names["p-failed"] = chain.add(*plain(R_TX, "p-failed", T + 3100), P, failed=True)
    names["w-small"] = chain.add(*moved(SMALL_TX, "w-small", slot_at(T - 1000), T - 1000, 7, W), W, P2)
    names["w-curve"] = chain.add(*plain(CURVE_TX, "w-curve", T + 500), W)
    names["w-failed"] = chain.add(*plain(R_TX, "w-failed", T + 100), W, failed=True)
    names["w-old"] = chain.add(*plain(CURVE_TX, "w-old", T - 8000), W)
    store = Store(tmp_path / "t.db")
    store.set_coverage("tape", {"spans": [SPAN]})
    names["tape-after"] = chain.add(*plain(CURVE_TX, "tape-after", T + 3640))  # on the chain, in no listing we ask for
    tape_row(store, "tape-after", T + 3640)
    yield chain, store, names
    store.close()


def run(chain: Chain, store: Store, now: int = NOW, **kw) -> dict:
    kw = {"days": 1.0, "min_sol": 0.1, **kw}

    async def go():
        rpc = chain.rpc()
        try:
            return await B.backfill(store, rpc, [W], now, **kw)
        finally:
            await rpc.close()

    return asyncio.run(go())


def amm_rows(store: Store, mint: str) -> list[tuple]:
    return store._q("SELECT sig, slot, pos, ts, user, is_buy, quote, base, q_after, b_after, venue FROM trades "
                    "WHERE mint=? ORDER BY slot, pos, ev", (mint,))  # fmt: skip


def test_the_stretch_around_a_wallets_buy_is_written_as_the_recorder_writes_it(world):
    chain, store, n = world
    out = run(chain, store)
    rows = amm_rows(store, M)
    # every trade of the pool from 60 s before the buy to an hour and 30 s after it, and nothing outside
    assert [r[0] for r in rows] == [n["p-before"], R, n["p-same-slot"], n["p-later"]]
    assert {r[10] for r in rows} == {"amm"}
    # the place in the block is the transaction's own index, as the recorder writes it
    assert [(r[1], r[2]) for r in rows] == [(slot_at(T - 30), 9), (S, IDX), (S, IDX + 1), (slot_at(T + 3000), 2)]
    # each row is what the recorder's decoder makes of the transaction, reserves after the trade included
    (want,) = normalize(extract_from_transaction(R_TX, R), IDX).trades
    (got,) = [r for r in rows if r[0] == R]
    assert got == (R, S, IDX, T, W, 1, want.quote, want.base, want.q_after, want.b_after, "amm")
    pre = {(b["owner"], b["mint"]): int(b["uiTokenAmount"]["amount"]) for b in R_TX["meta"]["preTokenBalances"]}
    assert got[9] == pre[(P, M)] - want.base  # the pool's coins after the buy: its balance before, less what it gave
    assert got[8] > pre[(P, WSOL_MINT)]  # its SOL after the buy: more than before
    # the fee record of each transaction comes with it
    fee = store._q("SELECT priority, tip FROM trades WHERE sig=?", (R,))[0]
    assert fee[0] is not None and fee[1] is not None
    assert out["stretches"]["fetched"] == 1 and out["stretches"]["trades"] == 4
    assert store.pool(P) == (M, WSOL_MINT)


def test_the_coverage_record_names_the_stretch_held_whole(world):
    chain, store, _ = world
    run(chain, store)
    assert store.coverage(backfill_key(M)) == {"pools": [P], "spans": [STRETCH]}
    spans = backfill_spans(store)
    assert spans == {M: [STRETCH]}  # a coin whose pool is known and nothing held is not in it
    assert backfill_covers(spans[M], T, T + 301) and not backfill_covers(spans[M], T, T + 3700)
    assert not backfill_covers(spans.get(M2), T - 1000, T - 900)
    # the small buy is the wallet's own trade and is written (a lab then sees it was not the wallet's first buy);
    # its pool is known, and no stretch of it is claimed
    assert [r[4] for r in amm_rows(store, M2)] == [W]
    assert store.coverage(backfill_key(M2)) == {"pools": [P2], "spans": []}


def test_requests_and_credits_are_counted(world):
    chain, store, n = world
    out = run(chain, store)
    # the wallet: 3 pages (two signatures each, the third reaches before the tape), 3 transactions (the failed one and
    # the one before the tape are not fetched); the pool: 3 pages back from the tape's signature, 4 transactions
    assert out["requests"] == {"getSignaturesForAddress": 6, "getTransaction": 7}
    assert chain.count("getSignaturesForAddress") == 6 and chain.count("getTransaction") == 7
    assert sorted(chain.fetched()) == sorted([n["w-curve"], R, n["w-small"], n["p-later"], n["p-same-slot"], R,
                                              n["p-before"]])  # fmt: skip
    assert out["credits"]["counted"] == 13 and out["stopped"] is None
    assert out["credits"]["prices"]["getTransaction"]["credits"] == B.PRICES["getTransaction"].credits == 1
    assert "Helius" in out["credits"]["prices"]["getTransaction"]["source"]
    assert store.usage_month("helius-backfill", NOW) == 13
    (w,) = out["wallets"]
    assert w["wallet"] == W and w["fetched"] == 3 and w["buys"] == 1 and w["missing"] == 0
    # the pool's listing started from the signature the tape holds just after the stretch
    first = next(p for m, p in chain.requests if m == "getSignaturesForAddress" and p[0] == P)
    assert first[1]["before"] == n["tape-after"] and first[1]["limit"] == 2


def test_every_request_waits_for_the_clients_limiter(world):
    chain, store, _ = world
    waited = []

    async def go():
        rpc = chain.rpc()
        acquire = rpc.limiter.acquire

        async def counted():
            waited.append(1)
            await acquire()

        rpc.limiter.acquire = counted
        try:
            return await B.backfill(store, rpc, [W], NOW, days=1.0, min_sol=0.1)
        finally:
            await rpc.close()

    asyncio.run(go())
    assert len(waited) == len(chain.requests) == 13


def test_a_dry_run_writes_no_tape_data_and_its_requests_are_counted(world):
    # until 12 Nov evening a dry run's own requests (it lists every signature: 3 here, about 37,000 for 7 days of a
    # wallet that failed transactions name 110,000 times in half an hour) were left out of the month's count: the
    # number under-reported what backfills spent, and the month's limit never saw a loop of dry runs
    chain, store, _ = world

    def tape() -> tuple:
        return (store._q("SELECT COUNT(*) FROM trades")[0][0],
                store._q("SELECT key, data FROM coverage WHERE key NOT LIKE 'api_usage:%' ORDER BY key"))  # fmt: skip

    before = tape()
    out = run(chain, store, dry_run=True)
    assert tape() == before  # no trade, no stretch, no place in a wallet's history: the ledger of credits alone moves
    assert store.usage_month("helius-backfill", NOW) == 3 and out["credits"]["this_month"] == 3
    assert chain.count("getTransaction") == 0 and chain.count("getSignaturesForAddress") == 3
    (w,) = out["wallets"]
    assert out["dry_run"] is True and w["to_fetch"] == 3 and w["buys"] == 0
    est = out["estimate"]
    assert est["wallet_transactions"] == 3 and est["stretches_known"] == 0
    # nothing is measured yet: the stretches those three transactions may hold are a guess, and it says so
    assert est["stretches_expected"] == pytest.approx(3 * B.ASSUMED_STRETCHES_PER_TX)
    assert est["credits"] == pytest.approx(3 + 3 * B.ASSUMED_STRETCHES_PER_TX * B.ASSUMED_TXS_PER_STRETCH, rel=0.01)
    assert len(est["assumed"]) == 2 and all("assumed" in line for line in est["assumed"])
    assert out["credits"]["counted"] == 3  # what the dry run itself asked for


def test_max_credits_stops_the_run_before_it_passes_and_the_next_run_continues(world):
    chain, store, n = world
    out = run(chain, store, max_credits=6)  # the wallet's 3 pages and 3 transactions fit; the pool's first page not
    assert out["stopped"] == "max_credits" and out["credits"]["counted"] == 6 and "6" in out["note"]
    assert len(chain.requests) == 6 and out["stretches"]["fetched"] == 0 and out["stretches"]["left"] == 1
    assert amm_rows(store, M) and backfill_spans(store) == {}  # the wallet's own buy is in; no stretch is claimed
    # a dry run now knows the buy: it lists the pool and says exactly what the stretch costs
    dry = run(chain, store, dry_run=True)
    assert dry["wallets"][0]["to_fetch"] == 0 and dry["wallets"][0]["buys"] == 1
    assert dry["estimate"]["stretches_known"] == 1 and dry["estimate"]["stretch_transactions_known"] == 4
    assert dry["estimate"]["credits"] == 4 and backfill_spans(store) == {}
    # the second run reads nothing of the wallet again: one page (nothing new), then the stretch
    chain.requests.clear()
    out = run(chain, store)
    assert out["stopped"] is None and out["wallets"][0]["fetched"] == 0
    assert sorted(chain.fetched()) == sorted([n["p-later"], n["p-same-slot"], R, n["p-before"]])
    assert chain.count("getSignaturesForAddress") == 1 + 3
    assert backfill_spans(store) == {M: [STRETCH]}
    assert store.usage_month("helius-backfill", NOW) == 6 + 4 + 8  # the dry run's four listing requests count too


def test_a_stretch_that_does_not_fit_is_not_started(world):
    chain, store, _ = world
    # 6 for the wallet, 3 pages of the pool: 9; its 4 transactions would make 13
    out = run(chain, store, max_credits=11)
    assert out["stopped"] == "max_credits" and out["credits"]["counted"] == 9
    assert chain.count("getTransaction") == 3 and backfill_spans(store) == {}


def test_the_months_limit_ends_a_run_whatever_its_own_limit_says(world):
    # every run counts into one number for the month; a run never takes it past the month's limit, so runs started
    # over and over (an agent's, each inside its own limit) cannot spend the plan
    chain, store, _ = world
    store.usage_add("helius-backfill", B.MONTH_CREDITS - 5, NOW)
    out = run(chain, store)
    assert out["stopped"] == "month_credits" and out["credits"]["counted"] == 5 and len(chain.requests) == 5
    assert "month" in out["note"] and out["credits"]["this_month"] == B.MONTH_CREDITS
    out = run(chain, store)  # nothing is left this month: nothing is asked
    assert out["stopped"] == "month_credits" and out["credits"]["counted"] == 0 and len(chain.requests) == 5
    out = run(chain, store, month_credits=B.MONTH_CREDITS + 100)  # the owner lifts it
    assert out["stopped"] is None and backfill_spans(store) == {M: [STRETCH]}


def test_a_second_run_asks_only_for_what_is_new(world):
    chain, store, _ = world
    run(chain, store)
    chain.requests.clear()
    again = run(chain, store)
    assert chain.count("getTransaction") == 0 and chain.count("getSignaturesForAddress") == 1
    assert again["stretches"]["held"] == 1 and again["stretches"]["fetched"] == 0
    # the wallet buys the coin again two hours later; the pool trades once more after it
    new = chain.add(*moved(R_TX, "w-new", slot_at(T + 7200), T + 7200, 11, W), W, P)
    later = chain.add(*moved(R_TX, "p-new-after", slot_at(T + 7300), T + 7300, 2, U1), P)
    chain.add(*plain(CURVE_TX, "tape-after-2", T + 10_840))
    tape_row(store, "tape-after-2", T + 10_840)
    chain.requests.clear()
    out = run(chain, store)
    assert sorted(chain.fetched()) == sorted([new, later, new])  # the new buy in the wallet's history, then its stretch
    assert out["wallets"][0]["fetched"] == 1 and out["wallets"][0]["buys"] == 2
    assert backfill_spans(store) == {M: [STRETCH, [T + 7200 - B.BEFORE_SECS, T + 7200 + 3600 + B.TAIL_SECS]]}
    assert [r[0] for r in amm_rows(store, M)][-2:] == [new, later]
    rec = store.coverage(B.wallet_key(W))
    assert rec["newest_sig"] == new and rec["oldest_ts"] <= SPAN[0] and rec["txs"] == 4


def test_a_scan_that_ended_before_the_cut_off_starts_over_and_counts_each_transaction_once(world):
    # the wallet was last read long ago: everything it has done since the cut-off is new, and transactions older than
    # the cut-off lie between that and the old scan
    chain, store, n = world
    older = chain.add(*plain(CURVE_TX, "w-older", T - 9000), W)
    store.set_coverage(B.wallet_key(W), {"newest_sig": older, "newest_ts": T - 9000, "oldest_sig": older,
                                         "oldest_ts": T - 9000, "txs": 1})  # fmt: skip
    dry = run(chain, store, dry_run=True)
    assert dry["wallets"][0]["to_fetch"] == 3 and chain.count("getSignaturesForAddress") == 3
    chain.requests.clear()
    out = run(chain, store)
    assert out["wallets"][0]["fetched"] == 3 and chain.count("getTransaction") == 3 + 4
    rec = store.coverage(B.wallet_key(W))
    assert rec["newest_sig"] == n["w-curve"] and rec["oldest_ts"] == SPAN[0] and rec["txs"] == 4
    chain.requests.clear()
    run(chain, store)
    assert chain.count("getTransaction") == 0 and chain.count("getSignaturesForAddress") == 1


def test_the_months_count_grows_while_the_job_runs(world, monkeypatch):
    # a job that is killed half way has still been counted: the month's limit holds for the next one
    chain, store, _ = world
    monkeypatch.setattr(B, "USAGE_EVERY", 1)
    seen = []
    handle = chain.handle

    def watching(request):
        seen.append(store.usage_month("helius-backfill", NOW))
        return handle(request)

    chain.handle = watching
    run(chain, store)
    assert seen[0] == 0 and seen == sorted(seen) and seen[-1] >= 10 and store.usage_month("helius-backfill", NOW) == 13


def test_a_coins_record_drops_stretches_from_before_the_tape(world):
    # the nightly prune deletes trades older than the tape keeps: a record must not go on claiming them
    chain, store, _ = world
    store.set_coverage(backfill_key(M), {"pools": [P], "spans": [[SPAN[0] - 5000, SPAN[0] - 4000]]})
    run(chain, store)
    assert store.coverage(backfill_key(M)) == {"pools": [P], "spans": [STRETCH]}


def test_overlapping_stretches_of_one_coin_are_fetched_once(world):
    chain, store, _ = world
    chain.add(*moved(R_TX, "w-topup", slot_at(T + 600), T + 600, 4, W), W, P)  # the wallet tops up ten minutes later
    chain.add(*plain(CURVE_TX, "tape-after-3", T + 4_240))
    tape_row(store, "tape-after-3", T + 4_240)
    out = run(chain, store)
    assert out["stretches"]["wanted"] == 1 and out["stretches"]["fetched"] == 1
    assert backfill_spans(store) == {M: [[T - B.BEFORE_SECS, T + 600 + 3600 + B.TAIL_SECS]]}
    assert len(chain.fetched()) == len(set(chain.fetched())) + 2  # only the wallet's two buys are read twice


def test_a_stretch_with_a_transaction_that_cannot_be_fetched_is_not_claimed(world):
    chain, store, n = world
    chain.unfetchable.add(n["p-later"])
    out = run(chain, store)
    assert out["stretches"]["incomplete"] == 1 and out["stretches"]["fetched"] == 0
    assert backfill_spans(store) == {} and len(amm_rows(store, M)) == 3  # what came is kept, nothing is claimed
    chain.unfetchable.clear()
    out = run(chain, store)
    assert out["stretches"]["fetched"] == 1 and backfill_spans(store) == {M: [STRETCH]}


def test_a_listing_without_the_wallets_own_buy_is_not_trusted(world):
    # the stretch is listed back from a signature of another transaction; if the RPC answered that with a listing
    # that lacks the buy the stretch is built around, the stretch is not whole
    chain, store, _ = world
    chain.of[P].remove(R)
    out = run(chain, store)
    assert out["stretches"]["listing_incomplete"] == 1 and backfill_spans(store) == {}
    assert chain.count("getTransaction") == 3  # the wallet's three; nothing of the pool


def test_a_stretch_the_recorder_already_holds_is_not_fetched(world):
    chain, store, _ = world
    store.add_migrations([Migration(M, P, slot_at(T - 600), T - 600, sig_of("migration"))])
    store.set_coverage(AMM_RECENT_KEY, {"runs": [{"run": "r", "from": T - 700, "to": T + 5000, "secs": 6000,
                                                  "skipped": [], "slot_secs": 0.4}]})  # fmt: skip
    out = run(chain, store)
    assert out["stretches"]["on_tape"] == 1 and out["stretches"]["fetched"] == 0
    assert not any(p[0] == P for m, p in chain.requests if m == "getSignaturesForAddress")


def test_a_buy_too_recent_for_a_whole_stretch_waits_for_a_later_run(world):
    chain, store, _ = world
    out = run(chain, store, now=T + 900)
    assert out["stretches"]["buys_too_recent"] == 1 and out["stretches"]["wanted"] == 0
    assert backfill_spans(store) == {} and chain.count("getTransaction") == 3  # the wallet's own three


def test_a_buy_outside_the_tape_is_counted_and_left(world):
    chain, store, _ = world
    store.set_coverage("tape", {"spans": [[SPAN[0], T - 10], [T + 100, SPAN[1]]]})  # the recorder was down at T
    out = run(chain, store)
    assert out["stretches"]["buys_outside_tape"] == 1 and out["stretches"]["wanted"] == 0
    assert out["cut_off"] == SPAN[0] and backfill_spans(store) == {}


def test_without_a_tape_nothing_is_asked(world):
    chain, store, _ = world
    store.set_coverage("tape", {"spans": []})
    out = run(chain, store)
    assert chain.requests == [] and "tape" in out["note"]


def test_a_stretch_with_no_signature_on_the_tape_to_list_back_from_is_left(world):
    chain, store, _ = world
    store.conn.execute("DELETE FROM trades WHERE venue='curve'")
    store.conn.commit()
    out = run(chain, store)
    assert out["stretches"]["no_cursor"] == 1 and backfill_spans(store) == {}


def test_no_write_holds_the_database_while_the_backfill_runs(world):
    chain, store, _ = world
    with Guard(store, after=2) as g:
        run(chain, store)
    assert g.transactions >= 4 and g.got_in is True and g.max_rows <= Store.WRITE_BATCH


def test_the_credit_prices_say_where_they_come_from():
    for method in ("getSignaturesForAddress", "getTransaction"):
        price = B.PRICES[method]
        assert price.credits > 0 and price.source and isinstance(price.verified, bool)
    out = B.price_table()
    assert out["getTransaction"]["credits"] == 1 and "assumed" not in out["getTransaction"]["source"]
    assert B.price_table({"x": B.Price(3, "a guess", False)})["x"]["source"] == "a guess (assumed)"


def test_the_command_runs_from_the_command_line(world, monkeypatch, capsys):
    chain, store, _ = world
    from snipetech.solana.rpc import SolanaRpc

    monkeypatch.delenv("PUMPFUNDATA_API_KEY", raising=False)  # the bought files' key is not this command's
    monkeypatch.setattr(SolanaRpc, "from_config", classmethod(lambda cls, cfg: chain.rpc()))
    monkeypatch.setattr("time.time", lambda: float(NOW))
    # an hour after each buy, as this world's stretch is (the command gives 300 s when nothing is said: 12 Nov)
    base = ["--db", store.path, "history", "amm-backfill", "--wallet", W, "--days", "1", "--min-sol", "0.1",
            "--after-secs", "3600"]  # fmt: skip
    assert cli.main([*base, "--dry-run", "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["wallets"][0]["to_fetch"] == 3 and chain.count("getTransaction") == 0
    assert cli.main([*base, "--max-credits", "6"]) == 0
    text = capsys.readouterr().out
    assert "stopped" in text and "6 credits" in text
    assert cli.main([*base, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stretches"]["fetched"] == 1 and out["stopped"] is None
    fresh = Store(store.path)
    try:
        assert backfill_spans(fresh) == {M: [STRETCH]}
    finally:
        fresh.close()
    assert cli.main(["--db", store.path, "history", "amm-backfill", "--days", "1"]) == 2  # no wallet named
    assert "--wallet" in capsys.readouterr().err


def test_a_group_names_the_wallets(world, monkeypatch, capsys):
    chain, store, _ = world
    from snipetech.solana.rpc import SolanaRpc

    store.set_label(W, "Ansem", kind="kol")
    monkeypatch.setattr(SolanaRpc, "from_config", classmethod(lambda cls, cfg: chain.rpc()))
    monkeypatch.setattr("time.time", lambda: float(NOW))
    assert cli.main(["--db", store.path, "history", "amm-backfill", "--group", "big", "--days", "1", "--min-sol",
                     "0.1", "--dry-run", "--json"]) == 0  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert [(w["wallet"], w["name"]) for w in out["wallets"]] == [(W, "Ansem")]


def test_the_job_is_allowed_on_the_server_and_for_the_agents(tmp_path, monkeypatch):
    from snipetech import ops
    from snipetech.operator import jobs

    monkeypatch.setattr(jobs, "AGENT_BACKFILL_OPEN", True)  # the mechanism, once it is opened (closed by default)

    assert "history" in ops.ALLOWED
    # a backfill holds no deploy (12 Nov evening: the analyst's ten-wallet run held one for 42 minutes and more while
    # the fix it was waiting for sat pushed; it reports no numbers, it has loaded its decoder by its first rows, it
    # survives the restart and goes on where it stopped)
    assert not ops.holds_deploy(["history", "amm-backfill", "--group", "big", "--days", "7"])
    assert not ops.holds_deploy(["history", "range"])  # the bought files' commands hold nothing back, as before
    assert cli.batch_name("history", "amm-backfill") in cli.BATCH_COMMANDS
    assert cli.batch_name("history", "fetch") not in cli.BATCH_COMMANDS
    assert "history" not in jobs.RESEARCH_COMMANDS  # the bought files stay the owner's: only this action is an agent's
    assert ("history", "amm-backfill") in jobs.RESEARCH_ACTIONS
    oj = jobs.OperatorJobs(repo=tmp_path, data=tmp_path, db=tmp_path / "t.db", branch="b")
    started = []
    oj.ops.op_run = lambda argv: started.append(argv) or {"id": "j1"}
    assert oj.start(["history", "amm-backfill", "--group", "big", "--days", "7", "--dry-run", "--json"], now=1e9)
    assert started == [["history", "amm-backfill", "--group", "big", "--days", "7", "--dry-run", "--json"]]
    with pytest.raises(PermissionError, match="not a research command"):
        oj.start(["history", "fetch", "--from", "2026-11-01"], now=1e9)
    with pytest.raises(PermissionError, match="5,000,000"):
        oj.start(["history", "amm-backfill", "--group", "big", "--days", "7", "--max-credits", "90000000"], now=1e9)
    with pytest.raises(PermissionError, match="5,000,000"):
        oj.start(["history", "amm-backfill", "--group", "big", "--max-credits=90000000"], now=1e9)
    with pytest.raises(PermissionError, match="owner's"):  # the month's limit is the owner's to lift
        oj.start(["history", "amm-backfill", "--group", "big", "--month-credits", "90000000"], now=1e9)
    assert len(started) == 1


def test_dry_runs_cannot_pass_the_months_limit_and_are_counted_while_they_run(world, monkeypatch):
    chain, store, _ = world
    monkeypatch.setattr(B, "USAGE_EVERY", 1)
    seen = []
    handle = chain.handle

    def watching(request):
        seen.append(store.usage_month("helius-backfill", NOW))
        return handle(request)

    chain.handle = watching
    run(chain, store, dry_run=True)
    assert seen == [0, 1, 2]  # a dry run that is killed half way has still been counted
    store.usage_add("helius-backfill", B.MONTH_CREDITS - 3 - 2, NOW)  # two credits are left of the month
    out = run(chain, store, dry_run=True)
    assert out["stopped"] == "month_credits" and out["credits"]["counted"] == 2
    assert store.usage_month("helius-backfill", NOW) == B.MONTH_CREDITS
    out = run(chain, store, dry_run=True)  # nothing is left: a dry run asks nothing either
    assert out["credits"]["counted"] == 0 and chain.count("getSignaturesForAddress") == 5


def test_the_texts_no_longer_call_a_dry_run_a_few_credits():
    import inspect

    from snipetech.operator import tools

    history = next(a for a in cli.build_parser()._actions if getattr(a, "choices", None) and "history" in a.choices)
    text = " ".join(history.choices["history"].format_help().split())
    assert "a few credits" not in text and "counted in the month" in text
    assert "a few credits" not in " ".join((B.backfill.__doc__ or "").split())
    told = " ".join(inspect.getsource(tools).split())
    assert "not much to pay" not in told and "37,000" in told and "a dry run too" in told


# -- what an agent may ask for (operator/jobs.py) ----------------------------------------------------------------------


def _agent_jobs(tmp_path, db=None):
    from snipetech.operator import jobs

    oj = jobs.OperatorJobs(repo=tmp_path, data=tmp_path, db=db or tmp_path / "t.db", branch="b")
    (tmp_path / "jobs").mkdir(exist_ok=True)
    started = []
    oj.ops.op_run = lambda argv: started.append(argv) or {"id": f"j{len(started)}"}
    return oj, started


BIG = ["history", "amm-backfill", "--group", "big"]


@pytest.mark.parametrize("more", [
    ["--month", "1e12"], ["--mo=1e12"], ["--month-c", "100000000"], ["--month-credit", "5"], ["--mo", "1"],
    ["--max-cred", "9e9"], ["--max-cred=9e9"], ["--max-credit", "9000000"], ["--max-cre", "90000000"],
    ["--max-cred", "9e9", "--month", "1e12", "--after", "86400"],  # the review's own command
    ["--max-c", "9e9"], ["--max", "9e9"], ["--m", "9e9"],  # ambiguous to the parser: never left to it
    ["--max-credits", "inf"], ["--max-credits", "nan"], ["--max-credits=NaN"], ["--max-credits", "1e999"],
    ["--max-credits", "lots"], ["--max-credits"], ["--max-credits", "5000001"], ["--max-credits", "--dry-run"],
    ["--max-credits", "1000", "--max-credits", "9e9"],  # the parser takes the last one
])  # fmt: skip
def test_an_agent_cannot_lift_a_backfills_limits_by_shortening_or_bending_a_flag(tmp_path, more):
    # 12 Nov, the review: the check looked for the two flags spelled in full, and the command line took any prefix of
    # them (--max-cred 9e9 --month 1e12 ran with both limits lifted)
    oj, started = _agent_jobs(tmp_path)
    with pytest.raises(PermissionError, match="no job started|owner's"):
        oj.start([*BIG, *more], now=1e9)
    assert started == []


def test_an_agents_backfill_inside_the_limits_starts(tmp_path):
    from snipetech.operator import jobs

    oj, started = _agent_jobs(tmp_path)
    for more in (["--max-credits", "5000000"], ["--max-credits=4e6"], ["--max-credits", "400000", "--days", "3"],
                 ["--min-sol", "2", "--max-create-share", "1"], ["--max-stretch-txs", "20000"],
                 [f"--max-stretch-txs={jobs.AGENT_BACKFILL_STRETCH_TXS}"]):  # fmt: skip
        assert oj.start([*BIG, *more], now=1e9)
        assert cli.build_parser().parse_args([*BIG, *more])  # and the command line reads what the check let through
    assert len(started) == 6


@pytest.mark.parametrize("argv", [
    [*BIG, "--max-cred", "9e9"], [*BIG, "--month", "1e12"], [*BIG, "--after", "86400"], [*BIG, "--dry"],
    ["--verb", "init"], ["lab", "--hour", "24"], ["tips", "--gm"],
])  # fmt: skip
def test_the_command_line_reads_no_shortened_flag(argv, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.build_parser().parse_args(argv)
    assert stop.value.code == 2 and "unrecognized arguments" in capsys.readouterr().err


def test_every_command_and_sub_command_parser_refuses_shortened_flags():
    import argparse

    parser = cli.build_parser()
    (subs,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert parser.allow_abbrev is False and len(subs.choices) > 50
    assert [name for name, sp in subs.choices.items() if sp.allow_abbrev] == []


def test_a_hint_that_names_a_flag_names_it_in_full():
    # the one place the repo told somebody to type a shortened flag (found by scanning every tracked file for a
    # `--word` that is a prefix of a flag and no flag itself): `oktech cycle` said "pass --ignore MINT"
    import inspect

    from snipetech import service

    text = inspect.getsource(service)
    assert "pass --ignore-mint MINT" in text and "--ignore MINT" not in text
    assert cli.build_parser().parse_args(["oktech", "cycle", "--ignore-mint", "M"]).ignore_mint == ["M"]


def test_the_servers_own_commands_are_spelled_in_full():
    """The timer units and the nightly job call the command line: each of their commands must parse now that a
    shortened flag is refused."""
    import re
    import shlex
    from pathlib import Path

    units = Path(__file__).resolve().parents[1] / "deploy" / "systemd"
    parser, seen = cli.build_parser(), 0
    for unit in sorted(units.glob("*.service")):
        text = unit.read_text().replace("\\\n", " ").replace("$S $DB", "snipetech --db x.db")
        for found in re.findall(r"snipetech (--db \S+ [a-z][^;>'\n]*)", text):
            parser.parse_args(shlex.split(found))  # SystemExit fails the test
            seen += 1
    assert seen >= 13


# -- a backfill that stopped at its limit goes on at once --------------------------------------------------------------


def _ended(oj, jid: str, argv: list[str], log: str, ago: int = 600, now: int = NOW) -> None:
    meta = {"id": jid, "argv": argv, "started": now - ago - 60, "ended": now - ago, "exit_code": 0, "running": False}
    (oj.ops.data / "jobs" / f"{jid}.json").write_text(json.dumps(meta))
    (oj.ops.data / "jobs" / f"{jid}.log").write_text(log)


@pytest.fixture
def command(world, monkeypatch, capsys):
    """The real command on this world: (run it and give back the job log it leaves, stdout and stderr together)."""
    chain, store, _ = world
    from snipetech.solana.rpc import SolanaRpc

    monkeypatch.setattr(SolanaRpc, "from_config", classmethod(lambda cls, cfg: chain.rpc()))
    clock = __import__("time").time

    def go(argv: list[str]) -> str:
        monkeypatch.setattr("time.time", lambda: float(NOW))
        try:
            assert cli.main(["--db", store.path, *argv]) == 0
        finally:
            monkeypatch.setattr("time.time", clock)
        io = capsys.readouterr()
        return io.err + io.out

    return go


ONE = ["history", "amm-backfill", "--wallet", W, "--days", "1", "--min-sol", "0.1", "--after-secs", "3600"]


@pytest.mark.parametrize("fmt", [[], ["--json"]])
def test_a_backfill_that_stopped_at_its_credit_limit_may_be_started_again_at_once(world, command, tmp_path, fmt):
    # 12 Nov, the review: the result said "Run it again to go on where it stopped" and the agents' start refused the
    # same command for six hours ("already ran ... read its log")
    _, store, _ = world
    oj, started = _agent_jobs(tmp_path, store.path)
    argv = [*ONE, "--max-credits", "9", *fmt]  # the wallet's 6 and the pool's 3 pages fit; its 4 transactions do not
    _ended(oj, "b1", argv, command(argv))
    assert backfill_spans(store) == {}
    assert oj.start(argv, now=NOW)["id"] and started == [argv]
    # while the run that goes on is still running, a third start of the same command is not a second fetch beside it
    going = {"id": "b2", "argv": argv, "started": NOW - 30, "ended": None, "exit_code": None, "running": True,
             "pid": os.getpid()}  # fmt: skip
    (oj.ops.data / "jobs" / "b2.json").write_text(json.dumps(going))
    with pytest.raises(PermissionError, match="job b2 is this same backfill and still runs"):
        oj.start(argv, now=NOW)
    # the run that went on ended whole (1 + 3 pages, 4 transactions): the same command is then held like any other
    whole = command(argv)
    assert backfill_spans(store) == {M: [STRETCH]}
    _ended(oj, "b2", argv, whole, ago=60)
    with pytest.raises(PermissionError, match="already ran: job b2"):
        oj.start(argv, now=NOW)
    assert len(started) == 1


def test_only_a_real_backfill_stopped_at_its_own_limit_is_started_again(world, command, tmp_path):
    _, store, _ = world
    oj, started = _agent_jobs(tmp_path, store.path)
    dry = [*ONE, "--max-credits", "2", "--dry-run", "--json"]  # a dry run keeps no place: again is the same again
    log = command(dry)
    assert json.loads(log[log.index("{"):])["stopped"] == "max_credits"
    _ended(oj, "d1", dry, log)
    with pytest.raises(PermissionError, match="already ran: job d1"):
        oj.start(dry, now=NOW)
    # another command whose log happens to carry the words is no backfill
    lab = ["lab", "--latency", "0", "--json"]
    _ended(oj, "l1", lab, 'amm-backfill: stopped at max_credits\n{"stopped": "max_credits"}')
    with pytest.raises(PermissionError, match="already ran: job l1"):
        oj.start(lab, now=NOW)
    # a backfill that ended whole, or whose log is gone
    done = [*ONE, "--json"]
    _ended(oj, "w1", done, command(done))
    with pytest.raises(PermissionError, match="already ran: job w1"):
        oj.start(done, now=NOW)
    (oj.ops.data / "jobs" / "w1.log").unlink()
    with pytest.raises(PermissionError, match="already ran: job w1"):
        oj.start(done, now=NOW)
    assert started == []


def test_a_backfill_stopped_at_the_months_limit_waits_for_the_owner(world, command, tmp_path, monkeypatch):
    _, store, _ = world
    oj, started = _agent_jobs(tmp_path, store.path)
    store.usage_add("helius-backfill", B.MONTH_CREDITS - 5, NOW)
    argv = [*ONE, "--json"]
    log = command(argv)
    assert json.loads(log[log.index("{"):])["stopped"] == "month_credits"
    _ended(oj, "m1", argv, log)
    with pytest.raises(PermissionError, match="month's limit.*owner"):
        oj.start(argv, now=NOW)  # nothing is left of the month: the same run would stop at its first request
    monkeypatch.setattr(B, "MONTH_CREDITS", B.MONTH_CREDITS + 1000)  # the owner lifts it
    assert oj.start(argv, now=NOW)["id"] and started == [argv]


# -- what is never a stretch -------------------------------------------------------------------------------------------


def test_a_buy_of_something_that_is_not_a_coin_gets_no_stretch(world, monkeypatch):
    # 12 Nov: a big wallet swapped into USDC through a PumpSwap pool and the backfill fetched that pool's trades
    from snipetech import positions

    assert B.NON_COIN_MINTS is positions.NON_COIN_MINTS
    assert {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", WSOL_MINT} <= set(B.NON_COIN_MINTS)
    chain, store, _ = world
    monkeypatch.setitem(positions.NON_COIN_MINTS, M, "a stablecoin")
    out = run(chain, store)
    assert out["stretches"]["buys_non_coin"] == 1 and out["stretches"]["wanted"] == 0
    assert out["wallets"][0]["buys"] == 0 and backfill_spans(store) == {}
    assert not any(p[0] == P for m, p in chain.requests if m == "getSignaturesForAddress")  # the pool is not listed
    assert chain.count("getTransaction") == 3  # the wallet's own three
    assert "1 buy of something that is not a coin" in out["note"] and "a stablecoin" in out["note"]
    dry = run(chain, store, dry_run=True)
    assert dry["stretches"]["buys_non_coin"] == 1 and dry["estimate"]["stretches_known"] == 0


def test_a_stretch_busier_than_the_limit_is_listed_and_not_fetched(world):
    # 12 Nov: one coin with 48,689 trades in 20 minutes took most of a ten-wallet run's 392,543 requests. Listing a
    # stretch costs a credit a thousand signatures, so its size is known before a transaction is paid for
    chain, store, _ = world
    out = run(chain, store, max_stretch_txs=3)  # the stretch around the buy holds 4 transactions
    s = out["stretches"]
    assert s["too_busy"] == 1 and s["wanted"] == 1 and s["fetched"] == 0 and s["transactions"] == 0
    assert chain.count("getTransaction") == 3 and backfill_spans(store) == {}  # the wallet's own three, no pool's
    assert out["max_stretch_txs"] == 3 and out["stopped"] is None
    assert out["too_busy"] == [{"mint": M, "from": STRETCH[0], "to": STRETCH[1], "transactions": 4,
                                "buys": [{"wallet": W, "sig": R, "ts": T}]}]  # fmt: skip
    assert "1 stretch" in out["note"] and "more than 3 transactions" in out["note"]
    assert "4 credits" in out["note"] and "--max-stretch-txs" in out["note"]
    # the dry run's estimate leaves the same stretch out
    dry = run(chain, store, dry_run=True, max_stretch_txs=3)
    est = dry["estimate"]
    assert dry["stretches"]["too_busy"] == 1 and dry["too_busy"][0]["transactions"] == 4
    assert est["stretches_known"] == 1 and est["stretches_too_busy"] == 1
    assert est["stretch_transactions_known"] == 0 and est["credits"] == 0
    assert run(chain, store, dry_run=True)["estimate"]["credits"] == 4  # at the limit given when nothing is said
    # at a limit the stretch fits it is fetched: the limit is "more than", and the next run goes on
    out = run(chain, store, max_stretch_txs=4)
    assert out["stretches"]["fetched"] == 1 and out["stretches"]["too_busy"] == 0 and out["too_busy"] == []
    assert backfill_spans(store) == {M: [STRETCH]} and out["note"] == ""


def test_the_limit_on_a_stretchs_size_from_the_command_line_and_from_an_agent(world, command, tmp_path):
    from snipetech.operator import jobs

    _, store, _ = world
    assert B.MAX_STRETCH_TXS == 8000 and B.MAX_STRETCH_TXS < jobs.AGENT_BACKFILL_STRETCH_TXS
    assert cli.build_parser().parse_args(ONE).max_stretch_txs is None  # the module's 8,000 when nothing is said
    log = command([*ONE, "--max-stretch-txs", "3", "--json"])
    out = json.loads(log[log.index("{"):])
    assert out["stretches"]["too_busy"] == 1 and out["max_stretch_txs"] == 3
    text = command([*ONE, "--max-stretch-txs", "3"])
    assert "more than 3 transactions" in text  # the words of the result say it too
    assert cli.main(["--db", store.path, *ONE, "--max-stretch-txs", "0"]) == 2
    oj, started = _agent_jobs(tmp_path)
    over = str(jobs.AGENT_BACKFILL_STRETCH_TXS + 1)
    for more in (["--max-stretch-txs", over], [f"--max-stretch-txs={over}"], ["--max-stretch", "9000"],
                 ["--max-stretch-txs", "0"], ["--max-stretch-txs", "-1"], ["--max-stretch-txs", "1e4"],
                 ["--max-stretch-txs", "inf"], ["--max-stretch-txs"]):  # fmt: skip
        with pytest.raises(PermissionError, match="no job started"):
            oj.start([*BIG, *more], now=1e9)
    assert started == []


def test_a_second_run_with_a_longer_time_after_the_buy_fetches_only_the_seconds_that_are_missing(world):
    # what the agents are told to do for a lab that sells in parts: backfill the same wallets again with a longer
    # "--after-secs". The stretch held is not fetched again; the seconds past it are
    chain, store, n = world
    run(chain, store, after_secs=3600)
    chain.add(*plain(CURVE_TX, "tape-after-4", T + 3800))
    tape_row(store, "tape-after-4", T + 3800)
    chain.requests.clear()
    out = run(chain, store, after_secs=3700)
    assert out["stretches"]["wanted"] == 1 and out["stretches"]["fetched"] == 1 and out["stretches"]["held"] == 0
    assert chain.fetched() == [n["p-after"]]  # the one trade between the old end and the new one
    assert backfill_spans(store) == {M: [[T - B.BEFORE_SECS, T + 3700 + B.TAIL_SECS]]}
