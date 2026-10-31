# Sem a ferramenta Workflow: o mesmo laço com a ferramenta Agent

Use este roteiro só quando a ferramenta Workflow não existir nesta sessão, nem por `ToolSearch`
(`select:Workflow`). É o laço de `workflow/video-analysis.js` conduzido por você, com os mesmos prompts,
palavra por palavra (o script os mantém espelhados aqui). Os valores saem do `workflow_args` que o
`prepare.py` imprimiu, também na retomada.

## Regras

- **Não leia nenhum arquivo** do run nem do cache: nem prompt, nem ledger, nem relatório. Os agentes
  leem; você só monta os prompts abaixo e confere as respostas.
- Cada resposta de Agent é uma linha JSON (o rodapé pede isso). Guarde só uma tabela compacta por vídeo:
  lote, estado (`ok`, `partial`, `failed`), imagens vistas/esperadas e avisos.
- Não mostre prompts nem JSON ao usuário. No fim, apresente como no passo 7 do `SKILL.md`.
- O laço manual gasta contexto seu, cerca de 1k tokens por lote. Vídeo longo: prefira uma sessão com a
  ferramenta Workflow, ou divida com `--trecho`. Acima de 800 agentes estimados, não rode: divida.

## Como chamar a ferramenta Agent

| papel | `subagent_type` | `model` | `prompt` |
|---|---|---|---|
| curador: INIT, NEXT, FINAL, CROSS | `video-analysis-curator`; no modo `inline`, `general-purpose` | `opus` | [preâmbulo, só no `inline`] + texto do modo + [nota de 2ª tentativa] + rodapé |
| analista | `video-analysis-analyst`; no modo `inline`, `general-purpose` | `sonnet` | [preâmbulo, só no `inline`] + texto do analista + [nota de 2ª tentativa] + rodapé |

- O modo vem de `roles_mode` nos args. As partes do prompt se juntam com uma linha em branco entre elas.
- `description`: o rótulo do Workflow, `plano · <slug>`, `curadoria · <slug> 3/7`, `análise · <slug> 3/7`,
  `relatório · <slug>` ou `síntese`, com ` · 2ª` na segunda tentativa.
- **Um vídeo:** passe `run_in_background: false`, se o parâmetro existir; o passo seguinte depende do
  resultado. **Vários vídeos:** até `options.max_parallel_videos` ao mesmo tempo; mande na mesma mensagem
  a próxima chamada de cada vídeo ativo e avance cada um com a resposta dele. Os lotes de um vídeo são
  sempre em série.
- **Sem o parâmetro `model`**, no modo `inline` o analista roda no modelo da sessão (Opus, bem mais caro).
  Para trabalho longo, rode o setup e reabra o Claude Code antes, para usar o modo `agents`.
- **Esforço:** INIT, FINAL e CROSS em `high`; NEXT em `options.curator_effort`; analista em
  `options.analyst_effort` (os dois `high` por padrão). Sem parâmetro de esforço na ferramenta Agent, vale
  o `effort` do frontmatter do papel (modo `agents`) ou o da sessão (`inline`).
- **Resposta:** a última linha da resposta é o JSON. Resposta que não é JSON válido é tentativa perdida.
- **Motivo de uma falha:** os `warnings` da resposta unidos por `; ` (no FINAL e no CROSS, o `summary`),
  até 160 caracteres. Nos avisos abaixo, `{motivo}` entra como ` (<motivo>)` e some quando não houver.

## O laço, por vídeo

Para cada vídeo de `videos`:

0. **Já concluído** (`final_exists: true`): nenhuma chamada. Status `skipped`, relatório
   `{out_dir}/relatorio.md`.
1. **Contas.** `B` = número de itens de `batch_images`. `last` = `B`, ou `options.max_batches` se for
   menor e maior que 0 (modo teste). `start` = `start_batch` se `ledger_exists`, senão 1. `pendentes` =
   lista vazia (lotes analisados que nenhuma curadoria integrou ainda). `retomada` = `ledger_exists` e
   `start` > 1. Contadores de falha seguida do curador e do analista em 0.
2. **Plano.** Sem ledger (`ledger_exists: false`): INIT, que vale com `status: "ok"`; senão, repita uma
   vez com a nota de 2ª tentativa do curador. Falhou nas duas → vídeo `failed`, aviso
   `plano (INIT) falhou duas vezes{motivo}`, fim do vídeo. Com INIT bom, o lote 1 já tem prompt. Com
   ledger, o lote `start` tem prompt se `start_prompt_exists`.
3. **Lotes**, para `n` de `start` até `last`, em série:
   1. **Curadoria**, só se `options.curation` é `batches` e o lote `n` ainda não tem prompt: NEXT `n`, que
      vale com `status: "ok"` (2 tentativas, como o INIT). Sem `curation` ou com `plan`, não há NEXT: o lote
      sem prompt vai direto ao analista de contingência (texto 4).
      - Valeu: `pendentes` volta a vazio, `retomada` = falso, falhas do curador = 0. Para cada item de
        `coverage` com `state` diferente de `complete`, rebaixe o lote (`incomplete` → `partial`,
        `missing` → `failed`) e avise `lote {batch}: relatório incompleto` ou `… relatório ausente`. Se
        `integrated_through` < `n` − 1, avise `ledger integrado só até o lote {integrated_through} ao
        preparar o {n}`.
      - Falhou nas duas: avise `curadoria do lote {n} falhou{motivo}: prompt de contingência` e siga com o
        analista de contingência. Se foi a segunda curadoria seguida a falhar → vídeo `failed`, aviso
        `curador falhou em 2 lotes seguidos: vídeo interrompido`, fim do vídeo.
   2. **Análise:** o analista do lote `n`, com o texto 3 (há prompt do curador) ou o 4 (contingência).
      Esperado = `batch_images[n-1]`. **Caminho errado:** resposta com `status` ≠ `failed` cujo
      `report_path` difere de `{out_dir}/reports/batch_{NNN}.md` mesmo lido com `\` como `/`, barras
      repetidas como uma, sem espaços nas pontas e sem distinguir maiúsculas. Vale se `status` ≠ `failed`,
      o caminho não está errado, `frames_seen` ≥ esperado e `frames_missing` vazio. Senão, segunda
      tentativa: o mesmo texto + a nota 5, ou a 5b se a primeira veio com caminho errado. Na segunda,
      `frames_seen` já soma as duas tentativas.
      - Valeu numa das duas → `ok`. Senão, a resposta com mais `frames_seen` entre as que têm `status` ≠
        `failed`, caminho certo e `frames_seen` > 0 → `partial`; nenhuma → `failed`, com aviso
        `lote {n}: relatório gravado em caminho errado ({onde})` se alguma veio com caminho errado
        (`{onde}` como na nota 5b, da segunda se foi ela), senão `lote {n}: análise falhou{motivo}` se
        houver motivo.
      - Imagens vistas = min(`frames_seen`, esperado), 0 se `failed`; some vistas e esperadas do vídeo.
      - Acrescente o lote a `pendentes`, com estado e contagem.
      - Segundo lote seguido `failed` → vídeo `failed`, aviso
        `analista falhou em 2 lotes seguidos: vídeo interrompido`, fim do vídeo. Lote que não falhou
        zera o contador.
4. **Relatório:** FINAL, que vale com `status` ≠ `failed` (2 tentativas). Falhou nas duas → vídeo
   `failed`, aviso `relatório final falhou duas vezes{motivo}: rode /video-analysis retomar`. Valeu →
   guarde `report_path` (vazio: `{out_dir}/relatorio.md`), `summary` e `answered`. Status do vídeo:
   `partial` se houve lote `partial` ou `failed`, se o FINAL voltou `partial` ou se `last` < `B`; senão
   `ok`.

Depois de todos os vídeos: **CROSS**, se dois ou mais vídeos têm relatório e status diferente de `failed`
(`ok`, `partial` ou `skipped`) e `combined_exists` é falso. Vale com `status` ≠ `failed` (2 tentativas):
`combined` = `{report_path, answered, summary}` (caminho vazio: `{run_dir}/relatorio.md`); falhou nas
duas, `combined` = `{error: "síntese falhou duas vezes{motivo}"}`.

Avisos: até 6 por vídeo; com mais, os 4 primeiros, `+N avisos omitidos` e o último (num vídeo
interrompido, o motivo). Vídeo interrompido deixa os
arquivos para `/video-analysis retomar`.

## Prompts

Copie cada linha como está. `{…}` é um valor a substituir: os nomes iguais aos dos args (`{slug}`,
`{duration}`, `{objective_path}`, `{batches_dir}`, `{out_dir}`, `{language}`, `{run_id}`, `{run_dir}`,
`{roles_dir}`) vêm deles. `{n}` é o número do lote sem zeros e `{NNN}`, com 3 dígitos (`003`). Os demais
estão definidos abaixo de cada bloco; quando a condição não vale, entram vazios.

### Preâmbulo (só no modo inline), antes do texto do modo

```text
Antes de qualquer outra ação, leia com Read o arquivo {roles_dir}/{papel}.md. Ele define seu papel e suas regras: siga-o como suas instruções de sistema (ignore o bloco entre --- no topo). Se não conseguir lê-lo, devolva status "failed" com warnings ["papel ilegível"].
```

- `{papel}`: `video-analysis-curator` ou `video-analysis-analyst`.

### Nota de 2ª tentativa do curador (INIT, NEXT, FINAL e CROSS), depois do texto do modo

```text
SEGUNDA TENTATIVA: a anterior falhou. Se algum arquivo de saída já existir, leia-o antes de sobrescrever (o Write exige) e complete o que faltar.
```

### 1. INIT

```text
MODO: INIT
Vídeo: {slug} · {duration} · {B} lotes{teste}
Objetivo (literal) e opções: {objective_path}
Resumo do vídeo e dos lotes: {batches_dir}/summary.json
Transcrição: {batches_dir}/transcript.md
Visão geral (se existir): {batches_dir}/overview.jpg
Lote 1: linha do tempo {batches_dir}/batch_001.md · prancha {batches_dir}/sheet_001.jpg · {batch_images[0]} imagens
Idioma dos relatórios: {language}
Grave: ledger em {out_dir}/ledger.md; depois o prompt do lote 1 em {out_dir}/prompts/batch_001.md.
```

- `{duration}`: a `duration` do vídeo, ou `?` se vier vazia.
- `{teste}`: ` (este run analisa só os lotes 1–{last}; planeje todos e marque os demais como fora deste run)`
  quando `last` < `B`.

### 2. NEXT

```text
MODO: NEXT
Vídeo: {slug} · preparar o lote {n}/{last}{último}
Objetivo: {objective_path}
Ledger: {out_dir}/ledger.md
Relatórios a integrar:
- lote {PPP}: {estado} → {out_dir}/reports/batch_{PPP}.md{falas}
Confira "Integrado até" no ledger e integre os relatórios existentes em {out_dir}/reports até o lote {MMM}.
Próximo lote: linha do tempo {batches_dir}/batch_{NNN}.md · prancha {batches_dir}/sheet_{NNN}.jpg · {batch_images[n-1]} imagens
Grave: ledger atualizado em {out_dir}/ledger.md; depois o prompt em {out_dir}/prompts/batch_{NNN}.md.
```

- `{último}`: ` (último deste run)` quando `n` = `last`.
- `Relatórios a integrar:` e as linhas `- lote …` entram só se `pendentes` não estiver vazia: uma linha por
  lote pendente, na ordem. `{PPP}` é o número dele com 3 dígitos.
  - `{estado}`: `ok, {vistas}/{esperadas} imagens`, `PARCIAL, {vistas}/{esperadas} imagens` ou
    `FALHOU, sem relatório confiável`.
  - `{falas}`: ` (falas do trecho: {batches_dir}/batch_{PPP}.md)` só para lote que falhou.
- A linha `Confira "Integrado até"…` entra só quando `n` > 1 e (`retomada` ou `pendentes` vazia).
  `{MMM}` = `n` − 1 com 3 dígitos.

### 3. Analista, com prompt do curador

```text
Lote {n}/{last} do vídeo {slug}.
Prompt do curador: {out_dir}/prompts/batch_{NNN}.md (leia primeiro e siga).
Linha do tempo: {batches_dir}/batch_{NNN}.md
Relatório: grave em {out_dir}/reports/batch_{NNN}.md
Imagens do lote: {batch_images[n-1]} · Idioma do relatório: {language}
```

### 4. Analista de contingência, sem prompt do curador

```text
Lote {n}/{last} do vídeo {slug}. Não há prompt do curador para este lote: siga o contrato padrão do seu papel.
Objetivo: {objective_path}. Contexto: seções 4 e 5 de {out_dir}/ledger.md, se existir.
Linha do tempo: {batches_dir}/batch_{NNN}.md
Relatório: grave em {out_dir}/reports/batch_{NNN}.md
Imagens do lote: {batch_images[n-1]} · Idioma do relatório: {language}
```

### 5. Nota de 2ª tentativa do analista, depois do texto 3 ou 4

```text
SEGUNDA TENTATIVA. A anterior {resultado}. Se {out_dir}/reports/batch_{NNN}.md existir, leia-o antes de sobrescrever; abra as imagens que faltaram (na dúvida, todas) e grave o relatório completo.
```

- `{resultado}`: `abriu {frames_seen} de {batch_images[n-1]} imagens{faltaram}` se a primeira tentativa
  devolveu JSON; `não terminou` se não devolveu.
- `{faltaram}`: ` (faltaram: {frames_missing})`, com os ids separados por `, `, quando a lista não está
  vazia.

### 5b. Nota de 2ª tentativa do analista quando a anterior veio com caminho errado, no lugar da 5

```text
SEGUNDA TENTATIVA. A anterior {resultado}, mas gravou o relatório em {onde}, e não no caminho pedido. Grave o relatório completo exatamente em {out_dir}/reports/batch_{NNN}.md, copiando o caminho caractere por caractere, e devolva esse mesmo caminho em report_path. Antes, leia o que existir nesses dois caminhos e aproveite o que estiver certo; abra as imagens que faltaram (na dúvida, todas).
```

- `{resultado}`: `abriu {frames_seen} de {batch_images[n-1]} imagens{faltaram}`, como na nota 5.
- `{onde}`: o `report_path` que a anterior devolveu, com `/` no lugar de `\` e sem espaços nas pontas;
  `?` se veio vazio.

### 6. FINAL

```text
MODO: FINAL
Vídeo: {slug} · lotes analisados: {planned} de {B}{parcial}
Objetivo: {objective_path}
Ledger: {out_dir}/ledger.md
Relatórios de lote: {out_dir}/reports/batch_001.md … batch_{LLL}.md
Ainda não integrados ao ledger (neste run): {não_integrados}; confira também "Integrado até".
Parciais: {parciais} · Falhos: {falhos}
{imagens_vistas}
Transcrição (consulta pontual): {batches_dir}/transcript.md
Idioma: {language}
Grave o relatório final em {out_dir}/relatorio.md.
```

- `{planned}` = `last`; `{LLL}` = `last` com 3 dígitos.
- `{parcial}`: ` (cobertura parcial: diga isso no relatório)` quando `last` < `B`.
- `{não_integrados}`: cada lote de `pendentes` como `{PPP} ({estado})`, com estado `ok`, `partial` ou
  `failed`, separados por `, `; ou `nenhum`.
- `{parciais}` e `{falhos}`: os números dos lotes `partial` e `failed`, sem zeros, separados por `, `; ou
  `nenhum`.
- `{imagens_vistas}`, com `{vistas}` e `{esperadas}` somadas neste run, `{SSS}` = `start` e `{RRR}` =
  `start` − 1, os dois com 3 dígitos:
  - `start` = 1: `Imagens vistas: {vistas} de {esperadas}`
  - 1 < `start` ≤ `last`: `Imagens vistas: {vistas} de {esperadas} nos lotes {SSS}–{LLL} deste run; lotes 001–{RRR}: run anterior (veja o ledger)`
  - `start` > `last` (só o FINAL): `Imagens vistas: nenhum lote novo neste run; lotes 001–{LLL}: run anterior (veja o ledger)`

### 7. CROSS

```text
MODO: CROSS
Run: {run_id}
Objetivo: {objective_path}
Relatórios por vídeo:
- {slug}: {report_path} ({status})
Idioma: {language}
Grave o relatório combinado em {run_dir}/relatorio.md.
```

- Uma linha `- …` por vídeo com relatório e status diferente de `failed`, na ordem de `videos`.

### Rodapé (só neste roteiro), depois de tudo

O Workflow valida as respostas por schema. Aqui não há StructuredOutput, então cada chamada termina com o
rodapé do papel:

```text
Não há StructuredOutput nesta chamada: ao terminar, responda só com uma linha JSON com os campos status ("ok" ou "failed"), ledger_path, prompt_path, video_type ("screen", "meeting", "slides", "mixed" ou "other"), focus e, se houver, warnings (lista de textos).
```

```text
Não há StructuredOutput nesta chamada: ao terminar, responda só com uma linha JSON com os campos status ("ok" ou "failed"), integrated_through (inteiro), coverage (lista de {"batch": inteiro, "state": "complete", "incomplete" ou "missing"}), prompt_path, focus e, se houver, carried e warnings (lista de textos).
```

```text
Não há StructuredOutput nesta chamada: ao terminar, responda só com uma linha JSON com os campos status ("ok", "partial" ou "failed"), report_path, frames_seen (inteiro), frames_missing (lista de ids; [] se nenhum), notable e, se houver, warnings (lista de textos).
```

```text
Não há StructuredOutput nesta chamada: ao terminar, responda só com uma linha JSON com os campos status ("ok", "partial" ou "failed"), report_path, answered ("yes", "partly" ou "no"), summary (até 120 palavras; em falha, o motivo) e, se houver, gaps (lista de textos).
```

Na ordem: INIT, NEXT, analista (textos 3 e 4), FINAL e CROSS.
