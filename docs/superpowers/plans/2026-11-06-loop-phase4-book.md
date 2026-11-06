# Learning loop, phase 4 — the book (dry first): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every five minutes the book decides, from the chain and the tables, which qualified wallets to copy, with what fill and bid arm, and when to stop; with `[book] enabled = false` it only writes what it would do, so the owner watches the decisions for a day before a single SOL moves.

**Architecture:** A `src/snipetech/book/` package: `policy.py` (pure: inputs in, a `BookPlan` of actions out; no I/O), `state.py` (the `book_state` table and the day's accounting from `fills`), `run.py` (`book run`: gather → plan → in dry mode log, in live mode execute through the existing `Service.oktech_*` calls, one Telegram line per action). A 5-minute systemd timer. The policy never stops a sniper while the wallet holds a coin, never starts below the equity floor, and sizes every fill so the arm's fee is at most 3 % of it.

**Tech Stack:** Python 3.11+, sqlite3 via `Store`, `Service.oktech_status / oktech_copylist / oktech_task / holdings`, `operator.notify.Notifier`.

**Spec:** `docs/superpowers/specs/2026-11-06-learning-loop-design.md` (Stage 4 — the book; Safety; Tables `book_state`).

## Global Constraints

- `cfg.book.enabled` is read from the config file only; nothing in the API, Telegram or a job argument can flip it. Dry mode is the default and is also forced when `equity < equity_floor_sol`.
- The policy is a pure function of `(equity, day, qualified, live, bid_model, snipers, holdings, cfg, now)`; every action it returns carries a one-line reason.
- Never stop a sniper while the wallet holds any coin (the same rule as `/stop`); never create or start when equity is below the floor, when the day's loss has reached `day_stop_sol`, or when `daily_risk_sol` is used up.
- Fill size: `max(fill_min_sol, min(fill_max_sol, arm_fee_sol / 0.03))`; if even `fill_max_sol` cannot keep the arm's fee at or under 3 %, that arm is skipped with a note (at the spec's arms, the paid arm at 0.04 SOL needs a 1.33 SOL fill and is skipped until the owner raises `fill_max_sol`).
- Bid arms live in `[book] bid_arms` as a dict keyed by arm name (`{control = {priority = 0.001, tip = 0.002}, paid = {priority = 0.02, tip = 0.02}}`): the config loader merges dicts and would stringify a list.
- Book snipers are named `snipetech book <wallet6> arm:<arm>`; `measure.arm_for` already reads that tag.
- Every live action is a Telegram line and a `book_state` row; tests use a fake Service (no network) and assert zero OKTech calls in dry mode.

## Review Focus

1. A wallet that drops out of the top set while the wallet holds a coin must produce `keep (holding)` and no stop → Task 1 `test_never_stops_while_holding`.
2. Equity exactly at the floor, then one lamport below: the second run produces stop actions for non-holders and no create → Task 1 `test_equity_floor_edge`.
3. A finished sniper (spent its buys) for a wallet still in the top set is replaced by a new one, not restarted → Task 1 `test_spent_sniper_is_replaced`.
4. `explore_share = 0.2` with 6 wallets assigns the alternative arm to exactly 1 wallet (ceil), never the same wallet two runs in a row when the plan is stable → Task 1 `test_explore_assignment_is_deterministic`.
5. Dry mode with `enabled = false` makes zero OKTech calls even when the plan says create/start/stop → Task 3 `test_dry_mode_calls_nothing`.

---

### Task 1: The policy (pure)

**Files:** Create `src/snipetech/book/__init__.py`, `policy.py`; Modify `src/snipetech/config.py` (`BookConfig` gains `min_score: float = 0.005`, `explore_share: float = 0.2`, `bid_arms: dict[str, dict] = {"control": {"priority": 0.001, "tip": 0.002}, "paid": {"priority": 0.02, "tip": 0.02}}`, `send_count: int = 1`, `max_fee_share: float = 0.03`, `live_n: int = 10`); Test `tests/test_book_policy.py`.

**Interfaces (Produces):**
- `@dataclass BookInputs(equity_sol: float | None, day_realised_sol: float, day_risk_used_sol: float, qualified: list[dict] (latest verdicts with status qualified), live: dict[str, dict] (wallet_live by wallet), bid_model: dict[str, dict] (by arm), snipers: list[dict] (OKTech tasks: id, name, status), holdings: list[dict], now: float)`.
- `@dataclass Action(kind: str  # create | start | stop | keep | skip | kill, wallet: str | None, arm: str | None, fill_sol: float | None, sniper_id: str | None, reason: str)` and `@dataclass BookPlan(actions: list[Action], kill_reason: str | None, ranked: list[dict], notes: list[str])`.
- `rank_wallets(qualified, live, cfg) -> list[dict]`: score = `live[w].mean_sol` when `live[w].n >= cfg.live_n` else the verdict score; drop below `min_score`; top `max_wallets`.
- `fill_for(arm: dict, cfg) -> float | None` per the sizing rule; `best_arm(bid_model, cfg) -> str` (the arm with the lowest `p75_pos` among arms with `n >= 30`, else `"control"`); `assign_arms(ranked, bid_model, cfg) -> dict[wallet, arm]` (alternative arm for the last `ceil(explore_share × len)` wallets of the ranking, deterministic).
- `plan_book(inp: BookInputs, cfg) -> BookPlan` applying stops first (floor, day stop, daily risk), then the diff against current book snipers (`keep` / `create`+`start` / `stop` only when holdings are empty / `skip` with reason).

- [ ] Steps: tests (`test_rank_prefers_live_after_ten_fills`, `test_fill_sizing_keeps_fee_under_three_percent_or_skips_arm`, `test_never_stops_while_holding`, `test_equity_floor_edge`, `test_spent_sniper_is_replaced`, `test_explore_assignment_is_deterministic`, `test_day_stop_and_daily_risk`), implement, suite, commit `feat(book): the policy`.

### Task 2: State and the day's accounting

**Files:** Create `src/snipetech/book/state.py`; Modify `src/snipetech/store.py` (table `book_state(key TEXT PRIMARY KEY, value TEXT, updated INTEGER)`, `Store.book_get(key, default=None)`, `Store.book_set(key, value)`); Test `tests/test_book_state.py`.

**Interfaces (Produces):**
- `day_accounting(store, wallet, now) -> dict`: `{day, realised_sol (sum of sell fills' pnl_sol since 00:00 UTC), risk_used_sol (sum of buy fills' sol since 00:00 UTC), equity_at_day_start (from book_state, set on the first run of the day)}`.
- `record_run(store, plan, dry, now)`: `book_state.last_run`, `kill_reason`, `enabled`, and one row per action in `events(kind="book_action", ...)` once phase 5's table exists (until then `scores` with kind `book`).

- [ ] Steps: tests, implement, suite, commit `feat(book): state and day accounting`.

### Task 3: The runner, CLI, timer

**Files:** Create `src/snipetech/book/run.py`; Modify `src/snipetech/cli.py` (`book run [--live]` where `--live` is refused unless `cfg.book.enabled`), `src/snipetech/ops.py` (`"book"` allowed), `deploy/systemd/snipetech-book.service/.timer` (`*:0/5`, `RandomizedDelaySec=15`, `TimeoutStartSec=4m`), `deploy/setup.sh`, `src/snipetech/operator/snapshot.py` (units; `book` summary in the snapshot), `src/snipetech/operator/prompts.py` (the book's actions are decisions the model explains, never re-takes), `README.md`; Test `tests/test_book_run.py`.

**Interfaces (Produces):**
- `async run_book(svc, cfg, now, *, notifier=None, dry: bool | None = None) -> dict`: gathers `BookInputs` (equity via `operator.snapshot.rpc_balances`, holdings via `svc.holdings`, snipers via `svc.oktech_status`, verdicts and live via the store), `plan_book`, then: dry → notes `[book dry] would <action> <wallet6> <arm> <fill> — <reason>` (deduplicated per day per action) and no OKTech call; live → execute in order stop → create → start through `svc.oktech_copylist(... create=True, tag=f"arm:{arm}")` / `svc.oktech_task`, each a Telegram line; returns `{dry, actions: {...counts}, kill_reason, ranked: n}`.
- Acceptance: on the server `book run` in dry mode lists the plan against the real verdicts and the real OKTech list; a day of dry notes reviewed by the owner before `enabled = true`.

- [ ] Steps: tests (`test_dry_mode_calls_nothing`, `test_live_mode_executes_in_order_with_telegram_lines`, `test_forced_dry_below_floor`, `test_cli_refuses_live_when_disabled`), implement, suite, commit `feat(book): dry runner, timer, operator wiring`.
