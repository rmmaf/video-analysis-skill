"""The transcription environment of va/environment.py: where it lives, its status (the probe runs this
interpreter over a fake site-packages on PYTHONPATH, so no WhisperX is needed), the PyTorch build by the NVIDIA
driver, uv, NLTK's punkt (an in-memory zip) and the installation with a fake runner: nothing is installed or
downloaded here."""
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from va import TRANSCRIBE_DEFAULTS, environment as E

PUNKT_FILES = TRANSCRIBE_DEFAULTS["punkt_files"]
VERSIONS = {"whisperx": "3.8.6", "torch": "2.8.0+cpu", "ctranslate2": "4.6.0", "faster-whisper": "1.2.0",
            "pyannote.audio": "4.0.1", "huggingface-hub": "0.35.3", "nltk": "3.9.1"}
MODULES = ("whisperx", "torch", "faster_whisper", "pyannote.audio", "nltk", "huggingface_hub")
TURBO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
ALIGN_PT = TRANSCRIBE_DEFAULTS["align_models"]["pt"]


def fake_site(root: Path, versions=None, modules=MODULES, token=True, cached=(TURBO, ALIGN_PT)) -> Path:
    """A folder for PYTHONPATH that looks like the transcription environment to the probe."""
    root.mkdir(parents=True, exist_ok=True)
    for dist, ver in {**VERSIONS, **(versions or {})}.items():
        if ver is None:
            continue
        info = root / f"{dist.replace('-', '_').replace('.', '_')}-{ver}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {dist}\nVersion: {ver}\n", encoding="utf-8")
    for mod in modules:
        pkg = root.joinpath(*mod.split("."))
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
    if "huggingface_hub" in modules:
        (root / "huggingface_hub" / "__init__.py").write_text(
            f"def get_token():\n    return {'hf_segredo' if token else None!r}\n"
            f"def try_to_load_from_cache(repo, name):\n    return '/cache/' + name if repo in {list(cached)!r} else None\n",
            encoding="utf-8")
    return root


def punkt_tree(root: Path, languages=("portuguese", "english")) -> Path:
    for lang in languages:
        (root / lang).mkdir(parents=True, exist_ok=True)
        for f in PUNKT_FILES:
            (root / lang / f).write_text("x", encoding="utf-8")
    return root


@pytest.fixture
def env_dir(tmp_path, monkeypatch):
    d = tmp_path / "whisperx-env"
    monkeypatch.setenv("VA_WHISPERX_ENV", str(d))
    monkeypatch.delenv("VA_TORCH_BACKEND", raising=False)
    return d


def status(tmp_path, monkeypatch, gpu=None, punkt=True, **site):
    monkeypatch.setenv("PYTHONPATH", str(fake_site(tmp_path / "site", **site)))
    if punkt:
        punkt_tree(E.nltk_dir() / "tokenizers" / "punkt_tab")
    return E.transcription_status(python=sys.executable, gpu=gpu)


# --- where it lives ------------------------------------------------------------------------------------------

def test_env_dir_default_override_and_login(tmp_path, monkeypatch):
    monkeypatch.delenv("VA_WHISPERX_ENV", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    assert E.whisperx_env_dir() == tmp_path / "user" / "whisperx-env"
    monkeypatch.setenv("VA_WHISPERX_ENV", f'"{tmp_path / "outro"}"')
    assert E.whisperx_env_dir() == tmp_path / "outro" and E.nltk_dir() == tmp_path / "outro" / "nltk_data"
    exe = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    assert E.whisperx_python() == tmp_path / "outro" / exe[0] / exe[1]
    cmd = E.login_command()
    assert cmd.endswith(" auth login") and ("hf.exe" in cmd if os.name == "nt" else "/bin/hf" in cmd)


# --- the status ----------------------------------------------------------------------------------------------

def test_status_absent(env_dir):
    st = E.transcription_status()
    assert (st["ok"], st["state"], st["problem"]) == (False, "absent", "absent")
    assert st["detail"] == f"no transcription environment at {env_dir.as_posix()}"


def test_status_ok_cpu(tmp_path, monkeypatch, env_dir):
    st = status(tmp_path, monkeypatch)
    assert (st["ok"], st["state"], st["ready"], st["version"], st["backend"], st["device"]) == \
        (True, "ok", True, "3.8.6", "cpu", "cpu")
    assert st["default_model"] == "large-v3-turbo" and st["token"] is True and st["models"][TURBO] is True
    assert st["detail"] == f"whisperx 3.8.6 in {env_dir.as_posix()} (cpu; model large-v3-turbo)"
    assert "hf_segredo" not in json.dumps(st)                                  # the token itself never comes out


def test_status_gpu_picks_the_model_by_memory(tmp_path, monkeypatch, env_dir):
    big = {"name": "RTX 4090", "driver": "581.57", "driver_major": 581, "vram_mb": 24564, "compute_cap": 8.9}
    st = status(tmp_path, monkeypatch, gpu=big, versions={"torch": "2.8.0+cu128"})
    assert (st["device"], st["default_model"], st["backend"]) == ("cuda", "large-v3", "cu128")
    assert st["ready"] is False                                                # large-v3 is not in the cache
    small = {**big, "name": "GTX 1650", "vram_mb": 4096}
    st = E.transcription_status(python=sys.executable, gpu=small)
    assert (st["device"], st["default_model"], st["ready"]) == ("cuda", "large-v3-turbo", True)
    st = E.transcription_status(python=sys.executable, gpu=None)               # a CUDA build, but no GPU now
    assert (st["device"], st["default_model"]) == ("cpu", "large-v3-turbo")


def test_status_gpu_older_than_volta_runs_on_the_cpu(tmp_path, monkeypatch, env_dir):
    """A CUDA build (installed by hand, or before the check) on a Pascal card: cu128 has no kernels for it and
    CTranslate2's float16 needs 7.0, so the device is the CPU and the model large-v3-turbo, despite 8 GB."""
    pascal = {"name": "GTX 1080", "driver": "581.57", "driver_major": 581, "vram_mb": 8192, "compute_cap": 6.1}
    st = status(tmp_path, monkeypatch, gpu=pascal, versions={"torch": "2.8.0+cu128"})
    assert (st["device"], st["default_model"], st["gpu"]) == ("cpu", "large-v3-turbo", None)


@pytest.mark.parametrize("site, punkt, problem, needle", [
    ({"versions": {"whisperx": "3.7.4"}}, True, "version", "has whisperx 3.7.4, and the skill uses 3.8.6"),
    ({"versions": {"whisperx": None}}, True, "version", "has whisperx of an unknown version"),
    ({"modules": ("whisperx", "torch", "nltk", "huggingface_hub")}, True, "modules", "lacks faster_whisper, pyannote.audio"),
    ({}, False, "punkt", "NLTK's sentence tokenizer is missing in"),
])
def test_status_broken(tmp_path, monkeypatch, env_dir, site, punkt, problem, needle):
    st = status(tmp_path, monkeypatch, punkt=punkt, **site)
    assert (st["ok"], st["state"], st["problem"]) == (False, "broken", problem) and needle in st["detail"]


def test_status_without_token_or_models(tmp_path, monkeypatch, env_dir):
    st = status(tmp_path, monkeypatch, token=False, cached=())
    assert (st["ok"], st["token"], st["ready"]) == (True, False, False)


def test_status_python_that_does_not_run(tmp_path, env_dir):
    fake = tmp_path / "python.exe"
    fake.write_bytes(b"not a program")
    st = E.transcription_status(python=fake)
    assert (st["ok"], st["problem"]) == (False, "python") and "did not run" in st["detail"]


def test_intel_mac_is_unsupported(monkeypatch, env_dir):
    monkeypatch.setattr(E.sys, "platform", "darwin")
    monkeypatch.setattr(E.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(E.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "0\n", ""))
    ok, why = E.platform_supported()
    assert not ok and "Intel" in why
    st = E.transcription_status()
    assert (st["state"], st["problem"]) == ("unsupported", "platform")
    with pytest.raises(E.SetupError, match="Intel"):
        E.install_transcription(runner=lambda cmd, t: pytest.fail("nothing runs"))
    monkeypatch.setattr(E.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "1\n", ""))
    assert E.platform_supported() == (True, "")                                # Apple silicon under Rosetta


# --- the GPU and the PyTorch build ---------------------------------------------------------------------------

@pytest.mark.parametrize("driver, backend", [(581, "cu128"), (570, "cu128"), (566, "cu126"), (528, "cu126"),
                                             (472, "cpu")])
def test_torch_backend_by_driver(monkeypatch, driver, backend):
    monkeypatch.delenv("VA_TORCH_BACKEND", raising=False)
    monkeypatch.setattr(E.sys, "platform", "win32")
    assert E.torch_backend({"driver_major": driver, "vram_mb": 8000}) == backend


@pytest.mark.parametrize("cc, driver, backend", [(6.1, 581, "cpu"), (5.2, 581, "cpu"), (6.0, 570, "cpu"),
                                                 (6.1, 566, "cpu"), (5.2, 566, "cpu"), (7.0, 581, "cu128"),
                                                 (8.6, 581, "cu128"), (7.5, 566, "cu126"), (None, 581, "cu128")])
def test_torch_backend_by_compute_capability(monkeypatch, cc, driver, backend):
    """Maxwell and Pascal (below 7.0) with any driver: the CPU build, since cu128 lacks sm_50-sm_61 and
    CTranslate2's float16 needs 7.0; an unknown capability (an old nvidia-smi) keeps the driver's answer."""
    monkeypatch.delenv("VA_TORCH_BACKEND", raising=False)
    monkeypatch.setattr(E.sys, "platform", "win32")
    assert E.torch_backend({"driver_major": driver, "vram_mb": 8192, "compute_cap": cc}) == backend


def test_torch_backend_without_gpu_on_mac_and_forced(monkeypatch):
    monkeypatch.delenv("VA_TORCH_BACKEND", raising=False)
    monkeypatch.setattr(E.sys, "platform", "win32")
    assert E.torch_backend(None) == "cpu"
    monkeypatch.setattr(E.sys, "platform", "darwin")
    assert E.torch_backend(None) is None                                       # PyPI's build (CPU and Metal)
    monkeypatch.setenv("VA_TORCH_BACKEND", "cu126")
    assert E.torch_backend(None) == "cu126"


def test_nvidia_info_parses_nvidia_smi(monkeypatch):
    monkeypatch.setattr(E.shutil, "which", lambda name: "C:/nv/nvidia-smi.exe" if name == "nvidia-smi" else None)
    answers = {"name,driver_version,memory.total,compute_cap": (0, "NVIDIA GeForce RTX 3060, 566.36, 12288, 8.6\n")}

    def run(cmd, **k):
        code, out = answers.get(cmd[1].split("=", 1)[1], (2, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")
    monkeypatch.setattr(E.subprocess, "run", run)
    assert E.nvidia_info() == {"name": "NVIDIA GeForce RTX 3060", "driver": "566.36", "driver_major": 566,
                               "vram_mb": 12288, "compute_cap": 8.6}
    answers.clear()
    answers["name,driver_version,memory.total"] = (0, "Quadro P2000, 472.12, 5120\n")      # an old driver: no compute_cap
    assert E.nvidia_info()["driver_major"] == 472 and E.nvidia_info()["compute_cap"] is None
    monkeypatch.setattr(E.shutil, "which", lambda name: None)
    monkeypatch.setattr(E.os, "name", "posix")
    assert E.nvidia_info() is None


def test_find_uv(monkeypatch, tmp_path):
    monkeypatch.setattr(E.shutil, "which", lambda name: "C:/bin/uv.exe" if name == "uv" else None)
    assert E.find_uv() == ["C:/bin/uv.exe"]
    monkeypatch.setattr(E.shutil, "which", lambda name: None)
    monkeypatch.setattr(E.importlib.util, "find_spec", lambda name: object() if name == "uv" else None)
    assert E.find_uv() == [sys.executable, "-m", "uv"]
    monkeypatch.setattr(E.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert E.find_uv() is None
    exe = tmp_path / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert E.find_uv() == [str(exe)]


# --- NLTK's punkt ---------------------------------------------------------------------------------------------

def punkt_zip(languages=("portuguese", "english"), extra=()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for lang in languages:
            for f in PUNKT_FILES:
                z.writestr(f"punkt_tab/{lang}/{f}", "x")
        for name in extra:
            z.writestr(name, "x")
    return buf.getvalue()


class Opener:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, url, timeout):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return io.BytesIO(a)


def test_punkt_download_reuse_and_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(E.time, "sleep", lambda s: None)
    nd = tmp_path / "nltk_data"
    op = Opener(ConnectionResetError("reset"), punkt_zip())
    assert E.prepare_punkt(nd, opener=op) == "downloaded" and op.calls == 2     # a reset connection: tried again
    assert E.punkt_complete(nd / "tokenizers" / "punkt_tab") and (nd / "tokenizers" / "punkt_tab" / ".complete").is_file()
    assert not (nd / "tokenizers" / "punkt_tab.downloading").exists()
    assert E.prepare_punkt(nd, opener=Opener()) == "already in place"
    (nd / "tokenizers" / "punkt_tab" / ".complete").unlink()
    assert E.prepare_punkt(nd, opener=Opener()) == "manual installation adopted"
    assert E.prepare_punkt(nd, opener=Opener(punkt_zip()), force=True) == "downloaded"


@pytest.mark.parametrize("data, needle", [
    (punkt_zip(languages=("portuguese",)), "lacks portuguese, english whole"),
    (punkt_zip(extra=("../fora.txt",)), "paths outside its folder"),
    (b"not a zip", "BadZipFile"),
])
def test_punkt_refuses_bad_packages(tmp_path, data, needle):
    nd = tmp_path / "nltk_data"
    punkt_tree(nd / "tokenizers" / "punkt_tab", languages=("portuguese",))   # an older, partial install
    (nd / "tokenizers" / "punkt_tab" / ".complete").touch()
    with pytest.raises(E.SetupError) as exc:
        E.prepare_punkt(nd, opener=Opener(data))
    assert needle in str(exc.value) and "download https://raw.githubusercontent.com/nltk/nltk_data" in str(exc.value)
    assert not (nd / "tokenizers" / "punkt_tab.downloading").exists() and not (tmp_path / "fora.txt").exists()


# --- the installation ------------------------------------------------------------------------------------------

def ok_status(env_dir, **fields):
    env = env_dir.as_posix()
    return {"id": "transcription", "ok": True, "ready": True, "state": "ok", "problem": None, "env": env,
            "python": env + "/Scripts/python.exe", "version": "3.8.6", "torch": "2.8.0+cpu", "backend": "cpu",
            "device": "cpu", "gpu": None, "default_model": "large-v3-turbo", "token": False, "models": {},
            "nltk_data": env + "/nltk_data", "login_command": "hf auth login", "detail": "whisperx 3.8.6 in " + env,
            **fields}


class Machine:
    """A fake machine for install_transcription: the runner, the environment's status and the proof's output."""

    def __init__(self, env_dir, state="absent", proof=None, check_codes=(0,)):
        self.env_dir, self.state, self.cmds = env_dir, state, []
        self.proof = proof or {"ok": True, "device": "cpu", "model_ok": True, "align": {"pt": "ok"}, "diarization": "no_token"}
        self.check_codes = list(check_codes)

    def status(self, *a, **k):
        if self.state == "absent":
            return {**ok_status(self.env_dir), "ok": False, "ready": False, "state": "absent", "problem": "absent",
                    "torch": None, "detail": "no transcription environment"}
        if self.state == "modules":
            return {**ok_status(self.env_dir), "ok": False, "state": "broken", "problem": "modules", "detail": "lacks whisperx"}
        if self.state == "python":
            return {**ok_status(self.env_dir), "ok": False, "state": "broken", "problem": "python", "detail": "exit 9"}
        return ok_status(self.env_dir, torch=self.state if self.state.startswith("2.8") else "2.8.0+cpu")

    def __call__(self, cmd, timeout):
        self.cmds.append(cmd)
        if "venv" in cmd and "--python" in cmd:                       # uv venv (uv's own command or python -m uv)
            py = E.whisperx_python(self.env_dir)
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_bytes(b"")
            (self.env_dir / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
            self.state = "modules"
        elif "pip" in cmd and "install" in cmd and "--python" in cmd:  # uv pip install into the environment
            self.state = "ok"
        elif "--check" in cmd:
            code = self.check_codes.pop(0)
            return code, "baixando modelo...\n" + json.dumps({**self.proof, "ok": code == 0}) + "\n"
        return 0, "ok\n"


@pytest.fixture
def machine(env_dir, monkeypatch):
    m = Machine(env_dir)
    monkeypatch.setattr(E, "transcription_status", m.status)
    monkeypatch.setattr(E, "prepare_punkt", lambda nd, opener=None: "downloaded")
    return m


def test_install_from_scratch(machine, env_dir):
    r = E.install_transcription(runner=machine, uv=["uv"], backend="cpu")
    venv, pip, check = machine.cmds
    assert venv == ["uv", "venv", "--python", "3.12", str(env_dir)]
    assert pip[:5] == ["uv", "pip", "install", "--python", str(E.whisperx_python(env_dir))]
    assert pip[5:7] == ["--torch-backend", "cpu"] and pip[7:] == ["whisperx==3.8.6", "torch==2.8.0", "torchaudio==2.8.0",
                                                                   "torchvision==0.23.0"]
    assert check[1:4] == ["-B", str(E.TRANSCRIBE_SCRIPT), "--check"] and check[check.index("--model") + 1] == "large-v3-turbo"
    assert "--diarize" in check and check[check.index("--align") + 1] == "pt"
    assert r["done"][0].startswith("create the environment") and "NLTK punkt: downloaded" in r["done"]
    assert r["done"][-1] == "downloaded and loaded large-v3-turbo and the alignment of pt on cpu"
    assert r["check"]["diarization"] == "no_token" and r["status"]["ok"]


def test_install_again_only_proves(machine, env_dir, monkeypatch):
    machine.state = "ok"
    py = E.whisperx_python(env_dir)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    monkeypatch.setattr(E, "prepare_punkt", lambda nd, opener=None: "already in place")
    r = E.install_transcription(runner=machine, uv=["uv"], backend="cpu")
    assert [c for c in machine.cmds if "--check" not in c] == [] and len(r["done"]) == 1


def test_install_on_mac_uses_pypi_torch(machine, env_dir):
    E.install_transcription(runner=machine, uv=["uv"], backend=None)
    assert "--torch-backend" not in machine.cmds[1]


def test_install_switches_the_torch_build(machine, env_dir):
    machine.state = "2.8.0+cpu"
    py = E.whisperx_python(env_dir)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    E.install_transcription(runner=machine, uv=["uv"], backend="cu128")
    pip = machine.cmds[0]
    assert pip[5:7] == ["--torch-backend", "cu128"] and "--reinstall-package" in pip


def test_install_reinstalls_once_when_whisperx_does_not_load(machine, env_dir):
    machine.check_codes = [3, 0]
    E.install_transcription(runner=machine, uv=["uv"], backend="cpu")
    kinds = ["check" if "--check" in c else c[1] for c in machine.cmds]
    assert kinds == ["venv", "pip", "check", "pip", "check"] and "--reinstall" in machine.cmds[3]


def test_install_proof_failure(machine):
    machine.check_codes = [4]
    machine.proof = {"model_ok": False, "error": "large-v3-turbo: LocalEntryNotFoundError: connection reset",
                     "align": {"pt": "ok"}}
    with pytest.raises(E.SetupError) as exc:
        E.install_transcription(runner=machine, uv=["uv"], backend="cpu")
    assert "connection reset" in str(exc.value) and "resumes when you run it again" in str(exc.value)


def test_install_without_uv(machine, env_dir, monkeypatch):
    """No uv anywhere: pip installs it for this Python, and ``python -m uv`` runs it."""
    monkeypatch.setattr(E, "find_uv", lambda: None)
    E.install_transcription(runner=machine, backend="cpu", base_python="C:/Py/python.exe")
    assert machine.cmds[0] == ["C:/Py/python.exe", "-m", "pip", "install", "--user", "--disable-pip-version-check", "uv"]
    assert machine.cmds[1][:4] == ["C:/Py/python.exe", "-m", "uv", "venv"]


def test_install_uv_refused_points_to_the_package_manager(machine, monkeypatch):
    monkeypatch.setattr(E, "find_uv", lambda: None)

    def runner(cmd, timeout):
        return 1, "error: externally-managed-environment\n"
    with pytest.raises(E.SetupError) as exc:
        E.install_transcription(runner=runner, backend="cpu")
    assert "externally-managed-environment" in str(exc.value) and "install uv" in str(exc.value)


def test_install_replaces_an_environment_whose_python_does_not_run(machine, env_dir):
    machine.state = "python"
    py = E.whisperx_python(env_dir)
    py.parent.mkdir(parents=True)
    py.write_bytes(b"")
    (env_dir / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    r = E.install_transcription(runner=machine, uv=["uv"], backend="cpu")
    assert r["done"][0].startswith("remove the environment") and machine.cmds[0][1] == "venv"


def test_install_refuses_a_folder_that_is_not_an_environment(machine, env_dir):
    env_dir.mkdir()
    (env_dir / "meu arquivo.txt").write_text("x", encoding="utf-8")
    with pytest.raises(E.SetupError, match="is not a Python environment"):
        E.install_transcription(runner=machine, uv=["uv"], backend="cpu")
    assert (env_dir / "meu arquivo.txt").exists()


# --- separating the speakers -----------------------------------------------------------------------------------

@pytest.mark.parametrize("st, access, state, ok", [
    ({"ok": False}, None, "unavailable", False),
    ({"token": False}, None, "no_token", False),
    ({}, "no_access", "no_access", False),
    ({}, "ok", "download", False),
    ({"models": {TRANSCRIBE_DEFAULTS["diarize_model"]: True}}, "ok", "ok", True),
    ({"models": {TRANSCRIBE_DEFAULTS["diarize_model"]: True}}, "offline: ConnectionError: x", "ok", True),
    ({}, "offline: ConnectionError: x", "download", False),
])
def test_diarization_status(env_dir, monkeypatch, st, access, state, ok):
    base = ok_status(env_dir, token=True, models={})
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, f"{access}\n", "")
    monkeypatch.setattr(E.subprocess, "run", run)
    ds = E.diarization_status({**base, **st})
    assert (ds["state"], ds["ok"]) == (state, ok)
    assert bool(calls) == (access is not None) and (not calls or calls[0][-1] == TRANSCRIBE_DEFAULTS["diarize_model"])
    if state == "no_access":
        assert E.DIARIZE_PAGE in ds["detail"]


class _HTTPError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.response = type("Response", (), {"status_code": status_code})() if status_code else None


@pytest.mark.parametrize("exc, out", [
    (None, "ok"),
    (type("GatedRepoError", (Exception,), {})("Access to model pyannote/x is restricted"), "no_access"),
    (_HTTPError("401 Client Error: Unauthorized for url: https://huggingface.co/api/models/pyannote/x"), "no_access"),
    (_HTTPError("Client Error. (Request ID: Root=1-6a1b)", 403), "no_access"),
    # the digits of an id, a proxy's 407: offline, not a refused access
    (_HTTPError("Max retries exceeded (Request ID: Root=1-4013ab)"),
     "offline: _HTTPError: Max retries exceeded (Request ID: Root=1-4013ab)"),
    (OSError("Tunnel connection failed: 407 Proxy Authentication Required"),
     "offline: OSError: Tunnel connection failed: 407 Proxy Authentication Required"),
])
def test_access_probe_reads_the_http_codes(monkeypatch, capsys, exc, out):
    """The probe runs in the environment's Python; here, with this one and a stand-in auth_check."""
    def auth_check(repo):
        assert repo == "pyannote/x"
        if exc is not None:
            raise exc
    monkeypatch.setitem(sys.modules, "huggingface_hub", type(sys)("huggingface_hub"))
    monkeypatch.setattr(sys.modules["huggingface_hub"], "auth_check", auth_check, raising=False)
    monkeypatch.setattr(sys, "argv", ["-c", "pyannote/x"])
    exec(compile(E._ACCESS_PROBE, "<access probe>", "exec"), {"__name__": "__main__"})
    assert capsys.readouterr().out.strip() == out
