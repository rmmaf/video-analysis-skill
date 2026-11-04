"""Frames stage: decode, dedup, write the kept JPEGs + frames.json + samples.csv.

``run_frames_stage(src, info, params, out_dir, progress)`` (design-deterministic.md 3.1, 6).

Decoding. The sampling grid is ``fps=fps=S:start_time=0:round=near`` (VFR-safe, t_k = range
start + k/S), with ``-skip_frame:v noref`` as an input option.
- lockstep (default): ONE ffmpeg decode split in two branches. Branch A (gray, area-scaled to
  the analysis size) goes to stdout as rawvideo and feeds the Analyzer; branch B (lanczos to
  the output size, MJPEG -q:v 3) is written by the same process as numbered spool JPEGs
  (image2, atomic writes) a few samples ahead of A. Measured on this machine, two concurrent
  decoders cost ~1.9x one decode on 1080p60 (both are CPU-bound), so one shared decode keeps
  the run within the section 9 limits. Python moves the selected spool files into place
  (no re-encode) and deletes the others, so the spool holds ~lookahead samples.
- sequential (``params["sequential"]``, also the automatic fallback): pass A alone, then pass B
  alone through image2pipe; the JPEGs selected by pass A are written. Same bytes as lockstep.
- a selected sample still missing after that is repaired with ``-ss t -frames:v 1``.

Ownership (section 7): this module writes content files only (JPEGs, frames.json,
samples.csv, ffmpeg_a.log, ffmpeg_b.log); cache.py writes stage.json.
"""
from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import ALGO_VERSION, SCHEMA_IDS
from .common import as_posix, fmt_hms, frame_filename, frame_id, ms_to_s, s_to_ms, video_title, write_json, write_text
from .dedup import Analyzer, Decision, density_report, effective_frame_params, frame_params_for_output, suggest_ignore
from .ffmpeg_io import (CREATE_NO_WINDOW, FFmpegError, JpegStreamReader, RawFrameReader, gray_cmd, mjpeg_pipe_cmd, read_log_tail,
                        seek_jpeg_cmd, split_cmd, start, stop)
from .probe import VideoInfo, probe_video

__all__ = [
    "run_frames_stage", "effective_frame_params", "probe_video", "VideoInfo", "analysis_size", "output_size",
    "image_tokens", "estimate_seconds", "frame_range_ms", "decode_gray", "analyze_video", "gray_stream_cache",
    "SAMPLES_HEADER",
]

SAMPLES_HEADER = ("k,t,e,cut,local,small,hot_frac,act_frac,p_cls,p_px,p_blobs,r_cls,r_px,r_blobs,r_frac,r_bbox,"
                  "state,stable,event,frame,p_weak,r_weak")

ProgressFn = Callable[[dict], None]


# --- geometry ----------------------------------------------------------------------------

def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def analysis_size(info: VideoInfo, params: dict | None = None) -> tuple[int, int]:
    """AH = min(360, floor(H/8)*8); AW = round(AH*W/H/8)*8 (1080p -> 640x360; 3024x1964 -> 552x360)."""
    p = effective_frame_params(params)
    align = int(p["analysis_align"])
    ah = min(int(p["analysis_height"]), (int(info.height) // align) * align)
    ah = max(align, ah)
    aw = max(align, _round_half_up(ah * info.width / info.height / align) * align)
    return aw, ah


def output_size(info: VideoInfo, params: dict | None = None) -> tuple[int, int]:
    """scale = min(1, max_edge / max(W, H)), both sides rounded to even (1080p -> 1568x882)."""
    p = effective_frame_params(params)
    scale = min(1.0, float(p["max_edge"]) / max(info.width, info.height))
    return (max(2, _round_half_up(info.width * scale / 2.0) * 2), max(2, _round_half_up(info.height * scale / 2.0) * 2))


def image_tokens(size: tuple[int, int] | list[int]) -> int:
    return math.ceil(size[0] / 28) * math.ceil(size[1] / 28)


def frame_range_ms(info: VideoInfo, params: dict | None = None) -> tuple[int, int]:
    """[start, end] of the analysed range in integer ms: end = min(duration, range end)."""
    p = effective_frame_params(params)
    dur = s_to_ms(info.duration_s)
    rng = p.get("range")
    if not rng:
        return 0, dur
    a = s_to_ms(rng[0] or 0.0)
    b = dur if rng[1] is None else min(dur, s_to_ms(rng[1]))
    if b <= a:
        raise ValueError(f"empty range {rng} for a video of {info.duration_s:.3f} s")
    return a, b


def estimate_seconds(info: VideoInfo, params: dict | None = None) -> float:
    """Rough wall time of the frames stage (cold), from the decode cost of this machine:
    ~0.75 s per 10^9 decoded pixels (H.264, 8 threads) + ~6 ms per sample (Python + JPEG)."""
    p = effective_frame_params(params)
    r0, r1 = frame_range_ms(info, p)
    dur = (r1 - r0) / 1000.0
    pixels = float(info.width) * info.height * max(info.fps_avg, 1.0) * dur
    noref = 0.7 if info.has_b_frames and p.get("skip_noref", True) else 1.0
    return round(2.0 + pixels / 1e9 * 0.75 * noref + dur * float(p["sample_fps"]) * 0.006, 1)


# --- helpers -----------------------------------------------------------------------------

def _clean_out_dir(out_dir: Path) -> None:
    """Remove this stage's own content files left by an interrupted run (never stage.json)."""
    for f in out_dir.iterdir():
        name = f.name
        if f.is_file() and ((name.startswith("F") and name.endswith(".jpg")) or name in
                            ("frames.json", "samples.csv", "ffmpeg_a.log", "ffmpeg_b.log")):
            _unlink(f)
    sp = out_dir / "_spool"
    if sp.exists():
        _rmtree(sp)


def _unlink(p: Path, tries: int = 6) -> bool:
    delay = 0.05
    for _ in range(tries):
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            time.sleep(delay)
            delay *= 2
    return False


def _rmtree(p: Path) -> None:
    def onexc(func, path, exc):
        try:
            os.chmod(path, 0o666)
            func(path)
        except OSError:
            pass
    for _ in range(5):
        try:
            shutil.rmtree(p, onexc=onexc)
        except OSError:
            pass
        if not p.exists():
            return
        time.sleep(0.2)


def _reencode(data: bytes, quality: int) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        out = io.BytesIO()
        im.convert("RGB").save(out, "JPEG", quality=int(quality))
    return out.getvalue()


class _Writer:
    """Writes the selected JPEGs (final names) and remembers their sizes."""

    def __init__(self, out_dir: Path, p: dict, r0_ms: int, analyzer: Analyzer):
        self.out_dir = out_dir
        self.p = p
        self.r0 = r0_ms
        self.an = analyzer
        self.files: dict[int, str] = {}       # frame number -> file name
        self.bytes: dict[int, int] = {}
        self.reenc: set[int] = set()

    def name(self, d: Decision) -> str:
        return frame_filename(d.n, ms_to_s(self.r0 + self.an.ms(d.sample)))

    def put_bytes(self, d: Decision, data: bytes) -> None:
        limit = int(self.p["jpeg_reencode_above"])
        if len(data) > limit:
            data = _reencode(data, int(self.p["jpeg_reencode_quality"]))
            self.reenc.add(d.n)
        name = self.name(d)
        (self.out_dir / name).write_bytes(data)
        self.files[d.n] = name
        self.bytes[d.n] = len(data)

    def put_file(self, d: Decision, path: Path) -> None:
        size = path.stat().st_size
        if size > int(self.p["jpeg_reencode_above"]):
            self.put_bytes(d, path.read_bytes())
            _unlink(path)
            return
        name = self.name(d)
        dst = self.out_dir / name
        delay = 0.05
        for i in range(7):
            try:
                os.replace(path, dst)
                break
            except PermissionError:
                if i == 6:
                    raise
                time.sleep(delay)
                delay *= 2
        self.files[d.n] = name
        self.bytes[d.n] = size


# --- passes ------------------------------------------------------------------------------

class _Run:
    def __init__(self, src: Path, info: VideoInfo, p: dict, out_dir: Path, progress: ProgressFn | None):
        self.src, self.info, self.p, self.out_dir, self.progress = src, info, p, out_dir, progress
        self.aw, self.ah = analysis_size(info, p)
        self.ow, self.oh = output_size(info, p)
        self.r0, self.r1 = frame_range_ms(info, p)
        self.S = float(p["sample_fps"])
        rng = p.get("range")
        self.start_s = self.r0 / 1000.0 if self.r0 > 0 else None
        self.dur_s = (self.r1 - self.r0) / 1000.0 if rng else None
        self.max_samples = max(1, math.ceil((self.r1 - self.r0) * self.S / 1000.0 - 1e-9))
        self.log_a = out_dir / "ffmpeg_a.log"
        self.log_b = out_dir / "ffmpeg_b.log"
        self.an = Analyzer(p, (self.aw, self.ah))
        self.w = _Writer(out_dir, p, self.r0, self.an)
        self.a_count = 0                       # frames read from A (all, even past the range)
        self.used = 0                          # samples pushed into the analyzer
        self._t_prog = 0.0
        self.notes: list[str] = []

    def _report(self, force: bool = False) -> None:
        if self.progress is None:
            return
        now = time.monotonic()
        if not force and now - self._t_prog < 1.0:
            return
        self._t_prog = now
        try:
            self.progress({"stage": "frames", "done_s": round(min(self.used / self.S, (self.r1 - self.r0) / 1000.0), 1),
                           "total_s": round((self.r1 - self.r0) / 1000.0, 1), "frames": len(self.an.decisions)})
        except Exception:
            pass

    def _push(self, g: np.ndarray) -> list[Decision]:
        self.a_count += 1
        if self.used >= self.max_samples:
            return []
        self.used += 1
        return self.an.push(g)

    def _check_a(self, proc: subprocess.Popen, rc: int | None) -> None:
        expected = (self.r1 - self.r0) * self.S / 1000.0
        if self.used == 0:
            raise FFmpegError(f"no frames decoded from {self.src}", read_log_tail(self.log_a))
        if rc not in (0, None) and self.used < 0.9 * expected - 2:
            raise FFmpegError(f"ffmpeg stopped after {self.used} of ~{expected:.0f} samples (exit {rc})",
                              read_log_tail(self.log_a))
        if rc not in (0, None):
            self.notes.append(f"ffmpeg A exited with code {rc}; {self.used} samples used")

    # -- lockstep: one decode, A on stdout, B as spool files
    def lockstep(self) -> bool:
        spool = self.out_dir / "_spool"
        spool.mkdir(parents=True, exist_ok=True)
        pattern = as_posix(spool).replace("%", "%%") + "/%07d.jpg"
        p = self.p
        cmd = split_cmd(self.src, self.info.stream_index, self.S, self.aw, self.ah, self.ow, self.oh, int(p["jpeg_q"]),
                        pattern, gray_flags=p["analysis_scale_flags"], jpeg_flags=p["output_scale_flags"],
                        skip_noref=bool(p["skip_noref"]), start_s=self.start_s, dur_s=self.dur_s)
        cmd.insert(cmd.index("-start_number"), "-atomic_writing")
        cmd.insert(cmd.index("-start_number"), "1")
        proc = start(cmd, self.log_a)
        waiting: list[Decision] = []
        swept = 0
        b_ok = True
        try:
            reader = RawFrameReader(proc.stdout, self.aw, self.ah)
            while True:
                g = reader.read()
                if g is None:
                    break
                waiting.extend(self._push(g))
                if waiting:
                    waiting = self._take_spool(spool, waiting, proc, final=False)
                lo = min(self.an.min_capture_sample(), self.used - 1) - 1
                while swept < lo:
                    if not any(d.sample == swept for d in waiting):
                        _unlink(spool / f"{swept:07d}.jpg", tries=2)
                    swept += 1
                self._report()
            waiting.extend(self.an.finish())
            try:
                rc = proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                rc = None
            self._check_a(proc, rc)
            # B must have produced exactly one JPEG per A frame
            last = spool / f"{self.a_count - 1:07d}.jpg"
            extra = spool / f"{self.a_count:07d}.jpg"
            if rc != 0 or not last.exists() or extra.exists():
                b_ok = False
                self.notes.append("spool JPEG out of sync; pass B repeated sequentially")
            if b_ok:
                waiting = self._take_spool(spool, waiting, proc, final=True)
                if waiting:
                    b_ok = False
        finally:
            stop(proc)
        if not b_ok:
            self._redo_b()
        _rmtree(spool)
        return b_ok

    def _take_spool(self, spool: Path, waiting: list[Decision], proc: subprocess.Popen, final: bool) -> list[Decision]:
        left: list[Decision] = []
        for d in waiting:
            if d.same_as is not None:
                continue
            f = spool / f"{d.sample:07d}.jpg"
            if not f.exists() and not final:
                # B runs a few samples ahead of A; with the lookahead this is rare
                t0 = time.monotonic()
                while not f.exists() and proc.poll() is None and time.monotonic() - t0 < 30:
                    time.sleep(0.01)
            if f.exists():
                self.w.put_file(d, f)
            else:
                left.append(d)
        return left

    # -- sequential: A alone, then B alone through image2pipe
    def sequential(self) -> None:
        p = self.p
        cmd = gray_cmd(self.src, self.info.stream_index, self.S, self.aw, self.ah, flags=p["analysis_scale_flags"],
                       skip_noref=bool(p["skip_noref"]), start_s=self.start_s, dur_s=self.dur_s)
        proc = start(cmd, self.log_a)
        try:
            reader = RawFrameReader(proc.stdout, self.aw, self.ah)
            while True:
                g = reader.read()
                if g is None:
                    break
                self._push(g)
                self._report()
            self.an.finish()
            try:
                rc = proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                rc = None
            self._check_a(proc, rc)
        finally:
            stop(proc)
        self._redo_b()

    def _redo_b(self) -> None:
        need = {d.sample: d for d in self.an.decisions if d.same_as is None and d.n not in self.w.files}
        if not need:
            return
        p = self.p
        cmd = mjpeg_pipe_cmd(self.src, self.info.stream_index, self.S, self.ow, self.oh, int(p["jpeg_q"]),
                             flags=p["output_scale_flags"], skip_noref=bool(p["skip_noref"]),
                             start_s=self.start_s, dur_s=self.dur_s)
        proc = start(cmd, self.log_b)
        count = 0
        try:
            reader = JpegStreamReader(proc.stdout)
            while need:
                j = reader.read()
                if j is None:
                    break
                d = need.pop(count, None)
                if d is not None:
                    self.w.put_bytes(d, j)
                count += 1
        except FFmpegError as exc:
            self.notes.append(f"pass B failed: {str(exc).splitlines()[0]}")
        finally:
            stop(proc)
        for k, d in sorted(need.items()):
            self._repair(d)

    def _repair(self, d: Decision) -> None:
        t = (self.r0 + self.an.ms(d.sample)) / 1000.0
        cmd = seek_jpeg_cmd(self.src, self.info.stream_index, t, self.ow, self.oh, int(self.p["jpeg_q"]),
                            flags=self.p["output_scale_flags"])
        try:
            out = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120,
                                 creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError(f"could not extract the JPEG of sample {d.sample} (t={t:.3f} s)") from exc
        data = out.stdout
        if out.returncode != 0 or not data.startswith(b"\xff\xd8"):
            raise FFmpegError(f"could not extract the JPEG of sample {d.sample} (t={t:.3f} s)",
                              out.stderr.decode("utf-8", "replace")[-1500:])
        self.notes.append(f"JPEG of {frame_id(d.n)} redone by seeking to {t:.3f} s")
        self.w.put_bytes(d, data)


# --- frames.json -------------------------------------------------------------------------

def _warnings(run: _Run, frames: list[dict], density: dict, suggestions: list[dict], hot_segments: list[dict]) -> list[str]:
    p = run.p
    out: list[str] = []
    dur_s = (run.r1 - run.r0) / 1000.0
    fpm, mx = density["frames_per_min"], density["max_per_min"]
    busy = []
    if fpm > float(p["warn_frames_per_min"]):
        busy.append(f"{fpm:.1f} frames/min in the video (limit {float(p['warn_frames_per_min']):g})")
    if mx > int(p["warn_window_frames"]) and dur_s >= 30:
        busy.append(f"{mx} frames in 60 s starting at {fmt_hms(density['max_window'][0], 0)} (limit {int(p['warn_window_frames'])})")
    if busy:
        msg = "high density: " + " and ".join(busy)
        if suggestions:
            s = suggestions[0]
            reg = ",".join(f"{v:.2f}" for v in s["region"])
            msg += f"; suggested region to ignore: {reg} (explains {round(100 * s['explains'])}% of the frames)"
        else:
            msg += "; no recurring region to ignore (if there are redundant frames: sensitivity low)"
        out.append(msg)
    for a, b in run.an.camera_spans:
        if (b - a) / run.S >= 10.0:
            t0 = (run.r0 + run.an.ms(a)) / 1000.0
            t1 = min(run.r1, run.r0 + run.an.ms(b)) / 1000.0
            out.append(f"camera-like segment at {fmt_hms(t0, 0)}–{fmt_hms(t1, 0)}: everything moves; "
                       f"extraction drops to ~2 frames/min (heartbeat)")
    for seg in hot_segments:
        if 0.35 <= seg["frac"] < float(p["camera_hot_frac"]) and seg["end"] - seg["start"] >= 60.0:
            out.append(f"hot mask covered {round(100 * seg['frac'])}% of the frame at "
                       f"{fmt_hms(seg['start'], 0)}–{fmt_hms(seg['end'], 0)}; if a panel of the screen dropped out of the "
                       f"comparison, use --hot-mode off")
    if len(frames) > int(p["warn_frames_total"]):
        tok = sum(f["tokens"] for f in frames if f["same_as"] is None)
        out.append(f"{len(frames)} frames: high image cost (~{round(tok / 1000)}k tokens for images alone)")
    out.extend(run.notes)
    return out


def _samples_csv(run: _Run) -> str:
    lines = [SAMPLES_HEADER]
    tags = run.an.frame_tags
    for row in run.an.rows:
        (k, t_ms, e, cut, local, small, hot, act, pc, ppx, pbl, rc, rpx, rbl, rfr, rbb, state, stable, event, _f,
         pweak, rweak) = row
        bb = "" if not rbb else ";".join(f"{v:.4f}" for v in rbb)
        lines.append(f"{k},{(run.r0 + t_ms) / 1000.0:.3f},{e:.4f},{cut},{local},{small},{hot:.4f},{act:.4f},{pc},{ppx},"
                     f"{pbl},{rc},{rpx},{rbl},{rfr:.4f},{bb},{state},{stable},{event},{tags.get(k, '')},{pweak},{rweak}")
    return "\n".join(lines) + "\n"


def run_frames_stage(src: str | os.PathLike, info: VideoInfo, params: dict, out_dir: str | os.PathLike,
                     progress: ProgressFn | None = None, *, video_key: str | None = None) -> dict:
    """Extract the unique frames of ``src`` into ``out_dir`` and return the frames.json dict.

    ``params`` may be raw overrides or the output of ``effective_frame_params`` (idempotent).
    ``progress`` receives ``{"stage": "frames", "done_s", "total_s", "frames"}`` about once per
    second. ``video_key`` defaults to the name of ``out_dir``'s parent (cache/<video_key>/frames-<h8>).
    Raises FFmpegNotFound, FFmpegError (decode failure) or ValueError (bad params/range).
    """
    p = effective_frame_params(params)
    src = Path(os.path.abspath(os.fspath(src)))
    out_dir = Path(os.path.abspath(os.fspath(out_dir)))
    out_dir.mkdir(parents=True, exist_ok=True)
    _clean_out_dir(out_dir)
    run = _Run(src, info, p, out_dir, progress)
    write_text(run.log_b, "")
    if p.get("sequential"):
        run.sequential()
    else:
        try:
            run.lockstep()
        except FFmpegError as exc:
            # the shared decode failed (e.g. the JPEG branch could not write): redo everything
            # with separate passes; a real decode problem fails again and is raised
            first = str(exc).splitlines()[0]
            _clean_out_dir(out_dir)
            run = _Run(src, info, p, out_dir, progress)
            write_text(run.log_b, "")
            run.notes.append(f"shared decode failed ({first}); redone in separate passes")
            run.sequential()
    run._report(force=True)
    an = run.an
    decisions = an.decisions
    missing = [d.n for d in decisions if d.same_as is None and d.n not in run.w.files]
    if missing:
        raise FFmpegError(f"{len(missing)} selected frames have no JPEG (first: {frame_id(missing[0])})")
    total = len(decisions)
    if total > 9999:                          # ids/file names get 5 digits: rename the 4-digit files
        for d in decisions:
            if d.n in run.w.files:
                new = frame_filename(d.n, ms_to_s(run.r0 + an.ms(d.sample)), total)
                os.replace(out_dir / run.w.files[d.n], out_dir / new)
                run.w.files[d.n] = new
    tokens = image_tokens((run.ow, run.oh))
    frames: list[dict] = []
    starts = [run.r0 if d.n == 1 else run.r0 + an.ms(d.start_sample) for d in decisions]
    for i, d in enumerate(decisions):
        orig = d.same_as if d.same_as is not None else d.n
        cap = run.r0 + an.ms(d.sample)
        end = starts[i + 1] if i + 1 < total else run.r1
        f = {"id": frame_id(d.n, total), "file": run.w.files[orig], "start": ms_to_s(starts[i]), "end": ms_to_s(end),
             "captured_at": ms_to_s(cap), "sample": d.sample, "kind": d.kind,
             "same_as": None if d.same_as is None else frame_id(d.same_as, total),
             "change": {"frac": d.frac, "bbox": [round(v, 4) for v in d.bbox], "blobs": d.blobs},
             "hot_frac": d.hot_frac, "bytes": run.w.bytes[orig], "tokens": tokens}
        if orig in run.w.reenc:
            f["reencoded"] = True
        frames.append(f)
    density = density_report([s_to_ms(f["captured_at"]) for f in frames], (run.r0, run.r1),
                             int(round(float(p["warn_window_s"]) * 1000)))
    cell = (1.0 / an.cmp.gw, 1.0 / an.cmp.gh)
    suggestions = suggest_ignore([f["change"]["bbox"] for f in frames[1:]], total, p, cell)
    hot_segments = an.hot_segments_s(run.r0, run.r1)
    for seg in hot_segments:
        seg["start"], seg["end"] = round(seg["start"], 3), round(seg["end"], 3)
    title = video_title(src)
    key = video_key or out_dir.parent.name
    doc = {
        "schema": SCHEMA_IDS["frames"],
        "algo_version": ALGO_VERSION,
        "video": info.meta(key, title) | {"path": as_posix(src)},
        "range": [ms_to_s(run.r0), ms_to_s(run.r1)],
        "sample_fps": run.S,
        "n_samples": run.used,
        "analysis_size": [run.aw, run.ah],
        "output_size": [run.ow, run.oh],
        "frames_dir": as_posix(out_dir),
        "params": frame_params_for_output(p),
        "frames": frames,
        "n_unique": sum(1 for f in frames if f["same_as"] is None),
        "hot_segments": hot_segments,
        "density": density,
        "suggested_ignore": suggestions,
        "warnings": [],
    }
    doc["warnings"] = _warnings(run, frames, density, suggestions, hot_segments)
    write_text(out_dir / "samples.csv", _samples_csv(run))
    write_json(out_dir / "frames.json", doc)
    return doc


# --- calibration helpers -------------------------------------------------------------------

def decode_gray(src: str | os.PathLike, info: VideoInfo, params: dict | None = None, log_path: str | os.PathLike | None = None):
    """Yield the gray analysis samples (copies) of pass A, for calibrate.py / explain.py."""
    p = effective_frame_params(params)
    aw, ah = analysis_size(info, p)
    r0, r1 = frame_range_ms(info, p)
    rng = p.get("range")
    S = float(p["sample_fps"])
    max_samples = max(1, math.ceil((r1 - r0) * S / 1000.0 - 1e-9))
    cmd = gray_cmd(src, info.stream_index, S, aw, ah, flags=p["analysis_scale_flags"], skip_noref=bool(p["skip_noref"]),
                   start_s=r0 / 1000.0 if r0 > 0 else None, dur_s=(r1 - r0) / 1000.0 if rng else None)
    proc = start(cmd, log_path)
    try:
        reader = RawFrameReader(proc.stdout, aw, ah)
        n = 0
        while n < max_samples:
            g = reader.read()
            if g is None:
                break
            n += 1
            yield g.copy()
    finally:
        stop(proc)


def analyze_video(src: str | os.PathLike, info: VideoInfo, params: dict | None = None, *,
                  static_mask: np.ndarray | None = None) -> Analyzer:
    """Pass A only (no JPEGs): run the Analyzer over the whole range and return it."""
    p = effective_frame_params(params)
    an = Analyzer(p, analysis_size(info, p), static_mask=static_mask)
    for g in decode_gray(src, info, p):
        an.push(g)
    an.finish()
    return an


def gray_stream_cache(src: str | os.PathLike, info: VideoInfo, params: dict | None, cache_dir: str | os.PathLike,
                      key: str) -> np.ndarray:
    """Pass A decoded once into ``cache_dir/<key>-<fps>-<range>-<aw>x<ah>.npy`` (a memmap) and
    reused by calibrate.py / explain.py to replay the Analyzer without decoding again."""
    import json as _json
    p = effective_frame_params(params)
    aw, ah = analysis_size(info, p)
    r0, r1 = frame_range_ms(info, p)
    tag = f"{key}-{fmt_fps_tag(p['sample_fps'])}-{r0}-{r1}-{aw}x{ah}-{p['analysis_scale_flags']}-{int(bool(p['skip_noref']))}"
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    npy, meta = cache_dir / f"{tag}.npy", cache_dir / f"{tag}.json"
    if npy.exists() and meta.exists():
        n = int(_json.loads(meta.read_text(encoding="utf-8"))["n"])
        mm = np.load(npy, mmap_mode="r")
        return mm[:n]
    S = float(p["sample_fps"])
    cap = max(1, math.ceil((r1 - r0) * S / 1000.0 - 1e-9)) + 2
    tmp = cache_dir / f"{tag}.tmp.npy"
    mm = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.uint8, shape=(cap, ah, aw))
    n = 0
    for g in decode_gray(src, info, p):
        if n >= cap:
            break
        mm[n] = g
        n += 1
    mm.flush()
    del mm
    os.replace(tmp, npy)
    write_text(meta, _json.dumps({"n": n, "src": as_posix(src), "shape": [n, ah, aw]}) + chr(10))
    return np.load(npy, mmap_mode="r")[:n]


def fmt_fps_tag(fps: float) -> str:
    f = float(fps)
    return str(int(f)) if f == int(f) else str(f).replace(".", "p")
