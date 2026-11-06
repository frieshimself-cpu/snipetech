# snipetech documents — where to look

| document | what it answers | who writes it |
|---|---|---|
| `roadmap.md` | Where we stand and what is next; the top section is always current | Claude, after every finding |
| `live-recipe.md` | Every live test with numbers: what ran, what filled, what it cost, the verdict | Claude, the same day |
| `oktech-features.md` | Every OKTech feature, gated or not, what it does and how it is built | Claude, from the client bundle |
| `oktech-settings.md` | What each sniper setting means and the values we use | Claude |
| `oktech-browser-handoff.md` | Brief for a local Claude session that can see oktech.me in Chrome | Claude |
| `birdeye.md` | What the Birdeye plan gives us, CU costs, where it plugs in | Claude |
| `superpowers/specs/` | Designs approved before building (operator agent) | Claude, owner approves |
| `superpowers/plans/` | Task-by-task implementation plans with their tests | Claude |
| `inbox/` | The owner's raw notes and screenshots, read-only for Claude | Owner |
| `../README.md` | How to install and run everything, server included | Claude |
| `../CLAUDE.md` | Rules and map for any Claude session in this repo | Claude |

Live state that is not in a document: the server's `operator/notes.md` (what the operator saw each round) and
`operator/candidates.json` (wallets that cleared the three gates), both readable with `snipetech remote read`.
