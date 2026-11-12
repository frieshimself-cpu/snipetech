# What a paper result at 10, 30 or 60 SOL may be read for

12 Nov 2026. Asked by the server's analyst the same day: "the first place at 30 SOL +3.9 a trade ... 208 SOL a day on
paper is a simulator upper bound I do not believe as it stands", and "the 60 SOL turn depends on how the simulator
fills a buy that ends a curve, which I do not trust". The owner's rule is "has to be profitable paper trade first",
and "1 to 30 isnt the limit". So a paper number at size has to mean something, or say that it does not.

Branch `wf/size-honesty`. Not deployed. Follows `2026-11-11-fill-model-at-size-design.md` (the replay model).

## The short answer

- **10 SOL**: can be paper-tested. 3 % of the big wallets' curve moments are left out. Our own buy moves the price
  51 % at the median, so the result is a range, and the lab now prints both ends of it.
- **30 SOL**: can be paper-tested on two thirds of the curve moments only. A third are left out, because the buy
  would end the curve or be most of what it still takes. On the rest our own buy nearly triples the price (+185 % at
  the median). The upper end of the range is not evidence by itself any more.
- **60 SOL**: cannot be paper-tested on a pump.fun curve at all. Every one of the 191 curve moments is left out. Only
  buys on PumpSwap (26 moments in 72 hours) can be scored, at +118 % own impact.
- **The "turn" at 60 SOL (+3.9 a trade at 30, -8.2 at 60) was not a market fact.** It was two faults of the
  simulator, both described below. Neither number should be quoted.

## The pools the big-wallet rule meets (server tape, read-only SQL)

Window: 9 Nov 18:10 to 12 Nov 18:10 UTC (72 hours). The rule: the first buy in a coin of 1 SOL or more by a wallet
of group `big` (30 wallets), once a coin. 227 moments: 191 on a pump.fun curve, 26 on PumpSwap, 10 on LaunchLab.
Cupsey's two wallets carry 163 of them (Cupseyy 85, CupseyFriend 78), then Gake Side 32, Cooker 19, Remus 5,
Dior 5 (two wallets), Gake side 2, Cupsey Side 2 one. "First buy" is the first inside the window, so a few are top-ups of a
position opened before it. The pool is read right after the wallet's buy, which is the pool our copy meets.

On the curve (191 moments, all on pump.fun's standard curve). SOL in the curve after the wallet's buy: 5.0 / 5.0 /
13.0 / 33.8 / 52.2 (10th / 25th / median / 75th / 90th percentile). SOL the curve still takes before it completes:
32.8 / 51.2 / 72.0 / 80.0 / 80.0.

| our buy | our coins as a share of the pool's (25th / median / 75th) | our own price move (median, 90th) | share of the SOL the curve still takes (25th / median / 75th) | would end the curve | more than half of what is left | left out |
|---|---|---|---|---|---|---|
| 0.2 SOL | 0.3 % / 0.5 % / 0.6 % | +1 %, +1 % | under 1 % | 0 | 0 | 0 |
| 2 SOL | 3.0 % / 4.4 % / 5.3 % | +9 %, +12 % | 2 % / 3 % / 4 % | 0 | 0 | 0 |
| 5 SOL | 7.2 % / 10.3 % / 12.4 % | +24 %, +30 % | 6 % / 7 % / 10 % | 0 | 0 | 0 |
| 10 SOL | 13.4 % / 18.7 % / 22.0 % | +51 %, +64 % | 12 % / 14 % / 19 % | 0 | 5 | 5 of 191 (3 %) |
| 30 SOL | 31.7 % / 40.8 % / 45.8 % | +185 %, +241 % | 37 % / 41 % / 58 % | 13 | 52 | 65 of 191 (34 %) |
| 60 SOL | 48.2 % / 57.9 % / 62.9 % | +465 %, +625 % | 74 % / 82 % / 116 % | 65 | 125 | 190 of 191 |

One more moment at every size: the wallet's own buy completed the curve, so there was nothing left to buy (see
"What it still leaves out"). That is why 60 SOL reads "every one".

On PumpSwap (26 moments). SOL in the pool after the wallet's buy: 90.6 / 97.1 / 124.8 / 170.3 / 1022.3.

| our buy | our coins as a share of the pool's (25th / median / 75th) | our own price move (median, 90th) | left out |
|---|---|---|---|
| 2 SOL | 1.2 % / 1.6 % / 2.0 % | +3 %, +4 % | 0 |
| 5 SOL | 2.8 % / 3.8 % / 4.8 % | +8 %, +11 % | 0 |
| 10 SOL | 5.5 % / 7.4 % / 9.2 % | +17 %, +23 % | 0 |
| 30 SOL | 14.9 % / 19.3 % / 23.4 % | +53 %, +76 % | 0 |
| 60 SOL | 25.9 % / 32.3 % / 37.9 % | +118 %, +174 % | 0 |

These shares come from SQL and a spreadsheet-style calculation (buy net of a 1.25 % fee, constant product), not from
a lab. The lab enters behind the wallet's companions, so its own counts will differ by a few moments. The commands
for the lab's own numbers are at the end.

## What was wrong, and what was decided

### 1. A buy that ends the curve

A pump.fun curve sells 793.1 million coins and no more. It completes when it holds 85.005 SOL. A buy that asks for
more gets what is left, pays only for that, and the coin moves to PumpSwap. The simulator did not know this. It sold
a 30 SOL buy into a curve holding 70 SOL more coins than the curve has, at prices no curve ever had, and then valued
the position against the tape's later curve trades, which with our buy in place could not have happened.

Two decisions, and when each applies:

- **(a) The fill is capped, everywhere.** `MarketPath.buy_fill` gives a buy what the curve has left and charges only
  what that costs. `prepare` uses it, so every caller gets the chain's fill at every size: the lab, the paper test,
  qualify, the replay of live fills. The position says so (`capped`, and `spent` is the smaller amount).
- **(b) The lab and the paper test do not score such a moment at all.** After a buy that ends the curve the tape's
  later curve trades are fiction, and no pricing rule mends that. The moment is left out with the reason
  `size_ends_curve` and counted. The same for a buy that is **more than half of the SOL the curve still takes**
  (`size_curve_fill`, constant `CURVE_FILL_MAX = 0.5`).

Why one half. Above it we are ourselves the larger part of the way to the migration. Every buyer the tape recorded
behind us then has less room on the curve than we took, and any run on the tape that brought in more SOL than ours
would have ended the curve on the way. At one half or less, the tape's later buyers still fit behind us for at least
as much SOL as our own. The rule reads only the buy against the pool it meets, known before anything after it. So it
picks no moment by how the coin went, and a live sniper can keep the same gate (OKTech's curve limits on a followed
buy). It is a judgement, not a law: the constant is named so it can be moved, and the lab reports the count.

### 2. The share of the pool

Once our coins are more than half of the pool's coins (`MAX_SHARE = 0.5`), the simulator stops pricing with the
replay model and prices a sale into the tape's pool as it is. That is a different rule, switched on in silence in the
middle of a position, and at the switch the position's value drops to a third at once. A stop-loss in the replay
then fires on a coin that was being bought.

This explains the 60 SOL "turn". At 60 SOL our coins are 58 % of the pool's at the median entry. So the fallback
rule was on from the first moment, the position showed an instant loss of most of its value, and the exits sold
into it. The -8.2 SOL a trade was that, not the market.

Decisions:

- A buy that takes **more than half the pool's coins at its entry** (more SOL than the pool holds) is left out
  (`size_pool_share`), on the curve and on PumpSwap.
- Where the switch happens later, during the hold, the score stays in the result and is **counted**. Each scored
  moment lists the exits whose score rests on a price outside the model (`outside`), each strategy says how many of
  its newer trades do (`outside_model`), what they add up to (`outside_model_sol`) and its mean without them
  (`inside_mean`).
- The same count covers a second case. With our coins out of the pool, the curve would have sold its last coin
  earlier than on the tape. From that moment the tape shows a curve that, with us in it, had already completed. And
  the PumpSwap pool a coin migrates into starts as the protocol makes it, not short of our coins, while the replay
  still counts it short. Such a score is marked `past_curve`. It is counted for positions of 1 % of the pool's coins
  or more (`SIZE_FLOOR_SHARE`): a 0.2 SOL buy is at most 0.66 % of a curve.

A trade counted there is not evidence either way. It is neither removed (the trades that reach those states are
mostly the winners, so removing them would bend the result) nor trusted.

### 3. The others' behaviour at size

The replay lets every later trader do what the tape recorded, however far our buy moved the price. About four in ten
curve buys ask for an exact number of coins. Beside our position the model lets each of them pay whatever those
coins now cost. Behind a 30 SOL buy that moved the price +185 %, that is nearly three times what they paid on the
tape, and that extra SOL is ours when we sell. On the chain such a buy fails once its cost passes the buyer's limit.
The tape does not record the limit. This is the main reason the 30 SOL number is too good.

No tape can say what the others would have done. It can be bounded and shown:

- **Our own price move**: each strategy row shows `own_impact_p50` and `own_impact_p90` (0.5 = +50 %) and
  `pool_share_p50`.
- **Both ends of the result.** `test.mean` is the upper end: every later buyer buys as on the tape.
  `crowd_free_mean` is the lower end: every win is replaced by our own round trip against the pool right after our
  buy (nothing a later buyer adds: it costs the two fees, the two transactions and the bid), every loss is kept. The
  truth lies between. `crowd_needed` is the share of what the later buyers added that must still come for the rule
  to break even: 0.2 is robust, 0.8 is hope. One more number a moment (`own_rt`), no second replay.
- **A tighter upper end, on request**: `lab --same-sol` replays every later buyer spending the SOL it spent on the
  tape and no more. A buyer of an exact number of coins gets fewer coins instead of paying more. It is a second lab
  of the same command, never above the first.

### 4. The size line

Every lab now carries `size`: per rule, the moments, how many were scored, how many were left out for size and why,
the median share of the pool, the median and 90th percentile of our own price move, how many scored moments rest on
a price outside the model, and one sentence (`line`) the analyst can repeat to the owner. For example:

> At 30 SOL our own buy moves the price 185 % at the median (241 % at the 90th percentile) and is 41 % of the pool's
> coins; 65 of 191 moments were left out for size (52 because it was more than half of the SOL the curve still takes,
> 13 because the buy would have ended the curve); 9 of the 126 scored rest on a price outside the model.

(The first numbers are the table's; the last count is made up, to show the form.) The analyst's tool text tells it
to quote that sentence with every result above 1 SOL, and that a rule with most moments left out cannot be
paper-tested at that size.

## What a result can and cannot be trusted for after this

Can be trusted:

- The fill: what our SOL buys, at any size, including at the end of a curve.
- That no scored moment is one where our own buy made the tape's later trades impossible at the entry.
- The lower end (`crowd_free_mean`): it is arithmetic, with no assumption about anyone.
- The counts: how much of the result rests on left-out moments and on prices outside the model.

Cannot be trusted:

- The upper end (`test.mean`) as a forecast, at any size where our own price move is large. It assumes nobody
  changes their mind because of us.
- Any size where most moments are left out. A few survivors are not the rule.
- A trade counted in `outside_model`.

A size can be proposed to the owner only when the range is positive enough to argue from: a positive lower bound of
the `--same-sol` lab, a low `crowd_needed`, few moments left out and few outside the model. Otherwise the honest
sentence is "this size cannot be shown on paper", and the next step is a small live measure, which is the owner's
call.

## What it still leaves out, said plainly

- **A position carried into a migration is still valued too high, at every size.** The replay keeps the pool short
  of our coins across the migration. The real PumpSwap pool starts with the protocol's fixed amounts. At size this
  is counted (`past_curve`), but the price is not corrected. At 0.2 SOL it is not counted and overstates a ride into
  a migration by up to 3 % of the position's value there (an early-curve 0.2 SOL buy holds 1.65 % of the pool's
  coins). The fix is to set the shortfall to zero at the migration and keep only what later sellers give back. It was
  not built here because it changes 0.2 SOL results, which this change had to leave untouched. It needs its own
  change, a new model name (`FILL_MODEL`) and a re-score.
- **A copy that lands after the followed buy completed the curve** is still filled at the completed curve's price.
  That stands in for a PumpSwap buy at the opening price: 6 % more coins than the pool would give at 30 SOL, 1 %
  fewer at 0.2 SOL. About 1 % of the big wallets' moments.
- **LaunchLab**: its curves end where each platform set them, which the tape does not say. Those buys are not
  capped. Only the half-of-the-pool rule applies.
- **The value drop at the half-of-the-pool switch** is still there. It is counted now, not removed: removing it
  would change what a 0.2 SOL position is worth in a pool that was drained.
- **Paper trades already kept** at 2 to 20 SOL were scored before these rules. `paper rescore` leaves a row that is
  now too large for its pool as it was and counts it (`size`). The forward run scores no such moment any more.
- **At 0.2 SOL** nothing changes, with one exception by design: a buy within 0.4 SOL of the end of a curve is now
  left out or capped. None of the fixtures has one.

## Where it lives

- `sim/market.py`: `PUMP_CURVE_END_Q`, `MarketPath.curve_room`, `buy_fill`, `leaves_model`, `same_sol`; the module's
  account of the three places the model ends.
- `sim/exits.py`: `prepare` (the capped fill; `asked`, `capped`, `ends_curve`, `curve_fill`, `pool_share`, `impact`
  on the position), `round_trip_pnl`, and `SimResult.capped` / `fallback` / `past_curve`.
- `analysis/lab.py`: `CURVE_FILL_MAX`, `ENTRY_POOL_SHARE_MAX`, `SIZE_FLOOR_SHARE`, `SIZE_WORDS`, `size_gap`, the row
  fields (`impact`, `pool_share`, `curve_fill`, `own_rt`, `outside`), `_size_report` (`size` in the lab's result),
  `_size_of` (`size` on each ranked strategy), `collect(..., same_sol=)`.
- `analysis/paper.py`: the paper run and `rescore` leave out what the lab leaves out.
- `cli.py` (`lab --same-sol`, `size` in the output), `report.py` (the "Size:" lines), `operator/tools.py` (SIZE
  CHECK in the analyst's tool text).
- No exit or trigger field changed, so every frozen paper rule keeps its id.

## Checks

`tests/test_size_honesty.py`, each against a brute-force pool or the curve's constants: the curve completes at
85.005 SOL; a buy larger than what is left gets exactly what is left and pays exactly what that costs (30 SOL into
70, 60 into 40, 0.2 into 84.9); a buy with room is filled bit for bit as before; PumpSwap, LaunchLab and hand-built
paths are never capped; the own price move and pool share; the round trip; the two flags fire at the state first
principles give; the lab's counts and medians on a synthetic world at 10, 30 and 60 SOL; the paper test leaves out
the same moments; the lab command prints the line; `--same-sol` against a brute-force pool in which nobody pays more
SOL than on the tape. Two pins: every exit of the lab's set on the real tape of the 12 live copies of 6 Nov at
0.2 SOL gives the same sum and the same hash of all 732 results as the code before this change, and two rule ids
are the ones the code before gave. The whole suite passes unchanged.

## To run on the server after the deploy

Each is one lab. Two run at a time. The analyst ran the first three forms earlier today, so the before numbers
exist (10 SOL first place +2.2 a trade over 108 trades; 30 SOL +3.9; 60 SOL -8.2).

```
snipetech remote --wait run lab --trigger '{"kind": "kol", "group": "big", "sol": 1}' --copy-position +0 --buy 10 --buy-bid 0.05 --hours 144 --json
snipetech remote --wait run lab --trigger '{"kind": "kol", "group": "big", "sol": 1}' --copy-position +0 --buy 30 --buy-bid 0.05 --hours 144 --json
snipetech remote --wait run lab --trigger '{"kind": "kol", "group": "big", "sol": 1}' --copy-position +0 --buy 60 --buy-bid 0.05 --hours 144 --json
snipetech remote --wait run lab --trigger '{"kind": "kol", "group": "big", "sol": 1}' --copy-position +0 --buy 10 --buy-bid 0.05 --hours 144 --same-sol --json
snipetech remote --wait run lab --trigger '{"kind": "kol", "group": "big", "sol": 1}' --copy-position +0 --buy 30 --buy-bid 0.05 --hours 144 --same-sol --json
snipetech remote --wait run lab --trigger '{"kind": "kol", "group": "big", "sol": 1}' --copy-position +0 --buy 2 --buy-bid 0.05 --hours 144 --json
```

What a good answer looks like:

- `size.triggers` has one entry for the rule, with `line`. At 2 SOL nothing is left out, or one or two moments at
  the very end of a curve, and the strategy numbers are those of today's 18:00 UTC lab (`20261112-180038-994402`)
  but for the tape that has moved on since (compare `moments` first).
- At 10 SOL a few percent of moments are left out. At 30 SOL about a third. At 60 SOL nearly all: the rule is then
  unranked or rests on a handful of PumpSwap moments, and the line says so.
- Each strategy's `size.crowd_free_mean` is negative and near minus 2.5 % of the buy, less the bid and two
  transactions: about -0.30 at 10 SOL, -0.80 at 30 SOL.
- The `--same-sol` mean is below the plain mean at the same size. The gap between them is what the "buyers pay
  whatever it costs" assumption was worth.
- If `outside_model` is a large part of `test.n`, read `inside_mean` beside `test.mean` and say both.
