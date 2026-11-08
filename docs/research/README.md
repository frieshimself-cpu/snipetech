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
