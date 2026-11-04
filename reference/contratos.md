# Contracts: request, prepare, workflow_args, schemas, return and folders

Source of truth: the "Unified contract" section of `docs/plano.md`. The schemas and the return hold as
they are in `workflow/video-analysis.js`; this page repeats them for reference. Every path in JSON and in
the generated `.md` files is absolute and uses `/` (`C:/Users/digom/...`).

## 1. `pedido.md`

The main agent writes it with Write to `C:/Users/digom/video-analysis/pedidos/pedido.md`. Nothing goes
through the shell: a path with spaces and an objective with quotes or colons need no escaping.

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

- Sections: exactly the lines `# videos`, `# objetivo` and `# opcoes`.
- `# videos`: one path per line (file, folder, or wildcard with `*`/`?`), without quotes. Folder: only the
  videos in it, without subfolders.
- `# objetivo`: the user's text, verbatim, on one or more lines. It goes to the run's `objective.md`.
- `# opcoes`: `key: value`, one per line; the value is the rest of the line after the first `:`.
  `papeis` always; `idioma` when the objective is not in Portuguese; the others, only the ones given.

| key | values | default | user option |
|---|---|---|---|
| `papeis` | `agents`, `inline` | chosen by the main agent (step 2) | `--papeis` |
| `max_lotes` | integer ≥ 1 (`todos` or `0` = no limit) | no limit | `--max-lotes` |
| `trecho` | `hh:mm:ss-hh:mm:ss` | whole video | `--trecho` |
| `paralelo` | 1 to 6 | 3 | `--paralelo` |
| `esforco_analista` | `low`, `medium`, `high`, `xhigh`, `max` | `high` | `--esforco-analista` |
| `esforco_curador` | same (applies only to the NEXT) | `high` | `--esforco-curador` |
| `curadoria` | `plano`, `lotes` | `plano` | `--curadoria` |
| `idioma` | `pt-BR`, `en`, `es`… | the objective's; `pt-BR` | `--idioma` |
| `falantes` | `SPEAKER_01=Name;SPEAKER_03=Other` | the labels in the subtitles | `--falantes` |
| `sensibilidade` | `low`, `normal`, `high` | `normal` | `--sensibilidade` |
| `ignorar_regiao` | `x,y,w,h` in fractions of the frame (0 to 1, origin at the top left corner) | none | `--ignorar-regiao` |

**Consumption.** On success, the request is moved to `runs/<run_id>/pedido.md`; on error, renamed to
`pedidos/pedido.erro.md` (replacing the previous one), and the next Write needs no Read. On exit code 4
(one video failed, the others form a run), a copy also goes to the run. `--estimar` does not consume it:
the next Write needs a Read first. An option given on the command line prevails over the one in the request.

## 2. The `prepare.py` command line used by the skill

```bash
python "<skill>/scripts/prepare.py" --pedido "C:/Users/digom/video-analysis/pedidos/pedido.md" --estimar
python "<skill>/scripts/prepare.py" --pedido "C:/Users/digom/video-analysis/pedidos/pedido.md"
python "<skill>/scripts/prepare.py" --resume <run_id|run_dir|ultimo> [--papeis agents|inline] [--max-lotes N] [--curadoria plano|lotes]
```

- `<skill>` is `${CLAUDE_SKILL_DIR}`. Always through the Bash tool and always `python`, never `python3`.
- Subtitle rule: the `.vtt` has the same name as the video, in the same folder. `--vtt "<path>"` points to
  another one (only with 1 video); `--skip-missing` skips, in a folder, the videos without subtitles.
- `--resume` accepts only `--papeis`, `--max-lotes`, `--paralelo`, `--esforco-analista`,
  `--esforco-curador` and `--curadoria`; an option not given keeps the value stored in the run (`curadoria`
  of a run older than the option: `lotes`). It refuses `--pedido`, inputs, `--estimar` and `--force`.
- Manual use, outside the skill: direct inputs (`<videos…> --objective-file <md> | --objective "…"
  [--run [NAME]]`; without an objective, it only warms the cache, without a run), `--gc [DAYS]` (default 30)
  and the parameters of [`parametros.md`](parametros.md). General: `--home FOLDER` (default
  `VIDEO_ANALYSIS_HOME` or `%USERPROFILE%\video-analysis`), `--jobs N` (videos in parallel, default 2),
  `--force[=all|cues|frames|batches]` (redoes the stage and the following ones), `--forcar` (accepts more
  than 800 agents, but the Workflow refuses them anyway), `--recursive`, `--json-out FILE`, `--ascii`,
  `--quiet`, `--debug`. The request options also exist as command-line options (`--papeis`, `--max-lotes`,
  `--paralelo`, `--esforco-analista`, `--esforco-curador`, `--curadoria`, `--idioma`, `--trecho`,
  `--falantes`, `--sensibilidade`, `--ignorar-regiao`). Full reference: `python prepare.py --help` and
  `docs/design-deterministico.md`, §2.

**Exit codes**, with the `error.code` of each one:

| exit code | meaning | `error.code` |
|---|---|---|
| `0` | ok (there may be `warnings`) | — |
| `2` | input | `uso_invalido`, `pedido_invalido`, `objetivo_ausente`, `opcao_invalida`, `video_nao_encontrado`, `vtt_ausente`, `run_nao_encontrado`, `run_inconsistente`, `run_grande_demais` |
| `3` | environment | `ffmpeg_ausente`, `dependencia_ausente`, `workspace_inacessivel`, `instalacao_incompleta` |
| `4` | one video failed; the others come in the JSON | `video_falhou` |
| `130` | interrupted; the completed stages stay in the cache | `interrompido` |
| `1` | internal error (a bug in `prepare.py`; traceback in the log, or on stderr with `--debug`) | `erro_interno` |

A video with all three stages in the cache (cues, frames, batches) needs neither ffprobe nor ffmpeg, only
numpy and Pillow: their absence only matters when some video needs to be probed or extracted.

## 3. `prepare.py` stdout

One JSON line, the last one on stdout. It is also saved to `<run_dir>/prepare.json` and to `logs/`. The
stderr starts with `run_dir: …` and carries the progress; the run's `status.json` is updated every 5 s.

```json
{"ok": true, "mode": "prepare", "version": "1.0.0", "home": "C:/Users/digom/video-analysis",
 "run_id": "20261006-153012-venda-zero-clientes",
 "run_dir": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes",
 "json_path": "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes/prepare.json",
 "videos": [{"vid": "v1", "slug": "venda-zero-clientes", "title": "venda_zero_clientes", "duration_s": 441.3,
   "n_frames": 86, "n_batches": 4, "frames_per_min": 11.7, "n_cues": 98, "language": "pt",
   "cache_hit": {"cues": false, "frames": false, "batches": false},
   "timing_s": {"frames": 26.4, "batches": 1.1}, "warnings": [], "…": "other fields of summary.json"}],
 "estimate": {"batches": 2, "agents": 4, "seconds": 28},
 "env": {"permissions_ok": true, "agents_current": false, "workflow_current": true},
 "script_path": "C:/Users/digom/.claude/skills/video-analysis/workflow/video-analysis.js",
 "workflow_args": {"schema_version": 1, "…": "section 4"},
 "errors": [], "warnings": []}
```

Keys on success, in this order: `ok`, `mode`, `version`, `home`, `run_id`, `run_dir`, `json_path`,
`videos`, `estimate`, `env`, `script_path`, `workflow_args`, `errors`, `warnings`.

Error (exit code 1, 2, 3, 4 or 130): the same keys, with `ok: false` and `error` (`code`, `detail`) right
after `ok`; `workflow_args` comes as `null` and the fields that do not apply come empty (`videos: []`). The
exception is exit code 4 with some video ready, which brings usable `videos` and `workflow_args` (below).

```json
{"ok": false, "error": {"code": "vtt_ausente", "detail": "…candidates in the folder and the suggested Rename-Item…"},
 "mode": "prepare", "…": "other fields of the envelope",
 "missing_vtt": [{"video": "C:/…/Reunião-20260610_150844-Gravação de Reunião.mp4",
   "candidates": ["C:/…/Reunião.vtt"], "suggestion": "Rename-Item -LiteralPath '…' -NewName '…'"}]}
```

- Extra keys per error, after `warnings`: `vtt_ausente` adds
  `missing_vtt: [{video, candidates, suggestion}]`; `video_nao_encontrado` adds `inputs: […]`, the
  inputs not found. In `video_falhou`, `errors` (the usual key) lists `vid, slug, video, code,
  detail` per video.
- Exit code 4 in the prepare: `run.json`, `objective.md` and a usable `workflow_args` with the videos that
  succeeded are still written; `error.code` is `video_falhou` and `errors` lists the ones that failed.
- `--gc` returns `"mode": "gc"` and the key `gc` (`removed`, `freed_mb`, `kept`, `failed`).

| field | use by the main agent |
|---|---|
| `ok`, `error.code`, `error.detail` | the error in one sentence |
| `estimate.seconds` | with `--estimar`, the estimate of the extraction: above 400 s, the prepare runs in the background. In the prepare and the resume, the time it took |
| `estimate.batches`, `estimate.agents` | above 80 agents, confirm with the user |
| `env.permissions_ok` | `false`: warn and offer the setup |
| `env.agents_current` | the copies in `~/.claude/agents/` are identical to the skill's; if not, `papeis: agents` becomes `inline`, with a warning |
| `env.workflow_current` | `~/.claude/workflows/video-analysis.js` is identical to the skill's script (the same Claude Code folder the verifier checks: `VA_CLAUDE_HOME` or `%USERPROFILE%\.claude`): the Workflow is called by name. If not, missing or of another version: inline script and a setup suggestion (step 6 of `SKILL.md`) |
| `warnings`, `videos[].warnings` | high density (with a suggested region to ignore), camera-like segment, short or offset subtitles |
| `workflow_args` | goes to the Workflow in `args`, as a JSON object and unedited |
| `script_path` | the skill's script, only for the inline Workflow (step 6 of `SKILL.md`): read and passed in `script`, unedited. In `scriptPath` the tool refuses it; with `env.workflow_current: true`, the normal case is `name: "video-analysis"`, the workflow saved by the setup |

- `estimate.agents` = Σ per video (batches to analyse + 2, or + 1 on a resume with a ledger, which has
  no INIT; with `curadoria: lotes`, 2 × batches + 1; a video already finished, with `final_exists`,
  counts 0) + 1 if there is more than one video (the CROSS, counted even with `combined_exists`).
- With `--estimar`, the envelope is the same, with `"mode": "estimate"`: ffprobe only, so `estimate.seconds`
  is the estimate of the extraction and the batch fields may come null with a cold cache.

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

| field | meaning |
|---|---|
| `run_id` | `YYYYMMDD-HHMMSS-<slug>` |
| `objective_path` | the run's `objective.md`: `# Objetivo (literal)`, `# Opções`, `# Vídeos` |
| `roles_dir` | `<central folder>/roles`, a copy of `agents/*.md` renewed on every run (read in inline mode) |
| `roles_mode` | `agents` or `inline`, already demoted if the installed copies differ |
| `language` | language of the reports |
| `options.max_batches` | `null` or `0` = all; N = only batches 1–N (test mode) |
| `options.max_parallel_videos` | 1 to 6, default 3; the batches of a video are always in series |
| `options.analyst_effort`, `options.curator_effort` | default `high` |
| `options.curation` | `plan` (default, and when missing): INIT, analysts and FINAL; `batches`: also the NEXT before each batch after the 1st |
| `combined_exists` | the combined `relatorio.md` already exists: the CROSS does not run |
| `videos[].slug` | short ASCII, unique in the run (a duplicate gets `-2`) |
| `videos[].duration`, `n_frames` | `hh:mm:ss`; unique frames of the video or of the segment |
| `videos[].batches_dir` | the cache's `batches-<h8>` folder (read-only for the agents) |
| `videos[].out_dir` | `<run_dir>/v<N>`, with `v<N>` = the video's `vid` in the `prepare.py` JSON, numbered in the order of the inputs (folder and wildcard by name; without the ones skipped by `--skip-missing`). A video that fails in the prepare (exit code 4) gets no folder and does not renumber the others: the run may have `v1` and `v3`, or only `v2`. Old runs: `<run_dir>/<slug>`. The name does not repeat the run's slug: with `<run_id>/<slug>`, the analyst would join the two and write outside |
| `videos[].batch_images` | images to open in each batch; a revisit within the same batch does not count. The length of the list is the total number of batches |
| `videos[].start_batch` | first batch, up to the last of this run, without `reports/batch_NNN.md` |
| `videos[].ledger_exists` | `ledger.md` exists |
| `videos[].start_prompt_exists` | `prompts/batch_{start}.md` exists |
| `videos[].final_exists` | `relatorio.md` exists and no batch is left: the video is skipped (`skipped`) |

**Validation in the Workflow.** Required: `schema_version: 1`, `run_id`, `run_dir`, `objective_path`,
`roles_dir`, `language` and, per video, `slug` (no repeats), `title`, `batches_dir`, `out_dir` and
`batch_images` (non-empty list of positive integers). Optional, if present, with the right type:
`roles_mode`, `combined_exists`, `options.*`, `start_batch`, `ledger_exists`, `start_prompt_exists`,
`final_exists`. The script replaces `\` with `/` in the paths and caps `max_parallel_videos` at 6. If the
validation fails: `invalid workflow_args (<fields>): run prepare.py and pass workflow_args as a JSON object,
without editing`; the old names `cache_dir` and `batch_frames` are pointed out in the message.

**Derived paths** (the same as the script's; `NNN` = batch with 3 digits):

| path | content |
|---|---|
| `{batches_dir}/batch_NNN.md` | timeline of the batch: frames, intervals, image paths, speech |
| `{batches_dir}/sheet_NNN.jpg` | contact sheet with the batch's thumbnails (curator only) |
| `{batches_dir}/overview.jpg` | one thumbnail per batch |
| `{batches_dir}/transcript.md` | transcript in paragraphs per speaker (the curator reads it in the INIT) |
| `{batches_dir}/summary.json` | counts and interval of each batch |
| `{out_dir}/ledger.md` | curated context of the video |
| `{out_dir}/prompts/batch_NNN.md` | curator prompt for the batch |
| `{out_dir}/reports/batch_NNN.md` | analyst's report |
| `{out_dir}/relatorio.md` | final report of the video |
| `{run_dir}/relatorio.md` | combined report (2+ videos) |
| `{roles_dir}/video-analysis-curator.md`, `{roles_dir}/video-analysis-analyst.md` | roles, in inline mode |

No file written by an agent is named `report.md`, `summary.md` or `findings.md`: Claude Code blocks
subagents from writing those names ("Subagents should return findings as text…"). That is why the final
report is `relatorio.md`.

**`batch_NNN.md`** (generated by `scripts/va/render.py`; labels in Portuguese when the subtitles are in
Portuguese, otherwise in English, except the revisit marker ` (revisita de Fxxxx)`, the same in any
language):

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

- **Image line:** only the absolute path, with `/`, of a JPEG; on a revisit of a frame from an earlier
  batch (header ending in `· = Fxxxx`), followed by exactly ` (revisita de Fxxxx)`, which is not part of the
  path. A revisit of a frame of the same batch (`· = Fxxxx (mesma tela, acima)`) has no image line.
  The same path never appears twice in a batch. `batch_images[n-1]` = number of image lines.
- **`Falantes:`** lists only who speaks in the batch. **`Folha de contato:`** (in English, `Contact sheet:`)
  is the curator's contact sheet; the analyst does not open it and it never counts as an image.
- Cues: `≈` = approximate time; `(cont.)` = began before the batch; `↳ C0141` = already printed above;
  `- (sem fala)`. `tests/check_run.py` checks this format (`lote.formato`).

**Ledger.** The second line starts with `Integrado até o lote: NNN de BBB` (`000` after the INIT). A batch
that failed also counts as integrated, recorded as a gap. It is this header that makes each step of the
curator idempotent on resume.

**Resume** (`--resume`, DL-11 semantics of the agentic design):

- without `ledger.md`: `prompts/` and `reports/` go to `_descartado/`, and `start_batch` = 1;
- with `relatorio.md` and pending work: it becomes `relatorio.anterior.md`; the same holds for the
  combined `relatorio.md` when some video is going to run;
- `report.md` from a run older than the renaming counts as the final report (in the video and in the
  combined one): without pending work, it is renamed to `relatorio.md`; with pending work, it goes to
  `relatorio.anterior.md` (or `report.anterior.md`, if there was also a `relatorio.md`);
- `trecho`: batches numbered 001… within the segment, with the frame ids of the whole video.

| ledger | `start_batch` | `start_prompt_exists` | what the Workflow does |
|---|---|---|---|
| no | — | — | INIT, then from batch 1 |
| yes | k | yes | analyst of batch k directly |
| yes | k | no | `batches`: NEXT k ("check Integrado até"), then analyst k; `plan`: the contingency analyst k |
| yes | last + 1 | — | only the FINAL |

`final_exists: true` skips the video.

## 5. Schemas of the agents' returns

Each agent returns the object through the StructuredOutput tool. Without it (procedure
[`sem-workflow.md`](sem-workflow.md)), it replies with a single JSON line with the same fields. Only the
types `type`, `enum`, `items`, `required` and `description` are used.

```js
const STR = { type: 'string' }, STRS = { type: 'array', items: { type: 'string' } }
const S_INIT = { type: 'object', required: ['status', 'ledger_path', 'prompt_path', 'video_type', 'focus'], properties: {
  status: { type: 'string', enum: ['ok', 'failed'] }, ledger_path: STR, prompt_path: STR,
  video_type: { type: 'string', enum: ['screen', 'meeting', 'slides', 'mixed', 'other'] },
  focus: { type: 'string', description: 'focus of batch 1, 1 sentence' }, warnings: STRS } }
const S_NEXT = { type: 'object', required: ['status', 'integrated_through', 'coverage', 'prompt_path', 'focus'], properties: {
  status: { type: 'string', enum: ['ok', 'failed'] },
  integrated_through: { type: 'integer', description: 'last batch whose report is integrated into the ledger' },
  coverage: { type: 'array', description: 'one item per report integrated in this call', items: { type: 'object', required: ['batch', 'state'],
    properties: { batch: { type: 'integer' }, state: { type: 'string', enum: ['complete', 'incomplete', 'missing'] } } } },
  prompt_path: STR, focus: { type: 'string', description: 'focus of the prepared batch, 1 sentence' },
  carried: { type: 'string', description: 'what was carried forward, ≤ 25 words' }, warnings: STRS } }
const S_ANALYST = { type: 'object', required: ['status', 'report_path', 'frames_seen', 'frames_missing', 'notable'], properties: {
  status: { type: 'string', enum: ['ok', 'partial', 'failed'] }, report_path: STR,
  frames_seen: { type: 'integer', description: 'images of the batch opened with Read, adding the previous attempt if this is the second' },
  frames_missing: { type: 'array', items: { type: 'string' }, description: 'ids of the frames of the batch whose image no attempt opened' },
  notable: { type: 'string', description: 'the most relevant point for the objective in this batch, ≤ 2 sentences, no sensitive data' }, warnings: STRS } }
const S_FINAL = { type: 'object', required: ['status', 'report_path', 'answered', 'summary'], properties: {
  status: { type: 'string', enum: ['ok', 'partial', 'failed'] }, report_path: STR,
  answered: { type: 'string', enum: ['yes', 'partly', 'no'] },
  summary: { type: 'string', description: 'direct answer to the objective, ≤ 120 words (on failure: the reason)' }, gaps: STRS } }
```

| agent | fields | valid when |
|---|---|---|
| INIT (curator) | `status, ledger_path, prompt_path, video_type, focus`, `warnings?` | `status: ok` |
| NEXT (curator) | `status, integrated_through, coverage[{batch, state}], prompt_path, focus`, `carried?`, `warnings?` | `status: ok` |
| analyst | `status ok\|partial\|failed, report_path, frames_seen, frames_missing[], notable`, `warnings?` | `status` ≠ `failed`, `report_path` = the requested path (tolerating `\`, `//`, spaces at the ends and upper case), `frames_seen` ≥ `batch_images[n-1]` and `frames_missing` empty |
| FINAL and CROSS (curator) | `status, report_path, answered, summary` (≤ 120 words), `gaps?` | `status` ≠ `failed` |

- `frames_seen` and `frames_missing` count **images** of the batch (the path lines of `batch_NNN.md`).
  Reference frames requested by the curator are extras and do not count.
- On the analyst's second attempt (a completion), `frames_seen` adds up the images seen in both attempts,
  and `frames_missing` lists only the ones neither attempt opened.
- A `report_path` other than the requested path never counts: the second attempt receives both paths and
  writes to the requested one; wrong again, the batch is `failed`, with the warning
  `batch N: report written to the wrong path (<path>)`, and the FINAL lists it under `Failed`; with
  `curadoria: lotes`, the following NEXT also receives it, as FAILED.
- `coverage` is the curator's audit: does the report's table cover all the ids of the batch? `incomplete`
  demotes the batch to `partial`; `missing`, to `failed`.
- The S_FINAL has no `warnings`: on failure, the reason goes in the `summary`. In the others, it goes in
  `warnings`. The Workflow repeats that reason (up to 160 characters) in the video's warning, in parentheses.

## 6. Workflow return

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
    "summary": "…direct answer to the objective, ≤ 120 words…",
    "batches": {"total": 4, "planned": 2, "from": 1, "ok": 2, "partial": [], "failed": []},
    "frames": "39/39",
    "warnings": []
  }],
  "combined": null
}
```

- `videos[].status`: `ok` · `partial` (a partial or failed batch, FINAL `partial`, or test mode with
  `planned` < `total`) · `failed` (video interrupted; the files stay for `retomar`) · `skipped`
  (already finished).
- `batches`: `ok` is a count; `partial` and `failed` are lists of batches; `from` is the starting batch
  (> 1 on a resume; greater than `planned` when only the FINAL was missing); `planned` is the last batch of
  this run (smaller only if the token budget ran out). `batches` is `null` for a video not run. `frames`:
  images seen/expected in this run (`null` without a new batch or in `skipped`). `warnings`: up to 6; with
  more, the first 4, `+N warnings omitted` and the last one (in a `failed` video, the reason for the
  interruption).
- `combined`: `null` (one video, fewer than 2 reports, or a combined report that already exists),
  `{report_path, answered, summary}`, or `{error: "synthesis failed twice (reason)"}`.
- `agents`: agent calls made in this run. The return does not count tokens: the Workflow's
  `budget.spent()` adds up the whole session, not the run (in a 5-agent run, it came to 2.77 M).

## 7. Central folder

```
C:\Users\digom\video-analysis\
  cache\<slug>-<fp8>\                   source.json, probe.json, last_used, .lock
    cues-<h8>\                          cues.json, stage.json
    frames-<h8>\                        F0001_hhmmss.jpg…, frames.json, samples.csv, ffmpeg_a.log, ffmpeg_b.log, stage.json
    batches-<h8>\                       batch_NNN.md/.json, sheet_NNN.jpg, overview.jpg, transcript.md,
                                        summary.json, timeline.json, batches.json, stage.json
  runs\<YYYYMMDD-HHMMSS>-<slug>\        pedido.md, objective.md, run.json, prepare.json, status.json, relatorio.md (combined)
    v<N>\                               ledger.md, relatorio.md, prompts\batch_NNN.md, reports\batch_NNN.md
  pedidos\                              pedido.md (temporary), pedido.erro.md
  roles\                                copy of the roles for inline mode
  logs\  debug\  tmp\
```

- `v<N>`: the video's `vid` (`v1`, `v2`…, in the order of the inputs). A video that fails in the prepare
  gets no folder and does not renumber the others (`videos[].out_dir`, section 4). An old run uses the
  video's slug instead, and the resume keeps that name.
- `<fp8>` comes from the path, the size and the date of the video: a moved, renamed or edited video is
  extracted again.
- Each cache stage has its own key (`<h8>`): changing the batching does not re-extract frames; changing the
  subtitles or the speakers does not re-extract frames; changing the sensitivity or the ignored region
  re-extracts frames and redoes the batches.
- The agents only read the cache; they write only in `runs\`.
