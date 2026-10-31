#!/usr/bin/env python
"""Preparação determinística da skill video-analysis: frames únicos, legenda, lotes e workflow_args.

Pela skill (sempre a ferramenta Bash, sempre ``python``, nunca ``python3``):
    python prepare.py --pedido "C:/Users/digom/video-analysis/pedidos/pedido.md" --estimar
    python prepare.py --pedido "C:/Users/digom/video-analysis/pedidos/pedido.md"
    python prepare.py --resume <run_id|run_dir|ultimo> [--papeis agents|inline] [--max-lotes N] [--curadoria plano|lotes]
Uso manual:
    python prepare.py INPUT [INPUT ...] [--objective-file MD | --objective "texto"] [--run [NOME]] [opções]
    python prepare.py --gc [DIAS]

stdout: exatamente uma linha JSON (a última), também gravada em <run_dir>/prepare.json e em logs/.
stderr: primeiro "run_dir: …", depois o progresso; <run_dir>/status.json é atualizado a cada 5 s.
Saída: 0 ok · 2 entrada · 3 ambiente · 4 um vídeo falhou (os outros vêm no JSON) · 130 interrompido.
Contratos: reference/contratos.md; CLI completa: docs/design-deterministico.md §2.
"""
import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")  # also for spawned worker processes

import argparse  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
# multiprocessing, concurrent.futures and traceback are imported where they are used: a fully
# cached run never needs them (design §9: warm cache <= 2 s per video)

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import va  # noqa: E402
from va import WORKFLOW_DEFAULTS, WORKSPACE_DIRS, ambiente, cache, contracts, discover, runs  # noqa: E402
from va.common import (as_posix, fmt_hms, parse_time, read_json, resolve_unicode_paths, setup_console,  # noqa: E402
                       video_title, workspace_home, write_json, write_text)

SCRIPT_PATH = as_posix(SKILL_DIR / "workflow" / "video-analysis.js")
STATUS_INTERVAL_S = 5.0
LONG_PATH = 240
DEFAULT_LANGUAGE = "pt-BR"

EXIT = {
    "uso_invalido": 2, "pedido_invalido": 2, "objetivo_ausente": 2, "opcao_invalida": 2,
    "video_nao_encontrado": 2, "vtt_ausente": 2, "run_nao_encontrado": 2, "run_inconsistente": 2,
    "run_grande_demais": 2,
    "ffmpeg_ausente": 3, "dependencia_ausente": 3, "workspace_inacessivel": 3, "instalacao_incompleta": 3,
    "video_falhou": 4,
    "interrompido": 130,
    "erro_interno": 1,
}
_REASONS = {"nao_encontrado": "nada com esse caminho", "pasta_sem_videos": "pasta sem vídeos (a busca não entra em subpastas)",
            "glob_sem_videos": "o curinga não casou com nenhum vídeo"}


class Fail(Exception):
    """Stops prepare.py with ``{"ok": false, "error": {code, detail}}`` and the code's exit status."""

    def __init__(self, code: str, detail: str, **extra):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.extra = code, detail, extra


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Fail("uso_invalido", f"{message} (veja python prepare.py --help)")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="prepare.py", description=__doc__.splitlines()[0],
                formatter_class=argparse.RawDescriptionHelpFormatter, epilog="\n".join(__doc__.splitlines()[1:]))
    p.add_argument("inputs", nargs="*", metavar="INPUT", help="vídeo, pasta ou curinga (* e ?) — uso manual")
    m = p.add_argument_group("modo")
    m.add_argument("--pedido", metavar="MD", help="pedido.md com # videos, # objetivo e # opcoes (entrada da skill)")
    m.add_argument("--estimar", "--estimate", dest="estimate", action="store_true",
                   help="só ffprobe e cache: estima o tempo; não consome o pedido nem cria run")
    m.add_argument("--resume", "--retomar", dest="resume", metavar="RUN", help="run_id, pasta do run ou ultimo")
    m.add_argument("--gc", nargs="?", const=30.0, type=float, metavar="DIAS",
                   help="apaga do cache os vídeos sem uso há mais de DIAS (padrão 30)")
    g = p.add_argument_group("geral")
    g.add_argument("--home", metavar="PASTA", help="pasta central (padrão: VIDEO_ANALYSIS_HOME ou %%USERPROFILE%%\\video-analysis)")
    g.add_argument("--jobs", type=int, default=2, help="vídeos processados em paralelo, em processos separados (padrão 2)")
    g.add_argument("--force", nargs="?", const="all", choices=["all", "cues", "frames", "batches"],
                   help="refaz o estágio e os seguintes (sem valor: all)")
    g.add_argument("--forcar", action="store_true",
                   help="prepara mesmo acima de 800 agentes estimados (diagnóstico: o Workflow recusa; divida o run)")
    g.add_argument("--vtt", metavar="ARQ", help="legenda explícita (só com exatamente 1 vídeo)")
    g.add_argument("--skip-missing", action="store_true", help="pula os vídeos sem .vtt de mesmo nome")
    g.add_argument("--recursive", action="store_true", help="busca vídeos também nas subpastas")
    g.add_argument("--run", nargs="?", const="", metavar="NOME", help="cria runs/<carimbo>-<NOME> (uso manual)")
    o = g.add_mutually_exclusive_group()
    o.add_argument("--objective-file", "--objetivo-arquivo", dest="objective_file", metavar="MD")
    o.add_argument("--objective", "--objetivo", dest="objective", metavar="TEXTO")
    g.add_argument("--json-out", metavar="ARQ", help="grava também o JSON neste arquivo")
    g.add_argument("--ascii", action="store_true", help="JSON só com ASCII (\\uXXXX)")
    g.add_argument("--quiet", action="store_true", help="sem progresso no stderr (a linha run_dir continua)")
    g.add_argument("--debug", action="store_true", help="traceback completo no stderr")
    a = p.add_argument_group("opções do pedido (a da linha de comando prevalece)")
    a.add_argument("--papeis", metavar="agents|inline")
    a.add_argument("--max-lotes", dest="max_lotes", metavar="N")
    a.add_argument("--paralelo", metavar="N")
    a.add_argument("--esforco-analista", dest="esforco_analista", metavar="NIVEL")
    a.add_argument("--esforco-curador", dest="esforco_curador", metavar="NIVEL")
    a.add_argument("--curadoria", metavar="plano|lotes",
                   help="lotes = curador antes de cada lote (padrão: plano; no --resume, a do run, ou lotes num run anterior à opção)")
    a.add_argument("--idioma", metavar="xx")
    a.add_argument("--trecho", metavar="hh:mm:ss-hh:mm:ss")
    a.add_argument("--falantes", "--speaker-names", dest="falantes", metavar='"SPEAKER_01=Nome;…"')
    a.add_argument("--sensibilidade", "--sensitivity", dest="sensibilidade", metavar="low|normal|high")
    a.add_argument("--ignorar-regiao", "--ignore-region", dest="ignorar_regiao", action="append", metavar="x,y,w,h")
    d = p.add_argument_group("extração e lotes (uso manual; design §2)")
    d.add_argument("--start", metavar="T", help="início do intervalo extraído ([HH:]MM:SS[.mmm] ou segundos)")
    d.add_argument("--end", metavar="T", help="fim do intervalo extraído")
    d.add_argument("--vtt-offset", type=float, metavar="SEG")
    d.add_argument("--sample-fps", type=float)
    d.add_argument("--settle", type=float)
    d.add_argument("--max-settle-wait", type=float)
    d.add_argument("--motion-interval", type=float)
    d.add_argument("--motion-max-interval", type=float)
    d.add_argument("--cursor-size", type=float)
    d.add_argument("--hot-mode", choices=["windowed", "off"])
    d.add_argument("--no-revisit", action="store_true")
    d.add_argument("--max-edge", type=int)
    d.add_argument("--jpeg-q", type=int)
    d.add_argument("--sequential", action="store_true", help="passes A e B separados (mesma saída; diagnóstico)")
    d.add_argument("--batch-frames", type=int)
    d.add_argument("--batch-text", type=int)
    d.add_argument("--batch-span", type=float)
    d.add_argument("--no-soft-cuts", action="store_true")
    return p


# --- context, logging, output ----------------------------------------------------------------------

class Ctx:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.home: Path | None = None
        self.mode = "prepare"
        self.run_id: str | None = None
        self.run_dir: Path | None = None
        self.log_path: Path | None = None
        self.json_log: Path | None = None
        self.json_out: str | None = None
        self.ascii = False
        self.quiet = False
        self.debug = False
        self.stderr_on = False
        self.pedido: Path | None = None
        self.consume = False
        self.partial_run = False
        self.status: "Status | None" = None
        self.warnings: list[str] = []

    def log(self, msg: str, echo: bool = True) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        if self.log_path is not None:
            with contextlib.suppress(OSError):
                with open(self.log_path, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
        if echo and self.stderr_on and not self.quiet:
            print(line, file=sys.stderr, flush=True)

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0


def envelope(ctx: Ctx, ok: bool, error: dict | None = None, **fields) -> dict:
    env: dict = {"ok": ok}
    if error is not None:
        env["error"] = error
    env.update(mode=ctx.mode, version=va.VERSION, home=as_posix(ctx.home) if ctx.home else None,
               run_id=ctx.run_id, run_dir=as_posix(ctx.run_dir) if ctx.run_dir else None, json_path=None)
    defaults = {"videos": [], "estimate": None, "env": None, "script_path": SCRIPT_PATH, "workflow_args": None,
                "errors": [], "warnings": list(ctx.warnings)}
    for key, default in defaults.items():
        env[key] = fields.pop(key, default)
    env.update(fields)
    return env


def emit(ctx: Ctx, env: dict, code: int) -> int:
    env = cache.plain(env)
    targets = []
    if ctx.run_dir is not None and ctx.run_dir.is_dir():
        targets.append(ctx.run_dir / "prepare.json")
    if ctx.json_log is not None:
        targets.append(ctx.json_log)
    if ctx.json_out:
        targets.append(Path(ctx.json_out))
    env["json_path"] = as_posix(targets[0]) if targets else None
    for t in targets:
        try:
            write_json(t, env)
        except (OSError, ValueError) as exc:
            ctx.log(f"não consegui gravar {as_posix(t)}: {exc}", echo=False)
    line = json.dumps(env, ensure_ascii=ctx.ascii, separators=(",", ":"), allow_nan=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return code


def ensure_home(ctx: Ctx) -> None:
    st = ambiente.workspace_status(ctx.home, create=True)
    if not st["ok"]:
        raise Fail("workspace_inacessivel", st["detail"])


def open_log(ctx: Ctx, stem: str) -> None:
    logs = ctx.home / WORKSPACE_DIRS["logs"]
    ctx.log_path = logs / f"{stem}.log"
    ctx.json_log = logs / f"{stem}.json"


def rename_log(ctx: Ctx, stem: str) -> None:
    old_log = ctx.log_path
    open_log(ctx, stem)
    if old_log is not None and old_log != ctx.log_path and old_log.exists():
        with contextlib.suppress(OSError):
            os.replace(old_log, ctx.log_path)


# --- status.json ------------------------------------------------------------------------------------

class Status:
    """<run_dir>/status.json, refreshed every STATUS_INTERVAL_S from the workers' progress files."""

    def __init__(self, ctx: Ctx, videos: list[dict]):
        self.path = ctx.run_dir / "status.json"
        self.progress_dir = ctx.home / WORKSPACE_DIRS["tmp"] / f"progress-{ctx.run_id}"
        self.progress_dir.mkdir(parents=True, exist_ok=True)
        self.t0 = time.monotonic()
        self.doc = {"run_id": ctx.run_id, "mode": ctx.mode, "state": "preparando", "started": cache.iso_now(),
                    "updated": None, "elapsed_s": 0,
                    "videos": [{"vid": v["vid"], "slug": v["slug"], "title": v["title"], "stage": None,
                                "state": "na fila", "info": None} for v in videos]}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def progress_path(self, vid: str) -> str:
        return str(self.progress_dir / f"{vid}.json")

    def write(self) -> None:
        with self._lock:
            for v in self.doc["videos"]:
                with contextlib.suppress(OSError, ValueError):
                    p = read_json(self.progress_path(v["vid"]))
                    v.update(stage=p.get("stage"), state=p.get("state"), info=p.get("info"))
            self.doc["updated"] = cache.iso_now()
            self.doc["elapsed_s"] = round(time.monotonic() - self.t0, 1)
            with contextlib.suppress(OSError, ValueError):
                write_json(self.path, self.doc)

    def _loop(self) -> None:
        while not self._stop.wait(STATUS_INTERVAL_S):
            self.write()

    def start(self) -> None:
        self.write()
        self._thread = threading.Thread(target=self._loop, name="va-status", daemon=True)
        self._thread.start()

    def finish(self, state: str) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self.write()
        with self._lock:
            self.doc["state"] = state
            with contextlib.suppress(OSError, ValueError):
                write_json(self.path, self.doc)
        cache.rmtree_robust(self.progress_dir)


# --- inputs and options -------------------------------------------------------------------------------

def gather(ctx: Ctx, args) -> tuple[list[str], str | None, dict]:
    if args.pedido:
        ctx.pedido = Path(os.path.abspath(args.pedido))
        ctx.consume = not args.estimate  # any later failure renames it to pedido.erro.md
        if args.inputs:
            raise Fail("uso_invalido", "use --pedido ou entradas diretas (INPUT…), não os dois")
        if args.objective or args.objective_file:
            raise Fail("uso_invalido", "com --pedido, o objetivo vem da seção '# objetivo'")
        ped = runs.read_pedido(ctx.pedido)
        entries, objective, raw = ped["videos"], ped["objective"], dict(ped["options"])
    else:
        if not args.inputs:
            raise Fail("uso_invalido", "informe --pedido <md>, vídeos (INPUT…), --resume <run> ou --gc")
        entries, raw = list(args.inputs), {}
        objective = None
        if args.objective_file:
            hits = [p for p in resolve_unicode_paths(args.objective_file) if p.is_file()]
            if len(hits) != 1:
                raise Fail("objetivo_ausente", f"arquivo do objetivo não encontrado: {args.objective_file}")
            data = hits[0].read_bytes()
            try:
                objective = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                objective = data.decode("cp1252", errors="replace")
        elif args.objective is not None:
            objective = args.objective
        if objective is not None and not objective.strip():
            raise Fail("objetivo_ausente", "o objetivo está vazio")
    for key in runs.OPTION_KEYS:
        value = getattr(args, key, None)
        if value is not None:
            raw[key] = value if key != "ignorar_regiao" else list(value)
    return entries, objective, raw


def _num(args, name: str, lo: float, hi: float, label: str) -> float | None:
    value = getattr(args, name)
    if value is None:
        return None
    if not (lo <= value <= hi):
        raise Fail("opcao_invalida", f"{label}: {value} fora de {lo}–{hi}")
    return value


def stage_overrides(args, opts: dict) -> dict:
    """User overrides per stage: pedido options plus the manual flags of design §2."""
    fr: dict = {}
    cu: dict = {}
    ba: dict = {}
    if opts.get("sensibilidade"):
        fr["sensitivity"] = opts["sensibilidade"]
    if opts.get("ignorar_regiao"):
        fr["ignore_regions"] = opts["ignorar_regiao"]
    for name, key, lo, hi in (("sample_fps", "sample_fps", 0.1, 30.0), ("settle", "settle_s", 0.0, 30.0),
                              ("max_settle_wait", "max_settle_wait_s", 0.0, 120.0),
                              ("motion_interval", "motion_interval_s", 0.5, 600.0),
                              ("motion_max_interval", "motion_max_interval_s", 0.5, 3600.0)):
        v = _num(args, name, lo, hi, "--" + name.replace("_", "-"))
        if v is not None:
            fr[key] = v
    cs = _num(args, "cursor_size", 0.005, 0.25, "--cursor-size")
    if cs is not None:
        fr["cursor_size"] = cs  # the frames module derives cursor_h_frac/cursor_w_frac (width = 2/3)
    if args.hot_mode:
        fr["hot_mode"] = args.hot_mode
    if args.no_revisit:
        fr["revisit"] = False
    me = _num(args, "max_edge", 64, va.OUTPUT_DEFAULTS["max_edge_cap"], "--max-edge")
    if me is not None:
        fr["max_edge"] = int(me)
    jq = _num(args, "jpeg_q", va.OUTPUT_DEFAULTS["jpeg_q_min"], va.OUTPUT_DEFAULTS["jpeg_q_max"], "--jpeg-q")
    if jq is not None:
        fr["jpeg_q"] = int(jq)
    if args.sequential:
        fr["sequential"] = True  # execution only: same output, not part of the frames key
    if args.start is not None or args.end is not None:
        try:
            start = parse_time(args.start) if args.start is not None else 0.0
            end = parse_time(args.end) if args.end is not None else None
        except ValueError as exc:
            raise Fail("opcao_invalida", f"--start/--end: {exc}") from None
        if start < 0 or (end is not None and end <= start):
            raise Fail("opcao_invalida", "--end precisa ser depois de --start")
        fr["range"] = [start, end]
    if opts.get("falantes"):
        cu["speaker_names"] = opts["falantes"]
    if args.vtt_offset is not None:
        cu["offset_s"] = float(args.vtt_offset)
    bf = _num(args, "batch_frames", 1, 99, "--batch-frames")
    if bf is not None:
        ba["max_frames"] = int(bf)  # batching derives soft_min and the tail limits from it
    bt = _num(args, "batch_text", 500, 200000, "--batch-text")
    if bt is not None:
        ba["max_text_chars"] = int(bt)
    bs = _num(args, "batch_span", 10.0, 86400.0, "--batch-span")
    if bs is not None:
        ba["max_span_s"] = float(bs)
    if args.no_soft_cuts:
        ba["soft_cuts"] = False
    return {"frames": fr, "cues": cu, "batches": ba}


def effective_params(overrides: dict, trecho: list | None) -> dict:
    try:
        fr = cache.stage_fn("effective_frame_params")(dict(overrides["frames"]))
        cu = cache.stage_fn("effective_cue_params")(dict(overrides["cues"]))
        bo = dict(overrides["batches"])
        if trecho:
            bo["range"] = list(trecho)
        ba = cache.stage_fn("effective_batch_params")(bo)
        if trecho and isinstance(ba, dict) and "range" not in ba:
            ba = {**ba, "range": list(trecho)}
    except cache.StageUnavailable as exc:
        raise Fail("instalacao_incompleta", f"{exc}: reinstale a skill (scripts/va incompleto)") from None
    except (ValueError, TypeError, KeyError) as exc:
        raise Fail("opcao_invalida", f"parâmetro inválido: {exc}") from None
    return {"frames": cache.plain(fr), "cues": cache.plain(cu), "batches": cache.plain(ba)}


def discover_pairs(ctx: Ctx, args, entries: list[str]) -> list[tuple[Path, Path]]:
    videos, problems = discover.expand_inputs(entries, recursive=args.recursive)
    if problems:
        detail = "; ".join(f"{p['input']} ({_REASONS.get(p['reason'], p['reason'])})" for p in problems)
        raise Fail("video_nao_encontrado", f"não achei: {detail}. Confira o caminho (entre aspas, se tiver espaços); "
                   "acentos, ’, espaços duplos e colchetes já são tratados", inputs=[p["input"] for p in problems])
    if not videos:
        raise Fail("video_nao_encontrado", "nenhum vídeo nas entradas")
    explicit = None
    if args.vtt:
        if len(videos) != 1:
            raise Fail("uso_invalido", f"--vtt só vale com exatamente 1 vídeo (as entradas dão {len(videos)})")
        hits = [p for p in resolve_unicode_paths(args.vtt) if p.is_file()]
        if len(hits) != 1:
            raise Fail("vtt_ausente", f"--vtt aponta para um arquivo que não existe: {args.vtt}")
        explicit = hits[0]
    pairs, skipped, missing = discover.match_vtts(videos, explicit, skip_missing=args.skip_missing)
    if missing:
        raise Fail("vtt_ausente", discover.missing_vtt_detail(missing, allow_vtt_flag=len(videos) == 1),
                   missing_vtt=[{"video": as_posix(m["video"]), "candidates": [as_posix(c["path"]) for c in m["candidates"]],
                                 "suggestion": m["suggestion"]} for m in missing])
    if skipped:
        ctx.warnings.append("sem legenda de mesmo nome, pulado(s) por --skip-missing: " + "; ".join(p.name for p in skipped))
    if not pairs:
        raise Fail("vtt_ausente", "nenhum vídeo com legenda .vtt de mesmo nome (todos pulados por --skip-missing): "
                   + "; ".join(p.name for p in skipped))
    for video, _ in pairs:
        if len(str(video)) > LONG_PATH:
            ctx.warnings.append(f"caminho com mais de {LONG_PATH} caracteres (limite do Windows por perto): {video.name}")
    return pairs


def runtime_check(ctx: Ctx, need: tuple[str, ...]) -> str | None:
    """Fail with exit 3 when something in ``need`` is missing; returns the ffmpeg version text when
    ffmpeg is in ``need`` (recorded in the frames stage.json)."""
    if not need:
        return None
    problems = ambiente.runtime_problems(need)
    if problems:
        code = "ffmpeg_ausente" if any(p["code"] == "ffmpeg_ausente" for p in problems) else "dependencia_ausente"
        raise Fail(code, "; ".join(p["detail"] for p in problems) + ": rode /video-analysis setup")
    if "ffmpeg" in need:
        with contextlib.suppress(Exception):
            return ambiente.tool_status("ffmpeg").get("version")
    return None


def estimate_seconds(info, hits: dict, frame_params: dict | None = None) -> float:
    """Rough wall time of the missing stages. Frames: the frames stage's own estimate when it
    can give one, else decode-bound rates (~0.06x realtime for Teams 1080p16, ~0.15x for 1080p60;
    design §0 and §9)."""
    dur = float(cache.info_get(info, "duration_s", "duration", default=0.0) or 0.0)
    s = 0.5
    if not hits.get("cues"):
        s += 0.5
    if not hits.get("frames"):
        frames_s = None
        with contextlib.suppress(Exception):
            frames_s = float(cache.stage_fn("estimate_frames_seconds")(info, frame_params))
        if frames_s is None or not frames_s >= 0:
            w = cache.info_get(info, "width", default=1920) or 1920
            h = cache.info_get(info, "height", default=1080) or 1080
            fps = cache.info_get(info, "fps_avg", "fps", default=30.0) or 30.0
            mpx = float(w) * float(h) * float(fps) / 1e6
            frames_s = min(0.30, max(0.04, 0.03 + 0.001 * mpx)) * dur + 2.0
        s += frames_s
    if not hits.get("batches"):
        s += 1.0 + 0.004 * dur
    return s


def _duration(info) -> float:
    d = cache.info_get(info, "duration_s", "duration")
    try:
        d = float(d)
    except (TypeError, ValueError):
        d = 0.0
    if not d > 0:
        raise ValueError("ffprobe não informou a duração (arquivo sem faixa de vídeo?)")
    return d


def env_status(ctx: Ctx) -> tuple[dict, dict]:
    """(``env`` of the envelope, agents_status). The Claude home is the one verificar_ambiente.py checks
    by default (env VA_CLAUDE_HOME, else %USERPROFILE%\\.claude). ``workflow_current``: the saved
    workflow (<claude home>/workflows/video-analysis.js) is byte-identical to the skill's script, so
    the Workflow can run it by name; otherwise the skill passes the script inline."""
    chome = ambiente.claude_home()
    agents = ambiente.agents_status(SKILL_DIR, chome)
    env = {"permissions_ok": bool(ambiente.permissions_status(ctx.home, chome)["ok"]),
           "agents_current": bool(agents["current"]),
           "workflow_current": bool(ambiente.workflow_status(SKILL_DIR, chome)["current"])}
    return env, agents


def roles_and_env(ctx: Ctx, requested: str) -> tuple[str, dict]:
    roles = runs.copy_roles(SKILL_DIR, ctx.home / WORKSPACE_DIRS["roles"])
    if not roles:
        ctx.warnings.append("a skill não tem papéis em agents/: o modo inline não vai achar o papel")
    env, agents = env_status(ctx)
    mode = requested
    if requested == "agents" and not agents["current"]:
        mode = "inline"
        ctx.warnings.append("papeis: agents pedido, mas as cópias em ~/.claude/agents não são idênticas às da skill "
                            f"({agents['detail']}); usando inline. Rode /video-analysis setup e reabra o Claude Code")
    return mode, env


def guard(ctx: Ctx, agents: int, forcar: bool, **extra) -> None:
    limit = WORKFLOW_DEFAULTS["max_agents"]
    if agents > limit and not forcar:
        raise Fail("run_grande_demais", f"~{agents} agentes estimados (limite {limit}; o Workflow recusa acima disso): "
                   "divida em runs menores, use trecho ou max_lotes", **extra)
    if agents > limit:
        ctx.warnings.append(f"~{agents} agentes (--forcar): o Workflow recusa mais de {limit} agentes; divida o run "
                            "(--trecho/--max-lotes ou menos vídeos) antes de disparar")


def video_entry(vid: str, slug: str, title: str, res: dict, extra_warnings: list[str]) -> dict:
    summary = {k: v for k, v in (res.get("summary") or {}).items() if k != "schema"}
    entry = {"vid": vid, "slug": slug, **summary}
    entry.setdefault("title", title)
    entry["warnings"] = list(summary.get("warnings") or []) + list(res.get("warnings") or []) + extra_warnings
    entry["cache_hit"] = res.get("cache_hit") or {}
    entry["timing_s"] = res.get("timing_s") or {}
    return entry


def span(summary: dict, duration: float, trecho: list | None) -> float:
    rng = summary.get("range") if isinstance(summary, dict) else None
    if isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, (int, float)) for x in rng) and rng[1] > rng[0]:
        t0, t1 = float(rng[0]), float(rng[1])
    else:
        t0, t1 = 0.0, duration
    if trecho:
        t0, t1 = max(t0, trecho[0]), min(t1, trecho[1])
    return max(0.0, t1 - t0)


def pool_plan(jobs: list[dict], n_jobs: int) -> list[int]:
    """Indexes of the jobs that go to the ProcessPool, in submission order. Fully cached jobs
    (``warm``) stay in-process: they only read files, and a spawned worker costs more than the
    whole job. The others go to the pool when there are 2+ of them and --jobs > 1, longest first
    (``cost`` = estimated seconds), so a long video does not start last while the other worker
    idles (longest-processing-time order). Empty: everything runs in-process (also with the
    in-process test overrides)."""
    cold = [i for i, job in enumerate(jobs) if not job.get("warm")]
    if n_jobs <= 1 or len(cold) <= 1 or cache.STAGE_OVERRIDES:
        return []
    return sorted(cold, key=lambda i: (-float(jobs[i].get("cost") or 0.0), i))


def run_jobs(ctx: Ctx, jobs: list[dict], n_jobs: int) -> list[dict]:
    """Run ``cache.prepare_video`` for each job, in-process or in a spawn ProcessPool (see
    ``pool_plan``); the results come back in input order."""
    results: list[dict | None] = [None] * len(jobs)
    pooled = pool_plan(jobs, n_jobs)
    for i, job in enumerate(jobs):
        if i not in pooled:
            ctx.log(f"[{i + 1}/{len(jobs)}] {job['slug']}")
            results[i] = cache.prepare_video(job)
    if not pooled:
        return results  # type: ignore[return-value]
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    workers = min(n_jobs, len(pooled))
    ctx.log(f"{len(pooled)} vídeos, {workers} em paralelo")
    ex = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                             initializer=cache.child_init)
    try:
        futures = {ex.submit(cache.prepare_video, jobs[i]): i for i in pooled}
        for fut in as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # BrokenProcessPool, pickling problems
                results[i] = {"vid": jobs[i]["vid"], "ok": False, "cache_hit": {}, "timing_s": {}, "warnings": [],
                              "error": {"code": "video_falhou", "detail": f"o processo do vídeo morreu: {type(exc).__name__}: {exc}"}}
    except KeyboardInterrupt:
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown(wait=True)
    return [r if r is not None else {"vid": jobs[i]["vid"], "ok": False, "cache_hit": {}, "timing_s": {}, "warnings": [],
                                     "error": {"code": "video_falhou", "detail": "o processo do vídeo não devolveu resultado"}}
            for i, r in enumerate(results)]


def option_lines(opts: dict, wopts: dict, roles_mode: str, language: str) -> list[str]:
    lines = [f"papeis: {roles_mode}",
             f"max_lotes: {wopts['max_batches'] if wopts['max_batches'] else 'todos'}",
             f"paralelo: {wopts['max_parallel_videos']}",
             f"esforco_analista: {wopts['analyst_effort']}",
             f"esforco_curador: {wopts['curator_effort']}",
             f"curadoria: {'lotes' if wopts['curation'] == 'batches' else 'plano'}",
             f"idioma: {language}"]
    if opts.get("trecho"):
        lines.append(f"trecho: {fmt_hms(opts['trecho'][0], 0)}-{fmt_hms(opts['trecho'][1], 0)}")
    if opts.get("falantes"):
        lines.append("falantes: " + "; ".join(f"{k}={v}" for k, v in opts["falantes"].items()))
    if opts.get("sensibilidade"):
        lines.append(f"sensibilidade: {opts['sensibilidade']}")
    if opts.get("ignorar_regiao"):
        lines.append("ignorar_regiao: " + "; ".join(",".join(f"{x:g}" for x in r) for r in opts["ignorar_regiao"]))
    return lines


def check_args(wargs: dict) -> None:
    problems = contracts.check_workflow_args(wargs)
    if problems:
        raise Fail("erro_interno", "workflow_args inválido gerado pelo prepare.py: " + "; ".join(problems[:5]))


# --- modes ----------------------------------------------------------------------------------------------

def run_gc(ctx: Ctx, args) -> int:
    ctx.mode = "gc"
    if args.inputs or args.pedido or args.resume or args.estimate:
        raise Fail("uso_invalido", "--gc roda sozinho")
    if args.gc < 0:
        raise Fail("opcao_invalida", "--gc: DIAS precisa ser 0 ou mais")
    ensure_home(ctx)
    open_log(ctx, f"gc-{time.strftime('%Y%m%d-%H%M%S')}")
    ctx.stderr_on = True
    res = cache.gc_cache(ctx.home / WORKSPACE_DIRS["cache"], args.gc)
    ctx.log(f"gc: {len(res['removed'])} vídeo(s) apagado(s) do cache, {res['freed_mb']} MB liberados; "
            f"{res['kept']} mantido(s)")
    errors = [{"key": f["key"], "code": "gc_falhou", "detail": "não consegui apagar (arquivo em uso?)"} for f in res["failed"]]
    return emit(ctx, envelope(ctx, True, errors=errors, gc=res), 0)


def run_prepare(ctx: Ctx, args) -> int:
    estimate = bool(args.estimate)
    ctx.mode = "estimate" if estimate else "prepare"
    entries, objective, raw = gather(ctx, args)
    try:
        opts = runs.normalize_options(raw)
    except runs.RunError as exc:
        raise Fail(exc.code, exc.detail) from None
    make_run = not estimate and (ctx.pedido is not None or objective is not None or args.run is not None)
    if args.run is not None and objective is None and not estimate:
        raise Fail("objetivo_ausente", "--run precisa de --objective-file ou --objective")
    ensure_home(ctx)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    open_log(ctx, f"{ctx.mode}-{stamp}")
    ctx.log(f"prepare.py {va.VERSION} · modo {ctx.mode} · home {as_posix(ctx.home)} · argv {sys.argv[1:]}", echo=False)

    pairs = discover_pairs(ctx, args, entries)
    overrides = stage_overrides(args, opts)
    params = effective_params(overrides, opts.get("trecho"))
    force = cache.expand_force(args.force)
    wopts = runs.workflow_options(opts)
    language = opts.get("idioma") or DEFAULT_LANGUAGE
    trecho = opts.get("trecho")

    # keys and cache state first: stat calls and small JSON reads, no subprocess and no frame
    # engine import, so a fully cached run needs neither ffprobe nor ffmpeg (design §9: warm
    # cache <= 2 s per video)
    errors: list[dict] = []
    known = []
    slugs = runs.unique_slugs([v.stem for v, _ in pairs])
    for i, ((video, vtt), slug) in enumerate(zip(pairs, slugs)):
        vid, title = f"v{i + 1}", video_title(video)
        meta = {"title": title, "video_path": as_posix(video), "vtt_path": as_posix(vtt)}
        try:
            vkey = cache.video_key(video)
            keys = cache.stage_keys(vkey, vtt, params, meta)
        except OSError as exc:
            errors.append({"vid": vid, "slug": slug, "video": as_posix(video), "code": "video_falhou",
                           "detail": f"leitura: {type(exc).__name__}: {exc}"})
            ctx.log(f"{slug}: não consegui ler o vídeo ou a legenda: {exc}", echo=False)
            continue
        vdir = ctx.home / WORKSPACE_DIRS["cache"] / vkey
        hits = cache.stage_hits(vdir, keys, force)
        info = cache.cached_probe(vdir, video) if hits["cues"] and hits["frames"] else None
        known.append({"vid": vid, "slug": slug, "title": title, "video": video, "vtt": vtt, "video_key": vkey,
                      "keys": keys, "meta": meta, "hits": hits, "info": info, "info_from_cache": info is not None})
    need = ["numpy", "pillow"]  # find_spec only: cheap
    if any(k["info"] is None for k in known):
        need.insert(0, "ffprobe")
    if any(not k["hits"]["frames"] for k in known):
        need.insert(0, "ffmpeg")  # --estimar checks it too: the prepare that follows will need it
    ffmpeg_version = runtime_check(ctx, tuple(need))

    # ffprobe for what the cache does not answer (fast; before the run folder exists)
    items = []
    for k in known:
        try:
            info = k["info"] if k["info"] is not None else cache.stage_fn("probe_video")(k["video"])
            duration = _duration(info)
        except cache.StageUnavailable as exc:
            raise Fail("instalacao_incompleta", str(exc)) from None
        except Exception as exc:
            errors.append({"vid": k["vid"], "slug": k["slug"], "video": as_posix(k["video"]), "code": "video_falhou",
                           "detail": f"ffprobe: {type(exc).__name__}: {exc}"})
            ctx.log(f"{k['slug']}: ffprobe falhou: {exc}", echo=False)
            continue
        if trecho and trecho[0] >= duration:
            raise Fail("opcao_invalida", f"trecho começa em {fmt_hms(trecho[0], 0)}, depois do fim de {k['title']} "
                       f"({fmt_hms(duration, 0)})")
        warn = []
        if trecho and trecho[1] > duration:
            warn.append(f"trecho termina depois do fim do vídeo ({fmt_hms(duration, 0)}): vai até o fim")
        items.append({**k, "info": info, "duration": duration, "warn": warn})
    if not items:
        raise Fail("video_falhou", "nenhum vídeo pôde ser lido: " + "; ".join(f"{e['slug']}: {e['detail']}" for e in errors),
                   errors=errors)

    if estimate:
        return finish_estimate(ctx, args, items, errors, wopts, params)

    if make_run:
        ctx.run_id, ctx.run_dir = runs.new_run(ctx.home / WORKSPACE_DIRS["runs"], args.run or (pairs[0][0].stem))
        rename_log(ctx, f"prepare-{ctx.run_id}")
        print(f"run_dir: {as_posix(ctx.run_dir)}", file=sys.stderr, flush=True)
        ctx.status = Status(ctx, items)
        ctx.status.start()
    ctx.stderr_on = True
    ctx.log(f"{len(items)} vídeo(s); log em {as_posix(ctx.log_path)}")

    jobs = []
    for it in items:
        warm = all(it["hits"].values())
        cost = 0.0 if warm or len(items) < 2 else round(estimate_seconds(it["info"], it["hits"], params["frames"]), 1)
        jobs.append({"vid": it["vid"], "slug": it["slug"], "title": it["title"], "video_path": str(it["video"]),
                     "vtt_path": str(it["vtt"]), "video_key": it["video_key"],
                     "video_dir": str(ctx.home / WORKSPACE_DIRS["cache"] / it["video_key"]),
                     "keys": it["keys"], "params": params, "info": it["info"], "info_from_cache": it["info_from_cache"],
                     "force": sorted(force), "meta": it["meta"], "warm": warm, "cost": cost,
                     "ffmpeg": ffmpeg_version, "log_path": str(ctx.log_path), "quiet": ctx.quiet,
                     "progress_path": ctx.status.progress_path(it["vid"]) if ctx.status else None})
    jobs_n = args.jobs if args.jobs and args.jobs > 0 else 1
    results = run_jobs(ctx, jobs, jobs_n) if jobs else []

    done = []
    for it, res in zip(items, results):
        if res.get("ok"):
            done.append((it, res))
            continue
        err = res.get("error") or {"code": "video_falhou", "detail": "falhou sem detalhe"}
        if err.get("code") == "instalacao_incompleta":
            raise Fail("instalacao_incompleta", err["detail"])
        errors.append({"vid": it["vid"], "slug": it["slug"], "video": as_posix(it["video"]), "code": "video_falhou",
                       "detail": err["detail"]})
    if not done:
        raise Fail("video_falhou", "nenhum vídeo foi preparado: " + "; ".join(f"{e['slug']}: {e['detail']}" for e in errors),
                   errors=errors)

    videos_out, states, wvideos = [], [], []
    for it, res in done:
        b = res.get("batches") or {}
        images = list(b.get("batch_images") or [])
        n_frames = int(b.get("n_frames") or sum(images))
        remaining = min(len(images), wopts["max_batches"]) if wopts["max_batches"] else len(images)
        state = {"start_batch": 1, "ledger_exists": False, "start_prompt_exists": False, "final_exists": False,
                 "remaining": remaining}
        states.append(state)
        videos_out.append(video_entry(it["vid"], it["slug"], it["title"], res, it["warn"]))
        wvideos.append({"slug": it["slug"], "title": it["title"], "n_frames": n_frames, "batch_images": images,
                        "duration_s": span(res.get("summary") or {}, it["duration"], trecho),
                        "batches_dir": res["dirs"]["batches"],
                        # v1, v2...: a pasta do vídeo não repete o slug do run (o analista juntava os dois nomes)
                        "out_dir": str(ctx.run_dir / it["vid"]) if ctx.run_dir else None, **state, "_res": res, "_it": it})
    n_batches = sum(s["remaining"] for s in states)
    agents = runs.estimate_agents(states, wopts["curation"])
    estimate = {"batches": n_batches, "agents": agents, "seconds": round(ctx.elapsed())}

    if not make_run:  # cache only (manual use): no run, no workflow_args
        ctx.log(f"pronto em {ctx.elapsed():.1f} s (sem run: use --run/--objective para criar um)")
        env = envelope(ctx, not errors, error=_partial_error(errors, len(items)) if errors else None,
                       videos=videos_out, estimate=estimate, errors=errors)
        return emit(ctx, env, 0 if not errors else EXIT["video_falhou"])

    requested = opts.get("papeis") or WORKFLOW_DEFAULTS["roles_mode"]
    roles_mode, env_info = roles_and_env(ctx, requested)
    guard(ctx, agents, args.forcar, videos=videos_out, estimate=estimate, env=env_info, errors=errors)
    for v in wvideos:
        runs.scaffold_video(v["out_dir"])
    wargs = runs.build_workflow_args(run_id=ctx.run_id, run_dir=ctx.run_dir, roles_dir=ctx.home / WORKSPACE_DIRS["roles"],
                                     roles_mode=roles_mode, language=language, options=wopts, combined_exists=False,
                                     videos=wvideos)
    check_args(wargs)
    vlines = []
    for v in wvideos:
        it = v["_it"]
        line = (f"{v['slug']}: {it['title']} · {fmt_hms(v['duration_s'], 0)} · {len(v['batch_images'])} lotes · "
                f"vídeo {as_posix(it['video'])} · legenda {as_posix(it['vtt'])}")
        if trecho:
            line += f" · trecho {fmt_hms(trecho[0], 0)}-{fmt_hms(min(trecho[1], it['duration']), 0)}"
        vlines.append(line)
    write_text(ctx.run_dir / "objective.md", runs.objective_markdown(objective, option_lines(opts, wopts, roles_mode, language), vlines))
    run_doc = {
        "schema": "va.run/1", "version": va.VERSION, "run_id": ctx.run_id, "run_dir": as_posix(ctx.run_dir),
        "created": cache.iso_now(), "argv": sys.argv[1:], "home": as_posix(ctx.home),
        "pedido": as_posix(ctx.run_dir / "pedido.md") if ctx.pedido else None,
        "objective_md": as_posix(ctx.run_dir / "objective.md"), "objective": objective, "language": language,
        "options": {**wopts, "roles_mode_requested": requested, "roles_mode": roles_mode, "trecho": trecho},
        "overrides": overrides, "params": params,
        "versions": {"algo": va.ALGO_VERSION, "vtt": va.VTT_VERSION, "render": va.RENDER_VERSION},
        "videos": [{"vid": v["_it"]["vid"], "slug": v["slug"], "title": v["title"],
                    "video_path": as_posix(v["_it"]["video"]), "vtt_path": as_posix(v["_it"]["vtt"]),
                    "video_key": v["_it"]["video_key"], "out_dir": as_posix(v["out_dir"]),
                    "keys": v["_it"]["keys"], "params": params, "meta": v["_it"]["meta"],
                    "stages": v["_res"]["dirs"], "duration_s": v["_it"]["duration"], "probe": v["_res"].get("info"),
                    "n_frames": v["n_frames"], "n_batches": len(v["batch_images"]), "batch_images": v["batch_images"]}
                   for v in wvideos],
        "workflow_args": wargs, "resumes": [],
    }
    write_json(ctx.run_dir / "run.json", cache.plain(run_doc))
    ok = not errors
    ctx.partial_run = not ok
    if ok and ctx.consume:
        runs.consume_pedido(ctx.pedido, ctx.run_dir, ok=True)
        ctx.consume = False
    ctx.log(f"pronto em {ctx.elapsed():.1f} s: {n_batches} lote(s), ~{agents} agente(s), papéis {roles_mode}")
    if ctx.status:
        ctx.status.finish("pronto" if ok else "parcial")
        ctx.status = None
    env = envelope(ctx, ok, error=_partial_error(errors, len(items)) if errors else None, videos=videos_out,
                   estimate=estimate, env=env_info, workflow_args=wargs, errors=errors)
    if not ok:
        raise _Partial(env)
    return emit(ctx, env, 0)


class _Partial(Exception):
    """Exit 4 with a usable run: the envelope is ready; the pedido still counts as failed."""

    def __init__(self, env: dict):
        super().__init__("partial")
        self.env = env


def _partial_error(errors: list[dict], total: int) -> dict:
    names = "; ".join(f"{e['slug']}: {e['detail']}" for e in errors)
    return {"code": "video_falhou", "detail": f"{len(errors)} de {total} vídeo(s) falharam ({names}); os demais estão no JSON"}


def finish_estimate(ctx: Ctx, args, items: list[dict], errors: list[dict], wopts: dict, params: dict) -> int:
    ctx.stderr_on = True
    videos_out, states, secs = [], [], []
    known = True
    for it in items:
        vdir = ctx.home / WORKSPACE_DIRS["cache"] / it["video_key"]
        hits = dict(it["hits"])  # cache.stage_hits: valid and not forced
        s = estimate_seconds(it["info"], hits, params["frames"])
        secs.append(s)
        n_batches = n_frames = None
        if hits["batches"]:
            res = (cache.read_stage(cache.stage_dir(vdir, "batches", it["keys"]["batches"])) or {}).get("result") or {}
            images = res.get("batch_images") or []
            n_batches, n_frames = len(images), res.get("n_frames")
            states.append({"remaining": min(n_batches, wopts["max_batches"]) if wopts["max_batches"] else n_batches})
        else:
            known = False
        videos_out.append({"vid": it["vid"], "slug": it["slug"], "title": it["title"], "video_path": as_posix(it["video"]),
                           "vtt_path": as_posix(it["vtt"]), "video_key": it["video_key"], "duration_s": round(it["duration"], 3),
                           "resolution": [cache.info_get(it["info"], "width"), cache.info_get(it["info"], "height")],
                           "fps_avg": cache.info_get(it["info"], "fps_avg", "fps"), "n_frames": n_frames,
                           "n_batches": n_batches, "cache_hit": hits, "estimate_s": round(s, 1), "warnings": it["warn"]})
    jobs = max(1, min(args.jobs or 1, len(secs) or 1))
    if jobs > 1:
        # decoding is CPU-bound: measured on this machine, 2 Teams 1080p16 videos in parallel took
        # 87 s for 65 + 60 s of single-video estimates (each ~1.3x slower), i.e. ~0.7x the sum
        total = max(max(secs) * 1.3, sum(secs) * 0.75)
    else:
        total = sum(secs)
    agents = runs.estimate_agents(states, wopts["curation"]) if known and states else None
    estimate = {"batches": sum(s["remaining"] for s in states) if known and states else None, "agents": agents,
                "seconds": int(-(-total // 1))}
    env_info, _ = env_status(ctx)
    if agents is not None:
        guard(ctx, agents, args.forcar, videos=videos_out, estimate=estimate, env=env_info, errors=errors)
    ctx.log(f"estimativa: ~{estimate['seconds']} s para {len(items)} vídeo(s)")
    if errors and not items:
        raise Fail("video_falhou", "; ".join(f"{e['slug']}: {e['detail']}" for e in errors), errors=errors)
    env = envelope(ctx, not errors, error=_partial_error(errors, len(items) + len(errors)) if errors else None,
                   videos=videos_out, estimate=estimate, env=env_info, errors=errors)
    return emit(ctx, env, 0 if not errors else EXIT["video_falhou"])


def run_resume(ctx: Ctx, args) -> int:
    ctx.mode = "resume"
    if args.pedido or args.inputs or args.estimate or args.force:
        raise Fail("uso_invalido", "--resume não aceita --pedido, entradas, --estimar nem --force")
    ensure_home(ctx)
    try:
        run_dir = runs.find_run(ctx.home, args.resume)
    except runs.RunError as exc:
        raise Fail(exc.code, exc.detail) from None
    try:
        rj = read_json(run_dir / "run.json")
        old = rj["workflow_args"]
        vids = rj["videos"]
        assert isinstance(vids, list) and vids and len(vids) == len(old["videos"])
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
        raise Fail("run_inconsistente", f"{as_posix(run_dir / 'run.json')} incompleto ou ilegível ({exc})") from None
    ctx.run_dir, ctx.run_id = run_dir, run_dir.name
    stamp = time.strftime("%Y%m%d-%H%M%S")
    open_log(ctx, f"resume-{stamp}-{ctx.run_id}")
    print(f"run_dir: {as_posix(ctx.run_dir)}", file=sys.stderr, flush=True)
    ctx.stderr_on = True
    if not (run_dir / "objective.md").is_file():
        raise Fail("run_inconsistente", f"falta {as_posix(run_dir / 'objective.md')}")
    raw = {k: getattr(args, k) for k in ("papeis", "max_lotes", "paralelo", "esforco_analista", "esforco_curador",
                                         "curadoria")
           if getattr(args, k) is not None}
    try:
        over = runs.normalize_options(raw)
    except runs.RunError as exc:
        raise Fail(exc.code, exc.detail) from None
    stored = rj.get("options") or {}
    wopts = {"max_batches": old["options"].get("max_batches"), "max_parallel_videos": old["options"]["max_parallel_videos"],
             "analyst_effort": old["options"]["analyst_effort"], "curator_effort": old["options"]["curator_effort"],
             "curation": old["options"].get("curation") or "batches"}  # run anterior à opção: tinha o NEXT
    if args.max_lotes is not None:
        wopts["max_batches"] = over["max_lotes"] or None
    if over["paralelo"]:
        wopts["max_parallel_videos"] = over["paralelo"]
    if over["esforco_analista"]:
        wopts["analyst_effort"] = over["esforco_analista"]
    if over["esforco_curador"]:
        wopts["curator_effort"] = over["esforco_curador"]
    if over["curadoria"]:
        wopts["curation"] = over["curadoria"]
    requested = over["papeis"] or stored.get("roles_mode_requested") or old.get("roles_mode") or "inline"

    # rebuild missing or invalid stages from the stored params (deterministic, same folders)
    jobs, results = [], {}
    for i, v in enumerate(vids):
        keys = v["keys"]
        vdir = ctx.home / WORKSPACE_DIRS["cache"] / v["video_key"]
        valid = all(cache.stage_valid(cache.stage_dir(vdir, s, keys[s]), s, keys[s]) for s in cache.STAGES)
        job = {"vid": v.get("vid") or f"v{i + 1}", "slug": v["slug"], "title": v["title"], "video_path": v["video_path"],
               "vtt_path": v["vtt_path"], "video_key": v["video_key"], "video_dir": str(vdir), "keys": keys,
               "params": v.get("params") or rj.get("params"), "info": None, "force": [], "meta": v.get("meta") or {},
               "expected_fp": v["video_key"].rsplit("-", 1)[-1], "log_path": str(ctx.log_path), "quiet": ctx.quiet}
        if valid:
            results[i] = cache.prepare_video(job)  # all hits: reads the summary, refreshes last_used
        else:
            ctx.log(f"{v['slug']}: cache incompleto; refazendo com os parâmetros do run")
            jobs.append((i, job))
    if jobs:
        ffv = runtime_check(ctx, ("ffmpeg", "ffprobe", "numpy", "pillow"))
        ctx.status = Status(ctx, [j for _, j in jobs])
        ctx.status.start()
        for _, job in jobs:
            job["ffmpeg"] = ffv
            job["progress_path"] = ctx.status.progress_path(job["vid"])
        for (i, _), res in zip(jobs, run_jobs(ctx, [j for _, j in jobs], args.jobs or 1)):
            results[i] = res
        ctx.status.finish("pronto" if all(r.get("ok") for r in results.values()) else "erro")
        ctx.status = None
    errors = []
    for i, v in enumerate(vids):
        res = results.get(i) or {}
        if not res.get("ok"):
            err = res.get("error") or {}
            if err.get("code") == "instalacao_incompleta":
                raise Fail("instalacao_incompleta", err.get("detail", ""))
            errors.append({"vid": v.get("vid"), "slug": v["slug"], "video": v["video_path"], "code": "video_falhou",
                           "detail": err.get("detail") or "cache ausente e não reconstruído"})
    if errors:
        raise Fail("video_falhou", "não deu para retomar: " + "; ".join(f"{e['slug']}: {e['detail']}" for e in errors),
                   errors=errors)

    states, videos_out, wvideos, actions = [], [], [], []
    for i, (v, ov) in enumerate(zip(vids, old["videos"])):
        res = results[i]
        stage_result = res.get("batches") or {}
        images = list(stage_result.get("batch_images") or ov["batch_images"])
        n_frames = int(stage_result.get("n_frames") or ov["n_frames"])
        warn = []
        if images != list(ov["batch_images"]):
            warn.append("os lotes refeitos diferem dos do run original: relatórios antigos podem não bater")
        out_dir = run_dir / Path(ov["out_dir"]).name  # recomputed: still valid if the central folder was moved
        st = runs.resume_video(out_dir, len(images), wopts["max_batches"], stamp)
        actions += [f"{v['slug']}: {a}" for a in st["actions"]]
        states.append(st)
        videos_out.append(video_entry(v.get("vid") or f"v{i + 1}", v["slug"], v["title"], res, warn))
        wvideos.append({"slug": ov["slug"], "title": ov["title"], "n_frames": n_frames, "batch_images": images,
                        "duration_s": _hms_seconds(ov["duration"]),
                        "batches_dir": (res.get("dirs") or {}).get("batches") or ov["batches_dir"],
                        "out_dir": out_dir, **{k: st[k] for k in ("start_batch", "ledger_exists",
                                                                  "start_prompt_exists", "final_exists")}})
    combined_exists, acts = runs.resume_combined(run_dir, states)
    actions += acts
    for a in actions:
        ctx.log(a)
    roles_mode, env_info = roles_and_env(ctx, requested)
    agents = runs.estimate_agents(states, wopts["curation"])
    n_batches = sum(s["remaining"] for s in states)
    estimate = {"batches": n_batches, "agents": agents, "seconds": round(ctx.elapsed())}
    guard(ctx, agents, args.forcar, videos=videos_out, estimate=estimate, env=env_info)
    wargs = runs.build_workflow_args(run_id=ctx.run_id, run_dir=run_dir, roles_dir=ctx.home / WORKSPACE_DIRS["roles"],
                                     roles_mode=roles_mode, language=old["language"], options=wopts,
                                     combined_exists=combined_exists, videos=wvideos)
    check_args(wargs)
    if all(s["final_exists"] for s in states) and (len(states) == 1 or combined_exists):
        ctx.warnings.append("o run já está completo: nada a retomar (para outra pergunta, um run novo reaproveita o cache)")
    rj["workflow_args"] = wargs
    rj["options"] = {**stored, **wopts, "roles_mode_requested": requested, "roles_mode": roles_mode}
    rj.setdefault("resumes", []).append({"at": cache.iso_now(), "argv": sys.argv[1:], "actions": actions,
                                         "start_batches": [s["start_batch"] for s in states]})
    write_json(run_dir / "run.json", cache.plain(rj))
    ctx.log(f"retomada pronta: {n_batches} lote(s) a analisar, ~{agents} agente(s), papéis {roles_mode}")
    env = envelope(ctx, True, videos=videos_out, estimate=estimate, env=env_info, workflow_args=wargs)
    return emit(ctx, env, 0)


def _hms_seconds(text: str) -> float:
    try:
        return parse_time(text)
    except (TypeError, ValueError):
        return 0.0


# --- main -------------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    setup_console()
    ctx = Ctx()
    try:
        args = build_parser().parse_args(argv)
        ctx.ascii, ctx.quiet, ctx.debug, ctx.json_out = args.ascii, args.quiet, args.debug, args.json_out
        ctx.home = workspace_home(args.home)
        modes = [m for m, on in (("--resume", args.resume is not None), ("--gc", args.gc is not None)) if on]
        if len(modes) > 1:
            raise Fail("uso_invalido", f"use só um modo: {' ou '.join(modes)}")
        if args.gc is not None:
            return run_gc(ctx, args)
        if args.resume is not None:
            return run_resume(ctx, args)
        return run_prepare(ctx, args)
    except _Partial as p:
        code = EXIT["video_falhou"]
        _consume_failed(ctx)
        return emit(ctx, p.env, code)
    except runs.RunError as exc:
        return _failed(ctx, Fail(exc.code, exc.detail))
    except Fail as f:
        return _failed(ctx, f)
    except KeyboardInterrupt:
        return _failed(ctx, Fail("interrompido", "interrompido (Ctrl+C ou processo encerrado); os estágios completos ficam no cache"))
    except Exception as exc:  # a bug: still one JSON line, with the traceback in the log
        import traceback
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        ctx.log("ERRO INTERNO\n" + tb, echo=False)
        if ctx.debug:
            print(tb, file=sys.stderr)
        return _failed(ctx, Fail("erro_interno", f"{type(exc).__name__}: {exc}"))


def _consume_failed(ctx: Ctx) -> None:
    if ctx.status is not None:
        with contextlib.suppress(Exception):
            ctx.status.finish("erro")
        ctx.status = None
    if ctx.consume and ctx.pedido is not None:
        with contextlib.suppress(OSError):
            runs.consume_pedido(ctx.pedido, ctx.run_dir, ok=False, copy_to_run=ctx.partial_run)
        ctx.consume = False


def _failed(ctx: Ctx, f: Fail) -> int:
    code = EXIT.get(f.code, 1)
    ctx.log(f"erro {f.code}: {f.detail}", echo=False)
    _consume_failed(ctx)
    extra = dict(f.extra)
    env = envelope(ctx, False, error={"code": f.code, "detail": f.detail}, **extra)
    return emit(ctx, env, code)


if __name__ == "__main__":
    sys.exit(main())
