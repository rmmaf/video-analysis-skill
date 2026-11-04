"""verificar_ambiente.py (skill root): [OK]/[FALTA]/[AVISO], --json, exit 0/1, --instalar-agentes (roles
and the saved workflow) and --aplicar-permissoes. Always against a temporary Claude home (--claude-home /
VA_CLAUDE_HOME) and a temporary USERPROFILE: the real C:\\Users\\digom\\.claude is never touched (checked
below)."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from va import ambiente

SKILL = Path(__file__).resolve().parent.parent
WORKFLOW_SRC = SKILL / "workflow" / "video-analysis.js"
REAL_SETTINGS = Path(os.path.expanduser("~")) / ".claude" / "settings.json"


def _load_cli():
    spec = importlib.util.spec_from_file_location("verificar_ambiente_cli", SKILL / "verificar_ambiente.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CLI = _load_cli()


REAL_CLAUDE = REAL_SETTINGS.parent


def _inside_real_claude(path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(REAL_CLAUDE))
        return True
    except ValueError:
        return False


@pytest.fixture(scope="module", autouse=True)
def real_settings_untouched():
    """No merge backup may appear in the real ~/.claude (Claude Code itself may rewrite its own
    settings.json at any time, so its bytes are not compared)."""
    before = set(REAL_CLAUDE.glob("settings.json.bak-*"))
    yield
    assert set(REAL_CLAUDE.glob("settings.json.bak-*")) == before, "a backup appeared in the real ~/.claude!"


@pytest.fixture(autouse=True)
def never_real_claude_home(monkeypatch):
    """In-process guard: the setup actions refuse any target inside the real ~/.claude."""
    merge, install, install_wf = ambiente.merge_permissions, ambiente.install_agents, ambiente.install_workflow

    def safe_merge(path, *a, **k):
        assert not _inside_real_claude(path), f"merge_permissions on the real settings: {path}"
        return merge(path, *a, **k)

    def safe_install(skill_dir, chome=None):
        target = chome if chome is not None else ambiente.claude_home()
        assert not _inside_real_claude(target), f"install_agents into the real ~/.claude: {target}"
        return install(skill_dir, chome)

    def safe_install_wf(skill_dir, chome=None):
        target = chome if chome is not None else ambiente.claude_home()
        assert not _inside_real_claude(target), f"install_workflow into the real ~/.claude: {target}"
        return install_wf(skill_dir, chome)

    monkeypatch.setattr(ambiente, "merge_permissions", safe_merge)
    monkeypatch.setattr(ambiente, "install_agents", safe_install)
    monkeypatch.setattr(ambiente, "install_workflow", safe_install_wf)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    user = tmp_path / "user"
    chome, home = user / ".claude", user / "video-analysis"
    chome.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("VA_CLAUDE_HOME", str(chome))
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", str(home))
    return user, chome, home


@pytest.fixture
def tools_ok(monkeypatch):
    monkeypatch.setattr(ambiente, "tool_status", lambda name, timeout=15.0: {
        "id": name, "ok": True, "version": f"{name} version 8.1.2", "path": f"C:/bin/{name}.exe", "detail": f"{name} 8.1.2"})
    monkeypatch.setattr(ambiente, "module_status", lambda key: {"id": key, "ok": True, "version": "1", "detail": f"{key} 1"})


def run(capsys, *argv):
    code = CLI.main([str(a) for a in argv])
    out = capsys.readouterr().out
    return code, out


def line_of(out: str, ident: str) -> str:
    return next(l for l in out.splitlines() if l[8:].split(" ", 1)[0] == ident)


def test_not_ready_without_permissions(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    code, out = run(capsys)
    assert code == 1
    lines = out.splitlines()
    assert lines[-1] == "1 item(s) missing: run /video-analysis setup."      # the two AVISOs do not count
    assert any(l.startswith("[OK]    python ") for l in lines)
    assert any(l.startswith("[OK]    pasta-central ") for l in lines) and (home / "runs").is_dir()
    perm = line_of(out, "permissoes")
    assert perm.startswith("[FALTA]") and "-> setup.md, step 5 (permissões)" in perm and "--aplicar-permissoes" in perm
    ag = line_of(out, "agentes")
    assert ag.startswith("[AVISO]") and "-> setup.md, step 4 (agentes)" in ag
    wf = line_of(out, "workflow")
    assert wf.startswith("[AVISO]") and "-> setup.md, step 4 (agentes)" in wf
    assert "video-analysis.js ausente in " + (chome / "workflows").as_posix() in wf and "--instalar-agentes" in wf
    assert not (chome / "workflows").exists()                                  # the check only reads


def test_install_apply_then_ready(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    (chome / "settings.json").write_text(json.dumps({"enabledPlugins": {"p@m": True}}, indent=2) + "\n", encoding="utf-8")
    code, out = run(capsys, "--instalar-agentes", "--aplicar-permissoes")
    assert code == 0, out
    lines = out.splitlines()
    assert lines[-1] == "Ready."
    assert lines[0].startswith("[OK]    instalar-agentes ") and "close and reopen Claude Code" in lines[0]
    assert lines[1].startswith("[OK]    instalar-workflow ") and "name video-analysis" in lines[1]
    assert (chome / "workflows" / "video-analysis.js").as_posix() in lines[1]
    # the workflow changed: the open session may keep the previous version in memory (an AVISO, exit still 0)
    assert lines[2].startswith("[AVISO] instalar-workflow ") and "previous version" in lines[2]
    assert "close and reopen Claude Code" in lines[2] and "scriptPath" in lines[2]
    assert lines[2].endswith("-> setup.md, step 4 (agentes)")
    assert lines[3].startswith("[OK]    aplicar-permissoes ") and "backup at" in lines[3]
    data = json.loads((chome / "settings.json").read_text(encoding="utf-8"))
    assert data["enabledPlugins"] == {"p@m": True}
    assert data["permissions"]["allow"] == ambiente.workspace_rules(home)
    assert len(list(chome.glob("settings.json.bak-*"))) == 1
    for f in (SKILL / "agents").glob("*.md"):
        assert (chome / "agents" / f.name).read_bytes() == f.read_bytes()
    assert (chome / "workflows" / "video-analysis.js").read_bytes() == WORKFLOW_SRC.read_bytes()
    assert line_of(out, "agentes").startswith("[OK]") and line_of(out, "workflow").startswith("[OK]")
    code, out = run(capsys, "--instalar-agentes", "--aplicar-permissoes")      # idempotent
    assert code == 0 and "were already" in out and len(list(chome.glob("settings.json.bak-*"))) == 1
    assert "the workflow was already identical in " in line_of(out, "instalar-workflow")
    assert "[AVISO]" not in out and "previous version" not in out                 # nothing changed: no warning
    assert sorted(p.name for p in (chome / "workflows").iterdir()) == ["video-analysis.js"]


def test_outdated_workflow_is_aviso_until_reinstalled(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    installed = chome / "workflows" / "video-analysis.js"
    installed.parent.mkdir()
    installed.write_text("export const meta = {name: 'video-analysis'}  // versão antiga", encoding="utf-8")
    code, out = run(capsys, "--json")
    wf = json.loads(out)["itens"][-1]
    assert wf["id"] == "workflow" and wf["estado"] == "aviso" and wf["copia"] == "diferente"
    assert wf["detalhe"].startswith("video-analysis.js diferente in ") and wf["passo"] == "4-agentes"
    code, out = run(capsys, "--instalar-agentes", "--json")
    data = json.loads(out)
    action = next(a for a in data["acoes"] if a["id"] == "instalar-workflow")
    assert action["estado"] == "ok" and action["copiado"] is True and action["caminho"] == installed.as_posix()
    assert data["itens"][-1]["estado"] == "ok" and data["itens"][-1]["copia"] == "ok"
    assert installed.read_bytes() == WORKFLOW_SRC.read_bytes()
    # replaced with this session open: the note says the session may keep the old version in memory
    assert action["nota"] == CLI.NOTA_WORKFLOW and "previous version" in action["nota"]
    assert "close and reopen Claude Code" in action["nota"] and "scriptPath" in action["nota"]
    code, out = run(capsys, "--instalar-agentes", "--json")                    # already identical: no note
    action = next(a for a in json.loads(out)["acoes"] if a["id"] == "instalar-workflow")
    assert action["copiado"] is False and action["nota"] is None


def test_workflow_copy_failure_is_falta(sandbox, tools_ok, monkeypatch, capsys):
    user, chome, home = sandbox

    def refuse(skill_dir, chome=None):
        raise PermissionError("acesso negado")
    monkeypatch.setattr(ambiente, "install_workflow", refuse)
    code, out = run(capsys, "--instalar-agentes")
    action = line_of(out, "instalar-workflow")
    assert code == 1 and action.startswith("[FALTA]") and "could not copy the workflow" in action and "acesso negado" in action
    assert "-> setup.md, step 4 (agentes)" in action and line_of(out, "instalar-agentes").startswith("[OK]")
    assert line_of(out, "workflow").startswith("[AVISO]")
    assert out.splitlines()[-1] == "2 item(s) missing: run /video-analysis setup."   # the action and permissoes


def test_missing_tools_are_falta(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(ambiente, "tool_status", lambda name, timeout=15.0: {"id": name, "ok": False, "version": None,
                                                                             "path": None, "detail": f"{name} não está no PATH"})
    monkeypatch.setattr(ambiente, "module_status", lambda key: {"id": key, "ok": key == "numpy", "version": None,
                                                                "detail": f"{key}?"})
    code, out = run(capsys)
    assert code == 1
    for ident, step in (("ffmpeg", "1 (ferramentas)"), ("ffprobe", "1 (ferramentas)"), ("pillow", "2 (bibliotecas)")):
        line = next(l for l in out.splitlines() if f" {ident} " in l)
        assert line.startswith("[FALTA]") and f"step {step}" in line
    assert "winget install --id Gyan.FFmpeg -e" in out and "python -m pip install numpy Pillow" in out
    assert out.splitlines()[-1] == "4 item(s) missing: run /video-analysis setup."


def test_json_output(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    code, out = run(capsys, "--json", "--home", home)
    data = json.loads(out)
    assert code == 1 and data["pronto"] is False and data["acoes"] == []
    assert data["claude_home"] == chome.as_posix() and data["home"] == home.as_posix()
    ids = [i["id"] for i in data["itens"]]
    assert ids == ["python", "ffmpeg", "ffprobe", "numpy", "pillow", "pasta-central", "permissoes", "agentes", "workflow"]
    perm = data["itens"][6]
    assert perm["estado"] == "falta" and perm["passo"] == "5-permissões" and perm["regras"] == ambiente.workspace_rules(home)
    assert data["itens"][7]["estado"] == "aviso"
    wf = data["itens"][8]
    assert wf["estado"] == "aviso" and wf["passo"] == "4-agentes" and wf["copia"] == "ausente"
    assert wf["caminho"] == (chome / "workflows" / "video-analysis.js").as_posix()


def test_bad_settings_json_is_not_overwritten(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    (chome / "settings.json").write_text("{quebrado", encoding="utf-8")
    code, out = run(capsys, "--aplicar-permissoes")
    assert code == 1 and "nothing was written" in out
    assert (chome / "settings.json").read_text(encoding="utf-8") == "{quebrado"


def test_claude_home_flag_wins_over_env(sandbox, tools_ok, capsys, tmp_path):
    other = tmp_path / "outro-claude"
    code, out = run(capsys, "--claude-home", other, "--instalar-agentes", "--json")
    data = json.loads(out)
    assert data["claude_home"] == other.as_posix() and (other / "agents").is_dir()
    assert (other / "workflows" / "video-analysis.js").read_bytes() == WORKFLOW_SRC.read_bytes()
    assert data["itens"][-1]["caminho"] == (other / "workflows" / "video-analysis.js").as_posix()
    assert not (sandbox[1] / "agents").exists() and not (sandbox[1] / "workflows").exists()


def test_subprocess_with_env_override(sandbox, tmp_path):
    """The real CLI as a process (real tool checks), with VA_CLAUDE_HOME pointing to the sandbox."""
    user, chome, home = sandbox
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-B", str(SKILL / "verificar_ambiente.py"), "--instalar-agentes",
                        "--aplicar-permissoes", "--json"], capture_output=True, env=env, timeout=120)
    data = json.loads(r.stdout.decode("utf-8"))
    assert data["claude_home"] == chome.as_posix()
    assert {a["id"]: a["estado"] for a in data["acoes"]} == {"instalar-agentes": "ok", "instalar-workflow": "ok",
                                                             "aplicar-permissoes": "ok"}
    items = {i["id"]: i["estado"] for i in data["itens"]}
    assert items["permissoes"] == "ok" and items["agentes"] == "ok" and items["pasta-central"] == "ok"
    assert items["workflow"] == "ok"
    tools_ready = all(items[k] == "ok" for k in ("python", "ffmpeg", "ffprobe", "numpy", "pillow"))
    assert (r.returncode == 0) == tools_ready and data["pronto"] == tools_ready
    assert (chome / "settings.json").is_file() and (chome / "agents").is_dir()
    assert (chome / "workflows" / "video-analysis.js").read_bytes() == WORKFLOW_SRC.read_bytes()
