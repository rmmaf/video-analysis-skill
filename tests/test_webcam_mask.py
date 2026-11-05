"""scripts/webcam_mask.py needs DeepFace, in its own environment, so these tests run it with stand-ins for
cv2, numpy and deepface. They cover the YuNet score that reaches DeepFace (yunet_score_threshold), which
the script checks before loading DeepFace."""
import os
import subprocess
import sys

import pytest

from conftest import SCRIPTS_DIR

# the stand-in for deepface prints, when imported, the score that DeepFace would read
STUBS = {
    "cv2.py": "",
    "numpy.py": "",
    "deepface/__init__.py": "import os, sys\n"
                            "print('deepface score=' + os.environ['yunet_score_threshold'], file=sys.stderr)\n"
                            "DeepFace = None\n",
}


@pytest.fixture
def run(tmp_path):
    """Runs webcam_mask.py on a video that does not exist, with the given yunet_score_threshold (None:
    without the variable); returns the exit code and the error output."""
    for name, text in STUBS.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def run_script(score):
        env = {k: v for k, v in os.environ.items() if k.lower() != "yunet_score_threshold"}
        env.update(PYTHONPATH=str(tmp_path), PYTHONIOENCODING="utf-8")
        if score is not None:
            env["yunet_score_threshold"] = score
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS_DIR / "webcam_mask.py"), str(tmp_path / "none.mp4")],
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
        return r.returncode, r.stderr

    return run_script


@pytest.mark.parametrize("score, seen", [(None, "0.85"), ("0.9", "0.9"), (" 0.9 ", "0.9"), ("0.75", "0.75"),
                                         ("0.999", "0.999")])
def test_valid_score_reaches_deepface(run, score, seen):
    """Without the variable DeepFace reads 0.85, not its own 0.9; a valid value reaches it as a plain
    number, and the script goes on to open the video."""
    code, err = run(score)
    assert f"deepface score={seen}\n" in err
    assert code == 1 and "esperava um video, achei 0" in err


@pytest.mark.parametrize("score", ["", "abc", "0,85", "nan", "inf", "-inf", "-0.85", "0", "0.74", "1", "1.0",
                                   "85"])
def test_invalid_score_stops_before_deepface(run, score):
    """Not a number, not finite, below 0.75, or 1 and above: a clear error, before DeepFace is loaded. A low
    score lets in false faces, which become ignored regions when they move; at 1 YuNet finds no face."""
    code, err = run(score)
    assert code == 1
    assert err == f"yunet_score_threshold={score!r}: use um numero com ponto, de 0.75 ate menos de 1" \
                  " (sem a variavel, 0.85)\n"
