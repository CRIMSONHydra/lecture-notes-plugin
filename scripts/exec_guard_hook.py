# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse hook for Bash|PowerShell: gate commands while a notes run's guard is active.

stdin  = hook JSON ({"tool_input": {"command": ...}, "cwd", "agent_id", "agent_type", ...})
exit 2 = blocked (reason on stderr); exit 0 = no objection (Claude Code's normal permission flow applies).

    no active guard, or inside the classifier itself   -> allow (ordinary work is never touched)
    floor (credentials, wsl, sudo, tampering)          -> block, for every caller during a run
    caller is not a notes:* agent                      -> allow (the orchestrator runs plugin scripts)
    fast path (read-only, plugin scripts)              -> allow
    otherwise                                          -> LLM classifier (exec_classifier): allow, deny,
                                                          or "run exactly this instead"; approvals are
                                                          recorded with their cleanup plan for exec_cleanup
FAIL-CLOSED while a run is guarded: an internal error blocks the command and is logged.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

_HINT = ("Show this command in a fence WITHOUT {run} and give representative output from the transcript and "
         "docs. A block is final: do not retry it in another form. See reference/execution-policy.md.")


def _log(data_dir: Path | None, exc: BaseException) -> None:
    try:
        if data_dir is None:
            return
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(data_dir / "exec-guard-errors.log", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {''.join(traceback.format_exception(exc))}\n")
    except Exception:
        pass


def _block(msg: str) -> int:
    sys.stderr.write(f"notes plugin: blocked during a notes run: {msg}\n")
    return 2


def main() -> int:
    data_dir = None
    try:
        if os.environ.get("NOTES_EXEC_CLASSIFIER"):
            return 0
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notes_lib import guard, platform as plat

        data_dir = plat.default_data_dir()
        raw = sys.stdin.read()
        evt = json.loads(raw) if raw.strip() else {}
        cmd = (evt.get("tool_input") or {}).get("command")
        g = guard.load_guard(data_dir) if isinstance(cmd, str) else None
        if not g:
            return 0
    except BaseException as e:  # noqa: BLE001  (no readable event or guard: nothing to enforce)
        if isinstance(e, SystemExit):
            raise
        _log(data_dir, e)
        return 0
    try:
        from notes_lib import exec_classifier as ec, exec_policy as ep

        why = ep.floor(cmd)
        if why:
            return _block(f"{why}. {_HINT}")
        if not str(evt.get("agent_type") or "").startswith("notes:") or ep.fast_allow(cmd):
            return 0
        from notes_lib import layout, platform as plat
        try:
            project = layout.find_project(start=evt.get("cwd"))
        except Exception:  # noqa: BLE001  (no project: no course resources to name)
            project = None
        shell = "powershell" if evt.get("tool_name") == "PowerShell" else (
            "bash (Git Bash on Windows)" if plat.os_kind() == "windows" else "bash")
        item = {"id": "1", "kind": "command", "text": cmd, "cwd": evt.get("cwd"),
                "scripts": ep.script_files(cmd, evt.get("cwd"))}           # judge the code, not its file name
        root = str(project) if project else ""
        workspace = {"project": root, "plugin_root": str(ep.PLUGIN_ROOT),
                     "writable": list(g.get("allow") or []),             # the job's own output folders
                     "read_only": [p for p in g.get("protect") or [] if p.rstrip("/\\") != root.rstrip("/\\")]}
        v = ec.judge([item], {"run_id": g.get("run_id", ""), "agent_type": evt.get("agent_type"), "shell": shell,
                              "course_resources": ec.course_resources(project), "workspace": workspace})["1"]
        if v["decision"] == "allow":
            from notes_lib import exec_cleanup
            v = dict(v, cleanup=exec_cleanup.prefilter(v.get("cleanup") or []))   # never what already exists
            ec.record(g.get("run_id", ""), evt.get("agent_id", ""), item, v)
            return 0
        if v["decision"] == "rewrite" and v.get("command") and v["command"] != cmd and not ep.floor(v["command"]):
            return _block(f"{v['reason']} Run exactly this instead (it is pre-approved):\n{v['command']}")
        return _block(f"{v['reason']}. {_HINT}")
    except BaseException as e:  # noqa: BLE001  (fail-closed while a run is guarded)
        if isinstance(e, SystemExit):
            raise
        _log(data_dir, e)
        return _block(f"the execution policy could not check this command ({type(e).__name__}). {_HINT}")


if __name__ == "__main__":
    sys.exit(main())
