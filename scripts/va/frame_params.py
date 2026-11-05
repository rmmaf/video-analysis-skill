"""Frame-stage parameters: defaults + sensitivity preset + overrides -> the canonical dict
hashed into the frames cache key (design-deterministic.md section 3.6 and 7).

Pure Python on purpose (no numpy): prepare.py computes the cache keys of a fully cached run
without importing the frame engine. ``va.dedup`` and ``va.extract`` re-export these names.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from . import DEDUP_DEFAULTS, OUTPUT_DEFAULTS, SENSITIVITY_PRESETS

__all__ = ["EXEC_KEYS", "effective_frame_params", "frame_params_for_output"]

# Keys that change how the stage runs but never what it writes (excluded from frames.json "params").
EXEC_KEYS = ("sequential",)

# CLI-style names accepted by effective_frame_params, mapped to the canonical keys.
_ALIASES = {
    "settle": "settle_s",
    "max_settle_wait": "max_settle_wait_s",
    "motion_interval": "motion_interval_s",
    "motion_max_interval": "motion_max_interval_s",
    "ignore_region": "ignore_regions",
    "hot_window": "hot_window_s",
    "heartbeat": "heartbeat_s",
    "revisit_window": "revisit_window_s",
}


# --- parameters --------------------------------------------------------------------------

def _num(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"frame param {name!r}: expected a finite number, got {v!r}")
    return float(v)


def _norm_region(r: Any) -> list[float]:
    if isinstance(r, str):
        r = [float(x) for x in r.replace(";", ",").split(",") if x.strip()]
    if not isinstance(r, (list, tuple)) or len(r) != 4:
        raise ValueError(f"ignore region must be x,y,w,h fractions, got {r!r}")
    x, y, w, h = (_num(v, "ignore_regions") for v in r)
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError(f"ignore region {r!r}: x,y in [0,1), w,h in (0,1]")
    w, h = min(w, 1 - x), min(h, 1 - y)
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def _norm_regions(v: Any) -> list[list[float]]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [s for s in v.split(";") if s.strip()]
    if isinstance(v, (list, tuple)) and len(v) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        v = [v]
    out = sorted({tuple(_norm_region(r)) for r in v})
    return [list(r) for r in out]


def _norm_span(e: Any) -> tuple[float, ...]:
    """One timeline entry -> (start, end, x, y, w, h): [start, end, x, y, w, h] or
    {"start": s, "end": s, "region": [x, y, w, h] or "x,y,w,h"} (seconds of the video)."""
    if isinstance(e, dict):
        extra = sorted(set(e) - {"start", "end", "region"})
        if extra or not {"start", "end", "region"} <= set(e):
            raise ValueError(f"ignore timeline entry needs exactly start, end and region, got {sorted(e)}")
        start, end, region = e["start"], e["end"], e["region"]
    elif isinstance(e, (list, tuple)) and len(e) == 6:
        start, end, region = e[0], e[1], list(e[2:])
    else:
        raise ValueError(f"ignore timeline entry must be [start, end, x, y, w, h] or "
                         f"{{start, end, region}}, got {e!r}")
    a, b = _num(start, "ignore_timeline"), _num(end, "ignore_timeline")
    if a < 0 or b <= a:
        raise ValueError(f"ignore timeline entry {e!r}: need 0 <= start < end (seconds)")
    return (round(a, 3), round(b, 3), *_norm_region(region))


def _norm_timeline(v: Any) -> list[list[float]]:
    """Regions ignored only during a span of the video -> sorted unique [start, end, x, y, w, h]."""
    if v is None:
        return []
    if not isinstance(v, (list, tuple)):
        raise ValueError(f"ignore_timeline must be a list of entries, got {type(v).__name__}")
    return [list(s) for s in sorted({_norm_span(e) for e in v})]


def _norm_range(v: Any) -> list[float] | None:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"range must be [start, end] seconds or null, got {v!r}")
    a = 0.0 if v[0] is None else _num(v[0], "range")
    b = None if v[1] is None else _num(v[1], "range")
    if a < 0:
        raise ValueError(f"range start must be >= 0, got {a}")
    a = round(a, 3)
    if b is not None:
        b = round(b, 3)
        if b <= a:
            raise ValueError(f"range end {b} must be > start {a}")
    if a == 0 and b is None:
        return None
    return [a, b]


def effective_frame_params(overrides: dict | None = None, *, strict: bool = True) -> dict:
    """DEDUP_DEFAULTS + the sensitivity preset + OUTPUT_DEFAULTS + overrides -> canonical dict.

    Accepts the canonical keys and the CLI names (settle, max_settle_wait, motion_interval,
    motion_max_interval, cursor_size, ignore_region(s), no_revisit, start/end). ``None`` values
    mean "default". The result is deterministic, JSON-serializable and idempotent
    (``effective_frame_params(effective_frame_params(x)) == effective_frame_params(x)``), so
    the CLI can hash it for the frames cache key. ``sequential`` (execution only, same output)
    is kept only when true, and ``ignore_timeline`` (regions ignored only during a span of the
    video, ``[start, end, x, y, w, h]``) only when not empty, so the keys of the runs without
    them do not change. Unknown keys raise ValueError; ``strict=False`` drops them instead
    (params read back from an older frames.json or run.json).
    """
    ov = {k: v for k, v in dict(overrides or {}).items() if v is not None}
    for old, new in _ALIASES.items():
        if old in ov:
            val = ov.pop(old)
            ov.setdefault(new, val)
    if "cursor_size" in ov:
        size = _num(ov.pop("cursor_size"), "cursor_size")
        if not 0 < size <= 0.5:
            raise ValueError(f"cursor_size must be in (0, 0.5], got {size}")
        ov.setdefault("cursor_h_frac", round(size, 6))
        ov.setdefault("cursor_w_frac", round(size * 2.0 / 3.0, 6))
    if "no_revisit" in ov:
        if ov.pop("no_revisit"):
            ov["revisit"] = False
    if "start" in ov or "end" in ov:
        rng = [ov.pop("start", 0.0), ov.pop("end", None)]
        ov.setdefault("range", rng)
    sens = ov.pop("sensitivity", DEDUP_DEFAULTS["sensitivity"])
    if sens not in SENSITIVITY_PRESETS:
        raise ValueError(f"sensitivity must be one of {sorted(SENSITIVITY_PRESETS)}, got {sens!r}")
    p: dict[str, Any] = copy.deepcopy(DEDUP_DEFAULTS)
    for k, v in OUTPUT_DEFAULTS.items():
        p.setdefault(k, copy.deepcopy(v))
    p.update(copy.deepcopy(SENSITIVITY_PRESETS[sens]))
    p["sensitivity"] = sens
    p["range"] = None
    sequential = bool(ov.pop("sequential", False))
    timeline = _norm_timeline(ov.pop("ignore_timeline", None))
    unknown = sorted(k for k in ov if k not in p)
    if unknown and not strict:
        for k in unknown:
            ov.pop(k)
        unknown = []
    if unknown:
        accepted = sorted(set(p) | set(_ALIASES) | {"cursor_size", "no_revisit", "start", "end", "sequential",
                                                    "ignore_timeline"})
        raise ValueError(f"unknown frame params {unknown}; accepted: {accepted}")
    for k, v in ov.items():
        default = p[k]
        if k == "ignore_regions":
            p[k] = _norm_regions(v)
        elif k == "range":
            p[k] = _norm_range(v)
        elif k == "hot_mode":
            if v not in ("windowed", "off", "global"):
                raise ValueError(f"hot_mode must be windowed or off, got {v!r}")
            p[k] = v
        elif isinstance(default, bool):
            if not isinstance(v, bool):
                raise ValueError(f"frame param {k!r}: expected true/false, got {v!r}")
            p[k] = v
        elif isinstance(default, int):
            fv = _num(v, k)
            if fv != int(fv):
                raise ValueError(f"frame param {k!r}: expected an integer, got {v!r}")
            p[k] = int(fv)
        elif isinstance(default, float):
            p[k] = float(_num(v, k))
        elif isinstance(default, str):
            if not isinstance(v, str):
                raise ValueError(f"frame param {k!r}: expected a string, got {v!r}")
            p[k] = v
        else:
            p[k] = copy.deepcopy(v)
    if p["sample_fps"] <= 0 or p["sample_fps"] > 30:
        raise ValueError(f"sample_fps must be in (0, 30], got {p['sample_fps']}")
    p["max_edge"] = max(16, min(int(p["max_edge"]), int(p["max_edge_cap"])))
    p["jpeg_q"] = max(int(p["jpeg_q_min"]), min(int(p["jpeg_q"]), int(p["jpeg_q_max"])))
    for k in ("settle_s", "max_settle_wait_s", "motion_interval_s", "motion_max_interval_s", "hot_window_s"):
        if p[k] <= 0:
            raise ValueError(f"{k} must be > 0, got {p[k]}")
    if not 0 <= p["t_lo"] < p["t_hi"] <= 255:
        raise ValueError(f"need 0 <= t_lo < t_hi <= 255, got {p['t_lo']} / {p['t_hi']}")
    if p["cell_px"] not in (4, 8, 16) or p["cc_cell_px"] not in (1, 2, 4, 8):
        raise ValueError("cell_px must be 4, 8 or 16 and cc_cell_px 1, 2, 4 or 8")
    if timeline:
        p["ignore_timeline"] = timeline
    if sequential:
        p["sequential"] = True
    return p


def frame_params_for_output(p: dict) -> dict:
    """The params written to frames.json: everything that shapes the output (no EXEC_KEYS)."""
    return {k: copy.deepcopy(v) for k, v in p.items() if k not in EXEC_KEYS}
