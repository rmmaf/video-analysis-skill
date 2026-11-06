---
name: video-analysis
description: 'Analyses videos frame by frame against the subtitles (same-name .vtt) for an objective given in the call: screen recordings, recorded meetings (Teams, Zoom) and lectures with slides. Extracts only the unique frames (cursor-only changes do not count), aligns each with the speech, splits them into batches and launches a Workflow in which an Opus curator plans the batches and a Sonnet analyst reads the frames and speech of each batch, in sequence; delivers one report per video and, with two or more videos, a combined one. Use when asked to analyse, summarize, audit or extract steps, decisions, errors or evidence from a video (.mp4, .mkv, .mov, .webm) or a folder of videos, "frame a frame", "contra a legenda" or "contra a transcrição", or what appears on screen when something is said, and to resume an interrupted analysis (`resume`, or `retomar`). With `setup` (`/video-analysis setup`), prepares the machine: ffmpeg, Python, agents, workflow and permissions. Do not write an ad hoc frame script: use this skill.'
argument-hint: "<video(s) or folder> <objective> [--max-batches N] [--range hh:mm:ss-hh:mm:ss] | resume [run_id] | setup"
allowed-tools:
  - Bash(python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" *)
  - Bash(python "${CLAUDE_SKILL_DIR}/check_environment.py" *)
  - Bash(python "${CLAUDE_SKILL_DIR}/verificar_ambiente.py" *)
  - Write(~/video-analysis/requests/*)
  - Write(~/video-analysis/pedidos/*)
---

# Frame-by-frame video analysis against the subtitles

This skill answers an objective about one or more videos (screen recording, recorded meeting, lecture
with slides) by cross-checking each screen against the speech in the `.vtt` of the same name. `prepare.py`
extracts only the unique frames, each with the interval it represents, and splits them into batches of
up to 20. A Workflow drives the batches of each video in series: the curator (Opus) plans all the batches
and writes the prompt of the first one; the analyst (Sonnet) opens all the frames of the batch,
cross-checks them against the speech and writes the batch report. With `--curation batches`, the curator
also writes the prompt of each following batch, carrying the context forward.

**Golden rule: you do not read the video.** Frames, contact sheets, batches, transcripts, prompts, the
ledger and batch reports are opened by the Workflow's agents. You prepare, launch and present.

## Commands

| call | what it does |
|---|---|
| `/video-analysis <video(s)\|folder> <objective> [options]` | new analysis: the **Flow** section |
| `/video-analysis resume [run_id]` | continues an interrupted run (also `retomar`); without an id, the one from this conversation (**Resume** section) |
| `/video-analysis setup` | prepares the machine: follow [`setup.md`](setup.md) and **do nothing else in this call** |

The argument reaches the end of the skill as `ARGUMENTS: …`. A request to "configure", "install" or
"prepare" the skill is also `setup`; if it interrupted an analysis, offer to resume it at the end.

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
| `--sensitivity low\|normal\|high` | `sensitivity` | `high` catches smaller changes (more frames); `low`, the opposite |
| `--ignore-region x,y,w,h` | `ignore_region` | region (fractions of the frame) left out of the comparison: webcam, clock |
| `--ignore-timeline FILE.json` | `ignore_timeline` | regions left out only during spans of the video: a webcam that comes and goes with the layout (one file per video, from `scripts/webcam_mask.py --timeline`; it prevails over the automatic timeline of that video, even without spans) |
| `--webcam-timeline auto\|on\|off` | `webcam_timeline` | the webcam timeline that `prepare.py` makes with DeepFace when the setup created its environment: `auto` (default) probes the video and runs the full scan only if a face shows up; `on` always runs the full scan; `off` never runs it |

When to change each one: [`reference/parameters.md`](reference/parameters.md). The Portuguese names of the
earlier version (`--max-lotes`, `--papeis`, `--curadoria plano|lotes`, `retomar`, the same keys in the request…)
still work: map them to the ones above.

## Token economy (applies to you)

- **Never read** frames, contact sheets, the `batches-*` folders, `frames.json`, `cues.json`,
  `timeline.json`, `transcript.md`, `prompts/`, `ledger.md` or `reports/`. **Never run** ffmpeg or write
  an ad hoc frame script.
- Do not repeat the `prepare.py` JSON or the `workflow_args` in your answer.
- Read a `relatorio.md` only if the user asks something the `summary` does not answer; a frame or a
  batch report, only if the user names it. To go deeper into a segment, start a new run with `--range`.
- Invoking this skill **is** the explicit request for a Workflow with many agents: 1 per batch plus 2 per
  video (with `--curation batches`, 2 per batch plus 1), plus 1 with two or more videos. Do not merge or
  skip batches to fit a 10-agent guideline.

## Flow

1. **Interpret.** Separate from `ARGUMENTS` the paths (videos or folders; a path ends in the video
   extension, such as `.mp4`, `.mkv`, `.mov` or `.webm`, or comes in quotes), the `--…` options and the
   objective: the rest, **verbatim**. Ask only if the objective is missing (one question, with two example
   objectives) or if no video can be identified.
2. **Roles.** `agents` if `video-analysis-curator` and `video-analysis-analyst` appear in the list of
   available agent types; otherwise `inline`, which works the same and spends a little more per agent.
   The user's `--roles` option prevails.
3. **Request.** With Write, save `<home>/requests/request.md`, where `<home>` is the central folder
   (**Files** section) with `/`, such as `C:/Users/<user>/video-analysis`. Take it from the `home` that
   `prepare.py` or `check_environment.py` already printed in this conversation; otherwise run
   `python "${CLAUDE_SKILL_DIR}/check_environment.py" --paths` (instant: it checks nothing) and use its
   `home`. If the file exists, read it first (Write requires it). The objective goes in verbatim, with quotes and line breaks: nothing
   goes through the shell. In `# options`, with the keys of the table above: `roles` always, `language` if
   the objective is not in Portuguese, and only the other options given.

   ```
   # videos
   C:/Users/<user>/Downloads/venda_zero_clientes.mp4

   # objective
   <the user's text, verbatim>

   # options
   roles: inline
   max_batches: 2
   ```

4. **Estimate**, with the **Bash** tool (it is the one the skill pre-approves; do not use PowerShell or
   `python3`):

   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/prepare.py" --request "<home>/requests/request.md" --estimate
   ```

   The estimate does not consume the request; an error here is handled as in step 5. If
   `estimate.seconds` exceeds 400, run step 5 with `run_in_background`, say in one line that the
   extraction continues in the background and wait for the notification: Bash cuts off at 600 s. A video
   whose `webcam_timeline.state` is `pending` first gets its webcam timeline with DeepFace, already counted
   in `estimate.seconds` (up to `webcam_timeline.estimate_s`, which assumes a face is found): mention it in
   that line.
5. **Prepare.** The same command without `--estimate`, with `timeout` 600000. The last line of stdout is a
   JSON (fields in [`reference/contracts.md`](reference/contracts.md)); the request moves to the run folder.
   - exit code other than 0, or `ok: false` → state the problem in one sentence, by exit code:
     - `2` input, by `error.code`: `missing_vtt` → show the candidates and the suggested `Rename-Item`
       from the `detail` (the user is the one who decides to rename); `video_not_found` → confirm
       the path; `run_too_large` (more than 800 agents) → propose `--range` or `--max-batches`;
       invalid request or option → fix the request.
     - `3` environment (ffmpeg, Python, numpy, Pillow; with `webcam_timeline: on`, the DeepFace
       environment): offer `/video-analysis setup`.
     - `4` a video failed: say which one (`errors`); if `workflow_args` came back, the others are ready:
       ask whether to go on with them (step 6, with that JSON).
     - `130` interrupted: the completed stages stay in the cache; offer to run it again.
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
   greater than `planned`: no new batch). Then the batches in `batches.partial` and `batches.failed`, up to 3
   warnings of the video and, if present, the `combined` (summary and path; with `error`, the synthesis
   failed). `partial` with `--max-batches` is partial coverage, not an error; `failed` → the last warning
   gives the reason; offer `/video-analysis resume`. End by offering to open a report or to run a
   `--range`.

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
`--apply-permissions`, `--install-deepface`):

```bash
python "${CLAUDE_SKILL_DIR}/check_environment.py"
```

`--install-agents`, `--apply-permissions` and `--install-deepface` change files outside the central
folder: only after the "yes".

## Files

Central folder (`<home>`): `%USERPROFILE%\video-analysis\`, or the folder in `VIDEO_ANALYSIS_HOME`;
the `home` of `check_environment.py --paths` gives its concrete path. `cache\<slug>-<fp8>\` (subtitles, frames, batches and the
webcam timeline, reused across runs) · `runs\<YYYYMMDD-HHMMSS>-<slug>\` (combined `relatorio.md`, with 2+ videos; per
video, `v1\relatorio.md`, `v2\…`, with ledger, prompts and batch reports) · `requests\` · `roles\` ·
`logs\`. Formats and paths: [`reference/contracts.md`](reference/contracts.md). Errors and warnings:
[`reference/troubleshooting.md`](reference/troubleshooting.md).
