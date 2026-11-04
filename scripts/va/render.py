"""Batches stage: alignment, batch plan and the LLM-facing files (spec sections 5-6, WP2).

``run_batches_stage(frames, cues, params, out_dir, meta)`` writes into ``out_dir`` (the
batches-<h8> folder): batch_NNN.md, batch_NNN.json, sheet_NNN.jpg, overview.jpg, batches.json,
timeline.json, transcript.md and summary.json (last), and returns
``{"batches": <batches.json dict>, "summary": <summary.json dict>}``. ``stage.json`` belongs to
the cache module.

Contract kept by batch_NNN.md (the analyst opens exactly the image path lines):
- an image path line holds only an absolute forward-slash path to a JPEG, optionally followed by
  " (revisita de Fxxxx)" when the revisited original is outside this batch. This marker is the
  same in every language (REVISIT_MARK), so the roles and parsers need a single pattern;
- a revisit whose image is already shown in the batch has no path line: its header ends with
  " · = Fxxxx (mesma tela, acima)" (localized); the same path never appears twice in one batch;
- the contact sheet line is "Folha de contato: <path>" (localized label), never a bare path;
- n_images (batches.json, batch_NNN.json, summary.json) = number of image path lines.
Other labels are pt-BR when the language is pt, else English. Output is deterministic.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import NAMES, RENDER_VERSION, SCHEMA_IDS
from .align import align, frames_in_range
from .batching import batch_cues, effective_batch_params, image_tokens, plan_batches, text_tokens
from .common import as_posix, batch_filename, batch_id, fmt_hms, ms_to_s, s_to_ms, write_json, write_text
from .contracts import FRAME_KINDS
from .sheets import make_contact_sheet, make_overview, short_time

__all__ = ["LABELS", "REVISIT_MARK", "label_language", "run_batches_stage"]

# Suffix of an image path line whose original is in an earlier batch: " (revisita de F0012)".
# Language-independent on purpose (the analyst role and tests/check_run.py match it literally).
REVISIT_MARK = "revisita de"

LABELS = {
    "pt": {
        "batch": "Lote", "of": "de", "video": "Vídeo", "speakers": "Falantes", "sheet": "Folha de contato",
        "prev": "Anterior", "next": "Próximo", "none": "—",
        "reading": ("Leitura: frame vale de início a fim; img = instante da imagem; fala completa na 1ª vez no "
                    "lote, depois ↳ id; ≈ horário aproximado; (cont.) começou antes do lote."),
        "motion": "movimento", "same_above": "mesma tela, acima",
        "no_speech": "(sem fala)", "no_labels": "(legenda sem rótulos de falante)",
        "no_speech_batch": "(sem fala no lote)", "range": "trecho", "cont": "(cont.)",
        "transcript": "Transcrição", "duration": "Duração", "cue_one": "fala", "cue_many": "falas",
        "no_subtitles": "(sem legenda)",
    },
    "en": {
        "batch": "Batch", "of": "of", "video": "Video", "speakers": "Speakers", "sheet": "Contact sheet",
        "prev": "Previous", "next": "Next", "none": "—",
        "reading": ("Reading: a frame holds from start to end; img = image instant; a cue is printed in full "
                    "the 1st time in the batch, then ↳ id; ≈ approximate time; (cont.) began before the batch."),
        "motion": "motion", "same_above": "same screen, above",
        "no_speech": "(no speech)", "no_labels": "(subtitles without speaker labels)",
        "no_speech_batch": "(no speech in this batch)", "range": "range", "cont": "(cont.)",
        "transcript": "Transcript", "duration": "Duration", "cue_one": "cue", "cue_many": "cues",
        "no_subtitles": "(no subtitles)",
    },
}

_STALE_RE = re.compile(r"^(?:batch_(\d{3,})\.(?:md|json)|sheet_(\d{3,})\.jpg)$")


@dataclass
class _Frame:
    id: str
    path: str          # absolute forward-slash path of the image (a revisit points to its original's file)
    start: int         # ms
    end: int
    cap: int
    kind: str
    same_as: str | None
    frac: float
    tokens: int


@dataclass
class _Cue:
    id: str
    start: int
    end: int
    spk: str | None
    text: str
    approx: bool


def label_language(meta: dict | None, cues: dict | None) -> str:
    """"pt" when the language (meta["language"] override, else the cues' detected language)
    is Portuguese, else "en"."""
    lang = (meta or {}).get("language") or ((cues or {}).get("vtt") or {}).get("language") or "und"
    return "pt" if str(lang).strip().lower().startswith("pt") else "en"


def _abs_posix(p: Any) -> str | None:
    if p is None or p == "":
        return None
    return as_posix(os.path.abspath(os.fspath(p)))


def _tag_key(tag: str) -> tuple[int, int]:
    return (1, 0) if tag == "S?" else (0, int(tag[1:]) if tag[1:].isdigit() else 0)


def _hms(ms: int, dec: int = 1) -> str:
    return fmt_hms(ms / 1000.0, dec)


def _range_text(a: int, b: int) -> str:
    return f"{_hms(a)}–{_hms(b)}"


def _load_frames(frames: dict, frames_dir: str, p: dict) -> tuple[list[_Frame], list[float], list[int]]:
    """-> (selected frames, effective range [s, s], selected indexes in frames.json)."""
    items = frames.get("frames") or []
    if not items:
        raise ValueError("frames.json has no frames")
    out_size = frames.get("output_size") or [0, 0]
    default_tokens = image_tokens(out_size[0], out_size[1], p["image_token_px"]) if out_size[0] and out_size[1] else 0
    bounds = [(s_to_ms(f["start"]), s_to_ms(f["end"])) for f in items]
    rng = frames.get("range") or [ms_to_s(bounds[0][0]), ms_to_s(bounds[-1][1])]
    if p["range"] is None:
        selected = list(range(len(items)))
        eff = [ms_to_s(s_to_ms(rng[0])), ms_to_s(s_to_ms(rng[1]))]
    else:
        t0, t1 = s_to_ms(p["range"][0]), s_to_ms(p["range"][1])
        selected = frames_in_range(bounds, t0, t1)
        if not selected:
            raise ValueError(f"range {p['range']} has no frames (video range {rng})")
        eff = [ms_to_s(max(t0, s_to_ms(rng[0]))), ms_to_s(min(t1, s_to_ms(rng[1])))]
    out: list[_Frame] = []
    for i in selected:
        f = items[i]
        change = f.get("change") or {}
        tokens = f.get("tokens")
        out.append(_Frame(
            id=f["id"], path=f"{frames_dir}/{f['file']}", start=bounds[i][0], end=bounds[i][1],
            cap=s_to_ms(f.get("captured_at", f["start"])), kind=f.get("kind") or "",
            same_as=f.get("same_as"), frac=float(change.get("frac") or 0.0),
            tokens=int(tokens) if isinstance(tokens, int) and not isinstance(tokens, bool) else default_tokens))
    return out, eff, selected


def _load_cues(cues: dict | None) -> list[_Cue]:
    return [_Cue(c["id"], s_to_ms(c["start"]), s_to_ms(c["end"]), c.get("spk"), c["text"], bool(c.get("approx")))
            for c in ((cues or {}).get("cues") or [])]


class _Ctx:
    """Everything the renderers share for one run of the stage."""

    def __init__(self, frames: dict, cues: dict | None, p: dict, out_dir: Path, meta: dict):
        self.p = p
        self.meta = meta
        self.out_dir = out_dir
        self.out_posix = as_posix(out_dir)
        self.lang = label_language(meta, cues)
        self.L = LABELS[self.lang]
        video = frames.get("video") or {}
        self.video = video
        self.title = meta.get("title") or video.get("title") or ""
        self.duration_ms = s_to_ms(video.get("duration_s") or (frames.get("range") or [0.0, 0.0])[1] or 0.0)
        self.output_size = frames.get("output_size") or []
        frames_dir = _abs_posix(meta.get("frames_dir") or frames.get("frames_dir"))
        if not frames_dir:
            raise ValueError("frames_dir missing from meta and frames.json")
        self.frames_dir = frames_dir.rstrip("/")
        self.F, self.range, self.selected = _load_frames(frames, self.frames_dir, p)
        self.C = _load_cues(cues)
        self.speaker_names = {s["tag"]: s["name"] for s in ((cues or {}).get("speakers") or [])}
        self.frame_cues, self.cue_frames = align([(f.start, f.end) for f in self.F], [(c.start, c.end) for c in self.C])
        self.used = [j for j, fr in enumerate(self.cue_frames) if fr]
        self.plan = plan_batches([(f.start, f.end, f.frac) for f in self.F],
                                 [(c.start, c.end, len(c.text)) for c in self.C], self.frame_cues, p)
        self.batch_of = [0] * len(self.F)
        for k, (i0, i1) in enumerate(self.plan):
            for i in range(i0, i1):
                self.batch_of[i] = k
        aspect = self.output_size if len(self.output_size) == 2 and all(self.output_size) else None
        self.aspect = aspect or [16, 9]
        self.trecho = p["range"] is not None

    def file(self, kind: str, n: int) -> Path:
        return self.out_dir / batch_filename(kind, n)

    def bounds(self, k: int) -> tuple[int, int]:
        i0, i1 = self.plan[k]
        return self.F[i0].start, self.F[i1 - 1].end


# --- batch_NNN.md / .json ------------------------------------------------------------------------

def _speaker_legend(ctx: _Ctx, cue_idx: list[int]) -> tuple[str, list[dict]]:
    tags = sorted({ctx.C[j].spk for j in cue_idx if ctx.C[j].spk}, key=_tag_key)
    speakers = [{"tag": t, "name": ctx.speaker_names.get(t, t)} for t in tags]
    if speakers:
        return " · ".join(f"{s['tag']} = {s['name']}" for s in speakers), speakers
    return (ctx.L["no_labels"] if cue_idx else ctx.L["no_speech_batch"]), speakers


def _render_batch(ctx: _Ctx, k: int, sheet: dict) -> tuple[str, dict, dict]:
    """-> (markdown, batch_NNN.json document, index entry)."""
    L, p = ctx.L, ctx.p
    n, nb = k + 1, len(ctx.plan)
    i0, i1 = ctx.plan[k]
    bid = batch_id(n)
    t0, t1 = ctx.bounds(k)
    cue_idx = batch_cues(i0, i1, ctx.frame_cues)
    chars = sum(len(ctx.C[j].text) for j in cue_idx)
    legend, speakers = _speaker_legend(ctx, cue_idx)

    def neighbor(m: int) -> tuple[str, dict | None]:
        if not 0 <= m < nb:
            return L["none"], None
        a, b = ctx.bounds(m)
        return f"{batch_id(m + 1)} ({_range_text(a, b)})", {"id": batch_id(m + 1), "t": [ms_to_s(a), ms_to_s(b)]}

    prev_txt, prev_doc = neighbor(k - 1)
    next_txt, next_doc = neighbor(k + 1)
    video_line = f"{L['video']}: {ctx.title} · {_hms(ctx.duration_ms)}"
    if len(ctx.output_size) == 2:
        video_line += f" · {ctx.output_size[0]}×{ctx.output_size[1]}"
    if ctx.trecho:
        video_line += f" · {L['range']} {_range_text(s_to_ms(ctx.range[0]), s_to_ms(ctx.range[1]))}"
    lines = [
        f"# {L['batch']} {bid} {L['of']} {nb} · {ctx.F[i0].id}–{ctx.F[i1 - 1].id} · {_range_text(t0, t1)}",
        video_line,
        f"{L['speakers']}: {legend}",
        f"{L['sheet']}: {sheet['path']}",
        f"{L['prev']}: {prev_txt} · {L['next']}: {next_txt}",
        L["reading"],
    ]
    shown: dict[str, str] = {}
    printed: set[int] = set()
    n_images = 0
    img_tokens = 0
    frame_docs = []
    for i in range(i0, i1):
        f = ctx.F[i]
        header = f"### {f.id} · {_range_text(f.start, f.end)} · img {_hms(f.cap)}"
        if f.kind == "motion":
            header += f" · {L['motion']}"
        path_line = None
        if f.path in shown:
            header += f" · = {shown[f.path]} ({L['same_above']})"
        else:
            shown[f.path] = f.id
            if f.same_as:
                header += f" · = {f.same_as}"
                path_line = f"{f.path} ({REVISIT_MARK} {f.same_as})"
            else:
                path_line = f.path
        lines += ["", header]
        if path_line:
            lines.append(path_line)
            n_images += 1
            img_tokens += f.tokens
        new_cues = []
        if not ctx.frame_cues[i]:
            lines.append(f"- {L['no_speech']}")
        for j in ctx.frame_cues[i]:
            c = ctx.C[j]
            if j in printed:
                lines.append(f"- ↳ {c.id}")
                continue
            printed.add(j)
            new_cues.append(c.id)
            cont = f" {L['cont']}" if c.start < t0 else ""
            spk = f" {c.spk}:" if c.spk else ""
            lines.append(f"- {c.id} {'≈' if c.approx else ''}{_range_text(c.start, c.end)}{cont}{spk} {c.text}")
        frame_docs.append({"id": f.id, "path": f.path, "start": ms_to_s(f.start), "end": ms_to_s(f.end),
                           "captured_at": ms_to_s(f.cap), "kind": f.kind, "same_as": f.same_as,
                           "cues": [ctx.C[j].id for j in ctx.frame_cues[i]], "new_cues": new_cues})
    entry = {
        "n": n, "id": bid, "frames": [ctx.F[i0].id, ctx.F[i1 - 1].id], "t": [ms_to_s(t0), ms_to_s(t1)],
        "n_frames": i1 - i0, "n_images": n_images,
        "cues": [ctx.C[cue_idx[0]].id, ctx.C[cue_idx[-1]].id] if cue_idx else [],
        "chars": chars, "over_budget": chars > p["max_text_chars"],
        "est_tokens": {"images": img_tokens, "text": text_tokens(chars, p["text_chars_per_token"]), "sheet": sheet["tokens"]},
        "md": as_posix(ctx.file("batch_md", n)), "json": as_posix(ctx.file("batch_json", n)), "sheet": sheet["path"],
    }
    doc = {
        "schema": SCHEMA_IDS["batch"],
        "video": {"title": ctx.title, "key": ctx.meta.get("video_key") or ctx.video.get("key") or "",
                  "duration_s": ms_to_s(ctx.duration_ms)},
        "batch": entry, "prev": prev_doc, "next": next_doc, "speakers": speakers, "frames": frame_docs,
        "cues": [{"id": ctx.C[j].id, "start": ms_to_s(ctx.C[j].start), "end": ms_to_s(ctx.C[j].end),
                  "spk": ctx.C[j].spk, "text": ctx.C[j].text, "approx": ctx.C[j].approx, "cont": ctx.C[j].start < t0}
                 for j in cue_idx],
    }
    return "\n".join(lines) + "\n", doc, entry


def _sheet_tiles(ctx: _Ctx, k: int) -> list[dict]:
    i0, i1 = ctx.plan[k]
    tiles = []
    for f in ctx.F[i0:i1]:
        label = f"{f.id} {short_time(f.cap)}"
        if f.kind == "motion":
            label += " M"
        if f.same_as:
            label += f" ={f.same_as}"
        tiles.append({"path": f.path, "label": label, "id": f.id})
    return tiles


# --- transcript.md ---------------------------------------------------------------------------------

def _paragraphs(ctx: _Ctx) -> list[list[int]]:
    """Consecutive cues of the same speaker, gap <= transcript_gap_s, up to transcript_max_chars
    characters and transcript_max_s seconds per paragraph."""
    p = ctx.p
    gap, max_chars, max_ms = s_to_ms(p["transcript_gap_s"]), p["transcript_max_chars"], s_to_ms(p["transcript_max_s"])
    paras: list[list[int]] = []
    spk, start, last_end, length = None, 0, 0, 0
    for j in ctx.used:
        c = ctx.C[j]
        if (paras and c.spk == spk and c.start - last_end <= gap and length + 1 + len(c.text) <= max_chars
                and c.end - start <= max_ms):
            paras[-1].append(j)
            last_end = max(last_end, c.end)
            length += 1 + len(c.text)
            continue
        paras.append([j])
        spk, start, last_end, length = c.spk, c.start, c.end, len(c.text)
    return paras


def _render_transcript(ctx: _Ctx) -> str:
    L = ctx.L
    used = ctx.used
    count = f"{len(used)} {L['cue_one'] if len(used) == 1 else L['cue_many']}"
    info = [f"{L['duration']} {_hms(ctx.duration_ms)}"]
    if ctx.trecho:
        info.append(f"{L['range']} {_range_text(s_to_ms(ctx.range[0]), s_to_ms(ctx.range[1]))}")
    info.append(count)
    talk: dict[str, int] = {}
    for j in used:
        c = ctx.C[j]
        if c.spk:
            talk[c.spk] = talk.get(c.spk, 0) + (c.end - c.start)
    total = sum(talk.values())
    if talk:
        order = sorted(talk, key=lambda t: (-talk[t], _tag_key(t)))
        pct = {t: (200 * talk[t] + total) // (2 * total) if total else 0 for t in order}
        info.append(f"{L['speakers']}: " + " · ".join(f"{t} = {ctx.speaker_names.get(t, t)} ({pct[t]}%)" for t in order))
    lines = [f"# {L['transcript']} · {ctx.title}", " · ".join(info), ""]
    if not used:
        lines.append(L["no_subtitles"])
    for para in _paragraphs(ctx):
        first, last = ctx.C[para[0]], ctx.C[para[-1]]
        f0 = min(ctx.cue_frames[j][0] for j in para)
        f1 = max(ctx.cue_frames[j][-1] for j in para)
        frames_txt = ctx.F[f0].id if f0 == f1 else f"{ctx.F[f0].id}–{ctx.F[f1].id}"
        b0, b1 = ctx.batch_of[f0], ctx.batch_of[f1]
        batch_txt = batch_id(b0 + 1) if b0 == b1 else f"{batch_id(b0 + 1)}–{batch_id(b1 + 1)}"
        ids = first.id if len(para) == 1 else f"{first.id}–{last.id}"
        spk = f"{first.spk}: " if first.spk else ""
        text = " ".join(ctx.C[j].text for j in para)
        lines.append(f"[{'≈' if first.approx else ''}{_hms(first.start)} · {frames_txt} · {batch_txt}] {spk}{text} ({ids})")
    return "\n".join(lines) + "\n"


# --- stage ------------------------------------------------------------------------------------------

def _dedup(items: list[str]) -> list[str]:
    return list(dict.fromkeys(str(x) for x in items if x))


def _remove_stale(out_dir: Path, n_batches: int) -> None:
    try:
        names = os.listdir(out_dir)
    except OSError:
        return
    for name in names:
        m = _STALE_RE.match(name)
        if m and int(m.group(1) or m.group(2)) > n_batches:
            try:
                os.remove(out_dir / name)
            except OSError:
                pass


def run_batches_stage(frames: dict, cues: dict, params: dict, out_dir: Path, meta: dict) -> dict:
    """Plan the batches and write every file of the batches stage into ``out_dir``.

    ``params``: batch params (partial is fine; completed by effective_batch_params). With
    ``"range": [t0, t1]`` (a segment) only the frames overlapping it are batched; batch numbers
    restart at 001 and frame ids are kept. ``meta``: {title, video_path, vtt_path, video_key,
    frames_dir, frames_json, cues_json, language (optional label-language override)}; missing
    entries fall back to frames.json / cues.json.
    Returns {"batches": <batches.json dict>, "summary": <summary.json dict>}.
    """
    p = effective_batch_params(params)
    meta = dict(meta or {})
    out_dir = Path(os.path.abspath(os.fspath(out_dir)))
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = _Ctx(frames, cues, p, out_dir, meta)
    warnings: list[str] = list(frames.get("warnings") or []) + list((cues or {}).get("warnings") or [])

    entries = []
    sheet_tokens = 0
    missing: list[str] = []
    for k in range(len(ctx.plan)):
        sheet = make_contact_sheet(_sheet_tiles(ctx, k), ctx.file("sheet", k + 1), ctx.aspect, p)
        missing += [m for m in sheet["missing"] if m not in missing]
        md, doc, entry = _render_batch(ctx, k, sheet)
        write_text(ctx.file("batch_md", k + 1), md)
        write_json(ctx.file("batch_json", k + 1), doc)
        entries.append(entry)
        sheet_tokens += sheet["tokens"]
        if entry["over_budget"]:
            msg = f"batches: {entry['id']} has {entry['chars']} characters of speech (limit {p['max_text_chars']})"
            if entry["n_frames"] == 1:
                msg += f"; frame {entry['frames'][0]} alone exceeds the limit"
            warnings.append(msg)
    overview_tiles = [{"path": ctx.F[i0].path, "label": f"{batch_id(k + 1)} · {_hms(ctx.F[i0].start, 0)}",
                       "id": batch_id(k + 1)} for k, (i0, _i1) in enumerate(ctx.plan)]
    overview = make_overview(overview_tiles, out_dir / NAMES["overview"], ctx.aspect, p)
    if missing:
        warnings.append(f"batches: {len(missing)} frame image(s) not found for the contact sheets "
                        f"(e.g. {', '.join(missing[:3])})")

    timeline = {
        "schema": SCHEMA_IDS["timeline"],
        "frames": [{"id": f.id, "start": ms_to_s(f.start), "end": ms_to_s(f.end),
                    "cues": [ctx.C[j].id for j in ctx.frame_cues[i]]} for i, f in enumerate(ctx.F)],
        "cue_frames": {ctx.C[j].id: [ctx.F[i].id for i in ctx.cue_frames[j]] for j in ctx.used},
        "batches": [{"id": e["id"], "frames": list(e["frames"])} for e in entries],
    }
    write_json(out_dir / NAMES["timeline"], timeline)
    write_text(out_dir / NAMES["transcript"], _render_transcript(ctx))

    batches_doc = {"schema": SCHEMA_IDS["batches"], "render_version": RENDER_VERSION, "language": ctx.lang,
                   "n_batches": len(entries), "params": p, "batches": entries}
    write_json(out_dir / NAMES["batches_json"], batches_doc)

    video = ctx.video
    kinds = {k: 0 for k in FRAME_KINDS}
    for f in ctx.F:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    span_ms = s_to_ms(ctx.range[1]) - s_to_ms(ctx.range[0])
    spk_used = {ctx.C[j].spk for j in ctx.used if ctx.C[j].spk}
    frames_json = _abs_posix(meta.get("frames_json")) or f"{ctx.frames_dir}/{NAMES['frames_json']}"
    summary = {
        "schema": SCHEMA_IDS["summary"],
        "title": ctx.title,
        "video_path": _abs_posix(meta.get("video_path") or video.get("path")) or "",
        "vtt_path": _abs_posix(meta.get("vtt_path") or ((cues or {}).get("vtt") or {}).get("path")) or "",
        "video_key": meta.get("video_key") or video.get("key") or "",
        "duration_s": ms_to_s(ctx.duration_ms),
        "range": list(ctx.range),
        "resolution": [video.get("width") or 0, video.get("height") or 0],
        "fps_avg": video.get("fps_avg") or 0.0,
        "vfr": bool(video.get("vfr")),
        "output_size": list(ctx.output_size),
        "sample_fps": frames.get("sample_fps") or 0.0,
        "n_samples": frames.get("n_samples") or 0,
        "n_frames": len(ctx.F),
        "n_unique": sum(1 for f in ctx.F if not f.same_as),
        "frames_per_min": round(len(ctx.F) * 60000.0 / span_ms, 1) if span_ms > 0 else 0.0,
        "kinds": kinds,
        "n_cues": len(ctx.used),
        "n_speakers": len(spk_used),
        "language": str(meta.get("language") or ((cues or {}).get("vtt") or {}).get("language") or "und"),
        "cue_style": ((cues or {}).get("vtt") or {}).get("style") or "plain",
        "n_batches": len(entries),
        "batches": [[e["n"], e["frames"][0], e["frames"][1], e["t"][0], e["t"][1], e["n_frames"], e["n_images"]]
                    for e in entries],
        "est_tokens": {"frames": sum(e["est_tokens"]["images"] for e in entries),
                       "sheets": sheet_tokens + overview["tokens"],
                       "text": sum(e["est_tokens"]["text"] for e in entries)},
        "paths": {"frames_dir": ctx.frames_dir, "batches_dir": ctx.out_posix, "frames_json": frames_json,
                  "cues_json": _abs_posix(meta.get("cues_json")) or "",
                  "timeline_json": as_posix(out_dir / NAMES["timeline"]),
                  "transcript_md": as_posix(out_dir / NAMES["transcript"]),
                  "batches_json": as_posix(out_dir / NAMES["batches_json"])},
        "warnings": _dedup(warnings),
    }
    write_json(out_dir / NAMES["summary"], summary)
    _remove_stale(out_dir, len(entries))
    return {"batches": batches_doc, "summary": summary}
