# History backfill: big wallets' buys in older, bigger coins

12 Nov 2026. The owner said "Ok do it" to backfilling from Helius. Built on branch `wf/amm-backfill`. Not run on the
server yet: every number below that is not a price from Helius's page is a formula or an assumption, and says so.

## Goal

Big wallets (Ansem, frankdegods, Remus, Gake) buy coins that are hours or days old. Those coins trade on PumpSwap.
The recorder keeps a coin's PumpSwap trades for 30 minutes after it leaves its pump.fun curve (`[live]
amm_recent_secs`), and nothing after that. So a rule like "copy a big wallet's buy above 1,000 SOL of market cap"
cannot be scored: the buy and everything after it are not on the tape.

The backfill fetches exactly the missing piece from Helius: each big wallet's PumpSwap buys, and every trade of that
pool from a minute before the buy to an hour after it. A lab can then score those moments like any other.

## What was wrong before (fixed here)

A wallet rule (`kol` trigger) whose buy was on PumpSwap was scored on whatever the tape happened to hold. Outside the
30 minutes after the migration that is almost nothing. The position then saw no trade after its buy. Every exit sold
at the entry price. The result was only the fees, and it looked like a real (small) loss.

The test's coin shows the size of it (a made-up coin, a copy 3 buys behind the wallet, 0.2 SOL, 0.0035 SOL a
transaction). Without the later trades every exit read -0.0119 SOL. With them the same copy made +0.0759 SOL.

Now the lab scores such a moment only where the tape holds the stretch whole: inside the recorder's 30 minutes, or
on a backfilled stretch. Every other one is left out and counted under the reason `no_backfill`, in the lab's
`amm_tape` report. Test: `tests/test_lab_backfill.py`.

This changes old numbers too. The qualify lab uses the same code, so a wallet's PumpSwap buys past the recorder's
window no longer count as small losses in its verdict. They are left out until a backfill holds them.

## Data flow

`snipetech history amm-backfill --wallet W [--wallet ...] [--group big] --days D [--after-secs 3600] [--min-sol 1]
[--dry-run] [--max-credits N] [--json]`

Step 1, per wallet: its PumpSwap buys.

1. `getSignaturesForAddress(wallet)`, newest first, 1,000 a page, back to the cut-off.
2. `getTransaction` for each one that did not fail on the chain.
3. Each transaction goes through the recorder's own decoder (`solana/tx.extract_from_transaction`, then
   `solana/normalize.normalize`). No second decoder exists.
4. The wallet's own PumpSwap trades are written to `trades`. The pool of each buy is kept in the coin's coverage
   record.

The cut-off is `--days` ago, or the start of the tape if that is later. A lab reads trades only inside the tape's own
stretches (`coverage: tape`), and the nightly prune deletes older rows, so older history could be stored but never
scored.

Step 2, per buy of at least `--min-sol`: the pool's trades around it.

1. The stretch is 60 s before the buy to `--after-secs` + 30 s after it. Stretches of one coin that overlap are
   joined. Parts already held are left out.
2. `getSignaturesForAddress(pool)` starts from a signature the tape already holds just after the stretch (the RPC
   accepts any signature as the `before` mark), and pages back until it passes the start of the stretch.
3. `getTransaction` for each, decoded as above. The pool's reserves after each trade come from the PumpSwap event
   itself, as on the tape. The place in the block is the transaction's own index (`transactionIndex`), the same
   number the recorder writes.
4. Rows go into `trades` with `INSERT OR IGNORE`: a trade the tape already holds is never written twice or changed.
5. Only when every transaction of the stretch was fetched is the stretch added to the coin's coverage record.

A stretch is skipped, and counted, when: the recorder's own PumpSwap tape already holds it (`on_tape`); it ends less
than 30 s ago (`buys_too_recent`); the buy is outside the tape's stretches (`buys_outside_tape`); the tape has no
signature within 30 minutes after it to start from (`no_cursor`); or the pool's listing does not contain the
wallet's own buy (`listing_incomplete`). The last one is a self-check: the listing starts from a signature of
another transaction, and if Helius answered that differently from what the repo assumes, the stretch would have
holes. Such a stretch is not fetched and not claimed.

A stretch with a transaction that could not be fetched, after a second try, keeps its rows but is not claimed
(`incomplete`). The next run fetches it again.

## Tables written

| where | what |
|---|---|
| `trades` | the same rows the recorder writes (venue `amm`), with payer, priority fee and tip |
| `pools` | pool -> coin, for the pools the wallets bought in |
| `coverage`, key `tape:amm_backfill:<mint>` | `{"pools": [...], "spans": [[from, to], ...]}`: the stretches held whole |
| `coverage`, key `amm_backfill:wallet:<wallet>` | where the scan of this wallet stands (newest and oldest signature read) |
| `coverage`, key `api_usage:helius-backfill:<month>` | credits this job counted in the month |

Writes are short: rows go in through `Store.add_trades` (500 rows a transaction), after each batch of 200 fetched
transactions. No write transaction is open during a network call. Guard test:
`test_no_write_holds_the_database_while_the_backfill_runs`.

## How the lab reads it

`amm_tape.backfill_spans(store)` gives `{mint: spans}`. `lab.collect` loads it once when a wallet rule is among the
triggers. In `_coin_rows`, a wallet rule's moment whose buy lands after the coin left its curve needs the tape from
the followed buy to the entry plus the longest exit plus one slot. It counts when a backfilled stretch holds that,
or when the recorder's 30 minutes hold it. Otherwise it is skipped as `no_backfill`.

The lab reads the read-only copy of the database (taken every three hours). A backfill shows up in a lab after the
next copy.

With `--after-secs 3600` (the default) every exit up to an hour is covered. An exit that holds longer than
`--after-secs` leaves every backfilled moment out.

## Resume and limits

- A second run continues: the wallet record says how far the scan got, and the coverage records say which stretches
  are held. Buys already found are read back from `trades`.
- Rate: the repo's one limiter (`SolanaRpc.limiter`, `[rpc] max_rps`; a batch job on the paid endpoint runs at 120 a
  second of the plan's 200). A 429 halves the rate.
- `--max-credits` (default 1,000,000, one percent of the month's plan): the job stops before the request that would
  pass it and says so (`stopped: max_credits`). A stretch is never started unless all of it fits.
- A limit for the month: all backfills together count at most 30,000,000 credits a month (30 % of the plan). Every
  run adds what it counted to one number as it goes, so a killed job is counted too. A run that reaches the limit
  stops and says so (`stopped: month_credits`). Only the owner's `--month-credits` raises it.
- An agent may start it (`start_job`: `history amm-backfill` only, no other `history` action), with at most
  5,000,000 credits a job and never with `--month-credits`. The month's limit is what keeps a loop of agent runs from
  spending the plan.
- On the server it is a job like `place-bids`: allowed by `remote run`, run below the recorder's priority and at the
  batch RPC rate. `remote update` waits for it. It imports all its code when it starts and goes on where it stopped,
  so `remote update --force` during a long run loses nothing.

## Cost model

Credit prices sit in one table, `amm_backfill.PRICES`, each with where it comes from:

| request | credits | from |
|---|---|---|
| `getSignaturesForAddress` (up to 1,000 signatures) | 1 | Helius docs, billing/credits page, read 12 Nov 2026 |
| `getTransaction` | 1 | the same page ("historical data queries ... 1 credit each") |

The page was read by a page-reading tool, not by hand, and not checked against the account's own usage page. The
first real run must compare the two (see Checks). A price that is only a guess is printed with the word "assumed".

Cost = pages + transactions. Transactions are nearly all of it.

- Step 1: one credit per transaction the wallet made since the cut-off.
- Step 2: one credit per transaction of the pool inside each stretch.

A worked example, with assumed sizes (no run has measured them yet): 10 wallets with 3,000 transactions each in 7
days is 30,000 credits. If one transaction in 20 is a PumpSwap buy of 1 SOL or more, that is 1,500 stretches. If a
pool has 5,000 transactions in an hour, that is 7.5 million credits: 7.5 % of the month's 100 million. At 120
requests a second it takes about 17 hours. A hot coin can have ten times as many trades in its hour; a quiet one a
tenth. A run that size needs `--max-credits 7500000`: the default stops at one million.

`--dry-run` replaces the guesses with counts. It lists signatures, fetches no transaction and writes no tape data.
The listing is paid for: one request for every thousand signatures, about 37,000 requests for seven days of a wallet
that failed transactions name 110,000 times in half an hour. Since 12 Nov evening those requests are added to the
month's number like a real run's (before that a dry run was left out of it). It prints, per wallet, the
transactions still to fetch and the buys found so far; for the buys already
known, the exact number of pool transactions; for the buys not found yet, an estimate marked `assumed` until a run
has measured this wallet's own rates.

## What it cannot do

- Nothing older than the tape. Today a lab cannot read outside the tape's stretches.
- Only the pool the wallet bought in. A coin with a second PumpSwap pool has that pool's trades missing. Trades on
  other exchanges (Raydium, Meteora) are never read.
- A wallet's buy routed through a program that hides the wallet (the event names another account) is not found.
- It does not record the landing. A paper result on these stretches still has to name its landing and the bid that
  buys it (CLAUDE.md rule 2). A big wallet's block is crowded: the "+6 regime" applies.
- The forward paper test (`paper.run`) is not changed. It still prices a wallet rule's PumpSwap buy on what the tape
  holds.
- Helius has a newer request, `getTransactionsForAddress`, that the docs page prices at 10 credits per 100 whole
  transactions (a tenth of this) and that filters by time. It is not used: there is no recorded answer in the repo to
  build against. Worth a look if the first run shows the cost matters.

## Checks on the server (not done)

1. `remote --wait run history amm-backfill --group big --days 2 --dry-run --json`: the counts are plausible and the
   job wrote nothing.
2. One wallet, small: `remote --wait run history amm-backfill --wallet <W> --days 1 --max-credits 20000 --json`.
   Look at: `credits.counted` against the change on Helius's usage page (this settles the two prices);
   `stretches.fetched` above 0; `listing_incomplete` and `no_cursor` at 0 (either one above 0 means the listing
   from a foreign signature does not work on Helius as the repo assumes); `wallets[].missing` at 0.
3. `remote sql` on one backfilled coin: rows in the stretch, `pos` values like the tape's (the block index, in the
   hundreds or thousands, not 0, 1, 2), reserves that chain from one trade to the next, and the coverage record
   `tape:amm_backfill:<mint>`.
4. The recorder's lag during the run (`remote status`), and that no write waited (the job's log has no "held the
   database's write lock" line).
5. After the next copy of the database (every three hours), a lab with
   `{"kind": "kol", "group": "big", "sol": 1, "min_mcap": 500}`: its `amm_tape` report shows moments scored and the
   rest under `no_backfill`.
6. The qualify lab's next run: how many candidates' moments are now left out as `no_backfill` (they used to count as
   small losses).

## The first real run (12 Nov 2026, 19:02 UTC)

`history amm-backfill --wallet 4Hq9UkSX… (Gake Side) --days 2 --after-secs 300 --max-credits 20000`: 585 wallet
transactions read, 21 PumpSwap buys of 1 SOL or more, 19 stretches fetched and 1 already on the tape, 18,824 pool
transactions, 3,937 trades written, 19,453 credits counted, about 3 minutes. The wallet's PumpSwap trades are in
`trades` afterwards (24 buys, 59 sells in two days), most with a fee record.

- Cost: about 1,000 credits a buy with 300 s after it. A dry run with an hour after it listed 113,466 transactions for
  3 stretches: about 38,000 a buy. `--after-secs` therefore defaults to 300 since this run (the lab's exits hold 300 s
  at most); section "With `--after-secs 3600` (the default)" above describes the default before it.
- A dry run counts too few buys before any transaction is read (3 against 21): its estimate is a floor.
- A wallet named by many failed transactions is slow to list, not dear: Cupseyy's main wallet had 111,847 signatures
  in half an hour, 218 of them successful. Seven days of it are about 37,000 list requests and over an hour.
- Not checked: the credits counted against what Helius charged (only the account's own page shows that).
- The agents may start a backfill since this run (`operator.jobs.AGENT_BACKFILL_OPEN = True`).

## Changed after the review of 12 Nov evening

The analyst's first run (ten big wallets, 3 days, 300 s after each buy, limit 400,000 credits) spent 392,543
`getTransaction` requests on 12 stretches: about 33,000 a stretch, where the first real run measured about 1,000. Two
stretches explain it. One "coin" was USDC: a big wallet swapped into it through a PumpSwap pool. One coin had 48,689
trades in 20 minutes.

- A buy of something that is not a coin (wrapped SOL, USDC, USDT, USD1: `positions.NON_COIN_MINTS`) gets no stretch.
  The result counts it under `stretches.buys_non_coin`.
- A stretch whose listing holds more than 8,000 transactions is listed and not fetched (`--max-stretch-txs`, default
  8,000; an agent may give up to 50,000). Listing costs one credit for every thousand signatures, so the size is known
  before a transaction is paid for. The result counts it under `stretches.too_busy`, names each one in `too_busy`
  (coin, seconds, size, buys) and says in `note` what they would have cost. The dry run's estimate leaves them out
  the same way. A stretch of an hour holds about 38,000 transactions, so a run with a long `--after-secs` has to
  raise the limit.
- The command line reads no shortened flag any more (`allow_abbrev=False` on every parser). An agent had been able to
  lift both credit limits with `--max-cred 9e9 --month 1e12`: the agents' check looked for the full spellings and the
  command line took any prefix. The check now refuses any shortening of a limit flag, and a value that is not a
  plain number (nan, inf).
- A run that stopped at `max_credits` may be started again at once by an agent with the same command. Before, the
  same command was refused for six hours. A dry run is not (it keeps no place), and a run that stopped at
  `month_credits` waits until the month's limit has room again.
- A lab that sells in parts holds longer than 300 s: `--sell-parts 4 --sell-part-secs 15` holds 345 s. A backfill at
  the default 300 keeps 330 s after a buy, so that lab scores none of its moments. Backfill with `--after-secs 360`
  for it; the second run fetches only the missing seconds.
- Not done here: the rows the first run wrote for USDC stay in `trades` and its coverage record stays. The text form
  of the result (`report.render_amm_backfill`) shows the two new reasons only through `note`.
