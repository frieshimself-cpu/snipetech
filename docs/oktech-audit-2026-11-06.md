# OKTech integration audit — 6 Nov 2026

What was checked: our side of the OKTech Sniper API (`src/snipetech/oktech.py`, `service.py`, the operator, the
book, the measure stage) against four sources of truth: OKTech's API page and the app bundle (`app.js`, the form
defaults and serialisers), the task documents the API returns for our snipers, every transaction the sniper wallet
`6VFrZ15…` made on 5–6 Nov (decoded from the chain), and the notes in `docs/live-recipe.md`. Six independent readers
found 93 issues (many duplicates); the ones that mattered were fixed the same afternoon. This file is the record:
what was wrong on our side, what is now known about OKTech, and what only OKTech's owner can answer.

## Fixed on our side (commits `0f0e17d`, `c4b3501`, `5e24154` and the landing fix after them)

| # | What was wrong | Why it mattered | Now |
|---|---|---|---|
| 1 | Only strategy snipers sent `seedAccount: false`; every copy/dev/replica/probe sniper used OKTech's seed account | Every fill was two transactions (buy into a seed account, sweep into the wallet), a failed duplicate sweep in 3 of 6, ~0.004 SOL of rent parked per fill, and the 5 Nov 6023 race (a stop-loss selling from an empty wallet while the sweep was in flight) | `execution_params()` sends `seedAccount false` + `globalSendCount 1` from every builder; a test checks each |
| 2 | Sells bid the arm's buy fee (`max(priority, tip)`, 0.02 SOL on the paid arm) | OKTech sends every sell 2–3 times on separate nonce accounts; the losers fail (pump error 6023) and still pay the priority fee, so a 0.02 sell bid costs 0.02 per twin | sells bid `[oktech] sell_fee_sol` = 0.002 on every arm |
| 3 | The measure stage could not tell the fee-test arms apart (`run_measure` never passed a sniper id → every fill `control`) | The whole fee test would have produced one arm | the arm is read from the fee paid on chain (nearest arm by priority fee, tip as tiebreaker); failed sends too |
| 4 | Leaders were taken from every active local sniper row, including the 4 Nov speed-test probe that copied the bot `6Z91…` | 2 of the 6 copy fills were credited to that bot (0 trades behind, +0.3 %) instead of Gfree (+18–33 %); the bot got a live-expectancy row | only `copy` snipers define leaders; stale live rows are pruned |
| 5 | `trades_behind` counted our own other fill (the second arm fires on the same leader buy) | the arm that goes second measures worse by construction | counts other wallets' buys only |
| 6 | Tips were recognised only to Jito's 8 accounts; OKTech tips Nozomi, Astralane, Helius Sender and a `FLaSH…` relay | every fill read tip 0, mean fee 0.0011 instead of 0.0028 SOL | relay tip accounts from the operators' docs; any other tip-sized transfer in one of *our* fills counts as a tip to an unknown relay; `bid_model` has `tipped_share`, `mean_behind`, `mean_overhead_sol` |
| 7 | Three definitions of "holds a coin": `/stop` counted 0.0006 USDC (and refused every stop, forever), the snapshot ignored anything under 1,000,000 tokens (a real 675,850-token position read as 0 held), the cycler had its own | the kill switch did not work and the gate was blind | `positions.py`: WSOL/stablecoins never count, dust by tape value (< 0.001 SOL), unpriced tokens listed apart; one `open_positions()` for `/stop`, the book, the snapshot, the guard, the cycler and `oktech holdings` |
| 8 | Every "is a sniper running" check compared to the literal `running`; OKTech also has `starting`, `stopping`, `failed` | a starting sniper was invisible to `/stop` and the gate; the book planned a second sniper for a wallet whose sniper was starting, and would recreate a failed one every 5 min | `running/starting/stopping` are active everywhere; the book keeps a stopping one, skips a failed one with a note |
| 9 | The book ignored the owner's `/stop` flag | a live book would have restarted idle snipers 5 min after `/stop` | `operator/STOP` is kill mode for the book (creates nothing, stops non-holders) |
| 10 | `oktech cycle` stopped every sniper at arm time and only then looked at the wallet | the exact action rule 1 forbids | it reads the wallet first and refuses while a coin is held |
| 11 | The guard said "stopped buying" when every stop call failed, died on one OKTech error, and its floor was on the SOL *balance* | the fee test's own buys (12 × 0.2 SOL from 2.9) would have breached a 1.2 SOL balance floor and stopped everything while coins were held | floor on equity (SOL + the tape value of the coins held) plus a 0.05 SOL reserve; failed stops reported by id; OKTech errors logged and watched through |
| 12 | A spent book sniper's replacement had the same `externalId` | OKTech refuses the duplicate; the book would loop | `PlannedSniper.salt` (the book passes a 5-minute generation) |
| 13 | `crowdBuys` was sent under mode `normal` (strategy `--via crowd`, replica); the replica was uncapped | the block gates only in OKTech's *shapes* mode (not granted): such a sniper buys every launch with no cap | `--via crowd` refused; replica capped; burst rules become notes |
| 14 | `oktech delete` on a task OKTech no longer has kept the local row; one failing id aborted a multi-id stop/start/delete | stale rows, half-done stops | NOT_FOUND retires the row; the loop continues and exits 1 |
| 15 | The measured landing override used the rank *inside the slot* and would have moved the qualify labs to +4 | paper results at a better landing than we get are not evidence (rule 2) | the override is p75 of buys behind the leader + 3 (the 6 Nov calibration) and can only move the labs further back |

Re-measured after the fixes (`measure backfill`, 5–6 Nov, 39 buys / 39 sells / 83 failed sends): mean 2.5 buys
behind the copied wallet, price gap +26 %, p75 position 4 in the slot, 0.0028 SOL of fees per buy (priority + tip),
0.0020 SOL more per buy in failed sends, 90 % of buys tipped; the copy fills belong to Gfree (3), `4vw54B…` (2) and
`CgaA9a…` (1), as the hand audit said.

Still open on our side (small, listed so nothing is lost): `computeUnits` per arm is plumbed but unset until the
measure stage has consumption per venue (`fills.cu_used`; PumpSwap buys need more than curve buys); the fee-claim
decoder needs a real `collect_creator_fee` transaction as a fixture; `expected.json` never expires; `/stop` is
polled with the operator (up to 15 minutes late); the guard's emergency stop strands by design (this repo never
sells); `remote update` still kills running jobs (the rule stands: check `remote jobs` first).

## What is now known about OKTech (from the bundle, the API and the chain)

- Snipers are always created **idle**; `"start": true` on the create, or `POST /tasks/{id}/start`, starts them (the
  key's "Allow starting" only permits that). We never send `start` on a create: capped snipers are armed by
  `oktech guard`.
- Task statuses: `idle`, `starting`, `running`, `stopping`, `finished`, `failed`. A stopped task reads `idle` with a
  `stoppedAt`. The documents carry no config, counters, errors or fills; `startedAt` is overwritten by each restart.
- `extraParams.globalSendCount` defaults to 1 in the form; an omitted field takes the form default. The 2–3 sell
  transactions per exit are therefore not that setting: each sell is signed on up to three nonce accounts
  (`9tiBPJRc…` tipped, `HpQ2ykpy…` and `4KsxV3Bz…` untipped); the tipped one lands, the others execute and fail with
  pump error 6023 and pay priority + base fee (1.56 twins per landed sell measured). Buys show no twins.
- Tips (`tipAmount`) go to staked-connection relays, never Jito on buys: Nozomi, Astralane, Helius Sender and
  `FLaSH…` accounts; one buy in three carried no tip at all.
- Seed account on (OKTech's default): buy into an ephemeral account + a sweep transaction 1–2 slots later. Off: the
  tokens are in the wallet's token account from the first slot (the 5 Nov hunt buys prove it).
- Compute-unit limit: 150,000 on every buy (form default `computeUnits`); our buys consumed 54–79k. Priority is
  ranked per CU by the leader and by the relays, so a tighter limit at the same SOL fee bids higher.
- `crowdBuys` is a *shapes*-mode block (an account grant). The API accepts blocks that do not apply to the chosen
  mode, and features the account is not approved for, with `201` and ignores them: the sniper degrades to "buy
  every launch" with no error. `GET /status` shows only create/start permissions, not the account's grants.
- `preconf: true` is accepted on every task type; OKTech says preconfs are gated per task type and off for the
  high-volume ones. The flag is sent; whether it changes anything is measured, not assumed.
- `maxMcapUsd` on a copy sniper is a minimum-out that fails on chain, not a skip filter: a buy above the cap costs a
  failed transaction's fee.
- Hunt: one buy per start (it never re-arms); the `hunt` block only applies with mode `hunt`; thresholds are read as
  sent (whole numbers are not required by the app), and `minTotalSol` counts the creator's own buy.
- Rate limits: 20 creates a minute; the account runs at most 20 tasks at once (the owner can ask for more).
- The one "task disappeared" (`6aed00a7…` NOT_FOUND on 6 Nov) was a mistyped id in this session, not drift: a
  reconciliation of the local table against `/tasks` showed every local id present.

## Questions for OKTech's owner (plain words; each one changes what we pay or measure)

1. **Sells go out two or three times.** Each time your bot sells for us, two or three different sell transactions
   reach the chain (signed on different nonce accounts). One works; the others fail with pump error 6023 and still
   cost us the priority fee. Buys do not do this. What sends the extra sells, does `extraParams.globalSendCount`
   control auto-sell at all, and can exits be sent once?
2. **One buy in three carries no tip.** Of our last six copy buys, four paid the 0.002 SOL tip to a relay (Nozomi,
   Astralane, Helius Sender, FLaSH) and two paid no tip at all. Is that by design (a path that needs no tip) or a
   dropped tip? Which relays do you send through, and can we choose?
3. **Compute units.** Our buys use 54–79k CU but every one is sent with a 150,000 limit, which dilutes the priority
   fee per CU. Does `extraParams.computeUnits` apply to ctbot buys, and is there a per-venue (curve vs PumpSwap)
   setting?
4. **Silent acceptance.** A config with a feature our account is not approved for, or a block that does not apply to
   the mode (`crowdBuys` under `normal`), is accepted with 201 and ignored, so the sniper quietly buys every launch.
   Please refuse the create or return a warning, and show the account's approvals (hunt, pre-execution, account
   filter, preconfs, shapes) in `GET /status`.
5. **Preconfs.** Which task types have preconfirmations on for this account right now? Is a ctbot copy one of them?
6. **Restarting a finished capped sniper.** If we `start` a task that has `finished` (its `totalSnipes` spent), does it
   buy again (counter reset) or finish at once?
7. **Task documents.** Could a task carry its config, its buy/sell counters and its last error? Today we have to
   read the chain to know what a sniper did.
8. **Limits.** We will run up to 6 copy snipers per wallet set plus probes; please raise the account to 40 running /
   100 total tasks.
9. **Nonce accounts after a stop.** When a sniper is stopped while a sell is in flight, is the in-flight sell still
   sent?
