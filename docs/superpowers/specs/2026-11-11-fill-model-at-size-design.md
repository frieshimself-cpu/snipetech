# What a large buy of ours is worth later: the simulator's model at size

11 Nov 2026. Found by the server's analyst (ideas I35 and I39, 22:31 and 23:01 UTC: "on 7 coins traded forward at both
2 and 5 SOL the returns are the same to within 0.7 points: the paper test shows almost no cost of size") and handed to
this session. Confirmed in the code and on the tape the same hour. Audit row 75.

## What is wrong

`sim/market.py` prices our own buy correctly: a 5 SOL buy gets fewer coins per SOL than a 2 SOL buy. After that it
values the position with one assumption: **the pool has, at every later moment, exactly our coins fewer than the tape
recorded** ("tokens held out of the pool"). That is what happens when every later trader deals in coins: a sale of
coins somebody already holds, or a buy that asks for an exact number of coins and pays whatever they cost. Such a
buyer pays more SOL because of us, and that extra SOL is credited to our position.

Most buyers do not buy that way. They spend an exact amount of SOL and take the coins it buys. With our buy in front
of them they get fewer coins, the pool keeps more of them, and the price we later sell at is lower than the model's.

On the tape (10-minute samples at the same hour, 5 to 11 Nov, every day alike): buys that spend exact SOL
(`buy_exact_quote_in`, `buy_exact_sol_in`) are 53 to 62 % of pump.fun curve buys and 43 to 66 % of their SOL; on
PumpSwap they are 85 to 98 %. Every buy on the tape carries its instruction kind (`trades.ix`).

What it does to a result, from the simulator's own code on a pump.fun curve (no fees, later buyers all exact-SOL):

| pool SOL at entry, at exit | our buy | simulator | later buyers spend the same SOL | overstated by |
|---|---|---|---|---|
| 50 to 60 | 0.2 SOL | +44.1 % | +43.7 % | 0.4 points (0.0008 SOL) |
| 50 to 60 | 1 SOL | +44.6 % | +42.5 % | 2.1 points |
| 50 to 60 | 2 SOL | +45.2 % | +41.1 % | 4.1 points (0.08 SOL) |
| 50 to 60 | 5 SOL | +46.9 % | +37.4 % | 9.5 points (0.48 SOL) |
| 50 to 53 | 1 SOL | +12.5 % | +12.0 % | 0.5 points |
| 50 to 53 | 5 SOL | +13.0 % | +10.7 % | 2.4 points |
| 40 to 80 | 0.2 SOL | +302 % | +296 % | 6 points (0.012 SOL) |
| 40 to 80 | 2 SOL | +321 % | +265 % | 56 points (1.13 SOL) |
| 60 to 50 (only sales after us) | any | the same | the same | nothing |

So: **winners are overstated, more with size and more with the size of the move; losers are priced right.** In the
simulator a bigger buy even returns slightly more per SOL than a smaller one (+46.9 % against +45.2 %), which is what
the analyst saw. Every lab, paper and qualify number is touched; at 0.2 SOL only the big runners matter (0.012 SOL on
a coin that quadruples), at 1 SOL and above every winner does. Results at 0.5 SOL and more are upper bounds until the
re-score: the KOL paper rules at 2 to 20 SOL on the owner's page, the size labs, and the "paid place behind a known
wallet at 1 to 5 SOL" lab (idea I51).

## The model that replaces it

The others do what they did, in the terms they did it in. For a position opened at state `e` holding `h` coins, the
simulator carries one number along the path: `E`, how many coins the pool is short of what the tape recorded. At the
entry `E = h` (the coins we took). At each later trade `j`, from the trade's own recorded amount and the reserves it
left (so a jump between trades, such as a migration or a boost, changes nothing):

- **a buy that spends exact SOL** (`ix` in `EXACT_IN`): the same SOL goes into the pool in both worlds. With `k` the
  reserves' product after the trade, `s` its SOL and `(q, b)` the reserves it left: the pool before it held
  `b_pre = k / (q - s)` coins on the tape and `b_pre - E` with us, so `q' = k / (b_pre - E) + s` after it and
  `E = b - k / q'`. `E` shrinks: the buyer got fewer coins than recorded. The shortfall is remembered for that wallet.
- **a sale by a wallet with such a shortfall**: it sells the same share of what it holds as on the tape (its recorded
  sale over its recorded balance), so that share of its shortfall comes back: `E` grows by it. A buyer who came after
  us and left before us changes nothing for us, as in reality. A wallet whose balance the tape does not know releases
  nothing (the careful side).
- **anything else** (a buy of exact coins, a sale by a wallet without a shortfall, a kind the tape does not name):
  the same coins move in both worlds and `E` stays.
- **our own sale** of `x` coins at a state: `E` falls by `x`.

Selling `x` of our coins at state `j` returns `k_j * (1 / (b_j - E_j) - 1 / (b_j - E_j + x))` before fees. With
`E = h` throughout this is the old formula, so a path with no exact-SOL buy after the entry, and every path built by
hand (no instruction kinds), prices exactly as before. `0 <= E <= h` always, so the new value never exceeds the old
one: the old model is the upper bound.

The guard against a position that is most of the pool (`MAX_SHARE`) stays, on `E`.

## What it still leaves out, said plainly

- A buy of exact coins fails when our buy pushes its cost past the buyer's limit. The tape does not record the limit,
  so those buys are assumed to go through and to pay more. That keeps a small optimistic rest for the third of curve
  buys of that kind.
- Nobody's decisions change: a trader who would have bought less, or not at all, at a higher price is replayed as on
  the tape. No replay of a tape can know that.
- `analysis/copytrade.py` (the wallet replay behind `follow`) still uses the old formula for its own running
  position. It is not what the lab, the paper test or qualify score with.

## Where it lives

- `sim/market.py`: `MarketPath.exact_in` (per state, from `Trade.ix`), `displacement(e, held, until=None,
  own_sells=())` (the series `E` from the entry on), `value_series(e, held, ...)` and `sell_at(...)` priced on it.
  Per path, once: wallet numbers and each sale's share of its wallet's recorded balance.
- `sim/exits.py`: `prepare` computes the position's `E` series up to a horizon after the entry (`DISPLACEMENT_SECS`,
  15 minutes: longer than any exit holds; `run_exit` extends it for an exit that holds longer) and keeps it on the
  `Prepared` position; every sale in `run_exit` is priced on it, a sale after one of our own on a series recomputed
  with that sale in it.
- No caller changes: the lab, the paper test, qualify, the deployer scoring and measure's exit replay all go through
  `prepare` and `run_exit`.

## Checks

- unit: a brute-force two-world pool in the test (every trade applied to a constant-product pool with and without our
  buy: exact-SOL buys spend the same SOL, exact-coin buys take the same coins, sales sell the same share of the
  wallet's holdings) against `displacement` and the values, on seeded random sequences, with and without our own
  partial sales; the closed form for "only exact-SOL buys after us"; "no exact-SOL buy" equals the old model bit for
  bit; a later buyer's round trip leaves `E` where it was; never above the old value; a reserve jump between trades;
  `MarketPath.build` reads the kinds; the horizon and its extension.
- the whole suite: paths built by hand carry no kinds, so every existing expectation must hold unchanged.
- on the server, before and after, with `--profile`: one lab's time (the walk is a loop per entry), and the same lab
  at 0.2, 1, 2 and 5 SOL under both models. Then the re-score: the paper rules' trades (the paper test keeps what a
  trade made under the code of its day: they are re-run from the tape), the qualify verdicts and the deployer scores
  follow at their next scheduled runs; the basis string of stored scores changes so no old score is read as new.
