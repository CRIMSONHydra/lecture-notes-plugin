# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse hook for Bash|PowerShell: enforce the execution policy while a notes run's guard is active.

stdin  = hook JSON ({"tool_name": ..., "tool_input": {"command": ...}, "cwd": ...})
exit 2 = blocked (message on stderr); exit 0 = allowed.
No active guard: always allowed (the policy never touches ordinary work outside a notes run).
Active guard: FAIL-CLOSED. A policy error blocks the command, because an unchecked command during a run
could reach the student's cloud account. Errors are logged to <data>/exec-guard-errors.log.
Rules: scripts/notes_lib/exec_policy.py; prose for agents: reference/execution-policy.md.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

_HINT = ("Notes are written from the transcript, instructor code and docs, not by re-running the class: "
         "show this command in a fence WITHOUT {run} and give representative output. "
         "See reference/execution-policy.md.")


def _log(data_dir: Path | None, exc: BaseException) -> None:
    try:
        if data_dir is None:
            return
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(data_dir / "exec-guard-errors.log", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {''.join(traceback.format_exception(exc))}\n")
    except Exception:
        pass


def main() -> int:
    data_dir = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notes_lib import guard, platform as plat

        data_dir = plat.default_data_dir()
        raw = sys.stdin.read()
        evt = json.loads(raw) if raw.strip() else {}
        cmd = (evt.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or not guard.load_guard(data_dir):
            return 0
    except BaseException as e:  # noqa: BLE001  (no readable event or guard: nothing to enforce)
        if isinstance(e, SystemExit):
            raise
        _log(data_dir, e)
        return 0
    try:
        from notes_lib import exec_policy

        reason = exec_policy.classify(cmd, cwd=evt.get("cwd"))
    except BaseException as e:  # noqa: BLE001  (fail-closed while a run is guarded)
        if isinstance(e, SystemExit):
            raise
        _log(data_dir, e)
        reason = f"the execution policy could not check this command ({type(e).__name__})"
    if reason:
        sys.stderr.write(f"notes plugin: blocked during a notes run: {reason}. {_HINT}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
