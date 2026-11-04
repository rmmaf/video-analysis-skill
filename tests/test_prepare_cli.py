"""prepare.py end to end with simulated stages: envelope, exit codes, request consumption, run
scaffolding, cache hits, estimate, resume (DL-11), size guard (DL-12), roles/env, trecho, gc, and a
real spawn ProcessPool run (--jobs 2) in a subprocess.

The fake stage functions below are driven by a JSON spec written inside each fake "video" file:
{"duration_s", "n_frames", "revisit_every", "fail": "probe|frames|batches", "interrupt"}.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

import prepare
from va import ambiente, cache, contracts
from va.common import as_posix, read_json, write_json

NFD = lambda s: unicodedata.normalize("NFD", s)  # noqa: E731
TEAMS = "Repasse - Fluxo de Carga dos DA’s-20260818_160841-Gravação de Reunião"
STAGE_NAMES = ("probe_video", "effective_frame_params", "run_frames_stage", "estimate_frames_seconds",
               "effective_cue_params", "run_cues_stage", "effective_batch_params", "run_batches_stage")
ENVELOPE_KEYS = ["ok", "mode", "version", "home", "run_id", "run_dir", "json_path", "videos", "estimate", "env",
                 "script_path", "workflow_args", "errors", "warnings"]

FAKE_SRC = r'''
"""Fake stage functions for the prepare.py tests (driven by a JSON spec inside the fake video)."""
import json
import os
from pathlib import Path

JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


def _spec(path):
    try:
        text = Path(path).read_text(encoding="utf-8")
        return json.loads(text) if text.lstrip().startswith("{") else {}
    except (OSError, ValueError):
        return {}


def _log(stage, out_dir):
    target = os.environ.get("VA_FAKE_CALLS")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"{stage} {Path(out_dir).parent.name} {Path(out_dir).name}\n")


def _w(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def probe_video(path):
    spec = _spec(path)
    if spec.get("fail") == "probe":
        raise RuntimeError("arquivo corrompido")
    return {"path": str(path).replace("\\", "/"), "duration_s": float(spec.get("duration_s", 300.0)),
            "width": 1920, "height": 1080, "fps_avg": 16.0, "vfr": False, "codec": "h264", "has_b_frames": False}


def effective_frame_params(overrides=None):
    p = {"sample_fps": 2.0, "sensitivity": "normal", "ignore_regions": [], "range": None, "max_edge": 1568}
    for k, v in dict(overrides or {}).items():
        if k == "sensitivity" and v not in ("low", "normal", "high"):
            raise ValueError(f"sensitivity inválida: {v}")
        p[k] = v
    return p


def effective_cue_params(overrides=None):
    return {"offset_s": 0.0, "speaker_names": {}, **dict(overrides or {})}


def effective_batch_params(overrides=None):
    p = {"max_frames": 20, "range": None}
    for k, v in dict(overrides or {}).items():
        if k not in ("max_frames", "range", "max_text_chars", "max_span_s", "soft_cuts"):
            raise ValueError(f"unknown batch parameter {k!r}")
        p[k] = v
    return p


def estimate_frames_seconds(info, params=None):
    return round(0.06 * float(info["duration_s"]) + 2.0, 1)


def run_cues_stage(vtt_path, params, out_dir, duration_s):
    _log("cues", out_dir)
    doc = {"schema": "va.cues/1", "vtt": {"path": str(vtt_path).replace("\\", "/"), "language": "pt", "style": "plain"},
           "params": params, "duration_s": duration_s,
           "cues": [{"id": "C0001", "start": 1.0, "end": 2.0, "spk": None, "text": "olá"}]}
    _w(Path(out_dir) / "cues.json", doc)
    return doc


def run_frames_stage(src, info, params, out_dir, progress=None):
    spec = _spec(src)
    if spec.get("fail") == "frames":
        raise RuntimeError("decodificação falhou")
    if spec.get("interrupt"):
        raise KeyboardInterrupt
    _log("frames", out_dir)
    n = int(spec.get("n_frames", 45))
    dur = float(info["duration_s"])
    rev = int(spec.get("revisit_every", 0))
    frames, files, last_unique = [], {}, None
    for i in range(n):
        start = round(i * dur / n, 3)
        end = round((i + 1) * dur / n, 3) if i + 1 < n else dur
        fid = f"F{i + 1:04d}"
        if rev and (i + 1) % rev == 0 and last_unique is not None:
            same, name = last_unique, files[last_unique]
        else:
            s = int(start)
            same, name = None, f"{fid}_{s // 3600:02d}{s % 3600 // 60:02d}{s % 60:02d}.jpg"
            (Path(out_dir) / name).write_bytes(JPEG)
            files[fid] = name
            last_unique = fid
        frames.append({"id": fid, "file": name, "start": start, "end": end, "captured_at": start, "sample": int(start * 2),
                       "kind": "settled", "same_as": same, "change": {"frac": 0.5, "bbox": [0, 0, 1, 1], "blobs": 1},
                       "hot_frac": 0.0, "bytes": len(JPEG), "tokens": 1792})
    if progress:
        progress({"stage": "frames", "done_s": dur, "total_s": dur, "frames": n})
    doc = {"schema": "va.frames/1", "video": {"path": str(src).replace("\\", "/"), "key": Path(out_dir).parent.name,
                                              "duration_s": dur},
           "range": [0.0, dur], "params": params, "frames": frames, "n_unique": len(files), "warnings": []}
    _w(Path(out_dir) / "frames.json", doc)
    return doc


def run_batches_stage(frames, cues, params, out_dir, meta):
    spec = _spec(meta["video_path"])
    if spec.get("fail") == "batches":
        raise RuntimeError("lotes falharam")
    _log("batches", out_dir)
    out = Path(out_dir)
    rng = params.get("range")
    fl = frames["frames"]
    if rng:
        fl = [f for f in fl if f["start"] < rng[1] and f["end"] > rng[0]]
    size = int(params.get("max_frames", 20))
    entries = []
    for k in range(0, len(fl), size):
        chunk = fl[k:k + size]
        ids = {f["id"] for f in chunk}
        n_images = sum(1 for f in chunk if f["same_as"] is None or f["same_as"] not in ids)
        n = len(entries) + 1
        md = out / f"batch_{n:03d}.md"
        md.write_text(f"# Lote B{n:03d}\n" + "".join(f"### {f['id']}\n" for f in chunk), encoding="utf-8")
        (out / f"sheet_{n:03d}.jpg").write_bytes(JPEG)
        entries.append({"n": n, "id": f"B{n:03d}", "frames": [chunk[0]["id"], chunk[-1]["id"]],
                        "t": [chunk[0]["start"], chunk[-1]["end"]], "n_frames": len(chunk), "n_images": n_images,
                        "md": md.as_posix()})
    (out / "transcript.md").write_text("# Transcrição\n", encoding="utf-8")
    (out / "overview.jpg").write_bytes(JPEG)
    batches_doc = {"schema": "va.batches/1", "n_batches": len(entries), "params": params, "batches": entries}
    _w(out / "batches.json", batches_doc)
    full = frames["range"]
    summary = {"schema": "va.summary/1", "title": meta.get("title"), "video_path": meta.get("video_path"),
               "vtt_path": meta.get("vtt_path"), "video_key": meta.get("video_key"),
               "duration_s": frames["video"]["duration_s"],
               "range": [max(rng[0], full[0]), min(rng[1], full[1])] if rng else full,
               "n_frames": len(fl), "n_batches": len(entries), "language": meta.get("language", "und"),
               "batches": [[e["n"], e["frames"][0], e["frames"][1], e["t"][0], e["t"][1], e["n_frames"], e["n_images"]]
                           for e in entries],
               "paths": {"batches_dir": out.as_posix()}, "warnings": []}
    _w(out / "summary.json", summary)
    return {"batches": batches_doc, "summary": summary}
'''


def load_fakes() -> dict:
    ns: dict = {"__name__": "va_fake_stages"}
    exec(compile(FAKE_SRC, "va_fake_stages.py", "exec"), ns)
    return ns


def fake_video(path: Path, **spec) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"duration_s": 300.0, "n_frames": 45, **spec}), encoding="utf-8")
    return path


def fake_vtt(path: Path, text: str = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nolá\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def sb(tmp_path, monkeypatch):
    """Sandbox: isolated workspace and Claude home, fake stages, environment checks stubbed."""
    user = tmp_path / "user"
    home, chome = user / "video-analysis", user / ".claude"
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", str(home))
    monkeypatch.setenv("VA_CLAUDE_HOME", str(chome))
    calls = tmp_path / "calls.txt"
    monkeypatch.setenv("VA_FAKE_CALLS", str(calls))
    fakes = load_fakes()
    for name in STAGE_NAMES:
        monkeypatch.setitem(cache.STAGE_OVERRIDES, name, fakes[name])
    monkeypatch.setattr(ambiente, "runtime_problems", lambda need=(): [])
    monkeypatch.setattr(ambiente, "tool_status", lambda name, timeout=15.0: {"id": name, "ok": True, "version": f"{name} version test",
                                                                             "path": name, "detail": name})
    monkeypatch.setattr(prepare, "STATUS_INTERVAL_S", 0.05)
    vids = tmp_path / "vids"
    v1 = fake_video(vids / "[PES] pasta  dupla" / (NFD(TEAMS) + ".mp4"), n_frames=45, revisit_every=7)
    fake_vtt(vids / "[PES] pasta  dupla" / (NFD(TEAMS) + ".vtt"))
    v2 = fake_video(vids / "aulas" / "venda_zero_clientes.mp4", n_frames=30, duration_s=441.2165)
    fake_vtt(vids / "aulas" / "venda_zero_clientes.vtt")
    return SimpleNamespace(tmp=tmp_path, user=user, home=home, chome=chome, calls=calls, vids=vids, v1=v1, v2=v2,
                           requests=home / "requests", pedidos=home / "pedidos")


def write_request(sb, videos, objective="Explique o que aparece na tela.", options=None, legacy=False) -> Path:
    """requests/request.md as the skill writes it; ``legacy``: pedidos/pedido.md with the section names
    of before the rename (# objetivo, # opcoes), which still work."""
    folder, name, sections = ((sb.pedidos, "pedido.md", ("# objetivo", "# opcoes")) if legacy else
                              (sb.requests, "request.md", ("# objective", "# options")))
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["# videos", *[as_posix(v) for v in videos], "", sections[0], objective, "", sections[1]]
    lines += [f"{k}: {v}" for k, v in (options or {"papeis" if legacy else "roles": "inline"}).items()]
    p = folder / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def run_cli(capsys, *argv):
    code = prepare.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, f"stdout must be exactly one JSON line, got {len(lines)}:\n{out[:2000]}"
    return code, json.loads(lines[0]), err


def calls(sb) -> list[str]:
    try:
        return sb.calls.read_text(encoding="utf-8").split()[0::3]
    except FileNotFoundError:
        return []


def reset_calls(sb):
    if sb.calls.exists():
        sb.calls.unlink()


def node_validate(tmp_path: Path, wargs: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH")
    runner = tmp_path / "validate.mjs"
    runner.write_text(r"""
import { readFileSync } from 'node:fs'
const [script, argsFile] = process.argv.slice(2)
const SRC = readFileSync(script, 'utf8').replace(/^export const meta =/, 'const meta =')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const body = new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'log', 'phase', 'budget', 'workflow', SRC)
const args = JSON.parse(readFileSync(argsFile, 'utf8'))
const logs = []
const parallel = thunks => Promise.all(thunks.map(f => Promise.resolve().then(f).catch(() => null)))
try {
  const r = await body(args, async () => null, parallel, null, m => logs.push(String(m)), () => {},
    { total: null, spent: () => 0, remaining: () => Infinity }, null)
  process.stdout.write(JSON.stringify({ ok: true, log: logs[0], videos: r.videos.length }))
} catch (e) { process.stdout.write(JSON.stringify({ ok: false, error: String(e && e.message || e) })) }
""", encoding="utf-8")
    args_file = tmp_path / "wargs.json"
    args_file.write_text(json.dumps(wargs, ensure_ascii=False), encoding="utf-8")
    script = Path(prepare.SCRIPT_PATH)
    r = subprocess.run([node, str(runner), str(script), str(args_file)], capture_output=True, text=True,
                       encoding="utf-8", timeout=60)
    return json.loads(r.stdout)


# --- the main path -------------------------------------------------------------------------------------

OBJ = 'Explique o indicador "venda zero clientes": de onde vem: cada dado.\n\n## Formato\nTabela.'


def test_request_happy_path(sb, capsys, skill_dir):
    req = write_request(sb, [sb.v1, sb.v2], OBJ, {
        "roles": "inline", "max_batches": "2", "parallel": "2", "analyst_effort": "medium", "language": "pt-BR",
        "speakers": "SPEAKER_01=Ana", "sensitivity": "high", "ignore_region": "0.8,0.75,0.2,0.25; 0,0,0.2,0.06"})
    code, env, err = run_cli(capsys, "--request", req)
    assert code == 0, env
    assert list(env) == ENVELOPE_KEYS
    assert env["ok"] is True and env["mode"] == "prepare" and env["version"] == "1.0.0"
    run_dir = Path(env["run_dir"])
    assert env["run_id"] == run_dir.name and run_dir.is_dir() and env["run_id"].endswith("-repasse-fluxo-de-carga")
    assert err.splitlines()[0] == f"run_dir: {env['run_dir']}"
    assert env["home"] == as_posix(sb.home) and "\\" not in json.dumps(env, ensure_ascii=False).replace("\\\\", "")
    # prepare.json and logs carry the same JSON
    assert env["json_path"] == as_posix(run_dir / "prepare.json") and read_json(run_dir / "prepare.json") == env
    assert read_json(sb.home / "logs" / f"prepare-{env['run_id']}.json") == env
    assert (sb.home / "logs" / f"prepare-{env['run_id']}.log").read_text(encoding="utf-8")
    # request consumed into the run
    assert not req.exists() and (run_dir / "request.md").read_text(encoding="utf-8").startswith("# videos")
    assert not (run_dir / "pedido.md").exists() and not list(sb.requests.iterdir())
    # objective.md
    md = (run_dir / "objective.md").read_text(encoding="utf-8")
    assert md.startswith("# Objetivo (literal)\n\n" + OBJ + "\n\n# Opções\n\n- papeis: inline\n- max_lotes: 2\n")
    assert "\n# Vídeos\n\n- repasse-fluxo-de-carga-20260818-160841: " in md and "sensibilidade: high" in md
    # workflow_args
    wa = env["workflow_args"]
    assert contracts.check_workflow_args(wa) == []
    assert wa["options"] == {"max_batches": 2, "max_parallel_videos": 2, "analyst_effort": "medium", "curator_effort": "high",
                             "curation": "plan"}
    assert [v["out_dir"] for v in wa["videos"]] == [f"{as_posix(run_dir)}/v1", f"{as_posix(run_dir)}/v2"]
    assert wa["roles_dir"] == as_posix(sb.home / "roles") and wa["roles_mode"] == "inline" and wa["language"] == "pt-BR"
    assert [v["slug"] for v in wa["videos"]] == ["repasse-fluxo-de-carga-20260818-160841", "venda-zero-clientes"]
    v1, v2 = wa["videos"]
    assert v1["batch_images"] == [18, 18, 4] and v1["n_frames"] == 45      # revisits within a batch are not images
    assert v2["batch_images"] == [20, 10] and v2["duration"] == "00:07:21"
    for v in wa["videos"]:
        assert (v["start_batch"], v["ledger_exists"], v["start_prompt_exists"], v["final_exists"]) == (1, False, False, False)
        assert Path(v["out_dir"], "prompts").is_dir() and Path(v["out_dir"], "reports").is_dir()
        assert Path(v["batches_dir"], "batch_001.md").is_file() and Path(v["batches_dir"]).name.startswith("batches-")
    assert env["estimate"]["batches"] == 4 and env["estimate"]["agents"] == (2 + 2) * 2 + 1
    assert env["script_path"] == as_posix(skill_dir / "workflow" / "video-analysis.js") and "\\" not in env["script_path"]
    assert env["env"] == {"permissions_ok": False, "agents_current": False, "workflow_current": False}
    # videos: summary fields + cache_hit + timing
    for v in env["videos"]:
        assert {"vid", "slug", "title", "n_frames", "n_batches", "cache_hit", "timing_s", "warnings"} <= set(v)
        assert v["cache_hit"] == {"cues": False, "frames": False, "batches": False}
        assert set(v["timing_s"]) == {"cues", "frames", "batches"}
    assert env["videos"][0]["title"] == unicodedata.normalize("NFC", TEAMS)
    # params reached the stages; run.json keeps everything for resume
    rj = read_json(run_dir / "run.json")
    assert rj["workflow_args"] == wa and rj["objective"] == OBJ
    assert rj["request"] == as_posix(run_dir / "request.md") and "pedido" not in rj
    assert rj["params"]["frames"]["sensitivity"] == "high"
    assert rj["params"]["frames"]["ignore_regions"] == [[0.8, 0.75, 0.2, 0.25], [0.0, 0.0, 0.2, 0.06]]
    assert rj["params"]["cues"]["speaker_names"] == {"SPEAKER_01": "Ana"}
    assert set(rj["videos"][0]["stages"]) == {"cues", "frames", "batches"}
    assert rj["options"]["roles_mode_requested"] == "inline"
    # status.json, roles, no leftovers
    st = read_json(run_dir / "status.json")
    assert st["state"] == "pronto" and [v["state"] for v in st["videos"]] == ["pronto", "pronto"]
    for role in (skill_dir / "agents").glob("*.md"):
        assert (sb.home / "roles" / role.name).read_bytes() == role.read_bytes()
    assert not list((sb.home / "tmp").glob("progress-*"))
    assert not list((sb.home / "cache").glob("*/.lock"))
    assert calls(sb) == ["cues", "frames", "batches", "cues", "frames", "batches"]


def test_node_accepts_workflow_args(sb, capsys):
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1, sb.v2], options={"roles": "inline", "max_batches": "1"}))
    assert code == 0
    res = node_validate(sb.tmp, env["workflow_args"])
    assert res["ok"], res
    assert f"~{env['estimate']['agents']} agents" in res["log"]   # the Workflow's own count matches DL-12


def test_second_run_hits_cache_and_force(sb, capsys):
    code, env1, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    assert code == 0
    reset_calls(sb)
    code, env2, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2], objective="Outra pergunta."))
    assert code == 0 and calls(sb) == []
    assert env2["videos"][0]["cache_hit"] == {"cues": True, "frames": True, "batches": True}
    assert env2["run_id"] != env1["run_id"]
    assert env2["workflow_args"]["videos"][0]["batches_dir"] == env1["workflow_args"]["videos"][0]["batches_dir"]
    code, env3, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]), "--force", "frames")
    assert code == 0 and calls(sb) == ["frames", "batches"]
    assert env3["videos"][0]["cache_hit"] == {"cues": True, "frames": False, "batches": False}
    reset_calls(sb)
    code, _, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]), "--force")
    assert calls(sb) == ["cues", "frames", "batches"]


def test_warm_run_needs_no_ffprobe_and_no_tool_check(sb, capsys, monkeypatch):
    """Integration (design §9, warm cache <= 2 s per video): a fully cached video is answered from
    the cache. probe.json replaces ffprobe and neither ffmpeg nor ffprobe is checked; a stage to
    build brings both back. Measured on the real fixtures: 4.5 s -> 0.6 s for one warm video."""
    needs, probes = [], []
    monkeypatch.setattr(ambiente, "runtime_problems", lambda need=(): needs.append(tuple(need)) or [])
    real_probe = cache.STAGE_OVERRIDES["probe_video"]
    monkeypatch.setitem(cache.STAGE_OVERRIDES, "probe_video", lambda p: probes.append(p) or real_probe(p))
    code, env1, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1, sb.v2]))
    assert code == 0 and needs == [("ffmpeg", "ffprobe", "numpy", "pillow")] and len(probes) == 2
    needs.clear(), probes.clear(), reset_calls(sb)
    code, env2, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1, sb.v2]), "--jobs", "2")
    assert code == 0 and needs == [("numpy", "pillow")] and probes == [] and calls(sb) == []
    assert all(v["cache_hit"] == {"cues": True, "frames": True, "batches": True} for v in env2["videos"])
    same = lambda env: [(v["duration"], v["n_frames"], v["batch_images"]) for v in env["workflow_args"]["videos"]]  # noqa: E731
    assert same(env1) == same(env2)
    assert read_json(Path(env2["run_dir"]) / "run.json")["videos"][1]["probe"]["duration_s"] == 441.2165
    needs.clear()
    code, est, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1, sb.v2]), "--estimar")
    assert code == 0 and needs == [("numpy", "pillow")] and probes == [] and est["estimate"]["seconds"] <= 2
    # batch-only change: cues and frames stay cached, so still no ffprobe and no ffmpeg
    needs.clear()
    code, _, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]), "--batch-frames", "10")
    assert code == 0 and needs == [("numpy", "pillow")] and probes == [] and calls(sb) == ["batches"]
    # frames to build: tools checked again and a fresh probe
    needs.clear(), reset_calls(sb)
    code, _, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]), "--force", "frames")
    assert code == 0 and needs == [("ffmpeg", "ffprobe", "numpy", "pillow")] and len(probes) == 1
    assert calls(sb) == ["frames", "batches"]
    # --estimar on a cold video checks ffmpeg too (the prepare that follows needs it): exit 3 early
    cold = fake_video(sb.vids / "frio" / "novo.mp4")
    fake_vtt(sb.vids / "frio" / "novo.vtt")
    needs.clear()
    code, est, _ = run_cli(capsys, "--request", write_request(sb, [cold]), "--estimar")
    assert code == 0 and needs == [("ffmpeg", "ffprobe", "numpy", "pillow")]


def test_pool_plan_keeps_warm_jobs_in_process_and_starts_the_longest_first(monkeypatch):
    monkeypatch.setattr(cache, "STAGE_OVERRIDES", {})
    jobs = [{"warm": True, "cost": 0.0}, {"warm": False, "cost": 20.3}, {"warm": False, "cost": 140.9},
            {"warm": True}, {"warm": False, "cost": 41.9}, {"warm": False, "cost": 41.9}]
    assert prepare.pool_plan(jobs, 2) == [2, 4, 5, 1]                  # longest first; ties in input order
    assert prepare.pool_plan(jobs, 1) == []                             # --jobs 1: everything in-process
    assert prepare.pool_plan(jobs[:2] + jobs[3:4], 2) == []             # a single job to build: no pool
    assert prepare.pool_plan([{"warm": True}, {"warm": True}], 2) == []  # all cached: no worker start-up
    assert prepare.pool_plan([{"warm": False}, {"warm": False}], 3) == [0, 1]
    monkeypatch.setattr(cache, "STAGE_OVERRIDES", {"probe_video": print})
    assert prepare.pool_plan(jobs, 2) == []                             # in-process test overrides


def test_warm_path_imports_neither_numpy_nor_pillow(skill_dir):
    """prepare.py hashes the stage params and reads the cache without the frame engine: these imports
    must not pull numpy, Pillow or the process pool (they cost ~1-2 s per run on this machine)."""
    code = ("import sys; sys.dont_write_bytecode = True; sys.path.insert(0, sys.argv[1]); import prepare; "
            "from va import cache; [cache.stage_fn(n) for n in ('effective_frame_params', 'effective_cue_params', "
            "'effective_batch_params')]; bad = [m for m in ('numpy', 'PIL', 'multiprocessing', 'concurrent.futures') "
            "if m in sys.modules]; print(','.join(bad) or 'ok')")
    r = subprocess.run([sys.executable, "-B", "-c", code, str(skill_dir / "scripts")], capture_output=True, text=True,
                       timeout=120)
    assert r.stdout.strip() == "ok", r.stdout + r.stderr


def test_missing_jpeg_rebuilds_frames_and_batches(sb, capsys):
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    frames_dir = Path(read_json(Path(env["run_dir"]) / "run.json")["videos"][0]["stages"]["frames"])
    next(frames_dir.glob("F0003_*.jpg")).unlink()
    reset_calls(sb)
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    assert code == 0 and calls(sb) == ["frames", "batches"]


def test_batch_params_change_does_not_reextract(sb, capsys):
    run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    reset_calls(sb)
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]), "--batch-frames", "10")
    assert code == 0 and calls(sb) == ["batches"]
    assert env["workflow_args"]["videos"][0]["batch_images"] == [10, 10, 10]


def test_estimar_does_not_consume_and_reports_hits(sb, capsys):
    req = write_request(sb, [sb.v1, sb.v2])
    code, env, err = run_cli(capsys, "--request", req, "--estimar")
    assert code == 0 and env["mode"] == "estimate" and req.exists()
    assert list(env) == ENVELOPE_KEYS
    assert env["run_id"] is None and env["run_dir"] is None and env["workflow_args"] is None
    assert not (sb.home / "runs").exists() or not any((sb.home / "runs").iterdir())
    assert "run_dir:" not in err
    assert isinstance(env["estimate"]["seconds"], int) and env["estimate"]["seconds"] > 0
    assert env["estimate"]["batches"] is None and env["estimate"]["agents"] is None
    assert env["env"] == {"permissions_ok": False, "agents_current": False, "workflow_current": False}
    assert all(v["cache_hit"] == {"cues": False, "frames": False, "batches": False} for v in env["videos"])
    assert env["json_path"].startswith(as_posix(sb.home / "logs" / "estimate-"))
    assert calls(sb) == []
    run_cli(capsys, "--request", req)
    req = write_request(sb, [sb.v1, sb.v2], options={"roles": "inline", "max_batches": "1"})
    code, env, _ = run_cli(capsys, "--request", req, "--estimate")
    assert all(v["cache_hit"] == {"cues": True, "frames": True, "batches": True} for v in env["videos"])
    assert env["estimate"]["batches"] == 2 and env["estimate"]["agents"] == 3 + 3 + 1
    assert env["estimate"]["seconds"] <= 2 and req.exists()


def test_old_names_still_work(sb, capsys):
    """The names of before the rename (pedidos/pedido.md, --pedido, # objetivo, # opcoes, and the option
    keys and values in Portuguese) give the same run as the English ones."""
    legacy = write_request(sb, [sb.v2], OBJ, {"papeis": "inline", "max_lotes": "todos", "curadoria": "lotes",
                                              "esforco_analista": "medio", "idioma": "pt-BR", "sensibilidade": "alta"},
                           legacy=True)
    assert legacy == sb.pedidos / "pedido.md"
    code, old, _ = run_cli(capsys, "--pedido", legacy)
    assert code == 0, old
    code, new, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2], OBJ, {
        "roles": "inline", "max_batches": "all", "curation": "batches", "analyst_effort": "medium", "language": "pt-BR",
        "sensitivity": "high"}))
    assert code == 0, new
    assert old["workflow_args"]["options"] == new["workflow_args"]["options"] == {
        "max_batches": None, "max_parallel_videos": 3, "analyst_effort": "medium", "curator_effort": "high",
        "curation": "batches"}
    old_dir, new_dir = Path(old["run_dir"]), Path(new["run_dir"])
    assert (old_dir / "objective.md").read_text(encoding="utf-8") == (new_dir / "objective.md").read_text(encoding="utf-8")
    assert read_json(old_dir / "run.json")["params"] == read_json(new_dir / "run.json")["params"]
    for run_dir in (old_dir, new_dir):  # whatever the request's name, the run keeps it as request.md
        assert (run_dir / "request.md").read_text(encoding="utf-8").startswith("# videos")
        assert not (run_dir / "pedido.md").exists()
        assert read_json(run_dir / "run.json")["request"] == as_posix(run_dir / "request.md")
    assert not legacy.exists() and not list(sb.pedidos.iterdir())
    # the old name fails into pedido.erro.md, as before
    legacy = write_request(sb, [sb.vids / "nao existe.mp4"], legacy=True)
    code, env, _ = run_cli(capsys, "--pedido", legacy)
    assert code == 2 and env["error"]["code"] == "video_nao_encontrado"
    assert not legacy.exists() and (sb.pedidos / "pedido.erro.md").read_text(encoding="utf-8").startswith("# videos")
    assert not sb.requests.exists() or not list(sb.requests.iterdir())


# --- input errors ------------------------------------------------------------------------------------------

def test_vtt_missing_teams_pattern(sb, capsys):
    folder = sb.vids / "teams"
    video = fake_video(folder / "Indicador PES-20260610_150844-Gravação de Reunião.mp4")
    fake_vtt(folder / "Indicador PES.vtt")
    sb.requests.mkdir(parents=True, exist_ok=True)
    (sb.requests / "request.error.md").write_text("velho", encoding="utf-8")
    req = write_request(sb, [video])
    code, env, err = run_cli(capsys, "--request", req)
    assert code == 2 and env["ok"] is False and env["error"]["code"] == "vtt_ausente"
    d = env["error"]["detail"]
    assert "Indicador PES.vtt (prefix of the video name)" in d
    assert ("Rename-Item -LiteralPath '" + os.path.normpath(str(folder / "Indicador PES.vtt")) +
            "' -NewName 'Indicador PES-20260610_150844-Gravação de Reunião.vtt'") in d
    assert "--vtt" in d and "--skip-missing" in d
    assert env["missing_vtt"][0]["candidates"] == [as_posix(folder / "Indicador PES.vtt")]
    assert not req.exists() and (sb.requests / "request.error.md").read_text(encoding="utf-8").startswith("# videos")
    assert not (sb.home / "runs").exists() or not any((sb.home / "runs").iterdir())
    assert "run_dir:" not in err and calls(sb) == []


def test_vtt_missing_doubles_curly_quote(sb, capsys):
    folder = sb.vids / "curly"
    video = fake_video(folder / f"{TEAMS}.mp4")
    fake_vtt(folder / "Repasse - Fluxo de Carga dos DA’s.vtt")
    code, env, _ = run_cli(capsys, video, "--objective", "x")
    assert code == 2 and "DA’’s.vtt'" in env["error"]["detail"]


def test_skip_missing_and_vtt_flag(sb, capsys):
    folder = sb.vids / "mista"
    a = fake_video(folder / "a.mp4")
    fake_vtt(folder / "a.vtt")
    fake_video(folder / "b sem legenda.mp4")
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [folder]))
    assert code == 2 and env["error"]["code"] == "vtt_ausente" and "--vtt" not in env["error"]["detail"]
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [folder]), "--skip-missing")
    assert code == 0 and [v["slug"] for v in env["workflow_args"]["videos"]] == ["a"]
    assert any("b sem legenda.mp4" in w for w in env["warnings"])
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [folder]), "--vtt", folder / "a.vtt")
    assert code == 2 and env["error"]["code"] == "uso_invalido" and "exactly 1 video" in env["error"]["detail"]
    other = fake_vtt(sb.vids / "legendas" / "qualquer nome.vtt")
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [folder / "b sem legenda.mp4"]), "--vtt", other)
    assert code == 0 and env["videos"][0]["vtt_path"] == as_posix(other)
    code, env, _ = run_cli(capsys, a, "--objective", "x", "--vtt", sb.vids / "nao.vtt")
    assert code == 2 and env["error"]["code"] == "vtt_ausente"


@pytest.mark.parametrize("make_args, code, err_code", [
    (lambda sb: ["--request", write_request(sb, [sb.vids / "nao existe.mp4"])], 2, "video_nao_encontrado"),
    (lambda sb: ["--request", write_request(sb, [sb.v1], options={"parallel": "9"})], 2, "opcao_invalida"),
    (lambda sb: ["--request", write_request(sb, [sb.v1], options={"turbo": "sim"})], 2, "opcao_invalida"),
    (lambda sb: ["--request", write_request(sb, [sb.v1], options={"sensitivity": "x"})], 2, "opcao_invalida"),
    (lambda sb: ["--request", write_request(sb, [sb.v1], objective="")], 2, "objetivo_ausente"),
    (lambda sb: ["--request", write_request(sb, [sb.v1], options={"range": "02:00:00-03:00:00"})], 2, "opcao_invalida"),
    (lambda sb: ["--request", sb.requests / "nao.md"], 2, "pedido_invalido"),
    (lambda sb: ["--pedido", sb.pedidos / "nao.md"], 2, "pedido_invalido"),
    (lambda sb: ["--pedido", write_request(sb, [sb.v1], options={"turbo": "sim"}, legacy=True)], 2, "opcao_invalida"),
    (lambda sb: ["--request", write_request(sb, [sb.v1]), sb.v2], 2, "uso_invalido"),
    (lambda sb: [sb.v1, "--run"], 2, "objetivo_ausente"),
    (lambda sb: ["--jobs", "muitos", sb.v1], 2, "uso_invalido"),
    (lambda sb: [], 2, "uso_invalido"),
    (lambda sb: ["--resume", "nada-disso"], 2, "run_nao_encontrado"),
    (lambda sb: ["--gc", "5", "--resume", "ultimo"], 2, "uso_invalido"),
    (lambda sb: ["--resume", "ultimo", "--request", sb.requests / "request.md"], 2, "uso_invalido"),
    (lambda sb: ["--resume", "ultimo", "--pedido", sb.pedidos / "pedido.md"], 2, "uso_invalido"),
])
def test_input_errors(sb, capsys, make_args, code, err_code):
    c, env, _ = run_cli(capsys, *make_args(sb))
    assert c == code and env["ok"] is False and env["error"]["code"] == err_code, env
    assert env["error"]["detail"] and env["workflow_args"] is None
    assert not list(sb.requests.glob("request.md")) if sb.requests.exists() else True
    assert not list(sb.pedidos.glob("pedido.md")) if sb.pedidos.exists() else True


def test_environment_missing_exit_3(sb, capsys, monkeypatch):
    monkeypatch.setattr(ambiente, "runtime_problems", lambda need=(): [
        {"id": "ffmpeg", "ok": False, "code": "ffmpeg_ausente", "detail": "ffmpeg não está no PATH"}])
    req = write_request(sb, [sb.v1])
    code, env, _ = run_cli(capsys, "--request", req)
    assert code == 3 and env["error"]["code"] == "ffmpeg_ausente" and "setup" in env["error"]["detail"]
    assert (sb.requests / "request.error.md").exists()
    monkeypatch.setattr(ambiente, "runtime_problems", lambda need=(): [
        {"id": "numpy", "ok": False, "code": "dependencia_ausente", "detail": "numpy não está instalado"}])
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1]))
    assert code == 3 and env["error"]["code"] == "dependencia_ausente"


def test_workspace_not_writable_exit_3(sb, capsys, tmp_path):
    blocker = tmp_path / "arquivo"
    blocker.write_text("não é pasta", encoding="utf-8")
    code, env, _ = run_cli(capsys, sb.v1, "--objective", "x", "--home", blocker)
    assert code == 3 and env["error"]["code"] == "workspace_inacessivel"


# --- video failures, interruption, size guard ------------------------------------------------------------------

def test_one_video_fails_exit_4_keeps_the_others(sb, capsys):
    bad = fake_video(sb.vids / "ruim" / "quebrado.mp4", fail="frames")
    fake_vtt(sb.vids / "ruim" / "quebrado.vtt")
    req = write_request(sb, [sb.v2, bad])
    code, env, _ = run_cli(capsys, "--request", req)
    assert code == 4 and env["ok"] is False and env["error"]["code"] == "video_falhou"
    assert "quebrado" in env["error"]["detail"] and "decodificação falhou" in env["error"]["detail"]
    assert [e["slug"] for e in env["errors"]] == ["quebrado"]
    assert [v["slug"] for v in env["videos"]] == ["venda-zero-clientes"]
    wa = env["workflow_args"]
    assert contracts.check_workflow_args(wa) == [] and [v["slug"] for v in wa["videos"]] == ["venda-zero-clientes"]
    run_dir = Path(env["run_dir"])
    assert read_json(run_dir / "run.json")["workflow_args"] == wa       # resumable
    assert (run_dir / "request.md").exists() and (sb.requests / "request.error.md").exists() and not req.exists()
    assert read_json(run_dir / "status.json")["state"] == "parcial"
    log = (sb.home / "logs" / f"prepare-{env['run_id']}.log").read_text(encoding="utf-8")
    assert "Traceback" in log and "decodificação falhou" in log


def test_probe_failure_and_all_failed(sb, capsys):
    bad = fake_video(sb.vids / "ruim" / "corrompido.mp4", fail="probe")
    fake_vtt(sb.vids / "ruim" / "corrompido.vtt")
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [bad]))
    assert code == 4 and env["error"]["code"] == "video_falhou" and env["workflow_args"] is None
    assert "arquivo corrompido" in env["error"]["detail"] and env["errors"][0]["slug"] == "corrompido"


def test_interrupted_exit_130(sb, capsys):
    stop = fake_video(sb.vids / "int" / "longo.mp4", interrupt=True)
    fake_vtt(sb.vids / "int" / "longo.vtt")
    req = write_request(sb, [stop])
    code, env, err = run_cli(capsys, "--request", req)
    assert code == 130 and env["error"]["code"] == "interrompido"
    assert (sb.requests / "request.error.md").exists() and not req.exists()
    assert not list((sb.home / "cache").glob("*/.lock"))
    assert read_json(Path(env["run_dir"]) / "status.json")["state"] == "erro"


def test_size_guard_and_forcar(sb, capsys):
    big = fake_video(sb.vids / "big" / "reuniao longa.mp4", n_frames=401, duration_s=24060.0)
    fake_vtt(sb.vids / "big" / "reuniao longa.vtt")
    req = write_request(sb, [big], options={"curation": "batches"})
    code, env, _ = run_cli(capsys, "--request", req, "--batch-frames", "1")
    assert code == 2 and env["error"]["code"] == "run_grande_demais" and "803" in env["error"]["detail"]
    assert env["estimate"]["agents"] == 803 and env["workflow_args"] is None
    assert (sb.requests / "request.error.md").exists()
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [big], options={"curation": "batches"}), "--batch-frames", "1",
                           "--forcar")
    assert code == 0 and any("--forcar" in w for w in env["warnings"])
    # the Workflow refuses > 800 agents (reference/sem-workflow.md: do not run it either): split the run
    assert any("refuses more than 800" in w and "split the run" in w for w in env["warnings"])
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [big], options={"roles": "inline", "max_batches": "10",
                                                                               "curation": "batches"}),
                           "--batch-frames", "1")
    assert code == 0 and env["estimate"]["agents"] == 21
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [big]), "--batch-frames", "1")
    assert code == 0 and env["estimate"]["agents"] == 403 and env["workflow_args"]["options"]["curation"] == "plan"


# --- roles, permissions ---------------------------------------------------------------------------------

def test_roles_agents_downgrade_and_current(sb, capsys, skill_dir):
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2], options={"roles": "agents"}))
    assert code == 0 and env["workflow_args"]["roles_mode"] == "inline" and env["env"]["agents_current"] is False
    assert any("roles (--papeis): agents requested" in w for w in env["warnings"])
    ambiente.install_agents(skill_dir, sb.chome)
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2], options={"roles": "agents"}))
    assert env["workflow_args"]["roles_mode"] == "agents" and env["env"]["agents_current"] is True
    assert not any("papeis" in w for w in env["warnings"])
    next((sb.chome / "agents").glob("*.md")).write_text("editado", encoding="utf-8")
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2], options={"roles": "agents"}))
    assert env["workflow_args"]["roles_mode"] == "inline" and env["env"]["agents_current"] is False


def test_permissions_ok_from_settings(sb, capsys):
    sb.chome.mkdir(parents=True, exist_ok=True)
    (sb.chome / "settings.json").write_text(json.dumps({"permissions": {"allow": ambiente.workspace_rules(sb.home)}}),
                                            encoding="utf-8")
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    assert env["env"]["permissions_ok"] is True


def test_workflow_current_tracks_the_saved_copy(sb, capsys, skill_dir, monkeypatch):
    """env.workflow_current: <claude home>/workflows/video-analysis.js is byte-identical to the skill's
    script (SKILL.md step 6 calls the Workflow by name only then). The Claude home is the one
    verificar_ambiente.py checks by default: VA_CLAUDE_HOME (here, the sandbox) or %USERPROFILE%\\.claude."""
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    assert code == 0 and env["env"]["workflow_current"] is False                       # never installed
    ambiente.install_workflow(skill_dir, sb.chome)
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    assert code == 0 and env["env"]["workflow_current"] is True
    code, est, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]), "--estimar")
    assert code == 0 and est["env"]["workflow_current"] is True
    code, res, _ = run_cli(capsys, "--resume", env["run_id"])
    assert code == 0 and res["env"]["workflow_current"] is True
    (sb.chome / "workflows" / "video-analysis.js").write_text("// cópia de outra versão", encoding="utf-8")
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    assert code == 0 and env["env"]["workflow_current"] is False                       # stale copy
    other = sb.tmp / "outra-claude"
    monkeypatch.setenv("VA_CLAUDE_HOME", str(other))
    ambiente.install_workflow(skill_dir, other)
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    assert code == 0 and env["env"]["workflow_current"] is True                        # the override is honoured


# --- resume -------------------------------------------------------------------------------------------------

def test_resume_cases(sb, capsys):
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1], options={"roles": "inline", "max_batches": "2"}))
    assert code == 0
    run_dir = Path(env["run_dir"])
    out = Path(env["workflow_args"]["videos"][0]["out_dir"])
    (out / "ledger.md").write_text("Integrado até o lote: 001 de 003", encoding="utf-8")
    (out / "reports" / "batch_001.md").write_text("r1", encoding="utf-8")
    (out / "prompts" / "batch_002.md").write_text("p2", encoding="utf-8")
    reset_calls(sb)
    code, env, err = run_cli(capsys, "--resume", "ultimo")
    assert code == 0 and env["mode"] == "resume" and env["run_id"] == run_dir.name and calls(sb) == []
    assert err.splitlines()[0] == f"run_dir: {as_posix(run_dir)}"
    v = env["workflow_args"]["videos"][0]
    assert (v["start_batch"], v["ledger_exists"], v["start_prompt_exists"], v["final_exists"]) == (2, True, True, False)
    assert env["estimate"] == {"batches": 1, "agents": 2, "seconds": env["estimate"]["seconds"]}
    assert env["workflow_args"]["options"]["curation"] == "plan" and Path(out).name == "v1"
    assert contracts.check_workflow_args(env["workflow_args"]) == []
    code, env, _ = run_cli(capsys, "--resume", run_dir.name, "--max-lotes", "3", "--papeis", "agents")
    assert env["workflow_args"]["options"]["max_batches"] == 3 and env["estimate"]["agents"] == 3
    code, env, _ = run_cli(capsys, "--resume", run_dir.name, "--curadoria", "lotes")
    assert env["workflow_args"]["options"]["curation"] == "batches" and env["estimate"]["agents"] == 5
    assert env["workflow_args"]["roles_mode"] == "inline" and any("roles (--papeis): agents requested" in w for w in env["warnings"])
    for n in (2, 3):
        (out / "reports" / f"batch_{n:03d}.md").write_text("r", encoding="utf-8")
    (out / "relatorio.md").write_text("final", encoding="utf-8")
    code, env, _ = run_cli(capsys, "--resume", as_posix(run_dir), "--max-lotes", "0")
    v = env["workflow_args"]["videos"][0]
    assert (v["start_batch"], v["final_exists"]) == (4, True) and env["estimate"]["agents"] == 0
    assert any("already complete" in w for w in env["warnings"]) and (out / "relatorio.md").exists()
    (out / "reports" / "batch_003.md").unlink()
    code, env, _ = run_cli(capsys, "--resume", "ultimo")
    v = env["workflow_args"]["videos"][0]
    assert (v["start_batch"], v["final_exists"]) == (3, False)
    assert (out / "relatorio.anterior.md").read_text(encoding="utf-8") == "final" and not (out / "relatorio.md").exists()
    (out / "ledger.md").unlink()
    code, env, _ = run_cli(capsys, "--resume", "ultimo")
    v = env["workflow_args"]["videos"][0]
    assert (v["start_batch"], v["ledger_exists"]) == (1, False)
    assert list((out / "_descartado").glob("*/reports/batch_001.md"))
    rj = read_json(run_dir / "run.json")
    assert len(rj["resumes"]) == 6 and rj["workflow_args"] == env["workflow_args"]   # 6 resumes above
    assert read_json(run_dir / "prepare.json") == env


def test_resume_old_run_slug_folder(sb, capsys):
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1], options={"roles": "inline", "max_batches": "2"}))
    assert code == 0
    run_dir = Path(env["run_dir"])
    rj = read_json(run_dir / "run.json")
    wa = rj["workflow_args"]
    v = wa["videos"][0]
    old = run_dir / v["slug"]
    Path(v["out_dir"]).rename(old)
    v["out_dir"] = as_posix(old)
    del wa["options"]["curation"]
    (run_dir / "run.json").write_text(json.dumps(rj, ensure_ascii=False), encoding="utf-8")
    (old / "ledger.md").write_text("Integrado até o lote: 001 de 003", encoding="utf-8")
    (old / "reports" / "batch_001.md").write_text("r1", encoding="utf-8")
    code, env, _ = run_cli(capsys, "--resume", "ultimo")
    assert code == 0
    v = env["workflow_args"]["videos"][0]
    assert v["out_dir"] == as_posix(old) and (v["start_batch"], v["ledger_exists"]) == (2, True)
    assert env["workflow_args"]["options"]["curation"] == "batches" and env["estimate"]["agents"] == 3
    assert not (run_dir / "v1").exists()


def test_resume_combined_report_two_videos(sb, capsys):
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1, sb.v2]))
    run_dir = Path(env["run_dir"])
    (run_dir / "relatorio.md").write_text("combinado", encoding="utf-8")
    for v in env["workflow_args"]["videos"]:
        out = Path(v["out_dir"])
        (out / "ledger.md").write_text("L", encoding="utf-8")
        for n in range(1, len(v["batch_images"]) + 1):
            (out / "reports" / f"batch_{n:03d}.md").write_text("r", encoding="utf-8")
        (out / "relatorio.md").write_text("final", encoding="utf-8")
    code, env, _ = run_cli(capsys, "--resume", "ultimo")
    assert env["workflow_args"]["combined_exists"] is True and env["estimate"]["agents"] == 1  # only the CROSS slot
    first = Path(env["workflow_args"]["videos"][0]["out_dir"])
    (first / "reports" / "batch_002.md").unlink()
    code, env, _ = run_cli(capsys, "--resume", "ultimo")
    assert env["workflow_args"]["combined_exists"] is False and (run_dir / "relatorio.anterior.md").exists()


def test_resume_legacy_report_md_counts_as_final(sb, capsys):
    """A run made before the rename has <out_dir>/report.md and <run_dir>/report.md: --resume counts them
    as the finals and renames them to relatorio.md, the paths the Workflow derives; nothing is redone."""
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v1, sb.v2]))
    run_dir = Path(env["run_dir"])
    (run_dir / "report.md").write_text("combinado", encoding="utf-8")
    for v in env["workflow_args"]["videos"]:
        out = Path(v["out_dir"])
        (out / "ledger.md").write_text("L", encoding="utf-8")
        for n in range(1, len(v["batch_images"]) + 1):
            (out / "reports" / f"batch_{n:03d}.md").write_text("r", encoding="utf-8")
        (out / "report.md").write_text("final", encoding="utf-8")
    code, env, _ = run_cli(capsys, "--resume", "ultimo")
    wa = env["workflow_args"]
    assert code == 0 and wa["combined_exists"] is True and all(v["final_exists"] for v in wa["videos"])
    assert env["estimate"]["agents"] == 1 and any("already complete" in w for w in env["warnings"])
    for v in wa["videos"]:
        assert (Path(v["out_dir"]) / "relatorio.md").read_text(encoding="utf-8") == "final"
        assert not (Path(v["out_dir"]) / "report.md").exists()
    assert (run_dir / "relatorio.md").read_text(encoding="utf-8") == "combinado" and not (run_dir / "report.md").exists()
    acts = read_json(run_dir / "run.json")["resumes"][-1]["actions"]
    assert sum("-> relatorio.md (new name" in a for a in acts) == 3, acts                 # 2 videos + the combined


def test_resume_rebuilds_missing_cache_and_refuses_changed_source(sb, capsys):
    code, env, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    batches_dir = env["workflow_args"]["videos"][0]["batches_dir"]
    shutil.rmtree(sb.home / "cache")
    reset_calls(sb)
    code, env2, _ = run_cli(capsys, "--resume", "ultimo")
    assert code == 0 and calls(sb) == ["cues", "frames", "batches"]
    assert env2["workflow_args"]["videos"][0]["batches_dir"] == batches_dir
    assert env2["videos"][0]["cache_hit"] == {"cues": False, "frames": False, "batches": False}
    shutil.rmtree(sb.home / "cache")
    time.sleep(0.01)
    sb.v2.write_text(json.dumps({"duration_s": 441.2165, "n_frames": 31}), encoding="utf-8")
    code, env3, _ = run_cli(capsys, "--resume", "ultimo")
    assert code == 4 and env3["error"]["code"] == "video_falhou" and "changed" in env3["error"]["detail"]


# --- other modes ---------------------------------------------------------------------------------------------

def test_direct_mode_with_objective_file_and_run_name(sb, capsys, tmp_path):
    obj = tmp_path / "objetivo.md"
    obj.write_text("﻿Objetivo do arquivo: \"literal\"\n", encoding="utf-8")
    code, env, _ = run_cli(capsys, sb.v2, "--objective-file", obj, "--run=Minha Análise", "--papeis", "inline",
                           "--max-lotes", "1", "--trecho", "00:01:00-00:02:00")
    assert code == 0 and env["run_id"].endswith("-minha-analise")
    md = (Path(env["run_dir"]) / "objective.md").read_text(encoding="utf-8")
    assert md.startswith('# Objetivo (literal)\n\nObjetivo do arquivo: "literal"\n\n# Opções')
    assert "trecho: 00:01:00-00:02:00" in md
    v = env["workflow_args"]["videos"][0]
    # 30 frames of ~14.7 s each: frames 5..9 overlap 00:01:00-00:02:00
    assert v["duration"] == "00:01:00" and v["n_frames"] == 5 and v["batch_images"] == [5]


def test_trecho_reuses_frames_and_gets_its_own_batches(sb, capsys):
    code, full, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2]))
    reset_calls(sb)
    code, part, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2], options={"roles": "inline",
                                                                                   "range": "00:01:00-00:02:00"}))
    assert code == 0 and calls(sb) == ["batches"]
    assert part["workflow_args"]["videos"][0]["batches_dir"] != full["workflow_args"]["videos"][0]["batches_dir"]
    assert read_json(Path(part["run_dir"]) / "run.json")["params"]["batches"]["range"] == [60.0, 120.0]
    code, late, _ = run_cli(capsys, "--request", write_request(sb, [sb.v2], options={"roles": "inline",
                                                                                   "range": "00:07:00-00:09:00"}))
    assert code == 0 and any("after the end" in w for w in late["videos"][0]["warnings"])
    assert late["workflow_args"]["videos"][0]["duration"] == "00:00:21"


def test_cache_only_mode_without_objective(sb, capsys):
    code, env, err = run_cli(capsys, sb.v2, "--ascii")
    assert code == 0 and env["run_id"] is None and env["workflow_args"] is None and env["videos"]
    assert env["json_path"].startswith(as_posix(sb.home / "logs" / "prepare-"))
    assert "run_dir:" not in err


def test_ascii_output(sb, capsys):
    code = prepare.main([str(sb.v1), "--objective", "ação", "--ascii"])
    out = capsys.readouterr().out
    assert code == 0 and out.isascii() and json.loads(out)["videos"][0]["title"] == unicodedata.normalize("NFC", TEAMS)


def test_gc_mode(sb, capsys):
    run_cli(capsys, "--request", write_request(sb, [sb.v1, sb.v2]))
    keys = sorted(p for p in (sb.home / "cache").iterdir())
    old = time.time() - 40 * 86400
    os.utime(keys[0] / "last_used", (old, old))
    code, env, _ = run_cli(capsys, "--gc")
    assert code == 0 and env["mode"] == "gc" and env["gc"]["days"] == 30
    assert [r["key"] for r in env["gc"]["removed"]] == [keys[0].name] and env["gc"]["kept"] == 1
    assert env["gc"]["freed_bytes"] > 0 and not keys[0].exists() and keys[1].exists()
    code, env, _ = run_cli(capsys, "--gc", "0")
    assert code == 0 and len(env["gc"]["removed"]) == 1


def test_status_refreshes_from_progress_files(sb, monkeypatch):
    ctx = prepare.Ctx()
    ctx.home, ctx.run_id = sb.home, "20261006-000000-x"
    ctx.run_dir = sb.home / "runs" / ctx.run_id
    ctx.run_dir.mkdir(parents=True)
    st = prepare.Status(ctx, [{"vid": "v1", "slug": "x", "title": "X"}])
    st.start()
    write_json(st.progress_path("v1"), {"stage": "frames", "state": "processando", "info": {"done_s": 5}})

    def status():
        for _ in range(50):  # the refresher may be replacing the file right now (Windows sharing)
            try:
                return read_json(ctx.run_dir / "status.json")
            except (OSError, ValueError):
                time.sleep(0.02)
        return read_json(ctx.run_dir / "status.json")

    deadline = time.time() + 10
    while time.time() < deadline and status()["videos"][0]["stage"] != "frames":
        time.sleep(0.05)
    assert status()["videos"][0]["info"] == {"done_s": 5}
    st.finish("pronto")
    assert read_json(ctx.run_dir / "status.json")["state"] == "pronto" and not Path(st.progress_path("v1")).parent.exists()


def test_exit_code_table_matches_contract():
    assert prepare.EXIT["video_falhou"] == 4 and prepare.EXIT["interrompido"] == 130
    assert {prepare.EXIT[c] for c in ("vtt_ausente", "video_nao_encontrado", "pedido_invalido", "run_grande_demais")} == {2}
    assert {prepare.EXIT[c] for c in ("ffmpeg_ausente", "dependencia_ausente", "workspace_inacessivel")} == {3}


# --- real processes: spawn ProcessPool (--jobs 2) --------------------------------------------------------------

def test_jobs_2_spawn_pool_subprocess(sb, skill_dir):
    """A real `python prepare.py --jobs 2` run: workers are spawned processes that load the fake
    stages through VA_STAGES_SHIM (env vars of the sandbox are inherited)."""
    shim = sb.tmp / "shim_stages.py"
    shim.write_text(FAKE_SRC, encoding="utf-8")
    v3 = fake_video(sb.vids / "terceiro" / "Aula 3.mov", n_frames=12)
    fake_vtt(sb.vids / "terceiro" / "Aula 3.vtt")
    req = write_request(sb, [sb.v1, sb.v2, v3], options={"roles": "inline"})
    env = {**os.environ, "VA_STAGES_SHIM": str(shim), "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-B", str(skill_dir / "scripts" / "prepare.py"), "--request", str(req),
                           "--jobs", "2"], capture_output=True, env=env, timeout=300)
    out = proc.stdout.decode("utf-8")
    err = proc.stderr.decode("utf-8", "replace")
    if proc.returncode == 3:
        pytest.skip(f"environment without ffmpeg/numpy/Pillow: {out[-300:]}")
    assert proc.returncode == 0, err[-3000:] + out[-2000:]
    lines = out.splitlines()
    assert len(lines) == 1
    res = json.loads(lines[0])
    assert err.splitlines()[0] == f"run_dir: {res['run_dir']}"
    assert [v["slug"] for v in res["workflow_args"]["videos"]] == ["repasse-fluxo-de-carga-20260818-160841",
                                                                   "venda-zero-clientes", "aula-3"]
    assert contracts.check_workflow_args(res["workflow_args"]) == []
    assert "2 in parallel" in err
    assert sorted(calls(sb)) == sorted(["cues", "frames", "batches"] * 3)
    assert not list((skill_dir / "scripts").rglob("__pycache__"))
