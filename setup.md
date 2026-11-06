# `/video-analysis setup`: prepare the machine

This is the procedure for the `setup` command. **No video is analysed in this call.** The goal is for
`check_environment.py` to exit with code 0 ("Ready."). The setup can run as many times as needed:
whatever is already in place comes out `[OK]` and is skipped.

**What code 0 means:** Python 3.10 or newer, `ffmpeg` and `ffprobe` on PATH, numpy and Pillow installed,
the central folder (`%USERPROFILE%\video-analysis\`, or the folder in `VIDEO_ANALYSIS_HOME`) accepts
writes, and the permission rules cover that folder. The verifier prints the concrete paths of this
machine (`home` and `claude_home` in `--json`). With that, the Workflow's agents read frames and write reports without asking for permission for
each file.

**The installed agents and workflow never come out `[MISSING]`, only `[WARN]`.** Without the copies in
`~/.claude/agents/`, or with copies different from the skill's, the analysis runs in `inline` mode,
which works the same and spends a little more per agent. Without the workflow saved in
`~/.claude/workflows/`, or with a copy different from the skill's, the skill passes the whole script to
the Workflow on every run, which costs ~13k more tokens.

**The DeepFace environment is optional: `[OK]` or `[WARN]`, never `[MISSING]`.** With it, `prepare.py`
leaves each webcam out of the comparison only while it is on screen (step 6). Without it, the skill
works the same, with the automatic mask only.

What is only confirmed in use:

- the first analysis proves the permission rules: no permission prompt during the run. Start with a
  short one, with `--max-batches 1` or `2`;
- the first analysis in a session opened after the setup proves `agents` mode and the workflow called
  by name (`name: "video-analysis"`).

## What the setup does not ask for

The skill uses **no secrets** and no login: it works with local files, `ffmpeg` and Python. Outside the
central folder, the setup writes to only these places, and only after the person's "yes" (the last row
only if the person accepts the optional step 6):

| what | where | how to undo |
|---|---|---|
| the two agent roles | `%USERPROFILE%\.claude\agents\video-analysis-*.md` | delete the two files |
| the saved workflow | `%USERPROFILE%\.claude\workflows\video-analysis.js` | delete the file |
| the permission rules for the central folder | `%USERPROFILE%\.claude\settings.json` | restore `settings.json.bak-<date>` |
| the DeepFace environment and the YuNet weights | `%USERPROFILE%\deepface-env\` and `%USERPROFILE%\.deepface\weights\face_detection_yunet_2023mar.onnx` | delete the environment folder and the YuNet file (the `weights` folder is DeepFace's and may hold other models) |

These are the defaults; `VA_CLAUDE_HOME`, `VA_DEEPFACE_ENV` and `DEEPFACE_HOME` move them. When telling
the person where something goes, use the paths the verifier printed, not this table: in `--json`,
`claude_home`, and the `env` and `weights_path` of the `deepface` item.

You also do not accept terms or run an installation that asks for elevation on the person's behalf.

## How to conduct the setup

1. **First check whether Python and ffmpeg are present**, read-only, because the verifier is a Python
   script. In PowerShell:
   `Get-Command python, py, ffmpeg, ffprobe, winget -ErrorAction SilentlyContinue | Select-Object Name, Source`.
   A `python` in `...\WindowsApps\` only counts if `python --version` shows the version: without the
   Microsoft Store Python installed, it is only the shortcut that opens the store. Without Python, start
   with step 1.
2. **Run the full verifier** and show the result to the person. Use the command in the **Setup** section
   of `SKILL.md`, which already comes with the skill path resolved and matches the pre-approval; run it
   through the Bash tool:

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
   when the rest is `[OK]`. Step 6 is optional: offer it once, at the end, when `deepface` comes out
   `[WARN]`.
4. **Each action carries one of two marks:**
   - **you run it**: show the command, say in one sentence what it changes on the machine and run it
     only after the person's "yes";
   - **the person does it**: hand over the step-by-step instructions, wait for them to say they are
     done and run the verifier again.
5. **An item counts as done when the verifier shows `[OK]`**, not when the person says they did it.
6. **Finish** when the verifier exits with code 0. Do not declare the setup ready with code 1.
7. **If the setup started in the middle of an analysis** (`prepare.py` exited with 3, or reported
   `permissions_ok: false`), offer to resume it when you finish.

## Step 1 — tools: Python and ffmpeg

**The person does it, in their own terminal.** On the first run, `winget` may ask for the source's terms
to be accepted, and Windows may ask for elevation (UAC). Only the person can accept those. Hand over only
the commands for what came out missing:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg -e
```

- Any Python 3.10 or newer works; whoever already has one does not need to switch.
- The `Gyan.FFmpeg` package brings `ffmpeg` and `ffprobe` together.
- Without `winget`, use the official installers: <https://www.python.org/downloads/> and
  <https://www.gyan.dev/ffmpeg/builds/> (adding the `bin` folder to PATH).

**After installing**, the person closes and reopens Claude Code and calls `/video-analysis setup` again.
PATH is only reread by a new process: `[MISSING]` right after the installation is usually that, not an
installation failure.

## Step 2 — libraries: numpy and Pillow

**You run it**, after the "yes":

```bash
python -m pip install numpy Pillow
```

If pip refuses because of permissions, repeat with `--user`. The skill does not use OpenCV or any other
dependency.

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
     accepts; after that, the one that does not work can be removed.
2. **You run it**, only after the "yes":

   ```bash
   python "<skill folder>/check_environment.py" --apply-permissions
   ```

   The verifier makes a backup at `settings.json.bak-<date>` and merges the rules into `settings.json`,
   without touching `enabledPlugins` or anything else.

## Step 6 — optional: the DeepFace environment (webcam timeline)

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
- With the DeepFace environment installed, the next analysis of each video starts with its webcam
  timeline; the estimate says how long it may take.
- If there was an analysis before the setup, offer to resume it.
