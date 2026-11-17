# Frame engine calibration (WP1)

Measured on this machine (8 CPUs, Windows 11, Python 3.14.5, numpy 2.4.6, Pillow 12.2, ffmpeg 8.1.2) on
2026-10-06. Other agents were running at the same time, so wall times vary by up to ~2x between runs;
the ranges below are the observed spread. Frozen result: `ALGO_VERSION = "dedup-1.1"`, defaults in
`scripts/va/__init__.py` (`DEDUP_DEFAULTS`, `OUTPUT_DEFAULTS`). Tools: `scripts/calibrate.py` (sweeps
on a cached gray stream), `scripts/explain.py` (why a frame was kept / a sample ignored).

## 1. Decoding strategy (spec 0 and 3.1)

120 s of `metric_view` (1080p60, H.264 High, B-frames), `-skip_frame:v noref`, 2 fps:

| run | D (reference) | A alone (gray 640x360) | B alone (MJPEG 1568x882) | A + B as two processes at once | one decode split in A + B |
|---|---|---|---|---|---|
| 1 | — | 7.72 s | 8.05 s | 15.29 s | 10.94 s |
| 2 | 4.56 s | 4.27 s | 4.53 s | 7.05 s | 4.61 s |
| 3 | 3.76 s | 3.90 s | 5.01 s | 8.43 s | 6.28 s |

Full `venda_zero_clientes` (Teams 1080p16, 441 s): D 5.4-5.8 s, A 6.8-9.8 s, B 9.3-11.1 s, two processes
13.2-22.9 s, split 12.6-16.5 s. Pipe throughput (Python `readinto` from ffmpeg) is 700-900 MB/s, not a
bottleneck.

Both decoders are CPU-bound, so two concurrent decoders cost ~1.9-2.0 D (the spec measured "roughly
single-pass"). Decision: **lockstep = one ffmpeg process, `split` after the `fps` filter**: branch A
(gray, area) to stdout, branch B (lanczos, MJPEG q 3) written by the same process as numbered spool
JPEGs (`image2`, `-atomic_writing 1`); stdout backpressure keeps the spool a few samples ahead; the
selected spool files are moved into place (no re-encode), the rest deleted. `sequential` = pass A,
then pass B through `image2pipe`. Both give byte-identical output (section 6).

## 2. Synthetic scenarios (tests/synth.py, 1280x720 30 fps, x264 veryfast)

All rows of the spec 9 table pass, plus CRF 32 variants (zero false changes on static content):

| scenario | expected | result (captured_at, kind) |
|---|---|---|
| cursor_only 20 s | 1 frame | 1.0 first |
| typing 25 s (2 bursts of 40 chars at 5/s, select-all delete) | 1-4 frames per burst, settled <= 1.5 s after it stops, text complete | 1.0f 6.0m 11.0s 13.0s(=F1) 17.0m 22.0s; text diff 0.78 (CRF 28), 1.41 (CRF 32), limit 3 |
| slides_cam 90 s (cam tile 20 %, laser dot) | {0,20,30,45,70} +1 | starts 0 20 30 45 70, no extra |
| meeting_layouts 120 s (share + rings + cam, gallery with cam and rings, share) | {0,10,25,40,80,90,105} +3, 90/105 detected | exactly those 7, no extra; no hot segment after 80 s |
| scroll 40 s (15 s at 60 px/s) | <= 5 in [5,22], motion then settled ~21 | 8.0m 16.0m 21.0s |
| transient 20 s (tooltip 0.3 s, menu 10-13 s, per-second clock) | 10 and 13 only | 11.0s 14.0s(=F1); clock masked (ticker) |
| small_changes 20 s (1 digit at 5 s, 2 words at 12 s) | 12 only | 13.0s |
| camera 60 s (textured scene, pan, jitter, flicker, noise) | <= 4, heartbeat, warning | 1.0f 31.0h + camera warning |
| static_text 30 s (IDE-like, caret, pointer) CRF 28 / 32 | 1 frame | 1 / 1 |
| slides_cam VFR (`select` drop pattern, `-fps_mode vfr`) | same times, n_samples = 180 +-1 | same, 180 |
| meeting_layouts `-bf 2` | same | same 7 frames |
| CRF 32: slides_cam, transient, cursor_only, meeting_layouts, small_changes, typing | same as CRF 28 | same |

A/B on meeting_layouts (calibrate.py, same gray stream): windowed 7 frames, recall 1.00, 0 extra;
global (September mask) 16 frames, missed 105 s, 10 extra; off 7 frames, recall 0.57.

## 3. What calibration changed (each item found on a scenario or a fixture)

Starting point = spec 3.6 defaults: 8 of 12 synthetic rows passed; Repasse 77 frames, recall 98.6 %.

| change | evidence |
|---|---|
| stability test also compares with the first sample of the stable run using the strict cursor test (pointer box or vertical merge only) | typing settled every ~1.5 s: 5 glyphs + a tall caret fit the "merged horizontally" clause (w <= 2 cw, h >= ch/2) |
| settled frame captured at the last sample of the stable run | the first stable sample missed the last glyph typed inside the settle window |
| "small" pair (recurrence): busy cells (active in >= 3 of the last 6 pairs) left out, thin components (fill <= 25 % of their bbox) do not count toward the 3 % extent | gallery speaker rings were never masked (a gallery webcam made every pair big; a ring around a big tile spans 4-11 % of the cells); per-component or px-based variants masked the SQL results table of Repasse (its update splits into many column components) |
| "local" back to the spec's per-pair e <= 0.25 | per-component "local" let a full-screen text change split into line bands, so a long scroll became hot |
| ticker criterion (cursor-sized activity whose cells differ from 2 samples earlier, duty >= 0.3, keep 0.15) | the spec drops cursor-like components from the pair activity, so a per-second timer never got hot; on a static screen its digits accumulated vs the last kept frame into a false frame every ~10 s: 18 false frames in 3 min (13-px digits), 4 (9-px); after: 0. A blinking caret returns to the same state and stays unmasked |
| layout cut = big pair followed by a calm pair within 4 pairs (2 s), not only the next one | dias_estoque: camera view -> white page loading progressively; no cut, the face mask leaked ~20 s into the page |
| noise also when strong px < 25 % of the component's px (`min_strong_ratio`, below 0.5 % of the frame) | soft hover shading and camera grain counted; at CRF 32 the keyframe re-quantization of a static text line gave a 63 px component with 22 % strong px (false frame). Real content measured with explain.py: text 53-78 % strong, low-contrast icons 37 % |
| up to 6 cursor-sized blobs with <= 2x the cursor budget in total = cursor (`cursor_scatter_*`) | venda_zero / Repasse: pointer + taskbar highlight + tab strip changes (6-7 tiny blobs) made frames |
| a change <= 2 % of the pending change (and <= the scatter budget) does not break the settle (`settle_noise_rel`) | Repasse 239.5-241.5: a 76 px flicker hid a 2 s dialog-closed state (32,800 px) |
| revisit test with the commit's mask, only when >= 25 % of the frame is unmasked | camera scenario: a heartbeat frame "revisited" frame 1 through an almost fully masked comparison |
| synthetic camera rendered in numpy (texture + pan + jitter + flicker + noise) | lavfi testsrc2 is mostly static, not camera-like |

## 4. Real fixtures (final engine, lockstep)

| fixture | duration | frames (unique) | frames/min | kinds | max JPEG | wall time |
|---|---|---|---|---|---|---|
| Repasse da_1608 (Teams, WhisperX) | 544.5 s | 54 (41) | 6.0 (max 15 in 60 s) | 46 settled, 7 motion | 316 KB | 20.7-22.5 s = 0.038-0.041x |
| venda_zero_clientes | 441.2 s | 57 (53) | 7.8 (max 17) | 40 settled, 16 motion | 226 KB | 12.9-18.1 s = 0.029-0.041x |
| dias_estoque (Teams) | 398.8 s | 41 (37) | 6.2 (max 11) | 31 settled, 9 motion | 209 KB | 11.4-20.1 s = 0.028-0.050x |
| Criando sua primeira PR.mov (3024x1964 60 fps) | 186.7 s | 39 (38) | 12.5 (max 15) | 28 settled, 10 motion | 275 KB | 27.4-46.7 s = 0.147-0.25x |
| metric_view (1080p60) | 1683.9 s | 204 | 7.3 | — | — | 99.4-121.5 s (see 5) |
| red_aderencia (1080p60, optional) | 2509.3 s | 316 (291) | 7.6 (max 18) | 268 settled, 46 motion | 375 KB | 145.5 s = 0.058x |

Repasse vs the September baseline (128 frames, 72 significant): recall **1.000** within +-2 s, 54 frames
(range 50-128). Earlier engine versions: 97.2 %; the two misses then were an autocomplete popup visible
for 1 s (199.5/200.5 s), a transient by design. Our frames with no baseline frame within 2 s: 5, all
tiny changes (0.02-0.14 % of the frame) in the participant column or UI chrome
(`<workspace>/debug/regression/da_1608.md` lists them with thumbnails).

Hot-mask A/B (calibrate.py `--grid "hot_mode=windowed,global,off"`, before `min_strong_ratio` 0.25):

| fixture | windowed | global (Sept. mask) | off |
|---|---|---|---|
| Repasse | 55 frames, recall 0.972 | 81, recall 0.958 (21 motion) | 66, recall 0.778 (41 motion) |
| dias_estoque | 43 | 62 (20 motion) | 29 (21 motion, 7 settled: nothing settles) |
| venda_zero_clientes | 56 | 68 | 68 |

Sensitivity / settle sweep on Repasse (windowed): settle 0.5 s gives 82-85 frames (recall 1.00) for
low/normal/high; settle 1.0 s gives 54-57 frames (recall 0.972-1.00). The plan fixes 2 fps with 1 s.

## 5. Performance (tests/test_perf.py, cold run, stage in a child process)

| video | reference decode D | stage | limits | peak working set |
|---|---|---|---|---|
| metric_view 28 min 1080p60 | 71.8 s | 99.4 s = 1.38 D = 0.059x realtime | <= 1.8 D + 15 s, <= 0.15x | — |
| metric_view (loaded machine) | 96.1 s | 121.5 s = 1.26 D = 0.072x | idem | 63 MB |
| Repasse 9 min Teams 1080p16 | — | 22.3 s = 0.041x | <= 0.06x | 61 MB |

Memory: gray ring (lookahead 64 samples x 230 KB) + hot counters (4 x 128 x 3600 int32) + 16 revisit
screens; ffmpeg runs in its own process. Warm cache is the CLI's job (no decoding).

## 6. Determinism

Two lockstep runs and a sequential run into the same folder give byte-identical `frames.json`,
`samples.csv` and every JPEG (sha256): synthetic meeting_layouts (9 files) and venda_zero_clientes
(54 files). Tested in `test_extract_synth.py` and `test_frames_real.py`.

## 7. Frozen defaults that differ from the spec text

`min_strong_ratio 0.25`, `cursor_scatter_blobs 6`, `cursor_scatter_px 2.0`, `cut_calm_pairs 4`,
`small_max 0.03` (pair-level, busy cells excluded, thin components without extent), `thin_max 0.25`,
`busy_pairs 6`, `busy_min 3`, `tick_on_duty 0.3`, `tick_keep_duty 0.15`, `tick_tol 4.0`,
`settle_noise_rel 0.02`, `revisit_min_active 0.25`; `OUTPUT_DEFAULTS["lookahead_s"]` removed (the
lookahead is `hot_window_s` + `cut_calm_pairs`). All other values are the spec's (t_lo/t_hi 16/40,
cursor 0.045/0.030, settle 1.0 s, motion 4 -> 30 s, heartbeat 30 s / 0.10, revisit 16 / 600 s, hot
on 0.5 / 0.85 / 8 per min, keep 0.3 / 0.6 / 4 per min).

## 8. Integration: prepare.py end to end (2026-10-06, machine shared with another session)

One manual run of the 5 fixtures, `--jobs 2`, real CLI, default workspace (cache-only mode):

| fixture | frames (unique) | batches (images per batch) | frames stage | batches stage |
|---|---|---|---|---|
| Repasse da_1608 (NFD + U+2019 path, typed in NFC) | 54 (41) | 3 (11, 16, 14) | 32.5 s | 2.0 s |
| venda_zero_clientes | 57 (53) | 4 (12, 13, 17, 11) | 14.7 s | 0.8 s |
| dias_estoque | 41 (37) | 2 (16, 21) | 27.1 s | 3.0 s |
| Criando sua primeira PR.mov (accents, spaces) | 39 (38) | 2 (19, 19) | 57.4 s | 0.7 s |
| metric_view | 204 (172) | 13 | 130.2 s | 2.1 s |

- Wall time 171 s (`--estimar` said 184 s). The pool then took videos in input order, so
  metric_view started last; prepare.py now submits the longest job first (3-video check: 59.8 s
  instead of ~77 s).
- Warm cache: 4.5 s per single video before (3 runs of `ffmpeg/ffprobe -version`, a 64-entry PATH
  scan, an `importlib.metadata` scan, the numpy import for the frame params, ffprobe), now
  0.55-0.63 s; 5 videos with `--jobs 2`: 9.9 s -> 0.8 s. A fully cached video reuses
  `cache/<key>/probe.json`, checks no tool and runs in-process. Batch-only change
  (`--batch-frames 15`): frames not re-extracted, 3 videos rebuilt in 2.2 s.
- Determinism: `--jobs 1` and `--jobs 2`, both `--force all`, into two workspaces (venda_zero and
  the .mov): 125 files byte-identical after replacing the workspace prefix (16 JSON, 8 MD, 2 CSV,
  99 JPEG including sheets and overview).

## 9. Local transcription (WhisperX 3.8.6, 2026-11-11)

A copy of the Repasse da_1608 recording without its `.vtt` (9 min 04 s, Teams, 12 participants), through the
real flow: the request without a speaker count, the estimate (`pending`, `needs_speakers`), the count, then
the prepare in the background. Machine: a 2017 laptop, Intel i7-7700HQ (4 cores, 8 threads), Windows 11,
no NVIDIA GPU. `large-v3-turbo`, CPU, `int8`, 4 threads, batch 8; `num_speakers: 4-12`.

**Installation** (`--install-transcription`, from nothing): 9 min. On disk: the environment 3.7 GB (uv
reused the machine's Python 3.12), `large-v3-turbo` 1.6 GB, the Portuguese aligner 1.2 GB, pyannote's
community-1 32 MB. transformers 4.57 also fetched, in a background thread, the Hub bot's safetensors copy
of the aligner (another 1.2 GB, never loaded): `transcribe.py` now sets `DISABLE_SAFETENSORS_CONVERSION`.

**Time** (`transcribe.log`; 544.5 s of audio):

| phase | seconds | per second of audio |
|---|---|---|
| imports and audio | 10 | — |
| model load | 55 | — |
| language detection (6 speech windows; pt, probability 1.00) | 21 | — |
| speech (1 block: up to 15 min is one block) | 639 | 1.17 |
| times of the words (wav2vec2 aligner) | 211 | 0.39 |
| speakers (pyannote community-1, its batches of 32) | 544 | 1.00 |
| total | 1480 | 2.72 |

The first cost model (0.9 + 0.12 + 0.12 per second, 45 s to start) said 666 s; the calibrated one
(`TRANSCRIBE_DEFAULTS["cost"]["cpu"]`: 90 s, 1.17 + 0.39 + 1.0) says 1484 s. Only `large-v3-turbo` was
measured: the other CPU models are scaled from it, and the CUDA figures are not measured. On a CPU the speakers
are about 40% of the whole, so another `num_speakers` on a transcribed video costs about the length of the
video, not minutes.

**Quality against the notebook's subtitles** of the same meeting (WhisperX on Colab;
`compare_vtt`-style metrics, the skill's own parser on both sides, no text compared by eye):

| | notebook | local |
|---|---|---|
| speakers | 6, plus 1 `FALANTE_?` cue | 6, none unknown |
| words | 1350 | 1336 |
| timing lines (after the parser's repairs) | 155 (156: 1 rolling merge, 1 long split, 6 fast) | 178 (177: 1 rolling merge, 5 fast) |
| share of the video under a cue | 81.5% | 78.9% |

Word error rate of the local subtitles against the notebook's 7.8% (difflib similarity 0.946); they are two
Whisper runs, so this is disagreement, not error. Speakers: after the best one-to-one mapping of the labels,
the same speaker at 98.7% of 849 instants sampled every 0.5 s where both have a cue.

**The speaker count from the images.** Asked how many people speak, the user had an agent (Sonnet) read the
41 unique frames instead: 4 people with the speaking indicator in some frame, 12 in Teams' people counter,
so `4-12`; pyannote found 6. It read every frame: 60 s, about 150k tokens. That became the form's `Não sei`:
`prepare.py --speaker-shots` took 12 screenshots spread over the video in 4.5 s (`SHOTS_DEFAULTS`), and the
agent of `reference/speaker-count.md`, reading only those, answered the same `SPEAKERS: 4-12` in 13 s and about
90k tokens (the counter showed in 2 of the 12; the speaking indicator, on 4 different people).

**A hang found on the way:** the first run sat for 53 min at 0 s of CPU. `--watch-stdin` had a thread
blocked in `os.read(0)` on prepare.py's pipe, which on Windows holds the C runtime's lock on fd 0, and
`import numpy` waited on it forever (`test_transcribe_script.py` reproduces it). The watcher now polls the
pipe with `PeekNamedPipe` on Windows.
