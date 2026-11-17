"""Why was this frame kept, or this sample ignored? (design-deterministic.md section 2 and 9)

    python explain.py --frames-json PATH (--frame F0042 ... | --sample 812 ... | --top-ignored 12 |
                      --smallest 10 | --kind motion) [--out DIR] [--home PATH] [--no-cache]

Replays the Analyzer over the gray analysis stream of the video in frames.json (decoded once and
cached in <workspace>/debug/calib/), with the params stored in frames.json, and writes one PNG
panel per item to <workspace>/debug/ (or --out):

    [current sample + hot/ignored mask] [reference = last kept frame] [diff: weak yellow, strong red]
    [components: counted red, cursor-like blue, noise gray]

plus a text explanation on stdout (classification, counts, cursor box, components). For a kept
frame the comparison shown is R at its captured sample (vs the previous kept frame); for an
ignored sample it is R at that sample. --top-ignored N picks the largest diffs classified as
cursor/same (likely false negatives); --smallest N the kept frames with the smallest change
(likely false positives).
"""
import sys

sys.dont_write_bytecode = True

import va  # noqa: E402  (its __init__ parses on old Pythons; the rest of the skill needs 3.10)

if va.old_python():   # one clear line instead of a SyntaxError in an import (Apple's python3 is 3.9)
    sys.exit(va.old_python())

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from va.common import read_json, setup_console, workspace_home  # noqa: E402
from va.dedup import CHANGE, Analyzer, effective_frame_params  # noqa: E402
from va.extract import gray_stream_cache  # noqa: E402
from va.probe import VideoInfo, probe_video  # noqa: E402


def _info_from_doc(doc: dict) -> VideoInfo:
    v = doc["video"]
    try:
        return probe_video(v["path"])
    except Exception:
        raise SystemExit(f"cannot open the video of this frames.json: {v['path']}")


def _replay(doc: dict, info: VideoInfo, watch: set[int], gray: np.ndarray) -> Analyzer:
    p = effective_frame_params(doc["params"], strict=False)
    an = Analyzer(p, tuple(doc["analysis_size"]))
    an.watch = set(watch)
    for g in gray:
        an.push(g)
    an.finish()
    return an


def _panel(item: dict, an: Analyzer, out_path: Path, title: str) -> list[str]:
    from PIL import Image, ImageDraw, ImageFont
    ins = item["ins"]
    cmp = an.cmp
    cur, ref, active = ins["cur"], ins["ref"], ins["active"]
    if ref is None:
        ref = np.zeros_like(cur)
    C = cmp.classify(cur, ref, active, ins["n_active"], detail=True)
    ah, aw = cur.shape
    sc = 2 if aw <= 640 else 1
    W, H = aw * sc, ah * sc

    def up(a):
        return Image.fromarray(a).resize((W, H), Image.NEAREST)

    base = np.stack([cur] * 3, -1)
    if active is not None:
        m = ~active
        base[m] = (base[m] * 0.4 + np.array([255, 0, 255]) * 0.6).astype(np.uint8)
    t1 = up(base)
    t2 = up(np.stack([ref] * 3, -1))
    d = cmp.absdiff(cur, ref)
    heat = np.zeros((ah, aw, 3), np.uint8)
    heat[..., :] = (cur // 4)[..., None]
    weak = d > cmp.t_lo
    strong = d > cmp.t_hi
    heat[weak] = (255, 220, 0)
    heat[strong] = (255, 0, 0)
    if active is not None:
        heat[~active] = (heat[~active] * 0.3).astype(np.uint8)
    t3 = up(heat)
    t4 = up(np.stack([cur // 2] * 3, -1))
    lines = [f"{title}: {C.cls} px={C.px} blobs={C.blobs} frac={C.frac:.4f} weak={C.weak} strong={C.strong}"
             f" fast={C.fast} active={ins['n_active']}/{aw * ah} hot_frac={ins['hot_frac']:.3f} hot_pairs={ins['hot_pairs']}",
             f"  cursor box w<={cmp.cw} h<={cmp.ch} px<={2 * cmp.budget:.0f}; noise: px<{cmp.min_blob} or "
             f"(strong<{cmp.min_strong} and px<{cmp.weak_only_max:.0f}); t_lo={cmp.t_lo} t_hi={cmp.t_hi}"]
    for name in ("P", "W"):
        v = ins.get(name)
        if v is not None:
            lines.append(f"  {name}: {v.cls} px={v.px} blobs={v.blobs} weak={v.weak}")
    dr4 = ImageDraw.Draw(t4)
    comps = C.comps
    if comps is not None and comps["n"]:
        order = np.argsort(-comps["px"], kind="stable")
        for rank, i in enumerate(order):
            x0, y0, x1, y1 = (int(comps[k][i]) for k in ("x0", "y0", "x1", "y1"))
            kind = "noise" if comps["noise"][i] else ("cursor" if comps["cursor"][i] else "COUNT")
            col = {"noise": (128, 128, 128), "cursor": (60, 140, 255), "COUNT": (255, 40, 40)}[kind]
            dr4.rectangle([x0 * sc, y0 * sc, x1 * sc - 1, y1 * sc - 1], outline=col, width=2 if kind == "COUNT" else 1)
            if rank < 14:
                lines.append(f"  comp {rank + 1}: {kind:6s} x={x0}..{x1} y={y0}..{y1} ({x1 - x0}x{y1 - y0}) "
                             f"px={int(comps['px'][i])} strong={int(comps['strong'][i])}")
        if comps["n"] > 14:
            lines.append(f"  ... {comps['n'] - 14} more components")
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 14)
    except OSError:
        font = ImageFont.load_default()
    pad = 4
    text_h = 18 * (len(lines) + 1)
    sheet = Image.new("RGB", (2 * W + 3 * pad, 2 * H + 3 * pad + text_h), (30, 30, 30))
    for i, t in enumerate((t1, t2, t3, t4)):
        sheet.paste(t, (pad + (i % 2) * (W + pad), pad + (i // 2) * (H + pad)))
    dr = ImageDraw.Draw(sheet)
    y = 2 * H + 3 * pad
    for ln in lines:
        dr.text((pad, y), ln, fill=(235, 235, 235), font=font)
        y += 18
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return lines


def main(argv=None) -> int:
    setup_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames-json", required=True)
    ap.add_argument("--frame", action="append", default=[], help="frame id (repeatable)")
    ap.add_argument("--sample", action="append", type=int, default=[], help="sample index (repeatable)")
    ap.add_argument("--top-ignored", type=int, default=0)
    ap.add_argument("--smallest", type=int, default=0)
    ap.add_argument("--kind", choices=["first", "settled", "motion", "heartbeat", "final"])
    ap.add_argument("--out", help="output folder (default <workspace>/debug)")
    ap.add_argument("--home")
    ap.add_argument("--no-cache", action="store_true", help="decode again instead of using debug/calib")
    a = ap.parse_args(argv)
    doc = read_json(a.frames_json)
    info = _info_from_doc(doc)
    home = workspace_home(a.home)
    out_dir = Path(a.out) if a.out else home / "debug"
    p = effective_frame_params(doc["params"], strict=False)
    if a.no_cache:
        from va.extract import decode_gray
        gray = np.stack(list(decode_gray(info.path, info, p)))
    else:
        gray = gray_stream_cache(info.path, info, p, home / "debug" / "calib", doc["video"]["key"])
    frames = {f["id"]: f for f in doc["frames"]}
    items: list[dict] = []
    for fid in a.frame:
        f = frames.get(fid.upper())
        if f is None:
            raise SystemExit(f"unknown frame {fid}")
        items.append({"label": f["id"], "k": f["sample"]})
    for k in a.sample:
        items.append({"label": f"k{k}", "k": k})
    if a.smallest:
        cand = sorted((f for f in doc["frames"][1:] if f["same_as"] is None), key=lambda f: (f["change"]["frac"], f["id"]))
        items += [{"label": f["id"], "k": f["sample"]} for f in cand[: a.smallest]]
    if a.kind:
        items += [{"label": f["id"], "k": f["sample"]} for f in doc["frames"] if f["kind"] == a.kind]
    an = None
    if a.top_ignored:
        an = _replay(doc, info, set(), gray)
        rows = [r for r in an.rows if r[11] != CHANGE]
        rows.sort(key=lambda r: (-r[21], r[0]))
        picked: list[int] = []
        for r in rows:
            if len(picked) >= a.top_ignored:
                break
            if all(abs(r[0] - k) > 2 for k in picked) and r[21] > 0:
                picked.append(r[0])
        items += [{"label": f"k{k}", "k": k} for k in picked]
    if not items:
        raise SystemExit("nothing to explain: use --frame, --sample, --top-ignored, --smallest or --kind")
    an = _replay(doc, info, {it["k"] for it in items}, gray)
    kept = [(d.n, d.sample, d.kind) for d in an.decisions]
    ref_kept = [(int(f["id"][1:]), f["sample"], f["kind"]) for f in doc["frames"]]
    if kept != ref_kept:
        print(f"warning: the replay kept {len(kept)} frames, frames.json has {len(ref_kept)} (params or decoder changed?)")
    key = doc["video"]["key"]
    for it in items:
        ins = an.inspect.get(it["k"])
        if ins is None:
            print(f"{it['label']}: sample {it['k']} not in the stream")
            continue
        it["ins"] = ins
        out = out_dir / f"explain_{key}_{it['label']}.png"
        t = doc["range"][0] + it["k"] / doc["sample_fps"]
        lines = _panel(it, an, out, f"{it['label']} k={it['k']} t={t:.1f}s")
        print("\n".join(lines))
        print(f"  panel: {out.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
