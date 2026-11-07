# Strategy research rounds

The record of the continuous strategy research the cloud session runs (7 Nov 2026 on): each round reads every
earlier round's report here, chooses angles nobody has covered, screens them with read-only SQL on the server, runs
labs one at a time at the landing our live buys get, puts every positive result to three skeptics, and writes its
report here as `<date>-round-<N>.md`. Survivors go into the forward paper test (`paper freeze --trigger ... --only-live`),
which feeds the owner's automatic live test. `research-loop.workflow.js` is the round itself (a Claude Code workflow
script, run from the cloud session with `args: {round: N}`); it only reads the repo and the server and runs labs.

Round 1 is the edge hunt of 7 Nov 12:20 UTC (seven fixed angles); round 2 on choose their own.

**Lab numbers from round 1 and from later labs are not comparable.** Round 1's labs charged the lab's flat 0.0035 SOL
a transaction (0.007 a round trip, against about 0.009 live) and scored launch-type exits as if OKTech watched a coin
from the moment of the buy (it starts about 1.3 s later), so its numbers are 0.002 to 0.006 SOL a trade too kind. Since
7 Nov evening (on the server once deployed), `snipetech lab` charges the measured cost of a transaction unless
`--per-tx` says otherwise and says which it charged (`per_tx_sol`, `per_tx_source`: measured, flat or flag), and every
exit of a launch, hunt, whale or burst rule waits OKTech's 1.3 s watch delay (`watch1.3s` in the exit's name). The round
script runs its labs without `--per-tx`, and holds a result charged less than live (the flat cost, while the low bid's
cost is not measured) to live cost, 0.003 SOL a trade, before it counts as positive.
