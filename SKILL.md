---
name: video-analysis
description: 'Analyses videos frame by frame against the subtitles for an objective given in the call: screen recordings, recorded meetings (Teams, Zoom) and lectures with slides. Without a same-name .vtt, it transcribes the video locally with WhisperX, asking how many people speak. Extracts only the unique frames (cursor motion does not count), aligns each with the speech and launches a Workflow in which an Opus curator plans the batches and a Sonnet analyst reads each one, in sequence; one report per video and, with two or more, a combined one. Use when asked to analyse, summarize, audit or extract steps, decisions, errors or evidence from a video (.mp4, .mkv, .mov, .webm) or a folder of videos, "frame a frame", "contra a legenda" or "contra a transcrição", or what appears on screen when something is said, and to resume an interrupted analysis (`resume`, `retomar`). With `setup`, prepares the machine: ffmpeg, Python, transcription, agents, workflow, permissions. Do not write an ad hoc frame script: use this skill.'
argument-hint: "<video(s) or folder> <objective> [--max-batches N] [--range hh:mm:ss-hh:mm:ss] [--num-speakers N] | resume [run_id] | setup"
allowed-tools:
  - Bash(python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" *)
  - Bash(python "${CLAUDE_SKILL_DIR}/check_environment.py" *)
  - Bash(python "${CLAUDE_SKILL_DIR}/verificar_ambiente.py" *)
  - Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/prepare.py" *)
  - Bash(python3 "${CLAUDE_SKILL_DIR}/check_environment.py" *)
  - Write(~/video-analysis/requests/*)
  - Write(~/video-analysis/pedidos/*)
---

# Frame-by-frame video analysis against the subtitles

This skill answers an objective about one or more videos (screen recording, recorded meeting, lecture
with slides) by cross-checking each screen against the speech in the `.vtt` of the same name. A video
without one is transcribed on this machine first, with WhisperX in the environment of the setup (step 6):
the skill asks how many people speak, and the `.vtt` it generates stays next to the video, so the next
analysis of that video asks nothing. `prepare.py` extracts only the unique frames, each with the interval
it represents, and splits them into batches of up to 20. A Workflow drives the batches of each video in
series: the curator (Opus) plans all the batches and writes the prompt of the first one; the analyst
(Sonnet) opens all the frames of the batch, cross-checks them against the speech and writes the batch
report. With `--curation batches`, the curator also writes the prompt of each following batch, carrying
the context forward.

**Golden rule: you do not read the video.** Frames, contact sheets, batches, subtitles, transcripts,
prompts, the ledger and batch reports are opened by the Workflow's agents. You prepare, launch and present.

## Commands

| call | what it does |
|---|---|
| `/video-analysis <video(s)\|folder> <objective> [options]` | new analysis: the **Flow** section |
| `/video-analysis resume [run_id]` | continues an interrupted run (also `retomar`); without an id, the one from this conversation (**Resume** section) |
| `/video-analysis setup` | prepares the machine: follow [`setup.md`](setup.md) and **do nothing else in this call** |

The argument reaches the end of the skill as `ARGUMENTS: …`. A request to "configure", "install" or
"prepare" the skill is also `setup`; if it interrupted an analysis, offer to resume it at the end.

Every command of this skill goes through the **Bash** tool, the one the skill pre-approves (never
PowerShell), with `python` on Windows (there, `python3` is the Microsoft Store shortcut) and `python3` on
macOS. The examples below use `python`.

| option | in `request.md` | effect |
|---|---|---|
| `--max-batches N` | `max_batches` | test mode: only batches 1–N of each video |
| `--range hh:mm:ss-hh:mm:ss` | `range` | only that segment, with its own batches over the cache of the whole video |
| `--parallel N` | `parallel` | videos in parallel (default 3); `1` against rate limits |
| `--analyst-effort low\|medium\|high` | `analyst_effort` | effort of the Sonnet analyst (default `high`; `medium` costs ~13% less and makes the answers less complete) |
| `--curation plan\|batches` | `curation` | `batches`: curator before each batch, carrying the context forward (≈ 2× the cost; default `plan`) |
| `--curator-effort low\|medium\|high` | `curator_effort` | effort of the Opus curator in the batches, only with `--curation batches` (default `high`) |
| `--roles agents\|inline` | `roles` | forces the roles mode (without the option, step 2 applies) |
| `--language xx` | `language` | language of the reports (default: that of the objective; when in doubt, `pt-BR`) |
| `--speakers "SPEAKER_01=Name;…"` | `speakers` | real names of the speakers in the subtitles |
| `--num-speakers N` | `num_speakers` | how many people speak, for a video the skill transcribes: `3`, a range `2-4`, a minimum `4+`, `auto`, or `1` (no speaker separation); per video, `v1=3; v2=auto`. Without it, step 4 asks |
| `--whisper-model NAME` | `whisper_model` | Whisper model of the transcription (default `large-v3-turbo` on a CPU and on a Mac, `large-v3` on an NVIDIA GPU) |
| `--audio-language xx` | `audio_language` | language spoken in the video, for the transcription (default `auto`: detected over the speech); `--language` is the reports' |
| `--sensitivity low\|normal\|high` | `sensitivity` | `high` catches smaller changes (more frames); `low`, the opposite |
| `--ignore-region x,y,w,h` | `ignore_region` | region (fractions of the frame) left out of the comparison: webcam, clock |
| `--ignore-timeline FILE.json` | `ignore_timeline` | regions left out only during spans of the video: a webcam that comes and goes with the layout (one file per video, from `scripts/webcam_mask.py --timeline`; it prevails over the automatic timeline of that video, even without spans) |
| `--webcam-timeline auto\|on\|off` | `webcam_timeline` | the webcam timeline that `prepare.py` makes with DeepFace when the setup created its environment: `auto` (default) probes the video and runs the full scan only if a face shows up; `on` always runs the full scan; `off` never runs it |

When to change each one: [`reference/parameters.md`](reference/parameters.md). The Portuguese names of the
earlier version (`--max-lotes`, `--papeis`, `--curadoria plano|lotes`, `retomar`, the same keys in the request…)
still work, as do `num_falantes`, `modelo_whisper` and `idioma_audio`: map them to the ones above.

## Token economy (applies to you)

- **Never read** frames, contact sheets, the `batches-*`, `transcript-*` and `shots-*` folders, `frames.json`,
  `cues.json`, `timeline.json`, `transcript.md`, the subtitles (`.vtt`, also the one the skill generates),
  `prompts/`, `ledger.md` or `reports/`. **Never run** ffmpeg or WhisperX, nor write an ad hoc frame or
  transcription script.
- Do not repeat the `prepare.py` JSON or the `workflow_args` in your answer.
- Read a `relatorio.md` only if the user asks something the `summary` does not answer; a frame or a
  batch report, only if the user names it. To go deeper into a segment, start a new run with `--range`.
- Invoking this skill **is** the explicit request for a Workflow with many agents: 1 per batch plus 2 per
  video (with `--curation batches`, 2 per batch plus 1), plus 1 with two or more videos. Do not merge or
  skip batches to fit a 10-agent guideline.

## Flow

1. **Interpret.** Separate from `ARGUMENTS` the paths (videos or folders; a path ends in the video
   extension, such as `.mp4`, `.mkv`, `.mov` or `.webm`, or comes in quotes), the `--…` options and the
   objective: the rest, **verbatim**. How many people speak, when the user says it ("são 3 pessoas"), is
   `num_speakers`. Ask only if the objective is missing (one question, with two example objectives) or if
   no video can be identified.
2. **Roles.** `agents` if `video-analysis-curator` and `video-analysis-analyst` appear in the list of
   available agent types; otherwise `inline`, which works the same and spends a little more per agent.
   The user's `--roles` option prevails.
3. **Request.** With Write, save `<home>/requests/request.md`, where `<home>` is the central folder
   (**Files** section) with `/`, such as `C:/Users/<user>/video-analysis`. Take it from the `home` that
   `prepare.py` or `check_environment.py` already printed in this conversation; otherwise run
   `python "${CLAUDE_SKILL_DIR}/check_environment.py" --paths` (instant: it checks nothing) and use its
   `home`. If the file exists, read it first (Write requires it). The objective goes in verbatim, with quotes and line breaks: nothing
   goes through the shell. In `# options`, with the keys of the table above: `roles` always, `language` if
   the objective is not in Portuguese, and only the other options given (`num_speakers` only when the
   user said it: otherwise step 4 asks, when a video needs it).

   ```
   # videos
   C:/Users/<user>/Downloads/venda_zero_clientes.mp4

   # objective
   <the user's text, verbatim>

   # options
   roles: inline
   max_batches: 2
   ```

4. **Estimate:**

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" --request "<home>/requests/request.md" --estimate
   ```

   The estimate does not consume the request; an error here is handled as in step 5.
   - **Subtitles.** Each video's `transcript.state` says where its subtitles come from: `file` (the `.vtt`
     of the same name), `cached` (transcribed before; only rendered again), `pending` (it will be
     transcribed in step 5, the whole video even with `--range`) or `choice` (no `.vtt` of the same name,
     but a similar one in the folder, in `transcript.candidates`, such as the Teams transcript
     `<Meeting>.vtt` of `<Meeting>-YYYYMMDD_HHMMSS-Gravação de Reunião.mp4`). A video with `choice`, or
     with `transcript.needs_speakers: true`, needs the **form** below before step 5.
   - **Background.** If `estimate.seconds` exceeds 400, or some video is `pending` or `choice`, run step 5
     with `run_in_background`, say in one line that the work continues in the background and wait for the
     notification: Bash cuts off at 600 s. In that line, mention the local transcription
     (`transcript.estimate_s`) and a webcam timeline to make (`webcam_timeline.state` `pending`, up to
     `webcam_timeline.estimate_s`, which assumes a face is found), both already counted in
     `estimate.seconds`, and a `transcript.detail` (without a Hugging Face token, the subtitles will not
     separate the speakers: setup step 7).

   **Form: how many people speak.** With AskUserQuestion (if it is deferred, load it with `ToolSearch`,
   `select:AskUserQuestion`), one question per video that needs it, in the language of the conversation:
   - the question `Quantas pessoas falam em «<title>» (<duration>)?`, with the header `Falantes` (with two
     or more videos, `Vídeo 1`, `Vídeo 2`…, by `vid`) and the options `2 pessoas`, `3 pessoas`, `4 ou mais`
     and `Não sei` (description: `um agente conta pelas imagens do vídeo`), adapted to the context
     (`1 pessoa` first in a lecture); "Other" takes `1`, `5`, `2-4`;
   - a `choice` video: first the option `Usar a legenda existente`, whose description names the first
     candidate (a Teams transcript also brings the real names of who speaks), then `2 pessoas`,
     `3 pessoas` and `Não sei`; with `needs_speakers: false` (no Hugging Face token), only `Usar a legenda
     existente` and `Transcrever aqui`;
   - more than 4 videos: one question for all of them. Do not ask about a video whose count the user
     already gave.

   `Não sei` → **the count from the images**: run
   `python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" --request "<home>/requests/request.md" --speaker-shots v1,v3`
   with the vids answered so (12 screenshots per video, in seconds; the request stays), then, in a single
   message, one Agent per video with the prompt of [`reference/speaker-count.md`](reference/speaker-count.md)
   and that video's `shots.paths`. Its last line, `SPEAKERS: 4-12` (or `auto`), is the video's value; say it in
   one line (`Pelas imagens: 4 a 12 pessoas em «<title>»`). Without the Agent tool, `auto`.

   Then Read and Write the request again with `num_speakers` in `# options`: `2 pessoas` → `2`,
   `3 pessoas` → `3`, `4 ou mais` → `4+`, `Não sei` → the value from the images, `Transcrever aqui` →
   `auto`, "Other" → the text as typed (the parser accepts `2 a 4`, `5 pessoas`…). The same answer for every
   video: `num_speakers: 3`; different answers: `num_speakers: v1=3; v2=4-12`, by the `vid` of the estimate.
   `Usar a legenda existente` is the consent to rename it: with Bash, `mv -n "<candidate>" "<folder of the
   video>/<name of the video without the extension>.vtt"` (rename the subtitles, never the video, which would
   be extracted again), leave that video out of `num_speakers` and run the estimate again.
5. **Prepare.** The same command without `--estimate`, with `timeout` 600000 (or in the background, as in
   step 4). The last line of stdout is a JSON (fields in [`reference/contracts.md`](reference/contracts.md));
   the request moves to the run folder.
   - exit code other than 0, or `ok: false` → state the problem in one sentence, by exit code:
     - `2` input, by `error.code`: `missing_vtt` → per item of `missing_vtt`, the candidates and the
       suggested rename from the `detail` (`Rename-Item` on Windows, `mv -n` on macOS; the user is the one
       who decides to rename), and, by `transcription.state`: `absent` → also offer `/video-analysis
       setup`, whose step 6 installs the local transcription; `ok` → a `choice` video without
       `num_speakers`: the form of step 4; `unsupported` → this machine cannot transcribe (the `detail`
       says why): the rename or a `.vtt` only. `video_not_found` → confirm the path; `run_too_large` (more
       than 800 agents) → propose `--range` or `--max-batches`; invalid request or option → fix the
       request.
     - `3` environment (ffmpeg, Python, numpy, Pillow; the transcription environment, when a video has no
       subtitles; with `webcam_timeline: on`, the DeepFace environment): offer `/video-analysis setup`.
     - `4` a video failed: say which one (`errors`; a `detail` that starts with `transcription:` is its
       transcription); if `workflow_args` came back, the others are ready: ask whether to go on with them
       (step 6, with that JSON).
     - `130` interrupted: the completed stages stay in the cache (a transcription, its blocks of ~10 min);
       offer to run it again.
     - `1` internal error of `prepare.py`: give the `detail` and the log in `logs\`; do not try to work
       around it.

     An error in step 5 (not in 4) renames the request to `request.error.md`: to retry, write it again.
   - `env.permissions_ok: false` → warn that every frame read will ask for permission and offer the
     setup before launching.
   - `estimate.agents` above 80 → give the batches, the agents and the approximate duration (≈ 4 min per
     batch, in series within the video, videos in parallel) and ask before going on.
   - a high-density warning with a suggested region to ignore → offer to repeat with
     `ignore_region: x,y,w,h` in a new request (fewer frames, lower cost).
   - `webcam timeline failed` (in the video's warnings) or `webcam timeline skipped` (in `warnings`) → the
     frames were extracted without the webcam timeline; for `skipped`, the DeepFace environment is not the
     setup's (it does not run, another deepface version, damaged weights), and for a `failed` whose detail
     points to DeepFace or its libraries (an `ImportError`, a DLL), it does not load: offer
     `/video-analysis setup` at the end.
   - `speakers not separated` (in the video's warnings) → offer, at the end, step 7 of the setup (the
     person saves a Hugging Face token); for `the diarization failed (…)`, fixing the cause the error names
     instead (the network or the proxy, the memory). Afterwards, a new call with `num_speakers` (even the
     same count) redoes only the speakers, not the transcription. `language detected with low confidence` →
     offer a new run with `audio_language`.

   Gather into a single question whatever needs an answer. A warning that needs no answer fits in one of
   the 3 lines of step 6. Other errors and warnings: [`reference/troubleshooting.md`](reference/troubleshooting.md).
6. **Launch** through the **Workflow** tool (deferred: load it with `ToolSearch`, `select:Workflow`),
   with `args` = `workflow_args` **as a JSON object, not as a string, and without editing**, in this order:
   - `name: "video-analysis"`, the workflow saved by the setup in `~/.claude/workflows/`, only with
     `env.workflow_current: true` (the saved copy is identical to the skill's) and if the setup did not
     install or renew it in this conversation: the Workflow keeps using the previous version in memory
     until the next conversation;
   - otherwise, or if the tool does not know the name: if the Workflow has already returned a script path
     in this conversation, copy the `script_path` over it with Bash (`cp`) and pass that path in
     `scriptPath`; without one, `script` = the content of the `script_path`, read with Read, without
     editing (the tool refuses it in `scriptPath`). It costs ~13k tokens: if the saved copy is out of
     date, suggest `/video-analysis setup` when presenting;
   - if the tool does not exist in this session: [`reference/without-workflow.md`](reference/without-workflow.md).

   Reply in at most 3 lines (run_id, batches, agents, run folder, "aviso quando terminar") and **end
   the turn**, without monitoring it.
7. **Present**, when the notification arrives, each video like this:

   ```
   **<slug>** — <status> · lotes <batches.from>–<batches.planned> de <batches.total> (<batches.ok> ok) · imagens <frames>
   <summary>
   Relatório: <report_path>
   ```

   A null field is left out of the line (`skipped`: video already finished, only the report; `from`
   greater than `planned`: no new batch). A video whose `transcript.state` was `made` or `cached` in the
   `prepare.py` JSON gets one more line: `Legenda gerada: <transcript.sidecar, or transcript.path> ·
   <transcript.model> · <transcript.language> · <transcript.speakers_found> falantes` (without
   `speakers_found`, `falantes não separados`). Then the batches in `batches.partial` and
   `batches.failed`, up to 3 warnings of the video and, if present, the `combined` (summary and path;
   with `error`, the synthesis failed). `partial` with `--max-batches` is partial coverage, not an error;
   `failed` → the last warning gives the reason; offer `/video-analysis resume`. End by offering to open a
   report or to run a `--range`; for transcribed subtitles, whose speakers are anonymous (`SPEAKER_00`…),
   also a new run with `--speakers "SPEAKER_00=Nome;…"`, which redoes only the subtitles and the batches.

## Resume

Which run: the run_id the user gave; otherwise, the `run_id` (or the `run_dir`) that `prepare.py` returned
in this conversation. With neither of the two, or if the user asked for the most recent one (`latest`),
be careful: the user runs several sessions, and the most recent one may come from **any** of them. Find
it with Glob in `<home>/runs/*/run.json`, `<home>` as in step 3: the one whose folder has the greatest
name, which starts with `YYYYMMDD-HHMMSS`. Confirm the run_id with the user and only then run with that
id: `--resume` already changes the run's files.

```bash
python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" --resume <run_id> --roles inline
```

Replace `inline` with the mode from step 2 and add `--max-batches N` and `--curation plan|batches` only if
the user asked for each one; without `--curation`, the run's curation applies (a run older than the
option comes back with `batches`). Then, steps 5 to 7. The Workflow restarts at the 1st batch without a
report; a video with a finished `relatorio.md` is left out.

## Setup

Follow [`setup.md`](setup.md). The verifier is at the root of the skill; always run it with this prefix,
which the skill pre-approves, plus the options the setup indicates (`--json`, `--install-agents`,
`--apply-permissions`, `--install-transcription`, `--install-deepface`):

```bash
python "${CLAUDE_SKILL_DIR}/check_environment.py"
```

`--install-agents`, `--apply-permissions`, `--install-transcription` and `--install-deepface` change files
outside the central folder: only after the "yes". `--install-transcription` downloads about 6.5 GB: always
with `run_in_background`.

## Files

Central folder (`<home>`): `%USERPROFILE%\video-analysis\`, or the folder in `VIDEO_ANALYSIS_HOME`;
the `home` of `check_environment.py --paths` gives its concrete path. `cache\<slug>-<fp8>\` (subtitles,
frames, batches, the webcam timeline, the local transcription and the screenshots of the speaker count,
reused across runs) · `runs\<YYYYMMDD-HHMMSS>-<slug>\` (combined `relatorio.md`, with 2+ videos; per video,
`v1\relatorio.md`, `v2\…`, with ledger, prompts and batch reports) · `requests\` · `roles\` · `logs\`. The
`.vtt` the skill generates goes next to the video, only when there is none there. Formats and paths:
[`reference/contracts.md`](reference/contracts.md). Errors and warnings:
[`reference/troubleshooting.md`](reference/troubleshooting.md).
