"""Old name of check_environment.py (before the rename): runs it with the same arguments, including
--instalar-agentes and --aplicar-permissoes, and returns its exit code. The output is the new one:
[MISSING]/[WARN] and, with --json, the English keys (ready, items, state...)."""
import runpy
import sys
from pathlib import Path

sys.dont_write_bytecode = True
target = Path(__file__).resolve().with_name("check_environment.py")
sys.argv[0] = str(target)
runpy.run_path(str(target), run_name="__main__")
