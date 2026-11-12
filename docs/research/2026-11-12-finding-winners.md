# Finding the winning bots and big wallets without being handed them (12 Nov, 17:00 UTC)

**The owner, 12 Nov:** "You need to find these people, me having to give you his wallet is insane. For next wallets
you have to find them and the person. Practice on this one, look at pumpfun leaderboards."

**Answer in three lines.**
1. The system can now rank every busy wallet on our own tape by the SOL it actually made, and say which ones are bots.
   The command is `winners`. The server's own wallet discovery asks it every six hours, so nobody has to start it.
2. Public leaderboards give the **people** (names, X accounts). Two of them can be read today without a login:
   pump.fun's own KOL board (kolscan.io) and GMGN's official API. Neither lists the bots: none of the owner's three
   bots is on kolscan. The bots come from our tape; the names come from the boards.
3. It is built and tested here. **It has not run on the server yet**: nothing was deployed, by the rules of this task.

Everything below says where a number comes from. A number from a test market or from this machine is marked as such.

## Part 1: what was built

### What `winners` does now

It reads the tape twice, by time, and follows every wallet that bought and sold at least 20 times in the window.
For each of them it closes **round trips**: one coin bought and sold back to nothing. A second buy after that is the
next round trip. An open position is not a result and is shown apart.

Each wallet gets these numbers:

| number | meaning |
|---|---|
| SOL made | what its sales brought, less what its buys cost, less the venue's fee, less its priority fees and tips (the tape has those since 11 Nov 16:34 UTC) |
| SOL a day, round trips a day | over the days of tape the read covers |
| win | the share of round trips that made SOL |
| lower bound a trip | the bootstrap lower bound of the SOL one round trip makes (80 %, one-sided). Under zero means the total may be luck |
| hold, buy size | medians |
| behind known | the share of its buys that land in the same block right behind a wallet we have a name for (copy-like) |
| behind big | the same, behind any buy of 1 SOL or more by anybody (6Z91 follows any large buy, with no wallet list) |
| at create | the share of its buys within 3 seconds of the coin's create (sniper-like) |
| open SOL | SOL in less SOL out on coins it still holds |
| kind | **bot**: 20 round trips a day or more, and a median hold of a minute or less, or 30 % of its buys right behind somebody or right after a create. Otherwise **trader** |

Left out: our own wallets, service and router wallets, a coin's own creator. A wallet that sells tokens it never
bought on the tape is marked "insider": that is supply handed to it, not a trade.

**The landing these numbers assume: each wallet's own place in the block, at its own bid.** They are not what a copy
of the wallet makes. A copy lands behind the wallet and pays more. `qualify` measures the copy at our landing.
`winners --label 20` hands it the 20 best, and the discovery source hands it up to 100 every six hours.

### What changed in the code

- `src/snipetech/analysis/winners.py`: rewritten. The old version asked the tape once for every coin launched in the
  window. On 12 Nov that is 42,000 coins a day. It also saw only coins launched inside the window, so a bot's trips
  in older coins were missing. It charged no landing fees. It could not tell a bot from a trader.
- `src/snipetech/store.py`: `trade_pieces` (the tape in ten-minute pieces off the time index) and `label_kinds`.
- `src/snipetech/cli.py`: `winners --min-trips 20 --min-sol-per-day X --kind bot|trader --top N --label N --json`.
  Without `--json` it prints a table (`report.render_winners`).
- `src/snipetech/discovery/sources.py`: a new discovery source, `winners`, asked every six hours. This is the part
  that makes the system find them by itself. Its wallets wait first in qualify's queue.
- `src/snipetech/operator/tools.py`: the agents are told how to start it and how to read it.
- `--label` no longer overwrites a name. Before, labelling a winner that was already a KOL turned its label into
  `cand`. Now a wallet with a name keeps it.
- Tests: `tests/test_winners.py` (18 tests). A test market with a winning copy bot, a losing launch sniper, a slow
  trader, a bag holder with an open position, our own wallet and a router.

### Is it cheap? Measured here, not on the server

The server's tape on 12 Nov: 335,000 trades an hour (read from the server), 107,000 wallets in six hours, 4,900 of
them with 40 trades or more.

A made-up tape of that size on this machine (6 hours, 2.0 million trades): **15 seconds, 220 MB of memory.** 3.7 s of
that is the count of every wallet's trades, 6.9 s is reading the rows, the rest is the arithmetic.

72 hours is 24 million trades. At the same pace that is **about 3 minutes**. This is an estimate. The server's disk is
slower when the file is not in memory, and the real tape has not been read by this code. The first run on the server
will say.

### What it says about the owner's three bots (a small real sample)

This is a check of the code on real rows, not a result. I read each bot's newest 500 trades from the server (the
read-only query stops at 500 rows) and ran the finder on them. Read at 16:50 UTC, 12 Nov. "Behind known" and "behind
big" cannot be judged from this sample: it holds only the bots' own trades.

| wallet | sample | round trips | SOL made after fees | fees paid to land | win | lower bound a trip | hold | buy | within 3 s of the create |
|---|---|---|---|---|---|---|---|---|---|
| 6Z91Dgiz | 5.0 hours | 96 | **+19.0** | 4.77 SOL | 54 % | +0.137 | 7.5 s | 2 SOL | 55 % |
| Fhbh1DTU | 9.6 hours | 159 | **-9.1** | 2.56 SOL | 20 % | -0.072 | 7 s | 0.9 SOL | 8 % |
| 7iyYn4gs | 158 hours | 13 | -0.36 | not known for most | 62 % | -0.261 | 14 s | 5 SOL | 29 % |

Read with care. 6Z91 made 19 SOL in five hours on our tape. Fhbh1 **lost** 9 SOL in its last ten hours. One window
does not make a wallet good or bad: the full 72-hour run on the server decides. 7iyYn4 trades too rarely for
`winners` at its default (13 round trips in a week; the default asks for 20 in 72 hours).

**What 6Z91 pays to land (same five hours, the server's tape).** This is the owner's point 2, "it all comes down to
fees", in numbers:

| | trades | average bid (priority fee + tip) | highest bid | average size |
|---|---|---|---|---|
| 6Z91 buys | 60 | **0.097 SOL** | 2.6 SOL | 2.6 SOL |
| 6Z91 sells | 231 | 0.00015 SOL | 0.008 SOL | 0.79 SOL |
| Fhbh1 buys | 117 | 0.0079 SOL | 0.25 SOL | 1.09 SOL |
| Fhbh1 sells | 175 | 0.00016 SOL | 0.002 SOL | 0.79 SOL |

40 of 6Z91's 60 buys bid 0.01 SOL or more. 8 bid 0.05 SOL or more. Our bid is 0.003 SOL. It bids on the way in and
almost nothing on the way out. The winning bot bids twelve times what the losing one does.

## Part 2: who lists the winners in public, and can we read it

What the server already asks (so nothing here is built twice): SolanaTracker's whole-market leaderboard and first
buyers, Birdeye's weekly and monthly gainers and the top traders of hot coins, Vybe's 30-day board. They are in
`src/snipetech/discovery/sources.py`. The record on them (roadmap, 8 Nov): of 19 SolanaTracker leaderboard wallets, 18
made no pump.fun buy in two days of tape. Paid whole-market boards rank people who trade elsewhere.

Each row below was tried with a real request from this machine on 12 Nov unless it says otherwise. No login and no
key of the owner's was used. Two services publish a demo key in their own documents; those were used.

| source | can it be read without logging in | what it gives | limits and cost | key on the server | already wired |
|---|---|---|---|---|---|
| **kolscan.io** (pump.fun's own KOL board; its pictures are served from `kolscan-images.pump.fun`) | **Yes, tried.** `GET https://kolscan.io/leaderboard` answers 200 with the board inside the page. Its paging address (`/api/leaderboard`) answers "Forbidden" | 373 wallets with name, X account, Telegram. For about 47 wallets each: profit in SOL, wins and losses over 1, 7 and 30 days | free. One page a read. No stated limit. It is a web page, not an API: its layout can change | none needed | **no** |
| **GMGN official API** (`openapi.gmgn.ai`) | **Yes, tried**, with the read-only demo key GMGN publishes. `GET /v1/user/kol?chain=sol&limit=100` and `/v1/user/smartmoney` answered 200 | the newest 100 trades by wallets GMGN tags KOL or smart money: wallet, X name, tags (kol, smart_degen, wash_trader, arbitrager, and the app it trades through: axiom, bullx, padre), coin, side, size. `/v1/user/wallet_stats` gives a wallet's 7-day profit, buys, sells, win rate. `/v1/market/token_top_traders` gives a coin's top traders | free plan: 5 requests a second. A key of our own is made at gmgn.ai/ai (it wants an Ed25519 public key; IPv4 only). 100 trades covered 8.6 minutes and 16 wallets when tried | **no** | **no** |
| GMGN's old web address (`gmgn.ai/defi/quotation/...`) | **No.** 403, Cloudflare | - | - | - | no |
| **pump.fun** itself (`frontend-api-v3.pump.fun`, `pump.fun/leaderboard`) | **No.** 403, Cloudflare "Just a moment", from this machine | not seen | its analytics API wants a login token (documented by others; not tried) | no | no |
| **Axiom** | **No.** `api.axiom.trade` answers 425 without a login | the owner's own tracked-wallet list is what we have from it (2,630 wallets, exported by hand on 10 Nov) | login only | no | by hand |
| **SolanaTracker KOL board** | Needs the key (401 without). **Not tried with the key**: I may not read it | `GET https://data.solanatracker.io/v2/pnl/leaderboard/kols?sort=realized&direction=desc&limit=100` and `/kols/period?period=7d`: named KOLs with X account, realized profit, win rate | inside the plan the server already pays for | **yes** | **no** (only the whole-market board is) |
| **Birdeye** | Needs the key (401 without) | `/trader/gainers-losers` (weekly, monthly), `/defi/v2/tokens/top_traders` | paid plan, counted in units | **yes** | **yes** |
| **Vybe** | Needs the key (401 without) | `/v4/wallets/top-traders` | free board | **yes** | **yes** |
| **MadeOnSol** | **Yes, tried**, with its published demo key: `GET https://madeonsol.com/api/v1/alpha/leaderboard?period=7d&limit=100` answered 200 | wallets ranked on pump.fun and LaunchLab coins: net profit in SOL, wins, losses, buys, sells, last seen. It has a switch `exclude_bots` (on by default). A KOL board (`/kol/leaderboard`) needs a free key | demo key: 20 calls an hour. Free key: 200 calls a day. Paid from 43 euros a month | no | no |
| **Dune** | Needs a key (401 without) | community tables of pump.fun traders; quality depends on who wrote the query | free key exists; not tried | no | no |
| **DexScreener** | Its open API answers (200) but has no list of traders | - | - | - | no |
| **FOMO app** | No public API was found. Not tried further | its users are on our tape already (`fomo-wallets`) | - | - | from the tape |
| Bitquery, Codex, Cielo, Nansen | not tried: each wants a paid key | wallet rankings by query | paid | no | no |

### What the kolscan board holds that we do not

Read at 16:45 UTC and compared with the server's labels:

- 373 wallets on the page. **170 already have a label on the server** (161 from the owner's Axiom list, 7 KOLs, 2
  candidates). **203 do not.**
- 82 wallets are on a 1, 7 or 30-day ranking. 63 of them we know. **19 we do not.**
- Of those 19, **15 traded on our tape in the last 55 hours**, between 13 and 723 trades each.
- The five best of the week are all on the owner's list already (Chairman +2,916 SOL, gr3g +1,250, Schoen +778,
  West +626, Dior +616). So the owner's list is good at the top. The gap is further down.
- None of the owner's three bots is on the page. A KOL board lists people. Bots are found on the tape.

Be careful with the board's profit. It is the wallet's own result over a week, with a few big wins. Chairman's week is
27 wins and 56 losses. On 8 Nov seven KOLs were scored as copies at our landing and none qualified.

## What to wire next (recommendation)

1. **kolscan.io as a discovery source, and as the source of names.** One request every six hours:
   `GET https://kolscan.io/leaderboard`, read the rows `{"wallet_address","name","twitter","profit","wins","losses",
   "timeframe"}` out of the page. It costs nothing and needs no key. It gives 203 named wallets we lack today, 15 of
   them active on our tape and ranked. Each goes to `candidates` (source `kolscan`) and gets its name and X account
   as a `kol` label, so `winners` can say "behind known" for them and the owner sees a person, not an address.
   Risk: it is a web page. The source must fail loudly when the page changes, not return nothing.
2. **GMGN's official API for the tags and the live KOL feed.** `GET https://openapi.gmgn.ai/v1/user/wallet_stats
   ?chain=sol&wallet_address=<wallet>&period=7d` on every wallet `winners` lists gives a name and GMGN's own tags
   (kol, smart money, wash trader, arbitrager, which app it trades through) for a wallet we found ourselves. That is "find the wallet
   and the person" for the wallets the tape finds. It needs a free key of our own: the owner makes one at gmgn.ai/ai.
   The demo key is for trying, not for a service.
3. Cheap third: the **SolanaTracker KOL board** (`/v2/pnl/leaderboard/kols`). The server already pays for the key.
   It likely overlaps kolscan; compare before keeping both.

Not recommended: Axiom (login), pump.fun's own API (blocked, wants a login), the paid query services.

## What is not done

- **Nothing ran on the server.** No deploy, no job. The first real ranking needs the code deployed and
  `winners --hours 72 --json` run once. Its run time and memory there are estimates until then.
- The two outside sources above are **researched, not wired**.
- A transaction that trades for two wallets at once has its landing fee charged to each of them (452 of 331,000
  transactions in one hour of tape: 0.14 %).
- "Behind known" also counts a wallet's own companion wallets. `leaders-of <wallet>` names who it follows.
- The creator of a coin older than a day before the window is not recognised as its creator.
- The bot rule (20 round trips a day and so on) is a first cut. It was tried on a test market and on three real
  wallets, not tuned on the whole tape.
