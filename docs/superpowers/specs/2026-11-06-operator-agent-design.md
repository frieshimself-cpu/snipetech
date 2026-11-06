# Operator agent — design (6 Nov 2026)

## Purpose

A Claude that runs **on the server** around the clock so progress no longer depends on a chat session being
awake. It watches health, reads finished research, starts the next research step, keeps a running list of
findings, and tells the owner only what changes a decision. It **never trades, never starts a sniper, never
spends SOL**: every live step still needs the owner's yes in chat.

Success: the owner wakes to a short list of what changed overnight and a wallet shortlist when one clears the
three gates; the cloud session spends its time on decisions, not polling. Spend is capped by the agent itself.

What the owner said (6 Nov): keep reports "just here" (the chat) → the agent writes a notes file the cloud
session relays hourly; a Telegram bot for the few things that need a decision (added at the owner's offer);
40 $/day approved, 100 $ of credit loaded; start the cap at 15 $/day.

## Approach

**B — deterministic snapshot every 15 minutes, Claude only when something changed.** A script gathers the
facts for free; Claude is called only on change or for the daily analysis. Rejected: A (Claude every round:
pays for idle rounds, least predictable) and C (no Claude on the server: nobody reasons about results between
the cloud session's rounds, which is the problem being solved).

## Components

All new code lives in `src/snipetech/operator/`; nothing in `agent/` (the interactive analyst) changes.

### 1. Snapshot (`operator/snapshot.py`)

`take_snapshot(svc, store, paths) -> Snapshot` collects, without any LLM call:

- services + disk + tape lag (reuse `Service.status()`);
- jobs: running ids, and ids that **ended since the previous snapshot** with exit code and log path
  (read `/var/lib/snipetech/jobs/*.json` metadata; no ops HTTP call);
- sniper wallet: native SOL + wrapped-SOL account + held token accounts above dust (RPC; the wallet address
  from config `operator.wallet`);
- OKTech: each sniper's `status` (reuse `Service.oktech_status()`); `running` count and any `held` coin;
- fills by the wallet since the previous snapshot (tape `trades` rows for `user = wallet`);
- paper forward test summary (`paper report` numbers) — read-only;
- today's spend from the ledger (§5);
- `now`, and `previous` = the last snapshot's digest fields.

Saved as JSON to `<data>/operator/snapshot.json` (previous copy kept as `snapshot.prev.json`). The dataclass
is plain data so tests can build one by hand.

### 2. Gate (`operator/gate.py`)

`decide(snapshot, previous, now, cfg) -> Decision(call: bool, reasons: list[str], mode: "operator"|"analyst")`.
Calls Claude when any of:

- a job ended since the previous snapshot;
- an OKTech sniper is `running`, or a coin is held, when the expected state is "all idle, nothing held"
  (expected state is a file the owner/cloud session sets: `<data>/operator/expected.json`, default idle);
- equity moved by more than `operator.equity_alert_sol` (default 0.1) since the previous snapshot;
- the tape's last trade is older than `operator.tape_stale_secs` (default 300);
- a service is not active;
- the daily **analyst** run is due: after `operator.analyst_after_utc` (default 06:00) and not yet done today
  (`<data>/operator/analyst-<date>.md` absent) → mode `analyst`;
- the owner left a request in `<data>/operator/inbox.md` (the cloud session writes it through `remote put`).

Otherwise `call=False` → one line appended to `operator/log.txt`: `<ts> quiet`. No API call, no cost.

### 3. Operator turn (`operator/turn.py`, prompts in `operator/prompts.py`)

One `tool_runner` conversation (same client pattern as `agent/runner.py`) with the snapshot, the gate's reasons
and the standing rules as the user message. Tools (and only these):

| tool | what it may do |
|---|---|
| `read_job_log(id, tail=200)` | read `<data>/jobs/<id>.log` |
| `read_file(path)` | read under `<data>/reports`, `<data>/operator`, `<data>/jobs` only |
| `sql(query)` | read-only SELECT, same authorizer as ops (`_ALLOWED_ACTIONS`), 30 s cap, 200 rows |
| `start_job(argv)` | run a snipetech command **from a fixed list**: `lab`, `follow`, `discover-wallets`, `label`, `copy-rank`, `paper`, `study`, `copy`; refused otherwise; **one `lab` at a time** and at most `operator.max_jobs` (3) running — enforced in code, not prompt |
| `write_note(text, level)` | append to `<data>/operator/notes.md` as `- <ts> [level] text`; `level in {info, decision}` |
| `set_candidates(json)` | write `<data>/operator/candidates.json` (wallets that cleared gates, with the numbers) |
| `write_report(name, text)` | write `<data>/operator/<name>.md` (analyst output); `name` is a plain file name, no path |

No tool can reach OKTech writes, wallets, keys, the env file or the ops service. `oktech` is **not** in the
start_job list. The prompt states the budget, the "never trade" rule, the one-lab rule and the three gates
(positive on 30-day history AND tape lab at +1 AND at +6, n ≥ 20, small price gap to the second buyer).

Every turn ends with at least one `write_note` (what it saw, what it started). Token usage is written to the
ledger after each API response.

### 4. Analyst turn

Same machinery, mode `analyst`, a longer prompt and `max_tokens`: read the day's lab/follow/discover/copy-rank
outputs (paths listed in the snapshot), apply the three gates, write `operator/analyst-<date>.md` through
`write_note`-style `write_report(name, text)` (restricted to `operator/`), update `candidates.json`. A wallet
that clears all three gates is a `decision` note (→ Telegram).

### 5. Budget ledger (`operator/budget.py`)

`<data>/operator/spend.jsonl`: one line per API response `{ts, mode, in_tokens, out_tokens, cache_read,
cache_write, usd}`. Prices per million tokens come from config `operator.prices` (defaults for the configured
model; the owner can change them without code). `spent_today(now)` sums UTC-day lines. The gate refuses to
call when `spent_today ≥ operator.daily_usd` (default **15**) and writes one `[decision] budget reached` note
per day. A single turn is also capped by `operator.turn_usd` (default 2): the runner stops after the response
that crosses it.

### 6. Notes and Telegram (`operator/notify.py`)

- `notes.md` is the record; the cloud session relays new lines on its hourly round (`remote read
  operator/notes.md`).
- `[decision]` notes are also sent to Telegram when `TELEGRAM_BOT_TOKEN` is set: a wallet passing the gates,
  a sniper running or coin held when none should be, equity moved > 0.1 SOL, tape stalled, service down,
  budget reached. Plain `sendMessage`, no markdown, 4 000-char cap, failures logged and ignored.
- `snipetech operate --telegram-setup`: calls `getUpdates`, stores the first private chat id in
  `<data>/operator/telegram.json`, sends "snipetech operator connected". The owner must have messaged the
  bot once (done 6 Nov).

### 7. Running it

- `snipetech operate [--analyst] [--dry-run] [--telegram-setup]`; `operate` added to the ops `ALLOWED` list so
  the cloud session can trigger a round.
- systemd: `snipetech-operator.service` (oneshot, `User=snipetech`, `EnvironmentFile=/etc/snipetech/env`,
  `Slice=snipetech.slice`) + `snipetech-operator.timer` (`OnCalendar=*:0/15`, `Persistent=true`,
  `RandomizedDelaySec=60`); the analyst is the same unit run by the gate's "due" rule, no second timer.
- `setup.sh` installs/enables the units; deploy = `remote update` **only when no job runs**.
- Config block `[operator]` in `snipetech.toml`: `wallet`, `daily_usd=15`, `turn_usd=2`, `max_jobs=3`,
  `equity_alert_sol=0.1`, `tape_stale_secs=300`, `analyst_after_utc="06:00"`, `model="claude-sonnet-5-5"` (operator turns: cheap, frequent),
  `analyst_model="claude-opus-5-5"` (one deeper turn a day), `prices` (per-million-token rates for both).

## Data flow

timer → `operate` → snapshot (RPC + sqlite + files) → gate → [quiet | Claude turn with tools] → notes.md /
candidates.json / jobs started / spend.jsonl → (Telegram for `decision`) → cloud session reads notes hourly
and relays here; owner's requests go the other way through `inbox.md`.

## Error handling

- RPC or OKTech API down: snapshot marks the section `unavailable`; the gate treats a missing OKTech section as
  a reason to call (it cannot confirm "all idle"); Telegram `decision`.
- Anthropic API error / credits exhausted: logged, one `[decision]` note per day, round exits 0; nothing else
  is affected.
- `start_job` failures return the error text to the model; the one-lab rule returns "a lab is running".
- A turn that exceeds `turn_usd`, `max_tokens` or 20 tool calls is cut off with a note.
- Timer overlap: a lock file `<data>/operator/lock`; a second instance exits immediately.

## Testing

Unit tests with the Anthropic client mocked (a fake runner yielding scripted messages):

- gate: each trigger condition and the quiet path; budget-reached refusal; analyst-due once per day;
- budget: per-line cost arithmetic from token counts, `spent_today` across UTC boundary, turn cap;
- tools: `start_job` refuses `oktech`, refuses a second `lab`, refuses unknown commands; `read_file` refuses
  paths outside the allowed roots; `sql` refuses writes; `write_note` format;
- notify: `decision` goes to the Telegram sender, `info` does not; missing token → no send, no error;
- snapshot: built from a temp store with a few trades and job metadata files; `ended_since_previous` logic;
- lock: second instance exits.

Acceptance on the server: deploy when no job runs; `operate --dry-run` prints the snapshot and the gate
decision; `operate --telegram-setup` sends the hello message; one real round with `daily_usd=1`; `notes.md`
has the first entry; then `systemctl enable --now snipetech-operator.timer`. The cloud session's hourly
prompt gains "read `operator/notes.md` and relay new lines".

## Out of scope

Anything that places, changes or stops a trade; editing `docs/` from the server (the cloud session folds
`candidates.json` into the roadmap through git); a web UI; more than one wallet.
