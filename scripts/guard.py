# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Manage the read-only guard that stops Write/Edit under resources/ (and external prior notes) during a run.

    uv run guard.py set    [--project DIR [--profile REF]] [--protect P ...] [--allow A ...] [--hours 6] [--run ID] [--data-dir D]
        --project derives protect = resources_dir + prior_notes_external, allow = notes/ pdf/ .notes-plugin-internal/ ;
        explicit --protect/--allow are added to those.
    uv run guard.py clear  [--run ID] --data-dir D
    uv run guard.py status --data-dir D

Each run's guard lives in <data>/guards/<run>.json and expires after --hours (a crashed run cannot block you
forever), so runs in other projects or sessions never overwrite or clear it. The PreToolUse hook
(guard_hook.py) checks writes against every active guard; scripts call notes_lib.guard.assert_writable().
While it is set, exec_guard_hook.py gates the owning run's commands; `clear --run ID` first runs that run's
pending cleanup plans (notes_lib/exec_cleanup.py). `clear` without --run clears the only active guard and
refuses when several runs are guarded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import guard, layout, platform as plat, profile as prof  # noqa: E402


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("set", "clear", "status"):
        sp = sub.add_parser(name)
        sp.add_argument("--data-dir", help="plugin data dir (default: $CLAUDE_PLUGIN_DATA / OS default)")
        if name == "clear":
            sp.add_argument("--run", default=None, help="the run whose guard to clear (default: the only active one)")
        if name == "set":
            sp.add_argument("--project", nargs="?", const="", default=None, metavar="DIR",
                            help="project dir (omit DIR to discover from cwd)")
            sp.add_argument("--profile", default=None, help="profile ref/path (default: the project's .notes-plugin-internal/profile.md)")
            sp.add_argument("--protect", nargs="*", default=[], metavar="PATH", help="read-only roots")
            sp.add_argument("--allow", nargs="*", default=[], metavar="PATH", help="writable roots (output)")
            sp.add_argument("--hours", type=float, default=6.0)
            sp.add_argument("--run", default="", help="run id: names the guard file; clear it with the same id")
    a = ap.parse_args(argv)
    ddir = plat.resolve_data_dir(a.data_dir)

    if a.cmd == "set":
        protect, allow = list(a.protect), list(a.allow)
        if a.project is not None:
            try:
                lay = layout.Layout.for_project(a.project or None)
                profile = prof.load_profile(a.profile, data_dir=ddir, project=lay.root)
            except (layout.ProjectError, prof.ProfileError) as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            p_, a_ = lay.guard_roots(profile)
            protect += p_
            allow += a_
        if not protect:
            print("error: give --project or --protect", file=sys.stderr)
            return 2
        f = guard.set_guard(ddir, protect, allow, hours=a.hours, run_id=a.run)
        g = guard.load_guard(ddir, a.run) or {}
        print(f"guard ON  ({f})  expires {g.get('expires')}")
        for p in g.get("protect", []):
            print(f"  protect {p}")
        for p in g.get("allow", []):
            print(f"  allow   {p}")
        return 0
    if a.cmd == "clear":
        active = guard.load_guards(ddir)
        run = a.run
        if run is None:
            if len(active) > 1:
                print("error: several runs are guarded; pass --run with the one to clear:", file=sys.stderr)
                for g in active:
                    print(f"  {g.get('run_id') or '(anonymous)'}  expires {g.get('expires')}", file=sys.stderr)
                return 2
            run = (active[0].get("run_id") or "") if active else None
        g = guard.load_guard(ddir, run) if run is not None else None
        try:
            if g:                               # final sweep: undo anything the run's approved commands left
                from notes_lib import exec_cleanup
                os.environ["LECTURE_NOTES_DATA"] = str(ddir)
                for line in exec_cleanup.cleanup(g.get("run_id", "")):
                    print(f"  cleanup {line}")
        except Exception as e:  # noqa: BLE001  (a cleanup problem must never leave the guard on)
            print(f"  cleanup skipped: {type(e).__name__}: {e}", file=sys.stderr)
        had = guard.clear_guard(ddir, run)
        print(f"guard cleared ({run or 'anonymous'})" if had else "no guard was active")
        return 0
    guards = guard.load_guards(ddir)
    if guards:
        # top-level fields: the newest run's guard (as before per-run guards); "guards": every active run
        print(json.dumps({"active": True, **guards[0], "count": len(guards), "guards": guards}, indent=2))
    else:
        print(json.dumps({"active": False, "data_dir": str(ddir)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
