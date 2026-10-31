"""Parameter sweeps for the frame engine, replaying the Analyzer without decoding again.

    python calibrate.py VIDEO [--start T --end T] [--set k=v ...] [--grid "k=v1,v2;k2=v3,v4"]
                        [--truth truth.json] [--baseline baseline.json] [--tol 2.0]
                        [--cache-stream] [--hot-mode windowed|off|global] [--home PATH] [--json-out PATH]

- The gray analysis stream (pass A) is decoded once; with --cache-stream it is kept as a memmap in
  <workspace>/debug/calib/ and reused by later sweeps and by explain.py.
- --set / --grid take frame params (canonical names or the CLI aliases, e.g. settle=0.5,
  t_lo=14, sensitivity=high, ignore_regions=0.8,0.75,0.2,0.25). Values are parsed as JSON when
  possible ("true", "[0.8,0.7,0.2,0.3]"), else kept as strings.
- --truth: a synthetic truth file (tests/synth.py): events found / missed / extra (+-tol).
- --baseline: tests/data/baseline/da_1608.json (recall of the frames marked sig) or a JSON list
  of times, or another frames.json (its captured_at times).
- --hot-mode global: the September baseline mask (cells active in > 15 % of all pairs, dilated
  1 cell) used as a static mask with the windowed mask off, for an A/B against windowed.

Prints one line per combination (frames, unique, kinds, frames/min, busiest minute, truth or
baseline scores) and writes all results as JSON with --json-out.
"""
import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import itertools  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402

import numpy as np  # noqa: E402

from va.common import as_posix, read_json, resolve_unicode_path, setup_console, slugify, workspace_home, write_json  # noqa: E402
from va.dedup import Analyzer, Comparator, _dilate8, density_report, effective_frame_params, match_events  # noqa: E402
from va.extract import analysis_size, decode_gray, frame_range_ms, gray_stream_cache  # noqa: E402
from va.probe import probe_video  # noqa: E402


def _value(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_sets(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--set expects k=v, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = _value(v.strip())
    return out


def parse_grid(text: str | None) -> list[dict]:
    if not text:
        return [{}]
    axes = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        k, vals = part.split("=", 1)
        k = k.strip()
        if k in ("ignore_regions", "ignore_region"):
            values = [_value(v) for v in vals.split("|")]
        else:
            values = [_value(v.strip()) for v in vals.split(",")]
        axes.append([(k, v) for v in values])
    return [dict(c) for c in itertools.product(*axes)]


def global_mask(gray, p: dict, aw: int, ah: int) -> np.ndarray:
    """Baseline-style global hot mask: cells active in > 15 % of all pairs, dilated 1 cell."""
    cmp = Comparator(p, aw, ah)
    count = np.zeros((cmp.gh, cmp.gw), np.int32)
    n = 0
    prev = None
    for g in gray:
        if prev is not None:
            cells = cmp.pair_cells(g, prev)[0]
            count += cells
            n += 1
        prev = g
    hot = count > 0.15 * max(n, 1)
    hot = _dilate8(hot, 1)
    g8 = cmp.cell
    return np.repeat(np.repeat(hot, g8, axis=0), g8, axis=1)


def load_baseline(path: str) -> tuple[list[float], str]:
    doc = read_json(path)
    if isinstance(doc, list):
        return [float(t) for t in doc], "times"
    if isinstance(doc, dict) and doc.get("schema") == "va.frames/1":
        return [f["captured_at"] for f in doc["frames"]], "frames.json"
    if isinstance(doc, dict) and "frames" in doc:
        sig = [f["t"] for f in doc["frames"] if f.get("sig", True)]
        return sig, f"baseline sig ({len(sig)} of {len(doc['frames'])})"
    raise SystemExit(f"unknown baseline format: {path}")


def recall_within(frames: list[tuple[float, float]], times: list[float], tol: float) -> tuple[float, list[float]]:
    miss = [t for t in times if not any(abs(s - t) <= tol or abs(c - t) <= tol for s, c in frames)]
    return (1 - len(miss) / len(times)) if times else 1.0, miss


def main(argv=None) -> int:
    setup_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--start", type=float)
    ap.add_argument("--end", type=float)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--grid")
    ap.add_argument("--truth")
    ap.add_argument("--baseline")
    ap.add_argument("--tol", type=float, default=None, help="matching tolerance in s (truth: its tol; baseline: 2.0)")
    ap.add_argument("--cache-stream", action="store_true")
    ap.add_argument("--hot-mode", choices=["windowed", "off", "global"])
    ap.add_argument("--home")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)

    src = resolve_unicode_path(a.video)
    info = probe_video(src)
    base = parse_sets(a.set)
    if a.start is not None or a.end is not None:
        base["range"] = [a.start or 0.0, a.end]
    if a.hot_mode:
        base["hot_mode"] = a.hot_mode
    combos = parse_grid(a.grid)
    home = workspace_home(a.home)
    key = slugify(src.stem)
    truth = read_json(a.truth) if a.truth else None
    baseline, btag = load_baseline(a.baseline) if a.baseline else (None, "")
    results = []
    streams: dict[str, object] = {}
    print(f"{as_posix(src)}  {info.width}x{info.height} {info.fps_avg} fps  {info.duration_s:.1f} s  "
          f"{len(combos)} combination(s)" + (f"  baseline: {btag}" if baseline else ""))
    for combo in combos:
        over = {**base, **combo}
        mode = over.get("hot_mode", "windowed")
        p = effective_frame_params({**over, "hot_mode": "off" if mode == "global" else mode})
        aw, ah = analysis_size(info, p)
        skey = json.dumps([p["sample_fps"], p["range"], aw, ah, p["analysis_scale_flags"], p["skip_noref"]])
        t0 = time.perf_counter()
        if skey not in streams:
            if a.cache_stream:
                streams[skey] = gray_stream_cache(src, info, p, home / "debug" / "calib", key)
            else:
                streams[skey] = np.stack(list(decode_gray(src, info, p)))
        gray = streams[skey]
        t_dec = time.perf_counter() - t0
        static = global_mask(gray, p, aw, ah) if mode == "global" else None
        t1 = time.perf_counter()
        an = Analyzer(p, (aw, ah), static_mask=static, record_rows=False)
        for g in gray:
            an.push(g)
        an.finish()
        t_an = time.perf_counter() - t1
        r0, r1 = frame_range_ms(info, p)
        caps = [an.ms(d.sample) for d in an.decisions]
        starts = [0 if d.n == 1 else an.ms(d.start_sample) for d in an.decisions]
        pairs = [((r0 + s) / 1000.0, (r0 + c) / 1000.0) for s, c in zip(starts, caps)]
        dens = density_report([r0 + c for c in caps], (r0, r1))
        res = {"params": combo, "hot_mode": mode, "n_frames": len(an.decisions),
               "n_unique": sum(1 for d in an.decisions if d.same_as is None),
               "kinds": dict(Counter(d.kind for d in an.decisions)), "frames_per_min": dens["frames_per_min"],
               "max_per_min": dens["max_per_min"], "n_samples": an.n, "decode_s": round(t_dec, 1),
               "analyze_s": round(t_an, 1), "captured": [round(c, 3) for _s, c in pairs]}
        line = (f"{json.dumps(combo, ensure_ascii=False):40s} {mode:8s} n={res['n_frames']:4d} uniq={res['n_unique']:4d} "
                f"fpm={res['frames_per_min']:5.1f} max={res['max_per_min']:3d} {res['kinds']}")
        if truth:
            tol = a.tol if a.tol is not None else float(truth.get("tol", 1.0))
            m = match_events(pairs, truth.get("events", []), tol)
            res["truth"] = {"missed": m["missed"], "extra": m["extra"], "recall": m["recall"]}
            line += f" | truth recall={m['recall']:.2f} missed={m['missed']} extra={len(m['extra'])}"
        if baseline is not None:
            tol = a.tol if a.tol is not None else 2.0
            rec, miss = recall_within(pairs, baseline, tol)
            res["baseline"] = {"recall": round(rec, 4), "missed": miss, "n": len(baseline)}
            line += f" | baseline recall={rec:.3f} missed={len(miss)}"
        print(line, flush=True)
        results.append(res)
    if a.json_out:
        write_json(a.json_out, {"video": as_posix(src), "base": base, "results": results})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
