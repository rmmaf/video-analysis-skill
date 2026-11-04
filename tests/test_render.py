"""va.render: the batches stage end to end on a hand-written fixture (no ffmpeg).

Golden files: batch_001.md, batch_002.md and transcript.md must match byte for byte. The fixture
covers motion, an in-batch revisit (no path line), a revisit of an earlier batch ("(revisita de
F0001)", the same marker in every language), a cue spanning the batch boundary ("(cont.)"), an
approximate cue, overlapping cues of two speakers, a frame without speech and a cue without speaker.
"""
import importlib.util
import json
import re
import shutil
import sys

import pytest
from PIL import Image

from conftest import TESTS_DIR
from va.contracts import is_abs_posix
from va.render import LABELS, REVISIT_MARK, label_language, run_batches_stage

_spec = importlib.util.spec_from_file_location("va_check_run_for_render", TESTS_DIR / "check_run.py")
check_run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_run
_spec.loader.exec_module(check_run)

IMAGE_LINE = re.compile(r"^(?:[A-Za-z]:/|//)[^\n]*\.jpg(?: \(revisita de F\d{4,5}\))?$")   # every language
PARAMS = {"max_frames": 4, "soft_min": 2, "tail_min_frames": 2, "tail_merge_max": 5}
COLORS = {"F0001_000000.jpg": (200, 40, 40), "F0002_000011.jpg": (40, 200, 40), "F0003_000025.jpg": (40, 40, 200),
          "F0005_000041.jpg": (200, 200, 40), "F0007_000081.jpg": (40, 200, 200), "F0008_000096.jpg": (200, 40, 200)}


def _frame(n, start, end, cap, kind, file, same_as=None, frac=0.2):
    return {"id": f"F{n:04d}", "file": file, "start": start, "end": end, "captured_at": cap, "sample": int(cap * 2),
            "kind": kind, "same_as": same_as, "change": {"frac": frac, "bbox": [0.0, 0.0, 1.0, 1.0], "blobs": 1},
            "hot_frac": 0.0, "bytes": 1000, "tokens": 1792}


def _cue(n, start, end, spk, text, approx=False):
    return {"id": f"C{n:04d}", "start": start, "end": end, "spk": spk, "text": text, "approx": approx,
            "src": [n], "utt": None}


def make_fixture(root, language="pt"):
    fdir, bdir = root / "frames-0a0b0c0d", root / "batches-1a2b3c4d"
    fdir.mkdir(parents=True, exist_ok=True)
    for name, color in COLORS.items():
        Image.new("RGB", (160, 90), color).save(fdir / name, quality=90)
    frames = {
        "schema": "va.frames/1", "algo_version": "dedup-1.0",
        "video": {"path": "C:/videos/Reunião de teste.mp4", "title": "Reunião de teste", "key": "reuniao-de-teste-1a2b3c4d",
                  "duration_s": 120.0, "width": 1920, "height": 1080, "fps_avg": 16.0, "vfr": True, "codec": "h264"},
        "range": [0.0, 120.0], "sample_fps": 2.0, "n_samples": 240, "analysis_size": [640, 360],
        "output_size": [1568, 882], "frames_dir": fdir.as_posix(), "params": {},
        "frames": [
            _frame(1, 0.0, 10.0, 0.5, "first", "F0001_000000.jpg", frac=1.0),
            _frame(2, 10.0, 25.0, 11.0, "settled", "F0002_000011.jpg", frac=0.3),
            _frame(3, 25.0, 31.5, 25.5, "motion", "F0003_000025.jpg"),
            _frame(4, 31.5, 40.0, 32.0, "settled", "F0002_000011.jpg", same_as="F0002", frac=0.1),
            _frame(5, 40.0, 70.0, 41.0, "settled", "F0005_000041.jpg", frac=0.6),
            _frame(6, 70.0, 80.0, 70.5, "settled", "F0001_000000.jpg", same_as="F0001"),
            _frame(7, 80.0, 95.0, 81.0, "settled", "F0007_000081.jpg"),
            _frame(8, 95.0, 120.0, 96.0, "final", "F0008_000096.jpg"),
        ],
        "n_unique": 6, "hot_segments": [], "density": {"frames_per_min": 4.0, "max_per_min": 2, "max_window": [0.0, 60.0]},
        "suggested_ignore": [], "warnings": ["frames: aviso de teste"],
    }
    cues = {
        "schema": "va.cues/1", "parser_version": "vtt-1.0",
        "vtt": {"path": "C:/videos/Reunião de teste.vtt", "sha1": "0" * 40, "style": "whisperx", "language": language},
        "offset_s": 0.0, "stats": {},
        "speakers": [{"tag": "S1", "name": "Ana", "cues": 5, "talk_s": 27.6},
                     {"tag": "S2", "name": "SPEAKER_01", "cues": 3, "talk_s": 7.5}],
        "cues": [
            _cue(1, 1.2, 4.0, "S1", "Bom dia, vamos abrir o painel de vendas."),
            _cue(2, 4.5, 12.3, "S1", "Aqui em cima fica o filtro de região e o período do relatório."),
            _cue(3, 12.5, 13.0, "S2", "Certo."),
            _cue(4, 26.0, 30.0, "S1", "Agora eu rolo a tabela até o fim para mostrar os totais.", approx=True),
            _cue(5, 38.0, 44.0, "S2", "E esse número vem de onde?"),
            _cue(6, 45.0, 52.0, "S1", "Vem da base de pedidos que o processo noturno atualiza."),
            _cue(7, 50.0, 51.0, "S2", "Entendi."),
            _cue(8, 72.0, 78.0, "S1", "Voltando para a tela inicial."),
            _cue(9, 100.0, 104.0, None, "(aplausos)"),
        ],
        "warnings": ["legenda: aviso de teste"],
    }
    meta = {"title": "Reunião de teste", "video_path": "C:/videos/Reunião de teste.mp4",
            "vtt_path": "C:/videos/Reunião de teste.vtt", "video_key": "reuniao-de-teste-1a2b3c4d",
            "frames_dir": fdir, "frames_json": fdir / "frames.json", "cues_json": root / "cues-9f8e7d6c" / "cues.json"}
    return frames, cues, meta, fdir, bdir


def run(tmp_path, params=None, language="pt", meta_extra=None, frames_fn=None, cues_fn=None):
    frames, cues, meta, fdir, bdir = make_fixture(tmp_path, language)
    if frames_fn:
        frames_fn(frames)
    if cues_fn:
        cues_fn(cues)
    meta.update(meta_extra or {})
    result = run_batches_stage(frames, cues, PARAMS if params is None else params, bdir, meta)
    return result, fdir.as_posix(), bdir.as_posix(), bdir


GOLDEN_B001 = """\
# Lote B001 de 2 · F0001–F0004 · 00:00:00.0–00:00:40.0
Vídeo: Reunião de teste · 00:02:00.0 · 1568×882
Falantes: S1 = Ana · S2 = SPEAKER_01
Folha de contato: {B}/sheet_001.jpg
Anterior: — · Próximo: B002 (00:00:40.0–00:02:00.0)
Leitura: frame vale de início a fim; img = instante da imagem; fala completa na 1ª vez no lote, depois ↳ id; ≈ horário aproximado; (cont.) começou antes do lote.

### F0001 · 00:00:00.0–00:00:10.0 · img 00:00:00.5
{F}/F0001_000000.jpg
- C0001 00:00:01.2–00:00:04.0 S1: Bom dia, vamos abrir o painel de vendas.
- C0002 00:00:04.5–00:00:12.3 S1: Aqui em cima fica o filtro de região e o período do relatório.

### F0002 · 00:00:10.0–00:00:25.0 · img 00:00:11.0
{F}/F0002_000011.jpg
- ↳ C0002
- C0003 00:00:12.5–00:00:13.0 S2: Certo.

### F0003 · 00:00:25.0–00:00:31.5 · img 00:00:25.5 · movimento
{F}/F0003_000025.jpg
- C0004 ≈00:00:26.0–00:00:30.0 S1: Agora eu rolo a tabela até o fim para mostrar os totais.

### F0004 · 00:00:31.5–00:00:40.0 · img 00:00:32.0 · = F0002 (mesma tela, acima)
- C0005 00:00:38.0–00:00:44.0 S2: E esse número vem de onde?
"""

GOLDEN_B002 = """\
# Lote B002 de 2 · F0005–F0008 · 00:00:40.0–00:02:00.0
Vídeo: Reunião de teste · 00:02:00.0 · 1568×882
Falantes: S1 = Ana · S2 = SPEAKER_01
Folha de contato: {B}/sheet_002.jpg
Anterior: B001 (00:00:00.0–00:00:40.0) · Próximo: —
Leitura: frame vale de início a fim; img = instante da imagem; fala completa na 1ª vez no lote, depois ↳ id; ≈ horário aproximado; (cont.) começou antes do lote.

### F0005 · 00:00:40.0–00:01:10.0 · img 00:00:41.0
{F}/F0005_000041.jpg
- C0005 00:00:38.0–00:00:44.0 (cont.) S2: E esse número vem de onde?
- C0006 00:00:45.0–00:00:52.0 S1: Vem da base de pedidos que o processo noturno atualiza.
- C0007 00:00:50.0–00:00:51.0 S2: Entendi.

### F0006 · 00:01:10.0–00:01:20.0 · img 00:01:10.5 · = F0001
{F}/F0001_000000.jpg (revisita de F0001)
- C0008 00:01:12.0–00:01:18.0 S1: Voltando para a tela inicial.

### F0007 · 00:01:20.0–00:01:35.0 · img 00:01:21.0
{F}/F0007_000081.jpg
- (sem fala)

### F0008 · 00:01:35.0–00:02:00.0 · img 00:01:36.0
{F}/F0008_000096.jpg
- C0009 00:01:40.0–00:01:44.0 (aplausos)
"""

GOLDEN_TRANSCRIPT = """\
# Transcrição · Reunião de teste
Duração 00:02:00.0 · 9 falas · Falantes: S1 = Ana (79%) · S2 = SPEAKER_01 (21%)

[00:00:01.2 · F0001–F0002 · B001] S1: Bom dia, vamos abrir o painel de vendas. Aqui em cima fica o filtro de região e o período do relatório. (C0001–C0002)
[00:00:12.5 · F0002 · B001] S2: Certo. (C0003)
[≈00:00:26.0 · F0003 · B001] S1: Agora eu rolo a tabela até o fim para mostrar os totais. (C0004)
[00:00:38.0 · F0004–F0005 · B001–B002] S2: E esse número vem de onde? (C0005)
[00:00:45.0 · F0005 · B002] S1: Vem da base de pedidos que o processo noturno atualiza. (C0006)
[00:00:50.0 · F0005 · B002] S2: Entendi. (C0007)
[00:01:12.0 · F0006 · B002] S1: Voltando para a tela inicial. (C0008)
[00:01:40.0 · F0008 · B002] (aplausos) (C0009)
"""


def golden(text, fpos, bpos):
    return text.replace("{F}", fpos).replace("{B}", bpos).encode("utf-8")


# --- golden ---------------------------------------------------------------------------------------

def test_golden_batch_md_and_transcript(tmp_path):
    _, fpos, bpos, bdir = run(tmp_path)
    assert (bdir / "batch_001.md").read_bytes() == golden(GOLDEN_B001, fpos, bpos)
    assert (bdir / "batch_002.md").read_bytes() == golden(GOLDEN_B002, fpos, bpos)
    assert (bdir / "transcript.md").read_bytes() == golden(GOLDEN_TRANSCRIPT, fpos, bpos)


def test_stage_writes_every_file(tmp_path):
    _, _, _, bdir = run(tmp_path)
    assert sorted(p.name for p in bdir.iterdir()) == [
        "batch_001.json", "batch_001.md", "batch_002.json", "batch_002.md", "batches.json", "overview.jpg",
        "sheet_001.jpg", "sheet_002.jpg", "summary.json", "timeline.json", "transcript.md"]
    for p in bdir.iterdir():
        data = p.read_bytes()
        if p.suffix in (".md", ".json"):
            assert b"\r" not in data and data.endswith(b"\n")
        else:
            assert data[:3] == b"\xff\xd8\xff"


# --- the image-line contract ----------------------------------------------------------------------------

def test_image_path_lines_equal_n_images_everywhere(tmp_path):
    result, fpos, bpos, bdir = run(tmp_path)
    summary = result["summary"]
    for entry, srow in zip(result["batches"]["batches"], summary["batches"]):
        text = (bdir / f"batch_{entry['n']:03d}.md").read_text(encoding="utf-8")
        lines = text.split("\n")
        image_lines = [ln for ln in lines if IMAGE_LINE.match(ln)]
        assert len(image_lines) == entry["n_images"] == srow[6]
        assert check_run.parse_batch(text).images == entry["n_images"]
        paths = [ln.split(" (", 1)[0] for ln in image_lines]
        assert len(paths) == len(set(paths)), "the same path twice in one batch"
        assert all((bdir.parent / "frames-0a0b0c0d" / p.rsplit("/", 1)[1]).is_file() for p in paths)
        # every other line mentioning a JPEG is the contact sheet line, never a bare path
        others = [ln for ln in lines if ".jpg" in ln and ln not in image_lines]
        assert others == [f"Folha de contato: {bpos}/sheet_{entry['n']:03d}.jpg"]
        doc = json.loads((bdir / f"batch_{entry['n']:03d}.json").read_text(encoding="utf-8"))
        assert doc["batch"]["n_images"] == entry["n_images"]
    assert [e["n_images"] for e in result["batches"]["batches"]] == [3, 4]


# --- JSON documents ------------------------------------------------------------------------------------

def _strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


def test_json_documents(tmp_path):
    result, fpos, bpos, bdir = run(tmp_path)
    batches = json.loads((bdir / "batches.json").read_text(encoding="utf-8"))
    assert batches == result["batches"]
    assert list(batches) == ["schema", "render_version", "language", "n_batches", "params", "batches"]
    assert batches["schema"] == "va.batches/1" and batches["n_batches"] == 2 and batches["language"] == "pt"
    assert batches["params"]["max_frames"] == 4 and batches["params"]["range"] is None
    e1, e2 = batches["batches"]
    assert list(e1) == ["n", "id", "frames", "t", "n_frames", "n_images", "cues", "chars", "over_budget",
                        "est_tokens", "md", "json", "sheet"]
    assert (e1["id"], e1["frames"], e1["t"], e1["n_frames"], e1["cues"]) == ("B001", ["F0001", "F0004"], [0.0, 40.0], 4, ["C0001", "C0005"])
    assert (e2["id"], e2["frames"], e2["t"], e2["cues"]) == ("B002", ["F0005", "F0008"], [40.0, 120.0], ["C0005", "C0009"])
    cues = make_fixture(tmp_path / "x")[1]["cues"]
    assert e1["chars"] == sum(len(c["text"]) for c in cues[:5]) and not e1["over_budget"]
    assert e1["est_tokens"]["images"] == 3 * 1792 and e2["est_tokens"]["images"] == 4 * 1792
    assert e1["est_tokens"]["text"] == -(-e1["chars"] * 2 // 7)
    assert e1["md"] == f"{bpos}/batch_001.md" and e1["sheet"] == f"{bpos}/sheet_001.jpg"

    b2 = json.loads((bdir / "batch_002.json").read_text(encoding="utf-8"))
    assert list(b2) == ["schema", "video", "batch", "prev", "next", "speakers", "frames", "cues"]
    assert b2["prev"] == {"id": "B001", "t": [0.0, 40.0]} and b2["next"] is None
    assert b2["video"] == {"title": "Reunião de teste", "key": "reuniao-de-teste-1a2b3c4d", "duration_s": 120.0}
    assert b2["speakers"] == [{"tag": "S1", "name": "Ana"}, {"tag": "S2", "name": "SPEAKER_01"}]
    f5, f6 = b2["frames"][0], b2["frames"][1]
    assert f5["cues"] == ["C0005", "C0006", "C0007"] and f5["new_cues"] == ["C0005", "C0006", "C0007"]
    assert f6["path"] == f"{fpos}/F0001_000000.jpg" and f6["same_as"] == "F0001"
    assert [c["cont"] for c in b2["cues"]] == [True, False, False, False, False]

    tl = json.loads((bdir / "timeline.json").read_text(encoding="utf-8"))
    assert tl["schema"] == "va.timeline/1"
    assert tl["frames"][0] == {"id": "F0001", "start": 0.0, "end": 10.0, "cues": ["C0001", "C0002"]}
    assert tl["cue_frames"]["C0005"] == ["F0004", "F0005"] and tl["cue_frames"]["C0002"] == ["F0001", "F0002"]
    assert tl["batches"] == [{"id": "B001", "frames": ["F0001", "F0004"]}, {"id": "B002", "frames": ["F0005", "F0008"]}]

    for doc in (batches, b2, tl, result["summary"]):
        for s in _strings(doc):
            if s.endswith((".jpg", ".md", ".json", ".mp4", ".vtt")) or s.startswith(("C:", "c:")):
                assert is_abs_posix(s), s


def test_summary_fields(tmp_path):
    result, fpos, bpos, bdir = run(tmp_path)
    s = json.loads((bdir / "summary.json").read_text(encoding="utf-8"))
    assert s == result["summary"]
    assert list(s) == ["schema", "title", "video_path", "vtt_path", "video_key", "duration_s", "range", "resolution",
                       "fps_avg", "vfr", "output_size", "sample_fps", "n_samples", "n_frames", "n_unique",
                       "frames_per_min", "kinds", "n_cues", "n_speakers", "language", "cue_style", "n_batches",
                       "batches", "est_tokens", "paths", "warnings"]
    assert (s["title"], s["video_path"], s["video_key"]) == ("Reunião de teste", "C:/videos/Reunião de teste.mp4",
                                                             "reuniao-de-teste-1a2b3c4d")
    assert (s["duration_s"], s["range"], s["resolution"], s["output_size"]) == (120.0, [0.0, 120.0], [1920, 1080], [1568, 882])
    assert (s["n_frames"], s["n_unique"], s["frames_per_min"], s["n_cues"], s["n_speakers"]) == (8, 6, 4.0, 9, 2)
    assert s["kinds"] == {"first": 1, "settled": 5, "motion": 1, "heartbeat": 0, "final": 1}
    assert (s["language"], s["cue_style"], s["n_batches"]) == ("pt", "whisperx", 2)
    assert s["batches"] == [[1, "F0001", "F0004", 0.0, 40.0, 4, 3], [2, "F0005", "F0008", 40.0, 120.0, 4, 4]]
    assert s["est_tokens"]["frames"] == 7 * 1792 and s["est_tokens"]["sheets"] > 0 and s["est_tokens"]["text"] > 0
    assert s["paths"] == {
        "frames_dir": fpos, "batches_dir": bpos, "frames_json": f"{fpos}/frames.json",
        "cues_json": f"{bpos.rsplit('/', 1)[0]}/cues-9f8e7d6c/cues.json", "timeline_json": f"{bpos}/timeline.json",
        "transcript_md": f"{bpos}/transcript.md", "batches_json": f"{bpos}/batches.json"}
    assert s["warnings"] == ["frames: aviso de teste", "legenda: aviso de teste"]


# --- variants ----------------------------------------------------------------------------------------

def test_english_labels(tmp_path):
    result, fpos, bpos, bdir = run(tmp_path, language="en")
    b1 = (bdir / "batch_001.md").read_text(encoding="utf-8")
    b2 = (bdir / "batch_002.md").read_text(encoding="utf-8")
    assert b1.startswith("# Batch B001 of 2 · F0001–F0004 · 00:00:00.0–00:00:40.0\nVideo: Reunião de teste")
    assert "\nSpeakers: S1 = Ana · S2 = SPEAKER_01\n" in b1 and f"\nContact sheet: {bpos}/sheet_001.jpg\n" in b1
    assert "\nPrevious: — · Next: B002 (00:00:40.0–00:02:00.0)\n" in b1 and "\nReading: a frame holds" in b1
    assert " · motion\n" in b1 and " · = F0002 (same screen, above)\n" in b1   # in-batch revisit: localized
    # the path marker of a revisit from an earlier batch is language-independent
    assert f"\n### F0006 · 00:01:10.0–00:01:20.0 · img 00:01:10.5 · = F0001\n{fpos}/F0001_000000.jpg (revisita de F0001)\n" in b2
    assert "revisit of" not in b1 + b2 and "\n- (no speech)\n" in b2
    assert "(cont.) S2:" in b2
    tr = (bdir / "transcript.md").read_text(encoding="utf-8")
    assert tr.startswith("# Transcript · Reunião de teste\nDuration 00:02:00.0 · 9 cues · Speakers: S1 = Ana (79%)")
    assert result["batches"]["language"] == "en" and [e["n_images"] for e in result["batches"]["batches"]] == [3, 4]
    assert check_run.parse_batch(b2).images == 4


@pytest.mark.parametrize("language", ["pt", "en", "es", "und"])
def test_revisit_path_marker_is_language_independent(tmp_path, language):
    """The suffix of an image path line is always " (revisita de Fxxxx)": the analyst role and
    tests/check_run.py match it literally, whatever language the labels follow."""
    result, fpos, _, bdir = run(tmp_path, language=language)
    b2 = (bdir / "batch_002.md").read_text(encoding="utf-8")
    marked = [ln for ln in b2.split("\n") if ln.endswith(")") and ln.startswith(fpos)]
    assert marked == [f"{fpos}/F0001_000000.jpg ({REVISIT_MARK} F0001)"] == [f"{fpos}/F0001_000000.jpg (revisita de F0001)"]
    assert [e["n_images"] for e in result["batches"]["batches"]] == [3, 4]
    assert check_run.parse_batch(b2).images == 4
    assert all("revisit_of" not in labels for labels in LABELS.values())


def test_label_language():
    assert label_language({"language": "pt-BR"}, {"vtt": {"language": "en"}}) == "pt"
    assert label_language({}, {"vtt": {"language": "pt"}}) == "pt"
    assert label_language({"language": None}, {"vtt": {"language": "es"}}) == "en"
    assert label_language({}, {"vtt": {"language": "und"}}) == "en"
    assert label_language(None, None) == "en"


def test_meta_language_override(tmp_path):
    result, _, _, bdir = run(tmp_path, language="und", meta_extra={"language": "pt-BR"})
    assert (bdir / "batch_001.md").read_text(encoding="utf-8").startswith("# Lote B001 de 2")
    assert result["summary"]["language"] == "pt-BR"


def test_trecho_restarts_numbering_and_keeps_frame_ids(tmp_path):
    result, fpos, bpos, bdir = run(tmp_path, params={**PARAMS, "range": [30.0, 75.0]})
    s = result["summary"]
    assert s["range"] == [30.0, 75.0] and s["n_frames"] == 4 and s["n_batches"] == 1
    assert s["batches"] == [[1, "F0003", "F0006", 25.0, 80.0, 4, 4]]
    md = (bdir / "batch_001.md").read_text(encoding="utf-8")
    assert md.startswith("# Lote B001 de 1 · F0003–F0006 · 00:00:25.0–00:01:20.0\n"
                         "Vídeo: Reunião de teste · 00:02:00.0 · 1568×882 · trecho 00:00:30.0–00:01:15.0\n")
    assert "\nAnterior: — · Próximo: —\n" in md
    assert f"\n### F0004 · 00:00:31.5–00:00:40.0 · img 00:00:32.0 · = F0002\n{fpos}/F0002_000011.jpg (revisita de F0002)\n" in md
    assert f"\n{fpos}/F0001_000000.jpg (revisita de F0001)\n" in md
    assert check_run.parse_batch(md).images == 4
    assert not (bdir / "batch_002.md").exists()
    tr = (bdir / "transcript.md").read_text(encoding="utf-8")
    assert "Duração 00:02:00.0 · trecho 00:00:30.0–00:01:15.0 · 5 falas" in tr
    assert "(C0004)" in tr and "(C0008)" in tr and "(C0001" not in tr and "(C0009)" not in tr
    assert result["batches"]["params"]["range"] == [30.0, 75.0]
    with pytest.raises(ValueError, match="no frames"):
        run(tmp_path / "out", params={**PARAMS, "range": [130.0, 140.0]})


def test_no_cues_and_plain_cues(tmp_path):
    def no_cues(c):
        c["cues"], c["speakers"] = [], []

    _, _, _, bdir = run(tmp_path / "a", cues_fn=no_cues)
    md = (bdir / "batch_001.md").read_text(encoding="utf-8")
    assert "\nFalantes: (sem fala no lote)\n" in md and md.count("- (sem fala)") == 4
    tr = (bdir / "transcript.md").read_text(encoding="utf-8")
    assert tr == "# Transcrição · Reunião de teste\nDuração 00:02:00.0 · 0 falas\n\n(sem legenda)\n"

    def plain(c):
        c["speakers"] = []
        for cue in c["cues"]:
            cue["spk"] = None

    _, _, _, bdir = run(tmp_path / "b", cues_fn=plain)
    md = (bdir / "batch_001.md").read_text(encoding="utf-8")
    assert "\nFalantes: (legenda sem rótulos de falante)\n" in md
    assert "\n- C0001 00:00:01.2–00:00:04.0 Bom dia, vamos abrir o painel de vendas.\n" in md
    tr = (bdir / "transcript.md").read_text(encoding="utf-8").split("\n")
    assert tr[1] == "Duração 00:02:00.0 · 9 falas"
    assert tr[3].startswith("[00:00:01.2 · F0001–F0002 · B001] Bom dia,") and tr[3].endswith("(C0001–C0003)")


def test_over_budget_warning(tmp_path):
    def long_text(c):
        c["cues"][5]["text"] = "palavra " * 1200 + "fim."

    result, _, _, _ = run(tmp_path, params={**PARAMS, "max_text_chars": 8000}, cues_fn=long_text)
    over = [e for e in result["batches"]["batches"] if e["over_budget"]]
    assert over and over[0]["frames"][0] == "F0005"
    assert any("characters of speech" in w for w in result["summary"]["warnings"])


def test_missing_image_gives_placeholder_and_warning(tmp_path):
    frames, cues, meta, fdir, bdir = make_fixture(tmp_path)
    (fdir / "F0007_000081.jpg").unlink()
    result = run_batches_stage(frames, cues, PARAMS, bdir, meta)
    assert any("not found" in w and "F0007" in w for w in result["summary"]["warnings"])
    assert (bdir / "sheet_002.jpg").is_file()


def test_deterministic_and_stale_files_removed(tmp_path):
    _, _, _, bdir = run(tmp_path)
    first = {p.name: p.read_bytes() for p in bdir.iterdir()}
    shutil.rmtree(bdir)
    bdir.mkdir()
    (bdir / "batch_007.md").write_text("velho", encoding="utf-8")
    (bdir / "sheet_009.jpg").write_bytes(b"velho")
    (bdir / "stage.json").write_text("{}", encoding="utf-8")
    run(tmp_path)
    second = {p.name: p.read_bytes() for p in bdir.iterdir() if p.name != "stage.json"}
    assert second == first
    assert (bdir / "stage.json").read_text(encoding="utf-8") == "{}"  # not ours: untouched


# --- real VTT on synthetic frames (no ffmpeg) ------------------------------------------------------

@pytest.mark.real
def test_real_dias_estoque_md_has_speakers_and_overlaps(real_fixture, tmp_path):
    from va.vtt import run_cues_stage

    fx = real_fixture("dias_estoque")
    duration = fx["expect"]["duration_s"]
    cues = run_cues_stage(fx["vtt"], {}, tmp_path / "cues", duration)
    fdir = tmp_path / "frames"
    fdir.mkdir()
    Image.new("RGB", (160, 90), (90, 90, 90)).save(fdir / "frame.jpg")
    starts = [float(t) for t in range(0, int(duration), 15)]
    frames = {"video": {"title": "dias_estoque", "duration_s": duration, "width": 1920, "height": 1080},
              "range": [0.0, duration], "output_size": [1568, 882], "frames_dir": fdir.as_posix(),
              "frames": [{"id": f"F{i + 1:04d}", "file": "frame.jpg" if i == 0 else f"F{i + 1:04d}.jpg",
                          "start": t, "end": starts[i + 1] if i + 1 < len(starts) else duration, "captured_at": t,
                          "kind": "settled", "same_as": None, "change": {"frac": 0.1}}
                         for i, t in enumerate(starts)]}
    for f in frames["frames"][1:]:
        shutil.copyfile(fdir / "frame.jpg", fdir / f["file"])
    result = run_batches_stage(frames, cues, {}, tmp_path / "batches", {"frames_dir": fdir})
    texts = [(tmp_path / "batches" / f"batch_{e['n']:03d}.md").read_text(encoding="utf-8")
             for e in result["batches"]["batches"]]
    legends = [t.split("\n")[2] for t in texts]
    assert any(leg.count(" = ") >= 2 for leg in legends)
    by_id = {c["id"]: c for c in cues["cues"]}
    overlapping_in_md = 0
    for text in texts:
        for block in text.split("\n### ")[1:]:
            ids = re.findall(r"^- (C\d{4}) ", block, re.M)
            for a, b in zip(ids, ids[1:]):
                ca, cb = by_id[a], by_id[b]
                if cb["start"] < ca["end"] and ca["spk"] != cb["spk"]:
                    overlapping_in_md += 1
    assert overlapping_in_md >= 10
    for text, e in zip(texts, result["batches"]["batches"]):
        assert check_run.parse_batch(text).images == e["n_images"] == e["n_frames"]
    assert result["summary"]["n_cues"] == len(cues["cues"]) and result["summary"]["n_speakers"] == 3
