# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Self-test for scripts/python_guard_hook.py.  uv run tests/selftest_python_guard.py"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "python_guard_hook.py"
BLOCK = ["python -c x", "python3 script.py", "cd x && python y.py", "FOO=1 python a.py", "py -3 x.py",
         "echo hi | python", "python3.12 a.py", '"C:/Users/x/AppData/Local/Microsoft/WindowsApps/python.exe" a.py',
         "ls\npython a.py", "echo $(python -V)"]
ALLOW = ["uv run python -c x", "uv run --with pandas python x.py", "grep -r python .",
         '"C:/Users/x/AppData/Local/uv/cache/environments-v2/e/Scripts/python.exe" a.py',
         "uv run scripts/qa.py", "./.venv/Scripts/python.exe a.py", "pip list"]


def run(stdin: str) -> int:
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True, text=True).returncode


bad = []
for c in BLOCK:
    if run(json.dumps({"tool_input": {"command": c}})) != 2:
        bad.append(("should block", c))
for c in ALLOW:
    if run(json.dumps({"tool_input": {"command": c}})) != 0:
        bad.append(("should allow", c))
if run("not json{") != 0:
    bad.append(("bad json", ""))
print("FAIL" if bad else "OK", bad if bad else f"{len(BLOCK)+len(ALLOW)+1} cases")
sys.exit(1 if bad else 0)
