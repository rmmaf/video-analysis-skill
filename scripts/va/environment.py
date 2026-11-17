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
- Transcription (videos without subtitles): another environment, ``VA_WHISPERX_ENV`` or
  ``%USERPROFILE%\\whisperx-env``, made with uv and its own Python 3.12, with whisperx (the version the setup
  installs), the PyTorch build of the machine (CUDA by the NVIDIA driver, else CPU) and NLTK's punkt.
  ``transcription_status`` checks it offline in about a second; ``diarization_status`` checks the Hugging
  Face token and its access to the gated pyannote model (the person saves the token: it never goes through
  Claude); ``install_transcription`` creates or repairs it and proves it with scripts/transcribe.py --check,
  which downloads the models.

The Claude home is ``--claude-home`` / env ``VA_CLAUDE_HOME`` / ``%USERPROFILE%\\.claude``: tests must
always point it somewhere else, so the real settings are never touched.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import json
import os
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import MIN_PYTHON, TRANSCRIBE_DEFAULTS, WEBCAM_DEFAULTS, WHISPER_MODELS, WORKSPACE_DIRS
from .common import as_posix, atomic_write_bytes

__all__ = [
    "CLAUDE_HOME_ENV", "DEEPFACE_ENV", "MIN_PYTHON", "RULE_TOOLS", "WORKFLOW_SCRIPT", "SetupError", "AmbienteError",
    "agents_status", "claude_home", "deepface_env_dir", "deepface_python", "deepface_status", "install_agents",
    "install_deepface", "install_workflow", "merge_permissions",
    "module_status", "permissions_status", "python_status", "role_files", "runtime_problems",
    "tool_status", "user_home", "workflow_source", "workflow_status", "workspace_rules",
    "workspace_status", "yunet_weights",
    "TORCH_BACKEND_ENV", "WHISPERX_ENV", "diarization_status", "find_uv", "install_transcription", "login_command",
    "nltk_dir", "nvidia_info", "platform_supported", "prepare_punkt", "punkt_complete", "torch_backend",
    "transcription_status", "whisperx_env_dir", "whisperx_python", "BREW_BIN_DIRS", "brew_tool",
]

CLAUDE_HOME_ENV = "VA_CLAUDE_HOME"
# Homebrew's bin folders (Apple silicon, then Intel): a Mac whose shell never ran ``brew shellenv`` has
# ffmpeg there but not on the PATH Claude Code's shell gets
BREW_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
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


def brew_tool(name: str) -> tuple[str, str] | None:
    """On macOS, ``(path, note)`` of a ``name`` that is in a Homebrew bin folder but not on PATH; ``note`` says
    how to put Homebrew on PATH. None elsewhere, or when it is not there either."""
    if sys.platform != "darwin":
        return None
    for folder in BREW_BIN_DIRS:
        cand = os.path.join(folder, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            brew = posixpath.join(folder.replace("\\", "/"), "brew")
            return cand, (f"found in {folder}, which is not on PATH: add eval \"$({brew} shellenv)\" to ~/.zprofile "
                          "and open a new terminal")
    return None


def tool_status(name: str, timeout: float = 15.0) -> dict:
    """``ffmpeg`` / ``ffprobe``: on PATH (on macOS, also in Homebrew's folder off PATH) and ``-version`` runs."""
    path, note = shutil.which(name), ""
    if not path:
        found = brew_tool(name)
        if not found:
            return {"id": name, "ok": False, "version": None, "path": None, "detail": f"{name} is not on PATH"}
        path, note = found[0], f"; {found[1]}"
    try:
        r = subprocess.run([path, "-hide_banner", "-version"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"id": name, "ok": False, "version": None, "path": path, "detail": f"{name} did not run: {exc}{note}"}
    first = (r.stdout or r.stderr or "").strip().splitlines()[:1]
    version = first[0].strip() if first else ""
    if r.returncode != 0:
        return {"id": name, "ok": False, "version": version or None, "path": path,
                "detail": f"{name} -version exited with {r.returncode}{note}"}
    short = re.search(r"version\s+(\S+)", version)
    return {"id": name, "ok": True, "version": version, "path": path,
            "detail": f"{name} {short.group(1) if short else version} ({path}){note}"}


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


# the path rules of this system (posixpath on macOS and Linux, ntpath on Windows); the tests swap it to
# check the other system's rules
_ospath = os.path


def _posix() -> bool:
    return _ospath.sep == "/"


def workspace_rules(home: str | os.PathLike, uhome: str | os.PathLike | None = None) -> list[str]:
    """The allow rules for the workspace, derived from its real path: ``Tool(~/<rel>/**)`` when it
    is under the user's home, and ``Tool(//<drive>/<path>/**)`` on Windows. For C:\\Users\\<user>\\video-analysis
    these are exactly the 6 rules of the plan. On macOS and Linux, a workspace outside the home (an external
    disk: /Volumes/SSD/video-analysis) gets Claude Code's absolute spelling, ``Tool(//Volumes/SSD/video-analysis/**)``
    (a single slash would be relative to the settings file). Empty when no rule can name it (a network path)."""
    ws = _ospath.abspath(os.fspath(home))
    uh = _ospath.abspath(os.fspath(uhome)) if uhome is not None else str(user_home())
    rules = []
    ws_n, uh_n = _norm(ws), _norm(uh)
    under_home = ws_n.startswith(uh_n + "/")
    if under_home:
        rel = ws.replace("\\", "/")[len(uh.replace("\\", "/").rstrip("/")) + 1:]
        rules += [f"{t}(~/{rel}/**)" for t in RULE_TOOLS]
    if _posix():
        path = ws.strip("/")
        if not under_home and path:
            rules += [f"{t}(//{path}/**)" for t in RULE_TOOLS]
        return rules
    drive, rest = _ospath.splitdrive(ws)
    if re.fullmatch(r"[A-Za-z]:", drive):
        path = rest.replace("\\", "/").strip("/")
        rules += [f"{t}(//{drive[0].lower()}/{path}/**)" for t in RULE_TOOLS]
    return rules


_RULE_RE = re.compile(r"^\s*([A-Za-z][\w-]*)\s*(?:\((.*)\))?\s*$", re.DOTALL)


def _rule_base(pattern: str, uhome: Path, settings_dir: Path) -> str | None:
    """Absolute, comparable base of a ``…/**`` path pattern; None when it is not one.

    Claude Code's spellings only: ``~/…`` (user home), ``//c/…`` (absolute, Windows drive as a
    folder; on macOS and Linux, ``//<path>`` is the absolute ``/<path>``) and ``/…`` (relative to the
    settings file). ``C:/…`` or ``//C:/…`` would be read as relative paths by Claude Code, so they do
    not count."""
    p = pattern.strip()
    if "\\" in p or not p.endswith("/**"):
        return None
    p = p[:-3]
    if p in ("~", "") or p.startswith("~/"):
        base = str(uhome).replace("\\", "/").rstrip("/") + p[1:]
    elif p.startswith("//") and _posix():
        base = "/" + p.lstrip("/")
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
    uh = Path(_ospath.abspath(os.fspath(uhome))) if uhome is not None else user_home()
    path = ch / "settings.json"
    ws_n = _norm(_ospath.abspath(os.fspath(workspace)))
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
            if _cert_error(exc):
                raise SetupError(f"could not download the YuNet weights from {url}: {last}{_cert_hint()}") from None
            if attempt < attempts:
                time.sleep(min(pause_s * attempt, 10.0))
    raise SetupError(f"could not download the YuNet weights from {url} ({attempts} attempts): {last}")


def _cert_error(exc: BaseException) -> bool:
    """An HTTPS certificate that this Python cannot verify (urllib wraps it in a URLError): trying again
    does not help."""
    import ssl
    reason = getattr(exc, "reason", None)
    return (isinstance(exc, ssl.SSLCertVerificationError) or isinstance(reason, ssl.SSLCertVerificationError)
            or "CERTIFICATE_VERIFY_FAILED" in str(exc))


def _cert_hint() -> str:
    """What to do about a certificate error on macOS, where python.org's Python ships without a CA bundle."""
    if sys.platform != "darwin":
        return ""
    v = sys.version_info
    return (f'; run "/Applications/Python {v.major}.{v.minor}/Install Certificates.command" (python.org\'s Python '
            "ships without a CA bundle) or use Homebrew's Python (brew install python)")


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
    ``expected``, ``weights`` (intact) and ``weights_path`` (the YuNet file, under DEEPFACE_HOME or the
    user folder) are for check_environment.py."""
    d = deepface_env_dir(env_dir)
    py = Path(python) if python is not None else deepface_python(d)
    expected, w = WEBCAM_DEFAULTS["deepface_version"], yunet_weights()
    weights = weights_state(w)
    out = {"id": "deepface", "ok": False, "state": "absent", "problem": "absent", "env": as_posix(d),
           "python": as_posix(py), "version": None, "expected": expected, "weights": weights == "ok",
           "weights_path": as_posix(w), "detail": f"no DeepFace environment at {as_posix(d)}"}

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


def _run_step(cmd: list[str], timeout: float, env: dict | None = None) -> tuple[int, str]:
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, creationflags=_NO_WINDOW,
                       env=env or {**os.environ, "PYTHONIOENCODING": "utf-8"})   # DeepFace logs emojis
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


# --- the transcription environment (WhisperX: videos without subtitles) ------------------------------------

WHISPERX_ENV = "VA_WHISPERX_ENV"         # another folder for the transcription environment (tests, another disk)
TORCH_BACKEND_ENV = "VA_TORCH_BACKEND"   # forces the PyTorch build of the setup: cpu, cu126, cu128...
TRANSCRIBE_SCRIPT = Path(__file__).resolve().parent.parent / "transcribe.py"
DIARIZE_PAGE = "https://huggingface.co/" + TRANSCRIBE_DEFAULTS["diarize_model"]
# run by the environment's Python, offline: the versions, the modules that are missing, whether a Hugging Face
# token is saved (never the token itself), which models are already in the cache (argv[1]: repo -> files) and
# the environment's processor architecture (an x86_64 Python on Apple silicon runs under Rosetta)
_WHISPERX_PROBE = r'''
import importlib.metadata as m, importlib.util as u, json, platform, sys
out = {"python": "%d.%d.%d" % sys.version_info[:3], "machine": platform.machine(), "versions": {}, "missing": [],
       "token": False, "cached": {}}
for d in ("whisperx", "torch", "ctranslate2", "faster-whisper", "pyannote.audio", "huggingface-hub", "nltk"):
    try:
        out["versions"][d] = m.version(d)
    except m.PackageNotFoundError:
        out["versions"][d] = None
for n in ("whisperx", "torch", "faster_whisper", "pyannote.audio", "nltk", "huggingface_hub"):
    try:
        if u.find_spec(n) is None:
            out["missing"].append(n)
    except (ImportError, ValueError):
        out["missing"].append(n)
try:
    import huggingface_hub as h
    out["token"] = h.get_token() is not None
    for repo, files in json.loads(sys.argv[1]).items():
        hits = []
        for f in files:
            try:
                r = h.try_to_load_from_cache(repo, f)
            except Exception:
                r = None
            hits.append(isinstance(r, str))
        out["cached"][repo] = all(hits)
except Exception as exc:
    out["hub_error"] = "%s: %s" % (type(exc).__name__, exc)
print(json.dumps(out))
'''
# run by the environment's Python, online: may this token download the gated diarization model?
# (an HTTP 401/403, from the response or as a code in the text, not those digits inside a number; "gated" as a
# word: the same reading as classify_failure in scripts/transcribe.py)
_ACCESS_PROBE = r'''
import re
import sys
try:
    from huggingface_hub import auth_check
    auth_check(sys.argv[1])
    print("ok")
except Exception as exc:
    name = type(exc).__name__
    text = str(exc).lower()
    try:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    except Exception:
        status = None
    if (name in ("GatedRepoError", "RepositoryNotFoundError") or status in (401, 403)
            or re.search(r"(?<![\w.])40[13](?![\w.])|\bgated\b", text)):
        print("no_access")
    else:
        print("offline: %s: %s" % (name, " ".join(str(exc).split())[:200]))
'''


def whisperx_env_dir(override: str | os.PathLike | None = None) -> Path:
    """``override`` > env VA_WHISPERX_ENV > %USERPROFILE%\\whisperx-env (~/whisperx-env on macOS)."""
    for value in (os.fspath(override) if override is not None else "", os.environ.get(WHISPERX_ENV, "")):
        value = value.strip().strip('"').strip()
        if value:
            return Path(os.path.abspath(os.path.expanduser(value)))
    return user_home() / TRANSCRIBE_DEFAULTS["env_name"]


def _env_bin(env_dir: Path, name: str) -> Path:
    return env_dir / "Scripts" / f"{name}.exe" if os.name == "nt" else env_dir / "bin" / name


def whisperx_python(env_dir: str | os.PathLike | None = None) -> Path:
    return _env_bin(whisperx_env_dir(env_dir), "python")


def nltk_dir(env_dir: str | os.PathLike | None = None) -> Path:
    """Where the setup puts NLTK's sentence tokenizer (transcribe.py gets it through NLTK_DATA)."""
    return whisperx_env_dir(env_dir) / "nltk_data"


def login_command(env_dir: str | os.PathLike | None = None) -> str:
    """The command the person runs in their own terminal to save the Hugging Face token (setup.md, step 7):
    the token never goes through Claude."""
    hf = _env_bin(whisperx_env_dir(env_dir), "hf")
    return f'& "{hf}" auth login' if os.name == "nt" else f'"{hf}" auth login'


def platform_supported() -> tuple[bool, str]:
    """WhisperX 3.8.6 needs PyTorch 2.8, built for Windows and Linux x86-64 and for Apple silicon only."""
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        if machine not in ("arm64", "aarch64"):
            try:
                arm = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True,
                                     timeout=5).stdout.strip() == "1"
            except (OSError, subprocess.SubprocessError):
                arm = False
            if not arm:
                return False, ("this Mac has an Intel processor: PyTorch 2.8, which WhisperX 3.8.6 needs, has no "
                               "build for it, so every video needs its .vtt")
    elif sys.platform not in ("win32", "linux"):
        return False, f"local transcription is not supported on {sys.platform}"
    return True, ""


def nvidia_info(timeout: float = 15.0) -> dict | None:
    """The first NVIDIA GPU by nvidia-smi: ``{"name", "driver", "driver_major", "vram_mb", "compute_cap"}``,
    or None without one (or without the driver)."""
    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        for cand in (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
                     Path(r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe")):
            if cand.is_file():
                exe = str(cand)
                break
    if not exe:
        return None
    for fields in ("name,driver_version,memory.total,compute_cap", "name,driver_version,memory.total"):
        try:
            r = subprocess.run([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return None
        line = (r.stdout or "").strip().splitlines()[:1]
        if r.returncode == 0 and line:
            parts = [p.strip() for p in line[0].split(",")]
            try:
                major = int(parts[1].split(".")[0])
                vram = int(float(parts[2]))
            except (IndexError, ValueError):
                return None
            cc = None
            if len(parts) > 3:
                try:
                    cc = float(parts[3])
                except ValueError:
                    cc = None
            return {"name": parts[0], "driver": parts[1], "driver_major": major, "vram_mb": vram, "compute_cap": cc}
    return None


def gpu_usable(info: dict | None) -> bool:
    """Whether nvidia_info()'s GPU can transcribe: one older than Volta (compute capability below
    TRANSCRIBE_DEFAULTS["gpu_min_compute_cap"]) cannot; an unknown capability (an old nvidia-smi) counts."""
    if not info:
        return False
    cc = info.get("compute_cap")
    return cc is None or cc >= TRANSCRIBE_DEFAULTS["gpu_min_compute_cap"]


def torch_backend(gpu: dict | None | bool = False) -> str | None:
    """The PyTorch build to install: None on macOS (PyPI's build, CPU and Metal), even with VA_TORCH_BACKEND;
    else VA_TORCH_BACKEND when set, or ``cu128``/``cu126`` by the NVIDIA driver (TRANSCRIBE_DEFAULTS["cuda_backends"]),
    or ``cpu`` (also for a GPU older than Volta, see gpu_usable). ``gpu``: nvidia_info()'s answer (None: no
    GPU); left out, nvidia-smi is asked."""
    if sys.platform == "darwin":
        # the cpu index's macOS wheel has no "+cpu" tag, so a forced build never matched what was installed
        # and every setup reinstalled torch
        return None
    forced = os.environ.get(TORCH_BACKEND_ENV, "").strip()
    if forced:
        return forced
    info = nvidia_info() if gpu is False else gpu
    if not gpu_usable(info):
        return "cpu"
    for min_driver, backend in TRANSCRIBE_DEFAULTS["cuda_backends"]:
        if info["driver_major"] >= min_driver:
            return backend
    return "cpu"


def _torch_build(version: str | None) -> str | None:
    """"2.8.0+cu128" -> "cu128"; "2.8.0+cpu" -> "cpu"; "2.8.0" (PyPI: macOS, or Linux CUDA) -> None."""
    if not version or "+" not in version:
        return None
    return version.split("+", 1)[1]


def find_uv() -> list[str] | None:
    """The uv command: the one on PATH, else ``python -m uv`` (pip's), else the installers' usual places."""
    exe = shutil.which("uv")
    if exe:
        return [exe]
    try:
        if importlib.util.find_spec("uv") is not None:
            return [sys.executable, "-m", "uv"]
    except (ImportError, ValueError):
        pass
    home = Path(os.path.expanduser("~"))
    names = ["uv.exe"] if os.name == "nt" else ["uv"]
    folders = [home / ".local" / "bin", home / ".cargo" / "bin"]
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            folders.append(Path(local) / "Microsoft" / "WinGet" / "Links")
    else:
        folders += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    for folder in folders:
        for name in names:
            if (folder / name).is_file():
                return [str(folder / name)]
    return None


def _model_files(model: str) -> dict[str, list[str]]:
    d = TRANSCRIBE_DEFAULTS
    out = {WHISPER_MODELS[model]: list(d["whisper_files"])} if model in WHISPER_MODELS else {}
    for repo in d["align_models"].values():
        out[repo] = list(d["align_files"])
    out[d["diarize_model"]] = list(d["diarize_files"])
    return out


def punkt_complete(root: str | os.PathLike, languages: list[str] | None = None) -> bool:
    """The four files NLTK reads for each language (nltk/tokenize/punkt.py) are in ``root/<language>/``: a
    folder cut short by an interrupted extraction does not count."""
    root = Path(root)
    langs = languages or TRANSCRIBE_DEFAULTS["punkt_languages"]
    return all((root / lang / f).is_file() for lang in langs for f in TRANSCRIBE_DEFAULTS["punkt_files"])


def transcription_status(env_dir: str | os.PathLike | None = None, python: str | os.PathLike | None = None,
                         timeout: float = 90.0, gpu: dict | None | bool = False) -> dict:
    """The transcription environment, in about a second and offline. ``ok``: its Python runs and has whisperx
    in the version the setup installs, the modules it needs and NLTK's punkt; ``ready``: also the default
    Whisper model and the Portuguese alignment model in the Hugging Face cache (the setup downloads them, so
    the first analysis does not). ``state``: ok, absent, unsupported (an Intel Mac) or broken (``problem``:
    python, architecture (an x86_64 environment on a Mac), modules, version or punkt). ``machine``: the
    environment's platform.machine(); ``ctranslate2``: its CTranslate2 version. ``device`` (cuda or cpu), ``default_model`` (large-v3 on a GPU with
    enough memory, large-v3-turbo otherwise), ``token`` (a Hugging Face token is saved: needed to separate
    the speakers) and ``models`` (repo -> in the cache). ``python`` replaces the environment's (tests);
    ``gpu`` replaces nvidia-smi's answer (tests)."""
    d = whisperx_env_dir(env_dir)
    py = Path(python) if python is not None else whisperx_python(d)
    expected = TRANSCRIBE_DEFAULTS["whisperx_version"]
    out = {"id": "transcription", "ok": False, "ready": False, "state": "absent", "problem": "absent",
           "env": as_posix(d), "python": as_posix(py), "python_version": None, "version": None, "expected": expected,
           "torch": None, "ctranslate2": None, "machine": None, "backend": None, "device": "cpu", "gpu": None,
           "default_model": TRANSCRIBE_DEFAULTS["model_cpu"],
           "token": False, "models": {}, "punkt": False, "nltk_data": as_posix(nltk_dir(d)),
           "login_command": login_command(d), "detail": f"no transcription environment at {as_posix(d)}"}

    def broken(problem: str, detail: str) -> dict:
        out.update(state="broken", problem=problem, detail=detail)
        return out

    supported, why = platform_supported()
    if not supported:
        out.update(state="unsupported", problem="platform", detail=why)
        return out
    if not py.is_file():
        return out
    files = _model_files(TRANSCRIBE_DEFAULTS["model_gpu"])
    files.update(_model_files(TRANSCRIBE_DEFAULTS["model_cpu"]))
    try:
        r = subprocess.run([str(py), "-B", "-c", _WHISPERX_PROBE, json.dumps(files)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=_NO_WINDOW, env={**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1"})
    except (OSError, subprocess.SubprocessError) as exc:
        return broken("python", f"the Python of {as_posix(d)} did not run: {exc}")
    try:
        probe = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (IndexError, ValueError):
        tail = _last_line(r.stderr or r.stdout)
        return broken("python", f"the Python of {as_posix(d)} exited with {r.returncode}: {tail}".rstrip(": "))
    versions = probe.get("versions") or {}
    out.update(python_version=probe.get("python"), version=versions.get("whisperx"), torch=versions.get("torch"),
               ctranslate2=versions.get("ctranslate2"), machine=probe.get("machine"),
               token=bool(probe.get("token")), models=probe.get("cached") or {})
    out["backend"] = _torch_build(out["torch"])
    if sys.platform == "darwin" and out["machine"] and out["machine"].lower() not in ("arm64", "aarch64"):
        # an x86_64 uv or base Python (Intel Homebrew in /usr/local, under Rosetta) built it: PyTorch 2.8 has no
        # Intel macOS build, and pip-installing into it again cannot change its architecture
        return broken("architecture", f"{as_posix(d)} is an {out['machine']} environment (made by an Intel uv or "
                                      "Python, under Rosetta), and PyTorch 2.8 needs arm64 on a Mac: delete that "
                                      "folder and run step 6 again (--install-transcription) with the arm64 "
                                      "Homebrew (/opt/homebrew)")
    if probe.get("missing"):
        return broken("modules", f"{as_posix(d)} lacks {', '.join(probe['missing'])}")
    if out["version"] != expected:
        return broken("version", f"{as_posix(d)} has whisperx {out['version'] or 'of an unknown version'}, "
                                 f"and the skill uses {expected}")
    if out["backend"] and out["backend"].startswith("cu"):
        info = nvidia_info() if gpu is False else gpu
        if gpu_usable(info):
            out["device"], out["gpu"] = "cuda", info
            if info["vram_mb"] >= TRANSCRIBE_DEFAULTS["gpu_min_vram_mb"]:
                out["default_model"] = TRANSCRIBE_DEFAULTS["model_gpu"]
    out["punkt"] = punkt_complete(nltk_dir(d) / "tokenizers" / "punkt_tab")
    if not out["punkt"]:
        return broken("punkt", f"NLTK's sentence tokenizer is missing in {as_posix(nltk_dir(d))}")
    want = [WHISPER_MODELS[out["default_model"]], *TRANSCRIBE_DEFAULTS["align_models"].values()]
    out["ready"] = all(out["models"].get(repo) for repo in want)
    where = f"{out['device']}, {out['gpu']['name']}" if out["gpu"] else out["device"]
    out.update(ok=True, state="ok", problem=None,
               detail=f"whisperx {out['version']} in {as_posix(d)} ({where}; model {out['default_model']})")
    return out


def diarization_status(status: dict | None = None, online: bool = True, timeout: float = 30.0) -> dict:
    """Separating the speakers: a token saved, access to the gated pyannote model (online, when it can be
    checked) and the model in the cache. ``state``: ok, unavailable (no transcription environment),
    no_token, no_access or download (access, but the model is not downloaded yet). Offline, a model in the
    cache counts as ok."""
    st = status if status is not None else transcription_status()
    repo = TRANSCRIBE_DEFAULTS["diarize_model"]
    out = {"id": "speakers", "ok": False, "state": "unavailable", "model": repo, "page": DIARIZE_PAGE,
           "login_command": st.get("login_command"), "cached": bool((st.get("models") or {}).get(repo)),
           "access": None, "detail": "the transcription environment comes first (setup.md, step 6)"}
    if not st.get("ok"):
        return out
    if not st.get("token"):
        out.update(state="no_token", detail="no Hugging Face token saved: the subtitles will not separate the speakers")
        return out
    access = None
    if online:
        try:
            r = subprocess.run([st["python"], "-B", "-c", _ACCESS_PROBE, repo], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                               creationflags=_NO_WINDOW, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            access = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else f"offline: exit {r.returncode}"
        except (OSError, subprocess.SubprocessError) as exc:
            access = f"offline: {exc}"
    out["access"] = access
    if access == "no_access":
        out.update(state="no_access", detail=f"the saved token has no access to {repo}: accept its conditions at "
                                             f"{DIARIZE_PAGE}, signed in with the same account")
        return out
    if not out["cached"]:
        if access == "ok":
            out.update(state="download", detail=f"token with access to {repo}, but the model is not downloaded yet")
        else:
            out.update(state="download", detail=f"token saved; access to {repo} not confirmed ({access or 'not checked'}) "
                                                 "and the model is not in the cache")
        return out
    note = "access confirmed" if access == "ok" else "the model is in the cache (access not checked online)"
    out.update(ok=True, state="ok", detail=f"token saved, {note}: the subtitles separate the speakers")
    return out


def _fetch(url: str, opener=None, attempts: int = 6, pause_s: float = 2.0, timeout: float = 180.0) -> bytes:
    fetch = opener or (lambda u, t: urllib.request.urlopen(u, timeout=t))
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            with fetch(url, timeout) as r:
                return r.read()
        except Exception as exc:   # the network or the server: try again, with a growing pause
            last = f"{type(exc).__name__}: {exc}"
            if _cert_error(exc):
                raise SetupError(f"could not download {url}: {last}{_cert_hint()}") from None
            if attempt < attempts:
                time.sleep(min(pause_s * attempt, 10.0))
    raise SetupError(f"could not download {url} ({attempts} attempts): {last}")


def prepare_punkt(nltk_data: str | os.PathLike, languages: list[str] | None = None, opener=None,
                  force: bool = False) -> str:
    """NLTK's punkt_tab in ``<nltk_data>/tokenizers/punkt_tab``, whole for each language (the notebook's
    preparar_punkt). NLTK's own downloader refuses to run behind some proxies, so the package comes straight
    from NLTK's repository. The rule is the languages being whole, not the folder existing: an extraction
    cut short stops in alphabetical order. Published by a rename, so the folder is never half there. Returns
    what it did; raises SetupError."""
    import io
    import zipfile
    tokenizers = Path(nltk_data) / "tokenizers"
    punkt = tokenizers / "punkt_tab"
    mark = punkt / ".complete"
    tmp = tokenizers / "punkt_tab.downloading"
    langs = languages or TRANSCRIBE_DEFAULTS["punkt_languages"]
    has_mark = mark.exists()
    if not force:
        if has_mark and punkt_complete(punkt, langs):
            return "already in place"
        if not has_mark and punkt_complete(punkt, langs):
            mark.touch()
            return "manual installation adopted"
    if has_mark:
        mark.unlink()
    url = TRANSCRIBE_DEFAULTS["punkt_url"]
    shutil.rmtree(tmp, ignore_errors=True)
    tokenizers.mkdir(parents=True, exist_ok=True)
    try:
        data = _fetch(url, opener)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            if any(n.startswith(("/", "\\")) or ".." in Path(n).parts or ":" in n for n in names):
                raise SetupError("the package has paths outside its folder")
            z.extractall(tmp)
        missing = [n for n in names if not (tmp / n).exists()]
        root = tmp / "punkt_tab"
        if missing:
            raise SetupError(f"{len(missing)} file(s) did not extract (e.g. {missing[0]})")
        if not root.is_dir():
            raise SetupError("the package has no punkt_tab/ folder")
        if not punkt_complete(root, langs):
            raise SetupError(f"the package lacks {', '.join(langs)} whole")
        (root / ".complete").touch()
        shutil.rmtree(punkt, ignore_errors=True)
        os.replace(root, punkt)
    except SetupError as exc:
        raise SetupError(f"could not prepare NLTK's punkt ({exc}); download {url} by hand and extract it in "
                         f"{as_posix(tokenizers)}") from None
    except (OSError, zipfile.BadZipFile) as exc:
        raise SetupError(f"could not prepare NLTK's punkt ({type(exc).__name__}: {exc}); download {url} by hand "
                         f"and extract it in {as_posix(tokenizers)}") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "downloaded"


def _uv_hint() -> str:
    if sys.platform == "darwin":
        return "install uv with Homebrew (brew install uv) and run it again"
    return "install uv (winget install --id=astral-sh.uv -e, or python -m pip install --user uv) and run it again"


def _venv_python() -> str:
    """uv's request for the environment's Python. On a Mac, the arm64 build by name: an x86_64 uv or base
    Python (Intel Homebrew, under Rosetta) would otherwise make an x86_64 environment, for which PyTorch 2.8
    has no build."""
    version = TRANSCRIBE_DEFAULTS["python"]
    return f"cpython-{version}-macos-aarch64-none" if sys.platform == "darwin" else version


def install_transcription(env_dir: str | os.PathLike | None = None, runner=None, opener=None, uv: list[str] | None = None,
                          backend: str | None | bool = False, base_python: str | None = None) -> dict:
    """Create or repair the transcription environment, skipping what is already done: uv (pip's, when it is
    missing), ``uv venv --python 3.12`` (uv downloads that Python when the machine lacks it: WhisperX needs
    one older than 3.14; on a Mac, its arm64 build by name), ``uv pip install`` of whisperx, the pinned
    CTranslate2 and the PyTorch build of this machine (``torch_backend``), NLTK's punkt, and then the proof: transcribe.py --check loads the default Whisper
    model, the alignment models of the setup's languages and, with a token, pyannote, downloading them on the
    way. A proof that cannot import whisperx reinstalls it once. ``runner(cmd, timeout) -> (code, output)``,
    ``opener``, ``uv`` and ``backend`` replace the subprocess, the download, uv and the GPU detection (tests).
    Returns ``{"env", "done", "status", "check"}``; raises SetupError with the end of the failing step's output."""
    supported, why = platform_supported()
    if not supported:
        raise SetupError(why)
    d = whisperx_env_dir(env_dir)
    py = whisperx_python(d)
    done: list[str] = []
    timeouts = TRANSCRIBE_DEFAULTS

    def attempt(cmd: list, timeout: float, env: dict | None = None) -> tuple[int, str]:
        try:
            if runner is not None:
                return runner([str(c) for c in cmd], timeout)
            return _run_step([str(c) for c in cmd], timeout, env)
        except (OSError, subprocess.SubprocessError) as exc:
            return -1, f"{type(exc).__name__}: {exc}"

    def step(cmd: list, what: str, timeout: float, env: dict | None = None, hint: str = "") -> str:
        code, output = attempt(cmd, timeout, env)
        if code != 0:
            tail = " | ".join([line.strip() for line in output.splitlines() if line.strip()][-3:])
            raise SetupError(f"could not {what} (exit {code}): {tail}" + (f"; {hint}" if hint else ""))
        done.append(what)
        return output

    uvc = uv if uv is not None else find_uv()
    if uvc is None:
        step([base_python or sys.executable, "-m", "pip", "install", "--user", "--disable-pip-version-check", "uv"],
             "install uv (python -m pip install --user uv)", 900.0, hint=_uv_hint())
        uvc = [base_python or sys.executable, "-m", "uv"]
    st = transcription_status(d) if py.is_file() else None
    if st is not None and st["problem"] == "python":       # a folder whose Python does not run: start over
        if not (d / "pyvenv.cfg").is_file():
            raise SetupError(f"{as_posix(d)} is not a Python environment: move it away, or point VA_WHISPERX_ENV elsewhere")
        shutil.rmtree(d, ignore_errors=True)
        done.append(f"remove the environment {as_posix(d)}, whose Python did not run")
        st = None
    if not py.is_file():
        if d.exists() and any(d.iterdir()) and not (d / "pyvenv.cfg").is_file():
            raise SetupError(f"{as_posix(d)} exists and is not a Python environment: move it away, or point "
                             "VA_WHISPERX_ENV elsewhere")
        step(uvc + ["venv", "--python", _venv_python(), str(d)],
             f"create the environment {as_posix(d)} (Python {TRANSCRIBE_DEFAULTS['python']})", 1800.0)
        st = None
    want = torch_backend() if backend is False else backend
    if st is None:
        st = transcription_status(d)
    if st["problem"] == "architecture":   # its packages cannot be fixed in place: the person deletes it
        raise SetupError(st["detail"])
    ct2 = TRANSCRIBE_DEFAULTS["ctranslate2_version"]
    pkgs = [f"whisperx=={TRANSCRIBE_DEFAULTS['whisperx_version']}", f"ctranslate2=={ct2}",
            *TRANSCRIBE_DEFAULTS["torch_packages"]]
    install = uvc + ["pip", "install", "--python", str(py)] + (["--torch-backend", want] if want else [])
    wrong_build = bool(want) and st.get("torch") is not None and _torch_build(st.get("torch")) != want
    wrong_ct2 = st.get("ctranslate2") not in (None, ct2)   # an environment of before the pin
    if st["problem"] in ("absent", "modules", "version") or wrong_build or wrong_ct2:
        what = f"install whisperx {TRANSCRIBE_DEFAULTS['whisperx_version']} with the {want or 'default'} PyTorch build"
        step(install + (["--reinstall-package", "torch"] if wrong_build else []) + pkgs, what,
             timeouts["install_timeout_s"])
    result = prepare_punkt(nltk_dir(d), opener=opener)
    if result != "already in place":
        done.append(f"NLTK punkt: {result}")
    st = transcription_status(d)
    if not st["ok"]:
        raise SetupError(f"{st['detail']} after the installation")
    check = [str(py), "-B", str(TRANSCRIBE_SCRIPT), "--check", "--model", st["default_model"], "--diarize"]
    for lang in TRANSCRIBE_DEFAULTS["setup_languages"]:
        check += ["--align", lang]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
           "NLTK_DATA": str(nltk_dir(d)), "PYTHONWARNINGS": "ignore"}
    env.pop("PYTHONPATH", None)
    code, output = attempt(check, timeouts["check_timeout_s"], env)
    if code == 3:                         # whisperx does not import: reinstall it, once
        step(install + ["--reinstall"] + pkgs, "reinstall whisperx, which did not load "
             f"({_last_line(output)})", timeouts["install_timeout_s"])
        code, output = attempt(check, timeouts["check_timeout_s"], env)
    proof = None
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                proof = json.loads(line)
            except ValueError:
                proof = None
            break
    if code != 0 or not isinstance(proof, dict):
        detail = (proof or {}).get("error") or "; ".join(
            f"{k}: {v}" for k, v in ((proof or {}).get("align") or {}).items() if v != "ok") or _last_line(output)
        raise SetupError(f"the proof of the environment failed (exit {code}): {detail}. A download that failed "
                         "resumes when you run it again")
    done.append(f"downloaded and loaded {st['default_model']} and the alignment of "
                f"{', '.join(TRANSCRIBE_DEFAULTS['setup_languages'])} on {proof.get('device')}")
    return {"env": as_posix(d), "done": done, "status": transcription_status(d), "check": proof}
