"""Webcam timeline: prepare.py runs scripts/webcam_mask.py in the DeepFace environment and uses its spans
as the ``ignore_timeline`` of the video (reference/parameters.md, "Region over time").

Layout (``<home>/cache/<video_key>/``)::

    webcam-<h8>/   timeline.json  webcam_mask.log  stage.json

- Key: h8(WEBCAM_VERSION, video_key, the ``yunet_score_threshold`` variable as given, None without
  it, and the detector: the deepface version and the YuNet weights' SHA-256 that the setup installs,
  the only ones prepare.py runs). The video_key already changes when the video is moved, renamed or
  edited.
- ``timeline.json`` is the file of ``webcam_mask.py --timeline``: ``"scan": "probe"`` when the probe
  covered the video and saw no face in the same place in 2 samples (empty, the full scan did not run;
  a video of 10 s or less gets a 1-sample probe, which cannot say that, so it goes to the full scan),
  ``"full"`` after the full scan (one sample every 2 s, timed by the frame's timestamp). A probe that
  did not cover the video (``probe.complete`` false) is never kept as "no webcam": webcam_mask.py goes
  on to the full scan, and a probe-only file without ``complete`` is refused here and in the cache.
  ``webcam_mask.log`` keeps the script's report, in Portuguese.
- ``stage.json`` is written last: a folder without it is incomplete and is made again.
- Modes (request ``webcam_timeline``): ``auto`` takes any complete timeline and, without one, runs
  the probe first; ``on`` needs a full scan (it replaces a probe-only timeline); ``off`` does not run
  it. A video with its own ``ignore_timeline`` file keeps that file.

Numpy-free and DeepFace-free: everything heavy runs in the other environment, in a subprocess.
"""
from __future__ import annotations

import contextlib
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from . import WEBCAM_DEFAULTS, WEBCAM_VERSION, cache
from .common import as_posix, read_json
from .frame_params import _norm_timeline

__all__ = ["SCORE_ENV", "STAGE", "WebcamError", "cached", "estimate_seconds", "make_timeline", "probe_samples",
           "run_process", "score_setting", "tiles_per_frame", "webcam_dir", "webcam_key"]

STAGE = "webcam"
SCORE_ENV = "yunet_score_threshold"   # read by webcam_mask.py (and by DeepFace)
TILE_W, TILE_H = 640, 540             # webcam_mask.py's tiles (--tiles overlap: half overlapping)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class WebcamError(RuntimeError):
    """webcam_mask.py failed or wrote no valid timeline; the message ends with the end of its log."""


def score_setting() -> str | None:
    """The YuNet score set in the environment (``yunet_score_threshold``), as given; None without it."""
    v = os.environ.get(SCORE_ENV)
    return None if v is None else v.strip()


def webcam_key(video_key: str, score: str | None = None) -> str:
    """The detector is in the key: the deepface version and the YuNet weights' SHA-256 that the setup
    installs, the only ones prepare.py runs (environment.deepface_status), so a timeline is not reused
    when the skill moves to another detector."""
    d = WEBCAM_DEFAULTS
    return cache.h8({"stage": STAGE, "version": WEBCAM_VERSION, "video_key": video_key,
                     "params": {SCORE_ENV: score, "tiles": "overlap"},
                     "detector": {"deepface": d["deepface_version"], "yunet_sha256": d["yunet_sha256"]}})


def webcam_dir(video_dir: str | os.PathLike, key: str) -> Path:
    return Path(video_dir) / f"{STAGE}-{key}"


def _probe_covered(probe: Any) -> bool:
    """A probe-only timeline counts only when the probe covered the video (webcam_mask.py's ``complete``)."""
    return isinstance(probe, dict) and probe.get("complete") is True


def cached(video_dir: str | os.PathLike, key: str, need_full: bool = False) -> dict | None:
    """The complete timeline of ``webcam-<key>``: ``{"spans", "scan", "path", "seconds"}``; None when
    it is missing, incomplete or unreadable, or is only a probe and ``need_full`` (or a probe that did
    not cover the video)."""
    sdir = webcam_dir(video_dir, key)
    doc = cache.read_stage(sdir)
    if not doc or doc.get("status") != "complete" or doc.get("stage") != STAGE or doc.get("key") != key:
        return None
    scan = doc.get("scan")
    if scan not in ("probe", "full") or (scan == "probe" and (need_full or not _probe_covered(doc.get("probe")))):
        return None
    try:
        tl = read_json(sdir / "timeline.json")
        spans = _norm_timeline(tl["ignore_timeline"]) if isinstance(tl.get("ignore_timeline"), list) else None
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None
    if spans is None:
        return None
    return {"spans": spans, "scan": scan, "path": as_posix(sdir / "timeline.json"), "seconds": doc.get("timing_s")}


def probe_samples(duration_s: float) -> int:
    """Frames the probe looks at: one every ``probe_every_s`` (10 s), up to ``probe_samples`` (60)."""
    d = WEBCAM_DEFAULTS
    return max(1, min(int(d["probe_samples"]), math.ceil(max(0.0, float(duration_s)) / float(d["probe_every_s"]))))


def _starts(length: int, size: int) -> int:
    """How many tiles of ``size`` cover ``length``, half overlapping, the last one touching the edge."""
    if length <= size:
        return 1
    step = size // 2
    n = len(range(0, length - size + 1, step))
    return n if (n - 1) * step == length - size else n + 1


def tiles_per_frame(width: float, height: float) -> int:
    """YuNet calls per sample (15 at 1080p, 6 at 720p, 63 at 3024x1964)."""
    return _starts(int(width), TILE_W) * _starts(int(height), TILE_H)


def estimate_seconds(info: Any, scan: str) -> float:
    """Rough seconds of webcam_mask.py on this video (cost model in WEBCAM_DEFAULTS). ``probe``: the probe
    plus the full scan, as if a face showed up (the worst case); ``full``: the full scan only. In a
    variable frame rate video (ffprobe's ``vfr``) the probe also counts a decoding pass: OpenCV's seek
    misses there, and the probe goes through the video from the start."""
    d = WEBCAM_DEFAULTS
    dur = float(cache.info_get(info, "duration_s", "duration", default=0.0) or 0.0)
    w = float(cache.info_get(info, "width", default=1920) or 1920)
    h = float(cache.info_get(info, "height", default=1080) or 1080)
    fps = float(cache.info_get(info, "fps_avg", "fps", default=30.0) or 30.0)
    tiles = tiles_per_frame(w, h)
    decode = dur * fps * w * h / 1e6 * float(d["decode_s_per_mpx"])
    full = decode + dur / 2.0 * tiles * float(d["detect_s_per_tile"])
    probe = 0.0
    if scan == "probe":
        probe = probe_samples(dur) * (float(d["probe_seek_s"]) + tiles * float(d["detect_s_per_tile"]))
        if cache.info_get(info, "vfr", default=False):
            probe += decode
    return float(d["startup_s"]) + probe + full


def run_process(cmd: list[str], log_path: str, env: dict | None = None) -> int:
    """Run webcam_mask.py (``cmd``) with stdout and stderr in ``log_path``. Its stdin is a pipe that stays
    open while prepare.py waits: with --watch-stdin the script exits when the pipe closes, so it does not
    outlive a prepare.py killed hard, which closes the pipe but kills no child process. If prepare.py is
    interrupted, the process is killed. Test hook: the stage name ``run_webcam_mask`` (``cache.stage_fn``)."""
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT, env=env,
                                creationflags=_NO_WINDOW)
        try:
            return proc.wait()
        except BaseException:
            with contextlib.suppress(OSError):
                proc.kill()
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
            raise
        finally:
            with contextlib.suppress(OSError):
                proc.stdin.close()


def _tail(path: Path, lines: int = 2) -> str:
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return "no log"
    last = [line.strip() for line in text.splitlines() if line.strip()][-lines:]
    return " | ".join(last)[-400:] or "empty log"


def make_timeline(*, python: str | os.PathLike, script: str | os.PathLike, video_path: str | os.PathLike,
                  video_dir: str | os.PathLike, key: str, scan: str, duration_s: float) -> dict:
    """Run webcam_mask.py on the video and keep its timeline in ``webcam-<key>``. ``scan``: ``probe``
    (auto: the probe first) or ``full`` (on). Returns ``{"spans", "scan", "path", "seconds"}``; raises
    WebcamError, with the end of the log, when the script fails or writes no valid timeline."""
    sdir = cache.fresh_dir(webcam_dir(video_dir, key))
    part, final, log_path = sdir / "timeline.part.json", sdir / "timeline.json", sdir / "webcam_mask.log"
    cmd = [str(python), "-B", str(script), str(video_path), "--timeline", str(part)]
    n = probe_samples(duration_s) if scan == "probe" else 0
    if n:
        cmd += ["--probe", str(n)]
    cmd.append("--watch-stdin")                          # with run_process's pipe (above)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}   # DeepFace logs emojis; the log is UTF-8
    t0 = time.perf_counter()
    code = cache.stage_fn("run_webcam_mask")(cmd, str(log_path), env)
    seconds = round(time.perf_counter() - t0, 1)
    if code != 0:
        raise WebcamError(f"webcam_mask.py exited with {code}: {_tail(log_path)}")
    try:
        doc = read_json(part)
        spans = _norm_timeline(doc["ignore_timeline"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WebcamError(f"webcam_mask.py wrote no valid timeline ({type(exc).__name__}: {exc}): "
                          f"{_tail(log_path)}") from None
    made = doc.get("scan") if doc.get("scan") in ("probe", "full") else "full"
    if made == "probe" and not _probe_covered(doc.get("probe")):
        raise WebcamError(f"webcam_mask.py stopped at a probe that did not cover the video: {_tail(log_path)}")
    os.replace(part, final)
    cache.write_stage_json(sdir, STAGE, key, scan=made, n_spans=len(spans), timing_s=seconds,
                           probe=doc.get("probe"), min_score=doc.get("min_score"),
                           inputs={"video": as_posix(video_path)}, command=[as_posix(c) for c in cmd[1:]])
    return {"spans": spans, "scan": made, "path": as_posix(final), "seconds": seconds}
