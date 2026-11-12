# OKTech — every feature found in the web app (5 Nov 2026)

Source: the oktech.me app bundle (`/assets/index-UAY4O43V.js`), help texts, form
labels and config keys. Items marked **approved** are gated to approved
accounts; the API accepts them from any key and silently ignores them otherwise
(verified live 5 Nov, see live-recipe.md).

## Products (top-level modules)

| Module | What it is |
|---|---|
| Sniper | Task runner that buys launches / migrations / copies wallets. Modes below. |
| Mayhem | Dev-wallet + helper-wallet campaign orchestrator (launch, helper buys/sells, dev trade plan, profit goal). |
| Catch | Realtime pump.fun launch feed and search. |
| Vamp | "Contract Address to Vamp": copy-launch a coin from an existing CA. |
| Social | Live tweet feed, FOMO traders/coins feed, News feed; handles you follow; Ultimate speed stream. |
| Signals | On-chain alerts: wallet activity, token launch, liquidity change, program instruction, large/whale transfers; optional webhook; can fire a sniper. |
| Twitter tasks | Server-side triggers on posts/replies/retweets/follows/profile updates/community membership that buy a CA extracted from text, with a market-cap range. |
| Stocks | Stonk Auto / Pump stock launches. |
| Launch | Launch DBC (Meteora) tokens on Solana; copy launch. |
| Sniper API | Keys that create snipers from scripts/webhooks (POST, names an imported wallet, never carries a key). |
| Settings / Wallets | Solana, ETH, Base, BNB, Arc, Robinhood-chain keys; TwitterAPI.io key; Discord/Telegram notifications. |
| Admin | Platform operators only. |

## Sniper modes (`mode`)

`normal`, `mig` (pump migration sniper), `shapes` (launch-shape gate, **approved**),
`hunt` (buy after other buyers cross thresholds, **approved**), `web` (follow a
wallet's funding web, **approved**), `autodeploy` (**approved**), `ctbot`
(copy-trade), `reverse`, `claimsniper`, `ansem`, `gucci`, `stonk`, `loadout`
(app.loadout.xyz, **approved**), `copydev`.

## Targeting

- **Platforms**: pump.fun, PumpSwap raw pools, Raydium LaunchLab (+ platform/global
  config rule language: fees, vesting, supply, curve, quote, migration target),
  Meteora DBC / DAMM v1 / DLMM, Raydium AMM v4, Stonk.
- **Creator filter**: every launch or named creators; watched creators; blocked
  creator wallets; Developer Migration Check (min past migrations / min
  migration rate %); Dev Funding Gate; per-deployer price-action gating (stages
  "+X % within N ms", up to 5 per dev).
- **Metadata filter**: name/symbol contains / not contains / regex; website,
  twitter, telegram, image presence; max market cap USD.
- **Quote whitelist / blacklist**, **ignore configs** (wins over everything),
  **required / must-not-include accounts**, atomic launches only.
- **Launch shape** (**approved**): wrapped launches, wrapper programs, ignored
  wrappers, new wrappers only, platform payer, signal-only, relay to Discord.
- **Crowd buys**: several signers each buying one coin in one tx; min signers,
  min total quote, platforms, signal-only or buy.
- **Hunt** (**approved**): thresholds (min total SOL from others, min/max unique
  buyers, max dev buy, min CPI buyers), time window, trigger mode any/all,
  known-bot programs/wallets/LUTs/accounts, "require a known bot", fleet
  defaults.
- **Web** (**approved**): track transfers and configs from base wallets, depth,
  lookback days, historical preload, Helius creation-history filter,
  auto-subscribe, auto migration snipe, unsubscribe after ms / N creations,
  inherit rules to webbed wallets, token-recipient tracking.
- **CTBot copy-trade**: copy wallets, buy multiplier, Buy-on-Sell / Sell-on-Buy
  reversal, copy exit (sell threshold %, sell %), migration follow (when the
  leader's buy completes the curve, buy the AMM pool), skip check.
- **MIG**: snipe pump migrations, optional dev filter.
- **Twitter-triggered**: max tweet age, auto-stop ceiling (5 s–24 h), handle
  match strictness (verbatim link / any link shape / any mention).

## Execution

- `singleSnipe` + `totalSnipes` (attempts, failed buys count), cooldown between
  launches, max launches, transactions per wallet, entry delay (Loadout).
- Buy amount, priority fee, tip; sell tip / sell priority; Jito memo; global
  send count; global smart fees; Jetstream ALT tables + extra lookup tables;
  compute units; loaded-accounts data-size limit + pad %.
- `pumpTradeVersion` v3 / legacy; `sendTxVersion` legacy / v1 (SIMD-0385, 4,096
  bytes, no LUT, no Jito memo).
- Seed account (buy via ephemeral account, then sweep) on/off.
- Two waves (create wait / complement wait).
- **Local pre-execution** (**approved**), **preconfs** (**approved**), **account
  whitelist/blacklist** (**approved**).
- Rate limits and a running cap of 20 snipers per account.

## Auto-sell

- Base: sell X % after N s (0 % on expiry keeps the position).
- **Stages**: gain % and/or bail timer per stage, process delay ms; 100 % must
  be last; drag to reorder.
- **Stop-loss**: laddered stages (e.g. 40 % at -25 %, 50 % at -50 %), confirm
  window ms (debounce).
- **Trailing stop-loss**: drop % from peak → sell %, arms after +gain %, confirm ms.
- **Dead exit**: no trades for N s after entry → sell %.
- **Auto-stop**: +gain % within N ms.
- **Migration sell**: on migration to a new pool, sell % after delay (100 ms–1 min).
- **Momentum hold**: defer a triggered sell while velocity ≥ X %/s over a
  lookback; release on fade, peak-drop %, or max hold.
- **Timer extension**: velocity above threshold extends a stage's timer, capped;
  a drop ≥ cancel % snaps it back.
- **Stage acceleration**: when one tick trips several gain stages, the lowest
  sells now, the rest stagger; a drop ≥ release % fires them all.
- **Wick mode**: a second "wick wallet" fires a counter-buy before your sell
  (fixed or random SOL range, chance %, gain-stages-only toggle).
- Saved sell strategies, loadable into any task; auto-sell override per sniper.

### Which of these the lab can score (12 Nov 2026)

The lab is our simulator. It replays the tape and sells a paper position by an exit rule.
Since 12 Nov it knows three more of OKTech's sells. No lab has run with them yet, so nothing here says they pay.

| OKTech sell | In the lab | Sent to a live sniper |
|---|---|---|
| Sell-all timer | yes | yes |
| One take-profit, whole or part | yes | yes |
| **Ladder of take-profit stages** | yes, new (`tp_stages`) | yes, with one open question (below) |
| **Ladder of stop stages** | yes, new (`sl_stages`) | yes, same open question |
| Trailing stop | yes | yes |
| Dead exit | yes | yes |
| Migration sell | yes | yes, never seen firing |
| **Momentum hold** | yes, new, research only (`momentum_hold`) | no: refused until a live test |
| Selling out in parts | yes, research only | no |
| Auto-stop | no | never (it switches the exits off) |
| Timer extension, stage acceleration, wick mode, per-stage bail timers, confirm windows | no | no |

How a ladder is scored, in plain words:

- A take-profit ladder is a list of steps, for example a quarter at +20 %, a quarter at +50 %, a quarter at +100 %.
- Each step sells its share of the **first** position when what we still hold is worth that gain.
- A stop ladder is the same going down, for example 40 % at -25 % and 60 % at -50 %.
- What no step sold stays with the other exits: the stop, the trailing stop, the dead exit, the copy exit, the timer.
- Every sale of ours moves the price. The next step is priced in the pool our earlier sales left.
- Every step pays its own transaction fee.
- One trade that reaches two steps sells both on that trade.
- The old "sell part at one gain" exit is a ladder of one step. Its numbers did not change: 60,480 simulations on
  random markets, old code against new, gave the same result to the last digit.

How momentum hold is scored:

- A take-profit step that fires while the price is still running waits.
- "Running" means the position's value rose at least X % a second over the last N seconds.
- It sells at the first of three moments: the rise fades below X, the value falls a set share under its high, or
  the longest wait is over.
- A stop is never held back. A stop or trailing stop that fires during the wait sells as it always does.

Every lab now scores 7 such exits next to the 54 it had (MIG: 2 next to 23). Their labels read step by step:
`TP+20%@25%/+50%@25%/+100%@25%` sells a quarter at each gain; `SL-10%@50%/-25%@50%` is a stop in two halves;
`MOM2%/s@3s<30s-10%` waits while the price is up 2 % a second over 3 s, at most 30 s, or until 10 % under the high.

Cost in lab time: 13 % more exits. A ladder exit costs more than a plain one, because every step that fires replays
the pool once more. On synthetic markets, where a step fires on about every second position, the exit simulation
took 20 to 28 % longer in four runs (about 1.3 s for the 54 old exits, about 1.6 s for all 61, on 240 positions).
The 54 old exits run as fast as before, within the noise of the measurement. Simulating exits is one part of a lab,
next to reading the tape and finding the moments, so a whole lab grows by less. On the real tape fewer positions
reach +20 %, so the extra should be nearer 13 %. That is not measured yet.

### Unknown until a tiny live test

These are guesses the lab makes about OKTech. Each one needs one small live trade to settle.

1. **What does a stage's `sellPercent` mean: a share of the first position, or of what is left?**
   The docs and the app's help texts do not say. The lab models "of the first position".
   The two differ from the second step on. With steps of 25 %, 25 %, 25 %: our reading sells 25 % of the first
   position at the second step; the other reading sells 25 % of the remaining 75 %, which is 18.75 %.
   The test: one 0.05 SOL buy with three steps close together, then read the size of each sale on the tape.
   Until then a sniper with a ladder carries a note saying so. The step that sells the last of a position is always
   sent as 100 %, which is right under both readings.
   If OKTech reads "of what is left", a live ladder sells smaller parts than the lab scored. It never leaves a
   position without its stop.
2. **Is the same true for stop stages?** We send them the same way and assume the same reading.
3. **"100 % must be last".** We read it as: the last take-profit stage must sell 100 %. So a ladder that leaves a
   rest is followed by a stage at +10000 % that never fires. If the rule is only about order, that stage is harmless.
4. **One trade reaches two steps: one transaction or two?** The lab charges two.
5. **Where does the trailing stop measure from after a step sold?** The lab measures the drop from the high since
   that sale, and treats the trail as armed once a take-profit step has sold. OKTech may measure from the high since
   the buy.
6. **How does OKTech time a momentum hold?** Its settings are `minVelocity`, `lookbackMs`, `maxHoldMs` and
   `trailingDropPercent`. Not known: whether it checks on a clock or only when a trade arrives, whether the speed is
   read on price or on market cap, and whether it holds back only take-profit stages. The lab assumes a clock, the
   price we would sell at, and take-profit stages only. Because of this no live sniper is sent a momentum hold:
   `oktech.autosell` refuses the exit, so a rule scored with it cannot become a sniper by itself.

## Mayhem (campaigns)

Dev wallet launches with a dev buy; helper wallets fire random-size buys
(min/max per buy, min/max buys per helper, min/max gap ms); helper sells by %
after N helper buys / N seconds / a milestone; dev trade plan (buy more, partial
sell, final exit, mark milestone, wait); profit goal SOL, max launches,
iteration cooldown; live stats on the mayhem operator's buys/sells/streaks/net
flow vs organic (non-bot) buys; "Mayhem Battle".

## Tiers seen

VIP (updates feed, some tasks), Ultimate (speed stream, more Social handles),
Sniper API keys (needs Sniper), approved-user feature flags listed above.

## Second pass — everything else found (small or hidden)

### Feature-gate table (what an admin can grant per account)
Pages: Twitter tasks & groups · Chain (on-chain signals) · Service settings ·
Catch · Sniper · Mayhem · Vamp · Over (licenses, fee wallets, servers) · Recent
updates · Social. Sniper modes: advanced modes (CTBot, ClaimSniper, Reverse,
MIG, CopyDev, Stocks) · [A] mode · Gucci mode · Stocks Auto · Preconfs
(approved) · [S] tab Shapes/Web/Hunt (approved) · AD AutoDeploy (approved) ·
Loadout (approved) · Local pre-execution (approved) · Account whitelist/
blacklist (approved) · Sniper API. Streams: Speed stream (Ultimate) · FOMO
tracking. Admin can also set a per-account max concurrent snipers (site
default otherwise), VIP expiry, banners, "Recent updates" posts, maintenance
mode (stop all snipers, restart them when it ends unless the user stopped
theirs), and share codes.

### Sniper modes not described above
- **[A] mode (`ansem`)** — Dev Funding Gate: reads the deployer's FIRST-ever
  funding; buys only if it was a round chunk of SOL inside your band (default
  0.5–10 SOL) within your window (default 15 min) before deploying. Max dev tx
  count rejects active wallets. Fail-closed on/off ("Fail Open" logs instead).
- **Gucci mode** — a list of "gucci entries" (per-entry targets); no help text.
- **AutoDeploy (AD)** — launches coins itself from a metadata URI / coin form
  with a chosen creator wallet; dev buy funded from the creator wallet's own
  quote balance (launch still goes out without the buy if short). Quote mint
  selectable; "if pump.fun already accepts it, the launch goes out as soon as
  the sniper is started."
- **CopyDev** — watches external dev wallets for pump.fun CREATE_V2 and replays
  the create + dev buy under your creator wallet. "Mirror Sharing Config"
  splices the target's fee-sharing config (and follow-up updates within 30
  min). "CDEV auto vamps token creations. This is experimental."
- **Reverse** — watch one wallet; Buy-on-Sell or Sell-on-Buy against a target
  mint + pool; Raw mode with instruction filters; Prebuild (pre-signed reverse
  txs; don't trade that wallet by hand).
- **ClaimSniper** — fires on pump.fun creator-fee claims: by GitHub user, by
  mint, by claim wallet allowlist (signer enforced), "Snipe Everything",
  "First Claim Only" (first time the social user has claimed SOL), minimum SOL
  per claim.
- **MIG** — pump migration sniper; Migration Window (max seconds from CREATE to
  MIGRATE, default 10 s); deployer blacklist; Developer Migration Check.
- **Stocks / Stonk** — StonkFun V1 (CLMM) or V2 (LaunchLab); creator is
  StonkFun's deployer; buys in the quote, auto-sell exits back into the quote.
- **Loadout** — app.loadout.xyz launches, Entry delay in seconds with a
  "send this much earlier" lead; the on-chain fee steps once per second.
- **Normal extras** — Monitor Program (program-wide subscription), mints list
  ("known mints to snipe the moment their pool or curve appears"), DBC
  protection ("Buy protected launches at once", "No rug levers": LP locked,
  mint authority gone, metadata frozen), "Once per config" (one launch per
  listed config for the sniper's life), platform payer, "Track" and "Both /
  Any" config logic.

### Quotes and swaps
- **Pump Auto-Swap / Non-SOL curves only / Pump · new quotes only** —
  pump.fun curves can now be quoted in arbitrary tokens; Auto-Swap routes the
  buy through a regional swap server that converts SOL to the quote in one
  tx. "Snipes per new quote" budget; quote pattern blacklist (address-pattern,
  OR'd); "A cashback coin takes the legacy instructions either way."
- **Raydium Auto-Swap, DBC Auto-Swap** — the same for LaunchLab / Meteora DBC
  launches quoted in MET, USDC, etc. Sells still land in the quote.
- **Match Cashback Status** (experimental) — only pump.fun launches eligible for
  the creator-cashback program. **Sharing check** — "Pump.fun only. Enables the
  bot's sharing check on incoming launches to improve transaction land rate."
- **Creator rewrite check** — catches a dev who rewrites the curve's creator
  with a fee-sharing config right after create (moves the vault).

### Execution extras
- **Max market cap (USD)** is enforced as each buy's own min-out, so a buy that
  would land above it fails on chain rather than filling.
- **Pre-create token account** (ATA, Token or Token-2022) at sniper start so
  the buy carries no account creation.
- **Preconfs** (approved) — registers watched dev / copied wallets on the hub's
  preconfirmation lanes so their txs are seen before they confirm.
- **Additional snipers** — extra wallets each with their own buy amount and
  optional own auto-sell; "Inherit primary auto-sell".
- **Auto-Transfer** — after the snipe and auto-sell, split the bought token to
  up to 3 destination wallets by percent (~4 s after the last auto-sell; rent
  ~0.00204 SOL per new recipient).
- **Bind to trigger tweet** — a tweet-triggered sniper only buys tokens whose
  metadata references that exact tweet; the manual start button is disabled.
- **Notify on complete** (Discord/Telegram); **Relay to Discord** of shapes,
  crowds and buys; **Jupiter API key** (optional, platform key by default).
- Per-sniper **share code** (import/export a sniper or strategy; imports copy
  current settings); "Legacy custom — pick a strategy to migrate".
- Sniper API: key generated from the Sniper page; "Allow starting" toggle (a
  key that can start snipers "deserves the care you give the wallet itself");
  create answers 201 even if the servers failed to start it; unknown fields are
  errors; key never accepted in the URL; a tweet-bound sniper can be created
  but not started by the key; "Reason (shown in the start-rejected error)".

### Twitter tasks (details)
Conditions: post by users, reply/quote (to specific users or post IDs, keyword
scope: replier text / original / both), retweet, follow (target users),
profile/bio change, community membership (ID; "each community check costs ~").
Match ALL/ANY. Extract CA from text (bio/posts/replies/quotes/retweets),
ignore @mentions, keyword→CA overrides, additional CAs (multi-buy), market-cap
filter ($1k–$1B, enforced by the OkBot worker at fire time), schedule
(auto-start / auto-pause daily), "Task resets to running after each execution",
test matching against a pasted tweet, per-task webhook key, Notify on complete,
groups. Chains: Solana (OkBot enhanced workers; "Slippage pinned to 100% for
SOL — we rely on Tip + Priority"), Base/ETH (WETH swap on the pool), BNB, Arc
(USDC is also gas), Robinhood Chain (USDG, ETH for gas; "Ponz launch sniper"
fires on every new Pons v1/v2 launch from the Robinhood webhook), Bankr
fee-claim trigger (BANKR_FEE_CLAIM webhook, recipient match, first-claim only,
min WETH). A task can instead "enable up to 3 of your sniper tasks" on match.
Sources: TweetCatcher, X API (TwitterAPI.io key), "1322" Standard and Ultimate
streams (tweets only, limited slots, per-handle start by approved users), 1322
FOMO feed, 1322 News feed (publishers chosen by admin, never triggers).

### Mayhem (details)
Campaign: max relaunches, profit goal, stop after first loss, iteration
cooldown, slippage bps, priority fee. Helpers: count, saved wallets, buy
range, buys per helper, gap range, sells by % after N buys / N seconds / a
milestone / a named signal rule (default `mayhemDumpDuringRun`, "exit if we
detect the mayhem operator dumping"), abort on drop %. Dev trade plan steps:
buy more, partial sell, final exit, mark milestone, wait for milestone,
interval between steps, halt follow-buys (diagnostic). Signal metrics:
mayhem-bot buys/sells/streaks/SOL in/out/net flow, organic buys and SOL sold,
price vs entry ×, seconds since launch. Dev wallet can be any saved sniper
wallet.

### Social / Catch / Vamp / Signals
Social: followed handles (posts, follows, profile and pinned-post changes),
staff see every tracked account, FOMO traders and coins (fomo.family), news
articles, "how long a tweet took to reach us". Catch: pump.fun launch feed with
search and "query modes", token page with socials. Vamp: paste a CA, it
launches a copy (image uploaded to IPFS). Signals: wallet activity, token
launch (creator / name), liquidity change, program instruction (optional
discriminator and required accounts), large transfer (optional counterparty,
min amount), whale SOL > 1,000; alert via Discord/Telegram, POST to webhook,
or fire a sniper; rule language on LaunchLab configs (fee schedules, vesting,
supply, LP split, migration target) with values like `fee.totalPercent`.

## Gated features — exact behaviour and payload (from the app's help texts and payload code)

Default gate table (`X0` in the bundle): level-unlocked — tasks/settings/catch/
updates ≥1, sniper/mayhem ≥4, advanced modes ≥4.1, stocks auto ≥4.1 or =4.2,
gucci =4.2/11.5/12.5, ansem ≥9, over ≥10, vamp ≥11 or =4.145, sniperApi open.
Per-user only (`usersOnly`): chain, social, sniperAutoDeploy, sniperPreconf,
sniperSModes, sniperLoadout, sniperPreexec, sniperAccountFilter,
socialUltimate, fomoTracking.

- **Hunt** `mode:"hunt"`, `hunt:{timeWindowMs(10000), triggerMode any|all,
  thresholds:{minTotalSol, minUniqueBuyers, maxUniqueBuyers, maxDevBuySol,
  minCpiBuyers, minLutMatches, minAccountMatches, requireKnownBot},
  knownBotPrograms[], knownBotWallets[], knownBotLuts[], knownBotAccounts[],
  useKnownBotDefaults}`. Registers every launch on the chosen venues and buys
  once the thresholds hold inside the window; dropped when the window closes.
- **Shapes** `mode:"shapes"`, `launchShape:{enabled, wrapped only|direct|any,
  wrapperPrograms, ignoreWrapperPrograms, newWrappersOnly, platformPayer,
  signal}`, `crowdBuys:{enabled, minSigners, minTotalQuote, platforms, signal,
  buy}`, `signalOnly`, `relaySignals`; wrapped-launch reader twin|shreds|off.
- **Web** `mode:"web"`, `web.tracking:{trackTransfers, trackConfigs,
  autoSubscribe, autoMigrationSnipe, maxDepth(3), unsubscribeAfterMs,
  fundingFilters:{matchMode, sol, usdc, usd1, usdt, custom[]},
  preload:{enabled, lookbackDays(28), maxTransactions(1000)},
  childTracking:{maxDepth(1), lookbackDays(1), maxTransactions(100)},
  creatorFilter:{enabled, minCreations, maxCreations,
  maxCreationsWhileSubscribed(1), timeoutMs(2000), allowOnError}}`.
- **Local pre-execution** `preexec:{enabled, padCu(700), padPercent,
  loadedAccounts(true), loadedAccountsPadPercent(10), complement,
  complementWaitMs(25, max 500), createWaitMs(5)}`. Replays create + buy in
  the sniper's VM; sends with a CU limit sized from consumption (ranks higher
  at the same fee); a buy that fails locally is not sent; SOL-quoted pump.fun
  only. Two waves: plain buy now + measured copy on the same nonce.
- **Account filter** `accountFilter:{whitelist[], whitelistMode all|any,
  blacklist[]}` — keys the launch tx must / must not name, ≤32 each,
  blacklist judged first.
- **Preconfs** `preconf:true` — registers dev / copied wallets on the hub's
  preconfirmation lanes; no-op with Monitor Program alone.
- **AutoDeploy** `mode:"autodeploy"`, `autoDeploy:{quoteMint, name, symbol,
  description, twitter, website, imageUrl, metadataUri, devBuy:{enabled,
  amount}}` — launches the coin itself from the chosen creator wallet.
- **Loadout** `mode:"loadout"`, `loadout:{entryDelaySecs, maxFeePercent,
  leadMs, buyProtectedAtOnce}` — protected launches: fee falls from ~100 % to
  the 5 % floor over 60 s; buy only, no auto-sell.
- **[A] mode** (level 9) `mode:"ansem"`, `fundingAge:{minFundingSol(0.5),
  maxFundingSol(10), fundingWindowMinutes(15), maxDevTxCount, onMismatch
  skip|log}` — first-ever funding of the deployer inside the band and window.
- **Chain / Social / Ultimate / FOMO** — pages and streams, not sniper fields.
