"""Shared helpers for the video-analysis scripts (spec section 10, WP0).

Conventions
- Time: integer milliseconds internally. Public time helpers take and return SECONDS unless
  the name ends in ``_ms``. JSON stores seconds with 3 decimals (``ms_to_s``).
- Files: UTF-8, "\\n" newlines, written atomically (temp file in the same folder + os.replace).
- Paths written to JSON/MD use forward slashes (``as_posix``); callers make them absolute.
- Nothing here touches the console or the filesystem at import time.
"""
from __future__ import annotations

import errno
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

from . import NAMES, WORKSPACE_DEFAULT_NAME, WORKSPACE_ENV

__all__ = [
    "AmbiguousPathError",
    "as_posix",
    "atomic_write_bytes",
    "batch_filename",
    "batch_id",
    "cue_id",
    "dumps_json",
    "fmt_hhmmss_compact",
    "fmt_hms",
    "frame_filename",
    "frame_id",
    "ms_to_s",
    "nfc_casefold",
    "parse_time",
    "parse_time_ms",
    "read_json",
    "resolve_unicode_path",
    "resolve_unicode_paths",
    "s_to_ms",
    "setup_console",
    "slugify",
    "video_title",
    "workspace_home",
    "write_json",
    "write_text",
]


# --- console ---------------------------------------------------------------------------

def setup_console(*, ascii_only: bool = False) -> None:
    """Make stdout/stderr UTF-8 with "\\n" newlines (the Windows console is cp1252).

    ``ascii_only=True`` (for consoles that re-decode output, the ``--ascii`` flag) writes
    ASCII with backslash escapes instead. Idempotent; streams without ``reconfigure``
    (or already closed) are left alone.
    """
    encoding, errors = ("ascii", "backslashreplace") if ascii_only else ("utf-8", "replace")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding=encoding, errors=errors, newline="\n")
        except (ValueError, OSError, io.UnsupportedOperation):
            pass


# --- files -----------------------------------------------------------------------------

_REPLACE_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)


def _replace_with_retry(src: str, dst: Path) -> None:
    """os.replace with a short backoff: antivirus, the indexer or an open viewer can hold
    the target for a moment on Windows (spec section 8, item 8)."""
    for delay in _REPLACE_DELAYS:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(src, dst)


def atomic_write_bytes(path: str | os.PathLike, data: bytes) -> Path:
    """Write ``data`` to ``path`` atomically: readers see the old file or the new one, never
    a partial one. Creates missing parent folders. Returns the path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + target.name[:40] + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        _replace_with_retry(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def write_text(path: str | os.PathLike, text: str) -> Path:
    """Write ``text`` as UTF-8, exactly as given (no newline translation), atomically."""
    return atomic_write_bytes(path, text.encode("utf-8"))


def dumps_json(obj: Any, *, line: bool = False) -> str:
    """Serialize like every JSON file of the skill: UTF-8 text (ensure_ascii=False),
    insertion order, allow_nan=False (NaN/inf raise ValueError).

    Default: indent=1 plus a final "\\n" (files). ``line=True``: one compact line with no
    final newline (the prepare.py stdout JSON).
    """
    if line:
        return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


def write_json(path: str | os.PathLike, obj: Any) -> Path:
    """Write ``obj`` as deterministic JSON (see ``dumps_json``), atomically. Equal inputs
    give byte-identical files. On a serialization error nothing is written."""
    return write_text(path, dumps_json(obj))


def read_json(path: str | os.PathLike) -> Any:
    """Read a JSON file (UTF-8; a BOM written by other tools is tolerated)."""
    return json.loads(Path(path).read_bytes().decode("utf-8-sig"))


# --- time --------------------------------------------------------------------------------

def _check_number(t: Any) -> float:
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        raise TypeError(f"expected a number of seconds, got {type(t).__name__}: {t!r}")
    if not math.isfinite(t):
        raise ValueError(f"time must be finite, got {t!r}")
    return t


def s_to_ms(t: float) -> int:
    """Seconds -> integer milliseconds (half away from zero)."""
    t = _check_number(t)
    ms = int(math.floor(abs(t) * 1000.0 + 0.5))
    return -ms if t < 0 else ms


def ms_to_s(ms: int | float) -> float:
    """Milliseconds -> seconds with 3 decimals, as stored in JSON."""
    ms = _check_number(ms)
    return round(math.floor(ms + 0.5) / 1000.0, 3)


def fmt_hms(t: float, dec: int = 1) -> str:
    """Seconds -> "HH:MM:SS.d" (hours always shown, never truncated past 99).

    ``dec`` 1..3 rounds half-up at that decimal (00:02:58.697 -> "00:02:58.7").
    ``dec=0`` truncates to the whole second, like a clock reading and like
    ``fmt_hhmmss_compact`` (544.512 -> "00:09:04").
    """
    ms = s_to_ms(t)
    sign = "-" if ms < 0 else ""
    ms = abs(ms)
    if dec <= 0:
        secs, frac, dec = ms // 1000, 0, 0
    else:
        dec = min(int(dec), 3)
        scale = 10 ** dec
        div = 1000 // scale
        units = (ms + div // 2) // div
        secs, frac = divmod(units, scale)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    out = f"{sign}{h:02d}:{m:02d}:{s:02d}"
    return f"{out}.{frac:0{dec}d}" if dec else out


def fmt_hhmmss_compact(t: float) -> str:
    """Seconds -> "HHMMSS" of the whole second (floored), for frame file names."""
    ms = s_to_ms(t)
    if ms < 0:
        raise ValueError(f"negative time: {t!r}")
    h, rem = divmod(ms // 1000, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}{m:02d}{s:02d}"


_CLOCK_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d+))?$")
_SECONDS_RE = re.compile(r"^(\d+)(?:[.,](\d+))?$")


def _fraction_ms(frac: str | None) -> int:
    if not frac:
        return 0
    if len(frac) <= 3:
        return int(frac.ljust(3, "0"))  # "5" -> 500 ms (padded to the right)
    num, den = int(frac) * 1000, 10 ** len(frac)
    return (2 * num + den) // (2 * den)  # round half-up to the millisecond


def parse_time_ms(value: str | int | float) -> int:
    """Parse "[HH:]MM:SS[.mmm]" or plain seconds ("90", "90.5") -> integer milliseconds.

    Accepts "," as the decimal separator, 1-3 fraction digits padded to the right (more are
    rounded), any number of hour digits and an optional leading sign. Minutes and seconds
    must be below 60 in clock form. Numbers are taken as seconds. Raises ValueError.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return s_to_ms(value)
    if not isinstance(value, str):
        raise TypeError(f"expected str or number, got {type(value).__name__}")
    s = value.strip()
    sign = 1
    if s and s[0] in "+-":
        sign = -1 if s[0] == "-" else 1
        s = s[1:].strip()
    m = _CLOCK_RE.match(s)
    if m:
        hours, minutes, seconds = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"minutes and seconds must be < 60: {value!r}")
        return sign * (((hours * 60 + minutes) * 60 + seconds) * 1000 + _fraction_ms(m.group(4)))
    m = _SECONDS_RE.match(s)
    if m:
        return sign * (int(m.group(1)) * 1000 + _fraction_ms(m.group(2)))
    raise ValueError(f"invalid time {value!r}: use [HH:]MM:SS[.mmm] or seconds")


def parse_time(value: str | int | float) -> float:
    """Like ``parse_time_ms`` but returns seconds (3 decimals)."""
    return ms_to_s(parse_time_ms(value))


# --- names ---------------------------------------------------------------------------------

_TEAMS_STAMP_RE = re.compile(r"(?<![0-9])([0-9]{8})[_ ]([0-9]{6})(?![0-9])")
_SLUG_DROP = dict.fromkeys(map(ord, "\u2019\u2018\u201c\u201d'\"`"), None)
_SLUG_SEP_RE = re.compile(r"[^a-z0-9]+")


def _slug_base(text: str) -> str:
    return _SLUG_SEP_RE.sub("-", text.translate(_SLUG_DROP).lower()).strip("-")


def _cut_at_hyphen(slug: str, max_len: int) -> str:
    if len(slug) <= max_len:
        return slug
    if slug[max_len] == "-":
        return slug[:max_len].rstrip("-")
    cut = slug.rfind("-", 0, max_len)
    return (slug[:cut] if cut > 0 else slug[:max_len]).rstrip("-")


def slugify(stem: str, max_len: int = 24) -> str:
    """ASCII slug for folder names (spec section 1).

    NFKD, drop combining marks and quotes (’ ‘ “ ” ' " `), lowercase, runs of [^a-z0-9] -> "-",
    cut to ``max_len`` chars at a "-". A Teams stamp ``yyyymmdd_hhmmss`` (or with a space)
    is appended as "-yyyymmdd-hhmmss"; the base is then the text before the stamp (the text
    after it is the "Gravação de Reunião" suffix), or the text after it when nothing comes
    before. Empty result -> "video". At most max_len + 16 chars.
    """
    text = unicodedata.normalize("NFKD", str(stem))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    stamp = _TEAMS_STAMP_RE.search(text)
    if stamp:
        suffix = f"-{stamp.group(1)}-{stamp.group(2)}"
        base = _slug_base(text[: stamp.start()]) or _slug_base(text[stamp.end():])
    else:
        suffix, base = "", _slug_base(text)
    base = _cut_at_hyphen(base, max_len)
    slug = f"{base}{suffix}" if base else suffix.lstrip("-")
    return slug or "video"


def frame_id(n: int, total: int = 0) -> str:
    """1-based frame id: "F0001"; 5 digits when the video has more than 9,999 frames."""
    width = 5 if max(n, total) > 9999 else 4
    return f"F{n:0{width}d}"


def frame_filename(n: int, captured_at: float, total: int = 0) -> str:
    """"F0041_000254.jpg": frame id + HHMMSS of captured_at (seconds, floored)."""
    return f"{frame_id(n, total)}_{fmt_hhmmss_compact(captured_at)}.jpg"


def cue_id(n: int) -> str:
    return NAMES["cue_id"].format(n=n)


def batch_id(n: int) -> str:
    return NAMES["batch_id"].format(n=n)


def batch_filename(kind: str, n: int) -> str:
    """``kind`` is "batch_md", "batch_json" or "sheet": batch_filename("sheet", 3) -> "sheet_003.jpg"."""
    if kind not in ("batch_md", "batch_json", "sheet"):
        raise ValueError(f"unknown batch file kind: {kind!r}")
    return NAMES[kind].format(NNN=f"{n:03d}")


def video_title(path: str | os.PathLike) -> str:
    """Display title: the file stem in NFC (the on-disk name may be NFD)."""
    return unicodedata.normalize("NFC", Path(path).stem)


def nfc_casefold(text: str) -> str:
    """Sort/compare key used everywhere (scandir order is not guaranteed)."""
    return unicodedata.normalize("NFC", text).casefold()


# --- paths ---------------------------------------------------------------------------------

class AmbiguousPathError(ValueError):
    """More than one existing path matches; ``matches`` lists them (sorted)."""

    def __init__(self, path: str | os.PathLike, matches: list[Path]):
        self.path = os.fspath(path)
        self.matches = list(matches)
        shown = ", ".join(as_posix(m) for m in self.matches[:5])
        more = f" (+{len(self.matches) - 5})" if len(self.matches) > 5 else ""
        super().__init__(f"{len(self.matches)} paths match {self.path!r}: {shown}{more}")


_QUOTE_FOLD = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u2032": "'", "\u00b4": "'",
                             "`": "'", "\u201c": '"', "\u201d": '"', "\u2033": '"'})
_SPACE_RUN_RE = re.compile(r"\s+")


def _key_nfc(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def _key_quotes(name: str) -> str:
    return nfc_casefold(name).translate(_QUOTE_FOLD)


def _key_spaces(name: str) -> str:
    return _SPACE_RUN_RE.sub(" ", _key_quotes(name)).strip()


# Tried in order, only when the literal name is missing: NFC/NFD difference, then case,
# then curly vs straight quotes, then runs of spaces ("databricks  schema").
_NAME_KEYS = (_key_nfc, nfc_casefold, _key_quotes, _key_spaces)
_GLOB_KEYS = (nfc_casefold, _key_quotes)


def _has_glob(text: str) -> bool:
    return "*" in text or "?" in text


def _glob_regex(pattern: str) -> re.Pattern[str]:
    # Only "*" and "?" are wildcards; "[" and "]" stay literal ("[PES] ..." names exist).
    return re.compile("".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern), re.DOTALL)


def _list_dir(folder: Path) -> list[str]:
    try:
        with os.scandir(folder) as entries:
            return [e.name for e in entries]
    except OSError:
        return []


def _match_component(folder: Path, part: str) -> list[Path]:
    names = _list_dir(folder)
    if not names:
        return []
    if _has_glob(part):
        for key in _GLOB_KEYS:
            rx = _glob_regex(key(part))
            hits = [n for n in names if rx.fullmatch(key(n))]
            if hits:
                return [folder / n for n in hits]
        return []
    for key in _NAME_KEYS:
        wanted = key(part)
        hits = [n for n in names if key(n) == wanted]
        if hits:
            return [folder / n for n in hits]
    return []


def resolve_unicode_paths(p: str | os.PathLike) -> list[Path]:
    """All existing paths matching ``p`` (spec section 8, item 4), as absolute paths with the
    names exactly as stored on disk (possibly NFD), sorted by NFC.casefold.

    The literal path wins when it exists. Otherwise each component is matched against the
    folder entries, trying: same NFC text, NFC.casefold, curly/straight quotes folded, runs
    of whitespace collapsed. A component containing "*" or "?" is a glob (used only because
    the literal path is missing); "[" and "]" are always literal. Returns [] if nothing matches
    (also for an empty string, which would otherwise mean the current folder).
    """
    if not os.fspath(p).strip():
        return []
    absolute = os.path.abspath(os.path.expanduser(os.fspath(p)))
    if not _has_glob(absolute) and os.path.lexists(absolute):
        return [Path(absolute)]
    path = Path(absolute)
    current = [Path(path.anchor)]
    for part in path.parts[1:]:
        found: list[Path] = []
        for folder in current:
            literal = folder / part
            if not _has_glob(part) and os.path.lexists(literal):
                found.append(literal)
            else:
                found.extend(_match_component(folder, part))
        current = list(dict.fromkeys(found))
        if not current:
            return []
    return sorted(current, key=lambda q: nfc_casefold(str(q)))


def resolve_unicode_path(p: str | os.PathLike) -> Path:
    """The single existing path matching ``p`` (see ``resolve_unicode_paths``).
    Raises FileNotFoundError when nothing matches, AmbiguousPathError when several do."""
    matches = resolve_unicode_paths(p)
    if not matches:
        raise FileNotFoundError(errno.ENOENT, "no such file or folder (also tried Unicode/case/quote variants)", os.fspath(p))
    if len(matches) > 1:
        raise AmbiguousPathError(p, matches)
    return matches[0]


def as_posix(p: str | os.PathLike) -> str:
    """Path -> string with forward slashes ("C:\\\\a\\\\b" -> "C:/a/b"). No filesystem access,
    no normalization: pass an absolute path to get an absolute result."""
    return os.fspath(p).replace("\\", "/")


def workspace_home(cli_value: str | os.PathLike | None = None) -> Path:
    """Workspace root: ``cli_value`` (--home), else env VIDEO_ANALYSIS_HOME, else
    %USERPROFILE%\\video-analysis (spec section 2: "--home PATH, default: env
    VIDEO_ANALYSIS_HOME or %USERPROFILE%\\video-analysis"). Blank values and surrounding
    double quotes are ignored. Returned absolute; nothing is created."""
    cli = os.fspath(cli_value).strip().strip('"').strip() if cli_value is not None else ""
    env = os.environ.get(WORKSPACE_ENV, "").strip().strip('"').strip()
    if cli:
        value = cli
    elif env:
        value = env
    else:
        base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        value = os.path.join(base, WORKSPACE_DEFAULT_NAME)
    return Path(os.path.abspath(os.path.expanduser(value)))
