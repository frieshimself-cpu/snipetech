# The bot turns its own wrapped SOL back into plain SOL for curve buys

11 Nov 2026. The owner's yes, 21:50 UTC: "Yes, convert as needed" (docs/live-recipe.md). Audit row 72, open item 20.

## Why

A buy on pump.fun's curve is paid whole in plain SOL; only a coin that has left its curve (PumpSwap) is paid in
wrapped SOL. The wallet's SOL turns into wrapped SOL by itself: a sale on PumpSwap pays wrapped SOL, and a plain
transfer out of the wallet moves only the plain part. On 11 Nov the wallet held 0.47 wrapped SOL and 0.004 plain SOL,
and the first copy of the one qualified wallet failed for the 0.2 SOL it could not take from plain SOL. Nothing turns
wrapped SOL back. OKTech does not (it wrapped 0.0996 SOL at a sniper's start on 6 Nov and never unwrapped).

## What the owner agreed to, and nothing more

1. It is built first.
2. The first conversion is a test of 0.05 SOL.
3. After that the bot keeps plain SOL for up to two 0.2 SOL curve buys (0.41 SOL) whenever the wallet has it in
   wrapped form, and leaves the rest wrapped for PumpSwap buys.
4. Nothing leaves the wallet.

## How one conversion works

One transaction, signed by the wallet alone (the key the server already holds for `/sell`, `SNIPER_SECRET`), with a
small priority fee (no hurry):

1. make a temporary token account at an address derived from the wallet and a random seed (system program,
   `create_account_with_seed`, built by the `solders` library), paid for by the wallet;
2. make it a wrapped-SOL account owned by the wallet (`InitializeAccount3`);
3. move the amount from the wallet's wrapped-SOL account into it (`TransferChecked`, authority: the wallet);
4. close it to the wallet (`CloseAccount`): the amount and the rent of step 1 arrive as plain SOL.

The wallet's own wrapped-SOL account is never closed: OKTech buys on PumpSwap from it and our own sale pays into it.

**Where the instruction layouts come from.** Steps 2 and 3 are copied from a transaction landed from our own wallet
(the buy of 10 Nov 22:19:33, tests/fixtures: its `TransferChecked` from our wrapped-SOL account and its
`InitializeAccount3`), and a test holds the built bytes against it. Step 4 has no landed copy from our wallet (OKTech
never closes an account). So nothing is sent on trust: **every conversion is first simulated on the chain**, and it is
sent only when the simulation shows, with the chain's own log, the four instructions by name and exactly this result:
the wallet's plain SOL up by the amount less the fee, its wrapped SOL down by the amount, no error. Anything else is a
refusal with the reason.

**Where the SOL can go.** The destination of step 4 is the signing wallet, taken from the key, not from an argument.
A test checks that every account the transaction may write is the wallet, its wrapped-SOL account or the temporary
account.

## When it converts by itself (after the test)

In the agents' round, every 15 minutes, from the round's own reading of the wallet:

- only while the book is live and the kill switch is not set;
- only when the wallet cannot pay one curve buy of the book's size (0.205 SOL) and its plain and wrapped SOL together
  can; a wallet that can pay one is left alone (so the two forms do not swing back and forth);
- the amount: up to `keep_curve_buys` (2) buys of plain SOL, never more than is wrapped, never under 0.01 SOL;
- at most 6 conversions a day; each one is a line in the notes (ours, not the owner's move) and a row in
  `operator/unwrap.json`;
- `[book] keep_curve_buys = 0` switches it off. It is 0 until the 0.05 SOL test has been read on the chain.

By hand (the owner's command, never an agent's): `snipetech unwrap --sol 0.05`, `--dry-run` simulates and sends
nothing, and with no amount it shows what the rule above would convert.

## What is not known and how it is found out

Whether OKTech wraps plain SOL again by itself when a sniper starts or a PumpSwap buy needs it. If it does, the rule
above converts at most once a round and six times a day, each for a fee of about 0.00001 SOL, and the notes show the
swing; the rule is then changed, not left to flap. Read after the test: the wallet's two balances over the next rounds.

## Checks

- unit tests: the instruction bytes against the landed ones; the writable accounts; the plan's amounts; a simulation
  that shows anything but this conversion refuses; a dry run sends nothing; the command is the owner's.
- on the server: `unwrap --sol 0.05 --dry-run` (the chain's simulation), then the 0.05 SOL conversion, then the two
  balances read from the chain; written into docs/live-recipe.md with the signature.
