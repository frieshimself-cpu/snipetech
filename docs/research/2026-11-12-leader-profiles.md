# The wallets worth following, one by one: twelve profiles and a rule for each (12 Nov 2026, 19:00 UTC)

**Question (owner, 12 Nov):** an agent "solely to analyse that specific wallet": profile the wallets worth following
one by one, so each gets its own paper rule. And: "we cant copy a wallet that is copying because we are filled above,
we can learn from it." So no copy bot is proposed here (6Z91, Fhbh1D, 7iyYn4 and their kind): only the wallets they
follow.

**Answer.**

- **Following a leader pays in one place: the first place behind it.** Over the whole tracked list (2,658 wallets,
  10,391 first buys of 1 SOL or more in 7 days), a 2 SOL copy right behind the wallet made +0.037 SOL a copy in the
  lab (4,157 unseen copies, lower bound +0.032, a 0.03 SOL bid charged). Two places back it lost 0.068. Six places
  back, where our copies land at a 0.003 SOL bid, it lost 0.084.
- **Behind the best twelve the first place is worth 15 to 62 % of the buy within two seconds** (the price path, before
  costs), and +0.4 to +2.5 SOL on a 5 SOL copy in the lab. Two places back most of that is gone. The move is made
  inside the wallet's own block by the bots that buy 3 to 20 SOL right behind it.
- **Three kinds of leader came out, and they need three kinds of rule.**
  - *Pays without the first place:* Schoen, Dustxbt (and, on thin samples, cap and the wallet pair FczAVr / DVMkhi).
    Two and even six places back still pay. No fee war is needed. These are the rules to paper-test first.
  - *An auction for the first place:* Cupseyy, Decu, Cented, both 0xsolomon sets. The highest bidder stands first in
    55 to 94 % of the blocks. The place has a price and the price can be read off the tape.
  - *A race for the first place:* theo side, dv and, on 12 blocks, Jijo. The highest bidder is first in only 35 to 46
    % of the blocks, and the place is often held on a bid of 0.003 SOL or less. A bid alone does not buy it.
- **The five to paper-test first:** Schoen, Dustxbt, Decu, Cupseyy, Cented. One line each on why is under "The
  ranking"; the exact commands and what each answered today are under "The five rules as lab commands".
- **Two of the five pay in the lab at the landing we already have.** Schoen six places back, 2 SOL, a 0.02 SOL bid
  charged: +0.090 SOL a copy (122 copies in 7 days, lower bound +0.068). Dustxbt six places back, 2 SOL, a 0.01 SOL
  bid charged: +0.068 (86 copies, lower bound +0.040); two back +0.159. The other three pay only at the first place:
  Decu needs it one time in eleven at a 0.2 SOL bid, Cupseyy one in ten at 0.9 SOL on a 10 SOL copy, Cented one in six
  at 0.2 SOL.
- **Good by name, not worth a rule:** Dior, Cooker, Remus, frankdegods, ResellCalendar, Ansem, Gake's main wallet,
  CupseyFriend, Slingor. Why is under "Good by name, not worth following".
- **None of this is a forward paper test yet.** These are lab replays of the same 7 days the wallets and the gates
  were picked on, and Schoen and Dustxbt are the best two of 169 wallets: some of that is luck. The first place is
  only evidence once a bid is shown to buy it (rule 2 of this repo). Every rule below names its landing and its bid.
  Nothing was started, tested live or changed.

**Windows.** The tape from 5 Nov 18:12 to 12 Nov 18:12 UTC (7 days). Bids (priority fee plus tip) are on the tape only
since 11 Nov 16:34 UTC: every number about bids is from the last 25.6 hours, 3 to 51 blocks a wallet. GMGN was asked
on 12 Nov between 18:15 and 19:16 UTC (31 look-ups: 15 wallets and one name). One block lasts 0.2145 seconds on this
tape.

Earlier findings this builds on, without repeating them: [the three bots](2026-11-12-winning-bots.md) (whom they
follow, what a bid buys), [the same-block finding](2026-11-10-same-block.md), and [the stopped
wallets](2026-11-11-stopped-wallets.md) (one operator's wallets buying in consecutive transactions).

## How to read the numbers

- **A moment** is a wallet's first buy of a coin in the window, 1 SOL or more. Top-ups are left out.
- **Its own entry** is its buy and, in the same block, its companions' buys (the other wallets of the same operator,
  found by the rule of `analysis/companions.py`). Nobody lands between transactions sent together.
- **An outsider** is any other wallet that buys 0.1 SOL or more behind it. Our own wallets are left out.
- **A place.** "Right behind it" is the first place behind its own entry. "Two places back" is behind two outsiders'
  buys, "six places back" behind six, counted over its block and the next five blocks (about one second). A place is
  never scored cheaper than right behind the wallet.
- **The price path** is the coin's price at +1 s, +2 s, +5 s, +20 s, +60 s and +5 min over the price at the place. It
  is gross: before pump.fun's fees (2.5 % a round trip), before the buyer's own price impact, before the bid. Means
  count every coin between -100 % and +100 %, so one coin that went up tenfold does not make a wallet.
- **"A copy at a size"** gives two bounds after pump.fun's fees and the copy's own price impact. The low bound sells
  into the tape's pool as if our SOL had never been in it. The high bound is the simulator's upper bound. The lab's
  replay lies between them.
- **The lab** (`snipetech lab`) is the system's own replay: fees, own price impact, 0.00625 SOL a transaction, the
  exit it picks on the older 60 % of the moments. A lab row for one wallet is that wallet's copies with the group's
  exit. It is not out of sample for the wallet. In a group, a coin fires once, for the first group wallet into it. So
  a wallet's row in "the tracked list" holds only the coins where it was the first tracked wallet in (about half of
  Decu's moments). The lab's own measure of where our live copies land today is six places back ("+6").
- **"Break-even share"** is arithmetic on two lab rows, not a result: how often a copy must be first for the bid to
  pay, if it is two back the rest of the time. On rows without the bid: share = (bid - SOL two back) / (SOL first -
  SOL two back). On rows with the bid already charged: share = loss two back / (gain first + loss two back).

## What the lab says about the groups (7 days, every job of this study)

| lab (group) | landing | buy, SOL | bid charged | moments | unseen copies | SOL a copy | lower bound | wins | exit the lab picked |
|---|---|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 10391 | 4157 | +0.037 | +0.032 | 38 % | STALE1s TP+20%@50% SL-8% TRAIL10% hold300s |
| the tracked list | two back | 2 | 0.03 | 10387 | 4155 | -0.068 | -0.072 | 22 % | STALE1s TP+20%@50% SL-8% TRAIL10% hold300s |
| the tracked list | six back | 2 | 0.03 | 10385 | 4154 | -0.084 | -0.088 | 19 % | STALE1s TP+20%@50% SL-8% TRAIL10% hold300s |
| the tracked list | first place | 5 | 0 | 10382 | 4153 | +0.140 | +0.130 | 45 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the tracked list | two back | 5 | 0 | 10379 | 4152 | -0.105 | -0.114 | 29 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the tracked list | first place | 10 | 0 | 9960 | 3984 | +0.173 | +0.152 | 43 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the big names | first place | 2 | 0 | 297 | 119 | +0.683 | +0.628 | 79 % | TP+30% SL-20% hold120s |
| the big names | two back | 2 | 0 | 297 | 119 | +0.044 | +0.012 | 47 % | hold3s |
| the big names | six back | 2 | 0 | 297 | 119 | -0.017 | -0.047 | 39 % | hold3s |
| the big names | first place | 5 | 0 | 297 | 119 | +1.386 | +1.269 | 87 % | STALE1s TP+20%@25%/+50%@25%/+100%@25% SL-15% TRAIL18% hold300s |
| the big names | two back | 5 | 0 | 297 | 119 | +0.028 | -0.033 | 39 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the big names | six back | 5 | 0 | 297 | 119 | -0.171 | -0.242 | 28 % | STALE1s TP+20%@50% SL-15% TRAIL18% hold300s |
| the big names | first place | 10 | 0 | 297 | 119 | +2.186 | +1.985 | 82 % | STALE1s TP+20%@25%/+50%@25%/+100%@25% SL-15% TRAIL18% hold300s |
| the big names | two back | 10 | 0 | 297 | 119 | -0.114 | -0.230 | 36 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the big names | six back | 10 | 0 | 297 | 119 | -0.475 | -0.594 | 24 % | STALE1s TP+20%@50% SL-15% TRAIL10% hold300s |
| the 18 KOLs | first place | 2 | 0 | 791 | 317 | +0.215 | +0.164 | 56 % | STALE2s SL-15% hold300s |
| the 18 KOLs | two back | 2 | 0 | 791 | 317 | +0.036 | +0.017 | 40 % | TP+10%@35% SL-8% TRAIL10% hold300s |
| the 18 KOLs | six back | 2 | 0 | 791 | 317 | +0.006 | -0.014 | 30 % | TP+20%@35% SL-8% TRAIL10% hold300s |
| the 18 KOLs | first place | 5 | 0 | 791 | 317 | +0.377 | +0.331 | 60 % | TP+10%@35% SL-8% TRAIL10% hold300s |
| the 18 KOLs | two back | 5 | 0 | 791 | 317 | +0.052 | +0.007 | 38 % | TP+10%@35% SL-8% TRAIL10% hold300s |
| the big names, market cap up to 150 SOL | first place | 5 | 0 | 157 | 63 | +2.088 | +1.931 | 94 % | STALE1s TP+20%@25%/+50%@25%/+100%@25% SL-15% TRAIL18% hold300s |
| the big names, market cap up to 150 SOL | two back | 5 | 0 | 157 | 63 | +0.131 | +0.026 | 41 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the big names, market cap up to 150 SOL | first place | 10 | 0 | 157 | 63 | +3.441 | +3.171 | 90 % | STALE1s TP+20%@25%/+50%@25%/+100%@25% SL-15% TRAIL18% hold300s |
| the tracked list, its buy up to 3 SOL, market cap up to 80 SOL | two back | 2 | 0 | 6204 | 2482 | -0.028 | -0.033 | 32 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the tracked list, its buy up to 3 SOL, market cap up to 80 SOL | first place | 5 | 0 | 6210 | 2484 | +0.233 | +0.216 | 51 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the 18 KOLs, a second KOL in | two back | 2 | 0 | 67 | 27 | -0.013 | -0.040 | 37 % | hold5s |
| the 18 KOLs, a second KOL in | two back | 5 | 0 | 66 | 27 | -0.069 | -0.135 | 33 % | hold5s |
| the 18 KOLs, market cap up to 300 SOL (rule 1) | six back | 2 | 0.02 | 730 | 292 | -0.017 | -0.032 | 37 % | STALE1s TP+10%@50% SL-15% TRAIL18% hold300s |
| the 18 KOLs, market cap up to 300 SOL (rule 1) | two back | 5 | 0.02 | 730 | 292 | +0.027 | -0.005 | 42 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the tracked list, its buy up to 2 SOL, market cap up to 150 SOL (rule 2) | two back | 2 | 0.01 | 6440 | 2576 | -0.043 | -0.048 | 28 % | STALE1s TP+20%@50% SL-8% TRAIL10% hold300s |
| the tracked list, its buy up to 2 SOL, market cap up to 150 SOL (rule 2) | six back | 2 | 0.01 | 6435 | 2574 | -0.063 | -0.067 | 23 % | STALE1s TP+20%@50% SL-8% TRAIL10% hold300s |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | first place | 5 | 0.2 | 7959 | 3184 | -0.025 | -0.037 | 38 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | two back | 5 | 0.2 | 7955 | 3182 | -0.312 | -0.322 | 20 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |
| the big names, market cap up to 150 SOL (rule 4) | first place | 10 | 0.9 | 157 | 63 | +2.541 | +2.271 | 84 % | STALE1s TP+20%@25%/+50%@25%/+100%@25% SL-15% TRAIL18% hold300s |
| the big names, market cap up to 150 SOL (rule 4) | two back | 10 | 0.9 | 157 | 63 | -0.798 | -0.983 | 22 % | STALE1s TP+10%@50% SL-8% TRAIL10% hold300s |

Read: for the tracked list and the big names the sign flips between the first place and two back. For the 18 KOLs it
stays above zero two back (lower bound +0.017 at 2 SOL, +0.007 at 5 SOL) and is flat six back. A second KOL already in
the coin (67 moments) does not help. With a 0.2 SOL bid charged the tracked list loses even at the first place (-0.025
a 5 SOL copy on 3,184 unseen copies): a bid pays behind the few wallets worth it, not behind the list.

## The ranking

Ranked by what a paper rule can show at a landing a bid really buys, then by how often the wallet gives a moment.

| # | wallet | moments a day (7 days) | first place, +2 s, gross | two back, +5 s, gross | lab, 5 SOL: first / two back (copies) | first outsider's bid, SOL: 50 / 75 / 90 % (blocks) | highest bidder first | kind |
|---|---|---|---|---|---|---|---|---|
| 1 | Schoen `5hAgYC` | 16 (113) | +28 % | +8 % | +0.87 / +0.33 (132) | 0.014 / 0.24 / 0.49 (12) | 67 % of 9 | pays without the first place |
| 2 | Dustxbt `FzVQSz` | 20 (138) | +15 % | +12 % | +0.41 / +0.27 (84) | 0.0011 / 0.005 / 0.007 (7) | 57 % of 7 | pays without the first place |
| 3 | Decu `4vw54B` | 50 (349) | +38 % | +5 % | +1.59 / +0.07 (187) | 0.016 / 0.033 / 0.20 (51) | 62 % of 45 | auction |
| 4 | Cupseyy `6DQAGJ` | 21 (145) | +62 % | +7 % | +2.02 / +0.19 (134) | 0.31 / 0.85 / 2.72 (16) | 83 % of 12 | auction, expensive |
| 5 | Cented `CyaE1V` | 21 (147) | +38 % | +6 % | +1.42 / +0.06 (109) | 0.004 / 0.031 / 0.19 (35) | 55 % of 29 | auction |
| 6 | theo side `Bi4rd5` | 43 (304) | +36 % | -1 % | +1.52 / -0.01 (212) | 0.0015 / 0.015 / 0.031 (24) | 35 % of 23 | race |
| 7 | Jijo `4BdKax` | 10 (73) | +48 % | +0 % | +2.16 / -0.29 (28) | 0.009 / 0.034 / 1.26 (12) | 42 % of 12 | half a race, a miss costs |
| 8 | 0xsolomon `3GzBLp` (and `DEdEW3`) | 19 (136) and 11 (79) | +31 % and +21 % | +4 % and +1 % | +0.99 / -0.03 (93); +0.77 / -0.05 (49) | 0.034 / 0.44 / 0.45 (17); 0.033 / 0.14 / 0.20 (15) | 94 % of 17; 90 % of 10 | pure auction, slow coins |
| 9 | FczAVr and DVMkhi (one operator) | 15 (107) and 18 (124) | +31 % | +3 % (+13 % at 60 s) | not run (labelled `cand`) | 0.0011 / 0.0016 / 0.0018 (3); 0.002 / 0.059 / 0.094 (7) | too few | rides for a minute |
| 10 | cap `CAPn1y` | 4 (31) | +44 % | +2 % (+16 % at 60 s) | +2.00 / +0.04 (9) | 2.1 / 2.1 / 2.1 (3) | too few | rides for minutes, thin |
| 11 | Gake Side `4Hq9Uk` | 7 (48) | +42 % | +1 % | +1.31 / -0.16 (45) | 0.82 / 0.83 / 0.83 (3) | too few | auction, expensive, a miss costs |
| 12 | Slingor `5YRgrP` | 2 (16) | +61 % | -5 % | +2.49 / +0.31 (14) | 0.61 / 1.54 / 2.18 (4) | 3 of 3 | sells into its followers |

The five most worth a paper rule first:

1. **Schoen.** The only busy wallet whose buys pay six places back: +0.116 SOL a 2 SOL copy in the lab (132 copies,
   lower bound +0.086), +0.152 two back, +0.326 two back at 5 SOL. With the rule's gate and a 0.02 SOL bid charged:
   +0.090 six back (122 copies, lower bound +0.068). Its coins keep rising for a minute. It needs no fee war, so the
   paper test can run at the landing we already get.
2. **Dustxbt.** Two back pays almost as much as first: +0.114 SOL a 2 SOL copy (84 copies, lower bound +0.086), +0.266
   at 5 SOL. With the rule's gates and a 0.01 SOL bid charged: +0.159 two back and +0.068 six back (86 copies, lower
   bounds +0.130 and +0.040). Nobody behind it bid over 0.007 SOL in the day with bids.
3. **Decu.** The largest prize by count: 50 moments a day, the first place +38 % every day of the eight (+36 to +48
   %), +1.59 SOL a 5 SOL copy in the lab (187 copies). Nine of ten first outsiders bid 0.2 SOL or less. With a 0.2 SOL
   bid charged: +1.49 first, -0.15 two back (169 copies): one first place in eleven breaks even.
4. **Cupseyy.** The most valuable place on the tape: +62 % in one second, +2.02 SOL a 5 SOL copy and +3.40 a 10 SOL
   copy (134 copies). Two back is still positive in the lab without a bid (+0.19 at 5 SOL, lower bound +0.13). The
   place costs 0.3 to 2.7 SOL, so this is a rule for 5 to 10 SOL, not for 2. At 10 SOL with a 0.9 SOL bid charged,
   under 150 SOL of market cap: +4.61 first, -0.53 two back (90 copies): one first place in ten.
5. **Cented.** The same shape as Decu at half the count, with a cheaper place: half of the first outsiders bid 0.004
   SOL or less, three quarters 0.031 or less. +1.42 SOL a 5 SOL copy at the first place (109 copies). With a 0.2 SOL
   bid charged: +1.21 first, -0.23 two back (102 copies): one first place in six.

Why not theo side at rank 3, with 43 moments a day and +1.52 SOL a 5 SOL copy? Because the bid does not decide its
first place: on the day with bids the highest bidder was first in 8 of 23 blocks, and the place went to bids of 0.003
SOL and less. It is the next one to test, once a live test shows how often our sniper is first on speed.

The leaders are mostly different coins: Decu shares 23 of its 407 moments' coins with theo side, 13 with Cented, 23
with Cupseyy. Rules on them add up.

## The twelve profiles

Each profile: who it is, what it buys, what happens after its buy by place, the crowd and its bids, where following
pays and where it loses, the rule to paper-test, and how sure this is. "Needs: only this wallet" marks a rule that
wants a per-wallet knob. The lab has none yet (the main session adds it); until then the same trigger runs for the
wallet's whole group with `--by-wallet`, and the wallet's own row is read from the answer.

### 1. Schoen `5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM`

**Who.** On our list as the KOL "Schoen" (one of the 18). GMGN: 3,900 users follow it, 70,763 $ made in 7 days, 79,159
$ in 30 days, win rate 48 %. No side wallet by name, no companion on the tape. It pays 0.0055 SOL a buy to land (GMGN,
median of 51 buys).

**What it buys.** 16 followable first buys a day, 1 to 3 SOL, in coins 10 seconds to 2.5 minutes old at 55 to 124 SOL
of market cap. Our tape holds 482 of the 996 buys GMGN counts (48 %): the rest are PumpSwap pools of older coins and
other launchpads (GMGN's last buys name Meteora and bags). It holds 35 to 193 seconds and sells in one sale (91 %).

| price after its first buy (113 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +24.6 % / +21.7 % | +27.7 % / +24.1 % | +26.2 % / +21.8 % | +33.5 % / +28.6 % | +38.6 % / +39.1 % | +7.2 % / -12.3 % |
| right behind it: above +2.5 % | 96 % | 98 % | 91 % | 82 % | 78 % | 42 % |
| two places back: mean / median | +6.5 % / +5.5 % | +9.2 % / +7.5 % | +8.3 % / +5.9 % | +16.2 % / +8.5 % | +23.3 % / +18.8 % | -1.7 % / -23.9 % |
| two places back: above +2.5 % | 63 % | 68 % | 65 % | 62 % | 64 % | 36 % |
| six places back: mean / median | +1.9 % / +1.4 % | +4.5 % / +2.9 % | +3.7 % / +1.4 % | +11.9 % / +6.6 % | +19.4 % / +16.3 % | -5.1 % / -28.4 % |
| six places back: above +2.5 % | 42 % | 50 % | 47 % | 56 % | 60 % | 35 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the 18 KOLs | first place | 2 | 0 | 132 | +0.410 | +0.346 | 67 % |
| the 18 KOLs | two back | 2 | 0 | 132 | +0.152 | +0.123 | 58 % |
| the 18 KOLs | six back | 2 | 0 | 132 | +0.116 | +0.086 | 45 % |
| the 18 KOLs | first place | 5 | 0 | 132 | +0.869 | +0.803 | 92 % |
| the 18 KOLs | two back | 5 | 0 | 132 | +0.326 | +0.260 | 55 % |
| the 18 KOLs, a second KOL in | two back | 2 | 0 | 7 | +0.082 | +0.036 | 71 % |
| the 18 KOLs, a second KOL in | two back | 5 | 0 | 6 | +0.236 | +0.108 | 83 % |
| the 18 KOLs, market cap up to 300 SOL (rule 1) | six back | 2 | 0.02 | 122 | +0.090 | +0.068 | 60 % |
| the 18 KOLs, market cap up to 300 SOL (rule 1) | two back | 5 | 0.02 | 122 | +0.216 | +0.174 | 61 % |

**Where it pays, where it loses.** Two places back it pays in every size band, every age band and every market cap
band under 300 SOL (+5 to +15 % at 5 s, +18 to +46 % at 60 s). It loses in one split: when Schoen is the first tracked
wallet into the coin (22 buys): two back +4 % at 5 s and -7 % at 60 s. With one or more tracked wallets already in (91
buys) two back makes +4 to +14 % at 5 s and +24 to +41 % at 60 s. By day, two back at 20 s was positive on six of
eight days; the two negative days had five and four buys. Its 44 first buys past the curve (PumpSwap) make less: +7 %
two back at 5 s, nothing at 60 s.

**The rule to paper-test.**
`{"kind":"kol","group":"kol","sol":1,"max_mcap":300}`. Needs: only this wallet. A second gate, "a tracked wallet is
already in", cannot be written exactly today: `known` counts wallets of the rule's own group, and with the 18 KOLs it
leaves 7 copies (the lab at "a second KOL in": +0.08 SOL on 7 copies). Size: 2 SOL first, then 5. Bid: 0.02 SOL in the
tip, above half of the first outsiders' bids; the test is scored two and six places back, not first. Exit: ride. The
price two back is +8 % at 5 s, +16 % at 20 s, +23 % at 60 s and under zero at 5 minutes; Schoen itself sells at 94
seconds (median). So: hold up to 60 to 90 seconds, or sell on Schoen's sale, whichever comes first.

**How sure.** 113 curve moments and 132 lab copies in 7 days; bids on 12 blocks only. The lab row uses the KOL group's
exit, not one picked for Schoen. What would change my mind: the per-wallet lab with its own exit showing a lower bound
under zero six places back, or a week in which the 60-second rise is gone (it rests on Schoen's coins running, and 7
days is one regime).

### 2. Dustxbt `FzVQSzj8JJr6WMGqbUHzx2XH1KkrfxRrRPv6WcbbZmND`

**Who.** On the owner's tracker as "3 | Dustxbt" (four more wallets carry the name; none traded in the 7 days). GMGN:
671 followers, 6,347 $ in 7 days, 23,970 $ in 30 days, win rate 32 %. It is the first of a set of three: `85jmgE…` and
`9kXTVj…` (on our list as "winner +56 SOL/74") buy 1.3 SOL each right behind it in 154 and 155 of its 161 moments. A
copy lands behind all three (3.9 SOL).

**What it buys.** 20 followable first buys a day of 1 to 1.3 SOL, in coins 6 to 45 seconds old at 47 to 89 SOL of
market cap. Our tape holds 650 of GMGN's 1,496 buys (43 %); GMGN's last buys are mostly on the launchpad it calls
"float" and on pools it does not name. It sells in one sale after 25 to 119 seconds.

| price after its first buy (138 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +14.8 % / +11.6 % | +15.0 % / +12.4 % | +17.1 % / +12.8 % | +15.1 % / +10.8 % | +6.6 % / +1.7 % | -26.5 % / -43.4 % |
| right behind it: above +2.5 % | 82 % | 78 % | 73 % | 62 % | 50 % | 22 % |
| two places back: mean / median | +9.4 % / +6.3 % | +9.7 % / +7.6 % | +11.5 % / +8.6 % | +9.5 % / +6.3 % | +2.1 % / -2.4 % | -29.4 % / -46.2 % |
| two places back: above +2.5 % | 64 % | 66 % | 65 % | 59 % | 43 % | 20 % |
| six places back: mean / median | +3.6 % / +0.3 % | +3.9 % / +0.3 % | +5.9 % / +1.9 % | +4.3 % / +1.9 % | -2.6 % / -7.0 % | -32.3 % / -50.3 % |
| six places back: above +2.5 % | 41 % | 39 % | 49 % | 48 % | 45 % | 20 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 84 | +0.158 | +0.131 | 70 % |
| the tracked list | two back | 2 | 0.03 | 84 | +0.114 | +0.086 | 62 % |
| the tracked list | six back | 2 | 0.03 | 84 | +0.032 | +0.006 | 48 % |
| the tracked list | first place | 5 | 0 | 84 | +0.413 | +0.359 | 75 % |
| the tracked list | two back | 5 | 0 | 84 | +0.266 | +0.212 | 62 % |
| the tracked list | first place | 10 | 0 | 83 | +0.640 | +0.539 | 67 % |
| the tracked list, its buy up to 2 SOL, market cap up to 150 SOL (rule 2) | two back | 2 | 0.01 | 86 | +0.159 | +0.130 | 67 % |
| the tracked list, its buy up to 2 SOL, market cap up to 150 SOL (rule 2) | six back | 2 | 0.01 | 86 | +0.068 | +0.040 | 52 % |

**Where it pays, where it loses.** Two back pays +9 to +12 % at 1 to 5 seconds in every band with enough buys: its 1
to 2 SOL buys (126), coins up to 5 minutes old (133), market cap under 150 SOL (130), and on every one of seven days
(+1 to +20 %). Its larger buys (2 SOL and more) are 12 moments and its buys above 150 SOL of market cap are 8: too few
to say, so the rule leaves them out. It loses when held: two back is +2 % at 60 s and -29 % at 5 minutes. The first
place is not much better than two back (+17 % against +12 % at 5 s), because the buyers behind it are small: the two
most regular first-place holders buy 0.35 and 0.5 SOL.

**The rule to paper-test.**
`{"kind":"kol","group":"wallet","sol":1,"max_sol":2,"max_mcap":150}`. Needs: only this wallet. Size: 2 SOL, then
5 (the pool is small: the set's 3.9 SOL goes into about 45 SOL in the curve's price formula; the low bound of a 5 SOL
copy two back is -11 %, the lab says +0.27 SOL). Bid: 0.01 SOL in the tip. The highest bid seen behind it was 0.007
SOL (7 blocks, 41 bids, nine in ten under 0.0023). Exit: out in seconds, everything sold by 20 seconds.

**How sure.** 138 curve moments, 84 lab copies, but bids on 7 blocks only: "nobody bids here" is one day's reading.
Once someone copies it with size the place gets a price and the edge goes. What would change my mind: a first
outsider's bid above 0.03 SOL becoming usual, or the lab's two-back lower bound falling under zero with the bid
charged.

### 3. Decu `4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9`

**Who.** On the tracker as "Decu"; X @notdecu (104,051 followers). GMGN: 60,119 users follow it, 44,008 $ in 7 days,
302,550 $ in 30 days, win rate 59 %, tags kol and top_followed. Other wallets by name: a second "Decu" (`GQyqQa…`, 16
buys in 7 days), "Decubot" (silent) and "DecuCopytrade" (`7iyYn4…`, the losing copy bot of the owner's three, not
Decu's). No companion on the tape. It pays 0.0075 SOL a buy (GMGN, 73 buys).

**What it buys.** 226 buys a day on our tape, three quarters of them top-ups of 0.02 to 0.24 SOL. 50 followable first
buys a day of 1 to 3 SOL, in coins 6 to 54 seconds old at 54 to 107 SOL of market cap, at every hour but 06 to 12 UTC.
Our tape holds 1,586 of GMGN's 3,456 buys (46 %). It sells in one sale (86 %) after 23 to 112 seconds, and made +302
SOL on 419 finished trades.

| price after its first buy (349 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +38.3 % / +33.5 % | +38.1 % / +34.9 % | +40.2 % / +36.7 % | +33.9 % / +35.1 % | +22.1 % / +18.8 % | -13.0 % / -37.2 % |
| right behind it: above +2.5 % | 97 % | 96 % | 95 % | 80 % | 59 % | 28 % |
| two places back: mean / median | +2.2 % / +0.7 % | +2.3 % / +1.4 % | +4.5 % / +2.3 % | +1.5 % / +1.6 % | -4.9 % / -11.4 % | -29.6 % / -56.4 % |
| two places back: above +2.5 % | 42 % | 45 % | 49 % | 48 % | 44 % | 21 % |
| six places back: mean / median | -1.5 % / -1.9 % | -1.2 % / -1.7 % | +1.0 % / -1.2 % | -1.7 % / -1.1 % | -7.8 % / -12.9 % | -31.3 % / -57.6 % |
| six places back: above +2.5 % | 25 % | 33 % | 42 % | 43 % | 40 % | 21 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 186 | +0.681 | +0.659 | 97 % |
| the tracked list | two back | 2 | 0.03 | 186 | +0.007 | -0.011 | 41 % |
| the tracked list | six back | 2 | 0.03 | 188 | -0.092 | -0.107 | 27 % |
| the tracked list | first place | 5 | 0 | 188 | +1.588 | +1.542 | 97 % |
| the tracked list | two back | 5 | 0 | 187 | +0.073 | +0.031 | 50 % |
| the tracked list | first place | 10 | 0 | 182 | +2.752 | +2.672 | 99 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | first place | 5 | 0.2 | 169 | +1.493 | +1.447 | 99 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | two back | 5 | 0.2 | 169 | -0.151 | -0.197 | 37 % |

**Where it pays, where it loses.** The first place pays in every split and on every day (+36 to +48 % a day, 25 to 70
buys a day). It is best under 80 SOL of market cap (+47 to +64 % at 5 s, 184 buys) and in coins under a minute old.
Two back is small and only for seconds: +5 % at 5 s, +2 % at 20 s, -5 % at 60 s. Two back is best when Decu's buy is 3
to 5 SOL (25 buys, +9 %) or the market cap is under 50 SOL (64 buys, +10 %), and it loses when held (-11 % at 60 s in
coins under 15 seconds old). Six places back makes nothing (+1 % at 5 s) and the lab says -0.09 SOL a 2 SOL copy.

**The crowd in one line.** 4 outsiders in its block at the median, 28 buyers within two seconds. The first place is
held by `8r6N8w…` (27 blocks, 7.9 SOL a buy; bids 0.40 and 0.99 on the two seen), `7aXsVQ…` (15), `4gtaks…` (14; 0.015
twice), 6Z91 (13; 0.005 once), Fhbh1D (8) and `638PYX…` (8; 0.66 and 1.57). The price two places back is already 33 %
above the price right behind Decu.

**The rule to paper-test.**
`{"kind":"kol","group":"wallet","sol":1,"max_sol":5,"max_mcap":150}`. Needs: only this wallet. Size: 5 SOL. Bid:
0.2 SOL in the tip, the 90 % mark of the first outsider's bid (51 blocks) and above the highest bid in its block in
three blocks of four (0.18). Exit: out in seconds. The first place is +38 % from 1 to 5 seconds, +34 % at 20 s, +22 %
at 60 s and -13 % at 5 minutes; Decu itself is out after 55 seconds (median). Sell everything within 20 seconds. The
rule's own lab run, bid charged (169 copies): +1.493 first, -0.151 two back. Break-even share: 0.151 / (1.493 + 0.151)
= one first place in eleven.

**How sure.** The largest sample here: 349 curve moments, 187 lab copies, 51 blocks with bids. What the tape cannot
say is how often OUR sniper is first at a 0.2 SOL bid: the highest bidder was first in 62 % of 45 blocks, and
`8r6N8w…` and `4gtaks…` take the place on speed as well. What would change my mind: `place-bids` over two more days
showing a 0.2 SOL bid first in fewer than one block in eight, or the lab's six-back row (-0.09) being where a 0.2 SOL
bid lands.

### 4. Cupseyy `6DQAGJT7VZPVBsuG4kn3AvpyHCEi7B2RFFvMZdbqQqqP`

**Who.** On the tracker as "Cupseyy", the active one of 12 wallets carrying Cupsey's name. In 7 days the others made 0
to 6 buys each, except "CupseyFriend" (`3z4vj1…`, 414 buys): that one buys in the create block of its own coins 82 %
of the time, a launch wallet, and the lab gives about nothing behind it (23 copies, -0.13 to +0.09 SOL). GMGN: 2,150
users follow Cupseyy, 13,242 $ in 7 days, -61,022 $ in 30 days, win rate 36 %. No companion on the tape. It pays 0.01
SOL a buy (GMGN) and bid 0.02 SOL on the buy shown under "How it was measured".

**What it buys.** 218 buys a day on our tape, 87 % of them top-ups of 0.1 SOL. 21 followable first buys a day on the
curve, 1 to 5 SOL (and up to 10), in coins 12 seconds to 2.3 minutes old at 55 to 163 SOL of market cap; none between
06 and 12 UTC. Another 44 first buys in 7 days were past the curve, on PumpSwap. Our tape holds 1,521 of GMGN's 4,562
buys (33 %). It holds 44 to 338 seconds, one sale (82 %), and made +328 SOL on 204 finished trades.

| price after its first buy (145 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +62.3 % / +60.0 % | +62.2 % / +60.4 % | +62.8 % / +63.0 % | +58.9 % / +60.7 % | +43.9 % / +52.8 % | +5.3 % / -13.5 % |
| right behind it: above +2.5 % | 98 % | 98 % | 97 % | 92 % | 79 % | 41 % |
| two places back: mean / median | +4.8 % / +4.1 % | +5.3 % / +4.2 % | +7.3 % / +3.0 % | +6.1 % / +2.8 % | -1.2 % / -3.2 % | -20.8 % / -50.0 % |
| two places back: above +2.5 % | 57 % | 54 % | 52 % | 50 % | 41 % | 30 % |
| six places back: mean / median | -1.0 % / +0.0 % | -0.6 % / -1.0 % | +1.1 % / -1.2 % | +1.4 % / -0.9 % | -5.4 % / -6.0 % | -23.2 % / -50.6 % |
| six places back: above +2.5 % | 32 % | 34 % | 38 % | 43 % | 39 % | 28 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the big names | first place | 2 | 0 | 134 | +0.911 | +0.861 | 89 % |
| the big names | two back | 2 | 0 | 134 | +0.118 | +0.086 | 56 % |
| the big names | six back | 2 | 0 | 134 | +0.017 | -0.009 | 44 % |
| the big names | first place | 5 | 0 | 134 | +2.020 | +1.917 | 95 % |
| the big names | two back | 5 | 0 | 134 | +0.192 | +0.134 | 54 % |
| the big names | six back | 5 | 0 | 134 | -0.124 | -0.180 | 33 % |
| the big names | first place | 10 | 0 | 134 | +3.395 | +3.213 | 92 % |
| the big names | two back | 10 | 0 | 134 | +0.240 | +0.126 | 51 % |
| the big names | six back | 10 | 0 | 134 | -0.263 | -0.368 | 32 % |
| the big names, market cap up to 150 SOL | first place | 5 | 0 | 90 | +3.253 | +3.122 | 100 % |
| the big names, market cap up to 150 SOL | two back | 5 | 0 | 90 | +0.277 | +0.189 | 52 % |
| the big names, market cap up to 150 SOL | first place | 10 | 0 | 90 | +5.506 | +5.274 | 100 % |
| the big names, market cap up to 150 SOL (rule 4) | first place | 10 | 0.9 | 90 | +4.606 | +4.374 | 99 % |
| the big names, market cap up to 150 SOL (rule 4) | two back | 10 | 0.9 | 90 | -0.533 | -0.699 | 29 % |

**Where it pays, where it loses.** The first place pays everywhere: +32 % behind its buys of 5 SOL and more, +85 %
behind its 1 to 2 SOL buys, +38 % even at 150 to 300 SOL of market cap. Two back pays +8 to +14 % at 5 s under 150 SOL
of market cap (103 buys) and nothing above it (42 buys: -1 to +1 %). Two back is best behind its 1 to 2 SOL buys (58
buys, +12 % at 5 s) and when two or three tracked wallets are already in (25 buys, +13 % at 5 s, +19 % at 60 s); it
loses when held with one tracked wallet in (23 buys, -19 % at 60 s). Six back is zero. Past the curve the first place
is worth +24 % and two back +11 % (44 buys): the pools are deeper, so the crowd moves the price less.

**The crowd in one line.** The most crowded wallet here: 29 buyers within two seconds, an outsider in its block 94 %
of the time. The first place is held by `4BHBSZ…` (17 blocks, 19.7 SOL a buy; bids 0.0005, 0.88 and 2.44 on the three
seen), 6Z91 (11, 14.8 SOL; 0.84 and 3.38), `3dHniL…` (6, 16.8 SOL; 0.01 and 3.00), `7ovu7s…` (6) and `8r6N8w…` (5,
13.8 SOL; 0.68). The highest bidder stood first in 10 of 12 blocks: this is an auction.

**The rule to paper-test.**
`{"kind":"kol","group":"big","sol":1,"max_mcap":150}`. It runs today: the group "big" is Cupseyy for 134 of its 297
moments, and `--by-wallet` gives Cupseyy's own row; a per-wallet knob makes it exact. Size: 10 SOL (5 as the second
arm). Bid: 0.9 SOL in the tip at 10 SOL, the 75 % mark of both the first outsider's bid (0.85) and the highest bid in
its block (0.91); 0.35 SOL at 5 SOL, just above the median. Exit: out in seconds; the first place holds +59 to +63 %
from 1 to 20 seconds and is +5 % (median -14 %) at 5 minutes. The rule's own lab run at 10 SOL, bid charged (90
copies): +4.606 first, -0.533 two back. Break-even share: 0.533 / (4.606 + 0.533) = one first place in ten. At 5 SOL
and 0.35, on the rows without the gate: (0.35 - 0.19) / (2.02 - 0.19) = one in eleven. At 2 SOL a 0.35 SOL bid needs
one first place in three: (0.35 - 0.118) / (0.911 - 0.118). 2 SOL is the wrong size here.

**How sure.** 145 curve moments, 134 lab copies, but bids on 16 blocks only, in one day. The bid marks (0.31, 0.85,
2.72) can move a lot with one more day. Today's live test (docs/live-recipe.md) landed first behind Cupseyy twice at a
0.035 SOL bid, but on PumpSwap top-ups with nobody else in the block: it says nothing about the crowded curve moments.
What would change my mind: two more days of bids showing the 75 % mark above 1.5 SOL, or a live or paper landing at a
0.9 SOL bid that is six back (the lab's six-back row at 5 SOL is -0.12).

### 5. Cented `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

**Who.** On the tracker as "Cented"; X @Cented7 (821,710 followers). GMGN: 64,670 users follow it, 37,871 $ in 7 days,
461,423 $ in 30 days, win rate 57 %. Five more wallets carry the name ("Cented REAL", three "cented", "cented
friend"); none traded in the 7 days. No companion on the tape. It pays 0.002 SOL a buy (GMGN, 80 buys).

**What it buys.** 159 buys a day on our tape, 83 % top-ups of 0.24 SOL. 21 followable first buys a day, mostly just
under 3 SOL, in coins 2 to 14 seconds old at 45 to 76 SOL of market cap, mostly 18 to 06 UTC (131 of 147). Our tape
holds only 1,111 of GMGN's 4,257 buys (26 %): GMGN's last buys include seven on Meteora. It sells after 12 to 108
seconds, 72 % in one sale, and made +200 SOL on 184 finished trades.

| price after its first buy (147 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +37.6 % / +35.1 % | +37.8 % / +35.0 % | +36.8 % / +32.2 % | +34.7 % / +31.9 % | +23.2 % / +16.7 % | -8.1 % / -28.8 % |
| right behind it: above +2.5 % | 94 % | 93 % | 89 % | 73 % | 57 % | 30 % |
| two places back: mean / median | +5.4 % / +3.6 % | +5.6 % / +3.2 % | +5.6 % / +1.8 % | +6.5 % / +2.9 % | +0.6 % / -11.8 % | -23.2 % / -47.7 % |
| two places back: above +2.5 % | 54 % | 52 % | 50 % | 51 % | 43 % | 24 % |
| six places back: mean / median | +1.8 % / +0.2 % | +1.9 % / -0.4 % | +2.0 % / -0.1 % | +3.3 % / -1.3 % | -2.5 % / -11.3 % | -25.2 % / -50.1 % |
| six places back: above +2.5 % | 35 % | 44 % | 45 % | 48 % | 39 % | 21 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 109 | +0.596 | +0.564 | 95 % |
| the tracked list | two back | 2 | 0.03 | 109 | +0.003 | -0.024 | 39 % |
| the tracked list | six back | 2 | 0.03 | 109 | -0.045 | -0.069 | 29 % |
| the tracked list | first place | 5 | 0 | 109 | +1.424 | +1.355 | 100 % |
| the tracked list | two back | 5 | 0 | 109 | +0.056 | -0.011 | 50 % |
| the tracked list | first place | 10 | 0 | 109 | +2.450 | +2.317 | 99 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | first place | 5 | 0.2 | 102 | +1.207 | +1.148 | 98 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | two back | 5 | 0.2 | 102 | -0.227 | -0.272 | 33 % |

**Where it pays, where it loses.** The first place pays under 150 SOL of market cap (+26 to +51 % at 5 s, 136 buys)
and not above it (+4 to +7 %, 11 buys). It is best when no tracked wallet is in yet (41 buys: +53 % first, +10 % two
back, +12 % two back at 20 s) and weakest when four or more are (25 buys: +24 % first, +2 % two back, -14 % at 60 s).
Two back makes +2 to +9 % at 5 s on six of seven days. Its 8 buys in a coin's create block are worth little (+15 %
first, -12 % two back): a copy lands a block later there.

**The rule to paper-test.**
`{"kind":"kol","group":"wallet","sol":1,"max_sol":5,"max_mcap":150}`. Needs: only this wallet. Size: 5 SOL. Bid:
0.2 SOL in the tip: the 90 % mark of the first outsider's bid (0.19) and of the highest bid in its block (0.20), on 35
blocks. Exit: out in seconds, everything sold by 20 seconds (first place +38 % at 1 to 2 s, +35 % at 20 s, +23 % at 60
s, -8 % at 5 minutes). The rule's own lab run, bid charged (102 copies): +1.207 first, -0.227 two back. Break-even
share: 0.227 / (1.207 + 0.227) = one first place in six.

**How sure.** 147 curve moments, 109 lab copies, 35 blocks with bids. The highest bidder was first in 55 % of 29
blocks: a bid helps and does not decide. Three quarters of what Cented does is not on our tape. What would change my
mind: the first place going to a no-bid wallet most of the time (then it is a race like theo side), or a paper landing
at a 0.2 SOL bid that is first less than one time in six.

### 6. theo side `Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt`

**Who.** On the tracker as "theo side"; X @theonomix. GMGN: 39,012 users follow it, 34,605 $ in 7 days, 201,080 $ in
30 days, win rate 65 %. Other wallets by name: "Theo" and "Theo Side" (silent), and "Theo dev" (`29yFze…`, 322 buys,
98 % in the create block of its own coins: a launch wallet, nothing to follow). No companion on the tape. It pays
0.0005 SOL a buy (GMGN).

**What it buys.** 43 followable first buys a day, nearly all 2.96 SOL, in coins 5 to 20 seconds old at 42 to 78 SOL of
market cap, around the clock. Our tape holds 878 of GMGN's 2,048 buys (43 %). It sells after 24 to 113 seconds.

| price after its first buy (304 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +37.2 % / +34.4 % | +36.3 % / +32.9 % | +34.3 % / +30.6 % | +23.8 % / +16.6 % | +9.1 % / -5.8 % | -9.8 % / -29.0 % |
| right behind it: above +2.5 % | 98 % | 96 % | 92 % | 67 % | 41 % | 27 % |
| two places back: mean / median | +1.1 % / -0.2 % | +0.3 % / -1.8 % | -0.8 % / -4.6 % | -6.7 % / -14.4 % | -15.6 % / -32.0 % | -28.9 % / -50.8 % |
| two places back: above +2.5 % | 35 % | 36 % | 31 % | 30 % | 26 % | 20 % |
| six places back: mean / median | +0.1 % / -0.0 % | -0.6 % / -1.2 % | -1.7 % / -4.9 % | -7.4 % / -14.8 % | -16.2 % / -31.2 % | -29.2 % / -50.7 % |
| six places back: above +2.5 % | 28 % | 33 % | 31 % | 28 % | 27 % | 20 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 214 | +0.651 | +0.634 | 99 % |
| the tracked list | two back | 2 | 0.03 | 213 | -0.017 | -0.034 | 33 % |
| the tracked list | six back | 2 | 0.03 | 213 | -0.059 | -0.074 | 24 % |
| the tracked list | first place | 5 | 0 | 212 | +1.522 | +1.484 | 100 % |
| the tracked list | two back | 5 | 0 | 212 | -0.006 | -0.046 | 37 % |
| the tracked list | first place | 10 | 0 | 210 | +2.595 | +2.527 | 100 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | first place | 5 | 0.2 | 205 | +1.353 | +1.315 | 100 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | two back | 5 | 0.2 | 205 | -0.204 | -0.243 | 30 % |

**Where it pays, where it loses.** Only the first place pays, and only for seconds: +36 % at 2 s, +24 % at 20 s, +9 %
at 60 s (median -6 %). Two back loses in every band: -1 % at 5 s, -7 % at 20 s, -16 % at 60 s; in the youngest coins
(under 15 s, 195 buys) -20 % at 60 s. No band with 30 or more buys makes more than +3 % two back at 5 s, which is no
more than pump.fun's fees.

**The crowd.** The first outsider's bid is small (median 0.0015 SOL, nine in ten under 0.031), yet the highest bidder
stood first in only 8 of 23 blocks. Over the week `8r6N8w…` was first 28 times with 6.9 SOL (none of those in the day
with bids, so its bid here is not known; behind Decu it bid 0.40 and 0.99), 6Z91 16 times (bids 0.0002 and 0.005 on
the two seen) and `3cbuqq…` 10 times (0.001 on the one seen). On the day with bids the place went to the fastest, not
to the highest bid.

**The rule to paper-test.**
`{"kind":"kol","group":"wallet","sol":1,"max_mcap":150}`. Needs: only this wallet. Size: 5 SOL. Bid: 0.04 SOL in
the tip (above the 90 % mark, 0.031). Exit: everything out within 5 seconds. Break-even share: (0.04 + 0.006) / (1.522
+ 0.006) = one first place in thirty-three, because a miss costs almost nothing in the lab without a bid (-0.006).
That arithmetic is why it is worth a test. The lab's six-back row is -0.059 at 2 SOL, and with a 0.2 SOL bid charged
two back is -0.204 (205 copies): keep the bid small here.

**How sure.** 304 curve moments, 212 lab copies, 24 blocks with bids. The open question is not the bid, it is speed.
What would change my mind: a paper or live landing that is never first here.

### 7. Jijo `4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk`

**Who.** On the tracker as "Jijo"; X @jijo_exe. GMGN: 64,933 users follow it, 7,789 $ in 7 days, 44,880 $ in 30 days,
win rate 53 %. No side wallet by name, no companion. It pays 0.004 SOL a buy (GMGN).

**What it buys.** 10 followable first buys a day of 1 to 5 SOL, in coins 8 to 100 seconds old at 61 to 140 SOL of
market cap, from 12 to 24 UTC (63 of 73). Our tape holds 270 of GMGN's 446 buys (61 %). It sells in one sale (90 %)
after 42 to 290 seconds.

| price after its first buy (73 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +47.3 % / +42.1 % | +47.7 % / +45.4 % | +44.8 % / +41.6 % | +40.5 % / +33.7 % | +30.0 % / +30.3 % | +7.4 % / -12.8 % |
| right behind it: above +2.5 % | 99 % | 97 % | 96 % | 84 % | 67 % | 44 % |
| two places back: mean / median | +0.9 % / +0.2 % | +1.8 % / -2.6 % | +0.1 % / -1.3 % | +2.8 % / -4.5 % | -2.4 % / -9.7 % | -15.7 % / -42.5 % |
| two places back: above +2.5 % | 40 % | 41 % | 40 % | 41 % | 40 % | 33 % |
| six places back: mean / median | -0.4 % / -0.6 % | +0.7 % / -1.1 % | -0.7 % / -3.5 % | +1.6 % / -7.3 % | -2.4 % / -12.0 % | -14.6 % / -42.2 % |
| six places back: above +2.5 % | 29 % | 33 % | 34 % | 37 % | 42 % | 33 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 29 | +0.962 | +0.881 | 93 % |
| the tracked list | two back | 2 | 0.03 | 28 | -0.129 | -0.199 | 21 % |
| the tracked list | six back | 2 | 0.03 | 28 | -0.150 | -0.223 | 21 % |
| the tracked list | first place | 5 | 0 | 28 | +2.158 | +1.982 | 93 % |
| the tracked list | two back | 5 | 0 | 28 | -0.292 | -0.459 | 25 % |
| the tracked list | first place | 10 | 0 | 27 | +3.721 | +3.443 | 96 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | first place | 5 | 0.2 | 27 | +2.058 | +1.897 | 96 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | two back | 5 | 0.2 | 27 | -0.416 | -0.591 | 22 % |

**Where it pays, where it loses.** The first place: +42 to +48 % for 5 seconds, +30 % at 60 s. Two back is zero on
average and depends on who is already in: with a tracked wallet in before it (57 buys) two back makes +2 to +15 % at 5
s; when Jijo is the first tracked wallet in (16 buys) two back loses 19 % at 5 s and 39 % at 60 s. Between 00 and 06
UTC (10 buys) two back loses 14 %.

**The rule to paper-test.**
`{"kind":"kol","group":"wallet","sol":1,"max_sol":5,"max_mcap":300,"known":2}`. Needs: only this wallet; `known`
then has to count tracked wallets of any group. Size: 5 SOL. Bid: 0.05 SOL in the tip, above three first outsiders in
four (0.034); one in ten bid 1.26 SOL or more. Exit: out within 20 seconds. Break-even share at 5 SOL and 0.05: (0.05
+ 0.292) / (2.158 + 0.292) = one first place in seven. A miss costs here.

**How sure.** Thin: 73 curve moments, 28 lab copies, 12 blocks with bids. The highest bidder was first in 5 of 12.

### 8. 0xsolomon: two sets of four wallets, `3GzBLpxTWWTPWwBYQ8Jgrz5XLUCg9aY6HiB3GEBUBAv6` and `DEdEW3SMPU2dCfXEcgj2YppmX9H3bnMDJaU4ctn2BQDQ`

**Who.** Two wallets on the tracker carry the name "0xsolomon" (X @0xsolomon on GMGN for `DEdEW3…`). GMGN: 1,942 and
3,619 users follow them; 6,595 $ and 5,796 $ in 7 days; win rates 45 % and 37 %. Each is one of four wallets that buy
1.5 SOL in consecutive transactions. `3GzBLp…` is the third of its set: `BNnN2M…` and `2pLuge…` buy ahead of it in 123
of 139 moments and `7Pteyy…` behind it in 121 (the set the [stopped-wallets study](2026-11-11-stopped-wallets.md)
found under `BNnN2M…`). `DEdEW3…` is the first of the other set: `fRECQz…`, `9s6jeG…` and `AwbjKL…` follow in 83, 80
and 75 of 85. The two sets share 38 coins but almost never at the same moment (3 within five seconds): two decisions,
perhaps one person. A copy lands behind the whole set, 6 SOL of buys.

**What they buy.** 19 and 11 followable moments a day. Old, small coins: 1 to 60 minutes old (median 6 and 15 minutes)
at 38 to 68 SOL of market cap. They hold long: the first sale comes after 7 and 15 minutes at the median. The tape
holds 61 % and 50 % of GMGN's buys.

The tables are for `3GzBLp…` (136 curve moments); `DEdEW3…` (79) is in the ranking table and in the lab rows below.

| price after its first buy (136 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +30.4 % / +30.5 % | +30.6 % / +30.7 % | +31.4 % / +30.6 % | +35.6 % / +29.9 % | +31.6 % / +25.7 % | +16.5 % / +7.1 % |
| right behind it: above +2.5 % | 94 % | 92 % | 90 % | 86 % | 76 % | 58 % |
| two places back: mean / median | +3.2 % / +4.1 % | +3.4 % / +4.4 % | +4.4 % / +4.3 % | +9.1 % / +3.7 % | +7.1 % / -0.9 % | -3.6 % / -13.3 % |
| two places back: above +2.5 % | 60 % | 60 % | 57 % | 54 % | 46 % | 32 % |
| six places back: mean / median | +0.9 % / +0.4 % | +1.0 % / +0.4 % | +2.1 % / -0.3 % | +6.7 % / +0.3 % | +4.9 % / -4.2 % | -5.7 % / -15.9 % |
| six places back: above +2.5 % | 35 % | 37 % | 40 % | 43 % | 43 % | 30 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 93 | +0.405 | +0.379 | 94 % |
| the tracked list | two back | 2 | 0.03 | 93 | -0.033 | -0.054 | 42 % |
| the tracked list | six back | 2 | 0.03 | 93 | -0.082 | -0.102 | 28 % |
| the tracked list | first place | 5 | 0 | 93 | +0.989 | +0.932 | 96 % |
| the tracked list | two back | 5 | 0 | 93 | -0.033 | -0.082 | 48 % |
| the tracked list | first place | 10 | 0 | 91 | +1.651 | +1.547 | 95 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | first place | 5 | 0.2 | 91 | +0.784 | +0.729 | 95 % |
| the tracked list, its buy up to 5 SOL, market cap up to 150 SOL (rules 3 and 5) | two back | 5 | 0.2 | 91 | -0.250 | -0.302 | 29 % |

`DEdEW3…`:

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 49 | +0.302 | +0.264 | 90 % |
| the tracked list | two back | 2 | 0.03 | 49 | -0.049 | -0.088 | 24 % |
| the tracked list | six back | 2 | 0.03 | 49 | -0.068 | -0.106 | 22 % |
| the tracked list | first place | 5 | 0 | 49 | +0.768 | +0.687 | 90 % |
| the tracked list | two back | 5 | 0 | 49 | -0.054 | -0.146 | 31 % |
| the tracked list | first place | 10 | 0 | 49 | +1.194 | +1.070 | 90 % |

**Where it pays, where it loses.** The first place makes +31 % and keeps it: +36 % at 20 s, +32 % at 60 s, +17 % at 5
minutes (58 % of the coins still above +2.5 %). Two back makes +3 to +4 % for 5 seconds and +9 % at 20 s on the price
path, and the lab calls it a small loss (-0.03 SOL at 2 and at 5 SOL). The set's rare 2 to 3 SOL buys lose (15 buys:
-2 % first, -22 % two back). Behind `DEdEW3…` everything is weaker: +21 % first, +1 % two back.

**The crowd.** A clean auction: the highest bidder stood first in 16 of 17 blocks behind `3GzBLp…` and 9 of 10 behind
`DEdEW3…`. The regular winner behind `3GzBLp…` is `2irjsc…` (16 blocks, 4.9 SOL a buy). On its eight first places with
a known bid it paid 0.44 SOL four times and 0.002 to 0.005 four times. First outsider's bid: 0.034 at the median, 0.44
at the 75 % mark.

**The rule to paper-test.**
`{"kind":"kol","group":"wallet","sol":1,"max_sol":2,"max_mcap":150}`. Needs: only this wallet (`3GzBLp…`). Size:
5 SOL. Bid: 0.5 SOL in the tip, above `2irjsc…`'s 0.44 and the 90 % mark (0.45). Exit: this is the one auction wallet
where the first place can be held: sell half within 20 seconds and the rest by 60 to 300 seconds. Break-even share at
5 SOL and 0.5: (0.5 + 0.033) / (0.989 + 0.033) = one first place in two.

**How sure.** 136 and 79 curve moments, 93 and 49 lab copies, 17 and 15 blocks with bids. One first place in two is a
high bar; it is only met if the auction is as clean as one day says.

### 9. FczAVr and DVMkhi, one operator: `FczAVrSD8WPAymBhWrcFy8QsRjgDqmFkuqWa1KkAbzuD` and `DVMkhiQe1D8yenuEgsW44NjRn9LfVQjGEpZcez5x7Mff`

**Who.** Both are on our list as "month-study pick I64": the server's analyst picked them, the owner did not name
them. No name on GMGN. `FczAVr…`: 913 followers, 16,346 $ in 7 days, 38,012 $ in 30 days, win rate 45 %. They buy the
same coin in the same block in 110 of the 118 coins they share; `FczAVr…` is ahead in 94. A third wallet (`H6V5Zj…`, 1
to 1.5 SOL) buys behind them in 56 to 60 moments. They are not copy bots: they hold 77 to 428 seconds and nobody is
ahead of them in the block.

**What they buy.** 15 and 18 followable moments a day, 1 to 5 SOL, in coins 30 seconds to 6 minutes old at 58 to 159
SOL of market cap. The tape holds 52 % of `FczAVr…`'s GMGN buys.

The tables are for `FczAVr…` (107 curve moments).

| price after its first buy (107 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +31.7 % / +22.4 % | +30.9 % / +21.7 % | +31.7 % / +23.2 % | +33.5 % / +28.9 % | +37.4 % / +34.0 % | +7.0 % / -7.8 % |
| right behind it: above +2.5 % | 95 % | 94 % | 91 % | 81 % | 76 % | 47 % |
| two places back: mean / median | +3.0 % / +2.3 % | +2.4 % / +1.7 % | +2.9 % / +0.3 % | +5.7 % / +1.5 % | +12.7 % / +9.2 % | -8.3 % / -37.5 % |
| two places back: above +2.5 % | 48 % | 46 % | 45 % | 48 % | 55 % | 37 % |
| six places back: mean / median | +0.9 % / +0.1 % | +0.3 % / +0.0 % | +0.9 % / -2.0 % | +4.1 % / -0.2 % | +10.9 % / +6.0 % | -9.7 % / -36.3 % |
| six places back: above +2.5 % | 33 % | 33 % | 37 % | 44 % | 57 % | 38 % |

No lab row: the two are labelled `cand`, and no lab of this study ran the group `cand`.

**Where it pays, where it loses.** One clear split. When their buy is 1 to 3 SOL (60 buys) the first place makes +36
to +56 % and two back +6 to +7 % at 5 s, +13 to +27 % at 60 s. When their buy is 3 to 5 SOL (45 buys) the first place
makes +11 % and two back loses 2 %. The same line by market cap: under 80 SOL (43 buys) two back makes +10 to +12 % at
5 s and +29 to +32 % at 60 s; from 80 to 150 SOL (46 buys) it loses. `DVMkhi…` shows the same split on its own 124
moments.

**The rule to paper-test.**
`{"kind":"kol","group":"cand","sol":1,"max_sol":3,"max_mcap":80}`. Needs: only these two wallets. Size: 2 SOL. Bid:
0.01 SOL in the tip (first outsider's bid 0.001 to 0.002 on 3 blocks behind `FczAVr…`; 0.002 to 0.09 on 7 behind
`DVMkhi…`). Exit: ride 60 seconds.

**How sure.** Not very: the paying split is 43 to 60 buys, the bids are 3 and 7 blocks, and there is no lab row. The
first thing to do is one lab with `--by-wallet` on the group `cand`.

### 10. cap `CAPn1yH4oSywsxGU456jfgTrSSUidf9jgeAnHceNUJdw`

**Who.** On the tracker as "cap". GMGN: 8,795 users follow it, 629 $ in 7 days, 17,251 $ in 30 days, win rate 32 %. No
side wallet by name, no companion. It pays 0.003 SOL a buy (GMGN).

**What it buys.** Few and large: 31 followable first buys on the curve in 7 days, 4 a day, 1 to 7 SOL and more, in
coins 2 to 20 minutes old at 86 to 199 SOL of market cap. Another 23 first buys were past the curve and 7 in a create
block. Our tape holds 384 of GMGN's 1,116 buys (34 %).

| price after its first buy (31 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +44.1 % / +33.8 % | +43.5 % / +34.4 % | +46.9 % / +38.2 % | +49.4 % / +37.7 % | +52.6 % / +48.1 % | +39.4 % / +43.3 % |
| right behind it: above +2.5 % | 100 % | 100 % | 97 % | 94 % | 90 % | 65 % |
| two places back: mean / median | -1.1 % / -1.6 % | -0.9 % / -1.3 % | +2.3 % / +2.9 % | +7.6 % / +4.0 % | +15.9 % / +3.9 % | +15.4 % / +21.6 % |
| two places back: above +2.5 % | 26 % | 29 % | 52 % | 52 % | 55 % | 55 % |
| six places back: mean / median | +1.4 % / +0.5 % | +1.4 % / +0.0 % | +4.8 % / +6.5 % | +9.4 % / +0.7 % | +18.9 % / +11.6 % | +17.0 % / +21.6 % |
| six places back: above +2.5 % | 32 % | 26 % | 55 % | 48 % | 65 % | 55 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 9 | +0.906 | +0.685 | 89 % |
| the tracked list | two back | 2 | 0.03 | 9 | -0.017 | -0.111 | 33 % |
| the tracked list | six back | 2 | 0.03 | 9 | +0.044 | -0.035 | 44 % |
| the tracked list | first place | 5 | 0 | 9 | +2.003 | +1.517 | 89 % |
| the tracked list | two back | 5 | 0 | 9 | +0.044 | -0.182 | 44 % |
| the tracked list | first place | 10 | 0 | 9 | +4.293 | +3.518 | 100 % |

**Where it pays, where it loses.** cap's coins keep going. The first place is +44 % at 2 s, +53 % at 60 s and +39 % at
5 minutes (65 % of the coins still above +2.5 %). Two back starts at -1 %, is +8 % at 20 s, +16 % at 60 s and +15 % at
5 minutes. Six back does the same (+19 % at 60 s). Few bots are in its block (two outsiders at the median). Past the
curve (23 buys) nothing: +9 % first, 0 % two back.

**The rule to paper-test.**
`{"kind":"kol","group":"wallet","sol":1,"max_mcap":410}` (410 SOL of market cap is where a curve ends). Needs: only
this wallet. Size: 2 SOL. Bid: 0.02 SOL in the tip; do not pay for the first place: the first outsider paid 2.1 SOL on
each of the three blocks with bids (6Z91 on one of them, for a 12.8 SOL buy). Exit: ride, 60 to 300 seconds.

**How sure.** Thin: 31 moments, 9 lab copies, 3 blocks with bids. A wallet to watch for two more weeks, not to size.

### 11. Gake Side `4Hq9UkSXjEwDZDBMAo9Lnri7VnbQCoomRAdrT6NGMh1c`

**Who.** On the tracker as "Gake Side", the only active one of six wallets with Gake's name ("Gake", "GAKE", "Gake
Vector" and "Gake sidewallet" made no buy in 7 days; "Gake side" `CNudZY…` made three of about 19 SOL each, none on
the curve). GMGN: 7,800 users follow it, 967 $ in 7 days, -9,001 $ in 30 days, win rate 39 %. No companion.

**What it buys.** 7 followable first buys a day of 1 to 5 SOL, in old coins (1.5 to 38 minutes) at 74 to 161 SOL of
market cap, from 00 to 18 UTC. It holds long (15 minutes at the median) and lost 12.7 SOL on its own 42 finished
trades. Our tape holds 83 of GMGN's 244 buys (34 %).

| price after its first buy (48 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +40.8 % / +39.3 % | +42.2 % / +40.9 % | +44.9 % / +43.0 % | +44.2 % / +43.2 % | +46.6 % / +44.7 % | +22.2 % / +18.4 % |
| right behind it: above +2.5 % | 96 % | 96 % | 94 % | 94 % | 92 % | 67 % |
| two places back: mean / median | -3.2 % / -1.7 % | -2.5 % / -1.3 % | +0.8 % / +1.4 % | -0.2 % / +0.2 % | +4.6 % / -3.3 % | -9.5 % / -18.1 % |
| two places back: above +2.5 % | 19 % | 25 % | 46 % | 48 % | 42 % | 25 % |
| six places back: mean / median | -4.8 % / -0.8 % | -3.8 % / -1.2 % | -0.1 % / +2.0 % | -0.9 % / +0.8 % | +3.1 % / +2.2 % | -9.9 % / -18.4 % |
| six places back: above +2.5 % | 10 % | 21 % | 48 % | 42 % | 46 % | 27 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the big names | first place | 2 | 0 | 45 | +0.714 | +0.637 | 93 % |
| the big names | two back | 2 | 0 | 45 | -0.098 | -0.161 | 36 % |
| the big names | six back | 2 | 0 | 45 | -0.158 | -0.215 | 31 % |
| the big names | first place | 5 | 0 | 45 | +1.311 | +1.125 | 93 % |
| the big names | two back | 5 | 0 | 45 | -0.164 | -0.301 | 27 % |
| the big names | six back | 5 | 0 | 45 | -0.374 | -0.506 | 24 % |
| the big names | first place | 10 | 0 | 45 | +2.185 | +1.851 | 93 % |
| the big names | two back | 10 | 0 | 45 | -0.528 | -0.815 | 24 % |
| the big names | six back | 10 | 0 | 45 | -0.919 | -1.155 | 13 % |
| the big names, market cap up to 150 SOL | first place | 5 | 0 | 34 | +2.233 | +2.058 | 100 % |
| the big names, market cap up to 150 SOL | two back | 5 | 0 | 34 | +0.079 | -0.067 | 35 % |
| the big names, market cap up to 150 SOL | first place | 10 | 0 | 34 | +3.749 | +3.439 | 100 % |
| the big names, market cap up to 150 SOL (rule 4) | first place | 10 | 0.9 | 34 | +2.849 | +2.539 | 100 % |
| the big names, market cap up to 150 SOL (rule 4) | two back | 10 | 0.9 | 34 | -0.847 | -1.117 | 18 % |

**Where it pays, where it loses.** The first place is +42 % and holds for a minute (+47 % at 60 s, +22 % at 5
minutes). Two back is nothing: -3 % at 2 s, +1 % at 5 s. The first place is weak behind its 3 to 5 SOL buys (15 buys,
+15 %) and at 150 to 300 SOL of market cap (17 buys, +16 %), strong under 150 SOL (31 buys, +51 to +96 %).

**The rule to paper-test.**
`{"kind":"kol","group":"big","sol":1,"max_sol":3,"max_mcap":150}`. Needs: only this wallet (in the group "big" it is
45 of 297 moments). Size: 10 SOL. Bid: 0.9 SOL in the tip: on the three blocks with bids the first outsider paid 0.82
to 0.83 SOL (6Z91 and `2H6Dwr…` among them; 6Z91 was first in 14 blocks of the week with 13.3 SOL a buy). Exit: the
first place can be held 60 seconds. Break-even share at 10 SOL and 0.9: (0.9 + 0.53) / (2.19 + 0.53) = more than one
first place in two.

**How sure.** 48 moments, 45 lab copies, 3 blocks with bids. On these numbers it is not worth the bid: a miss costs
0.16 to 0.53 SOL and the place costs what Cupseyy's does for a third of the prize count.

### 12. Slingor `5YRgrP3mjGzrzirYYN5HAQH19cTYREYwGxW6XRJQUzij`

**Who.** On the tracker as "Slingor". GMGN: 7,317 users follow it, tags top_dev and arbitrager. GMGN shows -2.1
million $ in 7 days on 1,913 buys, which reads like a coin maker's wallet, not a trader's result. Our tape holds 69 of
those buys (4 %).

**What it buys.** 16 followable first buys in 7 days, 13 of them 5 SOL and more (its buys: 10 SOL at the median, 29 at
the 90 % mark): its own buy moves the price 36 %. It sells fast: the first sale 4 to 15 seconds after the buy (10 % to
50 % marks). It made +136.5 SOL on 24 finished trades, 96 % wins.

| price after its first buy (16 curve buys), gross | +1 s | +2 s | +5 s | +20 s | +60 s | +5 min |
|---|---|---|---|---|---|---|
| right behind it: mean / median | +60.6 % / +59.7 % | +61.3 % / +66.8 % | +47.1 % / +44.4 % | +30.5 % / +13.7 % | +13.5 % / +1.3 % | -7.9 % / -11.7 % |
| right behind it: above +2.5 % | 100 % | 100 % | 81 % | 69 % | 50 % | 38 % |
| two places back: mean / median | +8.3 % / +2.7 % | +8.2 % / +3.4 % | -5.3 % / -7.9 % | -15.6 % / -24.4 % | -26.7 % / -38.8 % | -40.8 % / -41.2 % |
| two places back: above +2.5 % | 56 % | 50 % | 31 % | 31 % | 25 % | 12 % |
| six places back: mean / median | -3.3 % / +0.3 % | -4.1 % / +1.9 % | -17.2 % / -11.8 % | -21.3 % / -32.2 % | -30.9 % / -44.0 % | -43.8 % / -50.0 % |
| six places back: above +2.5 % | 44 % | 50 % | 12 % | 25 % | 25 % | 19 % |

| lab (group) | landing | buy, SOL | bid charged | its copies | SOL a copy | lower bound | wins |
|---|---|---|---|---|---|---|---|
| the tracked list | first place | 2 | 0.03 | 14 | +1.050 | +0.716 | 57 % |
| the tracked list | two back | 2 | 0.03 | 14 | +0.095 | -0.047 | 21 % |
| the tracked list | six back | 2 | 0.03 | 14 | -0.146 | -0.218 | 14 % |
| the tracked list | first place | 5 | 0 | 14 | +2.487 | +1.727 | 71 % |
| the tracked list | two back | 5 | 0 | 14 | +0.312 | -0.020 | 29 % |
| the tracked list | first place | 10 | 0 | 7 | +8.614 | +7.101 | 100 % |

**Where it pays, where it loses.** It sells into its followers. The first place is +61 % for two seconds, +47 % at 5
s, +31 % at 20 s, +14 % at 60 s. Two back is +8 % for two seconds and then -5 % at 5 s, -16 % at 20 s, -27 % at 60 s.
Six back loses from the start.

**The rule to paper-test.** None before the others. If ever:
`{"kind":"kol","group":"wallet","sol":5}`, only this wallet, 5 SOL, first place only, everything sold within
2 seconds, and the place cost 0.6 to 2.6 SOL on the four blocks with bids.

**How sure.** 16 moments. Enough to say what it is, not enough for a rule.

## The other wallets, one line each

300 labelled wallets (kinds wallet, kol, cand, smart) made 30 or more buys of 1 SOL or more in the 7 days. 291 were
read. Nine with more than 15,000 trades in the week were left out as machines (6Z91 among them), like the 4 too busy
to count at all (40,000 trades and more: `G2oZrd…`, `omegoM…`, `sssssD…`, `ssssswd…`). With the big names that made
fewer buys, 299 wallets had a moment. Of the 299: 47 follow (they buy behind another buy in the same block on half
their buys or more and are out within 30 seconds: copy bots and members of a set, never proposed), 30 buy in the
create block of the coin (developers, bundles, launch snipers: a copy lands a block later, nothing to follow), 3 are
known copy bots (`2H6Dwr…`, Fhbh1D, 7iyYn4), and 219 are none of these. Of the 219, 169 have 20 or more followable
moments, and for 85 of those the first place makes under 10 %.

The table: the 40 best of those 219 by first-place value a day, then the named wallets asked about. "A buy ahead of it
in its block" is the share of its moments where somebody bought the coin in the same block before it: at half and more
the wallet is the second wallet of a set (Art diddy bundle, found:EgYa22, `9kXTVj…` behind Dustxbt) or a copier that
holds longer than 30 seconds. Those lines are there to be read, not followed. "First place, out in seconds" is the
gross price path right behind the wallet (40 % sold at 2 s, 30 % at 5 s, 30 % at 20 s). "Last place of its block" is
the same exit bought behind every buyer of its block. Lab columns: SOL a 2 SOL copy and the number of copies, from the
tracked list (0.03 SOL bid charged), the big names or the 18 KOLs (no bid); "n/a" = in no group a lab ran.

| wallet | what it is | a buy ahead of it in its block | buys of 1 SOL+ that can be followed, a day | its buy, SOL | market cap, SOL | coin age | its first sale | first place, out in seconds | last place of its block | last place, held 60 s | outsiders in its block | lab: first place, 2 SOL | lab: two back |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| winner +59 SOL/106 `ardinR` | leads | 21 % | 34.3 (240) | 2.93 | 84 | 20 s | 29 s | +24 % | -1 % | -13 % | 4.1 | n/a | n/a |
| Art `CgaA9a` | leads | 49 % | 26.3 (184) | 1.8 | 60 | 56 s | 84 s | +29 % | +5 % | +1 % | 4.3 | +0.40 (57) | +0.03 (57) |
| Art diddy bundle `BsvjGu` | second of a set, or a slow copier | 59 % | 25.1 (176) | 1.73 | 63 | 56 s | 86 s | +29 % | +6 % | +3 % | 4.1 | +0.42 (31) | +0.06 (31) |
| Megga `H31vEB` | leads | 19 % | 24.3 (170) | 1.36 | 67 | 6 s | 22 s | +28 % | +1 % | -13 % | 4.6 | +0.15 (75) | -0.06 (75) |
| found:5Y5nsb `5Y5nsb` | leads | 49 % | 20.6 (144) | 1.46 | 49 | 26 s | 72 s | +32 % | +7 % | +4 % | 4.7 | +0.36 (52) | +0.03 (52) |
| found:EgYa22 `EgYa22` | leads | 48 % | 19.9 (139) | 1.47 | 49 | 23 s | 71 s | +31 % | +6 % | +3 % | 4.8 | +0.38 (41) | +0.07 (41) |
| month-study pick I64 `DVMkhi` | second of a set, or a slow copier | 69 % | 17.7 (124) | 3.42 | 119 | 94 s | 176 s | +34 % | +6 % | +15 % | 3.4 | n/a | n/a |
| dv `BCagck` | leads | 19 % | 26.6 (186) | 4.23 | 62 | 13 s | 23 s | +23 % | -0 % | -14 % | 3.8 | +0.41 (95) | -0.05 (95) |
| 0xsolomon `DEdEW3` | leads | 5 % | 11.3 (79) | 1.5 | 47 | 910 s | 892 s | +47 % | +4 % | +3 % | 6.2 | +0.30 (49) | -0.05 (49) |
| Flames `6aXFYX` | leads | 17 % | 36.0 (252) | 1.2 | 64 | 6 s | 20 s | +16 % | -2 % | -25 % | 3.9 | +0.07 (174) | -0.02 (173) |
| winner +57 SOL/207 `CzsxDQ` | second of a set, or a slow copier | 71 % | 28.4 (199) | 1.26 | 95 | 212 s | 45 s | +17 % | +4 % | +6 % | 3.4 | n/a | n/a |
| winner +56 SOL/74 `9kXTVj` | second of a set, or a slow copier | 99 % | 19.7 (138) | 1.3 | 66 | 16 s | 60 s | +23 % | +7 % | -1 % | 5.7 | n/a | n/a |
| Limfork.eth `BQVz7f` | leads | 19 % | 14.9 (104) | 1.63 | 69 | 55 s | 42 s | +28 % | -0 % | -5 % | 5.3 | +0.21 (28) | -0.06 (28) |
| Gfree `4yo9CU` | leads | 17 % | 13.4 (94) | 1.56 | 71 | 42 s | 104 s | +31 % | +5 % | +3 % | 6.1 | +0.40 (79) | +0.07 (79) |
| Seb `3BLjRc` | second of a set, or a slow copier | 53 % | 14.6 (102) | 2.08 | 84 | 18 s | 51 s | +25 % | +5 % | -5 % | 3.0 | +0.27 (45) | -0.06 (45) |
| winner +89 SOL/116 `3VUNtV` | leads | 6 % | 37.6 (263) | 2.95 | 71 | 219 s | 27 s | +12 % | +1 % | -3 % | 2.4 | n/a | n/a |
| Bean Side `VJSDW6` | second of a set, or a slow copier | 50 % | 15.1 (106) | 3.62 | 115 | 92 s | 88 s | +23 % | +4 % | +5 % | 3.7 | +0.12 (46) | -0.01 (46) |
| Limfork.eth side `Fyr3eV` | second of a set, or a slow copier | 92 % | 14.1 (99) | 1.63 | 74 | 60 s | 43 s | +22 % | -0 % | -7 % | 4.6 | n/a | n/a |
| winner +52 SOL/199 `7djZvJ` | second of a set, or a slow copier | 71 % | 21.4 (150) | 1.23 | 114 | 258 s | 42 s | +15 % | +3 % | -1 % | 3.5 | n/a | n/a |
| winner +69 SOL/204 `EqiFgy` | second of a set, or a slow copier | 54 % | 26.9 (188) | 1.94 | 141 | 332 s | 45 s | +12 % | +2 % | +1 % | 3.2 | n/a | n/a |
| winner +68 SOL/53 `3PwXa9` | leads | 16 % | 18.1 (127) | 1.96 | 44 | 54 s | 27 s | +17 % | +5 % | +1 % | 2.6 | n/a | n/a |
| The Doc `DYAn4X` | second of a set, or a slow copier | 50 % | 6.6 (46) | 3.73 | 120 | 162 s | 186 s | +37 % | +12 % | +10 % | 3.0 | +0.45 (11) | +0.06 (11) |
| winner +78 SOL/153 `DADDY9` | leads | 33 % | 43.1 (302) | 3.7 | 141 | 357 s | 16 s | +8 % | +2 % | +2 % | 2.3 | n/a | n/a |
| Kev `BTf4A2` | leads | 33 % | 36.4 (255) | 2.93 | 60 | 8 s | 14 s | +9 % | +4 % | -7 % | 1.9 | +0.05 (174) | -0.03 (174) |
| zrool `99i9uV` | second of a set, or a slow copier | 72 % | 21.4 (150) | 1.29 | 63 | 15 s | 34 s | +13 % | +3 % | -7 % | 3.0 | -0.06 (19) | -0.04 (19) |
| Zrool `CuEdRE` | second of a set, or a slow copier | 69 % | 20.9 (146) | 1.28 | 62 | 15 s | 30 s | +13 % | +2 % | -8 % | 3.1 | -0.05 (26) | -0.08 (26) |
| Zrool `GDiAmj` | second of a set, or a slow copier | 75 % | 21.0 (147) | 1.27 | 63 | 15 s | 32 s | +13 % | +2 % | -8 % | 3.1 | -0.07 (14) | -0.06 (14) |
| Lynk `CkPFGv` | leads | 15 % | 16.4 (115) | 3.05 | 87 | 47 s | 70 s | +16 % | +1 % | +4 % | 3.6 | +0.29 (73) | -0.04 (73) |
| Tom crime `6TAHDM` | second of a set, or a slow copier | 59 % | 20.7 (145) | 1.42 | 65 | 28 s | 54 s | +12 % | +2 % | +0 % | 3.1 | +0.02 (39) | -0.04 (39) |
| EustazZ `FqamE7` | leads | 19 % | 74.7 (523) | 1.45 | 56 | 74 s | 30 s | +6 % | +0 % | -5 % | 1.8 | +0.05 (161) | -0.05 (161) |
| CONNREO `9EyPAM` | second of a set, or a slow copier | 61 % | 4.0 (28) | 1.68 | 95 | 56 s | 100 s | +49 % | +17 % | +9 % | 7.6 | +0.27 (8) | +0.12 (8) |
| Trey `831yhv` | leads | 7 % | 9.6 (67) | 2.93 | 57 | 12 s | 63 s | +22 % | -5 % | -10 % | 3.5 | +0.45 (38) | -0.02 (38) |
| pr6spr `sAdNbe` | leads | 37 % | 10.1 (71) | 3.13 | 114 | 24 s | 64 s | +21 % | +4 % | -4 % | 4.8 | +0.04 (30) | -0.09 (30) |
| jidn `3h65Mm` | leads | 31 % | 16.9 (118) | 1.47 | 67 | 19 s | 33 s | +14 % | +2 % | -6 % | 3.2 | +0.17 (45) | -0.04 (45) |
| early-leader n38 +0.0251 `6SHf2w` | leads | 38 % | 14.7 (103) | 1.09 | 48 | 1 s | n/a | +15 % | +8 % | +10 % | 2.8 | n/a | n/a |
| TOM CRIME `D1CGEL` | second of a set, or a slow copier | 68 % | 16.3 (114) | 1.36 | 63 | 21 s | 64 s | +13 % | +2 % | +2 % | 3.1 | +0.03 (17) | -0.05 (17) |
| Ani Early `EjtQrP` | leads | 20 % | 14.6 (102) | 1.91 | 79 | 63 s | 83 s | +14 % | +3 % | -3 % | 3.6 | +0.11 (35) | +0.08 (35) |
| found:YARSci `YARSci` | second of a set, or a slow copier | 65 % | 17.9 (125) | 1.26 | 147 | 410 s | 42 s | +11 % | +1 % | +2 % | 3.2 | +0.00 (41) | -0.07 (41) |
| early-leader n16 +0.0866 `a16ZRR` | second of a set, or a slow copier | 81 % | 9.4 (66) | 3.67 | 116 | 22 s | 65 s | +18 % | +5 % | -2 % | 4.5 | n/a | n/a |
| ramsetx `71PCu3` | leads | 4 % | 10.0 (70) | 2.44 | 81 | 274 s | 113 s | +17 % | -1 % | -2 % | 3.3 | +0.36 (19) | +0.00 (19) |
| Chester `PMJA8U` | follows | 72 % | 23.9 (167) | 1.34 | 55 | 3 s | 21 s | +12 % | +2 % | -13 % | 3.3 | +0.03 (169) | +0.01 (169) |
| 3 | casino `8rvAsD` | second of a set, or a slow copier | 64 % | 3.4 (24) | 2.66 | 116 | 44 s | 101 s | +38 % | +16 % | +15 % | 5.2 | n/a | n/a |
| 3 | casino side `J898BL` | second of a set, or a slow copier | 77 % | 3.6 (25) | 2.3 | 109 | 46 s | 103 s | +36 % | +14 % | +11 % | 5.1 | +0.44 (4) | +0.32 (4) |
| month-study pick I64 `eeee4M` | leads | 12 % | 14.4 (101) | 1.23 | 76 | 301 s | 182 s | +12 % | +5 % | +5 % | 2.6 | n/a | n/a |
| month-study pick I64 `7x6AHo` | leads | 10 % | 8.1 (57) | 3.93 | 100 | 446 s | 300 s | +18 % | +5 % | +7 % | 4.2 | n/a | n/a |
| month-study pick I64 `9xoW7t` | leads | 19 % | 4.3 (30) | 5.52 | 252 | 216 s | 291 s | +23 % | +11 % | +18 % | 4.2 | n/a | n/a |
| Yugi `AQ46kf` | leads | 23 % | 31.1 (218) | 2.93 | 59 | 6 s | 45 s | +7 % | +3 % | -2 % | 1.8 | -0.01 (156) | -0.06 (156) |
| Beaver `GM7Hrz` | leads | 11 % | 2.1 (15) | 4.67 | 106 | 85 s | 133 s | +22 % | +7 % | +14 % | 4.2 | +0.22 (10) | +0.13 (10) |
| Dior New Wallet `D2wBct` | leads | 17 % | 1.3 (9) | 2.93 | 204 | 1344 s | 276 s | +27 % | +11 % | +12 % | 3.6 | +0.26 (8) | +0.09 (8) |
| Pengu `JAunzN` | follows | 59 % | 9.1 (64) | 1.33 | 62 | 4 s | 11 s | +8 % | +3 % | -19 % | 2.2 | +0.13 (84) | -0.01 (84) |
| BIg `6TbDFs` | leads | 25 % | 8.7 (61) | 3.91 | 96 | 26 s | 25 s | +7 % | +4 % | -3 % | 1.4 | +0.02 (32) | -0.04 (32) |
| Solanaswaggy `8DgVVt` | leads | 19 % | 12.1 (85) | 1.98 | 74 | 15 s | 30 s | +9 % | -0 % | -11 % | 2.3 | +0.09 (28) | -0.10 (28) |
| ColerDevs `99xnE2` | second of a set, or a slow copier | 97 % | 8.6 (60) | 1.22 | 79 | 8 s | 41 s | +10 % | -2 % | -17 % | 2.6 | +0.19 (30) | -0.05 (30) |
| ResellCalendar `DxM1hf` | leads | 0 % | 0.1 (1) | 8.28 | 295 | 31376 s | 280 s | +90 % | +5 % | +8 % | 1.0 | n/a | n/a |
| frankdegods - fomo `498g1r` | leads | 33 % | 0.0 (0) | 47.48 | 0 | n/a | 104 s | n/a | n/a | n/a | 0.0 | n/a | n/a |
| Dior `87rRds` | leads | 0 % | 0.0 (0) | 24.68 | 0 | n/a | 8224 s | n/a | n/a | n/a | 0.0 | +0.08 (10) | +0.10 (10) |
| Remus `BCrTEX` | leads | 33 % | 0.0 (0) | 11.58 | 0 | n/a | 1137 s | n/a | n/a | n/a | 0.0 | +0.02 (6) | +0.05 (6) |
| Gake side `CNudZY` | leads | 0 % | 0.0 (0) | 19.76 | 0 | n/a | n/a | n/a | n/a | n/a | 0.0 | n/a | n/a |
| CupseyFriend `3z4vj1` | create block | 43 % | 4.0 (28) | 5.0 | 64 | 18 s | 3 s | +6 % | +1 % | +1 % | 1.3 | +0.09 (23) | -0.10 (23) |
| MACXBT `ETU3Gy` | leads | 34 % | 8.3 (58) | 1.96 | 83 | 57 s | 52 s | +6 % | -0 % | -5 % | 2.5 | +0.04 (41) | -0.10 (41) |
| daumeneth `8MaVa9` | leads | 9 % | 2.7 (19) | 2.94 | 170 | 240 s | 54 s | +9 % | -4 % | -0 % | 3.8 | +0.05 (11) | -0.02 (11) |
| risk100x side `67Nwfi` | leads | 22 % | 14.7 (103) | 2.18 | 102 | 27 s | 34 s | +7 % | -1 % | -11 % | 2.7 | +0.05 (53) | -0.06 (53) |
| Yugi dev `8AcZBv` | create block | 50 % | 0.3 (2) | 2.96 | 78 | 258 s | 6 s | -9 % | -10 % | -21 % | 1.0 | n/a | n/a |
| Cooker `8deJ9x` | leads | 17 % | 1.9 (13) | 4.94 | 178 | 543 s | 6 s | +1 % | -2 % | -4 % | 1.8 | -0.22 (14) | -0.08 (14) |

Read: Gfree (KOL) is the next "pays without the first place" candidate (lab two back +0.07 SOL on 79 copies, lower
bound +0.04). Art and its second wallet, found:5Y5nsb and its second wallet found:EgYa22 are sets like Dustxbt with
+0.03 to +0.07 two back. dv is a race like theo side. `ardinRsN…` (found by our own winners job) is an auction like
Decu with a smaller prize (+26 % first, zero two back) and is in no group a lab ran. The two wallets named "winner +52
SOL/33" and "winner +57 SOL/34" (`5m1GCX…`, `ENGkTt…`) would look best of all (+70 to +76 %) and are copy bots: they
buy behind another buy in the same block on 70 to 74 % of their buys and sell after 5 seconds. The move is the
leader's they copy. They are in no table here.

## Good by name, not worth following

| wallet | what the 7 days show | verdict |
|---|---|---|
| Dior `87rRds…` (KOL) | 196 buys in 12 coins, all on PumpSwap, past the curve; 25 SOL a first buy, 11 first buys; lab 10 copies: -0.04 to +0.10 SOL at any place and size | deep pools and no crowd to sell into: nothing to follow |
| Dior New Wallet `D2wBct…` | 9 followable moments; lab 8 copies, positive at every place | too few to say; look again in two weeks |
| Cooker `8deJ9x…` | 13 followable moments, first place +1 %; lab 14 copies: -0.22 SOL a 2 SOL copy even first | loses at the first place |
| Remus `BCrTEX…` | 11 buys: 7 on Raydium LaunchLab, where our sniper does not buy, and 4 on PumpSwap; 12 SOL a first buy; lab 6 copies | none on the curve; too few to say |
| frankdegods | the main wallets made 0 and 1 buy; "frankdegods - fomo" 6 buys of 47 SOL (5 on PumpSwap, 1 on LaunchLab) | no activity to follow |
| ResellCalendar `DxM1hf…` | 2 buys | no activity |
| Ansem | GMGN lists two wallets under his X account (@blknoiz06). `AVAZvH…` is on our list as "fake Ansem (not a KOL)": it holds 0.03 SOL and has no trade on the tape. `GV6UUmNxz2RpKxmNAPadYKb7uQpszwqQAu3qLJxVdC52` is on none of our lists: 13,068 GMGN followers, 274 SOL, -155,444 $ in 7 days on GMGN; on our tape 5 buys in 7 days, 71 SOL in 2 coins, all on PumpSwap | nothing to follow on the curve; label the second wallet and look again |
| Gake's main wallets | "Gake", "GAKE", "Gake Vector", "Gake sidewallet": no buy in 7 days | only Gake Side trades (profile 11) |
| CupseyFriend `3z4vj1…` | 414 buys, 82 % in the create block of its own coins, sells after 2.6 s | a launch wallet; lab 23 copies about zero |
| Cupsey's other 10 wallets | 0 to 6 buys each | only Cupseyy trades (profile 4) |
| Slingor | profile 12: sells into its followers within seconds, 2 moments a day, 4 % of its buys on our tape | first place only, at 0.6 to 2.6 SOL of bid |
| Gake Side | profile 11: a miss costs 0.16 to 0.53 SOL and the place cost 0.83 SOL | more than one first place in two needed |
| Flames, jidn, daumeneth, Solanaswaggy, Yugi, BIg, MACXBT, Chester, ColerDevs, YARSci | 6Z91's and Fhbh1D's most followed by count: first place +6 to +16 % gross, lab first place -0.01 to +0.17 SOL a 2 SOL copy | too small a move for a 2 to 5 SOL copy; the bots follow them with 0.5 to 2 SOL at bids of 0.001 to 0.03 |

## The five rules as lab commands

Every `lab` line ran for this study exactly as written, on 12 Nov between 18:56 and 19:12 UTC, with the bid charged;
the answer it gave is under it. The `place-bids` lines are the form that ran once (the tracked-list trigger, 30 hours,
six wallets in one job); at 48 hours they are for 14 Nov, when two days of bids exist. Until the per-wallet knob
exists, the wallet's own row is read from `wallets` in the answer (`--by-wallet`). A paper result needs three things:
the rule at the place its bid buys with the bid charged, the same rule a place class worse, and `place-bids` for how
often that bid is first.

**1. Schoen** (the row of `5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM`):

```
snipetech lab --trigger '{"kind":"kol","group":"kol","sol":1,"max_mcap":300}' --by-wallet --copy-position +6 --buy 2 --buy-bid 0.02 --hours 168 --top 400 --json
snipetech lab --trigger '{"kind":"kol","group":"kol","sol":1,"max_mcap":300}' --by-wallet --copy-position +2 --buy 5 --buy-bid 0.02 --hours 168 --top 400 --json
snipetech place-bids --trigger '{"kind":"kol","group":"kol","sol":1}' --each --wallet 5hAgYC8TJCcEZV7LTXAzkTrm7YL29YXyQQJPCNrG84zM --hours 48 --json
```

This study: six back at 2 SOL +0.090 SOL a copy (122 copies, lower bound +0.068, 60 % wins); two back at 5 SOL +0.216
(lower bound +0.174). The whole KOL group under the same gate: -0.017 six back and +0.027 two back, so the result is
Schoen's, not the group's. A good answer on more days: Schoen's lower bound still above zero six back on 100 or more
copies. The `place-bids` line was not run for Schoen.

**2. Dustxbt** (the row of `FzVQSzj8JJr6WMGqbUHzx2XH1KkrfxRrRPv6WcbbZmND`):

```
snipetech lab --trigger '{"kind":"kol","group":"wallet","sol":1,"max_sol":2,"max_mcap":150}' --by-wallet --copy-position +2 --buy 2 --buy-bid 0.01 --hours 168 --top 400 --json
snipetech lab --trigger '{"kind":"kol","group":"wallet","sol":1,"max_sol":2,"max_mcap":150}' --by-wallet --copy-position +6 --buy 2 --buy-bid 0.01 --hours 168 --top 400 --json
snipetech place-bids --trigger '{"kind":"kol","group":"wallet","sol":1}' --each --wallet FzVQSzj8JJr6WMGqbUHzx2XH1KkrfxRrRPv6WcbbZmND --hours 48 --json
```

This study: two back +0.159 SOL a copy (86 copies, lower bound +0.130, 67 % wins); six back +0.068 (lower bound
+0.040, 52 % wins). The whole tracked list under the same gates: -0.043 and -0.063. `place-bids` (30 hours, run with
five more wallets): first outsider's bid 0.0011 SOL at the median on 10 moments. A good answer on more days: both
lower bounds still above zero, and first outsiders still under 0.01 SOL.

**3. Decu** (the row of `4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9`):

```
snipetech lab --trigger '{"kind":"kol","group":"wallet","sol":1,"max_sol":5,"max_mcap":150}' --by-wallet --copy-position +0 --buy 5 --buy-bid 0.2 --hours 168 --top 400 --json
snipetech lab --trigger '{"kind":"kol","group":"wallet","sol":1,"max_sol":5,"max_mcap":150}' --by-wallet --copy-position +2 --buy 5 --buy-bid 0.2 --hours 168 --top 400 --json
snipetech place-bids --trigger '{"kind":"kol","group":"wallet","sol":1}' --each --wallet 4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9 --hours 48 --json
```

This study: first place +1.493 SOL a copy after the bid (169 copies, lower bound +1.447, 99 % wins); two back -0.151
(lower bound -0.197, 37 % wins). Break-even share: 0.151 / (1.493 + 0.151) = one first place in eleven. `place-bids`
(30 hours, 62 moments): first outsider's bid 0.0162 SOL at the median, 19 % under 0.003. A good answer on more days:
`place-bids` showing a 0.2 SOL bid above the first outsider's in more than half the blocks, and then a paper landing
that is first more than one time in eleven.

**4. Cupseyy** (the row of `6DQAGJT7VZPVBsuG4kn3AvpyHCEi7B2RFFvMZdbqQqqP`; the group "big" is mostly this wallet):

```
snipetech lab --trigger '{"kind":"kol","group":"big","sol":1,"max_mcap":150}' --by-wallet --copy-position +0 --buy 10 --buy-bid 0.9 --hours 168 --top 400 --json
snipetech lab --trigger '{"kind":"kol","group":"big","sol":1,"max_mcap":150}' --by-wallet --copy-position +2 --buy 10 --buy-bid 0.9 --hours 168 --top 400 --json
snipetech place-bids --trigger '{"kind":"kol","group":"big","sol":1}' --each --wallet 6DQAGJT7VZPVBsuG4kn3AvpyHCEi7B2RFFvMZdbqQqqP --hours 48 --json
```

This study: first place +4.606 SOL a copy after the bid (90 copies, lower bound +4.374, 99 % wins); two back -0.533
(lower bound -0.699, 29 % wins). Break-even share: 0.533 / (4.606 + 0.533) = one first place in ten. The group as a
whole (63 unseen copies): +2.54 first, -0.80 two back. `place-bids` was run with the tracked-list trigger, not this
one (see "How it was measured" for why its bid marks behind Cupseyy are too low). A good answer on more days: a 0.9
SOL bid above the first outsider's in more than half of Cupseyy's blocks.

**5. Cented** (the row of `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`): the two lab lines of rule 3 give its rows
too.

```
snipetech place-bids --trigger '{"kind":"kol","group":"wallet","sol":1}' --each --wallet CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o --hours 48 --json
```

This study: first place +1.207 SOL a copy after the bid (102 copies, lower bound +1.148, 98 % wins); two back -0.227
(lower bound -0.272, 33 % wins). Break-even share: 0.227 / (1.207 + 0.227) = one first place in six. `place-bids` (30
hours, 38 moments): first outsider's bid 0.004 SOL at the median, 45 % under 0.003.

The same two lab runs as rule 3 also hold the rows of theo side (+1.353 first on 205 copies, -0.204 two back), Jijo
(+2.058 on 27, -0.416) and `3GzBLp…` (+0.784 on 91, -0.250), all with the 0.2 SOL bid charged.

What the main session has to add for exact rules: a per-wallet knob on the kol trigger (a list of addresses), and
`known` counting tracked wallets of every group (Schoen's and Jijo's best split). And one fix: `place-bids` should not
count a buy of dust as the first buyer.

## What the tape cannot show

- **How often our sniper is first at a given bid.** The whole value is in one place. The tape shows what others bid
  and who stood first, not what our transaction would have done. Only `place-bids` over more days and a live ladder
  test answer it.
- **Bids before 11 Nov 16:34 UTC.** One day of bids. Three to 51 blocks a wallet. The marks for Cupseyy (16 blocks),
  Gake Side (3), cap (3) and Slingor (4) are thin.
- **Failed and late transactions.** Only landed buys are on the tape. A bot that sends five transactions and lands one
  shows one bid.
- **Half to three quarters of each wallet's trading.** The tape holds 26 % (Cented) to 61 % (Jijo, 0xsolomon) of the
  buys GMGN counts, and 4 % of Slingor's. The rest is PumpSwap pools of coins that left their curve long ago, Raydium
  LaunchLab, Meteora and others. A wallet's GMGN profit is on all of it; our numbers are on the pump.fun part. The
  PumpSwap stream also lost the 12 hours of the server's outage (12 Nov 00:31 to 12:30 UTC).
- **Whether the others still buy behind us.** The first place is worth +38 % behind Decu because bots buy 4 to 8 SOL
  behind it. The path and the lab both assume they do the same with our copy ahead of them.
- **One regime.** Seven days. Schoen's and cap's rides rest on their coins running for a minute; that is the first
  thing a quieter market takes away.
- **Picked on the same days.** The twelve, their gates and their sizes were chosen on the 7 days they are scored on. A
  wallet's lab row is all of its copies, not unseen ones, and the best of 169 wallets looks better than it is. Only
  the forward paper test (rules frozen now, scored on the days after) says what is left.
- **Who owns what.** Sets are read off the order of buys (Dustxbt's three, 0xsolomon's two fours, FczAVr and DVMkhi).
  Whether `8r6N8w…` is an outside bot or sent by a leader's own software cannot be read off trades.
- **GMGN's own numbers.** Its fee fields carry no unit in its documents; they agree with our tape where both exist
  (Cupseyy: 0.01 SOL a buy on GMGN, 0.02 on the one buy shown below).

## New copy bots seen from behind the leaders

They hold the first place behind several of the twelve and are on none of our lists. Not proposed for following; to
label and to learn from. Counts are blocks of the 7 days. "Seen" is how many of those fell in the day with bids; "none
seen" means the bid is not known, not that there was none.

| wallet | first behind | its buy, SOL | its bid, SOL (seen) |
|---|---|---|---|
| `8r6N8wnuFwk44LKbmMkoQbFhKjpG1J3E94nKS9Ag3c4W` | Decu 27, theo side 28, ardinRsN 26, Cented 8, Cupseyy 5 | 5 to 14 | 0.33 to 0.99 (6: behind Decu, Cupseyy, ardinRsN); none seen behind theo side or Cented |
| `4BHBSZA96XCaepYLvmteV6wLqKTqJPyfkME941X5PqHP` | Cupseyy 17, Jijo 3, Slingor 1 | 12 to 33 | 0.0005, 0.88, 1.57, 2.44 (4) |
| `3dHniL84uuybxxDVatmnLoowdWChTGgRebHtWB5hGuvn` | Cupseyy 6, theo side 8, cap 2 | 10 to 17 | 0.01, 1.10, 3.00 (3) |
| `9ET25r6RLyYKwssaxVTv7Rvyy5UCWpDFv9uXEzFGQdVR` | Megga 18, theo side 9, Cented 8, Decu 7, dv 6, ardinRsN 6, Jijo 5 | 4 | 0.002 (1) |
| `777cXSXTw2XDW48Q8V9a1h4ca1ENpUfiDfndcWGZrrTZ` | Schoen 13, FczAVr 12, DVMkhi 11, found:5Y5nsb 6 | 2.5 to 10.8 | 0.0005 three times, 0.18 once (4) |
| `2irjscdyZaqCukg9bcutqDES1FwXifyctuUARirHqV85` | 0xsolomon `3GzBLp…` 16 | 4.9 | 0.44 four times, 0.002 to 0.005 four times (8) |
| `6cMeT5j3jPm1eTTya3kSbayy3SUtnFzM2iJWLN4WoFNc` | dv 32 | 2 | none seen |
| `7ovu7sqYQGcHwUnLfVfwjCx5VGBCiJmBrsrBv2ab1Ltx` | ardinRsN 9, Cupseyy 6, Jijo 4 | 4.9 | 0 and 0.002 (2) |
| `4gtaksjHVZgXAeu7nCg95NhdE42ewEWhEB5enaCcvqUZ`, `7aXsVQrEmpqPYrqPwgrPHpRHb83FL1qPJxo8xyjgQe9c` | Decu 14 and 15 | 4 | 0.015 twice (2); none seen |

## How it was measured

Read-only: SQL on the server's tape through the signed ops endpoint (30 seconds and 500 rows a query, always on an
index) into a local cache, 31 GMGN look-ups through the server's `gmgn` job, and read-only lab jobs, one at a time.
Nothing was written to the server.

- `tools/leader_profiles.py` does all of it and can redo it: `count` (every labelled wallet's 7-day buys), `pull --top
  300` (trades and price paths of the 299 wallets), `blocks W..` (every trade of the coin in the wallet's block and
  the next five, and the coin's history, for the wallets profiled in full), `rank`, `profile W..` (JSON) and `report
  W.. --gmgn <folder>` (the tables of the tables file). Set `SNIPETECH_OPS_URL`, `SNIPETECH_OPS_KEY` (the key's path)
  and `LEADER_DB` (the cache file). The whole pull took about 20 minutes with three readers.
  `tests/test_leader_profiles.py` holds the parts that decide a number: places among outsiders, companions, finished
  trades, the sized bounds.
- A moment's price path: the coin's last trade at or before 5, 9, 23, 93, 280 and 1,400 blocks after the wallet's
  block (0.2145 s a block). Market cap = price times a billion coins.
- Companions: `analysis/companions.py`'s rule on the moments of 1 SOL or more (the module itself uses 0.1 SOL): a
  wallet in the next five transactions behind it on half its moments, or in its block on half and on both sides of it
  a quarter of the time. The known copy bots are never companions.
- The crowd: outsiders' buys of 0.1 SOL or more behind the wallet in its own block. "Buyers in 2 seconds" counts every
  buyer, companions too. The bid of a buy is its priority fee plus its tip.
- Cross-check with the system's own job: `place-bids --each` over 30 hours gives the same first outsider's bid behind
  Decu (0.0162 SOL, 62 moments), Cented (0.004, 38), Dustxbt (0.0011, 10) and `3GzBLp…` (0.033, 19). Behind Cupseyy it
  says 0.034 SOL (19 moments) where this study says 0.305 (16 blocks). The difference is dust: `place-bids` counts a
  buy of 0.00 SOL that lands right behind Cupseyy as the first buyer. Such a buy moves no price and is no competitor,
  so here a buy under 0.1 SOL takes no place. `place-bids` should leave dust out too.
- One of Cupseyy's blocks as an example (slot 468,556,288, 12 Nov): Cupseyy buys 9.78 SOL (bid 0.02) at 304 SOL of
  market cap; 6Z91 is next with 7.9 SOL and a 0.84 SOL bid, the cap is 355; seven more buys of 0.2 to 2 SOL at bids of
  0.0002 to 0.015 take it to 407 within the block; three sells bring it back to 368 before the block ends.
- 35 lab jobs and one `place-bids` job, 18:13 to 19:12 UTC, one at a time (all labs `--hours 168 --by-wallet --json`,
  161 hours of tape): the tracked list at 2 SOL first, two back and six back (0.03 SOL bid), at 5 SOL first and two
  back and at 10 SOL first; the big names at 2, 5 and 10 SOL at the three places; the 18 KOLs at 2 SOL at the three
  places and at 5 SOL first and two back; gated variants; and the five rules as written above. A lab's "unseen copies"
  are the newest 40 % of its moments.
- The helper's tests: two (dust buys, the sized bounds) were written first and seen to fail; the others were written
  right after the code they check.

## Next (proposals; nothing was started)

1. Add the per-wallet knob to the kol trigger and let `known` count every tracked wallet; then freeze the paper rules
   from the commands above, each at the landing its bid buys. Schoen and Dustxbt first: six places back is where the
   lab measures our copies landing today (at a 0.003 SOL bid), and both lower bounds are above zero there at 2 SOL.
2. Hand `place-bids --each --wallet <the five> --hours 48` to the server's analyst from 14 Nov, when two days of bids
   exist: the first-place share of a 0.2 SOL bid behind Decu and Cented, and of 0.9 SOL behind Cupseyy, is the one
   number every auction rule waits for.
3. Run one lab on the group `cand` with `--by-wallet` for FczAVr, DVMkhi and `ardinRsN…`.
4. Label the copy bots of the table above (`8r6N8w…`, `4BHBSZ…`, `3dHniL…`, `9ET25r…`, `777cXS…`, `2irjsc…`,
   `6cMeT5…`, `7ovu7s…`) and the companions found here (Dustxbt's `85jmgE…` and `9kXTVj…`; `H6V5Zj…`).
5. Label Ansem's second wallet (`GV6UUm…`, from GMGN's `who Ansem`) and settle whether `AVAZvH…` is his (GMGN says so,
   our label says fake). Expect nothing on the curve from Dior, Cooker, Remus and frankdegods: on the curve the group
   "big" is Cupseyy and Gake Side.
6. Run the same profiles again in a week (`tools/leader_profiles.py`): cap, Jijo, Gfree and Dior New Wallet are the
   thin ones that could move up.
