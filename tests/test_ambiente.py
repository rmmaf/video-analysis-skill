"""va.ambiente: permission-rule coverage, settings merge with backup, agents and saved-workflow copies,
runtime checks.
Everything runs against temporary homes: the real ~/.claude is never read or written here."""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from va import ambiente as A
from va.common import as_posix

PLAN_RULES = [
    "Read(~/video-analysis/**)", "Edit(~/video-analysis/**)", "Write(~/video-analysis/**)",
    "Read(//c/Users/digom/video-analysis/**)", "Edit(//c/Users/digom/video-analysis/**)", "Write(//c/Users/digom/video-analysis/**)",
]


@pytest.fixture
def homes(tmp_path, monkeypatch):
    user = tmp_path / "user"
    chome, ws = user / ".claude", user / "video-analysis"
    chome.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv(A.CLAUDE_HOME_ENV, str(chome))
    return user, chome, ws


def settings(chome: Path, allow=None, deny=None, ask=None, extra=None):
    perms = {}
    if allow is not None:
        perms["allow"] = allow
    if deny is not None:
        perms["deny"] = deny
    if ask is not None:
        perms["ask"] = ask
    data = {**(extra or {}), "permissions": perms}
    (chome / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def slash_form(p: Path) -> str:
    drive, rest = os.path.splitdrive(str(p))
    return f"//{drive[0].lower()}/{rest.replace(os.sep, '/').strip('/')}"


# --- homes --------------------------------------------------------------------------------------

def test_claude_home_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "u"))
    monkeypatch.delenv(A.CLAUDE_HOME_ENV, raising=False)
    assert A.claude_home() == tmp_path / "u" / ".claude"
    monkeypatch.setenv(A.CLAUDE_HOME_ENV, f'"{tmp_path / "env"}"')
    assert A.claude_home() == tmp_path / "env"
    assert A.claude_home(tmp_path / "cli") == tmp_path / "cli"
    assert A.claude_home("  ") == tmp_path / "env"


# --- rules ---------------------------------------------------------------------------------------

def test_workspace_rules_for_the_plan_path():
    assert A.workspace_rules(r"C:\Users\digom\video-analysis", r"C:\Users\digom") == PLAN_RULES


def test_workspace_rules_outside_home(tmp_path):
    rules = A.workspace_rules(r"D:\dados\va", r"C:\Users\digom")
    assert rules == ["Read(//d/dados/va/**)", "Edit(//d/dados/va/**)", "Write(//d/dados/va/**)"]


def test_permissions_plan_rules_cover(homes):
    user, chome, ws = homes
    settings(chome, allow=A.workspace_rules(ws), extra={"enabledPlugins": {"x": True}})
    st = A.permissions_status(ws, chome)
    assert st["ok"] and st["missing"] == [] and st["blocked"] == []
    assert st["covered"]["Read"] == f"Read(~/{ws.name}/**)"


@pytest.mark.parametrize("make_allow, ok, missing", [
    (lambda ws: [f"Read(~/{ws.name}/**)", f"Edit(~/{ws.name}/**)", f"Write(~/{ws.name}/**)"], True, []),
    (lambda ws: [f"Read({slash_form(ws)}/**)", f"Edit({slash_form(ws)}/**)"], True, []),        # Edit covers Write
    (lambda ws: [f"Read({slash_form(ws).replace('//c/', '//C/')}/**)", f"Edit({slash_form(ws).upper()}/**)"], True, []),
    (lambda ws: [f"Read({slash_form(ws).replace('//c/', '//C:/')}/**)", f"Edit({as_posix(ws)}/**)"],
     False, ["Read", "Edit", "Write"]),                                                          # not Claude Code syntax
    (lambda ws: [f"Read({str(ws)}\\**)", "Edit(~\\video-analysis\\**)"], False, ["Read", "Edit", "Write"]),
    (lambda ws: ["Read(~/**)", "Edit(~/**)"], True, []),                                           # ancestor folder
    (lambda ws: ["Read", "Edit"], True, []),                                                       # bare tool names
    (lambda ws: [f"Read(~/{ws.name}/*)", f"Edit(~/{ws.name}/**)"], False, ["Read"]),               # one level only
    (lambda ws: [f"Read(~/{ws.name}/**)", f"Write(~/{ws.name}/**)"], False, ["Edit"]),             # Write does not cover Edit
    (lambda ws: [f"Read(~/outra/**)", "Edit(src/**)", "Bash(git *)"], False, ["Read", "Edit", "Write"]),
    (lambda ws: [f"Read(~/{ws.name[:5]}*/**)", f"Edit(~/{ws.name}/**)"], True, []),               # glob in the base
    (lambda ws: [], False, ["Read", "Edit", "Write"]),
])
def test_permissions_coverage_variants(homes, make_allow, ok, missing):
    user, chome, ws = homes
    settings(chome, allow=make_allow(ws))
    st = A.permissions_status(ws, chome)
    assert st["ok"] is ok and st["missing"] == missing, st


def test_permissions_deny_and_ask_block(homes):
    user, chome, ws = homes
    settings(chome, allow=A.workspace_rules(ws), deny=[f"Edit(~/{ws.name}/**)"])
    st = A.permissions_status(ws, chome)
    assert not st["ok"] and st["blocked"] == [f"deny: Edit(~/{ws.name}/**)"] and "blocks" in st["detail"]
    settings(chome, allow=A.workspace_rules(ws), ask=["Read(~/**)"])
    assert not A.permissions_status(ws, chome)["ok"]
    settings(chome, allow=A.workspace_rules(ws), deny=[f"Read(~/{ws.name}/tmp/**)"])
    st = A.permissions_status(ws, chome)
    assert st["ok"] and st["notes"]                                  # a deny inside the folder is only noted


def test_permissions_missing_or_bad_settings(homes):
    user, chome, ws = homes
    st = A.permissions_status(ws, chome)
    assert not st["ok"] and "does not exist" in st["detail"]
    (chome / "settings.json").write_text("{quebrado", encoding="utf-8")
    assert "unreadable" in A.permissions_status(ws, chome)["detail"]
    (chome / "settings.json").write_text("[]", encoding="utf-8")
    assert "object" in A.permissions_status(ws, chome)["detail"]
    (chome / "settings.json").write_text('{"permissions": {"allow": "Read"}}', encoding="utf-8")
    assert not A.permissions_status(ws, chome)["ok"]


def test_permissions_default_claude_home_from_env(homes):
    user, chome, ws = homes
    settings(chome, allow=A.workspace_rules(ws))
    assert A.permissions_status(ws)["ok"]                            # VA_CLAUDE_HOME / USERPROFILE of the sandbox


# --- merge -----------------------------------------------------------------------------------------

def test_merge_permissions_backup_keeps_keys_and_is_idempotent(homes):
    user, chome, ws = homes
    original = {"enabledPlugins": {"a@b": True}, "effortLevel": "high",
                "permissions": {"allow": ["Bash(git status)"], "deny": ["Read(./.env)"]}, "theme": "dark"}
    path = chome / "settings.json"
    path.write_text(json.dumps(original, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    before = path.read_bytes()
    rules = A.workspace_rules(ws)
    r = A.merge_permissions(path, rules, now=1_700_000_000)
    assert r["changed"] and r["added"] == rules
    backups = list(chome.glob("settings.json.bak-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert re.fullmatch(r"settings\.json\.bak-\d{8}-\d{6}", backups[0].name)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data) == list(original)
    assert data["permissions"]["allow"] == ["Bash(git status)", *rules]
    assert data["permissions"]["deny"] == ["Read(./.env)"] and data["enabledPlugins"] == {"a@b": True}
    assert path.read_text(encoding="utf-8").startswith('{\n  "enabledPlugins"')        # indentation kept
    assert A.permissions_status(ws, chome)["ok"]
    after = path.read_bytes()
    r2 = A.merge_permissions(path, rules, now=1_700_000_100)
    assert r2 == {"changed": False, "added": [], "backup": None, "path": as_posix(path)}
    assert path.read_bytes() == after and len(list(chome.glob("settings.json.bak-*"))) == 1


def test_merge_permissions_without_permissions_block_and_new_file(homes):
    user, chome, ws = homes
    path = chome / "settings.json"
    path.write_text('{"enabledPlugins": {}}', encoding="utf-8")
    r = A.merge_permissions(path, PLAN_RULES)
    assert r["changed"] and json.loads(path.read_text(encoding="utf-8")) == {"enabledPlugins": {}, "permissions": {"allow": PLAN_RULES}}
    new = chome / "sub" / "settings.json"
    r = A.merge_permissions(new, PLAN_RULES[:1])
    assert r["changed"] and r["backup"] is None and json.loads(new.read_text(encoding="utf-8"))["permissions"]["allow"] == PLAN_RULES[:1]


def test_merge_permissions_backup_name_collision(homes):
    user, chome, ws = homes
    path = chome / "settings.json"
    path.write_text("{}", encoding="utf-8")
    A.merge_permissions(path, ["Read(~/a/**)"], now=1_700_000_000)
    A.merge_permissions(path, ["Read(~/b/**)"], now=1_700_000_000)
    assert len(list(chome.glob("settings.json.bak-*"))) == 2


@pytest.mark.parametrize("content", ["{quebrado", "[]", '{"permissions": []}', '{"permissions": {"allow": "x"}}'])
def test_merge_permissions_refuses_bad_files(homes, content):
    user, chome, ws = homes
    path = chome / "settings.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(A.AmbienteError):
        A.merge_permissions(path, PLAN_RULES)
    assert path.read_text(encoding="utf-8") == content and not list(chome.glob("settings.json.bak-*"))


# --- agents ------------------------------------------------------------------------------------------

def test_agents_status_and_install(homes, skill_dir):
    user, chome, ws = homes
    st = A.agents_status(skill_dir, chome)
    assert not st["current"] and {f["state"] for f in st["files"]} == {"ausente"} and len(st["files"]) == 2
    r = A.install_agents(skill_dir, chome)
    assert sorted(r["copied"]) == ["video-analysis-analyst.md", "video-analysis-curator.md"] and r["unchanged"] == []
    assert A.agents_status(skill_dir, chome)["current"]
    for f in (skill_dir / "agents").glob("*.md"):
        assert (chome / "agents" / f.name).read_bytes() == f.read_bytes()
    assert A.install_agents(skill_dir, chome)["copied"] == []
    (chome / "agents" / "video-analysis-analyst.md").write_text("antigo", encoding="utf-8")
    st = A.agents_status(skill_dir, chome)
    assert not st["current"] and "diferente" in st["detail"]
    assert A.install_agents(skill_dir, chome)["copied"] == ["video-analysis-analyst.md"]


# --- saved workflow ------------------------------------------------------------------------------------

def test_workflow_status_and_install(homes, skill_dir):
    user, chome, ws = homes
    src = skill_dir / "workflow" / "video-analysis.js"
    target = chome / "workflows" / "video-analysis.js"
    st = A.workflow_status(skill_dir, chome)
    assert st == {"id": "workflow", "current": False, "path": as_posix(target), "state": "ausente",
                  "detail": f"video-analysis.js ausente in {as_posix(target.parent)}"}
    assert not (chome / "workflows").exists()                         # the status only reads
    assert A.install_workflow(skill_dir, chome) == {"path": as_posix(target), "copied": True}
    assert target.read_bytes() == src.read_bytes()
    st = A.workflow_status(skill_dir)                                 # default Claude home: VA_CLAUDE_HOME of the sandbox
    assert st["current"] and st["state"] == "ok" and st["path"] == as_posix(target) and "identical" in st["detail"]
    assert A.install_workflow(skill_dir, chome)["copied"] is False
    target.write_text("export const meta = {name: 'video-analysis'}  // antigo", encoding="utf-8")
    st = A.workflow_status(skill_dir, chome)
    assert not st["current"] and st["state"] == "diferente" and "diferente" in st["detail"]
    assert A.install_workflow(skill_dir, chome)["copied"] is True and target.read_bytes() == src.read_bytes()
    assert sorted(p.name for p in target.parent.iterdir()) == ["video-analysis.js"]   # no temp file left


def test_workflow_status_without_the_skill_script(homes, tmp_path):
    user, chome, ws = homes
    st = A.workflow_status(tmp_path / "skill-vazia", chome)
    assert not st["current"] and st["state"] == "sem-fonte" and "workflow/video-analysis.js" in st["detail"]
    with pytest.raises(OSError):
        A.install_workflow(tmp_path / "skill-vazia", chome)
    assert not (chome / "workflows").exists()                         # nothing created when there is no source


# --- runtime --------------------------------------------------------------------------------------------

def test_python_status():
    st = A.python_status()
    assert st["ok"] and re.fullmatch(r"\d+\.\d+\.\d+", st["version"])


def test_tool_status_missing_and_failing(monkeypatch):
    monkeypatch.setattr(A.shutil, "which", lambda name: None)
    st = A.tool_status("ffmpeg")
    assert not st["ok"] and "PATH" in st["detail"]
    monkeypatch.setattr(A.shutil, "which", lambda name: "C:/x/ffmpeg.exe")

    def run_fail(*a, **k):
        return subprocess.CompletedProcess(a, 1, stdout="", stderr="erro")
    monkeypatch.setattr(A.subprocess, "run", run_fail)
    assert not A.tool_status("ffmpeg")["ok"]

    def run_ok(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout="ffmpeg version 8.1.2-full Copyright\nmore", stderr="")
    monkeypatch.setattr(A.subprocess, "run", run_ok)
    st = A.tool_status("ffmpeg")
    assert st["ok"] and st["detail"].startswith("ffmpeg 8.1.2-full") and st["version"].startswith("ffmpeg version 8.1.2")

    def run_raise(*a, **k):
        raise OSError("acesso negado")
    monkeypatch.setattr(A.subprocess, "run", run_raise)
    assert "acesso negado" in A.tool_status("ffprobe")["detail"]


def test_module_status_and_runtime_problems(monkeypatch):
    assert A.module_status("numpy")["ok"]
    monkeypatch.setitem(A.MODULES, "pillow", ("modulo_que_nao_existe_xyz", "Pillow"))
    st = A.module_status("pillow")
    assert not st["ok"] and "Pillow" in st["detail"]
    monkeypatch.setattr(A, "tool_status", lambda name, timeout=15.0: {"id": name, "ok": name != "ffprobe", "detail": f"{name}?"})
    probs = A.runtime_problems()
    assert [(p["id"], p["code"]) for p in probs] == [("ffprobe", "ffmpeg_ausente"), ("pillow", "dependencia_ausente")]
    assert A.runtime_problems(("ffmpeg", "numpy")) == []



def test_runtime_problems_skips_the_package_metadata_scan(monkeypatch):
    """prepare.py only needs ok / not ok: importlib.metadata (hundreds of ms per run) is left to
    verificar_ambiente.py, which shows the versions."""
    from importlib import metadata

    def boom(*a, **k):
        raise AssertionError("importlib.metadata.version must not be called by runtime_problems")
    monkeypatch.setattr(metadata, "version", boom)
    monkeypatch.setattr(A, "tool_status", lambda name, timeout=15.0: {"id": name, "ok": True, "detail": name})
    assert A.runtime_problems() == []
    st = A.module_status("numpy", version=False)
    assert st["ok"] and st["version"] is None and st["detail"] == "numpy"

def test_workspace_status(tmp_path):
    ws = tmp_path / "ws"
    st = A.workspace_status(ws)
    assert st["ok"] and all((ws / d).is_dir() for d in ("cache", "runs", "requests", "roles", "logs", "tmp"))
    assert not list((ws / "tmp").iterdir())
    blocker = tmp_path / "arquivo"
    blocker.write_text("x", encoding="utf-8")
    assert not A.workspace_status(blocker)["ok"]
    assert not A.workspace_status(tmp_path / "nao", create=False)["ok"]
