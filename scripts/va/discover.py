"""Input expansion and same-basename VTT matching (spec section 8, items 4-5; plan, item 5).

- ``expand_inputs``: files, folders (non-recursive by default) and globs (``*``/``?`` only, used
  only when the literal path is missing; ``[`` and ``]`` are always literal) -> video paths as
  stored on disk (possibly NFD), deduplicated, in input order (folder contents sorted by
  NFC.casefold). Folder scans and glob matches skip the macOS AppleDouble companions ("._*").
- ``find_vtt``: the strict rule. The subtitle has the same stem as the video (compared with
  NFC.casefold) and the ``.vtt`` extension, in the same folder. Nothing is renamed.
- ``vtt_candidates`` / ``rename_suggestion``: what to show when the rule fails: stems that are a
  prefix of the video stem (the Teams "<name>-YYYYMMDD_HHMMSS-Gravação de Reunião" pattern),
  stems that start with the video stem ("<video>.pt-BR.vtt"), and difflib ratio >= 0.6; plus a
  ``Rename-Item -LiteralPath '<path>' -NewName '<name>'`` line for PowerShell (``mv -n`` on macOS).

Pure functions: no printing, no exceptions for missing inputs (callers decide the error).
"""
from __future__ import annotations

import difflib
import os
from pathlib import Path

from .common import AmbiguousPathError, nfc_casefold, resolve_unicode_paths

__all__ = [
    "VIDEO_EXTS", "VTT_EXT", "expand_inputs", "find_vtt", "vtt_candidates", "rename_suggestion",
    "ps_quote", "sh_quote", "match_vtts", "missing_vtt_detail", "is_video_name",
    "is_appledouble_name",
]

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".flv")
VTT_EXT = ".vtt"
SIMILARITY_MIN = 0.6
MAX_CANDIDATES = 5

# PowerShell treats all of these as single quotes: inside a '...' literal each must be doubled.
_PS_SINGLE_QUOTES = ("'", "\u2018", "\u2019", "\u201a", "\u201b")


def is_video_name(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS


def is_appledouble_name(name: str) -> bool:
    """macOS writes a "._<name>" companion (4 KB of metadata, not media) next to each file it
    copies to exFAT/FAT/SMB volumes; those must never become a video or a subtitle candidate."""
    return name.startswith("._")


def _scan(folder: Path) -> list[os.DirEntry]:
    """The folder's entries, without the AppleDouble companions (every scan here looks for
    candidates; a file the person names explicitly never goes through it)."""
    try:
        with os.scandir(folder) as it:
            return [e for e in it if not is_appledouble_name(e.name)]
    except OSError:
        return []


def _videos_in(folder: Path, recursive: bool) -> list[Path]:
    found: list[Path] = []
    entries = sorted(_scan(folder), key=lambda e: nfc_casefold(e.name))
    subdirs = []
    for e in entries:
        try:
            if e.is_file() and is_video_name(e.name):
                found.append(Path(e.path))
            elif recursive and e.is_dir(follow_symlinks=False):
                subdirs.append(Path(e.path))
        except OSError:
            continue
    for sub in subdirs:
        found.extend(_videos_in(sub, True))
    return found


def _clean_entry(entry: str) -> str:
    s = str(entry).strip()
    for q in ('"', "'", "`"):
        if len(s) >= 2 and s[0] == q and s[-1] == q:
            s = s[1:-1].strip()
    return s


def expand_inputs(entries: list[str], recursive: bool = False) -> tuple[list[Path], list[dict]]:
    """Expand the INPUT list. Returns ``(videos, problems)``.

    ``problems`` items: ``{"input", "reason"}`` with reason ``"not_found"`` (nothing
    matches), ``"folder_without_videos"`` (a folder without video files) or ``"glob_without_videos"``.
    A path listed twice (or matched by two entries) is kept once, at its first position.
    An explicit file is accepted whatever its extension (ffprobe decides later).
    """
    videos: list[Path] = []
    seen: set[str] = set()
    problems: list[dict] = []

    def add(p: Path) -> None:
        key = nfc_casefold(os.path.normpath(str(p)))
        if key not in seen:
            seen.add(key)
            videos.append(p)

    for raw in entries:
        entry = _clean_entry(raw)
        if not entry:
            continue
        has_glob = "*" in entry or "?" in entry
        # "/Volumes/X/*.mp4" also matches the "._" companions; a pattern that itself starts with
        # "._" asks for them, so it keeps them.
        skip_appledouble = has_glob and not is_appledouble_name(os.path.basename(entry.rstrip("/\\")))
        try:
            matches = resolve_unicode_paths(entry)
        except (OSError, ValueError, AmbiguousPathError):
            matches = []
        if not matches:
            problems.append({"input": entry, "reason": "not_found"})
            continue
        matched = 0
        for m in matches:
            if m.is_dir():
                if has_glob and len(matches) > 1:
                    continue  # a glob selects files; folders matched by it are ignored
                vids = _videos_in(m, recursive)
                if not vids:
                    problems.append({"input": entry, "reason": "folder_without_videos"})
                matched += len(vids)
                for v in vids:
                    add(v)
            elif m.is_file():
                if has_glob and not is_video_name(m.name):
                    continue  # "C:/x/*" also matches the .vtt files
                if skip_appledouble and is_appledouble_name(m.name):
                    continue
                matched += 1
                add(m)
        if has_glob and not matched and not any(p["input"] == entry for p in problems):
            problems.append({"input": entry, "reason": "glob_without_videos"})
    return videos, problems


def _stem_key(name: str) -> str:
    return nfc_casefold(os.path.splitext(name)[0])


def _vtts_in(folder: Path) -> list[Path]:
    out = []
    for e in _scan(folder):
        try:
            if os.path.splitext(e.name)[1].lower() == VTT_EXT and e.is_file():
                out.append(Path(e.path))
        except OSError:
            continue
    return sorted(out, key=lambda p: nfc_casefold(p.name))


def find_vtt(video: Path) -> Path | None:
    """Same-basename subtitle: exact NFC.casefold stem match with a ``.vtt`` extension, in the
    video's folder (one directory scan). A byte-identical stem wins when several match."""
    video = Path(video)
    want = _stem_key(video.name)
    hits = [p for p in _vtts_in(video.parent) if _stem_key(p.name) == want]
    if not hits:
        return None
    exact = [p for p in hits if os.path.splitext(p.name)[0] == video.stem]
    return (exact or hits)[0]


def vtt_candidates(video: Path, limit: int = MAX_CANDIDATES) -> list[dict]:
    """Subtitles in the video's folder that probably belong to it, best first.

    Each item: ``{"path": Path, "name": str, "reason": "prefix"|"starts_with_video"|"similar",
    "score": float}``. ``prefixo``: the subtitle stem is a prefix of the video stem (Teams
    downloads the transcript as "<meeting>.vtt" next to "<meeting>-YYYYMMDD_HHMMSS-Gravação de
    Reunião.mp4"); ``comeca_com_video``: the subtitle stem starts with the video stem
    ("aula.pt-BR.vtt"); ``parecido``: difflib ratio >= 0.6 on the NFC.casefold stems.
    """
    video = Path(video)
    vkey = _stem_key(video.name)
    out = []
    for p in _vtts_in(video.parent):
        skey = _stem_key(p.name)
        if not skey or skey == vkey:
            continue
        ratio = difflib.SequenceMatcher(None, vkey, skey).ratio()
        if vkey.startswith(skey):
            out.append({"path": p, "name": p.name, "reason": "prefix", "score": 2.0 + len(skey) / max(len(vkey), 1)})
        elif skey.startswith(vkey):
            out.append({"path": p, "name": p.name, "reason": "starts_with_video", "score": 1.5 + len(vkey) / max(len(skey), 1)})
        elif ratio >= SIMILARITY_MIN:
            out.append({"path": p, "name": p.name, "reason": "similar", "score": ratio})
    out.sort(key=lambda c: (-c["score"], nfc_casefold(c["name"])))
    for c in out:
        c["score"] = round(c["score"], 3)
    return out[:limit]


def ps_quote(text: str) -> str:
    """A PowerShell single-quoted literal: ', ‘, ’, ‚ and ‛ all close such a string, so each is
    doubled inside the quotes. Everything else (brackets, $, backticks) is literal there."""
    s = str(text)
    for q in _PS_SINGLE_QUOTES:
        s = s.replace(q, q + q)
    return f"'{s}'"


def sh_quote(text: str) -> str:
    """A POSIX shell single-quoted literal: nothing is special inside, and a ' becomes '\\''."""
    return "'" + str(text).replace("'", "'\\''") + "'"


def rename_suggestion(vtt: Path, video: Path, posix: bool | None = None) -> str:
    """The command that gives the subtitles the video's name, in the person's shell. On Windows,
    ``Rename-Item -LiteralPath '<vtt>' -NewName '<video stem>.vtt'`` (PowerShell; Windows separators and
    the names exactly as stored on disk, so -LiteralPath matches NFD names too); elsewhere (macOS),
    ``mv -n '<vtt>' '<folder>/<video stem>.vtt'`` (-n never overwrites). ``posix`` forces one (tests)."""
    new = Path(video).stem + VTT_EXT
    if posix if posix is not None else os.name != "nt":
        return f"mv -n {sh_quote(str(vtt))} {sh_quote(str(Path(video).with_name(new)))}"
    src = os.path.normpath(str(vtt))
    return f"Rename-Item -LiteralPath {ps_quote(src)} -NewName {ps_quote(new)}"


_REASON_TEXT = {"prefix": "prefix of the video name", "starts_with_video": "starts with the video name",
                "similar": "similar name"}


def match_vtts(videos: list[Path], explicit_vtt: Path | None = None,
               skip_missing: bool = False) -> tuple[list[tuple[Path, Path]], list[Path], list[dict]]:
    """Pair each video with its subtitle. Returns ``(pairs, skipped, missing)``.

    - ``explicit_vtt`` (--vtt) is used for the single video; callers enforce "exactly one".
    - ``missing`` items: ``{"video", "candidates", "suggestion"}`` (suggestion may be None).
      With ``skip_missing`` those videos go to ``skipped`` instead, and ``missing`` stays empty.
    """
    pairs: list[tuple[Path, Path]] = []
    skipped: list[Path] = []
    missing: list[dict] = []
    for video in videos:
        vtt = Path(explicit_vtt) if explicit_vtt is not None else find_vtt(video)
        if vtt is not None:
            pairs.append((video, vtt))
            continue
        if skip_missing:
            skipped.append(video)
            continue
        cands = vtt_candidates(video)
        missing.append({"video": video, "candidates": cands,
                        "suggestion": rename_suggestion(cands[0]["path"], video) if cands else None})
    return pairs, skipped, missing


def missing_vtt_detail(missing: list[dict], allow_vtt_flag: bool) -> str:
    """The English ``error.detail`` for ``missing_vtt``: per video, the candidates in its folder and
    the suggested rename (``rename_suggestion``; the user decides; nothing is renamed automatically)."""
    n = len(missing)
    lines = [f"No .vtt subtitles with the same name for {n} video{'s' if n > 1 else ''} "
             "(strict rule: the same name as the video, with the .vtt extension, in the same folder):"]
    for m in missing:
        lines.append(f"- {os.path.normpath(str(m['video']))}")
        cands = m["candidates"]
        if cands:
            lines.append("  candidates in the folder: " + "; ".join(f"{c['name']} ({_REASON_TEXT[c['reason']]})" for c in cands))
            shell = "PowerShell" if str(m["suggestion"]).startswith("Rename-Item") else "Terminal"
            lines.append(f"  suggestion ({shell}): {m['suggestion']}")
        else:
            lines.append("  no similar .vtt in the folder")
    alt = ["--skip-missing skips the videos without subtitles"]
    if allow_vtt_flag:
        alt.insert(0, '--vtt "<path of the .vtt>" points to the subtitles (only with 1 video)')
    lines.append("Rename the .vtt, not the video (a renamed video is extracted again). Alternatives: " + "; ".join(alt) + ".")
    return "\n".join(lines)
