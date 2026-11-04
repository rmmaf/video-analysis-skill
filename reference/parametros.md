# Parameters: what each one does and when to change it

The values below are the ones calibrated on the real fixtures and on the synthetic videos (`ALGO_VERSION`
`dedup-1.1`), frozen in `scripts/va/__init__.py` (`DEDUP_DEFAULTS`, `OUTPUT_DEFAULTS`,
`BATCH_DEFAULTS`); the measurements and the reason for each adjustment to the design are in
`tests/data/calibration.md`. Changing a frame parameter changes the cache key: the video is extracted
again.

## Through the skill: the options of the call

| option | when to use | what it costs |
|---|---|---|
| `--sensibilidade high` | a small change that matters did not become a frame: a number, a short word | more frames, more batches, more tokens |
| `--sensibilidade low` | too many frames from compression noise or a large cursor | may miss a small change |
| `--ignorar-regiao x,y,w,h` | the density warning suggested a region: webcam, clock, animation | what changes there stops counting |
| `--trecho hh:mm:ss-hh:mm:ss` | only one part matters, or to go deeper into a segment of an earlier run | the first segment of a new video still extracts the whole video (it stays in the cache) |
| `--max-lotes N` | to test the objective before the whole run | partial coverage, stated in the report |
| `--falantes "SPEAKER_01=Name;…"` | the subtitles have generic labels (WhisperX) and you know the names | redoes only the subtitles and the batches, without re-extracting frames |
| `--paralelo N` | `1` against rate limits; up to 6 with many short videos | — |
| `--esforco-analista medium` (or `low`) | to save on a simple video, without a dense screen | measured on 14/10 (13 batches, an Excel screen): `medium` cost ~US$ 0.7 less (−13%), but had ~5 p.p. fewer complete facts and ~3 p.p. less precision |
| `--curadoria lotes` | a long video in which one batch changes the reading of the next (a demonstration in several steps) | Opus curator before each batch: ≈ 2× the cost (measured: US$ 9.9 vs 5.4 on a 13-batch video, with no recall gain) |
| `--esforco-curador medium` | with `--curadoria lotes`, to also save on the curator before each batch | without `--curadoria lotes`, it changes nothing: the plan and the curator's reports always stay at `high` |

`--sensibilidade` and `--ignorar-regiao` change the extraction: the frames are extracted again, and the
subtitles come from the cache. For several regions, separate them with `;` in the request
(`ignorar_regiao: 0.80,0.75,0.20,0.25; 0.00,0.00,0.20,0.06`). The other parameters on this page exist only
on the `prepare.py` command line, for manual use and calibration; the skill does not pass them.

**Region in fractions.** `x,y` is the top-left corner and `w,h` the width and the height, from 0 to 1
relative to the displayed frame. Examples: a webcam in the bottom-right corner, `0.80,0.75,0.20,0.25`; a
clock strip at the top, `0.00,0.00,1.00,0.05`. Prefer the region the warning suggests, which comes already
measured.

## How a frame becomes "unique"

1. **Sampling.** 2 samples per second, in grayscale at 640×360, only for comparing.
2. **Comparison with the last kept frame**, not with the previous sample: slow change and typing
   accumulate until they count.
3. **Two thresholds per pixel**, on the brightness difference (0 to 255): above the weak one, "changed a
   little"; above the strong one, "changed a lot". The changed pixels become blobs on a grid, and noise
   blobs are dropped: the tiny ones and the ones too faint, with less than 25% strong pixels (hover
   shading, camera grain, keyframe recompression).
4. **Cursor.** Up to 3 pointer-sized blobs (≈ 49 px tall at 1080p, which covers 150% scaling) count as
   "cursor only": the old position, the new one and the blinking text cursor. Up to 6 small blobs of that
   size that add up to at most 2 cursors do too: the pointer plus the highlight of the taskbar or of the
   tabs.
5. **Hot regions.** What changes by itself for a long time (webcam, the ring around whoever is speaking)
   leaves the comparison. So do stopwatch, clock and spinner, by a criterion of their own ("ticker"):
   cursor-sized new content in at least 30% of the sample pairs; the blinking text cursor goes back to the
   same state and does not count. The mask is computed over a ±30 s window, cut at layout changes (a large
   change followed by calm within 2 s, which also catches a screen that loads little by little), so as not
   to hide content of the shared screen.
6. **Settling.** A change only becomes a frame after 1 s of stability, and the image is that of the last
   sample of that second: the most complete state, with the last character typed. A flicker of up to 2% of
   the pending change does not restart the wait. During continuous scrolling or typing, a "motion" frame
   comes out every 4 s, with the interval doubling up to 30 s. A tooltip that appears and disappears does
   not become a frame.
7. **Revisit.** A screen the same as one of the last 16, within 10 min, reuses the earlier image. The test
   only runs with at least 25% of the frame outside the mask.
8. **Camera.** In camera footage, everything moves: the extraction drops to ~2 frames/min ("heartbeat")
   and warns.

Each frame stores `start` (when the change began), `captured_at` (the instant of the image) and `end` (the
start of the next one). The intervals are contiguous and cover the whole video; a cue belongs to the frame
whose interval it crosses.

## Comparison and sensitivity

| parameter | normal | low | high | what it is |
|---|---|---|---|---|
| `t_lo` / `t_hi` | 16 / 40 | 20 / 50 | 12 / 32 | weak and strong thresholds of brightness difference |
| `cursor_h_frac` / `cursor_w_frac` | 0.045 / 0.030 | 0.060 / 0.040 | 0.035 / 0.024 | maximum size of a "cursor" blob, as a fraction of the frame height |

The same at all three levels: up to 3 cursor blobs (`max_cursor_blobs`), or up to 6 adding up to at most
2 cursors (`cursor_scatter_blobs` 6, `cursor_scatter_px` 2.0); a blob with fewer than 3 px
(`min_blob_px`), or below 0.5% of the frame (`weak_only_min_frac`) with fewer than 2 strong px or less
than 25% strong px (`min_strong_px`, `min_strong_ratio` 0.25), is noise; an 8 px grid, with a cell active
from 4 changed px up (`cell_px`, `cell_active_px`).

**Known limits.** The cursor tolerance hides a change of one digit or up to ~4 characters; such changes
accumulate against the last kept frame and end up showing, and `high` reduces the effect. The grayscale
difference does not see a color swap with the same brightness (red ↔ green); an icon that also changes
shape is detected.

## Hot regions, settling, revisit and camera

| parameter | value | what it is | on the command line |
|---|---|---|---|
| sampling | 2 per second | 4 helps in a demonstration with fast clicks (costs more JPEG and Python per sample, not more decoding) | `--sample-fps` |
| settling | 1.0 s | time without change before the change becomes a frame | `--settle` |
| maximum wait | 3.0 s | after that, during continuous motion, a "motion" frame comes out | `--max-settle-wait` |
| "motion" frames | 4 → 30 s | initial and maximum interval, doubling with each one | `--motion-interval`, `--motion-max-interval` |
| hot mask | ±30 s window | `off` turns it off; use it when a panel that updates by itself drops out of the comparison | `--hot-mode windowed\|off` |
| turns the mask on | local activity ≥ 50% · total ≥ 85% · recurrence ≥ 8/min | what counts as "changes by itself"; in the recurrence, always-active cells (webcam) and thin outlines (the ring around whoever is speaking) do not count | — |
| keeps it on | 30% · 60% · 4/min | hysteresis, reset at each layout change | — |
| stopwatch ("ticker") | turns on with ≥ 30% of the pairs, stays on with 15% | cursor-sized new content, different from 2 samples before (clock, stopwatch, spinner) | — |
| layout change | change ≥ 50% followed, within 2 s (4 pairs), by a pair with < 20% | cuts the mask window | — |
| flicker during the wait | up to 2% of the pending change | does not restart the settling | — |
| camera | every ≥ 30 s, if it changed ≥ 10% | the "heartbeat" | — |
| revisit | 16 frames / 600 s, with ≥ 25% of the frame outside the mask | `--no-revisit` turns it off | `--no-revisit` |
| cursor | 0.045 of the height | size of the cursor box | `--cursor-size` |

## Output images

| parameter | value | what it is | on the command line |
|---|---|---|---|
| longer side | 1568 px (up to 2000) | 1568 is what the model reads without downscaling | `--max-edge` |
| JPEG quality | 3 (2 to 5; lower = better) | ~160 KB per frame at 1568×882; above 480 KB, the image is rewritten | `--jpeg-q` |

**Tokens.** An image costs ⌈w/28⌉ × ⌈h/28⌉ tokens: 1,792 at 1568×882 and 2,072 at 1568×1018. A batch of
20 images adds up to ≈ 36k tokens; the contact sheet, ≈ 1.5k; the text, one token every ~3.5 characters.

## Batches

| parameter | value | what it is | on the command line |
|---|---|---|---|
| frames per batch | up to 20 (preferred cut from 12 on) | analyst reliability: in windows of 50 it skipped 17 to 38 frames | `--batch-frames` |
| speech per batch | 8,000 distinct characters | a single frame above that becomes an `over_budget` batch | `--batch-text` |
| batch duration | 900 s (15 min) | — | `--batch-span` |
| soft cuts | on | `--no-soft-cuts` always cuts at the limit | `--no-soft-cuts` |

- **Where to cut.** Between the 12th frame and the limit, the point with the most points wins: scene
  change (+3), no cue crossing the cut (+2), silence of ±1 s (+1), previous frame lasting 30 s or more
  (+1). Tie: the latest cut.
- **Tail.** A last batch with fewer than 8 frames merges into the previous one if the total stays at 25 or
  fewer; otherwise the two are split down the middle.
- **`batch_images`.** A revisit counts toward the cap of 20 frames, but is not an image to open when the
  original is in the same batch. That is why the Workflow checks `frames_seen` against `batch_images`, not
  against the number of frames.
- **Labels** of `batch_NNN.md` come out in the language detected in the subtitles (pt-BR or English); the
  suffix of a revisit's image line is always ` (revisita de Fxxxx)`. The reports come out in the
  request's `idioma`. Full format in [`contratos.md`](contratos.md), section 4.

## Density warnings and the suggested region

The warnings come out in English, in `videos[].warnings` of the `prepare.py` JSON (the ones that the
earlier, Portuguese version of the skill stored in the cache stay in Portuguese until the frames or
subtitles they come from are redone):

- **Density**, above 12 frames/min in the video or 30 in any one minute:
  `high density: 14.2 frames/min in the video (limit 12); suggested region to ignore: 0.80,0.75,0.20,0.25
  (explains 45% of the frames)`, or, without a region,
  `…; no recurring region to ignore (if there are redundant frames: sensibilidade low)`.
- **Region suggestion:** `prepare.py` groups the change boxes of the kept frames that take up at most 25%
  of the frame. If a group explains at least 30% of the frames and at least 10 frames, it suggests
  ignoring the union of those boxes plus one cell, with 2 decimal places. It is the typical case of a
  webcam or clock that the automatic mask did not catch.
- **Camera**, a segment of 10 s or more:
  `camera-like segment at …: everything moves; extraction drops to ~2 frames/min (heartbeat)`.
- **Large mask**, from 35% to 50% of the frame for 60 s or more: `hot mask covered N% of the frame at …;
  if a panel of the screen dropped out of the comparison, use --hot-mode off`.
- **Cost**, more than 600 frames: `N frames: high image cost (~Nk tokens for images alone)`.
- **Subtitles**, with the prefix `subtitles:`: cues that cover less than 50% of the video, cues after the
  end (you may need `--vtt-offset SEC`, on the command line), invalid times, empty subtitles.

## What each change redoes

| change | redoes | reuses |
|---|---|---|
| new objective, `max_lotes`, `paralelo`, efforts, `idioma`, `papeis` | only the agents | the whole cache |
| `trecho` | the batches of the segment | frames and subtitles of the whole video |
| `falantes`, edited subtitles | subtitles and batches | frames |
| `sensibilidade`, `ignorar_regiao` | frames and batches | subtitles |
| video moved, renamed or edited | everything (another cache key) | — |

## Diagnostics (manual use, by a person)

- `scripts/explain.py --frames-json <frames.json> --frame F0042` builds a "why this frame was kept"
  panel; with `--top-ignored 12`, the largest changes treated as cursor or same (false-negative
  candidates). The `frames.json` is in `cache\<slug>-<fp8>\frames-<h8>\`.
- `scripts/calibrate.py <video> [--start --end] --grid "k=v1,v2;k2=…"` sweeps parameters without decoding
  again and compares counts; it is used to recalibrate the defaults.
- `scripts/prepare.py --gc 30` deletes from the cache the videos unused for more than 30 days and shows
  the space freed.
