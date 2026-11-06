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

## Hunt live, 5 Nov 21:13–22:10 UTC (account approved for Hunt + local pre-execution)

Facts measured on chain and on the tape, 0.02 SOL per buy on the SNIPER wallet:

- **Hunt triggers correctly.** Every audited fill had a big first-second buy ahead of it; we landed 1–2
  slots after the create with 2–7 buys in front.
- **A hunt sniper fires once per start and never re-arms.** Sniper A: one buy 23 s after start, then
  none across 46 qualifying launches; stop + start → a buy within seconds; after that position closed,
  none across 12 more. Sniper B identical. `oktech cycle` (stop + start a spent sniper only while the
  wallet holds no coin bought during the run) is the workaround; 6 re-arms produced 2 fills in 2 min.
- **Hunt sums the creator's own buy.** Suncat fired on 1.6 SOL from others + 1.0 SOL from the dev.
  Tape (4 h, 500 candidates): total ≥ 2.5 with dev ≤ 1 SOL keeps 79 % of our single-non-dev-≥2 rule
  at 86 % precision (total ≥ 2 alone: 66 %). Strategy snipers now send minTotalSol 2.5, maxDevBuySol 1.
- **PUMP-quoted launches** (quote `pumpCmXq…`, also SQQQ…) trigger Hunt (quote units counted as SOL) and
  the SOL buy fails with pump error 6010; local pre-execution does not cover non-SOL curves. Only the
  account filter (not granted) can exclude them. Each such attempt spends the sniper's one shot.
- **Pre-execution grant landed between the two creates**: the 21:13 sniper ran without it, the 21:19 one
  with it (OKTech turned it on by default). Behaviour was the same either way.
- **A dead coin holds to the timer**: Bricks sat flat for 300 s. Strategy snipers now add a 60 s dead exit.
- **`remote update` kills running server jobs** (the first cycler died at a deploy). Never deploy while a
  guard or cycler runs.

| fill | non-dev buy in 1 s | dev buy | rule met | in → out (SOL) |
|---|---|---|---|---|
| RLT | 11.85 | 0 | yes | 0.0198 → 0.0215 |
| Bricks | 2.96 | 0 | yes | 0.0198 → 0.0196 (timer) |
| Suncat | 1.60 | 1.0 | no | 0.0198 → 0.0272 |
| Gogeta | 2.96 | 0 | yes | 0.0198 → 0.0301 |
| II | 2.86 | 3.16 | yes (new thresholds would skip) | 0.0198 → 0.0170 |

Gross +0.0166 SOL on 0.099 deployed; wallet net −0.016 (1.6878 → 1.6718): at 0.02 SOL the fixed fees per
round trip and per failed PUMP-quoted send outweigh the edge. 22:11 UTC: 0.2 SOL batch started, two snipers
under `oktech cycle --max-fills 10 --max-minutes 60 --floor 0.3`.

### 0.2 SOL batch, 22:11–22:40 UTC — stopped

| coin | entry slot | big first-second buys | in → out (SOL) | note |
|---|---|---|---|---|
| Mumdumi | +3 | 4 buys summing > 2.5, biggest 0.59 | 0.1975 → 0.190 | Hunt sums, our rule is one buy |
| BETRAYAL | +1 | dev 0.99 + 8 wallets × 1.5–4.2 (create bundle) | 0.1975 → 0.0864 | bundle dumped 3 s after our buy |
| HOUSE | 0 | dev 3.95 + 2.37 | 0.1975 → 0.1195 (sold by hand 22:40) | dumped 4 s after; OKTech never sent a sell |
| GTA | +3 | dev 1.38 only | 0.1975 → 0.1975 | dead exit 60 s worked; thresholds not enforced |
| ALON | +2 | dev 0.99 + 8 wallets × 1.5–4.4 (create bundle) | 0.1975 → 0.0618 | bundle dumped at slot +10 |
| CAT | 0 | dev 2.96 + 2.67 | 0.1975 → 0.7215 (sold by hand 22:40, two txs) | the one runner |

Net of the batch +0.25 SOL thanks to CAT; without it −0.47. Day: 1.903 → 1.777 SOL.

**The structural finding.** Our paper entry is position +1 in the trigger's own slot. When the ≥ 2 SOL buy sits
inside the create bundle (most of the triggers), nobody can land there: OKTech lands at slot +1/+2, after the
whole bundle, at the top. Tape, last 3 h, our-rule launches, entry at the first trade of slot +2, mark to
market after 30 s / 120 s:

| big buy | create-slot buyers | n | 30 s | 120 s | dumped > 30 % within 30 s |
|---|---|---|---|---|---|
| in create | ≤ 2 | 108 | −11 % | −17 % | 19 % |
| in create | 3–4 | 88 | −17 % | −16 % | 50 % |
| in create | ≥ 5 | 69 | −15 % | −26 % | 41 % |
| same slot | ≤ 2 | 22 | −4 % | −4 % | 18 % |
| same slot | 3–4 | 126 | −19 % | −23 % | 53 % |
| same slot | ≥ 5 | 263 | −8 % | −20 % | 49 % |
| later slot | ≤ 2 | 21 | +2 % | +6 % | 29 % |
| later slot | 3–4 / ≥ 5 | 25 | −14 % / +4 % | −15 % / −24 % | 36 % / 27 % |

Lab runs with the realistic landing (`--latency 1|2 --position start`) were started 22:29 (jobs
20261105-222916-301925, 20261105-222915-7da66c); their verdict goes here. Other live facts: Hunt thresholds
must be whole numbers without null fields (2.5 / 1.0 / nulls were ignored and the sniper bought every launch);
OKTech sent no sell at all for HOUSE in 25 minutes; CAT's trailing stop did not fire on a 26 % drop from peak
either. Live trading is halted until the lab says a variant is positive where OKTech lands.

### Verdict at the realistic landing (lab, 24.5 h of tape, 224k moments, 5 Nov 23:45 UTC)

Entry at the first trade of the slot after the trigger (`--latency 1 --position start`), 0.2 SOL, every
trigger with its best exit, out of sample. (Staleness is measured in slot gaps since commit 1ea0b43; the
earlier run that showed −0.0115 everywhere was the whole-second artefact.)

| trigger (best exit) | n | SOL/trade | lower bound | wins |
|---|---|---|---|---|
| buy ≥ 2 SOL by a non-dev, 1 s, normal coins (STALE1s SL-15%) | 834 | −0.0115 | −0.0125 | 12 % |
| same, not a create bundle (solo), 1 s | 674 | −0.0117 | −0.0128 | 12 % |
| solo, 5 s window | 733 | −0.0093 | −0.0112 | 12 % |
| buy ≥ 1 SOL solo, 5 s | 1,569 | −0.0093 | −0.0102 | 13 % |
| tracked wallet first buy ≥ 1 SOL (STALE1s SL-30%) | 811 | −0.0064 | −0.0078 | 19 % |
| momentum ≥ 20 buyers/5 m, curve ≥ 30 % | 1,263 | −0.0051 | −0.0066 | 24 % |
| KOL first buy ≥ 0.1 SOL, hold 60 s | 74 | +0.0158 | −0.0011 | 38 % |

Not one of the 40 triggers has a positive lower bound with n ≥ 200. The best exit for almost every launch
trigger is the immediate dead exit, i.e. the least-bad choice is to get out at once: **at the slot after
the trigger, launch sniping has no edge on this tape**. The +0.129/trade paper result for the ≥ 2 SOL
trigger existed only at position +1 inside the trigger's own slot, which no sender gets.

Still open: `--latency 2` (running), and the per-wallet ranking at the realistic landing (`--by-wallet`),
because a KOL's buy is its own transaction and copying it lands +1 in the same slot on the measured copy
tests — the one entry where the paper position is reachable.

Two slots after the trigger (`--latency 2 --position start`, 25.1 h): the same picture, slightly worse —
our trigger −0.0131/trade (n 861, 7 % wins), solo −0.0120 (n 758), best launch trigger −0.0072 (momentum,
n 702); only the two tiny KOL first-buy rows are positive (+0.011 / +0.008, n 59 / 75, lower bounds
negative). No positive lower bound with n ≥ 200 at either landing.

### Wallet copies at the realistic landing (`--by-wallet --latency 1 --position start`, 25.6 h, 6 Nov 00:45 UTC)

97 wallets scored (first buy ≥ 1 or ≥ 2 SOL, or a KOL's first buy ≥ 0.1 SOL), best exit each, out of sample:

| wallet | n | SOL/trade | lower bound | wins | 25 h total |
|---|---|---|---|---|---|
| Washy dev | 6 | +0.102 | +0.015 | 67 % | +0.61 |
| Pengu (KOL, hold 60 s) | 16 | +0.069 | +0.009 | 38 % | +1.10 |
| ottabag | 10 | +0.044 | −0.004 | 30 % | +0.44 |
| Cupseyy | 6 | +0.042 | −0.010 | 33 % | +0.25 |
| clukz | 5 | +0.026 | +0.013 | 60 % | +0.13 |
| Bean Side (≥ 2 SOL) | 16 | +0.016 | +0.002 | 56 % | +0.26 |
| Megga | 23 | +0.015 | −0.004 | 30 % | +0.34 |
| theo side (≥ 2 SOL) | 32 | +0.008 | −0.008 | 38 % | +0.25 |
| Decu | 29 | +0.002 | −0.006 | 45 % | +0.05 |
| dv (≥ 2 SOL) | 33 | +0.001 | −0.021 | 30 % | +0.05 |
| Solprop (≥ 2 SOL) | 54 | +0.001 | −0.006 | 31 % | +0.06 |

Everything positive is small and thin: the top 25 wallets together made about +4 SOL of paper in 25 hours at
0.2 SOL a trade, most of it from a handful of fills, and only three rows have a positive lower bound (n ≤ 16).
A copy bot on these wallets is a few SOL a day at best at this size, not a path to 100. The fairer landing for
copies — one trade behind the leader in the same slot, which the speed tests measured — is being re-run now with
the corrected staleness (`--latency 0 --position +1 --by-wallet`).

## Copy-landing test, 6 Nov 04:36–05:45 UTC — stopped, answer in hand

Twenty copy snipers (five of the paper-positive wallets at 0.05 SOL, fifteen breadth wallets at 0.03 SOL, lab
exit = 1 s stale / −30 % / 300 s, three cyclers re-arming spent snipers) on wallet SNIPER. Seven fills in 70
minutes, every one a bonding-curve buy that landed in the leader's own slot or the next one — the speed is
there — and still 2–4 trades behind the leader, after the other copy bots had already moved the price:

| time (UTC) | leader | leader buy | our buy | behind | price gap at fill | result |
|---|---|---|---|---|---|---|
| 04:4x | Gfree | 0.9 SOL | 0.049 | 2 trades | +3x % | −26 % (leader sold in 20 s) |
| 04:5x | Decu | — | 0.049 | 2 trades | — | +11 % |
| 05:04 | Gfree | 0.98 SOL | 0.049 | 2 trades | +34 % | −26 % |
| 05:15 | Decu | 2.93 SOL | 0.049 | 2 trades | +4 % | ≈ +0 % (coin graduated in 9 s; sold on PumpSwap into WSOL 0.0549) |
| 05:32 | Decu | 2.93 SOL | 0.049 | 4 trades | +43 % | −10 % |
| 05:32 | Art | 0.96 SOL | 0.030 | 2 trades | +26 % | +1 % |
| 05:39 | Gfree | 1.25 SOL | 0.049 | 3 trades | +33 % | −26 % |
| 05:42 | Gfree | 2.40 SOL | 0.049 | 2 trades | +18 % | +17 % |

Mean per fill about −7 %; equity 1.777 → 1.65 SOL (0.13 SOL spent, of which ≈ 0.02 was OKTech's duplicate
sells and failed ATA creates — every sell is still sent three times). The price gap column is the whole story:
the first copy bots pay the leader's price, we pay 18–43 % more, and a 60-second hold cannot recover that on
coins the leader himself flips within a minute. This is exactly the "+6 behind" regime the lab predicted
(roadmap, position-sensitivity table), where the known wallets are worth ≈ 0, not the "+1" regime that paid.

Stopped at 05:45 while nothing was held: cyclers killed first, then all twenty snipers set idle (verified).
Nothing is running on the account; the snipers still exist and can be restarted with one command if a
different landing is ever available. Remaining budget was not the reason — the question was answered, and
every further fill would have been paid for at a known negative expectancy.

### The "quiet follow-through" wallet hunt (6 Nov 05:38 UTC) — no survivors

Scoring 271 discovered wallets for "price keeps moving up 20–30 s after their buy, with few other buyers piling
in" produced 34 candidates with huge mean returns (+5 400 %, +1 300 %…). Checked at the next slot after their
buy with a 30 % stop and returns capped at +200 %:

- eight of the 34 are the coins' own creators (every "buy" is the dev's bundle in the create transaction) —
  uncopyable, we land a slot later;
- the rest have 16–35 % winners and 70–95 % of entries stopped out; the means were one or two 50× outliers;
- best non-creator row: +4 % per trade with 82 % stopped (DFihBUSK) — noise.

Low-cap follow-through wallets with a copyable, quiet landing do not exist in this 30-hour window. The search
stays automated (`discover-wallets` → `follow` → lab by wallet at +1 and +6) in the autopilot, but the live
list is empty until a wallet clears all three with n ≥ 20.

### Inside the slot: what the buyers ahead of us paid (6 Nov, 08:30 UTC)

Every buyer between the leader's buy and ours on the seven copy fills, read from the transactions themselves
(version-1 transactions need `maxSupportedTransactionVersion: 1`; our own fills are legacy/v0, OKTech's `tipAmount`
does not appear as a Jito tip):

| coin | position in slot | wallet | buy | priority fee | Jito tip | who |
|---|---|---|---|---|---|---|
| 3BrEjvww | 61 / 62 / 63 | Gfree + two wallets | 1.25 / 1.30 / 1.36 | 0.002 each | – | leader's own bundle (same fee, same tx format, adjacent) |
| 3BrEjvww | 334 | 6Z91Dgiz | 2.47 | 0 | **0.033** | copy bot |
| 3BrEjvww | 392 | **us** | 0.049 | 0.001 | – | last |
| H4D5mThN | 24 / 25 | Decu / FteNBUJP | 2.93 / 4.94 | 0 / **0.200** | – | a bot paying 0.2 SOL to sit directly behind Decu |
| H4D5mThN | 235–256 | three bots | 0.3–3.0 | 0.0004–0.010 | – | |
| H4D5mThN | 270 | **us** | 0.049 | 0.001 | – | last |
| A7YsCfw6 | 108 | E3wNBV6L | 4.15 | 0 | **0.325** | copy bot (8 % of the buy) |
| au6jPXHf | 184 | 9nH7Fpth | 3.25 | 0.032 | 0.003 | directly behind Gfree |
| 7bDNtSnb | 407 / 461 | 6Z91Dgiz / **us** | 2.47 / 0.049 | 0 / 0.001 | 0.033 / – | |

Reading: we were in the leader's slot six times out of seven — OKTech's speed is not the problem. The order inside
the slot is a fee auction: the leader's own wallets are unbeatable, then bots paying 0.01–0.3 SOL per fill, then us
at the 0.001 minimum, last every time. The bots trade 1–5 SOL a fill, so 0.01–0.03 SOL of fees is 1–3 % to them
and 20–60 % to our 0.05 SOL fills. Conclusion: the copy test failed on the bid and the size, not on the pipe. Next:
a paid-fee test on OKTech at 0.2 SOL fills (control at minimum fees vs 0.02 priority + 0.02 tip, same leaders,
five fills each, position and price gap as the outcome), and the lab at position +3, where a paid bid would land.

## Operator phase 1 on the server (6 Nov, 10:31 UTC) — accepted

Deployed `2de75be` at 10:30 with no job running. The 10:31 round (new code) logged
`resources: anthropic_credit=ok birdeye_cu=ok solanatracker_requests=ok vybe_credits=ok sol_float=ok disk=ok
oktech_failures=ok`, sent the first daily digest to Telegram, and called the operator about `snipetech-daily`
failing (the 6-hour timeout above). `/status`, `/stop`, `/resume` are live but need a message from the owner's
phone to be seen in the log; the dashboard needs `DASH_TOKEN` in the env file. Phase 2 (`discover`, `qualify`)
deployed right after; the timers wait for `setup.sh` on the box.

## Learning loop phase 2 on the server (6 Nov, 10:38–11:05 UTC) — accepted

`discover run` (job 20261106-103747): 4 338 new candidates, no source error, 223 s. `qualify run` (job
20261106-104525): 300 evaluated, 126 through one restricted lab (cand group only), 777 s; 1 qualified
(`2YDnmMQt…`, n 93, +0.0146 / +0.0019 SOL, crowding 3), 22 retired, 277 candidate, 174 without tape data.
Vybe source after the field fix (job 20261106-110247): 344 rows, 311 new. The operator's 11:01 round flagged the
qualified wallet as a decision note. Timers `snipetech-discover.timer` / `snipetech-qualify.timer` are installed
by `setup.sh` and still need the owner's `enable --now`; until then the hourly cloud round stands in.

## Measure stage on the server (6 Nov, 11:15 UTC) — accepted, and it agrees with the hand-made table

`measure run` (job 20261106-111501, 3 s) over the last 24 h: 118 fills — 28 buys, 28 sells, **62 failed sends**
(the triple-send duplicates, 05 Nov 17:02 → 06 Nov 05:44), 28 round trips, 4 copied wallets with a live expectancy.
The copy-test buys it attributed by itself, against the table in "Inside the slot" above:

| time | coin | copied | trades behind | gap % | pos in slot / txs | priority | tip | CU limit |
|---|---|---|---|---|---|---|---|---|
| 05:04:12 | au6jPXHf | 4yo9CUuT | 2 | 34.0 | 4 / 12 | 0.001 | 0 | 150 000 |
| 05:15:46 | 2NgdUjFY | 4vw54BmA | 2 | 3.7 | 0 / 13 | 0.001 | 0 | 150 000 |
| 05:32:08 | H4D5mThN | 4vw54BmA | 4 | 42.7 | 5 / 24 | 0.001 | 0 | 150 000 |
| 05:32:08 | A7YsCfw6 | CgaA9a1J | 2 | 26.2 | 4 / 10 | 0.001 | 0 | 150 000 |
| 05:39:45 | 3BrEjvww | 6Z91Dgiz | 0 | 0.3 | 4 / 7 | 0.001 | 0 | 150 000 |
| 05:42:23 | 7bDNtSnb | 6Z91Dgiz | 0 | 0.2 | 3 / 4 | 0.001 | 0 | 150 000 |

Bid model, arm `control`: n 28, failed 62, mean position 1.7, p75 position 3.75, mean gap 17.9 %, mean fee
0.00113 SOL. Live expectancy (all `warming`, n ≤ 2): every copied wallet negative at this landing, as the test said.
Failed sends carry no fee fields yet (the fingerprinter skips errored transactions); their count is the cost signal.
From here on this table writes itself every five minutes once `snipetech-measure.timer` is enabled.

## Fee test, 6 Nov 2026 (prepared 12:20 UTC; the owner starts it)

Owner's yes in chat: "it's funded, I say yes to everything". Wallet 2.92 SOL at 12:00 UTC.

- **Question:** does paying for position move us up inside the leader's slot? Two arms on the same leaders:
  `control` (priority 0.001, tip 0.002, OKTech's minimums) and `paid` (priority 0.02, tip 0.02). Both with one send
  per transaction (`globalSendCount 1`, new default) and preconfs requested.
- **Leaders:** `2YDnmMQt…` (qualified at +6, n 93), `G2oZrd1k…` (n 40, +0.017 at +6), `4yo9CUuT…` Gfree (active,
  the loudest copier crowd). Copy only their buys ≥ 0.3 SOL (Gfree ≥ 0.5).
- **Size and caps:** 0.2 SOL a fill, 2 buys per sniper, 6 snipers → at most 12 fills (2.4 SOL notional, sequential;
  the lab exit returns the SOL: dead exit 1 s, stop-loss 30 %, 300 s max). Fees at most 0.24 SOL on the paid arm.
- **Stop rules:** `oktech guard` stops everything below a 1.2 SOL balance or after 180 minutes, and never stops a
  sniper that holds a coin. `operator/expected.json` = 6 running, 3 held, so the operator reports deviations, not the
  test itself. `/stop` on Telegram works the same way.
- **Measurement:** the measure stage writes every fill (position in the slot, trades behind the leader, gap, fees)
  and the bid model per arm; the comparison is `bid_model`: p75 position and mean gap, control vs paid.
- **Why the owner runs the two create commands and the guard:** the cloud session's safety layer refuses to start
  real-money transactions itself; the commands are in the chat of 6 Nov 12:25 UTC.

Result: _pending_.

## Operator outage, 6 Nov 12:16–12:45 UTC — fixed

Two rounds (12:16, 12:30) crashed before the snapshot: a local `from .service import Service` inside the CLI's new
`book` branch shadowed the module-level name for the whole `main()`, and the `operate` branch raised
`UnboundLocalError`. Fixed in `35c8580` with a regression test on the dispatch functions; the 12:45 round ran.
Lesson recorded in the test's docstring.

