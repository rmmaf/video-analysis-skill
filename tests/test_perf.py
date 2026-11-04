"""Performance of the frames stage (design-deterministic.md section 9, "Performance").
Run with --run-slow (or VA_RUN_SLOW=1); skips when a fixture video is missing.

- D = reference decode: ffmpeg -skip_frame:v noref -i X -an -vf fps=2,scale=640:360,format=gray -f null -
- metric_view (28 min, 1080p60, B-frames): cold run <= 1.8 D + 15 s and <= 0.15 x realtime.
- Teams 1080p16 (da_1608): <= 0.06 x realtime.
- peak working set of the Python process <= 400 MB (the stage runs in a child process).
- warm cache: prepare.py end to end on a fully cached video <= 2 s (no ffprobe, no tool check,
  no frame-engine import, no worker process).
Measured numbers are printed (pytest -s) and recorded in tests/data/calibration.md.
"""
import json
import subprocess
import sys
import time

import pytest

from conftest import SCRIPTS_DIR, resolve_fixture_glob
from va.ffmpeg_io import CREATE_NO_WINDOW, find_tool

pytestmark = pytest.mark.slow

CHILD = r"""
import sys, time, json, ctypes
from ctypes import wintypes
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from va.probe import probe_video
from va.extract import run_frames_stage

class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]

def peak_mb():
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    fn = k32.K32GetProcessMemoryInfo
    fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    fn.restype = wintypes.BOOL
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    if not fn(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        return -1.0
    return pmc.PeakWorkingSetSize / 2 ** 20

info = probe_video(sys.argv[2])
t = time.perf_counter()
doc = run_frames_stage(sys.argv[2], info, {}, sys.argv[3])
print(json.dumps({"elapsed": time.perf_counter() - t, "peak_mb": peak_mb(), "frames": len(doc["frames"]),
                  "samples": doc["n_samples"], "duration": info.duration_s}))
"""


def _video(fixtures_db, name):
    hits = resolve_fixture_glob(fixtures_db["fixtures"][name]["video"])
    if not hits:
        pytest.skip(f"fixture {name} not found")
    return hits[0]


def _reference_decode(src) -> float:
    cmd = [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-skip_frame:v", "noref", "-i", str(src),
           "-an", "-vf", "fps=2,scale=640:360,format=gray", "-f", "null", "-"]
    t = time.perf_counter()
    subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, check=True, creationflags=CREATE_NO_WINDOW)
    return time.perf_counter() - t


def _stage_in_child(src, out) -> dict:
    proc = subprocess.run([sys.executable, "-B", "-c", CHILD, str(SCRIPTS_DIR), str(src), str(out)],
                          capture_output=True, text=True, encoding="utf-8", check=True, creationflags=CREATE_NO_WINDOW)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_metric_view_1080p60(fixtures_db, tmp_path):
    src = _video(fixtures_db, "metric_view")
    exp = fixtures_db["fixtures"]["metric_view"]["expect"]
    d = _reference_decode(src)
    r = _stage_in_child(src, tmp_path / "frames-metric")
    print(f"\nmetric_view: D={d:.1f}s stage={r['elapsed']:.1f}s ({r['elapsed'] / r['duration']:.3f}x realtime, "
          f"{r['elapsed'] / d:.2f} D) peak={r['peak_mb']:.0f} MB frames={r['frames']} samples={r['samples']}")
    assert r["elapsed"] <= exp["max_decode_multiple"] * d + exp["max_decode_overhead_s"]
    assert r["elapsed"] <= exp["max_realtime_factor"] * r["duration"]
    assert 0 < r["peak_mb"] <= exp["max_rss_mb"]


def test_teams_1080p16(fixtures_db, tmp_path):
    src = _video(fixtures_db, "repasse_da_1608")
    exp = fixtures_db["fixtures"]["repasse_da_1608"]["expect"]
    r = _stage_in_child(src, tmp_path / "frames-teams")
    print(f"\nda_1608: stage={r['elapsed']:.1f}s ({r['elapsed'] / r['duration']:.3f}x realtime) peak={r['peak_mb']:.0f} MB "
          f"frames={r['frames']}")
    assert r["elapsed"] <= exp["max_realtime_factor"] * r["duration"]
    assert 0 < r["peak_mb"] <= 400


def test_warm_cache_prepare_cli(fixtures_db, tmp_path):
    """design §9: warm cache <= 2 s per video, through the real CLI (cache-only mode, fresh
    workspace): the second run must hit every stage. Measured 0.6 s (was 4.5 s before the
    cached probe.json, the need-based tool check and the numpy-free frame params)."""
    src = _video(fixtures_db, "venda_zero_clientes")
    home = tmp_path / "h"
    cmd = [sys.executable, "-B", str(SCRIPTS_DIR / "prepare.py"), str(src), "--home", str(home), "--quiet", "--jobs", "2"]
    cold = subprocess.run(cmd, capture_output=True, timeout=600, creationflags=CREATE_NO_WINDOW)
    assert cold.returncode == 0, cold.stdout.decode("utf-8", "replace")[-2000:]
    t = time.perf_counter()
    warm = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
    elapsed = time.perf_counter() - t
    env = json.loads(warm.stdout.decode("utf-8").splitlines()[-1])
    v = env["videos"][0]
    print(f"\nwarm cache: {elapsed:.2f} s for {v['n_frames']} frames / {v['n_batches']} batches")
    assert warm.returncode == 0 and env["ok"] and v["cache_hit"] == {"cues": True, "frames": True, "batches": True}
    assert v["timing_s"] == {}
    assert elapsed <= fixtures_db["fixtures"]["metric_view"]["expect"]["max_warm_cache_s"]
