"""Per-video cache: keys, stage folders, stage.json, heartbeat locks, GC (spec section 7).

Layout (``<home>/cache/<video_key>/``)::

    source.json  probe.json  last_used  .lock
    cues-<h8>/     cues.json  stage.json
    frames-<h8>/   F0001_000000.jpg ...  frames.json  samples.csv  stage.json
    batches-<h8>/  batch_001.md/.json  sheet_001.jpg ...  batches.json  timeline.json
                   transcript.md  summary.json  overview.jpg  stage.json

Keys
- ``video_key`` = slug + "-" + fp8, fp8 = sha1(NFC.casefold(abs path) | size | mtime_ns)[:8].
- ``cues-<h8>``    = sha1(vtt bytes, VTT_VERSION, effective cue params).
- ``frames-<h8>``  = sha1(video_key, effective frame params (range included), ALGO_VERSION).
- ``batches-<h8>`` = sha1(frames key, cues key, effective batch params (the ``trecho`` range
  included), RENDER_VERSION, title and source paths rendered into the outputs).
  Changing the batch size never re-extracts frames; a ``trecho`` gets its own batches folder.

Ownership: stage functions write their content files only; this module writes ``stage.json``
last, atomically. A folder without a valid ``stage.json`` is deleted and rebuilt; a completed
folder is never overwritten (new parameters give a new folder). ``--force`` rebuilds the named
stage and everything downstream; a rebuilt upstream stage also rebuilds the batches.

Locks never use ``os.kill`` (on Windows it calls TerminateProcess): ``.lock`` is created with
O_CREAT|O_EXCL, its mtime is a heartbeat refreshed every 30 s, and a lock without a heartbeat
for 10 min is stale. A second process polls every 2 s for up to 30 min.

``prepare_video`` is the top-level, plain-data worker run in-process or in a spawn
ProcessPool (spec section 8, item 9). Stage functions are resolved lazily with ``stage_fn``.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib
import importlib.util
import json
import math
import os
import shutil
import socket
import stat
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import ALGO_VERSION, RENDER_VERSION, VERSION, VTT_VERSION
from .common import as_posix, nfc_casefold, read_json, slugify, write_json, write_text

__all__ = [
    "LOCK_HEARTBEAT_S", "LOCK_POLL_S", "LOCK_STALE_S", "LOCK_WAIT_S", "STAGES", "STAGE_OVERRIDES",
    "LockTimeout", "StageUnavailable", "VideoLock",
    "canonical_json", "child_init", "dir_size", "fresh_dir", "expand_force", "file_sha1", "gc_cache", "h8", "info_get", "iso_now",
    "plain", "prepare_video", "read_stage", "rmtree_robust", "stage_dir", "stage_fn", "stage_hits", "stage_keys",
    "stage_valid", "cached_probe", "touch_last_used", "video_fingerprint", "video_key", "write_stage_json",
]

STAGES = ("cues", "frames", "batches")
STAGE_SCHEMA = "va.stage/1"

LOCK_HEARTBEAT_S = 30.0
LOCK_STALE_S = 600.0
LOCK_WAIT_S = 1800.0
LOCK_POLL_S = 2.0


# --- helpers ------------------------------------------------------------------------------------

def iso_now(t: float | None = None) -> str:
    """Local time, ISO 8601 to the second (only in non-deterministic files)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() if t is None else t))


def _json_default(obj: Any) -> Any:
    if isinstance(obj, os.PathLike):
        return as_posix(os.fspath(obj))
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=str)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def canonical_json(obj: Any) -> str:
    """Key-sorted compact JSON used for hashing (equal content -> equal text)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
                      default=_json_default)


def h8(obj: Any) -> str:
    return hashlib.sha1(canonical_json(obj).encode("utf-8")).hexdigest()[:8]


def file_sha1(path: str | os.PathLike, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def plain(obj: Any) -> Any:
    """Any probe/stage result -> JSON-ready data (dataclass, mapping, namedtuple, object)."""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, os.PathLike):
        return as_posix(os.fspath(obj))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k): plain(v) for k, v in obj.items()}
    if hasattr(obj, "_asdict"):
        return plain(obj._asdict())
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [plain(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: plain(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


def info_get(info: Any, *keys: str, default: Any = None) -> Any:
    """First present field of a VideoInfo (attribute or mapping key)."""
    for key in keys:
        if isinstance(info, Mapping):
            if info.get(key) is not None:
                return info[key]
        elif getattr(info, key, None) is not None:
            return getattr(info, key)
    return default


def dir_size(path: str | os.PathLike) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _clear_readonly(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except FileNotFoundError:
        pass


def rmtree_robust(path: str | os.PathLike, attempts: int = 6) -> bool:
    """Delete a folder tree, clearing read-only bits and retrying with backoff (antivirus, the
    indexer or an open viewer can hold files for a moment on Windows). True when it is gone."""
    p = Path(path)
    delay = 0.05
    for _ in range(attempts):
        if not os.path.lexists(p):
            return True
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(p, onexc=_clear_readonly)
            else:  # pragma: no cover - Python 3.10/3.11
                shutil.rmtree(p, onerror=lambda f, q, e: _clear_readonly(f, q, e[1]))
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(delay)
            delay *= 2
    return not os.path.lexists(p)


def fresh_dir(path: str | os.PathLike, attempts: int = 10) -> Path:
    """An empty folder at ``path`` (deleting what is there). On Windows a folder just deleted can
    linger in a delete-pending state while another process (antivirus, indexer, a viewer) still
    has a handle on it; creating it again then fails for a moment, so mkdir is retried."""
    p = Path(path)
    delay = 0.05
    for _ in range(attempts):
        rmtree_robust(p)
        try:
            p.mkdir(parents=True)
            return p
        except FileExistsError:
            if p.is_dir() and not any(p.iterdir()):
                return p
        except PermissionError:
            pass
        time.sleep(delay)
        delay = min(delay * 2, 1.0)
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- stage functions (lazy imports; test hooks) -------------------------------------------------

STAGE_OVERRIDES: dict[str, Callable[..., Any]] = {}   # tests: in-process replacements by name
SHIM_ENV = "VA_STAGES_SHIM"   # tests: path of a .py file defining replacements (inherited by spawn children)

_STAGE_HOME = {   # name -> (module, attribute)
    "probe_video": ("va.probe", "probe_video"),
    "effective_frame_params": ("va.frame_params", "effective_frame_params"),   # numpy-free (warm runs)
    "run_frames_stage": ("va.extract", "run_frames_stage"),
    "estimate_frames_seconds": ("va.extract", "estimate_seconds"),
    "effective_cue_params": ("va.vtt", "effective_cue_params"),
    "run_cues_stage": ("va.vtt", "run_cues_stage"),
    "effective_batch_params": ("va.batching", "effective_batch_params"),
    "run_batches_stage": ("va.render", "run_batches_stage"),
}
EXEC_ONLY_FRAME_KEYS = ("sequential",)   # execution choices with identical output: not in the frames key
_shim_cache: dict[str, Any] = {}


class StageUnavailable(RuntimeError):
    """A stage module is missing or broken (incomplete installation)."""


def _load_shim() -> Any:
    path = os.environ.get(SHIM_ENV, "").strip()
    if not path:
        return None
    if path not in _shim_cache:
        spec = importlib.util.spec_from_file_location("va_stages_shim", path)
        if spec is None or spec.loader is None:
            raise StageUnavailable(f"{SHIM_ENV}={path!r} não é um módulo Python")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _shim_cache[path] = module
    return _shim_cache[path]


def stage_fn(name: str) -> Callable[..., Any]:
    """The stage function ``name`` (see ``_STAGE_HOME``), imported on first use."""
    if name in STAGE_OVERRIDES:
        return STAGE_OVERRIDES[name]
    shim = _load_shim()
    if shim is not None and hasattr(shim, name):
        return getattr(shim, name)
    module_name, attr = _STAGE_HOME[name]
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise StageUnavailable(f"{module_name} não pôde ser importado ({exc})") from exc
    fn = getattr(module, attr, None)
    if fn is None:
        raise StageUnavailable(f"{module_name} não define {attr}()")
    return fn


# --- keys ---------------------------------------------------------------------------------------

def video_fingerprint(path: str | os.PathLike) -> str:
    """fp8 = sha1(NFC.casefold(absolute path) | size | mtime_ns)[:8]: a moved, renamed or edited
    video gets a new key (and is extracted again)."""
    absolute = os.path.normpath(os.path.abspath(os.fspath(path)))
    st = os.stat(absolute)
    return hashlib.sha1(f"{nfc_casefold(absolute)}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")).hexdigest()[:8]


def video_key(path: str | os.PathLike) -> str:
    return f"{slugify(Path(path).stem)}-{video_fingerprint(path)}"


def stage_keys(vkey: str, vtt_path: str | os.PathLike, params: dict, meta: dict | None = None) -> dict:
    """``{"cues", "frames", "batches"}`` h8 keys from the effective params of each stage.

    ``params`` = ``{"cues": {...}, "frames": {...}, "batches": {...}}`` (effective, JSON data).
    ``meta``: values rendered into the batch outputs (title, video_path, vtt_path).
    """
    cues = h8({"stage": "cues", "version": VTT_VERSION, "vtt_sha1": file_sha1(vtt_path), "params": params["cues"]})
    frame_params = {k: v for k, v in dict(params["frames"]).items() if k not in EXEC_ONLY_FRAME_KEYS}
    frames = h8({"stage": "frames", "version": ALGO_VERSION, "video_key": vkey, "params": frame_params})
    batches = h8({"stage": "batches", "version": RENDER_VERSION, "cues": cues, "frames": frames,
                  "params": params["batches"], "meta": meta or {}})
    return {"cues": cues, "frames": frames, "batches": batches}


def stage_dir(video_dir: str | os.PathLike, stage: str, key: str) -> Path:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    return Path(video_dir) / f"{stage}-{key}"


def expand_force(force: str | None) -> set[str]:
    """--force value -> stages to rebuild (the named one and everything downstream)."""
    if not force:
        return set()
    return {"all": set(STAGES), "cues": {"cues", "batches"}, "frames": {"frames", "batches"},
            "batches": {"batches"}}[force]


# --- stage.json -----------------------------------------------------------------------------------

def read_stage(sdir: str | os.PathLike) -> dict | None:
    try:
        doc = read_json(Path(sdir) / "stage.json")
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def write_stage_json(sdir: str | os.PathLike, stage: str, key: str, **fields: Any) -> dict:
    """Mark a stage folder complete: written last, atomically (temp file + os.replace)."""
    doc = {"schema": STAGE_SCHEMA, "stage": stage, "key": key, "status": "complete", "created": iso_now(),
           "version": VERSION, **{k: plain(v) for k, v in fields.items()}}
    write_json(Path(sdir) / "stage.json", doc)
    return doc


def _frames_files_ok(sdir: Path) -> bool:
    try:
        doc = read_json(sdir / "frames.json")
        files = {f["file"] for f in doc["frames"]}
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return bool(files) and all(os.path.isfile(sdir / name) for name in files)


def stage_valid(sdir: str | os.PathLike, stage: str, key: str) -> bool:
    """Complete ``stage.json`` with the same key, plus the stage's own files: cues.json; frames.json
    and every JPEG it lists; summary.json, batches.json and every batch_NNN.md."""
    sdir = Path(sdir)
    doc = read_stage(sdir)
    if not doc or doc.get("status") != "complete" or doc.get("key") != key or doc.get("stage") != stage:
        return False
    if stage == "cues":
        return (sdir / "cues.json").is_file()
    if stage == "frames":
        return _frames_files_ok(sdir)
    result = doc.get("result") or {}
    n = result.get("n_batches")
    if not isinstance(n, int) or n < 1 or not isinstance(result.get("batch_images"), list):
        return False
    if not ((sdir / "summary.json").is_file() and (sdir / "batches.json").is_file()):
        return False
    return all((sdir / f"batch_{i:03d}.md").is_file() for i in range(1, n + 1))


def stage_hits(video_dir: str | os.PathLike, keys: dict, force: set[str] | frozenset[str] | tuple = ()) -> dict:
    """``{"cues", "frames", "batches"}`` -> True when that stage is valid and not forced; a stage
    rebuilt upstream also rebuilds the batches (same rule as ``prepare_video``). Only ``stat``
    and small JSON reads: no subprocess, no frame engine import."""
    hits = {s: s not in force and stage_valid(stage_dir(video_dir, s, keys[s]), s, keys[s]) for s in STAGES}
    if not (hits["cues"] and hits["frames"]):
        hits["batches"] = False
    return hits


def cached_probe(video_dir: str | os.PathLike, video_path: str | os.PathLike) -> dict | None:
    """The ``probe.json`` that ``prepare_video`` keeps per video, as a plain dict, or None when it is
    missing, unreadable or not about this file. The ``video_key`` folder is already tied to the
    file's path, size and mtime, so a fully cached run needs no ffprobe."""
    try:
        doc = read_json(Path(video_dir) / "probe.json")
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    try:
        usable = float(doc["duration_s"]) > 0 and int(doc["width"]) > 0 and int(doc["height"]) > 0
        same = nfc_casefold(os.path.normpath(str(doc["path"]))) == nfc_casefold(
            os.path.normpath(os.path.abspath(os.fspath(video_path))))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return doc if usable and same else None


def touch_last_used(video_dir: str | os.PathLike) -> None:
    write_text(Path(video_dir) / "last_used", iso_now() + "\n")


# --- lock -----------------------------------------------------------------------------------------

class LockTimeout(TimeoutError):
    def __init__(self, path: Path, waited_s: float, holder: str):
        self.path, self.waited_s, self.holder = path, waited_s, holder
        super().__init__(f"trava {as_posix(path)} ocupada há {waited_s:.0f} s ({holder or 'dono desconhecido'})")


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


class VideoLock:
    """Heartbeat lock file (spec section 7). Use as a context manager.

    The holder's thread refreshes the file's mtime every ``heartbeat_s``. Another process
    considers it stale after ``stale_s`` without a refresh and breaks it (rename to a unique
    name, verify, delete); otherwise it polls every ``poll_s`` up to ``wait_s`` and then raises
    LockTimeout. Processes are never signalled.
    """

    def __init__(self, path: str | os.PathLike, *, wait_s: float = LOCK_WAIT_S, poll_s: float = LOCK_POLL_S,
                 stale_s: float = LOCK_STALE_S, heartbeat_s: float = LOCK_HEARTBEAT_S,
                 on_wait: Callable[[str], None] | None = None):
        self.path = Path(path)
        self.wait_s, self.poll_s, self.stale_s, self.heartbeat_s = wait_s, poll_s, stale_s, heartbeat_s
        self.on_wait = on_wait
        self.token = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self.broke_stale = False
        self.lost = False
        self.waited_s = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _holder(self) -> str:
        text = _read_text(self.path) or ""
        try:
            d = json.loads(text)
            return f"pid {d.get('pid')} em {d.get('host')}, desde {d.get('created')}"
        except ValueError:
            return ""

    def _break_if_stale(self) -> bool:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if time.time() - st.st_mtime < self.stale_s:
            return False
        before = _read_text(self.path)
        grave = self.path.with_name(f"{self.path.name}.stale-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        try:
            os.rename(self.path, grave)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            fresh = time.time() - os.stat(grave).st_mtime < self.stale_s
        except OSError:
            fresh = False
        if fresh or _read_text(grave) != before:  # someone re-created it in between: put it back
            with contextlib.suppress(OSError):
                os.rename(grave, self.path)
            return False
        with contextlib.suppress(OSError):
            os.remove(grave)
        self.broke_stale = True
        return True

    def acquire(self) -> "VideoLock":
        start = time.monotonic()
        announced = False
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._break_if_stale():
                    continue
            except PermissionError:  # Windows: the previous lock file is still being deleted
                if time.monotonic() - start >= self.wait_s:
                    raise LockTimeout(self.path, time.monotonic() - start, self._holder()) from None
                time.sleep(0.05)
                continue
            else:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    json.dump({"token": self.token, "pid": os.getpid(), "host": socket.gethostname(),
                               "created": iso_now(), "argv": sys.argv[:3]}, fh, ensure_ascii=False)
                self.waited_s = time.monotonic() - start
                self._thread = threading.Thread(target=self._beat, name="va-lock-heartbeat", daemon=True)
                self._thread.start()
                return self
            waited = time.monotonic() - start
            if waited >= self.wait_s:
                raise LockTimeout(self.path, waited, self._holder())
            if not announced and self.on_wait is not None:
                self.on_wait(f"esperando a trava {as_posix(self.path)} ({self._holder() or 'outro processo'})")
                announced = True
            time.sleep(min(self.poll_s, max(0.0, self.wait_s - waited)) or 0.01)

    def _beat(self) -> None:
        while not self._stop.wait(self.heartbeat_s):
            try:
                os.utime(self.path, None)
            except FileNotFoundError:
                self.lost = True
                return
            except OSError:
                pass

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        for delay in (0, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6):  # an antivirus may hold the new file briefly
            time.sleep(delay)
            text = _read_text(self.path)
            if text is None:
                return
            try:
                mine = json.loads(text).get("token") == self.token
            except ValueError:
                mine = False
            if not mine:
                return
            try:
                os.remove(self.path)
                return
            except FileNotFoundError:
                return
            except OSError:
                continue

    def __enter__(self) -> "VideoLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


# --- GC -------------------------------------------------------------------------------------------

def gc_cache(cache_dir: str | os.PathLike, days: float, *, now: float | None = None,
             stale_s: float = LOCK_STALE_S) -> dict:
    """Delete whole ``<video_key>`` folders whose ``last_used`` is older than ``days``. Folders
    with a live lock are skipped. Returns counts and the space freed."""
    now = time.time() if now is None else now
    cutoff = now - float(days) * 86400.0
    removed, skipped, failed = [], [], []
    kept = 0
    root = Path(cache_dir)
    entries = sorted((e for e in root.iterdir() if e.is_dir()), key=lambda p: nfc_casefold(p.name)) if root.is_dir() else []
    for entry in entries:
        try:
            last = (entry / "last_used").stat().st_mtime
        except OSError:
            last = entry.stat().st_mtime
        if last >= cutoff:
            kept += 1
            continue
        lock = entry / ".lock"
        try:
            if now - lock.stat().st_mtime < stale_s:
                skipped.append({"key": entry.name, "reason": "em uso (trava ativa)"})
                continue
        except OSError:
            pass
        size = dir_size(entry)
        item = {"key": entry.name, "bytes": size, "last_used": iso_now(last)}
        (removed if rmtree_robust(entry) else failed).append(item)
    freed = sum(r["bytes"] for r in removed)
    return {"days": days, "removed": removed, "kept": kept, "skipped": skipped, "failed": failed,
            "freed_bytes": freed, "freed_mb": round(freed / 1e6, 1)}


# --- worker ---------------------------------------------------------------------------------------

def child_init() -> None:
    """ProcessPool initializer: whatever a worker process writes to stdout (Python or a child
    program) goes to stderr, because stdout carries only prepare.py's JSON line."""
    with contextlib.suppress(Exception):
        sys.stdout.flush()
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())


class _Log:
    """Progress for one video: log file (always) and stderr (unless quiet)."""

    def __init__(self, job: dict):
        self.path = job.get("log_path")
        self.quiet = bool(job.get("quiet"))
        self.prefix = job.get("slug") or job.get("vid") or "video"
        self.progress_path = job.get("progress_path")
        self.vid = job.get("vid")
        self._last_progress = 0.0
        self._last_echo = 0.0

    def __call__(self, msg: str, echo: bool = True) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {self.prefix}: {msg}"
        if self.path:
            with contextlib.suppress(OSError):
                with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
        if echo and not self.quiet:
            print(line, file=sys.stderr, flush=True)

    def state(self, stage: str, state: str, info: Any = None) -> None:
        if not self.progress_path:
            return
        doc = {"vid": self.vid, "stage": stage, "state": state, "updated": iso_now(), "info": plain(info)}
        with contextlib.suppress(OSError, ValueError, TypeError):
            write_json(self.progress_path, doc)

    def progress(self, stage: str) -> Callable[[Any], None]:
        def cb(info: Any = None) -> None:
            now = time.monotonic()
            if now - self._last_progress >= 1.0:
                self._last_progress = now
                self.state(stage, "processando", info)
            if now - self._last_echo >= 15.0:
                self._last_echo = now
                self(f"{stage} {_progress_text(info)}".rstrip())
        return cb


def _progress_text(info: Any) -> str:
    if isinstance(info, Mapping):
        for key in ("pct", "percent", "progress"):
            v = info.get(key)
            if isinstance(v, (int, float)):
                return f"{v:.0f}%" if v > 1 else f"{100 * v:.0f}%"
        t = info.get("done_s", info.get("t"))
        d = info.get("total_s") or info.get("duration") or info.get("duration_s")
        if isinstance(t, (int, float)) and isinstance(d, (int, float)) and d > 0:
            extra = f", {info['frames']} frames" if isinstance(info.get("frames"), int) else ""
            return f"{min(100.0, 100 * t / d):.0f}%{extra}"
    return ""


def _batches_list(index: Any) -> list[dict]:
    if isinstance(index, Mapping):
        lst = index.get("batches")
    else:
        lst = index
    if not isinstance(lst, list) or not all(isinstance(b, Mapping) for b in lst):
        raise ValueError("run_batches_stage não devolveu a lista de lotes")
    return lst


def _batch_result(index: Any, summary: Any, warnings: list[str]) -> dict:
    lst = _batches_list(index)
    if not lst:
        raise ValueError("nenhum lote gerado")
    images, frames = [], []
    for b in lst:
        nf = b.get("n_frames")
        ni = b.get("n_images")
        if ni is None:
            ni = nf
            if "sem n_images" not in " ".join(warnings):
                warnings.append("lotes sem n_images: usando n_frames (revisitas contam como imagem)")
        if not isinstance(ni, int) or isinstance(ni, bool) or ni < 1:
            raise ValueError(f"lote {b.get('n') or b.get('id')}: n_images inválido ({ni!r})")
        images.append(ni)
        frames.append(nf if isinstance(nf, int) and not isinstance(nf, bool) else ni)
    n_frames = sum(frames)
    if isinstance(summary, Mapping) and isinstance(summary.get("n_frames"), int) and not any(
            isinstance(b.get("n_frames"), int) for b in lst):
        n_frames = summary["n_frames"]
    return {"n_batches": len(lst), "batch_images": images, "batch_frames": frames, "n_frames": n_frames}


def _summary_for_disk(summary: Any, index: Any, meta: dict, sdir: Path, result: dict) -> dict:
    """Fallback summary.json when the render stage did not write one (keeps known fields)."""
    doc = dict(summary) if isinstance(summary, Mapping) else {}
    for key in ("title", "video_path", "vtt_path", "video_key"):
        doc.setdefault(key, meta.get(key))
    doc.setdefault("n_frames", result["n_frames"])
    doc.setdefault("n_batches", result["n_batches"])
    paths = dict(doc.get("paths") or {})
    paths.setdefault("frames_dir", meta.get("frames_dir"))
    paths.setdefault("batches_dir", as_posix(sdir))
    paths.setdefault("frames_json", meta.get("frames_json"))
    paths.setdefault("cues_json", meta.get("cues_json"))
    paths.setdefault("timeline_json", as_posix(sdir / "timeline.json"))
    paths.setdefault("transcript_md", as_posix(sdir / "transcript.md"))
    paths.setdefault("batches_json", as_posix(sdir / "batches.json"))
    doc["paths"] = paths
    doc.setdefault("warnings", [])
    return doc


def _write_source(vdir: Path, video_path: str, vkey: str, title: str) -> None:
    try:
        st = os.stat(video_path)
    except OSError:
        return
    doc = {"path": as_posix(os.path.abspath(video_path)), "title": title, "video_key": vkey,
           "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    target = vdir / "source.json"
    try:
        if read_json(target) == doc:
            return
    except (OSError, ValueError):
        pass
    write_json(target, doc)


def prepare_video(job: dict) -> dict:
    """Build or reuse the cues, frames and batches stages of one video (plain data in and out).

    ``job`` keys: vid, slug, title, video_path, vtt_path, video_key, video_dir, keys{cues,frames,
    batches}, params{cues,frames,batches}, info (VideoInfo or None: probed on demand),
    info_from_cache (info is the cached probe.json dict: re-probed if a stage must be built), force
    (list of stages), meta (title/video_path/vtt_path for render), ffmpeg (version text),
    expected_fp (resume: refuse to rebuild from a changed source), log_path, progress_path,
    quiet, lock (optional VideoLock kwargs); warm / cost are scheduling hints of prepare.py.

    Returns ``{"vid", "ok", "error", "cache_hit", "timing_s", "warnings", "summary", "batches",
    "dirs", "info", "language"}``; ``error`` = ``{"code", "detail"}`` when ``ok`` is false.
    """
    log = _Log(job)
    out: dict[str, Any] = {"vid": job.get("vid"), "ok": False, "error": None,
                           "cache_hit": {s: False for s in STAGES}, "timing_s": {}, "warnings": [],
                           "summary": None, "batches": None, "dirs": {}, "info": None, "language": None}
    stage_now = "início"
    with contextlib.redirect_stdout(sys.stderr):  # stdout carries only prepare.py's JSON line
        try:
            vdir = Path(job["video_dir"])
            vdir.mkdir(parents=True, exist_ok=True)
            keys, params = job["keys"], job["params"]
            dirs = {s: stage_dir(vdir, s, keys[s]) for s in STAGES}
            out["dirs"] = {s: as_posix(d) for s, d in dirs.items()}
            lock_kw = dict(job.get("lock") or {})
            with VideoLock(vdir / ".lock", on_wait=lambda m: log(m), **lock_kw):
                touch_last_used(vdir)
                force = set(job.get("force") or ())
                info = job.get("info")
                # a cached probe.json (plain dict) is enough to report on a fully cached video; a stage
                # that has to be built after all gets a fresh VideoInfo from ffprobe
                fresh = not job.get("info_from_cache")
                video_path, vtt_path = job["video_path"], job["vtt_path"]

                def need_info() -> Any:
                    nonlocal info, fresh
                    if not os.path.isfile(video_path):
                        raise FileNotFoundError(f"vídeo ausente: {video_path} (o cache precisa ser refeito)")
                    expected = job.get("expected_fp")
                    if expected and video_fingerprint(video_path) != expected:
                        raise RuntimeError(f"o vídeo mudou desde o run (tamanho ou data): {video_path}")
                    if info is None or not fresh:
                        log("ffprobe")
                        info = stage_fn("probe_video")(Path(video_path))
                        fresh = True
                    return info

                rebuild = {s for s in STAGES if s in force or not stage_valid(dirs[s], s, keys[s])}
                if rebuild & {"cues", "frames"}:
                    rebuild.add("batches")
                cues_doc = frames_doc = None

                if "cues" in rebuild:
                    stage_now = "legenda"
                    sdir = fresh_dir(dirs["cues"])
                    duration = info_get(need_info(), "duration_s", "duration")
                    if not os.path.isfile(vtt_path):
                        raise FileNotFoundError(f"legenda ausente: {vtt_path}")
                    log.state("cues", "processando")
                    t0 = time.perf_counter()
                    cues_doc = stage_fn("run_cues_stage")(Path(vtt_path), params["cues"], sdir, duration)
                    if not (sdir / "cues.json").is_file():
                        write_json(sdir / "cues.json", cues_doc)
                    out["timing_s"]["cues"] = round(time.perf_counter() - t0, 3)
                    write_stage_json(sdir, "cues", keys["cues"], video_key=job["video_key"], params=params["cues"],
                                     inputs={"vtt": as_posix(vtt_path)}, timing_s=out["timing_s"]["cues"],
                                     result={"n_cues": len(cues_doc.get("cues") or []) if isinstance(cues_doc, Mapping) else None})
                    log(f"legenda pronta ({out['timing_s']['cues']:.1f} s)")
                else:
                    out["cache_hit"]["cues"] = True

                if "frames" in rebuild:
                    stage_now = "frames"
                    sdir = fresh_dir(dirs["frames"])
                    vinfo = need_info()
                    log.state("frames", "processando")
                    log("extraindo frames")
                    t0 = time.perf_counter()
                    frames_doc = stage_fn("run_frames_stage")(Path(video_path), vinfo, params["frames"], sdir,
                                                              log.progress("frames"))
                    if not (sdir / "frames.json").is_file():
                        write_json(sdir / "frames.json", frames_doc)
                    out["timing_s"]["frames"] = round(time.perf_counter() - t0, 3)
                    n = len(frames_doc.get("frames") or []) if isinstance(frames_doc, Mapping) else None
                    write_stage_json(sdir, "frames", keys["frames"], video_key=job["video_key"], params=params["frames"],
                                     inputs={"video": as_posix(video_path)}, ffmpeg=job.get("ffmpeg"),
                                     timing_s=out["timing_s"]["frames"],
                                     result={"n_frames": n, "n_unique": frames_doc.get("n_unique") if isinstance(frames_doc, Mapping) else None})
                    if not stage_valid(sdir, "frames", keys["frames"]):
                        raise RuntimeError("o estágio de frames terminou sem frames.json válido ou sem os JPEGs listados")
                    log(f"{n} frames ({out['timing_s']['frames']:.1f} s)")
                else:
                    out["cache_hit"]["frames"] = True

                if "batches" in rebuild:
                    stage_now = "lotes"
                    sdir = fresh_dir(dirs["batches"])
                    if cues_doc is None:
                        cues_doc = read_json(dirs["cues"] / "cues.json")
                    if frames_doc is None:
                        frames_doc = read_json(dirs["frames"] / "frames.json")
                    vtt_info = cues_doc.get("vtt") if isinstance(cues_doc, Mapping) else None
                    language = (vtt_info or {}).get("language") if isinstance(vtt_info, Mapping) else None
                    meta = {**(job.get("meta") or {}), "video_key": job["video_key"],
                            "frames_dir": as_posix(dirs["frames"]), "frames_json": as_posix(dirs["frames"] / "frames.json"),
                            "cues_json": as_posix(dirs["cues"] / "cues.json")}
                    if language:
                        meta["language"] = language  # labels follow the language detected in the subtitle
                    log.state("batches", "processando")
                    t0 = time.perf_counter()
                    res = stage_fn("run_batches_stage")(frames_doc, cues_doc, params["batches"], sdir, meta)
                    if not isinstance(res, Mapping):
                        raise ValueError("run_batches_stage não devolveu um dicionário")
                    result = _batch_result(res.get("batches"), res.get("summary"), out["warnings"])
                    if not (sdir / "batches.json").is_file():
                        index = res.get("batches")
                        write_json(sdir / "batches.json", index if isinstance(index, Mapping) else
                                   {"n_batches": result["n_batches"], "params": params["batches"], "batches": index})
                    if not (sdir / "summary.json").is_file():
                        write_json(sdir / "summary.json", _summary_for_disk(res.get("summary"), res.get("batches"), meta, sdir, result))
                    out["timing_s"]["batches"] = round(time.perf_counter() - t0, 3)
                    write_stage_json(sdir, "batches", keys["batches"], video_key=job["video_key"], params=params["batches"],
                                     inputs={"cues": keys["cues"], "frames": keys["frames"]},
                                     timing_s=out["timing_s"]["batches"], result=result, language=language)
                    if not stage_valid(sdir, "batches", keys["batches"]):
                        raise RuntimeError("o estágio de lotes terminou sem summary.json, batches.json ou batch_NNN.md")
                    log(f"{result['n_batches']} lotes ({out['timing_s']['batches']:.1f} s)")
                else:
                    out["cache_hit"]["batches"] = True

                stage_now = "resumo"
                sdoc = read_stage(dirs["batches"]) or {}
                out["batches"] = sdoc.get("result")
                out["language"] = sdoc.get("language")
                summary = read_json(dirs["batches"] / "summary.json")
                out["summary"] = summary if isinstance(summary, dict) else {}
                if info is not None:
                    out["info"] = plain(info)
                    with contextlib.suppress(OSError, ValueError, TypeError):
                        write_json(vdir / "probe.json", out["info"])
                _write_source(vdir, video_path, job["video_key"], job.get("title") or Path(video_path).stem)
                out["ok"] = True
                log.state("batches", "pronto")
        except KeyboardInterrupt:
            raise
        except LockTimeout as exc:
            out["error"] = {"code": "video_falhou",
                            "detail": f"{exc}: outro prepare.py está usando este vídeo; espere ele terminar ou confira se ficou preso"}
        except StageUnavailable as exc:
            out["error"] = {"code": "instalacao_incompleta", "detail": str(exc)}
        except Exception as exc:  # one video failing must not stop the others
            import traceback  # lazy: only on failures (keeps the warm path light)
            out["error"] = {"code": "video_falhou", "detail": f"{stage_now}: {type(exc).__name__}: {exc}"}
            log("ERRO " + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip(), echo=False)
        if out["error"]:
            log(f"falhou: {out['error']['detail']}")
            log.state(stage_now, "falhou", out["error"])
    return out
