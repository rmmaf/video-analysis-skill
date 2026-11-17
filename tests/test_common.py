"""Unit tests for va.common and the va package defaults (WP0)."""
import errno
import io
import json
import os
import random
import re
import sys
import unicodedata
from pathlib import Path

import pytest

import va
from va import common as C

NFD = lambda s: unicodedata.normalize("NFD", s)  # noqa: E731

TEAMS_STEM = "Repasse - Fluxo de Carga dos DA\u2019s-20260818_160841-Gravação de Reunião"


# --- slugify ---------------------------------------------------------------------------------

@pytest.mark.parametrize("stem, expected", [
    (TEAMS_STEM, "repasse-fluxo-de-carga-20260818-160841"),       # spec example (video_key prefix)
    (NFD(TEAMS_STEM), "repasse-fluxo-de-carga-20260818-160841"),  # NFD name on disk
    ("Repasse - Fluxo de Carga dos DA's-20260818_150822-Gravação de Reunião", "repasse-fluxo-de-carga-20260818-150822"),
    ("Indicador PES-20260610_150844-Gravação de Reunião", "indicador-pes-20260610-150844"),
    ("Solar  Indicium DE - Office Hours-Job Clusters-20251023_100338-Meeting Recording", "solar-indicium-de-office-20251023-100338"),
    ("Treinamento Playbook Ágil - Projeto Refpet E Robô Previdenciário-20260203 090538-Gravação De Reunião",
     "treinamento-playbook-20260203-090538"),
    ("20260818_160841-Gravação de Reunião", "gravacao-de-reuniao-20260818-160841"),
    ("20260818_160841", "20260818-160841"),
    ("Criando sua primeira PR", "criando-sua-primeira-pr"),
    ("venda_zero_clientes", "venda-zero-clientes"),
    ("[PES] Tira dúvidas DIAS DE ESTOQUE", "pes-tira-duvidas-dias-de"),
    ("Pílula Crtl+Alt+Data - Depoly no databricks-20250129_164043-Gravação de Reunião", "pilula-crtl-alt-data-20250129-164043"),
    ("Repasse conhecimento migração de estrutura(0-1) Eng. 1_2-20241014", "repasse-conhecimento"),
    ("PES — Origem e cálculo dos indicadores de NPS", "pes-origem-e-calculo-dos"),
    ("Supercalifragilisticexpialidocious", "supercalifragilisticexpi"),
    ("abcdefghij-klmnopqrst-uv", "abcdefghij-klmnopqrst-uv"),      # exactly 24
    ("abcdefghij-klmnopqrst-uvw", "abcdefghij-klmnopqrst"),        # 25 -> back to the last "-"
    ("abcdefghij-klmnopqrst-uv-x", "abcdefghij-klmnopqrst-uv"),    # char 25 is "-" -> keep 24
    ("“Citação” ‘simples’ e \"retas\"", "citacao-simples-e-retas"),
    ("ﬁle Ⅻ ①", "file-xii-1"),                                    # NFKD compatibility forms
    ("", "video"),
    ("\u2019\u2019\u2019", "video"),
    ("!!! ???", "video"),
    ("漢字", "video"),
])
def test_slugify(stem, expected):
    assert C.slugify(stem) == expected


def test_slugify_shape():
    rx = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    rng = random.Random(7)
    alphabet = "aãçé’ _-.()[]!0123456789ÁÖ漢\u0301"
    for _ in range(500):
        stem = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        if rng.random() < 0.3:
            stem += "-20250101_123456-Gravação"
        slug = C.slugify(stem)
        assert rx.match(slug), (stem, slug)
        assert slug.isascii() and len(slug) <= 40
        bare = "".join(ch for ch in unicodedata.normalize("NFKD", stem) if not unicodedata.combining(ch))
        if not re.search(r"[0-9]{8}[_ ][0-9]{6}", bare):
            assert len(slug) <= 24


# --- time --------------------------------------------------------------------------------------

@pytest.mark.parametrize("t, dec, expected", [
    (0, 1, "00:00:00.0"),
    (174.5, 1, "00:02:54.5"),
    (544.512, 1, "00:09:04.5"),
    (178.697, 1, "00:02:58.7"),        # rounds (spec batch example)
    (187.283, 1, "00:03:07.3"),
    (59.96, 1, "00:01:00.0"),          # carry
    (3600, 1, "01:00:00.0"),
    (359999.96, 1, "100:00:00.0"),     # hours never truncated
    (174.614, 3, "00:02:54.614"),
    (174.614, 2, "00:02:54.61"),
    (544.512, 0, "00:09:04"),          # dec=0 truncates (spec: duration "00:09:04")
    (441.2165, 0, "00:07:21"),
    (-1.5, 1, "-00:00:01.5"),
])
def test_fmt_hms(t, dec, expected):
    assert C.fmt_hms(t, dec) == expected


def test_fmt_hms_default_one_decimal():
    assert C.fmt_hms(2.034) == "00:00:02.0"


@pytest.mark.parametrize("t, expected", [(0.0, "000000"), (0.5, "000000"), (2.0, "000002"), (174.5, "000254"),
                                         (3725.9, "010205"), (59.9996, "000100"), (360000.0, "1000000")])
def test_fmt_hhmmss_compact(t, expected):
    assert C.fmt_hhmmss_compact(t) == expected


def test_fmt_hhmmss_compact_rejects_negative():
    with pytest.raises(ValueError):
        C.fmt_hhmmss_compact(-0.5)


@pytest.mark.parametrize("text, expected_ms", [
    ("00:02:54.614", 174614),
    ("02:54.614", 174614),
    ("2:54", 174000),
    ("1:00:00.460", 3600460),
    ("01:00:00.460", 3600460),
    ("59:59.460", 3599460),
    ("100:00:00", 360000000),
    ("00:00:13,5", 13500),            # comma, fraction padded to the right
    ("00:00:01.5", 1500),
    ("00:00:01.05", 1050),
    ("90", 90000),
    ("90.5", 90500),
    ("1.2345", 1235),                 # more than 3 digits: rounded half-up
    ("0.0004", 0),
    ("  12:34  ", 754000),
    ("-1.5", -1500),
    ("+00:10", 10000),
])
def test_parse_time_ms(text, expected_ms):
    assert C.parse_time_ms(text) == expected_ms
    assert C.parse_time(text) == expected_ms / 1000


@pytest.mark.parametrize("bad", ["", " ", "abc", "-", "1:2:3:4", "00:61.000", "01:60:00", "60:00",
                                 "00:00:1e3", ":30", "12:", "1:30.", "1,2,3"])
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError):
        C.parse_time(bad)


def test_parse_time_numbers_and_types():
    assert C.parse_time(12) == 12.0
    assert C.parse_time(1.2345) == 1.235
    with pytest.raises(TypeError):
        C.parse_time(True)
    with pytest.raises(TypeError):
        C.parse_time(None)


def test_time_round_trips():
    rng = random.Random(42)
    values = [0, 1, 999, 1000, 59999, 60000, 3599999, 3600000, 362439101] + [rng.randrange(0, 10 ** 8) for _ in range(2000)]
    for ms in values:
        t = C.ms_to_s(ms)
        assert C.s_to_ms(t) == ms
        assert C.parse_time_ms(C.fmt_hms(t, 3)) == ms
        assert C.parse_time(C.fmt_hms(t, 3)) == t
        assert C.parse_time_ms(C.fmt_hms(t, 1)) == (ms + 50) // 100 * 100
        assert C.parse_time_ms(C.fmt_hms(t, 0)) == ms // 1000 * 1000
        assert json.loads(json.dumps(t)) == t and len(repr(t).split(".")[-1]) <= 3


def test_ms_seconds_helpers():
    assert C.s_to_ms(2.034) == 2034
    assert C.s_to_ms(0.0005) == 1
    assert C.s_to_ms(-1.5) == -1500
    assert C.ms_to_s(2034) == 2.034
    assert C.ms_to_s(1500.7) == 1.501
    assert isinstance(C.s_to_ms(1.0), int)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            C.s_to_ms(bad)
    with pytest.raises(TypeError):
        C.s_to_ms("1.0")


# --- names ------------------------------------------------------------------------------------

def test_naming_helpers():
    assert C.frame_id(1) == "F0001"
    assert C.frame_id(9999, 9999) == "F9999"
    assert C.frame_id(5, 12000) == "F00005"
    assert C.frame_id(10000) == "F10000"
    assert C.frame_filename(1, 0.5) == "F0001_000000.jpg"
    assert C.frame_filename(41, 174.5) == "F0041_000254.jpg"
    assert C.frame_filename(7, 3725.9, 10001) == "F00007_010205.jpg"
    assert C.cue_id(61) == "C0061"
    assert C.batch_id(3) == "B003"
    assert C.batch_filename("batch_md", 3) == "batch_003.md"
    assert C.batch_filename("batch_json", 12) == "batch_012.json"
    assert C.batch_filename("sheet", 101) == "sheet_101.jpg"
    with pytest.raises(ValueError):
        C.batch_filename("overview", 1)


def test_video_title_and_sort_key():
    p = Path("C:/x") / (NFD(TEAMS_STEM) + ".mp4")
    assert C.video_title(p) == unicodedata.normalize("NFC", TEAMS_STEM)
    assert C.nfc_casefold(NFD("GRAVAÇÃO")) == C.nfc_casefold("gravação")


# --- files ------------------------------------------------------------------------------------

SAMPLE = {"schema": "va.test/1", "z": 1, "a": [0.0, 2.034, 544.512], "título": "Gravação de Reunião",
          "nested": {"b": None, "a": True}, "empty": []}


def test_write_json_format_and_determinism(tmp_path):
    p1, p2 = tmp_path / "a.json", tmp_path / "sub" / "b.json"
    C.write_json(p1, SAMPLE)
    C.write_json(p2, json.loads(json.dumps(SAMPLE)))  # equal content, built separately
    b1, b2 = p1.read_bytes(), p2.read_bytes()
    assert b1 == b2
    assert b1 == (json.dumps(SAMPLE, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    assert b"\r\n" not in b1 and b1.endswith(b"}\n")
    assert "Gravação".encode("utf-8") in b1 and b"\\u" not in b1
    text = b1.decode("utf-8")
    assert text.index('"z"') < text.index('"a"')  # insertion order, not sorted
    assert C.read_json(p1) == SAMPLE
    C.write_json(p1, SAMPLE)
    assert p1.read_bytes() == b1


def test_write_json_rejects_nan_and_keeps_old_file(tmp_path):
    p = tmp_path / "x.json"
    C.write_json(p, {"ok": 1})
    before = p.read_bytes()
    with pytest.raises(ValueError):
        C.write_json(p, {"bad": float("nan")})
    assert p.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == ["x.json"]


def test_dumps_json_line():
    assert C.dumps_json({"a": "ç", "b": [1, 2]}, line=True) == '{"a":"ç","b":[1,2]}'
    with pytest.raises(ValueError):
        C.dumps_json({"x": float("inf")}, line=True)


def test_read_json_tolerates_bom(tmp_path):
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": "é"}, ensure_ascii=False).encode("utf-8"))
    assert C.read_json(p) == {"a": "é"}


def test_atomic_write_bytes_and_write_text(tmp_path):
    p = tmp_path / "deep" / "er" / "f.bin"
    C.atomic_write_bytes(p, b"\x00\x01first")
    C.atomic_write_bytes(p, b"second")
    assert p.read_bytes() == b"second"
    assert os.listdir(p.parent) == ["f.bin"]  # no temp files left
    t = tmp_path / "t.md"
    C.write_text(t, "linha 1\nlinha 2 ação\n")
    assert t.read_bytes() == "linha 1\nlinha 2 ação\n".encode("utf-8")


def test_atomic_write_cleans_up_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "f.txt"

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(C.os, "replace", boom)
    with pytest.raises(OSError):
        C.atomic_write_bytes(p, b"data")
    assert os.listdir(tmp_path) == []


_REAL_FDOPEN = os.fdopen


class _Cut:
    """A write that stops after 3 bytes: a full disk (ENOSPC) or Ctrl+C."""

    def __init__(self, fh, exc):
        self.fh, self.exc = fh, exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fh.close()

    def write(self, data):
        self.fh.write(data[:3])
        self.fh.flush()
        raise self.exc


def _cut_write(monkeypatch, exc, nth):
    calls = []

    def fdopen(fd, *a, **k):
        calls.append(fd)
        fh = _REAL_FDOPEN(fd, *a, **k)
        return _Cut(fh, exc) if len(calls) == nth else fh
    monkeypatch.setattr(C.os, "fdopen", fdopen)


@pytest.fixture(params=["platform", "hard link", "no hard links"])
def publish(request, monkeypatch):
    """How atomic_create_bytes publishes: this platform's way (Windows: a rename that refuses an existing name;
    POSIX: a hard link), a hard link (forced on Windows too: NTFS has them), and a volume without hard links
    (exFAT, FAT, some shares), where an exclusive create stands in."""
    if request.param != "platform":
        monkeypatch.setattr(C, "_RENAME_REPLACES", True)
    if request.param == "no hard links":
        def no_links(src, dst):
            raise OSError(errno.EPERM, "Operation not permitted")
        monkeypatch.setattr(C.os, "link", no_links)
    return request.param


def test_atomic_create_bytes_never_replaces_a_file(tmp_path, publish):
    p = tmp_path / "f.vtt"
    assert C.atomic_create_bytes(p, b"WEBVTT\n\nfirst\n") == p and p.read_bytes() == b"WEBVTT\n\nfirst\n"
    with pytest.raises(FileExistsError):
        C.atomic_create_bytes(p, b"second")
    assert p.read_bytes() == b"WEBVTT\n\nfirst\n" and os.listdir(tmp_path) == ["f.vtt"]  # kept, no temp files left
    with pytest.raises(FileNotFoundError):
        C.atomic_create_bytes(tmp_path / "gone" / "f.vtt", b"x")                  # the folder is never created
    assert os.listdir(tmp_path) == ["f.vtt"]


@pytest.mark.parametrize("exc", [OSError(errno.ENOSPC, "No space left on device"), KeyboardInterrupt()],
                         ids=["full disk", "ctrl+c"])
def test_atomic_create_bytes_leaves_nothing_when_the_write_fails(tmp_path, monkeypatch, publish, exc):
    # in the middle of the write: no f.vtt, empty or cut (later runs would take it for a whole one), no temp file
    for nth in ((1, 2) if publish == "no hard links" else (1,)):                 # 2: the exclusive create's write
        _cut_write(monkeypatch, exc, nth)
        with pytest.raises(type(exc)):
            C.atomic_create_bytes(tmp_path / "f.vtt", b"WEBVTT\n" * 100)
        assert os.listdir(tmp_path) == []


def test_atomic_create_bytes_when_a_file_appears_meanwhile(tmp_path, monkeypatch, publish):
    # another program writes f.vtt while the bytes go to the temp file: its file is kept, the temp file removed
    p = tmp_path / "f.vtt"

    def fdopen(fd, *a, **k):
        p.write_bytes(b"da pessoa")
        return _REAL_FDOPEN(fd, *a, **k)
    monkeypatch.setattr(C.os, "fdopen", fdopen)
    with pytest.raises(FileExistsError):
        C.atomic_create_bytes(p, b"nossa")
    assert p.read_bytes() == b"da pessoa" and os.listdir(tmp_path) == ["f.vtt"]


def test_atomic_create_bytes_waits_for_a_held_file(tmp_path, monkeypatch):
    # Windows: antivirus or the indexer holds the new file for a moment, and the rename is tried again
    monkeypatch.setattr(C, "_RENAME_REPLACES", False)
    monkeypatch.setattr(C, "_REPLACE_DELAYS", (0.0, 0.0, 0.0))
    real, tries = C.os.rename, []

    def held(src, dst):
        tries.append(dst)
        if len(tries) < 3:
            raise PermissionError(errno.EACCES, "held by another process")
        real(src, dst)
    monkeypatch.setattr(C.os, "rename", held)
    p = C.atomic_create_bytes(tmp_path / "f.vtt", b"x")
    assert p.read_bytes() == b"x" and len(tries) == 3 and os.listdir(tmp_path) == ["f.vtt"]


# --- paths ------------------------------------------------------------------------------------

@pytest.fixture
def tricky_tree(tmp_path):
    """databricks  schema/3.DA/<NFD Teams name with ’>.mp4|.vtt, [PES] files, NFD folder."""
    base = tmp_path / "databricks  schema" / "3.DA"
    base.mkdir(parents=True)
    video = base / (NFD(TEAMS_STEM) + ".mp4")
    video.write_bytes(b"v")
    (base / (NFD(TEAMS_STEM) + ".vtt")).write_bytes(b"WEBVTT\n")
    pes = tmp_path / NFD("Conteúdos")
    pes.mkdir()
    (pes / "[PES] Tira dúvidas.vtt").write_bytes(b"x")
    (pes / "P.vtt").write_bytes(b"x")
    (pes / "E.vtt").write_bytes(b"x")
    return tmp_path, video


def test_resolve_literal_path(tricky_tree):
    root, video = tricky_tree
    assert C.resolve_unicode_path(video) == video
    assert C.resolve_unicode_paths(str(video)) == [video]


def test_resolve_nfc_input_finds_nfd_name(tricky_tree):
    root, video = tricky_tree
    nfc = root / "databricks  schema" / "3.DA" / (unicodedata.normalize("NFC", TEAMS_STEM) + ".mp4")
    assert not os.path.exists(nfc)  # NTFS does not normalize
    got = C.resolve_unicode_path(nfc)
    assert got == video and got.name == video.name  # on-disk (NFD) name returned


def test_resolve_case_quotes_and_spaces(tricky_tree):
    root, video = tricky_tree
    variants = [
        root / "DATABRICKS  SCHEMA" / "3.da" / (TEAMS_STEM.upper() + ".MP4"),
        root / "databricks  schema" / "3.DA" / (TEAMS_STEM.replace("\u2019", "'") + ".mp4"),
        root / "databricks schema" / "3.DA" / (TEAMS_STEM + ".mp4"),  # double space typed as one
    ]
    for v in variants:
        assert C.resolve_unicode_path(v) == video, v


def test_resolve_glob(tricky_tree):
    root, video = tricky_tree
    pattern = root / "databricks  schema" / "3.DA" / "Repasse - Fluxo de Carga dos DA*-20260818_160841-Grava*o de Reuni*o.mp4"
    assert C.resolve_unicode_path(pattern) == video
    both = C.resolve_unicode_paths(root / "databricks  schema" / "3.DA" / "*160841*")
    assert [p.suffix for p in both] == [".mp4", ".vtt"]
    with pytest.raises(C.AmbiguousPathError) as exc:
        C.resolve_unicode_path(root / "databricks  schema" / "3.DA" / "*160841*")
    assert len(exc.value.matches) == 2
    assert C.resolve_unicode_paths(root / "*" / "3.DA" / "*.vtt") == [both[1]]


def test_resolve_brackets_are_literal(tricky_tree):
    root, _ = tricky_tree
    folder = root / NFD("Conteúdos")
    target = folder / "[PES] Tira dúvidas.vtt"
    assert C.resolve_unicode_path(root / "Conteúdos" / "[PES] Tira dúvidas.vtt") == target
    assert C.resolve_unicode_path(root / "Conteúdos" / NFD("[pes] tira dúvidas.vtt")) == target
    assert C.resolve_unicode_paths(root / "Conteúdos" / "[PES]*.vtt") == [target]
    assert C.resolve_unicode_paths(root / "Conteúdos" / "[PE]*") == []


def test_resolve_missing(tricky_tree):
    root, _ = tricky_tree
    missing = root / "nao existe" / "x.mp4"
    assert C.resolve_unicode_paths(missing) == []
    with pytest.raises(FileNotFoundError):
        C.resolve_unicode_path(missing)
    assert C.resolve_unicode_paths(root / "databricks  schema" / "*.mov") == []
    assert C.resolve_unicode_paths("") == [] and C.resolve_unicode_paths("  ") == []


def test_resolve_relative_path(tricky_tree, monkeypatch):
    root, video = tricky_tree
    monkeypatch.chdir(root)
    got = C.resolve_unicode_path(Path("databricks  schema") / "3.DA" / (TEAMS_STEM + ".mp4"))
    assert got == video and got.is_absolute()


def test_as_posix():
    assert C.as_posix("C:\\Users\\digom\\video-analysis\\runs\\x y") == "C:/Users/digom/video-analysis/runs/x y"
    assert C.as_posix(Path("C:/a") / "b c" / "d.json") == "C:/a/b c/d.json"
    assert C.as_posix("\\\\server\\share\\f.mp4") == "//server/share/f.mp4"
    assert C.as_posix("C:/já/está") == "C:/já/está"


# --- workspace ---------------------------------------------------------------------------------

def test_workspace_home_precedence(tmp_path, monkeypatch):
    """--home (cli_value) > env VIDEO_ANALYSIS_HOME > %USERPROFILE%\\video-analysis."""
    env_home, cli_home, profile = tmp_path / "env", tmp_path / "cli", tmp_path / "profile"
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("VIDEO_ANALYSIS_HOME", raising=False)
    assert C.workspace_home() == profile / "video-analysis"
    assert C.workspace_home(str(cli_home)) == cli_home
    assert C.workspace_home(cli_home) == cli_home
    assert C.workspace_home(f'"{cli_home}"') == cli_home       # quotes from a shell are tolerated
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", f'"{env_home}"')
    assert C.workspace_home(cli_home) == cli_home               # --home wins over the env var
    assert C.workspace_home() == env_home                       # env wins over the default
    assert C.workspace_home(None) == env_home
    assert C.workspace_home("   ") == env_home                  # blank --home is ignored
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", "   ")
    assert C.workspace_home(cli_home) == cli_home
    assert C.workspace_home() == profile / "video-analysis"     # blank env is ignored
    assert C.workspace_home().is_absolute()
    rel = C.workspace_home("rel-home")
    assert rel.is_absolute() and rel.name == "rel-home"


def test_va_home_fixture(va_home):
    assert C.workspace_home() == va_home
    assert list(va_home.iterdir()) == []


# --- console ------------------------------------------------------------------------------------

def test_setup_console(monkeypatch):
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    C.setup_console()
    C.setup_console()  # idempotent
    assert out.encoding == "utf-8" and err.encoding == "utf-8"
    print("Gravação ’ 漢\nok")
    out.flush()
    assert out.buffer.getvalue() == "Gravação ’ 漢\nok\n".encode("utf-8")
    C.setup_console(ascii_only=True)
    print("ç")
    out.flush()
    assert out.buffer.getvalue().endswith(b"\\xe7\n")


def test_setup_console_tolerates_odd_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    closed = io.TextIOWrapper(io.BytesIO())
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    C.setup_console()


# --- package defaults -----------------------------------------------------------------------------

def _walk(obj):
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk(v)


DEFAULT_GROUPS = ["DEDUP_DEFAULTS", "SENSITIVITY_PRESETS", "OUTPUT_DEFAULTS", "CUES_DEFAULTS", "BATCH_DEFAULTS",
                  "WORKFLOW_DEFAULTS", "WORKSPACE_DIRS", "NAMES", "SCHEMA_IDS", "EXIT_CODES", "TRANSCRIBE_DEFAULTS",
                  "WHISPER_MODELS", "WEBCAM_DEFAULTS", "SHOTS_DEFAULTS"]


@pytest.mark.parametrize("name", DEFAULT_GROUPS)
def test_defaults_are_plain_json(name):
    group = getattr(va, name)
    assert isinstance(group, dict)
    assert not any(isinstance(x, tuple) for x in _walk(group)), "use lists, not tuples (JSON round trip)"
    assert json.loads(json.dumps(group, allow_nan=False)) == group


def test_versions():
    assert va.VERSION == "1.1.0"
    assert va.SCHEMA_VERSION == 1
    assert va.VTT_VERSION == va.PARSER_VERSION
    for v in (va.ALGO_VERSION, va.PARSER_VERSION, va.RENDER_VERSION, va.WEBCAM_VERSION, va.TRANSCRIBE_VERSION):
        assert isinstance(v, str) and v


def test_sensitivity_presets():
    presets = va.SENSITIVITY_PRESETS
    assert list(presets) == ["low", "normal", "high"]
    keys = {"t_lo", "t_hi", "cursor_h_frac", "cursor_w_frac"}
    assert all(set(p) == keys for p in presets.values())
    assert presets["normal"] == {"t_lo": 16, "t_hi": 40, "cursor_h_frac": 0.045, "cursor_w_frac": 0.030}
    assert presets["low"]["t_lo"] > presets["normal"]["t_lo"] > presets["high"]["t_lo"]
    d = va.DEDUP_DEFAULTS
    assert d["sensitivity"] == "normal" and all(d[k] == v for k, v in presets["normal"].items())
    assert (d["max_cursor_blobs"], d["min_blob_px"], d["min_strong_px"], d["cell_px"], d["cell_active_px"]) == (3, 3, 2, 8, 4)
    assert (d["settle_s"], d["max_settle_wait_s"], d["motion_interval_s"], d["motion_max_interval_s"]) == (1.0, 3.0, 4.0, 30.0)
    assert (d["revisit_frames"], d["revisit_window_s"], d["heartbeat_s"], d["heartbeat_min_frac"]) == (16, 600.0, 30.0, 0.10)


def test_output_cues_batch_defaults():
    o, c, b = va.OUTPUT_DEFAULTS, va.CUES_DEFAULTS, va.BATCH_DEFAULTS
    assert (o["sample_fps"], o["analysis_height"], o["max_edge"], o["jpeg_q"]) == (2.0, 360, 1568, 3)
    assert o["max_edge"] <= o["max_edge_cap"] == 2000
    assert o["jpeg_q_min"] <= o["jpeg_q"] <= o["jpeg_q_max"]
    assert (c["min_duration_s"], c["merge_gap_s"], c["long_min_s"], c["long_min_chars"]) == (0.1, 0.5, 15.0, 200)
    assert (c["fast_max_cps"], c["fast_target_cps"]) == (30.0, 15.0)
    assert (b["max_frames"], b["soft_min"], b["max_text_chars"], b["max_span_s"]) == (20, 12, 8000, 900.0)
    assert b["soft_min"] == round(0.6 * b["max_frames"])
    assert b["tail_merge_max"] == int(1.25 * b["max_frames"]) and b["tail_min_frames"] == 8


def test_workflow_and_workspace_defaults():
    w = va.WORKFLOW_DEFAULTS
    assert (w["analyst_effort"], w["curator_effort"], w["max_parallel_videos"]) == ("high", "high", 3)
    assert w["curation"] == "plan" and w["curation"] in va.CURATION_MODES
    assert w["analyst_effort"] in va.EFFORT_LEVELS and w["roles_mode"] in va.ROLES_MODES
    assert set(va.WORKSPACE_DIRS) == {"cache", "runs", "requests", "roles", "logs", "debug", "tmp"}
    assert va.WORKSPACE_ENV == "VIDEO_ANALYSIS_HOME"
    assert va.EXIT_CODES == {"ok": 0, "input": 2, "environment": 3, "video_failed": 4, "interrupted": 130}


# --- the guard against old Pythons ---------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
GUARDED = ["check_environment.py", "verificar_ambiente.py", "scripts/prepare.py", "scripts/calibrate.py",
           "scripts/explain.py", "scripts/webcam_mask.py"]


def test_old_python_message():
    assert va.MIN_PYTHON == (3, 10) and va.old_python((3, 10, 0)) is None and va.old_python((3, 14, 5)) is None
    assert va.old_python() is None                                       # the Python running the tests
    mac = va.old_python((3, 9, 6), "/usr/bin/python3", "darwin")
    assert mac == ("video-analysis needs Python 3.10 or newer, and this is Python 3.9.6 (/usr/bin/python3): "
                   "brew install python, with /opt/homebrew/bin first on PATH, then run it with python3 again")
    win = va.old_python((3, 8, 10, "final", 0), "C:/Py38/python.exe", "win32")
    assert "Python 3.8.10 (C:/Py38/python.exe)" in win and "brew" not in win and "\n" not in win


def test_package_init_parses_on_old_pythons():
    """va/__init__.py runs before the guard, so it must stay free of 3.10 syntax (``X | None``, ``match``)."""
    import ast
    src = (SKILL_DIR / "scripts" / "va" / "__init__.py").read_text(encoding="utf-8")
    ast.parse(src, feature_version=(3, 7))
    assert "|" not in "".join(line for line in src.splitlines() if line.lstrip().startswith("def "))


@pytest.mark.parametrize("script", [s for s in GUARDED if s != "scripts/prepare.py"])
def test_entry_scripts_stop_on_an_old_python(script, monkeypatch, capsys):
    """Each entry script, run with a 3.9 version tuple, stops with the one line before importing anything of
    3.10 (prepare.py, with its JSON envelope: test_prepare_cli.py)."""
    import runpy
    monkeypatch.setattr(sys, "version_info", (3, 9, 6, "final", 0))
    monkeypatch.setattr(sys, "argv", [str(SKILL_DIR / script)])
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(str(SKILL_DIR / script), run_name="__main__")
    out = capsys.readouterr().out
    message = stop.value.code if isinstance(stop.value.code, str) else out.strip()
    assert stop.value.code in (1, message)
    assert message.startswith("video-analysis needs Python 3.10 or newer, and this is Python 3.9.6 (")
    assert sys.executable in message and len(message.splitlines()) == 1
