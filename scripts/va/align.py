"""Frame <-> cue alignment (spec section 5, WP2). Pure functions on integer milliseconds.

Rule: a cue overlaps a frame when ``cue.start < frame.end and cue.end > frame.start``; equality
at a boundary is excluded on both sides, so a cue that ends exactly where a frame starts does
not belong to it (and a zero-length cue on a boundary matches no frame, which is why the cues
stage enforces a minimum duration).
"""
from __future__ import annotations

import bisect
from collections.abc import Sequence

__all__ = ["CueIndex", "align", "frames_in_range", "overlaps"]

Interval = tuple[int, int]


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Open-interval overlap: touching intervals do not overlap."""
    return a_start < b_end and a_end > b_start


def frames_in_range(frames: Sequence[Interval], t0: int, t1: int) -> list[int]:
    """Indexes of the frames that overlap [t0, t1] (a trecho), in order."""
    return [i for i, (s, e) in enumerate(frames) if overlaps(s, e, t0, t1)]


def align(frames: Sequence[Interval], cues: Sequence[Interval]) -> tuple[list[list[int]], list[list[int]]]:
    """-> (frame_cues, cue_frames).

    ``frames``: (start_ms, end_ms) sorted by start and not overlapping each other (frames.json
    intervals are contiguous). ``cues``: (start_ms, end_ms) in any order, possibly overlapping.
    frame_cues[i] lists the cues overlapping frame i, ordered by (cue start, cue end, index);
    cue_frames[j] lists the frames overlapping cue j, ascending.
    """
    starts = [s for s, _ in frames]
    frame_cues: list[list[int]] = [[] for _ in frames]
    cue_frames: list[list[int]] = []
    for j, (cs, ce) in enumerate(cues):
        hit: list[int] = []
        i = max(0, bisect.bisect_right(starts, cs) - 1)
        while i < len(frames) and frames[i][0] < ce:
            if overlaps(cs, ce, frames[i][0], frames[i][1]):
                hit.append(i)
                frame_cues[i].append(j)
            i += 1
        cue_frames.append(hit)
    for lst in frame_cues:
        lst.sort(key=lambda j: (cues[j][0], cues[j][1], j))
    return frame_cues, cue_frames


class CueIndex:
    """Fast "is there speech here?" queries over cue intervals (batch boundary scores).

    ``spans(t)``: some cue has start < t < end (a cut at t would split it).
    ``near(t, r)``: some cue overlaps the window (t - r, t + r).
    Both are O(log n) with a prefix maximum of cue ends over cues sorted by start.
    """

    def __init__(self, cues: Sequence[Interval]):
        ordered = sorted(cues)
        self._starts = [s for s, _ in ordered]
        self._max_end: list[int] = [-(1 << 62)]
        for _, e in ordered:
            self._max_end.append(max(self._max_end[-1], e))

    def _any(self, start_before: int, end_after: int) -> bool:
        """Some cue with start < start_before and end > end_after."""
        k = bisect.bisect_left(self._starts, start_before)
        return self._max_end[k] > end_after

    def spans(self, t: int) -> bool:
        return self._any(t, t)

    def near(self, t: int, radius: int) -> bool:
        return self._any(t + radius, t - radius)
