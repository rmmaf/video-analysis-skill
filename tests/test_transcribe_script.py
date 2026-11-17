"""scripts/transcribe.py without WhisperX: the blocks of the ASR (cut where it is quiet, the same on a
resume), why a diarization did not run, the JSON-safe segments, the arguments, and the exit code 3 that tells
prepare.py the environment does not load (run here with this interpreter, which has no whisperx)."""
import json
import os
import subprocess
import sys

import numpy as np
import pytest

import transcribe as TX

SR = TX.SR


def speechy(seconds: float, quiet_at=(), rng=np.random.default_rng(7)) -> np.ndarray:
    """Noise standing for speech, with half-second silences at ``quiet_at`` (seconds)."""
    audio = (rng.standard_normal(int(seconds * SR)) * 0.3).astype(np.float32)
    for t in quiet_at:
        audio[int(t * SR):int((t + 0.5) * SR)] = 0.0
    return audio


def test_short_audio_is_one_block():
    audio = speechy(14 * 60)
    assert TX.plan_chunks(audio) == [(0, len(audio))]                        # up to 1.5 blocks: one block


def test_blocks_are_cut_in_the_quiet_and_cover_everything():
    audio = speechy(35 * 60, quiet_at=(612.3, 1188.8, 1830.0))
    chunks = TX.plan_chunks(audio)
    assert chunks[0][0] == 0 and chunks[-1][1] == len(audio)
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:]))           # contiguous, no gap, no overlap
    cuts = [c[1] / SR for c in chunks[:-1]]
    # 10-min blocks, each cut at the silence within 30 s of its target; the 1830 s silence is outside the third
    # search window (1789 ± 30 s), and the last block (about 5 min) is long enough to stand alone
    assert len(chunks) == 4 and abs(cuts[0] - 612.55) < 0.5 and abs(cuts[1] - 1189.05) < 0.5
    assert abs(cuts[2] - (cuts[1] + 600)) <= 30.5
    assert TX.plan_chunks(audio) == chunks                                   # the same blocks on a resume


def test_blocks_without_a_quiet_point_cut_near_the_target():
    audio = speechy(26 * 60)
    chunks = TX.plan_chunks(audio)
    cuts = [c[1] / SR for c in chunks[:-1]]
    assert len(chunks) == 3 and abs(cuts[0] - 600) <= 30.5 and abs(cuts[1] - (cuts[0] + 600)) <= 30.5
    assert (len(audio) - chunks[-1][0]) / SR > 300                         # no stub at the end


class GatedRepoError(Exception):
    pass


class HTTPError(Exception):
    """Like requests' and huggingface_hub's: the response, with its status code, rides on the exception."""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.response = type("Response", (), {"status_code": status_code})() if status_code else None


@pytest.mark.parametrize("exc, token, reason", [
    (RuntimeError("x"), False, "no_token"),
    (GatedRepoError("Access to model is restricted"), True, "no_access"),
    (OSError("401 Client Error: Unauthorized for url"), True, "no_access"),
    (OSError("403 Client Error: Forbidden for url"), True, "no_access"),
    (HTTPError("Client Error. (Request ID: Root=1-6a1b)", 403), True, "no_access"),
    (AttributeError("'NoneType' object has no attribute 'to'"), True, "no_access"),
    (RuntimeError("CUDA error: device-side assert"), True, "error: RuntimeError: CUDA error: device-side assert"),
    # the digits of a size or an id, "gated" inside a word, a proxy's authentication: failures to fix and retry
    (RuntimeError("CUDA out of memory. Tried to allocate 403.00 MiB"), True,
     "error: RuntimeError: CUDA out of memory. Tried to allocate 403.00 MiB"),
    (OSError("Max retries exceeded (Request ID: Root=1-4013ab)"), True,
     "error: OSError: Max retries exceeded (Request ID: Root=1-4013ab)"),
    (OSError("Tunnel connection failed: 407 Proxy Authentication Required"), True,
     "error: OSError: Tunnel connection failed: 407 Proxy Authentication Required"),
    (RuntimeError("the failure propagated from a worker"), True, "error: RuntimeError: the failure propagated from a worker"),
    (HTTPError("Server Error", 503), True, "error: HTTPError: Server Error"),
])
def test_classify_failure(exc, token, reason):
    assert TX.classify_failure(exc, token) == reason


def test_classify_failure_follows_the_cause():
    try:
        try:
            raise GatedRepoError("gated")
        except GatedRepoError as inner:
            raise RuntimeError("could not load the pipeline") from inner
    except RuntimeError as outer:
        assert TX.classify_failure(outer, True) == "no_access"


def test_clean_segments():
    segs = [{"start": np.float32(1.25), "end": np.float64("nan"), "text": " Olá ",
             "words": [{"word": "Olá", "start": np.float32(1.25), "end": 1.5, "score": np.float32(0.9)},
                       {"word": "2024", "start": None}, {"word": " "}], "chars": [1]},
            {"start": 3, "end": 4, "text": "  "}]
    out = TX.clean_segments(segs, offset_s=600.0)
    assert out == [{"text": "Olá", "start": 601.25, "words": [{"word": "Olá", "start": 601.25, "end": 601.5, "score": 0.9},
                                                              {"word": "2024"}]}]
    json.dumps(out, allow_nan=False)


def test_threads_and_oom():
    assert 1 <= TX.default_threads() <= 16
    assert TX.is_oom(RuntimeError("CUDA failed with error out of memory")) and not TX.is_oom(RuntimeError("other"))


def test_arguments(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        TX.main([str(tmp_path / "v.mp4")])                                  # no --work / --asr-out
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        TX.main(["v.mp4", "--work", "w", "--asr-out", "a.json", "--diar-out", "d.json"])   # no --diar-key
    assert exc.value.code == 2
    assert TX.main([str(tmp_path / "nao existe.mp4"), "--work", str(tmp_path), "--asr-out", str(tmp_path / "a.json")]) == 2
    assert "video not found" in capsys.readouterr().err


def test_no_safetensors_conversion_download(tmp_path, monkeypatch):
    """transformers would download the Hub bot's safetensors copy of the Portuguese aligner (1.2 GB, never
    loaded) in a background thread: the driver turns that off before anything imports transformers."""
    monkeypatch.delenv("DISABLE_SAFETENSORS_CONVERSION", raising=False)
    assert TX.main([str(tmp_path / "x.mp4"), "--work", str(tmp_path), "--asr-out", str(tmp_path / "a.json")]) == 2
    assert os.environ.get("DISABLE_SAFETENSORS_CONVERSION") == "true"


@pytest.mark.parametrize("wanted, capability, expected", [("auto", (6, 1), "cpu"), ("auto", (5, 2), "cpu"),
                                                         ("auto", (7, 0), "cuda"), ("auto", (8, 6), "cuda"),
                                                         ("cuda", (6, 1), "cuda")])
def test_pick_device_leaves_gpus_older_than_volta_to_the_cpu(wanted, capability, expected):
    """auto on a Pascal or Maxwell card that torch still sees: the CPU (CTranslate2's float16 needs 7.0, and
    cu128 has no kernels for it); --device cuda is obeyed."""
    from types import SimpleNamespace
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True,
                                                 get_device_capability=lambda index=0: capability))
    assert TX.pick_device(torch, wanted) == expected
    from va import TRANSCRIBE_DEFAULTS                                  # the two thresholds stay in step
    assert TX.MIN_GPU_CAPABILITY == divmod(round(TRANSCRIBE_DEFAULTS["gpu_min_compute_cap"] * 10), 10)


@pytest.mark.parametrize("wanted, cuda_ok, expected", [("auto", False, "cpu"), ("cpu", True, "cpu"), ("auto", True, "cuda")])
def test_reused_asr_diarizes_on_the_device_of_this_run(tmp_path, monkeypatch, wanted, cuda_ok, expected):
    """A complete asr.json made on CUDA, reused to redo only the speakers: the diarization runs on the device
    this run picks (CUDA gone, or --device cpu), not on the one the ASR ran on; asr.json keeps its "device"."""
    from types import SimpleNamespace
    torch = SimpleNamespace(__version__="fake", cuda=SimpleNamespace(is_available=lambda: cuda_ok,
                                                                      get_device_capability=lambda index=0: (8, 6)))
    whisperx = SimpleNamespace(load_audio=lambda path: np.zeros(SR, dtype=np.float32))
    monkeypatch.setattr(TX, "load_libs", lambda: (torch, whisperx))
    seen = []

    def fake_diarization(args, torch, audio, device, progress):
        seen.append(device)
        return {"schema": "va.diar/1", "complete": True, "diarized": False, "reason": "no_token", "turns": []}
    monkeypatch.setattr(TX, "run_diarization", fake_diarization)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    asr = tmp_path / "asr.json"
    saved = {"schema": TX.SCHEMA_ASR, "complete": True, "model": "large-v3", "language_requested": "auto",
             "language": "pt", "device": "cuda", "segments": []}
    asr.write_text(json.dumps(saved), encoding="utf-8")
    rc = TX.main([str(video), "--work", str(tmp_path), "--asr-out", str(asr), "--model", "large-v3",
                  "--diar-out", str(tmp_path / "diar.json"), "--diar-key", "d", "--min-speakers", "2",
                  "--max-speakers", "2", "--device", wanted])
    assert rc == 0 and seen == [expected]
    assert json.loads(asr.read_text(encoding="utf-8")) == saved         # the ASR's device stays as a record


@pytest.mark.parametrize("extra, threads", [([], 6), (["--threads", "3"], 3)])
def test_reused_asr_on_a_mac_follows_the_asr_device_env_and_sets_the_threads(tmp_path, monkeypatch, capsys,
                                                                             extra, threads):
    """asr.json made on Metal (VA_ASR_DEVICE=mps), reused: no "ASR made on mps" note, and a diarization that
    may run on the CPU gets the performance cores, not torch's default."""
    torch = fake_torch(mps=True)
    set_threads = []
    torch.set_num_threads = set_threads.append
    monkeypatch.setattr(TX.sys, "platform", "darwin")
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "x")       # restored after main() sets it
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK")
    monkeypatch.setenv(TX.ASR_DEVICE_ENV, "mps")
    monkeypatch.setattr(TX, "default_threads", lambda: 6)
    monkeypatch.setattr(TX, "load_libs", lambda: (torch, fake_whisperx()))
    seen = []

    def fake_diarization(args, torch, audio, device, progress):
        seen.append(device)
        return {"schema": "va.diar/1", "complete": True, "diarized": False, "reason": "no_token", "turns": []}
    monkeypatch.setattr(TX, "run_diarization", fake_diarization)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    asr = tmp_path / "asr.json"
    asr.write_text(json.dumps({"schema": TX.SCHEMA_ASR, "complete": True, "model": "large-v3",
                               "language_requested": "auto", "language": "pt", "device": "mps", "segments": []}),
                   encoding="utf-8")
    rc = TX.main([str(video), "--work", str(tmp_path), "--asr-out", str(asr), "--model", "large-v3",
                  "--diar-out", str(tmp_path / "diar.json"), "--diar-key", "d", *extra])
    out = capsys.readouterr().out
    assert rc == 0 and seen == ["mps"] and set_threads == [threads] and "ASR made on" not in out


@pytest.mark.skipif(os.name != "nt", reason="the deadlock is in Windows' C runtime")
def test_watch_stdin_lets_imports_run_and_exits_when_the_pipe_closes(skill_dir):
    """--watch-stdin with prepare.py's pipe open (webcam.run_process): numpy imports normally, and closing the
    pipe ends the process with 130. On Windows, a thread blocked in os.read(0) holds the C runtime's lock on
    fd 0, and the first import that touches it (numpy's) waited forever: the first real transcription hung at
    the start, with 0 s of CPU."""
    import queue
    import threading as th
    code = ("import sys; sys.path.insert(0, %r); import transcribe; transcribe.watch_stdin(); "
            "import numpy; print('imported', flush=True); import time; time.sleep(60)") % str(skill_dir / "scripts")
    p = subprocess.Popen([sys.executable, "-B", "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, creationflags=0x08000000)
    lines: queue.Queue = queue.Queue()
    th.Thread(target=lambda: [lines.put(raw) for raw in iter(p.stdout.readline, b"")], daemon=True).start()
    try:
        first = lines.get(timeout=60).decode("utf-8", errors="replace").strip()
        assert first == "imported"                                   # numpy imported with the pipe open
        p.stdin.close()                                              # prepare.py gone
        assert p.wait(timeout=20) == 130
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_exit_3_when_whisperx_does_not_load(skill_dir):
    """This interpreter has no whisperx: the proof and a transcription both exit with 3, the code prepare.py
    turns into "run /video-analysis setup (step 6 repairs it)"."""
    try:
        import whisperx  # noqa: F401
        pytest.skip("whisperx is installed in this interpreter")
    except ImportError:
        pass
    r = subprocess.run([sys.executable, "-B", str(skill_dir / "scripts" / "transcribe.py"), "--check", "--model", "tiny"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 3 and "the WhisperX environment does not load" in r.stdout


# --- Apple silicon: the torch phases on MPS, the CPU's performance cores, the opt-in Metal ASR -----------------

def fake_torch(mps=False, cuda=False, capability=(8, 6)):
    """torch with CUDA and MPS as asked; it counts the cache frees of each device."""
    from types import SimpleNamespace
    freed = []
    return SimpleNamespace(
        __version__="fake", freed=freed, set_num_threads=lambda n: None,
        cuda=SimpleNamespace(is_available=lambda: cuda, get_device_capability=lambda index=0: capability,
                             empty_cache=lambda: freed.append("cuda")),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
        mps=SimpleNamespace(empty_cache=lambda: freed.append("mps")))


@pytest.mark.parametrize("wanted, mps, asr_device, expected", [
    ("auto", True, "cpu", "mps"), ("auto", False, "cpu", "cpu"), ("auto", True, "cuda", "cuda"),
    ("cpu", True, "cpu", "cpu"), ("cpu", False, "cpu", "cpu"),
    ("mps", True, "cpu", "mps"), ("mps", False, "cpu", "cpu"),
    ("cuda", True, "cuda", "cuda"), ("cuda", False, "cuda", "cuda"),
    ("auto", True, "mps", "mps"),                                   # VA_ASR_DEVICE=mps: the torch phases follow
])
def test_pick_torch_device(wanted, mps, asr_device, expected):
    assert TX.pick_torch_device(fake_torch(mps=mps), wanted, asr_device) == expected


def test_torch_without_an_mps_backend_and_the_asr_device_of_mps():
    from types import SimpleNamespace
    old = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))          # a build without backends.mps
    assert TX.pick_torch_device(old, "auto", "cpu") == "cpu" and TX.mps_available(old) is False

    def boom():
        raise RuntimeError("no Metal device")
    broken = SimpleNamespace(backends=SimpleNamespace(mps=SimpleNamespace(is_available=boom)))
    assert TX.pick_torch_device(broken, "mps", "cpu") == "cpu"
    assert TX.pick_device(fake_torch(mps=True), "mps") == "cpu"     # --device mps: the ASR stays on the CPU


@pytest.mark.parametrize("stdout, cpu_count, expected", [("12\n", 16, 12), ("10", 14, 10), ("0\n", 14, 14),
                                                         (None, 14, 14), (None, 20, 16)])
def test_default_threads_on_apple_silicon_are_the_performance_cores(monkeypatch, stdout, cpu_count, expected):
    """M4 Max: 12 performance cores of 16; sysctl failing (or saying nothing usable): os.cpu_count(), capped."""
    from types import SimpleNamespace
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw.get("timeout")))
        if stdout is None:
            raise FileNotFoundError("sysctl")
        return SimpleNamespace(stdout=stdout, returncode=0)
    monkeypatch.setattr(TX.sys, "platform", "darwin")
    monkeypatch.setattr(TX.subprocess, "run", run)
    monkeypatch.setattr(TX.os, "cpu_count", lambda: cpu_count)
    assert TX.default_threads() == expected
    assert calls[0][0] == ["sysctl", "-n", "hw.perflevel0.physicalcpu"] and calls[0][1]


def test_default_threads_elsewhere_are_half_the_logical_cores(monkeypatch):
    monkeypatch.setattr(TX.sys, "platform", "win32")
    monkeypatch.setattr(TX.os, "cpu_count", lambda: 24)
    monkeypatch.setattr(TX.subprocess, "run", lambda *a, **k: pytest.fail("sysctl outside macOS"))
    assert TX.default_threads() == 12


@pytest.mark.parametrize("platform, expected", [("darwin", "1"), ("win32", None)])
def test_mps_fallback_is_set_before_torch_on_macos(tmp_path, monkeypatch, platform, expected):
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "x")       # so monkeypatch restores the session's state...
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK")            # ...after main() sets it
    monkeypatch.setattr(TX.sys, "platform", platform)
    seen = []

    def load_libs():                                             # where torch would be imported
        seen.append(os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK"))
        raise SystemExit(3)
    monkeypatch.setattr(TX, "load_libs", load_libs)
    with pytest.raises(SystemExit):
        TX.main(["--check", "--model", "tiny"])
    assert seen == [expected]


def test_device_mps_is_an_accepted_choice():
    assert TX.build_parser().parse_args(["v.mp4", "--device", "mps"]).device == "mps"


class FakeModel:
    """whisperx's pipeline: one segment per block; out of memory while ``oom`` says so."""

    def __init__(self, device, oom, fail=None):
        self.device, self.oom, self.fail = device, oom, fail

    def transcribe(self, audio, batch_size, language, progress_callback=None):
        if self.oom(self.device, batch_size):
            raise RuntimeError("out of memory")
        if self.fail and self.device in self.fail:
            raise RuntimeError("MPSGraph: failed assertion 'Error: MLIR pass manager failed'")
        return {"segments": [{"start": 0.0, "end": 1.0, "text": "olá"}]}


def fake_whisperx(load_fail=(), align_fail=(), oom=lambda device, batch: False, run_fail=()):
    """whisperx whose model load fails on the devices in ``load_fail`` and whose alignment fails on those in
    ``align_fail``; it records every call as (what, device, compute_type)."""
    from types import SimpleNamespace
    calls = []

    def load_model(name, device, compute_type=None, language=None, threads=None):
        calls.append(("load", device, compute_type))
        if device in load_fail:
            raise RuntimeError(f"CTranslate2 has no {device} backend")
        return FakeModel(device, oom, run_fail)

    def load_align_model(language_code, device):
        calls.append(("align_model", device, None))
        if device in align_fail:
            raise RuntimeError(f"aten op not implemented on {device}")
        return object(), {}

    def align(segments, model, meta, audio, device, return_char_alignments=False, progress_callback=None):
        calls.append(("align", device, None))
        return {"segments": [dict(s, words=[{"word": "olá", "start": 0.1, "end": 0.5, "score": 0.9}])
                             for s in segments]}
    return SimpleNamespace(calls=calls, load_model=load_model, load_align_model=load_align_model, align=align,
                           load_audio=lambda path: np.zeros(SR, dtype=np.float32))


def asr_args(tmp_path, *extra):
    return TX.build_parser().parse_args(["v.mp4", "--work", str(tmp_path), "--asr-out", str(tmp_path / "a.json"),
                                         "--language", "pt", *extra])


def run_asr(tmp_path, torch, whisperx, *extra):
    return TX.run_asr(asr_args(tmp_path, *extra), torch, whisperx, np.zeros(SR, dtype=np.float32), 1.0,
                      lambda *a, **k: None)


def test_alignment_on_mps_is_retried_on_the_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(TX, "default_threads", lambda: 4)
    torch, wx = fake_torch(mps=True), fake_whisperx(align_fail={"mps"})
    doc = run_asr(tmp_path, torch, wx)
    assert doc["device"] == "cpu" and doc["compute_type"] == "int8"         # the ASR: CTranslate2 on the CPU
    assert doc["align_device"] == "cpu" and doc["aligned"] is True and doc["segments"][0]["words"]
    assert [c[:2] for c in wx.calls if c[0] != "load"] == [("align_model", "mps"), ("align_model", "cpu"),
                                                          ("align", "cpu")]
    assert "mps" in torch.freed and not doc["warnings"]


def test_alignment_device_is_recorded_and_none_when_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(TX, "default_threads", lambda: 4)
    doc = run_asr(tmp_path, fake_torch(mps=True), fake_whisperx())
    assert doc["align_device"] == "mps" and doc["aligned"] is True
    doc = run_asr(tmp_path, fake_torch(mps=True), fake_whisperx(align_fail={"mps", "cpu"}))
    assert doc["align_device"] is None and doc["aligned"] is False
    assert len(doc["warnings"]) == 1 and "word alignment skipped" in doc["warnings"][0]
    wx = fake_whisperx(align_fail={"cpu"})
    doc = run_asr(tmp_path, fake_torch(mps=False), wx)                     # no retry off MPS
    assert doc["align_device"] is None and [c[1] for c in wx.calls if c[0] == "align_model"] == ["cpu"]


def test_alignment_follows_cuda(tmp_path, monkeypatch):
    monkeypatch.setattr(TX, "default_threads", lambda: 4)
    torch = fake_torch(mps=False, cuda=True)
    doc = run_asr(tmp_path, torch, fake_whisperx())
    assert doc["device"] == "cuda" and doc["align_device"] == "cuda" and "cuda" in torch.freed


def test_asr_on_metal_when_asked_and_cpu_int8_when_it_does_not_load(tmp_path, monkeypatch):
    monkeypatch.setattr(TX.sys, "platform", "darwin")
    monkeypatch.setattr(TX, "default_threads", lambda: 12)
    monkeypatch.setenv(TX.ASR_DEVICE_ENV, "mps")
    doc = run_asr(tmp_path, fake_torch(mps=True), fake_whisperx())
    assert (doc["device"], doc["compute_type"], doc["batch_size"]) == ("mps", "float16", TX.DEFAULTS["batch_mps"])
    assert doc["align_device"] == "mps"
    wx = fake_whisperx(load_fail={"mps"})
    doc = run_asr(tmp_path, fake_torch(mps=True), wx)
    assert [c for c in wx.calls if c[0] == "load"] == [("load", "mps", "float16"), ("load", "cpu", "int8")]
    assert (doc["device"], doc["compute_type"]) == ("cpu", "int8") and doc["align_device"] == "mps"
    assert any("ASR on mps failed" in w for w in doc["warnings"])
    doc = run_asr(tmp_path, fake_torch(mps=True), fake_whisperx(), "--device", "cpu")
    assert doc["device"] == "cpu" and doc["align_device"] == "cpu"                # an explicit --device cpu wins
    doc = run_asr(tmp_path, fake_torch(mps=True), fake_whisperx(), "--device", "mps")
    assert doc["device"] == "cpu" and doc["align_device"] == "mps"         # --device mps: only the torch phases


def test_named_batch_for_metal():
    from va import TRANSCRIBE_DEFAULTS
    assert TX.DEFAULTS["batch_mps"] == TRANSCRIBE_DEFAULTS["batch_size"]["mps"]
    assert TX.DEFAULTS["batch_cuda"] == TRANSCRIBE_DEFAULTS["batch_size"]["cuda"]
    assert TX.DEFAULTS["batch_cpu"] == TRANSCRIBE_DEFAULTS["batch_size"]["cpu"]


@pytest.mark.parametrize("platform, value", [("win32", "mps"), ("darwin", ""), ("darwin", "cuda")])
def test_asr_device_env_is_ignored_off_macos_or_with_another_value(tmp_path, monkeypatch, platform, value):
    monkeypatch.setattr(TX.sys, "platform", platform)
    monkeypatch.setattr(TX, "default_threads", lambda: 4)
    monkeypatch.setenv(TX.ASR_DEVICE_ENV, value)
    assert run_asr(tmp_path, fake_torch(mps=True), fake_whisperx())["device"] == "cpu"


def test_asr_on_metal_out_of_memory_at_batch_1_falls_back_to_cpu_int8(tmp_path, monkeypatch):
    monkeypatch.setattr(TX.sys, "platform", "darwin")
    monkeypatch.setattr(TX, "default_threads", lambda: 12)
    monkeypatch.setenv(TX.ASR_DEVICE_ENV, "mps")
    wx = fake_whisperx(oom=lambda device, batch: device == "mps")
    doc = run_asr(tmp_path, fake_torch(mps=True), wx, "--batch-size", "2")
    assert (doc["device"], doc["compute_type"], doc["batch_size"]) == ("cpu", "int8", 2)
    assert [c for c in wx.calls if c[0] == "load"] == [("load", "mps", "float16"), ("load", "mps", "float16"),
                                                       ("load", "cpu", "int8")]


def test_asr_on_metal_any_runtime_error_falls_back_to_cpu_int8(tmp_path, monkeypatch):
    monkeypatch.setattr(TX.sys, "platform", "darwin")
    monkeypatch.setattr(TX, "default_threads", lambda: 12)
    monkeypatch.setenv(TX.ASR_DEVICE_ENV, "mps")
    wx = fake_whisperx(run_fail={"mps"})
    doc = run_asr(tmp_path, fake_torch(mps=True), wx)
    assert (doc["device"], doc["compute_type"], doc["batch_size"]) == ("cpu", "int8", TX.DEFAULTS["batch_cpu"])
    assert [c for c in wx.calls if c[0] == "load"] == [("load", "mps", "float16"), ("load", "cpu", "int8")]
    assert any("ASR on mps failed (RuntimeError: MPSGraph" in w for w in doc["warnings"])
    asr = TX.Asr(fake_whisperx(run_fail={"cpu"}), fake_torch(), "tiny", "cpu", "pt", None, 4).load()
    with pytest.raises(RuntimeError, match="MPSGraph"):                    # off mps the error still surfaces
        asr.transcribe(np.zeros(SR, dtype=np.float32), "pt", None)


def test_language_detection_on_metal_falls_back_to_cpu_int8(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(TX, "default_threads", lambda: 4)
    loads = []

    def load_model(name, device, compute_type=None, language=None, threads=None):
        loads.append((device, compute_type))

        def detect(audio, vad_filter, language_detection_segments):
            if device == "mps":
                raise RuntimeError("MPSGraph does not support this op")
            return "pt", 0.97, None

        def own(audio):
            if device == "mps":
                raise RuntimeError("MPSGraph does not support this op")
            return "pt"
        return SimpleNamespace(model=SimpleNamespace(detect_language=detect), detect_language=own)
    (tmp_path / "partial").mkdir()
    asr = TX.Asr(SimpleNamespace(load_model=load_model), fake_torch(mps=True), "tiny", "mps", "auto", None, 4).load()
    lang, prob = TX.detect_language(asr, np.zeros(SR, dtype=np.float32), str(tmp_path), lambda *a, **k: None)
    assert (lang, prob) == ("pt", 0.97) and loads == [("mps", "float16"), ("cpu", "int8")]
    assert asr.device == "cpu" and any("ASR on mps failed" in n for n in asr.notes)


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, cols):
        return self

    def itertuples(self, index=False):
        return iter(self.rows)


def diar_args(tmp_path):
    return TX.build_parser().parse_args(["v.mp4", "--work", str(tmp_path), "--asr-out", str(tmp_path / "a.json"),
                                         "--diar-out", str(tmp_path / "d.json"), "--diar-key", "d",
                                         "--min-speakers", "2", "--max-speakers", "2"])


@pytest.mark.parametrize("stage", ["load", "run"])
def test_diarization_on_mps_is_retried_on_the_cpu(tmp_path, monkeypatch, stage):
    tried = []

    def load(model, device):
        tried.append(device)
        if device == "mps" and stage == "load":
            raise RuntimeError("MPS backend out of memory")

        def pipe(audio, **kw):
            if device == "mps":
                raise NotImplementedError("aten::_fft_r2c is not implemented for the MPS device")
            assert kw == {"num_speakers": 2}
            return FakeFrame([(1.0, 2.0, "SPEAKER_01"), (0.0, 1.0, "SPEAKER_00")])
        return pipe
    monkeypatch.setattr(TX, "token_present", lambda: True)
    monkeypatch.setattr(TX, "load_diarization", load)
    torch = fake_torch(mps=True)
    doc = TX.run_diarization(diar_args(tmp_path), torch, np.zeros(SR, dtype=np.float32), "mps", lambda *a, **k: None)
    assert tried == ["mps", "cpu"]
    assert doc["diarized"] is True and doc["reason"] is None and doc["device"] == "cpu"
    assert doc["turns"] == [[0.0, 1.0, "SPEAKER_00"], [1.0, 2.0, "SPEAKER_01"]] and doc["speakers_found"] == 2
    assert "mps" in torch.freed


def test_diarization_without_access_is_not_retried_on_the_cpu(tmp_path, monkeypatch):
    tried = []

    def load(model, device):
        tried.append(device)
        raise GatedRepoError("Access to model pyannote/speaker-diarization-community-1 is restricted")
    monkeypatch.setattr(TX, "token_present", lambda: True)
    monkeypatch.setattr(TX, "load_diarization", load)
    doc = TX.run_diarization(diar_args(tmp_path), fake_torch(mps=True), np.zeros(SR, dtype=np.float32), "mps",
                             lambda *a, **k: None)
    assert tried == ["mps"] and doc["reason"] == "no_access" and doc["device"] == "mps" and not doc["diarized"]


def test_diarization_on_the_cpu_is_tried_once(tmp_path, monkeypatch):
    tried = []

    def load(model, device):
        tried.append(device)
        raise RuntimeError("boom")
    monkeypatch.setattr(TX, "token_present", lambda: True)
    monkeypatch.setattr(TX, "load_diarization", load)
    doc = TX.run_diarization(diar_args(tmp_path), fake_torch(), np.zeros(SR, dtype=np.float32), "cpu",
                             lambda *a, **k: None)
    assert tried == ["cpu"] and doc["reason"] == "error: RuntimeError: boom" and doc["device"] == "cpu"


def test_new_asr_diarizes_on_mps(tmp_path, monkeypatch):
    """A fresh run on a Mac: asr.json says the ASR ran on the CPU and the alignment on MPS; pyannote gets MPS."""
    torch, wx = fake_torch(mps=True), fake_whisperx()
    monkeypatch.setattr(TX, "load_libs", lambda: (torch, wx))
    monkeypatch.setattr(TX, "default_threads", lambda: 4)
    seen = []

    def fake_diarization(args, torch, audio, device, progress):
        seen.append(device)
        return {"schema": "va.diar/1", "complete": True, "diarized": False, "turns": []}
    monkeypatch.setattr(TX, "run_diarization", fake_diarization)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"")
    rc = TX.main([str(video), "--work", str(tmp_path), "--asr-out", str(tmp_path / "asr.json"), "--language", "pt",
                  "--diar-out", str(tmp_path / "diar.json"), "--diar-key", "d"])
    doc = json.loads((tmp_path / "asr.json").read_text(encoding="utf-8"))
    assert rc == 0 and seen == ["mps"] and doc["device"] == "cpu" and doc["align_device"] == "mps"


def test_check_reports_the_torch_device_and_retries_on_the_cpu(monkeypatch, capsys):
    torch, wx = fake_torch(mps=True), fake_whisperx(align_fail={"mps"})
    monkeypatch.setattr(TX, "load_libs", lambda: (torch, wx))
    monkeypatch.setattr(TX, "default_threads", lambda: 4)
    monkeypatch.setattr(TX, "token_present", lambda: True)
    tried = []

    def load(model, device):
        tried.append(device)
        if device == "mps":
            raise RuntimeError("MPS backend out of memory")
        return object()
    monkeypatch.setattr(TX, "load_diarization", load)
    code = TX.check(TX.build_parser().parse_args(["--check", "--model", "tiny", "--align", "pt", "--diarize"]))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and out["ok"] is True and out["model_ok"] is True
    assert (out["device"], out["torch_device"], out["mps"]) == ("cpu", "mps", True)
    assert out["align"] == {"pt": "ok"} and out["diarization"] == "ok" and tried == ["mps", "cpu"]
    assert {"error", "gpu", "vram_mb"} <= set(out)
