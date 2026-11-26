# Problems: symptom, cause and what to do

`prepare.py` writes its progress to `<home>/logs/` (`<home>` is the central folder, `~/video-analysis` by
default; its concrete path is the `home` of the `prepare.py` JSON) and its state to
`runs/<run_id>/status.json`. Its errors come out as `{"ok": false, "error": {"code", "detail"}}`, with exit
code `2` (input), `3` (environment), `4` (a video failed), `130` (interrupted) or `1` (internal error);
the table of the `error.code` values is in [`contracts.md`](contracts.md), section 2. An error in the
prepare renames the request to `request.error.md` (`--estimate` does not touch it). The commands cited here
are those of `SKILL.md`, with the same pre-approved prefix: `python` on Windows, `python3` on macOS and
Linux.

## Preparation (`prepare.py`)

### `.vtt` subtitles not found (exit code 2)

- **Cause:** the rule is strict: the subtitles have **the same name as the video**, only with the `.vtt`
  extension, in the same folder. Without them, the skill transcribes the video on the machine; it stops
  with `missing_vtt` only when it cannot (no transcription environment, or a machine without support) or
  when a similar `.vtt` is in the folder and nobody chose yet ("Local transcription", below). The `detail`
  lists the candidates in the folder (names that are a prefix of the video name or similar to it) and
  suggests a `Rename-Item` on Windows, or an `mv -n` on macOS and Linux. Nothing is renamed on its own.
- **Teams pattern:** the recording comes out as `<Meeting>-YYYYMMDD_HHMMSS-Gravação de Reunião.mp4` (or
  `…-Meeting Recording.mp4`), and the downloaded transcript as `<Meeting>.vtt`. Example, in PowerShell:

  ```powershell
  Rename-Item -LiteralPath 'C:\…\Indicador PES.vtt' -NewName 'Indicador PES-20260610_150844-Gravação de Reunião.vtt'
  ```

  And in a macOS or Linux terminal:

  ```bash
  mv -n '/Users/<user>/…/Indicador PES.vtt' '/Users/<user>/…/Indicador PES-20260610_150844-Gravação de Reunião.vtt'
  ```

- **What to do:** show the candidates and the suggested command (also in `missing_vtt`). Rename only after
  the user's "yes" (the answer `Usar a legenda existente` of the form in step 4 of `SKILL.md` is one), or let
  the user run it. Then repeat: if the error came from
  the prepare, write the request again (the previous one became `request.error.md`); if it came from
  `--estimate`, `request.md` is still there.
  - Rename the `.vtt`, not the video: a renamed video gets another cache key and is extracted again.
  - In PowerShell, use `-LiteralPath`: with `-Path`, brackets in the name (`[PES] …`) become wildcards.
  - In PowerShell, `’` and `‘` also close a single-quoted string: inside it, double them (`’’`).
  - With `mv`, `-n` never replaces an existing file; inside single quotes, a `'` of the name is written
    `'\''` (the suggestion already comes that way).
  - With a single video, the alternative is to point to the subtitles: `--vtt "<path of the .vtt>"` in
    the prepare command.
  - In a folder with videos without subtitles, `--skip-missing` in the prepare command skips those videos.

### Video not found (exit code 2)

- **Cause:** a wrong path, or one cut wrongly when separating it from the objective (a folder with spaces,
  without quotes). Accents, `’`, names coming from a Mac, double spaces and brackets are already handled by
  `prepare.py`. Files whose name starts with `._`, the metadata that macOS leaves on external and network
  drives, are ignored, as videos and as subtitles.
- **What to do:** confirm the path with the user and write the request again. The search in a folder does
  not go into subfolders: list each subfolder on its own line of `# videos`.

### macOS: "Operation not permitted", or a folder that cannot be read

- **Cause:** the privacy protection of macOS. The Desktop, Documents and Downloads folders, iCloud Drive,
  external drives and network volumes can only be read by an app the person allowed: the terminal, or the
  Claude app, that runs Claude Code. Without that permission, reading the folder fails with "Operation not
  permitted", and `prepare.py` says that it cannot read the folder.
- **What to do:** the person allows the app in System Settings → Privacy & Security → Files & Folders (or
  Full Disk Access) and reopens it; or moves the videos to a folder outside the protected ones, such as a
  new folder in the home. Then repeat the call.

### Invalid request or option (exit code 2)

- **Cause:** `invalid_request` (a missing or repeated section, text before `# videos`, an option line
  without `:`), `missing_objective`, `invalid_option` (an unknown key or a value outside the accepted ones)
  or `invalid_usage` (command options that do not go together). The `detail` says which.
- **What to do:** fix it and write the request again, in the format of [`contracts.md`](contracts.md),
  section 1.

### ffmpeg, ffprobe, Python, numpy or Pillow missing or too old (exit code 3)

- **What to do:** `/video-analysis setup`. Right after installing something, the new PATH only applies in
  a reopened Claude Code. A version older than the minimums (Python 3.10, ffmpeg 5.1, Pillow 9.1) counts
  as missing: "Python, ffmpeg or Pillow older than the skill needs", under "Setup", below.
  `workspace_not_writable` is the central folder without write access (step 3 of the setup);
  `incomplete_install` is the skill folder without the whole of `scripts/va`: reinstall the skill.
  `missing_dependency` starting with `webcam_timeline: on` is the DeepFace environment, which only `on`
  requires: step 8 of the setup creates it, or run without the option (`auto` goes on without it).
  `missing_dependency` starting with `the transcription environment does not work` is the WhisperX
  environment, needed by a video without subtitles: step 6 of the setup repairs it.

### A video failed (exit code 4)

- **Cause:** a corrupted file, one without a video track, or one still being downloaded or recorded. The
  reason is in `errors` and in the log. A `detail` that starts with `transcription:` is the local
  transcription of that video ("Local transcription", below).
- **What to do:** say which video and why, in one sentence, and ask whether to go on with the others.
  Their run is ready: `run.json` written and `workflow_args` in the JSON (step 6 of `SKILL.md`, with that
  JSON). The request became `request.error.md`, with a copy in the run folder.

### Internal error (exit code 1)

- **Cause:** a bug in `prepare.py` (`internal_error`). The traceback is in the log in `logs/`.
- **What to do:** give the `detail` and the path of the log in one sentence; do not try to work around it
  with another command or with a script of your own.

### Interrupted (exit code 130), or cut off by the 600 s limit of Bash

- **Cause:** Ctrl+C, Claude Code closed, a background task stopped, or a long extraction running in the
  foreground. On macOS and Linux, the end of a task (`SIGTERM`) or of its terminal (`SIGHUP`) counts as a
  Ctrl+C: the prepare writes its JSON and releases its locks.
- **What to do:** run it again, in the background (`run_in_background`). Completed stages stay in the
  cache; an interrupted frame extraction starts over from scratch, because there is no resume point in
  the middle of it. At 1080p60, count on ~0.15× the duration of the video; for Teams at 1080p, ~0.06×. An
  interrupted transcription resumes at its first missing block of ~10 min.

### The last line of stdout did not come through whole

- **What to do:** read `<run_dir>/prepare.json`, which has the same JSON. The `run_dir` is the first line
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
- **A lock the system refuses:** on Windows, a lock file that cannot be created for a moment (an
  antivirus, the indexer) is tried again. On macOS and Linux, that refusal is a real lack of permission,
  and the prepare stops at once and says so: the central folder or its cache belongs to another user, as
  a folder made with `sudo` belongs to root.

### Strange characters in the output, `python` opens the Microsoft Store, or `python` is not found

- **Cause:** a command run through PowerShell, which re-decodes the output as cp1252; `python3` on
  Windows, which there is the Store shortcut; or `python` on macOS and Linux, where it may not exist
  (Debian, Ubuntu and macOS have only `python3`) or may be another Python.
- **What to do:** always the Bash tool, with `python` on Windows and `python3` on macOS and Linux,
  exactly as in `SKILL.md`.

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

## Setup (`check_environment.py`)

### Python, ffmpeg or Pillow older than the skill needs

- **Symptom:** the verifier shows `python`, `ffmpeg`, `ffprobe` or `pillow` as `[MISSING]` although it is
  installed, and `prepare.py` stops with exit code 3.
- **Cause:** the minimums are Python 3.10, ffmpeg 5.1 and Pillow 9.1. Older ones come with Apple's own
  `/usr/bin/python3` (3.9), with apt on Ubuntu 22.04 (ffmpeg 4.4, Pillow 9.0) and with older releases
  (Debian 11: Python 3.9 and ffmpeg 4.3; Ubuntu 20.04: Python 3.8). An ffmpeg older than 5.1 lacks an
  option the extraction uses. An ffmpeg build without a version number, compiled from source, passes.
- **What to do:** step 1 of the setup, for this system: on macOS, Homebrew's Python (next entries); on
  Ubuntu 22.04, an ffmpeg from outside apt and Pillow from pip, with the minimum version in the command
  (steps 1 and 2: without it, pip keeps apt's 9.0); on an older release, a newer one, whose `python3` is
  3.10 or newer.

### ffmpeg without full H.264 (Fedora's `ffmpeg-free`)

- **Symptom:** on Fedora, a `.mp4` from Teams, Zoom or a screen recorder fails to decode or decodes with
  errors (exit code 4, with the ffmpeg error in the log), or the synthetic test videos are skipped for
  lack of libx264.
- **Cause:** Fedora's own `ffmpeg-free` leaves out the patented codecs: FFmpeg's own H.264 decoder, which
  most recordings need, and x264. Cisco's OpenH264 stands in for the decoder when installed, slower, and
  it may not decode every recording.
- **What to do:** step 1 of the setup, Fedora: the full ffmpeg of RPM Fusion, in place of `ffmpeg-free`.

### pip refuses: `externally-managed-environment`

- **Symptom:** `python3 -m pip install …` stops with `externally-managed-environment`: in step 2 of the
  setup (numpy and Pillow), or in step 6, when the setup would install uv with pip.
- **Cause:** PEP 668. That Python belongs to the system or to Homebrew (Debian 12, Ubuntu 23.04 and newer,
  Arch, Homebrew's Python…), which refuse pip outside a virtual environment.
- **What to do:** never `--break-system-packages`. numpy and Pillow come from the system's packages
  (step 1 of the setup: `brew install numpy pillow`, `python3-numpy python3-pil` with apt,
  `python3-numpy python3-pillow` with dnf, `python-numpy python-pillow` with pacman). For uv, the setup
  stops before creating anything and says how to install it on this system (uv's installer,
  `brew install uv`, `sudo dnf install uv` or `sudo pacman -S uv`); then `--install-transcription` again. The
  transcription and DeepFace environments are virtual environments of their own: PEP 668 does not touch
  what goes inside them.

### The DeepFace environment is not created: `python3-venv` is missing (Debian, Ubuntu)

- **Cause:** Debian and Ubuntu ship `ensurepip`, which `python3 -m venv` needs to put pip in the new
  environment, in a separate package, `python3-venv`. Without it, `python3 -m venv` starts, then fails
  and leaves a half-made folder.
- **What to do:** the person runs `sudo apt install python3-venv`, then step 8 of the setup again. The
  setup checks for it before creating the environment, and when creating it fails halfway, it removes what
  that run left in `~/deepface-env`, so nothing half made stays. The transcription environment does not
  need it: uv creates that one by itself.

### macOS: `python3` asks for the Command Line Tools, or is Python 3.9

- **Cause:** `/usr/bin/python3` is Apple's. Without the Command Line Tools, it only opens a dialog that
  offers to install them; with them, it is Python 3.9, older than the skill needs: the verifier shows
  `python` as `[MISSING]`, and `prepare.py` stops with exit code 3.
- **What to do:** step 1 of the setup, macOS: Homebrew's Python (`brew install python`; the Homebrew
  installer brings the Command Line Tools), with Homebrew's folder ahead of `/usr/bin` on the PATH, or the
  installer of python.org. Any `python3` other than `/usr/bin/python3` whose `python3 --version` shows
  3.10 or newer counts: `/opt/homebrew/bin/python3` (Apple silicon), `/usr/local/bin/python3` (Intel) or
  python.org's, in `/Library/Frameworks/Python.framework/…`. Installing the Command Line Tools alone does
  not help: their Python is the 3.9.

### macOS: Homebrew's `python3` or `brew` not found

- **Symptom:** although `brew install` worked in the person's terminal, `command -v python3` in Claude Code
  still shows `/usr/bin/python3` (the verifier then shows `python` as `[MISSING]`, at 3.9), or
  `command -v brew` finds nothing.
- **Cause:** on Apple silicon, Homebrew installs in `/opt/homebrew/bin` and puts that folder on the PATH
  through the shell profile (`~/.zprofile`). Claude Code opened before that line existed, or opened from
  the Dock without reading the profile, has a shorter PATH. The skill finds ffmpeg, ffprobe and uv in
  Homebrew's folders by itself, but `python3` always comes from the PATH. (On Intel, Homebrew's
  `/usr/local/bin` is already ahead of `/usr/bin`.)
- **What to do:** check that `~/.zprofile` has the line the Homebrew installer printed
  (`eval "$(/opt/homebrew/bin/brew shellenv)"`), then close and reopen Claude Code.

### Intel Mac: the transcription is not supported

- **Cause:** WhisperX 3.8.6 needs PyTorch 2.8, which has no build for Intel Macs. The verifier shows the
  transcription as a `[WARN]` there, so the setup ends ("Ready.") without it, and a video without its
  `.vtt` stops with `missing_vtt` and `transcription.state: unsupported`.
- **What to do:** a `.vtt` for each video: the Teams or Zoom transcript renamed to the video's name ("`.vtt`
  subtitles not found", above), or subtitles made on another machine. The rest of the skill works.

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
  `cache/<slug>-<fp8>/webcam-<h8>/`. `skipped` (in `warnings`): the DeepFace environment exists but is
  not the setup's: a package missing, a broken Python, another deepface version, or the YuNet weights
  missing or damaged.
- **What to do:** nothing for this run: the frames were extracted without the timeline, as before the
  option. `/video-analysis setup` checks the environment with a detection, and its step 8
  (`--install-deepface`) repairs it: it installs what is missing, downloads damaged weights again and,
  when the detection fails, reinstalls deepface. The same goes for a `failed` whose report points to
  DeepFace or its libraries (an `ImportError`, a DLL, `.so` or `.dylib` that does not load). A failed
  timeline is not kept: the next run of the video tries again; `--webcam-timeline off` skips it.

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
  video, as in the earlier version ("Intel Mac", above).

### `speakers not separated` warning

- **Cause:** the text after the colon says which: `no Hugging Face token` (step 7 of the setup was not
  done), `the token has no access to pyannote/speaker-diarization-community-1` (a token is saved, but its
  account did not accept the model's conditions) or `the diarization failed (…)`.
- **What to do:** for the first two, step 7 of the setup, which ends with `--install-transcription` again.
  Then call the analysis again with `num_speakers`: the speech comes from the cache, and only the speakers
  are redone (on a CPU, about the length of the video). A video transcribed without a token is also redone at its next
  analysis, once the token is there (the skill asks how many people speak). For a failure, the warning
  brings the end of the error, and `transcribe.log` (in `cache/<slug>-<fp8>/transcript-<a8>/`) the whole
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
  installs the build that matches the driver (a driver older than 528, or 525 on Linux, has none: update it).

### Out of memory (`out of memory even with the smallest batch`, exit code 4)

- **Cause:** the GPU has less free memory than the model needs, even with batch 1 and `int8_float16`;
  another program (a game, another model) may be holding it. Without a GPU, a machine short of RAM can
  have the transcription killed by the system (on Linux, the out-of-memory killer): the `detail` then says
  that the process was killed by a signal.
- **What to do:** close what uses the GPU or the memory, or `whisper_model: large-v3-turbo` (or `medium`).

### `the WhisperX environment does not load` (exit code 3 or 4)

- **Cause:** the environment was changed by hand, a package went missing, or an update broke a native
  library (a DLL, `.so` or `.dylib`).
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
- **uv not installed**, with `externally-managed-environment`: "pip refuses", under "Setup", above.

### The transcription runs on the CPU although the machine has a GPU

- **Cause:** an NVIDIA GPU older than Volta (compute capability below 7.0: GTX 9xx, GTX 10xx, Quadro P).
  The PyTorch 2.8 CUDA 12.8 build has no code for it and Whisper's `float16` needs 7.0, so the setup
  installs the CPU build and the transcription uses `large-v3-turbo` on the CPU. The driver being too old
  (below 528; on Linux, 525) has the same effect. An AMD or Intel GPU, and the Mac's GPU, are never used.
- **What to do:** nothing; it works, only slower. Forcing a CUDA build (`VA_TORCH_BACKEND=cu126`) does not
  make these cards transcribe. An environment installed on such a card before this check is reinstalled
  with the CPU build by `/video-analysis setup` (step 6).

### The first transcription on a recent NVIDIA GPU spends minutes before starting

- **Cause:** the GPU code is compiled once for a card newer than the build (JIT).
- **What to do:** wait. The skill keeps a 4 GB compilation cache (`CUDA_CACHE_MAXSIZE`), so the next
  transcriptions start quickly.

### `torchcodec` warning in `transcribe.log`

- **What to do:** nothing. pyannote imports torchcodec, which looks for FFmpeg's shared libraries (DLLs,
  `.so` or `.dylib`); the skill passes the audio in memory and never uses it.

### The transcribed subtitles came out in the wrong language

- **Cause:** a call that starts in another language, or little speech; the warning `language detected with
  low confidence` comes with it when the probability was below 0.6.
- **What to do:** a new run with `audio_language: pt` (or the right code): the video is transcribed again.

### To transcribe a video again from scratch

- Delete the `.vtt` next to the video, only if the skill made it (its `NOTE` starts with `Legenda gerada
  automaticamente pela skill video-analysis`), and the folders `cache/<slug>-<fp8>/transcript-*`: the
  next analysis transcribes it again. Another `whisper_model` or `audio_language` does the same without
  deleting anything. A `.vtt` the person wrote or edited is never replaced: on one the skill made, the
  warning `… was made by the skill but changed since: kept as it is` says so; delete or rename it.

## Workflow and agents

### Permission prompt for every frame or report

- **Cause:** the permission rules for the central folder are missing, their spelling did not match, or
  Claude Code has not reread `settings.json` yet. `prepare.py` flags it with `env.permissions_ok: false`.
- **What to do:** `/video-analysis setup` and, afterwards, a reopened Claude Code. If it continues with the
  rules written, it is the spelling. On Windows, both forms (`~/…` and `//c/…`) stay until the first
  analysis shows which one works; on macOS and Linux, see the next entry.

### Permission rules on macOS and Linux

- **Cause:** on macOS and Linux, the verifier writes one spelling: `~/<folder>/**` for a central folder
  inside the home (the default, `~/video-analysis`), and `//<absolute path>/**` for one outside it
  (`VIDEO_ANALYSIS_HOME=/data/video-analysis` gives `Read(//data/video-analysis/**)`, and the same for
  `Edit` and `Write`). Claude Code reads `//` as the root of the disk; with a single `/`, a rule is relative
  to the folder of `settings.json` and does not cover the central folder.
- **What to do:** `--apply-permissions` (step 5 of the setup) writes the right spelling. Rules written by
  hand count when they match the `rules` the verifier prints (`--json`), or name an ancestor folder with
  `/**`. Then reopen Claude Code.

### Permission prompt for `prepare.py` itself or for the verifier

- **Cause:** the command did not match the skill's pre-approval: PowerShell instead of Bash, `python3` on
  Windows or `python` on macOS and Linux, another path, or the way `${CLAUDE_SKILL_DIR}` is substituted on
  Windows.
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
  `~/.claude/workflows/video-analysis.js`, which the setup installs (step 4) and which may only show up in
  a reopened Claude Code.
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
  workflow as `different` (`prepare.py`, with `env.workflow_current: false`), run the setup (step 4) and,
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

- **Cause:** almost always systemic: permission, unreadable role in `roles/`, quota.
- **What to do:** fix the cause (setup, quota) and `/video-analysis resume`: nothing finished is redone.

### Run interrupted: Claude Code closed, quota, error

- **What to do:** `/video-analysis resume`, for the most recent one, or `resume <run_id>`. Resuming
  works from the files: it restarts at the 1st batch without a report (`reports/batch_NNN.md`). If the
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

- **Cause:** the id does not exist in `runs/` (or is a prefix of more than one run), that run's prepare
  failed before writing `run.json`, or `run.json`/`objective.md` are missing from the run folder.
- **What to do:** check the id with the user (the runs are in `<home>/runs/`); a
  run without `run.json` cannot be resumed: run the analysis again, and the cache is reused.

### `resume` fails with "the subtitles changed since the run" (`video_failed`)

- **Cause:** the cache was deleted (`--gc`) and the `.vtt` next to the video is no longer the one the run
  used (edited, or replaced by a later run with another `num_speakers`, model or language), with no
  `legenda.vtt` of the run holding the old bytes. Rebuilding would mix the reports already written with
  other speech or speakers.
- **What to do:** a new run with the subtitles as they are now; the frames come from the cache.
