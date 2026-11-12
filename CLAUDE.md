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
- `tests/` — pytest, real mainnet fixtures plus synthetic markets. `.venv/bin/pytest -q -n 4` (four cores: about a
  minute; `-q` alone: three) must be green before a push.

## The server

One Ubuntu box runs the tape recorder, a daily research job, the operator timer and `snipetech-ops`, a signed
remote-operations endpoint. From a checkout with an authorized key:
`snipetech remote status | jobs | --wait run <command> | read jobs/<id>.log | sql "<select>" | logs <unit> | update`.
`remote logs kernel --boot -1` and `remote logs systemd --boot -1` are the machine's own last lines before its last
restart (out of memory, what was starting); a unit's name with `--boot -1` gives that unit's.
`remote update` restarts `snipetech-ops` and the tape recorder. Since 7 Nov 20:49 (`KillMode=process`, installed by the
owner's `deploy/setup.sh` run) a job started with `remote run` survives it like the bot's own jobs, but keeps the old code
until it ends; before that it died with it (7 Nov 07:44: a `research search` was lost). Deploy between labs, so no
lab reports numbers from old code: the server keeps that rule itself once it runs the code of 12 Nov (`ops.holds_deploy`,
`ops.HOLD_UNITS`). `remote update` is refused while a job that reports numbers or a timer-run unit (the agents' round,
the book run, qualify) is mid-run, and names it; `remote status` lists them under `deploy_waits_for`;
`remote update --when-quiet` waits for the moment, `--force` deploys all the same. The slice has no CPU quota since
20:49 (all 6 cores, 12 GB).
Secrets live only in `/etc/snipetech/env` on the server (Helius, OKTech, Anthropic, Telegram, TypeSafe keys). They
are never pasted into chat, commits, notes or logs; `ops.redact` scrubs outputs.

## Rules that do not bend

1. **No live trading without the owner's explicit yes in chat for that specific step** (wallet, size, duration).
   Never create or start an OKTech sniper, move SOL, or change wallets on your own. A stopped sniper strands its
   coin: never stop one that holds a position it bought.
   **Standing yes for small live tests (owner, 12 Nov 2026, "Yes to everything" to the offer "live tests are fine
   without asking, up to 0.2 SOL a buy and 2 SOL lost a day"):** a live TEST inside both numbers, from the sniper
   wallet, needs no further yes; each one is written into docs/live-recipe.md before it starts (what it tests, the
   sniper, the stop rule) and after (every fill). Anything larger, anything that is not a test (a rule run for
   profit, a bigger size, another wallet), and RISK / AUTOTEST still need the owner's yes for that step.
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

**The owner's other answers of 12 Nov 2026 ("Yes to everything"):** parallel helpers (sub-agents, workflows) may be
used again in a session (the rule of 8 Nov against them is lifted); the server's agents may spend the remaining
PumpFunData history credits; the agents' budget is 500 $ a day with an analysis every operator round; size and bid
have no cap in what is tested or proposed; the copy snipers that no longer pass the rules (7aPo3n, AG89fn) are to be
stopped once they hold nothing.

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
