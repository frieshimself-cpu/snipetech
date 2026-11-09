# Night grid (9 Nov 2026, 02:46-04:20 UTC): every strategy at 2, 5, 10, 20 and 40 SOL

The owner emptied the wallet on purpose (01:05) and asked for research and paper trading "even with 2-40 sol, legit
anything". The full lab ran once per size, on 43-48 h of tape, every trigger and exit, out of sample (test half), at
the live landing, with a paid fee per transaction, and froze each rule positive at that landing into the forward paper
test at that size (`paper freeze --buy B --per-tx T --hours 48 --top 5 --only-live`). The lab's curve fills price our
own buy's impact, so bigger buys pay more to get in.

| size | fee a tx | newly frozen | the rule | test n | net / trade | % of the buy | lower bound |
|---|---|---|---|---|---|---|---|
| 2 SOL | 0.01 | 1 | KOL's first buy in a coin, >= 0.1 SOL; SL-15 %, trail 18 % after +20 %, 300 s | 136 | +0.060 | +3.0 % | +0.010 |
| 5 SOL | 0.02 | 1 | same, trail 10 % after +20 % | 136 | +0.163 | +3.3 % | +0.033 |
| 10 SOL | 0.03 | 1 | same, trail 18 % after +20 % | 136 | +0.269 | +2.7 % | +0.007 |
| 20 SOL | 0.04 | 2 | KOL's first buy >= 1 SOL; SL-15 %, trail 10 %, 300 s | 115 | +0.727 | +3.6 % | +0.144 |
| 20 SOL | 0.04 | | KOL's first buy >= 0.1 SOL; same exit | 136 | +0.550 | +2.7 % | +0.047 |
| 40 SOL | 0.05 | 0 | nothing positive: our own buy moves the curve too far | | | | |

Frozen ids: 0cd0374b1d (2), b17196e12a (5), a0c5b59350 (10), bd20e4ebb1 and d55c50d94b (20). Their forward paper
trades start from the freeze; judge them at 20+ new trades each.

## Reading

- One family wins at every size from 2 to 20 SOL: copying one of the 19 labelled KOL wallets on its first buy in a
  coin, landed up to 6 trades behind it in its slot (the measured copy landing), with a 15 % stop and a trailing stop.
  At 0.2 SOL the same rule was flat (round 7: KOL copies refuted at 0.2): the fees ate it. Size is what changes it,
  and the edge holds at about +3 % a trade until the buy itself moves the price (40 SOL: nothing).
- Caveats, in order of weight:
  1. The landing. The lab copies at "+6" (up to 6 trades behind, same slot). Our only live KOL copies (Gfree, 6 on
     6 Nov) landed 4.3 trades behind at +29 % above the KOL's price and all lost. A crowded KOL's copiers pay high
     tips to sit right behind it; at 2-20 SOL we can pay those tips too (round 8: the place is worth it at size),
     but that is untested live.
  2. One family, one 43 h window, 136 test trades: a lower bound of +0.007 to +0.033 at 2-10 SOL is thin. The 20 SOL
     row with >= 1 SOL KOL buys has the strongest bound (+0.144 on n 115).
  3. Our sells: OKTech's exits watch 1.3 s after the buy; the shadow exit runner (exitrun) is the fix if they cost.

## Forward paper so far (07:00 UTC, trades after each freeze; the USDC fault excluded)

| id | size | trades | total | mean per trade |
|---|---|---|---|---|
| 0cd0374b1d | 2 SOL | 14 | +0.96 SOL | +3.4 % |
| b17196e12a | 5 SOL | 14 | +0.44 SOL | +0.6 % |
| a0c5b59350 | 10 SOL | 12 | -4.48 SOL | -3.7 % |
| bd20e4ebb1 | 20 SOL, KOL buys >= 1 SOL | 5 | +12.25 SOL | +12.2 % |
| d55c50d94b | 20 SOL, KOL buys >= 0.1 SOL | 7 | +11.41 SOL | +8.1 % |

At 04:31 the first 8 trades were -5 to -12 % (stops filling at -17 to -32 % at size); since then the 2 and 20 SOL rows
turned positive and the 10 SOL row stayed negative. Every row is under the 20 trades a decision needs, and two or three
coins carry the 20 SOL rows. Separate labs on the newest 24 h at 0.2 SOL (KOL buys >= 0.1, >= 1, >= 3 SOL) were flat
or negative: whatever edge there is lives at size. Fixed on the way (234698b): the KOL trigger had bought USDC on paper
(EPjF..., marked pump.fun on the tape, 11 M SOL market cap); coins above 1 M SOL are now skipped.

## Live test proposal (for the owner's yes; nothing starts without it)

- **What:** copy the 19 labelled KOL wallets' first buys of 1 SOL or more, one OKTech copy sniper, the 20 SOL row's
  exit (SL-15 %, trailing 10 %, 300 s), our copy exit off.
- **Wallet:** the sniper wallet 6VFrZ15V…, funded by the owner.
- **Size and fee:** 2 SOL a buy (the smallest size the grid says pays), priority 0.005 + tip 0.01 SOL, so we land
  at or near the front of the copiers; a second arm at the minimum fee would show what the tip buys.
- **Stop rule:** stop after 20 buys, or once the test is 2 SOL down, whichever comes first; 24 h at most.
- **Keep or drop:** keep (and step to 5 SOL) if the 20 copies average above +0.03 SOL net and land within 3 trades
  of the KOL; drop if they average below 0, or land more than 4 trades behind at +15 % or worse.
- **Money at risk:** 2 SOL of losses at most (the stop), plus fees of about 0.03 a round trip.
