# Birdeye Data API — what we have and what it is for (6 Nov 2026)

Plan: **Starter**, 99 $/month, 15 requests/s (900/min per key), **8 M compute units (CU) a month**, overage 12 $ per
1 M CU. Key name on the server: `BIRDEYE_API_KEY` in `/etc/snipetech/env`. Wallet endpoints are beta and capped at
5 requests/s and 75/min whatever the plan.

Base URL `https://public-api.birdeye.so`; headers `X-API-KEY: <key>` and `x-chain: solana` on every request.

## Budget

8 M CU / 30 days ≈ **266 k CU a day**. At the costs below that is, per day, any one of: ~8 800 wallet PnL summaries,
~8 800 top-trader lists, ~22 000 token trade pages, ~6 600 1-minute OHLCV pages. Plenty for a daily wallet scan;
not for mirroring the tape (the tape is free and already live).

## Endpoints we will use, with CU per request

| purpose | endpoint | CU | notes |
|---|---|---|---|
| wallet 30-day gate, fast | `GET /wallet/v2/pnl/summary` | 30 | one wallet; Starter has it |
| wallet positions | `GET /wallet/v2/pnl` | ceil(10·n^0.8) | per-token PnL |
| wallet trade history | `GET /trader/txs/seek_by_time` | 12 | replaces RPC backfill for copy-rank |
| who traded a token best | `GET /defi/v2/tokens/top_traders` | 30 | discovery source |
| market-wide winners | `GET /trader/gainers-losers` | 30 | discovery source, by period |
| first buyers of a coin | `GET /token/v1/first-buyers` | 25 | who lands early (bundle vs organic) |
| new listings | `GET /defi/v2/tokens/new_listing` | 25 | cross-check of our launch feed |
| meme list | `GET /defi/v3/token/meme/list` | 30 | pump.fun coins with stats |
| post-graduation prices | `GET /defi/v3/ohlcv` | 25–100 | PumpSwap/Raydium leg of exits |
| token stats | `GET /defi/v3/token/market-data` | 10 | volume, liquidity, holders |
| token trades | `GET /defi/v3/token/txs` | 12 | only off-tape venues/time |
| holders | `GET /defi/v3/token/holder` | 30 | concentration filter |
| credits left | `GET /utils/v1/credits` | — | the operator checks this before a scan |

Not on Starter: the "multiple" variants (PnL multiple, net-worth multiple, holder batch, token meta/trading multiple)
and websockets. Each wallet is one call.

## Where it plugs in

1. `copy-rank` and `discover-wallets` get a `--source birdeye` path: candidates from top traders + gainers, the
   30-day gate from PnL summary, history from seek_by_time; the tape labs at +1/+6 stay the last two gates.
2. `doctor` gets a `birdeye` row (one `/defi/price` call, 3 CU).
3. The operator may start Birdeye-backed research jobs; it reads `/utils/v1/credits` first and stops a scan when
   the day's CU allowance (266 k) is spent.

Docs: https://data.birdeye.so/docs/llms.txt (index), quickstart, "What is Compute Unit Cost", "Data Accessibility
by Packages".
