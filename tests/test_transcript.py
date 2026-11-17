"""va/transcript.py: the speaker count of the request, the keys, the plan of a video without subtitles, the
checks of the driver's JSON (the notebook's carregar_bruto), the cues (speaker changes, pauses, limits), the
VTT the existing parser reads back, the subtitles written next to the video (never over a person's file) and
make() with an in-process stand-in for transcribe.py. Nothing here needs WhisperX."""
import errno
import math
import os
import shutil
import time
from pathlib import Path

import pytest

from va import TRANSCRIBE_DEFAULTS, TRANSCRIBE_VERSION, cache, discover, transcript as T, vtt
from va.common import read_json, write_json

STATUS = {"ok": True, "state": "ok", "device": "cpu", "default_model": "large-v3-turbo", "token": True,
          "python": "C:/env/Scripts/python.exe", "nltk_data": "C:/env/nltk_data"}


# --- the speaker count --------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, spec, bounds", [
    ("3", "3", (3, 3)), ("1", "1", (1, 1)), (" 2-4 ", "2-4", (2, 4)), ("2 a 4", "2-4", (2, 4)), ("3–5", "3-5", (3, 5)),
    ("4+", "4+", (4, None)), ("4 ou mais", "4+", (4, None)), ("5 or more", "5+", (5, None)), ("3 pessoas", "3", (3, 3)),
    ("Não sei", "auto", (None, None)), ("AUTO", "auto", (None, None)), ("?", "auto", (None, None)),
    ("1+", "auto", (None, None)), ("2-2", "2", (2, 2)),
])
def test_canon_speakers(value, spec, bounds):
    assert T.canon_speakers(value) == spec and T.speakers_bounds(spec) == bounds


@pytest.mark.parametrize("value", ["", "0", "21", "5-2", "dois", "2,5", "1-30"])
def test_canon_speakers_refuses(value):
    with pytest.raises(ValueError):
        T.canon_speakers(value)


def test_num_speakers_per_video():
    opt = T.parse_num_speakers("3; v2 = auto ; Reunião de Teste.MP4=2 a 4")
    assert opt == {"all": "3", "videos": {"v2": "auto", "reunião de teste.mp4": "2-4"}}
    assert T.speakers_for(opt, "v1", "C:/x/aula.mp4", "aula") == "3"                     # the value for all
    assert T.speakers_for(opt, "v2", "C:/x/a.mp4", "a") == "auto"                         # by vid
    assert T.speakers_for(opt, "v3", "C:/x/Reunião de Teste.mp4", "reuniao-de-teste") == "2-4"   # by file name
    assert T.speakers_for(T.parse_num_speakers("reuniao-de-teste=5"), "v9", "C:/y.mp4", "reuniao-de-teste") == "5"
    assert T.speakers_for(None, "v1", "C:/x.mp4", "x") is None
    only = T.parse_num_speakers("v1=2")
    assert T.speakers_for(only, "v2", "C:/b.mp4", "b") is None                           # not given for v2
    videos = [("v1", "C:/x/aula.mp4", "aula"), ("v2", "C:/x/b.mp4", "b")]
    assert T.unknown_speaker_keys(T.parse_num_speakers("v1=2; v7=3; outra.mp4=2; aula=4"), videos) == ["v7", "outra.mp4"]
    # back to text, as run.json and objective.md keep it
    assert T.format_num_speakers(opt) == "3; v2=auto; reunião de teste.mp4=2-4"
    assert T.format_num_speakers(T.parse_num_speakers("4 a 12")) == "4-12" and T.format_num_speakers(None) is None
    assert T.parse_num_speakers(T.format_num_speakers(opt)) == opt


def test_model_and_language():
    assert [T.canon_model(m) for m in ("turbo", "Large", "large-v3-turbo", "MEDIUM", "large_v2")] == \
        ["large-v3-turbo", "large-v3", "large-v3-turbo", "medium", "large-v2"]
    assert [T.canon_language(x) for x in ("pt-BR", "EN", "auto", "Automático", "es_419")] == ["pt", "en", "auto", "auto", "es"]
    for bad, fn in (("gigante", T.canon_model), ("português", T.canon_language), ("p", T.canon_language)):
        with pytest.raises(ValueError):
            fn(bad)


def test_keys():
    """The ASR key ignores the speakers and the rendering (hours of CPU are not redone for them); the
    diarization key follows the speakers and the mode."""
    a = T.asr_key("aula-1a2b3c4d", "large-v3-turbo", "auto")
    assert a == T.asr_key("aula-1a2b3c4d", "large-v3-turbo", "auto") and len(a) == 8
    assert len({a, T.asr_key("aula-1a2b3c4d", "large-v3", "auto"), T.asr_key("aula-1a2b3c4d", "large-v3-turbo", "pt"),
                T.asr_key("aula-99999999", "large-v3-turbo", "auto")}) == 4
    d = {T.diar_key("3", "pyannote"), T.diar_key("2", "pyannote"), T.diar_key("auto", "pyannote"), T.diar_key("3", "none"),
         T.diar_key("1", "single")}
    assert len(d) == 5
    assert T.stage_dir("C:/cache/v", a) == Path("C:/cache/v") / f"transcript-{a}"


# --- asr.json and diar-<d8>.json ---------------------------------------------------------------------------

def asr_doc(segments, **fields):
    return {"schema": "va.asr/1", "complete": True, "whisperx": "3.8.6", "model": "large-v3-turbo", "device": "cpu",
            "language": "pt", "language_probability": 0.97, "language_requested": "auto", "aligned": True,
            "segments": segments, **fields}


def test_load_asr_normalizes(tmp_path):
    p = tmp_path / "asr.json"
    write_json(p, asr_doc([{"start": 1, "end": None, "text": None, "words": [{"word": "a", "start": 1, "end": 2, "speaker": "X"}]},
                           {"start": 3.5, "end": 4.0, "text": "b"}]))
    doc = T.load_asr(p)
    s0, s1 = doc["segments"]
    assert s0["start"] == 1.0 and isinstance(s0["start"], float) and "end" not in s0 and s0["text"] == ""
    assert s0["words"] == [{"word": "a", "start": 1.0, "end": 2.0}]           # the speakers come from diar-<d8>.json
    assert s1["words"] == []


@pytest.mark.parametrize("text, needle", [
    ('{"complete": true, "segments": [{"start": NaN, "end": 1}]}', "not a finite number"),
    ('{"complete": true, "segments": [{"start": Infinity}]}', "not a finite number"),
    ('{"complete": true, "segments": [{"start": "1"}]}', "is not a number"),
    ('{"complete": true, "segments": [{"start": true}]}', "is not a number"),
    ('{"complete": true, "segments": [{"text": 3}]}', "is not text"),
    ('{"complete": true, "segments": [{"words": "a b"}]}', "words is not a list of objects"),
    ('{"complete": true, "segments": {"a": 1}}', "'segments' is not a list"),
    ('{"complete": false, "segments": []}', "incomplete"),
    ('[]', "the root is not an object"),
    ('{"complete": tr', "Expecting"),
])
def test_load_asr_refuses(tmp_path, text, needle):
    p = tmp_path / "asr.json"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(T.TranscriptError) as exc:
        T.load_asr(p)
    assert needle in str(exc.value) and "transcribe.py left no valid" in str(exc.value)


def test_load_diar(tmp_path):
    p = tmp_path / "diar.json"
    write_json(p, {"complete": True, "diarized": True, "turns": [[5, 6, "SPEAKER_01"], [0, 2.5, "SPEAKER_00"]]})
    assert T.load_diar(p)["turns"] == [[0.0, 2.5, "SPEAKER_00"], [5.0, 6.0, "SPEAKER_01"]]
    write_json(p, {"complete": True, "turns": [[5, 4, "S"]]})
    with pytest.raises(T.TranscriptError):
        T.load_diar(p)


# --- cues and the VTT ---------------------------------------------------------------------------------------

def W(word, start=None, end=None):
    w = {"word": word}
    if start is not None:
        w["start"], w["end"] = start, end
    return w


SEGMENTS = [
    {"start": 0.5, "end": 3.5, "text": "Olá a todos. Tudo bem?",
     "words": [W("Olá", 0.5, 0.9), W("a", 1.0, 1.1), W("todos.", 1.1, 1.6), W("Tudo", 2.8, 3.1), W("bem?", 3.1, 3.5)]},
    {"start": 5.0, "end": 6.6, "text": "Sim, 2024 foi bom.",
     "words": [W("Sim,", 5.0, 5.3), W("2024"), W("foi", 5.9, 6.1), W("bom.", 6.1, 6.6)]},
    {"start": 8.0, "end": 9.0, "text": "Sem palavras alinhadas."},
]
TURNS = [[0.0, 1.8, "SPEAKER_00"], [2.5, 3.6, "SPEAKER_01"], [4.9, 9.5, "SPEAKER_00"]]


def test_build_cues_speakers_and_inheritance():
    cues = T.build_cues({"language": "pt", "segments": SEGMENTS}, TURNS, "pyannote")
    assert [(c["speaker"], c["text"]) for c in cues] == [
        ("SPEAKER_00", "Olá a todos."), ("SPEAKER_01", "Tudo bem?"),     # the speaker changes mid-segment
        ("SPEAKER_00", "Sim, 2024 foi bom."),                            # "2024" has no time: it joins its neighbours
        ("SPEAKER_00", "Sem palavras alinhadas.")]                       # a segment without words keeps its own times
    assert (cues[0]["start"], cues[0]["end"]) == (0.5, 1.6) and (cues[3]["start"], cues[3]["end"]) == (8.0, 9.0)


def test_build_cues_modes_and_unknown_speaker():
    asr = {"language": "pt", "segments": SEGMENTS[:1]}
    assert {c["speaker"] for c in T.build_cues(asr, None, "single")} == {"SPEAKER_00"}
    none = T.build_cues(asr, None, "none")
    assert {c["speaker"] for c in none} == {None} and [c["text"] for c in none] == ["Olá a todos.", "Tudo bem?"]  # the pause
    far = T.build_cues(asr, [[100.0, 101.0, "SPEAKER_03"]], "pyannote")
    assert {c["speaker"] for c in far} == {"FALANTE_?"}                  # no turn near the words
    near = T.build_cues(asr, [[1.7, 2.7, "SPEAKER_02"]], "pyannote")     # gaps under 1 s: the nearest turn
    assert {c["speaker"] for c in near} == {"SPEAKER_02"}


def test_build_cues_limits_and_languages():
    words = [W(f"palavra{i}", i * 0.5, i * 0.5 + 0.4) for i in range(40)]  # 20 s of speech without a pause
    cues = T.build_cues({"language": "pt", "segments": [{"start": 0, "end": 20, "text": "x", "words": words}]}, None, "none")
    assert len(cues) >= 3 and all(c["end"] - c["start"] <= TRANSCRIBE_DEFAULTS["cue_max_s"] for c in cues)
    assert all(len(c["text"]) <= TRANSCRIBE_DEFAULTS["cue_max_chars"] for c in cues)
    assert " ".join(c["text"] for c in cues) == " ".join(w["word"] for w in words)
    ja = T.build_cues({"language": "ja", "segments": [{"start": 0, "end": 1, "words": [W("こん", 0, .3), W("にちは", .3, .8)]}]},
                      None, "none")
    assert ja[0]["text"] == "こんにちは"


def test_render_vtt_is_read_back_by_the_parser():
    """The VTT this module writes is the WhisperX style the cues stage already reads: the language from the
    header, the speakers in order of appearance, the NOTE skipped, exact times (no ≈ repair)."""
    cues = T.build_cues({"language": "pt", "segments": SEGMENTS}, TURNS, "pyannote")
    meta = {"version": TRANSCRIBE_VERSION, "whisperx": "3.8.6", "model": "large-v3-turbo", "language": "pt",
            "language_requested": "auto", "speakers": "2", "mode": "pyannote", "diarized": True, "reason": None,
            "asr": "aaaaaaaa", "diar": "dddddddd", "render": "r1"}
    text = T.render_vtt(cues, "pt", meta)
    assert text.startswith("WEBVTT\nLanguage: pt\n\nNOTE\nLegenda gerada automaticamente pela skill video-analysis")
    assert "\n00:00:00.500 --> 00:00:01.600\nSPEAKER_00: Olá a todos.\n" in text and "-->" not in text.split("NOTE")[1].split("\n\n")[0]
    doc = vtt.parse_vtt_bytes(text.encode("utf-8"))
    assert doc["vtt"]["style"] == "whisperx" and doc["vtt"]["language"] == "pt"
    assert [(s["tag"], s["name"]) for s in doc["speakers"]] == [("S1", "SPEAKER_00"), ("S2", "SPEAKER_01")]
    assert [c["text"] for c in doc["cues"]] == ["Olá a todos.", "Tudo bem?", "Sim, 2024 foi bom.", "Sem palavras alinhadas."]
    assert not any(c.get("approx") for c in doc["cues"])
    named = vtt.parse_vtt_bytes(text.encode("utf-8"), {"speaker_names": "SPEAKER_00=Ana"})
    assert [(s["tag"], s["name"]) for s in named["speakers"]][0] == ("S1", "Ana")


def test_note_says_how_many_speakers_were_found():
    """The NOTE's sentence gives the speakers pyannote found, with the count asked for: "falantes: 4-12" alone
    (the range of the request) read like a result."""
    base = {"version": TRANSCRIBE_VERSION, "whisperx": "3.8.6", "model": "large-v3-turbo", "language": "pt"}

    def human(**meta):
        return T.note_lines({**base, **meta})[1]
    assert "6 falantes (pedido: 4-12)" in human(mode="pyannote", diarized=True, speakers="4-12", speakers_found=6)
    assert "3 falantes (número detectado automaticamente)" in human(mode="pyannote", diarized=True, speakers="auto",
                                                                   speakers_found=3)
    assert "1 falante)" in human(mode="single", diarized=True, speakers="1", speakers_found=1)
    assert "falantes não separados (sem token" in human(mode="none", diarized=False, speakers="auto")
    assert "falantes não separados)" in human(mode="pyannote", diarized=False, speakers="3", reason="no_access")


def test_render_vtt_cleans_text():
    text = T.render_vtt([{"start": 1, "end": 1, "speaker": None, "text": "a --> b\nc"}, {"start": 2, "end": 3, "text": " "}],
                        None, {"version": "x"})
    assert "Language:" not in text and "\n00:00:01.000 --> 00:00:01.200\na -> b c\n" in text   # minimum length; one line
    assert text.count(" --> ") == 1


# --- the subtitles next to the video ------------------------------------------------------------------------

META = {"version": TRANSCRIBE_VERSION, "model": "large-v3-turbo", "language_requested": "auto", "speakers": "3",
        "mode": "pyannote", "diarized": True, "reason": None}


def test_sidecar_is_created_once_and_never_over_a_persons_file(tmp_path):
    video, vdir = tmp_path / "vids" / "Reunião.mp4", tmp_path / "cache" / "reuniao-1"
    video.parent.mkdir()
    vdir.mkdir(parents=True)
    video.write_bytes(b"v")
    text = T.render_vtt([], "pt", META)
    path, warn = T.export_sidecar(text, video, vdir)
    assert path == video.with_name("Reunião.vtt") and warn is None and path.read_text(encoding="utf-8") == text
    assert T.read_note(path)["speakers"] == "3" and T.own_sidecar(path, vdir)["mode"] == "pyannote"
    assert read_json(vdir / T.EXPORT_JSON)["path"].endswith("/vids/Reunião.vtt")
    path2, warn = T.export_sidecar(text + "\n", video, vdir)                  # already there: kept
    assert path2 is None and "appeared next to the video" in warn and path.read_text(encoding="utf-8") == text
    new = T.render_vtt([], "pt", {**META, "speakers": "2"})
    path3, warn = T.export_sidecar(new, video, vdir, replace=True)            # ours, untouched: replaced
    assert path3 == path and warn is None and T.own_sidecar(path, vdir)["speakers"] == "2"
    path.write_text(path.read_text(encoding="utf-8") + "\n00:00:09.000 --> 00:00:10.000\nnome corrigido\n", encoding="utf-8")
    assert T.read_note(path) is not None and T.own_sidecar(path, vdir) is None   # edited by the person: no longer ours
    path4, warn = T.export_sidecar(text, video, vdir, replace=True)
    assert path4 is None and "changed since the skill wrote it" in warn and "nome corrigido" in path.read_text(encoding="utf-8")
    user = tmp_path / "vids" / "outra.vtt"
    user.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nolá\n", encoding="utf-8")
    assert T.read_note(user) is None and T.own_sidecar(user, vdir) is None


def test_sidecar_stays_ours_after_gc_removes_the_cache(tmp_path):
    """--gc removes the video's cache folder, transcript-export.json with it: the NOTE's own hash still says
    the skill wrote the subtitles and nobody edited them, so another model still replaces them; an edit, to a
    cue, to the header or to the NOTE's settings, still makes them the person's."""
    video, vdir = tmp_path / "aula.mp4", tmp_path / "cache"
    vdir.mkdir()
    video.write_bytes(b"v")
    cues = T.build_cues({"language": "pt", "segments": SEGMENTS}, TURNS, "pyannote")
    path, warn = T.export_sidecar(T.render_vtt(cues, "pt", META), video, vdir)
    assert warn is None
    shutil.rmtree(vdir)                                                         # what cache.gc_cache does
    own = T.own_sidecar(path, vdir)
    assert own is not None and own["model"] == "large-v3-turbo"
    p = plan(tmp_path, {"whisper_model": "large-v3"}, vtt=path, own=own)
    assert (p["state"], p["replace"], p["model"]) == ("pending", True, "large-v3")
    assert plan(tmp_path, vtt=path, own=own)["state"] == "file"                 # unchanged: reused (and copied)
    new = T.render_vtt(cues, "pt", {**META, "model": "large-v3"})
    path2, warn = T.export_sidecar(new, video, vdir, replace=True)
    assert path2 == path and warn is None and T.own_sidecar(path, vdir)["model"] == "large-v3"
    original = path.read_bytes()
    for old, edit in (("Olá a todos.", "Olá a todas."), ("Language: pt", "Language: en"),
                      ('"speakers":"3"', '"speakers":"4"')):
        path.write_bytes(original.replace(old.encode("utf-8"), edit.encode("utf-8")))
        assert path.read_bytes() != original
        for d in (vdir, tmp_path / "gone"):                                     # with or without the record
            assert T.read_note(path) is not None and T.own_sidecar(path, d) is None, edit
        assert plan(tmp_path, {"whisper_model": "large-v3-turbo"}, vtt=path, own=None)["state"] == "file"


def test_sidecar_in_a_folder_that_refuses_writes(tmp_path, monkeypatch):
    video, vdir = tmp_path / "v.mp4", tmp_path / "cache"
    vdir.mkdir()

    def refuse(*a, **k):
        raise PermissionError("somente leitura")
    monkeypatch.setattr(T.os, "open", refuse)
    path, warn = T.export_sidecar("WEBVTT\n", video, vdir)
    assert path is None and "could not write the subtitles next to the video" in warn and "somente leitura" in warn


class FullFolder:
    """The video's folder is full (an external drive, a share at quota) while the cache has room: a file opened
    there takes ``written`` bytes, then its write fails with ENOSPC (Windows writes nothing on a full disk; POSIX
    can write a part), or Ctrl+C arrives in the middle of it. ``on = False``: room again."""

    def __init__(self, monkeypatch, folder, written=0, ctrl_c=False):
        self.folder, self.written, self.ctrl_c, self.on, self.fds = os.path.normcase(str(folder)), written, ctrl_c, True, set()
        real_open, real_fdopen = os.open, os.fdopen

        def opener(path, flags, *a, **k):
            fd = real_open(path, flags, *a, **k)
            if self.on and os.path.normcase(os.path.dirname(os.path.abspath(path))) == self.folder:
                self.fds.add(fd)
            return fd

        def fdopen(fd, *a, **k):
            fh = real_fdopen(fd, *a, **k)
            if fd not in self.fds:
                return fh
            self.fds.discard(fd)
            return FullFile(fh, self)
        monkeypatch.setattr(T.os, "open", opener)
        monkeypatch.setattr(T.os, "fdopen", fdopen)


class FullFile:
    def __init__(self, fh, folder):
        self.fh, self.folder = fh, folder

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fh.close()

    def write(self, data):
        self.fh.write(data[:self.folder.written])
        self.fh.flush()
        raise KeyboardInterrupt if self.folder.ctrl_c else OSError(errno.ENOSPC, "No space left on device")


def test_sidecar_ctrl_c_in_the_middle_of_the_write_leaves_nothing(tmp_path, video, monkeypatch):
    FullFolder(monkeypatch, video.parent, written=100, ctrl_c=True)
    with pytest.raises(KeyboardInterrupt):
        T.export_sidecar(T.render_vtt([], "pt", META), video, tmp_path / "cache")
    assert os.listdir(video.parent) == ["aula.mp4"]                          # no aula.vtt cut at 100 bytes


# --- the plan -----------------------------------------------------------------------------------------------

def plan(tmp_path, opts=None, status=None, vtt=None, candidates=None, own=None, vid="v1"):
    return T.plan(video=tmp_path / "aula.mp4", video_dir=tmp_path / "cache", video_key="aula-1a2b3c4d", vid=vid,
                  slug="aula", opts=opts or {}, status=status if status is not None else STATUS, vtt=vtt,
                  candidates=candidates, own=own)


def finish_asr(p):
    sdir = Path(p["sdir"])
    sdir.mkdir(parents=True, exist_ok=True)
    write_json(sdir / "asr.json", asr_doc(SEGMENTS))
    cache.write_stage_json(sdir, T.STAGE, p["akey"], language="pt")


def finish_diar(p, **fields):
    write_json(Path(p["sdir"]) / f"diar-{p['dkey']}.json", {"complete": True, "key": p["dkey"], "diarized": True,
                                                           "speakers": p["speakers"], "mode": p["mode"], "turns": TURNS,
                                                           "created": cache.iso_now(), **fields})


def test_plan_states(tmp_path):
    p = plan(tmp_path)
    assert (p["state"], p["phase"], p["model"], p["language"], p["speakers"], p["mode"]) == \
        ("pending", "asr", "large-v3-turbo", "auto", "auto", "pyannote")
    assert p["needs_speakers"] is True and p["speakers_given"] is False
    given = plan(tmp_path, {"num_speakers": T.parse_num_speakers("3")})
    assert (given["needs_speakers"], given["speakers"], given["akey"]) == (False, "3", p["akey"])
    finish_asr(given)
    again = plan(tmp_path, {"num_speakers": T.parse_num_speakers("3")})
    assert (again["state"], again["phase"]) == ("pending", "diar")             # the ASR is reused
    finish_diar(again)
    assert plan(tmp_path, {"num_speakers": T.parse_num_speakers("3")})["state"] == "cached"
    assert plan(tmp_path, {"num_speakers": T.parse_num_speakers("2")})["phase"] == "diar"   # other count: only pyannote
    later = plan(tmp_path)                                                     # no count given: the earlier transcript
    assert (later["state"], later["speakers"], later["needs_speakers"]) == ("cached", "3", False)
    single = plan(tmp_path, {"num_speakers": T.parse_num_speakers("1")})
    assert (single["state"], single["mode"]) == ("cached", "single")         # one speaker: no pyannote at all
    other = plan(tmp_path, {"whisper_model": "medium", "audio_language": "pt"})
    assert (other["state"], other["phase"], other["model"], other["language"]) == ("pending", "asr", "medium", "pt")
    assert other["akey"] != p["akey"]


def test_plan_without_a_token_and_with_a_gpu(tmp_path):
    p = plan(tmp_path, status={**STATUS, "token": False})
    assert (p["mode"], p["needs_speakers"]) == ("none", False) and "no Hugging Face token" in p["detail"]
    gpu = plan(tmp_path, status={**STATUS, "device": "cuda", "default_model": "large-v3"})
    assert (gpu["device"], gpu["model"]) == ("cuda", "large-v3")


def test_plan_failed_diarization_is_tried_again(tmp_path):
    p = plan(tmp_path, {"num_speakers": T.parse_num_speakers("3")})
    finish_asr(p)
    finish_diar(p, diarized=False, reason="no_access", turns=[])
    assert plan(tmp_path, {"num_speakers": T.parse_num_speakers("3")})["phase"] == "diar"   # not done: tried again
    assert plan(tmp_path)["needs_speakers"] is True                                        # even without a count
    finish_diar(p, diarized=False, reason="error: MemoryError: Unable to allocate 1.62 GiB", turns=[])
    again = plan(tmp_path, {"num_speakers": T.parse_num_speakers("3")})
    assert (again["state"], again["phase"]) == ("pending", "diar")          # another failure: when the count is given
    later = plan(tmp_path)                                                  # without one: the last result, no question
    assert (later["state"], later["speakers"], later["needs_speakers"]) == ("cached", "3", False)


def test_plan_choice_with_similar_subtitles(tmp_path):
    p = plan(tmp_path, candidates=["C:/x/Reunião.vtt"])
    assert p["state"] == "choice" and p["candidates"] == ["C:/x/Reunião.vtt"] and p["needs_speakers"] is True
    told = plan(tmp_path, {"num_speakers": T.parse_num_speakers("2")}, candidates=["C:/x/Reunião.vtt"])
    assert told["state"] == "pending"                                          # the count given: transcribe


def test_plan_with_subtitles_and_when_ours_are_redone(tmp_path):
    vtt_path = tmp_path / "aula.vtt"
    assert plan(tmp_path, vtt=vtt_path, status={})["state"] == "file"          # a person's file: always used
    own = {**META, "speakers": "3", "model": "large-v3-turbo", "language_requested": "auto"}
    same = {"num_speakers": T.parse_num_speakers("3")}
    assert plan(tmp_path, same, vtt=vtt_path, own=own)["state"] == "file"
    redo = plan(tmp_path, {"num_speakers": T.parse_num_speakers("4")}, vtt=vtt_path, own=own)
    assert redo["state"] == "pending" and redo["replace"] is True
    assert plan(tmp_path, {"whisper_model": "large-v3"}, vtt=vtt_path, own=own)["replace"] is True
    no_token = {**own, "mode": "none", "diarized": False, "reason": "no_token", "speakers": "auto"}
    up = plan(tmp_path, vtt=vtt_path, own=no_token)                            # a token now: speakers this time
    assert up["replace"] is True and up["needs_speakers"] is True
    assert plan(tmp_path, vtt=vtt_path, own=no_token, status={**STATUS, "token": False})["state"] == "file"
    assert T.needs_status(None, "3", None, None) is False
    assert T.needs_status(own, None, None, None) is False                       # the usual repeat run: no subprocess
    assert T.needs_status(own, "3", None, None) and T.needs_status(no_token, None, None, None)


def test_plan_redoing_ours_keeps_the_model_and_language_they_were_made_with(tmp_path):
    # subtitles made with medium and a forced pt: another count, or a token added later, redoes only the speakers
    vtt_path = tmp_path / "aula.vtt"
    first = plan(tmp_path, {"num_speakers": T.parse_num_speakers("3"), "whisper_model": "medium",
                            "audio_language": "pt"})
    finish_asr(first)
    finish_diar(first)
    own = {**META, "speakers": "3", "model": "medium", "language_requested": "pt"}
    redo = plan(tmp_path, {"num_speakers": T.parse_num_speakers("4")}, vtt=vtt_path, own=own)
    assert (redo["state"], redo["phase"], redo["replace"]) == ("pending", "diar", True)
    assert (redo["model"], redo["language"], redo["akey"]) == ("medium", "pt", first["akey"])
    no_token = {**own, "mode": "none", "diarized": False, "reason": "no_token", "speakers": "auto"}
    up = plan(tmp_path, vtt=vtt_path, own=no_token)                            # a token now, no option given
    assert up["replace"] is True and (up["model"], up["language"], up["akey"]) == ("medium", "pt", first["akey"])
    assert up["state"] == "cached" or up["phase"] == "diar"
    other = plan(tmp_path, {"whisper_model": "large-v3"}, vtt=vtt_path, own=own)   # what the call gives wins
    assert (other["model"], other["language"], other["phase"]) == ("large-v3", "pt", "asr")
    auto = plan(tmp_path, {"audio_language": "auto"}, vtt=vtt_path, own=own)
    assert (auto["model"], auto["language"], auto["phase"]) == ("medium", "auto", "asr")
    odd = plan(tmp_path, {"num_speakers": T.parse_num_speakers("4")}, vtt=vtt_path,
               own={**own, "model": "gigante", "language_requested": "??"})   # a NOTE that makes no sense
    assert (odd["model"], odd["language"]) == ("large-v3-turbo", "auto")


def test_plan_redoing_ours_keeps_the_speaker_count_they_were_made_with(tmp_path):
    # only the model or the language changes: the count of the subtitles stays, with no question asked again
    vtt_path = tmp_path / "aula.vtt"
    no_tok = {**STATUS, "token": False}
    single = {**META, "speakers": "1", "mode": "single", "diarized": True, "model": "large-v3-turbo",
              "language_requested": "auto"}
    for opts in ({"audio_language": "en"}, {"whisper_model": "medium"}):
        for status in (no_tok, STATUS):
            p = plan(tmp_path, opts, status=status, vtt=vtt_path, own=single)
            assert (p["replace"], p["phase"], p["speakers"], p["mode"]) == (True, "asr", "1", "single")
            assert (p["needs_speakers"], p["speakers_given"], p["detail"]) == (False, False, None)
    three = {**single, "speakers": "3", "mode": "pyannote"}
    p = plan(tmp_path, {"whisper_model": "medium"}, vtt=vtt_path, own=three)
    assert (p["speakers"], p["mode"], p["needs_speakers"]) == ("3", "pyannote", False)
    rng = plan(tmp_path, {"whisper_model": "medium"}, vtt=vtt_path, own={**three, "speakers": "4-12"})
    assert (rng["speakers"], rng["needs_speakers"]) == ("4-12", False)
    told = plan(tmp_path, {"whisper_model": "medium", "num_speakers": T.parse_num_speakers("2")}, vtt=vtt_path,
                own=three)
    assert told["speakers"] == "2"                                             # what the call gives wins
    odd = plan(tmp_path, {"whisper_model": "medium"}, vtt=vtt_path, own={**three, "speakers": "muitos"})
    assert (odd["speakers"], odd["needs_speakers"]) == ("auto", True)        # a NOTE that makes no sense: asked
    # the count given without a token, a token added later: only the speakers, with that count
    first = plan(tmp_path, {"num_speakers": T.parse_num_speakers("3")}, status=no_tok)
    finish_asr(first)
    no_token = {**three, "mode": "none", "diarized": False, "reason": "no_token"}
    up = plan(tmp_path, vtt=vtt_path, own=no_token)
    assert (up["replace"], up["phase"], up["speakers"], up["mode"]) == (True, "diar", "3", "pyannote")
    assert (up["needs_speakers"], up["akey"]) == (False, first["akey"])


def test_view_and_estimate(monkeypatch):
    monkeypatch.setattr(T.sys, "platform", "win32")   # the measured CPU profile, not Apple silicon's
    p ={"state": "pending", "phase": "asr", "model": "large-v3-turbo", "device": "cpu", "vtt": None, "estimate_s": 9.0}
    v = T.view(p)
    assert v["state"] == "pending" and v["path"] is None and v["estimate_s"] == 9.0
    assert T.view({"state": "file", "vtt": Path("C:/x/a.vtt")}) == {**{k: None for k in v}, "state": "file", "path": "C:/x/a.vtt"}
    cpu = T.estimate_seconds(3600, "cpu", "large-v3-turbo", "asr", "pyannote")
    assert T.estimate_seconds(3600, "cpu", "large-v3", "asr", "pyannote") > cpu > T.estimate_seconds(3600, "cuda", "large-v3", "asr", "pyannote")
    # only the speakers again: on a CPU, about 40% of the whole (measured: 544 of 1482 s on a 9-min meeting)
    assert cpu / 3 < T.estimate_seconds(3600, "cpu", "large-v3-turbo", "diar", "pyannote") < cpu / 2
    assert T.estimate_seconds(3600, "cpu", "large-v3-turbo", "asr", "none") < cpu
    assert math.isclose(T.estimate_seconds(0, "cpu", "x", "asr", "single"), TRANSCRIBE_DEFAULTS["cost"]["cpu"]["startup_s"])


def test_estimate_on_apple_silicon(monkeypatch):
    """On macOS the "cpu" device is Apple silicon: the "apple" profile (ASR on the P-cores, alignment and
    speakers on MPS), much faster than the 4-core laptop's CPU numbers; elsewhere "cpu" stays the measured one."""
    apple = TRANSCRIBE_DEFAULTS["cost"]["apple"]
    assert apple["startup_s"] == 60.0 and apple["asr"]["large-v3-turbo"] == 0.35 and apple["asr"]["large-v3"] == 0.9
    assert (apple["asr_other"], apple["align"], apple["diarize"]) == (0.6, 0.06, 0.12)
    assert set(apple["asr"]) == set(TRANSCRIBE_DEFAULTS["cost"]["cpu"]["asr"])
    monkeypatch.setattr(T.sys, "platform", "win32")
    windows = T.estimate_seconds(3600, "cpu", "large-v3-turbo", "asr", "pyannote")
    monkeypatch.setattr(T.sys, "platform", "darwin")
    mac = T.estimate_seconds(3600, "cpu", "large-v3-turbo", "asr", "pyannote")
    assert mac == round(60.0 + 3600 * (0.35 + 0.06 + 0.12), 1) and mac < windows / 3
    assert T.estimate_seconds(3600, "mps", "large-v3-turbo", "asr", "pyannote") == mac
    assert T.estimate_seconds(3600, None, "large-v3-turbo", "asr", "pyannote") == mac
    assert T.estimate_seconds(3600, "cpu", "x", "asr", "none") == round(60.0 + 3600 * (0.6 + 0.06), 1)
    assert T.estimate_seconds(3600, "cpu", "large-v3-turbo", "diar", "pyannote") == round(60.0 + 3600 * 0.12, 1)


# --- make() with a stand-in for transcribe.py ---------------------------------------------------------------

class FakeDriver:
    """run_transcribe: writes asr.json and diar-<d8>.json as transcribe.py does, from ``diar`` and ``code``."""

    def __init__(self, diar=None, code=0, log="carregando large-v3-turbo\nfeito\n"):
        self.diar = diar if diar is not None else {"diarized": True, "turns": TURNS}
        self.code, self.log, self.calls = code, log, []

    def __call__(self, cmd, log_path, env=None):
        args = cmd[3:]
        self.calls.append(args)
        Path(log_path).write_text(self.log, encoding="utf-8")
        if self.code:
            return self.code
        opt = {a: args[i + 1] for i, a in enumerate(args) if a.startswith("--") and i + 1 < len(args)}
        out = Path(opt["--asr-out"])
        if not out.exists():
            write_json(out, asr_doc(SEGMENTS, model=opt["--model"], language_requested=opt["--language"]))
        if "--diar-out" in opt:
            write_json(opt["--diar-out"], {"complete": True, "key": opt["--diar-key"], "speakers": opt["--speakers"],
                                           "mode": "pyannote", "reason": None, "created": cache.iso_now(), **self.diar})
        return 0


@pytest.fixture
def video(tmp_path):
    v = tmp_path / "vids" / "aula.mp4"
    v.parent.mkdir()
    v.write_bytes(b"video")
    return v


def make(tmp_path, video, monkeypatch, driver, p=None, **kw):
    monkeypatch.setitem(cache.STAGE_OVERRIDES, "run_transcribe", driver)
    p = p or plan(tmp_path, {"num_speakers": T.parse_num_speakers("2")})
    return T.make(p=p, python="C:/env/python.exe", script="transcribe.py", video=video, video_dir=tmp_path / "cache",
                  duration_s=10.0, nltk_data="C:/env/nltk_data", vid="v1", **kw)


def test_make_transcribes_writes_next_to_the_video_and_reuses(tmp_path, video, monkeypatch):
    drv = FakeDriver()
    res = make(tmp_path, video, monkeypatch, drv)
    assert res["state"] == "made" and res["vtt"] == video.with_name("aula.vtt") and res["sidecar"].endswith("/vids/aula.vtt")
    assert (res["language"], res["speakers_found"], res["diarized"], res["mode"], res["n_cues"]) == ("pt", 2, True, "pyannote", 4)
    args = drv.calls[0]
    assert args[0] == str(video) and args[args.index("--speakers") + 1] == "2"
    assert args[args.index("--min-speakers") + 1] == "2" and args[args.index("--max-speakers") + 1] == "2"
    assert args[-1] == "--watch-stdin"
    sdir = Path(res["sdir"])
    assert read_json(sdir / "stage.json")["status"] == "complete" and read_json(sdir / "stage.json")["language"] == "pt"
    assert res["cache_vtt"].read_bytes() == res["vtt"].read_bytes()
    doc = vtt.parse_vtt_bytes(res["vtt"].read_bytes())
    assert doc["vtt"]["style"] == "whisperx" and len(doc["speakers"]) == 2
    # the same plan again: from the cache, no driver
    drv2 = FakeDriver()
    p = plan(tmp_path, {"num_speakers": T.parse_num_speakers("2")})
    assert p["state"] == "cached"
    res2 = make(tmp_path, video, monkeypatch, drv2, p=p)
    assert drv2.calls == [] and res2["state"] == "cached" and res2["sidecar"] is None
    assert any("appeared next to the video" in w for w in res2["warnings"])      # the sidecar is already there
    assert res2["vtt"] == res2["cache_vtt"]


def test_make_without_access_gives_subtitles_without_speakers(tmp_path, video, monkeypatch):
    res = make(tmp_path, video, monkeypatch, FakeDriver(diar={"diarized": False, "reason": "no_access", "turns": []}))
    assert (res["diarized"], res["mode"], res["reason"]) == (False, "none", "no_access")
    assert any("has no access to pyannote/speaker-diarization-community-1" in w for w in res["warnings"])
    text = res["vtt"].read_text(encoding="utf-8")
    assert "SPEAKER_" not in text.split("NOTE")[1].split("\n\n", 1)[1] and T.read_note(res["vtt"])["reason"] == "no_access"


def test_make_retries_a_failed_diarization_when_the_count_is_given_again(tmp_path, video, monkeypatch):
    # pyannote failed for a passing cause (a proxy down, out of memory): subtitles without speakers. Once the cause
    # is fixed, the same num_speakers redoes only the speakers; a call without a count uses what is there
    failed = {"diarized": False, "reason": "error: ProxyError: HTTPSConnectionPool(host='huggingface.co')", "turns": []}
    res = make(tmp_path, video, monkeypatch, FakeDriver(diar=failed))
    assert (res["state"], res["diarized"], res["mode"]) == ("made", False, "none")
    assert any("the diarization failed (error: ProxyError" in w and "call again with num_speakers" in w
               for w in res["warnings"])
    side, sdir = video.with_name("aula.vtt"), Path(res["sdir"])
    own = T.own_sidecar(side, tmp_path / "cache")
    asr_stage = (sdir / "stage.json").read_bytes()
    assert plan(tmp_path, vtt=side, own=own)["state"] == "file"               # no count: the subtitles as they are
    assert plan(tmp_path)["state"] == "cached"                                  # nor without them: no question
    p = plan(tmp_path, {"num_speakers": T.parse_num_speakers("2")}, vtt=side, own=own)
    assert (p["state"], p["phase"], p["replace"]) == ("pending", "diar", True)  # the same count: tried again
    drv = FakeDriver()
    res2 = make(tmp_path, video, monkeypatch, drv, p=p)
    assert len(drv.calls) == 1 and "--diar-out" in drv.calls[0]
    assert (res2["state"], res2["diarized"], res2["mode"], res2["speakers_found"]) == ("made", True, "pyannote", 2)
    assert not any("speakers not separated" in w for w in res2["warnings"])
    assert (sdir / "stage.json").read_bytes() == asr_stage                      # the speech was not redone
    assert res2["sidecar"].endswith("/vids/aula.vtt") and len(vtt.parse_vtt_bytes(side.read_bytes())["speakers"]) == 2


def test_make_another_language_keeps_the_single_speaker_without_a_token(tmp_path, video, monkeypatch):
    # a lecture transcribed with one speaker and no token, redone in another language: still SPEAKER_00, no warning
    no_tok = {**STATUS, "token": False}
    res = make(tmp_path, video, monkeypatch, FakeDriver(),
               p=plan(tmp_path, {"num_speakers": T.parse_num_speakers("1")}, status=no_tok))
    side = video.with_name("aula.vtt")
    own = T.own_sidecar(side, tmp_path / "cache")
    assert res["mode"] == "single" and own["speakers"] == "1"
    p = plan(tmp_path, {"audio_language": "en"}, status=no_tok, vtt=side, own=own)
    res2 = make(tmp_path, video, monkeypatch, FakeDriver(), p=p)
    assert (res2["state"], res2["mode"]) == ("made", "single")
    assert not any("speakers not separated" in w for w in res2["warnings"])
    assert [s["name"] for s in vtt.parse_vtt_bytes(side.read_bytes())["speakers"]] == ["SPEAKER_00"]
    assert (T.read_note(side)["speakers"], T.read_note(side)["language_requested"]) == ("1", "en")


def test_make_single_and_no_token(tmp_path, video, monkeypatch):
    drv = FakeDriver()
    res = make(tmp_path, video, monkeypatch, drv, p=plan(tmp_path, {"num_speakers": T.parse_num_speakers("1")}))
    assert "--diar-out" not in drv.calls[0] and res["speakers_found"] == 1
    assert {s["name"] for s in vtt.parse_vtt_bytes(res["vtt"].read_bytes())["speakers"]} == {"SPEAKER_00"}
    video.with_name("aula.vtt").unlink()
    p = plan(tmp_path, status={**STATUS, "token": False})
    drv2 = FakeDriver()
    res = make(tmp_path, video, monkeypatch, drv2, p=p)
    assert drv2.calls == [] and res["reason"] == "no_token" and any("no Hugging Face token" in w for w in res["warnings"])


@pytest.mark.parametrize("code, needle", [
    (3, "the WhisperX environment does not load"), (5, "out of memory"), (1, "transcribe.py exited with 1"),
    (130, "interrupted"),
])
def test_make_driver_failures(tmp_path, video, monkeypatch, code, needle):
    with pytest.raises(T.TranscriptError) as exc:
        make(tmp_path, video, monkeypatch, FakeDriver(code=code, log="linha 1\nModuleNotFoundError: No module named 'x'\n"))
    assert needle in str(exc.value) and "No module named 'x'" in str(exc.value)
    assert not video.with_name("aula.vtt").exists()


def test_make_video_without_audio(tmp_path, video, monkeypatch):
    drv = FakeDriver()
    res = make(tmp_path, video, monkeypatch, drv, has_audio=False)
    assert drv.calls == [] and res["n_cues"] == 0 and any("no audio track" in w for w in res["warnings"])
    assert vtt.parse_vtt_bytes(res["vtt"].read_bytes())["cues"] == []


@pytest.mark.parametrize("written", [0, 100])
def test_make_on_a_full_disk_leaves_no_partial_subtitles(tmp_path, video, monkeypatch, written):
    # the video's drive fills up while the cache's has room: the run warns and uses the cache's subtitles, and
    # leaves no empty or cut aula.vtt next to the video, which every later run would take for the person's file
    # (0 cues, or the end of the meeting missing) instead of the complete transcript in the cache
    full = FullFolder(monkeypatch, video.parent, written)
    res = make(tmp_path, video, monkeypatch, FakeDriver())
    assert res["state"] == "made" and res["sidecar"] is None and res["vtt"] == res["cache_vtt"] and res["n_cues"] == 4
    assert any("could not write the subtitles next to the video" in w and "No space left" in w for w in res["warnings"])
    assert os.listdir(video.parent) == ["aula.mp4"] and not (tmp_path / "cache" / T.EXPORT_JSON).exists()
    full.on = False                                                          # room again: the next analysis
    assert discover.find_vtt(video) is None
    p = plan(tmp_path)
    assert p["state"] == "cached"
    drv = FakeDriver()
    res2 = make(tmp_path, video, monkeypatch, drv, p=p)
    assert drv.calls == [] and res2["sidecar"].endswith("/vids/aula.vtt") and res2["vtt"].read_bytes() == res["vtt"].read_bytes()
    assert T.own_sidecar(res2["vtt"], tmp_path / "cache")["speakers"] == "2"   # the skill's: replaceable later


def test_make_keeps_the_gc_away_while_it_transcribes(tmp_path, video, monkeypatch):
    # hours on a CPU under .transcript.lock alone: a --gc run meanwhile, on a video last used 40 days ago (another
    # num_speakers now) or with --gc 0, leaves its checkpoints and its folder alone
    root = tmp_path / "home" / "cache"
    vdir = root / "aula-1a2b3c4d"
    vdir.mkdir(parents=True)
    cache.touch_last_used(vdir)
    old = time.time() - 40 * 86400
    os.utime(vdir / "last_used", (old, old))
    gcs = []

    class GcMeanwhile(FakeDriver):
        def __call__(self, cmd, log_path, env=None):
            chunk = Path(log_path).parent / "partial" / "chunk_000.json"
            chunk.parent.mkdir()
            write_json(chunk, {"block": 0})
            gcs.extend(cache.gc_cache(root, days) for days in (30, 0))
            assert chunk.is_file()
            return super().__call__(cmd, log_path, env)

    monkeypatch.setitem(cache.STAGE_OVERRIDES, "run_transcribe", GcMeanwhile())
    p = T.plan(video=video, video_dir=vdir, video_key="aula-1a2b3c4d", vid="v1", slug="aula",
               opts={"num_speakers": T.parse_num_speakers("2")}, status=STATUS)
    res = T.make(p=p, python="C:/env/python.exe", script="transcribe.py", video=video, video_dir=vdir, duration_s=10.0)
    assert res["state"] == "made" and res["n_cues"] == 4 and (Path(res["sdir"]) / "asr.json").is_file()
    assert gcs[0]["removed"] == [] and gcs[0]["kept"] == 1                     # a transcription counts as use
    assert gcs[1]["removed"] == [] and gcs[1]["skipped"] == [{"key": vdir.name, "reason": "in use (active lock)"}]


def test_driver_env(monkeypatch, tmp_path):
    from va import ffmpeg_io
    monkeypatch.setattr(ffmpeg_io, "_TOOLS", {})
    monkeypatch.setenv("PYTHONPATH", "C:/vazamento")
    ff = tmp_path / "ff" / "ffmpeg.exe"
    ff.parent.mkdir()
    ff.write_bytes(b"")
    monkeypatch.setenv("VA_FFMPEG", str(ff))
    env = T.driver_env("C:/env/nltk_data")
    assert "PYTHONPATH" not in env and env["NLTK_DATA"] == "C:/env/nltk_data" and env["PYTHONIOENCODING"] == "utf-8"
    assert env["PATH"].split(os.pathsep)[0] == str(ff.parent)                 # the same ffmpeg as the skill's
    assert env["MPLBACKEND"] == "Agg" and env["CUDA_CACHE_MAXSIZE"]


def test_driver_env_puts_homebrew_ffmpeg_on_path(monkeypatch, tmp_path):
    """A Mac whose shell never ran brew shellenv: prepare.py finds /opt/homebrew/bin/ffmpeg, and so must the
    transcription (whisperx.load_audio runs a bare ffmpeg from PATH)."""
    from va import environment, ffmpeg_io
    opt = tmp_path / "opt-homebrew-bin"
    opt.mkdir()
    (opt / "ffmpeg").write_bytes(b"")
    monkeypatch.setattr(ffmpeg_io, "_TOOLS", {})
    monkeypatch.setattr(environment, "BREW_BIN_DIRS", (str(opt), str(tmp_path / "usr-local-bin")))
    monkeypatch.setattr(environment.sys, "platform", "darwin")
    monkeypatch.setattr(environment.os, "access", lambda p, mode: True)
    monkeypatch.setattr(ffmpeg_io.shutil, "which", lambda name: None)
    monkeypatch.delenv("VA_FFMPEG", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin" + os.pathsep + "/bin")
    env = T.driver_env(None)
    assert env["PATH"].split(os.pathsep)[:2] == [str(opt), "/usr/bin"]
    monkeypatch.setattr(environment.sys, "platform", "win32")                 # nowhere: PATH as it was
    monkeypatch.setattr(ffmpeg_io, "_TOOLS", {})
    assert T.driver_env(None)["PATH"] == "/usr/bin" + os.pathsep + "/bin"
