# Learning loop, phase 5 — events: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The things that move a pump.fun coin besides wallets — trending and graduating lists, exact curve crossings, creator fee claims, dev-to-wallet transfers — become rows in an `events` table (and `edges` for relationships) that the lab can use as triggers and the gates can use as filters, with no one watching.

**Architecture:** A `src/snipetech/events/` package: `tables` (store helpers), `feeds.py` (SolanaTracker trending/graduating and Birdeye trending through the clients we already pay for; pump.fun's own frontend is behind a Cloudflare browser check and is not used), `curve.py` (exact crossings from the tape: a coin's curve passing 10 / 30 / 60 SOL of real reserves, the on-chain fact behind "king of the hill"), `fees.py` (creator fee claims decoded from the tape recorder's instruction stream), `dev.py` (the creator's outgoing SOL transfers around launch via the RPC, bounded to coins that reached 20 SOL), and `run.py` (`events poll` every 60 s for feeds, `events scan` hourly for curve crossings, fee claims and dev transfers). The relationship gate already reads `edges` (`paid_by_dev`).

**Tech Stack:** Python 3.11+, sqlite3 via `Store`, `SolanaTracker`/`Birdeye` clients (+counters), `SolanaRpc.get_signatures/get_transactions`, the tape decoder (`solana/normalize.py`) for fee-claim instructions.

**Spec:** `docs/superpowers/specs/2026-11-06-learning-loop-design.md` (Stage 1 — ingest additions; Tables `events`, `edges`; Stage 6 — event expectancy).

## Global Constraints

- Every feed poll is budgeted: SolanaTracker ≤ 2 requests per poll (trending, graduating), Birdeye ≤ 1 (trending, 30 CU); counters report to `Store.usage_add`; a 4xx/5xx or budget stop logs and skips, never raises.
- An event is written once: `events` has a UNIQUE index on `(kind, mint, wallet, slot)`; re-polling the same list re-emits nothing.
- Feed field names are verified on the server with `--raw` before the parser is trusted (SolanaTracker: `GET /tokens/trending/{timeframe}`, `GET /tokens/multi/graduating`; Birdeye: `GET /defi/token_trending`); until then the parser is tolerant and logs unknown shapes.
- Dev-transfer scanning is bounded to launches whose curve reached 20 SOL and to 24 h around the launch; at most 400 launches per hourly scan (Helius credits stay in the low thousands a day).
- Tests with canned JSON and the synthetic tape; no network; `.venv/bin/pytest -q` green before every commit.

## Review Focus

1. A coin that appears in the trending list for ten consecutive polls must produce one `trending` event, not ten → Task 2 `test_feed_diff_emits_once_per_appearance`.
2. A curve that oscillates around 30 SOL must produce one `curve_cross:30` per coin (first crossing only) → Task 3 `test_curve_cross_fires_once`.
3. A dev transfer to a wallet that later buys the coin must produce a `paid_by_dev` edge, and the relationship gate must then veto that wallet → Task 5 `test_paid_by_dev_edge_vetoes_in_relationship_gate`.
4. A fee claim by a wallet other than the creator (fee recipient changed) must be recorded with the real claimer → Task 4 `test_fee_claim_records_claimer`.
5. A feed outage (HTTP 500 for an hour) must leave the poller alive and the other feeds flowing → Task 2 `test_feed_failure_is_isolated`.

---

### Task 1: Tables and helpers

**Files:** Modify `src/snipetech/store.py`; Test `tests/test_store_events.py`.

**Interfaces (Produces):**
- Tables `events(id INTEGER PRIMARY KEY, ts INTEGER, slot INTEGER, kind TEXT, mint TEXT, wallet TEXT, data TEXT, source TEXT)` with `UNIQUE(kind, mint, wallet, slot)` and indexes `(mint, ts)`, `(wallet, ts)`, `(kind, ts)`; `edges(src TEXT, dst TEXT, kind TEXT, first_ts INTEGER, last_ts INTEGER, lamports INTEGER, n INTEGER, PRIMARY KEY (src, dst, kind))`.
- `Store.add_events(rows: list[dict]) -> int` (INSERT OR IGNORE; returns inserted count), `Store.events(kind=None, mint=None, wallet=None, since_ts=0, limit=1000) -> list[dict]` (newest first), `Store.event_counts(since_ts) -> dict[kind, n]`, `Store.upsert_edge(src, dst, kind, ts, lamports) -> None` (first_ts min, last_ts max, lamports sum, n+1), `Store.edges_to(dst, kind=None) -> list[dict]`.

- [ ] Steps: tests (`test_add_events_is_idempotent`, `test_event_filters`, `test_upsert_edge_accumulates`), implement, suite, commit `feat(store): events and edges`.

### Task 2: Feeds and the poller

**Files:** Create `src/snipetech/events/__init__.py`, `feeds.py`, `run.py`; Modify `src/snipetech/solanatracker.py` (`trending(timeframe="1h", limit=50) -> list[dict]`, `graduating(limit=50) -> list[dict]`), `src/snipetech/birdeye.py` (`trending(limit=20) -> list[dict]`, cost 30 CU), `src/snipetech/cli.py` (`events poll [--once] [--raw]`, `events scan`), `src/snipetech/ops.py` (`"events"` allowed), `deploy/systemd/snipetech-events.service` (long-running, `Restart=always`), `deploy/setup.sh`; Test `tests/test_events_feeds.py`.

**Interfaces (Produces):**
- `FeedState(seen: dict[str, set[str]])` persisted in `coverage("events:feeds")`; `diff_feed(kind: str, mints: list[str], state: FeedState, now, slot=None) -> list[dict]` emits `{ts, slot, kind, mint, wallet: None, data: {rank}, source}` for mints not in `state.seen[kind]`; a mint leaves the set after 6 h absence so a coin trending again a day later is a new event.
- `poll_once(store, clients, now, log) -> dict` runs each feed in its own try/except, returns `{kind: n_events, errors: {...}}`.
- `events poll` loops every 60 s (`--once` for a job), writes `coverage("events:last_poll")`; the operator's gate gains the state reason `events feed stale > 15 min` once the service is enabled.

- [ ] Steps: tests (`test_feed_diff_emits_once_per_appearance`, `test_feed_failure_is_isolated`, `test_tolerant_parsers`), implement, suite, commit `feat(events): trending and graduating feeds`.

### Task 3: Curve crossings from the tape

**Files:** Create `src/snipetech/events/curve.py`; Test `tests/test_events_curve.py`.

**Interfaces (Produces):**
- `curve_crossings(store, since_ts, levels=(10, 30, 60)) -> list[dict]`: for every coin with trades since `since_ts`, the first trade whose real reserves (`q_after − 30 SOL virtual`, i.e. `q_after − 30_000_000_000` on a standard curve) reach each level → event `kind = f"curve_cross:{level}"`, `slot`, `ts`, `data = {q_after, trades_before, secs_since_launch}`; idempotent through the UNIQUE index.

- [ ] Steps: tests (`test_curve_cross_fires_once`, `test_levels_and_timing`), implement, hook into `events scan`, suite, commit `feat(events): curve crossings`.

### Task 4: Creator fee claims

**Files:** Modify `src/snipetech/solana/normalize.py` (recognise `collect_creator_fee` / `collect_coin_creator_fee` instructions on pump.fun and PumpSwap; emit a `FeeClaim(sig, slot, ts, mint, claimer, lamports, venue)` next to trades), `src/snipetech/ingest.py` and the LaserStream recorder (`src/snipetech/live.py` or the tape module: persist claims as `events(kind="fee_claim", wallet=claimer, data={lamports, venue})`); Test `tests/test_events_fees.py` with a real mainnet fixture fetched on the server (`tests/fixtures/tx/<sig>.json` of one collect_creator_fee transaction; the discriminators are read from the IDL under `src/snipetech/solana/idl/`).

- [ ] Steps: fetch the fixture (a job on the server: `snipetech mint <a coin whose creator claimed>` or a direct `getTransaction` through a small allowlisted helper), write `test_fee_claim_records_claimer`, implement the decoder branch, run the tape decoder tests, commit `feat(events): creator fee claims from the tape`.

### Task 5: Dev transfers and edges

**Files:** Create `src/snipetech/events/dev.py`; Modify `src/snipetech/events/run.py` (`events scan` calls it), `src/snipetech/qualify/gates.py` (already reads `edges`; add the `co_bundle` edge from `bundle_launch_count` results), Test `tests/test_events_dev.py`.

**Interfaces (Produces):**
- `dev_transfers(svc, since_ts, max_launches=400, min_curve_sol=20) -> int`: for each qualifying launch, the creator's signatures in `[ts − 24 h, ts + 24 h]` (`get_signatures` with `until`/`before` bounds), the SOL transfers out of the creator (`TxMeta.transfers`) → `events(kind="dev_transfer", mint, wallet=dst, data={lamports})` and `upsert_edge(creator, dst, "paid_by_dev", ts, lamports)`; a cursor in `coverage("events:dev_cursor")`.

- [ ] Steps: tests (`test_paid_by_dev_edge_vetoes_in_relationship_gate`, `test_scan_is_bounded_and_resumable`), implement, suite, commit `feat(events): dev transfers and paid_by_dev edges`.

### Task 6: Event expectancy (nightly) and docs

**Files:** Modify `src/snipetech/analysis/lab.py` (a `Trigger("event", kind=...)` that fires at an event's slot for the coin), `src/snipetech/events/run.py` (`events edge` nightly: the lab at `landing_pos` with each event kind as trigger → `coverage("events:edge")` rows `{kind, n, mean_sol, lower_sol}`), `src/snipetech/operator/prompts.py` and `tools.py` (`read_events(kind, hours)`), `src/snipetech/dash.py` (an events section), `README.md`, `tests/test_deploy_units.py`.
- Acceptance on the server: `events poll --once` shows the three feeds answering; `events scan` writes curve crossings for the last 6 h; the first fee claim and dev transfer rows appear; `docs/roadmap.md` gets the first event-expectancy table.

- [ ] Steps: tests, implement, suite, commit `feat(events): event expectancy lab, operator and dashboard wiring`; deploy; enable `snipetech-events.service` (owner); record.
