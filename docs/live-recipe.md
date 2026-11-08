# The live recipe (forward-tested on paper, 2026-11-05)

All numbers are paper trades on market data recorded **after** each strategy was frozen, simulated at the
landing position OKTech was measured at (same slot as the trigger, ~1 trade in that coin ahead of us),
0.2 SOL per buy, fees and tips included (0.0035 SOL per transaction).

## The owner, 7 Nov 2026 about 22:05 UTC: "pnl matters but dont stop the bot"

In their words: "no there's no sol checks idc ab it", then "no as in pnl matters but dont stop the bot", then "just make
it understand why it lost and how to do better". So the profit and loss is what counts, a loss should teach the bot
something rather than stop it, and the wallet's balance stays out of the reports.

- The one stop of the whole bot left is the guard's 5 SOL day stop (operator/RISK; the floor has been off since 12:55).
  The cloud session's safety layer refused to switch it off from here (`remote run operate risk --day-stop off`,
  22:06), so it stays the owner's switch: `/risk stop off` in Telegram.
  The owner, about 22:12: "Ok we can leave it at that. The bot has like 3 sol in it": the day stop stays as it is (with
  about 3 SOL in the wallet a 5 SOL day stop cannot trip).
- The learning: the post-mortem already names a cause for every lost trip and writes lessons and ideas; a build started
  22:10 (loss-learning) breaks every live trip into price move, fees and failed duplicate sends in code, puts those
  numbers in the post-mortem, and sends the owner a short summary after each batch (built on branch `loss-learning`,
  not deployed yet; docs/architecture.md says what it computes). Tonight's three copies moved +0.0294 SOL before any
  cost and ended -0.0119: pump.fun's 1.25 % a side took 0.0152, the fees (0.001 priority + 0.002 tip on every buy and
  landed sell) 0.0180, and four failed duplicate sells at 0.002 each 0.0080. The +0.0142 and -0.0039 first written
  here were after pump.fun's fee and without the duplicates.

## AG89fn's first live copies lost −0.418 SOL in 10 minutes; the book paused it (8 Nov 2026, 13:06–13:20 UTC)

AG89fn, booked since 7 Nov 13:50 without a buy, made six copies at 0.2 SOL from 13:06 (landing 0-1 trades behind
at +0.4 to +0.9 %): 8t8x2s −0.1289, AJNiDe +0.0129 then −0.1776 (the leader bought it twice), CWojKR −0.005 and
−0.0092, 5pTLJV −0.1097: **−0.4175 SOL whole**, 1 won. Three of the six were copy exits at −55 to −85 %: the leader's
sell was the dump itself and our sell landed behind it. At 13:20 the book stopped AG89fn's sniper under its own
rule (6 live copies, mean −0.070, upper bound −0.032: `live_kill_n` 5), the wallet holding nothing. 7aPo3n's ninth
copy of the day (13:03, 5Wco3R −0.0461) brings it to +0.129 on 9 today. **The day's whole result at 13:30: −0.288 SOL
on 15 copies, 5 won.** Seven more exits fired for coins never bought (2WY37w, 6EtVCH, CiPBTK, 42rDoo): 0.013 SOL.
The 44 idle and finished OKTech tasks were deleted 13:06-13:38 (6 tasks left, 1 running).

## The duplicate sells explained and made free (8 Nov 2026, 12:50 UTC, 93e7177)

Each OKTech exit is two sends, not one send copied: a relay-sent sell carrying `sellTipAmount` as its tip and the
buy's priority (the one that lands, 47 of 49 on record), and a plain send with no tip at `sellPriorityFeeSol`, which
fails with pump error 6023 and keeps that priority fee (a failed send gives its tip back). We set both fields to
0.002, so every exit paid 0.002 for nothing: 0.008 of the 0.119 gross on the 12:10-12:20 trips, 0.172 SOL over the
5-7 Nov record. OKTech's owner told the owner the twins follow our settings; they do. Since 93e7177 every sniper's
`sellPriorityFeeSol` is 0 and the bid lives in the tip. The two running copy snipers keep their old settings until
the book replaces them (after their next buy, as always). To verify on the first sells after that: the failed twin's
`priority_lamports` in `fills` reads 0 and `twin_fee_sol` about 0.000005; a sell that never lands would show as an
unsold coin (the watch and `/sell` cover it). The owner, 12:35: "the agents should decide on the fees depending on
what the specific task is" (a fee policy per sniper kind is building).

## Live copies of 7aPo3n, 8 Nov 2026 11:30–12:20 UTC (the book, control arm, 0.2 SOL)

The first three "sells" (11:30 2WY37w, 11:32 6EtVCH, 11:44 CiPBTK) were not trips at all. Read from the chain
(12:55): the wallet held 0 of each coin ("NotEnoughTokensToSell, Left: 0"), no buy was ever sent, and OKTech still
sent two sells per coin, each failing and paying 0.002005 SOL: 0.012 SOL for nothing. So OKTech fires an exit for a
coin it never bought us (its buy was skipped or dropped while the leader's buy and sell were seen). Since 93e7177
such a send costs about 0.000005. A question for OKTech: why does an exit fire when the buy was never sent? The
next three copies landed on a recording tape, every buy 0 trades behind the leader at +0.5 %, and all three won:

| time (UTC) | coin | held | exit | price result | failed duplicates | whole result |
|---|---|---|---|---|---|---|
| 12:10:40 | 8LjhQY | 11 s | dead exit | +0.0124 | 1 (0.0020) | +0.0104 |
| 12:14:29 | 7Avk6P | 13 s | copy exit | +0.0380 | 1 (0.0020) | +0.0360 |
| 12:20:01 | EnzACy | 9 s | copy exit | +0.0690 | 2 (0.0040) | +0.0650 |

Whole result +0.1114 SOL on 0.6 SOL deployed, in 10 minutes; the duplicates took 0.008 of a 0.119 gross.

Five more by 12:43 (all 7aPo3n, 0.2 SOL, landing 0-1 trades behind at +0.4 to +4.1 %): 12:34 42X976 dead exit
−0.0247; 12:37 DatepQ dead exit −0.0403; 12:39 Bh3vMa dead exit −0.0218; 12:41 Gwje9b copy exit **+0.1807**
(0.2 → 0.389 in 24 s); 12:42 Gwje9b again (the leader bought it a second time) copy exit −0.0298. The eight copies
of 12:10-12:43 made **+0.176 SOL net** (4 won), the losers held to −0.02…−0.04 each by the dead exit and the
winners paid for them. 7aPo3n's live record (7 Nov and 8 Nov): 11 copies, 5 won, about +0.164 net, less the 0.012 of the three
phantom exits.

## Live copies of 7aPo3n, 7 Nov 2026 19:18–19:22 UTC (the book, control arm, 0.2 SOL)

The book's copy sniper on `7aPo3npv…bGXN8` (armed 16:05, qualified at the copy landing: n 35, +0.0271 SOL a trade,
lower bound +0.0049, a seller at a median 19.5 s) made its first three round trips. Nothing is held; after each buy
the book retired the sniper and armed a fresh one, so its spare buys are never needed.

| buy (UTC) | coin | landing | sell | exit | price before any cost | result (every cost in) |
|---|---|---|---|---|---|---|
| 19:18:49 | `8RJQ2bo2…pump` | 0 trades behind the leader, +0.5 % | 19:18:58, 0.1994 SOL | copy exit | +0.0044 SOL | −0.0106 SOL |
| 19:20:44 | `9QUt8h52…pump` | 0 behind, +0.5 % | 19:20:47, 0.2189 SOL | copy exit | +0.0241 SOL | +0.0088 SOL |
| 19:21:59 | `EZaNbQve…pump` | 1 behind, +0.8 % | 19:22:04, 0.1959 SOL | dead exit | +0.0008 SOL | −0.0101 SOL |

Net −0.0119 SOL over 3 trades (−0.0040 a trade against the forecast +0.027; three trades say nothing yet). The price
rose on all three, +0.0294 SOL before any cost (+5.0 % a trip); pump.fun's 1.25 % a side took 0.0152, the buys and the
landed sells 0.0090 each (0.001 priority + 0.002 tip + the base fee every time), and the first two sells each carried
two failed duplicate sends (Custom 6023, 0.002005 SOL each), 0.0080 in all. The −0.0039 first written here was the
landed trades alone: `fills.pnl_sol` keeps the duplicates apart in `twin_fee_sol`, and the post-mortem's sum left them
out (the loss-learning build counts them in every live result). The landing is the news: 0–1 trades behind at
+0.5–0.8 %, against 2–4 behind at +18–43 % for the earlier leaders' copies (the "+6 regime"). 7aPo3n buys late in the
curve (79–93 %, research round 2), where fewer bots race the leader.

## The owner's yes widened: live trading never stops (7 Nov 2026, about 12:10 UTC)

Told that 5–8 SOL is enough for the test but that their guard blocks every live trade under 10 SOL, and asked what the
floor should be, the owner wrote (in chat, word for word): "ngl ill just keep it at around 5-8 sol that's enough no?"
and then "Just make it so live trading never stops". The wallet held 3.51 SOL at 12:15 (the owner moved SOL out at
about 11:30 and 11:45; no trades). Wallet and size are unchanged: `6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at`,
0.2 SOL a buy, one coin at a time. What it changes, as built:

1. No wallet floor and no day stop: the wallet-wide guard never halts live trading (a buy still needs 0.2 SOL and its
   fees in the wallet).
2. The automatic test never turns itself off and has no limit on blocks or hours: a strategy that passes on paper
   trades block after block while its live result stays at least half of its paper result and above zero.
3. A strategy that falls below that, or is 1 SOL down live, is dropped, and the next strategy that passes on paper
   takes over. With none passing, the bot waits for one: it never trades a strategy without paper evidence (at our
   landing every Hunt setting loses about 0.007 SOL a trade).
4. What still ends a sniper is an execution fault (OKTech errors, a coin OKTech fails to sell), never a trading result;
   a sniper holding a coin is never stopped (CLAUDE.md rule 1).

At about 13:30 UTC the owner added, word for word: "bro it doesnt matter how much sol is in the wallet, however
much I put is how much im willing to lose just be smart about it yk?" So the wallet itself is the loss budget: no
floor and no day stop are the owner's settings, whatever the balance. Being smart about it, as run: only strategies
that pass on paper at the real landing and real costs, 0.2 SOL a buy (a round trip costs about 0.009 SOL whatever the
size, so smaller only loses), one coin at a time, a strategy dropped below half of its paper result or 1 SOL down, a
sniper holding a coin never stopped. A bigger size only after a strategy has proven itself live, and the owner hears
it before the first raise.

### What runs now (7 Nov 2026, 12:55 UTC)

- The guard and the automatic test of the 09:15 plan are deployed (c265fb7). At the owner's word above, the guard was
  set at 12:55 to a 5 SOL day stop (the most its bounds allow) and no floor (0 SOL): `operate risk --floor 0
  --day-stop 5`. The real off switch, the book following it, and an automatic test with no block limit are being
  built (branch never-stops).
- The automatic test is off until the owner sends `/test auto on` in Telegram: the cloud session's safety layer
  refused to switch it on from here, so the switch that lets the bot start live trading by itself stays the owner's.
- The sniper wallet held 0 SOL and 0.22 wSOL at 12:45 (the owner moved 2.80, 3.57 and 3.29 SOL to their wallet
  `GXcAHhvu…HRtQ` at 11:25, 11:31 and 12:45). A look-alike `GXcANzWb…HRtQ` sent it zero-SOL transfers at 11:27 and
  11:33 (address poisoning; the owner was warned).

### How the yes runs as code

Built 7 Nov on branch `never-stops`; it applies once deployed. The guard's off is the owner's own word in Telegram, as
every change of it always was: after the deploy, `/risk stop off` and `/risk floor off`.

**1. The wallet-wide guard can be off** (`operator/risk_guard.py`; the owner's values in `operator/RISK`).

- `/risk stop off` and `/risk floor off` in Telegram (the owner's chat only; a message more than 5 minutes old is
  refused), or `snipetech remote --wait run operate risk --day-stop off --floor off` from the cloud session with the
  owner's yes in chat. `/risk off` alone is not understood: each stop is named.
- The file keeps the word "off" with when it was said, and only that word means off: a missing value or a file that
  cannot be read falls back to the 7 Nov values, on (1 SOL a day, 10 SOL). `/risk default` puts both back on there.
  The numbers keep their bounds (a day stop of 0.1 to 5 SOL, a floor of 0 to 1000 SOL).
- A stop that is off never trips. With both off the guard reads nothing (no fills, no balance, no coins), never says
  it cannot tell, and blocks nothing; the cycler reads the wallet's SOL for it only while the floor is on.
- `/risk` and `/status` show each stop on with its value, or "off (the owner, <time>)".
- At 12:55 the owner set a 5 SOL day stop and a 0 SOL floor, the most the bounds allowed; those apply until the
  owner's off.

**2. The book follows the owner's guard** (`book/switch.book_stops`).

- The server's config file pins `[book] equity_floor_sol` 3.0 and `day_stop_sol` 1.0 (a dump of every default;
  changing one needs SSH, which the owner does not do). A value the owner set with `/risk`, a number or off, is the
  book's floor or day stop instead, value by value; with none, the pinned values apply as before. So the owner's 12:55
  values make the book's floor 0 SOL and its day stop 5 SOL.
- The daily cap of SOL bought (`daily_risk_sol`, 2 SOL: ten 0.2 SOL buys) halts the book for the day rather than
  sizing it, so it follows the day stop's off, and only its off: with the owner's day stop off the cap is off too; while
  the day stop is a number (the owner's 5 SOL of 12:55 was set for the guard, before the book followed `/risk`; a
  tighter `/risk stop 0.5` is no word to buy more) the pinned 2 SOL cap stays, and the automatic test's buys from the
  same wallet count toward it. With the floor off, a balance that cannot be read no longer halts it.
- What still halts the book: `/stop`, `/book off`, and the guard while it is on and holds. The per-wallet rules are
  unchanged (a wallet paused after 5 losing live copies, the bar, a sniper retired after its relied-on buy): each drops
  one wallet, and the book goes on. `/book on` says which stops apply.

**3. The automatic test never stops** (`operator/autotest.py`; the owner's switch in `operator/AUTOTEST`, the record
in `operator/autotest.json`).

- `/test auto on` and `operate autotest --on --approved "<the owner's words>" --wallet 6VFrZ15V…` write this plan. A
  switch on from the 09:15 plan runs it too (the owner's latest yes); a strategy's run in progress keeps its floor (what
  its first floor left below its start).
- A strategy that passes on paper (as on 09:15: 100+ forward trades where a hunt fill lands, at the measured cost of
  a trade, a lower bound above zero, also with gains capped) trades in blocks of at most 10 buys and 120 minutes (180
  at most), one coin at a time, at its own size, on the recorded wallet only. A strategy scored above 0.2 SOL a buy is
  refused (told once a day), and the best one that fits trades instead. There is no limit on blocks or hours.
- After every block, once the measure stage has read past it and no coin it bought is still in the wallet, its trades
  are counted as on 09:15: the measure stage's fills, read two ways (with and without the buys the fills call copies),
  the failed sends, sales covering 99 % of a buy's tokens, up to 45 minutes' wait for a buy's sale. A coin still held
  never counts at its whole cost: its block waits until it is sold (its sale counts) or gone (its cost counts 45
  minutes after a round last saw it), and a sale the measure stage writes late replaces a whole cost counted before.
- The strategy may lose 1 SOL by its own trades: the lower of the two readings' sums, failed sends included even in a
  block with no buy. At 1 SOL down, or so near it that another buy and its fees have no room left, it is at its floor
  and dropped, after any block, whatever its number of trades. Each block's floor is the wallet it starts from less
  what is left of that 1 SOL, and the cycler stops re-arming under it: with only the strategy trading it is the first
  block's floor, and a deposit, a withdrawal (11:30 and 11:45 that morning) or the book's result moves it with the
  wallet, never the strategy's loss. A block the cycler ended on its floor is judged on its trades like any other.
- Once it has 10 live round trips of its own (never sooner because the cycler counted 10 buys of the wallet: the
  book's are among them), it is judged after every block on the worse of the two readings: above zero and at least
  half of paper, the next block. Until then the next block starts, whatever ended the last one (its time limit is
  normal). Below half of paper, or at its floor, the strategy is dropped for good: the record keeps why and the
  numbers, the owner gets one line (live against paper), the automatic test never starts that strategy again, and the
  next one that passes on paper starts in the same round with a floor of its own. With none, it waits and looks again
  every round. A strategy whose run ends without a drop (the owner's off, its paper flickering for a round) takes its
  live record into its next run: its trades and what is left of its 1 SOL.
- An execution fault holds new blocks and never drops a strategy: a cycler that died (its sniper stayed armed with
  nobody watching until the round closed the block, and a buy it made then counts; no block in the round that finds it,
  one at the next round, and after a second death in a row none for 4 hours, then twice as long after each death in a
  row, a day at most, unless a deploy changes the code or the owner sends `/stop` then `/resume`), OKTech not answering
  or refusing a create, a coin OKTech left unsold (held until it is sold with `/sell` or gone), the wallet unreadable,
  the measure stage stalled 45 minutes past a block. Each round checks again; the owner hears it once when it starts
  holding and once when it clears, and the same strategy goes on. A wallet without room for a block holds the same way,
  said once a day: SOL under a buy, its fees and the coin account's rent (about 0.25 SOL; 10:59 that morning the wallet
  was emptied), a wallet too small for a floor of at least 1 SOL, or SOL without its wrapped SOL at or below the block's
  floor (the cycler would not start).
- It never turns itself off: only `/test auto off` or `operate autotest --off` does, and a block running then runs to
  its end. `/stop` holds every start while its flag exists (a block running ends at once, its sniper stopped once the
  wallet holds nothing; a sniper holding a coin keeps running until the coin is sold); `/resume` lets the same strategy
  go on if it still passes on paper. Both replies say so. The guard, while it is on and holds, holds it too.
- What the owner hears: a strategy starting, a drop, a hold starting and clearing, and a strategy's run ending without
  a drop (the owner's off, another wallet, a strategy that no longer passes on paper). Never a line per block or per
  round: the blocks' own lines are notes, and the day's blocks are in the daily digest. `/status` and `/test auto` show
  the strategy, its blocks and live round trips, live against paper, what holds it, and the strategies dropped.
- Recorded: each block in `operator/TEST.json` and `operator/tests.jsonl` as before, and one `"kind": "sequence"` line
  there per strategy's run, with `"dropped"` true or false, each block's floor and what the run carried from earlier
  ones.
- Reviewed before the deploy (7 Nov afternoon), and changed: the 1 SOL was read only at 10 trades and a deposit hid
  the rest; a withdrawal, the book's loss or the cycler's floor end dropped a strategy that had lost nothing; a dead
  cycler was replaced every round; a coin left unsold counted its whole cost after 45 minutes; a block of mostly the
  book's buys was judged on two of the strategy's own; a run that ended forgot its losses; and any day stop the owner
  set as a number lifted the book's 2 SOL daily cap.

## The owner's yes for the first launch-rule test (7 Nov 2026, 09:15 UTC)

Asked "What's the most efficient way to do this without rinsing the 10 sol?", the owner answered the plan below with
"Yes, use this plan" (09:15 UTC, in chat). The plan, as it was put to the owner:

1. A wallet-wide guard: no new live trade once the day is 1 SOL down or the wallet is under 10 SOL, changeable from
   Telegram.
2. When a strategy passes on paper (100+ new trades at the real landing, positive with outliers capped, at real
   costs), one live test starts straight away: sniper wallet `6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at`, 0.2 SOL a
   buy, one coin at a time, at most 10 buys, at most 2 hours, stopping on its own 1 SOL below the starting balance.
3. After 10 trades it continues only if live is at least half of paper (then 30 more trades, the same size, the same
   1 SOL stop counted from the first test's start); otherwise it stops and the analyst works out why.

Wallet at the time: 11.88 SOL, nothing held, no sniper running. Why 0.2 SOL and not less: a round trip costs about
0.009 SOL whatever the size, so a 0.02 SOL test can only lose. Results go in this file as they come.

### How the 09:15 yes ran as code (kept for the record; the widened yes above replaced it)

The guard is on from the start. The automatic test starts nothing until the owner's yes is recorded: `/test auto on` in
Telegram (that message is the yes, for the wallet `/test auto` names), or from the cloud session `snipetech remote
--wait run operate autotest --on --approved "Yes, use this plan (7 Nov 09:15 UTC)" --wallet
6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at`. `/test auto` shows where it stands; `/test auto off` turns it off.
The yes is recorded with the sniper wallet and the OKTech wallet it is for: while the config names another, nothing
starts by itself (the owner hears it once a day) and a sequence in progress ends.

**1. The wallet-wide guard** (`operator/risk_guard.py`; the owner's values in `operator/RISK`).

- On at 1 SOL a day and 10 SOL. `/risk` shows it with today's result; `/risk stop 1.5`, `/risk floor 9` and
  `/risk default` change it (`operate risk --day-stop N --floor N --default` does the same). The day stop is 0.1 to 5 SOL
  and the floor 0 to 1000 SOL. There is no off.
- The day's result is our own trading only: today's closed round trips in the measure stage's fills (real fees and
  failed duplicate sells included), plus the coins our buys still hold, at the tape's price. The fills lag by up to 5
  minutes and the cycler re-arms seconds after a sale, so our trades the fills do not have yet are read from the tape,
  and a coin bought today that has left the wallet with no sale seen counts its whole cost as lost. A deposit or a
  withdrawal moves nothing in it. The floor is the wallet itself: SOL, wrapped SOL and coins.
- When the measure stage has not run for 40 minutes and the wallet has traded today since, the guard cannot tell the
  day (it says so in `/risk` and `/status`), and nothing new starts until it runs again.
- While it holds, nothing new starts. The book creates and starts nothing, and stops its snipers once the wallet holds
  nothing. `/test go` and the round's start of a test refuse. The cycler does not start, and it reads the guard before
  each re-arm (like `/stop`) and ends its run. The automatic test asks for nothing. What the guard cannot read never
  counts as clear.
- What it does not stop: while the wallet holds any coin, every book sniper keeps running, and each can still buy up to
  its cap (`[book] max_buys_per_sniper`, 3). The book cannot tell which of its snipers holds a coin (a fill can name the
  wrong leader), and a stopped sniper strands its coin (CLAUDE.md rule 1). The trip line says so; `/sell <coin>` sells
  the coin, and the next book run then stops them all. This is how the book's own stops have always worked.
- The owner hears it once when a stop trips (one line a day for each stop), and `/status` shows it.

**2. The first test starts by itself** (`operator/autotest.py`; the owner's switch in `operator/AUTOTEST`, the
sequence's record in `operator/autotest.json`).

- Each round checks the conditions: the switch on, no test in progress, no `/stop`, the guard clear. When all hold, the
  round writes the `/test go` request itself for the best strategy `/test` would offer (100+ forward trades where a hunt
  fill lands, a lower bound above zero, also with gains capped, scored at no less than the measured cost of a trade).
- "At real costs": a strategy frozen below the cost of a transaction the measure stage measures (half the low bid's
  measured round trip: 0.0047 SOL a transaction, 0.0094 a round trip, on 6 Nov) is not eligible, for `/test go`
  either. `paper freeze` now scores at that measured cost unless `--per-tx` says otherwise (it used to take 0.0035,
  and accepted 0). Since 7 Nov evening the bar never falls back to the flat 0.0035: the measure stage keeps the last
  cost it measured, and after a week with too few low-bid fills (the bid model reads 7 days) labs, freezes and this
  bar use that; with nothing ever measured they use 0.0045. Each frozen strategy records where its cost came from
  (`per_tx_source`).
- How OKTech sells: a hunt strategy frozen before 7 Nov evening, scored with exits that act from the buy, is not
  eligible. OKTech starts watching a coin about 1.3 s after its buy lands, and every exit of a launch, hunt, whale or
  burst rule frozen since waits that long. The same rule frozen again is a new strategy with the delay but the same
  OKTech sniper, so a drop of either holds for both, and so does the live record a run leaves.
- It runs at the strategy's own size, never another. A strategy scored above the yes's 0.2 SOL is refused, and the owner
  is told once a day: a test at another size says nothing about it.
- The yes's caps apply: 10 buys, 120 minutes, the floor 1 SOL below the wallet's balance. The same round starts it, as
  for a `/test go`: one hunt sniper, one cycler.

**3. It goes on only while live is at least half of paper.**

- After a block has made all its 10 buys and closed, its result is read from the measure stage's fills, once that
  stage has run after the block's last trade. Only our own round trips count, with their real fees, the failed
  duplicate sells and the block's failed buy sends (OKTech sends buys that fail on chain anyway, about 0.001 SOL each;
  paper does not model them). A buy counts once its sales cover its tokens (a scale-out exit sells it in two). A buy
  not covered after 45 minutes counts its unsold part at its whole cost; a measure stage that has not read the block
  45 minutes after it ended stops the sequence for the analyst.
- The book trades from the same wallet, and the fills cannot tell its copies from the test's own buys for sure (they
  name a copied leader on any buy in a coin one of the book's leaders bought first). So the block is read twice, with
  the buys the fills call copies left out and with every buy of the wallet in the block, and the worse reading is the
  one judged. A coin left held counts against the block only when the block bought it.
- The live mean per trade, over every trade of the sequence so far, is compared with the strategy's forward paper mean.
- At least half, and above zero: the next block starts at once, with the same strategy, size and caps. Its floor stays
  the first block's, so the whole sequence can lose at most 1 SOL. There are at most 3 more blocks (40 trades in all).
- Below half: the sequence stops, and a decision note says "the analyst works out why" for the post-mortem to pick up.

**What ends it.** A block that ends early (its 120 minutes, the floor, `/stop`, the guard) or does not start. Live
below half of paper. All 4 blocks done. No room above the floor for another block. The config naming another wallet
than the yes. The switch turned off: a block running then runs to its end (`/stop` ends it now), and a request not
started yet never starts. `/stop` between blocks ends the sequence too: it is written into `operator/AUTOTEST` at
once, so a `/resume` before the next round does not bring it back. With no sequence in progress `/stop` leaves the
switch as it is: nothing starts while the flag is set, and after `/resume` the standing yes may start a test again. At
the end the switch turns itself off (marked used, with the result) and the owner gets one summary. A new sequence needs
the owner's yes again; `/test auto on` is refused while a sequence still runs.

**Recorded:** each block in `operator/TEST.json` and `operator/tests.jsonl` with its sequence and block number, and one
`"kind": "sequence"` line per sequence there. A decision note and a Telegram line go out at every start, block end and
sequence end.

## The strategy

| | |
|---|---|
| Trigger | someone other than the dev buys **>= 2 SOL** of a pump.fun coin **within 1 s of its creation** |
| Coins | normal coins only — skip mayhem and holder-reward coins |
| Entry | right behind that buy, same slot (OKTech's measured speed) |
| Size | 0.2 SOL per buy (scale only after live results match paper) |
| Exit | stop-loss -15%, trailing stop 10% from the peak, sell everything after 300 s at the latest |

The >= 5 SOL version (trailing 18% after +20%) is a subset: fewer trades, similar per-trade result.

## Forward results

| Strategy (frozen 13:05 UTC) | Hours | Trades | Per trade | Lower bound | Win | Total |
|---|---|---|---|---|---|---|
| >= 2 SOL within 1 s, normal coins | 2.7 | 342 | +0.171 | +0.153 | 55% | +58.4 SOL |
| >= 5 SOL within 1 s, normal coins | 2.7 | 151 | +0.176 | +0.153 | 57% | +26.6 SOL |
| (same period, unfiltered >= 2 SOL) | 2.7 | 830 | +0.061 | +0.052 | 36% | +50.5 SOL |
| >= 2 SOL within 1 s, normal coins (later check) | 4.8 | 629 | +0.133 | +0.120 | 49% | +83.5 SOL |
| >= 5 SOL within 1 s, normal coins (later check) | 4.8 | 204 | +0.181 | +0.162 | 56% | +36.8 SOL |

How it was found: the original >= 5 SOL strategy (frozen 05:01) held for 7.8 h (+0.15/trade, n=308);
its profit was all in big buys within 1 s of the create (+0.31/trade) and on normal coins.

## Why paper is not live

* Paper assumes we land right behind the trigger in its slot; measured live: 0-1 trades behind at the
  launch, 1-5 behind on copies. At 3 trades behind the original strategies earned ~nothing.
* Paper does not model other bots reacting to our buys, failed transactions, or new coin types
  OKTech cannot trade yet (pump.fun error 6010 on "CreateV2 / SplitSupply" coins).
* The edge lives in one-off creators: dev-wallet lists cannot express it.

## What OKTech needs — an account approval (5 Nov, 19:46–20:20 UTC)

Three capped live runs from the SNIPER wallet (0.2 SOL × 3 fills, then 0.02 SOL × 3 and × 4), 1.903 → 1.688 SOL:

| run | trigger as sent | fills | had the >= 2 SOL non-dev buy before us | result |
|---|---|---|---|---|
| 19:46 | `hunt` all / minTotalSol 2 / 1000 ms | 3 | 2 of 3 (both holder-reward coins) | -0.13 SOL incl. ~10 failed buys |
| 19:58 | same, no seed account, preexec | 3 | 0 of 3 (1.29, none, 0.27 SOL) | -0.04 |
| 20:06 | `crowdBuys` minSigners 2 / minTotalQuote 2 | 4 | 0 of 4 (create tx carried 0.99, 0, 0, 1.98 SOL) | -0.04 |

Both fired about every 10 s, which is the launch rate, not the trigger's 76/h. So with `monitorProgram` on, the sniper
buys every launch, and `hunt` / `crowdBuys` are signal modules, not entry filters. The 17:02 "verified" hunt trade was a
coincidence. What was learned on the way:

* Auto-sell works as configured (stop -15%, trailing 10%, 300 s timer): sells at 9 s, ~100 s and exactly 300 s observed.
  The twin `6023 NotEnoughTokensToSell` next to each landed sell is OKTech's duplicate send losing the race (fee only).
* Buys that fail on-chain with `6010 AccountTypeNotSupported` are sent anyway (~10 per batch, ~0.001 SOL each) and count
  toward `totalSnipes`; `preexec` did not stop them.
* A capped sniper can finish with a position still open (4siK, 20:04): `snipetech sweep` is our own sell for that case
  (needs `SNIPER_SECRET` in `/etc/snipetech/env`).

Resolved from the OKTech web app's own code (oktech.me bundle, 20:20 UTC): **Hunt is the gate** — its help text is
"SniperHunt: buy only once other buyers cross your thresholds on a new coin", `minTotalSol` = "SOL other buyers must have
put in before you buy", `minUniqueBuyers` = "Distinct buyer wallets needed", `maxDevBuySol` = "Disqualify the launch when
the creator bought more than this with it", `minCpiBuyers` = "buys that came through router programs". But the feature
list reads **"[S] tab (Shapes, Web, Hunt): approved users only"**, as do "Local pre-execution on sniper tasks" and "Account
whitelist/blacklist on sniper tasks". The API accepts the `hunt` block from any key and ignores it on an account that is
not approved: that is today's every-launch buying. The status endpoint reports only create/start, not approvals.

**The fix is an account approval, not a setting:** ask OKTech to approve the account for the [S] tab (Hunt), local
pre-execution and the account filter. The sniper config already sent is then the strategy as frozen.

## Going live (once a gating setting is found)

1. One capped buy (totalSnipes 1, 0.05 SOL): verify on-chain that a >= 2 SOL non-dev buy came first, and that it sold.
2. Batches of 10 buys at 0.2 SOL, each audited against paper (trigger, landing position, exit, PnL).
3. Raise size / remove the cap only while live per-trade results stay within ~half of paper.

## How a live test starts (7 Nov)

No SSH and no command line: the owner starts and ends a capped live test from Telegram (`operator/live_test.py`).

1. **`/test`** lists the hunt strategies the forward paper test backs where a live hunt fill lands (100 forward trades
   or more, a lower bound above zero; the lab alone never counts), best lower bound first, at most three, each with its
   forward n, mean and lower bound. Then the plan for the best one, and last the exact line to send. `/test <id>` shows
   another one's plan.
2. **`/test <id> go`** is the owner's yes for exactly that plan (wallet, size, duration). It only writes
   `operator/TEST.json` (state `requested`), and only when the strategy qualifies now, no test is requested, running or
   closing, no OKTech cycler or guard runs, no sniper that is not the book's is active on OKTech (an earlier test's left
   armed, one started by hand), the tape is at most 2 minutes behind (the book's rule: the cap counts our buys there),
   `/stop` is not set, the wallet holds no coin, the wallet holds at least the floor plus one buy and its fees, and the
   wallet-wide guard (above) is clear. It
   offers only strategies whose forward lower bound is above zero also with each gain capped at 2.5 times its buy (the
   7 Nov create-slot bundle artifact made a losing rule look like +10.6 SOL). A message more
   than 5 minutes old is refused, as for `/sell`.
3. **The next operator round** (within 15 minutes) checks all of it again, writes `starting`, creates the strategy's own
   hunt sniper on OKTech (one buy a start, an externalId of its own) and starts one job:
   `oktech cycle --id <sniper> --address <wallet> --floor <F> --max-minutes 120 --max-fills 10 --json`. The round's jobs
   survive the round and deploys; the one-minute pass would kill its children, so it never starts one. A request no
   round acted on within 30 minutes expires; a round that died mid-start is never retried (a cycler it did start is
   adopted), so a crash cannot leave a second sniper.
4. **Caps:** the strategy's own size a buy, 0.5 SOL at most; at most 10 buys, one coin at a time (the cycler re-arms
   only while the wallet holds nothing); 120 minutes; floor = 1 SOL below the balance the test starts from (the
   owner's stop, 7 Nov 09:15), never under 1 SOL. Below the floor the
   cycler stops re-arming. A sniper holding a coin is never stopped. The 10 buys are the larger of our buys on the tape
   and the coins seen in the wallet (a probe with the recorder missing our buys made 118 under a cap of 10 when only the
   tape counted), and the cycler does not re-arm while the tape is more than 2 minutes behind.
5. **How it ends.** At every end (10 buys, 120 minutes, the floor, `/stop`) the cycler stops its sniper if the wallet
   holds nothing: at the time limit the sniper has almost always just been re-armed, and left armed it would buy at the
   next launch that qualifies, past the approved minutes, with nobody watching. The round then checks OKTech: the test
   is done only once its sniper is stopped (the round stops it itself when the cycler could not, after a reboot or a
   kill). While the wallet holds a coin the sniper keeps running to sell it and the test stays `closing`, its sniper
   and its coin expected; the round that finds the wallet empty stops it. `/stop` cancels a test not started yet and
   ends a running one (the cycler reads the flag every 5 seconds). The unsold-coin watch and `/sell` cover a coin
   OKTech does not sell (2 of 11 hunt fills on 5 Nov, HOUSE and CAT).
6. **Recorded:** `operator/TEST.json` (the plan, the sniper, the job, start and end, the cycler's report: fills, start
   and end balance, why it ended, the coins held by the shared rule, and whether the round stopped the sniper), one
   line per finished test in `operator/tests.jsonl`, a decision note and a Telegram line at the request, the start, the
   end and the close, the cycler's log `jobs/<id>.log` (never pruned), and every fill on the tape and in `fills`. Each
   test's result is written up here.

**A creator-0 rule waits for OKTech's answer (research round 3's priority 1; built 8 Nov on branch `dev0-gate`, it
applies once deployed).** A hunt whose trigger has a creator limit of 0 (dev_max 0, sent to OKTech as maxDevBuySol 0)
was scored skipping every launch whose creator bought anything. Nobody knows yet whether OKTech's Hunt reads 0 that way
or as "no limit". Read as no limit, its sniper would run the plain crowd hunt, which lost 0.0147 SOL a trade over 1,285
trades on 4-5 Nov tape the research had not used, and OKTech has ignored thresholds before without saying so (5 Nov
22:11). So such a rule never starts live until OKTech's answer is recorded: `/test` names the wait as its reason,
`/test <id> go` and the round's start refuse it, the automatic test starts the best strategy that does not wait and
tells the owner once a day, and `oktech strategy` says so in the plan's notes. The rule is still sent as scored
(maxDevBuySol 0); nothing else changes. Only the owner records the answer, in `operator/OKTECH`: `/oktech dev0
confirmed` in Telegram, or from the cloud session with the owner's word in chat, `snipetech remote --wait run operate
oktech --dev-max-zero confirmed --words "<when and how OKTech confirmed it>"`; if OKTech says 0 means no limit, record
nothing (any answer but `confirmed` changes nothing), and the owner gets a Telegram line and a decision note whenever the
cloud session records or takes it back. `/oktech` shows what is recorded and the question to put to OKTech; `/oktech
dev0 unconfirmed`, or `--dev-max-zero unconfirmed`, takes it back (a block running then runs to its end,
and no next block of the rule starts). Nothing in the code writes the file, and the agents cannot. Rules with a creator
limit above 0, copies and everything live now are untouched. The two creator-0 hunts in the paper test since 7 Nov
evening (f94add343f, 7d6b4302ff) are the ones this holds back if their paper passes.

## Hunt live, 5 Nov 21:13–22:10 UTC (account approved for Hunt + local pre-execution)

Facts measured on chain and on the tape, 0.02 SOL per buy on the SNIPER wallet:

- **Hunt triggers correctly.** Every audited fill had a big first-second buy ahead of it; we landed 1–2
  slots after the create with 2–7 buys in front.
- **A hunt sniper fires once per start and never re-arms.** Sniper A: one buy 23 s after start, then
  none across 46 qualifying launches; stop + start → a buy within seconds; after that position closed,
  none across 12 more. Sniper B identical. `oktech cycle` (stop + start a spent sniper only while the
  wallet holds no coin bought during the run) is the workaround; 6 re-arms produced 2 fills in 2 min.
- **Hunt sums the creator's own buy.** Suncat fired on 1.6 SOL from others + 1.0 SOL from the dev.
  Tape (4 h, 500 candidates): total ≥ 2.5 with dev ≤ 1 SOL keeps 79 % of our single-non-dev-≥2 rule
  at 86 % precision (total ≥ 2 alone: 66 %). Strategy snipers now send minTotalSol 2.5, maxDevBuySol 1.
- **PUMP-quoted launches** (quote `pumpCmXq…`, also SQQQ…) trigger Hunt (quote units counted as SOL) and
  the SOL buy fails with pump error 6010; local pre-execution does not cover non-SOL curves. Only the
  account filter (not granted) can exclude them. Each such attempt spends the sniper's one shot.
- **Pre-execution grant landed between the two creates**: the 21:13 sniper ran without it, the 21:19 one
  with it (OKTech turned it on by default). Behaviour was the same either way.
- **A dead coin holds to the timer**: Bricks sat flat for 300 s. Strategy snipers now add a 60 s dead exit.
- **`remote update` kills running server jobs** (the first cycler died at a deploy). Never deploy while a
  guard or cycler runs.

| fill | non-dev buy in 1 s | dev buy | rule met | in → out (SOL) |
|---|---|---|---|---|
| RLT | 11.85 | 0 | yes | 0.0198 → 0.0215 |
| Bricks | 2.96 | 0 | yes | 0.0198 → 0.0196 (timer) |
| Suncat | 1.60 | 1.0 | no | 0.0198 → 0.0272 |
| Gogeta | 2.96 | 0 | yes | 0.0198 → 0.0301 |
| II | 2.86 | 3.16 | yes (new thresholds would skip) | 0.0198 → 0.0170 |

Gross +0.0166 SOL on 0.099 deployed; wallet net −0.016 (1.6878 → 1.6718): at 0.02 SOL the fixed fees per
round trip and per failed PUMP-quoted send outweigh the edge. 22:11 UTC: 0.2 SOL batch started, two snipers
under `oktech cycle --max-fills 10 --max-minutes 60 --floor 0.3`.

### 0.2 SOL batch, 22:11–22:40 UTC — stopped

| coin | entry slot | big first-second buys | in → out (SOL) | note |
|---|---|---|---|---|
| Mumdumi | +3 | 4 buys summing > 2.5, biggest 0.59 | 0.1975 → 0.190 | Hunt sums, our rule is one buy |
| BETRAYAL | +1 | dev 0.99 + 8 wallets × 1.5–4.2 (create bundle) | 0.1975 → 0.0864 | bundle dumped 3 s after our buy |
| HOUSE | 0 | dev 3.95 + 2.37 | 0.1975 → 0.1195 (sold by hand 22:40) | dumped 4 s after; OKTech never sent a sell |
| GTA | +3 | dev 1.38 only | 0.1975 → 0.1975 | dead exit 60 s worked; thresholds not enforced |
| ALON | +2 | dev 0.99 + 8 wallets × 1.5–4.4 (create bundle) | 0.1975 → 0.0618 | bundle dumped at slot +10 |
| CAT | 0 | dev 2.96 + 2.67 | 0.1975 → 0.7215 (sold by hand 22:40, two txs) | the one runner |

Net of the batch +0.25 SOL thanks to CAT; without it −0.47. Day: 1.903 → 1.777 SOL.

**The structural finding.** Our paper entry is position +1 in the trigger's own slot. When the ≥ 2 SOL buy sits
inside the create bundle (most of the triggers), nobody can land there: OKTech lands at slot +1/+2, after the
whole bundle, at the top. Tape, last 3 h, our-rule launches, entry at the first trade of slot +2, mark to
market after 30 s / 120 s:

| big buy | create-slot buyers | n | 30 s | 120 s | dumped > 30 % within 30 s |
|---|---|---|---|---|---|
| in create | ≤ 2 | 108 | −11 % | −17 % | 19 % |
| in create | 3–4 | 88 | −17 % | −16 % | 50 % |
| in create | ≥ 5 | 69 | −15 % | −26 % | 41 % |
| same slot | ≤ 2 | 22 | −4 % | −4 % | 18 % |
| same slot | 3–4 | 126 | −19 % | −23 % | 53 % |
| same slot | ≥ 5 | 263 | −8 % | −20 % | 49 % |
| later slot | ≤ 2 | 21 | +2 % | +6 % | 29 % |
| later slot | 3–4 / ≥ 5 | 25 | −14 % / +4 % | −15 % / −24 % | 36 % / 27 % |

Lab runs with the realistic landing (`--latency 1|2 --position start`) were started 22:29 (jobs
20261105-222916-301925, 20261105-222915-7da66c); their verdict goes here. Other live facts: Hunt thresholds
must be whole numbers without null fields (2.5 / 1.0 / nulls were ignored and the sniper bought every launch);
OKTech sent no sell at all for HOUSE in 25 minutes; CAT's trailing stop did not fire on a 26 % drop from peak
either. Live trading is halted until the lab says a variant is positive where OKTech lands.

### Verdict at the realistic landing (lab, 24.5 h of tape, 224k moments, 5 Nov 23:45 UTC)

Entry at the first trade of the slot after the trigger (`--latency 1 --position start`), 0.2 SOL, every
trigger with its best exit, out of sample. (Staleness is measured in slot gaps since commit 1ea0b43; the
earlier run that showed −0.0115 everywhere was the whole-second artefact.)

| trigger (best exit) | n | SOL/trade | lower bound | wins |
|---|---|---|---|---|
| buy ≥ 2 SOL by a non-dev, 1 s, normal coins (STALE1s SL-15%) | 834 | −0.0115 | −0.0125 | 12 % |
| same, not a create bundle (solo), 1 s | 674 | −0.0117 | −0.0128 | 12 % |
| solo, 5 s window | 733 | −0.0093 | −0.0112 | 12 % |
| buy ≥ 1 SOL solo, 5 s | 1,569 | −0.0093 | −0.0102 | 13 % |
| tracked wallet first buy ≥ 1 SOL (STALE1s SL-30%) | 811 | −0.0064 | −0.0078 | 19 % |
| momentum ≥ 20 buyers/5 m, curve ≥ 30 % | 1,263 | −0.0051 | −0.0066 | 24 % |
| KOL first buy ≥ 0.1 SOL, hold 60 s | 74 | +0.0158 | −0.0011 | 38 % |

Not one of the 40 triggers has a positive lower bound with n ≥ 200. The best exit for almost every launch
trigger is the immediate dead exit, i.e. the least-bad choice is to get out at once: **at the slot after
the trigger, launch sniping has no edge on this tape**. The +0.129/trade paper result for the ≥ 2 SOL
trigger existed only at position +1 inside the trigger's own slot, which no sender gets.

Still open: `--latency 2` (running), and the per-wallet ranking at the realistic landing (`--by-wallet`),
because a KOL's buy is its own transaction and copying it lands +1 in the same slot on the measured copy
tests — the one entry where the paper position is reachable.

Two slots after the trigger (`--latency 2 --position start`, 25.1 h): the same picture, slightly worse —
our trigger −0.0131/trade (n 861, 7 % wins), solo −0.0120 (n 758), best launch trigger −0.0072 (momentum,
n 702); only the two tiny KOL first-buy rows are positive (+0.011 / +0.008, n 59 / 75, lower bounds
negative). No positive lower bound with n ≥ 200 at either landing.

### Wallet copies at the realistic landing (`--by-wallet --latency 1 --position start`, 25.6 h, 6 Nov 00:45 UTC)

97 wallets scored (first buy ≥ 1 or ≥ 2 SOL, or a KOL's first buy ≥ 0.1 SOL), best exit each, out of sample:

| wallet | n | SOL/trade | lower bound | wins | 25 h total |
|---|---|---|---|---|---|
| Washy dev | 6 | +0.102 | +0.015 | 67 % | +0.61 |
| Pengu (KOL, hold 60 s) | 16 | +0.069 | +0.009 | 38 % | +1.10 |
| ottabag | 10 | +0.044 | −0.004 | 30 % | +0.44 |
| Cupseyy | 6 | +0.042 | −0.010 | 33 % | +0.25 |
| clukz | 5 | +0.026 | +0.013 | 60 % | +0.13 |
| Bean Side (≥ 2 SOL) | 16 | +0.016 | +0.002 | 56 % | +0.26 |
| Megga | 23 | +0.015 | −0.004 | 30 % | +0.34 |
| theo side (≥ 2 SOL) | 32 | +0.008 | −0.008 | 38 % | +0.25 |
| Decu | 29 | +0.002 | −0.006 | 45 % | +0.05 |
| dv (≥ 2 SOL) | 33 | +0.001 | −0.021 | 30 % | +0.05 |
| Solprop (≥ 2 SOL) | 54 | +0.001 | −0.006 | 31 % | +0.06 |

Everything positive is small and thin: the top 25 wallets together made about +4 SOL of paper in 25 hours at
0.2 SOL a trade, most of it from a handful of fills, and only three rows have a positive lower bound (n ≤ 16).
A copy bot on these wallets is a few SOL a day at best at this size, not a path to 100. The fairer landing for
copies — one trade behind the leader in the same slot, which the speed tests measured — is being re-run now with
the corrected staleness (`--latency 0 --position +1 --by-wallet`).

## Copy-landing test, 6 Nov 04:36–05:45 UTC — stopped, answer in hand

Twenty copy snipers (five of the paper-positive wallets at 0.05 SOL, fifteen breadth wallets at 0.03 SOL, lab
exit = 1 s stale / −30 % / 300 s, three cyclers re-arming spent snipers) on wallet SNIPER. Seven fills in 70
minutes, every one a bonding-curve buy that landed in the leader's own slot or the next one — the speed is
there — and still 2–4 trades behind the leader, after the other copy bots had already moved the price:

| time (UTC) | leader | leader buy | our buy | behind | price gap at fill | result |
|---|---|---|---|---|---|---|
| 04:4x | Gfree | 0.9 SOL | 0.049 | 2 trades | +3x % | −26 % (leader sold in 20 s) |
| 04:5x | Decu | — | 0.049 | 2 trades | — | +11 % |
| 05:04 | Gfree | 0.98 SOL | 0.049 | 2 trades | +34 % | −26 % |
| 05:15 | Decu | 2.93 SOL | 0.049 | 2 trades | +4 % | ≈ +0 % (coin graduated in 9 s; sold on PumpSwap into WSOL 0.0549) |
| 05:32 | Decu | 2.93 SOL | 0.049 | 4 trades | +43 % | −10 % |
| 05:32 | Art | 0.96 SOL | 0.030 | 2 trades | +26 % | +1 % |
| 05:39 | Gfree | 1.25 SOL | 0.049 | 3 trades | +33 % | −26 % |
| 05:42 | Gfree | 2.40 SOL | 0.049 | 2 trades | +18 % | +17 % |

Mean per fill about −7 %; equity 1.777 → 1.65 SOL (0.13 SOL spent, of which ≈ 0.02 was OKTech's duplicate
sells and failed ATA creates — every sell is still sent three times). The price gap column is the whole story:
the first copy bots pay the leader's price, we pay 18–43 % more, and a 60-second hold cannot recover that on
coins the leader himself flips within a minute. This is exactly the "+6 behind" regime the lab predicted
(roadmap, position-sensitivity table), where the known wallets are worth ≈ 0, not the "+1" regime that paid.

Stopped at 05:45 while nothing was held: cyclers killed first, then all twenty snipers set idle (verified).
Nothing is running on the account; the snipers still exist and can be restarted with one command if a
different landing is ever available. Remaining budget was not the reason — the question was answered, and
every further fill would have been paid for at a known negative expectancy.

### The "quiet follow-through" wallet hunt (6 Nov 05:38 UTC) — no survivors

Scoring 271 discovered wallets for "price keeps moving up 20–30 s after their buy, with few other buyers piling
in" produced 34 candidates with huge mean returns (+5 400 %, +1 300 %…). Checked at the next slot after their
buy with a 30 % stop and returns capped at +200 %:

- eight of the 34 are the coins' own creators (every "buy" is the dev's bundle in the create transaction) —
  uncopyable, we land a slot later;
- the rest have 16–35 % winners and 70–95 % of entries stopped out; the means were one or two 50× outliers;
- best non-creator row: +4 % per trade with 82 % stopped (DFihBUSK) — noise.

Low-cap follow-through wallets with a copyable, quiet landing do not exist in this 30-hour window. The search
stays automated (`discover-wallets` → `follow` → lab by wallet at +1 and +6) in the autopilot, but the live
list is empty until a wallet clears all three with n ≥ 20.

### Inside the slot: what the buyers ahead of us paid (6 Nov, 08:30 UTC)

Every buyer between the leader's buy and ours on the seven copy fills, read from the transactions themselves
(version-1 transactions need `maxSupportedTransactionVersion: 1`; our own fills are legacy/v0, OKTech's `tipAmount`
does not appear as a Jito tip):

| coin | position in slot | wallet | buy | priority fee | Jito tip | who |
|---|---|---|---|---|---|---|
| 3BrEjvww | 61 / 62 / 63 | Gfree + two wallets | 1.25 / 1.30 / 1.36 | 0.002 each | – | leader's own bundle (same fee, same tx format, adjacent) |
| 3BrEjvww | 334 | 6Z91Dgiz | 2.47 | 0 | **0.033** | copy bot |
| 3BrEjvww | 392 | **us** | 0.049 | 0.001 | – | last |
| H4D5mThN | 24 / 25 | Decu / FteNBUJP | 2.93 / 4.94 | 0 / **0.200** | – | a bot paying 0.2 SOL to sit directly behind Decu |
| H4D5mThN | 235–256 | three bots | 0.3–3.0 | 0.0004–0.010 | – | |
| H4D5mThN | 270 | **us** | 0.049 | 0.001 | – | last |
| A7YsCfw6 | 108 | E3wNBV6L | 4.15 | 0 | **0.325** | copy bot (8 % of the buy) |
| au6jPXHf | 184 | 9nH7Fpth | 3.25 | 0.032 | 0.003 | directly behind Gfree |
| 7bDNtSnb | 407 / 461 | 6Z91Dgiz / **us** | 2.47 / 0.049 | 0 / 0.001 | 0.033 / – | |

Reading: we were in the leader's slot six times out of seven — OKTech's speed is not the problem. The order inside
the slot is a fee auction: the leader's own wallets are unbeatable, then bots paying 0.01–0.3 SOL per fill, then us
at the 0.001 minimum, last every time. The bots trade 1–5 SOL a fill, so 0.01–0.03 SOL of fees is 1–3 % to them
and 20–60 % to our 0.05 SOL fills. Conclusion: the copy test failed on the bid and the size, not on the pipe. Next:
a paid-fee test on OKTech at 0.2 SOL fills (control at minimum fees vs 0.02 priority + 0.02 tip, same leaders,
five fills each, position and price gap as the outcome), and the lab at position +3, where a paid bid would land.

## Operator phase 1 on the server (6 Nov, 10:31 UTC) — accepted

Deployed `2de75be` at 10:30 with no job running. The 10:31 round (new code) logged
`resources: anthropic_credit=ok birdeye_cu=ok solanatracker_requests=ok vybe_credits=ok sol_float=ok disk=ok
oktech_failures=ok`, sent the first daily digest to Telegram, and called the operator about `snipetech-daily`
failing (the 6-hour timeout above). `/status`, `/stop`, `/resume` are live but need a message from the owner's
phone to be seen in the log; the dashboard needs `DASH_TOKEN` in the env file. Phase 2 (`discover`, `qualify`)
deployed right after; the timers wait for `setup.sh` on the box.

## Learning loop phase 2 on the server (6 Nov, 10:38–11:05 UTC) — accepted

`discover run` (job 20261106-103747): 4 338 new candidates, no source error, 223 s. `qualify run` (job
20261106-104525): 300 evaluated, 126 through one restricted lab (cand group only), 777 s; 1 qualified
(`2YDnmMQt…`, n 93, +0.0146 / +0.0019 SOL, crowding 3), 22 retired, 277 candidate, 174 without tape data.
Vybe source after the field fix (job 20261106-110247): 344 rows, 311 new. The operator's 11:01 round flagged the
qualified wallet as a decision note. Timers `snipetech-discover.timer` / `snipetech-qualify.timer` are installed
by `setup.sh` and still need the owner's `enable --now`; until then the hourly cloud round stands in.

## Measure stage on the server (6 Nov, 11:15 UTC) — accepted, and it agrees with the hand-made table

`measure run` (job 20261106-111501, 3 s) over the last 24 h: 118 fills — 28 buys, 28 sells, **62 failed sends**
(the triple-send duplicates, 05 Nov 17:02 → 06 Nov 05:44), 28 round trips, 4 copied wallets with a live expectancy.
The copy-test buys it attributed by itself, against the table in "Inside the slot" above:

| time | coin | copied | trades behind | gap % | pos in slot / txs | priority | tip | CU limit |
|---|---|---|---|---|---|---|---|---|
| 05:04:12 | au6jPXHf | 4yo9CUuT | 2 | 34.0 | 4 / 12 | 0.001 | 0 | 150 000 |
| 05:15:46 | 2NgdUjFY | 4vw54BmA | 2 | 3.7 | 0 / 13 | 0.001 | 0 | 150 000 |
| 05:32:08 | H4D5mThN | 4vw54BmA | 4 | 42.7 | 5 / 24 | 0.001 | 0 | 150 000 |
| 05:32:08 | A7YsCfw6 | CgaA9a1J | 2 | 26.2 | 4 / 10 | 0.001 | 0 | 150 000 |
| 05:39:45 | 3BrEjvww | 6Z91Dgiz | 0 | 0.3 | 4 / 7 | 0.001 | 0 | 150 000 |
| 05:42:23 | 7bDNtSnb | 6Z91Dgiz | 0 | 0.2 | 3 / 4 | 0.001 | 0 | 150 000 |

Bid model, arm `control`: n 28, failed 62, mean position 1.7, p75 position 3.75, mean gap 17.9 %, mean fee
0.00113 SOL. Live expectancy (all `warming`, n ≤ 2): every copied wallet negative at this landing, as the test said.
Failed sends carry no fee fields yet (the fingerprinter skips errored transactions); their count is the cost signal.
From here on this table writes itself every five minutes once `snipetech-measure.timer` is enabled.

## Fee test, 6 Nov 2026 (prepared 12:20 UTC, revised 13:40 after the audit; the owner starts it)

Owner's yes in chat: "it's funded, I say yes to everything". Wallet 2.92 SOL at 12:00 UTC.

- **Question:** does paying for position move us up inside the leader's slot? Two arms on the same leaders:
  `control` (priority 0.001, tip 0.002, OKTech's minimums) and `paid` (priority 0.02, tip 0.02). Sells bid 0.002 on
  both arms (OKTech sends every sell 2–3 times; the losers pay the sell priority fee, so a 0.02 sell bid would cost
  0.02 per twin). Seed account off, one send requested, preconfs requested (OKTech gates them per task type).
- **Leaders:** `2YDnmMQt…` (qualified at +6, n 93), `G2oZrd1k…` (n 40, +0.017 at +6), `4yo9CUuT…` Gfree (active,
  the loudest copier crowd). Copy only their buys ≥ 0.3 SOL (Gfree ≥ 0.5).
- **Size and caps:** 0.2 SOL a fill, 2 buys per sniper, 6 snipers → at most 12 fills (2.4 SOL notional; the lab exit
  returns the SOL: dead exit 1 s, stop-loss 30 %, 300 s max). Fees: the paid arm pays 0.04 SOL per buy, the control
  arm 0.003; each sell ≈ 0.004 plus 1–2 failed twins at ≈ 0.003 each. Budget ≈ 0.4 SOL of fees for the whole test.
- **Design caveat (accepted):** both arms copy the same leaders from one wallet, so each leader buy triggers two
  fills (0.4 SOL a signal) and the positions share one token account. That is also the point: each signal gives a
  matched pair, control and paid, in the same market moment. The measure stage counts only *other* wallets' trades
  between the leader and each fill, so our own second fill does not make the arm that went second look worse.
- **Stop rules:** `oktech guard --floor 1.2 --max-minutes 180` stops everything if **equity** (SOL + the tape value of
  the coins held) falls below 1.2 SOL or the SOL balance below 0.05, and otherwise never stops a sniper (they are
  capped). A floor breach is the emergency: it strands whatever is held, and the guard lists it for the owner to
  sell by hand. `operator/expected.json` = 6 running, 3 held, so the operator reports deviations, not the test
  itself. `/stop` on Telegram stops the snipers only while nothing is held.
- **Measurement:** every fill becomes a `fills` row with its arm read from the fee it paid (both arms share the
  wallet; OKTech's task documents carry no fills), the copied wallet, buys behind it, price gap, priority, tip and
  relay, and PnL; `bid_model` per arm: n, failed sends, mean/p75 position, mean buys behind, mean gap, mean fee,
  tipped share, overhead. The comparison is the paired one: for each leader signal, where did control land, where
  did paid land.
- **Baseline before the test** (re-measured 6 Nov over 5–6 Nov, 39 round trips at the minimum fees): mean 2.5 buys
  behind the copied wallet, price gap +26 %, p75 position 4 in the slot, 0.0028 SOL of fees per buy, +0.0020 SOL per
  buy in failed sends, 90 % of buys tipped; realised −0.199 SOL with every fill's real fees.
- **Reading the result:** `snipetech remote --wait run measure report --since <start>` pairs each control fill with
  the paid fill on the same coin within 20 s and says in one line whether paying landed earlier and what it cost.
- **Safety while it runs:** `/stop` acts within a minute once `snipetech-commands.timer` is enabled; a manual
  `oktech stop` refuses while a coin is held (add `--force` only to strand it on purpose); `guard` and `start` refuse
  while the `/stop` flag is set.
- **Why the owner runs the create commands and the guard:** the cloud session's safety layer refuses to start
  real-money transactions itself. The commands (from a checkout with the ops key):

```
snipetech remote --wait run oktech copylist --create --buy 0.2 --snipes 2 --tag arm:control \
  --copy 2YDnmMQt7x7Y3VFounzsnZ3s8TdozqWyK9qeQPWY9saH:2YDn:0.3 \
  --copy G2oZrd1kAUrWE1E4YPZoMi9euBeGgvtKVj5CHSdwazx:G2oZ:0.3 \
  --copy 4yo9CUuTBbds9NFhZd4MzPiZZkUvveXdTnAH8qMsE8ku:Gfree:0.5
snipetech remote --wait run oktech copylist --create --buy 0.2 --snipes 2 --priority 0.02 --tip 0.02 --tag arm:paid \
  --copy 2YDnmMQt7x7Y3VFounzsnZ3s8TdozqWyK9qeQPWY9saH:2YDn:0.3 \
  --copy G2oZrd1kAUrWE1E4YPZoMi9euBeGgvtKVj5CHSdwazx:G2oZ:0.3 \
  --copy 4yo9CUuTBbds9NFhZd4MzPiZZkUvveXdTnAH8qMsE8ku:Gfree:0.5
snipetech remote run oktech guard --id <the six ids printed above> --address 6VFrZ15Vi3cfZCL2MxiSrFBMdqphamoCmh8bc1UJF6at --floor 1.2 --max-minutes 180
```

**Started 6 Nov 15:08 UTC** on the owner's "do everything" in chat. The sniper wallet held 4.76 SOL (4.64 SOL + 0.12
WSOL), no coins. Six capped snipers, created idle and started by `oktech guard` (equity floor 2.0 SOL, 180 minutes,
job `20261106-150848-a85b9a`):

| Leader | Low bid (control) | Paid bid |
|---|---|---|
| `2YDnmMQt…` | `6aeded3c9d2356e2dc629ed5` | `6aeded569d2356e2dc62a3e8` |
| `G2oZrd1k…` | `6aeded3c9d2356e2dc629ee1` | `6aeded579d2356e2dc62a3f4` |
| Gfree `4yo9CUuT…` | `6aeded3d9d2356e2dc629eed` | `6aeded579d2356e2dc62a400` |

At the start OKTech wrapped 0.0996 SOL into WSOL in one transaction (system transfer + sync native): the SOL
balance fell, equity did not. `operator/expected.json` = 6 running, 6 held for the test.

Owner's money moves during the test (not trading): 15:16:40 UTC 2.542 SOL out of the sniper wallet in a plain
transfer, 15:16:51 0.599 SOL back in; equity 2.82 SOL at 15:20 (2.60 SOL + 0.22 WSOL), no coin held. The guard saw
the SOL balance at 1.997, under its 2.0 floor, valued the wallet at 2.22 SOL with the wrapped SOL and correctly did
not call an emergency. The owner said they are adding more.

**Result (6 Nov 18:10 UTC): paying more did not buy a better landing; both arms lost on what they bought.**
From `measure report --since 1793977728` (15:08 to 18:08; the window also holds the live book's copies of 2YDn from
17:01, which ran on the low-fee arm):

| arm | round trips | buys behind the copied wallet | price above its buy | fees, SOL | result, SOL |
|---|---|---|---|---|---|
| low fee (0.001 + 0.002) | 9 | 0.22 | +2.3 % | 0.082 (≈0.009 a trip) | −0.282 (whole: −0.339) |
| paid (0.02 + 0.02) | 4 | 0.5 | +1.6 % | 0.222 (≈0.055 a trip) | −0.325 (whole: −0.387) |

Correction, 8 Nov (the loss breakdown): the result column is the landed trades only, as `measure report` printed it
on 6 Nov. The whole result, with each sell's failed duplicate sends (`twin_fee_sol`), is in brackets: −0.3386 and
−0.3865 SOL over the same window, which is what `measure report` prints since 8 Nov.

- **Landing:** both arms landed right behind the wallet they copied (under one trade behind, 2 % above its price),
  against 2.5 behind and +26 % in the 5–6 Nov baseline. The baseline copied crowded wallets (Gfree) with the seed
  account on; today's leaders (2YDn, G2oZ) were less crowded and every sniper had the seed account off. Paying 20×
  the fee added nothing measurable on these wallets.
- **Cost:** the paid arm costs about 0.055 SOL a round trip, six times the low-fee arm. Some of its sells and their
  failed duplicates went out at the 0.02 buy priority.
- **Pairing:** none. OKTech buys a coin once per wallet, so when both arms copy the same wallet only the faster one
  buys; a paired comparison needs two OKTech wallets.
- **Seed-account sweeps:** gone (0 failed 100k-CU sweeps since the fix; 13 on 5–6 Nov).
- **Duplicate sells:** still there, about one failed send per landed sell (11 for 9 on the low-fee arm, 4 for 4 on
  the paid arm).
- **A last buy left unsold:** the G2oZ paid sniper bought DTAU… at 17:09 with its last allowed buy; its 300 s timer
  never fired, the coin ran 2.6× and fell back, and the owner sold it by hand at 17:18 (−0.048 SOL). A capped
  sniper's last position is not reliably managed after it finishes.
- **What lost the money:** not the landing. The copies buy right behind a large buy (2YDn buys about 3 SOL) and the
  exit (dead exit after 1 s, stop-loss 30 %, copy exit only when the leader sells half at once, no take-profit) sells
  seconds later into the dip, or never locks in a run. The live book dropped 2YDn at 17:50 after 10 live fills at
  −0.045 SOL a trade (its tape verdict had said +0.0146). The OKTech feature audit replays these trades against
  other exits and entry filters before the next live step.

Deploy during the test: at 15:58 the owner's "go do it" deployed `097cb4c`, which restarts the ops service and killed
the first guard; a second guard (`oktech guard --watch-only`, job `20261106-155810-0f725c`, same floor, until 18:08)
watches the same six snipers without calling start on them again.

## A copy OKTech never sold, 6 Nov 2026 20:33 UTC (the third such coin)

One of the two fee-test snipers still running on Gfree (`4yo9CUuT…`; the lab exit: dead exit 1 s, stop-loss 30 %,
300 s timer; 2 buys each) bought 0.2 SOL of `5m5hP15rrB5kyF3V9xsbYfCnvKKr6ZzXozYwAd1kirCF` at 20:33:28, behind
Gfree's buy. The crowd that copies Gfree sold within a second and the price fell about 65 %. **No exit fired**: no
dead exit (the coin then sat quiet for 44 s), no stop-loss, no 300 s timer; OKTech still listed the sniper as
running. The same evening its two earlier Gfree copies were sold within 2 and 8 seconds.

- Third coin OKTech abandoned (4siK 5 Nov, DTAU 17:09, 5m5h 20:33); all three were most likely the buy that used up
  their sniper's cap. OKTech's exits cannot be relied on for the cap-using buy, whatever the preset.
- Our own sell (`snipetech sweep --mint …`) was refused by the cloud session's safety layer even with the owner's
  "do everything" in chat; the owner was asked to sell by hand (by 21:03 the coin was still held, worth about 0.07
  SOL of the 0.2 paid; no trade in it since 20:39).
- What changes: the unsold-coin watch (a Telegram line within about a minute of a missed exit), `/sell <coin>` in
  Telegram so the owner sells with one message, and the book relying on each sniper's first buy only.
- The two Gfree test snipers (`6aeded579d2356e2dc62a400`, `6aeded3d9d2356e2dc629eed`) should be stopped once the
  wallet is flat (`/stop`); the session's safety layer refused that stop too.
- **Sold 7 Nov 05:38:47 UTC** for 0.0763 SOL (tx `45KUm1VdELBNLHXMQdBQZyG8ewNAzHwJrmytkW5qnNvuoC3kNRTY1YyzfVkKLZm6CZHJdR9Lq9dE1KpKB34Qriu2`):
  −0.121 SOL on the 0.1975 bought (−0.167 with the paid arm's 0.04 SOL buy fee and failed sends), nine hours after
  OKTech's exits should have sold it.

## The live book switched on, 6 Nov 2026 16:30 UTC

Owner's yes in chat: "Bro just start it go we can work on safety when im home" (after "nah just start it" and "just
start if for me rq"; the session's safety layer had refused the switch twice before allowing it). `snipetech book on`
wrote `operator/BOOK_ON` on the server. Wallet: the sniper wallet `6VFrZ15V…`. Size and caps (config defaults): 0.2 SOL
fills on the low-fee arm, at most 6 wallets, 2 SOL a day at risk, a 1 SOL day stop, idle below 3 SOL equity, only
wallets whose verdict scores at least 0.005 SOL a trade. Duration: until `/book off` or `/stop`. At the switch:
equity 2.65 SOL and one qualified wallet at 0.0013, so the book sits idle until both conditions change.
Code on the server at the switch: `097cb4c`; the review fixes (`a095854`: `/book off` stops the book's snipers, 3 buys
a sniper) were refused deployment while the fee-test guard ran, so until they are live `/stop` is the halt.

## Operator outage, 6 Nov 12:16–12:45 UTC — fixed

Two rounds (12:16, 12:30) crashed before the snapshot: a local `from .service import Service` inside the CLI's new
`book` branch shadowed the module-level name for the whole `main()`, and the `operate` branch raised
`UnboundLocalError`. Fixed in `35c8580` with a regression test on the dispatch functions; the 12:45 round ran.
Lesson recorded in the test's docstring.

