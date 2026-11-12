"""The history backfill (`snipetech history amm-backfill`, 12 Nov 2026): a big wallet's PumpSwap buys in older coins and
the pool's trades around each, fetched from the RPC into the tape's own tables, with a coverage record of the stretches
held whole. Real PumpSwap transactions and moved copies of them (tests/amm_chain.py), served page by page to the repo's
own RPC client."""

import asyncio
import copy
import json

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


def test_a_dry_run_counts_and_writes_nothing(world):
    chain, store, _ = world
    before = store._q("SELECT COUNT(*) FROM trades")[0][0], store._q("SELECT key FROM coverage ORDER BY key")
    out = run(chain, store, dry_run=True)
    assert (store._q("SELECT COUNT(*) FROM trades")[0][0], store._q("SELECT key FROM coverage ORDER BY key")) == before
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
    assert store.usage_month("helius-backfill", NOW) == 6 + 8


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
    assert ops.holds_deploy(["history", "amm-backfill", "--group", "big", "--days", "7"])
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
