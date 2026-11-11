# A copy lands behind the copied wallet's own companion wallets

11 Nov 2026. Audit row 66, open item 18. Evidence: docs/research/2026-11-11-stopped-wallets.md.

## Why

The qualify lab places a copy "behind the third buy after the copied wallet's, counted across slots" (`+3x`,
`lab.copy_entry`). The three buys stand for the buyers that beat our copy into the block.

The wallets that look best to copy are each the first of one operator's three or four wallets, which buy in the next
transactions of the same block (BNnN2M with 2pLuge, 3GzBLp, 7Pteyy on 94 to 98 % of its buys, one to three places
behind; DEdEW3, 8MaVa9, H31vEB, 9kXTVj the same way). For such a wallet the three buys are its own. The paper copy
then sits in the first place behind the operator, ahead of every outside bot. No copy of ours lands between
transactions that were sent together, and the place behind them is taken by bots that bid 10 to 100 times what we bid.

Measured on the copies after each wallet's verdict (0.2 SOL, the book's exit, 0.00421 SOL a transaction):

| wallet | behind its 3rd next buy | 4th | 5th | 6th |
|---|---|---|---|---|
| BNnN2M (three companions) | +0.0432 | +0.0109 | +0.0020 | +0.0007 |
| DEdEW3 (three) | +0.0172 | -0.0105 | -0.0146 | -0.0154 |
| 8MaVa9 (three; due to qualify 12 Nov 04:48 UTC) | +0.0190 | -0.0031 | -0.0077 | -0.0086 |
| 74xVT9 (no companion right behind it) | +0.0081 | +0.0052 | +0.0023 | +0.0003 |

A paper result at a better landing than our bid buys is not evidence (CLAUDE.md rule 2).

## What

**Companion of a wallet W:** a wallet that buys the same coin in W's block, one to `COMPANION_PLACES` (5) transactions
behind W's first buy of the coin, on at least `COMPANION_SHARE` (half) of W's first buys on the tape, and on at least
`COMPANION_MIN` (5) of them.

Both parts are needed. Next to W alone is not enough: fast bots land in the very next place now and then (the second
bot wallet one place behind DEdEW3's four, once). Often in W's block alone is not enough: a copy bot is (7DLM4c is in
BNnN2M's block on 64 % of its buys, on average 104 places behind). On six wallets checked by hand the rule finds
every companion (80 to 99 % of buys, one to three places behind) and nothing else; the nearest outsider reaches
26 %, never within three places more than once.

**Landing:** W's companions' buys in W's block are part of W's own entry. They are not counted among the N buys the
copy lands behind, and the copy never lands ahead of the last of them. Everything else stays: N buys counted across
slots within `COPY_LAND_SLOTS`, never cheaper than the closest a live copy lands (now: right behind W's companions),
never in the create slot.

So `+3x` means the same for every wallet: three buys by others between the operator and us.

## Where

1. `qualify/gates.py`: `companions(store, wallet, since) -> frozenset[str]`, one read per wallet on the tape the lab
   reads (its first buys by coin, then the buys within five places behind each in the same block).
2. `analysis/lab.py`: `copy_entry(path, i, n, latency, mates)`; a lab run carries `mates` (wallet -> its companions)
   to its workers; `collect(..., mates=...)`; each wallet's row says how many companions it was scored behind
   (`mates`). `LAB_VERSION` stays 2: the book lifts a live pause on a verdict from a newer lab, and a stricter landing
   is no reason to copy a wallet again that lost live.
3. `qualify/run.py`: the qualify lab's runner finds the companions of the wallets it measures and passes them. The
   `stopped-wallets` study uses the same runner.
4. `qualify/gates.py` `verdict_for`: a wallet scored behind companions says so in its reasons.

Not in this change: labs started by hand or by the agents with `--copy-position` on a whole label group (they keep
counting every buy; said in the lab's help), and the crowding rule (its gap to the third follower still counts
companions: it stays as strict as it was).

## Effect, expected

- Wallets that lead a set lose their inflated numbers at their next review: BNnN2M about +0.0007, DEdEW3 about
  -0.015, 8MaVa9 about -0.009 (their old `+6x`). None qualifies on a place we do not reach.
- 74xVT9 keeps its number: its three wallets buy 9 to 13 places behind it on 44 to 56 % of its buys and within three
  places on 13 to 18 %: no companion by the rule.
- A forward check re-reads a wallet's copies from the lab of its run, so the copies after a pick are scored at the
  corrected landing from then on.
- One more read per measured wallet. It is on indexes and returns a handful of rows per coin.

## Checks after the deploy

- `stopped-wallets` again: the three wallets above fall to about their `+6x` numbers at `+3x`; 74xVT9 is unchanged.
- A qualify run's time and `secs` (lab, gates) against the runs before.
- 74xVT9's next verdict still qualifies; 8MaVa9's does not.

## Risks

- A wallet with a loose set (companions sent apart, like 74xVT9's) is not caught: its copy is still scored ahead of
  its own later wallets on some buys. Its numbers fall slowly with the landing, so the error is small; the live
  landing record (`trades_behind`) is what will say.
- A persistent fast bot that lands within five places on half a wallet's buys would be taken for a companion and the
  copy scored behind it. That errs to the cautious side.
