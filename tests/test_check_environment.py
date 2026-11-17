"""check_environment.py (skill root): [OK]/[MISSING]/[WARN], --json, exit 0/1, --install-agents (roles
and the saved workflow) and --apply-permissions; the old names (verificar_ambiente.py, --install-agents,
--apply-permissions) still work. Always against a temporary Claude home (--claude-home /
VA_CLAUDE_HOME) and a temporary USERPROFILE: the real %USERPROFILE%\\.claude is never touched (checked
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
    # pip and uv must never run in the tests: a test of --install-deepface / --install-transcription replaces them
    monkeypatch.setattr(environment, "install_deepface",
                        lambda *a, **k: pytest.fail("install_deepface must be replaced in the tests"))
    monkeypatch.setattr(environment, "install_transcription",
                        lambda *a, **k: pytest.fail("install_transcription must be replaced in the tests"))


def _transcription(user, **fields) -> dict:
    env = (user / "whisperx-env").as_posix()
    st = {"id": "transcription", "ok": True, "ready": True, "state": "ok", "problem": None, "env": env,
          "python": env + "/Scripts/python.exe", "python_version": "3.12.11", "version": "3.8.6", "expected": "3.8.6",
          "torch": "2.8.0+cpu", "backend": "cpu", "device": "cpu", "gpu": None, "default_model": "large-v3-turbo",
          "token": True, "models": {"pyannote/speaker-diarization-community-1": True}, "punkt": True,
          "nltk_data": env + "/nltk_data", "login_command": f'& "{env}/Scripts/hf.exe" auth login',
          "detail": f"whisperx 3.8.6 in {env} (cpu; model large-v3-turbo)"}
    return {**st, **fields}


@pytest.fixture(autouse=True)
def transcription_ready(monkeypatch, tmp_path):
    """The transcription environment ready and the speakers separated, unless a test says otherwise: the other
    tests are about the other items (the real check would look for a WhisperX environment in the sandbox)."""
    user = tmp_path / "user"
    monkeypatch.delenv("VA_WHISPERX_ENV", raising=False)
    monkeypatch.setattr(environment, "transcription_status", lambda *a, **k: _transcription(user))
    monkeypatch.setattr(environment, "diarization_status", lambda st=None, **k: {
        "id": "speakers", "ok": True, "state": "ok", "model": "pyannote/speaker-diarization-community-1",
        "page": environment.DIARIZE_PAGE, "login_command": "hf auth login", "cached": True, "access": "ok",
        "detail": "token saved, access confirmed: the subtitles separate the speakers"})


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    user = tmp_path / "user"
    chome, home = user / ".claude", user / "video-analysis"
    chome.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("VA_CLAUDE_HOME", str(chome))
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", str(home))
    monkeypatch.delenv("VA_WHISPERX_ENV", raising=False)
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
                   "transcription", "speakers", "deepface"]
    perm = data["items"][6]
    assert perm["state"] == "missing" and perm["step"] == "5-permissions" and perm["rules"] == environment.workspace_rules(home)
    assert data["items"][7]["state"] == "warn"
    wf = data["items"][8]
    assert wf["state"] == "warn" and wf["step"] == "4-agents" and wf["copy"] == "missing"
    assert wf["path"] == (chome / "workflows" / "video-analysis.js").as_posix()
    tx = data["items"][9]
    assert tx["state"] == "ok" and tx["step"] == "6-transcription" and tx["default_model"] == "large-v3-turbo"
    assert tx["env"] == (user / "whisperx-env").as_posix() and tx["expected"] == "3.8.6"
    assert data["items"][10]["state"] == "ok" and data["items"][10]["step"] == "7-speakers"
    df = data["items"][11]
    assert df["state"] == "warn" and df["step"] == "8-webcam timeline" and df["version"] is None
    assert df["env"] == (user / "deepface-env").as_posix() and df["expected"] == "0.0.104"
    assert df["weights_path"] == environment.yunet_weights().as_posix()     # where setup.md says the weights go


def test_paths_only_resolves_the_folders(sandbox, capsys, monkeypatch, tmp_path):
    """--paths, which SKILL.md step 3 runs to find <home>: the two folders and nothing else. No check runs
    (the DeepFace detection can take minutes) and nothing is created."""
    user, chome, home = sandbox
    for name in ("checks", "action_install", "action_permissions", "action_install_deepface",
                 "action_install_transcription"):
        monkeypatch.setattr(CLI, name, lambda *a, **k: pytest.fail("--paths must not check or act"))
    code, out = run(capsys, "--paths")
    assert code == 0 and json.loads(out) == {"home": home.as_posix(), "claude_home": chome.as_posix()}
    assert not home.exists()
    monkeypatch.delenv("VIDEO_ANALYSIS_HOME")
    code, out = run(capsys, "--paths", "--claude-home", tmp_path / "outro")
    assert json.loads(out) == {"home": (user / "video-analysis").as_posix(), "claude_home": (tmp_path / "outro").as_posix()}
    with pytest.raises(SystemExit) as exc:
        run(capsys, "--paths", "--install-agents")
    assert exc.value.code == 2


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
    # the real check, in a process: the sandbox's USERPROFILE has no WhisperX environment
    assert items["transcription"] == "missing" and items["speakers"] == "warn"
    assert data["ready"] is False and r.returncode == 1
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
          "version": "0.0.104", "expected": "0.0.104", "weights": True,
          "weights_path": (user / ".deepface" / "weights" / "face_detection_yunet_2023mar.onnx").as_posix(),
          "detail": f"deepface 0.0.104 in {env}"}
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
    """The DeepFace environment never makes the check fail: [OK], or [WARN] pointing to step 8. The check
    is the deep one: [OK] needs a detection that ran."""
    user, chome, home = sandbox
    asked = []
    monkeypatch.setattr(environment, "deepface_status", lambda **k: asked.append(k) or _deepface(user, **fields))
    code, out = run(capsys, "--install-agents", "--apply-permissions")
    assert code == 0 and out.splitlines()[-1] == "Ready." and asked == [{"deep": True}]
    line = line_of(out, "deepface")
    assert line.startswith("[OK]" if state == "ok" else "[WARN]") and needle in line
    assert line.endswith("-> setup.md, step 8 (webcam timeline)") == (state == "warn")


def test_install_deepface_action(sandbox, tools_ok, capsys, monkeypatch):
    user, chome, home = sandbox
    ok = _deepface(user)
    monkeypatch.setattr(environment, "deepface_status", lambda **k: ok)
    monkeypatch.setattr(environment, "install_deepface",
                        lambda: {"env": ok["env"], "done": ["install deepface 0.0.104"], "status": ok})
    code, out = run(capsys, "--install-deepface", "--json")
    action = json.loads(out)["actions"][0]
    assert item_of(json.loads(out), "deepface")["weights_path"] == ok["weights_path"]
    assert action["id"] == "install-deepface" and action["state"] == "ok" and action["step"] == "8-webcam timeline"
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
    assert action.endswith("-> setup.md, step 8 (webcam timeline)")
    assert out.splitlines()[-1] == "1 item(s) missing: run /video-analysis setup."


# --- the transcription environment and the speakers --------------------------------------------------------

@pytest.mark.parametrize("fields, state, needle", [
    ({}, "ok", "videos without subtitles are transcribed here"),
    ({"ok": False, "ready": False, "state": "absent", "problem": "absent", "version": None,
      "detail": "no transcription environment at C:/x"}, "missing",
     "no transcription environment at C:/x; without it, every video needs a .vtt of the same name"),
    ({"ok": False, "ready": False, "state": "broken", "problem": "version",
      "detail": "C:/x has whisperx 3.7.4, and the skill uses 3.8.6"}, "missing", "(--install-transcription repairs it)"),
    ({"ready": False}, "missing", "but the models are not downloaded yet (--install-transcription downloads them)"),
    ({"ok": False, "ready": False, "state": "unsupported", "problem": "platform",
      "detail": "this Mac has an Intel processor: PyTorch 2.8, which WhisperX 3.8.6 needs, has no build for it"},
     "missing", "this Mac has an Intel processor"),
])
def test_transcription_is_required(sandbox, tools_ok, capsys, monkeypatch, fields, state, needle):
    """Without the transcription environment (or its models) the setup is not done: [MISSING], step 6."""
    user, chome, home = sandbox
    monkeypatch.setattr(environment, "transcription_status", lambda *a, **k: _transcription(user, **fields))
    code, out = run(capsys, "--install-agents", "--apply-permissions")
    line = line_of(out, "transcription")
    assert line.startswith("[OK]" if state == "ok" else "[MISSING]") and needle in line
    assert line.endswith("-> setup.md, step 6 (transcription)") == (state != "ok")
    assert code == (0 if state == "ok" else 1)


@pytest.mark.parametrize("ds, needle", [
    ({"ok": False, "state": "no_token", "detail": "no Hugging Face token saved: the subtitles will not separate the speakers"},
     "and runs & \"C:/w/Scripts/hf.exe\" auth login in their own terminal"),
    ({"ok": False, "state": "no_access", "detail": "the saved token has no access to pyannote/x: accept its conditions at P"},
     "accept its conditions at P"),
    ({"ok": False, "state": "download", "detail": "token with access to pyannote/x, but the model is not downloaded yet"},
     "(--install-transcription downloads it)"),
    ({"ok": False, "state": "unavailable", "detail": "the transcription environment comes first (setup.md, step 6)"},
     "comes first"),
])
def test_speakers_is_a_warning(sandbox, tools_ok, capsys, monkeypatch, ds, needle):
    """No token, no access or no model: the subtitles come out without speakers, a [WARN] pointing to step 7
    that does not fail the check."""
    user, chome, home = sandbox
    base = {"id": "speakers", "model": "pyannote/x", "page": "P", "login_command": '& "C:/w/Scripts/hf.exe" auth login',
            "cached": False, "access": None}
    monkeypatch.setattr(environment, "diarization_status", lambda st=None, **k: {**base, **ds})
    code, out = run(capsys, "--install-agents", "--apply-permissions")
    line = line_of(out, "speakers")
    assert line.startswith("[WARN]") and needle in line and line.endswith("-> setup.md, step 7 (speakers)")
    assert code == 0 and out.splitlines()[-1] == "Ready."
    assert "auth login" in line or ds["state"] != "no_token"


def test_install_transcription_action(sandbox, tools_ok, capsys, monkeypatch):
    user, chome, home = sandbox
    ok = _transcription(user)
    proof = {"ok": True, "device": "cpu", "diarization": "ok"}
    monkeypatch.setattr(environment, "install_transcription", lambda: {
        "env": ok["env"], "done": ["create the environment", "install whisperx 3.8.6 with the cpu PyTorch build"],
        "status": ok, "check": proof})
    code, out = run(capsys, "--install-transcription", "--install-agents", "--apply-permissions", "--json")
    data = json.loads(out)
    action = next(a for a in data["actions"] if a["id"] == "install-transcription")
    assert code == 0 and action["state"] == "ok" and action["step"] == "6-transcription" and action["note"] is None
    assert action["detail"] == ("create the environment; install whisperx 3.8.6 with the cpu PyTorch build: "
                                f"{ok['detail']}")
    # without a token yet: done, with a note pointing to step 7 (shown as a [WARN] line, which does not fail)
    monkeypatch.setattr(environment, "install_transcription", lambda: {
        "env": ok["env"], "done": [], "status": ok, "check": {**proof, "diarization": "no_token"}})
    code, out = run(capsys, "--install-transcription", "--install-agents", "--apply-permissions")
    warn = [l for l in out.splitlines() if l.startswith("[WARN]") and " install-transcription " in l]
    assert code == 0 and line_of(out, "install-transcription").startswith("[OK]")
    assert warn and "no Hugging Face token yet" in warn[0] and warn[0].endswith("-> setup.md, step 7 (speakers)")

    def refuse():
        raise environment.SetupError("could not create the environment C:/x (exit 2): error: No interpreter found")
    monkeypatch.setattr(environment, "install_transcription", refuse)
    code, out = run(capsys, "--install-transcription", "--install-agents", "--apply-permissions")
    action = line_of(out, "install-transcription")
    assert code == 1 and action.startswith("[MISSING]") and "No interpreter found" in action
    assert action.endswith("-> setup.md, step 6 (transcription)")


def test_transcription_built_under_rosetta(sandbox, tools_ok, capsys, monkeypatch):
    """An x86_64 environment on a Mac: [MISSING] with what to do (delete it, step 6 with the arm64 Homebrew),
    not "--install-transcription repairs it", which refuses it."""
    user, chome, home = sandbox
    detail = (f"{(user / 'whisperx-env').as_posix()} is an x86_64 environment (made by an Intel uv or Python, under "
              "Rosetta), and PyTorch 2.8 needs arm64 on a Mac: delete that folder and run step 6 again")
    monkeypatch.setattr(environment, "transcription_status", lambda *a, **k: _transcription(
        user, ok=False, ready=False, state="broken", problem="architecture", machine="x86_64", detail=detail))
    code, out = run(capsys, "--install-agents", "--apply-permissions")
    line = line_of(out, "transcription")
    assert code == 1 and line.startswith("[MISSING]") and detail in line and "repairs it" not in line


def test_transcription_warns_on_another_ctranslate2(sandbox, tools_ok, capsys, monkeypatch):
    """An environment of before the ctranslate2 pin works: [WARN] with both versions and the repair, exit 0."""
    from va import TRANSCRIBE_DEFAULTS
    user, chome, home = sandbox
    pin = TRANSCRIBE_DEFAULTS["ctranslate2_version"]
    monkeypatch.setattr(environment, "transcription_status",
                        lambda *a, **k: _transcription(user, ctranslate2="4.6.0"))
    code, out = run(capsys, "--install-agents", "--apply-permissions")
    line = line_of(out, "transcription")
    assert code == 0 and line.startswith("[WARN]") and out.splitlines()[-1] == "Ready."
    assert f"with CTranslate2 4.6.0 instead of {pin} (--install-transcription repairs it)" in line
    monkeypatch.setattr(environment, "transcription_status", lambda *a, **k: _transcription(user, ctranslate2=pin))
    code, out = run(capsys, "--install-agents", "--apply-permissions", "--json")
    tx = item_of(json.loads(out), "transcription")
    assert code == 0 and tx["state"] == "ok" and tx["ctranslate2"] == pin


# --- macOS: Homebrew off PATH, numpy/Pillow for this Python, an old Python -----------------------------------

@pytest.fixture
def brew(tmp_path, monkeypatch):
    """A Mac whose PATH lacks Homebrew: ffmpeg only in a fake /usr/local/bin."""
    opt, local = tmp_path / "opt-homebrew-bin", tmp_path / "usr-local-bin"
    opt.mkdir()
    local.mkdir()
    (local / "ffmpeg").write_bytes(b"")
    monkeypatch.setattr(environment, "BREW_BIN_DIRS", (str(opt), str(local)))
    monkeypatch.setattr(environment.sys, "platform", "darwin")
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    return opt, local


def test_tool_status_finds_homebrew_off_path(brew, monkeypatch):
    opt, local = brew
    runs = []

    def run_version(cmd, **k):
        runs.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ffmpeg version 8.0 Copyright (c) 2000-2025\n", "")
    monkeypatch.setattr(environment.subprocess, "run", run_version)
    st = environment.tool_status("ffmpeg")
    exe = os.path.join(str(local), "ffmpeg")
    assert st["ok"] and st["path"] == exe and runs[0][0] == exe
    assert st["detail"].startswith(f"ffmpeg 8.0 ({exe}); found in {local}, which is not on PATH")
    assert f'eval "$({local.as_posix()}/brew shellenv)" to ~/.zprofile' in st["detail"]
    missing = environment.tool_status("ffprobe")
    assert (missing["ok"], missing["detail"]) == (False, "ffprobe is not on PATH")
    monkeypatch.setattr(environment.sys, "platform", "win32")                 # elsewhere: only the PATH
    assert environment.brew_tool("ffmpeg") is None and not environment.tool_status("ffmpeg")["ok"]


def test_find_tool_uses_homebrew_off_path(brew, monkeypatch):
    from va import ffmpeg_io
    opt, local = brew
    monkeypatch.setattr(ffmpeg_io, "_TOOLS", {})
    monkeypatch.setattr(ffmpeg_io.shutil, "which", lambda name: None)
    monkeypatch.delenv("VA_FFMPEG", raising=False)
    monkeypatch.delenv("VA_FFPROBE", raising=False)
    assert ffmpeg_io.find_tool("ffmpeg") == os.path.join(str(local), "ffmpeg")
    with pytest.raises(ffmpeg_io.FFmpegNotFound, match="brew install ffmpeg") as exc:
        ffmpeg_io.find_tool("ffprobe")
    assert "brew shellenv" in str(exc.value)


def test_libs_hint_installs_into_this_python():
    exe = "/opt/homebrew/opt/python@3.13/bin/python3.13"
    hint = CLI.libs_hint(mac=True, exe=exe)
    assert f'"{exe}" -m pip install --user --break-system-packages numpy Pillow' in hint
    assert f'"{exe}" -m venv' in hint and "brew install numpy" not in hint and hint.startswith(f" (for this Python, {exe}:")
    assert CLI.libs_hint(mac=False) == " (python -m pip install numpy Pillow)"
    assert sys.executable in CLI.libs_hint(mac=True)


def test_old_python_stops_check_environment_and_its_old_name(monkeypatch, capsys):
    import runpy
    monkeypatch.setattr(sys, "version_info", (3, 9, 6, "final", 0))
    monkeypatch.setattr(environment.sys, "platform", "darwin")
    for script in ("check_environment.py", "verificar_ambiente.py"):
        monkeypatch.setattr(sys, "argv", [str(SKILL / script), "--json"])
        with pytest.raises(SystemExit) as stop:
            runpy.run_path(str(SKILL / script), run_name="__main__")
        out = capsys.readouterr().out.strip()
        assert stop.value.code == 1 and len(out.splitlines()) == 1
        assert out.startswith("video-analysis needs Python 3.10 or newer, and this is Python 3.9.6 (")
        assert out.endswith("brew install python, with /opt/homebrew/bin first on PATH, then run it with python3 again")


# --- permissions outside the home folder (macOS and Linux) -----------------------------------------------------

@pytest.fixture
def posix_paths(monkeypatch):
    import posixpath
    monkeypatch.setattr(environment, "_ospath", posixpath)


def test_rules_for_a_workspace_on_an_external_disk(posix_paths, tmp_path):
    rules = environment.workspace_rules("/Volumes/SSD/video-analysis", uhome="/Users/ana")
    assert rules == [f"{t}(//Volumes/SSD/video-analysis/**)" for t in ("Read", "Edit", "Write")]
    assert environment.workspace_rules("/Users/ana/video-analysis", uhome="/Users/ana") == [
        f"{t}(~/video-analysis/**)" for t in ("Read", "Edit", "Write")]
    chome = tmp_path / "claude"
    chome.mkdir()
    settings = chome / "settings.json"
    settings.write_text(json.dumps({"permissions": {"allow": rules}}), encoding="utf-8")
    st = environment.permissions_status("/Volumes/SSD/video-analysis", chome, uhome="/Users/ana")
    assert st["ok"] and st["covered"]["Read"] == "Read(//Volumes/SSD/video-analysis/**)"
    # an ancestor in the absolute spelling covers it; a single slash is relative to the settings file
    settings.write_text(json.dumps({"permissions": {"allow": ["Read(//Volumes/**)",
                                                              "Edit(/Volumes/SSD/video-analysis/**)"]}}), encoding="utf-8")
    st = environment.permissions_status("/Volumes/SSD/video-analysis", chome, uhome="/Users/ana")
    assert st["covered"]["Read"] == "Read(//Volumes/**)" and st["missing"] == ["Edit", "Write"]
    settings.write_text(json.dumps({"permissions": {"allow": ["Read(~/video-analysis/**)"],
                                                    "deny": ["Edit(//Volumes/SSD/**)"]}}), encoding="utf-8")
    assert environment.permissions_status("/Volumes/SSD/video-analysis", chome, uhome="/Users/ana")["blocked"] == [
        "deny: Edit(//Volumes/SSD/**)"]


def test_apply_permissions_without_rules_is_an_error(sandbox, tools_ok, capsys, monkeypatch):
    """A folder no rule can name (a network path) must not read as "already in place"."""
    user, chome, home = sandbox
    monkeypatch.setattr(environment, "workspace_rules", lambda *a, **k: [])
    code, out = run(capsys, "--apply-permissions", "--install-agents")
    line = line_of(out, "apply-permissions")
    assert code == 1 and line.startswith("[MISSING]") and "no permission rule can name" in line
    assert "already" not in line and not (chome / "settings.json").exists()
    assert "--apply-permissions merges" not in line_of(out, "permissions")
