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

## What OKTech needs — an account approval (5 Nov, 19:46–20:20 UTC)

Three capped live runs from the SNIPER wallet (0.2 SOL × 3 fills, then 0.02 SOL × 3 and × 4), 1.903 → 1.688 SOL:

| run | trigger as sent | fills | had the >= 2 SOL non-dev buy before us | result |
|---|---|---|---|---|
| 19:46 | `hunt` all / minTotalSol 2 / 1000 ms | 3 | 2 of 3 (both holder-reward coins) | -0.13 SOL incl. ~10 failed buys |
| 19:58 | same, no seed account, preexec | 3 | 0 of 3 (1.29, none, 0.27 SOL) | -0.04 |
| 20:06 | `crowdBuys` minSigners 2 / minTotalQuote 2 | 4 | 0 of 4 (create tx carried 0.99, 0, 0, 1.98 SOL) | -0.04 |

Both fired about every 10 s, which is the launch rate, not the trigger's 76/h. So with `monitorProgram` on, the sniper
buys every launch, and `hunt` / `crowdBuys` are signal modules, not entry filters. The 17:02 "verified" hunt trade was a
coincidence. What was learned on the way:

* Auto-sell works as configured (stop -15%, trailing 10%, 300 s timer): sells at 9 s, ~100 s and exactly 300 s observed.
  The twin `6023 NotEnoughTokensToSell` next to each landed sell is OKTech's duplicate send losing the race (fee only).
* Buys that fail on-chain with `6010 AccountTypeNotSupported` are sent anyway (~10 per batch, ~0.001 SOL each) and count
  toward `totalSnipes`; `preexec` did not stop them.
* A capped sniper can finish with a position still open (4siK, 20:04): `snipetech sweep` is our own sell for that case
  (needs `SNIPER_SECRET` in `/etc/snipetech/env`).

Resolved from the OKTech web app's own code (oktech.me bundle, 20:20 UTC): **Hunt is the gate** — its help text is
"SniperHunt: buy only once other buyers cross your thresholds on a new coin", `minTotalSol` = "SOL other buyers must have
put in before you buy", `minUniqueBuyers` = "Distinct buyer wallets needed", `maxDevBuySol` = "Disqualify the launch when
the creator bought more than this with it", `minCpiBuyers` = "buys that came through router programs". But the feature
list reads **"[S] tab (Shapes, Web, Hunt): approved users only"**, as do "Local pre-execution on sniper tasks" and "Account
whitelist/blacklist on sniper tasks". The API accepts the `hunt` block from any key and ignores it on an account that is
not approved: that is today's every-launch buying. The status endpoint reports only create/start, not approvals.

**The fix is an account approval, not a setting:** ask OKTech to approve the account for the [S] tab (Hunt), local
pre-execution and the account filter. The sniper config already sent is then the strategy as frozen.

## Going live (once a gating setting is found)

1. One capped buy (totalSnipes 1, 0.05 SOL): verify on-chain that a >= 2 SOL non-dev buy came first, and that it sold.
2. Batches of 10 buys at 0.2 SOL, each audited against paper (trigger, landing position, exit, PnL).
3. Raise size / remove the cap only while live per-trade results stay within ~half of paper.
