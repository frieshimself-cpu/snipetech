# Roadmap: our own version of what the winning bots do

The bots we studied (6Z91, Fhbh1) win on coins that are 0–3 s old, right after real money shows up.
They will usually be ahead of us on the first buy, so we don't fight them for it. We use their buys
as part of our signal, enter one step behind, and make the money where they make it: on the coins
that keep running after the first seconds, with a scale-out exit. Everything below is paper-tested
first on the server's recording and only then run live in capped batches.

## Copy landing measured live (6 Nov, 05:45 UTC) — the known wallets are the +6 regime

Seven live copy fills (docs/live-recipe.md, "Copy-landing test, 6 Nov"): same or next slot as the leader, but
2–4 trades behind and 18–43 % above the leader's price once the other copy bots have filled. Mean −7 % per
fill. That is the bottom row of the position-sensitivity table below, not the top one, so the "+13–14 SOL a
day" paper number for the top-25 wallets does not apply to this bot. The live test is stopped with nothing
held and nothing running.

What is left that can still work, in order of evidence:

1. A wallet whose copiers are few — measured by the price gap between its buy and the second buy after it.
   The 271-wallet discovery pass found none in 30 h of tape (every quiet-landing candidate was a creator or an
   outlier). The pass keeps running daily; a wallet needs n ≥ 20 positive at +6 before it goes live.
2. Exits on the leader's sell, not on a timer: OKTech's `ctbotCopyExit` was on, but the leader sold before our
   buy had confirmed in three of the seven fills. Nothing to tune there.
3. Being first: landing inside the create slot (the only regime that paid on paper) needs the create tx itself
   as the trigger, i.e. a bundle or a pre-signed buy in the same block — out of reach for OKTech's remote
   execution, as the Hunt test showed.

## Wallet copies at the measured copy landing (6 Nov, 01:50 UTC) — the one positive result

`lab --latency 0 --position +1 --by-wallet`, 26 h of tape, 0.2 SOL a trade, 99 wallets scored, exits
simulated, out of sample. This is the landing the speed tests measured for copies (one trade behind the
leader, same slot) — unlike launch triggers, a KOL's buy is its own transaction, so +1 behind it is a real
place to land. 31 wallets have a positive lower bound; the top 25 together made +12.9 SOL of paper in 26 h.

| wallet | trigger | n | SOL/trade | lower bound | wins | 26 h total |
|---|---|---|---|---|---|---|
| Pengu (KOL) | first buy ≥ 0.1 SOL, hold 60 s | 16 | +0.085 | +0.016 | 38 % | +1.35 |
| 0xsolomon `3GzBLpxT` | first buy ≥ 1 SOL, STALE1s SL-30% | 20 | +0.050 | +0.044 | 95 % | +1.00 |
| Megga `H31vEBxS` | same | 23 | +0.048 | +0.029 | 78 % | +1.10 |
| Decu `4vw54BmA` | same | 29 | +0.036 | +0.028 | 72 % | +1.04 |
| dv `BCagckXe` | first buy ≥ 2 SOL, STALE2s SL-30% | 34 | +0.029 | +0.007 | 50 % | +0.99 |
| theo side `Bi4rd5FH` | same | 34 | +0.027 | +0.011 | 56 % | +0.92 |
| Bean Side `VJSDW6S7` | same | 16 | +0.027 | +0.013 | 69 % | +0.44 |
| Washy dev, Cupseyy, 0xsolomon `DEdEW3SM`, clukz, Limfork, Art diddy bundle | ≥ 1 SOL | 4–9 each | +0.045…+0.13 | > 0 | 80–100 % | +0.2…+0.8 each |

At the next-slot landing the same wallets fall to +0.00…+0.015 (see live-recipe.md), so **the copy edge also
lives in the same-slot landing**. That landing was measured on five copies of one wallet (speed test, 5 Nov);
it is the assumption to verify before any size: a live copy test on the four n ≥ 20 wallets at 0.05 SOL, with
every fill checked on the tape for its slot relative to the leader's buy. Expected if the landing holds: about
+0.04 SOL per trade, ~100 copied buys a day across the list at 0.2 SOL, i.e. 3–5 SOL a day of paper — a
real but modest product. Not started: needs the owner's go.


**Day-2 refresh (6 Nov 04:40 UTC, 29.3 h of tape, +1 behind):** 112 wallets scored, 40 with a positive lower
bound, top-25 total +14.0 SOL/day paper at 0.2. The five live candidates held or improved: 0xsolomon
`3GzBLpxT` n 26 +0.052 (lower +0.046, 96 % wins), Gfree n 20 +0.070 (lower +0.043), Megga n 23 +0.048,
Decu n 30 +0.037, Pengu n 16 +0.085. New strong rows: Cupseyy n 10 +0.103 (lower +0.065), 0xsolomon
`DEdEW3SM` n 12 +0.074, dv n 47 +0.023. The live copy-landing test on the five started 04:36 UTC.

**Sensitivity to the position inside the slot (6 Nov, 02:50 UTC).** Our own 11 speed-test fills (5 Nov 04:30–05:18)
landed in the same slot as the last big buy ahead of them in 9 of 11 cases, with 0–3 trades in between. The same
per-wallet ranking at +6 trades behind the leader (a conservative bound) keeps far less: 8 wallets with a
positive lower bound instead of 31, top-25 total +5.5 SOL/day instead of +12.9.

| wallet | +1 behind | +6 behind |
|---|---|---|
| 0xsolomon `3GzBLpxT` (n 20) | +0.050 | −0.005 |
| Megga (n 23) | +0.048 | +0.014 |
| Decu (n 29) | +0.036 | +0.003 |
| dv (n 34) | +0.029 | +0.001 |
| Bean Side (n 16) | +0.027 | +0.015 |
| Pengu, KOL hold 60 s (n 16) | +0.085 | +0.071 |
| Gfree, KOL hold 60 s (n 18) | — | +0.033 |

So the copy edge lives in the first few trades after the leader's buy. The live test must therefore record,
for every fill, how many trades sat between the copied buy and ours; the answer decides the list and the size.
The KOL "first buy, hold 60 s" rows (Pengu, Gfree) are the least position-sensitive and the first to test.

## Inside the create slot — what the paper edge actually was (6 Nov, 01:05 UTC)

Our-rule launches (a non-dev buy ≥ 2 SOL within 1 s, normal coins), last 8 h of tape, split by where that
buy sat. "+1" is the paper entry: right behind the trigger buy, inside its slot. "next slot" is the first
trade of the following slot, the best any outside sender can do. Mark to market after 120 s.

| class | n | +1 entry (paper) | next-slot entry | price gap +1 → next slot | spike ≥ 30 % after next-slot entry |
|---|---|---|---|---|---|
| big buy inside the create tx | 25 | +715 % | +14 % | +11 % | 17 % |
| big buy in the create's slot, own tx | 864 | +67 % | −17.5 % | +76 % | 42 % |
| big buy in a later slot | 79 | −9 % | −13 % | +7 % | 35 % |

The whole edge is the **+76 % that happens inside the trigger's own slot**: after the ≥ 2 SOL buy, the rest
of the bundle or burst keeps buying before the slot closes, and by the first trade of the next slot the
price has already moved 76 % on average. Paper bought at +1 and sold into that move; a real sender buys
after it and holds the bag. 864 of 968 cases (89 %) are this class.

What "being inside the slot" would take:

- **Be the bundle.** The buyers inside the create slot are the launch's own wallets or bots co-submitting
  with the create (Jito bundle to the create's leader). Joining that means being the launcher or paying for
  a slot in someone's bundle — a different business (and the one the "Mayhem" product sells).
- **Same-slot landing from outside.** A tx seen in the shreds of slot N is already executed; to land in slot N
  you must build, sign and deliver a tx to the same leader before it seals the block, i.e. well under 400 ms
  including network, and the leader must still accept it. Colocated bundlers do this sometimes; it is not a
  reliable entry, and every competitor with the same idea is in front of us.
- **The 11 % class** (big buy inside the create tx, n = 25, +14 % next slot) is the only slice where a
  next-slot entry is positive, and it is 25 launches in 8 h with a 17 % spike rate — too thin to build on.

Conclusion: the ≥ 2 SOL first-second trigger is not a strategy an outside sniper can run. Launch triggers are
closed. What remains measurable from our seat: wallet copies at the measured copy landing (running now), and
anything that fires *after* the create-slot move has already happened (momentum-type entries, all negative so
far at the next slot).

## Where we stand (5 Nov, night) — the landing reality

Live with Hunt approved (21:13–22:40 UTC, 13 fills at 0.02 and 0.2 SOL) settled the question the paper
engine could not: **where our buy lands**. OKTech lands 1–2 slots after the create. The paper edge
(a586d8164a, +0.129/trade) was computed at position +1 *inside the trigger's slot*, right behind the
≥ 2 SOL buy. When that buy is part of the create bundle — most of the triggers — nobody lands there; we
land after the whole bundle, at the top, and bundles dump within seconds (BETRAYAL −56 %, ALON −69 %,
HOUSE −40 %). The one winner, CAT (+265 %), was a dev-heavy launch the new thresholds would skip.

Measured on the tape at the realistic entry (first trade of slot +2):

- every bundle class is negative 30 s and 120 s later (−8 … −26 %), 40–50 % dump > 30 % within 30 s;
- the only reachable slice — the big buy arrives in a *later* slot than the create, quiet create slot —
  is −23 % at 120 s (n = 217, 12 h), 55 % dump, but 38 % spike ≥ 20 % first: a trailing exit might
  still carry it; the lab (`--latency 1|2 --position start`) decides, with exits simulated.

Consequences for the plan:

1. **Phase 1 changes meaning.** "The entry we can actually get" is slot +1 at best, for anyone, OKTech
   or our own sender. Every strategy is re-ranked at `--latency 1 --position start`; nothing computed at
   latency 0 counts as evidence any more.
2. **Hunt is usable but one-shot and coarse.** It fires once per start (`oktech cycle` re-arms), sums the
   creator's buy, takes whole-number thresholds only (minTotalSol 3 / maxDevBuySol 1 / maxUniqueBuyers ≤ 2
   is the closest expression of "a lone big buy on a quiet launch"), and cannot see non-SOL quotes (6010
   failures; the account filter would fix it). Its sells are not reliable (HOUSE never sold; CAT's trailing
   stop did not fire): `snipetech sweep` with SNIPER_SECRET on the server is the safety net.
3. **The wallet-copy angle is unaffected** by the bundle problem (a KOL's buy is its own tx, and the
   measured copy landing is +1 same slot) and is re-ranked at the realistic landing next (`--by-wallet`).
4. **Live is halted** until a variant is positive at the realistic landing with n ≥ 200 and a positive
   lower bound; then 0.2 SOL, cycled, with a daily loss stop, and a fresh go from the owner.

## Where we stand (5 Nov, evening)

- Recorder: every pump.fun / LaunchLab trade with exact block position, 24/7 on the server (LaserStream).
- Paper strategy `a586d8164a` (frozen 13:05 UTC, judged only on newer data): buy right after a non-dev puts
  >= 2 SOL into a coin within 1 s of its creation, normal coins only, SL -15% / trailing 10% / 5 min:
  **+0.133 SOL per 0.2 SOL trade, 629 trades over 4.8 h, 49% wins** (bound +0.120). The >= 5 SOL version
  `3fe27cc4b7`: +0.181 over 204 trades (bound +0.162). The lab over the whole 19.6 h tape agrees (+0.121 / +0.192 on
  the newer 40%).
- OKTech trigger NOT solved: live probes (19:46–20:12) show `hunt` and `crowdBuys` do not gate; with monitorProgram the sniper buys every launch (see live-recipe.md).
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

1. ~~Add its ladder to the simulator and paper-test it against the current exit~~ Done 5 Nov evening: 24 ladder
   variants (sell 35–50% at +10/+20%, trail 10–18%, stop -8/-15%, with and without a 1-s dead exit) are in the lab's
   exit grid. On 20.3 h of tape none beat the plain trailing stop for any trigger (the freeze found nothing new), so
   the live exit stays SL -15% / trail 10% / 300 s. Re-check when the tape is a few days long; the ladder helps 6Z91
   because it holds minutes, and our edge is in the first seconds.
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
