export const meta = {
  name: 'videos-frame-a-frame',
  description: 'Analyses the 7 videos frame by frame against the transcript, one window per agent, with an adversarial verifier',
  phases: [{ title: 'Windows', detail: 'one agent per window of ~50 frames' }, { title: 'Verification', detail: 'one verifier per window with items' }],
}

const KB = 'C:/Users/digom/Documents/solar/databricks  schema'
const SCR = 'C:/Users/digom/AppData/Local/Temp/claude/C--Users-digom-Documents-solar-databricks--schema/cd2c1414-3ebd-4b7c-a607-fbbe8a10608f/scratchpad'
const SKILL = 'C:/Users/digom/.claude/skills/ingestao-adf/SKILL.md'

const VIDEOS = {
  da_1508: { titulo: "Repasse - Fluxo de Carga dos DA's (parte das 15:08)", data: '18/08/2026',
    tema: 'handover of the load flow of the files from the DAs (distributors): from the order in SFA to the file on FTP/Blob, to ADF and to bronze. In kb/adf, folders DADOS_EXTERNOS/DA and DADOS_EXTERNOS/CONTROL: PL_CONTROLLER_DA_DIARIO, PL_CONTROLLER_DA_FULL_DIARIO, PL_WORKER_DA_* (daily, PS, history blob and FTP), PL_WORKER_DA_PARTITION, LS_FORSRP347_DA, LS_FTP_GENERAL.' },
  da_1608: { titulo: "Repasse - Fluxo de Carga dos DA's (parte das 16:08)", data: '18/08/2026',
    tema: 'continuation of the handover of the DA load flow; it starts by talking about a blocked IP on the FTP and a flow that runs OK with no new data.' },
  adf_overview: { titulo: 'Azure Data Factory - Overview', data: 'unknown (check on the screen)',
    tema: 'overview of the Solar Azure Data Factory: folder structure, controller/worker pipelines, datasets, linked services, integration runtimes, triggers, publish, environments.' },
  migracao_0_1_p1: { titulo: 'Repasse conhecimento migração de estrutura (0-1), parte 1 de 2', data: '14/10/2024',
    tema: 'migration of the ingestion structure from Architecture 0 to Architecture 1: ADF folders (ARQUITETURA_0 × DATA_ENG), configuration JSON, workers, Synapse/Databricks, storage.' },
  migracao_0_1_p2: { titulo: 'Repasse conhecimento migração de estrutura (0-1), parte 2 de 2', data: '15/10/2024',
    tema: 'continuation of the migration from Architecture 0 to 1.' },
  arq1_sharepoint: { titulo: 'Repasse conhecimento ARQ1 Sharepoint', data: '26/06/2025',
    tema: 'ingestion of SharePoint files in Architecture 1 (LS_HTTP_SHAREPOINT in kb/adf).' },
  arquitetura_1812: { titulo: 'Arquitetura de Dados Solar', data: '18/12/2025',
    tema: 'the engineering team explains the current data architecture to the governance consultancy (Nava/Purview): sources, ADF, lake, Databricks, Fabric, and how to collect catalog and lineage.' },
}

const JANELAS = [{"slug": "adf_overview", "janela": 1, "inicio": "00:00:00", "fim": "00:08:32", "frames": 50}, {"slug": "adf_overview", "janela": 2, "inicio": "00:08:32", "fim": "00:11:45", "frames": 50}, {"slug": "adf_overview", "janela": 3, "inicio": "00:11:45", "fim": "00:18:42", "frames": 50}, {"slug": "adf_overview", "janela": 4, "inicio": "00:18:42", "fim": "00:24:30", "frames": 50}, {"slug": "adf_overview", "janela": 5, "inicio": "00:24:30", "fim": "00:34:52", "frames": 48}, {"slug": "adf_overview", "janela": 6, "inicio": "00:34:52", "fim": "00:37:32", "frames": 50}, {"slug": "adf_overview", "janela": 7, "inicio": "00:37:32", "fim": "00:42:58", "frames": 50}, {"slug": "adf_overview", "janela": 8, "inicio": "00:42:58", "fim": "00:50:16", "frames": 50}, {"slug": "adf_overview", "janela": 9, "inicio": "00:50:16", "fim": "00:52:41", "frames": 50}, {"slug": "adf_overview", "janela": 10, "inicio": "00:52:41", "fim": "00:54:26", "frames": 50}, {"slug": "adf_overview", "janela": 11, "inicio": "00:54:26", "fim": "00:57:53", "frames": 50}, {"slug": "adf_overview", "janela": 12, "inicio": "00:57:53", "fim": "01:05:50", "frames": 50}, {"slug": "adf_overview", "janela": 13, "inicio": "01:05:50", "fim": "01:12:12", "frames": 50}, {"slug": "adf_overview", "janela": 14, "inicio": "01:12:12", "fim": "01:14:47", "frames": 50}, {"slug": "adf_overview", "janela": 15, "inicio": "01:14:47", "fim": "01:20:10", "frames": 50}, {"slug": "adf_overview", "janela": 16, "inicio": "01:20:10", "fim": "01:22:39", "frames": 50}, {"slug": "adf_overview", "janela": 17, "inicio": "01:22:39", "fim": "01:25:06", "frames": 50}, {"slug": "adf_overview", "janela": 18, "inicio": "01:25:06", "fim": "01:27:15", "frames": 50}, {"slug": "adf_overview", "janela": 19, "inicio": "01:27:15", "fim": "01:30:04", "frames": 50}, {"slug": "adf_overview", "janela": 20, "inicio": "01:30:04", "fim": "01:33:28", "frames": 50}, {"slug": "adf_overview", "janela": 21, "inicio": "01:33:28", "fim": "01:36:33", "frames": 50}, {"slug": "adf_overview", "janela": 22, "inicio": "01:36:33", "fim": "01:46:25", "frames": 50}, {"slug": "adf_overview", "janela": 23, "inicio": "01:46:25", "fim": "01:50:03", "frames": 40}, {"slug": "arq1_sharepoint", "janela": 1, "inicio": "00:00:00", "fim": "00:01:06", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 2, "inicio": "00:01:06", "fim": "00:02:20", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 3, "inicio": "00:02:20", "fim": "00:04:49", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 4, "inicio": "00:04:49", "fim": "00:06:00", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 5, "inicio": "00:06:00", "fim": "00:07:23", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 6, "inicio": "00:07:23", "fim": "00:08:45", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 7, "inicio": "00:08:45", "fim": "00:09:39", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 8, "inicio": "00:09:39", "fim": "00:10:53", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 9, "inicio": "00:10:53", "fim": "00:12:03", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 10, "inicio": "00:12:03", "fim": "00:12:52", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 11, "inicio": "00:12:52", "fim": "00:14:23", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 12, "inicio": "00:14:23", "fim": "00:15:41", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 13, "inicio": "00:15:41", "fim": "00:16:36", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 14, "inicio": "00:16:36", "fim": "00:17:56", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 15, "inicio": "00:17:56", "fim": "00:18:47", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 16, "inicio": "00:18:47", "fim": "00:19:42", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 17, "inicio": "00:19:42", "fim": "00:20:43", "frames": 50}, {"slug": "arq1_sharepoint", "janela": 18, "inicio": "00:20:43", "fim": "00:22:06", "frames": 50}, {"slug": "arquitetura_1812", "janela": 1, "inicio": "00:00:00", "fim": "00:09:22", "frames": 50}, {"slug": "arquitetura_1812", "janela": 2, "inicio": "00:09:22", "fim": "00:11:12", "frames": 50}, {"slug": "arquitetura_1812", "janela": 3, "inicio": "00:11:12", "fim": "00:12:33", "frames": 50}, {"slug": "arquitetura_1812", "janela": 4, "inicio": "00:12:33", "fim": "00:14:19", "frames": 50}, {"slug": "arquitetura_1812", "janela": 5, "inicio": "00:14:19", "fim": "00:16:20", "frames": 50}, {"slug": "arquitetura_1812", "janela": 6, "inicio": "00:16:20", "fim": "00:17:15", "frames": 50}, {"slug": "arquitetura_1812", "janela": 7, "inicio": "00:17:15", "fim": "00:17:55", "frames": 50}, {"slug": "arquitetura_1812", "janela": 8, "inicio": "00:17:55", "fim": "00:19:44", "frames": 50}, {"slug": "arquitetura_1812", "janela": 9, "inicio": "00:19:44", "fim": "00:23:17", "frames": 50}, {"slug": "arquitetura_1812", "janela": 10, "inicio": "00:23:17", "fim": "00:25:37", "frames": 50}, {"slug": "arquitetura_1812", "janela": 11, "inicio": "00:25:37", "fim": "00:27:09", "frames": 50}, {"slug": "arquitetura_1812", "janela": 12, "inicio": "00:27:09", "fim": "00:27:57", "frames": 50}, {"slug": "arquitetura_1812", "janela": 13, "inicio": "00:27:57", "fim": "00:38:47", "frames": 33}, {"slug": "arquitetura_1812", "janela": 14, "inicio": "00:38:47", "fim": "00:39:34", "frames": 50}, {"slug": "arquitetura_1812", "janela": 15, "inicio": "00:39:34", "fim": "00:46:12", "frames": 58}, {"slug": "da_1508", "janela": 1, "inicio": "00:00:00", "fim": "00:03:48", "frames": 50}, {"slug": "da_1508", "janela": 2, "inicio": "00:03:48", "fim": "00:07:24", "frames": 50}, {"slug": "da_1508", "janela": 3, "inicio": "00:07:24", "fim": "00:10:25", "frames": 50}, {"slug": "da_1508", "janela": 4, "inicio": "00:10:25", "fim": "00:12:25", "frames": 50}, {"slug": "da_1508", "janela": 5, "inicio": "00:12:25", "fim": "00:15:04", "frames": 50}, {"slug": "da_1508", "janela": 6, "inicio": "00:15:04", "fim": "00:16:10", "frames": 50}, {"slug": "da_1508", "janela": 7, "inicio": "00:16:10", "fim": "00:17:16", "frames": 50}, {"slug": "da_1508", "janela": 8, "inicio": "00:17:16", "fim": "00:18:51", "frames": 50}, {"slug": "da_1508", "janela": 9, "inicio": "00:18:51", "fim": "00:21:13", "frames": 50}, {"slug": "da_1508", "janela": 10, "inicio": "00:21:13", "fim": "00:22:24", "frames": 50}, {"slug": "da_1508", "janela": 11, "inicio": "00:22:24", "fim": "00:27:13", "frames": 50}, {"slug": "da_1508", "janela": 12, "inicio": "00:27:13", "fim": "00:33:56", "frames": 50}, {"slug": "da_1508", "janela": 13, "inicio": "00:33:56", "fim": "00:35:27", "frames": 50}, {"slug": "da_1508", "janela": 14, "inicio": "00:35:27", "fim": "00:36:53", "frames": 50}, {"slug": "da_1508", "janela": 15, "inicio": "00:36:53", "fim": "00:38:20", "frames": 50}, {"slug": "da_1508", "janela": 16, "inicio": "00:38:20", "fim": "00:39:56", "frames": 50}, {"slug": "da_1508", "janela": 17, "inicio": "00:39:56", "fim": "00:41:18", "frames": 50}, {"slug": "da_1508", "janela": 18, "inicio": "00:41:18", "fim": "00:47:03", "frames": 50}, {"slug": "da_1508", "janela": 19, "inicio": "00:47:03", "fim": "00:49:46", "frames": 50}, {"slug": "da_1508", "janela": 20, "inicio": "00:49:46", "fim": "00:54:33", "frames": 51}, {"slug": "da_1608", "janela": 1, "inicio": "00:00:00", "fim": "00:04:15", "frames": 50}, {"slug": "da_1608", "janela": 2, "inicio": "00:04:15", "fim": "00:07:25", "frames": 50}, {"slug": "da_1608", "janela": 3, "inicio": "00:07:25", "fim": "00:09:04", "frames": 28}, {"slug": "migracao_0_1_p1", "janela": 1, "inicio": "00:00:00", "fim": "00:01:56", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 2, "inicio": "00:01:56", "fim": "00:03:54", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 3, "inicio": "00:03:54", "fim": "00:05:11", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 4, "inicio": "00:05:11", "fim": "00:06:10", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 5, "inicio": "00:06:10", "fim": "00:10:09", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 6, "inicio": "00:10:09", "fim": "00:12:37", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 7, "inicio": "00:12:37", "fim": "00:15:08", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 8, "inicio": "00:15:08", "fim": "00:17:28", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 9, "inicio": "00:17:28", "fim": "00:22:03", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 10, "inicio": "00:22:03", "fim": "00:25:17", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 11, "inicio": "00:25:17", "fim": "00:27:21", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 12, "inicio": "00:27:21", "fim": "00:32:20", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 13, "inicio": "00:32:20", "fim": "00:34:34", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 14, "inicio": "00:34:34", "fim": "00:37:33", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 15, "inicio": "00:37:33", "fim": "00:41:43", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 16, "inicio": "00:41:43", "fim": "00:44:32", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 17, "inicio": "00:44:32", "fim": "00:47:11", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 18, "inicio": "00:47:11", "fim": "00:51:07", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 19, "inicio": "00:51:07", "fim": "00:52:47", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 20, "inicio": "00:52:47", "fim": "00:54:43", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 21, "inicio": "00:54:43", "fim": "00:56:25", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 22, "inicio": "00:56:25", "fim": "00:59:20", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 23, "inicio": "00:59:20", "fim": "01:00:29", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 24, "inicio": "01:00:29", "fim": "01:02:55", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 25, "inicio": "01:02:55", "fim": "01:04:56", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 26, "inicio": "01:04:56", "fim": "01:08:55", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 27, "inicio": "01:08:55", "fim": "01:11:25", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 28, "inicio": "01:11:25", "fim": "01:13:02", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 29, "inicio": "01:13:02", "fim": "01:16:42", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 30, "inicio": "01:16:42", "fim": "01:18:43", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 31, "inicio": "01:18:43", "fim": "01:19:52", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 32, "inicio": "01:19:52", "fim": "01:21:55", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 33, "inicio": "01:21:55", "fim": "01:23:39", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 34, "inicio": "01:23:39", "fim": "01:24:48", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 35, "inicio": "01:24:48", "fim": "01:28:25", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 36, "inicio": "01:28:25", "fim": "01:30:38", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 37, "inicio": "01:30:38", "fim": "01:32:14", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 38, "inicio": "01:32:14", "fim": "01:33:51", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 39, "inicio": "01:33:51", "fim": "01:36:36", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 40, "inicio": "01:36:36", "fim": "01:38:40", "frames": 50}, {"slug": "migracao_0_1_p1", "janela": 41, "inicio": "01:38:40", "fim": "01:40:35", "frames": 56}, {"slug": "migracao_0_1_p2", "janela": 1, "inicio": "00:00:00", "fim": "00:02:12", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 2, "inicio": "00:02:12", "fim": "00:08:18", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 3, "inicio": "00:08:18", "fim": "00:09:33", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 4, "inicio": "00:09:33", "fim": "00:10:52", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 5, "inicio": "00:10:52", "fim": "00:12:07", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 6, "inicio": "00:12:07", "fim": "00:13:36", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 7, "inicio": "00:13:36", "fim": "00:15:53", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 8, "inicio": "00:15:53", "fim": "00:19:14", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 9, "inicio": "00:19:14", "fim": "00:20:33", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 10, "inicio": "00:20:33", "fim": "00:22:53", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 11, "inicio": "00:22:53", "fim": "00:24:11", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 12, "inicio": "00:24:11", "fim": "00:26:08", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 13, "inicio": "00:26:08", "fim": "00:28:12", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 14, "inicio": "00:28:12", "fim": "00:29:45", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 15, "inicio": "00:29:45", "fim": "00:35:29", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 16, "inicio": "00:35:29", "fim": "00:37:00", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 17, "inicio": "00:37:00", "fim": "00:38:08", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 18, "inicio": "00:38:08", "fim": "00:41:54", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 19, "inicio": "00:41:54", "fim": "00:45:25", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 20, "inicio": "00:45:25", "fim": "00:49:54", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 21, "inicio": "00:49:54", "fim": "00:53:43", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 22, "inicio": "00:53:43", "fim": "01:01:42", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 23, "inicio": "01:01:42", "fim": "01:03:10", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 24, "inicio": "01:03:10", "fim": "01:03:59", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 25, "inicio": "01:03:59", "fim": "01:05:25", "frames": 50}, {"slug": "migracao_0_1_p2", "janela": 26, "inicio": "01:05:25", "fim": "01:08:49", "frames": 40}]

const ITEM = {
  type: 'object',
  properties: {
    arquivo: { type: 'string', description: 'the video: slug and title' },
    localizacao: { type: 'string', description: 'hh:mm:ss and frame (e.g. 00:32:20, f0600_003220.jpg)' },
    trecho_literal: { type: 'string', description: 'verbatim speech from the transcript or text read on the screen, up to 400 characters' },
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
    evidencia: { type: 'string', description: 'tela | fala | tela+fala, and what each one shows' },
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
          tela: { type: 'string', description: 'application and what is visible: artifact, path, parameter, code, table' },
          fala: { type: 'string', description: 'what was said, summarized, with the speaker' },
          passo: { type: 'string', description: 'step of the data flow or of the procedure' },
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
## The skill and the nature of the data
The skill 'ingestao-adf' (${SKILL}; read it first) operates the 'Ingestao' repository of the Solar Azure Data Factory. It will be extended with checklists by NATURE OF THE DATA: destination (lakehouse landing -> bronze, or load into SAP S/4HANA in the migration), source (SAP ECC via RFC, SAP partitioned by configuration JSON in config-prd with flag 'able', legacy Z table, S/4HANA, external DA file via FTP/Blob/FileServer, SharePoint, relational database, API/cloud, Fabric), load mode (full, incremental, CDC, backfill), sensitivity (LGPD), architecture generation (0, 1, 02) and stage (demand, extraction, landing, bronze, orchestration/trigger, validation, governance, PR/deploy, operation/monitoring). "Related" = affects how one ingests, validates, documents, governs, versions, publishes or operates an ingestion. dbt (prata/ouro), BI and Databricks deploy are other skills.

Rule base (the normalized wiki, the source of truth): ${KB}/kb/rules.jsonl (Grep for terms). Measured ADF repository: ${KB}/kb/adf/ (catalogo-pipelines.md, fontes.md, ingestao-sap.md, tabelas-declaradas.md, grafo-chamadas.md); JSON of the pipelines in ${KB}/Ingestao/pipeline/. Search by name; do not read them whole.

## Conduct
- Only write files in ${SCR}/videos/<slug>/zoom/ (crops). Do not change anything else.
- Credential, password, token, key, connection string, SAS, IP with user, phone number, e-mail or CPF that appears on the screen or in the speech: NEVER transcribe the value. Record in 'alerta_sensivel' that it appeared and at what time.
- People: use the role when possible. A name only to identify a speaker or to attribute a decision.
- What is said in a meeting is an account; it becomes a rule only if it is said as a rule or a decision ("sempre", "tem que", "nunca", "o padrão é"). Set 'vigencia' and 'forca_no_documento' accordingly. What is shown on the screen as an existing configuration is a 'fato' about what existed on the date of the video.
- 'trecho_literal' is the verbatim speech (copied from the transcript, already with the correction that the screen justifies, in square brackets) or the text read on the screen.
- If an item contradicts the base, fill in 'conflito'. Careful with eng-uarq-008: it bars reading a new Z/Y TABLE in the medallion; a ZTTC condition in KONV and a column starting with Z are not a Z table.
- Be exhaustive: every procedure, parameter, folder/name convention, operational precaution, known error and check that appears is an item.`

const janelaPrompt = j => {
  const v = VIDEOS[j.slug]
  const kk = String(j.janela).padStart(2, '0')
  return `You analyse, frame by frame, window ${j.janela} (${j.inicio} to ${j.fim}, ${j.frames} frames) of the video "${v.titulo}" (${v.data}). Video topic: ${v.tema}

Window file: ${SCR}/videos/${j.slug}/janela_${kk}.json. It holds 'frames' (paths of the distinct frames, in order; the name gives the time: f0600_003220.jpg = 00:32:20) and 'transcricao' (the cues of the .vtt in the window, with 30 s of margin; each cue ends with '(tela: <frame>)', the frame that was on the screen when it started). The transcript is automatic, with anonymous speakers (SPEAKER_nn) and recognition errors.

## What to do
1. Read the JSON. Open with Read ALL the frames of the window, in order, without skipping any. They are distinct frames sampled at 2 fps: each one has some change on the screen.
2. The shared screen takes up only part of the frame and the text is small. Whenever there is a pipeline name, folder, path, parameter, JSON, code, table or configuration screen that you cannot read in full, crop and enlarge the region with Python (PIL: crop and resize 2x) and save it in ${SCR}/videos/${j.slug}/zoom/ (create the folder), then open the crop with Read.
3. Align speech and screen: resolve "aqui", "esse", "essa pasta", "esse pipeline" by the frame in which the cue started and by its neighbors.
4. Correct the transcript by what the screen shows (name of a pipeline, system, table, person) and record it in 'correcoes_transcricao'.
5. Identify the speakers when the screen (label of who is speaking, who is sharing) or the speech allows it.
6. Build the 'linha_do_tempo' (contiguous segments on the same subject), list the 'artefatos_vistos' (pipeline, dataset, linked service, container, folder, file, table, notebook, job, trigger, storage) and extract the 'itens'.
${CONDUTA}

Return the schema object. In 'frames_vistos' state how many frames you opened.`
}

const verifPrompt = (j, r) => {
  const v = VIDEOS[j.slug]
  const kk = String(j.janela).padStart(2, '0')
  return `You are the adversarial verifier of window ${j.janela} (${j.inicio} to ${j.fim}) of the video "${v.titulo}" (${v.data}). An analyst saw the frames and the transcript and extracted the items below. Knock down what is wrong and find what is missing. Do not assume the analyst got it right.

Window file: ${SCR}/videos/${j.slug}/janela_${kk}.json (frames and transcript). Crops the analyst made: ${SCR}/videos/${j.slug}/zoom/.

For each item (idx): open the frame(s) of the 'localizacao' and their neighbors, and the corresponding cue in the JSON. Confirm that the screen and the speech support the item, that the verbatim excerpt exists, that the statement does not exaggerate (an account turned into a rule, a proposal turned into an obligation, a misread value), that nature/stage are right, that the coverage in rules.jsonl checks out (Grep) and that the conflict holds. Check in kb/adf or in the JSON of the repository whether the cited artifacts exist with that name. Verdict for ALL the idx: 'mantido', 'corrigido' (with 'correcoes' only for the fields that change) or 'descartado' (with a reason).

Then, completeness: go through the transcript and the frames of the window looking for what the analyst did not record and add it to 'itens_novos'.
${CONDUTA}

## Analyst summary
${r.resumo}

## Analyst timeline
${JSON.stringify(r.linha_do_tempo)}

## Analyst items
${r.itens.map((it, i) => `idx ${i}: ${JSON.stringify(it)}`).join('\n')}`
}

// Windows completed on Opus before the stop: same prompt and same options, so that the resume uses the cache.
const CONCLUIDAS = new Set(Array.from({ length: 11 }, (_, i) => `adf_overview#${i + 1}`))
// The rest runs on a lighter agent, so as not to exceed the token quota.
const LEVE = { model: 'sonnet', effort: 'medium' }
const ECONOMIA = `

## Token economy (mandatory)
- Open the frames in BLOCKS: in the same response, make 8 to 10 Read calls in parallel (consecutive frames), analyse the block and only then open the next one. Never open one frame per response.
- Do not reopen a frame already seen. Crop (zoom) only when the text is relevant to the ingestion and unreadable, and crop only the region that matters.
- Frames of the participant grid with no shared screen: it is enough to record that there is no content.`

const res = await pipeline(
  JANELAS,
  j => CONCLUIDAS.has(`${j.slug}#${j.janela}`)
    ? agent(janelaPrompt(j), { label: `${j.slug}#${j.janela}`, phase: 'Windows', schema: JAN, effort: 'max' })
    : agent(janelaPrompt(j) + ECONOMIA, { label: `${j.slug}#${j.janela}`, phase: 'Windows', schema: JAN, ...LEVE }),
  (r, j) => {
    if (!r) return { slug: j.slug, janela: j.janela, erro: 'no result' }
    const base = { slug: j.slug, janela: j.janela, frames: j.frames, frames_vistos: r.frames_vistos, itens: r.itens.length }
    if (!r.itens.length) return base
    return agent(verifPrompt(j, r) + ECONOMIA, { label: `verif:${j.slug}#${j.janela}`, phase: 'Verification', schema: VERIF, ...LEVE })
      .then(v => ({ ...base, verif: v ? { descartados: v.vereditos.filter(x => x.veredito === 'descartado').length, corrigidos: v.vereditos.filter(x => x.veredito === 'corrigido').length, novos: v.itens_novos.length } : 'no result' }))
  },
)
const vistos = res.filter(Boolean).filter(r => r.frames_vistos !== undefined && r.frames_vistos < r.frames)
if (vistos.length) log(`windows with fewer frames seen than the total: ${vistos.map(r => r.slug + '#' + r.janela + ' ' + r.frames_vistos + '/' + r.frames).join(', ')}`)
return res
