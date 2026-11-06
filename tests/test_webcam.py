"""The webcam timeline that prepare.py makes with DeepFace (scripts/va/webcam.py) and the DeepFace
environment of scripts/va/environment.py, without DeepFace: a fake webcam_mask.py and a fake environment
(a folder on PYTHONPATH with the package metadata). Nothing is installed or downloaded: install_deepface
runs with a fake runner, and the weights download with a fake opener."""
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

from va import WEBCAM_DEFAULTS, cache, environment, webcam

# what webcam_mask.py --timeline does, driven by a JSON spec inside the fake video: "spans" (the
# timeline of a webcam that was found), "fail" (exits with an error), "garbage" (writes broken JSON),
# "uncovered" (an empty probe-only timeline whose probe did not cover the video: never written by the
# real script, which goes on to the full scan)
FAKE_MASK = r'''
import json, sys
args = sys.argv[1:]
out = args[args.index("--timeline") + 1]
probe = int(args[args.index("--probe") + 1]) if "--probe" in args else 0
spec = json.loads(open(args[0], encoding="utf-8").read() or "{}")
print("rodando", " ".join(args[1:]), file=sys.stderr)
if spec.get("fail"):
    sys.exit("o OpenCV nao abriu o video")
if spec.get("garbage"):
    open(out, "w").write("{")
    sys.exit(0)
spans = spec.get("spans", [])
info = {"targets": probe, "samples": 0 if spec.get("uncovered") else probe, "complete": not spec.get("uncovered")}
json.dump({"video": args[0], "made_by": "webcam_mask.py", "scan": "probe" if probe and not spans else "full",
           "probe": info if probe else None, "min_score": 0.85, "ignore_timeline": spans},
          open(out, "w", encoding="utf-8"))
'''
SPAN = {"start": 0, "end": 40, "region": "0.8,0,0.2,0.2"}


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / "fake_mask.py"
    script.write_text(FAKE_MASK, encoding="utf-8")

    def make(spec: dict, scan: str = "probe", key: str = "k1"):
        video = tmp_path / "Reunião [1].mp4"
        video.write_text(json.dumps(spec), encoding="utf-8")
        return webcam.make_timeline(python=sys.executable, script=script, video_path=video,
                                    video_dir=tmp_path / "cache" / "reuniao-1234abcd", key=key, scan=scan,
                                    duration_s=115.0)

    return make


def test_key_follows_the_video_and_the_score():
    k = webcam.webcam_key("reuniao-1234abcd")
    assert k == webcam.webcam_key("reuniao-1234abcd", None) and len(k) == 8
    assert k != webcam.webcam_key("reuniao-1234abcd", "0.9") != webcam.webcam_key("outra-5678ef90")


def test_key_follows_the_detector(monkeypatch):
    """The setup's deepface version and YuNet weights are in the key: when the skill moves to another
    detector, the timelines are made again."""
    keys = {webcam.webcam_key("reuniao-1234abcd")}
    monkeypatch.setitem(WEBCAM_DEFAULTS, "deepface_version", "0.0.105")
    keys.add(webcam.webcam_key("reuniao-1234abcd"))
    monkeypatch.setitem(WEBCAM_DEFAULTS, "yunet_sha256", "0" * 64)
    keys.add(webcam.webcam_key("reuniao-1234abcd"))
    assert len(keys) == 3


def test_score_setting(monkeypatch):
    monkeypatch.delenv("yunet_score_threshold", raising=False)
    assert webcam.score_setting() is None
    monkeypatch.setenv("yunet_score_threshold", " 0.9 ")
    assert webcam.score_setting() == "0.9"


@pytest.mark.parametrize("duration, n", [(0, 1), (35, 4), (115, 12), (600, 60), (7200, 60)])
def test_probe_samples(duration, n):
    assert webcam.probe_samples(duration) == n


@pytest.mark.parametrize("w, h, tiles", [(1920, 1080, 15), (1280, 720, 6), (3024, 1964, 63), (640, 480, 1)])
def test_tiles_per_frame(w, h, tiles):
    """The tiles of webcam_mask.py --tiles overlap: 640x540, half overlapping, the last one at the edge."""
    assert webcam.tiles_per_frame(w, h) == tiles


def test_estimate_follows_the_measurements():
    """1080p60, one hour: the full scan was measured at 24 min (34 with the machine busy); the probe adds
    about a minute; 720p has 6 tiles instead of 15."""
    hd60 = {"duration_s": 3600.0, "width": 1920, "height": 1080, "fps_avg": 60.0}
    full = webcam.estimate_seconds(hd60, "full")
    assert 24 * 60 <= full <= 34 * 60
    assert 30 <= webcam.estimate_seconds(hd60, "probe") - full <= 120
    hd30 = {"duration_s": 3600.0, "width": 1280, "height": 720, "fps_avg": 30.0}
    assert webcam.estimate_seconds(hd30, "full") < full / 2
    assert webcam.estimate_seconds({"duration_s": 0}, "probe") >= WEBCAM_DEFAULTS["startup_s"]
    # variable frame rate: the probe may go through the whole video (OpenCV's seek misses), one more
    # decoding pass; the full scan alone does not change
    vfr = {**hd60, "vfr": True}
    decode = 3600 * 60 * 1920 * 1080 / 1e6 * WEBCAM_DEFAULTS["decode_s_per_mpx"]
    assert webcam.estimate_seconds(vfr, "probe") == pytest.approx(webcam.estimate_seconds(hd60, "probe") + decode)
    assert webcam.estimate_seconds(vfr, "full") == full


def test_make_timeline_keeps_it_in_the_cache(fake, tmp_path):
    res = fake({"spans": [SPAN]})
    sdir = tmp_path / "cache" / "reuniao-1234abcd" / "webcam-k1"
    assert res == {"spans": [[0.0, 40.0, 0.8, 0.0, 0.2, 0.2]], "scan": "full",
                   "path": (sdir / "timeline.json").as_posix(), "seconds": res["seconds"]}
    assert sorted(p.name for p in sdir.iterdir()) == ["stage.json", "timeline.json", "webcam_mask.log"]
    log = (sdir / "webcam_mask.log").read_text(encoding="utf-8")
    assert "rodando --timeline" in log and "--probe 12" in log           # 115 s: one probe frame every 10 s
    assert "--watch-stdin" in log                                        # it exits if prepare.py dies
    stage = cache.read_stage(sdir)
    assert stage["stage"] == "webcam" and stage["key"] == "k1" and stage["scan"] == "full" and stage["n_spans"] == 1
    assert stage["inputs"]["video"].endswith("Reunião [1].mp4")
    hit = webcam.cached(tmp_path / "cache" / "reuniao-1234abcd", "k1")
    assert hit["spans"] == res["spans"] and hit["scan"] == "full"
    assert webcam.cached(tmp_path / "cache" / "reuniao-1234abcd", "k1", need_full=True) == hit
    assert webcam.cached(tmp_path / "cache" / "reuniao-1234abcd", "outra") is None


def test_a_probe_only_timeline_does_not_serve_on(fake, tmp_path):
    """No face in the probe: an empty timeline that auto reuses, but on (need_full) makes again."""
    res = fake({})
    assert res["spans"] == [] and res["scan"] == "probe"
    vdir = tmp_path / "cache" / "reuniao-1234abcd"
    assert webcam.cached(vdir, "k1")["spans"] == []
    assert webcam.cached(vdir, "k1", need_full=True) is None
    res = fake({}, scan="full")                                    # on: no --probe, a full scan
    assert res["scan"] == "full" and "--probe" not in (vdir / "webcam-k1" / "webcam_mask.log").read_text(encoding="utf-8")
    assert webcam.cached(vdir, "k1", need_full=True)["scan"] == "full"


def test_a_probe_that_did_not_cover_the_video_is_not_kept(fake, tmp_path):
    """"No webcam" from a probe that did not cover the video would be reused by every auto run: it is refused
    when made, and a cache entry without the probe's ``complete`` is not used."""
    with pytest.raises(webcam.WebcamError) as exc:
        fake({"uncovered": True})
    assert "stopped at a probe that did not cover the video" in str(exc.value)
    vdir = tmp_path / "cache" / "reuniao-1234abcd"
    assert not (vdir / "webcam-k1" / "stage.json").exists() and webcam.cached(vdir, "k1") is None
    fake({})                                                          # a covered probe is kept...
    stage = vdir / "webcam-k1" / "stage.json"
    doc = json.loads(stage.read_text(encoding="utf-8"))
    assert doc["probe"] == {"targets": 12, "samples": 12, "complete": True} and webcam.cached(vdir, "k1")
    for probe in ({"samples": 12}, None, {**doc["probe"], "complete": "yes"}):   # ...one without "complete" is not
        stage.write_text(json.dumps({**doc, "probe": probe}), encoding="utf-8")
        assert webcam.cached(vdir, "k1") is None


@pytest.mark.parametrize("spec, needle", [
    ({"fail": True}, "exited with 1: rodando --timeline"),
    ({"garbage": True}, "wrote no valid timeline"),
])
def test_failure_leaves_no_complete_folder(fake, tmp_path, spec, needle):
    with pytest.raises(webcam.WebcamError) as exc:
        fake(spec)
    assert needle in str(exc.value)
    if spec.get("fail"):
        assert "o OpenCV nao abriu o video" in str(exc.value)          # the end of the log
    sdir = tmp_path / "cache" / "reuniao-1234abcd" / "webcam-k1"
    assert not (sdir / "stage.json").exists() and webcam.cached(sdir.parent, "k1") is None


def test_run_process_keeps_stdin_open_until_the_script_ends(tmp_path):
    """webcam_mask.py --watch-stdin exits when its stdin closes: run_process gives it a pipe that stays open
    while prepare.py waits (a closed stdin, or none, would stop the script at once). The child below exits
    with 0 if its stdin is still open after half a second, and with 9 at the end of its stdin."""
    child = tmp_path / "child.py"
    child.write_text("import os, sys, threading\n"
                     "threading.Timer(0.5, os._exit, (0,)).start()\n"
                     "sys.stdin.buffer.read()\n"
                     "os._exit(9)\n", encoding="utf-8")
    assert webcam.run_process([sys.executable, "-B", str(child)], str(tmp_path / "child.log")) == 0


def test_incomplete_or_foreign_folders_are_not_used(fake, tmp_path):
    fake({"spans": [SPAN]})
    vdir = tmp_path / "cache" / "reuniao-1234abcd"
    stage = vdir / "webcam-k1" / "stage.json"
    doc = json.loads(stage.read_text(encoding="utf-8"))
    stage.write_text(json.dumps({**doc, "key": "outra"}), encoding="utf-8")
    assert webcam.cached(vdir, "k1") is None
    stage.write_text(json.dumps(doc), encoding="utf-8")
    (vdir / "webcam-k1" / "timeline.json").write_text('{"video": "x"}', encoding="utf-8")
    assert webcam.cached(vdir, "k1") is None
    stage.unlink()
    assert webcam.cached(vdir, "k1") is None


# --- the DeepFace environment -------------------------------------------------------------------------------

WEIGHTS = b"pesos da yunet"   # stands for the 232,589-byte file: its SHA-256 replaces the real one
# a deepface that runs the detection of environment._DEEPFACE_CHECK (finding no face)
DEEPFACE_RUNS = "class DeepFace:\n    @staticmethod\n    def extract_faces(**kw):\n        return []\n"


@pytest.fixture
def weights(tmp_path, monkeypatch):
    """DEEPFACE_HOME in tmp_path, with the stand-in YuNet weights in place and taken as intact."""
    monkeypatch.setenv("DEEPFACE_HOME", str(tmp_path / "home"))
    monkeypatch.setitem(WEBCAM_DEFAULTS, "yunet_sha256", hashlib.sha256(WEIGHTS).hexdigest())
    path = environment.yunet_weights()
    path.parent.mkdir(parents=True)
    path.write_bytes(WEIGHTS)
    return path


class FakeOpener:
    """urllib's opener for download_weights: each answer in turn, an exception or the body."""
    def __init__(self, *answers):
        self.answers, self.urls = list(answers), []

    def __call__(self, url, timeout):
        self.urls.append(url)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(answer)


def no_download(url, timeout):
    pytest.fail("the weights are in place: nothing to download")


def fake_site(root: Path, version: str | None = "0.0.104", modules=("deepface", "cv2"), deepface_init: str = "") -> Path:
    """A folder for PYTHONPATH that looks like the DeepFace environment to importlib (numpy is the real one);
    ``deepface_init`` is deepface/__init__.py (empty: the package is there, but DeepFace does not import)."""
    root.mkdir(parents=True, exist_ok=True)
    if version:
        info = root / f"deepface-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: deepface\nVersion: {version}\n", encoding="utf-8")
    for m in modules:
        (root / m).mkdir()
        (root / m / "__init__.py").write_text(deepface_init if m == "deepface" else "", encoding="utf-8")
    return root


def test_deepface_status_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_DEEPFACE_ENV", str(tmp_path / "nenhum"))
    st = environment.deepface_status()
    assert st["ok"] is False and st["state"] == "absent" and st["version"] is None
    assert st["env"] == (tmp_path / "nenhum").as_posix() and "no DeepFace environment at" in st["detail"]
    assert st["python"].endswith("/Scripts/python.exe" if sys.platform == "win32" else "/bin/python")


def test_deepface_env_dir_default_and_override(tmp_path, monkeypatch):
    monkeypatch.delenv("VA_DEEPFACE_ENV", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    assert environment.deepface_env_dir() == tmp_path / "user" / "deepface-env"
    monkeypatch.setenv("VA_DEEPFACE_ENV", f'"{tmp_path / "outro"}"')
    assert environment.deepface_env_dir() == tmp_path / "outro"
    assert environment.deepface_env_dir(tmp_path / "arg") == tmp_path / "arg"


@pytest.mark.parametrize("version, modules, ok, problem, needle", [
    ("0.0.104", ("deepface", "cv2"), True, None, "deepface 0.0.104 in "),
    ("0.0.99", ("deepface", "cv2"), False, "version", "has deepface 0.0.99, and the skill uses 0.0.104"),
    ("0.0.104", ("deepface",), False, "modules", "lacks cv2"),
    (None, ("cv2",), False, "modules", "lacks deepface"),
])
def test_deepface_status_runs_the_environments_python(tmp_path, monkeypatch, weights, version, modules, ok, problem,
                                                      needle):
    """Only the version the setup installs runs: another one may find other faces, and the timeline's cache
    key holds the setup's version."""
    monkeypatch.setenv("PYTHONPATH", str(fake_site(tmp_path / "site", version, modules)))
    monkeypatch.setenv("VA_DEEPFACE_ENV", str(tmp_path / "env"))
    st = environment.deepface_status(python=sys.executable)
    assert st["ok"] is ok and st["state"] == ("ok" if ok else "broken") and st["problem"] == problem
    assert needle in st["detail"] and st["version"] == version and st["expected"] == "0.0.104"


@pytest.mark.parametrize("content, needle", [
    (None, "the YuNet weights are not in "),
    (b"", "are damaged (not the file the setup downloads)"),
    (b"<html>too many requests</html>", "are damaged (not the file the setup downloads)"),
])
def test_deepface_status_needs_intact_weights(tmp_path, monkeypatch, weights, content, needle):
    """Missing or damaged weights (a cut download, a page instead of the file): broken, so prepare.py does
    not run a DeepFace that would fail on them or download them in the middle of an analysis."""
    if content is None:
        weights.unlink()
    else:
        weights.write_bytes(content)
    monkeypatch.setenv("PYTHONPATH", str(fake_site(tmp_path / "site")))
    monkeypatch.setenv("VA_DEEPFACE_ENV", str(tmp_path / "env"))
    st = environment.deepface_status(python=sys.executable)
    assert st["ok"] is False and st["problem"] == "weights" and needle in st["detail"] and st["weights"] is False
    assert environment.weights_state(weights) == ("missing" if content is None else "damaged")


@pytest.mark.parametrize("init, ok", [(DEEPFACE_RUNS, True), ("", False)])
def test_the_deep_status_runs_a_detection(tmp_path, monkeypatch, weights, init, ok):
    """find_spec does not see a package that does not load: the quick check passes both. The deep one
    (check_environment.py) imports DeepFace and runs a detection."""
    monkeypatch.setenv("PYTHONPATH", str(fake_site(tmp_path / "site", deepface_init=init)))
    monkeypatch.setenv("VA_DEEPFACE_ENV", str(tmp_path / "env"))
    assert environment.deepface_status(python=sys.executable)["ok"] is True
    st = environment.deepface_status(python=sys.executable, deep=True)
    assert st["ok"] is ok and st["problem"] == (None if ok else "detection")
    if not ok:
        assert st["detail"].startswith(f"DeepFace did not run a detection in {(tmp_path / 'env').as_posix()} (exit 1): ")
        assert "cannot import name 'DeepFace'" in st["detail"]


def test_download_weights_retries_and_checks_the_hash(weights):
    """Up to 8 GETs: a reset connection or another content (a page instead of the file) is tried again, and
    only the file with the expected SHA-256 is written."""
    weights.unlink()
    opener = FakeOpener(ConnectionResetError(10054, "reset"), b"<html>", WEIGHTS)
    assert environment.download_weights(opener=opener, pause_s=0) == 3
    assert weights.read_bytes() == WEIGHTS and opener.urls == [WEBCAM_DEFAULTS["yunet_url"]] * 3
    weights.unlink()
    with pytest.raises(environment.SetupError) as exc:
        environment.download_weights(opener=FakeOpener(*[TimeoutError("timed out")] * 8), pause_s=0)
    assert str(exc.value).endswith("(8 attempts): TimeoutError: timed out") and not weights.exists()


def test_install_deepface_runs_the_missing_steps(tmp_path, monkeypatch, weights):
    env_dir = tmp_path / "env"
    monkeypatch.setenv("VA_DEEPFACE_ENV", str(env_dir))
    weights.unlink()
    ran = []

    def runner(cmd, timeout):
        ran.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            py = environment.deepface_python(env_dir)
            py.parent.mkdir(parents=True)
            py.write_bytes(b"")
        return 0, "ok"

    states = iter([{"ok": False, "problem": "modules"}, {"ok": True, "problem": None, "detail": "deepface 0.0.104 in x"}])
    monkeypatch.setattr(environment, "deepface_status", lambda env=None: next(states))
    r = environment.install_deepface(base_python="C:/Python/python.exe", runner=runner, opener=FakeOpener(WEIGHTS))
    py = str(environment.deepface_python(env_dir))
    check = [py, "-B", "-c", environment._DEEPFACE_CHECK]
    assert ran == [["C:/Python/python.exe", "-m", "venv", str(env_dir)],
                   [py, "-m", "pip", "install", "--disable-pip-version-check", "deepface==0.0.104"], check]
    assert r["env"] == env_dir.as_posix() and r["status"]["ok"] is True and weights.read_bytes() == WEIGHTS
    assert r["done"] == [f"create the environment {env_dir.as_posix()}", "install deepface 0.0.104",
                         "download the YuNet weights"]
    # everything in place: only the detection runs, and it always does
    monkeypatch.setattr(environment, "deepface_status", lambda env=None: {"ok": True, "problem": None})
    ran.clear()
    assert environment.install_deepface(runner=runner, opener=no_download)["done"] == [] and ran == [check]


def test_install_deepface_replaces_damaged_weights(tmp_path, monkeypatch, weights):
    """Damaged weights are removed and downloaded again; before, any file in place was enough."""
    monkeypatch.setenv("VA_DEEPFACE_ENV", str(tmp_path / "env"))
    weights.write_bytes(b"cortado")
    monkeypatch.setattr(environment, "deepface_status", lambda env=None: {"ok": False, "problem": "weights"})
    py = environment.deepface_python()
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    r = environment.install_deepface(runner=lambda cmd, timeout: (0, ""), opener=FakeOpener(WEIGHTS))
    assert r["done"] == ["remove the damaged YuNet weights", "download the YuNet weights"]
    assert weights.read_bytes() == WEIGHTS


def test_install_deepface_reinstalls_when_the_detection_fails(tmp_path, monkeypatch, weights):
    """Version and weights in place, but DeepFace does not load (a native library, half an interrupted
    install): before, the setup said "already in place" and repaired nothing. Now the detection fails,
    deepface is reinstalled with its dependencies, and the detection runs again."""
    monkeypatch.setenv("VA_DEEPFACE_ENV", str(tmp_path / "env"))
    monkeypatch.setattr(environment, "deepface_status", lambda env=None: {"ok": True, "problem": None, "detail": "x"})
    environment.deepface_python().parent.mkdir(parents=True)
    environment.deepface_python().write_bytes(b"")
    py = str(environment.deepface_python())
    check = [py, "-B", "-c", environment._DEEPFACE_CHECK]
    reinstall = [py, "-m", "pip", "install", "--disable-pip-version-check", "--force-reinstall", "deepface==0.0.104"]
    dll = "ImportError: DLL load failed while importing onnxruntime_pybind11_state"
    answers, ran = iter([(1, f"Traceback\n{dll}\n"), (0, "ok"), (0, "ok")]), []

    def runner(cmd, timeout):
        ran.append(cmd)
        return next(answers)

    r = environment.install_deepface(runner=runner, opener=no_download)
    assert ran == [check, reinstall, check]
    assert r["done"] == [f"reinstall deepface 0.0.104, since DeepFace did not run a detection ({dll})",
                         "run a detection with DeepFace after the reinstall"]
    answers = iter([(1, dll), (0, "ok"), (1, dll)])           # it still fails after the reinstall: the setup says so
    with pytest.raises(environment.SetupError) as exc:
        environment.install_deepface(runner=runner, opener=no_download)
    assert str(exc.value) == f"could not run a detection with DeepFace after the reinstall (exit 1): {dll}"


def test_install_deepface_failure_reports_the_end_of_the_output(tmp_path, monkeypatch, weights):
    monkeypatch.setenv("VA_DEEPFACE_ENV", str(tmp_path / "env"))
    py = environment.deepface_python()
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    monkeypatch.setattr(environment, "deepface_status", lambda env=None: {"ok": False, "problem": "modules"})
    with pytest.raises(environment.SetupError) as exc:
        environment.install_deepface(runner=lambda cmd, timeout: (1, "Collecting deepface\n\nERROR: No matching distribution"))
    assert str(exc.value) == ("could not install deepface 0.0.104 (exit 1): Collecting deepface | "
                              "ERROR: No matching distribution")
