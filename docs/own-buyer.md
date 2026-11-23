# Our own buyer: `snipetech buy` (23 Nov 2026)

**Where it stands.** The repo can now build, sign and price a pump.fun buy of its own, and send it to Jito as a bundle.
Until now every buy was OKTech's. Everything here is checked on recorded mainnet transactions and a fake chain only.
Nothing has been sent. No live test has run yet. The server's job list does not run it, and the agents cannot start it.

**Why it matters.** The place right behind a big wallet's buy is the one that pays on paper (docs/roadmap.md, 23 Nov).
Order-flow networks sell that place as a seat: they hand the buyer the leader's signed transaction before it lands.
OKTech cannot use a seat, because it only reacts to buys that have already landed. This buyer can.

## How to run it

- `snipetech buy MINT --sol 0.02` is a dry run. It reads the coin, works out the quote, and builds and signs the buy. It
  then asks the chain to simulate the buy, and prints the quote, the least coins it will accept, every account's role
  and the simulation. Nothing is sent. Without the wallet's key it builds the buy unsigned and still simulates it.
- `--send` sends the bundle and prints its id. That is a live trade. It needs the owner's yes for that step (CLAUDE.md
  rule 1). A small test inside the owner's standing yes (up to 0.2 SOL a buy, 2 SOL lost a day) needs no further yes.
  Each such test is written into docs/live-recipe.md before it starts and again after, with every fill.
- `--slippage C` (default 0.10): the buy fails rather than fill at a price more than C above the quote's.
- `--tip SOL` (default 0.001): the tip to Jito. It is always the transaction's last instruction.
- `--behind B64`: the leader's signed transaction, as a seat hands it over (see below).
- `--region R`: the Jito block engine: amsterdam (the default), frankfurt, ny, tokyo or slc.

## What it checks before anything is sent

- **The per-buy cap.** `--sol` is at most `[operator] buy_max_sol`, which is 0.2 SOL: the owner's standing yes. Raising
  it is the owner's decision. The tip may not be above the cap either.
- **The owner's /stop flag** (operator/STOP, the same flag the runner and the guard read). A send is refused before the
  chain is asked anything. A dry run still runs and says that a send would be refused.
- **The owner's wallet-wide guard** (the day stop and the floor, as for every sniper start). A send is refused while
  the guard holds, or while it cannot tell.
- **The wallet's SOL.** The wallet must hold the buy, its network fee (0.001005 SOL), the tip, the coin's token account
  (0.0015 SOL when it is new) and the cost of selling the coin back. A buy never leaves the wallet unable to pay its own
  exit.
- **The key.** A send needs SNIPER_SECRET. The key is read as the sale reads it. It is never printed, logged or put in
  an error; a test checks every path of the command for it.
- **A bundle needs a tip** of at least 1,000 lamports (Jito's least).

## What it refuses, and why

- **A coin that has left its curve** (the curve reads complete). It trades on PumpSwap now; buying there is a later
  step.
- **A curve priced in another token** than SOL.
- **Mayhem-mode and cashback coins.** There is no landed buy of either through this instruction to check the accounts
  against. Holder-reward coins are covered: two landed buys of such coins (one of ours, one of another wallet) show
  the same accounts.
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
- **The quote** is pump.fun's own arithmetic. It reproduces nine landed buys to the coin: ours, and eight of other
  wallets, normal, holder-reward and mayhem coins, from 1.8 to 359 SOL of market cap. Of the SOL spent, the most whose
  fees fit goes into the curve. The fees are 0.95 % to pump.fun and 0.30 % to the creator, each rounded up. The coins
  are priced on one lamport less than goes in. (On most coins half of pump.fun's fee goes to its buyback; that comes
  out of the fee, never on top.) The dry run also checks this arithmetic against the program's own fill in the
  simulation.

## How a seat would use `--behind`

1. The seat hands over the leader's signed transaction, as base64, before it lands.
2. `snipetech buy MINT --sol X --behind <base64>` asks the chain to simulate the leader's transaction as it is. The
   quote and the least coins out stand on the curve the leader leaves, not on today's curve. This also works when the
   leader buys through a router (Axiom's buys do).
3. With `--send`, the bundle is the leader's transaction first and ours right after it. The block engine lands both in
   that order or neither. Our buy can never land in front of the leader's.
4. If the leader's transaction fails in simulation, or does not buy this coin, the buy is refused. If the leader lands
   on its own first, our bundle cannot land, and it costs nothing.

## Not done yet

- **No live test.** The first one is a 0.02 SOL dry run on the server's RPC, then a 0.02 SOL send inside the standing
  yes, each written into docs/live-recipe.md.
- **Not on the server's job list** (`ops.ALLOWED`), and not an agent's command. Adding it to the job list is its own
  step.
- **PumpSwap buys**, and mayhem and cashback coins, wait for a landed copy to build from.
- **Speed.** Each run reads the guard and the coin, and simulates the leader's transaction, before it builds anything.
  A resident buyer that holds those reads ready is the next step for racing a seat.
- **The default region** (Amsterdam, where the order-flow network in the roadmap streams from) is a guess. Which block
  engine is nearest the machine that sends is not measured yet.

Code: `src/snipetech/solana/buy.py`, `src/snipetech/solana/bundle.py`. Tests: `tests/test_buy.py`,
`tests/test_bundle.py`.
