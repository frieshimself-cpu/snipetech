# Three specialised research agents — design (12 Nov 2026)

Status: **built on 12 Nov (steps 1 to 10), switched off, not deployed, never run against the real model.** Step 11
is not built. A fourth role was added the same day. The last section, "What was built, and what differs from this
design", says exactly what the code does where it is not what the text below says: read it first. The text between
here and that section is the design as it was written, unchanged. Task 148. The owner asked on 12 Nov: "Could we
maybe add more specalised agents like lookign for wallets etc, ot make it even better. Is there anything else that
can speed up process".

## Why

Today one analyst does all the research. It has one idea queue. It asks one question a turn.

Measured on the server on 12 Nov, 12:34 to 17:32 UTC, the five hours after it came back (`operator/log.txt`):

- 14 analyst turns ran and cost 12.97 $. That is 0.93 $ a turn (0.42 to 1.73 $), with 8 to 31 tool calls.
- A turn took 92 seconds in the middle case and 199 seconds at most. A round comes every 900 seconds.
- Since 16:02 an analysis is allowed every round, and it ran in 6 of 6. That pace is 96 turns and about 89 $ a
  day, of the 500 $ allowed.
- The main session measured the rest today: a wallet-rule lab takes about 2 minutes, the machine is busy a third of
  the time, and the analyst asks for 4 to 8 tests an hour.
- For those five hours the analyst followed one thread (the big wallets at size). Meanwhile 18 research ideas stood
  at "testing", among them the wallet hunts (I22, I44, I64) and the fee question (I20).

So the limit is not the machine and not the money. The analyst thinks for a minute and a half and starts a lab. The
lab ends about two minutes later. Then the answer waits up to 13 minutes for the next round. And three jobs wait behind
each other in one queue. The owner has named all three: find the wallets and the people without being handed them
("me having to give you his wallet is insane"), find the rule that pays, and outbid the other bots ("it all comes
down to fees").

## The design in short

- Three agents take over the analyst's turns between the daily analyses: the **wallet scout**, the **strategy
  tester** and the **fee and place analyst** (`scout`, `tester`, `fees` in code). Each has its own instructions, a
  short tool list, its own idea queue and its own share of the day's dollars.
- All three start at every operator round, side by side, each in its own process. One with nothing new skips its
  turn and costs nothing.
- The daily analysis (the day's report after 06:00 UTC) and the post-mortem of live trades stay one agent, with
  today's instructions.
- Nothing new acts live. No role has a tool that trades, as today. Live steps stay with the round's own code (the
  book, the owner's /test, the autotest under the owner's switch) and with the owner.

## The three roles

| | wallet scout | strategy tester | fee and place analyst |
|---|---|---|---|
| question | Who is worth following, and who are they? | Which rule pays where we land? | What is a place worth, and which bid wins it? |
| reads | the `candidates` table that `discover` fills hourly (tape, winners, the owner's list, SolanaTracker, Birdeye, coinfirst, vybe; GMGN once its branch is merged), verdicts, labels, the tape | lab and paper results, the strategy search, events, the other two tables | the tape's `priority` and `tip` per trade (since 11 Nov 16:34 UTC), our fills, the fee policy, live round trips |
| may start | `winners`, `playbook`, `leaders-of`, `early-leaders`, `fomo-wallets`, `discover-wallets`, `follow`, `copy`, `copy-rank`, `copy-context`, `stopped-wallets`, `label`, `place-bids --each` (its `by_wallet`: who is copied most, who holds the first place) | `lab` (entries, exits, `--buy` sizes, `min_mcap` / `max_mcap` bands, `--sell-parts`; OKTech's sell stages once that branch is merged), `paper freeze`, `dip-buys`, `launch-tools`, `study`, `copy-context` | `place-bids` (`auction.in_bid_order`, `win_at`), `lab` only with `--copy-position`, `--per-tx` or `--buy-bid`, `playbook` (a bot's bids), `copy-context --behind-leader` |
| writes | `operator/scout-facts.md`: one row a wallet (name or person where a source gives one, bot or trader, SOL a day, who copies it and how many in its block, its companion wallets, where it stands in qualify). Wallets labelled `cand` for qualify. `set_candidates`. | `operator/tester-rules.md`: one row a frozen paper rule (trigger, landing, bid, size, forward trades, total, lower bound, capped bound, top-5 share, which reading). Lab results in its journal. | `operator/fees-bids.md`: one row a kind of trade and size (the place aimed at, the bid as priority plus tip, how often it wins the place, what the place is worth, n, dates, or "not measured"). Tip-ladder proposals. |
| hands on | an idea for the tester ("score copies of this group") or for fees ("price the first place behind this wallet") | an idea for fees ("this rule needs the first place: what does it cost at 5 SOL?") | an idea for the tester ("score rule X at place +1 with 0.05 SOL charged") |
| never | starts a lab or a freeze; calls a wallet copyable (its own profit is at its own place; qualify's verdict at the copy landing is the judge) | scores a rule at a better place than `fees-bids` says its bid buys; freezes a rule the paper test refuted | starts a ladder; names a bid without the size it pays at; changes the fee policy (the strategist's and the owner's) |

The group `big` (the owner's named wallets) belongs to the scout as a list and to the tester as a trigger. History
from before our tape (older, bigger coins) comes to the scout through the backfill once that branch is merged.

### What each is told (outline of its instructions)

Shared core, cut from today's rules: you never trade and ask for no tool that does; any live step is the owner's;
your jobs are the ones your list names; every test names the idea it tests; a paper result names its landing and the
bid that buys it; check a winner for a few trades that carry it; lessons only from evidence, with n; plain words.

**Scout.** (1) Find wallets and bots before the owner names one: start from `winners`, then read a find's rules with
`playbook` and whom it follows with `leaders-of`. (2) One operator's wallets count as one. (3) Say who is copied
most: the wallets other bots buy behind are where a first place is worth the most. (4) Label the best `cand`, at
most 40 a day, so qualify's queue is not flooded. (5) Keep `scout-facts` current and short. (6) A wallet's own
profit is never a copy's profit: say so every time.

**Tester.** Today's "Hypotheses" part of the analyst's instructions, unchanged in its rules: one trigger as JSON, the
lab scores it where it trades live, a lower bound above zero at the live landing goes to `paper freeze`, zero or
below is refuted with its numbers, "not measured" is not "refuted", one forward record a rule, the capped bound and
the top-5 share before any winner is believed. Added: (1) sizes from 1 SOL up while SOL a day still rises;
(2) market-cap bands; (3) exits and OKTech's sell features; (4) take the place and the bid from `fees-bids`, and the
wallet groups from `scout-facts`; (5) the owner's bar: no rule is written up for real size before its forward paper
record is positive.

**Fees.** (1) What a place is worth: the same lab at place +0, +1, +2 at the size meant; the difference is the most
a bid may cost. (2) Which bid wins it: `place-bids`; `in_bid_order` near 1 means the place is bought, near 0.5 means
speed takes it and no bid buys it. (3) Priority fee against tip, apart (the owner: "priority fee has to be decent if
its a gigga copytrade but tip is the most important"): the tape has both columns. (4) Preconfirmations: compare our
fills with and without. (5) Our own landings (`read_fills`, `read_round_trips`, `ours` in `place-bids --each`) are
the truth; paper places are not. (6) Keep `fees-bids` current. (7) A tip-ladder proposal is an idea of kind
live-test: the wallet followed, at most 0.2 SOL a buy, the tips, the buys per tip, the worst case with every tip
counted as lost (inside 2 SOL lost a day), the stop rule, what each outcome changes in the table, and how the place
will be read. At most one open proposal. No proposal until our fills can show where a bid landed: on 12 Nov the
first ladder arm took equity from 0.4730 to 0.2513 SOL and `measure` paired none of its copies. The agent never
starts a ladder. The cloud session does, inside the owner's standing yes and with its entry in
`docs/live-recipe.md` first, or the owner does with /test. Anything larger needs the owner's yes for that step.

### Tools (all exist today; step 11 would add the first new one)

| tool | scout | tester | fees | daily analysis and post-mortem |
|---|---|---|---|---|
| `read_job_log`, `read_file`, `sql`, `read_lessons`, `add_lesson`, `list_ideas`, `upsert_idea` | yes | yes | yes | yes |
| `start_job` | its commands | its commands | its commands | all, as today |
| `write_note` | info only, one a turn | info only, one a turn | info only, one a turn | as today |
| `write_report` | `scout-facts`, its journal | `tester-rules`, its journal | `fees-bids`, its journal | the day's report |
| `read_verdicts` | yes | yes | no | yes |
| `set_candidates` | yes | no | no | yes |
| `read_events` | no | yes | no | yes |
| `read_fills`, `read_fee_policy`, `read_round_trips` | no | no | yes | yes |

A shorter list is also a cheaper turn: today every turn carries the long help text for all 18 research commands.

**What no role can do, kept by the code and not only by its instructions:** reach OKTech, a wallet or a key (no such
tool exists); start one of the owner's commands (`operate`, `oktech`, `sweep`, `unwrap`, `book`, `strategist`,
`config`: `start_job` refuses them, as today); write the owner's switches (the test, autotest, risk and budget
files); send a decision note. Only the operator round and the owner's switches act live.

### Queues and shared memory

- An idea gets a `role` field. A role's turn sees and tests only its own queue. A research idea without a role is
  the tester's. Ideas of kind code, config and live-test have no role and wait for the cloud session or the owner.
- A role may add an idea to any queue. That is how work is handed on. Only the queue's own role moves or closes it.
- Each role may hold 15 open research ideas at most. It closes or merges before it adds.
- Lessons stay one shared file. Every turn carries the last ten, whoever wrote them, as today.
- Each role's turn carries the other two roles' tables (2,000 characters each). The notes file stays shared.
- Today's 19 open research ideas would be split so: scout I22, I28, I44, I50, I64; fees I20, I74; tester the
  other twelve (I6, I9, I29, I30, I33, I35, I59, I65, I68, I70, I73, I75). The cloud session sets this once.

## Scheduling

**Side by side, not one after another.** The operator round already starts background stages each round (`paper
run`, `research next`, `exitrun`). Three more rows start `snipetech operate agent --role <role>`, each as its
own job with its own lock. They need no new systemd unit, so no SSH step for the owner. The round itself does not
wait for them: alerts, the book and the kill switch keep their minutes. Three turns in a row inside the round could
take an hour against the unit's 30-minute limit, so that way is out.

**The "nothing new" gate, per role.** A role's turn runs when one of these holds, else it skips with one log line:

1. a job it started has ended since its last turn;
2. none of its jobs runs and its queue holds an idea nobody tested;
3. another role added an idea to its queue or changed a table since its last turn;
4. its news arrived: new verdicts or candidates (scout), the strategy search ended or a paper rule reached its next
   reading (tester), a new fill of ours (fees);
5. three hours have passed since its last turn.

A role that ends two turns in a row without starting a test or closing an idea waits an hour, unless rule 1 fires.

**The lab lock.** The machine runs two labs at once and `qualify` takes a slot too. The three roles together hold
at most one slot (slots less one, at least one). The other stays free for `qualify`, the strategy search and the
freeze. The scout's commands take no lab slot, apart from `stopped-wallets`. A refused lab is told which job holds
the slot. The same command with the same arguments is refused for six hours, whichever role asks, as today.

**The budget.** The owner's day (500 $) is split by share. Scout 20 % (100 $), tester 40 % (200 $), fees 25 %
(125 $). The last 15 % (75 $) is the reserve for the alert rounds, the daily analysis and the post-mortems. Today's
rule already pauses extra analyses when less than 75 $ of the day is left. A role's turn does not start when its
share is spent, or when the day is inside the reserve. An unused share is not passed on. A role's turn stops at 5 $
(today's dearest turn cost 1.73 $) and at the owner's tool-call limit. Three turns that check the ledger in the same
second can overrun by 15 $ at most, inside the reserve. The owner's `/budget every` stays the off switch: `every 0`
means the daily analysis only, and no role turn.

**What to expect.** The first column is measured. The other two are estimates from one afternoon's 14 turns.

| a day | today, one analyst | three roles | three roles that wait for their own lab (step 11) |
|---|---|---|---|
| turns | up to 96 | up to 288, likely 150 to 220 | the same |
| tests an hour | 4 to 8 | 10 to 20 | 20 to 30: one lab slot at two minutes a lab is the ceiling |
| dollars a turn | 0.93 | 0.6 to 1.0 | 1.5 to 3 |
| dollars a day | about 89 | 90 to 220 | 225 and up; the shares stop it at 425 |

More tests are not more winners. The count that matters is forward paper records that pass, and then live fills.

## Code changes, in build order (tests first, one session)

Each step is a failing test, then the code. Steps 1 to 8 change nothing on the server: step 9 is the switch.

| # | file and functions | tests that pin it |
|---|---|---|
| 1 | new `operator/roles.py`: `ROLES`, `SHARES`, `ROLE_TURN_USD`, `ROLE_COMMANDS`, `ROLE_TOOLS`, `HANDOFF` | new `tests/operator/test_roles.py`: `test_every_role_command_is_a_research_command_and_never_an_owners`, `test_every_research_command_has_a_role`, `test_shares_leave_the_alert_reserve`, `test_role_tools_exist_today` |
| 2 | `operator/learning.py`: `IDEA_FIELDS` gains `role`; `_validated`; new `role_of`; `list_ideas`, `untested` and `context` take `role`; `upsert_idea` keeps a queue to its role and at 15 open; `cli.py`: `operate idea --role` | `test_learning.py`: `test_research_idea_without_role_is_the_testers`, `test_a_role_sees_only_its_queue`, `test_role_cannot_close_another_roles_idea`, `test_role_may_add_to_another_queue`, `test_context_carries_the_other_roles_tables`; `test_idea_cli.py`: `test_idea_cli_sets_the_role` |
| 3 | `operator/jobs.py`: `OperatorJobs.start(argv, now, role, idea)`: the role's commands, the idea named, the fees lab's flags, new `_agent_labs` (one slot), new `record_test` writing `operator/tests.jsonl` (when, role, idea, job, command) | `test_jobs_tools.py`: `test_scout_cannot_start_a_lab_or_a_freeze`, `test_tester_cannot_start_a_wallet_study`, `test_fees_lab_needs_a_place_or_a_bid`, `test_role_test_needs_an_idea_of_its_queue`, `test_agents_leave_one_lab_slot_free`, `test_same_command_is_refused_whichever_role_asks`, `test_no_role_starts_an_owner_command`, `test_role_start_is_written_to_the_tests_ledger` |
| 4 | `operator/tools.py`: `make_operator_tools(..., role)` returns the role's subset; `start_job` takes `idea` and its help from new `START_JOB_HELP[role]` (today's text cut in three, each command's paragraph moved as it is); `write_note` and `write_report` keep the role's limits | `test_jobs_tools.py`: `test_role_tools_are_a_subset_of_todays`, `test_only_the_scout_sets_candidates`, `test_role_note_is_info_and_one_a_turn`, `test_role_writes_only_its_own_table_and_journal`, `test_each_command_is_explained_to_the_roles_that_may_run_it_and_no_other` |
| 5 | `operator/prompts.py`: `ROLE_RULES`, `SCOUT_SYSTEM`, `TESTER_SYSTEM`, `FEES_SYSTEM`, `ROLE_SYSTEM`, `role_message` (no wallet, no snipers: not a role's business); `_job_lines` names the role that started a job | new `test_prompts_roles.py`: `test_every_role_is_told_it_never_trades`, `test_every_role_names_the_landing_rule`, `test_tester_keeps_the_freeze_rules`, `test_role_message_leaves_out_wallet_and_snipers`, `test_job_line_names_the_role_that_started_it`, `test_no_model_name_in_a_prompt` |
| 6 | `operator/budget.py`: `TIERS` prices a role like the analyst; new `Ledger.spent_today_by`; `operator/budget_switch.py`: new `room_for_role`; `budget_line` names the shares | `test_budget.py`: `test_role_turn_is_priced_like_the_analyst`, `test_spent_today_by_role`; `test_budget_switch.py`: `test_role_stops_at_its_share`, `test_no_role_turn_inside_the_alert_reserve`, `test_budget_line_names_the_shares` |
| 7 | `operator/turn.py`: `run_turn(..., role)`: the role's instructions, tools and 5 $ stop; spend recorded under the role; `analyst.last` and the day's report untouched | `test_turn.py`: `test_role_turn_uses_its_instructions_and_tools`, `test_role_spend_is_recorded_under_its_role`, `test_role_turn_stops_at_the_role_cap`, `test_role_turn_leaves_the_days_analysis_alone` |
| 8 | `operator/roles.py`: new `role_due`, `run_role` (lock `operator/agent-<role>.lock`, state `operator/agents.json`), `on`; `cli.py`: `operate agent --role` | `test_roles.py`: `test_role_skips_when_nothing_is_new`, `test_role_runs_when_its_own_job_ended`, `test_role_runs_for_an_untested_idea`, `test_role_runs_when_handed_something`, `test_role_runs_after_three_quiet_hours`, `test_role_waits_after_two_turns_without_a_test`, `test_one_role_never_runs_twice_at_once`, `test_role_turn_never_takes_the_rounds_lock`, `test_role_over_its_share_skips_and_says_so_once_a_day` |
| 9 | the switch. `operator/standin.py`: three `STANDIN` rows after `research next`, `STAGE_LIMIT_SECS` of 26 minutes; `operator/gate.py`: `analyst_reason` gives no in-between analysis while `roles.on`; `decide` does not call the operator for a job a role started; `ops.py`: `holds_deploy` holds for a role's turn | `test_standin.py`: `test_role_turns_start_each_round_after_the_idle_research`, `test_role_turn_is_not_started_while_its_last_one_runs`; `test_gate.py`: `test_no_in_between_analysis_while_roles_are_on`, `test_daily_analysis_and_post_mortem_stay`, `test_a_roles_job_ending_does_not_call_the_operator`, `test_budget_every_zero_turns_the_roles_off`; `tests/test_ops.py`: `test_a_role_turn_holds_a_deploy` |
| 10 | words: `learning.digest_lines` names one next test a role and the week's three counts from new `roles.week_counts` (tests started, rules frozen, rules that passed forward); `docs/architecture.md`, `docs/autopilot.md`, `CLAUDE.md` | `test_learning.py`: `test_digest_names_one_next_test_a_role`; `test_roles.py`: `test_week_counts_tests_freezes_and_forward_passes` |
| 11 | later, after a day of numbers. `operator/tools.py`: `wait_job(id, secs)`, the one new tool: it waits up to 240 s for the role's own job and returns its result, so one turn can test, read and test again. `operator/research.py`: `run_next` does not count a role's lab as busy while a slot is free | `test_jobs_tools.py`: `test_wait_job_returns_the_result_when_the_job_ends`, `test_wait_job_gives_up_and_says_still_running`; `test_research.py`: `test_idle_research_still_starts_while_a_roles_lab_runs` |

**Untouched:** the round in `operator/run.py` (lock, the owner's commands, snapshot, risk guard, live test and
autotest, plain SOL, resources, digest, the operator turn); `book/`, `risk_guard.py`, `live_test.py`, `autotest.py`,
`commands.py`, `oktech.py`; qualify, discover, measure, the forward paper run, the idle research, the strategist;
the lab lock itself; the daily analysis and the post-mortem. `OWNER_COMMANDS` keeps `operate`, so no agent can
start another agent's turn or touch the budget.

## Risks, and the answer to each

- **Agents repeat each other's tests.** One queue a role, own command lists, every test names its idea, the same
  command refused for six hours whoever asks, and every turn's job list shows who started what.
- **More searches, more false winners.** The lower bound is at 80 % by the code's default (`[verdict]
  confidence`), so a rule with no edge passes a lab about one time in five. At 20 tests an hour with no edge in any
  of them, that is about 4 lucky lab winners an hour. So a lab result stays a ticket to the forward paper test and
  nothing more. Only the tester can freeze. One forward record a rule stays. The lab keeps judging a rule on the
  part of the tape it was not picked on (its `test` numbers). The forward test keeps counting only trades made after
  the freeze. New, in the tester's instructions, and stricter than the bar of 12 Nov (30 forward trades, total and
  lower bound above zero): a rule is written up for the owner after two forward readings that do not overlap (30
  trades, then 30 more), both above zero, with the capped bound and the top-5 share checked. Luck passes all three
  about one time in 125. The digest says the week's counts: tests started, rules frozen, rules that passed forward.
- **Cost runs away.** Shares a role, 5 $ a turn, the 75 $ reserve, the skip gate and the one-hour wait after two
  turns without a test. The worst day is 425 $ for the roles. `/budget every 0` stops them without a deploy.
- **Note spam to the owner.** A role's note is always "info" and one a turn, so nothing of theirs goes to Telegram.
  What the owner must decide is an idea marked for the owner, and the six-hourly digest carries it. Decision notes
  stay with the operator turn and the round's code.
- **A role starves the machine's own jobs.** One lab slot for the roles; their rows start after the idle research
  has looked.
- **Deploys find no quiet moment.** A role's turn holds a deploy, as the round does. Turns of one to three minutes
  starting on the quarter hour leave gaps. With step 11 turns grow to 20 minutes: that step needs a pause for a
  waiting deploy first. Not designed here.

## Open points, said plainly

- Every number in the two right-hand columns above is an estimate. The check after a day on the server: tests an
  hour, dollars a role, how often a role skipped, how many freezes, how long `qualify` waited for a slot.
- The tester's and the fee analyst's work meet in the lab. The flag rule (a fees lab needs a place or a bid) is a
  simple line between them and may need moving once real turns show where they overlap.
- A paper place that follows the bid does not exist yet (task 152). Until it does, `fees-bids` says "not measured"
  for most rows, and the tester can only score fixed places.
- Three agents ask more questions. They do not make a losing rule win. The two paper rules that showed a positive
  bound on 12 Nov both rest on their five best trades (the analyst's notes of 16:32 and 17:32 UTC).

## What was built, and what differs from this design (12 Nov, evening)

Steps 1 to 10 are built, each from a failing test. Step 11 (`wait_job`) is not. Nothing is deployed. No role has run
a turn against the real model: every turn in the tests is a scripted reply or a stand-in.

### The switch is off

The design had step 9 switch the roles on with the deploy. It does not. `[operator] role_agents` in `config.py` is
`False`, and `roles.on` reads it. The server's file holds no value for it, so a deploy changes nothing. With it off:
no role stage is started, the gate gives the analyst its turns between the daily ones, a job ending calls the
operator as before, the budget line and the digest name no agent, `operate agent` does nothing, and the analyst's
instructions and sixteen tools are byte for byte what they were (compared with the commit before, once, by script).
To switch on: `True` in `config.py` and in `tests/test_config_drift.py`, then a deploy. The owner's `/budget every 0`
stays the off switch without a deploy, as designed.

### A fourth role: `oktech`

The owner, 12 Nov: "an agent that masters oktech's features an how to apply them". Its question: which of OKTech's
features would make a paper-proven rule land better or sell better, and what is the cheapest test of each?

- It starts only commands that read: `landing`, `copy-context`, `playbook`, `launch-tools`. No lab, no freeze.
- It reads `docs/oktech-playbook.md`, `docs/oktech-features.md` and `docs/oktech-settings.md` with `read_file`
  (exactly these three paths, read-only, for this role alone), our fills, and the tester's and the fee analyst's tables.
- It writes `operator/oktech-facts.md`: one row a feature (what it does as far as known, verified or not and by
  which fill, what it would change for which rule, the cheapest test).
- It hands the tester ideas ("score this sell ladder on rule X"). A proposal for the owner is an idea of kind
  `live-test` that names the wallet, the size, the cap, the duration and the stop rule in words.
- It never trades and never creates, starts, stops or arms a sniper. It has no tool that could. The command `oktech`
  is refused to it as to every agent (`tests/operator/test_jobs_tools.py`,
  `test_the_oktech_role_starts_only_commands_that_read`).

Shares with four roles: scout 20 %, tester 35 %, fees 20 %, oktech 10 %, reserve 15 % (unchanged). At 500 $ a day:
100, 175, 100, 50 and 75 $. The worst day for the roles is still 425 $.

### What today's code changed in the design

- **The paper place that follows the bid exists** (task 152; the open point above is closed). The tester scores and
  freezes at `--copy-position bid` or `bid-` with `--buy-bid`. The fee analyst owns `place-bids` (with
  `--model-check`), `tips` and `landing`.
- **New commands, each with a role.** `gmgn`, `winners` and `history amm-backfill` are the scout's. `tips` and
  `landing` are the fee analyst's (`landing` is also the OKTech analyst's). `lab --recipe` and `paper freeze
  --recipe` are the tester's. `tests/operator/test_roles.py` (`test_every_research_command_has_a_role`) fails when a
  command is added to the agents' list without a role.
- **The size check** (`size.triggers[rule].line`, `crowd_free_mean`, `--same-sol`) is the tester's to read and repeat.
  Its instructions say so.

### Where the code is not the design's text

- **`START_JOB_HELP[role]` is `roles.job_help(text, role)`.** The analyst's `start_job` text stays one docstring in
  `tools.py`, untouched. `roles.JOB_HELP_CUTS` names the phrase each part begins with and the commands it explains.
  A role is told the parts for its commands. Two tests hold this: the parts put together are the text, word for
  word, and every part but one is in at least one role's help. The one part is the sentence that lists every
  command an agent may start ("Only lab, follow, ..."): a role is told its own list instead.
- **The fee analyst's help is long.** It may start a lab, so it is told every lab paragraph (15,000 characters of the
  23,300). A finer cut needs paragraphs tagged by role, not by command.
- **`operate idea --role` and `operate agent --role` share one flag.**
- **`run.py` changed by three lines.** The design left the round untouched. The round has to tell `start_standins`
  whether the agents are on, because only the round has the config. Off, the call is the one it was.
- **The role stages are not rows of `STANDIN`.** `due_standins(..., agents=...)` adds them behind `research next`
  when the round passes the roles. `STANDIN` itself is what it was.
- **A role's start moves its idea to "testing" in code** (`OperatorJobs.record_test`). The design left that to the
  agent. Without it the gate's "an idea nobody tested" would fire on an idea whose test runs.
- **A role cannot change another queue's idea at all**, not only not move or close it.
- **The 15-idea limit binds a role's turn only.** The analyst, the operator and the cloud session write as before.
  Nineteen research ideas were open on 12 Nov and all are the tester's until someone moves them, so the tester adds
  nothing until it has closed five. That is the design's "close or merge before it adds".
- **The scout's `place-bids` needs `--each`.** The design named `place-bids --each`; the code enforces it, as it does
  the fee analyst's lab flags.
- **A role's table that says the same is not written again**, so an unchanged table calls no other role's turn.
- **The tester's news is simpler than designed.** "A paper rule reached its next reading" is: a rule's count of
  paper trades passed another multiple of 30. It counts all of a rule's rows, not only those in its record.
- **Week counts.** "Rules frozen" counts rules whose forward test began in the last seven days. "Passed forward" is
  the rules that pass now (30 or more trades, total and lower bound above zero), read from the paper report's kept
  rows. The stricter bar of two readings that do not overlap is in the tester's instructions only; no code checks it.
- **`CLAUDE.md` and `docs/autopilot.md` are not updated** (the main session's). `docs/architecture.md` is.

### Open points that remain

- **Nothing here has met the real model.** Whether a role keeps to one test a turn, writes its table, and names its
  idea on every job is unknown until the first hour on the server.
- **Every number in the "What to expect" table is still an estimate**, and it was made for three roles.
- **Other roles' tables may call every role every round.** A role is told to keep its table current. Each change is
  news for the other three. The damper is the one-hour wait after two turns without a test. If dollars a role run
  away in the first hours, this rule is the first to look at.
- **The fee analyst and the tester still meet in the lab.** The flag rule is the line between them.
- **The oktech role and the fee analyst both read `landing`.** Who owns a finding about our own landings is not
  settled.
- **`gmgn fees` is the scout's.** The fee analyst would use it (what a wallet pays to land); today it asks the scout.
- **A backfill the scout started is not in the "jobs that ended" list** of any turn (that list shows research
  commands, and `history` is an action). The turn's reason still names the job's id.
- **The daily analysis does not see who started a job.** Its job list is unchanged.
- **Step 11** and the pause a waiting deploy needs before it.
- **Splitting today's open research ideas between the queues** (the list above) is the cloud session's, once.
