# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse hook for Bash: block bare `python`/`python3`/`py` (Microsoft Store stub on Windows).

stdin  = hook JSON ({"tool_input": {"command": ...}})
exit 2 = blocked (message on stderr); exit 0 = allowed. FAIL-OPEN: any internal error exits 0 and is
logged to <data>/python-guard-errors.log.
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

_SPLIT = re.compile(r"&&|\|\||;|\||\n|\r|\$\(|`")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SKIP = {"sudo", "env", "time", "exec"}
_BARE = re.compile(r"^(python(\d+(\.\d+)*)?|py)(\.exe)?$", re.I)


def _first_word(simple: str) -> str | None:
    for w in simple.split():
        w = w.strip("\"'()({")
        if not w or _ASSIGN.match(w) or w.lower() in _SKIP:
            continue
        return w
    return None


def bad_word(cmd: str) -> str | None:
    for simple in _SPLIT.split(cmd):
        w = _first_word(simple)
        if not w:
            continue
        low = w.replace("\\", "/").lower()
        base = low.rsplit("/", 1)[-1]
        if "/" not in low:
            if _BARE.match(base):
                return w
        elif "/windowsapps/" in low and _BARE.match(base):
            return w
    return None


def _log(exc: BaseException) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notes_lib import platform as plat

        d = plat.default_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "python-guard-errors.log", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {''.join(traceback.format_exception(exc))}\n")
    except Exception:
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
        evt = json.loads(raw) if raw.strip() else {}
        cmd = (evt.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str):
            return 0
        w = bad_word(cmd)
        if w:
            sys.stderr.write(
                f'notes plugin: don\'t call "{w}" directly — on Windows it can be the Microsoft Store stub. '
                'Use "uv run python …" (or "uv run --with PKG python …"); '
                'for scripts with a PEP-723 header use "uv run script.py".\n'
            )
            return 2
        return 0
    except BaseException as e:  # noqa: BLE001  (fail-open by design)
        if isinstance(e, SystemExit):
            raise
        _log(e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
