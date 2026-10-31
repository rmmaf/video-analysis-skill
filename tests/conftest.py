"""Shared pytest setup for the video-analysis skill.

- Puts scripts/ on sys.path, so tests import ``va.*`` directly.
- Tests marked ``real`` or ``slow`` are skipped unless --run-real / --run-slow is given
  (or VA_RUN_REAL=1 / VA_RUN_SLOW=1).
- Fixtures: ``real_fixture`` (loads tests/fixtures.json, resolves the globs, skips when a
  file is missing), ``va_home`` (isolated workspace via VIDEO_ANALYSIS_HOME), ``skill_dir``,
  ``data_dir``.
"""
import sys

sys.dont_write_bytecode = True

import glob  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
DATA_DIR = TESTS_DIR / "data"
FIXTURES_JSON = TESTS_DIR / "fixtures.json"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_MARKERS = {
    "real": ("--run-real", "VA_RUN_REAL", "needs the real videos/VTTs of tests/fixtures.json"),
    "slow": ("--run-slow", "VA_RUN_SLOW", "long performance test"),
}


def pytest_addoption(parser):
    for name, (option, env, what) in _MARKERS.items():
        parser.addoption(option, action="store_true", default=False,
                         help=f"run tests marked {name} ({what}); same as {env}=1")


def pytest_configure(config):
    for name, (option, env, what) in _MARKERS.items():
        config.addinivalue_line("markers", f"{name}: {what} (run with {option} or {env}=1)")


def pytest_collection_modifyitems(config, items):
    for name, (option, env, _what) in _MARKERS.items():
        if config.getoption(option) or os.environ.get(env, "").strip() == "1":
            continue
        skip = pytest.mark.skip(reason=f"{name} test: use {option} or {env}=1")
        for item in items:
            if name in item.keywords:
                item.add_marker(skip)


def pytest_sessionfinish(session, exitstatus):
    """Safety net for runs without -B: no bytecode caches may stay in the skill folder."""
    for cache in SKILL_DIR.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def resolve_fixture_glob(pattern: str) -> list[Path]:
    """Matches of a fixtures.json path: only the last component may use * or ?, so the
    folder part is escaped ("[" in folder names stays literal)."""
    folder, name = os.path.split(pattern)
    return [Path(p) for p in sorted(glob.glob(os.path.join(glob.escape(folder), name)))]


@pytest.fixture(scope="session")
def skill_dir() -> Path:
    return SKILL_DIR


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def fixtures_db() -> dict:
    return json.loads(FIXTURES_JSON.read_text(encoding="utf-8"))


@pytest.fixture
def real_fixture(fixtures_db):
    """``real_fixture(name)`` -> the fixture's dict from tests/fixtures.json with "name" added
    and "video"/"vtt" replaced by resolved Paths. Skips the test when a file is missing;
    fails when a glob matches more than one file."""

    def load(name: str) -> dict:
        entry = fixtures_db["fixtures"].get(name)
        if entry is None:
            pytest.fail(f"unknown fixture {name!r}; known: {sorted(fixtures_db['fixtures'])}")
        out = dict(entry, name=name)
        for key in ("video", "vtt"):
            pattern = entry.get(key)
            if not pattern:
                out[key] = None
                continue
            hits = resolve_fixture_glob(pattern)
            if not hits:
                pytest.skip(f"fixture {name}: {key} not found ({pattern})")
            if len(hits) > 1:
                pytest.fail(f"fixture {name}: {key} glob matches {len(hits)} files ({pattern})")
            out[key] = hits[0]
        return out

    return load


@pytest.fixture
def va_home(tmp_path, monkeypatch) -> Path:
    """Isolated, empty workspace: VIDEO_ANALYSIS_HOME points to a fresh folder in tmp_path."""
    home = tmp_path / "va-home"
    home.mkdir()
    monkeypatch.setenv("VIDEO_ANALYSIS_HOME", str(home))
    return home
