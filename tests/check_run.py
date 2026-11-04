"""Checks a finished run of the video-analysis skill (test plan, step 2).

Usage:
    python check_run.py <run_dir> [--args <workflow_args.json>] [--return <return.json | '{...}'>]

The workflow_args (to find the batches_dir and out_dir of each video) come from --args or from
<run_dir>/run.json (key "workflow_args"); <run_dir>/prepare.json is the fallback. Examines batches 1..last
of each video, with last = min(number of batches, options.max_batches), or batches.planned of the return
when --return is given. The curation is options.curation: plan (the prepare.py default) or batches
(--curadoria lotes, with the NEXT before each batch after the 1st); without the key, batches, because the
run predates the option and had the NEXT.

Checks (one line each: PASS, AVISO or FAIL; only FAIL fails the run):
  args        the run_dir of the args is the folder being checked
  batch       images: image lines of batch_NNN.md = batch_images[n-1]. format: the contract of
              scripts/va/render.py: the image line is only the absolute path, with "/", of a .jpg, plus
              " (revisita de Fxxxx)" when the header ends in "· = Fxxxx" (also accepts the old English
              form, " (revisit of Fxxxx)"); an in-batch revisit ("· = Fxxxx (mesma tela, acima)" or
              "(same screen, above)") has no image line; no path repeats in the batch; there is a
              "Folha de contato:" line (or "Contact sheet:"), which never counts as an image
  ledger      exists; "Integrado até o lote: NNN de BBB" with BBB = number of batches and NNN = last-1 (or
              last) with batches, or from 000 to last with plan (the INIT writes 000; without the NEXT,
              nobody rewrites the ledger); words (budget 1,500); plan table (header with "lotes")
  prompt      exists (with plan, only the one of batch 1 is required; those of the other batches are
              checked if they exist); cites the absolute path of batch_NNN.md; copied cue: 8 to 11
              consecutive words of a cue of the batch = short quote (AVISO), 12 or more, or the whole cue =
              copy (FAIL); references to frames, with a range F0041–F0045 counting as 1: up to 8 (the 3–6
              Foco items and up to 2 reference frames of the curator role), up to 16 AVISO, above FAIL
              (copied frame list); words (budget 450)
  report      cites every frame of the batch, one by one or inside a range (F0042–F0045)
  final       <out_dir>/relatorio.md has "Resposta ao objetivo" and "Cobertura: lotes x/B" with x = last;
              frames a/b equal to the return
  combined    <run_dir>/relatorio.md when there are 2+ videos
  return      (--return) frames seen = expected; status ≠ failed; partial when planned < total

Word budgets: up to the limit, PASS; up to 1.5 times the limit, AVISO; above, FAIL.
Exit code: 0 no FAIL (there may be AVISO) · 1 some check failed · 2 usage error.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

PASS, AVISO, FAIL = "PASS", "AVISO", "FAIL"
FINAL_MD = "relatorio.md"   # final report, per video and combined (never report.md: the harness blocks subagents)
MAX_LEDGER_WORDS = 1500
MAX_PROMPT_WORDS = 450
SOFT_FACTOR = 1.5      # budget exceeded up to 1.5x: AVISO; above: FAIL
MAX_FRAME_REFS = 8     # curator role: Foco with 3-6 items by id/time + up to 2 reference frames
HARD_FRAME_REFS = 16   # above this the prompt is listing the frames of the batch
COPY_WORDS = 8         # 8+ consecutive words of a cue: short quote (AVISO)
HARD_COPY_WORDS = 12   # 12+ consecutive words, or the whole cue (with 8+ words): copy (FAIL)

FRAME_HDR = re.compile(r"^###\s+(F\d{4,5})\b(.*)$")
SAME_ABOVE = re.compile(r"·\s*=\s*(F\d{4,5})\s*\((?:mesma tela, acima|same screen, above)\)\s*$")
REVISIT_HDR = re.compile(r"·\s*=\s*(F\d{4,5})\s*$")
SHEET_LINE = re.compile(r"^\s*(?:Folha de contato|Contact sheet)\s*:", re.I)
CUE_LINE = re.compile(r"^\s*-\s+(C\d{4,5})\s+(.*)$")
TIME_RANGE = re.compile(r"^≈?\s*\d{1,2}(?::\d{2}){1,2}(?:[.,]\d+)?\s*[–—-]\s*≈?\s*\d{1,2}(?::\d{2}){1,2}(?:[.,]\d+)?\s*")
CONT = re.compile(r"\((?:cont\.?|cont'd)\)\s*", re.I)
SPEAKER = re.compile(r"^(?:S\d+|S\?)\s*:\s*")
IMG_LINE = re.compile(r"^\s*(?:[A-Za-z]:[\\/]|/|\\\\)\S.*?\.(?:jpe?g|png|webp)\b", re.I)
IMG_STRICT = re.compile(r"^((?:[A-Za-z]:/|//(?!/))[^\\]*?\.jpe?g)(?: \((?:revisita de|revisit of) (F\d{4,5})\))?$", re.I)
RANGE_RE = re.compile(r"\bF(\d{4,5})(?:\s*[–—-]\s*|\s*\.\.\s*|\s+(?:a|até|to)\s+)F?(\d{4,5})\b")
SINGLE_RE = re.compile(r"\bF(\d{4,5})\b")
WORD = re.compile(r"\w+(?:['’-]\w+)*")
LEDGER_HDR = re.compile(r"Integrado até o lote:\s*(\d{3,})(?:\s+de\s+(\d{3,}))?")
SEP_ROW = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
ANSWER = re.compile(r"resposta ao objetivo|answer to the objective|respuesta al objetivo", re.I)
COVERAGE = re.compile(r"(?:cobertura|coverage)\b[^\n]*?\b(?:lotes|batches)\s*:?\s*(\d+)\s*/\s*(\d+)", re.I)
COV_FRAMES = re.compile(r"(?:cobertura|coverage)\b[^\n]*?\bframes\s*:?\s*(\d+)\s*/\s*(\d+)", re.I)


class UsageError(Exception):
    pass


@dataclass
class Batch:
    frames: list[str] = field(default_factory=list)
    cues: dict[str, str] = field(default_factory=dict)
    images: int = 0
    sheet: bool = False
    problems: list[str] = field(default_factory=list)


@dataclass
class Row:
    level: str
    check: str
    target: str
    detail: str


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def norm(s: str) -> str:
    return re.sub(r"/{2,}", "/", s.replace("\\", "/")).lower()


def count_words(text: str) -> int:
    return sum(1 for tok in text.split() if re.search(r"\w", tok))


def parse_batch(text: str) -> Batch:
    """Frames, cues, image lines and format deviations of batch_NNN.md (scripts/va/render.py).
    Image line = a line that starts with an absolute path; the contact sheet line starts with the
    label and never counts."""
    b = Batch()
    cur: dict | None = None
    paths: set[str] = set()

    def close() -> None:
        if cur is None:
            return
        fid, n = cur["id"], cur["images"]
        if cur["kind"] == "above":
            if n:
                b.problems.append(f"{fid}: in-batch revisit with an image line")
            return
        if n != 1:
            b.problems.append(f"{fid}: {n} image lines (expected 1)")
        elif not cur["bad"] and cur["suffix"] != cur["ref"]:
            want = f'"(revisita de {cur["ref"]})"' if cur["ref"] else "none"
            got = f'"(revisita de {cur["suffix"]})"' if cur["suffix"] else "none"
            b.problems.append(f"{fid}: image line suffix {got}, expected {want}")

    for line in text.splitlines():
        m = FRAME_HDR.match(line)
        if m:
            close()
            rest = m.group(2)
            above, rev = SAME_ABOVE.search(rest), REVISIT_HDR.search(rest)
            cur = {"id": m.group(1), "kind": "above" if above else "revisit" if rev else "plain",
                   "ref": rev.group(1) if rev and not above else None, "images": 0, "suffix": None, "bad": False}
            b.frames.append(m.group(1))
            continue
        if SHEET_LINE.match(line):
            b.sheet = True
            if cur is not None:
                b.problems.append(f"{cur['id']}: contact sheet line inside the frame list")
            continue
        if cur is None:
            continue
        c = CUE_LINE.match(line)
        if c:
            rest = CONT.sub("", TIME_RANGE.sub("", c.group(2).strip(), count=1))
            b.cues.setdefault(c.group(1), SPEAKER.sub("", rest.strip()).strip())
        elif IMG_LINE.match(line):
            b.images += 1
            cur["images"] += 1
            s = IMG_STRICT.match(line)
            if not s:
                cur["bad"] = True
                b.problems.append(f"{cur['id']}: malformed image line: {line.strip()[:90]}")
                continue
            cur["suffix"] = s.group(2)
            if s.group(1).lower() in paths:
                b.problems.append(f"{cur['id']}: image path repeated in the batch")
            paths.add(s.group(1).lower())
    close()
    return b


def tokens(text: str) -> list[str]:
    return WORD.findall(text.lower())


def longest_run(a: list[str], b: list[str]) -> int:
    """Longest run of consecutive words common to both lists."""
    best, prev = 0, [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def copied_cues(prompt: str, cues: dict[str, str], n: int = COPY_WORDS) -> list[tuple[str, int, bool]]:
    """(id, consecutive words reproduced, is a copy) of the cues with n+ consecutive words in the prompt.
    It is a copy with HARD_COPY_WORDS+ consecutive words, or with the whole cue; below that, a short quote."""
    toks = tokens(prompt)
    grams = {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}
    hits = []
    for cid, text in cues.items():
        t = tokens(text)
        if any(tuple(t[i:i + n]) in grams for i in range(len(t) - n + 1)):
            k = longest_run(t, toks)
            hits.append((cid, k, k >= HARD_COPY_WORDS or k == len(t)))
    return hits


def frame_refs(text: str) -> set[tuple[int, int]]:
    """Distinct frame references; a range F0041–F0045 counts as one."""
    refs = {(int(a), int(b)) for a, b in RANGE_RE.findall(text)}
    refs |= {(int(a), int(a)) for a in SINGLE_RE.findall(RANGE_RE.sub(" ", text))}
    return refs


def covered_numbers(text: str) -> set[int]:
    nums: set[int] = set()
    for a, b in RANGE_RE.findall(text):
        lo, hi = sorted((int(a), int(b)))
        if hi - lo <= 500:
            nums.update(range(lo, hi + 1))
    nums.update(int(a) for a in SINGLE_RE.findall(text))
    return nums


def has_plan_table(text: str) -> bool:
    lines = text.splitlines()
    return any("|" in lines[i] and SEP_ROW.match(lines[i + 1]) and re.search(r"\blotes?\b|\bbatch(?:es)?\b", lines[i], re.I)
               for i in range(len(lines) - 1))


def find_workflow_args(data):
    if isinstance(data, dict):
        vids = data.get("videos")
        if isinstance(vids, list) and vids and all(isinstance(v, dict) and "batches_dir" in v for v in vids):
            return data
        if isinstance(data.get("workflow_args"), dict):
            return find_workflow_args(data["workflow_args"])
    return None


def load_workflow_args(run_dir: Path, args_path: str | None) -> tuple[dict, Path]:
    candidates = [Path(args_path)] if args_path else [run_dir / "run.json", run_dir / "prepare.json"]
    for p in candidates:
        if not p.is_file():
            continue
        try:
            wa = find_workflow_args(json.loads(read(p)))
        except json.JSONDecodeError as e:
            raise UsageError(f"{p}: invalid JSON ({e})") from e
        if wa:
            return wa, p
    raise UsageError("workflow_args not found in " + ", ".join(str(p) for p in candidates)
                     + " (use --args <file with workflow_args>)")


def load_return(arg: str) -> dict:
    s = arg.strip()
    try:
        data = json.loads(s) if s.startswith("{") else json.loads(read(Path(s)))
    except (OSError, json.JSONDecodeError) as e:
        raise UsageError(f"--return unreadable: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
        raise UsageError("--return does not have the shape of the workflow return (videos[] missing)")
    return data


class Checker:
    def __init__(self) -> None:
        self.rows: list[Row] = []

    def add(self, ok: bool, check: str, target: str, detail: str = "") -> bool:
        self.rows.append(Row(PASS if ok else FAIL, check, target, detail))
        return bool(ok)

    def add_level(self, level: str, check: str, target: str, detail: str = "") -> None:
        self.rows.append(Row(level, check, target, detail))

    def budget(self, value: int, limit: int, check: str, target: str, unit: str) -> None:
        hard = int(limit * SOFT_FACTOR)
        level = PASS if value <= limit else AVISO if value <= hard else FAIL
        self.add_level(level, check, target, f"{value} {unit} · budget {limit} (FAIL above {hard})")

    def count(self, level: str) -> int:
        return sum(1 for r in self.rows if r.level == level)

    @property
    def failed(self) -> int:
        return self.count(FAIL)

    def table(self) -> str:
        w1 = max([len("CHECK")] + [len(r.check) for r in self.rows])
        w2 = max([len("TARGET")] + [len(r.target) for r in self.rows])
        out = [f"{'RESULT':6}  {'CHECK':{w1}}  {'TARGET':{w2}}  DETAIL"]
        out += [f"{r.level:6}  {r.check:{w1}}  {r.target:{w2}}  {r.detail}" for r in self.rows]
        out.append(f"\n{len(self.rows)} checks: {self.count(PASS)} PASS, {self.count(AVISO)} AVISO, {self.failed} FAIL")
        return "\n".join(out)


def check_video(chk: Checker, wa: dict, v: dict, rv: dict | None) -> None:
    slug = str(v.get("slug", "?"))
    bdir, out = Path(v["batches_dir"]), Path(v["out_dir"])
    images = list(v.get("batch_images") or [])
    total = len(images)
    maxb = (wa.get("options") or {}).get("max_batches") or 0
    last = min(total, int(maxb)) if int(maxb) > 0 else total
    if rv and isinstance(rv.get("batches"), dict) and isinstance(rv["batches"].get("planned"), int):
        last = rv["batches"]["planned"]
    cur = (wa.get("options") or {}).get("curation") or "batches"  # without the key: the run predates the option and had the NEXT
    plan = cur == "plan"  # without the NEXT: the INIT writes the ledger with 000 and only the prompt of batch 1

    batches: dict[int, Batch] = {}
    for n in range(1, last + 1):
        tl = bdir / f"batch_{n:03d}.md"
        tgt = f"{slug} {n:03d}"
        if not chk.add(tl.is_file(), "lote.linha_do_tempo", tgt, str(tl) if not tl.is_file() else ""):
            continue
        b = batches[n] = parse_batch(read(tl))
        want = images[n - 1] if n - 1 < len(images) else None
        chk.add(b.images == want, "lote.imagens", tgt, f"{b.images} image lines · batch_images {want} · {len(b.frames)} frames")
        probs = b.problems + ([] if b.sheet else ['missing the "Folha de contato:" line'])
        detail = "; ".join(probs[:3]) + (f" … (+{len(probs) - 3})" if len(probs) > 3 else "") if probs else "render.py format"
        chk.add(not probs, "lote.formato", tgt, detail)

    ledger = out / "ledger.md"
    if chk.add(ledger.is_file(), "ledger.existe", slug, "" if ledger.is_file() else str(ledger)):
        text = read(ledger)
        m = LEDGER_HDR.search(text)
        if not m:
            chk.add(False, "ledger.integrado", slug, 'missing "Integrado até o lote: NNN de BBB"')
        else:
            k, bb = int(m.group(1)), m.group(2)
            lo = 0 if plan else max(0, last - 1)
            ok = bb is not None and int(bb) == total and lo <= k <= last
            want = f"{lo:03d}–{last:03d} de {total:03d} (plan curation)" if plan else f"{lo:03d} de {total:03d}"
            fmt = "" if bb is not None else ' · expected format "NNN de BBB"'
            chk.add(ok, "ledger.integrado", slug, f"{m.group(0)} · expected {want}{fmt}")
        chk.budget(count_words(text), MAX_LEDGER_WORDS, "ledger.palavras", slug, "words")
        chk.add(has_plan_table(text), "ledger.plano", slug, "plan table" if has_plan_table(text) else "no table with a \"lotes\" header")

    for n in range(1, last + 1):
        tgt = f"{slug} {n:03d}"
        p = out / "prompts" / f"batch_{n:03d}.md"
        if (n == 1 or not plan or p.is_file()) and chk.add(p.is_file(), "prompt.existe", tgt, "" if p.is_file() else str(p)):
            text = read(p)
            want = norm(f"{v['batches_dir']}/batch_{n:03d}.md")
            chk.add(want in norm(text), "prompt.caminho", tgt, "cites batch_NNN.md" if want in norm(text) else f"does not cite {want}")
            if n in batches:
                hits = copied_cues(text, batches[n].cues)
                copies = [f"{cid} ({k} words)" for cid, k, hard in hits if hard]
                quotes = [f"{cid} ({k} words)" for cid, k, hard in hits if not hard]
                if copies:
                    chk.add_level(FAIL, "prompt.copia_fala", tgt, "copies " + ", ".join(copies[:6]))
                elif quotes:
                    chk.add_level(AVISO, "prompt.copia_fala", tgt, "quotes an excerpt of " + ", ".join(quotes[:6]))
                else:
                    chk.add_level(PASS, "prompt.copia_fala", tgt, f"no cue excerpt with {COPY_WORDS}+ words")
            refs = len(frame_refs(text))
            level = PASS if refs <= MAX_FRAME_REFS else AVISO if refs <= HARD_FRAME_REFS else FAIL
            chk.add_level(level, "prompt.ids", tgt, f"{refs} frame references · up to {MAX_FRAME_REFS} (FAIL above {HARD_FRAME_REFS})")
            chk.budget(count_words(text), MAX_PROMPT_WORDS, "prompt.palavras", tgt, "words")
        r = out / "reports" / f"batch_{n:03d}.md"
        if chk.add(r.is_file(), "relatorio.existe", tgt, "" if r.is_file() else str(r)) and n in batches:
            nums = covered_numbers(read(r))
            missing = [f for f in batches[n].frames if int(f[1:]) not in nums]
            detail = f"missing {', '.join(missing[:8])}{' …' if len(missing) > 8 else ''}" if missing else f"{len(batches[n].frames)} frames cited"
            chk.add(not missing, "relatorio.frames", tgt, detail)

    final = out / FINAL_MD
    if chk.add(final.is_file(), "final.existe", slug, "" if final.is_file() else str(final)):
        text = read(final)
        chk.add(bool(ANSWER.search(text)), "final.resposta", slug, '"Resposta ao objetivo"' if ANSWER.search(text) else 'missing "Resposta ao objetivo"')
        m = COVERAGE.search(text)
        if not m:
            chk.add(False, "final.cobertura", slug, 'missing "Cobertura: lotes x/B"')
        else:
            x, y = int(m.group(1)), int(m.group(2))
            chk.add(x == last and y == total, "final.cobertura", slug, f"batches {x}/{y} · expected {last}/{total}")
        frames = (rv or {}).get("frames")
        if frames and (rv.get("batches") or {}).get("from") == 1:
            fm = COV_FRAMES.search(text)
            got = f"{fm.group(1)}/{fm.group(2)}" if fm else None
            chk.add(got == frames, "final.frames", slug, f"frames {got} · return {frames}")


def check_return(chk: Checker, wa: dict, ret: dict) -> None:
    by_slug = {r.get("slug"): r for r in ret["videos"] if isinstance(r, dict)}
    for v in wa["videos"]:
        slug = str(v.get("slug"))
        rv = by_slug.get(slug)
        if not chk.add(rv is not None, "retorno.video", slug, "" if rv else "missing from the return"):
            continue
        status, frames, b = rv.get("status"), rv.get("frames"), rv.get("batches") or {}
        if status == "skipped":
            chk.add(True, "retorno.frames", slug, f"skipped ({FINAL_MD} already existed)")
        elif frames is None and isinstance(b.get("from"), int) and isinstance(b.get("planned"), int) and b["from"] > b["planned"]:
            chk.add(True, "retorno.frames", slug, "no new batch in this run (resume with the FINAL only)")
        else:
            m = re.fullmatch(r"(\d+)/(\d+)", str(frames or ""))
            chk.add(bool(m) and m.group(1) == m.group(2), "retorno.frames", slug, f"frames {frames}")
        partial_needed = isinstance(b.get("planned"), int) and isinstance(b.get("total"), int) and b["planned"] < b["total"]
        ok = status in ("ok", "partial", "skipped") and (status != "ok" or not partial_needed)
        chk.add(ok, "retorno.status", slug, f"{status}{' (limited run: expected partial)' if partial_needed else ''}")
    comb = ret.get("combined")
    if isinstance(comb, dict) and comb.get("error"):
        chk.add(False, "retorno.combinado", "run", str(comb["error"]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_run.py", description="Checks a finished run of the video-analysis skill.")
    ap.add_argument("run_dir", help="run folder (runs/<run_id>)")
    ap.add_argument("--args", dest="args_path", help="file with the workflow_args (default: <run_dir>/run.json)")
    ap.add_argument("--return", dest="ret", help="workflow return: a JSON file or the JSON in quotes")
    ns = ap.parse_args(argv)
    run_dir = Path(ns.run_dir)
    try:
        if not run_dir.is_dir():
            raise UsageError(f"run folder not found: {run_dir}")
        wa, src = load_workflow_args(run_dir, ns.args_path)
        ret = load_return(ns.ret) if ns.ret else None
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    chk = Checker()
    same = norm(str(wa.get("run_dir", ""))).rstrip("/") == norm(str(run_dir.resolve())).rstrip("/")
    chk.add(same, "args.run_dir", "run", f"args from {src.name}" if same else f"{src.name} points to {wa.get('run_dir')}")
    by_slug = {r.get("slug"): r for r in (ret or {}).get("videos", []) if isinstance(r, dict)}
    for v in wa["videos"]:
        check_video(chk, wa, v, by_slug.get(v.get("slug")))
    if len(wa["videos"]) > 1:
        comb = run_dir / FINAL_MD
        chk.add(comb.is_file(), "combinado.existe", "run", "" if comb.is_file() else str(comb))
    if ret is not None:
        check_return(chk, wa, ret)
    print(chk.table())
    return 1 if chk.failed else 0


def _utf8_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    _utf8_console()
    sys.exit(main())
