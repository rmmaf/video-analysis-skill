"""Speaker shots: a few screenshots spread over a video, for the agent that counts who speaks when the person
does not know (SKILL.md, step 4: the form's "Não sei"; the agent's prompt is reference/speaker-count.md).

``prepare.py --speaker-shots`` calls ``make_shots`` for each video: ffmpeg seeks each instant with the frame
engine's own repair command (``ffmpeg_io.seek_jpeg_cmd``: one accurate seek, one JPEG), at the size the analyst
reads (``SHOTS_DEFAULTS``: 12 shots, 1568 px on the longer side), into ``cache/<video_key>/shots-<h8>/``::

    shot_01_000022.jpg … shot_12_000839.jpg   (shot number and hhmmss of the instant)
    stage.json                                 (written last: the files, their instants and their size)

A complete folder is reused; an incomplete one is redone (seconds). The shots do not depend on the subtitles, the
webcam timeline or the frame parameters, so they can be taken before the transcription. ffmpeg_io (numpy) is
imported only when shots are made.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import SHOTS_DEFAULTS, SHOTS_VERSION, cache
from .common import as_posix, atomic_write_bytes

__all__ = ["STAGE", "ShotsError", "make_shots", "shot_size", "shot_times", "shots_dir", "shots_key"]

STAGE = "shots"


class ShotsError(RuntimeError):
    """ffmpeg could not take any screenshot of the video."""


def shot_times(duration_s: float, n: int) -> list[float]:
    """``n`` instants at the middle of ``n`` equal parts of the video: never the first or the last frame,
    which in a recording are often black or a waiting screen."""
    d = float(duration_s or 0.0)
    if not d > 0 or n < 1:
        return [0.0]
    return [round(d * (i + 0.5) / n, 3) for i in range(int(n))]


def shot_size(width: int, height: int, max_edge: int) -> tuple[int, int]:
    """The longer side at most ``max_edge``, both sides even (1920x1080 -> 1568x882, as the frames)."""
    w, h = max(2, int(width or 0)), max(2, int(height or 0))
    scale = min(1.0, float(max_edge) / max(w, h))
    return max(2, int(w * scale / 2.0 + 0.5) * 2), max(2, int(h * scale / 2.0 + 0.5) * 2)


def shots_key(video_key: str, params: dict | None = None) -> str:
    p = {**SHOTS_DEFAULTS, **(params or {})}
    return cache.h8({"stage": STAGE, "version": SHOTS_VERSION, "video_key": video_key, "n": p["n"],
                     "max_edge": p["max_edge"], "jpeg_q": p["jpeg_q"], "scale_flags": p["scale_flags"]})


def shots_dir(video_dir: str | Path, key: str) -> Path:
    return Path(video_dir) / f"{STAGE}-{key}"


def _hhmmss(t: float) -> str:
    s = int(t)
    return f"{s // 3600:02d}{s % 3600 // 60:02d}{s % 60:02d}"


def _done(sdir: Path, key: str) -> dict | None:
    doc = cache.read_stage(sdir)
    if not doc or doc.get("status") != "complete" or doc.get("stage") != STAGE or doc.get("key") != key:
        return None
    files = doc.get("files") or []
    return doc if files and all((sdir / f).is_file() for f in files) else None


def make_shots(*, video: Any, video_dir: str | Path, video_key: str, info: Any, params: dict | None = None) -> dict:
    """The screenshots of one video: ``{"dir", "paths", "times", "size", "cached"}`` (paths with ``/``).
    ``info``: the video's probe (VideoInfo or its dict). Raises ShotsError when no screenshot could be taken."""
    p = {**SHOTS_DEFAULTS, **(params or {})}
    key = shots_key(video_key, p)
    sdir = shots_dir(video_dir, key)
    doc = _done(sdir, key)
    cached = doc is not None
    if doc is None:
        from .ffmpeg_io import CREATE_NO_WINDOW, seek_jpeg_cmd
        duration = float(cache.info_get(info, "duration_s", "duration", default=0.0) or 0.0)
        size = shot_size(cache.info_get(info, "width", default=1920), cache.info_get(info, "height", default=1080),
                         int(p["max_edge"]))
        stream = int(cache.info_get(info, "stream_index", default=0) or 0)
        sdir = cache.fresh_dir(sdir)
        files, times, problems = [], [], []
        for i, t in enumerate(shot_times(duration, int(p["n"]))):
            cmd = seek_jpeg_cmd(video, stream, t, size[0], size[1], int(p["jpeg_q"]), flags=p["scale_flags"])
            try:
                out = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=float(p["timeout_s"]),
                                     creationflags=CREATE_NO_WINDOW)
            except (OSError, subprocess.SubprocessError) as exc:
                problems.append(f"{t:.1f} s: {type(exc).__name__}")
                continue
            if out.returncode != 0 or not out.stdout.startswith(b"\xff\xd8"):
                tail = out.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [f"exit {out.returncode}"]
                problems.append(f"{t:.1f} s: {tail[0][:160]}")
                continue
            name = f"shot_{i + 1:02d}_{_hhmmss(t)}.jpg"
            atomic_write_bytes(sdir / name, out.stdout)
            files.append(name)
            times.append(t)
        if not files:
            raise ShotsError("ffmpeg took no screenshot of the video" + (f" ({problems[0]})" if problems else ""))
        doc = cache.write_stage_json(sdir, STAGE, key, files=files, times=times, size=list(size), failed=problems,
                                     inputs={"video": as_posix(video)})
    return {"dir": as_posix(sdir), "paths": [as_posix(sdir / f) for f in doc["files"]], "times": doc["times"],
            "size": doc.get("size"), "cached": cached}
