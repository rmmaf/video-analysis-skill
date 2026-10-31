# Contratos: pedido, prepare, workflow_args, schemas, retorno e pastas

Fonte da verdade: o "Contrato unificado" de `docs/plano.md`. Os schemas e o retorno valem como estão em
`workflow/video-analysis.js`; esta página os repete para consulta. Todo caminho em JSON e nos `.md`
gerados é absoluto e usa `/` (`C:/Users/digom/...`).

## 1. `pedido.md`

O agente principal grava com Write em `C:/Users/digom/video-analysis/pedidos/pedido.md`. Nada passa pelo
shell: caminho com espaço e objetivo com aspas ou dois-pontos não precisam de escape.

```markdown
# videos
C:/Users/digom/Downloads/venda_zero_clientes.mp4

# objetivo
Explique o indicador "venda zero clientes": o que é pedido, de onde vem cada dado citado e o que
aparece na tela quando cada fonte é mencionada

# opcoes
papeis: inline
max_lotes: 2
```

- Seções: exatamente as linhas `# videos`, `# objetivo` e `# opcoes`.
- `# videos`: um caminho por linha (arquivo, pasta, ou curinga com `*`/`?`), sem aspas. Pasta: só os
  vídeos dela, sem subpastas.
- `# objetivo`: o texto do usuário, literal, em uma ou mais linhas. Vai para `objective.md` do run.
- `# opcoes`: `chave: valor`, uma por linha; o valor é o resto da linha depois do primeiro `:`.
  `papeis` sempre; `idioma` quando o objetivo não está em português; as demais, só as dadas.

| chave | valores | padrão | opção do usuário |
|---|---|---|---|
| `papeis` | `agents`, `inline` | escolhido pelo principal (passo 2) | `--papeis` |
| `max_lotes` | inteiro ≥ 1 (`todos` ou `0` = sem limite) | sem limite | `--max-lotes` |
| `trecho` | `hh:mm:ss-hh:mm:ss` | vídeo inteiro | `--trecho` |
| `paralelo` | 1 a 6 | 3 | `--paralelo` |
| `esforco_analista` | `low`, `medium`, `high`, `xhigh`, `max` | `high` | `--esforco-analista` |
| `esforco_curador` | idem (vale só para o NEXT) | `high` | `--esforco-curador` |
| `curadoria` | `plano`, `lotes` | `plano` | `--curadoria` |
| `idioma` | `pt-BR`, `en`, `es`… | o do objetivo; `pt-BR` | `--idioma` |
| `falantes` | `SPEAKER_01=Nome;SPEAKER_03=Outro` | os rótulos da legenda | `--falantes` |
| `sensibilidade` | `low`, `normal`, `high` | `normal` | `--sensibilidade` |
| `ignorar_regiao` | `x,y,w,h` em frações do quadro (0 a 1, origem no canto superior esquerdo) | nenhuma | `--ignorar-regiao` |

**Consumo.** Com sucesso, o pedido é movido para `runs/<run_id>/pedido.md`; com erro, renomeado para
`pedidos/pedido.erro.md` (substitui o anterior), e o Write seguinte não precisa de Read. Na saída 4 (um
vídeo falhou, os outros formam um run), vai também uma cópia para o run. `--estimar` não o consome: o
Write seguinte precisa de Read antes. Opção dada na linha de comando prevalece sobre a do pedido.

## 2. Linha de comando do `prepare.py` usada pela skill

```bash
python "<skill>/scripts/prepare.py" --pedido "C:/Users/digom/video-analysis/pedidos/pedido.md" --estimar
python "<skill>/scripts/prepare.py" --pedido "C:/Users/digom/video-analysis/pedidos/pedido.md"
python "<skill>/scripts/prepare.py" --resume <run_id|run_dir|ultimo> [--papeis agents|inline] [--max-lotes N] [--curadoria plano|lotes]
```

- `<skill>` é `${CLAUDE_SKILL_DIR}`. Sempre pela ferramenta Bash e sempre `python`, nunca `python3`.
- Regra da legenda: a `.vtt` tem o mesmo nome do vídeo, na mesma pasta. `--vtt "<caminho>"` aponta outra
  (só com 1 vídeo); `--skip-missing` pula, numa pasta, os vídeos sem legenda.
- `--resume` aceita só `--papeis`, `--max-lotes`, `--paralelo`, `--esforco-analista`,
  `--esforco-curador` e `--curadoria`; a que não vier mantém o valor gravado no run (`curadoria` de um run
  anterior à opção: `lotes`). Recusa `--pedido`, entradas, `--estimar` e `--force`.
- Uso manual, fora da skill: entradas diretas (`<vídeos…> --objective-file <md> | --objective "…"
  [--run [NOME]]`; sem objetivo, só aquece o cache, sem run), `--gc [DIAS]` (padrão 30) e os parâmetros de
  [`parametros.md`](parametros.md). Gerais: `--home PASTA` (padrão `VIDEO_ANALYSIS_HOME` ou
  `%USERPROFILE%\video-analysis`), `--jobs N` (vídeos em paralelo, padrão 2), `--force[=all|cues|frames|batches]`
  (refaz o estágio e os seguintes), `--forcar` (aceita mais de 800 agentes, mas o Workflow recusa do mesmo
  jeito), `--recursive`, `--json-out ARQ`, `--ascii`, `--quiet`, `--debug`. As opções do pedido também
  existem como opção (`--papeis`, `--max-lotes`, `--paralelo`, `--esforco-analista`, `--esforco-curador`,
  `--curadoria`, `--idioma`, `--trecho`, `--falantes`, `--sensibilidade`, `--ignorar-regiao`). Referência completa:
  `python prepare.py --help` e `docs/design-deterministico.md`, §2.

**Códigos de saída**, com o `error.code` de cada um:

| saída | significado | `error.code` |
|---|---|---|
| `0` | ok (pode haver `warnings`) | — |
| `2` | entrada | `uso_invalido`, `pedido_invalido`, `objetivo_ausente`, `opcao_invalida`, `video_nao_encontrado`, `vtt_ausente`, `run_nao_encontrado`, `run_inconsistente`, `run_grande_demais` |
| `3` | ambiente | `ffmpeg_ausente`, `dependencia_ausente`, `workspace_inacessivel`, `instalacao_incompleta` |
| `4` | um vídeo falhou; os outros vêm no JSON | `video_falhou` |
| `130` | interrompido; os estágios completos ficam no cache | `interrompido` |
| `1` | erro interno (bug do `prepare.py`; traceback no log, ou no stderr com `--debug`) | `erro_interno` |

Vídeo com os três estágios no cache (cues, frames, lotes) não precisa de ffprobe nem de ffmpeg, só de
numpy e Pillow: a falta deles só pesa quando algum vídeo precisa ser sondado ou extraído.

## 3. stdout do `prepare.py`

Uma linha JSON, a última do stdout. Fica também em `<run_dir>/prepare.json` e em `logs/`. O stderr começa
com `run_dir: …` e traz o progresso; o `status.json` do run é atualizado a cada 5 s.

```json
{"ok": true, "mode": "prepare", "version": "1.0.0", "home": "C:/Users/digom/video-analysis",
 "run_id": "20261006-153012-venda-zero-clientes",
 "run_dir": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes",
 "json_path": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes/prepare.json",
 "videos": [{"vid": "v1", "slug": "venda-zero-clientes", "title": "venda_zero_clientes", "duration_s": 441.3,
   "n_frames": 86, "n_batches": 4, "frames_per_min": 11.7, "n_cues": 98, "language": "pt",
   "cache_hit": {"cues": false, "frames": false, "batches": false},
   "timing_s": {"frames": 26.4, "batches": 1.1}, "warnings": [], "…": "demais campos do summary.json"}],
 "estimate": {"batches": 2, "agents": 4, "seconds": 28},
 "env": {"permissions_ok": true, "agents_current": false, "workflow_current": true},
 "script_path": "C:/Users/digom/.claude/skills/video-analysis/workflow/video-analysis.js",
 "workflow_args": {"schema_version": 1, "…": "seção 4"},
 "errors": [], "warnings": []}
```

Chaves do sucesso, nesta ordem: `ok`, `mode`, `version`, `home`, `run_id`, `run_dir`, `json_path`,
`videos`, `estimate`, `env`, `script_path`, `workflow_args`, `errors`, `warnings`.

Erro (saída 1, 2, 3, 4 ou 130): as mesmas chaves, com `ok: false` e `error` (`code`, `detail`) logo depois
de `ok`; `workflow_args` vem `null` e os campos que não se aplicam vêm vazios (`videos: []`). A exceção é
a saída 4 com algum vídeo pronto, que traz `videos` e `workflow_args` utilizáveis (abaixo).

```json
{"ok": false, "error": {"code": "vtt_ausente", "detail": "…candidatos na pasta e o Rename-Item sugerido…"},
 "mode": "prepare", "…": "demais campos do envelope",
 "missing_vtt": [{"video": "C:/…/Reunião-20260610_150844-Gravação de Reunião.mp4",
   "candidates": ["C:/…/Reunião.vtt"], "suggestion": "Rename-Item -LiteralPath '…' -NewName '…'"}]}
```

- Chaves extras por erro, depois de `warnings`: `vtt_ausente` acrescenta
  `missing_vtt: [{video, candidates, suggestion}]`; `video_nao_encontrado` acrescenta `inputs: […]`, as
  entradas não achadas. Em `video_falhou`, `errors` (chave de sempre) lista `vid, slug, video, code,
  detail` por vídeo.
- Saída 4 no prepare: `run.json`, `objective.md` e um `workflow_args` utilizável com os vídeos que deram
  certo continuam gravados; `error.code` é `video_falhou` e `errors` lista os que falharam.
- `--gc` devolve `"mode": "gc"` e a chave `gc` (`removed`, `freed_mb`, `kept`, `failed`).

| campo | uso pelo agente principal |
|---|---|
| `ok`, `error.code`, `error.detail` | erro numa frase |
| `estimate.seconds` | no `--estimar`, a estimativa da extração: acima de 400 s, o prepare roda em segundo plano. No prepare e no resume, o tempo que levou |
| `estimate.batches`, `estimate.agents` | acima de 80 agentes, confirmar com o usuário |
| `env.permissions_ok` | `false`: avisar e oferecer o setup |
| `env.agents_current` | cópias de `~/.claude/agents/` idênticas às da skill; se não, `papeis: agents` vira `inline`, com aviso |
| `env.workflow_current` | `~/.claude/workflows/video-analysis.js` idêntico ao script da skill (mesma pasta do Claude Code que o verificador confere: `VA_CLAUDE_HOME` ou `%USERPROFILE%\.claude`): o Workflow é chamado pelo nome. Se não, ausente ou de outra versão: script inline e sugestão do setup (passo 6 do `SKILL.md`) |
| `warnings`, `videos[].warnings` | densidade (com região sugerida), câmera, legenda curta ou deslocada |
| `workflow_args` | vai ao Workflow em `args`, como objeto JSON e sem edição |
| `script_path` | o script da skill, só para o Workflow inline (passo 6 do `SKILL.md`): lido e passado em `script`, sem edição. Em `scriptPath` a ferramenta o recusa; com `env.workflow_current: true`, o normal é `name: "video-analysis"`, o workflow salvo pelo setup |

- `estimate.agents` = Σ por vídeo (lotes a analisar + 2, ou + 1 na retomada com ledger, que não tem INIT; com
  `curadoria: lotes`, 2 × lotes + 1; vídeo já concluído, com `final_exists`, conta 0) + 1 se houver mais de
  um vídeo (o CROSS, contado mesmo com `combined_exists`).
- Com `--estimar`, o envelope é o mesmo, com `"mode": "estimate"`: só ffprobe, então `estimate.seconds` é a
  estimativa da extração e os campos de lote podem vir nulos com o cache frio.

## 4. `workflow_args` v1

```json
{
  "schema_version": 1,
  "run_id": "20261006-153012-venda-zero-clientes",
  "run_dir": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes",
  "objective_path": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes/objective.md",
  "roles_dir": "C:/Users/digom/video-analysis/roles",
  "roles_mode": "inline",
  "language": "pt-BR",
  "options": {"max_batches": 2, "max_parallel_videos": 3, "analyst_effort": "high", "curator_effort": "high", "curation": "plan"},
  "combined_exists": false,
  "videos": [{
    "slug": "venda-zero-clientes",
    "title": "venda_zero_clientes",
    "duration": "00:07:21",
    "n_frames": 86,
    "batches_dir": "C:/Users/digom/video-analysis/cache/venda-zero-clientes-1a2b3c4d/batches-5e6f7a8b",
    "out_dir": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes/v1",
    "batch_images": [20, 19, 21, 22],
    "start_batch": 1,
    "ledger_exists": false,
    "start_prompt_exists": false,
    "final_exists": false
  }]
}
```

| campo | significado |
|---|---|
| `run_id` | `AAAAMMDD-HHMMSS-<slug>` |
| `objective_path` | `objective.md` do run: `# Objetivo (literal)`, `# Opções`, `# Vídeos` |
| `roles_dir` | `<pasta central>/roles`, cópia de `agents/*.md` renovada a cada run (lida no modo inline) |
| `roles_mode` | `agents` ou `inline`, já rebaixado se as cópias instaladas diferem |
| `language` | idioma dos relatórios |
| `options.max_batches` | `null` ou `0` = todos; N = só os lotes 1–N (modo teste) |
| `options.max_parallel_videos` | 1 a 6, padrão 3; os lotes de um vídeo são sempre em série |
| `options.analyst_effort`, `options.curator_effort` | padrão `high` |
| `options.curation` | `plan` (padrão, e quando ausente): INIT, analistas e FINAL; `batches`: também o NEXT antes de cada lote depois do 1º |
| `combined_exists` | o `relatorio.md` combinado já existe: o CROSS não roda |
| `videos[].slug` | ASCII curto, único no run (duplicata ganha `-2`) |
| `videos[].duration`, `n_frames` | `hh:mm:ss`; frames únicos do vídeo ou do trecho |
| `videos[].batches_dir` | pasta `batches-<h8>` do cache (só leitura para os agentes) |
| `videos[].out_dir` | `<run_dir>/v<N>`, com `v<N>` = o `vid` do vídeo no JSON do `prepare.py`, numerado na ordem das entradas (pasta e curinga por nome; sem os pulados por `--skip-missing`). Vídeo que falha no prepare (saída 4) fica sem pasta e não renumera os outros: o run pode ter `v1` e `v3`, ou só `v2`. Runs antigos: `<run_dir>/<slug>`. O nome não repete o slug do run: com `<run_id>/<slug>`, o analista juntava os dois e gravava fora |
| `videos[].batch_images` | imagens a abrir em cada lote; revisita dentro do mesmo lote não conta. O tamanho da lista é o total de lotes |
| `videos[].start_batch` | primeiro lote, até o último deste run, sem `reports/batch_NNN.md` |
| `videos[].ledger_exists` | `ledger.md` existe |
| `videos[].start_prompt_exists` | `prompts/batch_{start}.md` existe |
| `videos[].final_exists` | `relatorio.md` existe e não sobra lote: o vídeo é pulado (`skipped`) |

**Validação no Workflow.** Obrigatórios: `schema_version: 1`, `run_id`, `run_dir`, `objective_path`,
`roles_dir`, `language` e, por vídeo, `slug` (sem repetir), `title`, `batches_dir`, `out_dir` e
`batch_images` (lista não vazia de inteiros positivos). Opcionais, se vierem, com o tipo certo:
`roles_mode`, `combined_exists`, `options.*`, `start_batch`, `ledger_exists`, `start_prompt_exists`,
`final_exists`. O script troca `\` por `/` nos caminhos e limita `max_parallel_videos` a 6. Falhou a
validação: `workflow_args inválido (<campos>): rode prepare.py e passe workflow_args como objeto JSON,
sem editar`; os nomes antigos `cache_dir` e `batch_frames` são apontados na mensagem.

**Caminhos derivados** (iguais aos do script; `NNN` = lote com 3 dígitos):

| caminho | conteúdo |
|---|---|
| `{batches_dir}/batch_NNN.md` | linha do tempo do lote: frames, intervalos, caminhos das imagens, falas |
| `{batches_dir}/sheet_NNN.jpg` | prancha de miniaturas do lote (só o curador) |
| `{batches_dir}/overview.jpg` | uma miniatura por lote |
| `{batches_dir}/transcript.md` | transcrição em parágrafos por falante (o curador lê no INIT) |
| `{batches_dir}/summary.json` | contagens e intervalo de cada lote |
| `{out_dir}/ledger.md` | contexto curado do vídeo |
| `{out_dir}/prompts/batch_NNN.md` | prompt do curador para o lote |
| `{out_dir}/reports/batch_NNN.md` | relatório do analista |
| `{out_dir}/relatorio.md` | relatório final do vídeo |
| `{run_dir}/relatorio.md` | relatório combinado (2+ vídeos) |
| `{roles_dir}/video-analysis-curator.md`, `{roles_dir}/video-analysis-analyst.md` | papéis, no modo inline |

Nenhum arquivo gravado por agente se chama `report.md`, `summary.md` ou `findings.md`: o Claude Code
bloqueia subagentes de gravar esses nomes ("Subagents should return findings as text…"). Por isso o
relatório final é `relatorio.md`.

**`batch_NNN.md`** (gerado por `scripts/va/render.py`; rótulos em português quando a legenda é em
português, senão em inglês, menos o marcador de revisita ` (revisita de Fxxxx)`, igual em qualquer
idioma):

```text
# Lote B004 de 11 · F0055–F0072 · 00:08:55.0–00:10:46.0
Vídeo: <título> · 00:25:02.8 · 1568×882
Falantes: S1 = SPEAKER_03 · S3 = SPEAKER_00
Folha de contato: C:/Users/digom/video-analysis/cache/<slug>-<fp8>/batches-<h8>/sheet_004.jpg
Anterior: B003 (…) · Próximo: B005 (…)
Leitura: frame vale de início a fim; img = instante da imagem; …

### F0055 · 00:08:55.0–00:08:59.0 · img 00:08:55.5
C:/Users/digom/video-analysis/cache/<slug>-<fp8>/frames-<h8>/F0055_000855.jpg
- C0141 00:08:55.2–00:08:58.9 S1: <fala completa, na 1ª vez no lote>

### F0056 · 00:08:59.0–00:09:05.5 · img 00:08:59.0 · = F0054
C:/Users/digom/video-analysis/cache/<slug>-<fp8>/frames-<h8>/F0054_000851.jpg (revisita de F0054)
- ↳ C0141

### F0057 · 00:09:05.5–00:09:10.0 · img 00:09:06.0 · movimento · = F0055 (mesma tela, acima)
- (sem fala)
```

- **Linha de imagem:** só o caminho absoluto, com `/`, de um JPEG; numa revisita de frame de lote anterior
  (cabeçalho terminado em `· = Fxxxx`), seguido de exatamente ` (revisita de Fxxxx)`, que não faz parte do
  caminho. Revisita de um frame do mesmo lote (`· = Fxxxx (mesma tela, acima)`) não tem linha de imagem.
  O mesmo caminho nunca aparece duas vezes num lote. `batch_images[n-1]` = número de linhas de imagem.
- **`Falantes:`** lista só quem fala no lote. **`Folha de contato:`** (em inglês, `Contact sheet:`) é a
  prancha do curador; o analista não a abre e ela nunca conta como imagem.
- Falas: `≈` = horário aproximado; `(cont.)` = começou antes do lote; `↳ C0141` = já impressa acima;
  `- (sem fala)`. `tests/check_run.py` confere esse formato (`lote.formato`).

**Ledger.** A segunda linha começa com `Integrado até o lote: NNN de BBB` (`000` depois do INIT). Lote que
falhou também conta como integrado, registrado como lacuna. É esse cabeçalho que torna cada passo do
curador idempotente na retomada.

**Retomada** (`--resume`, semântica DL-11 do design agêntico):

- sem `ledger.md`: `prompts/` e `reports/` vão para `_descartado/`, e `start_batch` = 1;
- com `relatorio.md` e trabalho pendente: ele vira `relatorio.anterior.md`; o mesmo vale para o
  `relatorio.md` combinado quando algum vídeo vai rodar;
- `report.md` de um run anterior à troca de nome vale como relatório final (no vídeo e no combinado):
  sem trabalho pendente, é renomeado para `relatorio.md`; com trabalho pendente, vai para
  `relatorio.anterior.md` (ou `report.anterior.md`, se havia também um `relatorio.md`);
- `trecho`: lotes numerados 001… dentro do trecho, com os ids de frame do vídeo inteiro.

| ledger | `start_batch` | `start_prompt_exists` | o que o Workflow faz |
|---|---|---|---|
| não | — | — | INIT, depois a partir do lote 1 |
| sim | k | sim | analista do lote k direto |
| sim | k | não | `batches`: NEXT k ("confira Integrado até"), depois o analista k; `plan`: o analista k de contingência |
| sim | último + 1 | — | só o FINAL |

`final_exists: true` pula o vídeo.

## 5. Schemas dos retornos dos agentes

Cada agente devolve o objeto pela ferramenta StructuredOutput. Sem ela (roteiro
[`sem-workflow.md`](sem-workflow.md)), responde uma única linha JSON com os mesmos campos. Só os tipos
`type`, `enum`, `items`, `required` e `description` são usados.

```js
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
```

| agente | campos | vale quando |
|---|---|---|
| INIT (curador) | `status, ledger_path, prompt_path, video_type, focus`, `warnings?` | `status: ok` |
| NEXT (curador) | `status, integrated_through, coverage[{batch, state}], prompt_path, focus`, `carried?`, `warnings?` | `status: ok` |
| analista | `status ok\|partial\|failed, report_path, frames_seen, frames_missing[], notable`, `warnings?` | `status` ≠ `failed`, `report_path` = o caminho pedido (tolerando `\`, `//`, espaços nas pontas e maiúsculas), `frames_seen` ≥ `batch_images[n-1]` e `frames_missing` vazio |
| FINAL e CROSS (curador) | `status, report_path, answered, summary` (≤ 120 palavras), `gaps?` | `status` ≠ `failed` |

- `frames_seen` e `frames_missing` contam **imagens** do lote (as linhas de caminho do `batch_NNN.md`).
  Frames de referência pedidos pelo curador são extras e não contam.
- Na segunda tentativa do analista (complemento), `frames_seen` soma as imagens vistas nas duas tentativas,
  e `frames_missing` lista só as que nenhuma das duas abriu.
- `report_path` fora do caminho pedido nunca vale: a segunda tentativa recebe os dois caminhos e grava no
  pedido; errado de novo, o lote fica `failed`, com o aviso `lote N: relatório gravado em caminho errado
  (<caminho>)`, e o FINAL o lista em `Falhos`; com `curadoria: lotes`, o NEXT seguinte também o recebe,
  como FALHOU.
- `coverage` é a auditoria do curador: a tabela do relatório cobre todos os ids do lote? `incomplete`
  rebaixa o lote para `partial`; `missing`, para `failed`.
- O S_FINAL não tem `warnings`: em falha, o motivo vai no `summary`. Nos outros, vai em `warnings`. O
  Workflow repete esse motivo (até 160 caracteres) no aviso do vídeo, entre parênteses.

## 6. Retorno do Workflow

```json
{
  "run_id": "20261006-153012-venda-zero-clientes",
  "run_dir": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes",
  "roles_mode": "inline",
  "agents": 4,
  "videos": [{
    "slug": "venda-zero-clientes",
    "title": "venda_zero_clientes",
    "status": "partial",
    "report_path": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes/v1/relatorio.md",
    "answered": "partly",
    "summary": "…resposta direta ao objetivo, ≤ 120 palavras…",
    "batches": {"total": 4, "planned": 2, "from": 1, "ok": 2, "partial": [], "failed": []},
    "frames": "39/39",
    "warnings": []
  }],
  "combined": null
}
```

- `videos[].status`: `ok` · `partial` (lote parcial ou falho, FINAL `partial`, ou modo teste com
  `planned` < `total`) · `failed` (vídeo interrompido; os arquivos ficam para `retomar`) · `skipped`
  (já concluído).
- `batches`: `ok` é contagem; `partial` e `failed` são listas de lotes; `from` é o lote inicial (> 1 na
  retomada; maior que `planned` quando só faltava o FINAL); `planned` é o último lote deste run (menor
  só se o orçamento de tokens acabou). `batches` é `null` em vídeo não executado. `frames`: imagens
  vistas/esperadas neste run (`null` sem lote novo ou em `skipped`). `warnings`: até 6; com mais, os 4
  primeiros, `+N avisos omitidos` e o último (num vídeo `failed`, o motivo da interrupção).
- `combined`: `null` (um vídeo, menos de 2 relatórios, ou combinado já existente),
  `{report_path, answered, summary}`, ou `{error: "síntese falhou duas vezes (motivo)"}`.
- `agents`: chamadas de agente feitas neste run. O retorno não conta tokens: o `budget.spent()` do
  Workflow soma a sessão inteira, não o run (num run de 5 agentes, deu 2,77 M).

## 7. Pasta central

```
C:\Users\digom\video-analysis\
  cache\<slug>-<fp8>\                   source.json, probe.json, last_used, .lock
    cues-<h8>\                          cues.json, stage.json
    frames-<h8>\                        F0001_hhmmss.jpg…, frames.json, samples.csv, ffmpeg_a.log, ffmpeg_b.log, stage.json
    batches-<h8>\                       batch_NNN.md/.json, sheet_NNN.jpg, overview.jpg, transcript.md,
                                        summary.json, timeline.json, batches.json, stage.json
  runs\<AAAAMMDD-HHMMSS>-<slug>\        pedido.md, objective.md, run.json, prepare.json, status.json, relatorio.md (combinado)
    v<N>\                               ledger.md, relatorio.md, prompts\batch_NNN.md, reports\batch_NNN.md
  pedidos\                              pedido.md (temporário), pedido.erro.md
  roles\                                cópia dos papéis para o modo inline
  logs\  debug\  tmp\
```

- `v<N>`: o `vid` do vídeo (`v1`, `v2`…, na ordem das entradas). Vídeo que falha no prepare fica sem pasta e
  não renumera os outros (`videos[].out_dir`, seção 4). Um run antigo usa o slug do vídeo no lugar, e a
  retomada mantém esse nome.
- `<fp8>` sai do caminho, do tamanho e da data do vídeo: vídeo movido, renomeado ou editado é extraído de novo.
- Cada estágio do cache tem a sua chave (`<h8>`): mudar o lote não reextrai frames; mudar a legenda ou os
  falantes não reextrai frames; mudar sensibilidade ou região ignorada reextrai frames e refaz os lotes.
- Os agentes só leem o cache; gravam só em `runs\`.
