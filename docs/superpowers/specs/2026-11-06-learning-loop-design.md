# The learning loop — design (6 Nov 2026)

## Purpose

Turn snipetech from "a brain the owner drives" into a system that runs itself: it keeps finding candidate wallets and
entry signals from every source the owner pays for, qualifies them against the landing we actually get, trades the
qualified ones through OKTech inside a standing budget, measures every fill, and feeds the measurements back into
what it trades next. The owner hears from it on Telegram only when money or a decision is involved. Everything else
is deterministic code with tests; Claude is used where judgment helps (synthesis, anomalies, proposals), not for
the loop itself.

Owner's words (6 Nov): "whether it be agents researching while others are trading… it just has to be smart and
learn as it's going, find what's worth it, what's not… build it in the best way possible using every single resource
I gave you… make sure the Telegram bot messages me for important things like I need more credit." Added: pump.fun
callouts, fee claims and wallet interactions as signals.

Resources in hand: Helius (RPC, LaserStream tape, Sender, parsed history with slot index), Birdeye Starter (8 M CU),
SolanaTracker Advanced (200 k requests), Vybe free, OKTech Sniper API (per-sniper priority fee, tip, send count,
compute units), TypeSafe Jev, Telegram bot, Anthropic API (40 $/day approved), Vultr Frankfurt VPS (resize to
16 GB / 6 CPU / 320 GB approved in principle), a staked-routing API and a second trading API (idle until an own
sender is justified).

## What the measurements say (the premises)

- OKTech lands in the leader's slot 6/7 times; we lose inside the slot, in a fee auction (docs/live-recipe.md,
  "Inside the slot"). The bots ahead pay 0.01–0.3 SOL per fill on 1–5 SOL fills; we bid 0.001 on 0.05 SOL fills.
- The paper edge of the known wallets exists at the first position after the leader and vanishes at +6
  (docs/roadmap.md). The landing position is therefore a *parameter to be measured and bought*, not assumed.
- KOL lists are crowded; the uncrowded wallets are found, not bought (SolanaTracker 90-day board: 19 human-paced
  wallets with 70–90 % win rates on the first scan; Birdeye top traders of hot coins are mostly high-frequency bots).
- Research (reports/Solana copy trading tools 2026.md): no hosted executor exposes more control than OKTech or is
  shown to land earlier; Jito ShredStream is gone; raw shred feeds and landing services do not change the in-slot
  auction; the only levers are bid, compute-unit limit and fill size.

## Approach

**A deterministic pipeline with bounded policies, Claude at the judgment points.** Rejected: a swarm of always-on
agents (expensive, unpredictable, not something to hand money to) and pure scripts (nobody notices the new thing).

## Architecture: one loop, six stages, one database

All stages are `snipetech` subcommands run by systemd timers, idempotent, writing to tables in the one SQLite
database (WAL, writes retried; analysis jobs read a consistent snapshot copy refreshed hourly so they never block
the tape). Stage order and cadence:

| stage | cadence | command | writes |
|---|---|---|---|
| ingest | continuous | tape recorder (exists); `events poll` every 10 s | `trades`, `launches`, `events` |
| discover | hourly | `discover run` | `candidates` |
| qualify | after discover, continuous queue | `qualify run` (one lab at a time) | `verdicts` |
| book | every 5 min when enabled | `book run` | `book_state`, OKTech snipers |
| measure | every 5 min | `measure run` | `fills`, `bid_model`, `wallet_live` |
| think | 15 min / daily / weekly | `operate` (exists), analyst, `strategist` | notes, reports, proposals |
| resources | every 15 min (inside `operate`) | — | `resources`, Telegram |

### Tables (new)

- `candidates(wallet PK, first_seen, last_seen, sources JSON, stats JSON, status)` — every wallet ever surfaced,
  never deleted. Sources: tape, solanatracker, birdeye_gainers, birdeye_top, vybe, coin_first_buyers, callout_follow.
- `verdicts(wallet, computed_at, landing_pos, n_tape, mean_sol, lower_sol, hist_days, hist_realized_usd,
  hist_win_rate, hist_trades, crowding, venue_share, score, status, next_review, reasons JSON)` — one row per
  evaluation; latest wins; `status` ∈ candidate | qualified | retired | banned.
- `events(id, ts, slot, kind, mint, wallet, data JSON, source)` — kinds: `koth` (king of the hill), `live_start`,
  `live_end`, `featured`, `trending`, `fee_claim` (creator fee collected: amount, claimer, venue), `callout`
  (channel, author, mint, strength), `dev_transfer` (dev → wallet before a buy or callout), `migration`, `social`
  (twitter/website first seen). Indexed by (mint, ts) and (wallet, ts).
- `edges(src, dst, kind, first_ts, last_ts, lamports, n)` — wallet relationships: funded_by, paid_by_dev,
  co_bundle, same_funder. Used as filters (a wallet paid by the dev is not a copy target).
- `fills(sig PK, ts, slot, pos_in_slot, wallet_copied, event_kind, mint, side, sol, tokens, trades_behind,
  price_gap_pct, priority_lamports, tip_lamports, cu_limit, cu_used, landing_service, pnl_sol, exit_reason,
  sniper_id, bid_arm)` — every live buy and sell, audited from the chain and the tape.
- `bid_model(arm, n, mean_pos, p25_pos, p75_pos, mean_gap_pct, mean_fee_sol, updated_at)` — what each bid buys.
- `wallet_live(wallet, n, mean_sol, lower_sol, last_fill, status)` — live expectancy with exponential decay
  (half-life 7 days); replaces the paper number once `n ≥ 10`.
- `book_state(key, value)` — enabled flag, day's risk used, equity at day start, kill reason.
- `resources(name, value, unit, checked_at, threshold, status)` — credits and capacity.

### Stage 1 — ingest additions

- `events poll`: pump.fun's public feed for King of the Hill, currently-live, featured/trending, every 10 s;
  diff against the last poll → events. Rate-limited, failures logged not raised.
- Fee claims: the tape recorder already sees every pump.fun / PumpSwap transaction; the decoder gains the
  `collect_creator_fee` / `collect_coin_creator_fee` instructions → `fee_claim` events with amount and claimer.
- Dev transfers: for every launch, the creator's outgoing SOL transfers in the 24 h around launch (Helius parsed
  history, 10 credits per 100 tx) → `dev_transfer` events + `paid_by_dev` edges. Bounded to launches that reach
  ≥ 20 SOL on the curve, so the credit cost stays in the low thousands a day.
- Callouts (phase 2, needs the owner's channel list and a one-time reader login): a Telegram reader account
  streams the chosen channels; each post goes to Jev with three questions (is this a buy call; for which mint;
  how strong) → `callout` events. Cost: cents a day.
- Helius slot-index backfill: for every wallet that reaches `qualified`, 90 days of its trades with
  `slot` + `transactionIndex`, so entry position is known beyond the tape's window.

### Stage 2 — discover (hourly)

Sources, each a function returning `[{wallet, source, stats}]`, run in parallel with per-source budgets:
tape (`discover-wallets`, exists), SolanaTracker leaderboard 30 d and 90 d (5 pages each, server-side
`maxSingleTokenPct`), Birdeye gainers (realised, 1 W and 30 d) and top traders of the tape's hottest coins,
Vybe 30 d board, **coin-first**: for every coin that did ≥ 5× from its first minute in the last 24 h (tape),
its first 50 buyers (SolanaTracker `first-buyers`, Birdeye `first-buyers`) and who sold above 2× — the "huge
candles" angle. Union → upsert `candidates`. Platform wallets (labels kind service/platform, Birdeye/SolanaTracker
bot/exchange identity) are stored with `status=banned` so they are never re-evaluated.

### Stage 3 — qualify (continuous, one lab at a time)

For each candidate with no verdict or `next_review ≤ now` (new first, then oldest), compute:

1. **History gate**: SolanaTracker 90 d (or Birdeye 30 d when absent): `trades ≥ 20`, `win_rate ≥ 35 %`,
   `realized_usd ≥ 500`, max single-coin share ≤ 60 %, `20 ≤ trades/90 d ≤ 3000` (human-paced).
2. **Venue gate**: ≥ 50 % of its last 90 d trades on pump.fun curves or PumpSwap (what OKTech executes).
3. **Tape gate**: the by-wallet lab at `landing_pos` (config; starts at +6, updated by the measure stage to the
   position the current bid actually buys): `n ≥ 20` and `lower_sol > 0` at 0.2 SOL; or `n ≥ 10` with the
   history gate strong (win ≥ 60 %, ≥ 50 coins).
4. **Crowding gate**: from the tape, the median count of distinct buyers within 1 s after its buys and the
   median price gap to the 3rd follower; `crowding = followers_1s`; qualified only if the lab at `landing_pos`
   already prices this in and the gap to the 3rd follower is < 15 %.
5. **Relationship gate**: no `paid_by_dev` edge on its recent coins; not a repeat co-bundle wallet.

Score = lower_sol at landing × min(1, n/30) × venue_share, minus a crowding penalty. `status=qualified` when all
gates pass; `next_review` = 24 h (qualified) / 72 h (candidate) / 7 d (retired). Labs are the expensive step: the
queue runs them in batches of 300 wallets (one lab call covers all labelled wallets), one lab at a time, and the
operator's own lab starts go through the same queue, so two labs never run together.

### Stage 4 — the book (every 5 min when `book.enabled`)

Policy with owner-set bounds in `snipetech.toml`:

```
[book]
enabled = false            # the owner flips this
daily_risk_sol = 2.0       # realised + unrealised loss that stops the day
fill_min_sol = 0.2
fill_max_sol = 1.0
max_wallets = 6
equity_floor_sol = 3.0     # below this nothing starts
day_stop_sol = 1.0         # day loss that stops everything until tomorrow
min_score = 0.005          # SOL per trade lower bound at landing
explore_share = 0.2        # share of fills on the alternative bid arm
bid_arms = [{priority=0.001, tip=0.002, cu=200000, name="control"},
            {priority=0.02,  tip=0.02,  cu=60000,  name="paid"}]
send_count = 1
```

Each run: refresh equity (native + WSOL) and the day's realised/unrealised result from `fills`; if a stop
condition holds, stop every sniper that holds nothing and write `book_state.kill_reason`; otherwise rank
`qualified` wallets by `wallet_live` (when n ≥ 10) else verdict score; keep one capped `ctbot` sniper per wallet
in the top `max_wallets` (create / restart spent ones / stop retired ones that hold nothing — never one that holds
a coin); size each fill so that the arm's fee is ≤ 3 % of the fill within [fill_min, fill_max]; assign arms per
sniper so that `explore_share` of expected fills run on the non-best arm; every create/start/stop/retire is a
Telegram line. The first days of the book *are* the fee experiment: two arms on the same leaders.

### Stage 5 — measure (every 5 min)

For every new transaction of the book wallet: fetch the transaction (version 1), classify buy/sell, read
priority fee, tip, CU limit/used; from the tape: the leader's buy on that coin, our position in the slot, trades
between, price gap, the exit and PnL per round trip → `fills`. Update `bid_model` per arm (what position and gap
each bid bought), `wallet_live` with decay, and, when `n ≥ 30` fills on an arm, set `landing_pos` for the qualify
stage to that arm's p75 position so paper and live agree. Duplicate sends (send count) and failed transactions
are counted as cost. All of today's `copyaudit` logic moves here and runs unattended.

### Stage 6 — think

- Operator (exists): 15-min rounds on change; gains the `resources` section and the Telegram command inbox.
- Analyst (exists, daily): reads verdicts, fills and bid_model; writes the day's report and the shortlist.
- **Strategist** (weekly, Opus, `turn_usd` 10): reads a week of `fills`, `bid_model`, `verdicts`, `events`
  expectancies; proposes parameter changes as a JSON patch to `[book]` / `[qualify]` within `[autotune]` bounds
  (e.g., bid arms within priority ≤ 0.05, fill_max ≤ owner's ceiling, min_score ± 50 %); the patch is applied
  automatically when inside bounds and announced, else sent to Telegram as a question. Every applied change is a
  `events(kind=param_change)` row so results can be attributed.
- Event expectancy: a nightly job runs the lab with each event kind as trigger at `landing_pos` and writes
  `event_edge(kind, n, mean_sol, lower_sol)`; the analyst reports which event kinds carry edge; a kind with
  `lower_sol > 0` and `n ≥ 50` becomes a candidate strategy sniper (hunt/normal mode) the strategist may propose.

### Resources and alerts (inside `operate`, every 15 min)

| resource | source | alert when | message says |
|---|---|---|---|
| Anthropic credit | spend ledger vs `[resources] anthropic_credit_usd` (owner sets when topping up) | 20 % left or < 3 days at the 7-day average | "top up 100 $ at console.anthropic.com, then run `snipetech config set resources.anthropic_credit_usd <new total>`" |
| Birdeye CU | `/utils/v1/credits` | < 15 % of cycle left | plan page link |
| SolanaTracker requests | own counter (table) vs 200 k/month | < 15 % left | plan page |
| Vybe credits | own counter vs 25 k | < 15 % | — |
| Helius credits | own counter of calls × cost (no balance API) | > 80 % of plan | Helius dashboard |
| SOL float | native + WSOL vs `max_wallets × fill_max × 2 + 0.2` | below | "send X SOL to <wallet>" |
| disk | free GB from the server | < 30 GB | "resize to 320 GB" |
| tape lag / services | exists | stale / down | — |
| OKTech | sniper errors, failed sells | > 3 in an hour | — |

Telegram also gets a **daily digest** at 07:00 UTC: equity, day PnL, fills by arm with positions, wallets in/out,
spend, resources. **Commands** read by the operator each round via `getUpdates`: `/status` (one message),
`/stop` (book disabled, snipers holding nothing stopped, confirmation), `/resume`, `/budget <sol>` (within the
owner's configured ceiling), `/why <wallet>` (its verdict reasons).

### Infrastructure

- Server: resize to 16 GB / 6 CPU / 320 GB in a quiet hour chosen by the operator (no job running); the tape
  retention becomes 60 days.
- Database: hourly `VACUUM INTO` snapshot `snipetech.ro.db` used by labs, qualify and analyst reads; the tape and
  the book write to the live file; the lock-retry stays as a backstop.
- Jobs: the existing job runner gets a priority queue (`book`/`measure` first, labs last) and the one-lab rule
  enforced centrally; every stage is also runnable by hand through `remote run`.
- Deploys: `remote update` drains jobs (waits for running labs up to 10 min, refuses while the book has an open
  position) instead of killing them.
- Secrets: unchanged (env file, redaction extended to every new key).

### Safety

- The book never exceeds `[book]` bounds; bounds change only through the owner (config) or the strategist inside
  `[autotune]` limits; both logged as events.
- Never stop a sniper that holds a coin; never start when equity < floor; day stop and daily risk are computed
  from the chain, not from OKTech's reports.
- Kill switches: `/stop` on Telegram, `operator/inbox.md` containing `STOP`, or `book.enabled=false`.
- Every live action is one Telegram line; every parameter change is attributable.

## Testing

Unit tests per stage with canned data (no network): discover merging and banning; each qualify gate; the book
policy (sizing, arms, stops, never stopping a holder) against a fake OKTech; measure's classification from real
fixture transactions (version 1) and position/gap maths against a synthetic tape; bid_model and decay updates;
alert thresholds and message text; Telegram command parsing; strategist patch validation against bounds.
Acceptance on the server: each stage run by hand once, then its timer; the book first with `enabled=false`
(dry: it logs what it would do) for a day, then enabled at the owner's numbers.

## Rollout order

1. Resources, alerts, daily digest, Telegram commands (owner asked first).
2. `candidates` / `verdicts` tables, `discover run`, `qualify run` with the lab queue; the 19 SolanaTracker
   wallets and the Birdeye sets go through it.
3. `measure run` with the audit moved in; `fills` from the earlier copy test backfilled.
4. The book, dry first, then live at the owner's numbers (the fee experiment).
5. Events: pump.fun feed, fee claims, dev transfers; nightly event-edge lab.
6. Strategist and autotune; DB snapshot; deploy draining; server resize scheduled.
7. Callouts via a Telegram reader + Jev (needs the channel list).

## Out of scope

An own transaction sender (research: no slot gain; revisit only if OKTech's bid knobs prove not to buy position);
X/Twitter ingestion; multiple wallets; a web UI.
