# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SubagentStop hook for notes:* agents: clean up what the agent's approved commands left behind.

Runs the cleanup plans the classifier recorded for this agent (exec_cleanup), then sweeps Docker objects
labelled with the run id. It never blocks the stop: it removes leftovers itself and logs what it did to
<data>/exec-ledger/<run>.cleanup.log. The run is the one whose guard gated the agent (same session, else the
guarded project the agent worked in, as in exec_guard_hook). No such guard: nothing to do.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    try:
        if os.environ.get("NOTES_EXEC_CLASSIFIER"):
            return 0
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notes_lib import exec_classifier as ec, exec_cleanup, guard, platform as plat

        raw = sys.stdin.read()
        evt = json.loads(raw) if raw.strip() else {}
        if not str(evt.get("agent_type") or "").startswith("notes:"):
            return 0
        guards = guard.load_guards(plat.default_data_dir())
        mine = guard.owned_by_session(guards, evt.get("session_id")) or guard.covering(guards, evt.get("cwd"))
        if not mine:
            return 0
        g = mine[0]
        from notes_lib import layout
        try:
            project = str(layout.find_project(start=evt.get("cwd")))
        except Exception:  # noqa: BLE001
            project = None
        done = exec_cleanup.cleanup(g.get("run_id", ""), evt.get("agent_id"), project=project)
        if done:
            log = ec.ledger_path(g.get("run_id", "")).with_suffix(".cleanup.log")
            with open(log, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(f"{datetime.now().isoformat(timespec='seconds')} {evt.get('agent_type')} "
                         f"{evt.get('agent_id')}\n" + "".join(f"  {d}\n" for d in done))
    except Exception:  # noqa: BLE001  (a cleanup problem must never trap an agent)
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
