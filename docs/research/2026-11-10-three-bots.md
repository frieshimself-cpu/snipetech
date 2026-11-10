# Three profitable bots read off the chain, and what a known wallet's buy is worth by context (10 Nov, 12:30 UTC)

**Question (owner, 10 Nov):** "You can literally see on chain exactly what he's doing." "Look at every factor possible:
coin type, volume, time of day, who's in the coin, specific people, market cap, literally anything."

**Answer so far: the three bots do the same simple thing (buy 1-3 SOL right behind someone's 1-3 SOL buy, in the same
block, sell on a timer within seconds), and the tape says that trade is worth most when a second known wallet is
already in the coin, the coin has volume and some age, and the buy followed is 1 to 3 SOL.** All of it is before our
own costs; the paper test at 1-5 SOL with the bid counted is the next step (idea I51).

Tools (new, 10 Nov): `snipetech playbook <wallet>` and `snipetech copy-context`. Tape: 72 h to 10 Nov 12:15 UTC.

## The bots

| | 6Z91Dgiz… | niggerd5…dkm5zS | Fhbh1DTU… |
|---|---|---|---|
| coins | 1,525 | 186 | 532 |
| result, closed trips, before bids | +380 SOL | +73 SOL | +92 SOL |
| first buy in the create block | 46 % | 1 coin | 16 % |
| first buy in the same block as another buy | 33 % | 82 % | 67 % |
| first buy 1-2 blocks after another buy | 20 % | 17 % | 15 % |
| usual sizes paid (SOL) | 1.8, 3.0, 1.2, 2.0, 2.5 | 3.0, then 3.2-5.5 | 2.0, 3.0, 0.5, 5.0 |
| the buy it follows (median, SOL) | 1.6 | 2.0 | 1.45 |
| trades behind that buy (median) | 1 | 1 | 1 |
| SOL in the curve when it buys (25-75 %) | 9-28 | 8-28 | 14-30 |
| coin age when it buys (median) | 80 s | 2.5 min | 18 s |
| first sell after (25 / 50 / 75 %, slots) | 7 / 9 / 15 | 10 / 14 / 19 | 5 / 8 / 19 |
| share sold first (median) | 40 % | 25 % | 100 % |
| sells a coin (median) | 3 | 5 | 2 |
| gain at the first sell (25 / 50 / 75 %) | 0 / +5.2 / +14.2 % | −0.8 / +6.0 / +15.0 % | 0 / +2.8 / +12.1 % |
| first sell at a loss | 25 % | 28 % | 25 % |
| the wallet it followed sold first | 19 % | 20 % | 11 % |
| bid a buy where known (median; n) | 0.033 SOL (107) | 0.21 SOL (8) | 0.0011 SOL (28) |

- **No wallet list.** 6Z91 sat right behind 446 different wallets; its ten most followed cover 19 % of its copies, and
  the wallets it follows most often are followed about a quarter of the time (5qMp9p2o 28 %, Solanaswaggy 27 %,
  3VUNtVtj 20 %). The other two: 83 and 252 wallets. Many of the wallets ahead of them are the same kind of bot.
- **The exit is a timer.** The first sell comes 3 to 6 seconds in, up or down, in pieces; it does not wait for the
  wallet ahead to sell.
- **Create-block buys (6Z91):** 696 coins of 621 different creators, behind a creator's own buy of 1.4 SOL at the
  median: a rule on the create itself, not a creator list. +126 SOL, 46 % wins.

## What a known wallet's buy is worth by context

17,439 first buys of 0.5 SOL or more by labelled wallets (18 KOLs, 2,663 tracked) in 9,794 coins launched in the
window. Entered right behind the buy, in its block, where 20 of our 24 live copies since 7 Nov landed. Average price
change in percent, before 2.5 % of fees, the bid and our own push on the price; "13 slots" is 5 seconds.

| split | n | 2 slots | 13 slots | 30 slots | 150 slots | above fees at 13 |
|---|---|---|---|---|---|---|
| all | 17,439 | +5.9 | +4.2 | +2.9 | −2.4 | 48 % |
| **the first known wallet in the coin** | 9,794 | +3.6 | +0.5 | −1.6 | −8.9 | 40 % |
| **the second** | 2,379 | +8.9 | +8.5 | +8.1 | +4.9 | 56 % |
| the third | 1,521 | +8.9 | +9.6 | +9.1 | +6.2 | 59 % |
| the fourth or later | 3,745 | +8.7 | +9.0 | +8.6 | +6.5 | 61 % |
| coin volume before: under 5 SOL | 2,783 | +1.1 | −1.8 | −5.2 | −12.7 | 36 % |
| 5-20 SOL | 4,168 | +4.2 | +0.5 | −1.3 | −9.9 | 41 % |
| 20-60 SOL | 4,088 | +7.6 | +6.1 | +4.9 | −0.1 | 50 % |
| 60 SOL and more | 6,400 | +8.0 | +8.0 | +7.8 | +5.4 | 58 % |
| coin age: under 30 s | 8,064 | +5.4 | +2.7 | +0.6 | −6.5 | 44 % |
| 30 s to 5 min | 6,967 | +5.7 | +4.4 | +3.6 | −0.9 | 50 % |
| 5 to 60 min | 1,923 | +8.0 | +8.9 | +8.5 | +6.9 | 60 % |
| the followed buy: under 1 SOL | 10,778 | +3.0 | +1.1 | −0.2 | −5.4 | 42 % |
| **1-3 SOL** | 5,613 | +11.3 | +10.5 | +9.5 | +3.6 | 61 % |
| 3-10 SOL | 932 | +7.4 | +4.2 | +1.2 | −1.7 | 53 % |
| 10 SOL and more | 116 | +0.9 | −11.4 | −16.4 | −21.8 | 18 % |
| hour (UTC) 04-07, the best | 3,054 | +7.3 | +6.0 | +4.5 | +1.5 | 51 % |
| hour (UTC) 12-15, the worst | 2,060 | +4.3 | +3.0 | +1.4 | −3.0 | 46 % |
| a labelled KOL | 280 | +14.4 | +14.5 | +14.6 | +15.3 | 75 % |
| a tracked wallet | 17,159 | +5.8 | +4.0 | +2.7 | −2.7 | 48 % |

The means barely move without their 3 best events (the `ex3` columns of the job output), so no single coin makes them.

**Other buys later in the followed wallet's block** (what the bots add): none, −3.0 % at 13 slots; under 2 SOL, +3.3 %;
2-10 SOL, +20.0 % (83 % above fees); 10 SOL and more, +51 %. This one is known only after the block: it says the gain
belongs to whoever is right behind the leader, ahead of the other bots, which is what the bid buys.

## The same buys entered one block late

The same 17,426 buys, entered at the end of the block after the wallet's (`copy-context --entry-slots 1`): −0.3 % at
5 slots, −1.7 % at 13, −2.9 % at 30, −8.0 % at 150; a quarter to a third above fees. No split is worth more than +1 %:
the second known wallet in −0.2 % at 13 slots, 60 SOL of volume +0.1 %, a 1-3 SOL buy −0.7 %, a labelled KOL +0.9 %.
The whole value of the trade is inside the followed wallet's block, right behind it.

## Every wallet's buy of 1 SOL or more, and which ones the bots took

63,527 first buys of 1 SOL or more by any wallet in 6,744 coins of the last 24 h, entered right behind
(`copy-context --any-buyer --min-sol 1 --behind-leader --taken-by <the three bots>`).

- **Young, thin coins are not readable this way.** Under 30 s of age, under 5 buyers or under 5 SOL in the curve the
  averages are +40 to +90 %: creators' bundles, several wallets of one sender in one block, where nobody can land
  between them. Leave these rows out.
- **Established coins:** behind any 1 SOL buy in a coin older than 30 s, +3.5 to +4.6 % at 13 slots; with 60 SOL of
  volume +3.3 %; with 100 or more earlier buyers +3.0 %. Less than behind a known wallet in the same coins (+8 to +9 %),
  so who buys matters.
- **Who buys:** a labelled KOL +15.2 % at 13 slots (120 buys, 75 % above fees); a tracked wallet +6.4 %.
- **What the bots took:** one of the three was in the block behind 1.3 % of these buys: 4.2 % behind a labelled KOL
  and 4.0 % behind a tracked wallet against 1.1 % behind an unlabelled one, 2 to 3 % in the youngest coins, 0.9 % at
  12-15 UTC. No single factor here makes them buy: their trigger is narrower than any one split, which is what the
  50-factor `study` of each bot is for.

## The second bot's rule, from 50 factors (`study`, 12:55 UTC)

282 entries of niggerd5…dkm5zS against 4,230 moments it passed on, in the same minutes, and 279 sells against 1,055
moments it held. A depth-3 tree on the strongest factors, cross-validated at 93 %:

- **It buys when the coin is hot this second:** at least 1 SOL traded in the last 5 s (its median 5.9 SOL; the moments
  it passed on, 0), a trade in the same second, and a buy of 0.87 SOL or more in the last minute. 261 of its 282
  entries fit, against 231 of the others.
- **What "hot" looks like at its entries (medians):** 22 SOL traded in 30 s and 39 SOL in a minute, 6 buys of 1 SOL or
  more in the minute, 20 different buyers in 30 s, price +11 % in 5 s, +25 % in 30 s and +96 % in 5 minutes, among the
  12 busiest coins of the market, market cap 74 SOL (40 to 186: 18 to 75 % of the curve), 82 s old (6 s to 6.5 min).
- **Who is in does not matter to it:** a labelled KOL bought in the minute before 3.5 % of its entries. The creator had
  bought 3 SOL at the median and had already sold in 82 %.
- **It sells when the tape pauses:** every one of the 113 sells made after a second with no trade in the coin, held
  over a second, fits "held more than 1 s and the last trade a second or more ago"; the rest come as others' sells pile
  up (more than 5 in 5 s). Its first sell is 3 s in at the median. This is the dead exit OKTech has (1 s at least).

So this bot is not a copier at all: it rides volume bursts in the same block as a 1 SOL buy and leaves at the first
pause. The lab's momentum rules were scored behind the 3rd buy after the trigger; at the place right behind the buy,
with the bid charged, they have not been.

## What this does not say yet

- These are price changes for a copy too small to move the price. A 2 SOL buy in a coin holding 15 SOL pushes the price
  about 10 % and gives about 4 % back on the way out; fees are 2.5 %; a 0.035 SOL bid on 2 SOL is 1.8 %. The good
  splits (+8 to +11 %) are the ones left with a margin; the rest are not.
- The splits are one at a time and were read after the fact. Combined rules (second known wallet in, 60 SOL of volume,
  a 1-3 SOL buy) go to the lab at 1-5 SOL with the bid counted, then to forward paper: 20 trades, positive without the
  3 best, before any live proposal.
- Being right behind the leader is an auction. Our 0.003 SOL bid lands there behind quiet wallets; behind wallets the
  bots follow it lands last.
- Running: `copy-context --any-buyer --taken-by <the three bots>` (every wallet's 1 SOL buy, with the share the bots
  followed, by SOL in the curve, buyers, last 30 s of volume) and `study` on each bot (about 50 factors at what it
  bought against what it passed on: socials, creator's history, holders, market activity, time of day).
