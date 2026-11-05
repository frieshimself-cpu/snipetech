# The live recipe (forward-tested on paper, 2026-11-05)

All numbers are paper trades on market data recorded **after** each strategy was frozen, simulated at the
landing position OKTech was measured at (same slot as the trigger, ~1 trade in that coin ahead of us),
0.2 SOL per buy, fees and tips included (0.0035 SOL per transaction).

## The strategy

| | |
|---|---|
| Trigger | someone other than the dev buys **>= 2 SOL** of a pump.fun coin **within 1 s of its creation** |
| Coins | normal coins only — skip mayhem and holder-reward coins |
| Entry | right behind that buy, same slot (OKTech's measured speed) |
| Size | 0.2 SOL per buy (scale only after live results match paper) |
| Exit | stop-loss -15%, trailing stop 10% from the peak, sell everything after 300 s at the latest |

The >= 5 SOL version (trailing 18% after +20%) is a subset: fewer trades, similar per-trade result.

## Forward results

| Strategy (frozen 13:05 UTC) | Hours | Trades | Per trade | Lower bound | Win | Total |
|---|---|---|---|---|---|---|
| >= 2 SOL within 1 s, normal coins | 2.7 | 342 | +0.171 | +0.153 | 55% | +58.4 SOL |
| >= 5 SOL within 1 s, normal coins | 2.7 | 151 | +0.176 | +0.153 | 57% | +26.6 SOL |
| (same period, unfiltered >= 2 SOL) | 2.7 | 830 | +0.061 | +0.052 | 36% | +50.5 SOL |
| >= 2 SOL within 1 s, normal coins (later check) | 4.8 | 629 | +0.133 | +0.120 | 49% | +83.5 SOL |
| >= 5 SOL within 1 s, normal coins (later check) | 4.8 | 204 | +0.181 | +0.162 | 56% | +36.8 SOL |

How it was found: the original >= 5 SOL strategy (frozen 05:01) held for 7.8 h (+0.15/trade, n=308);
its profit was all in big buys within 1 s of the create (+0.31/trade) and on normal coins.

## Why paper is not live

* Paper assumes we land right behind the trigger in its slot; measured live: 0-1 trades behind at the
  launch, 1-5 behind on copies. At 3 trades behind the original strategies earned ~nothing.
* Paper does not model other bots reacting to our buys, failed transactions, or new coin types
  OKTech cannot trade yet (pump.fun error 6010 on "CreateV2 / SplitSupply" coins).
* The edge lives in one-off creators: dev-wallet lists cannot express it.

## What OKTech needs — solved

`hunt` with `triggerMode: "all"`, only `thresholds.minTotalSol` set, `timeWindowMs` = the launch window.
Verified live (2026-11-05 17:02 UTC): the buy landed in the create's own slot right after a 3.5 SOL buy
by another wallet. With `triggerMode: "any"` and a buyer-count threshold it fires at the first buy;
`crowdBuys` fires at the create. `snipetech oktech strategy --id a586d8164a` prints the config.

## Going live (once the trigger is solved)

1. One capped buy (totalSnipes 1, 0.05 SOL): verify on-chain that a >= 2 SOL non-dev buy came first, and that it sold.
2. Batches of 10 buys at 0.2 SOL, each audited against paper (trigger, landing position, exit, PnL).
3. Raise size / remove the cap only while live per-trade results stay within ~half of paper.
