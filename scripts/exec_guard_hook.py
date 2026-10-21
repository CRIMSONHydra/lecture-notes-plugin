# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse hook for Bash|PowerShell: gate commands while a notes run's guard is active.

stdin  = hook JSON ({"tool_input": {"command": ...}, "cwd", "session_id", "agent_id", "agent_type", ...})
exit 2 = blocked (reason on stderr); exit 0 = no objection (Claude Code's normal permission flow applies).

    no active guard, or inside the classifier itself   -> allow (ordinary work is never touched)
    no guard of this event's run                       -> allow (each run has its own guard: a session's
                                                          guard is matched by session id, and a notes agent
                                                          from another session only by working inside the
                                                          guarded project)
    floor (credentials, sudo, env overrides)           -> block; subagents also may not touch the guard,
                                                          the hooks, the ledger or the cache
    the main thread of the owning session              -> allow (the orchestrator runs plugin scripts)
    fast path (read-only, plugin scripts)              -> allow
    any subagent (notes:* or one a notes agent spawned) -> LLM classifier (exec_classifier): allow, deny,
                                                          or "run exactly this instead"; approvals are
                                                          recorded with their cleanup plan for exec_cleanup
FAIL-CLOSED while a run is guarded: an internal error blocks a subagent's command and is logged. The guard's
presence is checked with the standard library first, so a broken import cannot open the gate.
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


def _data_dir_stdlib() -> Path:
    """The plugin data dir, computed without importing the plugin (mirrors notes_lib.platform.default_data_dir)."""
    for var in ("LECTURE_NOTES_DATA", "CLAUDE_PLUGIN_DATA"):
        if os.environ.get(var):
            return Path(os.environ[var])
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "lecture-notes"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "lecture-notes"
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "lecture-notes"


def _log(data_dir: Path | None, exc: BaseException) -> None:
    try:
        if data_dir is None:
            return
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(data_dir / "exec-guard-errors.log", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {''.join(traceback.format_exception(exc))}\n")
    except Exception:
        pass


def _block(msg: str, subagent: bool, run_id: str = "") -> int:
    if subagent:
        sys.stderr.write(f"notes plugin: blocked during a notes run: {msg}. {_HINT}\n")
    else:
        sys.stderr.write(f"notes plugin: a notes run is active ({run_id or 'guard set'}) and this command {msg}. "
                         "Wait for the run to finish; if it crashed, clear the guard with /notes:doctor clear-guard.\n")
    return 2


def main() -> int:
    if os.environ.get("NOTES_EXEC_CLASSIFIER"):
        return 0
    try:
        raw = sys.stdin.read()
        evt = json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001  (unreadable event: nothing to judge)
        return 0
    cmd = (evt.get("tool_input") or {}).get("command")
    if not isinstance(cmd, str):
        return 0
    agent_type = str(evt.get("agent_type") or "")
    plugin_agent = agent_type.startswith("notes:")
    subagent = plugin_agent or bool(evt.get("agent_id"))        # a subagent of any kind during a guarded run
    data_dir = _data_dir_stdlib()
    guard_file_present = (data_dir / "guard.json").is_file() or any((data_dir / "guards").glob("*.json"))
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notes_lib import guard, platform as plat

        data_dir = plat.default_data_dir()
        guards = guard.load_guards(data_dir)
        if not guards:
            return 0
        # Each run has its own guard. The session that set it is gated by it; a notes agent that is not that
        # session's is gated only when it works inside the guarded project. Another run's guard (another
        # project, another session) is not this event's business: judging it against that run's folders would
        # refuse or misrecord its commands and hand its cleanup to the wrong run.
        mine = guard.owned_by_session(guards, evt.get("session_id"))
        if not mine and plugin_agent:
            mine = guard.covering(guards, evt.get("cwd"))
        if not mine:
            return 0
        g = mine[0]
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, SystemExit):
            raise
        _log(data_dir, e)
        if guard_file_present and subagent:                     # a guard may be active and we cannot read it
            return _block("could not be checked: the plugin failed to load while a guard file exists", True)
        return 0
    run_id = str(g.get("run_id") or "")
    try:
        from notes_lib import exec_classifier as ec, exec_policy as ep

        why = ep.floor(cmd, strict=subagent)
        if why:
            return _block(why, subagent, run_id)
        if not subagent or ep.fast_allow(cmd):
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
        root = Path(project).as_posix().rstrip("/").lower() if project else ""
        workspace = {"project": str(project) if project else "", "plugin_roots": [str(r) for r in ep.plugin_roots()],
                     "writable": list(g.get("allow") or []),             # the job's own output folders
                     "read_only": [p for p in g.get("protect") or []
                                   if Path(p).as_posix().rstrip("/").lower() != root]}
        agent_id = str(evt.get("agent_id") or "")
        v = ec.judge([item], {"run_id": run_id, "agent_id": agent_id, "agent_type": agent_type, "shell": shell,
                              "course_resources": ec.course_resources(project), "workspace": workspace})["1"]
        if v["decision"] == "allow":
            from notes_lib import exec_cleanup
            v = dict(v, cleanup=exec_cleanup.prefilter(v.get("cleanup") or []))   # never what already exists
            ec.record(run_id, agent_id, item, v)
            return 0
        if v["decision"] == "rewrite" and v.get("command") and v["command"] != cmd \
                and not ep.floor(v["command"], strict=True):
            return _block(f"{v['reason']} Run exactly this instead (it is pre-approved):\n{v['command']}",
                          subagent, run_id)
        return _block(str(v["reason"]), subagent, run_id)
    except BaseException as e:  # noqa: BLE001  (fail-closed while a run is guarded)
        if isinstance(e, SystemExit):
            raise
        _log(data_dir, e)
        return _block(f"could not be checked by the execution policy ({type(e).__name__})", subagent, run_id)


if __name__ == "__main__":
    sys.exit(main())
