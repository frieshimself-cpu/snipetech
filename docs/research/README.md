# Strategy research rounds

The record of the continuous strategy research the cloud session runs (7 Nov 2026 on): each round reads every
earlier round's report here, chooses angles nobody has covered, screens them with read-only SQL on the server, runs
labs one at a time at the landing our live buys get, puts every positive result to three skeptics, and writes its
report here as `<date>-round-<N>.md`. Survivors go into the forward paper test (`paper freeze --trigger ... --only-live`),
which feeds the owner's automatic live test. `research-loop.workflow.js` is the round itself (a Claude Code workflow
script, run from the cloud session with `args: {round: N}`); it only reads the repo and the server and runs labs.

The server's agents read this record too (since 8 Nov): every operator, analyst and strategist turn carries the newest
round's OUTCOME paragraph (the first paragraph under its `## Report`) and the lines of its `## Lessons`, then the line
below for each older round (`operator/learning.py`, `research_record`), and the operator and the analyst can read any
report here whole with `read_file docs/research/<name>.md`. So every report keeps those two sections, each lesson one
line with its numbers, and every round gets one line below that links its file. The agents mark an idea a round
refuted as rejected, and leave a round's builds and its questions for the owner to the cloud session and the owner.

Round 1 is the edge hunt of 7 Nov 12:20 UTC (seven fixed angles); round 2 on choose their own.

- [Round 1](2026-11-07-round-1.md), 7 Nov 12:20-16:25: 8 labs, none positive; MIG the one open idea.
- [Round 2](2026-11-07-round-2.md), 7 Nov 16:27-20:48: 5 labs, none past the skeptics' bar; copy verdicts do not carry
  forward (-0.0147 a copy on later tape); leads: MIG on Qm-uri coins (decided by a forward test about 8 Nov 04:00 UTC)
  and the creator-0 crowd hunt (15 test trades, +0.0234; decided on 30 new firings, about 9 Nov).
- [Round 3](2026-11-07-round-3.md), 7 Nov 20:50 - 8 Nov 00:00: 5 labs, none past the bar; one bundle operator's coins
  (41 SOL first-slot bundles, creator 0) are behind nearly every positive hunt and copy result; the creator-0 hunts lose
  on unseen 4-5 Nov tape; the one lead is hunting that operator's coins directly, decided by its next 20 launches.

- [Round 4](2026-11-08-round-4.md), 8 Nov 00:05-09:10: nothing frozen; the lab, paper test and autotest count LaunchLab coins our pump.fun-only snipers cannot buy (30-60 % of creator-0 firings; all of bbe92cca81's paper profit); the 41 SOL bundle operator is 15 fixed wallets (+0.0214 a trade on 27 coins, but silent since 7 Nov 16:55 and real wallets in our spot made about 0); the LaunchLab wave has ended.

**Launch-type labs before the venue knob mix launchpads.** Until round 4's priority-1 build (8 Nov, on the server once
deployed) the lab and the paper test fired launch, hunt, whale and burst rules on Raydium LaunchLab launches too, which
no sniper of ours buys; a lab job from before the deploy holds such rows. Since then those rules fire on pump.fun
launches only unless the trigger says `"venue": "launchlab"`, and frozen rules' LaunchLab paper trades are kept but not
counted (docs/architecture.md, "The lab and the paper test").

- [Round 5](2026-11-08-round-5.md), 8 Nov 12:15-14:00: nothing frozen; pump.fun-only creator-0 hunts are positive but 75-90 % of their test rows are the 41 SOL operator's coins (which returned 12:10-12:43); crowd hunts without mayhem and holder-reward coins still lose; fast-bot consensus, the session clock and round 2's MIG-10 Qm test are refuted; one thin picker lead (Ewqs4Pbc).

- [Round 6](2026-11-08-round-6.md), 8 Nov 14:10, interrupted by the usage limit after 3 of 6 lenses: a serial rug crew ("forg", one whale plus dust wallets) is all of the loose creator-0 paper rules' loss today; counting only 0.2 SOL+ buyers avoids it but makes no edge; only the 41 SOL operator's cluster is positive at the landing.
- [Round 7](2026-11-08-round-7.md), 8 Nov 15:30-16:10, inline (agents blocked by the usage limit): the owner's three asks. KOL copies refuted at our landing (7 scored, none qualifies); crowd-following ("FOMO") leaders were refuted in round 1 (-0.016 to -0.019 on 1,906 copies); dev-wallet snipes of the five serial launchers on the leaderboard refuted at the launch landing (1,306 trades, all lower bounds below zero) with the new `creators` lab knob; MIG-10 at 0.5 SOL refuted (-0.022, n 72).
- [Round 8](2026-11-08-round-8-fees.md), 8 Nov 20:57, by the server analyst on the owner's API credits: paying more to land ahead of other copy traders does not pay at 0.2 SOL (1st instead of 3rd behind the leader is worth 0.004-0.007 SOL on early buys; the bots ahead bid 0.01-0.3 SOL).
- [Round 9](2026-11-08-round-9-early-leaders.md), 8 Nov 23:06: the new `early-leaders` job ranks wallets by their early buys; it finds AG89 first, but its 7 new wallets all fail qualify at the live landing (bundle members, mostly off pump.fun, or losing with real exits).
- [Night grid](2026-11-09-night-grid.md), 9 Nov 02:46-04:20: every strategy at 2-40 SOL with paid fees; copying a labelled KOL's first buy pays about +3 % a trade at 2-20 SOL (n 115-136, lower bounds +0.007 to +0.144), flat at 0.2 SOL, nothing at 40 SOL; frozen into forward paper; live test proposal at 2 SOL for the owner.
- [The same-block finding](2026-11-10-same-block.md), 10 Nov 11:30: 6Z91 buys in the same block as the wallet it copies and sells about 5 s later (+145 SOL on 349 such coins in 72 h); after that block the price falls on average (−1.7 % at 15 slots, −18.5 % at 10 min); it pays a median 0.033 SOL a buy for its place; our copies land in the leader's block too (20 of 24) but only at 0.2 SOL and a 0.003 bid behind quiet wallets; ideas I50, I51.
- [Three bots and the context of a copied buy](2026-11-10-three-bots.md), 10 Nov 12:30: 6Z91 and two more bots follow no wallet list; each buys 1-3 SOL right behind someone's 1-3 SOL buy in the same block and sells on a 3-6 s timer in pieces. A copy right behind a known wallet is worth +8 to +11 % gross at 5 s when a second known wallet is already in, the coin has 60+ SOL of volume or 5+ min of age, or the buy is 1-3 SOL, and about nothing behind the first known wallet in a young, thin coin; before fees, bid and own price impact.
- [The owner's dip and instant-bond ideas](2026-11-11-dip-and-instant-bond.md), 11 Nov 15:40: both lose at our landing. A hot coin's 60 % fall bought 3 trades later loses 0.054 SOL a 0.2 SOL trade in 30 s (n 1,355); bought only once the price has turned 10 % up it loses 0.0097 (n 920: the bounce is about +2 %, the costs 6.7 %), also at a perfect landing (-0.0042) and at 1 SOL; a buy at the migration of coins that migrate within 60 s of their create loses 0.0151 on 367 unseen trades. New job: `dip-buys`.
- [What the wallets a rule stops made afterwards](2026-11-11-stopped-wallets.md), 11 Nov 17:35: the crowding rule costs nothing (the 20 wallets only it stops lose 0.0020 SOL a copy after their verdicts, 259 moments). The wallets that look best to copy are the first of one operator's three or four wallets buying in consecutive transactions; "behind the 3rd buy after it" counts those companions as buyers ahead of us, so the paper copy sits in the first place behind the operator, where bots of 2-7 SOL bid 0.03-0.3 SOL a buy. One buy further back the profit is gone (DEdEW3 +0.0172 to -0.0105, BNnN2M +0.0432 to +0.0109). New job: `stopped-wallets`.
- [GMGN's 19 agent skills: what snipetech can use](2026-11-12-gmgn-skills-map.md), 12 Nov: read as text, nothing run. GMGN finds wallets by itself (KOL and smart-money trade feeds, search by name, top traders per coin; no leaderboard call), shows fees per trade and per wallet (`priority_fee`, `tip_fee`, `bought_fee`, `sold_fee`) but not the place in a block, and gives `follow_count` for how copied a wallet is; all polled, too slow for a first place; the build list and the commands never to wire are in the file.
- [The three bots the owner named](2026-11-12-winning-bots.md), 12 Nov 17:30: on 7 days of tape 6Z91 made +1,051 SOL before bids (+72.7 after bids on the one day with bids: 43 % of its result goes to bids), Fhbh1D +312 (+44.3 after bids; half its buys on LaunchLab), 7iyYn4 lost 10.7 (the same wallets and exit as 6Z91 at a bid near zero). A bid buys a chance at the first place behind a followed wallet, not the place: 13 % under 0.002 SOL, 51 % at 0.015-0.04, 76 % at 0.3+ (6,779 buys behind 57 wallets, 24 h); the first place is worth about 10 % of the buy and the second 5 %, so the bid pays at 2 SOL and more, not at 0.2. Candidates of the same kind: dKMjeGJ4 (+558 SOL of cash in 7 days), 2H6Dwrq, 3cbuqqiT, HmR7SxDo, 88UqCRfa.
- [Finding the winning bots and big wallets without being handed them](2026-11-12-finding-winners.md), 12 Nov 17:00: `winners` now ranks every busy wallet on the tape by the SOL it made after its landing fees and marks the bots; the server's discovery asks it every six hours. Built and tested, **not yet run on the server**. Each wallet's own fills at its own place in the block, not a copy's. A small real sample: 6Z91 +19.0 SOL in its last 5 hours after 4.77 SOL of fees (its buys bid 0.097 SOL on average, ours 0.003); Fhbh1 -9.1 SOL in its last 10 hours. Public boards: pump.fun's own KOL board (kolscan.io) reads without a login and holds 203 named wallets we lack, 15 of them ranked and active on our tape; GMGN's official API gives names and tags for a wallet. Neither lists bots. Both are researched, not wired.
- [A paper place that follows the bid](2026-11-12-bid-landing.md), 12 Nov 19:00: a paper copy can stand where its bid puts it among the wallets that really bought behind the followed one (`lab --copy-position bid` / `bid-` with `--buy-bid`; `bid-`, one buy further back, is the owner's bar), and `paper freeze` takes a place and a bid of its own (`--copy-position`, `--buy-bid`, `--sell-parts`) that the forward run keeps. Built and tested on synthetic tapes, **not yet run on the server**. A model, not where our copies land live: two real fills exist to check it against (`place-bids --model-check`). A first look at 18 of Cupsey's blocks: a 0.05 SOL bid is first on 22 % of them and second on 56 %; someone stood within two transactions of Cupsey on a third.

**Lab numbers from round 1 and from later labs are not comparable.** Round 1's labs charged the lab's flat 0.0035 SOL
a transaction (0.007 a round trip, against about 0.009 live) and scored launch-type exits as if OKTech watched a coin
from the moment of the buy (it starts about 1.3 s later), so its numbers are 0.002 to 0.006 SOL a trade too kind. Since
7 Nov evening (on the server once deployed), `snipetech lab` charges the measured cost of a transaction unless
`--per-tx` says otherwise and says which it charged (`per_tx_sol`, `per_tx_source`: measured, last measured, estimate
or flag), and every exit of a launch, hunt, whale or burst rule waits OKTech's 1.3 s watch delay (`watch1.3s` in the
exit's name). "Last measured" is the cost the measure stage last measured, kept once a week passes without 30 low-bid
fills (the bid model reads only 7 days); "estimate" is 0.0045 while nothing has been measured. No lab charges the flat
0.0035 any more. The round script runs its labs without `--per-tx`, and holds a result charged less than live (or one
from a server that does not say) to live cost, 0.003 SOL a trade, before it counts as positive.
