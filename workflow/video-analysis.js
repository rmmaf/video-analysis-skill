export const meta = {
  name: 'video-analysis',
  description: 'Analisa vídeos em lotes sequenciais: curador Opus planeja (e, com curation batches, escreve o prompt de cada lote), analista Sonnet lê frames e falas; relatório por vídeo e, com 2+ vídeos, combinado',
  whenToUse: 'Só pela skill video-analysis, com os workflow_args impressos por prepare.py',
  phases: [
    { title: 'Plano', detail: 'curador lê objetivo e transcrição; grava ledger e prompt do lote 1', model: 'opus' },
    { title: 'Lotes', detail: 'por vídeo, em sequência: analista (sonnet) de cada lote; com curation batches, curador (opus) do lote seguinte' },
    { title: 'Relatório', detail: 'relatorio.md de cada vídeo', model: 'opus' },
    { title: 'Síntese', detail: 'relatorio.md combinado quando há 2+ vídeos', model: 'opus' },
  ],
}

// Workflow da skill video-analysis. Entrada: workflow_args v1 impressos por scripts/prepare.py (reference/contratos.md).
// Toda troca entre agentes passa por arquivos; os agentes devolvem objetos mínimos validados por schema.
// Os textos dos prompts têm espelho em reference/sem-workflow.md: mude os dois juntos (tests/harness.mjs --dump-templates).

const LV = ['low', 'medium', 'high', 'xhigh', 'max']
const CUR = ['plan', 'batches']
// Relatório final (por vídeo e combinado). Nunca report.md, summary.md ou findings.md: o Claude Code bloqueia subagentes de gravar esses nomes.
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
        for (const k of ['slug', 'title', 'batches_dir', 'out_dir']) if (!str(v[k])) bad.push(`${at}.${k}${k === 'batches_dir' && v.cache_dir ? ' (cache_dir é o nome antigo)' : ''}`)
        if (!Array.isArray(v.batch_images) || !v.batch_images.length || !v.batch_images.every(posInt)) bad.push(`${at}.batch_images${v.batch_frames ? ' (batch_frames é o nome antigo)' : ''}`)
        if (!opt(v.start_batch, posInt)) bad.push(`${at}.start_batch`)
        for (const k of ['ledger_exists', 'start_prompt_exists', 'final_exists']) if (!opt(v[k], bool)) bad.push(`${at}.${k}`)
        if (str(v.slug)) { if (slugs.has(v.slug)) bad.push(`${at}.slug (repetido)`); slugs.add(v.slug) }
      })
    }
  }
  if (bad.length) throw new Error(`workflow_args inválido (${bad.join(', ')}): rode prepare.py e passe workflow_args como objeto JSON, sem editar`)
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
// Curadoria por lote (NEXT) só com curation 'batches'; no padrão 'plan', o INIT planeja e os analistas dos lotes 2+ usam o contrato padrão.
const NEXT = O.curation === 'batches'
const ROLE = {
  curator: { type: 'video-analysis-curator', model: 'opus', file: `${A.roles_dir}/video-analysis-curator.md` },
  analyst: { type: 'video-analysis-analyst', model: 'sonnet', file: `${A.roles_dir}/video-analysis-analyst.md` },
}

const STR = { type: 'string' }, STRS = { type: 'array', items: { type: 'string' } }
const S_INIT = { type: 'object', required: ['status', 'ledger_path', 'prompt_path', 'video_type', 'focus'], properties: {
  status: { type: 'string', enum: ['ok', 'failed'] }, ledger_path: STR, prompt_path: STR,
  video_type: { type: 'string', enum: ['screen', 'meeting', 'slides', 'mixed', 'other'] },
  focus: { type: 'string', description: 'foco do lote 1, 1 frase' }, warnings: STRS } }
const S_NEXT = { type: 'object', required: ['status', 'integrated_through', 'coverage', 'prompt_path', 'focus'], properties: {
  status: { type: 'string', enum: ['ok', 'failed'] },
  integrated_through: { type: 'integer', description: 'último lote cujo relatório está integrado ao ledger' },
  coverage: { type: 'array', description: 'um item por relatório integrado nesta chamada', items: { type: 'object', required: ['batch', 'state'],
    properties: { batch: { type: 'integer' }, state: { type: 'string', enum: ['complete', 'incomplete', 'missing'] } } } },
  prompt_path: STR, focus: { type: 'string', description: 'foco do lote preparado, 1 frase' },
  carried: { type: 'string', description: 'o que foi passado adiante, ≤ 25 palavras' }, warnings: STRS } }
const S_ANALYST = { type: 'object', required: ['status', 'report_path', 'frames_seen', 'frames_missing', 'notable'], properties: {
  status: { type: 'string', enum: ['ok', 'partial', 'failed'] }, report_path: STR,
  frames_seen: { type: 'integer', description: 'imagens do lote abertas com Read, somando a tentativa anterior se esta for a segunda' },
  frames_missing: { type: 'array', items: { type: 'string' }, description: 'ids dos frames do lote cuja imagem nenhuma tentativa abriu' },
  notable: { type: 'string', description: 'o mais relevante para o objetivo neste lote, ≤ 2 frases, sem dado sensível' }, warnings: STRS } }
const S_FINAL = { type: 'object', required: ['status', 'report_path', 'answered', 'summary'], properties: {
  status: { type: 'string', enum: ['ok', 'partial', 'failed'] }, report_path: STR,
  answered: { type: 'string', enum: ['yes', 'partly', 'no'] },
  summary: { type: 'string', description: 'resposta direta ao objetivo, ≤ 120 palavras (em falha: o motivo)' }, gaps: STRS } }

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
  else text = `Antes de qualquer outra ação, leia com Read o arquivo ${r.file}. Ele define seu papel e suas regras: siga-o como suas instruções de sistema (ignore o bloco entre --- no topo). Se não conseguir lê-lo, devolva status "failed" com warnings ["papel ilegível"].\n\n${prompt}`
  calls++
  return agent(text, opts)
}
// Até 2 tentativas; em falha, guarda em fail.reason o motivo informado pelo agente (warnings ou, no FINAL/CROSS, summary).
async function twice(make, ok, fail = {}) {
  for (let k = 0; k < 2; k++) {
    const r = await make(k)
    if (r && ok(r)) return r
    if (r && reasonOf(r)) fail.reason = reasonOf(r)
  }
  return null
}
const because = why => (why.reason ? ` (${why.reason})` : '')
const AGAIN = '\n\nSEGUNDA TENTATIVA: a anterior falhou. Se algum arquivo de saída já existir, leia-o antes de sobrescrever (o Write exige) e complete o que faltar.'

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
  'MODO: INIT',
  `Vídeo: ${v.slug} · ${v.duration || '?'} · ${B} lotes${last < B ? ` (este run analisa só os lotes 1–${last}; planeje todos e marque os demais como fora deste run)` : ''}`,
  `Objetivo (literal) e opções: ${A.objective_path}`,
  `Resumo do vídeo e dos lotes: ${P.summary}`,
  `Transcrição: ${P.transcript}`,
  `Visão geral (se existir): ${P.overview}`,
  `Lote 1: linha do tempo ${P.timeline(1)} · prancha ${P.contact(1)} · ${v.batch_images[0]} imagens`,
  `Idioma dos relatórios: ${A.language}`,
  `Grave: ledger em ${P.ledger}; depois o prompt do lote 1 em ${P.prompt(1)}.`,
].join('\n')

const stateText = p => p.state === 'ok' ? `ok, ${p.seen}/${p.expected} imagens`
  : p.state === 'partial' ? `PARCIAL, ${p.seen}/${p.expected} imagens` : 'FALHOU, sem relatório confiável'

function nextPrompt(v, P, n, last, pending, resumed) {
  const L = ['MODO: NEXT', `Vídeo: ${v.slug} · preparar o lote ${n}/${last}${n === last ? ' (último deste run)' : ''}`,
    `Objetivo: ${A.objective_path}`, `Ledger: ${P.ledger}`]
  if (pending.length) {
    L.push('Relatórios a integrar:')
    for (const p of pending) L.push(`- lote ${pad(p.n)}: ${stateText(p)} → ${P.report(p.n)}${p.state === 'failed' ? ` (falas do trecho: ${P.timeline(p.n)})` : ''}`)
  }
  if (n > 1 && (resumed || !pending.length)) L.push(`Confira "Integrado até" no ledger e integre os relatórios existentes em ${P.reports} até o lote ${pad(n - 1)}.`)
  L.push(`Próximo lote: linha do tempo ${P.timeline(n)} · prancha ${P.contact(n)} · ${v.batch_images[n - 1]} imagens`,
    `Grave: ledger atualizado em ${P.ledger}; depois o prompt em ${P.prompt(n)}.`)
  return L.join('\n')
}

function analystPrompt(v, P, n, last, curated) {
  const L = curated
    ? [`Lote ${n}/${last} do vídeo ${v.slug}.`, `Prompt do curador: ${P.prompt(n)} (leia primeiro e siga).`]
    : [`Lote ${n}/${last} do vídeo ${v.slug}. Não há prompt do curador para este lote: siga o contrato padrão do seu papel.`,
       `Objetivo: ${A.objective_path}. Contexto: seções 4 e 5 de ${P.ledger}, se existir.`]
  L.push(`Linha do tempo: ${P.timeline(n)}`, `Relatório: grave em ${P.report(n)}`,
    `Imagens do lote: ${v.batch_images[n - 1]} · Idioma do relatório: ${A.language}`)
  return L.join('\n')
}
// `onde`: caminho que a 1ª tentativa devolveu quando não é o pedido (nota 5b de reference/sem-workflow.md); vazio = nota 5.
function analystAgain(r, expected, path, onde) {
  const did = r ? `abriu ${r.frames_seen} de ${expected} imagens${(r.frames_missing || []).length ? ` (faltaram: ${r.frames_missing.join(', ')})` : ''}` : 'não terminou'
  return onde
    ? `\n\nSEGUNDA TENTATIVA. A anterior ${did}, mas gravou o relatório em ${onde}, e não no caminho pedido. Grave o relatório completo exatamente em ${path}, copiando o caminho caractere por caractere, e devolva esse mesmo caminho em report_path. Antes, leia o que existir nesses dois caminhos e aproveite o que estiver certo; abra as imagens que faltaram (na dúvida, todas).`
    : `\n\nSEGUNDA TENTATIVA. A anterior ${did}. Se ${path} existir, leia-o antes de sobrescrever; abra as imagens que faltaram (na dúvida, todas) e grave o relatório completo.`
}

const seenText = res => res.from <= 1 ? `Imagens vistas: ${res.seen} de ${res.expected}`
  : res.planned >= res.from ? `Imagens vistas: ${res.seen} de ${res.expected} nos lotes ${pad(res.from)}–${pad(res.planned)} deste run; lotes 001–${pad(res.from - 1)}: run anterior (veja o ledger)`
  : `Imagens vistas: nenhum lote novo neste run; lotes 001–${pad(res.planned)}: run anterior (veja o ledger)`

const finalPrompt = (v, P, B, last, res, pending) => [
  'MODO: FINAL',
  `Vídeo: ${v.slug} · lotes analisados: ${res.planned} de ${B}${res.planned < B ? ' (cobertura parcial: diga isso no relatório)' : ''}`,
  `Objetivo: ${A.objective_path}`, `Ledger: ${P.ledger}`,
  `Relatórios de lote: ${P.reports}/batch_001.md … batch_${pad(last)}.md`,
  `Ainda não integrados ao ledger (neste run): ${pending.length ? pending.map(p => `${pad(p.n)} (${p.state})`).join(', ') : 'nenhum'}; confira também "Integrado até".`,
  `Parciais: ${res.partial.join(', ') || 'nenhum'} · Falhos: ${res.failed.join(', ') || 'nenhum'}`,
  seenText(res),
  `Transcrição (consulta pontual): ${P.transcript}`, `Idioma: ${A.language}`,
  `Grave o relatório final em ${P.final}.`,
].join('\n')

const crossPrompt = reps => ['MODO: CROSS', `Run: ${A.run_id}`, `Objetivo: ${A.objective_path}`, 'Relatórios por vídeo:',
  ...reps.map(r => `- ${r.slug}: ${r.report_path} (${r.status})`),
  `Idioma: ${A.language}`, `Grave o relatório combinado em ${A.run_dir}/${FINAL_MD}.`].join('\n')

async function analyze(v, P, n, last, curated, tag) {
  const expected = v.batch_images[n - 1], path = P.report(n)
  const base = analystPrompt(v, P, n, last, curated)
  // Caminho errado: resposta sem status failed cujo report_path não é o pedido; o curador não acharia o relatório.
  const wrong = r => !!r && r.status !== 'failed' && norm(r.report_path) !== norm(path)
  const where = r => String(r.report_path || '').trim().replace(/\\/g, '/') || '?'
  const good = r => r && r.status !== 'failed' && !wrong(r) && r.frames_seen >= expected && !(r.frames_missing || []).length
  const opts = k => ({ label: `análise · ${tag} ${n}/${last}${k ? ' · 2ª' : ''}`, phase: 'Lotes', schema: S_ANALYST, effort: EFF.analyst })
  const out = (r, state, extra) => ({ n, state, expected, seen: r ? Math.min(r.frames_seen, expected) : 0, reason: '', wrong_path: null,
    notable: (r && r.notable) || '', ...extra })
  const r1 = await call('analyst', base, opts(0))
  if (good(r1)) return out(r1, 'ok')
  log(`${tag} ${n}/${last}: ${r1 ? `${r1.frames_seen}/${expected} imagens, ${r1.status}${wrong(r1) ? `, relatório em ${where(r1)}` : ''}` : 'analista sem resposta'}; repetindo`)
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
  if (v.final_exists) { log(`${tag}: ${FINAL_MD} já existe; vídeo pulado`); return { ...res, status: 'skipped', report_path: P.final } }
  // Erro inesperado (ex.: orçamento esgotado faz agent() lançar) vira falha do vídeo, sem perder o progresso já contado.
  try { await steps(v, P, B, last, tag, res) }
  catch (e) { res.status = 'failed'; res.warnings.push(`erro: ${(e && e.message) || e}`) }
  return res
}

async function steps(v, P, B, last, tag, res) {
  if (last < B) log(`${tag}: modo teste, lotes 1–${last} de ${B}`)
  const start = v.ledger_exists ? Math.max(1, Number(v.start_batch) || 1) : 1
  res.from = start
  let ready, pending = [], resumed = !!v.ledger_exists && start > 1, curFail = 0, anaFail = 0

  if (!v.ledger_exists) {
    const why = {}
    const r = await twice(k => call('curator', initPrompt(v, P, B, last) + (k ? AGAIN : ''),
      { label: `plano · ${tag}${k ? ' · 2ª' : ''}`, phase: 'Plano', schema: S_INIT, effort: EFF.init }), r => r.status === 'ok', why)
    if (!r) { res.status = 'failed'; res.warnings.push(`plano (INIT) falhou duas vezes${because(why)}`); return }
    ready = true
    log(`${tag}: plano pronto (${r.video_type}); lote 1: ${r.focus}`)
  } else ready = !!v.start_prompt_exists

  for (let n = start; n <= last; n++) {
    if (budget.total && budget.remaining() < 80000) { res.warnings.push(`orçamento de tokens no fim: parou antes do lote ${n}`); res.planned = n - 1; break }
    let curated = ready
    ready = false
    if (!curated && NEXT) {
      const why = {}
      const r = await twice(k => call('curator', nextPrompt(v, P, n, last, pending, resumed) + (k ? AGAIN : ''),
        { label: `curadoria · ${tag} ${n}/${last}${k ? ' · 2ª' : ''}`, phase: 'Lotes', schema: S_NEXT, effort: EFF.next }), r => r.status === 'ok', why)
      if (r) {
        curated = true; pending = []; resumed = false; curFail = 0
        for (const c of r.coverage || []) if (c.state !== 'complete') { demote(res, c.batch, c.state); res.warnings.push(`lote ${c.batch}: relatório ${c.state === 'missing' ? 'ausente' : 'incompleto'}`) }
        if (r.integrated_through < n - 1) res.warnings.push(`ledger integrado só até o lote ${r.integrated_through} ao preparar o ${n}`)
      } else {
        res.warnings.push(`curadoria do lote ${n} falhou${because(why)}: prompt de contingência`)
        if (++curFail >= 2) { res.status = 'failed'; res.warnings.push('curador falhou em 2 lotes seguidos: vídeo interrompido'); return }
      }
    }
    const a = await analyze(v, P, n, last, curated, tag)
    res[a.state].push(n); res.seen += a.seen; res.expected += a.expected
    if (a.wrong_path) res.warnings.push(`lote ${n}: relatório gravado em caminho errado (${a.wrong_path})`)
    else if (a.state === 'failed' && a.reason) res.warnings.push(`lote ${n}: análise falhou (${a.reason})`)
    pending.push(a)
    log(`${tag} ${n}/${last} ${a.state} ${a.seen}/${a.expected}${a.notable ? ' — ' + a.notable.slice(0, 140) : ''}`)
    if (a.state === 'failed') { if (++anaFail >= 2) { res.status = 'failed'; res.warnings.push('analista falhou em 2 lotes seguidos: vídeo interrompido'); return } }
    else anaFail = 0
  }

  if (res.planned < 1) { res.status = 'failed'; res.warnings.push('nenhum lote analisado: relatório final não gerado'); return }
  const why = {}
  const fin = await twice(k => call('curator', finalPrompt(v, P, B, res.planned, res, pending) + (k ? AGAIN : ''),
    { label: `relatório · ${tag}${k ? ' · 2ª' : ''}`, phase: 'Relatório', schema: S_FINAL, effort: EFF.final }), r => r.status !== 'failed', why)
  if (!fin) { res.status = 'failed'; res.warnings.push(`relatório final falhou duas vezes${because(why)}: rode /video-analysis retomar`); return }
  Object.assign(res, { report_path: fin.report_path || P.final, summary: fin.summary, answered: fin.answered })
  if (res.partial.length || res.failed.length || fin.status === 'partial' || res.planned < B) res.status = 'partial'
}

const remaining = v => (v.final_exists ? 0 : Math.max(0, lastOf(v) - (v.ledger_exists ? Math.max(1, Number(v.start_batch) || 1) : 1) + 1))
const planned = A.videos.reduce((s, v) => s + (v.final_exists ? 0 : NEXT ? 2 * remaining(v) + 1 : remaining(v) + (v.ledger_exists ? 1 : 2)), 0) + (A.videos.length > 1 ? 1 : 0)
log(`run ${A.run_id}: ${A.videos.length} vídeo(s), ~${planned} agentes, papéis ${MODE}, curadoria ${NEXT ? 'por lote' : 'só no plano'}, até ${PAR} vídeo(s) em paralelo`)
if (planned > 800) throw new Error(`run grande demais (~${planned} agentes; teto do Workflow 1000): divida em runs menores ou use max_batches/trecho`)

const queue = A.videos.map((v, i) => ({ v, i }))
const results = new Array(A.videos.length)
await parallel(Array.from({ length: Math.min(PAR, queue.length) }, () => async () => {
  while (queue.length) {
    const { v, i } = queue.shift()
    try { results[i] = await runVideo(v) }
    catch (e) { results[i] = { slug: v.slug, title: v.title, status: 'failed', warnings: [`erro: ${(e && e.message) || e}`] } }
  }
}))
const R = results.map((r, i) => r || { slug: A.videos[i].slug, title: A.videos[i].title, status: 'failed', warnings: ['não executado'] })

let combined = null
const reps = R.filter(r => r.report_path && r.status !== 'failed')
if (reps.length > 1 && !A.combined_exists) {
  const why = {}
  const c = await twice(k => call('curator', crossPrompt(reps) + (k ? AGAIN : ''),
    { label: `síntese${k ? ' · 2ª' : ''}`, phase: 'Síntese', schema: S_FINAL, effort: EFF.cross }), r => r.status !== 'failed', why)
  combined = c ? { report_path: c.report_path || `${A.run_dir}/${FINAL_MD}`, answered: c.answered, summary: c.summary } : { error: `síntese falhou duas vezes${because(why)}` }
}
// Sem contagem de tokens no retorno: o spent() do budget soma a sessão inteira, não este run.
// Até 6 avisos: com mais, os 4 primeiros, a contagem dos omitidos e o último (num vídeo interrompido, o motivo).
const capW = w => (w.length > 6 ? [...w.slice(0, 4), `+${w.length - 5} avisos omitidos`, w[w.length - 1]] : w)
return {
  run_id: A.run_id, run_dir: A.run_dir, roles_mode: MODE, agents: calls,
  videos: R.map(r => ({ slug: r.slug, title: r.title || null, status: r.status, report_path: r.report_path || null, answered: r.answered || null, summary: r.summary || null,
    batches: r.total ? { total: r.total, planned: r.planned, from: r.from, ok: r.ok.length, partial: r.partial, failed: r.failed } : null,
    frames: r.expected ? `${r.seen}/${r.expected}` : null, warnings: capW(r.warnings || []) })),
  combined,
}
