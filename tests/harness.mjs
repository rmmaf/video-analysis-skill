#!/usr/bin/env node
// Testes do workflow/video-analysis.js sem a ferramenta Workflow (Node 24, sem dependências).
//   node tests/harness.mjs                          roda todos os cenários; exit 0 só se todos passarem
//   node tests/harness.mjs --dump-templates <md>    grava um exemplo de cada template de prompt, para comparar
//                                                   com reference/sem-workflow.md
// Mocks: agent() guiado por uma tabela de respostas por cenário; parallel = Promise.all com falha → null;
// budget simulado; Date.now, Math.random e new Date() sem argumento lançam erro durante a execução do script.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SCRIPT = path.resolve(HERE, '..', 'workflow', 'video-analysis.js')
const DATA = path.join(HERE, 'data', 'workflow')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const PHASES = ['Plano', 'Lotes', 'Relatório', 'Síntese']
const PHASE_OF = { INIT: 'Plano', NEXT: 'Lotes', A: 'Lotes', FINAL: 'Relatório', CROSS: 'Síntese' }
const KEY_OF = { INIT: 'video_type', NEXT: 'integrated_through', A: 'frames_seen', FINAL: 'answered', CROSS: 'answered' }
const PREAMBLE_HEAD = 'Antes de qualquer outra ação, leia com Read o arquivo '
const preamble = file => `${PREAMBLE_HEAD}${file}. Ele define seu papel e suas regras: siga-o como suas instruções de sistema (ignore o bloco entre --- no topo). Se não conseguir lê-lo, devolva status "failed" com warnings ["papel ilegível"].\n\n`
const AGAIN = '\n\nSEGUNDA TENTATIVA: a anterior falhou. Se algum arquivo de saída já existir, leia-o antes de sobrescrever (o Write exige) e complete o que faltar.'
const pad = n => String(n).padStart(3, '0')
const clone = o => JSON.parse(JSON.stringify(o))
const sum = a => a.reduce((s, x) => s + x, 0)
const fix = p => String(p).replace(/\\/g, '/').replace(/\/+$/, '')
const has = (s, sub) => typeof s === 'string' && s.includes(sub)
const fixture = name => JSON.parse(readFileSync(path.join(DATA, `args_${name}.json`), 'utf8'))

// ---------------------------------------------------------------- análise estática
const SRC = readFileSync(SCRIPT, 'utf8')

function balanced(s, open) { // recorta o literal {...} que começa em `open`, respeitando strings
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

function metaProblems(lit) { // sem as strings, só podem sobrar chaves, {}, [], vírgulas, dois-pontos, números e booleanos
  const probs = []
  let rest = '', q = null
  for (let i = 0; i < lit.length; i++) {
    const c = lit[i]
    if (q) { if (c === '\\') i++; else if (c === q) { q = null; rest += '§' } continue }
    if (c === '`') { probs.push('template literal'); continue }
    if (c === "'" || c === '"') { q = c; continue }
    if (c === '/' && (lit[i + 1] === '/' || lit[i + 1] === '*')) { probs.push('comentário dentro de meta'); break }
    rest += c
  }
  const toks = rest.match(/[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|[{}[\],:§]|\S/g) || []
  toks.forEach((t, i) => {
    if (/^[A-Za-z_$]/.test(t)) { if (!['true', 'false', 'null'].includes(t) && toks[i + 1] !== ':') probs.push(`identificador fora de chave: ${t}`) }
    else if (!/^(?:\d+(?:\.\d+)?|[{}[\],:§])$/.test(t)) probs.push(`símbolo não literal: ${t}`)
  })
  return probs
}

const FORBIDDEN = [
  [/\bDate\.now\b/, 'Date.now'], [/\bMath\.random\b/, 'Math.random'], [/\bnew\s+Date\s*\(\s*\)/, 'new Date()'],
  [/\brequire\s*\(/, 'require('], [/^\s*import[\s{*]/m, 'import'], [/\bimport\s*\(/, 'import()'],
  [/\bprocess\./, 'process.'], [/\bfs\./, 'fs.'], [/\bglobalThis\b/, 'globalThis'], [/\beval\s*\(/, 'eval('],
  [/\bnew\s+Function\b/, 'new Function'], [/\bset(?:Timeout|Interval|Immediate)\s*\(/, 'timers'], [/\bfetch\s*\(/, 'fetch('],
  [/\bconsole\./, 'console.'], [/\bbudget\.spent\b/, 'budget.spent (soma a sessão inteira, não o run)'],
]
// O Claude Code bloqueia subagentes de gravar arquivos com estes nomes ("Subagents should return findings as text…").
const BLOCKED_NAMES = /(?:^|[\/\\\s`])(?:report|summary|findings)\.md\b/i

let COMPILED = null, COMPILE_ERROR = null
try {
  COMPILED = new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'log', 'phase', 'budget', 'workflow',
    SRC.replace(/^export const meta =/, 'const meta ='))
} catch (e) { COMPILE_ERROR = e }

// ---------------------------------------------------------------- guarda de relógio e aleatoriedade
const RealDate = globalThis.Date, realNow = RealDate.now, realRandom = Math.random
const deny = what => () => { throw new Error(`${what} é proibido no Workflow`) }
const GuardDate = new Proxy(RealDate, {
  construct(t, a, nt) { if (!a.length) throw new Error('new Date() sem argumento é proibido no Workflow'); return Reflect.construct(t, a, nt) },
  apply: deny('Date()'),
})
function guardOn() { RealDate.now = deny('Date.now()'); Math.random = deny('Math.random()'); globalThis.Date = GuardDate }
function guardOff() { RealDate.now = realNow; Math.random = realRandom; globalThis.Date = RealDate }

// ---------------------------------------------------------------- mini validação de JSON Schema (subconjunto usado)
function schemaShape(s, where, errs) {
  if (!s || s.type !== 'object' || !s.properties || typeof s.properties !== 'object') { errs.push(`${where}: schema sem {type:'object', properties}`); return }
  for (const k of s.required || []) if (!(k in s.properties)) errs.push(`${where}: required "${k}" fora de properties`)
  for (const [k, p] of Object.entries(s.properties)) {
    if (p.type === 'object') schemaShape(p, `${where}.${k}`, errs)
    if (p.type === 'array' && p.items && p.items.type === 'object') schemaShape(p.items, `${where}.${k}[]`, errs)
  }
}
function conforms(val, s, where, errs) {
  if (s.type === 'object') {
    if (!val || typeof val !== 'object' || Array.isArray(val)) { errs.push(`${where}: não é objeto`); return }
    for (const k of s.required || []) if (!(k in val)) errs.push(`${where}: falta ${k}`)
    for (const [k, v] of Object.entries(val)) { if (!s.properties[k]) errs.push(`${where}: campo fora do schema: ${k}`); else conforms(v, s.properties[k], `${where}.${k}`, errs) }
  } else if (s.type === 'array') {
    if (!Array.isArray(val)) { errs.push(`${where}: não é lista`); return }
    if (s.items) val.forEach((x, i) => conforms(x, s.items, `${where}[${i}]`, errs))
  } else if (s.type === 'string' && typeof val !== 'string') errs.push(`${where}: não é texto`)
  else if (s.type === 'integer' && !Number.isInteger(val)) errs.push(`${where}: não é inteiro`)
  if (s.enum && !s.enum.includes(val)) errs.push(`${where}: ${JSON.stringify(val)} fora de ${s.enum.join('|')}`)
}

// ---------------------------------------------------------------- execução com mocks
function classify(prompt) {
  const m = /^MODO: (INIT|NEXT|FINAL|CROSS)$/m.exec(prompt)
  const kind = m ? m[1] : 'A'
  let slug = null, n = null, last = null, x
  if (kind === 'A') { if ((x = /^Lote (\d+)\/(\d+) do vídeo ([^\s.]+)\./m.exec(prompt))) { n = +x[1]; last = +x[2]; slug = x[3] } }
  else if (kind !== 'CROSS' && (x = /^Vídeo: (\S+) · /m.exec(prompt))) slug = x[1]
  if (kind === 'NEXT' && (x = /preparar o lote (\d+)\/(\d+)/.exec(prompt))) { n = +x[1]; last = +x[2] }
  const base = kind === 'A' ? `A${n}` : kind === 'NEXT' ? `N${n}` : kind
  return { kind, slug, n, last, base, contingency: kind === 'A' && prompt.includes('Não há prompt do curador'), retry: prompt.includes('SEGUNDA TENTATIVA') }
}

function defaults(c, ctx) { // resposta de sucesso válida para cada modo
  const out = c.video ? fix(c.video.out_dir) : '?'
  switch (c.kind) {
    case 'INIT': return { status: 'ok', ledger_path: `${out}/ledger.md`, prompt_path: `${out}/prompts/batch_001.md`, video_type: 'screen', focus: 'foco do lote 1' }
    case 'NEXT': {
      const integ = [...c.prompt.matchAll(/^- lote (\d{3}):/gm)].map(m => +m[1])
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
    if (typeof prompt !== 'string' || !prompt) ctx.errors.push(`${c.id}: prompt vazio`)
    schemaShape(opts.schema, c.id, ctx.errors)
    ctx.active++; ctx.maxActive = Math.max(ctx.maxActive, ctx.active)
    try {
      if (sc.delay) await new Promise(r => setTimeout(r, sc.delay(c)))
      let val = sc.respond ? sc.respond(c, ctx) : undefined
      if (val === undefined) val = defaults(c, ctx)
      if (val !== null && opts.schema) conforms(val, opts.schema, `${c.id} (resposta)`, ctx.errors)
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
  const workflow = () => { throw new Error('workflow() não é usado por este script') }
  if (!COMPILED) throw new Error(`script não compila: ${COMPILE_ERROR && COMPILE_ERROR.message}`)
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

// Regras de contrato válidas para toda chamada de agente.
function contract(ctx, t, { mode = 'inline', eff = {} } = {}) {
  const rolesDir = fix(ctx.args.roles_dir)
  for (const c of ctx.calls) {
    const o = c.opts, cur = c.kind !== 'A', id = c.id
    t(o.phase === PHASE_OF[c.kind], `${id}: phase "${o.phase}" (esperado ${PHASE_OF[c.kind]})`)
    t(o.schema && (o.schema.required || []).includes(KEY_OF[c.kind]), `${id}: schema não é o do modo ${c.kind}`)
    t(o.model === (cur ? 'opus' : 'sonnet'), `${id}: model ${o.model}`)
    const O = (ctx.args && ctx.args.options) || {}
    const e = { INIT: 'high', FINAL: 'high', CROSS: 'high', NEXT: eff.next || O.curator_effort || 'high', A: eff.analyst || O.analyst_effort || 'high' }[c.kind]
    t(o.effort === e, `${id}: effort ${o.effort} (esperado ${e})`)
    t(typeof o.label === 'string' && o.label.trim() !== '', `${id}: sem label`)
    t(!c.attempt || has(o.label, '· 2ª'), `${id}: retentativa sem "· 2ª" no label`)
    t(c.attempt < 2, `${id}: mais de 2 tentativas`)
    t(cur || !/MODO:/.test(c.prompt), `${id}: prompt do analista com linha MODO`)
    t(!BLOCKED_NAMES.test(c.prompt), `${id}: prompt pede report.md, summary.md ou findings.md (o harness bloqueia subagentes de gravá-los)`)
    const roleFile = `${rolesDir}/video-analysis-${cur ? 'curator' : 'analyst'}.md`
    if (mode === 'agents') {
      t(o.agentType === (cur ? 'video-analysis-curator' : 'video-analysis-analyst'), `${id}: agentType ${o.agentType}`)
      t(!c.prompt.includes(PREAMBLE_HEAD), `${id}: preâmbulo inline no modo agents`)
    } else {
      t(!('agentType' in o), `${id}: agentType no modo inline`)
      t(c.prompt.startsWith(preamble(roleFile)), `${id}: preâmbulo inline ausente ou com papel errado`)
    }
  }
  for (const e of ctx.errors) t(false, e)
}

const RESULTS = []
async function scenario(name, fn) {
  const fails = []
  const t = (ok, msg) => { if (!ok) fails.push(msg) }
  try { await fn(t) } catch (e) { fails.push(`exceção no cenário: ${(e && e.stack) || e}`) }
  RESULTS.push({ name, fails })
}

// ---------------------------------------------------------------- cenários
async function allScenarios() {
  await scenario('estático: meta literal puro, lint de APIs proibidas, compilação, guarda', async t => {
    t(SRC.charCodeAt(0) !== 0xfeff, 'arquivo começa com BOM')
    t(SRC.startsWith('export const meta = {'), 'o script não começa com "export const meta = {"')
    const lit = balanced(SRC, SRC.indexOf('{'))
    t(!!lit, 'literal de meta não fechado')
    if (lit) {
      for (const p of metaProblems(lit)) t(false, `meta não é literal puro: ${p}`)
      let M = null
      try { M = Function(`"use strict"; return (${lit})`)() } catch (e) { t(false, `meta não avalia: ${e.message}`) }
      if (M) {
        t(M.name === 'video-analysis', `meta.name ${M.name}`)
        t(typeof M.description === 'string' && M.description.length > 10, 'meta.description')
        t(M.whenToUse === undefined || typeof M.whenToUse === 'string', 'meta.whenToUse')
        t(Array.isArray(M.phases) && J(M.phases.map(p => p.title)) === J(PHASES), `meta.phases ≠ ${PHASES.join(', ')}`)
        for (const p of M.phases || []) {
          t(typeof p.detail === 'string', `fase ${p.title}: detail`)
          t(p.model === undefined || ['opus', 'sonnet', 'haiku'].includes(p.model), `fase ${p.title}: model`)
          const extra = Object.keys(p).filter(k => !['title', 'detail', 'model'].includes(k))
          t(!extra.length, `fase ${p.title}: chaves extras ${extra}`)
        }
        const extra = Object.keys(M).filter(k => !['name', 'description', 'whenToUse', 'phases'].includes(k))
        t(!extra.length, `meta com chaves extras: ${extra}`)
      }
    }
    for (const [re, what] of FORBIDDEN) t(!re.test(SRC), `API proibida no script: ${what}`)
    t((SRC.match(/\bexport\b/g) || []).length === 1, 'o script deve exportar só meta')
    t(!!COMPILED, `corpo não compila como AsyncFunction: ${COMPILE_ERROR && COMPILE_ERROR.message}`)
    for (const body of ['return Date.now()', 'return Math.random()', 'return new Date()', 'return Date()']) {
      let threw = false
      guardOn()
      try { await new AsyncFunction(body)() } catch (e) { threw = true } finally { guardOff() }
      t(threw, `a guarda não bloqueou: ${body}`)
    }
    let allowed = null
    guardOn()
    try { allowed = await new AsyncFunction('return new Date(0).getTime() + Math.max(1, 2)')() } catch (e) { } finally { guardOff() }
    t(allowed === 2, 'a guarda bloqueou uso permitido (new Date(0), Math.max)')
  })

  await scenario('caminho feliz: INIT, A1, N2, A2, N3, A3, FINAL', async t => {
    const a = fixture('happy'), v = a.videos[0], bd = v.batches_dir, od = v.out_dir, im = v.batch_images
    const ctx = await run(a)
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,A2,N3,A3,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t, { mode: 'inline' })
    const init = P(ctx, 'INIT')
    for (const s of [`\n\nMODO: INIT\nVídeo: ${v.slug} · ${v.duration} · 3 lotes\n`, `Objetivo (literal) e opções: ${a.objective_path}\n`,
      `Resumo do vídeo e dos lotes: ${bd}/summary.json\n`, `Transcrição: ${bd}/transcript.md\n`, `Visão geral (se existir): ${bd}/overview.jpg\n`,
      `Lote 1: linha do tempo ${bd}/batch_001.md · prancha ${bd}/sheet_001.jpg · ${im[0]} imagens\n`, 'Idioma dos relatórios: pt-BR\n',
      `Grave: ledger em ${od}/ledger.md; depois o prompt do lote 1 em ${od}/prompts/batch_001.md.`]) t(has(init, s), `INIT sem: ${J(s)}`)
    t(!has(init, 'este run analisa só'), 'INIT com aviso de run limitado num run completo')
    const a1 = P(ctx, 'A1')
    for (const s of [`Lote 1/3 do vídeo ${v.slug}.\n`, `Prompt do curador: ${od}/prompts/batch_001.md (leia primeiro e siga).\n`,
      `Linha do tempo: ${bd}/batch_001.md\n`, `Relatório: grave em ${od}/reports/batch_001.md\n`, `Imagens do lote: ${im[0]} · Idioma do relatório: pt-BR`]) t(has(a1, s), `A1 sem: ${J(s)}`)
    const n2 = P(ctx, 'N2')
    for (const s of [`MODO: NEXT\nVídeo: ${v.slug} · preparar o lote 2/3\n`, `Objetivo: ${a.objective_path}\nLedger: ${od}/ledger.md\n`, 'Relatórios a integrar:\n',
      `- lote 001: ok, ${im[0]}/${im[0]} imagens → ${od}/reports/batch_001.md\n`,
      `Próximo lote: linha do tempo ${bd}/batch_002.md · prancha ${bd}/sheet_002.jpg · ${im[1]} imagens\n`,
      `Grave: ledger atualizado em ${od}/ledger.md; depois o prompt em ${od}/prompts/batch_002.md.`]) t(has(n2, s), `N2 sem: ${J(s)}`)
    t(!has(n2, 'Confira "Integrado até"'), 'N2 pede a conferência de retomada sem retomada')
    t(has(P(ctx, 'N3'), 'preparar o lote 3/3 (último deste run)\n'), 'N3 sem "(último deste run)"')
    const fin = P(ctx, 'FINAL')
    for (const s of [`MODO: FINAL\nVídeo: ${v.slug} · lotes analisados: 3 de 3\n`, `Relatórios de lote: ${od}/reports/batch_001.md … batch_003.md\n`,
      'Ainda não integrados ao ledger (neste run): 003 (ok); confira também "Integrado até".\n', 'Parciais: nenhum · Falhos: nenhum\n',
      `Imagens vistas: ${sum(im)} de ${sum(im)}\n`, `Transcrição (consulta pontual): ${bd}/transcript.md\nIdioma: pt-BR\n`,
      `Grave o relatório final em ${od}/relatorio.md.`]) t(has(fin, s), `FINAL sem: ${J(s)}`)
    t(!has(fin, 'cobertura parcial'), 'FINAL com cobertura parcial num run completo')
    const R = ctx.result || {}, rv = vres(ctx)
    t(rv.status === 'ok', `status ${rv.status}`)
    t(J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 3, partial: [], failed: [] }), `batches ${J(rv.batches)}`)
    t(rv.frames === `${sum(im)}/${sum(im)}`, `frames ${rv.frames}`)
    t(rv.report_path === `${od}/relatorio.md` && rv.summary === `resumo de ${v.slug}` && rv.answered === 'yes', 'report_path/summary/answered')
    t(J(rv.warnings) === '[]', `avisos inesperados ${J(rv.warnings)}`)
    t(R.agents === 7 && R.roles_mode === 'inline' && R.combined === null && !('output_tokens' in R), `agents ${R.agents}, roles_mode ${R.roles_mode}, combined ${J(R.combined)}, output_tokens ${R.output_tokens}`)
    t(ctx.logs.some(l => has(l, 'plano pronto (screen)')), 'log sem "plano pronto"')
    t(ctx.logs[0] === `run ${a.run_id}: 1 vídeo(s), ~7 agentes, papéis inline, curadoria por lote, até 3 vídeo(s) em paralelo`, `1º log: ${ctx.logs[0]}`)
    t(!ctx.calls.some(c => c.retry), 'retentativa inesperada')
  })

  await scenario('run limitado (max_batches 2 de 4): 5 agentes e cobertura parcial', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir
    a.options.max_batches = 2; v.batch_images = [20, 18, 19, 21]
    const ctx = await run(a)
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,A2,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t)
    t(has(P(ctx, 'INIT'), `· 4 lotes (este run analisa só os lotes 1–2; planeje todos e marque os demais como fora deste run)\n`), 'INIT sem o aviso de run limitado')
    t(has(P(ctx, 'N2'), 'preparar o lote 2/2 (último deste run)'), 'N2 sem "(último deste run)"')
    const fin = P(ctx, 'FINAL')
    t(has(fin, 'lotes analisados: 2 de 4 (cobertura parcial: diga isso no relatório)\n'), 'FINAL sem cobertura parcial')
    t(has(fin, `${od}/reports/batch_001.md … batch_002.md\n`) && has(fin, 'Imagens vistas: 38 de 38\n'), 'FINAL com faixa de relatórios ou imagens errada')
    const rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 4, planned: 2, from: 1, ok: 2, partial: [], failed: [] }) && rv.frames === '38/38', `retorno ${J(rv)}`)
    t(ctx.logs.some(l => has(l, 'modo teste, lotes 1–2 de 4')), 'log sem "modo teste"')
    t(has(ctx.logs[0], '~5 agentes'), `estimativa: ${ctx.logs[0]}`)
  })

  await scenario('analista com imagens faltando: complemento com os ids; parcial vai ao NEXT', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir, im = v.batch_images
    const ctx = await run(a, { respond: c => {
      if (c.base === 'A1' && c.attempt === 0) return { status: 'partial', report_path: `${od}/reports/batch_001.md`, frames_seen: im[0] - 2, frames_missing: ['F0007', 'F0012'], notable: 'x' }
      if (c.base === 'A2') return { status: 'partial', report_path: `${od}/reports/batch_002.md`, frames_seen: im[1] - 3, frames_missing: ['F0030', 'F0031', 'F0033'], notable: 'y' }
      if (c.base === 'A3' && c.attempt === 0) return null
    } })
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,A1,N2,A2,A2,N3,A3,A3,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t)
    const a1 = P(ctx, 'A1'), a1b = P(ctx, 'A1', 1)
    t(a1b.startsWith(a1 + '\n\nSEGUNDA TENTATIVA. '), 'a 2ª tentativa do A1 não é o prompt base + complemento')
    t(has(a1b, `SEGUNDA TENTATIVA. A anterior abriu ${im[0] - 2} de ${im[0]} imagens (faltaram: F0007, F0012). Se ${od}/reports/batch_001.md existir, leia-o antes de sobrescrever; abra as imagens que faltaram (na dúvida, todas) e grave o relatório completo.`), `complemento do A1: ${J(a1b.slice(a1.length))}`)
    t(has(P(ctx, 'A3', 1), 'SEGUNDA TENTATIVA. A anterior não terminou. Se '), 'A3 2ª sem "não terminou"')
    const n3 = P(ctx, 'N3')
    t(has(n3, `- lote 002: PARCIAL, ${im[1] - 3}/${im[1]} imagens → ${od}/reports/batch_002.md\n`), 'N3 sem a linha PARCIAL')
    t(!has(n3, 'falas do trecho'), 'PARCIAL não leva "falas do trecho"')
    const rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 2, partial: [2], failed: [] }), `retorno ${J(rv)}`)
    t(rv.frames === `${sum(im) - 3}/${sum(im)}`, `frames ${rv.frames}`)
    t(has(P(ctx, 'FINAL'), 'Parciais: 2 · Falhos: nenhum\n') && has(P(ctx, 'FINAL'), `Imagens vistas: ${sum(im) - 3} de ${sum(im)}\n`), 'FINAL sem parciais/imagens')
    t(ctx.logs.some(l => has(l, `1/3: ${im[0] - 2}/${im[0]} imagens, partial; repetindo`)), 'log da repetição')
  })

  await scenario('curadoria falha (NEXT nulo 2×): analista em contingência; o NEXT seguinte lista 2 pendentes com FALHOU', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir, bd = v.batches_dir, im = v.batch_images
    const ctx = await run(a, { respond: c => (c.base === 'N2' || c.base === 'A2' ? null : undefined) })
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,N2,A2,A2,N3,A3,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t)
    t(P(ctx, 'N2', 1) === P(ctx, 'N2') + AGAIN, 'a 2ª tentativa do NEXT não é o prompt + nota AGAIN')
    const a2 = callsOf(ctx, 'A2')[0] || {}
    t(a2.contingency && has(a2.prompt, `Lote 2/3 do vídeo ${v.slug}. Não há prompt do curador para este lote: siga o contrato padrão do seu papel.\nObjetivo: ${a.objective_path}. Contexto: seções 4 e 5 de ${od}/ledger.md, se existir.\nLinha do tempo: ${bd}/batch_002.md\nRelatório: grave em ${od}/reports/batch_002.md\nImagens do lote: ${im[1]} · Idioma do relatório: pt-BR`), 'prompt de contingência do A2')
    t(!has(a2.prompt, 'Prompt do curador:'), 'contingência cita prompt do curador')
    const lines = P(ctx, 'N3').split('\n').filter(l => l.startsWith('- lote '))
    t(lines.length === 2, `N3 deveria listar 2 pendentes: ${J(lines)}`)
    t(lines[0] === `- lote 001: ok, ${im[0]}/${im[0]} imagens → ${od}/reports/batch_001.md`, `1ª pendente: ${lines[0]}`)
    t(lines[1] === `- lote 002: FALHOU, sem relatório confiável → ${od}/reports/batch_002.md (falas do trecho: ${bd}/batch_002.md)`, `2ª pendente: ${lines[1]}`)
    t(!(callsOf(ctx, 'A3')[0] || {}).contingency, 'A3 deveria ter prompt do curador')
    const rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 2, partial: [], failed: [2] }), `retorno ${J(rv)}`)
    t((rv.warnings || []).includes('curadoria do lote 2 falhou: prompt de contingência'), `avisos ${J(rv.warnings)}`)
    t(has(P(ctx, 'FINAL'), 'Parciais: nenhum · Falhos: 2\n') && has(P(ctx, 'FINAL'), `Imagens vistas: ${im[0] + im[2]} de ${sum(im)}\n`), 'FINAL sem falhos/imagens')
  })

  await scenario('duas falhas seguidas interrompem o vídeo (curador, analista, INIT)', async t => {
    const a = fixture('happy'); a.videos[0].batch_images = [20, 18, 19, 21]
    let ctx = await run(a, { respond: c => (c.base === 'N2' || c.base === 'N3' ? null : undefined) })
    t(order(ctx) === 'INIT,A1,N2,N2,A2,N3,N3', `curador: ordem ${order(ctx)}`)
    let rv = vres(ctx)
    t(rv.status === 'failed' && rv.report_path === null && rv.summary === null, `curador: retorno ${J(rv)}`)
    t((rv.warnings || []).includes('curador falhou em 2 lotes seguidos: vídeo interrompido'), `curador: avisos ${J(rv.warnings)}`)
    t(J(rv.batches) === J({ total: 4, planned: 4, from: 1, ok: 2, partial: [], failed: [] }), `curador: batches ${J(rv.batches)}`)
    contract(ctx, t)

    const failA = { status: 'failed', report_path: '', frames_seen: 0, frames_missing: [], notable: '', warnings: ['papel ilegível'] }
    ctx = await run(fixture('happy'), { respond: c => (c.base === 'A1' ? failA : c.base === 'A2' ? null : undefined) })
    t(order(ctx) === 'INIT,A1,A1,N2,A2,A2', `analista: ordem ${order(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'failed' && J(rv.batches && rv.batches.failed) === '[1,2]', `analista: retorno ${J(rv)}`)
    t((rv.warnings || []).includes('lote 1: análise falhou (papel ilegível)'), `analista: motivo ausente ${J(rv.warnings)}`)
    t((rv.warnings || []).includes('analista falhou em 2 lotes seguidos: vídeo interrompido'), `analista: avisos ${J(rv.warnings)}`)
    t(has(P(ctx, 'N2'), '- lote 001: FALHOU, sem relatório confiável → '), 'N2 sem FALHOU do lote 1')
    contract(ctx, t)

    const failI = { status: 'failed', ledger_path: '', prompt_path: '', video_type: 'other', focus: '', warnings: ['papel ilegível'] }
    ctx = await run(fixture('happy'), { respond: c => (c.kind === 'INIT' ? failI : undefined) })
    t(order(ctx) === 'INIT,INIT', `INIT: ordem ${order(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'failed' && (rv.warnings || []).includes('plano (INIT) falhou duas vezes (papel ilegível)'), `INIT: retorno ${J(rv)}`)
    t(P(ctx, 'INIT', 1) === P(ctx, 'INIT') + AGAIN, 'INIT 2ª sem nota AGAIN')
    contract(ctx, t)

    const nc = fixture('happy'); nc.videos[0].batch_images = [20, 18, 19, 21, 17]
    ctx = await run(nc, { respond: c => (['A1', 'N2', 'A3', 'N4'].includes(c.base) ? null : undefined) })
    t(order(ctx) === 'INIT,A1,A1,N2,N2,A2,N3,A3,A3,N4,N4,A4,N5,A5,FINAL', `falhas não seguidas não interrompem: ordem ${order(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 5, planned: 5, from: 1, ok: 3, partial: [], failed: [1, 3] }), `falhas não seguidas: retorno ${J(rv)}`)
    t((callsOf(ctx, 'A2')[0] || {}).contingency && (callsOf(ctx, 'A4')[0] || {}).contingency && !(callsOf(ctx, 'A5')[0] || {}).contingency, 'contingência só onde a curadoria falhou')

    const failF = { status: 'failed', report_path: '', answered: 'no', summary: 'ledger ilegível' }
    ctx = await run(fixture('happy'), { respond: c => (c.kind === 'FINAL' ? failF : undefined) })
    rv = vres(ctx)
    t(order(ctx) === 'INIT,A1,N2,A2,N3,A3,FINAL,FINAL', `FINAL: ordem ${order(ctx)}`)
    t(rv.status === 'failed' && (rv.warnings || []).includes('relatório final falhou duas vezes (ledger ilegível): rode /video-analysis retomar'), `FINAL: retorno ${J(rv)}`)
  })

  await scenario('retomada: ledger presente e lote 3 sem prompt → 1ª chamada é NEXT 3 com "Integrado até"; casos de §3.3', async t => {
    const a = fixture('resume'), v = a.videos[0], od = v.out_dir, im = v.batch_images
    let ctx = await run(a)
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'N3,A3,N4,A4,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t)
    const n3 = (ctx.calls[0] || {}).prompt || ''
    t(has(n3, 'MODO: NEXT\n') && has(n3, 'preparar o lote 3/4\n'), 'a 1ª chamada não é NEXT 3')
    t(has(n3, `Confira "Integrado até" no ledger e integre os relatórios existentes em ${od}/reports até o lote 002.\n`), 'NEXT 3 sem "Integrado até"')
    t(!has(n3, 'Relatórios a integrar:'), 'NEXT 3 lista pendentes inexistentes')
    t(!has(P(ctx, 'N4'), 'Confira "Integrado até"') && has(P(ctx, 'N4'), `- lote 003: ok, ${im[2]}/${im[2]} imagens → `), 'NEXT 4')
    let rv = vres(ctx)
    const seen = im[2] + im[3]
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 5, planned: 4, from: 3, ok: 2, partial: [], failed: [] }) && rv.frames === `${seen}/${seen}`, `retorno ${J(rv)}`)
    const fin = P(ctx, 'FINAL')
    t(has(fin, 'lotes analisados: 4 de 5 (cobertura parcial: diga isso no relatório)\n') && has(fin, '… batch_004.md\n'), 'FINAL da retomada')
    t(has(fin, `Imagens vistas: ${seen} de ${seen} nos lotes 003–004 deste run; lotes 001–002: run anterior (veja o ledger)\n`), 'FINAL sem imagens vistas da retomada')
    t(has(ctx.logs[0], '~5 agentes'), `estimativa ${ctx.logs[0]}`)

    const b = fixture('resume'); b.videos[0].start_prompt_exists = true
    ctx = await run(b)
    t(order(ctx) === 'A3,N4,A4,FINAL', `prompt existente: ordem ${order(ctx)}`)
    t(has(P(ctx, 'A3'), `Prompt do curador: ${od}/prompts/batch_003.md (leia primeiro e siga).`), 'A3 sem o prompt do curador')

    const c2 = fixture('resume'); c2.videos[0].start_batch = 5
    ctx = await run(c2)
    t(order(ctx) === 'FINAL', `tudo analisado: ordem ${order(ctx)}`)
    t(has(P(ctx, 'FINAL'), 'Ainda não integrados ao ledger (neste run): nenhum; confira também "Integrado até".\n'), 'FINAL sem "nenhum"')
    t(has(P(ctx, 'FINAL'), 'Imagens vistas: nenhum lote novo neste run; lotes 001–004: run anterior (veja o ledger)\n'), 'FINAL sem imagens da retomada vazia')
    rv = vres(ctx)
    t(rv.frames === null && rv.batches && rv.batches.from === 5 && rv.batches.planned === 4 && rv.status === 'partial', `tudo analisado: retorno ${J(rv)}`)

    const d = fixture('resume'); d.videos[0].ledger_exists = false
    ctx = await run(d)
    t(order(ctx).startsWith('INIT,A1,N2,'), `sem ledger: ordem ${order(ctx)}`)
  })

  await scenario('curadoria padrão (plan): INIT, A1, A2, A3, FINAL; analistas 2+ em contingência; analista em high', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir
    delete a.options.curation; delete a.options.analyst_effort
    const ctx = await run(a)
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,A2,A3,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t)
    t(callsOf(ctx, 'A1').every(c => c.opts.effort === 'high'), 'analista sem esforço high por padrão')
    t(!(callsOf(ctx, 'A1')[0] || {}).contingency && has(P(ctx, 'A1'), `Prompt do curador: ${od}/prompts/batch_001.md (leia primeiro e siga).`), 'A1 sem o prompt do INIT')
    for (const n of [2, 3]) {
      const p = P(ctx, `A${n}`)
      t(has(p, `Lote ${n}/3 do vídeo ${v.slug}. Não há prompt do curador para este lote: siga o contrato padrão do seu papel.\n`)
        && has(p, `Objetivo: ${a.objective_path}. Contexto: seções 4 e 5 de ${od}/ledger.md, se existir.\n`), `A${n} sem o texto de contingência`)
    }
    t(has(P(ctx, 'FINAL'), 'Ainda não integrados ao ledger (neste run): 001 (ok), 002 (ok), 003 (ok); confira também "Integrado até".\n'), 'FINAL sem os 3 lotes pendentes')
    const R = ctx.result || {}, rv = vres(ctx)
    t(rv.status === 'ok' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 3, partial: [], failed: [] }) && J(rv.warnings) === '[]', `retorno ${J(rv)}`)
    t(R.agents === 5, `agents ${R.agents}`)
    t(ctx.logs[0] === `run ${a.run_id}: 1 vídeo(s), ~5 agentes, papéis inline, curadoria só no plano, até 3 vídeo(s) em paralelo`, `1º log: ${ctx.logs[0]}`)
    const b = fixture('happy'); b.options.curation = 'plan'
    t(order(await run(b)) === 'INIT,A1,A2,A3,FINAL', 'curation "plan" difere do padrão')
    const c = fixture('happy'); c.options.curation = 'lotes'
    const cc = await run(c)
    t(!!cc.error && has(errMsg(cc), 'options.curation') && !cc.calls.length, `curation inválida aceita: ${errMsg(cc)}`)
  })

  await scenario('retomada sem curadoria por lote: lote 3 sem prompt → analista de contingência, sem NEXT', async t => {
    const a = fixture('resume'); delete a.options.curation
    let ctx = await run(a)
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'A3,A4,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t)
    t(ctx.calls.slice(0, 2).every(c => c.contingency), 'A3/A4 fora da contingência')
    t(has(P(ctx, 'FINAL'), 'Ainda não integrados ao ledger (neste run): 003 (ok), 004 (ok); confira também "Integrado até".\n'), 'FINAL da retomada sem os pendentes')
    t(has(ctx.logs[0], '~3 agentes'), `estimativa ${ctx.logs[0]}`)   // retomada com ledger: sem INIT
    const b = fixture('resume'); delete b.options.curation; b.videos[0].start_prompt_exists = true
    ctx = await run(b)
    t(order(ctx) === 'A3,A4,FINAL' && !ctx.calls[0].contingency && ctx.calls[1].contingency, `prompt existente: ordem ${order(ctx)}`)
  })

  await scenario('final_exists: vídeo pulado sem agentes', async t => {
    const a = fixture('happy'), v = a.videos[0]; v.final_exists = true
    const ctx = await run(a)
    t(!ctx.error && ctx.calls.length === 0, `chamadas ${order(ctx)} ${errMsg(ctx)}`)
    const rv = vres(ctx)
    t(rv.status === 'skipped' && rv.report_path === `${v.out_dir}/relatorio.md` && rv.frames === null, `retorno ${J(rv)}`)
    t(J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 0, partial: [], failed: [] }), `batches ${J(rv.batches)}`)
    t(ctx.result.agents === 0 && ctx.result.combined === null, 'agents/combined')
    t(ctx.logs.some(l => has(l, 'vídeo pulado')), 'log sem "pulado"')
  })

  await scenario('3 vídeos com paralelismo 2: nunca mais de 2 agentes simultâneos; CROSS no fim', async t => {
    const a = fixture('multi')
    const ctx = await run(a, { delay: c => 1 + ((c.idx * 7) % 5) })
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(ctx.maxActive <= 2, `pico de ${ctx.maxActive} agentes simultâneos`)
    t(ctx.maxActive === 2, 'os vídeos não rodaram em paralelo')
    for (const v of a.videos) t(order(ctx, v.slug) === 'INIT,A1,N2,A2,FINAL', `${v.slug}: ${order(ctx, v.slug)}`)
    const inits = a.videos.map(v => (callsOf(ctx, 'INIT', v.slug)[0] || {}).idx)
    t(inits[0] < 2 && inits[1] < 2 && inits[2] >= 5, `o 3º vídeo deveria esperar uma vaga: ${J(inits)}`)
    const cross = ctx.calls.filter(c => c.kind === 'CROSS')
    t(cross.length === 1 && ctx.calls[ctx.calls.length - 1].kind === 'CROSS', 'CROSS deve rodar uma vez, por último')
    const cp = (cross[0] || {}).prompt || ''
    t(has(cp, `MODO: CROSS\nRun: ${a.run_id}\nObjetivo: ${a.objective_path}\nRelatórios por vídeo:\n`), 'cabeçalho do CROSS')
    for (const v of a.videos) t(has(cp, `\n- ${v.slug}: ${v.out_dir}/relatorio.md (ok)\n`), `CROSS sem ${v.slug}`)
    t(has(cp, `Idioma: pt-BR\nGrave o relatório combinado em ${a.run_dir}/relatorio.md.`), 'rodapé do CROSS')
    const R = ctx.result || {}
    t(J(R.combined) === J({ report_path: `${a.run_dir}/relatorio.md`, answered: 'yes', summary: 'resumo combinado' }), `combined ${J(R.combined)}`)
    t(R.agents === 16 && R.roles_mode === 'agents', `agents ${R.agents}`)
    contract(ctx, t, { mode: 'agents' })
    const long = a.videos[0].slug
    t(ctx.calls.filter(c => c.slug === long).every(c => has(c.opts.label, long.slice(0, 23) + '…')), 'rótulo do slug longo não foi abreviado')
    t(has(ctx.logs[0], '~16 agentes') && has(ctx.logs[0], 'até 2 vídeo(s) em paralelo'), `estimativa ${ctx.logs[0]}`)
  })

  await scenario('CROSS só com 2+ relatórios', async t => {
    let ctx = await run(fixture('happy'))
    t(!ctx.calls.some(c => c.kind === 'CROSS') && ctx.result.combined === null, '1 vídeo não tem CROSS')

    const two = fixture('multi'); two.videos = two.videos.slice(0, 2)
    ctx = await run(two, { respond: c => (c.kind === 'INIT' && c.slug === two.videos[1].slug ? null : undefined) })
    t(!ctx.calls.some(c => c.kind === 'CROSS') && ctx.result.combined === null, 'com 1 relatório não deveria haver CROSS')
    t(vres(ctx, 1).status === 'failed' && vres(ctx, 0).status === 'ok', 'status dos dois vídeos')

    const sk = fixture('multi'); sk.videos = sk.videos.slice(0, 2); sk.videos[0].final_exists = true
    ctx = await run(sk)
    const cp = P(ctx, 'CROSS')
    t(callsOf(ctx, 'CROSS').length === 1 && has(cp, `- ${sk.videos[0].slug}: ${sk.videos[0].out_dir}/relatorio.md (skipped)\n`) && has(cp, `- ${sk.videos[1].slug}: ${sk.videos[1].out_dir}/relatorio.md (ok)\n`), 'vídeo pulado entra no CROSS')
    t(has(ctx.logs[0], '~6 agentes'), `estimativa ${ctx.logs[0]}`)

    const ce = fixture('multi'); ce.combined_exists = true
    ctx = await run(ce)
    t(!ctx.calls.some(c => c.kind === 'CROSS') && ctx.result.combined === null, 'combined_exists não deveria rodar CROSS')

    ctx = await run(fixture('multi'), { respond: c => (c.kind === 'CROSS' ? { status: 'failed', report_path: '', answered: 'no', summary: 'relatório de vídeo ilegível' } : undefined) })
    t(callsOf(ctx, 'CROSS').length === 2 && P(ctx, 'CROSS', 1) === P(ctx, 'CROSS') + AGAIN, 'CROSS repetido com nota AGAIN')
    t(J(ctx.result.combined) === J({ error: 'síntese falhou duas vezes (relatório de vídeo ilegível)' }), `combined ${J(ctx.result.combined)}`)
  })

  await scenario('guarda de tamanho: recusa acima de 800 agentes', async t => {
    const a = fixture('happy'); a.videos[0].batch_images = Array.from({ length: 400 }, () => 20)
    let ctx = await run(a)
    t(/^run grande demais \(~801 agentes; teto do Workflow 1000\)/.test(errMsg(ctx)), `erro: ${errMsg(ctx)}`)
    t(ctx.calls.length === 0, 'chamou agentes antes de recusar')
    a.options.max_batches = 10
    ctx = await run(a)
    t(!ctx.error && ctx.calls.length === 21, `com max_batches 10: ${ctx.calls.length} chamadas ${errMsg(ctx)}`)
    const b = fixture('happy'); b.videos[0].batch_images = Array.from({ length: 399 }, () => 20)
    ctx = await run(b)
    t(!ctx.error && ctx.calls.length === 799, `399 lotes (799 agentes) deveria passar: ${ctx.calls.length} ${errMsg(ctx)}`)
  })

  await scenario('modos de papéis: agents passa agentType+model+effort; inline tem preâmbulo e não tem agentType', async t => {
    const a = fixture('happy'); a.roles_mode = 'agents'
    let ctx = await run(a)
    t(!ctx.error && ctx.result.roles_mode === 'agents', `agents: ${errMsg(ctx)}`)
    contract(ctx, t, { mode: 'agents' })
    t(ctx.calls.every(c => c.opts.agentType && c.opts.model && c.opts.effort), 'agents: falta agentType/model/effort')
    ctx = await run(fixture('happy'))
    contract(ctx, t, { mode: 'inline' })
    t(ctx.calls.every(c => c.opts.model && c.opts.effort && !('agentType' in c.opts)), 'inline: opções')
    const roles = fixture('happy').roles_dir
    t(has(P(ctx, 'INIT'), preamble(`${roles}/video-analysis-curator.md`) + 'MODO: INIT\n'), 'inline: preâmbulo do curador seguido da linha MODO')
    t(has(P(ctx, 'A1'), preamble(`${roles}/video-analysis-analyst.md`) + 'Lote 1/3 do vídeo '), 'inline: preâmbulo do analista')
    const e = fixture('happy'); e.options.analyst_effort = 'medium'; e.options.curator_effort = 'low'
    ctx = await run(e)
    contract(ctx, t, { mode: 'inline', eff: { next: 'low', analyst: 'medium' } })
    const m = fixture('happy'); delete m.roles_mode; delete m.options
    ctx = await run(m)
    t(!ctx.error && ctx.result.roles_mode === 'inline', 'sem roles_mode/options → inline')
    contract(ctx, t, { mode: 'inline' })
  })

  await scenario('formato do retorno', async t => {
    for (const name of ['happy', 'multi', 'resume']) {
      const a = fixture(name), ctx = await run(a), R = ctx.result
      t(R && J(Object.keys(R)) === J(['run_id', 'run_dir', 'roles_mode', 'agents', 'videos', 'combined']), `${name}: chaves ${R && Object.keys(R)}`)
      if (!R) continue
      t(R.run_id === a.run_id && R.run_dir === a.run_dir && typeof R.agents === 'number', `${name}: run_id/run_dir/agents`)
      t(R.videos.length === a.videos.length, `${name}: nº de vídeos`)
      for (const rv of R.videos) {
        t(J(Object.keys(rv)) === J(['slug', 'title', 'status', 'report_path', 'answered', 'summary', 'batches', 'frames', 'warnings']), `${name}: chaves do vídeo ${Object.keys(rv)}`)
        t(['ok', 'partial', 'failed', 'skipped'].includes(rv.status), `${name}: status ${rv.status}`)
        t(rv.batches && J(Object.keys(rv.batches)) === J(['total', 'planned', 'from', 'ok', 'partial', 'failed']), `${name}: chaves de batches`)
        t(rv.batches && typeof rv.batches.ok === 'number' && Array.isArray(rv.batches.partial) && Array.isArray(rv.batches.failed), `${name}: tipos de batches`)
        t(/^\d+\/\d+$/.test(rv.frames), `${name}: frames ${rv.frames}`)
        t(Array.isArray(rv.warnings) && rv.warnings.length <= 6, `${name}: warnings`)
        t(rv.title === (a.videos.find(v => v.slug === rv.slug) || {}).title, `${name}: title`)
      }
      t(J(R).length < 6000, `${name}: retorno grande demais (${J(R).length} caracteres)`)
    }
    const w = fixture('happy'); w.videos[0].batch_images = [20, 18, 19, 20, 17, 16, 15, 14, 13]
    const ctx = await run(w, { respond: c => (c.kind === 'NEXT' ? { status: 'ok', integrated_through: c.n - 1, coverage: [{ batch: c.n - 1, state: 'incomplete' }], prompt_path: 'x', focus: 'f' } : undefined) })
    const ws = vres(ctx).warnings || []
    t(ws.length === 6 && ws[4] === '+3 avisos omitidos' && ws[0] === 'lote 1: relatório incompleto' && ws[5] === 'lote 8: relatório incompleto', `avisos sem corte explícito: ${J(ws)}`)
    const f = fixture('happy'); f.videos[0].batch_images = [20, 18, 19, 20, 17, 16, 15, 14, 13]
    const cf = await run(f, { respond: c => {
      if (c.kind === 'NEXT' && c.n <= 7) return { status: 'ok', integrated_through: c.n - 1, coverage: [{ batch: c.n - 1, state: 'incomplete' }], prompt_path: 'x', focus: 'f' }
      if (c.base === 'A7' || c.base === 'A8') return null
    } })
    const wf = vres(cf).warnings || []
    t(vres(cf).status === 'failed' && wf.length === 6 && wf[4] === '+2 avisos omitidos' && wf[5] === 'analista falhou em 2 lotes seguidos: vídeo interrompido', `o motivo da interrupção não sobreviveu ao corte de avisos: ${J(wf)}`)
  })

  await scenario('validação de workflow_args: o erro nomeia as chaves', async t => {
    const a = fixture('happy'), v = a.videos[0]
    a.schema_version = 2; v.cache_dir = v.batches_dir; delete v.batches_dir; delete v.title
    v.batch_frames = v.batch_images; delete v.batch_images; v.final_exists = 'nao'
    let ctx = await run(a)
    t(ctx.error && ctx.calls.length === 0, 'deveria recusar sem chamar agentes')
    for (const k of ['schema_version', 'videos[0].batches_dir (cache_dir é o nome antigo)', 'videos[0].title', 'videos[0].batch_images (batch_frames é o nome antigo)', 'videos[0].final_exists', 'rode prepare.py e passe workflow_args como objeto JSON, sem editar'])
      t(has(errMsg(ctx), k), `erro sem "${k}": ${errMsg(ctx)}`)
    const b = fixture('happy'); b.options.analyst_effort = 'alto'; b.options.max_parallel_videos = 0; b.roles_mode = 'agent'; b.videos[0].batch_images = [20, 0]
    ctx = await run(b)
    for (const k of ['options.analyst_effort', 'options.max_parallel_videos', 'roles_mode', 'videos[0].batch_images']) t(has(errMsg(ctx), k), `erro sem "${k}": ${errMsg(ctx)}`)
    t(!has(errMsg(ctx), 'schema_version'), 'acusou chave válida')
    const c = fixture('multi'); c.videos[1].slug = c.videos[0].slug
    ctx = await run(c)
    t(has(errMsg(ctx), 'videos[1].slug (repetido)'), `slug repetido: ${errMsg(ctx)}`)
    for (const bad of [undefined, '{não é json', [1, 2]]) { ctx = await run(bad); t(has(errMsg(ctx), 'inválido (args)'), `args ${J(bad)}: ${errMsg(ctx)}`) }
    const e = fixture('happy'); e.videos = []
    ctx = await run(e)
    t(has(errMsg(ctx), 'inválido (videos)'), `videos vazio: ${errMsg(ctx)}`)
    ctx = await run(JSON.stringify(fixture('happy')))
    t(!ctx.error && order(ctx) === 'INIT,A1,N2,A2,N3,A3,FINAL', `args como texto JSON: ${order(ctx)} ${errMsg(ctx)}`)
    const f = fixture('happy'), clean = fixture('happy').videos[0]
    f.videos[0].batches_dir = f.videos[0].batches_dir.replace(/\//g, '\\') + '\\'; f.videos[0].out_dir += '/'
    ctx = await run(f)
    t(has(P(ctx, 'A1'), `Linha do tempo: ${clean.batches_dir}/batch_001.md\nRelatório: grave em ${clean.out_dir}/reports/batch_001.md`), 'caminhos com \\ e barra final não foram normalizados')
    t(!ctx.calls.some(c2 => c2.prompt.includes('\\') || /[^:]\/\//.test(c2.prompt)), 'prompt com \\ ou //')
    t(J(vres(ctx).warnings) === '[]', `avisos com caminhos normalizados: ${J(vres(ctx).warnings)}`)
  })

  await scenario('orçamento no fim, erro de agente, auditoria de cobertura e caminho de relatório', async t => {
    let ctx = await run(fixture('happy'), { budget: c => ({ total: 1000000, spent: () => 1, remaining: () => (c.calls.length >= 4 ? 50000 : 900000) }) })
    t(order(ctx) === 'INIT,A1,N2,A2,FINAL', `orçamento: ordem ${order(ctx)}`)
    let rv = vres(ctx)
    t(rv.status === 'partial' && rv.batches.planned === 2 && (rv.warnings || []).includes('orçamento de tokens no fim: parou antes do lote 3'), `orçamento: retorno ${J(rv)}`)
    t(has(P(ctx, 'FINAL'), 'lotes analisados: 2 de 3 (cobertura parcial: diga isso no relatório)\n') && has(P(ctx, 'FINAL'), '… batch_002.md\n'), 'orçamento: FINAL')
    t(!('output_tokens' in ctx.result), 'o retorno não traz output_tokens (budget.spent() soma a sessão inteira)')

    const two = fixture('multi'); two.videos = two.videos.slice(0, 2)
    ctx = await run(two, { respond: c => { if (c.slug === two.videos[0].slug && c.base === 'A2') throw new Error('orçamento esgotado') } })
    t(!ctx.error, `erro de agente não deveria derrubar o workflow: ${errMsg(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'failed' && (rv.warnings || []).includes('erro: orçamento esgotado') && rv.batches && rv.batches.ok === 1, `erro: retorno ${J(rv)}`)
    t(vres(ctx, 1).status === 'ok' && !ctx.calls.some(c => c.kind === 'CROSS') && ctx.result.combined === null, 'o outro vídeo segue; sem CROSS')

    ctx = await run(fixture('happy'), { respond: c => {
      if (c.base === 'N2') return { status: 'ok', integrated_through: 1, coverage: [{ batch: 1, state: 'incomplete' }], prompt_path: 'x', focus: 'f' }
      if (c.base === 'N3') return { status: 'ok', integrated_through: 1, coverage: [{ batch: 2, state: 'missing' }], prompt_path: 'x', focus: 'f' }
    } })
    rv = vres(ctx)
    t(J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 1, partial: [1], failed: [2] }) && rv.status === 'partial', `cobertura: retorno ${J(rv)}`)
    for (const w of ['lote 1: relatório incompleto', 'lote 2: relatório ausente', 'ledger integrado só até o lote 1 ao preparar o 3']) t((rv.warnings || []).includes(w), `cobertura: falta aviso "${w}"`)

    const h = fixture('happy'), od = h.videos[0].out_dir
    ctx = await run(h, { respond: c => {
      if (c.base === 'A1') return { status: 'ok', report_path: `${od}/reports/batch_001.md`.replace(/\//g, '\\').toUpperCase(), frames_seen: 20, frames_missing: [], notable: '' }
      if (c.base === 'A2') return { status: 'ok', report_path: 'C:/outro/batch_002.md', frames_seen: 18, frames_missing: [], notable: '' }
    } })
    t(order(ctx) === 'INIT,A1,N2,A2,A2,N3,A3,FINAL', `caminho: ordem ${order(ctx)}`)
    t(J(vres(ctx).warnings) === J(['lote 2: relatório gravado em caminho errado (C:/outro/batch_002.md)']), `caminho: avisos ${J(vres(ctx).warnings)}`)
  })

  await scenario('caminho errado corrigido na 2ª tentativa: nota 5b com os dois caminhos; lote ok, sem aviso', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir, im = v.batch_images
    // como no run real 20261006-172520: caminho montado a partir do run_id e do slug, não copiado da chamada
    const bad = `${a.run_dir}-gip-portal-mira-20261002-095428/${v.slug}/reports/batch_002.md`
    let ctx = await run(a, { respond: c => {
      if (c.base === 'A1') return { status: 'ok', report_path: ` ${od}//reports/batch_001.md `.replace(/\//g, '\\').toUpperCase(), frames_seen: im[0], frames_missing: [], notable: '' }
      if (c.base === 'A2' && c.attempt === 0) return { status: 'ok', report_path: bad.replace(/\//g, '\\'), frames_seen: im[1], frames_missing: [], notable: 'x' }
    } })
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,A2,A2,N3,A3,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t)
    const a2 = P(ctx, 'A2'), a2b = P(ctx, 'A2', 1)
    t(a2b === a2 + `\n\nSEGUNDA TENTATIVA. A anterior abriu ${im[1]} de ${im[1]} imagens, mas gravou o relatório em ${bad}, e não no caminho pedido. Grave o relatório completo exatamente em ${od}/reports/batch_002.md, copiando o caminho caractere por caractere, e devolva esse mesmo caminho em report_path. Antes, leia o que existir nesses dois caminhos e aproveite o que estiver certo; abra as imagens que faltaram (na dúvida, todas).`, `nota 5b: ${J(a2b.slice(a2.length))}`)
    t(ctx.logs.some(l => has(l, `2/3: ${im[1]}/${im[1]} imagens, ok, relatório em ${bad}; repetindo`)), 'log da repetição por caminho errado')
    t(has(P(ctx, 'N3'), `- lote 002: ok, ${im[1]}/${im[1]} imagens → ${od}/reports/batch_002.md\n`), 'N3 sem o lote 2 ok')
    let rv = vres(ctx)
    t(rv.status === 'ok' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 3, partial: [], failed: [] }) && rv.frames === `${sum(im)}/${sum(im)}`, `retorno ${J(rv)}`)
    t(J(rv.warnings) === '[]', `avisos ${J(rv.warnings)}`)
    t(callsOf(ctx, 'A1').length === 1, 'caminho com \\, //, espaços e maiúsculas deveria valer sem repetir')

    // caminho vazio aparece como "?"; a 2ª, parcial no caminho certo, vale como parcial: a 1ª, no caminho errado, não conta
    ctx = await run(fixture('happy'), { respond: c => {
      if (c.base === 'A2' && c.attempt === 0) return { status: 'partial', report_path: '', frames_seen: im[1] - 1, frames_missing: ['F0031'], notable: '' }
      if (c.base === 'A2') return { status: 'partial', report_path: `${od}/reports/batch_002.md`, frames_seen: im[1] - 4, frames_missing: ['F0030', 'F0031', 'F0032', 'F0033'], notable: '' }
    } })
    t(has(P(ctx, 'A2', 1), `\n\nSEGUNDA TENTATIVA. A anterior abriu ${im[1] - 1} de ${im[1]} imagens (faltaram: F0031), mas gravou o relatório em ?, e não no caminho pedido. Grave o relatório completo exatamente em ${od}/reports/batch_002.md,`), `nota 5b com caminho vazio: ${J(P(ctx, 'A2', 1).slice(P(ctx, 'A2').length))}`)
    rv = vres(ctx)
    t(J(rv.batches && rv.batches.partial) === '[2]' && rv.frames === `${sum(im) - 4}/${sum(im)}` && J(rv.warnings) === '[]', `parcial: retorno ${J(rv)}`)
    t(has(P(ctx, 'N3'), `- lote 002: PARCIAL, ${im[1] - 4}/${im[1]} imagens → `), 'N3 sem a linha PARCIAL da 2ª tentativa')
  })

  await scenario('caminho errado nas duas tentativas: lote failed com o aviso do caminho; o NEXT seguinte traz FALHOU', async t => {
    const a = fixture('happy'), v = a.videos[0], od = v.out_dir, bd = v.batches_dir, im = v.batch_images
    const bad1 = `${a.run_dir}-x/${v.slug}/reports/batch_002.md`, bad2 = `C:/Users/digom/video-analysis/runs/${v.slug}/reports/batch_002.md`
    let ctx = await run(a, { respond: c => (c.base === 'A2' ? { status: 'ok', report_path: c.attempt ? bad2 : bad1, frames_seen: im[1], frames_missing: [], notable: 'x' } : undefined) })
    t(!ctx.error, `erro: ${errMsg(ctx)}`)
    t(order(ctx) === 'INIT,A1,N2,A2,A2,N3,A3,FINAL', `ordem ${order(ctx)}`)
    contract(ctx, t)
    t(has(P(ctx, 'A2', 1), `, mas gravou o relatório em ${bad1}, e não no caminho pedido. Grave o relatório completo exatamente em ${od}/reports/batch_002.md,`), 'A2 2ª sem a nota 5b')
    const lines = P(ctx, 'N3').split('\n').filter(l => l.startsWith('- lote '))
    t(J(lines) === J([`- lote 002: FALHOU, sem relatório confiável → ${od}/reports/batch_002.md (falas do trecho: ${bd}/batch_002.md)`]), `N3: ${J(lines)}`)
    let rv = vres(ctx)
    t(rv.status === 'partial' && J(rv.batches) === J({ total: 3, planned: 3, from: 1, ok: 2, partial: [], failed: [2] }) && rv.frames === `${im[0] + im[2]}/${sum(im)}`, `retorno ${J(rv)}`)
    t(J(rv.warnings) === J([`lote 2: relatório gravado em caminho errado (${bad2})`]), `avisos ${J(rv.warnings)}`)
    t(has(P(ctx, 'FINAL'), 'Ainda não integrados ao ledger (neste run): 003 (ok);') && has(P(ctx, 'FINAL'), 'Parciais: nenhum · Falhos: 2\n'), 'FINAL sem o lote 2 falho')

    // 1ª no caminho errado e 2ª sem resposta: failed, com o caminho da 1ª
    ctx = await run(fixture('happy'), { respond: c => (c.base === 'A2' ? (c.attempt ? null : { status: 'ok', report_path: bad1, frames_seen: im[1], frames_missing: [], notable: '' }) : undefined) })
    rv = vres(ctx)
    t(J(rv.batches && rv.batches.failed) === '[2]' && J(rv.warnings) === J([`lote 2: relatório gravado em caminho errado (${bad1})`]), `sem 2ª resposta: ${J(rv)}`)

    // dois lotes seguidos no caminho errado são duas falhas seguidas do analista: vídeo interrompido
    ctx = await run(fixture('happy'), { respond: c => (c.kind === 'A' && c.n <= 2 ? { status: 'ok', report_path: 'C:/outro/lugar.md', frames_seen: im[c.n - 1], frames_missing: [], notable: '' } : undefined) })
    t(order(ctx) === 'INIT,A1,A1,N2,A2,A2', `seguidos: ordem ${order(ctx)}`)
    rv = vres(ctx)
    t(rv.status === 'failed' && J(rv.warnings) === J(['lote 1: relatório gravado em caminho errado (C:/outro/lugar.md)', 'lote 2: relatório gravado em caminho errado (C:/outro/lugar.md)', 'analista falhou em 2 lotes seguidos: vídeo interrompido']), `seguidos: ${J(rv)}`)
    t(has(P(ctx, 'N2'), '- lote 001: FALHOU, sem relatório confiável → '), 'N2 sem FALHOU do lote 1')
  })
}

// ---------------------------------------------------------------- --dump-templates
async function dumpTemplates(outFile) {
  const v1 = { slug: '{slug}', title: '{titulo}', duration: '{duracao}', n_frames: 100, batches_dir: '{batches_dir}', out_dir: '{out_dir}',
    batch_images: [18, 20, 19, 20, 17], start_batch: 1, ledger_exists: false, start_prompt_exists: false, final_exists: false }
  const v2 = { ...v1, slug: '{slug2}', title: '{titulo2}', duration: '{duracao2}', n_frames: 31, batches_dir: '{batches_dir2}', out_dir: '{out_dir2}', batch_images: [15, 16] }
  const ph = { schema_version: 1, run_id: '{run_id}', run_dir: '{run_dir}', objective_path: '{objective_path}', roles_dir: '{roles_dir}',
    roles_mode: 'agents', language: '{idioma}', options: { max_batches: 3, max_parallel_videos: 1, curation: 'batches' }, combined_exists: false, videos: [v1, v2] }
  const ctx = await run(ph, { respond: c => {
    if (c.slug !== '{slug}') return undefined
    if (c.base === 'A1' && c.attempt === 0) return { status: 'partial', report_path: '{out_dir}/reports/batch_001.md', frames_seen: 16, frames_missing: ['F0007', 'F0012'], notable: '' }
    if (c.base === 'N2' || c.base === 'A2') return null
    if (c.base === 'A3' && c.attempt === 0) return { status: 'ok', report_path: '{onde}', frames_seen: 19, frames_missing: [], notable: '' }
  } })
  if (ctx.error) throw ctx.error
  const resume = await run({ ...ph, videos: [{ ...v1, ledger_exists: true, start_batch: 2 }] })
  const inline = await run({ ...ph, roles_mode: 'inline', videos: [v1] })
  const g = (base, k = 0) => P(ctx, base, k, base === 'CROSS' ? undefined : '{slug}')
  const tail = (whole, head) => (whole.startsWith(head + '\n\n') ? whole.slice(head.length + 2) : `(ERRO: não começa com o prompt base + linha em branco)\n${whole}`)
  const init = P(inline, 'INIT')
  const blocks = [
    ['1. INIT (curador)', g('INIT')],
    ['2. NEXT (curador)', g('N3')],
    ['3. Analista (com prompt do curador)', g('A1')],
    ['4. Analista em contingência (sem prompt do curador)', g('A2')],
    ['5. Analista: segunda tentativa (sufixo; vai depois do prompt do analista e de uma linha em branco)', tail(g('A1', 1), g('A1'))],
    ['5b. Analista: segunda tentativa quando a anterior gravou em outro caminho (sufixo, no lugar do 5)', tail(g('A3', 1), g('A3'))],
    ['6. FINAL (curador)', g('FINAL')],
    ['7. CROSS (curador)', g('CROSS')],
    ['Extra A. NEXT na retomada (ledger existe, lote 2 sem prompt)', (resume.calls[0] || {}).prompt || ''],
    ['Extra B. Curador: segunda tentativa (sufixo de INIT, NEXT, FINAL e CROSS; vai depois do prompt e de uma linha em branco)', tail(g('N2', 1), g('N2'))],
    ['Extra C. Preâmbulo do modo inline (vai antes de todo prompt, seguido de uma linha em branco; papel = curator ou analyst)', init.slice(0, init.indexOf('MODO: INIT'))],
  ]
  const md = [
    '# Templates de prompt do workflow (exemplos renderizados)',
    '',
    'Gerado por `node tests/harness.mjs --dump-templates <saída>` a partir de `workflow/video-analysis.js`.',
    'O texto deve ser igual ao de `reference/sem-workflow.md`, trocando só os valores.',
    '',
    'Valores de exemplo: `{…}` são marcadores. Vídeo `{slug}` com 5 lotes e imagens por lote [18, 20, 19, 20, 17]; run limitado aos lotes 1–3 (`max_batches` 3).',
    'Lote 1: a 1ª tentativa do analista abriu 16 de 18 imagens (faltaram F0007, F0012). Curadoria do lote 2 falhou duas vezes, então o analista do lote 2 rodou em contingência e também falhou duas vezes (FALHOU).',
    'Lote 3: a 1ª tentativa do analista abriu as 19 imagens, mas devolveu `report_path` = `{onde}`, outro caminho.',
    'Um 2º vídeo, `{slug2}`, com 2 lotes, existe só para o CROSS. Modo `agents` (sem preâmbulo); o preâmbulo do modo inline está no Extra C.',
    '',
    ...blocks.flatMap(([title, text]) => [`## ${title}`, '', '````text', text.replace(/\n+$/, ''), '````', '']),
  ].join('\n')
  mkdirSync(path.dirname(path.resolve(outFile)), { recursive: true })
  writeFileSync(outFile, md, 'utf8')
  return blocks.length
}

// ---------------------------------------------------------------- principal
const argv = process.argv.slice(2)
const di = argv.indexOf('--dump-templates')
if (di >= 0) {
  const out = argv[di + 1]
  if (!out) { process.stderr.write('uso: node tests/harness.mjs --dump-templates <saida.md>\n'); process.exitCode = 2 }
  else {
    const n = await dumpTemplates(out)
    process.stdout.write(`${n} templates gravados em ${path.resolve(out)}\n`)
  }
} else {
  await allScenarios()
  let failed = 0
  for (const r of RESULTS) {
    process.stdout.write(`${r.fails.length ? 'FAIL' : 'PASS'}  ${r.name}\n`)
    for (const f of r.fails.slice(0, 25)) process.stdout.write(`      - ${f}\n`)
    if (r.fails.length > 25) process.stdout.write(`      … mais ${r.fails.length - 25}\n`)
    if (r.fails.length) failed++
  }
  process.stdout.write(`\n${RESULTS.length - failed}/${RESULTS.length} cenários passaram\n`)
  process.exitCode = failed ? 1 : 0
}
