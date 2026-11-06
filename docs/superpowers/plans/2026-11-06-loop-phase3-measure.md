# Learning loop, phase 3 — measure: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every live buy and sell of the book wallet becomes a `fills` row audited from the chain and the tape (position in the slot, trades behind the copied wallet, price gap, fees paid, PnL per round trip), and two derived tables — `bid_model` (what each bid arm buys) and `wallet_live` (live expectancy per copied wallet) — update every five minutes without anyone watching.

**Architecture:** A `src/snipetech/measure/` package: `classify.py` (pure functions over `Trade`, `TxMeta` and the store: leader match, position, gap, fees, round-trip pairing), `run.py` (`run_measure`: new trades since the last run → fingerprints → fills → aggregates; `backfill` for the 6 Nov copy test), a 5-minute systemd timer, `measure run|backfill` in the CLI and on the ops allowlist. The qualify stage reads the measured landing once an arm has 30 fills. All of `oktech copyaudit` moves in.

**Tech Stack:** Python 3.11+, sqlite3 via `Store`, `Ingestor.ensure_tx_meta` (RPC, version-1 transactions), `analysis.audit._position_in_slot`, existing `TxMeta`.

**Spec:** `docs/superpowers/specs/2026-11-06-learning-loop-design.md` (Stage 5 — measure; Tables `fills`, `bid_model`, `wallet_live`).

## Global Constraints

- Money columns in lamports are integers; SOL columns are floats rounded to 6 places; never a float where the spec says lamports.
- `fills.sig` is the primary key: re-running measure over the same window never duplicates a row (`INSERT OR REPLACE`).
- The leader of a buy is the most recent earlier buy on the same coin by any wallet the book copies (`oktech_snipers` payload `ctbotWallets[].address`) or, when none is configured, any wallet labelled `kol`, `wallet` or `cand`; no leader → the row keeps `wallet_copied = NULL` and `trades_behind = NULL` (a strategy or manual buy, not a copy).
- `price_gap_pct = 100 × ((q_after_ours / q_after_leader)² − 1)` on the bonding curve (the copy-audit formula); on PumpSwap (`venue = 'amm'`) the same formula with `q_after/b_after` prices.
- Failed sends are cost: every failed signature of the wallet since the last run becomes a `fills` row with `side = 'failed'`, `sol = 0`, `priority_lamports` and `tip_lamports` from its fingerprint when the RPC returns one; counted in `bid_model.failed_n`.
- Never a network call in tests; RPC behind an injectable `ensure_meta`/`failed_sigs` callable.
- `.venv/bin/pytest -q` green before every commit.

## Review Focus

1. A buy with no earlier leader buy on the coin must not be matched to the leader's *later* buy → Task 2 `test_no_leader_before_us_means_no_copy`.
2. Three identical OKTech sends where one lands: the landed one is a buy row, the two others are `failed` rows, and the arm's `failed_n` counts both → Task 3 `test_duplicate_sends_count_as_cost`.
3. A sell without a matching buy in the window (position opened before the window) must not produce a negative PnL round trip → Task 2 `test_sell_without_buy_is_left_open`.
4. `wallet_live` decay: a fill 14 days old weighs a quarter of a fresh one (half-life 7 d) → Task 3 `test_wallet_live_decay`.
5. The measured landing override must only move `landing_pos` when an arm has n ≥ 30 and must be visible to `qualify` the next run → Task 3 `test_landing_override_after_30_fills` and Task 4's `run_qualify` reads `coverage("measure:landing_pos")`.

---

### Task 1: Tables and helpers

**Files:** Modify `src/snipetech/store.py` (SCHEMA + methods); Test `tests/test_store_fills.py`.

**Interfaces (Produces):**
- Tables `fills(sig TEXT PRIMARY KEY, ts INTEGER, slot INTEGER, pos_in_slot INTEGER, slot_txs INTEGER, wallet TEXT, wallet_copied TEXT, event_kind TEXT, mint TEXT, side TEXT, sol REAL, tokens REAL, trades_behind INTEGER, price_gap_pct REAL, priority_lamports INTEGER, tip_lamports INTEGER, cu_limit INTEGER, cu_used INTEGER, landing_service TEXT, pnl_sol REAL, exit_reason TEXT, sniper_id TEXT, bid_arm TEXT, buy_sig TEXT)` with index `(wallet, ts)`; `bid_model(arm TEXT PRIMARY KEY, n INTEGER, failed_n INTEGER, mean_pos REAL, p25_pos REAL, p75_pos REAL, mean_gap_pct REAL, mean_fee_sol REAL, updated_at INTEGER)`; `wallet_live(wallet TEXT PRIMARY KEY, n INTEGER, mean_sol REAL, lower_sol REAL, last_fill INTEGER, status TEXT, updated_at INTEGER)`.
- `Store.put_fill(row: dict) -> None` (INSERT OR REPLACE by sig; unknown keys ignored), `Store.fills(wallet: str | None = None, since_ts: int = 0, side: str | None = None, limit: int = 1000) -> list[dict]` (newest first), `Store.fill(sig) -> dict | None`, `Store.put_bid_model(row: dict)`, `Store.bid_model() -> list[dict]`, `Store.put_wallet_live(row: dict)`, `Store.wallet_live(status: str | None = None) -> list[dict]`.

- [ ] Steps: tests (`test_put_fill_is_idempotent`, `test_fills_filters_and_order`, `test_bid_model_and_wallet_live_roundtrip`), implement, suite, commit `feat(store): fills, bid_model, wallet_live`.

### Task 2: Classification (pure)

**Files:** Create `src/snipetech/measure/__init__.py`, `classify.py`; Test `tests/test_measure_classify.py`.

**Interfaces (Produces):**
- `leaders_for(store) -> set[str]`: `ctbotWallets[].address` from every active `oktech_snipers` payload (`store.oktech_snipers()`), else `labels("kol") ∪ labels("wallet") ∪ labels("cand")` keys.
- `classify_trade(store, trade: Trade, leaders: set[str], meta: TxMeta | None) -> dict`: the `fills` row for one trade: `side` buy|sell, `sol = quote/1e9`, `tokens = base`, `pos_in_slot, slot_txs` from `analysis.audit._position_in_slot`, for buys `wallet_copied`, `trades_behind` (buys strictly between the leader's buy and ours, as in `Service.oktech_copy_audit`), `price_gap_pct`, and the fee fields from `meta` (`priority_lamports`, `tip_lamports`, `cu_limit`, `cu_consumed → cu_used`, `landing_service = meta.tips[0]["service"]` if any); missing meta → the fee fields are `None`.
- `pair_round_trips(buys: list[dict], sells: list[dict], per_tx_sol: float) -> list[dict]`: FIFO per mint; each closed trip is `{buy_sig, sell_sig, pnl_sol, hold_secs}` with `pnl_sol = received − spent − per_tx_sol × 2` using `quote ∓ fee` like `audit_report`; sells with no open buy are returned under `"unmatched"`; partial sells close the proportional share.
- `arm_for(store, sniper_id: str | None) -> str`: the sniper record's `name` suffix `arm:<name>` if present, else `"control"`.

- [ ] Steps: tests (`test_copy_buy_matches_latest_earlier_leader_buy`, `test_no_leader_before_us_means_no_copy`, `test_fee_fields_from_meta_and_none_without`, `test_pair_round_trips_fifo_and_partial`, `test_sell_without_buy_is_left_open`), implement, suite, commit `feat(measure): fill classification`.

### Task 3: The runner, aggregates, backfill

**Files:** Create `src/snipetech/measure/run.py`; Modify `src/snipetech/config.py` (`MeasureConfig(half_life_days: float = 7.0, min_arm_fills: int = 30, overlap_secs: int = 600)` registered as `measure`), `src/snipetech/cli.py` (`measure run [--since <ts>]`, `measure backfill --since <ts> [--until <ts>]`), `src/snipetech/ops.py` (`"measure"` in ALLOWED), `src/snipetech/qualify/run.py` (`landing_pos = store.coverage("measure:landing_pos").get("pos") or q.landing_pos`); Test `tests/test_measure_run.py`.

**Interfaces (Produces):**
- `async run_measure(svc, cfg, now: float, *, since_ts: int | None = None, ensure_meta: Callable[[list[str]], Awaitable[int]] | None = None, failed_sigs: Callable[[str, int], Awaitable[list[dict]]] | None = None) -> dict`: window = `since_ts` or `coverage("measure:last_ts") − overlap_secs` (first run: `now − 86400`); trades = `store.trades_for_user(cfg.operator.wallet, window)`; `await ensure_meta(sigs)` (default `svc.ingestor.ensure_tx_meta`); classify every trade; failed signatures from `failed_sigs(wallet, window)` (default: `svc.rpc.get_signatures` with `err` set) → `failed` rows; `pair_round_trips` over all fills of the wallet in the last 7 days → `pnl_sol`, `exit_reason = "sold"`, `buy_sig` on sell rows; `bid_model` per arm over the last 7 days of buys (`n`, `failed_n`, mean/p25/p75 of `pos_in_slot`, mean gap, mean fee in SOL = priority + tip + 0.000005 × 1); `wallet_live` per `wallet_copied` from closed trips with weights `0.5 ** (age_days / half_life)`: `n` (unweighted count), `mean_sol` (weighted mean), `lower_sol` (weighted mean − 1.28 × weighted sd / √n_eff), `status = "live"` when `n ≥ 10` else `"warming"`; when any arm has `n ≥ min_arm_fills`, `set_coverage("measure:landing_pos", {"pos": f"+{int(p75_pos)}", "arm": arm, "ts": now})`; `set_coverage("measure:last_ts", {"ts": now})`; returns `{fills, buys, sells, failed, trips, arms: {...}, wallets_live: n, landing_pos: str | None}`.
- `async backfill(svc, cfg, since_ts, until_ts) -> dict` = `run_measure` over `[since_ts, until_ts]` without moving `measure:last_ts`.

- [ ] Steps: tests (`test_run_writes_fills_and_trips_from_a_synthetic_tape`, `test_duplicate_sends_count_as_cost`, `test_wallet_live_decay`, `test_landing_override_after_30_fills`, `test_backfill_does_not_move_last_ts`, `test_cli_measure_parses`), implement, suite, commit `feat(measure): runner, bid model, live expectancy, backfill`.

### Task 4: Timer, operator, docs, acceptance

**Files:** Create `deploy/systemd/snipetech-measure.service/.timer` (`OnCalendar=*:0/5`, `RandomizedDelaySec=20`, `TimeoutStartSec=10m`); Modify `deploy/setup.sh` (the enable line), `src/snipetech/ops.py` (UNITS), `src/snipetech/operator/snapshot.py` (`WATCHED_UNITS`/`ONESHOT_UNITS`, `fills_24h: int`), `src/snipetech/operator/tools.py` (`read_fills(hours=24, limit=50)` → fills + bid_model + wallet_live, redacted), `src/snipetech/operator/prompts.py` (one paragraph: fills are the truth for the live landing; paper numbers are provisional once `wallet_live.n ≥ 10`), `src/snipetech/dash.py` (the fills section shows `bid_model` rows), `README.md` (3e: measure), `tests/test_deploy_units.py`.
- Acceptance on the server: deploy when nothing runs; `remote --wait run measure backfill --since 1793940000 --until 1793947000` (the 6 Nov 04:36–05:45 copy test) → 7 buy fills with `trades_behind` 2–4 and gaps 18–43 % matching docs/live-recipe.md; `measure run` on an empty window returns zeros; `setup.sh` + `enable --now snipetech-measure.timer` by the owner; `docs/live-recipe.md` gets the backfilled table.

- [ ] Steps: tests, implement, suite, commit `feat: measure timer, operator fills tool, docs`; deploy; backfill; record.
