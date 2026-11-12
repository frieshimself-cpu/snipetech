# snipetech — instructions for any Claude session in this repo

## What this is

The research brain for a pump.fun sniper: it records every trade of every token (the "tape"), replays strategies
and wallets at the landing the owner's bot actually gets, and decides what to snipe and who to copy. Execution is
done by OKTech's sniper (remote service, `src/snipetech/oktech.py`); this repo never signs a trade itself.
The owner's goal is a bot that makes money without them watching it. The honest state of that goal is always the
top section of `docs/roadmap.md`; the test log is `docs/live-recipe.md`. Read both before proposing anything.

## Where things are

- `docs/index.md` — map of every document. `docs/inbox/` — the owner's raw notes and screenshots: read them, fold
  them into the real docs, never edit or delete them.
- `src/snipetech/` — Python 3.11+ package; `cli.py` is the entry point (`snipetech <command>`); `service.py` ties the
  store, RPC, analysis and OKTech client together; `operator/` is the unattended 15-minute agent on the server.
- `deploy/` — server install (`bootstrap.sh`, `setup.sh`, systemd units, `ops_authorized_keys`).
- `tests/` — pytest, real mainnet fixtures plus synthetic markets. `.venv/bin/pytest -q` must be green before a push.

## The server

One Ubuntu box runs the tape recorder, a daily research job, the operator timer and `snipetech-ops`, a signed
remote-operations endpoint. From a checkout with an authorized key:
`snipetech remote status | jobs | --wait run <command> | read jobs/<id>.log | sql "<select>" | update`.
`remote update` restarts `snipetech-ops` and the tape recorder. Since 7 Nov 20:49 (`KillMode=process`, installed by the
owner's `deploy/setup.sh` run) a job started with `remote run` survives it like the bot's own jobs, but keeps the old code
until it ends; before that it died with it (7 Nov 07:44: a `research search` was lost). Check `remote jobs` first and
deploy between labs, so no lab reports numbers from old code. The slice has no CPU quota since 20:49 (all 6 cores, 12 GB).
Secrets live only in `/etc/snipetech/env` on the server (Helius, OKTech, Anthropic, Telegram, TypeSafe keys). They
are never pasted into chat, commits, notes or logs; `ops.redact` scrubs outputs.

## Rules that do not bend

1. **No live trading without the owner's explicit yes in chat for that specific step** (wallet, size, duration).
   Never create or start an OKTech sniper, move SOL, or change wallets on your own. A stopped sniper strands its
   coin: never stop one that holds a position it bought.
2. **Measure at the real landing.** Live copies land in the leader's own block (20 of 24 since 7 Nov); the place
   inside it is a fee auction. At a 0.003 SOL bid: 0.3 trades behind at +1 % behind a quiet leader, 2–4 trades behind
   at +18–43 % behind a crowded one (the "+6 regime"). Launch snipes land a slot after the create. A paper result
   names its landing and the bid that buys it; one at a better landing than its bid buys is not evidence. No copy
   lands between one operator's own wallets: buys by a copied wallet's companions are never "buyers ahead of us".
3. **At most two `lab` jobs at a time** on the 32 GB server (one on a machine under 24 GB); the lab lock
   (`analysis/lab_lock.py`) enforces it, and `qualify` takes a slot too.
4. **Do not touch `/home/user/velo`** or any other repository the owner has not pointed you at.
5. Commits end with the attribution trailers the session is given; no model names in repo artifacts.
6. Tests first (the Superpowers workflow is enabled on the owner's account): a failing test, then the code.

## Working with the owner

Non-technical, decisive, wants plain words and the numbers that change a decision. Lead with the outcome; one
idea per sentence; no jargon without a gloss. Ask only when the answer changes what you build. Every milestone and
every live result goes into `docs/` and is pushed, so the next session starts from the record, not from memory.

**Who does what (owner, 12 Nov 2026).** A session in this repo improves the system: code, bugs, speed, tools, the
agents' instructions, the record. The system itself does the trading and the analysing, on the owner's API credits.
A research question goes to the server's agents as an idea (`snipetech remote --wait run operate idea ...`), with the
missing tool built here. A session starts a job on the server only to test or measure its own change, to run a repair
it built, or to read state.

## Local-session automations (set these up in `.claude/settings.json` on the owner's PC; a cloud session cannot)

1. Permission allowlist for read-only server commands and tests: `Bash(.venv/bin/snipetech remote status*)`,
   `Bash(.venv/bin/snipetech remote jobs*)`, `Bash(.venv/bin/snipetech remote read*)`,
   `Bash(.venv/bin/snipetech remote sql*)`, `Bash(.venv/bin/pytest*)`. Never allowlist `remote update`,
   `remote kill`, `remote run oktech*` or anything that trades.
2. PreToolUse hook on Edit/Write that refuses paths matching `/etc/snipetech/env`, `.env`, `ops_key`, `*.key`.
3. PostToolUse hook on Edit/Write of `src/**/*.py`: run `.venv/bin/pytest -q -x` and surface failures.
4. PostToolUse hook on Edit/Write of `src/**/*.py` and `tests/**/*.py`: `ruff format <file>` then `ruff check --fix <file>`
   (Ruff is configured in pyproject.toml; CI runs the tests).
5. Skill `.claude/skills/server-ops/SKILL.md` (`user-invocable: false`): the `snipetech remote` command set, "never
   `update` while a job runs", how to read a job's JSON output, secrets only in /etc/snipetech/env.
6. Skill `.claude/skills/live-step/SKILL.md` (`disable-model-invocation: true`): the checklist for any step that spends
   SOL — wallet, size, cap, duration, stop rule, the owner's explicit yes in chat, and the docs/live-recipe.md entry.
7. Subagent `.claude/agents/trade-safety-reviewer.md`: reviews diffs touching oktech.py, operator/, ops.py, deploy/ for
   anything that could start a sniper, move SOL, widen the job allowlist or print a secret.
