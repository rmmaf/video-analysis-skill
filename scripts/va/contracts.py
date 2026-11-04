"""Data contracts of the video-analysis skill (spec section 6 + plan "Contrato unificado").

TypedDicts document the JSON shapes (key order in the files follows the field order here).
Validators return a list of error strings; an empty list means the document is valid.

All JSON: UTF-8, ensure_ascii=False, indent=1, allow_nan=False, "\\n" newlines, forward-slash
absolute paths, times in seconds with 3 decimals (integer ms internally).

No ``from __future__ import annotations`` here: it would hide NotRequired from TypedDict.
"""
import math
import re
from typing import Any, NotRequired, TypedDict

from . import CURATION_MODES, EFFORT_LEVELS, ROLES_MODES, SCHEMA_IDS, SCHEMA_VERSION, WORKFLOW_DEFAULTS
from .common import cue_id, frame_filename, frame_id, s_to_ms

__all__ = [
    "BatchDoc", "BatchFrame", "BatchIndexEntry", "BatchesDoc", "Cue", "CuesDoc", "FrameChange",
    "FrameEntry", "FramesDoc", "PrepareError", "PrepareOutput", "PrepareVideo", "SummaryDoc",
    "TimelineDoc", "WorkflowArgs", "WorkflowOptions", "WorkflowVideo",
    "CUE_STYLES", "FRAME_KINDS",
    "check_contiguity", "check_cues", "check_frames", "check_workflow_args", "is_abs_posix",
]

FRAME_KINDS = ("first", "settled", "motion", "heartbeat", "final")
CUE_STYLES = ("whisperx", "teams", "plain", "mixed")


# --- frames.json ---------------------------------------------------------------------------

class VideoMeta(TypedDict):
    path: str              # on-disk path as stored (may be NFD), forward slashes
    title: str             # NFC stem, for display
    key: str               # video_key = slug + "-" + fp8
    duration_s: float
    width: int             # displayed size (after SAR and rotation)
    height: int
    fps_avg: float
    vfr: bool
    codec: str


class FrameChange(TypedDict):
    frac: float            # changed fraction of the active area vs the previous kept frame
    bbox: list[float]      # [x, y, w, h] fractions
    blobs: int


class FrameEntry(TypedDict):
    id: str                # "F0001" (5 digits above 9,999 frames)
    file: str              # "F0001_000000.jpg"; a revisit reuses its original's file
    start: float           # first detection of the change
    end: float             # next frame's start; the last one ends at the range end
    captured_at: float     # the image instant: start <= captured_at < end (last: <= end)
    sample: int            # 0-based index on the sampling grid: captured_at = range[0] + sample / sample_fps
    kind: str              # FRAME_KINDS
    same_as: str | None    # revisit: id of an earlier frame showing the same screen
    change: FrameChange
    hot_frac: float
    bytes: int
    tokens: int            # image tokens = ceil(w/28) * ceil(h/28)
    reencoded: NotRequired[bool]


class HotSegment(TypedDict):
    start: float
    end: float
    frac: float
    bbox: list[float]
    cells: str             # base64 packbits of the 80x45 cell mask


class Density(TypedDict):
    frames_per_min: float
    max_per_min: int
    max_window: list[float]


class SuggestedIgnore(TypedDict):
    region: list[float]
    explains: float


class FramesDoc(TypedDict):
    schema: str            # "va.frames/1"
    algo_version: str
    video: VideoMeta
    range: list[float]     # [start, end] seconds
    sample_fps: float
    n_samples: int
    analysis_size: list[int]
    output_size: list[int]
    frames_dir: str
    params: dict[str, Any]
    frames: list[FrameEntry]
    n_unique: int          # frames with same_as == null
    hot_segments: list[HotSegment]
    density: Density
    suggested_ignore: list[SuggestedIgnore]
    warnings: list[str]


# --- cues.json -------------------------------------------------------------------------------

class VttInfo(TypedDict):
    path: str
    sha1: str
    style: str             # CUE_STYLES
    language: str          # pt | en | es | und (or the header value)


class CueStats(TypedDict):
    raw: int
    kept: int
    merged_dupes: int
    merged_rolling: int
    split_long: int
    extended_fast: int
    dropped_empty: int
    overlaps: int
    nonmonotonic: int


class Speaker(TypedDict):
    tag: str               # "S1"... by first appearance; "S?" for FALANTE_?
    name: str
    cues: int
    talk_s: float


class Cue(TypedDict):
    id: str                # "C0001"... assigned after all edits
    start: float
    end: float
    spk: str | None        # speaker tag or null
    text: str              # one line, whitespace collapsed
    approx: bool           # split or extended by a repair
    src: list[int]         # source cue indexes (1-based)
    utt: int | None        # Teams utterance number ("uuid/18-1" -> 18)


class CuesDoc(TypedDict):
    schema: str            # "va.cues/1"
    parser_version: str
    vtt: VttInfo
    offset_s: float
    stats: CueStats
    speakers: list[Speaker]
    cues: list[Cue]


# --- timeline.json, batches.json, batch_NNN.json ---------------------------------------------

class TimelineFrame(TypedDict):
    id: str
    start: float
    end: float
    cues: list[str]


class TimelineBatch(TypedDict):
    id: str
    frames: list[str]      # [first id, last id]


class TimelineDoc(TypedDict):
    schema: NotRequired[str]   # "va.timeline/1" (optional; first key when present)
    frames: list[TimelineFrame]
    cue_frames: dict[str, list[str]]
    batches: list[TimelineBatch]


class BatchTokens(TypedDict):
    images: int
    text: int
    sheet: int


class BatchIndexEntry(TypedDict):
    n: int
    id: str                # "B001"
    frames: list[str]      # [first id, last id]
    t: list[float]         # [start, end]
    n_frames: int
    cues: list[str]        # [first id, last id] or []
    chars: int
    over_budget: bool
    est_tokens: BatchTokens
    md: str
    json: str
    sheet: str


class BatchesDoc(TypedDict):
    schema: NotRequired[str]   # "va.batches/1"
    n_batches: int
    params: dict[str, Any]
    batches: list[BatchIndexEntry]


class BatchNeighbor(TypedDict):
    id: str
    t: list[float]


class BatchVideo(TypedDict):
    title: str
    key: str
    duration_s: float


class BatchSpeaker(TypedDict):
    tag: str
    name: str


class BatchFrame(TypedDict):
    id: str
    path: str
    start: float
    end: float
    captured_at: float
    kind: str
    same_as: str | None
    cues: list[str]
    new_cues: list[str]    # cues printed in full at this frame


class BatchCue(TypedDict):
    id: str
    start: float
    end: float
    spk: str | None
    text: str
    approx: bool
    cont: bool             # began before the batch's first frame


class BatchDoc(TypedDict):
    schema: NotRequired[str]   # "va.batch/1"
    video: BatchVideo
    batch: BatchIndexEntry
    prev: BatchNeighbor | None
    next: BatchNeighbor | None
    speakers: list[BatchSpeaker]
    frames: list[BatchFrame]
    cues: list[BatchCue]


# --- summary.json ---------------------------------------------------------------------------

class SummaryTokens(TypedDict):
    frames: int
    sheets: int
    text: int


class SummaryPaths(TypedDict):
    frames_dir: str
    batches_dir: str
    frames_json: str
    cues_json: str
    timeline_json: str
    transcript_md: str
    batches_json: str


class SummaryDoc(TypedDict):
    schema: NotRequired[str]   # "va.summary/1"
    title: str
    video_path: str
    vtt_path: str
    video_key: str
    duration_s: float
    range: list[float]
    resolution: list[int]
    fps_avg: float
    vfr: bool
    output_size: list[int]
    sample_fps: float
    n_samples: int
    n_frames: int
    n_unique: int
    frames_per_min: float
    kinds: dict[str, int]
    n_cues: int
    n_speakers: int
    language: str
    cue_style: str
    n_batches: int
    batches: list[list[Any]]   # [[n, first_frame, last_frame, t0, t1, n_frames, n_images], ...]
                               # e.g. [1, "F0001", "F0014", 0.0, 212.5, 14, 12]; n_images = image path lines
    est_tokens: SummaryTokens
    paths: SummaryPaths
    warnings: list[str]


# --- workflow_args v1 (plan, unified contract item 1) ----------------------------------------

class WorkflowOptions(TypedDict):
    max_batches: int | None        # None (or 0) = all batches
    max_parallel_videos: int       # default 3; the script clamps to 1..6
    analyst_effort: str            # EFFORT_LEVELS, default "high"
    curator_effort: str            # EFFORT_LEVELS, default "high"
    curation: NotRequired[str]     # CURATION_MODES; absent = "plan"


class WorkflowVideo(TypedDict):
    slug: str
    title: str
    duration: str                  # "HH:MM:SS"
    n_frames: int
    batches_dir: str               # batch_NNN.md, sheet_NNN.jpg, transcript.md, summary.json, overview.jpg
    out_dir: str                   # <run_dir>/v<N> (older runs: <run_dir>/<slug>): ledger.md, relatorio.md, prompts/, reports/
    batch_images: list[int]        # images to open per batch (revisits within the batch excluded)
    start_batch: int               # first batch without reports/batch_NNN.md (1-based)
    ledger_exists: bool
    start_prompt_exists: bool
    final_exists: bool


class WorkflowArgs(TypedDict):
    schema_version: int            # 1
    run_id: str                    # basename of run_dir
    run_dir: str
    objective_path: str
    roles_dir: str
    roles_mode: str                # ROLES_MODES
    language: str
    options: WorkflowOptions
    combined_exists: bool
    videos: list[WorkflowVideo]


# --- prepare.py stdout (spec 6 + plan item 3) --------------------------------------------------

class PrepareEstimate(TypedDict):
    batches: int
    agents: int
    seconds: float


class PrepareEnv(TypedDict):
    permissions_ok: bool
    agents_current: bool
    workflow_current: bool         # <claude home>/workflows/video-analysis.js byte-identical to the skill's


class PrepareVideo(SummaryDoc):
    vid: str
    cache_hit: dict[str, bool]     # {"cues": bool, "frames": bool, "batches": bool}
    timing_s: dict[str, float]     # not deterministic


class PrepareOutput(TypedDict):
    ok: bool                       # true
    mode: str                      # "prepare" (resume/estimate/gc reuse ok/mode/version/home)
    version: str
    home: str
    run_id: str
    run_dir: str
    json_path: str
    script_path: str               # workflow/video-analysis.js, forward slashes
    videos: list[PrepareVideo]
    estimate: PrepareEstimate
    env: PrepareEnv
    workflow_args: WorkflowArgs
    errors: list[Any]
    warnings: list[str]


class PrepareErrorBody(TypedDict):
    code: str
    detail: str


class PrepareError(TypedDict):
    ok: bool                       # false; exit codes: 2 input, 3 environment, 4 video failed, 130 interrupted
    error: PrepareErrorBody


# --- validators ------------------------------------------------------------------------------

_ABS_POSIX_RE = re.compile(r"^(?:[A-Za-z]:/|//[^/])")
_SPK_TAG_RE = re.compile(r"^S(?:[1-9][0-9]*|\?)$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HMS_RE = re.compile(r"^\d{2,}:[0-5]\d:[0-5]\d$")
_INVISIBLE = frozenset("\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\ufeff\u00a0")


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def is_abs_posix(p: Any) -> bool:
    """True for an absolute path written with forward slashes ("C:/..." or "//server/...")."""
    return isinstance(p, str) and "\\" not in p and bool(_ABS_POSIX_RE.match(p))


def _missing(obj: dict, keys, where: str) -> list[str]:
    return [f"{where}: missing key {k!r}" for k in keys if k not in obj]


def _required(td: type) -> tuple[str, ...]:
    return tuple(k for k in td.__annotations__ if k in td.__required_keys__)


def check_contiguity(items: list[dict], range_: list[float] | tuple[float, float] | None = None, *,
                     label: str = "frames", start_key: str = "start", end_key: str = "end") -> list[str]:
    """Intervals must tile [range_[0], range_[1]] with no gap or overlap (compared in integer ms).
    Every interval needs start < end, except the last one, which may be empty."""
    if not isinstance(items, list) or not items:
        return [f"{label}: expected a non-empty list of intervals"]
    errors: list[str] = []
    prev_end: int | None = None
    for i, it in enumerate(items):
        tag = f"{label}[{i}]" + (f" {it.get('id')}" if isinstance(it, dict) and it.get("id") else "")
        if not isinstance(it, dict) or not _num(it.get(start_key)) or not _num(it.get(end_key)):
            errors.append(f"{tag}: {start_key}/{end_key} must be numbers")
            prev_end = None
            continue
        s, e = s_to_ms(it[start_key]), s_to_ms(it[end_key])
        last = i == len(items) - 1
        if e < s or (e == s and not last):
            errors.append(f"{tag}: {end_key} {it[end_key]} must be > {start_key} {it[start_key]}")
        if prev_end is not None and s != prev_end:
            kind = "gap" if s > prev_end else "overlap"
            errors.append(f"{tag}: {start_key} {it[start_key]} != previous {end_key} {prev_end / 1000:.3f} ({kind})")
        prev_end = e
    if range_ is not None:
        if not (isinstance(range_, (list, tuple)) and len(range_) == 2 and all(_num(x) for x in range_)):
            errors.append(f"{label}: range must be [start, end]")
        else:
            first, last_it = items[0], items[-1]
            if isinstance(first, dict) and _num(first.get(start_key)) and s_to_ms(first[start_key]) != s_to_ms(range_[0]):
                errors.append(f"{label}: first {start_key} {first[start_key]} != range start {range_[0]}")
            if isinstance(last_it, dict) and _num(last_it.get(end_key)) and s_to_ms(last_it[end_key]) != s_to_ms(range_[1]):
                errors.append(f"{label}: last {end_key} {last_it[end_key]} != range end {range_[1]}")
    return errors


def _check_size(value: Any, where: str, multiple: int) -> list[str]:
    if not (isinstance(value, list) and len(value) == 2 and all(_int(v) and v > 0 for v in value)):
        return [f"{where}: expected [width, height] positive ints"]
    if any(v % multiple for v in value):
        return [f"{where}: {value} sides must be multiples of {multiple}"]
    return []


def check_frames(doc: Any) -> list[str]:
    """Validate a frames.json document: required keys, sequential ids and file names,
    contiguity over the range, start <= captured_at < end (last may be <= end), sample grid,
    revisits pointing to an earlier frame with the same file, n_unique."""
    if not isinstance(doc, dict):
        return ["frames.json: expected an object"]
    errors = _missing(doc, _required(FramesDoc), "frames.json")
    if doc.get("schema", SCHEMA_IDS["frames"]) != SCHEMA_IDS["frames"]:
        errors.append(f"frames.json: schema {doc.get('schema')!r} != {SCHEMA_IDS['frames']!r}")
    video = doc.get("video")
    if isinstance(video, dict):
        errors += _missing(video, _required(VideoMeta), "video")
        if "path" in video and not is_abs_posix(video["path"]):
            errors.append(f"video.path: not an absolute forward-slash path: {video['path']!r}")
        if "duration_s" in video and not (_num(video["duration_s"]) and video["duration_s"] > 0):
            errors.append("video.duration_s: expected a positive number")
    elif "video" in doc:
        errors.append("video: expected an object")
    if "frames_dir" in doc and not is_abs_posix(doc["frames_dir"]):
        errors.append(f"frames_dir: not an absolute forward-slash path: {doc['frames_dir']!r}")
    if "analysis_size" in doc:
        errors += _check_size(doc["analysis_size"], "analysis_size", 8)
    if "output_size" in doc:
        errors += _check_size(doc["output_size"], "output_size", 2)
    rng = doc.get("range")
    rng_ok = isinstance(rng, list) and len(rng) == 2 and all(_num(x) for x in rng) and s_to_ms(rng[0]) < s_to_ms(rng[1])
    if "range" in doc and not rng_ok:
        errors.append(f"range: expected [start, end] with start < end, got {rng!r}")
    if rng_ok and isinstance(video, dict) and _num(video.get("duration_s")) and s_to_ms(rng[1]) > s_to_ms(video["duration_s"]):
        errors.append(f"range: end {rng[1]} is past the video duration {video['duration_s']}")
    fps = doc.get("sample_fps")
    fps_ok = _num(fps) and fps > 0
    if "sample_fps" in doc and not fps_ok:
        errors.append("sample_fps: expected a positive number")
    n_samples = doc.get("n_samples")
    if "n_samples" in doc and not (_int(n_samples) and n_samples > 0):
        errors.append("n_samples: expected a positive int")
        n_samples = None

    frames = doc.get("frames")
    if not isinstance(frames, list) or not frames:
        errors.append("frames: expected a non-empty list")
        return errors
    total = len(frames)
    files: dict[str, str] = {}
    unique = 0
    usable = True
    for i, f in enumerate(frames):
        where = f"frames[{i}]"
        if not isinstance(f, dict):
            errors.append(f"{where}: expected an object")
            usable = False
            continue
        errors += _missing(f, _required(FrameEntry), where)
        fid, expected_id = f.get("id"), frame_id(i + 1, total)
        if fid != expected_id:
            errors.append(f"{where}: id {fid!r} != {expected_id!r} (ids must be sequential from F0001)")
        where = f"{where} {fid}"
        if f.get("kind") not in FRAME_KINDS:
            errors.append(f"{where}: kind {f.get('kind')!r} not in {FRAME_KINDS}")
        s, c, e = f.get("start"), f.get("captured_at"), f.get("end")
        if not (_num(s) and _num(c) and _num(e)):
            errors.append(f"{where}: start/captured_at/end must be numbers")
            usable = False
        else:
            sm, cm, em = s_to_ms(s), s_to_ms(c), s_to_ms(e)
            last = i == total - 1
            if cm < sm:
                errors.append(f"{where}: captured_at {c} < start {s}")
            if cm > em or (cm == em and not last):
                errors.append(f"{where}: captured_at {c} must be < end {e}" + (" (<= for the last frame)" if last else ""))
            for name, value in (("start", s), ("captured_at", c), ("end", e)):
                if value != round(value, 3):
                    errors.append(f"{where}: {name} {value} has more than 3 decimals")
            sample = f.get("sample")
            if not (_int(sample) and sample >= 0):
                errors.append(f"{where}: sample must be an int >= 0")
            else:
                if n_samples is not None and sample >= n_samples:
                    errors.append(f"{where}: sample {sample} >= n_samples {n_samples}")
                if rng_ok and fps_ok and abs(cm - s_to_ms(rng[0] + sample / fps)) > 1:
                    errors.append(f"{where}: captured_at {c} != range start + sample/sample_fps = {rng[0] + sample / fps:.3f}")
        same_as, file = f.get("same_as"), f.get("file")
        if same_as is None:
            unique += 1
            if _num(c) and isinstance(fid, str):
                expected_file = frame_filename(i + 1, c, total)
                if file != expected_file:
                    errors.append(f"{where}: file {file!r} != {expected_file!r}")
        elif same_as not in files:
            errors.append(f"{where}: same_as {same_as!r} does not reference an earlier frame")
        elif file != files[same_as]:
            errors.append(f"{where}: revisit file {file!r} != file of {same_as} {files[same_as]!r}")
        if isinstance(fid, str):
            files[fid] = file
        change = f.get("change")
        if isinstance(change, dict):
            bbox = change.get("bbox")
            if not (_num(change.get("frac")) and 0 <= change["frac"] <= 1):
                errors.append(f"{where}: change.frac must be a number in [0, 1]")
            if not (isinstance(bbox, list) and len(bbox) == 4 and all(_num(v) and 0 <= v <= 1 for v in bbox)):
                errors.append(f"{where}: change.bbox must be [x, y, w, h] fractions")
            if not (_int(change.get("blobs")) and change["blobs"] >= 0):
                errors.append(f"{where}: change.blobs must be an int >= 0")
        elif "change" in f:
            errors.append(f"{where}: change must be an object")
        if "hot_frac" in f and not (_num(f["hot_frac"]) and 0 <= f["hot_frac"] <= 1):
            errors.append(f"{where}: hot_frac must be a number in [0, 1]")
        for name in ("bytes", "tokens"):
            if name in f and not (_int(f[name]) and f[name] >= 0):
                errors.append(f"{where}: {name} must be an int >= 0")
    if usable:
        errors += check_contiguity(frames, rng if rng_ok else None, label="frames")
    if "n_unique" in doc and doc["n_unique"] != unique:
        errors.append(f"n_unique: {doc['n_unique']!r} != {unique} frames without same_as")
    if "warnings" in doc and not (isinstance(doc["warnings"], list) and all(isinstance(w, str) for w in doc["warnings"])):
        errors.append("warnings: expected a list of strings")
    return errors


def check_cues(doc: Any, duration_s: float | None = None) -> list[str]:
    """Validate a cues.json document: required keys, sequential ids, start < end, order by
    (start, end), single-line normalized text, speaker tags (S1... by first appearance, S?)
    and their counts, and (when given) cues ending within ``duration_s``."""
    if not isinstance(doc, dict):
        return ["cues.json: expected an object"]
    errors = _missing(doc, _required(CuesDoc), "cues.json")
    if doc.get("schema", SCHEMA_IDS["cues"]) != SCHEMA_IDS["cues"]:
        errors.append(f"cues.json: schema {doc.get('schema')!r} != {SCHEMA_IDS['cues']!r}")
    vtt = doc.get("vtt")
    if isinstance(vtt, dict):
        errors += _missing(vtt, _required(VttInfo), "vtt")
        if "path" in vtt and not is_abs_posix(vtt["path"]):
            errors.append(f"vtt.path: not an absolute forward-slash path: {vtt['path']!r}")
        if "style" in vtt and vtt["style"] not in CUE_STYLES:
            errors.append(f"vtt.style: {vtt['style']!r} not in {CUE_STYLES}")
        if "sha1" in vtt and not (isinstance(vtt["sha1"], str) and re.fullmatch(r"[0-9a-f]{40}", vtt["sha1"])):
            errors.append("vtt.sha1: expected 40 lowercase hex chars")
    elif "vtt" in doc:
        errors.append("vtt: expected an object")
    stats = doc.get("stats")
    if isinstance(stats, dict):
        for k, v in stats.items():
            if not (_int(v) and v >= 0):
                errors.append(f"stats.{k}: expected an int >= 0")
    elif "stats" in doc:
        errors.append("stats: expected an object")

    speakers = doc.get("speakers")
    tags: dict[str, int] = {}
    if isinstance(speakers, list):
        for i, sp in enumerate(speakers):
            if not isinstance(sp, dict):
                errors.append(f"speakers[{i}]: expected an object")
                continue
            errors += _missing(sp, _required(Speaker), f"speakers[{i}]")
            tag = sp.get("tag")
            if not (isinstance(tag, str) and _SPK_TAG_RE.match(tag)):
                errors.append(f"speakers[{i}]: tag {tag!r} must be S1, S2, ... or S?")
            elif tag in tags:
                errors.append(f"speakers[{i}]: duplicate tag {tag!r}")
            else:
                tags[tag] = i
    elif "speakers" in doc:
        errors.append("speakers: expected a list")

    cues = doc.get("cues")
    if not isinstance(cues, list):
        errors.append("cues: expected a list")
        return errors
    counts: dict[str, int] = {}
    first_seen: list[str] = []
    prev: tuple[int, int] | None = None
    dur_ms = s_to_ms(duration_s) if _num(duration_s) else None
    for i, c in enumerate(cues):
        where = f"cues[{i}]"
        if not isinstance(c, dict):
            errors.append(f"{where}: expected an object")
            continue
        errors += _missing(c, _required(Cue), where)
        cid, expected = c.get("id"), cue_id(i + 1)
        if cid != expected:
            errors.append(f"{where}: id {cid!r} != {expected!r} (ids must be sequential from C0001)")
        where = f"{where} {cid}"
        s, e = c.get("start"), c.get("end")
        if not (_num(s) and _num(e)):
            errors.append(f"{where}: start/end must be numbers")
        else:
            sm, em = s_to_ms(s), s_to_ms(e)
            if sm < 0:
                errors.append(f"{where}: start {s} < 0")
            if em <= sm:
                errors.append(f"{where}: end {e} must be > start {s}")
            if prev is not None and (sm, em) < prev:
                errors.append(f"{where}: out of order (cues must be sorted by start, end)")
            prev = (sm, em)
            if dur_ms is not None and em > dur_ms:
                errors.append(f"{where}: end {e} is past the duration {duration_s}")
        text = c.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{where}: text must be a non-empty string")
        elif text != " ".join(text.split()) or any(ch in _INVISIBLE for ch in text):
            errors.append(f"{where}: text must be one line with collapsed whitespace and no invisible characters")
        spk = c.get("spk")
        if spk is not None:
            if spk not in tags:
                errors.append(f"{where}: spk {spk!r} is not a speaker tag")
            counts[spk] = counts.get(spk, 0) + 1
            if spk not in first_seen:
                first_seen.append(spk)
        if "approx" in c and not isinstance(c["approx"], bool):
            errors.append(f"{where}: approx must be a boolean")
        src = c.get("src")
        if "src" in c and not (isinstance(src, list) and src and all(_int(x) and x >= 1 for x in src)):
            errors.append(f"{where}: src must be a non-empty list of source indexes >= 1")
        if c.get("utt") is not None and not _int(c.get("utt")):
            errors.append(f"{where}: utt must be an int or null")
    numbered = [t for t in first_seen if t != "S?"]
    expected_order = [f"S{k}" for k in range(1, len(numbered) + 1)]
    if numbered != expected_order:
        errors.append(f"speakers: tags by first appearance are {numbered}, expected {expected_order}")
    if isinstance(speakers, list):
        for tag, idx in tags.items():
            sp = speakers[idx]
            if _int(sp.get("cues")) and sp["cues"] != counts.get(tag, 0):
                errors.append(f"speakers[{idx}] {tag}: cues {sp['cues']} != {counts.get(tag, 0)} cues with this tag")
            if "talk_s" in sp and not (_num(sp["talk_s"]) and sp["talk_s"] >= 0):
                errors.append(f"speakers[{idx}] {tag}: talk_s must be a number >= 0")
    return errors


_LEGACY_KEYS = {"cache_dir": "batches_dir", "batch_frames": "batch_images"}


def check_workflow_args(args: Any) -> list[str]:
    """Validate workflow_args v1 exactly as the Workflow script consumes it: required keys and
    no unknown ones, schema_version 1, forward-slash absolute paths, out_dir inside run_dir,
    batch_images positive ints, start_batch in 1..len+1, valid modes and efforts."""
    if not isinstance(args, dict):
        return ["workflow_args: expected an object"]
    errors = _missing(args, _required(WorkflowArgs), "workflow_args")
    errors += [f"workflow_args: unknown key {k!r}" for k in args if k not in WorkflowArgs.__annotations__]
    if "schema_version" in args and (not _int(args["schema_version"]) or args["schema_version"] != SCHEMA_VERSION):
        errors.append(f"schema_version: {args['schema_version']!r} != {SCHEMA_VERSION}")
    for key in ("run_id", "language"):
        if key in args and not (isinstance(args[key], str) and args[key].strip()):
            errors.append(f"{key}: expected a non-empty string")
    for key in ("run_dir", "objective_path", "roles_dir"):
        if key in args and not is_abs_posix(args[key]):
            errors.append(f"{key}: not an absolute forward-slash path: {args[key]!r}")
    run_dir = args.get("run_dir") if is_abs_posix(args.get("run_dir")) else None
    if run_dir and isinstance(args.get("run_id"), str) and run_dir.rstrip("/").rsplit("/", 1)[-1] != args["run_id"]:
        errors.append(f"run_id: {args['run_id']!r} is not the basename of run_dir {run_dir!r}")
    if "roles_mode" in args and args["roles_mode"] not in ROLES_MODES:
        errors.append(f"roles_mode: {args['roles_mode']!r} not in {ROLES_MODES}")
    if "combined_exists" in args and not isinstance(args["combined_exists"], bool):
        errors.append("combined_exists: expected a boolean")

    options = args.get("options")
    if isinstance(options, dict):
        errors += _missing(options, _required(WorkflowOptions), "options")
        errors += [f"options: unknown key {k!r}" for k in options if k not in WorkflowOptions.__annotations__]
        mb = options.get("max_batches")
        if "max_batches" in options and mb is not None and not (_int(mb) and mb >= 0):
            errors.append("options.max_batches: expected an int >= 0 or null")
        mp = options.get("max_parallel_videos")
        cap = WORKFLOW_DEFAULTS["max_parallel_videos_cap"]
        if "max_parallel_videos" in options and not (_int(mp) and 1 <= mp <= cap):
            errors.append(f"options.max_parallel_videos: expected an int in 1..{cap}")
        for key in ("analyst_effort", "curator_effort"):
            if key in options and options[key] not in EFFORT_LEVELS:
                errors.append(f"options.{key}: {options[key]!r} not in {EFFORT_LEVELS}")
        if "curation" in options and options["curation"] not in CURATION_MODES:
            errors.append(f"options.curation: {options['curation']!r} not in {CURATION_MODES}")
    elif "options" in args:
        errors.append("options: expected an object")

    videos = args.get("videos")
    if not isinstance(videos, list) or not videos:
        if "videos" in args:
            errors.append("videos: expected a non-empty list")
        return errors
    slugs: set[str] = set()
    out_dirs: set[str] = set()
    for i, v in enumerate(videos):
        where = f"videos[{i}]"
        if not isinstance(v, dict):
            errors.append(f"{where}: expected an object")
            continue
        for old, new in _LEGACY_KEYS.items():
            if old in v:
                errors.append(f"{where}: {old!r} was renamed to {new!r} (plan, unified contract)")
        errors += _missing(v, _required(WorkflowVideo), where)
        errors += [f"{where}: unknown key {k!r}" for k in v if k not in WorkflowVideo.__annotations__ and k not in _LEGACY_KEYS]
        slug = v.get("slug")
        if "slug" in v:
            if not (isinstance(slug, str) and _SLUG_RE.match(slug) and len(slug) <= 40):
                errors.append(f"{where}: slug {slug!r} must be ASCII [a-z0-9-], at most 40 chars")
            elif slug in slugs:
                errors.append(f"{where}: duplicate slug {slug!r}")
            else:
                slugs.add(slug)
        if "title" in v and not isinstance(v["title"], str):
            errors.append(f"{where}: title must be a string")
        if "duration" in v and not (isinstance(v["duration"], str) and _HMS_RE.match(v["duration"])):
            errors.append(f"{where}: duration {v['duration']!r} must be HH:MM:SS")
        for key in ("batches_dir", "out_dir"):
            if key in v and not is_abs_posix(v[key]):
                errors.append(f"{where}: {key} not an absolute forward-slash path: {v[key]!r}")
        out_dir = v.get("out_dir")
        if is_abs_posix(out_dir):
            if out_dir in out_dirs:
                errors.append(f"{where}: duplicate out_dir {out_dir!r}")
            out_dirs.add(out_dir)
            if run_dir and not out_dir.startswith(run_dir.rstrip("/") + "/"):
                errors.append(f"{where}: out_dir {out_dir!r} is not inside run_dir")
        images = v.get("batch_images")
        images_ok = isinstance(images, list) and bool(images) and all(_int(n) and n > 0 for n in images)
        if "batch_images" in v and not images_ok:
            errors.append(f"{where}: batch_images must be a non-empty list of positive ints")
        n_frames = v.get("n_frames")
        if "n_frames" in v and not (_int(n_frames) and n_frames > 0):
            errors.append(f"{where}: n_frames must be a positive int")
        elif images_ok and sum(images) > n_frames:
            errors.append(f"{where}: sum(batch_images) {sum(images)} > n_frames {n_frames}")
        start = v.get("start_batch")
        if "start_batch" in v:
            if not (_int(start) and start >= 1):
                errors.append(f"{where}: start_batch must be an int >= 1")
            elif images_ok and start > len(images) + 1:
                errors.append(f"{where}: start_batch {start} > number of batches + 1 ({len(images) + 1})")
        for key in ("ledger_exists", "start_prompt_exists", "final_exists"):
            if key in v and not isinstance(v[key], bool):
                errors.append(f"{where}: {key} must be a boolean")
    return errors
