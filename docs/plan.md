# Plan — skill `video-analysis`

## Errata (implementation)

What real use changed in this plan; where they diverge, this holds and not the text below.

- **Launching the Workflow:** the tool refuses a `scriptPath` outside the session, such as the skill's `workflow/video-analysis.js` (step 3 of the architecture). The normal case is the saved workflow, called by name (`name: "video-analysis"`), which the setup installs in `~/.claude/workflows/` with `--instalar-agentes`; the inline script (`script`) stays as a fallback. `prepare.py` says in `env.workflow_current` whether the saved copy is identical to the skill's.
- **Final report:** it is named `relatorio.md`, per video and combined (on resume, the previous one becomes `relatorio.anterior.md`), and not `report.md`: Claude Code blocks subagents from writing files named `report.md`, `summary.md` or `findings.md`. A `report.md` from an old run still counts as the final report on resume.
- **Custom agents:** in this environment, the agents installed in `~/.claude/agents/` were recognized in the middle of the session; the caveat that they only load in a new session may not hold.
- **Lockstep extraction:** a single ffmpeg decodes the video and the `split` filter feeds the two outputs (A in grayscale, B in JPEG), instead of two synchronized ffmpeg processes.
- **Folder of each video in the run:** `runs\<YYYYMMDD-HHMMSS>-<slug>\v<N>\` (`videos[].out_dir`), and not `<video-slug>\`: with the slug, the analyst joined the run's slug and the video's and wrote outside the folder. `v<N>` is the video's `vid` in the `prepare.py` JSON, numbered in the order of the inputs; a video that fails in the prepare (exit code 4) does not renumber the others, and the run may end up with `v1` and `v3`. On resume, an old run keeps the `<video-slug>\` folder.
- **Curation:** the request's `curadoria` option (`--curadoria plano|lotes`; in `workflow_args.options.curation`, `plan` or `batches`) is `plano` by default: the curator only plans, with the INIT writing the ledger and the prompt of batch 1, and the analysts of the following batches receive sections 4 and 5 of the ledger. The NEXT, before each batch after the 1st, only runs with `--curadoria lotes`, which on a 13-batch video cost ≈ 2× (US$ 9.9 against 5.4) with no gain in recall. Per video, there is 1 agent per batch plus 2 (with `lotes`, 2 per batch plus 1): the 55-min meeting in "Risks and mitigation" (≈50 batches) gives ≈52 agents, and not ≈100. With two or more videos, the run has 1 more, the CROSS (full count in `estimate.agents`, in `reference/contracts.md`).
- **Curator effort:** the `esforco_curador` option (`--esforco-curador`; in `workflow_args.options.curator_effort`) only applies to the NEXT; in the Workflow, INIT, FINAL and CROSS always run at `high`. In the default curation (`plano`), without NEXT, it changes nothing.
- **Translation to English:** on 2026-11-04, the skill was translated to English without changing its behavior: `README.md`, `SKILL.md`, `setup.md`, `reference/`, the agent roles and the workflow prompts, the comments, docstrings, messages and help texts of the code, the tests and `docs/`. The interface stays in Portuguese: the command-line flags and their values, the grammar of `pedido.md` (until the next item), the `retomar` and `ultimo` keywords, the names of files and folders, the JSON keys and values, the labels of the files generated for the agents and the templates of the reports.
- **Request in English:** right after the translation, the request became `request.md`, in `~/video-analysis/requests/` (it was `pedido.md`, in `pedidos/`), read by `prepare.py --request` (it was `--pedido`), with the sections `# videos`, `# objective` and `# options` and the option keys in English: `roles`, `max_batches`, `parallel`, `analyst_effort`, `curator_effort`, `curation`, `language`, `range`, `speakers`, `sensitivity` and `ignore_region`, with the values `all`, `plan` and `batches`. The old names still work: `--pedido`, `# objetivo`, `# opcoes`, the Portuguese keys (the names of the command-line options) and values; a request named `pedido.md` that fails still becomes `pedido.erro.md`, and the new one becomes `request.error.md`. The run keeps the copy as `request.md` and `run.json` points to it under `request` (an older run has `pedido.md` and the key `pedido`; nothing reads them). `SKILL.md` pre-approves Write in `requests/*` and still in `pedidos/*`. The command-line options, the error codes (`pedido_invalido`…) and the labels of `objective.md` stay in Portuguese.
- **English interface:** after the request, the rest of the interface became English, and the Portuguese names still work as aliases. The options of `prepare.py`, which are also the keys of `request.md`: `--roles`, `--max-batches`, `--parallel`, `--analyst-effort`, `--curator-effort`, `--curation plan|batches`, `--language`, `--range`, `--speakers`, `--sensitivity` and `--ignore-region`, plus `--estimate` and `--ignore-agent-limit` (the old `--papeis`, `--max-lotes`…, `--estimar` and `--forcar` still work). The keywords `resume` and `latest` (`retomar` and `ultimo`). The verifier, now `check_environment.py` with `--install-agents` and `--apply-permissions` (`verificar_ambiente.py` runs it), with `[MISSING]`/`[WARN]` and its JSON in English (`ready`, `actions`, `items`, `state`, `step`…). The module `scripts/va/environment.py` (`va.ambiente` re-exports it), the reference files (`contracts.md`, `troubleshooting.md`, `parameters.md`, `without-workflow.md`) and the files of `docs/` (`plan.md`, `design-agentic.md`, `design-deterministic.md`). The error codes (`missing_vtt`, `invalid_request`…), the states of `status.json` (`ready`, `partial`, `error`…) and the `range` key of `run.json` (it was `trecho`) changed without aliases: they are output, and nothing outside the skill reads them. What stays in Portuguese is the vocabulary of the agents: the run files (`relatorio.md`, `_descartado/`), the ledger and report templates, and the labels of `objective.md` and of the batches.

## Context

Today each video+subtitles analysis makes Claude write an ad hoc script (extract frames, match them with the subtitles, read everything), and that wastes tokens for nothing. The `video-analysis` skill makes the process fixed:

1. **Deterministic scripts** extract only the unique frames (a cursor-only change counts as a duplicate), each with the time interval it represents, and associate that interval with the speech in the `.vtt` of the same name.
2. **Agentic analysis on demand**: a Workflow drives batches **in sequence per video**. **Opus** builds the prompt of each batch and curates the context that is carried forward. **Sonnet** only executes the prompt: it reads frames and subtitles and writes the report.

Decisions made with you:
- Loop driven by a **Workflow script**; if the Workflow tool does not exist in the session, the main agent drives it through the Agent tool.
- Video types: **screen recording, recorded meeting and slides/lecture**.
- Output in a **central folder**, `C:\Users\digom\video-analysis\`, with a cache per video and one folder per analysis.
- Opus sees a **contact sheet of thumbnails** of the next batch to steer the prompt.
- **Permissions**: I may add Read/Write/Edit rules restricted to the central folder in `~/.claude/settings.json`. I show the exact snippet before writing it.
- **Effort**: `high` for the Sonnet analyst and for the Opus curator, both adjustable by an option.

Environment checked:
- Python 3.14.5 (`python`), numpy 2.4.6, Pillow 12.2.0, pytest 9.0.3, Node 24 and ffmpeg/ffprobe 8.1.2 on the PATH.
- **No OpenCV**, and nothing new will be installed.
- Claude Code 2.1.220, which supports `${CLAUDE_SKILL_DIR}`.
- 8 CPUs, so the Workflow runs up to 6 agents at the same time.
- Console in cp1252, so the scripts force UTF-8.
- `~/.claude/agents/` does not exist yet, so agents created in this session only load in a new session.

What is reused: in September, a session already wrote `frames.py` (ffmpeg + numpy, 2 fps, webcam mask), `preparar_janelas.py` and `wf_videos.js` for you. They are in `C:\Users\digom\AppData\Local\Temp\claude\C--Users-digom-Documents-solar-databricks--schema\cd2c1414-3ebd-4b7c-a607-fbbe8a10608f\scratchpad\`.
- Lesson 1: Sonnet skipped 17 to 38 frames in windows of 50. Hence the batches of ~20 frames with a check of `frames_seen`.
- Lesson 2: the quota ran out. Hence the lean curation and the minimal returns.
- The old outputs serve as a regression baseline; for example, "Repasse… DA’s" produced 128 frames.
- Since the Temp folder may be cleaned up, the first wave copies this data.

## Architecture

```
/video-analysis <videos|folder> <objective> [options]   |   /video-analysis retomar [run_id]   |   /video-analysis setup
      │
      ▼ MAIN AGENT — lightweight orchestrator: never opens frames, batches, transcript, prompts, ledger or batch reports
 1. Write ~/video-analysis/pedidos/pedido.md   (videos + verbatim objective + options; avoids quotes in the shell)
 2. Bash  python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" --pedido … [--estimar]   → 1 JSON (summary + workflow_args)
      │     DETERMINISTIC, with a cache per video: unique frames with interval → .vtt → alignment → batches of ~20 frames
      │     → batch_NNN.md (timeline for the LLM) + contact sheet sheet_NNN.jpg + transcript.md + summary.json
 3. Workflow(scriptPath = …/workflow/video-analysis.js, args = workflow_args)              → runs in the background
      │  per video (up to 3 videos in parallel; the batches of a video always in series):
      │    Opus INIT   reads objective + whole transcript + summary → ledger.md (per-batch plan) + prompt of batch 1
      │    for each batch n:
      │      Sonnet    reads prompt n + batch_n.md + ALL the images of the batch → reports/batch_n.md → {frames_seen, …}
      │      Opus NEXT reads ledger + report n + batch_{n+1}.md + contact sheet n+1 → rewrites the ledger (curation) + prompt n+1
      │    Opus FINAL  ledger + reports → report.md of the video
      │  2+ videos → Opus CROSS → combined report.md
 4. Notification → presents only the returned summaries (≤120 words per video) + paths of the reports
```

Where tokens are saved:
- Only unique frames go to analysis. The September baseline discarded 66% to 91% of the samples, and the revisit reuses screens that come back.
- Opus never recopies deterministic data: its prompt cites the path of `batch_NNN.md`.
- The transcript is read only once, in the INIT, and distilled into the ledger. The ledger has up to 1,500 words and is rewritten at every batch.
- The agents have minimal tools, so the system prompt is small, and they give back tiny structured returns.
- The per-stage cache avoids re-extracting frames when you ask a new question about the same video.
- The main agent only sees the `prepare.py` JSON and the final summaries.

### Where each thing lives

```
C:\Users\digom\.claude\skills\video-analysis\
  SKILL.md · setup.md · check_environment.py            (the verifier at the root, as verificar_ambiente.py in your other skills)
  scripts\prepare.py · calibrate.py · explain.py
  scripts\va\     __init__ (versions + default parameters), common, contracts, probe, ffmpeg_io, dedup, extract,
                  vtt, align, batching, render, sheets, cache, discover, runs, ambiente
  workflow\video-analysis.js
  agents\video-analysis-curator.md · video-analysis-analyst.md     (canonical; the setup copies them to ~/.claude/agents)
  reference\without-workflow.md · troubleshooting.md · contracts.md · parameters.md
  docs\design-deterministic.md · design-agentic.md               (full specs from the design agents)
  tests\          pytest (unit, synthetic, real fixtures) + synth.py + data\ + harness.mjs + check_run.py
C:\Users\digom\.claude\agents\video-analysis-{curator,analyst}.md  (installed by the setup)
C:\Users\digom\video-analysis\
  cache\<slug>-<fp8>\      cues-<h8>\ · frames-<h8>\ (F0001_hhmmss.jpg, frames.json, samples.csv) ·
                           batches-<h8>\ (batch_NNN.md/.json, sheet_NNN.jpg, overview.jpg, transcript.md, summary.json, timeline.json)
  runs\<YYYYMMDD-HHMMSS>-<slug>\   pedido.md, objective.md, run.json, prepare.json, status.json, report.md (combined)
                                   <video-slug>\{ledger.md, report.md, prompts\, reports\}
  pedidos\ · roles\ (copy of the roles for inline mode) · logs\ · debug\ · tmp\
```

## Deterministic layer (Python: ffmpeg + numpy + Pillow, no new dependencies)

**Extraction** (`va/extract.py`, `va/dedup.py`):
- Two ffmpeg processes run in sync:
  - **A** produces 2 fps in grayscale at 640×360 for the analysis;
  - **B** produces JPEG on the same grid, already at the output size (longer side 1568, ~160 KB).
- Python writes only the chosen JPEGs.
- `-skip_frame:v noref` makes the extraction up to 30% faster on H.264 with B-frames.
- `fps=…:round=near` gives exact timestamps, including on variable frame rate (VFR) video.
- Measured on this machine: ~0.06× real time on Teams 1080p16 and ~0.15× on 1080p60.

**Comparison**:
- Always against the **last kept frame**, so that slow changes and typing accumulate.
- The difference uses two thresholds (16/40) and connected components on a grid of cells.
- **Cursor**: up to 3 compact components the size of a pointer count as a duplicate. The size is 0.045 × the height, ≈49 px at 1080p, which covers 150% scaling. This includes the old and the new position merged and the blinking text cursor.

**Hot regions** (webcam, speaker ring, timers):
- The mask is computed per window of ±30 s.
- The window is cut at layout changes (gallery ↔ shared screen), so that the mask does not hide content of the shared screen.
- There are 3 criteria with hysteresis: continuous local activity, total activity and recurrence per minute.

**Stabilization**:
- A change only becomes a frame after 1 s of stability.
- During continuous scrolling or typing, "motion" frames come out at an interval growing from 4 to 30 s.
- A tooltip that appears and disappears goes back to counting as "same".
- **Revisit**: a screen equal to one of the last 16 (within 10 min) reuses the earlier image.
- In camera footage, the extraction degrades gracefully to ~2 frames/min (heartbeat).

**Intervals**: each frame has `start` (when the change began), `captured_at` (the instant of the image) and `end` (the start of the next one). The intervals are contiguous and cover the whole video, with times in integer ms.

**Subtitles** (`va/vtt.py`):
- Covers the 4 styles found on your disk:
  - WhisperX, with `SPEAKER_00:`;
  - Teams, with `<v Name>`, UUID and overlapping cues;
  - plain;
  - without hours (`MM:SS.mmm`), including with a format change after 1 h.
- Handles BOM, CRLF, NOTE/STYLE blocks, tags and HTML entities.
- Merges repetitions and "rolling" captions of the same speaker, never of different speakers.
- Splits long Whisper cues and marks the time as approximate (≈).

**Alignment and batches** (`va/align.py`, `va/batching.py`):
- A cue belongs to a frame when it overlaps the frame's interval.
- Each batch has at most 20 frames, 8,000 characters of speech and 15 min.
- The cut falls preferably on a scene change or a silence. A small tail is merged into the previous batch.

**Outputs**:
- `batch_NNN.md` (format in the design):
  - header with the speaker legend and the path of the contact sheet;
  - per frame: id, interval, absolute path of the image and cues;
  - each cue is printed once per batch and, after that, only referenced as `↳ C0123`.
- Contact sheet `sheet_NNN.jpg`: ~1,560×780, ≈1.5k tokens, with id and time on each thumbnail.
- `overview.jpg`: one thumbnail per batch.
- `transcript.md`: paragraphs per speaker, in the format `[hh:mm:ss · F… · B…]`.
- `summary.json`, `frames.json`, `cues.json`, `timeline.json` and `samples.csv` (diagnostics) also come out.

**Per-stage cache**:
- Each stage has a parameter key: `cues-<h8>`, `frames-<h8>`, `batches-<h8>`.
- Changing the batch size does not re-extract frames.
- Lock with heartbeat; `--gc` deletes old entries.

**Diagnostic tools**: `calibrate.py` (parameter sweep without decoding again) and `explain.py` (panel "why this frame was kept or ignored").

## Agentic layer

### Workflow `workflow/video-analysis.js`

**Input and returns**:
- The input is `workflow_args` v1 (see Unified contract).
- Structured returns (schema) of each agent:

  | Agent | Fields |
  |---|---|
  | INIT | `status, ledger_path, prompt_path, video_type, focus` |
  | analyst | `status ok\|partial\|failed, report_path, frames_seen, frames_missing[], notable` |
  | NEXT | `status, integrated_through, coverage[{batch,state}], prompt_path, focus, carried` |
  | FINAL and CROSS | `status, report_path, answered, summary ≤120 words` |

**Integrity**:
- `frames_seen` must match `batch_images`. If it does not, there is 1 "complement" retry with the ids that were missing.
- The NEXT also audits whether the report covers all the frames of the batch.

**Failures**:
- Each agent has up to 2 attempts.
- A batch that fails becomes a "gap", reported to the following NEXT.
- If the curation fails, the analyst receives a contingency prompt.
- Two consecutive failures of the same role stop the video. The files are kept for resuming.

**Resume from files**: the ledger header `Integrado até o lote: NNN` makes each step idempotent.

**Roles**:
- `agents` mode: uses `agentType`.
- `inline` mode: default subagent with `model`, and the prompt says to read the role in `roles/…md`.
- The main agent chooses according to whether the types appear in the list of available agents.

**Guards**:
- Above 80 estimated agents, the main agent confirms with you before launching.
- Above 800, the Workflow refuses (its ceiling is 1000).
- Workflow restrictions respected: literal `meta`, no `Date`, `Math.random` or file access.
- Phases in `/workflows`: Plan · Batches · Report · Synthesis.

### Curator — `video-analysis-curator` (Opus)
- **Frontmatter**: `tools: Read, Write, Edit, Grep` · `model: opus` · `effort: high` · `maxTurns: 40`.
- **Modes**: INIT, NEXT, FINAL, CROSS.
- **Ledger** (≤1,500 words):
  1. Objetivo interpretado
  2. Mapa e plano por lote
  3. Contrato do relatório
  4. Contexto acumulado
  5. Fios abertos
  6. Achados consolidados `[batch · hh:mm:ss · Fxxxx]`
  7. Lacunas
  8. Log de curadoria
- **Batch prompt** (≤450 words): objective of the batch, what the analyst needs to know (≤150 words), focus by id or time, questions Q1–Q6, format and up to 2 reference frames.
- **Rules**:
  - never copy the timeline or the speech; cite the path;
  - the contact sheet is only for planning, without describing frames;
  - context goes in only if it changes the reading of the batch;
  - the same contract in all batches.
- **Specialization by type of video**:
  - screen: step by step, typed values, errors;
  - meeting: speakers, decisions and pending items, speech × screen;
  - slides: title and points, what the presenter adds.

### Analyst — `video-analysis-analyst` (Sonnet)
- **Frontmatter**: `tools: Read, Write` · `model: sonnet` · `effort: high` · `maxTurns: 24`.
- **Steps**:
  1. Reads the prompt and the timeline.
  2. Opens ALL the images of the batch, in order, in blocks of 8–10 parallel Reads, without reopening.
  3. Writes the report.
  4. Returns the status.
- **Default report contract**:
  1. Linha do tempo: frame | intervalo | tela | fala | relação (confirma, complementa, diverge, só tela, só fala)
  2. Respostas
  3. Achados `[Fxxxx · hh:mm:ss]` com confiança
  4. Para levar adiante
  5. Incertezas e correções de legenda
  6. Alertas
- **Rules**: inference always marked; illegible text recorded as "ilegível"; sensitive data never copied; up to 1,000 words per 20 frames.

### SKILL.md (pt-BR, ≤180 lines)
- **Frontmatter**:
  - `name`;
  - `description` with ~950 characters and triggers such as "frame a frame", "contra a legenda", meetings, `retomar` and `setup`;
  - `argument-hint`;
  - `allowed-tools`: Bash for `prepare.py` and `check_environment.py`, and Write in `~/video-analysis/pedidos/*`.
- **Commands**: analysis, `retomar [run_id]` and `setup`.
- **Options**: `--max-lotes`, `--trecho hh:mm:ss-hh:mm:ss`, `--paralelo`, `--esforco-analista`, `--papeis` and `--idioma`.
- **Flow of the main agent**:
  1. Interprets the request. Asks only if the objective or the video is missing.
  2. Chooses the roles mode.
  3. Writes `pedido.md`.
  4. Runs `prepare.py --estimar`. Above 400 s, runs the prepare in the background (the Bash limit is 600 s).
  5. Runs `prepare.py`.
  6. Calls the Workflow with `script_path` and `workflow_args` without editing, replies in at most 3 lines and ends the turn.
  7. On the notification, presents the results.
- **Fallback**: without the Workflow tool, `reference/without-workflow.md` has the same 7 prompt templates for the Agent tool, with the return in 1 JSON line.

## Unified contract (prevails over the specs in `docs/` when there is a conflict)

1. **`workflow_args` v1** follows the format of the agentic design, with two changes:
   - `cache_dir` becomes **`batches_dir`**, which holds `batch_NNN.md`, `sheet_NNN.jpg`, `transcript.md`, `summary.json` and `overview.jpg`;
   - `batch_frames` becomes **`batch_images`**: how many images to open per batch. Revisits within the same batch do not count.

   Fields: `schema_version, run_id, run_dir, objective_path, roles_dir, roles_mode, language, options{max_batches, max_parallel_videos=3, analyst_effort=high, curator_effort=high}, combined_exists, videos[{slug, title, duration, n_frames, batches_dir, out_dir, batch_images[], start_batch, ledger_exists, start_prompt_exists, final_exists}]`. All paths are absolute and use `/`.
2. **`prepare.py`**:
   - The main input is `--pedido <md>`, with the sections `# videos`, `# objetivo` and `# opcoes`.
   - Options accepted in the request: `papeis, max_lotes, paralelo, esforco_analista, esforco_curador, idioma, trecho, falantes, sensibilidade, ignorar_regiao`. The last two are for the main agent to repeat the extraction with the region suggested by the density warning.
   - Once used, the request is moved to the run folder; on an error, it is renamed to `pedido.erro.md`.
   - `--estimar` does not consume the request.
   - Direct inputs with `--objective-file` still work for manual use.
   - `--resume <run_id|run_dir|ultimo>` follows the DL-11 semantics of the agentic design.
   - `trecho` creates its own batches stage over the cache of the whole video: batches numbered 001… within the segment, with the frame ids preserved.
3. **Output JSON**: the fields of the deterministic design, plus `run_id`, `script_path`, `estimate{batches, agents, seconds}`, `env{permissions_ok, agents_current}` and `workflow_args`.
   - An error comes out as `{"ok": false, "error": {"code", "detail"}}`.
   - Exit codes: 0 ok, 2 input, 3 environment, 4 video failed, 130 interrupted.
4. **Roles**:
   - On every run, `prepare.py` copies `agents/*.md` to `<workspace>/roles/`.
   - If the requested mode is `agents` but the copies in `~/.claude/agents/` are not identical, it demotes it to `inline`.
5. **Missing VTT**:
   - Raises an error listing candidates, with a suggested `Rename-Item`, by the strict same-name rule.
   - `--vtt` only works with 1 video.
   - In a folder, `--skip-missing` skips the videos without subtitles.
6. **Open questions of the designs, resolved**:
   - curator with Grep: yes;
   - confirmation above 80 agents;
   - FINAL runs on Opus;
   - revisit enabled;
   - 2 fps with 1 s stabilization;
   - labels of `batch_NNN.md` in the language detected in the subtitles;
   - `zoom.py` is left for v2.

## Permissions and setup

**Permission rules**:
- Merge into `C:\Users\digom\.claude\settings.json`, which today has no `permissions` block.
- `enabledPlugins` and the rest stay as they are, with a backup in `settings.json.bak-<date>`.
- I show the snippet in the chat before writing it:
  ```json
  "permissions": { "allow": [
    "Read(~/video-analysis/**)", "Edit(~/video-analysis/**)", "Write(~/video-analysis/**)",
    "Read(//c/Users/digom/video-analysis/**)", "Edit(//c/Users/digom/video-analysis/**)", "Write(//c/Users/digom/video-analysis/**)"
  ]}
  ```
- The two spellings stay until the E2E shows which one Windows accepts. Then I remove the one that does not work.

**`/video-analysis setup`**:
- Follows the pattern of `ingestao-adf`: `setup.md` + `verificar_ambiente.py [--json] [--instalar-agentes] [--aplicar-permissoes]`, with `[OK]/[FALTA]/[AVISO]` output.
- Checks: Python ≥3.10, ffmpeg and ffprobe, numpy and Pillow, writable central folder, permission rules, and whether the installed agents are identical to the canonical ones.
- `--instalar-agentes` creates `~/.claude/agents/` and copies the two roles.
- Restart warning: a new agent only loads when Claude Code opens. Until then the skill runs in `inline` mode, which works the same and spends a little more per agent.

## Implementation with Opus subagents (I only orchestrate)

Every file is written by subagents with `model: "opus"`. I launch the waves, check the reports and pass on corrections. Each subagent reads this plan (the unified contract prevails) and the corresponding section in `docs/`.

| Wave | Subagent (Opus) | Deliverable | Acceptance |
|---|---|---|---|
| 0 | Specs + foundation | Extracts the final reports of the two design agents to `docs/design-*.md`. Creates `scripts/va/{__init__,common,contracts}.py`, `tests/conftest.py` and `pytest.ini`. **Copies the September baseline** and short excerpts of your real VTTs to `tests/data/` (they stay only on your machine) | importable modules; data copied |
| 1 ∥ | Frames | `va/{probe,ffmpeg_io,dedup,extract}.py`, `calibrate.py`, `explain.py`, `tests/synth.py` + tests | synthetic ground truth; determinism |
| 1 ∥ | Subtitles and batches | `va/{vtt,align,batching,render,sheets}.py` (includes `overview.jpg`) + tests | excerpts of the 4 styles + edge-case file; golden test of `batch_NNN.md` and `transcript.md` |
| 1 ∥ | CLI and infra | `va/{cache,discover,runs,ambiente}.py`, `prepare.py`, `check_environment.py` + tests with simulated stages | hard names (NFD, ’, double spaces, `[PES]`); request, resume and estimate |
| 1 ∥ | Workflow | `workflow/video-analysis.js`, `tests/harness.mjs` (mocks of `agent`/`parallel`), `tests/check_run.py` | all the harness scenarios; restrictions lint clean |
| 1 ∥ | Roles | `agents/video-analysis-curator.md`, `agents/video-analysis-analyst.md` | return fields equal to the schemas; ≤3k and ≤2k tokens |
| 1 ∥ | Docs | `SKILL.md`, `setup.md`, `reference/*.md` | ≤180 lines; description ≤1,024 characters; fallback templates equal to the JS ones |
| 2 | Integration and calibration | `prepare.py` end to end on the 5 fixtures; calibration loop; freezes the parameters and records them in `tests/data/calibration.md` | Verification 1–4 |
| 3 ∥ | Review | Checks the contracts end to end (prepare ↔ args ↔ JS ↔ roles ↔ fallback ↔ SKILL), runs pytest and harness, fixes | all green |
| 3 ∥ | Setup | `--instalar-agentes`, then `--aplicar-permissoes` (after I show you the snippet) | setup without `[FALTA]` |
| 4 | E2E | I launch the skill; subagents check and fix | Verification 6 |

## Verification

1. **Unit pytest** (without ffmpeg):
   - VTT parser with real excerpts of the 4 styles and a synthetic edge-case file;
   - alignment: boundaries, overlap, a cue that spans batches;
   - batches;
   - numpy analyzer;
   - golden test of `batch_NNN.md` and `transcript.md`.
2. **Synthetic videos with ground truth** (Pillow → x264):

   | Scenario | Expected |
   |---|---|
   | only the cursor moves | 1 frame |
   | typing | 1 to 4 frames |
   | slides with camera | frames at 0/20/30/45/70 s |
   | meeting with a layout change | frames at 0/10/25/40/80/90/105 s, with no mask leakage |
   | scrolling | ≤5 "motion" frames |
   | tooltip and clock | ignored |
   | camera | ≤4 heartbeat frames |
   | VFR variant | the same times |

3. **Real fixtures**:
   - "Repasse… DA’s": between 50 and 128 frames, with recall ≥90% of the significant frames of the baseline (±2 s), plus a panel of the differences for review;
   - `venda_zero_clientes`: 98 cues, ≤15 frames/min, JPEG ≤500 KB;
   - `dias_estoque` (Teams): ≥2 speakers and overlapping cues;
   - `Criando sua primeira PR.mov`: output 1568×1018, with accents in the path;
   - performance: `metric_view` (28 min, 60 fps) ≤0.15× real time, Teams ≤0.06×, warm cache ≤2 s, RAM ≤400 MB.
4. **Determinism**: two executions with `--force all`, synchronized × sequential extraction and `--jobs 1` × `2` produce JSON, MD and JPEG identical byte for byte.
5. **`node tests/harness.mjs`**:
   - happy path;
   - analyst that skips frames;
   - curation that fails;
   - two consecutive failures;
   - resume;
   - video already finished;
   - 3 videos with parallelism 2;
   - CROSS;
   - size guard;
   - roles modes.
6. **E2E in this session** (inline mode):
   - Command: `/video-analysis C:\Users\digom\Downloads\venda_zero_clientes.mp4 Explique o indicador "venda zero clientes": o que é pedido, de onde vem cada dado citado e o que aparece na tela quando cada fonte é mencionada --max-lotes 2`.
   - Expected: 5 agents (INIT, A1, N2, A2, FINAL) and **no permission request**.
   - `check_run.py` must approve:
     - the ledger evolves;
     - the prompts cite `batch_NNN.md` without copying cues;
     - the reports cover all the frames;
     - `report.md` answers the objective.
   - Then, `retomar ultimo --max-lotes 3` must run without INIT and integrate batch 3.
7. **New session** (you open it): the same command with `--papeis agents`, to validate `agentType` + `schema` + `model` and compare the tokens with inline mode.

## Risks and mitigation
- **Syntax of the permission rules on Windows**: the E2E is blocking, and `prepare.py` reports `permissions_ok=false`.
- **`${CLAUDE_SKILL_DIR}` with backslashes or quotes in `allowed-tools`**: in the worst case, you approve once with "don't ask again".
- **Workflow refusing a `scriptPath` outside the session folder**: the main agent passes the script inline.
- **The cursor tolerance hides tiny changes** (one digit, up to ~4 characters). This is by design: they accumulate against the last kept frame, and `--sensitivity high` reduces the effect.
- **The grayscale difference does not see a color change with the same brightness**: it is documented.
- **Long videos are expensive and slow**: a 55-min meeting gives ≈50 batches, ≈100 agents, hours in series. Mitigation: confirmation above 80 agents, `--trecho` and `--max-lotes`.
- **API rate limits**: `--paralelo 1`.
- **Prepare above 600 s**: runs in the background, guided by `--estimar`.

## Out of scope (v2)
- "Coverage by relevance": the curator skips batches unrelated to the objective. It is the biggest gain in long meetings.
- Curation every 2 batches in homogeneous segments.
- Parallel chapters for a long video.
- `zoom.py`: native-resolution crop for tiny text.
- Difference in color too.
- Packaging as a plugin, to register the agents without copying or restarting.
