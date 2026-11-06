# Learning loop, phase 1 — resources, alerts, digest, Telegram commands: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The operator round checks every paid resource and the wallet float, alerts the owner on Telegram with the exact thing to do, sends one daily digest, and obeys `/status`, `/stop`, `/resume` from the owner's Telegram.

**Architecture:** Deterministic checks inside the existing `operate` round (`src/snipetech/operator/`): a `resources` module computes a list of `Resource` rows from the ledger, the data-API clients' usage counters, the chain and the disk; critical rows become `[decision]` notes written by the round itself (no Claude call needed) and re-fire at most daily; a `commands` module polls Telegram `getUpdates` for the owner's chat and executes three commands before the gate runs; a `digest` module sends one summary per UTC day. Usage counters live in the store's `coverage` table so every process shares them.

**Tech Stack:** Python 3.11+, httpx, sqlite3 via `Store`, existing operator modules, pytest with mock transports.

**Spec:** `docs/superpowers/specs/2026-11-06-learning-loop-design.md` (sections "Resources and alerts", "Safety", "Stage 6 — think").

## Global Constraints

- No tool or command may start a sniper, move SOL or change a wallet; `/stop` only stops snipers that hold nothing.
- Commands are accepted only from the stored Telegram `chat_id`; anything else is ignored and logged.
- Secrets never appear in messages; `ops.redact` wraps every outbound text.
- Every alert message names the action: the amount to buy, the URL, or the command to run.
- Tests: `.venv/bin/pytest -q` green before each commit; no network in tests.
- Commit messages end with the branch's two attribution trailers.

## Review Focus

1. A resource that stays critical for days must alert once a day, not every 15 minutes → Task 1 test `test_critical_resource_alerts_once_per_day`.
2. `/stop` while a sniper holds a coin must leave that sniper running and say so → Task 2 test `test_stop_keeps_holders_running`.
3. A Telegram message from a stranger (different chat id) must be ignored even if it says `/stop` → Task 2 test `test_commands_from_other_chats_are_ignored`.
4. Telegram API down during `getUpdates` must not break the round → Task 2 test `test_poll_failure_is_logged_not_raised`.
5. The digest must not send twice on a day when the operator runs several rounds after 07:00 → Task 3 test `test_digest_once_per_day`.

---

### Task 1: Resource checks and alerts

**Files:**
- Create: `src/snipetech/operator/resources.py`
- Modify: `src/snipetech/config.py` (new `ResourcesConfig`, `Config.resources`), `src/snipetech/operator/snapshot.py` (field `resources: list[dict]`), `src/snipetech/operator/run.py` (alerts after the snapshot), `src/snipetech/store.py` (usage counter helpers)
- Test: `tests/operator/test_resources.py`

**Interfaces:**
- Produces: `ResourcesConfig`: `anthropic_credit_usd: float = 100.0`, `anthropic_alert_pct: float = 20.0`, `anthropic_min_days: float = 3.0`, `birdeye_alert_pct: float = 15.0`, `solanatracker_month_requests: int = 200_000`, `solanatracker_alert_pct: float = 15.0`, `vybe_month_credits: int = 25_000`, `vybe_alert_pct: float = 15.0`, `disk_alert_gb: float = 30.0`, `sol_float_margin: float = 0.2`, `oktech_fail_alert: int = 3`.
- Produces: `Store.usage_add(provider: str, n: int = 1, now: float | None = None) -> int` and `Store.usage_month(provider: str, now: float | None = None) -> int` — stored under coverage key `api_usage:<provider>:<YYYY-MM>` as `{"n": int}`.
- Produces: `@dataclass Resource(name: str, value: float | None, unit: str, threshold: float | None, status: str, message: str)` with `status ∈ ok | warn | crit | unknown`; `to_dict()`.
- Produces: `check_resources(cfg: Config, store: Store, snap: Snapshot, ledger: Ledger, *, disk_free_gb: Callable[[], float], birdeye_credits: Callable[[], dict] | None, now: float) -> list[Resource]` covering: `anthropic_credit` (credit − `ledger.spent_since(credit_set_at)`; `credit_set_at` from coverage key `resources:anthropic_credit` written by `config set`; crit when left ≤ alert_pct % or days-left at the 7-day average ≤ min_days; message: "top up 100 $ at console.anthropic.com, then run: snipetech config set resources.anthropic_credit_usd <new total>"), `birdeye_cu` (from `birdeye_credits()` remaining/total of cycle; crit ≤ 15 %), `solanatracker_requests` and `vybe_credits` (from `usage_month`; crit ≤ 15 % left), `sol_float` (`snap.equity` vs `needed = book.max_wallets × book.fill_max_sol × 2 + sol_float_margin` when `book.enabled`, else `0.2`; crit below; message "send X SOL to <wallet>"), `disk` (crit < disk_alert_gb; message "resize to the 320 GB plan"), `oktech_failures` (failed transactions by the wallet in the last hour from `snap.fills`-independent RPC count passed in as `snap.errors`? no — from a new snapshot field `failed_tx_1h: int | None` filled by `take_snapshot` via `getSignaturesForAddress` limit 50; crit ≥ oktech_fail_alert).
- Produces: `alert_lines(resources: list[Resource]) -> list[str]` — one line per crit, `"<name>: <message>"`.
- Modify `run.py`: after `decide`, for each crit resource whose name has not been noted today (`notes.has_today(f"resource {name}", now)`), write `[decision] resource <name>: <message>` and send it; log all statuses on one line.

- [ ] **Step 1: Write the failing tests**

```python
def test_usage_counter_is_monthly(tmp_store):
    assert tmp_store.usage_add("solanatracker", 5, now=NOV_6) == 5
    assert tmp_store.usage_month("solanatracker", now=NOV_6) == 5 and tmp_store.usage_month("solanatracker", now=DEC_6) == 0

def test_anthropic_credit_alert_at_20_percent(tmp_store, ledger_with_spend_85):
    cfg = Config(); cfg.resources.anthropic_credit_usd = 100
    rs = check_resources(cfg, tmp_store, snap(), ledger_with_spend_85, disk_free_gb=lambda: 50, birdeye_credits=None, now=NOW)
    r = next(x for x in rs if x.name == "anthropic_credit")
    assert r.status == "crit" and "console.anthropic.com" in r.message and "config set resources.anthropic_credit_usd" in r.message

def test_disk_and_float_and_birdeye_thresholds(tmp_store, ledger):
    cfg = Config(); cfg.book.enabled = True; cfg.book.max_wallets = 6; cfg.book.fill_max_sol = 1.0
    rs = {x.name: x for x in check_resources(cfg, tmp_store, snap(native_sol=1.0, wsol=0.0), ledger, disk_free_gb=lambda: 12.0,
                                               birdeye_credits=lambda: {"used": 7_000_000, "remaining": 900_000}, now=NOW)}
    assert rs["disk"].status == "crit" and "320" in rs["disk"].message
    assert rs["sol_float"].status == "crit" and "send" in rs["sol_float"].message and "6VFrZ15V" in rs["sol_float"].message
    assert rs["birdeye_cu"].status == "crit"

def test_critical_resource_alerts_once_per_day(run_env):   # two rounds, same crit → one [decision] note, one Telegram send
    ...; assert paths.notes.read_text().count("resource disk") == 1 and len(sent) == 1

def test_unknown_when_a_probe_fails(tmp_store, ledger):
    rs = {x.name: x for x in check_resources(Config(), tmp_store, snap(), ledger, disk_free_gb=lambda: 1/0, birdeye_credits=None, now=NOW)}
    assert rs["disk"].status == "unknown"
```

- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/operator/test_resources.py` → FAIL (imports).
- [ ] **Step 3: Implement** `ResourcesConfig` (and a minimal `BookConfig` with `enabled=False`, `max_wallets=6`, `fill_min_sol=0.2`, `fill_max_sol=1.0`, `daily_risk_sol=2.0`, `day_stop_sol=1.0`, `equity_floor_sol=3.0` so the float check can read it; the full book comes in phase 4), the store helpers, `resources.py`, the snapshot field `failed_tx_1h` (count of `err != None` among the wallet's last 50 signatures within 3600 s; `None` on RPC failure) and `resources: list[dict]`, and the `run.py` alert step. `credit_set_at`: when `Config.set("resources.anthropic_credit_usd", v)` runs through the CLI `config set`, also `store.set_coverage("resources:anthropic_credit", {"usd": v, "set_at": now})`; `check_resources` reads it and falls back to the config value with `set_at = 0`.
- [ ] **Step 4: Run** the file and the full suite → PASS.
- [ ] **Step 5: Commit** `feat(operator): resource checks and daily-deduplicated alerts`.

### Task 2: Telegram commands

**Files:**
- Create: `src/snipetech/operator/commands.py`
- Modify: `src/snipetech/operator/notify.py` (`get_updates(token, offset) -> list[dict]` with httpx, 10 s timeout), `src/snipetech/operator/run.py` (poll + handle before the gate; STOP file respected), `src/snipetech/operator/paths.py` (`stop_flag` → `root / "STOP"`)
- Test: `tests/operator/test_commands.py`

**Interfaces:**
- Produces: `@dataclass Command(name: str, args: list[str], chat_id: int, update_id: int)`; `parse_updates(updates: list[dict], owner_chat_id: int) -> tuple[list[Command], int | None]` (returns commands from the owner only and the highest update_id seen; ignores others, counting them in a returned stat via a module-level logger call); `poll_commands(token, paths, get: Callable | None = None) -> list[Command]` reading/writing `offset` in `paths.telegram` JSON next to `chat_id`; never raises (returns `[]` and logs on failure).
- Produces: `async handle_command(cmd: Command, *, svc, cfg, paths, snap: Snapshot | None, notifier: Notifier, notes: NoteSink, now: float) -> str` (the reply text, also sent via `notifier.send`): `/status` → equity, held coins, snipers running, today's spend, last note time, book enabled flag, top 3 resources not ok; `/stop` → write `paths.stop_flag` with the timestamp, then for every sniper with `status == "running"` whose wallet holds nothing (`svc.holdings(cfg.operator.wallet)` empty) call `svc.oktech_task("stop", id)`; reply lists stopped ids and any left running because a coin is held; `/resume` → remove the flag; `/help` or unknown → the command list. `/why` and `/budget` reply "available in a later phase".
- Modify `run.py`: `operate` polls and handles commands right after acquiring the lock (before the snapshot), with `svc` available; while `paths.stop_flag` exists the round still runs health and alerts but writes `stopped by owner` into the log line and the snapshot's `expected` is treated as zero snipers.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_only_owner_commands():
    ups = [{"update_id": 1, "message": {"chat": {"id": 7}, "text": "/stop"}}, {"update_id": 2, "message": {"chat": {"id": 9}, "text": "/stop"}},
           {"update_id": 3, "message": {"chat": {"id": 7}, "text": "/why W1 extra"}}]
    cmds, last = parse_updates(ups, owner_chat_id=7)
    assert [(c.name, c.args) for c in cmds] == [("stop", []), ("why", ["W1", "extra"])] and last == 3

def test_commands_from_other_chats_are_ignored(): ...  # a stranger's /stop produces no Command

def test_poll_persists_offset_and_survives_failure(tmp_path):
    paths = OperatorPaths(tmp_path).ensure(); paths.telegram.write_text(json.dumps({"chat_id": 7}))
    cmds = poll_commands("T", paths, get=lambda url, params: {"result": [{"update_id": 41, "message": {"chat": {"id": 7}, "text": "/status"}}]})
    assert [c.name for c in cmds] == ["status"] and json.loads(paths.telegram.read_text())["offset"] == 42
    assert poll_commands("T", paths, get=lambda u, p: (_ for _ in ()).throw(TimeoutError())) == []

async def test_stop_keeps_holders_running(fake_svc_with_running_snipers_and_a_held_coin): ...  # only the holding-nothing sniper is stopped; reply names both
async def test_status_reply_has_equity_and_spend(...): ...
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**. **Step 4: Run** full suite → PASS. **Step 5: Commit** `feat(operator): telegram commands /status /stop /resume`.

### Task 3: Daily digest

**Files:**
- Create: `src/snipetech/operator/digest.py`
- Modify: `src/snipetech/config.py` (`OperatorConfig.digest_hour_utc: int = 7`), `src/snipetech/operator/run.py`
- Test: `tests/operator/test_digest.py`

**Interfaces:**
- Produces: `digest_text(snap: Snapshot, resources: list[dict], ledger: Ledger, store: Store, now: float) -> str` — plain text, under 1 500 chars: date; equity and 24 h change (from the previous day's digest value stored in coverage `digest:last`); spend today and 7-day total; resources not ok; operator notes written in 24 h (count, last decision line); candidates/verdict counts when the tables exist (phase 2: `getattr(store, "verdict_counts", None)`), else omitted.
- Produces: `digest_due(paths, now, hour_utc) -> bool` (after `hour_utc` and no `paths.root / f"digest-{date}.sent"`); `mark_sent(paths, now)`.
- Modify `run.py`: after alerts, if due → send and mark; failure to send does not mark.

- [ ] **Step 1: Write the failing tests** (`test_digest_once_per_day`, `test_digest_text_mentions_equity_spend_and_crit_resources`, `test_not_due_before_hour`).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**. **Step 4: Run** → PASS. **Step 5: Commit** `feat(operator): daily digest`.

### Task 4: Usage counters in the data-API clients, plus a minimal Vybe client

**Files:**
- Modify: `src/snipetech/birdeye.py`, `src/snipetech/solanatracker.py` (optional `counter: Callable[[int], None]` called with the request count after each call), `src/snipetech/cli.py` (pass `svc.store.usage_add` partials when constructing clients), `src/snipetech/ops.py` (`REDACT_ENV` += `VYBE_API_KEY` already; nothing else)
- Create: `src/snipetech/vybe.py` — `Vybe(key=None, transport=None, counter=None)`, header `X-API-KEY`, `top_traders(resolution="30d", limit=1000, sort="realizedPnlUsd") -> list[dict]` mapped to `{wallet, realized_usd, unrealized_usd, win_rate, volume_usd, trades, tokens}` from Vybe's `accountAddress, realizedPnlUsd, unrealizedPnlUsd, winRate, tradesVolumeUsd, tradesCount, uniqueTokensTraded`; `doctor` row `vybe`.
- Test: `tests/test_vybe.py`, additions to `tests/test_birdeye.py` / `tests/test_solanatracker.py` (`counter` called once per request).

- [ ] Steps 1–5 as above; commit `feat: usage counters for data APIs; vybe client and doctor row`.

### Task 5: Wire-up, docs, deploy, acceptance

**Files:**
- Modify: `README.md` (3e: alerts, digest, commands), `docs/index.md` (no change unless a doc is added), `src/snipetech/operator/prompts.py` (one line: resources are checked by code; the model only explains anomalies).
- Acceptance on the server: deploy when no job runs; `operate --dry-run` shows `resources`; send `/status` from the owner's phone and see the reply within 15 minutes (or trigger `remote --wait run operate`); `/stop` then `/resume` round-trip with no sniper running; the first digest the next morning.

- [ ] **Step 1:** docs + prompt line; suite green; commit `docs: operator alerts, digest and commands`.
- [ ] **Step 2:** deploy and run the acceptance list; record results in `docs/live-recipe.md` under "Operator phase 1".

### Task 6: Live dashboard

**Files:**
- Create: `src/snipetech/dash.py` (state aggregation), `src/snipetech/dash.html` (single page, no framework, auto-refresh 15 s, phone layout)
- Modify: `src/snipetech/api.py` (routes `GET /dash` → the HTML, `GET /api/dash/state` → JSON; HTTP basic auth with password from env `DASH_TOKEN`; both refuse without it), `deploy/systemd/snipetech-api.service` (enabled by setup), `deploy/Caddyfile` or `bootstrap.sh` (proxy `/dash*` and `/api/dash*` to the api port), `deploy/setup.sh` env template (`DASH_TOKEN=`)
- Test: `tests/test_dash.py`

**Interfaces:**
- Produces: `dash_state(svc, paths: OperatorPaths, cfg, now) -> dict` with keys `services`, `jobs` (running + last 10 ended with argv/exit), `operator` (last 20 notes, last 5 decisions, spend today / 7 d, next timer run), `resources` (from the last snapshot), `snapshot` (equity, held, snipers by status, tape lag), `candidates` (counts by status, top 10 qualified by score when the tables exist), `fills` (last 20 when the table exists), `book` (enabled, stop flag), `version` (git short hash from `deploy`), `generated_at`.
- The page renders those sections; red/amber/green by status; never shows a secret (state is built from the snapshot and tables only).

- [ ] **Step 1: Tests**: `test_state_without_tables_is_complete` (keys present, empty lists), `test_dash_requires_token` (401 without, 200 with), `test_state_has_no_secret_values` (monkeypatched env keys never appear in the JSON).
- [ ] **Step 2–5:** implement, suite, commit `feat: live dashboard at /dash`; deploy: `DASH_TOKEN` set by the owner over SSH, `systemctl enable --now snipetech-api`, Caddy reload; open `https://snipetech.45-77-66-186.sslip.io/dash` on the phone.
