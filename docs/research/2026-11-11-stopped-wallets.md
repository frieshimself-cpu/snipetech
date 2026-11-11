# Do the crowding rule and the history rule cost money? What the wallets they stop made afterwards (11 Nov, 17:35 UTC)

**Question (audit of 11 Nov, open items 4 and 8):** the five wallets with the best paper numbers are all stopped by
the crowding rule, and the wallet with the most copies by the history rule. Are those rules throwing money away?

**Answer: no. And the paper numbers of those wallets were too kind for a reason nobody had seen.** Every wallet that
looks good to copy is the first of one operator's three or four wallets, which buy together in consecutive
transactions. The paper copy lands "behind the third buy after the wallet's", and for such a wallet those three buys
are its own companions: the paper copy sits in the first place behind the operator, ahead of every outside bot. That
place exists, and it pays. It is taken in every block by bots that buy 2 to 7 SOL and bid 0.03 to 0.3 SOL for it.
One place further back the profit is gone.

All numbers: the server's tape to 11 Nov 17:09 UTC; the qualify lab (0.2 SOL a copy, the exit the book sends, 0.00421
SOL a transaction, measured); each wallet's copies **after** its first verdict that only that rule stopped (a verdict
is made from the copies before it, so nobody picked on these). The job is `stopped-wallets` (new, 11 Nov); the wallets
picked under the same rules are measured beside them as the reference.

## Report

OUTCOME: nothing to arm and no rule to loosen. Copies of the 20 wallets only the crowding rule stops lose 0.0020 SOL
a copy after their verdicts (259 moments, lower bound -0.0047); 6 of 14 wallets are up. The three that hold (BNnN2M
+0.043 SOL a copy over 27 copies, 96 % winners; DEdEW3 +0.017 over 51, 80 %; 2pLuge +0.011 over 27) hold only at the
first place behind their own wallets: one buy further back BNnN2M makes +0.011, DEdEW3 loses 0.011. That first place
is bought today by bots bidding 0.03 to 0.3 SOL a buy; our bid is 0.003. One wallet hours from being armed (8MaVa9)
has the same shape and loses one place further back.

### What the stopped wallets made after their verdicts

| group | wallets (with copies since) | copies (moments) | SOL a copy | lower bound | win | wallets up | at the verdict |
|---|---|---|---|---|---|---|---|
| only the crowding rule | 20 (15) | 348 (259) | -0.0020 | -0.0047 | 46 % | 6 of 14 | +0.0207 |
| only the history rule | 1 (1) | 13 | -0.0078 | -0.0103 | 15 % | 0 of 1 | +0.0088 |
| both | 2 (1) | 51 | +0.0172 | +0.0135 | 80 % | 1 of 1 | +0.0162 |
| picked (the reference) | 55 (29) | 694 (380) | -0.0069 | -0.0091 | 34 % | 4 of 24 | +0.0218 |

A moment several wallets of a group share (the same second) counts once: the wallets come in sets. "Wallets up": of
those with five or more copies since. The picked wallets lose after their pick too, which is what the forward check
is for: it has retired most of them.

### The same copies, landed further back

| landed behind the wallet's | crowding | both (DEdEW3) | picked | BNnN2M | 8MaVa9 | 74xVT9 |
|---|---|---|---|---|---|---|
| 3rd next buy (the qualify lab) | -0.0020 | +0.0172 | -0.0069 | +0.0432 (96 %) | +0.0190 (73 %) | +0.0081 (56 %) |
| 4th | -0.0078 | -0.0105 | -0.0097 | +0.0109 (67 %) | -0.0031 (32 %) | +0.0052 (53 %) |
| 5th | -0.0095 | -0.0146 | -0.0106 | +0.0020 (48 %) | -0.0077 | +0.0023 (47 %) |
| 6th | -0.0107 | -0.0154 | -0.0117 | +0.0007 (44 %) | -0.0086 | +0.0003 (41 %) |
| 8th | -0.0103 | -0.0192 | -0.0138 | -0.0013 (37 %) | -0.0074 | -0.0012 (38 %) |

SOL a 0.2 SOL copy after costs, copies since each wallet's verdict (n: BNnN2M 27, DEdEW3 51, 8MaVa9 22, 74xVT9 34).

### Why one place decides it: who is in those blocks

Tape order inside the block, every time the same:

- **BNnN2M** buys 1.5 SOL, and 2pLuge, 3GzBLp and 7Pteyy buy 1.5 SOL each in the next three transactions (44, 42 and
  42 of its 46 coins in three days, always the same block). 6 SOL in four consecutive transactions: one operator.
  7 to 90 places later a buy of 3 to 5 SOL from a wallet that changes every time, then HqhUCX (0.49 SOL) and 7DLM4c
  (0.2 SOL, 41 of the 46 coins), then the small ones.
- **DEdEW3** (labelled 0xsolomon) with fRECQz, AwbjKL and 9s6jeG, 1.5 SOL each, the same way. One to twenty places
  behind them: 6Z91 (4.9 SOL), 88UqCR (2.5 SOL), the second bot wallet (…dkm5zS, 3.95 SOL, once in the very next
  place).
- **8MaVa9** (labelled daumeneth; picked on 11 Nov, due to qualify at its next review, 12 Nov 04:48 UTC) with HodLXS,
  TWiNYz and PUMPRL (0.46, 0.64, 0.82, 1.01 SOL). 30 to 100 places behind them 6Z91 with 2.47 SOL.
- **74xVT9**, the one qualified wallet, is one of four too (with G71566, 4eT9SV, 9dr7jz), but mostly not the first:
  the three buys behind it are other people's. Its paper number was measured where an outsider lands, and it is the
  one that falls slowly (+0.0081, +0.0052, +0.0023, +0.0003).

So "behind the third buy after the wallet's" means two different places. Behind a wallet that buys alone or last of
its set: three outsiders ahead of us, about where our copies land. Behind the first wallet of a set of four: nobody
ahead of us. No copy of ours can land between an operator's own transactions, and the bots behind them are faster
and bid more than we do.

### What that first place costs (the fee records, live since 16:34 UTC)

| who | buys since 16:34 | size | bid a buy (priority fee + tip) | bid as a share of the buy |
|---|---|---|---|---|
| the second bot wallet (…dkm5zS) | 3 transactions | 3.4 to 24.7 SOL | 0.064, 0.12 and 0.294 SOL | 0.5 to 9 % |
| 6Z91 | 35 buys | 1.8 SOL (median) | 0.027 median, 0.18 at the 90th percentile, 2.18 at most | 1.5 % (median) |
| the small copiers in the same blocks (0.15 to 0.55 SOL) | | | 0.001 to 0.003 SOL | |

The small copiers land 20 to 80 places behind the big ones. Our copies bid 0.003 SOL. The place is not bought by the
bid alone: at 17:29 a 2.96 SOL buy with a 0.016 SOL bid landed nine places ahead of the second bot wallet's 0.294.

The second bot wallet does exactly this trade: in five days it bought 1,440 SOL on 306 coins and sold 1,557 (+117
SOL before pump.fun's fees of about 30 SOL and its own bids). It buys right behind 3VUNtV (39 coins), H31vEB,
9xoWzo, 9eczFC, 9LXWa7, 8MaVa9's set and DEdEW3's. The `winners` study had already listed it (+65 SOL over 178 round
trips).

### After the landing was corrected (17:50 UTC)

The qualify lab now lands a copy behind the copied wallet's own companions and then behind three buys by others
(docs/superpowers/specs/2026-11-11-companion-landing-design.md). The same job, the same copies:

| wallet | companions | before | after |
|---|---|---|---|
| BNnN2M | 3 | +0.0432 | +0.0007 |
| DEdEW3 | 3 | +0.0172 | -0.0158 |
| 8MaVa9 | 3 | +0.0190 | -0.0086 |
| 74xVT9 | 0 | +0.0081 | +0.0081 |

13 of the 20 crowded wallets and 10 of the 55 picked ones buy with companions. No wallet qualifies any more on a
place we do not reach; 74xVT9's number is the same because it was measured behind outsiders all along.

### The agents' own lab on that first place, run again (18:20 UTC)

The server's analyst had this lead since 10 Nov (idea I51: "a paid place right behind a known wallet"): its labs
scored a copy right behind the wallet, which for a wallet with companions is between one operator's own transactions.
Since 18:19 UTC every lab with a wallet rule lands a copy behind the followed wallet's companions. The same lab again
(72 h, the second known wallet in a coin with a 1 to 3 SOL buy, a 1 SOL copy, 0.02 SOL a transaction, sold after 5 s;
53 of the 2,658 tracked wallets buy with companions):

| copy lands | unseen trades | SOL a 1 SOL copy | lower bound | win |
|---|---|---|---|---|
| right behind the wallet and its companions | 870 | +0.059 | +0.051 | 50 % |
| one trade further back in the block | 870 | +0.005 | -0.001 | 38 % |

Before the fix the first row read +0.063 (n 800): for these signals the correction is small, the lead stands. It is
the first place and nothing else, about 290 signals a day. The lab charges 0.02 SOL on the buy and on the sale; what
the first place really costs is the open number. In the first two hours of bids on the tape the first outside buyer
behind a tracked wallet's 1 to 3 SOL buy bid 0.0002 SOL at the median and 0.015 at the 90th percentile (146 blocks):
the place is taken by speed as much as by the bid, and many of those "outsiders" buy the set's own size two places
behind, so they are its unlabelled companions.

## Lessons

- The copy edge sits in one place: the first place behind a multi-wallet operator's own buys. One buy further back it is gone (DEdEW3 +0.0172 a 0.2 SOL copy at the 3rd next buy, -0.0105 at the 4th, n 51; BNnN2M +0.0432, +0.0109, +0.0020, n 27).
- "Behind the 3rd buy after the wallet's" counts an operator's own companion wallets as buyers ahead of us: for the first wallet of a set of four the paper copy lands ahead of every outsider, a place our 0.003 SOL bid does not buy. Such a paper result is not evidence (CLAUDE.md rule 2).
- That first place is bought with 0.03 to 0.3 SOL a buy on buys of 2 to 7 SOL (6Z91: median bid 0.027 SOL, 35 buys; the second bot wallet: 0.064 to 0.294, 3 transactions); copiers of 0.15 to 0.55 SOL bid 0.001 to 0.003 SOL and land 20 to 80 places behind them.
- Wallets only the crowding rule stops lose after their verdict: -0.0020 SOL a copy over 259 moments (lower bound -0.0047), 6 of 14 wallets up. The rule costs nothing.
- Wallets picked under the 7 Nov rules lose after their pick: -0.0069 SOL a copy over 380 moments, 4 of 24 wallets up; the forward check is what keeps them from being armed.
- 74xVT9's result survives two places further back (+0.0081, +0.0052, +0.0023 at the 3rd, 4th, 5th next buy, n 34): it is usually not the first wallet of its set, so its three next buys are outsiders'.

## What was not tested

- The trade the second bot wallet makes: 2 to 5 SOL right behind an operator's set at a bid of 0.05 to 0.3 SOL. The
  lab cannot score it yet: "right behind the wallet" lands between the operator's own transactions. It needs the
  landing to count only outsiders (next build), a few days of bids on the tape, and then a lab at that size and that
  cost. If it pays on paper it is a yes/no for the owner: the size is above 0.2 SOL and the bid is new.
- The history rule on its own: one wallet with 13 copies. No answer.
- Live fills behind any of these wallets: none exist.
