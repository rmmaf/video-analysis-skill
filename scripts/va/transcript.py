"""Local transcription: a video without subtitles is transcribed with WhisperX in the transcription
environment (setup.md, step 6), and this module keeps the result in the video's cache and renders the VTT
that the rest of the skill reads (reference/contracts.md, section 7).

Layout (``<home>/cache/<video_key>/``)::

    transcript-<a8>/   asr.json  stage.json  transcribe.log  partial/   (ASR + word alignment)
                       diar-<d8>.json                                    (pyannote's speaker turns)
                       <d8>-<render>.vtt                                 (the rendered subtitles)
    transcript-export.json    the subtitles this module wrote next to the video (path and SHA-1; the NOTE's
                              own hash makes it optional: --gc removes it with the folder)
    .transcript.lock          its own lock: a transcription takes hours on a CPU (a live one keeps --gc away)

Keys
- ``a8`` = h8(TRANSCRIBE_VERSION, video_key, Whisper model, requested language, whisperx version): the ASR
  and the alignment, the slow part, depend neither on the speakers nor on the rendering.
- ``d8`` = h8(TRANSCRIBE_VERSION, speakers, mode, diarization model). ``mode``: ``pyannote`` (speaker
  turns), ``single`` (one speaker, no pyannote: every cue is SPEAKER_00) or ``none`` (no Hugging Face token:
  no speaker labels).

``cache.fresh_dir`` is never used here: ``partial/`` keeps the checkpoints of an interrupted ASR, which the
next run resumes. ``stage.json`` is written last, by this module, once ``asr.json`` is valid; a
``diar-<d8>.json`` that failed for lack of access (no token, conditions not accepted) does not count as done,
nor one that failed for another reason when the call gives the speaker count again.

Next to the video, ``<stem>.vtt`` is created only when absent, and whole (``common.atomic_create_bytes``),
with a NOTE that says how it was made. One this module wrote and nobody edited since (the ``content_sha1``
of its NOTE: the SHA-1 of the whole file with that line written without it, which survives --gc and a move
of the video with its subtitles) is replaced when a call asks for other transcription settings; a file the
person wrote or edited is never touched.

Numpy-free and torch-free: everything heavy runs in scripts/transcribe.py, in a subprocess.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from . import TRANSCRIBE_DEFAULTS, TRANSCRIBE_VERSION, WHISPER_ALIASES, WHISPER_MODELS, cache
from .common import as_posix, atomic_create_bytes, atomic_write_bytes, nfc_casefold, read_json, write_json

__all__ = [
    "EXPORT_JSON", "LOCK_NAME", "STAGE", "TranscriptError", "asr_done", "asr_key", "build_cues", "canon_language",
    "canon_model", "canon_speakers", "diar_done", "diar_key", "driver_env", "estimate_seconds", "export_sidecar",
    "format_num_speakers",
    "load_asr", "load_diar", "make", "needs_status", "note_lines", "own_sidecar", "parse_num_speakers", "plan",
    "read_note", "render_vtt", "sidecar_path", "speakers_bounds", "speakers_for", "stage_dir",
    "unknown_speaker_keys", "view", "wants_redo",
]

STAGE = "transcript"
EXPORT_JSON = "transcript-export.json"
LOCK_NAME = cache.TRANSCRIPT_LOCK             # .transcript.lock: one of cache.LOCK_FILES, which --gc respects
NOTE_KEY = "video-analysis:"                  # the machine-readable line of the NOTE block
CONTENT_KEY = "content_sha1"                  # in that line: the file's own hash (``_content_sha1``)
MAX_SPEAKERS = 20
# languages written without spaces between words: the words of a cue are joined without one
NO_SPACE_LANGS = frozenset({"ja", "zh", "yue", "th", "lo", "my", "km", "bo"})
DRIVER_EXIT = {
    2: "transcribe.py refused its arguments (a bug of the skill)",
    3: "the WhisperX environment does not load: run /video-analysis setup (step 6 repairs it)",
    5: "out of memory even with the smallest batch: try whisper_model: large-v3-turbo, or medium",
    130: "interrupted",
    -1: "transcribe.py was stopped from outside (killed)",
    0xFFFFFFFF: "transcribe.py was stopped from outside (killed)",   # Windows' -1, as an unsigned exit code
}


class TranscriptError(RuntimeError):
    """The transcription of one video failed; the message ends with the end of transcribe.log."""


# --- options --------------------------------------------------------------------------------------------

def _fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    return re.sub(r"\s+", " ", s).strip()


_AUTO = {"auto", "nao sei", "?", "detectar", "automatico", "unknown", "varia", "nao sei / detectar"}
_NOISE = re.compile(r"\b(?:pessoas?|falantes?|people|persons?|speakers?|participantes?)\b")


def canon_speakers(value: Any) -> str:
    """One video's speaker count -> ``"3"``, ``"2-4"`` (a range), ``"4+"`` (a minimum) or ``"auto"``
    ("não sei", "?"). Raises ValueError."""
    s = _NOISE.sub(" ", _fold(value).strip("\"'` ")).strip()
    s = re.sub(r"\s+", " ", s)
    if s in _AUTO or not s:
        if not s:
            raise ValueError("empty")
        return "auto"
    m = re.fullmatch(r"(\d{1,3})", s)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= MAX_SPEAKERS:
            raise ValueError(f"use 1 to {MAX_SPEAKERS}")
        return str(n)
    m = re.fullmatch(r"(\d{1,3}) ?(?:-|–|a|ate|to) ?(\d{1,3})", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if not 1 <= lo <= hi <= MAX_SPEAKERS:
            raise ValueError(f"a range from 1 to {MAX_SPEAKERS}, the smaller number first")
        return str(lo) if lo == hi else f"{lo}-{hi}"
    m = re.fullmatch(r"(\d{1,3}) ?(?:\+|ou mais|or more)", s)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= MAX_SPEAKERS:
            raise ValueError(f"use 1 to {MAX_SPEAKERS}")
        return "auto" if n == 1 else f"{n}+"
    raise ValueError("use a number (3), a range (2-4), a minimum (4+) or auto")


def speakers_bounds(spec: str | None) -> tuple[int | None, int | None]:
    """``"3"`` -> (3, 3); ``"2-4"`` -> (2, 4); ``"4+"`` -> (4, None); ``"auto"``/None -> (None, None)."""
    if not spec or spec == "auto":
        return None, None
    if spec.endswith("+"):
        return int(spec[:-1]), None
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return int(lo), int(hi)
    return int(spec), int(spec)


def parse_num_speakers(value: Any) -> dict:
    """The ``num_speakers`` option -> ``{"all": spec | None, "videos": {key: spec}}``. ``3`` holds for every
    video; ``v1=3; reunião.mp4=auto`` per video (the video's ``vid`` in the estimate, its slug or its file
    name); ``3; v2=auto`` both. Raises ValueError."""
    out: dict[str, Any] = {"all": None, "videos": {}}
    for part in re.split(r"[;\n]", str(value)):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, _, spec = part.rpartition("=")
            key = key.strip().strip("\"'`").strip()
            if not key:
                raise ValueError("a per-video value needs the video before the '=' (v1=3)")
            fk = nfc_casefold(key)
            if fk in out["videos"]:
                raise ValueError(f"{key} appears twice")
            out["videos"][fk] = canon_speakers(spec)
        else:
            if out["all"] is not None:
                raise ValueError("two values for every video")
            out["all"] = canon_speakers(part)
    if out["all"] is None and not out["videos"]:
        raise ValueError("empty")
    return out


def format_num_speakers(opt: dict | None) -> str | None:
    """``parse_num_speakers``'s result back to its text: ``4-12``, ``v1=3; v2=auto``, ``3; v2=auto``."""
    if not opt:
        return None
    parts = ([opt["all"]] if opt.get("all") else []) + [f"{k}={v}" for k, v in (opt.get("videos") or {}).items()]
    return "; ".join(parts) or None


def _video_keys(vid: str, video: Any, slug: str) -> set[str]:
    p = Path(str(video))
    return {nfc_casefold(vid), nfc_casefold(slug), nfc_casefold(p.name), nfc_casefold(p.stem)}


def speakers_for(opt: dict | None, vid: str, video: Any, slug: str) -> str | None:
    """The speakers given for this video (its own value, else the one for every video); None: not given."""
    if not opt:
        return None
    keys = _video_keys(vid, video, slug)
    for k, spec in (opt.get("videos") or {}).items():
        if k in keys:
            return spec
    return opt.get("all")


def unknown_speaker_keys(opt: dict | None, videos: list[tuple[str, Any, str]]) -> list[str]:
    """Per-video keys of ``num_speakers`` that name no video of the run (``videos``: (vid, path, slug))."""
    if not opt:
        return []
    known: set[str] = set()
    for vid, video, slug in videos:
        known |= _video_keys(vid, video, slug)
    return [k for k in (opt.get("videos") or {}) if k not in known]


def canon_model(value: Any) -> str:
    """A faster-whisper model name (``turbo`` -> ``large-v3-turbo``, ``large`` -> ``large-v3``)."""
    s = _fold(value).replace(" ", "-").replace("_", "-")
    s = WHISPER_ALIASES.get(s, s)
    if s not in WHISPER_MODELS:
        raise ValueError("use one of " + ", ".join(sorted(WHISPER_MODELS)))
    return s


def canon_language(value: Any) -> str:
    """``auto`` or the primary subtag of a language code (``pt-BR`` -> ``pt``)."""
    s = _fold(value)
    if s in ("auto", "automatico", "detectar", "detect"):
        return "auto"
    m = re.fullmatch(r"([a-z]{2,3})(?:[-_][a-z0-9-]+)?", s)
    if not m:
        raise ValueError("use auto or a code such as pt, en or es")
    return m.group(1)


# --- keys and state -------------------------------------------------------------------------------------

def asr_key(video_key: str, model: str, language: str) -> str:
    return cache.h8({"stage": STAGE, "version": TRANSCRIBE_VERSION, "video_key": video_key, "model": model,
                     "language": language, "whisperx": TRANSCRIBE_DEFAULTS["whisperx_version"]})


def diar_key(speakers: str, mode: str) -> str:
    return cache.h8({"stage": "diarization", "version": TRANSCRIBE_VERSION, "speakers": speakers, "mode": mode,
                     "model": TRANSCRIBE_DEFAULTS["diarize_model"] if mode == "pyannote" else None})


def stage_dir(video_dir: str | os.PathLike, akey: str) -> Path:
    return Path(video_dir) / f"{STAGE}-{akey}"


def asr_done(sdir: str | os.PathLike, akey: str) -> dict | None:
    """The ``stage.json`` of a complete ASR (with ``asr.json`` in place), else None."""
    sdir = Path(sdir)
    doc = cache.read_stage(sdir)
    if not doc or doc.get("status") != "complete" or doc.get("stage") != STAGE or doc.get("key") != akey:
        return None
    return doc if (sdir / "asr.json").is_file() else None


def diar_done(sdir: str | os.PathLike, dkey: str, retry_failed: bool = False) -> dict | None:
    """``diar-<d8>.json`` when complete. A diarization that failed for lack of access does not count: a
    later run tries it again (the token or the conditions may be in place by then). One that failed for
    another reason (a proxy or the network down, out of memory) does not count either when ``retry_failed``:
    the call gave the speaker count again, after fixing the cause; a call without one uses it as it is, so a
    failure that repeats costs neither a question nor another pass of pyannote at every run."""
    try:
        doc = read_json(Path(sdir) / f"diar-{dkey}.json")
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("complete") is not True or doc.get("key") != dkey:
        return None
    if not doc.get("diarized") and (retry_failed or doc.get("reason") in ("no_token", "no_access")):
        return None
    return doc


def _latest_diar(sdir: Path) -> dict | None:
    """The most recent complete diarization in this ASR folder (any speaker count)."""
    best = None
    for p in sdir.glob("diar-*.json"):
        doc = diar_done(sdir, p.stem[len("diar-"):])
        if doc and (best is None or str(doc.get("created") or "") > str(best.get("created") or "")):
            best = doc
    return best


# --- the plan of one video --------------------------------------------------------------------------------

def _mode(speakers: str, token: bool) -> str:
    if speakers == "1":
        return "single"
    return "pyannote" if token else "none"


def plan(*, video: Any, video_dir: str | os.PathLike, video_key: str, vid: str, slug: str, opts: dict,
         status: dict | None, vtt: Any = None, candidates: list | None = None, own: dict | None = None) -> dict:
    """What to do about the subtitles of one video. ``vtt``: the same-name subtitles, when present; ``own``:
    their NOTE when this module wrote them and nobody edited them since (``own_sidecar``); ``status``: the
    transcription environment (``environment.transcription_status``), needed unless the plan is ``file``.

    ``state``: ``file`` (use ``vtt``), ``cached`` (the transcript is in the cache: only the VTT to render),
    ``pending`` (``phase`` ``asr`` -- the ASR and, when needed, the diarization -- or ``diar``), or ``choice``
    (no subtitles, but similar ones in the folder, such as a Teams transcript under the meeting's name, and no
    speaker count given: prepare.py keeps the ``missing_vtt`` of before, and the estimate offers the choice).
    ``needs_speakers``: the transcript will be diarized and the call gave no speaker count."""
    given = speakers_for(opts.get("num_speakers"), vid, video, slug)
    wanted_model, wanted_lang = opts.get("whisper_model"), opts.get("audio_language")
    speakers_kept: str | None = None
    out: dict[str, Any] = {"state": "file", "phase": None, "vtt": vtt, "replace": False, "model": None,
                           "language": None, "device": None, "speakers": given, "speakers_given": given is not None,
                           "mode": None, "needs_speakers": False, "candidates": [as_posix(c) for c in candidates or []],
                           "akey": None, "dkey": None, "sdir": None, "estimate_s": None, "detail": None}
    if vtt is not None:
        redo = own is not None and wants_redo(own, given, wanted_model, wanted_lang, status)
        if not redo:
            return out
        out["replace"] = True
        # what the call does not change stays as the subtitles were made: another count redoes only the speakers
        wanted_model = wanted_model or _from_note(canon_model, own.get("model"))
        wanted_lang = wanted_lang or _from_note(canon_language, own.get("language_requested"))
        if given is None:                         # and the count they were made with ("auto" counts as none)
            inherited = _from_note(canon_speakers, own.get("speakers"))
            if inherited != "auto":
                speakers_kept = inherited
    if status is None:
        raise ValueError("plan: the transcription status is needed")
    model = wanted_model or status.get("default_model") or TRANSCRIBE_DEFAULTS["model_cpu"]
    language = wanted_lang or "auto"
    token = bool(status.get("token"))
    akey = asr_key(video_key, model, language)
    sdir = stage_dir(video_dir, akey)
    out.update(model=model, language=language, device=status.get("device") or "cpu", akey=akey, sdir=sdir)
    done = asr_done(sdir, akey)
    if given is None and speakers_kept is not None:
        speakers, mode = speakers_kept, _mode(speakers_kept, token)
    elif given is None:
        prev = _latest_diar(sdir) if done else None
        if prev is not None:                      # this video was transcribed before: no question
            out.update(state="cached", speakers=prev.get("speakers") or "auto", mode=prev.get("mode"),
                       dkey=prev.get("key"))
            return out
        speakers, mode = "auto", _mode("auto", token)
        out["needs_speakers"] = mode == "pyannote"
    else:
        speakers, mode = given, _mode(given, token)
    dkey = diar_key(speakers, mode)
    out.update(speakers=speakers, mode=mode, dkey=dkey)
    if done and (mode != "pyannote" or diar_done(sdir, dkey, retry_failed=given is not None)):
        out["state"] = "cached"
    elif done:
        out.update(state="pending", phase="diar")
    else:
        out.update(state="pending", phase="asr")
    if vtt is None and candidates and given is None and out["state"] != "cached":
        out["state"] = "choice"
    if mode == "none" and out["state"] != "cached":
        out["detail"] = ("no Hugging Face token: the subtitles will not separate the speakers "
                         "(run /video-analysis setup, step 7)")
    return out


def _from_note(canon, value: Any) -> str | None:
    """A setting read from the NOTE of our subtitles, or None when it is missing or makes no sense."""
    if value is None:
        return None
    try:
        return canon(value)
    except ValueError:
        return None


def needs_status(own: dict | None, given: str | None, model: str | None, language: str | None) -> bool:
    """Whether deciding about subtitles this module wrote needs the transcription status (a subprocess of
    about a second): only when the call gives transcription settings, or when they came out without speakers
    for lack of a token. The usual repeat run of a transcribed video needs none."""
    if own is None:
        return False
    return given is not None or model is not None or language is not None or own.get("reason") == "no_token"


def wants_redo(own: dict, given: str | None, model: str | None, language: str | None, status: dict | None) -> bool:
    """Replace subtitles this module wrote: the call asks for other settings; or they came out without
    speakers and a token is there now (asked again with the same count, or none had been given)."""
    if model is not None and model != own.get("model"):
        return True
    if language is not None and language != own.get("language_requested"):
        return True
    token = bool(status and status.get("ok") and status.get("token"))
    undiarized = own.get("mode") in ("pyannote", "none") and not own.get("diarized")
    if given is not None:
        return given != own.get("speakers") or (undiarized and token)
    return own.get("reason") == "no_token" and token


def estimate_seconds(duration_s: float, device: str | None, model: str | None, phase: str | None,
                     mode: str | None) -> float:
    """Rough seconds of transcribe.py on this video (cost model in TRANSCRIBE_DEFAULTS). On macOS, where only
    Apple silicon transcribes, the "cpu" device (and "mps") takes the "apple" profile: the alignment and the
    speakers run on the GPU there, so the 4-core laptop's CPU numbers would overstate them several times."""
    profile = device or "cpu"
    if sys.platform == "darwin" and profile in ("cpu", "mps"):
        profile = "apple"
    cost = TRANSCRIBE_DEFAULTS["cost"].get(profile) or TRANSCRIBE_DEFAULTS["cost"]["cpu"]
    dur = max(0.0, float(duration_s or 0.0))
    s = float(cost["startup_s"])
    if phase == "asr":
        s += dur * float(cost["asr"].get(model or "", cost["asr_other"])) + dur * float(cost["align"])
    if mode == "pyannote" and phase in ("asr", "diar"):
        s += dur * float(cost["diarize"])
    return round(s, 1)


def view(p: dict) -> dict:
    """``videos[].transcript`` of the prepare.py stdout (reference/contracts.md, section 3)."""
    keys = ("state", "phase", "model", "device", "language", "language_probability", "speakers", "speakers_found",
            "diarized", "needs_speakers", "candidates", "sidecar", "path", "estimate_s", "seconds", "detail")
    out = {k: p.get(k) for k in keys}
    out["path"] = as_posix(p["vtt"]) if p.get("vtt") else None
    if p.get("state") == "file":
        return {"state": "file", "path": out["path"], **{k: None for k in keys if k not in ("state", "path")}}
    return out


# --- asr.json and diar-<d8>.json ---------------------------------------------------------------------------

def _num(d: dict, key: str, where: str) -> None:
    v = d.get(key)
    if v is None:
        d.pop(key, None)                 # null = absent: the neighbours fill it in
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{where}.{key} is not a number")
    v = float(v)
    if not math.isfinite(v):             # json accepts NaN/Infinity, which would break the timestamps
        raise ValueError(f"{where}.{key} is not a finite number")
    d[key] = v


def _text(d: dict, key: str, where: str, empty_ok: bool) -> None:
    v = d.get(key)
    if v is None:
        if empty_ok:
            d[key] = ""
        else:
            d.pop(key, None)
    elif not isinstance(v, str):
        raise ValueError(f"{where}.{key} is not text")


def load_asr(path: str | os.PathLike) -> dict:
    """Load, check and normalize ``asr.json`` (the notebook's carregar_bruto): what passes never breaks the
    rendering. start/end numbers (null = absent), text/word strings, words a list of objects. Raises
    TranscriptError."""
    try:
        doc = read_json(path)
        if not isinstance(doc, dict):
            raise ValueError("the root is not an object")
        if doc.get("complete") is not True:
            raise ValueError("incomplete (the transcription stopped before the end)")
        segs = doc.get("segments")
        if not isinstance(segs, list) or not all(isinstance(s, dict) for s in segs):
            raise ValueError("'segments' is not a list of objects")
        for i, s in enumerate(segs):
            where = f"segments[{i}]"
            _num(s, "start", where)
            _num(s, "end", where)
            _text(s, "text", where, empty_ok=True)
            words = s.get("words")
            if words is None:
                s["words"] = []
                continue
            if not isinstance(words, list) or not all(isinstance(w, dict) for w in words):
                raise ValueError(f"{where}.words is not a list of objects")
            for j, w in enumerate(words):
                _num(w, "start", f"{where}.words[{j}]")
                _num(w, "end", f"{where}.words[{j}]")
                _text(w, "word", f"{where}.words[{j}]", empty_ok=True)
                w.pop("speaker", None)            # the speakers come from diar-<d8>.json
        lang = doc.get("language")
        if lang is not None and not isinstance(lang, str):
            raise ValueError("'language' is not text")
        return doc
    except (OSError, ValueError, TypeError) as exc:
        raise TranscriptError(f"transcribe.py left no valid {as_posix(path)} ({exc})") from None


def load_diar(path: str | os.PathLike) -> dict:
    """``diar-<d8>.json``: ``turns`` = [[start, end, speaker], ...] sorted by start. Raises TranscriptError."""
    try:
        doc = read_json(path)
        if not isinstance(doc, dict) or doc.get("complete") is not True:
            raise ValueError("incomplete")
        turns = []
        for t in doc.get("turns") or []:
            s, e, spk = float(t[0]), float(t[1]), str(t[2])
            if not (math.isfinite(s) and math.isfinite(e)) or e < s:
                raise ValueError(f"invalid turn {t!r}")
            turns.append([s, e, spk])
        turns.sort(key=lambda t: (t[0], t[1]))
        doc["turns"] = turns
        return doc
    except (OSError, ValueError, TypeError, IndexError) as exc:
        raise TranscriptError(f"transcribe.py left no valid {as_posix(path)} ({exc})") from None


# --- speakers and cues ------------------------------------------------------------------------------------

class _Turns:
    """Speaker turns, for the speaker of a time interval: the turn of largest overlap; without overlap, the
    nearest one within ``near_s``."""

    def __init__(self, turns: list[list], near_s: float = 1.0):
        self.turns = turns
        self.starts = [t[0] for t in turns]
        self.longest = max((t[1] - t[0] for t in turns), default=0.0)
        self.near_s = near_s

    def who(self, start: float, end: float) -> str | None:
        if not self.turns:
            return None
        lo = bisect.bisect_left(self.starts, start - self.longest - self.near_s)
        hi = bisect.bisect_right(self.starts, end + self.near_s)
        best, best_ov, near, near_gap = None, 0.0, None, self.near_s
        for t in self.turns[lo:hi]:
            ov = min(end, t[1]) - max(start, t[0])
            if ov > best_ov:
                best, best_ov = t[2], ov
            elif ov <= 0:
                gap = max(t[0] - end, start - t[1])
                if gap < near_gap:
                    near, near_gap = t[2], gap
        return best if best is not None else near


def _timed_words(seg: dict) -> list[dict]:
    """The segment's words with times: a word without them (digits, symbols the aligner does not cover)
    takes them from the word before (the notebook's dividir_por_falante)."""
    out = []
    last = seg.get("start", 0.0)
    for w in seg.get("words") or []:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        st = w.get("start", last)
        en = w.get("end", st)
        if en < st:
            en = st
        last = en
        out.append({"word": text, "start": st, "end": en, "timed": "start" in w})
    return out


def build_cues(asr: dict, turns: list[list] | None, mode: str, params: dict | None = None) -> list[dict]:
    """The cues of the VTT: [{start, end, speaker, text}]. A new cue at each segment (whisperx's alignment
    splits them at sentences), at a speaker change, at a pause of ``cue_pause_s`` and before passing
    ``cue_max_s`` or ``cue_max_chars``. ``mode``: ``pyannote`` (``turns`` give the speakers; one not found is
    ``FALANTE_?``), ``single`` (SPEAKER_00) or ``none`` (no label)."""
    p = {**TRANSCRIBE_DEFAULTS, **(params or {})}
    max_s, max_chars, pause = float(p["cue_max_s"]), int(p["cue_max_chars"]), float(p["cue_pause_s"])
    sep = "" if (asr.get("language") or "") in NO_SPACE_LANGS else " "
    index = _Turns(turns or []) if mode == "pyannote" else None
    cues: list[dict] = []

    def label(spk: str | None) -> str | None:
        if mode == "single":
            return "SPEAKER_00"
        if mode == "pyannote":
            return spk or "FALANTE_?"
        return None

    for seg in asr.get("segments") or []:
        words = _timed_words(seg)
        if not words:
            text = (seg.get("text") or "").strip()
            if text and "start" in seg:
                st = seg["start"]
                en = max(seg.get("end", st), st)
                cues.append({"start": st, "end": en, "speaker": label(index.who(st, en) if index else None),
                             "text": text})
            continue
        prev_spk = None
        if index:
            for w in words:              # untimed words keep the speaker of the word before
                spk = index.who(w["start"], w["end"]) if w["timed"] else None
                w["speaker"] = spk or prev_spk
                prev_spk = w["speaker"]
            first = next((w["speaker"] for w in words if w["speaker"]), None)
            for w in words:
                if w["speaker"] is None:
                    w["speaker"] = first
        cur: dict | None = None
        for w in words:
            spk = w.get("speaker")
            if cur is not None:
                too_long = w["end"] - cur["start"] > max_s or len(cur["text"]) + len(sep) + len(w["word"]) > max_chars
                if spk != cur["spk"] or w["start"] - cur["end"] >= pause or too_long:
                    cues.append({"start": cur["start"], "end": cur["end"], "speaker": label(cur["spk"]),
                                 "text": cur["text"]})
                    cur = None
            if cur is None:
                cur = {"start": w["start"], "end": w["end"], "spk": spk, "text": w["word"]}
            else:
                cur["end"] = max(cur["end"], w["end"])
                cur["text"] += sep + w["word"]
        if cur is not None:
            cues.append({"start": cur["start"], "end": cur["end"], "speaker": label(cur["spk"]), "text": cur["text"]})
    return cues


# --- the VTT ---------------------------------------------------------------------------------------------

def _ts(t: float) -> str:
    ms = int(round(max(0.0, float(t)) * 1000.0))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("-->", "->")).strip()


def note_lines(meta: dict) -> list[str]:
    """The NOTE block: a sentence for whoever opens the file, and the line this module reads back."""
    spk, found, mode = meta.get("speakers"), meta.get("speakers_found"), meta.get("mode")
    asked = bool(spk) and spk != "auto"
    if mode == "single":
        who = "1 falante"
    elif mode == "none":
        who = "falantes não separados (sem token do Hugging Face)"
    elif not meta.get("diarized"):
        who = "falantes não separados"
    elif found:
        who = (f"{found} falante{'s' if found != 1 else ''} "
               + (f"(pedido: {spk})" if asked else "(número detectado automaticamente)"))
    else:
        who = f"falantes: {spk}" if asked else "falantes detectados automaticamente"
    human = (f"Legenda gerada automaticamente pela skill video-analysis (WhisperX {meta.get('whisperx')}, "
             f"{meta.get('model')}, idioma {meta.get('language')}, {who}). Se você editar este arquivo, a skill "
             "não o substitui mais.")
    return ["NOTE", human, _note_line(meta)]


def _note_line(meta: dict) -> str:
    return NOTE_KEY + " " + json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _content_sha1(text: str, meta: dict) -> str | None:
    """SHA-1 of the subtitles with their machine-readable NOTE line written from ``meta`` without
    ``content_sha1``: any edit, to a cue, a header, the sentence or a setting of the NOTE, changes it. None
    without that line."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith(NOTE_KEY):
            lines[i] = _note_line({k: v for k, v in meta.items() if k != CONTENT_KEY})
            return _sha1("\n".join(lines).encode("utf-8"))
    return None


def render_vtt(cues: list[dict], language: str | None, meta: dict) -> str:
    """WEBVTT with a ``Language:`` header (the parser takes the language from it), the NOTE and the cues
    (``SPEAKER_xx: text``, the WhisperX style the parser already reads). The NOTE carries the file's own
    ``content_sha1``, which tells later runs, even after --gc, that nobody edited it (``own_sidecar``)."""
    meta = {k: v for k, v in meta.items() if k != CONTENT_KEY}
    text = _render(cues, language, meta)
    return _render(cues, language, {**meta, CONTENT_KEY: _sha1(text.encode("utf-8"))})


def _render(cues: list[dict], language: str | None, meta: dict) -> str:
    head = ["WEBVTT"]
    if language and re.fullmatch(r"[A-Za-z]{2,3}", language):
        head.append(f"Language: {language.lower()}")
    blocks = ["\n".join(head), "\n".join(note_lines(meta))]
    min_s = float(TRANSCRIBE_DEFAULTS["cue_min_s"])
    for c in cues:
        text = _clean(c.get("text") or "")
        if not text:
            continue
        st = float(c["start"])
        en = max(float(c["end"]), st + min_s)
        spk = c.get("speaker")
        blocks.append(f"{_ts(st)} --> {_ts(en)}\n{spk + ': ' if spk else ''}{text}")
    return "\n\n".join(blocks) + "\n"


def read_note(path: str | os.PathLike) -> dict | None:
    """The machine-readable NOTE line of subtitles this module wrote; None for any other file."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16384).decode("utf-8-sig", errors="replace")
    except OSError:
        return None
    for line in head.replace("\r\n", "\n").split("\n"):
        if line.startswith(NOTE_KEY):
            try:
                doc = json.loads(line[len(NOTE_KEY):].strip())
            except ValueError:
                return None
            return doc if isinstance(doc, dict) and doc.get("version") else None
    return None


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sidecar_path(video: Any) -> Path:
    v = Path(str(video))
    return v.with_name(v.stem + ".vtt")


def own_sidecar(path: Any, video_dir: str | os.PathLike) -> dict | None:
    """The NOTE of ``path`` when this module wrote it and nobody edited it since; None otherwise. The NOTE's
    ``content_sha1`` decides, so ownership survives --gc (which removes transcript-export.json with the
    video's cache folder); subtitles written before that field have only transcript-export.json's SHA-1."""
    meta = read_note(path)
    if meta is None:
        return None
    try:
        data = Path(str(path)).read_bytes()
    except OSError:
        return None
    if meta.get(CONTENT_KEY):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return meta if _content_sha1(text, meta) == meta[CONTENT_KEY] else None
    try:
        reg = read_json(Path(video_dir) / EXPORT_JSON)
    except (OSError, ValueError):
        return None
    if not isinstance(reg, dict):
        return None
    same = nfc_casefold(os.path.normpath(str(reg.get("path") or ""))) == nfc_casefold(
        os.path.normpath(os.path.abspath(str(path))))
    return meta if same and reg.get("sha1") == _sha1(data) else None


def export_sidecar(text: str, video: Any, video_dir: str | os.PathLike, replace: bool = False) -> tuple[Path | None, str | None]:
    """Write ``<stem>.vtt`` next to the video: created only when absent, and whole (``atomic_create_bytes``: a
    full disk or Ctrl+C leave no cut file, which later runs would take for the person's and use instead of the
    cache's); with ``replace``, a file this module wrote and nobody edited is replaced. Returns (path or None,
    warning or None); a folder that refuses the write (read-only, synced, no permission, full) is a warning,
    never an error."""
    path = sidecar_path(video)
    data = text.encode("utf-8")
    try:
        if replace and path.exists():
            if own_sidecar(path, video_dir) is None:
                return None, f"{path.name} changed since the skill wrote it: kept as it is"
            atomic_write_bytes(path, data)
        else:
            atomic_create_bytes(path, data)
    except FileExistsError:
        return None, f"{path.name} appeared next to the video meanwhile: kept as it is"
    except OSError as exc:
        return None, f"could not write the subtitles next to the video ({type(exc).__name__}: {exc}); they stay in the cache"
    write_json(Path(video_dir) / EXPORT_JSON, {"path": as_posix(os.path.abspath(path)), "sha1": _sha1(data),
                                               "written": cache.iso_now()})
    return path, None


# --- running it ------------------------------------------------------------------------------------------

def driver_env(nltk_data: str | os.PathLike | None) -> dict:
    """The environment of transcribe.py: UTF-8, unbuffered, nothing of this Python leaking into the other one
    (PYTHONPATH), matplotlib without the notebook's backend, the NLTK data the setup downloaded, ffmpeg (the
    skill's: ffmpeg_io.find_tool, so VA_FFMPEG, the PATH or, on macOS, Homebrew's folder off PATH) first on
    PATH, and a CUDA JIT cache big enough to keep."""
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(k, None)
    env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", MPLBACKEND="Agg", HF_HUB_DISABLE_TELEMETRY="1",
               TOKENIZERS_PARALLELISM="false", PYTHONWARNINGS="ignore")
    env.setdefault("CUDA_CACHE_MAXSIZE", "4294967296")
    if nltk_data:
        env["NLTK_DATA"] = str(nltk_data)
    from .ffmpeg_io import FFmpegNotFound, find_tool   # lazy: numpy comes with it
    try:   # the skill's own ffmpeg, also Homebrew's off PATH: whisperx.load_audio runs a bare "ffmpeg"
        ff = find_tool("ffmpeg")
    except FFmpegNotFound:
        ff = os.environ.get("VA_FFMPEG", "").strip().strip('"') or shutil.which("ffmpeg") or ""
    if ff:
        folder = os.path.dirname(os.path.abspath(ff))
        env["PATH"] = folder + os.pathsep + env.get("PATH", "")
    return env


def _tail(path: Path, lines: int = 2) -> str:
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return "no log"
    last = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(last[-lines:])[-500:] or "empty log"


def _write_progress(path: str | None, vid: str, info: dict) -> None:
    if not path:
        return
    try:
        write_json(path, {"vid": vid, "stage": STAGE, "state": "processing", "updated": cache.iso_now(), "info": info})
    except (OSError, ValueError):
        pass


def make(*, p: dict, python: str | os.PathLike, script: str | os.PathLike, video: Any, video_dir: str | os.PathLike,
         duration_s: float, has_audio: bool = True, nltk_data: str | os.PathLike | None = None, vid: str = "v1",
         progress_path: str | None = None, log=None) -> dict:
    """Transcribe one video by its plan ``p`` (``plan``: pending or cached), render the VTT and write it next
    to the video. Returns the plan updated with ``vtt`` (the subtitles to use: the ones next to the video when
    written, else the cache's), ``cache_vtt``, ``sidecar``, ``language``, ``language_probability``,
    ``speakers_found``, ``diarized``, ``reason``, ``seconds`` and ``warnings``. Raises TranscriptError."""
    say = log or (lambda m: None)
    vdir, sdir = Path(video_dir), Path(p["sdir"])
    t0 = time.perf_counter()
    warnings: list[str] = []
    sdir.mkdir(parents=True, exist_ok=True)
    with cache.VideoLock(vdir / LOCK_NAME, wait_s=TRANSCRIBE_DEFAULTS["lock_wait_s"], on_wait=say):
        cache.touch_last_used(vdir)     # a use of the video: --gc keeps its folder, checkpoints included
        diar_path = sdir / f"diar-{p['dkey']}.json"
        need_asr = asr_done(sdir, p["akey"]) is None
        need_diar = (p["mode"] == "pyannote"
                     and diar_done(sdir, p["dkey"], retry_failed=bool(p.get("speakers_given"))) is None)
        if not has_audio:
            asr = {"complete": True, "language": None if p["language"] == "auto" else p["language"],
                   "segments": [], "has_speech": False}
            warnings.append("the video has no audio track: empty subtitles")
            need_asr = need_diar = False
        elif need_asr or need_diar:
            cmd = [str(python), "-B", str(script), str(video), "--work", str(sdir), "--asr-out", str(sdir / "asr.json"),
                   "--model", p["model"], "--language", p["language"]]
            if need_diar:
                lo, hi = speakers_bounds(p["speakers"])
                cmd += ["--diar-out", str(diar_path), "--diar-key", p["dkey"], "--speakers", p["speakers"]]
                if lo is not None:
                    cmd += ["--min-speakers", str(lo)]
                if hi is not None:
                    cmd += ["--max-speakers", str(hi)]
            if progress_path:
                cmd += ["--progress", str(progress_path), "--vid", vid]
            cmd.append("--watch-stdin")
            _write_progress(progress_path, vid, {"phase": "asr" if need_asr else "diarize", "done_s": 0.0,
                                                 "total_s": round(float(duration_s or 0.0), 1)})
            log_path = sdir / "transcribe.log"
            code = cache.stage_fn("run_transcribe")(cmd, str(log_path), driver_env(nltk_data))
            if code != 0:
                why = DRIVER_EXIT.get(code, f"transcribe.py exited with {code}")
                raise TranscriptError(f"{why}: {_tail(log_path)}")
            asr = load_asr(sdir / "asr.json")
        else:
            asr = load_asr(sdir / "asr.json")
        if has_audio and need_asr:
            cache.write_stage_json(sdir, STAGE, p["akey"], model=asr.get("model") or p["model"],
                                   language=asr.get("language"), language_probability=asr.get("language_probability"),
                                   language_requested=p["language"], device=asr.get("device"),
                                   compute_type=asr.get("compute_type"), aligned=asr.get("aligned"),
                                   whisperx=asr.get("whisperx"), timing_s=asr.get("timing_s"),
                                   inputs={"video": as_posix(video)})
        turns, diarized, reason, found = None, p["mode"] == "single", None, None
        if p["mode"] == "pyannote" and has_audio:
            d = load_diar(diar_path)
            diarized, reason = bool(d.get("diarized")), d.get("reason")
            turns = d["turns"] if diarized else None
            found = len({t[2] for t in d["turns"]}) if diarized else None
            if not diarized:
                warnings.append(_diar_warning(reason))
        elif p["mode"] == "none":
            reason = "no_token"
            warnings.append(_diar_warning(reason))
        elif p["mode"] == "single":
            found = 1
        mode = p["mode"] if (p["mode"] != "pyannote" or diarized) else "none"
        language = asr.get("language") or (None if p["language"] == "auto" else p["language"])
        prob = asr.get("language_probability")
        if (p["language"] == "auto" and isinstance(prob, (int, float))
                and prob < TRANSCRIBE_DEFAULTS["low_language_probability"]):
            warnings.append(f"language detected with low confidence ({language}, {prob:.2f}): "
                            "if it is wrong, give audio_language")
        for w in asr.get("warnings") or []:
            if isinstance(w, str):
                warnings.append(w)
        cues = build_cues(asr, turns, mode)
        meta = {"version": TRANSCRIBE_VERSION, "render": TRANSCRIBE_DEFAULTS["render_version"], "asr": p["akey"],
                "diar": p["dkey"], "whisperx": TRANSCRIBE_DEFAULTS["whisperx_version"], "model": p["model"],
                "language": language, "language_requested": p["language"], "speakers": p["speakers"],
                "speakers_found": found, "mode": p["mode"], "diarized": bool(diarized), "reason": reason}
        text = render_vtt(cues, language, meta)
        cache_vtt = sdir / f"{p['dkey']}-{TRANSCRIBE_DEFAULTS['render_version']}.vtt"
        try:
            same = cache_vtt.read_bytes() == text.encode("utf-8")
        except OSError:
            same = False
        if not same:
            atomic_write_bytes(cache_vtt, text.encode("utf-8"))
    side, warn = export_sidecar(text, video, vdir, replace=bool(p.get("replace")))
    if warn:
        warnings.append(warn)
    out = dict(p)
    out.update(state="made" if (need_asr or need_diar) else "cached", vtt=side or cache_vtt, cache_vtt=cache_vtt,
               sidecar=as_posix(side) if side else None, language=language, language_probability=prob,
               speakers_found=found, diarized=bool(diarized), reason=reason, mode=mode,
               seconds=round(time.perf_counter() - t0, 1), warnings=warnings, n_cues=len(cues))
    return out


def _diar_warning(reason: str | None) -> str:
    if reason == "no_token":
        return ("speakers not separated: no Hugging Face token (run /video-analysis setup, step 7; then call again "
                "with num_speakers: the transcript is reused)")
    if reason == "no_access":
        return ("speakers not separated: the token has no access to pyannote/speaker-diarization-community-1 "
                "(accept its conditions on huggingface.co, then call again with num_speakers)")
    return (f"speakers not separated: the diarization failed ({reason or 'no detail'}; once its cause is fixed, "
            "call again with num_speakers: only the speakers are redone)")
