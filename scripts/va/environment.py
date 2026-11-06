"""Environment checks shared by prepare.py and check_environment.py (design-agentic §7, DL-6,
DL-13; plan, "Permissions and setup").

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
- DeepFace (optional, for the webcam timeline): a separate Python environment, ``VA_DEEPFACE_ENV`` or
  ``%USERPROFILE%\\deepface-env``, whose Python has deepface (the version the setup installs), cv2 and
  numpy, and the YuNet weights, checked by their SHA-256. ``install_deepface`` creates it (venv),
  installs ``deepface==0.0.104``, downloads the weights and proves it all with a detection, reinstalling
  deepface when the detection fails; the setup runs it only after the person's "yes".

The Claude home is ``--claude-home`` / env ``VA_CLAUDE_HOME`` / ``%USERPROFILE%\\.claude``: tests must
always point it somewhere else, so the real settings are never touched.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import WEBCAM_DEFAULTS, WORKSPACE_DIRS
from .common import as_posix, atomic_write_bytes

__all__ = [
    "CLAUDE_HOME_ENV", "DEEPFACE_ENV", "MIN_PYTHON", "RULE_TOOLS", "WORKFLOW_SCRIPT", "SetupError", "AmbienteError",
    "agents_status", "claude_home", "deepface_env_dir", "deepface_python", "deepface_status", "install_agents",
    "install_deepface", "install_workflow", "merge_permissions",
    "module_status", "permissions_status", "python_status", "role_files", "runtime_problems",
    "tool_status", "user_home", "workflow_source", "workflow_status", "workspace_rules",
    "workspace_status", "yunet_weights",
]

CLAUDE_HOME_ENV = "VA_CLAUDE_HOME"
MIN_PYTHON = (3, 10)
RULE_TOOLS = ("Read", "Edit", "Write")       # order of the 6 rules in the plan
WORKFLOW_SCRIPT = "video-analysis.js"        # <skill>/workflow/<this> -> <claude home>/workflows/<this>
MODULES = {"numpy": ("numpy", "numpy"), "pillow": ("PIL", "Pillow")}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SetupError(RuntimeError):
    """A setup action could not be done safely (nothing was written)."""


AmbienteError = SetupError  # the name of before the rename, kept for scripts that import it


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
            "detail": f"Python {version} ({sys.executable})" if ok else f"Python {version}: the skill needs 3.10 or newer"}


def tool_status(name: str, timeout: float = 15.0) -> dict:
    """``ffmpeg`` / ``ffprobe``: on PATH and ``-version`` runs."""
    path = shutil.which(name)
    if not path:
        return {"id": name, "ok": False, "version": None, "path": None, "detail": f"{name} is not on PATH"}
    try:
        r = subprocess.run([path, "-hide_banner", "-version"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"id": name, "ok": False, "version": None, "path": path, "detail": f"{name} did not run: {exc}"}
    first = (r.stdout or r.stderr or "").strip().splitlines()[:1]
    version = first[0].strip() if first else ""
    if r.returncode != 0:
        return {"id": name, "ok": False, "version": version or None, "path": path,
                "detail": f"{name} -version exited with {r.returncode}"}
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
        return {"id": key, "ok": False, "version": None, "detail": f"{dist} is not installed for {sys.executable}"}
    if not version:
        return {"id": key, "ok": True, "version": None, "detail": dist}
    from importlib import metadata  # lazy: only check_environment.py shows the versions
    try:
        found = metadata.version(dist)
    except metadata.PackageNotFoundError:
        found = "?"
    return {"id": key, "ok": True, "version": found, "detail": f"{dist} {found}"}


def runtime_problems(need: tuple[str, ...] = ("ffmpeg", "ffprobe", "numpy", "pillow")) -> list[dict]:
    """Failing runtime checks among ``need``, each with the prepare.py error code
    (``missing_ffmpeg`` or ``missing_dependency``)."""
    out = []
    for item in need:
        st = tool_status(item) if item in ("ffmpeg", "ffprobe") else module_status(item, version=False)
        if not st["ok"]:
            st["code"] = "missing_ffmpeg" if item in ("ffmpeg", "ffprobe") else "missing_dependency"
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
            return {"id": "central-folder", "ok": False, "detail": f"{as_posix(home)} does not exist (it is created on first use)"}
        probe = home / WORKSPACE_DIRS["tmp"] / f".escrita-{os.getpid()}"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        return {"id": "central-folder", "ok": False, "detail": f"{as_posix(home)} does not accept writes: {exc}"}
    return {"id": "central-folder", "ok": True, "detail": f"{as_posix(home)} accepts writes"}


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
        return None, f"{as_posix(path)} does not exist"
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError) as exc:
        return None, f"{as_posix(path)} unreadable: {exc}"
    if not isinstance(data, dict):
        return None, f"{as_posix(path)} is not a JSON object"
    return data, None


def permissions_status(workspace: str | os.PathLike, chome: str | os.PathLike | None = None,
                       uhome: str | os.PathLike | None = None) -> dict:
    """Do the user settings allow Read, Edit and Write on the workspace without prompting?"""
    ch = Path(chome) if chome is not None else claude_home()
    uh = Path(os.path.abspath(uhome)) if uhome is not None else user_home()
    path = ch / "settings.json"
    ws_n = _norm(os.path.abspath(workspace))
    out = {"id": "permissions", "ok": False, "settings_path": as_posix(path), "covered": dict.fromkeys(RULE_TOOLS),
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
                    out["notes"].append(f"{kind} inside the central folder: {r}")
    out["missing"] = [t for t in RULE_TOOLS if out["covered"][t] is None]
    out["ok"] = not out["missing"] and not out["blocked"]
    if out["ok"]:
        out["detail"] = "Read, Edit and Write allowed for " + as_posix(os.path.abspath(workspace))
    elif out["blocked"]:
        out["detail"] = "rule that blocks the central folder: " + "; ".join(out["blocked"])
    else:
        out["detail"] = f"missing rules {', '.join(out['missing'])} for {as_posix(os.path.abspath(workspace))} in {as_posix(path)}"
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
    Before writing an existing file, copies it to ``settings.json.bak-YYYYMMDD-HHMMSS``.
    Raises SetupError (and writes nothing) when the file is not a valid JSON object."""
    path = Path(settings_path)
    raw = None
    if path.exists():
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
            data = json.loads(text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise SetupError(f"{as_posix(path)} is not valid JSON ({exc}); nothing was written") from None
        if not isinstance(data, dict):
            raise SetupError(f"{as_posix(path)} is not a JSON object; nothing was written")
        indent, final_nl = _indent_of(text), text.endswith("\n")
    else:
        data, indent, final_nl = {}, 2, True
    perms = data.setdefault("permissions", {})
    if not isinstance(perms, dict):
        raise SetupError(f"'permissions' in {as_posix(path)} is not an object; nothing was written")
    allow = perms.setdefault("allow", [])
    if not isinstance(allow, list):
        raise SetupError(f"'permissions.allow' in {as_posix(path)} is not a list; nothing was written")
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
            state = "ok" if target.read_bytes() == src.read_bytes() else "different"
        except FileNotFoundError:
            state = "missing"
        except OSError:
            state = "unreadable"
        files.append({"name": src.name, "state": state})
    current = bool(files) and all(f["state"] == "ok" for f in files)
    if current:
        detail = f"{len(files)} roles identical to the skill's in {as_posix(dest)}"
    elif not files:
        detail = "the skill has no roles in agents/"
    else:
        detail = "; ".join(f"{f['name']} {f['state']}" for f in files if f["state"] != "ok") + f" in {as_posix(dest)}"
    return {"id": "agents", "current": current, "dir": as_posix(dest), "files": files, "detail": detail}


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
    ``workflow/video-analysis.js``. ``state``: ok, missing, different, unreadable, or no-source (the
    skill itself has no script)."""
    ch = Path(chome) if chome is not None else claude_home()
    target = ch / "workflows" / WORKFLOW_SCRIPT
    out = {"id": "workflow", "current": False, "path": as_posix(target), "state": "no-source", "detail": ""}
    try:
        data = workflow_source(skill_dir).read_bytes()
    except OSError:
        out["detail"] = f"the skill has no workflow/{WORKFLOW_SCRIPT}"
        return out
    try:
        out["state"] = "ok" if target.read_bytes() == data else "different"
    except FileNotFoundError:
        out["state"] = "missing"
    except OSError:
        out["state"] = "unreadable"
    out["current"] = out["state"] == "ok"
    if out["current"]:
        out["detail"] = f"saved workflow identical to the skill's in {as_posix(target)}"
    else:
        out["detail"] = f"{WORKFLOW_SCRIPT} {out['state']} in {as_posix(target.parent)}"
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


# --- DeepFace environment (optional: the webcam timeline) ----------------------------------------------

DEEPFACE_ENV = "VA_DEEPFACE_ENV"   # another folder for the DeepFace environment (tests, another machine)
DEEPFACE_CHECK_TIMEOUT_S = 300.0   # the detection below: DeepFace, ONNX Runtime and OpenCV load in seconds, more when cold
# run by the environment's Python: deepface's version (empty without it), then the modules
# webcam_mask.py imports that are missing (find_spec only: nothing heavy is imported)
_DEEPFACE_PROBE = ("import importlib.metadata as m, importlib.util as u\n"
                   "try:\n    print(m.version('deepface'))\nexcept m.PackageNotFoundError:\n    print('')\n"
                   "print(','.join(n for n in ('deepface', 'cv2', 'numpy') if u.find_spec(n) is None))\n")
# run by the environment's Python: a detection on a black image, the proof that DeepFace, what it loads
# and the YuNet weights work, which find_spec does not see (a native library that does not load, half an
# interrupted install). The weights must already be in place: DeepFace would download them otherwise.
_DEEPFACE_CHECK = ("import numpy as np\n"
                   "from deepface import DeepFace\n"
                   "DeepFace.extract_faces(img_path=np.zeros((64, 64, 3), np.uint8), detector_backend='yunet',"
                   " enforce_detection=False, align=False)\n")


def deepface_env_dir(override: str | os.PathLike | None = None) -> Path:
    """``override`` > env VA_DEEPFACE_ENV > %USERPROFILE%\\deepface-env."""
    for value in (os.fspath(override) if override is not None else "", os.environ.get(DEEPFACE_ENV, "")):
        value = value.strip().strip('"').strip()
        if value:
            return Path(os.path.abspath(os.path.expanduser(value)))
    return user_home() / WEBCAM_DEFAULTS["env_name"]


def deepface_python(env_dir: str | os.PathLike | None = None) -> Path:
    d = deepface_env_dir(env_dir)
    return d / "Scripts" / "python.exe" if os.name == "nt" else d / "bin" / "python"


def yunet_weights() -> Path:
    """Where DeepFace keeps the YuNet weights: <DEEPFACE_HOME, or the user folder>/.deepface/weights."""
    base = os.environ.get("DEEPFACE_HOME", "").strip() or os.path.expanduser("~")
    return Path(base) / ".deepface" / "weights" / WEBCAM_DEFAULTS["yunet_weights"]


def weights_state(path: str | os.PathLike | None = None) -> str:
    """The YuNet weights (``yunet_weights()`` by default): ``ok`` (the file the setup downloads, by its
    SHA-256), ``missing`` or ``damaged`` (another content: a cut download, a page instead of the file)."""
    p = Path(path) if path is not None else yunet_weights()
    try:
        data = p.read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "damaged"
    return "ok" if hashlib.sha256(data).hexdigest() == WEBCAM_DEFAULTS["yunet_sha256"] else "damaged"


def download_weights(opener=None, attempts: int = 8, pause_s: float = 2.0) -> int:
    """Download the YuNet weights (about 230 KB) to ``yunet_weights()``, checked by their SHA-256, so that an
    analysis needs no network. Up to ``attempts`` GETs, with a growing pause between them: DeepFace tries
    once, and a connection reset on the way fails it (seen on a network that reset 4 in 10 connections to
    GitHub). ``opener(url, timeout)`` replaces urllib's (tests). Returns the attempts it took; raises
    SetupError when all fail."""
    url, expected = WEBCAM_DEFAULTS["yunet_url"], WEBCAM_DEFAULTS["yunet_sha256"]
    fetch = opener or (lambda u, timeout: urllib.request.urlopen(u, timeout=timeout))
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            with fetch(url, 60) as r:
                data = r.read()
            got = hashlib.sha256(data).hexdigest()
            if got != expected:
                raise ValueError(f"{len(data)} bytes with SHA-256 {got[:12]}..., not the YuNet weights")
            atomic_write_bytes(yunet_weights(), data)
            return attempt
        except Exception as exc:   # the network, the server or the content: try again
            last = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                time.sleep(min(pause_s * attempt, 10.0))
    raise SetupError(f"could not download the YuNet weights from {url} ({attempts} attempts): {last}")


def _last_line(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][-300:] if lines else ""


def deepface_status(env_dir: str | os.PathLike | None = None, python: str | os.PathLike | None = None,
                    timeout: float = 60.0, deep: bool = False) -> dict:
    """The DeepFace environment of the webcam timeline. ``ok``: its Python runs and has deepface in the
    version the setup installs, cv2 and numpy, and the YuNet weights are in place and intact (their
    SHA-256); with ``deep``, DeepFace also runs a detection (seconds: check_environment.py; prepare.py
    leaves that proof to webcam_mask.py itself). ``python`` replaces the environment's own (tests).
    ``state``: ok, absent (no Python in the folder) or broken, with ``problem`` (absent, python, modules,
    version, weights or detection; None when ok) for install_deepface; ``version`` of deepface,
    ``expected`` and ``weights`` (intact) are for check_environment.py."""
    d = deepface_env_dir(env_dir)
    py = Path(python) if python is not None else deepface_python(d)
    expected, w = WEBCAM_DEFAULTS["deepface_version"], yunet_weights()
    weights = weights_state(w)
    out = {"id": "deepface", "ok": False, "state": "absent", "problem": "absent", "env": as_posix(d),
           "python": as_posix(py), "version": None, "expected": expected, "weights": weights == "ok",
           "detail": f"no DeepFace environment at {as_posix(d)}"}

    def broken(problem: str, detail: str) -> dict:
        out.update(state="broken", problem=problem, detail=detail)
        return out

    if not py.is_file():
        return out
    try:
        r = subprocess.run([str(py), "-B", "-c", _DEEPFACE_PROBE], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return broken("python", f"the Python of {as_posix(d)} did not run: {exc}")
    lines = (r.stdout or "").splitlines()
    if r.returncode != 0 or len(lines) < 2:
        tail = _last_line(r.stderr or r.stdout)
        return broken("python", f"the Python of {as_posix(d)} exited with {r.returncode}: {tail}".rstrip(": "))
    out["version"] = lines[0].strip() or None
    missing = [m for m in lines[1].strip().split(",") if m]
    if missing:
        return broken("modules", f"{as_posix(d)} lacks {', '.join(missing)}")
    if out["version"] != expected:
        return broken("version", f"{as_posix(d)} has deepface {out['version'] or 'of an unknown version'}, "
                                 f"and the skill uses {expected}")
    if weights == "missing":
        return broken("weights", f"the YuNet weights are not in {as_posix(w.parent)}")
    if weights == "damaged":
        return broken("weights", f"the YuNet weights in {as_posix(w)} are damaged (not the file the setup downloads)")
    if deep:
        try:
            r = subprocess.run([str(py), "-B", "-c", _DEEPFACE_CHECK], stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
                               timeout=max(timeout, DEEPFACE_CHECK_TIMEOUT_S),
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})   # DeepFace logs emojis
        except (OSError, subprocess.SubprocessError) as exc:
            return broken("detection", f"DeepFace did not run a detection in {as_posix(d)}: {exc}")
        if r.returncode != 0:
            return broken("detection", f"DeepFace did not run a detection in {as_posix(d)} (exit {r.returncode}): "
                                       f"{_last_line(r.stderr or r.stdout)}")
    out.update(ok=True, state="ok", problem=None, detail=f"deepface {out['version']} in {as_posix(d)}")
    return out


def _run_step(cmd: list[str], timeout: float) -> tuple[int, str]:
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=_NO_WINDOW,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})   # DeepFace logs emojis
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def install_deepface(env_dir: str | os.PathLike | None = None, base_python: str | None = None, runner=None,
                     timeout: float = 1800.0, opener=None) -> dict:
    """Create the DeepFace environment with ``python -m venv`` (``base_python``: the Python that runs this,
    by default), install ``deepface==<WEBCAM_DEFAULTS version>`` in it and download the YuNet weights (a
    damaged copy goes away first), skipping each step already done; then prove it all with a detection,
    which always runs. When it fails, deepface is reinstalled with its dependencies (``--force-reinstall``:
    a package that does not load, half an interrupted install) and the detection runs again.
    ``runner(cmd, timeout) -> (returncode, output)`` replaces the subprocess and ``opener`` the download
    (tests). Returns ``{"env", "done", "status"}``; raises SetupError with the end of the output of the
    step that failed."""
    d = deepface_env_dir(env_dir)
    run = runner or _run_step
    version = WEBCAM_DEFAULTS["deepface_version"]
    pip = [deepface_python(d), "-m", "pip", "install", "--disable-pip-version-check"]
    check = [deepface_python(d), "-B", "-c", _DEEPFACE_CHECK]
    done = []

    def attempt(cmd: list) -> tuple[int, str]:
        try:
            return run([str(c) for c in cmd], timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return -1, f"{type(exc).__name__}: {exc}"

    def step(cmd: list, what: str) -> None:
        code, output = attempt(cmd)
        if code != 0:
            tail = " | ".join([line.strip() for line in output.splitlines() if line.strip()][-3:])
            raise SetupError(f"could not {what} (exit {code}): {tail}")
        done.append(what)

    if not deepface_python(d).is_file():
        step([base_python or sys.executable, "-m", "venv", d], f"create the environment {as_posix(d)}")
    if deepface_status(d)["problem"] in ("absent", "python", "modules", "version"):
        step(pip + [f"deepface=={version}"], f"install deepface {version}")
    weights = weights_state()
    if weights == "damaged":
        yunet_weights().unlink()
        done.append("remove the damaged YuNet weights")
    if weights != "ok":
        download_weights(opener=opener)
        done.append("download the YuNet weights")
    code, output = attempt(check)
    if code != 0:
        step(pip + ["--force-reinstall", f"deepface=={version}"],
             f"reinstall deepface {version}, since DeepFace did not run a detection ({_last_line(output)})")
        step(check, "run a detection with DeepFace after the reinstall")
    return {"env": as_posix(d), "done": done, "status": deepface_status(d)}
