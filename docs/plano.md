# Plano — skill `video-analysis`

## Erratas (implementação)

O que o uso real mudou neste plano; onde divergem, vale isto e não o texto abaixo.

- **Disparo do Workflow:** a ferramenta recusa um `scriptPath` fora da sessão, como o `workflow/video-analysis.js` da skill (passo 3 da arquitetura). O normal é o workflow salvo, chamado pelo nome (`name: "video-analysis"`), que o setup instala em `~/.claude/workflows/` com `--instalar-agentes`; o script inline (`script`) fica como reserva. O `prepare.py` diz em `env.workflow_current` se a cópia salva é idêntica à da skill.
- **Relatório final:** chama-se `relatorio.md`, por vídeo e combinado (na retomada, o anterior vira `relatorio.anterior.md`), e não `report.md`: o Claude Code bloqueia subagentes de gravar arquivos chamados `report.md`, `summary.md` ou `findings.md`. Um `report.md` de run antigo ainda vale como relatório final na retomada.
- **Agentes personalizados:** neste ambiente, os agentes instalados em `~/.claude/agents/` foram reconhecidos no meio da sessão; a ressalva de que só carregam numa sessão nova pode não valer.
- **Extração em lockstep:** um único ffmpeg decodifica o vídeo e o filtro `split` alimenta as duas saídas (A em cinza, B em JPEG), em vez de dois ffmpeg sincronizados.
- **Pasta de cada vídeo no run:** `runs\<AAAAMMDD-HHMMSS>-<slug>\v<N>\` (`videos[].out_dir`), e não `<slug-do-vídeo>\`: com o slug, o analista juntava o slug do run e o do vídeo e gravava fora da pasta. `v<N>` é o `vid` do vídeo no JSON do `prepare.py`, numerado na ordem das entradas; um vídeo que falha no prepare (saída 4) não renumera os outros, e o run pode ficar com `v1` e `v3`. Na retomada, um run antigo mantém a pasta `<slug-do-vídeo>\`.
- **Curadoria:** a opção `curadoria` do pedido (`--curadoria plano|lotes`; em `workflow_args.options.curation`, `plan` ou `batches`) vale `plano` por padrão: o curador só planeja, com o INIT gravando o ledger e o prompt do lote 1, e os analistas dos lotes seguintes recebem as seções 4 e 5 do ledger. O NEXT, antes de cada lote depois do 1º, só roda com `--curadoria lotes`, que num vídeo de 13 lotes custou ≈ 2× (US$ 9,9 contra 5,4) sem ganho de recall. Por vídeo, são 1 agente por lote e mais 2 (com `lotes`, 2 por lote e mais 1): a reunião de 55 min de "Riscos e mitigação" (≈50 lotes) dá ≈52 agentes, e não ≈100. Com dois vídeos ou mais, o run tem mais 1, o CROSS (conta completa em `estimate.agents`, no `reference/contratos.md`).
- **Esforço do curador:** a opção `esforco_curador` (`--esforco-curador`; em `workflow_args.options.curator_effort`) só vale para o NEXT; no Workflow, INIT, FINAL e CROSS rodam sempre em `high`. Na curadoria padrão (`plano`), sem NEXT, ela não muda nada.

## Contexto

Hoje cada análise de vídeo+legenda faz o Claude escrever um script ad hoc (extrair frames, casar com a legenda, ler tudo), e isso gasta tokens à toa. A skill `video-analysis` torna o processo fixo:

1. **Scripts determinísticos** extraem só os frames únicos (mudança só de cursor conta como duplicado), cada um com o intervalo de tempo que representa, e associam esse intervalo às falas da `.vtt` de mesmo nome.
2. **Análise agêntica sob demanda**: um Workflow conduz lotes **sequenciais por vídeo**. O **Opus** monta o prompt de cada lote e faz a curadoria do contexto que passa adiante. O **Sonnet** só executa o prompt: lê frames e legendas e escreve o relatório.

Decisões tomadas com você:
- Loop conduzido por **script de Workflow**; se a ferramenta Workflow não existir na sessão, o agente principal conduz via ferramenta Agent.
- Tipos de vídeo: **gravação de tela, reunião gravada e slides/aula**.
- Saída numa **pasta central**, `C:\Users\digom\video-analysis\`, com cache por vídeo e uma pasta por análise.
- O Opus vê uma **prancha de miniaturas** do próximo lote para direcionar o prompt.
- **Permissões**: posso adicionar regras Read/Write/Edit restritas à pasta central no `~/.claude/settings.json`. Mostro o trecho exato antes de gravar.
- **Esforço**: `high` para o Sonnet analista e para o Opus curador, ambos ajustáveis por opção.

Ambiente verificado:
- Python 3.14.5 (`python`), numpy 2.4.6, Pillow 12.2.0, pytest 9.0.3, Node 24 e ffmpeg/ffprobe 8.1.2 no PATH.
- **Sem OpenCV**, e nada novo será instalado.
- Claude Code 2.1.220, que suporta `${CLAUDE_SKILL_DIR}`.
- 8 CPUs, então o Workflow roda até 6 agentes simultâneos.
- Console em cp1252, então os scripts forçam UTF-8.
- `~/.claude/agents/` ainda não existe, então agentes criados nesta sessão só carregam numa sessão nova.

O que é reaproveitado: em setembro, uma sessão já escreveu para você `frames.py` (ffmpeg + numpy, 2 fps, máscara de webcam), `preparar_janelas.py` e `wf_videos.js`. Estão em `C:\Users\digom\AppData\Local\Temp\claude\C--Users-digom-Documents-solar-databricks--schema\cd2c1414-3ebd-4b7c-a607-fbbe8a10608f\scratchpad\`.
- Lição 1: o Sonnet pulou de 17 a 38 frames em janelas de 50. Daí os lotes de ~20 frames com conferência de `frames_seen`.
- Lição 2: a cota estourou. Daí a curadoria enxuta e os retornos mínimos.
- As saídas antigas servem de linha de regressão; por exemplo, "Repasse… DA’s" gerou 128 frames.
- Como a pasta Temp pode ser limpa, a primeira onda copia esses dados.

## Arquitetura

```
/video-analysis <vídeos|pasta> <objetivo> [opções]   |   /video-analysis retomar [run_id]   |   /video-analysis setup
      │
      ▼ AGENTE PRINCIPAL — orquestrador leve: nunca abre frames, lotes, transcrição, prompts, ledger ou relatórios de lote
 1. Write ~/video-analysis/pedidos/pedido.md   (vídeos + objetivo literal + opções; evita aspas no shell)
 2. Bash  python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" --pedido … [--estimar]   → 1 JSON (resumo + workflow_args)
      │     DETERMINÍSTICO, com cache por vídeo: frames únicos com intervalo → .vtt → alinhamento → lotes de ~20 frames
      │     → batch_NNN.md (linha do tempo para LLM) + prancha sheet_NNN.jpg + transcript.md + summary.json
 3. Workflow(scriptPath = …/workflow/video-analysis.js, args = workflow_args)              → roda em background
      │  por vídeo (até 3 vídeos em paralelo; os lotes de um vídeo sempre em série):
      │    Opus INIT   lê objetivo + transcrição inteira + resumo → ledger.md (plano por lote) + prompt do lote 1
      │    para cada lote n:
      │      Sonnet    lê prompt n + batch_n.md + TODAS as imagens do lote → reports/batch_n.md → {frames_seen, …}
      │      Opus NEXT lê ledger + relatório n + batch_{n+1}.md + prancha n+1 → reescreve o ledger (curadoria) + prompt n+1
      │    Opus FINAL  ledger + relatórios → report.md do vídeo
      │  2+ vídeos → Opus CROSS → report.md combinado
 4. Notificação → apresenta só os resumos devolvidos (≤120 palavras por vídeo) + caminhos dos relatórios
```

Onde o token é economizado:
- Só frames únicos vão para análise. O baseline de setembro descartou de 66% a 91% das amostras, e a revisita reaproveita telas que voltam.
- O Opus nunca recopia dados determinísticos: o prompt dele cita o caminho de `batch_NNN.md`.
- A transcrição é lida uma única vez, no INIT, e destilada no ledger. O ledger tem até 1.500 palavras e é reescrito a cada lote.
- Os agentes têm ferramentas mínimas, então o system prompt é pequeno, e devolvem retornos estruturados minúsculos.
- O cache por estágio evita reextrair frames quando você faz uma nova pergunta sobre o mesmo vídeo.
- O agente principal só vê o JSON do `prepare.py` e os resumos finais.

### Onde fica cada coisa

```
C:\Users\digom\.claude\skills\video-analysis\
  SKILL.md · setup.md · verificar_ambiente.py            (verificar_ambiente na raiz, como nas suas outras skills)
  scripts\prepare.py · calibrate.py · explain.py
  scripts\va\     __init__ (versões + parâmetros padrão), common, contracts, probe, ffmpeg_io, dedup, extract,
                  vtt, align, batching, render, sheets, cache, discover, runs, ambiente
  workflow\video-analysis.js
  agents\video-analysis-curator.md · video-analysis-analyst.md     (canônicos; o setup copia para ~/.claude/agents)
  reference\sem-workflow.md · problemas.md · contratos.md · parametros.md
  docs\design-deterministico.md · design-agentico.md               (specs completas dos agentes de design)
  tests\          pytest (unitários, sintéticos, fixtures reais) + synth.py + data\ + harness.mjs + check_run.py
C:\Users\digom\.claude\agents\video-analysis-{curator,analyst}.md  (instalados pelo setup)
C:\Users\digom\video-analysis\
  cache\<slug>-<fp8>\      cues-<h8>\ · frames-<h8>\ (F0001_hhmmss.jpg, frames.json, samples.csv) ·
                           batches-<h8>\ (batch_NNN.md/.json, sheet_NNN.jpg, overview.jpg, transcript.md, summary.json, timeline.json)
  runs\<AAAAMMDD-HHMMSS>-<slug>\   pedido.md, objective.md, run.json, prepare.json, status.json, report.md (combinado)
                                   <slug-do-vídeo>\{ledger.md, report.md, prompts\, reports\}
  pedidos\ · roles\ (cópia dos papéis para o modo inline) · logs\ · debug\ · tmp\
```

## Camada determinística (Python: ffmpeg + numpy + Pillow, sem dependências novas)

**Extração** (`va/extract.py`, `va/dedup.py`):
- Dois ffmpeg rodam sincronizados:
  - o **A** gera 2 fps em cinza 640×360 para a análise;
  - o **B** gera JPEG no mesmo grid, já no tamanho de saída (lado maior 1568, ~160 KB).
- O Python grava só os JPEGs escolhidos.
- `-skip_frame:v noref` deixa a extração até 30% mais rápida em H.264 com B-frames.
- `fps=…:round=near` dá timestamps exatos, inclusive em vídeo de taxa variável (VFR).
- Medido nesta máquina: ~0,06× o tempo real em Teams 1080p16 e ~0,15× em 1080p60.

**Comparação**:
- Sempre contra o **último frame mantido**, para que mudanças lentas e digitação se acumulem.
- A diferença usa dois limiares (16/40) e componentes conexos numa grade de células.
- **Cursor**: até 3 componentes compactos do tamanho de um ponteiro contam como duplicado. O tamanho é 0,045 × a altura, ≈49 px em 1080p, o que cobre escala de 150%. Isso inclui a posição antiga e a nova fundidas e o cursor de texto piscando.

**Regiões quentes** (webcam, anel de quem fala, cronômetros):
- A máscara é calculada por janela de ±30 s.
- A janela é cortada nas trocas de layout (galeria ↔ tela compartilhada), para a máscara não esconder conteúdo da tela compartilhada.
- São 3 critérios com histerese: atividade local contínua, atividade total e recorrência por minuto.

**Estabilização**:
- Uma mudança só vira frame depois de 1 s estável.
- Em rolagem ou digitação contínua, saem frames "movimento" com intervalo crescente de 4 a 30 s.
- Um tooltip que aparece e some volta a contar como "igual".
- **Revisita**: uma tela igual a uma das 16 últimas (em até 10 min) reaproveita a imagem anterior.
- Em filmagem de câmera, a extração degrada com elegância para ~2 frames/min (heartbeat).

**Intervalos**: cada frame tem `start` (quando a mudança começou), `captured_at` (o instante da imagem) e `end` (o início do próximo). Os intervalos são contíguos e cobrem o vídeo todo, com tempos em ms inteiros.

**Legenda** (`va/vtt.py`):
- Cobre os 4 estilos achados no seu disco:
  - WhisperX, com `SPEAKER_00:`;
  - Teams, com `<v Nome>`, UUID e falas sobrepostas;
  - simples;
  - sem horas (`MM:SS.mmm`), inclusive com troca de formato depois de 1 h.
- Trata BOM, CRLF, blocos NOTE/STYLE, tags e entidades HTML.
- Funde repetições e legendas "rolantes" do mesmo falante, nunca de falantes diferentes.
- Quebra falas longas do Whisper e marca o horário como aproximado (≈).

**Alinhamento e lotes** (`va/align.py`, `va/batching.py`):
- Uma fala pertence a um frame quando se sobrepõe ao intervalo dele.
- Cada lote tem no máximo 20 frames, 8.000 caracteres de fala e 15 min.
- O corte vai preferencialmente numa troca de cena ou num silêncio. A cauda pequena é fundida ao lote anterior.

**Saídas**:
- `batch_NNN.md` (formato no design):
  - cabeçalho com legenda de falantes e caminho da prancha;
  - por frame: id, intervalo, caminho absoluto da imagem e falas;
  - cada fala é impressa uma vez por lote e, depois, só referenciada como `↳ C0123`.
- Prancha `sheet_NNN.jpg`: ~1.560×780, ≈1,5k tokens, com id e tempo em cada miniatura.
- `overview.jpg`: uma miniatura por lote.
- `transcript.md`: parágrafos por falante, no formato `[hh:mm:ss · F… · B…]`.
- Também saem `summary.json`, `frames.json`, `cues.json`, `timeline.json` e `samples.csv` (diagnóstico).

**Cache por estágio**:
- Cada estágio tem chave de parâmetros: `cues-<h8>`, `frames-<h8>`, `batches-<h8>`.
- Mudar o tamanho do lote não reextrai frames.
- Lock com heartbeat; `--gc` apaga entradas antigas.

**Ferramentas de diagnóstico**: `calibrate.py` (varredura de parâmetros sem decodificar de novo) e `explain.py` (painel "por que este frame foi mantido ou ignorado").

## Camada agêntica

### Workflow `workflow/video-analysis.js`

**Entrada e retornos**:
- A entrada é o `workflow_args` v1 (ver Contrato unificado).
- Retornos estruturados (schema) de cada agente:

  | Agente | Campos |
  |---|---|
  | INIT | `status, ledger_path, prompt_path, video_type, focus` |
  | analista | `status ok\|partial\|failed, report_path, frames_seen, frames_missing[], notable` |
  | NEXT | `status, integrated_through, coverage[{batch,state}], prompt_path, focus, carried` |
  | FINAL e CROSS | `status, report_path, answered, summary ≤120 palavras` |

**Integridade**:
- `frames_seen` precisa bater com `batch_images`. Se não bater, há 1 retentativa "complemento" com os ids que faltaram.
- O NEXT também audita se o relatório cobre todos os frames do lote.

**Falhas**:
- Cada agente tem até 2 tentativas.
- Um lote que falha vira "lacuna", informada ao NEXT seguinte.
- Se a curadoria falhar, o analista recebe um prompt de contingência.
- Duas falhas seguidas do mesmo papel interrompem o vídeo. Os arquivos ficam para retomar.

**Retomada por arquivos**: o cabeçalho `Integrado até o lote: NNN` do ledger torna cada passo idempotente.

**Papéis**:
- Modo `agents`: usa `agentType`.
- Modo `inline`: subagente padrão com `model`, e o prompt manda ler o papel em `roles/…md`.
- O principal escolhe conforme os tipos apareçam na lista de agentes disponíveis.

**Guardas**:
- Acima de 80 agentes estimados, o principal confirma com você antes de disparar.
- Acima de 800, o Workflow recusa (o teto dele é 1000).
- Restrições do Workflow respeitadas: `meta` literal, sem `Date`, `Math.random` ou acesso a arquivos.
- Fases no `/workflows`: Plano · Lotes · Relatório · Síntese.

### Curador — `video-analysis-curator` (Opus)
- **Frontmatter**: `tools: Read, Write, Edit, Grep` · `model: opus` · `effort: high` · `maxTurns: 40`.
- **Modos**: INIT, NEXT, FINAL, CROSS.
- **Ledger** (≤1.500 palavras):
  1. Objetivo interpretado
  2. Mapa e plano por lote
  3. Contrato do relatório
  4. Contexto acumulado
  5. Fios abertos
  6. Achados consolidados `[lote · hh:mm:ss · Fxxxx]`
  7. Lacunas
  8. Log de curadoria
- **Prompt do lote** (≤450 palavras): objetivo do lote, o que o analista precisa saber (≤150 palavras), foco por id ou tempo, perguntas Q1–Q6, formato e até 2 frames de referência.
- **Regras**:
  - nunca copiar a linha do tempo ou as falas; citar o caminho;
  - a prancha serve só para planejar, sem descrever frames;
  - contexto só entra se mudar a leitura do lote;
  - o mesmo contrato em todos os lotes.
- **Especialização por tipo de vídeo**:
  - tela: passo a passo, valores digitados, erros;
  - reunião: falantes, decisões e pendências, fala × tela;
  - slides: título e pontos, o que o apresentador acrescenta.

### Analista — `video-analysis-analyst` (Sonnet)
- **Frontmatter**: `tools: Read, Write` · `model: sonnet` · `effort: high` · `maxTurns: 24`.
- **Passos**:
  1. Lê o prompt e a linha do tempo.
  2. Abre TODAS as imagens do lote, em ordem, em blocos de 8–10 Reads em paralelo, sem reabrir.
  3. Grava o relatório.
  4. Devolve o status.
- **Contrato padrão do relatório**:
  1. Linha do tempo: frame | intervalo | tela | fala | relação (confirma, complementa, diverge, só tela, só fala)
  2. Respostas
  3. Achados `[Fxxxx · hh:mm:ss]` com confiança
  4. Para levar adiante
  5. Incertezas e correções de legenda
  6. Alertas
- **Regras**: inferência sempre marcada; texto ilegível registrado como "ilegível"; dado sensível nunca copiado; até 1.000 palavras por 20 frames.

### SKILL.md (pt-BR, ≤180 linhas)
- **Frontmatter**:
  - `name`;
  - `description` com ~950 caracteres e gatilhos como "frame a frame", "contra a legenda", reuniões, `retomar` e `setup`;
  - `argument-hint`;
  - `allowed-tools`: Bash do `prepare.py` e do `verificar_ambiente.py`, e Write em `~/video-analysis/pedidos/*`.
- **Comandos**: análise, `retomar [run_id]` e `setup`.
- **Opções**: `--max-lotes`, `--trecho hh:mm:ss-hh:mm:ss`, `--paralelo`, `--esforco-analista`, `--papeis` e `--idioma`.
- **Fluxo do principal**:
  1. Interpreta o pedido. Só pergunta se faltar o objetivo ou o vídeo.
  2. Escolhe o modo de papéis.
  3. Grava `pedido.md`.
  4. Roda `prepare.py --estimar`. Acima de 400 s, roda o prepare em background (o limite do Bash é 600 s).
  5. Roda `prepare.py`.
  6. Chama o Workflow com `script_path` e `workflow_args` sem editar, responde em até 3 linhas e encerra o turno.
  7. Na notificação, apresenta os resultados.
- **Fallback**: sem a ferramenta Workflow, `reference/sem-workflow.md` traz os mesmos 7 templates de prompt para a ferramenta Agent, com retorno em 1 linha JSON.

## Contrato unificado (prevalece sobre as specs em `docs/` quando houver conflito)

1. **`workflow_args` v1** segue o formato do design agêntico, com duas trocas:
   - `cache_dir` vira **`batches_dir`**, onde ficam `batch_NNN.md`, `sheet_NNN.jpg`, `transcript.md`, `summary.json` e `overview.jpg`;
   - `batch_frames` vira **`batch_images`**: quantas imagens abrir por lote. Revisitas dentro do mesmo lote não contam.

   Campos: `schema_version, run_id, run_dir, objective_path, roles_dir, roles_mode, language, options{max_batches, max_parallel_videos=3, analyst_effort=high, curator_effort=high}, combined_exists, videos[{slug, title, duration, n_frames, batches_dir, out_dir, batch_images[], start_batch, ledger_exists, start_prompt_exists, final_exists}]`. Todos os caminhos são absolutos e com `/`.
2. **`prepare.py`**:
   - A entrada principal é `--pedido <md>`, com as seções `# videos`, `# objetivo` e `# opcoes`.
   - Opções aceitas no pedido: `papeis, max_lotes, paralelo, esforco_analista, esforco_curador, idioma, trecho, falantes, sensibilidade, ignorar_regiao`. As duas últimas servem para o principal repetir a extração com a região sugerida pelo aviso de densidade.
   - Depois de usado, o pedido é movido para a pasta do run; se der erro, é renomeado para `pedido.erro.md`.
   - `--estimar` não consome o pedido.
   - Entradas diretas com `--objective-file` continuam valendo para uso manual.
   - `--resume <run_id|run_dir|ultimo>` segue a semântica DL-11 do design agêntico.
   - `trecho` cria um estágio de lotes próprio sobre o cache do vídeo inteiro: lotes numerados 001… dentro do trecho, com os ids de frame preservados.
3. **JSON de saída**: os campos do design determinístico, mais `run_id`, `script_path`, `estimate{batches, agents, seconds}`, `env{permissions_ok, agents_current}` e `workflow_args`.
   - Erro sai como `{"ok": false, "error": {"code", "detail"}}`.
   - Códigos de saída: 0 ok, 2 entrada, 3 ambiente, 4 vídeo falhou, 130 interrompido.
4. **Papéis**:
   - A cada run, `prepare.py` copia `agents/*.md` para `<workspace>/roles/`.
   - Se o modo pedido for `agents` mas as cópias em `~/.claude/agents/` não estiverem idênticas, ele rebaixa para `inline`.
5. **VTT ausente**:
   - Gera erro listando candidatos, com sugestão de `Rename-Item`, pela regra estrita do mesmo nome.
   - `--vtt` só vale com 1 vídeo.
   - Numa pasta, `--skip-missing` pula os vídeos sem legenda.
6. **Questões abertas dos designs, resolvidas**:
   - curador com Grep: sim;
   - confirmação acima de 80 agentes;
   - FINAL roda no Opus;
   - revisita ligada;
   - 2 fps com estabilização de 1 s;
   - rótulos do `batch_NNN.md` no idioma detectado na legenda;
   - `zoom.py` fica para a v2.

## Permissões e setup

**Regras de permissão**:
- Mesclar no `C:\Users\digom\.claude\settings.json`, que hoje não tem bloco `permissions`.
- `enabledPlugins` e o resto ficam como estão, com backup em `settings.json.bak-<data>`.
- Mostro o trecho no chat antes de gravar:
  ```json
  "permissions": { "allow": [
    "Read(~/video-analysis/**)", "Edit(~/video-analysis/**)", "Write(~/video-analysis/**)",
    "Read(//c/Users/digom/video-analysis/**)", "Edit(//c/Users/digom/video-analysis/**)", "Write(//c/Users/digom/video-analysis/**)"
  ]}
  ```
- As duas grafias ficam até o E2E mostrar qual o Windows aceita. Depois removo a que não funcionar.

**`/video-analysis setup`**:
- Segue o padrão de `ingestao-adf`: `setup.md` + `verificar_ambiente.py [--json] [--instalar-agentes] [--aplicar-permissoes]`, com saída `[OK]/[FALTA]/[AVISO]`.
- Verifica: Python ≥3.10, ffmpeg e ffprobe, numpy e Pillow, pasta central gravável, regras de permissão, e se os agentes instalados são idênticos aos canônicos.
- `--instalar-agentes` cria `~/.claude/agents/` e copia os dois papéis.
- Aviso de reinício: um agente novo só carrega quando o Claude Code abre. Até lá a skill roda em modo `inline`, que funciona igual e gasta um pouco mais por agente.

## Implementação com subagentes Opus (eu só orquestro)

Todo arquivo é escrito por subagentes com `model: "opus"`. Eu disparo as ondas, confiro os relatórios e encaminho correções. Cada subagente lê este plano (o contrato unificado prevalece) e a seção correspondente em `docs/`.

| Onda | Subagente (Opus) | Entrega | Aceite |
|---|---|---|---|
| 0 | Specs + fundação | Extrai os relatórios finais dos dois agentes de design para `docs/design-*.md`. Cria `scripts/va/{__init__,common,contracts}.py`, `tests/conftest.py` e `pytest.ini`. **Copia o baseline de setembro** e trechos curtos das suas VTTs reais para `tests/data/` (ficam só na sua máquina) | módulos importáveis; dados copiados |
| 1 ∥ | Frames | `va/{probe,ffmpeg_io,dedup,extract}.py`, `calibrate.py`, `explain.py`, `tests/synth.py` + testes | gabarito sintético; determinismo |
| 1 ∥ | Legenda e lotes | `va/{vtt,align,batching,render,sheets}.py` (inclui `overview.jpg`) + testes | trechos dos 4 estilos + arquivo de bordas; golden test de `batch_NNN.md` e `transcript.md` |
| 1 ∥ | CLI e infra | `va/{cache,discover,runs,ambiente}.py`, `prepare.py`, `verificar_ambiente.py` + testes com estágios simulados | nomes difíceis (NFD, ’, espaços duplos, `[PES]`); pedido, resume e estimar |
| 1 ∥ | Workflow | `workflow/video-analysis.js`, `tests/harness.mjs` (mocks de `agent`/`parallel`), `tests/check_run.py` | todos os cenários do harness; lint das restrições limpo |
| 1 ∥ | Papéis | `agents/video-analysis-curator.md`, `agents/video-analysis-analyst.md` | campos de retorno iguais aos schemas; ≤3k e ≤2k tokens |
| 1 ∥ | Docs | `SKILL.md`, `setup.md`, `reference/*.md` | ≤180 linhas; description ≤1.024 caracteres; templates do fallback iguais aos do JS |
| 2 | Integração e calibração | `prepare.py` ponta a ponta nas 5 fixtures; laço de calibração; congela os parâmetros e registra em `tests/data/calibration.md` | Verificação 1–4 |
| 3 ∥ | Revisão | Confere os contratos ponta a ponta (prepare ↔ args ↔ JS ↔ papéis ↔ fallback ↔ SKILL), roda pytest e harness, corrige | tudo verde |
| 3 ∥ | Setup | `--instalar-agentes`, depois `--aplicar-permissoes` (após eu te mostrar o trecho) | setup sem `[FALTA]` |
| 4 | E2E | Eu disparo a skill; subagentes conferem e corrigem | Verificação 6 |

## Verificação

1. **pytest unitário** (sem ffmpeg):
   - parser VTT com trechos reais dos 4 estilos e um arquivo sintético de bordas;
   - alinhamento: fronteiras, sobreposição, fala que atravessa lotes;
   - lotes;
   - analisador numpy;
   - golden test de `batch_NNN.md` e `transcript.md`.
2. **Vídeos sintéticos com gabarito** (Pillow → x264):

   | Cenário | Esperado |
   |---|---|
   | só o cursor se mexe | 1 frame |
   | digitação | 1 a 4 frames |
   | slides com câmera | frames em 0/20/30/45/70 s |
   | reunião com troca de layout | frames em 0/10/25/40/80/90/105 s, sem vazamento da máscara |
   | rolagem | ≤5 frames "movimento" |
   | tooltip e relógio | ignorados |
   | câmera | ≤4 frames heartbeat |
   | variante VFR | os mesmos tempos |

3. **Fixtures reais**:
   - "Repasse… DA’s": entre 50 e 128 frames, com recall ≥90% dos frames significativos do baseline (±2 s), mais um painel das diferenças para revisão;
   - `venda_zero_clientes`: 98 falas, ≤15 frames/min, JPEG ≤500 KB;
   - `dias_estoque` (Teams): ≥2 falantes e falas sobrepostas;
   - `Criando sua primeira PR.mov`: saída 1568×1018, com acentos no caminho;
   - desempenho: `metric_view` (28 min, 60 fps) ≤0,15× o tempo real, Teams ≤0,06×, cache quente ≤2 s, RAM ≤400 MB.
4. **Determinismo**: duas execuções com `--force all`, extração sincronizada × sequencial e `--jobs 1` × `2` geram JSON, MD e JPEG idênticos byte a byte.
5. **`node tests/harness.mjs`**:
   - caminho feliz;
   - analista que pula frames;
   - curadoria que falha;
   - duas falhas seguidas;
   - retomada;
   - vídeo já concluído;
   - 3 vídeos com paralelismo 2;
   - CROSS;
   - guarda de tamanho;
   - modos de papéis.
6. **E2E nesta sessão** (modo inline):
   - Comando: `/video-analysis C:\Users\digom\Downloads\venda_zero_clientes.mp4 Explique o indicador "venda zero clientes": o que é pedido, de onde vem cada dado citado e o que aparece na tela quando cada fonte é mencionada --max-lotes 2`.
   - Esperado: 5 agentes (INIT, A1, N2, A2, FINAL) e **nenhum pedido de permissão**.
   - `check_run.py` deve aprovar:
     - o ledger evolui;
     - os prompts citam `batch_NNN.md` sem copiar falas;
     - os relatórios cobrem todos os frames;
     - `report.md` responde ao objetivo.
   - Em seguida, `retomar ultimo --max-lotes 3` deve rodar sem INIT e integrar o lote 3.
7. **Sessão nova** (você abre): o mesmo comando com `--papeis agents`, para validar `agentType` + `schema` + `model` e comparar os tokens com o modo inline.

## Riscos e mitigação
- **Sintaxe das regras de permissão no Windows**: o E2E é bloqueante, e o `prepare.py` acusa `permissions_ok=false`.
- **`${CLAUDE_SKILL_DIR}` com barras invertidas ou aspas no `allowed-tools`**: no pior caso, você aprova uma vez com "não perguntar de novo".
- **Workflow recusar um `scriptPath` fora da pasta da sessão**: o principal passa o script inline.
- **A tolerância a cursor esconde mudanças minúsculas** (um dígito, até ~4 caracteres). Isso é por design: elas se acumulam contra o último frame mantido, e `--sensitivity high` reduz o efeito.
- **A diferença em cinza não vê troca de cor com o mesmo brilho**: fica documentado.
- **Vídeos longos custam caro e demoram**: uma reunião de 55 min dá ≈50 lotes, ≈100 agentes, horas em série. Mitigação: confirmação acima de 80 agentes, `--trecho` e `--max-lotes`.
- **Limites de taxa da API**: `--paralelo 1`.
- **Prepare acima de 600 s**: roda em background, guiado pelo `--estimar`.

## Fora do escopo (v2)
- "Cobertura por relevância": o curador pula lotes alheios ao objetivo. É o maior ganho em reuniões longas.
- Curadoria a cada 2 lotes em trechos homogêneos.
- Capítulos paralelos para um vídeo longo.
- `zoom.py`: recorte em resolução nativa para texto miúdo.
- Diferença também em cor.
- Empacotar como plugin, para registrar os agentes sem copiar nem reiniciar.
