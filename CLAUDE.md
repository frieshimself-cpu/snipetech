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
`remote update` and any restart of `snipetech-ops` kill running research jobs: check `remote jobs` first.
Secrets live only in `/etc/snipetech/env` on the server (Helius, OKTech, Anthropic, Telegram, TypeSafe keys). They
are never pasted into chat, commits, notes or logs; `ops.redact` scrubs outputs.

## Rules that do not bend

1. **No live trading without the owner's explicit yes in chat for that specific step** (wallet, size, duration).
   Never create or start an OKTech sniper, move SOL, or change wallets on your own. A stopped sniper strands its
   coin: never stop one that holds a position it bought.
2. **Measure at the real landing.** Live copies land 2–4 trades behind the leader at +18–43 % price (the "+6
   regime"); launch snipes land a slot after the create. Paper results at better landings are not evidence.
3. **One `lab` job at a time** on the server; it is the heavy one.
4. **Do not touch `/home/user/velo`** or any other repository the owner has not pointed you at.
5. Commits end with the attribution trailers the session is given; no model names in repo artifacts.
6. Tests first (the Superpowers workflow is enabled on the owner's account): a failing test, then the code.

## Working with the owner

Non-technical, decisive, wants plain words and the numbers that change a decision. Lead with the outcome; one
idea per sentence; no jargon without a gloss. Ask only when the answer changes what you build. Every milestone and
every live result goes into `docs/` and is pushed, so the next session starts from the record, not from memory.
