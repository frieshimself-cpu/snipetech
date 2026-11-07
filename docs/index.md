# snipetech documents — where to look

| document | what it answers | who writes it |
|---|---|---|
| `roadmap.md` | Where we stand and what is next; the top section is always current | Claude, after every finding |
| `live-recipe.md` | Every live test with numbers: what ran, what filled, what it cost, the verdict | Claude, the same day |
| `oktech-audit-2026-11-06.md` | The 6 Nov audit of our OKTech integration: what was wrong on our side (fixed), what is known about OKTech, the questions for its owner | Claude, 6 Nov |
| `oktech-features.md` | Every OKTech feature, gated or not, what it does and how it is built | Claude, from the client bundle |
| `oktech-settings.md` | What each sniper setting means and the values we use | Claude |
| `oktech-browser-handoff.md` | Brief for a local Claude session that can see oktech.me in Chrome | Claude |
| `architecture.md` | The whole loop as one diagram (Obsidian renders it); the database's rule for writers; how our own exit (/sell) sells on the curve and on PumpSwap | Claude |
| `birdeye.md` | What the Birdeye plan gives us, CU costs, where it plugs in | Claude |
| `jev.md` | TypeSafe's Jev: what it is, why it is not used yet, the measured-first plan | Claude |
| `superpowers/specs/` | Designs approved before building (operator agent) | Claude, owner approves |
| `superpowers/plans/` | Task-by-task implementation plans with their tests | Claude |
| `inbox/` | The owner's raw notes and screenshots, read-only for Claude | Owner |
| `../README.md` | How to install and run everything, server included | Claude |
| `../CLAUDE.md` | Rules and map for any Claude session in this repo | Claude |

Live state that is not in a document: the server's `operator/notes.md` (what the operator saw each round) and
`operator/candidates.json` (wallets that cleared the three gates), both readable with `snipetech remote read`; the
live dashboard at `https://<host>/dash` (password `DASH_TOKEN`) shows the same plus services, jobs and resources.
