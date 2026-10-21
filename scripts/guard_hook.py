# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse hook for Write|Edit|NotebookEdit: block writes under protected paths (resources/, external prior notes).

While a run is guarded, a subagent (anything with an ``agent_id``) also may not write under the plugin's data
directory (guard, ledger, cache) or under any installed version of the plugin: that would disable the
execution policy or plant a "plugin script" the fast path trusts.

stdin  = hook JSON ({"tool_name":..., "tool_input": {"file_path"|"notebook_path": ...}, "cwd": ...})
exit 2 = blocked (message on stderr); exit 0 = allowed. FAIL-OPEN: any internal error exits 0 and
is logged to <data>/guard-errors.log, so a bug here can never block ordinary work.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _log(exc: BaseException) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notes_lib import platform as plat

        d = plat.default_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "guard-errors.log", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {''.join(traceback.format_exception(exc))}\n")
    except Exception:
        pass


def main() -> int:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from notes_lib import guard, platform as plat

        raw = sys.stdin.read()
        evt = json.loads(raw) if raw.strip() else {}
        tin = evt.get("tool_input") or {}
        target = tin.get("file_path") or tin.get("notebook_path")
        if not target:
            return 0
        data_dir = plat.default_data_dir()
        g = guard.load_guard(data_dir)
        if not g:
            return 0
        p = Path(os.path.expanduser(str(target)))
        if not p.is_absolute() and evt.get("cwd"):
            p = Path(evt["cwd"]) / p
        msg = guard.check_path(p, g)
        if msg:
            sys.stderr.write(msg + "\n")
            return 2
        if evt.get("agent_id") or str(evt.get("agent_type") or "").startswith("notes:"):
            from notes_lib import exec_policy as ep

            rp = p.resolve()
            for root in [data_dir] + ep.plugin_roots():
                try:
                    if rp.is_relative_to(root.resolve()):
                        sys.stderr.write(f"notes guard: {p} is inside the plugin's own files ({root}); an agent may not "
                                         "change the plugin, its guard, ledger or cache during a run.\n")
                        return 2
                except OSError:
                    continue
        return 0
    except BaseException as e:  # noqa: BLE001  (fail-open by design)
        if isinstance(e, SystemExit):
            raise
        _log(e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
