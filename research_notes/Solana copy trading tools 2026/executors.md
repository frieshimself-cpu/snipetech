# Solana sniper / copy-trade executors with programmatic control (as of 6 Nov 2026)

Scope note: written for an existing OKTech Sniper-API user (modes `normal`/`hunt`/`ctbot`, per-sniper `priorityFee` + `tipAmount`, `extraParams.globalSendCount`, "Jetstream" `speedMode`). Everything dated before mid-2026 is flagged "(pre-mid-2026, possibly stale)". Vendor claims are labelled as such; the only non-vendor slot-level measurements I found are the ones in this repo's own `docs/live-recipe.md` (OKTech) and vendor case studies.

## KQ1. Per-executor profile: server-usable API, copy mode, fee knobs, fee/subscription, landing stack

### Takeaway
Only three hosted executors expose a documented server-side trading API: **OKTech** (full sniper/copy task CRUD with per-task priority fee, tip, send count, speed mode), **Bloom** (`/swap`, `/deploy`, `/wallets` REST with EU/US hosts; copy-trade *tasks* are UI/Manager only in the docs I could read), and **GMGN** (whitelist-only Agent/OpenAPI for data + swaps; copy tasks not exposed). Every other named product (Axiom, Photon, Trojan, Nova, Maestro, Banana Gun, Padre, TradeWiz, Pepeboost, Fasol, Soul Sniper, Odinbot, Subglow) is UI/Telegram-only or has only reverse-engineered community SDKs; BullX disabled trading on 1 June 2026.

### Cited Findings

**OKTech (oktech.me)**
- Public web: `https://oktech.me` served an empty page to a fetcher (JS app, no public docs indexed); no third-party coverage of "OKTech" or "Jetstream" exists in web search — [oktech.me fetch](https://oktech.me); [search "OKTech sniper Jetstream Solana"](https://dev.to/apollo_ag/i-sniped-a-solana-token-in-400ms-heres-the-full-tech-stack-3ocf) (returned only unrelated results).
- Sniper API base `https://oktech.me/api/sniper-api/v1`, Bearer key, endpoints `GET /status`, `GET /wallets`, `GET/POST /tasks`, `POST /tasks/{id}/start|stop`, `DELETE /tasks/{id}`; 429 with `Retry-After`; `externalId` dedupe; keys may lack "Allow starting" so tasks are created idle — local source `/home/user/snipetech/src/snipetech/oktech.py` and `/home/user/snipetech/src/snipetech/config.py`.
- Modes: `normal`, `mig`, `ctbot` (copy: `ctbotWallets [{address,minSol,maxSol,minMcapUsd,maxMcapUsd}]`, `ctbotMinWallets` within `ctbotTimeWindowMs`, retrigger cooldown, `ctbotCopyExit {sellThresholdPercent, sellPercent, triggerWalletOnly, delayMs}`, groups, `ctbotMigrationFollow`), `copydev`, `reverse`, `claimsniper`, `stonk`, plus `hunt` and `loadout` (approved accounts) — `/home/user/snipetech/docs/oktech-settings.md`.
- Fee knobs per task: `priorityFee` (min 0.001 SOL), `tipAmount` (min 0.002 SOL), `sellTipAmount`, `sellPriorityFeeSol`, `speedMode` ("Jetstream", ctbot only; normal is always Jetstream), `preconf` (preconfirmations, approved accounts), `preexec {padCu, padPercent, complement, complementWaitMs…}` (local pre-execution, approved), `extraParams {computeUnits, loadedAccountsDataSizeLimit, useJitoMemo, globalSendCount, globalSmartFees, addressLookupTables, seedAccount}`; the account's own "PROJECT SNIPE" task pays 0.25 SOL priority + 1.3 SOL tip per 15 SOL buy — `/home/user/snipetech/docs/oktech-settings.md`.
- Auto-sell: staged TP ladder (1–10 stages), timer sell, laddered stop-loss with `confirmMs`, trailing stop, `deadExit`, `autoStop`, `migrationSell`, `momentumHold`, `timerExtension`, `wickServer` — `/home/user/snipetech/docs/oktech-settings.md`.
- Tiers observed in the app bundle: VIP (updates feed, some tasks), Ultimate (speed stream, more Social handles), Sniper API keys (needs Sniper), plus admin-granted feature flags (Preconfs, [S] tab Hunt, Loadout, Local pre-execution, Sniper API); "Over" page lists licenses, fee wallets, servers — `/home/user/snipetech/docs/oktech-features.md`.
- No public price list or platform-fee percentage for OKTech was found anywhere (see Gaps).

**Axiom (axiom.trade)**
- Official fee tiers: Wood 0.95% (0.05% cashback), Bronze 0.90%, Silver 0.875%, Gold 0.85%, Platinum 0.825%, Diamond 0.80%, Champion 0.75% (0.25% cashback), cashback paid in SOL; volume thresholds not published — [docs.axiom.trade axiom-fees](https://docs.axiom.trade/getting-started/fees/axiom-fees.md).
- Official docs index has no copy-trading, API, priority-fee/bribe or speed page; wallet tracking pages describe a tracker that "shows what your added wallets are buying in real time" and do not mention automatic execution — [docs.axiom.trade llms.txt](https://docs.axiom.trade/llms.txt); [monitor-wallets](https://docs.axiom.trade/wallet-tracking/monitor-wallets.md).
- Only community reverse-engineered SDKs exist: `axiomtradeapi` (Python, token + Cloudflare-clearance cookie auth) and `axiomtrade-rs` (Rust, REST/WS across api2…api10.axiom.trade; no dedicated trade endpoint in the reference excerpt) — [PyPI axiomtradeapi](https://pypi.org/project/axiomtradeapi/1.1.5/); [axiomtrade-rs endpoints](https://vibheksoni.github.io/axiomtrade-rs/reference/endpoints.html).
- Third-party reviews conflict on whether Axiom has automated copy trading; MadeOnSol says "Axiom has it fully built in" while others say manual only — [MadeOnSol Photon vs Axiom](https://madeonsol.com/blog/photon-vs-axiom-solana-trading-terminal); [Axiom fee search summary](https://medium.com/@crypto-deploy/axiom-trade-fees-what-you-actually-pay-and-how-to-pay-less-0df290e57f7b).

**Photon (photon-sol.tinyastro.io)**
- Fee about 1% per swap; landing path not stated; no API — [Dysnix comparison, 5 Nov 2026](https://dysnix.com/blog/top-solana-sniper-bot).
- Copy trading: 2026 reviews say "Photon has no copy trading" / "no native copy trading", while a 2025 Reddit guide describes wallet tracker + Quick Buy (manual copy) — [MadeOnSol](https://madeonsol.com/blog/photon-vs-axiom-solana-trading-terminal); [uwuu review](https://uwuu.ai/blog/photon-review); [Reddit guide Apr 2025 (pre-mid-2026, possibly stale)](https://reddit.birdcat.cafe/r/SolanaSniperBots/comments/1jzqn80/how_to_make_passive_income_with_manually_copy).

**BullX / BullX NEO**
- "Trading on this version of the app will be disabled at 12:00 AM UTC on 1 June, 2026 … Withdraw and wallet functions will be accessible as usual … to make way for the future version of the app" (BullX notice quoted by a competitor) — [Moby shutdown guide](https://moby.win/learn/bullx-shutdown/); corroborated by [Phemex news](https://phemex.com/news/article/bullx-suspends-trading-functionality-to-focus-on-future-development-87410) and [Wu Blockchain item](https://news.jeremywhittaker.com/item/wublockchainenglish-24566/).
- Pre-shutdown fee ~1% flat, copy trading present — [Dysnix](https://dysnix.com/blog/top-solana-sniper-bot).

**Trojan (docs.trojan.com)**
- Fee "1% per successful trade"; SOL cashback rising with Arena rank (rates unspecified); priority fee = SOL to validators; bribe = SOL "used for MEV protection when it's enabled", routed via Jito/"trusted validators"; token-account fee ~0.002 SOL refundable — [Trojan FAQ](https://docs.trojan.com/faqs).
- Copy-trade settings: target address, execution wallet, buy as % of target or fixed SOL, buy priority fee, bribe (required with MEV protection), buy slippage, MEV toggle; Copy Sell ("sell the same % as they do"), Auto Sell (SL/TP limit orders); Copy Sell and Auto Sell each have their own priority fee, bribe, slippage, MEV settings; filters: market-cap range, min liquidity, max spend per buy, exclude launchpad (bonding-curve) tokens, prevent duplicate buys — [Trojan copy-trading docs](https://docs.trojan.com/terminal-overview/tools-and-widgets/copy-trading-bot).
- No public trading API or API-key system found — [search summary](https://www.quicknode.com/ko/builders-guide/tools/trojan-on-solana-by-trojan-team).

**Nova (tradeonnova.io)**
- 1% per trade, no subscription; Solana gas 0.0005–0.002 SOL, optional Jito tips 0.0003–0.0015 SOL; cashback up to 50% + 10% discount can take effective fee to ~0.5% (conditional); copy trading = "customizable allocation per wallet" + stop-loss; "Ultra V2" and "Demon" processors route via Jito bundles; private keys held server-side; no API — [MadeOnSol Nova review, updated 30 Sep 2026](https://madeonsol.com/blog/nova-bot-solana-review).
- The `nova-trade.gitbook.io` docs that search returns belong to a different product (peer-to-pool derivatives DEX on BSC/Arbitrum), not the Solana bot — [nova-trade llms.txt](https://nova-trade.gitbook.io/nova-trade/llms.txt).

**GMGN (gmgn.ai)**
- 1% handling fee per transaction; priority fee min 0.0001 SOL; priority+tip capped at 2 SOL; anti-MEV requires priority ≥ 0.002 SOL and tip ≥ 0.0001 SOL; tip "paid to private nodes", not collected by GMGN; anti-MEV modes Off ("to all nodes, very fast") / Reduced ("limited set of nodes, may be front-run") / Secure ("safe nodes, slightly slower, includes compensation"); custom RPC can be attached as a parallel submission path — [GMGN fees & settings](https://docs.gmgn.ai/index/gmgn-fees-settings.md).
- Copy trade: Fixed buy / Maximum follow-buy; follow-up selling proportional to target (only sells copy-bought tokens) or TP/SL (once enabled, target sells are not followed); batch TP/SL ladder; max 10 copy tasks; "Lightning Mode" speeds copying by ~2 s using unconfirmed data (may roll back / double-execute); failed trades retried 1–3 times, pauses after 3 failures — [GMGN copy-trade doc](https://docs.gmgn.ai/index/copy-trade-copy-smart-money-automatically-earn-sol.md); gitbook mirror adds Priority Fee 0.0002–0.0006 SOL, Tip Fee from 0.0001 SOL, Boost Mode Off/Red/Sec, Dev Sell, Migrated Sell, Trailing SL — [GMGN app tutorial](https://gmgn-app.gitbook.io/gmgn-app-tutorial/tutorial/copy-trade).
- Agent API / OpenAPI: data + "token swaps routed through optimal paths" with custom slippage; API key from locally generated keypair uploaded at gmgn.ai/ai; trading needs the private key; IPv4 only with IP whitelist; SOL/BSC/Base; no copy-trade endpoint, no rate limits or fees listed — [GMGN Agent API](https://docs.gmgn.ai/index/gmgn-agent-api.md); [Solana trading API page](https://docs.gmgn.ai/index/cooperation-api-integrate-gmgn-solana-trading-api.md). News: entered internal testing March 2026, Ed25519 auth, whitelist UIDs imported daily — [ChainCatcher](https://www.chaincatcher.com/en/article/2252662).

**Maestro (docs.maestrobots.com)**
- 1% on every successful buy/sell; on Solana "fees will be directly extracted in the same transaction"; fee address `MaestroUL88UBnZr3wfoN7hqmNWFi3ZYCGqZoJJHE36` — [Maestro monetization](https://docs.maestrobots.com/sniper/monetization.md).
- Premium $200/month: 12 copytrade wallets per chain (base 5) and the "Maestro Pro Bot … even faster speeds" — [Maestro premium](https://docs.maestrobots.com/sniper/premium-subscription.md).
- Copytrade: mempool detection + frontrun only on Ethereum/BSC; on Solana "after-mined detection … buys or sells in the next block and uses your gas settings" — [Maestro copytrade](https://docs.maestrobots.com/sniper/copytrade). Third-party: custodial (holds keys), "validator / Jito tip controls" via Telegram — [solanatools comparison](https://solanatools-io.pages.dev/trojan-vs-maestro.html). No API.

**Banana Gun (Solana docs)**
- Copy-trade settings: Max Buy, Buy Exact / Buy Percent / Buy Fixed, Copy Sells on/off, Sell Mode Relative/Absolute, Sell Exact / Sell Percent, Buy/Sell Anti-MEV (private tx, "slower"), Buy/Sell Mev-Tip (added to gas when Anti-MEV on), Buy/Sell Gas, slippage, tax limits, Buy Only Once (7-day block), Spending Limit, min/max market cap and token age, Min Trade Size, Min Quote Liquidity, blocked tokens, creator filter; no bribe field, no speed or API notes — [Banana Gun Solana copy trading](https://banana-gun.gitbook.io/banana-gun-solana/advanced-features/copy-trading.md); presets Normal/Fast/Insane with Anti-MEV + Mev Tip — [preset settings](https://banana-gun.gitbook.io/banana-gun-solana/trading/settings/preset-settings).
- Fee ~1% plus gas and "speed-enhancement tips" — [Dysnix](https://dysnix.com/blog/top-solana-sniper-bot); other pages list 0.5% (ETH manual) / 1% other chains — [MadeOnSol compare-bots (Sept 2026)](https://madeonsol.com/compare-bots).

**Bloom (docs.bloombot.app / dev.bloombot.app)**
- Fee "1% on every buy and sell"; 0.9% via referral (framed as 10% cashback) — [Bloom fees](https://docs.bloombot.app/grow-with-bloom/fees-structure.md).
- Official REST API: Solana hosts `https://eu.solana.bloombot.app/api/v1` and `https://us.solana.bloombot.app/api/v1` (shared rate budget, region = latency only), EVM `https://evm.bloombot.app/api/v1`; Bearer-token auth; `/swap` (buys/sells across one or more wallets, optional TP/SL/trailing/time/dev-sell auto-orders), `/deploy` (pump.fun, Bags, Bonk), `/wallets`; "builder fees" share for integrations; copy-trade task endpoints not documented; swap tip/priority/anti-MEV params not shown in the intro page — [dev.bloombot.app introduction](https://dev.bloombot.app/introduction); [API docs overview](https://docs.bloombot.app/api-docs/api-docs.md).
- Solana bot copy settings: Buy Exact / Buy % (>100% allowed) / Buy Fixed; Follow Sells (matching %), Sell On Transfer, Sell Only Copied Buys, Reverse Mode; Buy Tip / Sell Tip = Jito tip, Auto Tip = Jito-suggested tip with priority fee fixed; Buy Fee / Sell Fee = priority fee; Buy/Sell Protection (anti-MEV, "fastest processor is selected automatically"); filters incl. Min/Max Trigger Buy, MCap, token age, First Interaction, Max Dev Holdings, Skip Deploys, Max Buy Count, Max Per Token; **Max Slot Diff** ("how many slots of difference you'll accept from the target's transaction; if the gap is too large the trade doesn't execute") — [Bloom Solana copy](https://docs.bloombot.app/solana/solana-bot/copy.md); Manager version adds Processor Tip, Min Sell, Fixed Sell, deployer white/blacklists, enable/disable times — [Bloom Manager Solana copy-trade](https://docs.bloombot.app/manager/pages/tasks/solana/copy-trade.md).

**Padre (trade.padre.gg)**
- Official docs redirect to the app (`docs.padre.gg` → `trade.padre.gg`, 302) — [docs.padre.gg fetch](https://docs.padre.gg). Third-party: rebranded to "Terminal" after a Pump.fun acquisition, domains still resolve; MEV protection optional; copy feature described as "discovery-plus-clicks, not … sub-400ms mirroring"; 10% default SOL cashback, up to 35% via referral — [uwuu Padre review](https://uwuu.ai/blog/padre-terminal-review). Fee figures conflict (0.5% flat vs 1% with volume tiers 0.7/0.6/0.5%) across unaffiliated guides — [pumpfunguide (unaffiliated)](https://pumpfunguide.pages.dev/guides/what-is-padre-features-fees-cashback-2025); [MadeOnSol compare](https://madeonsol.com/compare/banana-gun-vs-padre). No API found.

**TradeWiz (tradewizbot.pro)**
- Flat 1% per buy/sell + 0.002 SOL token-account fee; Jito tips pass-through when Anti-MEV on; team markets ~1.1 s average execution and "some same-block fills" (self-reported, unaudited); Sept 2026 refresh added a copy-trading web app + Chrome extension; no API — [MadeOnSol TradeWiz review, updated 30 Sep 2026](https://madeonsol.com/blog/tradewiz-review-solana-copy-trading-bot).

**Pepeboost, Fasol, Soul Sniper**
- Pepeboost: 1% on successful swaps, up to 30% cashback by volume, 0.9% via referral, copy trading listed; "publishes no official pricing page" — [Comparedge Pepeboost](https://comparedge.com/tools/pepeboost); [Comparedge pricing](https://comparedge.com/tools/pepeboost/pricing). No official docs or API found — [search](https://medium.com/@pixcrypt/how-to-use-pepeboost-a-guide-to-the-fastest-pumpfun-trading-bot-202e1dafad91) (2024, pre-mid-2026, possibly stale).
- Fasol: "free, non-custodial Solana trading bot, reachable through Telegram or a web app called Fasol Terminal"; Soul Sniper: Telegram sniper with token risk scanner, freemium — [MadeOnSol Fasol alternatives](https://madeonsol.com/alternatives/fasol); [MadeOnSol Soul Sniper](https://madeonsol.com/alternatives/soul-sniper). No copy-trade settings, fee schedule, or API documentation found for either.

**2026 entrants / specialists**
- **Odinbot** (web app, Solana only): "We also only charge a 1% fee (thats it)"; "Up to 65% of trades landing in 0-block" (vendor claim; public dashboard speed.odinbot.io loads only a title for fetchers); 40% affiliate; 50% default slippage on low-liquidity pools; venues Pump.fun/PumpSwap, Raydium V4/CPMM/LaunchLab, Meteora DBC/DAMM, Jupiter v6, OKX router, LetsBonk; no API, infra not stated — [odinbot.io](https://www.odinbot.io/); [speed.odinbot.io](https://speed.odinbot.io). Earlier (Apr 2025, pre-mid-2026) Decrypt piece quoted 15% same-block and "first to execute in 62% of trades" in a self-run comparison, with "predictive trading" before confirmation — [Decrypt](https://decrypt.co/314700/how-solana-copy-trading-platform-odinbot-is-achieving-zero-block-latency).
- **Subglow** (web dashboard + own gRPC infra): Starter free 1.0% / 1 wallet; Trader 0.5 SOL/mo 0.5% / 5 wallets; Pro 1.5 SOL/mo 0.25% / 25 wallets (footer says "0.2% flat", conflicting); detection via own gRPC reading the validator shred stream, Jito bundles + direct-to-validator send in parallel, Jupiter routing; "~83 ms" detection→send benchmark (50 ms detect, <1 ms filter, 20 ms quote, 12 ms sign+send); copy sell proportional (Trader+), manual 25/50/100%, TP/SL/trailing; no copy-bot API (gRPC/JSON-RPC keys sold separately from $99/mo) — [subglow.io copy-trader](https://subglow.io/tools/copy-trader).
- **Moby**: 0.75% swap fee with $0.90 minimum, up to 30% fee back in $MOBY; copy trading and API not mentioned — [Moby](https://moby.win/learn/bullx-shutdown/).
- **REKT Bot**, **FrogLabs Terminal** appear in MadeOnSol's Nov 2026 top-10 (REKT: channel sniping + copy trading + Chrome extension) with no fee/API details — [MadeOnSol best trading bots, 5 Nov 2026](https://madeonsol.com/best/trading-bots).

### Inferences
- OKTech is the only hosted product in this set whose documented API lets a server create and manage *copy* tasks with per-task fee, tip, send-count and routing knobs; Bloom's public API is a swap/deploy API (your server would have to do the detection and call `/swap`), and GMGN's is swap-only behind a whitelist.
- Every Telegram bot (Trojan, Banana Gun, Maestro, Nova, TradeWiz, Pepeboost) and every web terminal (Axiom, Photon, Padre) would need UI automation to drive from a server; the Axiom community SDKs depend on Cloudflare cookies and are fragile.

### Gaps
- OKTech: no public pricing, platform-fee %, infrastructure description (validators, SWQoS stake, Jito/Nozomi use) or Jetstream explanation exists outside the app; the only knowledge is this repo's notes from the app bundle.
- Bloom: the `/swap` parameter list (tip, priority fee, anti-MEV, processor) and whether copy tasks can be created via API were not visible in the pages fetched (dev.bloombot.app swap reference not retrieved).
- Axiom, Photon, Padre, Nova, Pepeboost, Fasol, Soul Sniper: no official copy-trade setting pages were reachable; fees for Padre/Pepeboost rest on third-party sites.

## KQ2. Which executors let you set a Jito tip and priority fee per copy, and send via staked connection / Jito bundle, from an API?

### Takeaway
From an API: OKTech only (per-task `priorityFee` + `tipAmount`, `sellTipAmount`/`sellPriorityFeeSol`, `useJitoMemo`, `globalSendCount`, `speedMode`); Bloom's `/swap` is an API but copy logic stays client-side and its tip/priority parameters were not confirmed in the fetched docs. From a UI: Trojan, Banana Gun, Bloom, GMGN all expose separate buy- and sell-side priority fee and tip/bribe per copy config; Maestro and Nova expose tips less granularly. No vendor documents a user-selectable staked-connection vs Jito route except GMGN's Off/Reduced/Secure node modes and Bloom's "processor" selection.

### Cited Findings
- OKTech ctbot task: `priorityFee`, `tipAmount`, `sellTipAmount`, `sellPriorityFeeSol`, `speedMode` (Jetstream), `extraParams.globalSendCount` ("OKTech sends every tx N times"), `useJitoMemo`, `globalSmartFees`, `preconf`, `preexec` — all set in the JSON body of `POST /tasks` — `/home/user/snipetech/src/snipetech/oktech.py` (lines ~233–295), `/home/user/snipetech/docs/oktech-settings.md`.
- Trojan: buy priority fee + bribe + MEV toggle per copy; Copy Sell and Auto Sell each with own priority fee, bribe, slippage, MEV — UI only — [Trojan copy docs](https://docs.trojan.com/terminal-overview/tools-and-widgets/copy-trading-bot).
- Banana Gun: Buy/Sell Gas, Buy/Sell Mev-Tip, Buy/Sell Anti-MEV per copy — UI only — [Banana Gun copy docs](https://banana-gun.gitbook.io/banana-gun-solana/advanced-features/copy-trading.md).
- Bloom: Buy Tip / Sell Tip (Jito), Auto Tip, Buy Fee / Sell Fee (priority), Buy/Sell Protection, "fastest processor is selected automatically", Max Slot Diff — UI/Manager; API `/swap` exists with EU/US hosts — [Bloom Solana copy](https://docs.bloombot.app/solana/solana-bot/copy.md); [dev.bloombot.app](https://dev.bloombot.app/introduction).
- GMGN: priority fee (≥0.0001; ≥0.002 with anti-MEV), tip fee (≥0.0001, "to private nodes"), send modes Off/Reduced/Secure, optional custom RPC as parallel path; copy tasks UI only; Agent API = swaps only — [GMGN fees](https://docs.gmgn.ai/index/gmgn-fees-settings.md); [Agent API](https://docs.gmgn.ai/index/gmgn-agent-api.md).
- Maestro: Solana copies execute "in the next block and use your gas settings for that wallet" (global gas, not per copy in the doc) — [Maestro copytrade](https://docs.maestrobots.com/sniper/copytrade).
- Subglow: Jito bundles plus direct-to-validator in parallel; user-tunable tip not described; no copy API — [Subglow](https://subglow.io/tools/copy-trader).
- Landing-path vocabulary from infra vendors (for reading the above): Helius Sender Max submits "to all pathways (Helius, Jito, Harmonic, Rakurai, etc.) simultaneously", min tip 0.001 SOL; SWQOS-only tier min 0.000005 SOL; regional endpoints slc/ewr/lon/fra/ams/sg/tyo; 50 rps/region with key — [Helius sending-transactions](https://www.helius.dev/docs/sending-transactions). RPC Fast's case-study bot sent in parallel via Helius Sender (primary), bloXroute (paid secondary), QuickNode Fastlane (free secondary) — [RPC Fast case study](https://rpcfast.com/blog/copy-trading-bot-case-study).
- Dysnix (5 Nov 2026) on the retail bots: "none ship with bare-metal nodes or staked SWQoS by default" and none publishes landing times under fixed conditions — [Dysnix](https://dysnix.com/blog/top-solana-sniper-bot).

### Inferences
- Nothing in the hosted market exposes *more* per-copy fee control via API than OKTech already does; the alternatives' advantage, if any, is in UI granularity (per-side tip/priority on sells in Trojan/Banana/Bloom, which OKTech also has via `sellTipAmount`/`sellPriorityFeeSol`) or in route choice (GMGN node modes, Bloom processor selection).
- A "send via staked connection" choice is not a user-facing knob anywhere; it is implied by the vendor's backend (Helius Sender-style multi-path). OKTech's `globalSendCount` duplicate sends are the closest explicit equivalent, and this repo observed its duplicate sends losing races ("`6023 NotEnoughTokensToSell` next to each landed sell is OKTech's duplicate send losing the race") — `/home/user/snipetech/docs/live-recipe.md`.

### Gaps
- Bloom `/swap` request schema (does it accept tip, priority fee, protection, processor?) was not retrieved.
- No vendor states whether its copy path uses Jito bundles vs SWQoS vs Nozomi/0slot; only GMGN's "private nodes" and Bloom's "Jito tip … Jito blocks" wording hint at Jito-first routing.

## KQ3. Measured evidence of copy latency relative to the leader

### Takeaway
There is no independent, published slot-level benchmark of any hosted copy bot. The only measurements are (a) this repo's own OKTech audit — six of seven copy fills landed in the leader's own slot, the seventh in the next, yet 2–4 trades behind because the intra-slot order is a fee auction — and (b) vendor claims: Odinbot "up to 65% 0-block", RPC Fast client "same slot or +1", Subglow "~170 ms p50 / ~440 ms p99" pipeline.

### Cited Findings
- OKTech, measured by this repo (6 Nov 2026): seven live copy fills "every one a bonding-curve buy that landed in the leader's own slot or the next one — the speed is there — and still 2–4 trades behind the leader"; fills 18–43% above the leader's price; per-fill reading of the transactions ahead shows the leader's own bundle wallets (same fee, adjacent), then bots paying 0.01–0.3 SOL priority (one paid 0.200 SOL to sit directly behind the leader), then OKTech at minimum fees; "we were in the leader's slot six times out of seven — OKTech's speed is not the problem. The order inside the slot is a fee auction" — `/home/user/snipetech/docs/live-recipe.md` ("Copy-landing test, 6 Nov" and "Inside the slot"), summarized in `/home/user/snipetech/docs/roadmap.md`.
- OKTech launch snipes (hunt mode): "we landed 1–2 slots after the create with 2–7 buys in front"; when the trigger buy sits inside the create bundle "nobody can land there: OKTech lands at slot +1/+2, after the whole bundle" — `/home/user/snipetech/docs/live-recipe.md`.
- OKTech ctbot "fires on the leader's *attempt*, even when it fails on chain" (measured) — `/home/user/snipetech/docs/oktech-settings.md`.
- Odinbot: "Up to 65% of trades landing in 0-block" (vendor, 2026) — [odinbot.io](https://www.odinbot.io/); Apr 2025: 15% same-block, first in 62% of a self-run 4-bot comparison (pre-mid-2026) — [Decrypt](https://decrypt.co/314700/how-solana-copy-trading-platform-odinbot-is-achieving-zero-block-latency).
- RPC Fast client bot (published 7 Feb 2026, updated 29 Oct 2026): lands "same slot or the +1 slot immediately following the target wallet", "in most cases, landed +1"; landing latency avg 200–300 ms, best 15 ms; Yellowstone gRPC + Jito-compatible shred stream, Frankfurt co-location; no % split; figures from vendor Grafana screenshots — [RPC Fast case study](https://rpcfast.com/blog/copy-trading-bot-case-study).
- RPC Fast playbook (15 Jul 2026): Yellowstone gRPC detection 10–40 ms, gRPC+ShredStream 5–20 ms, detect-to-submit budget <60 ms, typical end-to-end 80–250 ms; illustrative slippage vs leader ~1% at 60 ms, ≥18% at 800 ms — [RPC Fast playbook](https://rpcfast.com/blog/how-to-build-a-solana-copy-trading-bot).
- Subglow latency budget (20 Apr 2026, pre-mid-2026): one week of Pump.fun traffic, p50/p99: slot→Geyser 3/8 ms, Geyser→client 30/80 ms, parse <1/5 ms, strategy 5/25 ms, browser sign 80/180 ms, bundle submit 50/140 ms, total ~170/~440 ms; "if you miss [one slot] you will land in block N+1 at the earliest" — [Subglow blog](https://subglow.io/blog/solana-copy-trading-latency-budget); product page: "200–400 ms from on-chain landing to our confirmed fill" — [Subglow use case](https://subglow.io/use-cases/copy-trading).
- Maestro documents Solana copies as next-block ("after-mined detection … in the next block") — [Maestro copytrade](https://docs.maestrobots.com/sniper/copytrade). GMGN "Lightning Mode" claims ~2 s faster using unconfirmed data — [GMGN copy](https://docs.gmgn.ai/index/copy-trade-copy-smart-money-automatically-earn-sol.md). TradeWiz ~1.1 s average, "some same-block fills" (self-reported) — [MadeOnSol](https://madeonsol.com/blog/tradewiz-review-solana-copy-trading-bot).
- Dysnix (5 Nov 2026): none of Trojan/GMGN/Bloom/Photon/BullX/Banana/BONK/Axiom publishes fixed-condition landing times — [Dysnix](https://dysnix.com/blog/top-solana-sniper-bot).
- Reddit builder (May 2025, pre-mid-2026): copies land "on the same one, or the one right after" depending on fees/conditions — [r/solana thread](https://redlib.hackliberty.org/r/solana/comments/1kihyyy/built_a_solana_copy_trading_bot_that_executes_in).

### Inferences
- Measured OKTech performance (same slot ~86% of the sample, +1 otherwise) already matches or beats every vendor claim found (Odinbot ≤65% same-block, RPC Fast "mostly +1", Maestro next-block, GMGN/TradeWiz ~1–2 s). Switching executors is unlikely to move the slot; moving *inside* the slot requires paying the fee auction (0.01–0.3 SOL observed) or being in the leader's bundle.
- "Same slot" is necessary but not sufficient: position within the slot is decided by priority fee/tip, which is exactly the knob OKTech exposes per task.

### Gaps
- No third-party dataset compares hosted bots' copy fills against leader slots; no tx-level evidence was found for Trojan, GMGN, Axiom, Nova, Banana Gun, Bloom or Padre copies.
- Odinbot's live dashboard could not be read (JS-only); its 65% figure and methodology are unverified.

## KQ4. Fees: platform fee per trade, monthly cost, minimum balances

### Takeaway
The market has converged on 1% per trade with cashback/referral rebates (Trojan, GMGN, Bloom, Maestro, Banana Gun, Nova, TradeWiz, Pepeboost, Odinbot, Photon); the cheapest documented rates are Subglow Pro 0.25% at 1.5 SOL/month, Moby 0.75% ($0.90 min), and Axiom's 0.75–0.95% tiers. Maestro is the only one with a published subscription ($200/mo Premium). OKTech's fee/price is not public.

### Cited Findings
- Axiom 0.95% → 0.75% by tier, SOL cashback 0.05–0.25% — [Axiom fees](https://docs.axiom.trade/getting-started/fees/axiom-fees.md).
- Trojan 1% per successful trade; failed trades free; ~0.002 SOL token-account fee refundable — [Trojan FAQ](https://docs.trojan.com/faqs).
- GMGN 1% handling fee; priority+tip cap 2 SOL; anti-MEV needs ≥0.002 SOL priority — [GMGN fees](https://docs.gmgn.ai/index/gmgn-fees-settings.md).
- Bloom 1% each buy and sell; 0.9% with referral — [Bloom fees](https://docs.bloombot.app/grow-with-bloom/fees-structure.md).
- Maestro 1% every buy/sell (Solana: extracted in the same tx); Premium $200/month — [Maestro monetization](https://docs.maestrobots.com/sniper/monetization.md); [Maestro premium](https://docs.maestrobots.com/sniper/premium-subscription.md).
- Banana Gun ~1% + gas + tips (Dysnix) vs 0.5% listed in MadeOnSol's calculator — [Dysnix](https://dysnix.com/blog/top-solana-sniper-bot); [MadeOnSol compare-bots](https://madeonsol.com/compare-bots) (conflict).
- Nova 1%, cashback to ~0.5% effective (conditional) — [MadeOnSol Nova](https://madeonsol.com/blog/nova-bot-solana-review).
- TradeWiz 1% + 0.002 SOL token-account fee — [MadeOnSol TradeWiz](https://madeonsol.com/blog/tradewiz-review-solana-copy-trading-bot).
- Pepeboost 1%, up to 30% cashback, 0.9% referral (third-party; no official pricing page) — [Comparedge](https://comparedge.com/tools/pepeboost/pricing).
- Odinbot 1% — [odinbot.io](https://www.odinbot.io/). Subglow 1.0% / 0.5% / 0.25% at free / 0.5 SOL / 1.5 SOL per month — [Subglow](https://subglow.io/tools/copy-trader). Moby 0.75%, $0.90 minimum per swap — [Moby](https://moby.win/learn/bullx-shutdown/).
- Photon ~1% — [Dysnix](https://dysnix.com/blog/top-solana-sniper-bot). Padre 0.5% vs 1% (conflicting third-party) — [MadeOnSol compare](https://madeonsol.com/compare/banana-gun-vs-padre); [pumpfunguide](https://pumpfunguide.pages.dev/guides/what-is-padre-features-fees-cashback-2025).
- OKTech minimums per buy: `priorityFee` ≥ 0.001 SOL, `tipAmount` ≥ 0.002 SOL, `buyAmount` 0.001–100 SOL; measured fixed cost ≈ 0.007 SOL per round trip — `/home/user/snipetech/docs/oktech-settings.md`.

### Inferences
- Platform fee is not a differentiator among the mainstream executors; the real cost lever for a copy strategy is the priority/tip auction inside the slot, which dwarfs 1% on small fills (0.2 SOL priority observed on a 4.9 SOL buy ≈ 4%).

### Gaps
- No minimum-balance requirements were published by any vendor except Subglow's wallet-count tiers and Maestro's $200/mo; OKTech pricing/tier costs are unknown publicly.

## KQ5. Copy-exit when leader sells, partial exits, and bundling the copy with a backrun

### Takeaway
Copy-exit on leader sell is standard (OKTech `ctbotCopyExit`, Trojan Copy Sell, Banana Gun Copy Sells, Bloom Follow Sells, GMGN automatic follow-up selling, Subglow copy sell); partial/laddered exits exist in OKTech, GMGN, Bloom and Trojan (Auto Sell). No executor documents bundling a copy with a backrun; the nearest features are OKTech `reverse` mode, Bloom "Reverse Mode" and GMGN TP/SL that *stops* following the leader once enabled.

### Cited Findings
- OKTech: `ctbotCopyExit {enabled, sellThresholdPercent, sellPercent, triggerWalletOnly, delayMs}`; auto-sell stages 1–10 with `gainPercent`/`sellPercent`, laddered stop-loss, trailing SL, dead exit, migration sell, momentum hold, timer extension; `reverse` mode acts on one wallet's trade with a prebuilt tx; this repo's live run: "OKTech's `ctbotCopyExit` was on, but the leader sold before our [exit landed]" — `/home/user/snipetech/docs/oktech-settings.md`; `/home/user/snipetech/docs/roadmap.md`.
- Trojan: Copy Sell "sell the same % as they do"; Auto Sell SL/TP limit orders with sell amount; each with own fees — [Trojan copy docs](https://docs.trojan.com/terminal-overview/tools-and-widgets/copy-trading-bot).
- Banana Gun: Copy Sells on/off, Sell Mode Relative/Absolute, Sell Exact / Sell Percent, limit orders — [Banana Gun copy docs](https://banana-gun.gitbook.io/banana-gun-solana/advanced-features/copy-trading.md).
- Bloom: Follow Sells (matching %), Min Sell threshold, Fixed Sell, Sell On Transfer, Sell Only Copied Buys, Reverse Mode (buy when target sells, with Reverse Mode Min Sell), copy limit orders — [Bloom Solana copy](https://docs.bloombot.app/solana/solana-bot/copy.md); [Bloom Manager copy](https://docs.bloombot.app/manager/pages/tasks/solana/copy-trade.md).
- GMGN: automatic follow-up selling proportional to target's reduction (only copy-bought tokens); batch TP/SL ladder (X% per rule, recalculated on add); "once [TP/SL] enabled, the target's sells are not followed"; gitbook mirror lists Dev Sell, Migrated Sell, Trailing SL — [GMGN copy](https://docs.gmgn.ai/index/copy-trade-copy-smart-money-automatically-earn-sol.md); [GMGN app tutorial](https://gmgn-app.gitbook.io/gmgn-app-tutorial/tutorial/copy-trade).
- Subglow: copy sell mirrors proportional exit (Trader+), manual 25/50/100%, TP/SL/trailing — [Subglow](https://subglow.io/tools/copy-trader).
- Maestro: copies target sells next block (Solana); frontrun option only on ETH/BSC mempool — [Maestro copytrade](https://docs.maestrobots.com/sniper/copytrade).
- Odinbot: copy sell not documented; testimonial mentions "no tip on sell" — [odinbot.io](https://www.odinbot.io/).
- Backrun bundling: no vendor page mentions bundling a copy with a backrun; Dysnix notes none of the retail bots ships staked SWQoS or bare-metal by default — [Dysnix](https://dysnix.com/blog/top-solana-sniper-bot).

### Inferences
- The copy-exit race is lost on the same fee auction as the entry (this repo's leader-sold-first observation); OKTech's `sellTipAmount`/`sellPriorityFeeSol` and Trojan/Banana/Bloom per-side sell fees are the only levers, and none is unique to an alternative.
- A backrun-bundled copy (leader tx + own buy in one Jito bundle) would require seeing the leader's tx pre-inclusion (ShredStream/mempool-like feed) and is only feasible self-hosted; no hosted executor sells it.

### Gaps
- Whether any hosted bot bundles its copy with the leader's transaction (Jito backrun) is undocumented everywhere.

## KQ6. Self-hosting: best-maintained 2026 open-source pump.fun copy/sniper bots and assumed infrastructure

### Takeaway
GitHub's "copy trading bot" space is dominated by low-star, Telegram-advertised Rust/TS repos; the largest (coffellas-cto, 416 stars, last push Apr 2026) is a promo README. The genuinely maintained 2026 ones with real infra are modest (justFiveDev Rust, 52 stars, Oct 2026, Helius `transactionSubscribe` + Jito; crypdev007 pumpfun-sniper, 62 stars, Oct 2026, ShredStream + gRPC "0 block"; khalido6222, Nozomi/0slot/bloXroute/Jito/gRPC/shred). All assume a co-located Yellowstone gRPC (ideally + Jito ShredStream) and multi-sender submission; vendor case studies put that stack at same-slot/+1.

### Cited Findings
- GitHub search (6 Nov 2026) "solana copy trading" sorted by stars: coffellas-cto/Solana-Copy-Trading-Bot 416★ (pushed 17 Apr 2026, README advertises a Telegram handle; "Shred stream, pump fun, raydium cpmm, launchpad, pumpswap"); mortdeus 63★ (Sep 2025); leionion/solana-bot-bump 60★ (Mar 2026); justFiveDev/Solana-Copy-Trading-Bot 52★ (pushed 25 Oct 2026); imcrazysteven 43★ (Sep 2025); danbayk/solana-copytrade 34★ (gRPC + Jito-Solana, 2024); keidev-sol/Solana-Copy-Trading-Bot-Rust 20★ ("zero-block execution", Nov 2025); bigmacman1129 20★ (Jun 2026); avenoirrr/echo-sol 4★ (Geyser gRPC, 10+ DEX, Jul 2026) — `gh search repos` output in this session (GitHub API).
- justFiveDev README: Rust; WebSocket `transactionSubscribe` (Helius-style) at `processed` commitment; filters Raydium AMM / Pump.fun; optional Jito bundle + tip; `accountExclude` for Jupiter noise; example target/copy tx pair on Solscan — [raw README](https://raw.githubusercontent.com/justFiveDev/Solana-Copy-Trading-Bot/main/README.md).
- Pump.fun sniper search by stars: bigmacman1129/solana-rust-sniper-bot 146★ (Aug 2026); vvizardev 81★ (Jan 2026); bitman09 77★ (May 2026); soldrift 69★ (Feb 2026); crypdev007/pumpfun-sniper 62★ (pushed 17 Oct 2026, "Jito shredstream + GRPC pumpfun sniper, 0 block sniping in stable, first buyer with 70~80% win rate"); LaChance-Lab/0slot-solana-pumpfun-sniper 7★ (ShredStream listener, two Solscan create/buy pairs shown as same-block first-buyer evidence) — GitHub search this session; [LaChance README](https://raw.githubusercontent.com/LaChance-Lab/0slot-solana-pumpfun-sniper/main/README.md).
- khalido6222/Pumpfun-Sniper (1★, Oct 2026): "integrates with nozomi, zeroslot, bloxroute, jito, yellowstone gRPC, and shred stream … no standard RPC required" — GitHub search description this session; mirrored at [DeepWiki coffellas-cto](https://deepwiki.com/coffellas-cto/Solana-Copy-Trading-Bot) (parallel dispatch to Jito, Nozomi, BloxRoute, ZeroSlot).
- Recently updated (not starred) 2026 repos: TierZeroDevStudio/solana-copy-trading-bot-rust (Rust policy + paper engine for Pump.fun/PumpSwap, Aug 2026); italo-afr/copy-trade-bot-solana (Pump.fun/PumpSwap/Jupiter/Jito with dashboard, Jun 2026); Yuval-Gerber/HYPE (Python, Jupiter, TP/SL, Sep 2026) — GitHub search this session.
- Reference tutorials: QuickNode "Create a Solana Copy Trading Bot Using Pump.fun" — [QuickNode guide](https://www.quicknode.com/guides/solana-development/defi/pump-fun-copy-trade); Bitquery gRPC copy-bot example — [Bitquery docs](https://docs.bitquery.io/docs/grpc/solana/examples/grpc-copy-trading-bot/).
- Infra assumptions from vendors: RPC Fast — Yellowstone gRPC with server-side filters "production standard", +Jito ShredStream fastest (5–20 ms detect), SWQoS-staked submission, multi-relay multi-region, pre-signed templates, bare-metal co-located nodes — [RPC Fast playbook](https://rpcfast.com/blog/how-to-build-a-solana-copy-trading-bot); case-study bot: dedicated Frankfurt node, gRPC + shred stream, Helius Sender + bloXroute + QuickNode Fastlane in parallel, 10+ TPS sustained — [RPC Fast case study](https://rpcfast.com/blog/copy-trading-bot-case-study). Helius Sender Max: all pathways (Helius, Jito, Harmonic, Rakurai…) simultaneously, 0.001 SOL min tip, regional endpoints — [Helius](https://www.helius.dev/docs/sending-transactions). Arct Cloud (vendor): 0.046 ms median to Jito, <0.13 ms to Temporal/Nozomi from Amsterdam — [Arct](https://www.arct.cloud/blog/solana-trading-vps). Shreder: sub-100 ms shred streaming via Geyser-gRPC interface (vendor) — [MadeOnSol](https://madeonsol.com/compare/shreder-vs-trojan).

### Inferences
- No open-source copy bot in 2026 is both popular and clearly maintained; the high-star repos are marketing fronts for closed Telegram services. A self-hosted path means building on the gRPC/ShredStream + multi-sender pattern yourself (which this repo's LaserStream tape recorder already half-covers) rather than adopting a repo wholesale.
- The self-hosted stack's documented ceiling (same slot / +1, 15–300 ms landing) equals what OKTech already measured; its advantage over OKTech is control of intra-slot priority (tip sizing per fill, backrun bundles, pre-signed templates), not slot latency.

### Gaps
- GitHub API star/README reads for coffellas-cto, crypdev007, khalido6222 and bigmacman1129 were blocked (403 in this session; raw README 404 on main/master), so their default branch, code quality and real stack are unverified beyond descriptions.
- No independent benchmark of any open-source bot's same-slot rate exists; the LaChance and justFiveDev Solscan examples are single cherry-picked pairs.
