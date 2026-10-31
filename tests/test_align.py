"""va.align: frame <-> cue overlap on integer milliseconds (spec section 5)."""
from va.align import CueIndex, align, frames_in_range, overlaps

FRAMES = [(0, 10_000), (10_000, 20_000), (20_000, 30_000), (30_000, 30_000)]  # last one empty (allowed)


def test_boundary_equality_is_excluded_on_both_sides():
    cues = [(5_000, 10_000),    # ends exactly where F2 starts -> only F1
            (10_000, 12_000),   # starts exactly where F1 ends -> only F2
            (9_999, 10_001)]    # straddles the boundary -> F1 and F2
    frame_cues, cue_frames = align(FRAMES[:3], cues)
    assert cue_frames == [[0], [1], [0, 1]]
    assert frame_cues == [[0, 2], [2, 1], []]  # each frame's cues ordered by (start, end, index)


def test_zero_length_cue_on_a_boundary_matches_no_frame():
    _, cue_frames = align(FRAMES[:3], [(10_000, 10_000), (15_000, 15_000)])
    assert cue_frames[0] == []
    assert cue_frames[1] == [1]  # strictly inside a frame it still overlaps (start < end, end > start)


def test_nested_overlaps_and_a_cue_spanning_frames():
    cues = [(1_000, 25_000),   # long cue over three frames
            (2_000, 3_000),    # nested inside it
            (2_500, 2_800),    # nested twice (different speaker in Teams)
            (21_000, 22_000)]
    frame_cues, cue_frames = align(FRAMES[:3], cues)
    assert cue_frames == [[0, 1, 2], [0], [0], [2]]
    assert frame_cues == [[0, 1, 2], [0], [0, 3]]


def test_frames_without_cues_and_cues_outside_the_frames():
    frame_cues, cue_frames = align(FRAMES[:3], [(40_000, 41_000), (-5_000, -1_000)])
    assert frame_cues == [[], [], []]
    assert cue_frames == [[], []]
    assert align([], [(0, 1)]) == ([], [[]])


def test_unsorted_cues_and_non_contiguous_frames():
    frames = [(0, 5_000), (8_000, 9_000), (9_000, 12_000)]
    cues = [(11_000, 13_000), (4_000, 8_500), (5_000, 8_000)]  # the last one falls in the gap
    frame_cues, cue_frames = align(frames, cues)
    assert cue_frames == [[2], [0, 1], []]
    assert frame_cues == [[1], [1], [0]]


def test_overlaps_and_range_clipping():
    assert overlaps(0, 10, 5, 15) and not overlaps(0, 10, 10, 20) and not overlaps(10, 20, 0, 10)
    assert frames_in_range(FRAMES[:3], 10_000, 20_000) == [1]
    assert frames_in_range(FRAMES[:3], 9_000, 20_001) == [0, 1, 2]
    assert frames_in_range(FRAMES[:3], 30_000, 40_000) == []


def test_cue_index_spans_and_near():
    idx = CueIndex([(10_000, 12_000), (2_000, 3_000), (20_000, 30_000)])
    assert idx.spans(11_000) and idx.spans(25_000)
    assert not idx.spans(10_000) and not idx.spans(12_000) and not idx.spans(5_000)
    assert idx.near(13_000, 1_000) is False and idx.near(12_900, 1_000) is True
    assert idx.near(9_000, 1_000) is False and idx.near(9_001, 1_000) is True
    assert CueIndex([]).spans(0) is False and CueIndex([]).near(0, 1_000) is False
