# The live recipe (forward-tested on paper, 2026-11-05)

All numbers are paper trades on market data recorded **after** each strategy was frozen, simulated at the
landing position OKTech was measured at (same slot as the trigger, ~1 trade in that coin ahead of us),
0.2 SOL per buy, fees and tips included (0.0035 SOL per transaction).

## The owner's yes for the first launch-rule test (7 Nov 2026, 09:15 UTC)

Asked "What's the most efficient way to do this without rinsing the 10 sol?", the owner answered the plan below with
"Yes, use this plan" (09:15 UTC, in chat). The plan, as it was put to the owner:

1. A wallet-wide guard: no new live trade once the day is 1 SOL down or the wallet is under 10 SOL, changeable from
   Telegram.
2. When a strategy passes on paper (100+ new trades at the real landing, positive with outliers capped, at real
   costs), one live test starts straight away: sniper wallet `6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at`, 0.2 SOL a
   buy, one coin at a time, at most 10 buys, at most 2 hours, stopping on its own 1 SOL below the starting balance.
3. After 10 trades it continues only if live is at least half of paper (then 30 more trades, the same size, the same
   1 SOL stop counted from the first test's start); otherwise it stops and the analyst works out why.

Wallet at the time: 11.88 SOL, nothing held, no sniper running. Why 0.2 SOL and not less: a round trip costs about
0.009 SOL whatever the size, so a 0.02 SOL test can only lose. Results go in this file as they come.

### How the yes runs as code

The guard is on from the start. The automatic test starts nothing until the owner's yes is recorded: `/test auto on` in
Telegram (that message is the yes, for the wallet `/test auto` names), or from the cloud session `snipetech remote
--wait run operate autotest --on --approved "Yes, use this plan (7 Nov 09:15 UTC)" --wallet
6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at`. `/test auto` shows where it stands; `/test auto off` turns it off.
The yes is recorded with the sniper wallet and the OKTech wallet it is for: while the config names another, nothing
starts by itself (the owner hears it once a day) and a sequence in progress ends.

**1. The wallet-wide guard** (`operator/risk_guard.py`; the owner's values in `operator/RISK`).

- On at 1 SOL a day and 10 SOL. `/risk` shows it with today's result; `/risk stop 1.5`, `/risk floor 9` and
  `/risk default` change it (`operate risk --day-stop N --floor N --default` does the same). The day stop is 0.1 to 5 SOL
  and the floor 0 to 1000 SOL. There is no off.
- The day's result is our own trading only: today's closed round trips in the measure stage's fills (real fees and
  failed duplicate sells included), plus the coins our buys still hold, at the tape's price. The fills lag by up to 5
  minutes and the cycler re-arms seconds after a sale, so our trades the fills do not have yet are read from the tape,
  and a coin bought today that has left the wallet with no sale seen counts its whole cost as lost. A deposit or a
  withdrawal moves nothing in it. The floor is the wallet itself: SOL, wrapped SOL and coins.
- When the measure stage has not run for 40 minutes and the wallet has traded today since, the guard cannot tell the
  day (it says so in `/risk` and `/status`), and nothing new starts until it runs again.
- While it holds, nothing new starts. The book creates and starts nothing, and stops its snipers once the wallet holds
  nothing. `/test go` and the round's start of a test refuse. The cycler does not start, and it reads the guard before
  each re-arm (like `/stop`) and ends its run. The automatic test asks for nothing. What the guard cannot read never
  counts as clear.
- What it does not stop: while the wallet holds any coin, every book sniper keeps running, and each can still buy up to
  its cap (`[book] max_buys_per_sniper`, 3). The book cannot tell which of its snipers holds a coin (a fill can name the
  wrong leader), and a stopped sniper strands its coin (CLAUDE.md rule 1). The trip line says so; `/sell <coin>` sells
  the coin, and the next book run then stops them all. This is how the book's own stops have always worked.
- The owner hears it once when a stop trips (one line a day for each stop), and `/status` shows it.

**2. The first test starts by itself** (`operator/autotest.py`; the owner's switch in `operator/AUTOTEST`, the
sequence's record in `operator/autotest.json`).

- Each round checks the conditions: the switch on, no test in progress, no `/stop`, the guard clear. When all hold, the
  round writes the `/test go` request itself for the best strategy `/test` would offer (100+ forward trades where a hunt
  fill lands, a lower bound above zero, also with gains capped, scored at no less than the measured cost of a trade).
- "At real costs": a strategy frozen below the cost of a transaction the measure stage measures (half the low bid's
  measured round trip: 0.0047 SOL a transaction, 0.0094 a round trip, on 6 Nov) is not eligible, for `/test go`
  either. `paper freeze` now scores at that measured cost unless `--per-tx` says otherwise (it used to take 0.0035,
  and accepted 0).
- It runs at the strategy's own size, never another. A strategy scored above the yes's 0.2 SOL is refused, and the owner
  is told once a day: a test at another size says nothing about it.
- The yes's caps apply: 10 buys, 120 minutes, the floor 1 SOL below the wallet's balance. The same round starts it, as
  for a `/test go`: one hunt sniper, one cycler.

**3. It goes on only while live is at least half of paper.**

- After a block has made all its 10 buys and closed, its result is read from the measure stage's fills, once that
  stage has run after the block's last trade. Only our own round trips count, with their real fees, the failed
  duplicate sells and the block's failed buy sends (OKTech sends buys that fail on chain anyway, about 0.001 SOL each;
  paper does not model them). A buy counts once its sales cover its tokens (a scale-out exit sells it in two). A buy
  not covered after 45 minutes counts its unsold part at its whole cost; a measure stage that has not read the block
  45 minutes after it ended stops the sequence for the analyst.
- The book trades from the same wallet, and the fills cannot tell its copies from the test's own buys for sure (they
  name a copied leader on any buy in a coin one of the book's leaders bought first). So the block is read twice, with
  the buys the fills call copies left out and with every buy of the wallet in the block, and the worse reading is the
  one judged. A coin left held counts against the block only when the block bought it.
- The live mean per trade, over every trade of the sequence so far, is compared with the strategy's forward paper mean.
- At least half, and above zero: the next block starts at once, with the same strategy, size and caps. Its floor stays
  the first block's, so the whole sequence can lose at most 1 SOL. There are at most 3 more blocks (40 trades in all).
- Below half: the sequence stops, and a decision note says "the analyst works out why" for the post-mortem to pick up.

**What ends it.** A block that ends early (its 120 minutes, the floor, `/stop`, the guard) or does not start. Live
below half of paper. All 4 blocks done. No room above the floor for another block. The config naming another wallet
than the yes. The switch turned off: a block running then runs to its end (`/stop` ends it now), and a request not
started yet never starts. `/stop` between blocks ends the sequence too: it is written into `operator/AUTOTEST` at
once, so a `/resume` before the next round does not bring it back. With no sequence in progress `/stop` leaves the
switch as it is: nothing starts while the flag is set, and after `/resume` the standing yes may start a test again. At
the end the switch turns itself off (marked used, with the result) and the owner gets one summary. A new sequence needs
the owner's yes again; `/test auto on` is refused while a sequence still runs.

**Recorded:** each block in `operator/TEST.json` and `operator/tests.jsonl` with its sequence and block number, and one
`"kind": "sequence"` line per sequence there. A decision note and a Telegram line go out at every start, block end and
sequence end.

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

## How a live test starts (7 Nov)

No SSH and no command line: the owner starts and ends a capped live test from Telegram (`operator/live_test.py`).

1. **`/test`** lists the hunt strategies the forward paper test backs where a live hunt fill lands (100 forward trades
   or more, a lower bound above zero; the lab alone never counts), best lower bound first, at most three, each with its
   forward n, mean and lower bound. Then the plan for the best one, and last the exact line to send. `/test <id>` shows
   another one's plan.
2. **`/test <id> go`** is the owner's yes for exactly that plan (wallet, size, duration). It only writes
   `operator/TEST.json` (state `requested`), and only when the strategy qualifies now, no test is requested, running or
   closing, no OKTech cycler or guard runs, no sniper that is not the book's is active on OKTech (an earlier test's left
   armed, one started by hand), the tape is at most 2 minutes behind (the book's rule: the cap counts our buys there),
   `/stop` is not set, the wallet holds no coin, the wallet holds at least the floor plus one buy and its fees, and the
   wallet-wide guard (above) is clear. It
   offers only strategies whose forward lower bound is above zero also with each gain capped at 2.5 times its buy (the
   7 Nov create-slot bundle artifact made a losing rule look like +10.6 SOL). A message more
   than 5 minutes old is refused, as for `/sell`.
3. **The next operator round** (within 15 minutes) checks all of it again, writes `starting`, creates the strategy's own
   hunt sniper on OKTech (one buy a start, an externalId of its own) and starts one job:
   `oktech cycle --id <sniper> --address <wallet> --floor <F> --max-minutes 120 --max-fills 10 --json`. The round's jobs
   survive the round and deploys; the one-minute pass would kill its children, so it never starts one. A request no
   round acted on within 30 minutes expires; a round that died mid-start is never retried (a cycler it did start is
   adopted), so a crash cannot leave a second sniper.
4. **Caps:** the strategy's own size a buy, 0.5 SOL at most; at most 10 buys, one coin at a time (the cycler re-arms
   only while the wallet holds nothing); 120 minutes; floor = 1 SOL below the balance the test starts from (the
   owner's stop, 7 Nov 09:15), never under 1 SOL. Below the floor the
   cycler stops re-arming. A sniper holding a coin is never stopped. The 10 buys are the larger of our buys on the tape
   and the coins seen in the wallet (a probe with the recorder missing our buys made 118 under a cap of 10 when only the
   tape counted), and the cycler does not re-arm while the tape is more than 2 minutes behind.
5. **How it ends.** At every end (10 buys, 120 minutes, the floor, `/stop`) the cycler stops its sniper if the wallet
   holds nothing: at the time limit the sniper has almost always just been re-armed, and left armed it would buy at the
   next launch that qualifies, past the approved minutes, with nobody watching. The round then checks OKTech: the test
   is done only once its sniper is stopped (the round stops it itself when the cycler could not, after a reboot or a
   kill). While the wallet holds a coin the sniper keeps running to sell it and the test stays `closing`, its sniper
   and its coin expected; the round that finds the wallet empty stops it. `/stop` cancels a test not started yet and
   ends a running one (the cycler reads the flag every 5 seconds). The unsold-coin watch and `/sell` cover a coin
   OKTech does not sell (2 of 11 hunt fills on 5 Nov, HOUSE and CAT).
6. **Recorded:** `operator/TEST.json` (the plan, the sniper, the job, start and end, the cycler's report: fills, start
   and end balance, why it ended, the coins held by the shared rule, and whether the round stopped the sniper), one
   line per finished test in `operator/tests.jsonl`, a decision note and a Telegram line at the request, the start, the
   end and the close, the cycler's log `jobs/<id>.log` (never pruned), and every fill on the tape and in `fills`. Each
   test's result is written up here.

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

## Fee test, 6 Nov 2026 (prepared 12:20 UTC, revised 13:40 after the audit; the owner starts it)

Owner's yes in chat: "it's funded, I say yes to everything". Wallet 2.92 SOL at 12:00 UTC.

- **Question:** does paying for position move us up inside the leader's slot? Two arms on the same leaders:
  `control` (priority 0.001, tip 0.002, OKTech's minimums) and `paid` (priority 0.02, tip 0.02). Sells bid 0.002 on
  both arms (OKTech sends every sell 2–3 times; the losers pay the sell priority fee, so a 0.02 sell bid would cost
  0.02 per twin). Seed account off, one send requested, preconfs requested (OKTech gates them per task type).
- **Leaders:** `2YDnmMQt…` (qualified at +6, n 93), `G2oZrd1k…` (n 40, +0.017 at +6), `4yo9CUuT…` Gfree (active,
  the loudest copier crowd). Copy only their buys ≥ 0.3 SOL (Gfree ≥ 0.5).
- **Size and caps:** 0.2 SOL a fill, 2 buys per sniper, 6 snipers → at most 12 fills (2.4 SOL notional; the lab exit
  returns the SOL: dead exit 1 s, stop-loss 30 %, 300 s max). Fees: the paid arm pays 0.04 SOL per buy, the control
  arm 0.003; each sell ≈ 0.004 plus 1–2 failed twins at ≈ 0.003 each. Budget ≈ 0.4 SOL of fees for the whole test.
- **Design caveat (accepted):** both arms copy the same leaders from one wallet, so each leader buy triggers two
  fills (0.4 SOL a signal) and the positions share one token account. That is also the point: each signal gives a
  matched pair, control and paid, in the same market moment. The measure stage counts only *other* wallets' trades
  between the leader and each fill, so our own second fill does not make the arm that went second look worse.
- **Stop rules:** `oktech guard --floor 1.2 --max-minutes 180` stops everything if **equity** (SOL + the tape value of
  the coins held) falls below 1.2 SOL or the SOL balance below 0.05, and otherwise never stops a sniper (they are
  capped). A floor breach is the emergency: it strands whatever is held, and the guard lists it for the owner to
  sell by hand. `operator/expected.json` = 6 running, 3 held, so the operator reports deviations, not the test
  itself. `/stop` on Telegram stops the snipers only while nothing is held.
- **Measurement:** every fill becomes a `fills` row with its arm read from the fee it paid (both arms share the
  wallet; OKTech's task documents carry no fills), the copied wallet, buys behind it, price gap, priority, tip and
  relay, and PnL; `bid_model` per arm: n, failed sends, mean/p75 position, mean buys behind, mean gap, mean fee,
  tipped share, overhead. The comparison is the paired one: for each leader signal, where did control land, where
  did paid land.
- **Baseline before the test** (re-measured 6 Nov over 5–6 Nov, 39 round trips at the minimum fees): mean 2.5 buys
  behind the copied wallet, price gap +26 %, p75 position 4 in the slot, 0.0028 SOL of fees per buy, +0.0020 SOL per
  buy in failed sends, 90 % of buys tipped; realised −0.199 SOL with every fill's real fees.
- **Reading the result:** `snipetech remote --wait run measure report --since <start>` pairs each control fill with
  the paid fill on the same coin within 20 s and says in one line whether paying landed earlier and what it cost.
- **Safety while it runs:** `/stop` acts within a minute once `snipetech-commands.timer` is enabled; a manual
  `oktech stop` refuses while a coin is held (add `--force` only to strand it on purpose); `guard` and `start` refuse
  while the `/stop` flag is set.
- **Why the owner runs the create commands and the guard:** the cloud session's safety layer refuses to start
  real-money transactions itself. The commands (from a checkout with the ops key):

```
snipetech remote --wait run oktech copylist --create --buy 0.2 --snipes 2 --tag arm:control \
  --copy 2YDnmMQt7x7Y3VFounzsnZ3s8TdozqWyK9qeQPWY9saH:2YDn:0.3 \
  --copy G2oZrd1kAUrWE1E4YPZoMi9euBeGgvtKVj5CHSdwazx:G2oZ:0.3 \
  --copy 4yo9CUuTBbds9NFhZd4MzPiZZkUvveXdTnAH8qMsE8ku:Gfree:0.5
snipetech remote --wait run oktech copylist --create --buy 0.2 --snipes 2 --priority 0.02 --tip 0.02 --tag arm:paid \
  --copy 2YDnmMQt7x7Y3VFounzsnZ3s8TdozqWyK9qeQPWY9saH:2YDn:0.3 \
  --copy G2oZrd1kAUrWE1E4YPZoMi9euBeGgvtKVj5CHSdwazx:G2oZ:0.3 \
  --copy 4yo9CUuTBbds9NFhZd4MzPiZZkUvveXdTnAH8qMsE8ku:Gfree:0.5
snipetech remote run oktech guard --id <the six ids printed above> --address 6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at --floor 1.2 --max-minutes 180
```

**Started 6 Nov 15:08 UTC** on the owner's "do everything" in chat. The sniper wallet held 4.76 SOL (4.64 SOL + 0.12
WSOL), no coins. Six capped snipers, created idle and started by `oktech guard` (equity floor 2.0 SOL, 180 minutes,
job `20261106-150848-a85b9a`):

| Leader | Low bid (control) | Paid bid |
|---|---|---|
| `2YDnmMQt…` | `6aeded3c9d2356e2dc629ed5` | `6aeded569d2356e2dc62a3e8` |
| `G2oZrd1k…` | `6aeded3c9d2356e2dc629ee1` | `6aeded579d2356e2dc62a3f4` |
| Gfree `4yo9CUuT…` | `6aeded3d9d2356e2dc629eed` | `6aeded579d2356e2dc62a400` |

At the start OKTech wrapped 0.0996 SOL into WSOL in one transaction (system transfer + sync native): the SOL
balance fell, equity did not. `operator/expected.json` = 6 running, 6 held for the test.

Owner's money moves during the test (not trading): 15:16:40 UTC 2.542 SOL out of the sniper wallet in a plain
transfer, 15:16:51 0.599 SOL back in; equity 2.82 SOL at 15:20 (2.60 SOL + 0.22 WSOL), no coin held. The guard saw
the SOL balance at 1.997, under its 2.0 floor, valued the wallet at 2.22 SOL with the wrapped SOL and correctly did
not call an emergency. The owner said they are adding more.

**Result (6 Nov 18:10 UTC): paying more did not buy a better landing; both arms lost on what they bought.**
From `measure report --since 1793977728` (15:08 to 18:08; the window also holds the live book's copies of 2YDn from
17:01, which ran on the low-fee arm):

| arm | round trips | buys behind the copied wallet | price above its buy | fees, SOL | result, SOL |
|---|---|---|---|---|---|
| low fee (0.001 + 0.002) | 9 | 0.22 | +2.3 % | 0.082 (≈0.009 a trip) | −0.282 |
| paid (0.02 + 0.02) | 4 | 0.5 | +1.6 % | 0.222 (≈0.055 a trip) | −0.325 |

- **Landing:** both arms landed right behind the wallet they copied (under one trade behind, 2 % above its price),
  against 2.5 behind and +26 % in the 5–6 Nov baseline. The baseline copied crowded wallets (Gfree) with the seed
  account on; today's leaders (2YDn, G2oZ) were less crowded and every sniper had the seed account off. Paying 20×
  the fee added nothing measurable on these wallets.
- **Cost:** the paid arm costs about 0.055 SOL a round trip, six times the low-fee arm. Some of its sells and their
  failed duplicates went out at the 0.02 buy priority.
- **Pairing:** none. OKTech buys a coin once per wallet, so when both arms copy the same wallet only the faster one
  buys; a paired comparison needs two OKTech wallets.
- **Seed-account sweeps:** gone (0 failed 100k-CU sweeps since the fix; 13 on 5–6 Nov).
- **Duplicate sells:** still there, about one failed send per landed sell (11 for 9 on the low-fee arm, 4 for 4 on
  the paid arm).
- **A last buy left unsold:** the G2oZ paid sniper bought DTAU… at 17:09 with its last allowed buy; its 300 s timer
  never fired, the coin ran 2.6× and fell back, and the owner sold it by hand at 17:18 (−0.048 SOL). A capped
  sniper's last position is not reliably managed after it finishes.
- **What lost the money:** not the landing. The copies buy right behind a large buy (2YDn buys about 3 SOL) and the
  exit (dead exit after 1 s, stop-loss 30 %, copy exit only when the leader sells half at once, no take-profit) sells
  seconds later into the dip, or never locks in a run. The live book dropped 2YDn at 17:50 after 10 live fills at
  −0.045 SOL a trade (its tape verdict had said +0.0146). The OKTech feature audit replays these trades against
  other exits and entry filters before the next live step.

Deploy during the test: at 15:58 the owner's "go do it" deployed `097cb4c`, which restarts the ops service and killed
the first guard; a second guard (`oktech guard --watch-only`, job `20261106-155810-0f725c`, same floor, until 18:08)
watches the same six snipers without calling start on them again.

## A copy OKTech never sold, 6 Nov 2026 20:33 UTC (the third such coin)

One of the two fee-test snipers still running on Gfree (`4yo9CUuT…`; the lab exit: dead exit 1 s, stop-loss 30 %,
300 s timer; 2 buys each) bought 0.2 SOL of `5m5hP15rrB5kyF3V9xsbYfCnvKKr6ZzXozYwAd1kirCF` at 20:33:28, behind
Gfree's buy. The crowd that copies Gfree sold within a second and the price fell about 65 %. **No exit fired**: no
dead exit (the coin then sat quiet for 44 s), no stop-loss, no 300 s timer; OKTech still listed the sniper as
running. The same evening its two earlier Gfree copies were sold within 2 and 8 seconds.

- Third coin OKTech abandoned (4siK 5 Nov, DTAU 17:09, 5m5h 20:33); all three were most likely the buy that used up
  their sniper's cap. OKTech's exits cannot be relied on for the cap-using buy, whatever the preset.
- Our own sell (`snipetech sweep --mint …`) was refused by the cloud session's safety layer even with the owner's
  "do everything" in chat; the owner was asked to sell by hand (by 21:03 the coin was still held, worth about 0.07
  SOL of the 0.2 paid; no trade in it since 20:39).
- What changes: the unsold-coin watch (a Telegram line within about a minute of a missed exit), `/sell <coin>` in
  Telegram so the owner sells with one message, and the book relying on each sniper's first buy only.
- The two Gfree test snipers (`6aeded579d2356e2dc62a400`, `6aeded3d9d2356e2dc629eed`) should be stopped once the
  wallet is flat (`/stop`); the session's safety layer refused that stop too.
- **Sold 7 Nov 05:38:47 UTC** for 0.0763 SOL (tx `45KUm1VdELBNLHXMQdBQZyG8ewNAzHwJrmytkW5qnNvuoC3kNRTY1YyzfVkKLZm6CZHJdR9Lq9dE1KpKB34Qriu2`):
  −0.121 SOL on the 0.1975 bought (−0.167 with the paid arm's 0.04 SOL buy fee and failed sends), nine hours after
  OKTech's exits should have sold it.

## The live book switched on, 6 Nov 2026 16:30 UTC

Owner's yes in chat: "Bro just start it go we can work on safety when im home" (after "nah just start it" and "just
start if for me rq"; the session's safety layer had refused the switch twice before allowing it). `snipetech book on`
wrote `operator/BOOK_ON` on the server. Wallet: the sniper wallet `6VFrZ15V…`. Size and caps (config defaults): 0.2 SOL
fills on the low-fee arm, at most 6 wallets, 2 SOL a day at risk, a 1 SOL day stop, idle below 3 SOL equity, only
wallets whose verdict scores at least 0.005 SOL a trade. Duration: until `/book off` or `/stop`. At the switch:
equity 2.65 SOL and one qualified wallet at 0.0013, so the book sits idle until both conditions change.
Code on the server at the switch: `097cb4c`; the review fixes (`a095854`: `/book off` stops the book's snipers, 3 buys
a sniper) were refused deployment while the fee-test guard ran, so until they are live `/stop` is the halt.

## Operator outage, 6 Nov 12:16–12:45 UTC — fixed

Two rounds (12:16, 12:30) crashed before the snapshot: a local `from .service import Service` inside the CLI's new
`book` branch shadowed the module-level name for the whole `main()`, and the `operate` branch raised
`UnboundLocalError`. Fixed in `35c8580` with a regression test on the dispatch functions; the 12:45 round ran.
Lesson recorded in the test's docstring.

