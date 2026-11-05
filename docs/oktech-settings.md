# OKTech sniper settings — what each does and what it is good for

Source: the OKTech Sniper API page (v1) including the stored config of the account's own sniper
"PROJECT SNIPE". Settings marked *(undocumented)* appear only in that stored config; their meaning is
inferred from the field names and must be confirmed in the app before money depends on it.

## Modes (`mode`)

| Mode | What it snipes | Useful for |
|---|---|---|
| `normal` | launches by watched dev wallets, a whole launchpad (`monitorProgram`), known mints, DBC / LaunchLab configs | dev sniping, launch strategies, everything below |
| `mig` | the migration of a curve to its pool (`migrationExpirySec`, `devMigrationCheck {minMigrations, minMigrationRatePct}`) | buying graduations; only devs whose coins migrate often |
| `ctbot` | copies wallets (`ctbotWallets [{address, minSol, maxSol, minMcapUsd, maxMcapUsd}]`, `ctbotMinWallets` within `ctbotTimeWindowMs`, cooldown, `ctbotCopyExit`, groups, `ctbotMigrationFollow`) | KOL / smart-money copying; "N wallets bought within X ms" consensus. Measured: fires on the leader's *attempt*, even when it fails on chain |
| `copydev` | `copyDev {targetWallets, creatorWalletId, matchCashbackStatus, mirrorSharingConfig}` + `autoDeploy` | mirrors a dev's launch with a launch of your own (copy-deploys) — not a buying strategy |
| `reverse` | `reverseCopy {wallet, direction, platform, mint, pool, sellPercent, prebuild}` | acts on one wallet's trade in one known mint/pool with a prebuilt tx (e.g. buy when they sell) |
| `claimsniper` | `claimGithubUsers`, `claimMintAddresses`, `claimMinSol`, `claimWalletClaims` | coins whose creator rewards are claimed by given GitHub users / wallets |
| `stonk` | launches quoted in stock tokens (`stonkQuotes`, patterns, `stonkAutoMode`) | xStocks-quoted launches |

## Buying (every mode)

| Setting | What it does | Why it matters for us |
|---|---|---|
| `buyAmount` (0.001–100) | SOL per buy, main wallet | position size; the lab's edge scales with size minus fixed fees (~0.007 SOL per round trip measured) |
| `priorityFee` (≥0.001), `tipAmount` (≥0.002) | per-buy fees | landing position. PROJECT SNIPE pays 0.25 + 1.3 SOL per 15 SOL buy — sized to win the creation slot |
| `singleSnipe` + `totalSnipes` (1–100) | stop after N buys | **the safe cap**: the task keeps managing its exits until positions close. Stopping a task by hand also stops its auto-sell (learned live: 3 positions stranded) |
| `additionalSnipers` (≤9) | more wallets buying alongside, own sizes / exits | spread size across wallets |
| `maxMcapUsd` | buy carries a min-out so it fails above this cap | never chase a launch that already ran |
| `blockedCreators` (≤100) | dev blacklist on every venue | feed snipetech's AVOID / rinse devs |
| `speedMode` ("Jetstream") | ctbot only; normal is always Jetstream | copy speed |
| `preconf` | preconfirmations (account approved) | earlier landing |
| `preexec` (approved) `{padCu, padPercent, loadedAccounts, complement, complementWaitMs, createWaitMs}` | simulates the create + own buy locally, then asks for exactly the compute it needs; the scheduler ranks by reward per CU, so the same fee ranks higher; `complement` sends a second, tighter wave on the same nonce | **speed for free** — on in PROJECT SNIPE |
| `extraParams` `{computeUnits, loadedAccountsDataSizeLimit, useJitoMemo, globalSendCount, globalSmartFees, addressLookupTables, seedAccount}` | low-level tx tuning; smart fees adjust fees automatically | copy from an app-built sniper |
| `pumpTradeVersion` (v3/legacy), `sendTxVersion` (legacy/v1) | instruction / wire format | leave default |
| `notifyOnComplete` | Discord/Telegram alert when a task ends | monitoring |
| `signalOnly`, `relaySignals` *(undocumented)* | emit signals instead of / besides buying | dry runs |

## What to watch (normal mode)

| Setting | What it does | Why it matters |
|---|---|---|
| `platforms` | pumpfun, dbc, raydium (LaunchLab), pumpswap, raydiumv4, dammv1, dlmm | pick the venue |
| `devWallets` (≤30) | buy launches signed by these creators | dev sniping (snipetech's SNIPE verdicts) |
| `acceptAsCreator` | also take creates that only *name* a dev (off-chain created coins) | catches pump.fun web-created coins of a watched dev; spoofable |
| `monitorProgram` | every launch on one platform | launch strategies; always pair with filters or a small `totalSnipes` |
| `mints` (≤30, `preAta`) | snipe a known mint the moment its curve/pool appears | pre-announced tickers |
| `walletTriggers {wallet, stages[{percentGain, withinMs}], maxWaitMs, abortOnDropPct}` | wait for a price move before buying a dev's launch | snipetech's "confirm" entry rule for rinse devs |
| `pumpFilters {nonSolQuoteOnly, cashbackOnly, sharingCheck}` | pump.fun-only gates | non-SOL quoted coins (the Fhbh1 replica), cashback coins |
| `pumpAutoSwap` / `dbcAutoSwap` / `raydiumAutoSwap` | buy non-SOL-quoted launches with SOL via the swap server | wallets only need SOL |
| `pumpAutoMode` + `pumpTotalSnipePerNew`, quote block lists | only launches quoted in quotes pump.fun newly whitelists | first launches on a new quote asset |
| DBC: `dbcConfigs`, `dbcRules` (fee, LP lock, mint authority, migration target…), `dbcProtection.maxFeePercent`, `dbcOnUnknown` | Meteora DBC launches with rule gates | safety presets ("no rug levers", "low tax") |
| LaunchLab: `launchlabConfigs`, `launchlabRules` (curve, supply, fees, vesting, quote…), whitelists, `launchlabMaxFeePercent`, `launchlabSnipeOncePerConfig` | letsbonk / LaunchLab launches with rule gates | LaunchLab strategies |
| `liquidityFilter` | min / max pool liquidity per quote, for pool venues | direct pool launches |
| `loadout {entryDelaySecs, maxFeePercent, leadMs, buyProtectedAtOnce}` *(undocumented)* | `entryDelaySecs` looks like "buy N s after the launch" | delayed entries (after the first dump) |
| `accountFilter {whitelist, whitelistMode, blacklist}` *(undocumented)* | require / refuse accounts in the create | bundle / bot fingerprints |
| `launchShape {wrapped, wrapperPrograms, newWrappersOnly, platformPayer}`, `wrappedLaunches` *(undocumented)* | launches made through wrapper programs (third-party launchpads on pump.fun) | include / exclude front-ends |
| `crowdBuys {minSigners, minTotalQuote, platforms, signal, buy}` *(undocumented)* | **measured live: fires at the create, at the starting price** — evidently the buys bundled with the create (its co-signers), not later whale buys | bundled-launch detection; NOT "after a 5 SOL buy" |
| `hunt {timeWindowMs, triggerMode, thresholds{minTotalSol, minUniqueBuyers, maxUniqueBuyers, maxDevBuySol, minCpiBuyers, requireKnownBot, minLutMatches, minAccountMatches}, knownBot*}` *(undocumented)* | looks like: buy when, within `timeWindowMs` of a launch, buyers put in ≥ `minTotalSol` (≥/≤ N buyers, dev buy ≤ X, bot buyers via CPI / known bot programs, wallets, LUTs) | **the candidate for the lab's best trigger** ("buy right after early buyers put in ≥ 5 SOL") and for "only when known snipers are in" |
| `web.tracking` *(undocumented)* `{trackTransfers, trackConfigs, autoSubscribe, autoMigrationSnipe, maxDepth, fundingFilters, preload, childTracking, creatorFilter, unsubscribeAfterMs}` + `feeClaimerTracking`, `leftoverReceiverTracking`, `tokenRecipientTracking` | follows a dev's money: wallets it funds (to `maxDepth`), fee claimers, leftover / token recipients — and auto-subscribes them as devs | **devs who rotate fresh wallets** (rinsers) stay covered; `creatorFilter` limits how many creations a tracked wallet may have |

## Filters

| Setting | What it does | Why |
|---|---|---|
| `metadataFilter` (normal / mig / stonk) `{matchMode, conditions[{variable, operator, value, action}], fetchOffChain, timeouts, allowOnError}` | name / symbol / uri, or any metadata-file field (twitter, website…) with exists / equals / contains / regex…; placeholders `{name}`, `{symbol:compact}` | e.g. "website contains {name:compact}", "twitter links a /status/" (tweet coins) |
| `metadataFilter.tweetBinding {variable, matchOn: tweetId}`, `maxTweetAgeSec` *(undocumented)* | bind a sniper to a trigger tweet; started by OKTech's Twitter task; tweet must be at most N s old | **tweet / KOL-post sniping** — the original "buy right after an influencer posts" idea is built in |
| `vanityFilter` | mint address starts / ends / contains | e.g. only `…pump` mints |

## Auto-sell (`autoSell`, needs ≥1 stage when enabled)

| Setting | What it does | Why |
|---|---|---|
| `stages[{gainPercent, sellPercent, sellAfterMs, delaySellAllSeconds, processDelayMs}]` (1–10) | take-profit ladder; per-stage bail timers | scale out of winners |
| `sellAfterSeconds` / `sellPercent` | sell-all timer | the lab's max hold (300 s) |
| `stopLoss {stages[{percentLoss, sellPercent}], confirmMs}` | stop below entry, must hold `confirmMs` | cut losers (lab: −15% / −30%) |
| `trailingStopLoss {percentFromPeak, activateAfterGainPercent, confirmMs}` | sell on a drop from the peak | "if it's only going up hold, if it dips get out" (lab: 25% from peak) |
| `deadExit {staleSeconds}` | sell when nothing trades for N s | the lab's best exit on most triggers (1 s) |
| `autoStop {percentGain, withinMs}` | a fast pump turns the exits off and lets it run | moonbags on runners |
| `migrationSell {sellPercent, delayMs}` | sell at migration | exit into the graduation pump |
| `momentumHold {minVelocity, lookbackMs, maxHoldMs, trailingDropPercent}` | keep holding while the price climbs fast | ride momentum past the timer |
| `timerExtension {minGainVelocity, lookbackMs, extensionMs, maxExtensionMs, cancelDropPercent}` | extend the sell timer while gaining | same, finer |
| `stageAcceleration {minStages, delayMs, releaseOnDropPercent}` | speeds up the ladder | |
| `wickServer {walletId, wickGainStagesOnly}` | sell into wicks via a separate server | better fills on spikes |
| `sellTipAmount`, `sellPriorityFeeSol` | sell fees | exits must land too |

## What this means for snipetech

1. **Caps belong inside the sniper** (`singleSnipe` + `totalSnipes`), never in an outside stop: a stopped task stops selling.
2. The lab's best trigger needs `hunt` (or `loadout.entryDelaySecs` + filters), not `crowdBuys`.
3. Dev sniping should combine `devWallets` + `web.tracking` (rotating wallets) + `blockedCreators` (our rinse list) + `walletTriggers` (confirm rule).
4. Tweet sniping exists natively (Twitter task + `tweetBinding` + `maxTweetAgeSec`).
5. Speed: keep `preexec` (+ `complement`) and `preconf` on, as PROJECT SNIPE does.
