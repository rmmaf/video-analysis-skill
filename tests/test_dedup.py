"""Unit tests for va.dedup (pure numpy, no ffmpeg): classify paths, the cursor predicate,
components, pair activity, hot mask windows/hysteresis/cuts, the state machine (settle, motion
backoff, revert, revisit, heartbeat, final), params, density and suggestions."""
import json

import numpy as np
import pytest

from va import DEDUP_DEFAULTS, OUTPUT_DEFAULTS, SENSITIVITY_PRESETS
from va.dedup import (CHANGE, CURSOR, SAME, Analyzer, Comparator, HotTracker, density_report,
                      effective_frame_params, frame_params_for_output, label8, match_events, pack_cells,
                      static_mask_from_regions, suggest_ignore, unpack_cells)

AW, AH = 640, 360
P = effective_frame_params({})


# --- helpers -------------------------------------------------------------------------------

def screen(seed=1, bg=235):
    """A static 'document' screen: light background with rows of dark text-like dashes."""
    rng = np.random.default_rng(seed)
    g = np.full((AH, AW), bg, np.uint8)
    for y in range(30, AH - 30, 14):
        x = 40
        while x < AW - 80:
            w = int(rng.integers(6, 30))
            g[y:y + 6, x:x + w] = 40
            x += w + int(rng.integers(3, 8))
    return g


def pointer(g, x, y):
    """Arrow-like pointer, 7x11 px (cursor-sized at 640x360)."""
    out = g.copy()
    for i in range(11):
        out[y + i, x:x + max(1, min(7, i // 1 - 2) if i > 2 else 1)] = 0
    out[y:y + 11, x] = 0
    return out


def block(g, x, y, w, h, v=0):
    out = g.copy()
    out[y:y + h, x:x + w] = v
    return out


def run(frames, params=None, **kw):
    an = Analyzer(params if params is not None else P, (AW, AH), **kw)
    for f in frames:
        an.push(f)
    an.finish()
    return an


def kinds(an):
    return [(d.sample, d.kind, d.same_as) for d in an.decisions]


# --- label8 ----------------------------------------------------------------------------------

def _bfs_label(mask):
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    n = 0
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not lab[y, x]:
                n += 1
                stack = [(y, x)]
                lab[y, x] = n
                while stack:
                    cy, cx = stack.pop()
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                                lab[ny, nx] = n
                                stack.append((ny, nx))
    return lab, n


@pytest.mark.parametrize("seed,density", [(0, 0.1), (1, 0.3), (2, 0.5), (3, 0.02)])
def test_label8_matches_bfs(seed, density):
    rng = np.random.default_rng(seed)
    m = rng.random((45, 80)) < density
    lab, n = label8(m)
    ref, rn = _bfs_label(m)
    assert n == rn
    # same partition, labels numbered by first appearance in row-major order
    assert np.array_equal(lab > 0, m)
    assert np.array_equal(lab, ref)


def test_label8_long_snake_and_empty():
    m = np.zeros((20, 60), bool)
    m[1, 1:59] = True
    m[1:19, 58] = True
    m[18, 1:59] = True
    lab, n = label8(m)
    assert n == 1 and (lab[m] == 1).all()
    assert label8(np.zeros((5, 5), bool))[1] == 0


# --- cursor predicate and classify -------------------------------------------------------------

def test_cursor_box_sizes():
    c = Comparator(P, AW, AH)
    assert (c.cw, c.ch) == (12, 18)          # spec 3.2: ceil(0.030*360)+1, ceil(0.045*360)+1
    assert c.budget == pytest.approx(0.6 * 12 * 18)


@pytest.mark.parametrize("w,h,px,expected,strict_expected", [
    (7, 11, 40, True, True),      # pointer
    (2, 16, 20, True, True),      # I-beam / caret
    (12, 18, 120, True, True),    # box limit
    (22, 12, 120, True, False),   # old+new pointer merged horizontally (pointer tall)
    (22, 6, 60, False, False),    # a text line fragment: not pointer tall
    (12, 34, 150, True, True),    # merged vertically
    (18, 26, 200, True, False),   # merged diagonally
    (30, 18, 150, False, False),  # too wide
    (12, 18, 300, False, False),  # too many px for the budget
])
def test_cursor_predicate_table(w, h, px, expected, strict_expected):
    c = Comparator(P, AW, AH)
    a = lambda v: np.array([v])  # noqa: E731
    assert bool(c.cursor_like(a(w), a(h), a(px))[0]) is expected
    assert bool(c.cursor_like(a(w), a(h), a(px), strict=True)[0]) is strict_expected


def test_classify_same_noise_cursor_change():
    c = Comparator(P, AW, AH)
    g = screen()
    assert c.classify(g, g).cls == SAME
    # weak-only speckle (diff 20 < t_hi): noise -> same
    n = g.copy()
    n[100:102, 100:102] = np.clip(n[100:102, 100:102].astype(int) + 20, 0, 255)
    assert c.classify(n, g).cls == SAME
    # pointer moved: two pointer blobs -> cursor
    a, b = pointer(g, 200, 100), pointer(g, 300, 200)
    r = c.classify(b, a)
    assert r.cls == CURSOR and r.blobs == 2
    # a dark word appears on the background (40x6 px, strong change) -> change
    w = block(g, 300, 320, 40, 6, 0)
    r = c.classify(w, g)
    assert r.cls == CHANGE and not r.fast and r.bbox == (300, 320, 340, 326)
    # a soft, weak-only change of that size on the blank margin is noise
    assert c.classify(block(g, 0, 2, 38, 6, 255), g).cls == SAME
    assert c.classify(block(g, 0, 2, 38, 6, 0), g).cls == CHANGE
    # big change -> fast path
    r = c.classify(screen(seed=9), g)
    assert r.cls == CHANGE and r.fast and r.frac > 0.05


def test_classify_scatter_rule_and_strict():
    c = Comparator(P, AW, AH)
    g = np.full((AH, AW), 235, np.uint8)
    s = g.copy()
    for i in range(5):                      # 5 tiny strong blobs far apart, 16 px each
        s[20 + i * 60:24 + i * 60, 20 + i * 100:24 + i * 100] = 0
    r = c.classify(s, g)
    assert r.blobs == 5 and r.cls == CURSOR            # <= 6 blobs, total px <= 2 x budget
    assert c.classify(s, g, strict=True).cls == CHANGE  # strict: no scatter allowance
    t = s.copy()
    for i in range(3):                      # 8 blobs: over the scatter limit
        t[300:304, 400 + i * 60:404 + i * 60] = 0
    assert c.classify(t, g).cls == CHANGE


def test_classify_soft_low_contrast_blob_is_noise():
    c = Comparator(P, AW, AH)
    g = np.full((AH, AW), 200, np.uint8)
    s = g.copy()
    s[100:130, 100:130] = 225               # 900 px, all weak (diff 25), no strong px
    s[110, 110] = 255                       # 1 strong px only: below the 10% strong ratio
    assert c.classify(s, g).cls == SAME


def test_classify_respects_active_mask():
    c = Comparator(P, AW, AH)
    g = screen()
    w = block(g, 300, 300, 60, 10, 255)
    active = np.ones((AH, AW), bool)
    active[290:320, 290:370] = False
    assert c.classify(w, g, active).cls == SAME
    assert c.classify(w, g, np.zeros((AH, AW), bool)).cls == SAME


# --- pair activity and small activity ------------------------------------------------------------

def test_pair_cells_drops_cursor_and_flags_local():
    c = Comparator(P, AW, AH)
    g = screen()
    cells, e, local, counts, cur = c.pair_cells(pointer(g, 300, 152), g)
    assert e == 0.0 and not cells.any() and cur.any()   # the pointer only shows up as cursor cells
    w = block(g, 96, 96, 64, 32, 255)                # aligned to the 8-px cells: 8 x 4 cells
    cells, e, local, counts, cur = c.pair_cells(w, g)
    assert cells.sum() == 8 * 4 and np.array_equal(local, cells) and e == pytest.approx(32 / 3600)
    big = c.pair_cells(screen(seed=5), g)
    assert big[1] > 0.25 and not big[2].any()     # e > local_max: no local cells


def test_small_activity_thin_ring_vs_dense_area():
    c = Comparator(P, AW, AH)
    ring = np.zeros((45, 80), bool)
    ring[2, 2:60] = ring[30, 2:60] = True
    ring[2:31, 2] = ring[2:31, 59] = True          # thin outline: 172 cells (4.8% of the cells)
    counts = np.where(ring, 10, 0).astype(np.int32)
    assert c.small_activity(ring, counts).sum() == ring.sum()
    dense = np.zeros((45, 80), bool)
    dense[5:20, 5:20] = True                       # 225 dense cells (6.25%)
    assert not c.small_activity(dense, np.where(dense, 10, 0).astype(np.int32)).any()
    tiny = np.zeros((45, 80), bool)
    tiny[40:42, 70:74] = True
    assert c.small_activity(tiny, np.where(tiny, 10, 0).astype(np.int32)).sum() == 8


# --- hot tracker --------------------------------------------------------------------------------

def _tracker(p=None):
    p = p or P
    c = Comparator(p, AW, AH)
    return HotTracker(p, c, 2.0), c


def stream(t, pairs):
    """Feed pairs 1..n (cells, e, local, small) and query the masks in streaming order, as the
    Analyzer does (mask_for(k) once pairs up to k+H are final). Returns {k: mask}."""
    masks, k = {}, 0
    for p, (cells, e, local, small) in enumerate(pairs, start=1):
        t.add_pair(p, cells, e, local, small)
        while k + t.H <= t.finalized:
            masks[k] = t.mask_for(k).copy()
            k += 1
    t.flush()
    while k <= len(pairs):
        masks[k] = t.mask_for(k).copy()
        k += 1
    return masks


def test_hot_tracker_duty_on_and_release_with_hysteresis():
    t, c = _tracker()
    cell = np.zeros((45, 80), bool)
    cell[10, 10] = True
    z = np.zeros_like(cell)
    # active in every pair for pairs 1..80, then quiet
    pairs = [(cell, 0.0003, cell, z) if p <= 80 else (z, 0.0, z, z) for p in range(1, 200)]
    m = stream(t, pairs)
    assert m[0][10, 10] and m[40][10, 10]   # centered window: on from the start
    assert m[85][10, 10]                    # duty 0.46 < on (0.5) but >= keep (0.3): still on
    assert not m[150][10, 10]
    assert m[40][9, 9] and m[40][11, 11]    # dilated by one cell


def test_hot_tracker_window_split_at_cut_and_min_pairs():
    t, c = _tracker()
    cell = np.zeros((45, 80), bool)
    cell[5, 5] = True
    z = np.zeros_like(cell)
    full = np.ones_like(cell)
    pairs = [(cell, 0.0003, cell, z) if p <= 79 else (full, 0.95, z, z) if p == 80 else (z, 0.0, z, z)
             for p in range(1, 161)]                # layout change at pair 80, then calm -> cut
    m = stream(t, pairs)
    assert 80 in t.cuts
    assert m[70][5, 5]
    assert not any(m[k][5, 5] for k in range(80, 161))   # no leak into the new layout


def test_hot_tracker_cut_needs_calm_within_lookahead():
    p = effective_frame_params({"cut_calm_pairs": 4})
    t, c = _tracker(p)
    z = np.zeros((45, 80), bool)
    full = np.ones_like(z)
    es = [0.0] * 10 + [0.9, 0.4, 0.3, 0.1] + [0.0] * 10 + [0.9] + [0.4] * 10
    stream(t, [(full if e > 0.2 else z, e, z, z) for e in es])
    assert t.cuts == [11]                         # calm 3 pairs later; the last big pair never calms


def test_hot_tracker_recurrence_criterion():
    t, c = _tracker()
    cell = np.zeros((45, 80), bool)
    cell[20, 40] = True
    z = np.zeros_like(cell)
    pairs = [(cell, 0.0003, z, cell) if p % 6 == 0 else (z, 0.0, z, z) for p in range(1, 241)]
    m = stream(t, pairs)                          # one small hit every 3 s = 20/min
    assert m[120][20, 40]
    t2, _ = _tracker()
    m2 = stream(t2, [(cell, 0.0003, z, cell) if p % 30 == 0 else (z, 0.0, z, z) for p in range(1, 241)])
    assert not m2[120][20, 40]                    # 4/min: below the on threshold (8/min)


# --- analyzer: state machine ----------------------------------------------------------------------

def test_static_video_gives_one_first_frame():
    g = screen()
    an = run([g] * 40)
    assert kinds(an) == [(2, "first", None)] and an.decisions[0].start_sample == 0


def test_settled_change_and_capture_time():
    a, b = screen(1), screen(1)
    b = block(b, 100, 100, 200, 40, 255)
    an = run([a] * 20 + [b] * 20)
    # detected at 20, stable for 1 s (2 pairs), captured at the end of the stable run
    assert kinds(an) == [(2, "first", None), (22, "settled", None)]
    assert an.decisions[1].start_sample == 20


def test_fade_in_first_frame_captures_first_stable_sample():
    final = screen(3)
    frames = [(final.astype(np.float32) * f).astype(np.uint8) for f in (0.2, 0.4, 0.6, 0.8)] + [final] * 20
    an = run(frames)
    d = an.decisions[0]
    assert d.kind == "first" and d.start_sample == 0 and d.sample == 6   # after the fade-in


def test_transient_flash_is_ignored():
    a = screen(2)
    tip = block(a, 300, 200, 120, 30, 255)
    an = run([a] * 10 + [tip] + [a] * 10)
    assert kinds(an) == [(2, "first", None)]


def test_cursor_wandering_is_ignored():
    a = screen(4)
    frames = [pointer(a, 50 + 13 * i, 60 + 7 * i) for i in range(40)]
    assert kinds(run(frames)) == [(2, "first", None)]


def _scroll(seconds, base):
    frames = [base] * 4
    for i in range(int(seconds * 2)):
        frames.append(np.roll(base, -3 * (i + 1), axis=0))
    return frames + [frames[-1]] * 10


def test_scroll_gives_motion_frames_with_backoff():
    an = run(_scroll(40, screen(5)))              # 40 s scroll: duty_all stays below 0.85
    motion = [d.sample for d in an.decisions if d.kind == "motion"]
    assert len(motion) == 3
    assert list(np.diff(motion) / 2.0) == [8.0, 16.0]   # 4 -> 8 -> 16 s spacing
    assert an.decisions[-1].kind == "settled" and an.decisions[-1].sample == 83 + 2   # last scroll frame + 1 s


def test_motion_backoff_caps_at_max_interval():
    p = effective_frame_params({"hot_mode": "off"})
    an = run(_scroll(120, screen(5)), p)
    motion = [d.sample for d in an.decisions if d.kind == "motion"]
    gaps = list(np.diff(motion) / 2.0)
    assert gaps[:4] == [8.0, 16.0, 30.0, 30.0]


def test_long_continuous_change_counts_as_video():
    an = run(_scroll(90, screen(5)))              # continuous for 90 s: duty_all >= 0.85 -> hot
    assert max(an.hot_fracs) > 0.5 and an.camera_spans


def test_typing_does_not_settle_until_it_stops():
    g = np.full((AH, AW), 235, np.uint8)
    g[100:120, 50:52] = 0
    frames = [g] * 6
    text = g.copy()
    for i in range(30):                            # 5 glyphs per second next to a tall caret
        text = text.copy()
        x = 60 + i * 9
        text[104:112, x:x + 6] = 30
        f = text.copy()
        f[100:120, x + 8:x + 10] = 0
        frames.append(f)
    frames += [frames[-1]] * 12
    an = run(frames)
    settled = [d.sample for d in an.decisions if d.kind == "settled"]
    assert settled == [36]                        # only after typing stops (sample 36)
    assert len(an.decisions) <= 4


def test_revisit_reuses_the_original_frame():
    a = screen(6)
    b = block(a, 100, 80, 300, 150, 255)
    an = run([a] * 10 + [b] * 10 + [a] * 10)
    assert kinds(an) == [(2, "first", None), (12, "settled", None), (22, "settled", 1)]
    no = run([a] * 10 + [b] * 10 + [a] * 10, effective_frame_params({"revisit": False}))
    assert kinds(no)[-1] == (22, "settled", None)


def test_pending_change_at_eof_is_committed_as_final():
    a = screen(7)
    b = block(a, 50, 50, 300, 100, 255)
    an = run([a] * 10 + [b])
    assert kinds(an)[-1] == (10, "final", None) and an.decisions[-1].start_sample == 10


def test_single_sample_video():
    an = run([screen(8)])
    assert kinds(an) == [(0, "first", None)]


def test_webcam_region_is_masked_and_mask_does_not_leak_after_cut():
    rng = np.random.default_rng(11)
    base = screen(9)
    frames = []
    for i in range(120):                           # 60 s with a live webcam tile
        f = base.copy()
        f[250:350, 500:630] = rng.integers(0, 255, (100, 130), dtype=np.uint8)
        frames.append(f)
    other = np.full((AH, AW), 30, np.uint8)         # layout change: dark screen, no webcam
    frames += [other] * 30
    changed = block(other, 520, 280, 80, 30, 255)   # change inside the former webcam area
    frames += [changed] * 30
    an = run(frames)
    ks = kinds(an)
    assert ks[0] == (2, "first", None)
    assert [k for k in ks[1:] if k[0] < 120] == []  # webcam noise produces no frames
    assert (122, "settled", None) in ks              # the layout change
    assert (152, "settled", None) in ks              # detected: no mask leak from the webcam


def test_camera_like_segment_degrades_to_heartbeat():
    rng = np.random.default_rng(12)
    tex = rng.integers(0, 255, (AH + 40, AW + 400), dtype=np.uint8)
    frames = [tex[10 + (i % 7):10 + (i % 7) + AH, 3 * i:3 * i + AW] for i in range(130)]
    an = run(frames)
    assert an.decisions[0].kind == "first"
    assert all(d.kind == "heartbeat" for d in an.decisions[1:])
    assert 2 <= len(an.decisions) <= 4
    assert an.camera_spans and an.camera_spans[0][1] - an.camera_spans[0][0] >= 100


def _timer_frames(seconds, caret=True, size=13):
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", size)
    except OSError:
        font = ImageFont.load_default(size=size)
    base = screen(3)
    out = []
    for k in range(int(seconds * 2)):
        t = 50 + k / 2
        im = Image.fromarray(base.copy())
        s = int(t)
        ImageDraw.Draw(im).text((560, 6), f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}", fill=0, font=font)
        a = np.asarray(im).copy()
        if caret and int(t * 1000 / 530) % 2 == 0:
            a[200:212, 300:302] = 0
        out.append(a)
    return out


def test_running_timer_is_a_ticker_and_masked():
    an = run(_timer_frames(180))
    assert kinds(an) == [(2, "first", None)]        # no false frame from the accumulating digits
    assert 0 < max(an.hot_fracs) < 0.01
    off = run(_timer_frames(180), effective_frame_params({"tick_on_duty": 2.0, "tick_keep_duty": 2.0}))
    assert len(off.decisions) > 5                   # without the ticker criterion: a frame every ~10 s


def test_blinking_caret_is_not_masked():
    frames = _timer_frames(60, caret=True)
    an = Analyzer(P, (AW, AH))
    an.watch = {60}
    for f in frames:
        an.push(f)
    an.finish()
    active = an.inspect[60]["active"]
    assert active is None or active[200:212, 290:312].all()   # the caret area stays visible


def test_static_ignore_region():
    a = screen(13)
    b = block(a, 560, 10, 70, 30, 255)               # clock area in the top right corner
    p = effective_frame_params({"ignore_regions": [[0.85, 0.0, 0.15, 0.15]]})
    assert kinds(run([a] * 10 + [b] * 10, p)) == [(2, "first", None)]
    assert len(run([a] * 10 + [b] * 10).decisions) == 2


CORNER = [0.85, 0.0, 0.15, 0.15]
CAM = [0.6, 0.0, 0.4, 0.4]


def _slow_cam_then_change():
    """A webcam window in the top right area from 5 s to 30 s (samples 10-59 at 2 fps) whose picture
    changes every 10 s, too seldom for the hot mask; then the screen alone and, from sample 70
    (35 s), a real change in the same area."""
    a = screen(13)
    cam = [block(a, 400, 10, 200, 100, 60 + 60 * (i // 20)) for i in range(50)]
    return [a] * 10 + cam + [a] * 10 + [block(a, 400, 10, 200, 100, 255)] * 20


@pytest.mark.parametrize("hot_mode", ["windowed", "off"])
def test_ignore_timeline_masks_the_region_only_during_its_span(hot_mode):
    frames = _slow_cam_then_change()
    assert len(run(frames, effective_frame_params({"hot_mode": hot_mode})).decisions) > 3   # the webcam leaks
    span = effective_frame_params({"hot_mode": hot_mode, "ignore_timeline": [[5.0, 30.0, *CAM]]})
    k = kinds(run(frames, span))
    assert k[0] == (2, "first", None) and len(k) == 2 and k[1][0] >= 70      # only the change after the span
    fixed = effective_frame_params({"hot_mode": hot_mode, "ignore_regions": [CAM]})
    assert kinds(run(frames, fixed)) == [(2, "first", None)]                  # a fixed region hides it too
    # the spans are seconds of the video: with a range starting at 10 s, the same samples are 15-40 s
    shifted = effective_frame_params({"hot_mode": hot_mode, "range": [10.0, None],
                                      "ignore_timeline": [[15.0, 40.0, *CAM]]})
    assert kinds(run(frames, shifted)) == k


def test_ignore_timeline_only_adds_to_the_hot_mask():
    """The hot tracker still sees the webcam inside a span: its hot mask keeps covering what changes
    around the window (here, a speaking ring that blinks just under it, outside the span's region),
    as without the timeline."""
    a = screen(13)
    frames = []
    for i in range(120):
        f = block(a, 400, 10, 200, 100, 60 + 40 * (i % 4))                   # webcam: changes every sample
        frames.append(block(f, 400, 112, 200, 4, 0 if (i // 20) % 2 == 0 else 255))   # ring, every 10 s
    window = [0.625, 0.0278, 0.3125, 0.2778]                                  # the webcam alone: y 10-110
    plain = run(frames)
    span = run(frames, effective_frame_params({"ignore_timeline": [[0.0, 60.0, *window]]}))
    assert len(plain.decisions) == len(span.decisions) == 1
    assert span.hot_fracs == plain.hot_fracs                                  # the same hot mask
    # masked in the pair activity too (as a fixed ignore region is), the webcam is not hot and the ring shows
    assert len(run(frames, effective_frame_params({"ignore_regions": [window]})).decisions) > 1


def test_ignore_timeline_spans_overlap_and_outside_the_range():
    an = Analyzer(effective_frame_params({"range": [10.0, None], "ignore_regions": [[0.0, 0.0, 0.1, 0.1]],
                                          "ignore_timeline": [[0.0, 5.0, *CORNER], [12.0, 20.0, *CORNER],
                                                              [15.0, 30.0, 0.0, 0.9, 0.5, 0.1]]}), (AW, AH))
    assert [(k0, k1) for k0, k1, _m in an.spans] == [(4, 20), (10, 40)]       # 0-5 s is before the range
    key, ignored, active, n = an._static(3)
    assert key == () and active is an.static_active
    key, ignored, active, n = an._static(12)                                   # both spans at 16 s
    assert key == (0, 1) and ignored[0, 0] and ignored[5, 600] and ignored[350, 10] and not ignored[180, 320]
    assert n == int(np.count_nonzero(active)) and an._static(12)[2] is active   # cached
    assert an._static(30)[0] == (1,) and not an._static(30)[1][5, 600]


def test_decisions_are_deterministic_and_rows_recorded():
    rng = np.random.default_rng(3)
    base = screen(14)
    frames = []
    for i in range(90):
        f = base if (i // 20) % 2 == 0 else block(base, 100, 100, 200, 60, 255)
        f = f.copy()
        f[300:340, 20:80] = rng.integers(0, 255, (40, 60), dtype=np.uint8)
        frames.append(f)
    a, b = run(frames), run(frames)
    assert kinds(a) == kinds(b)
    assert [r[:20] for r in a.rows] == [r[:20] for r in b.rows]
    assert len(a.rows) == 90


def test_min_capture_sample_never_exceeds_future_captures():
    base = screen(15)
    frames = [base] * 5 + [block(base, 50, 50, 100, 100, 255)] * 3 + [block(base, 50, 50, 300, 200, 255)] * 20
    an = Analyzer(P, (AW, AH))
    mins = []
    for f in frames:
        for d in an.push(f):
            assert d.sample >= min(mins) if mins else True
        mins.append(an.min_capture_sample())
    for d in an.finish():
        assert d.sample >= mins[-1] - 1


# --- params -----------------------------------------------------------------------------------

def test_effective_params_defaults_and_idempotent():
    p = effective_frame_params(None)
    assert p == effective_frame_params(p) == effective_frame_params({})
    assert p["sensitivity"] == "normal" and p["t_lo"] == 16 and p["sample_fps"] == OUTPUT_DEFAULTS["sample_fps"]
    assert set(DEDUP_DEFAULTS) <= set(p) and set(OUTPUT_DEFAULTS) <= set(p)
    json.dumps(p, allow_nan=False)
    assert "sequential" not in p


def test_effective_params_aliases_presets_and_validation():
    p = effective_frame_params({"sensitivity": "high", "settle": 0.5, "motion_interval": 2, "cursor_size": 0.06,
                                "ignore_region": "0.8,0.75,0.2,0.25", "no_revisit": True, "start": 10, "end": 70.5,
                                "sequential": True, "max_edge": 5000, "jpeg_q": 9, "sample_fps": None})
    assert (p["t_lo"], p["t_hi"]) == (SENSITIVITY_PRESETS["high"]["t_lo"], SENSITIVITY_PRESETS["high"]["t_hi"])
    assert p["settle_s"] == 0.5 and p["motion_interval_s"] == 2.0
    assert p["cursor_h_frac"] == 0.06 and p["cursor_w_frac"] == pytest.approx(0.04)
    assert p["ignore_regions"] == [[0.8, 0.75, 0.2, 0.25]] and p["revisit"] is False
    assert p["range"] == [10.0, 70.5] and p["sequential"] is True
    assert p["max_edge"] == 2000 and p["jpeg_q"] == 5 and p["sample_fps"] == 2.0
    assert "sequential" not in frame_params_for_output(p)
    assert effective_frame_params(p) == p
    with pytest.raises(ValueError):
        effective_frame_params({"no_such_param": 1})
    assert effective_frame_params({"no_such_param": 1, "t_lo": 14}, strict=False)["t_lo"] == 14
    with pytest.raises(ValueError):
        effective_frame_params({"sensitivity": "extreme"})
    with pytest.raises(ValueError):
        effective_frame_params({"range": [30, 10]})
    with pytest.raises(ValueError):
        effective_frame_params({"ignore_regions": [[0.5, 0.5, 0.0, 0.1]]})


def test_effective_params_ignore_timeline():
    p = effective_frame_params({"ignore_timeline": [{"start": 12, "end": 30.5, "region": "0.85,0,0.15,0.15"},
                                                    [0, 12, *CORNER], (0, 12, *CORNER)]})
    assert p["ignore_timeline"] == [[0.0, 12.0, *CORNER], [12.0, 30.5, *CORNER]]   # sorted, duplicates dropped
    assert effective_frame_params(p) == p and frame_params_for_output(p)["ignore_timeline"] == p["ignore_timeline"]
    json.dumps(p, allow_nan=False)
    # absent or empty: no key at all, so the frames key of the runs without a timeline does not change
    assert "ignore_timeline" not in effective_frame_params({"ignore_timeline": []})
    assert effective_frame_params({"ignore_timeline": None}) == effective_frame_params({})
    assert effective_frame_params({"ignore_timeline": [[0, 1, *CORNER]], "x": 1}, strict=False)["ignore_timeline"]
    for bad in ([[5, 5, *CORNER]], [[-1, 5, *CORNER]], [[0, 5, 0.9, 0.0, 0.2]], [{"start": 0, "end": 5}],
                [{"start": 0, "end": 5, "region": CORNER, "x": 1}], "0,5,0,0,1,1", [[0, 5, 0.5, 0.5, 0.0, 0.1]],
                [[0, float("nan"), *CORNER]], [[True, 5, *CORNER]]):
        with pytest.raises(ValueError):
            effective_frame_params({"ignore_timeline": bad})


def test_static_mask_and_cells_roundtrip():
    m = static_mask_from_regions([[0.5, 0.5, 0.25, 0.25]], 640, 360)
    assert m.sum() == 160 * 90 and m[180, 320] and not m[179, 319]
    assert static_mask_from_regions([], 640, 360) is None
    rng = np.random.default_rng(4)
    cells = rng.random((45, 80)) < 0.3
    assert np.array_equal(unpack_cells(pack_cells(cells), (45, 80)), cells)


# --- density, suggestions, matching ------------------------------------------------------------------

def test_density_report():
    caps = [0, 1000, 2000] + [70000 + 1500 * i for i in range(20)]
    d = density_report(caps, (0, 120000))
    assert d["frames_per_min"] == round(23 / 2, 1) and d["max_per_min"] == 20 and d["max_window"][0] == 70.0


def test_suggest_ignore_finds_recurrent_region():
    boxes = [[0.82, 0.1, 0.15, 0.2]] * 12 + [[0.1, 0.2, 0.5, 0.5]] * 3 + [[0.83, 0.12, 0.14, 0.18]] * 5
    s = suggest_ignore(boxes, 21, P, (1 / 80, 1 / 45))
    assert len(s) == 1 and s[0]["explains"] == round(17 / 21, 2)
    x, y, w, h = s[0]["region"]
    assert x <= 0.82 and y <= 0.1 and x + w >= 0.97 and y + h >= 0.3
    assert suggest_ignore(boxes[:5], 21, P, (1 / 80, 1 / 45)) == []


def test_match_events():
    m = match_events([(0.0, 0.0), (9.5, 10.0), (20.0, 21.0), (40.0, 40.5)], [0.0, 10.0, 20.0, 30.0], 1.0)
    assert m["missed"] == [30.0] and m["extra"] == [40.5] and m["recall"] == 0.75


def test_params_live_in_a_numpy_free_module():
    """prepare.py computes the frames cache key on a warm run without importing numpy:
    effective_frame_params lives in va.frame_params and dedup/extract re-export the same objects."""
    from va import extract, frame_params
    import va.dedup as dedup
    assert dedup.effective_frame_params is frame_params.effective_frame_params is extract.effective_frame_params
    assert dedup.frame_params_for_output is frame_params.frame_params_for_output
    assert dedup.EXEC_KEYS == frame_params.EXEC_KEYS == ("sequential",)
    assert frame_params.effective_frame_params({}) == P
