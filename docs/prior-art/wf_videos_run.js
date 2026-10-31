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

const JANELAS = [{"slug": "adf_overview", "janela": 1, "inicio": "00:00:00", "fim": "00:08:32", "frames": 50}, {"slug": "adf_overview", "janela": 2, "inicio": "00:08:32", "fim": "00:11:45", "frames": 50}, {"slug": "adf_overview", "janela": 3, "inicio": "00:11:45", "fim": "00:18:42", "frames": 50}, {"slug": "adf_overview", "janela": 4, "inicio": "00:18:42", "fim": "00:24:30", "frames": 50}, {"slug": "adf_overview", "janela": 5, "inicio": "00:24:30", "fim": "00:34:52", "frames": 48}, {"slug": "adf_overview", "janela": 6, "inicio": "00:34:52", "fim": "00:37:32", "frames": 50}, {"slug": "adf_overview", "janela": 7, "inicio": "00:37:32", "fim": "00:42:58", "frames": 50}, {"slug": "adf_overview", "janela": 8, "inicio": "00:42:58", "fim": "00:50:16", "frames": 50}, {"slug": "adf_overview", "janela": 9, "inicio": "00:50:16", "fim": "00:52:41", "frames": 50}, {"slug": "adf_overview", "janela": 10, "inicio": "00:52:41", "fim": "00:54:26", "frames": 50}, {"slug": "adf_overview", "janela": 11, "inicio": "00:54:26", "fim": "00:57:53", "frames": 50}, {"slug": "adf_overview", "janela": 12, "inicio": "00:57:53", "fim": "01:05:50", "frames": 50}, {"slug": "adf_overview", "janela": 13, "inicio": "01:05:50", "fim": "01:12:12", "frames": 50}, {"slug": "adf_overview", "janela": 14, "inicio": "01:12:12", "fim": "01:14:47", "frames": 50}, {"slug": "adf_overview", "janela": 15, "inicio": "01:14:47", "fim": "01:20:10", "frames": 50}, {"slug": "adf_overview", "janela": 16, "inicio": "01:20:10", "fim": "01:22:39", "frames": 50}, {"slug": "adf_overview", "janela": 17, "inicio": "01:22:39", "fim": "01:25:06", "frames": 50}, {"slug": "adf_overview", "janela": 18, "inicio": "01:25:06", "fim": "01:27:15", "frames": 50}, {"slug": "adf_overview", "janela": 19, "inicio": "01:27:15", "fim": "01:30:04", "frames": 50}, {"slug": "adf_overview", "janela": 20, "inicio": "01:30:04", "fim": "01:33:28", "frames": 50}, {"slug": "adf_overview", "janela": 21, "inicio": "01:33:28", "fim": "01:36:33", "frames": 50}, {"slug": "adf_overview", "janela": 22, "inicio": "01:36:33", "fim": "01:46:25", "frames": 50}, {"slug": "adf_overview", "janela": 23, "inicio": "01:46:25", "fim": "01:50:03", "frames": 40}, {"slug": "arq1_sharepoint", "janela": 1, "inicio": "00:00:00", "fim": "00:01:06", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 2, "inicio": "00:01:06", "fim": "00:02:20", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 3, "inicio": "00:02:20", "fim": "00:04:49", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 4, "inicio": "00:04:49", "fim": "00:06:00", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 5, "inicio": "00:06:00", "fim": "00:07:23", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 6, "inicio": "00:07:23", "fim": "00:08:45", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 7, "inicio": "00:08:45", "fim": "00:09:39", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 8, "inicio": "00:09:39", "fim": "00:10:53", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 9, "inicio": "00:10:53", "fim": "00:12:03", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 10, "inicio": "00:12:03", "fim": "00:12:52", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 11, "inicio": "00:12:52", "fim": "00:14:23", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 12, "inicio": "00:14:23", "fim": "00:15:41", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 13, "inicio": "00:15:41", "fim": "00:16:36", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 14, "inicio": "00:16:36", "fim": "00:17:56", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 15, "inicio": "00:17:56", "fim": "00:18:47", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 16, "inicio": "00:18:47", "fim": "00:19:42", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 17, "inicio": "00:19:42", "fim": "00:20:43", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 18, "inicio": "00:20:43", "fim": "00:22:06", "frames": 50}, {"slug": "arquitetura_1812", "janela": 1, "inicio": "00:00:00", "fim": "00:09:22", "frames": 50}, {"slug": "arquitetura_1812", "janela": 2, "inicio": "00:09:22", "fim": "00:11:12", "frames": 50}, {"slug": "arquitetura_1812", "janela": 3, "inicio": "00:11:12", "fim": "00:12:33", "frames": 50}, {"slug": "arquitetura_1812", "janela": 4, "inicio": "00:12:33", "fim": "00:14:19", "frames": 50}, {"slug": "arquitetura_1812", "janela": 5, "inicio": "00:14:19", "fim": "00:16:20", "frames": 50}, {"slug": "arquitetura_1812", "janela": 6, "inicio": "00:16:20", "fim": "00:17:15", "frames": 50}, {"slug": "arquitetura_1812", "janela": 7, "inicio": "00:17:15", "fim": "00:17:55", "frames": 50}, {"slug": "arquitetura_1812", "janela": 8, "inicio": "00:17:55", "fim": "00:19:44", "frames": 50}, {"slug": "arquitetura_1812", "janela": 9, "inicio": "00:19:44", "fim": "00:23:17", "frames": 50}, {"slug": "arquitetura_1812", "janela": 10, "inicio": "00:23:17", "fim": "00:25:37", "frames": 50}, {"slug": "arquitetura_1812", "janela": 11, "inicio": "00:25:37", "fim": "00:27:09", "frames": 50}, {"slug": "arquitetura_1812", "janela": 12, "inicio": "00:27:09", "fim": "00:27:57", "frames": 50}, {"slug": "arquitetura_1812", "janela": 13, "inicio": "00:27:57", "fim": "00:38:47", "frames": 33}, {"slug": "arquitetura_1812", "janela": 14, "inicio": "00:38:47", "fim": "00:39:34", "frames": 50}, {"slug": "arquitetura_1812", "janela": 15, "inicio": "00:39:34", "fim": "00:46:12", "frames": 58}, {"slug": "da_1508", "janela": 1, "inicio": "00:00:00", "fim": "00:03:48", "frames": 50}, {"slug": "da_1508", "janela": 2, "inicio": "00:03:48", "fim": "00:07:24", "frames": 50}, {"slug": "da_1508", "janela": 3, "inicio": "00:07:24", "fim": "00:10:25", "frames": 50}, {"slug": "da_1508", "janela": 4, "inicio": "00:10:25", "fim": "00:12:25", "frames": 50}, {"slug": "da_1508", "janela": 5, "inicio": "00:12:25", "fim": "00:15:04", "frames": 50}, {"slug": "da_1508", "janela": 6, "inicio": "00:15:04", "fim": "00:16:10", "frames": 50}, {"slug": "da_1508", "janela": 7, "inicio": "00:16:10", "fim": "00:17:16", "frames": 50}, {"slug": "da_1508", "janela": 8, "inicio": "00:17:16", "fim": "00:18:51", "frames": 50}, {"slug": "da_1508", "janela": 9, "inicio": "00:18:51", "fim": "00:21:13", "frames": 50}, {"slug": "da_1508", "janela": 10, "inicio": "00:21:13", "fim": "00:22:24", "frames": 50}, {"slug": "da_1508", "janela": 11, "inicio": "00:22:24", "fim": "00:27:13", "frames": 50}, {"slug": "da_1508", "janela": 12, "inicio": "00:27:13", "fim": "00:33:56", "frames": 50}, {"slug": "da_1508", "janela": 13, "inicio": "00:33:56", "fim": "00:35:27", "frames": 50}, {"slug": "da_1508", "janela": 14, "inicio": "00:35:27", "fim": "00:36:53", "frames": 50}, {"slug": "da_1508", "janela": 15, "inicio": "00:36:53", "fim": "00:38:20", "frames": 50}, {"slug": "da_1508", "janela": 16, "inicio": "00:38:20", "fim": "00:39:56", "frames": 50}, {"slug": "da_1508", "janela": 17, "inicio": "00:39:56", "fim": "00:41:18", "frames": 50}, {"slug": "da_1508", "janela": 18, "inicio": "00:41:18", "fim": "00:47:03", "frames": 50}, {"slug": "da_1508", "janela": 19, "inicio": "00:47:03", "fim": "00:49:46", "frames": 50}, {"slug": "da_1508", "janela": 20, "inicio": "00:49:46", "fim": "00:54:33", "frames": 51}, {"slug": "da_1608", "janela": 1, "inicio": "00:00:00", "fim": "00:04:15", "frames": 50}, {"slug": "da_1608", "janela": 2, "inicio": "00:04:15", "fim": "00:07:25", "frames": 50}, {"slug": "da_1608", "janela": 3, "inicio": "00:07:25", "fim": "00:09:04", "frames": 28}, {"slug": "migracao_0_1_p1", "janela": 1, "inicio": "00:00:00", "fim": "00:01:56", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 2, "inicio": "00:01:56", "fim": "00:03:54", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 3, "inicio": "00:03:54", "fim": "00:05:11", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 4, "inicio": "00:05:11", "fim": "00:06:10", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 5, "inicio": "00:06:10", "fim": "00:10:09", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 6, "inicio": "00:10:09", "fim": "00:12:37", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 7, "inicio": "00:12:37", "fim": "00:15:08", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 8, "inicio": "00:15:08", "fim": "00:17:28", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 9, "inicio": "00:17:28", "fim": "00:22:03", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 10, "inicio": "00:22:03", "fim": "00:25:17", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 11, "inicio": "00:25:17", "fim": "00:27:21", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 12, "inicio": "00:27:21", "fim": "00:32:20", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 13, "inicio": "00:32:20", "fim": "00:34:34", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 14, "inicio": "00:34:34", "fim": "00:37:33", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 15, "inicio": "00:37:33", "fim": "00:41:43", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 16, "inicio": "00:41:43", "fim": "00:44:32", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 17, "inicio": "00:44:32", "fim": "00:47:11", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 18, "inicio": "00:47:11", "fim": "00:51:07", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 19, "inicio": "00:51:07", "fim": "00:52:47", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 20, "inicio": "00:52:47", "fim": "00:54:43", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 21, "inicio": "00:54:43", "fim": "00:56:25", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 22, "inicio": "00:56:25", "fim": "00:59:20", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 23, "inicio": "00:59:20", "fim": "01:00:29", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 24, "inicio": "01:00:29", "fim": "01:02:55", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 25, "inicio": "01:02:55", "fim": "01:04:56", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 26, "inicio": "01:04:56", "fim": "01:08:55", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 27, "inicio": "01:08:55", "fim": "01:11:25", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 28, "inicio": "01:11:25", "fim": "01:13:02", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 29, "inicio": "01:13:02", "fim": "01:16:42", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 30, "inicio": "01:16:42", "fim": "01:18:43", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 31, "inicio": "01:18:43", "fim": "01:19:52", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 32, "inicio": "01:19:52", "fim": "01:21:55", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 33, "inicio": "01:21:55", "fim": "01:23:39", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 34, "inicio": "01:23:39", "fim": "01:24:48", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 35, "inicio": "01:24:48", "fim": "01:28:25", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 36, "inicio": "01:28:25", "fim": "01:30:38", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 37, "inicio": "01:30:38", "fim": "01:32:14", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 38, "inicio": "01:32:14", "fim": "01:33:51", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 39, "inicio": "01:33:51", "fim": "01:36:36", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 40, "inicio": "01:36:36", "fim": "01:38:40", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 41, "inicio": "01:38:40", "fim": "01:40:35", "frames": 56}, {"slug": "migracao_0_1_p2", "janela": 1, "inicio": "00:00:00", "fim": "00:02:12", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 2, "inicio": "00:02:12", "fim": "00:08:18", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 3, "inicio": "00:08:18", "fim": "00:09:33", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 4, "inicio": "00:09:33", "fim": "00:10:52", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 5, "inicio": "00:10:52", "fim": "00:12:07", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 6, "inicio": "00:12:07", "fim": "00:13:36", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 7, "inicio": "00:13:36", "fim": "00:15:53", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 8, "inicio": "00:15:53", "fim": "00:19:14", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 9, "inicio": "00:19:14", "fim": "00:20:33", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 10, "inicio": "00:20:33", "fim": "00:22:53", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 11, "inicio": "00:22:53", "fim": "00:24:11", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 12, "inicio": "00:24:11", "fim": "00:26:08", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 13, "inicio": "00:26:08", "fim": "00:28:12", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 14, "inicio": "00:28:12", "fim": "00:29:45", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 15, "inicio": "00:29:45", "fim": "00:35:29", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 16, "inicio": "00:35:29", "fim": "00:37:00", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 17, "inicio": "00:37:00", "fim": "00:38:08", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 18, "inicio": "00:38:08", "fim": "00:41:54", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 19, "inicio": "00:41:54", "fim": "00:45:25", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 20, "inicio": "00:45:25", "fim": "00:49:54", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 21, "inicio": "00:49:54", "fim": "00:53:43", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 22, "inicio": "00:53:43", "fim": "01:01:42", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 23, "inicio": "01:01:42", "fim": "01:03:10", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 24, "inicio": "01:03:10", "fim": "01:03:59", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 25, "inicio": "01:03:59", "fim": "01:05:25", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 26, "inicio": "01:05:25", "fim": "01:08:49", "frames": 40}]

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

// Janelas concluídas no Opus antes da parada: mesmo prompt e mesmas opções, para o resume usar o cache.
const CONCLUIDAS = new Set(Array.from({ length: 11 }, (_, i) => `adf_overview#${i + 1}`))
// O resto roda num agente mais leve, para não estourar a cota de tokens.
const LEVE = { model: 'sonnet', effort: 'medium' }
const ECONOMIA = `

## Economia de tokens (obrigatório)
- Abra os frames em BLOCOS: na mesma resposta, faça de 8 a 10 chamadas Read em paralelo (frames consecutivos), analise o bloco e só então abra o próximo. Nunca abra um frame por resposta.
- Não reabra frame já visto. Recorte (zoom) só quando o texto for relevante para a ingestão e estiver ilegível, e recorte só a região que interessa.
- Frames da grade de participantes sem tela compartilhada: basta registrar que não há conteúdo.`

const res = await pipeline(
  JANELAS,
  j => CONCLUIDAS.has(`${j.slug}#${j.janela}`)
    ? agent(janelaPrompt(j), { label: `${j.slug}#${j.janela}`, phase: 'Janelas', schema: JAN, effort: 'max' })
    : agent(janelaPrompt(j) + ECONOMIA, { label: `${j.slug}#${j.janela}`, phase: 'Janelas', schema: JAN, ...LEVE }),
  (r, j) => {
    if (!r) return { slug: j.slug, janela: j.janela, erro: 'sem resultado' }
    const base = { slug: j.slug, janela: j.janela, frames: j.frames, frames_vistos: r.frames_vistos, itens: r.itens.length }
    if (!r.itens.length) return base
    return agent(verifPrompt(j, r) + ECONOMIA, { label: `verif:${j.slug}#${j.janela}`, phase: 'Verificação', schema: VERIF, ...LEVE })
      .then(v => ({ ...base, verif: v ? { descartados: v.vereditos.filter(x => x.veredito === 'descartado').length, corrigidos: v.vereditos.filter(x => x.veredito === 'corrigido').length, novos: v.itens_novos.length } : 'sem resultado' }))
  },
)
const vistos = res.filter(Boolean).filter(r => r.frames_vistos !== undefined && r.frames_vistos < r.frames)
if (vistos.length) log(`janelas com menos frames vistos que o total: ${vistos.map(r => r.slug + '#' + r.janela + ' ' + r.frames_vistos + '/' + r.frames).join(', ')}`)
return res
