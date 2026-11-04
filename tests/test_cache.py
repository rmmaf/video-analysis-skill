"""va.cache: keys, stage folders and stage.json validity, heartbeat locks (never os.kill), GC and
the per-video worker (stage functions simulated)."""
import dataclasses
import json
import os
import stat
import threading
import time
import unicodedata
from pathlib import Path

import pytest

from va import cache as K
from va.common import as_posix, read_json

from test_prepare_cli import STAGE_NAMES, fake_video, fake_vtt, load_fakes

NFD = lambda s: unicodedata.normalize("NFD", s)  # noqa: E731


@pytest.fixture(autouse=True)
def no_kill(monkeypatch):
    """Locks must never signal a process (on Windows os.kill calls TerminateProcess)."""
    def boom(*a, **k):
        raise AssertionError("os.kill must never be called")
    monkeypatch.setattr(os, "kill", boom)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    ns = load_fakes()
    for name in STAGE_NAMES:
        monkeypatch.setitem(K.STAGE_OVERRIDES, name, ns[name])
    monkeypatch.setenv("VA_FAKE_CALLS", str(tmp_path / "calls.txt"))
    return ns


# --- keys -----------------------------------------------------------------------------------------

def test_canonical_json_and_h8():
    assert K.canonical_json({"b": 1, "a": [1.5, "ç"]}) == '{"a":[1.5,"ç"],"b":1}'
    assert K.h8({"b": 1, "a": 2}) == K.h8({"a": 2, "b": 1}) and len(K.h8({})) == 8
    assert K.canonical_json({"p": Path("C:/x/y")}) == '{"p":"C:/x/y"}'
    with pytest.raises(ValueError):
        K.h8({"x": float("nan")})


def test_video_fingerprint_and_key(tmp_path):
    v = tmp_path / (NFD("Gravação de Reunião") + ".mp4")
    v.write_bytes(b"12345")
    fp = K.video_fingerprint(v)
    assert len(fp) == 8 and fp == K.video_fingerprint(str(v))
    nfc = tmp_path / unicodedata.normalize("NFC", v.name)
    assert K.video_fingerprint(as_posix(v)) == fp                    # separators do not matter
    assert K.video_key(v) == f"gravacao-de-reuniao-{fp}"
    st = os.stat(v)
    os.utime(v, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert K.video_fingerprint(v) != fp                              # edited -> new key
    v.write_bytes(b"123456")
    assert K.video_fingerprint(v) != fp
    assert str(nfc) != str(v)


def test_stage_keys(tmp_path):
    vtt = tmp_path / "a.vtt"
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    params = {"cues": {"offset_s": 0.0}, "frames": {"t_lo": 16}, "batches": {"max_frames": 20, "range": None}}
    k = K.stage_keys("v-12345678", vtt, params, {"title": "A"})
    assert set(k) == {"cues", "frames", "batches"} and all(len(x) == 8 for x in k.values())
    assert K.stage_keys("v-12345678", vtt, params, {"title": "A"}) == k
    k2 = K.stage_keys("v-12345678", vtt, {**params, "batches": {"max_frames": 10, "range": None}}, {"title": "A"})
    assert k2["cues"] == k["cues"] and k2["frames"] == k["frames"] and k2["batches"] != k["batches"]
    k3 = K.stage_keys("v-12345678", vtt, {**params, "batches": {"max_frames": 20, "range": [60.0, 120.0]}}, {"title": "A"})
    assert k3["batches"] != k["batches"]                              # trecho gets its own folder
    k4 = K.stage_keys("v-12345678", vtt, {**params, "frames": {"t_lo": 12}}, {"title": "A"})
    assert k4["frames"] != k["frames"] and k4["batches"] != k["batches"] and k4["cues"] == k["cues"]
    k5 = K.stage_keys("v-12345678", vtt, {**params, "frames": {"t_lo": 16, "sequential": True}}, {"title": "A"})
    assert k5 == k                                                    # execution-only flag
    vtt.write_text("WEBVTT\n\n00:01.000 --> 00:02.000\nx\n", encoding="utf-8")
    k6 = K.stage_keys("v-12345678", vtt, params, {"title": "A"})
    assert k6["cues"] != k["cues"] and k6["frames"] == k["frames"] and k6["batches"] != k["batches"]
    assert K.stage_keys("w-12345678", vtt, params, {"title": "A"})["frames"] != k6["frames"]


def test_stage_dir_and_force():
    assert K.stage_dir("C:/c/v", "frames", "abcd1234") == Path("C:/c/v/frames-abcd1234")
    with pytest.raises(ValueError):
        K.stage_dir("C:/c/v", "sheets", "x")
    assert K.expand_force(None) == set() and K.expand_force("all") == {"cues", "frames", "batches"}
    assert K.expand_force("cues") == {"cues", "batches"} and K.expand_force("frames") == {"frames", "batches"}
    assert K.expand_force("batches") == {"batches"}


# --- stage.json -------------------------------------------------------------------------------------

def _frames_stage(tmp_path, key="k1", n=3):
    d = tmp_path / f"frames-{key}"
    d.mkdir(parents=True)
    frames = []
    for i in range(1, n + 1):
        (d / f"F{i:04d}_000000.jpg").write_bytes(b"jpg")
        frames.append({"id": f"F{i:04d}", "file": f"F{i:04d}_000000.jpg"})
    frames.append({"id": f"F{n + 1:04d}", "file": "F0001_000000.jpg"})  # revisit reuses a file
    (d / "frames.json").write_text(json.dumps({"frames": frames}), encoding="utf-8")
    return d


def test_stage_valid_frames(tmp_path):
    d = _frames_stage(tmp_path)
    assert not K.stage_valid(d, "frames", "k1")                      # no stage.json yet
    doc = K.write_stage_json(d, "frames", "k1", params={"a": Path("C:/x")}, timing_s=1.5)
    assert doc["status"] == "complete" and read_json(d / "stage.json")["params"] == {"a": "C:/x"}
    assert K.stage_valid(d, "frames", "k1")
    assert not K.stage_valid(d, "frames", "k2") and not K.stage_valid(d, "cues", "k1")
    (d / "F0002_000000.jpg").unlink()
    assert not K.stage_valid(d, "frames", "k1")                      # every listed JPEG must exist
    assert not [p for p in d.iterdir() if p.name.endswith(".tmp")]


def test_stage_valid_cues_and_batches(tmp_path):
    c = tmp_path / "cues-k"
    c.mkdir()
    K.write_stage_json(c, "cues", "k")
    assert not K.stage_valid(c, "cues", "k")
    (c / "cues.json").write_text("{}", encoding="utf-8")
    assert K.stage_valid(c, "cues", "k")
    b = tmp_path / "batches-k"
    b.mkdir()
    for name in ("summary.json", "batches.json", "batch_001.md", "batch_002.md"):
        (b / name).write_text("x", encoding="utf-8")
    K.write_stage_json(b, "batches", "k", result={"n_batches": 2, "batch_images": [3, 2]})
    assert K.stage_valid(b, "batches", "k")
    (b / "batch_002.md").unlink()
    assert not K.stage_valid(b, "batches", "k")
    K.write_stage_json(b, "batches", "k", result={})
    assert not K.stage_valid(b, "batches", "k")
    st = read_json(c / "stage.json")
    st["status"] = "running"
    (c / "stage.json").write_text(json.dumps(st), encoding="utf-8")
    assert not K.stage_valid(c, "cues", "k")
    (c / "stage.json").write_text("{corrompido", encoding="utf-8")
    assert K.read_stage(c) is None and not K.stage_valid(c, "cues", "k")


def test_rmtree_robust_readonly(tmp_path):
    d = tmp_path / "x" / "y"
    d.mkdir(parents=True)
    f = d / "ro.jpg"
    f.write_bytes(b"1")
    os.chmod(f, stat.S_IREAD)
    assert K.rmtree_robust(tmp_path / "x") and not (tmp_path / "x").exists()
    assert K.rmtree_robust(tmp_path / "nada")


def test_touch_last_used_and_dir_size(tmp_path):
    K.touch_last_used(tmp_path)
    assert (tmp_path / "last_used").read_text(encoding="utf-8").strip()
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    assert K.dir_size(tmp_path) >= 100


def test_plain_and_info_get():
    @dataclasses.dataclass(frozen=True)
    class Info:
        path: Path
        duration_s: float
        fps_avg: float = float("nan")
    info = Info(Path("C:/v.mp4"), 12.5)
    assert K.plain(info) == {"path": "C:/v.mp4", "duration_s": 12.5, "fps_avg": None}
    assert K.plain({"t": (1, 2), "s": {3}}) == {"t": [1, 2], "s": [3]}
    assert K.info_get(info, "duration_s") == 12.5 and K.info_get({"duration": 3}, "duration_s", "duration") == 3
    assert K.info_get({}, "x", default=7) == 7


# --- stage functions ------------------------------------------------------------------------------------

def test_stage_fn_overrides_shim_and_real(monkeypatch, tmp_path):
    shim = tmp_path / "shim.py"
    shim.write_text("def probe_video(p):\n    return {'shim': True}\n", encoding="utf-8")
    monkeypatch.setenv(K.SHIM_ENV, str(shim))
    assert K.stage_fn("probe_video")("x") == {"shim": True}
    monkeypatch.setitem(K.STAGE_OVERRIDES, "probe_video", lambda p: {"override": True})
    assert K.stage_fn("probe_video")("x") == {"override": True}
    monkeypatch.delenv(K.SHIM_ENV)
    assert K.stage_fn("effective_batch_params").__module__ == "va.batching"   # the real module, lazily imported
    with pytest.raises(KeyError):
        K.stage_fn("nao_existe")


# --- lock -----------------------------------------------------------------------------------------------

def test_lock_acquire_release(tmp_path):
    p = tmp_path / ".lock"
    with K.VideoLock(p) as lk:
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert doc["pid"] == os.getpid() and doc["token"] == lk.token
    assert not p.exists()


def test_lock_second_waits_then_times_out(tmp_path):
    p = tmp_path / ".lock"
    msgs = []
    with K.VideoLock(p):
        t0 = time.monotonic()
        with pytest.raises(K.LockTimeout) as exc:
            K.VideoLock(p, wait_s=0.3, poll_s=0.05, on_wait=msgs.append).acquire()
        assert time.monotonic() - t0 >= 0.3 and "busy" in str(exc.value)
        assert msgs and "waiting for the lock" in msgs[0]


def test_lock_second_gets_it_after_release(tmp_path):
    p = tmp_path / ".lock"
    first = K.VideoLock(p).acquire()
    got = []

    def second():
        with K.VideoLock(p, wait_s=5, poll_s=0.05) as lk:
            got.append(lk.waited_s)

    t = threading.Thread(target=second)
    t.start()
    time.sleep(0.3)
    assert not got                       # still waiting while the first holder keeps the lock
    first.release()
    t.join(5)
    assert got and not p.exists()        # acquired after the release, then released in turn


def test_lock_stale_is_broken_live_is_not(tmp_path):
    p = tmp_path / ".lock"
    p.write_text(json.dumps({"token": "x", "pid": 999999, "host": "h", "created": "antes"}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(p, (old, old))
    lk = K.VideoLock(p, stale_s=600, wait_s=1, poll_s=0.05).acquire()
    assert lk.broke_stale and json.loads(p.read_text(encoding="utf-8"))["token"] == lk.token
    lk.release()
    p.write_text(json.dumps({"token": "y"}), encoding="utf-8")         # fresh: not stale
    with pytest.raises(K.LockTimeout):
        K.VideoLock(p, stale_s=600, wait_s=0.2, poll_s=0.05).acquire()
    assert json.loads(p.read_text(encoding="utf-8"))["token"] == "y"  # someone else's lock is left alone
    assert not list(tmp_path.glob(".lock.stale-*"))


def test_lock_heartbeat_refreshes_mtime(tmp_path):
    p = tmp_path / ".lock"
    with K.VideoLock(p, heartbeat_s=0.05):
        old = time.time() - 3600
        os.utime(p, (old, old))
        deadline = time.time() + 3
        while time.time() < deadline and time.time() - p.stat().st_mtime > 60:
            time.sleep(0.02)
        assert time.time() - p.stat().st_mtime < 60


def test_lock_release_keeps_foreign_lock(tmp_path):
    p = tmp_path / ".lock"
    lk = K.VideoLock(p).acquire()
    p.write_text(json.dumps({"token": "outro"}), encoding="utf-8")
    lk.release()
    assert p.exists()


# --- GC ----------------------------------------------------------------------------------------------

def test_gc_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    now = time.time()
    for name, age_days, locked in (("velho-11111111", 40, False), ("novo-22222222", 2, False),
                                   ("velho-travado-33333333", 40, True), ("sem-last-used-44444444", None, False)):
        d = cache_dir / name / "frames-k"
        d.mkdir(parents=True)
        (d / "F0001.jpg").write_bytes(b"x" * 1000)
        if age_days is not None:
            K.touch_last_used(d.parent)
            t = now - age_days * 86400
            os.utime(d.parent / "last_used", (t, t))
        if locked:
            (d.parent / ".lock").write_text("{}", encoding="utf-8")
    res = K.gc_cache(cache_dir, 30, now=now)
    assert [r["key"] for r in res["removed"]] == ["velho-11111111"]
    assert res["freed_bytes"] >= 1000 and res["kept"] == 2
    assert [s["key"] for s in res["skipped"]] == ["velho-travado-33333333"]
    assert not (cache_dir / "velho-11111111").exists() and (cache_dir / "novo-22222222").exists()
    assert K.gc_cache(tmp_path / "nao-existe", 30)["removed"] == []


# --- worker ---------------------------------------------------------------------------------------------

def _job(tmp_path, fakes, **over):
    video = fake_video(tmp_path / "v" / "aula.mp4", n_frames=25)
    vtt = fake_vtt(tmp_path / "v" / "aula.vtt")
    params = {"cues": fakes["effective_cue_params"]({}), "frames": fakes["effective_frame_params"]({}),
              "batches": fakes["effective_batch_params"]({})}
    vkey = K.video_key(video)
    meta = {"title": "aula", "video_path": as_posix(video), "vtt_path": as_posix(vtt)}
    job = {"vid": "v1", "slug": "aula", "title": "aula", "video_path": str(video), "vtt_path": str(vtt),
           "video_key": vkey, "video_dir": str(tmp_path / "cache" / vkey), "keys": K.stage_keys(vkey, vtt, params, meta),
           "params": params, "info": None, "force": [], "meta": meta, "quiet": True,
           "log_path": str(tmp_path / "log.txt"), "progress_path": str(tmp_path / "progress.json")}
    job.update(over)
    return job


def _calls(tmp_path):
    p = tmp_path / "calls.txt"
    out = p.read_text(encoding="utf-8").split()[0::3] if p.exists() else []
    if p.exists():
        p.unlink()
    return out


def test_prepare_video_builds_then_hits(tmp_path, fakes):
    job = _job(tmp_path, fakes)
    r = K.prepare_video(job)
    assert r["ok"], r["error"]
    assert r["cache_hit"] == {"cues": False, "frames": False, "batches": False}
    assert r["batches"] == {"n_batches": 2, "batch_images": [20, 5], "batch_frames": [20, 5], "n_frames": 25}
    assert r["summary"]["n_frames"] == 25 and r["info"]["duration_s"] == 300.0
    vdir = Path(job["video_dir"])
    assert (vdir / "last_used").exists() and (vdir / "probe.json").exists() and not (vdir / ".lock").exists()
    assert read_json(vdir / "source.json")["video_key"] == job["video_key"]
    for s in K.STAGES:
        assert K.stage_valid(vdir / f"{s}-{job['keys'][s]}", s, job["keys"][s])
    st = read_json(vdir / f"frames-{job['keys']['frames']}" / "stage.json")
    assert st["result"]["n_frames"] == 25 and st["video_key"] == job["video_key"]
    assert read_json(job["progress_path"])["state"] == "ready"
    assert _calls(tmp_path) == ["cues", "frames", "batches"]
    r2 = K.prepare_video(job)
    assert r2["ok"] and r2["cache_hit"] == {"cues": True, "frames": True, "batches": True} and _calls(tmp_path) == []
    assert r2["batches"] == r["batches"] and r2["timing_s"] == {}


def test_prepare_video_force_and_invalid_upstream(tmp_path, fakes):
    job = _job(tmp_path, fakes)
    K.prepare_video(job)
    _calls(tmp_path)
    assert K.prepare_video({**job, "force": sorted(K.expand_force("cues"))})["ok"]
    assert _calls(tmp_path) == ["cues", "batches"]
    stage = Path(job["video_dir"]) / f"cues-{job['keys']['cues']}" / "stage.json"
    stage.unlink()                                       # an interrupted cues stage: rebuilt, and the batches too
    r = K.prepare_video(job)
    assert r["cache_hit"] == {"cues": False, "frames": True, "batches": False} and _calls(tmp_path) == ["cues", "batches"]
    leftover = Path(job["video_dir"]) / "frames-deadbeef"
    leftover.mkdir()
    (leftover / "lixo.jpg").write_bytes(b"x")
    K.prepare_video({**job, "keys": {**job["keys"], "frames": "deadbeef"}})
    assert not (leftover / "lixo.jpg").exists() and (leftover / "stage.json").exists()


def test_prepare_video_failures(tmp_path, fakes):
    bad = _job(tmp_path, fakes)
    Path(bad["video_path"]).write_text(json.dumps({"fail": "frames"}), encoding="utf-8")
    r = K.prepare_video(bad)
    assert not r["ok"] and r["error"]["code"] == "video_failed" and "frames" in r["error"]["detail"]
    assert "Traceback" in Path(bad["log_path"]).read_text(encoding="utf-8")
    assert not (Path(bad["video_dir"]) / ".lock").exists()
    missing = _job(tmp_path / "m", fakes)
    Path(missing["video_path"]).unlink()
    r = K.prepare_video(missing)
    assert not r["ok"] and "video missing" in r["error"]["detail"]
    changed = _job(tmp_path / "c", fakes, expected_fp="00000000")
    r = K.prepare_video(changed)
    assert not r["ok"] and "changed" in r["error"]["detail"]


def test_prepare_video_lock_timeout(tmp_path, fakes):
    job = _job(tmp_path, fakes, lock={"wait_s": 0.2, "poll_s": 0.05})
    Path(job["video_dir"]).mkdir(parents=True)
    with K.VideoLock(Path(job["video_dir"]) / ".lock"):
        r = K.prepare_video(job)
    assert not r["ok"] and r["error"]["code"] == "video_failed" and "another prepare.py" in r["error"]["detail"]


def test_prepare_video_interrupt_releases_lock(tmp_path, fakes):
    job = _job(tmp_path, fakes)
    Path(job["video_path"]).write_text(json.dumps({"interrupt": True}), encoding="utf-8")
    with pytest.raises(KeyboardInterrupt):
        K.prepare_video(job)
    assert not (Path(job["video_dir"]) / ".lock").exists()


def test_prepare_video_stage_unavailable(tmp_path, fakes, monkeypatch):
    job = _job(tmp_path, fakes)

    def broken(name):
        raise K.StageUnavailable("va.render could not be imported (test)")
    monkeypatch.delitem(K.STAGE_OVERRIDES, "run_batches_stage")
    monkeypatch.setattr(K, "stage_fn", lambda name: broken(name) if name == "run_batches_stage" else fakes[name])
    r = K.prepare_video(job)
    assert not r["ok"] and r["error"]["code"] == "incomplete_install"


def test_prepare_video_without_n_images_falls_back(tmp_path, fakes, monkeypatch):
    real = fakes["run_batches_stage"]

    def no_images(frames, cues, params, out_dir, meta):
        res = real(frames, cues, params, out_dir, meta)
        for b in res["batches"]["batches"]:
            del b["n_images"]
        return res
    monkeypatch.setitem(K.STAGE_OVERRIDES, "run_batches_stage", no_images)
    r = K.prepare_video(_job(tmp_path, fakes))
    assert r["ok"] and r["batches"]["batch_images"] == [20, 5] and any("n_images" in w for w in r["warnings"])


def test_prepare_video_writes_missing_summary(tmp_path, fakes, monkeypatch):
    real = fakes["run_batches_stage"]

    def no_files(frames, cues, params, out_dir, meta):
        res = real(frames, cues, params, out_dir, meta)
        (Path(out_dir) / "summary.json").unlink()
        (Path(out_dir) / "batches.json").unlink()
        return res
    monkeypatch.setitem(K.STAGE_OVERRIDES, "run_batches_stage", no_files)
    job = _job(tmp_path, fakes)
    r = K.prepare_video(job)
    bdir = Path(job["video_dir"]) / f"batches-{job['keys']['batches']}"
    assert r["ok"] and (bdir / "summary.json").exists() and (bdir / "batches.json").exists()
    assert read_json(bdir / "summary.json")["paths"]["batches_dir"] == as_posix(bdir)


# --- warm path: a fully cached video needs no ffprobe and no tool check (integration) --------------------

def test_stage_hits_force_and_upstream_rule(tmp_path, fakes):
    job = _job(tmp_path, fakes)
    vdir, keys = Path(job["video_dir"]), job["keys"]
    assert K.stage_hits(vdir, keys) == {"cues": False, "frames": False, "batches": False}
    assert K.prepare_video(job)["ok"]
    assert K.stage_hits(vdir, keys) == {"cues": True, "frames": True, "batches": True}
    assert K.stage_hits(vdir, keys, K.expand_force("batches")) == {"cues": True, "frames": True, "batches": False}
    assert K.stage_hits(vdir, keys, {"frames"}) == {"cues": True, "frames": False, "batches": False}  # upstream rule
    (vdir / f"cues-{keys['cues']}" / "stage.json").unlink()
    assert K.stage_hits(vdir, keys) == {"cues": False, "frames": True, "batches": False}


def test_cached_probe(tmp_path, fakes):
    job = _job(tmp_path, fakes)
    vdir = Path(job["video_dir"])
    assert K.cached_probe(vdir, job["video_path"]) is None             # nothing cached yet
    assert K.prepare_video(job)["ok"]
    doc = K.cached_probe(vdir, job["video_path"])
    assert doc == read_json(vdir / "probe.json") and doc["duration_s"] == 300.0
    assert K.cached_probe(vdir, job["video_path"].upper()) == doc      # compared with NFC.casefold (Windows)
    assert K.cached_probe(vdir, tmp_path / "outro.mp4") is None         # probe.json of another file
    for bad in ('{"path": "x"}', "[1, 2]", "{nao json", json.dumps({**doc, "duration_s": 0})):
        (vdir / "probe.json").write_text(bad, encoding="utf-8")
        assert K.cached_probe(vdir, job["video_path"]) is None


def test_prepare_video_reprobes_a_cached_info_before_building(tmp_path, fakes, monkeypatch):
    """prepare.py hands the cached probe.json dict only for a fully cached video. If a stage turns
    out to be missing under the lock (deleted meanwhile), the worker probes again instead of giving
    the plain dict to the frame engine."""
    job = _job(tmp_path, fakes)
    assert K.prepare_video(job)["ok"]
    _calls(tmp_path)
    probes = []
    real = fakes["probe_video"]
    monkeypatch.setitem(K.STAGE_OVERRIDES, "probe_video", lambda p: probes.append(p) or real(p))
    cached = K.cached_probe(job["video_dir"], job["video_path"])
    r = K.prepare_video({**job, "info": cached, "info_from_cache": True})
    assert r["ok"] and probes == [] and r["cache_hit"] == {"cues": True, "frames": True, "batches": True}
    assert r["info"] == cached
    (Path(job["video_dir"]) / f"frames-{job['keys']['frames']}" / "stage.json").unlink()
    r = K.prepare_video({**job, "info": cached, "info_from_cache": True})
    assert r["ok"] and len(probes) == 1 and _calls(tmp_path) == ["frames", "batches"]
