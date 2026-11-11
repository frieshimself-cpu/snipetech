# The owner's two ideas of 10 Nov: buy the dip of a hot coin, snipe the coins that bond at once (11 Nov, 15:40 UTC)

**Questions (owner, 10 Nov):** "Coins with many eyes and volume that quickly go up and quickly go super low can be
bought for the bounce." And: "Coins that migrate and instantly bond because a boost bot buys into them pay."

**Answer: both lose where our buys land.** Buying the fall loses a quarter of the buy within 30 seconds. Waiting for
the price to turn finds a real bounce, about 2 % in 30 seconds, against a round trip that costs 6.7 %. Buying every
coin that migrates within a minute of its launch loses 7 % a trade.

Both ideas had waited a day untested. The server's agents had no tool for the first (the lab has no rule that fires
on a fall) and were never told of the tool for the second (audit of 11 Nov, row 62). The first is now the `dip-buys`
job; the second is a lab the analyst started by itself at 15:01 UTC.

All numbers: the server's tape, 144 hours to 11 Nov 15:10 UTC (137 hours for the lab), 0.2 SOL a buy, 0.00421 SOL a
transaction (measured), the lab's simulator: our own size moves the price, pump.fun's fee both ways, two transactions.

## Report

OUTCOME: nothing to freeze and nothing to trade. A hot coin's fall of 60 % keeps falling: 1,355 falls bought three
trades later lose 0.054 SOL a 0.2 SOL trade at 30 s (lower bound -0.058, 25 % win), every split negative. Bought only
once the price is 10 % off its low (920 of the 1,355 turn), the trade loses 0.0097 SOL at 30 s (lower bound -0.013,
36 % win): the bounce is about +2 % before costs and the costs are 6.7 %. A buy at the migration of a coin that
migrated within 60 s of its create (916 coins) loses 0.015 SOL a trade on 367 unseen trades (lower bound -0.019).

### Buy the dip (`dip-buys`)

An event, found in tape order with nothing known of what comes after: the first trade at which a coin's curve price is
60 % under its highest so far, when that high came with 40+ SOL in the curve and 100+ wallets (the creator not
counted), at most 10 minutes earlier. One event a coin. 29,096 coins crossed 30 SOL in the six days; 1,355 had such a
fall, about 226 a day. The buy lands three trades after the event's trade (where our copies land behind a buy they
react to), or at the last trade when fewer follow: every event counts, also the coin nobody trades again.

| rule | trades | sold after 30 s | after 2 min | after 10 min | win at 30 s |
|---|---|---|---|---|---|
| buy the fall at -60 % | 1,355 | -0.054 (bound -0.058) | -0.060 | -0.073 | 25 % |
| buy the fall at -80 % | 982 | -0.043 (bound -0.046) | -0.050 | -0.063 | 12 % |
| wait for +10 % off the low, then buy | 920 of 1,355 | -0.0097 (bound -0.013) | -0.020 | -0.040 | 36 % |
| the same, landing right behind the turn (no trade ahead of us) | 920 | -0.0042 (bound -0.008) | -0.015 | -0.035 | 41 % |
| the same at 1 SOL a buy, three trades behind (SOL a 1 SOL trade) | 920 | -0.014 (bound -0.033) | -0.063 | -0.163 | 41 % |

SOL a 0.2 SOL trade, after costs; "bound" is the one-sided 95 % lower bound. The five best trades make 12 % of what
the winners made at 30 s, so no handful of coins carries the table either way.

Splits of "buy the fall at -60 %", at 30 s: a fall that took under a minute -0.080 (716 events, 16 % win); one to five
minutes -0.026 (520); five to ten minutes -0.022 (119); a high of 60+ SOL -0.071 (483) against -0.045 under 60 (872);
200+ wallets -0.053 (928) against -0.056. Nothing in the splits turns it: the faster and the bigger the fall, the worse.

Splits of "wait for the turn", at 30 s: 100-199 wallets -0.003 (249 trades, bound -0.012); a high under 60 SOL -0.007
(610); every other group between -0.009 and -0.015. The best group still loses.

The 435 falls that never turned 10 % up within five minutes are no trade under the third rule; under the first they
are most of the loss.

Two checks close it. A place no bid of ours buys, right behind the trade that marks the turn, still loses 0.0042 SOL
a trade (bound -0.008): speed does not rescue it. And at 1 SOL a buy, where the two transactions weigh a fifth as
much, it loses 0.014 SOL at 30 s and 0.163 at 10 minutes: our own size moves a thin curve. One group comes out near
zero in both checks, the coins with 100-199 wallets (249 trades: +0.005 SOL at the perfect landing, bound -0.005;
+0.022 at 1 SOL, bound -0.024): no lower bound above zero anywhere.

### Snipe the coins that bond at once (lab job 20261111-150116-1d6c2e)

OKTech's migration sniper as the lab scores it: a buy on PumpSwap behind the first three trades after the migration,
only on coins that migrated within 60 s (or 20 s) of their create. 916 coins in 137 hours did (870 within 20 s): 6.7
an hour. The exit is picked on the older 60 % of them and judged on the newer 40 %.

| window | coins | exit picked | unseen trades | mean | lower bound | win |
|---|---|---|---|---|---|---|
| within 60 s | 916 | stop -20 %, trail 20 % after +10 %, 300 s | 367 | -0.0151 | -0.0194 | 43 % |
| within 20 s | 870 | stop -20 %, 300 s | 348 | -0.0141 | -0.0185 | 43 % |

Traded on every one it would lose about 3 SOL a day at 0.2 SOL a buy. Nearly all of these coins bond inside their
create (870 of 916 within 20 s): the curve is bought out by its own launcher's bundle, which then sells into whoever
bought the pool.

## Lessons

- A hot coin that has fallen 60 % from its high within 10 minutes keeps falling: bought 3 trades later it loses 0.054 SOL a 0.2 SOL trade in 30 s (n 1,355, bound -0.058, 25 % win); every split by size, wallets and speed is negative.
- After such a fall the price turns 10 % up in 68 % of the coins, and the bounce from there is about +2 % in 30 s before costs: a 0.2 SOL round trip costs 6.7 %, so buying the turn loses 0.0097 SOL a trade (n 920, bound -0.013).
- Neither speed nor size rescues the bounce: right behind the turn's trade it loses 0.0042 SOL a 0.2 SOL trade (n 920, bound -0.008), and at 1 SOL it loses 0.014 at 30 s and 0.163 at 10 min (n 920).
- A buy at the migration of a coin that migrated within 60 s of its create loses 0.0151 SOL a 0.2 SOL trade on unseen trades (n 367, bound -0.0194, 43 % win); within 20 s the same (n 348, -0.0141).
- 870 of 916 fast migrations happen within 20 s of the create: an instant bond is a launcher's own bundle, not a crowd.

## What was not tested

- The fall after the migration. `dip-buys` reads the curve; a coin that completed its curve and fell on its PumpSwap
  pool is not in it.
- The instant-bond coins one known wallet bought the last SOL of (the owner's "boost bot"). The lab scores all 916
  alike; the split by who filled the curve is the analyst's next step (SQL on the trades before each migration).
- A live executor. No OKTech mode buys a fall, so even a positive table would have needed a way to trade it.
