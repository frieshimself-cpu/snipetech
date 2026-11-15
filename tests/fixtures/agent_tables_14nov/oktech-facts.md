# OKTech features (features analyst), 14 Nov 07:50 UTC

No rule has passed a forward reading (tester-rules 07:35). Last landed buy of ours 12 Nov 17:24; last fill 14 Nov 06:41 (the failed USDC sends; none since, SQL 07:46). NEW 07:50 (I171, closed): a FLPjyS copy on the copy exit alone, with no stop, is carried by five coins on the tape (row below); price moves only, not a copy's result. Open live proposal: I81 (guard first, then what a tip buys); waits for the owner; its tips 0.03 / 0.1 are above the fee policy's outer bound of 0.005. My queue: empty. Sig = first 8 characters. Tape rows are not a copy's result. Detail: journals 13-14 Nov.

| Feature | Verified? | Changes what, for which rule | Cheapest test |
|---|---|---|---|
| Non-coin guard (platforms, maxMcapUsd) | platforms FAILED once (14 Nov, 3NZqbqi4 + 49 sends on USDC one block behind FvHwFe's PumpSwap buy). maxMcapUsd: not tested, a skip still pays a failed send | only FvHwFe and its 3 companions need it (I163: 84 of 465 buys); 9 other candidates: 0 in 14 d | ask OKTech for a mint blocklist and why 50 retries (I81 stage 0) |
| Platforms vs LaunchLab, other programs | not verified | YJFQNK 8 of 39 buys on LaunchLab | question with I81 stage 0 |
| Failed sends (I93, I162) | 37 failed for 86 landed buys to 12 Nov, priority only; 14 Nov one storm of 50 = 0.048 SOL | 0.0002-0.0006 SOL a landed buy normally | OKTech's answer |
| Tip buys a place (I143) | NOT measured: 69 buys at tip 0.002 avg 0.5 behind; 7 at 0.02+0.02 0-15 behind; leaders differ | every plan that assumes a place (f98f at 1-2 SOL, copies at 2 SOL) | I81 stages 1-2 |
| Hunt landing (I146) | 14 buys 5 Nov: median 3 behind; 5 SOL+ trigger never live | f98f at 2 SOL: lower +0.027 at +3@1, -0.020 at +7@1 (tester) | I81 stage 1 |
| Timer | 300 s verified (6 sells within 2 s); 5-60 s never sent | f98f hold 5-10 s | I81 stage 1 |
| Take-profit, stages, part sales, trailing | NOT verified: none fired | no forward rule needs it yet | wait |
| Stop-loss | 2 sells, 1-2 slots late, filled -17 to -23 % on -15 % | FLPjyS, FvHwFe plans; on FLPjyS it may decide the result (next row) | first losing live copy |
| Copy exit 50 % | VERIFIED on the curve, 19 sells, his block or the next, median -2.3 %; sells the whole bag | all copies | re-read at 10+ new sells |
| FLPjyS copy on the copy exit alone (I171, tape n 137, no stop scored) | paper: entry at the end of its buy block, exit at the end of its first-sale block: mean +5.0 % before about 4.3 % costs; top-5 +11.5 of a +6.8 sum; other 132 sum -4.6; losses average about -50 %. 13 coins sold on PumpSwap left out | the rule "hold 300 s, copy exit, no stop" rests on five coins; whether a stop that fills near -20 % changes it is not measured | the tester's forward reading with its top-5 share |
| One buy a coin per wallet | verified 5 of 5; counts landed buys, not sends | first leg only | none |
| Copy landing | quiet 0-1 behind at +0.2 to +4.5 % (n 37); crowded 2-4 behind at +12 to +43 % (n 9) | FvHwFe is crowded: a copy starts about 12 % above | first landed fills |
| FvHwFe copy at our bid (I156, tape n 177) | paper: 10 coins are 67 % of the gain; the other 167 average +7.8 % before about 11-12 % costs | qualify's +0.0053 rests on a few coins | live fills |
| Exits after migration | NOT verified: migrationSell never sent; 0 of 66 sells on PumpSwap | FLPjyS's best 12 of 71 coins | I123 (code) |
| Dead exit 1 s | verified, 20 sells, 1.5-2.4 s | minor | none |
| Priority again on sells | verified 26 of 27 sells | bid goes in the tip | none |
| sellPriorityFeeSol 0.0001 (I90) | ignored on 47 of 47 failed sends to 12 Nov | 0.051 SOL on 27 sells | ask OKTech |
| totalSnipes cap | faulty: 4 buys on a cap of 3 (12 Nov) | a test needs a SOL stop | ask OKTech |
| preexec, preconf, computeUnits, smart fees | effect never measured | the block we land in | ask OKTech |
| Launch sniper, MIG, momentum hold, maxSol, minMcapUsd, cooldown, consensus | never run | no rule now | wait |

Candidates (tape, nothing live).

| Wallet | Finding | Idea |
|---|---|---|
| FvHwFe + 3 companions | crowded; few coins carry a copy; 18 % of buys are USDC on PumpSwap, which platforms lets through | I153, I156, I162 |
| FLPjyS + i3Sj7y | first sale 7 slots to 300 s on 90 % of coins; no non-coin buy; five coins carry a copy without a stop | I107-I120, I163, I171 |
| YJFQNK | 21 % of buys on LaunchLab; off-tape pairs not seen | I163 |
| A1eN4E, Byn68j, AtifzK | part-sellers or migration winners: need features not verified | journal |
| HjuSth | re-enters; one buy a coin gets the first leg only | I149 |
| 66hAs8 (copy bot, not a leader) | 0.25 SOL, bid 0.0012, 1 behind on 83 % of 751; one full sale at 90-94 slots or behind the leader; my tape count after fees +0.0045 a coin; five coins carry it (scout) | I167 rejected |
