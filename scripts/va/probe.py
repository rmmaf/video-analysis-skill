"""ffprobe -> VideoInfo (design-deterministic.md section 3.1, "Probe").

The displayed size accounts for the sample aspect ratio and the rotation (ffmpeg autorotates
when decoding, so the frames arrive in displayed orientation). ``stream_index`` is N in
``-map 0:v:N``: the position among the video streams, skipping cover art (attached_pic).
"""
from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from .common import as_posix, ms_to_s, s_to_ms
from .ffmpeg_io import CREATE_NO_WINDOW, FFmpegError, find_tool

__all__ = ["VideoInfo", "ProbeError", "probe_video", "parse_probe"]


class ProbeError(FFmpegError):
    """ffprobe failed or the file has no usable video stream."""


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    duration_s: float
    width: int                    # displayed (after SAR and rotation)
    height: int
    fps_avg: float
    vfr: bool
    codec: str
    has_b_frames: bool
    stream_index: int             # N in -map 0:v:N
    rotation: int                 # 0, 90, 180 or 270 (clockwise, as displayed)
    coded_width: int = 0
    coded_height: int = 0
    sar: str = "1:1"
    pix_fmt: str = ""
    profile: str = ""
    nb_frames: int | None = None
    start_time: float = 0.0
    bit_rate: int | None = None
    has_audio: bool = False
    format_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["path"] = as_posix(self.path)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoInfo":
        known = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in d.items() if k in known}
        kw["path"] = Path(kw["path"])
        return cls(**kw)

    def meta(self, key: str, title: str) -> dict[str, Any]:
        """The frames.json "video" object (spec section 6)."""
        return {"path": as_posix(self.path), "title": title, "key": key, "duration_s": ms_to_s(s_to_ms(self.duration_s)),
                "width": self.width, "height": self.height, "fps_avg": self.fps_avg, "vfr": self.vfr, "codec": self.codec}


def _frac(text: Any) -> Fraction | None:
    if not isinstance(text, str) or "/" not in text:
        return None
    a, b = text.split("/", 1)
    try:
        num, den = int(a), int(b)
    except ValueError:
        return None
    if num <= 0 or den <= 0:
        return None
    return Fraction(num, den)


def _float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _tag_duration(tags: dict) -> float | None:
    for k, v in (tags or {}).items():
        if k.upper() == "DURATION" and isinstance(v, str) and ":" in v:
            try:
                h, m, s = v.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
            except ValueError:
                return None
    return None


def _rotation(stream: dict) -> int:
    rot = None
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            rot = _float(sd.get("rotation"))
            break
    if rot is None:
        rot = _float((stream.get("tags") or {}).get("rotate"))
    if rot is None:
        return 0
    # ffprobe reports the display matrix rotation counter-clockwise (e.g. -90 for a phone
    # video shown rotated 90 degrees clockwise); normalize to 0/90/180/270.
    return int(round(-rot / 90.0)) % 4 * 90


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def parse_probe(data: dict, path: str | os.PathLike) -> VideoInfo:
    """Build VideoInfo from ``ffprobe -show_format -show_streams -of json`` output."""
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    videos = [s for s in streams if s.get("codec_type") == "video"]
    pick = None
    for n, s in enumerate(videos):
        if (s.get("disposition") or {}).get("attached_pic"):
            continue
        if not s.get("width") or not s.get("height"):
            continue
        pick = (n, s)
        break
    if pick is None:
        raise ProbeError(f"no video stream in {os.fspath(path)}")
    n, s = pick
    cw, chh = int(s["width"]), int(s["height"])
    sar = _frac(s.get("sample_aspect_ratio"))
    dw = cw * float(sar) if sar and sar != 1 else float(cw)
    width, height = (_even(dw) if sar and sar != 1 else cw), chh
    rotation = _rotation(s)
    if rotation in (90, 270):
        width, height = height, width
    avg = _frac(s.get("avg_frame_rate"))
    rfr = _frac(s.get("r_frame_rate"))
    duration = _float(s.get("duration")) or _tag_duration(s.get("tags")) or _float(fmt.get("duration"))
    if not duration or duration <= 0:
        raise ProbeError(f"unknown duration for {os.fspath(path)}")
    nb = s.get("nb_frames")
    nb_frames = int(nb) if isinstance(nb, str) and nb.isdigit() else None
    fps = avg or rfr
    if fps is None and nb_frames:
        fps = Fraction(nb_frames) / Fraction(duration).limit_denominator(1000000)
    fps_avg = round(float(fps), 3) if fps else 0.0
    vfr = bool(avg and rfr and avg != rfr)
    br = s.get("bit_rate") or fmt.get("bit_rate")
    return VideoInfo(
        path=Path(os.path.abspath(os.fspath(path))), duration_s=round(float(duration), 6), width=int(width),
        height=int(height), fps_avg=fps_avg, vfr=vfr, codec=str(s.get("codec_name") or ""),
        has_b_frames=int(s.get("has_b_frames") or 0) > 0, stream_index=n, rotation=rotation,
        coded_width=cw, coded_height=chh, sar=str(s.get("sample_aspect_ratio") or "1:1"),
        pix_fmt=str(s.get("pix_fmt") or ""), profile=str(s.get("profile") or ""), nb_frames=nb_frames,
        start_time=_float(s.get("start_time")) or 0.0, bit_rate=int(br) if isinstance(br, str) and br.isdigit() else None,
        has_audio=any(x.get("codec_type") == "audio" for x in streams), format_name=str(fmt.get("format_name") or ""))


def probe_video(path: str | os.PathLike) -> VideoInfo:
    """Run ffprobe on ``path`` (argument list, no shell) and return its VideoInfo.

    Raises FFmpegNotFound when ffprobe is missing and ProbeError when the file cannot be read
    or has no video stream."""
    cmd = [find_tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           os.fspath(path)]
    try:
        out = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
                             creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"ffprobe timed out on {os.fspath(path)}") from exc
    if out.returncode != 0:
        raise ProbeError(f"ffprobe failed on {os.fspath(path)}", out.stderr.decode("utf-8", "replace")[-1500:])
    try:
        data = json.loads(out.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned invalid JSON for {os.fspath(path)}") from exc
    return parse_probe(data, path)
