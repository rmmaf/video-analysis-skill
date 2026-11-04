"""Run folders, the request file (request.md), resume scan and workflow_args (plan, unified
contract items 1-4; design-agentico DL-2, DL-5, DL-6, DL-11, DL-12).

- ``parse_request``: sections ``# videos`` (one path per line), ``# objective`` (literal text) and
  ``# options`` (``key: value``; the value is the rest of the line after the first ":").
  Headings are matched without accents or case; the Portuguese names of before the rename
  (``# objetivo``, ``# opcoes``, also "# Opções") still work.
- ``normalize_options``: validates the option values (request or CLI) into typed values. The
  request's keys are the English names of ``OPTION_NAMES``; the internal keys, in Portuguese (the
  names of the command-line options), still work in the request.
- ``new_run``: ``runs/<YYYYMMDD-HHMMSS>-<slug24>`` (``-2``... on a collision in the same second).
- ``resume_video``: DL-11. No ledger -> prompts/ and reports/ go to ``_descartado/<stamp>/`` and the
  video starts at batch 1; ``relatorio.md`` becomes ``relatorio.anterior.md`` when work remains. The
  final report is never ``report.md`` (Claude Code blocks subagents from writing report.md, summary.md
  and findings.md); a legacy ``report.md`` of an older run still counts as the final (``_settle_final``).
- ``build_workflow_args``: the v1 object the Workflow script validates.
- ``consume_request``: success -> ``<run_dir>/request.md``; failure -> ``request.error.md``
  (``pedido.erro.md`` for a request still named ``pedido.md``), replacing an older one, so the next
  Write of the request never needs a Read first.
- ``parse_pedido``, ``read_pedido`` and ``consume_pedido``: the names of before the rename, kept with
  the same parameters for scripts that import them.
"""
from __future__ import annotations

import os
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any

from . import EFFORT_LEVELS, SCHEMA_VERSION, WORKFLOW_DEFAULTS
from .common import as_posix, fmt_hms, ms_to_s, parse_time_ms, read_json, resolve_unicode_paths, slugify

__all__ = [
    "FINAL_MD", "LEGACY_FINAL_MD", "OPTION_KEYS", "OPTION_NAMES", "REQUEST_MD", "RunError", "build_workflow_args",
    "consume_request", "copy_roles", "estimate_agents", "find_run", "new_run", "normalize_options",
    "objective_markdown", "parse_request", "read_request", "resume_combined", "resume_video", "run_slug",
    "scaffold_video",
    "unique_slugs", "workflow_options", "parse_regions", "parse_trecho", "parse_speakers", "fold",
    "consume_pedido", "parse_pedido", "read_pedido",
]

MAX_SLUG = 40
RUN_SLUG = 24
FINAL_MD = "relatorio.md"             # final report, per video (<out_dir>/) and combined (<run_dir>/)
BACKUP_MD = "relatorio.anterior.md"   # the previous final, kept when a resume has work left
LEGACY_FINAL_MD = "report.md"         # the name before the rename (the harness blocks subagents from writing it)
LEGACY_BACKUP_MD = "report.anterior.md"
REQUEST_MD = "request.md"             # the request's copy in the run folder (pedido.md before the rename)
LEGACY_REQUEST_STEM = "pedido"        # a request still named pedido.md fails into pedido.erro.md, as before


class RunError(ValueError):
    """An input problem with an error code of the stdout contract (exit 2)."""

    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def fold(text: str) -> str:
    """Accent- and case-insensitive key: "Opções" -> "opcoes", "--max-lotes" -> "max_lotes"."""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold().strip()
    s = s.lstrip("-").strip()
    return re.sub(r"[\s\-]+", "_", s)


# --- request.md -------------------------------------------------------------------------------------

# folded heading -> section; "objetivo" and "opcoes" are the names of before the rename
_SECTIONS = {"videos": "videos", "video": "videos", "objective": "objective", "objetivo": "objective",
             "options": "options", "option": "options", "opcoes": "options", "opcao": "options"}
_HEADING_RE = re.compile(r"^#[ \t]+(\S.*?)[ \t#]*$")
_BULLET_RE = re.compile(r"^(?:[-*+][ \t]+)")


def _strip_quotes(s: str) -> str:
    s = s.strip()
    for q in ('"', "'", "`"):
        if len(s) >= 2 and s[0] == q and s[-1] == q:
            return s[1:-1].strip()
    return s


def parse_request(text: str) -> dict:
    """``{"videos": [str], "objective": str, "options": {key: value or [values]}}``, with the options
    under their internal keys (``OPTION_KEYS``).

    Raises RunError ``pedido_invalido`` (missing/duplicated section, text before the first
    section, option line without ":"), ``objetivo_ausente`` (empty objective) or
    ``opcao_invalida`` (unknown or repeated option).
    """
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.split("\n"):
        m = _HEADING_RE.match(line)
        name = _SECTIONS.get(fold(m.group(1))) if m else None
        if name:
            if name in blocks:
                raise RunError("pedido_invalido", f"the section '# {name}' appears twice in the request")
            blocks[name] = []
            current = name
        elif current is None:
            if line.strip():
                raise RunError("pedido_invalido", f"text before the first section ('# videos'): {line.strip()[:80]!r}")
        else:
            blocks[current].append(line)
    if "videos" not in blocks:
        raise RunError("pedido_invalido", "missing section '# videos' (one video or folder path per line)")
    videos = []
    for line in blocks["videos"]:
        s = _BULLET_RE.sub("", line.strip())
        s = _strip_quotes(s)
        if s:
            videos.append(s)
    if not videos:
        raise RunError("pedido_invalido", "the section '# videos' is empty")
    lines = blocks.get("objective")
    if lines is None:
        raise RunError("objetivo_ausente", "missing section '# objective' in the request")
    while lines and not lines[0].strip():
        lines = lines[1:]
    objective = "\n".join(lines).rstrip()
    if not objective.strip():
        raise RunError("objetivo_ausente", "the section '# objective' is empty")
    options: dict[str, Any] = {}
    for raw in blocks.get("options", []):
        line = _BULLET_RE.sub("", raw.strip())
        if not line:
            continue
        if ":" not in line:
            raise RunError("pedido_invalido", f"option line without ':' in '# options': {line[:80]!r} (use key: value)")
        key, value = line.split(":", 1)
        canon = option_key(key)
        value = _strip_quotes(value)
        if canon == "ignorar_regiao":
            options.setdefault(canon, []).append(value)
        elif canon in options:
            raise RunError("opcao_invalida", f"the option {OPTION_NAMES[canon]!r} appears twice in the request")
        else:
            options[canon] = value
    return {"videos": videos, "objective": objective, "options": options}


def read_request(path: str | os.PathLike) -> dict:
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raise RunError("pedido_invalido", f"request file not found: {as_posix(p)} (write it with Write first)") from None
    except OSError as exc:
        raise RunError("pedido_invalido", f"could not read the request file {as_posix(p)}: {exc}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return parse_request(text)


# --- options --------------------------------------------------------------------------------------

# internal keys: the names of the command-line options (--max-lotes -> max_lotes), still accepted in the request
OPTION_KEYS = ("papeis", "max_lotes", "paralelo", "esforco_analista", "esforco_curador", "curadoria", "idioma",
               "trecho", "falantes", "sensibilidade", "ignorar_regiao")
# the keys of the request's '# options', in English, by internal key
OPTION_NAMES = {"papeis": "roles", "max_lotes": "max_batches", "paralelo": "parallel",
                "esforco_analista": "analyst_effort", "esforco_curador": "curator_effort", "curadoria": "curation",
                "idioma": "language", "trecho": "range", "falantes": "speakers", "sensibilidade": "sensitivity",
                "ignorar_regiao": "ignore_region"}
_OPTION_ALIASES = {
    "papel": "papeis", "roles": "papeis", "roles_mode": "papeis", "modo": "papeis",
    "max_batches": "max_lotes", "lotes": "max_lotes", "maximo_de_lotes": "max_lotes",
    "parallel": "paralelo", "max_parallel_videos": "paralelo",
    "analyst_effort": "esforco_analista", "esforco_do_analista": "esforco_analista",
    "curator_effort": "esforco_curador", "esforco_do_curador": "esforco_curador",
    "curation": "curadoria",
    "language": "idioma", "lingua": "idioma",
    "range": "trecho", "speakers": "falantes", "speaker_names": "falantes",
    "sensitivity": "sensibilidade",
    "ignore_region": "ignorar_regiao", "ignore_regions": "ignorar_regiao", "ignorar_regioes": "ignorar_regiao",
}


def option_key(key: str) -> str:
    k = fold(key)
    k = _OPTION_ALIASES.get(k, k)
    if k not in OPTION_KEYS:
        raise RunError("opcao_invalida", f"unknown option {key.strip()!r}; accepted: {', '.join(OPTION_NAMES.values())} "
                                         f"(or the Portuguese {', '.join(OPTION_KEYS)})")
    return k


_EFFORT_PT = {"baixo": "low", "baixa": "low", "medio": "medium", "media": "medium", "alto": "high", "alta": "high"}
_CURATION = {"plano": "plan", "plan": "plan", "inicial": "plan",
             "lotes": "batches", "lote": "batches", "por_lote": "batches", "batches": "batches"}
_SENS = {"low": "low", "baixa": "low", "baixo": "low", "normal": "normal", "media": "normal", "medio": "normal",
         "high": "high", "alta": "high", "alto": "high"}
_IDIOMA_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def _bad(key: str, value: Any, why: str) -> RunError:
    """The option by its request key and its command-line option: "max_batches (--max-lotes): …"."""
    name = f"{OPTION_NAMES[key]} (--{key.replace('_', '-')})" if key in OPTION_NAMES else key
    return RunError("opcao_invalida", f"{name}: {str(value).strip()!r} is invalid ({why})")


def _int(key: str, value: Any, lo: int, hi: int | None) -> int:
    s = str(value).strip()
    if not re.fullmatch(r"\+?\d+", s):
        raise _bad(key, value, "use an integer")
    n = int(s)
    if n < lo or (hi is not None and n > hi):
        raise _bad(key, value, f"use {lo} to {hi}" if hi is not None else f"use {lo} or more")
    return n


def parse_trecho(value: Any) -> list[float]:
    """"hh:mm:ss-hh:mm:ss" (also with "–", " a " or " até ") -> [start, end] in seconds."""
    s = str(value).strip().replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+(?:a|ate|até)\s+", "-", s, flags=re.IGNORECASE)
    parts = [p.strip() for p in s.split("-")]
    if len(parts) != 2 or not all(parts):
        raise _bad("trecho", value, "use hh:mm:ss-hh:mm:ss")
    try:
        t0, t1 = parse_time_ms(parts[0]), parse_time_ms(parts[1])
    except ValueError:
        raise _bad("trecho", value, "use hh:mm:ss-hh:mm:ss") from None
    if t0 < 0 or t1 <= t0:
        raise _bad("trecho", value, "the end must be after the start")
    return [ms_to_s(t0), ms_to_s(t1)]


def parse_speakers(value: Any) -> dict:
    """"SPEAKER_01=Name;SPEAKER_03=Other" -> {"SPEAKER_01": "Name", "SPEAKER_03": "Other"}."""
    out: dict[str, str] = {}
    for part in re.split(r"[;\n]", str(value)):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise _bad("falantes", value, "use LABEL=Name separated by ';'")
        label, name = (x.strip() for x in part.split("=", 1))
        if not label or not name:
            raise _bad("falantes", value, "use LABEL=Name separated by ';'")
        out[label] = name
    if not out:
        raise _bad("falantes", value, "use LABEL=Name separated by ';'")
    return out


def parse_regions(value: Any) -> list[list[float]]:
    """"x,y,w,h; x,y,w,h" (fractions of the displayed frame, decimal point) -> [[x, y, w, h], ...]."""
    values = value if isinstance(value, list) else [value]
    regions = []
    for item in values:
        for part in re.split(r"[;\n]", str(item)):
            part = part.strip().strip("[]()")
            if not part:
                continue
            nums = [n for n in re.split(r"\s*,\s*|\s+", part) if n]
            if len(nums) != 4:
                raise _bad("ignorar_regiao", part, "use x,y,w,h with a decimal point; several regions separated by ';'")
            try:
                x, y, w, h = (float(n) for n in nums)
            except ValueError:
                raise _bad("ignorar_regiao", part, "use numbers with a decimal point") from None
            eps = 1e-6
            if min(x, y) < 0 or w <= 0 or h <= 0 or x + w > 1 + eps or y + h > 1 + eps:
                raise _bad("ignorar_regiao", part, "fractions from 0 to 1, with x+w ≤ 1 and y+h ≤ 1")
            regions.append([round(x, 4), round(y, 4), round(w, 4), round(h, 4)])
    if not regions:
        raise _bad("ignorar_regiao", value, "no region")
    return regions


def normalize_options(raw: dict) -> dict:
    """Raw strings (request or CLI) -> typed options; every key of OPTION_KEYS is present
    (None when not given). Raises RunError ``opcao_invalida``."""
    opts: dict[str, Any] = dict.fromkeys(OPTION_KEYS)
    for key, value in raw.items():
        key = option_key(key)
        if value is None:
            continue
        if not isinstance(value, list) and not str(value).strip():
            raise _bad(key, value, "empty value")
        if key == "papeis":
            f = fold(value)
            mode = {"agents": "agents", "agentes": "agents", "agent": "agents", "inline": "inline"}.get(f)
            if not mode:
                raise _bad(key, value, "use agents or inline")
            opts[key] = mode
        elif key == "max_lotes":
            f = fold(value)
            opts[key] = None if f in ("todos", "all", "0") else _int(key, value, 0, None) or None
        elif key == "paralelo":
            opts[key] = _int(key, value, 1, WORKFLOW_DEFAULTS["max_parallel_videos_cap"])
        elif key in ("esforco_analista", "esforco_curador"):
            f = fold(value)
            level = _EFFORT_PT.get(f, f)
            if level not in EFFORT_LEVELS:
                raise _bad(key, value, "use " + ", ".join(EFFORT_LEVELS))
            opts[key] = level
        elif key == "curadoria":
            mode = _CURATION.get(fold(value))
            if not mode:
                raise _bad(key, value, "use plan or batches (or plano, lotes)")
            opts[key] = mode
        elif key == "idioma":
            s = str(value).strip().replace("_", "-")
            if not _IDIOMA_RE.match(s):
                raise _bad(key, value, "use a code such as pt-BR, en or es")
            opts[key] = s
        elif key == "trecho":
            opts[key] = parse_trecho(value)
        elif key == "falantes":
            opts[key] = parse_speakers(value)
        elif key == "sensibilidade":
            level = _SENS.get(fold(value))
            if not level:
                raise _bad(key, value, "use low, normal or high")
            opts[key] = level
        elif key == "ignorar_regiao":
            opts[key] = parse_regions(value)
    return opts


def workflow_options(opts: dict) -> dict:
    """workflow_args.options from the typed options (defaults: all batches, 3 videos, analyst and
    curator high, curation plan)."""
    return {"max_batches": opts.get("max_lotes") or None,
            "max_parallel_videos": opts.get("paralelo") or WORKFLOW_DEFAULTS["max_parallel_videos"],
            "analyst_effort": opts.get("esforco_analista") or WORKFLOW_DEFAULTS["analyst_effort"],
            "curator_effort": opts.get("esforco_curador") or WORKFLOW_DEFAULTS["curator_effort"],
            "curation": opts.get("curadoria") or WORKFLOW_DEFAULTS["curation"]}


# --- run folders ------------------------------------------------------------------------------------

def _cut(slug: str, max_len: int) -> str:
    if len(slug) <= max_len:
        return slug
    if slug[max_len] == "-":
        return slug[:max_len].rstrip("-")
    cut = slug.rfind("-", 0, max_len)
    return (slug[:cut] if cut > 0 else slug[:max_len]).rstrip("-")


def run_slug(name: str) -> str:
    """Slug of the run folder name: at most 24 chars (the Teams stamp is dropped when too long)."""
    return _cut(slugify(name), RUN_SLUG) or "video"


def new_run(runs_dir: str | os.PathLike, name: str, now: float | None = None) -> tuple[str, Path]:
    """Create ``runs/<YYYYMMDD-HHMMSS>-<slug>``; ``-2``, ``-3``... if it already exists."""
    runs = Path(runs_dir)
    runs.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    base = f"{stamp}-{run_slug(name)}"
    for i in range(1, 1000):
        run_id = base if i == 1 else f"{base}-{i}"
        try:
            (runs / run_id).mkdir()
            return run_id, runs / run_id
        except FileExistsError:
            continue
    raise RunError("uso_invalido", f"could not create the run folder in {as_posix(runs)}")


def unique_slugs(stems: list[str]) -> list[str]:
    """ASCII slugs (<= 40 chars), the second equal one gets ``-2``, the third ``-3``..."""
    out: list[str] = []
    used: set[str] = set()
    for stem in stems:
        base = slugify(stem)[:MAX_SLUG].strip("-") or "video"
        slug, n = base, 1
        while slug in used:
            n += 1
            suffix = f"-{n}"
            slug = base[:MAX_SLUG - len(suffix)].rstrip("-") + suffix
        used.add(slug)
        out.append(slug)
    return out


def scaffold_video(out_dir: str | os.PathLike) -> Path:
    out = Path(out_dir)
    (out / "prompts").mkdir(parents=True, exist_ok=True)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    return out


def objective_markdown(objective: str, options: list[str], videos: list[str]) -> str:
    """objective.md (DL-5): ``# Objetivo (literal)`` exactly as given, ``# Opções``, ``# Vídeos``."""
    lines = objective.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines = lines[1:]
    body = "\n".join(lines).rstrip()
    opt = "\n".join(f"- {o}" for o in options) or "- (padrões)"
    vids = "\n".join(f"- {v}" for v in videos)
    return f"# Objetivo (literal)\n\n{body}\n\n# Opções\n\n{opt}\n\n# Vídeos\n\n{vids}\n"


def copy_roles(skill_dir: str | os.PathLike, roles_dir: str | os.PathLike) -> list[str]:
    """DL-6: copy ``<skill>/agents/*.md`` to ``<workspace>/roles/`` (byte-identical) on every run,
    so inline agents read their role inside the already-allowed workspace."""
    src = Path(skill_dir) / "agents"
    dst = Path(roles_dir)
    dst.mkdir(parents=True, exist_ok=True)
    names = []
    for f in sorted(src.glob("*.md"), key=lambda p: p.name.lower()):
        target = dst / f.name
        data = f.read_bytes()
        try:
            same = target.read_bytes() == data
        except OSError:
            same = False
        if not same:
            tmp = target.with_name(f".{target.name}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
        names.append(f.name)
    return names


# --- request consumption ----------------------------------------------------------------------------

def _move(src: Path, dst: Path) -> None:
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(str(src), str(dst))


def consume_request(request: str | os.PathLike, run_dir: str | os.PathLike | None, ok: bool,
                    copy_to_run: bool = False) -> Path | None:
    """Success: move the request to ``<run_dir>/request.md``. Failure: rename it to
    ``<stem>.error<ext>`` next to it (``pedido.erro.md`` for a request still named ``pedido.md``),
    replacing an older one; with ``copy_to_run`` a copy also goes to the run folder (a video failed
    but the others form a usable run)."""
    src = Path(request)
    if not src.is_file():
        return None
    if ok and run_dir is not None:
        dst = Path(run_dir) / REQUEST_MD
        _move(src, dst)
        return dst
    if copy_to_run and run_dir is not None:
        try:
            shutil.copyfile(src, Path(run_dir) / REQUEST_MD)
        except OSError:
            pass
    tag = ".erro" if src.stem.casefold() == LEGACY_REQUEST_STEM else ".error"
    dst = src.with_name(f"{src.stem}{tag}{src.suffix or '.md'}")
    _move(src, dst)
    return dst


# the names of before the rename, kept with the same parameters for scripts that import them
parse_pedido = parse_request
read_pedido = read_request


def consume_pedido(pedido: str | os.PathLike, run_dir: str | os.PathLike | None, ok: bool,
                   copy_to_run: bool = False) -> Path | None:
    """``consume_request`` under its old name, with the old parameter ``pedido``."""
    return consume_request(pedido, run_dir, ok, copy_to_run)


# --- resume (DL-11) -----------------------------------------------------------------------------------

_LATEST = {"ultimo", "ultima", "latest", "last", "recente", "mais_recente"}


def _run_ok(d: Path) -> bool:
    try:
        doc = read_json(d / "run.json")
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and isinstance(doc.get("workflow_args"), dict)


def find_run(home: str | os.PathLike, ref: str) -> Path:
    """``ultimo`` (most recent run with a run.json), a run_id (or a unique prefix of one) or a run
    folder path. Raises RunError ``run_nao_encontrado``."""
    runs = Path(home) / "runs"
    r = str(ref or "").strip().strip('"').strip("'")
    if not r or fold(r) in _LATEST:
        cands = sorted((d for d in runs.iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True) if runs.is_dir() else []
        for d in cands:
            if _run_ok(d):
                return d
        raise RunError("run_nao_encontrado", f"no run with run.json in {as_posix(runs)}")
    if any(c in r for c in "/\\:"):
        hits = [p for p in resolve_unicode_paths(r) if p.is_dir()]
        if len(hits) == 1 and _run_ok(hits[0]):
            return hits[0]
        raise RunError("run_nao_encontrado", f"invalid run folder or no run.json: {r}")
    exact = runs / r
    if exact.is_dir():
        if _run_ok(exact):
            return exact
        raise RunError("run_nao_encontrado", f"run {r} has no run.json with workflow_args (did its prepare fail?)")
    pref = [d for d in (runs.iterdir() if runs.is_dir() else []) if d.is_dir() and d.name.startswith(r) and _run_ok(d)]
    if len(pref) == 1:
        return pref[0]
    if len(pref) > 1:
        raise RunError("run_nao_encontrado", f"{r!r} is a prefix of {len(pref)} runs: " + ", ".join(sorted(d.name for d in pref)[:5]))
    raise RunError("run_nao_encontrado", f"run {r!r} not found in {as_posix(runs)}")


def _discard(out_dir: Path, stamp: str) -> list[str]:
    """Move non-empty prompts/ and reports/ to _descartado/<stamp>/ (no ledger: start over)."""
    moved = []
    for name in ("prompts", "reports"):
        src = out_dir / name
        if src.is_dir() and any(src.iterdir()):
            dst_root = out_dir / "_descartado" / stamp
            dst_root.mkdir(parents=True, exist_ok=True)
            dst = dst_root / name
            n = 2
            while dst.exists():
                dst = dst_root / f"{name}-{n}"
                n += 1
            _move(src, dst)
            moved.append(as_posix(dst))
    return moved


def _settle_final(folder: Path, remains: bool, label: str = "") -> tuple[bool, list[str]]:
    """Is the final report of ``folder`` (a video's out_dir, or the run root for the combined one)
    done? Returns (exists, actions).

    With work left (``remains``) the final is moved to ``relatorio.anterior.md`` and the answer is
    False. A legacy ``report.md`` counts as the final too: with work left it is backed up the same
    way (as ``report.anterior.md`` when a ``relatorio.md`` was there as well); with nothing left it
    is renamed to ``relatorio.md``, the path the Workflow derives for a skipped video and the CROSS."""
    new, old = folder / FINAL_MD, folder / LEGACY_FINAL_MD
    tag = f" {label}" if label else ""
    actions: list[str] = []
    if remains:
        backup = BACKUP_MD
        for src in (new, old):
            if src.is_file():
                _move(src, folder / backup)
                actions.append(f"{src.name}{tag} -> {backup}")
                backup = LEGACY_BACKUP_MD
        return False, actions
    if not new.is_file() and old.is_file():
        _move(old, new)
        actions.append(f"{LEGACY_FINAL_MD}{tag} -> {FINAL_MD} (new name of the final report)")
    return new.is_file(), actions


def resume_video(out_dir: str | os.PathLike, n_batches: int, max_batches: int | None, stamp: str) -> dict:
    """Resume state of one video (DL-11), applying the file moves it implies.

    Returns ``{"start_batch", "last", "ledger_exists", "start_prompt_exists", "final_exists",
    "remaining", "actions"}``: ``start_batch`` is the first batch up to ``last`` without
    ``reports/batch_NNN.md`` (``last + 1`` when none is missing); ``final_exists`` only when
    ``relatorio.md`` (or a legacy ``report.md``, renamed to it) exists and no batch remains.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    last = min(n_batches, max_batches) if max_batches else n_batches
    actions: list[str] = []
    ledger = (out / "ledger.md").is_file()
    if not ledger:
        for dst in _discard(out, stamp):
            actions.append(f"no ledger: moved to {dst}")
        start = 1
    else:
        start = next((n for n in range(1, last + 1) if not (out / "reports" / f"batch_{n:03d}.md").is_file()), last + 1)
    scaffold_video(out)
    remains = start <= last
    final_exists, settled = _settle_final(out, remains)
    actions += settled
    prompt = remains and (out / "prompts" / f"batch_{start:03d}.md").is_file()
    return {"start_batch": start, "last": last, "ledger_exists": ledger, "start_prompt_exists": bool(prompt),
            "final_exists": final_exists, "remaining": 0 if final_exists else max(0, last - start + 1),
            "actions": actions}


def resume_combined(run_dir: str | os.PathLike, states: list[dict]) -> tuple[bool, list[str]]:
    """The run-root relatorio.md (combined): moved to relatorio.anterior.md when there are 2+ videos
    and any of them will run; otherwise, if it exists, the CROSS is skipped. A legacy report.md counts
    as it (``_settle_final``). Returns (combined_exists, actions)."""
    remains = len(states) > 1 and any(not s["final_exists"] for s in states)
    return _settle_final(Path(run_dir), remains, "combined")


def estimate_agents(states: list[dict], curation: str = WORKFLOW_DEFAULTS["curation"]) -> int:
    """DL-12: Σ(R + 2) over the videos that will run (R = batches left to analyse: INIT, analysts,
    FINAL; R + 1 on resume, when the ledger exists and there is no INIT), or Σ(2·R + 1) with curation
    "batches" (a NEXT before each batch but the first), plus 1 for the CROSS when the run has more than
    one video. Same count as the Workflow's guard."""
    def per(s: dict) -> int:
        r = int(s.get("remaining") or 0)
        return 2 * r + 1 if curation == "batches" else r + (1 if s.get("ledger_exists") else 2)
    total = sum(0 if s.get("final_exists") else per(s) for s in states)
    return total + (1 if len(states) > 1 else 0)


# --- workflow_args ------------------------------------------------------------------------------------

def build_workflow_args(*, run_id: str, run_dir: str | os.PathLike, roles_dir: str | os.PathLike, roles_mode: str,
                        language: str, options: dict, combined_exists: bool, videos: list[dict]) -> dict:
    """workflow_args v1 (plan, unified contract item 1); every path absolute with "/".

    ``videos`` items: slug, title, duration_s, n_frames, batches_dir, out_dir, batch_images and
    the resume fields (start_batch, ledger_exists, start_prompt_exists, final_exists).
    """
    run_dir = as_posix(os.path.abspath(run_dir))
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_dir": run_dir,
        "objective_path": f"{run_dir}/objective.md",
        "roles_dir": as_posix(os.path.abspath(roles_dir)),
        "roles_mode": roles_mode,
        "language": language,
        "options": {"max_batches": options.get("max_batches"), "max_parallel_videos": options["max_parallel_videos"],
                    "analyst_effort": options["analyst_effort"], "curator_effort": options["curator_effort"],
                    "curation": options.get("curation") or WORKFLOW_DEFAULTS["curation"]},
        "combined_exists": bool(combined_exists),
        "videos": [{
            "slug": v["slug"],
            "title": v["title"],
            "duration": fmt_hms(v["duration_s"], 0),
            "n_frames": int(v["n_frames"]),
            "batches_dir": as_posix(os.path.abspath(v["batches_dir"])),
            "out_dir": as_posix(os.path.abspath(v["out_dir"])),
            "batch_images": [int(n) for n in v["batch_images"]],
            "start_batch": int(v.get("start_batch", 1)),
            "ledger_exists": bool(v.get("ledger_exists", False)),
            "start_prompt_exists": bool(v.get("start_prompt_exists", False)),
            "final_exists": bool(v.get("final_exists", False)),
        } for v in videos],
    }
