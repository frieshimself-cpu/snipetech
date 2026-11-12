# A paper place that follows the bid, and a freeze at a place and bid of its own (12 Nov 2026)

**Outcome.** A paper copy can now stand where its bid puts it, not at a fixed place. A rule can be frozen and
tested forward at that place, with the bid charged on every buy. It is built and tested on synthetic tapes. **It has
not run on the server yet.** It is a model: it is not where our copies land live, and every output says so.

## Why

The owner's rule: "Has to be profitable paper trade first" before any live copy at real size.

Until now a paper copy stood at a fixed place behind the followed wallet (`+0`, `+1`, `+2`, `+Nx`). The bid was only a
cost. A test of "we pay 0.05 SOL" at `+0` assumed the bid wins the first place every time. The playbook
([the three bots](2026-11-12-winning-bots.md)) says it does not: the highest bidder is first in 56 % of blocks.

The forward paper test could not hold such a rule at all. `paper freeze` took no place and no bid, so the analyst's
freeze of 12 Nov 18:01 was refused ("a plain freeze would score +6, where the rule loses").

## What a bid landing is

The tape holds every trade's priority fee and tip since 11 Nov 16:34 UTC. For every moment a wallet rule follows, the
lab reads the followed wallet's block and the ten blocks after it. A copy that pays a bid of X SOL lands:

1. behind the followed wallet's companions (the wallets its operator buys with), as every copy does;
2. behind every outsider that bid at least X, in that block and in the ten blocks after it;
3. behind an outsider that stands in the first two transactions behind the wallet, whatever it bid. No bid beats that
   one: it was sent with the wallet's own buy, or it is simply faster;
4. ahead of every other outsider.

Our own wallets are nobody's place. A moment with no fee record on the tape is left out and counted. It is never
guessed.

Two positions:

- `bid`: the place above.
- `bid-`: one buy further back on every moment, for the faster wallet the tape cannot show. **The owner's bar uses
  this one.**

### Where the numbers in the model come from

- **Two transactions** (`bid_landing.FAST_TXS`): the playbook, 139 blocks where a bid of 0.01 SOL or more was the
  highest and still not first. The first buyer there had bid 0.003 SOL at the median and stood two transactions
  behind the followed wallet.
- **Ten blocks** (`lab.COPY_LAND_SLOTS`): how far a counted place (`+Nx`) already looks. Not every copy lands in the
  wallet's own block: 20 of 24 did since 7 Nov, and one of the two placed copies of 12 Nov landed a block later.
- **An equal bid is ahead of us.** The tape does not hold the fee per compute unit, which is what orders a block.

### What the model gets wrong on purpose

The order follows the bids in 60 % of pairs, not in all. So on the tape a lower bid can stand ahead of a bid that
beats ours. The copy is then priced behind both. That is the worse price. The model never gives a place better than
the bid buys.

### What it cannot know

- A copy that lands a block late is behind every trade of the wallet's block. The model counts only the higher bids
  there.
- Where no one bought in those ten blocks, `bid-` counts one place further back but pays the same price as `bid`.
  There is no trade to stand behind.

## A first look on real blocks (not a lab, no result in SOL)

Read through read-only SQL on 12 Nov about 18:30 UTC: Cupsey's main wallet, its first buy of 1 SOL or more in a curve
coin since the fee records began. **18 moments. That is a small sample.** Companions were not found by this read (it
sees 500 rows a query), so a wallet of Cupsey's own may be counted as an outsider.

| our bid (SOL) | first | second | third or later | a higher bid in the block | an outsider no bid beats |
|---|---|---|---|---|---|
| 0.003 | 0 % | 28 % | 72 % | 72 % | 33 % |
| 0.01 | 11 % | 33 % | 56 % | 72 % | 33 % |
| 0.035 (our live copies) | 22 % | 50 % | 28 % | 50 % | 33 % |
| 0.05 | 22 % | 56 % | 22 % | 50 % | 33 % |
| 0.1 | 22 % | 56 % | 22 % | 50 % | 33 % |
| 0.3 | 28 % | 67 % | 6 % | 39 % | 33 % |

Read with care. Behind Cupsey the model gives the first place far less often than the playbook's table for all 57
wallets (45 to 51 % at these bids). Two reasons show in the rows: someone stood within two transactions of Cupsey in
a third of the blocks, and in half of them someone bid 0.1 SOL or more. Our two real copies at 0.035 SOL landed first.
They were on a PumpSwap coin, which this read does not cover.

## The check against our real buys

`snipetech place-bids --model-check` compares the model with every buy of ours the chain reader placed
(`fills.landing_src = 'chain'`). For each buy: how many outsiders the model puts ahead of a copy with that bid in that
block, against how many really were.

Today there are two such fills. **That shows the mechanism works. It is not a calibration.** The command says so until
it holds 30 fills. For a coin the tape does not hold (both fills today), the fills row names only the buyers that
stood ahead of us. There the model can be shown too kind, never too harsh.

## Freezing a rule at a place and bid of its own

`paper freeze --trigger ...` now takes `--copy-position` (every value the lab takes: `+0`, `+N`, `+Nx`, `bid`,
`bid-`), `--buy-bid X` and `--sell-parts N --sell-part-secs S`.

- The rule stores the landing and the bid it was frozen with.
- The forward run scores every trade at that landing and charges the bid once on each buy.
- It has an id of its own. The same trigger and exit at another landing or bid is another rule.
- The report names both: "+1 with a 0.05 SOL bid".
- It is never shown as where our copies land live, and the hourly move to the measured copy landing leaves it alone.
- A rule frozen before keeps its id.
- Asking for today's copy landing by name, with no bid, is the plain rule.

## What changed in the code

- `src/snipetech/analysis/bid_landing.py` (new): the one reading of a block (`place-bids` reads its table from it
  too) and the model.
- `lab.bid_entry`, `lab.collect`: the entry, and `bid_landing` in a lab's result (the places the copies got, who
  stood ahead, the moments left out and why, the hours of tape that carry bids).
- `paper.freeze`, `paper.run`, `paper.report`, `paper.rescore`: the landing and bid of a frozen rule.
- `place_bids.model_check`: the check against our fills.
- `store.fee_records_from`: when the tape's fee records start. Run against the server's tape through read-only SQL:
  11 Nov 16:34:31 UTC, 22 small reads.
- The analyst's `start_job` text explains `bid`, `bid-` and the freeze.

## To run after the deploy

Nothing below was run. Start each long job once with `remote run`, then read `remote jobs`. One lab at a time from
this session: the server's analyst uses the lab slots too.

1. The owner's bar, on paper (the big wallets, 2 SOL a buy, a 0.05 SOL bid, the cautious place):
   `snipetech remote run lab --trigger '{"kind":"kol","group":"big","sol":1}' --hours 30 --buy 2 --buy-bid 0.05 --copy-position bid- --json`
2. The same lab at `--copy-position bid`, then at `+0` and at `+1` (the same costs): what the model takes away from
   the fixed places the analyst scored so far.
3. `snipetech remote --wait run place-bids --model-check --json`: the model against our real buys.
4. The freezes the analyst was blocked on (each freezes only a rule whose lab lower bound is above zero):
   - `snipetech remote run paper freeze --trigger '{"kind":"kol","group":"big","sol":1}' --copy-position +1 --buy-bid 0.05 --buy 2 --hours 72 --json`
   - the same with `--buy 5`
   - `snipetech remote run paper freeze --trigger '{"kind":"kol","group":"big","sol":1}' --copy-position bid- --buy-bid 0.05 --buy 2 --hours 30 --json`
   - the same with `--buy 5`

What to read in the lab's answer:

- `bid_landing.scored` and `bid_landing.skipped`: the moments placed, and the ones left out for want of a fee record.
  With `--hours 30` a few hours lie before the records began, so some `no_bids` are expected. `bid_unknown` should be
  0 or near it.
- `bid_landing.bid_hours`: the hours since 11 Nov 16:34 UTC that the window holds.
- `bid_landing.places`: first, second, third or later. At `bid-` first is always 0. Read `bid` beside the table
  above and the playbook's.
- `copy_companions.wallets`: above 0, or the big wallets' own side wallets were counted as outsiders.
- `strategies[0].landing` is `bid-@0.05`, `live_landing` is false, and `test.ev_lower` above zero on enough moments
  (`test.n`) is the paper bar met. Under zero: the rule does not pay at the place that bid buys.

## Before this goes near live money

- A bid landing is a model with two real fills behind it. A positive lower bound at `bid-` is the owner's paper bar.
  It is not proof that a live copy lands there.
- The live sniper must pay the bid the paper rule was charged, in the tip (a tip is paid once and comes back when a
  send fails; OKTech charges the buy's priority fee again on every sell).
- Read `place-bids --model-check` after every batch of live copies. "Model too kind" on real fills means paper at
  that landing flatters the rule.
