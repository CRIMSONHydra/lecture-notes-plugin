# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Deterministic course inventory for the cross-note sweep (PLAN 16.3).

    uv run concept_map.py [--project DIR] [--profile REF] [--json OUT] [--sessions 2,3] [--data-dir D]

Reads every session's ``work/sessions/NN/digest.md`` (no transcripts, no LLM) and writes
``<project>/.notes-plugin-internal/work/course/inventory.json``:

    {"schema": 1, "sessions": [{n, nn, title, slug, date, digest, notes: {tier: {path, outline: [...]}},
                                concepts: [{id, name, depth, minutes, emphasis, assessable}],
                                corrections: [{id, said, actual, severity, confidence}],
                                vocabulary: [{term, first_ts, definition}],
                                visuals: [{id, frame, concept, what, treatment, time}],
                                links: [bullet text of the digest's "Course links"]}],
     "shared_exact": [{key, kind, sessions: [{n, id|term}]}],   # identical normalised names in >= 2 sessions
     "missing_digests": [n, ...],
     "pending": [{id, kind: marker|legacy, tier, note, from, line, end_line, heading, targets, target_notes, present, missing,
                  resolvable, text, replaces}],      # pending-ref markers + legacy "has no note yet" phrases (PLAN 18)
     "process_talk": [{id, tier, note, from, line, end_line, heading, phrase, replaces, text}]}
                                                     # prose about how the note was made: each needs a ``scrub`` edit

Which concepts *are the same idea* under different names ("stack frame" vs "activation record") is NOT decided
here: the sweep-analyst agent does that from this file and writes ``work/course/concepts.json``.
``shared_exact`` is only the cheap, certain part (equal after lower-casing and stripping punctuation).
Output is byte-stable for the same digests. ``--sessions`` restricts the output (incremental sweeps) but
``shared_exact`` always compares against every digest, so edges to old sessions are not lost.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import digest as dg, guard, layout, notefile, pending as pn, platform as plat, process_talk, profile as prof  # noqa: E402
from notes_lib.profile import split_front_matter  # noqa: E402

plat.utf8_stdio()


def norm_key(s: str) -> str:
    s = re.sub(r"`", "", str(s).lower())
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9+_:\-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def col(header: list[str], names: tuple[str, ...], default: int | None) -> int | None:
    for i, h in enumerate(header):
        if any(h.startswith(n) for n in names):
            return i
    return default


def table_rows(digest: Path, keywords: tuple[str, ...], id_re: str | None, spec: dict) -> list[dict]:
    """Rows of the first table under an H2 starting with a keyword; ``spec`` = {field: (header names, pos)}."""
    out: list[dict] = []
    for header, trs in dg._tables(digest, keywords):
        idx = {k: col(header, names, pos) for k, (names, pos) in spec.items()}
        for _ln, c in trs:
            if not c or (id_re and not re.fullmatch(id_re, c[0])):
                continue
            out.append({k: (c[i].strip() if i is not None and i < len(c) else "") for k, i in idx.items()})
        break
    return out


def bullets_under(digest: Path, keyword: str) -> list[str]:
    _meta, body = split_front_matter(Path(digest).read_text(encoding="utf-8-sig"))
    lines = body.replace("\r\n", "\n").split("\n")
    out, on = [], False
    for ln in lines:
        m = re.match(r"^##\s+(?:\d+[.)]?\s+)?(.*)$", ln)
        if m:
            on = m.group(1).strip().lower().startswith(keyword)
            continue
        if on and re.match(r"^\s*[-*]\s+", ln):
            out.append(re.sub(r"^\s*[-*]\s+", "", ln).strip())
        elif on and out and ln.startswith("  ") and ln.strip():
            out[-1] += " " + ln.strip()
    return out


def outline(note: Path) -> list[str]:
    try:
        body = notefile.parse_note(note).body
    except Exception:  # noqa: BLE001
        return []
    out = []
    for kind, text, info in notefile.iter_blocks(body):
        if kind == "heading" and int(info) in (2, 3):
            out.append(("  " if int(info) == 3 else "") + notefile.clean_inline(text).strip())
    return out


def session_entry(lay, profile: dict, s: dict, with_notes: bool = True) -> tuple[dict, bool]:
    n = int(s["n"])
    nn = f"{n:02d}"
    digest = lay.session(nn) / "digest.md"
    ent: dict = {"n": n, "nn": nn, "title": s.get("title"), "date": s.get("date"), "slug": None,
                 "digest": None, "notes": {}, "concepts": [], "corrections": [], "vocabulary": [],
                 "visuals": [], "links": []}
    if with_notes:
        for t in layout.TIERS:
            d = lay.notes(t)
            for f in sorted(d.glob(f"{nn}-*.md")) if d.is_dir() else []:
                ent["notes"][t] = {"path": f.relative_to(lay.root).as_posix(), "outline": outline(f)}
                ent["slug"] = ent["slug"] or notefile.note_id(f.name)[1]
                break
    if not digest.is_file():
        return ent, False
    meta, _ = split_front_matter(digest.read_text(encoding="utf-8-sig"))
    ent["digest"] = digest.relative_to(lay.root).as_posix()
    ent["title"] = meta.get("title_as_taught") or meta.get("title_syllabus") or ent["title"]
    ent["slug"] = meta.get("slug") or ent["slug"]
    ent["date"] = str(meta.get("date") or ent["date"] or "") or None
    ent["concepts"] = table_rows(digest, ("concept inventory",), r"C\d+", {
        "id": (("id",), 0), "name": (("concept",), 1), "depth": (("depth",), 2), "minutes": (("minutes", "min"), 3),
        "emphasis": (("emphasis",), None), "assessable": (("assessable",), None)})
    for c in ent["concepts"]:
        c["depth"] = (c["depth"].lower().split() or [""])[0]
        c["assessable"] = c["assessable"].lower().startswith("y")
    ent["corrections"] = table_rows(digest, ("class-correction", "class correction", "corrections"), r"K\d+", {
        "id": (("id",), 0), "said": (("said",), 1), "actual": (("likely actual", "actual"), 2),
        "severity": (("severity",), 3), "confidence": (("confidence",), 4)})
    ent["vocabulary"] = table_rows(digest, ("vocabulary",), None, {
        "term": (("term",), 0), "first_ts": (("first",), 1), "definition": (("definition",), 2)})
    ent["vocabulary"] = [v for v in ent["vocabulary"] if v["term"] and not set(v["term"]) <= set("-: ")]
    ent["visuals"] = [{"id": v["id"], "frame": v["frame"], "concept": v["concept"], "what": v["what"],
                       "treatment": v["treatment"], "time": v["time"]} for v in dg.parse_visuals(digest)]
    ent["links"] = bullets_under(digest, "course links")
    return ent, True


def pending_inventory(lay, only: set[int] | None) -> list[dict]:
    """Every pending reference in every note (PLAN 18): ``pending-ref`` markers and legacy stale phrases.

    One item per marker or per stale sentence: ``{id, kind, tier, note, from, line, end_line, heading, targets,
    target_notes {"7": "07-slug.md"|null}, present, missing, resolvable, text, replaces}``. ``resolvable`` is true when the note of
    every target session exists in the same tier (then the sweep must turn the item into a ``resolve-pending``
    edit); otherwise it stays and goes into the sweep report. ``id`` is the stable edit id the analyst must use.
    """
    items: list[dict] = []
    for t in layout.TIERS:
        d = lay.notes(t)
        if not d.is_dir():
            continue
        have = {notefile.note_id(f.name)[0]: f.name for f in sorted(d.glob("[0-9]*.md"))}
        for f in sorted(d.glob("[0-9]*.md")):
            num = notefile.note_id(f.name)[0]
            m = re.match(r"\d+", num)
            if not m:
                continue
            frm = int(m.group())
            res = pn.scan(f.read_text(encoding="utf-8-sig"))
            used: dict[str, int] = {}
            for it in res["markers"] + res["legacy"]:
                if only is not None and frm not in only and not (set(it["targets"]) & only):
                    continue
                tn = {str(x): have.get(f"{x:02d}") for x in it["targets"]}
                base = "resolve-pending-" + "-".join(f"{x:02d}" for x in it["targets"] or [0]) + f"-from-{frm:02d}"
                used[base] = used.get(base, 0) + 1
                eid = base if used[base] == 1 else f"{base}-{used[base]}"
                items.append({"id": eid, "kind": it["kind"], "tier": t, "note": f.name, "from": frm,
                              "line": it["line"], "end_line": it["end_line"], "heading": it["heading"],
                              "targets": it["targets"], "target_notes": tn,
                              "present": [x for x in it["targets"] if tn[str(x)]],
                              "missing": [x for x in it["targets"] if not tn[str(x)]],
                              "resolvable": bool(it["targets"]) and all(v for v in tn.values()),
                              "text": it["text"], "replaces": it["replaces"]})
    return items


def build(lay, profile: dict, only: set[int] | None) -> dict:
    sessions = sorted(prof.course_sessions(profile), key=lambda s: (s.get("order") or int(s["n"]), int(s["n"])))
    full, missing = [], []
    for s in sessions:
        ent, ok = session_entry(lay, profile, s)
        full.append(ent)
        if not ok:
            missing.append(ent["n"])
    index: dict[tuple[str, str], list[dict]] = {}
    for e in full:
        for c in e["concepts"]:
            index.setdefault(("concept", norm_key(c["name"])), []).append({"n": e["n"], "id": c["id"]})
        for v in e["vocabulary"]:
            index.setdefault(("term", norm_key(v["term"])), []).append({"n": e["n"], "term": v["term"]})
    shared = []
    for (kind, key), refs in sorted(index.items()):
        if not key or len({r["n"] for r in refs}) < 2:
            continue
        if only is not None and not ({r["n"] for r in refs} & only):
            continue
        shared.append({"key": key, "kind": kind, "sessions": refs})
    out_sessions = [e for e in full if only is None or e["n"] in only]
    return {"schema": 1, "sessions": out_sessions, "shared_exact": shared, "missing_digests": missing,
            "pending": pending_inventory(lay, only), "process_talk": process_talk_inventory(lay, only)}


def process_talk_inventory(lay, only: set[int] | None) -> list[dict]:
    """Every passage that talks about how a note was made (``notes_lib.process_talk``), for ``scrub`` edits:
    ``{id, tier, note, from, line, end_line, heading, phrase, replaces, text}``. ``id`` is stable per note and order."""
    items: list[dict] = []
    for t in layout.TIERS:
        d = lay.notes(t)
        if not d.is_dir():
            continue
        for f in sorted(d.glob("[0-9]*.md")):
            m = re.match(r"\d+", notefile.note_id(f.name)[0])
            if not m or (only is not None and int(m.group()) not in only):
                continue
            for k, it in enumerate(process_talk.scan(f.read_text(encoding="utf-8-sig")), 1):
                items.append({"id": f"scrub-{int(m.group()):02d}-{k:03d}", "tier": t, "note": f.name,
                              "from": int(m.group()), **it})
    return items


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project")
    ap.add_argument("--profile")
    ap.add_argument("--json", metavar="OUT", help="default <project>/.notes-plugin-internal/work/course/inventory.json")
    ap.add_argument("--sessions", help="comma list: limit the output to these sessions (incremental sweep)")
    ap.add_argument("--data-dir")
    a = ap.parse_args(argv)
    try:
        lay = layout.Layout.for_project(a.project)
        profile = prof.load_profile(a.profile, data_dir=plat.resolve_data_dir(a.data_dir), project=lay.root)
    except (prof.ProfileError, layout.ProjectError) as e:
        print(f"concept_map: {e}", file=sys.stderr)
        return 2
    only = {int(x) for x in a.sessions.split(",") if x.strip().isdigit()} if a.sessions else None
    inv = build(lay, profile, only)
    out = plat.norm(a.json) if a.json else lay.work / "course" / "inventory.json"
    guard.assert_writable(out, plat.resolve_data_dir(a.data_dir))
    plat.write_json(out, inv)
    ns = len(inv["sessions"])
    print(f"inventory: {ns} session(s), {sum(len(e['concepts']) for e in inv['sessions'])} concepts, "
          f"{sum(len(e['corrections']) for e in inv['sessions'])} correction candidates, "
          f"{sum(len(e['vocabulary']) for e in inv['sessions'])} terms, {len(inv['shared_exact'])} exact repeats "
          f"-> {out.as_posix()}")
    pend = inv["pending"]
    if pend:
        res = [p for p in pend if p["resolvable"]]
        print(f"pending references: {len(pend)} ({sum(p['kind'] == 'marker' for p in pend)} pending-ref marker(s), "
              f"{sum(p['kind'] == 'legacy' for p in pend)} legacy stale phrase(s)); {len(res)} resolvable now "
              f"(target note exists), {len(pend) - len(res)} still waiting")
    if inv["process_talk"]:
        by = {}
        for it in inv["process_talk"]:
            by[it["tier"]] = by.get(it["tier"], 0) + 1
        print(f"process talk: {len(inv['process_talk'])} passage(s) about how the notes were made "
              f"({', '.join(f'{k} {v}' for k, v in by.items())}); each needs a scrub edit")
    if inv["missing_digests"]:
        print(f"no digest yet for session(s): {', '.join(map(str, inv['missing_digests']))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
