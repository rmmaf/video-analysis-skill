"""Checks whether this machine is ready for the video-analysis skill (procedure in setup.md).

Read-only, except with the three action options, which the setup runs after the person's "yes":

    python check_environment.py                        # full check
    python check_environment.py --json                 # same thing, in machine format
    python check_environment.py --paths                # only {"home", "claude_home"} in JSON: no check, nothing created
    python check_environment.py --install-agents       # copies the 2 roles to ~/.claude/agents and the workflow to ~/.claude/workflows
    python check_environment.py --apply-permissions    # backup of settings.json and merge of the 6 Read/Edit/Write rules
    python check_environment.py --install-deepface     # optional: the DeepFace environment of the webcam timeline (about 350 MB)
    python check_environment.py --home FOLDER          # another central folder (default: VIDEO_ANALYSIS_HOME or %USERPROFILE%\\video-analysis)
    python check_environment.py --claude-home FOLDER   # another Claude Code folder (default: VA_CLAUDE_HOME or %USERPROFILE%\\.claude)

The names of before the rename still work: verificar_ambiente.py (it runs this file), --instalar-agentes
and --aplicar-permissoes.

Items: python (3.10+), ffmpeg, ffprobe, numpy, pillow, central-folder and permissions come out [OK] or
[MISSING]; agents and workflow come out [OK] or [WARN] (without the copies, the skill runs in inline mode
and passes the script inline to the Workflow); deepface comes out [OK] or [WARN] (optional: without it,
the skill does not mask the webcam over time; [OK] only when DeepFace, in the version the setup installs
and with intact YuNet weights, runs a detection, a few seconds). Each [MISSING]/[WARN] points to the setup.md step that
resolves it. When --install-agents changes the saved workflow, a [WARN] (in --json, the action's note
field) reminds that the open Claude Code session may keep the previous version in memory.

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

from va import environment  # noqa: E402
from va.common import as_posix, setup_console, workspace_home  # noqa: E402

STEPS = {
    "python": "1-tools", "ffmpeg": "1-tools", "ffprobe": "1-tools",
    "numpy": "2-libraries", "pillow": "2-libraries",
    "central-folder": "3-central folder",
    "agents": "4-agents", "install-agents": "4-agents",
    "workflow": "4-agents", "install-workflow": "4-agents",
    "permissions": "5-permissions", "apply-permissions": "5-permissions",
    "deepface": "6-webcam timeline", "install-deepface": "6-webcam timeline",
}
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
        hint = "" if st["ok"] else " (winget install --id Gyan.FFmpeg -e)"
        items.append(item(name, "ok" if st["ok"] else "missing", st["detail"] + hint))
    for name in ("numpy", "pillow"):
        st = environment.module_status(name)
        hint = "" if st["ok"] else " (python -m pip install numpy Pillow)"
        items.append(item(name, "ok" if st["ok"] else "missing", st["detail"] + hint))
    ws = environment.workspace_status(home, create=True)
    items.append(item("central-folder", "ok" if ws["ok"] else "missing", ws["detail"]))
    perm = environment.permissions_status(home, chome)
    rules = environment.workspace_rules(home)
    detail = perm["detail"]
    if not perm["ok"] and not perm["blocked"]:
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
    ap.add_argument("--install-deepface", dest="install_deepface", action="store_true",
                    help="optional: creates the DeepFace environment of the webcam timeline (python -m venv, "
                         "deepface==0.0.104, YuNet weights; about 350 MB)")
    ap.add_argument("--home", help="central folder (default: VIDEO_ANALYSIS_HOME or %%USERPROFILE%%\\video-analysis)")
    ap.add_argument("--claude-home", help="Claude Code folder (default: VA_CLAUDE_HOME or %%USERPROFILE%%\\.claude)")
    a = ap.parse_args(argv)
    home = workspace_home(a.home)
    chome = environment.claude_home(a.claude_home)
    if a.paths:
        if a.install_agents or a.apply_permissions or a.install_deepface:
            ap.error("--paths only prints the folders: run the action options without it")
        print(json.dumps({"home": as_posix(home), "claude_home": as_posix(chome)}, ensure_ascii=False))
        return 0

    actions = []
    if a.install_agents:
        actions.append(action_install(chome))
        actions.append(action_install_workflow(chome))
    if a.apply_permissions:
        actions.append(action_permissions(home, chome))
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
                print(line("warn", r["id"], r["note"], r["step"]))
        print()
        print("Ready." if not missing else f"{len(missing)} item(s) missing: run /video-analysis setup.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
