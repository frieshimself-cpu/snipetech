# snipetech — the loop, in one picture

Obsidian renders this diagram; the boxes link to the documents that define them.

```mermaid
flowchart LR
  subgraph Sources
    H[Helius LaserStream<br/>every pump.fun trade]
    ST[SolanaTracker<br/>30/90-day boards, first buyers]
    BE[Birdeye<br/>gainers, top traders, wallet PnL]
    VY[Vybe<br/>30-day board]
    PF[pump.fun feed<br/>KOTH, live, trending]
    TG[Telegram channels<br/>callouts via Jev]
  end

  H --> T[(tape: trades, launches)]
  PF --> E[(events)]
  TG --> E
  H --> E

  ST --> D[discover<br/>hourly]
  BE --> D
  VY --> D
  T --> D
  D --> C[(candidates)]

  C --> Q[qualify<br/>gates where copies land<br/>and on the tape after the pick<br/>one lab at a time]
  T --> Q
  E --> Q
  Q --> V[(verdicts)]

  V --> B[book<br/>every 5 min when enabled<br/>bounded by the owner's numbers]
  B --> OK[OKTech snipers<br/>bid, tip, CU, send count]
  OK --> CH[Solana]
  CH --> M[measure<br/>position in slot, gap, fee, PnL]
  M --> F[(fills, bid model, live stats)]
  F --> Q
  F --> B

  F --> O[operator every 15 min<br/>analyst daily, strategist weekly]
  V --> O
  E --> O
  O --> N[notes.md + reports]
  O --> TGB[Telegram: decisions, alerts, digest<br/>commands /status /stop /resume]
  O --> R[resources: credits, CU, requests, SOL, disk]
```

Documents: [[roadmap]] (where we stand), [[live-recipe]] (every live test), the design in
`superpowers/specs/2026-11-06-learning-loop-design.md`, the plans under `superpowers/plans/`, [[birdeye]], [[jev]],
[[oktech-features]], [[oktech-settings]]. Live state: the dashboard at `/dash` on the server (phase 1, task 6).

## Which wallets the book may copy: verdicts scored where copies land, and checked after the pick (7 Nov)

Research round 2 found that copy verdicts did not carry forward: the 27 wallets the verdicts and the 14:00 search rated
positive lost 0.0147 SOL a copy on the tape recorded after their verdicts (257 copies), after making +0.063 a copy in
the 6 h before them. The verdicts caught hot streaks that then stopped. So the qualify stage now asks three more things
of a wallet before the book may copy it. First, its copies are scored where live copies land: behind the 3rd buy after
the leader's, whatever slot that buy is in ("+3x"), not up to six trades behind it inside the leader's own slot ("+6"),
which put a leader alone in its slot right behind its own buy. Only a worse landing measured live moves it, never a
closer one. The exit is the one OKTech runs: sell after 1 s without a trade, at -30 %, after 300 s, or when the leader
sells half its bag. Second, a wallet with 10 % or more of its first buys in a coin's create slot is not copied: two of
the search's best rows were such artifacts. Third, the forward check: a wallet must also pass on its own copies after it
was first picked, at least 20 of them over at least 6 h of later tape, with a lower bound above 0. Copies that lose
there retire it. Until then it is "forward pending" and not copied; 16 of 24 verdict wallets never bought again within
9-13 h. Each verdict carries these numbers (`forward`, `create_slot`). Nothing in the book changed: it still copies only
qualified wallets. A wallet it copied before these rules is not stopped by them: its verdict stays the one the book
armed it on and says it no longer qualifies (`kept_armed`); whether it goes is the owner's decision. The measure stage
now records, for each live copy, where on its curve the leader bought (`fills.leader_progress`), next to the trades
behind and the price gap, so live landings can be read by curve progress (7aPo3n, buying at 79-93 %, was copied 0-1
trades behind).

## One database, many writers: every write is a short transaction

Every process on the server writes the same SQLite file (`snipetech.db`, WAL mode): the tape recorder about 100,000
trades an hour, the operator rounds and their jobs (events scan, qualify, the strategy search, the paper test, measure,
book, discover, the strategist). Readers never wait, but only one connection writes at a time, and the recorder waits at
most 20 s for its turn: on 7 Nov a lock held too long, then a refused write left open, stopped the tape for 1 h 45 min
(14:03-15:48), and a paper run that had waited 30 s for the lock died (14:06). So every writer goes through
`Store._write` and commits in short transactions: one row, one trade, one wallet, or a batch of `Store.WRITE_BATCH`
(500) rows, never one transaction across a lab, a network call or a whole table. A write that fails is rolled back at
once (an open transaction keeps the lock and pins the WAL); a refused one is retried for 10 minutes (20 s in the
recorder). The recorder's hourly prune clips the tape's coverage first, then deletes 500 rows per transaction as a task
of its own, the recorder going on in between (measured on a 2.4M-trade copy: another writer waited 2.0 s while one
transaction pruned an hour of tape, 12.4 s for six hours, and 0.08 s at most in batches; the server's database is ten
times larger). Each step of the prune reads at most `Store.PRUNE_SCAN` (5,000) rows, from where the last step stopped,
so a prune reads each old row once, as the single DELETE did. The first batched prune searched again from the oldest row
for every batch: across the trades a list protects, which pile up below the cutoff every day a wallet stays listed, and
through `seen_sigs`, which has no index on ts. Beside 199k protected trades an hour's prune took 43 s of the recorder's
loop, beside 4M 19 minutes in steps of 6 s; now 17 and 27 s in steps of 0.1 s (the single DELETE: 4 and 10 s, the lock
held throughout). Most of what is left is the WAL checkpoints its commits run: 3.3 s instead of 15.8 s with them off,
for an hour with no pile. A reader mid-run sees some of the batches: rows that each stand alone (trades, events,
fingerprints, paper trades, verdicts), so nothing it reads is half made, and during a prune a tape that starts at the
cutoff with some older rows still outside it. A job that dies leaves whole batches, never half of one, and its next run
writes the rest. A new index on a big table, or a table rebuilt, holds the lock for the whole build: run it once by hand
at a quiet hour, never as a line in `SCHEMA` that every process runs when it opens the database.
`tests/test_short_writes.py` guards the rule: a second connection writes while each writer is mid-run.
