"""va.discover: input expansion and the strict same-basename VTT rule, on tricky names created in
tmp_path (NFD, ’, double spaces, [PES], the Teams name mismatch)."""
import os
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

from va import discover as D

NFD = lambda s: unicodedata.normalize("NFD", s)  # noqa: E731
NFC = lambda s: unicodedata.normalize("NFC", s)  # noqa: E731
TEAMS = "Repasse - Fluxo de Carga dos DA’s-20260818_160841-Gravação de Reunião"
PES_VIDEO = "Indicador PES-20260610_150844-Gravação de Reunião"


def touch(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def tree(tmp_path):
    base = tmp_path / "databricks  schema" / "3.DA"
    v1 = touch(base / (NFD(TEAMS) + ".mp4"))
    t1 = touch(base / (NFD(TEAMS) + ".vtt"))
    pes = tmp_path / "[PES] Conteúdos"
    v2 = touch(pes / f"{PES_VIDEO}.mp4")
    c2 = touch(pes / "Indicador PES.vtt")
    v3 = touch(pes / "[PES] Tira dúvidas.mov")
    t3 = touch(pes / "[pes] tira DÚVIDAS.VTT")
    touch(pes / "notas.txt")
    sub = touch(pes / "sub" / "dentro.mp4")
    return {"root": tmp_path, "base": base, "pes": pes, "v1": v1, "t1": t1, "v2": v2, "c2": c2, "v3": v3, "t3": t3, "sub": sub}


# --- expand_inputs ------------------------------------------------------------------------------

def test_expand_literal_file_and_nfc_input_for_nfd_name(tree):
    vids, problems = D.expand_inputs([str(tree["v1"])])
    assert vids == [tree["v1"]] and problems == []
    nfc = tree["base"] / (NFC(TEAMS) + ".mp4")
    assert not os.path.exists(nfc)
    vids, problems = D.expand_inputs([str(nfc)])
    assert vids == [tree["v1"]] and vids[0].name == tree["v1"].name  # on-disk (NFD) name kept
    assert problems == []


def test_expand_double_space_typed_as_one_and_straight_quote(tree):
    typed = tree["root"] / "databricks schema" / "3.DA" / (TEAMS.replace("’", "'") + ".mp4")
    vids, problems = D.expand_inputs([str(typed)])
    assert vids == [tree["v1"]] and problems == []


def test_expand_folder_non_recursive_sorted_and_recursive(tree):
    vids, problems = D.expand_inputs([str(tree["pes"])])
    assert problems == []
    assert [v.name for v in vids] == ["[PES] Tira dúvidas.mov", f"{PES_VIDEO}.mp4"]  # NFC.casefold order
    vids, _ = D.expand_inputs([str(tree["pes"])], recursive=True)
    assert tree["sub"] in vids and len(vids) == 3


def test_expand_brackets_are_literal_and_quotes_are_stripped(tree):
    folder = f'"{tree["pes"]}"'
    vids, problems = D.expand_inputs([folder])
    assert len(vids) == 2 and problems == []
    vids, problems = D.expand_inputs([str(tree["pes"] / "[PES] Tira dúvidas.mov")])
    assert vids == [tree["v3"]]


def test_expand_glob_selects_videos_only(tree):
    vids, problems = D.expand_inputs([str(tree["pes"] / "*")])
    assert [v.suffix for v in vids] == [".mov", ".mp4"] and problems == []
    vids, problems = D.expand_inputs([str(tree["base"] / "Repasse*160841*.mp4")])
    assert vids == [tree["v1"]]
    vids, problems = D.expand_inputs([str(tree["pes"] / "*.webm")])
    assert vids == [] and problems == [{"input": str(tree["pes"] / "*.webm"), "reason": "not_found"}]


def test_expand_problems_and_dedup(tree, tmp_path):
    empty = tmp_path / "vazia"
    empty.mkdir()
    entries = [str(tree["v2"]), str(tree["pes"]), str(tmp_path / "nao existe.mp4"), str(empty), "  "]
    vids, problems = D.expand_inputs(entries)
    assert vids == [tree["v2"], tree["v3"]]  # v2 listed twice (file and folder): kept once, at its first position
    assert [p["reason"] for p in problems] == ["not_found", "folder_without_videos"]


def test_is_video_name():
    assert D.is_video_name("a.MP4") and D.is_video_name("b.webm") and D.is_video_name("c.mkv")
    assert not D.is_video_name("a.vtt") and not D.is_video_name("mp4")


# --- find_vtt -----------------------------------------------------------------------------------

def test_find_vtt_exact_nfc_casefold(tree):
    assert D.find_vtt(tree["v1"]) == tree["t1"]
    assert D.find_vtt(tree["v3"]) == tree["t3"]      # case of stem and extension ignored
    assert D.find_vtt(tree["v2"]) is None            # Teams name mismatch: strict rule


def test_find_vtt_nfc_video_nfd_subtitle(tmp_path):
    v = touch(tmp_path / (NFC("Gravação ’x’") + ".mp4"))
    t = touch(tmp_path / (NFD("GRAVAÇÃO ’X’") + ".vtt"))
    assert D.find_vtt(v) == t


def test_find_vtt_prefers_byte_identical_stem(tmp_path):
    """NTFS does not normalize: an NFC and an NFD copy of the same name can coexist."""
    v = touch(tmp_path / (NFD("Gravação") + ".mp4"))
    touch(tmp_path / (NFC("Gravação") + ".vtt"))
    exact = touch(tmp_path / (NFD("Gravação") + ".vtt"))
    assert len(list(tmp_path.glob("*.vtt"))) == 2
    assert D.find_vtt(v) == exact


# --- candidates and suggestion --------------------------------------------------------------------

def test_candidates_teams_prefix(tree):
    cands = D.vtt_candidates(tree["v2"])
    assert cands and cands[0]["path"] == tree["c2"] and cands[0]["reason"] == "prefix"


def test_candidates_language_suffix_similar_and_unrelated(tmp_path):
    v = touch(tmp_path / "reuniao_final_do_projeto.mp4")
    lang = touch(tmp_path / "reuniao_final_do_projeto.pt-BR.vtt")
    sim = touch(tmp_path / "reuniao_finall_do_projeto.vtt")
    touch(tmp_path / "outra coisa.vtt")
    cands = D.vtt_candidates(v)
    assert [c["path"] for c in cands] == [lang, sim]
    assert [c["reason"] for c in cands] == ["starts_with_video", "similar"]
    assert D.vtt_candidates(touch(tmp_path / "zzz.mp4")) == []


def test_candidates_limit_and_order(tmp_path):
    v = touch(tmp_path / "abc def ghi jkl.mp4")
    for stem in ("abc", "abc def", "abc def ghi", "abc def ghi jk", "abc def ghi jkx", "abc def ghi jkly"):
        touch(tmp_path / f"{stem}.vtt")
    cands = D.vtt_candidates(v)
    assert len(cands) == D.MAX_CANDIDATES
    assert cands[0]["name"] == "abc def ghi jk.vtt"  # longest prefix first


@pytest.mark.parametrize("text, expected", [
    ("plain", "'plain'"),
    ("it's", "'it''s'"),
    ("DA’s", "'DA’’s'"),
    ("‘citado’", "'‘‘citado’’'"),
    ("[PES] $x `y`", "'[PES] $x `y`'"),
    ("‚b‛", "'‚‚b‛‛'"),
])
def test_ps_quote(text, expected):
    assert D.ps_quote(text) == expected


def test_rename_suggestion_format(tree):
    s = D.rename_suggestion(tree["c2"], tree["v2"])
    assert s == (f"Rename-Item -LiteralPath '{os.path.normpath(str(tree['c2']))}' "
                 f"-NewName '{PES_VIDEO}.vtt'")
    assert "\\" in s and "/" not in s.split("-LiteralPath", 1)[1].split("-NewName")[0].replace("//", "")


def test_rename_suggestion_doubles_curly_quotes(tmp_path):
    v = touch(tmp_path / f"{TEAMS}.mp4")
    c = touch(tmp_path / "Repasse - Fluxo de Carga dos DA’s.vtt")
    s = D.rename_suggestion(c, v)
    assert "DA’’s.vtt'" in s and "DA’’s-20260818_160841" in s


def test_rename_suggestion_on_macos(tmp_path):
    """macOS: mv -n (never overwrites) to the video's name in its folder; a ' closes and reopens the quotes."""
    v = touch(tmp_path / "Reunião d'Ana-20260610_150844-Gravação de Reunião.mp4")
    c = touch(tmp_path / "Reunião d'Ana.vtt")
    s = D.rename_suggestion(c, v, posix=True)
    new = tmp_path / (v.stem + ".vtt")
    assert s == f"mv -n {D.sh_quote(str(c))} {D.sh_quote(str(new))}" and "d'\\''Ana-2026" in s
    assert D.sh_quote("d'Ana") == "'d'\\''Ana'"
    assert D.rename_suggestion(c, v, posix=False).startswith("Rename-Item -LiteralPath '")


def _powershell():
    for exe in ("pwsh", "powershell"):
        path = shutil.which(exe)
        if path:
            return path
    return None


@pytest.mark.skipif(os.name != "nt" or _powershell() is None, reason="needs PowerShell on Windows")
def test_rename_suggestion_runs_in_powershell(tmp_path):
    """The suggested command really renames the file (quotes, ’, brackets, double spaces)."""
    folder = tmp_path / "[PES] pasta  dupla"
    v = touch(folder / f"{TEAMS}.mp4")
    c = touch(folder / "Repasse - Fluxo de Carga dos DA’s.vtt")
    cmd = D.rename_suggestion(c, v)
    r = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr
    assert D.find_vtt(v) is not None and not c.exists()


# --- match_vtts and the error detail ----------------------------------------------------------------

def test_match_vtts_missing_skip_and_explicit(tree):
    pairs, skipped, missing = D.match_vtts([tree["v1"], tree["v2"]])
    assert pairs == [(tree["v1"], tree["t1"])] and skipped == []
    assert len(missing) == 1 and missing[0]["video"] == tree["v2"]
    assert missing[0]["candidates"][0]["path"] == tree["c2"]
    assert missing[0]["suggestion"].startswith("Rename-Item -LiteralPath '")
    pairs, skipped, missing = D.match_vtts([tree["v1"], tree["v2"]], skip_missing=True)
    assert pairs == [(tree["v1"], tree["t1"])] and skipped == [tree["v2"]] and missing == []
    pairs, skipped, missing = D.match_vtts([tree["v2"]], explicit_vtt=tree["c2"])
    assert pairs == [(tree["v2"], tree["c2"])] and not skipped and not missing


def test_missing_vtt_detail(tree, tmp_path):
    lone = touch(tmp_path / "sem par" / "xyz.mp4")
    _, _, missing = D.match_vtts([tree["v2"], lone])
    detail = D.missing_vtt_detail(missing, allow_vtt_flag=False)
    assert detail.startswith("No .vtt subtitles with the same name for 2 videos")
    assert "Indicador PES.vtt (prefix of the video name)" in detail
    assert "Rename-Item -LiteralPath" in detail and f"-NewName '{PES_VIDEO}.vtt'" in detail
    assert "no similar .vtt in the folder" in detail
    assert "--skip-missing" in detail and "--vtt" not in detail
    assert "--vtt" in D.missing_vtt_detail(missing[:1], allow_vtt_flag=True)


# --- macOS AppleDouble companions ("._*") -------------------------------------------------------

@pytest.fixture
def appledouble(tmp_path):
    """An exFAT/SMB folder after a Mac copied a meeting into it: each file has a 4 KB "._" twin."""
    folder = tmp_path / "Volumes" / "REUNIOES"
    video = touch(folder / "reuniao.mp4")
    vtt = touch(folder / "reuniao.vtt")
    touch(folder / "._reuniao.mp4", b"\0" * 4096)
    touch(folder / "._reuniao.vtt", b"\0" * 4096)
    touch(folder / ".DS_Store", b"\0" * 6148)
    return {"folder": folder, "video": video, "vtt": vtt}


def test_is_appledouble_name():
    assert D.is_appledouble_name("._reuniao.mp4")
    assert not D.is_appledouble_name(".DS_Store")
    assert not D.is_appledouble_name("reuniao._x.mp4")


def test_folder_scan_skips_appledouble(appledouble):
    videos, problems = D.expand_inputs([str(appledouble["folder"])])
    assert videos == [appledouble["video"]] and problems == []
    videos, _ = D.expand_inputs([str(appledouble["folder"].parent)], recursive=True)
    assert videos == [appledouble["video"]]


def test_glob_skips_appledouble_unless_asked(appledouble):
    folder = appledouble["folder"]
    videos, problems = D.expand_inputs([str(folder / "*.mp4")])
    assert videos == [appledouble["video"]] and problems == []
    videos, _ = D.expand_inputs([str(folder / "*")])
    assert videos == [appledouble["video"]]
    # a pattern that itself starts with "._" names the companions on purpose
    videos, _ = D.expand_inputs([str(folder / "._*.mp4")])
    assert [v.name for v in videos] == ["._reuniao.mp4"]


def test_vtt_scan_skips_appledouble(appledouble):
    video, vtt = appledouble["video"], appledouble["vtt"]
    assert D.find_vtt(video) == vtt
    assert D.vtt_candidates(video) == []
    pairs, skipped, missing = D.match_vtts([video])
    assert pairs == [(video, vtt)] and skipped == [] and missing == []
    # no "._" twin is ever offered as the subtitle of a video with another name
    other = touch(appledouble["folder"] / "reuniao parte 2.mp4")
    assert all(not c["name"].startswith("._") for c in D.vtt_candidates(other))


def test_explicit_appledouble_file_is_left_alone(appledouble):
    named = appledouble["folder"] / "._reuniao.mp4"
    videos, problems = D.expand_inputs([str(named)])
    assert videos == [named] and problems == []
    pairs, _, _ = D.match_vtts([named], explicit_vtt=appledouble["folder"] / "._reuniao.vtt")
    assert pairs == [(named, appledouble["folder"] / "._reuniao.vtt")]
