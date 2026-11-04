"""Checks whether this machine is ready for the video-analysis skill (procedure in setup.md).

Read-only, except with the two action options, which the setup runs after the person's "yes":

    python verificar_ambiente.py                        # full check
    python verificar_ambiente.py --json                 # same thing, in machine format
    python verificar_ambiente.py --instalar-agentes     # copies the 2 roles to ~/.claude/agents and the workflow to ~/.claude/workflows
    python verificar_ambiente.py --aplicar-permissoes   # backup of settings.json and merge of the 6 Read/Edit/Write rules
    python verificar_ambiente.py --home FOLDER          # another central folder (default: VIDEO_ANALYSIS_HOME or %USERPROFILE%\\video-analysis)
    python verificar_ambiente.py --claude-home FOLDER   # another Claude Code folder (default: VA_CLAUDE_HOME or %USERPROFILE%\\.claude)

Items: python (3.10+), ffmpeg, ffprobe, numpy, pillow, pasta-central and permissoes come out [OK] or
[FALTA]; agentes and workflow come out [OK] or [AVISO] (without the copies, the skill runs in inline mode
and passes the script inline to the Workflow). Each [FALTA]/[AVISO] points to the setup.md step that
resolves it. When --instalar-agentes changes the saved workflow, an [AVISO] (in --json, the action's nota
field) reminds that the open Claude Code session may keep the previous version in memory.

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

from va import ambiente  # noqa: E402
from va.common import as_posix, setup_console, workspace_home  # noqa: E402

STEPS = {
    "python": "1-tools", "ffmpeg": "1-tools", "ffprobe": "1-tools",
    "numpy": "2-libraries", "pillow": "2-libraries",
    "pasta-central": "3-central folder",
    "agentes": "4-agents", "instalar-agentes": "4-agents",
    "workflow": "4-agents", "instalar-workflow": "4-agents",
    "permissoes": "5-permissions", "aplicar-permissoes": "5-permissions",
}
LABEL = {"ok": "[OK]   ", "falta": "[FALTA]", "aviso": "[AVISO]"}
# The Workflow keeps in memory, per session, the saved workflow it has already read: once the file is
# replaced, the name keeps running the previous version until Claude Code reopens (step 6 of SKILL.md).
NOTA_WORKFLOW = ("this Claude Code session may keep the previous version of the workflow in memory (or not "
                 "even know the name): close and reopen Claude Code; until then, the skill passes the script "
                 "inline or in scriptPath, without using the name (step 6 of SKILL.md)")


def item(ident: str, state: str, detail: str, **extra) -> dict:
    return {"id": ident, "estado": state, "detalhe": detail, "passo": STEPS.get(ident), **extra}


def line(state: str, ident: str, detail: str, step: str | None) -> str:
    suffix = ""
    if state in ("falta", "aviso") and step:
        number, name = step.split("-", 1)
        suffix = f"  -> setup.md, step {number} ({name})"
    return f"{LABEL[state]} {ident:<18} {detail}{suffix}"


def action_install(chome: Path) -> dict:
    try:
        r = ambiente.install_agents(SKILL, chome)
    except OSError as exc:
        return item("instalar-agentes", "falta", f"could not copy the roles to {as_posix(chome / 'agents')}: {exc}")
    if r["copied"]:
        txt = (f"{len(r['copied'])} role(s) copied to {r['dir']} ({', '.join(r['copied'])}); "
               "close and reopen Claude Code to load them (until then the skill runs in inline mode)")
    else:
        txt = f"the roles were already identical in {r['dir']}"
    return item("instalar-agentes", "ok", txt, copiados=r["copied"], iguais=r["unchanged"])


def action_install_workflow(chome: Path) -> dict:
    try:
        r = ambiente.install_workflow(SKILL, chome)
    except OSError as exc:
        return item("instalar-workflow", "falta",
                    f"could not copy the workflow to {as_posix(chome / 'workflows')}: {exc}")
    if r["copied"]:
        txt = f"workflow copied to {r['path']}; in a new session, the Workflow calls it by the name video-analysis"
    else:
        txt = f"the workflow was already identical in {r['path']}"
    return item("instalar-workflow", "ok", txt, caminho=r["path"], copiado=r["copied"],
                nota=NOTA_WORKFLOW if r["copied"] else None)


def action_permissions(home: Path, chome: Path) -> dict:
    rules = ambiente.workspace_rules(home)
    target = chome / "settings.json"
    try:
        r = ambiente.merge_permissions(target, rules)
    except (ambiente.AmbienteError, OSError) as exc:
        return item("aplicar-permissoes", "falta", str(exc), regras=rules)
    if r["changed"]:
        bak = f"; backup at {r['backup']}" if r["backup"] else " (new file)"
        txt = (f"{len(r['added'])} rule(s) added to {r['path']}{bak}; "
               "if the permission prompts continue, close and reopen Claude Code")
    else:
        txt = f"the rules were already in {r['path']}; nothing was written"
    return item("aplicar-permissoes", "ok", txt, acrescentadas=r["added"], backup=r["backup"], regras=rules)


def checks(home: Path, chome: Path) -> list[dict]:
    items = []
    py = ambiente.python_status()
    items.append(item("python", "ok" if py["ok"] else "falta", py["detail"]))
    for name in ("ffmpeg", "ffprobe"):
        st = ambiente.tool_status(name)
        hint = "" if st["ok"] else " (winget install --id Gyan.FFmpeg -e)"
        items.append(item(name, "ok" if st["ok"] else "falta", st["detail"] + hint))
    for name in ("numpy", "pillow"):
        st = ambiente.module_status(name)
        hint = "" if st["ok"] else " (python -m pip install numpy Pillow)"
        items.append(item(name, "ok" if st["ok"] else "falta", st["detail"] + hint))
    ws = ambiente.workspace_status(home, create=True)
    items.append(item("pasta-central", "ok" if ws["ok"] else "falta", ws["detail"]))
    perm = ambiente.permissions_status(home, chome)
    rules = ambiente.workspace_rules(home)
    detail = perm["detail"]
    if not perm["ok"] and not perm["blocked"]:
        detail += " (--aplicar-permissoes merges: " + ", ".join(rules) + ")"
    items.append(item("permissoes", "ok" if perm["ok"] else "falta", detail, regras=rules,
                      cobertas=perm["covered"], bloqueios=perm["blocked"]))
    ag = ambiente.agents_status(SKILL, chome)
    det = ag["detail"] if ag["current"] else ag["detail"] + " (--instalar-agentes copies them; without them the skill runs in inline mode)"
    items.append(item("agentes", "ok" if ag["current"] else "aviso", det, arquivos=ag["files"]))
    wf = ambiente.workflow_status(SKILL, chome)
    det = wf["detail"] if wf["current"] else wf["detail"] + (
        " (--instalar-agentes copies it; without it the skill passes the script inline to the Workflow, spending more tokens)")
    items.append(item("workflow", "ok" if wf["current"] else "aviso", det, caminho=wf["path"], copia=wf["state"]))
    return items


def main(argv: list[str] | None = None) -> int:
    setup_console()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="output in JSON")
    ap.add_argument("--instalar-agentes", action="store_true",
                    help="copies the 2 roles to <claude home>/agents and the workflow to <claude home>/workflows")
    ap.add_argument("--aplicar-permissoes", action="store_true",
                    help="backup of settings.json and merge of the Read/Edit/Write rules of the central folder")
    ap.add_argument("--home", help="central folder (default: VIDEO_ANALYSIS_HOME or %%USERPROFILE%%\\video-analysis)")
    ap.add_argument("--claude-home", help="Claude Code folder (default: VA_CLAUDE_HOME or %%USERPROFILE%%\\.claude)")
    a = ap.parse_args(argv)
    home = workspace_home(a.home)
    chome = ambiente.claude_home(a.claude_home)

    actions = []
    if a.instalar_agentes:
        actions.append(action_install(chome))
        actions.append(action_install_workflow(chome))
    if a.aplicar_permissoes:
        actions.append(action_permissions(home, chome))
    items = checks(home, chome)
    missing = [r for r in actions + items if r["estado"] == "falta"]

    if a.json:
        print(json.dumps({"pronto": not missing, "home": as_posix(home), "claude_home": as_posix(chome),
                          "acoes": actions, "itens": items}, ensure_ascii=False, indent=2))
    else:
        for r in actions + items:
            print(line(r["estado"], r["id"], r["detalhe"], r["passo"]))
            if r.get("nota"):
                print(line("aviso", r["id"], r["nota"], r["passo"]))
        print()
        print("Ready." if not missing else f"{len(missing)} item(s) missing: run /video-analysis setup.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
