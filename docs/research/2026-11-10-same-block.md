# The same-block finding (10 Nov, 11:30 UTC)

**Question (owner, 10 Nov):** "6Z91 makes so much money off copytrading, it just copytraded Gake." How does it do it,
and why does copying the same wallets lose for us?

**Answer: 6Z91 buys in the same block as the wallet it copies, pays for its place right behind that wallet (a median
0.033 SOL a buy), buys 3-5 SOL and sells about 5 seconds later. After that block the price falls on average. Our copies
land in the same block too, but we have only tried 0.2 SOL at a 0.003 SOL bid on quiet wallets: the gap is the bid, the
size, the exit and the choice of wallets, not speed.**

*Corrected at 11:50 UTC: the first version said our copies land a block late. The fills table says otherwise (below).*

All numbers are from the server's tape, 72 hours to 10 Nov 11:20 UTC, wallet
`6Z91Dgiz9E44bMW11rQuMBjt1NwwPoQGXFNZieGfVtAb`.

## What 6Z91 does

| its first buy in a coin | coins | net SOL | per coin | SOL a buy | wins | first sell after |
|---|---|---|---|---|---|---|
| same slot as a wallet on our labelled list | 349 | +145.4 | +0.42 | 4.8 | 54 % | 13 slots (about 5 s) |
| no labelled wallet in its slot | 1,176 | +313.7 | +0.27 | 3.3 | 50 % | 10 slots |

Gake (side wallet `4Hq9UkSX`), 29 coins in 72 h: 6Z91 was in 20 of them. In 8 it bought in Gake's own slot, with
8-36 SOL against Gake's 2-5 SOL, and first sold 2-25 slots later: +15.3 SOL on the 8 (6 wins). In 9 it was already in
the coin before Gake (its launch snipes). In 3 it came later.

Who it follows inside the block (`leaders-of --max-slots 0`): no single wallet is ahead of it in more than 3.4 % of
its coins; it sits 1-3 trades behind them at +10 to +30 % price. It reacts to many wallets, or to the same signals as
other fast bots, not to one short list.

## What the price does after the leader's block

339 curve coins where 6Z91 bought in a labelled wallet's slot. Average price against the price at the end of that slot:

| slots after | +2 | +15 | +30 | +75 (30 s) | +150 (1 min) | +450 (3 min) | +1500 (10 min) |
|---|---|---|---|---|---|---|---|
| average | +1.3 % | −1.7 % | −1.8 % | −5.4 % | −6.9 % | −12.2 % | −18.5 % |

From slot +30 to slot +450 the price rose in 30 % of the coins (average −10.1 %): there is no second wave to buy
either, on average.

## Where our own copies land (the fills table)

| day | leader | copies | same block | next block | trades behind | price gap | our bid a buy |
|---|---|---|---|---|---|---|---|
| 6 Nov | 2YDnmM | 10 | 10 | 0 | 0.2 | +2.0 % | 0.010 |
| 6 Nov | 4yo9CU (crowded) | 6 | 4 | 1 | 4.3 | +29.2 % | 0.021 |
| 7-9 Nov | 7aPo3n | 13 | 10 | 3 | 0.3 | +1 % | 0.003 |
| 8 Nov | AG89fn | 6 | 6 | 0 | 0.2 | +0.6 % | 0.003 |
| 9 Nov | 2Tp9sc | 5 | 4 | 1 | 0.2 | +1.6 % | 0.003 |

Since 7 Nov: 20 of 24 copies in the leader's own block, 0.3 trades behind. OKTech's speed is not the problem. Behind
a wallet many bots follow (4yo9CU) the place inside the block is a fee auction and the minimum bid lands last.

## What 6Z91 pays for its place

142 of its buys have fee data on the tape: priority fee plus tip a buy is 0.033 SOL at the median (a quarter under
0.006, a quarter over 0.07, a tenth at 0.29 or more), 1.3 % of the buy at the median, on buys of 3.5 SOL. The gross
+459 SOL above is before these fees; at the median fee they are about 50 SOL of it, at the mean (0.22) about 340.

## What it means

- The move of a copied wallet's buy is inside its own block and the seconds after it. A bot right behind the wallet
  takes it and sells into the slower copiers.
- We are in that block already. What we have not done is what 6Z91 does: bid for the place behind wallets that bring a
  crowd, at a size where the bid is about 1 % of the trade, and sell within seconds. Our live copies were 0.2 SOL at a
  0.003 SOL bid behind quiet scalpers, where fixed fees alone are 2.5 % of the trade.
- The paper tests that refuted KOL copying scored a copy behind the 3rd buy after the leader's ("+3x"), the place a
  minimum bid gets behind a crowded wallet. A paid place right behind the leader is untested (idea I51: 1-5 SOL, bid
  0.01-0.07 a transaction, exits in seconds, on 72 h of tape; then forward paper).
- The owner's splits (10 Nov: the second known wallet in, the coin's volume, the time of day) are scored by the new
  `copy-context` study at both landings (`--behind-leader` and a block late).
- Idea I50 (leaders nobody follows inside their block) and the winners study's mid and late swing traders (I46) stay
  open beside it.

## How it was measured

SQL on the tape through `snipetech remote sql` (first buy slot per coin for 6Z91; labelled wallets' buys in the same
slot; net SOL per coin after fees; curve price `q_after / b_after` of the last trade at or before each slot offset) and
the batch job `leaders-of 6Z91… --hours 72 --max-slots 0`. Coins of the last 30 minutes are left out so every offset
exists. The price path is an average over coins, before our own fees and price impact.
