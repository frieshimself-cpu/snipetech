# The three bots the owner named, read off our own tape: what they do, what they pay, what they make (12 Nov, 17:30 UTC)

**Question (owner, 12 Nov):** "Just look at this wallet, it's a bot ... you can see it working live making so much."
"It all comes down to fees and having higher ones than other bots." The three wallets:
`6Z91Dgiz9E44bMW11rQuMBjt1NwwPoQGXFNZieGfVtAb`, `7iyYn4gshakdEUkF2bfV4pU3oaYBmg9ktJwGW8kQ9z96`,
`Fhbh1DTUDKt6qu9zVg8Q5VgnZa1WWWbxBxG6pCoJoggA`.

**Answer: two of the three make money, one loses, and the fee is half of the story.**

- **6Z91 made +1,051 SOL in 7 days before its bids** (4,557 finished trades). On the one day we can see its bids it
  made +128 SOL and paid 56 SOL of it in bids: **+73 SOL that day after bids.**
- **Fhbh1D made +312 SOL in 7 days before its bids** (1,745 finished trades); on the day with bids +53 SOL, 9 SOL of
  bids, **+44 SOL after.** More than half of its trades are on Raydium LaunchLab, which our sniper cannot buy.
- **7iyYn4 lost 10.7 SOL in 7 days** (34 trades). It copies the same wallets as 6Z91 with 3 to 7 SOL, pays almost no
  bid (0 to 0.003 SOL where we can see it), is the first outsider behind the wallet in 3 buys of 10 and buys 23 %
  above the wallet it copies at the median (54 % behind Decu). It is the picture of the owner's point: same idea, no
  fee, loses.
- **A higher bid does buy a better place, but it does not guarantee the first one.** Behind 57 followed wallets in the
  last 24 hours, a buyer bidding under 0.002 SOL was first in 13 % of the blocks it was in, one bidding 0.015 to 0.04
  in 51 %, one bidding 0.3 or more in 76 %. The block's highest bidder was first in 56 % of blocks.
- **The bid only pays at size.** The first place behind a wallet is worth about 10 % of the SOL put in, the second
  about 5 %. On 6Z91's average buy there (3.8 SOL) that step is 0.17 SOL, so a bid of 0.03 to 0.06 is cheap. On our
  0.2 SOL buy the same step is 0.01 SOL: the bid that wins the place costs more than the place is worth.

A word on what these numbers are. They are the bots' own real trades, at the place in the block each one really got,
with the bid it really paid. Nothing here is a paper test of ours.

**Windows.** The tape from 5 Nov 16:27 to 12 Nov 16:28 UTC (7 days). Bids (priority fee plus tip) are on the tape
only since 11 Nov 16:34 UTC: every number about bids is from those last 24 hours. Before that the fees are unknown.
On this tape one block (slot) lasts 0.214 seconds (403,000 a day); the notes of 10 Nov counted 0.4 seconds a block,
so their "13 slots, about 5 s" is 2.8 seconds.

Earlier findings this extends, without repeating them: [the same-block finding](2026-11-10-same-block.md) and
[three bots](2026-11-10-three-bots.md) (10 Nov, 72 hours, 142 bids of 6Z91 from a sample).

## Report

OUTCOME: 6Z91 and Fhbh1D win, 7iyYn4 loses, and the difference between 6Z91 and 7iyYn4 behind the same wallets is the
place in the block. A bid moves the chance of the first place from 13 % (under 0.002 SOL) to 51 % (0.015 to 0.04) to
76 % (0.3 and more) on 6,779 buys behind 57 followed wallets in 24 hours; it is never certain. The bots bid per task:
0.03 SOL behind ordinary wallets, 0.2 to 3.4 SOL behind Cupsey, Decu and Slingor, on buys of 5 to 20 SOL.
6Z91 pays 43 % of what its trades make in bids. Nothing was started, tested live or changed.

### 1. Activity (7 days)

| | 6Z91Dgiz… | 7iyYn4… ("DecuCopytrade" on the owner's tracker list) | Fhbh1DTU… |
|---|---|---|---|
| buys | 4,917 | 35 | 2,227 |
| buys a day | 702 | 5 | 318 |
| coins | 3,935 | 34 | 1,032 |
| SOL bought | 14,304 | 170 | 3,806 |
| on the pump.fun curve | 4,405 buys (90 %), 12,461 SOL | 35 buys (100 %) | 203 buys (9 %), 662 SOL |
| on PumpSwap (after the coin left the curve) | 512 buys (10 %), 1,843 SOL | none | 841 buys (38 %), 1,517 SOL |
| on Raydium LaunchLab | none | none | 1,183 buys (53 %), 1,627 SOL |
| buy size, SOL (10 / 25 / 50 / 75 / 90 %) | 1.2 / 1.8 / 2.2 / 3.0 / 4.9 | 3.0 / 4.0 / 4.0 / 6.9 / 6.9 | 0.5 / 0.5 / 1.0 / 2.1 / 4.0 |
| the sizes it types most | 1.8 (960 buys), 3.0 (922), 2.0 (665), 1.2 (630), 2.5 (443) | 7 (10), 4 (10), 3 (7), 5 (5) | 0.5 (562), 1.0 (328), 3.0 (185) |
| largest buy | 32.6 | 6.9 | 16.9 |
| one buy a coin | 97 % of trades | 97 % | 94 % |
| last trade seen | 12 Nov 16:28 | 11 Nov 20:40 | 12 Nov 16:24 |

6Z91 trades all day (110 to 300 buys in every hour of the day over the week). Fhbh1D is busiest from 16 to 01 UTC.

### 2. What triggers a buy

Each first buy of a trade, by what stood in front of it in the block. "Labelled" is a wallet on our lists (kinds
wallet and kol: the owner's tracker and the 18 KOLs).

| its buy is | 6Z91 | 7iyYn4 | Fhbh1D |
|---|---|---|---|
| in the coin's create block (a launch snipe) | 1,689 (36 %) | 0 | 154 (8 %) |
| in the same block as a labelled wallet's buy, behind it | 1,123 (24 %) | 26 (76 %) | 449 (24 %) |
| in the same block as an unlabelled wallet's buy, behind it | 830 (18 %) | 7 (21 %) | 859 (47 %) |
| 1 to 3 blocks (0.2 to 0.6 s) after a buy | 921 (20 %) | 1 | 299 (16 %) |
| no buy in the 4 blocks before | 73 (2 %) | 0 | 79 (4 %) |
| all | 4,636 | 34 | 1,840 |

What each kind made, before bids (finished trades):

| | 6Z91 trades / SOL / a trade / wins | Fhbh1D trades / SOL / a trade / wins |
|---|---|---|
| create block | 1,686 / +288 / +0.17 / 48 % | 143 / +50 / +0.35 / 61 % |
| behind a labelled wallet | 1,096 / +379 / +0.35 / 61 % | 440 / +87 / +0.20 / 54 % |
| behind an unlabelled wallet | 810 / +201 / +0.25 / 59 % | 822 / +91 / +0.11 / 51 % |
| 1 to 3 blocks late | 899 / +165 / +0.18 / 53 % | 280 / +70 / +0.25 / 56 % |

7iyYn4: 26 trades behind a labelled wallet, −8.9 SOL; 7 behind an unlabelled one, −1.3 SOL.

**How far behind the wallet it lands.** Many followed wallets buy with two to four wallets of their own in the next
transactions (their companions: jidn, Flames, daumeneth and Gfree all do). Nobody lands between those. So the place is
counted among outsiders, companions left out.

| behind a labelled wallet, same block | 6Z91 | 7iyYn4 | Fhbh1D |
|---|---|---|---|
| buys | 1,123 | 26 | 449 |
| different labelled wallets followed | 88 | 13 | 100 |
| it is the first outsider | 689 (61 %) | 8 (31 %) | 263 (59 %) |
| second / third / fourth or later | 278 / 97 / 59 | 13 / 1 / 4 | 81 / 57 / 48 |
| its price over the followed wallet's price (25 / 50 / 75 %) | +12 / +18 / +24 % | +14 / +23 / +37 % | +1.1 / +3.2 / +7.1 % |
| the followed buy, SOL (25 / 50 / 75 %) | 1.0 / 1.5 / 3.0 | 1.5 / 2.0 / 3.0 | 1.0 / 1.6 / 3.0 |

The price gap is mostly the bot's own size: 6Z91 buys 2 to 10 SOL into a young curve. Fhbh1D's gap is small because it
buys 0.5 to 1 SOL in bigger pools.

6Z91's copies (behind a labelled or an unlabelled wallet, 1,953 buys): the coin is 13 seconds to 7 minutes old (25 to
75 %; median 57 s), its market cap right after 6Z91's buy is 57 to 122 SOL (median 81), and two thirds of the buys it
follows are 1 to 3 SOL.

**The 15 wallets 6Z91 follows most** (7 days; 59 % of its buys behind labelled wallets; before bids):

| followed wallet | buys behind it | its buy (SOL) | 6Z91's buy | first outsider | SOL made | wins | 6Z91's bid there (median; buys with a bid) |
|---|---|---|---|---|---|---|---|
| jidn `3h65Mm` | 104 | 1.0 | 2.0 | 27 | +9.6 | 57 % | 0.029 (30) |
| Flames `6aXFYX` | 83 | 1.2 | 2.0 | 75 | +7.3 | 43 % | 0.012 (5) |
| found:YARSci `YARSci` | 82 | 1.0 | 2.0 | 26 | +19.8 | 80 % | 0.038 (12) |
| daumeneth `8MaVa9` | 65 | 0.5 | 2.0 | 57 | +2.7 | 48 % | 0.031 (16) |
| Gfree `4yo9CU` (KOL) | 42 | 1.6 | 2.5 | 33 | +10.8 | 79 % | 0.033 (11) |
| Decu `4vw54B` | 38 | 2.5 | 6.5 | 15 | +25.3 | 77 % | 0.296 (4) |
| Solanaswaggy `8DgVVt` | 36 | 2.0 | 2.0 | 31 | −1.4 | 37 % | 0.001 (3) |
| theo side `Bi4rd5` | 30 | 3.0 | 6.0 | 17 | +9.4 | 31 % | 0.005 (3) |
| Limfork.eth `BQVz7f` | 29 | 1.6 | 3.0 | 25 | +5.1 | 69 % | 0.056 (2) |
| Cupseyy `6DQAGJ` | 28 | 3.0 | 10.0 | 20 | +50.7 | 83 % | 0.172 (3), one of 3.38 |
| ColerDevs `99xnE2` | 28 | 1.0 | 3.5 | 23 | +1.5 | 50 % | 0.111 (5) |
| dv `BCagck` | 26 | 4.3 | 4.0 | 14 | +5.9 | 73 % | 0.065 (1) |
| Bean Side `VJSDW6` | 24 | 2.4 | 5.0 | 11 | +2.5 | 64 % | 0.077 (2) |
| Schoen `5hAgYC` (KOL) | 23 | 3.0 | 2.5 | 18 | +7.3 | 65 % | 0.005 (1) |
| Art `CgaA9a` | 23 | 2.1 | 3.0 | 21 | +2.9 | 57 % | 0.047 (1) |

Its best eight followed wallets made +205 of the +379 SOL: Cupseyy +50.7, Gake Side +36.7 (23 buys), Jijo +27.7 (7),
Decu +25.3, YARSci +19.8, Slingor +16.0 (10), found:5Y5nsb +15.0 (22), cap +13.4 (9). It has no short list: behind any
wallet's buy (labelled or not) it followed 490 different wallets, and its ten most followed are 25 % of those buys.

**The 15 wallets Fhbh1D follows most** (7 days; these 15 are half of its buys behind labelled wallets):

| followed wallet | buys behind it | its buy (SOL) | Fhbh1D's buy | first outsider | SOL made | wins | Fhbh1D's bid there (median; buys with a bid) |
|---|---|---|---|---|---|---|---|
| Chester `PMJA8U` | 42 | 1.1 | 0.95 | 22 | +0.9 | 39 % | 0.0011 (3) |
| Cupseyy `6DQAGJ` | 26 | 1.0 | 4.0 | 18 | +25.1 | 69 % | 0.35 (4) |
| Flames `6aXFYX` | 17 | 1.2 | 1.1 | 10 | +0.5 | 44 % | none seen |
| Decu `4vw54B` | 16 | 2.5 | 6.0 | 13 | +10.9 | 81 % | 0.35 (2) |
| Yugi `AQ46kf` | 15 | 4.0 | 0.5 | 15 | −1.4 | 20 % | 0.0011 (3) |
| BIg `6TbDFs` | 14 | 3.0 | 1.0 | 12 | +3.1 | 64 % | 0.0011 (2) |
| Pengu `JAunzN` (KOL) | 13 | 1.3 | 0.95 | 3 | +1.2 | 92 % | 0.0011 (5) |
| risk100x side `67Nwfi` | 13 | 3.3 | 0.9 | 10 | +0.1 | 54 % | none seen |
| 3 \| casino `8rvAsD` | 12 | 2.2 | 0.9 | 5 | −0.5 | 33 % | 0.04 (1) |
| cap `CAPn1y` | 11 | 1.0 | 5.0 | 11 | +7.9 | 55 % | none seen |
| Bean Side `VJSDW6` | 10 | 3.7 | 0.5 | 2 | +0.4 | 60 % | 0.0011 (1) |
| MACXBT `ETU3Gy` (KOL) | 9 | 5.0 | 0.5 | 8 | 0.0 | 22 % | none seen |
| Beaver `GM7Hrz` | 9 | 5.0 | 0.5 | 4 | −0.1 | 22 % | none seen |
| daumeneth `8MaVa9` | 9 | 3.0 | 2.0 | 5 | +2.2 | 89 % | none seen |
| theo side `Bi4rd5` | 8 | 3.0 | 3.0 | 7 | +1.3 | 62 % | 0 (1) |

Fhbh1D runs two settings. Behind most wallets it buys 0.5 to 1 SOL at a 0.0011 SOL bid and makes about nothing a
trade. Behind Cupseyy, Decu and cap it buys up to 11 SOL (4 to 6 at the median) and, on 5 of the 6 such buys with a
known bid, pays 0.35 SOL: 53 buys, +43.9 SOL.

**7iyYn4** followed Decu 6 times (−5.6 SOL, never the first outsider, +54 % over Decu's price at the median), Seb 4
(−0.2), Schoen 3 (+1.8), then ten wallets once or twice. Its coins are 3 to 14 seconds old when it buys.

### 3. What they pay

"Bid" is priority fee plus tip (SOL sent to a landing service) of one transaction. 24 hours to 12 Nov 16:28 UTC.

| a buy | 6Z91 (658 buys) | Fhbh1D (481 buys) | 7iyYn4 (3 buys) |
|---|---|---|---|
| priority fee (25 / 50 / 75 %, max) | 0.001 / 0.0144 / 0.049, max 5.22 | 0 / 0.0001 / 0.0011, max 0.35 | about 0 |
| tip (25 / 50 / 75 %, max) | 0 / 0 / 0, max 2.18 (no tip on 87 % of buys) | 0 / 0 / 0.0011, max 1.5 | 0 / 0 / 0.0029 |
| bid in all (10 / 25 / 50 / 75 / 90 %, max) | 0.001 / 0.006 / 0.029 / 0.061 / 0.152, max 5.22 | 0 / 0.0011 / 0.0011 / 0.0022 / 0.06, max 1.5 | 0 / 0.00001 / 0.0029 |
| mean bid | 0.086 | 0.022 | 0.001 |
| bid as a share of the buy (median) | 1.3 % | 0.2 % | 0 % |
| all bids of the day | 56.3 SOL | 10.6 SOL | 0.003 SOL |

| a sell | 6Z91 (2,892 sells) | Fhbh1D (864 sells) | 7iyYn4 (16 sells) |
|---|---|---|---|
| bid (median, max) | 0.00011, max 0.008 | 0.0001, max 0.006 | 0.0001 |
| all sells of the day | 0.47 SOL | 0.23 SOL | 0.001 SOL |

Eight older fee records of 7iyYn4 (five buys and three sells, 5 to 10 Nov, from a study's sample) say the same: buys
of 3 to 7 SOL at bids of about 0 to 0.0025 SOL.

- **They pay to get in, not to get out.** The buy carries the bid; a sell costs 0.0001 SOL.
- **6Z91 pays with the priority fee**, not with tips: 87 % of its buys send no tip at all.
- **The bid is a setting per task, not a reaction to the crowd.** 6Z91's bid goes with the size it types: 0.009 SOL on
  its 1.2 SOL buys (99 buys), 0.029 on 1.5 to 2.5 SOL (242), 0.033 on 2.5 to 4 SOL (181), 0.125 on 4 to 8 SOL (80),
  0.82 on 8 SOL and more (12). With nobody else behind the wallet in the block its median bid is 0.012 (42 buys);
  with one other buyer 0.027 (71); with four or more 0.029 (197). It does not rise with the crowd.
- **By market cap** (SOL, right after its buy): under 40 SOL 0.012 (135 buys, the launch snipes); 40 to 60 SOL 0.029
  (212); 60 to 100 SOL 0.033 (115); 100 to 200 SOL 0.023 (79); over 200 SOL 0.056 (26). No clear slope past the
  launch.
- **By followed wallet it changes a lot.** Behind jidn, daumeneth, YARSci and Gfree: 0.029 to 0.038 (69 buys). Behind
  ColerDevs 0.11 (5 buys), ramsetx 0.21 (4), Decu 0.21 to 0.64 (3 of 4 buys), Slingor 0.48 (3, one of 1.19), Cupseyy
  0.016, 0.17 and 3.38 (3 buys; the 3.38 SOL was on a 15 SOL buy). These are the wallets where it buys 5 to 20 SOL.
- **Against the others in the block.** In 406 blocks where 6Z91 bought behind a first buy and another buyer's bid is
  known, 6Z91 was the highest bidder in 264 (65 %); at the median its bid was 3.6 times the highest of the others'.
  Behind the
  57 followed wallets, companions left out, it was the highest bidder in 81 of 127 blocks and first in 48 of those
  (59 %); where it was not the highest bidder it was first in 14 of 46 (30 %). Of the 110 outsiders that landed ahead
  of it, 58 % had bid less than it did.

### 4. How they sell

Seconds are blocks times 0.214.

| | 6Z91 (4,557 finished trades) | 7iyYn4 (34) | Fhbh1D (1,745) |
|---|---|---|---|
| first sell after (10 / 25 / 50 / 75 / 90 %) | 0 / 1.1 / 2.1 / 3.2 / 3.6 s | 1.9 / 2.4 / 3.2 / 3.6 / 3.9 s | 0.6 / 1.1 / 1.5 / 3.4 / 11.8 s |
| last sell after (25 / 50 / 75 / 90 %) | 1.9 / 10.1 / 19.3 / 23.5 s | 3.4 / 6.2 / 14.6 / 22.3 s | 1.1 / 2.1 / 9.8 / 30.8 s |
| sells a trade (25 / 50 / 75 / 90 %) | 1 / 4 / 6 / 9 | 1 / 3 / 4 / 7 | 1 / 1 / 3 / 6 |
| out in one sell | 25 % of trades | 38 % | 67 % |
| share sold in the first sell (median) | 35 % | 30 % | 100 % |
| price at the first sell against its own buy (25 / 50 / 75 %) | 0 / +5.2 / +13.2 % | −18.8 / +3.5 / +16.4 % | 0 / +0.9 / +7.2 % |
| first sell under its buy price | 24 % | 44 % | 32 % |
| sells above its buy price, all sells | 67 % | 71 % | 66 % |
| still selling after 5 minutes | 0.7 % of trades | none | 3 % |

**6Z91 sells in pieces on a clock.** On 3,419 one-buy trades: 42 % of the coins are sold after 2 seconds, 65 % after
5, 82 % after 10, 98 % after 20. The pieces come about every 3 seconds: 40 % at 1.9 s, 20 % at 5.3 s, then 17 %,
17 %, 15 % and the rest at 8, 11, 14 and 16 s (medians).

The first sell falls in four groups (all 4,557 finished trades, before bids):

| the first sell comes | trades | what it does | SOL made | a trade | wins |
|---|---|---|---|---|---|
| in the buy's own block or the next two (under 0.6 s) | 788 | sells 40 % into the buyers behind it, +8 to +9 % over its buy | +213 | +0.27 | 62 % |
| 0.6 to 1.7 s | 547 | 40 % or all | +80 | +0.15 | 51 % |
| 1.7 to 2.8 s | 1,277 | usually everything in one sell, near its buy price | −3.6 | −0.003 | 36 % |
| 2.8 to 4.3 s | 1,580 | 30 %, then five more pieces | +371 | +0.24 | 58 % |
| later than 4.3 s | 365 | 10 to 20 % pieces at +27 to +58 % | +391 | +1.07 | 87 to 92 % |

Read: it takes a first piece at once when buyers pile in behind it, drops the whole position at about 2 seconds when
nothing happened, and lets the rest run in pieces. One trade in ten sells its first piece in the very block it bought
in. 7iyYn4's sells have the same shape (30 % at 3.2 s, pieces every 3 s, out by 20 s). Fhbh1D mostly sells
everything at once after 1 to 1.5 seconds.

Into strength or weakness: both winners sell two thirds of their pieces above their buy price. They sell into the
buyers who come after them, in the seconds when those buyers are still arriving.

### 5. Results

A trade is "finished" when 99 % of the coins bought were seen sold. SOL in and out include pump.fun's fee.

| 7 days, before bids | 6Z91 | 7iyYn4 | Fhbh1D |
|---|---|---|---|
| finished trades | 4,557 | 34 | 1,745 |
| SOL put in | 13,707 | 172.5 | 3,511 |
| SOL made | **+1,051.3** | **−10.7** | **+311.7** |
| a trade (mean; 25 / 50 / 75 %) | +0.23; −0.08 / +0.02 / +0.27 | −0.31; −1.07 / −0.39 / +0.41 | +0.18; −0.02 / +0.01 / +0.15 |
| return on the SOL put in | +7.7 % | −6.2 % | +8.9 % |
| winning trades | 54 % | 44 % | 54 % |
| a day | +150 | −1.5 | +45 |
| without its 10 best trades | +923 | −19.8 | +233 |
| best / worst trade | +19.7 / −6.4 | +2.0 / −2.7 | +15.9 / −1.8 |
| still open (coins not all seen sold) | 79 trades: 758 SOL in, 720 SOL back so far | none | 95 trades: 327 SOL in, 277 back so far |
| never sold at all | 3 trades, 9.8 SOL | none | 5 trades, 22.1 SOL |
| all trades, the unsold coins counted as worth nothing | +1,013 | −10.7 | +262 |
| sells of coins bought before the window (left out) | 287 sells | none | 161 sells |

By day, finished trades by the day of the buy, before bids:

| day (UTC) | 5 Nov (from 16:27) | 6 | 7 | 8 | 9 | 10 | 11 | 12 (to 16:28) |
|---|---|---|---|---|---|---|---|---|
| 6Z91, SOL | +62 | +184 | +149 | +138 | +106 | +149 | +190 | +75 |
| Fhbh1D, SOL | +5 | +47 | +69 | +35 | +28 | +47 | +53 | +28 |
| 7iyYn4, SOL | −2.9 | −6.2 | −0.6 | −0.3 | −1.4 | −0.9 | +1.7 | no trade |

**After the bids** (finished trades of the 24 hours where every transaction's bid is known):

| | 6Z91 | Fhbh1D | 7iyYn4 |
|---|---|---|---|
| trades | 602 | 275 | 3 |
| SOL put in | 1,751 | 558 | 17.5 |
| made before bids | +128.3 | +53.1 | −0.8 |
| bids paid (3,405 / 1,010 / 19 transactions) | 55.6 | 8.8 | 0.003 |
| **made after bids** | **+72.7** | **+44.3** | **−0.8** |
| share of the result that went to bids | 43 % | 16 % | none |
| winning trades after bids | 47 % | 56 % | 1 of 3 |

Before 11 Nov 16:34 the bids are unknown. If 6Z91 paid the same share all week, its week is about +600 SOL after
bids; that is an estimate from one day, not a measurement.

6Z91's day after bids, by what triggered the buy: behind an unlabelled wallet +33.9 SOL (105 trades), behind a
labelled wallet +21.6 (149), launch snipes +10.7 (196), 1 to 3 blocks late +6.7 (141).

6Z91's day by the size of its bid: under 0.03 SOL a buy, +56.8 SOL after bids on 336 trades. From 0.03 to 0.15 SOL,
−4.6 SOL on 207 trades: the bid ate the whole result. At 0.15 SOL and more, +20.5 SOL on 59 trades (4 to 7 SOL a
buy at the median), its tasks behind the big names.

The larger the buy, the better it did all week, before bids: +1.4 % on its 1.2 SOL buys (705 trades), +3.6 % on 2 SOL
(1,587), +5.0 % on 3 SOL (1,419), +9.4 % on 4 to 8 SOL (658), +17.2 % on 8 SOL and more (188 trades, +408 SOL).

### 6. The one-line recipes

What an OKTech copy sniper would have to do to be each bot. None of this is tested by us; sizes above 0.2 SOL and new
bids are the owner's yes for each step.

**Corrected the same evening** ([the recipe as rules](2026-11-12-recipe-6z91.md)). The copy half's line below reads
the bot's whole week as one clock. On its copies alone the bot sells 30 % at 3.2 seconds and 17 % every 2.7 seconds,
and drops a copy whole at 2 seconds on 2 of 100; the 40 % at 2 seconds and the drop are its launch snipes. The 57 to
122 SOL is the market cap after the bot's own buy (47 to 106 after the buy it follows), and the three bands are the
middle half of what it buys, not its limits. `lab --recipe 6z91` runs the corrected rules.

- **6Z91, the copy half.** Copy any wallet's buy of 1 to 3 SOL in a pump.fun coin 13 seconds to 7 minutes old at a
  market cap of 57 to 122 SOL, in the wallet's own block, 2 to 3.6 SOL a buy, priority fee about 0.03 SOL and no tip,
  sell 40 % within 2 seconds and the rest in pieces every 3 seconds, everything out by 20 seconds, drop everything at
  2 seconds when the price has not moved. Separate tasks for Cupsey, Decu, Gake's side wallet, Slingor: 5 to 20 SOL
  at 0.2 to 3.4 SOL of bid. It is the first outsider in 6 of 10 buys; a 0.003 SOL bid like ours is first in 2 of 10.
- **6Z91, the launch half.** Buy 1.2 or 1.8 SOL in the create block behind the creator's own buy (0.5 SOL or more:
  behind a creator buy under 0.5 SOL it made +0.06 SOL a trade, 30 % wins, 284 trades), bid 0.007 to 0.06 SOL, same
  exit. Our launch snipes land a block after the create, not in it, so this half is out of reach today.
- **Fhbh1D.** Two tasks. One buys 0.5 to 1 SOL behind anyone's 1 SOL buy at a 0.0011 SOL bid and sells everything
  after 1 to 1.5 seconds; it makes little a trade and half of it is on LaunchLab. The other buys 4 to 11 SOL behind
  Cupsey, Decu and cap at a 0.35 SOL bid (+43.9 SOL on 53 buys in 7 days, before bids).
- **7iyYn4.** Do not be this bot. 3 to 7 SOL behind Decu and a dozen others at a bid of 0 to 0.003 SOL, the same
  exit as 6Z91. It lands behind the bots that pay and loses 6 % of what it puts in.

## What this says about the owner's thesis

The thesis: behind a wallet with a lot of motion the copy bots pile in, so the tip must beat the other bots'.

**What the tape says, on every buy of 57 followed wallets in 24 hours** (Cupsey's, Decu's, Gake's and frankdegods'
wallets, the 18 KOLs, and the wallets the three bots follow most; 3,068 blocks where the wallet bought 0.1 SOL or
more; create blocks and the wallet's own companions left out):

- In 1,837 blocks (60 %) at least one outsider bought behind the wallet in its own block; in 1,292 two or more; in
  657 four or more.
- **The place by the bid** (6,779 outsider buys in the 1,292 blocks with two or more outsiders):

| the buyer's bid (SOL) | buys | first behind the wallet | first or second | first, in blocks with 4 or more outsiders |
|---|---|---|---|---|
| under 0.0005 | 3,490 | 13 % | 28 % | 9 % |
| 0.0005 to 0.002 | 1,638 | 13 % | 31 % | 8 % |
| 0.002 to 0.006 (our copies bid 0.003) | 620 | 20 % | 44 % | 15 % |
| 0.006 to 0.015 | 344 | 40 % | 67 % | 27 % |
| 0.015 to 0.04 | 507 | 51 % | 88 % | 50 % |
| 0.04 to 0.1 | 67 | 45 % | 70 % | 40 % |
| 0.1 to 0.3 | 71 | 48 % | 75 % | 45 % |
| 0.3 and more | 42 | 76 % | 95 % | 71 % |

- **Does the highest bid get the first place?** In 725 of the 1,292 blocks (56 %). In crowded blocks (four or more
  outsiders) in 284 of 657 (43 %). When the highest bid is 0.01 SOL or more and at least 20 times the next one, in 115
  of 149 (77 %); 5 to 20 times, 64 of 103 (62 %); 2 to 5 times, 36 of 83 (43 %).
- **Pairs of buyers stand in the order of their bids in 60 % of cases** (15,122 of 25,022 pairs; 50 % would mean the
  bid does nothing, 100 % a pure auction).
- **Who beats a high bid.** In the 139 blocks where a bid of 0.01 SOL or more was the highest and still not first,
  the first buyer had bid 0.003 SOL at the median and stood two transactions behind the followed wallet. That is a
  wallet sent together with the followed wallet's own buy, or one that is simply faster. No bid buys that.
- **The first outsider's bid:** median 0.0011 SOL; 62 % under 0.003; 82 % under 0.03; 95 % under 0.1.

So the owner is right that the fee matters, and by how much: it lifts the chance of the first place from one in eight
to one in two at 0.015 to 0.04 SOL, and to three in four at 0.3 SOL and more. It is not the whole answer. Between
0.015 and 0.3 SOL more bid buys nothing more on this tape, the highest bidder is beaten in four blocks of ten, and the
bots that win also choose whom to follow, buy large, and are out in seconds.

**What tip would have beaten these bots.**

- 6Z91: more than 0.03 SOL beats its bid on half of its buys, more than 0.06 on three quarters, more than 0.15 on nine
  in ten. Behind Cupsey, Decu, Gake's side wallet and Slingor it bid 0.2 to 0.6 SOL, once 1.19 and once 3.38.
- Fhbh1D: more than 0.0011 SOL beats it on most buys. Behind Cupsey and Decu it bid 0.35 SOL.
- 7iyYn4: any bid.
- The others in those blocks: 0.05 SOL was above the first outsider's bid in 93 % of the 1,837 blocks and above every
  outsider's bid in 90 %; 0.03 SOL in 82 % and 78 %; our 0.003 SOL in 62 % and 54 %.
- Outbidding is not landing first. 6Z91 was the highest bidder in 81 blocks and first in 48 of them.

**What the first place is worth, and why size decides.** 6Z91, 7 days, buys behind a first buy in the block,
companions left out, before bids:

| place among outsiders | 6Z91 trades | SOL a trade | return | Fhbh1D trades | SOL a trade | return |
|---|---|---|---|---|---|---|
| first | 1,104 | +0.379 | +9.9 % | 627 | +0.202 | +8.7 % |
| second | 370 | +0.209 | +5.4 % | 205 | +0.052 | +3.1 % |
| third | 193 | +0.143 | +4.5 % | 144 | +0.105 | +5.9 % |
| fourth or later | 239 | +0.237 | +7.3 % | 286 | +0.089 | +6.2 % |

The step from second to first is worth 4.5 to 5.6 % of the buy. On 6Z91's average buy there (3.8 SOL) that is 0.17
SOL: a 0.03 to 0.06 SOL bid is a fair price for an even chance at it. On our 0.2 SOL copy the same step is 0.009 to
0.011 SOL, and the whole first-place result is about 0.02 SOL. The bid that gets the first place half the time (0.015
to 0.04 SOL) costs as much as the place makes. **A fee war cannot be won at 0.2 SOL a buy. It is a game for 2 SOL and more.** These rows
are what the bots made where they landed; the blocks where a bot is first are not the same blocks as where it is
third, so the step is a guide, not a promise.

**The cleanest pair on the tape.** Behind Decu, 7 days: 6Z91 bought 38 times with 2 to 10 SOL, first outsider 15
times, +25.3 SOL, 77 % wins; its four buys there with a known bid paid 0.006, 0.21, 0.30 and 0.64 SOL. 7iyYn4 bought 6
times with 7 SOL, never first, −5.6 SOL; the one buy there with a fee record paid 0.0000025 SOL, and its seven other
known buy bids are 0 to 0.003 SOL. Fhbh1D bought 16 times, mostly 5 to 8 SOL, first 13 times, +10.9 SOL; its two buys
there with a known bid paid 0.35 and 0.07 SOL.

## Other wallets doing the same thing

Found two ways: wallets that hold the first outsider place behind several of the 57 followed wallets (24 hours), and
wallets that keep turning up in the three bots' blocks (7 days). "Cash" is every SOL out of sells minus every SOL
into buys over the 7 days, pump.fun's fee included: coins not yet sold count as lost and sells of older coins count as
gain, so it is a screen, not a result.

| wallet | what it does | first outsider (24 h) | size (SOL) | its bid | 7-day cash | 24 h cash and bids |
|---|---|---|---|---|---|---|
| `dKMjeGJ4Mk9S3hxdfTThvZ7exks2xMwWqSM5NvN2CTK` | the same trade as 6Z91, bids higher | 10 of 17 blocks, 7 wallets | 3 | 0.2 (0.01 to 0.3) | **+558** on 1,059 buys | +132, bids 48.8 |
| `2H6DwrqAsETL1PjrT5KAX3MAELx71FuDkKVZj18PaeJf` (on our list as "winner +107 SOL/70") | fewer, larger buys | 13 of 17, 9 wallets | 5 | 0.018 (0.011 to 0.43), max 2.57 | **+304** on 250 buys | +33.5, bids 18.4 |
| `3cbuqqiTmcg8GD48uAaLH1NViQwUj8C1Czx9u3xozpaK` | first place on a tiny bid | 10 of 13, 8 wallets | 3 | 0.001 | +84 on 135 buys | +1.2, bids 0.17 |
| `HmR7SxDoPTcYFdktbyU4fqmjkvGgXKXSQ3zXfm9Xj6hj` | pays for the place | 8 of 9, 6 wallets | 4 | 0.18 (0.07 to 0.35) | +49 on 124 buys | +10.6, bids 2.6 |
| `88UqCRfa4yQyRCmTvw8UbFsALnn32ducJF9csEbUwqPD` | first place most often after 6Z91, on a small bid | 34 of 47, 14 wallets | 3 | 0.004, max 1.11 | +36 on 535 buys | +16.1, bids 3.9 |
| `GM8tLq2RRn5d8ArXtbwrLpp3ZV9RBUxSjWxSJstqnfsr` | new (3.5 days), first place on a small bid | 16 of 19, 6 wallets | 4 | 0.002 | +25 on 59 buys | +9.4, bids 0.05 |
| `DDb4vLEkQs78Zs8yxsZuKFAG8C85k5cFmevbMjXxphkK` | new (4.6 days), first place with no bid | 8 of 9, 8 wallets | 4 | about 0 | +21 on 53 buys | +4.2, bids 0.2 |
| `638PYXz5TuEbPRAhxxsP23KcJxkwTYTNRotVCwS5Yf5x` | **the warning:** bids the most, loses | 10 of 11, 5 wallets | 6 | 0.37, max 6.77 | **−197** on 168 buys | −2.4, bids 11.1 |
| `sssssDdMNAWKingjpEojkTNdVuZrBe7FsJLaGtexe7d`, `BUkpShoEWRiPckdFs3kzYGjBPfUhL6G39ufo84QeogPP`, `7PedQZmcjFdgTxA2L94JhY9r6swfRrysCxpnPeJ1SwR7` (all on our list as winners) | another trade: 10,000 to 27,000 buys of 1 SOL a week at bids of 0.0005 to 0.0013 | first in 11 of 43, not seen, 9 of 14 | 1 | 0.001 | +710, +423, +319 | +172, +125, +55; bids 4.3, 1.1, 3.4 |

- `dKMjeGJ4…` is the one to study next: in the 24 hours with bids its cash after bids is +83 SOL on 253 buys, against
  6Z91's +73 SOL on 658 buys.
- Four of the wallets take the first place with bids of 0.004 SOL and less (`88UqCRfa…`, `3cbuqqiT…`, `GM8tLq2R…`,
  and `DDb4vLEk…` with none). They are faster, or they see the followed wallet's buy earlier than the others. This
  is the part of the place a bid does not buy.
- `638PYXz5…` pays the most for the place and has lost 197 SOL of cash in the week. The highest fee alone is not a
  strategy.
- The wallets that buy right behind one wallet only (`6ejT3iUN…`, `HrDLkiLC…`, `9gnJFS3L…`, `B5HLmoX8…` behind jidn
  at fixed bids of 0.03 and 0.01; `J4hrKzU3…` and `5qMp9p2o…` behind Flames at 0.02; `EJExDn3P…` and `EUvQT21t…`
  behind Gfree at 0.004; `HodLXSGK…`, `TWiNYzgn…`, `PUMPRLSw…` behind daumeneth at 0.003) are that wallet's own set
  or its dedicated copiers. The tape cannot tell which. Behind jidn the ones bidding 0.03 land ahead of the one
  bidding 0.01 most of the time (the highest bidder is first in 147 of 171 blocks): among transactions sent at the
  same moment, the order does follow the bid.

**How the system can find these by itself.** The job `snipetech place-bids` (built 12 Nov, not yet run on the
server's tape) already lists `first_place_holders` for the moments a rule follows. A wallet that is the first
outsider behind five or more different followed wallets, with buys of 1 SOL or more, is a candidate of this kind;
its week of cash and its bids take one indexed query each. That screen is not wired into discovery yet.

## Lessons

- 6Z91 made +1,051 SOL in 7 days before bids (4,557 trades, +7.7 % of the SOL put in) and +72.7 SOL after bids on the one day with bids (602 trades; 55.6 SOL of bids, 43 % of the result).
- Fhbh1D made +312 SOL in 7 days before bids (1,745 trades) and +44.3 after bids in 24 h (275 trades, 8.8 SOL of bids); 53 % of its buys are on Raydium LaunchLab.
- 7iyYn4 lost 10.7 SOL in 7 days (34 trades, 3 to 7 SOL each): the same wallets and the same exit as 6Z91 at a bid of 0 to 0.003 SOL, never first behind Decu (6 buys, −5.6 SOL) where 6Z91 made +25.3 on 38 buys at 0.2 to 0.6 SOL.
- A bid buys a chance, not the place: first outsider in 13 % of blocks under 0.002 SOL, 20 % at 0.002 to 0.006, 40 % at 0.006 to 0.015, 51 % at 0.015 to 0.04, 45 to 48 % at 0.04 to 0.3, 76 % at 0.3 and more (6,779 buys behind 57 followed wallets, 24 h). The highest bidder is first in 56 % of blocks, 43 % when four or more outsiders compete.
- The first place is worth about 10 % of the buy and the second about 5 % (6Z91: +0.379 against +0.209 SOL a trade, 1,104 and 370 trades; Fhbh1D +0.202 against +0.052). At 0.2 SOL a buy that step is 0.01 SOL, less than the bid that wins it half the time; at 3 SOL it is 0.17 SOL.
- The bots bid per task, not per crowd: 6Z91 0.009 SOL on 1.2 SOL buys, 0.029 on 2 SOL, 0.125 on 4 to 8 SOL, 0.2 to 3.4 behind Cupsey, Decu, Slingor; Fhbh1D 0.0011 on small buys and 0.35 behind Cupsey and Decu.
- 6Z91 pays with the priority fee (no tip on 87 % of 658 buys) and its sells cost 0.0001 SOL; bids of 0.03 to 0.15 SOL ate its whole result on 207 trades (−4.6 SOL after bids).
- 6Z91's exit: 40 % sold by 2 s, 65 % by 5 s, 98 % by 20 s, in pieces every 3 s; the whole position dropped at about 2 s when nothing happened (1,277 trades, −3.6 SOL); one trade in ten sells its first piece in the block it bought in.
- Followed wallets with their own sets (jidn, Flames, daumeneth, Gfree) put 2 to 4 companion buys ahead of every outsider: 6Z91 is "third or fourth" behind them and still the first outsider (61 % of 1,123 buys once companions are left out, 34 % before).
- New candidates of the same kind: dKMjeGJ4 (+558 SOL of cash in 7 days, bids 0.2), 2H6Dwrq (+304), 3cbuqqiT (+84 at a 0.001 bid), HmR7SxDo (+49), 88UqCRfa (first place 34 of 47 times at 0.004). 638PYXz5 bids the most (0.37, up to 6.8) and lost 197.

## What the tape cannot show

- **Fees before 11 Nov 16:34 UTC.** Six of the seven days have no bids. Every "after bids" number is from 24 hours.
- **Failed transactions.** A bot that sends several transactions for one buy pays the priority fee on the ones that
  fail too. Only landed trades are on the tape, so the bots' true fee bill is at least what is written here.
- **Fee per unit of compute.** The chain orders transactions by fee per unit of compute, and the tape holds the fee
  in SOL. Two bids close together say nothing about which one ranked higher.
- **Tips to services we do not know.** A tip counts only when it goes to a landing service on our list.
- **When a transaction was sent, and by which route.** The tape shows where it landed, not how fast it got there. The
  wallets that take the first place on a 0.001 SOL bid are faster or better connected; how is not visible.
- **Who owns what.** Whether jidn's four followers are jidn's own wallets or outside copiers, and whether 7iyYn4 and
  6Z91 run the same software (their exits look the same), cannot be read off trades.
- **Coins that leave the tape.** PumpSwap trades are kept for recently migrated coins only. 79 of 6Z91's trades and 95
  of Fhbh1D's are "open": most of their coins were sold and the last part was not seen. Counted as worthless they
  take 38 and 50 SOL off the totals (the table gives both).
- **Before 5 Nov.** The tape starts there. 7iyYn4 may have made money earlier; in these 7 days it did not.
- **The cause.** "First place makes +0.379 a trade" is what happened where the bot was first. It does not prove that
  buying the first place in another block makes the same.

## How it was measured

Read-only SQL on the server's tape through the signed ops endpoint (`sql`, 30 seconds and 500 rows a query, always on
an index), and small Python on a local copy. No job was started on the server and nothing was written to it.

- Every trade of the three wallets since 5 Nov 16:27 UTC (`trades INDEXED BY trades_user`): 27,038 + 146 + 6,809 rows.
- For each of their 7,113 buy blocks (a coin and a block), every trade of that coin in that block and the 3 before it
  (`trades_mint`), the coin's launch row, and the labels table (3,046 labels).
- A trade (round trip): from a buy with no coins held until 99 % of the coins bought are sold. Result = SOL from
  sells minus SOL into buys, pump.fun's fee on both sides. Bid = priority fee + tip of each transaction, once.
- The trigger: the coin's first buy in the block ahead of the bot's buy; "labelled" when a wallet of kind wallet or
  kol bought ahead of it; a create block is the block of the coin's launch row.
- Companions, as `analysis/companions.py` defines them: a wallet in the next 5 transactions behind the followed
  wallet on half its blocks (5 blocks at least), or in its block on half and on both sides of it a quarter of the
  time. The three bots are never counted as companions.
- The thesis test: every buy of 0.1 SOL or more by 57 followed wallets since 11 Nov 16:34 UTC (81 looked up, 57 had
  buys: the wallets labelled Cupsey, frank, Gake or Decu, the 18 KOLs, and the 45 labelled wallets the three bots
  follow most), with every trade of the coin in that block: 3,068 blocks. Outsiders = other buyers behind the wallet
  in its block, companions and our own wallets left out. Bids within 10 % of each other are left out of the pair
  count.
- Seconds = blocks × 0.214 (measured on these rows: 0.2140 to 0.2150 on each of the seven days).

The existing jobs cover parts of this and can repeat it on the server: `snipetech playbook <wallet>` (trigger, size,
exit, bid of one wallet), `leaders-of <wallet> --max-slots 0` (whom it follows), `place-bids` (place against bid for
the moments a rule follows).

## Next (proposals; nothing was started)

1. Hand `place-bids --hours 48 --json` to the server's analyst (idea I51) once two full days of bids exist (from
   about 14 Nov). The table above says what to expect: an even chance of the first place at 0.015 to 0.04 SOL.
2. Score the rule at the place a bid really buys: first outsider half the time, second otherwise, at 2 to 5 SOL and
   0.03 to 0.06 SOL a buy, with 6Z91's exit (pieces every 3 s, everything out at 2 s when flat). The lab's new
   sell-in-parts exit and the companions landing are both needed. A positive lower bound there is one yes/no for the
   owner: the size and the bid are both new.
3. Per-wallet tasks for the wallets where both winners pay up: Cupseyy `6DQAGJ`, Decu `4vw54B`, Gake Side
   `4Hq9Uk`, Slingor, cap. Half of what both bots make behind labelled wallets comes from a handful of such wallets.
4. Add the screen for first-place holders to discovery, and label `dKMjeGJ4…`, `HmR7SxDo…`, `3cbuqqiT…`,
   `88UqCRfa…`, `GM8tLq2R…`, `DDb4vLEk…` as candidates, `638PYXz5…` as the losing high bidder.
5. Take `7iyYn4` off any list of wallets to learn from, or keep it as the control.
