# Roadmap: our own version of what the winning bots do

The bots we studied (6Z91, Fhbh1) win on coins that are 0–3 s old, right after real money shows up.
They will usually be ahead of us on the first buy, so we don't fight them for it. We use their buys
as part of our signal, enter one step behind, and make the money where they make it: on the coins
that keep running after the first seconds, with a scale-out exit. Everything below is paper-tested
first on the server's recording and only then run live in capped batches.

## Where we stand (5 Nov)

- Recorder: every pump.fun / LaunchLab trade with exact block position, 24/7 on the server.
- Paper strategy (frozen 13:05 UTC, judged only on newer data): buy right after a non-dev puts
  >= 2 SOL into a coin within 1 s of its creation, normal coins only, SL -15% / trailing 10% / 5 min:
  **+0.171 SOL per 0.2 SOL trade, 342 trades, 55% wins** (bound +0.153).
- OKTech trigger solved: `hunt` strict mode waits for that buy (verified on-chain, 17:02 UTC).
- Sells are safe: snipers cap their own buys and keep selling; the guard only stops in an emergency.

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
2. Split across 2–3 OKTech wallets (`additionalSnipers`) so several positions can be open at once
   without one wallet's balance capping the batch.
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
