# Round 9 (8 Nov 2026, 23:06-23:30 UTC): more wallets like 7aPo, from their early buys

Built `snipetech early-leaders` (87be217), a job the server's agents may start: each wallet's first buy of 0.3+ SOL
per coin made 1-20 slots after the create, entered at the 3rd trade after it, marked 30 s after the leader's buy, net
of 0.009 SOL a round trip (coins whose create slot bought 85+ SOL skipped).

72 h of tape, 50 wallets with 20+ early buys; 8 with a positive lower bound:

| wallet | early buys | mean / 0.2 SOL copy | lower bound | qualify at the live landing (real exits) |
|---|---|---|---|---|
| AG89fnx9 | 26 | +0.069 | +0.019 | qualified (already copied, 75 SOL cap) |
| 3nDtLwqR | 22 | +0.049 | +0.012 | retired: trades mostly off pump.fun (38 %) |
| J6QzD7E2 | 41 | +0.049 | +0.009 | retired: off pump.fun; -0.009 at +6 (n 21) |
| 88887QrR | 20 | +0.047 | +0.008 | no tape data in qualify's window |
| 6SHf2w54 | 37 | +0.026 | +0.005 | retired: bundle member in 8 launches |
| QuANTFun | 52 | +0.023 | +0.005 | retired: bundle member in 10 launches; -0.003 at +6 |
| C29JBEEt | 27 | +0.034 | +0.004 | retired: -0.006 at +6 with the lab's exits (n 35) |
| Fhbh1DTU | 158 | +0.012 | +0.001 | retired: bundle member in 31 launches |

Correction (9 Nov 00:45): the qualify column above is each wallet's latest verdict from 6-7 Nov, not a new test. The
`--label` flag only labelled wallets; qualify's queue is the candidates table, so none of them was re-scored on 8 Nov.
Fixed in the next commit (both screens now add their wallets to the candidates table, never evaluated first in the
queue). The reasons above (bundle member, venue share, losing at +6) are real verdicts, only older.

Reading: the screen finds AG89 first, so it agrees with the analyst's hand count, but its fixed 30 s mark is kinder
than the exits a live copy runs, and three of its eight are launch-bundle wallets (they buy 1-3 slots after the
create as part of the launch, where no copy lands). Nothing new is armed. Next for the agents (I22, testing): skip
bundle members and wallets mostly off pump.fun inside the screen, mark with the lab's exits instead of a fixed 30 s,
rerun daily, and label only what survives for qualify.
