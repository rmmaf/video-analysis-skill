---
name: video-analysis
description: 'Analisa vídeos frame a frame contra a legenda (.vtt de mesmo nome) para um objetivo dado na chamada: gravações de tela, reuniões gravadas (Teams, Zoom) e aulas com slides. Extrai só os frames únicos (mudança só do cursor não conta), alinha cada um às falas, divide em lotes e dispara um Workflow em que um curador Opus planeja os lotes e um analista Sonnet lê os frames e as falas de cada lote, em sequência; entrega um relatório por vídeo e, com dois vídeos ou mais, um combinado. Use quando pedirem para analisar, resumir, auditar ou extrair passos, decisões, erros ou evidências de um vídeo (.mp4, .mkv, .mov, .webm) ou de uma pasta de vídeos, "frame a frame", "contra a legenda" ou "contra a transcrição", ou o que aparece na tela quando algo é dito, e para retomar uma análise interrompida (`retomar`). Com `setup` (`/video-analysis setup`), prepara a máquina: ffmpeg, Python, agentes, workflow e permissões. Não escreva script ad hoc de frames: use esta skill.'
argument-hint: "<vídeo(s) ou pasta> <objetivo> [--max-lotes N] [--trecho hh:mm:ss-hh:mm:ss] | retomar [run_id] | setup"
allowed-tools:
  - Bash(python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" *)
  - Bash(python "${CLAUDE_SKILL_DIR}/verificar_ambiente.py" *)
  - Write(~/video-analysis/pedidos/*)
---

# Análise de vídeo frame a frame contra a legenda

Esta skill responde a um objetivo sobre um ou mais vídeos (gravação de tela, reunião gravada, aula com
slides) cruzando cada tela com as falas da `.vtt` de mesmo nome. O `prepare.py` extrai só os frames
únicos, cada um com o intervalo que representa, e os divide em lotes de até 20. Um Workflow conduz os
lotes de cada vídeo em série: o curador (Opus) planeja todos os lotes e escreve o prompt do primeiro;
o analista (Sonnet) abre todos os frames do lote, cruza com as falas e grava o relatório do lote. Com
`--curadoria lotes`, o curador também escreve o prompt de cada lote seguinte, levando o contexto adiante.

**Regra de ouro: você não lê o vídeo.** Quem abre frame, prancha, lote, transcrição, prompt, ledger e
relatório de lote são os agentes do Workflow. Você prepara, dispara e apresenta.

## Comandos

| chamada | o que faz |
|---|---|
| `/video-analysis <vídeo(s)\|pasta> <objetivo> [opções]` | análise nova: a seção **Fluxo** |
| `/video-analysis retomar [run_id]` | continua um run interrompido; sem id, o desta conversa (seção **Retomar**) |
| `/video-analysis setup` | prepara a máquina: siga [`setup.md`](setup.md) e **não faça mais nada nesta chamada** |

O argumento chega ao fim da skill como `ARGUMENTS: …`. Pedido para "configurar", "instalar" ou
"preparar" a skill também é o `setup`; se ele interrompeu uma análise, ofereça retomá-la ao terminar.

| opção | no `pedido.md` | efeito |
|---|---|---|
| `--max-lotes N` | `max_lotes` | modo teste: só os lotes 1–N de cada vídeo |
| `--trecho hh:mm:ss-hh:mm:ss` | `trecho` | só esse trecho, com lotes próprios sobre o cache do vídeo inteiro |
| `--paralelo N` | `paralelo` | vídeos em paralelo (padrão 3); `1` contra limite de taxa |
| `--esforco-analista low\|medium\|high` | `esforco_analista` | esforço do analista Sonnet (padrão `high`; `medium` custa ~13% menos e deixa respostas menos completas) |
| `--curadoria plano\|lotes` | `curadoria` | `lotes`: curador antes de cada lote, levando o contexto adiante (≈ 2× o custo; padrão `plano`) |
| `--esforco-curador low\|medium\|high` | `esforco_curador` | esforço do curador Opus nos lotes, só com `--curadoria lotes` (padrão `high`) |
| `--papeis agents\|inline` | `papeis` | força o modo dos papéis (sem a opção, vale o passo 2) |
| `--idioma xx` | `idioma` | idioma dos relatórios (padrão: o do objetivo; na dúvida, `pt-BR`) |
| `--falantes "SPEAKER_01=Nome;…"` | `falantes` | nomes reais dos falantes da legenda |
| `--sensibilidade low\|normal\|high` | `sensibilidade` | `high` pega mudanças menores (mais frames); `low`, o contrário |
| `--ignorar-regiao x,y,w,h` | `ignorar_regiao` | região (frações do quadro) fora da comparação: webcam, relógio |

Quando mexer em cada uma: [`reference/parametros.md`](reference/parametros.md).

## Economia de tokens (vale para você)

- **Nunca leia** frames, pranchas, as pastas `batches-*`, `frames.json`, `cues.json`, `timeline.json`,
  `transcript.md`, `prompts/`, `ledger.md` nem `reports/`. **Nunca rode** ffmpeg nem escreva script ad
  hoc de frames.
- Não repita na resposta o JSON do `prepare.py` nem os `workflow_args`.
- Leia um `relatorio.md` só se o usuário perguntar algo que o `summary` não responde; um frame ou
  relatório de lote, só se ele o nomear. Para aprofundar um trecho, rode um run novo com `--trecho`.
- Invocar esta skill **é** o pedido explícito de um Workflow com muitos agentes: 1 por lote e mais 2 por
  vídeo (com `--curadoria lotes`, 2 por lote e mais 1), e mais 1 com dois vídeos ou mais. Não junte nem
  pule lotes para caber numa diretriz de 10 agentes.

## Fluxo

1. **Interprete.** Separe de `ARGUMENTS` os caminhos (vídeos ou pastas; um caminho termina na extensão
   do vídeo, como `.mp4`, `.mkv`, `.mov` ou `.webm`, ou vem entre aspas), as opções `--…` e o objetivo:
   o resto, **literal**. Só pergunte se faltar o objetivo (uma pergunta, com dois exemplos de objetivo)
   ou se nenhum vídeo puder ser identificado.
2. **Papéis.** `agents` se `video-analysis-curator` e `video-analysis-analyst` aparecem na lista de tipos
   de agente disponíveis; senão `inline`, que funciona igual e gasta um pouco mais por agente. A opção
   `--papeis` do usuário prevalece.
3. **Pedido.** Com Write, grave `C:\Users\digom\video-analysis\pedidos\pedido.md`; se o arquivo existir,
   leia antes (o Write exige). O objetivo vai literal, com aspas e quebras de linha: nada passa pelo
   shell. Em `# opcoes`: `papeis` sempre, `idioma` se o objetivo não estiver em português, e só as
   demais opções dadas.

   ```
   # videos
   C:/Users/digom/Downloads/venda_zero_clientes.mp4

   # objetivo
   <texto do usuário, literal>

   # opcoes
   papeis: inline
   max_lotes: 2
   ```

4. **Estime**, com a ferramenta **Bash** (é ela que a skill pré-aprova; não use PowerShell nem `python3`):

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" --pedido "C:/Users/digom/video-analysis/pedidos/pedido.md" --estimar
   ```

   A estimativa não consome o pedido; erro aqui se trata como no passo 5. Se `estimate.seconds` passar
   de 400, rode o passo 5 com `run_in_background`, diga numa linha que a extração segue em segundo plano
   e espere a notificação: o Bash corta em 600 s.
5. **Prepare.** O mesmo comando sem `--estimar`, com `timeout` 600000. A última linha do stdout é um JSON
   (campos em [`reference/contratos.md`](reference/contratos.md)); o pedido vai para a pasta do run.
   - saída diferente de 0, ou `ok: false` → diga o problema numa frase, pelo código de saída:
     - `2` entrada, pelo `error.code`: `vtt_ausente` → mostre os candidatos e o `Rename-Item` sugerido
       no `detail` (quem decide renomear é o usuário); `video_nao_encontrado` → confirme o caminho;
       `run_grande_demais` (mais de 800 agentes) → proponha `--trecho` ou `--max-lotes`; pedido ou
       opção inválidos → corrija o pedido.
     - `3` ambiente (ffmpeg, Python, numpy, Pillow): ofereça `/video-analysis setup`.
     - `4` um vídeo falhou: diga qual (`errors`); se veio `workflow_args`, os outros estão prontos:
       pergunte se segue com eles (passo 6, com esse JSON).
     - `130` interrompido: as etapas prontas ficam no cache; ofereça rodar de novo.
     - `1` erro interno do `prepare.py`: diga o `detail` e o log em `logs\`; não tente contornar.

     Erro no passo 5 (não no 4) renomeia o pedido para `pedido.erro.md`: para repetir, grave-o de novo.
   - `env.permissions_ok: false` → avise que cada frame lido vai pedir permissão e ofereça o setup
     antes de disparar.
   - `estimate.agents` acima de 80 → diga lotes, agentes e a duração aproximada (≈ 4 min por lote, em
     série dentro do vídeo, vídeos em paralelo) e pergunte antes de seguir.
   - aviso de densidade com região sugerida → ofereça repetir com `ignorar_regiao: x,y,w,h` num pedido
     novo (menos frames, menos custo).

   Junte numa pergunta só o que precisar de resposta. Aviso que não pede resposta cabe numa das 3
   linhas do passo 6. Outros erros e avisos: [`reference/problemas.md`](reference/problemas.md).
6. **Dispare** pela ferramenta **Workflow** (adiada: carregue-a com `ToolSearch`, `select:Workflow`),
   com `args` = `workflow_args` **como objeto JSON, não como string, e sem editar**, nesta ordem:
   - `name: "video-analysis"`, o workflow salvo pelo setup em `~/.claude/workflows/`, só com
     `env.workflow_current: true` (a cópia salva é idêntica à da skill) e se o setup não o instalou nem
     renovou nesta conversa: o Workflow segue com a versão anterior na memória até a próxima conversa;
   - senão, ou se ela não conhecer o nome: se o Workflow já devolveu um caminho de script nesta conversa,
     copie o `script_path` por cima dele com Bash (`cp`) e passe esse caminho em `scriptPath`; sem ele,
     `script` = o conteúdo do `script_path`, lido com Read, sem editar (em `scriptPath` ela o recusa).
     Custa ~13k tokens: com a cópia salva desatualizada, sugira `/video-analysis setup` ao apresentar;
   - se ela não existir nesta sessão: [`reference/sem-workflow.md`](reference/sem-workflow.md).

   Responda em até 3 linhas (run_id, lotes, agentes, pasta do run, "aviso quando terminar") e **encerre
   o turno**, sem acompanhar.
7. **Apresente**, quando a notificação chegar, cada vídeo assim:

   ```
   **<slug>** — <status> · lotes <batches.from>–<batches.planned> de <batches.total> (<batches.ok> ok) · imagens <frames>
   <summary>
   Relatório: <report_path>
   ```

   Campo nulo sai da linha (`skipped`: vídeo já concluído, só o relatório; `from` maior que `planned`:
   nenhum lote novo). Depois, os lotes de `batches.partial` e `batches.failed`, até 3 avisos do vídeo e,
   se houver, o `combined` (summary e caminho; com `error`, a síntese falhou). `partial` com
   `--max-lotes` é cobertura parcial, não erro; `failed` → o último aviso diz o motivo; ofereça
   `/video-analysis retomar`. Termine oferecendo abrir um relatório ou rodar um `--trecho`.

## Retomar

Qual run: o run_id que o usuário deu; senão, o `run_id` (ou o `run_dir`) que o `prepare.py` devolveu
nesta conversa. Sem nenhum dos dois, ou se ele pediu o mais recente (`ultimo`), cuidado: o usuário roda
várias sessões, e o mais recente é o de **qualquer** uma. Ache-o com Glob em
`C:/Users/digom/video-analysis/runs/*/run.json` (o de pasta com nome maior, que começa por
`AAAAMMDD-HHMMSS`), confirme o run_id com o usuário e só então rode com esse id: o `--resume` já mexe
nos arquivos do run.

```bash
python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" --resume <run_id> --papeis inline
```

Troque `inline` pelo modo do passo 2 e acrescente `--max-lotes N` e `--curadoria plano|lotes` só se ele
pediu cada um; sem `--curadoria`, vale a do run (um run anterior à opção volta com `lotes`). Depois, os
passos 5 a 7. O Workflow recomeça no 1º lote sem relatório; vídeo com `relatorio.md` pronto fica de fora.

## Setup

Siga [`setup.md`](setup.md). O verificador fica na raiz da skill; rode-o sempre com este prefixo, que a
skill pré-aprova, mais as opções que o setup indicar (`--json`, `--instalar-agentes`,
`--aplicar-permissoes`):

```bash
python "${CLAUDE_SKILL_DIR}/verificar_ambiente.py"
```

`--instalar-agentes` e `--aplicar-permissoes` mudam arquivos fora da pasta central: só depois do "sim".

## Arquivos

Pasta central `C:\Users\digom\video-analysis\`: `cache\<slug>-<fp8>\` (legenda, frames e lotes,
reaproveitados entre runs) · `runs\<AAAAMMDD-HHMMSS>-<slug>\` (`relatorio.md` combinado, com 2+ vídeos;
por vídeo, `v1\relatorio.md`, `v2\…`, com ledger, prompts e relatórios de lote) · `pedidos\` · `roles\` ·
`logs\`. Formatos e caminhos: [`reference/contratos.md`](reference/contratos.md). Erros e avisos:
[`reference/problemas.md`](reference/problemas.md).
