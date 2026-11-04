"""Cues stage: WebVTT parsing, normalization, merges and repairs (spec section 4, WP2).

Entry points
- ``effective_cue_params(overrides=None) -> dict``: CUES_DEFAULTS + overrides, validated and
  normalized (JSON-serializable, stable types), so the dict can be hashed into the cache key.
- ``parse_vtt_bytes(data, params=None, *, duration_s=None, path=None) -> dict``: the cues.json
  document, without touching the filesystem.
- ``run_cues_stage(vtt_path, params, out_dir, duration_s) -> dict``: reads the .vtt, writes
  ``out_dir/cues.json`` and returns the document. ``stage.json`` belongs to the cache module.

Pipeline (spec section 4, in this order): read (utf-8-sig, cp1252 fallback, newlines, NFC) ->
blocks (NOTE/STYLE/REGION skipped, cue ids, a second timing line splits a block) -> payload
(``<v Name>`` voices and ``SPEAKER_xx:`` / ``FALANTE_?:`` prefixes, tags stripped, entities
decoded, invisible characters removed) -> times (end < start, offset, clip to [0, duration]) ->
stable sort -> merges (same speaker only: identical text, rolling growth, 2-line scroll) ->
minimum duration (after the merges, so a run of 20 ms stutter cues keeps its real end) ->
repairs (long cues split, too-fast cues extended; both ``approx: true``) -> ids, speaker tags,
language, style, stats.

Times are integer milliseconds internally; cues.json stores seconds with 3 decimals. The output
is deterministic: the same bytes and params always give byte-identical cues.json.
"""
from __future__ import annotations

import bisect
import codecs
import copy
import hashlib
import html
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import CUES_DEFAULTS, NAMES, PARSER_VERSION, SCHEMA_IDS
from .common import as_posix, cue_id, fmt_hms, ms_to_s, nfc_casefold, parse_time_ms, s_to_ms, write_json

__all__ = [
    "detect_language",
    "effective_cue_params",
    "parse_speaker_names",
    "parse_vtt_bytes",
    "run_cues_stage",
]

# --- regular expressions (spec section 4) ----------------------------------------------------

# Hours are optional and per cue; the fraction is padded to the right ("5" = 500 ms) and may use
# ",". The spec requires a fraction; it is optional here so "00:01:02 --> 00:01:04" still parses.
_TIME = r"(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?"
_TIMING_RE = re.compile(rf"^\s*({_TIME})\s*-->\s*({_TIME})(.*)$")
_VOICE_RE = re.compile(r"<v(?:\.[\w.-]+)?\s+([^>]+)>")
_PREFIX_RE = re.compile(r"^\s*((?:SPEAKER|FALANTE)_(?:\d{1,3}|\?))\s*:\s*")
_TAG_RE = re.compile(r"<[^>]+>")
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")
_UTT_RE = re.compile(r"/(\d+)-\d+\s*$")
_LANG_HDR_RE = re.compile(r"^\s*Language\s*:\s*([A-Za-z]{2,3})(?:[-_][A-Za-z0-9-]*)?\s*$", re.IGNORECASE)
_META_BLOCK_RE = re.compile(r"^(?:NOTE|STYLE|REGION)(?:\s|$)")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")
_WORD_RE = re.compile(r"\w+")
_LETTERS_RE = re.compile(r"[^\W\d_]+")

_CUE_ALIASES = {
    "vtt_offset": "offset_s",
    "vtt_offset_s": "offset_s",
    "offset": "offset_s",
    "speakers": "speaker_names",
    "falantes": "speaker_names",
}
_FLOAT_KEYS = ("offset_s", "min_duration_s", "end_before_start_s", "merge_gap_s", "long_min_s",
               "split_chunk_min_s", "split_chunk_max_s", "fast_max_cps", "fast_target_cps",
               "warn_coverage_below")
_BOOL_KEYS = ("merge_identical", "merge_rolling", "merge_scroll")
_WARN_EXAMPLES = 3


# --- parameters ------------------------------------------------------------------------------

def parse_speaker_names(value: Any) -> dict[str, str]:
    """``--speaker-names`` / the request's ``falantes`` -> {original name or tag: display name}.

    Accepts a dict, a list of "A=B" strings, or one string "SPEAKER_01=Ana;SPEAKER_03=Bruno"
    (";" or newlines between entries). Keys are matched case-insensitively against the name
    in the subtitle ("SPEAKER_01", "FALANTE_?", a Teams name) or the tag ("S1"). The result is
    sorted by key so equal mappings hash to the same cache key.
    """
    if value is None or value == "":
        return {}
    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        pairs = [(str(k), str(v)) for k, v in value.items()]
    else:
        items = re.split(r"[;\n]", value) if isinstance(value, str) else list(value)
        for item in items:
            item = str(item).strip()
            if not item:
                continue
            if "=" not in item:
                raise ValueError(f"speaker_names: expected NAME=Display, got {item!r}")
            k, v = item.split("=", 1)
            pairs.append((k, v))
    out: dict[str, str] = {}
    for k, v in pairs:
        k, v = " ".join(k.split()), " ".join(v.split())
        if not k or not v:
            raise ValueError(f"speaker_names: empty name in {k!r}={v!r}")
        out[k] = v
    return {k: out[k] for k in sorted(out, key=lambda s: (nfc_casefold(s), s))}


def effective_cue_params(overrides: dict | None = None) -> dict:
    """CUES_DEFAULTS updated with ``overrides`` (None values are ignored), validated.

    Aliases: vtt_offset/vtt_offset_s/offset -> offset_s; speakers/falantes -> speaker_names.
    Unknown keys raise ValueError. Types are normalized to the defaults' types (floats stay
    floats, offset rounded to the millisecond), so the result is stable for hashing.
    Idempotent: effective_cue_params(effective_cue_params(x)) == effective_cue_params(x).
    """
    p = copy.deepcopy(CUES_DEFAULTS)
    for key, value in (overrides or {}).items():
        name = _CUE_ALIASES.get(key, key)
        if name not in p:
            raise ValueError(f"unknown cue parameter {key!r}; valid: {', '.join(sorted(p))}")
        if value is None:
            continue
        p[name] = copy.deepcopy(value)
    for key in _FLOAT_KEYS:
        value = p[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"cue parameter {key!r} must be a finite number, got {value!r}")
        p[key] = float(value)
    p["offset_s"] = ms_to_s(s_to_ms(p["offset_s"]))
    for key in _BOOL_KEYS:
        if not isinstance(p[key], bool):
            raise ValueError(f"cue parameter {key!r} must be true or false, got {p[key]!r}")
    if isinstance(p["long_min_chars"], bool) or not isinstance(p["long_min_chars"], int) or p["long_min_chars"] < 0:
        raise ValueError(f"cue parameter 'long_min_chars' must be an int >= 0, got {p['long_min_chars']!r}")
    for key in ("min_duration_s", "end_before_start_s", "long_min_s", "split_chunk_min_s", "fast_max_cps", "fast_target_cps"):
        if p[key] <= 0:
            raise ValueError(f"cue parameter {key!r} must be > 0, got {p[key]!r}")
    if p["merge_gap_s"] < 0:
        raise ValueError("cue parameter 'merge_gap_s' must be >= 0")
    if p["split_chunk_max_s"] < p["split_chunk_min_s"]:
        raise ValueError("cue parameter 'split_chunk_max_s' must be >= split_chunk_min_s")
    if not 0.0 <= p["warn_coverage_below"] <= 1.0:
        raise ValueError("cue parameter 'warn_coverage_below' must be in [0, 1]")
    try:
        codecs.lookup(str(p["fallback_encoding"]))
    except LookupError as exc:
        raise ValueError(f"cue parameter 'fallback_encoding': unknown codec {p['fallback_encoding']!r}") from exc
    p["fallback_encoding"] = str(p["fallback_encoding"])
    p["speaker_names"] = parse_speaker_names(p["speaker_names"])
    return p


# --- language --------------------------------------------------------------------------------

# Distinctive function words: words shared by two of the languages ("que", "de", "no", "a",
# "para", "por", "como", "está"...) are left out so pt and es separate well.
_STOPWORDS = {
    "pt": frozenset("""não você vocês então isso isto aqui também tá pra uma da das na nas ao aos é com ele ela
        eles elas eu vai tem esse essa esses essas já só até agora assim foi são muito mais quando onde seu sua
        nós fazer aí né vou ou meu minha lá coisa depois ainda sim bom em e pelo pela num numa dessa desse nesse
        nessa aquele aquela você vamos estou estamos tô pode podemos nosso nossa dele dela""".split()),
    "es": frozenset("""el la los las y es en una un pero más eso esto muy también cuando donde ya solo hasta
        ahora así hay yo usted ustedes nosotros pues bueno entonces aquí tiene hacer puede del al con lo le les
        su sus mi fue son todo sí qué cómo estoy estamos tenemos vez ella ellos nuestro nuestra""".split()),
    "en": frozenset("""the and is are to of that it you this for with on we have be not so what can will just
        there they but was if or at from my your i an in do as all about would like know think going get here
        yes yeah okay right now how which our been has had were their them then than when where who why""".split()),
}


def detect_language(texts: list[str], header: str | None = None) -> str:
    """"pt" | "en" | "es" | "und": the header's primary subtag when present ("pt-BR" -> "pt"),
    else the language whose distinctive stopwords are most frequent (at least 2 hits and 1.5x
    the runner-up)."""
    if header:
        return header.lower()
    counts = {lang: 0 for lang in _STOPWORDS}
    for text in texts:
        for tok in _LETTERS_RE.findall(nfc_casefold(text)):
            for lang, words in _STOPWORDS.items():
                if tok in words:
                    counts[lang] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    (best, n1), (_second, n2) = ranked[0], ranked[1]
    if n1 >= 2 and n1 >= 1.5 * n2:
        return best
    return "und"


# --- internal cue ------------------------------------------------------------------------------

@dataclass
class _Cue:
    start: int                 # ms
    end: int                   # ms
    spk: str | None            # speaker name as written in the file
    kind: str | None           # "voice" | "prefix" | None (where the speaker came from)
    lines: list[str]           # cleaned payload lines (kept for the 2-line scroll merge)
    src: list[int]             # 1-based timing-line indexes
    utt: int | None            # Teams utterance number
    order: tuple[int, int, int]  # stable sort key: (source index, voice part, split chunk)
    approx: bool = False

    @property
    def text(self) -> str:
        return " ".join(self.lines)


class _Warnings:
    """Collects warnings by kind (count + first examples) and renders them once, in English."""

    def __init__(self) -> None:
        self.flags: list[str] = []
        self.counts: dict[str, int] = {}
        self.examples: dict[str, list[str]] = {}

    def flag(self, text: str) -> None:
        if text not in self.flags:
            self.flags.append(text)

    def count(self, kind: str, example: str | None = None, n: int = 1) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + n
        if example is not None:
            ex = self.examples.setdefault(kind, [])
            if len(ex) < _WARN_EXAMPLES:
                ex.append(example)

    def ex(self, kind: str) -> str:
        items = self.examples.get(kind) or []
        return f" (e.g. {', '.join(items)})" if items else ""


# --- read and blocks ---------------------------------------------------------------------------

def _decode(data: bytes, fallback: str, w: _Warnings) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode(fallback, errors="replace")
        if text.startswith("ï»¿"):      # a UTF-8 BOM seen through cp1252
            text = text[3:]
        w.flag(f"subtitles: not valid UTF-8; read as {fallback}")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)


def _split_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if line.strip():
            cur.append(line)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _parse_blocks(text: str, w: _Warnings) -> tuple[str | None, list[tuple], int]:
    """-> (header language subtag, entries, raw timing-line count).
    entry = (src index, id line or None, start text, end text, payload lines)."""
    blocks = _split_blocks(text)
    header_lang = None
    entries: list[tuple] = []
    raw = 0
    has_header = bool(blocks) and blocks[0][0].startswith("WEBVTT")
    if not has_header:
        w.flag("subtitles: missing the WEBVTT header")
    for b, block in enumerate(blocks):
        timing = [i for i, line in enumerate(block) if _TIMING_RE.match(line)]
        id_allowed = True
        if b == 0 and has_header:
            for line in block[1:timing[0] if timing else len(block)]:
                m = _LANG_HDR_RE.match(line)
                if m and header_lang is None:
                    header_lang = m.group(1).lower()
            id_allowed = False
        elif _META_BLOCK_RE.match(block[0].strip()):
            id_allowed = False
        elif not timing:
            w.count("junk_block")
        if not timing:
            continue
        for k, ti in enumerate(timing):
            raw += 1
            m = _TIMING_RE.match(block[ti])
            id_line = None
            if k == 0 and id_allowed and ti > 0:
                id_line = block[ti - 1].strip()
            stop = timing[k + 1] if k + 1 < len(timing) else len(block)
            entries.append((raw, id_line, m.group(1), m.group(2), block[ti + 1:stop]))
    return header_lang, entries, raw


# --- payload -------------------------------------------------------------------------------------

def _clean_line(line: str) -> str:
    line = _TAG_RE.sub("", line)
    line = html.unescape(line)
    line = _INVISIBLE_RE.sub("", line).replace("\u00a0", " ")
    return " ".join(unicodedata.normalize("NFC", line).split())


def _clean_lines(text: str) -> list[str]:
    return [c for c in (_clean_line(line) for line in text.split("\n")) if c]


def _segments(payload: list[str]) -> list[tuple[str | None, str | None, list[str]]]:
    """Split a payload into (speaker, kind, cleaned lines) parts: one per ``<v Name>`` voice,
    or one per line starting with a ``SPEAKER_xx:`` / ``FALANTE_?:`` prefix. Text before the
    first voice or prefix is a part without speaker."""
    joined = "\n".join(payload)
    voices = list(_VOICE_RE.finditer(joined))
    parts: list[tuple[str | None, str | None, list[str]]] = []
    if voices:
        head = _clean_lines(joined[:voices[0].start()])
        if head:
            parts.append((None, None, head))
        for k, m in enumerate(voices):
            stop = voices[k + 1].start() if k + 1 < len(voices) else len(joined)
            name = _clean_line(m.group(1)) or None
            parts.append((name, "voice" if name else None, _clean_lines(joined[m.end():stop])))
        return parts
    spk: str | None = None
    kind: str | None = None
    lines: list[str] = []
    started = False
    for line in payload:
        bare = _TAG_RE.sub("", line)
        m = _PREFIX_RE.match(bare)
        if m:
            if started:
                parts.append((spk, kind, lines))
            spk, kind, lines, started = m.group(1), "prefix", [], True
            rest = _clean_line(bare[m.end():])
        else:
            started = True
            rest = _clean_line(line)
        if rest:
            lines.append(rest)
    if started:
        parts.append((spk, kind, lines))
    return parts


# --- merges ----------------------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(nfc_casefold(text))


def _merge_kind(p: _Cue, c: _Cue, params: dict) -> str | None:
    tp, tc = _tokens(p.text), _tokens(c.text)
    if params["merge_identical"] and (tp == tc if (tp or tc) else p.text == c.text):
        return "dupe"
    if params["merge_rolling"] and tp and len(tc) > len(tp) and tc[:len(tp)] == tp:
        return "rolling"
    if params["merge_scroll"] and len(p.lines) >= 2 and c.lines:
        last = _tokens(p.lines[-1])
        if last and last == _tokens(c.lines[0]):
            return "scroll"
    return None


def _merge(cues: list[_Cue], params: dict, stats: dict) -> list[_Cue]:
    """Same speaker only, gap <= merge_gap_s or overlapping. Compares each cue with the most
    recent kept cue of the same speaker (another speaker's interjection may sit between)."""
    gap_ms = s_to_ms(params["merge_gap_s"])
    out: list[_Cue] = []
    last_by_spk: dict[str | None, int] = {}
    for c in cues:
        j = last_by_spk.get(c.spk)
        if j is not None:
            p = out[j]
            kind = _merge_kind(p, c, params) if c.start - p.end <= gap_ms else None
            if kind is not None:
                if kind == "dupe":
                    stats["merged_dupes"] += 1
                elif kind == "rolling":
                    p.lines = list(c.lines)
                    stats["merged_rolling"] += 1
                else:
                    p.lines = p.lines + c.lines[1:]
                    stats["merged_rolling"] += 1
                p.start = min(p.start, c.start)
                p.end = max(p.end, c.end)
                p.src = sorted(set(p.src) | set(c.src))
                p.approx = p.approx or c.approx
                if p.utt is None:
                    p.utt = c.utt
                continue
        out.append(c)
        last_by_spk[c.spk] = len(out) - 1
    return out


# --- repairs -----------------------------------------------------------------------------------------

def _split_words(text: str, max_chars: int) -> list[str]:
    """Cut an unpunctuated sentence at word boundaries into ceil(len/max_chars) pieces of
    about equal length (integer arithmetic, deterministic)."""
    words = text.split()
    n = max(1, -(-len(text) // max_chars))
    if n == 1 or len(words) < 2:
        return [text]
    ends: list[int] = []
    pos = 0
    for i, word in enumerate(words):
        pos += len(word) + (1 if i else 0)
        ends.append(pos)
    total = ends[-1]
    cuts: list[int] = []
    last = -1
    for k in range(1, n):
        choices = range(last + 1, len(words) - 1)
        if not choices:
            break
        best = min(choices, key=lambda i: (abs(n * ends[i] - k * total), i))
        cuts.append(best)
        last = best
    pieces: list[str] = []
    begin = 0
    for cut in cuts:
        pieces.append(" ".join(words[begin:cut + 1]))
        begin = cut + 1
    pieces.append(" ".join(words[begin:]))
    return [pc for pc in pieces if pc]


def _pack(pieces: list[str], min_chars: int, max_chars: int) -> list[str]:
    chunks: list[str] = []
    cur = ""
    for pc in pieces:
        if not cur:
            cur = pc
        elif len(cur) + 1 + len(pc) <= max_chars or len(cur) < min_chars:
            cur = f"{cur} {pc}"
        else:
            chunks.append(cur)
            cur = pc
    if cur:
        if chunks and len(cur) < min_chars and 2 * (len(chunks[-1]) + 1 + len(cur)) <= 3 * max_chars:
            chunks[-1] = f"{chunks[-1]} {cur}"
        else:
            chunks.append(cur)
    return chunks


def _split_long(c: _Cue, params: dict) -> list[_Cue] | None:
    """Long cue (> long_min_s and > long_min_chars): sentence chunks of about 6-10 s at the
    cue's own characters per second; times allocated by cumulative characters."""
    dur = c.end - c.start
    text = c.text
    if dur <= s_to_ms(params["long_min_s"]) or len(text) <= params["long_min_chars"]:
        return None
    cps = len(text) * 1000.0 / dur
    max_chars = max(1, int(params["split_chunk_max_s"] * cps))
    min_chars = max(1, int(params["split_chunk_min_s"] * cps))
    pieces: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        sentence = sentence.strip()
        if sentence:
            pieces.extend(_split_words(sentence, max_chars) if len(sentence) > max_chars else [sentence])
    chunks = _pack(pieces, min_chars, max_chars)
    if len(chunks) < 2:
        return None
    total = sum(len(ch) for ch in chunks)
    out: list[_Cue] = []
    acc = 0
    prev = c.start
    for k, chunk in enumerate(chunks):
        acc += len(chunk)
        end = c.end if k == len(chunks) - 1 else c.start + (2 * dur * acc + total) // (2 * total)
        end = min(max(end, prev + 1), c.end - (len(chunks) - 1 - k))
        out.append(_Cue(prev, end, c.spk, c.kind, [chunk], list(c.src), c.utt,
                        (c.order[0], c.order[1], k), True))
        prev = end
    return out


def _sort_key(c: _Cue) -> tuple:
    return (c.start, c.end, c.order)


def _extend_fast(cues: list[_Cue], params: dict, dur_ms: int | None, stats: dict) -> None:
    """Too fast (> fast_max_cps): end = min(next cue start, start + chars/fast_target_cps),
    never shortening a cue and never past the duration."""
    starts = [c.start for c in cues]
    for i, c in enumerate(cues):
        chars = len(c.text)
        dur = c.end - c.start
        if chars * 1000.0 <= params["fast_max_cps"] * dur:
            continue
        k = bisect.bisect_right(starts, c.start, lo=i + 1)
        next_start = starts[k] if k < len(starts) else (dur_ms if dur_ms is not None else None)
        target = c.start + int(math.floor(chars * 1000.0 / params["fast_target_cps"] + 0.5))
        new_end = target if next_start is None else min(target, next_start)
        if dur_ms is not None:
            new_end = min(new_end, dur_ms)
        if new_end > c.end:
            c.end = new_end
            c.approx = True
            stats["extended_fast"] += 1


def _count_overlaps(cues: list[_Cue]) -> int:
    starts = [c.start for c in cues]
    return sum(bisect.bisect_left(starts, c.end, lo=i + 1) - (i + 1) for i, c in enumerate(cues))


# --- main parser --------------------------------------------------------------------------------------

def _speaker_tag_key(name: str) -> bool:
    return name.endswith("_?")


def _style(cues: list[_Cue]) -> str:
    voice = any(c.kind == "voice" for c in cues)
    prefix = any(c.kind == "prefix" for c in cues)
    if voice and prefix:
        return "mixed"
    if voice:
        return "teams"
    if prefix:
        return "whisperx"
    return "plain"


def parse_vtt_bytes(data: bytes, params: dict | None = None, *, duration_s: float | None = None,
                    path: str | os.PathLike | None = None) -> dict:
    """Parse WebVTT bytes into the cues.json document (spec section 6). ``duration_s`` (the
    video duration) clips cues and enables the coverage warnings; ``path`` is recorded as an
    absolute forward-slash path. Pure: no filesystem access."""
    p = effective_cue_params(params)
    w = _Warnings()
    stats = {"raw": 0, "kept": 0, "merged_dupes": 0, "merged_rolling": 0, "split_long": 0,
             "extended_fast": 0, "dropped_empty": 0, "overlaps": 0, "nonmonotonic": 0}
    dur_ms = s_to_ms(duration_s) if isinstance(duration_s, (int, float)) and not isinstance(duration_s, bool) \
        and math.isfinite(duration_s) and duration_s > 0 else None
    offset_ms = s_to_ms(p["offset_s"])
    min_ms = s_to_ms(p["min_duration_s"])
    fix_ms = s_to_ms(p["end_before_start_s"])

    text = _decode(data, p["fallback_encoding"], w)
    header_lang, entries, stats["raw"] = _parse_blocks(text, w)

    cues: list[_Cue] = []
    prev_start: int | None = None
    for src, id_line, s_txt, e_txt, payload in entries:
        try:
            start, end = parse_time_ms(s_txt), parse_time_ms(e_txt)
        except ValueError:
            w.count("bad_time", f"#{src} {s_txt}")
            continue
        if prev_start is not None and start < prev_start:
            stats["nonmonotonic"] += 1
        prev_start = start
        if end < start:
            w.count("end_before_start", f"#{src} {fmt_hms(start / 1000)}")
            end = start + fix_ms
        utt = None
        if id_line:
            m = _UTT_RE.search(id_line)
            utt = int(m.group(1)) if m else None
        parts = _segments(payload)
        if not parts:
            stats["dropped_empty"] += 1
            continue
        for v, (spk, kind, lines) in enumerate(parts):
            if not lines:
                stats["dropped_empty"] += 1
                continue
            s, e = start + offset_ms, end + offset_ms
            if e <= 0:
                w.count("before_start", f"#{src}")
                continue
            s = max(s, 0)
            if dur_ms is not None:
                if s >= dur_ms:
                    w.count("past_end", f"#{src} {fmt_hms(s / 1000)}")
                    continue
                e = min(e, dur_ms)
            cues.append(_Cue(s, e, spk, kind, lines, [src], utt, (src, v, 0)))

    cues.sort(key=_sort_key)
    cues = _merge(cues, p, stats)
    # Minimum duration after the merges: a run of 20-60 ms stutter cues keeps its real end
    # instead of the last one's end + 0.1 s (spec section 4 lists it under "times").
    for c in cues:
        if c.end - c.start < min_ms:
            c.end = c.start + min_ms if dur_ms is None else min(c.start + min_ms, dur_ms)
    repaired: list[_Cue] = []
    for c in cues:
        chunks = _split_long(c, p)
        if chunks:
            stats["split_long"] += 1
            repaired.extend(chunks)
        else:
            repaired.append(c)
    repaired.sort(key=_sort_key)
    _extend_fast(repaired, p, dur_ms, stats)
    repaired.sort(key=_sort_key)
    stats["kept"] = len(repaired)
    stats["overlaps"] = _count_overlaps(repaired)

    # speakers: tags by first appearance; FALANTE_? (any "_?" name) shares the tag "S?"
    names = p["speaker_names"]
    lookup = {nfc_casefold(k): v for k, v in names.items()}
    tags: dict[str, str] = {}
    info: dict[str, dict] = {}
    for c in repaired:
        if c.spk is None or c.spk in tags:
            continue
        tag = "S?" if _speaker_tag_key(c.spk) else f"S{sum(1 for t in tags.values() if t != 'S?') + 1}"
        tags[c.spk] = tag
        if tag not in info:
            info[tag] = {"tag": tag, "name": c.spk, "cues": 0, "talk_ms": 0}
    for c in repaired:
        if c.spk is not None:
            entry = info[tags[c.spk]]
            entry["cues"] += 1
            entry["talk_ms"] += c.end - c.start
    speakers = []
    for entry in info.values():
        display = lookup.get(nfc_casefold(entry["name"])) or lookup.get(nfc_casefold(entry["tag"])) or entry["name"]
        speakers.append({"tag": entry["tag"], "name": display, "cues": entry["cues"],
                         "talk_s": round(entry["talk_ms"] / 1000.0, 1)})

    out_cues = []
    for n, c in enumerate(repaired, 1):
        out_cues.append({"id": cue_id(n), "start": ms_to_s(c.start), "end": ms_to_s(c.end),
                         "spk": tags.get(c.spk) if c.spk is not None else None, "text": c.text,
                         "approx": c.approx, "src": list(c.src), "utt": c.utt})

    warnings = _render_warnings(w, repaired, dur_ms, p)
    vtt_path = as_posix(os.path.abspath(os.fspath(path))) if path is not None else ""
    return {
        "schema": SCHEMA_IDS["cues"],
        "parser_version": PARSER_VERSION,
        "vtt": {"path": vtt_path, "sha1": hashlib.sha1(data).hexdigest(), "style": _style(repaired),
                "language": detect_language([c.text for c in repaired], header_lang)},
        "offset_s": p["offset_s"],
        "stats": stats,
        "speakers": speakers,
        "cues": out_cues,
        "warnings": warnings,
    }


def _render_warnings(w: _Warnings, cues: list[_Cue], dur_ms: int | None, p: dict) -> list[str]:
    out = list(w.flags)
    n = w.counts
    if n.get("bad_time"):
        out.append(f"subtitles: {n['bad_time']} cue(s) with an invalid time (minutes or seconds >= 60) "
                   f"ignored{w.ex('bad_time')}")
    if n.get("end_before_start"):
        out.append(f"subtitles: {n['end_before_start']} cue(s) with end before start; end = start + "
                   f"{p['end_before_start_s']:g} s{w.ex('end_before_start')}")
    if n.get("junk_block"):
        out.append(f"subtitles: {n['junk_block']} block(s) without a timing line ignored")
    if n.get("before_start"):
        out.append(f"subtitles: {n['before_start']} cue(s) before the start of the video dropped "
                   f"(offset {p['offset_s']:g} s)")
    if n.get("past_end") and dur_ms is not None:
        out.append(f"subtitles: {n['past_end']} cue(s) start after the end of the video ({fmt_hms(dur_ms / 1000)}) "
                   f"and were dropped; you may need --vtt-offset{w.ex('past_end')}")
    if not cues:
        out.append("subtitles: no cues in the subtitles")
    elif dur_ms:
        first = min(c.start for c in cues)
        last = max(c.end for c in cues)
        share = (last - first) / dur_ms
        if share < p["warn_coverage_below"]:
            out.append(f"subtitles: the cues cover only {int(share * 100)}% of the video ({fmt_hms(first / 1000, 0)}–"
                       f"{fmt_hms(last / 1000, 0)}); check that it is the right .vtt or use --vtt-offset")
    return out


def run_cues_stage(vtt_path: Path, params: dict, out_dir: Path, duration_s: float) -> dict:
    """Read ``vtt_path``, write ``out_dir/cues.json`` and return the document. ``params`` may be
    partial (completed by effective_cue_params). ``stage.json`` is written by the cache module."""
    vtt_path = Path(vtt_path)
    doc = parse_vtt_bytes(vtt_path.read_bytes(), params, duration_s=duration_s, path=vtt_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / NAMES["cues_json"], doc)
    return doc
