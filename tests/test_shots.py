"""va/shots.py: the screenshots for the agent that counts who speaks when the person does not know (the form's
"Não sei", reference/speaker-count.md). Instants and sizes are pure; the shots themselves come from the real
ffmpeg, on a short test pattern made here."""
import shutil
import subprocess

import pytest

from va import SHOTS_DEFAULTS, cache, shots as S

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_shot_times_are_the_middles_of_equal_parts():
    t = S.shot_times(544.512, 12)
    assert len(t) == 12 and t[0] == 22.688 and t[-1] == pytest.approx(521.824)
    assert all(round(b - a, 3) == 45.376 for a, b in zip(t, t[1:]))      # never the first or the last frame
    assert S.shot_times(0.0, 12) == [0.0] and S.shot_times(10.0, 0) == [0.0]
    assert S.shot_times(4.0, 4) == [0.5, 1.5, 2.5, 3.5]


@pytest.mark.parametrize("w, h, size", [
    (1920, 1080, (1568, 882)),       # as the frames
    (1080, 1920, (882, 1568)),       # portrait: the longer side is the height
    (3024, 1964, (1568, 1018)),
    (640, 360, (640, 360)),          # never enlarged
    (641, 361, (642, 362)),          # even sides
])
def test_shot_size(w, h, size):
    assert S.shot_size(w, h, SHOTS_DEFAULTS["max_edge"]) == size


def _pattern(path, seconds=4, size="320x240"):
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc=duration={seconds}:size={size}:rate=10", "-pix_fmt", "yuv420p", "-y", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_make_shots_takes_reuses_and_repairs(tmp_path):
    video = _pattern(tmp_path / "padrão de teste.mp4")
    vdir = tmp_path / "cache" / "padrao-1234abcd"
    info = {"duration_s": 4.0, "width": 320, "height": 240, "stream_index": 0}
    first = S.make_shots(video=video, video_dir=vdir, video_key="padrao-1234abcd", info=info, params={"n": 4})
    assert first["cached"] is False and first["times"] == [0.5, 1.5, 2.5, 3.5] and first["size"] == [320, 240]
    names = [p.rsplit("/", 1)[1] for p in first["paths"]]
    assert names == ["shot_01_000000.jpg", "shot_02_000001.jpg", "shot_03_000002.jpg", "shot_04_000003.jpg"]
    for p in first["paths"]:
        with open(p, "rb") as fh:
            assert fh.read(2) == b"\xff\xd8"
    doc = cache.read_stage(first["dir"])
    assert doc["stage"] == "shots" and doc["status"] == "complete" and doc["failed"] == []
    # the same call: from the cache, nothing redone
    again = S.make_shots(video=video, video_dir=vdir, video_key="padrao-1234abcd", info=info, params={"n": 4})
    assert again["cached"] is True and again["paths"] == first["paths"]
    # a screenshot gone: the folder is redone (seconds)
    (vdir / f"shots-{S.shots_key('padrao-1234abcd', {'n': 4})}" / names[2]).unlink()
    redone = S.make_shots(video=video, video_dir=vdir, video_key="padrao-1234abcd", info=info, params={"n": 4})
    assert redone["cached"] is False and redone["paths"] == first["paths"]
    # another count of shots: another folder
    other = S.make_shots(video=video, video_dir=vdir, video_key="padrao-1234abcd", info=info, params={"n": 2})
    assert other["dir"] != first["dir"] and len(other["paths"]) == 2


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs ffmpeg")
def test_make_shots_fails_without_a_single_screenshot(tmp_path):
    bad = tmp_path / "não é vídeo.mp4"
    bad.write_bytes(b"not a video at all")
    with pytest.raises(S.ShotsError, match="no screenshot"):
        S.make_shots(video=bad, video_dir=tmp_path / "cache" / "x", video_key="x",
                     info={"duration_s": 10.0, "width": 320, "height": 240}, params={"n": 2})
    assert not (tmp_path / "cache" / "x" / f"shots-{S.shots_key('x', {'n': 2})}" / "stage.json").exists()
