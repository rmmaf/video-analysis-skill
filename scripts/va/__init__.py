"""video-analysis, deterministic layer: versions and default parameters.

Pure data, no side effects. Entry points must set ``sys.dont_write_bytecode = True``
*before* importing this package, so no ``__pycache__`` lands in the skill folder.

Usage notes for the stage modules:
- Never mutate these dicts; build effective params with a copy, e.g.
  ``p = {**copy.deepcopy(DEDUP_DEFAULTS), **SENSITIVITY_PRESETS[s], "sensitivity": s, **overrides}``.
- Every value is JSON-serializable (lists, never tuples), so the effective params can be
  written to frames.json / stage.json and hashed into the cache keys (spec section 7).
- Times are seconds in these dicts; stage code works in integer milliseconds internally.
"""

VERSION = "1.0.0"

# Bumped whenever the output of a stage changes for the same inputs (part of the cache keys).
ALGO_VERSION = "dedup-1.1"      # frames stage: dedup + extraction (frames-<h8>); 1.1 = calibrated defaults
PARSER_VERSION = "vtt-1.0"      # VTT parser and cue repairs (cues-<h8>)
VTT_VERSION = PARSER_VERSION    # alias: spec section 7 calls it VTT_VERSION
RENDER_VERSION = "render-1.1"   # alignment, batching, batch_NNN.md/json, transcript, sheets (batches-<h8>)
                                # 1.1 = the " (revisita de Fxxxx)" path marker is the same in every language

SCHEMA_VERSION = 1              # workflow_args["schema_version"]

# "schema" identifiers written as the first key of each JSON document.
SCHEMA_IDS = {
    "frames": "va.frames/1",
    "cues": "va.cues/1",
    "timeline": "va.timeline/1",
    "batches": "va.batches/1",
    "batch": "va.batch/1",
    "summary": "va.summary/1",
}

# --- frames stage: dedup (spec 3.2-3.6) ---------------------------------------------

# Threshold presets for --sensitivity (spec 3.6). Everything else is shared by all presets.
SENSITIVITY_PRESETS = {
    "low": {"t_lo": 20, "t_hi": 50, "cursor_h_frac": 0.060, "cursor_w_frac": 0.040},
    "normal": {"t_lo": 16, "t_hi": 40, "cursor_h_frac": 0.045, "cursor_w_frac": 0.030},
    "high": {"t_lo": 12, "t_hi": 32, "cursor_h_frac": 0.035, "cursor_w_frac": 0.024},
}

DEDUP_DEFAULTS = {
    "sensitivity": "normal",
    # classify(): weak = d > t_lo, strong = d > t_hi (uint8 abs diff on the analysis gray frame)
    "t_lo": 16,
    "t_hi": 40,
    "fast_strong_frac": 0.003,     # strong px > 0.3% of active px -> change (fast path)
    "fast_weak_frac": 0.05,        # weak px > 5% of active px -> change (fast path)
    "cc_cell_px": 4,               # connected-components grid (90x160 cells at 640x360)
    # noise components: px < min_blob_px, or (strong px < max(min_strong_px, min_strong_ratio * px)
    # and px < weak_only_min_frac of the frame)
    "min_blob_px": 3,
    "min_strong_px": 2,
    "min_strong_ratio": 0.25,
    "weak_only_min_frac": 0.005,
    # cursor test: ch = ceil(cursor_h_frac*AH)+1, cw = ceil(cursor_w_frac*AH)+1, budget = factor*cw*ch
    "cursor_h_frac": 0.045,
    "cursor_w_frac": 0.030,
    "cursor_budget_factor": 0.6,
    "max_cursor_blobs": 3,
    "cursor_scatter_blobs": 6,     # up to 6 cursor-like blobs still count as cursor when their
    "cursor_scatter_px": 2.0,      # total px <= cursor_scatter_px * budget (pointer + UI highlights)
    # pair activity (spec 3.3): 8x8 cells, active with >= 4 counted weak px
    "cell_px": 8,
    "cell_active_px": 4,
    "cut_on": 0.5,                 # cut_r = e_r >= cut_on and min(e_{r+1..r+cut_calm_pairs}) < cut_after_max
    "cut_after_max": 0.2,
    "cut_calm_pairs": 4,
    "small_max": 0.03,             # small pair: dense non-busy activity spans <= 3% of the cells and all of it <= 3% of the px
    "thin_max": 0.25,              # components filling <= 25% of their bbox (rings, borders) are thin: no extent
    "busy_pairs": 6,               # cells active in >= busy_min of the last busy_pairs pairs (webcams)
    "busy_min": 3,                 # are left out of the "small" components
    "local_max": 0.25,             # local pair: e_r <= local_max
    # hot mask (windowed, split at cuts, hysteresis)
    "hot_mode": "windowed",        # windowed | off  (global only in calibrate.py)
    "hot_window_s": 30.0,          # pairs in [k-H, k+H]
    "hot_min_pairs": 20,
    "hot_on_duty_local": 0.5,
    "hot_on_duty_all": 0.85,
    "hot_on_recur_per_min": 8.0,
    "hot_keep_duty_local": 0.3,
    "hot_keep_duty_all": 0.6,
    "hot_keep_recur_per_min": 4.0,
    "tick_on_duty": 0.3,           # ticker (timer, spinner): cursor-sized new content in >= 30% of the pairs
    "tick_keep_duty": 0.15,
    "tick_tol": 4.0,               # "new content": cell differs from 2 samples ago by > 4 gray levels (mean)
    "hot_dilate_cells": 1,
    "hot_fill_ratio": 0.4,         # fill the bbox of compact hot components ...
    "hot_fill_max_area": 0.25,     # ... whose bbox is <= 25% of the frame
    "hot_segment_jaccard": 0.8,    # new hot segment when mask Jaccard drops below this
    # camera-like segments: heartbeat
    "camera_hot_frac": 0.5,
    "heartbeat_s": 30.0,
    "heartbeat_min_frac": 0.10,
    # state machine (spec 3.4)
    "settle_s": 1.0,               # settle_pairs = max(1, round(settle_s * sample_fps))
    "max_settle_wait_s": 3.0,
    "settle_noise_rel": 0.02,      # a change <= 2% of the pending change (and <= scatter budget) keeps the settle
    "motion_interval_s": 4.0,      # motion backoff 4 -> 8 -> 16 -> 30 s
    "motion_max_interval_s": 30.0,
    # revisit
    "revisit": True,
    "revisit_frames": 16,
    "revisit_window_s": 600.0,
    "revisit_min_active": 0.25,    # no revisit test when more than 75% of the frame is masked
    # static ignore regions: list of [x, y, w, h] fractions of the displayed frame
    "ignore_regions": [],
    # density warnings and suggested ignore region (spec 3.5)
    "warn_frames_per_min": 12.0,
    "warn_window_frames": 30,      # > 30 frames in any 60 s window
    "warn_window_s": 60.0,
    "warn_frames_total": 600,
    "suggest_max_area": 0.25,
    "suggest_iou": 0.5,
    "suggest_min_share": 0.30,
    "suggest_min_frames": 10,
}

# --- frames stage: decoding and output (spec 3.1) -------------------------------------

OUTPUT_DEFAULTS = {
    "sample_fps": 2.0,             # fps=fps=S:start_time=0:round=near
    "analysis_height": 360,        # AH = min(360, floor(H/8)*8); AW = round(AH*W/H/8)*8
    "analysis_align": 8,
    "analysis_scale_flags": "area",
    "max_edge": 1568,              # output scale = min(1, max_edge / max(W, H)), even sides
    "max_edge_cap": 2000,          # --max-edge is capped at this value
    "output_scale_flags": "lanczos",
    "jpeg_q": 3,                   # ffmpeg mjpeg -q:v
    "jpeg_q_min": 2,
    "jpeg_q_max": 5,
    "jpeg_reencode_above": 480000, # safety net: re-encode with Pillow above this size ...
    "jpeg_reencode_quality": 85,   # ... at this quality, marking "reencoded": true
    "jpeg_max_bytes": 500000,      # acceptance limit per frame JPEG
    "skip_noref": True,            # -skip_frame:v noref (input option of the decode)
}

# --- cues stage (spec 4) ---------------------------------------------------------------

CUES_DEFAULTS = {
    "offset_s": 0.0,               # --vtt-offset
    "speaker_names": {},           # --speaker-names, e.g. {"SPEAKER_01": "Lianderson"}
    "fallback_encoding": "cp1252", # when utf-8-sig decoding fails (with a warning)
    "min_duration_s": 0.1,
    "end_before_start_s": 0.5,     # end < start -> end = start + 0.5 s (warning)
    # merges: always same speaker, gap <= merge_gap_s or overlapping
    "merge_gap_s": 0.5,
    "merge_identical": True,
    "merge_rolling": True,         # prefix growth
    "merge_scroll": True,          # 2-line scroll
    # repairs (approx: true)
    "long_min_s": 15.0,            # long cue: duration > 15 s and text > 200 chars -> split
    "long_min_chars": 200,
    "split_chunk_min_s": 6.0,
    "split_chunk_max_s": 10.0,
    "fast_max_cps": 30.0,          # too fast: > 30 chars/s ...
    "fast_target_cps": 15.0,       # ... end = min(next start, start + chars/15)
    # warnings
    "warn_coverage_below": 0.5,    # VTT covers < 50% of the duration
}

# --- batches stage (spec 5 and 6) -------------------------------------------------------

BATCH_DEFAULTS = {
    "max_frames": 20,
    "soft_min": 12,                # 0.6 x max_frames: first boundary candidate
    "max_text_chars": 8000,        # distinct cue text per batch
    "max_span_s": 900.0,
    "soft_cuts": True,             # --no-soft-cuts -> cut at j_limit
    # boundary scores
    "score_scene_frac": 0.5,       # +3 if frame b's change frac >= 0.5
    "score_scene": 3,
    "score_no_cue_span": 2,        # +2 if no cue spans frame b's start
    "score_quiet_s": 1.0,          # +1 if no cue overlaps +-1 s around it
    "score_quiet": 1,
    "score_long_prev_s": 30.0,     # +1 if frame b-1 lasts >= 30 s
    "score_long_prev": 1,
    # tail: last batch < tail_min_frames -> merge into the previous one when the total is
    # <= tail_merge_max, otherwise split both batches' frames evenly (ceil for the first half)
    "tail_min_frames": 8,
    "tail_merge_max": 25,
    # token estimates
    "image_token_px": 28,          # ceil(w/28) * ceil(h/28)
    "text_chars_per_token": 3.5,
    # transcript.md paragraphs: same speaker, gap <= 1.5 s, up to 600 chars or 60 s
    "transcript_gap_s": 1.5,
    "transcript_max_chars": 600,
    "transcript_max_s": 60.0,
    # contact sheets
    "sheet_width": 1600,
    "sheet_gap_px": 4,
    "sheet_cols_upto20": 5,
    "sheet_cols_upto30": 6,
    "sheet_cols_more": 8,
    "sheet_label_px": 20,
    "sheet_font": "C:/Windows/Fonts/consola.ttf",
    "sheet_font_px": 14,
    "sheet_jpeg_quality": 80,
    "overview_max_batches": 40,    # overview.jpg samples batches above this count
    # trecho: None = the whole frames range; [t0, t1] seconds = batch only the frames that
    # overlap it (own batches stage over the whole-video cache, numbering restarts at 001)
    "range": None,
}

# --- agentic layer (plan, unified contract) ---------------------------------------------

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
ROLES_MODES = ["agents", "inline"]
CURATION_MODES = ["plan", "batches"]  # plan: no NEXT; batches: the NEXT before each batch after the 1st

WORKFLOW_DEFAULTS = {
    "roles_mode": "inline",
    "max_batches": None,           # None = all batches
    "max_parallel_videos": 3,      # the Workflow script clamps to 1..6
    "max_parallel_videos_cap": 6,
    "analyst_effort": "high",
    "curator_effort": "high",
    "curation": "plan",
    "confirm_agents_above": 80,    # main agent asks the user above this estimate
    "max_agents": 800,             # refused above this (Workflow ceiling is 1000)
}

# --- workspace ----------------------------------------------------------------------------

WORKSPACE_ENV = "VIDEO_ANALYSIS_HOME"
WORKSPACE_DEFAULT_NAME = "video-analysis"   # %USERPROFILE%\video-analysis

WORKSPACE_DIRS = {
    "cache": "cache",
    "runs": "runs",
    "requests": "requests",          # the request being prepared (pedidos before the rename)
    "roles": "roles",
    "logs": "logs",
    "debug": "debug",
    "tmp": "tmp",
}

# File and id naming (spec 1). {NNN} = 3-digit batch number.
NAMES = {
    "frame_id": "F{n:04d}",            # 5 digits only above 9,999 frames
    "frame_file": "{id}_{hhmmss}.jpg", # hhmmss of captured_at, floored
    "cue_id": "C{n:04d}",
    "batch_id": "B{n:03d}",
    "frames_json": "frames.json",
    "samples_csv": "samples.csv",
    "cues_json": "cues.json",
    "batch_md": "batch_{NNN}.md",
    "batch_json": "batch_{NNN}.json",
    "sheet": "sheet_{NNN}.jpg",
    "overview": "overview.jpg",
    "transcript": "transcript.md",
    "summary": "summary.json",
    "timeline": "timeline.json",
    "batches_json": "batches.json",
    "stage_json": "stage.json",
}

# prepare.py exit codes (plan, unified contract item 3).
EXIT_CODES = {"ok": 0, "input": 2, "environment": 3, "video_failed": 4, "interrupted": 130}
