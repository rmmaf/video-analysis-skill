export const meta = {
  name: 'videos-frame-a-frame',
  description: 'Analisa os 7 vídeos frame a frame contra a transcrição, uma janela por agente, com verificador adversarial',
  phases: [{ title: 'Janelas', detail: 'um agente por janela de ~50 frames' }, { title: 'Verificação', detail: 'um verificador por janela com itens' }],
}

const KB = 'C:/Users/digom/Documents/solar/databricks  schema'
const SCR = 'C:/Users/digom/AppData/Local/Temp/claude/C--Users-digom-Documents-solar-databricks--schema/cd2c1414-3ebd-4b7c-a607-fbbe8a10608f/scratchpad'
const SKILL = 'C:/Users/digom/.claude/skills/ingestao-adf/SKILL.md'

const VIDEOS = {
  da_1508: { titulo: "Repasse - Fluxo de Carga dos DA's (parte das 15:08)", data: '18/08/2026',
    tema: 'repasse do fluxo de carga dos arquivos dos DAs (distribuidores): do pedido no SFA ao arquivo no FTP/Blob, ao ADF e ao bronze. No kb/adf, pastas DADOS_EXTERNOS/DA e DADOS_EXTERNOS/CONTROL: PL_CONTROLLER_DA_DIARIO, PL_CONTROLLER_DA_FULL_DIARIO, PL_WORKER_DA_* (diário, PS, histórico blob e FTP), PL_WORKER_DA_PARTITION, LS_FORSRP347_DA, LS_FTP_GENERAL.' },
  da_1608: { titulo: "Repasse - Fluxo de Carga dos DA's (parte das 16:08)", data: '18/08/2026',
    tema: 'continuação do repasse do fluxo de carga dos DAs; começa falando de IP bloqueado no FTP e fluxo que roda OK sem dado novo.' },
  adf_overview: { titulo: 'Azure Data Factory - Overview', data: 'desconhecida (confira na tela)',
    tema: 'visão geral do Azure Data Factory da Solar: estrutura de pastas, pipelines controller/worker, datasets, linked services, integration runtimes, triggers, publish, ambientes.' },
  migracao_0_1_p1: { titulo: 'Repasse conhecimento migração de estrutura (0-1), parte 1 de 2', data: '14/10/2024',
    tema: 'migração da estrutura de ingestão da Arquitetura 0 para a Arquitetura 1: pastas do ADF (ARQUITETURA_0 × DATA_ENG), JSON de configuração, workers, Synapse/Databricks, storage.' },
  migracao_0_1_p2: { titulo: 'Repasse conhecimento migração de estrutura (0-1), parte 2 de 2', data: '15/10/2024',
    tema: 'continuação da migração da Arquitetura 0 para a 1.' },
  arq1_sharepoint: { titulo: 'Repasse conhecimento ARQ1 Sharepoint', data: '26/06/2025',
    tema: 'ingestão de arquivos do SharePoint na Arquitetura 1 (LS_HTTP_SHAREPOINT no kb/adf).' },
  arquitetura_1812: { titulo: 'Arquitetura de Dados Solar', data: '18/12/2025',
    tema: 'a engenharia explica a arquitetura de dados atual para a consultoria de governança (Nava/Purview): fontes, ADF, lake, Databricks, Fabric, e como coletar catálogo e linhagem.' },
}

const JANELAS = args.janelas   // [{slug, janela, inicio, fim, frames}]

const ITEM = {
  type: 'object',
  properties: {
    arquivo: { type: 'string', description: 'o vídeo: slug e título' },
    localizacao: { type: 'string', description: 'hh:mm:ss e frame (ex.: 00:32:20, f0600_003220.jpg)' },
    trecho_literal: { type: 'string', description: 'fala literal da transcrição ou texto lido na tela, até 400 caracteres' },
    enunciado: { type: 'string' },
    tipo: { type: 'string', enum: ['checklist', 'regra', 'decisao', 'procedimento', 'armadilha', 'fato', 'papel'] },
    forca_no_documento: { type: 'string', enum: ['critico', 'atencao', 'padrao', 'obrigatorio_declarado', 'recomendado', 'informativo', 'nao_declarada'] },
    vigencia: { type: 'string', enum: ['vigente', 'proposta', 'poc', 'historica', 'desconhecida'] },
    natureza: {
      type: 'object',
      properties: {
        destino: { type: 'array', items: { type: 'string' } }, origem: { type: 'array', items: { type: 'string' } },
        modo_carga: { type: 'array', items: { type: 'string' } }, sensibilidade: { type: 'string' },
        arquitetura: { type: 'string' }, etapa: { type: 'array', items: { type: 'string' } },
      },
      required: ['destino', 'origem', 'etapa'],
    },
    cobertura_kb: { type: 'object', properties: { ids: { type: 'array', items: { type: 'string' } }, grau: { type: 'string', enum: ['igual', 'parcial', 'nenhuma'] }, nota: { type: 'string' } }, required: ['ids', 'grau'] },
    cobertura_skill: { type: 'string', enum: ['ja_esta', 'parcial', 'ausente'] },
    conflito: { type: 'object', properties: { ids: { type: 'array', items: { type: 'string' } }, explicacao: { type: 'string' } }, required: ['ids', 'explicacao'] },
    relevancia_skill: { type: 'string', enum: ['direta', 'contexto', 'outra_skill'] },
    evidencia: { type: 'string', description: 'tela | fala | tela+fala, e o que cada uma mostra' },
    observacao: { type: 'string' },
  },
  required: ['arquivo', 'localizacao', 'trecho_literal', 'enunciado', 'tipo', 'vigencia', 'natureza', 'cobertura_kb', 'cobertura_skill', 'relevancia_skill', 'evidencia'],
}

const JAN = {
  type: 'object',
  properties: {
    frames_vistos: { type: 'integer' },
    linha_do_tempo: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          inicio: { type: 'string' }, fim: { type: 'string' }, frames: { type: 'array', items: { type: 'string' } },
          tela: { type: 'string', description: 'aplicativo e o que está visível: artefato, caminho, parâmetro, código, tabela' },
          fala: { type: 'string', description: 'o que foi dito, resumido, com o falante' },
          passo: { type: 'string', description: 'passo do fluxo de dados ou do procedimento' },
        },
        required: ['inicio', 'fim', 'frames', 'tela', 'fala'],
      },
    },
    artefatos_vistos: {
      type: 'array',
      items: { type: 'object', properties: { nome: { type: 'string' }, tipo: { type: 'string' }, instante: { type: 'string' }, frame: { type: 'string' } }, required: ['nome', 'tipo', 'instante'] },
    },
    itens: { type: 'array', items: ITEM },
    correcoes_transcricao: {
      type: 'array',
      items: { type: 'object', properties: { instante: { type: 'string' }, transcrito: { type: 'string' }, correto: { type: 'string' }, evidencia: { type: 'string' } }, required: ['instante', 'transcrito', 'correto'] },
    },
    falantes: {
      type: 'array',
      items: { type: 'object', properties: { rotulo: { type: 'string' }, identificacao: { type: 'string' }, evidencia: { type: 'string' } }, required: ['rotulo', 'identificacao'] },
    },
    alerta_sensivel: { type: 'string' },
    resumo: { type: 'string' },
  },
  required: ['frames_vistos', 'linha_do_tempo', 'artefatos_vistos', 'itens', 'resumo'],
}

const VERIF = {
  type: 'object',
  properties: {
    vereditos: {
      type: 'array',
      items: { type: 'object', properties: { idx: { type: 'integer' }, veredito: { type: 'string', enum: ['mantido', 'corrigido', 'descartado'] }, motivo: { type: 'string' }, correcoes: { type: 'object' } }, required: ['idx', 'veredito', 'motivo'] },
    },
    itens_novos: { type: 'array', items: ITEM },
    notas: { type: 'string' },
  },
  required: ['vereditos', 'itens_novos'],
}

const CONDUTA = `
## A skill e a natureza do dado
A skill 'ingestao-adf' (${SKILL}; leia-a antes) opera o repositório 'Ingestao' do Azure Data Factory da Solar. Ela será ampliada com checklists por NATUREZA DO DADO: destino (lakehouse landing -> bronze, ou carga para o SAP S/4HANA na migração), origem (SAP ECC via RFC, SAP particionado por JSON de configuração em config-prd com flag 'able', tabela Z legada, S/4HANA, arquivo externo de DA via FTP/Blob/FileServer, SharePoint, banco relacional, API/nuvem, Fabric), modo de carga (full, incremental, CDC, backfill), sensibilidade (LGPD), geração de arquitetura (0, 1, 02) e etapa (demanda, extração, landing, bronze, orquestração/trigger, validação, governança, PR/deploy, operação/monitoramento). "Relacionado" = afeta como se ingere, valida, documenta, governa, versiona, publica ou opera uma ingestão. dbt (prata/ouro), BI e deploy de Databricks são outras skills.

Base de regras (a wiki normalizada, fonte da verdade): ${KB}/kb/rules.jsonl (Grep por termos). Repositório do ADF medido: ${KB}/kb/adf/ (catalogo-pipelines.md, fontes.md, ingestao-sap.md, tabelas-declaradas.md, grafo-chamadas.md); JSON dos pipelines em ${KB}/Ingestao/pipeline/. Procure por nome; não leia inteiro.

## Conduta
- Só grave arquivos em ${SCR}/videos/<slug>/zoom/ (recortes). Não altere mais nada.
- Credencial, senha, token, chave, connection string, SAS, IP com usuário, telefone, e-mail ou CPF que apareça na tela ou na fala: NUNCA transcreva o valor. Registre em 'alerta_sensivel' que apareceu e em que instante.
- Pessoas: use o papel quando possível. Nome só para identificar falante ou atribuir uma decisão.
- O que é dito numa reunião é relato; vira regra só se for dito como regra ou decisão ("sempre", "tem que", "nunca", "o padrão é"). Marque 'vigencia' e 'forca_no_documento' de acordo. Mostrado na tela como configuração existente é 'fato' do que existia na data do vídeo.
- 'trecho_literal' é a fala literal (copiada da transcrição, já com a correção que a tela justificar, entre colchetes) ou o texto lido na tela.
- Se um item contraria a base, preencha 'conflito'. Cuidado com a eng-uarq-008: barra ler TABELA Z/Y nova no medalhão; condição ZTTC na KONV e coluna começando com Z não são tabela Z.
- Seja exaustivo: todo procedimento, parâmetro, convenção de pasta/nome, cuidado operacional, erro conhecido e verificação que apareça é item.`

const janelaPrompt = j => {
  const v = VIDEOS[j.slug]
  const kk = String(j.janela).padStart(2, '0')
  return `Você analisa, frame a frame, a janela ${j.janela} (${j.inicio} a ${j.fim}, ${j.frames} frames) do vídeo "${v.titulo}" (${v.data}). Tema do vídeo: ${v.tema}

Arquivo da janela: ${SCR}/videos/${j.slug}/janela_${kk}.json. Ele traz 'frames' (caminhos dos frames distintos, em ordem; o nome dá o instante: f0600_003220.jpg = 00:32:20) e 'transcricao' (as falas da .vtt da janela com 30 s de folga; cada fala termina com '(tela: <frame>)', o frame que estava na tela quando ela começou). A transcrição é automática, com falantes anônimos (SPEAKER_nn) e erros de reconhecimento.

## O que fazer
1. Leia o JSON. Abra com Read TODOS os frames da janela, na ordem, sem pular nenhum. São frames distintos amostrados a 2 fps: cada um tem alguma mudança na tela.
2. A tela compartilhada ocupa só parte do frame e o texto fica pequeno. Sempre que houver nome de pipeline, pasta, caminho, parâmetro, JSON, código, tabela ou tela de configuração que você não leia inteiro, recorte e amplie a região com Python (PIL: crop e resize 2x) e salve em ${SCR}/videos/${j.slug}/zoom/ (crie a pasta), depois abra o recorte com Read.
3. Alinhe fala e tela: resolva "aqui", "esse", "essa pasta", "esse pipeline" pelo frame em que a fala começou e pelos vizinhos.
4. Corrija a transcrição pelo que a tela mostra (nome de pipeline, sistema, tabela, pessoa) e registre em 'correcoes_transcricao'.
5. Identifique os falantes quando a tela (rótulo de quem fala, quem compartilha) ou a fala permitir.
6. Monte a 'linha_do_tempo' (trechos contíguos de mesmo assunto), liste os 'artefatos_vistos' (pipeline, dataset, linked service, container, pasta, arquivo, tabela, notebook, job, trigger, storage) e extraia os 'itens'.
${CONDUTA}

Devolva o objeto do schema. Em 'frames_vistos' informe quantos frames você abriu.`
}

const verifPrompt = (j, r) => {
  const v = VIDEOS[j.slug]
  const kk = String(j.janela).padStart(2, '0')
  return `Você é o verificador adversarial da janela ${j.janela} (${j.inicio} a ${j.fim}) do vídeo "${v.titulo}" (${v.data}). Um analista viu os frames e a transcrição e extraiu os itens abaixo. Derrube o que estiver errado e ache o que faltou. Não presuma que ele acertou.

Arquivo da janela: ${SCR}/videos/${j.slug}/janela_${kk}.json (frames e transcrição). Recortes que o analista fez: ${SCR}/videos/${j.slug}/zoom/.

Para cada item (idx): abra o(s) frame(s) da 'localizacao' e os vizinhos, e a fala correspondente no JSON. Confirme que a tela e a fala sustentam o item, que o trecho literal existe, que o enunciado não exagera (relato virou regra, proposta virou obrigação, valor mal lido), que natureza/etapa estão certas, que a cobertura em rules.jsonl confere (Grep) e que o conflito procede. Confira no kb/adf ou nos JSON do repositório se os artefatos citados existem com esse nome. Veredito para TODOS os idx: 'mantido', 'corrigido' (com 'correcoes' só dos campos que mudam) ou 'descartado' (com motivo).

Depois, completude: percorra a transcrição e os frames da janela atrás do que o analista não registrou e acrescente em 'itens_novos'.
${CONDUTA}

## Resumo do analista
${r.resumo}

## Linha do tempo do analista
${JSON.stringify(r.linha_do_tempo)}

## Itens do analista
${r.itens.map((it, i) => `idx ${i}: ${JSON.stringify(it)}`).join('\n')}`
}

const res = await pipeline(
  JANELAS,
  j => agent(janelaPrompt(j), { label: `${j.slug}#${j.janela}`, phase: 'Janelas', schema: JAN, effort: 'max' }),
  (r, j) => {
    if (!r) return { slug: j.slug, janela: j.janela, erro: 'sem resultado' }
    const base = { slug: j.slug, janela: j.janela, frames: j.frames, frames_vistos: r.frames_vistos, itens: r.itens.length }
    if (!r.itens.length) return base
    return agent(verifPrompt(j, r), { label: `verif:${j.slug}#${j.janela}`, phase: 'Verificação', schema: VERIF, effort: 'max' })
      .then(v => ({ ...base, verif: v ? { descartados: v.vereditos.filter(x => x.veredito === 'descartado').length, corrigidos: v.vereditos.filter(x => x.veredito === 'corrigido').length, novos: v.itens_novos.length } : 'sem resultado' }))
  },
)
const vistos = res.filter(Boolean).filter(r => r.frames_vistos !== undefined && r.frames_vistos < r.frames)
if (vistos.length) log(`janelas com menos frames vistos que o total: ${vistos.map(r => r.slug + '#' + r.janela + ' ' + r.frames_vistos + '/' + r.frames).join(', ')}`)
return res
