# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf"]
# ///
"""Inventory resources/ and map every file to a session (PLAN section 11).

    uv run scan_resources.py [--project DIR] [--profile REF] [--json OUT] [--write-sessions] [--data-dir D]

Classifies every file (transcript, whisper, frames, recording, slides, handout, syllabus, code, image,
other), scores it against the profile's sessions and prints the session map, the course-wide documents and
the files it could not place (with their top-2 candidates). Nothing is silently dropped.

--fix-layout [--confirm]: class files found OUTSIDE resources/ (project root, a stray videos/ or slides folder, notes/) are listed in a
"Files outside resources/" block (and in --json as ``outside_resources``); --fix-layout --confirm moves them to their
suggested destinations (never overwrites; a name clash refuses that file); without --confirm it only reports.

--write-sessions appends sessions derived from the files (new subject, or files matching no session) to
.notes-plugin-internal/profile.md; existing rows are never rewritten. resources/ is never written.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import discover, guard, layout, layout_check, platform as plat, profile as prof  # noqa: E402


def print_report(d: discover.Discovery, out=sys.stdout) -> None:
    p = lambda *a: print(*a, file=out)  # noqa: E731
    p(f"resources: {d.resources.as_posix()}  ({len(d.items)} entries)")
    p("session map:")
    for r in d.session_map():
        tag = "  [derived]" if r["derived"] else ""
        p(f"  {r['n']:>2}  {r['title']!r}{tag}")
        for f in r["files"]:
            p(f"        {f['kind']:<10} {Path(f['path']).name}   ({f['reason']}, {f['score']})")
    cd = d.course_docs()
    if cd:
        p("course-wide:")
        for x in cd:
            p(f"        {x['kind']:<10} {Path(x['path']).name}   ({x['reason']})")
    wide = [i for i in d.items if i.scope == "course" and i.kind in ("data", "exercise")]
    if wide:
        p("course-wide data / exercises (given to every session):")
        for i in wide:
            p(f"        {i.kind:<10} {i.rel}   ({i.reason})")
    un = d.unassigned()
    if un:
        p("UNASSIGNED (rename the file, or pin it in the profile paths.*):")
        for x in un:
            cands = ", ".join(f"session {c['n']}: {c['score']}" for c in x["candidates"]) or "no candidates"
            p(f"        {x['kind']:<10} {Path(x['path']).name}   [{cands}]")
    for w in d.warnings:
        p(f"warning: {w}")


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--profile")
    ap.add_argument("--json", metavar="OUT")
    ap.add_argument("--write-sessions", action="store_true")
    ap.add_argument("--data-dir")
    ap.add_argument("--fix-layout", action="store_true",
                    help="move class inputs found outside resources/ into it (needs --confirm)")
    ap.add_argument("--confirm", action="store_true",
                    help="with --fix-layout: really move the files; without it only report")
    a = ap.parse_args(argv)
    try:
        lay = layout.Layout.for_project(a.project)
        profile = prof.load_profile(a.profile, data_dir=plat.resolve_data_dir(a.data_dir), project=lay.root)
    except (prof.ProfileError, layout.ProjectError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    res = lay.resources(profile)
    if not res.is_dir():
        print(f"error: resources dir does not exist: {res}", file=sys.stderr)
        return 2
    outside = layout_check.check(lay.root, profile)
    if a.fix_layout:
        if not outside:
            print("layout ok: no class files outside resources/")
        elif not a.confirm:
            layout_check.print_block(outside)
            print("--fix-layout without --confirm: nothing moved")
        else:
            results = layout_check.fix(lay.root, outside, profile)
            layout_check.print_fix(results, lay.root)
            if any(r["status"] == "refused" for r in results):
                print("some files were refused (see above); the others were moved")
            outside = layout_check.check(lay.root, profile)
    d = discover.discover(profile, res)
    print_report(d)
    if not (a.fix_layout and not a.confirm and outside):
        layout_check.print_block(outside)
    ddir = plat.resolve_data_dir(a.data_dir)
    if a.write_sessions:
        if not d.derived:
            print("no derived sessions to write")
        else:
            path = Path(profile["_path"])
            guard.assert_writable(path, ddir)
            text = path.read_text(encoding="utf-8-sig")
            path.write_text(discover.append_sessions_to_profile(text, d.derived), encoding="utf-8", newline="\n")
            print(f"appended {len(d.derived)} derived session(s) to {path.as_posix()}: check the table")
    if a.json:
        guard.assert_writable(a.json, ddir)
        plat.write_json(a.json, {**d.to_json(), "outside_resources": outside})
    return 0


if __name__ == "__main__":
    sys.exit(main())
