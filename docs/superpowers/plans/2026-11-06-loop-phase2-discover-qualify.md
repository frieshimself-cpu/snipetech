# Learning loop, phase 2 — discover and qualify: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every hour, every source's candidates land in one table; a continuous qualify stage pushes each candidate through the history, venue, tape, crowding and relationship gates at the measured landing and writes a verdict with a review date; one lab at a time, all automatic.

**Architecture:** Two new packages: `src/snipetech/discovery/` (one module per source behind a common `Source` protocol, a merge step, bans) and `src/snipetech/qualify/` (pure gate functions, a queue runner that labels due wallets, runs the by-wallet lab through the existing `Service` lab entry point, and writes verdicts). Two store tables with small helpers. Two systemd timers. The operator's analyst reads verdicts instead of raw logs.

**Tech Stack:** Python 3.11+, sqlite3 via `Store`, existing lab (`src/snipetech/analysis/lab.py` via `Service`), Birdeye/SolanaTracker/Vybe clients from phase 1.

**Spec:** `docs/superpowers/specs/2026-11-06-learning-loop-design.md` (Stage 2, Stage 3, Tables).

**Status (6 Nov 2026, 10:45 UTC):** Tasks 1–5 built with tests and deployed. Rulings while executing: venues are
`curve`/`amm` (no `pool`); the lab has no Service entry point, so `qualify` calls `analysis.lab.collect` and
`rank_wallets` in-process; the lab's `wallet` triggers fire only at ≥ 1 SOL, so a `cand` trigger group at the 0.1 SOL
floor was added (it also covers the tracked list); a running lab is detected by `snapshot.running_jobs` **and** a
box-wide `lab.lock` that every lab takes (daily job, ops jobs, qualify); the tape source uses the pure-tape
`discover_wallets` (no RPC per wallet); the coin-first source measures the price (q/b) multiple, scans the last 6 h
and caps at 100 coins; an owner source feeds the tracked list; `qualify` runs at most one lab per `lab_interval_h`
(2 h) because the box is CPU-bound; the daily job's six labs became one. Timers must be enabled by the owner once.

## Global Constraints

- One lab at a time, enforced by checking the jobs directory (reuse `operator.jobs.OperatorJobs.running`), never by convention.
- Per-source budgets: Birdeye ≤ 60 000 CU per discover run, SolanaTracker ≤ 1 500 requests, Vybe ≤ 500 credits; a source that hits its budget stops and reports, the run continues.
- Candidates are never deleted; `banned` is a status.
- Gate thresholds come from a `[qualify]` config section (defaults below) so the strategist can tune them later.
- Tests with canned data; no network; `.venv/bin/pytest -q` green per commit.

## Review Focus

1. A wallet seen by three sources must be one row with three sources, not three rows → Task 1 `test_upsert_merges_sources`.
2. A source throwing (API down) must not abort the run or lose the other sources' results → Task 2 `test_source_failure_is_isolated`.
3. Qualify must refuse to start a lab while another lab job is running and retry on the next tick → Task 4 `test_lab_queue_respects_running_lab`.
4. A wallet with 19 tape trades and a strong history must get `candidate` with `next_review` in 72 h, not `qualified` → Task 3/4 tests on the `n` thresholds.
5. A wallet paid by a dev on one of its coins must be `retired` with the reason even if its numbers are good → Task 3 `test_relationship_gate_vetoes`.

---

### Task 1: Tables and helpers

**Files:** Modify `src/snipetech/store.py` (SCHEMA + methods); Test `tests/test_store_candidates.py`.

**Interfaces (Produces):**
- Tables `candidates(wallet TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, sources TEXT, stats TEXT, status TEXT DEFAULT 'candidate')` and `verdicts(id INTEGER PRIMARY KEY, wallet TEXT, computed_at INTEGER, landing_pos TEXT, n_tape INTEGER, mean_sol REAL, lower_sol REAL, hist_days INTEGER, hist_realized_usd REAL, hist_win_rate REAL, hist_trades INTEGER, crowding REAL, venue_share REAL, score REAL, status TEXT, next_review INTEGER, reasons TEXT)` with index `(wallet, computed_at)`.
- `Store.upsert_candidate(wallet: str, source: str, stats: dict, now: int, status: str | None = None) -> None` (merges `sources` as a sorted unique list and `stats[source] = stats`), `Store.candidates(status: str | None = None, due_before: int | None = None, limit: int = 1000) -> list[dict]` (due = no verdict or latest `next_review <= due_before`; ordered: never-evaluated first, then oldest review), `Store.set_candidate_status(wallet, status)`, `Store.put_verdict(v: dict) -> int`, `Store.latest_verdicts(status: str | None = None) -> list[dict]`, `Store.verdict_counts() -> dict[str, int]`.

- [ ] Steps 1–5: tests (`test_upsert_merges_sources`, `test_due_ordering`, `test_latest_verdict_wins`, `test_verdict_counts`), implement, suite, commit `feat(store): candidates and verdicts`.

### Task 2: Discovery sources and the hourly run

**Files:** Create `src/snipetech/discovery/__init__.py`, `sources.py`, `run.py`; Modify `src/snipetech/cli.py` (`discover run [--sources a,b]`), `src/snipetech/ops.py` (`"discover"` in ALLOWED); Test `tests/test_discovery_run.py`.

**Interfaces (Produces):**
- `class Source(Protocol): name: str; def fetch(self, ctx: DiscoverContext) -> list[dict]` where each dict is `{wallet, stats}`; `DiscoverContext(svc, cfg, now, birdeye, solanatracker, vybe)` (clients may be `None`).
- Sources: `TapeSource` (wraps `svc.discover_real_traders(days=2, min_positions=5, limit=300)`), `SolanaTrackerSource(days=90, pages=5)` and `(days=30, pages=3)`, `BirdeyeGainersSource`, `BirdeyeTopTradersSource(top_coins=30)`, `VybeSource(resolution="30d")`, `CoinFirstBuyersSource(min_multiple=5.0, hours=24, per_coin=50)` (coins from the tape whose max `q_after` within the first 10 min ≥ 5 × the create-slot `q_after`; first buyers from SolanaTracker `first_buyers`, falling back to the tape's first 50 buyers).
- `run_discover(ctx, sources: list[Source]) -> dict` — runs each source in a thread, isolates failures (`{source: error}` in the summary), upserts candidates, bans wallets labelled service/platform or flagged bot/exchange by a source, returns `{"new": n, "updated": m, "banned": k, "errors": {...}, "by_source": {...}}`.

- [ ] Steps: tests (`test_source_failure_is_isolated`, `test_bans_platform_wallets`, `test_coin_first_picks_runners`, `test_cli_discover_run`), implement, suite, commit `feat(discovery): hourly multi-source candidate run`.

### Task 3: Gates

**Files:** Create `src/snipetech/qualify/__init__.py`, `gates.py`; Modify `src/snipetech/config.py` (`QualifyConfig`); Test `tests/test_qualify_gates.py`.

**Interfaces (Produces):**
- `QualifyConfig`: `landing_pos: str = "+6"`, `min_hist_trades: int = 20`, `max_hist_trades: int = 3000`, `min_win_rate: float = 35.0`, `min_realized_usd: float = 500.0`, `max_single_share: float = 0.6`, `min_venue_share: float = 0.5`, `min_n_tape: int = 20`, `min_n_tape_strong: int = 10`, `strong_win_rate: float = 60.0`, `strong_coins: int = 50`, `max_gap3_pct: float = 15.0`, `review_qualified_h: int = 24`, `review_candidate_h: int = 72`, `review_retired_h: int = 168`.
- `history_gate(stats: dict, cfg: QualifyConfig) -> tuple[bool, bool, list[str]]` → (passes, strong, reasons) from a normalised stats dict `{trades, win_rate, realized_usd, max_single_share, coins, days}` built by `normalise_history(candidate_stats: dict) -> dict` preferring solanatracker 90 d, then 30 d, then birdeye.
- `venue_share(store, wallet, since_ts) -> float | None` (share of the wallet's tape trades on `venue in ('curve','pool')`; `None` when < 5 trades).
- `crowding(store, wallet, since_ts) -> dict` → `{followers_1s: median distinct buyers within 1 s after its buys, gap3_pct: median price gap between its buy and the 3rd following buy, n}`.
- `relationship_gate(store, wallet) -> tuple[bool, list[str]]` (false when an `edges` row `paid_by_dev` exists for the wallet, or the wallet appears as a bundle member in ≥ 2 launches via existing bundle tables; until the `edges` table exists (phase 5) only the bundle check runs).
- `score(lower_sol, n, venue_share, followers_1s) -> float` = `lower_sol × min(1, n/30) × venue_share × max(0.2, 1 − followers_1s/10)`.
- `verdict_for(wallet, hist, lab_row: dict | None, venue, crowd, rel_ok, rel_reasons, cfg, now) -> dict` → the verdict row with `status` and `next_review` per the spec.

- [ ] Steps: tests for each gate and `test_relationship_gate_vetoes`, `test_verdict_status_matrix` (strong+n10 → qualified; weak+n19 → candidate 72 h; gap3 20 % → candidate with reason), implement, suite, commit `feat(qualify): gates and verdicts`.

### Task 4: The qualify runner and lab queue

**Files:** Create `src/snipetech/qualify/run.py`; Modify `src/snipetech/cli.py` (`qualify run [--max 300]`), `src/snipetech/ops.py` (`"qualify"`); Test `tests/test_qualify_run.py`.

**Interfaces (Produces):**
- `run_qualify(svc, cfg, paths_jobs: Path, now: float, lab_runner: Callable[[list[str], str], dict] | None = None, max_wallets: int = 300) -> dict`: picks due candidates (`store.candidates(due_before=now)`, excluding `banned`), labels them (`store.set_label(w, f"cand:{w[:6]}", "wallet", "qualify")`), and, if no lab job is running (`OperatorJobs(...).running()` has no `argv[0]=="lab"`), runs ONE by-wallet lab at `cfg.qualify.landing_pos` over the labelled set via `lab_runner` (default: `svc` lab call used by the CLI's `lab --by-wallet`), parses `wallets` rows (best exit per wallet), computes gates per wallet and writes verdicts; if a lab is running, writes nothing and returns `{"deferred": True}`. Summary `{evaluated, qualified, candidate, retired, deferred}`.
- Backfill: wallets with no tape trades at all get `candidate` with `next_review` 72 h and reason `no tape data`.

- [ ] Steps: tests (`test_lab_queue_respects_running_lab`, `test_run_writes_verdicts_from_lab_rows`, `test_no_tape_data_defers`), implement, suite, commit `feat(qualify): queue runner`.

### Task 5: Timers, operator wiring, docs, acceptance

**Files:** Create `deploy/systemd/snipetech-discover.service/.timer` (hourly, `OnCalendar=*:07`), `snipetech-qualify.service/.timer` (`*:0/30`, `KillMode=process`); Modify `deploy/setup.sh` (enable both), `src/snipetech/ops.py` (UNITS), `src/snipetech/operator/prompts.py` (the analyst reads `latest_verdicts` through a new tool `read_verdicts(status)` in `operator/tools.py`), `src/snipetech/operator/snapshot.py` (`verdict_counts` into the snapshot), `README.md`, `tests/test_deploy_units.py`.
- Acceptance: deploy; `discover run` by hand → candidates count; `qualify run` by hand → a lab starts, verdicts written; timers enabled; the next analyst turn lists qualified wallets or says none; `docs/roadmap.md` gets the first verdict table.
