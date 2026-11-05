# Roadmap: our own version of what the winning bots do

The bots we studied (6Z91, Fhbh1) win on coins that are 0–3 s old, right after real money shows up.
They will usually be ahead of us on the first buy, so we don't fight them for it. We use their buys
as part of our signal, enter one step behind, and make the money where they make it: on the coins
that keep running after the first seconds, with a scale-out exit. Everything below is paper-tested
first on the server's recording and only then run live in capped batches.

## Where we stand (5 Nov, evening)

- Recorder: every pump.fun / LaunchLab trade with exact block position, 24/7 on the server (LaserStream).
- Paper strategy `a586d8164a` (frozen 13:05 UTC, judged only on newer data): buy right after a non-dev puts
  >= 2 SOL into a coin within 1 s of its creation, normal coins only, SL -15% / trailing 10% / 5 min:
  **+0.133 SOL per 0.2 SOL trade, 629 trades over 4.8 h, 49% wins** (bound +0.120). The >= 5 SOL version
  `3fe27cc4b7`: +0.181 over 204 trades (bound +0.162). The lab over the whole 19.6 h tape agrees (+0.121 / +0.192 on
  the newer 40%).
- OKTech trigger solved: `hunt` strict mode waits for that buy (verified on-chain, 17:02 UTC).
- Sells are safe: snipers cap their own buys and keep selling; the guard only stops in an emergency.
- The owner's tracked-wallet list (2,626 Solana addresses: KOLs, devs, snipers, bundles, cabals) is loaded as labels
  (`label --file inputs/tracked_labels.tsv --kind wallet`) and studied below.

## What the tracked-wallet list says (5 Nov)

Two days of tape, 171 of the 2,626 wallets active (>= 6 coins bought). Three questions, three answers:

1. **Copy them trade for trade, one slot behind, mirroring their exits (0.5 SOL)?** No. 156 wallets scored
   (`follow`), 7 positive, median -0.11 SOL per position. The same-slot column shows why: Washy2 +0.21 landing with
   them, -0.00 one slot later; Decu +0.03 → -0.14; Theo dev +0.14 → -0.04; Cupseyy -0.03 → -0.35. Two to five copy
   bots already sit in their slot and the price we get is theirs.
2. **Does the crowd follow their first buy?** For a short list, strongly. First buy >= 1 SOL on the curve, price
   30 s later vs right after their buy: Cupseyy +86% (10 coins, 170 buyers follow), cap +52%, 0xsolomon +50%
   (21 coins), dv +47% (24), Washy2 +47% (22), Decu +43% (49 coins, 144 buyers), 3|Dustxbt +34% (34), Theo +30%.
   Snipers, bundles, "dev" wallets and the copy bots on the list are flat to negative: they are the ones being
   copied. (Fake Ansem is excluded: not a KOL.)
3. **Their first buy as a signal with *our* exit, at our speed?** Positive for a handful, small in SOL. Lab, 0.2 SOL,
   landing in their slot one trade behind (+1), exit picked on older data (dead-exit 2 s / SL -30% / 300 s), scored on
   all of each wallet's first buys >= 1 SOL (n is small: treat as a shortlist, not a result):

| wallet | address | first buys | +1 mean | +1 bound | win | +3 mean | +3 bound |
|---|---|---|---|---|---|---|---|
| 0xsolomon | `DEdEW3SM…` | 6 | +0.258 | +0.095 | 100% | +0.197 | +0.053 |
| Limfork.eth | `BQVz7fQ1…` | 4 | +0.071 | +0.061 | 100% | - | - |
| 0xsolomon | `3GzBLpxT…` | 11 | +0.065 | +0.053 | 100% | +0.007 | -0.003 |
| Decu | `4vw54BmA…` | 19 | +0.081 | +0.040 | 53% | +0.047 | +0.019 |
| Art diddy bundle | `BsvjGudt…` | 5 | +0.040 | +0.032 | 100% | - | - |
| dv | `BCagckXe…` | 19 | +0.099 | +0.029 | 42% | +0.068 | -0.004 |
| Publix | `86AEJExy…` | 4 | +0.030 | +0.017 | 75% | +0.026 | +0.009 |
| 3 | Dustxbt | `FzVQSzj8…` | 22 | +0.028 | +0.013 | 59% | +0.008 | -0.006 |
| Dior | `87rRdssF…` | 4 | +0.023 | +0.012 | 75% | +0.021 | +0.009 |
| ANALYSE DECU COPY | `Fhbh1DTU…` | 17 | +0.023 | +0.011 | 59% | +0.022 | +0.010 |
| Doji bunde | `D4yrdUWH…` | 9 | +0.018 | +0.009 | 56% | +0.003 | -0.004 |
| Lynk | `CkPFGv2W…` | 13 | +0.035 | +0.003 | 46% | +0.030 | -0.001 |
| TIL | `EHg5YkU2…` | 7 | +0.010 | +0.002 | 57% | +0.004 | -0.005 |
| 2 | Latuche | `GJA1HEbx…` | 6 | +0.012 | +0.002 | 83% | -0.004 | -0.012 |
| theo side | `Bi4rd5FH…` | 18 | +0.055 | +0.002 | 39% | - | - |
| Dali | `CvNiezB8…` | 11 | +0.025 | +0.001 | 45% | +0.025 | +0.001 |
| trunoest | `ardinRsN…` | 35 | +0.021 | +0.000 | 46% | -0.006 | -0.016 |

Decu's first buy is a signal (+0.08 at +1, 53% wins) even though copying Decu's exits loses (-0.14): the money is in
selling into the crowd within seconds, not in holding through Decu's scale-out. Three trades behind (+3) most of it is
gone, so a KOL copy sniper lives or dies on landing in their slot.

What this is worth: Decu makes ~1 qualifying first buy an hour, 0xsolomon ~1, dv ~1. At 0.2 SOL that is a few SOL a
day from the whole shortlist, against ~400 SOL/day of paper signal from the launch trigger. So: a side dish, run as
one capped `ctbot` sniper per wallet (first buy >= 1 SOL, retrigger cooldown so top-ups don't fire), sized up only
after the launch strategy is live and audited. The labeled-KOL group as a whole (the earlier 50 KOLs) is +0.038 per
trade at +1 (bound +0.019, 41 test moments); the full 2,626-wallet group is noise (-0.001).

The nightly job re-ranks this list (`lab --by-wallet`) so wallets that stop working drop out.

## Phase 1 — the entry we can actually get (this week)

Goal: enter *behind* the fast bots instead of racing them, and prove it live.

1. **Trigger = "the fast money is in".** Not "first 2 SOL buy" but: within 3 s of the launch, >= 2 SOL
   bought by non-devs **and** >= 2 distinct buyers, price up >= 10% from the create. The fast bots'
   buys are part of that signal; we're the 3rd–6th buyer, which is where 6Z91 itself makes the most
   per coin (+0.35–0.45 vs +0.29 as first buyer).
2. **Coin filter (from both bots' rules):** dev buy at create between 0.02 and 8.5 SOL; no mayhem,
   no holder-reward coins; tweet-linked first (OKTech `metadataFilter`: twitter contains `/status/`).
3. **Fees:** priority 0.01–0.015 SOL per buy (6Z91 pays ~0.015), tips at minimum. Buys land in the
   creation slot; sells need no fee.
4. **Live proof:** 1 capped buy, verify on-chain (trigger first, then us, then the sell). Then batches
   of 10 at 0.2 SOL, each audited against paper. Pass mark: live >= half of paper per trade.

Needs: ~2 SOL in the SNIPER wallet for the 0.2 SOL batches.

## Phase 2 — the exit that makes the money (week 1–2)

6Z91's quick flips lose (-23 SOL on coins held < 2 s); its profit is in coins held 16 s to minutes.
Our 100%-out trailing stop leaves that on the table.

1. Add its ladder to the simulator and paper-test it against the current exit:
   sell ~35% at +10%, trail the rest 10% from the peak, dump everything on a 1-s pause or at -8%.
2. Add OKTech's remaining exits to the search grid: `autoStop` (a fast +50% in 3 s turns the exits
   off and lets it run), `momentumHold`, `timerExtension`, `stageAcceleration`.
3. Freeze the winner, judge it forward for >= 300 trades, then switch the live sniper to it.

## Phase 3 — more signal, same speed (week 2–3)

Each of these is a lab trigger first; only forward-positive ones go live, each as its own capped sniper.

1. **Fast-bot confirmation.** Keep a live list of the 20 fastest profitable wallets on the tape
   (re-ranked nightly, execution wallets excluded). Trigger: >= 2 of them bought within 3 s of the
   launch. They did the filtering; we buy the consensus one slot later.
2. **Tweet coins.** 71–83% of the bots' coins link a tweet. OKTech's Twitter task + `tweetBinding`
   + `maxTweetAgeSec` buys the moment a watched account's tweet gets its coin; snipetech picks the
   accounts whose coins pumped on the tape.
3. **Stock-quoted launches.** The bots overweight MSFTx / OPENAI / CARDS-quoted coins; the lab runs
   the same trigger per quote asset (OKTech: `pumpFilters.nonSolQuoteOnly` + `pumpAutoSwap`).
4. **Dev snipes with confirmation.** For the few repeat devs whose launches pump (snipetech's SNIPE
   list), OKTech `walletTriggers` buys only after +X% within Y ms, with `web.tracking` following
   their fresh wallets and `blockedCreators` fed from the rinse list.

## Phase 4 — scale to 100 SOL a day (week 3+)

Paper at 0.2 SOL: ~130 signals an hour x +0.17 = ~22 SOL/h gross; live will be a fraction of that.
Scaling is by size, not by more signals:

1. Raise size 0.2 → 0.5 → 1 SOL only while live per-trade stays >= half of paper at the new size.
   The lab's size sweep says the edge grows roughly with size up to ~1 SOL; beyond that our own
   buys move the price and the bots start reacting to us.
2. One wallet is enough (owner's call): the balance only limits how many positions can be open at once, so fund
   SNIPER for the batch size rather than splitting across wallets.
3. Risk rails, fixed before scaling: daily loss limit (suggest 5% of the trading balance), the
   balance guard, and a nightly report that stops a sniper whose 24-h live result falls below zero.

## What we will not do

- Copy 6Z91 / Fhbh1 trade for trade: every replay loses (-0.03 to -0.17 SOL per position) because
  we land behind them and OKTech copies even their failed buys.
- Race for the first buy with 0.045 SOL tips: that race is theirs; our edge is one step behind it.
- Dev-wallet lists as the main strategy: the edge is in one-off creators (repeat devs lost on paper).

## Checkpoints

| When | Question | Pass |
|---|---|---|
| Day 1 | Does the live trigger fire for the right reason and sell? | 10/10 buys after a qualifying buy, 0 stranded |
| Day 2–3 | Does live match paper at 0.2 SOL? | >= +0.08 SOL/trade over 100 trades |
| Week 1 | Does the scale-out exit beat the trailing stop forward? | higher bound over 300 trades |
| Week 2 | Does it hold at 0.5–1 SOL? | >= half of paper at that size |
| Week 3 | Daily live PnL | 20+ SOL/day sustained before pushing to 100 |
