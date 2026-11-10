# Three profitable bots read off the chain, and what a known wallet's buy is worth by context (10 Nov, 12:30 UTC)

**Question (owner, 10 Nov):** "You can literally see on chain exactly what he's doing." "Look at every factor possible:
coin type, volume, time of day, who's in the coin, specific people, market cap, literally anything."

**Answer so far: the three bots do the same simple thing (buy 1-3 SOL right behind someone's 1-3 SOL buy, in the same
block, sell on a timer within seconds), and the tape says that trade is worth most when a second known wallet is
already in the coin, the coin has volume and some age, and the buy followed is 1 to 3 SOL.** All of it is before our
own costs; the paper test at 1-5 SOL with the bid counted is the next step (idea I51).

Tools (new, 10 Nov): `snipetech playbook <wallet>` and `snipetech copy-context`. Tape: 72 h to 10 Nov 12:15 UTC.

## The bots

| | 6Z91Dgiz… | niggerd5…dkm5zS | Fhbh1DTU… |
|---|---|---|---|
| coins | 1,525 | 186 | 532 |
| result, closed trips, before bids | +380 SOL | +73 SOL | +92 SOL |
| first buy in the create block | 46 % | 1 coin | 16 % |
| first buy in the same block as another buy | 33 % | 82 % | 67 % |
| first buy 1-2 blocks after another buy | 20 % | 17 % | 15 % |
| usual sizes paid (SOL) | 1.8, 3.0, 1.2, 2.0, 2.5 | 3.0, then 3.2-5.5 | 2.0, 3.0, 0.5, 5.0 |
| the buy it follows (median, SOL) | 1.6 | 2.0 | 1.45 |
| trades behind that buy (median) | 1 | 1 | 1 |
| SOL in the curve when it buys (25-75 %) | 9-28 | 8-28 | 14-30 |
| coin age when it buys (median) | 80 s | 2.5 min | 18 s |
| first sell after (25 / 50 / 75 %, slots) | 7 / 9 / 15 | 10 / 14 / 19 | 5 / 8 / 19 |
| share sold first (median) | 40 % | 25 % | 100 % |
| sells a coin (median) | 3 | 5 | 2 |
| gain at the first sell (25 / 50 / 75 %) | 0 / +5.2 / +14.2 % | −0.8 / +6.0 / +15.0 % | 0 / +2.8 / +12.1 % |
| first sell at a loss | 25 % | 28 % | 25 % |
| the wallet it followed sold first | 19 % | 20 % | 11 % |
| bid a buy where known (median; n) | 0.033 SOL (107) | 0.21 SOL (8) | 0.0011 SOL (28) |

- **No wallet list.** 6Z91 sat right behind 446 different wallets; its ten most followed cover 19 % of its copies, and
  the wallets it follows most often are followed about a quarter of the time (5qMp9p2o 28 %, Solanaswaggy 27 %,
  3VUNtVtj 20 %). The other two: 83 and 252 wallets. Many of the wallets ahead of them are the same kind of bot.
- **The exit is a timer.** The first sell comes 3 to 6 seconds in, up or down, in pieces; it does not wait for the
  wallet ahead to sell.
- **Create-block buys (6Z91):** 696 coins of 621 different creators, behind a creator's own buy of 1.4 SOL at the
  median: a rule on the create itself, not a creator list. +126 SOL, 46 % wins.

## What a known wallet's buy is worth by context

17,439 first buys of 0.5 SOL or more by labelled wallets (18 KOLs, 2,663 tracked) in 9,794 coins launched in the
window. Entered right behind the buy, in its block, where 20 of our 24 live copies since 7 Nov landed. Average price
change in percent, before 2.5 % of fees, the bid and our own push on the price; "13 slots" is 5 seconds.

| split | n | 2 slots | 13 slots | 30 slots | 150 slots | above fees at 13 |
|---|---|---|---|---|---|---|
| all | 17,439 | +5.9 | +4.2 | +2.9 | −2.4 | 48 % |
| **the first known wallet in the coin** | 9,794 | +3.6 | +0.5 | −1.6 | −8.9 | 40 % |
| **the second** | 2,379 | +8.9 | +8.5 | +8.1 | +4.9 | 56 % |
| the third | 1,521 | +8.9 | +9.6 | +9.1 | +6.2 | 59 % |
| the fourth or later | 3,745 | +8.7 | +9.0 | +8.6 | +6.5 | 61 % |
| coin volume before: under 5 SOL | 2,783 | +1.1 | −1.8 | −5.2 | −12.7 | 36 % |
| 5-20 SOL | 4,168 | +4.2 | +0.5 | −1.3 | −9.9 | 41 % |
| 20-60 SOL | 4,088 | +7.6 | +6.1 | +4.9 | −0.1 | 50 % |
| 60 SOL and more | 6,400 | +8.0 | +8.0 | +7.8 | +5.4 | 58 % |
| coin age: under 30 s | 8,064 | +5.4 | +2.7 | +0.6 | −6.5 | 44 % |
| 30 s to 5 min | 6,967 | +5.7 | +4.4 | +3.6 | −0.9 | 50 % |
| 5 to 60 min | 1,923 | +8.0 | +8.9 | +8.5 | +6.9 | 60 % |
| the followed buy: under 1 SOL | 10,778 | +3.0 | +1.1 | −0.2 | −5.4 | 42 % |
| **1-3 SOL** | 5,613 | +11.3 | +10.5 | +9.5 | +3.6 | 61 % |
| 3-10 SOL | 932 | +7.4 | +4.2 | +1.2 | −1.7 | 53 % |
| 10 SOL and more | 116 | +0.9 | −11.4 | −16.4 | −21.8 | 18 % |
| hour (UTC) 04-07, the best | 3,054 | +7.3 | +6.0 | +4.5 | +1.5 | 51 % |
| hour (UTC) 12-15, the worst | 2,060 | +4.3 | +3.0 | +1.4 | −3.0 | 46 % |
| a labelled KOL | 280 | +14.4 | +14.5 | +14.6 | +15.3 | 75 % |
| a tracked wallet | 17,159 | +5.8 | +4.0 | +2.7 | −2.7 | 48 % |

The means barely move without their 3 best events (the `ex3` columns of the job output), so no single coin makes them.

**Other buys later in the followed wallet's block** (what the bots add): none, −3.0 % at 13 slots; under 2 SOL, +3.3 %;
2-10 SOL, +20.0 % (83 % above fees); 10 SOL and more, +51 %. This one is known only after the block: it says the gain
belongs to whoever is right behind the leader, ahead of the other bots, which is what the bid buys.

## The same buys entered one block late

The same 17,426 buys, entered at the end of the block after the wallet's (`copy-context --entry-slots 1`): −0.3 % at
5 slots, −1.7 % at 13, −2.9 % at 30, −8.0 % at 150; a quarter to a third above fees. No split is worth more than +1 %:
the second known wallet in −0.2 % at 13 slots, 60 SOL of volume +0.1 %, a 1-3 SOL buy −0.7 %, a labelled KOL +0.9 %.
The whole value of the trade is inside the followed wallet's block, right behind it.

## Every wallet's buy of 1 SOL or more, and which ones the bots took

63,527 first buys of 1 SOL or more by any wallet in 6,744 coins of the last 24 h, entered right behind
(`copy-context --any-buyer --min-sol 1 --behind-leader --taken-by <the three bots>`).

- **Young, thin coins are not readable this way.** Under 30 s of age, under 5 buyers or under 5 SOL in the curve the
  averages are +40 to +90 %: creators' bundles, several wallets of one sender in one block, where nobody can land
  between them. Leave these rows out.
- **Established coins:** behind any 1 SOL buy in a coin older than 30 s, +3.5 to +4.6 % at 13 slots; with 60 SOL of
  volume +3.3 %; with 100 or more earlier buyers +3.0 %. Less than behind a known wallet in the same coins (+8 to +9 %),
  so who buys matters.
- **Who buys:** a labelled KOL +15.2 % at 13 slots (120 buys, 75 % above fees); a tracked wallet +6.4 %.
- **What the bots took:** one of the three was in the block behind 1.3 % of these buys: 4.2 % behind a labelled KOL
  and 4.0 % behind a tracked wallet against 1.1 % behind an unlabelled one, 2 to 3 % in the youngest coins, 0.9 % at
  12-15 UTC. No single factor here makes them buy: their trigger is narrower than any one split, which is what the
  50-factor `study` of each bot is for.

## The second bot's rule, from 50 factors (`study`, 12:55 UTC)

282 entries of niggerd5…dkm5zS against 4,230 moments it passed on, in the same minutes, and 279 sells against 1,055
moments it held. A depth-3 tree on the strongest factors, cross-validated at 93 %:

- **It buys when the coin is hot this second:** at least 1 SOL traded in the last 5 s (its median 5.9 SOL; the moments
  it passed on, 0), a trade in the same second, and a buy of 0.87 SOL or more in the last minute. 261 of its 282
  entries fit, against 231 of the others.
- **What "hot" looks like at its entries (medians):** 22 SOL traded in 30 s and 39 SOL in a minute, 6 buys of 1 SOL or
  more in the minute, 20 different buyers in 30 s, price +11 % in 5 s, +25 % in 30 s and +96 % in 5 minutes, among the
  12 busiest coins of the market, market cap 74 SOL (40 to 186: 18 to 75 % of the curve), 82 s old (6 s to 6.5 min).
- **Who is in does not matter to it:** a labelled KOL bought in the minute before 3.5 % of its entries. The creator had
  bought 3 SOL at the median and had already sold in 82 %.
- **It sells when the tape pauses:** every one of the 113 sells made after a second with no trade in the coin, held
  over a second, fits "held more than 1 s and the last trade a second or more ago"; the rest come as others' sells pile
  up (more than 5 in 5 s). Its first sell is 3 s in at the median. This is the dead exit OKTech has (1 s at least).

So this bot is not a copier at all: it rides volume bursts in the same block as a 1 SOL buy and leaves at the first
pause. The lab's momentum rules were scored behind the 3rd buy after the trigger; at the place right behind the buy,
with the bid charged, they have not been.

## The paper test by place in the block and by bid (lab, 13:16-13:40 UTC)

The rule "a tracked wallet's first buy of 1-3 SOL, with at least 2 known wallets in the coin", 1 SOL bought, sold 5 s
later, 66.7 h of tape, the newest 40 % held out (800 trades, 30 an hour). SOL per trade after fees and the bid; "lower"
is the 80 % lower bound.

| place behind the trigger | bid per transaction | per trade | lower | wins |
|---|---|---|---|---|
| the next trade (+0) | 0.02 | +0.063 | +0.055 | 53 % |
| one trade later (+1) | 0.02 | +0.018 | +0.010 | 44 % |
| two later (+2) | 0.02 | -0.008 | -0.016 | 38 % |
| three later (+3) | 0.02 | -0.015 | -0.022 | 36 % |
| three or more, across blocks (+3x) | 0.02 | -0.039 | -0.045 | 28 % |
| the next trade (+0) | 0.05 | +0.003 | -0.005 | 41 % |
| the next trade (+0) | 0.10 | -0.097 | -0.105 | 28 % |
| the next trade (+0), 2 SOL bought | 0.035 | +0.141 | +0.124 | 54 % |
| **one trade later (+1), 2 SOL bought** | 0.035 | +0.047 | +0.033 | 46 % |

The other rules fall the same way and sooner: the third known wallet is +0.052 at +0 and +0.005 at +1; any tracked
wallet's 1-3 SOL buy is +0.039 at +0 and -0.009 at +1; a labelled KOL's buy is +0.084 at +0 (lower +0.028, wins 25 %)
and +0.013 at +1.

Read: the whole result is the one place right behind the buy, and it is worth about 0.05 SOL per transaction on a
1 SOL trade. 6Z91's median bid is 0.033 SOL a buy (a quarter of its buys above 0.07). The bots already pay most of
what the place is worth; a newcomer who outbids them hands the edge to the validators.

At 2 SOL the same place is worth twice as much (+0.141 SOL a trade at a 0.035 bid, the third known wallet +0.118, a
KOL's buy +0.192 with a lower bound of +0.076): the edge grows with the size and the bid does not, so at 2 SOL a bid of
about 0.10 a transaction still breaks even. What is not measured yet is the bid that wins the place: the fee paid by
the trade that did land right behind these buys, event by event (`tx_meta`).

Teams counted once (lab `teams`, 14:18 UTC), 1 SOL, 0.02 a transaction, best exit of each rule, held out:

| rule (second known team in, 1-3 SOL) | trades | per trade at +0 | lower | per trade at +1 | lower |
|---|---|---|---|---|---|
| by address (from the table above) | 800 | +0.063 | +0.055 | +0.018 | +0.010 |
| tracked list, teams once | 704 | +0.075 | +0.067 | +0.026 | +0.018 |
| the same, 10 s after the previous team | 493 | +0.077 | +0.068 | +0.013 | -0.000 |
| traders only (no faucets, snipers), teams once | 527 | +0.068 | +0.057 | +0.011 | +0.001 |
| traders only, 10 s after the previous team | 385 | +0.082 | +0.071 | +0.029 | +0.019 |
| a KOL's first buy of 0.5 SOL | 159 | +0.087 | +0.034 | +0.016 | -0.026 |

Counting a team once adds about 0.01 SOL a trade and keeps a positive lower bound one trade behind; the gap and the
trader group do not add to it reliably (each variant picked a different exit). Size adds more than any of them.

### Next (written 14:10 UTC)

The 2 SOL run one trade behind (809 held-out trades, 30 an hour; the third known wallet +0.042, lower +0.028) is the
first rule here with a positive lower bound at a place other than the one right behind. Before it is frozen:

1. `paper freeze --trigger` scores a KOL rule at the live copy landing, not at a named place: give `paper freeze` the
   lab's `--copy-position` (tests first) so the rule is frozen at +1, 2 SOL, 0.035 a transaction.
2. Read how the automatic live test picks from frozen strategies (`live_test.eligible`) first: a frozen 2 SOL rule must
   not reach it. Sizes above 0.2 SOL are the owner's yes, in chat, for that step.
3. Measure what a 0.035 SOL bid buys behind these buys (the fee of the trades that landed first and second behind
   them, `tx_meta`). Our 0.003 bid lands 2-4 trades behind a crowded leader; +2 loses.
4. Rerun the rule to freeze as "tracked list, teams once" at 2 SOL, +1 and +0, 0.035 a transaction.

## The owner's tracker is the tracked list, and a third of it is teams (13:35 UTC)

The owner pasted the Axiom wallet tracker (2,630 wallets, `docs/inbox/2026-11-10-axiom-tracked-wallets.json`; "a lot
of them I js track for fun or are friends"). 2,613 of them were already the server's 2,663 "tracked" wallets (loaded
5 Nov), so every "known wallet" number above is this list. Read by name (`analysis/names.py`):

- 1,626 different names: 1,357 wallets share a name with another, 682 sit in teams of five or more ("Faucet" 53,
  "Aloh" 45, "Doji bundle" 37, "AtlasDrill" 26, "COMMUNITY ..." 17, "Crime" 16, "INSIDER" 16, "ORANGE TT" 15).
- kinds by name: 1,681 traders, 223 snipers and bots, 219 devs, 159 insiders and cabals, 127 one coin's top traders
  or holders, 71 faucets and exchanges, 62 bundles, 53 whales, 24 named KOLs, and 10 of the owner's own wallets.
- The bot's own wallet (`6VFr...`, "Me") was in the tracked list: the labs counted our own buys as a known wallet's.
  The ten own wallets are now kind `own` (no leader group). Five traders missing from the server were added.

So "the second known wallet is in" is often the second wallet of one team's bundle, and the paper copy at +0 then sits
between two wallets of a bundle, a place no bid buys. `copy-context --by-entity` counts a team as one known wallet,
makes its first buy the one event and enters behind the team's back-to-back buys in the block; two new splits show
the slots since the previous known wallet and the kind of name.

### The check (13:49 UTC): the second known wallet holds when a team counts once

`copy-context --behind-leader`, 72 h, 0.5 SOL and more, percent right behind the buy before 2.5 % of fees:

| | n | 2 slots | 13 slots (5 s) | 150 slots (60 s) | above fees at 13 |
|---|---|---|---|---|---|
| by address: first known wallet in | 9,673 | +3.7 | +0.5 | -8.9 | 40 % |
| by address: second | 2,362 | +9.0 | +8.5 | +4.8 | 56 % |
| **by team: second** (behind the team's bundle) | 2,054 | +10.1 | +10.3 | +7.2 | 59 % |
| by team: third | 1,207 | +10.7 | +11.8 | +8.9 | 65 % |
| by team: fourth or later | 2,007 | +10.5 | +11.3 | +9.1 | 67 % |
| by team: in the previous known team's block | 439 | +18.3 | +18.8 | +17.6 | 76 % |
| by team: 1-2 slots after it | 217 | +5.7 | +6.1 | -1.1 | 52 % |
| by team: 3-25 slots after it | 1,172 | +7.4 | +7.5 | +2.0 | 54 % |
| **by team: 26 slots (10 s) or more after it** | 3,440 | +10.7 | +11.6 | +9.9 | 66 % |

The guess that the result was a bundle artefact was wrong: with a team counted once and the copy behind its bundle the
second known wallet is worth more, not less (17,268 events become 14,940). The 439 "same block" events are the part
that still looks like one team under two names (the whole gain is there after 2 slots). The rest of the gain is also
there after 2 slots (0.8 s) and then holds for a minute: buyers who arrive right behind the known wallet. That fits
the lab: the place right behind takes it, one trade later most of it is gone.

By the kind of name (by address, at 13 slots): the 18 labelled KOLs +15.0 % (n 266, 76 % above fees, +15.6 % at 60 s);
bundle +14.4 % (181), dev +11.5 % (143), insider +10.5 % (112); plain traders +5.0 % (11,657); sniper names +0.5 %
(376); faucets and exchanges +1.7 % and 27 % of all events (4,741: the "Faucet" wallets are high-frequency bots).

Built from it (lab, 43aed3f): a KOL rule's `teams` (a team counts once, fires behind its back-to-back buys), `gap`
(seconds since the previous known wallet's first buy) and the label group `trader` (the tracked list without faucets,
exchanges and snipers). Running: second known team in, 1-3 SOL, with and without a 10 s gap, on `wallet` and on
`trader`, at +1 and at +0, 1 SOL, 0.02 a transaction.

## A fourth bot, `EjywYB...` (owner: "buys from KOLs' dev wallets after the dev sell ... not very profitable")

`playbook`, 72 h: 586 coins, 0.22 SOL a buy, one sell of everything a median 5 slots (2 s) later, 79 % out within 6 s.
53 % of its buys are in the same block as someone's buy, one trade behind it (net +2.2 SOL over 309 coins before
bids). It sat behind 142 wallets; 30 % of its copies are behind one, `4EZyPz...` (131 of that wallet's 456 coins).
Over the whole tape (5.7 days): 1,351 buys, 338.6 SOL in, 356.3 out, +17.7 SOL before about 0.002-0.004 SOL a
transaction. The same play as the other three at a tenth of the size, near break-even: the owner's read is right.

## The dev wallet `Bjr89z...` (owner: Jack Duval's dev wallet)

`deployer`, the whole tape: 26 launches in three mornings (04:30-07:00 UTC, a minute apart), signed for 17 different
creator wallets; 20 dead, 4 rinsed, 1 runner ("Coin", 44 SOL), 1 graduated (RM, creator `5JE1ij...`; 6 SOL in the curve
a block after the create, 25 at 5 s, 66 at 5 min; 6Z91 bought 1.78 SOL in its create block). A 0.5 SOL snipe a block
after every launch: +0.025 SOL a launch, 15 % wins, the newest ten lost, most of the profit one coin: MARGINAL. Both
wallets are labelled `dev`. Two paying launches cannot tell RM from the nine "FAKE" coins at the create; OKTech's
Web kind (follow the wallets a dev funds) is the feature for a dev who launches from fresh wallets.

## A launch tool's mark (owner, 13:55: "a tool called proxima ... launches are usually organised and go high")

RM's create transaction (`tx`): a direct pump.fun CreateV2 and buy, no wrapper program, signed by `Bjr89z...` and the
mint, with two transfers at the end: 0.0085 SOL to a Nozomi tip account and 0.0025 SOL to `moonKfft...`, a fee wallet.
A tool that launches this way is not a "wrapped" launch for OKTech's Launch shape; its mark is the fee wallet in the
create transaction (OKTech's required-accounts filter) or the wallet that pays (Launch shape's platform payer). In
72 h 22,051 of 113,590 launches were signed by a wallet other than the creator; the busiest signers launch from a
fresh creator each time (`bwamJz...` "Bdubs" 724, "Theo dev" 161). Whether `moonKfft...` is Proxima is not known: one
coin the owner knows was a Proxima deploy gives its mark, then every launch with it can be counted and scored.

### Next for launch tools (owner, 14:30: "is there a way for it to automatically detect proxima?")

Told yes. Build `launch-tools --hours N` (a batch job, tests first; add to ops ALLOWED and the research commands): for
every launch in the window fetch its create transaction (the `tx` path; `doctor` measured about 98 a second), take its
marks (top-level programs other than pump.fun, system, token, associated-token and compute-budget; the destinations of
SOL transfers that are not known tip accounts; signer other than the creator), group launches by mark, and give per
mark: launches, share that reached 30 SOL and that graduated, SOL in the curve at 5 s and 60 s after a block-late
entry. The marks whose launches run are the tools worth targeting, Proxima among them whatever its address; a coin the
owner names only confirms which mark is which. Live, a mark is an OKTech required-account filter on the create (a fee
wallet) or a Launch shape wrapper program. The 2 SOL team-counted run one trade behind (14:23): +0.064 SOL a trade,
lower +0.049, 704 trades.

## Proxima's mark, found from six coins the owner named (14:40-15:30 UTC)

The owner named six coins as Proxima deploys (CUBCAT, CALLINU, FUENTES, NUT, Mwezi, GiftTok). `launch-tools --mint`
read each one's create transaction (two from the tape, four from the first transaction that named the mint). They pay
no tool fee, use no wrapper program and five of six are signed by their own creator. **All six load the same address
lookup table, `Hyif6eWb8x88RVrvjPfabsgRYnwkVnyByEXTVTXbUcyP`**, and nothing else is common to them. The scanner now
returns it as the mark `alt:<table>`. CALLINU and CUBCAT were each followed, in the create's own block, by four
wallets buying 2.2-2.9 SOL in four separate transactions (the team's bundle).

The mark is broad. Over the newest 30,000 launches (`launch-tools --hours 24 --mark alt:Hyif6e...`, entry at the end of
the block after the create, where a launch snipe lands, returns before about 2.5 % of fees):

| launches | n | reached 30 SOL | graduated | median peak | +5 s | +60 s | up after fees at 60 s |
|---|---|---|---|---|---|---|---|
| all | 30,000 | 14.1 % | 5.0 % | 1.0 SOL | +0.1 % | −1.8 % | 24 % |
| the mark | 3,401 (1,562 creators) | 19.0 % | 6.9 % | 4.4 | +0.4 % | +0.2 % | 22 % |
| the mark, nobody else in the create block | 2,595 | 18.3 % | 7.8 % | 2.2 | +0.6 % | −0.4 % | 20 % |
| others put under 2 SOL in the create block | 420 | 9.3 % | 2.6 % | 3.0 | −0.3 % | +2.9 % | 22 % |
| 2-5 SOL | 119 | 6.7 % | 0 % | 7.1 | −0.3 % | −1.8 % | 24 % |
| 5-10 SOL | 60 | 18.3 % | 3.3 % | 13.6 | +1.4 % | +4.5 % | 33 % |
| **10 SOL or more ("organised")** | **207 (202 creators)** | **53.6 %** | 9.7 % | 31.1 | −0.2 % | **+1.4 %** (median −1.4 %) | 46 % |

- The owner is right that organised Proxima launches go high: more than half reach 30 SOL against one launch in seven.
- A snipe one block later does not collect it. The team's 10-20 SOL is already in the price it pays, and the mean
  at one minute is +1.4 % before 2.5 % of fees.
- The mean hides two outcomes. Of the 41 organised launches in the newest 1.3 hours, 23 were at −63 to −65 % a
  minute later and 16 were at +30 to +113 %. Each loss is one block: the team's wallets sell everything at once,
  5 to 40 seconds after the launch. Most of these 41 are one operation (6 or 10 wallets put 19-20 SOL in the create
  block, the creator buys 0.99 SOL, a fresh creator wallet each time) relaunching the same names minutes apart
  (fomocat, ATTENTION, SEYONG, Inuployed, tagcat, MOMO). Of 9 groups of same-name launches, every earlier attempt
  was dumped (16 of 16) and the last one ran in 7 of 9.
- The ones still up after a minute went on to +100 to +200 % at two to five minutes in 9 of 17 cases, and 5 of the 17
  were dumped to −60 % by then. Bought at one minute and sold a minute later, those 17 average about +4 % with two
  losses of −70 to −80 %: a lead, on 17 trades from 1.3 hours, not a result.
- The owner's six coins are a week old and only two are on the tape; whether the organised launches the owner means
  are this crew's or a slower, hand-run kind is not known from the chain.

OKTech can target the mark without us: Hunt mode takes `knownBotLuts[]` with `minLutMatches`, and the account filter
takes keys a launch must name (whether that covers a lookup table's address is a question for OKTech). Nothing is
created: no rule here pays yet.

The owner's page has the section "Proxima launches" (the table above, each launch as a dot, the newest organised
launches). `tools/dashboard_snapshot.py` fills it from the newest finished `launch-tools ... --mark ... --json` job.

### The table is pump.fun's standard path; the tell is how the wallets are funded (owner, 17:30: "around 500 launches a day with proxima ... there's something else to it")

The mark over-counts: 3,309 launches in 19.5 h carry the table (about 4,070 a day). Sixty of their creates, fetched
raw, have the same shape as the six Proxima coins whatever else happens around them (compute budget, CreateV2 with
16 accounts, a token account, BuyV2 with 27 accounts, two signers, the one table): it is pump.fun's own create path,
which Proxima uses like everyone else. The token metadata says `createdOn: https://pump.fun` too. The create
transaction alone cannot say Proxima.

What the two coins on the tape show around the create:

- **The team's buys are one bundle with the create.** CALLINU's and CUBCAT's four buys right behind the create carry
  the create's own recent blockhash, a compute limit and price that look random (251593 / 429509, 265266 / 689109,
  ...), a plain `buy` with 18 accounts, and the last of the four has no compute budget and pays 0.003 SOL to a Jito
  tip account. Create plus four buys is the five-transaction limit of a Jito bundle. (The 6.9 SOL buy behind them in
  CALLINU's block is a sniper: another blockhash, another shape, a 0.011 SOL fee.)
- **Every wallet was funded minutes before, each from its own one-off wallet, by the same odd transaction.** The
  first transaction of all 10 wallets (2 creators, 8 team wallets) is `setComputeUnitLimit(1450)` plus one SOL
  transfer from a wallet used for nothing else, 2 to 4 minutes before the launch (CALLINU: 00:35:38, 00:36:01,
  00:36:18, 00:36:23, launch 00:39:33). The creators of the other Proxima coins are the same: 4 of the 4 that were
  ever funded, each with 0.174-0.178 SOL (Mwezi's creator was never funded: its payer wallet was).
- **Other launches do not have it.** The first transaction of 40 creators of other launches with the table (solo dev
  buys, launches with one bundled buyer, with several, the same-name crew's): 1 has the same shape (limit 1450 and a
  transfer, 0.02 SOL), 1 has the limit with a price as well, 38 have neither. The crew's creators are funded with
  20-28 SOL in a transfer with an estimated limit: the crew is not Proxima.

So the working definition of a Proxima launch: pump.fun's standard create, whose creator (and bundle wallets) were
first funded by a transfer with a compute limit of exactly 1450 from a one-off wallet minutes earlier. Not counted
yet: how many launches a day fit it (the owner says about 500). If the count agrees, the funding is visible two to
four minutes BEFORE the coin exists, which the create is not.

By the size of the bundle right behind the create (plain buys by other wallets at the next positions): none 3,713 a
day, one wallet 238, two 76, three 15, four 21, more 10.

### What OKTech's developer said (owner, 17:40)

"sniper hunt is my fav tho cause you can trail bundlers, especially bwam". `bwamJz...` signs launches for fresh
creator wallets: 121 in the last 19.5 h, each for a different creator. A buy one block after its creates: +4.3 % at
5 s (median 0, 43 % up after fees), −12.5 % at one minute, 2.5 % reach 30 SOL. A blind snipe behind bwam is a
five-second flip worth about its fees; Hunt mode adds thresholds (SOL and buyers inside a window) that the blind
number does not test. "The AIL snipe is in S mode too": what AIL stands for is not known here.

### The count (17:55 UTC) and bwam, read from the tape (18:00)

`launch-tools --hours 24 --max 30000 --funding-of alt:Hyif6e... --mark fund:cu1450` read the first transaction of
the creator of each of the 3,262 launches with the table. **79 were first funded by the relay transfer** (78
creators; about 97 a day), against the owner's "around 500 a day". The gap is not explained: the tell only sees a
creator whose FIRST transaction is the relay's, so a dev launching from a wallet they already had is missed even
when their bundle wallets were relay-funded. Reading the create-block buyers' funding as well is the next count.

| relay-funded creators | n | reached 30 SOL | +5 s | +60 s | up after fees at 60 s |
|---|---|---|---|---|---|
| all | 79 | 15.2 % | +1.4 % | −7.6 % | 20 % |
| nobody else in the create block | 20 | 0 % | +3.0 % | −5.1 % | 18 % |
| others put under 10 SOL | 40 | 2.5 % | −1.5 % | −13 % | 5 % |
| others put 10 SOL or more | 19 | 57.9 % | +6.2 % | +2.4 % (median +10.7 %) | 53 % |

Nineteen launches are too few to act on; the pattern matches the owner's "organised and go high" (58 % reach 30 SOL).

**bwam** (`bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`), the last 24 h on the tape: it traded 218 coins, every one a
launch it signed itself for a fresh creator wallet. It buys once, in the create's own block, into an empty curve
(median 5.9 SOL, 12 SOL at the 90th percentile), and its last sell is a median 6 s later. 95 % of the coins paid it,
+2.8 SOL a coin, +610 SOL in the day (+1,418 SOL over 72 h on 608 coins). It never bought a second time on any of the
217 coins: on the tape its own wallet does not "buy up the chart"; if bwam does that on some deploys it uses other
wallets, which are not identified yet. The other buyers in its create blocks are small snipers (0.1-0.5 SOL:
`7PtVk5...` on 104 of its launches in 72 h, `AmN88f...` 62, `2NUMEz...` 40, `6anjqM...` 32), present on 304 of 977
launches. A buy one block after a bwam create is what bwam sells into.

**The bundle wallets counted too (18:15).** The same scan now reads the first transaction of each launch's
create-block buyers (`team:cu1450`). 59 launches have a relay-funded bundle wallet, 79 a relay-funded creator, **104
either: about 130 a day**, still well under the owner's 500. The value 1450 is the only one that repeats (79 and 59;
the next is 5345 on 4 launches, then scattered values on 2-3). A buy one block later on the 59: +2.3 % at 5 s, −11 %
at a minute, 12 % reach 30 SOL; on the 12 of them with 10+ SOL from others, 50 % reach 30 SOL and −19 % at a minute.
On this definition a Proxima launch is no better to snipe a block late than any other: the bundle's SOL is in the
price and the bundle sells into the buyers. What is left to try is the entry after the first minute and whether the
owner's list of Proxima launches differs from this one.

OKTech's developer on Hunt: "if 5 bloom snipers buy a dev I wanna buy as well ... if mintech snipers go, if bwam
bundle wallets go, if blockx go, I go": Hunt buys a launch once named bots (by wallet, program, lookup table or
account) have bought it inside a window. Our crowd hunts counted any wallets and lost; which wallets is untested:
idea I59 (the top first-slot snipers by profit as the group, known 2/3/5, coins under 10 s old, +1 landing).

### Organised launches with a website (owner, 18:15: "maybe sniping them when it is linked to a certain type of thing, for example tech projects, has a website")

`launch-tools --mark alt:Hyif6e... --block-sol-min 5` (pump.fun's standard path, others put 5+ SOL in the create
block; 279 launches in 19.5 h), split by what the coin's metadata links to. A buy one block later, before fees:

| | n | reached 30 SOL | +5 s | +60 s | median +60 s | up after fees at 60 s |
|---|---|---|---|---|---|---|
| has a website | 138 | 51 % | +2.2 % | **+14.3 %** | +1.9 % | 50 % |
| no website | 132 | 46 % | +2.1 % | **−6.8 %** | −18.9 % | 37 % |
| X link is a tweet | 201 | 50 % | +3.4 % | +3.5 % | −3.3 % | 46 % |
| X link is a profile | 56 | 52 % | −0.8 % | +10.6 % | 0 % | 41 % |
| no X link | 12 | 8 % | −4.3 % | −16.8 % | −25.4 % | 25 % |
| has a Telegram | 7 | 71 % | −4.1 % | +16.6 % | +1.0 % | 43 % |

The website splits the organised launches by 21 points at one minute. It is one day, read after the fact, one of
three splits looked at, before 2.5 % of fees and before the price our own buy moves; the mean is carried by the
winners (median +1.9 %). It is the first split of launch snipes on this tape with a clearly positive mean at a
landing we have. Running: the same scan over the whole tape (`--hours 140 --max 250000`, job started 18:25) to see
whether it holds on the other five days. Then the lab with size and fees, then forward paper. OKTech can express it
without us: its metadata filter takes "website present", and Hunt's thresholds take the SOL and buyers in the first
seconds.

### Which bundled launches pay: fourteen features on the same 279 (owner, 18:40: "are there any other distinct features ... or anything with these bundled coins that make them profitable")

The 279 launches of the last 19.5 h where others put 5+ SOL in the create block, a buy one block later, at one
minute, before fees. Exploratory: one day, fourteen splits read after the fact.

| feature | value | n | mean | median | up after fees | reach 30 SOL |
|---|---|---|---|---|---|---|
| all | | 279 | +3.4 % | −2.9 % | 43 % | 48 % |
| website | none | 141 | −7.2 % | −14.1 % | 36 % | 44 % |
| | its own site | 100 | +12.8 % | +0.4 % | 47 % | 56 % |
| | a social or other page as the website | 38 | +18.0 % | +8.8 % | 58 % | 39 % |
| X link | none | 21 | −15.5 % | −12.5 % | 24 % | 14 % |
| description | none | 191 | +7.1 % | +0.2 % | 48 % | 53 % |
| | short | 61 | −6.6 % | −7.7 % | 31 % | 30 % |
| SOL others put in | 5-15 | 101 | −1.2 % | −6 % | 33 % | 18 % |
| | 15-25 | 164 | +5.7 % | +13.9 % | 51 % | 62 % |
| wallets in the block | 4 | 31 | −12.4 % | −9.0 % | 29 % | 35 % |
| | 5-6 | 77 | +12.7 % | 0 % | 47 % | 58 % |
| | 11+ | 29 | +11.0 % | +30.2 % | 59 % | 69 % |
| relay-funded (Proxima's pattern) | yes | 48 | −5.1 % | −0.5 % | 35 % | 25 % |
| | no | 231 | +5.2 % | −3.3 % | 45 % | 52 % |
| the same name launched again within 30 min (hindsight) | yes | 97 | −13.7 % | −63.5 % | 35 % | 49 % |

Nothing in the dev's buy size, the tweet's age, the hour or a signer other than the creator separates them. Combined,
using only what is known at the launch:

| rule | n | mean | without its 3 best | median | up after fees | reach 30 SOL |
|---|---|---|---|---|---|---|
| **website and 15+ SOL from others** | **77** | **+22.2 %** | **+16.9 %** | **+29.1 %** | 60 % | 75 % |
| the same, not a repeat of a name from the last 30 min | 43 | +25.9 % | +17.2 % | +23.6 % | 63 % | 74 % |
| no website and 15+ SOL | 101 | −6.3 % | −9.7 % | −14.1 % | 41 % | 56 % |
| website, relay-funded | 27 | +3.3 % | −3.4 % | +0.6 % | 48 % | 15 % |
| website, first half of the window / second half | 69 / 69 | +9.9 % / +18.6 % | +1.3 % / +13.9 % | | | |

"A bundled launch with a website" is about 95 launches a day at +22 % a minute later before 2.5 % of fees and our
own price impact, and it keeps most of that without its three best coins. It is still one day. The whole-tape scan
(job 20261110-182301-dbfaa6) gives the website split on six days at 5+ SOL; rerun it with `--block-sol-min 15` when
it ends (marks and metadata are kept, so the rerun only recomputes outcomes).

Not available: whether the coin's X account is verified (gold or blue), its followers or age: the metadata holds only
the link, and no X data source is connected. Other launchpads: the tape records pump.fun, PumpSwap and Raydium
LaunchLab (127 LaunchLab coins in the last hour against 2,457 on pump.fun's curve); Meteora DBC (Believe, Bags) is not
recorded, though OKTech snipes it. `launch-tools` scores pump.fun curves only: LaunchLab is the next venue for it.

### The bought month on ordered rows: what survived (23:10 UTC)

The scan over 5 Oct 08:00 - 9 Nov 2026 (856 hourly files, 1,244,675 launches), every file put in chain order first
(`history_scan.Orderer`; the token-reserve chain breaks on 0.3 % of followed trades; `path_check`: 4 of 20,387
launches differ between the table's minute and the same hold walked on the kept path, against 2,140 of 16,202
before). 20,387 bundled launches (others put 15+ SOL in the create block and the curve could still be bought; 4,286
more were bought out by their own bundle). A buy at the end of the block after the create, percent before 2.5 % of
round-trip fees. **These replace every number in the three sections under the correction note below.**

| split | launches | 5 s | 60 s | median 60 s | up after fees |
|---|---|---|---|---|---|
| every bundled launch | 20,387 | -1.1 | -6.5 | | |
| website yes / no | 12,256 / 5,278 | -0.1 / -2.0 | -4.5 / -9.8 | -9.0 / -26.7 | 37 % / 33 % |
| Telegram yes / no | 548 / 16,986 | +1.4 / -0.8 | +2.3 / -6.4 | 0.0 / -14.4 | 41 % / 35 % |
| dev bought 0 | 7,245 | -2.1 | -10.0 | -35.2 | 30 % |
| dev bought 0.5-1 SOL | 4,950 | +1.5 | -3.6 | -9.4 | 40 % |
| dev bought 1-2 SOL | 1,904 | -1.0 | -2.5 | -7.2 | 41 % |
| dev bought 2+ SOL | 1,946 | -3.4 | -12.7 | -20.2 | 27 % |
| others' SOL 15-25 / 25-50 / 50+ | 15,344 / 3,474 / 1,569 | | -8.0 / -2.2 / -2.3 | -25.9 / 0 / 0 | 33 / 35 / 41 % |

The website, the dev's own buy and the bundle's size each make a launch less bad; none makes it pay. The +5 % of
"dev bought 0.5-2 SOL" and the website's +1.3 % were made by the corrupted paths.

The record of a launch's create-block wallets on their earlier launches (split `team`, five or more earlier launches,
walked in time order):

| the team's earlier launches averaged | launches | bundle wallets | 60 s | median | up after fees | first / second half | one / three / eight blocks late |
|---|---|---|---|---|---|---|---|
| under -10 % | 4,590 | 12,783 | -22.2 | -51.1 | 23 % | -21.7 / -22.8 | -21.6 / -21.5 / -18.7 |
| -10 to 0 % | 6,947 | 18,620 | -5.0 | -11.7 | 36 % | -5.1 / -4.7 | -4.7 / -5.1 / -3.8 |
| 0 to +10 % | 1,380 | 5,717 | +1.9 | -2.0 | 45 % | +1.2 / +2.9 | +1.4 / +0.8 / -0.2 |
| +10 to +25 % | 344 | 1,464 | +15.0 | +8.4 | 54 % | +18.4 / +9.2 | +13.2 / +12.2 / +10.0 |
| over +25 % | 107 | 497 | +3.7 | -1.8 | 47 % | +10.6 / -8.2 | +4.5 / +2.1 / +1.5 |
| no record | 7,019 | 42,358 | -0.8 | 0.0 | 36 % | -1.6 / 0.0 | -1.4 / -2.0 / -2.7 |

On the days never used to find anything (4-9 Nov, `--show-from 2026-11-04`, 3,696 launches): under -10 %: -23.5 %
(953); -10 to 0: -3.0 % (918); 0 to +10: +6.5 % (228); +10 to +25: -1.7 % (35); over +25: -7.0 % (21); no record
-1.1 % (1,541). From 23 Oct on (9,605 launches): +10 to +25: +9.2 % (128; halves +14.4 / -1.9); over +25: -8.2 % (39).

Read:

1. **A team with a bad record keeps losing**: -22 to -24 % a minute later in every period, the unseen days included,
   4,590 launches. It is the one result here that is fully confirmed, and it is a filter: no launch rule should buy
   those, and they are a quarter of all bundled launches.
2. **A team with a good record is not confirmed.** +15 % over the 36 days with a positive median and little lost when
   the buy lands late (eight blocks late still +10 %), but +9 % from 23 Oct on, and -2 % on the 35 launches of the
   unseen days. The top row ("over +25 %") does worse than the row under it and turns negative late in the period:
   a mean of a few earlier launches is carried by one lucky runner and falls back. 451 launches in 36 days is 12 a
   day, 56 on the unseen days: too few to call.
3. **The rule OKTech can run natively does not work** (split `hunt`: any create-block wallet on a list rebuilt once a
   day, 1,167 wallets): -1.6 % against -7.1 % for the rest. The pooled, minute-fresh team record carries the signal;
   one listed wallet among strangers does not.
4. **Hold times**: for the +10 to +25 % row the mean climbs to a minute (+4 at 5 s, +9 at 20 s, +12 at 30 s, +15 at
   60 s) and the median peaks at 40-60 s (+8 %); at four minutes the median is -57 %. For bad teams the fall is over in
   30 seconds.

Next: a steadier team score (earlier returns clipped, and pulled toward the average when there are few), judged week
by week; the copy check re-read on ordered rows (running); and more months only if a score holds.

> **Correction, 22:10 UTC, for the three sections that follow** (the bought month; copying on it; the team record).
> Their numbers were read with a wrong assumption of mine: that every hourly file holds a coin's trades in block
> order. Some October files do not. `path_check` (the table's one-minute return against the same hold walked along the
> kept path) disagreed on 2,140 of 16,202 launches, with paths that jumped tenfold and back; the means in these
> sections are inflated by such launches (one read +834 % where the path says +17 %), the medians less. Since ea30e46
> every file is put in chain order first (`history_scan.Orderer`: by block, then by the token reserves inside a
> block; across blocks it agrees with our tape on 100 % of neighbouring trades). The scan over 5 Oct - 9 Nov and the
> follow run were read again on ordered rows: the section above holds the scan's ordered tables. The three sections
> below are kept as the record of what was said at the time; their numbers are superseded.

### The same question on a bought month: the website helps, the dev's own buy helps more (20:40 UTC)

The owner bought pumpfundata.com's history (10 Nov, 3,000 credits). Every pump.fun trade from 5 Oct 08:00 to 3 Nov 2026
(712 hourly files, 125.9 million rows, 1,056,694 launches priced in SOL) is on the server. Checked against our own tape:
the hour 9 Nov 12:00 holds 99.9 % of our SOL trades; the file's row order agrees with the tape's blocks on 99.9 % of
neighbouring trades of a coin and with the tape's place inside a block on 88 % (`history order`). So prices at block
ends can be trusted and "one trade behind" inside a block cannot. (The scan's `order_breaks` of 0.34 is mostly a third
reading of the amounts that fits neither "buy adds the amount" nor "sell takes amount plus fee": 26 % of trades do not
chain even in the tape's own order. The reserves after each trade are the chain's own numbers and are what is used.)

`history scan --from 2026-10-05 --to 2026-11-03 --min-block-sol 15`: 16,202 launches where others put 15+ SOL into the
create block and the curve could still be bought a block later (3,136 more were bought out by their own bundle and are
left out). A buy at the end of the block after the create, in percent, before 2.5 % of round-trip fees:

| split | launches | reach 30 SOL | graduate | 5 s | 60 s | median 60 s | up after fees |
|---|---|---|---|---|---|---|---|
| website yes | 9,926 | 65 % | 17 % | +4.3 | +1.3 | -4.5 | 40 % |
| website no | 3,620 | 57 % | 13 % | +1.6 | -5.6 | -21.0 | 34 % |
| metadata unreadable | 2,656 | 54 % | 7 % | +2.8 | -4.0 | 0.0 | 27 % |
| X link: a profile | 4,669 | 71 % | 22 % | +5.0 | +2.1 | 0.0 | 42 % |
| X link: a tweet | 7,875 | 57 % | 12 % | +3.3 | -1.5 | -19.1 | 37 % |
| no X link | 762 | 70 % | 19 % | -1.8 | -8.0 | -6.5 | 30 % |
| Telegram yes | 446 | 75 % | 35 % | +5.8 | +5.7 | 0.0 | 45 % |
| Telegram no | 13,100 | 63 % | 15 % | +3.5 | -0.8 | -8.8 | 38 % |
| others' SOL 15-25 | 11,827 | 49 % | 9 % | +3.0 | -2.7 | -19.8 | 35 % |
| others' SOL 25-50 | 2,910 | 94 % | 17 % | +4.6 | +3.5 | 0.0 | 38 % |
| others' SOL 50+ | 1,465 | 100 % | 54 % | +5.0 | +2.5 | +1.2 | 46 % |
| dev bought 0 | 5,858 | 50 % | 11 % | +1.6 | -6.0 | -27.0 | 33 % |
| dev bought under 0.5 SOL | 3,747 | 69 % | 20 % | +3.6 | +0.6 | 0.0 | 37 % |
| dev bought 0.5-1 SOL | 3,420 | 68 % | 14 % | +7.7 | +5.0 | -3.5 | 42 % |
| dev bought 1-2 SOL | 1,494 | 66 % | 14 % | +3.6 | +6.2 | -3.1 | 44 % |
| dev bought 2+ SOL | 1,683 | 68 % | 14 % | +1.2 | -6.8 | -14.0 | 29 % |

Read:

1. **The one day was a lucky day.** "Website and 15+ SOL" was +22 % a minute later on 9-10 Nov; over 30 days it is
   +1.3 %, which fees turn into a loss. The website side is ahead of the no-website side on 23 of 30 days, so it is a
   real filter, and it clears the fees on 9 of 30 days, so it is not a rule.
2. **The dev's own buy sorts them better.** A dev who buys 0.5-2 SOL of the coin in the create (4,914 launches, about
   160 a day): +5 to +6 % a minute later. No dev buy: -6 %. A dev buy over 2 SOL: -7 %.
3. **Every row's median is zero or negative.** Most of these launches lose a little and a few run far: the exit rule
   decides the result, not the entry filter.

Crossed (`--cross`), each cell with its 60 s return on the first and on the second 15 days (a cell among dozens that
pays in one half only is luck):

| cell | launches | 60 s | first half | second half |
|---|---|---|---|---|
| no website, no Telegram, dev 1-2 SOL | 399 | +7.3 | +6.4 | +7.9 |
| website, no Telegram, dev 1-2 SOL | 856 | +4.5 | +3.3 | +6.7 |
| website, no Telegram, dev 0.5-1 SOL | 2,201 | +4.8 | +6.1 | +1.4 |
| website, dev 0, others' SOL 25-50 | 560 | +7.3 (median +9.5, 62 % up) | +11.0 | +3.2 |
| X tweet, dev 1-2 SOL, 4+ wallets in the block | 773 | +6.3 | +5.3 | +7.1 |
| X tweet, dev 0.5-1 SOL, 4+ wallets | 1,315 | +5.9 | +5.1 | +6.6 |
| website, no Telegram, dev 2+ SOL | 1,064 | -5.1 | -3.8 | -7.6 |

"Dev bought 1-2 SOL" holds in both halves in every cell it appears in, with or without a website. After fees that is
+2 to +5 % a trade before the landing fee, on a mean carried by few winners. It is the first launch rule here that
held on two separate halves of a month; it is not a strategy until the exit is chosen on one half and holds on the
other (`history scan --exits`: each launch's block-end prices for ten minutes are kept and time, stop, take and trail
rules are walked along them; running).

### Copying, asked of the same month: the place pays, the wallet barely (21:10 UTC)

`history follow --from 2026-10-05 --to 2026-11-03 --min-sol 0.5`: 6,108,611 first buys of 0.5 SOL or more by 675,118
wallets in coins created inside the files, each scored as a copy. Percent a trade after 2.5 % of round-trip fees,
before the bid, held a minute:

| who is copied | trades | right behind the buy | one trade behind | end of the buy's block |
|---|---|---|---|---|
| every wallet, last week | 1,185,630 | +3.3 | -0.8 | -4.5 |
| picked on weeks 1-4 (40+ buys, positive by two standard errors at the block's end, not a create-block buyer), judged on week 5 | 11,629 by 204 wallets | +13.3 | +7.0 | +1.4 |
| the same, 100+ buys | 10,458 by 115 wallets | +13.8 | +7.4 | +1.6 |
| picked on weeks 1-3, judged on weeks 4-5 | 30,258 by 141 wallets | +11.9 | +6.0 | +0.6 |

1. **The first run picked on "one trade behind" and was wrong to.** Its best wallets were teams that buy their own
   launch with six wallets in one bundle; one trade behind the first of them is inside the bundle. The pick is now made
   at the end of the copied buy's block, the place a copy can count on (the files' order inside a block is right for
   88 % of neighbours, across blocks for 99.9 %).
2. **At that place, picking wallets is worth six points over copying everyone and ends near +1.5 %**, which the bid
   and a late landing eat. That is what the three live copy snipers show (-0.29 SOL over 24 round trips).
3. **Right behind the buy the same picks are +13 %.** The copy edge is the place inside the block. It is bought with
   the bid and fought over by bots that pay 0.03 SOL and more a buy.
4. **Walked forward** (`--walk`: for each week, wallets picked on the weeks before it, at rising bars on their mean):

| judged on | bar 0 | bar +3 % | bar +6 % | bar +10 % |
|---|---|---|---|---|
| week 2 | -0.3 (31,423) | +1.0 | +1.2 | +1.4 (2,023) |
| week 3 | +1.5 (16,766) | +2.3 | +3.1 | +5.3 (2,561) |
| week 4 | +0.6 (17,810) | +1.1 | +2.4 | +5.6 (1,946) |
| week 5 | +1.4 (11,629) | +1.8 | +3.3 (3,773) | +1.6 (1,627) |

   The return rises with the bar in every week: the selection is real, and thin. At a +6 % bar it is +1 to +3 % a
   trade on 4,000-7,000 trades a week.
5. **With create-block buyers left in, the same walk is +5.5 to +6.5 % at bar 0 and +13 to +15 % at bar +10 %**
   (weeks 3-5, 9,000-14,000 wallet-trades a week, 70-80 % of the wallets up). Those are bundle wallets, and "the end of
   their block" is the end of the create block, which no buyer outside the bundle gets. What it does say: **a bundle
   team's record carries over to its next launches.** `history scan` now keeps each launch's create-block wallets and
   `history table`'s split `team` gives every launch the record those wallets had before it, scored at the launch
   snipe's own landing (the end of the block after the create); running.

### A bundle team's record predicts its next launch (21:45 UTC)

`history scan --exits` keeps, for every bundled launch, who bought in the create block and the price at the end of
every block for ten minutes. `history table`'s split `team` gives a launch the mean a-minute-later return of the
earlier launches its bundle wallets were in (five or more earlier launches; walked in time order, a launch's own
outcome counts only once its minute has passed). The same 16,202 launches of 5 Oct - 3 Nov, bought at the end of the
block after the create, percent before 2.5 % of fees:

| the team's earlier launches averaged | launches | bundle wallets (commonest one's share) | 60 s | median | up after fees |
|---|---|---|---|---|---|
| under -10 % | 2,276 | 5,573 (20 %) | -24.7 | -51.6 | 21 % |
| -10 to 0 % | 4,888 | 12,828 (16 %) | -2.6 | -13.8 | 36 % |
| 0 to +10 % | 2,861 | 10,042 (14 %) | +3.1 | -4.1 | 43 % |
| +10 to +25 % | 578 | 2,266 (19 %) | +12.7 | +5.0 | 52 % |
| over +25 % | 216 | 677 (32 %) | +24.3 | +13.1 | 61 % |
| no record | 5,383 | 33,428 | +5.2 | 0.0 | 37 % |

It rises row by row, the two top rows have positive medians, and it is not one crew. With three or ten earlier
launches required instead of five the table has the same shape.

Exit rules on the kept paths (a stop or a take is seen at a block's close and filled at the next block's close; first
half / second half of the days):

| team record | exit | mean | median | up after fees | first half | second half |
|---|---|---|---|---|---|---|
| +10 to +25 % (578) | hold 60 s | +10.1 | +4.3 | 52 % | +9.5 | +11.1 |
| | take +30 % or 60 s | +9.4 | +13.7 | 63 % | +10.1 | +8.1 |
| | stop -25 %, take +50 %, 60 s | +9.1 | +5.7 | 55 % | +7.5 | +11.8 |
| | one block late, 60 s | +7.4 | +2.4 | 50 % | +7.5 | +7.1 |
| | three blocks late, 60 s | +5.5 | +0.6 | 49 % | +6.5 | +3.9 |
| | hold 4 minutes | -6.0 | -55.3 | 33 % | -6.1 | -5.8 |
| over +25 % (216) | hold 60 s | +18.3 | +11.4 | 59 % | +25.9 | +3.0 |
| | stop -15 %, take +50 %, 60 s | +12.2 | +9.1 | 57 % | +15.1 | +6.5 |
| | one block late, 60 s | +14.3 | +7.2 | 54 % | +22.8 | -2.8 |
| no record (5,383) | take +30 % or 60 s | +6.8 | +1.5 | 49 % | +6.7 | +6.9 |
| | one block late, 60 s | -0.2 | 0.0 | 34 % | -0.7 | +0.3 |

Read:

1. **Teams with a record of +10 % or better: 794 launches in 30 days, about 26 a day, +10 to +18 % a minute later on
   time, both halves positive.** After fees that is +7 to +15 % a trade before the landing fee.
2. **Speed is half of it.** One block late keeps about +7 %, three blocks late +5.5 % (second half +3.9 %, which fees
   nearly eat), and the "over +25 %" row's second half goes negative when late. The buy has to land in the block after
   the create, which is where OKTech's launch snipes land; a trigger of our own that calls OKTech after reading the
   block is two to four blocks late.
3. **Sell within the minute.** Held four minutes every row loses. A take at +30 % raises the median and the share of
   winning trades; the mean is the same as a plain 60 s hold.
4. **The worst row is as useful as the best**: a team whose earlier launches averaged under -10 % loses 25 % a minute
   later (2,276 launches). Any launch rule should refuse those.
5. Not yet: days outside the month (4-9 Nov bought, 143 files; the scan over 5 Oct - 9 Nov with `--show-from
   2026-11-04` is running), the same on our own tape at an exact landing, and how the list of good team wallets is
   handed to OKTech (Hunt takes known wallets; how many is not documented).

### Next for Proxima

FIRST (18:25): read the whole-tape scan when it ends (the newest finished `launch-tools --hours 140` job), then run it
again with `--block-sol-min 15` and read the website rows: "website and 15+ SOL from others" on six days, by day; if it holds, a lab test with size and fees (launch trigger with others' create-block SOL >= 5-10 and a
website, 0.5-2 SOL, exits 30 s to 5 min and a stop), then forward paper; tell the owner the result either way.

0. Count the funding tell: for every launch with the table, the creator's first transaction (two RPC calls a
   launch); mark `fund:cu1450` when it is a transfer with a compute limit of 1450 and nothing else. Launches a day
   with it against the owner's 500; then score those launches alone (reach 30 SOL, a buy one block later, at 30-120 s).
1. Keep each launch's marks in the database (done 17:12: `launch_marks`, written by the scan); then the recorder writes
   them as each create arrives, so the lab can gate on a mark.
2. Lab over the whole tape: the mark with 10+ SOL from others in the create block, bought at 30, 60 and 120 s if the
   price is still above the create block's close, sold on a timer and on the first large sale by a create-block
   wallet; at 0.5-2 SOL with fees. Forward paper only if the held-out half is positive without its three best coins.
3. A split by the wallets in the create block (the crew's 6 or 10 at 19 SOL against other shapes), so the crew's
   launches are scored apart from hand-run ones.

## The two bots' rules from 50 factors, and the winners' verdicts (`study`, `qualify`; read 15:00 UTC)

`study <wallet> --days 3` compares what a bot bought with what it passed on at the same moments (129 h of tape).

| | 6Z91 | Fhbh1 |
|---|---|---|
| buys scored | 2,794 | 3,626 |
| buys in the same second as another trade | 95 % | 97 % |
| SOL traded in the coin in the last 5 s (median) | 5.7 | 8.2 |
| price change in the last 5 s (median) | +18 % | +11 % |
| largest buy in the last minute (median) | 3.0 SOL | 4.0 SOL |
| coin's age at the buy (median) | 1 s (half in the first second) | 14 s |
| SOL in the curve at the buy (median) | 27 % of the way | 32 % |
| holds for (median) | 3 s | 1 s |
| sells when | no trade in the last second (45 % of sells), or 2 s held and 1 % off the peak | no trade in the last second (50 %) |

Both follow the same rule and it needs no wallet list: buy in the block where a large buy lands in a coin that is
moving this second, hold one to three seconds, sell at the first pause in the buying. The tree that separates their
buys from their passes is right 92 % of the time on "a trade this second, and the price up over the last 5 s"
(6Z91) and "a trade this second, the coin up over 5 minutes and under 8 minutes old" (Fhbh1). It is the trade the
paper test above scores (a paid place behind a 1-3 SOL buy), with any large buyer as the leader. Whether the rule
pays without their place in the block is the open question: the same buys one block late lose.

`qualify run --max 60 --early` on the winners study's wallets: 60 scored at the live copy landing, **0 qualified**, 33
stay candidates, 27 retired. No mid or late swing trader from that list is worth copying at our landing.

## What this does not say yet

- These are price changes for a copy too small to move the price. A 2 SOL buy in a coin holding 15 SOL pushes the price
  about 10 % and gives about 4 % back on the way out; fees are 2.5 %; a 0.035 SOL bid on 2 SOL is 1.8 %. The good
  splits (+8 to +11 %) are the ones left with a margin; the rest are not.
- The splits are one at a time and were read after the fact. Combined rules (second known wallet in, 60 SOL of volume,
  a 1-3 SOL buy) go to the lab at 1-5 SOL with the bid counted, then to forward paper: 20 trades, positive without the
  3 best, before any live proposal.
- Being right behind the leader is an auction. Our 0.003 SOL bid lands there behind quiet wallets; behind wallets the
  bots follow it lands last.
- Running: `copy-context --any-buyer --taken-by <the three bots>` (every wallet's 1 SOL buy, with the share the bots
  followed, by SOL in the curve, buyers, last 30 s of volume) and `study` on each bot (about 50 factors at what it
  bought against what it passed on: socials, creator's history, holders, market activity, time of day).
