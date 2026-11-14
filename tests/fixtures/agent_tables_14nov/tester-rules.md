# Paper rules (strategy tester), 14 Nov 07:50 UTC

Forward paper records only; none has passed a reading (total, lower and capped bound above zero AND top-5 share 0.5 or less). Counts: paper report ace0b6 (07:16) and my SQL 07:48, pump.fun only. Lab numbers are tickets, not evidence. NEW 07:50: I168: the FvHwFe copy at +6 assumed with 0.036 charged has a lab ticket (fees lab 9e95e1) and is being frozen (job 306279, id not out yet). First forward trades on FLPjyS (one coin, a loss on each size) and one more Schoen loss. NOT REPEATABLE (I161, code): a hunt ticket is good to about 0.015 a trade.

| Id | Trigger; exit | Landing, bid | Size | Fwd trades | Total SOL | Lower / capped | Top-5 | Reading | State |
|---|---|---|---|---|---|---|---|---|---|
| f98f8abd6a | hunt 5 SOL+ in 1 s, <=2 wallets, dev <=1; hold 10 s | +3@1 (live place never measured: I81), none | 0.2 | 571 (210 wins) | +7.31 | +0.0084 / +0.0063 | 0.53 | fails on top-5 (halves 0.70, 0.85; without five: +1.22 / 319 and +0.47 / 242) | I68. Not an edge yet: 1-2 % of a buy without five trades. Next reading: the 250 trades after 14 Nov 07:15. |
| 389fba5a59 | same, hold 60 s | +3@1 | 0.2 | 552 | +1.06 | -0.0055 / -0.016 | 6.4 | fails | the rule depends on its exit |
| 97aedceb34 | same; hold 5 s | +3@1, none | 1 | 4 (1 win) | -0.20 | - | - | none | I140. Tickets lower +0.012 / +0.027 (432); crowd needed 0.75-0.83 (hope). "At 1 SOL our own buy moves the price 4.0 % at the median (5.6 % at the 90th percentile) and is 1.9 % of the pool's coins; none of 1082 moments was left out for size; 49 of the 1082 scored rest on a price outside the model." |
| e26122778c | same; hold 5 s | +3@1, none | 2 | 2 (1 win) | -0.20 | - | - | none | I140. Ticket lower +0.027; same-sol +0.006; crowd needed 0.82-0.88. "At 2 SOL our own buy moves the price 8.0 % at the median (11 % at the 90th percentile) and is 3.8 % of the pool's coins; 2 of 1078 moments were left out for size (2 because it was more than half of the SOL the curve still takes); 54 of the 1076 scored rest on a price outside the model." |
| 76d0772177 | same; hold 5 s | +5@1 ASKED, not measured | 2 | 1 (1 win) | +0.11 | - | - | none | I140. Never a live candidate by itself. |
| 1a97dd9bfb | f98f with 1 wallet only | +3@1, none | 1 | 0 | - | - | - | none | I68. Thin ticket (17). |
| 5a9f9b988b | hunt 15 SOL+ in 1 s | +3@1 | 0.2 | 11 (2 wins) | -0.20 | - | - | none | judge at 30 |
| (no record) | f98f variants and sizes 0.5 / 5 SOL | - | - | - | - | - | - | - | Not frozen: no ticket stands without outside-model trades (c56cb7, f6e0e2, 30829e). |
| pending (freeze 306279) | FvHwFe first buy 0.3 SOL+, no gate; hold300s MIGSELL copy50% watch1.3s | +6 ASSUMED, 0.036 charged (fees-bids: lab places for this wallet are kinder than real, it counts 1 of 3 own wallets) | 0.5 | 0 | - | - | - | none | I168. Ticket 9e95e1: 76 newer trades of 188, +0.100, lower +0.044, 29 % win; one outside-model trade is 2.62 of 7.63 SOL, without it +0.067; own round trip -0.127, crowd needed 0.56; top-5 NOT checked. Band not confirmed (20-50 SOL prior 60 s carries 10.8 of 13.3 in-sample). At +10 with 0.005 (d4fdf1): lower +0.064. Paper only: no non-coin guard (oktech-facts). Judge at 30. |
| 39313b76ce | Schoen first buy 1 SOL+, mcap <=300; copy50% | +6 ASSUMED, 0.02 | 2 | 15 (4 wins) | -1.99 | - | one trade +2.22 | none (15 of 30) | I79. Going the wrong way. |
| 99aba41c50 | Schoen, <=50 SOL prior 60 s | +6 ASSUMED, 0.02 | 2 | 3 (0 wins) | -1.09 | - | - | none | I79. |
| 9e8c54c5f0 | KOL first buy 1 SOL+, <=5 SOL prior 60 s | +6 ASSUMED, 0.02 | 2 | 1 | -0.33 | - | - | none | I78 |
| 387cb95c99; bf4088ffa7 | 5Y9pyD first buy 0.5 SOL+ (all; mcap <=100) | +6 ASSUMED, 0.01 | 0.5 | 1 / 0 | -0.01 | - | - | none | I136. Qualify retired 5Y9pyD. |
| 243961926a | AtifzK first buy 0.5 SOL+ | +6 ASSUMED, 0.01 | 0.5 | 0 | - | - | - | none | I136. Thin ticket (8). |
| 396fa05089; 4d81cdb14a | A1eN4E; 7kgWHs first buy 0.3 SOL+ | +6 ASSUMED, 0.003 | 0.5 | 0 / 0 | - | - | - | none | I131. Thin tickets. |
| efe459e8a2; 2ebf04efe8 (+5 same-signal records) | FLPjyS first buy 0.3 SOL+; TP+100% SL-20% hold120s copy50% | +6 ASSUMED, 0.003 | 0.5; 2 | 1 / 1 (0 wins) | -0.43 / -1.65 | - | - | none (1 of 30) | I108. First forward coin (07:23) lost on every size; the 2 SOL loss is 83 % of the buy: read that trade next turn. |
| 43cb15dbae | big wallets first buy 1 SOL+ | bid- model, 0.05 | 2 | 57 | -3.46 | - | - | failed | I75 rejected |
| older: 0cd0374b1d, 2243373f00, 3eb9ccf852, 16a2872b0c, a69e410992 | - | - | - | - | negative | - | - | failed | rejected / not as live |
| (no record) I155, closed | any buyer's 0.5-1 SOL buy in FvHwFe's band | +3@1 ASSUMED | 0.5 | - | - | - | - | - | Lab 9c6a6e: lower -0.031 to -0.040. Not frozen. |

f98f in one line: on paper at +3@1 and 0.2 SOL it is +7.31 over 571 trades, but five trades make more than half of it on the whole record and on each half, the 60 s hold loses on the same moments, and in the lab it stops paying 7 trades behind. Its live place decides it and was never measured (I81, the owner's).
