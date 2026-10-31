"""va.sheets: contact sheets and the per-batch overview (spec section 6), on tiny Pillow JPEGs."""
import pytest
from PIL import Image

from va.batching import effective_batch_params
from va.sheets import load_font, make_contact_sheet, make_overview, sample_evenly, sheet_layout, short_time

P = effective_batch_params()
ASPECT = (1568, 882)


def jpgs(folder, colors):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, color in enumerate(colors):
        path = folder / f"F{i + 1:04d}_000000.jpg"
        Image.new("RGB", (160, 90), color).save(path, quality=90)
        paths.append(path.as_posix())
    return paths


@pytest.mark.parametrize("n, cols, tile, size", [
    (1, 5, (315, 177), (323, 205)),
    (4, 5, (315, 177), (1280, 205)),
    (20, 5, (315, 177), (1599, 808)),
    (21, 6, (262, 147), (1600, 688)),
    (30, 6, (262, 147), (1600, 859)),
    (31, 8, (195, 110), (1596, 540)),
    (40, 8, (195, 110), (1596, 674)),
])
def test_layout(n, cols, tile, size):
    lay = sheet_layout(n, ASPECT, P)
    assert lay["cols"] == cols and (lay["tile_w"], lay["tile_h"]) == tile and (lay["width"], lay["height"]) == size
    assert lay["width"] <= P["sheet_width"]
    assert lay["tokens"] == -(-size[0] // 28) * -(-size[1] // 28)


def test_layout_follows_the_aspect():
    assert sheet_layout(20, (1568, 1018), P)["tile_h"] == 205  # Retina .mov output
    assert sheet_layout(5, None, P)["tile_h"] == 177  # unknown aspect -> 16:9


def test_contact_sheet_is_deterministic_and_labelled(tmp_path):
    colors = [(200, 40, 40), (40, 200, 40), (40, 40, 200)]
    paths = jpgs(tmp_path / "frames", colors)
    tiles = [{"path": p, "label": f"F{i + 1:04d} 00:0{i}.5" + (" M" if i == 1 else ""), "id": f"F{i + 1:04d}"}
             for i, p in enumerate(paths)]
    a = make_contact_sheet(tiles, tmp_path / "a" / "sheet_001.jpg", ASPECT, P)
    b = make_contact_sheet(tiles, tmp_path / "b" / "sheet_001.jpg", ASPECT, P)
    data = (tmp_path / "a" / "sheet_001.jpg").read_bytes()
    assert data == (tmp_path / "b" / "sheet_001.jpg").read_bytes()
    assert a["size"] == b["size"] == [3 * 315 + 4 * 4, 177 + 20 + 2 * 4] and a["missing"] == [] and a["n_tiles"] == 3
    assert a["path"].endswith("/a/sheet_001.jpg") and "\\" not in a["path"]
    with Image.open(tmp_path / "a" / "sheet_001.jpg") as im:
        assert im.format == "JPEG" and im.mode == "RGB" and list(im.size) == a["size"] and "exif" not in im.info
        rgb = im.convert("RGB")
        for i, color in enumerate(colors):  # tile centers keep their colors
            px = rgb.getpixel((4 + i * (315 + 4) + 157, 4 + 88))
            assert all(abs(px[k] - color[k]) < 30 for k in range(3)), (i, px)
        band = [rgb.getpixel((4 + x, 4 + 177 + y)) for x in range(0, 315, 3) for y in range(20)]
        assert sum(1 for p in band if max(p) > 150) > 20  # white label text in the black band
        assert sum(1 for p in band if max(p) < 60) > len(band) // 2


def test_missing_image_and_font_fallback(tmp_path):
    params = {**P, "sheet_font": "C:/nao/existe.ttf"}
    paths = jpgs(tmp_path / "frames", [(10, 10, 10)])
    tiles = [{"path": paths[0], "label": "F0001 00:00.5", "id": "F0001"},
             {"path": (tmp_path / "sumiu.jpg").as_posix(), "label": "F0002 00:01.0", "id": "F0002"}]
    out = make_contact_sheet(tiles, tmp_path / "sheet_001.jpg", ASPECT, params)
    assert out["missing"] == ["F0002"]
    with Image.open(tmp_path / "sheet_001.jpg") as im:
        px = im.convert("RGB").getpixel((4 + 315 + 4 + 157, 4 + 88))
        assert all(80 <= c <= 112 for c in px)  # gray placeholder
    assert load_font("C:/nao/existe.ttf", 14) is not None
    with pytest.raises(ValueError):
        make_contact_sheet([], tmp_path / "x.jpg", ASPECT, P)


def test_long_labels_are_cut_to_the_tile(tmp_path):
    paths = jpgs(tmp_path / "frames", [(10, 10, 10)] * 31)
    tiles = [{"path": p, "label": "F0001 1:02:03.5 M =F0012 " + "x" * 80, "id": "F0001"} for p in paths]
    out = make_contact_sheet(tiles, tmp_path / "sheet.jpg", ASPECT, P)
    assert out["size"][0] <= 1600


def test_sample_evenly():
    assert sample_evenly(5, 40) == [0, 1, 2, 3, 4]
    idx = sample_evenly(100, 40)
    assert len(idx) == 40 and idx[0] == 0 and idx[-1] == 99 and idx == sorted(set(idx))
    assert sample_evenly(41, 40)[-1] == 40 and sample_evenly(10, 1) == [0]


def test_overview_samples_above_40_batches(tmp_path):
    paths = jpgs(tmp_path / "frames", [(200, 40, 40), (40, 40, 200)])
    tiles = [{"path": paths[k % 2], "label": f"B{k + 1:03d} · 00:{k // 60:02d}:{k % 60:02d}", "id": f"B{k + 1:03d}"}
             for k in range(100)]
    out = make_overview(tiles, tmp_path / "overview.jpg", ASPECT, P)
    assert out["n_tiles"] == 40 and out["size"] == [1596, 674] and out["size"][0] <= 1600
    small = make_overview(tiles[:3], tmp_path / "overview3.jpg", ASPECT, P)
    assert small["n_tiles"] == 3 and small["size"] == [3 * 315 + 16, 205]


def test_short_time():
    assert short_time(174_500) == "02:54.5"
    assert short_time(0) == "00:00.0"
    assert short_time(3_723_500) == "1:02:03.5"
    assert short_time(36_000_000) == "10:00:00.0"
