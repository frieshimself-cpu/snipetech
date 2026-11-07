export const meta = {
  name: 'research-loop',
  description: 'One round of the continuous strategy research: read every earlier round, pick fresh angles, screen them with read-only SQL, run labs one at a time at the live landing, three skeptics on every positive, synthesize',
  phases: [
    { title: 'Map', detail: 'the record of earlier rounds and what the server agents learned' },
    { title: 'Angles', detail: 'fresh angles nobody has covered' },
    { title: 'Hypothesize', detail: 'one analyst per angle, read-only SQL screens, batches of 3' },
    { title: 'Lab', detail: 'one lab at a time on the server, at the live landing' },
    { title: 'Verify', detail: 'three skeptics on every positive lab result' },
    { title: 'Synthesize', detail: 'ranked findings, paper freezes, builds, lessons, next angles' },
  ],
}

const ROUND = (args && args.round) || 2
const SCRATCH = '/tmp/claude-0/-home-user/e4403109-fa5e-5b10-9649-38580af733e4/scratchpad/research-loop'

const CTX = `You are part of a research team looking for a pump.fun trading strategy that makes money WHERE OUR LIVE BUYS ACTUALLY LAND. This is round ${ROUND} of a continuous research loop. The owner's goal is a bot that makes money by itself; be rigorous and honest, never inflate a result.

REPO (read only: never edit, create or commit files in it): /home/user/snipetech. The record of earlier research rounds is docs/research/*.md (read all of it: every angle already covered, every result, every lesson, the guides to OKTech and to the lab). Also: docs/roadmap.md (top sections = current state), docs/live-recipe.md (live tests), docs/oktech-features.md and docs/oktech-settings.md, src/snipetech/analysis/lab.py, src/snipetech/analysis/paper.py, src/snipetech/sim/market.py, src/snipetech/oktech.py. Scratch files go under ${SCRATCH}/ (create it).

SERVER ACCESS: always first run: cd /home/user/snipetech && export SNIPETECH_OPS_URL=https://snipetech.45-77-66-186.sslip.io SNIPETECH_OPS_KEY=/tmp/claude-0/-home-user-snipetech/e4403109-fa5e-5b10-9649-38580af733e4/scratchpad/ops_key   (the key is a PATH; never print or read its contents)
- .venv/bin/snipetech remote sql "<one SELECT>"  : read-only SQLite on the live database; 30 s limit per query; at most 500 rows back; tab-separated output.
- .venv/bin/snipetech remote read <path> : a file under the server's data dir (operator/lessons.md, operator/ideas.json, operator/analyst-<date>.md, operator/notes.md, jobs/<id>.json, jobs/<id>.log).
- .venv/bin/snipetech remote ls <dir> ; .venv/bin/snipetech remote jobs ; .venv/bin/snipetech remote status
- NEVER run: remote update, remote kill, or remote run with oktech, book, sweep, operate, strategist, config, qualify, measure, discover, or anything that trades, moves SOL or changes settings. Start a lab ONLY if your stage says so. Never print or look for secrets.

SERVER LOAD RULES (the server records the live tape on 6 CPUs; never slow it): one SQL query at a time; at most 10 queries per agent; every query on trades must bound ts (about 100,000 rows an hour; indexes on ts, mint, user; launches indexed on ts, user, creator); join windows of 6 h or less, simple aggregates of 24 h or less; aggregate on the server and return few rows. If a query hits the 30 s limit, narrow it instead of retrying it.

TABLES: trades(sig, ev, slot, pos, ts, mint, venue, user, is_buy, quote [SOL lamports], base [token units], fee, fee_bps, q_after, b_after, r_base_after, ix); launches(mint, user, creator, name, symbol, uri, bonding_curve, slot, ts, sig, token_program, mayhem, holder_reward, quote_mint); completions(mint, slot, ts, sig); migrations(mint, pool, slot, ts, sig); launch_features(mint, params, computed_at, data); token_meta(mint, uri, fetched_at, ok, data); funders(wallet, funder, amount, ts, sig, checked_at); events(id, ts, slot, kind, mint, wallet, data, source, dedupe); labels(address, label, kind, source, added_at); verdicts(id, wallet, computed_at, landing_pos, n_tape, mean_sol, lower_sol, hist_days, hist_realized_usd, hist_win_rate, hist_trades, crowding, venue_share, score, status, next_review, reasons, copy_plan, gap3_pct, create_slot, forward, kept_armed; the last three JSON, landing_pos "+3x" = behind the 3rd buy after the leader's, counted across slots); paper_trades(strategy, mint, trigger_ts, entry_ts, exit_ts, pnl, roi, reason, data); tx_meta(sig, slot, fee_payer, priority, tip, data); coverage(key, data). On a pump.fun curve the price after a trade is q_after / b_after (virtual reserves); pos orders trades inside a slot. PumpSwap trades of freshly migrated coins exist only from 7 Nov 12:13 UTC (coverage key tape:amm_recent, a list of runs).

HARD FACTS (measured 5-7 Nov):
- Our launch buys (OKTech hunt) land in the slot after the trigger, about 3 trades in ("+3@1"); a copy of a wallet lands 2-4 trades behind the leader at +18-43 % price ("+6"). Only these landings count; better landings are not evidence.
- A live round trip costs about 0.009 SOL whatever the size; buys are 0.2 SOL.
- The lab: snipetech lab --trigger '<JSON>' --hours N --json replays one trigger at its live landing over the newest N hours with the exit grid, picks the exit on the first part (train_mean), reports the later part as test {n, mean, ev_lower, win_rate, total} per strategy row, plus landing and live_landing. About 4-10 minutes on 24 h once it holds the server's lab lock.
- The lab charges what a trade costs live and sells the way OKTech sells (since 7 Nov evening; round 1's labs did neither). Cost: the measured cost of a transaction, half the low bid's measured round trip (0.0047 SOL on 6 Nov); never pass --per-tx. Its output says what it charged: per_tx_sol, and per_tx_source "measured" (the low bid's last 7 days), "last measured" (the last cost measured, kept once a week passes without enough low-bid fills; per_tx_measured_at says when) or "estimate" (0.0045 while nothing has been measured); never the flat 0.0035 of round 1, 0.002 SOL a trade too kind (0.003 with a partial take profit). Exits: every exit of a launch, hunt, whale or burst rule waits OKTech's 1.3 s watch delay (watch1.3s in its name). Round 1's lab numbers were charged 0.0035 a transaction without the delay: they are 0.002 to 0.006 SOL a trade kinder than this lab's.
- Trigger language: launch {plain}; burst {buyers, sol, window, max_age, plain}; whale {sol, max_age, plain, solo}; kol {sol, group (kol | wallet | cand), max_age, plain}; momentum {buyers, sol, progress, holders, ret, plain}; hunt {sol, buyers, window, max_buyers, dev_max} = OKTech's launch gate exactly (sol and dev_max whole numbers; window in seconds with whole milliseconds). Bounds: sol 0-100, buyers 0-200, window 0-60, max_age 0-600, max_buyers 0-200, dev_max 0-100, progress 0-1, holders 0-10000, ret -1-10; buyers, holders, max_buyers whole; plain and solo true/false. Launch, hunt, whale and burst are scored at the hunt landing and never buy a coin that left its curve; kol and momentum at the copy landing.
- What OKTech can run live: hunt, copy (follow a wallet's buys), MIG (buy at the migration), and auto-sell exits; the bot can arm and disarm snipers on a schedule. A strategy OKTech cannot run is a build proposal, not a result.
- Everything in docs/research/*.md and docs/roadmap.md marked refuted stays refuted unless you bring a genuinely new angle and say what is new.`

const MAP_SCHEMA = { type: 'object', properties: { digest: { type: 'string' } }, required: ['digest'] }
const ANGLES_SCHEMA = {
  type: 'object',
  properties: {
    angles: {
      type: 'array',
      items: { type: 'object', properties: { key: { type: 'string' }, angle: { type: 'string' }, why_new: { type: 'string' } }, required: ['key', 'angle', 'why_new'] },
    },
  },
  required: ['angles'],
}
const HYP_SCHEMA = {
  type: 'object',
  properties: {
    lens: { type: 'string' },
    hypotheses: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' }, statement: { type: 'string' }, mechanism: { type: 'string' },
          sql_evidence: { type: 'string' },
          lab_trigger: { type: 'string', description: 'one lab trigger as a JSON object string, or empty' },
          lab_hours: { type: 'number' }, needs_code: { type: 'string' }, deployable: { type: 'string' },
          prior: { type: 'number', description: '0 to 1: honest chance of a positive lower bound in the lab at the real landing' },
          refute_if: { type: 'string' },
        },
        required: ['id', 'statement', 'mechanism', 'sql_evidence', 'lab_trigger', 'lab_hours', 'needs_code', 'deployable', 'prior', 'refute_if'],
      },
    },
    dead_ends: { type: 'string' },
  },
  required: ['lens', 'hypotheses', 'dead_ends'],
}
const LAB_SCHEMA = {
  type: 'object',
  properties: {
    id: { type: 'string' }, trigger: { type: 'string' }, hours: { type: 'number' }, job_id: { type: 'string' },
    ok: { type: 'boolean' }, n: { type: 'number' }, mean: { type: 'number' }, ev_lower: { type: 'number' },
    win_rate: { type: 'number' }, total: { type: 'number' }, train_mean: { type: 'number' }, per_hour: { type: 'number' },
    landing: { type: 'string' }, live_landing: { type: 'boolean' }, exit: { type: 'string' }, rows: { type: 'string' }, notes: { type: 'string' },
    per_tx_sol: { type: 'number', description: 'the SOL a transaction the lab charged (per_tx_sol at the top of its result)' },
    per_tx_source: { type: 'string', description: 'where that cost came from (per_tx_source): measured, last measured, estimate or flag; leave it out if the result has none' },
  },
  required: ['id', 'trigger', 'ok', 'n', 'mean', 'ev_lower', 'rows', 'notes'],
}
// A lab result counts at live cost only when the lab charged a measured cost of a transaction (per_tx_source
// "measured", or "last measured" once a week passes without enough low-bid fills), or at least the 0.0045 SOL a live
// transaction costs (round 1: about 0.009 a round trip; the lab's "estimate" while nothing is measured). One charged
// less (a lab at the flat 0.0035 of round 1) or that does not say (a server without the 7 Nov evening lab) is held to
// live cost first: 0.003 SOL a trade off its mean and lower bound (round 1: 0.002, 0.003 with a partial take profit).
const LIVE_TX_SOL = 0.0045
const LIVE_COST_GAP = 0.003
const atLiveCost = r => ['measured', 'last measured'].includes(r.per_tx_source) || (r.per_tx_sol || 0) >= LIVE_TX_SOL
const VERDICT_SCHEMA = {
  type: 'object',
  properties: { refuted: { type: 'boolean' }, confidence: { type: 'number' }, reasons: { type: 'string' }, numbers: { type: 'string' } },
  required: ['refuted', 'confidence', 'reasons', 'numbers'],
}
const SYNTH_SCHEMA = {
  type: 'object',
  properties: {
    report: { type: 'string', description: 'plain-words report: what was tested, what survived, what died and why, with numbers; lead with the outcome' },
    freezes: { type: 'array', items: { type: 'object', properties: { trigger: { type: 'string' }, why: { type: 'string' } }, required: ['trigger', 'why'] } },
    builds: { type: 'array', items: { type: 'object', properties: { what: { type: 'string' }, evidence: { type: 'string' }, deployable: { type: 'string' }, priority: { type: 'number' } }, required: ['what', 'evidence', 'deployable', 'priority'] } },
    lessons: { type: 'array', items: { type: 'string' } },
    next_angles: { type: 'array', items: { type: 'string' }, description: 'angles the next round should take, each with why' },
  },
  required: ['report', 'freezes', 'builds', 'lessons', 'next_angles'],
}

phase('Map')
const record = await agent(`${CTX}

YOUR JOB (map; at most 4 SQL queries, no labs): write a compact digest (at most 1,200 words) of the whole research record so far, so this round repeats nothing: read every file in docs/research/ (oldest first), the top three sections of docs/roadmap.md, and on the server operator/lessons.md, operator/ideas.json, the newest analyst report (remote ls operator) and the newest strategy search (remote sql "SELECT data FROM coverage WHERE key='research:search'"), and the newest paper report (the newest 'paper report' job in remote jobs, its jobs/<id>.log). List: angles already covered (with their best numbers), what survived and is in the paper test, open next angles the last round proposed, and how many hours of tape exist now (remote status: tape hours; coverage tape:amm_recent for PumpSwap runs).`, { label: `r${ROUND}:map`, phase: 'Map', schema: MAP_SCHEMA })

phase('Angles')
const ang = await agent(`${CTX}

THE RECORD (map stage):
${record ? record.digest : '(missing)'}

YOUR JOB: choose 6 research angles for round ${ROUND} that the record has NOT covered, or that the record's next angles ask for, each likely to find money at OUR landing with something OKTech can run (hunt settings beyond what was tried, copy targets, MIG once enough post-migration tape exists, exits, timing, coin types, creators, anything the data suggests). Give each a short key, a one-paragraph brief an analyst can act on (what to screen with SQL, what a lab trigger would look like), and why it is new. Prefer angles where a cheap SQL screen can kill or support them quickly.`, { label: `r${ROUND}:angles`, phase: 'Angles', schema: ANGLES_SCHEMA })
const ANGLES = (ang && ang.angles || []).slice(0, 6)
log(`round ${ROUND} angles: ${ANGLES.map(a => a.key).join(', ')}`)

phase('Hypothesize')
const lensOut = []
const BATCH = 3
for (let i = 0; i < ANGLES.length; i += BATCH) {
  const batch = ANGLES.slice(i, i + BATCH)
  const got = await parallel(batch.map(l => () => agent(`${CTX}

THE RECORD (map stage):
${record ? record.digest : '(missing)'}

YOUR ANGLE (${l.key}): ${l.angle}
Why it is new: ${l.why_new}

YOUR JOB (no labs; at most 10 SQL queries, one at a time): propose 2 to 4 hypotheses from your angle that could make money AT OUR LANDING and that OKTech can run (or say exactly what would have to be built). For each, run 1 to 3 cheap SQL screens that test a prediction it makes, and report the numbers. Where the lab can express it, give the exact lab trigger JSON (check it against the trigger language and bounds; hunt sol and dev_max are whole numbers) and the hours to test. Be strict: no gross edge after 0.009 SOL on 0.2 SOL in the screen means a low prior; anything already refuted in the record needs a genuinely new angle or is dropped. Ids start with "r${ROUND}-${l.key}-".`, { label: `r${ROUND}:lens:${l.key}`, phase: 'Hypothesize', schema: HYP_SCHEMA })))
  got.forEach((g, j) => { if (g) lensOut.push(g); else log(`lens ${batch[j].key} returned nothing`) })
}

const norm = s => { try { const o = JSON.parse(s); return JSON.stringify(Object.keys(o).sort().reduce((a, k) => (a[k] = o[k], a), {})) } catch (e) { return null } }
const seen = new Map()
for (const L of lensOut) for (const h of L.hypotheses || []) {
  const key = h.lab_trigger ? norm(h.lab_trigger) : null
  if (!key) continue
  const prev = seen.get(key)
  if (!prev || (h.prior || 0) > (prev.prior || 0)) seen.set(key, { ...h, lens: L.lens, key })
}
const ranked = [...seen.values()].sort((a, b) => (b.prior || 0) - (a.prior || 0))
const MAX_LABS = 10
const toLab = ranked.slice(0, MAX_LABS)
if (ranked.length > MAX_LABS) log(`lab cap: ${ranked.length - MAX_LABS} lower-prior lab hypotheses not run: ${ranked.slice(MAX_LABS).map(h => h.id).join(', ')}`)
log(`${toLab.length} hypotheses go to the lab`)

const labResults = []
const verdicts = []
for (const h of toLab) {
  phase('Lab')
  const hours = Math.max(12, Math.min(48, Math.round(h.lab_hours || 24)))
  const res = await agent(`${CTX}

YOUR JOB: run exactly ONE lab on the server for this hypothesis and report its result. Start nothing else.
Hypothesis ${h.id} (${h.lens}): ${h.statement}
Trigger JSON: ${h.lab_trigger}
Hours: ${hours}

Steps:
1. cd /home/user/snipetech and export the two variables (see SERVER ACCESS).
2. Start it: .venv/bin/snipetech remote run lab --trigger '<the trigger JSON exactly>' --hours ${hours} --json   It prints the job record; note its "id". If the server refuses the trigger (exit code 2 with a reason in the log), report ok=false with the reason; do not invent a different trigger.
3. Wait without a tight loop: run   timeout 540 bash -c 'until .venv/bin/snipetech remote read jobs/<id>.json | grep -q "\\"running\\": false"; do sleep 30; done'   and repeat that call until the job has ended (it may first wait for the server's lab lock; allow up to 90 minutes in all).
4. Read the result: .venv/bin/snipetech remote read jobs/<id>.log (JSON with "strategies": rows with exit, landing, train_mean, test {n, mean, ev_lower, win_rate, total}, per_hour, live_landing; at its top per_tx_sol and per_tx_source, the cost a transaction it charged and where it came from). If the job ended without a result because the service restarted (no JSON, exit code not 0, no refusal reason), start it once more; a second failure is ok=false with the log's last lines.
5. Report the best row (highest test ev_lower among rows with live_landing true) in the fields, every row in "rows", and per_tx_sol and per_tx_source as the result gives them (leave them out if it has none).`, { label: `r${ROUND}:lab:${h.id}`, phase: 'Lab', schema: LAB_SCHEMA, effort: 'low' })
  if (!res) { log(`lab ${h.id}: no result`); continue }
  res.hypothesis = h
  labResults.push(res)
  const gap = atLiveCost(res) ? 0 : LIVE_COST_GAP
  log(`lab ${h.id}: ok ${res.ok} n ${res.n} mean ${res.mean} ev_lower ${res.ev_lower} per_tx ${res.per_tx_sol ?? '?'} (${res.per_tx_source || 'not said'})${gap ? `, held to live cost: -${gap} a trade` : ''}`)
  const positive = res.ok && res.live_landing !== false && ((res.ev_lower - gap > 0 && res.n >= 30) || (res.mean - gap > 0 && res.n >= 100))
  if (!positive) continue

  phase('Verify')
  const brief = `${CTX}

THE CLAIM TO TEST: hypothesis ${h.id} (${h.lens}): ${h.statement}
Mechanism claimed: ${h.mechanism}
Lab result (${hours} h of newest tape): ${JSON.stringify({ trigger: res.trigger, n: res.n, mean: res.mean, ev_lower: res.ev_lower, win_rate: res.win_rate, total: res.total, train_mean: res.train_mean, landing: res.landing, exit: res.exit, per_hour: res.per_hour, per_tx_sol: res.per_tx_sol, per_tx_source: res.per_tx_source, job: res.job_id })}
All rows: ${res.rows}

You are a skeptic: try hard to REFUTE that this pays live at our landing. Default to refuted=true when the evidence is not clear. Report the numbers you found.`
  const [art, real] = await parallel([
    () => agent(`${brief}

YOUR LENS: artifact and outliers (SQL only, at most 8 queries, no labs). Is the result carried by a few trades, one hour, one coin family, create-slot bundles, coins that completed the curve within seconds, mayhem or holder-reward coins, or wash trading? Reconstruct the trigger's firing moments with SQL where you can (at least a sample) and check how concentrated the profit is; read the lab job's log (remote read jobs/${res.job_id || '<id>'}.log).`, { label: `r${ROUND}:verify:art:${h.id}`, phase: 'Verify', schema: VERDICT_SCHEMA }),
    () => agent(`${brief}

YOUR LENS: execution realism (repo and SQL only, at most 6 queries, no labs). Can OKTech run exactly this (docs/oktech-features.md, src/snipetech/oktech.py)? Is the landing the lab used the one live buys get for this kind? Would our buy land (other buyers in the same slot, failed sends), at 0.2 SOL with the slippage this curve size implies, and can OKTech's auto-sell run the chosen exit? Charge 0.009 SOL a round trip.`, { label: `r${ROUND}:verify:real:${h.id}`, phase: 'Verify', schema: VERDICT_SCHEMA }),
  ])
  const stab = await agent(`${brief}

YOUR LENS: stability. Run ONE more lab with the same trigger on a different window: --hours ${hours <= 24 ? 48 : 12} (same steps: remote run lab --trigger '<json>' --hours N --json, wait with timeout 540 bash -c 'until ... "running": false ...; do sleep 30; done' repeated, then remote read jobs/<id>.log). Compare its test mean and ev_lower and its train_mean with the first run; a result that flips sign or depends on one window is refuted. Start nothing else.`, { label: `r${ROUND}:verify:stab:${h.id}`, phase: 'Verify', schema: VERDICT_SCHEMA })
  const vs = [art, real, stab].filter(Boolean)
  const survives = vs.filter(v => !v.refuted).length >= 2
  verdicts.push({ id: h.id, trigger: res.trigger, survives, verdicts: vs })
  log(`verify ${h.id}: ${survives ? 'SURVIVES' : 'refuted'} (${vs.map(v => v.refuted ? 'R' : 'ok').join('/')})`)
}

phase('Synthesize')
const synth = await agent(`${CTX}

THE RECORD (map stage):
${record ? record.digest : '(missing)'}

YOUR JOB: synthesize round ${ROUND} into a ranked, honest report. No new server calls except reading job logs if a number is unclear.

ANGLES: ${JSON.stringify(ANGLES, null, 1)}
HYPOTHESES (all lenses, with their SQL screens and dead ends): ${JSON.stringify(lensOut, null, 1)}
LAB RESULTS: ${JSON.stringify(labResults.map(r => ({ id: r.id, trigger: r.trigger, hours: r.hours, ok: r.ok, n: r.n, mean: r.mean, ev_lower: r.ev_lower, win_rate: r.win_rate, total: r.total, train_mean: r.train_mean, landing: r.landing, exit: r.exit, per_tx_sol: r.per_tx_sol, per_tx_source: r.per_tx_source, at_live_cost: atLiveCost(r), rows: r.rows, notes: r.notes })), null, 1)}
SKEPTICS' VERDICTS: ${JSON.stringify(verdicts, null, 1)}

Write: (1) a plain-words report (lead with the outcome; what was tested, what survived, what died and why, with numbers); (2) freezes: only triggers that survived the skeptics; (3) builds: what would have to be built, with evidence and how OKTech would run it, priority 1 (highest) to 5; (4) lessons, one line each with numbers; (5) next angles for round ${ROUND + 1}, each with why.`, { label: `r${ROUND}:synthesize`, phase: 'Synthesize', schema: SYNTH_SCHEMA })

return { round: ROUND, angles: ANGLES, lenses: lensOut, labs: labResults.map(r => { const { hypothesis, ...rest } = r; return { ...rest, lens: hypothesis.lens, statement: hypothesis.statement } }), verdicts, synthesis: synth }
