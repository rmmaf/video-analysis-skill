"""Unit tests for va.contracts: hand-made valid documents pass, broken ones are rejected."""
import copy

import pytest

from va import contracts as K

RUN = "C:/Users/digom/video-analysis/runs/20261006-153012-venda-zero-clientes"


# --- examples ----------------------------------------------------------------------------------

def frames_doc():
    def frame(n, start, end, cap, sample, kind, file, same_as=None, frac=0.2):
        return {"id": f"F{n:04d}", "file": file, "start": start, "end": end, "captured_at": cap, "sample": sample,
                "kind": kind, "same_as": same_as, "change": {"frac": frac, "bbox": [0.1, 0.2, 0.3, 0.4], "blobs": 2},
                "hot_frac": 0.0, "bytes": 51392, "tokens": 1792}

    return {
        "schema": "va.frames/1", "algo_version": "dedup-1.0",
        "video": {"path": "C:/Users/digom/Documents/solar/databricks  schema/docs-solar/01.Arquitetura/3.DA/"
                          "Repasse - Fluxo de Carga dos DA\u2019s-20260818_160841-Grava\u00e7\u00e3o de Reuni\u00e3o.mp4",
                  "title": "Repasse - Fluxo de Carga dos DA\u2019s-20260818_160841-Grava\u00e7\u00e3o de Reuni\u00e3o",
                  "key": "repasse-fluxo-de-carga-20260818-160841-3fa9c2d1", "duration_s": 30.512, "width": 1920,
                  "height": 1080, "fps_avg": 16.0, "vfr": True, "codec": "h264"},
        "range": [0.0, 30.0], "sample_fps": 2.0, "n_samples": 60, "analysis_size": [640, 360], "output_size": [1568, 882],
        "frames_dir": "C:/Users/digom/video-analysis/cache/repasse-fluxo-de-carga-20260818-160841-3fa9c2d1/frames-8c41d2e0",
        "params": {"t_lo": 16},
        "frames": [
            frame(1, 0.0, 10.0, 0.5, 1, "first", "F0001_000000.jpg", frac=1.0),
            frame(2, 10.0, 15.5, 11.0, 22, "settled", "F0002_000011.jpg"),
            frame(3, 15.5, 22.0, 19.5, 39, "motion", "F0003_000019.jpg"),
            frame(4, 22.0, 30.0, 23.0, 46, "final", "F0001_000000.jpg", same_as="F0001"),
        ],
        "n_unique": 3,
        "hot_segments": [{"start": 0.0, "end": 30.0, "frac": 0.034, "bbox": [0.83, 0.11, 0.17, 0.17], "cells": ""}],
        "density": {"frames_per_min": 8.0, "max_per_min": 4, "max_window": [0.0, 60.0]},
        "suggested_ignore": [], "warnings": [],
    }


def cues_doc():
    def cue(n, start, end, spk, text, src):
        return {"id": f"C{n:04d}", "start": start, "end": end, "spk": spk, "text": text, "approx": False, "src": src, "utt": None}

    return {
        "schema": "va.cues/1", "parser_version": "vtt-1.0",
        "vtt": {"path": "C:/Users/digom/Downloads/venda_zero_clientes.vtt", "sha1": "0123456789abcdef0123456789abcdef01234567",
                "style": "whisperx", "language": "pt"},
        "offset_s": 0.0,
        "stats": {"raw": 6, "kept": 5, "merged_dupes": 1, "merged_rolling": 0, "split_long": 0, "extended_fast": 0,
                  "dropped_empty": 0, "overlaps": 0, "nonmonotonic": 0},
        "speakers": [{"tag": "S1", "name": "SPEAKER_01", "cues": 2, "talk_s": 4.1},
                     {"tag": "S2", "name": "SPEAKER_03", "cues": 1, "talk_s": 0.5},
                     {"tag": "S?", "name": "FALANTE_?", "cues": 1, "talk_s": 0.3}],
        "cues": [
            cue(1, 2.034, 3.115, "S1", "Aí o que acontecia?", [1]),
            cue(2, 3.231, 6.25, "S1", "A control tentava bater no servidor FTP.", [2, 3]),
            cue(3, 6.3, 6.8, "S?", "Essa aqui.", [4]),
            cue(4, 6.3, 7.1, "S2", "aqui.", [5]),
            cue(5, 8.0, 9.5, None, "Texto sem falante.", [6]),
        ],
    }


def workflow_args():
    return {
        "schema_version": 1,
        "run_id": "20261006-153012-venda-zero-clientes",
        "run_dir": RUN,
        "objective_path": f"{RUN}/objective.md",
        "roles_dir": "C:/Users/digom/video-analysis/roles",
        "roles_mode": "inline",
        "language": "pt-BR",
        "options": {"max_batches": 2, "max_parallel_videos": 3, "analyst_effort": "high", "curator_effort": "high"},
        "combined_exists": False,
        "videos": [{
            "slug": "venda-zero-clientes", "title": "venda_zero_clientes", "duration": "00:07:21", "n_frames": 86,
            "batches_dir": "C:/Users/digom/video-analysis/cache/venda-zero-clientes-1a2b3c4d/batches-1b2c3d4e",
            "out_dir": f"{RUN}/venda-zero-clientes",
            "batch_images": [20, 22, 21, 23], "start_batch": 1,
            "ledger_exists": False, "start_prompt_exists": False, "final_exists": False,
        }],
    }


def mutated(base, fn):
    doc = copy.deepcopy(base)
    fn(doc)
    return doc


def assert_rejected(errors, *needles):
    assert errors, "expected validation errors"
    text = "\n".join(errors)
    for needle in needles:
        assert needle in text, f"{needle!r} not in:\n{text}"


# --- frames ------------------------------------------------------------------------------------

def test_valid_frames_doc():
    assert K.check_frames(frames_doc()) == []


def test_frames_without_revisit_and_last_captured_at_end():
    doc = frames_doc()
    doc["frames"] = doc["frames"][:3]
    doc["frames"][2].update(end=30.0, captured_at=30.0, sample=60, file="F0003_000030.jpg")
    doc["n_samples"], doc["n_unique"] = 61, 3
    assert K.check_frames(doc) == []


def test_frames_more_than_9999_use_five_digits():
    doc = frames_doc()
    n = 10001
    doc["range"], doc["n_samples"], doc["video"]["duration_s"] = [0.0, float(n)], 2 * n, float(n)
    doc["frames"] = [{**doc["frames"][1], "id": f"F{i:05d}", "file": f"F{i:05d}_{(i - 1) // 3600:02d}{(i - 1) % 3600 // 60:02d}{(i - 1) % 60:02d}.jpg",
                      "start": float(i - 1), "end": float(i), "captured_at": float(i - 1), "sample": 2 * (i - 1),
                      "same_as": None} for i in range(1, n + 1)]
    doc["n_unique"] = n
    assert K.check_frames(doc) == []


@pytest.mark.parametrize("fn, needles", [
    (lambda d: d["frames"][1].update(start=10.5), ["gap"]),
    (lambda d: d["frames"][2].update(start=15.0), ["overlap"]),
    (lambda d: d["frames"][2].update(id="F0004"), ["sequential"]),
    (lambda d: d["frames"][1].update(captured_at=15.5, sample=31), ["must be < end"]),
    (lambda d: d["frames"][1].update(captured_at=9.5, sample=19), ["captured_at 9.5 < start 10.0"]),
    (lambda d: d["frames"][3].update(same_as="F0004"), ["does not reference an earlier frame"]),
    (lambda d: d["frames"][3].update(same_as="F0009"), ["does not reference an earlier frame"]),
    (lambda d: d["frames"][3].update(file="F0004_000023.jpg"), ["revisit file"]),
    (lambda d: d["frames"][1].update(file="F0002_000010.jpg"), ["file 'F0002_000010.jpg' != 'F0002_000011.jpg'"]),
    (lambda d: d["frames"][3].update(end=29.5), ["last end 29.5 != range end 30.0"]),
    (lambda d: d["frames"][0].update(start=0.5), ["first start 0.5 != range start 0.0"]),
    (lambda d: d["frames"][2].update(kind="scroll"), ["kind 'scroll'"]),
    (lambda d: d.update(n_unique=4), ["n_unique"]),
    (lambda d: d["frames"][1].update(sample=21), ["sample/sample_fps"]),
    (lambda d: d["frames"][1].update(sample=60), ["n_samples"]),
    (lambda d: d["frames"][1].update(captured_at=11.0004), ["more than 3 decimals"]),
    (lambda d: d["frames"][1]["change"].update(bbox=[0, 0, 2, 1]), ["change.bbox"]),
    (lambda d: d["frames"][1].update(bytes=-1), ["bytes"]),
    (lambda d: d["frames"][1].pop("tokens"), ["missing key 'tokens'"]),
    (lambda d: d.pop("density"), ["missing key 'density'"]),
    (lambda d: d.update(schema="va.frames/2"), ["schema"]),
    (lambda d: d.update(frames_dir="C:\\Users\\digom\\x"), ["frames_dir"]),
    (lambda d: d["video"].update(path="relative/video.mp4"), ["video.path"]),
    (lambda d: d.update(output_size=[1567, 882]), ["output_size"]),
    (lambda d: d.update(analysis_size=[641, 360]), ["analysis_size"]),
    (lambda d: d.update(range=[0.0, 31.0]), ["past the video duration"]),
    (lambda d: d.update(frames=[]), ["non-empty list"]),
])
def test_broken_frames_doc(fn, needles):
    assert_rejected(K.check_frames(mutated(frames_doc(), fn)), *needles)


def test_check_frames_rejects_non_object():
    assert K.check_frames([]) == ["frames.json: expected an object"]


# --- contiguity ----------------------------------------------------------------------------------

def test_check_contiguity_generic():
    items = [{"a": 0.0, "b": 1.5}, {"a": 1.5, "b": 2.0}, {"a": 2.0, "b": 2.0}]
    assert K.check_contiguity(items, [0.0, 2.0], label="x", start_key="a", end_key="b") == []
    assert K.check_contiguity(items[:2], None, start_key="a", end_key="b") == []
    assert_rejected(K.check_contiguity([{"start": 0, "end": 1}, {"start": 1, "end": 1}, {"start": 1, "end": 2}]), "must be >")
    assert_rejected(K.check_contiguity([{"start": 0, "end": 2}, {"start": 1, "end": 3}], [0, 3]), "overlap")
    assert_rejected(K.check_contiguity([{"start": 0, "end": 1}], [0, 2]), "range end")
    assert_rejected(K.check_contiguity([]), "non-empty")
    assert_rejected(K.check_contiguity([{"start": "0", "end": 1}]), "must be numbers")
    assert_rejected(K.check_contiguity([{"start": 0, "end": 1}], [0]), "range must be")


def test_check_contiguity_compares_in_ms():
    items = [{"start": 0.0, "end": 0.1 + 0.2}, {"start": 0.3, "end": 1.0}]  # 0.30000000000000004
    assert K.check_contiguity(items, [0, 1]) == []


# --- cues ----------------------------------------------------------------------------------------

def test_valid_cues_doc():
    assert K.check_cues(cues_doc()) == []
    assert K.check_cues(cues_doc(), duration_s=9.5) == []


@pytest.mark.parametrize("fn, needles", [
    (lambda d: d["cues"][1].update(id="C0003"), ["sequential"]),
    (lambda d: d["cues"][0].update(end=2.034), ["must be > start"]),
    (lambda d: d["cues"][3].update(start=6.2), ["out of order"]),
    (lambda d: d["cues"][1].update(spk="S7"), ["not a speaker tag"]),
    (lambda d: d["cues"][1].update(text="dois  espaços"), ["collapsed whitespace"]),
    (lambda d: d["cues"][1].update(text="linha\nquebrada"), ["collapsed whitespace"]),
    (lambda d: d["cues"][1].update(text="espaço\u00a0duro"), ["invisible"]),
    (lambda d: d["cues"][1].update(text="zero\u200bwidth"), ["invisible"]),
    (lambda d: d["cues"][1].update(text="  "), ["non-empty string"]),
    (lambda d: (d["cues"][0].update(spk="S2"), d["cues"][1].update(spk="S2"), d["cues"][3].update(spk="S1"),
                d["speakers"][0].update(cues=1), d["speakers"][1].update(cues=2)), ["first appearance"]),
    (lambda d: d["speakers"][0].update(cues=5), ["cues 5 != 2"]),
    (lambda d: d["speakers"].append({"tag": "S1", "name": "dup", "cues": 0, "talk_s": 0}), ["duplicate tag"]),
    (lambda d: d["speakers"][1].update(tag="S03"), ["must be S1, S2"]),
    (lambda d: d["cues"][0].update(approx="no"), ["approx"]),
    (lambda d: d["cues"][0].update(src=[]), ["src"]),
    (lambda d: d["cues"][0].update(utt="18"), ["utt"]),
    (lambda d: d["vtt"].update(style="srt"), ["vtt.style"]),
    (lambda d: d["vtt"].update(sha1="xyz"), ["vtt.sha1"]),
    (lambda d: d["vtt"].update(path="C:\\x.vtt"), ["vtt.path"]),
    (lambda d: d["stats"].update(raw=-1), ["stats.raw"]),
    (lambda d: d.pop("speakers"), ["missing key 'speakers'"]),
    (lambda d: d.update(schema="va.frames/1"), ["schema"]),
])
def test_broken_cues_doc(fn, needles):
    assert_rejected(K.check_cues(mutated(cues_doc(), fn)), *needles)


def test_cues_past_duration():
    assert_rejected(K.check_cues(cues_doc(), duration_s=9.0), "past the duration")


# --- workflow_args ---------------------------------------------------------------------------------

def test_valid_workflow_args():
    assert K.check_workflow_args(workflow_args()) == []


def test_workflow_args_variants_still_valid():
    def resumed(a):
        a["roles_mode"] = "agents"
        a["options"].update(max_batches=None, max_parallel_videos=6, analyst_effort="medium", curator_effort="max",
                            curation="batches")
        a["combined_exists"] = True
        v = a["videos"][0]
        v.update(start_batch=5, ledger_exists=True, start_prompt_exists=True, final_exists=True)
        a["videos"].append({**copy.deepcopy(v), "slug": "dias-estoque", "out_dir": f"{RUN}/dias-estoque",
                            "n_frames": 20, "batch_images": [12, 8], "start_batch": 1})
    assert K.check_workflow_args(mutated(workflow_args(), resumed)) == []
    unc = mutated(workflow_args(), lambda a: a["videos"][0].update(batches_dir="//server/share/cache/b"))
    assert K.check_workflow_args(unc) == []


def test_workflow_typeddicts_match_example():
    a = workflow_args()
    assert set(a) == set(K.WorkflowArgs.__required_keys__)
    assert set(a["options"]) == set(K.WorkflowOptions.__required_keys__)
    assert set(a["videos"][0]) == set(K.WorkflowVideo.__required_keys__)


@pytest.mark.parametrize("fn, needles", [
    (lambda a: a.update(run_dir="C:\\Users\\digom\\video-analysis\\runs\\20261006-153012-venda-zero-clientes"), ["run_dir"]),
    (lambda a: a.update(objective_path="runs/x/objective.md"), ["objective_path"]),
    (lambda a: a.update(roles_dir="C:/Users\\digom/roles"), ["roles_dir"]),
    (lambda a: a["videos"][0].update(batches_dir="cache/x"), ["batches_dir"]),
    (lambda a: a["videos"][0].update(out_dir="C:\\out"), ["out_dir"]),
    (lambda a: a["videos"][0].update(out_dir="C:/Users/digom/elsewhere/venda"), ["not inside run_dir"]),
    (lambda a: a["videos"][0].update(batch_images=[20, 0, 21]), ["batch_images"]),
    (lambda a: a["videos"][0].update(batch_images=[20, -1]), ["batch_images"]),
    (lambda a: a["videos"][0].update(batch_images=[20, 2.5]), ["batch_images"]),
    (lambda a: a["videos"][0].update(batch_images=[True, 20]), ["batch_images"]),
    (lambda a: a["videos"][0].update(batch_images=[]), ["batch_images"]),
    (lambda a: a["videos"][0].update(batch_images="20,20"), ["batch_images"]),
    (lambda a: a["videos"][0].update(n_frames=80), ["sum(batch_images) 86 > n_frames 80"]),
    (lambda a: a["videos"][0].update(start_batch=0), ["start_batch"]),
    (lambda a: a["videos"][0].update(start_batch=6), ["start_batch 6 > number of batches + 1"]),
    (lambda a: a["videos"][0].update(ledger_exists="no"), ["ledger_exists"]),
    (lambda a: a["videos"][0].update(duration="7:21"), ["duration"]),
    (lambda a: a["videos"][0].update(slug="Venda Zero"), ["slug"]),
    (lambda a: a["videos"].append(copy.deepcopy(a["videos"][0])), ["duplicate slug", "duplicate out_dir"]),
    (lambda a: a["videos"][0].update(cache_dir=a["videos"][0].pop("batches_dir")), ["'cache_dir' was renamed to 'batches_dir'"]),
    (lambda a: a["videos"][0].update(batch_frames=a["videos"][0].pop("batch_images")), ["'batch_frames' was renamed to 'batch_images'"]),
    (lambda a: a["videos"][0].update(extra=1), ["unknown key 'extra'"]),
    (lambda a: a.update(script_path="C:/x.js"), ["unknown key 'script_path'"]),
    (lambda a: a["videos"][0].pop("title"), ["missing key 'title'"]),
    (lambda a: a.pop("objective_path"), ["missing key 'objective_path'"]),
    (lambda a: a.update(schema_version=2), ["schema_version"]),
    (lambda a: a.update(schema_version=True), ["schema_version"]),
    (lambda a: a.update(roles_mode="agent"), ["roles_mode"]),
    (lambda a: a.update(run_id="other"), ["basename of run_dir"]),
    (lambda a: a.update(language=""), ["language"]),
    (lambda a: a.update(combined_exists="false"), ["combined_exists"]),
    (lambda a: a["options"].update(analyst_effort="ultra"), ["analyst_effort"]),
    (lambda a: a["options"].update(curator_effort=None), ["curator_effort"]),
    (lambda a: a["options"].update(curation="lotes"), ["options.curation"]),
    (lambda a: a["options"].update(max_parallel_videos=0), ["max_parallel_videos"]),
    (lambda a: a["options"].update(max_parallel_videos=7), ["max_parallel_videos"]),
    (lambda a: a["options"].update(max_batches=-1), ["max_batches"]),
    (lambda a: a["options"].pop("max_batches"), ["missing key 'max_batches'"]),
    (lambda a: a["options"].update(paralelo=2), ["unknown key 'paralelo'"]),
    (lambda a: a.update(videos=[]), ["videos"]),
    (lambda a: a.update(options=[]), ["options"]),
])
def test_broken_workflow_args(fn, needles):
    assert_rejected(K.check_workflow_args(mutated(workflow_args(), fn)), *needles)


def test_check_workflow_args_rejects_non_object():
    assert K.check_workflow_args("{}") == ["workflow_args: expected an object"]


def test_is_abs_posix():
    good = ["C:/Users/digom", "c:/", "//server/share/x"]
    bad = ["C:\\Users", "C:/a\\b", "/c/Users/digom", "Users/digom", "C:", "///x", "", None, 3]
    assert all(K.is_abs_posix(p) for p in good)
    assert not any(K.is_abs_posix(p) for p in bad)


def test_contract_constants():
    assert K.FRAME_KINDS == ("first", "settled", "motion", "heartbeat", "final")
    assert set(K.CUE_STYLES) == {"whisperx", "teams", "plain", "mixed"}
    assert {"ok", "workflow_args", "estimate", "env", "script_path", "run_id"} <= K.PrepareOutput.__required_keys__
    assert {"code", "detail"} == set(K.PrepareErrorBody.__required_keys__)
    assert {"vid", "cache_hit", "timing_s", "title", "batches"} <= K.PrepareVideo.__required_keys__
    assert "reencoded" in K.FrameEntry.__optional_keys__
