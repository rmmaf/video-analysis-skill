"""Integrity of the WP0 test data: September baseline, VTT snippets and tests/fixtures.json."""
import json

import pytest

from conftest import DATA_DIR, FIXTURES_JSON, resolve_fixture_glob
from va.common import slugify

BASELINE = DATA_DIR / "baseline"
VTT_DIR = DATA_DIR / "vtt"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- baseline --------------------------------------------------------------------------------

def test_da_1608_baseline():
    b = load(BASELINE / "da_1608.json")
    frames = b["frames"]
    assert b["n_frames"] == len(frames) == 128
    assert b["n_samples"] == 1089 and b["duration_s"] == 544.512
    assert b["n_sig"] == sum(f["sig"] for f in frames) and 0 < b["n_sig"] < 128
    assert "*" in b["source_glob"] and b["sig_rule"]["min_frac"] == 0.02 and b["sig_rule"]["x_cut"] == 0.83
    assert frames[0]["t"] == 0.0 and frames[0]["sig"] is True
    for i, f in enumerate(frames):
        assert f["n"] == i + 1
        assert f["sample"] == round(f["t"] * 2)
        assert f["file"].startswith(f"f{i:04d}_")
        assert 0.0 <= f["chg"] <= 1.0 and f["sig"] == (i == 0 or f["chg"] >= 0.02)
        nxt = frames[i + 1]["t"] if i + 1 < len(frames) else b["duration_s"]
        assert f["end"] == nxt and f["t"] < f["end"]


def test_baseline_summary():
    s = load(BASELINE / "summary.json")
    by_id = {v["id"]: v for v in s["videos"]}
    assert len(by_id) == 7
    da = by_id["da_1608"]
    assert (da["duration_s"], da["samples"], da["kept"], da["runtime_s"]) == (544.512, 1089, 128, 15)
    for v in s["videos"]:
        assert 0 < v["kept"] < v["samples"] and v["runtime_s"] > 0
        assert 66.0 <= v["discarded_pct"] <= 92.0
        assert "\\" not in v["video"]


# --- VTT snippets ------------------------------------------------------------------------------

MANIFEST = load(VTT_DIR / "manifest.json")["snippets"]


def test_manifest_lists_every_vtt_file():
    listed = {e["file"] for e in MANIFEST}
    on_disk = {p.name for p in VTT_DIR.glob("*.vtt")}
    assert listed == on_disk
    for e in MANIFEST:
        if e["source_glob"]:
            folder = e["source_glob"].rsplit("/", 1)[0]
            assert "*" not in folder and "?" not in folder, "wildcards only in the last component"
    readme = (VTT_DIR / "README.md").read_text(encoding="utf-8")
    assert all(f"`{name}`" in readme for name in listed)


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: e["file"])
def test_snippet_bytes(entry):
    data = (VTT_DIR / entry["file"]).read_bytes()
    assert len(data) == entry["bytes"]
    assert data.count(b"-->") == entry["timing_lines"]
    assert data.startswith(b"\xef\xbb\xbf") == entry["bom"]
    lone_lf = data.replace(b"\r\n", b"").count(b"\n")
    lone_cr = data.replace(b"\r\n", b"").count(b"\r")
    if entry["newline"] == "LF":
        assert b"\r" not in data
    elif entry["newline"] == "CRLF":
        assert lone_lf == 0 and lone_cr == 0 and b"\r\n" in data
    else:
        assert lone_lf and lone_cr and b"\r\n" in data
    if entry["encoding"] == "utf-8":
        text = data.decode("utf-8-sig")
        assert text.startswith("WEBVTT")
    else:
        with pytest.raises(UnicodeDecodeError):
            data.decode("utf-8")
    if entry["source_glob"]:
        a, b = entry["cues"]
        assert entry["timing_lines"] == b - a + 1 <= entry["source_cues"]


def test_cp1252_copy_matches_utf8_snippet():
    cp = (VTT_DIR / "cp1252_accented.vtt").read_bytes().decode("cp1252")
    utf8 = (VTT_DIR / "whisperx_criando_pr.vtt").read_text(encoding="utf-8")
    assert utf8.startswith(cp) and "código" in cp and "atualizações" in cp


@pytest.mark.real
@pytest.mark.parametrize("entry", [e for e in MANIFEST if e["source_glob"]], ids=lambda e: e["file"])
def test_snippet_is_excerpt_of_source(entry):
    hits = resolve_fixture_glob(entry["source_glob"])
    if not hits:
        pytest.skip(f"source missing: {entry['source_glob']}")
    assert len(hits) == 1
    src = hits[0].read_bytes()
    data = (VTT_DIR / entry["file"]).read_bytes()
    nl = b"\r\n" if entry["newline"] == "CRLF" else b"\n"
    head_end = data.index(nl + nl) + 2 * len(nl)
    assert src.startswith(data[:head_end])
    assert data[head_end:] in src


# --- fixtures.json ------------------------------------------------------------------------------

FIXTURES = load(FIXTURES_JSON)["fixtures"]
REQUIRED_EXPECT = {"duration_s", "width", "height", "analysis_size", "output_size", "raw_cues", "cue_style", "slug"}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_entries(name):
    fx = FIXTURES[name]
    assert {"video", "vtt", "optional", "expect"} <= set(fx)
    assert "\\" not in fx["video"] and "\\" not in fx["vtt"]
    for key in ("video", "vtt"):
        folder = fx[key].rsplit("/", 1)[0]
        assert "*" not in folder and "?" not in folder, "wildcards only in the last component"
    assert REQUIRED_EXPECT <= set(fx["expect"])
    exp = fx["expect"]
    stem = fx["video"].rsplit("/", 1)[1].rsplit(".", 1)[0]
    if "*" not in stem:
        assert slugify(stem) == exp["slug"]
    assert exp["cue_style"] in ("whisperx", "teams", "plain", "mixed")


def test_required_fixtures_present_in_list():
    assert {"repasse_da_1608", "venda_zero_clientes", "dias_estoque", "criando_primeira_pr", "metric_view"} <= set(FIXTURES)
    assert FIXTURES["red_aderencia"]["optional"] is True
    assert FIXTURES["repasse_da_1608"]["baseline"] == "data/baseline/da_1608.json"


@pytest.mark.real
@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_globs_resolve_to_one_file(name, real_fixture):
    fx = real_fixture(name)
    assert fx["video"].is_file() and fx["vtt"].is_file()
    assert fx["video"].stem == fx["vtt"].stem
    raw = fx["vtt"].read_bytes()
    assert raw.count(b"-->") == fx["expect"]["raw_cues"]
    assert raw.startswith(b"\xef\xbb\xbf") == fx["expect"]["vtt_bom"]
    assert (b"\r\n" in raw) == (fx["expect"]["vtt_newline"] == "CRLF")
    assert slugify(fx["video"].stem) == fx["expect"]["slug"]
