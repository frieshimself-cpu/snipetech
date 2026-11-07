"""How tests/fixtures/hunt_fills_5nov_launches.tsv and hunt_fills_5nov_tape.tsv.gz were built (kept for the record).

The five hunt fills of 5 Nov 2026 21:13-22:10 UTC whose sniper had its thresholds enforced (minTotalSol 2, a 1 s
window, triggerMode all: docs/live-recipe.md, "Hunt live, 5 Nov"): RLT, Bricks, Suncat, Gogeta, II. Exported from
the server's database on 7 Nov with `snipetech remote sql` (the launches rows, and every trade of each coin up to
12 s after its create, our own buy included):

  SELECT mint, user, creator, name, symbol, uri, bonding_curve, slot, ts, sig, token_program, mayhem, holder_reward,
         quote_mint FROM launches WHERE mint IN (...)
  SELECT t.sig, t.ev, t.slot, t.pos, t.ts, t.mint, t.venue, t.user, t.is_buy, t.quote, t.base, t.fee, t.fee_bps,
         t.q_after, t.b_after, t.r_base_after, t.ix FROM trades t JOIN launches l ON l.mint = t.mint
         WHERE t.mint = ? AND t.ts <= l.ts + 12 ORDER BY t.slot, t.pos

Both outputs are tab-separated with a header row; the trades file is gzipped. Nothing here is secret: public chain data.
"""
