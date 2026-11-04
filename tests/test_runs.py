"""va.runs: request.md (and the old names), options, run folders, resume (DL-11), size estimate (DL-12),
workflow_args."""
import inspect
import re
import time

import pytest

from va import contracts, runs as R
from va.common import as_posix

REQUEST = """# videos
C:/Users/digom/Downloads/venda_zero_clientes.mp4
- "C:/Users/digom/Documents/solar/[PES] pasta  dupla/Reunião ’x’.mp4"

# objective
Explique o indicador "venda zero clientes": o que é pedido, de onde vem: cada dado citado.

## Formato
- uma tabela; # não é seção aqui

# options
roles: inline
max_batches: 2
range: 00:10:00-00:20:00
ignore_region: 0.80,0.75,0.20,0.25; 0.00,0.00,0.20,0.06
ignore_region: 0.5,0.5,0.1,0.1
speakers: SPEAKER_01=Lianderson;SPEAKER_03=Francisco
"""

# the same request with the names of before the rename (pedido.md): sections and option keys in Portuguese
LEGACY_REQUEST = """# videos
C:/Users/digom/Downloads/venda_zero_clientes.mp4
- "C:/Users/digom/Documents/solar/[PES] pasta  dupla/Reunião ’x’.mp4"

# objetivo
Explique o indicador "venda zero clientes": o que é pedido, de onde vem: cada dado citado.

## Formato
- uma tabela; # não é seção aqui

# opcoes
papeis: inline
max_lotes: 2
trecho: 00:10:00-00:20:00
ignorar_regiao: 0.80,0.75,0.20,0.25; 0.00,0.00,0.20,0.06
ignorar_regiao: 0.5,0.5,0.1,0.1
falantes: SPEAKER_01=Lianderson;SPEAKER_03=Francisco
"""


# --- request ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [REQUEST, LEGACY_REQUEST], ids=["english", "legacy"])
def test_parse_request_sections_and_literal_objective(text):
    p = R.parse_request(text)
    assert p["videos"] == ["C:/Users/digom/Downloads/venda_zero_clientes.mp4",
                           "C:/Users/digom/Documents/solar/[PES] pasta  dupla/Reunião ’x’.mp4"]
    assert p["objective"] == ('Explique o indicador "venda zero clientes": o que é pedido, de onde vem: cada dado citado.\n'
                              "\n## Formato\n- uma tabela; # não é seção aqui")
    assert p["options"]["papeis"] == "inline" and p["options"]["max_lotes"] == "2"
    assert p["options"]["trecho"] == "00:10:00-00:20:00"            # value = rest after the first ":"
    assert p["options"]["ignorar_regiao"] == ["0.80,0.75,0.20,0.25; 0.00,0.00,0.20,0.06", "0.5,0.5,0.1,0.1"]


def test_parse_request_old_and_new_names_give_the_same_request():
    assert R.parse_request(REQUEST) == R.parse_request(LEGACY_REQUEST)


@pytest.mark.parametrize("text", [
    "\ufeff# Vídeos\r\nC:/a.mp4\r\n\r\n# OBJETIVO\r\n  Resuma.\r\n# Opções\r\n--max-lotes: 3\r\nEsforço Analista: médio\r\n",
    "\ufeff# Videos\r\nC:/a.mp4\r\n\r\n# OBJECTIVE\r\n  Resuma.\r\n# Options #\r\nMax Batches: 3\r\nAnalyst-Effort: médio\r\n",
], ids=["legacy", "english"])
def test_parse_request_tolerates_accents_case_crlf_bom(text):
    p = R.parse_request(text)
    assert p["videos"] == ["C:/a.mp4"] and p["objective"] == "  Resuma."
    assert p["options"] == {"max_lotes": "3", "esforco_analista": "médio"}
    assert R.normalize_options(p["options"])["esforco_analista"] == "medium"


@pytest.mark.parametrize("text, code, needle", [
    ("# objective\nx\n", "pedido_invalido", "# videos"),
    ("# videos\n\n# objective\nx\n", "pedido_invalido", "is empty"),
    ("# videos\nC:/a.mp4\n", "objetivo_ausente", "# objective"),
    ("# videos\nC:/a.mp4\n# objective\n  \n\n", "objetivo_ausente", "is empty"),
    ("lixo antes\n# videos\nC:/a.mp4\n# objective\nx\n", "pedido_invalido", "before the first"),
    ("# videos\nC:/a.mp4\n# videos\nC:/b.mp4\n# objective\nx\n", "pedido_invalido", "twice"),
    ("# videos\nC:/a.mp4\n# objective\nx\n# objetivo\ny\n", "pedido_invalido", "'# objective' appears twice"),
    ("# videos\nC:/a.mp4\n# objective\nx\n# options\nroles inline\n", "pedido_invalido", "without ':' in '# options'"),
    ("# videos\nC:/a.mp4\n# objective\nx\n# options\nturbo: sim\n", "opcao_invalida", "accepted: roles, max_batches"),
    ("# videos\nC:/a.mp4\n# objective\nx\n# options\nroles: inline\npapeis: agents\n", "opcao_invalida",
     "'roles' appears twice"),
    ("# videos\nC:/a.mp4\n# objetivo\n  \n# opcoes\npapeis: inline\n", "objetivo_ausente", "is empty"),
    ("# videos\nC:/a.mp4\n# objetivo\nx\n# opcoes\npapeis inline\n", "pedido_invalido", "without ':'"),
])
def test_parse_request_errors(text, code, needle):
    with pytest.raises(R.RunError) as exc:
        R.parse_request(text)
    assert exc.value.code == code and needle in exc.value.detail


def test_read_request_missing_and_cp1252(tmp_path):
    with pytest.raises(R.RunError) as exc:
        R.read_request(tmp_path / "request.md")
    assert exc.value.code == "pedido_invalido"
    p = tmp_path / "request.md"
    p.write_bytes("# videos\nC:/v.mp4\n# objective\nAção\n".encode("cp1252"))
    assert R.read_request(p)["objective"] == "Ação"


# --- options ---------------------------------------------------------------------------------------

def test_normalize_options_all_keys():
    opts = R.normalize_options({
        "papeis": "Agentes", "max_lotes": "4", "paralelo": "6", "esforco_analista": "alto", "esforco_curador": "xhigh",
        "curadoria": "Por lote", "idioma": "en_US", "trecho": "1:00 a 1:30.5", "falantes": "SPEAKER_01 = Ana ; SPEAKER_02=Bruno Silva",
        "sensibilidade": "ALTA", "ignorar_regiao": ["0.8,0.75,0.2,0.25; 0 0 0.2 0.06"],
    })
    assert opts == {"papeis": "agents", "max_lotes": 4, "paralelo": 6, "esforco_analista": "high",
                    "esforco_curador": "xhigh", "curadoria": "batches", "idioma": "en-US", "trecho": [60.0, 90.5],
                    "falantes": {"SPEAKER_01": "Ana", "SPEAKER_02": "Bruno Silva"}, "sensibilidade": "high",
                    "ignorar_regiao": [[0.8, 0.75, 0.2, 0.25], [0.0, 0.0, 0.2, 0.06]]}
    empty = R.normalize_options({})
    assert set(empty) == set(R.OPTION_KEYS) and all(v is None for v in empty.values())
    assert R.normalize_options({"max_lotes": "0"})["max_lotes"] is None
    assert R.normalize_options({"max_lotes": "todos"})["max_lotes"] is None
    assert R.normalize_options({"trecho": "00:10:00 – 00:20:00"})["trecho"] == [600.0, 1200.0]
    assert [R.normalize_options({"curation": v})["curadoria"] for v in ("plano", "Inicial", "lotes", "batches")] == \
        ["plan", "plan", "batches", "batches"]


def test_request_option_names():
    """Every option has an English key in the request, and the old Portuguese key still works."""
    assert set(R.OPTION_NAMES) == set(R.OPTION_KEYS) and len(set(R.OPTION_NAMES.values())) == len(R.OPTION_KEYS)
    for key, name in R.OPTION_NAMES.items():
        assert R.option_key(name) == key and R.option_key(key) == key
    english = R.normalize_options({"roles": "agents", "max_batches": "all", "parallel": "2", "analyst_effort": "medium",
                                   "curator_effort": "low", "curation": "batches", "language": "en", "range": "1:00-1:30",
                                   "speakers": "SPEAKER_01=Ana", "sensitivity": "low", "ignore_region": ["0.8,0.75,0.2,0.25"]})
    legacy = R.normalize_options({"papeis": "agentes", "max_lotes": "todos", "paralelo": "2", "esforco_analista": "medio",
                                  "esforco_curador": "baixo", "curadoria": "lotes", "idioma": "en", "trecho": "1:00 a 1:30",
                                  "falantes": "SPEAKER_01=Ana", "sensibilidade": "baixa",
                                  "ignorar_regiao": ["0.8,0.75,0.2,0.25"]})
    assert english == legacy and english["curadoria"] == "batches" and english["max_lotes"] is None


@pytest.mark.parametrize("key, value, needle", [
    ("papeis", "auto", "agents or inline"),
    ("max_lotes", "dois", "integer"),
    ("max_lotes", "-1", "integer"),
    ("paralelo", "7", "1 to 6"),
    ("paralelo", "0", "1 to 6"),
    ("esforco_analista", "ultra", "low"),
    ("curadoria", "sempre", "plan or batches"),
    ("idioma", "português!", "pt-BR"),
    ("trecho", "00:20:00-00:10:00", "after the start"),
    ("trecho", "10 minutos", "hh:mm:ss"),
    ("falantes", "SPEAKER_01:Ana", "LABEL=Name"),
    ("sensibilidade", "maxima", "low, normal or high"),
    ("ignorar_regiao", "0,8,0,75,0,2,0,25", "decimal point"),
    ("ignorar_regiao", "0.9,0.9,0.2,0.2", "x+w"),
    ("ignorar_regiao", "0.1,0.1,0,0.2", "fractions"),
    ("papeis", "   ", "empty"),
    ("max_lotes", "dois", "max_batches (--max-lotes): 'dois' is invalid"),
    ("ignorar_regiao", "1,1", "ignore_region (--ignorar-regiao)"),
])
def test_normalize_options_errors(key, value, needle):
    with pytest.raises(R.RunError) as exc:
        R.normalize_options({key: value})
    assert exc.value.code == "opcao_invalida" and needle in exc.value.detail


def test_workflow_options_defaults():
    assert R.workflow_options(R.normalize_options({})) == {"max_batches": None, "max_parallel_videos": 3,
                                                           "analyst_effort": "high", "curator_effort": "high",
                                                           "curation": "plan"}
    o = R.workflow_options(R.normalize_options({"max_lotes": "2", "paralelo": "1", "esforco_curador": "medium",
                                                "curadoria": "lotes", "esforco_analista": "alto"}))
    assert o == {"max_batches": 2, "max_parallel_videos": 1, "analyst_effort": "high", "curator_effort": "medium",
                 "curation": "batches"}


# --- run folders -------------------------------------------------------------------------------------

def test_new_run_name_and_collision(tmp_path):
    t = time.mktime((2026, 10, 6, 15, 30, 12, 0, 0, -1))
    rid, rdir = R.new_run(tmp_path / "runs", "Repasse - Fluxo de Carga dos DA’s-20260818_160841-Gravação de Reunião", now=t)
    assert rid == "20261006-153012-repasse-fluxo-de-carga" and rdir.is_dir() and rdir.name == rid
    rid2, _ = R.new_run(tmp_path / "runs", "Repasse - Fluxo de Carga dos DA’s-20260818_160841-Gravação de Reunião", now=t)
    assert rid2 == rid + "-2"
    assert R.run_slug("venda_zero_clientes") == "venda-zero-clientes" and len(R.run_slug("x" * 80)) <= 24
    assert R.run_slug("漢字") == "video"


def test_unique_slugs_dedup_and_length():
    long = "Treinamento Playbook Ágil - Projeto Refpet-20260203 090538-Gravação"
    slugs = R.unique_slugs(["venda_zero_clientes", "Venda Zero Clientes", "venda-zero-clientes", long, long, "漢字", "!!!"])
    assert slugs[:3] == ["venda-zero-clientes", "venda-zero-clientes-2", "venda-zero-clientes-3"]
    assert slugs[3] != slugs[4] and slugs[4].endswith("-2")
    assert slugs[5:] == ["video", "video-2"]
    rx = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    assert all(rx.match(s) and len(s) <= 40 for s in slugs)
    forty = R.unique_slugs(["abcdefghij-klmnopqrst-uvwxyz-20260101_101010"] * 2)
    assert forty[1].endswith("-2") and len(forty[1]) <= 40 and forty[0] != forty[1]


def test_objective_markdown_literal():
    md = R.objective_markdown('\n\nExplique "x": y\n  com recuo\n\n', ["papeis: inline"], ["slug: título"])
    assert md == ('# Objetivo (literal)\n\nExplique "x": y\n  com recuo\n\n# Opções\n\n- papeis: inline\n\n'
                  "# Vídeos\n\n- slug: título\n")


def test_copy_roles(tmp_path, skill_dir):
    roles = tmp_path / "roles"
    names = R.copy_roles(skill_dir, roles)
    assert names == sorted(p.name for p in (skill_dir / "agents").glob("*.md"))
    for n in names:
        assert (roles / n).read_bytes() == (skill_dir / "agents" / n).read_bytes()
    (roles / names[0]).write_text("velho", encoding="utf-8")
    R.copy_roles(skill_dir, roles)
    assert (roles / names[0]).read_bytes() == (skill_dir / "agents" / names[0]).read_bytes()
    assert not list(roles.glob(".*.tmp"))


# --- request consumption ----------------------------------------------------------------------------

def test_consume_request_success_and_failure(tmp_path):
    req_dir, run = tmp_path / "requests", tmp_path / "runs" / "r1"
    req_dir.mkdir()
    run.mkdir(parents=True)
    p = req_dir / "request.md"
    p.write_text("um", encoding="utf-8")
    assert R.consume_request(p, run, ok=True) == run / "request.md"
    assert not p.exists() and (run / "request.md").read_text(encoding="utf-8") == "um"
    (req_dir / "request.error.md").write_text("antigo", encoding="utf-8")
    p.write_text("dois", encoding="utf-8")
    assert R.consume_request(p, None, ok=False) == req_dir / "request.error.md"
    assert not p.exists() and (req_dir / "request.error.md").read_text(encoding="utf-8") == "dois"  # older replaced
    p.write_text("três", encoding="utf-8")
    run2 = tmp_path / "runs" / "r2"
    run2.mkdir()
    R.consume_request(p, run2, ok=False, copy_to_run=True)
    assert (run2 / "request.md").read_text(encoding="utf-8") == "três"
    assert (req_dir / "request.error.md").read_text(encoding="utf-8") == "três"
    assert R.consume_request(p, run, ok=True) is None  # nothing left to consume


def test_consume_request_with_the_old_name(tmp_path):
    """A request still named pedido.md (pedidos/, before the rename): its copy in the run is
    request.md, like any other; on failure it becomes pedido.erro.md, as before."""
    ped_dir, run = tmp_path / "pedidos", tmp_path / "runs" / "r1"
    ped_dir.mkdir()
    run.mkdir(parents=True)
    p = ped_dir / "pedido.md"
    p.write_text("um", encoding="utf-8")
    assert R.consume_request(p, run, ok=True) == run / "request.md" and not (run / "pedido.md").exists()
    p.write_text("dois", encoding="utf-8")
    assert R.consume_request(p, None, ok=False) == ped_dir / "pedido.erro.md"
    assert not p.exists() and sorted(f.name for f in ped_dir.iterdir()) == ["pedido.erro.md"]
    other = ped_dir / "meu pedido.md"
    other.write_text("três", encoding="utf-8")
    assert R.consume_request(other, None, ok=False) == ped_dir / "meu pedido.error.md"


def test_old_function_names_still_work(tmp_path):
    """parse_pedido, read_pedido and consume_pedido, the names of before the rename, still import and
    take the same parameters as before, by position or by keyword."""
    from va.runs import consume_pedido, parse_pedido, read_pedido
    assert parse_pedido is R.parse_request and read_pedido is R.read_request
    assert {"parse_pedido", "read_pedido", "consume_pedido"} <= set(R.__all__)
    assert [list(inspect.signature(f).parameters) for f in (parse_pedido, read_pedido, consume_pedido)] ==         [["text"], ["path"], ["pedido", "run_dir", "ok", "copy_to_run"]]
    assert parse_pedido(LEGACY_REQUEST) == parse_pedido(text=LEGACY_REQUEST) == R.parse_request(REQUEST)
    ped_dir, run = tmp_path / "pedidos", tmp_path / "runs" / "r1"
    ped_dir.mkdir()
    run.mkdir(parents=True)
    p = ped_dir / "pedido.md"
    p.write_text(LEGACY_REQUEST, encoding="utf-8")
    assert read_pedido(p) == read_pedido(path=p) and read_pedido(p)["options"]["papeis"] == "inline"
    assert consume_pedido(p, run, ok=True) == run / "request.md" and not p.exists()
    p.write_text("dois", encoding="utf-8")
    assert consume_pedido(pedido=p, run_dir=run, ok=False, copy_to_run=True) == ped_dir / "pedido.erro.md"
    assert (run / "request.md").read_text(encoding="utf-8") == "dois" and not p.exists()


# --- resume -------------------------------------------------------------------------------------------

def _out(tmp_path, *, ledger=False, reports=(), prompts=(), report=False, legacy=False):
    out = tmp_path / "run" / "video"
    R.scaffold_video(out)
    if ledger:
        (out / "ledger.md").write_text("Integrado até o lote: 001 de 005", encoding="utf-8")
    for n in reports:
        (out / "reports" / f"batch_{n:03d}.md").write_text("r", encoding="utf-8")
    for n in prompts:
        (out / "prompts" / f"batch_{n:03d}.md").write_text("p", encoding="utf-8")
    if report:
        (out / "relatorio.md").write_text("final", encoding="utf-8")
    if legacy:  # final report of a run made before the rename to relatorio.md
        (out / "report.md").write_text("antigo", encoding="utf-8")
    return out


def test_final_report_names():
    # the harness blocks subagents from writing report.md, summary.md and findings.md
    assert R.FINAL_MD == "relatorio.md" and R.LEGACY_FINAL_MD == "report.md"
    assert R.FINAL_MD not in ("report.md", "summary.md", "findings.md")


def test_resume_no_ledger_discards_and_restarts(tmp_path):
    out = _out(tmp_path, reports=(1, 2), prompts=(1, 2, 3), report=True)
    st = R.resume_video(out, 5, None, "20261006-160000")
    assert st == {"start_batch": 1, "last": 5, "ledger_exists": False, "start_prompt_exists": False, "final_exists": False,
                  "remaining": 5, "actions": st["actions"]}
    disc = out / "_descartado" / "20261006-160000"
    assert sorted(p.name for p in (disc / "reports").iterdir()) == ["batch_001.md", "batch_002.md"]
    assert len(list((disc / "prompts").iterdir())) == 3
    assert (out / "prompts").is_dir() and not any((out / "prompts").iterdir())
    assert (out / "relatorio.anterior.md").read_text(encoding="utf-8") == "final" and not (out / "relatorio.md").exists()
    assert "relatorio.md -> relatorio.anterior.md" in st["actions"]
    st2 = R.resume_video(out, 5, None, "20261006-160000")  # nothing left to move; same stamp is fine
    assert st2["start_batch"] == 1 and st2["actions"] == []


def test_resume_with_ledger_start_and_prompt(tmp_path):
    out = _out(tmp_path, ledger=True, reports=(1, 2), prompts=(1, 2, 3))
    st = R.resume_video(out, 5, None, "s")
    assert (st["start_batch"], st["last"], st["ledger_exists"], st["start_prompt_exists"], st["final_exists"], st["remaining"]) == \
        (3, 5, True, True, False, 3)
    (out / "prompts" / "batch_003.md").unlink()
    assert R.resume_video(out, 5, None, "s")["start_prompt_exists"] is False


def test_resume_max_batches_and_final(tmp_path):
    out = _out(tmp_path, ledger=True, reports=(1, 2), report=True)
    st = R.resume_video(out, 5, 2, "s")          # test mode limited to 2: nothing left -> FINAL already done
    assert (st["start_batch"], st["last"], st["final_exists"], st["remaining"]) == (3, 2, True, 0)
    assert (out / "relatorio.md").exists() and st["actions"] == []
    st = R.resume_video(out, 5, 4, "s")          # more batches now: relatorio.md becomes relatorio.anterior.md
    assert (st["start_batch"], st["last"], st["final_exists"], st["remaining"]) == (3, 4, False, 2)
    assert not (out / "relatorio.md").exists() and (out / "relatorio.anterior.md").exists()
    out2 = _out(tmp_path / "b", ledger=True, reports=(1, 2, 3))
    st = R.resume_video(out2, 3, None, "s")      # all reports, no relatorio.md: FINAL only
    assert (st["start_batch"], st["final_exists"], st["remaining"]) == (4, False, 0)
    assert R.estimate_agents([st]) == 1


def test_resume_legacy_report_counts_as_final(tmp_path):
    out = _out(tmp_path, ledger=True, reports=(1, 2), legacy=True)
    st = R.resume_video(out, 5, 2, "s")          # nothing left: the legacy report.md is the final, renamed
    assert (st["start_batch"], st["final_exists"], st["remaining"]) == (3, True, 0)
    assert st["actions"] == ["report.md -> relatorio.md (new name of the final report)"]
    assert (out / "relatorio.md").read_text(encoding="utf-8") == "antigo" and not (out / "report.md").exists()
    st = R.resume_video(out, 5, 2, "s")          # idempotent
    assert st["final_exists"] is True and st["actions"] == []
    out2 = _out(tmp_path / "b", ledger=True, reports=(1, 2), legacy=True)
    st = R.resume_video(out2, 5, None, "s")      # work left: the legacy final is backed up under the new name
    assert (st["start_batch"], st["final_exists"], st["remaining"]) == (3, False, 3)
    assert st["actions"] == ["report.md -> relatorio.anterior.md"]
    assert (out2 / "relatorio.anterior.md").read_text(encoding="utf-8") == "antigo" and not (out2 / "report.md").exists()


def test_resume_with_both_final_names(tmp_path):
    out = _out(tmp_path, ledger=True, reports=(1, 2), report=True, legacy=True)
    st = R.resume_video(out, 2, None, "s")       # nothing left: relatorio.md wins, the legacy file stays put
    assert st["final_exists"] is True and st["actions"] == []
    assert (out / "relatorio.md").read_text(encoding="utf-8") == "final" and (out / "report.md").exists()
    st = R.resume_video(out, 3, None, "s")       # work left: both are backed up, nothing is overwritten
    assert st["final_exists"] is False
    assert st["actions"] == ["relatorio.md -> relatorio.anterior.md", "report.md -> report.anterior.md"]
    assert (out / "relatorio.anterior.md").read_text(encoding="utf-8") == "final"
    assert (out / "report.anterior.md").read_text(encoding="utf-8") == "antigo"
    assert not (out / "relatorio.md").exists() and not (out / "report.md").exists()


def test_resume_combined(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    assert R.resume_combined(run, [{"final_exists": False}]) == (False, [])
    (run / "relatorio.md").write_text("c", encoding="utf-8")
    assert R.resume_combined(run, [{"final_exists": True}, {"final_exists": True}]) == (True, [])
    exists, acts = R.resume_combined(run, [{"final_exists": True}, {"final_exists": False}])
    assert not exists and acts == ["relatorio.md combined -> relatorio.anterior.md"]
    assert (run / "relatorio.anterior.md").exists() and not (run / "relatorio.md").exists()


def test_resume_combined_legacy(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "report.md").write_text("c", encoding="utf-8")
    exists, acts = R.resume_combined(run, [{"final_exists": True}, {"final_exists": True}])
    assert exists and acts == ["report.md combined -> relatorio.md (new name of the final report)"]
    assert (run / "relatorio.md").read_text(encoding="utf-8") == "c" and not (run / "report.md").exists()
    (run / "relatorio.md").rename(run / "report.md")
    exists, acts = R.resume_combined(run, [{"final_exists": True}, {"final_exists": False}])
    assert not exists and acts == ["report.md combined -> relatorio.anterior.md"]
    assert (run / "relatorio.anterior.md").read_text(encoding="utf-8") == "c" and not (run / "report.md").exists()


def test_estimate_agents_formula():
    # default (curation plan): INIT + R analysts + FINAL; on resume with a ledger, without the INIT
    assert R.estimate_agents([{"remaining": 4}]) == 6
    assert R.estimate_agents([{"remaining": 2}, {"remaining": 2}, {"remaining": 2}]) == 13  # + CROSS
    assert R.estimate_agents([{"remaining": 3, "final_exists": True}, {"remaining": 1}]) == 4
    assert R.estimate_agents([{"remaining": 2, "ledger_exists": True}]) == 3
    assert R.estimate_agents([{"remaining": 0, "ledger_exists": True}]) == 1   # FINAL only
    # curation batches: one NEXT before each batch, except the 1st
    assert R.estimate_agents([{"remaining": 4}], "batches") == 9
    assert R.estimate_agents([{"remaining": 2}, {"remaining": 2}, {"remaining": 2}], "batches") == 16
    assert R.estimate_agents([{"remaining": 3, "final_exists": True}, {"remaining": 1}], "batches") == 4
    assert R.estimate_agents([{"remaining": 0, "ledger_exists": True}], "batches") == 1


def _make_run(home, name, with_wargs=True):
    d = home / "runs" / name
    d.mkdir(parents=True)
    (d / "run.json").write_text('{"workflow_args": {}}' if with_wargs else "{}", encoding="utf-8")
    return d


def test_find_run(tmp_path):
    home = tmp_path / "home"
    with pytest.raises(R.RunError) as exc:
        R.find_run(home, "ultimo")
    assert exc.value.code == "run_nao_encontrado"
    a = _make_run(home, "20261005-100000-a")
    b = _make_run(home, "20261006-100000-b")
    _make_run(home, "20261007-100000-falhou", with_wargs=False)  # failed prepare: no workflow_args
    assert R.find_run(home, "ultimo") == b and R.find_run(home, "último") == b and R.find_run(home, "") == b
    assert R.find_run(home, "20261005-100000-a") == a
    assert R.find_run(home, "20261005") == a                       # unique prefix
    assert R.find_run(home, str(a)) == a and R.find_run(home, as_posix(a)) == a
    for ref in ("2026", "nada", "20261007-100000-falhou", str(tmp_path / "x")):
        with pytest.raises(R.RunError):
            R.find_run(home, ref)


# --- workflow_args ---------------------------------------------------------------------------------------

def test_build_workflow_args_is_valid(tmp_path):
    run_dir = tmp_path / "runs" / "20261006-153012-venda"
    roles = tmp_path / "roles"
    videos = [{"slug": "venda", "title": "venda_zero_clientes", "duration_s": 441.2165, "n_frames": 60,
               "batches_dir": tmp_path / "cache" / "venda-1a2b3c4d" / "batches-5e6f7a8b",
               "out_dir": run_dir / "venda", "batch_images": [20, 19, 18]},
              {"slug": "venda-2", "title": "Reunião ’x’", "duration_s": 59.999, "n_frames": 5,
               "batches_dir": tmp_path / "cache" / "b" / "batches-1", "out_dir": run_dir / "venda-2", "batch_images": [5],
               "start_batch": 2, "ledger_exists": True, "start_prompt_exists": False, "final_exists": True}]
    wa = R.build_workflow_args(run_id=run_dir.name, run_dir=run_dir, roles_dir=roles, roles_mode="inline",
                               language="pt-BR", options={"max_batches": None, "max_parallel_videos": 3,
                                                          "analyst_effort": "high", "curator_effort": "high"},
                               combined_exists=False, videos=videos)
    assert contracts.check_workflow_args(wa) == []
    assert wa["videos"][0]["duration"] == "00:07:21" and wa["videos"][1]["duration"] == "00:00:59"
    assert wa["objective_path"] == as_posix(run_dir) + "/objective.md"
    assert "\\" not in str(wa)
    assert wa["videos"][1]["final_exists"] is True and wa["videos"][0]["start_batch"] == 1
    assert wa["options"]["curation"] == "plan"      # options without curation: the default
    assert list(wa) == ["schema_version", "run_id", "run_dir", "objective_path", "roles_dir", "roles_mode", "language",
                        "options", "combined_exists", "videos"]
