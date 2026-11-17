"""Checks whether this machine is ready for the video-analysis skill (procedure in setup.md).

Read-only, except with the four action options, which the setup runs after the person's "yes":

    python check_environment.py                          # full check
    python check_environment.py --json                   # same thing, in machine format
    python check_environment.py --paths                  # only {"home", "claude_home"} in JSON: no check, nothing created
    python check_environment.py --install-agents         # copies the 2 roles to ~/.claude/agents and the workflow to ~/.claude/workflows
    python check_environment.py --apply-permissions      # backup of settings.json and merge of the 6 Read/Edit/Write rules
    python check_environment.py --install-transcription  # the WhisperX environment of the videos without subtitles (GBs)
    python check_environment.py --install-deepface       # optional: the DeepFace environment of the webcam timeline (about 350 MB)
    python check_environment.py --home FOLDER            # another central folder (default: VIDEO_ANALYSIS_HOME or %USERPROFILE%\\video-analysis)
    python check_environment.py --claude-home FOLDER     # another Claude Code folder (default: VA_CLAUDE_HOME or %USERPROFILE%\\.claude)

On macOS the command is ``python3``. The names of before the rename still work: verificar_ambiente.py (it
runs this file), --instalar-agentes and --aplicar-permissoes.

Items: python (3.10+), ffmpeg, ffprobe, numpy, pillow, central-folder, permissions and transcription come
out [OK] or [MISSING]; agents and workflow come out [OK] or [WARN] (without the copies, the skill runs in
inline mode and passes the script inline to the Workflow); speakers comes out [OK] or [WARN] (without a
Hugging Face token with access to the pyannote model, the subtitles the skill makes do not separate the
speakers); deepface comes out [OK] or [WARN] (optional: without it, the skill does not mask the webcam over
time; [OK] only when DeepFace, in the version the setup installs and with intact YuNet weights, runs a
detection, a few seconds). Each [MISSING]/[WARN] points to the setup.md step that resolves it. When
--install-agents changes the saved workflow, a [WARN] (in --json, the action's note field) reminds that the
open Claude Code session may keep the previous version in memory.

--install-transcription creates %USERPROFILE%\\whisperx-env (or the folder in VA_WHISPERX_ENV) with uv and its
own Python 3.12, installs whisperx 3.8.6 and the PyTorch build of this machine (CUDA by the NVIDIA driver,
else CPU), downloads NLTK's punkt into it and proves it all with scripts/transcribe.py --check, which
downloads the Whisper model, the Portuguese alignment model and, with a token, pyannote's (to the Hugging Face
cache). It skips what is already there. It takes several GB and can take tens of minutes.

--install-deepface creates %USERPROFILE%\\deepface-env (or the folder in VA_DEEPFACE_ENV) with
``python -m venv``, installs deepface==0.0.104 in it and downloads the YuNet weights to
%USERPROFILE%\\.deepface\\weights (or DEEPFACE_HOME), skipping what is already there (damaged weights are
downloaded again). Then DeepFace runs a detection, always; if it fails, deepface is reinstalled with its
dependencies and the detection runs again. The environment takes about 350 MB on disk, and pip can take
several minutes.

Exit code 0 = ready (a warning does not count), 1 = something is missing.
"""
import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

SKILL = Path(__file__).resolve().parent
if str(SKILL / "scripts") not in sys.path:
    sys.path.insert(0, str(SKILL / "scripts"))

import va  # noqa: E402  (its __init__ parses on old Pythons; the rest of the skill needs 3.10)

if va.old_python():   # Apple's /usr/bin/python3 is 3.9: one clear line instead of a SyntaxError in an import
    print(va.old_python())
    sys.exit(1)

from va import environment  # noqa: E402
from va.common import as_posix, setup_console, workspace_home  # noqa: E402

STEPS = {
    "python": "1-tools", "ffmpeg": "1-tools", "ffprobe": "1-tools",
    "numpy": "2-libraries", "pillow": "2-libraries",
    "central-folder": "3-central folder",
    "agents": "4-agents", "install-agents": "4-agents",
    "workflow": "4-agents", "install-workflow": "4-agents",
    "permissions": "5-permissions", "apply-permissions": "5-permissions",
    "transcription": "6-transcription", "install-transcription": "6-transcription",
    "speakers": "7-speakers",
    "deepface": "8-webcam timeline", "install-deepface": "8-webcam timeline",
}
MAC = sys.platform == "darwin"
HINT_FFMPEG = " (brew install ffmpeg)" if MAC else " (winget install --id Gyan.FFmpeg -e)"


def libs_hint(mac: bool = MAC, exe: str | None = None) -> str:
    """How to install numpy and Pillow into exactly the Python being checked. On macOS not ``brew install numpy
    pillow``: those formulae can belong to another Python version than the python3 that runs the skill;
    ``--break-system-packages`` because Homebrew's Python refuses pip otherwise (PEP 668), ``--user`` so
    nothing of Homebrew's own is touched."""
    exe = exe or sys.executable or "python3"
    if not mac:
        return " (python -m pip install numpy Pillow)"
    return (f' (for this Python, {exe}: "{exe}" -m pip install --user --break-system-packages numpy Pillow; or a '
            f'virtual environment: "{exe}" -m venv ~/.venvs/video-analysis, then ~/.venvs/video-analysis/bin/python '
            "-m pip install numpy Pillow, with ~/.venvs/video-analysis/bin first on PATH)")


HINT_LIBS = libs_hint()
LABEL = {"ok": "[OK]     ", "missing": "[MISSING]", "warn": "[WARN]   "}
# The Workflow keeps in memory, per session, the saved workflow it has already read: once the file is
# replaced, the name keeps running the previous version until Claude Code reopens (step 6 of SKILL.md).
NOTE_WORKFLOW = ("this Claude Code session may keep the previous version of the workflow in memory (or not "
                 "even know the name): close and reopen Claude Code; until then, the skill passes the script "
                 "inline or in scriptPath, without using the name (step 6 of SKILL.md)")


def item(ident: str, state: str, detail: str, **extra) -> dict:
    return {"id": ident, "state": state, "detail": detail, "step": STEPS.get(ident), **extra}


def line(state: str, ident: str, detail: str, step: str | None) -> str:
    suffix = ""
    if state in ("missing", "warn") and step:
        number, name = step.split("-", 1)
        suffix = f"  -> setup.md, step {number} ({name})"
    return f"{LABEL[state]} {ident:<18} {detail}{suffix}"


def action_install(chome: Path) -> dict:
    try:
        r = environment.install_agents(SKILL, chome)
    except OSError as exc:
        return item("install-agents", "missing", f"could not copy the roles to {as_posix(chome / 'agents')}: {exc}")
    if r["copied"]:
        txt = (f"{len(r['copied'])} role(s) copied to {r['dir']} ({', '.join(r['copied'])}); "
               "close and reopen Claude Code to load them (until then the skill runs in inline mode)")
    else:
        txt = f"the roles were already identical in {r['dir']}"
    return item("install-agents", "ok", txt, copied=r["copied"], unchanged=r["unchanged"])


def action_install_workflow(chome: Path) -> dict:
    try:
        r = environment.install_workflow(SKILL, chome)
    except OSError as exc:
        return item("install-workflow", "missing",
                    f"could not copy the workflow to {as_posix(chome / 'workflows')}: {exc}")
    if r["copied"]:
        txt = f"workflow copied to {r['path']}; in a new session, the Workflow calls it by the name video-analysis"
    else:
        txt = f"the workflow was already identical in {r['path']}"
    return item("install-workflow", "ok", txt, path=r["path"], copied=r["copied"],
                note=NOTE_WORKFLOW if r["copied"] else None)


def action_permissions(home: Path, chome: Path) -> dict:
    rules = environment.workspace_rules(home)
    target = chome / "settings.json"
    if not rules:   # nothing merged would read as "already in place" while the prompts go on
        return item("apply-permissions", "missing",
                    f"no permission rule can name {as_posix(home)} (a network path?): add Read, Edit and Write rules "
                    f"for it to {as_posix(target)} by hand, or use a central folder on a local disk", rules=rules)
    try:
        r = environment.merge_permissions(target, rules)
    except (environment.SetupError, OSError) as exc:
        return item("apply-permissions", "missing", str(exc), rules=rules)
    if r["changed"]:
        bak = f"; backup at {r['backup']}" if r["backup"] else " (new file)"
        txt = (f"{len(r['added'])} rule(s) added to {r['path']}{bak}; "
               "if the permission prompts continue, close and reopen Claude Code")
    else:
        txt = f"the rules were already in {r['path']}; nothing was written"
    return item("apply-permissions", "ok", txt, added=r["added"], backup=r["backup"], rules=rules)


def action_install_deepface() -> dict:
    try:
        r = environment.install_deepface()
    except (environment.SetupError, OSError) as exc:
        return item("install-deepface", "missing", str(exc))
    st = r["status"]
    if not st["ok"]:
        return item("install-deepface", "missing", f"{st['detail']} after the installation", done=r["done"])
    txt = ("; ".join(r["done"]) if r["done"] else "already in place") + f"; DeepFace ran a detection: {st['detail']}"
    return item("install-deepface", "ok", txt, done=r["done"], env=r["env"])


def action_install_transcription() -> dict:
    try:
        r = environment.install_transcription()
    except (environment.SetupError, OSError) as exc:
        return item("install-transcription", "missing", str(exc))
    st, proof = r["status"], r.get("check") or {}
    txt = ("; ".join(r["done"]) if r["done"] else "already in place") + f": {st['detail']}"
    diar = proof.get("diarization")
    note, note_step = None, None
    if diar in ("no_token", "no_access"):
        note_step = STEPS["speakers"]       # the token is step 7, not this one
    if diar and diar != "ok":
        note = {"no_token": "no Hugging Face token yet: the person saves it (setup.md, step 7), then this runs again",
                "no_access": f"the token has no access to {environment.DIARIZE_PAGE}: accept its conditions with the "
                             "same account (setup.md, step 7), then this runs again"}.get(
            diar, f"pyannote did not load ({diar}): the subtitles will not separate the speakers")
    return item("install-transcription", "ok", txt, done=r["done"], env=r["env"], check=proof, note=note,
                note_step=note_step)


def transcription_item() -> dict:
    """The transcription environment: [OK] when it works and the setup already downloaded the default Whisper
    model and the Portuguese alignment model ([WARN] when its CTranslate2 is not the pinned one); [MISSING]
    otherwise (without it, every video needs its .vtt)."""
    st = environment.transcription_status()
    keys = ("env", "python", "python_version", "version", "expected", "torch", "ctranslate2", "backend", "device",
            "gpu", "default_model", "token", "models", "nltk_data", "login_command")
    extra = {k: st.get(k) for k in keys}
    if st["state"] == "unsupported":
        return item("transcription", "missing", st["detail"], **extra), st
    if st["state"] == "absent":
        return item("transcription", "missing", f"{st['detail']}; without it, every video needs a .vtt of the same "
                    "name (--install-transcription creates it: about 6.5 GB)", **extra), st
    if st.get("problem") == "architecture":   # the detail says what to do: --install-transcription refuses it
        return item("transcription", "missing", st["detail"], **extra), st
    if not st["ok"]:
        return item("transcription", "missing", f"{st['detail']} (--install-transcription repairs it)", **extra), st
    if not st["ready"]:
        return item("transcription", "missing", f"{st['detail']}, but the models are not downloaded yet "
                    "(--install-transcription downloads them)", **extra), st
    pin, ct2 = va.TRANSCRIBE_DEFAULTS["ctranslate2_version"], st.get("ctranslate2")
    if ct2 and ct2 != pin:   # it works, but not with the CTranslate2 the setup pins (one of before the pin)
        return item("transcription", "warn", f"{st['detail']}, with CTranslate2 {ct2} instead of {pin} "
                    "(--install-transcription repairs it)", **extra), st
    return item("transcription", "ok", f"{st['detail']}: videos without subtitles are transcribed here", **extra), st


def speakers_item(st: dict) -> dict:
    """Separating the speakers: [OK] with a Hugging Face token that reaches the pyannote model (in the cache);
    [WARN] otherwise: the subtitles come out without speaker labels, which blocks nothing."""
    ds = environment.diarization_status(st)
    extra = {k: ds.get(k) for k in ("model", "page", "login_command", "cached", "access")}
    extra["diarization"] = ds["state"]
    if ds["ok"]:
        return item("speakers", "ok", ds["detail"], **extra)
    if ds["state"] == "unavailable":
        return item("speakers", "warn", ds["detail"], **extra)
    todo = {"no_token": f"; the person creates a Read token, accepts the conditions at {ds['page']} and runs "
                        f"{ds['login_command']} in their own terminal",
            "no_access": "",
            "download": " (--install-transcription downloads it)"}.get(ds["state"], "")
    return item("speakers", "warn", ds["detail"] + todo, **extra)


def deepface_item() -> dict:
    """The optional DeepFace environment: [OK] when it has the version the setup installs and intact YuNet
    weights, and runs a detection (a few seconds); [WARN] otherwise (it never makes the setup fail)."""
    df = environment.deepface_status(deep=True)
    extra = {k: df[k] for k in ("env", "python", "version", "expected", "weights", "weights_path")}
    if df["state"] == "absent":
        return item("deepface", "warn", f"optional: {df['detail']}; without it, the skill does not mask the webcam "
                                        "over time (--install-deepface creates it, about 350 MB)", **extra)
    if not df["ok"]:
        return item("deepface", "warn", f"{df['detail']}; until it is repaired, the skill does not mask the webcam "
                                        "over time (--install-deepface repairs it)", **extra)
    return item("deepface", "ok", f"{df['detail']}, and it ran a detection: the skill masks the webcam over time",
                **extra)


def checks(home: Path, chome: Path) -> list[dict]:
    items = []
    py = environment.python_status()
    items.append(item("python", "ok" if py["ok"] else "missing", py["detail"]))
    for name in ("ffmpeg", "ffprobe"):
        st = environment.tool_status(name)
        hint = "" if st["ok"] else HINT_FFMPEG
        items.append(item(name, "ok" if st["ok"] else "missing", st["detail"] + hint))
    for name in ("numpy", "pillow"):
        st = environment.module_status(name)
        hint = "" if st["ok"] else libs_hint()
        items.append(item(name, "ok" if st["ok"] else "missing", st["detail"] + hint))
    ws = environment.workspace_status(home, create=True)
    items.append(item("central-folder", "ok" if ws["ok"] else "missing", ws["detail"]))
    perm = environment.permissions_status(home, chome)
    rules = environment.workspace_rules(home)
    detail = perm["detail"]
    if not perm["ok"] and not perm["blocked"] and rules:
        detail += " (--apply-permissions merges: " + ", ".join(rules) + ")"
    items.append(item("permissions", "ok" if perm["ok"] else "missing", detail, rules=rules,
                      covered=perm["covered"], blocked=perm["blocked"]))
    ag = environment.agents_status(SKILL, chome)
    det = ag["detail"] if ag["current"] else ag["detail"] + " (--install-agents copies them; without them the skill runs in inline mode)"
    items.append(item("agents", "ok" if ag["current"] else "warn", det, files=ag["files"]))
    wf = environment.workflow_status(SKILL, chome)
    det = wf["detail"] if wf["current"] else wf["detail"] + (
        " (--install-agents copies it; without it the skill passes the script inline to the Workflow, spending more tokens)")
    items.append(item("workflow", "ok" if wf["current"] else "warn", det, path=wf["path"], copy=wf["state"]))
    tx, st = transcription_item()
    items.append(tx)
    items.append(speakers_item(st))
    items.append(deepface_item())
    return items


def main(argv: list[str] | None = None) -> int:
    setup_console()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="output in JSON")
    ap.add_argument("--paths", action="store_true",
                    help="only print the resolved central folder and Claude Code folder, in JSON, and exit 0: "
                         "no check (no DeepFace detection) and nothing created; the action options are refused")
    ap.add_argument("--install-agents", "--instalar-agentes", dest="install_agents", action="store_true",
                    help="copies the 2 roles to <claude home>/agents and the workflow to <claude home>/workflows")
    ap.add_argument("--apply-permissions", "--aplicar-permissoes", dest="apply_permissions", action="store_true",
                    help="backup of settings.json and merge of the Read/Edit/Write rules of the central folder")
    ap.add_argument("--install-transcription", dest="install_transcription", action="store_true",
                    help="creates or repairs the WhisperX environment of the videos without subtitles (uv, Python 3.12, "
                         "whisperx 3.8.6, the PyTorch build of this machine, the models; several GB)")
    ap.add_argument("--install-deepface", dest="install_deepface", action="store_true",
                    help="optional: creates the DeepFace environment of the webcam timeline (python -m venv, "
                         "deepface==0.0.104, YuNet weights; about 350 MB)")
    ap.add_argument("--home", help="central folder (default: VIDEO_ANALYSIS_HOME or %%USERPROFILE%%\\video-analysis)")
    ap.add_argument("--claude-home", help="Claude Code folder (default: VA_CLAUDE_HOME or %%USERPROFILE%%\\.claude)")
    a = ap.parse_args(argv)
    home = workspace_home(a.home)
    chome = environment.claude_home(a.claude_home)
    if a.paths:
        if a.install_agents or a.apply_permissions or a.install_deepface or a.install_transcription:
            ap.error("--paths only prints the folders: run the action options without it")
        print(json.dumps({"home": as_posix(home), "claude_home": as_posix(chome)}, ensure_ascii=False))
        return 0

    actions = []
    if a.install_agents:
        actions.append(action_install(chome))
        actions.append(action_install_workflow(chome))
    if a.apply_permissions:
        actions.append(action_permissions(home, chome))
    if a.install_transcription:
        actions.append(action_install_transcription())
    if a.install_deepface:
        actions.append(action_install_deepface())
    items = checks(home, chome)
    missing = [r for r in actions + items if r["state"] == "missing"]

    if a.json:
        print(json.dumps({"ready": not missing, "home": as_posix(home), "claude_home": as_posix(chome),
                          "actions": actions, "items": items}, ensure_ascii=False, indent=2))
    else:
        for r in actions + items:
            print(line(r["state"], r["id"], r["detail"], r["step"]))
            if r.get("note"):
                print(line("warn", r["id"], r["note"], r.get("note_step") or r["step"]))
        print()
        print("Ready." if not missing else f"{len(missing)} item(s) missing: run /video-analysis setup.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
