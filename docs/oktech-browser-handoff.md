# OKTech browser handoff (for a Claude Code session running on the owner's PC with Claude in Chrome)

You are helping a cloud session that cannot see the browser. Use Claude in Chrome on
oktech.me (the user is already signed in). Do not change any sniper settings, do
not start or stop anything, do not spend. Read only. Write everything you find into
`docs/oktech-browser-findings.md` in this repo, commit on branch
`claude/stoic-babbage-5e6pck` and push. Do not touch the `velo` folder or repo.

## Context (5 Nov 2026, evening UTC)

Two hunt-mode snipers named "snipetech strategy: buy >= 2 SOL by a non-dev …" were
started on the SNIPER wallet (6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at):
A = id 6aecf1499d2356e2dc3c1134 (Pre-exec + Relay), B = id 6aecf2e09d2356e2dc3c57c3
(Relay only). Each fired exactly once right after start, then never again although
dozens of launches met the threshold. We need OKTech's own view of why.

## What to collect, in this order

1. **Sniper page → each of the two tasks.** Expand the card. Copy verbatim: status,
   any counters (snipes/attempts/buys x of 5, "hunted", "skipped", "signals"), any
   last-event text, timestamps, and the full config as shown. If there is a
   "View transaction", "log", "history", "events" or "signals" link or tab anywhere
   on the card or in a drawer, open it and copy all of it.
2. **Tasks page** (top nav). If our two snipers or their events appear there, copy
   the entries. Look for per-task logs, "activity", or "runs".
3. **Settings → Notifications.** Note whether a Discord webhook or Telegram chat is
   configured (do not paste secrets; just "set" / "not set").
4. **Sniper page → Create sniper → mode row.** List every mode button visible
   (Normal, [S] Shapes/Web/Hunt, [A], AD, Loadout, CTBot, ClaimSniper, Reverse,
   MIG, CopyDev, Stocks, Gucci). Open the [S] tab → Hunt and copy every field label
   and help tooltip text, especially anything about re-arming, "one at a time",
   cooldown, "singleSnipe", "total snipes", or what happens after a buy. Cancel
   without saving.
5. **Create sniper → Advanced.** Copy the help text of "Local pre-execution",
   "Preconfs", "Account filter", "Cooldown between launches", "Single snipe" /
   "Total snipes" and anything mentioning attempts or failed buys counting.
   Cancel without saving.
6. **Updates page.** Copy the titles and dates of the last 10 updates, and the full
   text of any that mention Hunt, [S], pre-execution, preconfs, or the sniper
   counters.
7. **Account badge** (top right): level shown, and anything under the gear icon
   that lists granted features.

## Output

Write `docs/oktech-browser-findings.md` with one section per item above, verbatim
text in fenced blocks, and a short "what I could not find" list. Commit with the
message `docs: OKTech browser findings (hunt stall)` and push to
`claude/stoic-babbage-5e6pck`. Then tell the user it is pushed.
