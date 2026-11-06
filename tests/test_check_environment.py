"""check_environment.py (skill root): [OK]/[MISSING]/[WARN], --json, exit 0/1, --install-agents (roles
and the saved workflow) and --apply-permissions; the old names (verificar_ambiente.py, --install-agents,
--apply-permissions) still work. Always against a temporary Claude home (--claude-home /
VA_CLAUDE_HOME) and a temporary USERPROFILE: the real C:\\Users\\digom\\.claude is never touched (checked
below)."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from va import environment

SKILL = Path(__file__).resolve().parent.parent
WORKFLOW_SRC = SKILL / "workflow" / "video-analysis.js"
REAL_SETTINGS = Path(os.path.expanduser("~")) / ".claude" / "settings.json"


def _load_cli():
    spec = importlib.util.spec_from_file_location("check_environment_cli", SKILL / "check_environment.py")
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
    merge, install, install_wf = environment.merge_permissions, environment.install_agents, environment.install_workflow

    def safe_merge(path, *a, **k):
        assert not _inside_real_claude(path), f"merge_permissions on the real settings: {path}"
        return merge(path, *a, **k)

    def safe_install(skill_dir, chome=None):
        target = chome if chome is not None else environment.claude_home()
        assert not _inside_real_claude(target), f"install_agents into the real ~/.claude: {target}"
        return install(skill_dir, chome)

    def safe_install_wf(skill_dir, chome=None):
        target = chome if chome is not None else environment.claude_home()
        assert not _inside_real_claude(target), f"install_workflow into the real ~/.claude: {target}"
        return install_wf(skill_dir, chome)

    monkeypatch.setattr(environment, "merge_permissions", safe_merge)
    monkeypatch.setattr(environment, "install_agents", safe_install)
    monkeypatch.setattr(environment, "install_workflow", safe_install_wf)
    # pip must never run in the tests: a test of --install-deepface replaces it
    monkeypatch.setattr(environment, "install_deepface",
                        lambda *a, **k: pytest.fail("install_deepface must be replaced in the tests"))


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
    monkeypatch.setattr(environment, "tool_status", lambda name, timeout=15.0: {
        "id": name, "ok": True, "version": f"{name} version 8.1.2", "path": f"C:/bin/{name}.exe", "detail": f"{name} 8.1.2"})
    monkeypatch.setattr(environment, "module_status", lambda key: {"id": key, "ok": True, "version": "1", "detail": f"{key} 1"})


def run(capsys, *argv):
    code = CLI.main([str(a) for a in argv])
    out = capsys.readouterr().out
    return code, out


def item_of(data: dict, ident: str) -> dict:
    return next(i for i in data["items"] if i["id"] == ident)


def line_of(out: str, ident: str) -> str:
    return next(l for l in out.splitlines() if l.startswith("[") and l.split("]", 1)[1].split()[0] == ident)


def test_not_ready_without_permissions(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    code, out = run(capsys)
    assert code == 1
    lines = out.splitlines()
    assert lines[-1] == "1 item(s) missing: run /video-analysis setup."      # the three WARNs do not count
    assert any(l.startswith("[OK]      python ") for l in lines)
    assert any(l.startswith("[OK]      central-folder ") for l in lines) and (home / "runs").is_dir()
    perm = line_of(out, "permissions")
    assert perm.startswith("[MISSING]") and "-> setup.md, step 5 (permissions)" in perm and "--apply-permissions" in perm
    ag = line_of(out, "agents")
    assert ag.startswith("[WARN]") and "-> setup.md, step 4 (agents)" in ag
    wf = line_of(out, "workflow")
    assert wf.startswith("[WARN]") and "-> setup.md, step 4 (agents)" in wf
    assert "video-analysis.js missing in " + (chome / "workflows").as_posix() in wf and "--install-agents" in wf
    assert not (chome / "workflows").exists()                                  # the check only reads


def test_install_apply_then_ready(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    (chome / "settings.json").write_text(json.dumps({"enabledPlugins": {"p@m": True}}, indent=2) + "\n", encoding="utf-8")
    code, out = run(capsys, "--install-agents", "--apply-permissions")
    assert code == 0, out
    lines = out.splitlines()
    assert lines[-1] == "Ready."
    assert lines[0].startswith("[OK]      install-agents ") and "close and reopen Claude Code" in lines[0]
    assert lines[1].startswith("[OK]      install-workflow ") and "name video-analysis" in lines[1]
    assert (chome / "workflows" / "video-analysis.js").as_posix() in lines[1]
    # the workflow changed: the open session may keep the previous version in memory (a WARN, exit still 0)
    assert lines[2].startswith("[WARN]    install-workflow ") and "previous version" in lines[2]
    assert "close and reopen Claude Code" in lines[2] and "scriptPath" in lines[2]
    assert lines[2].endswith("-> setup.md, step 4 (agents)")
    assert lines[3].startswith("[OK]      apply-permissions ") and "backup at" in lines[3]
    data = json.loads((chome / "settings.json").read_text(encoding="utf-8"))
    assert data["enabledPlugins"] == {"p@m": True}
    assert data["permissions"]["allow"] == environment.workspace_rules(home)
    assert len(list(chome.glob("settings.json.bak-*"))) == 1
    for f in (SKILL / "agents").glob("*.md"):
        assert (chome / "agents" / f.name).read_bytes() == f.read_bytes()
    assert (chome / "workflows" / "video-analysis.js").read_bytes() == WORKFLOW_SRC.read_bytes()
    assert line_of(out, "agents").startswith("[OK]") and line_of(out, "workflow").startswith("[OK]")
    code, out = run(capsys, "--install-agents", "--apply-permissions")      # idempotent
    assert code == 0 and "were already" in out and len(list(chome.glob("settings.json.bak-*"))) == 1
    assert "the workflow was already identical in " in line_of(out, "install-workflow")
    # nothing changed: no warning, except the optional DeepFace environment, absent in the sandbox
    assert [l.split()[1] for l in out.splitlines() if l.startswith("[WARN]")] == ["deepface"]
    assert "previous version" not in out
    assert sorted(p.name for p in (chome / "workflows").iterdir()) == ["video-analysis.js"]


def test_outdated_workflow_is_warn_until_reinstalled(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    installed = chome / "workflows" / "video-analysis.js"
    installed.parent.mkdir()
    installed.write_text("export const meta = {name: 'video-analysis'}  // versão antiga", encoding="utf-8")
    code, out = run(capsys, "--json")
    wf = item_of(json.loads(out), "workflow")
    assert wf["id"] == "workflow" and wf["state"] == "warn" and wf["copy"] == "different"
    assert wf["detail"].startswith("video-analysis.js different in ") and wf["step"] == "4-agents"
    code, out = run(capsys, "--install-agents", "--json")
    data = json.loads(out)
    action = next(a for a in data["actions"] if a["id"] == "install-workflow")
    assert action["state"] == "ok" and action["copied"] is True and action["path"] == installed.as_posix()
    assert item_of(data, "workflow")["state"] == "ok" and item_of(data, "workflow")["copy"] == "ok"
    assert installed.read_bytes() == WORKFLOW_SRC.read_bytes()
    # replaced with this session open: the note says the session may keep the old version in memory
    assert action["note"] == CLI.NOTE_WORKFLOW and "previous version" in action["note"]
    assert "close and reopen Claude Code" in action["note"] and "scriptPath" in action["note"]
    code, out = run(capsys, "--install-agents", "--json")                    # already identical: no note
    action = next(a for a in json.loads(out)["actions"] if a["id"] == "install-workflow")
    assert action["copied"] is False and action["note"] is None


def test_workflow_copy_failure_is_missing(sandbox, tools_ok, monkeypatch, capsys):
    user, chome, home = sandbox

    def refuse(skill_dir, chome=None):
        raise PermissionError("acesso negado")
    monkeypatch.setattr(environment, "install_workflow", refuse)
    code, out = run(capsys, "--install-agents")
    action = line_of(out, "install-workflow")
    assert code == 1 and action.startswith("[MISSING]") and "could not copy the workflow" in action and "acesso negado" in action
    assert "-> setup.md, step 4 (agents)" in action and line_of(out, "install-agents").startswith("[OK]")
    assert line_of(out, "workflow").startswith("[WARN]")
    assert out.splitlines()[-1] == "2 item(s) missing: run /video-analysis setup."   # the action and permissions


def test_missing_tools_fail_the_check(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(environment, "tool_status", lambda name, timeout=15.0: {"id": name, "ok": False, "version": None,
                                                                             "path": None, "detail": f"{name} não está no PATH"})
    monkeypatch.setattr(environment, "module_status", lambda key: {"id": key, "ok": key == "numpy", "version": None,
                                                                "detail": f"{key}?"})
    code, out = run(capsys)
    assert code == 1
    for ident, step in (("ffmpeg", "1 (tools)"), ("ffprobe", "1 (tools)"), ("pillow", "2 (libraries)")):
        line = next(l for l in out.splitlines() if f" {ident} " in l)
        assert line.startswith("[MISSING]") and f"step {step}" in line
    assert "winget install --id Gyan.FFmpeg -e" in out and "python -m pip install numpy Pillow" in out
    assert out.splitlines()[-1] == "4 item(s) missing: run /video-analysis setup."


def test_json_output(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    code, out = run(capsys, "--json", "--home", home)
    data = json.loads(out)
    assert code == 1 and data["ready"] is False and data["actions"] == []
    assert data["claude_home"] == chome.as_posix() and data["home"] == home.as_posix()
    ids = [i["id"] for i in data["items"]]
    assert ids == ["python", "ffmpeg", "ffprobe", "numpy", "pillow", "central-folder", "permissions", "agents", "workflow",
                   "deepface"]
    perm = data["items"][6]
    assert perm["state"] == "missing" and perm["step"] == "5-permissions" and perm["rules"] == environment.workspace_rules(home)
    assert data["items"][7]["state"] == "warn"
    wf = data["items"][8]
    assert wf["state"] == "warn" and wf["step"] == "4-agents" and wf["copy"] == "missing"
    assert wf["path"] == (chome / "workflows" / "video-analysis.js").as_posix()
    df = data["items"][9]
    assert df["state"] == "warn" and df["step"] == "6-webcam timeline" and df["version"] is None
    assert df["env"] == (user / "deepface-env").as_posix() and df["expected"] == "0.0.104"


def test_bad_settings_json_is_not_overwritten(sandbox, tools_ok, capsys):
    user, chome, home = sandbox
    (chome / "settings.json").write_text("{quebrado", encoding="utf-8")
    code, out = run(capsys, "--apply-permissions")
    assert code == 1 and "nothing was written" in out
    assert (chome / "settings.json").read_text(encoding="utf-8") == "{quebrado"


def test_claude_home_flag_wins_over_env(sandbox, tools_ok, capsys, tmp_path):
    other = tmp_path / "outro-claude"
    code, out = run(capsys, "--claude-home", other, "--install-agents", "--json")
    data = json.loads(out)
    assert data["claude_home"] == other.as_posix() and (other / "agents").is_dir()
    assert (other / "workflows" / "video-analysis.js").read_bytes() == WORKFLOW_SRC.read_bytes()
    assert item_of(data, "workflow")["path"] == (other / "workflows" / "video-analysis.js").as_posix()
    assert not (sandbox[1] / "agents").exists() and not (sandbox[1] / "workflows").exists()


def test_subprocess_with_env_override(sandbox, tmp_path):
    """The real CLI as a process (real tool checks), with VA_CLAUDE_HOME pointing to the sandbox."""
    user, chome, home = sandbox
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-B", str(SKILL / "check_environment.py"), "--install-agents",
                        "--apply-permissions", "--json"], capture_output=True, env=env, timeout=120)
    data = json.loads(r.stdout.decode("utf-8"))
    assert data["claude_home"] == chome.as_posix()
    assert {a["id"]: a["state"] for a in data["actions"]} == {"install-agents": "ok", "install-workflow": "ok",
                                                             "apply-permissions": "ok"}
    items = {i["id"]: i["state"] for i in data["items"]}
    assert items["permissions"] == "ok" and items["agents"] == "ok" and items["central-folder"] == "ok"
    assert items["workflow"] == "ok"
    tools_ready = all(items[k] == "ok" for k in ("python", "ffmpeg", "ffprobe", "numpy", "pillow"))
    assert (r.returncode == 0) == tools_ready and data["ready"] == tools_ready
    assert (chome / "settings.json").is_file() and (chome / "agents").is_dir()
    assert (chome / "workflows" / "video-analysis.js").read_bytes() == WORKFLOW_SRC.read_bytes()


def test_old_names_still_work(sandbox, tools_ok, capsys):
    """The names of before the rename: --instalar-agentes and --aplicar-permissoes, and
    verificar_ambiente.py, which runs check_environment.py with the same arguments and exit code."""
    user, chome, home = sandbox
    code, out = run(capsys, "--instalar-agentes", "--aplicar-permissoes")
    assert code == 0 and out.splitlines()[-1] == "Ready."
    assert line_of(out, "install-agents").startswith("[OK]") and line_of(out, "apply-permissions").startswith("[OK]")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-B", str(SKILL / "verificar_ambiente.py"), "--json"], capture_output=True,
                       env=env, timeout=120)
    data = json.loads(r.stdout.decode("utf-8"))
    assert list(data) == ["ready", "home", "claude_home", "actions", "items"]        # the new output, not the old one
    assert data["claude_home"] == chome.as_posix() and [i["id"] for i in data["items"]][0] == "python"
    assert {i["id"]: i["state"] for i in data["items"]}["agents"] == "ok"
    assert r.returncode == (0 if data["ready"] else 1)


def _deepface(user, **fields) -> dict:
    env = (user / "deepface-env").as_posix()
    st = {"id": "deepface", "ok": True, "state": "ok", "problem": None, "env": env, "python": env + "/Scripts/python.exe",
          "version": "0.0.104", "expected": "0.0.104", "weights": True, "detail": f"deepface 0.0.104 in {env}"}
    return {**st, **fields}


@pytest.mark.parametrize("fields, state, needle", [
    ({}, "ok", ", and it ran a detection: the skill masks the webcam over time"),
    ({"ok": False, "state": "absent", "version": None, "detail": "no DeepFace environment at C:/x"}, "warn",
     "optional: no DeepFace environment at C:/x; without it, the skill does not mask the webcam over time"),
    ({"ok": False, "state": "broken", "problem": "modules", "detail": "C:/x lacks cv2"}, "warn",
     "C:/x lacks cv2; until it is repaired, the skill does not mask the webcam over time (--install-deepface repairs it)"),
    ({"ok": False, "state": "broken", "problem": "version", "detail": "C:/x has deepface 0.0.99, and the skill uses 0.0.104"},
     "warn", "has deepface 0.0.99, and the skill uses 0.0.104; until it is repaired"),
    ({"ok": False, "state": "broken", "problem": "detection", "weights": True,
      "detail": "DeepFace did not run a detection in C:/x (exit 1): ImportError: DLL load failed"},
     "warn", "ImportError: DLL load failed; until it is repaired"),
])
def test_deepface_is_optional(sandbox, tools_ok, capsys, monkeypatch, fields, state, needle):
    """The DeepFace environment never makes the check fail: [OK], or [WARN] pointing to step 6. The check
    is the deep one: [OK] needs a detection that ran."""
    user, chome, home = sandbox
    asked = []
    monkeypatch.setattr(environment, "deepface_status", lambda **k: asked.append(k) or _deepface(user, **fields))
    code, out = run(capsys, "--install-agents", "--apply-permissions")
    assert code == 0 and out.splitlines()[-1] == "Ready." and asked == [{"deep": True}]
    line = line_of(out, "deepface")
    assert line.startswith("[OK]" if state == "ok" else "[WARN]") and needle in line
    assert line.endswith("-> setup.md, step 6 (webcam timeline)") == (state == "warn")


def test_install_deepface_action(sandbox, tools_ok, capsys, monkeypatch):
    user, chome, home = sandbox
    ok = _deepface(user)
    monkeypatch.setattr(environment, "deepface_status", lambda **k: ok)
    monkeypatch.setattr(environment, "install_deepface",
                        lambda: {"env": ok["env"], "done": ["install deepface 0.0.104"], "status": ok})
    code, out = run(capsys, "--install-deepface", "--json")
    action = json.loads(out)["actions"][0]
    assert action["id"] == "install-deepface" and action["state"] == "ok" and action["step"] == "6-webcam timeline"
    assert action["detail"] == f"install deepface 0.0.104; DeepFace ran a detection: deepface 0.0.104 in {ok['env']}"
    monkeypatch.setattr(environment, "install_deepface", lambda: {"env": ok["env"], "done": [], "status": ok})
    code, out = run(capsys, "--install-deepface")
    assert line_of(out, "install-deepface").startswith("[OK]")
    assert (f"already in place; DeepFace ran a detection: deepface 0.0.104 in {ok['env']}"
            in line_of(out, "install-deepface"))
    # it ran but the environment still does not work: the action is [MISSING]
    monkeypatch.setattr(environment, "install_deepface", lambda: {"env": ok["env"], "done": ["x"],
                                                                  "status": _deepface(user, ok=False, detail="C:/x lacks cv2")})
    code, out = run(capsys, "--install-deepface", "--install-agents", "--apply-permissions")
    assert code == 1 and "C:/x lacks cv2 after the installation" in line_of(out, "install-deepface")

    def refuse():
        raise environment.SetupError("could not install deepface 0.0.104 (exit 1): ERROR: No matching distribution")
    monkeypatch.setattr(environment, "install_deepface", refuse)
    code, out = run(capsys, "--install-deepface", "--install-agents", "--apply-permissions")
    action = line_of(out, "install-deepface")
    assert code == 1 and action.startswith("[MISSING]") and "No matching distribution" in action
    assert action.endswith("-> setup.md, step 6 (webcam timeline)")
    assert out.splitlines()[-1] == "1 item(s) missing: run /video-analysis setup."
