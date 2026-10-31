"""Environment checks shared by prepare.py and verificar_ambiente.py (design-agentico §7, DL-6,
DL-13; plan, "Permissões e setup").

- Runtime: Python >= 3.10, ffmpeg and ffprobe on PATH, numpy and Pillow importable.
- Workspace: the central folder and its subfolders exist (created on demand) and accept writes.
- Permissions: ``<claude home>/settings.json`` allow rules cover Read, Edit and Write for the
  workspace. Accepted spellings: ``Tool(~/video-analysis/**)`` and ``Tool(//c/Users/x/video-analysis/**)``
  (also an ancestor folder with ``/**``, or the bare tool name). An ``Edit`` rule also covers
  ``Write`` (Claude Code applies Edit rules to every file-editing tool). A deny or ask rule that
  covers the workspace makes it not OK.
- Agents: ``<claude home>/agents/<role>.md`` byte-identical to ``<skill>/agents/<role>.md``.
- Saved workflow: ``<claude home>/workflows/video-analysis.js`` byte-identical to
  ``<skill>/workflow/video-analysis.js``, so the Workflow tool runs it by name (``name: "video-analysis"``)
  instead of receiving the script inline.

The Claude home is ``--claude-home`` / env ``VA_CLAUDE_HOME`` / ``%USERPROFILE%\\.claude``: tests must
always point it somewhere else, so the real settings are never touched.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import WORKSPACE_DIRS
from .common import as_posix, atomic_write_bytes

__all__ = [
    "CLAUDE_HOME_ENV", "MIN_PYTHON", "RULE_TOOLS", "WORKFLOW_SCRIPT", "AmbienteError",
    "agents_status", "claude_home", "install_agents", "install_workflow", "merge_permissions",
    "module_status", "permissions_status", "python_status", "role_files", "runtime_problems",
    "tool_status", "user_home", "workflow_source", "workflow_status", "workspace_rules",
    "workspace_status",
]

CLAUDE_HOME_ENV = "VA_CLAUDE_HOME"
MIN_PYTHON = (3, 10)
RULE_TOOLS = ("Read", "Edit", "Write")       # order of the 6 rules in the plan
WORKFLOW_SCRIPT = "video-analysis.js"        # <skill>/workflow/<this> -> <claude home>/workflows/<this>
MODULES = {"numpy": ("numpy", "numpy"), "pillow": ("PIL", "Pillow")}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AmbienteError(RuntimeError):
    """A setup action could not be done safely (nothing was written)."""


# --- homes ----------------------------------------------------------------------------------------

def user_home() -> Path:
    return Path(os.path.abspath(os.environ.get("USERPROFILE") or os.path.expanduser("~")))


def claude_home(override: str | os.PathLike | None = None) -> Path:
    """``override`` (--claude-home) > env VA_CLAUDE_HOME > %USERPROFILE%\\.claude."""
    for value in (os.fspath(override) if override is not None else "", os.environ.get(CLAUDE_HOME_ENV, "")):
        value = value.strip().strip('"').strip()
        if value:
            return Path(os.path.abspath(os.path.expanduser(value)))
    return user_home() / ".claude"


# --- runtime --------------------------------------------------------------------------------------

def python_status() -> dict:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    version = f"{v.major}.{v.minor}.{v.micro}"
    return {"id": "python", "ok": ok, "version": version, "path": sys.executable,
            "detail": f"Python {version} ({sys.executable})" if ok else f"Python {version}: a skill precisa de 3.10 ou mais novo"}


def tool_status(name: str, timeout: float = 15.0) -> dict:
    """``ffmpeg`` / ``ffprobe``: on PATH and ``-version`` runs."""
    path = shutil.which(name)
    if not path:
        return {"id": name, "ok": False, "version": None, "path": None, "detail": f"{name} não está no PATH"}
    try:
        r = subprocess.run([path, "-hide_banner", "-version"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"id": name, "ok": False, "version": None, "path": path, "detail": f"{name} não executou: {exc}"}
    first = (r.stdout or r.stderr or "").strip().splitlines()[:1]
    version = first[0].strip() if first else ""
    if r.returncode != 0:
        return {"id": name, "ok": False, "version": version or None, "path": path,
                "detail": f"{name} -version saiu com {r.returncode}"}
    short = re.search(r"version\s+(\S+)", version)
    return {"id": name, "ok": True, "version": version, "path": path,
            "detail": f"{name} {short.group(1) if short else version} ({path})"}


def module_status(key: str, version: bool = True) -> dict:
    """``numpy`` / ``pillow``: importable, with the installed version. ``version=False`` skips the
    package-metadata lookup (a few hundred ms; prepare.py only needs ok / not ok)."""
    import_name, dist = MODULES[key]
    try:
        spec = importlib.util.find_spec(import_name)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return {"id": key, "ok": False, "version": None, "detail": f"{dist} não está instalado para {sys.executable}"}
    if not version:
        return {"id": key, "ok": True, "version": None, "detail": dist}
    from importlib import metadata  # lazy: only verificar_ambiente.py shows the versions
    try:
        found = metadata.version(dist)
    except metadata.PackageNotFoundError:
        found = "?"
    return {"id": key, "ok": True, "version": found, "detail": f"{dist} {found}"}


def runtime_problems(need: tuple[str, ...] = ("ffmpeg", "ffprobe", "numpy", "pillow")) -> list[dict]:
    """Failing runtime checks among ``need``, each with the prepare.py error code
    (``ffmpeg_ausente`` or ``dependencia_ausente``)."""
    out = []
    for item in need:
        st = tool_status(item) if item in ("ffmpeg", "ffprobe") else module_status(item, version=False)
        if not st["ok"]:
            st["code"] = "ffmpeg_ausente" if item in ("ffmpeg", "ffprobe") else "dependencia_ausente"
            out.append(st)
    return out


def workspace_status(home: str | os.PathLike, create: bool = True) -> dict:
    """The central folder and its subfolders exist (created when ``create``) and accept writes."""
    home = Path(home)
    try:
        if create:
            for sub in WORKSPACE_DIRS.values():
                (home / sub).mkdir(parents=True, exist_ok=True)
        if not home.is_dir():
            return {"id": "pasta-central", "ok": False, "detail": f"{as_posix(home)} não existe (é criada no primeiro uso)"}
        probe = home / WORKSPACE_DIRS["tmp"] / f".escrita-{os.getpid()}"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return {"id": "pasta-central", "ok": False, "detail": f"{as_posix(home)} não aceita gravação: {exc}"}
    return {"id": "pasta-central", "ok": True, "detail": f"{as_posix(home)} aceita gravação"}


# --- permission rules -------------------------------------------------------------------------------

def _norm(p: str) -> str:
    """Comparable form: forward slashes, no trailing slash, case-folded (Windows paths)."""
    s = p.replace("\\", "/").rstrip("/")
    return s.casefold() if os.name == "nt" or re.match(r"^[A-Za-z]:", s) else s


def workspace_rules(home: str | os.PathLike, uhome: str | os.PathLike | None = None) -> list[str]:
    """The allow rules for the workspace, derived from its real path: ``Tool(~/<rel>/**)`` when it
    is under the user's home, and ``Tool(//<drive>/<path>/**)``. For C:\\Users\\digom\\video-analysis
    these are exactly the 6 rules of the plan."""
    ws = Path(os.path.abspath(home))
    uh = Path(os.path.abspath(uhome)) if uhome is not None else user_home()
    rules = []
    ws_n, uh_n = _norm(str(ws)), _norm(str(uh))
    if ws_n.startswith(uh_n + "/"):
        rel = str(ws).replace("\\", "/")[len(str(uh).replace("\\", "/").rstrip("/")) + 1:]
        rules += [f"{t}(~/{rel}/**)" for t in RULE_TOOLS]
    drive, rest = os.path.splitdrive(str(ws))
    if re.fullmatch(r"[A-Za-z]:", drive):
        path = rest.replace("\\", "/").strip("/")
        rules += [f"{t}(//{drive[0].lower()}/{path}/**)" for t in RULE_TOOLS]
    return rules


_RULE_RE = re.compile(r"^\s*([A-Za-z][\w-]*)\s*(?:\((.*)\))?\s*$", re.DOTALL)


def _rule_base(pattern: str, uhome: Path, settings_dir: Path) -> str | None:
    """Absolute, comparable base of a ``…/**`` path pattern; None when it is not one.

    Claude Code's spellings only: ``~/…`` (user home), ``//c/…`` (absolute, Windows drive as a
    folder) and ``/…`` (relative to the settings file). ``C:/…`` or ``//C:/…`` would be read as
    relative paths by Claude Code, so they do not count."""
    p = pattern.strip()
    if "\\" in p or not p.endswith("/**"):
        return None
    p = p[:-3]
    if p in ("~", "") or p.startswith("~/"):
        base = str(uhome).replace("\\", "/").rstrip("/") + p[1:]
    elif p.startswith("//"):
        m = re.match(r"^//([A-Za-z])(?=/|$)", p)
        if not m:
            return None
        base = f"{m.group(1)}:{p[m.end():] or '/'}"
    elif p.startswith("/"):
        base = str(settings_dir).replace("\\", "/").rstrip("/") + p  # relative to the settings file
    else:
        return None  # project-relative pattern: says nothing about the workspace
    return _norm(base)


def _covers(rule: str, tool: str, ws_n: str, uhome: Path, settings_dir: Path) -> bool:
    m = _RULE_RE.match(rule) if isinstance(rule, str) else None
    if not m or m.group(1) != tool:
        return False
    if m.group(2) is None:
        return True  # bare tool name: every path
    base = _rule_base(m.group(2), uhome, settings_dir)
    if base is None:
        return False
    if any(c in base for c in "*?["):
        parts = ws_n.split("/")
        return any(fnmatch.fnmatchcase("/".join(parts[:i]), base) for i in range(1, len(parts) + 1))
    return ws_n == base or ws_n.startswith(base.rstrip("/") + "/")


def _inside(rule: str, tool: str, ws_n: str, uhome: Path, settings_dir: Path) -> bool:
    m = _RULE_RE.match(rule) if isinstance(rule, str) else None
    if not m or m.group(1) != tool or m.group(2) is None:
        return False
    p = m.group(2).strip()
    base = _rule_base(p if p.endswith("/**") else p.rstrip("/") + "/**", uhome, settings_dir)
    return bool(base) and base.startswith(ws_n + "/")


def _load_settings(path: Path) -> tuple[dict | None, str | None]:
    if not path.is_file():
        return None, f"{as_posix(path)} não existe"
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError) as exc:
        return None, f"{as_posix(path)} ilegível: {exc}"
    if not isinstance(data, dict):
        return None, f"{as_posix(path)} não é um objeto JSON"
    return data, None


def permissions_status(workspace: str | os.PathLike, chome: str | os.PathLike | None = None,
                       uhome: str | os.PathLike | None = None) -> dict:
    """Do the user settings allow Read, Edit and Write on the workspace without prompting?"""
    ch = Path(chome) if chome is not None else claude_home()
    uh = Path(os.path.abspath(uhome)) if uhome is not None else user_home()
    path = ch / "settings.json"
    ws_n = _norm(os.path.abspath(workspace))
    out = {"id": "permissoes", "ok": False, "settings_path": as_posix(path), "covered": dict.fromkeys(RULE_TOOLS),
           "missing": list(RULE_TOOLS), "blocked": [], "notes": [], "detail": ""}
    data, err = _load_settings(path)
    if err:
        out["detail"] = err
        return out
    perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    lists = {k: [r for r in (perms.get(k) or []) if isinstance(r, str)] if isinstance(perms.get(k), list) else []
             for k in ("allow", "deny", "ask")}
    for tool in RULE_TOOLS:
        hit = next((r for r in lists["allow"] if _covers(r, tool, ws_n, uh, ch)), None)
        if hit is None and tool == "Write":
            hit = next((r for r in lists["allow"] if _covers(r, "Edit", ws_n, uh, ch)), None)
        out["covered"][tool] = hit
        for kind in ("deny", "ask"):
            for r in lists[kind]:
                if _covers(r, tool, ws_n, uh, ch):
                    out["blocked"].append(f"{kind}: {r}")
                elif _inside(r, tool, ws_n, uh, ch):
                    out["notes"].append(f"{kind} dentro da pasta central: {r}")
    out["missing"] = [t for t in RULE_TOOLS if out["covered"][t] is None]
    out["ok"] = not out["missing"] and not out["blocked"]
    if out["ok"]:
        out["detail"] = "Read, Edit e Write liberados para " + as_posix(os.path.abspath(workspace))
    elif out["blocked"]:
        out["detail"] = "regra que bloqueia a pasta central: " + "; ".join(out["blocked"])
    else:
        out["detail"] = f"faltam regras {', '.join(out['missing'])} para {as_posix(os.path.abspath(workspace))} em {as_posix(path)}"
    return out


def _indent_of(text: str) -> int | str:
    for line in text.splitlines()[1:]:
        m = re.match(r"^([ \t]+)\S", line)
        if m:
            ws = m.group(1)
            return "\t" if ws.startswith("\t") else len(ws)
    return 2


def merge_permissions(settings_path: str | os.PathLike, rules: list[str], now: float | None = None) -> dict:
    """Add the missing ``rules`` to ``permissions.allow``, keeping every other key and the existing
    rules as they are. Idempotent: nothing is written (and no backup made) when all are present.
    Before writing an existing file, copies it to ``settings.json.bak-AAAAMMDD-HHMMSS``.
    Raises AmbienteError (and writes nothing) when the file is not a valid JSON object."""
    path = Path(settings_path)
    raw = None
    if path.exists():
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
            data = json.loads(text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise AmbienteError(f"{as_posix(path)} não é JSON válido ({exc}); nada foi gravado") from None
        if not isinstance(data, dict):
            raise AmbienteError(f"{as_posix(path)} não é um objeto JSON; nada foi gravado")
        indent, final_nl = _indent_of(text), text.endswith("\n")
    else:
        data, indent, final_nl = {}, 2, True
    perms = data.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise AmbienteError(f"'permissions' em {as_posix(path)} não é um objeto; nada foi gravado")
    allow = perms.setdefault("allow", [])
    if not isinstance(allow, list):
        raise AmbienteError(f"'permissions.allow' em {as_posix(path)} não é uma lista; nada foi gravado")
    added = [r for r in rules if r not in allow]
    if not added:
        return {"changed": False, "added": [], "backup": None, "path": as_posix(path)}
    allow.extend(added)
    backup = None
    if raw is not None:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
        candidate = path.with_name(f"{path.name}.bak-{stamp}")
        n = 2
        while candidate.exists():
            candidate = path.with_name(f"{path.name}.bak-{stamp}-{n}")
            n += 1
        candidate.write_bytes(raw)
        backup = as_posix(candidate)
    out_text = json.dumps(data, ensure_ascii=False, indent=indent) + ("\n" if final_nl else "")
    atomic_write_bytes(path, out_text.encode("utf-8"))
    return {"changed": True, "added": added, "backup": backup, "path": as_posix(path)}


# --- agents -----------------------------------------------------------------------------------------

def role_files(skill_dir: str | os.PathLike) -> list[Path]:
    return sorted((Path(skill_dir) / "agents").glob("*.md"), key=lambda p: p.name.lower())


def agents_status(skill_dir: str | os.PathLike, chome: str | os.PathLike | None = None) -> dict:
    """``current``: every ``<skill>/agents/*.md`` has a byte-identical copy in ``<claude home>/agents``."""
    ch = Path(chome) if chome is not None else claude_home()
    dest = ch / "agents"
    files = []
    for src in role_files(skill_dir):
        target = dest / src.name
        try:
            state = "ok" if target.read_bytes() == src.read_bytes() else "diferente"
        except FileNotFoundError:
            state = "ausente"
        except OSError:
            state = "ilegivel"
        files.append({"name": src.name, "state": state})
    current = bool(files) and all(f["state"] == "ok" for f in files)
    if current:
        detail = f"{len(files)} papéis idênticos aos da skill em {as_posix(dest)}"
    elif not files:
        detail = "a skill não tem papéis em agents/"
    else:
        detail = "; ".join(f"{f['name']} {f['state']}" for f in files if f["state"] != "ok") + f" em {as_posix(dest)}"
    return {"id": "agentes", "current": current, "dir": as_posix(dest), "files": files, "detail": detail}


def install_agents(skill_dir: str | os.PathLike, chome: str | os.PathLike | None = None) -> dict:
    """Create ``<claude home>/agents`` and copy the role files (skipping identical ones)."""
    ch = Path(chome) if chome is not None else claude_home()
    dest = ch / "agents"
    dest.mkdir(parents=True, exist_ok=True)
    copied, unchanged = [], []
    for src in role_files(skill_dir):
        data = src.read_bytes()
        target = dest / src.name
        try:
            if target.read_bytes() == data:
                unchanged.append(src.name)
                continue
        except OSError:
            pass
        atomic_write_bytes(target, data)
        copied.append(src.name)
    return {"dir": as_posix(dest), "copied": copied, "unchanged": unchanged}


# --- saved workflow -----------------------------------------------------------------------------------

def workflow_source(skill_dir: str | os.PathLike) -> Path:
    return Path(skill_dir) / "workflow" / WORKFLOW_SCRIPT


def workflow_status(skill_dir: str | os.PathLike, chome: str | os.PathLike | None = None) -> dict:
    """``current``: ``<claude home>/workflows/video-analysis.js`` is byte-identical to the skill's
    ``workflow/video-analysis.js``. ``state``: ok, ausente, diferente, ilegivel, or sem-fonte (the
    skill itself has no script)."""
    ch = Path(chome) if chome is not None else claude_home()
    target = ch / "workflows" / WORKFLOW_SCRIPT
    out = {"id": "workflow", "current": False, "path": as_posix(target), "state": "sem-fonte", "detail": ""}
    try:
        data = workflow_source(skill_dir).read_bytes()
    except OSError:
        out["detail"] = f"a skill não tem workflow/{WORKFLOW_SCRIPT}"
        return out
    try:
        out["state"] = "ok" if target.read_bytes() == data else "diferente"
    except FileNotFoundError:
        out["state"] = "ausente"
    except OSError:
        out["state"] = "ilegivel"
    out["current"] = out["state"] == "ok"
    if out["current"]:
        out["detail"] = f"workflow salvo idêntico ao da skill em {as_posix(target)}"
    else:
        out["detail"] = f"{WORKFLOW_SCRIPT} {out['state']} em {as_posix(target.parent)}"
    return out


def install_workflow(skill_dir: str | os.PathLike, chome: str | os.PathLike | None = None) -> dict:
    """Create ``<claude home>/workflows`` and copy the skill's workflow script there (skipped when the
    installed copy is already identical)."""
    ch = Path(chome) if chome is not None else claude_home()
    data = workflow_source(skill_dir).read_bytes()
    dest = ch / "workflows"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / WORKFLOW_SCRIPT
    try:
        if target.read_bytes() == data:
            return {"path": as_posix(target), "copied": False}
    except OSError:
        pass
    atomic_write_bytes(target, data)
    return {"path": as_posix(target), "copied": True}
