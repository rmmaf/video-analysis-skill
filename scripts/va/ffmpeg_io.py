"""ffmpeg/ffprobe plumbing: tool lookup, command builders, process handling, stream readers.

Rules (design-deterministico.md section 3.1 and 8): argument lists only (never a shell or a
command string), ``-nostdin`` + stdin=DEVNULL, stderr to a log file (never an undrained pipe),
CREATE_NO_WINDOW, ``-skip_frame:v noref`` as an input option, ``-map 0:v:N -an -sn -dn``,
children killed in ``finally``. Source paths never go inside filter strings.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import IO, Iterator

import numpy as np

__all__ = [
    "FFmpegError", "FFmpegNotFound", "CREATE_NO_WINDOW", "find_tool", "ffmpeg_version", "fmt_fps",
    "input_args", "gray_cmd", "mjpeg_pipe_cmd", "split_cmd", "seek_jpeg_cmd", "start", "stop",
    "RawFrameReader", "JpegStreamReader", "iter_jpegs",
]

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0


class FFmpegNotFound(RuntimeError):
    """ffmpeg or ffprobe is not on the PATH (prepare.py exit code 3, environment)."""


class FFmpegError(RuntimeError):
    """ffmpeg/ffprobe failed; ``log`` holds the tail of its stderr when available."""

    def __init__(self, message: str, log: str = ""):
        super().__init__(message + (f"\n{log.strip()}" if log and log.strip() else ""))
        self.log = log


_TOOLS: dict[str, str] = {}


def find_tool(name: str) -> str:
    """Absolute path of ffmpeg/ffprobe (env VA_FFMPEG / VA_FFPROBE override the PATH)."""
    if name in _TOOLS:
        return _TOOLS[name]
    env = os.environ.get("VA_" + name.upper(), "").strip().strip('"')
    path = env if env and Path(env).is_file() else shutil.which(name)
    if not path:
        raise FFmpegNotFound(f"{name} not found on PATH (install ffmpeg: see setup.md)")
    _TOOLS[name] = path
    return path


def ffmpeg_version() -> str:
    """First line of ``ffmpeg -version`` (recorded in stage.json, not part of cache keys)."""
    try:
        out = subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-version"], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        return out.stdout.decode("utf-8", "replace").splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def fmt_fps(fps: float) -> str:
    """2.0 -> "2"; 2.5 -> "2.5" (for the fps filter)."""
    f = float(fps)
    return str(int(f)) if f == int(f) else repr(f)


def input_args(src: str | os.PathLike, *, skip_noref: bool = True, start_s: float | None = None,
               dur_s: float | None = None) -> list[str]:
    a = [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error"]
    if skip_noref:
        a += ["-skip_frame:v", "noref"]
    if start_s:
        a += ["-ss", f"{start_s:.3f}"]
    if dur_s is not None:
        a += ["-t", f"{dur_s:.3f}"]
    return a + ["-i", os.fspath(src)]


def _fps_filter(fps: float) -> str:
    return f"fps=fps={fmt_fps(fps)}:start_time=0:round=near"


def _gray_chain(aw: int, ah: int, flags: str) -> str:
    return f"scale={aw}:{ah}:flags={flags},format=gray"


def _jpeg_chain(ow: int, oh: int, flags: str) -> str:
    return f"scale={ow}:{oh}:flags={flags}"


def gray_cmd(src, stream: int, fps: float, aw: int, ah: int, *, flags: str = "area", skip_noref: bool = True,
             start_s: float | None = None, dur_s: float | None = None) -> list[str]:
    """Decoder A alone: gray rawvideo at the analysis size on stdout."""
    return input_args(src, skip_noref=skip_noref, start_s=start_s, dur_s=dur_s) + [
        "-map", f"0:v:{stream}", "-an", "-sn", "-dn",
        "-vf", f"{_fps_filter(fps)},{_gray_chain(aw, ah, flags)}",
        "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"]


def mjpeg_pipe_cmd(src, stream: int, fps: float, ow: int, oh: int, q: int, *, flags: str = "lanczos",
                   skip_noref: bool = True, start_s: float | None = None, dur_s: float | None = None) -> list[str]:
    """Decoder B alone: MJPEG at the output size on stdout (image2pipe)."""
    return input_args(src, skip_noref=skip_noref, start_s=start_s, dur_s=dur_s) + [
        "-map", f"0:v:{stream}", "-an", "-sn", "-dn",
        "-vf", f"{_fps_filter(fps)},{_jpeg_chain(ow, oh, flags)}",
        "-fps_mode", "passthrough", "-c:v", "mjpeg", "-q:v", str(int(q)), "-f", "image2pipe", "-"]


def split_cmd(src, stream: int, fps: float, aw: int, ah: int, ow: int, oh: int, q: int, spool_pattern: str, *,
              gray_flags: str = "area", jpeg_flags: str = "lanczos", skip_noref: bool = True,
              start_s: float | None = None, dur_s: float | None = None) -> list[str]:
    """A and B in lockstep from ONE decode: the sampled frames are split; A goes to stdout as
    gray rawvideo, B is written as numbered JPEG files (``spool_pattern``, e.g. ".../%07d.jpg").
    The stdout backpressure throttles both branches, so the spool stays a few frames ahead."""
    graph = (f"[0:v:{stream}]{_fps_filter(fps)},split=2[a][b];"
             f"[a]{_gray_chain(aw, ah, gray_flags)}[ga];[b]{_jpeg_chain(ow, oh, jpeg_flags)}[gb]")
    return input_args(src, skip_noref=skip_noref, start_s=start_s, dur_s=dur_s) + [
        "-filter_complex", graph,
        "-map", "[ga]", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-",
        "-map", "[gb]", "-fps_mode", "passthrough", "-c:v", "mjpeg", "-q:v", str(int(q)),
        "-f", "image2", "-start_number", "0", spool_pattern]


def seek_jpeg_cmd(src, stream: int, t_s: float, ow: int, oh: int, q: int, *, flags: str = "lanczos") -> list[str]:
    """Last-resort repair of one missing JPEG: accurate seek to t and encode one frame."""
    return [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t_s:.3f}",
            "-i", os.fspath(src), "-map", f"0:v:{stream}", "-an", "-sn", "-dn", "-frames:v", "1",
            "-vf", _jpeg_chain(ow, oh, flags), "-c:v", "mjpeg", "-q:v", str(int(q)), "-f", "image2pipe", "-"]


def start(cmd: list[str], log_path: str | os.PathLike | None, *, stdout=subprocess.PIPE) -> subprocess.Popen:
    """Start ffmpeg: stdin DEVNULL, stdout piped (unbuffered), stderr appended to ``log_path``."""
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=stdout, stderr=log, bufsize=0,
                                creationflags=CREATE_NO_WINDOW)
    finally:
        if log_path:
            log.close()
    return proc


def stop(proc: subprocess.Popen | None, timeout: float = 5.0) -> int | None:
    """Kill (if still running) and reap a child; closes its stdout. Returns the exit code."""
    if proc is None:
        return None
    try:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
    finally:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
    return proc.returncode


def read_log_tail(log_path: str | os.PathLike | None, n: int = 2000) -> str:
    if not log_path:
        return ""
    try:
        data = Path(log_path).read_bytes()
    except OSError:
        return ""
    return data[-n:].decode("utf-8", "replace")


class RawFrameReader:
    """Fixed-size rawvideo frames from a pipe, read with ``readinto`` into a preallocated buffer."""

    def __init__(self, stream: IO[bytes], width: int, height: int):
        self.stream = stream
        self.w, self.h = int(width), int(height)
        self.size = self.w * self.h
        self.buf = bytearray(self.size)
        self.view = memoryview(self.buf)
        self.count = 0

    def read(self) -> np.ndarray | None:
        """Next frame as a (h, w) uint8 view of the internal buffer (copy it to keep it), or
        None at EOF. A truncated last frame is dropped."""
        got = 0
        while got < self.size:
            n = self.stream.readinto(self.view[got:])
            if not n:
                return None
            got += n
        self.count += 1
        return np.frombuffer(self.buf, np.uint8).reshape(self.h, self.w)


class JpegStreamReader:
    """Splits an MJPEG image2pipe byte stream into JPEG files by parsing the markers: segment
    lengths up to SOS, then the entropy-coded data up to EOI (in entropy data 0xFF is always
    followed by 0x00 or RSTn, so the first FF D9 after SOS is the EOI)."""

    CHUNK = 1 << 18

    def __init__(self, stream: IO[bytes]):
        self.stream = stream
        self.buf = bytearray()
        self.pos = 0
        self.eof = False
        self.count = 0
        self._chunk = bytearray(self.CHUNK)
        self._view = memoryview(self._chunk)

    def _fill(self) -> bool:
        if self.eof:
            return False
        n = self.stream.readinto(self._view)
        if not n:
            self.eof = True
            return False
        self.buf += self._view[:n]
        return True

    def _need(self, end: int) -> bool:
        while len(self.buf) < end:
            if not self._fill():
                return False
        return True

    def read(self) -> bytes | None:
        """Next complete JPEG (bytes) or None at EOF. Raises FFmpegError on a corrupt stream."""
        if self.pos:                                # compact only here: indexes below are absolute
            del self.buf[: self.pos]
            self.pos = 0
        if not self._need(self.pos + 2):
            return None
        b = self.buf
        start = self.pos
        if b[start] != 0xFF or b[start + 1] != 0xD8:
            nxt = b.find(b"\xff\xd8", start)
            while nxt < 0:
                if not self._fill():
                    return None
                b = self.buf
                nxt = b.find(b"\xff\xd8", start)
            start = nxt
        i = start + 2
        while True:
            if not self._need(i + 4):
                return None
            b = self.buf
            if b[i] != 0xFF:
                raise FFmpegError(f"corrupt MJPEG stream: expected a marker at byte {i}")
            marker = b[i + 1]
            if marker == 0xFF:                      # fill byte
                i += 1
                continue
            if marker == 0xD9:                      # EOI without scan (should not happen)
                end = i + 2
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seglen = (b[i + 2] << 8) | b[i + 3]
            if marker == 0xDA:                      # SOS: entropy data follows the header
                j = i + 2 + seglen
                while True:
                    k = self.buf.find(b"\xff\xd9", j)
                    if k >= 0:
                        end = k + 2
                        break
                    j = max(j, len(self.buf) - 1)
                    if not self._fill():
                        return None
                break
            i += 2 + seglen
        data = bytes(self.buf[start:end])
        self.pos = end
        self.count += 1
        return data


def iter_jpegs(stream: IO[bytes]) -> Iterator[bytes]:
    r = JpegStreamReader(stream)
    while True:
        j = r.read()
        if j is None:
            return
        yield j
