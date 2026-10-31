"""Batch planner (spec section 5, WP2). Pure functions on integer milliseconds.

Deterministic greedy: from the batch start, extend until the next frame would break a limit
(max_frames, distinct cue text, span) -> j_limit; then pick the best boundary candidate in
[i0 + soft_min, j_limit] by score (+3 scene change, +2 no cue spanning the cut, +1 no speech
within +-1 s, +1 previous frame >= 30 s), ties to the later cut, all zero -> j_limit. A short
tail batch is merged into the previous one (total <= tail_merge_max) or both are split evenly.
"""
from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from typing import Any

from . import BATCH_DEFAULTS
from .align import CueIndex
from .common import ms_to_s, parse_time, s_to_ms

__all__ = ["batch_cues", "effective_batch_params", "image_tokens", "plan_batches", "text_tokens"]

_BATCH_ALIASES = {
    "batch_frames": "max_frames",
    "batch_text": "max_text_chars",
    "batch_span": "max_span_s",
    "batch_span_s": "max_span_s",
    "trecho": "range",
}
_INT_KEYS = ("max_frames", "soft_min", "max_text_chars", "score_scene", "score_no_cue_span", "score_quiet",
             "score_long_prev", "tail_min_frames", "tail_merge_max", "image_token_px", "transcript_max_chars",
             "sheet_width", "sheet_gap_px", "sheet_cols_upto20", "sheet_cols_upto30", "sheet_cols_more",
             "sheet_label_px", "sheet_font_px", "sheet_jpeg_quality", "overview_max_batches")
_FLOAT_KEYS = ("max_span_s", "score_scene_frac", "score_quiet_s", "score_long_prev_s", "text_chars_per_token",
               "transcript_gap_s", "transcript_max_s")
# Derived from max_frames when it is overridden and they are not (defaults: 20 -> 12, 8, 25).
_DERIVED = {"soft_min": 0.6, "tail_min_frames": 0.4, "tail_merge_max": 1.25}


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def effective_batch_params(overrides: dict | None = None) -> dict:
    """BATCH_DEFAULTS updated with ``overrides`` (None values are ignored), validated.

    Aliases: batch_frames -> max_frames, batch_text -> max_text_chars, batch_span(_s) ->
    max_span_s, trecho -> range, no_soft_cuts=True -> soft_cuts=False. Unknown keys raise
    ValueError. When max_frames is overridden alone, soft_min / tail_min_frames /
    tail_merge_max follow it (0.6x, 0.4x, 1.25x). ``range`` is None or [t0, t1] in seconds
    (3 decimals). Types are normalized so the dict hashes stably; idempotent.
    """
    p = copy.deepcopy(BATCH_DEFAULTS)
    p.setdefault("range", None)
    given: set[str] = set()
    for key, value in (overrides or {}).items():
        if key == "no_soft_cuts":
            if value is not None:
                p["soft_cuts"] = not bool(value)
                given.add("soft_cuts")
            continue
        name = _BATCH_ALIASES.get(key, key)
        if name not in p:
            raise ValueError(f"unknown batch parameter {key!r}; valid: {', '.join(sorted(p))}")
        if value is None:
            continue
        p[name] = copy.deepcopy(value)
        given.add(name)
    if "max_frames" in given:
        for key, factor in _DERIVED.items():
            if key not in given:
                p[key] = _round_half_up(p["max_frames"] * factor) if key != "tail_merge_max" \
                    else int(math.floor(p["max_frames"] * factor))
    for key in _INT_KEYS:
        value = p[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise ValueError(f"batch parameter {key!r} must be an integer, got {value!r}")
        p[key] = int(value)
    for key in _FLOAT_KEYS:
        value = p[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"batch parameter {key!r} must be a finite number, got {value!r}")
        p[key] = float(value)
    if not isinstance(p["soft_cuts"], bool):
        raise ValueError(f"batch parameter 'soft_cuts' must be true or false, got {p['soft_cuts']!r}")
    if p["max_frames"] < 1:
        raise ValueError("batch parameter 'max_frames' must be >= 1")
    p["soft_min"] = max(1, min(p["soft_min"], p["max_frames"]))
    p["tail_min_frames"] = max(0, p["tail_min_frames"])
    p["tail_merge_max"] = max(p["tail_merge_max"], 1)
    for key in ("max_text_chars", "image_token_px", "sheet_width", "sheet_font_px", "transcript_max_chars"):
        if p[key] < 1:
            raise ValueError(f"batch parameter {key!r} must be >= 1")
    for key in ("max_span_s", "text_chars_per_token", "transcript_max_s"):
        if p[key] <= 0:
            raise ValueError(f"batch parameter {key!r} must be > 0")
    if not 1 <= p["sheet_jpeg_quality"] <= 100:
        raise ValueError("batch parameter 'sheet_jpeg_quality' must be in 1..100")
    if min(p["sheet_cols_upto20"], p["sheet_cols_upto30"], p["sheet_cols_more"]) < 1:
        raise ValueError("batch parameters 'sheet_cols_*' must be >= 1")
    if not isinstance(p["sheet_font"], str):
        raise ValueError("batch parameter 'sheet_font' must be a path string")
    p["sheet_font"] = p["sheet_font"].replace("\\", "/")
    p["range"] = _normalize_range(p["range"])
    return p


def _normalize_range(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        parts = value.replace("–", "-").split("-")
        if len(parts) != 2:
            raise ValueError(f"batch parameter 'range': expected 'hh:mm:ss-hh:mm:ss', got {value!r}")
        value = [parse_time(parts[0]), parse_time(parts[1])]
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"batch parameter 'range' must be [t0, t1] in seconds, got {value!r}")
    t0, t1 = value
    for t in (t0, t1):
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0:
            raise ValueError(f"batch parameter 'range' must hold two times >= 0, got {value!r}")
    t0, t1 = ms_to_s(s_to_ms(t0)), ms_to_s(s_to_ms(t1))
    if t1 <= t0:
        raise ValueError(f"batch parameter 'range': end {t1} must be after start {t0}")
    return [t0, t1]


def image_tokens(width: int, height: int, px: int = 28) -> int:
    """Image tokens = ceil(w/px) * ceil(h/px): 1,792 for 1568x882, 2,072 for 1568x1018."""
    return (-(-int(width) // px)) * (-(-int(height) // px))


def text_tokens(chars: int, chars_per_token: float = 3.5) -> int:
    """Text tokens = ceil(chars / 3.5)."""
    if chars <= 0:
        return 0
    return int(math.ceil(chars / chars_per_token - 1e-9))


def batch_cues(i0: int, i1: int, frame_cues: Sequence[Sequence[int]]) -> list[int]:
    """Distinct cue indexes of frames [i0, i1), ascending."""
    seen: set[int] = set()
    for i in range(i0, i1):
        seen.update(frame_cues[i])
    return sorted(seen)


def plan_batches(frames: Sequence[tuple[int, int, float]], cues: Sequence[tuple[int, int, int]],
                 frame_cues: Sequence[Sequence[int]], params: dict) -> list[tuple[int, int]]:
    """Split frames into batches -> list of half-open index ranges (i0, i1).

    ``frames``: (start_ms, end_ms, change frac) in order. ``cues``: (start_ms, end_ms, chars).
    ``frame_cues``: from ``align``. ``params``: effective batch params.
    """
    n = len(frames)
    if n == 0:
        return []
    p = params
    max_frames, soft_min, max_text = p["max_frames"], p["soft_min"], p["max_text_chars"]
    max_span = s_to_ms(p["max_span_s"])
    quiet = s_to_ms(p["score_quiet_s"])
    long_prev = s_to_ms(p["score_long_prev_s"])
    index = CueIndex([(s, e) for s, e, _ in cues])

    def limit(i0: int) -> int:
        seen: set[int] = set()
        chars = 0
        j = i0
        while j < n:
            new = [c for c in frame_cues[j] if c not in seen]
            add = sum(cues[c][2] for c in new)
            if j > i0 and (j - i0 + 1 > max_frames or chars + add > max_text
                           or frames[j][1] - frames[i0][0] > max_span):
                break
            seen.update(new)
            chars += add
            j += 1
        return j

    def score(b: int) -> int:
        t = frames[b][0]
        s = 0
        if frames[b][2] >= p["score_scene_frac"]:
            s += p["score_scene"]
        if not index.spans(t):
            s += p["score_no_cue_span"]
        if not index.near(t, quiet):
            s += p["score_quiet"]
        if frames[b - 1][1] - frames[b - 1][0] >= long_prev:
            s += p["score_long_prev"]
        return s

    out: list[tuple[int, int]] = []
    i0 = 0
    while i0 < n:
        j_limit = limit(i0)
        if j_limit >= n:
            out.append((i0, n))
            break
        cut = j_limit
        if p["soft_cuts"] and i0 + soft_min <= j_limit:
            best_score, best_b = max((score(b), b) for b in range(i0 + soft_min, j_limit + 1))
            if best_score > 0:
                cut = best_b
        out.append((i0, cut))
        i0 = cut
    if len(out) >= 2:
        (a0, _a1), (b0, b1) = out[-2], out[-1]
        if b1 - b0 < p["tail_min_frames"]:
            total = b1 - a0
            if total <= p["tail_merge_max"]:
                out[-2:] = [(a0, b1)]
            else:
                first = -(-total // 2)
                out[-2:] = [(a0, a0 + first), (a0 + first, b1)]
    return out
