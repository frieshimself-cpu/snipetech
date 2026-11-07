# Roadmap: our own version of what the winning bots do

The bots we studied (6Z91, Fhbh1) win on coins that are 0–3 s old, right after real money shows up.
They will usually be ahead of us on the first buy, so we don't fight them for it. We use their buys
as part of our signal, enter one step behind, and make the money where they make it: on the coins
that keep running after the first seconds, with a scale-out exit. Everything below is paper-tested
first on the server's recording and only then run live in capped batches.

## The afternoon of 7 Nov: live copying armed, the tape stalled for 1 h 45 min and now heals itself (16:15 UTC)

- **Deployed:** the owner's guard and the automatic test (c265fb7), then live trading that never stops (d682e4d: each
  `/risk` stop can be off, the book's floor and day stop follow `/risk`, the automatic test has no block limit and drops
  a failing strategy for the next), then the recorder's self-healing (fa49772). The guard stands at a 5 SOL day stop and
  no floor (12:55); `/risk stop off`, `/risk floor off` and `/test auto on` are the owner's to send in Telegram.
- **Live copying:** at 13:50 the book armed a copy sniper on `AG89fnx9…` (qualified at the copy landing: n 27, +0.0265
  SOL a trade, lower bound +0.0071, crowding 4, a dumper with its first sell at a median 17 s), 0.2 SOL on its buys of
  0.3 SOL or more. It has bought nothing yet (the leader only sold in the hours after).
- **The tape stalled 14:03-15:48:** a long write lock held by another job, then a write that gave up left the recorder's
  connection in an open transaction, and every later write failed ("database is locked") until a deploy restarted it;
  the database grew from 11.3 to 15 GB. Nobody was told: the only alert was the operator's model, refused by the API.
  Fixed (fa49772): a failed write rolls back, the recorder waits 20 s at most per write, keeps refused trades in memory,
  reopens its connection after 3 minutes and restarts itself after 10, and a tape 5 minutes behind is a critical row
  (Telegram, by code). Still to find: which job held the lock that long (events scan, qualify, search or paper).
- **The owner trades by hand in the sniper wallet** (Axiom): Db3AN1YJ bought 15:24-15:25 for 1.55 SOL, sold 15:30-15:37
  for 2.44 SOL, and moves SOL between their wallets (`H4SJzJ…`, `CsLVhf…`, `GXcAHhvu…`). At 15:45 the wallet held 0 SOL.
  Look-alike addresses keep sending it dust to be copied from its history: `95o22tih…` (for `95o2L9Jm…`), `GXcANzWb…`
  (for `GXcAHhvu…`), `CsLVZuXJ…` (for `CsLVhfV6…`), `H4SJmUSq…` (for `H4SJzJbp…`).
- **The server's agents** have been refused by the API since 11:16 (an Anthropic verification of the organization that
  owns the server's key, `b8b652c0-…`; the owner verified, the refusals go on). The research runs from the cloud session
  instead (docs/research/).

## The wallet was emptied at 10:59; no rule has an edge where our buys land yet (7 Nov, 11:15 UTC)

- **The sniper wallet:** at 10:59:53 UTC one transfer, signed with the wallet's own key, sent its 11.69 SOL to
  `GXcAHhvu…4SinHRtQ`. In the same second 15 other wallets were emptied into that address, the owner's funding wallet
  `95o2L9Jm…D6WaiC3` among them; 15 s later that address sent 8.73 SOL back to `95o2L9Jm…` and 8.73 SOL to `CcFLnC…`,
  and it takes payouts from Axiom's address. It reads as the owner gathering their Axiom wallets; the owner was asked
  to confirm. This repo holds no wallet key and sent nothing. The wallet holds 0 SOL and 0.22 wSOL; the book stopped
  itself below its floor; nothing is held and no sniper runs. Nothing trades live until the wallet holds 10 SOL again
  (the owner's guard, 09:15).
- **Address poisoning:** at 07:21 a look-alike address, `95o22tih…2aiC3` (the same first and last four characters as
  the owner's `95o2L9Jm…WaiC3`), sent the wallet 0.00001 SOL so the fake appears in its history. Check the whole
  address before any transfer.
- **The corrected search (08:16–08:57, 23.6 h of tape, at the real landing):** all 24 Hunt settings lose about
  0.007 SOL a trade, about what a round trip costs: no gross edge where our buys land. Nothing was frozen. Copying the
  tracked wallets' buys of 2 SOL or more: −0.0079 a trade.
- **The analyst, hourly, one hypothesis a turn:** a 1 SOL hunt in 5 s capped at 4 buyers lost 0.0067 a trade over
  1,738 trades (refuted). The one qualified wallet, `2Tp9`, draws 13.9 other buyers in the 2 s after its buys, as many
  as the two wallets whose live copies all lost. The best paper strategy (`7b15e38f74`, n 83, +0.0013 a trade) owes
  all of it to its five best trades. A trigger the lab would refuse now comes back to the agent at once (a11f72b).
- **The owner's yes (09:15, `docs/live-recipe.md`):** the first strategy that passes on paper starts one capped live
  test (0.2 SOL a buy, at most 10 buys and 2 hours, a 1 SOL stop) and goes on only at half of paper. `/test` in
  Telegram is live; the automatic start and the wallet-wide guard are being built, with PumpSwap trades of coins that
  just migrated, so MIG snipes and the migration sell can be scored.

## The launch rule that "held" was a landing artifact; the lab now scores where OKTech buys (7 Nov, 08:30 UTC)

- **Correction (08:20):** the forward paper "winner" below is not evidence. Its whole profit came from a handful of
  trades: one hour (04:00-05:00 UTC) made +9.98 SOL of its +10.3, and its three best trades (+11.0 SOL, 12-23x in no
  time) were coins whose create slot held a 22-wallet bundle of 115-150 SOL that completed the curve inside that slot
  (BRUH, PgFtGG, jpfML7). The paper test bought six trades behind the trigger inside that very slot and sold on
  PumpSwap; no live buy can land there (OKTech's Hunt buys a slot or two later, and its pump.fun buy fails on a
  completed curve). Without its 15 trades above 0.5 SOL the rule lost -9.3 SOL over 1,212 trades (mean -0.0076).
- **Fixed (eae2d0b, deployed 08:30):** every launch-type rule (launch, hunt, whale, burst) is scored where it lands
  live, in the same lab pass (whale, burst and hunt a slot after the trigger, 3 trades in); the paper test re-keys
  the frozen ones there; and a launch-type buy on a coin that has left its curve is no trade. Copies keep the copy
  landing. The 5 Nov hunt fills check the landing: the model enters 0-2 trades later than live did, never earlier.
- **What this means:** there is no launch rule with evidence yet. The next search (it starts when the wallet-check
  lab ends; then every 6 hours) scores 24 Hunt settings and the old rules at the real landing.

### What was written at 07:40 (kept for the record)

- **The forward paper test, 9 h of tape recorded after the freeze, at our real landing, 0.2 SOL fills:** "a non-dev
  buys 1 SOL or more in the first second of a normal coin, not inside the create bundle", lab exit (dead exit 1 s,
  stop-loss 30 %, 300 s): 1,200 trades, +0.0089 SOL a trade, lower bound +0.0035, +10.6 SOL in total (the lab had said
  +0.0124). The 5 s variant: 1,244 trades, +0.0079, lower bound +0.0028, +9.9 SOL. The other two frozen with them:
  +0.0012 (lower bound −0.0017) and −0.046. Every strategy the old lab froze keeps losing (for example "at the
  launch" −0.0099 a trade over 11,375). This is the first rule since the lab fix that stays positive on new tape.
- **Why it is not live yet:** OKTech cannot run that rule as scored. Its launch gate (Hunt) adds up every buy in its
  window, the creator's own included, takes whole numbers only, cannot see whether a buy sat in the create
  transaction, and cannot skip mayhem / holder-reward coins. `oktech strategy` sends an approximation (2 SOL in total
  within 1 s, the creator's buy at most 1 SOL, at most 2 wallets) that was never scored. Since 07:32 the lab has a
  `hunt` trigger that replays Hunt itself and scores it a slot after its trigger, 3 trades in (5 Nov hunt fills
  landed 1–2 slots after the create with 2–7 buys ahead); `oktech strategy` sends a hunt strategy exactly as scored.
  A strategy search started at 07:35 scores 24 Hunt settings, the approximation among them, on the newest 24 h.
  The live test waits for a Hunt setting with a positive lower bound there, and the owner's yes.
- **Paper is not live:** paper charges 0.007 SOL a round trip, live costs about 0.009 (failed duplicate sends);
  a Hunt sniper fires once per start and has to be re-armed (`oktech cycle`), so live takes far fewer than paper's
  ~130 trades an hour; and OKTech has left the sniper's cap-using buy without a sell three times (4siK, DTAU, 5m5h),
  so a live test runs with the unsold-coin watch and `/sell`.
- **The night:** the fixed lab qualified its first wallet, `2Tp9scbr…XUB8D65` (n 25, +0.0066 SOL a trade, lower bound
  +0.0024, crowding 11.5), below the book's bar (score 0.0004 against 0.005), so not copied. 5m5h, the coin OKTech
  abandoned, was sold at 05:38 for 0.076 SOL (0.1975 paid: −0.121 on the trade, −0.167 with the paid arm's fees). Free disk fell to 23.7 GB (a 12.9 GB database;
  long labs kept old read-only copies open), so `snapshot-db` now skips a copy that would leave less than 10 GB.
- **The server, resized 7 Nov ~07:10 by the owner:** down 07:12–07:26 UTC; back with 322 GB of disk (276 GB free),
  every service and timer restarted on its own, the tape recording again (the gap is a hole in the tape, not bad
  data). The owner topped the wallet up: 11.9 SOL at the 07:30 snapshot, nothing held, no sniper running.
- **The agents work harder (owner, 07:30: "I can afford 200$ a day in api credits"):** measured spend was 4.44 $ on
  6 Nov and 1.92 $ by 07:30 on 7 Nov, far under the 15 $ cap: the agents were idle by design, not by budget. Being
  built now: an owner budget (`/budget` in Telegram, no SSH), the analyst every 2 hours instead of once a day,
  hypothesis labs the agents run on their own trigger ideas and freeze into the paper test, and one lab on all CPU
  cores (one lab at a time still).

## The OKTech audit, built (6 Nov, evening UTC) — the leaks are closed; no wallet has proven itself live yet

What the audit found on today's live trades (docs/oktech-audit-2026-11-06.md, the plan in the audit workflow):

- **2YDn sells everything a few seconds after it buys.** All its copies lost (5 of 5 in the audit window, 10 live
  fills at −0.045 SOL a trade by 17:50). No exit could have saved it: the best price reachable after one second still
  loses. The lab rated it +0.0146 SOL a trade because it assumed a block takes 0.4 s (the tape says 0.214 s), had no
  watch delay before OKTech's exits act, no copy exit, and scored a different exit than the book sends.
- **DTAU was a sniper's last allowed buy, and OKTech never sold it** (no dead exit, no copy exit when G2oZ sold
  99.9 % at once, no 300 s timer). The same happened to 4siK on 5 Nov: 2 of 4 such buys.
- **Paying 20× the fee landed no earlier** and OKTech charges the buy priority fee again on every exit.
- **Two snipers on one leader from one wallet get one buy between them** and both try to sell it.
- **Every wallet got the same floor (0.3 SOL)**, so we also copied G2oZ's small top-ups (one lost 34 %), not only its
  big opening buys (+17 % and +26 % at one minute, n 2).

What changed (code on the branch, each piece with its tests; README "How the book uses OKTech"):

- The book relies on each sniper's **first buy only** and replaces the sniper once the wallet holds nothing; a wallet
  is **paused after 5 losing live copies**; **one sniper per leader**; each wallet is copied only on **buys of its own
  floor or more** (the lab's `copy_plan.min_leader_sol`); the paid arm is retired.
- **The unsold-coin watch**: a Telegram line within about a minute when a held coin's exit was due and nothing sold
  it, repeated every 15 minutes while held. It never sells.
- **The lab simulates the live sniper**: measured slot time, OKTech's watch delay (1.3 s), the copy exit, the exit the
  book sends, the real round-trip cost. It reproduces 11 of 12 of today's OKTech-sold copies within 2 slots and their
  proceeds within 0.2 %. Every verdict written before this fix is re-qualified first; until then the book copies none.
- **Measure** labels which exit sold each coin and charges each sell its failed duplicate sends.
- **The agents** (operator, analyst, strategist) get one OKTech playbook of twelve measured facts; the daily caps, the
  buys per sniper and the buy priority fee can never be tuned by the strategist.
- **A review of the merged build** (four reviewers, a skeptic per lens) confirmed 15 defects, all fixed with tests
  before the deploy. The two that mattered most: a retire could stop the sniper that had just bought (the wallet read
  lags our own tape by seconds), and the qualify lab never measured newly labelled wallets (they got "no tape data").
  Now a fresh buy on the tape counts as holding, holdings are read at the confirmed level and again after the stop,
  nothing new starts while the tape lags, each candidate is scored on its own first buy in a shared coin, and a
  live pause is remembered.
- **5m5h (20:33):** a third coin OKTech abandoned (docs/live-recipe.md). Our own sell was refused by the cloud
  session's safety layer; the owner sells by hand. Next: `/sell <coin>` in Telegram and the self-learning loop.

- **The agents learn by themselves** (the owner's "configure it so the agents automatically learn by themselves and
  consistently think about why, what to improve"): a shared memory (operator/lessons.md, one dated lesson a line with
  its evidence and n; operator/ideas.json, ranked improvement ideas each with its evidence and a concrete test) that
  every operator, analyst and strategist turn reads first; a post-mortem turn after every 5 new live round trips
  (at most every 3 hours) that puts each loss in one cause (late or crowded landing, a leader that dumps, an exit too
  early, an exit too late or none fired, fees and failed duplicate sends, a bad coin), counts the causes, writes the
  lesson and starts one research job to test the top idea; the strategist may tune daily, but only after 20 new live
  round trips; the 6-hour digest says what was learned and what is tested next. Code ideas are built and deployed by
  the hourly autopilot session; live tests still need the owner's yes. Nothing in the loop trades.
- **Exits per wallet, shadowed first** (audit item 11): a menu of three OKTech exits (lab: dead exit 1 s, stop-loss
  30 %, 300 s, copy exit 50 %; hold60: 60 s timer, copy exit; ride: trailing stop 25 % after +20 %, stop-loss 30 %,
  300 s, copy exit, no dead exit). The lab scores every wallet on all three and keeps a wallet's own exit only when it
  beat lab on moments it was not chosen on; the gating numbers stay lab's. Measure replays every live copy buy under
  each exit six minutes after it (table exit_shadow), so the post-mortems say which exit would have done better and by
  how much. The book still sends lab to every wallet (`[book] exit_mode = "fixed"`); only the owner switches it to
  `per_wallet`, and only exits in `verified_exits` (lab, hold60) are ever sent; ride needs one capped live fill first.
- **`/sell <coin>` in Telegram**: the owner sells a coin OKTech abandoned with one message (it needs SNIPER_SECRET
  in /etc/snipetech/env); `/sell` alone lists the coins held with the line to send.

- **The fixed lab passes its first check (7 Nov 00:00):** 2YDn, qualified by the old lab at +0.0146 SOL a trade
  (n 93), is retired by the fixed lab: -0.0075 SOL a trade over 220 moments at +6, lower bound -0.0144. Its live
  copies lost -0.045 a trade (n 10), so the lab now agrees with live in sign. Its copy plan reads as live showed:
  style "dumper" (82 % of its exits in one transaction, first sell after 7 s at the median), floor 2.962 SOL (its
  openers), 41 % of its qualifying buys were re-buys. The audit had predicted exactly this.
- **The first strategy search with the fixed lab** (6 Nov 22:15-23:50, 46.7 h of tape, 442,656 moments, the +6
  landing, 0.2 SOL fills): the best was "a non-dev buys 1 SOL or more in the first second of a coin that is not a
  create bundle", lab exit: +0.0124 SOL a trade over 2,698 cases, lower bound +0.0089, about 144 signals an hour;
  three variants +0.006 to +0.011. All four are frozen into the forward paper test. Caution from the same report:
  every launch strategy frozen earlier (by the old lab, which predicted +0.02 to +0.32 a trade) lost on new tape at
  our landing, for example "a non-dev buys 1 SOL or more" -0.008 a trade over 1,254 forward trades and "2 SOL in the
  first second" -0.034 over 540. No live test of a launch strategy until the new four hold up on new tape.

What happens after this deploy: the first runs re-qualify old-lab verdicts (qualified first), the book copies only
verdicts of the fixed lab, the research rotation keeps one lab busy whenever the server is idle, and a digest goes
to Telegram every 6 hours. Honest outlook: this closes the leaks; it does not make money by itself. No copied
wallet has proven itself live yet; G2oZ's opening buys are the best lead (2 of 2 rose after we bought).

## First live book day and the fee test (6 Nov, 18:10 UTC) — the landing is fine, the trades are not

- The owner switched the book on (16:30), lowered its bar to 0.001 (17:01), enabled every timer over SSH (17:10)
  and removed the daily limits. The book copied 2YDn from 17:01: 10 live fills at −0.045 SOL a trade, so it dropped
  the wallet at 17:50 and waits (no wallet passes). Since 15:08 the wallet lost about 0.6 SOL on 13 round trips.
- **Fee test:** paying 0.04 SOL a buy did not land earlier than the 0.003 SOL arm; both landed under one trade behind
  the copied wallet at about +2 % (docs/live-recipe.md). Speed is not what loses the money: the trades and the exits
  are. The paid arm stays off.
- **Next:** the OKTech feature audit (exits replayed on every live trade's price path, entry filters, execution
  settings, how the book and the agents choose them per wallet) becomes the build plan; the forward paper test runs
  around the clock again; the owner added SOL (7.4 SOL equity) for a capped test once the exits change.
- **The lab now copies what the live sniper does (audit items 6 and 7, built 6 Nov evening).** It counted a block as
  0.4 s (it is 0.214 s), so its 1 s dead exit sold on half-second pauses OKTech never sells on; it scored wallets with
  exits the book never sends; it ignored the copied wallet's own sales. Now it measures the block time, waits the
  1.3 s OKTech waits before it watches a coin, sells when the copied wallet sells half its bag, charges the measured
  cost of a round trip, and scores each wallet only on the buys its sniper will copy (its own minimum buy, above its
  top-ups). Checked on the 12 copy trades OKTech sold on 6 Nov: the same exit within 2 blocks on 11, the money within
  0.2 % (the old lab: 6 of 12, 4 % too rosy). **Every lab number changes from this fix on** (qualify verdicts, the
  lab, the strategies the paper test freezes, the event edge); older numbers are not comparable. After the deploy,
  qualify every wallet again: 2YDn's mean should turn negative, and if it does not, no verdict feeds the book until
  we know why.

## The bot runs by itself (6 Nov, 16:00 UTC) — what it waits for

Deployed at 15:58 on the owner's "go do it": every 15-minute operator round now starts the loop's stages itself
(measure, the book, the event feeds, discover, qualify, the read-only database copy, the event scan, the Sunday
strategist), because the owner has not enabled their timers over SSH. The first round at 16:00 started measure, the
book, the feeds and qualify with nobody logged in.

The book is still off: switching real money on is the owner's `/book on` in Telegram (this session's safety layer
refused to do it). Once on, it buys only when both hold: equity of at least 3 SOL (2.65 now) and a wallet that
scores at least 0.005 SOL a trade at our landing (today one wallet qualifies, `2YDnmMQt…`, at 0.0013).

First fee-test numbers (two paid buys copying `2YDnmMQt…`, 15:52 and 15:58): paying 0.02 priority + 0.02 tip
landed us 0 and 1 trades behind the leader at +0.9 % and +1.1 % above its price, against 2.5 behind and +26 % at the
minimum fees (39 round trips on 5–6 Nov). Both trades still lost (−0.16 SOL together, 0.12 of it fees): some sells
and their failed duplicates went out at the 0.02 buy fee, so the paid arm costs about 0.06 SOL a round trip, 30 % of
a 0.2 SOL fill. The low-fee arm never bought: OKTech seems to buy a coin once per wallet, so the faster paid sniper
takes every shared signal. Final numbers when the test ends at 18:08 (docs/live-recipe.md).

A review of the self-running code before it went live found that `/book off` left the book's snipers buying with
its stops switched off, that one truncated job file could stop every stage silently, and that a running guard
dropped off `remote jobs` after two hours; all fixed with tests (commit `a095854`).

## The loop runs itself now (6 Nov, 10:30 UTC) — what is automatic, what is still yours

Built, tested and deployed today (README 3e): the operator checks what you pay for every 15 minutes and tells you
on Telegram once a day per problem; a morning digest; `/status`, `/stop` (never strands a coin), `/resume`; a live
page at `/dash`; and the first two stages of the learning loop — `discover` (hourly, seven sources into one
candidates table) and `qualify` (every 30 minutes, one lab at a time, a verdict per wallet at the +6 landing).
The three gates from the roadmap are now code, not a checklist. Nothing trades: the book stays off until you fund
it and say so.

What the day's data said:

- The +6 by-wallet lab on 35 h of tape (300 938 moments): still one wallet with n ≥ 20 and a positive lower
  bound — `G2oZrd…` (n 40, +0.017 SOL a trade, 90 % wins, +0.66 SOL over the window). `3JD7zr…` (Solprop) has
  n 54 at +0.006 with a lower bound just above zero. Everything else positive has n < 20.
- The 19 SolanaTracker leaderboard wallets: 18 made **no** pump.fun buy in two days of tape, the one that did
  (`4AQyaA…`, 5 buys) had no followers at all. The paid leaderboards rank people who trade elsewhere; for us the
  tape, the coin-first source (first buyers of runners) and your tracked list are the feeds that matter.
- The daily job was killed at systemd's 6-hour limit this morning: it ran six labs beside my manual lab on a
  4-CPU box with 1.8 GB peak memory and swap. It now runs one lab (+1 landing), the +6 by-wallet lab belongs to
  `qualify`, and every lab takes the same lock. The resize to 16 GB / 6 CPU is still the right move once the Vultr
  balance is topped up.

First runs on the server (6 Nov, 10:38–11:05 UTC): `discover` put 4 338 wallets into the candidates table in
four minutes (tape 299, your tracked list 2 681, SolanaTracker 33, Birdeye 226, coin-first 1 280; Vybe added 344
once its field names were fixed). `qualify` measured the first 300 in 13 minutes: 174 had never traded in the
tape (review in 72 h), 22 retired (bundle members in 2–44 launches, or trading off pump.fun), 277 wait for more
evidence, and **one qualified**: `2YDnmMQt…` — n 93 at +6, +0.0146 SOL a trade, lower bound +0.0019, median 3
followers within a second, no API history (a tape-only wallet). The operator reported it on Telegram at 11:01
with the right caveat (history unverified). That is the loop working end to end without a hand on it.

The measure stage (phase 3) is live too (6 Nov, 11:15 UTC): every buy and sell of the sniper wallet becomes a
`fills` row with its position, the copied wallet, trades behind, price gap, fees and PnL, and failed sends count as
cost. Its first pass re-derived the copy-test table by itself (62 failed duplicate sends in 24 h, mean gap 18 %, p75
position 3.75) — the next live step will be measured the moment it happens, with no one watching.

Later the same day (12:45 UTC): the book runs in dry mode every five minutes (it writes what it would do; it said
"equity below floor" once, correctly, at 3 SOL) and the events stage is live — trending and graduating feeds, exact
curve crossings and dev-payment edges (1 141 in the first scan) feeding the relationship gate.

The OKTech audit (13:30 UTC, `docs/oktech-audit-2026-11-06.md`) checked our integration against the chain and
OKTech's own app and found fifteen things wrong on our side; all are fixed and deployed (`5e24154`+). The ones that
would have cost money or hidden it: every copy sniper still bought through OKTech's seed account (two transactions
and parked rent per fill, and the 6023 race); `/stop` refused every stop because of 0.0006 USDC while the gate said
nothing was held; the measure stage could not have told the fee-test arms apart and credited two copy fills to a bot
instead of the leader; tips to the relays OKTech really uses (not Jito) read as zero. The re-measured baseline over
5–6 Nov: 2.5 buys behind the copied wallet, +26 % price gap, 0.0028 SOL of fees per buy plus 0.0020 in failed sends.
What only OKTech can answer (why every sell goes out two or three times, why one buy in three carries no tip,
compute units, silent acceptance of ungranted features) is a nine-question list at the end of that file.

The strategist (stage 6, 14:30 UTC) is built and deployed too: once a week, on Sundays, an Opus turn reads the week's
numbers and may move one knob of the book or the qualify stage inside the `[autotune]` bounds you set (it can widen
exploration or move the labs' landing further back; it can never switch the book on, touch a wallet or raise a size
above your ceilings). Anything outside the bounds comes to you as a question on Telegram. Every applied change is an
event, so next week's numbers can be read against it. The learning loop as designed is now complete end to end; what
remains is live evidence.

Later (15:30 UTC): `/stop` now acts within a minute (a one-minute Telegram pass beside the 15-minute round); creator
fee claims are decoded from the chain and become events; the labs read the hourly read-only copy of the database when
it is fresh, so research no longer competes with the recorder for the live file. The wallet showed 4.76 SOL at 14:00.

A second review of the day's own fixes (afternoon) found and closed another set of holes, the worst two being that
`oktech push --prune` would have deleted the fee-test snipers and that a coin bought seconds earlier was invisible to
every stop decision (docs/oktech-audit-2026-11-06.md, "Second pass"). With every fill's real fees, the 39 round trips
of 5–6 Nov lost 0.199 SOL, nearly all of it fees: the case for measuring whether paying more for position is worth it.

The fee test started at 15:08 UTC on the owner's go: six capped snipers on three leaders, half at the minimum bid
and half paying 0.02 + 0.02 SOL, watched by the guard until about 18:08 UTC.

Still yours, in order: send
the nine questions to OKTech's owner; run `sudo bash /opt/snipetech/deploy/setup.sh` once and enable the new timers
and the events service (the exact line is in README 3e); put a `DASH_TOKEN` in `/etc/snipetech/env` for the page; top
up Vultr and say "resize now" between jobs; fund the wallet to 6–10 SOL when you want the book on.

## The wallet labs at both landings (6 Nov, 08:00 UTC) — one survivor, and it is small

Same 32 h of tape, 132 wallets, best exit each, 0.2 SOL a trade:

| landing | wallets positive | positive lower bound | top-25 together |
|---|---|---|---|
| +1 (one trade behind, the optimistic case) | 64 | 45 | +16.3 SOL |
| +6 (where the live copies actually landed) | 34 | 11 | +4.6 SOL |

Only **one** wallet has n ≥ 20 and a positive lower bound at both landings: `G2oZrd…` (found by the discovery
pass), n 37, +0.017 SOL per trade at +6 with a 92 % win rate — about 0.6 SOL a day at 0.2 SOL a trade. Every
wallet from the paid KOL lists (Gfree, Pengu, Decu, Cupseyy, Megga…) drops to zero or below at +6, which is what
the live test said. G2oZrd still needs the 30-day gate (Birdeye) and a quiet-landing check before any live step,
and 0.6 SOL a day is a data point, not a business.

## Copy landing measured live (6 Nov, 05:45 UTC) — the known wallets are the +6 regime

> Operator (6 Nov, built): a 15-minute round on the server that calls Claude only on change, with no trading tools;
> notes in `operator/notes.md`, decisions to Telegram, caps 15 $/day. README 3e; spec and plan under docs/superpowers/.

Seven live copy fills (docs/live-recipe.md, "Copy-landing test, 6 Nov"): same or next slot as the leader, but
2–4 trades behind and 18–43 % above the leader's price once the other copy bots have filled. Mean −7 % per
fill. That is the bottom row of the position-sensitivity table below, not the top one, so the "+13–14 SOL a
day" paper number for the top-25 wallets does not apply to this bot. The live test is stopped with nothing
held and nothing running.

What is left that can still work, in order of evidence:

1. A wallet whose copiers are few — measured by the price gap between its buy and the second buy after it.
   The 271-wallet discovery pass found none in 30 h of tape (every quiet-landing candidate was a creator or an
   outlier). The pass keeps running daily; a wallet needs n ≥ 20 positive at +6 before it goes live.
2. Exits on the leader's sell, not on a timer: OKTech's `ctbotCopyExit` was on, but the leader sold before our
   buy had confirmed in three of the seven fills. Nothing to tune there.
3. Being first: landing inside the create slot (the only regime that paid on paper) needs the create tx itself
   as the trigger, i.e. a bundle or a pre-signed buy in the same block — out of reach for OKTech's remote
   execution, as the Hunt test showed.

## Wallet copies at the measured copy landing (6 Nov, 01:50 UTC) — the one positive result

`lab --latency 0 --position +1 --by-wallet`, 26 h of tape, 0.2 SOL a trade, 99 wallets scored, exits
simulated, out of sample. This is the landing the speed tests measured for copies (one trade behind the
leader, same slot) — unlike launch triggers, a KOL's buy is its own transaction, so +1 behind it is a real
place to land. 31 wallets have a positive lower bound; the top 25 together made +12.9 SOL of paper in 26 h.

| wallet | trigger | n | SOL/trade | lower bound | wins | 26 h total |
|---|---|---|---|---|---|---|
| Pengu (KOL) | first buy ≥ 0.1 SOL, hold 60 s | 16 | +0.085 | +0.016 | 38 % | +1.35 |
| 0xsolomon `3GzBLpxT` | first buy ≥ 1 SOL, STALE1s SL-30% | 20 | +0.050 | +0.044 | 95 % | +1.00 |
| Megga `H31vEBxS` | same | 23 | +0.048 | +0.029 | 78 % | +1.10 |
| Decu `4vw54BmA` | same | 29 | +0.036 | +0.028 | 72 % | +1.04 |
| dv `BCagckXe` | first buy ≥ 2 SOL, STALE2s SL-30% | 34 | +0.029 | +0.007 | 50 % | +0.99 |
| theo side `Bi4rd5FH` | same | 34 | +0.027 | +0.011 | 56 % | +0.92 |
| Bean Side `VJSDW6S7` | same | 16 | +0.027 | +0.013 | 69 % | +0.44 |
| Washy dev, Cupseyy, 0xsolomon `DEdEW3SM`, clukz, Limfork, Art diddy bundle | ≥ 1 SOL | 4–9 each | +0.045…+0.13 | > 0 | 80–100 % | +0.2…+0.8 each |

At the next-slot landing the same wallets fall to +0.00…+0.015 (see live-recipe.md), so **the copy edge also
lives in the same-slot landing**. That landing was measured on five copies of one wallet (speed test, 5 Nov);
it is the assumption to verify before any size: a live copy test on the four n ≥ 20 wallets at 0.05 SOL, with
every fill checked on the tape for its slot relative to the leader's buy. Expected if the landing holds: about
+0.04 SOL per trade, ~100 copied buys a day across the list at 0.2 SOL, i.e. 3–5 SOL a day of paper — a
real but modest product. Not started: needs the owner's go.


**Day-2 refresh (6 Nov 04:40 UTC, 29.3 h of tape, +1 behind):** 112 wallets scored, 40 with a positive lower
bound, top-25 total +14.0 SOL/day paper at 0.2. The five live candidates held or improved: 0xsolomon
`3GzBLpxT` n 26 +0.052 (lower +0.046, 96 % wins), Gfree n 20 +0.070 (lower +0.043), Megga n 23 +0.048,
Decu n 30 +0.037, Pengu n 16 +0.085. New strong rows: Cupseyy n 10 +0.103 (lower +0.065), 0xsolomon
`DEdEW3SM` n 12 +0.074, dv n 47 +0.023. The live copy-landing test on the five started 04:36 UTC.

**Sensitivity to the position inside the slot (6 Nov, 02:50 UTC).** Our own 11 speed-test fills (5 Nov 04:30–05:18)
landed in the same slot as the last big buy ahead of them in 9 of 11 cases, with 0–3 trades in between. The same
per-wallet ranking at +6 trades behind the leader (a conservative bound) keeps far less: 8 wallets with a
positive lower bound instead of 31, top-25 total +5.5 SOL/day instead of +12.9.

| wallet | +1 behind | +6 behind |
|---|---|---|
| 0xsolomon `3GzBLpxT` (n 20) | +0.050 | −0.005 |
| Megga (n 23) | +0.048 | +0.014 |
| Decu (n 29) | +0.036 | +0.003 |
| dv (n 34) | +0.029 | +0.001 |
| Bean Side (n 16) | +0.027 | +0.015 |
| Pengu, KOL hold 60 s (n 16) | +0.085 | +0.071 |
| Gfree, KOL hold 60 s (n 18) | — | +0.033 |

So the copy edge lives in the first few trades after the leader's buy. The live test must therefore record,
for every fill, how many trades sat between the copied buy and ours; the answer decides the list and the size.
The KOL "first buy, hold 60 s" rows (Pengu, Gfree) are the least position-sensitive and the first to test.

## Inside the create slot — what the paper edge actually was (6 Nov, 01:05 UTC)

Our-rule launches (a non-dev buy ≥ 2 SOL within 1 s, normal coins), last 8 h of tape, split by where that
buy sat. "+1" is the paper entry: right behind the trigger buy, inside its slot. "next slot" is the first
trade of the following slot, the best any outside sender can do. Mark to market after 120 s.

| class | n | +1 entry (paper) | next-slot entry | price gap +1 → next slot | spike ≥ 30 % after next-slot entry |
|---|---|---|---|---|---|
| big buy inside the create tx | 25 | +715 % | +14 % | +11 % | 17 % |
| big buy in the create's slot, own tx | 864 | +67 % | −17.5 % | +76 % | 42 % |
| big buy in a later slot | 79 | −9 % | −13 % | +7 % | 35 % |

The whole edge is the **+76 % that happens inside the trigger's own slot**: after the ≥ 2 SOL buy, the rest
of the bundle or burst keeps buying before the slot closes, and by the first trade of the next slot the
price has already moved 76 % on average. Paper bought at +1 and sold into that move; a real sender buys
after it and holds the bag. 864 of 968 cases (89 %) are this class.

What "being inside the slot" would take:

- **Be the bundle.** The buyers inside the create slot are the launch's own wallets or bots co-submitting
  with the create (Jito bundle to the create's leader). Joining that means being the launcher or paying for
  a slot in someone's bundle — a different business (and the one the "Mayhem" product sells).
- **Same-slot landing from outside.** A tx seen in the shreds of slot N is already executed; to land in slot N
  you must build, sign and deliver a tx to the same leader before it seals the block, i.e. well under 400 ms
  including network, and the leader must still accept it. Colocated bundlers do this sometimes; it is not a
  reliable entry, and every competitor with the same idea is in front of us.
- **The 11 % class** (big buy inside the create tx, n = 25, +14 % next slot) is the only slice where a
  next-slot entry is positive, and it is 25 launches in 8 h with a 17 % spike rate — too thin to build on.

Conclusion: the ≥ 2 SOL first-second trigger is not a strategy an outside sniper can run. Launch triggers are
closed. What remains measurable from our seat: wallet copies at the measured copy landing (running now), and
anything that fires *after* the create-slot move has already happened (momentum-type entries, all negative so
far at the next slot).

## Where we stand (5 Nov, night) — the landing reality

Live with Hunt approved (21:13–22:40 UTC, 13 fills at 0.02 and 0.2 SOL) settled the question the paper
engine could not: **where our buy lands**. OKTech lands 1–2 slots after the create. The paper edge
(a586d8164a, +0.129/trade) was computed at position +1 *inside the trigger's slot*, right behind the
≥ 2 SOL buy. When that buy is part of the create bundle — most of the triggers — nobody lands there; we
land after the whole bundle, at the top, and bundles dump within seconds (BETRAYAL −56 %, ALON −69 %,
HOUSE −40 %). The one winner, CAT (+265 %), was a dev-heavy launch the new thresholds would skip.

Measured on the tape at the realistic entry (first trade of slot +2):

- every bundle class is negative 30 s and 120 s later (−8 … −26 %), 40–50 % dump > 30 % within 30 s;
- the only reachable slice — the big buy arrives in a *later* slot than the create, quiet create slot —
  is −23 % at 120 s (n = 217, 12 h), 55 % dump, but 38 % spike ≥ 20 % first: a trailing exit might
  still carry it; the lab (`--latency 1|2 --position start`) decides, with exits simulated.

Consequences for the plan:

1. **Phase 1 changes meaning.** "The entry we can actually get" is slot +1 at best, for anyone, OKTech
   or our own sender. Every strategy is re-ranked at `--latency 1 --position start`; nothing computed at
   latency 0 counts as evidence any more.
2. **Hunt is usable but one-shot and coarse.** It fires once per start (`oktech cycle` re-arms), sums the
   creator's buy, takes whole-number thresholds only (minTotalSol 3 / maxDevBuySol 1 / maxUniqueBuyers ≤ 2
   is the closest expression of "a lone big buy on a quiet launch"), and cannot see non-SOL quotes (6010
   failures; the account filter would fix it). Its sells are not reliable (HOUSE never sold; CAT's trailing
   stop did not fire): `snipetech sweep` with SNIPER_SECRET on the server is the safety net.
3. **The wallet-copy angle is unaffected** by the bundle problem (a KOL's buy is its own tx, and the
   measured copy landing is +1 same slot) and is re-ranked at the realistic landing next (`--by-wallet`).
4. **Live is halted** until a variant is positive at the realistic landing with n ≥ 200 and a positive
   lower bound; then 0.2 SOL, cycled, with a daily loss stop, and a fresh go from the owner.

## Where we stand (5 Nov, evening)

- Recorder: every pump.fun / LaunchLab trade with exact block position, 24/7 on the server (LaserStream).
- Paper strategy `a586d8164a` (frozen 13:05 UTC, judged only on newer data): buy right after a non-dev puts
  >= 2 SOL into a coin within 1 s of its creation, normal coins only, SL -15% / trailing 10% / 5 min:
  **+0.133 SOL per 0.2 SOL trade, 629 trades over 4.8 h, 49% wins** (bound +0.120). The >= 5 SOL version
  `3fe27cc4b7`: +0.181 over 204 trades (bound +0.162). The lab over the whole 19.6 h tape agrees (+0.121 / +0.192 on
  the newer 40%).
- OKTech trigger NOT solved: live probes (19:46–20:12) show `hunt` and `crowdBuys` do not gate; with monitorProgram the sniper buys every launch (see live-recipe.md).
- Sells are safe: snipers cap their own buys and keep selling; the guard only stops in an emergency.
- The owner's tracked-wallet list (2,626 Solana addresses: KOLs, devs, snipers, bundles, cabals) is loaded as labels
  (`label --file inputs/tracked_labels.tsv --kind wallet`) and studied below.

## What the tracked-wallet list says (5 Nov)

Two days of tape, 171 of the 2,626 wallets active (>= 6 coins bought). Three questions, three answers:

1. **Copy them trade for trade, one slot behind, mirroring their exits (0.5 SOL)?** No. 156 wallets scored
   (`follow`), 7 positive, median -0.11 SOL per position. The same-slot column shows why: Washy2 +0.21 landing with
   them, -0.00 one slot later; Decu +0.03 → -0.14; Theo dev +0.14 → -0.04; Cupseyy -0.03 → -0.35. Two to five copy
   bots already sit in their slot and the price we get is theirs.
2. **Does the crowd follow their first buy?** For a short list, strongly. First buy >= 1 SOL on the curve, price
   30 s later vs right after their buy: Cupseyy +86% (10 coins, 170 buyers follow), cap +52%, 0xsolomon +50%
   (21 coins), dv +47% (24), Washy2 +47% (22), Decu +43% (49 coins, 144 buyers), 3|Dustxbt +34% (34), Theo +30%.
   Snipers, bundles, "dev" wallets and the copy bots on the list are flat to negative: they are the ones being
   copied. (Fake Ansem is excluded: not a KOL.)
3. **Their first buy as a signal with *our* exit, at our speed?** Positive for a handful, small in SOL. Lab, 0.2 SOL,
   landing in their slot one trade behind (+1), exit picked on older data (dead-exit 2 s / SL -30% / 300 s), scored on
   all of each wallet's first buys >= 1 SOL (n is small: treat as a shortlist, not a result):

| wallet | address | first buys | +1 mean | +1 bound | win | +3 mean | +3 bound |
|---|---|---|---|---|---|---|---|
| 0xsolomon | `DEdEW3SM…` | 6 | +0.258 | +0.095 | 100% | +0.197 | +0.053 |
| Limfork.eth | `BQVz7fQ1…` | 4 | +0.071 | +0.061 | 100% | - | - |
| 0xsolomon | `3GzBLpxT…` | 11 | +0.065 | +0.053 | 100% | +0.007 | -0.003 |
| Decu | `4vw54BmA…` | 19 | +0.081 | +0.040 | 53% | +0.047 | +0.019 |
| Art diddy bundle | `BsvjGudt…` | 5 | +0.040 | +0.032 | 100% | - | - |
| dv | `BCagckXe…` | 19 | +0.099 | +0.029 | 42% | +0.068 | -0.004 |
| Publix | `86AEJExy…` | 4 | +0.030 | +0.017 | 75% | +0.026 | +0.009 |
| 3 | Dustxbt | `FzVQSzj8…` | 22 | +0.028 | +0.013 | 59% | +0.008 | -0.006 |
| Dior | `87rRdssF…` | 4 | +0.023 | +0.012 | 75% | +0.021 | +0.009 |
| ANALYSE DECU COPY | `Fhbh1DTU…` | 17 | +0.023 | +0.011 | 59% | +0.022 | +0.010 |
| Doji bunde | `D4yrdUWH…` | 9 | +0.018 | +0.009 | 56% | +0.003 | -0.004 |
| Lynk | `CkPFGv2W…` | 13 | +0.035 | +0.003 | 46% | +0.030 | -0.001 |
| TIL | `EHg5YkU2…` | 7 | +0.010 | +0.002 | 57% | +0.004 | -0.005 |
| 2 | Latuche | `GJA1HEbx…` | 6 | +0.012 | +0.002 | 83% | -0.004 | -0.012 |
| theo side | `Bi4rd5FH…` | 18 | +0.055 | +0.002 | 39% | - | - |
| Dali | `CvNiezB8…` | 11 | +0.025 | +0.001 | 45% | +0.025 | +0.001 |
| trunoest | `ardinRsN…` | 35 | +0.021 | +0.000 | 46% | -0.006 | -0.016 |

Decu's first buy is a signal (+0.08 at +1, 53% wins) even though copying Decu's exits loses (-0.14): the money is in
selling into the crowd within seconds, not in holding through Decu's scale-out. Three trades behind (+3) most of it is
gone, so a KOL copy sniper lives or dies on landing in their slot.

What this is worth: Decu makes ~1 qualifying first buy an hour, 0xsolomon ~1, dv ~1. At 0.2 SOL that is a few SOL a
day from the whole shortlist, against ~400 SOL/day of paper signal from the launch trigger. So: a side dish, run as
one capped `ctbot` sniper per wallet (first buy >= 1 SOL, retrigger cooldown so top-ups don't fire), sized up only
after the launch strategy is live and audited. The labeled-KOL group as a whole (the earlier 50 KOLs) is +0.038 per
trade at +1 (bound +0.019, 41 test moments); the full 2,626-wallet group is noise (-0.001).

The nightly job re-ranks this list (`lab --by-wallet`) so wallets that stop working drop out.

## Phase 1 — the entry we can actually get (this week)

Goal: enter *behind* the fast bots instead of racing them, and prove it live.

1. **Trigger = "the fast money is in".** Not "first 2 SOL buy" but: within 3 s of the launch, >= 2 SOL
   bought by non-devs **and** >= 2 distinct buyers, price up >= 10% from the create. The fast bots'
   buys are part of that signal; we're the 3rd–6th buyer, which is where 6Z91 itself makes the most
   per coin (+0.35–0.45 vs +0.29 as first buyer).
2. **Coin filter (from both bots' rules):** dev buy at create between 0.02 and 8.5 SOL; no mayhem,
   no holder-reward coins; tweet-linked first (OKTech `metadataFilter`: twitter contains `/status/`).
3. **Fees:** priority 0.01–0.015 SOL per buy (6Z91 pays ~0.015), tips at minimum. Buys land in the
   creation slot; sells need no fee.
4. **Live proof:** 1 capped buy, verify on-chain (trigger first, then us, then the sell). Then batches
   of 10 at 0.2 SOL, each audited against paper. Pass mark: live >= half of paper per trade.

Needs: ~2 SOL in the SNIPER wallet for the 0.2 SOL batches.

## Phase 2 — the exit that makes the money (week 1–2)

6Z91's quick flips lose (-23 SOL on coins held < 2 s); its profit is in coins held 16 s to minutes.
Our 100%-out trailing stop leaves that on the table.

1. ~~Add its ladder to the simulator and paper-test it against the current exit~~ Done 5 Nov evening: 24 ladder
   variants (sell 35–50% at +10/+20%, trail 10–18%, stop -8/-15%, with and without a 1-s dead exit) are in the lab's
   exit grid. On 20.3 h of tape none beat the plain trailing stop for any trigger (the freeze found nothing new), so
   the live exit stays SL -15% / trail 10% / 300 s. Re-check when the tape is a few days long; the ladder helps 6Z91
   because it holds minutes, and our edge is in the first seconds.
2. Add OKTech's remaining exits to the search grid: `autoStop` (a fast +50% in 3 s turns the exits
   off and lets it run), `momentumHold`, `timerExtension`, `stageAcceleration`.
3. Freeze the winner, judge it forward for >= 300 trades, then switch the live sniper to it.

## Phase 3 — more signal, same speed (week 2–3)

Each of these is a lab trigger first; only forward-positive ones go live, each as its own capped sniper.

1. **Fast-bot confirmation.** Keep a live list of the 20 fastest profitable wallets on the tape
   (re-ranked nightly, execution wallets excluded). Trigger: >= 2 of them bought within 3 s of the
   launch. They did the filtering; we buy the consensus one slot later.
2. **Tweet coins.** 71–83% of the bots' coins link a tweet. OKTech's Twitter task + `tweetBinding`
   + `maxTweetAgeSec` buys the moment a watched account's tweet gets its coin; snipetech picks the
   accounts whose coins pumped on the tape.
3. **Stock-quoted launches.** The bots overweight MSFTx / OPENAI / CARDS-quoted coins; the lab runs
   the same trigger per quote asset (OKTech: `pumpFilters.nonSolQuoteOnly` + `pumpAutoSwap`).
4. **Dev snipes with confirmation.** For the few repeat devs whose launches pump (snipetech's SNIPE
   list), OKTech `walletTriggers` buys only after +X% within Y ms, with `web.tracking` following
   their fresh wallets and `blockedCreators` fed from the rinse list.

## Phase 4 — scale to 100 SOL a day (week 3+)

Paper at 0.2 SOL: ~130 signals an hour x +0.17 = ~22 SOL/h gross; live will be a fraction of that.
Scaling is by size, not by more signals:

1. Raise size 0.2 → 0.5 → 1 SOL only while live per-trade stays >= half of paper at the new size.
   The lab's size sweep says the edge grows roughly with size up to ~1 SOL; beyond that our own
   buys move the price and the bots start reacting to us.
2. One wallet is enough (owner's call): the balance only limits how many positions can be open at once, so fund
   SNIPER for the batch size rather than splitting across wallets.
3. Risk rails, fixed before scaling: daily loss limit (suggest 5% of the trading balance), the
   balance guard, and a nightly report that stops a sniper whose 24-h live result falls below zero.

## What we will not do

- Copy 6Z91 / Fhbh1 trade for trade: every replay loses (-0.03 to -0.17 SOL per position) because
  we land behind them and OKTech copies even their failed buys.
- Race for the first buy with 0.045 SOL tips: that race is theirs; our edge is one step behind it.
- Dev-wallet lists as the main strategy: the edge is in one-off creators (repeat devs lost on paper).

## Checkpoints

| When | Question | Pass |
|---|---|---|
| Day 1 | Does the live trigger fire for the right reason and sell? | 10/10 buys after a qualifying buy, 0 stranded |
| Day 2–3 | Does live match paper at 0.2 SOL? | >= +0.08 SOL/trade over 100 trades |
| Week 1 | Does the scale-out exit beat the trailing stop forward? | higher bound over 300 trades |
| Week 2 | Does it hold at 0.5–1 SOL? | >= half of paper at that size |
| Week 3 | Daily live PnL | 20+ SOL/day sustained before pushing to 100 |
