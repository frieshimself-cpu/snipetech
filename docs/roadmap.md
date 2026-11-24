# Roadmap: our own version of what the winning bots do

The bots we studied (6Z91, Fhbh1) win on coins that are 0–3 s old, right after real money shows up.
They will usually be ahead of us on the first buy, so we don't fight them for it. We use their buys
as part of our signal, enter one step behind, and make the money where they make it: on the coins
that keep running after the first seconds, with a scale-out exit. Everything below is paper-tested
first on the server's recording and only then run live in capped batches.

## 24 Nov, 03:00 UTC: the edge hunt of the night (the owner: "Keep looking for edges"), and Inertia's answer

- **How it ran.** Five background agents, one hypothesis each, measured on the tape (narrow read-only SQL) and scored
  with the lab at our live landing, choosing on the older 60 % and judging on the newer 40 %; every positive claim then
  went to a second agent told to refute it. (Two Workflow calls for it were lost to restarts of this session's worker
  while they waited for approval; the agents ran without one.)
- **Scouts ahead of the big wallets: no edge.** Wallets that buy the big four's coins seconds before them exist, and
  rank stably (Spearman 0.79 out of sample), but they are Decu's front-runners: YS6Aa79H… and FBPEacRh… (one operator)
  come in before 15 to 23 % of his first buys of 1 SOL or more, paying 0.024 to 0.047 SOL of priority a buy, and sell
  into his buy about 1 s later. Copied at our landing they lose on the newer 40 %: -0.020 to -0.033 SOL a copy at 0.2
  SOL, -0.06 to -0.12 at 1 SOL (jobs 20261124-022502-89a91a, -022917-93ac79, -023707-6a0eba); breaking even needs 45-50
  % of copies followed by a big wallet, against 16-32 %.
- **New quiet leaders: none that holds.** 8NdJBk… paid on paper at 1 SOL (+0.034 a copy on the newer part, jobs
  20261124-021619-f6de83, -021941-74fa96), but the check refuted it: it was picked by qualify's size score of 22 Nov,
  which read the same days, and after the pick its copies made about nothing (+0.28 SOL over 25 trades at +2x, -1.13
  without the best three; jobs 20261124-022934-3d20be, -023303-4de9a5, -023513-3d162c, -023857-5a65f1). Qualify's own
  state: nothing qualified since 19 Nov 14:46 (0 of the latest 29,829 verdicts), 13 forward checks pending and none
  passed; of the stored 5 SOL size scores, 5,568 of 7,307 were computed at the older, closer place (2nd in line, a 0.2
  SOL bid) and should be recomputed before anyone reads them as passes.
- **A higher ceiling on our own fill: a candidate, under check.** The live copy rule (Cented and Yugi, 2 SOL or more,
  12 s hold, copy exit) with our fill allowed up to 75 SOL of market cap instead of 64.6: +0.55 to +0.99 SOL a day on the
  newer 40 % at 0.2 SOL (lab 20261124-022808-56dea8); at 1 SOL and +3x, +1.75 to +3.89 (75) and +4.46 with Cented's and
  Yugi's 2-4 SOL buys at 80 (lab 20261124-023125-43c5b6). Every newer day positive; Cented carries about 80 %. The
  8NdJBk check saw the same on the earlier week for Cented at 1 SOL: a 100 SOL ceiling +10.47 against +5.80 at 64.6 at
  +2x, +5.03 against +4.00 at +3x (both newer lower bounds -0.0007). Hour of day, SOL or price rise ahead of us,
  consensus between the four, repeats and coin kind all failed on the newer part. **CHECKED (03:10 UTC, not refuted,
  smaller than claimed):** the ceiling curve at 0.2 SOL is one step between 64.6 and 70-75 and then flat, in both weeks
  (+3x, SOL a day, older / newer week: 64.6 0.29 / 0.34, 75 0.46 / 0.63, 80 0.47 / 0.65, 100 0.38 / 0.64; jobs
  20261124-025148-77514e, -025003-5dd2b9, -024248-400855, -024525-0787eb); our real fills land at +3x (the 59 live sends
  placed on the tape: median ratio of fill price to the lab's +3x fill 1.000), so the gain at the real landing is +0.16
  to +0.29 SOL a day at 0.2 SOL, not +0.44. At 1 SOL, +3x, newer week (job -024723-aec1d3): 1.73 SOL a day at 64.6, 3.47
  at 75, 3.79 at 80. On the test's real moments: 9 of the 49 curve sends the 64.6 ceiling refused would have filled
  under 75, worth +0.31 SOL held 12 s (two winners carry it; +0.10 at 10 s, +0.05 at 8 s, so the gain depends on the
  exit's timing). Separately, 5 sends were refused at a 23.5 SOL ceiling: OKTech's SOL price was wrong after a restart
  of its own (the ceiling is set in dollars). The checker's guess for the live gain at 0.2 SOL: +0.05 to +0.2 SOL a day;
  its advice: test 75 (70 if cautious), several days to tell it from noise.
- **New coin kinds and curve events: no edge.** Mayhem coins are 41 % of launches; pump.fun's agent (BwWK17…) trades
  each one about every 1.4 s for a median 44 s, and its next move is a coin flip whatever came before; all 35 mayhem
  completions ended drained (median 0.39 SOL of virtual SOL). Every kind and event loses at a landing a slot later:
  launches -0.015 to -0.023 SOL a 0.2 SOL trade, after the agent's trades -0.021, the 80-95 % run-up to migration
  -0.023 to -0.047, MIG windows -0.009 to -0.026 (labs 20261124-021936-b98774, -023105-fa863b). For the copy route: a
  mayhem skip on the copy snipers is worth asking OKTech for (fills 3 and 4).
- **Other venues: no edge.** Only pump.fun's SOL curve stays positive at our landing (the big four +0.0047 a 0.2 SOL
  copy on the newer part). PumpSwap breaks even even at the quiet landing; LaunchLab loses; the next ten big wallets
  lose everywhere (-6.7 SOL a day on the curve). Curves priced in other tokens (about 121 of the big wallets' buys a
  day) read +0.41 SOL a day at the quiet landing with an assumed 1 % swap cost, but OKTech's copies of them fail on the
  quote (error 6009) and our own buyer refuses them (lab 20261124-023332-08253a).
- **Inertia answered the owner's application** (24 Nov, on Telegram, "Flint | Inertia": "Could you send your searcher
  wallet please"). Its documentation (documentation.inertia.sh/searchers, read 24 Nov): an API key per searcher, asked
  for on Telegram (t.me/inertiamev) or Discord, four permissions per key; gRPC without TLS on port 11227, Amsterdam
  only; the stream is filtered by up to 1,024 accounts; bids are tips to Inertia's own tip accounts, paid only if the
  bundle lands; **the minimum tip is 45 % of the profit the bundle expects** (behind Cented on paper: +1.90 a 6 SOL
  copy, about +1.0 after it). The owner was told to send only the public address of the sniper wallet, to check Flint
  in Inertia's official group, never to send a key or SOL or connect the wallet to a link, and to ask whether the
  stream carries pump.fun buys sent from trading apps like Axiom. With a key, the first step is read-only: subscribe to
  Cented's and Decu's addresses for a few hours and count how many of their buys appear before they land. The key goes
  on the server, never into chat.

## 24 Nov, 01:10 UTC: would a fast sender of our own reach the money? No (the check of the forwarded feeds analysis)

- **The question.** The owner forwarded an analysis (23 Nov ~22:00): buyable feeds (Yellowstone gRPC, Jito
  ShredStream) and landing paths (private relays, co-location) beat most traders, so buy a feed, send blind Jito
  bundles with tight slippage, then co-locate. A workflow (wf_b9d0e0a5-29e, 23 Nov 22:0x-23:36) checked it against our
  own record and tape and current vendor facts, then had an adversarial reviewer correct the answer (verdict "needs
  fixes"; the numbers below are the corrected ones).
- **The money sits right behind Cented's and Decu's first buys of 1.9 SOL or more:** paper, 6 SOL a copy, the place won
  every time, a 0.003 SOL tip: +1.90 SOL a copy behind Cented, +1.68 behind Decu; one buyer back +0.69, two back +0.22
  (Cented; Decu's one-back and two-back never scored at 6 SOL).
- **Speed rarely wins that place.** On 56 % of those buys someone stands within two transactions after the leader's.
  About half of them ride in the bundle Lunar (Axiom's sender) builds; most of the others pay 0.2 to 1 SOL in fees
  outside Jito and probably see the trade early (inferred from order, fees and failures, not shown). On the other 44 %
  nobody stands within 2 places: a sender landing 10 places back would be right behind on 27 % of the moments (37 %
  behind Cented), but no reacting wallet lands that close every time.
- **Where reacting senders land:** the fastest reacting wallets a median 42 to 58 block places behind the leader's buy;
  OKTech about 68 (15 to 177); the few reacting Jito bundles seen (about 13, in the route check's 26 blocks, not a random
  sample) 65 to 328. Jito rejects any bundle that puts a transaction carrying its don't-front marker anywhere but first,
  and the leaders' buys carry it: a bundle right behind the leader needs the leader's signed transaction, which only
  order flow gives.
- **What our own buyer could add without a seat, an estimate and not a lab result:** if it landed like the best
  reacting wallets (unmeasured: no buy of ours has been sent, and the tape keeps no receive time), 0.05 to 0.17 SOL on a
  6 SOL copy behind Cented, before tips and failed sends; 6 SOL is 30 times the standing yes, and big live copies have
  run 0.18 to 0.40 SOL each below paper. **On launches:** the first place an outside sender can take makes about nothing
  at 12 s and loses 5 to 7 % at 30 and 120 s (one six-hour window, our own price impact left out).
- **Vendor facts that changed (read 23 Nov):** Jito shut ShredStream down on 5 Sep 2026; raw-shred feeds now cost $450
  to 1,500 per IP a month (decoded shreds from $199), about 5 to 12 ms ahead of a processed gRPC feed like our
  LaserStream at the median. Slots are about 200 ms since Alpenglow (21 Oct; measured 215 ms and about 500 transactions
  a slot on 23 Nov). A Jito bundle that fails or loses its auction pays nothing, except when its block is skipped and
  its transactions land outside the bundle. Order-flow sellers: Harmonic (backrun only, KYC/KYB, a strategy family for
  copy trading, its validators' slots only), Inertia (searcher API in early alpha, key by Telegram or Discord,
  Amsterdam only), Flowra (open auction of validators' pending transactions, searchers register keys at its portal,
  Frankfurt and London, live since 21 Aug); Lunar sells no public product for the place inside its bundles.
- **What follows.** No live test of a reacting buyer of our own (the check's advice: no; nothing was asked of the
  owner). The buyer built tonight (branch work/own-buyer, merged 24 Nov: docs/own-buyer.md) is kept for a seat
  (`--behind`). The seat applications stand as the only route to 100 SOL a day, even on paper; nothing is likely by 29
  Nov. Meanwhile the 0.2 SOL OKTech copy runs (fill 9 at 00:47, +0.077).

## 23 Nov, 22:05 UTC: the place right behind Axiom's users is filled by the service that lands their trades (Lunar)

- **The owner's contact (Discord, shown 23 Nov ~21:50 UTC):** "axiom leaks orderflow through lunar", "pump through
  helius"; then "their edge comes from seeing events faster than people… they have private feeds that you cannot
  buy… they're backroom deals".
- **Checked on the chain** (the 26 blocks the 16:30 route check read, cached in the session's scratchpad
  route/verify/cache): every Axiom buy of Cented, Decu and Yugi in them, **24 of 24**, pays its tip (0.00001 to 0.015
  SOL) to one of the 100 accounts that Circular published as Axiom's bribe accounts landed by Hello Moon's Lunar
  Lander (github.com/Circular-fi/axiom-lunar-addresses; DefiLlama counts their tips as Lunar's fees,
  dimension-adapters fees/lunar-lander.ts, "confirmed on-chain to be landed by Lunar"). 78 of all 104 Axiom
  transactions in those blocks do; theo side's GMGN buys 0 of 4; none pays Lunar's ten public tip accounts.
- **So the bundles of three of 16:30 are Lunar's.** The leader's Axiom buy, then Lunar's own transaction (its fee 1 %
  of the leader's tip), then the buyer behind. The service that lands Axiom's users' trades is the one that places a
  buyer behind them, before any validator sees them. One operator holds 88 of the 103 in-bundle places (3.457 SOL
  buys from 47 wallets); 8r6N8w holds 9 with bundles of its own. DefiLlama: "what Lunar pays validators and order-flow
  partners happens off-chain". This is the private seat the contact describes; it has no public price and no form.
  A Lunar API key (a ticket on Hello Moon's Discord) lands our own transactions; it does not show anyone else's.
- **What it means for 100 SOL a day by 29 Nov: no route we can verify reaches it.** The 200-a-day paper number (21:15,
  below) needs that seat. A private deal for another app's users' trades is not something this repo builds on.
  Harmonic's open searcher network is the one legitimate door left, and at best it places us behind Lunar's buyer
  (one trade back: +0.69 SOL a copy behind Cented on paper, +244 SOL in the week), only in the slots its validators
  lead, after KYC and a 7 to 14 day pilot: not inside six days.
- **What runs:** the OKTech copy behind Cented and Yugi (about 1 to 2 SOL a day on paper; live about half so far):
  the 0.2 SOL test to 09:11 (no fill since the 19:59 restart), then the two-buys check, then the 1 SOL day.
- **The owner, ~22:15 UTC: "We need order book leaking."** The plan given in answer, three tracks at once:
  1. **The seat right behind Cented's and Decu's buys** (paper: about 115 to 175 SOL a day if won on every big buy,
     between a 0.6 SOL and a 0.003 SOL seat price), bought from whoever holds the flow and is allowed to sell it: the
     owner applies (only the owner can: identity) to Hello Moon (a Discord ticket asking for a Lunar Lander key and
     backrun placement on the flow it lands), Inertia (the searcher form: backruns, order-flow auction) and Harmonic
     (an ID check and a 7 to 14 day pilot). This session builds our own buyer meanwhile (a pump.fun curve buy from a
     landed template, a Jito bundle client that puts our buy right behind a transaction a seller hands us, caps,
     dry run by default; workflow wf_c19b879a-779, 22:2x); nothing is sent before the owner's yes to a 0.1 SOL test.
  2. **Money this week:** the OKTech copy route above.
  3. **Our own fast buyer without a seat:** only if the check running now (wf_b9d0e0a5-29e) shows it lands closer
     than OKTech's 2 to 4 buyers behind Cented; then a 0.1 SOL test with the same buyer, on the owner's yes.
  Honest bottom line given: 100 SOL a day by 29 Nov happens only if a seller grants the seat within two or three
  days.
- **The line, as told to the owner:** we buy seats behind other people's trades from whoever is allowed to sell
  them, and nothing else. Never in front of a user's trade: there our profit is the user's worse price; the leaders'
  transactions carry Jito's don't-front marker; Jito closed its public mempool in 2024 over sandwiching. Never flow
  from someone not allowed to sell it: that is taking part in a breach, and such a deal can vanish overnight. All the
  money measured sits right behind the buy, which is what the legitimate sellers offer.

## 23 Nov, 21:15 UTC: the owner's goal, 100 SOL a day in six days: the one route that reaches it, and what each step needs

- **The owner, 23 Nov ~21:05 UTC:** "6 days left until we want 100 SOL a day. Do everything possible in ur power to get
  there."
- **What we have.** The small-coin copy behind Cented and Yugi through OKTech (copies land a few buyers behind the
  leader): on paper at 1 SOL a copy, 3rd in line, with the re-arm gap, about +5 SOL a week (19:15 labs), about 1 SOL a
  day; at 0.2 SOL live since the 15:34 restart about half the paper (four fills). Size does not grow it: at 1 SOL our
  own buy moves these coins' price 4.6 %, and the 3.5 SOL night of 21-22 Nov lost 5.2 SOL on six copies. So this
  route makes about 1 to 2 SOL a day at best this week.
- **The one route on paper at about 100 a day** (22 Nov, below): every first buy of 1.9 SOL or more by Cented and
  Decu copied at 6 SOL **right behind** the leader: +1.32 and +1.07 SOL a copy with a 0.6 SOL bid charged, about 650
  copies a week. Two places further back the same copies lose. Nobody reaches that place by reacting to the landed
  buy (OKTech never has: 0 of 13 tipped buys). The buyers who stand there have the leader's transaction before it
  runs: inside Jito they ride in the bundle the leader's platform's sender builds (103 of 200; 23 Nov 16:30), and
  outside Jito they land right behind with large fees (8r6N8w 30 times, 0.72 SOL; a "Harmonic8SoAf…" signer next
  to the leader in 4 of 20 sampled slots). **New at 21:10:** the programs the in-bundle buyers trade through are not
  Axiom's: Axiom's FLASHX8D… is upgraded by AxDepcBgx…, the 3.457 SOL operator's L2TExMFK… has no upgrade
  authority (frozen), and the 6.42 SOL buyer's 5kZmvKba… is upgraded by G6iAiWP7… (read off the chain). So the seat is
  not Axiom's copy trading; it is sold to outside traders, by the networks that sell exactly this place: Harmonic's
  searcher network (validator side; KYC/KYB, a 7 to 14 day pilot) and Inertia (apps send their flow; a 50 ms
  auction; searcher API by application).
- **What the place is worth with a seat's small tip (labs of 21:16-21:19 UTC, the tape copy of 20:01, the week to 23
  Nov 20:01; every first buy of 1.9 SOL or more, copied at 6 SOL, sold at 12 s or on the leader's half sale, a 0.003
  SOL tip charged instead of the 0.6 SOL bid above; paper, the place assumed on every moment):** right behind (+0)
  Cented +1.90 SOL a copy over 353 copies (+672 SOL in the week; the newer 40 % +1.83), Decu +1.68 over 321 (+538;
  +1.82), theo side (Bi4rd5FH…) +1.03 over 166 (+171; +1.20): **about +1,380 SOL a week, about 200 a day**, before
  any competition for the place. One trade further back Cented makes +0.69 a copy (+244 in the week), two back +0.22
  (+77; the newer 40 % +0.09). At 6 SOL our own buy moves these coins' price 29 % at the median: the profit is the
  later buyers' money, so the place and the crowd behind it are the whole edge. Jobs 20261123-211654-04467b,
  -211737-0d5e42, -211738-128568, -211838-92fa2b, -211839-98b055. (The same moments at a seat taken only part of the
  time, or at a higher auction price, scale down from there: at a quarter of them, about 50 a day.)
- **How each network admits a searcher (read 23 Nov 21:30 UTC).** Harmonic (harmonic.gg/searcher-network; public beta
  since 5 Nov): apply, KYC/KYB and AML checks, then a 7 to 14 day pilot on sandboxed validators before admission; an
  open backrun-only auction, no exclusive access, every backrun logged with its compliance server; it covers the
  slots of the validators that opted in, not every slot. Inertia (inertia.sh): searcher API "in early alpha",
  streaming order flow from Amsterdam, a 50 ms auction, the winner placed behind the original flow as one bundle;
  access through its documentation, Discord or Telegram, no public form. Flowra (flowra.wtf): another Solana order
  flow auction, searchers register and bid through a documented API in 200 ms rounds, onboarding validators. None
  says which apps' flow (Axiom's, GMGN's) it carries; only a searcher seat would show how many of the leaders' buys
  reach us, and at what price.
- (CORRECTED 23 Nov 22:05 UTC: the leaders' Axiom buys are landed by Hello Moon's Lunar Lander, which places its own
  buyer right behind them inside its bundle; no network's application reaches that place, and Harmonic's at best
  places us one trade back, in its validators' slots, after a 7 to 14 day pilot. See 22:05 above.)
  **So 100 a day needs the owner's application to those networks** (an identity or a company, their terms, their
  price for the place, which the 0.6 SOL bid of the paper may not match). Only the owner can apply. With a seat, our
  side is a new buy path the repo does not have (it signs only sales today): a pump.fun buy builder pinned by a test
  on a landed buy, a bundle client for the network, and rule 1's caps and guard; a few days of work, built once the
  owner says go. Without a seat, the week's ceiling is the OKTech route's 1 to 2 SOL a day.
- **What runs meanwhile:** the 0.2 SOL test to 09:11; then the two-buys check (0.1 SOL a copy); then the 1 SOL day
  with the evening's changes (Yugi without the copy exit, two buys a start if the check passes, the run's own sale 15
  s after the buy), when the wallet holds what it needs (about 5 SOL at the least, docs/live-recipe.md); the labs of
  the place right behind at 6 SOL with an in-bundle tip (0.003 SOL) instead of the 0.6 bid (21:10). The server's
  agents have had no API credit since 22 Nov 23:01 and do no research until it is topped up.

## 23 Nov, 16:30 UTC: the place right behind the big wallets is bought with their own transaction (the route check)

- **The question** (22 Nov 07:50, below): another wallet's buy stands within 3 block places after most first buys of
  Cented, Decu and theo side, and a buy sent once the leader's buy is seen lands far later. So how do those buyers get
  there? The new `route` command (379832b, deployed 16:08) answers it from Jito's public bundle explorer, which says
  for each landed transaction the bundle it came in, and for each bundle its transactions, who paid its tip and its
  place in the block (no key, no Helius credits).
- **The answer** (job 20261123-160910-658404: the newest 200 of the 261 first buys of 1.9 SOL or more by Cented,
  Decu, theo side and Yugi between 21 Nov 03:40 and 23 Nov 08:31 UTC that had another wallet's buy of 0.1 SOL or more
  within 3 places after; the leaders' own companions apart).
  - On **103** the closest buyer rode **inside the bundle that carried the leader's buy**, and paid its tip (101 of
    103). (Corrected at 17:00 by an independent check of every row, scratchpad route/verify: this said the buyer held
    the leader's signed transaction and wrapped it in its own bundle. The chain shows otherwise. In the 91 bundles of
    three, the transaction between the leader's and the buyer's belongs to the leader's own landing service: its fee
    less 5,000 lamports is exactly 1 % of the leader's tip to Axiom's sender on 73 of 73, exactly half the leader's
    tip to GMGN on 18 of 18, and the same 26 keys tip the leaders' own bundles. So Axiom's and GMGN's senders build
    these bundles, and the buyer pays the tip to ride in them; whether a buyer ever held the leader's transaction
    cannot be read from the chain. Every leader transaction carries Jito's don't-front marker: others may ride behind
    it, nobody in front.)
  - **88 of those 103** are one operator's buys of exactly 3.457 SOL, from 47 wallets, always 2nd behind in a bundle
    of three (in the ones read whole: the leader's buy, an empty transaction of a third key, its buy through the
    program L2TExMFK…), at a median tip of **0.003 SOL**. 8r6N8w (the +100 SOL a week bot of 22 Nov) won 9 at 1st
    behind with tips of **0.40 to 1.08 SOL**.
  - **0** took the place with a bundle of their own. **97** stood there outside Jito (8r6N8w 30 times, FfW5e8 8,
    6Z91 7, 9AGtz3 5): none of them paid a Jito tip, and the explorer finds 95 to 98 % of the Jito bundles in those
    slots, so this holds. 12 of the 97 stood behind the leader's landing service's own transaction, the same
    three-part package landed outside Jito. Outside Jito the place right behind is bought with large fees: median
    0.355 SOL, 8r6N8w's 0.72.
  - The leaders do not send through Jito themselves: their buy was in a Jito bundle on 109 of the 200, 103 of them
    a follower's. Cented, Decu and Yugi buy through one trading platform's program (FLASHX8D…, each buy paying about
    0.004 SOL to one of a rotating set of accounts), theo side through GMGN's (GMgnVFR8…). Yet Yugi has almost nobody
    right behind (3 of 75 moments): the bundling buyers choose whom they follow.
  - Our own copies (OKTech's five buys behind Yugi today, read off the chain at 16:40) landed 36 to 277 block places
    after his buy in the same block, none in a Jito bundle, with their 0.002 SOL tips paid to a different landing
    service each time (AsTRAD…, FLaShB…, 4ACfpU…, noz…; fill 1 paid none). OKTech reacts to the landed buy, as the
    place says; it does not carry the leader's transaction.
- **What it means.** The place right behind exists, has a price, and the leaders' own platforms fill it: inside Jito a
  buyer rides in the bundle Axiom's or GMGN's sender builds around the leader's buy (tips median about 0.003 SOL;
  8r6N8w pays 0.40 to 1.08 in two-transaction bundles of its own with the leader's buy), and outside Jito it costs
  about 0.36 SOL of fees. On paper the place is worth about +1.1 to +1.3 SOL a copy at 6 SOL (22 Nov, below). The
  way in is a seat in the leader's platform's bundle, or the leader's transaction before it runs; nothing faster or
  higher-bidding alone reaches the place. No public feed sells a transaction before it lands since Jito closed its
  mempool in March 2024 (reports/Solana copy trading tools 2026.md). How a buyer gets a seat in Axiom's or GMGN's
  bundles is the next question.
  OKTech question 30 asks whether a copy can go out as such a bundle (docs/oktech-playbook.md). Any sender of our own
  stays the owner's decision; this is the fact it waits on, not a plan.
- **Who sells it (read at 16:55 UTC).** Cented, Decu and Yugi buy through Axiom's trade program (FLASHX8D… is "FL
  Axiom Trade Program" in Orb Markets' program list, orbmarkets.io/programs), theo side through GMGN's. Two order-flow
  networks sell exactly this place: Harmonic's searcher network (harmonic.gg/searcher-network, public beta since 5
  Nov 2026: "when your validator leads, Harmonic forwards eligible transactions to vetted searchers as they arrive,
  before they execute"; a batch auction; "the winning bundle lands with the user's transaction first and the
  searcher's trade directly behind it"; backrun only; KYC/KYB and AML checks and a 7 to 14 day pilot; costs not
  published) and Inertia (inertia.sh: "Validators and apps send transactions and bundles to Inertia", a 50 ms
  auction, the winner "lands behind the original flow as one bundle"; its searcher API in early alpha, by
  application). The 103 bundles ran on 68 different validators, and their middle transaction is the leaders' own
  senders' (above): the seat is filled on the apps' side. Whether Axiom's and GMGN's senders sell it through such a
  network, or another way, is not known (a signer "Harmonic8SoAf…" sat next to the leader in 4 of 20 sampled slots
  outside Jito: unverified). What the place would take: a buyer of ours admitted to the seller (an identity check of
  the owner or a company), a client that bids for the paper's moments (first buys of 1.9 SOL or more by Cented, Decu
  and theo side), and a test of what the place earns at the prices seen. That is the owner's decision.
- **The wallet finder has not found a wallet that pays after its checks** (read off the verdicts and the copy record
  at 16:55 UTC; the new `forward-tally` keeps this count). Only 9 wallets ever passed qualify's forward check (17
  verdicts, 11 to 19 Nov; none since 19 Nov 14:46 UTC; three passed in the same moment and two others together, so
  likely two packs among them). All 9 lost after it: 965 later paper copies at 0.2 SOL, about
  -10.3 SOL in all, -0.011 a copy, the best of them -0.34 SOL. The latest verdicts: 2 qualified, 119,439
  candidates, 14,422 retired. The one rule that holds (the small-coin copy behind Cented, Decu and Yugi) came from
  labs, not from qualify. So arming qualify's picks stays off; its runs (36 to 70 minutes of the machine each) have
  found no wallet that pays yet.
- **The paper against the live test** (lab 20261123-160934-bd53ff, the tape copy of 13:30): Yugi's fill 2 made
  +0.099 on paper and +0.101 live; the ten refused sends fall at the same moments on paper and live; fill 1 made
  +0.125 on paper (held 12 s) and +0.023 live (OKTech sold at 3 s when Yugi moved the coin into his own wallets,
  OKTech question 28). On the copy of 16:30 (lab 20261123-174151-644eb8): fill 3 (a mayhem coin) -0.147 on paper, -0.140 live; fill 4 -0.131, -0.115. Fill 5 (17:30, behind Cented, +0.0315) was sold at 4 s when Cented moved 79 % of his coins into three other wallets, as fill 1 was on Yugi's transfers. Two gaps the paper does not carry: OKTech's copy exit fires on such transfers (the paper sees no sale and holds 12 s), and after each fill the live sniper waits 20 to 45 s to be armed again, so it misses its wallet's next buys in that time (Yugi's at 14:40:12, -0.046 on paper), which the paper takes. Over Yugi's last 72 hours the paper's copy is about flat (34 moments, -0.0008 SOL a copy at 0.2 SOL).

## 23 Nov, 11:10 UTC: the whole system audited and fixed in three rounds; one rule holds in both weeks under the filter the live snipers use; it runs as a 0.2 SOL test

- **The audit (the owner, 22 Nov: "Go through every single thing and find as much as you can to fix or improve").**
  Round 1: fifteen areas, each read by one auditor and checked by one skeptic against the code and the server's
  data; 115 findings confirmed (1 high, 22 medium, the rest low), about 105 fixed test-first by thirteen fixers, each
  branch reviewed by a second reader; 171 commits, deployed 23 Nov 01:05 UTC (3b412e0). Round 2 took what round 1
  left: the live path, the agents' texts, the tape's disk rule and job records, the docs (about 70 commits). Round 3
  took what round 2's reviewers left on the path that holds the owner's SOL, and what the smoke tests showed.
  Deployed 23 Nov 07:10 UTC (9197324).
- **What was wrong, in one line each.** The owner's own coins, airdrops and hand trades read as the bot's in a dozen
  places, and a copy at fees no sniper was sent read as the owner's: one rule now everywhere: a buy is the bot's by
  the fees its snipers pay, or when it stands at most 3 slots right behind a buy of a wallet a sniper of ours follows
  AND spent exactly that sniper's size. /stop read the wallet once and could stop a sniper whose buy was on its way:
  it reads twice and looks at the chain. A machine restart left a dead run's snipers armed with nobody watching: the
  round takes them up. A restarted recorder could cut the tape from 14 days to 6 for good, and its disk rule would
  have walked it down by itself (the one high finding): both fixed. A refused send kept its sniper out a minute or
  more: 20 to 35 s now (seen live). The lab counted several buys in one coin as independent, so its lower bounds were
  too kind. The dashboard showed rules that were dropped. A failed Telegram send printed the bot's token. The agents'
  idea file was full at 100, and an agent could replace the whole paper test or spend Helius credits without a cap.
- **The number that was wrong in this record.** "About 100 SOL a day" stood at a place no sender that reacts can take
  (corrected in the 07:50 section below). Nothing in the audit found a faster way to that place.
- **The one rule that holds** (labs of 23 Nov; docs/live-recipe.md names each job). Copying the big wallets as before
  does not hold: at the place our buys land, every one of them lost in the week of 9-16 Nov. One rule holds in both
  weeks at every landing from 3rd to 5th in line, with the full 0.05 SOL bid charged: **copy a wallet's buys of 2 SOL
  or more only in coins that are seconds old.** It is no rule by itself (over all 3,049 labeled wallets it loses 0.17
  SOL a copy); it holds behind three of 46 wallets: Cented, Decu and Yugi.
- **Under the filter the live snipers really use, it still holds.** OKTech cannot gate on the coin after the wallet's
  buy, only on our own fill (its ceiling, about 64.6 SOL of market cap). Scored that way (labs
  20261123-080537-9e473b and -080538-5a8bfc), all three make money in both weeks: +16.5 and +14.1 SOL a week together
  at 1 SOL a copy, about 2 SOL a day, on about 20 copies a day, with the full 0.05 bid charged where the snipers pay
  0.0031. The weak spot: Yugi's newest three days are about flat.
- **What the smoke tests showed** (23 Nov 03:04-03:56, 06:59-07:23, 08:10-09:10 UTC, 0.02 SOL copies). OKTech's
  ceiling is on our own fill, worked out at OKTech's own SOL price, exact to the SOL; that price is sometimes $200, at
  any send, and such a send fails for 0.0011 SOL. OKTech copies a followed wallet's send before it knows it fails
  (both fills of smoke test 3 were copies of a test wallet's failed buys). `platforms: ["pumpfun"]` does not keep a
  copy to pump.fun's curve. OKTech leaves some copies with no exit; the run sells them at 20 to 25 s. Behind Yugi,
  OKTech copies his router transactions in his own block: the first admitted copy stood second in line and made
  +11 % at 12 s.
- **What the paper leaves out.** A followed wallet's buys in coins it created itself (the lab skips them, OKTech
  copies them; the run sells such a copy at once and rests that sniper three hours) and copies of sends that fail.
- **What runs** (the owner, 23 Nov 00:44 UTC: "when everything is down to perfection run it"; at ~09:08 the wallet was
  left for testing): the rule as a test at 0.2 SOL a copy behind Cented, Decu and Yugi since 09:11 UTC, stopped at
  0.3 SOL lost or after a day. On paper that is about +0.02 SOL a copy after fees. The 1 SOL day (the ladder's step,
  stopped at 2 SOL lost) is prepared and waits. docs/live-recipe.md has both entries and every fill.
- **What it would take to scale, if the test pays.** The same copies at 1 SOL (paper: about 2 SOL a day), then 2 SOL
  (the ladder's next step; its daily loss cap is the owner's to set). More wallets of this kind (the scout's next
  question: who else buys coins in their first seconds, and pays at our place).
- **Open with the owner.** The OpenRouter account is empty since 22 Nov 20:16 UTC, so the server's agents cannot think
  (add credit at openrouter.ai/settings/credits, then send /credit <dollars>). Yes or no on a sender of our own for the
  place right behind the big wallets (the 07:50 section). After a test that makes money: bigger copies and their cap.

## 22 Nov, 07:50 UTC: what the place right behind costs, and how its bot takes it (I605, I608, I609, `sends`)

- **The bot's rule** (the scout, I605). 8r6N8w copies only the first buy of a coin by Cented, Decu or theo side
  (Bi4rd5), and only one of 1.9 SOL or more: 0 of their 115 repeat buys, 0 of 4,073 smaller ones. It lands right
  behind, buys about 6-7 SOL at a bid of about 0.59 SOL, and sells in about 8 parts from slot 15 to slot 87. Over
  72 hours: +100 SOL on 47 closed coins, 89 % of them wins.
- **It picks its moments; it does not race them all** (the new `sends` command, deployed 07:41, job
  20261122-074139-296636). Of 406 such first buys in 72 hours it tried 62, and landed right behind on 46 of them (3 in
  4 of its tries). On the other 344 nothing of it reached the chain. When it goes, it sends about a dozen copies: 764
  failed sends at its 62 tries, about 0.0027 SOL each and 0.033 a tried moment (2.05 SOL in all), so only the send
  that lands pays the big bid. (Corrected by the audit of 22 Nov: this said 0.0008 SOL each and 3.8 SOL for all 5,425.
  3.8 SOL is what the 4,900 failed sends read before the credit cap paid, most of them at no moment, and 0.0008 their
  average. The job's own summary line put the 2.05 SOL on the 15 moments with only failed sends; `sends` now gives
  each outcome its own. The run also stopped reading at its credit limit and left the oldest sends unread, so "62 of
  406" may be off by about one moment; the fixed code fetches only the sends near a moment, and a rerun is the
  agents' to start.) A send that never landed leaves nothing, so these are the fewest tries it made.
- **What the place is worth** (the fee analyst, I609: paper, the place assumed). Every first buy of 1.9 SOL or more,
  copied at 6 SOL right behind with 0.6 SOL charged for the bid: +1.32 SOL a copy behind Cented (311 copies in 7 days,
  lower bound +1.19) and +1.07 behind Decu (339, lower +0.98). Two places further back the same copies lose 0.48 and
  0.75. So the place itself is worth about 1.8 SOL a copy. The bot's own picks did no better than all the moments, so
  its filter is not explained yet (I615). "Right behind" is the lab's +0: the copy takes the price the leader's own
  buy left, ahead of every buyer after it, 8r6N8w and the buyers below included. The 7 SOL lab of the same leaders'
  first buys (+1.31 a copy, n 330) reads -0.89 SOL a copy with nothing a later buyer adds (its crowd-free reading):
  the profit is the price those buyers push up behind the copy.
- **Who stands there** (the audit of 22 Nov, tape of 19-22 Nov: 386 first buys of 1.9 SOL or more by Cented, Decu and
  theo side). Another wallet's buy of 0.1 SOL or more stands within 3 block places after the leader on 251 of them
  (65 %), and within 3 places before it on 1. 8r6N8w stands at exactly +1. One operator buys exactly 3.457 SOL from
  54 different wallets at exactly +2, on 119 of the 386 (31 %). Beside 8r6N8w's 0.6 SOL, the first buyers within 3
  places bid about 0.06 SOL, those 11 or more places back about 0.23: the closest bid the least, so this is no bid
  race. They can stand there only with the leader's transaction in hand before it runs.
- **Where a sender that reacts lands.** A buy sent once the leader's buy is seen lands some places after it. At 5
  places it finds nobody of 0.1 SOL or more ahead on 31 % of the moments, at 15 on 22 %, at 50 on 10 %, at 150 on
  4 %. OKTech's copies landed 5 to 630 places after the leader, or a slot later (16 copy buys since 12 Nov). A rough
  mix with the lab's own numbers one and two buys back: about +0.3 SOL a copy at 5 places, +0.2 at 15, about 0 at 50
  and about -0.5 at the block's end, all before the live gap of 0.18-0.40 SOL a copy.
- **OKTech cannot take it** (I608). Our 13 tipped buys were never first. OKTech charges the buy's bid again on every
  sell and every failed send: about 1.6 SOL a round trip with one sell, 4.8 with 8 parts, against 2.13 SOL a coin. And
  selling in timed parts is not an OKTech exit.
- **What it means for 100 a day** (corrected by the audit of 22 Nov). On paper, about 650 such copies a week at
  +1.1 to +1.3 SOL each is about 100 SOL a day, at 6 SOL a buy. That paper copy stands at +0, a place no sender that
  reacts to the leader's landed buy reaches, OKTech or one of our own. So the +1.1 to +1.3 is evidence only for a
  route that puts our buy next to the leader's transaction before it runs (CLAUDE.md rule 2). At the places OKTech
  reached (10 of its 16 copies 50 or more places back or a slot later), the copy makes about 0 or less before the
  live gap. 100 SOL a day stays unmeasured until that route is found. This bullet said before that taking the place
  needs a sender of our own with a high bid and a dozen sends a moment: the tape says a faster send or a bid alone
  does not take it; speed still counts on the 22-31 % of moments with nobody within 5 to 15 places. Any sender is
  the owner's decision. The next facts for it: the route check (next), I615 (the bot's filter and what its send
  ladder costs) and I610 (the tester scores the place on every first buy).
- **Before any sender: the route check** (the audit of 22 Nov). Whether the place right behind these leaders exists
  for us depends on how the closest buyers get the leader's transaction before it runs. If a validator group, or the
  leaders' landing service, sells a place right behind that anyone can bid for, the place has a price (8r6N8w pays
  0.48-1.0 SOL a landed buy) and the thing to build is a bundle client on that route. If the flow is private (Cented
  and Decu pay the same trading platform's fee accounts), no sender of ours reaches +0 behind them, and the studies
  of 8r6N8w's filter (I605-I622) answer the wrong question. Built 22 Nov, not yet run: `sends` lists every moment
  with the first other buyer within 3 places, and `--validators` splits the moments by the block's leader (at most
  about 130 requests for 72 hours, read before the failed sends, so the run's credit limit cuts those and never the
  split). Still to build: reading a few of the blocks themselves (what sits at the leader's place +1 to +3 when it is
  no pump.fun trade, and where 8r6N8w's failed sends stand); then the fee analyst runs it.
- **What a sender of our own would be** (the audit of 22 Nov): a new buy path, not a setting. A builder for pump.fun's
  buy_exact_quote_in, pinned by a test on our own landed 3.5 SOL buy the way the sales are pinned
  (tests/test_sell_pumpswap.py); a send path outside the RPC client's shared limit of 8 requests a second, through
  several landing services at once, with a blockhash kept fresh by the stream; a trigger from the processed or shred
  feed; and rule 1's caps and the guard. Today the repo signs only sales, each 4-5 requests one after another. Build
  it only once the route check and a lab copy scored K block places after the leader show a place that clears the
  live gap. That landing is not in the lab yet: its "+N" and "+Nx" count the coin's own trades behind the leader, not
  the block's places, so a reacting sender's place is still the rough mix above.
- **Since 06:15.** The review of 22 Nov ended with 35 confirmed findings, all fixed test-first except low ones the
  runner's second loss check bounds; deployed a26aeee at 07:21, 4773984 at 07:40 (the loss alert on the bot's own
  trades and coins, never the owner's moves) and 226ea5e at 07:41 (`sends`). docs/autopilot.md lists each fix.

## 22 Nov, 06:15 UTC: who holds the place right behind the big wallets (I603, answered)

- **The table** (the fee analyst, 22 Nov 05:50; first buys of 15-21 Nov: Cented 351, Decu 351, Cupseyy 164, Cooker
  70). The position right behind a first buy (the next one in the same block) is taken on only 23-27 % of them (80,
  94 and 36 buys), about 80 % of those by seven bots: 8r6N8w, Fhbh1D, HmR7Sx, FfW5e8, 9AGtz3, 3RYcHv and 6Z91
  (priority 0.08-0.3 SOL, some at 0, beside 8r6N8w's 0.5). On the other three in four the first outsider stands 3 to
  200 positions back, 120-131 different wallets, a third of them bidding under 0.01 SOL. Second place: 109-128
  wallets, two in three under 0.01, never adjacent. Behind Cooker his own two wallets stand first. OKTech gave us the
  next position 0 times in 13 tipped buys. (Corrected by the audit of 22 Nov: "right behind" here is block place +1
  only, the very next transaction. Within three places, a buy of 0.1 SOL or more stands behind 65 % of the first buys
  of Cented, Decu and theo side on 19-22 Nov; see 07:50.)
- **One bot owns it behind Cented and Decu.** 8r6N8w (no label, funded with 5 SOL by a wallet nobody we track shares)
  is first in 62 of its 63 entries, buys 6-8 SOL, pays about 0.5 SOL priority and a 0.11 tip a buy, holds 11-21 s,
  and made about +100 SOL after fees and bids in the week (92 coins; behind Cented 25 wins of 29, +50.1; behind Decu
  20 of 31, +28.6). It enters only 8-9 % of their first buys: a coin filter, or only its winning sends land. The
  scout studies it now (I605: which coins it takes, and whether a copy of ours 2-3 behind makes money on those).
- **What it means for 100 a day.** The place right behind is open three times in four, and the bot that takes it
  behind the two busiest wallets makes about +14 SOL a day, at about 0.6 SOL a buy to get there. 100 a day at that
  place means landing right behind many wallets at once, as 6Z91 does (+52 to +105 a day across hundreds). That needs
  a sender of our own; whether one reaches the next position, and at what fee, is not measured (OKTech never has). The
  decision to build one is the owner's; I605 is the next fact for it. (Corrected by the audit of 22 Nov: "open three
  times in four" counts block place +1 only. The buyers 1 to 3 places behind must have the leader's transaction
  before it runs, so a sender that only reacts faster does not reach the place; 07:50 says what it is open to.)
- **Since 04:45.** Deployed 4881ff6 at 05:30 (everything of the list below, and the kinds of copy), then 394a24a at
  06:15 and 6d00b48 at 06:18, which carry: 067e563 (the runner survives a failed start, a lost answer, an unsent sale and a
  hung read; --sweep-after needs the selling key to be the run's wallet), 67ba989 (a run's end never stops a sniper
  that may hold a coin: OKTech reads a sniper that bought "running"), 02ff3c7 (whose a buy is: the chain read knows the
  learned tip accounts; a copied wallet on a fill counts only with our priority), 6c5bf34 (`paper freeze --exit`: a
  forward record with the exit the live sniper runs; the tester's I597 freezes failed for want of it; the tester is
  told, I607) and 6d00b48 (a second stop signal no longer cancels the runner's stop on the way out: `remote kill`
  sends two, and the snipers stayed armed). The review of c54388d..997a010 is still checking its findings, most of
  them about the floor while the owner trades by hand.

## 22 Nov, 04:45 UTC: checked against all our data, 100 SOL a day is out of reach through OKTech

- **The verdict.** Six research sweeps of the server's data, then an adversarial check of each of their 22 candidates
  (workflow of 22 Nov 02:03-04:36; its working files in the session scratchpad `chk/`). The quoted numbers reproduce;
  the forecasts mostly do not. What survives on OKTech comes to about -10 to +15 SOL a week, centred near 0 to +5.
  Expect about 0 to +5 SOL this week; losing a few SOL is about as likely. 100 SOL in the coming week is under 1
  chance in 20, and 100 a day is seven times further.
- **Why.** The money is in standing 1st or 2nd behind a big wallet. 71 % of the best paper rule's profit (3d82dd6508:
  +45.8 of +64.5 SOL) came from copies 1 behind it; 2-3 behind it makes about +0.30 per 5 SOL copy, which the measured
  gap between live and paper (0.18-0.40 SOL a copy) eats. OKTech lands us 2nd to 5th behind crowded wallets, and the
  buyers ahead of us mostly bid less than we did: speed decides the place, not the bid. OKTech also buys what the
  paper never counted: last week 38 of Cooker's 73 buys of 2 SOL or more were inside his own coin launches; Cupseyy's
  copies buy coins made by others that only name him (bait); repeat buys are copied too. No copy of ours has yet
  matched its paper number live.
- **Where 100 a day is.** At 1st place: 6Z91 netted about +544 SOL on 15-21 Nov after 532 SOL of bids (+52 to +105 a
  day). That takes a faster sender than OKTech, one of our own; OKTech signs every trade today and this repo never
  does. Whether a new sender could win that place against bots that hold it is unmeasured: the fee analyst's I603
  measures who stands 1st and 2nd behind Cented, Decu, Cupseyy and Cooker, and the owner decides on that table.
- **What holds (each checked):** copy first buys only; keep the 12 s hold (every faster exit makes less at our place);
  don't copy Cooker's launches; don't rerun Cented and Decu (8 live copies -3.58 against -1.34 on paper); the crew
  sniper of 11 Nov stays off for good (its paper twin -0.35 over 23 trades, the crew silent since 20 Nov); don't copy
  the launch bots (-0.31 to -0.34 per 1 SOL copy); don't run 5Y9pyD at size; the creator-0 hunt only as a 0.2 SOL
  probe (its profit was one operator's coins; capped, its forward record is -7.26); the 0.003 tip is worth testing
  (behind the same wallet a bigger tip bought no visible place).
- **Fixed tonight, test-first** (live-path audit of 22 Nov, 20 findings; the other five audits are being checked):
  - the wallet-wide guard counts only the bot's trades, told apart by the fees our snipers pay (c54388d, deployed:
    today reads -3.97 SOL, not -14.48; the owner's hand trades had tripped the day stop);
  - the runner leaves the owner's own coins alone: never sold, counted, placed or waited on, the floor moving with
    them (d8f4843); a coin showing late takes back only the floor's move for its own SOL;
  - at a run's end, the snipers that hold nothing are stopped at once, and one empty wallet reading stops none
    (d1f39c6);
  - a runner told to stop (its job's limit, a kill, a restart) stops what strands nothing before it goes; a run
    refuses a job limit shorter than itself; `remote run oktech cycle` gets the limit its flags need (306c8ef);
  - one runner per wallet: a lock, and no start beside another armed sniper (a04d3ab).
  These wait for a deploy (the daily job holds it). No live run starts on the old code.
  Added by the audit of 22 Nov (merged as 3b412e0), the runner beside the owner's own trades, one line a fix:
  - the run's own sale never sells the owner's tokens once its own are sold, or before the tape has its last sale
    (97d7f15), and it waits while the wallet holds less than the run's and the owner's tokens on the tape
    together (fa20d7d);
  - a coin the owner kept after the run's copy was sold becomes theirs (f38ad9b);
  - the run's tokens in a coin of the owner's keep its snipers armed at an end, in the watch after it and on the
    way out, and are sold past `--sweep-after` (40f8ea1): never more than the wallet holds beyond the owner's
    (cddf1d1), and held long enough for that sale (2185c19);
  - a wallet read that fails at an end is read again for up to 15 minutes, 15 s on the way out (91feda1);
  - SOL the owner takes out while a copy is held moves the floor (58244bd); a copy whose coin shows one reading
    before its SOL leaves, such as a PumpSwap copy OKTech wraps again a second later, no longer moves the floor
    as the owner's (6fa5d25);
  - own launch and bait go by the copied buy ours followed (2f6bf9f);
  - whose a buy is: the bot's when the measure stage tied it to a sniper, or when it paid a sniper's own priority
    with its tip or with no tip (fees nobody recorded still count as the bot's). A copied wallet beside our 0.001
    SOL priority is no longer enough: the owner's own tool pays that priority too (44bd292). The guard's day stop
    and the loss alert count by this rule. (Since the audit's second and third rounds, 23 Nov, a buy at other fees
    is the bot's also by a copy's shape: at most 3 slots behind a buy of a wallet one of our snipers follows AND at
    that sniper's buyAmount, to 0.1 %: aef634a, 9953d4e; docs/architecture.md, "Whose trade, whose coin".)
  docs/live-recipe.md ("The ends of a live run") and docs/architecture.md ("Whose trade, whose coin") say how the
  pieces work together.
- **The plan, in order** (no SOL until the runner fixes are out): the owner asks OKTech six questions (below, in
  the report of 05:00); deploy; a fill classifier in the runner (own-launch and bait copies sold at once and never
  counted for the place stop; a repeat copy is only tagged. Corrected by the audit of 22 Nov: this said repeats
  were sold at once too. A copy of another copied wallet's buy in a launcher's coin is a first or a repeat copy,
  not an own launch); one runner for several strategies; then the 1 SOL day behind Gake Side and
  Cooker as approved, a 0.1 SOL A/B tip test behind Cupseyy, and size only where counted copies make money live.

## 22 Nov, 03:00 UTC: the night's mistakes, each with its fix; the goal is now 100 SOL a day

- **The goal (the owner, 22 Nov ~02:20 UTC: "not 100 sol a week 100 sol a day").** That is about what the best bot on
  our tape makes: 6Z91 about +93 SOL a day after its bids (the section below: about 500 buys a day at about 3 SOL,
  behind hundreds of wallets, paying for its place). Our best evidence is far below it: the big wallets' first buys
  made money on paper every day 15-21 Nov (+42.5 SOL over 246 copies at 2 SOL with a landing up to 6 behind; +63
  over 158 at 5 SOL), about 6-9 SOL a day before any live haircut, and no copy of ours has held its paper number live
  yet. Getting from there to 100 needs what 6Z91 has: many more wallets copied at once, bigger buys with the bid
  that buys the place, and the speed to land first.
- **Live, 21-22 Nov.** 8 copies of Cented and Decu at 3.5 SOL (21 Nov 22:25 to 22 Nov 00:54): -3.6 SOL against
  +0.08 to +0.35 a copy on paper. **A mistake of choice:** the rule was started on its positive newer days while its
  older days and the repeat week of 7-14 Nov lost at every exit (the exit analyst's table, 22 Nov 01:32). Then the
  1 SOL day behind Gake Side and Cooker started three times (01:54, 02:14, 02:32) and ended three times on its floor
  with no fill, each time when SOL left the wallet without a trade.
- **The mistakes, each with its fix** (docs/live-recipe.md has the detail):
  1. OKTech left copy 6 unsold (its known fault: the buy that uses up a one-shot sniper can get no exit) and the
     runner ended on its floor 9 s before its own sale (`--sweep-after 45`): about 1.3 SOL lost to it. Fixed: the
     runner keeps watching after any end and sells what is still held (3ac64c5, deployed); the sale comes at 20 s
     after the coin shows now, not 45.
  2. The runner's floor counted SOL the owner took out as a trading loss: the three ends without a trade. Fixed in
     e2cb3bc (the floor moves with SOL that leaves with no coin bought or comes in with no coin sold); its deploy
     waits on routine jobs.
  3. That deploy waited over 40 minutes and gave up: routine jobs (qualify, discover) hold every deploy, a live-safety
     fix included. In the audit.
  4. `landing` crashed on a coin move, a fill side the night's own change added (KeyError 'transfer'): fixed 6ef6dc7.
  5. The agents could never score a momentum exit: their job tool read the "/" in MOM2%/s as a file path. Fixed
     bceef37; the exit analyst can score momentum holds and timer extensions now.
  6. Wrong numbers in the record: copies 4 and 5 were written as sold at the 12 s timer; they sold at 6 s and 9 s,
     with neither of the snipers' exits due (asked of OKTech, I571). The agents had taken this session's hand sales
     for late OKTech sales. Corrected, on the server too.
  7. A docs-only commit was pushed after a full suite with one flaky test failing: a push now runs only after a
     green suite (chained with &&); the flaky test has a task card.
- **What runs on it.** A full audit of every component, each finding checked against the code and the data before
  it is fixed (workflow of 22 Nov 02:5x), and a sweep of our data for the strategies that can scale (02:0x). Fixes
  go in test-first and out with `remote update --when-quiet`.

## 20 Nov, 20:00 UTC: what we were missing is size, and the speed to use it

- **The finding (the owner, 19:2x: "there's something we are missing, and you have to find it").** The winning copy
  bot on our tape, 6Z91, made about +93 SOL a day after its bids over the last 72 hours (1,319 buys into 1,155
  coins, 4,075 SOL in, 4,549 SOL out, 195 SOL of bids; its failed sends are not on the tape), the last 24 hours +93
  too; Fhbh1D about +26 a day. 6Z91 plays our game (copy known wallets, snipe launches) at about 3 SOL a buy, up to
  10 behind the biggest wallets, bids 0.18 SOL a buy on average (0.03 behind ordinary wallets, up to 3.4 behind
  Cupseyy), and makes about 500 buys a day behind hundreds of wallets. The place behind a wallet is worth about 10 %
  of the SOL put in (docs/research/2026-11-12-winning-bots.md): at 5 SOL a 0.2 bid pays for it, at our 0.05 to 1 SOL
  no bid does, and without one we land behind the bots that pay. We tested at the sizes where the game cannot pay;
  the bid labs of 20 Nov ("a higher bid loses") were at 2 SOL.
- **Paper at size** (labs 6a8a1a and 981ad1, 19:29 UTC: the big group's first buys of 0.95 SOL or more, `bid-`
  landing, hold12s copy exit, 168 h to 15:00 UTC). 5 SOL a copy, 0.2 bid: +0.287 a copy on the newer 101 (lower
  bound +0.139; our own buy moves the price 14 % at the median); over the week by wallet Cupseyy +0.318 over 105,
  Gake Side +0.260 over 27, Cooker +0.335 over 26, Dior New Wallet +0.114 over 20, CupseyFriend -0.060 over 35.
  10 SOL, 0.3 bid: +0.208 over 94 (lower -0.059; own impact 31 %). 2 SOL, 0.05 bid (f9cc32, 1677c2): +0.06 to
  +0.12. About 36 such buys a day: about +10 SOL a day on paper at 5 SOL, if the landing holds. THE LANDING DECIDES IT (labs 1c1bf1 and 9d971f, 19:58, the same 5 SOL lab with the copy placed behind the 3rd
  and the 5th buy after the wallet's): +3x +0.028 a copy on the newer 101 (lower -0.095; Cooker +0.202, Cupseyy
  +0.040, Gake Side +0.009 over the week), +5x -0.158 (lower -0.275; Cooker +0.126, Cupseyy -0.201, Gake Side
  -0.039). At 5 SOL each buyer ahead of us costs about 0.1 SOL a copy: the edge is in the first two places, and
  only Cooker (cheap crowd, 26 copies a week) holds at every place. Our live fills behind Cupseyy at a 0.05 bid
  stood 1, 2, 2, 5 and 5 behind.
- **The market-cap ceiling turns it round (the agents' I421/I442, then labs 3d13e5, fe1626, caef75, 230146 at 5 SOL,
  0.2 bid, 20:58-21:01).** Copy only buys made while the coin is under 100 SOL market cap (at the wallet's buy):
  +3x +0.332 a copy on the newer 41 (lower +0.100), Cupseyy +0.654 over 49 in the week, Cooker +0.519 over 7, Gake
  Side -0.021 over 7; +5x +0.028 (lower -0.182), Cupseyy +0.266, Cooker +0.432. At 200 SOL: +3x +0.263 over 70
  (lower +0.107; Cupseyy +0.220 over 70, Cooker +0.390 over 17, Gake Side +0.136 over 17); +5x +0.020 (Cupseyy
  -0.087, Cooker +0.291, Gake Side +0.065): Cupseyy's buys at 100-200 SOL lose about 0.8 a copy at +3x, Cooker's
  and Gake Side's gain. OKTech's ceiling (`maxMcapUsd`, `oktech copylist --max-mcap SOL` since 2ea7bd7) is a min-out
  on OUR buy, so it also refuses the copies that land too late (fill 5 paid +76 % over Cupseyy). The bands were
  picked on this week: the live result will be lower.
- **It does not spread to other wallets (21 Nov 03:04-03:08, labs 7c0be4 and 472721, the same 5 SOL recipe with the 100 SOL
  ceiling at +3x).** The 18 KOLs: -0.561 a copy on the newer 171 (lower -0.636), every KOL negative or flat (Gfree -0.190
  over 60, Lynk -0.619 over 75, Pengu -1.172 over 40). The owner's tracker list: -0.796 a copy on the newer 3,692 (lower
  -0.819); its best are Cupseyy (+0.765 over 29) and wallets with 4 to 10 copies (nyhrox +1.90 over 8, Bean Side +0.50 over
  10: at that count, out of hundreds of wallets, the best few look good by chance). 6Z91's own leaders over 72 h (`leaders-of`)
  are each 1-2 % of its coins: its edge is breadth plus speed, not a short list we can borrow. So scaling by more wallets is
  slow; the edge we have is narrow (Cupseyy's early-curve buys).
- **20 SOL a copy loses heavily (lab 54ea25, 21 Nov 03:12; the owner: "do 20 sol too any number is fine").** The big group, bid
  0.5, +3x, hold12s: -1.88 a copy on the newer 72 (lower -2.38); our own buy moves the price 70 % at the median (90 % of
  copies: 109 %), 94 of 180 moments left out for size; every wallet negative (Cupseyy -2.25 over 82, -185 SOL). 5 SOL is the
  size: the fee is worth paying and our own impact (14 %) is small.
- **THE PLACE IN LINE IS THE WHOLE GAME (21 Nov 03:15-03:31, labs ff6ca6, ecf5ea, 4c7f3a: the 5 SOL recipe, bid 0.2, 100 SOL
  ceiling, hold12s, over every candidate wallet, 168 h).** Behind the 3rd buy (+3x): -0.772 a copy on the newer 5,001, only
  Cupseyy pays (+0.823 over 28). 2nd in line (+1x): 20 of 141 wallets with 10+ copies positive; Cented +95.5 SOL in the week
  (220 copies), Decu +89.0 (144), Cupseyy +68.9 (28), winner +59 SOL/106 +33.5, Yugi +24.9; the ten best about +360 SOL a
  week. 1st in line (+0, right behind the wallet): 40 of 142 positive; Cented +363.2 (+1.65 a copy over 220), Decu +281.8
  (+1.96 over 144), winner +59 SOL/106 +174.0, Cupseyy +157.8 (+5.63 over 28), theo side +129.6, the ten best about +1,345 SOL
  a week (about 190 a day). Read with care: the best ten are picked after seeing the week, so the real figure is lower; the
  overall mean is still negative at every place (-0.348 at 1st) because most wallets lose; first place is what 6Z91 holds
  (first outsider in 61 % of its copies on 12 Nov). So the money is there in the data, and the gate is the place in line,
  not more data: at our measured places (2nd to 5th) it is about 3 SOL a day; at 1st it is the order of 100+.
- **Still true on the newest three days (lab bdc3c6, 18-21 Nov 02:15 UTC, 2nd in line, the same recipe).** Cented
  +0.726 a copy over 77 (lower +0.534), Decu +0.834 over 48 (+0.658), Cupseyy +2.846 over 7 (+2.585); the week's
  first four days alone give Cented +0.28, Decu +0.51, Cupseyy +2.33 a copy, so both halves agree for these three.
  winner +59 SOL/106 faded at 2nd (+0.064, lower -0.127); Tim5 flips sign between the halves (-1.58, then +1.43 a
  copy): left out. The three together: about 35-40 SOL a day on paper at 2nd in line, about 115 at 1st; picked after
  looking, so expect less. Proposed to the owner at 03:5x for the 5 SOL day in place of Gake Side and Cooker.
  Running: labs 18c575 and 45326a, the same week with the copy placed by its bid (0.2 and 0.5 SOL), to see what
  place the tape's other bidders leave a 0.2 bid behind these wallets.
- **Each wallet on its own buys, and the landing probe (21 Nov 03:53-04:32).** Scored the way its own copy sniper
  copies (each wallet's own first buys, 0.95 SOL or more under 100 SOL, labs a5f6b5 and 15062c, 168 h): Decu +1.85 a
  copy at 1st in line (lower +1.72) and +0.74 at 2nd (+0.63); Cented +1.62 (+1.42) and +0.46 (+0.29). The bid model
  puts a 0.2 bid first in 86 % of their blocks and a 0.5 bid barely more (labs 18c575, 45326a), but our real fills
  stood 2nd or worse, so 2nd is the place to plan on. The live probe at a 0.3 fee (docs/live-recipe.md): one real
  fill, 2nd in winner+59's block; two fills copied CREATE-BAIT, new coins made with Cupseyy's address in their create
  transaction, bought by his copy sniper at a fresh coin's 28-29 SOL, and OKTech left one of them unsold. So Cupseyy
  is out at size, and the runner sells what OKTech leaves (`--sweep-after`). A 35 SOL market-cap floor was to keep bait
  out, but OKTech ignores it in fresh coins, and the minimum buy size too (the floor test, 21 Nov 08:02: three buys at
  29-30 SOL under a 35 SOL floor, two of them the wallet's own creates under its minSol): no setting we send stops
  bait. The bait scan (no SOL) narrows it: OKTech does not copy another wallet's buy that names the copied wallet
  (two such 3-6 SOL buys came while our Cupseyy copy was armed and it sent nothing), only a coin's creation that
  names it, and none named Cented or Decu in their busiest hour. The daily wallet test now also scores every
  wallet at 5 SOL (fc79283), and `lab --each` scores what a copy sniper really copies, repeats included (314fdcd).
- **The open question is speed.** The landing test found the paper's place too kind in 3 of 5 fills behind Cupseyy,
  mostly from arriving late (a block after the wallet; buyers bidding 0.001 ahead of our 0.05), which no bid fixes.
  If OKTech cannot stand first behind these wallets, a faster way to send our buys is the build.
- **Live now: nothing.** The 1 SOL day on Gake Side and Cooker ended 21 Nov 01:49:18 by its floor (its one fill
  -0.012). Prepared: the landing probe behind Cented, Decu, Cupseyy and winner +59 at a 0.3 fee (waits for a little
  plain SOL) and the 5 SOL day on the owner's 20 SOL (docs/live-recipe.md, both entries).
- **Not reachable, said plainly to the owner:** 100 SOL a day within a week from a 9 SOL wallet. 6Z91's 93 a day
  comes with about 1,700 SOL bought a day, the first place in line, and nobody racing it for that place but us.

## 12 Nov, 14:00 UTC: twelve hours without a network (00:31 to 12:30 UTC); found, fixed in code, one command left for the owner

- **What happened.** The server never crashed. On 11 Nov at 07:14 UTC, during that morning's 11-minute freeze, its
  network manager failed one operation and gave up on the network card. From then on nothing renewed the server's
  lease on its internet address. The lease ran out on 12 Nov at 00:31 UTC, and for twelve hours the machine ran
  with no connection in or out: no trade recorded, no agent turn, no contact with OKTech, no message to the owner.
  A restart by BitLaunch's support at 12:30 UTC, asked for by the owner, brought it back. It was not the code update
  of 00:26. (Audit row 80. An earlier version of this section said the server was off and the cause unknown: it was
  running, and the cause is the one above.)
- **What it cost.** Twelve hours of the agents' work and of any live copy. No money: nothing was held, and the wallet
  cannot pay a curve buy anyway. The recorder replayed all twelve hours of pump.fun curve trades within 44 minutes of
  the restart: the tape has no hole. PumpSwap trades of recently migrated coins for those hours are missing.
- **This session could not tell a dead server from a cut-off one for ten hours.** It has one way in, and that way was
  the dead one. The owner's panel showed the machine as running, which was true.
- **Fixed since 13:17 UTC:** every 15 minutes the server checks its own network setup; a failed one is a message to
  the owner at once, with what to do. Last time that was 17 hours before the connection went.
- **Fixed for good since 14:46 UTC, when the owner ran the one command on the server:** the server now repairs its
  network setup by itself within a minute, and Ubuntu's daily update job, which caused the freeze, runs at the
  lowest priority.
- **Still nothing profitable live, and by the evening of 12 Nov no wallet worth copying.** The two wallets the book
  copied (74xVT9, 4sAUSQ) each buy as one of a set of four wallets that land in the same block in changing order.
  The server's analyst suspected it (its idea I72); the tape confirms it; the rule that finds a wallet's companions
  looked only behind the wallet and missed them. Measured behind the whole set a copy makes +0.003 SOL with a lower
  bound below zero (it had shown +0.009 with +0.005). Both lost their verdict at 14:26 UTC and the book stopped
  their snipers by itself at 14:30. (Audit row 81.)
- **The bid table was run by the analyst within 40 minutes of reaching the server.** The first place behind a known
  wallet is taken mostly by speed, not by the bid: the highest bidder is first only half the time, and half the
  first places paid under 0.001 SOL. On paper that place still pays (+0.069 SOL a trade at 1 SOL, 885 unseen
  moments, scored before the set rule: being re-run). What decides the lead now is whether our copies can be that
  fast. That is a question about the execution service's speed, not about a fee.
- **Built during the outage, on the server since 13:07 UTC (audit rows 77 to 79, open item 19):**
  - the table the nearest lead was waiting for: **which bid lands first behind a followed wallet** (`place-bids`).
    It says whether the first place behind a known wallet is bought with a bid or taken by speed, what each bid
    would have won, and who holds the place today. The server's analyst runs it and then scores the copy at that bid;
  - the server **refuses a code update while one of its jobs is mid-run** (a deploy went out during a scan on
    12 Nov 00:08);
  - the server **tells the owner once when it restarted or stood still**: from when to when, and what is not running;
  - the machine's own log of **the boot before** can be read (`remote logs kernel|systemd|network --boot -1`): how
    this outage's cause was found.

## 11 Nov, 23:30 UTC: every paper result at size is too good; a wrong alert went to the owner

- **The paper test overstates winning trades, more the bigger the buy** (audit row 75). The server's analyst saw two
  rules return the same on the same 7 coins at 2 and at 5 SOL and handed the question over. Confirmed in the code and
  on the tape: after our buy the simulator assumed every later buyer still got the same coins and paid more for
  them. On the tape over half the buys spend a fixed amount of SOL and would get fewer coins instead. On a +44 %
  move the simulator showed +45.2 % at 2 SOL and +46.9 % at 5 SOL where the replay gives +41.1 % and +37.4 %; at
  0.2 SOL the difference is 0.4 points. Losing trades were priced right.
- **What that touches:** the KOL paper rules at 2 to 20 SOL on the owner's page (5 SOL: +22 SOL over 460 trades,
  about zero without its three best), every size lab, and the "1 to 5 SOL right behind a known wallet" lab, which was
  the nearest thing to a live proposal. All of them are upper bounds until re-scored. The 0.2 SOL results barely move.
- **Fixed in the simulator and on the server since 23:49 UTC.** The model now replays each later trade in the terms
  it was made in, checked against a brute-force pool. The analyst's own 2 SOL lab, re-run: its best rule goes from
  +0.084 SOL a trade (lower bound +0.031) to **+0.023 (lower bound -0.026)**. No rule at size has a bound above zero.
- **The paper rules' kept trades, priced again (12 Nov 00:27 UTC).** The KOL copy rules on the owner's page: 2 SOL
  -2.5 SOL becomes **-20.7**; 5 SOL +23.6 becomes **-76.9**; 10 SOL +14.9 becomes **-332.9**; 20 SOL -9.6 and -7.2
  become **-545.7 and -688.8** (about 460 trades each). At these sizes our own buy is most of the move: the first dip
  after it is deeper than the old model read it and the -15 % stop fires. Checked against a brute-force replay of a
  real coin's recorded trades. **Copying KOL first buys at 2 to 20 SOL loses; the "+22 SOL at 5 SOL" never existed.**
- **Found on the way:** that lab took 17 minutes because it read every coin on the tape (20 million trades) to score
  903 moments. A lab of wallet rules now reads only the coins its wallets bought: the same numbers in under 2 minutes
  (audit row 76).
- **A wrong alert went to the owner at 22:15 UTC** (audit row 74): "3 failed transactions in the last hour; check
  OKTech sells and the send count". They were the three buys the wallet could not pay. The check now names such
  buys and does not page for them. (Since 23 Nov, 9197324, it names the buys refused on their own price limit the
  same way, "N buys refused on their price limit", and pages for none of them: a copy sniper's ceiling refuses every
  copy above it, 20 of the smoke test's failed sends that day.)

## 11 Nov, 22:40 UTC: the owner said yes three times; one of the three is waiting for a key only the owner has

- **Still nothing profitable live, and nothing bought on 11 Nov.** Four copies of 74xVT9 were sent and failed for
  lack of plain SOL (18:52, 21:28, 21:44, 22:06 UTC; 0.001 SOL of fees each). The wallet holds 0.47 SOL, nearly all
  of it wrapped: a curve buy cannot be paid with it.
- **The owner's three answers (about 21:50 UTC), and where each stands:**
  1. *The bot may turn its own wrapped SOL back into plain SOL.* Built, tested and on the server; the chain's own
     simulation shows the conversion to the lamport (plain SOL 0.0012 to 0.41, wrapped 0.47 to 0.06: two curve buys
     payable). **It has not run.** The server has never had the wallet's key, so nothing on it can sign for the
     wallet. The owner adds one line to `/etc/snipetech/env` over SSH (never in chat); then the agreed 0.05 SOL test
     runs, and after it the round keeps plain SOL for two curve buys by itself (audit row 73).
  2. *The crew sniper.* Not started: each of its buys is a curve buy, and it is started once the wallet can pay one.
  3. *Preconfirmations.* On the server since 21:59 UTC; the book's next copy sniper is the first to ask for them. The
     next 20 copies are compared with the 24 before.
- **What this cost to learn:** the first design of the conversion needed 0.0015 SOL of rent up front and the wallet
  had 0.0012 by the time it was simulated. The design that shipped needs its own fee only (0.000011 SOL).

## 11 Nov, 21:00 UTC: the first copy of the qualified wallet was sent and failed, for a reason this record had wrong

- **Still nothing profitable live, and still no live copy of 74xVT9.** At 18:52 UTC the sniper sent its first copy,
  in the wallet's own block, and the buy failed: pump.fun's curve takes the whole buy (0.2 SOL) from plain SOL, and
  the wallet held 0.0052 plain SOL beside 0.47 wrapped SOL. Cost: 0.001 SOL of fees. Read from the chain
  (docs/live-recipe.md, top entry).
- **What was wrong here.** The record, the agents' instructions and this session's message to the owner all said a
  buy needs "about 0.005 plain SOL for its fees". That holds for a coin that has left its curve (PumpSwap is paid in
  wrapped SOL: the four live buys of 9-10 Nov). Three quarters of 74xVT9's buys are on the curve, where one 0.2 SOL
  copy needs 0.205 plain SOL. Corrected in the record, in the agents' instructions, in the snapshot they read (how
  many copies the wallet can pay on each venue) and to the owner.
- **Two tools showed the first part of something as all of it** (audit rows 70 and 71):
  - the owner's dashboard read the five KOL paper rules through a query that returns 500 rows at most and showed 106
    trades a rule where each has about 426, with the totals of the first trades only. True record at 20:00 UTC: 2 SOL
    rule -5.8 SOL over 426 trades (the page said -1.0 over 106), 5 SOL rule +24.8 (the page said -2.9), 10 SOL +23.5,
    20 SOL +2.8 and +19.0. None has a lower bound above zero; five trades carry each positive total. The week's
    spend table had the same fault from a file read by its last 200,000 bytes (6 Nov: 2.16 $ shown, 4.44 $ spent).
  - the forward paper test placed a copy with its own line of code and had not followed the lab to "behind the
    copied wallet's companions" (17:50 and 18:19 UTC): a rule frozen from a lab was judged forward at a better place
    than the lab scored it at. Both now place every entry with one function (deployed 20:39 UTC).
- **The agents after the 18:19 fixes** (their own turns, 18:30-20:30 UTC): a finished job's result is read once from
  its outline, then by part (no more "the result went unread"); the analyst froze the lab's best 2 SOL KOL rule for a
  forward test instead of believing it (lab: +0.107 SOL a trade; the same rule family's forward record at 2 SOL:
  -0.014 a trade over 427), read the failed buy within eight minutes and wrote one info note. About 1 $ a turn.
- **The first qualify run on the corrected landing** (18:45 UTC, 900 wallets): 38 buy with companions and are scored
  behind them (33 verdicts say so); the companions read took 4 s of the run's 454. Nobody new qualified.
- **Open:** the wallet's SOL turns into wrapped SOL by itself and nothing turns it back (the owner's to decide: an
  automatic unwrap converts SOL inside their wallet); 8MaVa9's and 74xVT9's reviews at 03:46-04:48 UTC on 12 Nov; the
  bid table (two days of rows from 13 Nov); the deployer forward record (300 trades).

## 11 Nov, 17:00 UTC: the tape now writes what every trade bid for its place, and who paid for it

- **Still nothing profitable live.** 74xVT9 is the one qualified wallet; no strategy passes on paper. Nothing was
  bought on 11 Nov: eight buys of 74xVT9 over its floor, none copied, for the reason in the 14:30 entry below.
- **New on the tape since 16:34 UTC** (audit row 64): with every trade its priority fee, its tip to a landing service,
  and the wallet that paid when that was not the trader. Until now these were known only for a transaction some study
  fetched afterwards: none of the last 51,356 trades. Checked against 25 transactions read again from the chain:
  identical in all. The recorder's processor share did not move (0.09 of a core); a day of rows adds 100 MB.
- **What it opens.** (1) The place in a copied wallet's block is a fee auction (CLAUDE.md rule 2); what bid buys
  which place can be read off the tape once a few days of rows exist, where so far it took live fills. (2) The FOMO
  study sees every FOMO user: FOMO's wallet paid for 784 traders in 15 minutes, where the study saw 300 in three days
  (the owner asked for all of them on 8 Nov). (3) The bot study reads each bot's bid from its own trades.
- **The deployer forward record's first numbers** (to 14:15 UTC; it grows with each three-hourly tape copy): 39
  launches by deployers marked "worth sniping" lose 0.072 SOL a 0.5 SOL snipe, 361 "marginal" lose 0.036, 2,329
  "avoid" lose 0.020. Too few to judge: 300 are needed, about a day more. So far "worth sniping" is not better than
  "avoid".
- **Found and fixed on the way:** `snipetech --help` had crashed since the morning's `dip-buys` command (row 65).
- **17:35 UTC: the wallets our rules stop were checked, and the check found where the copy money is** (audit row
  66, docs/research/2026-11-11-stopped-wallets.md). The crowding rule and the history rule cost nothing: the wallets
  they stop lose after their verdicts. The wallets that look best to copy are each the first of one operator's three
  or four wallets, bought with in consecutive transactions. Our paper copy "three buys behind" counted the
  operator's own wallets as those three buys, so it sat in the first place behind the operator. That place pays
  (BNnN2M: +0.043 SOL a 0.2 SOL copy, 96 % winners) and is taken in every block by bots that buy 2 to 7 SOL and bid
  0.03 to 0.3 SOL for it. One place further back the profit is gone. Our copies bid 0.003 SOL.
- **What follows from it.** (1) The paper landing is corrected (deployed 17:50 UTC): a copy is scored behind the
  operator's own wallets and then behind three others, so no wallet qualifies on a place we do not reach. One such
  wallet was hours from being armed; on the corrected landing it loses. 74xVT9's number did not move.
  (2) The trade that works there is larger and bids more (the
  second bot wallet: +117 SOL in five days before fees and bids). Whether it pays for us is a lab question for the
  next days, with the bids now on the tape; if it does, it is one yes/no for the owner.
- **Open:** the 14:30 list below; the bid table (a few days of rows); who the two other large fee payers are.

## 11 Nov, 14:30 UTC: are the agents doing the work? The scheduled jobs yes; the paid analyst only in part

- **Still nothing profitable live.** 74xVT9 is still the one qualified wallet; no strategy passes on paper.
- **Live: nothing was bought on 11 Nov.** 74xVT9 made five buys over its floor (three in the morning, two at 14:12
  and 14:16 UTC) and none was copied: a buy pays its fees in plain SOL, and the sniper wallet holds 0.0008 of it
  beside 0.47 wrapped SOL (docs/live-recipe.md; the wallet is the owner's and is not flagged). The agents are now
  told this, so they stop hunting for a fault (audit row 61).
- **The scheduled jobs do their work**: the tape, the wallet checks every 30 minutes, the strategy search every two
  hours, the paper test every 30 minutes, the hourly re-scoring. All on time, the server nearly idle.
- **The paid analyst** ran 25 times on 11 Nov for 24.14 $. When something was new it did real work: it found that
  one operator's bundles carried the 5 SOL launch rule's paper profit (+0.12 SOL became -0.31 SOL an hour later), and
  that the search kept adding that rule again. But it started five jobs all day, none after 10:30, while 14 ideas
  that need no yes from the owner waited untested, two of them the owner's own (the dip buy, the instant-bond
  migration snipe). And four in ten of its database queries were guesses at column names.
- **Fixed:** the query tool now tells it the tables' columns (audit row 56). Every turn's message lists the ideas
  nobody tested, and a turn with nothing new tests the first one or hands it to the cloud session (row 59). The
  cadence and the budget are the owner's and unchanged.
- **Fixed: one paper record a rule** (row 57). The search had put the 5 SOL launch rule into the paper test four
  times in 2.5 hours, each time with another exit or a twin setting. A launch rule that passes on paper goes live by
  itself, so four tries at passing were three too many. No limit on new rules (the owner's 9 Nov instruction; the
  old "4 a day" sentence is gone from the analyst's instructions, row 58).
- **Fixed:** two studies read a venue name the tape never writes (row 60).
- **The owner's two ideas of 10 Nov are tested, and both lose where our buys land** (they had waited a day: the
  agents had no tool for one and were never told of the tool for the other, row 62). Buying a hot coin's 60 % fall
  loses a quarter of the buy in 30 seconds (1,355 falls in six days). Waiting for the price to turn finds a real
  bounce, about 2 %, against a round trip that costs 6.7 %: it loses too, also at a perfect landing and at 1 SOL.
  Buying coins that migrate within a minute of their launch loses 7 % a trade (367 unseen trades).
  docs/research/2026-11-11-dip-and-instant-bond.md.
- **Seen on the server after the deploys:** three analyst turns, 12 database queries, none failed (4 in 10 before);
  the 15:01 turn started the test of the owner's migration idea by itself and the 15:30 turn read and closed it. The
  turns did not get cheaper (about 1.20 $ against 0.97 $): they did more.
- **Open:** whether that holds over a day; the first strategy search that picks another exit for a rule under test.

## 11 Nov, 13:00 UTC: the nightly job worked three hours for nothing; a quarter of the "worth sniping" deployers lose where we land

- **Still nothing profitable live.** No new live result: 74xVT9 is still the one qualified wallet.
- **The nightly job** used three to four processor-hours every night (more than any other regular job) and three of
  its five steps fed nothing: a strategy lab scored at a place our buys do not reach (its report's first line read
  "+360 SOL a day"; nobody ever read the file), a ranking nothing reads, a plan never sent. While it ran, three to
  four hours a night, the real research loop stood still. Those three steps are retired; the server needs no root
  step for it. First night with it: 12 Nov 04:17 UTC.
- **The owner's market-cap limit** on a copied wallet (75 SOL on AG89fn) was turned into dollars at a SOL price that
  was only refreshed once a night, as a side effect of that nightly job. It is now refreshed when it is used, at
  most an hour old.
- **Deployers marked "worth sniping": 26 of 110 lose where our buys land.** A second rule marked a deployer "worth
  sniping" if a buy one block earlier than ours would have paid. That rule no longer decides anything, and every
  verdict is being made again. Nothing was trading on these verdicts.
- **Every deployer verdict was also about 0.006 SOL a snipe too kind**: it charged 0.0012 SOL a transaction where a
  live one costs 0.0042. It now charges the measured cost, as the labs do. After both fixes and a full re-scoring
  (13:43 UTC) 59 deployers are marked "worth sniping", none of them losing where we land (110 before).
- **Built: a forward record of the deployer verdicts.** They were made every hour and nothing used them, while 82
  deployers marked "worth sniping" launched 2,096 coins in a day. Now every launch of a deployer that was marked
  before that launch is one paper trade, at our landing and real cost, with "avoid" and "marginal" as the control.
  It shows in the paper table on the live page. Within days it says whether deployer sniping is worth a live test
  (which would be a yes/no for the owner) or whether the verdicts pick nothing. No sniper can start on it by itself.
- **A sniper builder would have turned an unknown rule into "buy every launch"**: `oktech strategy` now refuses a
  rule of a kind no sniper runs. No live test could have started one; a hand-made sniper could.
- **Four research tables counted a copy as a winner above +2.5 %; a copy of ours breaks even at +6.7 %.** They took
  off pump.fun's fees and forgot the two transactions (4.2 % of a 0.2 SOL copy), or the other way round. The agents
  ran one of them five times on 11 Nov. All four now take the whole cost off and say what it is. No decision had
  been taken on those tables alone: every rule from them went through the paper test at real cost, and none passed.
- **Hourly discovery is confirmed faster**: the 13:07 run took 8.9 seconds of processor where every run took over
  six minutes; five runs in six are like that now.
- Every timer and stage was checked against its schedule: all run when they should. Record:
  [audit-2026-11-11.md](audit-2026-11-11.md) rows 48-55.

## 11 Nov, 12:10 UTC: the live page's events took over 30 seconds to read; two safety checks could fail silently

- **Still nothing profitable live.** No new live result: 74xVT9 is still the one qualified wallet.
- **The live page's "events" part read 6.6 million rows every 10 seconds.** 97 % of them were the same payments
  written again and again (a coin creator's payment, once more for every coin of that creator; nothing ever deleted
  them). One read took over 30 seconds, so the database was never left alone. Those rows are no longer written, the
  old ones are deleted 400,000 every three hours (gone in two days), and the page reads through an index: each read
  now comes back as fast as an empty query. The first scan after the change wrote 2,451 rows where the one before
  wrote 95,746.
- **Two safety checks could fail without a word.** The scan that records which wallets a coin's creator pays skipped
  a creator whenever the RPC refused, never said so, and never went back. The wallet check that reads those records
  passed a wallet when its own read failed. Both now say so and try again. No whole scan was lost in the last two
  days; the first scan after the change read all 312 creators.
- **Every place the code catches an error was read** (42 places): these three hid something, the other 39 report it
  or guard something harmless. The server's logs since 06:00 hold nothing new: the 07:12 freeze and Ubuntu's updater
  killing three of our jobs at 06:04, both waiting on the owner's one root command.
- **Hourly discovery**: the change of 11:30 (the two slow sources look every six hours) is in place; the 12:08 run
  was the full one that starts the clock (6 min 10 s of processor), the 13:07 run is the first short one and its
  time goes into the audit. One earlier statement is corrected: "a discovery run finds 0 to 2 new wallets" was
  true of two runs in three. Every third run another source (the first buyers of new coins) brings 400 to 800 new
  wallets; it still looks at every run, so nothing is lost. The two slowed sources bring 0 to 2 a run.
- What is left, with numbers, and why each waits: [audit-2026-11-11.md](audit-2026-11-11.md) (rows 46-47, open
  items 5 and 10-13). The agents cost 42.87 $ yesterday and 24.70 $ by 12:20 today; that is the model and the half-hour cadence.
- **Still with the owner** (asked 08:30, no answer yet): preconfirmations on copy snipers, and the crew sniper. And
  the one root command on the server (`sudo bash /opt/snipetech/deploy/setup.sh`).

## 11 Nov, 10:30 UTC: the agents' round takes 2.5 s, not 42; the live page's paper table was wrong; two daily alerts were false

- **Still nothing profitable live.** No new live result: 74xVT9 is still the one qualified wallet.
- **The agents' round** spent 40 of its 42 seconds redoing the paper-test report from all 511,124 paper trades. A
  rule's numbers are now made when its trades change. The round takes 2.5 s (measured on the server with the same
  profile); the report took 2.3 GB of memory at its peak, and a whole round now peaks at 260 MB.
- **The live page's "Paper trading" table was wrong for launch-type rules**: it counted trades on a launchpad our
  snipers never buy on. Hunt rule bbe92cca81 showed 23 trades and +0.264 SOL; its record is 20 and +0.169. 18 of the
  36 rules listed had been dropped by the paper test and looked running. The page now shows the same numbers as the
  agents' report, marks dropped rules, and no longer re-reads every paper trade every 10 seconds.
- **Two alerts the server sent the owner every midnight were wrong.** One asked for SOL in the wallet, after the
  owner had said on 9 Nov not to flag that; it is gone for good. The other said "Anthropic credit: 0 $ left" from a
  count nobody could update; it now says "not known" quietly, and one Telegram message (`/credit <dollars>`) sets it.
- **Commands run on the server from outside no longer wake the paid agent** (8 turns for nothing that morning), and a
  "dry run" of the round no longer uses up what the next real round should be told.
- **A table that said "this pays" was wrong, and is now right.** The nightly "event edge" table (on the live page, in
  the strategist's evidence) showed +0.12 SOL a 0.2 SOL trade for buying when a curve passes 60 SOL, and +0.31 for
  "the creator paid out". Both came from the paper buy being placed ahead of the trade that made the event, inside a
  block we cannot land in, and from a signal picked by hindsight. Measured again where a buy could really land (a
  block later): every kind loses about 0.014 SOL a trade. Nothing had been traded on it.
- **Smaller ones**: the wallet-check lab loads 0.9 coins a scored buy, not 7.4 (205 ms a buy became 34 ms); one
  statistics routine no longer takes 2.3 GB for a single rule and is 2.6 times faster with identical results; the
  1,656 job records are no longer all re-read several times every 15 minutes; the analyst's opening message is
  shorter. What each one was, how it was measured and what is left: [audit-2026-11-11.md](audit-2026-11-11.md)
  (rows 35-45, open items 5 and 10-13).
- **Still with the owner** (asked 08:30, no answer yet): preconfirmations on copy snipers, and the crew sniper.
  And the one root command on the server (`sudo bash /opt/snipetech/deploy/setup.sh`): Ubuntu's updater killed our
  nightly job on three of seven mornings and froze the machine twice; the fix is in that script.

## 11 Nov, 08:00 UTC: the server never got settings changed in the code; the machine stood still for 11 minutes

- **Still nothing profitable live.** 74xVT9 stays the one qualified wallet; its first three signals were not bought
  (docs/live-recipe.md), so there is still no live result for it.
- **A setting changed in the code never reached the server**: its settings file is a copy of every default of install
  day. Found because the deployer verdicts, moved to our real landing at 06:48, were still written at the old one.
  A new check lists every such setting. Fixed: the verdict landing (3,350 deployers re-scored at it: 98 "snipe" with
  +0.07 SOL a 0.5 SOL snipe on average, +0.38 at most; before the two fixes 322 with up to +2.6), the limit the daily
  settings review had on the buy tip (ten times the intended one), a leftover "explore" share. A test now fails for
  any default changed later until the server follows it.
- **One of them is the owner's call: preconfirmations.** The code asks OKTech for them on every copy sniper since
  6 Nov (the copied wallet's buy is seen before it confirms; the account is approved). The server never asked. It
  changes how live buys are placed, so it stays off until the owner says yes.
- **The machine stood still from 07:12 to 07:23 UTC** (the same on 7 Nov, 07:10). No job of ours ran; Ubuntu's own
  update job held 15.6 GB. The tape lost nothing: its stream replayed the 11 minutes, the first real outage since
  that was built. The install script now makes the update job gentle; that needs the owner's one command as root and
  nothing breaks without it.
- **The recorder** uses a sixth of the processor per trade it did this morning: 0.95 ms a row written, 5.5 at 06:00,
  8.4 yesterday; 0.06 of one core. Two causes: text conversions done in plain Python and thrown away again, and its
  whole table of 48,340 known pools copied for every single transaction (a cost that grew with every new pool; found
  by letting the recorder profile itself on the server). It now writes down what it does every minute.
- **The strategy search ran for the first time since 9 Nov 19:16**: the wallet checks, which took every lab slot at
  2 h 18 min a run, take 12 minutes now.
- What is fixed, measured and open: [audit-2026-11-11.md](audit-2026-11-11.md) (rows 26-31, open items 7-8).

## 11 Nov, 05:10 UTC: the first wallet passed the forward check and is copied live; the audit's second round

- **Still nothing profitable live.** What changed: at 04:55 the book started copying **74xVT9**, the first wallet ever
  to pass the forward check (paper +0.0145 SOL a 0.2 SOL copy over 57 copies; the 26 copies it gave in the 40 hours
  after it was picked made +0.0095 SOL each). 0.2 SOL a buy, 3 buys, under the owner's standing settings. Seven
  wallets that looked as good on paper lost after their pick and were retired; that check is why they were not armed.
  The book stopped its sniper on 2Tp9sc (paper turned negative; nothing stranded). Its first live copies decide.
- **Why it took until today:** the queue never looked at a wallet twice (fixed 02:50), and the lab that does the
  looking scored all 22,204 wallets it had ever seen to answer for 50 (found 04:40, fixed 04:48). A run now takes
  12 minutes for 2,428 wallets (300 measured on six processes); it took 2 h 18 min for 300. The backlog of 41,000
  never-judged wallets clears in about a day.
- **The recorder** no longer does database work for every new coin or scores deployers beside its streams (a job of
  its own does, hourly). And it **replays what a restart or a stream drop made it miss**: the tape had 190 holes in six
  days (12 s at each deploy; twice half an hour and more, the server's resize and Helius turning our key away).
  Measured on Helius's stream first: a replay starts exactly where asked, in order, 20 minutes in 28 seconds.
- **Open:** the crew rule's yes/no is still with the owner; the crew has not bundled a launch since 8 Nov 12:20.
  (This line said the disk fills in late November: wrong. The server kept 6 days of tape; it keeps 14 since
  06:25, sized to the disk.) What is fixed, measured and open:
  [audit-2026-11-11.md](audit-2026-11-11.md).

## 11 Nov, 03:10 UTC: the whole system audited; why nothing ever qualified; the crew rule holds on our own tape

- **Still nothing profitable live** (three copy snipers, -0.2494 SOL over 24 round trips in three days).
- **Copying was blocked by our own queue, not by the wallets.** Qualify judged only never-seen candidates (41,450
  waiting, 10-16 thousand more a day), so it never looked twice at anyone: 25 wallets that pay on paper at our real
  landing (+0.02 to +0.06 SOL a 0.2 SOL copy, lower bound above zero) waited one to two days for the second look
  that qualifies them. Fixed at 02:50: they are first in the queue now. The first verdicts come from the run after
  03:50.
  A qualified wallet is armed by the book under the owner's standing settings; most will fail the second look.
- **The crew rule is positive on our own tape too** (exact block order, real hunt landing, measured costs): 27 launches
  with 30+ SOL in the first 1.5 s and a crew wallet among the buyers; on the 11 newer ones +0.048 SOL a 0.2 SOL trade,
  11 of 11 up. The crew has not bundled a launch since 8 Nov 12:20. One yes/no is with the owner (0.2 SOL, 10 buys).
- **Thirteen defects fixed and deployed** (recorder stalls of 50 s an hour and 16-36 s after each copy; the copy job
  days from failing; labs blind to fresh labels; the agents re-paying for the same text every half hour; more):
  [audit-2026-11-11.md](audit-2026-11-11.md), with what is still open (tape holes on restarts, disk in late November).

## 11 Nov, 00:00 UTC: one crew's launches are the first candidate for a live test

- **Still nothing profitable live.** The first rule that looks runnable: twelve wallets that bundle together launched
  266 coins in 36 days (7 a day); a buy a block after the create, sold a minute later, has a median of +21 % and pays
  77 % of the time before 2.5 % of fees (mean +7.5 %: one launch in four is dumped for about -65 %). Week by week the
  median is +32, +23, +27, +8, +30 %. Landing eight blocks late changes nothing. OKTech's Hunt takes a wallet list.
- **Why it is not a result yet:** the crew was found on the same 36 days; about +5 % a trade after fees on 7 trades a
  day is 0.3-0.4 SOL a day at 1 SOL; the crew can change wallets.
- **Before the owner is asked:** our own tape at the exact landing with an exit on the crew's own sell; a paper day.
- The owner asked about 100 SOL a day and was told plainly it is not in reach of sniping launches at 1-2 SOL a
  trade; a proven 1-5 SOL a day is the first target.

## 10 Nov, 23:10 UTC: the month re-read on ordered rows; one thing confirmed, nothing ready to trade

- **Nothing is profitable live and nothing is ready for a live test.** On ordered rows (36 days, 20,387 bundled
  launches, the last six never used to find anything) a buy a block after the create loses 6.5 % a minute later
  before fees. A website, the dev's own buy and a bigger bundle make it less bad; none makes it pay.
- **Confirmed:** a bundle team whose earlier launches averaged under -10 % loses 22-24 % on its next ones, in every
  period (4,590 launches). A filter for any launch rule.
- **Not confirmed:** a team with a +10 to +25 % record made +15 % over the 36 days (344 launches, positive median,
  robust to a late landing), +9 % in the last 18 days and -2 % on the 35 launches of the unseen days. The mean of a
  few earlier launches is too jumpy a score; a steadier one is next.
- **OKTech's native wallet-list rule (Hunt, known wallets) does not carry it**; a team rule needs our own trigger.
- **Copying** is being re-read on ordered rows (inside-block places now come from the token-reserve chain).
- Tables: [research/2026-11-10-three-bots.md](research/2026-11-10-three-bots.md), "The bought month on ordered rows".

## 10 Nov, 22:10 UTC: the month's first numbers were read from unordered files; they are being re-read

- **Nothing is profitable live, and nothing from the bought month is a result yet.** Tonight's tables (website rule,
  the dev's own buy, copying, a bundle team's record) were read assuming every hourly file holds a coin's trades in
  block order. Some do not: one launch in eight had a corrupted price path, which inflates the means. The fix
  (ea30e46) puts every file in chain order before it is scored; the scan over 5 Oct - 9 Nov (36 days, the last six
  never used to find anything) and the copy check run again on it. The owner was told the first numbers as they came
  and is told the corrected ones in the same words.
- **What the first pass pointed at, to be confirmed or dropped by the re-read:** (1) a bundled launch with a website
  is a filter, not a rule; (2) the dev buying 0.5-2 SOL of the coin sorts bundled launches better; (3) the record of
  a launch's bundle wallets on their earlier launches sorted them best of all, row by row, with positive medians in
  the top rows; (4) copying picked wallets at the end of their block is about break-even, and the value is in the
  place right behind the buy.
- **Owner, 21:00: "Figure it all out urself."** No more homework for the owner; one yes/no before anything spends
  SOL. The owner offered 100 SOL once there is proof; proof needs 0.2-1 SOL trades.
- **The path to money, fixed:** a month of history (both halves), then days never used, then our own tape at the real
  landing, then a paper day, then a capped live test with the owner's yes.

## 10 Nov, 20:10 UTC: a month of bought history is on the server; two checks run on it

- **The honest state is unchanged: nothing is profitable live.** What changed is the evidence we can ask: the owner
  bought pumpfundata.com's history (3,000 credits, about 720 used). Every pump.fun trade from 5 Oct to 3 Nov (712 hourly
  files, 16 GB) is on the server; one hour checked against our own tape holds 99.9 % of our SOL trades.
- **The website rule does not clearly hold so far.** One day of our tape said "others put 15+ SOL in the create block
  and the coin has a website: +22 % a minute later". The first five bought days (5-9 Oct, 3,781 such launches, read
  before launches whose own bundle fills the curve were set apart): +3.5 % with a website, +2.3 % without, median 0,
  the website side ahead on 2 days of 5. `history scan` over all 30 days runs since 20:08 and decides it.
- **Copying, asked of a month** (`history follow`, running since 20:08): every wallet's first buy of 0.5 SOL or more
  in a coin is scored as a copy right behind it and one trade behind, out at 5 s, a minute or the wallet's first sell.
  Wallets are picked on the weeks before the last and the same wallets are judged on the last week. Six days of tape
  could not tell a good wallet from a lucky one; this can. Numbers are after 2.5 % of fees and before the bid and our
  own price impact, so a pick is a wallet for the lab, not a result.
- **X accounts** (owner: "like X is gold verified"): `xaccounts.py` reads the account a coin links to (badge, followers,
  age) from twitterapi.io and `history table --x-max N` adds those rows. It waits for a key on the server
  (`TWITTERAPI_KEY`; pay as you go, no subscription).
- **Two mistakes of mine today, fixed:** a scan of 16 days died on one coin's malformed metadata address after 17
  minutes and my retry loop started it again (34d67e2: such a coin is kept as unreadable; a scan is saved before links
  are read; long jobs are started once and polled). `ruff format` on the whole tree rewrote 38 files; reverted before
  the commit.

## 10 Nov, 17:00 UTC: Proxima can be detected; a check of the whole system

- **The honest state: nothing is profitable live** (−0.29 SOL over 24 round trips in three days; three copy snipers
  running at 0.2 SOL). The best paper result is still the paid place one trade behind a tracked wallet's 1-3 SOL buy
  when a second known team is already in: 2 SOL at a 0.035 SOL bid, +0.064 SOL a trade (lower +0.049, n 704, 26 an
  hour). The bid that wins that place is not measured, and the forward paper test scores every rule at the live copy
  landing, so it cannot yet hold a rule at "+1 with a bid".
- **Proxima** ([research/2026-11-10-three-bots.md](research/2026-11-10-three-bots.md)). The owner named six coins; all
  six load one address lookup table at launch and `launch-tools` now reads it as a mark. It is on 3,401 of the newest
  30,000 launches. Where others put 10+ SOL into the create block, 54 % reach 30 SOL against 14 % of all launches, but
  a buy one block later is +1.4 % at a minute before fees: most are one crew relaunching the same names, dumping most
  in one block within 40 s and letting one run. The lead: buy only what still stands after a minute (17 trades,
  about +4 %, not evidence). The owner's page has a "Proxima launches" section fed by the newest finished scan.
- **Two bots' rules, read from 50 factors:** 6Z91 and Fhbh1 buy in the second a large buy lands in a coin that is
  moving, hold one to three seconds and sell at the first pause. No wallet list is needed.
- **Winners study:** `qualify` scored 60 of its wallets at the live copy landing; none qualified.
- **Found and fixed in the check (2d37b92):**
  1. The analyst ran every 30 minutes at about a dollar a turn (46 turns, 19.67 $ by 15:30) and most turns said the
     lab was still running. A cadence analysis now waits while research runs and nothing new has ended (three hours
     at most); with nothing running it still runs, to start the next test.
  2. The hourly paper run scored 35 frozen strategies, most long decided and several exact twins. A strategy with
     300+ trades and a mean three standard errors below zero is marked refuted and left out: 18 went on the first run.
- **Found, not fixed yet:**
  1. Failed duplicate sends cost 0.001 SOL each: 0.014 SOL on 9 Nov against 0.027 SOL lost on the six round trips
     themselves (idea I7).
  2. `[oktech] sell_fee_sol` is deprecated in the server's settings file and warns in every job log; `config` is not a
     remote command, so the rename to `sell_tip_sol` is a line for the owner's next SSH visit (same value, no effect on
     trading).
  3. The snapshot's "snipers armed" counts finished OKTech snipers too (9 listed, 3 running).
  4. The slice has crossed its soft memory limit 223,546 times (no kills, the recorder's memory pressure is 0): labs
     run two at a time near 24 of 26.9 GB.
- **Not a problem:** disk free fell 533 to 502 GB after the 12:30 resize and has held at 501 GB since; it was the
  hourly tape copy being rebuilt, not a leak. `doctor` passes on every line; the recorder is 2-5 s behind.
- **18:17-18:51 UTC: a 33-minute hole in the tape.** Helius refused the recorder's LaserStream connection with
  "Unable to verify project. Please check your API key" from 18:17:43 (before the 18:19 deploy; plain RPC calls with
  the same key kept working, `doctor` passed). The recorder kept retrying and has recorded again since about 18:51.
  Nothing of ours caused or fixed it. The gap is not backfilled yet.
- **Research jobs ask Helius at 120 requests a second since 18:49** (ff90ce5): the owner's plan is Business (200 a
  second, no rate limit hit in 24 h, 35 of 100 million credits used); scans were held to our own 40.
- **Bought data, if the owner buys it:** PumpFunData's hourly files (sample checked: wallet, slot, signature, reserves
  after each trade, creator; no place in the block, no metadata, no fees) are the pick for months of history; the first
  step after a key is a trade-for-trade comparison with a day we recorded ourselves.
- **Next, in order:** (1) a `launch_marks` table written by the scan and then by the recorder, so a re-scan costs
  nothing and the lab can gate on a mark; (2) the lab for organised Proxima launches bought at 30-120 s if still above
  the create block's close; (3) the bid that wins the place behind a known wallet; (4) forward paper at "+1 with a bid".

## 10 Nov: what 6Z91 does that we have not tried; the analyst runs on a stronger model (11:50 UTC)

- **The honest state: nothing is profitable yet; the untested thing is now named.** 6Z91 (the owner's friend's bot,
  +459 SOL gross in 72 h on our tape) buys in the same block as the wallet it copies, pays a median 0.033 SOL a buy
  for the place right behind it, buys 3-5 SOL and sells about 5 seconds later. On 339 such coins the price is +1.3 %
  two slots after the leader's block, −1.7 % at 15 slots, −6.9 % at one minute and −18.5 % at ten minutes. Our own
  copies land in the leader's block too (20 of 24 since 7 Nov, 0.3 trades behind): speed is not the gap. We tried
  0.2 SOL at a 0.003 SOL bid behind quiet scalpers; the paper tests scored KOL copies behind the 3rd buy after the
  leader's. Record: [research/2026-11-10-same-block.md](research/2026-11-10-same-block.md).
- **12:30: three bots read off the chain, and the owner's splits scored** ([research/2026-11-10-three-bots.md](research/2026-11-10-three-bots.md)).
  6Z91 and two more bots follow no wallet list: each buys 1-3 SOL right behind someone's 1-3 SOL buy, in the same
  block, and sells on a 3-6 s timer in pieces (+380, +73 and +92 SOL in 72 h before bids). A copy right behind a
  known wallet is worth +8 to +11 % at 5 s, before costs, when a second known wallet is already in the coin, the
  coin has 60+ SOL of volume or 5+ minutes of age, or the followed buy is 1-3 SOL; behind the first known wallet in
  a young, thin coin it is worth about nothing. Costs at 2 SOL are near 8-10 %, so only the combined good splits can
  pay: that is the lab and forward-paper test now (I51).
  The same buys entered one block late are worth −1.7 % at 5 s and no split is above +1 %: the trade exists only
  right behind the wallet, in its block.
- **13:00: the lab can test what the tape points to** (bebe012). Its KOL rule fired only on the first known wallet
  into a coin; a kol trigger now takes OKTech copy mode's own gates (`known` = ctbotMinWallets, `within`, `max_sol`,
  `min_curve` / `max_curve`), the exit set has 3 s and 5 s timers, and `--copy-position +0 --per-tx <bid>` scores the
  place right behind the wallet. First run queued: second and third known wallet in, 1-3 SOL buys, 1 SOL a copy, 0.02
  a transaction, 72 h. The second bot's rule is read out (hot coin this second, sell at the first pause).
- **The server is 32 GB / 8 cores / 644 GB since 12:30** (owner). The slice still holds the old 12 GB limit until
  `systemctl set-property snipetech.slice MemoryHigh=80% MemoryMax=88%` is run as root; the owner confirms the
  account has OKTech's [S] tab (Shapes / Web / Hunt), which `oktech.py` still treats as not granted.
- **The analyst runs on claude-fable-5-2 since its 12:17 turn** (verified in operator/turns.jsonl: the model that
  answered, three recorded pieces of reasoning where earlier turns had none, 0.68 $ for the turn).
- **What is being tested, in order.** (1) A paid place right behind known wallets at 1-5 SOL with exits in seconds
  (idea I51, lab on 72 h, then forward paper; a live step needs the owner's yes). (2) The owner's splits with the new
  `copy-context` study: the second known wallet in, the coin's volume, its age, the hour, the buys later in the
  leader's block. (3) Leaders nobody follows inside their block (I50). (4) The winners study's mid and late swing
  traders (I46): 40 picked wallets wait for their copy verdicts; `qualify` waits while a lab runs, so it is started
  right after the next deploy.
- **Refuted since 8 Nov:** KOL first-buy copies at every size on forward paper (20+ trades), single KOLs, rug crews'
  coins at launch, KOL buys on coins under 2 minutes old, three momentum versions, whale buys of 2-10 SOL.
- **The analyst runs on `claude-fable-5-2`** (owner, 10 Nov: "ok do it"; 27b0e13). The operator stays on
  `claude-sonnet-5-5`, the strategist on `claude-opus-5-5` (`[operator] strategist_model`). The spend ledger's prices
  are corrected (it overstated every line) and each agent is priced at its own model's rates. The server's settings
  file pinned the old model and prices, so a value there that equals a retired default now follows the new default.
  At 9 Nov's amount of work the analyst costs about 35 $ a day instead of 19 $; the operator about 2.60 $. Compare a
  day of its ideas and verdicts with 9 Nov before keeping it.
- **Built 9-10 Nov:** the live page (`/dash`, waits for the owner's password), the turn record with each agent's
  reasoning, `winners` and `leaders-of` (how the most profitable wallets trade and who a wallet follows), the lab's
  crew knob, exitrun's wallet stream (our buys are seen within a second).

## The night of 8 Nov: what the live trades really cost; the recorder off the database's waits

- **The live database's trades table was damaged at 09:51 UTC 8 Nov and is repaired (11:49).** Twenty minutes after the
  recorder's new checkpointer thread (recorder-writer, deployed about 09:30) went live, every write and recent read of
  `trades` failed with "database disk image is malformed" (quick_check: tree 7, the trades table's own pages near the
  end of the file). The change was rolled back at 09:55 (1c1497c); its approach stays out until a deeper review.
  New `snipetech db check | salvage | swap | backfill | forget` repair from the remote channel without SSH: the
  readable rows went into a fresh table, the damaged one was renamed `trades_damaged_1794138625` and kept, the recorder
  writes again since 11:49, and `db backfill` copies the rest of the old rows (about 4,000 a second, a few hours).
  Lost: trades from 09:51 to 11:49 and the rows on the damaged pages. Once the backfill ends, `db forget` takes the
  damaged table out of the schema so the hourly copy works again. Three 7aPo3n copy sells (11:30-11:44) left only
  their failed duplicates on record; their result comes from the chain.
- **Fees per task and the duplicate sells (12:50 93e7177, 14:20 d36a500).** Each OKTech exit is two sends: a
  relay-sent sell with the tip (lands) and a plain send at `sellPriorityFeeSol` (fails with 6023, keeps that fee).
  The bid now lives in `[oktech] sell_tip_sol` (0.002) and the plain send pays `sell_priority_sol` (0.0001): about
  0.002 saved per exit. `src/snipetech/fees.py` chooses each sniper's four fees by kind (copy per leader, hunt,
  launch, MIG, probe) from measured landing inside the owner's `[fees]` bounds; the strategist may only cap them.
- **The live book, 8 Nov:** 7aPo3n +0.129 on 9 copies; AG89fn −0.418 on 6 (copy exits behind one-slot dumps), paused
  by the book's own rule at 13:20. The day at 13:30: −0.288 SOL on 15 copies. Building: a dump-pattern check before
  the book arms a wallet; our own sells for timer and stop exits; the operator's coins counted forward to 20.
- **The lab, the paper test and the automatic test count only coins of the launchpad their sniper buys** (b52c4ca,
  9:50): LaunchLab coins no longer reach pump.fun rules' records.
- **The live book's real result is worse than its records said (merged 8 Nov 00:50, not yet deployed).** Every live
  round trip of 5-7 Nov broken down in code (`measure/attribution.py`) and checked on chain: the 58 recorded sells made
  -1.2142 SOL, not the recorded -1.0419, because `fills.pnl_sol` leaves each sell's failed duplicate sends apart
  (`twin_fee_sol`); with the two PumpSwap trips the tape never saw and the seed-account sweeps of 5-6 Nov, the 59 trips
  made -1.2822 (-0.0217 a trip, 4 won). Where it went: the price fell 0.92 SOL on 33 trips and rose 0.67 on 26 (CAT
  alone +0.52), the buys' fees took 0.43, the landed sells' 0.21, failed duplicate sells 0.18 (65 sends), pump.fun's
  1.25 % a side 0.16, seed sweeps 0.06. 7aPo3n's three copies of 7 Nov rose +5 % a trip before any cost (+0.0294 in
  all) and ended -0.0119 after 0.0412 of costs. Every reader of live results (the day stop, the automatic test's
  blocks, the post-mortem, read_fills, the fee-test report, the strategist's evidence) now counts each cost once, and
  after each post-mortem the owner gets the numbers, the biggest cause and the change that would have saved most in
  Telegram. The cheapest levers: OKTech sending each exit once (0.002-0.004 a trip; OKTech's to change) and the sell
  fee back to 0.001 SOL (about 0.002 a trip; the owner's yes); at 0.2 SOL a trip needs a price rise of 6.6 % to break
  even (5.6 % after the sell fee).
- **The recorder paused twice after the 23:14 deploy (14.2 s at 23:35, 6.9 s at 23:37), and never fell behind.** The
  first: the events scan's last step wrote 185,251 events in 371 back-to-back batches, and SQLite's busy wait (a poll
  every 100 ms) never got the recorder in between. The second: the recorder's own small write, most likely SQLite's
  automatic checkpoint inside its commit. A reviewed build (recorder-writer) moves the recorder's writes to one writer
  thread on its own connection, makes bulk writers pause between batches and writes dev-transfer edges in batches.
- **The server's agents will read the research record (research-memory, building).** On 7 Nov evening the analyst
  froze two creator-0 hunts and labbed a third while round 3 refuted them: the agents never saw docs/research. Every
  agent turn will carry the newest round's outcome and lessons, and a round's refutation settles an idea.

## The evening of 7 Nov: the lock fix is live; snipetech was using 2 of the server's 6 cores (19:45 UTC)

- **Correction, 8 Nov 00:05 (research round 3): do not /test the creator-0 hunts.** On 20.8 h of 4-5 Nov tape that no
  screen or lab had used, both rules frozen on 7 Nov evening lose with their frozen exit: f94add343f's rule -0.0099 a
  trade on 19 firings, 7d6b4302ff's -0.0553 on 8. Their good labs came from one bundle operator's launches (a ~41 SOL
  first-slot bundle from 15-21 wallets, no creator buy, +35 % in 20 s, then one dump), which are also all of
  7aPo3n's early buys. They stay on paper; a build (dev0-gate) keeps every maxDevBuySol 0 hunt out of live trading until
  OKTech confirms that 0 means "no creator buy allowed" (if it means "no limit", the plain crowd hunt runs: -0.0147 a
  trade on 1,285 unseen firings). For the owner: ask OKTech that question; and say yes or no to a stop rule for the copy
  book (retire a copied wallet after 15 live round trips at a mean of 0 or less, fees and failed sends included).

- **Short reads deployed 21:28 (4ff28dc), and the database shrank from 19.7 to 13.0 GB.** The heavy readers of the
  live file now read the hourly copy (discover's tape sources, a whole-table GROUP BY every hour that ran tens of
  minutes; the events scan; paper freeze; the Birdeye and coin-first sources), the paper run reads in 10-minute pieces,
  remote sql stops at 30 s, and every connection caps the WAL at 64 MiB once it restarts: the -wal file went from
  7.33 GB to 0.07 GB. The copy is now opened read-only and immutable and never written: its review found that a
  read-write open could leave a -wal beside it that the next renamed copy read as "malformed".
- **Deployed 23:14 (66a88f1): /sell sells PumpSwap coins; the book copies only wallets that pass after their pick.**
  `/sell`, the unsold-coin watch and `snipetech sweep` now sell a coin that left its curve on its PumpSwap pool, built
  from OKTech's own landed sell from our wallet (6 Nov 05:15:55; the proceeds land as WSOL, cashback coins are
  refused), and leave alone any holding worth less than the ~0.001 SOL fee to sell it. The qualify stage scores a copy
  behind the 3rd buy after the leader's across slots (never cheaper than right behind the leader), drops wallets with
  10 %+ of their first buys in create slots, and qualifies a wallet only if its copies after its latest pick also
  pass (20+ copies over 6 h+). Wallets the book copies now (7aPo3n, AG89fn) keep their verdict and score; their
  verdict says if they no longer qualify, and the owner decides.
- **The creator-0 crowd hunt is in the forward paper test, twice.** The analyst's 48 h lab of `{"kind": "hunt", "sol": 15,
  "buyers": 15, "window": 1, "dev_max": 0}` (job 20261107-221611-769313, live landing and measured cost) made +0.045 a
  trade on its test part (n 11, 10 won, lower bound +0.037; 26 moments in 45 h). Frozen at 23:00 as `7d6b4302ff`
  (its freeze lab chose SL-15 % / trail 10 % / 60 s: +0.0179 a trade, bound +0.0106); the looser ">= 10 SOL from >= 10
  wallets" version has been `f94add343f` since 21:31. Both fire about once every two hours, so the paper test needs
  days. (Round 3 refuted both on unseen tape: see the correction above.)
- **Slow writes name themselves (21:34, 25a3891).** Every write takes the lock up front (BEGIN IMMEDIATE), and one
  that holds it 5 s or more logs the code that asked for it in that job's log. After the slice change the recorder
  still waited 23.5 s for one write at 20:52 (as the events scan ended) and 12.6 s at 20:56 (during a qualify run).

- **Deployed d0b480e at 19:39,** between two labs: no writer holds the database's write lock for long (the
  recorder's hourly prune goes in short steps beside its writes, every failed write rolls back, batch inserts commit
  every 500 rows); MIG scored where its buys land, only on complete PumpSwap tape; every lab charged the live cost
  (0.0045 SOL a transaction until measured) and OKTech's 1.3 s watch delay, and the live test refuses a rule scored
  without them; measure no longer books another wallet's failed transfer into ours as our failed send (4 rows at
  17:50, the owner's SOL coming in); the recorder says why when it falls behind (database, its own CPU, the load).
- **The first reading of the server's limits (19:39, `remote status`):** the slice set on 4 Nov to share the box
  still capped snipetech at **2 cores and 2.73 GB** on the 6-core, 16 GB machine. The CPU quota had throttled 15 % of
  periods (12,888 s of CPU denied), memory.high had been hit 4,027,792 times, the recorder waited on the disk 9 % of
  its time, the WAL file alone was 7.33 GB of the 19.71 GB, and nothing else on the machine held 100 MB. It showed:
  `discover` hit its 50-minute limit at 17:57 and 18:57 (4 minutes on 6 Nov), and the recorder fell 125 s behind at
  19:32 and lost its stream at 19:33.
- **Done 20:49: the owner ran the one command.** At 20:50 the slice had no CPU quota and a 12.88 GB memory limit
  (8.56 GB already in use, the database's pages cached at last), CPU throttling had stopped, and the recorder waited
  0 % of the minute for CPU or memory. The stall meter's first hour (19:39-20:49, before it) showed what held the
  recorder: its loop sat 30-54 s of every minute in database calls (the hot path's small writes 20-34 s, the trade
  flush 8-15 s, the prune once 25 s, one flush 43.8 s) on 0.3-0.66 of a core: waiting on the disk, not on the CPU.
- **The command was** (root, over SSH): `sudo bash /opt/snipetech/deploy/setup.sh && sudo
  systemctl set-property snipetech.slice CPUQuota= MemoryHigh=12G MemoryMax=14G && sudo systemctl restart
  snipetech-tape snipetech-strategist.timer`. It installs the recorder's first call on the CPU (CPUWeight 1000), an
  ops service whose restart no longer kills the jobs it started (KillMode=process), the strategist's daily timer, and
  lifts the slice's limits (68f6d31 puts the same values in the repo's slice file).
- **Being built:** short reads (no job holds a read snapshot of the live file for long; heavy readers read the hourly
  copy; the WAL file capped after it restarts), reviewed before merge. Research round 2 runs its five labs one at a
  time; its report goes to docs/research/ when it ends.

## The afternoon of 7 Nov: live copying armed, the tape stalled for 1 h 45 min and now heals itself (16:15 UTC)

- **Deployed:** the owner's guard and the automatic test (c265fb7), then live trading that never stops (d682e4d: each
  `/risk` stop can be off, the book's floor and day stop follow `/risk`, the automatic test has no block limit and drops
  a failing strategy for the next), then the recorder's self-healing (fa49772). The guard stands at a 5 SOL day stop and
  no floor (12:55); `/risk stop off`, `/risk floor off` and `/test auto on` are the owner's to send in Telegram.
- **Live copying:** at 13:50 the book armed a copy sniper on `AG89fnx9…` (qualified at the copy landing: n 27, +0.0265
  SOL a trade, lower bound +0.0071, crowding 4, a dumper with its first sell at a median 17 s), 0.2 SOL on its buys of
  0.3 SOL or more. It has bought nothing yet (the leader only sold in the hours after).
- **The tape stalled 14:03-15:48:** a long write lock held by another job, then a write that gave up left the recorder's
  connection in an open transaction, and every later write failed ("database is locked") until a deploy restarted it;
  the database grew from 11.3 to 15 GB. Nobody was told: the only alert was the operator's model, refused by the API.
  Fixed (fa49772): a failed write rolls back, the recorder waits 20 s at most per write, keeps refused trades in memory,
  reopens its connection after 3 minutes and restarts itself after 10, and a tape 5 minutes behind is a critical row
  (Telegram, by code). Still to find: which job held the lock that long (events scan, qualify, search or paper).
- **The owner trades by hand in the sniper wallet** (Axiom): Db3AN1YJ bought 15:24-15:25 for 1.55 SOL, sold 15:30-15:37
  for 2.44 SOL, and moves SOL between their wallets (`H4SJzJ…`, `CsLVhf…`, `GXcAHhvu…`). At 15:45 the wallet held 0 SOL.
  Look-alike addresses keep sending it dust to be copied from its history: `95o22tih…` (for `95o2L9Jm…`), `GXcANzWb…`
  (for `GXcAHhvu…`), `CsLVZuXJ…` (for `CsLVhfV6…`), `H4SJmUSq…` (for `H4SJzJbp…`).
- **The server's agents** were refused by the API from 11:16 to 18:12 (an Anthropic verification of the organization
  that owned the server's key, `b8b652c0-…`). The owner made a new key on a new Anthropic account and put it in
  `/etc/snipetech/env` at about 18:05; the analyst's 18:12 turn ran (25 tool calls, 0.94 $) and the 18:15 round too. A
  key changed in the file now reaches remote jobs without a restart (6bd539a). The research also runs from the cloud
  session (docs/research/): round 1 found no rule that pays at our landing (MIG is the open idea); round 2 is running.

## The wallet was emptied at 10:59; no rule has an edge where our buys land yet (7 Nov, 11:15 UTC)

- **The sniper wallet:** at 10:59:53 UTC one transfer, signed with the wallet's own key, sent its 11.69 SOL to
  `GXcAHhvu…4SinHRtQ`. In the same second 15 other wallets were emptied into that address, the owner's funding wallet
  `95o2L9Jm…D6WaiC3` among them; 15 s later that address sent 8.73 SOL back to `95o2L9Jm…` and 8.73 SOL to `CcFLnC…`,
  and it takes payouts from Axiom's address. It reads as the owner gathering their Axiom wallets; the owner was asked
  to confirm. This repo holds no wallet key and sent nothing. The wallet holds 0 SOL and 0.22 wSOL; the book stopped
  itself below its floor; nothing is held and no sniper runs. Nothing trades live until the wallet holds 10 SOL again
  (the owner's guard, 09:15).
- **Address poisoning:** at 07:21 a look-alike address, `95o22tih…2aiC3` (the same first and last four characters as
  the owner's `95o2L9Jm…WaiC3`), sent the wallet 0.00001 SOL so the fake appears in its history. Check the whole
  address before any transfer.
- **The corrected search (08:16–08:57, 23.6 h of tape, at the real landing):** all 24 Hunt settings lose about
  0.007 SOL a trade, about what a round trip costs: no gross edge where our buys land. Nothing was frozen. Copying the
  tracked wallets' buys of 2 SOL or more: −0.0079 a trade.
- **The analyst, hourly, one hypothesis a turn:** a 1 SOL hunt in 5 s capped at 4 buyers lost 0.0067 a trade over
  1,738 trades (refuted). The one qualified wallet, `2Tp9`, draws 13.9 other buyers in the 2 s after its buys, as many
  as the two wallets whose live copies all lost. The best paper strategy (`7b15e38f74`, n 83, +0.0013 a trade) owes
  all of it to its five best trades. A trigger the lab would refuse now comes back to the agent at once (a11f72b).
- **The owner's yes (09:15, `docs/live-recipe.md`):** the first strategy that passes on paper starts one capped live
  test (0.2 SOL a buy, at most 10 buys and 2 hours, a 1 SOL stop) and goes on only at half of paper. `/test` in
  Telegram is live; the automatic start and the wallet-wide guard are being built, with PumpSwap trades of coins that
  just migrated, so MIG snipes and the migration sell can be scored.

## The launch rule that "held" was a landing artifact; the lab now scores where OKTech buys (7 Nov, 08:30 UTC)

- **Correction (08:20):** the forward paper "winner" below is not evidence. Its whole profit came from a handful of
  trades: one hour (04:00-05:00 UTC) made +9.98 SOL of its +10.3, and its three best trades (+11.0 SOL, 12-23x in no
  time) were coins whose create slot held a 22-wallet bundle of 115-150 SOL that completed the curve inside that slot
  (BRUH, PgFtGG, jpfML7). The paper test bought six trades behind the trigger inside that very slot and sold on
  PumpSwap; no live buy can land there (OKTech's Hunt buys a slot or two later, and its pump.fun buy fails on a
  completed curve). Without its 15 trades above 0.5 SOL the rule lost -9.3 SOL over 1,212 trades (mean -0.0076).
- **Fixed (eae2d0b, deployed 08:30):** every launch-type rule (launch, hunt, whale, burst) is scored where it lands
  live, in the same lab pass (whale, burst and hunt a slot after the trigger, 3 trades in); the paper test re-keys
  the frozen ones there; and a launch-type buy on a coin that has left its curve is no trade. Copies keep the copy
  landing. The 5 Nov hunt fills check the landing: the model enters 0-2 trades later than live did, never earlier.
- **What this means:** there is no launch rule with evidence yet. The next search (it starts when the wallet-check
  lab ends; then every 6 hours) scores 24 Hunt settings and the old rules at the real landing.

### What was written at 07:40 (kept for the record)

- **The forward paper test, 9 h of tape recorded after the freeze, at our real landing, 0.2 SOL fills:** "a non-dev
  buys 1 SOL or more in the first second of a normal coin, not inside the create bundle", lab exit (dead exit 1 s,
  stop-loss 30 %, 300 s): 1,200 trades, +0.0089 SOL a trade, lower bound +0.0035, +10.6 SOL in total (the lab had said
  +0.0124). The 5 s variant: 1,244 trades, +0.0079, lower bound +0.0028, +9.9 SOL. The other two frozen with them:
  +0.0012 (lower bound −0.0017) and −0.046. Every strategy the old lab froze keeps losing (for example "at the
  launch" −0.0099 a trade over 11,375). This is the first rule since the lab fix that stays positive on new tape.
- **Why it is not live yet:** OKTech cannot run that rule as scored. Its launch gate (Hunt) adds up every buy in its
  window, the creator's own included, takes whole numbers only, cannot see whether a buy sat in the create
  transaction, and cannot skip mayhem / holder-reward coins. `oktech strategy` sends an approximation (2 SOL in total
  within 1 s, the creator's buy at most 1 SOL, at most 2 wallets) that was never scored. Since 07:32 the lab has a
  `hunt` trigger that replays Hunt itself and scores it a slot after its trigger, 3 trades in (5 Nov hunt fills
  landed 1–2 slots after the create with 2–7 buys ahead); `oktech strategy` sends a hunt strategy exactly as scored.
  A strategy search started at 07:35 scores 24 Hunt settings, the approximation among them, on the newest 24 h.
  The live test waits for a Hunt setting with a positive lower bound there, and the owner's yes.
- **Paper is not live:** paper charges 0.007 SOL a round trip, live costs about 0.009 (failed duplicate sends);
  a Hunt sniper fires once per start and has to be re-armed (`oktech cycle`), so live takes far fewer than paper's
  ~130 trades an hour; and OKTech has left the sniper's cap-using buy without a sell three times (4siK, DTAU, 5m5h),
  so a live test runs with the unsold-coin watch and `/sell`.
- **The night:** the fixed lab qualified its first wallet, `2Tp9scbr…XUB8D65` (n 25, +0.0066 SOL a trade, lower bound
  +0.0024, crowding 11.5), below the book's bar (score 0.0004 against 0.005), so not copied. 5m5h, the coin OKTech
  abandoned, was sold at 05:38 for 0.076 SOL (0.1975 paid: −0.121 on the trade, −0.167 with the paid arm's fees). Free disk fell to 23.7 GB (a 12.9 GB database;
  long labs kept old read-only copies open), so `snapshot-db` now skips a copy that would leave less than 10 GB.
- **The server, resized 7 Nov ~07:10 by the owner:** down 07:12–07:26 UTC; back with 322 GB of disk (276 GB free),
  every service and timer restarted on its own, the tape recording again (the gap is a hole in the tape, not bad
  data). The owner topped the wallet up: 11.9 SOL at the 07:30 snapshot, nothing held, no sniper running.
- **The agents work harder (owner, 07:30: "I can afford 200$ a day in api credits"):** measured spend was 4.44 $ on
  6 Nov and 1.92 $ by 07:30 on 7 Nov, far under the 15 $ cap: the agents were idle by design, not by budget. Being
  built now: an owner budget (`/budget` in Telegram, no SSH), the analyst every 2 hours instead of once a day,
  hypothesis labs the agents run on their own trigger ideas and freeze into the paper test, and one lab on all CPU
  cores (one lab at a time still).

## The OKTech audit, built (6 Nov, evening UTC) — the leaks are closed; no wallet has proven itself live yet

What the audit found on today's live trades (docs/oktech-audit-2026-11-06.md, the plan in the audit workflow):

- **2YDn sells everything a few seconds after it buys.** All its copies lost (5 of 5 in the audit window, 10 live
  fills at −0.045 SOL a trade by 17:50). No exit could have saved it: the best price reachable after one second still
  loses. The lab rated it +0.0146 SOL a trade because it assumed a block takes 0.4 s (the tape says 0.214 s), had no
  watch delay before OKTech's exits act, no copy exit, and scored a different exit than the book sends.
- **DTAU was a sniper's last allowed buy, and OKTech never sold it** (no dead exit, no copy exit when G2oZ sold
  99.9 % at once, no 300 s timer). The same happened to 4siK on 5 Nov: 2 of 4 such buys.
- **Paying 20× the fee landed no earlier** and OKTech charges the buy priority fee again on every exit.
- **Two snipers on one leader from one wallet get one buy between them** and both try to sell it.
- **Every wallet got the same floor (0.3 SOL)**, so we also copied G2oZ's small top-ups (one lost 34 %), not only its
  big opening buys (+17 % and +26 % at one minute, n 2).

What changed (code on the branch, each piece with its tests; README "How the book uses OKTech"):

- The book relies on each sniper's **first buy only** and replaces the sniper once the wallet holds nothing; a wallet
  is **paused after 5 losing live copies**; **one sniper per leader**; each wallet is copied only on **buys of its own
  floor or more** (the lab's `copy_plan.min_leader_sol`); the paid arm is retired.
- **The unsold-coin watch**: a Telegram line within about a minute when a held coin's exit was due and nothing sold
  it, repeated every 15 minutes while held. It never sells. (Since the audit of 22 Nov it tells whose the coin is: a
  coin the owner bought by hand gets one line a day as theirs, with no /sell line; a copy of ours that only the
  measure stage's fills hold, an older PumpSwap coin, gets the 15-minute alert, not the airdrop line.
  docs/architecture.md, "Whose trade, whose coin".)
- **The lab simulates the live sniper**: measured slot time, OKTech's watch delay (1.3 s), the copy exit, the exit the
  book sends, the real round-trip cost. It reproduces 11 of 12 of today's OKTech-sold copies within 2 slots and their
  proceeds within 0.2 %. Every verdict written before this fix is re-qualified first; until then the book copies none.
- **Measure** labels which exit sold each coin and charges each sell its failed duplicate sends.
- **The agents** (operator, analyst, strategist) get one OKTech playbook of twelve measured facts; the daily caps, the
  buys per sniper and the buy priority fee can never be tuned by the strategist.
- **A review of the merged build** (four reviewers, a skeptic per lens) confirmed 15 defects, all fixed with tests
  before the deploy. The two that mattered most: a retire could stop the sniper that had just bought (the wallet read
  lags our own tape by seconds), and the qualify lab never measured newly labelled wallets (they got "no tape data").
  Now a fresh buy on the tape counts as holding, holdings are read at the confirmed level and again after the stop,
  nothing new starts while the tape lags, each candidate is scored on its own first buy in a shared coin, and a
  live pause is remembered.
- **5m5h (20:33):** a third coin OKTech abandoned (docs/live-recipe.md). Our own sell was refused by the cloud
  session's safety layer; the owner sells by hand. Next: `/sell <coin>` in Telegram and the self-learning loop.

- **The agents learn by themselves** (the owner's "configure it so the agents automatically learn by themselves and
  consistently think about why, what to improve"): a shared memory (operator/lessons.md, one dated lesson a line with
  its evidence and n; operator/ideas.json, ranked improvement ideas each with its evidence and a concrete test) that
  every operator, analyst and strategist turn reads first; a post-mortem turn after every 5 new live round trips
  (at most every 3 hours) that puts each loss in one cause (late or crowded landing, a leader that dumps, an exit too
  early, an exit too late or none fired, fees and failed duplicate sends, a bad coin), counts the causes, writes the
  lesson and starts one research job to test the top idea; the strategist may tune daily, but only after 20 new live
  round trips; the 6-hour digest says what was learned and what is tested next. Code ideas are built and deployed by
  the hourly autopilot session; live tests still need the owner's yes. Nothing in the loop trades.
- **Exits per wallet, shadowed first** (audit item 11): a menu of three OKTech exits (lab: dead exit 1 s, stop-loss
  30 %, 300 s, copy exit 50 %; hold60: 60 s timer, copy exit; ride: trailing stop 25 % after +20 %, stop-loss 30 %,
  300 s, copy exit, no dead exit). The lab scores every wallet on all three and keeps a wallet's own exit only when it
  beat lab on moments it was not chosen on; the gating numbers stay lab's. Measure replays every live copy buy under
  each exit six minutes after it (table exit_shadow), so the post-mortems say which exit would have done better and by
  how much. The book still sends lab to every wallet (`[book] exit_mode = "fixed"`); only the owner switches it to
  `per_wallet`, and only exits in `verified_exits` (lab, hold60) are ever sent; ride needs one capped live fill first.
- **`/sell <coin>` in Telegram**: the owner sells a coin OKTech abandoned with one message (it needs SNIPER_SECRET
  in /etc/snipetech/env); `/sell` alone lists the coins held with the line to send.

- **The fixed lab passes its first check (7 Nov 00:00):** 2YDn, qualified by the old lab at +0.0146 SOL a trade
  (n 93), is retired by the fixed lab: -0.0075 SOL a trade over 220 moments at +6, lower bound -0.0144. Its live
  copies lost -0.045 a trade (n 10), so the lab now agrees with live in sign. Its copy plan reads as live showed:
  style "dumper" (82 % of its exits in one transaction, first sell after 7 s at the median), floor 2.962 SOL (its
  openers), 41 % of its qualifying buys were re-buys. The audit had predicted exactly this.
- **The first strategy search with the fixed lab** (6 Nov 22:15-23:50, 46.7 h of tape, 442,656 moments, the +6
  landing, 0.2 SOL fills): the best was "a non-dev buys 1 SOL or more in the first second of a coin that is not a
  create bundle", lab exit: +0.0124 SOL a trade over 2,698 cases, lower bound +0.0089, about 144 signals an hour;
  three variants +0.006 to +0.011. All four are frozen into the forward paper test. Caution from the same report:
  every launch strategy frozen earlier (by the old lab, which predicted +0.02 to +0.32 a trade) lost on new tape at
  our landing, for example "a non-dev buys 1 SOL or more" -0.008 a trade over 1,254 forward trades and "2 SOL in the
  first second" -0.034 over 540. No live test of a launch strategy until the new four hold up on new tape.

What happens after this deploy: the first runs re-qualify old-lab verdicts (qualified first), the book copies only
verdicts of the fixed lab, the research rotation keeps one lab busy whenever the server is idle, and a digest goes
to Telegram every 6 hours. Honest outlook: this closes the leaks; it does not make money by itself. No copied
wallet has proven itself live yet; G2oZ's opening buys are the best lead (2 of 2 rose after we bought).

## First live book day and the fee test (6 Nov, 18:10 UTC) — the landing is fine, the trades are not

- The owner switched the book on (16:30), lowered its bar to 0.001 (17:01), enabled every timer over SSH (17:10)
  and removed the daily limits. The book copied 2YDn from 17:01: 10 live fills at −0.045 SOL a trade, so it dropped
  the wallet at 17:50 and waits (no wallet passes). Since 15:08 the wallet lost about 0.6 SOL on 13 round trips.
- **Fee test:** paying 0.04 SOL a buy did not land earlier than the 0.003 SOL arm; both landed under one trade behind
  the copied wallet at about +2 % (docs/live-recipe.md). Speed is not what loses the money: the trades and the exits
  are. The paid arm stays off.
- **Next:** the OKTech feature audit (exits replayed on every live trade's price path, entry filters, execution
  settings, how the book and the agents choose them per wallet) becomes the build plan; the forward paper test runs
  around the clock again; the owner added SOL (7.4 SOL equity) for a capped test once the exits change.
- **The lab now copies what the live sniper does (audit items 6 and 7, built 6 Nov evening).** It counted a block as
  0.4 s (it is 0.214 s), so its 1 s dead exit sold on half-second pauses OKTech never sells on; it scored wallets with
  exits the book never sends; it ignored the copied wallet's own sales. Now it measures the block time, waits the
  1.3 s OKTech waits before it watches a coin, sells when the copied wallet sells half its bag, charges the measured
  cost of a round trip, and scores each wallet only on the buys its sniper will copy (its own minimum buy, above its
  top-ups). Checked on the 12 copy trades OKTech sold on 6 Nov: the same exit within 2 blocks on 11, the money within
  0.2 % (the old lab: 6 of 12, 4 % too rosy). **Every lab number changes from this fix on** (qualify verdicts, the
  lab, the strategies the paper test freezes, the event edge); older numbers are not comparable. After the deploy,
  qualify every wallet again: 2YDn's mean should turn negative, and if it does not, no verdict feeds the book until
  we know why.

## The bot runs by itself (6 Nov, 16:00 UTC) — what it waits for

Deployed at 15:58 on the owner's "go do it": every 15-minute operator round now starts the loop's stages itself
(measure, the book, the event feeds, discover, qualify, the read-only database copy, the event scan, the Sunday
strategist), because the owner has not enabled their timers over SSH. The first round at 16:00 started measure, the
book, the feeds and qualify with nobody logged in.

The book is still off: switching real money on is the owner's `/book on` in Telegram (this session's safety layer
refused to do it). Once on, it buys only when both hold: equity of at least 3 SOL (2.65 now) and a wallet that
scores at least 0.005 SOL a trade at our landing (today one wallet qualifies, `2YDnmMQt…`, at 0.0013).

First fee-test numbers (two paid buys copying `2YDnmMQt…`, 15:52 and 15:58): paying 0.02 priority + 0.02 tip
landed us 0 and 1 trades behind the leader at +0.9 % and +1.1 % above its price, against 2.5 behind and +26 % at the
minimum fees (39 round trips on 5–6 Nov). Both trades still lost (−0.16 SOL together, 0.12 of it fees): some sells
and their failed duplicates went out at the 0.02 buy fee, so the paid arm costs about 0.06 SOL a round trip, 30 % of
a 0.2 SOL fill. The low-fee arm never bought: OKTech seems to buy a coin once per wallet, so the faster paid sniper
takes every shared signal. Final numbers when the test ends at 18:08 (docs/live-recipe.md).

A review of the self-running code before it went live found that `/book off` left the book's snipers buying with
its stops switched off, that one truncated job file could stop every stage silently, and that a running guard
dropped off `remote jobs` after two hours; all fixed with tests (commit `a095854`).

## The loop runs itself now (6 Nov, 10:30 UTC) — what is automatic, what is still yours

Built, tested and deployed today (README 3e): the operator checks what you pay for every 15 minutes and tells you
on Telegram once a day per problem; a morning digest; `/status`, `/stop` (never strands a coin), `/resume`; a live
page at `/dash`; and the first two stages of the learning loop — `discover` (hourly, seven sources into one
candidates table) and `qualify` (every 30 minutes, one lab at a time, a verdict per wallet at the +6 landing).
The three gates from the roadmap are now code, not a checklist. Nothing trades: the book stays off until you fund
it and say so.

What the day's data said:

- The +6 by-wallet lab on 35 h of tape (300 938 moments): still one wallet with n ≥ 20 and a positive lower
  bound — `G2oZrd…` (n 40, +0.017 SOL a trade, 90 % wins, +0.66 SOL over the window). `3JD7zr…` (Solprop) has
  n 54 at +0.006 with a lower bound just above zero. Everything else positive has n < 20.
- The 19 SolanaTracker leaderboard wallets: 18 made **no** pump.fun buy in two days of tape, the one that did
  (`4AQyaA…`, 5 buys) had no followers at all. The paid leaderboards rank people who trade elsewhere; for us the
  tape, the coin-first source (first buyers of runners) and your tracked list are the feeds that matter.
- The daily job was killed at systemd's 6-hour limit this morning: it ran six labs beside my manual lab on a
  4-CPU box with 1.8 GB peak memory and swap. It now runs one lab (+1 landing), the +6 by-wallet lab belongs to
  `qualify`, and every lab takes the same lock. The resize to 16 GB / 6 CPU is still the right move once the Vultr
  balance is topped up.

First runs on the server (6 Nov, 10:38–11:05 UTC): `discover` put 4 338 wallets into the candidates table in
four minutes (tape 299, your tracked list 2 681, SolanaTracker 33, Birdeye 226, coin-first 1 280; Vybe added 344
once its field names were fixed). `qualify` measured the first 300 in 13 minutes: 174 had never traded in the
tape (review in 72 h), 22 retired (bundle members in 2–44 launches, or trading off pump.fun), 277 wait for more
evidence, and **one qualified**: `2YDnmMQt…` — n 93 at +6, +0.0146 SOL a trade, lower bound +0.0019, median 3
followers within a second, no API history (a tape-only wallet). The operator reported it on Telegram at 11:01
with the right caveat (history unverified). That is the loop working end to end without a hand on it.

The measure stage (phase 3) is live too (6 Nov, 11:15 UTC): every buy and sell of the sniper wallet becomes a
`fills` row with its position, the copied wallet, trades behind, price gap, fees and PnL, and failed sends count as
cost. Its first pass re-derived the copy-test table by itself (62 failed duplicate sends in 24 h, mean gap 18 %, p75
position 3.75) — the next live step will be measured the moment it happens, with no one watching.

Later the same day (12:45 UTC): the book runs in dry mode every five minutes (it writes what it would do; it said
"equity below floor" once, correctly, at 3 SOL) and the events stage is live — trending and graduating feeds, exact
curve crossings and dev-payment edges (1 141 in the first scan) feeding the relationship gate.

The OKTech audit (13:30 UTC, `docs/oktech-audit-2026-11-06.md`) checked our integration against the chain and
OKTech's own app and found fifteen things wrong on our side; all are fixed and deployed (`5e24154`+). The ones that
would have cost money or hidden it: every copy sniper still bought through OKTech's seed account (two transactions
and parked rent per fill, and the 6023 race); `/stop` refused every stop because of 0.0006 USDC while the gate said
nothing was held; the measure stage could not have told the fee-test arms apart and credited two copy fills to a bot
instead of the leader; tips to the relays OKTech really uses (not Jito) read as zero. The re-measured baseline over
5–6 Nov: 2.5 buys behind the copied wallet, +26 % price gap, 0.0028 SOL of fees per buy plus 0.0020 in failed sends.
What only OKTech can answer (why every sell goes out two or three times, why one buy in three carries no tip,
compute units, silent acceptance of ungranted features) is a nine-question list at the end of that file.

The strategist (stage 6, 14:30 UTC) is built and deployed too: once a week, on Sundays, an Opus turn reads the week's
numbers and may move one knob of the book or the qualify stage inside the `[autotune]` bounds you set (it can widen
exploration or move the labs' landing further back; it can never switch the book on, touch a wallet or raise a size
above your ceilings). Anything outside the bounds comes to you as a question on Telegram. Every applied change is an
event, so next week's numbers can be read against it. The learning loop as designed is now complete end to end; what
remains is live evidence.

Later (15:30 UTC): `/stop` now acts within a minute (a one-minute Telegram pass beside the 15-minute round); creator
fee claims are decoded from the chain and become events; the labs read the hourly read-only copy of the database when
it is fresh, so research no longer competes with the recorder for the live file. The wallet showed 4.76 SOL at 14:00.

A second review of the day's own fixes (afternoon) found and closed another set of holes, the worst two being that
`oktech push --prune` would have deleted the fee-test snipers and that a coin bought seconds earlier was invisible to
every stop decision (docs/oktech-audit-2026-11-06.md, "Second pass"). With every fill's real fees, the 39 round trips
of 5–6 Nov lost 0.199 SOL, nearly all of it fees: the case for measuring whether paying more for position is worth it.

The fee test started at 15:08 UTC on the owner's go: six capped snipers on three leaders, half at the minimum bid
and half paying 0.02 + 0.02 SOL, watched by the guard until about 18:08 UTC.

Still yours, in order: send
the nine questions to OKTech's owner; run `sudo bash /opt/snipetech/deploy/setup.sh` once and enable the new timers
and the events service (the exact line is in README 3e); put a `DASH_TOKEN` in `/etc/snipetech/env` for the page; top
up Vultr and say "resize now" between jobs; fund the wallet to 6–10 SOL when you want the book on.

## The wallet labs at both landings (6 Nov, 08:00 UTC) — one survivor, and it is small

Same 32 h of tape, 132 wallets, best exit each, 0.2 SOL a trade:

| landing | wallets positive | positive lower bound | top-25 together |
|---|---|---|---|
| +1 (one trade behind, the optimistic case) | 64 | 45 | +16.3 SOL |
| +6 (where the live copies actually landed) | 34 | 11 | +4.6 SOL |

Only **one** wallet has n ≥ 20 and a positive lower bound at both landings: `G2oZrd…` (found by the discovery
pass), n 37, +0.017 SOL per trade at +6 with a 92 % win rate — about 0.6 SOL a day at 0.2 SOL a trade. Every
wallet from the paid KOL lists (Gfree, Pengu, Decu, Cupseyy, Megga…) drops to zero or below at +6, which is what
the live test said. G2oZrd still needs the 30-day gate (Birdeye) and a quiet-landing check before any live step,
and 0.6 SOL a day is a data point, not a business.

## Copy landing measured live (6 Nov, 05:45 UTC) — the known wallets are the +6 regime

> Operator (6 Nov, built): a 15-minute round on the server that calls Claude only on change, with no trading tools;
> notes in `operator/notes.md`, decisions to Telegram, caps 15 $/day. README 3e; spec and plan under docs/superpowers/.

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
- Sells are safe: snipers cap their own buys and keep selling; the guard only stops in an emergency. (Since the
  audit of 22 Nov not even then while a coin is held: it stops its snipers once the wallet holds nothing they bought.)
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
   12 Nov: ladders of take-profit stages, ladders of stop stages and `momentumHold` are in the lab's exit set
   (7 exits; no lab result yet). `autoStop`, `timerExtension` and `stageAcceleration` are still not modelled.
   What the lab assumes about OKTech is listed in oktech-features.md, "Unknown until a tiny live test".
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
