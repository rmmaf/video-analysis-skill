#!/usr/bin/env python
"""Local transcription for the video-analysis skill. It runs INSIDE the WhisperX environment
(%USERPROFILE%\\whisperx-env, or ~/whisperx-env on macOS; created by setup.md, step 6), never with the
skill's own Python.

prepare.py runs it, one video at a time, for a video without subtitles (scripts/va/transcript.py):

    <env python> -B transcribe.py VIDEO --work DIR --asr-out DIR/asr.json --model M --language auto|xx
        [--diar-out DIR/diar-<d8>.json --diar-key d8 --speakers S [--min-speakers N] [--max-speakers N]]
        [--device auto|cpu|cuda] [--batch-size N] [--threads N] [--progress FILE --vid vN] [--watch-stdin]

and the setup proves the environment with it, downloading the models on the way:

    <env python> -B transcribe.py --check --model M [--align pt ...] [--diarize]

Steps: the audio (ffmpeg, 16 kHz mono, in memory) -> the language (auto: faster-whisper's detect_language
over speech windows of the first 15 minutes, not just the first 30 s, which in a call are often silence) ->
the ASR in blocks of about 10 minutes cut at the quietest point, each one a checkpoint in DIR/partial (an
interrupted run resumes from it) -> the word alignment (wav2vec2; without a model for the language, the
segments keep their own times) -> asr.json. With --diar-out: pyannote's speaker turns, with the token that
`hf auth login` saved -> diar-<d8>.json. Without access (no token, the model's conditions not accepted) or
when pyannote fails, it writes "diarized": false and the reason, and exits 0: the subtitles come out without
speakers. An asr.json already complete (same model and language) is kept: only the diarization runs.

Exit codes: 0 ok · 2 arguments · 3 the environment does not load · 4 a model download failed (--check) ·
5 out of memory even with the smallest batch · 1 other · 130 the standard input closed (prepare.py stopped).
"""
import argparse
import gc
import json
import os
import re
import sys
import threading
import time
import warnings

SR = 16000                    # whisperx.audio.SAMPLE_RATE
SCHEMA_ASR = "va.asr/1"
SCHEMA_DIAR = "va.diar/1"
MIN_GPU_CAPABILITY = (7, 0)   # TRANSCRIBE_DEFAULTS["gpu_min_compute_cap"]: older GPUs transcribe on the CPU
CHUNK_VERSION = 1             # the header of partial/chunk_NNN.json
DEFAULTS = {"chunk_s": 600.0, "chunk_search_s": 30.0, "detect_window_s": 900.0, "detect_segments": 6,
            "batch_cuda": 16, "batch_cpu": 8, "diarize_model": "pyannote/speaker-diarization-community-1"}


def _pipe_peek():
    """Windows: a function telling whether the stdin pipe's other end is gone (True), still open (False), or
    whether stdin is no pipe at all (None: nothing to watch). PeekNamedPipe never blocks and reads nothing."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    peek = k32.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPDWORD, wintypes.LPDWORD,
                     wintypes.LPDWORD]
    peek.restype = wintypes.BOOL
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(0))

    def closed():
        avail = wintypes.DWORD(0)
        if peek(handle, None, 0, None, ctypes.byref(avail), None):
            return False
        # ERROR_BROKEN_PIPE, ERROR_NO_DATA, ERROR_PIPE_NOT_CONNECTED: the writer is gone
        return True if ctypes.get_last_error() in (109, 232, 233) else None
    return closed


def watch_stdin(interval_s: float = 1.0):
    """--watch-stdin: exit (130) when the standard input reaches its end. prepare.py keeps a pipe open on it
    while it waits; if prepare.py dies hard it does not take this process along, but the pipe closes, and the
    transcription stops instead of running on for nobody.

    On Windows the pipe is polled with PeekNamedPipe, never read: a thread blocked in os.read(0) holds the C
    runtime's lock on fd 0, and the first import that touches fd 0 (numpy's) then waits forever. Elsewhere a
    thread reads fd 0 with os.read (not sys.stdin, whose reader would hold its own lock and make the
    interpreter's shutdown fail) until the end of the pipe."""
    def stop():
        print("the standard input closed (prepare.py stopped): exiting", file=sys.stderr, flush=True)
        os._exit(130)

    if os.name == "nt":
        try:
            closed = _pipe_peek()
        except (OSError, ValueError, AttributeError):
            return                                   # no usable stdin handle: nothing to watch

        def wait():
            while True:
                state = closed()
                if state is None:
                    return                           # stdin is not a pipe (a console, NUL): nothing to watch
                if state:
                    stop()
                time.sleep(interval_s)
    else:
        def wait():
            try:
                while os.read(0, 4096):
                    pass
            except OSError:
                pass
            stop()

    threading.Thread(target=wait, name="watch-stdin", daemon=True).start()


if __name__ == "__main__" and "--watch-stdin" in sys.argv[1:] and sys.stdin is not None:
    watch_stdin()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def write_json(path, obj):
    """Atomic: a reader sees the old file or the new one, never half of one."""
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")
    for delay in (0.0, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6):   # an antivirus or the indexer may hold the target
        time.sleep(delay)
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            continue
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


class Progress:
    """The video's progress file, the one prepare.py's status.json reads (stage "transcript")."""

    def __init__(self, path, vid, total_s):
        self.path, self.vid, self.total_s = path, vid, round(float(total_s), 1)
        self.last = 0.0

    def __call__(self, phase, done_s, force=False):
        now = time.monotonic()
        if not self.path or (not force and now - self.last < 2.0):
            return
        self.last = now
        info = {"phase": phase, "done_s": round(max(0.0, min(float(done_s), self.total_s)), 1), "total_s": self.total_s}
        try:
            write_json(self.path, {"vid": self.vid, "stage": "transcript", "state": "processing", "updated": iso_now(),
                                   "info": info})
        except (OSError, ValueError):
            pass


# --- pure helpers (numpy only: tested without whisperx) -------------------------------------------------

def plan_chunks(audio, sr=SR, chunk_s=DEFAULTS["chunk_s"], search_s=DEFAULTS["chunk_search_s"], win_s=0.5):
    """[(start, end)] sample ranges of about ``chunk_s`` seconds, each cut at the quietest ``win_s`` window
    within ``search_s`` of the target boundary, so a cut rarely falls in the middle of a word. A recording up
    to 1.5 blocks long is one block. Deterministic: a resumed run gets the same blocks."""
    import numpy as np
    n = int(len(audio))
    if n <= int(chunk_s * 1.5 * sr):
        return [(0, n)]
    win = max(1, int(win_s * sr))
    bounds = [0]
    target = chunk_s
    while n - int(target * sr) > int(chunk_s * 0.5 * sr):
        lo = max(int((target - search_s) * sr), bounds[-1] + sr)
        hi = min(int((target + search_s) * sr), n - sr)
        cut = int(target * sr)
        if hi - lo >= 2 * win:
            seg = np.asarray(audio[lo:hi], dtype=np.float32)
            k = len(seg) // win
            energy = (seg[:k * win].reshape(k, win) ** 2).mean(axis=1)
            cut = lo + int(np.argmin(energy)) * win + win // 2
        bounds.append(cut)
        target = cut / sr + chunk_s
    bounds.append(n)
    return list(zip(bounds[:-1], bounds[1:]))


# the words of a refused access in an error's text: an HTTP 401/403 ("401 Client Error: Unauthorized"), not those
# digits inside a number, a size or an id ("Tried to allocate 403.00 MiB"); "gated" as a word, not inside
# "propagated"; an authentication, but not a proxy's (407 Proxy Authentication Required is the network's: a cause
# to fix, not conditions to accept). va/environment.py's _ACCESS_PROBE reads the codes the same way.
_ACCESS_DENIED = re.compile(r"(?<![\w.])40[13](?![\w.])|\bgated\b|(?<!proxy )authenticat|accept the user conditions"
                            r"|access to model|'nonetype' object has no attribute")


def _status_code(exc):
    """The HTTP status of an exception that carries its response (requests' and huggingface_hub's), or None."""
    try:
        return getattr(getattr(exc, "response", None), "status_code", None)
    except Exception:
        return None


def classify_failure(exc, token_present):
    """Why the diarization did not run: no_token, no_access (gated model whose conditions this account has
    not accepted, a token without permission, pyannote returning no pipeline) or error: <detail>, any other
    failure (the network, a proxy, out of memory), which a call that gives the speaker count again retries."""
    if not token_present:
        return "no_token"
    chain, seen = [], set()
    e = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        chain.append(e)
        e = e.__cause__ or e.__context__
    names = " ".join(type(x).__name__ for x in chain)
    text = " ".join(str(x) for x in chain).lower()
    if any(n in names for n in ("GatedRepoError", "RepositoryNotFoundError", "LocalTokenNotFoundError")):
        return "no_access"
    if any(_status_code(x) in (401, 403) for x in chain) or _ACCESS_DENIED.search(text):
        return "no_access"
    return f"error: {type(exc).__name__}: {' '.join(str(exc).split())[:300]}"


def default_threads():
    """CPU threads for CTranslate2 and torch: the physical cores (half the logical ones where SMT is the
    rule; every core on Apple silicon, which has none)."""
    n = os.cpu_count() or 2
    return max(1, min(16, n if sys.platform == "darwin" else n // 2))


def is_oom(exc):
    return "out of memory" in str(exc).lower() or type(exc).__name__ == "OutOfMemoryError"


def clean_number(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v, 3) if v == v and v not in (float("inf"), float("-inf")) else None


def clean_segments(segments, offset_s=0.0):
    """JSON-safe segments: start/end/text and the words' word/start/end/score (numpy floats, NaN -> absent)."""
    out = []
    for s in segments or []:
        seg = {"text": str(s.get("text") or "").strip()}
        for k in ("start", "end"):
            v = clean_number(s.get(k))
            if v is not None:
                seg[k] = round(v + offset_s, 3)
        words = []
        for w in s.get("words") or []:
            item = {"word": str(w.get("word") or "").strip()}
            for k in ("start", "end"):
                v = clean_number(w.get(k))
                if v is not None:
                    item[k] = round(v + offset_s, 3)
            sc = clean_number(w.get("score"))
            if sc is not None:
                item["score"] = sc
            if item["word"]:
                words.append(item)
        if words:
            seg["words"] = words
        if seg["text"] or words:
            out.append(seg)
    return out


# --- the environment --------------------------------------------------------------------------------------

def load_libs():
    """torch first (on Windows it loads the CUDA DLLs that CTranslate2 also needs), then whisperx. Exit 3
    when they do not load."""
    try:
        import torch  # noqa: F401
        import whisperx  # noqa: F401
    except Exception as exc:   # ImportError, a DLL that does not load (OSError), a broken install
        log(f"ERROR: the WhisperX environment does not load: {type(exc).__name__}: {exc}")
        sys.exit(3)
    import torch
    import whisperx
    return torch, whisperx


def pick_device(torch, wanted):
    """auto: CUDA only on a GPU from Volta on (TRANSCRIBE_DEFAULTS["gpu_min_compute_cap"]): torch still sees a
    Pascal or Maxwell card, but CTranslate2's float16 needs 7.0 and the cu128 build has no kernels for it."""
    if wanted in ("cpu", "cuda"):
        return wanted
    try:
        if not torch.cuda.is_available():
            return "cpu"
        return "cuda" if tuple(torch.cuda.get_device_capability(0)) >= MIN_GPU_CAPABILITY else "cpu"
    except Exception:
        return "cpu"


def version_of(dist):
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:
        return None


class Asr:
    """The Whisper model and its settings, reloaded with a lighter compute type when memory runs out."""

    def __init__(self, whisperx, torch, model, device, language, batch_size, threads):
        self.whisperx, self.torch = whisperx, torch
        self.name, self.device, self.language = model, device, language
        self.compute_type = "float16" if device == "cuda" else "int8"
        self.batch_size = batch_size or (DEFAULTS["batch_cuda"] if device == "cuda" else DEFAULTS["batch_cpu"])
        self.threads = threads or default_threads()
        self.model = None

    def load(self):
        self.free()
        log(f"loading {self.name} on {self.device} ({self.compute_type}, {self.threads} threads)")
        self.model = self.whisperx.load_model(self.name, self.device, compute_type=self.compute_type,
                                              language=None if self.language in (None, "auto") else self.language,
                                              threads=self.threads)
        return self

    def free(self):
        if self.model is not None:
            self.model = None
            gc.collect()
            if self.device == "cuda":
                try:
                    self.torch.cuda.empty_cache()
                except Exception:
                    pass

    def transcribe(self, audio, language, callback):
        """One block, with the batch halved (and then int8_float16 on a GPU) when memory runs out; exit 5
        when nothing fits."""
        while True:
            try:
                return self.model.transcribe(audio, batch_size=self.batch_size, language=language,
                                             progress_callback=callback)
            except TypeError:
                # a whisperx without progress_callback: the progress then moves block by block
                return self.model.transcribe(audio, batch_size=self.batch_size, language=language)
            except Exception as exc:
                if not is_oom(exc):
                    raise
                if self.batch_size > 1:
                    self.batch_size = max(1, self.batch_size // 2)
                    log(f"out of memory: batch {self.batch_size}")
                    self.free()
                    self.load()
                    continue
                if self.device == "cuda" and self.compute_type == "float16":
                    self.compute_type = "int8_float16"
                    log("out of memory: int8_float16")
                    self.load()
                    continue
                log(f"ERROR: out of memory even with batch 1: {exc}")
                sys.exit(5)


def detect_language(asr, audio, work, progress):
    """auto: faster-whisper's detect_language over up to 6 speech windows (Silero VAD) of the first 15
    minutes; whisperx's own (the first 30 s) only if that fails. Kept in partial/language.json, so a resumed
    run keeps the language of the blocks already done."""
    path = os.path.join(work, "partial", "language.json")
    saved = read_json(path)
    if isinstance(saved, dict) and saved.get("model") == asr.name and saved.get("language"):
        return saved["language"], saved.get("probability")
    progress("language", 0.0, force=True)
    window = audio[: int(DEFAULTS["detect_window_s"] * SR)]
    prob = None
    try:
        lang, prob, _ = asr.model.model.detect_language(audio=window, vad_filter=True,
                                                        language_detection_segments=DEFAULTS["detect_segments"])
    except Exception as exc:
        log(f"detection over speech windows failed ({type(exc).__name__}: {exc}); using the first 30 s")
        lang = asr.model.detect_language(window)
    prob = clean_number(prob)
    log(f"language: {lang}" + (f" ({prob:.2f})" if prob is not None else ""))
    write_json(path, {"model": asr.name, "language": lang, "probability": prob})
    return lang, prob


def run_asr(args, torch, whisperx, audio, duration, progress):
    """ASR (in checkpointed blocks) and alignment -> the asr.json document."""
    t0 = time.perf_counter()
    timing, notes = {}, []
    device = pick_device(torch, args.device)
    asr = Asr(whisperx, torch, args.model, device, args.language, args.batch_size, args.threads).load()
    if device == "cpu":
        try:
            torch.set_num_threads(asr.threads)
        except Exception:
            pass
    timing["load"] = round(time.perf_counter() - t0, 1)
    os.makedirs(os.path.join(args.work, "partial"), exist_ok=True)
    if args.language == "auto":
        lang, prob = detect_language(asr, audio, args.work, progress)
    else:
        lang, prob = args.language, None
    t1 = time.perf_counter()
    chunks = plan_chunks(audio)
    segments = []
    for i, (s, e) in enumerate(chunks):
        part = os.path.join(args.work, "partial", f"chunk_{i:03d}.json")
        header = {"version": CHUNK_VERSION, "model": args.model, "language": lang, "start": s, "end": e}
        saved = read_json(part)
        if isinstance(saved, dict) and saved.get("header") == header and isinstance(saved.get("segments"), list):
            segments += saved["segments"]
            log(f"block {i + 1}/{len(chunks)}: from the checkpoint")
            continue
        base = s / SR
        log(f"block {i + 1}/{len(chunks)}: {base / 60:.1f}-{e / SR / 60:.1f} min")

        def cb(*a, **k):
            pct = next((float(x) for x in list(a) + list(k.values()) if isinstance(x, (int, float))), None)
            if pct is not None:
                progress("asr", base + (e - s) / SR * min(100.0, max(0.0, pct)) / 100.0)

        progress("asr", base, force=True)
        res = asr.transcribe(audio[s:e], lang, cb)
        segs = clean_segments(res.get("segments"), offset_s=base)
        write_json(part, {"header": header, "segments": segs})
        segments += segs
        progress("asr", e / SR, force=True)
    timing["asr"] = round(time.perf_counter() - t1, 1)
    batch, compute = asr.batch_size, asr.compute_type
    asr.free()
    t2 = time.perf_counter()
    aligned = False
    if segments:
        progress("align", 0.0, force=True)
        try:
            model_a, meta = whisperx.load_align_model(language_code=lang, device=device)

            def acb(*a, **k):
                pct = next((float(x) for x in list(a) + list(k.values()) if isinstance(x, (int, float))), None)
                if pct is not None:
                    progress("align", duration * min(100.0, max(0.0, pct)) / 100.0)
            try:
                result = whisperx.align(segments, model_a, meta, audio, device, return_char_alignments=False,
                                        progress_callback=acb)
            except TypeError:
                result = whisperx.align(segments, model_a, meta, audio, device, return_char_alignments=False)
            segments = clean_segments(result.get("segments"))
            aligned = True
            del model_a
            gc.collect()
        except Exception as exc:
            msg = " ".join(str(exc).split())[:300]
            log(f"alignment skipped: {type(exc).__name__}: {msg}")
            notes.append(f"word alignment skipped ({type(exc).__name__}: {msg}): the cues keep the segments' times")
    timing["align"] = round(time.perf_counter() - t2, 1)
    timing["total"] = round(time.perf_counter() - t0, 1)
    return {"schema": SCHEMA_ASR, "complete": True, "whisperx": version_of("whisperx"), "model": args.model,
            "device": device, "compute_type": compute, "batch_size": batch, "threads": asr.threads,
            "language": lang, "language_probability": prob, "language_requested": args.language,
            "aligned": aligned, "duration_s": round(duration, 3), "has_speech": bool(segments), "chunks": len(chunks),
            "timing_s": timing, "warnings": notes, "segments": segments, "created": iso_now()}


def token_present():
    try:
        from huggingface_hub import get_token
        return get_token() is not None
    except Exception:
        return False


def run_diarization(args, torch, audio, device, progress):
    """pyannote's turns -> the diar-<d8>.json document; never raises (diarized: false and the reason)."""
    t0 = time.perf_counter()
    doc = {"schema": SCHEMA_DIAR, "complete": True, "key": args.diar_key, "speakers": args.speakers,
           "mode": "pyannote", "model": args.diarize_model, "min_speakers": args.min_speakers,
           "max_speakers": args.max_speakers, "diarized": False, "reason": None, "turns": [], "created": iso_now()}
    has_token = token_present()
    if not has_token:
        doc["reason"] = "no_token"
        log("diarization skipped: no Hugging Face token (hf auth login)")
        return doc
    progress("diarize", 0.0, force=True)
    try:
        from whisperx.diarize import DiarizationPipeline
        pipe = DiarizationPipeline(model_name=args.diarize_model, token=None, device=device)
        if getattr(pipe, "model", None) is None:
            raise RuntimeError("pyannote returned no pipeline: 'NoneType' object has no attribute 'to'")
    except Exception as exc:
        doc["reason"] = classify_failure(exc, has_token)
        log(f"diarization skipped ({doc['reason']}): {type(exc).__name__}: {exc}")
        return doc
    kwargs = {}
    if args.min_speakers is not None and args.min_speakers == args.max_speakers:
        kwargs["num_speakers"] = args.min_speakers
    else:
        if args.min_speakers is not None:
            kwargs["min_speakers"] = args.min_speakers
        if args.max_speakers is not None:
            kwargs["max_speakers"] = args.max_speakers
    try:
        df = pipe(audio, **kwargs)
        if isinstance(df, tuple):
            df = df[0]
        turns = []
        for row in df[["start", "end", "speaker"]].itertuples(index=False):
            s, e = clean_number(row[0]), clean_number(row[1])
            if s is not None and e is not None and e >= s:
                turns.append([s, e, str(row[2])])
        turns.sort(key=lambda t: (t[0], t[1]))
        doc.update(diarized=True, turns=turns, speakers_found=len({t[2] for t in turns}))
        log(f"diarization: {len(turns)} turns, {doc['speakers_found']} speakers")
    except Exception as exc:
        doc["reason"] = classify_failure(exc, has_token)
        log(f"diarization failed ({doc['reason']})")
    doc["timing_s"] = round(time.perf_counter() - t0, 1)
    return doc


def transcribe(args):
    t0 = time.perf_counter()
    torch, whisperx = load_libs()
    log(f"whisperx {version_of('whisperx')} · torch {torch.__version__} · {args.video}")
    audio = whisperx.load_audio(args.video)
    duration = len(audio) / SR
    progress = Progress(args.progress, args.vid, duration)
    log(f"audio: {duration / 60:.1f} min")
    saved = read_json(args.asr_out)
    keep = (isinstance(saved, dict) and saved.get("schema") == SCHEMA_ASR and saved.get("complete") is True
            and saved.get("model") == args.model and saved.get("language_requested") == args.language)
    if keep:
        log("asr.json already complete: only the diarization runs")
        device = pick_device(torch, args.device)    # this run's device; asr.json's "device" is only a record
        if saved.get("device") and saved.get("device") != device:
            log(f"ASR made on {saved.get('device')}; diarization on {device}")
    else:
        doc = run_asr(args, torch, whisperx, audio, duration, progress)
        write_json(args.asr_out, doc)
        device = doc["device"]
        log(f"asr.json: {len(doc['segments'])} segments, {doc['language']}, {doc['timing_s']}")
    if args.diar_out:
        doc = run_diarization(args, torch, audio, device, progress)
        write_json(args.diar_out, doc)
    progress("done", duration, force=True)
    log(f"done in {time.perf_counter() - t0:.0f} s")
    return 0


def check(args):
    """The setup's proof: the libraries load, and the Whisper model, the alignment models and (with
    --diarize and a token) pyannote load too, which downloads them. The last line of stdout is a JSON."""
    torch, whisperx = load_libs()
    device = pick_device(torch, args.device)
    out = {"ok": False, "python": sys.version.split()[0], "whisperx": version_of("whisperx"),
           "torch": getattr(torch, "__version__", None), "ctranslate2": version_of("ctranslate2"),
           "pyannote_audio": version_of("pyannote.audio"), "device": device, "gpu": None, "vram_mb": None,
           "model": args.model, "model_ok": False, "align": {}, "diarization": None, "error": None}
    if device == "cuda":
        try:
            props = torch.cuda.get_device_properties(0)
            out["gpu"], out["vram_mb"] = props.name, int(props.total_memory / 2**20)
        except Exception:
            pass
    code = 0
    try:
        asr = Asr(whisperx, torch, args.model, device, None, None, args.threads).load()
        asr.free()
        out["model_ok"] = True
    except Exception as exc:
        out["error"] = f"{args.model}: {type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
        code = 4
    for lang in args.align or []:
        try:
            model_a, _meta = whisperx.load_align_model(language_code=lang, device=device)
            del model_a
            gc.collect()
            out["align"][lang] = "ok"
        except Exception as exc:
            out["align"][lang] = f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}"
            code = code or 4
    if args.diarize:
        has_token = token_present()
        if not has_token:
            out["diarization"] = "no_token"
        else:
            try:
                from whisperx.diarize import DiarizationPipeline
                pipe = DiarizationPipeline(model_name=args.diarize_model, token=None, device=device)
                if getattr(pipe, "model", None) is None:
                    raise RuntimeError("pyannote returned no pipeline: 'NoneType' object has no attribute 'to'")
                out["diarization"] = "ok"
            except Exception as exc:
                out["diarization"] = classify_failure(exc, has_token)
    out["ok"] = code == 0
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return code


def build_parser():
    ap = argparse.ArgumentParser(prog="transcribe.py", description=__doc__.splitlines()[0])
    ap.add_argument("video", nargs="?")
    ap.add_argument("--check", action="store_true", help="prove the environment and download the models")
    ap.add_argument("--work", help="the stage folder (checkpoints in partial/)")
    ap.add_argument("--asr-out")
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--language", default="auto")
    ap.add_argument("--diar-out")
    ap.add_argument("--diar-key")
    ap.add_argument("--speakers", default="auto")
    ap.add_argument("--min-speakers", type=int)
    ap.add_argument("--max-speakers", type=int)
    ap.add_argument("--diarize-model", default=DEFAULTS["diarize_model"])
    ap.add_argument("--diarize", action="store_true", help="--check: also load pyannote")
    ap.add_argument("--align", action="append", help="--check: an alignment model to load (language code)")
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    ap.add_argument("--batch-size", type=int)
    ap.add_argument("--threads", type=int)
    ap.add_argument("--progress")
    ap.add_argument("--vid", default="v1")
    ap.add_argument("--watch-stdin", action="store_true")   # watch_stdin() already started, at the top
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    warnings.filterwarnings("ignore")    # torchcodec (unused: the audio goes in memory), torch and pyannote notices
    # transformers, loading an alignment model published only as pytorch_model.bin (the Portuguese one), starts a
    # background thread that downloads the Hub bot's safetensors conversion: 1.2 GB that is never loaded
    os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "true")
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.check:
        return check(args)
    if not (args.video and args.work and args.asr_out):
        ap.error("VIDEO, --work and --asr-out are required (or --check)")
    if args.diar_out and not args.diar_key:
        ap.error("--diar-out needs --diar-key")
    if not os.path.isfile(args.video):
        print(f"video not found: {args.video}", file=sys.stderr)
        return 2
    try:
        return transcribe(args)
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        traceback.print_exc()
        if is_oom(exc):
            return 5
        log(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
