"""va.vtt: the cues stage (spec section 4) on the real snippets of tests/data/vtt, synthetic
payloads and, with --run-real, the full VTTs of tests/fixtures.json."""
import json

import pytest

from conftest import DATA_DIR
from va import CUES_DEFAULTS
from va.common import s_to_ms
from va.contracts import check_cues
from va.vtt import detect_language, effective_cue_params, parse_speaker_names, parse_vtt_bytes, run_cues_stage

VTT_DIR = DATA_DIR / "vtt"


def parse(name, params=None, duration_s=None):
    path = VTT_DIR / name
    return parse_vtt_bytes(path.read_bytes(), params, duration_s=duration_s, path=path)


def parse_text(text, params=None, duration_s=None, header=True):
    body = ("WEBVTT\n\n" if header else "") + text
    return parse_vtt_bytes(body.encode("utf-8"), params, duration_s=duration_s, path="C:/x/teste.vtt")


def rows(doc):
    return [(c["start"], c["end"], c["spk"], c["text"]) for c in doc["cues"]]


def by_tag(doc):
    return {s["tag"]: s for s in doc["speakers"]}


# --- edge_cases.vtt: the README table, row by row --------------------------------------------------

EDGE_EXPECTED = [
    (1.0, 3.0, None, "Configuração com itálico e negrito", False),
    (3.0, 5.0, None, "Karaokê palavra por palavra", False),
    (5.0, 7.0, "S1", "Voz com classe & entidade <tag>", False),
    (7.0, 9.0, None, "Espaço duro e marca invisívelzero", False),
    (10.0, 10.8, None, "Duração zero", True),
    (13.0, 13.5, None, "Fim antes", False),
    (14.0, 16.0, None, "Primeira sem linha em branco", False),
    (16.0, 18.0, None, "Segunda colada na anterior", False),
    (18.5, 20.5, None, "Vírgula e fração curta", False),
    (21.0, 23.0, None, "Sem horas", False),
    (23.0, 25.0, "S2", "Falante WhisperX", False),
    (25.0, 27.0, "S?", "Falante desconhecido", False),
    (27.0, 29.0, None, "A pergunta é: isto não é falante", False),
    (29.0, 31.0, "S3", "Primeira voz", False),
    (29.0, 31.0, "S4", "Segunda voz", False),
    (31.0, 33.0, None, "漢kan rubi", False),
    (37.0, 39.0, None, "Linha CRLF", False),
    (39.0, 41.0, None, "Linha só CR um", False),
    (41.0, 43.0, None, "Linha só CR dois", False),
]


def test_edge_cases_match_the_readme_table():
    doc = parse("edge_cases.vtt")
    assert check_cues(doc) == []
    got = [(c["start"], c["end"], c["spk"], c["text"], c["approx"]) for c in doc["cues"]]
    assert got == EDGE_EXPECTED
    assert [c["id"] for c in doc["cues"]] == [f"C{n:04d}" for n in range(1, 20)]
    assert [(s["tag"], s["name"]) for s in doc["speakers"]] == [
        ("S1", "Maria Silva"), ("S2", "SPEAKER_02"), ("S?", "FALANTE_?"), ("S3", "Ana"), ("S4", "Bruno")]
    assert doc["vtt"]["style"] == "mixed" and doc["vtt"]["language"] == "pt"
    st = doc["stats"]
    assert st["raw"] == 21 and st["kept"] == 19 and st["dropped_empty"] == 2
    assert st["merged_dupes"] == st["merged_rolling"] == st["split_long"] == 0
    assert st["extended_fast"] == 1 and st["overlaps"] == 1  # the two voices share 29-31 s
    utt = {c["text"]: c["utt"] for c in doc["cues"]}
    assert utt["A pergunta é: isto não é falante"] == 18 and utt["Sem horas"] is None
    assert doc["cues"][13]["src"] == doc["cues"][14]["src"] == [14]
    warnings = " | ".join(doc["warnings"])
    assert "horário inválido" in warnings and "#18 00:01:61.000" in warnings
    assert "fim antes do início" in warnings and "#6" in warnings


def test_cp1252_fallback_equals_utf8_text():
    cp = parse("cp1252_accented.vtt")
    utf8 = parse("whisperx_criando_pr.vtt")
    assert [c["text"] for c in cp["cues"]] == [c["text"] for c in utf8["cues"][:6]]
    assert any("cp1252" in w for w in cp["warnings"])
    assert not any("cp1252" in w for w in utf8["warnings"])
    assert "código" in cp["cues"][2]["text"]


# --- the four styles -----------------------------------------------------------------------------

def test_whisperx_prefixes_and_speakers_by_first_appearance():
    doc = parse("whisperx_criando_pr.vtt")
    assert check_cues(doc) == []
    assert doc["vtt"]["style"] == "whisperx" and doc["vtt"]["language"] == "pt"
    assert [(s["tag"], s["name"]) for s in doc["speakers"]] == [("S1", "SPEAKER_01"), ("S2", "SPEAKER_00")]
    assert doc["stats"]["raw"] == doc["stats"]["kept"] == 25
    assert doc["cues"][0]["text"] == "Oi pessoal, eu vou mostrar aqui pra vocês como criar uma PR."
    assert not any(c["text"].startswith("SPEAKER") for c in doc["cues"])
    texts = [c["text"] for c in doc["cues"]]
    i = texts.index("Quando eu")  # a sentence split across speakers is never merged
    assert doc["cues"][i]["spk"] == "S2" and doc["cues"][i + 1]["spk"] == "S1"
    assert doc["cues"][i + 1]["text"].startswith("abrir aqui")


def test_whisperx_falante_unknown_and_long_cue_split():
    doc = parse("whisperx_falante_da1608.vtt")
    assert check_cues(doc) == []
    tags = by_tag(doc)
    assert tags["S?"]["name"] == "FALANTE_?" and tags["S?"]["cues"] == 1
    assert [s["tag"] for s in doc["speakers"]] == ["S1", "S2", "S3", "S?"]
    assert doc["stats"]["split_long"] == 1
    chunks = [c for c in doc["cues"] if c["src"] == [12]]  # the 26.4 s unpunctuated cue
    assert len(chunks) >= 3 and all(c["approx"] and c["spk"] == "S1" for c in chunks)
    assert chunks[0]["start"] == 217.755 and chunks[-1]["end"] == 244.147
    for a, b in zip(chunks, chunks[1:]):
        assert a["end"] == b["start"]
    assert all(4.0 <= c["end"] - c["start"] <= 12.0 for c in chunks)
    joined = " ".join(c["text"] for c in chunks)
    assert joined.startswith("dentro do próprio notebook") and joined.endswith("que está aqui no arquivo")


def test_teams_voices_ids_and_overlaps():
    doc = parse("teams_dias_estoque.vtt")
    assert check_cues(doc) == []
    assert doc["vtt"]["style"] == "teams"
    names = [s["name"] for s in doc["speakers"]]
    assert names[:2] == ["Jose Teixeira Vidal Junior", "Kalebe Rocha Barros"]
    c0 = doc["cues"][0]
    assert (c0["start"], c0["end"], c0["spk"], c0["text"], c0["utt"]) == (3.65, 4.53, "S1", "Calcular cobertura.", 6)
    multi = next(c for c in doc["cues"] if c["text"].startswith("a cobertura ele é"))
    assert multi["text"] == "a cobertura ele é quantos dias tu tem de estoque hoje que tu tem de estoque cobra"
    assert multi["utt"] == 9 and multi["spk"] == "S2"
    assert [c["utt"] for c in doc["cues"]][:6] == [6, 7, 8, 9, 9, 18]
    # overlaps between different speakers are kept (cues 1/2 of the snippet)
    a, b = doc["cues"][0], doc["cues"][1]
    assert b["start"] < a["end"] and a["spk"] != b["spk"]
    assert doc["stats"]["overlaps"] >= 5
    assert all("<v" not in c["text"] and "</v>" not in c["text"] for c in doc["cues"])


def test_teams_like_without_ids_decodes_entities():
    doc = parse("teams_scanntech.vtt")
    assert check_cues(doc) == []
    text = " ".join(c["text"] for c in doc["cues"])
    assert "Como é, no caso, a métrica" in text and "Promoções" in text and "That's." in text
    assert "&#" not in text and doc["vtt"]["style"] == "teams"
    assert all(c["utt"] is None for c in doc["cues"])


def test_plain_without_speakers():
    doc = parse("plain_venda_zero_clientes.vtt")
    assert check_cues(doc) == []
    assert doc["vtt"]["style"] == "plain" and doc["speakers"] == []
    assert all(c["spk"] is None for c in doc["cues"])
    assert rows(doc)[:2] == [(0.0, 3.66, None, "que já mostra ele"), (3.66, 4.64, None, "tu mostra esse Power BI")]


def test_no_hours_and_hour_switch():
    doc = parse("nohours_call_promocoes_scantech.vtt")
    assert check_cues(doc) == []
    assert rows(doc)[0] == (1.68, 3.94, None, "E o que é que eu quero ver contigo?")
    doc = parse("hourswitch_treinamento_playbook.vtt")
    starts = [c["start"] for c in doc["cues"]]
    assert 3599.04 in starts and 3600.46 in starts and starts == sorted(starts)
    i = starts.index(3599.04)
    assert doc["cues"][i]["text"] == "Beleza?" and doc["cues"][i + 1]["text"] == "Ok?"


# --- merges and repairs -------------------------------------------------------------------------------

def test_stutter_identical_cues_merge_and_long_cue_splits():
    doc = parse("stutter_mix_digital.vtt")
    assert check_cues(doc) == []
    assert doc["stats"]["merged_dupes"] == 29
    merged = [c for c in doc["cues"] if c["text"] == "Vamos ver se tem alguém aqui na estrutura lá dele."]
    assert len(merged) == 1
    assert (merged[0]["start"], merged[0]["end"]) == (1046.24, 1047.78) and len(merged[0]["src"]) == 30
    assert doc["stats"]["split_long"] == 1
    long_parts = [c for c in doc["cues"] if c["src"] == [35]]
    assert len(long_parts) >= 2 and all(c["approx"] for c in long_parts)


def test_longest_whisper_merge_is_split_into_short_chunks():
    raw = (VTT_DIR / "long_mix_digital.vtt").read_text(encoding="utf-8")
    original = raw.split("00:07:42.540 --> 00:11:26.280\n", 1)[1].split("\n\n", 1)[0]
    doc = parse("long_mix_digital.vtt")
    chunks = [c for c in doc["cues"] if c["src"] == [2]]
    assert len(chunks) >= 20
    assert " ".join(c["text"] for c in chunks) == " ".join(original.split())
    durations = [c["end"] - c["start"] for c in chunks]
    assert max(durations) <= 16.0 and min(durations) >= 3.0
    assert chunks[0]["start"] == 462.54 and chunks[-1]["end"] == 686.28


def test_nonmonotonic_and_end_before_start():
    doc = parse("long_nonmono_pes_pedido_sugerido.vtt")
    assert check_cues(doc) == []
    assert doc["stats"]["nonmonotonic"] == 1 and doc["stats"]["split_long"] == 1
    fixed = next(c for c in doc["cues"] if c["text"].startswith("Tchau, tchau."))
    # end < start -> start + 0.5 s; then 59 chars in 0.5 s is too fast -> extended to the next start
    assert (fixed["start"], fixed["end"], fixed["approx"]) == (149.02, 151.46, True)
    assert any("fim antes do início" in w for w in doc["warnings"])
    starts = [(c["start"], c["end"]) for c in doc["cues"]]
    assert starts == sorted(starts)


def test_rolling_caption_and_fast_cues():
    doc = parse("rolling_red_aderencia.vtt")
    assert check_cues(doc) == []
    first = doc["cues"][0]
    assert (first["start"], first["end"], first["text"], first["src"]) == (0.0, 13.56, "A rede de aderência pesquisa", [1, 2])
    assert doc["stats"]["merged_rolling"] == 1
    fast = next(c for c in doc["cues"] if c["src"] == [12])  # 175 chars in 0.82 s
    assert fast["approx"] and fast["start"] == 70.54 and fast["end"] == 71.86  # extended to the next start
    tiny = next(c for c in doc["cues"] if c["src"] == [13])  # "Entendi." lasting 0.02 s
    assert tiny["approx"] and tiny["end"] == round(71.86 + 8 / 15, 3)
    assert doc["stats"]["extended_fast"] >= 3


def test_merges_only_within_the_same_speaker():
    doc = parse_text(
        "00:00:01.000 --> 00:00:02.600\nSPEAKER_00: A rede\n\n"
        "00:00:02.100 --> 00:00:03.000\nSPEAKER_01: A rede de aderência\n\n"      # other speaker: kept
        "00:00:02.900 --> 00:00:03.500\nSPEAKER_00: A rede de aderência\n\n"      # rolling over the interjection
        "00:00:03.600 --> 00:00:04.000\nSPEAKER_00: a rede de aderência!\n\n"     # identical tokens
        "00:00:06.000 --> 00:00:07.000\nSPEAKER_00: A rede de aderência pesquisa\n\n")  # gap 2 s: kept
    assert rows(doc) == [
        (1.0, 4.0, "S1", "A rede de aderência"),
        (2.1, 3.0, "S2", "A rede de aderência"),
        (6.0, 7.0, "S1", "A rede de aderência pesquisa"),
    ]
    assert doc["stats"]["merged_rolling"] == 1 and doc["stats"]["merged_dupes"] == 1


def test_two_line_scroll_merge():
    doc = parse_text("00:00:01.000 --> 00:00:03.000\nprimeira linha aqui\nsegunda linha aqui\n\n"
                     "00:00:03.000 --> 00:00:05.000\nsegunda linha aqui\nterceira linha nova\n\n")
    assert rows(doc) == [(1.0, 5.0, None, "primeira linha aqui segunda linha aqui terceira linha nova")]
    assert doc["stats"]["merged_rolling"] == 1


def test_multiple_voices_and_whisper_prefix_lines_split_into_sub_cues():
    doc = parse_text("00:00:01.000 --> 00:00:03.000\n<v Ana>oi</v>\n<v.x Bruno>tudo bem?\ncontinua</v>\n\n"
                     "00:00:04.000 --> 00:00:06.000\nSPEAKER_00: primeiro\nSPEAKER_01: segundo\n\n")
    assert rows(doc) == [(1.0, 3.0, "S1", "oi"), (1.0, 3.0, "S2", "tudo bem? continua"),
                         (4.0, 6.0, "S3", "primeiro"), (4.0, 6.0, "S4", "segundo")]
    assert doc["vtt"]["style"] == "mixed"


def test_missing_header_offset_clip_and_duration_warnings():
    text = ("00:00:00.500 --> 00:00:02.000\nantes do início\n\n"
            "00:00:03.000 --> 00:00:05.000\nnormal\n\n"
            "00:00:09.000 --> 00:00:12.000\npassa do fim\n\n"
            "00:00:12.000 --> 00:00:13.000\ndepois do fim\n\n")
    doc = parse_text(text, {"offset_s": -1.0}, duration_s=10.0, header=False)
    assert check_cues(doc, 10.0) == []
    assert rows(doc) == [(0.0, 1.0, None, "antes do início"), (2.0, 4.0, None, "normal"), (8.0, 10.0, None, "passa do fim")]
    assert doc["offset_s"] == -1.0
    w = " | ".join(doc["warnings"])
    assert "sem o cabeçalho WEBVTT" in w and "1 fala(s) começam depois do fim" in w
    doc = parse_text("00:00:01.000 --> 00:00:02.000\ncurta\n\n", duration_s=100.0)
    assert any("cobrem só 1%" in x for x in doc["warnings"])
    doc = parse_text("NOTE só comentário\n\n", duration_s=10.0)
    assert doc["cues"] == [] and any("nenhuma fala" in x for x in doc["warnings"])


def test_speaker_names_rename_by_name_or_tag():
    doc = parse("whisperx_criando_pr.vtt", {"speaker_names": "SPEAKER_01=Lianderson; s2 = Francisco"})
    assert [(s["tag"], s["name"]) for s in doc["speakers"]] == [("S1", "Lianderson"), ("S2", "Francisco")]
    doc2 = parse("whisperx_criando_pr.vtt", {"falantes": {"speaker_01": "Lianderson", "S2": "Francisco"}})
    assert doc2["speakers"] == doc["speakers"]


# --- params, language, stage ------------------------------------------------------------------------

def test_effective_cue_params():
    p = effective_cue_params()
    assert p == {**CUES_DEFAULTS, "speaker_names": {}}
    assert effective_cue_params(p) == p
    q = effective_cue_params({"vtt_offset": 2, "speakers": "B=b;A=a", "long_min_s": 20, "merge_gap_s": None})
    assert q["offset_s"] == 2.0 and isinstance(q["long_min_s"], float) and q["merge_gap_s"] == 0.5
    assert list(q["speaker_names"]) == ["A", "B"]
    assert json.dumps(q, sort_keys=False) == json.dumps(effective_cue_params(q), sort_keys=False)
    with pytest.raises(ValueError, match="unknown cue parameter"):
        effective_cue_params({"offset_ms": 1})
    with pytest.raises(ValueError):
        effective_cue_params({"split_chunk_max_s": 1.0})
    with pytest.raises(ValueError):
        parse_speaker_names("SPEAKER_01 Lianderson")


@pytest.mark.parametrize("texts, expected", [
    (["Então, eu não sei se você já viu isso aqui também."], "pt"),
    (["So what we have here is the report that you can open from the menu."], "en"),
    (["Entonces, yo creo que el informe ya está en la carpeta, pero hay un problema."], "es"),
    (["12345", "ok"], "und"),
    ([], "und"),
])
def test_detect_language(texts, expected):
    assert detect_language(texts) == expected


def test_language_header_wins():
    assert detect_language(["the and is"], header="pt") == "pt"
    doc = parse_text("00:00:01.000 --> 00:00:02.000\nthe report is here and you can see it\n\n")
    assert doc["vtt"]["language"] == "en"


def test_run_cues_stage_writes_deterministic_json(tmp_path):
    src = VTT_DIR / "teams_dias_estoque.vtt"
    a = run_cues_stage(src, {}, tmp_path / "a", 398.784)
    b = run_cues_stage(src, effective_cue_params(), tmp_path / "b", 398.784)
    assert a == b
    data = (tmp_path / "a" / "cues.json").read_bytes()
    assert data == (tmp_path / "b" / "cues.json").read_bytes()
    assert data.endswith(b"\n") and b"\r" not in data
    doc = json.loads(data.decode("utf-8"))
    assert list(doc) == ["schema", "parser_version", "vtt", "offset_s", "stats", "speakers", "cues", "warnings"]
    assert doc["schema"] == "va.cues/1" and doc["parser_version"] == "vtt-1.0"
    assert doc["vtt"]["path"].endswith("/tests/data/vtt/teams_dias_estoque.vtt") and "\\" not in doc["vtt"]["path"]
    assert len(doc["vtt"]["sha1"]) == 40
    assert check_cues(doc, 398.784) == []


# --- real VTTs (tests/fixtures.json) ---------------------------------------------------------------------

FIXTURE_NAMES = ["repasse_da_1608", "venda_zero_clientes", "dias_estoque", "criando_primeira_pr", "metric_view",
                 "red_aderencia"]


@pytest.mark.real
@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_real_vtt_parses_cleanly(name, real_fixture, tmp_path):
    fx = real_fixture(name)
    exp = fx["expect"]
    doc = run_cues_stage(fx["vtt"], {}, tmp_path, exp["duration_s"])
    assert check_cues(doc, exp["duration_s"]) == []
    assert doc["stats"]["raw"] == exp["raw_cues"]
    assert doc["vtt"]["style"] == exp["cue_style"] and doc["vtt"]["language"] == "pt"
    assert len(doc["speakers"]) == exp["speakers"]
    assert ("S?" in by_tag(doc)) == bool(exp.get("has_falante"))
    assert all(s_to_ms(c["end"]) <= s_to_ms(exp["duration_s"]) for c in doc["cues"])


@pytest.mark.real
def test_real_dias_estoque_keeps_overlaps_between_speakers(real_fixture, tmp_path):
    fx = real_fixture("dias_estoque")
    doc = run_cues_stage(fx["vtt"], {}, tmp_path, fx["expect"]["duration_s"])
    assert len(doc["speakers"]) >= fx["expect"]["min_speakers"]
    cues = doc["cues"]
    pairs = [(a, b) for i, a in enumerate(cues) for b in cues[i + 1:i + 6]
             if b["start"] < a["end"] and a["spk"] != b["spk"]]
    assert len(pairs) >= 30 and doc["stats"]["overlaps"] >= len(pairs)


@pytest.mark.real
def test_real_red_aderencia_rolling_merged_and_fast_cues_approx(real_fixture, tmp_path):
    fx = real_fixture("red_aderencia")
    doc = run_cues_stage(fx["vtt"], {}, tmp_path, fx["expect"]["duration_s"])
    assert doc["stats"]["merged_rolling"] >= fx["expect"]["rolling_merges_min"]
    assert doc["cues"][0]["text"] == "A rede de aderência pesquisa" and doc["cues"][0]["src"] == [1, 2]
    assert doc["stats"]["extended_fast"] >= fx["expect"]["fast_cues_min"]
    fast = next(c for c in doc["cues"] if 12 in c["src"])
    assert fast["approx"] and fast["end"] > 71.36
