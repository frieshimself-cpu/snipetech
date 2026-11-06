# Operator Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `snipetech operate` command, run by a systemd timer every 15 minutes on the server, that snapshots the system, calls Claude only when something changed, lets it read/query/start research jobs/write notes through a restricted tool set, caps spend, and sends decision-level notes to Telegram.

**Architecture:** New package `src/snipetech/operator/` with one module per unit (paths, budget, snapshot, gate, jobs, tools, notify, prompts, turn, run). Pure-data `Snapshot` and `Decision` dataclasses so gate and prompt logic are testable without network. Jobs are started through the existing `ops.Ops` so the ops service and the operator share one job directory and format. Claude is called through the same `tool_runner` pattern as `agent/runner.py`, injected so tests use a fake runner.

**Tech Stack:** Python 3.11+, `anthropic` SDK (already a dependency), `httpx` for Telegram, sqlite3, systemd. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-06-operator-agent-design.md`

## Global Constraints

- No tool may reach OKTech writes, wallets, keys, `/etc/snipetech/env` or the ops service; `oktech` is never in the job allowlist.
- One `lab` job at a time; at most `operator.max_jobs` (default 3) jobs running; enforced in code.
- Spend caps: `operator.daily_usd` default **15**, `operator.turn_usd` default **2**; models `operator.model="claude-sonnet-5-5"`, `operator.analyst_model="claude-opus-5-5"`.
- Gate thresholds: `equity_alert_sol=0.1`, `tape_stale_secs=300`, `analyst_after_utc="06:00"`.
- All operator files live under `<data>/operator/`; secrets are read from the environment only and never written to notes, logs or Telegram.
- Tests: `.venv/bin/pytest -q` green before every commit; the Anthropic client is never called in tests.
- Commit messages end with the two attribution trailers used throughout this branch.

## Review Focus

1. A job metadata file whose process died with the ops service (meta says `running` but no pid alive) must count as *ended* with `exit_code: null` once, not every round → Task 3 (`ended_since` ignores metas already reported in `snapshot.prev.json`).
2. The Anthropic API returning 429/529 or "credit balance too low" must end the round with exit 0, one `[decision]` note per UTC day, no traceback → Task 7 test `test_api_error_writes_one_note`.
3. A Telegram outage (timeout, 4xx) must never fail the round or lose the note → Task 6 `test_send_failure_is_logged_not_raised`.
4. Two timers overlapping (previous round still inside a long Claude turn) → Task 8 lock test; second instance exits 0 with `locked` in the log line.
5. A model that calls `start_job(["lab", ...])` while a lab started by the *ops service* (not by the operator) runs must be refused → Task 5 reads running jobs from disk metadata, not from the operator's own memory.

---

### Task 1: Config section and paths

**Files:**
- Modify: `src/snipetech/config.py` (after `AgentConfig`, and `Config` fields)
- Create: `src/snipetech/operator/__init__.py` (empty), `src/snipetech/operator/paths.py`
- Test: `tests/operator/test_paths_config.py` (+ empty `tests/operator/__init__.py`)

**Interfaces:**
- Produces: `OperatorConfig` dataclass: `wallet: str = ""`, `daily_usd: float = 15.0`, `turn_usd: float = 2.0`, `max_jobs: int = 3`, `max_tool_calls: int = 20`, `equity_alert_sol: float = 0.1`, `tape_stale_secs: int = 300`, `analyst_after_utc: str = "06:00"`, `model: str = "claude-sonnet-5-5"`, `analyst_model: str = "claude-opus-5-5"`, `max_tokens: int = 8000`, `analyst_max_tokens: int = 16000`, `effort: str = "medium"`, `prices: dict[str, float]` (keys `in`, `out`, `cache_read`, `cache_write`, `analyst_in`, `analyst_out`, `analyst_cache_read`, `analyst_cache_write`; USD per million tokens; defaults Sonnet 3/15/0.3/3.75, Opus 5/25/0.5/6.25). `Config.operator: OperatorConfig`.
- Produces: `OperatorPaths(data: Path)` with properties `root` (`data/operator`), `notes`, `snapshot`, `snapshot_prev`, `spend`, `log`, `lock`, `expected`, `inbox`, `candidates`, `telegram`, `jobs` (`data/jobs`), `reports` (`data/reports`); `ensure() -> OperatorPaths` creates `root`; `report(name: str) -> Path` raising `ValueError` when `name` has `/`, `\\` or `..`.

- [ ] **Step 1: Write the failing tests**

```python
def test_operator_config_loads_from_toml(tmp_path):
    p = tmp_path / "s.toml"; p.write_text('[operator]\nwallet = "W"\ndaily_usd = 1.5\nprices = { in = 1.0 }\n')
    cfg = Config.load(p)
    assert cfg.operator.wallet == "W" and cfg.operator.daily_usd == 1.5
    assert cfg.operator.prices["in"] == 1.0 and cfg.operator.prices["out"] == 15.0  # merged over defaults
    assert cfg.operator.model == "claude-sonnet-5-5" and cfg.operator.daily_usd != 15.0

def test_paths_and_report_name_guard(tmp_path):
    ps = OperatorPaths(tmp_path).ensure()
    assert ps.root == tmp_path / "operator" and ps.root.is_dir() and ps.jobs == tmp_path / "jobs"
    assert ps.report("analyst-2026-11-06") == ps.root / "analyst-2026-11-06.md"
    for bad in ("../x", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            ps.report(bad)
```

- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/operator/test_paths_config.py` → FAIL (`OperatorConfig`/`OperatorPaths` not defined).
- [ ] **Step 3: Implement** `OperatorConfig` (dict field via `field(default_factory=lambda: dict(DEFAULT_PRICES))`); make `_apply` merge dicts into an existing dict field instead of replacing it (`current.update(value)` when both are dicts). Implement `OperatorPaths` in `paths.py`.
- [ ] **Step 4: Run** the test file and the full suite → PASS.
- [ ] **Step 5: Commit** `feat(operator): config section and paths`.

### Task 2: Budget ledger

**Files:**
- Create: `src/snipetech/operator/budget.py`
- Test: `tests/operator/test_budget.py`

**Interfaces:**
- Produces: `cost_usd(usage: dict, prices: dict, analyst: bool) -> float` where `usage` has `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` (missing keys = 0). `Ledger(path: Path, prices: dict)` with `record(mode: str, usage: dict, now: float) -> float` (appends one JSON line `{ts, mode, in, out, cache_read, cache_write, usd}` and returns usd), `spent_today(now: float) -> float` (sum of lines whose UTC date equals `now`'s), `spent_since(ts: float) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
def test_cost_from_tokens():
    prices = {"in": 3.0, "out": 15.0, "cache_read": 0.3, "cache_write": 3.75}
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_input_tokens": 2_000_000}
    assert cost_usd(usage, prices, analyst=False) == pytest.approx(3.0 + 1.5 + 0.6)

def test_spent_today_respects_utc_day(tmp_path):
    led = Ledger(tmp_path / "spend.jsonl", DEFAULT_PRICES)
    day = 1_793_923_200  # 2026-11-06 00:00:00 UTC
    led.record("operator", {"input_tokens": 1_000_000}, now=day - 60)     # yesterday
    led.record("operator", {"input_tokens": 1_000_000}, now=day + 60)     # today
    assert led.spent_today(day + 3600) == pytest.approx(3.0)
    assert led.spent_since(day - 3600) == pytest.approx(6.0)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `budget.py`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(operator): spend ledger`.

### Task 3: Snapshot

**Files:**
- Create: `src/snipetech/operator/snapshot.py`
- Test: `tests/operator/test_snapshot.py`

**Interfaces:**
- Produces: `@dataclass Snapshot` with fields `ts: float`, `services: dict[str,str]`, `tape_last_ts: int | None`, `jobs_running: list[dict]`, `jobs_ended: list[dict]` (each `{id, argv, exit_code, ended, log}`), `wallet: str`, `native_sol: float | None`, `wsol: float | None`, `held: list[dict]`, `snipers: list[dict]` (`{id, name, status}`) or `None` when unavailable, `fills: list[dict]` (`{ts, mint, side, sol}`), `paper: dict | None`, `spent_today: float`, `errors: list[str]`; `equity` property (`native_sol + wsol`, `None` if either is `None`). `to_json()/from_json()`.
- Produces: `ended_since(jobs_dir: Path, since_ts: float, already: set[str]) -> list[dict]`: metas with `ended >= since_ts` or (`running` true and pid dead → `exit_code None`), minus ids in `already` (the caller passes the ids listed in `prev.jobs_ended`, so a dead job is reported once).
- Produces: `async take_snapshot(svc, paths: OperatorPaths, cfg: Config, prev: Snapshot | None, now: float, *, services: Callable[[], dict[str,str]] = systemd_services, balances: Callable | None = None) -> Snapshot`. Default `balances` uses `svc.rpc.call("getBalance")` and `svc.holdings(wallet)` plus a `getTokenAccountsByOwner` read of the WSOL account; OKTech via `svc.oktech_status()` with every exception recorded in `errors` and `snipers=None`; fills via `svc.store._q("select ts, mint, is_buy, quote from trades where user=? and ts>? order by ts", ...)`; paper via `analysis.paper.report`; tape via `select max(ts) from trades`.
- Produces: `load_prev(paths) -> Snapshot | None`, `save(snap, paths)` (moves `snapshot.json` → `snapshot.prev.json` first).

- [ ] **Step 1: Write the failing tests**

```python
def test_ended_since_reports_dead_running_job_once(tmp_path):
    jobs = tmp_path / "jobs"; jobs.mkdir()
    (jobs / "a.json").write_text(json.dumps({"id": "a", "argv": ["lab"], "started": 100, "ended": 150, "exit_code": 0, "running": False}))
    (jobs / "b.json").write_text(json.dumps({"id": "b", "argv": ["follow"], "started": 100, "ended": None, "exit_code": None, "running": True, "pid": 999999}))
    out = ended_since(jobs, since_ts=120, already=set())
    assert {j["id"] for j in out} == {"a", "b"} and [j for j in out if j["id"] == "b"][0]["exit_code"] is None
    assert ended_since(jobs, since_ts=120, already={"a", "b"}) == []

async def test_take_snapshot_with_fakes(tmp_path, fake_service):   # fake_service: Store in tmp with 2 trades by wallet W, oktech_status raising
    snap = await take_snapshot(fake_service, OperatorPaths(tmp_path).ensure(), cfg, prev=None, now=2_000,
                               services=lambda: {"snipetech-tape": "active"}, balances=lambda w: (1.5, 0.25, []))
    assert snap.equity == pytest.approx(1.75) and len(snap.fills) == 2 and snap.snipers is None
    assert any("oktech" in e for e in snap.errors) and snap.tape_last_ts == 1_900
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `snapshot.py` (`systemd_services()` runs `systemctl is-active` over `ops.UNITS`; never raises). **Step 4: Run** → PASS. **Step 5: Commit** `feat(operator): snapshot`.

### Task 4: Gate

**Files:**
- Create: `src/snipetech/operator/gate.py`
- Test: `tests/operator/test_gate.py`

**Interfaces:**
- Consumes: `Snapshot`, `OperatorConfig`, `OperatorPaths`.
- Produces: `@dataclass Decision(call: bool, mode: str, reasons: list[str])` (`mode` in `operator|analyst|none`), `expected_state(paths) -> dict` (reads `expected.json`, default `{"snipers_running": 0, "held": 0}`), `analyst_done_today(paths, now) -> bool` (file `analyst-<YYYY-MM-DD>.md` exists), `decide(snap, prev, now, cfg, paths) -> Decision` implementing the spec's list in order: budget reached → `Decision(False, "none", ["budget"])` (the caller writes the once-a-day note); job ended; snipers/held beyond expected or `snipers is None`; equity move > `equity_alert_sol`; tape stale; service not active; inbox non-empty; analyst due; else quiet.

- [ ] **Step 1: Write the failing tests** (one per trigger; the fixture `snap()` builds an all-quiet Snapshot)

```python
def test_quiet_when_nothing_changed(): assert decide(snap(), snap(), NOW, CFG, paths).call is False
def test_job_ended_triggers(): assert "job ended" in decide(snap(jobs_ended=[{"id": "x"}]), snap(), NOW, CFG, paths).reasons
def test_unexpected_sniper_triggers(): assert decide(snap(snipers=[{"status": "running"}]), snap(), NOW, CFG, paths).call
def test_unknown_oktech_state_triggers(): assert decide(snap(snipers=None), snap(), NOW, CFG, paths).call
def test_equity_move_triggers(): assert decide(snap(native_sol=1.0), snap(native_sol=1.2), NOW, CFG, paths).call
def test_small_equity_move_is_quiet(): assert not decide(snap(native_sol=1.0), snap(native_sol=1.05), NOW, CFG, paths).call
def test_tape_stale_triggers(): assert decide(snap(tape_last_ts=NOW - 400), snap(), NOW, CFG, paths).call
def test_service_down_triggers(): assert decide(snap(services={"snipetech-tape": "failed"}), snap(), NOW, CFG, paths).call
def test_inbox_triggers(tmp_path): paths.inbox.write_text("check wallet X"); assert decide(snap(), snap(), NOW, CFG, paths).call
def test_analyst_due_after_0600_once(): d = decide(snap(), snap(), AT_0630, CFG, paths); assert d.mode == "analyst"; paths.report("analyst-2026-11-06").write_text("x"); assert decide(snap(), snap(), AT_0630, CFG, paths).mode != "analyst"
def test_budget_reached_blocks_call(): d = decide(snap(spent_today=15.0, jobs_ended=[{"id": "x"}]), snap(), NOW, CFG, paths); assert d.call is False and d.reasons == ["budget"]
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `gate.py`. **Step 4: Run** → PASS. **Step 5: Commit** `feat(operator): gate`.

### Task 5: Jobs through `Ops`, and the restricted tools

**Files:**
- Modify: `src/snipetech/ops.py` (`_start`, `_job_dict`, `_meta`)
- Create: `src/snipetech/operator/jobs.py`, `src/snipetech/operator/tools.py`
- Test: `tests/operator/test_jobs_tools.py`

**Interfaces:**
- Modify `Ops._start`: record `pid` in the meta dict and run the command through a wrapper that writes the exit
  code to a file so it survives the starting process exiting:
  `cmd = ["/bin/sh", "-c", '"$@"; echo $? > "$SNIPETECH_JOB_EXIT"', "sh", *cmd]` with
  `env["SNIPETECH_JOB_EXIT"] = str(data/jobs/<id>.exit)`. In `_meta()` when `running` is true: if `<id>.exit`
  exists → `ended = its mtime`, `exit_code = int(contents)`, `running = False`; elif `pid` is not alive
  (`os.kill(pid, 0)` raises) → `ended = now`, `exit_code = None`, `note = "process gone"`; else leave it running.
  `op_jobs` already builds its list through `_meta` for every metadata file.
- Produces: `OperatorJobs(repo: Path, data: Path, db: Path, branch: str)` wrapping an `Ops` built exactly as `cli._ops_serve` builds it, `command=[repo/.venv/bin/snipetech, "--db", db]`: `running() -> list[dict]` (metas with `running` true after reconciliation), `start(argv: list[str]) -> dict` raising `PermissionError` when `argv[0] not in RESEARCH_COMMANDS = {"lab","follow","discover-wallets","label","copy-rank","paper","study","copy"}`, when `argv[0]=="lab"` and a running job's `argv[0]=="lab"`, or when `len(running()) >= max_jobs`.
- Produces: `make_operator_tools(svc, paths: OperatorPaths, jobs: OperatorJobs, cfg: OperatorConfig, notes: NoteSink) -> list` of `anthropic` beta tools (`@beta_tool` as in `agent/tools.py`): `read_job_log(id, tail=200)`, `read_file(path)` (allowed roots `paths.reports`, `paths.root`, `paths.jobs`; resolved path must be relative to one), `sql(query)` (uses `ops._read_only_authorizer`, 30 s progress handler, 200 rows, returns JSON `{columns, rows, truncated}`), `start_job(argv: list[str])`, `write_note(text, level="info")`, `set_candidates(items: list[dict])`, `write_report(name, text)`. Every tool returns a string; errors are returned as `"error: ..."` strings, never raised.
- Produces: `NoteSink(paths, now=time.time)` with `write(text: str, level: str) -> str` appending `- <ISO ts> [<level>] <text>\n` to `paths.notes` and returning the line; `level` must be `info` or `decision` (else `ValueError`); `decisions: list[str]` collects decision lines for the notifier.

- [ ] **Step 1: Write the failing tests**

```python
def test_start_refuses_oktech_and_unknown(jobs): 
    for argv in (["oktech", "start"], ["rm", "-rf"], ["sweep"]):
        with pytest.raises(PermissionError): jobs.start(argv)
def test_one_lab_at_a_time(jobs_with_running_lab_meta):  # meta file written by hand with a live pid (os.getpid())
    with pytest.raises(PermissionError, match="lab"): jobs_with_running_lab_meta.start(["lab", "--latency", "0"])
    assert jobs_with_running_lab_meta.start(["follow", "--kols"])["id"]
def test_exit_file_reconciles_meta(tmp_ops):  # start ["status"] via Ops._start with command=[sys.executable, "-c", "print(1)"], wait, delete the in-memory job, read op_jobs
    ...; assert meta["running"] is False and meta["exit_code"] == 0
def test_read_file_outside_roots_refused(tools): assert tools["read_file"]("/etc/snipetech/env").startswith("error")
def test_sql_refuses_writes(tools): assert tools["sql"]("delete from trades").startswith("error")
def test_write_note_format_and_levels(tmp_path):
    sink = NoteSink(OperatorPaths(tmp_path).ensure(), now=lambda: 1_793_923_200)
    assert sink.write("hello", "decision") == "- 2026-11-06T00:00:00Z [decision] hello"
    with pytest.raises(ValueError): sink.write("x", "loud")
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `ops.py` changes, `jobs.py`, `tools.py`. **Step 4: Run** file + `tests/test_ops.py` + full suite → PASS. **Step 5: Commit** `feat(operator): jobs via Ops with exit-file reconciliation; restricted tools`.

### Task 6: Telegram notifier

**Files:**
- Create: `src/snipetech/operator/notify.py`
- Test: `tests/operator/test_notify.py`

**Interfaces:**
- Produces: `Notifier(token: str | None, chat_id: int | None, post: Callable[[str, dict], object] | None = None, log: Callable[[str], None] = print)`; `enabled` property; `send(text: str) -> bool` (POST `https://api.telegram.org/bot<token>/sendMessage` with `{chat_id, text[:4000], disable_web_page_preview: True}` via `httpx.post(timeout=10)`; any exception → `log(...)`, return False; never raises). `from_env(paths) -> Notifier` (token from `TELEGRAM_BOT_TOKEN`, chat id from `paths.telegram` JSON `{"chat_id": N}`). `telegram_setup(token: str, paths, get: Callable | None = None) -> int` calling `getUpdates`, taking the first `message.chat.id` of type `private`, writing `paths.telegram`, then `send("snipetech operator connected")`; raises `RuntimeError("no message from you yet")` when there are no updates.

- [ ] **Step 1: Write the failing tests**

```python
def test_send_posts_truncated_text(): calls=[]; n=Notifier("T", 7, post=lambda u,d: calls.append((u,d))); assert n.send("x"*5000); assert calls[0][0].endswith("/botT/sendMessage") and len(calls[0][1]["text"]) == 4000 and calls[0][1]["chat_id"] == 7
def test_missing_token_is_disabled_and_silent(): n=Notifier(None, None); assert not n.enabled and n.send("x") is False
def test_send_failure_is_logged_not_raised(): logs=[]; n=Notifier("T", 7, post=lambda u,d: (_ for _ in ()).throw(TimeoutError()), log=logs.append); assert n.send("x") is False and logs
def test_setup_stores_chat_id(tmp_path): paths=OperatorPaths(tmp_path).ensure(); cid=telegram_setup("T", paths, get=lambda u: {"result":[{"message":{"chat":{"id":42,"type":"private"}}}]}); assert cid==42 and json.loads(paths.telegram.read_text())["chat_id"]==42
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**. **Step 4: Run** → PASS. **Step 5: Commit** `feat(operator): telegram notifier`.

### Task 7: Prompts and the Claude turn

**Files:**
- Create: `src/snipetech/operator/prompts.py`, `src/snipetech/operator/turn.py`
- Test: `tests/operator/test_turn.py`

**Interfaces:**
- Produces: `OPERATOR_SYSTEM: str`, `ANALYST_SYSTEM: str` (rules from the spec §3/§4: never trade, one lab, the three gates with exact thresholds n ≥ 20, +1 and +6, 30-day history; always end with `write_note`; `decision` level only for the spec's list), `user_message(snap: Snapshot, decision: Decision, inbox: str) -> str` (compact JSON of the snapshot plus reasons; secrets never appear because the snapshot holds none).
- Produces: `async run_turn(cfg: Config, svc, paths, snap, decision, ledger: Ledger, notifier: Notifier, notes: NoteSink, *, runner_factory=None, now=time.time) -> dict` returning `{"usd", "tool_calls", "stopped", "notes"}`. Uses `AsyncAnthropic().beta.messages.tool_runner(...)` with `model = cfg.operator.analyst_model if decision.mode == "analyst" else cfg.operator.model`, `max_tokens` accordingly, `system`, tools from `make_operator_tools`; after each message `ledger.record(mode, message.usage.model_dump())` and stops (`stopped="turn_usd"`) when the turn's usd ≥ `turn_usd`, or `stopped="tool_calls"` past `max_tool_calls`. If the model ended without any `write_note`, write `[info] turn ended without a note` itself. After the turn, every `notes.decisions` line is passed to `notifier.send`. `anthropic.APIError`/`AuthenticationError` → `notes.write(f"api error: {type}", "decision")` once per day via `notes.has_today(prefix: str, now) -> bool` (scans `paths.notes` for a line of today's UTC date containing `prefix`; add it to `NoteSink` in Task 5), return `{"stopped": "api_error"}`, no raise.
- `runner_factory(model, max_tokens, system, tools, messages)` returns an async iterator of messages with `.content`, `.usage`, `.stop_reason` and a `generate_tool_call_response()` coroutine — the tests pass a fake; production passes `None` → real client.

- [ ] **Step 1: Write the failing tests**

```python
async def test_turn_records_usage_and_sends_decisions(fake_runner_calling_write_note_decision, env):
    out = await run_turn(cfg, svc, paths, snap, Decision(True, "operator", ["job ended"]), ledger, notifier, notes, runner_factory=fake_runner_calling_write_note_decision)
    assert ledger.spent_today(NOW) > 0 and notifier.sent == ["- ... [decision] wallet X cleared all gates"] and out["tool_calls"] == 1
async def test_turn_stops_at_turn_usd(fake_runner_expensive, env): out = await run_turn(...); assert out["stopped"] == "turn_usd"
async def test_turn_without_note_adds_one(fake_runner_silent, env): await run_turn(...); assert "turn ended without a note" in paths.notes.read_text()
async def test_api_error_writes_one_note(fake_runner_raising_api_error, env):
    await run_turn(...); await run_turn(...); assert paths.notes.read_text().count("api error") == 1
def test_user_message_has_reasons_and_no_secrets(): m = user_message(snap, Decision(True, "operator", ["tape stale"]), ""); assert "tape stale" in m and "sk-ant" not in m
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**. **Step 4: Run** → PASS. **Step 5: Commit** `feat(operator): prompts and Claude turn`.

### Task 8: `operate` entry point, lock, CLI, ops allowlist

**Files:**
- Create: `src/snipetech/operator/run.py`
- Modify: `src/snipetech/cli.py` (new `cmd("operate", ...)` with `--analyst`, `--dry-run`, `--telegram-setup`, `--repo`, `--branch`; dispatch in `_run`), `src/snipetech/ops.py` (`ALLOWED` += `"operate"`; `UNITS` += `"snipetech-operator"`, `"snipetech-operator.timer"`)
- Test: `tests/operator/test_run.py`

**Interfaces:**
- Produces: `async operate(cfg: Config, svc, *, analyst: bool = False, dry_run: bool = False, repo: Path, branch: str, now=time.time, runner_factory=None, services=None, balances=None) -> int`: acquire `paths.lock` with `O_CREAT|O_EXCL` (stale if older than 2 h → removed); on failure append `<ts> locked` to `paths.log`, return 0. Then `prev = load_prev`, `snap = take_snapshot(...)`, `save`, `decision = decide(...)` (`--analyst` forces mode analyst, `call=True` unless budget), log one line `<ts> <quiet|call mode reasons>`; budget-blocked → `[decision] budget reached: $X of $Y` note unless `notes.has_today("budget reached", now)`; `dry_run` prints snapshot + decision JSON and returns 0 without calling; else `run_turn`. Always releases the lock (try/finally). `telegram_setup` path: `--telegram-setup` runs `notify.telegram_setup(os.environ["TELEGRAM_BOT_TOKEN"], paths)` and prints the chat id only.

- [ ] **Step 1: Write the failing tests**

```python
async def test_second_instance_exits_locked(tmp_env): paths.lock.write_text(str(os.getpid())); assert await operate(cfg, svc, dry_run=True, repo=repo, branch="b") == 0 and "locked" in paths.log.read_text()
async def test_stale_lock_is_broken(tmp_env): paths.lock.write_text("1"); os.utime(paths.lock, (0, 0)); await operate(cfg, svc, dry_run=True, ...); assert "quiet" in paths.log.read_text() or "call" in paths.log.read_text()
async def test_quiet_round_calls_no_runner(tmp_env): calls=[]; await operate(cfg, svc, runner_factory=lambda *a, **k: calls.append(1), services=lambda: ALL_ACTIVE, balances=lambda w: (1.0, 0.0, []), ...); await operate(... same ...); assert calls == [] and paths.snapshot_prev.exists()
async def test_budget_note_once_per_day(tmp_env_with_spent_15): await operate(...); await operate(...); assert paths.notes.read_text().count("budget reached") == 1
def test_cli_has_operate_and_ops_allows_it(): assert "operate" in ops.ALLOWED; build_parser().parse_args(["operate", "--dry-run"])
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (`cli.py`: build `repo`/`branch` exactly as `_ops_serve` does). **Step 4: Run** full suite → PASS. **Step 5: Commit** `feat(operator): operate command with lock, dry-run and telegram setup`.

### Task 9: systemd units, installer, docs, autopilot

**Files:**
- Create: `deploy/systemd/snipetech-operator.service`, `deploy/systemd/snipetech-operator.timer`
- Modify: `deploy/setup.sh` (enable the timer in the final echo and in the `systemctl enable --now` list if present), `README.md` (one paragraph under "Run it 24/7": what the operator does, the caps, `operate --dry-run`, `--telegram-setup`), `docs/roadmap.md` (one line in the top section: operator live, where notes land), `deploy/bootstrap.sh` (no change unless it enumerates units).
- Test: `tests/test_ops.py` gains `test_units_list_includes_operator` (UNITS contains both new units) — the unit files are checked by a test that reads them and asserts `User=@USER@`, `EnvironmentFile=/etc/snipetech/env`, `OnCalendar=*:0/15`, `Persistent=true`, `RandomizedDelaySec=60`.

- [ ] **Step 1: Write the failing test** `tests/test_deploy_units.py::test_operator_units` (asserts above, plus `ExecStart` contains `operate` and nothing else).
- [ ] **Step 2: Run** → FAIL. **Step 3: Write** the units modelled on `snipetech-daily.service/.timer` (`Type=oneshot`, `Nice=10`, `TimeoutStartSec=30m`, `ExecStart=@REPO@/.venv/bin/snipetech --db @DATA@/snipetech.db operate --repo @REPO@`), update `setup.sh`, README, roadmap. **Step 4: Run** full suite → PASS. **Step 5: Commit** `feat(operator): systemd timer, installer, docs`.

### Task 10: Server acceptance (manual, after Tasks 1–9 are pushed)

- [ ] **Step 1:** `snipetech remote jobs` shows nothing running (wait otherwise; a deploy kills jobs).
- [ ] **Step 2:** `snipetech remote --wait update` → status `code` shows the new commit; `remote logs snipetech-ops` shows a clean start.
- [ ] **Step 3:** `remote --wait run operate --dry-run` → prints snapshot JSON with `equity`, `snipers` list, `spent_today: 0`, and a `Decision`.
- [ ] **Step 4:** `remote --wait run operate --telegram-setup` → prints a chat id; the owner confirms "snipetech operator connected" arrived on Telegram.
- [ ] **Step 5:** Owner-side: `[operator]` block in `/etc/snipetech/snipetech.toml` with `wallet = "6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at"` and `daily_usd = 1` (the cloud session gives the exact lines); `remote --wait run operate` → `operator/notes.md` has its first entry and `spend.jsonl` one line; `remote read operator/log.txt` shows the call line.
- [ ] **Step 6:** Set `daily_usd = 15`; on the server `sudo systemctl enable --now snipetech-operator.timer`; `systemctl list-timers` shows the next run.
- [ ] **Step 7:** Update the hourly autopilot prompt: read `operator/notes.md` and `operator/candidates.json`, relay new lines; stop doing the health round itself.
