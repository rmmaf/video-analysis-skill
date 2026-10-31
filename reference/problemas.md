# Problemas: sintoma, causa e o que fazer

O `prepare.py` grava o progresso em `C:\Users\digom\video-analysis\logs\` e o estado em
`runs\<run_id>\status.json`. Os erros dele saem como `{"ok": false, "error": {"code", "detail"}}`, com o
código de saída `2` (entrada), `3` (ambiente), `4` (um vídeo falhou), `130` (interrompido) ou `1` (erro
interno); a tabela dos `error.code` está em [`contratos.md`](contratos.md), seção 2. Erro no prepare
renomeia o pedido para `pedido.erro.md` (o `--estimar` não mexe nele). Os comandos citados aqui são os do
`SKILL.md`, com o mesmo prefixo pré-aprovado.

## Preparação (`prepare.py`)

### Legenda `.vtt` não encontrada (saída 2)

- **Causa:** a regra é estrita: a legenda tem **o mesmo nome do vídeo**, só com a extensão `.vtt`, na
  mesma pasta. O `detail` lista os candidatos da pasta (nomes que são prefixo do vídeo ou parecidos) e
  sugere um `Rename-Item`. Nada é renomeado sozinho.
- **Padrão do Teams:** a gravação sai como `<Reunião>-AAAAMMDD_HHMMSS-Gravação de Reunião.mp4` (ou
  `…-Meeting Recording.mp4`), e a transcrição baixada, como `<Reunião>.vtt`. Exemplo:

  ```powershell
  Rename-Item -LiteralPath 'C:\…\Indicador PES.vtt' -NewName 'Indicador PES-20260610_150844-Gravação de Reunião.vtt'
  ```

- **O que fazer:** mostre os candidatos e o comando sugerido (também em `missing_vtt`). Rode-o, no
  PowerShell, só depois do "sim" do usuário, ou deixe que ele rode. Depois, repita: se o erro veio do
  prepare, grave o pedido de novo (o anterior virou `pedido.erro.md`); se veio do `--estimar`, o
  `pedido.md` continua lá.
  - Renomeie a `.vtt`, não o vídeo: vídeo renomeado ganha outra chave de cache e é extraído de novo.
  - Use `-LiteralPath`: com `-Path`, colchetes no nome (`[PES] …`) viram curinga.
  - No PowerShell, `’` e `‘` também fecham string entre aspas simples: dentro dela, dobre-os (`’’`).
  - Com um vídeo só, a alternativa é apontar a legenda: `--vtt "<caminho da .vtt>"` no comando do prepare.
  - Numa pasta com vídeos sem legenda, `--skip-missing` no comando do prepare pula esses vídeos.

### Vídeo não encontrado (saída 2)

- **Causa:** caminho errado, ou cortado errado ao separar do objetivo (pasta com espaços, sem aspas).
  Acentos, `’`, nomes vindos do Mac, espaços duplos e colchetes o `prepare.py` já resolve.
- **O que fazer:** confirme o caminho com o usuário e grave o pedido de novo. A busca numa pasta não
  entra em subpastas: liste cada subpasta numa linha de `# videos`.

### Pedido ou opção inválidos (saída 2)

- **Causa:** `pedido_invalido` (seção faltando ou repetida, texto antes de `# videos`, linha de opção sem
  `:`), `objetivo_ausente`, `opcao_invalida` (chave desconhecida ou valor fora do aceito) ou
  `uso_invalido` (opções do comando que não combinam). O `detail` diz qual.
- **O que fazer:** corrija e grave o pedido de novo, no formato de [`contratos.md`](contratos.md), seção 1.

### ffmpeg, ffprobe, Python, numpy ou Pillow ausentes (saída 3)

- **O que fazer:** `/video-analysis setup`. Logo depois de instalar algo, o PATH novo só vale num Claude
  Code reaberto. `workspace_inacessivel` é a pasta central sem gravação (passo 3 do setup);
  `instalacao_incompleta` é a pasta da skill sem `scripts\va` inteiro: reinstale a skill.

### Um vídeo falhou (saída 4)

- **Causa:** arquivo corrompido, sem faixa de vídeo, ou ainda sendo baixado ou gravado. O motivo está em
  `errors` e no log.
- **O que fazer:** diga qual vídeo e por quê, numa frase, e pergunte se segue com os outros. O run deles
  está pronto: `run.json` gravado e `workflow_args` no JSON (passo 6 do `SKILL.md`, com esse JSON). O pedido
  virou `pedido.erro.md`, com uma cópia na pasta do run.

### Erro interno (saída 1)

- **Causa:** um bug do `prepare.py` (`erro_interno`). O traceback fica no log em `logs\`.
- **O que fazer:** diga o `detail` e o caminho do log numa frase; não tente contornar com outro comando
  nem com script próprio.

### Interrompido (saída 130), ou cortado pelo limite de 600 s do Bash

- **Causa:** Ctrl+C, Claude Code fechado, ou extração longa rodando em primeiro plano.
- **O que fazer:** rode de novo, em segundo plano (`run_in_background`). Estágios completos ficam no
  cache; uma extração de frames interrompida recomeça do zero, porque não há ponto de retomada no meio
  dela. Em 1080p60, conte ~0,15× a duração do vídeo; em Teams 1080p, ~0,06×.

### A última linha do stdout não veio inteira

- **O que fazer:** leia `<run_dir>\prepare.json`, que tem o mesmo JSON. O `run_dir` é a primeira linha do
  stderr (`run_dir: …`).

### "File has not been read yet" ao gravar o `pedido.md`

- **Causa:** sobrou um `pedido.md`, por exemplo depois de um `--estimar` sem o prepare em seguida.
- **O que fazer:** leia o arquivo e grave de novo.

### O prepare parece parado logo no começo

- **Causa:** outro `prepare.py` está usando o mesmo vídeo (trava do cache). Ele espera até 30 min; trava
  sem sinal de vida há 10 min é descartada.
- **O que fazer:** espere, ou confira se há outro run do mesmo vídeo em andamento.

### Caracteres estranhos na saída, ou `python` abre a Microsoft Store

- **Causa:** comando rodado pelo PowerShell, que redecodifica a saída em cp1252, ou com `python3`, que no
  Windows é o atalho da loja.
- **O que fazer:** sempre a ferramenta Bash e sempre `python`, exatamente como no `SKILL.md`.

### Mais de 80 agentes, ou "run grande demais" (mais de 800)

- **Causa:** vídeo longo. Uma reunião de 55 min dá ≈ 50 lotes, ≈ 100 agentes e horas em série.
- **O que fazer:** acima de 80, confirme com o usuário antes de disparar. Acima de 800, o prepare sai
  com `run_grande_demais` e o Workflow recusa (o teto dele é 1000); `--forcar` só faz o prepare aceitar.
  Divida em runs menores, use `--trecho` no que importa, ou `--max-lotes` para testar o objetivo antes.

## Frames e legenda

### Aviso de densidade: frames demais por minuto

- **Causa:** mais de 12 frames/min no vídeo, ou mais de 30 num minuto qualquer. Em geral, uma região que
  muda sozinha escapou da máscara automática: webcam, relógio, animação, notificação.
- **O que fazer:** se o aviso trouxer região sugerida, ofereça repetir com `--ignorar-regiao x,y,w,h` (no
  pedido, `ignorar_regiao`). Sem sugestão, `--sensibilidade low`. A legenda vem do cache; os frames são
  extraídos de novo. Detalhes em [`parametros.md`](parametros.md).

### Uma mudança pequena não virou frame

- **Causa:** é de propósito: um dígito ou até ~4 caracteres parecem cursor e contam como duplicado. Elas
  se acumulam contra o último frame mantido, então uma sequência delas acaba aparecendo.
- **O que fazer:** `--sensibilidade high` (mais frames, mais custo).

### Troca só de cor não aparece

- **Causa:** a comparação é em tons de cinza: vermelho ↔ verde com o mesmo brilho não muda nada. Ícone
  que também muda de forma é detectado.
- **O que fazer:** nada nesta versão; é limitação conhecida.

### Filmagem de câmera com poucos frames

- **Causa:** em câmera tudo se mexe, então a extração cai para ~2 frames/min e avisa.
- **O que fazer:** nada: é o esperado. O conteúdo vem mais da fala.

### Painel que se atualiza sozinho sumiu da comparação

- **Causa:** a máscara de regiões quentes pegou uma animação longa da própria tela; aparece nos avisos.
- **O que fazer:** só manual: `--hot-mode off` na linha de comando do prepare.

### A legenda cobre menos da metade do vídeo, ou passa do fim

- **Causa:** legenda de outro vídeo, ou transcrição do Teams que começou depois da gravação.
- **O que fazer:** confira se é a `.vtt` certa. Para deslocar todas as falas: `--vtt-offset SEC` na linha
  de comando do prepare (uso manual).

## Workflow e agentes

### Pedido de permissão a cada frame ou relatório

- **Causa:** faltam as regras de permissão da pasta central, a grafia não pegou, ou o Claude Code ainda
  não releu o `settings.json`. O `prepare.py` acusa com `env.permissions_ok: false`.
- **O que fazer:** `/video-analysis setup` e, depois, Claude Code reaberto. Se continuar com as regras
  gravadas, é a grafia: as duas formas (`~/…` e `//c/…`) ficam até a primeira análise mostrar qual vale.

### Pedido de permissão para o próprio `prepare.py` ou para o verificador

- **Causa:** o comando não casou com a pré-aprovação da skill: PowerShell em vez de Bash, `python3`,
  outro caminho, ou o jeito como `${CLAUDE_SKILL_DIR}` é substituído no Windows.
- **O que fazer:** use o comando exatamente como no `SKILL.md`. Se ainda pedir, o usuário aprova uma vez,
  com "não perguntar de novo".

### Tipo de agente não encontrado (`video-analysis-curator`, `video-analysis-analyst`)

- **Causa:** os agentes foram instalados depois que esta sessão abriu; agente novo só carrega com o
  Claude Code reaberto.
- **O que fazer:** `--papeis inline` agora, ou reabra o Claude Code. Cópia instalada desatualizada o
  `prepare.py` já rebaixa para `inline` sozinho, com aviso.

### A ferramenta Workflow não existe nesta sessão

- **O que fazer:** siga [`sem-workflow.md`](sem-workflow.md).

### O Workflow recusa o `scriptPath`, ou não conhece o nome `video-analysis`

- **Sintoma:** "scriptPath must be a script path this tool returned, or a file you can already read…", ou
  a ferramenta diz que não há workflow com o nome `video-analysis`.
- **Causa:** em `scriptPath`, a ferramenta só aceita o caminho que ela mesma devolveu ou um arquivo da
  pasta da sessão, nunca o `script_path` da pasta da skill. O nome só existe com o workflow salvo em
  `C:\Users\digom\.claude\workflows\video-analysis.js`, que o setup instala (passo 4) e que pode só
  aparecer num Claude Code reaberto.
- **O que fazer:** agora, chame o Workflow com `script` = o conteúdo do `script_path` (lido com Read, sem
  editar) e os mesmos `args`. Custa ~13k tokens; na mesma conversa, um run seguinte pode passar em
  `scriptPath` o caminho que o Workflow devolveu. Depois, `/video-analysis setup`; se o nome continuar
  desconhecido, reabra o Claude Code. Com `env.workflow_current: false` no JSON do `prepare.py` (cópia
  salva ausente ou de outra versão), faça o mesmo sem tentar o nome, que rodaria o script antigo.

### `workflow_args inválido (…)`

- **Causa:** os args foram editados ou passados como string, ou o workflow salvo em
  `~/.claude/workflows/` é de uma versão anterior da skill.
- **O que fazer:** passe `workflow_args` como objeto JSON, exatamente como o `prepare.py` imprimiu. Se o
  JSON se perdeu, `--resume <run_id>` imprime de novo. Se os args estavam certos e o verificador acusa o
  workflow `diferente` (o `prepare.py`, com `env.workflow_current: false`), rode o setup (passo 4) e, até
  lá, passe o script inline (passo 6 do `SKILL.md`).

### Agente não grava o relatório: "Subagents should return findings as text…"

- **Sintoma:** o Write de um agente falha com "Subagents should return findings as text, not write report
  files. Include this content in your final response instead.", e o relatório final ou o combinado não
  aparece (`relatório final falhou duas vezes`, `síntese falhou duas vezes`).
- **Causa:** o Claude Code bloqueia subagentes de gravar arquivos chamados exatamente `report.md`,
  `summary.md` ou `findings.md`; outros nomes, como `relatorio.md`, passam. Por isso o relatório final é
  `relatorio.md`, e os dois papéis proíbem esses três nomes e mandam gravar exatamente no caminho da
  chamada. Com a skill atualizada, só aparece num run disparado com uma cópia antiga do workflow ou dos
  papéis.
- **O que fazer:** `/video-analysis setup` (o `--instalar-agentes` renova os papéis em `~/.claude/agents/`
  e o workflow salvo) e `/video-analysis retomar`: os lotes prontos não são refeitos, só o que falta roda,
  e um `report.md` de run antigo vale como relatório pronto. Na conversa do setup, dispare o `retomar` sem
  o nome (seção seguinte).

### O Workflow roda a versão antiga (tenta gravar `report.md`, ou o retorno traz `output_tokens`)

- **Causa:** o workflow salvo em `~/.claude/workflows/` mudou com esta sessão aberta, em geral pelo
  `--instalar-agentes` do setup (o verificador avisa). A ferramenta Workflow guarda na memória, por
  sessão, o workflow salvo que já leu: até o Claude Code reabrir, `name: "video-analysis"` segue rodando a
  versão anterior, mesmo com o arquivo novo e `env.workflow_current: true`.
- **O que fazer:** nesta conversa, não use o nome. Se o Workflow já devolveu um caminho de script nela,
  copie o `script_path` por cima dele com Bash (`cp "<script_path>" "<caminho devolvido>"`, sem gastar
  tokens) e chame com `scriptPath` = esse caminho e os mesmos `args`; sem ele, passe `script` = o conteúdo
  do `script_path`, lido com Read, sem editar (~13k tokens). Ou feche e abra o Claude Code: numa sessão
  nova, o nome lê o arquivo novo. O run que a versão antiga deixou incompleto segue com
  `/video-analysis retomar`, disparado do mesmo jeito.

### Lote `partial` ou `failed`

- **Causa:** o analista não abriu todas as imagens, nem na segunda tentativa, ou não gravou o relatório.
  O relatório final lista as lacunas.
- **O que fazer:** se o trecho importa, rode um run com `--trecho` cobrindo-o; o cache é reaproveitado.

### Vídeo `failed`: "falhou em 2 lotes seguidos"

- **Causa:** quase sempre sistêmica: permissão, papel ilegível em `roles\`, cota.
- **O que fazer:** resolva a causa (setup, cota) e `/video-analysis retomar`: nada pronto é refeito.

### Run interrompido: Claude Code fechado, cota, erro

- **O que fazer:** `/video-analysis retomar`, para o mais recente, ou `retomar <run_id>`. A retomada é
  pelos arquivos: o ledger diz até que lote integrou. Se o cache tiver sido apagado (`--gc`), o
  `--resume` reconstrói os estágios com os mesmos parâmetros e pode demorar como uma extração nova.

### Limite de taxa ou cota

- **O que fazer:** `--paralelo 1`, `--esforco-analista medium` (ou `low`) e `--max-lotes` para testar; sem `--curadoria lotes`.

### Aviso "orçamento de tokens no fim"

- **Causa:** a meta de tokens da conversa (um `+500k`, por exemplo) acabou, e o vídeo parou antes de um
  lote.
- **O que fazer:** `retomar` com meta maior, ou sem meta.

### Modo `inline` mais caro que o esperado

- **Causa:** cada agente inline carrega o prompt padrão de subagente, a lista de ferramentas da sessão e
  o CLAUDE.md da pasta.
- **O que fazer:** setup com `--instalar-agentes` e Claude Code reaberto, para usar o modo `agents`.

### Resposta fraca, ou `answered: no`

- **Causa:** objetivo vago ou amplo demais.
- **O que fazer:** reformule o objetivo e rode de novo: a extração vem do cache, só os agentes rodam.

### `retomar` não faz nada

- **Causa:** o run já está completo, com `relatorio.md` em todo vídeo.
- **O que fazer:** para outra pergunta sobre o mesmo vídeo, um run novo; o cache é reaproveitado.

### `retomar` falha com `run_nao_encontrado` ou `run_inconsistente` (saída 2)

- **Causa:** o id não existe em `runs\` (ou é prefixo de mais de um run), o prepare daquele run falhou
  antes de gravar o `run.json`, ou faltam `run.json`/`objective.md` na pasta do run.
- **O que fazer:** confira o id com o usuário (os runs ficam em `C:\Users\digom\video-analysis\runs\`); um
  run sem `run.json` não tem como ser retomado: rode a análise de novo, que o cache é reaproveitado.
