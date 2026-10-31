"""va.batching: deterministic greedy batch planner and token estimates (spec section 5)."""
import pytest

from va import BATCH_DEFAULTS
from va.align import align
from va.batching import batch_cues, effective_batch_params, image_tokens, plan_batches, text_tokens


def frames_n(n, dur_ms=5_000, frac=0.1, fracs=None):
    fracs = fracs or {}
    return [(i * dur_ms, (i + 1) * dur_ms, fracs.get(i, frac)) for i in range(n)]


def plan(frames, cues=(), **overrides):
    cues = list(cues)
    frame_cues, _ = align([(s, e) for s, e, _ in frames], [(s, e) for s, e, _ in cues])
    return plan_batches(frames, cues, frame_cues, effective_batch_params(overrides))


def test_exact_multiples_and_empty():
    assert plan(frames_n(40)) == [(0, 20), (20, 40)]
    assert plan(frames_n(20)) == [(0, 20)]
    assert plan([]) == []
    assert plan(frames_n(5)) == [(0, 5)]  # a single short batch stays as it is


def test_tail_merge_up_to_25_frames():
    assert plan(frames_n(45)) == [(0, 20), (20, 45)]


def test_tail_split_evenly_above_25_frames():
    assert plan(frames_n(46)) == [(0, 20), (20, 33), (33, 46)]  # 26 frames -> 13 + 13
    assert plan(frames_n(47)) == [(0, 20), (20, 34), (34, 47)]  # 27 frames -> ceil 14 + 13


def test_text_budget_limits_the_batch():
    frames = frames_n(10)
    cues = [(i * 5_000 + 100, i * 5_000 + 4_900, 3_000) for i in range(10)]  # one 3,000-char cue per frame
    assert plan(frames, cues) == [(0, 2), (2, 4), (4, 6), (6, 10)]  # tail of 2 merged (spec: frames only)


def test_a_cue_spanning_frames_counts_once():
    frames = frames_n(30)
    cues = [(0, 150_000, 7_000)]  # 7,000 distinct chars over all frames: never breaks the 8,000 budget
    assert plan(frames, cues) == [(0, 20), (20, 30)]


def test_single_frame_over_budget_is_its_own_batch():
    frames = frames_n(25)
    cues = [(100, 4_900, 9_000)]
    out = plan(frames, cues)
    assert out == [(0, 1), (1, 25)]
    frame_cues, _ = align([(s, e) for s, e, _ in frames], [(s, e) for s, e, _ in cues])
    assert batch_cues(0, 1, frame_cues) == [0] and batch_cues(1, 25, frame_cues) == []


def test_soft_cut_prefers_a_scene_change():
    assert plan(frames_n(30, fracs={15: 0.6})) == [(0, 15), (15, 30)]


def test_soft_cut_tie_goes_to_the_later_boundary():
    assert plan(frames_n(30, fracs={13: 0.6, 17: 0.6})) == [(0, 17), (17, 30)]


def test_soft_cut_prefers_silence_and_all_zero_cuts_at_the_limit():
    frames = frames_n(30)
    gap_at_14 = [(0, 69_000, 50), (71_500, 150_000, 50)]  # speech everywhere except around 70 s (frame 14)
    assert plan(frames, gap_at_14) == [(0, 14), (14, 30)]
    speech_everywhere = [(0, 150_000, 50)]
    assert plan(frames, speech_everywhere) == [(0, 20), (20, 30)]


def test_long_previous_frame_scores():
    frames = [(i * 5_000, (i + 1) * 5_000, 0.1) for i in range(30)]
    frames = [(st if i < 15 else st + 40_000, en if i < 14 else en + 40_000, fr) for i, (st, en, fr) in enumerate(frames)]
    # frame 14 now lasts 45 s: the cut before frame 15 gets +1 and wins over the plain candidates
    assert plan(frames, [(0, 1_000, 10)]) == [(0, 15), (15, 30)]


def test_span_limit():
    frames = frames_n(20, dur_ms=100_000)
    assert plan(frames) == [(0, 9), (9, 20)]  # 9 frames = 900 s; the 2-frame tail merges into the previous
    long_first = [(0, 1_000_000, 0.1)] + [(1_000_000 + i * 5_000, 1_005_000 + i * 5_000, 0.1) for i in range(29)]
    assert plan(long_first) == [(0, 1), (1, 21), (21, 30)]  # a frame longer than the span is a batch alone


def test_no_soft_cuts_cuts_at_the_limit():
    assert plan(frames_n(30, fracs={15: 0.6}), soft_cuts=False) == [(0, 20), (20, 30)]
    assert plan(frames_n(30, fracs={15: 0.6}), no_soft_cuts=True) == [(0, 20), (20, 30)]


def test_smaller_max_frames_derives_soft_min_and_tail():
    p = effective_batch_params({"max_frames": 10})
    assert (p["soft_min"], p["tail_min_frames"], p["tail_merge_max"]) == (6, 4, 12)
    assert plan(frames_n(23), max_frames=10) == [(0, 10), (10, 17), (17, 23)]  # tail 3 < 4; 13 > 12 -> split 7 + 6
    assert plan(frames_n(22), max_frames=10) == [(0, 10), (10, 22)]  # tail 2; 12 <= 12 -> merged


def test_effective_batch_params():
    p = effective_batch_params()
    assert p == {**BATCH_DEFAULTS, "range": None}
    assert effective_batch_params(p) == p
    q = effective_batch_params({"batch_frames": 15, "batch_text": 5000, "batch_span": 600, "trecho": "00:01:00-00:02:30.5"})
    assert (q["max_frames"], q["max_text_chars"], q["max_span_s"], q["range"]) == (15, 5000, 600.0, [60.0, 150.5])
    assert (q["soft_min"], q["tail_min_frames"], q["tail_merge_max"]) == (9, 6, 18)
    assert effective_batch_params({"range": [10, 20.0004]})["range"] == [10.0, 20.0]
    assert effective_batch_params({"range": None, "max_frames": None})["max_frames"] == 20
    with pytest.raises(ValueError, match="unknown batch parameter"):
        effective_batch_params({"frames_per_batch": 10})
    with pytest.raises(ValueError):
        effective_batch_params({"range": [30, 10]})
    with pytest.raises(ValueError):
        effective_batch_params({"max_frames": 0})
    with pytest.raises(ValueError):
        effective_batch_params({"max_frames": 2.5})


def test_token_estimates():
    assert image_tokens(1568, 882) == 1792 and image_tokens(1568, 1018) == 2072
    assert image_tokens(1560, 780) == 1568
    assert text_tokens(6100) == 1743 and text_tokens(7) == 2 and text_tokens(0) == 0
