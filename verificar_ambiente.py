"""Old name of check_environment.py (before the rename): runs it with the same arguments, including
--instalar-agentes and --aplicar-permissoes, and returns its exit code. The output is the new one:
[MISSING]/[WARN] and, with --json, the English keys (ready, items, state...)."""
import runpy
import sys
from pathlib import Path

sys.dont_write_bytecode = True
if str(Path(__file__).resolve().parent / "scripts") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

import va  # noqa: E402  (its __init__ parses on old Pythons; the rest of the skill needs 3.10)

if va.old_python():   # the same line check_environment.py would print, before runpy compiles it
    print(va.old_python())
    sys.exit(1)

target = Path(__file__).resolve().with_name("check_environment.py")
sys.argv[0] = str(target)
runpy.run_path(str(target), run_name="__main__")
