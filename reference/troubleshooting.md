# Problems: symptom, cause and what to do

`prepare.py` writes its progress to `C:\Users\digom\video-analysis\logs\` and its state to
`runs\<run_id>\status.json`. Its errors come out as `{"ok": false, "error": {"code", "detail"}}`, with exit
code `2` (input), `3` (environment), `4` (a video failed), `130` (interrupted) or `1` (internal error);
the table of the `error.code` values is in [`contracts.md`](contracts.md), section 2. An error in the
prepare renames the request to `request.error.md` (`--estimate` does not touch it). The commands cited here
are those of `SKILL.md`, with the same pre-approved prefix.

## Preparation (`prepare.py`)

### `.vtt` subtitles not found (exit code 2)

- **Cause:** the rule is strict: the subtitles have **the same name as the video**, only with the `.vtt`
  extension, in the same folder. The `detail` lists the candidates in the folder (names that are a prefix
  of the video name or similar to it) and suggests a `Rename-Item`. Nothing is renamed on its own.
- **Teams pattern:** the recording comes out as `<Meeting>-YYYYMMDD_HHMMSS-Gravação de Reunião.mp4` (or
  `…-Meeting Recording.mp4`), and the downloaded transcript as `<Meeting>.vtt`. Example:

  ```powershell
  Rename-Item -LiteralPath 'C:\…\Indicador PES.vtt' -NewName 'Indicador PES-20260610_150844-Gravação de Reunião.vtt'
  ```

- **What to do:** show the candidates and the suggested command (also in `missing_vtt`). Run it, in
  PowerShell, only after the user's "yes", or let the user run it. Then repeat: if the error came from
  the prepare, write the request again (the previous one became `request.error.md`); if it came from
  `--estimate`, `request.md` is still there.
  - Rename the `.vtt`, not the video: a renamed video gets another cache key and is extracted again.
  - Use `-LiteralPath`: with `-Path`, brackets in the name (`[PES] …`) become wildcards.
  - In PowerShell, `’` and `‘` also close a single-quoted string: inside it, double them (`’’`).
  - With a single video, the alternative is to point to the subtitles: `--vtt "<path of the .vtt>"` in
    the prepare command.
  - In a folder with videos without subtitles, `--skip-missing` in the prepare command skips those videos.

### Video not found (exit code 2)

- **Cause:** a wrong path, or one cut wrongly when separating it from the objective (a folder with spaces,
  without quotes). Accents, `’`, names coming from a Mac, double spaces and brackets are already handled by
  `prepare.py`.
- **What to do:** confirm the path with the user and write the request again. The search in a folder does
  not go into subfolders: list each subfolder on its own line of `# videos`.

### Invalid request or option (exit code 2)

- **Cause:** `invalid_request` (a missing or repeated section, text before `# videos`, an option line
  without `:`), `missing_objective`, `invalid_option` (an unknown key or a value outside the accepted ones)
  or `invalid_usage` (command options that do not go together). The `detail` says which.
- **What to do:** fix it and write the request again, in the format of [`contracts.md`](contracts.md),
  section 1.

### ffmpeg, ffprobe, Python, numpy or Pillow missing (exit code 3)

- **What to do:** `/video-analysis setup`. Right after installing something, the new PATH only applies in
  a reopened Claude Code. `workspace_not_writable` is the central folder without write access (step 3 of
  the setup); `incomplete_install` is the skill folder without the whole of `scripts\va`: reinstall the
  skill.

### A video failed (exit code 4)

- **Cause:** a corrupted file, one without a video track, or one still being downloaded or recorded. The
  reason is in `errors` and in the log.
- **What to do:** say which video and why, in one sentence, and ask whether to go on with the others.
  Their run is ready: `run.json` written and `workflow_args` in the JSON (step 6 of `SKILL.md`, with that
  JSON). The request became `request.error.md`, with a copy in the run folder.

### Internal error (exit code 1)

- **Cause:** a bug in `prepare.py` (`internal_error`). The traceback is in the log in `logs\`.
- **What to do:** give the `detail` and the path of the log in one sentence; do not try to work around it
  with another command or with a script of your own.

### Interrupted (exit code 130), or cut off by the 600 s limit of Bash

- **Cause:** Ctrl+C, Claude Code closed, or a long extraction running in the foreground.
- **What to do:** run it again, in the background (`run_in_background`). Completed stages stay in the
  cache; an interrupted frame extraction starts over from scratch, because there is no resume point in
  the middle of it. At 1080p60, count on ~0.15× the duration of the video; for Teams at 1080p, ~0.06×.

### The last line of stdout did not come through whole

- **What to do:** read `<run_dir>\prepare.json`, which has the same JSON. The `run_dir` is the first line
  of stderr (`run_dir: …`).

### "File has not been read yet" when writing `request.md`

- **Cause:** a `request.md` was left over, for example after an `--estimate` not followed by the prepare.
- **What to do:** read the file and write it again.

### The prepare seems stuck right at the start

- **Cause:** another `prepare.py` is using the same video (cache lock). It waits up to 30 min; a lock with
  no sign of life for 10 min is discarded.
- **What to do:** wait, or check whether there is another run of the same video in progress.

### Strange characters in the output, or `python` opens the Microsoft Store

- **Cause:** a command run through PowerShell, which re-decodes the output as cp1252, or with `python3`,
  which on Windows is the Store shortcut.
- **What to do:** always the Bash tool and always `python`, exactly as in `SKILL.md`.

### More than 80 agents, or "run too large" (more than 800)

- **Cause:** a long video, several videos in the same run, or `--curation batches`, which almost doubles
  the count. Per video, there is 1 agent per batch plus 2 (with `batches`, 2 per batch plus 1); with two or
  more videos, the run has 1 more. A 55 min meeting gives ≈ 50 batches, ≈ 52 agents (≈ 100 with `batches`)
  and hours in series.
- **What to do:** above 80, confirm with the user before launching. Above 800, the prepare exits with
  `run_too_large` and the Workflow refuses (its cap is 1000); `--ignore-agent-limit` only makes the prepare
  accept it. Split into smaller runs, use `--range` on what matters, or `--max-batches` to test the
  objective first; with `--curation batches`, running without it cuts the count almost in half. On resume,
  only `--max-batches` and `--curation plan` change the count: without `--curation`, the run's applies (a
  run older than the option comes back with `batches`).

## Frames and subtitles

### High-density warning: too many frames per minute

- **Cause:** more than 12 frames/min in the video, or more than 30 in any one minute. Usually a region
  that changes by itself escaped the automatic mask: webcam, clock, animation, notification.
- **What to do:** if the warning brings a suggested region to ignore, offer to repeat with
  `--ignore-region x,y,w,h` (in the request, `ignore_region`). Without a suggestion,
  `--sensitivity low`. The subtitles come from the cache; the frames are extracted again. Details in
  [`parameters.md`](parameters.md).

### A small change did not become a frame

- **Cause:** it is on purpose: a digit or up to ~4 characters look like a cursor and count as a duplicate.
  They accumulate against the last kept frame, so a sequence of them ends up showing.
- **What to do:** `--sensitivity high` (more frames, more cost).

### A color-only change does not show

- **Cause:** the comparison is in grayscale: red ↔ green with the same brightness changes nothing. An icon
  that also changes shape is detected.
- **What to do:** nothing in this version; it is a known limitation.

### Camera footage with few frames

- **Cause:** in camera footage everything moves, so the extraction drops to ~2 frames/min and warns.
- **What to do:** nothing: it is expected. The content comes more from the speech.

### A panel that updates by itself dropped out of the comparison

- **Cause:** the hot-region mask caught a long animation of the screen itself; it shows in the warnings.
- **What to do:** manual only: `--hot-mode off` on the prepare command line.

### The subtitles cover less than half of the video, or go past the end

- **Cause:** subtitles of another video, or a Teams transcript that started after the recording.
- **What to do:** check that it is the right `.vtt`. To shift all the cues: `--vtt-offset SEC` on the
  prepare command line (manual use).

## Workflow and agents

### Permission prompt for every frame or report

- **Cause:** the permission rules for the central folder are missing, their spelling did not match, or
  Claude Code has not reread `settings.json` yet. `prepare.py` flags it with `env.permissions_ok: false`.
- **What to do:** `/video-analysis setup` and, afterwards, a reopened Claude Code. If it continues with the
  rules written, it is the spelling: both forms (`~/…` and `//c/…`) stay until the first analysis shows
  which one works.

### Permission prompt for `prepare.py` itself or for the verifier

- **Cause:** the command did not match the skill's pre-approval: PowerShell instead of Bash, `python3`,
  another path, or the way `${CLAUDE_SKILL_DIR}` is substituted on Windows.
- **What to do:** use the command exactly as in `SKILL.md`. If it still asks, the user approves it once,
  with "don't ask again".

### Agent type not found (`video-analysis-curator`, `video-analysis-analyst`)

- **Cause:** the agents were installed after this session opened; a new agent only loads with Claude Code
  reopened.
- **What to do:** `--roles inline` now, or reopen Claude Code. An out-of-date installed copy is already
  downgraded to `inline` by `prepare.py` on its own, with a warning.

### The Workflow tool does not exist in this session

- **What to do:** follow [`without-workflow.md`](without-workflow.md).

### The Workflow refuses the `scriptPath`, or does not know the name `video-analysis`

- **Symptom:** "scriptPath must be a script path this tool returned, or a file you can already read…", or
  the tool says there is no workflow with the name `video-analysis`.
- **Cause:** in `scriptPath`, the tool only accepts the path it returned itself or a file of the session
  folder, never the `script_path` of the skill folder. The name only exists with the workflow saved in
  `C:\Users\digom\.claude\workflows\video-analysis.js`, which the setup installs (step 4) and which may
  only show up in a reopened Claude Code.
- **What to do:** now, call the Workflow with `script` = the content of the `script_path` (read with Read,
  without editing) and the same `args`. It costs ~13k tokens; in the same conversation, a later run can
  pass in `scriptPath` the path the Workflow returned. Afterwards, `/video-analysis setup`; if the name is
  still unknown, reopen Claude Code. With `env.workflow_current: false` in the `prepare.py` JSON (saved
  copy missing or from another version), do the same without trying the name, which would run the old
  script.

### `invalid workflow_args (…)`

- **Cause:** the args were edited or passed as a string, or the workflow saved in
  `~/.claude/workflows/` is from an earlier version of the skill.
- **What to do:** pass `workflow_args` as a JSON object, exactly as `prepare.py` printed it. If the JSON
  was lost, `--resume <run_id>` prints it again. If the args were right and the verifier flags the
  workflow as `diferente` (`prepare.py`, with `env.workflow_current: false`), run the setup (step 4) and,
  until then, pass the script inline (step 6 of `SKILL.md`).

### Agent does not write the report: "Subagents should return findings as text…"

- **Symptom:** an agent's Write fails with "Subagents should return findings as text, not write report
  files. Include this content in your final response instead.", and the final report or the combined one
  does not show up (`final report failed twice`, `synthesis failed twice`).
- **Cause:** Claude Code blocks subagents from writing files named exactly `report.md`, `summary.md` or
  `findings.md`; other names, such as `relatorio.md`, go through. That is why the final report is
  `relatorio.md`, and both roles forbid those three names and require writing exactly to the path in the
  call. With the skill up to date, it only shows up in a run launched with an old copy of the workflow or
  of the roles.
- **What to do:** `/video-analysis setup` (`--install-agents` renews the roles in `~/.claude/agents/`
  and the saved workflow) and `/video-analysis resume`: the finished batches are not redone, only what is
  missing runs, and a `report.md` from an old run counts as a finished report. In the setup conversation,
  launch `resume` without the name (next section).

### The Workflow runs the old version (it tries to write `report.md`, or the return carries `output_tokens`)

- **Cause:** the workflow saved in `~/.claude/workflows/` changed while this session was open, usually
  through the setup's `--install-agents` (the verifier warns about it). The Workflow tool keeps in
  memory, per session, the saved workflow it has already read: until Claude Code is reopened,
  `name: "video-analysis"` keeps running the previous version, even with the new file and
  `env.workflow_current: true`.
- **What to do:** in this conversation, do not use the name. If the Workflow has already returned a script
  path in it, copy the `script_path` over it with Bash (`cp "<script_path>" "<returned path>"`, without
  spending tokens) and call with `scriptPath` = that path and the same `args`; without one, pass `script` =
  the content of the `script_path`, read with Read, without editing (~13k tokens). Or close and reopen
  Claude Code: in a new session, the name reads the new file. The run that the old version left
  incomplete continues with `/video-analysis resume`, launched the same way.

### Batch `partial` or `failed`

- **Cause:** the analyst did not open all the images, not even on the second attempt, or did not write the
  report. The final report lists the gaps.
- **What to do:** if the segment matters, start a run with `--range` covering it; the cache is reused.

### Video `failed`: "failed in 2 batches in a row"

- **Cause:** almost always systemic: permission, unreadable role in `roles\`, quota.
- **What to do:** fix the cause (setup, quota) and `/video-analysis resume`: nothing finished is redone.

### Run interrupted: Claude Code closed, quota, error

- **What to do:** `/video-analysis resume`, for the most recent one, or `resume <run_id>`. Resuming
  works from the files: it restarts at the 1st batch without a report (`reports\batch_NNN.md`). If the
  cache has been deleted (`--gc`), `--resume` rebuilds the stages with the same parameters and may take
  as long as a new extraction.

### Rate limit or quota

- **What to do:** `--parallel 1`, `--analyst-effort medium` (or `low`) and `--max-batches` to test;
  without `--curation batches` (on resume, `--curation plan`).

### "token budget running out" warning

- **Cause:** the conversation's token target (a `+500k`, for example) ran out, and the video stopped
  before a batch.
- **What to do:** `resume` with a larger target, or without one.

### `inline` mode more expensive than expected

- **Cause:** each inline agent loads the default subagent prompt, the session's tool list and the
  folder's CLAUDE.md.
- **What to do:** setup with `--install-agents` and Claude Code reopened, to use the `agents` mode.

### Weak answer, or `answered: no`

- **Cause:** an objective that is vague or too broad.
- **What to do:** rephrase the objective and run again: the extraction comes from the cache, only the
  agents run.

### `resume` does nothing

- **Cause:** the run is already complete, with `relatorio.md` in every video.
- **What to do:** for another question about the same video, a new run; the cache is reused.

### `resume` fails with `run_not_found` or `inconsistent_run` (exit code 2)

- **Cause:** the id does not exist in `runs\` (or is a prefix of more than one run), that run's prepare
  failed before writing `run.json`, or `run.json`/`objective.md` are missing from the run folder.
- **What to do:** check the id with the user (the runs are in `C:\Users\digom\video-analysis\runs\`); a
  run without `run.json` cannot be resumed: run the analysis again, and the cache is reused.
