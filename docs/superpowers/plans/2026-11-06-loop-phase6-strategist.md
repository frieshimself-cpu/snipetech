# Learning loop, phase 6 — the strategist (weekly, within bounds): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once a week a stronger model reads a week of fills, the bid model, verdicts and the event-expectancy table and proposes parameter changes to `[book]` and `[qualify]` as a JSON patch; a patch inside the owner's `[autotune]` bounds is applied and announced, one outside is sent to Telegram as a question. Every applied change is an event, so results can be attributed to it.

**Architecture:** `src/snipetech/strategist/` with `bounds.py` (pure: validate a patch against `[autotune]`), `evidence.py` (the week's numbers as one compact JSON document, from the tables only), `turn.py` (the Opus turn with two tools: `read_evidence`, `propose_patch`; `turn_usd` 10), `apply.py` (write the accepted keys into the config file atomically through the existing `config set` path, write an `events(kind="param_change")` row, Telegram line). A weekly timer (Sunday 05:00 UTC, after the daily job) runs `snipetech strategist run`; `--dry-run` prints the evidence and the proposal without applying.

**Tech Stack:** Python 3.11+, the Anthropic SDK tool runner as in `operator/turn.py`, `Store`, `config set` (tomllib/dump_toml), `operator.notify.Notifier`.

**Spec:** `docs/superpowers/specs/2026-11-06-learning-loop-design.md` (Stage 6 — think: Strategist; Safety).

**Status (6 Nov, 14:30 UTC):** built and deployed as planned (`strategist/` bounds, evidence, turn, apply, run; `[autotune]`; Sunday timer). Ruling: the strategist's ledger lines carry mode `strategist` (priced at the analyst rates) and count toward the operator's daily cap; `Config.save` was made atomic and env-safe (it no longer writes the Helius key into the file) because the apply step rewrites the config.

## Global Constraints

- The strategist can change only keys listed in `[autotune]`, each with `min`/`max` (numbers) or `choices` (strings); anything else in a patch is refused with the reason, and the whole patch is then a question, never partially applied.
- Default bounds: `book.fill_max_sol ≤ 1.0`, `book.min_score` within ×0.5–×2 of the current value, `book.explore_share` 0–0.3, `book.bid_arms.*.priority ≤ 0.05`, `book.bid_arms.*.tip ≤ 0.05`, `qualify.max_gap3_pct` 5–30, `qualify.min_n_tape` 10–40, `qualify.landing_pos` in `{+3, +6, +9}`. `book.enabled`, wallets, keys and sizes above the owner's ceilings are never autotunable.
- One strategist turn a week, `turn_usd` 10, on `cfg.operator.analyst_model`; a failed or over-budget turn changes nothing and writes one note.
- Every applied change: `events(kind="param_change", data={key, old, new, reason, evidence_ts})` and a Telegram line; the config file write is atomic (temp + rename) and keeps the owner's comments where the dump format allows.
- Tests with canned evidence and a scripted model reply; the apply step is tested against a temp config file.

## Review Focus

1. A patch with one in-bounds key and one out-of-bounds key is applied for neither and asked as one question → Task 1 `test_partial_patch_is_a_question`.
2. A patch that sets `book.enabled` is refused whatever the bounds say → Task 1 `test_enabled_is_never_tunable`.
3. `landing_pos` outside the choices (`+4`) is refused; `+9` is applied and the qualify stage uses it next run → Task 3 `test_landing_choice_applies`.
4. Two strategist runs in the same week: the second does nothing and says so → Task 3 `test_weekly_guard`.
5. The model proposing nothing is a valid outcome recorded as a note, not an error → Task 2 `test_no_change_is_recorded`.

---

### Task 1: Bounds (pure)

**Files:** Create `src/snipetech/strategist/__init__.py`, `bounds.py`; Modify `src/snipetech/config.py` (`AutotuneConfig(bounds: dict[str, dict] = {...defaults above...})` registered as `autotune`); Test `tests/test_strategist_bounds.py`.

**Interfaces (Produces):**
- `check_patch(patch: dict[str, Any], current: Config, cfg: AutotuneConfig) -> tuple[dict, list[str]]` → `(accepted, refusals)` where `accepted` is non-empty only when every key passed; relative bounds (`rel_min`/`rel_max`) are resolved against the current value.

- [ ] Steps: tests (`test_in_bounds_accepted`, `test_partial_patch_is_a_question`, `test_enabled_is_never_tunable`, `test_relative_bounds`), implement, suite, commit `feat(strategist): autotune bounds`.

### Task 2: Evidence and the turn

**Files:** Create `src/snipetech/strategist/evidence.py`, `turn.py`, `prompts.py`; Test `tests/test_strategist_turn.py` (mocked HTTP transport as in `tests/operator/test_turn.py`).

**Interfaces (Produces):**
- `week_evidence(store, cfg, now) -> dict`: fills by arm (n, failed, p75 position, mean gap, mean fee, PnL), wallet_live (top 10 and bottom 5), verdict counts and the top qualified, `events:edge`, the last 7 `param_change` events, the current `[book]`/`[qualify]` values and the `[autotune]` bounds.
- `async run_strategist_turn(cfg, evidence, ledger, notes, client=None) -> dict` with tools `read_evidence()` and `propose_patch(patch: dict, reason: str)`; returns `{patch, reason, usd, stopped}`; the system prompt says what each key does, that the bounds are hard, and that "no change" is a fine answer.

- [ ] Steps: tests (`test_turn_returns_patch_and_reason`, `test_no_change_is_recorded`, `test_turn_budget_stops`), implement, suite, commit `feat(strategist): evidence and the turn`.

### Task 3: Apply, CLI, timer, docs

**Files:** Create `src/snipetech/strategist/apply.py`, `run.py`; Modify `src/snipetech/cli.py` (`strategist run [--dry-run]`), `src/snipetech/ops.py` (`"strategist"`), `deploy/systemd/snipetech-strategist.service/.timer` (`OnCalendar=Sun *-*-* 05:00:00 UTC`), `deploy/setup.sh`, `README.md`, `tests/test_deploy_units.py`; Test `tests/test_strategist_run.py`.

**Interfaces (Produces):**
- `apply_patch(cfg_path, accepted: dict, reason, store, notifier, now) -> list[dict]` (atomic config write, `param_change` events, Telegram lines).
- `async run_strategist(svc, cfg, now, *, dry_run=False, client=None) -> dict` with the weekly guard (`coverage("strategist:last")`), the bounds check, apply or question (`notifier.send` with the refusals and the proposed patch), and a note either way.

- [ ] Steps: tests (`test_landing_choice_applies`, `test_weekly_guard`, `test_question_when_out_of_bounds`, `test_cli_dry_run_applies_nothing`), implement, suite, commit `feat(strategist): apply, weekly timer, docs`.
