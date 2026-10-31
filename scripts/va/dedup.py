"""Frame dedup: pure numpy, no I/O (design-deterministico.md section 3.2-3.6).

``Analyzer(params, analysis_size).push(gray) -> list[Decision]`` receives the gray analysis
frames of the sampling grid in order (sample k = range start + k / sample_fps) and decides,
with a lookahead of ``hot_window_s`` (+ ``cut_calm_pairs``; the windowed hot mask needs it),
which samples become frames. ``finish()`` decides the remaining samples and commits a pending
change.

Pieces
- ``Comparator.classify(cur, ref, active)``: same | cursor | change, using two thresholds,
  connected components on a 4-px cell grid, a noise rule and a cursor-shape rule.
- ``Comparator.pair_cells``: per-pair activity on 8x8-px cells (noise and cursor-like
  components dropped); e_r = active cells / all cells (used for layout cuts).
- ``HotTracker``: windowed hot mask (+-hot_window_s, split at layout cuts; criteria duty_local,
  duty_all, recurrence of small activity and ticker, with hysteresis; dilation, fill of compact
  components).
- ``Analyzer``: the state machine of section 3.4.
- ``density_report`` / ``suggest_ignore``: section 3.5.

Refinements over the spec text, all found on the synthetic scenarios or the real fixtures
(tests/data/calibration.md has the measurements):
- "small" (the recurrence criterion) is decided per pair as in the spec, on the active cells
  minus busy cells (active in >= busy_min of the previous busy_pairs pairs, e.g. a webcam:
  otherwise a gallery webcam makes every pair big), and thin components (filling <=
  thin_max of their bounding box: a speaker ring around a big gallery tile, the pulsing border
  of the Teams stage, 4-11 % of the cells) do not count toward the 3 % extent; all of it must
  still change <= small_max of the px. Per-component variants masked results tables, whose
  update splits into many small column components.
- a layout cut is a big pair followed by a calm pair within ``cut_calm_pairs`` (2 s), not only
  by the very next pair: screens that render progressively hid the cut and leaked the mask.
- noise also covers components whose strong px are below ``min_strong_ratio`` of their px
  (soft, low-contrast changes such as hover shading or camera grain).
- up to ``cursor_scatter_blobs`` cursor-sized blobs with at most ``cursor_scatter_px`` x the
  cursor budget in total count as cursor (pointer + taskbar/tab highlights).
- a settled frame is captured at the last sample of the stable run (the spec takes the first):
  the most complete state, e.g. it includes a glyph typed during the settle window.
- a sample is "quiet" only when the change vs the previous sample is cursor-like AND the change
  vs the first sample of the stable run passes the strict cursor test (pointer box or vertical
  merge only), so typing (2-3 glyphs per pair next to a tall caret) does not settle. A change
  of at most settle_noise_rel of the pending change (and at most the scatter budget) is quiet
  too: a 76 px flicker must not hide a 2 s dialog-closed state (32,800 px).
- a fourth hot criterion, "ticker": cursor-sized activity whose cell content differs from two
  samples earlier (a running timer, a spinner) in >= tick_on_duty of the window's pairs. The
  spec drops cursor-like components from the pair activity, so a per-second timer never got
  hot, and its digits accumulated vs the last kept frame into a false frame every ~10 s on a
  static screen. A blinking caret returns to the same state and is not a ticker.
- the revisit test uses the same mask as the commit and needs >= revisit_min_active of the
  frame unmasked (a camera-like frame is almost fully masked).

Everything is deterministic: integer pixel counts, fixed iteration orders, no randomness.
"""
from __future__ import annotations

import base64
import bisect
import math
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

__all__ = [
    "SAME", "CURSOR", "CHANGE", "Classified", "Comparator", "Decision", "HotTracker", "Analyzer", "label8",
    "effective_frame_params", "frame_params_for_output", "static_mask_from_regions", "density_report",
    "suggest_ignore", "pack_cells", "unpack_cells", "match_events", "EXEC_KEYS",
]

SAME, CURSOR, CHANGE = "same", "cursor", "change"

# Parameters live in a numpy-free module (prepare.py hashes them without importing numpy).
from .frame_params import (EXEC_KEYS, _ALIASES, _norm_range, _norm_region, _norm_regions,  # noqa: E402,F401
                           _num, effective_frame_params, frame_params_for_output)


def static_mask_from_regions(regions: list, aw: int, ah: int) -> np.ndarray | None:
    """Pixel mask (True = ignored) at analysis size for [x, y, w, h] fraction regions."""
    if not regions:
        return None
    m = np.zeros((ah, aw), bool)
    for x, y, w, h in regions:
        x0, y0 = int(math.floor(x * aw)), int(math.floor(y * ah))
        x1, y1 = int(math.ceil((x + w) * aw)), int(math.ceil((y + h) * ah))
        m[max(0, y0):min(ah, y1), max(0, x0):min(aw, x1)] = True
    return m


# --- connected components ------------------------------------------------------------------

def label8(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """8-connected component labels of a 2-D bool array: (int32 labels 1..n, n).

    Run-length based: runs per row, union of overlapping runs between consecutive rows,
    labels numbered by first appearance in row-major order (deterministic)."""
    mask = np.asarray(mask, bool)
    h, w = mask.shape
    pad = np.zeros((h, w + 2), np.int8)
    pad[:, 1:-1] = mask
    dif = np.diff(pad, axis=1)
    sr, sc = np.nonzero(dif == 1)
    er, ec = np.nonzero(dif == -1)
    nr = len(sr)
    out = np.zeros((h, w), np.int32)
    if nr == 0:
        return out, 0
    w2 = w + 2
    key_s = sr.astype(np.int64) * w2 + sc
    key_e = er.astype(np.int64) * w2 + ec
    # runs of the previous row overlapping run i (8-connectivity): sc_j <= ec_i and ec_j >= sc_i
    q_lo = (sr.astype(np.int64) - 1) * w2 + sc
    q_hi = (sr.astype(np.int64) - 1) * w2 + ec
    lo = np.searchsorted(key_e, q_lo, side="left")
    hi = np.searchsorted(key_s, q_hi, side="right")
    cnt = np.maximum(hi - lo, 0)
    total = int(cnt.sum())
    parent = list(range(nr))

    def find(a: int) -> int:
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:
            parent[a], a = root, parent[a]
        return root

    if total:
        ii = np.repeat(np.arange(nr), cnt)
        jj = np.repeat(lo, cnt) + (np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt))
        for a, b in zip(ii.tolist(), jj.tolist()):
            ra, rb = find(a), find(b)
            if ra != rb:
                if ra < rb:
                    parent[rb] = ra
                else:
                    parent[ra] = rb
    remap: dict[int, int] = {}
    lab = np.empty(nr, np.int32)
    for i in range(nr):
        r = find(i)
        if r not in remap:
            remap[r] = len(remap) + 1
        lab[i] = remap[r]
    delta = np.zeros((h, w + 1), np.int32)
    delta[sr, sc] = lab
    delta[er, ec] = -lab
    out[:] = np.cumsum(delta, axis=1)[:, :w]
    return out, len(remap)


def _dilate8(cells: np.ndarray, n: int = 1) -> np.ndarray:
    m = cells.copy()
    for _ in range(n):
        p = np.pad(m, 1)
        acc = p[1:-1, 1:-1].copy()
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                if dy == 1 and dx == 1:
                    continue
                acc |= p[dy:dy + m.shape[0], dx:dx + m.shape[1]]
        m = acc
    return m


def pack_cells(cells: np.ndarray) -> str:
    """bool cell mask -> base64 of np.packbits (row-major), as written to frames.json."""
    return base64.b64encode(np.packbits(np.asarray(cells, bool).ravel()).tobytes()).decode("ascii")


def unpack_cells(text: str, shape: tuple[int, int]) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(base64.b64decode(text), np.uint8))
    return bits[: shape[0] * shape[1]].reshape(shape).astype(bool)


# --- comparison primitive ------------------------------------------------------------------

@dataclass
class Classified:
    cls: str                     # same | cursor | change
    px: int = 0                  # counted px (see Comparator.classify)
    weak: int = 0                # raw weak px in the active area
    strong: int = 0              # raw strong px in the active area
    blobs: int = 0               # non-noise components (0 on the fast path)
    frac: float = 0.0            # px / active px
    bbox: tuple[int, int, int, int] | None = None   # x0, y0, x1, y1 (exclusive), analysis px
    fast: bool = False           # decided by the fast path (big change)
    comps: dict | None = None    # per-component arrays (detail=True), for explain.py


FULL_CHANGE = Classified(CHANGE, frac=1.0, fast=True)


class Comparator:
    """classify(cur, ref, active) and pair_cells(cur, prev, active) for one analysis size."""

    def __init__(self, p: dict, aw: int, ah: int):
        self.aw, self.ah = int(aw), int(ah)
        self.npx = self.aw * self.ah
        self.t_lo, self.t_hi = int(p["t_lo"]), int(p["t_hi"])
        self.fast_strong = float(p["fast_strong_frac"])
        self.fast_weak = float(p["fast_weak_frac"])
        self.cc = int(p["cc_cell_px"])
        self.cell = int(p["cell_px"])
        if self.aw % self.cell or self.ah % self.cell or self.aw % self.cc or self.ah % self.cc:
            raise ValueError(f"analysis size {aw}x{ah} must be a multiple of the cell sizes")
        self.gh, self.gw = self.ah // self.cell, self.aw // self.cell
        self.ncells = self.gh * self.gw
        self.cell_active = int(p["cell_active_px"])
        self.min_blob = int(p["min_blob_px"])
        self.min_strong = int(p["min_strong_px"])
        self.min_strong_ratio = float(p["min_strong_ratio"])
        self.weak_only_max = float(p["weak_only_min_frac"]) * self.npx
        self.ch = int(math.ceil(float(p["cursor_h_frac"]) * self.ah - 1e-9)) + 1
        self.cw = int(math.ceil(float(p["cursor_w_frac"]) * self.ah - 1e-9)) + 1
        self.budget = float(p["cursor_budget_factor"]) * self.cw * self.ch
        self.max_cursor_blobs = int(p["max_cursor_blobs"])
        self.scatter_blobs = int(p["cursor_scatter_blobs"])
        self.scatter_px = float(p["cursor_scatter_px"]) * self.budget
        self.small_cells = float(p["small_max"]) * self.ncells
        self.small_px = float(p["small_max"]) * self.npx
        self.thin_max = float(p["thin_max"])
        self.local_max = float(p["local_max"])

    # -- primitives
    @staticmethod
    def absdiff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return np.maximum(a, b) - np.minimum(a, b)       # uint8, no wrap-around

    def cursor_like(self, w: np.ndarray, h: np.ndarray, px: np.ndarray, strict: bool = False) -> np.ndarray:
        """Section 3.2 step 5. ``strict``: pointer box or vertical merge only (no horizontal or
        diagonal merge), used for the stability test over the settle window."""
        cw, ch = self.cw, self.ch
        ok = px <= 2 * self.budget
        c1 = (w <= cw) & (h <= ch)
        c3 = (w <= cw) & (h <= 2 * ch)
        if strict:
            return ok & (c1 | c3)
        c2 = (h >= 0.5 * ch) & (w <= 2 * cw) & (h <= ch)
        c4 = (h >= 0.5 * ch) & (w <= 1.6 * cw) & (h <= 1.6 * ch)
        return ok & (c1 | c2 | c3 | c4)

    def components(self, weak: np.ndarray, strong: np.ndarray, strict: bool = False) -> dict:
        """Components of the weak pixels on the cc-px cell grid: arrays x0, y0, x1, y1
        (exclusive), px, strong, noise, cursor, plus ys, xs, lab of every weak pixel."""
        cc = self.cc
        cells = weak.reshape(self.ah // cc, cc, self.aw // cc, cc).any(axis=(1, 3)) if cc > 1 else weak
        labels, n = label8(cells)
        ys, xs = np.nonzero(weak)
        lab = labels[ys // cc, xs // cc] if cc > 1 else labels[ys, xs]
        px = np.bincount(lab, minlength=n + 1)[1:]
        st = np.bincount(lab, weights=strong[ys, xs], minlength=n + 1)[1:].astype(np.int64)
        if n:
            order = np.argsort(lab, kind="stable")
            starts = np.searchsorted(lab[order], np.arange(1, n + 1))
            xo, yo = xs[order], ys[order]
            x0 = np.minimum.reduceat(xo, starts)
            x1 = np.maximum.reduceat(xo, starts) + 1
            y0 = np.minimum.reduceat(yo, starts)
            y1 = np.maximum.reduceat(yo, starts) + 1
        else:
            x0 = x1 = y0 = y1 = np.zeros(0, np.int64)
        weak_only = st < np.maximum(self.min_strong, self.min_strong_ratio * px)
        noise = (px < self.min_blob) | (weak_only & (px < self.weak_only_max))
        cursor = self.cursor_like(x1 - x0, y1 - y0, px, strict) & ~noise
        return {"n": n, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "px": px, "strong": st,
                "noise": noise, "cursor": cursor, "ys": ys, "xs": xs, "lab": lab}

    def classify(self, cur: np.ndarray, ref: np.ndarray, active: np.ndarray | None = None,
                 n_active: int | None = None, *, strict: bool = False, detail: bool = False) -> Classified:
        """same | cursor | change of ``cur`` vs ``ref`` over the ``active`` pixels.

        cursor: every non-noise component is cursor-like and there are at most
        max_cursor_blobs of them, or (not strict) at most cursor_scatter_blobs with at most
        cursor_scatter_px x budget px in total. A change reports px / frac / bbox over its
        non-cursor-like components (all components when every one is cursor-like)."""
        if n_active is None:
            n_active = self.npx if active is None else int(np.count_nonzero(active))
        if n_active <= 0:
            return Classified(SAME)
        d = self.absdiff(cur, ref)
        weak = d > self.t_lo
        if active is not None:
            weak &= active
        nweak = int(np.count_nonzero(weak))
        if nweak == 0:
            return Classified(SAME)
        strong = d > self.t_hi
        if active is not None:
            strong &= active
        nstrong = int(np.count_nonzero(strong))
        if nstrong > self.fast_strong * n_active or nweak > self.fast_weak * n_active:
            rows = np.flatnonzero(weak.any(axis=1))
            cols = np.flatnonzero(weak.any(axis=0))
            bbox = (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)
            return Classified(CHANGE, px=nweak, weak=nweak, strong=nstrong, blobs=0,
                              frac=nweak / n_active, bbox=bbox, fast=True)
        c = self.components(weak, strong, strict)
        keep = ~c["noise"]
        nk = int(np.count_nonzero(keep))
        comps = c if detail else None
        if nk == 0:
            return Classified(SAME, weak=nweak, strong=nstrong, comps=comps)
        all_cursor = bool(c["cursor"][keep].all())
        total = int(c["px"][keep].sum())
        if all_cursor and (nk <= self.max_cursor_blobs or
                           (not strict and nk <= self.scatter_blobs and total <= self.scatter_px)):
            sel, cls = keep, CURSOR
        else:
            cls = CHANGE
            noncur = keep & ~c["cursor"]
            sel = noncur if bool(noncur.any()) else keep
        px = int(c["px"][sel].sum())
        bbox = (int(c["x0"][sel].min()), int(c["y0"][sel].min()), int(c["x1"][sel].max()), int(c["y1"][sel].max()))
        return Classified(cls, px=px, weak=nweak, strong=nstrong, blobs=nk, frac=px / n_active,
                          bbox=bbox, comps=comps)

    def pair_cells(self, cur: np.ndarray, prev: np.ndarray, active: np.ndarray | None = None,
                   n_active: int | None = None) -> tuple[np.ndarray, float, np.ndarray, np.ndarray, np.ndarray]:
        """Pair activity (section 3.3): bool (gh, gw) cells with >= cell_active_px counted weak px
        (noise and cursor-like components dropped), e = active cells / all cells, the local
        cells (all active cells when e <= local_max, else none), the counted weak px per cell,
        and the cells with >= cell_active_px px of cursor-like components (for the ticker test;
        none on the fast path)."""
        if n_active is None:
            n_active = self.npx if active is None else int(np.count_nonzero(active))
        z = np.zeros((self.gh, self.gw), bool)
        zc = np.zeros((self.gh, self.gw), np.int32)
        if n_active <= 0:
            return z, 0.0, z, zc, z
        d = self.absdiff(cur, prev)
        weak = d > self.t_lo
        if active is not None:
            weak &= active
        nweak = int(np.count_nonzero(weak))
        if nweak == 0:
            return z, 0.0, z, zc, z
        g = self.cell
        big = nweak > self.fast_weak * n_active
        strong = None
        if not big:
            strong = d > self.t_hi
            if active is not None:
                strong &= active
            big = int(np.count_nonzero(strong)) > self.fast_strong * n_active
        cursor_cells = z
        if big:
            counts = weak.reshape(self.gh, g, self.gw, g).sum(axis=(1, 3), dtype=np.int32)
        else:
            c = self.components(weak, strong)
            if bool(c["cursor"].any()):
                mc = c["cursor"][c["lab"] - 1]
                ic = (c["ys"][mc] // g) * self.gw + (c["xs"][mc] // g)
                cursor_cells = np.bincount(ic, minlength=self.ncells).reshape(self.gh, self.gw) >= self.cell_active
            counted = ~c["noise"] & ~c["cursor"]
            if not bool(counted.any()):
                return z, 0.0, z, zc, cursor_cells
            mpx = counted[c["lab"] - 1]
            idx = (c["ys"][mpx] // g) * self.gw + (c["xs"][mpx] // g)
            counts = np.bincount(idx, minlength=self.ncells).reshape(self.gh, self.gw).astype(np.int32)
        cells = counts >= self.cell_active
        n_on = int(np.count_nonzero(cells))
        if n_on == 0:
            return z, 0.0, z, zc, cursor_cells
        e = n_on / self.ncells
        return cells, e, (cells if e <= self.local_max else z), counts, cursor_cells

    def small_activity(self, fresh: np.ndarray, counts: np.ndarray) -> np.ndarray:
        """Spec 3.3 "small" pair, refined: ``fresh`` (active minus busy cells) counts as small when
        its dense components span <= small_max of the cells in total and all of it changes
        <= small_max of the frame's px. A component is dense when it fills more than thin_max of
        its bounding box; thin ones (a ring, the pulsing border of the Teams stage) do not count
        toward the extent. Returns ``fresh`` when small, else no cells."""
        if not fresh.any():
            return fresh
        if float(counts[fresh].sum()) > self.small_px:
            return np.zeros_like(fresh)
        lab, n = label8(fresh)
        n_cells = np.bincount(lab.ravel(), minlength=n + 1)[1:]
        ys, xs = np.nonzero(lab)
        ls = lab[ys, xs]
        order = np.argsort(ls, kind="stable")
        starts = np.searchsorted(ls[order], np.arange(1, n + 1))
        yo, xo = ys[order], xs[order]
        area = ((np.maximum.reduceat(yo, starts) - np.minimum.reduceat(yo, starts) + 1) *
                (np.maximum.reduceat(xo, starts) - np.minimum.reduceat(xo, starts) + 1))
        dense = n_cells > self.thin_max * area
        if float(n_cells[dense].sum()) > self.small_cells:
            return np.zeros_like(fresh)
        return fresh


# --- hot mask ------------------------------------------------------------------------------

class HotTracker:
    """Windowed hot mask (section 3.3): per-cell counters over the pairs of [k-H, k+H], cut at
    the nearest layout cuts, with hysteresis that resets at each cut."""

    def __init__(self, p: dict, cmp: Comparator, sample_fps: float):
        self.cmp = cmp
        self.S = float(sample_fps)
        self.H = max(1, int(round(float(p["hot_window_s"]) * self.S)))
        self.m = max(1, int(p["cut_calm_pairs"]))
        self.R = 2 * self.H + self.m + 6
        self.nc = cmp.ncells
        self.cum = np.zeros((self.R, 4, self.nc), np.int32)
        self.cut_on, self.cut_after = float(p["cut_on"]), float(p["cut_after_max"])
        self.min_pairs = int(p["hot_min_pairs"])
        self.on = (float(p["hot_on_duty_local"]), float(p["hot_on_duty_all"]), float(p["hot_on_recur_per_min"]),
                   float(p["tick_on_duty"]))
        self.keep = (float(p["hot_keep_duty_local"]), float(p["hot_keep_duty_all"]), float(p["hot_keep_recur_per_min"]),
                     float(p["tick_keep_duty"]))
        self.dilate = int(p["hot_dilate_cells"])
        self.fill_ratio = float(p["hot_fill_ratio"])
        self.fill_max = float(p["hot_fill_max_area"]) * self.nc
        self.e: list[float] = [0.0]          # e[p] for pair p (pair 0 does not exist)
        self.cut: list[bool] = [False]
        self.cuts: list[int] = []
        self.finalized = 0
        self._pend: deque = deque()
        self.state = np.zeros(self.nc, bool)
        self._raw_prev: np.ndarray | None = None
        self._proc_prev = np.zeros((cmp.gh, cmp.gw), bool)
        self.version = 0                     # bumped whenever the processed mask changes
        self.last_n_pairs = 0

    def add_pair(self, p: int, cells: np.ndarray, e: float, local: np.ndarray, small: np.ndarray,
                 tick: np.ndarray | None = None) -> None:
        self.e.append(e)
        self.cut.append(False)
        tk = np.zeros(self.nc, bool) if tick is None else tick.ravel()
        self._pend.append((p, cells.ravel(), local.ravel(), small.ravel(), tk, e))
        while len(self._pend) > self.m:
            self._finalize_first()

    def flush(self) -> None:
        while self._pend:
            self._finalize_first()

    def _finalize_first(self) -> None:
        q, cells, local, small, tick, e = self._pend.popleft()
        nxt = [x[5] for x in list(self._pend)[: self.m]]
        cut = e >= self.cut_on and bool(nxt) and min(nxt) < self.cut_after
        prev = self.cum[(q - 1) % self.R]
        cur = self.cum[q % self.R]
        if cut:
            cur[:] = prev
            self.cut[q] = True
            self.cuts.append(q)
        else:
            cur[0] = prev[0] + local
            cur[1] = prev[1] + cells
            cur[2] = prev[2] + small
            cur[3] = prev[3] + tick
        self.finalized = q

    def mask_for(self, k: int) -> np.ndarray:
        """Processed hot cells (bool gh x gw) for sample k; needs pairs up to k+H finalized."""
        i = bisect.bisect_right(self.cuts, k)
        cl = self.cuts[i - 1] if i > 0 else -1
        cr = self.cuts[i] if i < len(self.cuts) else 1 << 60
        if k < len(self.cut) and self.cut[k]:
            self.state[:] = False
        lo = max(1, k - self.H, cl + 1)
        hi = min(self.finalized, k + self.H, cr - 1)
        n = hi - lo + 1
        self.last_n_pairs = max(n, 0)
        if n < self.min_pairs:
            self.state[:] = False
        else:
            s = self.cum[hi % self.R].astype(np.int64) - self.cum[(lo - 1) % self.R]
            loc, alll, small, tick = s[0], s[1], s[2], s[3]
            per_min = 60.0 * self.S / n            # small active pairs -> per minute
            on = ((loc >= self.on[0] * n) | (alll >= self.on[1] * n) | (small * per_min >= self.on[2])
                  | (tick >= self.on[3] * n))
            keep = ((loc >= self.keep[0] * n) | (alll >= self.keep[1] * n) | (small * per_min >= self.keep[2])
                    | (tick >= self.keep[3] * n))
            self.state = on | (self.state & keep)
        raw = self.state
        if self._raw_prev is not None and np.array_equal(raw, self._raw_prev):
            return self._proc_prev
        self._raw_prev = raw.copy()
        new = self._post(raw.reshape(self.cmp.gh, self.cmp.gw))
        if not np.array_equal(new, self._proc_prev):
            self._proc_prev = new
            self.version += 1
        return self._proc_prev

    def _post(self, cells: np.ndarray) -> np.ndarray:
        if not cells.any():
            return np.zeros_like(cells)
        m = _dilate8(cells, self.dilate) if self.dilate > 0 else cells.copy()
        lab, n = label8(m)
        out = m.copy()
        for i in range(1, n + 1):
            ys, xs = np.nonzero(lab == i)
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
            area = (y1 - y0) * (x1 - x0)
            if area <= self.fill_max and len(ys) >= self.fill_ratio * area:
                out[y0:y1, x0:x1] = True
        return out

    def pair_info(self, p: int) -> tuple[float, bool]:
        if 0 < p < len(self.e):
            return self.e[p], self.cut[p]
        return 0.0, False


# --- analyzer ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Decision:
    """One kept frame. Times are sample indices on the grid (t = range start + k / fps)."""
    n: int                        # 1-based frame number
    sample: int                   # captured sample
    start_sample: int             # first detection of the change (frame 1: 0 = range start)
    kind: str                     # first | settled | motion | heartbeat | final
    same_as: int | None           # revisit: frame number of the original screen
    frac: float
    bbox: tuple[float, float, float, float]   # x, y, w, h fractions of the frame
    blobs: int
    hot_frac: float
    committed_at: int             # sample at which the decision was taken


@dataclass
class _Screen:
    n: int
    gray: np.ndarray
    t_ms: int


@dataclass
class _HotSeg:
    k0: int
    first: np.ndarray
    union: np.ndarray
    k1: int = 0


class Analyzer:
    """Section 3.4 state machine over the gray samples. See the module docstring."""

    def __init__(self, params: dict, analysis_size: tuple[int, int] | list[int], *,
                 static_mask: np.ndarray | None = None, record_rows: bool = True):
        p = effective_frame_params(params)
        self.p = p
        self.aw, self.ah = int(analysis_size[0]), int(analysis_size[1])
        self.S = float(p["sample_fps"])
        self.cmp = Comparator(p, self.aw, self.ah)
        static = static_mask_from_regions(p["ignore_regions"], self.aw, self.ah)
        if static_mask is not None:
            static = static_mask.copy() if static is None else (static | static_mask)
        self.static = static
        self.static_active = None if static is None else ~static
        self.n_static_active = self.cmp.npx if static is None else int(np.count_nonzero(~static))
        self.windowed = p["hot_mode"] == "windowed"
        self.hot = HotTracker(p, self.cmp, self.S) if self.windowed else None
        self.lookahead = (self.hot.H + self.hot.m) if self.windowed else 0
        self.settle_pairs = max(1, int(round(float(p["settle_s"]) * self.S)))
        self.wait_ms = int(round(float(p["max_settle_wait_s"]) * 1000))
        self.mi_ms = float(p["motion_interval_s"]) * 1000
        self.mmax_ms = float(p["motion_max_interval_s"]) * 1000
        self.hb_ms = int(round(float(p["heartbeat_s"]) * 1000))
        self.hb_min = float(p["heartbeat_min_frac"])
        self.cam_hot = float(p["camera_hot_frac"])
        self.revisit = bool(p["revisit"])
        self.revisit_n = int(p["revisit_frames"])
        self.revisit_ms = int(round(float(p["revisit_window_s"]) * 1000))
        self.revisit_min_active = float(p["revisit_min_active"]) * self.cmp.npx
        self.local_max = float(p["local_max"])
        self.small_max = float(p["small_max"])
        self.L = self.lookahead + self.settle_pairs + 6
        self.ring = np.zeros((self.L, self.ah, self.aw), np.uint8)
        self.n = 0                    # samples pushed
        self.next_k = 0               # next sample to decide
        self.finished = False
        # state machine
        self.ref: np.ndarray | None = None
        self.pending: int | None = None
        self.pending_px = 0
        self.noise_rel = float(p["settle_noise_rel"])
        self.s0 = 0
        self.s0_img: np.ndarray | None = None
        self.stable = 0
        self.level = 0
        self.last_c = 0
        self.prev_screen = 0
        self.screens: list[_Screen] = []
        self._recent: dict[int, Classified] = {}
        self._hot_key: int | None = None
        # busy cells (active in >= busy_min of the previous busy_pairs pairs: a webcam) are left
        # out of the "small" components, so a ring touching a webcam tile is judged on its own
        self._hist = np.zeros((max(1, int(p["busy_pairs"])), self.cmp.gh, self.cmp.gw), bool)
        self.busy_min = max(1, int(p["busy_min"]))
        self.tick_tol = float(p["tick_tol"]) * self.cmp.cell * self.cmp.cell
        self._hot_frac = 0.0
        self._active = self.static_active
        self._n_active = self.n_static_active
        # outputs
        self.decisions: list[Decision] = []
        self.record_rows = record_rows
        self.rows: list[list[Any]] = []
        self.frame_tags: dict[int, str] = {}   # captured sample -> "F0012" or "F0012=F0003"
        self.hot_segments: list[dict] = []
        self._seg: _HotSeg | None = None
        self.camera_spans: list[list[int]] = []
        self.hot_fracs: list[float] = []
        self.inspect: dict[int, dict] = {}     # sample -> details, for samples in self.watch
        self.watch: set[int] = set()

    # -- time helpers
    def ms(self, k: int) -> int:
        """Time of sample k relative to the range start, in integer ms."""
        return int(math.floor(k * 1000.0 / self.S + 0.5))

    def gray(self, k: int) -> np.ndarray:
        if k < 0 or k >= self.n or k < self.n - self.L:
            raise IndexError(f"sample {k} not in the ring (have {max(0, self.n - self.L)}..{self.n - 1})")
        return self.ring[k % self.L]

    def min_capture_sample(self) -> int:
        """Smallest sample a future decision may capture (the JPEG spool can drop older ones):
        every commit captures the sample being decided, or the last one at EOF."""
        return self.next_k

    # -- public API
    def push(self, gray: np.ndarray) -> list[Decision]:
        if self.finished:
            raise RuntimeError("Analyzer.push after finish()")
        g = np.asarray(gray, np.uint8)
        if g.shape != (self.ah, self.aw):
            g = g.reshape(self.ah, self.aw)
        r = self.n
        self.ring[r % self.L] = g
        self.n += 1
        if self.windowed and r >= 1:
            cur, prev = self.ring[r % self.L], self.ring[(r - 1) % self.L]
            cells, e, local, counts, cursor_cells = self.cmp.pair_cells(cur, prev, self.static_active,
                                                                        self.n_static_active)
            busy = self._hist.sum(axis=0, dtype=np.int16) >= self.busy_min
            small = self.cmp.small_activity(cells & ~busy, counts)
            self._hist[r % len(self._hist)] = cells
            tick = None
            if r >= 2 and cursor_cells.any():
                # ticker: cursor-sized activity whose content is new (differs from 2 samples
                # ago): a running timer or a spinner; a blinking caret returns to the same state
                g8 = self.cmp.cell
                d2 = Comparator.absdiff(cur, self.ring[(r - 2) % self.L]).reshape(
                    self.cmp.gh, g8, self.cmp.gw, g8).sum(axis=(1, 3), dtype=np.int32)
                tick = cursor_cells & (d2 > self.tick_tol)
            self.hot.add_pair(r, cells, e, local, small, tick)
        out: list[Decision] = []
        limit = (self.n - 1 - self.lookahead) if self.windowed else (self.n - 1)
        while self.next_k <= limit:
            out.extend(self._process(self.next_k))
            self.next_k += 1
        return out

    def finish(self) -> list[Decision]:
        if self.finished:
            return []
        self.finished = True
        out: list[Decision] = []
        if self.n == 0:
            return out
        if self.windowed:
            self.hot.flush()
        while self.next_k < self.n:
            out.extend(self._process(self.next_k))
            self.next_k += 1
        last = self.n - 1
        if self.pending is not None:
            out.append(self._commit(last, self.pending, "final", self.gray(last), last))
        if self._seg is not None:
            self._close_seg(self.n)
        return out

    # -- internals
    def _masks(self, k: int) -> tuple[np.ndarray | None, int, float]:
        if not self.windowed:
            return self.static_active, self.n_static_active, 0.0
        cells = self.hot.mask_for(k)
        if self.hot.version != self._hot_key:
            self._hot_key = self.hot.version
            if cells.any():
                g = self.cmp.cell
                hp = np.repeat(np.repeat(cells, g, axis=0), g, axis=1)
                if self.static is not None:
                    hp = hp | self.static
                self._active = ~hp
                self._n_active = int(np.count_nonzero(self._active))
            else:
                self._active = self.static_active
                self._n_active = self.n_static_active
            self._hot_frac = float(np.count_nonzero(cells)) / self.cmp.ncells
        self._track_seg(k, cells)
        return self._active, self._n_active, self._hot_frac

    def _track_seg(self, k: int, cells: np.ndarray) -> None:
        seg = self._seg
        if not cells.any():
            if seg is not None:
                self._close_seg(k)
            return
        if seg is not None:
            if seg.first is cells:
                seg.k1 = k
                return
            inter = np.count_nonzero(seg.first & cells)
            union = np.count_nonzero(seg.first | cells)
            if union and inter / union >= float(self.p["hot_segment_jaccard"]):
                seg.union |= cells
                seg.k1 = k
                return
            self._close_seg(k)
        self._seg = _HotSeg(k0=k, first=cells, union=cells.copy(), k1=k)

    def _close_seg(self, k_end: int) -> None:
        seg = self._seg
        self._seg = None
        if seg is None:
            return
        u = seg.union
        ys, xs = np.nonzero(u)
        gh, gw = u.shape
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        bbox = [round(x0 / gw, 4), round(y0 / gh, 4), round((x1 - x0) / gw, 4), round((y1 - y0) / gh, 4)]
        self.hot_segments.append({"k0": seg.k0, "k1": k_end, "frac": round(float(u.mean()), 4),
                                  "bbox": bbox, "cells": pack_cells(u)})

    def _track_camera(self, k: int, hot_frac: float) -> None:
        if hot_frac >= self.cam_hot:
            if self.camera_spans and self.camera_spans[-1][1] == k:
                self.camera_spans[-1][1] = k + 1
            else:
                self.camera_spans.append([k, k + 1])

    def _process(self, k: int) -> list[Decision]:
        active, n_active, hot_frac = self._masks(k)
        self.hot_fracs.append(hot_frac)
        self._track_camera(k, hot_frac)
        g = self.gray(k)
        cmp = self.cmp
        ref0 = self.ref                        # the reference before any commit at this sample
        P = cmp.classify(g, self.gray(k - 1), active, n_active) if k > 0 else None
        R = FULL_CHANGE if self.ref is None else cmp.classify(g, self.ref, active, n_active)
        self._recent[k] = R
        self._recent.pop(k - self.settle_pairs - 4, None)
        event = ""
        if R.cls != CHANGE:
            if self.pending is not None:
                event = "revert"
            self.pending = None
        elif self.pending is None:
            self.pending = k
            self.pending_px = R.px if self.ref is not None else self.cmp.npx
            self.s0 = k
            self.s0_img = g.copy()
            self.stable = 0
            event = "pending"
        out: list[Decision] = []
        W = None
        if self.pending is not None and k > self.pending:
            # a change at most settle_noise_rel of the pending change (and <= the scatter budget)
            # does not break the settle: UI flicker next to a big change (a dialog closing)
            tol = min(self.noise_rel * self.pending_px, self.cmp.scatter_px)
            quiet = P is not None and (P.cls != CHANGE or P.px <= tol)
            if quiet and k - self.s0 >= 2:
                W = cmp.classify(g, self.s0_img, active, n_active, strict=True)
                quiet = W.cls != CHANGE or W.px <= tol
            if quiet:
                self.stable = k - self.s0
            else:
                self.s0 = k
                self.s0_img = g.copy()
                self.stable = 0
            if self.stable >= self.settle_pairs:
                out.append(self._commit(k, self.pending, "settled", g, k))
                self.level = 0
            else:
                since_pending = self.ms(k) - self.ms(self.pending)
                since_last = self.ms(k) - self.ms(self.last_c)
                interval = min(self.mi_ms * (2 ** self.level), self.mmax_ms)
                if since_pending >= self.wait_ms and since_last >= interval:
                    out.append(self._commit(k, self.pending, "motion", g, k))
                    self.level = min(self.level + 1, 16)
        if not out and self.ref is not None and hot_frac >= self.cam_hot \
                and self.ms(k) - self.ms(self.last_c) >= self.hb_ms:
            hb = cmp.classify(g, self.ref, self.static_active, self.n_static_active)
            if hb.cls == CHANGE and hb.frac >= self.hb_min:
                start = self.pending if self.pending is not None else k
                out.append(self._commit(k, start, "heartbeat", g, k, change=hb, full=True))
        if k in self.watch:
            self.inspect[k] = {"P": P, "R": R, "W": W, "active": None if active is None else active.copy(),
                               "n_active": n_active, "cur": g.copy(),
                               "prev": self.gray(k - 1).copy() if k > 0 else None,
                               "ref": None if ref0 is None else ref0.copy(), "hot_frac": hot_frac,
                               "hot_pairs": self.hot.last_n_pairs if self.windowed else 0}
        if self.record_rows:
            e, cut = self.hot.pair_info(k) if self.windowed else (0.0, False)
            row = [k, self.ms(k), e, int(cut), int(e <= self.local_max), int(e <= self.small_max),
                   hot_frac, n_active / cmp.npx,
                   "" if P is None else P.cls, 0 if P is None else P.px, 0 if P is None else P.blobs,
                   R.cls, R.px, R.blobs, R.frac, self._bbox_frac(R.bbox) if R.bbox else None,
                   "pending" if self.pending is not None else "idle", self.stable, event, "",
                   0 if P is None else P.weak, R.weak]
            for d in out:
                row[18] = (row[18] + ";" if row[18] else "") + f"commit_{d.kind}"
            self.rows.append(row)
        return out

    def _bbox_frac(self, bbox: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = bbox
        return (round(x0 / self.aw, 4), round(y0 / self.ah, 4), round((x1 - x0) / self.aw, 4), round((y1 - y0) / self.ah, 4))

    def _commit(self, c: int, start: int, kind: str, img: np.ndarray, k: int,
                change: Classified | None = None, full: bool = False) -> Decision:
        n = len(self.decisions) + 1
        if full:
            active, n_active = self.static_active, self.n_static_active
        else:
            active, n_active = self._active, self._n_active
        if n == 1:
            kind, start = "first", 0
            frac, bbox, blobs = 1.0, (0.0, 0.0, 1.0, 1.0), 0
        else:
            ch = change if change is not None else self._recent.get(c, FULL_CHANGE)
            frac = round(float(ch.frac), 4)
            bbox = self._bbox_frac(ch.bbox) if ch.bbox else (0.0, 0.0, 1.0, 1.0)
            blobs = int(ch.blobs)
        same_as = None
        tc = self.ms(c)
        if self.revisit and n > 1 and n_active >= self.revisit_min_active:
            for scr in self.screens:
                if scr.n == self.prev_screen or tc - scr.t_ms > self.revisit_ms:
                    continue
                if self.cmp.classify(img, scr.gray, active, n_active).cls != CHANGE:
                    same_as = scr.n
                    break
        hot_frac = self.hot_fracs[c] if c < len(self.hot_fracs) else 0.0
        d = Decision(n=n, sample=c, start_sample=start, kind=kind, same_as=same_as, frac=frac,
                     bbox=tuple(float(v) for v in bbox), blobs=blobs, hot_frac=round(hot_frac, 4), committed_at=k)
        self.decisions.append(d)
        self.frame_tags[c] = f"F{n:04d}" + (f"=F{same_as:04d}" if same_as else "")
        if same_as is not None:
            for i, scr in enumerate(self.screens):
                if scr.n == same_as:
                    scr.t_ms = tc
                    self.screens.insert(0, self.screens.pop(i))
                    break
            self.prev_screen = same_as
        else:
            self.screens.insert(0, _Screen(n=n, gray=img.copy(), t_ms=tc))
            del self.screens[self.revisit_n:]
            self.prev_screen = n
        self.ref = img.copy()
        self.last_c = c
        self.pending = None
        self.stable = 0
        return d

    # -- reporting helpers
    def hot_segments_s(self, range_start_ms: int, range_end_ms: int) -> list[dict]:
        out = []
        for s in self.hot_segments:
            start = range_start_ms + self.ms(s["k0"])
            end = min(range_end_ms, range_start_ms + self.ms(s["k1"]))
            if end <= start:
                end = min(range_end_ms, start + 1)
            out.append({"start": round(start / 1000.0, 3), "end": round(end / 1000.0, 3), "frac": s["frac"],
                        "bbox": s["bbox"], "cells": s["cells"]})
        return out


# --- density warnings and suggested ignore regions (section 3.5) --------------------------------

def density_report(captured_ms: list[int], range_ms: tuple[int, int], window_ms: int = 60000) -> dict:
    """frames/min over the range and the busiest window of ``window_ms`` (by captured time)."""
    r0, r1 = range_ms
    dur_min = max(r1 - r0, 1) / 60000.0
    n = len(captured_ms)
    best, best_t = 0, r0
    j = 0
    for i, t in enumerate(captured_ms):
        while j < n and captured_ms[j] < t + window_ms:
            j += 1
        if j - i > best:
            best, best_t = j - i, t
    return {"frames_per_min": round(n / dur_min, 1), "max_per_min": int(best),
            "max_window": [round(best_t / 1000.0, 3), round(max(best_t + 1, min(best_t + window_ms, r1)) / 1000.0, 3)]}


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw = max(0.0, min(ax1, bx1) - max(a[0], b[0]))
    ih = max(0.0, min(ay1, by1) - max(a[1], b[1]))
    inter = iw * ih
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def suggest_ignore(bboxes: list[list[float] | None], n_frames: int, p: dict, cell: tuple[float, float]) -> list[dict]:
    """Cluster small change bboxes (area <= suggest_max_area) greedily in frame order (IoU vs the
    cluster's union >= suggest_iou). A cluster explaining >= suggest_min_share of the frames and
    >= suggest_min_frames frames becomes a region: union bbox + 1 cell, rounded to 2 decimals."""
    max_area = float(p["suggest_max_area"])
    clusters: list[dict] = []
    for b in bboxes:
        if not b or b[2] <= 0 or b[3] <= 0 or b[2] * b[3] > max_area:
            continue
        for cl in clusters:
            if _iou(cl["u"], b) >= float(p["suggest_iou"]):
                u = cl["u"]
                x0, y0 = min(u[0], b[0]), min(u[1], b[1])
                x1, y1 = max(u[0] + u[2], b[0] + b[2]), max(u[1] + u[3], b[1] + b[3])
                if (x1 - x0) * (y1 - y0) <= max_area:
                    cl["u"] = [x0, y0, x1 - x0, y1 - y0]
                cl["n"] += 1
                break
        else:
            clusters.append({"u": list(b), "n": 1})
    out = []
    if n_frames <= 0:
        return out
    cw, chh = cell
    for cl in clusters:
        share = cl["n"] / n_frames
        if cl["n"] >= int(p["suggest_min_frames"]) and share >= float(p["suggest_min_share"]):
            x, y, w, h = cl["u"]
            x0, y0 = max(0.0, x - cw), max(0.0, y - chh)
            x1, y1 = min(1.0, x + w + cw), min(1.0, y + h + chh)
            x0r, y0r = math.floor(x0 * 100) / 100, math.floor(y0 * 100) / 100
            x1r, y1r = min(1.0, math.ceil(x1 * 100) / 100), min(1.0, math.ceil(y1 * 100) / 100)
            out.append({"region": [round(x0r, 2), round(y0r, 2), round(x1r - x0r, 2), round(y1r - y0r, 2)],
                        "explains": round(share, 2)})
    out.sort(key=lambda s: -s["explains"])
    return out


def match_events(frames: list[tuple[float, float]], truth: list[float], tol: float) -> dict:
    """Match truth event times to frames given as (start, captured_at) seconds. A truth time t is
    found when some frame has |start - t| <= tol or |captured_at - t| <= tol (each frame matches
    at most one truth time, greedily in time order). Returns found/missed/extra and recall."""
    used: set[int] = set()
    found, missed = [], []
    for t in sorted(truth):
        best, best_d = None, None
        for i, (s0, c) in enumerate(frames):
            if i in used:
                continue
            dd = min(abs(s0 - t), abs(c - t))
            if dd <= tol + 1e-9 and (best_d is None or dd < best_d):
                best, best_d = i, dd
        if best is None:
            missed.append(t)
        else:
            used.add(best)
            found.append((t, frames[best][1]))
    extra = [frames[i][1] for i in range(len(frames)) if i not in used]
    return {"found": found, "missed": missed, "extra": extra, "recall": (len(found) / len(truth)) if truth else 1.0}
