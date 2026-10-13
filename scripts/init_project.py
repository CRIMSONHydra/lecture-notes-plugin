# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf"]
# ///
"""Scaffold a subject project for the `notes` plugin (PLAN section 10).

    uv run init_project.py [--project DIR] [--profile ID|PATH] [--id NEWID] [--force] [--data-dir D]
                           [--json OUT] [--from-syllabus FILE | --from-resources] [--resources-dir DIR]

Creates in DIR (default: cwd)::

    resources/ (+ videos/, README.md)   notes/{basic,advanced}/figures/   pdf/{basic,advanced}/
    .notes-plugin-internal/profile.md   .notes-plugin-internal/README.md   .notes-plugin-internal/work/ (git-ignored)

``--profile`` is a shipped template id/alias (``cpp``, ``cv``, ``fmi``, ``devops``, ``_template``) or a path to
a profile .md; default ``_template``. It is copied to ``.notes-plugin-internal/profile.md``; an existing profile is never
overwritten without ``--force``.

A subject with no profile and no CLAUDE.md (PLAN 16.4): ``--from-syllabus FILE`` (PDF/DOCX/TXT/MD; a name looked
up in resources/ also works) or ``--from-resources`` writes a DRAFT profile skeleton from ``_template`` + the
syllabus text (course title hint, institution line, session rows, assessment lines) + every transcript header
(course name, lecture title, start-date candidates, duration) + discovery (file kinds, session assignment) +
the code languages found (snippets only for those; none for a non-programming subject; ``figures:
class+generated``). The deterministic facts, with an ``assumptions[]`` list for the agent to review, go to
``.notes-plugin-internal/work/init/facts.json`` (and ``--json``). ``--resources-dir`` points the profile at an
external, read-only resources folder (absolute path). ``--id`` rewrites the copied ``id:`` line. Appends ``.notes-plugin-internal/work/`` to the
project's ``.gitignore`` (created if absent). Existing files are left alone: in an existing folder it only
creates ``.notes-plugin-internal/``, ``notes/{basic,advanced}/`` and ``pdf/{basic,advanced}/`` and never writes into an existing
``resources/`` (it prints the naming guidance instead). Exit 2 on a bad profile/usage.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import discover, layout, layout_check, platform as plat, profile as prof  # noqa: E402

GITIGNORE_LINE = layout.GITIGNORE_LINE

README_TMPL = """# resources/ - drop the class inputs here

The `notes` plugin only **reads** this folder. Generated files go to `notes/`, `pdf/` and `.notes-plugin-internal/`.

| What | Where | Name |
| --- | --- | --- |
| Transcript | `resources/` | `{transcript}` (the session `stem` from `.notes-plugin-internal/profile.md`) |
| Recording (optional) | `resources/videos/` | `{recording}` |
| Slides / handouts | `resources/` | `*.pdf`, `*.pptx`, `*.docx` - names matched by `paths.slides` / `paths.handouts` in the profile |
| Existing keyframes / Whisper output (optional) | `resources/frames/<stem>/`, `resources/<stem>.whisper.json` | reused read-only when present |

File names do not have to match: the plugin scores every file against the sessions (title, header
date, `L3` / `lecture 3` / `session_03`, ...), uses course-wide files (syllabus, handouts) everywhere and lists any
file it cannot place (`scripts/scan_resources.py`). A session needs only one of: transcript, recording, Whisper output.

Then, in this folder:

```text
/notes:lecture <session number> --tier both
```

Profile: `.notes-plugin-internal/profile.md` (course facts and session list: edit it to add a session's `stem`).
"""


def _set_id(text: str, new_id: str) -> str:
    return re.sub(r"(?m)^id:\s*.*$", f"id: {new_id}", text, count=1)


def init(project: Path, profile_ref: str, new_id: str | None, force: bool, data_dir,
         from_syllabus: str | None = None, from_resources: bool = False, resources_dir: str | None = None) -> dict:
    project.mkdir(parents=True, exist_ok=True)
    lay = layout.Layout(project)
    actions: list[str] = list(layout.migrate_legacy(project))     # a pre-PLAN-13 .notes/ becomes .notes-plugin-internal/

    dest = lay.profile_path
    if dest.exists() and not force:
        raise SystemExit(f"error: {dest} already exists (use --force to overwrite)")
    drafted: dict | None = None
    src = prof.find_profile("_template" if (from_syllabus or from_resources) else profile_ref,
                            data_dir=data_dir, project=project)
    text = src.read_text(encoding="utf-8-sig")
    if from_syllabus or from_resources:
        from notes_lib import draft_profile
        res_dir = plat.norm(resources_dir) if resources_dir else lay.resources({})
        if not res_dir.is_dir():
            raise SystemExit(f"error: resources dir {res_dir} does not exist (--resources-dir)")
        syl = None
        if from_syllabus:
            cand = Path(from_syllabus)
            syl = cand if cand.is_file() else (res_dir / from_syllabus)
            if not syl.is_file():
                raise SystemExit(f"error: syllabus {from_syllabus} not found")
        facts, d0 = draft_profile.facts_for(res_dir, syl, {"resources_dir": str(res_dir)})
        syl_rel = None
        if syl:
            try:
                syl_rel = syl.resolve().relative_to(res_dir.resolve()).as_posix()
            except ValueError:
                syl_rel = None
        text = draft_profile.skeleton(text, facts, d0, new_id, resources_dir, syl_rel)
        drafted = facts
        new_id = None
    if new_id:
        if not re.fullmatch(r"[a-z0-9-]+", new_id):
            raise SystemExit(f"error: --id {new_id!r} must match [a-z0-9-]+")
        text = _set_id(text, new_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    actions.append(f"profile {src.name} -> {dest}")
    try:
        p = prof.load_profile(dest, data_dir=data_dir, project=project)
    except prof.ProfileError as e:
        if not (src.name.startswith("_")):  # the blank template is allowed to be incomplete
            raise SystemExit(f"error: copied profile is invalid: {e}")
        p = {"resources_dir": "resources", "paths": {}, "sessions": []}
        actions.append(f"note: the blank template needs editing ({e})")

    res = lay.resources(p)
    if drafted is not None:
        fp = lay.work / "init" / "facts.json"
        plat.write_json(fp, drafted)
        actions.append(f"DRAFT profile from {'the syllabus and ' if from_syllabus else ''}resources: review "
                       f"{dest.name} and {fp.name} ({len(drafted['assumptions'])} assumption(s))")
        for a_ in drafted["assumptions"]:
            actions.append(f"assumption: {a_}")
    session_map: list = []
    derived: list = []
    if res.is_dir() and any(res.iterdir()):
        # files already in resources/: derive the session table from them (PLAN 11.3)
        if src.name.startswith("_") and drafted is None:  # the blank template's example rows are placeholders
            dest.write_text(discover.clear_sessions(dest.read_text(encoding="utf-8-sig")), encoding="utf-8", newline=chr(10))
            try:
                p = prof.load_profile(dest, data_dir=data_dir, project=project)
            except prof.ProfileError:
                pass
        d = discover.discover(p, res)
        derived = d.derived
        if derived:
            dest.write_text(discover.append_sessions_to_profile(dest.read_text(encoding="utf-8-sig"), derived),
                            encoding="utf-8", newline=chr(10))
            actions.append(f"derived {len(derived)} session(s) from resources/ into {dest.name}: check the table")
        session_map = d.session_map()
        for u in d.unassigned():
            actions.append(f"unassigned: {Path(u['path']).name} ({u['kind']})")
    lay.ensure()
    actions += layout.ensure_gitattributes(project)
    irm = lay.internal / "README.md"
    if not irm.exists():
        irm.write_text(layout.INTERNAL_README, encoding="utf-8", newline=chr(10))
        actions.append(f"wrote {irm}")
    rm = res / "README.md"
    if res.is_dir():
        # an existing resources/ is user data (often git-ignored): never write into it (PLAN 12.4)
        paths = p.get("paths") or {}
        actions.append(f"resources/ exists: left untouched. Name transcripts {paths.get('transcripts', '{stem}.txt')}"
                       f" and recordings {paths.get('recordings', 'videos/{stem}.mp4')} (or any name: files are"
                       " matched by title, number and date; see scan_resources.py)")
    else:
        (res / "videos").mkdir(parents=True, exist_ok=True)
        paths = p.get("paths") or {}
        rm.write_text(README_TMPL.format(transcript=paths.get("transcripts", "{stem}.txt"),
                                         recording=paths.get("recordings", "videos/{stem}.mp4")),
                      encoding="utf-8", newline=chr(10))
        actions.append(f"wrote {rm}")

    gi = project / ".gitignore"
    cur = gi.read_text(encoding="utf-8-sig") if gi.exists() else ""
    if GITIGNORE_LINE not in [ln.strip() for ln in cur.splitlines()]:
        gi.write_text(cur + ("" if not cur or cur.endswith("\n") else "\n") + GITIGNORE_LINE + "\n",
                      encoding="utf-8", newline="\n")
        actions.append(f"appended {GITIGNORE_LINE} to {gi}")
    return {"project": project.as_posix(), "profile": dest.as_posix(), "resources": res.as_posix(),
            "id": p.get("id"), "actions": actions, "derived_sessions": derived,
            "outside_resources": layout_check.check(project, p),
            "session_map": session_map}


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=None, help="target dir (default: cwd)")
    ap.add_argument("--profile", default="_template", help="template id/alias or path to a profile .md")
    ap.add_argument("--id", dest="new_id", default=None, help="rewrite the copied profile's id")
    ap.add_argument("--force", action="store_true", help="overwrite an existing .notes-plugin-internal/profile.md")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--from-syllabus", default=None, help="draft the profile from this syllabus file (+ transcripts)")
    ap.add_argument("--from-resources", action="store_true", help="draft the profile from the resources alone")
    ap.add_argument("--resources-dir", default=None, help="external resources folder (absolute); written into the profile")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    project = plat.norm(a.project) if a.project else plat.norm(Path.cwd())
    try:
        res = init(project, a.profile, a.new_id, a.force, a.data_dir, a.from_syllabus, a.from_resources,
                   a.resources_dir)
    except prof.ProfileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 2
        raise
    print(f"project ready: {res['project']}  (profile id {res['id']})")
    for x in res["actions"]:
        print(f"  {x}")
    for r in res.get("session_map") or []:
        files = ", ".join(f"{f['kind']}:{Path(f['path']).name}" for f in r["files"]) or "no files yet"
        print(f"  session {r['n']:>2} {r['title']!r}{' [derived]' if r['derived'] else ''}: {files}")
    layout_check.print_block(res.get("outside_resources") or [])
    print(f"next: drop the transcript (and optional recording/slides) into {res['resources']}, "
          "then run /notes:lecture N")
    if a.json:
        plat.write_json(a.json, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
