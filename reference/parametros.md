# Parâmetros: o que cada um faz e quando mexer

Os valores abaixo são os calibrados nas fixtures reais e nos vídeos sintéticos (`ALGO_VERSION`
`dedup-1.1`), congelados em `scripts/va/__init__.py` (`DEDUP_DEFAULTS`, `OUTPUT_DEFAULTS`,
`BATCH_DEFAULTS`); as medições e o motivo de cada ajuste ao design estão em `tests/data/calibration.md`.
Mudar um parâmetro de frames muda a chave do cache: o vídeo é extraído de novo.

## Pela skill: as opções da chamada

| opção | quando usar | o que custa |
|---|---|---|
| `--sensibilidade high` | uma mudança pequena que importa não virou frame: um número, uma palavra curta | mais frames, mais lotes, mais tokens |
| `--sensibilidade low` | frames demais por ruído de compressão ou cursor grande | pode perder mudança pequena |
| `--ignorar-regiao x,y,w,h` | o aviso de densidade sugeriu uma região: webcam, relógio, animação | o que muda ali deixa de contar |
| `--trecho hh:mm:ss-hh:mm:ss` | só uma parte interessa, ou aprofundar um trecho de um run anterior | o primeiro trecho de um vídeo novo ainda extrai o vídeo inteiro (fica no cache) |
| `--max-lotes N` | testar o objetivo antes do run inteiro | cobertura parcial, declarada no relatório |
| `--falantes "SPEAKER_01=Nome;…"` | a legenda tem rótulos genéricos (WhisperX) e você sabe os nomes | refaz só legenda e lotes, sem reextrair frames |
| `--paralelo N` | `1` contra limite de taxa; até 6 com muitos vídeos curtos | — |
| `--esforco-analista medium` (ou `low`), `--esforco-curador medium` | economizar em vídeo simples, sem tela densa | medido em 14/10 (13 lotes, tela de Excel): `medium` custou ~US$ 0,7 a menos (−13%), mas teve ~5 p.p. menos fatos completos e ~3 p.p. menos precisão |
| `--curadoria lotes` | vídeo longo em que um lote muda a leitura do seguinte (uma demonstração em várias etapas) | curador Opus antes de cada lote: ≈ 2× o custo (medido: US$ 9,9 contra 5,4 num vídeo de 13 lotes, sem ganho de recall) |

`--sensibilidade` e `--ignorar-regiao` mudam a extração: os frames são extraídos de novo, e a legenda vem
do cache. Para várias regiões, separe-as com `;` no pedido (`ignorar_regiao: 0.80,0.75,0.20,0.25;
0.00,0.00,0.20,0.06`). Os demais parâmetros desta página existem só na linha de comando do `prepare.py`,
para uso manual e calibração; a skill não os passa.

**Região em frações.** `x,y` é o canto superior esquerdo e `w,h` a largura e a altura, de 0 a 1 em
relação ao quadro exibido. Exemplos: webcam no canto inferior direito, `0.80,0.75,0.20,0.25`; faixa de
relógio no topo, `0.00,0.00,1.00,0.05`. Prefira a região que o aviso sugere, que já vem medida.

## Como um frame vira "único"

1. **Amostragem.** 2 amostras por segundo, em tons de cinza a 640×360, só para comparar.
2. **Comparação com o último frame mantido**, não com a amostra anterior: mudança lenta e digitação se
   acumulam até contar.
3. **Dois limiares por pixel**, na diferença de brilho (0 a 255): acima do fraco, "mudou um pouco";
   acima do forte, "mudou muito". Os pixels mudados viram manchas numa grade, e manchas de ruído caem:
   as minúsculas e as suaves demais, com menos de 25% de pixels fortes (sombreado de hover, granulação de
   câmera, recompressão de quadro-chave).
4. **Cursor.** Até 3 manchas do tamanho de um ponteiro (≈ 49 px de altura em 1080p, o que cobre escala
   de 150%) contam como "só cursor": a posição antiga, a nova e o cursor de texto piscando. Até 6
   manchinhas desse tamanho que somem no máximo 2 cursores também: o ponteiro mais o destaque da barra de
   tarefas ou das abas.
5. **Regiões quentes.** O que muda sozinho por muito tempo (webcam, anel de quem fala) sai da comparação.
   Cronômetro, relógio e spinner também, por um critério próprio ("ticker"): conteúdo novo do tamanho de
   um cursor em ao menos 30% dos pares de amostras; o cursor de texto piscando volta ao mesmo estado e não
   conta. A máscara é calculada numa janela de ±30 s, cortada nas trocas de layout (mudança grande seguida
   de calma em até 2 s, o que pega também a tela que carrega aos poucos), para não esconder conteúdo da
   tela compartilhada.
6. **Estabilização.** A mudança só vira frame depois de 1 s estável, e a imagem é a da última amostra
   desse segundo: o estado mais completo, com o último caractere digitado. Uma tremulação de até 2% da
   mudança pendente não reinicia a espera. Em rolagem ou digitação contínua, sai um frame "movimento" a
   cada 4 s, com o intervalo dobrando até 30 s. Tooltip que aparece e some não vira frame.
7. **Revisita.** Tela igual a uma das 16 últimas, em até 10 min, reaproveita a imagem anterior. O teste só
   roda com ao menos 25% do quadro fora da máscara.
8. **Câmera.** Em filmagem, tudo se mexe: a extração cai para ~2 frames/min ("heartbeat") e avisa.

Cada frame guarda `start` (quando a mudança começou), `captured_at` (o instante da imagem) e `end` (o
início do próximo). Os intervalos são contíguos e cobrem o vídeo inteiro; a fala pertence ao frame cujo
intervalo ela cruza.

## Comparação e sensibilidade

| parâmetro | normal | low | high | o que é |
|---|---|---|---|---|
| `t_lo` / `t_hi` | 16 / 40 | 20 / 50 | 12 / 32 | limiares fraco e forte de diferença de brilho |
| `cursor_h_frac` / `cursor_w_frac` | 0,045 / 0,030 | 0,060 / 0,040 | 0,035 / 0,024 | tamanho máximo de uma mancha "cursor", em fração da altura do quadro |

Iguais nos três níveis: até 3 manchas de cursor (`max_cursor_blobs`), ou até 6 somando no máximo 2
cursores (`cursor_scatter_blobs` 6, `cursor_scatter_px` 2,0); mancha com menos de 3 px (`min_blob_px`), ou
abaixo de 0,5% do quadro (`weak_only_min_frac`) com menos de 2 px fortes ou menos de 25% de px fortes
(`min_strong_px`, `min_strong_ratio` 0,25), é ruído; grade de 8 px, com célula ativa a partir de 4 px
mudados (`cell_px`, `cell_active_px`).

**Limites conhecidos.** A tolerância a cursor esconde mudança de um dígito ou até ~4 caracteres; elas se
acumulam contra o último frame mantido e acabam aparecendo, e `high` reduz o efeito. A diferença em tons
de cinza não vê troca de cor com o mesmo brilho (vermelho ↔ verde); ícone que também muda de forma é
detectado.

## Regiões quentes, estabilização, revisita e câmera

| parâmetro | valor | o que é | na linha de comando |
|---|---|---|---|
| amostragem | 2 por segundo | 4 ajuda em demonstração com cliques rápidos (custa mais JPEG e Python por amostra, não mais decodificação) | `--sample-fps` |
| estabilização | 1,0 s | tempo parado antes de a mudança virar frame | `--settle` |
| espera máxima | 3,0 s | depois disso, em movimento contínuo, sai frame "movimento" | `--max-settle-wait` |
| frames "movimento" | 4 → 30 s | intervalo inicial e máximo, dobrando a cada um | `--motion-interval`, `--motion-max-interval` |
| máscara quente | janela ±30 s | `off` desliga; use quando um painel que se atualiza sozinho some da comparação | `--hot-mode windowed\|off` |
| liga a máscara | atividade local ≥ 50% · total ≥ 85% · recorrência ≥ 8/min | o que conta como "muda sozinho"; na recorrência, células sempre ativas (webcam) e contornos finos (anel de quem fala) não contam | — |
| mantém ligada | 30% · 60% · 4/min | histerese, zerada a cada troca de layout | — |
| cronômetro ("ticker") | liga com ≥ 30% dos pares, mantém com 15% | conteúdo novo do tamanho de um cursor, diferente de 2 amostras antes (relógio, cronômetro, spinner) | — |
| troca de layout | mudança ≥ 50% seguida, em até 2 s (4 pares), de um par com < 20% | corta a janela da máscara | — |
| tremulação na espera | até 2% da mudança pendente | não reinicia a estabilização | — |
| câmera | a cada ≥ 30 s, se mudou ≥ 10% | o "heartbeat" | — |
| revisita | 16 frames / 600 s, com ≥ 25% do quadro fora da máscara | `--no-revisit` desliga | `--no-revisit` |
| cursor | 0,045 da altura | tamanho da caixa de cursor | `--cursor-size` |

## Imagens de saída

| parâmetro | valor | o que é | na linha de comando |
|---|---|---|---|
| lado maior | 1568 px (até 2000) | 1568 é o que o modelo lê sem reduzir | `--max-edge` |
| qualidade JPEG | 3 (2 a 5; menor = melhor) | ~160 KB por frame em 1568×882; acima de 480 KB, a imagem é regravada | `--jpeg-q` |

**Tokens.** Uma imagem custa ⌈w/28⌉ × ⌈h/28⌉ tokens: 1.792 em 1568×882 e 2.072 em 1568×1018. Um lote de
20 imagens soma ≈ 36k tokens; a prancha, ≈ 1,5k; o texto, um token a cada ~3,5 caracteres.

## Lotes

| parâmetro | valor | o que é | na linha de comando |
|---|---|---|---|
| frames por lote | até 20 (corte preferido a partir de 12) | confiabilidade do analista: em janelas de 50 ele pulava de 17 a 38 frames | `--batch-frames` |
| fala por lote | 8.000 caracteres distintos | um frame sozinho acima disso vira lote `over_budget` | `--batch-text` |
| duração do lote | 900 s (15 min) | — | `--batch-span` |
| cortes suaves | ligados | `--no-soft-cuts` corta sempre no limite | `--no-soft-cuts` |

- **Onde cortar.** Entre o 12º frame e o limite, ganha o ponto com mais pontos: troca de cena (+3),
  nenhuma fala atravessando o corte (+2), silêncio de ±1 s (+1), frame anterior com 30 s ou mais (+1).
  Empate: o corte mais tardio.
- **Cauda.** Último lote com menos de 8 frames se funde ao anterior se o total ficar em até 25; senão os
  dois se dividem ao meio.
- **`batch_images`.** Revisita conta no teto de 20 frames, mas não é imagem a abrir quando o original
  está no mesmo lote. Por isso o Workflow confere `frames_seen` contra `batch_images`, não contra o
  número de frames.
- **Rótulos** do `batch_NNN.md` saem no idioma detectado na legenda (pt-BR ou inglês); o sufixo da linha
  de imagem de uma revisita é sempre ` (revisita de Fxxxx)`. Os relatórios saem no `idioma` do pedido.
  Formato completo em [`contratos.md`](contratos.md), seção 4.

## Avisos de densidade e a região sugerida

Os avisos saem em português, em `videos[].warnings` do JSON do `prepare.py`:

- **Densidade**, acima de 12 frames/min no vídeo ou de 30 num minuto qualquer:
  `densidade alta: 14.2 frames/min no vídeo (limite 12); região sugerida para ignorar: 0.80,0.75,0.20,0.25
  (explica 45% dos frames)`, ou, sem região, `…; nenhuma região recorrente para ignorar (se houver frames
  redundantes: sensibilidade low)`.
- **Sugestão de região:** o `prepare.py` agrupa as caixas de mudança dos frames mantidos que ocupam até
  25% do quadro. Se um grupo explica ao menos 30% dos frames e ao menos 10 frames, ele sugere ignorar a
  união dessas caixas mais uma célula, com 2 casas decimais. É o caso típico de webcam ou relógio que a
  máscara automática não pegou.
- **Câmera**, trecho de 10 s ou mais: `trecho com cara de câmera em …: tudo se mexe; a extração cai para ~2
  frames/min (heartbeat)`.
- **Máscara grande**, de 35% a 50% do quadro por 60 s ou mais: `máscara quente cobriu N% do quadro em …; se
  um painel da tela sumiu da comparação, use --hot-mode off`.
- **Custo**, mais de 600 frames: `N frames: custo alto de imagem (~Nk tokens só de imagens)`.
- **Legenda**, com o prefixo `legenda:`: falas que cobrem menos de 50% do vídeo, falas depois do fim
  (talvez precise de `--vtt-offset SEC`, na linha de comando), horários inválidos, legenda vazia.

## O que cada mudança refaz

| mudança | refaz | reaproveita |
|---|---|---|
| objetivo novo, `max_lotes`, `paralelo`, esforços, `idioma`, `papeis` | só os agentes | todo o cache |
| `trecho` | os lotes do trecho | frames e legenda do vídeo inteiro |
| `falantes`, legenda editada | legenda e lotes | frames |
| `sensibilidade`, `ignorar_regiao` | frames e lotes | legenda |
| vídeo movido, renomeado ou editado | tudo (outra chave de cache) | — |

## Diagnóstico (uso manual, pela pessoa)

- `scripts/explain.py --frames-json <frames.json> --frame F0042` monta um painel de "por que este frame foi
  mantido"; com `--top-ignored 12`, as maiores mudanças tratadas como cursor ou iguais (candidatas a falso
  negativo). O `frames.json` fica em `cache\<slug>-<fp8>\frames-<h8>\`.
- `scripts/calibrate.py <vídeo> [--start --end] --grid "k=v1,v2;k2=…"` varre parâmetros sem decodificar
  de novo e compara contagens; serve para recalibrar os padrões.
- `scripts/prepare.py --gc 30` apaga do cache os vídeos sem uso há mais de 30 dias e mostra o espaço
  liberado.
