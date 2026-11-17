# `/video-analysis setup`: prepare the machine

This is the procedure for the `setup` command. **No video is analysed in this call.** The goal is for
`check_environment.py` to exit with code 0 ("Ready."). The setup can run as many times as needed:
whatever is already in place comes out `[OK]` and is skipped.

**On macOS, every `python` in this file is `python3`**, and the paths under `%USERPROFILE%\` are under `~/`
(`~/video-analysis/`, `~/whisperx-env/`…). Only Apple silicon transcribes (step 6).

**What code 0 means:** Python 3.10 or newer, `ffmpeg` and `ffprobe` on PATH, numpy and Pillow installed,
the central folder (`%USERPROFILE%\video-analysis\`, or the folder in `VIDEO_ANALYSIS_HOME`) accepts
writes, the permission rules cover that folder, and the transcription environment works, with its models
downloaded. The verifier prints the concrete paths of this machine (`home` and `claude_home` in
`--json`). With that, the Workflow's agents read frames and write reports without asking for permission
for each file, and a video without subtitles is transcribed on the machine.

**The installed agents and workflow never come out `[MISSING]`, only `[WARN]`.** Without the copies in
`~/.claude/agents/`, or with copies different from the skill's, the analysis runs in `inline` mode,
which works the same and spends a little more per agent. Without the workflow saved in
`~/.claude/workflows/`, or with a copy different from the skill's, the skill passes the whole script to
the Workflow on every run, which costs ~13k more tokens.

**Separating the speakers and the DeepFace environment are optional: `[OK]` or `[WARN]`, never
`[MISSING]`.** Without a Hugging Face token (step 7), a transcribed video gets subtitles that do not say who
speaks. Without DeepFace (step 8), `prepare.py` does not leave each webcam out of the comparison only while
it is on screen, and uses the automatic mask only. Both block nothing.

What is only confirmed in use:

- the first analysis proves the permission rules: no permission prompt during the run. Start with a
  short one, with `--max-batches 1` or `2`;
- the first analysis in a session opened after the setup proves `agents` mode and the workflow called
  by name (`name: "video-analysis"`).

## What the setup writes, and the one secret

The skill uses **one secret, optional**: the Hugging Face token that separates the speakers, which the
person saves in their own terminal (step 7). Claude never sees it. Everything else works with local files,
`ffmpeg` and Python. Outside the central folder, the setup writes to only these places, and only after the
person's "yes" (the transcription rows with step 6; the token with step 7; DeepFace with step 8):

| what | where | how to undo |
|---|---|---|
| the two agent roles | `%USERPROFILE%\.claude\agents\video-analysis-*.md` | delete the two files |
| the saved workflow | `%USERPROFILE%\.claude\workflows\video-analysis.js` | delete the file |
| the permission rules for the central folder | `%USERPROFILE%\.claude\settings.json` | restore `settings.json.bak-<date>` |
| the transcription environment (WhisperX, PyTorch and NLTK's punkt) | `%USERPROFILE%\whisperx-env\` | delete the folder |
| uv, when the machine has none | the user's Python scripts folder (`pip install --user uv`); on macOS, Homebrew (`brew install uv`, step 1) | `python -m pip uninstall uv`; from Homebrew, `brew uninstall uv` |
| the Python 3.12 of that environment, which uv downloads when the machine has none | the folder that `uv python dir` prints | `uv python uninstall 3.12` |
| the packages uv downloaded | the folder that `uv cache dir` prints | `uv cache clean` |
| the models: Whisper, Portuguese alignment, pyannote | `%USERPROFILE%\.cache\huggingface\hub\` (or under `HF_HOME`) | delete the `models--*` folders (the cache may hold other programs' models) |
| the Hugging Face token (the person saves it) | `%USERPROFILE%\.cache\huggingface\token` | `hf auth logout`, with the `hf` of the environment |
| the DeepFace environment and the YuNet weights | `%USERPROFILE%\deepface-env\` and `%USERPROFILE%\.deepface\weights\face_detection_yunet_2023mar.onnx` | delete the environment folder and the YuNet file (the `weights` folder is DeepFace's and may hold other models) |

These are the defaults (on macOS, `~` instead of `%USERPROFILE%`); `VA_CLAUDE_HOME`, `VA_WHISPERX_ENV`,
`VA_DEEPFACE_ENV`, `HF_HOME` and `DEEPFACE_HOME` move them. When telling the person where something goes,
use the paths the verifier printed, not this table: in `--json`, `claude_home`, the `env` of the
`transcription` item, and the `env` and `weights_path` of the `deepface` item. Without uv on PATH,
`python -m uv` runs the commands of the table.

You also do not accept terms or run an installation that asks for elevation on the person's behalf.

## How to conduct the setup

1. **First check whether Python and ffmpeg are present**, read-only, because the verifier is a Python
   script. On Windows, in PowerShell:
   `Get-Command python, py, ffmpeg, ffprobe, winget -ErrorAction SilentlyContinue | Select-Object Name, Source`.
   A `python` in `...\WindowsApps\` only counts if `python --version` shows the version: without the
   Microsoft Store Python installed, it is only the shortcut that opens the store. On macOS, with Bash:
   `command -v python3 ffmpeg ffprobe brew uv; python3 --version`. Apple's `/usr/bin/python3` (3.9, from the
   Command Line Tools) does not count: the verifier then prints only `video-analysis needs Python 3.10 or
   newer, and this is Python 3.9…` and exits 1. Without a Python 3.10 or newer, start with step 1.
2. **Run the full verifier** and show the result to the person. Use the command in the **Setup** section
   of `SKILL.md`, which already comes with the skill path resolved and matches the pre-approval; run it
   through the Bash tool (`python3` instead of `python` on macOS):

   ```bash
   python "<skill folder>/check_environment.py"
   ```

   - `<skill folder>` is the `${CLAUDE_SKILL_DIR}` of `SKILL.md` (the *base directory* given when the
     skill loads). The verifier sits at the root of the skill, not in `scripts\`.
   - With `--json`, the items come out in machine format.
   - The output marks each item with `[OK]`, `[MISSING]` or `[WARN]`, and each `[MISSING]`/`[WARN]` points
     to the step of this procedure that resolves it. Exit code 0 = "Ready." (a warning does not
     count); 1 = something is missing.
   - `--home` and `--claude-home` (or the `VA_CLAUDE_HOME` variable) point to another central folder or
     another Claude Code folder; they are for testing, not for the normal setup.
3. **Resolve the `[MISSING]` items in step order, one at a time.** The order follows the
   dependencies: each step only needs what the previous ones left ready. Steps 4 and 5 apply even
   when the rest is `[OK]`. Steps 7 and 8 are optional: offer each one once, at the end, when `speakers`
   or `deepface` comes out `[WARN]`.
4. **Each action carries one of two marks:**
   - **you run it**: show the command, say in one sentence what it changes on the machine and run it
     only after the person's "yes";
   - **the person does it**: hand over the step-by-step instructions, wait for them to say they are
     done and run the verifier again.
5. **An item counts as done when the verifier shows `[OK]`**, not when the person says they did it.
6. **Finish** when the verifier exits with code 0. Do not declare the setup ready with code 1.
7. **If the setup started in the middle of an analysis** (`prepare.py` exited with 3, stopped with
   `missing_vtt` for lack of the transcription, or reported `permissions_ok: false`), offer to resume it
   when you finish.

## Step 1 — tools: Python and ffmpeg

**The person does it, in their own terminal.** On the first run, `winget` may ask for the source's terms
to be accepted, and Windows may ask for elevation (UAC). Only the person can accept those. Hand over only
the commands for what came out missing:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg -e
```

- Any Python 3.10 or newer works; whoever already has one does not need to switch. The transcription
  brings its own Python (step 6).
- The `Gyan.FFmpeg` package brings `ffmpeg` and `ffprobe` together.
- Without `winget`, use the official installers: <https://www.python.org/downloads/> and
  <https://www.gyan.dev/ffmpeg/builds/> (adding the `bin` folder to PATH).
- **macOS**, with Homebrew (<https://brew.sh>), which the person installs if it is missing:

  ```bash
  brew install python ffmpeg uv
  ```

  - If `brew` is not found right after installing it (or the verifier says ffmpeg was `found in
    /opt/homebrew/bin, which is not on PATH`), Homebrew is not on PATH yet. The person adds it once and
    opens a new terminal:

    ```zsh
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
    ```

    With it, `python3` is Homebrew's (`python3 --version` 3.12 or newer, `command -v python3` in
    `/opt/homebrew/bin`), not Apple's 3.9. An Intel Homebrew in `/usr/local` on an Apple silicon Mac runs
    under Rosetta: use the one in `/opt/homebrew`.
  - Take ffmpeg from Homebrew. An `ffmpeg` downloaded in a browser is blocked by Gatekeeper ("cannot be
    opened"): the person clears the flag on each binary, `xattr -d com.apple.quarantine <path>/ffmpeg`
    (and `ffprobe`), and puts its folder on PATH.
**After installing**, the person closes and reopens Claude Code and calls `/video-analysis setup` again.
PATH is only reread by a new process: `[MISSING]` right after the installation is usually that, not an
installation failure.

## Step 2 — libraries: numpy and Pillow

**You run it**, after the "yes":

```bash
python -m pip install numpy Pillow
```

If pip refuses because of permissions, repeat with `--user`. The skill does not use OpenCV or any other
dependency. **macOS**: install them into exactly the `python3` that runs the skill, the one the verifier
names in the item (not `brew install numpy pillow`, whose formulae can belong to another Python version).
Homebrew's Python refuses a plain `pip install` ("externally-managed-environment"), so the verifier
prints:

```bash
"<that python3>" -m pip install --user --break-system-packages numpy Pillow
```

`--user` leaves Homebrew's own packages alone. The alternative it also prints is a virtual environment,
`"<that python3>" -m venv ~/.venvs/video-analysis`, then `~/.venvs/video-analysis/bin/python -m pip install
numpy Pillow`, with `~/.venvs/video-analysis/bin` first on PATH so that `python3` is that one.

## Step 3 — central folder

The central folder (`%USERPROFILE%\video-analysis\`, or the folder in `VIDEO_ANALYSIS_HOME`) and its
subfolders (`cache`, `runs`, `requests`, `roles`, `logs`) are created on first use. The permission rules and `request.md` point to it: do not move it. The
`pedidos` subfolder of the earlier version is no longer used; if it is empty, the person may delete it.

If the item comes out `[MISSING]`, the folder exists but does not accept writes. **The person does it:**
they check whether there is a file, and not a folder, with that name; whether the disk has space; and
whether an antivirus blocks writing. Then run the verifier again.

## Step 4 — agents: install the roles and the workflow

**You run it**, after the "yes":

```bash
python "<skill folder>/check_environment.py" --install-agents
```

- Creates `%USERPROFILE%\.claude\agents\`, if it does not exist, and copies `video-analysis-curator.md`
  and `video-analysis-analyst.md` there, from the skill's `agents\` folder.
- Creates `%USERPROFILE%\.claude\workflows\`, if it does not exist, and copies `video-analysis.js`
  there, from the skill's `workflow\` folder. It is the saved workflow that step 6 of `SKILL.md` calls by
  name, without passing the script inline.
- If the workflow changed, the verifier warns (`[WARN]`): this session may keep the previous version in
  memory, so in this conversation step 6 of `SKILL.md` does not call it by name; in a new conversation,
  it does.

Run it again whenever the skill is updated: the verifier warns when the installed copies differ from the
skill's. In the meantime, `prepare.py` uses `inline` mode and reports `env.workflow_current: false`, and
the skill passes the script inline to the Workflow instead of calling the old copy by name.

## Step 5 — permissions: read and write in the central folder without asking

Without these rules, every frame an agent opens and every report it writes asks for permission: hundreds
of prompts per run.

1. **Show the snippet to the person** before writing, with the rules the verifier printed for this
   machine: the `rules` of the `permissions` item in `--json` (when the item is `[MISSING]`, the text
   output also lists them after `--apply-permissions merges:`). For the default central folder of a user whose folder is
   `C:\Users\<user>`, they are:

   ```json
   "permissions": { "allow": [
     "Read(~/video-analysis/**)", "Edit(~/video-analysis/**)", "Write(~/video-analysis/**)",
     "Read(//c/Users/<user>/video-analysis/**)", "Edit(//c/Users/<user>/video-analysis/**)", "Write(//c/Users/<user>/video-analysis/**)"
   ]}
   ```

   - The rules apply only to the central folder: nothing outside it becomes allowed.
   - The two spellings (`~/…` and `//c/…`) stay until the first analysis shows which one Windows
     accepts; after that, the one that does not work can be removed. On macOS a central folder under the
     home gets only `~/…`; one outside it (an external disk, `VIDEO_ANALYSIS_HOME=/Volumes/SSD/video-analysis`)
     gets the absolute spelling, `Read(//Volumes/SSD/video-analysis/**)` and so on (one `/` would be
     relative to the settings file).
   - A central folder no rule can name (a network path) makes `--apply-permissions` come out `[MISSING]`
     with `no permission rule can name …`: the person adds the rules by hand or moves the central folder to
     a local disk.
2. **You run it**, only after the "yes":

   ```bash
   python "<skill folder>/check_environment.py" --apply-permissions
   ```

   The verifier makes a backup at `settings.json.bak-<date>` and merges the rules into `settings.json`,
   without touching `enabledPlugins` or anything else.

## Step 6 — local transcription (WhisperX)

With it, a video without a `.vtt` of the same name is transcribed on this machine before the analysis:
the skill asks how many people speak, WhisperX transcribes the speech, times each word and, with step 7,
separates the speakers, and the skill saves `<name of the video>.vtt` next to the video. Without it, every
video needs its `.vtt`, as in the earlier version.

**Say what it costs** before the "yes":

- a Python environment of its own, `%USERPROFILE%\whisperx-env\` (`~/whisperx-env` on macOS), with
  whisperx 3.8.6, CTranslate2 4.9.0 and PyTorch 2.8 (3.7 GB with the Windows CPU build), plus the models,
  which the proof downloads:
  the default Whisper model of the machine (`large-v3-turbo`, 1.6 GB, on a CPU and on a Mac; `large-v3`,
  about 3 GB, on an NVIDIA GPU with 8 GB or more) and the Portuguese alignment model (1.2 GB). On a machine
  without NVIDIA, about 6.5 GB in all, and 9 minutes on the connection where it was measured; with NVIDIA,
  more (the CUDA build of PyTorch and `large-v3`);
- then each video without subtitles is transcribed once, before its first analysis. On a CPU it is slow:
  on the 4-core 2017 laptop where it was measured, 2.7 times the length of the video (25 min for a 9-min
  meeting: the speech 1.2×, the times of the words 0.4×, the speakers 1.0×); a newer CPU is faster, and an
  NVIDIA GPU should take minutes per hour (not measured). On a Mac the estimate uses an Apple silicon
  profile that is a guess (`TRANSCRIBE_DEFAULTS["cost"]["apple"]`) until it is calibrated on one. The
  estimate of each analysis gives the figure, and the result stays in the video's cache and next to the
  video.

**You run it**, after the "yes", always with `run_in_background` (wait for the notification):

```bash
python "<skill folder>/check_environment.py" --install-transcription
```

- Finds **uv** (on PATH, `python -m uv`, or its usual folders) and, without it, installs it with
  `python -m pip install --user uv`. uv creates the environment with its own **Python 3.12**: WhisperX does
  not run on Python 3.14, and uv downloads that Python when the machine lacks it. On macOS, if pip refuses
  ("externally-managed-environment"), `brew install uv` (step 1) and run it again.
- Installs whisperx 3.8.6, CTranslate2 4.9.0 (pinned; an environment with another version gets it
  reinstalled, and until then the verifier shows the item as `[WARN]`, `with CTranslate2 X instead of
  4.9.0`) and the PyTorch build of the machine: CUDA with an NVIDIA GPU whose driver is recent enough
  (`cu128` from driver 570, `cu126` from 528, read from `nvidia-smi`), CPU otherwise, and PyPI's own build
  on a Mac (CPU and MPS). A GPU older than Volta (compute capability below 7.0: GTX 9xx and 10xx)
  transcribes on the CPU: the CUDA builds and Whisper's float16 do not support it. On Windows and Linux the
  variable `VA_TORCH_BACKEND` forces one (`cpu`, `cu126`, `cu128`…); on macOS it is ignored.
- Downloads NLTK's punkt, which the alignment uses, straight from NLTK's repository into the environment
  (NLTK's own downloader fails behind some proxies).
- Then the **proof**: `scripts/transcribe.py --check` loads the default Whisper model and the Portuguese
  alignment model and, with the token of step 7, pyannote, downloading them on the way. A proof that cannot
  import whisperx reinstalls it once.
- A part already in place is skipped, and a download that failed resumes when it runs again: running it
  again repairs the environment. Other languages download their alignment model at first use.
- **Mac**: Apple silicon only (PyTorch 2.8 has no build for Intel Macs, so the verifier says "not
  supported" there, and every video needs its `.vtt`). uv creates the environment with the arm64 Python by
  name (`cpython-3.12-macos-aarch64-none`), even from an Intel uv. An environment that is x86_64 anyway
  (made under Rosetta) comes out `[MISSING]` with `is an x86_64 environment`, and `--install-transcription`
  refuses it: the person deletes that folder and runs step 6 again with the arm64 Homebrew
  (`/opt/homebrew`).
  - **What runs where.** The speech (Whisper, CTranslate2) runs on the CPU in `int8`, with one thread per
    performance core; the word times and the speakers run on the GPU through MPS when PyTorch has it, and
    a phase that fails there is retried once on the CPU. The proof's JSON (`transcribe.py --check`, last
    line) has `torch_device` (`mps` or `cpu`) and `mps` (whether PyTorch sees it).
  - **Experimental: the speech on the GPU too.** `VA_ASR_DEVICE=mps` runs the Whisper ASR on CTranslate2
    4.9.0's Metal backend (`float16`); if the model does not load there, runs out of memory or fails in
    any other way, the run falls back to `cpu`/`int8` with a warning. It applies only on macOS, and only to runs that see the
    variable. For Claude Code, the person adds it to the `env` block of `~/.claude/settings.json`
    (`"env": {"VA_ASR_DEVICE": "mps"}`) and reopens Claude Code, or runs `export VA_ASR_DEVICE=mps`
    before starting `claude` in that terminal. With it set, the proof of step 6 loads the model on Metal
    too (its JSON's `device`: `mps`, or `cpu` after the fallback). To go back, remove it
    from the `env` block (or `unset VA_ASR_DEVICE`) and reopen Claude Code.

If it fails, the item's `detail` says where; the most common causes (proxy, SSL, a download that stalls)
are in [`reference/troubleshooting.md`](reference/troubleshooting.md), "Local transcription", and those
of a Mac (`CERTIFICATE_VERIFY_FAILED` with python.org's Python, an x86_64 environment) in its "macOS"
section.

## Step 7 — optional: separating the speakers (Hugging Face token)

pyannote's speaker model (`pyannote/speaker-diarization-community-1`) is free, but gated: Hugging Face
only serves it to an account that accepted its conditions, through a token. Without it, the transcription
works, and its subtitles do not say who speaks. **Offer it once**, when `speakers` comes out `[WARN]`.

**The person does it** (Claude never sees the token):

1. Create a free account at <https://huggingface.co/join>, if they have none, and a token of the **Read**
   type at <https://huggingface.co/settings/tokens>.
2. Signed in with the same account, open
   <https://huggingface.co/pyannote/speaker-diarization-community-1> and accept the conditions.
3. In their own terminal (PowerShell on Windows, Terminal on macOS), save the token with the command that
   the verifier printed in the item (`login_command` in `--json`). By default, on Windows:

   ```powershell
   & "$env:USERPROFILE\whisperx-env\Scripts\hf.exe" auth login
   ```

   On macOS: `~/whisperx-env/bin/hf auth login`. Paste the token when asked (it does not show while
   typed), and answer no if asked whether to add it as a git credential.

Then **you run** step 6 again (`--install-transcription`, in the background): it downloads the pyannote
model and proves it loads, and the verifier shows `speakers` `[OK]`. `no_access` in the item means the
token works, but its account did not accept the conditions of step 2.

A video transcribed before the token keeps its transcript: the next analysis of it redoes only the
speakers (on a CPU, about the length of the video), not the speech, and replaces the `.vtt` the skill
wrote, unless the person edited it.

## Step 8 — optional: the DeepFace environment (webcam timeline)

In a call whose layout changes (the camera alone, then a screen share, then the gallery), a webcam makes
frames that are only someone moving, and a fixed region to ignore would also hide the shared screen
where the webcam used to be. With this environment, `prepare.py` finds the webcams with DeepFace before
extracting the frames and leaves each one out of the comparison only while it is on screen (the webcam
timeline; `--webcam-timeline` in [`SKILL.md`](SKILL.md)).

**Offer it once**, when `deepface` comes out `[WARN]`, saying what it costs:

- the installation puts DeepFace 0.0.104, ONNX Runtime and OpenCV (without TensorFlow) in
  `%USERPROFILE%\deepface-env\`, a Python environment separate from the skill's, which takes about
  350 MB on disk;
- then each new video gets a probe of up to 60 frames, about a minute at 1080p. When a face shows up,
  the full scan takes about 24 minutes of CPU per hour of 1080p video (34 with the machine busy), once
  per video: the timeline stays in the video's cache. Both run before the extraction and are counted in
  the estimate.

**You run it**, after the "yes". pip may take several minutes: use `timeout` 600000, or
`run_in_background` and wait for the notification.

```bash
python "<skill folder>/check_environment.py" --install-deepface
```

- Creates `%USERPROFILE%\deepface-env\` with `python -m venv`, installs `deepface==0.0.104` in it and
  downloads the weights of the YuNet face detector (about 230 KB) to `%USERPROFILE%\.deepface\weights\` (or under `DEEPFACE_HOME`),
  checked by their SHA-256, so that an analysis needs no network. A part already in place is skipped,
  and damaged weights are downloaded again. Then DeepFace runs a detection, always: if it fails, deepface
  is reinstalled with its dependencies (pip again, several minutes) and the detection runs once more.
  Running it again therefore repairs an environment that does not work, not only an incomplete one.
- `prepare.py` runs only this version of deepface, with these weights: another version or damaged
  weights come out `[WARN]` here and make the analyses skip the timeline until the repair.
- If the person declines, leave it: the `[WARN]` stays and blocks nothing. With the environment, an
  analysis can still skip the timeline with `--webcam-timeline off`; to remove it, delete the
  environment folder (`env`) and the YuNet file (`weights_path`) that the verifier printed.

## When you finish

Run the verifier one last time and show the result. Remind the person:

- **A new agent in `~/.claude/agents` is only loaded when Claude Code opens.** After installing, close
  and reopen Claude Code; until then the skill runs in `inline` mode, which works the same, spending a
  little more per agent.
- The workflow saved in `~/.claude/workflows` may need the same: if the Workflow says it does not know
  the name `video-analysis`, close and reopen Claude Code. Until then the skill passes the script inline,
  which works the same and costs ~13k more tokens per run.
- If the permission prompts continue after the rules are written, close and reopen Claude Code.
- With the transcription environment, a video without subtitles is transcribed before its first
  analysis, after the skill asks how many people speak; the estimate says how long it takes.
- With the DeepFace environment installed, the next analysis of each video starts with its webcam
  timeline; the estimate says how long it may take.
- If there was an analysis before the setup, offer to resume it.
