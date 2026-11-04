#!/usr/bin/env node
// Tests of workflow/video-analysis.js without the Workflow tool (Node 24, no dependencies).
//   node tests/harness.mjs                          runs all the scenarios; exit 0 only if all of them pass
//   node tests/harness.mjs --dump-templates <md>    writes an example of each prompt template, to compare
//                                                   with reference/sem-workflow.md
// Mocks: agent() driven by a table of responses per scenario; parallel = Promise.all with failure → null;
// simulated budget; Date.now, Math.random and new Date() without an argument throw an error while the script runs.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SCRIPT = path.resolve(HERE, '..', 'workflow', 'video-analysis.js')
const DATA = path.join(HERE, 'data', 'workflow')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const PHASES = ['Plan', 'Batches', 'Report', 'Synthesis']
const PHASE_OF = { INIT: 'Plan', NEXT: 'Batches', A: 'Batches', FINAL: 'Report', CROSS: 'Synthesis' }
const KEY_OF = { INIT: 'video_type', NEXT: 'integrated_through', A: 'frames_seen', FINAL: 'answered', CROSS: 'answered' }
const PREAMBLE_HEAD = 'Before any other action, use Read to read the file '
const preamble = file => `${PREAMBLE_HEAD}${file}. It defines your role and your rules: follow it as your system instructions (ignore the block between --- at the top). If you cannot read it, return status "failed" with warnings ["unreadable role"].\n\n`
const AGAIN = '\n\nSECOND ATTEMPT: the previous one failed. If any output file already exists, read it before overwriting (Write requires it) and complete what is missing.'
const pad = n => String(n).padStart(3, '0')
const clone = o => JSON.parse(JSON.stringify(o))
const sum = a => a.reduce((s, x) => s + x, 0)
const fix = p => String(p).replace(/\\/g, '/').replace(/\/+$/, '')
const has = (s, sub) => typeof s === 'string' && s.includes(sub)
const fixture = name => JSON.parse(readFileSync(path.join(DATA, `args_${name}.json`), 'utf8'))

// ---------------------------------------------------------------- static analysis
const SRC = readFileSync(SCRIPT, 'utf8')

function balanced(s, open) { // cuts out the {...} literal that starts at `open`, respecting strings
  let depth = 0, q = null
  for (let i = open; i < s.length; i++) {
    const c = s[i]
    if (q) { if (c === '\\') i++; else if (c === q) q = null; continue }
    if (c === "'" || c === '"' || c === '`') q = c
    else if (c === '{' || c === '[') depth++
    else if ((c === '}' || c === ']') && --depth === 0) return s.slice(open, i + 1)
  }
  return null
}

function metaProblems(lit) { // without the strings, only keys, {}, [], commas, colons, numbers and booleans may remain
  const probs = []
  let rest = '', q = null
  for (let i = 0; i < lit.length; i++) {
    const c = lit[i]
    if (q) { if (c === '\\') i++; else if (c === q) { q = null; rest += '§' } continue }
    if (c === '`') { probs.push('template literal'); continue }
    if (c === "'" || c === '"') { q = c; continue }
    if (c === '/' && (lit[i + 1] === '/' || lit[i + 1] === '*')) { probs.push('comment inside meta'); break }
    rest += c
  }
  const toks = rest.match(/[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|[{}[\],:§]|\S/g) || []
  toks.forEach((t, i) => {
    if (/^[A-Za-z_$]/.test(t)) { if (!['true', 'false', 'null'].includes(t) && toks[i + 1] !== ':') probs.push(`identifier outside a key: ${t}`) }
    else if (!/^(?:\d+(?:\.\d+)?|[{}[\],:§])$/.test(t)) probs.push(`non-literal symbol: ${t}`)
  })
  return probs
}

const FORBIDDEN = [
  [/\bDate\.now\b/, 'Date.now'], [/\bMath\.random\b/, 'Math.random'], [/\bnew\s+Date\s*\(\s*\)/, 'new Date()'],
  [/\brequire\s*\(/, 'require('], [/^\s*import[\s{*]/m, 'import'], [/\bimport\s*\(/, 'import()'],
  [/\bprocess\./, 'process.'], [/\bfs\./, 'fs.'], [/\bglobalThis\b/, 'globalThis'], [/\beval\s*\(/, 'eval('],
  [/\bnew\s+Function\b/, 'new Function'], [/\bset(?:Timeout|Interval|Immediate)\s*\(/, 'timers'], [/\bfetch\s*\(/, 'fetch('],
  [/\bconsole\./, 'console.'], [/\bbudget\.spent\b/, 'budget.spent (adds up the whole session, not the run)'],
]
// Claude Code blocks subagents from writing files with these names ("Subagents should return findings as text…").
const BLOCKED_NAMES = /(?:^|[\/\\\s`])(?:report|summary|findings)\.md\b/i

let COMPILED = null, COMPILE_ERROR = null
try {
  COMPILED = new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'log', 'phase', 'budget', 'workflow',
    SRC.replace(/^export const meta =/, 'const meta ='))
} catch (e) { COMPILE_ERROR = e }

// ---------------------------------------------------------------- clock and randomness guard
const RealDate = globalThis.Date, realNow = RealDate.now, realRandom = Math.random
const deny = what => () => { throw new Error(`${what} is forbidden in the Workflow`) }
const GuardDate = new Proxy(RealDate, {
  construct(t, a, nt) { if (!a.length) throw new Error('new Date() without an argument is forbidden in the Workflow'); return Reflect.construct(t, a, nt) },
  apply: deny('Date()'),
})
function guardOn() { RealDate.now = deny('Date.now()'); Math.random = deny('Math.random()'); globalThis.Date = GuardDate }
function guardOff() { RealDate.now = realNow; Math.random = realRandom; globalThis.Date = RealDate }

// ---------------------------------------------------------------- mini JSON Schema validation (the subset in use)
function schemaShape(s, where, errs) {
  if (!s || s.type !== 'object' || !s.properties || typeof s.properties !== 'object') { errs.push(`${where}: schema without {type:'object', properties}`); return }
  for (const k of s.required || []) if (!(k in s.properties)) errs.push(`${where}: required "${k}" outside properties`)
  for (const [k, p] of Object.entries(s.properties)) {
    if (p.type === 'object') schemaShape(p, `${where}.${k}`, errs)
    if (p.type === 'array' && p.items && p.items.type === 'object') schemaShape(p.items, `${where}.${k}[]`, errs)
  }
}
function conforms(val, s, where, errs) {
  if (s.type === 'object') {
    if (!val || typeof val !== 'object' || Array.isArray(val)) { errs.push(`${where}: not an object`); return }
    for (const k of s.required || []) if (!(k in val)) errs.push(`${where}: missing ${k}`)
    for (const [k, v] of Object.entries(val)) { if (!s.properties[k]) errs.push(`${where}: field outside the schema: ${k}`); else conforms(v, s.properties[k], `${where}.${k}`, errs) }
  } else if (s.type === 'array') {
    if (!Array.isArray(val)) { errs.push(`${where}: not a list`); return }
    if (s.items) val.forEach((x, i) => conforms(x, s.items, `${where}[${i}]`, errs))
  } else if (s.type === 'string' && typeof val !== 'string') errs.push(`${where}: not text`)
  else if (s.type === 'integer' && !Number.isInteger(val)) errs.push(`${where}: not an integer`)
  if (s.enum && !s.enum.includes(val)) errs.push(`${where}: ${JSON.stringify(val)} not in ${s.enum.join('|')}`)
}

// ---------------------------------------------------------------- execution with mocks
function classify(prompt) {
  const m = /^MODE: (INIT|NEXT|FINAL|CROSS)$/m.exec(prompt)
  const kind = m ? m[1] : 'A'
  let slug = null, n = null, last = null, x
  if (kind === 'A') { if ((x = /^Batch (\d+)\/(\d+) of video ([^\s.]+)\./m.exec(prompt))) { n = +x[1]; last = +x[2]; slug = x[3] } }
  else if (kind !== 'CROSS' && (x = /^Video: (\S+) · /m.exec(prompt))) slug = x[1]
  if (kind === 'NEXT' && (x = /prepare batch (\d+)\/(\d+)/.exec(prompt))) { n = +x[1]; last = +x[2] }
  const base = kind === 'A' ? `A${n}` : kind === 'NEXT' ? `N${n}` : kind
  return { kind, slug, n, last, base, contingency: kind === 'A' && prompt.includes('There is no curator prompt'), retry: prompt.includes('SECOND ATTEMPT') }
}

function defaults(c, ctx) { // valid success response for each mode
  const out = c.video ? fix(c.video.out_dir) : '?'
  switch (c.kind) {
    case 'INIT': return { status: 'ok', ledger_path: `${out}/ledger.md`, prompt_path: `${out}/prompts/batch_001.md`, video_type: 'screen', focus: 'foco do lote 1' }
    case 'NEXT': {
      const integ = [...c.prompt.matchAll(/^- batch (\d{3}):/gm)].map(m => +m[1])
      return { status: 'ok', integrated_through: c.n - 1, coverage: integ.map(b => ({ batch: b, state: 'complete' })),
        prompt_path: `${out}/prompts/batch_${pad(c.n)}.md`, focus: `foco do lote ${c.n}`, carried: 'contexto passado adiante' }
    }
    case 'A': return { status: 'ok', report_path: `${out}/reports/batch_${pad(c.n)}.md`, frames_seen: c.video.batch_images[c.n - 1], frames_missing: [], notable: `achado do lote ${c.n}` }
    case 'FINAL': return { status: 'ok', report_path: `${out}/relatorio.md`, answered: 'yes', summary: `resumo de ${c.slug}` }
    case 'CROSS': return { status: 'ok', report_path: `${fix(ctx.args.run_dir)}/relatorio.md`, answered: 'yes', summary: 'resumo combinado' }
  }
}

async function run(args, sc = {}) {
  let A0
  try { A0 = typeof args === 'string' ? JSON.parse(args) : args } catch (e) { A0 = null }
  const vids = new Map(((A0 && Array.isArray(A0.videos) && A0.videos) || []).filter(v => v && v.slug).map(v => [v.slug, v]))
  const ctx = { args: A0, calls: [], logs: [], errors: [], phases: [], active: 0, maxActive: 0, result: undefined, error: null }
  const tries = new Map()
  const agent = async (prompt, opts = {}) => {
    const info = classify(String(prompt))
    const key = `${info.slug}|${info.base}`
    const attempt = tries.get(key) || 0
    tries.set(key, attempt + 1)
    const c = { idx: ctx.calls.length, prompt, opts: { ...opts }, ...info, attempt, video: vids.get(info.slug) }
    c.id = `${info.slug ? info.slug + ':' : ''}${info.base}${attempt ? '#' + (attempt + 1) : ''}`
    ctx.calls.push(c)
    if (typeof prompt !== 'string' || !prompt) ctx.errors.push(`${c.id}: empty prompt`)
    schemaShape(opts.schema, c.id, ctx.errors)
    ctx.active++; ctx.maxActive = Math.max(ctx.maxActive, ctx.active)
    try {
      if (sc.delay) await new Promise(r => setTimeout(r, sc.delay(c)))
      let val = sc.respond ? sc.respond(c, ctx) : undefined
      if (val === undefined) val = defaults(c, ctx)
      if (val !== null && opts.schema) conforms(val, opts.schema, `${c.id} (response)`, ctx.errors)
      return val === null ? null : clone(val)
    } finally { ctx.active-- }
  }
  const parallel = thunks => Promise.all(thunks.map(f => Promise.resolve().then(f).catch(() => null)))
  const pipeline = (items, ...stages) => Promise.all(items.map(async (item, i) => {
    let r = item
    for (const s of stages) { try { r = await s(r, item, i) } catch (e) { return null } }
    return r
  }))
  const log = m => { ctx.logs.push(String(m)) }
  const phase = t => { ctx.phases.push(t) }
  const budget = sc.budget ? sc.budget(ctx) : { total: null, spent: () => 4242, remaining: () => Infinity }
  const workflow = () => { throw new Error('workflow() is not used by this script') }
  if (!COMPILED) throw new Error(`script does not compile: ${COMPILE_ERROR && COMPILE_ERROR.message}`)
  guardOn()
  try { ctx.result = await COMPILED(args, agent, parallel, pipeline, log, phase, budget, workflow) }
  catch (e) { ctx.error = e }
  finally { guardOff() }
  return ctx
}

const callsOf = (ctx, base, slug) => ctx.calls.filter(c => c.base === base && (!slug || c.slug === slug))
const P = (ctx, base, k = 0, slug) => (callsOf(ctx, base, slug)[k] || {}).prompt || ''
const order = (ctx, slug) => ctx.calls.filter(c => !slug || c.slug === slug).map(c => c.base).join(',')
const vres = (ctx, i = 0) => ((ctx.result && ctx.result.videos) || [])[i] || {}
const errMsg = ctx => (ctx.error && ctx.error.message) || ''
const J = x => JSON.stringify(x)

// Contract rules that hold for every agent call.
function contract(ctx, t, { mode = 'inline', eff = {} } = {}) {
  const rolesDir = fix(ctx.args.roles_dir)
  for (const c of ctx.calls) {
    const o = c.opts, cur = c.kind !== 'A', id = c.id
    t(o.phase === PHASE_OF[c.kind], `${id}: phase "${o.phase}" (expected ${PHASE_OF[c.kind]})`)
    t(o.schema && (o.schema.required || []).includes(KEY_OF[c.kind]), `${id}: schema is not the one of mode ${c.kind}`)
    t(o.model === (cur ? 'opus' : 'sonnet'), `${id}: model ${o.model}`)
    const O = (ctx.args && ctx.args.options) || {}
    const e = { INIT: 'high', FINAL: 'high', CROSS: 'high', NEXT: eff.next || O.curator_effort || 'high', A: eff.analyst || O.analyst_effort || 'high' }[c.kind]
    t(o.effort === e, `${id}: effort ${o.effort} (expected ${e})`)
    t(typeof o.label === 'string' && o.label.trim() !== '', `${id}: no label`)
    t(!c.attempt || has(o.label, '· 2nd'), `${id}: retry without "· 2nd" in the label`)
    t(c.attempt < 2, `${id}: more than 2 attempts`)
    t(cur || !/MODE:/.test(c.prompt), `${id}: analyst prompt with a MODE line`)
    t(!BLOCKED_NAMES.test(c.prompt), `${id}: prompt asks for report.md, summary.md or findings.md (the harness blocks subagents from writing them)`)
    const roleFile = `${rolesDir}/video-analysis-${cur ? 'curator' : 'analyst'}.md`
    if (mode === 'agents') {
      t(o.agentType === (cur ? 'video-analysis-curator' : 'video-analysis-analyst'), `${id}: agentType ${o.agentType}`)
      t(!c.prompt.includes(PREAMBLE_HEAD), `${id}: inline preamble in agents mode`)
    } else {
      t(!('agentType' in o), `${id}: agentType in inline mode`)
      t(c.prompt.startsWith(preamble(roleFile)), `${id}: inline preamble missing or with the wrong role`)
    }
  }
  for (const e of ctx.errors) t(false, e)
}

const RESULTS = []
async function scenario(name, fn) {
  const fails = []
  const t = (ok, msg) => { if (!ok) fails.push(msg) }
  try { await fn(t) } catch (e) { fails.push(`exception in the scenario: ${(e && e.stack) || e}`) }
  RESULTS.push({ name, fails })
}

// ---------------------------------------------------------------- scenarios
async function allScenarios() {
  await scenario('static: pure-literal meta, lint of forbidden APIs, compilation, guard', async t => {
    t(SRC.charCodeAt(0) !== 0xfeff, 'file starts with a BOM')
    t(SRC.startsWith('export const meta = {'), 'the script does not start with "export const meta = {"')
    const lit = balanced(SRC, SRC.indexOf('{'))
    t(!!lit, 'meta literal not closed')
    if (lit) {
      for (const p of metaProblems(lit)) t(false, `meta is not a pure literal: ${p}`)
      let M = null
      try { M = Function(`"use strict"; return (${lit})`)() } catch (e) { t(false, `meta does not evaluate: ${e.message}`) }
      if (M) {
        t(M.name === 'video-analysis', `meta.name ${M.name}`)
        t(typeof M.description === 'string' && M.description.length > 10, 'meta.description')
        t(M.whenToUse === undefined || typeof M.whenToUse === 'string', 'meta.whenToUse')
        t(Array.isArray(M.phases) && J(M.phases.map(p => p.title)) === J(PHASES), `meta.phases ≠ ${PHASES.join(', ')}`)
        for (const p of M.phases || []) {
          t(typeof p.detail === 'string', `phase ${p.title}: detail`)
          t(p.model === undefined || ['opus', 'sonnet', 'haiku'].includes(p.model), `phase ${p.title}: model`)
          const extra = Object.keys(p).filter(k => !['title', 'detail', 'model'].includes(k))
          t(!extra.length, `phase ${p.title}: extra keys ${extra}`)
        }
        const extra = Object.keys(M).filter(k => !['name', 'description', 'whenToUse', 'phases'].includes(k))
        t(!extra.length, `meta with extra keys: ${extra}`)
      }
    }
    for (const [re, what] of FORBIDDEN) t(!re.test(SRC), `forbidden API in the script: ${what}`)
    t((SRC.match(/\bexport\b/g) || []).length === 1, 'the script must export only meta')
    t(!!COMPILED, `body does not compile as an AsyncFunction: ${COMPILE_ERROR && COMPILE_ERROR.message}`)
    for (const body of ['return Date.now()', 'return Math.random()', 'return new Date()', 'return Date()']) {
      let threw = false
      guardOn()
      try { await new AsyncFunction(body)() } catch (e) { threw = true } finally { guardOff() }
      t(threw, `the guard did not block: ${body}`)
    }
    let allowed = null
    guardOn()
    try { allowed = await new AsyncFunction('return new Date(0).getTime() + Math.max(1, 2)')() } catch (e) { } finally { guardOff() }
    t(allowed === 2, 'the guard blocked an allowed use (new Date(0), Math.max)')
  })

  await scenario('happy path: INIT, A1, N2, A2, N3, A3, FINAL', async t => {
    const a = fixture('happy'), v = a.videos[0], bd = v.batches_dir, od = v.out_dir, im = v.batch_images
    const ctx = await run(a)
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,A2,N3,A3,FINAL', `order ${order(ctx)}`)
    contract(ctx, t, { mode: 'inline' })
    const init = P(ctx, 'INIT')
    for (const s of [`\n\nMODE: INIT\nVideo: ${v.slug} · ${v.duration} · 3 batches\n`, `Objective (verbatim) and options: ${a.objective_path}\n`,
      `Summary of the video and the batches: ${bd}/summary.json\n`, `Transcript: ${bd}/transcript.md\n`, `Overview (if it exists): ${bd}/overview.jpg\n`,
      `Batch 1: timeline ${bd}/batch_001.md · contact sheet ${bd}/sheet_001.jpg · ${im[0]} images\n`, 'Language of the reports: pt-BR\n',
      `Write: the ledger to ${od}/ledger.md; then the prompt of batch 1 to ${od}/prompts/batch_001.md.`]) t(has(init, s), `INIT missing: ${J(s)}`)
    t(!has(init, 'this run analyses only'), 'INIT with the limited-run notice in a complete run')
    const a1 = P(ctx, 'A1')
    for (const s of [`Batch 1/3 of video ${v.slug}.\n`, `Curator prompt: ${od}/prompts/batch_001.md (read it first and follow it).\n`,
      `Timeline: ${bd}/batch_001.md\n`, `Report: write it to ${od}/reports/batch_001.md\n`, `Images in the batch: ${im[0]} · Language of the report: pt-BR`]) t(has(a1, s), `A1 missing: ${J(s)}`)
    const n2 = P(ctx, 'N2')
    for (const s of [`MODE: NEXT\nVideo: ${v.slug} · prepare batch 2/3\n`, `Objective: ${a.objective_path}\nLedger: ${od}/ledger.md\n`, 'Reports to integrate:\n',
      `- batch 001: ok, ${im[0]}/${im[0]} images → ${od}/reports/batch_001.md\n`,
      `Next batch: timeline ${bd}/batch_002.md · contact sheet ${bd}/sheet_002.jpg · ${im[1]} images\n`,
      `Write: the updated ledger to ${od}/ledger.md; then the prompt to ${od}/prompts/batch_002.md.`]) t(has(n2, s), `N2 missing: ${J(s)}`)
    t(!has(n2, 'Check "Integrado até"'), 'N2 asks for the resume check without a resume')
    t(has(P(ctx, 'N3'), 'prepare batch 3/3 (last of this run)\n'), 'N3 without "(last of this run)"')
    const fin = P(ctx, 'FINAL')
    for (const s of [`MODE: FINAL\nVideo: ${v.slug} · batches analysed: 3 of 3\n`, `Batch reports: ${od}/reports/batch_001.md … batch_003.md\n`,
      'Not yet integrated into the ledger (in this run): 003 (ok); also check "Integrado até".\n', 'Partial: none · Failed: none\n',
      `Images seen: ${sum(im)} of ${sum(im)}\n`, `Transcript (for spot checks): ${bd}/transcript.md\nLanguage: pt-BR\n`,
      `Write the final report to ${od}/relatorio.md.`]) t(has(fin, s), `FINAL missing: ${J(s)}`)
    t(!has(fin, 'partial coverage'), 'FINAL with partial coverage in a complete run')
    const R = ctx.result || {}, rv = vres(ctx)
    t(rv.status === 'ok', `status ${rv.status}`)
    t(J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 3, partial: [], failed: [] }), `batches ${J(rv.batches)}`)
    t(rv.frames === `${sum(im)}/${sum(im)}`, `frames ${rv.frames}`)
    t(rv.report_path === `${od}/relatorio.md` && rv.summary === `resumo de ${v.slug}` && rv.answered === 'yes', 'report_path/summary/answered')
    t(J(rv.warnings) === '[]', `unexpected warnings ${J(rv.warnings)}`)
    t(R.agents === 7 && R.roles_mode === 'inline' && R.combined === null && !('output_tokens' in R), `agents ${R.agents}, roles_mode ${R.roles_mode}, combined ${J(R.combined)}, output_tokens ${R.output_tokens}`)
    t(ctx.logs.some(l => has(l, 'plan ready (screen)')), 'log without "plan ready"')
    t(ctx.logs[0] === `run ${a.run_id}: 1 video(s), ~7 agents, roles inline, curation per batch, up to 3 video(s) in parallel`, `1st log: ${ctx.logs[0]}`)
    t(!ctx.calls.some(c => c.retry), 'unexpected retry')
  })

  await scenario('limited run (max_batches 2 of 4): 5 agents and partial coverage', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir
    a.options.max_batches = 2; v.batch_images = [20, 18, 19, 21]
    const ctx = await run(a)
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,A2,FINAL', `order ${order(ctx)}`)
    contract(ctx, t)
    t(has(P(ctx, 'INIT'), `· 4 batches (this run analyses only batches 1–2; plan all of them and mark the others as outside this run)\n`), 'INIT without the limited-run notice')
    t(has(P(ctx, 'N2'), 'prepare batch 2/2 (last of this run)'), 'N2 without "(last of this run)"')
    const fin = P(ctx, 'FINAL')
    t(has(fin, 'batches analysed: 2 of 4 (partial coverage: say so in the report)\n'), 'FINAL without partial coverage')
    t(has(fin, `${od}/reports/batch_001.md … batch_002.md\n`) && has(fin, 'Images seen: 38 of 38\n'), 'FINAL with the wrong range of reports or images')
    const rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 4, planned: 2, from: 1, ok: 2, partial: [], failed: [] }) && rv.frames === '38/38', `return ${J(rv)}`)
    t(ctx.logs.some(l => has(l, 'test mode, batches 1–2 of 4')), 'log without "test mode"')
    t(has(ctx.logs[0], '~5 agents'), `estimate: ${ctx.logs[0]}`)
  })

  await scenario('analyst with missing images: 2nd-attempt note with the ids; partial goes to the NEXT', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir, im = v.batch_images
    const ctx = await run(a, { respond: c => {
      if (c.base === 'A1' && c.attempt === 0) return { status: 'partial', report_path: `${od}/reports/batch_001.md`, frames_seen: im[0] - 2, frames_missing: ['F0007', 'F0012'], notable: 'x' }
      if (c.base === 'A2') return { status: 'partial', report_path: `${od}/reports/batch_002.md`, frames_seen: im[1] - 3, frames_missing: ['F0030', 'F0031', 'F0033'], notable: 'y' }
      if (c.base === 'A3' && c.attempt === 0) return null
    } })
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,A1,N2,A2,A2,N3,A3,A3,FINAL', `order ${order(ctx)}`)
    contract(ctx, t)
    const a1 = P(ctx, 'A1'), a1b = P(ctx, 'A1', 1)
    t(a1b.startsWith(a1 + '\n\nSECOND ATTEMPT. '), 'the 2nd attempt of A1 is not the base prompt + note')
    t(has(a1b, `SECOND ATTEMPT. The previous one opened ${im[0] - 2} of ${im[0]} images (missing: F0007, F0012). If ${od}/reports/batch_001.md exists, read it before overwriting; open the images that were missing (when in doubt, all of them) and write the complete report.`), `A1 note: ${J(a1b.slice(a1.length))}`)
    t(has(P(ctx, 'A3', 1), 'SECOND ATTEMPT. The previous one did not finish. If '), 'A3 2nd without "did not finish"')
    const n3 = P(ctx, 'N3')
    t(has(n3, `- batch 002: PARTIAL, ${im[1] - 3}/${im[1]} images → ${od}/reports/batch_002.md\n`), 'N3 without the PARTIAL line')
    t(!has(n3, 'speech of the segment'), 'PARTIAL does not get "speech of the segment"')
    const rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 2, partial: [2], failed: [] }), `return ${J(rv)}`)
    t(rv.frames === `${sum(im) - 3}/${sum(im)}`, `frames ${rv.frames}`)
    t(has(P(ctx, 'FINAL'), 'Partial: 2 · Failed: none\n') && has(P(ctx, 'FINAL'), `Images seen: ${sum(im) - 3} of ${sum(im)}\n`), 'FINAL without partial/images')
    t(ctx.logs.some(l => has(l, `1/3: ${im[0] - 2}/${im[0]} images, partial; retrying`)), 'log of the retry')
  })

  await scenario('curation fails (NEXT null 2×): contingency analyst; the following NEXT lists 2 pending with FAILED', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir, bd = v.batches_dir, im = v.batch_images
    const ctx = await run(a, { respond: c => (c.base === 'N2' || c.base === 'A2' ? null : undefined) })
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,N2,A2,A2,N3,A3,FINAL', `order ${order(ctx)}`)
    contract(ctx, t)
    t(P(ctx, 'N2', 1) === P(ctx, 'N2') + AGAIN, 'the 2nd attempt of the NEXT is not the prompt + AGAIN note')
    const a2 = callsOf(ctx, 'A2')[0] || {}
    t(a2.contingency && has(a2.prompt, `Batch 2/3 of video ${v.slug}. There is no curator prompt for this batch: follow the default contract of your role.\nObjective: ${a.objective_path}. Context: sections 4 and 5 of ${od}/ledger.md, if it exists.\nTimeline: ${bd}/batch_002.md\nReport: write it to ${od}/reports/batch_002.md\nImages in the batch: ${im[1]} · Language of the report: pt-BR`), 'contingency prompt of A2')
    t(!has(a2.prompt, 'Curator prompt:'), 'contingency cites a curator prompt')
    const lines = P(ctx, 'N3').split('\n').filter(l => l.startsWith('- batch '))
    t(lines.length === 2, `N3 should list 2 pending: ${J(lines)}`)
    t(lines[0] === `- batch 001: ok, ${im[0]}/${im[0]} images → ${od}/reports/batch_001.md`, `1st pending: ${lines[0]}`)
    t(lines[1] === `- batch 002: FAILED, no reliable report → ${od}/reports/batch_002.md (speech of the segment: ${bd}/batch_002.md)`, `2nd pending: ${lines[1]}`)
    t(!(callsOf(ctx, 'A3')[0] || {}).contingency, 'A3 should have a curator prompt')
    const rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 2, partial: [], failed: [2] }), `return ${J(rv)}`)
    t((rv.warnings || []).includes('curation of batch 2 failed: contingency prompt'), `warnings ${J(rv.warnings)}`)
    t(has(P(ctx, 'FINAL'), 'Partial: none · Failed: 2\n') && has(P(ctx, 'FINAL'), `Images seen: ${im[0] + im[2]} of ${sum(im)}\n`), 'FINAL without failed/images')
  })

  await scenario('two failures in a row interrupt the video (curator, analyst, INIT)', async t => {
    const a = fixture('happy'); a.videos[0].batch_images = [20, 18, 19, 21]
    let ctx = await run(a, { respond: c => (c.base === 'N2' || c.base === 'N3' ? null : undefined) })
    t(order(ctx) === 'INIT,A1,N2,N2,A2,N3,N3', `curator: order ${order(ctx)}`)
    let rv = vres(ctx)
    t(rv.status === 'failed' && rv.report_path === null && rv.summary === null, `curator: return ${J(rv)}`)
    t((rv.warnings || []).includes('curator failed in 2 batches in a row: video interrupted'), `curator: warnings ${J(rv.warnings)}`)
    t(J(rv.batches) === J({ total: 4, planned: 4, from: 1, ok: 2, partial: [], failed: [] }), `curator: batches ${J(rv.batches)}`)
    contract(ctx, t)

    const failA = { status: 'failed', report_path: '', frames_seen: 0, frames_missing: [], notable: '', warnings: ['unreadable role'] }
    ctx = await run(fixture('happy'), { respond: c => (c.base === 'A1' ? failA : c.base === 'A2' ? null : undefined) })
    t(order(ctx) === 'INIT,A1,A1,N2,A2,A2', `analyst: order ${order(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'failed' && J(rv.batches && rv.batches.failed) === '[1,2]', `analyst: return ${J(rv)}`)
    t((rv.warnings || []).includes('batch 1: analysis failed (unreadable role)'), `analyst: reason missing ${J(rv.warnings)}`)
    t((rv.warnings || []).includes('analyst failed in 2 batches in a row: video interrupted'), `analyst: warnings ${J(rv.warnings)}`)
    t(has(P(ctx, 'N2'), '- batch 001: FAILED, no reliable report → '), 'N2 without FAILED for batch 1')
    contract(ctx, t)

    const failI = { status: 'failed', ledger_path: '', prompt_path: '', video_type: 'other', focus: '', warnings: ['unreadable role'] }
    ctx = await run(fixture('happy'), { respond: c => (c.kind === 'INIT' ? failI : undefined) })
    t(order(ctx) === 'INIT,INIT', `INIT: order ${order(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'failed' && (rv.warnings || []).includes('plan (INIT) failed twice (unreadable role)'), `INIT: return ${J(rv)}`)
    t(P(ctx, 'INIT', 1) === P(ctx, 'INIT') + AGAIN, 'INIT 2nd without the AGAIN note')
    contract(ctx, t)

    const nc = fixture('happy'); nc.videos[0].batch_images = [20, 18, 19, 21, 17]
    ctx = await run(nc, { respond: c => (['A1', 'N2', 'A3', 'N4'].includes(c.base) ? null : undefined) })
    t(order(ctx) === 'INIT,A1,A1,N2,N2,A2,N3,A3,A3,N4,N4,A4,N5,A5,FINAL', `failures not in a row do not interrupt: order ${order(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 5, planned: 5, from: 1, ok: 3, partial: [], failed: [1, 3] }), `failures not in a row: return ${J(rv)}`)
    t((callsOf(ctx, 'A2')[0] || {}).contingency && (callsOf(ctx, 'A4')[0] || {}).contingency && !(callsOf(ctx, 'A5')[0] || {}).contingency, 'contingency only where the curation failed')

    const failF = { status: 'failed', report_path: '', answered: 'no', summary: 'ledger ilegível' }
    ctx = await run(fixture('happy'), { respond: c => (c.kind === 'FINAL' ? failF : undefined) })
    rv = vres(ctx)
    t(order(ctx) === 'INIT,A1,N2,A2,N3,A3,FINAL,FINAL', `FINAL: order ${order(ctx)}`)
    t(rv.status === 'failed' && (rv.warnings || []).includes('final report failed twice (ledger ilegível): run /video-analysis retomar'), `FINAL: return ${J(rv)}`)
  })

  await scenario('resume: ledger present and batch 3 without a prompt → 1st call is NEXT 3 with "Integrado até"; cases of §3.3', async t => {
    const a = fixture('resume'), v = a.videos[0], od = v.out_dir, im = v.batch_images
    let ctx = await run(a)
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'N3,A3,N4,A4,FINAL', `order ${order(ctx)}`)
    contract(ctx, t)
    const n3 = (ctx.calls[0] || {}).prompt || ''
    t(has(n3, 'MODE: NEXT\n') && has(n3, 'prepare batch 3/4\n'), 'the 1st call is not NEXT 3')
    t(has(n3, `Check "Integrado até" in the ledger and integrate the existing reports in ${od}/reports up to batch 002.\n`), 'NEXT 3 without "Integrado até"')
    t(!has(n3, 'Reports to integrate:'), 'NEXT 3 lists pending batches that do not exist')
    t(!has(P(ctx, 'N4'), 'Check "Integrado até"') && has(P(ctx, 'N4'), `- batch 003: ok, ${im[2]}/${im[2]} images → `), 'NEXT 4')
    let rv = vres(ctx)
    const seen = im[2] + im[3]
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 5, planned: 4, from: 3, ok: 2, partial: [], failed: [] }) && rv.frames === `${seen}/${seen}`, `return ${J(rv)}`)
    const fin = P(ctx, 'FINAL')
    t(has(fin, 'batches analysed: 4 of 5 (partial coverage: say so in the report)\n') && has(fin, '… batch_004.md\n'), 'FINAL of the resume')
    t(has(fin, `Images seen: ${seen} of ${seen} in batches 003–004 of this run; batches 001–002: previous run (see the ledger)\n`), 'FINAL without the images seen of the resume')
    t(has(ctx.logs[0], '~5 agents'), `estimate ${ctx.logs[0]}`)

    const b = fixture('resume'); b.videos[0].start_prompt_exists = true
    ctx = await run(b)
    t(order(ctx) === 'A3,N4,A4,FINAL', `existing prompt: order ${order(ctx)}`)
    t(has(P(ctx, 'A3'), `Curator prompt: ${od}/prompts/batch_003.md (read it first and follow it).`), 'A3 without the curator prompt')

    const c2 = fixture('resume'); c2.videos[0].start_batch = 5
    ctx = await run(c2)
    t(order(ctx) === 'FINAL', `all analysed: order ${order(ctx)}`)
    t(has(P(ctx, 'FINAL'), 'Not yet integrated into the ledger (in this run): none; also check "Integrado até".\n'), 'FINAL without "none"')
    t(has(P(ctx, 'FINAL'), 'Images seen: no new batch in this run; batches 001–004: previous run (see the ledger)\n'), 'FINAL without the images of the empty resume')
    rv = vres(ctx)
    t(rv.frames === null && rv.batches && rv.batches.from === 5 && rv.batches.planned === 4 && rv.status === 'partial', `all analysed: return ${J(rv)}`)

    const d = fixture('resume'); d.videos[0].ledger_exists = false
    ctx = await run(d)
    t(order(ctx).startsWith('INIT,A1,N2,'), `no ledger: order ${order(ctx)}`)
  })

  await scenario('default curation (plan): INIT, A1, A2, A3, FINAL; analysts 2+ in contingency; analyst at high', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir
    delete a.options.curation; delete a.options.analyst_effort
    const ctx = await run(a)
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,A2,A3,FINAL', `order ${order(ctx)}`)
    contract(ctx, t)
    t(callsOf(ctx, 'A1').every(c => c.opts.effort === 'high'), 'analyst without high effort by default')
    t(!(callsOf(ctx, 'A1')[0] || {}).contingency && has(P(ctx, 'A1'), `Curator prompt: ${od}/prompts/batch_001.md (read it first and follow it).`), 'A1 without the prompt of the INIT')
    for (const n of [2, 3]) {
      const p = P(ctx, `A${n}`)
      t(has(p, `Batch ${n}/3 of video ${v.slug}. There is no curator prompt for this batch: follow the default contract of your role.\n`)
        && has(p, `Objective: ${a.objective_path}. Context: sections 4 and 5 of ${od}/ledger.md, if it exists.\n`), `A${n} without the contingency text`)
    }
    t(has(P(ctx, 'FINAL'), 'Not yet integrated into the ledger (in this run): 001 (ok), 002 (ok), 003 (ok); also check "Integrado até".\n'), 'FINAL without the 3 pending batches')
    const R = ctx.result || {}, rv = vres(ctx)
    t(rv.status === 'ok' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 3, partial: [], failed: [] }) && J(rv.warnings) === '[]', `return ${J(rv)}`)
    t(R.agents === 5, `agents ${R.agents}`)
    t(ctx.logs[0] === `run ${a.run_id}: 1 video(s), ~5 agents, roles inline, curation only in the plan, up to 3 video(s) in parallel`, `1st log: ${ctx.logs[0]}`)
    const b = fixture('happy'); b.options.curation = 'plan'
    t(order(await run(b)) === 'INIT,A1,A2,A3,FINAL', 'curation "plan" differs from the default')
    const c = fixture('happy'); c.options.curation = 'lotes'
    const cc = await run(c)
    t(!!cc.error && has(errMsg(cc), 'options.curation') && !cc.calls.length, `invalid curation accepted: ${errMsg(cc)}`)
  })

  await scenario('resume without per-batch curation: batch 3 without a prompt → contingency analyst, no NEXT', async t => {
    const a = fixture('resume'); delete a.options.curation
    let ctx = await run(a)
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'A3,A4,FINAL', `order ${order(ctx)}`)
    contract(ctx, t)
    t(ctx.calls.slice(0, 2).every(c => c.contingency), 'A3/A4 not in contingency')
    t(has(P(ctx, 'FINAL'), 'Not yet integrated into the ledger (in this run): 003 (ok), 004 (ok); also check "Integrado até".\n'), 'FINAL of the resume without the pending batches')
    t(has(ctx.logs[0], '~3 agents'), `estimate ${ctx.logs[0]}`)   // resume with a ledger: no INIT
    const b = fixture('resume'); delete b.options.curation; b.videos[0].start_prompt_exists = true
    ctx = await run(b)
    t(order(ctx) === 'A3,A4,FINAL' && !ctx.calls[0].contingency && ctx.calls[1].contingency, `existing prompt: order ${order(ctx)}`)
  })

  await scenario('final_exists: video skipped without agents', async t => {
    const a = fixture('happy'), v = a.videos[0]; v.final_exists = true
    const ctx = await run(a)
    t(!ctx.error && ctx.calls.length === 0, `calls ${order(ctx)} ${errMsg(ctx)}`)
    const rv = vres(ctx)
    t(rv.status === 'skipped' && rv.report_path === `${v.out_dir}/relatorio.md` && rv.frames === null, `return ${J(rv)}`)
    t(J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 0, partial: [], failed: [] }), `batches ${J(rv.batches)}`)
    t(ctx.result.agents === 0 && ctx.result.combined === null, 'agents/combined')
    t(ctx.logs.some(l => has(l, 'video skipped')), 'log without "skipped"')
  })

  await scenario('3 videos with parallelism 2: never more than 2 simultaneous agents; CROSS at the end', async t => {
    const a = fixture('multi')
    const ctx = await run(a, { delay: c => 1 + ((c.idx * 7) % 5) })
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(ctx.maxActive <= 2, `peak of ${ctx.maxActive} simultaneous agents`)
    t(ctx.maxActive === 2, 'the videos did not run in parallel')
    for (const v of a.videos) t(order(ctx, v.slug) === 'INIT,A1,N2,A2,FINAL', `${v.slug}: ${order(ctx, v.slug)}`)
    const inits = a.videos.map(v => (callsOf(ctx, 'INIT', v.slug)[0] || {}).idx)
    t(inits[0] < 2 && inits[1] < 2 && inits[2] >= 5, `the 3rd video should wait for a free slot: ${J(inits)}`)
    const cross = ctx.calls.filter(c => c.kind === 'CROSS')
    t(cross.length === 1 && ctx.calls[ctx.calls.length - 1].kind === 'CROSS', 'CROSS must run once, last')
    const cp = (cross[0] || {}).prompt || ''
    t(has(cp, `MODE: CROSS\nRun: ${a.run_id}\nObjective: ${a.objective_path}\nReports per video:\n`), 'header of the CROSS')
    for (const v of a.videos) t(has(cp, `\n- ${v.slug}: ${v.out_dir}/relatorio.md (ok)\n`), `CROSS without ${v.slug}`)
    t(has(cp, `Language: pt-BR\nWrite the combined report to ${a.run_dir}/relatorio.md.`), 'footer of the CROSS')
    const R = ctx.result || {}
    t(J(R.combined) === J({ report_path: `${a.run_dir}/relatorio.md`, answered: 'yes', summary: 'resumo combinado' }), `combined ${J(R.combined)}`)
    t(R.agents === 16 && R.roles_mode === 'agents', `agents ${R.agents}`)
    contract(ctx, t, { mode: 'agents' })
    const long = a.videos[0].slug
    t(ctx.calls.filter(c => c.slug === long).every(c => has(c.opts.label, long.slice(0, 23) + '…')), 'the label of the long slug was not shortened')
    t(has(ctx.logs[0], '~16 agents') && has(ctx.logs[0], 'up to 2 video(s) in parallel'), `estimate ${ctx.logs[0]}`)
  })

  await scenario('CROSS only with 2+ reports', async t => {
    let ctx = await run(fixture('happy'))
    t(!ctx.calls.some(c => c.kind === 'CROSS') && ctx.result.combined === null, '1 video has no CROSS')

    const two = fixture('multi'); two.videos = two.videos.slice(0, 2)
    ctx = await run(two, { respond: c => (c.kind === 'INIT' && c.slug === two.videos[1].slug ? null : undefined) })
    t(!ctx.calls.some(c => c.kind === 'CROSS') && ctx.result.combined === null, 'with 1 report there should be no CROSS')
    t(vres(ctx, 1).status === 'failed' && vres(ctx, 0).status === 'ok', 'status of the two videos')

    const sk = fixture('multi'); sk.videos = sk.videos.slice(0, 2); sk.videos[0].final_exists = true
    ctx = await run(sk)
    const cp = P(ctx, 'CROSS')
    t(callsOf(ctx, 'CROSS').length === 1 && has(cp, `- ${sk.videos[0].slug}: ${sk.videos[0].out_dir}/relatorio.md (skipped)\n`) && has(cp, `- ${sk.videos[1].slug}: ${sk.videos[1].out_dir}/relatorio.md (ok)\n`), 'skipped video goes into the CROSS')
    t(has(ctx.logs[0], '~6 agents'), `estimate ${ctx.logs[0]}`)

    const ce = fixture('multi'); ce.combined_exists = true
    ctx = await run(ce)
    t(!ctx.calls.some(c => c.kind === 'CROSS') && ctx.result.combined === null, 'combined_exists should not run CROSS')

    ctx = await run(fixture('multi'), { respond: c => (c.kind === 'CROSS' ? { status: 'failed', report_path: '', answered: 'no', summary: 'relatório de vídeo ilegível' } : undefined) })
    t(callsOf(ctx, 'CROSS').length === 2 && P(ctx, 'CROSS', 1) === P(ctx, 'CROSS') + AGAIN, 'CROSS repeated with the AGAIN note')
    t(J(ctx.result.combined) === J({ error: 'synthesis failed twice (relatório de vídeo ilegível)' }), `combined ${J(ctx.result.combined)}`)
  })

  await scenario('size guard: refuses above 800 agents', async t => {
    const a = fixture('happy'); a.videos[0].batch_images = Array.from({ length: 400 }, () => 20)
    let ctx = await run(a)
    t(/^run too large \(~801 agents; Workflow cap 1000\)/.test(errMsg(ctx)), `error: ${errMsg(ctx)}`)
    t(ctx.calls.length === 0, 'called agents before refusing')
    a.options.max_batches = 10
    ctx = await run(a)
    t(!ctx.error && ctx.calls.length === 21, `with max_batches 10: ${ctx.calls.length} calls ${errMsg(ctx)}`)
    const b = fixture('happy'); b.videos[0].batch_images = Array.from({ length: 399 }, () => 20)
    ctx = await run(b)
    t(!ctx.error && ctx.calls.length === 799, `399 batches (799 agents) should pass: ${ctx.calls.length} ${errMsg(ctx)}`)
  })

  await scenario('role modes: agents passes agentType+model+effort; inline has a preamble and no agentType', async t => {
    const a = fixture('happy'); a.roles_mode = 'agents'
    let ctx = await run(a)
    t(!ctx.error && ctx.result.roles_mode === 'agents', `agents: ${errMsg(ctx)}`)
    contract(ctx, t, { mode: 'agents' })
    t(ctx.calls.every(c => c.opts.agentType && c.opts.model && c.opts.effort), 'agents: missing agentType/model/effort')
    ctx = await run(fixture('happy'))
    contract(ctx, t, { mode: 'inline' })
    t(ctx.calls.every(c => c.opts.model && c.opts.effort && !('agentType' in c.opts)), 'inline: options')
    const roles = fixture('happy').roles_dir
    t(has(P(ctx, 'INIT'), preamble(`${roles}/video-analysis-curator.md`) + 'MODE: INIT\n'), 'inline: curator preamble followed by the MODE line')
    t(has(P(ctx, 'A1'), preamble(`${roles}/video-analysis-analyst.md`) + 'Batch 1/3 of video '), 'inline: analyst preamble')
    const e = fixture('happy'); e.options.analyst_effort = 'medium'; e.options.curator_effort = 'low'
    ctx = await run(e)
    contract(ctx, t, { mode: 'inline', eff: { next: 'low', analyst: 'medium' } })
    const m = fixture('happy'); delete m.roles_mode; delete m.options
    ctx = await run(m)
    t(!ctx.error && ctx.result.roles_mode === 'inline', 'without roles_mode/options → inline')
    contract(ctx, t, { mode: 'inline' })
  })

  await scenario('format of the return', async t => {
    for (const name of ['happy', 'multi', 'resume']) {
      const a = fixture(name), ctx = await run(a), R = ctx.result
      t(R && J(Object.keys(R)) === J(['run_id', 'run_dir', 'roles_mode', 'agents', 'videos', 'combined']), `${name}: keys ${R && Object.keys(R)}`)
      if (!R) continue
      t(R.run_id === a.run_id && R.run_dir === a.run_dir && typeof R.agents === 'number', `${name}: run_id/run_dir/agents`)
      t(R.videos.length === a.videos.length, `${name}: number of videos`)
      for (const rv of R.videos) {
        t(J(Object.keys(rv)) === J(['slug', 'title', 'status', 'report_path', 'answered', 'summary', 'batches', 'frames', 'warnings']), `${name}: keys of the video ${Object.keys(rv)}`)
        t(['ok', 'partial', 'failed', 'skipped'].includes(rv.status), `${name}: status ${rv.status}`)
        t(rv.batches && J(Object.keys(rv.batches)) === J(['total', 'planned', 'from', 'ok', 'partial', 'failed']), `${name}: keys of batches`)
        t(rv.batches && typeof rv.batches.ok === 'number' && Array.isArray(rv.batches.partial) && Array.isArray(rv.batches.failed), `${name}: types of batches`)
        t(/^\d+\/\d+$/.test(rv.frames), `${name}: frames ${rv.frames}`)
        t(Array.isArray(rv.warnings) && rv.warnings.length <= 6, `${name}: warnings`)
        t(rv.title === (a.videos.find(v => v.slug === rv.slug) || {}).title, `${name}: title`)
      }
      t(J(R).length < 6000, `${name}: return too large (${J(R).length} characters)`)
    }
    const w = fixture('happy'); w.videos[0].batch_images = [20, 18, 19, 20, 17, 16, 15, 14, 13]
    const ctx = await run(w, { respond: c => (c.kind === 'NEXT' ? { status: 'ok', integrated_through: c.n - 1, coverage: [{ batch: c.n - 1, state: 'incomplete' }], prompt_path: 'x', focus: 'f' } : undefined) })
    const ws = vres(ctx).warnings || []
    t(ws.length === 6 && ws[4] === '+3 warnings omitted' && ws[0] === 'batch 1: report incomplete' && ws[5] === 'batch 8: report incomplete', `warnings without an explicit cut: ${J(ws)}`)
    const f = fixture('happy'); f.videos[0].batch_images = [20, 18, 19, 20, 17, 16, 15, 14, 13]
    const cf = await run(f, { respond: c => {
      if (c.kind === 'NEXT' && c.n <= 7) return { status: 'ok', integrated_through: c.n - 1, coverage: [{ batch: c.n - 1, state: 'incomplete' }], prompt_path: 'x', focus: 'f' }
      if (c.base === 'A7' || c.base === 'A8') return null
    } })
    const wf = vres(cf).warnings || []
    t(vres(cf).status === 'failed' && wf.length === 6 && wf[4] === '+2 warnings omitted' && wf[5] === 'analyst failed in 2 batches in a row: video interrupted', `the reason for the interruption did not survive the cut of warnings: ${J(wf)}`)
  })

  await scenario('validation of workflow_args: the error names the keys', async t => {
    const a = fixture('happy'), v = a.videos[0]
    a.schema_version = 2; v.cache_dir = v.batches_dir; delete v.batches_dir; delete v.title
    v.batch_frames = v.batch_images; delete v.batch_images; v.final_exists = 'nao'
    let ctx = await run(a)
    t(ctx.error && ctx.calls.length === 0, 'should refuse without calling agents')
    for (const k of ['schema_version', 'videos[0].batches_dir (cache_dir is the old name)', 'videos[0].title', 'videos[0].batch_images (batch_frames is the old name)', 'videos[0].final_exists', 'run prepare.py and pass workflow_args as a JSON object, without editing'])
      t(has(errMsg(ctx), k), `error without "${k}": ${errMsg(ctx)}`)
    const b = fixture('happy'); b.options.analyst_effort = 'alto'; b.options.max_parallel_videos = 0; b.roles_mode = 'agent'; b.videos[0].batch_images = [20, 0]
    ctx = await run(b)
    for (const k of ['options.analyst_effort', 'options.max_parallel_videos', 'roles_mode', 'videos[0].batch_images']) t(has(errMsg(ctx), k), `error without "${k}": ${errMsg(ctx)}`)
    t(!has(errMsg(ctx), 'schema_version'), 'flagged a valid key')
    const c = fixture('multi'); c.videos[1].slug = c.videos[0].slug
    ctx = await run(c)
    t(has(errMsg(ctx), 'videos[1].slug (duplicate)'), `duplicate slug: ${errMsg(ctx)}`)
    for (const bad of [undefined, '{não é json', [1, 2]]) { ctx = await run(bad); t(has(errMsg(ctx), 'invalid workflow_args (args)'), `args ${J(bad)}: ${errMsg(ctx)}`) }
    const e = fixture('happy'); e.videos = []
    ctx = await run(e)
    t(has(errMsg(ctx), 'invalid workflow_args (videos)'), `empty videos: ${errMsg(ctx)}`)
    ctx = await run(JSON.stringify(fixture('happy')))
    t(!ctx.error && order(ctx) === 'INIT,A1,N2,A2,N3,A3,FINAL', `args as JSON text: ${order(ctx)} ${errMsg(ctx)}`)
    const f = fixture('happy'), clean = fixture('happy').videos[0]
    f.videos[0].batches_dir = f.videos[0].batches_dir.replace(/\//g, '\\') + '\\'; f.videos[0].out_dir += '/'
    ctx = await run(f)
    t(has(P(ctx, 'A1'), `Timeline: ${clean.batches_dir}/batch_001.md\nReport: write it to ${clean.out_dir}/reports/batch_001.md`), 'paths with \\ and a trailing slash were not normalized')
    t(!ctx.calls.some(c2 => c2.prompt.includes('\\') || /[^:]\/\//.test(c2.prompt)), 'prompt with \\ or //')
    t(J(vres(ctx).warnings) === '[]', `warnings with normalized paths: ${J(vres(ctx).warnings)}`)
  })

  await scenario('budget running out, agent error, coverage audit and report path', async t => {
    let ctx = await run(fixture('happy'), { budget: c => ({ total: 1000000, spent: () => 1, remaining: () => (c.calls.length >= 4 ? 50000 : 900000) }) })
    t(order(ctx) === 'INIT,A1,N2,A2,FINAL', `budget: order ${order(ctx)}`)
    let rv = vres(ctx)
    t(rv.status === 'partial' && rv.batches.planned === 2 && (rv.warnings || []).includes('token budget running out: stopped before batch 3'), `budget: return ${J(rv)}`)
    t(has(P(ctx, 'FINAL'), 'batches analysed: 2 of 3 (partial coverage: say so in the report)\n') && has(P(ctx, 'FINAL'), '… batch_002.md\n'), 'budget: FINAL')
    t(!('output_tokens' in ctx.result), 'the return does not carry output_tokens (budget.spent() adds up the whole session)')

    const two = fixture('multi'); two.videos = two.videos.slice(0, 2)
    ctx = await run(two, { respond: c => { if (c.slug === two.videos[0].slug && c.base === 'A2') throw new Error('orçamento esgotado') } })
    t(!ctx.error, `an agent error should not bring down the workflow: ${errMsg(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'failed' && (rv.warnings || []).includes('error: orçamento esgotado') && rv.batches && rv.batches.ok === 1, `error: return ${J(rv)}`)
    t(vres(ctx, 1).status === 'ok' && !ctx.calls.some(c => c.kind === 'CROSS') && ctx.result.combined === null, 'the other video goes on; no CROSS')

    ctx = await run(fixture('happy'), { respond: c => {
      if (c.base === 'N2') return { status: 'ok', integrated_through: 1, coverage: [{ batch: 1, state: 'incomplete' }], prompt_path: 'x', focus: 'f' }
      if (c.base === 'N3') return { status: 'ok', integrated_through: 1, coverage: [{ batch: 2, state: 'missing' }], prompt_path: 'x', focus: 'f' }
    } })
    rv = vres(ctx)
    t(J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 1, partial: [1], failed: [2] }) && rv.status === 'partial', `coverage: return ${J(rv)}`)
    for (const w of ['batch 1: report incomplete', 'batch 2: report missing', 'ledger integrated only up to batch 1 when preparing batch 3']) t((rv.warnings || []).includes(w), `coverage: missing warning "${w}"`)

    const h = fixture('happy'), od = h.videos[0].out_dir
    ctx = await run(h, { respond: c => {
      if (c.base === 'A1') return { status: 'ok', report_path: `${od}/reports/batch_001.md`.replace(/\//g, '\\').toUpperCase(), frames_seen: 20, frames_missing: [], notable: '' }
      if (c.base === 'A2') return { status: 'ok', report_path: 'C:/outro/batch_002.md', frames_seen: 18, frames_missing: [], notable: '' }
    } })
    t(order(ctx) === 'INIT,A1,N2,A2,A2,N3,A3,FINAL', `path: order ${order(ctx)}`)
    t(J(vres(ctx).warnings) === J(['batch 2: report written to the wrong path (C:/outro/batch_002.md)']), `path: warnings ${J(vres(ctx).warnings)}`)
  })

  await scenario('wrong path corrected on the 2nd attempt: note 5b with the two paths; batch ok, no warning', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir, im = v.batch_images
    // as in the real run 20261006-172520: path built from the run_id and the slug, not copied from the call
    const bad = `${a.run_dir}-gip-portal-mira-20261002-095428/${v.slug}/reports/batch_002.md`
    let ctx = await run(a, { respond: c => {
      if (c.base === 'A1') return { status: 'ok', report_path: ` ${od}//reports/batch_001.md `.replace(/\//g, '\\').toUpperCase(), frames_seen: im[0], frames_missing: [], notable: '' }
      if (c.base === 'A2' && c.attempt === 0) return { status: 'ok', report_path: bad.replace(/\//g, '\\'), frames_seen: im[1], frames_missing: [], notable: 'x' }
    } })
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,A2,A2,N3,A3,FINAL', `order ${order(ctx)}`)
    contract(ctx, t)
    const a2 = P(ctx, 'A2'), a2b = P(ctx, 'A2', 1)
    t(a2b === a2 + `\n\nSECOND ATTEMPT. The previous one opened ${im[1]} of ${im[1]} images, but wrote the report to ${bad}, not to the requested path. Write the complete report exactly to ${od}/reports/batch_002.md, copying the path character by character, and return that same path in report_path. First, read whatever exists at those two paths and keep what is right; open the images that were missing (when in doubt, all of them).`, `note 5b: ${J(a2b.slice(a2.length))}`)
    t(ctx.logs.some(l => has(l, `2/3: ${im[1]}/${im[1]} images, ok, report at ${bad}; retrying`)), 'log of the retry for the wrong path')
    t(has(P(ctx, 'N3'), `- batch 002: ok, ${im[1]}/${im[1]} images → ${od}/reports/batch_002.md\n`), 'N3 without batch 2 ok')
    let rv = vres(ctx)
    t(rv.status === 'ok' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 3, partial: [], failed: [] }) && rv.frames === `${sum(im)}/${sum(im)}`, `return ${J(rv)}`)
    t(J(rv.warnings) === '[]', `warnings ${J(rv.warnings)}`)
    t(callsOf(ctx, 'A1').length === 1, 'a path with \\, //, spaces and upper case should be accepted without a retry')

    // an empty path shows as "?"; the 2nd, partial at the right path, counts as partial: the 1st, at the wrong path, does not count
    ctx = await run(fixture('happy'), { respond: c => {
      if (c.base === 'A2' && c.attempt === 0) return { status: 'partial', report_path: '', frames_seen: im[1] - 1, frames_missing: ['F0031'], notable: '' }
      if (c.base === 'A2') return { status: 'partial', report_path: `${od}/reports/batch_002.md`, frames_seen: im[1] - 4, frames_missing: ['F0030', 'F0031', 'F0032', 'F0033'], notable: '' }
    } })
    t(has(P(ctx, 'A2', 1), `\n\nSECOND ATTEMPT. The previous one opened ${im[1] - 1} of ${im[1]} images (missing: F0031), but wrote the report to ?, not to the requested path. Write the complete report exactly to ${od}/reports/batch_002.md,`), `note 5b with an empty path: ${J(P(ctx, 'A2', 1).slice(P(ctx, 'A2').length))}`)
    rv = vres(ctx)
    t(J(rv.batches && rv.batches.partial) === '[2]' && rv.frames === `${sum(im) - 4}/${sum(im)}` && J(rv.warnings) === '[]', `partial: return ${J(rv)}`)
    t(has(P(ctx, 'N3'), `- batch 002: PARTIAL, ${im[1] - 4}/${im[1]} images → `), 'N3 without the PARTIAL line of the 2nd attempt')
  })

  await scenario('wrong path in both attempts: batch failed with the path warning; the following NEXT carries FAILED', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir, bd = v.batches_dir, im = v.batch_images
    const bad1 = `${a.run_dir}-x/${v.slug}/reports/batch_002.md`, bad2 = `C:/Users/digom/video-analysis/runs/${v.slug}/reports/batch_002.md`
    let ctx = await run(a, { respond: c => (c.base === 'A2' ? { status: 'ok', report_path: c.attempt ? bad2 : bad1, frames_seen: im[1], frames_missing: [], notable: 'x' } : undefined) })
    t(!ctx.error, `error: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,A2,A2,N3,A3,FINAL', `order ${order(ctx)}`)
    contract(ctx, t)
    t(has(P(ctx, 'A2', 1), `, but wrote the report to ${bad1}, not to the requested path. Write the complete report exactly to ${od}/reports/batch_002.md,`), 'A2 2nd without note 5b')
    const lines = P(ctx, 'N3').split('\n').filter(l => l.startsWith('- batch '))
    t(J(lines) === J([`- batch 002: FAILED, no reliable report → ${od}/reports/batch_002.md (speech of the segment: ${bd}/batch_002.md)`]), `N3: ${J(lines)}`)
    let rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 2, partial: [], failed: [2] }) && rv.frames === `${im[0] + im[2]}/${sum(im)}`, `return ${J(rv)}`)
    t(J(rv.warnings) === J([`batch 2: report written to the wrong path (${bad2})`]), `warnings ${J(rv.warnings)}`)
    t(has(P(ctx, 'FINAL'), 'Not yet integrated into the ledger (in this run): 003 (ok);') && has(P(ctx, 'FINAL'), 'Partial: none · Failed: 2\n'), 'FINAL without the failed batch 2')

    // 1st at the wrong path and 2nd without a response: failed, with the path of the 1st
    ctx = await run(fixture('happy'), { respond: c => (c.base === 'A2' ? (c.attempt ? null : { status: 'ok', report_path: bad1, frames_seen: im[1], frames_missing: [], notable: '' }) : undefined) })
    rv = vres(ctx)
    t(J(rv.batches && rv.batches.failed) === '[2]' && J(rv.warnings) === J([`batch 2: report written to the wrong path (${bad1})`]), `no 2nd response: ${J(rv)}`)

    // two batches in a row at the wrong path are two failures in a row of the analyst: video interrupted
    ctx = await run(fixture('happy'), { respond: c => (c.kind === 'A' && c.n <= 2 ? { status: 'ok', report_path: 'C:/outro/lugar.md', frames_seen: im[c.n - 1], frames_missing: [], notable: '' } : undefined) })
    t(order(ctx) === 'INIT,A1,A1,N2,A2,A2', `in a row: order ${order(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'failed' && J(rv.warnings) === J(['batch 1: report written to the wrong path (C:/outro/lugar.md)', 'batch 2: report written to the wrong path (C:/outro/lugar.md)', 'analyst failed in 2 batches in a row: video interrupted']), `in a row: ${J(rv)}`)
    t(has(P(ctx, 'N2'), '- batch 001: FAILED, no reliable report → '), 'N2 without FAILED for batch 1')
  })
}

// ---------------------------------------------------------------- --dump-templates
async function dumpTemplates(outFile) {
  const v1 = { slug: '{slug}', title: '{title}', duration: '{duration}', n_frames: 100, batches_dir: '{batches_dir}', out_dir: '{out_dir}',
    batch_images: [18, 20, 19, 20, 17], start_batch: 1, ledger_exists: false, start_prompt_exists: false, final_exists: false }
  const v2 = { ...v1, slug: '{slug2}', title: '{title2}', duration: '{duration2}', n_frames: 31, batches_dir: '{batches_dir2}', out_dir: '{out_dir2}', batch_images: [15, 16] }
  const ph = { schema_version: 1, run_id: '{run_id}', run_dir: '{run_dir}', objective_path: '{objective_path}', roles_dir: '{roles_dir}',
    roles_mode: 'agents', language: '{language}', options: { max_batches: 3, max_parallel_videos: 1, curation: 'batches' }, combined_exists: false, videos: [v1, v2] }
  const ctx = await run(ph, { respond: c => {
    if (c.slug !== '{slug}') return undefined
    if (c.base === 'A1' && c.attempt === 0) return { status: 'partial', report_path: '{out_dir}/reports/batch_001.md', frames_seen: 16, frames_missing: ['F0007', 'F0012'], notable: '' }
    if (c.base === 'N2' || c.base === 'A2') return null
    if (c.base === 'A3' && c.attempt === 0) return { status: 'ok', report_path: '{where}', frames_seen: 19, frames_missing: [], notable: '' }
  } })
  if (ctx.error) throw ctx.error
  const resume = await run({ ...ph, videos: [{ ...v1, ledger_exists: true, start_batch: 2 }] })
  const inline = await run({ ...ph, roles_mode: 'inline', videos: [v1] })
  const g = (base, k = 0) => P(ctx, base, k, base === 'CROSS' ? undefined : '{slug}')
  const tail = (whole, head) => (whole.startsWith(head + '\n\n') ? whole.slice(head.length + 2) : `(ERROR: does not start with the base prompt + a blank line)\n${whole}`)
  const init = P(inline, 'INIT')
  const blocks = [
    ['1. INIT (curator)', g('INIT')],
    ['2. NEXT (curator)', g('N3')],
    ['3. Analyst, with a curator prompt', g('A1')],
    ['4. Contingency analyst, without a curator prompt', g('A2')],
    ['5. Analyst\'s 2nd-attempt note (suffix; goes after the analyst prompt and a blank line)', tail(g('A1', 1), g('A1'))],
    ['5b. Analyst\'s 2nd-attempt note when the previous one came with a wrong path (suffix, instead of 5)', tail(g('A3', 1), g('A3'))],
    ['6. FINAL (curator)', g('FINAL')],
    ['7. CROSS (curator)', g('CROSS')],
    ['Extra A. NEXT on resume (ledger exists, batch 2 without a prompt)', (resume.calls[0] || {}).prompt || ''],
    ['Extra B. Curator\'s 2nd-attempt note (suffix of INIT, NEXT, FINAL and CROSS; goes after the prompt and a blank line)', tail(g('N2', 1), g('N2'))],
    ['Extra C. Preamble (only in inline mode; goes before every prompt, followed by a blank line; role = curator or analyst)', init.slice(0, init.indexOf('MODE: INIT'))],
  ]
  const md = [
    '# Workflow prompt templates (rendered examples)',
    '',
    'Generated by `node tests/harness.mjs --dump-templates <output>` from `workflow/video-analysis.js`.',
    'The text must be the same as in `reference/sem-workflow.md`, changing only the values.',
    '',
    'Example values: `{…}` are placeholders. Video `{slug}` with 5 batches and images per batch [18, 20, 19, 20, 17]; run limited to batches 1–3 (`max_batches` 3).',
    'Batch 1: the 1st attempt of the analyst opened 16 of 18 images (missing F0007, F0012). Curation of batch 2 failed twice, so the analyst of batch 2 ran in contingency and also failed twice (FAILED).',
    'Batch 3: the 1st attempt of the analyst opened the 19 images, but returned `report_path` = `{where}`, another path.',
    'A 2nd video, `{slug2}`, with 2 batches, exists only for the CROSS. `agents` mode (no preamble); the preamble of inline mode is in Extra C.',
    '',
    ...blocks.flatMap(([title, text]) => [`## ${title}`, '', '````text', text.replace(/\n+$/, ''), '````', '']),
  ].join('\n')
  mkdirSync(path.dirname(path.resolve(outFile)), { recursive: true })
  writeFileSync(outFile, md, 'utf8')
  return blocks.length
}

// ---------------------------------------------------------------- main
const argv = process.argv.slice(2)
const di = argv.indexOf('--dump-templates')
if (di >= 0) {
  const out = argv[di + 1]
  if (!out) { process.stderr.write('usage: node tests/harness.mjs --dump-templates <output.md>\n'); process.exitCode = 2 }
  else {
    const n = await dumpTemplates(out)
    process.stdout.write(`${n} templates written to ${path.resolve(out)}\n`)
  }
} else {
  await allScenarios()
  let failed = 0
  for (const r of RESULTS) {
    process.stdout.write(`${r.fails.length ? 'FAIL' : 'PASS'}  ${r.name}\n`)
    for (const f of r.fails.slice(0, 25)) process.stdout.write(`      - ${f}\n`)
    if (r.fails.length > 25) process.stdout.write(`      … ${r.fails.length - 25} more\n`)
    if (r.fails.length) failed++
  }
  process.stdout.write(`\n${RESULTS.length - failed}/${RESULTS.length} scenarios passed\n`)
  process.exitCode = failed ? 1 : 0
}
