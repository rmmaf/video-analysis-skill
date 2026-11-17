# Problems: symptom, cause and what to do

`prepare.py` writes its progress to `<home>\logs\` (`<home>` is the central folder,
`%USERPROFILE%\video-analysis` by default, `~/video-analysis` on macOS; its concrete path is the `home` of the `prepare.py` JSON) and
its state to `runs\<run_id>\status.json`. Its errors come out as `{"ok": false, "error": {"code", "detail"}}`, with exit
code `2` (input), `3` (environment), `4` (a video failed), `130` (interrupted) or `1` (internal error);
the table of the `error.code` values is in [`contracts.md`](contracts.md), section 2. An error in the
prepare renames the request to `request.error.md` (`--estimate` does not touch it). The commands cited here
are those of `SKILL.md`, with the same pre-approved prefix.

## Preparation (`prepare.py`)

### `.vtt` subtitles not found (exit code 2)

- **Cause:** the rule is strict: the subtitles have **the same name as the video**, only with the `.vtt`
  extension, in the same folder. Without them, the skill transcribes the video on the machine; it stops
  with `missing_vtt` only when it cannot (no transcription environment, or a machine without support) or
  when a similar `.vtt` is in the folder and nobody chose yet ("Local transcription", below). The `detail`
  lists the candidates in the folder (names that are a prefix of the video name or similar to it) and
  suggests a `Rename-Item` (an `mv -n` on macOS). Nothing is renamed on its own.
- **Teams pattern:** the recording comes out as `<Meeting>-YYYYMMDD_HHMMSS-Gravação de Reunião.mp4` (or
  `…-Meeting Recording.mp4`), and the downloaded transcript as `<Meeting>.vtt`. Example:

  ```powershell
  Rename-Item -LiteralPath 'C:\…\Indicador PES.vtt' -NewName 'Indicador PES-20260610_150844-Gravação de Reunião.vtt'
  ```

- **What to do:** show the candidates and the suggested command (also in `missing_vtt`). Rename only after
  the user's "yes" (the answer `Usar a legenda existente` of the form in step 4 of `SKILL.md` is one), or let
  the user run it. Then repeat: if the error came from
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
  skill. `missing_dependency` starting with `webcam_timeline: on` is the DeepFace environment, which only
  `on` requires: step 8 of the setup creates it, or run without the option (`auto` goes on without it).
  `missing_dependency` starting with `the transcription environment does not work` is the WhisperX
  environment, needed by a video without subtitles: step 6 of the setup repairs it. `missing_dependency`
  starting with `video-analysis needs Python 3.10 or newer` is the `python3` that ran it (Apple's
  `/usr/bin/python3` is 3.9): "macOS", below.

### A video failed (exit code 4)

- **Cause:** a corrupted file, one without a video track, or one still being downloaded or recorded. The
  reason is in `errors` and in the log. A `detail` that starts with `transcription:` is the local
  transcription of that video ("Local transcription", below).
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
  the middle of it. At 1080p60, count on ~0.15× the duration of the video; for Teams at 1080p, ~0.06×. An
  interrupted transcription resumes at its first missing block of ~10 min.

### The last line of stdout did not come through whole

- **What to do:** read `<run_dir>\prepare.json`, which has the same JSON. The `run_dir` is the first line
  of stderr (`run_dir: …`).

### "File has not been read yet" when writing `request.md`

- **Cause:** a `request.md` was left over, for example after an `--estimate` not followed by the prepare.
- **What to do:** read the file and write it again.

### The prepare seems stuck right at the start

- **Cause:** another `prepare.py` is using the same video (cache lock). It waits up to 30 min (4 h for
  the webcam timeline, 6 h for the transcription); a lock with no sign of life for 10 min is discarded,
  like the one a `prepare.py` killed in the task manager leaves (the `webcam_mask.py` or `transcribe.py`
  it ran stops with it). Or a video is being transcribed (the log says `local transcription with
  WhisperX`), on a CPU for 2 to 3 times the length of the video. Or the webcam timeline is being made: with the
  DeepFace environment, a video without one in the cache gets it before the extraction (the log says
  `webcam timeline with DeepFace`), up to about 24 min per hour of 1080p video when a face shows up.
- **What to do:** wait, or check whether there is another run of the same video in progress. The
  estimate already counts the timeline (`videos[].webcam_timeline.estimate_s`); `--webcam-timeline off`
  skips it in a call.

### Strange characters in the output, or `python` opens the Microsoft Store

- **Cause:** a command run through PowerShell, which re-decodes the output as cp1252, or with `python3`,
  which on Windows is the Store shortcut.
- **What to do:** always the Bash tool, with `python` on Windows (`python3` on macOS, where `python` may
  not exist), exactly as in `SKILL.md`.

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
- **Webcam in only part of the call** (camera alone at the start, a screen share later): a fixed region
  also hides the shared content in that area. With the DeepFace environment (step 8 of the setup), the
  skill already masks each webcam only while it is on screen (`webcam_timeline`); without it, use
  `ignore_timeline` with the file of `scripts/webcam_mask.py --timeline` (region over time, in
  [`parameters.md`](parameters.md)). A webcam that shows up only briefly can escape the probe:
  `--webcam-timeline on` runs the full scan anyway.

### `webcam timeline failed` or `webcam timeline skipped` warning

- **Cause:** `failed` (in the video's warnings): `webcam_mask.py` stopped with an error; the end of its
  report comes in the warning, and the whole of it in `webcam_mask.log`, in
  `cache\<slug>-<fp8>\webcam-<h8>\`. `skipped` (in `warnings`): the DeepFace environment exists but is
  not the setup's: a package missing, a broken Python, another deepface version, or the YuNet weights
  missing or damaged.
- **What to do:** nothing for this run: the frames were extracted without the timeline, as before the
  option. `/video-analysis setup` checks the environment with a detection, and its step 8
  (`--install-deepface`) repairs it: it installs what is missing, downloads damaged weights again and,
  when the detection fails, reinstalls deepface. The same goes for a `failed` whose report points to
  DeepFace or its libraries (an `ImportError`, a DLL that does not load). A failed timeline is not kept:
  the next run of the video tries again; `--webcam-timeline off` skips it.

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

## Local transcription

### A video without subtitles stops with `missing_vtt` instead of being transcribed

- **Cause:** by `missing_vtt[].transcription.state`: `absent`, the transcription environment was never
  installed; `unsupported`, the machine cannot run it (an Intel Mac, which has no PyTorch 2.8); `ok`, the
  folder has a similar `.vtt` (a Teams transcript under the meeting's name) and the call gave no
  `num_speakers` for that video, so the person chooses.
- **What to do:** `absent` → `/video-analysis setup`, step 6; `ok` → the form of step 4 of `SKILL.md`: use
  the similar `.vtt`, renamed, or give `num_speakers` to transcribe; `unsupported` → the `.vtt` of each
  video, as in the earlier version (previous section).

### `speakers not separated` warning

- **Cause:** the text after the colon says which: `no Hugging Face token` (step 7 of the setup was not
  done), `the token has no access to pyannote/speaker-diarization-community-1` (a token is saved, but its
  account did not accept the model's conditions) or `the diarization failed (…)`.
- **What to do:** for the first two, step 7 of the setup, which ends with `--install-transcription` again.
  Then call the analysis again with `num_speakers`: the speech comes from the cache, and only the speakers
  are redone (on a CPU, about the length of the video). A video transcribed without a token is also redone at its next
  analysis, once the token is there (the skill asks how many people speak). For a failure, the warning
  brings the end of the error, and `transcribe.log` (in `cache\<slug>-<fp8>\transcript-<a8>\`) the whole
  of it: fix its cause (the network or the proxy, such as `HTTPS_PROXY` or `REQUESTS_CA_BUNDLE`; the memory,
  closing what holds it; an `ImportError`, step 6 of the setup), then call the analysis again with the same
  `num_speakers`, and only the speakers are redone. A call without `num_speakers` keeps the subtitles without
  speakers and does not try again.

### The transcription takes too long

- **Cause:** on a CPU, the transcription takes 2 to 3 times the length of the video (measured on a 4-core 2017
  laptop: 2.7; the speech 1.2×, the times of the words 0.4×, the speakers 1.0×), more on a machine busy with
  other work, and the whole video is transcribed, even with `--range`. With an NVIDIA GPU, the environment may have the CPU
  build of PyTorch: the `device` of the `transcription` item says `cpu`.
- **What to do:** let it run in the background: it happens once per video, and the blocks of ~10 min are
  checkpoints, so an interruption loses at most one block. For a quicker and rougher pass,
  `whisper_model: small`. With an NVIDIA GPU and `device: cpu`, run `--install-transcription` again: it
  installs the build that matches the driver (a driver older than 528 has none: update it).

### Out of memory (`out of memory even with the smallest batch`, exit code 4)

- **Cause:** the GPU has less free memory than the model needs, even with batch 1 and `int8_float16`;
  another program (a game, another model) may be holding it.
- **What to do:** close what uses the GPU, or `whisper_model: large-v3-turbo` (or `medium`).

### `the WhisperX environment does not load` (exit code 3 or 4)

- **Cause:** the environment was changed by hand, a package went missing, or an update broke a DLL.
- **What to do:** `/video-analysis setup`: step 6 repairs it, reinstalling whisperx when its import fails.

### The setup's download stalls or fails (proxy, SSL)

- **A download stuck at the same point** (the Hugging Face models): `HF_HUB_DISABLE_XET=1` in the
  environment of Claude Code, then `--install-transcription` again; a download resumes where it stopped.
- **An SSL error from uv** (`invalid peer certificate`, a corporate proxy that inspects TLS):
  `UV_NATIVE_TLS=1`, so that uv uses the system's certificates. For Hugging Face behind such a proxy,
  `REQUESTS_CA_BUNDLE` pointing to the proxy's certificate, and `HTTPS_PROXY` when the proxy is not
  automatic.
- **NLTK's punkt:** the error gives the address of the zip and the folder: the person downloads it in a
  browser and extracts it there, then the setup runs again.

### The transcription runs on the CPU although the machine has an NVIDIA GPU

- **Cause:** the GPU is older than Volta (compute capability below 7.0: GTX 9xx, GTX 10xx, Quadro P). The
  PyTorch 2.8 CUDA 12.8 build has no code for it and Whisper's `float16` needs 7.0, so the setup installs the
  CPU build and the transcription uses `large-v3-turbo` on the CPU. The driver being too old (below 528) has
  the same effect.
- **What to do:** nothing; it works, only slower. Forcing a CUDA build (`VA_TORCH_BACKEND=cu126`) does not
  make these cards transcribe. An environment installed on such a card before this check is reinstalled
  with the CPU build by `/video-analysis setup` (step 6).

### The first transcription on a recent NVIDIA GPU spends minutes before starting

- **Cause:** the GPU code is compiled once for a card newer than the build (JIT).
- **What to do:** wait. The skill keeps a 4 GB compilation cache (`CUDA_CACHE_MAXSIZE`), so the next
  transcriptions start quickly.

### `torchcodec` warning in `transcribe.log`

- **What to do:** nothing. pyannote imports torchcodec, which looks for FFmpeg's DLLs; the skill passes the
  audio in memory and never uses it.

### The transcribed subtitles came out in the wrong language

- **Cause:** a call that starts in another language, or little speech; the warning `language detected with
  low confidence` comes with it when the probability was below 0.6.
- **What to do:** a new run with `audio_language: pt` (or the right code): the video is transcribed again.

### To transcribe a video again from scratch

- Delete the `.vtt` next to the video, only if the skill made it (its `NOTE` starts with `Legenda gerada
  automaticamente pela skill video-analysis`), and the folders `cache\<slug>-<fp8>\transcript-*`: the
  next analysis transcribes it again. Another `whisper_model` or `audio_language` does the same without
  deleting anything. A `.vtt` the person wrote or edited is never replaced: on one the skill made, the
  warning `… was made by the skill but changed since: kept as it is` says so; delete or rename it.

## macOS

What follows applies to a Mac with Apple silicon (an M4 Max, say). Every `python` is `python3` there.

### `video-analysis needs Python 3.10 or newer, and this is Python 3.9…`

- **Cause:** `python3` is Apple's `/usr/bin/python3` (3.9, from the Command Line Tools), because Homebrew's
  is not installed or not first on PATH. The verifier prints this one line and exits 1; `prepare.py`
  exits 3 with `missing_dependency` and `home: null`.
- **What to do:** `brew install python`, Homebrew on PATH (next item), a new terminal, and a reopened
  Claude Code. `command -v python3` should then print `/opt/homebrew/bin/python3`.

### `brew` not found, or ffmpeg `found in /opt/homebrew/bin, which is not on PATH`

- **Cause:** Homebrew was installed, but the shell never ran its `shellenv`, so `/opt/homebrew/bin` is not
  on the PATH that Claude Code's shell gets. The skill finds ffmpeg, ffprobe and uv there anyway (and puts
  that ffmpeg first on the PATH of the transcription, whose audio decoder runs a bare `ffmpeg`), but
  `python3` stays Apple's 3.9.
- **What to do:** the person runs `echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile`, opens
  a new terminal and reopens Claude Code.

### numpy or Pillow `[MISSING]` although they were installed

- **Cause:** they went into another Python: `brew install numpy pillow` (formulae that may belong to
  another Python version), or a `pip install` with a different `python3`.
- **What to do:** the command the verifier prints in the item, which names the Python being checked:
  `"<that python3>" -m pip install --user --break-system-packages numpy Pillow` (Homebrew's Python refuses
  a plain `pip install`), or the virtual environment it suggests (setup step 2).

### `CERTIFICATE_VERIFY_FAILED` when the setup downloads punkt or the YuNet weights

- **Cause:** the python.org Python ships without a CA bundle until its `Install Certificates.command` runs.
  The setup stops at the first certificate error, without retrying, and the detail says what to run.
- **What to do:** run `/Applications/Python 3.X/Install Certificates.command` (the version the detail
  names), or use Homebrew's Python, then the setup again. Behind a proxy that inspects TLS, "The setup's
  download stalls or fails" (above) applies as well.

### The transcription item says `is an x86_64 environment`

- **Cause:** the environment was made by an Intel uv or Python (Homebrew in `/usr/local`, under Rosetta).
  PyTorch 2.8 has no Intel macOS build, and installing into it again cannot change its architecture, so
  `--install-transcription` refuses it.
- **What to do:** delete the folder the detail names (`~/whisperx-env` by default), make sure `uv` and
  `python3` come from `/opt/homebrew` (`file "$(command -v uv)"` says `arm64`), and run step 6 of the setup
  again. The setup asks uv for the arm64 Python by name, so a new environment comes out arm64.

### `alignment on mps failed (…): retrying on the cpu` in `transcribe.log`

- **Cause:** the word times (wav2vec2) or the speakers (pyannote; `diarization on mps failed (…)`) run on
  the GPU through MPS, and an operator MPS lacks, or its memory, made that phase fail. The phase is retried
  once on the CPU: the result is the same, only slower. `asr.json` then has `align_device: "cpu"`, and the
  speakers file `device: "cpu"`. If the CPU fails too, the usual `alignment skipped` or `speakers not
  separated` warning follows.
- **What to do:** nothing: it costs only time. If it happens on every video, the log line names the
  error to report. Whether PyTorch sees MPS at all is the `mps` field of the setup's proof (step 6).

### Trying the speech on the GPU (`VA_ASR_DEVICE=mps`), and going back

- **What it is:** experimental. With `VA_ASR_DEVICE=mps`, Whisper runs on CTranslate2 4.9.0's Metal
  backend (`float16`) instead of the performance cores (`int8`). When the model does not load there, runs
  out of memory at batch 1, or fails in any other way (an `MPSGraph` error, in the speech or in the
  language detection), the run falls back to `cpu`/`int8` with the warning `ASR on mps failed (…):
  cpu/int8`. It applies to `transcribe.py --device auto` (what `prepare.py` uses); an explicit `--device`
  keeps the speech where it says. `asr.json`'s `device` says which one transcribed.
- **To try it:** `"env": {"VA_ASR_DEVICE": "mps"}` in `~/.claude/settings.json`, or `export
  VA_ASR_DEVICE=mps` before starting `claude`; then reopen Claude Code (setup step 6). Only videos
  transcribed from then on use it: the device is not in the cache key, so a video already transcribed is
  reused as it is.
- **To go back:** remove the variable from the `env` block (or `unset VA_ASR_DEVICE`) and reopen Claude
  Code. To transcribe a video again on the CPU, "To transcribe a video again from scratch" (above).

### Files named `._<video>` on an external disk

- **Cause:** macOS writes an AppleDouble companion (`._reuniao.mp4`, `._reuniao.vtt`) next to each file on
  a disk without its metadata (exFAT, FAT, some network shares). They are not media.
- **What to do:** nothing: the folder scan, the subtitle lookup and a glob such as `*.mp4` skip names that
  start with `._`. A `._` file named explicitly, or a pattern that itself starts with `._`, is taken as
  asked, and fails as a video. `dot_clean -m <folder>` removes them.

## Workflow and agents

### Permission prompt for every frame or report

- **Cause:** the permission rules for the central folder are missing, their spelling did not match, or
  Claude Code has not reread `settings.json` yet. `prepare.py` flags it with `env.permissions_ok: false`.
- **What to do:** `/video-analysis setup` and, afterwards, a reopened Claude Code. If it continues with the
  rules written, it is the spelling: both forms (`~/…` and `//c/…`) stay until the first analysis shows
  which one works. On macOS a central folder outside the home (`/Volumes/SSD/video-analysis`) gets
  `//Volumes/SSD/video-analysis/**`, with two slashes; a network path gets no rule, and
  `--apply-permissions` says so (`no permission rule can name …`): add the rules by hand or use a local
  disk.

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
  `%USERPROFILE%\.claude\workflows\video-analysis.js`, which the setup installs (step 4) and which may
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
- **What to do:** check the id with the user (the runs are in `<home>\runs\`); a
  run without `run.json` cannot be resumed: run the analysis again, and the cache is reused.

### `resume` fails with "the subtitles changed since the run" (`video_failed`)

- **Cause:** the cache was deleted (`--gc`) and the `.vtt` next to the video is no longer the one the run
  used (edited, or replaced by a later run with another `num_speakers`, model or language), with no
  `legenda.vtt` of the run holding the old bytes. Rebuilding would mix the reports already written with
  other speech or speakers.
- **What to do:** a new run with the subtitles as they are now; the frames come from the cache.
