# snipetech documents — where to look

| document | what it answers | who writes it |
|---|---|---|
| `roadmap.md` | Where we stand and what is next; the top section is always current | Claude, after every finding |
| `live-recipe.md` | Every live test with numbers: what ran, what filled, what it cost, the verdict | Claude, the same day |
| `autopilot.md` | What the cloud session's hourly round does: the owner's standing rules, the current state (what is deployed, what was told to the owner, what is checked), the open work in order. The scheduled task only points here | Claude, whenever the state changes |
| `audit-2026-11-11.md` | The whole-system audit of 11 Nov: every defect found with its evidence, the fix and how it was checked, what was found right, what is still open | Claude, 11 Nov |
| `oktech-audit-2026-11-06.md` | The 6 Nov audit of our OKTech integration: what was wrong on our side (fixed), what is known about OKTech, the questions for its owner | Claude, 6 Nov |
| `oktech-features.md` | Every OKTech feature, gated or not, what it does and how it is built | Claude, from the client bundle |
| `oktech-settings.md` | What each sniper setting means and the values we use | Claude |
| `oktech-browser-handoff.md` | Brief for a local Claude session that can see oktech.me in Chrome | Claude |
| `architecture.md` | The whole loop as one diagram (Obsidian renders it); the database's rules for writers and readers; how the lab scores a coin that leaves its curve while held; how our own exit (/sell) sells on the curve and on PumpSwap; how the post-mortem breaks each live trip into price, fees and duplicates; why OKTech sells twice and the fee policy per task (8 Nov) | Claude |
| `research/` | The cloud session's strategy research rounds: one report per round (outcome, labs, builds, lessons, next angles) and their index; the server's agents read the newest round at every turn | Claude, after every round |
| `birdeye.md` | What the Birdeye plan gives us, CU costs, where it plugs in | Claude |
| `research/2026-11-12-gmgn-api.md` | GMGN's query API: how a call is made (key in a header, no signature on a read), every read with its weight and fields, the limits, what `snipetech gmgn` and the `gmgn` discover source do, what is not verified until the first real call | Claude, 12 Nov |
| `jev.md` | TypeSafe's Jev: what it is, why it is not used yet, the measured-first plan | Claude |
| `superpowers/specs/` | Designs approved before building (operator agent). `2026-11-12-specialised-agents-design.md`: the analyst split into a wallet scout, a strategy tester and a fee and place analyst; a design, not built | Claude, owner approves |
| `superpowers/plans/` | Task-by-task implementation plans with their tests | Claude |
| `inbox/` | The owner's raw notes and screenshots, read-only for Claude | Owner |
| `../README.md` | How to install and run everything, server included | Claude |
| `../CLAUDE.md` | Rules and map for any Claude session in this repo | Claude |

Live state that is not in a document: the server's `operator/notes.md` (what the operator saw each round) and
`operator/candidates.json` (wallets that cleared the three gates), both readable with `snipetech remote read`; the
live dashboard at `https://<host>/dash` (password `DASH_TOKEN`) shows the same plus services, jobs and resources; it answers 404 until `DASH_TOKEN` is set in /etc/snipetech/env (9 Nov: not set).
- `tools/dashboard_snapshot.py` — writes the hourly snapshot behind the owner's dashboard, Snipetech Control Room (https://claude.ai/artifact/HbtWazUA7gNP9phU8fddNZ): status, KOL paper tests, live round trips, copied wallets, the agents' log and ideas, API spend.
