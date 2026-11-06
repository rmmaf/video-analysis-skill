"""scripts/webcam_mask.py needs DeepFace, in its own environment, so these tests run it with stand-ins for
cv2, numpy and deepface. They cover the YuNet score that reaches DeepFace (yunet_score_threshold), which
the script checks before loading DeepFace, and, with the real numpy, the probe (--probe), the video
path taken as it is before the wildcard, and the exit when prepare.py dies (--watch-stdin)."""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from conftest import SCRIPTS_DIR
from va import webcam

# the stand-in for deepface prints, when imported, the score that DeepFace would read
STUBS = {
    "cv2.py": "",
    "numpy.py": "",
    "deepface/__init__.py": "import os, sys\n"
                            "print('deepface score=' + os.environ['yunet_score_threshold'], file=sys.stderr)\n"
                            "DeepFace = None\n",
}


@pytest.fixture
def run(tmp_path):
    """Runs webcam_mask.py on a video that does not exist, with the given yunet_score_threshold (None:
    without the variable); returns the exit code and the error output."""
    for name, text in STUBS.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def run_script(score):
        env = {k: v for k, v in os.environ.items() if k.lower() != "yunet_score_threshold"}
        env.update(PYTHONPATH=str(tmp_path), PYTHONIOENCODING="utf-8")
        if score is not None:
            env["yunet_score_threshold"] = score
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS_DIR / "webcam_mask.py"), str(tmp_path / "none.mp4")],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        return r.returncode, r.stderr

    return run_script


@pytest.mark.parametrize("score, seen", [(None, "0.85"), ("0.9", "0.9"), (" 0.9 ", "0.9"), ("0.75", "0.75"),
                                         ("0.999", "0.999")])
def test_valid_score_reaches_deepface(run, score, seen):
    """Without the variable DeepFace reads 0.85, not its own 0.9; a valid value reaches it as a plain
    number, and the script goes on to open the video."""
    code, err = run(score)
    assert f"deepface score={seen}\n" in err
    assert code == 1 and "esperava um video, achei 0" in err


@pytest.mark.parametrize("score", ["", "abc", "0,85", "nan", "inf", "-inf", "-0.85", "0", "0.74", "1", "1.0",
                                   "85"])
def test_invalid_score_stops_before_deepface(run, score):
    """Not a number, not finite, below 0.75, or 1 and above: a clear error, before DeepFace is loaded. A low
    score lets in false faces, which become ignored regions when they move; at 1 YuNet finds no face."""
    code, err = run(score)
    assert code == 1
    assert err == f"yunet_score_threshold={score!r}: use um numero com ponto, de 0.75 ate menos de 1" \
                  " (sem a variavel, 0.85)\n"


# --- the probe (--probe) and the video path, with the real numpy -----------------------------------------

# A 60 s video of 1080p frames. Every call goes to STUB_LOG: "open" (a capture), "seek" (a probe read
# after a seek), "pass" (a capture read from the start without seeking: the probe's pass or the full scan).
# Like OpenCV: CAP_PROP_FPS is the average frame rate, a seek goes by frame count (msec * fps, which misses
# the time in a variable frame rate video), and CAP_PROP_POS_MSEC is the timestamp of the last frame read.
#   STUB_VFR=burst  30 fps in the first 10 s, then 1 fps (variable frame rate); =tail: 10 fps up to 48 s
#                   and no frame after (a still screen at the end); else 10 fps
#   STUB_SECONDS    the length of the 10 fps video (60 by default)
#   STUB_END        frames from that second on do not decode (a broken file whose header says 60 s)
#   STUB_OPEN_FAIL  the captures that do not open, by order (1 is the one that reads the frame rate)
#   STUB_HANG       a capture takes 2 min to open, writing "tick" every 0.1 s (a long scan)
CV2_STUB = r'''
import os
import time
import numpy as np

CAP_FFMPEG, CAP_PROP_POS_MSEC, CAP_PROP_FPS, CAP_PROP_FRAME_COUNT = 1900, 0, 5, 7
COLOR_BGR2GRAY, INTER_AREA, INTER_NEAREST = 6, 3, 0
SECONDS = int(os.environ.get("STUB_SECONDS") or 60)
VFR = os.environ.get("STUB_VFR")
if VFR == "burst":
    TIMES = [i / 30 for i in range(300)] + [10.0 + i for i in range(50)]
elif VFR == "tail":
    TIMES = [i / 10 for i in range(480)]
else:
    TIMES = [i / 10 for i in range(SECONDS * 10)]
FPS = len(TIMES) / SECONDS
END = float(os.environ.get("STUB_END") or "inf")
CLOSED = os.environ.get("STUB_OPEN_FAIL", "").split(",")
TIMECODE = os.environ.get("STUB_FACES", "").startswith("window")
_opened = [0]


def _log(what):
    with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
        fh.write(what + "\n")


def _frame(t):
    """Black; with STUB_FACES=window, the time in channel 0 (t * 2: what the deepface stand-in reads, and a
    face that moves 4 grey levels between samples 2 s apart) and a mark on the top-left pixel."""
    f = np.zeros((1080, 1920, 3), np.uint8)
    if TIMECODE:
        f[..., 0] = int(t * 2) % 256
        f[0, 0, 1] = 255
    return f


class VideoCapture:
    def __init__(self, path, backend=None):
        _opened[0] += 1
        self.ok = str(_opened[0]) not in CLOSED
        self.i, self.t, self.sought = 0, None, False
        _log("open")
        end = time.monotonic() + (120 if os.environ.get("STUB_HANG") else 0)
        while time.monotonic() < end:
            _log("tick")
            time.sleep(0.1)

    def isOpened(self):
        return self.ok

    def get(self, prop):
        if prop == CAP_PROP_POS_MSEC:
            return 0.0 if self.t is None else self.t * 1000
        return {CAP_PROP_FPS: FPS, CAP_PROP_FRAME_COUNT: len(TIMES)}.get(prop, 0.0)

    def set(self, prop, value):
        _log("seek")
        self.i, self.sought = min(len(TIMES), int(value / 1000 * FPS + 0.5)), True
        return True

    def grab(self):
        if not self.ok or self.i >= len(TIMES) or TIMES[self.i] >= END:
            return False
        if self.i == 0 and not self.sought:
            _log("pass")
        self.t = TIMES[self.i]
        self.i += 1
        return True

    def read(self):
        ok = self.grab()
        return ok, _frame(self.t) if ok else None

    def retrieve(self):
        return True, _frame(self.t)

    def release(self):
        pass


def cvtColor(img, code):
    return img[..., 0]


def resize(img, size, fx=None, fy=None, interpolation=None):
    return np.zeros((size[1], size[0]), img.dtype)
'''

# STUB_FACES=one: a face at the same place of every tile (a still face: not live, so no span);
# =window:A:B: a face in the top-left tile only, from second A to B (it moves: the frames carry the time);
# else none
DEEPFACE_STUB = '''
import os

FACE = [{"confidence": 0.95, "facial_area": {"x": 100, "y": 100, "w": 60, "h": 60}}]
NONE = [{"confidence": 0, "facial_area": {"x": 0, "y": 0, "w": 640, "h": 540}}]


class DeepFace:
    @staticmethod
    def extract_faces(img_path, detector_backend, enforce_detection, align):
        mode = os.environ.get("STUB_FACES", "")
        if mode == "one":
            return FACE
        if mode.startswith("window:") and img_path[0, 0, 1] == 255:
            a, b = (float(v) for v in mode.split(":")[1:])
            if a <= img_path[0, 0, 0] / 2 < b:
                return FACE
        return NONE
'''


@pytest.fixture
def mask(tmp_path):
    """Runs webcam_mask.py with the stubs above (and the real numpy); returns (exit code, stdout, stderr,
    the calls of the stub VideoCapture)."""
    stubs = tmp_path / "stubs"
    (stubs / "deepface").mkdir(parents=True)
    (stubs / "cv2.py").write_text(CV2_STUB, encoding="utf-8")
    (stubs / "deepface" / "__init__.py").write_text(DEEPFACE_STUB, encoding="utf-8")
    log = tmp_path / "cv2.log"

    def run_script(*args, faces=None, **stub):
        """``stub``: the video's STUB_* settings (vfr="burst", end=20, open_fail="2,3")."""
        env = {k: v for k, v in os.environ.items() if k.lower() != "yunet_score_threshold"}
        env.update(PYTHONPATH=str(stubs), PYTHONIOENCODING="utf-8", STUB_LOG=str(log))
        env.update({f"STUB_{k.upper()}": str(v) for k, v in stub.items()})
        if faces:
            env["STUB_FACES"] = faces
        if log.exists():
            log.unlink()
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS_DIR / "webcam_mask.py"), *map(str, args)],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
        calls = log.read_text(encoding="utf-8").split() if log.exists() else []
        return r.returncode, r.stdout, r.stderr, calls

    return run_script


def test_probe_without_a_face_skips_the_full_scan(mask, tmp_path):
    """No face in the probe: an empty timeline marked "scan": "probe", and the full scan never starts."""
    video = tmp_path / "reuniao.mp4"
    video.write_bytes(b"")
    out = tmp_path / "linha.json"
    code, stdout, stderr, calls = mask(video, "--probe", 12, "--timeline", out)
    assert code == 0, stderr
    assert stdout.splitlines() == ["nenhuma webcam achada"]
    assert "sondagem: 12 de 12 amostras, em " in stderr and "sem a varredura completa" in stderr
    assert calls.count("seek") == 12 and "pass" not in calls
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["scan"] == "probe" and doc["ignore_timeline"] == [] and doc["step_s"] is None
    p = doc["probe"]
    assert (p["targets"], p["samples"], p["covered"], p["complete"], p["walked"]) == (12, 12, 12, True, False)
    assert p["best_hits"] == 0 and doc["min_score"] == 0.85
    assert doc["video"] == os.path.abspath(video) and doc["made_by"] == "webcam_mask.py"


def test_probe_with_a_face_runs_the_full_scan(mask, tmp_path):
    """A face in the same place in 2 or more probe samples: the full scan runs (from the start of the video)
    and the timeline says "scan": "full"; a still face (a photo) makes no span."""
    video = tmp_path / "reuniao.mp4"
    video.write_bytes(b"")
    out = tmp_path / "linha.json"
    code, stdout, stderr, calls = mask(video, "--probe", 6, "--timeline", out, faces="one")
    assert code == 0, stderr
    assert calls.count("seek") == 6 and calls.count("pass") == 1
    assert calls.index("pass") > max(i for i, c in enumerate(calls) if c == "seek")
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["scan"] == "full" and doc["step_s"] == 2.0 and doc["probe"]["best_hits"] == 6
    assert doc["ignore_timeline"] == []                      # the stub's face does not move


def test_without_probe_the_timeline_is_a_full_scan(mask, tmp_path):
    video = tmp_path / "reuniao.mp4"
    video.write_bytes(b"")
    out = tmp_path / "linha.json"
    code, _, stderr, calls = mask(video, "--timeline", out)
    assert code == 0, stderr
    assert "seek" not in calls and calls.count("pass") == 1
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["scan"] == "full" and doc["probe"] is None


def spans_of(path):
    return [(t["start"], t["end"]) for t in json.loads(path.read_text(encoding="utf-8"))["ignore_timeline"]]


def test_variable_frame_rate_keeps_the_time(mask, tmp_path):
    """A face from 25 to 55 s. In the variable frame rate video, OpenCV's seeks (by frame count) land in the
    first 10 s but one: the probe goes through the video from the start and finds the face, and the full
    scan times each sample by the frame's timestamp, so the span is the same as at a constant frame rate
    (24-56 s: one 2 s step around the samples 26-54 s). Before, the probe saw the face once (no webcam)
    and frame / average fps put the span after the end of the face."""
    video = tmp_path / "reuniao.mp4"
    video.write_bytes(b"")
    cfr, vfr = tmp_path / "cfr.json", tmp_path / "vfr.json"
    code, _, stderr, calls = mask(video, "--probe", 6, "--timeline", cfr, faces="window:25:55")
    assert code == 0, stderr
    assert calls.count("pass") == 1 and "passada" not in stderr          # the seeks hit: no pass in the probe
    code, _, stderr, calls = mask(video, "--probe", 6, "--timeline", vfr, faces="window:25:55", vfr="burst")
    assert code == 0, stderr
    assert calls.count("seek") == 6 and calls.count("pass") == 2         # the probe's pass, then the full scan
    assert "sondagem: 6 de 6 amostras, com passada do comeco ao fim" in stderr
    doc = json.loads(vfr.read_text(encoding="utf-8"))
    p = doc["probe"]
    assert (p["samples"], p["complete"], p["walked"], p["best_hits"]) == (6, True, True, 3) and doc["scan"] == "full"
    assert spans_of(cfr) == spans_of(vfr) == [(24.0, 56.0)]


@pytest.mark.parametrize("stub, needle, covered", [
    ({"end": 20}, "sondagem incompleta: 2 de 6 partes do video cobertas", 2),     # stops at 20 s of 60
    ({"open_fail": "2,3"}, "sondagem incompleta: 0 de 6 partes do video cobertas", 0),   # the probe's captures
])
def test_a_probe_that_does_not_cover_the_video_goes_on_to_the_full_scan(mask, tmp_path, stub, needle, covered):
    """Without samples from the whole video, "no webcam" is not known: the full scan runs instead of an
    empty probe-only timeline that the cache would keep."""
    video = tmp_path / "reuniao.mp4"
    video.write_bytes(b"")
    out = tmp_path / "linha.json"
    code, stdout, stderr, calls = mask(video, "--probe", 6, "--timeline", out, **stub)
    assert code == 0, stderr
    assert needle in stderr and calls[-1] == "pass" and "sem a varredura completa" not in stderr
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["scan"] == "full" and doc["probe"]["complete"] is False and doc["probe"]["covered"] == covered


def test_a_short_video_goes_to_the_full_scan(mask, tmp_path):
    """8 s of video with a face all along: prepare.py asks for a 1-sample probe (one every 10 s), and one
    sample cannot show the face in the 2 the probe needs, so it would always say "no webcam" (and the cache
    would keep that). The script goes straight to the full scan, which finds the face."""
    video = tmp_path / "curto.mp4"
    video.write_bytes(b"")
    out = tmp_path / "linha.json"
    code, stdout, stderr, calls = mask(video, "--probe", 1, "--timeline", out, faces="window:0:8", seconds=8)
    assert code == 0, stderr
    assert "nao dizem que nao ha webcam; segue a varredura completa (video curto)" in stderr
    assert "seek" not in calls and calls.count("pass") == 1 and "nenhuma webcam achada" not in stdout
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["scan"] == "full" and doc["probe"] is None and len(spans_of(out)) == 1


def test_a_still_screen_at_the_end_is_covered(mask, tmp_path):
    """No frame in the last part of a variable frame rate video (the screen did not change): the pass ends
    before the end, but within the last 2 parts, and the probe still counts."""
    video = tmp_path / "reuniao.mp4"
    video.write_bytes(b"")
    out = tmp_path / "linha.json"
    code, stdout, stderr, calls = mask(video, "--probe", 6, "--timeline", out, vfr="tail")
    assert code == 0, stderr
    assert stdout.splitlines() == ["nenhuma webcam achada"] and calls.count("pass") == 1   # the probe's pass only
    doc = json.loads(out.read_text(encoding="utf-8"))
    p = doc["probe"]
    assert doc["scan"] == "probe" and (p["samples"], p["covered"], p["complete"], p["walked"]) == (5, 6, True, True)


def test_brackets_in_the_name_are_literal(mask, tmp_path):
    """The path is taken as it is before the wildcard: "[1]" would be a character class for glob."""
    video = tmp_path / "Reunião [1].mp4"
    video.write_bytes(b"")
    code, stdout, stderr, _ = mask(video, "--probe", 3)
    assert code == 0, stderr
    assert stdout.splitlines() == ["nenhuma webcam achada"]
    code, _, stderr, _ = mask(tmp_path / "Reuni*o ?1?.mp4", "--probe", 3)   # the wildcard still works
    assert code == 0, stderr


def text_of(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def wait_for(ready, seconds):
    end = time.monotonic() + seconds
    while not ready():
        if time.monotonic() > end:
            return False
        time.sleep(0.1)
    return True


@pytest.mark.parametrize("faces, scan", [(None, "probe"), ("window:25:55", "full")])
def test_watch_stdin_leaves_a_normal_run_alone(mask, tmp_path, faces, scan):
    """Run as prepare.py runs it (webcam.run_process: a pipe open on its stdin, --watch-stdin), the script
    ends as without the flag: exit 0 and the timeline. The thread that waits on stdin must not sit inside
    sys.stdin's reader: Python closes it when it ends and, finding its lock taken, stops with a fatal error
    after the work is done (seen in a real run with DeepFace, whose timeline prepare.py then dropped)."""
    video = tmp_path / "reuniao.mp4"
    video.write_bytes(b"")
    out, log = tmp_path / "linha.json", tmp_path / "webcam_mask.log"
    env = {k: v for k, v in os.environ.items() if k.lower() != "yunet_score_threshold"}
    env.update(PYTHONPATH=str(tmp_path / "stubs"), PYTHONIOENCODING="utf-8", STUB_LOG=str(tmp_path / "cv2.log"))
    if faces:
        env["STUB_FACES"] = faces
    cmd = [sys.executable, "-B", str(SCRIPTS_DIR / "webcam_mask.py"), str(video), "--probe", "6", "--timeline",
           str(out), "--watch-stdin"]
    assert webcam.run_process(cmd, str(log), env) == 0, text_of(log)[-600:]
    assert "Fatal Python error" not in text_of(log)
    assert json.loads(out.read_text(encoding="utf-8"))["scan"] == scan


def test_the_script_does_not_outlive_prepare(mask, tmp_path):
    """prepare.py (webcam.make_timeline) runs the script with --watch-stdin and a pipe on its stdin that
    stays open while it waits. Killed hard (by the task manager, say), prepare.py takes no child process
    with it, but the pipe closes, and the script exits instead of scanning on alone, with nobody to read
    the timeline. Here the stand-in capture takes 2 min to open and writes a tick every 0.1 s meanwhile."""
    video = tmp_path / "reuniao.mp4"
    video.write_bytes(b"")
    ticks, pid_file, vdir = tmp_path / "cv2.log", tmp_path / "prepare.pid", tmp_path / "cache" / "reuniao-1234abcd"
    prepare = (f"import os, sys\nsys.path.insert(0, {str(SCRIPTS_DIR)!r})\nfrom va import webcam\n"
               f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
               f"webcam.make_timeline(python=sys.executable, script={str(SCRIPTS_DIR / 'webcam_mask.py')!r}, "
               f"video_path={str(video)!r}, video_dir={str(vdir)!r}, key='k1', scan='full', duration_s=60.0)\n")
    env = {k: v for k, v in os.environ.items() if k.lower() != "yunet_score_threshold"}
    env.update(PYTHONPATH=str(tmp_path / "stubs"), PYTHONIOENCODING="utf-8", STUB_LOG=str(ticks), STUB_HANG="1")
    proc = subprocess.Popen([sys.executable, "-B", "-c", prepare], env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        assert wait_for(lambda: "tick" in text_of(ticks), 60), "webcam_mask.py did not start"
        os.kill(int(pid_file.read_text()), getattr(signal, "SIGKILL", signal.SIGTERM))   # Windows: TerminateProcess
    finally:
        proc.kill()
        proc.wait(timeout=30)
    log = vdir / "webcam-k1" / "webcam_mask.log"
    assert wait_for(lambda: "a entrada padrao fechou (o prepare.py parou): saindo" in text_of(log), 30), \
        text_of(log)[-400:]
    time.sleep(0.5)            # os._exit comes right after the message; a last tick may land in between
    n = text_of(ticks).count("tick")
    time.sleep(1.0)
    assert text_of(ticks).count("tick") == n                  # no tick since: the script is gone


@pytest.mark.parametrize("samples, expected", [
    ([[(0, 0, 10, 10)], [(1, 1, 10, 10)], []], (2, 1)),       # the same place in 2 samples
    ([[(0, 0, 10, 10)], [(100, 100, 10, 10)]], (1, 2)),       # two places, one sample each
    ([[(0, 0, 10, 10), (1, 0, 10, 10)]], (1, 1)),             # two tiles, same face, same sample
    ([], (0, 0)),
])
def test_best_place(mask, tmp_path, samples, expected):
    stubs = tmp_path / "stubs"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(stubs), str(SCRIPTS_DIR)]), "PYTHONIOENCODING": "utf-8"}
    code = f"import webcam_mask as w; print(list(w.best_place({samples!r})))"
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, encoding="utf-8", env=env,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1]) == list(expected)
