export const meta = {
  name: 'video-analysis',
  description: 'Analyses videos in sequential batches: an Opus curator plans (and, with curation batches, writes the prompt of each batch), a Sonnet analyst reads frames and speech; a report per video and, with 2+ videos, a combined one',
  whenToUse: 'Only through the video-analysis skill, with the workflow_args printed by prepare.py',
  phases: [
    { title: 'Plan', detail: 'curator reads the objective and the transcript; writes the ledger and the prompt of batch 1', model: 'opus' },
    { title: 'Batches', detail: 'per video, in sequence: analyst (sonnet) of each batch; with curation batches, curator (opus) of the next batch' },
    { title: 'Report', detail: 'relatorio.md of each video', model: 'opus' },
    { title: 'Synthesis', detail: 'combined relatorio.md when there are 2+ videos', model: 'opus' },
  ],
}

// Workflow of the video-analysis skill. Input: workflow_args v1 printed by scripts/prepare.py (reference/contratos.md).
// Every exchange between agents goes through files; the agents return minimal objects validated by schema.
// The prompt texts are mirrored in reference/sem-workflow.md: change both together (tests/harness.mjs --dump-templates).

const LV = ['low', 'medium', 'high', 'xhigh', 'max']
const CUR = ['plan', 'batches']
// Final report (per video and combined). Never report.md, summary.md or findings.md: Claude Code blocks subagents from writing those names.
const FINAL_MD = 'relatorio.md'
let RAW = args
if (typeof RAW === 'string') { try { RAW = JSON.parse(RAW) } catch (e) { RAW = null } }
{
  const bad = []
  const str = x => typeof x === 'string' && x.trim() !== ''
  const opt = (x, ok) => x === undefined || x === null || ok(x)
  const posInt = x => Number.isInteger(x) && x > 0
  const bool = x => typeof x === 'boolean'
  if (!RAW || typeof RAW !== 'object' || Array.isArray(RAW)) bad.push('args')
  else {
    if (RAW.schema_version !== 1) bad.push('schema_version')
    for (const k of ['run_id', 'run_dir', 'objective_path', 'roles_dir', 'language']) if (!str(RAW[k])) bad.push(k)
    if (!opt(RAW.roles_mode, x => x === 'agents' || x === 'inline')) bad.push('roles_mode')
    if (!opt(RAW.combined_exists, bool)) bad.push('combined_exists')
    const o = RAW.options
    if (!opt(o, x => typeof x === 'object' && !Array.isArray(x))) bad.push('options')
    else if (o) {
      if (!opt(o.max_batches, x => Number.isInteger(x) && x >= 0)) bad.push('options.max_batches')
      if (!opt(o.max_parallel_videos, posInt)) bad.push('options.max_parallel_videos')
      for (const k of ['analyst_effort', 'curator_effort']) if (!opt(o[k], x => LV.includes(x))) bad.push(`options.${k}`)
      if (!opt(o.curation, x => CUR.includes(x))) bad.push('options.curation')
    }
    if (!Array.isArray(RAW.videos) || !RAW.videos.length) bad.push('videos')
    else {
      const slugs = new Set()
      RAW.videos.forEach((v, i) => {
        const at = `videos[${i}]`
        if (!v || typeof v !== 'object' || Array.isArray(v)) { bad.push(at); return }
        for (const k of ['slug', 'title', 'batches_dir', 'out_dir']) if (!str(v[k])) bad.push(`${at}.${k}${k === 'batches_dir' && v.cache_dir ? ' (cache_dir is the old name)' : ''}`)
        if (!Array.isArray(v.batch_images) || !v.batch_images.length || !v.batch_images.every(posInt)) bad.push(`${at}.batch_images${v.batch_frames ? ' (batch_frames is the old name)' : ''}`)
        if (!opt(v.start_batch, posInt)) bad.push(`${at}.start_batch`)
        for (const k of ['ledger_exists', 'start_prompt_exists', 'final_exists']) if (!opt(v[k], bool)) bad.push(`${at}.${k}`)
        if (str(v.slug)) { if (slugs.has(v.slug)) bad.push(`${at}.slug (duplicate)`); slugs.add(v.slug) }
      })
    }
  }
  if (bad.length) throw new Error(`invalid workflow_args (${bad.join(', ')}): run prepare.py and pass workflow_args as a JSON object, without editing`)
}
const fix = p => String(p).replace(/\\/g, '/').replace(/\/+$/, '')
const A = { ...RAW, run_dir: fix(RAW.run_dir), objective_path: fix(RAW.objective_path), roles_dir: fix(RAW.roles_dir),
  videos: RAW.videos.map(v => ({ ...v, batches_dir: fix(v.batches_dir), out_dir: fix(v.out_dir) })) }

const O = A.options || {}
const MODE = A.roles_mode === 'agents' ? 'agents' : 'inline'
const PAR = Math.max(1, Math.min(6, Number(O.max_parallel_videos) || 3))
const MAXB = Number(O.max_batches) > 0 ? Number(O.max_batches) : Infinity
const eff = (x, d) => (LV.includes(x) ? x : d)
const EFF = { init: 'high', next: eff(O.curator_effort, 'high'), final: 'high', cross: 'high', analyst: eff(O.analyst_effort, 'high') }
// Per-batch curation (NEXT) only with curation 'batches'; in the default 'plan', the INIT plans and the analysts of batches 2+ use the default contract.
const NEXT = O.curation === 'batches'
const ROLE = {
  curator: { type: 'video-analysis-curator', model: 'opus', file: `${A.roles_dir}/video-analysis-curator.md` },
  analyst: { type: 'video-analysis-analyst', model: 'sonnet', file: `${A.roles_dir}/video-analysis-analyst.md` },
}

const STR = { type: 'string' }, STRS = { type: 'array', items: { type: 'string' } }
const S_INIT = { type: 'object', required: ['status', 'ledger_path', 'prompt_path', 'video_type', 'focus'], properties: {
  status: { type: 'string', enum: ['ok', 'failed'] }, ledger_path: STR, prompt_path: STR,
  video_type: { type: 'string', enum: ['screen', 'meeting', 'slides', 'mixed', 'other'] },
  focus: { type: 'string', description: 'focus of batch 1, 1 sentence' }, warnings: STRS } }
const S_NEXT = { type: 'object', required: ['status', 'integrated_through', 'coverage', 'prompt_path', 'focus'], properties: {
  status: { type: 'string', enum: ['ok', 'failed'] },
  integrated_through: { type: 'integer', description: 'last batch whose report is integrated into the ledger' },
  coverage: { type: 'array', description: 'one item per report integrated in this call', items: { type: 'object', required: ['batch', 'state'],
    properties: { batch: { type: 'integer' }, state: { type: 'string', enum: ['complete', 'incomplete', 'missing'] } } } },
  prompt_path: STR, focus: { type: 'string', description: 'focus of the prepared batch, 1 sentence' },
  carried: { type: 'string', description: 'what was carried forward, ≤ 25 words' }, warnings: STRS } }
const S_ANALYST = { type: 'object', required: ['status', 'report_path', 'frames_seen', 'frames_missing', 'notable'], properties: {
  status: { type: 'string', enum: ['ok', 'partial', 'failed'] }, report_path: STR,
  frames_seen: { type: 'integer', description: 'images of the batch opened with Read, adding the previous attempt if this is the second' },
  frames_missing: { type: 'array', items: { type: 'string' }, description: 'ids of the frames of the batch whose image no attempt opened' },
  notable: { type: 'string', description: 'the most relevant point for the objective in this batch, ≤ 2 sentences, no sensitive data' }, warnings: STRS } }
const S_FINAL = { type: 'object', required: ['status', 'report_path', 'answered', 'summary'], properties: {
  status: { type: 'string', enum: ['ok', 'partial', 'failed'] }, report_path: STR,
  answered: { type: 'string', enum: ['yes', 'partly', 'no'] },
  summary: { type: 'string', description: 'direct answer to the objective, ≤ 120 words (on failure: the reason)' }, gaps: STRS } }

const pad = n => String(n).padStart(3, '0')
const tagOf = s => (s.length > 24 ? s.slice(0, 23) + '…' : s)
const norm = s => String(s || '').trim().replace(/\\/g, '/').replace(/\/{2,}/g, '/').toLowerCase()
const lastOf = v => Math.min(v.batch_images.length, MAXB)
const reasonOf = r => String((r && ((r.warnings || []).filter(Boolean).join('; ') || r.summary)) || '').slice(0, 160)
let calls = 0

function call(role, prompt, o) {
  const r = ROLE[role]
  const opts = { label: o.label, phase: o.phase, schema: o.schema, model: r.model, effort: o.effort }
  let text = prompt
  if (MODE === 'agents') opts.agentType = r.type
  else text = `Before any other action, use Read to read the file ${r.file}. It defines your role and your rules: follow it as your system instructions (ignore the block between --- at the top). If you cannot read it, return status "failed" with warnings ["unreadable role"].\n\n${prompt}`
  calls++
  return agent(text, opts)
}
// Up to 2 attempts; on failure, stores in fail.reason the reason the agent gave (warnings or, in FINAL/CROSS, summary).
async function twice(make, ok, fail = {}) {
  for (let k = 0; k < 2; k++) {
    const r = await make(k)
    if (r && ok(r)) return r
    if (r && reasonOf(r)) fail.reason = reasonOf(r)
  }
  return null
}
const because = why => (why.reason ? ` (${why.reason})` : '')
const AGAIN = '\n\nSECOND ATTEMPT: the previous one failed. If any output file already exists, read it before overwriting (Write requires it) and complete what is missing.'

function pathsOf(v) {
  const c = v.batches_dir, o = v.out_dir
  return {
    ledger: `${o}/ledger.md`, final: `${o}/${FINAL_MD}`, reports: `${o}/reports`,
    prompt: n => `${o}/prompts/batch_${pad(n)}.md`, report: n => `${o}/reports/batch_${pad(n)}.md`,
    timeline: n => `${c}/batch_${pad(n)}.md`, contact: n => `${c}/sheet_${pad(n)}.jpg`,
    overview: `${c}/overview.jpg`, summary: `${c}/summary.json`, transcript: `${c}/transcript.md`,
  }
}

const initPrompt = (v, P, B, last) => [
  'MODE: INIT',
  `Video: ${v.slug} · ${v.duration || '?'} · ${B} batches${last < B ? ` (this run analyses only batches 1–${last}; plan all of them and mark the others as outside this run)` : ''}`,
  `Objective (verbatim) and options: ${A.objective_path}`,
  `Summary of the video and the batches: ${P.summary}`,
  `Transcript: ${P.transcript}`,
  `Overview (if it exists): ${P.overview}`,
  `Batch 1: timeline ${P.timeline(1)} · contact sheet ${P.contact(1)} · ${v.batch_images[0]} images`,
  `Language of the reports: ${A.language}`,
  `Write: the ledger to ${P.ledger}; then the prompt of batch 1 to ${P.prompt(1)}.`,
].join('\n')

const stateText = p => p.state === 'ok' ? `ok, ${p.seen}/${p.expected} images`
  : p.state === 'partial' ? `PARTIAL, ${p.seen}/${p.expected} images` : 'FAILED, no reliable report'

function nextPrompt(v, P, n, last, pending, resumed) {
  const L = ['MODE: NEXT', `Video: ${v.slug} · prepare batch ${n}/${last}${n === last ? ' (last of this run)' : ''}`,
    `Objective: ${A.objective_path}`, `Ledger: ${P.ledger}`]
  if (pending.length) {
    L.push('Reports to integrate:')
    for (const p of pending) L.push(`- batch ${pad(p.n)}: ${stateText(p)} → ${P.report(p.n)}${p.state === 'failed' ? ` (speech of the segment: ${P.timeline(p.n)})` : ''}`)
  }
  if (n > 1 && (resumed || !pending.length)) L.push(`Check "Integrado até" in the ledger and integrate the existing reports in ${P.reports} up to batch ${pad(n - 1)}.`)
  L.push(`Next batch: timeline ${P.timeline(n)} · contact sheet ${P.contact(n)} · ${v.batch_images[n - 1]} images`,
    `Write: the updated ledger to ${P.ledger}; then the prompt to ${P.prompt(n)}.`)
  return L.join('\n')
}

function analystPrompt(v, P, n, last, curated) {
  const L = curated
    ? [`Batch ${n}/${last} of video ${v.slug}.`, `Curator prompt: ${P.prompt(n)} (read it first and follow it).`]
    : [`Batch ${n}/${last} of video ${v.slug}. There is no curator prompt for this batch: follow the default contract of your role.`,
       `Objective: ${A.objective_path}. Context: sections 4 and 5 of ${P.ledger}, if it exists.`]
  L.push(`Timeline: ${P.timeline(n)}`, `Report: write it to ${P.report(n)}`,
    `Images in the batch: ${v.batch_images[n - 1]} · Language of the report: ${A.language}`)
  return L.join('\n')
}
// `where`: path the 1st attempt returned when it is not the requested one ({where} in note 5b of reference/sem-workflow.md); empty = note 5.
function analystAgain(r, expected, path, where) {
  const did = r ? `opened ${r.frames_seen} of ${expected} images${(r.frames_missing || []).length ? ` (missing: ${r.frames_missing.join(', ')})` : ''}` : 'did not finish'
  return where
    ? `\n\nSECOND ATTEMPT. The previous one ${did}, but wrote the report to ${where}, not to the requested path. Write the complete report exactly to ${path}, copying the path character by character, and return that same path in report_path. First, read whatever exists at those two paths and keep what is right; open the images that were missing (when in doubt, all of them).`
    : `\n\nSECOND ATTEMPT. The previous one ${did}. If ${path} exists, read it before overwriting; open the images that were missing (when in doubt, all of them) and write the complete report.`
}

const seenText = res => res.from <= 1 ? `Images seen: ${res.seen} of ${res.expected}`
  : res.planned >= res.from ? `Images seen: ${res.seen} of ${res.expected} in batches ${pad(res.from)}–${pad(res.planned)} of this run; batches 001–${pad(res.from - 1)}: previous run (see the ledger)`
  : `Images seen: no new batch in this run; batches 001–${pad(res.planned)}: previous run (see the ledger)`

const finalPrompt = (v, P, B, last, res, pending) => [
  'MODE: FINAL',
  `Video: ${v.slug} · batches analysed: ${res.planned} of ${B}${res.planned < B ? ' (partial coverage: say so in the report)' : ''}`,
  `Objective: ${A.objective_path}`, `Ledger: ${P.ledger}`,
  `Batch reports: ${P.reports}/batch_001.md … batch_${pad(last)}.md`,
  `Not yet integrated into the ledger (in this run): ${pending.length ? pending.map(p => `${pad(p.n)} (${p.state})`).join(', ') : 'none'}; also check "Integrado até".`,
  `Partial: ${res.partial.join(', ') || 'none'} · Failed: ${res.failed.join(', ') || 'none'}`,
  seenText(res),
  `Transcript (for spot checks): ${P.transcript}`, `Language: ${A.language}`,
  `Write the final report to ${P.final}.`,
].join('\n')

const crossPrompt = reps => ['MODE: CROSS', `Run: ${A.run_id}`, `Objective: ${A.objective_path}`, 'Reports per video:',
  ...reps.map(r => `- ${r.slug}: ${r.report_path} (${r.status})`),
  `Language: ${A.language}`, `Write the combined report to ${A.run_dir}/${FINAL_MD}.`].join('\n')

async function analyze(v, P, n, last, curated, tag) {
  const expected = v.batch_images[n - 1], path = P.report(n)
  const base = analystPrompt(v, P, n, last, curated)
  // Wrong path: a response without status failed whose report_path is not the requested one; the curator would not find the report.
  const wrong = r => !!r && r.status !== 'failed' && norm(r.report_path) !== norm(path)
  const where = r => String(r.report_path || '').trim().replace(/\\/g, '/') || '?'
  const good = r => r && r.status !== 'failed' && !wrong(r) && r.frames_seen >= expected && !(r.frames_missing || []).length
  const opts = k => ({ label: `analysis · ${tag} ${n}/${last}${k ? ' · 2nd' : ''}`, phase: 'Batches', schema: S_ANALYST, effort: EFF.analyst })
  const out = (r, state, extra) => ({ n, state, expected, seen: r ? Math.min(r.frames_seen, expected) : 0, reason: '', wrong_path: null,
    notable: (r && r.notable) || '', ...extra })
  const r1 = await call('analyst', base, opts(0))
  if (good(r1)) return out(r1, 'ok')
  log(`${tag} ${n}/${last}: ${r1 ? `${r1.frames_seen}/${expected} images, ${r1.status}${wrong(r1) ? `, report at ${where(r1)}` : ''}` : 'no response from the analyst'}; retrying`)
  const r2 = await call('analyst', base + analystAgain(r1, expected, path, wrong(r1) ? where(r1) : ''), opts(1))
  if (good(r2)) return out(r2, 'ok')
  const best = [r2, r1].filter(r => r && r.status !== 'failed' && !wrong(r) && r.frames_seen > 0).sort((a, b) => b.frames_seen - a.frames_seen)[0]
  if (best) return out(best, 'partial')
  const bad = [r2, r1].find(wrong)
  return out(null, 'failed', bad ? { wrong_path: where(bad) } : { reason: reasonOf(r2) || reasonOf(r1) })
}

function demote(res, b, state) {
  const to = state === 'missing' ? 'failed' : 'partial'
  for (const k of ['ok', 'partial']) { const i = res[k].indexOf(b); if (i >= 0 && k !== to) { res[k].splice(i, 1); res[to].push(b) } }
}

async function runVideo(v) {
  const P = pathsOf(v), B = v.batch_images.length, last = lastOf(v), tag = tagOf(v.slug)
  const res = { slug: v.slug, title: v.title, status: 'ok', report_path: null, summary: null, answered: null,
    total: B, planned: last, from: 1, ok: [], partial: [], failed: [], seen: 0, expected: 0, warnings: [] }
  if (v.final_exists) { log(`${tag}: ${FINAL_MD} already exists; video skipped`); return { ...res, status: 'skipped', report_path: P.final } }
  // An unexpected error (e.g. an exhausted budget makes agent() throw) becomes a failure of the video, without losing the progress already counted.
  try { await steps(v, P, B, last, tag, res) }
  catch (e) { res.status = 'failed'; res.warnings.push(`error: ${(e && e.message) || e}`) }
  return res
}

async function steps(v, P, B, last, tag, res) {
  if (last < B) log(`${tag}: test mode, batches 1–${last} of ${B}`)
  const start = v.ledger_exists ? Math.max(1, Number(v.start_batch) || 1) : 1
  res.from = start
  let ready, pending = [], resumed = !!v.ledger_exists && start > 1, curFail = 0, anaFail = 0

  if (!v.ledger_exists) {
    const why = {}
    const r = await twice(k => call('curator', initPrompt(v, P, B, last) + (k ? AGAIN : ''),
      { label: `plan · ${tag}${k ? ' · 2nd' : ''}`, phase: 'Plan', schema: S_INIT, effort: EFF.init }), r => r.status === 'ok', why)
    if (!r) { res.status = 'failed'; res.warnings.push(`plan (INIT) failed twice${because(why)}`); return }
    ready = true
    log(`${tag}: plan ready (${r.video_type}); batch 1: ${r.focus}`)
  } else ready = !!v.start_prompt_exists

  for (let n = start; n <= last; n++) {
    if (budget.total && budget.remaining() < 80000) { res.warnings.push(`token budget running out: stopped before batch ${n}`); res.planned = n - 1; break }
    let curated = ready
    ready = false
    if (!curated && NEXT) {
      const why = {}
      const r = await twice(k => call('curator', nextPrompt(v, P, n, last, pending, resumed) + (k ? AGAIN : ''),
        { label: `curation · ${tag} ${n}/${last}${k ? ' · 2nd' : ''}`, phase: 'Batches', schema: S_NEXT, effort: EFF.next }), r => r.status === 'ok', why)
      if (r) {
        curated = true; pending = []; resumed = false; curFail = 0
        for (const c of r.coverage || []) if (c.state !== 'complete') { demote(res, c.batch, c.state); res.warnings.push(`batch ${c.batch}: report ${c.state === 'missing' ? 'missing' : 'incomplete'}`) }
        if (r.integrated_through < n - 1) res.warnings.push(`ledger integrated only up to batch ${r.integrated_through} when preparing batch ${n}`)
      } else {
        res.warnings.push(`curation of batch ${n} failed${because(why)}: contingency prompt`)
        if (++curFail >= 2) { res.status = 'failed'; res.warnings.push('curator failed in 2 batches in a row: video interrupted'); return }
      }
    }
    const a = await analyze(v, P, n, last, curated, tag)
    res[a.state].push(n); res.seen += a.seen; res.expected += a.expected
    if (a.wrong_path) res.warnings.push(`batch ${n}: report written to the wrong path (${a.wrong_path})`)
    else if (a.state === 'failed' && a.reason) res.warnings.push(`batch ${n}: analysis failed (${a.reason})`)
    pending.push(a)
    log(`${tag} ${n}/${last} ${a.state} ${a.seen}/${a.expected}${a.notable ? ' — ' + a.notable.slice(0, 140) : ''}`)
    if (a.state === 'failed') { if (++anaFail >= 2) { res.status = 'failed'; res.warnings.push('analyst failed in 2 batches in a row: video interrupted'); return } }
    else anaFail = 0
  }

  if (res.planned < 1) { res.status = 'failed'; res.warnings.push('no batch analysed: final report not generated'); return }
  const why = {}
  const fin = await twice(k => call('curator', finalPrompt(v, P, B, res.planned, res, pending) + (k ? AGAIN : ''),
    { label: `report · ${tag}${k ? ' · 2nd' : ''}`, phase: 'Report', schema: S_FINAL, effort: EFF.final }), r => r.status !== 'failed', why)
  if (!fin) { res.status = 'failed'; res.warnings.push(`final report failed twice${because(why)}: run /video-analysis retomar`); return }
  Object.assign(res, { report_path: fin.report_path || P.final, summary: fin.summary, answered: fin.answered })
  if (res.partial.length || res.failed.length || fin.status === 'partial' || res.planned < B) res.status = 'partial'
}

const remaining = v => (v.final_exists ? 0 : Math.max(0, lastOf(v) - (v.ledger_exists ? Math.max(1, Number(v.start_batch) || 1) : 1) + 1))
const planned = A.videos.reduce((s, v) => s + (v.final_exists ? 0 : NEXT ? 2 * remaining(v) + 1 : remaining(v) + (v.ledger_exists ? 1 : 2)), 0) + (A.videos.length > 1 ? 1 : 0)
log(`run ${A.run_id}: ${A.videos.length} video(s), ~${planned} agents, roles ${MODE}, curation ${NEXT ? 'per batch' : 'only in the plan'}, up to ${PAR} video(s) in parallel`)
if (planned > 800) throw new Error(`run too large (~${planned} agents; Workflow cap 1000): split into smaller runs or use max_batches/trecho`)

const queue = A.videos.map((v, i) => ({ v, i }))
const results = new Array(A.videos.length)
await parallel(Array.from({ length: Math.min(PAR, queue.length) }, () => async () => {
  while (queue.length) {
    const { v, i } = queue.shift()
    try { results[i] = await runVideo(v) }
    catch (e) { results[i] = { slug: v.slug, title: v.title, status: 'failed', warnings: [`error: ${(e && e.message) || e}`] } }
  }
}))
const R = results.map((r, i) => r || { slug: A.videos[i].slug, title: A.videos[i].title, status: 'failed', warnings: ['not run'] })

let combined = null
const reps = R.filter(r => r.report_path && r.status !== 'failed')
if (reps.length > 1 && !A.combined_exists) {
  const why = {}
  const c = await twice(k => call('curator', crossPrompt(reps) + (k ? AGAIN : ''),
    { label: `synthesis${k ? ' · 2nd' : ''}`, phase: 'Synthesis', schema: S_FINAL, effort: EFF.cross }), r => r.status !== 'failed', why)
  combined = c ? { report_path: c.report_path || `${A.run_dir}/${FINAL_MD}`, answered: c.answered, summary: c.summary } : { error: `synthesis failed twice${because(why)}` }
}
// No token count in the return: the budget's spent() adds up the whole session, not this run.
// Up to 6 warnings: with more, the first 4, the count of the omitted ones and the last one (in an interrupted video, the reason).
const capW = w => (w.length > 6 ? [...w.slice(0, 4), `+${w.length - 5} warnings omitted`, w[w.length - 1]] : w)
return {
  run_id: A.run_id, run_dir: A.run_dir, roles_mode: MODE, agents: calls,
  videos: R.map(r => ({ slug: r.slug, title: r.title || null, status: r.status, report_path: r.report_path || null, answered: r.answered || null, summary: r.summary || null,
    batches: r.total ? { total: r.total, planned: r.planned, from: r.from, ok: r.ok.length, partial: r.partial, failed: r.failed } : null,
    frames: r.expected ? `${r.seen}/${r.expected}` : null, warnings: capW(r.warnings || []) })),
  combined,
}
