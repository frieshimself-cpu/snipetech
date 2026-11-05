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
