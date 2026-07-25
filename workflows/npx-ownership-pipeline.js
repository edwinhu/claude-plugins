export const meta = {
  name: 'npx-ownership-pipeline',
  description: 'Coordinates the four data legs of the N-PX × ownership panel on the WRDS SGE grid — S12 mutual-fund holdings, S34/EDGAR institutional holdings, the year-parallel N-PX vote array, and the ISS→CRSP crosswalk that gates it — then verifies the on-grid merge emits one analysis-ready panel over one asserted item universe.',
  whenToUse: 'Run when building or rebuilding the meeting-level voting/ownership panel from skills/wrds/examples/voting_ownership_pipeline. Use it instead of invoking run_pipeline.sh and run_npx_pipeline.sh separately: those are two orchestrators that cannot see each other, and nothing in them guarantees the ownership leg and the N-PX leg were built over the same item universe.',
  phases: [
    { title: 'Preflight', detail: 'verify grid access, stage scripts, and assert the declared universe matches pipeline_config.sas on the grid' },
    { title: 'Legs', detail: 'S12 · S34+EDGAR · (crosswalk → N-PX array) in parallel, each with its own verify' },
    { title: 'Merge', detail: 'merge_panel.sas on the grid: universe assertion + join cells onto the panel' },
    { title: 'Gate', detail: 'compute overall pass/fail in JS from raw per-leg assertions' },
  ],
}

// ─────────────────────────────────────────────────────────────────────────────
// WHAT THIS WORKFLOW DOES AND DOES NOT DO
//
// It COORDINATES AND VERIFIES; it does not execute SAS. Every leg runs on the
// WRDS SGE grid, reached over ssh. The agents below submit with `qsub`, poll
// `qstat`, and then read logs and datasets to assert concrete row counts. No SAS
// runs inside this process, and no leg is believed because its job exited 0 —
// an SGE array can lose a task to a node eviction and still look clean (observed:
// 20 of 21 outputs, no error anywhere).
//
// Why a workflow and not a bash orchestrator: the dependency structure is real.
// Leg 4 (crosswalk) HARD-GATES leg 3 (N-PX array) — the array hash-merges the
// crosswalk, and without it every task opens a missing dataset and exits 0 with
// empty output. Legs 1, 2 and 4 are independent and start together. Legs 2a/2b
// are alternative sources for one quantity and need a coalesce decision, not a
// blend. And a leg that fails verification must stop its own branch without
// killing the others — which is parallel() over per-leg pipelines, not hold_jid.
// ─────────────────────────────────────────────────────────────────────────────

let cfg = args
if (typeof cfg === 'string') { try { cfg = JSON.parse(cfg) } catch { cfg = {} } }
cfg = cfg || {}

const SSH = cfg.wrdsSsh || 'wrds'
const REMOTE = cfg.wrdsProjectDir || '~/projects/pass'
const OUTLIB = cfg.outLib || '/scratch/nyu/eddyhu/npx'
const PARSE13F = cfg.parse13fDir || '/scratch/nyu/eddyhu/parse_13f'

// ── THE UNIVERSE ─────────────────────────────────────────────────────────────
// Declared ONCE, here. Preflight asserts that pipeline_config.sas on the grid
// says the same thing; if it does not, the run stops before any leg starts.
// This is the mechanism that makes divergence impossible by construction rather
// than detectable after the fact — this project widened its meeting-type filter
// once already, and under split config the ownership and N-PX legs would have
// silently been built over different item sets.
const UNIVERSE = {
  year1: cfg.year1 || 2005,
  year2: cfg.year2 || 2025,
  meetingTypes: cfg.meetingTypes || ['Annual', 'Special', 'Annual/Special', 'Proxy Contest', 'Proxy Contest (M&A)'],
  voteResults: cfg.voteResults || ['Pass', 'Fail'],
}
const UNIVERSE_TXT = `year1=${UNIVERSE.year1} year2=${UNIVERSE.year2} `
  + `meetingtypes=[${UNIVERSE.meetingTypes.join(', ')}] voteresults=[${UNIVERSE.voteResults.join(', ')}]`

// Reconciliation targets. null ⇒ report the number, do not gate on it.
const EXPECT = {
  npxVoteRows: cfg.expectNpxVoteRows ?? null,   // e.g. 144375860 for 2005-2025, no meetingtype filter
  minLinkedShare: cfg.minLinkedShare ?? 0.75,   // crosswalk: linked share of vote rows
}

const SKIP = new Set(cfg.skipLegs || [])        // e.g. ["s12"] to reuse a prior build

// ── Schemas ──────────────────────────────────────────────────────────────────
const ASSERTION = {
  type: 'object', additionalProperties: false,
  required: ['name', 'status', 'expected', 'actual'],
  properties: {
    name: { type: 'string' },
    status: { type: 'string', enum: ['PASS', 'FAIL', 'SKIP'] },
    expected: { type: 'string', description: 'what was required, as a concrete value or rule' },
    actual: { type: 'string', description: 'the observed value — a number, not a summary' },
  },
}

const PREFLIGHT_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['gridReachable', 'configOnGrid', 'universeMatches', 'scriptsPresent', 'missingScripts', 'crosswalkStaged', 'assertions', 'notes'],
  properties: {
    gridReachable: { type: 'boolean' },
    configOnGrid: { type: 'string', description: 'the year/meetingtype/voteresult values actually read from pipeline_config.sas' },
    universeMatches: { type: 'boolean', description: 'does pipeline_config.sas match the declared universe EXACTLY' },
    scriptsPresent: { type: 'array', items: { type: 'string' } },
    missingScripts: { type: 'array', items: { type: 'string' } },
    crosswalkStaged: { type: 'boolean', description: 'npx_link.csv present in the remote project dir' },
    assertions: { type: 'array', items: ASSERTION },
    notes: { type: 'string' },
  },
}

const LEG_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['legId', 'submitted', 'jobIds', 'allTasksCompleted', 'outputs', 'assertions', 'status', 'notes'],
  properties: {
    legId: { type: 'string', description: 'echo the dispatched leg id verbatim — the gate keys on it' },
    submitted: { type: 'boolean' },
    jobIds: { type: 'array', items: { type: 'string' } },
    allTasksCompleted: { type: 'boolean', description: 'EVERY expected output exists — not merely "the job exited 0"' },
    outputs: {
      type: 'array', items: {
        type: 'object', additionalProperties: false, required: ['name', 'rows'],
        properties: { name: { type: 'string' }, rows: { type: 'integer' }, detail: { type: 'string' } },
      },
    },
    assertions: { type: 'array', items: ASSERTION },
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    notes: { type: 'string' },
  },
}

const MERGE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['mergeRan', 'universeAssertionFired', 'orphanItems', 'meetingsItems', 'npxItems',
             'passRows', 'passNpxRows', 'passNpxItems', 'itemsWithNoNpx', 'voteRowsRepresented',
             'blockBreakdown', 'assertions', 'status', 'notes'],
  properties: {
    mergeRan: { type: 'boolean' },
    universeAssertionFired: { type: 'boolean', description: 'true if merge_panel ABORTED on a universe mismatch' },
    orphanItems: { type: 'integer', description: 'items in out.meetings absent from out.npx_items — MUST be 0' },
    meetingsItems: { type: 'integer' },
    npxItems: { type: 'integer' },
    passRows: { type: 'integer', description: 'out.pass — item grain' },
    passNpxRows: { type: 'integer', description: 'out.pass_npx — (item, block) grain' },
    passNpxItems: { type: 'integer', description: 'distinct itemonagendaid in out.pass_npx' },
    itemsWithNoNpx: { type: 'integer', description: 'panel items with no fund-level coverage (null block)' },
    voteRowsRepresented: { type: 'integer' },
    blockBreakdown: {
      type: 'array', items: {
        type: 'object', additionalProperties: false, required: ['block', 'cells', 'voteRows'],
        properties: { block: { type: 'string' }, cells: { type: 'integer' }, voteRows: { type: 'integer' } },
      },
    },
    assertions: { type: 'array', items: ASSERTION },
    status: { type: 'string', enum: ['PASS', 'FAIL'] },
    notes: { type: 'string' },
  },
}

// Shared preamble. Every leg agent gets the same universe text, so a leg that
// quietly uses a different window is a verifiable error rather than a surprise.
const CONTEXT = `
You are operating the WRDS SGE grid over ssh. Host alias: ${SSH}. Remote project dir: ${REMOTE}.
SAS output library \`out\` points at: ${OUTLIB}.

THE UNIVERSE (declared by the orchestrator; pipeline_config.sas on the grid MUST agree):
  ${UNIVERSE_TXT}

RULES:
- You COORDINATE the grid; you do not run SAS locally. Submit with qsub, poll with
  \`qstat -u $USER\`, then VERIFY by reading logs and datasets.
- NEVER conclude a leg succeeded because a job exited 0. An SGE array can lose a task to a
  node eviction and still report clean — verify every expected output EXISTS and has rows.
- Report RAW numbers in assertions (actual counts), not judgements. The orchestrator gates.
- Do NOT edit pipeline_config.sas, and do NOT pass a year range or meeting-type filter to
  any script. The universe comes from that one file. If a script needs a filter, it reads it.
- If something is genuinely blocked (no grid access, missing input), return status=FAIL with
  an assertion naming what is missing. Do not fabricate counts.
`

// ── Phase 1: Preflight ───────────────────────────────────────────────────────
phase('Preflight')
const pre = await agent(
  `${CONTEXT}
PREFLIGHT. Do not submit any job in this step.

1. Confirm the grid is reachable: \`ssh ${SSH} "hostname; whoami"\`.
2. Read ${REMOTE}/pipeline_config.sas and report the ACTUAL year1, year2, MEETINGTYPES and
   VOTERESULTS values it declares, verbatim, in configOnGrid.
3. Set universeMatches=true ONLY if those values match the declared universe above EXACTLY
   (same years, same meeting-type list, same vote-result list — order-insensitive, but no
   extra or missing entries). A mismatch means the ownership leg and the N-PX leg would be
   built over different item sets; that is the single failure this workflow exists to prevent.
4. Check these scripts exist in ${REMOTE}: pipeline_config.sas, build_meetings.sas,
   build_inst_own.sas, build_mflinks.sas, split_s12.sas, tfn_holdings_parallel.sas,
   stage_npx_link.sas, build_npx.sas, run_npx_array.sh, run_npx_stage.sh, run_sas.sh,
   merge_panel.sas. List found vs missing.
5. Check whether npx_link.csv (the ISS→CRSP crosswalk) is staged in ${REMOTE}. It is the
   input to stage_npx_link.sas and therefore gates the entire N-PX leg.
6. Confirm ${OUTLIB} exists and is writable, and that ~/.pgpass exists (the SAS scripts read
   WRDS PostgreSQL credentials from it).

Emit an assertion for each of: grid_reachable, universe_matches, scripts_present,
crosswalk_staged, outlib_writable. Return PREFLIGHT_SCHEMA.`,
  { label: 'preflight', phase: 'Preflight', schema: PREFLIGHT_SCHEMA }
)

if (!pre.gridReachable) throw new Error(`Preflight: WRDS grid not reachable via ssh ${SSH}. ${pre.notes || ''}`)
if (!pre.universeMatches) {
  throw new Error(
    `Preflight: UNIVERSE MISMATCH — refusing to start.\n`
    + `  declared: ${UNIVERSE_TXT}\n`
    + `  on grid : ${pre.configOnGrid}\n`
    + `Edit pipeline_config.sas (or the workflow args) so both agree. Running the legs over `
    + `different item universes is exactly the silent-divergence failure this gate prevents.`)
}
if ((pre.missingScripts || []).length) {
  throw new Error(`Preflight: missing script(s) in ${REMOTE}: ${pre.missingScripts.join(', ')}`)
}
log(`Preflight OK — universe agreed: ${UNIVERSE_TXT}`)
if (!pre.crosswalkStaged) log('WARNING: npx_link.csv not staged — the N-PX leg will fail its gate')

// ── Phase 2: the four legs ───────────────────────────────────────────────────
phase('Legs')

const legS12 = () => agent(
  `${CONTEXT}
LEG 1 — S12 MUTUAL-FUND HOLDINGS.

tfn.s12 is a 44 GB SAS file on NFS. Parallel jobs reading it directly contend badly
(~40 min each vs ~5 min solo), so the design is READ ONCE VIA POSTGRESQL, PARTITION TO
/scratch, THEN PARALLELISE ON THE PARTITIONS. Do not "optimise" that away.

1. Submit split_s12.sas:  qsub -N split_s12 -o logs/split_s12.log -j y run_sas.sh split_s12.sas
   (~15 min; PG read → year-range partitions out.s12_YYYY_YYYY on /scratch)
2. When it completes, submit tfn_holdings_parallel.sas once per year range with -hold_jid,
   using the YEAR_RANGES already listed in run_pipeline.sh (they are balanced by ROW COUNT,
   not year count — S12 went from ~4M rows/yr pre-2017 to ~20-26M/yr after).
3. Poll to completion.

VERIFY (assertions, with raw counts):
- s12_partitions_present: every year-range partition dataset exists and has >0 obs
- mf_own_outputs_present: one out.mf_own_* per submitted range, each >0 obs
- no_task_lost: number of mf_own_* datasets == number of ranges submitted
- log_clean: no "ERROR:" lines in logs/tfn_holdings-*.log
Set allTasksCompleted only if every expected output exists. legId="s12". Return LEG_SCHEMA.`,
  { label: 's12', phase: 'Legs', schema: LEG_SCHEMA })

const legInstOwn = () => agent(
  `${CONTEXT}
LEG 2 — INSTITUTIONAL OWNERSHIP. TWO SOURCES FOR ONE QUANTITY.

  2a  Thomson S34 (build_inst_own.sas) — decayed after 2013; systematically UNDERCOUNTS
      holdings because Thomson stopped ingesting many filers.
  2b  EDGAR 13F scrape (${PARSE13F}, the parse_13f_go binary + sge/submit_array.sh) — the
      reason that parser exists at all.

COALESCE RULE — THIS IS NOT A JUDGEMENT CALL:
  EDGAR WINS wherever a manager-quarter is present in the EDGAR scrape.
  S34 is FALLBACK ONLY, used exclusively where EDGAR has no coverage.
  NEVER BLEND, average, or sum the two for the same manager-quarter — that double-counts.
  Every output row MUST carry a provenance column recording which source it came from.

1. Submit build_inst_own.sas (qsub … run_sas.sh build_inst_own.sas), ~3 min.
2. Check whether the EDGAR 13F parse outputs already exist under ${PARSE13F}/out. If they do,
   use them. If not, report that in notes and proceed with S34 only — say so explicitly, and
   record edgar_coverage as the honest number, do NOT silently ship an S34-only panel as if
   it were complete.
3. Build the coalesced institutional-ownership dataset applying the rule above.

VERIFY (assertions, with raw counts):
- inst_own_present: out.inst_own exists, >0 obs
- provenance_column_present: the coalesced output has a per-row source column
- no_blended_rows: count of manager-quarters carrying BOTH sources == 0 (MUST be 0)
- edgar_precedence: for manager-quarters present in EDGAR, count where the retained row is
  S34 == 0
- edgar_coverage: share of manager-quarters sourced from EDGAR (report the number)
legId="inst_own". Return LEG_SCHEMA.`,
  { label: 'inst_own', phase: 'Legs', schema: LEG_SCHEMA })

// Legs 4 → 3. A pipeline, because the gate is REAL: build_npx.sas opens
// out.npx_link and out.npx_items as hash datasets. Without them each of the 21
// array tasks fails to load the hash and exits 0 having written nothing — the
// worst failure mode available, which is why leg 3 never starts on an unverified
// leg 4.
const legLinkThenNpx = () => pipeline(
  [{ id: 'npx' }],
  () => agent(
    `${CONTEXT}
LEG 4 — ISS→CRSP CROSSWALK (staging). This HARD-GATES the N-PX array.

The crosswalk is built LOCALLY (see skills/wrds/examples/voting_ownership_pipeline/npx_linking/
— fuzzy fund-name matching against CRSP MFDB) and pushed up as npx_link.csv. It is ~660 KB.
stage_npx_link.sas turns it into two hash inputs on /scratch:
    out.npx_link   fundid → block, tna_w   (~27K rows)
    out.npx_items  itemonagendaid          (the SHARED item frame, from pipeline_config.sas)

1. Confirm ${REMOTE}/npx_link.csv exists. If it does not, return status=FAIL immediately with
   an assertion naming it — do NOT proceed, and do NOT invent a crosswalk.
2. Submit: qsub -v "LINKCSV=${REMOTE}/npx_link.csv" -o logs/stage.sge.log -j y run_npx_stage.sh
3. Poll to completion, then read the LINKSTAT and ITEMSTAT lines from logs/stage_npx_link.log.

VERIFY (assertions, with raw counts):
- npx_link_rows: out.npx_link has >0 rows; report the count
- blocks_not_truncated: distinct block values are exactly the expected labels
  (index / passive / active / asset_owner) — a truncated SAS \$24 label silently MERGES blocks
- npx_items_rows: out.npx_items has >0 rows; report distinct_items and raw_rows from ITEMSTAT
- item_frame_deduped: ITEMSTAT fanout_rows == raw_rows - distinct_items and is small
  (vavoteresults is NOT unique on itemonagendaid — 'Pending' + final versioning pairs)
legId="crosswalk". Return LEG_SCHEMA.`,
    { label: 'crosswalk', phase: 'Legs', schema: LEG_SCHEMA }),

  (link) => {
    // THE HARD GATE. Throwing here drops this branch to null and skips leg 3,
    // while legs 1 and 2 continue untouched — the reason this is a pipeline
    // inside a parallel(), not one big sequential script.
    if (!link || link.status !== 'PASS' || !link.allTasksCompleted) {
      throw new Error(`crosswalk leg failed — N-PX array not submitted. ${link?.notes || 'no result'}`)
    }
    // Return BOTH legs. pipeline() yields only the LAST stage's value, so
    // without this the crosswalk leg's verification would vanish from the
    // report even though it gated the run.
    return agent(
      `${CONTEXT}
LEG 3 — N-PX YEAR-PARALLEL ARRAY. The crosswalk leg has PASSED, so the hashes exist.

build_npx.sas reads risk.voteanalysis_npx — 238,445,215 rows / 329 GB — hash-merges
out.npx_link and out.npx_items, and accumulates to (itemonagendaid, block) cells. One SGE
task per year; SGE_TASK_ID IS the year.

1. Submit: qsub -t ${UNIVERSE.year1}-${UNIVERSE.year2} -o logs/ -j y run_npx_array.sh
2. Poll to completion. Expect ~8-75s per task, but BUDGET FOR A STRAGGLER: one task was
   observed taking 742s against a 60s median, and the array still reported clean while
   producing 20 of 21 outputs.
3. Reconcile from the logs: \`grep -h NPXSTAT logs/build_npx_*.log\`

VERIFY (assertions, with raw counts):
- all_years_present: one out.npx_cells_YYYY per year in ${UNIVERSE.year1}-${UNIVERSE.year2}.
  Report the count. If any year is MISSING, re-run just that year
  (qsub -t YYYY-YYYY -o logs/ -j y run_npx_array.sh) before reporting.
- vote_rows_total: sum of NPXSTAT kept= across all years${EXPECT.npxVoteRows ? ` — MUST equal ${EXPECT.npxVoteRows}` : ' (report the number)'}
- scanned_exceeds_kept: NPXSTAT scanned > kept for every year. The date range is NOT the
  analysis universe: npx.meetingdate over 2005-2025 is 237,057,808 rows against 144,375,860
  in the item frame. scanned == kept would mean the item hash did not filter, and every
  block denominator would be inflated by ~64%.
- unlinked_small: NPXSTAT unlinked= summed across years is a negligible share of kept
  (crosswalk coverage). Report the number and the share.
- no_error_lines: no "ERROR:" in logs/build_npx_*.log
legId="npx_array". Return LEG_SCHEMA.`,
      { label: 'npx_array', phase: 'Legs', schema: LEG_SCHEMA }
    ).then(npx => [link, npx])
  }
)

const legTasks = []
const expectedLegIds = []
if (!SKIP.has('s12')) { legTasks.push(legS12); expectedLegIds.push('s12') }
if (!SKIP.has('inst_own')) { legTasks.push(legInstOwn); expectedLegIds.push('inst_own') }
if (!SKIP.has('npx')) { legTasks.push(legLinkThenNpx); expectedLegIds.push('crosswalk', 'npx_array') }

const legResults = await parallel(legTasks)
// parallel() nulls a thunk that threw; pipeline() returns an ARRAY (one entry per
// item) and nulls the ITEM whose stage threw — so a dead pipeline branch arrives
// as [null], which is truthy. Flatten first, then drop nulls, then reconcile
// against the expected leg ids: counting surviving thunks would miss it.
const legs = legResults.flat().filter(Boolean)
const legById = Object.fromEntries(legs.map(l => [l.legId, l]))
const failedLegs = legs.filter(l => l.status !== 'PASS').map(l => l.legId)
const missingLegs = expectedLegIds.filter(id => !legById[id])

log(`Legs complete: ${legs.map(l => `${l.legId}=${l.status}`).join(' · ')}`
  + (missingLegs.length ? ` · aborted before reporting: ${missingLegs.join(', ')}` : ''))

// ── Phase 3: Merge on the grid ───────────────────────────────────────────────
phase('Merge')

// The merge is the point of the exercise: it emits the joined panel from the
// grid. Running it when a leg failed would produce a confidently-wrong file.
const mergeReady = !failedLegs.length && !missingLegs.length
let merge = null

if (!mergeReady) {
  log(`Merge SKIPPED — unresolved leg(s): ${[...failedLegs, ...missingLegs].join(', ')}`)
} else {
  merge = await agent(
    `${CONTEXT}
MERGE. All legs verified. Run merge_panel.sas on the grid and verify its output.

merge_panel.sas does three things that matter here:
  1. stacks out.npx_cells_* and RE-AGGREGATES on (itemonagendaid, block). A few
     itemonagendaids carry rows in more than one meeting year (restated filings), so their
     cells arrive split across two task outputs; a plain concat would leave duplicate keys
     in a dataset whose stated grain is unique.
  2. ASSERTS ONE UNIVERSE — every item in out.meetings must exist in out.npx_items. On a
     mismatch it calls \`abort abend\` and the job FAILS. That is intended: the legs having
     been built over different universes is not a warning condition.
  3. joins the cells onto the panel, emitting out.pass_npx at (itemonagendaid, block) grain —
     the ANALYSIS-READY file. The whole point is to ship the joined result, not two files.

1. Submit: qsub -N merge -o logs/merge_panel.log -j y run_sas.sh merge_panel.sas
2. Poll to completion. A NON-ZERO EXIT MAY BE THE UNIVERSE ASSERTION FIRING — read the log
   before concluding anything. If you see "UNIVERSE MISMATCH", set universeAssertionFired=true,
   report orphanItems from the log, and return status=FAIL. Do NOT retry it, and do NOT
   weaken the assertion.
3. On success, read the UNIVERSE note line (meetings_items, npx_items, orphans) and the two
   PROC SQL summaries at the end.

VERIFY (assertions, with raw counts):
- universe_agreed: orphanItems == 0 (MUST be 0)
- pass_npx_exists: out.pass_npx exists with >0 rows
- pass_npx_grain_unique: (itemonagendaid, block) is unique in out.pass_npx — verify it, do
  not assume it
- every_panel_item_retained: distinct itemonagendaid in out.pass_npx == distinct
  itemonagendaid in out.pass (the join is a LEFT join; no panel item may be dropped)
- npx_coverage: itemsWithNoNpx = panel items with a null block (no fund disclosed a vote).
  Report the number — it is expected to be non-zero, and is a coverage statistic, not a bug.
- blocks_expected: block values are index / passive / active / asset_owner (plus null)
Return MERGE_SCHEMA.`,
    { label: 'merge_panel', phase: 'Merge', schema: MERGE_SCHEMA })
}

// ── Phase 4: Gate (pure JS — classify from raw assertions, never an agent grade) ──
phase('Gate')

const allAssertions = [
  ...(pre.assertions || []).map(a => ({ leg: 'preflight', ...a })),
  ...legs.flatMap(l => (l.assertions || []).map(a => ({ leg: l.legId, ...a }))),
  ...((merge?.assertions) || []).map(a => ({ leg: 'merge', ...a })),
]
const failed = allAssertions.filter(a => a.status === 'FAIL')

// Hard gates — these are not opinions, they are the invariants the panel rests on.
const hardFailures = []
if (!pre.universeMatches) hardFailures.push('declared universe != pipeline_config.sas')
if (merge && merge.orphanItems !== 0) hardFailures.push(`universe mismatch: ${merge.orphanItems} orphan item(s)`)
if (merge && merge.universeAssertionFired) hardFailures.push('merge_panel aborted on the universe assertion')
if (failedLegs.length) hardFailures.push(`leg(s) failed verification: ${failedLegs.join(', ')}`)
if (missingLegs.length) hardFailures.push(`leg(s) aborted before reporting: ${missingLegs.join(', ')}`)
if (!merge) hardFailures.push('merge did not run')
if (merge && merge.status !== 'PASS') hardFailures.push('merge verification failed')
if (merge && merge.passNpxItems !== merge.passRows && merge.passRows > 0 && merge.passNpxItems > 0) {
  // out.pass is item-grained, so its row count IS its distinct item count.
  hardFailures.push(`panel items dropped by the join: out.pass=${merge.passRows} vs out.pass_npx distinct items=${merge.passNpxItems}`)
}
if (EXPECT.npxVoteRows != null) {
  const npx = legById['npx_array']
  const got = npx?.outputs?.find(o => /vote.?rows|kept/i.test(o.name))?.rows
  if (got != null && got !== EXPECT.npxVoteRows) {
    hardFailures.push(`N-PX vote rows ${got} != expected ${EXPECT.npxVoteRows}`)
  }
}

const overallPass = hardFailures.length === 0

const legTable = [
  '| Leg | Source | Status | Key output | Rows |',
  '|-----|--------|--------|-----------|------|',
  ...legs.map(l => {
    const o = (l.outputs || [])[0]
    return `| ${l.legId} | ${l.jobIds?.length ? l.jobIds.join(', ') : '—'} | ${l.status === 'PASS' ? '✅' : '❌'} | ${o?.name || '—'} | ${o?.rows != null ? o.rows.toLocaleString() : '—'} |`
  }),
].join('\n')

const assertionTable = [
  '| Leg | Assertion | Status | Expected | Actual |',
  '|-----|-----------|--------|----------|--------|',
  ...allAssertions.map(a => `| ${a.leg} | ${a.name} | ${a.status === 'PASS' ? '✅' : a.status === 'SKIP' ? '—' : '❌'} | ${a.expected} | ${a.actual} |`),
].join('\n')

log(overallPass
  ? `✅ panel built — out.pass_npx: ${merge.passNpxRows?.toLocaleString()} rows at (itemonagendaid, block) over ${merge.passNpxItems?.toLocaleString()} items`
  : `❌ pipeline failed — ${hardFailures.length} hard failure(s): ${hardFailures.join(' · ')}`)

return {
  overallPass,
  status: overallPass ? 'panel_built' : 'failed',
  universe: { ...UNIVERSE, declared: UNIVERSE_TXT, onGrid: pre.configOnGrid, agreed: pre.universeMatches },
  hardFailures,
  legs: legs.map(l => ({ legId: l.legId, status: l.status, jobIds: l.jobIds, outputs: l.outputs, notes: l.notes })),
  merge: merge && {
    passRows: merge.passRows,
    passNpxRows: merge.passNpxRows,
    passNpxItems: merge.passNpxItems,
    itemsWithNoNpx: merge.itemsWithNoNpx,
    voteRowsRepresented: merge.voteRowsRepresented,
    blockBreakdown: merge.blockBreakdown,
  },
  // out.pass_npx is the deliverable: item-level ownership + per-block direction cells,
  // joined on the grid. Grain = (itemonagendaid, block).
  analysisReadyOutput: overallPass ? `${OUTLIB}/pass_npx.sas7bdat` : null,
  legTable,
  assertionTable,
  failedAssertions: failed,
}
