# Parameters: what each one does and when to change it

The values below are the ones calibrated on the real fixtures and on the synthetic videos (`ALGO_VERSION`
`dedup-1.1`), frozen in `scripts/va/__init__.py` (`DEDUP_DEFAULTS`, `OUTPUT_DEFAULTS`,
`BATCH_DEFAULTS`); the measurements and the reason for each adjustment to the design are in
`tests/data/calibration.md`. Changing a frame parameter changes the cache key: the video is extracted
again.

## Through the skill: the options of the call

| option | when to use | what it costs |
|---|---|---|
| `--sensitivity high` | a small change that matters did not become a frame: a number, a short word | more frames, more batches, more tokens |
| `--sensitivity low` | too many frames from compression noise or a large cursor | may miss a small change |
| `--ignore-region x,y,w,h` | the density warning suggested a region: webcam, clock, animation | what changes there stops counting |
| `--ignore-timeline FILE.json` | a webcam that exists only in some layouts of the call: camera alone, then a screen share, then the gallery; with the DeepFace environment, the skill already makes this timeline (below), and a file given here prevails for its video | the region stops counting only during its spans; the file comes from `scripts/webcam_mask.py --timeline` |
| `--webcam-timeline on\|off` | the default `auto` needs nothing. `on`: a webcam on screen only briefly escaped the probe (60 frames); `off`: skip DeepFace in this call | `on`: the full scan even when the probe finds no face (about 24 min per hour of 1080p video); `off`: the webcam only goes through the automatic mask |
| `--range hh:mm:ss-hh:mm:ss` | only one part matters, or to go deeper into a segment of an earlier run | the first segment of a new video still extracts the whole video (it stays in the cache) |
| `--max-batches N` | to test the objective before the whole run | partial coverage, stated in the report |
| `--speakers "SPEAKER_01=Name;…"` | the subtitles have generic labels (WhisperX, or the skill's own transcription) and you know the names | redoes only the subtitles and the batches, without re-extracting frames |
| `--num-speakers N`, `--whisper-model`, `--audio-language` | a video without subtitles, which the skill transcribes: [below](#local-transcription-a-video-without-subtitles) | the transcription, once per video; another count redoes only the speakers |
| `--parallel N` | `1` against rate limits; up to 6 with many short videos | — |
| `--analyst-effort medium` (or `low`) | to save on a simple video, without a dense screen | measured on 14/10 (13 batches, an Excel screen): `medium` cost ~US$ 0.7 less (−13%), but had ~5 p.p. fewer complete facts and ~3 p.p. less precision |
| `--curation batches` | a long video in which one batch changes the reading of the next (a demonstration in several steps) | Opus curator before each batch: ≈ 2× the cost (measured: US$ 9.9 vs 5.4 on a 13-batch video, with no recall gain) |
| `--curator-effort medium` | with `--curation batches`, to also save on the curator before each batch | without `--curation batches`, it changes nothing: the plan and the curator's reports always stay at `high` |

`--sensitivity`, `--ignore-region` and `--ignore-timeline` change the extraction: the frames are extracted again, and the
subtitles come from the cache. So does a webcam timeline with spans, the first time it is made, and
`--webcam-timeline off` on a video that has one. For several regions, separate them with `;` in the request
(`ignore_region: 0.80,0.75,0.20,0.25; 0.00,0.00,0.20,0.06`). The other parameters on this page exist only
on the `prepare.py` command line, for manual use and calibration; the skill does not pass them.

**Region in fractions.** `x,y` is the top-left corner and `w,h` the width and the height, from 0 to 1
relative to the displayed frame. Examples: a webcam in the bottom-right corner, `0.80,0.75,0.20,0.25`; a
clock strip at the top, `0.00,0.00,1.00,0.05`. Prefer the region the warning suggests, which comes already
measured.

**Region over time.** A fixed region holds for the whole video: when the call starts with the camera alone
and a screen share comes in later, it also hides the shared content that falls where the webcam was.
`ignore_timeline` masks each region only during its span, on top of the automatic mask, which stays as it
is (it also covers what blinks around a webcam: name labels, speaking rings). What it removes is the webcam
frames the automatic mask lets through: on a 28-min Teams call (screen share, then gallery), 204 frames
became 182, two thirds of the frames whose change was inside a webcam went away (28 to 9), and no screen
content was lost.

**The timeline the skill makes** (`webcam_timeline`, default `auto`). When the DeepFace environment of
the setup exists (step 8 of [`setup.md`](../setup.md)), `prepare.py` makes the timeline of each video
before extracting its frames, with `scripts/webcam_mask.py`. First comes a probe of up to 60 frames spread
over the video (one every 10 s in a video shorter than 10 min): if no face shows up in the same place in
2 of them, there is no webcam, the timeline is empty and the full scan does not run. Otherwise the full
scan, one sample every 2 s, writes the timeline. A video of 10 s or less goes straight to the full scan:
its probe would have one frame, which cannot show a face in 2. Each sample is timed by the frame's own timestamp, so the
spans also fall in the right place in a variable frame rate (VFR) recording, such as a screen recording
that only gets frames when the screen changes. There the probe's seeks miss, because OpenCV seeks by
counting frames: the probe checks where each one landed and goes through the video from the start for
the parts left without a sample. A probe that still does not cover the video counts for nothing, and the
full scan runs. The timeline stays in the video's cache (`webcam-<h8>\`), and every later run reuses it,
also with `range` and `max_batches`. Its spans enter the frames key like an `ignore_timeline` file, so an
empty timeline changes nothing. The environment must be the setup's: deepface 0.0.104 and the YuNet
weights with their SHA-256, both in the timeline's cache key, so a timeline is never reused across
detectors; another version or damaged weights count as no environment, with a warning. `on` skips the
probe and always runs the full scan (without the environment, `prepare.py` stops with exit code 3);
`off` does not run DeepFace. A video with its own
`ignore_timeline` file keeps that file and gets no automatic timeline, even when the file has no spans:
an empty file turns the timeline off for that video alone. A failure becomes a warning, and the video is
extracted without the timeline. Measured on the same 4-core laptop:
- **1080p calls:** the probe took 55 to 66 s on three of them, plus about 7 s to load DeepFace, and
  found a face in all three.
- **3024×1964 screen recording** (VFR, 3 min): all 19 seeks landed in their part, so there was no pass
  from the start. The probe took 40 to 90 s and found no face, so the full scan (about 6 min by the
  estimate) did not run.
- **9-min 1080p call** (VFR, 16 frames/s on average): all 55 seeks also landed. Probe and full scan took
  4 min 13 s, against up to 5 min in the estimate, and made 3 spans (54 frames became 53). A 16 frames/s
  constant copy of the call gave the same 3 spans, to the second.

The estimate counts the worst case, probe and full scan, for each video whose timeline is not in the
cache yet, plus one decoding pass for the probe when ffprobe sees a variable frame rate.

The file, one per video:

```json
{"video": "C:/videos/call.mp4",
 "ignore_timeline": [{"start": 0, "end": 1834.5, "region": "0.80,0.00,0.20,0.20"},
                     {"start": 1830, "end": 3600, "region": "0.00,0.00,0.50,0.50"}]}
```

Times are seconds of the video (also with `range`), the region is in fractions as above, and
`[start, end, x, y, w, h]` also works as an entry. `"video"` (path, or just the file name) says which video
of the run it is for; without it, the file is accepted only in a run of one video. In the request, one line
`ignore_timeline: <file>` per file. `scripts/webcam_mask.py VIDEO --timeline FILE.json` writes it: it finds
the faces with DeepFace every 2 s and the camera window around each face. DeepFace runs in a separate Python
environment, which the setup creates (`check_environment.py --install-deepface`). It keeps the faces with a
YuNet score of 0.85 or more, below DeepFace's 0.9. On 60 frames of that call checked by eye, 0.9 missed 16
of 303 faces, 15 of them between 0.86 and 0.89, and nothing that was not a face scored above 0.73; the
timeline covered 92% of the live webcams with 0.9 and 98% with 0.85, and no region fell on the shared
screen with either. On four more recordings (2 h 18 min, 1080p and 720p captured from the browser), all
94 regions of 0.85 came from real faces: webcams, profile photos and a meeting-room camera. The gain is
largest in the 720p captures, where the webcams of the side strip are small and score mostly between
0.85 and 0.9: in one of them, the two webcams of the strip were masked 24 and 12 minutes longer than with
0.9. Things that are not faces also score in that band (percentage badges on a slide, up to 0.89), so
the score alone does not keep them out: the rule of the span does, 3 samples in the same place with
motion, and the badges were in a single sample. Two cases to know: a camera window can come out wider
than its tile (one covered the edge of the shared window for 28 s), and when a screen share shows a
Teams window, its webcams are masked like any other. The environment variable `yunet_score_threshold`
sets another score, from 0.75 up to but not including 1, written with a point: lower scores let in more
false faces, and at 1 YuNet finds no face. Any other value stops the script before it loads DeepFace.
Cost measured on a 4-core laptop CPU, for a 1080p 60 fps call: 24 min per hour of video (34 with the
machine busy), three quarters of it in the face detection, the same with either score; the extraction of
the skill takes 3 to 6 min per hour on the same video.

## Local transcription (a video without subtitles)

When a video has no `.vtt` of the same name, `prepare.py` transcribes it with `scripts/transcribe.py`,
which runs WhisperX 3.8.6 in the transcription environment of the setup (step 6 of
[`setup.md`](../setup.md)), one video at a time, before the webcam timeline and the extraction. The values
below are in `TRANSCRIBE_DEFAULTS` (`scripts/va/__init__.py`).

| option | when to use | what it costs |
|---|---|---|
| `num_speakers` | always worth giving, and the skill asks for it: it guides pyannote when it splits the voices. `3` when you know it; `2-4` or `4+` when you only know roughly; `1` for a lecture or a monologue (pyannote does not run, and every cue is `SPEAKER_00`); `auto` when nobody knows. The form's `Não sei` gives a range instead: an agent counts the people in 12 screenshots of the video ([`speaker-count.md`](speaker-count.md)): the ones seen speaking are the minimum, the ones in the meeting the maximum | on a transcribed video, another count redoes only the speakers, not the speech: on a CPU, about the length of the video (40% of the whole) |
| `whisper_model` | `large-v3` on a CPU when `large-v3-turbo` gets names or technical terms wrong; `medium` or `small` for a quicker, rougher pass; `large-v3-turbo` on an NVIDIA GPU that runs out of memory | another model transcribes the video again; on a CPU, `large-v3` takes two to three times as long as `large-v3-turbo` |
| `audio_language` | the language came out wrong (a warning says when the detection was not confident), or the video mixes languages and one of them should prevail | transcribes the video again |

How a video is transcribed:

1. **Audio.** ffmpeg decodes the audio track at 16 kHz, in memory. A video without an audio track gets
   empty subtitles, with a warning.
2. **Language** (`auto`). Whisper's language detection over up to 6 windows of 30 s with speech (voice
   activity detection) in the first 15 minutes, instead of the first 30 s, which in a call are often
   silence. Below a probability of 0.6, a warning suggests `audio_language`.
3. **Speech.** In blocks of about 10 minutes (a video of up to 15 minutes is one block), each cut at the
   quietest half second within 30 s of its boundary, so that no word is cut in two. Each block is a
   checkpoint (`partial\chunk_NNN.json`): an interrupted transcription resumes at the first block missing.
   On a CPU, `int8` with batch 8; on an NVIDIA GPU, `float16` with batch 16, halved at each out-of-memory
   error, then `int8_float16`, before giving up.
4. **Words.** The alignment model of the language (wav2vec2; the Portuguese one comes with the setup, the
   others at their first use) gives each word its time. Without a model for the language, or when the
   alignment fails, the times stay per segment, with a warning.
5. **Speakers.** pyannote (`speaker-diarization-community-1`, with the Hugging Face token of step 7 of the
   setup) splits the audio into turns, with `num_speakers` as the exact, minimum or maximum count. Each word
   goes to the turn it overlaps the most, or to the nearest one within 1 s; a word without a time (a
   number, a symbol) takes the speaker of the word before. Without the token, or without access to the
   model, the subtitles come out without speakers, with a warning.
6. **Cues.** A new cue at each sentence, at each change of speaker, at a pause of 0.8 s or more, and before
   going past 8 s or 160 characters: short cues, which the subtitle parser does not need to repair. A
   word whose speaker was not found is labelled `FALANTE_?`.

The `.vtt` follows the WhisperX style the parser already reads, with the language in its header and a
`NOTE` that says how it was made (the skill reads the last line of the `NOTE` back):

```text
WEBVTT
Language: pt

NOTE
Legenda gerada automaticamente pela skill video-analysis (WhisperX 3.8.6, large-v3-turbo, idioma pt, falantes: 3). Se você editar este arquivo, a skill não o substitui mais.
video-analysis: {"asr":"…","content_sha1":"…","diar":"…","diarized":true,"language":"pt","mode":"pyannote","model":"large-v3-turbo",…}

00:00:01.020 --> 00:00:04.310
SPEAKER_00: Bom dia, vamos começar pela pauta de hoje.
```

The whole video is transcribed, even with `range`, once: every later run of that video, with any range,
reuses it. The estimate counts it (`videos[].transcript.estimate_s`) with the cost model in
`TRANSCRIBE_DEFAULTS["cost"]`: a fixed start plus seconds of work per second of audio, per phase (speech,
words, speakers) and device. Measured on a 4-core 2017 laptop CPU with `large-v3-turbo` (a 9-min meeting,
25 min in all): 86 s to start (the imports, the model, the language), then 1.17 s per second of audio for
the speech, 0.39 for the words and 1.0 for the speakers, 2.7 times the length of the video
(`tests/data/calibration.md`, section 9). The other models on a CPU are scaled from that one, and the
NVIDIA figures are not measured.

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
  request's `language`. Full format in [`contracts.md`](contracts.md), section 4.

## Density warnings and the suggested region

The warnings come out in English, in `videos[].warnings` of the `prepare.py` JSON (the ones that the
earlier, Portuguese version of the skill stored in the cache stay in Portuguese until the frames or
subtitles they come from are redone):

- **Density**, above 12 frames/min in the video or 30 in any one minute:
  `high density: 14.2 frames/min in the video (limit 12); suggested region to ignore: 0.80,0.75,0.20,0.25
  (explains 45% of the frames)`, or, without a region,
  `…; no recurring region to ignore (if there are redundant frames: sensitivity low)`.
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
| new objective, `max_batches`, `parallel`, efforts, `language`, `roles` | only the agents | the whole cache |
| `range` | the batches of the segment | frames and subtitles of the whole video |
| `speakers`, edited subtitles | subtitles and batches | frames |
| another `num_speakers`, on a video the skill transcribed | the speakers (on a CPU, about the length of the video), the `.vtt`, subtitles and batches | the speech and the times of its words, frames |
| `whisper_model`, `audio_language`, on a video the skill transcribed (the one not given, and the `num_speakers`, stay as the `.vtt` was made: no new question about the speakers) | the transcription, the `.vtt`, subtitles and batches | frames |
| the `.vtt` the skill generated, edited by the person | subtitles and batches; the skill never replaces it again (a call with `num_speakers`, `whisper_model` or `audio_language` keeps it and warns) | frames, the transcription |
| `sensitivity`, `ignore_region`, `ignore_timeline`, a webcam timeline with spans (made, or turned `off`) | frames and batches | subtitles |
| a webcam timeline without spans (no webcam) | nothing | the whole cache |
| video moved, renamed or edited | everything (another cache key); the transcription too, if its `.vtt` stayed behind | — |

## Diagnostics (manual use, by a person)

- `scripts/explain.py --frames-json <frames.json> --frame F0042` builds a "why this frame was kept"
  panel; with `--top-ignored 12`, the largest changes treated as cursor or same (false-negative
  candidates). The `frames.json` is in `cache\<slug>-<fp8>\frames-<h8>\`.
- `scripts/calibrate.py <video> [--start --end] --grid "k=v1,v2;k2=…"` sweeps parameters without decoding
  again and compares counts; it is used to recalibrate the defaults.
- `scripts/prepare.py --gc 30` deletes from the cache the videos unused for more than 30 days and shows
  the space freed.
