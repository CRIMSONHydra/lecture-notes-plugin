# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf", "imageio-ffmpeg"]
# ///
"""What needs (re)generating? Compare every session's current inputs with the ledger (PLAN 13.3).

    uv run plan_updates.py --project D [--tiers both|basic|advanced] [--sessions 3,5] [--check-code]
                           [--json OUT] [--no-write-sessions] [--data-dir D]

Steps: migrate a legacy ``.notes/`` -> ``.notes-plugin-internal/``; discover resources/; append sessions for
lectures that are not in the profile to ``profile.md`` (next free ``n``, title/date from the transcript header or
file name, ``order`` from the date; comments and existing rows are never touched; printed); classify every
session in COURSE ORDER; adopt notes that have no ledger entry (``orphan``).

Status per session: new | changed | incomplete | orphan | up-to-date | no-inputs
  new         inputs exist, no note, no ledger entry
  changed     inputs differ from the ledger (reasons list exactly which: "slides: L5_.pdf added"); with
              --check-code also "code-changed: ..." (``code_changed: true``) when the instructor repo moved on
  incomplete  last run failed/partial, a requested tier has no note, or a recorded note file is gone
  orphan      a built note (its PDF exists) with no ledger entry: adopted now (fingerprinted with the current
              inputs), skipped. A note with no PDF and no ledger entry is an interrupted run: ``incomplete``
  up-to-date  nothing above applies (a deleted archived recording never changes this)
Action: write (new) | revise (changed/incomplete with an existing note) | skip.
Course docs (syllabus, course-wide handouts) never trigger regeneration; a change is reported under
``course_docs`` (informational).

JSON: {project, generated, tiers, order:[nn...], derived_added:[rows], adopted:[nn/tier], migrated:[msg],
       course_docs:{changed:[names], reasons:[...]},
       sessions:[{n, nn, title, order, date, status, reasons[], action, code_changed,
                  tiers:{basic:{note, exists, ledger_status}, advanced:{...}}}]}
Exit 0 always on success (the plan is information); 2 on usage/project errors.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import discover, guard, layout, layout_check, ledger, platform as plat, profile as prof  # noqa: E402
import resolve_inputs  # noqa: E402

CONTENT_KEYS = ("transcript", "whisper", "frames", "slides", "handouts", "classwork")


def remote_dir_shas(repo: str, ref: str) -> dict[str, str]:
    """{top-level dir: tree sha} from ONE recursive GitHub tree call."""
    url = f"https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
    hdr = {"User-Agent": "lecture-notes-plugin", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        hdr["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30) as r:
        tree = json.loads(r.read())
    return {e["path"]: e["sha"] for e in tree.get("tree", []) if e["type"] == "tree" and "/" not in e["path"]}


def write_derived(lay, profile, disc, ddir) -> tuple[list[dict], dict, object]:
    """Append derived sessions (with ``order`` from their date) to profile.md. -> (rows, profile, disc)."""
    rows = []
    cur = {"sessions": [dict(s) for s in profile.get("sessions") or []]}
    for r in disc.derived:
        row = {k: v for k, v in r.items() if k != "derived"}
        if not row.get("date"):
            # the Scaler header writes m/d/Y (the first candidate); a date in the file name counts too
            for it in disc.items:
                if it.scope == "session" and it.n == int(row["n"]) and it.kind == "transcript" and it.dates:
                    row["date"] = it.dates[0]
                    break
        o = prof.derive_order(cur, row.get("date"))
        if o is not None:
            row["order"] = o
        cur["sessions"].append(dict(row))
        rows.append(row)
    path = Path(profile["_path"])
    guard.assert_writable(path, ddir)
    text = path.read_text(encoding="utf-8-sig")
    path.write_text(discover.append_sessions_to_profile(text, rows), encoding="utf-8", newline="\n")
    profile = prof.load_profile(str(path), data_dir=ddir, project=lay.root)
    res = lay.resources(profile)
    return rows, profile, discover.discover(profile, res)


def _built(lay, nn: str, tier: str) -> bool:
    """True when ``pdf/<tier>/`` holds this session's PDF (``<PREFIX>-<nn>-<slug>-<tier>.pdf``)."""
    d = lay.pdf(tier)
    return d.is_dir() and any(d.glob(f"*-{nn}-*-{tier}.pdf"))


def classify(lay, profile, s, inputs, state, tiers, ns_state, code_remote):
    """-> plan row. Adopts orphan tiers into ``state`` (in memory)."""
    n, nn = int(s["n"]), f"{int(s['n']):02d}"
    fps = inputs["fingerprints"]
    ent = state["sessions"].get(nn)
    reasons: list[str] = []
    trigger = incomplete = False
    adopted_t: list[str] = []
    had_tiers = bool((ent or {}).get("tiers"))
    missing_t: list[str] = []
    has_content = any(k in fps for k in CONTENT_KEYS) or bool(inputs.get("recording"))
    exist = inputs["existing_notes"]
    tinfo = {}
    for t in tiers:
        te = ((ent or {}).get("tiers") or {}).get(t)
        tinfo[t] = {"note": inputs["note_paths"].get(t), "exists": bool(exist.get(t)),
                    "ledger_status": te.get("status") if te else None}
    any_note = any(tinfo[t]["exists"] for t in tiers)
    row = {"n": n, "nn": nn, "title": s.get("title"), "order": inputs["session"]["order"],
           "date": inputs["session"].get("date"), "tiers": tinfo, "code_changed": False}

    if not has_content and not ent and not any_note:
        return dict(row, status="no-inputs", reasons=["nothing for this session in resources/"], action="skip")

    for t in tiers:
        te = ((ent or {}).get("tiers") or {}).get(t)
        note_ok = tinfo[t]["exists"]
        if not note_ok:
            if te and te.get("note"):
                reasons.append(f"{t}: note {te['note']} is missing")
                incomplete = True
            else:
                missing_t.append(t)
                if te and te.get("status") != "ok":
                    reasons.append(f"{t}: last run {te.get('status')}")
                    incomplete = True
            continue
        if te is None and not _built(lay, nn, t):
            # a note with neither a ledger entry nor a PDF: an interrupted run, never a finished note
            reasons.append(f"{t}: note exists but was never built into a PDF (interrupted run); finish it")
            incomplete = True
            continue
        if te is None:
            # a built note without a ledger entry for this tier: adopt, then compare (trivially equal)
            ledger.record(lay, profile, inputs, t, "ok", mode="write", adopted=True, state=state, save_it=False)
            adopted_t.append(t)
            reasons.append(f"{t}: note exists without a ledger entry; adopted with the current inputs")
            continue
        if te.get("status") != "ok":
            reasons.append(f"{t}: last run {te.get('status')}")
            incomplete = True
        if has_content:
            r, trig = ledger.diff(te.get("inputs") or (ent or {}).get("inputs") or {}, fps)
            if not trig:       # a ledger written with raw-bytes hashes: silently move it to the normalised ones
                for stored in (te.get("inputs"), (ent or {}).get("inputs")):
                    if stored and ledger.upgrade(stored, fps):
                        ns_state["upgraded"] = True
            reasons += [f"{t}: {x}" if len(tiers) > 1 else x for x in r]
            trigger = trigger or trig
    if missing_t and (any_note or ent):
        reasons.append("no note yet for tier(s): " + ", ".join(missing_t))
        incomplete = True

    if code_remote is not None and ent:
        old = (((ent.get("inputs") or {}).get("code")) or {}).get("dir_sha")
        d = (inputs.get("instructor_code") or {}).get("dir")
        new = code_remote.get(d)
        if old and new and old != new:
            row["code_changed"] = True
            reasons.append(f"code-changed: {d}/ in the instructor repo moved on")
            trigger = True
        elif new and not old:
            reasons.append(f"code: no baseline recorded for {d}/ (not compared)")

    if trigger:
        status = "changed"
    elif incomplete:
        status = "incomplete"
    elif not any_note and not ent:
        status = "new"
        reasons = reasons or ["inputs exist, no note yet"]
    elif adopted_t and not had_tiers:
        status = "orphan"
    else:
        status = "up-to-date"
        if not has_content:
            reasons.append("inputs are no longer in resources/ (existing note kept)")
    if status == "new":
        action = "write"
    elif status in ("changed", "incomplete"):
        action = "revise" if any_note else "write"
    else:
        action = "skip"
    if adopted_t:
        ns_state.setdefault("adopted", []).extend(f"{nn}/{t}" for t in adopted_t)
    return dict(row, status=status, reasons=reasons, action=action)


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project")
    ap.add_argument("--profile")
    ap.add_argument("--tiers", choices=["both", "basic", "advanced"], default="both")
    ap.add_argument("--sessions", help="only these session numbers, e.g. 3,5")
    ap.add_argument("--check-code", action="store_true", help="one GitHub tree call; flag code-changed sessions")
    ap.add_argument("--no-write-sessions", action="store_true", help="do not append derived sessions to profile.md")
    ap.add_argument("--json", metavar="OUT")
    ap.add_argument("--data-dir")
    a = ap.parse_args(argv)
    ddir = plat.resolve_data_dir(a.data_dir)
    try:
        root = layout.find_project(explicit=a.project, migrate=False)
        migrated = layout.migrate_legacy(root)
        for m in migrated:
            print(m)
        for m in layout.ensure_gitattributes(root):          # existing projects pick it up on their next update
            migrated.append(m)
            print(m)
        lay = layout.Layout(root)
        profile = prof.load_profile(a.profile, data_dir=ddir, project=root)
    except (layout.ProjectError, prof.ProfileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    res = lay.resources(profile)
    if not res.is_dir():
        print(f"error: resources dir does not exist: {res}", file=sys.stderr)
        return 2
    tiers = list(layout.TIERS) if a.tiers == "both" else [a.tiers]

    disc = discover.discover(profile, res)
    derived_rows: list[dict] = []
    if disc.derived and not a.no_write_sessions:
        derived_rows, profile, disc = write_derived(lay, profile, disc, ddir)
        print(f"appended {len(derived_rows)} new session(s) to {Path(profile['_path']).as_posix()}: check the titles")
        for r in derived_rows:
            print("  " + discover.session_line(r).strip())

    outside = layout_check.check(root, profile)
    cache = {"lay": lay, "profile": profile, "disc": disc}
    state = ledger.load(lay)
    code_remote = None
    ic = profile.get("instructor_code")
    warnings: list[str] = []
    if a.check_code:
        if isinstance(ic, dict) and ic.get("repo"):
            try:
                code_remote = remote_dir_shas(ic["repo"], str(ic.get("ref", "HEAD")))
            except Exception as e:  # noqa: BLE001
                warnings.append(f"--check-code: GitHub tree call failed ({e}); code not compared")
        else:
            warnings.append("--check-code: the profile has no instructor_code.repo")

    only = {int(x) for x in a.sessions.split(",") if x.strip().isdigit()} if a.sessions else None
    plan_rows: list[dict] = []
    ns_state: dict = {}
    for s in prof.course_sessions(disc_profile := {**profile, "sessions": disc.sessions}):
        if only is not None and int(s["n"]) not in only:
            continue
        ns = SimpleNamespace(project=str(root), profile=a.profile, session=str(s["n"]), tiers="both",
                             video="auto", allow_no_speech=True, data_dir=a.data_dir)
        inputs, errors = resolve_inputs.resolve(ns, cache)
        if errors:
            plan_rows.append({"n": int(s["n"]), "nn": f"{int(s['n']):02d}", "title": s.get("title"),
                              "order": s.get("order"), "date": s.get("date"), "status": "no-inputs",
                              "reasons": errors, "action": "skip", "code_changed": False, "tiers": {}})
            continue
        plan_rows.append(classify(lay, disc_profile, s, inputs, state, tiers, ns_state, code_remote))

    if ns_state.get("adopted") or ns_state.get("upgraded"):
        with ledger.locked(lay):
            ledger.save(lay, state)

    # course docs: informational
    cd_changed: list[str] = []
    old_docs = state.get("course_docs") or {}
    if old_docs:
        for d in disc.course_docs():
            fp = ledger.file_fp(d["path"])
            nm = Path(d["path"]).name
            if fp and nm in old_docs and old_docs[nm] != fp["sha256"] and not ledger.same_hash(old_docs[nm], fp):
                cd_changed.append(nm)
    plan = {"project": root.as_posix(), "generated": ledger.now_iso(), "tiers": tiers,
            "order": [r["nn"] for r in plan_rows], "sessions": plan_rows, "derived_added": derived_rows,
            "adopted": ns_state.get("adopted", []), "migrated": migrated, "warnings": warnings,
            "outside_resources": outside,
            "course_docs": {"changed": cd_changed,
                            "reasons": [f"course-docs-changed: {x}" for x in cd_changed]}}

    print(f"update plan for {profile['id']} ({', '.join(tiers)}), course order:")
    print(f"  {'NN':<3} {'order':>6}  {'status':<11} {'action':<7} title")
    for r in plan_rows:
        print(f"  {r['nn']:<3} {r['order'] if r['order'] is not None else '':>6}  {r['status']:<11} "
              f"{r['action']:<7} {r['title']}")
        for x in r["reasons"]:
            print(f"        - {x}")
    for x in cd_changed:
        print(f"  course-docs-changed: {x} (informational; nothing is regenerated for it)")
    for w in warnings:
        print(f"  warning: {w}")
    counts: dict[str, int] = {}
    for r in plan_rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("summary: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    layout_check.print_block(outside)
    if a.json:
        guard.assert_writable(a.json, ddir)
        plat.write_json(a.json, plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
