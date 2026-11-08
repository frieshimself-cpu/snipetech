"""What every deciding agent (the 15-minute operator, the daily analyst, the weekly strategist) is told about OKTech:
facts measured on our own live trades, each with its evidence. Update a fact only from a live result written into
docs/live-recipe.md; a feature counts as working only after a live trade shows it fired."""

from __future__ import annotations

OKTECH_PLAYBOOK = """\
OKTech playbook (measured on our own live trades, 5-6 Nov 2026; a slot is about 0.214 s):
1. One copy sniper per leader per OKTech wallet. OKTech buys a coin once per wallet, so a second sniper on the same
   leader adds no buys, only crossed exits and failed duplicate sells (5 of 5 contested signals gave one buy).
2. A sniper's last buy, the one that uses up its totalSnipes cap, can be left with no exit at all (4siK 5 Nov; DTAU
   and 5m5h 6 Nov: -24 % and -65 % with no dead exit, stop-loss or timer). The book relies on each sniper's first buy
   only and replaces it once the wallet holds nothing. A coin nobody is selling is the owner's decision, never an
   agent's: report it with its /sell line.
3. OKTech charges the task's buy priority fee again on the landed sell of every exit (7 of 7 fee-test sells); the buy
   tip never leaks into sells. A bid is raised only through the tip, never through the priority fee.
4. Every sell brings 1.2-1.6 failed duplicate sends (42 for 31 sells; 4 for 3 sells on 8 Nov), each paying its
   priority fee plus the base fee while its tip is refunded; one send requested does not stop them. A round trip at
   the low bid cost about 0.009 SOL, not the lab's old 0.007. Since 8 Nov the sell bid is the tip ([oktech]
   sell_tip_sol, 0.002) and the sell priority is near zero ([oktech] sell_priority_sol, 0.0001: what each twin
   pays), on every sniper made from then on, each kind keeping the sell tip it bid before (copies 0.002, the rest
   0.001); a running sniper keeps its old sells until the book replaces it. On the first live sells after it, verify
   in fills that the landed sell paid its tip (0.002 on a copy) and its twins 100,000 lamports priority, and in measure's sells_per_buy line that sells still land once per buy: if OKTech's untipped copies were
   what landed, a coin could stay unsold (the unsold-coin watch and /sell cover it; the owner can set the priority
   back). What each kind of sniper bids is the fee policy's (read_fee_policy): the minimum unless 10+ fills at a
   higher tip landed measurably earlier.
5. Exits as they fire: the 1 s dead exit sells 1.5-2.4 s after the buy (n 6); the copy exit 0-1 slot after the
   leader's big sell (n 4); the stop-loss 1-2 slots after the price crosses (n 3); the 300 s timer within 2 s (n 6).
6. Landing: quiet leaders are copied 0-1 trade behind at +1-3 % (6 Nov, n 13); crowded leaders 2-4 behind at
   +18-43 %, whatever we pay. Paying 20 times the fee (0.02 + 0.02) landed no earlier and cost 6 times as much a
   round trip (fee test, 6 Nov).
7. maxMcapUsd is a minimum-out, not a filter: a skipped signal becomes a failed transaction that still pays its fee.
8. The OKTech API accepts features it then ignores (it answers 201). A feature counts as working only after one live
   trade shows it fired.
9. Never send autoStop: it switches the exits off.
10. Live beats paper. 2YDn scored +0.0146 SOL a trade in the lab and lost 5 of 5 live copies (-0.058 each). A wallet
   is paused after 5 losing live copies whatever its verdict says.
11. Copy only a leader's signal buys. G2oZ's opening buys rose 17-26 % in the first minute (n 2); its top-ups lost
   (one live copy -0.068 SOL, 40 on paper -57 % at 300 s). The verdict's copy_plan.min_leader_sol is each wallet's floor.
12. Seed account off on every sniper: its sweeps were 100k-compute failed transactions (13 on 5-6 Nov, 0 since)."""
