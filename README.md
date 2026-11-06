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
snipetech sniper <wallet> --deep 20       # + the whole market during its last 20 holds (trailing stops)
snipetech label <program-or-address> "BotName" --kind router   # name routers / tip destinations in reports
```

A profile separates what is **exact** (read straight from its transactions) from what is
**inferred** (patterns in its behaviour):

| Exact | Inferred |
|---|---|
| buy size (exact budget when it uses `buy_exact_sol_in`) | entry timing and rank among snipers |
| | entry market cap; wallets it buys right after (copy targets vs shared snipe targets) |
| | trailing stops (sells a steady % below the peak; needs the market while it held: `--deep` or `watch`) |
| compute-unit price & limit, priority fee | take-profit / stop-loss levels (exit clusters) |
| tips and the landing service paid (Jito accounts verified on-chain; separate bundle tip txs included) | time-based exits |
| bot/platform fees (bps of trade) vs flat fees | sells when the dev/bundle sells |
| router program, pump instruction, full tx template | partial exits |
| slippage caps (`max_sol_cost` / `min_tokens_out` / `min_sol_output`) | launch filters (its picks vs everything launched) |
| durable nonce, lookup tables, tx version, separate fee payer | related wallets (same funder, co-buying) |

Private config files never touch the chain, so nobody can read "the" config; this is everything the
chain reveals about it, with exits attributed in order (dev-sell reaction → take-profit band →
stop-loss band → trailing stop → time) so one behaviour isn't mistaken for another.

### 3b. Study every factor behind a wallet's trades

`sniper` says *how* a wallet trades (fees, tips, sizes, exits). `study` says *why*: for every
entry it snapshots the market at that moment and compares it with the tokens that were trading at
the same moment but that the wallet passed on; for every sell, with the moments it kept holding.

```bash
snipetech watch --tape                    # record every trade of every token (pump.fun curve,
                                          # PumpSwap, Raydium LaunchLab; ~1 GB/day) while it trades
snipetech label --file kols.txt --kind kol  # your KOL list: one address per line, optional name
snipetech study <wallet> --days 1         # contrast its entries/exits with what it passed on
snipetech study <wallet> --days 7 --backfill-entries 40   # + describe older entries from fetched windows
```

Factors at each moment, using only what happened before it:

| Group | Factors |
|---|---|
| Price action | change over 5s / 30s / 60s / 5m, distance from the 5m high / low, market cap, curve progress, age |
| Volume & flow | volume, buys, sells, buy share, net flow, unique buyers per window; largest buy, buys ≥ 1 SOL |
| Wallets | KOL wallets buying (your list), "smart" wallets buying (`--auto-smart N`), holders and top-10 share |
| Dev & deployer | dev buy at create, dev has sold, snipers in the first 3 slots, deployer's earlier launches |
| Socials | X link kind (tweet / community / profile), seconds since the linked tweet (decoded from the tweet id), tweet → launch delay, telegram, website, launch tool (pump.fun, j7tracker, uxento...) |
| Market | market-wide trades and launches, the token's volume rank among all tokens (trending) |
| Exits | position value, peak since entry, drop from peak, hold time (+ all of the above) |

Each factor gets an AUC (0.5 = no difference, 1.0 = separates perfectly) and its best cut; a depth-3
decision tree turns the strongest into rules, scored with time-ordered cross-validation so rules
that only describe the past are labelled as such. Tokens quoted in other assets (tokenized stocks,
wXRP, PUMP) are kept apart from SOL trades and valued at the asset's price; routing hops
(SOL → DJT → token) are not counted as entries. Tweet engagement (likes, the author's followers)
needs an X data source and isn't measured.

### 3c. Run it through OKTech

snipetech decides; [OKTech](https://oktech.me)'s snipers buy and sell. Wallets with a COPY verdict
become `ctbot` snipers (copy its buys above the size the analysis says are worth copying, under its
market-cap ceiling; sell when it sells; plus the optimized own exits where those beat mirroring).
Deployers with a SNIPE verdict become `normal` snipers on their launches, with the recommended size,
bribe (split into priority fee + tip) and exits.

```bash
export OKS_KEY=...                  # OKTech: Sniper page -> API (never put it in a URL or this repo)
snipetech oktech status             # key permissions, wallets, the snipers snipetech made
snipetech oktech plan --wallet SNIPER          # what would be created / replaced / removed, and why
snipetech oktech plan --json                   # the exact payloads
snipetech oktech push --wallet SNIPER          # apply (asks first); --prune also removes stale ones
```

Each sniper's `externalId` comes from its settings, so pushing twice never duplicates; a changed
verdict replaces the sniper (the old one is stopped and deleted). Unless "Allow starting" is on for
the key, snipers are created idle: start them on the Sniper page. Settings OKTech can't express
(e.g. "at most N other buyers") are listed as notes in the plan rather than silently dropped.

### 3d. Run it 24/7 on a server

One command on any Ubuntu/Debian server (as root or with sudo):

```bash
curl -fsSL https://raw.githubusercontent.com/frieshimself-cpu/snipetech/refs/heads/claude/stoic-babbage-5e6pck/deploy/bootstrap.sh | sudo bash
```

It installs snipetech under `/opt/snipetech`, asks for your Helius and OKTech keys (hidden input, stored
only in `/etc/snipetech/env`, mode 600, and checked against both APIs), and starts:

* `snipetech-tape` — records every trade of every token (several GB/day: give it 500 GB of NVMe);
* `snipetech-daily` — refreshes dev verdicts, ranks KOLs to follow, runs the strategy lab at several
  latencies and writes the OKTech plan to `/var/lib/snipetech/reports/<date>/`;
* `snipetech-ops` — remote operations at `https://snipetech.<your-ip>.sslip.io` (Caddy, automatic TLS).

Re-running the command updates the code and keeps keys and data (`SNIPETECH_REKEY=1` asks for the keys
again). Anything else on the server is left alone; an existing Caddy config gets one site block
appended (backed up, rolled back if it doesn't validate). A machine close to the validators
(Frankfurt / Amsterdam / New York) keeps the tape's gaps short.

### 3e. The operator — Claude on the server, every 15 minutes

`snipetech operate` is one unattended round: a script gathers the facts (services, tape lag, jobs, the sniper wallet's
SOL and wrapped SOL and holdings, every OKTech sniper's state, fills since last time, the paper test, today's spend),
compares them with the previous round, and calls Claude **only when something changed** or the daily analysis is due.
Claude gets a deliberately small tool set — read a job log, run a read-only query, start a research job from a fixed
list (one `lab` at a time), write a note — and nothing that touches OKTech, wallets or keys. Spend is capped per day
(`[operator] daily_usd`, default 15) and per turn (`turn_usd`, default 2). Notes land in `operator/notes.md`;
decision-level notes (a wallet clearing all three gates, a sniper running or a coin held when none should be, equity
moving more than `equity_alert_sol`, the tape stalled, a service down, the budget reached) also go to Telegram when
`TELEGRAM_BOT_TOKEN` is in `/etc/snipetech/env`.

```bash
snipetech operate --dry-run          # the snapshot and the gate's decision, never a Claude call
snipetech operate --telegram-setup   # after you messaged your bot once: stores your chat id, sends a hello
snipetech operate                    # one real round; --analyst forces the daily analyst turn
sudo systemctl enable --now snipetech-operator.timer   # every 15 minutes from now on
```

Config (`/etc/snipetech/snipetech.toml`): `[operator] wallet = "<sniper wallet>"` is required; `expected.json` in
`operator/` says what "all quiet" means when a live test is on (`{"snipers_running": N, "held": N}`); a request you
drop into `operator/inbox.md` is read on the next round. The design is in
`docs/superpowers/specs/2026-11-06-operator-agent-design.md`.

**Alerts, digest, commands, dashboard.** Every round also checks, in code, what you pay for — Anthropic credit
left, Birdeye compute units, SolanaTracker and Vybe monthly requests, the wallet's SOL float against the book's
needs, disk, failed transactions in the last hour — and sends one Telegram line per critical item per day with the
exact thing to do (`[resources]` thresholds in the config; `snipetech config set resources.anthropic_credit_usd <n>`
after a top-up). The first round after `digest_hour_utc` (07:00 UTC) sends a digest: wallet and its change since the
last digest, snipers, spend today and over 7 days, resources not ok, notes written. The bot obeys three commands
from your chat only: `/status`, `/stop` (writes a STOP flag so no running sniper is expected, and stops running
snipers only while the wallet holds nothing — a stopped sniper strands its coin, so holders are named and left
alone), `/resume`. A live page at `https://<host>/dash` shows services, resources, the wallet, jobs, the last
decisions and notes, candidates and fills, refreshing every 15 s; it is served by `snipetech-ops`, asks for a
password (`DASH_TOKEN` from `/etc/snipetech/env`, any user name) and does not exist until that token is set.

**Discover and qualify (the learning loop, phase 2).** Two timers do the wallet work by themselves.
`snipetech discover run` (hourly) puts every source's wallets into one `candidates` table: the tape's consistent
winners, your tracked list, SolanaTracker's 90- and 30-day boards, Birdeye's gainers and hot-coin top traders,
Vybe's 30-day board, and the first buyers of coins that ran 5× within ten minutes. Platform wallets and flagged bots
are banned, a failing source is reported and skipped, and each source has its own budget. `snipetech qualify run`
(every 30 min) takes the candidates whose review is due, labels them `cand`, runs **one** by-wallet lab at the
`[qualify] landing_pos` (+6) and writes a verdict per wallet: `qualified` (history, venue, tape n and lower bound,
crowding and relationship gates all passed; reviewed daily), `candidate` (not enough evidence yet; 72 h) or
`retired` (loses, trades elsewhere, bot pace, or tied to dev teams; a week). A lab runs at most once per
`lab_interval_h` and never beside another lab: every lab on the box takes the same `lab.lock`, so the daily job,
an ops job and the qualify timer queue instead of colliding. The operator reads the result with `read_verdicts`
and the dashboard shows the counts and the qualified wallets.

```bash
snipetech discover run --sources tape,owner,coinfirst   # one run, local sources only; --json prints the summary
snipetech qualify run --max 100                         # one lab over the 100 most overdue candidates
snipetech remote sql "select status, count(*) from candidates group by status"
sudo systemctl enable --now snipetech-discover.timer snipetech-qualify.timer snipetech-measure.timer snipetech-book.timer   # once, after deploy/setup.sh
```

**Measure (phase 3).** `snipetech measure run` (every 5 min) turns every buy and sell of the book wallet into a
`fills` row audited from the chain and the tape: position in the slot, the copied wallet's buy and how many trades
and what price gap we landed behind it, priority fee, tip and compute units from the transaction, PnL per round trip
(FIFO, partial sells), and failed sends as rows of pure cost. From those it keeps `bid_model` (what each bid arm
buys: position, gap, fee, failures) and `wallet_live` (live expectancy per copied wallet, 7-day half-life); once an
arm has 30 fills its p75 position becomes the landing the qualify lab measures at, so paper and live agree. `measure
backfill --since <unix> --until <unix>` replays a past window (the 6 Nov copy test). The operator reads it all
with `read_fills`.

**The book (phase 4), dry first.** `snipetech book run` (every 5 min) ranks the qualified wallets (a wallet's live
mean after 10 live fills, else its verdict score; `min_score` floor), keeps one capped copy sniper per wallet in the
top `max_wallets`, assigns bid arms (`[book] bid_arms`; the alternative arm for an `explore_share` of wallets), sizes
each fill so the arm's fee is at most 3 % of it (an arm that cannot is skipped and named), and applies the stops:
equity floor, day stop, daily risk. It never stops a sniper while the wallet holds a coin. With `enabled = false`
(the default) it only writes `[book dry] would …` notes, once per action per day, and calls OKTech for nothing; the
owner reads a day of those before flipping `enabled = true` in the config file, after which every stop, create and
start is a Telegram line. `--live` on the command line is refused while the config says disabled.

**Remote operations.** `snipetech-ops` only accepts requests signed with an Ed25519 key listed in
`deploy/ops_authorized_keys` — public halves only, so nothing secret is ever exchanged — and refuses
anything older than 5 minutes or replayed. It can: show status, run allowlisted snipetech commands as
background jobs, read reports and job logs (never the database or the env file), drop wallet lists
into `inputs/`, run one read-only SQL query at a time, tail the services' journals, start the daily
job, and update itself to the latest commit of its branch. Output is scrubbed of the keys in the
server's environment. From the operator's machine:

```bash
snipetech remote keygen ~/.snipetech/ops_key            # prints the line for deploy/ops_authorized_keys
export SNIPETECH_OPS_URL=https://snipetech.1-2-3-4.sslip.io SNIPETECH_OPS_KEY=~/.snipetech/ops_key
snipetech remote status
snipetech remote --wait run lab --latency 0 --position start
snipetech remote ls reports && snipetech remote read reports/2026-11-05/oktech-plan.txt
snipetech remote sql "SELECT count(*) FROM trades"
snipetech remote put kols.txt && snipetech remote --wait run label --file /var/lib/snipetech/inputs/kols.txt --kind kol
snipetech remote update                                    # pull, reinstall, restart
```

Turn it off on the server with `sudo systemctl disable --now snipetech-ops`.

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
