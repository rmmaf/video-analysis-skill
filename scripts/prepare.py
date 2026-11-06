#!/usr/bin/env python
"""Deterministic preparation for the video-analysis skill: unique frames, subtitles, batches and workflow_args.

From the skill (always the Bash tool, always ``python``, never ``python3``):
    python prepare.py --request "<home>/requests/request.md" --estimate
    python prepare.py --request "<home>/requests/request.md"
    python prepare.py --resume <run_id|run_dir|latest> [--roles agents|inline] [--max-batches N] [--curation plan|batches]
The Portuguese option names of before the rename (--estimar, --papeis, --max-lotes, --curadoria plano|lotes,
--forcar…) still work.
Manual use:
    python prepare.py INPUT [INPUT ...] [--objective-file MD | --objective "text"] [--run [NAME]] [options]
    python prepare.py --gc [DAYS]

stdout: exactly one JSON line (the last one), also written to <run_dir>/prepare.json and to logs/.
stderr: first "run_dir: …", then the progress; <run_dir>/status.json is updated every 5 s.
Webcam timeline: with the DeepFace environment of the setup, before extracting the frames, prepare.py runs
scripts/webcam_mask.py in it (a probe, then the full scan if a face shows up) and keeps the timeline in the
video's cache (--webcam-timeline auto|on|off; scripts/va/webcam.py).
Exit code: 0 ok · 2 input · 3 environment · 4 a video failed (the others come in the JSON) · 130 interrupted.
Contracts: reference/contracts.md; full CLI: docs/design-deterministic.md §2.
"""
import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")  # also for spawned worker processes

import argparse  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
# multiprocessing, concurrent.futures and traceback are imported where they are used: a fully
# cached run never needs them (design §9: warm cache <= 2 s per video)

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import va  # noqa: E402
from va import WEBCAM_DEFAULTS, WORKFLOW_DEFAULTS, WORKSPACE_DIRS, environment, cache, contracts, discover, runs, webcam  # noqa: E402
from va.common import (as_posix, fmt_hms, parse_time, read_json, resolve_unicode_paths, setup_console,  # noqa: E402
                       video_title, workspace_home, write_json, write_text)

SCRIPT_PATH = as_posix(SKILL_DIR / "workflow" / "video-analysis.js")
WEBCAM_SCRIPT = SCRIPTS_DIR / "webcam_mask.py"   # run with the Python of the DeepFace environment
STATUS_INTERVAL_S = 5.0
LONG_PATH = 240
DEFAULT_LANGUAGE = "pt-BR"

EXIT = {
    "invalid_usage": 2, "invalid_request": 2, "missing_objective": 2, "invalid_option": 2,
    "video_not_found": 2, "missing_vtt": 2, "run_not_found": 2, "inconsistent_run": 2,
    "run_too_large": 2,
    "missing_ffmpeg": 3, "missing_dependency": 3, "workspace_not_writable": 3, "incomplete_install": 3,
    "video_failed": 4,
    "interrupted": 130,
    "internal_error": 1,
}
_REASONS = {"not_found": "nothing at this path", "folder_without_videos": "folder without videos (the search does not go into subfolders)",
            "glob_without_videos": "the wildcard matched no video"}


class Fail(Exception):
    """Stops prepare.py with ``{"ok": false, "error": {code, detail}}`` and the code's exit status."""

    def __init__(self, code: str, detail: str, **extra):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.extra = code, detail, extra


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Fail("invalid_usage", f"{message} (see python prepare.py --help)")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="prepare.py", description=__doc__.splitlines()[0],
                formatter_class=argparse.RawDescriptionHelpFormatter, epilog="\n".join(__doc__.splitlines()[1:]))
    p.add_argument("inputs", nargs="*", metavar="INPUT", help="video, folder or wildcard (* and ?) — manual use")
    m = p.add_argument_group("mode")
    m.add_argument("--request", "--pedido", dest="request", metavar="MD",
                   help="request.md with # videos, # objective and # options (the skill's input; --pedido is the old name)")
    m.add_argument("--estimate", "--estimar", dest="estimate", action="store_true",
                   help="ffprobe and cache only: estimates the time; does not consume the request or create a run")
    m.add_argument("--resume", "--retomar", dest="resume", metavar="RUN", help="run_id, run folder or latest")
    m.add_argument("--gc", nargs="?", const=30.0, type=float, metavar="DAYS",
                   help="deletes from the cache the videos unused for more than DAYS (default 30)")
    g = p.add_argument_group("general")
    g.add_argument("--home", metavar="FOLDER", help="central folder (default: VIDEO_ANALYSIS_HOME or %%USERPROFILE%%\\video-analysis)")
    g.add_argument("--jobs", type=int, default=2, help="videos processed in parallel, in separate processes (default 2)")
    g.add_argument("--force", nargs="?", const="all", choices=["all", "cues", "frames", "batches"],
                   help="redoes the stage and the following ones (no value: all)")
    g.add_argument("--ignore-agent-limit", "--forcar", dest="ignore_agent_limit", action="store_true",
                   help="prepares even above 800 estimated agents (diagnostics: the Workflow refuses; split the run)")
    g.add_argument("--vtt", metavar="FILE", help="explicit subtitles (only with exactly 1 video)")
    g.add_argument("--skip-missing", action="store_true", help="skips the videos without a .vtt of the same name")
    g.add_argument("--recursive", action="store_true", help="also looks for videos in subfolders")
    g.add_argument("--run", nargs="?", const="", metavar="NAME", help="creates runs/<stamp>-<NAME> (manual use)")
    o = g.add_mutually_exclusive_group()
    o.add_argument("--objective-file", "--objetivo-arquivo", dest="objective_file", metavar="MD")
    o.add_argument("--objective", "--objetivo", dest="objective", metavar="TEXT")
    g.add_argument("--json-out", metavar="FILE", help="also writes the JSON to this file")
    g.add_argument("--ascii", action="store_true", help="ASCII-only JSON (\\uXXXX)")
    g.add_argument("--quiet", action="store_true", help="no progress on stderr (the run_dir line stays)")
    g.add_argument("--debug", action="store_true", help="full traceback on stderr")
    a = p.add_argument_group("request options (the command line's value prevails)")
    a.add_argument("--roles", "--papeis", dest="roles", metavar="agents|inline")
    a.add_argument("--max-batches", "--max-lotes", dest="max_batches", metavar="N")
    a.add_argument("--parallel", "--paralelo", dest="parallel", metavar="N")
    a.add_argument("--analyst-effort", "--esforco-analista", dest="analyst_effort", metavar="LEVEL")
    a.add_argument("--curator-effort", "--esforco-curador", dest="curator_effort", metavar="LEVEL")
    a.add_argument("--curation", "--curadoria", dest="curation", metavar="plan|batches",
                   help="batches = curator before each batch (default: plan; with --resume, the run's, or batches for a run older than the option)")
    a.add_argument("--language", "--idioma", dest="language", metavar="xx")
    a.add_argument("--range", "--trecho", dest="range", metavar="hh:mm:ss-hh:mm:ss")
    a.add_argument("--speakers", "--speaker-names", "--falantes", dest="speakers", metavar='"SPEAKER_01=Name;…"')
    a.add_argument("--sensitivity", "--sensibilidade", dest="sensitivity", metavar="low|normal|high")
    a.add_argument("--ignore-region", "--ignorar-regiao", dest="ignore_region", action="append", metavar="x,y,w,h")
    a.add_argument("--ignore-timeline", "--ignorar-linha-do-tempo", dest="ignore_timeline", action="append",
                   metavar="FILE.json", help="regions ignored only during spans of the video (scripts/webcam_mask.py "
                                             "--timeline writes it); one file per video, each naming its video")
    a.add_argument("--webcam-timeline", "--linha-do-tempo-webcam", dest="webcam_timeline", metavar="auto|on|off",
                   help="the webcam timeline made with DeepFace, when its environment exists (setup.md, step 6): "
                        "auto (default) probes the video and runs the full scan only if a face shows up; "
                        "on always runs the full scan (without the environment: exit 3); off never runs it")
    d = p.add_argument_group("extraction and batches (manual use; design §2)")
    d.add_argument("--start", metavar="T", help="start of the extracted range ([HH:]MM:SS[.mmm] or seconds)")
    d.add_argument("--end", metavar="T", help="end of the extracted range")
    d.add_argument("--vtt-offset", type=float, metavar="SEC")
    d.add_argument("--sample-fps", type=float)
    d.add_argument("--settle", type=float)
    d.add_argument("--max-settle-wait", type=float)
    d.add_argument("--motion-interval", type=float)
    d.add_argument("--motion-max-interval", type=float)
    d.add_argument("--cursor-size", type=float)
    d.add_argument("--hot-mode", choices=["windowed", "off"])
    d.add_argument("--no-revisit", action="store_true")
    d.add_argument("--max-edge", type=int)
    d.add_argument("--jpeg-q", type=int)
    d.add_argument("--sequential", action="store_true", help="separate passes A and B (same output; diagnostics)")
    d.add_argument("--batch-frames", type=int)
    d.add_argument("--batch-text", type=int)
    d.add_argument("--batch-span", type=float)
    d.add_argument("--no-soft-cuts", action="store_true")
    return p


# --- context, logging, output ----------------------------------------------------------------------

class Ctx:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.home: Path | None = None
        self.mode = "prepare"
        self.run_id: str | None = None
        self.run_dir: Path | None = None
        self.log_path: Path | None = None
        self.json_log: Path | None = None
        self.json_out: str | None = None
        self.ascii = False
        self.quiet = False
        self.debug = False
        self.stderr_on = False
        self.request: Path | None = None
        self.consume = False
        self.partial_run = False
        self.status: "Status | None" = None
        self.warnings: list[str] = []

    def log(self, msg: str, echo: bool = True) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        if self.log_path is not None:
            with contextlib.suppress(OSError):
                with open(self.log_path, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
        if echo and self.stderr_on and not self.quiet:
            print(line, file=sys.stderr, flush=True)

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0


def envelope(ctx: Ctx, ok: bool, error: dict | None = None, **fields) -> dict:
    env: dict = {"ok": ok}
    if error is not None:
        env["error"] = error
    env.update(mode=ctx.mode, version=va.VERSION, home=as_posix(ctx.home) if ctx.home else None,
               run_id=ctx.run_id, run_dir=as_posix(ctx.run_dir) if ctx.run_dir else None, json_path=None)
    defaults = {"videos": [], "estimate": None, "env": None, "script_path": SCRIPT_PATH, "workflow_args": None,
                "errors": [], "warnings": list(ctx.warnings)}
    for key, default in defaults.items():
        env[key] = fields.pop(key, default)
    env.update(fields)
    return env


def emit(ctx: Ctx, env: dict, code: int) -> int:
    env = cache.plain(env)
    targets = []
    if ctx.run_dir is not None and ctx.run_dir.is_dir():
        targets.append(ctx.run_dir / "prepare.json")
    if ctx.json_log is not None:
        targets.append(ctx.json_log)
    if ctx.json_out:
        targets.append(Path(ctx.json_out))
    env["json_path"] = as_posix(targets[0]) if targets else None
    for t in targets:
        try:
            write_json(t, env)
        except (OSError, ValueError) as exc:
            ctx.log(f"could not write {as_posix(t)}: {exc}", echo=False)
    line = json.dumps(env, ensure_ascii=ctx.ascii, separators=(",", ":"), allow_nan=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return code


def ensure_home(ctx: Ctx) -> None:
    st = environment.workspace_status(ctx.home, create=True)
    if not st["ok"]:
        raise Fail("workspace_not_writable", st["detail"])


def open_log(ctx: Ctx, stem: str) -> None:
    logs = ctx.home / WORKSPACE_DIRS["logs"]
    ctx.log_path = logs / f"{stem}.log"
    ctx.json_log = logs / f"{stem}.json"


def rename_log(ctx: Ctx, stem: str) -> None:
    old_log = ctx.log_path
    open_log(ctx, stem)
    if old_log is not None and old_log != ctx.log_path and old_log.exists():
        with contextlib.suppress(OSError):
            os.replace(old_log, ctx.log_path)


# --- status.json ------------------------------------------------------------------------------------

class Status:
    """<run_dir>/status.json, refreshed every STATUS_INTERVAL_S from the workers' progress files."""

    def __init__(self, ctx: Ctx, videos: list[dict]):
        self.path = ctx.run_dir / "status.json"
        self.progress_dir = ctx.home / WORKSPACE_DIRS["tmp"] / f"progress-{ctx.run_id}"
        self.progress_dir.mkdir(parents=True, exist_ok=True)
        self.t0 = time.monotonic()
        self.doc = {"run_id": ctx.run_id, "mode": ctx.mode, "state": "preparing", "started": cache.iso_now(),
                    "updated": None, "elapsed_s": 0,
                    "videos": [{"vid": v["vid"], "slug": v["slug"], "title": v["title"], "stage": None,
                                "state": "queued", "info": None} for v in videos]}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def progress_path(self, vid: str) -> str:
        return str(self.progress_dir / f"{vid}.json")

    def write(self) -> None:
        with self._lock:
            for v in self.doc["videos"]:
                with contextlib.suppress(OSError, ValueError):
                    p = read_json(self.progress_path(v["vid"]))
                    v.update(stage=p.get("stage"), state=p.get("state"), info=p.get("info"))
            self.doc["updated"] = cache.iso_now()
            self.doc["elapsed_s"] = round(time.monotonic() - self.t0, 1)
            with contextlib.suppress(OSError, ValueError):
                write_json(self.path, self.doc)

    def _loop(self) -> None:
        while not self._stop.wait(STATUS_INTERVAL_S):
            self.write()

    def start(self) -> None:
        self.write()
        self._thread = threading.Thread(target=self._loop, name="va-status", daemon=True)
        self._thread.start()

    def finish(self, state: str) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self.write()
        with self._lock:
            self.doc["state"] = state
            with contextlib.suppress(OSError, ValueError):
                write_json(self.path, self.doc)
        cache.rmtree_robust(self.progress_dir)


# --- inputs and options -------------------------------------------------------------------------------

def gather(ctx: Ctx, args) -> tuple[list[str], str | None, dict]:
    if args.request:
        ctx.request = Path(os.path.abspath(args.request))
        ctx.consume = not args.estimate  # any later failure renames it to request.error.md
        if args.inputs:
            raise Fail("invalid_usage", "use --request or direct inputs (INPUT…), not both")
        if args.objective or args.objective_file:
            raise Fail("invalid_usage", "with --request, the objective comes from the '# objective' section")
        req = runs.read_request(ctx.request)
        entries, objective, raw = req["videos"], req["objective"], dict(req["options"])
    else:
        if not args.inputs:
            raise Fail("invalid_usage", "provide --request <md>, videos (INPUT…), --resume <run> or --gc")
        entries, raw = list(args.inputs), {}
        objective = None
        if args.objective_file:
            hits = [p for p in resolve_unicode_paths(args.objective_file) if p.is_file()]
            if len(hits) != 1:
                raise Fail("missing_objective", f"objective file not found: {args.objective_file}")
            data = hits[0].read_bytes()
            try:
                objective = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                objective = data.decode("cp1252", errors="replace")
        elif args.objective is not None:
            objective = args.objective
        if objective is not None and not objective.strip():
            raise Fail("missing_objective", "the objective is empty")
    for key in runs.OPTION_KEYS:
        value = getattr(args, key, None)
        if value is not None:
            raw[key] = list(value) if isinstance(value, list) else value
    return entries, objective, raw


def _num(args, name: str, lo: float, hi: float, label: str) -> float | None:
    value = getattr(args, name)
    if value is None:
        return None
    if not (lo <= value <= hi):
        raise Fail("invalid_option", f"{label}: {value} outside {lo}–{hi}")
    return value


def stage_overrides(args, opts: dict) -> dict:
    """User overrides per stage: request options plus the manual flags of design §2."""
    fr: dict = {}
    cu: dict = {}
    ba: dict = {}
    if opts.get("sensitivity"):
        fr["sensitivity"] = opts["sensitivity"]
    if opts.get("ignore_region"):
        fr["ignore_regions"] = opts["ignore_region"]
    for name, key, lo, hi in (("sample_fps", "sample_fps", 0.1, 30.0), ("settle", "settle_s", 0.0, 30.0),
                              ("max_settle_wait", "max_settle_wait_s", 0.0, 120.0),
                              ("motion_interval", "motion_interval_s", 0.5, 600.0),
                              ("motion_max_interval", "motion_max_interval_s", 0.5, 3600.0)):
        v = _num(args, name, lo, hi, "--" + name.replace("_", "-"))
        if v is not None:
            fr[key] = v
    cs = _num(args, "cursor_size", 0.005, 0.25, "--cursor-size")
    if cs is not None:
        fr["cursor_size"] = cs  # the frames module derives cursor_h_frac/cursor_w_frac (width = 2/3)
    if args.hot_mode:
        fr["hot_mode"] = args.hot_mode
    if args.no_revisit:
        fr["revisit"] = False
    me = _num(args, "max_edge", 64, va.OUTPUT_DEFAULTS["max_edge_cap"], "--max-edge")
    if me is not None:
        fr["max_edge"] = int(me)
    jq = _num(args, "jpeg_q", va.OUTPUT_DEFAULTS["jpeg_q_min"], va.OUTPUT_DEFAULTS["jpeg_q_max"], "--jpeg-q")
    if jq is not None:
        fr["jpeg_q"] = int(jq)
    if args.sequential:
        fr["sequential"] = True  # execution only: same output, not part of the frames key
    if args.start is not None or args.end is not None:
        try:
            start = parse_time(args.start) if args.start is not None else 0.0
            end = parse_time(args.end) if args.end is not None else None
        except ValueError as exc:
            raise Fail("invalid_option", f"--start/--end: {exc}") from None
        if start < 0 or (end is not None and end <= start):
            raise Fail("invalid_option", "--end must be after --start")
        fr["range"] = [start, end]
    if opts.get("speakers"):
        cu["speaker_names"] = opts["speakers"]
    if args.vtt_offset is not None:
        cu["offset_s"] = float(args.vtt_offset)
    bf = _num(args, "batch_frames", 1, 99, "--batch-frames")
    if bf is not None:
        ba["max_frames"] = int(bf)  # batching derives soft_min and the tail limits from it
    bt = _num(args, "batch_text", 500, 200000, "--batch-text")
    if bt is not None:
        ba["max_text_chars"] = int(bt)
    bs = _num(args, "batch_span", 10.0, 86400.0, "--batch-span")
    if bs is not None:
        ba["max_span_s"] = float(bs)
    if args.no_soft_cuts:
        ba["soft_cuts"] = False
    return {"frames": fr, "cues": cu, "batches": ba}


def effective_params(overrides: dict, segment: list | None) -> dict:
    try:
        fr = cache.stage_fn("effective_frame_params")(dict(overrides["frames"]))
        cu = cache.stage_fn("effective_cue_params")(dict(overrides["cues"]))
        bo = dict(overrides["batches"])
        if segment:
            bo["range"] = list(segment)
        ba = cache.stage_fn("effective_batch_params")(bo)
        if segment and isinstance(ba, dict) and "range" not in ba:
            ba = {**ba, "range": list(segment)}
    except cache.StageUnavailable as exc:
        raise Fail("incomplete_install", f"{exc}: reinstall the skill (scripts/va incomplete)") from None
    except (ValueError, TypeError, KeyError) as exc:
        raise Fail("invalid_option", f"invalid parameter: {exc}") from None
    return {"frames": cache.plain(fr), "cues": cache.plain(cu), "batches": cache.plain(ba)}


def video_params(params: dict, overrides: dict, segment: list | None, spans: list) -> dict:
    """The params of one video: the run's, plus its ignore_timeline spans (only then: the frames
    key of a video without a timeline stays the run's)."""
    if not spans:
        return params
    return effective_params({**overrides, "frames": {**overrides["frames"], "ignore_timeline": spans}}, segment)


# --- webcam timeline (scripts/va/webcam.py) ------------------------------------------------------------

def webcam_plan(mode: str, given: list | None, vdir: Path, vkey: str) -> dict:
    """The webcam timeline of one video before the run: ``given`` (the request has an ignore_timeline
    file for it, which prevails, even without spans: None means no file), ``off``, ``cached`` (spans from
    the cache) or ``pending`` (to make: ``scan`` probe with auto, full with on)."""
    if given is not None:
        return {"state": "given", "scan": None, "spans": list(given)}
    if mode == "off":
        return {"state": "off", "scan": None, "spans": []}
    key = webcam.webcam_key(vkey, webcam.score_setting())
    hit = webcam.cached(vdir, key, need_full=mode == "on")
    if hit:
        return {**hit, "state": "cached", "key": key}
    return {"state": "pending", "scan": "full" if mode == "on" else "probe", "spans": [], "key": key}


def webcam_environment(ctx: Ctx, mode: str, known: list[dict]) -> str | None:
    """The Python of the DeepFace environment when some timeline is ``pending``; else None. Without a
    working environment, ``on`` fails (exit 3) and ``auto`` goes on without the timeline
    (``unavailable``), with a warning only when the environment exists but does not run."""
    pending = [k for k in known if k["webcam"]["state"] == "pending"]
    if not pending:
        return None
    st = environment.deepface_status()
    if st["ok"]:
        return st["python"]
    if mode == "on":
        raise Fail("missing_dependency", f"webcam_timeline: on, but {st['detail']}: run /video-analysis setup "
                   "(step 6 creates the DeepFace environment)")
    if st["state"] != "absent":
        ctx.warnings.append(f"webcam timeline skipped: {st['detail']} (run /video-analysis setup)")
    for k in pending:
        k["webcam"] = {**k["webcam"], "state": "unavailable", "scan": None, "detail": st["detail"]}
    return None


def webcam_view(w: dict) -> dict:
    """``videos[].webcam_timeline`` of the stdout (reference/contracts.md, section 3); ``spans`` counts the
    spans of the timeline the frames use (given, cached, made), null otherwise."""
    used = w["state"] in ("given", "cached", "made")
    return {"state": w["state"], "scan": w.get("scan"), "spans": len(w["spans"]) if used else None,
            "path": w.get("path"), "seconds": w.get("seconds") if w["state"] == "made" else None,
            "estimate_s": w.get("estimate_s"), "detail": w.get("detail")}


def make_webcam_timeline(ctx: Ctx, it: dict, python: str, overrides: dict, params: dict, segment: list | None,
                         force: set[str]) -> None:
    """Make the ``pending`` webcam timeline of one video, under the video's lock, and recompute its keys
    and cache state with the spans. A failure becomes a warning: the frames are extracted without it."""
    w, vdir = it["webcam"], it["vdir"]
    scan = w["scan"]
    est = webcam.estimate_seconds(it["info"], scan)
    what = "a probe, then the full scan if a face shows up" if scan == "probe" else "the full scan"
    ctx.log(f"{it['slug']}: webcam timeline with DeepFace ({what}; up to ~{max(1, round(est / 60))} min)")
    if ctx.status is not None:
        with contextlib.suppress(OSError, ValueError):
            write_json(ctx.status.progress_path(it["vid"]), {"vid": it["vid"], "stage": "webcam", "state": "processing",
                                                             "updated": cache.iso_now(), "info": {"scan": scan}})
    try:
        vdir.mkdir(parents=True, exist_ok=True)
        with cache.VideoLock(vdir / ".lock", wait_s=WEBCAM_DEFAULTS["lock_wait_s"],
                             on_wait=lambda m: ctx.log(f"{it['slug']}: {m}")):
            hit = webcam.cached(vdir, w["key"], need_full=scan == "full")   # made by another prepare.py meanwhile
            if hit:
                w.update(hit, state="cached")
            else:
                w.update(webcam.make_timeline(python=python, script=WEBCAM_SCRIPT, video_path=it["video"],
                                              video_dir=vdir, key=w["key"], scan=scan, duration_s=it["duration"]),
                         state="made")
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # optional: the video goes on without it
        detail = str(exc) if isinstance(exc, webcam.WebcamError) else f"{type(exc).__name__}: {exc}"
        w.update(state="failed", spans=[], detail=detail)
        it["warn"].append(f"webcam timeline failed ({detail}); frames extracted without it")
        ctx.log(f"{it['slug']}: webcam timeline failed: {detail}")
    else:
        took = f", {w['seconds']:.0f} s" if w["state"] == "made" else ", from the cache"
        ctx.log(f"{it['slug']}: webcam timeline: {len(w['spans'])} span(s) ({w['scan']}{took})")
    vparams = video_params(params, overrides, segment, w["spans"])
    keys = cache.stage_keys(it["video_key"], it["vtt"], vparams, it["meta"])
    it.update(params=vparams, keys=keys, hits=cache.stage_hits(vdir, keys, force))


def discover_pairs(ctx: Ctx, args, entries: list[str]) -> list[tuple[Path, Path]]:
    videos, problems = discover.expand_inputs(entries, recursive=args.recursive)
    if problems:
        detail = "; ".join(f"{p['input']} ({_REASONS.get(p['reason'], p['reason'])})" for p in problems)
        raise Fail("video_not_found", f"not found: {detail}. Check the path (in quotes, if it has spaces); "
                   "accents, ’, double spaces and brackets are already handled", inputs=[p["input"] for p in problems])
    if not videos:
        raise Fail("video_not_found", "no video in the inputs")
    explicit = None
    if args.vtt:
        if len(videos) != 1:
            raise Fail("invalid_usage", f"--vtt only works with exactly 1 video (the inputs give {len(videos)})")
        hits = [p for p in resolve_unicode_paths(args.vtt) if p.is_file()]
        if len(hits) != 1:
            raise Fail("missing_vtt", f"--vtt points to a file that does not exist: {args.vtt}")
        explicit = hits[0]
    pairs, skipped, missing = discover.match_vtts(videos, explicit, skip_missing=args.skip_missing)
    if missing:
        raise Fail("missing_vtt", discover.missing_vtt_detail(missing, allow_vtt_flag=len(videos) == 1),
                   missing_vtt=[{"video": as_posix(m["video"]), "candidates": [as_posix(c["path"]) for c in m["candidates"]],
                                 "suggestion": m["suggestion"]} for m in missing])
    if skipped:
        ctx.warnings.append("no subtitles with the same name, skipped by --skip-missing: " + "; ".join(p.name for p in skipped))
    if not pairs:
        raise Fail("missing_vtt", "no video with a .vtt subtitle file of the same name (all skipped by --skip-missing): "
                   + "; ".join(p.name for p in skipped))
    for video, _ in pairs:
        if len(str(video)) > LONG_PATH:
            ctx.warnings.append(f"path longer than {LONG_PATH} characters (close to the Windows limit): {video.name}")
    return pairs


def runtime_check(ctx: Ctx, need: tuple[str, ...]) -> str | None:
    """Fail with exit 3 when something in ``need`` is missing; returns the ffmpeg version text when
    ffmpeg is in ``need`` (recorded in the frames stage.json)."""
    if not need:
        return None
    problems = environment.runtime_problems(need)
    if problems:
        code = "missing_ffmpeg" if any(p["code"] == "missing_ffmpeg" for p in problems) else "missing_dependency"
        raise Fail(code, "; ".join(p["detail"] for p in problems) + ": run /video-analysis setup")
    if "ffmpeg" in need:
        with contextlib.suppress(Exception):
            return environment.tool_status("ffmpeg").get("version")
    return None


def estimate_seconds(info, hits: dict, frame_params: dict | None = None) -> float:
    """Rough wall time of the missing stages. Frames: the frames stage's own estimate when it
    can give one, else decode-bound rates (~0.06x realtime for Teams 1080p16, ~0.15x for 1080p60;
    design §0 and §9)."""
    dur = float(cache.info_get(info, "duration_s", "duration", default=0.0) or 0.0)
    s = 0.5
    if not hits.get("cues"):
        s += 0.5
    if not hits.get("frames"):
        frames_s = None
        with contextlib.suppress(Exception):
            frames_s = float(cache.stage_fn("estimate_frames_seconds")(info, frame_params))
        if frames_s is None or not frames_s >= 0:
            w = cache.info_get(info, "width", default=1920) or 1920
            h = cache.info_get(info, "height", default=1080) or 1080
            fps = cache.info_get(info, "fps_avg", "fps", default=30.0) or 30.0
            mpx = float(w) * float(h) * float(fps) / 1e6
            frames_s = min(0.30, max(0.04, 0.03 + 0.001 * mpx)) * dur + 2.0
        s += frames_s
    if not hits.get("batches"):
        s += 1.0 + 0.004 * dur
    return s


def _duration(info) -> float:
    d = cache.info_get(info, "duration_s", "duration")
    try:
        d = float(d)
    except (TypeError, ValueError):
        d = 0.0
    if not d > 0:
        raise ValueError("ffprobe did not report the duration (file without a video track?)")
    return d


def env_status(ctx: Ctx) -> tuple[dict, dict]:
    """(``env`` of the envelope, agents_status). The Claude home is the one check_environment.py checks
    by default (env VA_CLAUDE_HOME, else %USERPROFILE%\\.claude). ``workflow_current``: the saved
    workflow (<claude home>/workflows/video-analysis.js) is byte-identical to the skill's script, so
    the Workflow can run it by name; otherwise the skill passes the script inline."""
    chome = environment.claude_home()
    agents = environment.agents_status(SKILL_DIR, chome)
    env = {"permissions_ok": bool(environment.permissions_status(ctx.home, chome)["ok"]),
           "agents_current": bool(agents["current"]),
           "workflow_current": bool(environment.workflow_status(SKILL_DIR, chome)["current"])}
    return env, agents


def roles_and_env(ctx: Ctx, requested: str) -> tuple[str, dict]:
    roles = runs.copy_roles(SKILL_DIR, ctx.home / WORKSPACE_DIRS["roles"])
    if not roles:
        ctx.warnings.append("the skill has no roles in agents/: inline mode will not find the role")
    env, agents = env_status(ctx)
    mode = requested
    if requested == "agents" and not agents["current"]:
        mode = "inline"
        ctx.warnings.append("roles: agents requested, but the copies in ~/.claude/agents are not identical to the skill's "
                            f"({agents['detail']}); using inline. Run /video-analysis setup and reopen Claude Code")
    return mode, env


def guard(ctx: Ctx, agents: int, ignore_limit: bool, **extra) -> None:
    limit = WORKFLOW_DEFAULTS["max_agents"]
    if agents > limit and not ignore_limit:
        raise Fail("run_too_large", f"~{agents} estimated agents (limit {limit}; the Workflow refuses above that): "
                   "split into smaller runs, use --range or --max-batches", **extra)
    if agents > limit:
        ctx.warnings.append(f"~{agents} agents (--ignore-agent-limit): the Workflow refuses more than {limit} agents; split the run "
                            "(--range/--max-batches or fewer videos) before launching")


def video_entry(vid: str, slug: str, title: str, res: dict, extra_warnings: list[str]) -> dict:
    summary = {k: v for k, v in (res.get("summary") or {}).items() if k != "schema"}
    entry = {"vid": vid, "slug": slug, **summary}
    entry.setdefault("title", title)
    entry["warnings"] = list(summary.get("warnings") or []) + list(res.get("warnings") or []) + extra_warnings
    entry["cache_hit"] = res.get("cache_hit") or {}
    entry["timing_s"] = res.get("timing_s") or {}
    return entry


def span(summary: dict, duration: float, segment: list | None) -> float:
    rng = summary.get("range") if isinstance(summary, dict) else None
    if isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng) and rng[1] > rng[0]:
        t0, t1 = float(rng[0]), float(rng[1])
    else:
        t0, t1 = 0.0, duration
    if segment:
        t0, t1 = max(t0, segment[0]), min(t1, segment[1])
    return max(0.0, t1 - t0)


def pool_plan(jobs: list[dict], n_jobs: int) -> list[int]:
    """Indexes of the jobs that go to the ProcessPool, in submission order. Fully cached jobs
    (``warm``) stay in-process: they only read files, and a spawned worker costs more than the
    whole job. The others go to the pool when there are 2+ of them and --jobs > 1, longest first
    (``cost`` = estimated seconds), so a long video does not start last while the other worker
    idles (longest-processing-time order). Empty: everything runs in-process (also with the
    in-process test overrides)."""
    cold = [i for i, job in enumerate(jobs) if not job.get("warm")]
    if n_jobs <= 1 or len(cold) <= 1 or cache.STAGE_OVERRIDES:
        return []
    return sorted(cold, key=lambda i: (-float(jobs[i].get("cost") or 0.0), i))


def run_jobs(ctx: Ctx, jobs: list[dict], n_jobs: int) -> list[dict]:
    """Run ``cache.prepare_video`` for each job, in-process or in a spawn ProcessPool (see
    ``pool_plan``); the results come back in input order."""
    results: list[dict | None] = [None] * len(jobs)
    pooled = pool_plan(jobs, n_jobs)
    for i, job in enumerate(jobs):
        if i not in pooled:
            ctx.log(f"[{i + 1}/{len(jobs)}] {job['slug']}")
            results[i] = cache.prepare_video(job)
    if not pooled:
        return results  # type: ignore[return-value]
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    workers = min(n_jobs, len(pooled))
    ctx.log(f"{len(pooled)} videos, {workers} in parallel")
    ex = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                             initializer=cache.child_init)
    try:
        futures = {ex.submit(cache.prepare_video, jobs[i]): i for i in pooled}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # BrokenProcessPool, pickling problems
                results[i] = {"vid": jobs[i]["vid"], "ok": False, "cache_hit": {}, "timing_s": {}, "warnings": [],
                              "error": {"code": "video_failed", "detail": f"the video's process died: {type(exc).__name__}: {exc}"}}
    except KeyboardInterrupt:
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)
    return [r if r is not None else {"vid": jobs[i]["vid"], "ok": False, "cache_hit": {}, "timing_s": {}, "warnings": [],
                                     "error": {"code": "video_failed", "detail": "the video's process returned no result"}}
            for i, r in enumerate(results)]


def option_lines(opts: dict, wopts: dict, roles_mode: str, language: str) -> list[str]:
    lines = [f"papeis: {roles_mode}",
             f"max_lotes: {wopts['max_batches'] if wopts['max_batches'] else 'todos'}",
             f"paralelo: {wopts['max_parallel_videos']}",
             f"esforco_analista: {wopts['analyst_effort']}",
             f"esforco_curador: {wopts['curator_effort']}",
             f"curadoria: {'lotes' if wopts['curation'] == 'batches' else 'plano'}",
             f"idioma: {language}"]
    if opts.get("range"):
        lines.append(f"trecho: {fmt_hms(opts['range'][0], 0)}-{fmt_hms(opts['range'][1], 0)}")
    if opts.get("speakers"):
        lines.append("falantes: " + "; ".join(f"{k}={v}" for k, v in opts["speakers"].items()))
    if opts.get("sensitivity"):
        lines.append(f"sensibilidade: {opts['sensitivity']}")
    if opts.get("ignore_region"):
        lines.append("ignorar_regiao: " + "; ".join(",".join(f"{x:g}" for x in r) for r in opts["ignore_region"]))
    for t in opts.get("ignore_timeline") or []:
        target = f", vídeo {Path(t['video']).name}" if t["video"] else ""
        lines.append(f"ignorar_linha_do_tempo: {t['file']} ({len(t['spans'])} trecho(s){target})")
    if opts.get("webcam_timeline"):
        lines.append(f"linha_do_tempo_webcam: {opts['webcam_timeline']}")
    return lines


def check_args(wargs: dict) -> None:
    problems = contracts.check_workflow_args(wargs)
    if problems:
        raise Fail("internal_error", "invalid workflow_args generated by prepare.py: " + "; ".join(problems[:5]))


# --- modes ----------------------------------------------------------------------------------------------

def run_gc(ctx: Ctx, args) -> int:
    ctx.mode = "gc"
    if args.inputs or args.request or args.resume or args.estimate:
        raise Fail("invalid_usage", "--gc runs alone")
    if args.gc < 0:
        raise Fail("invalid_option", "--gc: DAYS must be 0 or more")
    ensure_home(ctx)
    open_log(ctx, f"gc-{time.strftime('%Y%m%d-%H%M%S')}")
    ctx.stderr_on = True
    res = cache.gc_cache(ctx.home / WORKSPACE_DIRS["cache"], args.gc)
    ctx.log(f"gc: {len(res['removed'])} video(s) deleted from the cache, {res['freed_mb']} MB freed; "
            f"{res['kept']} kept")
    errors = [{"key": f["key"], "code": "gc_failed", "detail": "could not delete (file in use?)"} for f in res["failed"]]
    return emit(ctx, envelope(ctx, True, errors=errors, gc=res), 0)


def run_prepare(ctx: Ctx, args) -> int:
    estimate = bool(args.estimate)
    ctx.mode = "estimate" if estimate else "prepare"
    entries, objective, raw = gather(ctx, args)
    try:
        opts = runs.normalize_options(raw)
    except runs.RunError as exc:
        raise Fail(exc.code, exc.detail) from None
    make_run = not estimate and (ctx.request is not None or objective is not None or args.run is not None)
    if args.run is not None and objective is None and not estimate:
        raise Fail("missing_objective", "--run needs --objective-file or --objective")
    ensure_home(ctx)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    open_log(ctx, f"{ctx.mode}-{stamp}")
    ctx.log(f"prepare.py {va.VERSION} · mode {ctx.mode} · home {as_posix(ctx.home)} · argv {sys.argv[1:]}", echo=False)

    pairs = discover_pairs(ctx, args, entries)
    overrides = stage_overrides(args, opts)
    params = effective_params(overrides, opts.get("range"))
    try:
        spans = runs.timeline_spans(opts.get("ignore_timeline"), [v for v, _ in pairs])
    except runs.RunError as exc:
        raise Fail(exc.code, exc.detail) from None
    force = cache.expand_force(args.force)
    wopts = runs.workflow_options(opts)
    language = opts.get("language") or DEFAULT_LANGUAGE
    segment = opts.get("range")
    wmode = opts.get("webcam_timeline") or WEBCAM_DEFAULTS["mode"]

    # keys and cache state first: stat calls and small JSON reads, no subprocess and no frame
    # engine import, so a fully cached run needs neither ffprobe nor ffmpeg (design §9: warm
    # cache <= 2 s per video)
    errors: list[dict] = []
    known = []
    slugs = runs.unique_slugs([v.stem for v, _ in pairs])

    def unreadable(k: dict, exc: OSError) -> None:
        errors.append({"vid": k["vid"], "slug": k["slug"], "video": as_posix(k["video"]), "code": "video_failed",
                       "detail": f"read: {type(exc).__name__}: {exc}"})
        ctx.log(f"{k['slug']}: could not read the video or the subtitles: {exc}", echo=False)

    for i, ((video, vtt), slug) in enumerate(zip(pairs, slugs)):
        vid, title = f"v{i + 1}", video_title(video)
        k = {"vid": vid, "slug": slug, "title": title, "video": video, "vtt": vtt,
             "meta": {"title": title, "video_path": as_posix(video), "vtt_path": as_posix(vtt)}}
        try:
            vkey = cache.video_key(video)
        except OSError as exc:
            unreadable(k, exc)
            continue
        vdir = ctx.home / WORKSPACE_DIRS["cache"] / vkey
        known.append({**k, "video_key": vkey, "vdir": vdir, "webcam": webcam_plan(wmode, spans[i], vdir, vkey)})
    deepface = webcam_environment(ctx, wmode, known)
    for k in list(known):
        vparams = video_params(params, overrides, segment, k["webcam"]["spans"])
        try:
            keys = cache.stage_keys(k["video_key"], k["vtt"], vparams, k["meta"])
        except OSError as exc:
            unreadable(k, exc)
            known.remove(k)
            continue
        hits = cache.stage_hits(k["vdir"], keys, force)
        if k["webcam"]["state"] == "pending":   # the timeline will change the frames key (worst case)
            hits = {**hits, "frames": False, "batches": False}
        info = cache.cached_probe(k["vdir"], k["video"]) if hits["cues"] and hits["frames"] else None
        k.update(keys=keys, params=vparams, hits=hits, info=info, info_from_cache=info is not None)
    need = ["numpy", "pillow"]  # find_spec only: cheap
    if any(k["info"] is None for k in known):
        need.insert(0, "ffprobe")
    if any(not k["hits"]["frames"] for k in known):
        need.insert(0, "ffmpeg")  # --estimar checks it too: the prepare that follows will need it
    ffmpeg_version = runtime_check(ctx, tuple(need))

    # ffprobe for what the cache does not answer (fast; before the run folder exists)
    items = []
    for k in known:
        try:
            info = k["info"] if k["info"] is not None else cache.stage_fn("probe_video")(k["video"])
            duration = _duration(info)
        except cache.StageUnavailable as exc:
            raise Fail("incomplete_install", str(exc)) from None
        except Exception as exc:
            errors.append({"vid": k["vid"], "slug": k["slug"], "video": as_posix(k["video"]), "code": "video_failed",
                           "detail": f"ffprobe: {type(exc).__name__}: {exc}"})
            ctx.log(f"{k['slug']}: ffprobe failed: {exc}", echo=False)
            continue
        if segment and segment[0] >= duration:
            raise Fail("invalid_option", f"range starts at {fmt_hms(segment[0], 0)}, after the end of {k['title']} "
                       f"({fmt_hms(duration, 0)})")
        warn = []
        if segment and segment[1] > duration:
            warn.append(f"range ends after the end of the video ({fmt_hms(duration, 0)}): it runs to the end")
        items.append({**k, "info": info, "duration": duration, "warn": warn})
    if not items:
        raise Fail("video_failed", "no video could be read: " + "; ".join(f"{e['slug']}: {e['detail']}" for e in errors),
                   errors=errors)

    if estimate:
        return finish_estimate(ctx, args, items, errors, wopts)

    if make_run:
        ctx.run_id, ctx.run_dir = runs.new_run(ctx.home / WORKSPACE_DIRS["runs"], args.run or (pairs[0][0].stem))
        rename_log(ctx, f"prepare-{ctx.run_id}")
        print(f"run_dir: {as_posix(ctx.run_dir)}", file=sys.stderr, flush=True)
        ctx.status = Status(ctx, items)
        ctx.status.start()
    ctx.stderr_on = True
    ctx.log(f"{len(items)} video(s); log at {as_posix(ctx.log_path)}")
    for it in items:   # one at a time: DeepFace already uses every core
        if it["webcam"]["state"] == "pending":
            make_webcam_timeline(ctx, it, deepface, overrides, params, segment, force)

    jobs = []
    for it in items:
        warm = all(it["hits"].values())
        cost = 0.0 if warm or len(items) < 2 else round(estimate_seconds(it["info"], it["hits"], it["params"]["frames"]), 1)
        jobs.append({"vid": it["vid"], "slug": it["slug"], "title": it["title"], "video_path": str(it["video"]),
                     "vtt_path": str(it["vtt"]), "video_key": it["video_key"],
                     "video_dir": str(ctx.home / WORKSPACE_DIRS["cache"] / it["video_key"]),
                     "keys": it["keys"], "params": it["params"], "info": it["info"], "info_from_cache": it["info_from_cache"],
                     "force": sorted(force), "meta": it["meta"], "warm": warm, "cost": cost,
                     "ffmpeg": ffmpeg_version, "log_path": str(ctx.log_path), "quiet": ctx.quiet,
                     "progress_path": ctx.status.progress_path(it["vid"]) if ctx.status else None})
    jobs_n = args.jobs if args.jobs and args.jobs > 0 else 1
    results = run_jobs(ctx, jobs, jobs_n) if jobs else []

    done = []
    for it, res in zip(items, results):
        if res.get("ok"):
            done.append((it, res))
            continue
        err = res.get("error") or {"code": "video_failed", "detail": "failed without detail"}
        if err.get("code") == "incomplete_install":
            raise Fail("incomplete_install", err["detail"])
        errors.append({"vid": it["vid"], "slug": it["slug"], "video": as_posix(it["video"]), "code": "video_failed",
                       "detail": err["detail"]})
    if not done:
        raise Fail("video_failed", "no video was prepared: " + "; ".join(f"{e['slug']}: {e['detail']}" for e in errors),
                   errors=errors)

    videos_out, states, wvideos = [], [], []
    for it, res in done:
        b = res.get("batches") or {}
        images = list(b.get("batch_images") or [])
        n_frames = int(b.get("n_frames") or sum(images))
        remaining = min(len(images), wopts["max_batches"]) if wopts["max_batches"] else len(images)
        state = {"start_batch": 1, "ledger_exists": False, "start_prompt_exists": False, "final_exists": False,
                 "remaining": remaining}
        states.append(state)
        videos_out.append({**video_entry(it["vid"], it["slug"], it["title"], res, it["warn"]),
                           "webcam_timeline": webcam_view(it["webcam"])})
        wvideos.append({"slug": it["slug"], "title": it["title"], "n_frames": n_frames, "batch_images": images,
                        "duration_s": span(res.get("summary") or {}, it["duration"], segment),
                        "batches_dir": res["dirs"]["batches"],
                        # v1, v2...: the video folder does not repeat the run's slug (the analyst used to join the two names)
                        "out_dir": str(ctx.run_dir / it["vid"]) if ctx.run_dir else None, **state, "_res": res, "_it": it})
    n_batches = sum(s["remaining"] for s in states)
    agents = runs.estimate_agents(states, wopts["curation"])
    estimate = {"batches": n_batches, "agents": agents, "seconds": round(ctx.elapsed())}

    if not make_run:  # cache only (manual use): no run, no workflow_args
        ctx.log(f"done in {ctx.elapsed():.1f} s (no run: use --run/--objective to create one)")
        env = envelope(ctx, not errors, error=_partial_error(errors, len(items)) if errors else None,
                       videos=videos_out, estimate=estimate, errors=errors)
        return emit(ctx, env, 0 if not errors else EXIT["video_failed"])

    requested = opts.get("roles") or WORKFLOW_DEFAULTS["roles_mode"]
    roles_mode, env_info = roles_and_env(ctx, requested)
    guard(ctx, agents, args.ignore_agent_limit, videos=videos_out, estimate=estimate, env=env_info, errors=errors)
    for v in wvideos:
        runs.scaffold_video(v["out_dir"])
    wargs = runs.build_workflow_args(run_id=ctx.run_id, run_dir=ctx.run_dir, roles_dir=ctx.home / WORKSPACE_DIRS["roles"],
                                     roles_mode=roles_mode, language=language, options=wopts, combined_exists=False,
                                     videos=wvideos)
    check_args(wargs)
    vlines = []
    for v in wvideos:
        it = v["_it"]
        line = (f"{v['slug']}: {it['title']} · {fmt_hms(v['duration_s'], 0)} · {len(v['batch_images'])} lotes · "
                f"vídeo {as_posix(it['video'])} · legenda {as_posix(it['vtt'])}")
        if segment:
            line += f" · trecho {fmt_hms(segment[0], 0)}-{fmt_hms(min(segment[1], it['duration']), 0)}"
        vlines.append(line)
    made = [(v["_it"]["webcam"], v["_it"]["video"]) for v in wvideos]
    made = [f"ignorar_linha_do_tempo: {w['path']} ({len(w['spans'])} trecho(s), vídeo {Path(video).name}, DeepFace)"
            for w, video in made if w["state"] in ("made", "cached") and w["spans"]]
    write_text(ctx.run_dir / "objective.md",
               runs.objective_markdown(objective, option_lines(opts, wopts, roles_mode, language) + made, vlines))
    run_doc = {
        "schema": "va.run/1", "version": va.VERSION, "run_id": ctx.run_id, "run_dir": as_posix(ctx.run_dir),
        "created": cache.iso_now(), "argv": sys.argv[1:], "home": as_posix(ctx.home),
        "request": as_posix(ctx.run_dir / runs.REQUEST_MD) if ctx.request else None,
        "objective_md": as_posix(ctx.run_dir / "objective.md"), "objective": objective, "language": language,
        "options": {**wopts, "roles_mode_requested": requested, "roles_mode": roles_mode, "range": segment,
                    "webcam_timeline": wmode},
        "overrides": overrides, "params": params,
        "versions": {"algo": va.ALGO_VERSION, "vtt": va.VTT_VERSION, "render": va.RENDER_VERSION},
        "videos": [{"vid": v["_it"]["vid"], "slug": v["slug"], "title": v["title"],
                    "video_path": as_posix(v["_it"]["video"]), "vtt_path": as_posix(v["_it"]["vtt"]),
                    "video_key": v["_it"]["video_key"], "out_dir": as_posix(v["out_dir"]),
                    "keys": v["_it"]["keys"], "params": v["_it"]["params"], "meta": v["_it"]["meta"],
                    "stages": v["_res"]["dirs"], "duration_s": v["_it"]["duration"], "probe": v["_res"].get("info"),
                    "n_frames": v["n_frames"], "n_batches": len(v["batch_images"]), "batch_images": v["batch_images"],
                    "webcam_timeline": webcam_view(v["_it"]["webcam"])}
                   for v in wvideos],
        "workflow_args": wargs, "resumes": [],
    }
    write_json(ctx.run_dir / "run.json", cache.plain(run_doc))
    ok = not errors
    ctx.partial_run = not ok
    if ok and ctx.consume:
        runs.consume_request(ctx.request, ctx.run_dir, ok=True)
        ctx.consume = False
    ctx.log(f"done in {ctx.elapsed():.1f} s: {n_batches} batch(es), ~{agents} agent(s), roles {roles_mode}")
    if ctx.status:
        ctx.status.finish("ready" if ok else "partial")
        ctx.status = None
    env = envelope(ctx, ok, error=_partial_error(errors, len(items)) if errors else None, videos=videos_out,
                   estimate=estimate, env=env_info, workflow_args=wargs, errors=errors)
    if not ok:
        raise _Partial(env)
    return emit(ctx, env, 0)


class _Partial(Exception):
    """Exit 4 with a usable run: the envelope is ready; the request still counts as failed."""

    def __init__(self, env: dict):
        super().__init__("partial")
        self.env = env


def _partial_error(errors: list[dict], total: int) -> dict:
    names = "; ".join(f"{e['slug']}: {e['detail']}" for e in errors)
    return {"code": "video_failed", "detail": f"{len(errors)} of {total} video(s) failed ({names}); the others are in the JSON"}


def finish_estimate(ctx: Ctx, args, items: list[dict], errors: list[dict], wopts: dict) -> int:
    ctx.stderr_on = True
    videos_out, states, secs = [], [], []
    known = True
    web = 0.0   # the webcam timelines run one at a time, before the extraction
    for it in items:
        vdir = ctx.home / WORKSPACE_DIRS["cache"] / it["video_key"]
        hits = dict(it["hits"])  # cache.stage_hits: valid and not forced
        s = estimate_seconds(it["info"], hits, it["params"]["frames"])
        secs.append(s)
        w = it["webcam"]
        if w["state"] == "pending":
            w["estimate_s"] = round(webcam.estimate_seconds(it["info"], w["scan"]), 1)
            web += w["estimate_s"]
        n_batches = n_frames = None
        if hits["batches"]:
            res = (cache.read_stage(cache.stage_dir(vdir, "batches", it["keys"]["batches"])) or {}).get("result") or {}
            images = res.get("batch_images") or []
            n_batches, n_frames = len(images), res.get("n_frames")
            states.append({"remaining": min(n_batches, wopts["max_batches"]) if wopts["max_batches"] else n_batches})
        else:
            known = False
        videos_out.append({"vid": it["vid"], "slug": it["slug"], "title": it["title"], "video_path": as_posix(it["video"]),
                           "vtt_path": as_posix(it["vtt"]), "video_key": it["video_key"], "duration_s": round(it["duration"], 3),
                           "resolution": [cache.info_get(it["info"], "width"), cache.info_get(it["info"], "height")],
                           "fps_avg": cache.info_get(it["info"], "fps_avg", "fps"), "n_frames": n_frames,
                           "n_batches": n_batches, "cache_hit": hits, "estimate_s": round(s, 1), "warnings": it["warn"],
                           "webcam_timeline": webcam_view(w)})
    jobs = max(1, min(args.jobs or 1, len(secs) or 1))
    if jobs > 1:
        # decoding is CPU-bound: measured on this machine, 2 Teams 1080p16 videos in parallel took
        # 87 s for 65 + 60 s of single-video estimates (each ~1.3x slower), i.e. ~0.7x the sum
        total = max(max(secs) * 1.3, sum(secs) * 0.75)
    else:
        total = sum(secs)
    total += web
    agents = runs.estimate_agents(states, wopts["curation"]) if known and states else None
    estimate = {"batches": sum(s["remaining"] for s in states) if known and states else None, "agents": agents,
                "seconds": int(-(-total // 1))}
    env_info, _ = env_status(ctx)
    if agents is not None:
        guard(ctx, agents, args.ignore_agent_limit, videos=videos_out, estimate=estimate, env=env_info, errors=errors)
    ctx.log(f"estimate: ~{estimate['seconds']} s for {len(items)} video(s)"
            + (f", up to ~{round(web)} s of them for the webcam timeline (DeepFace)" if web else ""))
    if errors and not items:
        raise Fail("video_failed", "; ".join(f"{e['slug']}: {e['detail']}" for e in errors), errors=errors)
    env = envelope(ctx, not errors, error=_partial_error(errors, len(items) + len(errors)) if errors else None,
                   videos=videos_out, estimate=estimate, env=env_info, errors=errors)
    return emit(ctx, env, 0 if not errors else EXIT["video_failed"])


def run_resume(ctx: Ctx, args) -> int:
    ctx.mode = "resume"
    if args.request or args.inputs or args.estimate or args.force:
        raise Fail("invalid_usage", "--resume does not accept --request, inputs, --estimar or --force")
    ensure_home(ctx)
    try:
        run_dir = runs.find_run(ctx.home, args.resume)
    except runs.RunError as exc:
        raise Fail(exc.code, exc.detail) from None
    try:
        rj = read_json(run_dir / "run.json")
        old = rj["workflow_args"]
        vids = rj["videos"]
        assert isinstance(vids, list) and vids and len(vids) == len(old["videos"])
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
        raise Fail("inconsistent_run", f"{as_posix(run_dir / 'run.json')} incomplete or unreadable ({exc})") from None
    ctx.run_dir, ctx.run_id = run_dir, run_dir.name
    stamp = time.strftime("%Y%m%d-%H%M%S")
    open_log(ctx, f"resume-{stamp}-{ctx.run_id}")
    print(f"run_dir: {as_posix(ctx.run_dir)}", file=sys.stderr, flush=True)
    ctx.stderr_on = True
    if not (run_dir / "objective.md").is_file():
        raise Fail("inconsistent_run", f"missing {as_posix(run_dir / 'objective.md')}")
    raw = {k: getattr(args, k) for k in ("roles", "max_batches", "parallel", "analyst_effort", "curator_effort",
                                         "curation")
           if getattr(args, k) is not None}
    try:
        over = runs.normalize_options(raw)
    except runs.RunError as exc:
        raise Fail(exc.code, exc.detail) from None
    stored = rj.get("options") or {}
    wopts = {"max_batches": old["options"].get("max_batches"), "max_parallel_videos": old["options"]["max_parallel_videos"],
             "analyst_effort": old["options"]["analyst_effort"], "curator_effort": old["options"]["curator_effort"],
             "curation": old["options"].get("curation") or "batches"}  # run older than the option: it had the NEXT
    if args.max_batches is not None:
        wopts["max_batches"] = over["max_batches"] or None
    if over["parallel"]:
        wopts["max_parallel_videos"] = over["parallel"]
    if over["analyst_effort"]:
        wopts["analyst_effort"] = over["analyst_effort"]
    if over["curator_effort"]:
        wopts["curator_effort"] = over["curator_effort"]
    if over["curation"]:
        wopts["curation"] = over["curation"]
    requested = over["roles"] or stored.get("roles_mode_requested") or old.get("roles_mode") or "inline"

    # rebuild missing or invalid stages from the stored params (deterministic, same folders)
    jobs, results = [], {}
    for i, v in enumerate(vids):
        keys = v["keys"]
        vdir = ctx.home / WORKSPACE_DIRS["cache"] / v["video_key"]
        valid = all(cache.stage_valid(cache.stage_dir(vdir, s, keys[s]), s, keys[s]) for s in cache.STAGES)
        job = {"vid": v.get("vid") or f"v{i + 1}", "slug": v["slug"], "title": v["title"], "video_path": v["video_path"],
               "vtt_path": v["vtt_path"], "video_key": v["video_key"], "video_dir": str(vdir), "keys": keys,
               "params": v.get("params") or rj.get("params"), "info": None, "force": [], "meta": v.get("meta") or {},
               "expected_fp": v["video_key"].rsplit("-", 1)[-1], "log_path": str(ctx.log_path), "quiet": ctx.quiet}
        if valid:
            results[i] = cache.prepare_video(job)  # all hits: reads the summary, refreshes last_used
        else:
            ctx.log(f"{v['slug']}: incomplete cache; rebuilding with the run's parameters")
            jobs.append((i, job))
    if jobs:
        ffv = runtime_check(ctx, ("ffmpeg", "ffprobe", "numpy", "pillow"))
        ctx.status = Status(ctx, [j for _, j in jobs])
        ctx.status.start()
        for _, job in jobs:
            job["ffmpeg"] = ffv
            job["progress_path"] = ctx.status.progress_path(job["vid"])
        for (i, _), res in zip(jobs, run_jobs(ctx, [j for _, j in jobs], args.jobs or 1)):
            results[i] = res
        ctx.status.finish("ready" if all(r.get("ok") for r in results.values()) else "error")
        ctx.status = None
    errors = []
    for i, v in enumerate(vids):
        res = results.get(i) or {}
        if not res.get("ok"):
            err = res.get("error") or {}
            if err.get("code") == "incomplete_install":
                raise Fail("incomplete_install", err.get("detail", ""))
            errors.append({"vid": v.get("vid"), "slug": v["slug"], "video": v["video_path"], "code": "video_failed",
                           "detail": err.get("detail") or "cache missing and not rebuilt"})
    if errors:
        raise Fail("video_failed", "could not resume: " + "; ".join(f"{e['slug']}: {e['detail']}" for e in errors),
                   errors=errors)

    states, videos_out, wvideos, actions = [], [], [], []
    for i, (v, ov) in enumerate(zip(vids, old["videos"])):
        res = results[i]
        stage_result = res.get("batches") or {}
        images = list(stage_result.get("batch_images") or ov["batch_images"])
        n_frames = int(stage_result.get("n_frames") or ov["n_frames"])
        warn = []
        if images != list(ov["batch_images"]):
            warn.append("the rebuilt batches differ from the original run's: old reports may not match")
        out_dir = run_dir / Path(ov["out_dir"]).name  # recomputed: still valid if the central folder was moved
        st = runs.resume_video(out_dir, len(images), wopts["max_batches"], stamp)
        actions += [f"{v['slug']}: {a}" for a in st["actions"]]
        states.append(st)
        videos_out.append(video_entry(v.get("vid") or f"v{i + 1}", v["slug"], v["title"], res, warn))
        wvideos.append({"slug": ov["slug"], "title": ov["title"], "n_frames": n_frames, "batch_images": images,
                        "duration_s": _hms_seconds(ov["duration"]),
                        "batches_dir": (res.get("dirs") or {}).get("batches") or ov["batches_dir"],
                        "out_dir": out_dir, **{k: st[k] for k in ("start_batch", "ledger_exists",
                                                                  "start_prompt_exists", "final_exists")}})
    combined_exists, acts = runs.resume_combined(run_dir, states)
    actions += acts
    for a in actions:
        ctx.log(a)
    roles_mode, env_info = roles_and_env(ctx, requested)
    agents = runs.estimate_agents(states, wopts["curation"])
    n_batches = sum(s["remaining"] for s in states)
    estimate = {"batches": n_batches, "agents": agents, "seconds": round(ctx.elapsed())}
    guard(ctx, agents, args.ignore_agent_limit, videos=videos_out, estimate=estimate, env=env_info)
    wargs = runs.build_workflow_args(run_id=ctx.run_id, run_dir=run_dir, roles_dir=ctx.home / WORKSPACE_DIRS["roles"],
                                     roles_mode=roles_mode, language=old["language"], options=wopts,
                                     combined_exists=combined_exists, videos=wvideos)
    check_args(wargs)
    if all(s["final_exists"] for s in states) and (len(states) == 1 or combined_exists):
        ctx.warnings.append("the run is already complete: nothing to resume (for another question, a new run reuses the cache)")
    rj["workflow_args"] = wargs
    rj["options"] = {**stored, **wopts, "roles_mode_requested": requested, "roles_mode": roles_mode}
    rj.setdefault("resumes", []).append({"at": cache.iso_now(), "argv": sys.argv[1:], "actions": actions,
                                         "start_batches": [s["start_batch"] for s in states]})
    write_json(run_dir / "run.json", cache.plain(rj))
    ctx.log(f"resume ready: {n_batches} batch(es) to analyse, ~{agents} agent(s), roles {roles_mode}")
    env = envelope(ctx, True, videos=videos_out, estimate=estimate, env=env_info, workflow_args=wargs)
    return emit(ctx, env, 0)


def _hms_seconds(text: str) -> float:
    try:
        return parse_time(text)
    except (TypeError, ValueError):
        return 0.0


# --- main -------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    setup_console()
    ctx = Ctx()
    try:
        args = build_parser().parse_args(argv)
        ctx.ascii, ctx.quiet, ctx.debug, ctx.json_out = args.ascii, args.quiet, args.debug, args.json_out
        ctx.home = workspace_home(args.home)
        modes = [m for m, on in (("--resume", args.resume is not None), ("--gc", args.gc is not None)) if on]
        if len(modes) > 1:
            raise Fail("invalid_usage", f"use only one mode: {' or '.join(modes)}")
        if args.gc is not None:
            return run_gc(ctx, args)
        if args.resume is not None:
            return run_resume(ctx, args)
        return run_prepare(ctx, args)
    except _Partial as p:
        code = EXIT["video_failed"]
        _consume_failed(ctx)
        return emit(ctx, p.env, code)
    except runs.RunError as exc:
        return _failed(ctx, Fail(exc.code, exc.detail))
    except Fail as f:
        return _failed(ctx, f)
    except KeyboardInterrupt:
        return _failed(ctx, Fail("interrupted", "interrupted (Ctrl+C or process terminated); the completed stages stay in the cache"))
    except Exception as exc:  # a bug: still one JSON line, with the traceback in the log
        import traceback
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        ctx.log("INTERNAL ERROR\n" + tb, echo=False)
        if ctx.debug:
            print(tb, file=sys.stderr)
        return _failed(ctx, Fail("internal_error", f"{type(exc).__name__}: {exc}"))


def _consume_failed(ctx: Ctx) -> None:
    if ctx.status is not None:
        with contextlib.suppress(Exception):
            ctx.status.finish("error")
        ctx.status = None
    if ctx.consume and ctx.request is not None:
        with contextlib.suppress(OSError):
            runs.consume_request(ctx.request, ctx.run_dir, ok=False, copy_to_run=ctx.partial_run)
        ctx.consume = False


def _failed(ctx: Ctx, f: Fail) -> int:
    code = EXIT.get(f.code, 1)
    ctx.log(f"error {f.code}: {f.detail}", echo=False)
    _consume_failed(ctx)
    extra = dict(f.extra)
    env = envelope(ctx, False, error={"code": f.code, "detail": f.detail}, **extra)
    return emit(ctx, env, code)


if __name__ == "__main__":
    sys.exit(main())
