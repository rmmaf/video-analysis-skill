"""Self-test of tests/check_run.py with a synthetic run built in tmp_path."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import importlib.util  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

_spec = importlib.util.spec_from_file_location("va_check_run", Path(__file__).resolve().with_name("check_run.py"))
check_run = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_run  # dataclasses resolve annotations through the registered module
_spec.loader.exec_module(check_run)

HEAD = """# Lote B00{n} de 3 · {rng}
Vídeo: Teste · 00:01:30.0 · 1568×882
Falantes: S1 = SPEAKER_00
Folha de contato: {B}/sheet_00{n}.jpg
Leitura: frame vale de início a fim; img = instante da imagem; fala completa na 1ª vez no lote, depois ↳ id; ≈ horário aproximado; (cont.) começou antes do lote.
"""
BATCH1 = """
### F0001 · 00:00:00.0–00:00:10.0 · img 00:00:00.5
{F}/F0001_000000.jpg
- C0001 00:00:00.6–00:00:05.0 S1: Hoje vamos abrir o painel de vendas e conferir o indicador de clientes sem compra.
- C0002 00:00:05.2–00:00:09.8 S1: Primeiro eu filtro a região sul.

### F0002 · 00:00:10.0–00:00:20.0 · img 00:00:10.5 · movimento
{F}/F0002_000010.jpg
- ↳ C0002
- C0003 ≈00:00:10.5–00:00:19.0 S1: Aqui aparece a tabela com a quantidade de clientes que não compraram no mês.

### F0003 · 00:00:20.0–00:00:30.0 · img 00:00:20.5 · = F0001 (mesma tela, acima)
- (sem fala)

### F0004 · 00:00:30.0–00:00:40.0 · img 00:00:30.5
{F}/F0004_000030.jpg
- C0004 00:00:30.2–00:00:38.0 S1: Esse número vem da base de pedidos que o processo noturno atualiza toda madrugada.
- C0005 00:00:38.5–00:00:44.0 S1: Então quando o pedido entra depois da meia noite ele só aparece no dia seguinte.
"""
BATCH2 = """
### F0005 · 00:00:40.0–00:00:50.0 · img 00:00:40.5
{F}/F0005_000040.jpg
- C0005 00:00:38.5–00:00:44.0 (cont.) S1: Então quando o pedido entra depois da meia noite ele só aparece no dia seguinte.

### F0006 · 00:00:50.0–00:01:00.0 · img 00:00:50.5 · = F0002
{F}/F0002_000010.jpg (revisita de F0002)
- (sem fala)

### F0007 · 00:01:00.0–00:01:10.0 · img 00:01:00.5
{F}/F0007_000100.jpg
- C0006 00:01:00.2–00:01:09.0 S1: Por hoje é isso, obrigado a todos pela atenção e até a próxima reunião.
"""
LEDGER = """# Ledger · teste
{hdr} · Tipo: tela · Idioma: pt-BR
## 1. Objetivo interpretado
Explicar o indicador de clientes sem compra e de onde vem cada dado.
## 2. Mapa e plano
| lotes | intervalo | assunto provável | foco | estado |
|---|---|---|---|---|
| 001 | 00:00–00:40 | painel e filtro | nome do indicador | feito |
| 002 | 00:40–01:10 | origem do dado | base de pedidos | pendente |
| 003 | 01:10–01:30 | encerramento | — | fora do run |
## 7. Lacunas
nenhuma
"""
PROMPT = """# Lote 00{n} de 003 · teste
Linha do tempo (frames, intervalos e falas): `{B}/batch_00{n}.md`
## Objetivo deste lote
Mostrar de onde vem o indicador de clientes sem compra.
## Foco
{foco}
## Perguntas
Q1. Qual filtro aparece na tela?
Q2. De onde vem o número citado?
## Formato
Contrato padrão; até 600 palavras.
"""
REPORT = """# Lote 00{n} · teste
## 1. Linha do tempo
| frame | intervalo | tela | fala | relação |
|---|---|---|---|---|
{rows}
## 2. Respostas
Q1. Região sul — [F000{a} · 00:00:05]
"""
ROWS1 = "| F0001–F0002 | 00:00:00–00:00:20 | painel | S1: filtro | confirma |\n| F0003 | 00:00:20 | = F0001, sem mudança | — | só tela |\n| F0004 | 00:00:30 | tabela | S1: base de pedidos | complementa |"
ROWS2 = "| F0005 | 00:00:40 | tabela | S1: horário de corte | confirma |\n| F0006 | 00:00:50 | = F0002, sem mudança | — | só tela |\n| F0007 | 00:01:00 | encerramento | S1: despedida | confirma |"
FINAL = """# Relatório · teste
Objetivo: explicar o indicador de clientes sem compra.
Cobertura: lotes 2/3 · frames 6/6 · lacunas 0
## 1. Resposta ao objetivo
O indicador conta clientes sem pedido no mês [00:00:10 · F0002].
"""


def make_run(tmp_path: Path, *, run_json: bool = True, ledger_hdr: str = "Integrado até o lote: 001 de 003",
             foco1: str = "- F0001–F0002: filtros e nome exato do indicador.\n- F0004: origem do número.",
             foco2: str = "- F0005–F0007: horário de corte e encerramento.", rows2: str = ROWS2,
             curation: str | None = "batches") -> tuple[Path, dict]:
    bdir = tmp_path / "cache" / "teste-1a2b3c4d" / "batches-5e6f7a8b"
    fdir = tmp_path / "cache" / "teste-1a2b3c4d" / "frames-0a0b0c0d"
    run_dir = (tmp_path / "runs" / "20261006-120000-teste")
    out = run_dir / "teste"
    for d in (bdir, out / "prompts", out / "reports"):
        d.mkdir(parents=True)
    B, F = bdir.as_posix(), fdir.as_posix()
    (bdir / "batch_001.md").write_text(HEAD.format(n=1, rng="F0001–F0004", B=B) + BATCH1.format(F=F), encoding="utf-8")
    (bdir / "batch_002.md").write_text(HEAD.format(n=2, rng="F0005–F0007", B=B) + BATCH2.format(F=F), encoding="utf-8")
    (out / "ledger.md").write_text(LEDGER.format(hdr=ledger_hdr), encoding="utf-8")
    (out / "prompts" / "batch_001.md").write_text(PROMPT.format(n=1, B=B, foco=foco1), encoding="utf-8")
    (out / "prompts" / "batch_002.md").write_text(PROMPT.format(n=2, B=B, foco=foco2), encoding="utf-8")
    (out / "reports" / "batch_001.md").write_text(REPORT.format(n=1, a=1, rows=ROWS1), encoding="utf-8")
    (out / "reports" / "batch_002.md").write_text(REPORT.format(n=2, a=5, rows=rows2), encoding="utf-8")
    (out / "relatorio.md").write_text(FINAL, encoding="utf-8")
    wa = {
        "schema_version": 1, "run_id": "20261006-120000-teste", "run_dir": run_dir.resolve().as_posix(),
        "objective_path": (run_dir / "objective.md").as_posix(), "roles_dir": (tmp_path / "roles").as_posix(),
        "roles_mode": "inline", "language": "pt-BR",
        "options": {"max_batches": 2, "max_parallel_videos": 3, "analyst_effort": "high", "curator_effort": "high",
                    "curation": curation},
        "combined_exists": False,
        "videos": [{"slug": "teste", "title": "Teste", "duration": "00:01:30", "n_frames": 9, "batches_dir": B,
                    "out_dir": out.as_posix(), "batch_images": [3, 3, 4], "start_batch": 1, "ledger_exists": False,
                    "start_prompt_exists": False, "final_exists": False}],
    }
    if curation is None:
        del wa["options"]["curation"]  # run that predates the option
    if run_json:
        (run_dir / "run.json").write_text(json.dumps({"argv": [], "workflow_args": wa}, ensure_ascii=False), encoding="utf-8")
    return run_dir, wa


def table(capsys) -> dict[str, list[str]]:
    out = capsys.readouterr().out.splitlines()
    return {level: [ln for ln in out if ln.startswith(level)] for level in ("FAIL", "WARN")}


def fails(capsys) -> list[str]:
    return table(capsys)["FAIL"]


def ret(frames: str | None = "6/6", status: str = "partial", start: int = 1) -> str:
    return json.dumps({"run_id": "20261006-120000-teste", "videos": [{"slug": "teste", "status": status, "frames": frames,
                       "batches": {"total": 3, "planned": 2, "from": start, "ok": 2, "partial": [], "failed": []}}], "combined": None})


def batch_text(n: int, B: str = "C:/va/cache/teste-1a2b3c4d/batches-5e6f7a8b", F: str = "C:/va/cache/teste-1a2b3c4d/frames-0a0b0c0d") -> str:
    return HEAD.format(n=n, rng="F0001–F0004" if n == 1 else "F0005–F0007", B=B) + (BATCH1 if n == 1 else BATCH2).format(F=F)


def test_parse_batch_reads_frames_images_and_cues(tmp_path):
    run_dir, wa = make_run(tmp_path)
    b1 = check_run.parse_batch((Path(wa["videos"][0]["batches_dir"]) / "batch_001.md").read_text(encoding="utf-8"))
    b2 = check_run.parse_batch((Path(wa["videos"][0]["batches_dir"]) / "batch_002.md").read_text(encoding="utf-8"))
    assert b1.frames == ["F0001", "F0002", "F0003", "F0004"] and b1.images == 3
    assert b2.frames == ["F0005", "F0006", "F0007"] and b2.images == 3
    assert b1.sheet and b2.sheet and not b1.problems and not b2.problems, (b1.problems, b2.problems)
    assert b1.cues["C0003"].startswith("Aqui aparece a tabela")
    assert b2.cues["C0005"] == b1.cues["C0005"]  # "(cont.)" and "S1:" are removed from the text


def test_parse_batch_accepts_english_labels_and_flags_format_deviations():
    en1 = batch_text(1).replace("Folha de contato:", "Contact sheet:").replace("(mesma tela, acima)", "(same screen, above)")
    en2 = batch_text(2).replace("Folha de contato:", "Contact sheet:").replace("(revisita de F0002)", "(revisit of F0002)")
    for text in (en1, en2):
        b = check_run.parse_batch(text)
        assert b.images == 3 and b.sheet and not b.problems, b.problems  # the contact sheet never counts as an image
    F = "C:/va/cache/teste-1a2b3c4d/frames-0a0b0c0d"
    cases = {
        "malformed image line": batch_text(1).replace(f"{F}/F0004_000030.jpg", F.replace("/", "\\") + "\\F0004_000030.jpg"),
        "in-batch revisit with an image line": batch_text(1).replace("(mesma tela, acima)\n", f"(mesma tela, acima)\n{F}/F0003_000020.jpg\n"),
        'none, expected "(revisita de F0002)"': batch_text(2).replace(" (revisita de F0002)", ""),
        "image path repeated": batch_text(1).replace(f"{F}/F0004_000030.jpg", f"{F}/F0001_000000.jpg"),
        "2 image lines": batch_text(2).replace(f"{F}/F0007_000100.jpg", f"{F}/F0007_000100.jpg\n{F}/F0008_000105.jpg"),
    }
    for want, text in cases.items():
        b = check_run.parse_batch(text)
        assert any(want in p for p in b.problems), (want, b.problems)
    assert not check_run.parse_batch(batch_text(1).replace("Folha de contato", "Prancha")).sheet


def test_batch_format_outside_the_contract_fails(tmp_path, capsys):
    run_dir, wa = make_run(tmp_path)
    tl = Path(wa["videos"][0]["batches_dir"]) / "batch_002.md"
    tl.write_text(tl.read_text(encoding="utf-8").replace(" (revisita de F0002)", ""), encoding="utf-8")
    assert check_run.main([str(run_dir)]) == 1
    bad = fails(capsys)
    assert len(bad) == 1 and "batch.format" in bad[0] and "teste 002" in bad[0] and "F0006" in bad[0], bad


def test_good_run_passes_with_and_without_return(tmp_path, capsys):
    run_dir, _ = make_run(tmp_path)
    assert check_run.main([str(run_dir)]) == 0, fails(capsys)
    assert check_run.main([str(run_dir), "--return", ret()]) == 0, fails(capsys)
    assert check_run.main([str(run_dir), "--return", ret(frames=None, start=3)]) == 0  # resume with the FINAL only
    t = table(capsys)
    assert not t["FAIL"] and not t["WARN"], t


def test_prompt_that_copies_a_cue_or_lists_frames_fails(tmp_path, capsys):
    ids17 = ", ".join(f"F{k:04d}" for k in [*range(1, 5), *range(10, 23)])
    run_dir, _ = make_run(tmp_path, foco1=f"- {ids17}: tudo.",
                          foco2="- Ele diz: então quando o pedido entra depois da meia noite ele só aparece depois.")
    assert check_run.main([str(run_dir)]) == 1
    bad = fails(capsys)
    assert any("prompt.cue_copy" in ln and "teste 002" in ln and "C0005 (12 words)" in ln for ln in bad), bad
    assert any("prompt.ids" in ln and "teste 001" in ln and "17 frame references" in ln for ln in bad), bad
    assert len(bad) == 2, bad


def test_short_quote_and_ids_above_the_role_are_warn(tmp_path, capsys):
    ids10 = ", ".join(f"F{k:04d}" for k in range(1, 11))
    run_dir, _ = make_run(tmp_path, foco1=f"- {ids10}: confira cada um.",
                          foco2='- Note o horário de corte ("quando o pedido entra depois da meia noite").')
    assert check_run.main([str(run_dir)]) == 0
    t = table(capsys)
    assert not t["FAIL"], t
    assert any("prompt.ids" in ln and "teste 001" in ln and "10 frame references" in ln for ln in t["WARN"]), t
    assert any("prompt.cue_copy" in ln and "teste 002" in ln and "C0005 (8 words)" in ln for ln in t["WARN"]), t
    assert len(t["WARN"]) == 2, t


def test_word_budget_has_warn_before_fail(tmp_path, capsys):
    run_dir, _ = make_run(tmp_path, foco2="- " + "detalhe " * 430)
    assert check_run.main([str(run_dir)]) == 0
    t = table(capsys)
    assert not t["FAIL"] and any("prompt.words" in ln and "teste 002" in ln for ln in t["WARN"]), t
    run_dir2, _ = make_run(tmp_path / "b", foco2="- " + "detalhe " * 700)
    assert check_run.main([str(run_dir2)]) == 1
    bad = fails(capsys)
    assert len(bad) == 1 and "prompt.words" in bad[0] and "teste 002" in bad[0], bad


def test_copied_cues_separates_short_quote_from_copy():
    words = [f"w{k}" for k in range(1, 15)]
    cues = {"C1": " ".join(words[:8]), "C2": " ".join(words)}
    hits = {cid: (k, hard) for cid, k, hard in check_run.copied_cues("ele disse " + " ".join(words[:8]) + " e saiu", cues)}
    assert hits == {"C1": (8, True), "C2": (8, False)}  # all of C1 (8 words) is a copy; 8 of the 14 of C2, a quote
    hits = {cid: (k, hard) for cid, k, hard in check_run.copied_cues("x " + " ".join(words[2:14]) + " y", cues)}
    assert hits == {"C2": (12, True)}


def test_report_missing_a_frame_fails_and_a_range_covers(tmp_path, capsys):
    run_dir, _ = make_run(tmp_path, rows2="| F0005 | 00:00:40 | tabela | S1: corte | confirma |\n| F0007 | 00:01:00 | fim | S1: despedida | confirma |")
    assert check_run.main([str(run_dir)]) == 1
    bad = fails(capsys)
    assert len(bad) == 1 and "report.frames" in bad[0] and "F0006" in bad[0], bad
    run_dir2, _ = make_run(tmp_path / "b", rows2="| F0005–F0007 | 00:00:40–00:01:10 | tabela e fim | S1: corte | confirma |")
    assert check_run.main([str(run_dir2)]) == 0, fails(capsys)


def test_malformed_ledger_and_divergent_return_fail(tmp_path, capsys):
    run_dir, _ = make_run(tmp_path, ledger_hdr="Integrado até o lote: 001")
    assert check_run.main([str(run_dir), "--return", ret(frames="5/6", status="ok")]) == 1
    bad = " ".join(fails(capsys))
    for check in ("ledger.integrated", "return.frames", "return.status", "final.frames"):
        assert check in bad, bad


def test_plan_curation_accepts_ledger_000_and_only_the_prompt_of_batch_1(tmp_path, capsys):
    """With plan, the default, there is no NEXT: the INIT writes the ledger with 000 and only the prompt of batch 1."""
    run_dir, wa = make_run(tmp_path, curation="plan", ledger_hdr="Integrado até o lote: 000 de 003")
    (Path(wa["videos"][0]["out_dir"]) / "prompts" / "batch_002.md").unlink()
    assert check_run.main([str(run_dir)]) == 0, fails(capsys)
    assert check_run.main([str(run_dir), "--return", ret()]) == 0, fails(capsys)
    t = table(capsys)
    assert not t["FAIL"] and not t["WARN"], t
    run_dir2, _ = make_run(tmp_path / "b", curation="plan")  # ledger at 001: NEXT before resuming as plan
    assert check_run.main([str(run_dir2)]) == 0, fails(capsys)
    run_dir3, _ = make_run(tmp_path / "c", curation="plan", ledger_hdr="Integrado até o lote: 003 de 003")
    assert check_run.main([str(run_dir3)]) == 1
    bad = fails(capsys)
    assert len(bad) == 1 and "ledger.integrated" in bad[0] and "expected 000–002 de 003" in bad[0], bad


def test_plan_curation_requires_the_prompt_of_batch_1_and_checks_the_others_that_exist(tmp_path, capsys):
    run_dir, wa = make_run(tmp_path, curation="plan", ledger_hdr="Integrado até o lote: 000 de 003")
    for n in (1, 2):
        (Path(wa["videos"][0]["out_dir"]) / "prompts" / f"batch_00{n}.md").unlink()
    assert check_run.main([str(run_dir)]) == 1
    bad = fails(capsys)
    assert len(bad) == 1 and "prompt.exists" in bad[0] and "teste 001" in bad[0], bad
    run_dir2, _ = make_run(tmp_path / "b", curation="plan", ledger_hdr="Integrado até o lote: 000 de 003",
                           foco2="- Ele diz: então quando o pedido entra depois da meia noite ele só aparece depois.")
    assert check_run.main([str(run_dir2)]) == 1
    bad = fails(capsys)
    assert len(bad) == 1 and "prompt.cue_copy" in bad[0] and "teste 002" in bad[0], bad


def test_batches_curation_or_no_key_requires_integrated_ledger_and_all_prompts(tmp_path, capsys):
    """Without options.curation, the run predates the option and had the NEXT: it counts as batches."""
    for cur in ("batches", None):
        run_dir, wa = make_run(tmp_path / str(cur), curation=cur, ledger_hdr="Integrado até o lote: 000 de 003")
        (Path(wa["videos"][0]["out_dir"]) / "prompts" / "batch_002.md").unlink()
        assert check_run.main([str(run_dir)]) == 1
        bad = fails(capsys)
        assert any("ledger.integrated" in ln and "expected 001 de 003" in ln for ln in bad), (cur, bad)
        assert any("prompt.exists" in ln and "teste 002" in ln for ln in bad), (cur, bad)
        assert len(bad) == 2, (cur, bad)


def test_final_report_only_with_the_new_name(tmp_path, capsys):
    """The final report is relatorio.md: the harness blocks subagents from writing report.md."""
    run_dir, wa = make_run(tmp_path)
    out = Path(wa["videos"][0]["out_dir"])
    (out / "relatorio.md").rename(out / "report.md")
    assert check_run.main([str(run_dir)]) == 1
    bad = fails(capsys)
    assert len(bad) == 1 and "final.exists" in bad[0] and "relatorio.md" in bad[0], bad


def test_args_by_flag_and_usage_error(tmp_path, capsys):
    run_dir, wa = make_run(tmp_path, run_json=False)
    assert check_run.main([str(run_dir)]) == 2
    args_file = tmp_path / "workflow_args.json"
    args_file.write_text(json.dumps(wa, ensure_ascii=False), encoding="utf-8")
    assert check_run.main([str(run_dir), "--args", str(args_file)]) == 0, fails(capsys)
    assert check_run.main([str(tmp_path / "nao-existe")]) == 2
