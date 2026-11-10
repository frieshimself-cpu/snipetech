# The same-block finding (10 Nov, 11:30 UTC)

**Question (owner, 10 Nov):** "6Z91 makes so much money off copytrading, it just copytraded Gake." How does it do it,
and why does copying the same wallets lose for us?

**Answer: 6Z91 buys in the same block as the wallet it copies and sells about 5 seconds later. After that block the
price falls on average. We land after that block, so we buy what it sells.**

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

## What it means

- The whole move of a copied wallet's buy is inside its own block. A bot in that block takes it and sells into the
  slower copiers within seconds.
- Our copies land 2-4 trades behind the leader at +18 to +43 % price: behind those bots. Every copy test at our
  landing lost for this reason (KOL first buys at every size, single KOLs, the tracked list). Sizes, fees and exits do
  not fix it.
- Two routes are left for copying: (1) land in the leader's block, which is an execution question for OKTech or for
  whatever 6Z91's owner runs; (2) copy only leaders that no fast bot follows inside their block (idea I50, with the
  server's agents: the share of a leader's buys with a same-slot follower, and the result of copying the low-share
  leaders).
- Strategies that do not depend on another wallet's buy (the mid and late swing traders of the winners study, I46)
  are not touched by this.

## How it was measured

SQL on the tape through `snipetech remote sql` (first buy slot per coin for 6Z91; labelled wallets' buys in the same
slot; net SOL per coin after fees; curve price `q_after / b_after` of the last trade at or before each slot offset) and
the batch job `leaders-of 6Z91… --hours 72 --max-slots 0`. Coins of the last 30 minutes are left out so every offset
exists. The price path is an average over coins, before our own fees and price impact.
