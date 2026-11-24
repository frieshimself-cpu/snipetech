# Our own buyer: `snipetech buy` (23 Nov 2026)

**Where it stands.** The repo can now build, sign and price a pump.fun buy of its own, and send it to Jito as a bundle.
Until now every buy was OKTech's. Everything here is checked on recorded mainnet transactions and a fake chain only.
Nothing has been sent. No live test has run yet. The server's job list does not run it, and the agents cannot start it.
A review of 23 Nov found 28 problems; all are fixed and each has a test (the list is at the end).

**Why it matters.** The place right behind a big wallet's buy is the one that pays on paper (docs/roadmap.md, 23 Nov).
Order-flow networks sell that place as a seat: they hand the buyer the leader's signed transaction before it lands.
OKTech cannot use a seat, because it only reacts to buys that have already landed. This buyer can.

## How to run it

- `snipetech buy MINT --sol 0.02` is a dry run. It reads the coin, works out the quote, builds the buy and asks the
  chain to simulate it. It prints the quote, the least coins it will accept, every account's role, what the wallet
  must hold and the simulation. Nothing is sent, and nothing in it can be sent: the simulated buy is unsigned and
  carries no blockhash of the chain's. With the wallet's key present, the key signs a copy over no blockhash; that
  copy is checked on this machine and never leaves it.
- `--send` sends the bundle and prints its id. That is a live trade, and CLAUDE.md rule 1 applies in full:
  - No live trade without the owner's explicit yes in chat for that specific step: the wallet, the size and how long.
  - The owner's standing yes (12 Nov 2026) covers small live **tests** only: from the sniper wallet, at most 0.2 SOL a
    buy and at most 2 SOL lost a day. Inside both numbers, a test needs no further yes.
  - Each such test is written into docs/live-recipe.md before it starts (what it tests, the sniper or buyer, the stop
    rule) and again after (every fill).
  - Anything else needs the owner's yes for that step: a run for profit, a bigger size, another wallet.
  - **This buyer's first live send waits for the owner's yes in chat, whatever its size** (the owner was told so on 23
    Nov ~22:15 UTC, when the build began: "Nothing goes live until you say yes to a 0.1 SOL test"). Sending our own
    signed buys is a new way of placing live buys, which the hourly round's rules also put to the owner first.
- `--slippage C` (default 0.10): the buy fails rather than fill at a price more than C above the quote's.
- `--tip SOL` (default 0.001, at most 0.01): the tip to Jito. It is always the transaction's last instruction.
- `--behind B64`: the leader's signed transaction, as a seat hands it over (see below). A blank value is refused.
- `--region R`: the Jito block engine: amsterdam (the default), frankfurt, ny, tokyo or slc.
- Run it with the server's own database (on the server, the default `--db`). The /stop flag, the wallet guard and the
  record of our buys are all read and written beside the database the command opens (`--db`, `SNIPETECH_DB`). A run
  against another database never sees the owner's /stop or the server's guard.

## What it checks before anything is sent

- **The per-buy cap.** `--sol` is at most 0.2 SOL, the owner's standing yes. The ceiling is in the code
  (`BUY_MAX_SOL`). `[operator] buy_max_sol` can lower it, never raise it, and a value that is not a number above 0 is
  refused (a setting of `inf` used to switch the cap off). Raising the ceiling is the owner's decision and a reviewed
  change of the code. Exactly 0.2 SOL is allowed.
- **The tip's own cap.** The tip is at most 0.01 SOL (`BUY_MAX_TIP_SOL`). Before, the tip was held only to the buy's
  cap, so one send could put 0.2 SOL into the curve and tip another 0.2 SOL away.
- **The owner's /stop flag** (operator/STOP, the same flag the runner and the guard read). A send is refused before the
  chain is asked anything. The flag is read again after the buy is built and right before the bundle goes out, since
  each read before it can take minutes of retries. A dry run still runs and says that a send would be refused.
- **The owner's wallet-wide guard** (the day stop and the floor, as for every sniper start). A send is refused while
  the guard holds, or while it cannot tell. A dry run asks it too (it only reads) and says what it would refuse.
- **The day stop sees these buys.** Each buy is written into the store's record of our own buys (`own_buys`) before its
  bundle goes out. The guard counts the buys on that record, and the sales that close them, in the day's result. A buy
  that cannot be written into the record is not sent. Only the guard reads this record so far: the agents' fill tools,
  the loss alert and the book still read these trades as the owner's own.
- **The wallet's SOL.** The wallet must hold the buy, its network fee (0.001005 SOL), the tip, the coin's token account
  (0.0015 SOL when it is new), the wallet's pump.fun volume account (0.0013 SOL, opened by a wallet's first buy) and the
  cost of selling the coin back. A buy never leaves the wallet unable to pay its own exit.
- **The key.** A send needs SNIPER_SECRET. The key is read as the sale reads it. It is never printed, logged or put in
  an error. A test checks every path of the command for it, including a key for another wallet, a refusing block
  engine, a lost answer and a gateway's error.
- **A bundle needs a tip** of at least 1,000 lamports (Jito's least).
- **Every safeguard is handed in.** A send from code (not only from the command) is refused unless the /stop check, the
  guard and the record are all given.

## What an answer from the block engine means

- **Refused, nothing sent:** the block engine said no (its own error message), or a 4xx such as 400 or 429.
- **Not known:** a 5xx from the gateway in front of it (502, 503, 504), a page that is not a JSON-RPC answer, or no
  bundle id. The bundle may have been taken and may still land. The command says so and names our transaction's
  signature: look it up before sending again. A second send is a different transaction (it tips another tip
  account), so it could buy twice.

## What it refuses, and why

- **A coin that has left its curve** (the curve reads complete). It trades on PumpSwap now; buying there is a later
  step.
- **A curve priced in another token** than SOL, and a curve that names WSOL as its quote explicitly. Every landed buy
  is on a curve with the default quote key; a WSOL one might take the SOL from the wallet's WSOL account.
- **Mayhem-mode and cashback coins.** There is no landed buy of either through this instruction to check the accounts
  against. Holder-reward coins are covered: two landed buys of such coins (one of ours, one of another wallet) show
  the same accounts.
- **A coin with a creator rate of its own.** pump.fun lets a coin set its own creator fee when it is made. Every landed
  buy is of a coin without one (the default, 30 basis points). On a coin at 100 basis points the quote would be 0.69 %
  too high. Such a coin is refused until a landed buy of one shows what the program charges.
- **A coin of the older token program.** Every landed buy checked is of a Token-2022 coin.
- **A buy that would take the curve's last coins.** The program then keeps buying past the curve's end, and that part
  is not quoted here.

## How it was built from our own buys, not from memory

- **The instruction** is the one OKTech's curve buys from our wallet use: `buy_exact_quote_in_v3` (spend this much SOL,
  get at least this many coins). The account list comes from our wallet's landed buy of 23 Nov, 03:22:01 UTC (0.02 SOL
  of G21H7ob4…pump, signature gXv9uz3u…). `tests/fixtures/pump_buy_v3_template.json` is that buy, taken out of the
  landed transaction by `tests/fixtures/build_pump_buy_v3_template.py`, and re-derived per coin.
- **Five more landed buys** through the same instruction derive alike: two of ours (6 Nov) and three of other wallets.
  Each transaction chooses two accounts itself:
  - The buyback fee recipient is one of several that pump.fun accepts. The six buys name five different ones. Ours
    names the one our 23 Nov buy (and our 5 Nov sell) landed with.
  - On 6 Nov, OKTech bought into a "seed account" (switched off by the 6 Nov audit). Ours opens the wallet's own token
    account, as the 23 Nov buy did.
- **The transaction** has the landed buy's shape. It sets the same compute budget (100,000 units at 10,000,000
  micro-lamports), opens the coin's token account, makes the buy, and then pays the tip to Jito. Unlike the landed
  buy, it has no durable nonce (ours uses a recent blockhash, as the sale does) and no lookup table. The landed buy
  tipped Astralane in the middle; ours tips Jito, last.
- **The quote** is pump.fun's own arithmetic. It reproduces eight landed buys to the coin: three of ours and five of
  other wallets, normal, holder-reward and mayhem coins, from 1.8 to 359 SOL of market cap. (The ninth exact-in buy in
  the fixtures takes a curve's last coins, the kind of buy this buyer refuses.) Of the SOL spent, the most whose fees
  fit goes into the curve. The fees are 0.95 % to pump.fun and 0.30 % to the creator, each rounded up. The coins are
  priced on one lamport less than goes in. (On most coins half of pump.fun's fee goes to its buyback; that comes out
  of the fee, never on top.) The dry run checks this arithmetic, with the buy's own spend and rates, against the
  program's own fill in the simulation, and shows the rates the program charged beside the quote's.

## How a seat would use `--behind`

1. The seat hands over the leader's signed transaction, as base64, before it lands.
2. `snipetech buy MINT --sol X --behind <base64>` asks the chain to simulate the leader's transaction as it is. What
   the leader buys is its own fill in that simulation: pump.fun's trade record of the coin in the log. This also works
   when the leader buys through a router (Axiom's buys do). The quote and the least coins out stand on the curve the
   leader leaves, not on today's curve, and that curve must be the one the leader's fill ends on.
3. With `--send`, the bundle is the leader's transaction first and ours right after it. The block engine lands both in
   that order or neither. Our buy can never land in front of the leader's.
4. If the leader's transaction fails in simulation, or buys none of this coin, the buy is refused. If the leader lands
   on its own first, our bundle cannot land, and it costs nothing.

## Not done yet

- **No live test.** The first step is a dry run on the server's RPC (nothing sent). The first send, a test of at most
  0.1 SOL, waits for the owner's yes in chat, and is written into docs/live-recipe.md before it starts and after.
- **What it is for (the check of 23 Nov, docs/roadmap.md 24 Nov 01:10):** a seat right behind a big wallet's buy,
  bought from whoever holds that order flow, with `--behind`. Sent on its own, reacting to a landed buy, it would land
  about where OKTech's copies land (the fastest reacting wallets stand a median 42-58 block places behind those buys,
  OKTech about 68), so it is not built to race.
- **Not on the server's job list** (`ops.ALLOWED`), and not an agent's command. Adding it to the job list is its own
  step.
- **PumpSwap buys**, and mayhem, cashback and own-creator-rate coins, wait for a landed copy to build from.
- **Speed.** Each run reads the guard and the coin, and simulates the leader's transaction, before it builds anything.
  A resident buyer that holds those reads ready is the next step for racing a seat. It must hand in the /stop check,
  the guard and the record, as the command does; the code refuses a send without them.
- **The default region** (Amsterdam, where the order-flow network in the roadmap streams from) is a guess. Which block
  engine is nearest the machine that sends is not measured yet.
- **The other readers of the bot's trades** (the agents' fill tools, the loss alert, the book's records) do not read
  the record of our own buys yet. Only the wallet-wide guard does.

## The review of 23 Nov, and what changed

- Safety: a blank `--behind` sent the buy alone (now refused). A dry run signed a landable buy over a live blockhash
  and gave it to the RPC (now unsigned, no blockhash). /stop was read only once (now again before the send). The
  per-buy cap could be switched off by a setting (now a ceiling in code). The tip could be as large as the buy (now
  its own cap). A gateway's 5xx said "nothing sent" (now "not known"). The day stop did not see these buys (now on a
  record the guard reads). A send from code needed no safeguards (now refused without them).
- Correctness: a coin's own creator rate and an explicit WSOL quote are refused. The volume account's rent is counted.
  The leader's buy is told by its own fill, not by two reads of the curve from different moments. The quote's
  evidence is eight landed buys, not nine.
- Tests: the blockhash, the commitment levels, `--slippage`, the exact cap, the wallet's need to the lamport, the
  check on a fill that disagrees, a wrongly signed leader, every refusal path for the key, and the key exported on the
  machine running the tests.

Code: `src/snipetech/solana/buy.py`, `src/snipetech/solana/bundle.py`, the record in `src/snipetech/store.py`
(`own_buys`), the guard's reading in `src/snipetech/operator/risk_guard.py`. Tests: `tests/test_buy.py`,
`tests/test_bundle.py`.
