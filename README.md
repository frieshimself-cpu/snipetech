# snipetech

**Decide *what* to snipe and *who* to copy on pump.fun — from on-chain evidence, not vibes.**

snipetech replays real pump.fun history with *your* bot's settings — landing latency, size,
priority fee + tip, bot fee, exit rules — and tells you, per deployer and per wallet, whether the
trade makes money **for you**:

- **Which dev wallets are profitable to snipe:** how often they rinse snipers (dev + bundle wallets
  dump within seconds), the outcome mix of their launches, your expected PnL per snipe with a
  confidence bound, the exit rules that would have worked (validated on launches they were *not*
  tuned on), the size to use, and how fast you must land. Verdict: `SNIPE` / `MARGINAL` / `AVOID`.
- **What it costs to get in before the other snipers:** who snipes each deployer's launches and
  exactly what they pay (priority fee + tips, read from their transactions), the bribe needed to
  land at each position, and a bribe sweep with your EV *after* paying each level.
- **What other snipers are doing:** `snipetech sniper <wallet>` reconstructs a competitor's setup —
  exact buy size, compute-unit price/limit, tips and which landing service gets them, bot fees,
  router program, slippage caps, transaction template — plus its entry timing, take-profit /
  stop-loss / time exits, dev-sell reactions, launch filters, related wallets and real PnL.
- **Copytrading:** a wallet's PnL is not your PnL. Every position it took is replayed with you landing
  N slots behind it, behind the other copiers, paying fees on every hop, and exiting after it does.
  You get your EV, the latency at which copying stops working, crowding and copier-farming
  detection, and a projected SOL/month. Verdict: `COPY` / `COPY_FILTERED` / `MARGINAL` / `AVOID`.
- **Strategy research:** search ~300 exit rules, sizes and latencies; run a walk-forward backtest of
  snipe/skip rules (deployer history plus what a launch shows when you land: dev buy, same-slot
  bundle wallets, mayhem mode) over every indexed launch; find repeat deployers and profitable copy
  candidates.
- **Your own wallet:** a post-mortem of where your sniper lost money (by deployer, outcome, entry
  delay), plus calibration of your real landing latency so every simulation matches your bot.
- **Live:** an indexer + signal stream for new launches and copy-target trades, an HTTP API and a
  rules file for your sniper's hot path, and a **Claude agent** that runs all of this for you in plain
  English.

## How it works

1. **Data.** Standard Solana RPC (any provider). pump.fun events are decoded from transactions with
   the official IDLs (vendored from `pump-fun/pump-public-docs`), including 2026 additions: v2/v3
   trades, fees kept on the curve, synthetic migration, mayhem mode, holder-reward coins, and
   PumpSwap's signed `virtual_quote_reserves`. Every semantic is pinned by tests on real mainnet
   transactions: curve reserves are post-trade, PumpSwap reserves are pre-trade, and the wallet
   pays exactly `quote + fee` (plus tips/rent).
2. **Counterfactual fills.** For a recorded state with reserves `(Q, B)` and `k = Q·B`, holding `h`
   tokens means the pool has `h` fewer, so selling them returns `Q·h/(B−h)`. This prices your buys
   and sells exactly through the curve, *including your own price impact*. An immediate round trip
   costs only the fees. It stays valid when reserves jump for reasons other than trades (mayhem
   agent, PumpSwap boosts) and across migration.
3. **Landing model.** Your buy lands `entry_slot_offset` slots after the create (or after the copied
   wallet) at the `start`/`middle`/`end` of that slot's trades. Every exit trigger (take-profit,
   stop-loss, trailing, "dev sold", time) executes `exit_latency_slots` later, at whatever the price
   is *then*. That is why a −35% stop fills at −39% when insiders dump.
4. **Real costs.** The on-chain fee rate in effect at that moment (pump.fun fees are tiered by
   market cap), your platform fee, and priority fee + tip on every transaction.
5. **Honest statistics.** Verdicts use a one-sided bootstrap lower bound of the mean, not the mean.
   Optimizers choose on older launches and report performance on newer, unseen ones (walk-forward).
   Warnings fire for small samples, a single launch carrying all the profit, behaviour changes, and
   being too slow for a deployer whose insiders sell before you land.

## Install

```bash
pip install -e .            # Python 3.11+
snipetech init              # writes snipetech.toml (see snipetech.example.toml for annotations)
export HELIUS_API_KEY=...   # or SNIPETECH_RPC_URL=https://...  (public RPC works but is slow)
export ANTHROPIC_API_KEY=...  # only for `snipetech agent`
snipetech doctor            # RPC latency + tx/s, websocket support, agent credentials
```

The public RPC works for single lookups (the client backs off automatically when rate-limited)
but manages only ~1-2 transactions/s and drops the websocket firehose. Use a paid RPC for
`watch`, `index-launches` and anything with lots of history.

## Workflows

### 0. Calibrate to your bot (do this first)

```bash
snipetech audit <your-sniper-wallet> --days 7 --apply-calibration
```

This measures how many slots after the create your buys actually land, and where in the slot,
then writes `snipe.entry_slot_offset` / `snipe.entry_position` into your config. It also shows
where you lost money: by deployer (with a blocklist suggestion), by launch outcome, and by entry
delay. Set `costs.priority_fee_sol`, `costs.tip_sol` and `costs.platform_fee_bps` to what you
actually pay.

### 1. Should I snipe this deployer?

```bash
snipetech deployer <deployer-wallet>                 # backfills its launches, then analyzes
snipetech deployer <wallet> --family                 # + sibling wallets funded by the same wallet
snipetech deployer <wallet> --buy 1 --entry-slots 0  # what if I were bigger / faster?
```

Every deployer report now includes **Competition** (who snipes, what they pay, bribe needed to be
#1/#2/#3…) and a **bribe sweep**: for each bribe level, the rank you'd have landed at and your EV
after paying it. The recommended bribe is picked on older launches and checked on newer ones, and
lands in `recommended.bribe_sol` for your sniper.

### 2. Find dev wallets worth sniping

```bash
snipetech index-launches --hours 6        # every launch, ~1 RPC call each (~2,600/hour)
snipetech profitable-devs --backfill 20   # repeat deployers ranked by your EV, with the bribe to land early
snipetech top-deployers --backfill 20     # fetch early trades for the 20 most active, rank by EV bound
snipetech backtest --days 7               # which snipe/skip rule (deployer history + dev buy, bundles, mayhem) made money?
snipetech optimize --days 7               # best exits/size/latency across all indexed launches
```

### 3. See what the other snipers are doing

```bash
snipetech snipers --days 3                # wallets that land first on indexed launches, by realized PnL
snipetech sniper <wallet> --days 3        # reconstruct its settings and behaviour
snipetech label <program-or-address> "BotName" --kind router   # name routers / tip destinations in reports
```

A profile separates what is **exact** (read straight from its transactions) from what is
**inferred** (patterns in its behaviour):

| Exact | Inferred |
|---|---|
| buy size (exact budget when it uses `buy_exact_sol_in`) | entry timing and rank among snipers |
| compute-unit price & limit, priority fee | take-profit / stop-loss levels (exit clusters) |
| tips and the landing service paid (Jito accounts verified on-chain; separate bundle tip txs included) | time-based exits |
| bot/platform fees (bps of trade) vs flat fees | sells when the dev/bundle sells |
| router program, pump instruction, full tx template | partial exits |
| slippage caps (`max_sol_cost` / `min_tokens_out` / `min_sol_output`) | launch filters (its picks vs everything launched) |
| durable nonce, lookup tables, tx version, separate fee payer | related wallets (same funder, co-buying) |

Private config files never touch the chain, so nobody can read "the" config; this is everything the
chain reveals about it, with exits attributed in order (dev-sell reaction → take-profit band →
stop-loss band → time) so one behaviour isn't mistaken for another.

### 4. Is copytrading this wallet worth it?

```bash
snipetech copy <wallet> --days 14 --size 0.5 --latency 1
snipetech copy-rank <w1> <w2> <w3>        # or --file wallets.txt
snipetech discover-wallets --days 3       # profitable round-trippers in your indexed data
```

### 5. Run it live next to your sniper

```bash
snipetech watch                  # indexes every launch's first 15 min + emits signals (JSONL)
snipetech serve                  # HTTP API on 127.0.0.1:8787
snipetech export                 # snipetech-rules.json for your sniper to load at startup
snipetech list add <addr> --list block --reason "rinsed me 5x"
snipetech list add <addr> --list copy_target    # watch follows it live
```

`watch` needs a websocket-capable RPC that tolerates the pump.fun firehose (Helius, Triton,
QuickNode...); the public endpoint drops it. Run it for a day or two and `top-deployers`,
`backtest` and `discover-wallets` gain a complete dataset with no extra RPC cost.

### 6. Ask the agent

```bash
snipetech agent
> is 7xKX…2b worth sniping at 0.5 SOL? I land 1 slot late
> compare these copy targets: A…, B…, C… — which should I follow and at what size?
> index the last 3 hours, find the deployers I should be sniping, and block the rinsers
snipetech agent -p "audit my wallet 9yQ… and tell me what to change"
```

The agent (Claude Opus 5.5 with adaptive thinking, via the Anthropic tool runner) calls the same
analyses through 15 tools. It answers from real tool output, prefers walk-forward numbers, and
only changes lists or settings when you ask.

## Example (real mainnet output, abridged)

```text
$ snipetech deployer GUjnwzeGHsjUJYEjwL43iVr3KRYKUtJeigBu396bHCms --launches 8
VERDICT: AVOID   score 8.7/100
  • EV +0.0098 SOL/snipe but lower bound -0.0875 is not above 0
  • but the newest 3 launches (unseen by the optimizer) lost -0.2618 SOL/snipe
  • rinses snipers in 50% of launches (insiders dump within 30s)
  → skip this deployer's launches

Outcomes (sniper's view):      runner 12%   rinse 50%   rug 38%
Behaviour:
  dev buy median 1.5012 SOL (8.2% supply)
  insiders dump after median 20s; dev sells in 100% of launches
  peak mcap median 35.6 SOL; graduation 0%; mayhem 100%
  cadence: 528 launches/day, median gap 47s, median 31.5 trades per launch window

Your strategy:  n=8  EV +0.0098 SOL (bound -0.0875)  win 38%  maxDD 1.1159
Walk-forward:   picked [TP+100% SL-50% hold30s devexit] on 5 older launches →
                unseen 3: EV -0.2618 SOL  win 0%

Entry latency (slots after create):   0 → +0.018   2 → +0.066   10 → +0.040
Recent launches:
  3unodQ…pump  Know  rug     -0.2360  stop_loss    0.97x  dump 31s
  7cJtWb…pump  Know  rinse   +0.4443  dev_sell     1.93x  dump 5s
  5Ktg4o…pump  Know  runner  +0.6114  take_profit  2.23x  dump 22s
```

## Sniper integration

**Hot path: HTTP lookup.** Sub-millisecond, served from the local cache, no RPC:

```ts
const v = await fetch(`http://127.0.0.1:8787/v1/deployer/${createEvent.creator}`).then(r => r.json());
if (v.verdict === "SNIPE" || v.verdict === "ALLOW") buy(mint, v.recommended?.buy_sol ?? 0.5, v.recommended?.exit);
```

**Zero-latency: rules file.** Load `snipetech-rules.json` (`snipetech export` / `GET /v1/rules`)
into memory and check `snipe_deployers` / `skip_deployers` sets. Each deployer entry carries the
recommended `buy_sol` and exit rules.

**Push: signals.** `snipetech watch` prints one JSON object per signal and POSTs it to
`live.webhook_url`:

```json
{"kind": "launch", "mint": "...", "deployer": "...", "action": "skip", "verdict": "AVOID", "mayhem": false, ...}
{"kind": "copy", "wallet": "...", "mint": "...", "side": "buy", "sol": 1.2, "copy_verdict": "COPY", "recommended": {...}}
```

| Endpoint | |
|---|---|
| `GET /v1/deployer/{addr}` | cached verdict + list overrides + funding-family fallback (`?refresh=1` re-analyzes) |
| `GET /v1/mint/{mint}` | the launch and its deployers' verdicts |
| `GET /v1/copy/{wallet}` | cached copy verdict (`?refresh=1`) |
| `GET /v1/rules` | everything in one document |
| `GET /v1/signals?since={id}` | signals from `watch` |
| `GET/POST/DELETE /v1/lists/...` | allow / block / watch_deployer / copy_target |

Set `api.api_key` to require an `X-API-Key` header.

## Reading the verdicts

| Verdict | Meaning |
|---|---|
| `SNIPE` / `COPY` | EV lower bound > `verdict.min_ev_sol` on enough history |
| `COPY_FILTERED` | profitable only with the reported filter (validated on unseen positions) |
| `MARGINAL` | positive mean, but the lower bound isn't — size down or wait for data |
| `AVOID` | negative EV for you after latency, fees and tips |
| `INSUFFICIENT_DATA` | fewer than `snipe.min_launches` / `copy.min_positions` |
| `ALLOW` / `BLOCK` | your manual overrides (a blocked *funder* blocks its fresh wallets too) |

Launch outcomes (sniper's view): **rinse** = dev + same-slot bundle wallets dumped within
`rinse_window_secs`; **rug** = they dumped later; **runner** = your position hit `runner_multiple`
before insiders dumped; **graduated** = curve completed; **dead** = nobody came; **chop** = the rest.

## Limitations

- Copytrading simulates pump.fun bonding-curve and PumpSwap trades. Positions on other DEXes
  (Raydium, Meteora, …) are counted as unsimulated, not guessed.
- Counterfactuals assume other traders' token amounts would not change because of you. That's
  accurate for normal sizes and conservative-to-neutral for large ones; check the size sweep.
- Snipes held past migration are valued on the PumpSwap pool where trade data exists, else at the
  last known price.
- Insiders are the dev wallets, wallets that co-sign the create transaction and, with transaction
  fingerprints, creation-slot buyers paid for or funded by the team plus untipped buys bundled right
  behind a tipped create. Without fingerprints (live-only data) every creation-slot buyer counts as
  an insider. Wallets the dev funded in other slots are only linked with `--family`.
- The bribe sweep assumes you are fast enough to land in the same slots as the snipers you outbid;
  fees decide ordering among equally fast bots (the report shows how often the higher bribe actually
  landed first).
- Past behaviour is evidence, not a guarantee. Deployers adapt, which is why recent-behaviour
  warnings and walk-forward numbers exist.

## Development

```bash
pip install -e ".[dev]" && pytest          # tests use real mainnet fixtures + synthetic markets
python scripts/update_idl.py               # refresh the vendored pump.fun IDLs after a program upgrade
```

Layout: `solana/` (RPC, IDL decoding, normalization), `ingest.py` (backfills), `store.py`
(SQLite), `sim/` (market paths, entries/exits), `analysis/` (launches, deployer, copytrade,
optimizer, policy backtest, audit), `service.py` (orchestration), `live.py`, `api.py`, `agent/`,
`cli.py`.
