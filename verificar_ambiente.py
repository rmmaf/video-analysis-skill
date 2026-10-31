"""Confere se esta máquina está pronta para a skill video-analysis (roteiro em setup.md).

Só lê, a não ser com as duas opções de ação, que o setup roda depois do "sim" da pessoa:

    python verificar_ambiente.py                        # checagem completa
    python verificar_ambiente.py --json                 # mesma coisa, em formato de máquina
    python verificar_ambiente.py --instalar-agentes     # copia os 2 papéis para ~/.claude/agents e o workflow para ~/.claude/workflows
    python verificar_ambiente.py --aplicar-permissoes   # backup do settings.json e mescla das 6 regras Read/Edit/Write
    python verificar_ambiente.py --home PASTA           # outra pasta central (padrão: VIDEO_ANALYSIS_HOME ou %USERPROFILE%\\video-analysis)
    python verificar_ambiente.py --claude-home PASTA    # outra pasta do Claude Code (padrão: VA_CLAUDE_HOME ou %USERPROFILE%\\.claude)

Itens: python (3.10+), ffmpeg, ffprobe, numpy, pillow, pasta-central e permissoes saem [OK] ou [FALTA];
agentes e workflow saem [OK] ou [AVISO] (sem as cópias, a skill roda no modo inline e passa o script
inline ao Workflow). Cada [FALTA]/[AVISO] aponta o passo de setup.md que o resolve. Quando o
--instalar-agentes muda o workflow salvo, um [AVISO] (no --json, o campo nota da ação) lembra que a
sessão aberta do Claude Code pode manter na memória a versão anterior.

Saída 0 = pronto (aviso não conta), 1 = falta alguma coisa.
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

PASSOS = {
    "python": "1-ferramentas", "ffmpeg": "1-ferramentas", "ffprobe": "1-ferramentas",
    "numpy": "2-bibliotecas", "pillow": "2-bibliotecas",
    "pasta-central": "3-pasta central",
    "agentes": "4-agentes", "instalar-agentes": "4-agentes",
    "workflow": "4-agentes", "instalar-workflow": "4-agentes",
    "permissoes": "5-permissões", "aplicar-permissoes": "5-permissões",
}
ROTULO = {"ok": "[OK]   ", "falta": "[FALTA]", "aviso": "[AVISO]"}
# O Workflow guarda na memória, por sessão, o workflow salvo que já leu: trocado o arquivo, o nome segue
# rodando a versão anterior até o Claude Code reabrir (passo 6 do SKILL.md).
NOTA_WORKFLOW = ("esta sessão do Claude Code pode manter na memória a versão anterior do workflow (ou nem "
                 "conhecer o nome): feche e abra o Claude Code; até lá, a skill passa o script inline ou em "
                 "scriptPath, sem usar o nome (passo 6 do SKILL.md)")


def item(ident: str, estado: str, detalhe: str, **extra) -> dict:
    return {"id": ident, "estado": estado, "detalhe": detalhe, "passo": PASSOS.get(ident), **extra}


def linha(estado: str, ident: str, detalhe: str, passo: str | None) -> str:
    sufixo = ""
    if estado in ("falta", "aviso") and passo:
        numero, nome = passo.split("-", 1)
        sufixo = f"  -> setup.md, passo {numero} ({nome})"
    return f"{ROTULO[estado]} {ident:<18} {detalhe}{sufixo}"


def acao_instalar(chome: Path) -> dict:
    try:
        r = ambiente.install_agents(SKILL, chome)
    except OSError as exc:
        return item("instalar-agentes", "falta", f"não consegui copiar os papéis para {as_posix(chome / 'agents')}: {exc}")
    if r["copied"]:
        txt = (f"{len(r['copied'])} papel(éis) copiado(s) para {r['dir']} ({', '.join(r['copied'])}); "
               "feche e abra o Claude Code para carregá-los (até lá a skill roda no modo inline)")
    else:
        txt = f"os papéis já estavam idênticos em {r['dir']}"
    return item("instalar-agentes", "ok", txt, copiados=r["copied"], iguais=r["unchanged"])


def acao_instalar_workflow(chome: Path) -> dict:
    try:
        r = ambiente.install_workflow(SKILL, chome)
    except OSError as exc:
        return item("instalar-workflow", "falta",
                    f"não consegui copiar o workflow para {as_posix(chome / 'workflows')}: {exc}")
    if r["copied"]:
        txt = f"workflow copiado para {r['path']}; numa sessão nova, o Workflow o chama pelo nome video-analysis"
    else:
        txt = f"o workflow já estava idêntico em {r['path']}"
    return item("instalar-workflow", "ok", txt, caminho=r["path"], copiado=r["copied"],
                nota=NOTA_WORKFLOW if r["copied"] else None)


def acao_permissoes(home: Path, chome: Path) -> dict:
    regras = ambiente.workspace_rules(home)
    alvo = chome / "settings.json"
    try:
        r = ambiente.merge_permissions(alvo, regras)
    except (ambiente.AmbienteError, OSError) as exc:
        return item("aplicar-permissoes", "falta", str(exc), regras=regras)
    if r["changed"]:
        bak = f"; backup em {r['backup']}" if r["backup"] else " (arquivo novo)"
        txt = (f"{len(r['added'])} regra(s) acrescentada(s) em {r['path']}{bak}; "
               "se os pedidos de permissão continuarem, feche e abra o Claude Code")
    else:
        txt = f"as regras já estavam em {r['path']}; nada foi gravado"
    return item("aplicar-permissoes", "ok", txt, acrescentadas=r["added"], backup=r["backup"], regras=regras)


def checagens(home: Path, chome: Path) -> list[dict]:
    itens = []
    py = ambiente.python_status()
    itens.append(item("python", "ok" if py["ok"] else "falta", py["detail"]))
    for nome in ("ffmpeg", "ffprobe"):
        st = ambiente.tool_status(nome)
        dica = "" if st["ok"] else " (winget install --id Gyan.FFmpeg -e)"
        itens.append(item(nome, "ok" if st["ok"] else "falta", st["detail"] + dica))
    for nome in ("numpy", "pillow"):
        st = ambiente.module_status(nome)
        dica = "" if st["ok"] else " (python -m pip install numpy Pillow)"
        itens.append(item(nome, "ok" if st["ok"] else "falta", st["detail"] + dica))
    ws = ambiente.workspace_status(home, create=True)
    itens.append(item("pasta-central", "ok" if ws["ok"] else "falta", ws["detail"]))
    perm = ambiente.permissions_status(home, chome)
    regras = ambiente.workspace_rules(home)
    detalhe = perm["detail"]
    if not perm["ok"] and not perm["blocked"]:
        detalhe += " (--aplicar-permissoes mescla: " + ", ".join(regras) + ")"
    itens.append(item("permissoes", "ok" if perm["ok"] else "falta", detalhe, regras=regras,
                      cobertas=perm["covered"], bloqueios=perm["blocked"]))
    ag = ambiente.agents_status(SKILL, chome)
    det = ag["detail"] if ag["current"] else ag["detail"] + " (--instalar-agentes copia; sem eles a skill roda no modo inline)"
    itens.append(item("agentes", "ok" if ag["current"] else "aviso", det, arquivos=ag["files"]))
    wf = ambiente.workflow_status(SKILL, chome)
    det = wf["detail"] if wf["current"] else wf["detail"] + (
        " (--instalar-agentes copia; sem ele a skill passa o script inline ao Workflow, gastando mais tokens)")
    itens.append(item("workflow", "ok" if wf["current"] else "aviso", det, caminho=wf["path"], copia=wf["state"]))
    return itens


def main(argv: list[str] | None = None) -> int:
    setup_console()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="saída em JSON")
    ap.add_argument("--instalar-agentes", action="store_true",
                    help="copia os 2 papéis para <claude home>/agents e o workflow para <claude home>/workflows")
    ap.add_argument("--aplicar-permissoes", action="store_true",
                    help="backup do settings.json e mescla das regras Read/Edit/Write da pasta central")
    ap.add_argument("--home", help="pasta central (padrão: VIDEO_ANALYSIS_HOME ou %%USERPROFILE%%\\video-analysis)")
    ap.add_argument("--claude-home", help="pasta do Claude Code (padrão: VA_CLAUDE_HOME ou %%USERPROFILE%%\\.claude)")
    a = ap.parse_args(argv)
    home = workspace_home(a.home)
    chome = ambiente.claude_home(a.claude_home)

    acoes = []
    if a.instalar_agentes:
        acoes.append(acao_instalar(chome))
        acoes.append(acao_instalar_workflow(chome))
    if a.aplicar_permissoes:
        acoes.append(acao_permissoes(home, chome))
    itens = checagens(home, chome)
    faltas = [r for r in acoes + itens if r["estado"] == "falta"]

    if a.json:
        print(json.dumps({"pronto": not faltas, "home": as_posix(home), "claude_home": as_posix(chome),
                          "acoes": acoes, "itens": itens}, ensure_ascii=False, indent=2))
    else:
        for r in acoes + itens:
            print(linha(r["estado"], r["id"], r["detalhe"], r["passo"]))
            if r.get("nota"):
                print(linha("aviso", r["id"], r["nota"], r["passo"]))
        print()
        print("Pronto." if not faltas else f"Falta {len(faltas)} item(ns): rode /video-analysis setup.")
    return 1 if faltas else 0


if __name__ == "__main__":
    sys.exit(main())
