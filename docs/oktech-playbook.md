# The OKTech playbook: every feature, how to apply it, what to test, what to ask (12 Nov 2026)

**What this is.** One reference for the server's agents and the main session: everything OKTech's sniper can do,
whether we use it, whether our simulator knows it, what a live trade has shown, and what to do with it next.
Nothing here was started, sent or changed. Sizes above 0.2 SOL a buy and anything run for profit need the owner's
yes for that step.

**The owner's words this answers (12 Nov).** "There are like so many sell strategies on the tech, so many, go through
them all." "CA post snipes on twitter, or fee claims. literally so much you can do with it." "If it snipes something
super low and the coin only ups it can use some of the features to sell better like hold it until it start going down,
or partially sell but hold some." "Priority fee has to be decent if its a gigga copytrade but tip is the most
important."

**The short version.**

1. We use a small corner of OKTech. Of 72 snipers we ever created, 59 were copy snipers, 6 hunts and 7 plain launch
   snipers. Not one of the 72 carried a take-profit that could fire: every sell ladder we sent was a single stage at
   +10,000 % or a 60 s timer (DB).
2. The exit our copies run (sell after 1 s of silence, at -30 %, after 300 s) is the opposite of what the winning
   bots do (sell 40 % into the buyers behind them within 2 s, the rest in pieces). 85 live round trips so far lost
   1.41 SOL before the failed duplicates' fees, 16 of them won (DB, `fills.pnl_sol`).
3. The owner is right about the tip, and the chain shows why it matters more on OKTech than anywhere: OKTech pays the
   buy's priority fee again on the sell and on each failed duplicate of the sell. A tip is paid once.
4. Five things are worth more than everything else in the list: the bid in the tip, a guard against copying
   stablecoin swaps, selling in parts, a trailing stop on the part we keep, and a market-cap band per copied wallet.
5. OKTech publishes nothing we can read without the key. Every "unknown" below needs one tiny live trade or one
   answer from their support.

## Where each claim comes from

| tag | source |
|---|---|
| F:n | `docs/oktech-features.md`, line n (the web app's bundle, read 5 Nov) |
| S | `docs/oktech-settings.md` (the Sniper API page and the stored config of the account's sniper "PROJECT SNIPE") |
| A | `docs/oktech-audit-2026-11-06.md` |
| L date | `docs/live-recipe.md`, the entry of that date |
| W | `docs/research/2026-11-12-winning-bots.md` (the three bots, read off our tape) |
| G | `docs/research/2026-11-12-gmgn-skills-map.md` |
| code | `src/snipetech/oktech.py` unless another file is named |
| DB | the server's tables `fills` and `oktech_snipers`, read 12 Nov 2026 at 18:05 UTC with read-only SQL |

**OKTech's public pages, tried 12 Nov 18:10 UTC.** `oktech.me/`, `oktech.me/docs` and `oktech.me/updates` open but
hold only the word "oktech" (the app is built in the browser; nothing to read). `oktech.me/api/sniper-api/docs`
answers 401 without the key. `docs.oktech.me` does not exist. Two web searches found no page about OKTech at all.
`snipetech oktech docs` prints OKTech's own API reference, but it needs the key and the network, so it was not run
here: the main session can run it on the server (`snipetech remote --wait run oktech docs`) and check this file
against it.

**What the DB says we have sent, all time (72 snipers, 5 to 12 Nov).** Modes: `ctbot` 59, `hunt` 6, `normal` 7.
Top-level keys ever sent: `mode`, `buyAmount`, `priorityFee`, `tipAmount`, `singleSnipe`, `totalSnipes`, `autoSell`,
`ctbotWallets`, `ctbotMinWallets`, `ctbotTimeWindowMs`, `ctbotCopyExit`, `speedMode`, `extraParams`, `platforms`,
`monitorProgram`, `hunt`, `preexec`, `preconf` (3 snipers), `crowdBuys` (2, ignored), `ctbotRetriggerCooldownMs` (1).
Inside `autoSell`: `stages`, `sellAfterSeconds`, `sellPercent`, `sellTipAmount`, `sellPriorityFeeSol`, `stopLoss` (69),
`deadExit` (60), `trailingStopLoss` (11). Inside a copied wallet: `address`, `label`, `minSol`, and `maxMcapUsd` once.
Never sent: everything else in the tables below.

**What closed our live sells, all time (DB, `fills.exit_reason`).** Dead exit 20, copy exit 19, stop-loss 2, timer 1,
sold by hand 9, not classified 34. No take-profit, no ladder, no trailing stop has ever closed a copy.

---

## 1. Every feature, one line each

Columns: **sent today** = which of our builders writes it (and whether a sniper with it was ever created);
**sim** = whether the lab can score it (yes / partly / no); **live** = what a real trade showed, with its date, or
"unknown" (short for: unknown until a tiny live test); **matters** = how much it could move profit, 0 to 10, with the
reason. The scores are judgment, not measurement.

### 1a. Modes (`mode`)

| feature (key) | what it does | sent today | sim | live | matters |
|---|---|---|---|---|---|
| Normal (`normal`, with `monitorProgram`, `devWallets` or `mints`) | buys launches: all of a launchpad, named creators, or known coins (F:27, F:234) | yes: `dev_sniper`, `strategy_sniper` (launch rules), `replica_from_study`, the speed probe; 7 created | yes (launch rules, a block after the create) | 5 Nov: with `monitorProgram` and no gate it buys every launch, as built (L 5 Nov) | 3: at the block after the create no launch rule pays on our tape (40 rules, L 5 Nov 23:45) |
| MIG (`mig`, `migrationExpirySec`, `devMigrationCheck`) | buys a coin on PumpSwap as it leaves its curve, only if that came within N s of its create (F:228) | builder exists (`strategy_sniper`, kind mig); never created | yes (since 7 Nov, exits of 300 s or less) | never run | 3: on paper it loses: -0.015 SOL a trade on 367 unseen trades (research 11 Nov), -0.022 at 0.5 SOL (round 7) |
| Shapes (`shapes`, `launchShape`, `crowdBuys`) | gate on the shape of the launch transaction: wrapped launches, several signers buying in one transaction (F:46-49, F:337) | no: `--via crowd` is refused | no | 5 Nov: `crowdBuys` sent under `normal` was ignored. Under `shapes` itself: unknown | 4: "several wallets bought 30 SOL in the create" is the crew rule's own shape |
| Hunt (`hunt`, thresholds, `knownBot*`) | buys a new coin once other buyers cross thresholds inside a time window (F:50, F:331) | yes: `strategy_sniper` (hunt, whale, burst rules); 6 created | yes (`lab._hunt`, as scored) | 5 Nov 21:13: gates correctly, fires once per start, counts the creator's buy, reads whole numbers only. `requireKnownBot` with our own wallet list: unknown | 4: every plain hunt loses about 0.007 SOL a trade where we land (L 7 Nov); the crew rule is the one open lead |
| Web (`web`, `web.tracking`) | follows a creator's money to the wallets it funds and snipes their launches (F:54, F:341) | no | partly (our `paid_by_dev` links, not OKTech's tracking) | never run | 4: the only feature that follows creators who change wallet each coin |
| AutoDeploy (`autodeploy`) | launches a coin itself (F:212) | no | no | never run | 0: we do not launch coins |
| CTBot (`ctbot`) | copies wallets' buys (F:58) | yes: `list_copy_sniper` (the book and `copylist`), `copy_sniper`; 59 created | yes (wallet rules, copy exit, companions) | 85 round trips since 5 Nov: lands in the leader's block (20 of 24 since 7 Nov), -1.41 SOL in all | 9: the owner's main case |
| Reverse (`reverse`, `reverseCopy`) | watches one wallet in one known coin: buy when it sells, or sell when it buys (F:221) | no | no | never run | 2: one coin at a time, set up by hand |
| ClaimSniper (`claimsniper`, `claimGithubUsers`, `claimMintAddresses`, `claimWalletClaims`, `claimMinSol`) | buys a coin when its creator fees are claimed (F:224) | no | partly (`fee_claim` events, 110,567 rows) | never run | 4: unknown; needs a paper test first (section 2e) |
| [A] mode (`ansem`, `fundingAge`) | buys only launches whose creator's first-ever funding was a round sum inside a band and a time window (F:207, F:365) | no | partly (table `funders`) | never run; needs account level 9, ours unknown | 3 |
| Gucci (`gucci`) | a list of "gucci entries"; no help text (F:211) | no | no | unknown | 1: nobody knows what it does |
| Stonk (`stonk`) | launches quoted in stock tokens (F:230) | no | no | never run | 1 |
| Loadout (`loadout`) | launches on app.loadout.xyz, buy only, no auto-sell (F:232, F:362) | no | no | never run | 1 |
| CopyDev (`copydev`) | replays another creator's launch as a launch of ours (F:217) | no | no | never run | 0: we do not launch coins |

From the gate table (F:196-199, F:324-329): CTBot, ClaimSniper, Reverse, MIG, CopyDev and Stocks are one switch
("advanced modes"). CTBot works on our account, so the other five should be open too. That is a reading of the gate
table, not a test. Hunt, Shapes and Web are also one switch (the [S] tab), and Hunt has worked since 5 Nov 21:13: so
Shapes and Web are probably open, and "this account does not have shapes" (code, `strategy_sniper`) was never tested
under mode `shapes`.

### 1b. What to buy: targets and filters

| feature (key) | what it does | sent today | sim | live | matters |
|---|---|---|---|---|---|
| Platforms (`platforms`: pumpfun, pumpswap, raydium, dbc, raydiumv4, dammv1, dlmm) | which venues a sniper buys on (F:35, S) | yes on launch, hunt and MIG snipers (`["pumpfun"]`). **Not on copy snipers** | yes (a rule's venue) | on launch snipers it works. On a copy sniper: unknown | 7: the first candidate for keeping a copy out of a USDC swap and off LaunchLab |
| Named creators (`devWallets`, up to 30; `acceptAsCreator`) | buy only launches signed by these wallets (S) | yes: `dev_sniper`; never created | yes (deployer scores, the lab's `creators`) | never run | 3: five serial launchers lost at the launch landing (1,306 trades, round 7) |
| Blocked creators (`blockedCreators`, up to 100) | never buy these creators' coins (S) | no | partly (our block list) | never run | 3: cheap insurance on any launch or hunt sniper |
| Developer Migration Check (`devMigrationCheck {minMigrations, minMigrationRatePct}`) | only creators whose earlier coins left the curve often enough (F:39, F:229) | no | partly (the deployer record) | never run | 3 |
| Dev Funding Gate (`fundingAge`) | see [A] mode | no | partly | never run | 3 |
| Price-action gate per creator (`walletTriggers`: "+X % within N ms", up to 5 stages) | wait for a move before buying a named creator's launch (F:40, S) | yes: `dev_sniper`'s "confirm" rule; never created | yes | never run | 3 |
| Metadata filter (`metadataFilter`: name, symbol, regex, website, twitter, telegram, image) | buy only coins whose name or links match (F:42, S) | `replica_from_study` only; never created | partly (`x_kind`, `x_handle`, the X account's badge and followers) | never run | 4: "links a tweet" is one condition here |
| Max market cap (`maxMcapUsd`) | the buy carries a minimum-out, so a buy above the cap fails on chain and costs a failed send (F:256, A) | once: AG89fn's copy sniper, 8 Nov, $6,700 | yes (`max_mcap`) | sent 8 Nov; that it fails above the cap rather than filling: read from the app, not seen on chain | 7: it is the only price limit a buy has (every buy goes out with "at least 1 token", L 11 Nov) |
| Quote lists and pump filters (`pumpFilters {nonSolQuoteOnly, cashbackOnly, sharingCheck}`, quote whitelist and blacklist, `pumpAutoSwap`) | choose or skip coins quoted in something other than SOL; swap SOL into the quote in the same transaction (F:44, F:242-250) | `replica_from_study` only; never created | partly (quote of the coin) | 5 Nov: PUMP-quoted launches tripped Hunt and the SOL buy failed (error 6010); no filter was on | 3: saves failed sends on hunts |
| Account filter (`accountFilter {whitelist, whitelistMode, blacklist}`) | the launch transaction must, or must not, name these accounts (F:354) | no | no | "not granted" on 5 Nov (L); unknown now | 4: could require a crew's accounts or shut out non-SOL curves |
| Launch shape, wrapped launches, platform payer | see Shapes | no | no | unknown | 1 |
| Crowd buys (`crowdBuys {minSigners, minTotalQuote, platforms, signal, buy}`) | several signers buying one coin in one transaction (F:48) | no (refused) | partly (create-block bundles) | see Shapes | 4 |
| Hunt thresholds (`minTotalSol`, `minUniqueBuyers`, `maxUniqueBuyers`, `maxDevBuySol`, `minCpiBuyers`, `minLutMatches`, `minAccountMatches`, `requireKnownBot`, `timeWindowMs`, `triggerMode`) | the gate of a hunt (F:331) | the first four, `requireKnownBot`, the window and the mode; never `minCpiBuyers`, `minLutMatches`, `minAccountMatches` | yes for the ones sent | 5 Nov: whole numbers work; 2.5, 1.0 and null were ignored and it bought every launch. `maxDevBuySol: 0`: unknown (it may mean "no limit") | 4 |
| Known coins (`mints`, up to 30, `preAta`) | buy a named coin the moment its curve or pool appears (F:234, S) | no | no | never run | 2: for tickers announced ahead |
| Monitor Program (`monitorProgram`) | every launch on a platform (F:234) | yes | yes | 5 Nov | 2: only useful with a gate |
| LaunchLab and DBC rules (`launchlabRules`, `dbcRules`, configs, protection) | Raydium LaunchLab and Meteora launches with rules on fees, vesting, supply (F:35, S) | no: a rule scored on LaunchLab is refused (`venue_refusal`) | partly (LaunchLab launches are on the tape) | never run | 4: Fhbh1D makes 53 % of its buys on LaunchLab (W) |
| Liquidity filter, vanity filter, once per config, Match Cashback Status, creator rewrite check | pool size limits, mint-name patterns, one launch per config, cashback coins, a creator moved right after create (F:234-253, S) | no | no | never run | 1 each |
| Signal only (`signalOnly`, `relaySignals`) | emit the signal instead of buying (F:340, S) | no | not needed | unknown; documented for Shapes only | 5 as a tool: a trigger could be checked live for nothing |

### 1c. Copy mode (`ctbot`) fields

| feature (key) | what it does | sent today | sim | live | matters |
|---|---|---|---|---|---|
| Wallet list (`ctbotWallets[]`) | the wallets one sniper copies; a list, so one sniper can hold many (F:58, S) | yes, always exactly one wallet a sniper | yes | works | 8 |
| Smallest buy to copy (`minSol` per wallet) | skip the wallet's buys under this size (S) | yes (the book: 0.3 SOL or the wallet's own floor) | yes | sent on all 59; arm A, with a 1 SOL floor, copied a swap of Cupsey's into USDC: the floor looks at the SOL spent, not at what is bought (L 12 Nov) | 7 |
| Largest buy to copy (`maxSol` per wallet) | skip the wallet's buys over this size (S) | no | partly | unknown | 4: two thirds of the buys 6Z91 follows are 1 to 3 SOL (W) |
| Market-cap floor (`minMcapUsd` per wallet) | skip buys in coins under this market cap (S) | no | yes (`min_mcap`, since 12 Nov) | unknown | 6: the owner asked for it; also the cheapest test of a guard |
| Market-cap ceiling (`maxMcapUsd` per wallet) | see 1b; on a copy it fails the buy on chain, it does not skip it (A) | once | yes | sent once | 7: at a stablecoin's market cap the copy would fail for a failed send's fee instead of buying |
| Buy multiplier (key unknown; F:58) | size our copy as a multiple of the leader's buy | no | no (a rule has one size) | unknown | 5: the winners size per wallet and per buy (W) |
| Consensus (`ctbotMinWallets` within `ctbotTimeWindowMs`) | buy only when N of the listed wallets bought the coin inside the window (S) | sent as 1 wallet, 10,000 ms | partly (the "2nd known wallet in" rule) | never above 1 | 6: the analyst's best paper lead today is a second-wallet-in rule (docs/autopilot.md, 12 Nov) |
| Re-buy cooldown (`ctbotRetriggerCooldownMs`) | wait before copying the same trigger again | the speed probe only (15,000 ms) | no | unknown | 4: a leader's second buy of a coin is copied again and lost twice on 8 Nov (Gwje9b, AJNiDe) |
| Copy exit (`ctbotCopyExit {sellThresholdPercent, sellPercent, triggerWalletOnly, delayMs}`) | sell when the copied wallet sells (F:59) | yes: 50 %, sell 100 %, at once | yes (`copy_exit_pct`) | 19 live sells; when the leader dumps everything our sell lands behind it (AG89fn, three exits at -55 to -85 %, 8 Nov) | 7 |
| Buy-on-Sell and Sell-on-Buy (reversal) | do the opposite of the wallet (F:59) | no | no | never run | 1 |
| Migration follow (`ctbotMigrationFollow`) | when the leader's buy completes the curve, buy the pool (F:60) | no | no | never run | 2 |
| Groups, skip check | wallet groups; a check that can be skipped. No help text (S, F:60) | no | no | unknown | 1 |
| Jetstream (`speedMode`) | the faster stream for copies (S) | yes | not needed | on for every copy; never compared | 5 |
| Copies an attempt | the copy fires on the leader's transaction even when that then fails (S) | not a setting | no | measured (S) | a risk to know, larger with preconfirmations |
| Once per coin and wallet | a coin is bought once while held; two snipers copying one wallet do not both buy (L 6 Nov) | not a setting | yes | 6 Nov | a paired fee test needs a second wallet |

### 1d. Execution: fees, speed, limits inside a sniper

| feature (key) | what it does | sent today | sim | live | matters |
|---|---|---|---|---|---|
| Buy priority fee (`priorityFee`, 0.001 at least) | the fee the block orders by, per unit of compute | yes; 0.001 on 68 of 72 snipers | partly (`--per-tx`, `--buy-bid`) | **charged again on the sell and on each failed duplicate of it** (below) | 9 |
| Buy tip (`tipAmount`, 0.002 at least) | paid to a relay (Nozomi, Astralane, Helius Sender, FLaSH) that hands the transaction to the block's leader; returned when the send fails (A) | yes; 0.002 on 68, 0.02 on 3, 0.03 on 1 | partly (`--buy-bid`) | 12 Nov: 0.005 + 0.03 landed first behind Cupsey 2 times of 2 (L). 6 Nov: 0.02 + 0.02 landed no earlier behind quiet wallets (L) | 9 |
| Sell tip (`autoSell.sellTipAmount`) | the tip on the relay-sent copy of a sell | yes, 0.002 on copies | partly | 81 of 85 landed sells were tipped (DB) | 5 |
| Sell priority (`autoSell.sellPriorityFeeSol`) | meant as the priority of the plain copy of a sell | yes, 0.0001 | no | since 8 Nov one send of 47 carried it; the others paid the buy's priority, or an older sniper's 0.002 (below) | 3 as it stands; 8 if OKTech can make it work |
| Buy cap (`singleSnipe` + `totalSnipes`) | stop buying after N buys; failed buys count (F:67) | yes, always | yes | works, with two faults: the buy that uses up the cap can be left with no exit (4siK, DTAU, 5m5h: L 6 Nov), and arm A bought 4 times on a cap of 3 (DB, 12 Nov) | 6 |
| Send count (`extraParams.globalSendCount`) | how many times each transaction is sent (F:70) | yes, 1 | no | it does not govern the duplicate sells (A) | 2 |
| Smart fees (`extraParams.globalSmartFees`) | "adjusts fees automatically"; nothing more is written (S) | no | no | unknown | 5: it may be the switch that prices sells apart from buys; ask |
| Compute units (`extraParams.computeUnits`) | the limit a buy asks for; the same fee over a smaller limit ranks higher | plumbed (`execution_params`), never set | no | buys use 51,000 to 79,000 of the 150,000 default (A; arm A's PumpSwap buys 56,000 to 72,000, DB). Whether a copy honours the field: unknown | 5: up to 1.5 times the rank for nothing |
| Seed account (`extraParams.seedAccount`) | buy through a throwaway account and sweep after (F:74) | yes, off | not needed | 6 Nov: off removed the failed sweeps and the 5 Nov stop-loss race (A) | done |
| Local pre-execution (`preexec`), two waves | runs the create and our buy locally first, sizes the compute limit from it, does not send a buy that fails there; SOL-quoted pump.fun launches only (F:348) | yes on strategy snipers (also on MIG, where it cannot apply) | not needed | 5 Nov: behaviour the same with and without (L) | 3 |
| Preconfirmations (`preconf`) | sees a watched wallet's transaction before it is confirmed (F:357) | yes, on the 3 newest copy snipers | no | 12 Nov: 2 placed copies, one in Cupsey's own block, one a block later (L). Not compared with "off" | 7 |
| Pre-create token account (`preAta`) | makes the coin's account at start, so the buy is smaller (F:258) | no | no | unknown | 2: only for coins known ahead |
| Extra wallets (`additionalSnipers`, up to 9, "Inherit primary auto-sell") | more wallets buy alongside, each with its own size and exit (F:262) | no | no | never run | 5 as a tool: two wallets make a paired fee test possible (L 6 Nov) |
| Cooldown between launches, max launches, transactions per wallet | pacing of a launch sniper (F:67) | no | no | never run | 2 |
| Trade and wire versions, Jito memo, lookup tables, data-size limit | low-level transaction settings (F:70-73) | no (defaults) | no | defaults in use | 1 |
| Notify on complete, relay to Discord | a message when a sniper ends (F:269) | no | not needed | never run | 2: operations only |
| Auto-Transfer, share codes, Jupiter key | move the coins on after the sale; import a saved sniper (F:264-272) | no | no | never run | 0 |

**What a sell really costs in priority fees (DB, every send of a sell since 8 Nov 13:00 UTC, when
`sellPriorityFeeSol` went to 0.0001).**

- 19 sells landed. 18 paid their sniper's **buy** priority: 15 at 0.001, 3 at arm A's 0.005 (one of those with no
  tip). One paid 0.0001.
- Beside them 28 duplicate sells failed, 1.47 for each sell that landed. 25 paid their sniper's buy priority (22 at
  0.001, 3 at arm A's 0.005). The other 3 came from snipers made before the change, whose sell priority was still
  0.002 with a buy priority of 0.001: they paid 0.002.
- Not one failed duplicate paid 0.0001. Of 47 sends, one carried it.
- The duplicates cost 0.038 SOL over those 19 sells (`fills.twin_fee_sol`), 0.002 a sell.

Read plainly: a sell priority set under the buy's is not used; the send goes out at the buy's priority. A sell
priority above the buy's (0.002 over 0.001, before 8 Nov) was used. So the entry "the duplicate sells made free"
(L 8 Nov) did not come true, and "`sellPriorityFeeSol` prices the untipped copy" (F:395) held for one send in 47.
Whether OKTech has a floor (its smallest priority fee is 0.001) or takes the larger of the two numbers is question 3.

So one round trip with S sales, a buy priority P, a buy tip T and a sell tip of 0.002 costs in fees about
`T + P x (1 + 2.47 x S) + 0.002 x S` (the buy, then each sale's landed send and its 1.47 failed ones):

| the bid on the buy | one sale | five sales (a ladder) |
|---|---|---|
| priority 0.001, tip 0.03 | 0.035 SOL | 0.053 SOL |
| priority 0.005, tip 0.03 (arm A) | 0.049 | 0.107 |
| priority 0.03, tip at its 0.002 minimum (6Z91 bids in the priority, W) | 0.108 | 0.413 |

The same bid costs three to eight times as much in the priority as in the tip. That is the owner's point, with the
number. It holds for OKTech's way of selling; a bot that sells through its own sender (6Z91: 0.0001 SOL a sell, W)
does not pay it.

### 1e. Auto-sell (`autoSell`): every way OKTech can sell

| feature (key) | what it does | sent today | sim | live | matters |
|---|---|---|---|---|---|
| Sell-all timer (`sellAfterSeconds` + `sellPercent`; 0 % keeps the position) | sell X % after N s (F:82) | yes, 300 s on copies | yes | 5 Nov: sold at exactly 300 s (L). One copy closed by it (DB) | 5: the backstop |
| Gain stages (`stages[{gainPercent, sellPercent}]`, 1 to 10, "100 % must be last") | sell a share at each gain (F:83) | builder: yes (`autosell`, from `take_profit` or `tp_stages`). Sent for real: never (72 of 72 snipers had only the +10,000 % stage or a timer stage) | yes (`tp_stages`, since 12 Nov) | **unknown**: no take-profit of ours ever fired | 8: selling a first part into the buyers behind us is what the winners do |
| What `sellPercent` means in a stage | a share of the position as bought, or of what is left | sent as "of the position as bought", the last stage as 100 | the lab assumes "as bought" | unknown (F:155) | decides whether a live ladder sells what the lab scored |
| Stage bail timer (`sellAfterMs` on a stage) | the stage also fires after N ms (F:83, S) | one timed stage of 100 % when the exit is a bare timer of 60 s or less (3 snipers) | no (only the sell-all timer and equal parts) | 6 Nov: a 60 s exit sold at 61.2 s, but the sell-all timer was 60 s too, so which one fired is not known | 8: timed part sales are the 6Z91 exit |
| Stage delays (`processDelayMs`, `delaySellAllSeconds`) | wait before a stage's sale goes out (F:83, S) | `delaySellAllSeconds: 0` always | no | unknown | 1 |
| Stop-loss ladder (`stopLoss.stages[{percentLoss, sellPercent}]`) | sell shares at falls, e.g. 40 % at -25 %, the rest at -50 % (F:85) | yes: one stage at -30 %, 100 %; ladders through `sl_stages` | yes (`sl_stages`) | 2 copies closed by the stop (DB). 5 Nov: a 15 % stop filled at -57 % and -70 % on dumps (code, `EXIT_MAX_STOP_LOSS`) | 6 |
| Stop confirm window (`stopLoss.confirmMs`) | the fall must last this long (F:86) | yes, 250 ms on every stop | **no**: the lab's stop fires at once | not isolated | 3: a gap between what is scored and what runs |
| Trailing stop (`trailingStopLoss {percentFromPeak, activateAfterGainPercent, sellPercent, confirmMs}`) | sell on a drop from the high, once a gain armed it (F:87) | yes when the exit has one (11 snipers on 5 Nov; the menu's `ride`) | yes | 5 Nov: launch snipers with a 10 % trail, a -15 % stop and a 300 s timer sold at 9 s, about 100 s and 300 s (L); the trail matched our replay on 8 of 11 of their sells (`exits_menu.py`); it did not fire on CAT's 26 % drop (L). Never on a copy | 8: this is "hold it until it starts going down" |
| Dead exit (`deadExit {staleSeconds}`, whole seconds, 1 at least) | sell when nothing traded for N s (F:88) | yes: 1 s on copies, 60 s on strategy snipers | yes | 20 live sells (DB); 5 Nov a 60 s one worked (GTA) | 5: it caps losers at -0.02 to -0.04 on quiet coins (L 8 Nov) and cuts winners at 1 s |
| Auto-stop (`autoStop {percentGain, withinMs}`) | on a fast pump the exits switch off and the coin is left to run (F:89, S) | never: `autosell` refuses it | no | never run | 2: a moonbag nobody then sells; only with our own seller behind it |
| Migration sell (`migrationSell {sellPercent, delayMs}`, 100 ms to 1 min) | sell a share when the coin moves to its pool (F:90) | builder: yes, as scored; never created | yes | never seen firing | 3 |
| Momentum hold (`momentumHold {minVelocity, lookbackMs, maxHoldMs, trailingDropPercent}`) | a sale that is due waits while the price still climbs fast; released when it slows, falls from its high, or the longest wait is over (F:91) | no: `autosell` and `validate_exit` refuse it | yes, on our guess of its clock (`momentum_hold`) | unknown: clock or per trade, price or market cap, which sales it holds (F:171) | 7: "if the coin only ups, do not sell yet" |
| Timer extension (`timerExtension {minGainVelocity, lookbackMs, extensionMs, maxExtensionMs, cancelDropPercent}`) | a stage's timer is pushed back while the price climbs, up to a cap; a drop snaps it back (F:93) | no | no | unknown | 7: "sell all at 2 s unless it is running" is the closest OKTech gets to 6Z91's rule |
| Stage acceleration (`stageAcceleration {minStages, delayMs, releaseOnDropPercent}`) | when one trade trips several stages, the lowest sells now and the rest are spread out; a drop fires them all (F:95) | no | no (the lab sells every tripped stage on that trade) | unknown | 4: fewer sales into one spike |
| Wick mode (`wickServer {walletId, wickGainStagesOnly}`) | a second wallet buys just before our sale, so the sale meets a higher price (F:97) | no | no | never run | 1: it buys our own sale; a cost and a risk, needs a second wallet |
| Saved strategies, auto-sell override per sniper | a named sell setup loadable into any task (F:99) | no: every payload carries its own `autoSell` | not needed | never run | 2: for the owner's hand-made snipers |
| Copy exit | see 1c | yes | yes | 19 sells | 7 |
| Selling out in equal parts (`sell_parts`, ours, not OKTech's) | the lab's research exit: the last sale in N parts | never (refused) | yes | none | the lab's stand-in for timed stages |

Three faults that belong to every exit, all seen live:

- **OKTech starts watching a coin about 1.3 s after the buy lands** (L 7 Nov). Nothing sells before that. The winners'
  best group sells its first part within 0.6 s (788 of 6Z91's trades, +0.27 SOL a trade, W): out of reach.
- **The buy that uses up a sniper's cap can be left with no exit at all** (three coins, 5 and 6 Nov). The book works
  around it by relying on a sniper's first buy only.
- **Exits fire for coins never bought** (three coins on 8 Nov, two failed sells each, L).

### 1f. Outside the sniper: Twitter tasks, Signals, Social, the rest

| feature | what it does | used today | sim | live | matters |
|---|---|---|---|---|---|
| Twitter task: post by users | a named account posts; the task pulls the contract address out of the text and buys it (F:280-284) | no | no | never run | 6: unknown; the owner's "CA post snipes" (section 2d) |
| Twitter task: reply, quote, retweet | the same on replies (to named users or posts), quotes and retweets, with keywords | no | no | never run | 4 |
| Twitter task: follow, profile or bio change, community membership | fires when an account follows someone, changes its bio (an address in the bio), or joins a community (F:281) | no | no | never run | 3 |
| Extract CA, keyword to CA, extra CAs, ignore mentions | how the address is found in the text; several coins on one match (F:283) | no | no | never run | part of the above |
| Market-cap range ($1,000 to $1 billion) | checked by OKTech's worker at the moment it fires (F:285) | no | partly | never run | part of the above |
| Max tweet age, handle match strictness, auto-stop ceiling (5 s to 24 h) | a tweet older than N s is skipped; how exactly the handle must match (F:62) | no | partly (`tweet_ts` of every coin that links a tweet) | never run | part of the above |
| "Enable up to 3 of your sniper tasks" on a match | the tweet starts snipers instead of buying (F:293) | no | no | never run | 5: a tweet could start a sniper with our own exit |
| Bind to trigger tweet (`metadataFilter.tweetBinding`, `maxTweetAgeSec`) | a sniper buys only launches whose metadata names that exact tweet (F:267, S) | no | partly (`tweet_id` in our metadata) | never run | 5: the "coin about a big account's tweet" case |
| Schedule, reset after each run, test against a pasted tweet, webhook key, groups | task housekeeping (F:286-288) | no | not needed | never run | 1 |
| Tweet sources: TweetCatcher, X API with a TwitterAPI.io key, the "1322" Standard and Ultimate streams | where the tweets come from; the 1322 streams have limited slots, per handle, for approved users (F:294) | no | not needed | never run | decides the delay, which is the trade |
| Other chains (Base, ETH, BNB, Arc, Robinhood), Bankr fee-claim trigger | the same tasks elsewhere (F:288-292) | no | no | never run | 0: out of scope |
| Signals (wallet activity, token launch, liquidity change, program instruction, large and whale transfers; Discord, Telegram, webhook, or fire a sniper) | on-chain alerts that can start a sniper (F:17, F:315) | no | partly (our `events`) | never run; per-account page, ours unknown | 4: "start a sniper when wallet X moves" without a round of ours in between |
| Social (tweet feed, followed handles, FOMO traders and coins, news) | feeds to look at; shows how long a tweet took to arrive (F:16, F:311) | no | no | per-account page, ours unknown | 2: a source of handles and of the delay number |
| Catch, Vamp, Mayhem, Stocks, Launch | a launch feed; copy-launch a coin; run a launch campaign with helper wallets; stock launches; Meteora launches (F:13-20) | no | no | never run | 0: we do not launch coins |
| Sniper API | what we use: creates snipers idle, starts them with a key that may; a tweet-bound sniper can be created but not started by the key (F:273) | yes | not needed | works; 201 even for settings it then ignores (A) | the pipe |

### 1g. Account limits

| limit | what is written | source |
|---|---|---|
| Snipers running at once | 20 by default. The 6 Nov audit records it raised to 40 running and 200 tasks. Our config still plans for 20 (`[oktech] max_snipers`). `oktech status` is the check | F:78, A |
| Creates | 20 a minute a key; `oktech.apply` waits 61 s after every 18 | A, code |
| Stages a sniper | 1 to 10 | S |
| Named creators, blocked creators, known coins, account-filter keys, extra wallets | 30, 100, 30, 32 each list, 9 | S, F:354 |
| Buy size | 0.001 to 100 SOL | S |
| Wallets in one copy sniper | unknown | ask (section 4) |
| What the account is approved for | Hunt and pre-execution (5 Nov), preconfirmations ("approved", code). Account filter: not granted on 5 Nov. Shapes, Web, Signals, Social, [A] mode: unknown. `GET /status` does not say | L 5 Nov, A |

---

## 2. How to apply them, by kind of trade

Every setting below is a starting value to test, not a result. "Cannot be sent today" means our builder
(`oktech.autosell`) does not write it yet; that is a small build, not a limit of OKTech.

### (a) Copying a heavily copied wallet at 2 to 10 SOL (the owner's main case)

**What the tape says the trade is (W).** The first place behind the wallet is worth about 10 % of the buy, the second
about 5 %. A bid of 0.015 to 0.04 SOL is first in half the blocks, 0.3 SOL and more in three of four. Behind Cupsey
and Decu the winners bid 0.2 to 0.35 SOL on buys of 4 to 10 SOL. At 0.2 SOL a buy the bid costs more than the place
makes; at 2 SOL the step from second to first is worth 0.1 SOL.

**The settings.**

| field | value | why |
|---|---|---|
| `mode` | `ctbot` | |
| `ctbotWallets[].address` | the wallet itself, never a bot that copies it | the owner: "we cant copy a wallet that is copying because we are filled above" |
| `ctbotWallets[].minSol` | 1 | two thirds of the buys the winners follow are 1 to 3 SOL (W); below 1 SOL the wallet is testing |
| `ctbotWallets[].maxSol` | leave open until a lab says otherwise | never sent; unknown |
| `ctbotWallets[].minMcapUsd` | the low end of the band where that wallet's copies paid on paper | never sent; unknown whether it skips or fails on chain |
| `ctbotWallets[].maxMcapUsd` | about twice the market cap the wallet usually buys at; never above a few million dollars | the only price limit the buy has; also makes a stablecoin swap fail for a failed send's fee instead of buying |
| `platforms` | `["pumpfun", "pumpswap"]` | never sent on a copy; unknown whether a copy honours it (test T4) |
| `buyAmount` | 2, up to 10 behind the few wallets where both winners pay up | the owner's yes for each size |
| buy multiplier | not used until OKTech names the key | unknown |
| `speedMode` | `true` | as today |
| `preconf` | `true` | the owner's yes of 11 Nov; 2 of 2 placed copies landed first with it (L 12 Nov) |
| `priorityFee` | 0.001, the minimum | paid again on every sell and every failed duplicate (1d) |
| `tipAmount` | the whole bid: 0.03 to 0.06 behind ordinary wallets, 0.2 to 0.35 behind Cupsey, Decu, Gake's side wallet, Slingor, cap | W; paid once, returned on a failed send |
| `extraParams.computeUnits` | 100,000 | buys use at most 79,000; a free half more rank if a copy honours it (test T2) |
| `extraParams.seedAccount`, `globalSendCount` | `false`, 1 | as today |
| `ctbotRetriggerCooldownMs` | 60,000 | the leader's second buy of a coin is a worse trade (8 Nov); unknown whether the cooldown is per coin |
| `singleSnipe`, `totalSnipes` | `true`, and a cap the stop rule allows | the cap is the only limit that works inside OKTech |

**The bid split, in plain words.** The owner's rule has two halves. "Tip is the most important": yes, on OKTech more
than anywhere, because of the table in 1d. "Priority fee has to be decent if it is a gigga copytrade": the tape agrees
that the winners bid in the priority (6Z91 sends no tip on 87 % of its buys, W), and the chain orders a block by
priority per unit of compute. What nobody has measured is whether a tip alone buys the same place through OKTech's
relays. Arm A (0.005 + 0.03) was first twice. So: start with the priority at the minimum and the bid in the tip
(test T5). If that lands behind buyers who bid less in all but more in priority, raise the priority, and then keep the
exit to one or two sales, because every sale multiplies it.

**One sniper with many wallets, or one a wallet?** Both, for different wallets.

- **One sniper a wallet** for the handful where the bid and the size are their own (Cupsey's two wallets, Decu, Gake's
  side wallet, Slingor, cap). The winners bid per wallet there, ten times what they bid elsewhere. Size, fees, exit
  and cap are one setting a sniper, so a wallet with its own bid needs its own sniper. Six such snipers use six of the
  20 (or 40) places.
- **One sniper with many wallets** for the long tail at one common bid of about 0.03 SOL. 6Z91 follows 490 wallets and
  has no short list (W). `minSol` and the market-cap band stay per wallet. What is shared: the size, the fees, the
  exit, the cap. It is also the only way to run "buy when two of these wallets are in" (`ctbotMinWallets: 2`).
  Not known: how many wallets one sniper takes, and whether the cap counts per sniper or per wallet.
- Either way a fill is tied to its leader from the chain (`snipetech landing`), not from OKTech, so nothing is lost
  for the record.
- The wallet needs both kinds of SOL at this size: a curve buy is paid whole in plain SOL, a PumpSwap buy in wrapped
  SOL (L 11 Nov). The rule that keeps plain SOL for two buys is sized for 0.2 SOL (`[book] keep_curve_buys`).

**The exit: the bots' recipe in OKTech's stages.** The recipe (W): 40 % at 2 s, pieces every 3 s, everything out by
20 s, everything at 2 s when the price has not moved.

As far as OKTech's fields allow, in timed stages:

```json
"autoSell": {
  "enabled": true,
  "stages": [
    {"sellAfterMs": 2000,  "sellPercent": 40},
    {"sellAfterMs": 5000,  "sellPercent": 20},
    {"sellAfterMs": 8000,  "sellPercent": 15},
    {"sellAfterMs": 11000, "sellPercent": 15},
    {"sellAfterMs": 14000, "sellPercent": 100}
  ],
  "sellAfterSeconds": 20, "sellPercent": 100,
  "stopLoss": {"enabled": true, "stages": [{"percentLoss": 20, "sellPercent": 100}], "confirmMs": 0},
  "sellTipAmount": 0.002, "sellPriorityFeeSol": 0.0001
}
```

plus the copy exit as it is (`ctbotCopyExit`, 50 %), as a backstop.

- The shares are written as shares of the position as bought. If OKTech reads them as shares of what is left, the
  same exit is 40, 33, 38, 60, 100 (test T3 settles which).
- Five sales cost about 0.022 SOL at the minimum priority (0.0045 a sale: priority, tip and 1.47 failed duplicates).
  On 2 SOL that is 1.1 % of the buy. On 0.2 SOL it is 11 %: selling in parts is for size only.
- **Cannot be sent today:** `autosell` writes a timed stage only for a bare timer of 60 s or less, one stage, 100 %.
- **Not known:** whether a timed stage may sell less than 100 %, whether several timed stages run, whether a stage
  without `gainPercent` must carry the key as null (OKTech ignored nulls in Hunt on 5 Nov).

"Everything at 2 s if the price has not moved" has no field. The two nearest things:

- **A stage with a gain and a timer** (`gainPercent: 8`, `sellAfterMs: 2000`, `sellPercent: 40`): the app says a stage
  has "gain % and/or bail timer" (F:83). Read as "at +8 % or after 2 s, whichever comes first", it sells the first 40 %
  into strength when buyers pile in and on the clock when they do not. That reading is a guess.
- **Timer extension**: one stage "sell 100 % after 2 s" whose timer is pushed back while the price climbs, to 20 s at
  most, and snaps back on a drop. That is "drop it at 2 s unless it is running". Never sent, not in the simulator,
  its clock unknown.

**What cannot be expressed at all.** A sale in the buy's own block or within 0.6 s (OKTech watches from about 1.3 s).
"If flat sell all, otherwise sell 40 %" as one rule. Pieces sized by what the buyers behind us are buying. A sale
priced apart from the buy (every send of a sell pays the buy's priority).

**What the simulator can score of it.** A ladder by gain (`tp_stages`), a stop ladder, a trailing stop, the copy exit,
the last sale in equal parts (`--sell-parts`, `--sell-part-secs`), a bid on the buy (`--buy-bid`). Not a ladder on a
clock with unequal parts: the 6Z91 recipe itself is "partly". The owner's bar stands: a forward paper test of the same
rule, profitable, before any copy above 0.2 SOL.

### (b) A low-cap launch snipe where "the coin only ups"

The owner: "hold it until it start going down, or partially sell but hold some." OKTech has a field for each half.

| what the owner said | field | starting value | state |
|---|---|---|---|
| take the cost out early | `stages[0] = {gainPercent: 50, sellPercent: 30}`, `stages[1] = {gainPercent: 150, sellPercent: 30}` | 30 % at +50 %, 30 % at +150 % | sendable today (`tp_stages`); never fired live; test T3 |
| "hold some" | a last stage `{gainPercent: 10000, sellPercent: 100}` | the rest, 40 %, stays behind a gain it never reaches | sendable today (`UNREACHABLE_STAGE`) |
| "until it starts going down" | `trailingStopLoss {percentFromPeak: 25, activateAfterGainPercent: 20, sellPercent: 100, confirmMs: 0}` | sell the rest on a 25 % drop from its high, once up 20 % | sendable today (the menu's `ride`); fired on launch snipers on 5 Nov, missed once (CAT) |
| do not sell a stage while it is still running | `momentumHold {minVelocity: 2, lookbackMs: 3000, maxHoldMs: 30000, trailingDropPercent: 10}` | wait while it climbs 2 % a second over 3 s, 30 s at most, or until 10 % under its high | refused today; in the lab as `MOM2%/s@3s<30s-10%`; test T8 |
| let a timer run on while it climbs | `timerExtension {minGainVelocity, lookbackMs, extensionMs, maxExtensionMs, cancelDropPercent}` | 2 % a second over 3 s, 10 s a time, 120 s at most, cancel on a 10 % drop | never sent, not in the lab |
| do not dump three stages into one spike | `stageAcceleration {minStages: 2, delayMs: 500, releaseOnDropPercent: 10}` | spread them half a second apart; a 10 % drop sells them all | never sent, not in the lab |
| the floor | `stopLoss {stages: [{percentLoss: 30, sellPercent: 100}], confirmMs: 250}` | -30 % | as today; never wider than 35 % (`validate_exit`) |
| a dead coin | `deadExit {staleSeconds: 60, sellPercent: 100}` | 60 s of silence | as today on strategy snipers |
| the end | `sellAfterSeconds: 600`, `sellPercent: 100` | 10 minutes | the longest hold the lab scores (`EXIT_MAX_HOLD_SECS`) |

- What the one live replay says: the `ride` exit made +0.155 SOL on DTAU instead of -0.022, and was 0.12 SOL worse
  than the plain exit on the other 12 fills (`exits_menu.py`, 6 Nov). Riding pays on the runner and costs on the
  rest. A ladder that takes the cost out first is how to pay for the ride.
- "Hold longer than 10 minutes" is refused on purpose. `autoStop` would do it (the exits switch off on a fast pump),
  and then nothing sells the coin: never, unless our own seller watches it.
- Not known: where the trailing stop measures from after a stage sold part (the high since the buy, or since that
  sale: F:168); whether one trade that trips two stages is one sale or two.
- The landing does not change: a launch snipe of ours lands a block after the create, behind that block's first
  buys. No exit turns a losing entry into a winning one; this exit is for the entries a lab says pay.

### (c) A migration snipe

| field | value | why |
|---|---|---|
| `mode` | `mig` | buys on PumpSwap once the coin has moved (F:228) |
| `migrationExpirySec` | 10 (OKTech's default; whole seconds) | only coins that moved within N s of their create. How to send "no window" is not known, so an any-time rule is refused (code). Since 13 Nov the lab scores a buy after a migration of any age as research only (`{"kind": "mig", "window": 0}`: never frozen, never sent) and a window up to a week as that window. No largest value is written anywhere and no MIG sniper was ever created, so a window above 60 s is marked "not confirmed by OKTech" |
| `devMigrationCheck` | `{minMigrations: 2, minMigrationRatePct: 20}` | only creators whose coins move often; never sent |
| `blockedCreators` | our block list | never sent |
| `metadataFilter` | as for launches | it applies to MIG too (S) |
| exit | 300 s at most | the lab scores MIG only on exits of 300 s or less (`MIG_MAX_HOLD_SECS`) |
| paid with | wrapped SOL for the buy, plain SOL for the fees | L 11 Nov |

- On paper it loses where it lands: a buy behind the first three PumpSwap trades after a migration within 60 s of the
  create made -0.015 SOL a trade over 367 unseen trades (research 11 Nov). 1,571 of 1,738 such coins were bought out
  inside their own create by the launcher's set of wallets, which then sells into the pool's buyers. No reason to run
  it for profit.
- Never run live. One 0.05 SOL buy would show where it lands and whether its exits fire (test T9).
- Three more migration features belong to other trades: `migrationSell` (a curve position sells into the move),
  `ctbotMigrationFollow` (follow a leader whose buy completes the curve), Web's `autoMigrationSnipe`. None has fired
  for us.

### (d) A Twitter CA-post snipe

**Two different things carry this name.**

1. **A known account posts a contract address, and people buy that coin.** OKTech: a Twitter task, condition "post by
   users", "Extract CA from text" on, a market-cap range, a max tweet age. The task buys the address itself ("slippage
   pinned to 100 % for SOL: we rely on tip and priority", F:288), or it starts up to 3 of our snipers.
2. **A big account tweets, and someone launches a coin about that tweet.** OKTech: a sniper bound to the trigger tweet
   (`metadataFilter.tweetBinding`, `maxTweetAgeSec`), started by a Twitter task. It buys only a launch whose metadata
   names that exact tweet.

**Settings for the first.**

| setting | value to start from | note |
|---|---|---|
| task type | post by users (later: reply or quote by the same users, bio change) | F:280 |
| handles | 10 to 30 accounts whose calls moved coins on our tape; from GMGN's call-out record and the owner's own list | to be found by the paper test, not guessed |
| handle match | verbatim link | the strictest of the three (F:62) |
| extract CA | on; ignore @mentions on | F:283 |
| market-cap range | $20,000 to $2,000,000 | a call on a coin already at $50 million is a different trade; the range is OKTech's own filter ($1,000 to $1 billion) |
| max tweet age | 5 s | older than that, the copy bots are in already |
| buy | 0.2 SOL at most, tip as in (a) | the standing yes |
| exit | the task's own, or better: let the task start a sniper of ours with the exit of (b) | whether a task buy can carry our `autoSell` is not known |
| tweet source | the X API with a TwitterAPI.io key, pasted by the owner into OKTech's Settings | F:22, F:294. TweetCatcher and the 1322 streams are the other sources; 1322 has limited slots for approved users |

**The key.** OKTech needs its own TwitterAPI.io key in its Settings page. That is a different place from the
`TWITTERAPI_KEY` our server reads (`xaccounts.py`); the same key can be pasted in both. Only the owner can do the
first: Twitter tasks live in the web app, and the Sniper API key cannot start a tweet-bound sniper (F:276).

**What we need on our side to paper-test it first.**

- **The posts.** Who posted which address when. Two sources: GMGN's call-outs (`callout global --only-kol` and
  `callout record`: the caller's wallet and X handle, the coin, the price and market cap at the call, the time in
  milliseconds; 30 days back; G) and TwitterAPI.io's search of a handle's tweets (pay as you go; needs
  `TWITTERAPI_KEY` on the server, asked of the owner on 12 Nov).
- **The price after the post.** Our tape, for coins on their curve or just off it. For older, bigger coins the tape
  has nothing: GMGN's one-second candles or the Helius backfill.
- **The delay.** From the post to OKTech's buy: unknown. The Social page shows "how long a tweet took to reach us"
  (F:313). Until measured, score the entry at 1, 3 and 10 s after the post and see where the edge ends.
- **Already there for the second kind:** every coin's metadata is parsed for a linked tweet, its author and the
  tweet's own time (`meta.parse_socials`: `x_kind`, `x_handle`, `tweet_id`, `tweet_ts`). "Launches that link a tweet of
  account H younger than N seconds" can be scored on the existing tape today.
- GMGN's feeds are polled and take 0.5 to 0.8 s a call (G): good for finding handles and for the paper test, too slow
  to be the live trigger. The live trigger is OKTech's own.

### (e) Fee-claim snipes (ClaimSniper)

**What triggers it (F:224).** A creator-fee claim on pump.fun. It can be narrowed: by the GitHub user the fees are
shared with, by coin, by a list of claiming wallets (the signer is checked), or "Snipe Everything". "First Claim Only"
fires the first time that social user ever claims SOL. A minimum SOL a claim.

**Why it might pay.** A first claim by the person a coin's fees were assigned to says that person is real and has
noticed the coin. Nobody here has measured that. It is a story until a paper test.

**What our events table already holds (DB).** 110,567 `fee_claim` rows from 5 to 12 Nov; on 12 Nov, 2,140 rows from
728 claim transactions by 153 wallets. Each row: the time, the block, the claiming wallet, the SOL claimed, the
program, the signature. Three limits:

- They come from the creator scan: only creators of coins whose curve reached 60 SOL, a day either side of the launch.
- A creator's claim covers all of its coins, so one claim is written once for every coin of that creator. The row's
  coin is "a coin of the claimer", not "the coin the fee came from".
- They are found by a scan after the fact, never live. Fine for paper, useless as a trigger.

Scored a block after the event, every event kind we have loses about 0.014 SOL a trade (docs/autopilot.md,
11 Nov). That was every claim. The first claim alone, and the claim by a shared-fee user, were never split out.

**What a paper test needs.**

1. Claims tied to one coin: the claim of a fee-sharing user names the coin; GMGN's signal 18 ("pump.fun fee claim":
   coin, time, market cap at the trigger) is a second list (G).
2. A "first claim by this wallet" flag, from our own rows in time order.
3. The claim's size, for the minimum.
4. The landing: OKTech sees the claim and then buys, so a block after the claim, behind that block's first buys.
5. The exits of (b).

If the first-claim rows have a positive lower bound over 30 or more unseen claims, one capped live test follows:
`mode: "claimsniper"`, "First Claim Only", `claimMinSol` 0.5, 0.05 SOL a buy.

### (f) Dev-wallet snipes with the dev filters

| field | value | state |
|---|---|---|
| `mode`, `devWallets` | `normal`, the deployers with a SNIPE verdict (30 a sniper at most) | `dev_sniper` builds it; none was ever created |
| `acceptAsCreator` | off | it also takes coins that only name the dev: easy to fake (S) |
| `walletTriggers` | buy only after +X % within N ms, give up on a 25 % drop | built for the "confirm" rule; never live |
| `devMigrationCheck` | `{minMigrations, minMigrationRatePct}` from the deployer's record | never sent; it would be OKTech's own check next to ours |
| `blockedCreators` | our block list | never sent |
| `fundingAge` ([A] mode) | first funding 0.5 to 10 SOL within 15 minutes before the launch | needs level 9; unknown |
| `web.tracking` | follow the wallets a good creator funds | never sent; the one answer to creators who use a new wallet each coin |
| Hunt's `maxDevBuySol` | skip launches whose creator bought more than N SOL | sent on hunts; what 0 means is unknown |
| `maxMcapUsd`, `preexec` | a cap on the entry; the local dry run | as on other launch snipers |

- The evidence is against it so far: five serial launchers scored at the launch landing lost on 1,306 trades, every
  lower bound under zero (round 7). The live recipe's own line stands: "the edge lives in one-off creators: dev-wallet
  lists cannot express it."
- So the order is: Web tracking and [A] mode first on paper (they pick creators by their money, not by a list), a
  dev list only for a deployer whose verdict holds forward.

---

## 3. The tests that would settle the unknowns, cheapest first

Every test is inside the owner's standing yes for small live tests: 0.2 SOL a buy at most, 2 SOL lost a day at most.
Each one is written into `docs/live-recipe.md` before it starts and after. None was started. What is read
afterwards: the table `fills` (`snipetech remote sql`), and for copies `snipetech remote --wait run landing
--since-minutes N --leader <wallet>`.

| # | question | what to send | what it costs | what to read | what each outcome means |
|---|---|---|---|---|---|
| T0 | Did arm A buy a fourth time, and is 4 USDC sitting in the wallet? | nothing | nothing | `fills` has a buy of `EPjFWd…` (USDC) at 17:24:31 UTC on 12 Nov, 0.05 SOL, at arm A's bid, with no sale after it at 18:05. `oktech status` for the sniper's buy count; the wallet's token accounts | A fourth buy on a cap of 3: `totalSnipes` does not count what we think (ask OKTech). USDC still held: nothing of ours flags it, because stablecoins never count as held (`positions.py`) |
| T1 | What does each send of a sell pay, and does `sellPriorityFeeSol` price anything under the buy's priority? | nothing for the first half: it is read (section 1d). For the second: on any sniper that is sent with a buy priority above the minimum anyway (an arm at 0.005), set `sellPriorityFeeSol` to 0.001, OKTech's own smallest priority | nothing extra | `fills.priority_lamports` of every landed and failed sell | Untipped sends at 0.001: the field works from OKTech's minimum up, and 0.0001 was simply under it; send 0.001 on every sniper, and a raised buy priority is no longer paid on those sends. Still the buy's 0.005: nothing prices a sell but the buy's priority, and the bid belongs in the tip for good |
| T2 | Does a copy honour `computeUnits`? | on the next test sniper, `--compute-units 100000` | nothing extra | `fills.cu_limit` of the buy | 100,000: every copy gets half more rank for the same fee; make it the default. 150,000: the field is ignored on copies |
| T3 | Can stages sell in timed parts, and is `sellPercent` a share of the first position or of what is left? | one launch probe, 0.05 SOL, one buy, stages at 3, 6, 9 and 12 s selling 25, 25, 25 and 100 %, no other exit but a -40 % stop. Needs a small build: `autosell` cannot write timed part stages yet | about 0.03 SOL of fees (four sales), 0.07 at worst | the four sales' `tokens` against the buy's, and their times | 25, 25, 25, 25 % of the buy: "of the first position", the lab is right. 25 %, 18.75 %, 14.06 %, the rest: "of what is left", the builder must convert. One sale of everything: timed part stages do not exist, and the 6Z91 exit needs gain stages and timer extension instead. The first sale earlier than 3 s after the buy never happens (the 1.3 s watch delay) |
| T4 | What keeps a copy sniper out of a stablecoin swap? | the next small copy behind Cupsey with `platforms: ["pumpfun", "pumpswap"]` and a per-wallet `maxMcapUsd` of 5,000,000; 0.05 SOL, minimum bid | 0.009 SOL a round trip if it still buys; 0.001 if the buy fails on the cap | `fills` for the mint `EPjFWd…` after Cupsey's next USDC swap | No transaction: one of the two fields skips it (run each alone once to tell which). A failed buy: the cap works as a minimum-out and costs a failed send each time, so the platform list or OKTech's own blacklist is still needed. A filled buy: neither field applies to copies; ask OKTech |
| T5 | Does the bid in the tip land as well as the same bid in the priority? | arm C behind Cupsey: priority 0.001 + tip 0.034, the same 0.035 SOL as arm A; 0.05 SOL a copy; after T4's guard is in | 0.04 SOL a round trip; 10 buys: 0.4 SOL | `landing`: blocks after Cupsey, buyers ahead, their bids, the verdict column | First as often as arm A: the tip carries the bid; the priority stays at the minimum. Behind buyers who bid less in all but more in priority: the block follows the priority; then a "decent" priority is needed and the exit must be one or two sales. Three buys are a hint; the fee policy's own rule wants 10 a side |
| T6 | Does `ctbotRetriggerCooldownMs` stop the copy of a leader's second buy of a coin? | any small copy sniper with the cooldown at 60,000 ms, behind a wallet that re-buys (7aPo3n did twice on 8 Nov) | a copy's normal cost | `fills`: is there a second buy of the same coin inside a minute | No second buy: send it on every copy. A second buy: the cooldown is not per coin |
| T7 | Do gain stages fire, and is one trade through two stages one sale or two? | the same as T3 with gain stages at +5, +10, +15 % (25 % each) on a copy behind a wallet whose copies usually rise; sendable today through an exit with `tp_stages` | 0.03 to 0.07 SOL | the sales' sizes and blocks | settles F:167 and backs T3's reading of `sellPercent` on gain stages |
| T8 | How does OKTech time a momentum hold? | one launch probe, 0.05 SOL, a +10 % stage with `momentumHold` (2 % a second, 3 s, 30 s, 10 %). Needs the refusal in `autosell` lifted for a test | 0.03 to 0.07 SOL | the sale's time against the coin's trades on the tape | Sold between trades: a clock, as the lab assumes. Sold only on a trade: the lab's timing is off. Sold at once: the field was ignored |
| T9 | Where does a MIG buy land, and do its exits fire? | one MIG sniper, 0.05 SOL, one buy, window 10 s | 0.01 SOL of fees plus the position | the buy's place among the first PumpSwap trades | behind the first three: the lab's landing is right; else the MIG lab is re-scored |
| T10 | Is Shapes open on our account? | one sniper, `mode: "shapes"`, `crowdBuys` with `signal: true, buy: false`, cap 1, 0.01 SOL | nothing if signal-only is honoured; 0.02 SOL if it buys | whether it bought, and what | No buy and a signal: Shapes and signal-only work, and triggers can be checked live for nothing. A buy of the next launch: not open, or signal-only is ignored |
| T11 | Where does the trailing stop measure from after a part sale? | a launch probe with one +20 % stage of 50 % and a 15 % trailing stop | 0.03 to 0.07 SOL | the last sale against the coin's highs | from the high since the buy, or since the part sale (F:168) |

The five cheapest: T0, T1 and T2 cost nothing; T3 costs about 0.03 SOL; T4 about 0.01.

---

## 4. What to ask OKTech

For the owner to paste to their support. Each is one sentence, and each changes what we send or pay.

1. In `autoSell.stages` and in `stopLoss.stages`, is `sellPercent` a share of the position as it was bought, or of what
   is left when the stage fires?
2. Can several stages each have `sellAfterMs` and a `sellPercent` under 100, so a position sells in timed parts (40 %
   at 2 s, 20 % at 5 s and so on), and what does a stage do that has both `gainPercent` and `sellAfterMs`?
3. Every send of a sell, the failed duplicates included, pays the task's buy `priorityFee` although we set
   `sellPriorityFeeSol` to 0.0001: which setting prices a sell, and can a sell be sent once?
4. Inside a block, is our place decided by `tipAmount`, by `priorityFee`, or by both, and what does `globalSmartFees`
   change?
5. How do we stop a CTBot sniper from copying a wallet's swap into USDC, USDT or wrapped SOL: does `platforms` apply
   to CTBot, or is there a mint blacklist?
6. Does a CTBot wallet entry take `minMcapUsd`, `maxSol` and a buy multiplier, what are the exact keys, and do they
   skip the buy or fail it on chain?
7. How many wallets can one CTBot sniper hold, and does `totalSnipes` count per sniper or per wallet (sniper
   `6af5f176fe29cb0d8b704239` bought 4 times on a cap of 3 on 12 Nov)?
8. Are preconfirmations on for CTBot on our account, and with them does the sniper buy even when the copied wallet's
   transaction then fails?
9. Why can the buy that uses up `totalSnipes` be left with no exit at all (three coins on 5 and 6 Nov), and why did
   exits fire for coins the sniper never bought (8 Nov)?
10. In Hunt, does `maxDevBuySol: 0` mean "skip every launch whose creator bought anything" or "no limit"?
11. Are momentum hold and timer extension checked on a clock or only when a trade arrives, on price or on market cap,
    and do they ever hold back a stop-loss?
12. After a stage has sold part of a position, does the trailing stop measure from the high since the buy or since
    that sale, and is one trade that reaches two stages one transaction or two?
13. Is `extraParams.computeUnits` honoured on CTBot buys, on the curve and on PumpSwap?
14. Which approvals does our account have today (Shapes, Web, account filter, [A] mode, Signals, Social), and how many
    snipers may it run at once?
15. For a Twitter task with "Extract CA": how long from the tweet to the buy on the TwitterAPI.io source against the
    1322 stream, can the buy carry one of our saved sell strategies, and can the Sniper API key create or start such a
    task?
16. For ClaimSniper: which claims count (the creator's own, a fee-sharing user's), and how soon after the claim does
    the buy land?
17. For `ctbotCopyExit`: is `sellThresholdPercent` the share the wallet sells in one transaction or added up over
    several, and can `sellPercent` follow the wallet's part sales?
18. For MIG: how is "no migration window" sent, what is the largest Migration Window it takes (is 86,400 s allowed?),
    and where does the buy land against the migration transaction?
19. For `ctbotCopyExit` past the curve: does it follow the followed wallet's sell on PumpSwap? The one time it was
    needed there it did not sell (idea I58, read from our fills and the tape on 13 Nov): 7aPo sold its whole bag
    (3.60 SOL, one transaction, on PumpSwap) 8 s after our buy of 8Wi8 and no sell of ours followed; ours came 250 s
    after the buy with the sniper's own fees, by no exit we had sent. Of our 4 live copies bought on PumpSwap none
    was sold by the copy exit (dead exit 2, stop-loss 1, unknown 1); all 19 copy-exit sells on record are of 6 to
    8 Nov.

Questions 1, 2, 3, 4 and 5 decide the real-size copy. The rest can wait a day.

---

## 5. What we get wrong today

Listed with file and function. Nothing was changed: `src/snipetech/oktech.py` is another helper's this afternoon
(branch `wf/copy-safety`, the copy sniper builder).

| # | where | what is wrong or risky | evidence |
|---|---|---|---|
| 1 | `oktech.py`: `list_copy_sniper`, `copy_sniper` | A copy sniper carries no `platforms` and no guard on what is bought. It copied Cupsey's swap into USDC twice | `fills`: buys of `EPjFWd…` at 17:05:56 and 17:24:31 UTC, 12 Nov |
| 2 | `fees.py` (module text, `RULE`), `config.py` (`OktechConfig.sell_priority_sol`), `oktech.py` (`autosell`'s docstring), `docs/oktech-features.md` line 395, `docs/live-recipe.md` 8 Nov | All say "the sell priority is what each failed duplicate pays". Since the change not one of 28 failed duplicates paid it: 25 paid the buy's priority, 3 an older sniper's 0.002. The duplicates were never made free | DB, section 1d |
| 3 | `oktech.py`: `list_copy_sniper` (`priority_fee` by hand), `fees()` (`tip_share` 0.7), `dev_sniper` | A hand flag or a verdict's bribe can raise the buy priority. `fees()` puts 30 % of a bribe into it. Every sale then pays it again, 2.47 times a sale. Arm A's 0.005 cost 0.015 SOL a round trip in priority alone | L 12 Nov; DB |
| 4 | `config.py`: `_fee_bounds_today`, `OktechConfig.max_buy_sol`, `BookConfig.fill_max_sol` | The fee policy may tip 0.005 SOL at most, and a buy is 1 SOL at most. Right for 0.2 SOL copies. The 2 to 10 SOL case has no fee kind of its own: today it can only be sent with hand flags that step around the policy | W; `fees.choose` |
| 5 | `oktech.py`: `autosell` | Every stop is sent with `confirmMs: 250`. The lab's stop fires at once. `docs/oktech-features.md` (the table at line 118) says confirm windows are not sent | code, line `"confirmMs": 250` |
| 6 | `oktech.py`: `autosell` | Two keys go out as null: `trailingStopLoss.activateAfterGainPercent` when there is no arming gain (10 of the 11 trailing snipers), and `gainPercent` on a timed stage. OKTech ignored thresholds sent as null in Hunt on 5 Nov and bought every launch. CAT's trailing stop did not fire the same evening | DB; L 5 Nov |
| 7 | `exits_menu.py`: `lab`, the exit of every book sniper | No take-profit at all, and a dead exit at 1 s that fires first (a median 1.9 s after the buy). It "sells seconds later into the dip, or never locks in a run" (L 6 Nov). The winners do the opposite. 20 dead exits made -0.36 SOL and 19 copy exits -0.43 | DB; W |
| 8 | `oktech.py`: `list_copy_sniper`, `copy_sniper` | No `ctbotRetriggerCooldownMs`: a leader's second buy of the same coin is copied again. Both such copies on 8 Nov lost (-0.030, -0.178) | L 8 Nov |
| 9 | `oktech.py`: `execution_params`, `config.py`: `OktechConfig.compute_units` | The compute limit has stood unset since the audit of 6 Nov called it open. Buys ask for 150,000 and use about half | A, "Still open" |
| 10 | `oktech.py`: `strategy_sniper`, kind `mig` | A MIG sniper is sent `preexec` (it replays a create and a buy: pump.fun launches only) and `platforms: ["pumpfun"]` though it buys on PumpSwap. The audit's own rule: never send a block the mode does not use, because OKTech answers 201 and then does something else | code; A |
| 11 | `oktech.py`: `replica_from_study` | Its sell ladder is one stage of 50 % at +20 % with no 100 % stage last, built by hand beside `autosell`, never checked by `validate_exit`. It also sends `pumpFilters`, `metadataFilter` and a top-level `maxMcapUsd`, none ever seen working. Never created, but one command away | code; F:83 |
| 12 | `oktech.py`: `strategy_sniper` | "This account does not have shapes" is written as a fact. `crowdBuys` was only ever sent under mode `normal`. Shapes shares one approval with Hunt, which works | F:198, F:327; L 5 Nov |
| 13 | `oktech.py`: `list_copy_sniper` | `ctbotCopyExit` waits for the leader to sell half its bag. A leader that sells in slices never trips it; one that dumps everything is copied a block late, behind the dump. Whether "half" is one transaction or a running sum is not known | L 6 Nov, 8 Nov |
| 14 | `positions.py` (the shared "holds a coin" rule) | Stablecoins never count as held. Right for `/stop`. But a copy sniper that buys USDC and does not sell it leaves SOL parked where nothing looks | A row 7; test T0 |
| 15 | `config.py`: `BookConfig.keep_curve_buys` | Plain SOL is kept for two curve buys of 0.2 SOL. A 2 SOL curve copy needs 2 SOL plain, and wrapped SOL was back at 0.22 right after our first conversion (15:30 UTC, 12 Nov; most likely OKTech's doing) | L 11 and 12 Nov |
| 16 | the book (`BookConfig.max_buys_per_sniper` 3, `retire_after_buys` 1) | The book makes a new sniper after every buy. That works around the "last buy has no exit" fault. It also means the cap itself is rarely put to the test, and arm A bought 4 times on a cap of 3 | DB; `fills`, 12 Nov |

---

## 6. The ten features most worth applying next

In order. Each names the test that has to come first.

1. **The bid in the tip, the priority at its minimum** (`tipAmount`, `priorityFee`). Three to eight times cheaper for
   the same bid. Test T5.
2. **A guard on what a copy buys** (`platforms`, `maxMcapUsd` per wallet). It bought USDC twice. Test T4; question 5.
3. **Selling in parts** (`stages` with `sellAfterMs` or `gainPercent`). The winners' exit, and "partially sell but
   hold some". Test T3, then T7.
4. **A trailing stop on the part we keep** (`trailingStopLoss`). "Hold it until it starts going down." Sendable today;
   test T11 for where it measures from.
5. **A market-cap band and a size band per copied wallet** (`minMcapUsd`, `maxMcapUsd`, `minSol`, `maxSol`). The lab
   scores the band since 12 Nov; the ceiling is also the buy's only price limit. Question 6.
6. **A tighter compute limit** (`computeUnits: 100000`). Free rank. Test T2.
7. **Preconfirmations on every copy** (`preconf`). On since 11 Nov; keep reading the landing with it.
8. **Buy when two known wallets are in** (`ctbotMinWallets: 2`, many wallets in one sniper). The analyst's best paper
   lead has this shape. Question 7.
9. **Timer extension and momentum hold** (`timerExtension`, `momentumHold`). The nearest OKTech gets to "drop it at
   2 s unless it is running" and to "the coin only ups". Test T8; question 11.
10. **A Twitter task that pulls the address out of a post** (and the tweet-bound sniper). The owner's own idea, and
    the one trade here where we would not be racing copy bots behind the same wallet. Paper first (section 2d); the
    owner pastes a TwitterAPI.io key into OKTech's Settings.

Not on the list, on purpose: MIG and dev lists (they lose on paper), wick mode (it buys our own sale), auto-stop (it
switches the exits off), and every mode that launches coins.
