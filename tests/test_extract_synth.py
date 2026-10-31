"""Frame engine on synthetic videos with ground truth (design-deterministico.md section 9).

Every acceptance row of the section 9 table, the CRF 32 / -bf 2 / VFR variants, the
frames.json contract, files and revisits, range, the re-encode safety net, static ignore
regions, progress, errors, and determinism (two runs and lockstep vs sequential must be
byte-identical). Videos are rendered once by tests/synth.py and cached in %TEMP%.
"""
import hashlib
import math
import shutil
from pathlib import Path

import pytest
from PIL import Image

import synth
from va.contracts import check_frames
from va.dedup import effective_frame_params, frame_params_for_output
from va.extract import analysis_size, output_size, run_frames_stage
from va.probe import ProbeError, probe_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="needs ffmpeg and ffprobe on PATH")

VARIANTS = [(name, 28, 0, False) for name in synth.SCENARIOS] + [
    ("static_text", 32, 0, False),     # zero false changes on static content at CRF 32
    ("typing", 32, 0, False),
    ("slides_cam", 28, 0, True),       # VFR variant: same times
    ("meeting_layouts", 28, 2, False), # -bf 2: exercises -skip_frame:v noref
]


@pytest.fixture(scope="session")
def rendered():
    cache: dict = {}

    def get(name, crf=28, bf=0, vfr=False):
        key = (name, crf, bf, vfr)
        if key not in cache:
            cache[key] = synth.render(name, crf=crf, bf=bf, vfr=vfr)
        return cache[key]

    return get


@pytest.fixture(scope="session")
def extracted(rendered, tmp_path_factory):
    cache: dict = {}

    def get(name, crf=28, bf=0, vfr=False):
        key = (name, crf, bf, vfr)
        if key not in cache:
            mp4, truth = rendered(name, crf, bf, vfr)
            info = probe_video(mp4)
            out = tmp_path_factory.mktemp(synth.variant_name(name, crf, bf, vfr)) / "frames-synth"
            doc = run_frames_stage(mp4, info, {}, out, video_key="synth-0000")
            cache[key] = (doc, truth, info, out)
        return cache[key]

    return get


def _digest(folder: Path) -> dict:
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(folder.iterdir()) if f.suffix in (".jpg", ".json", ".csv")}


# --- acceptance table ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: synth.variant_name(*v))
def test_acceptance(variant, extracted):
    doc, truth, info, out = extracted(*variant)
    ok, msgs = synth.evaluate(doc, truth)
    kept = [(f["captured_at"], f["kind"], f["same_as"]) for f in doc["frames"]]
    assert ok, f"{msgs}; kept {kept}"
    assert check_frames(doc) == []


@pytest.mark.parametrize("crf", [28, 32])
def test_typing_last_frame_has_the_whole_text(crf, extracted):
    doc, truth, info, out = extracted("typing", crf)
    assert synth.final_text_diff(doc, truth) < 3.0


def test_meeting_mask_does_not_leak_into_the_next_layout(extracted):
    doc, truth, info, out = extracted("meeting_layouts")
    later = [f for f in doc["frames"] if f["captured_at"] >= 80.0]
    assert [round(f["start"]) for f in later] == [80, 90, 105]
    for f in later[1:]:                       # the changes inside the former webcam tile area
        x, y, w, h = f["change"]["bbox"]
        assert x >= 0.75 and y < 0.25
        assert f["hot_frac"] == 0.0
    assert all(seg["end"] <= 80.0 for seg in doc["hot_segments"])


def test_vfr_and_bframe_variants(extracted):
    doc, truth, info, out = extracted("slides_cam", 28, 0, True)
    assert info.vfr is True
    assert abs(doc["n_samples"] - math.floor(2 * truth["duration"])) <= 1
    doc2, _t, info2, _o = extracted("meeting_layouts", 28, 2, False)
    assert info2.has_b_frames is True
    base, *_ = extracted("meeting_layouts")
    assert [f["start"] for f in doc2["frames"]] == [f["start"] for f in base["frames"]]


def test_camera_heartbeat_and_warning(extracted):
    doc, truth, info, out = extracted("camera")
    assert [f["kind"] for f in doc["frames"]][1:] == ["heartbeat"] * (len(doc["frames"]) - 1)
    assert any(w.startswith("trecho com cara de câmera") for w in doc["warnings"])
    assert doc["hot_segments"] and doc["hot_segments"][0]["frac"] >= 0.5


# --- frames.json, files, geometry ---------------------------------------------------------------

def test_frames_json_files_and_revisits(extracted):
    doc, truth, info, out = extracted("transient")
    assert doc["analysis_size"] == [640, 360] and doc["output_size"] == [1280, 720]
    assert doc["params"] == frame_params_for_output(effective_frame_params({}))
    assert doc["video"]["key"] == "synth-0000" and doc["frames_dir"] == out.as_posix()
    files = {f["file"] for f in doc["frames"]}
    on_disk = {p.name for p in out.glob("*.jpg")}
    assert on_disk == files                        # no stray spool files
    by_id = {f["id"]: f for f in doc["frames"]}
    for f in doc["frames"]:
        assert f["tokens"] == math.ceil(1280 / 28) * math.ceil(720 / 28)
        assert (out / f["file"]).stat().st_size == f["bytes"]
        if f["same_as"]:
            assert f["file"] == by_id[f["same_as"]]["file"]
    assert [f["same_as"] for f in doc["frames"]] == [None, None, "F0001"]   # the menu closes
    with Image.open(out / doc["frames"][1]["file"]) as im:
        assert im.size == (1280, 720) and im.format == "JPEG"
    rows = (out / "samples.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("k,t,e,cut,local,small,hot_frac,act_frac,p_cls")
    assert len(rows) == doc["n_samples"] + 1
    assert not (out / "_spool").exists() and (out / "ffmpeg_a.log").exists()


def test_geometry_helpers():
    from va.probe import VideoInfo
    hd = VideoInfo(path=Path("x.mp4"), duration_s=10, width=1920, height=1080, fps_avg=16, vfr=False, codec="h264",
                   has_b_frames=False, stream_index=0, rotation=0)
    retina = VideoInfo(path=Path("y.mov"), duration_s=10, width=3024, height=1964, fps_avg=60, vfr=True, codec="h264",
                       has_b_frames=True, stream_index=0, rotation=0)
    assert analysis_size(hd) == (640, 360) and output_size(hd) == (1568, 882)
    assert analysis_size(retina) == (552, 360) and output_size(retina) == (1568, 1018)
    assert output_size(hd, {"max_edge": 1000}) == (1000, 562)


def test_range_is_respected(rendered, tmp_path):
    mp4, truth = rendered("slides_cam")
    info = probe_video(mp4)
    doc = run_frames_stage(mp4, info, {"range": [15, 50]}, tmp_path / "frames-range")
    assert doc["range"] == [15.0, 50.0] and doc["frames"][0]["start"] == 15.0 and doc["frames"][-1]["end"] == 50.0
    assert check_frames(doc) == []
    assert abs(doc["n_samples"] - 70) <= 1
    starts = [round(f["start"]) for f in doc["frames"]]
    assert starts == [15, 20, 30, 45]


def test_reencode_safety_net(rendered, tmp_path):
    mp4, truth = rendered("small_changes")
    info = probe_video(mp4)
    doc = run_frames_stage(mp4, info, {"jpeg_reencode_above": 2000}, tmp_path / "frames-reenc")
    assert all(f.get("reencoded") for f in doc["frames"])
    for f in doc["frames"]:
        with Image.open(tmp_path / "frames-reenc" / f["file"]) as im:
            im.load()
        assert (tmp_path / "frames-reenc" / f["file"]).stat().st_size == f["bytes"]


def test_static_ignore_region_with_hot_mask_off(rendered, tmp_path):
    mp4, truth = rendered("transient")
    info = probe_video(mp4)
    clock = [0.86, 0.03, 0.1, 0.06]                 # the per-second clock in the title bar
    doc = run_frames_stage(mp4, info, {"hot_mode": "off", "ignore_regions": [clock]}, tmp_path / "frames-ign")
    ok, msgs = synth.evaluate(doc, truth)
    assert ok, msgs
    assert doc["params"]["ignore_regions"] == [clock] and doc["hot_segments"] == []


def test_progress_callback(rendered, tmp_path):
    mp4, truth = rendered("cursor_only")
    info = probe_video(mp4)
    seen = []
    run_frames_stage(mp4, info, {}, tmp_path / "frames-prog", progress=seen.append)
    assert seen and all(set(d) == {"stage", "done_s", "total_s", "frames"} and d["stage"] == "frames" for d in seen)
    assert seen[-1]["done_s"] == pytest.approx(seen[-1]["total_s"], abs=0.6)


def test_probe_synthetic(rendered):
    mp4, truth = rendered("cursor_only")
    info = probe_video(mp4)
    assert (info.width, info.height, info.codec, info.stream_index, info.rotation) == (1280, 720, "h264", 0, 0)
    assert info.fps_avg == 30.0 and info.vfr is False and abs(info.duration_s - 20.0) < 0.1
    assert type(info).from_dict(info.to_dict()) == info


def test_errors(tmp_path, rendered):
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_text("hello", encoding="utf-8")
    with pytest.raises(ProbeError):
        probe_video(bogus)
    mp4, truth = rendered("cursor_only")
    info = probe_video(mp4)
    with pytest.raises(ValueError):
        run_frames_stage(mp4, info, {"range": [30, 40]}, tmp_path / "frames-err")
    with pytest.raises(ValueError):
        run_frames_stage(mp4, info, {"bogus_param": 1}, tmp_path / "frames-err2")


# --- determinism ------------------------------------------------------------------------------

def test_determinism_two_runs_and_lockstep_vs_sequential(rendered, tmp_path):
    mp4, truth = rendered("meeting_layouts")
    info = probe_video(mp4)
    out = tmp_path / "frames-det"
    digests = []
    for params in ({}, {}, {"sequential": True}):
        if out.exists():
            shutil.rmtree(out)
        run_frames_stage(mp4, info, params, out, video_key="det")
        digests.append(_digest(out))
    assert digests[0] == digests[1]                 # frames.json, samples.csv, every JPEG
    assert digests[0] == digests[2]                 # lockstep (one shared decode) == two passes
    assert len([k for k in digests[0] if k.endswith(".jpg")]) == 7
