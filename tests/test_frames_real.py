"""Frame engine on the real fixtures of tests/fixtures.json (design-deterministic.md section 9,
plan.md Verification 3 and 4). Run with --run-real (or VA_RUN_REAL=1).

- Repasse (da_1608): 50 <= frames <= 128 and recall >= 90 % of the significant September
  baseline frames within +-2 s; writes <workspace>/debug/regression/da_1608.md with thumbnails
  of the unmatched frames from either side, for manual review.
- venda_zero_clientes: <= 15 frames/min, every JPEG <= 500 KB, output 1568x882.
- dias_estoque (Teams): contract, density, hot segments.
- Criando sua primeira PR.mov (Retina 60 fps, B-frames, accents in the path): analysis 552x360,
  output 1568x1018, <= 20 frames/min.
- determinism on venda_zero: two runs and lockstep vs sequential are byte-identical.
"""
import hashlib
import io
import json
import math
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from conftest import DATA_DIR, resolve_fixture_glob
from va.common import as_posix, fmt_hms, workspace_home
from va.contracts import check_frames
from va.extract import run_frames_stage
from va.ffmpeg_io import CREATE_NO_WINDOW, seek_jpeg_cmd
from va.probe import probe_video

pytestmark = pytest.mark.real


@pytest.fixture(scope="module")
def stage(tmp_path_factory, fixtures_db):
    cache: dict = {}

    def get(name: str):
        if name not in cache:
            entry = fixtures_db["fixtures"][name]
            hits = resolve_fixture_glob(entry["video"])
            if not hits:
                pytest.skip(f"fixture {name}: video not found")
            src = hits[0]
            info = probe_video(src)
            out = tmp_path_factory.mktemp(name) / "frames-real"
            t0 = time.perf_counter()
            doc = run_frames_stage(src, info, {}, out, video_key=entry["expect"]["slug"] + "-test")
            cache[name] = (doc, info, entry, out, time.perf_counter() - t0)
        return cache[name]

    return get


def _common_checks(doc, info, entry):
    exp = entry["expect"]
    assert check_frames(doc) == []
    assert doc["analysis_size"] == exp["analysis_size"] and doc["output_size"] == exp["output_size"]
    assert abs(doc["video"]["duration_s"] - exp["duration_s"]) < 0.01
    assert abs(doc["n_samples"] - math.floor(2 * exp["duration_s"])) <= 1
    out = Path(doc["frames_dir"])
    for f in doc["frames"]:
        assert (out / f["file"]).stat().st_size == f["bytes"] <= 500_000


# --- Repasse regression vs the September baseline ------------------------------------------------

def _thumb_from_video(src: Path, t: float, size=(400, 225)) -> Image.Image:
    cmd = seek_jpeg_cmd(src, 0, t, size[0], size[1], 4)
    out = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
    return Image.open(io.BytesIO(out.stdout)).convert("RGB") if out.returncode == 0 and out.stdout else Image.new("RGB", size)


def _sheet(items: list[tuple[str, Image.Image]], path: Path, cols: int = 4) -> None:
    if not items:
        return
    w, h = items[0][1].size
    rows = math.ceil(len(items) / cols)
    sheet = Image.new("RGB", (cols * (w + 4) + 4, rows * (h + 24) + 4), (30, 30, 30))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 15)
    except OSError:
        font = ImageFont.load_default()
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(items):
        x, y = 4 + (i % cols) * (w + 4), 4 + (i // cols) * (h + 24)
        sheet.paste(im.resize((w, h)), (x, y))
        d.text((x, y + h + 3), label, fill=(255, 255, 0), font=font)
    sheet.save(path, quality=85)


def _write_regression_report(doc, src: Path, base: dict, missed: list[dict], extra: list[dict], recall: float) -> Path:
    folder = workspace_home() / "debug" / "regression"
    folder.mkdir(parents=True, exist_ok=True)
    out = Path(doc["frames_dir"])
    _sheet([(f"base {fmt_hms(b['t'])} chg={b['chg']:.3f}", _thumb_from_video(src, b["t"])) for b in missed],
           folder / "da_1608_baseline_unmatched.jpg")
    ours = []
    for f in extra:
        with Image.open(out / f["file"]) as im:
            ours.append((f"{f['id']} {fmt_hms(f['captured_at'])} {f['kind']}", im.convert("RGB").resize((400, 225))))
    _sheet(ours, folder / "da_1608_ours_unmatched.jpg")
    lines = [
        "# Regression da_1608 (Repasse - Fluxo de Carga dos DA's)",
        "",
        f"- frames: {len(doc['frames'])} (baseline {base['n_frames']}), unique {doc['n_unique']}",
        f"- recall of the {base['n_sig']} significant baseline frames within +-2 s: {recall:.3f}",
        f"- baseline frames unmatched: {len(missed)}; our frames with no baseline frame within +-2 s: {len(extra)}",
        "",
        "## Significant baseline frames without a frame of ours (+-2 s)",
        "",
    ]
    lines += [f"- {fmt_hms(b['t'])} (sample {b['sample']}, chg {b['chg']})" for b in missed] or ["- none"]
    if missed:
        lines += ["", f"![baseline unmatched]({as_posix(folder / 'da_1608_baseline_unmatched.jpg')})"]
    lines += ["", "## Our frames with no baseline frame within +-2 s", ""]
    lines += [f"- {f['id']} {fmt_hms(f['captured_at'])} {f['kind']} change {f['change']['frac']}" for f in extra] or ["- none"]
    if extra:
        lines += ["", f"![ours unmatched]({as_posix(folder / 'da_1608_ours_unmatched.jpg')})"]
    path = folder / "da_1608.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_repasse_regression_against_baseline(stage):
    doc, info, entry, out, elapsed = stage("repasse_da_1608")
    _common_checks(doc, info, entry)
    exp = entry["expect"]
    base = json.loads((DATA_DIR / "baseline" / "da_1608.json").read_text(encoding="utf-8"))
    frames = doc["frames"]
    tol = exp["baseline_tolerance_s"]

    def near(t):
        return any(abs(f["start"] - t) <= tol or abs(f["captured_at"] - t) <= tol for f in frames)

    sig = [b for b in base["frames"] if b["sig"]]
    missed = [b for b in sig if not near(b["t"])]
    recall = 1 - len(missed) / len(sig)
    extra = [f for f in frames if not any(abs(f["start"] - b["t"]) <= tol or abs(f["captured_at"] - b["t"]) <= tol
                                          for b in base["frames"])]
    report = _write_regression_report(doc, info.path, base, missed, extra, recall)
    assert report.exists()
    assert exp["frames_min"] <= len(frames) <= exp["frames_max"], f"{len(frames)} frames"
    assert recall >= exp["baseline_recall_min"], f"recall {recall:.3f}; missed {[b['t'] for b in missed]}"


# --- other fixtures ----------------------------------------------------------------------------

def test_venda_zero_clientes(stage):
    doc, info, entry, out, elapsed = stage("venda_zero_clientes")
    _common_checks(doc, info, entry)
    exp = entry["expect"]
    assert doc["density"]["frames_per_min"] <= exp["max_frames_per_min"]
    assert max(f["bytes"] for f in doc["frames"]) <= exp["max_jpeg_bytes"]
    assert doc["frames"][0]["start"] == 0.0 and doc["frames"][-1]["end"] == doc["range"][1]


def test_dias_estoque_teams(stage):
    doc, info, entry, out, elapsed = stage("dias_estoque")
    _common_checks(doc, info, entry)
    assert doc["density"]["frames_per_min"] <= 15
    assert doc["hot_segments"], "the Teams presenter cam / participant column should be masked"
    assert all(seg["frac"] < 0.5 for seg in doc["hot_segments"])


def test_criando_primeira_pr_retina_mov(stage):
    doc, info, entry, out, elapsed = stage("criando_primeira_pr")
    _common_checks(doc, info, entry)
    exp = entry["expect"]
    assert info.has_b_frames and info.width == 3024 and info.height == 1964
    assert "Conteúdos" in doc["video"]["path"] and doc["video"]["title"] == "Criando sua primeira PR"
    assert doc["density"]["frames_per_min"] <= exp["max_frames_per_min"]
    assert doc["frames"][1]["tokens"] == math.ceil(1568 / 28) * math.ceil(1018 / 28)


# --- determinism ----------------------------------------------------------------------------------

def test_determinism_on_venda_zero(tmp_path, fixtures_db):
    entry = fixtures_db["fixtures"]["venda_zero_clientes"]
    hits = resolve_fixture_glob(entry["video"])
    if not hits:
        pytest.skip("venda_zero_clientes not found")
    info = probe_video(hits[0])
    out = tmp_path / "frames-det"
    digests = []
    for params in ({}, {}, {"sequential": True}):
        if out.exists():
            shutil.rmtree(out)
        run_frames_stage(hits[0], info, params, out, video_key="det")
        digests.append({f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                        for f in sorted(out.iterdir()) if f.suffix in (".jpg", ".json", ".csv")})
    assert digests[0] == digests[1]
    assert digests[0] == digests[2]
