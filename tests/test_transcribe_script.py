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
