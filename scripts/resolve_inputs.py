# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf", "imageio-ffmpeg"]
# ///
"""Resolve everything a run needs for one session of a subject profile -> inputs.json.

    uv run resolve_inputs.py --session N [--project DIR] [--profile REF]
        [--tiers both|basic|advanced] [--video auto|reuse|run|frames-only|skip]
        [--allow-no-speech] [--data-dir D] [--dry-run] [--json OUT]

--project defaults to the first dir above cwd holding .notes-plugin-internal/profile.md; --profile defaults to that file.
Writes <project>/.notes-plugin-internal/work/sessions/NN/inputs.json (not with --dry-run). resources/ is never written.

Works with whatever resources exist (PLAN section 11): profile path patterns first, then files discovered
and scored by notes_lib.discover (renamed files, .vtt/.srt, audio-only, derived sessions). At least one
speech source is needed (transcript, Whisper output or a recording) unless --allow-no-speech. Exits 2 when
there is no project/profile or no speech source.

New keys: speech{primary: transcript|whisper|recording, sources[{kind,path}]}, course_docs[{path,kind,reason}],
unassigned[{path,kind,candidates[{n,score}]}], data[]/exercises[]/references[]{path,kind,sessions,reason}, existing_notes{basic,advanced}; video_action may be
"whisper-only" (audio-only recording) or "run-force-whisper" (the recording is the only speech source).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import discover, guard, layout, ledger, notefile, platform as plat, profile as prof  # noqa: E402
from notes_lib.transcript import cues_to_text, parse_cues, parse_header  # noqa: E402

VIDEO_MODES = ("auto", "reuse", "run", "frames-only", "skip")
CLASSWORK_IGNORE = ["*.o", "*.obj", "*.out", "*.exe", "*.class", "*.swp", ".*.swp", "*.pyc", ".DS_Store"]


def os_key(p) -> str:
    return os.path.normcase(str(p))


def probe_duration_s(path: Path) -> float | None:
    """Media duration in seconds from the container header (ffmpeg -i; no decoding), or None."""
    try:
        import subprocess
        import imageio_ffmpeg
        r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        m = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", r.stderr)
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    except Exception:  # noqa: BLE001
        return None


def _p(path) -> str:
    return Path(path).as_posix()


def _first(paths: list[Path], what: str, warnings: list[str]) -> Path | None:
    if not paths:
        return None
    if len(paths) > 1:
        warnings.append(f"{what}: {len(paths)} matches, using {paths[0].name} "
                        f"(others: {', '.join(x.name for x in paths[1:4])})")
    return paths[0]


def _frames_info(d: Path) -> dict | None:
    index = d / "index.md"
    if not index.is_file():
        return None
    fj = d / "frames.json"
    count = None
    if fj.is_file():
        try:
            data = json.loads(fj.read_text(encoding="utf-8-sig"))
            count = len(data) if isinstance(data, list) else len(data.get("frames", []))
        except (OSError, ValueError):
            count = None
    if count is None:
        count = len(list(d.glob("*.jpg")))
    tm = d / "transcript.md"
    return {"dir": _p(d), "index": _p(index), "count": count,
            "frames_json": _p(fj) if fj.is_file() else None,
            "transcript_md": _p(tm) if tm.is_file() else None}


def _list_files(root: Path) -> list[str]:
    out = []
    for f in sorted(root.rglob("*")):
        if f.is_file():
            rel = f.relative_to(root).as_posix()
            if any(part.startswith(".") for part in rel.split("/")[:-1]):
                continue  # editor config dirs (.vscode, .idea ...)
            if any(fnmatch.fnmatch(f.name, g) for g in CLASSWORK_IGNORE):
                continue
            out.append(rel)
    return out


def _prior_md(dirs: list[Path], n: int, sessions: list[dict] | None = None, omap: dict | None = None) -> list[str]:
    """Markdown notes in `dirs` for sessions strictly earlier than n *in course order* (never n itself;
    PLAN 13.5: a session held between 12 and 13 but numbered 14 counts as earlier than 13).

    A note without an NN prefix is matched to a session by title similarity (PLAN 11.5)."""
    omap = omap or {}
    me = prof.order_of(omap, n)
    found: list[tuple] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if f.name.lower() == "readme.md":
                continue
            num, _slug = notefile.note_id(f.name)
            m = re.match(r"(\d+)", num or "")
            k = int(m.group(1)) if m else (discover.match_session(sessions, f.name) if sessions else None)
            if k is not None and k != n and prof.order_of(omap, k) < me:
                found.append((prof.order_of(omap, k), k, f.name, _p(f)))
    found.sort()
    return [x[3] for x in found]


def _existing_notes(lay: layout.Layout, n: int, warnings: list[str] | None = None) -> dict:
    """The project's own note for session n per tier (NN-<slug>.md; a companion NNa-<slug>.md is never the
    session note and is reported as an anomaly), else None. Several primary notes for one session are reported."""
    out: dict = {}
    for t in layout.TIERS:
        files = ledger.note_files(lay, t).get(f"{n:02d}", [])
        prim = ledger.primary_notes(files)
        if len(prim) > 1 and warnings is not None:
            warnings.append(f"{t}: {len(prim)} notes for session {n} ({', '.join(f.name for f in prim)}): one note "
                            "per session and tier; delete the extras (lint fails on duplicates)")
        extra = [f for f in files if f not in prim]
        if extra and warnings is not None:
            warnings.append(f"{t}: {', '.join(f.name for f in extra)} is a companion/split note: not allowed (one note per "
                            "session and tier); merge it into the main note and delete it (lint and qa fail on it)")
        out[t] = _p(prim[0]) if prim else None        # a companion never stands in for the session's note
    return out


def _note_paths(lay: layout.Layout, nn: str, existing: dict, state: dict) -> dict:
    """The exact note path writers must use per tier (PLAN 13.4): the ledger's fixed slug, else the
    existing file, else None (a first generation chooses the slug)."""
    ent = (state.get("sessions") or {}).get(nn) or {}
    out = {}
    for t in layout.TIERS:
        rp = ((ent.get("tiers") or {}).get(t) or {}).get("note")
        led = lay.root / rp if rp else None
        if led is not None and led.is_file():
            out[t] = _p(led)
        elif existing.get(t):
            out[t] = existing[t]
        elif led is not None:
            out[t] = _p(led)
        else:
            out[t] = None
    return out


def _speech_sources(transcript, whisper, rec, frames) -> tuple[str | None, list[dict]]:
    sources: list[dict] = []
    if transcript:
        sources.append({"kind": "transcript", "path": transcript["path"]})
    if whisper:
        sources.append({"kind": "whisper", "path": whisper["path"]})
    elif frames and frames.get("transcript_md"):
        sources.append({"kind": "whisper", "path": frames["transcript_md"]})
    if rec:
        sources.append({"kind": "recording", "path": rec})
    primary = sources[0]["kind"] if sources else None
    return primary, sources


def resolve(args, cache: dict | None = None) -> tuple[dict, list[str]]:
    """Returns (inputs dict, fatal errors). ``cache`` (a dict) lets a caller that resolves many sessions
    (plan_updates) share the project, profile and discovery: it is filled on first use."""
    errors: list[str] = []
    warnings: list[str] = []
    ddir = plat.resolve_data_dir(args.data_dir)
    cache = cache if cache is not None else {}
    if "lay" not in cache:
        cache["lay"] = layout.Layout.for_project(args.project)
        cache["profile"] = prof.load_profile(args.profile, data_dir=ddir, project=cache["lay"].root)
    lay, profile = cache["lay"], cache["profile"]
    res = lay.resources(profile)
    if not res.is_dir():
        errors.append(f"resources dir does not exist: {res} (create it and drop the class files there)")

    # discovery: every file in resources/ scored against the sessions (PLAN 11); derived sessions fill gaps
    if "disc" not in cache:
        cache["disc"] = discover.discover(profile, res) if res.is_dir() else None
    disc = cache["disc"]
    if disc is not None and disc.derived:
        profile = {**profile, "sessions": disc.sessions}
    si = prof.session_info(profile, args.session)
    n, nn = si["n"], si["nn"]
    derived_row = next((s for s in (disc.derived if disc else []) if int(s["n"]) == n), None)
    if derived_row:
        warnings.append(f"session {n} is not in the profile table: derived from the files "
                        "(run scan_resources.py --write-sessions to record it)")

    session_dir = lay.session(nn)
    fmt = dict(si)

    def found(key, kind):
        """Profile pattern hits first, then discovery's files for this session; de-duplicated."""
        hits = list(prof.resolve_repo_paths(profile, key, **fmt))
        seen = {os_key(x) for x in hits}
        for it in (disc.for_session(n, kinds=(kind,)) if disc else []):
            if os_key(it.path) not in seen:
                seen.add(os_key(it.path))
                hits.append(it.path)
        return hits

    # transcript (text, .vtt or .srt)
    tpath = _first(found("transcripts", "transcript"), "transcript", warnings)
    transcript = None
    duration_min = None
    date = si["date"]
    if tpath is not None:
        fmt_t = tpath.suffix.lower().lstrip(".") if tpath.suffix.lower() in (".vtt", ".srt") else "txt"
        transcript = {"path": _p(tpath), "bytes": tpath.stat().st_size, "format": fmt_t}
        try:
            raw = tpath.read_text(encoding="utf-8-sig", errors="replace")
            if fmt_t in ("vtt", "srt"):
                cues = parse_cues(raw)
                if cues:
                    duration_min = int(round(cues[-1]["end"] / 60))
            else:
                hdr, _body = parse_header(raw)
                duration_min = hdr["duration_min"]
                if not date and hdr["date"]:
                    date = hdr["date"]
                elif not date and hdr["date_ambiguous"]:
                    cands = ", ".join(c["date"] for c in hdr["start_candidates"])
                    warnings.append(f"transcript date is ambiguous ({cands}); set `date:` in the profile session")
                if si["title"] and hdr["lecture_title"] and hdr["lecture_title"].strip() != si["title"].strip():
                    warnings.append(f"transcript title {hdr['lecture_title']!r} differs from profile title {si['title']!r}")
        except OSError as e:
            warnings.append(f"could not read transcript header: {e}")
    if not si["stem"] and tpath is not None:
        si = dict(si, stem=tpath.stem)

    # recording, whisper, frames
    recs = found("recordings", "recording")
    rec = recs[0] if recs else None
    rec_candidates: list[dict] = []
    if len(recs) > 1:
        # several recordings match: the longest is the class, unless it is wildly longer than the transcript
        for r_ in recs:
            d_ = probe_duration_s(r_)
            rec_candidates.append({"path": _p(r_), "duration_s": round(d_, 1) if d_ is not None else None,
                                   "bytes": r_.stat().st_size})
        t_dur = None
        if tpath is not None and duration_min:
            t_dur = duration_min * 60
        timed = [c for c in rec_candidates if c["duration_s"]]
        pool = [c for c in timed if not (t_dur and c["duration_s"] > 3 * t_dur)] or timed
        if pool:
            best = max(pool, key=lambda c: c["duration_s"])
            rec = Path(best["path"])
            warnings.append(f"recording: {len(recs)} candidates, chose {rec.name} "
                            f"({best['duration_s'] / 60:.0f} min, the longest"
                            + (" within 3x the transcript duration" if t_dur else "") + "); others: "
                            + ", ".join(f"{Path(c['path']).name} ({(c['duration_s'] or 0) / 60:.0f} min)"
                                        for c in rec_candidates if c["path"] != best["path"]))
        else:
            warnings.append(f"recording: {len(recs)} candidates, durations unreadable; using {rec.name}")
    if rec is not None and duration_min:
        d_ = next((c["duration_s"] for c in rec_candidates if c["path"] == _p(rec)), None)             if rec_candidates else probe_duration_s(rec)
        if d_ and d_ < 0.5 * duration_min * 60:
            warnings.append(f"recording {rec.name} looks incomplete: {d_ / 60:.0f} min vs "
                            f"{duration_min} min in the transcript header")
    audio_only = bool(rec and rec.suffix.lower() in discover.AUDIO_EXT)
    whisper = None
    wp = _first(found("whisper", "whisper"), "whisper", warnings)
    if wp:
        whisper = {"path": _p(wp), "source": "resources"}
    elif (session_dir / "video" / "whisper.json").is_file():
        whisper = {"path": _p(session_dir / "video" / "whisper.json"), "source": "session"}

    frames = None
    frames_source = None
    for d in found("frames", "frames"):
        if d.is_dir():
            frames = _frames_info(d)
            if frames:
                frames_source = "resources"
                break
    if frames is None and (session_dir / "video" / "frames" / "index.md").is_file():
        frames = _frames_info(session_dir / "video" / "frames")
        frames_source = "session"
    if frames:
        frames["source"] = frames_source

    # speech: any one of transcript / whisper / recording is enough (PLAN 11.4)
    primary, sources = _speech_sources(transcript, whisper, _p(rec) if rec else None, frames)
    no_speech = primary is None
    if no_speech and not args.allow_no_speech:
        errors.append(f"no speech source for session {n}: found no transcript, Whisper output or recording "
                      f"under {res.as_posix()}. Drop one in (any name: files are matched by title, number and "
                      "date; see scan_resources.py), or pass --allow-no-speech to write from documents only")
    if no_speech:
        warnings.append("no speech source: notes will be written from slides, handouts and code only "
                        "(--allow-no-speech); say so in the note front matter")
    need_force = bool(rec and not transcript and not whisper and not (frames and frames.get("transcript_md")))

    # video action
    mode = args.video
    if audio_only:
        if mode == "frames-only":
            warnings.append("--video frames-only: the recording is audio-only, so there are no frames")
        action = "whisper-only" if (mode != "skip" and not whisper and (need_force or mode == "run")) else "skip"
    elif mode == "auto":
        action = "reuse" if frames else ("run" if rec else "skip")
        if need_force:
            action = "run-force-whisper"
    elif mode == "reuse":
        action = "reuse" if frames else "skip"
        if not frames:
            warnings.append("--video reuse: no existing keyframes found, so the video step is skipped")
        if need_force:
            action = "run-force-whisper"
            warnings.append("the recording is the only speech source: Whisper will be forced")
    elif mode in ("run", "frames-only"):
        action = mode if rec else "skip"
        if not rec:
            warnings.append(f"--video {mode}: no recording found, so the video step is skipped")
        elif need_force:
            action = "run-force-whisper"
            if mode == "frames-only":
                warnings.append("--video frames-only: the recording is the only speech source, so Whisper is forced")
    else:
        action = "skip"
        if need_force:
            warnings.append("--video skip: the recording is the only speech source, so nothing will be heard "
                            "(drop --video skip, or add a transcript)")
    if action == "run-force-whisper":
        warnings.append("recording is the only speech source: run the pipeline with --whisper force "
                        "(on CPU only it is slow: tell the user the expected time and proceed)")
    if action == "skip" and not frames and not no_speech:
        warnings.append("no keyframes and no video step: the digest will rest on the transcript, code and slides only")

    # slides, handouts (profile patterns + files discovery assigned to this session), classwork
    def _docs(key, kind):
        out = [{"path": _p(x), "kind": x.suffix.lower().lstrip(".")}
               for x in prof.resolve_repo_paths(profile, key, **fmt)
               if x.is_file() and x.suffix.lower() in (".pdf", ".docx", ".pptx")]
        have = {os_key(Path(d["path"])) for d in out}
        for it in (disc.for_session(n, kinds=(kind,)) if disc else []):
            if it.path.is_file() and os_key(it.path) not in have:
                have.add(os_key(it.path))
                out.append({"path": _p(it.path), "kind": it.path.suffix.lower().lstrip(".")})
        return out

    slides_detail = _docs("slides", "slides")
    slides = [d["path"] for d in slides_detail]
    handouts = _docs("handouts", "handout")
    extra_files = [{"path": _p(it.path), "kind": it.kind} for it in (disc.for_session(n, kinds=("image", "code", "other")) if disc else [])]
    course_docs = disc.course_docs() if disc else []
    extras = disc.extras(n) if disc else {"data": [], "exercises": [], "references": []}
    unassigned = disc.unassigned() if disc else []
    if unassigned:
        warnings.append(f"{len(unassigned)} file(s) in resources/ could not be assigned to a session "
                        "(see unassigned[]): rename them with the session number or pin them in the profile")
    classwork = None
    cw = _first([x for x in prof.resolve_repo_paths(profile, "classwork", **fmt) if x.is_dir()], "classwork", warnings)
    if cw:
        classwork = {"dir": _p(cw), "files": _list_files(cw)}

    # instructor code
    ic = profile.get("instructor_code")
    instructor_code = None
    if isinstance(ic, dict) and ic.get("kind", "github") == "github" and ic.get("repo"):
        folder = str(ic.get("session_dir", "Lec-{n}")).format(n=n, nn=nn)
        ref = str(ic.get("ref", "HEAD"))
        instructor_code = {"repo": ic["repo"], "dir": folder, "ref": ref,
                           "url": f"https://github.com/{ic['repo']}/tree/{ref}/{folder}",
                           "ignore": list(ic.get("ignore") or []),
                           # no <dir> in the repo -> fetch_code.py --title falls back to loose root files
                           "fallback": "root", "title": si["title"]}

    # tiers + layout
    tiers = ["basic", "advanced"] if args.tiers == "both" else [args.tiers]
    paths = {"project": _p(lay.root),
             "resources": _p(res),
             "reports": _p(lay.reports),
             "session": _p(session_dir),
             "session_transcript": _p(session_dir / "transcript"),
             "session_code": _p(session_dir / "code"),
             "session_slides": _p(session_dir / "slides"),
             "session_video": _p(session_dir / "video"),
             "session_screen_notes": _p(session_dir / "screen-notes"),
             "session_logs": _p(session_dir / "logs"),
             "digest": _p(session_dir / "digest.md"),
             "research": _p(session_dir / "research.md"),
             "research_verify": _p(session_dir / "research-verify.md"),
             "research_deep": _p(session_dir / "research-deep.md"),
             "timings": _p(session_dir / "timings.jsonl")}
    for t in layout.TIERS:
        paths[f"{t}_notes"] = _p(lay.notes(t))
        paths[f"{t}_figures"] = _p(lay.figures(t))
        paths[f"{t}_meta"] = _p(lay.meta(t))
        paths[f"{t}_build"] = _p(lay.build(t))
        paths[f"{t}_pdf"] = _p(lay.pdf(t))

    # prior notes (strictly earlier sessions only)
    prior_dirs = lay.prior_dirs(profile)
    sess_tbl = profile.get("sessions") or []
    omap = prof.session_orders({"sessions": sess_tbl})
    prior = {"external": _prior_md([d for d in prior_dirs if d.is_dir()], n, sess_tbl, omap),
             "project": {t: _prior_md([lay.notes(t)], n, sess_tbl, omap) for t in layout.TIERS}}
    existing_notes = _existing_notes(lay, n, warnings)
    state = ledger.load(lay)
    note_paths = _note_paths(lay, nn, existing_notes, state)
    me_order = prof.order_of(omap, n)
    prior_sessions = [f"{int(s['n']):02d}" for s in sorted(sess_tbl, key=lambda s: (prof.order_of(omap, s["n"]), int(s["n"])))
                      if int(s["n"]) != n and prof.order_of(omap, s["n"]) < me_order]

    # protect list: resources_dir, external prior notes, and any resolved input living elsewhere
    protect = [_p(res)]
    for d in prior_dirs:
        if _p(d) not in protect:
            protect.append(_p(d))
    for item in [tpath, rec, wp, cw, *[Path(x) for x in slides], *[Path(d['path']) for d in handouts],
                 Path(frames["dir"]) if frames and frames_source == "resources" else None]:
        if item and not plat.is_under(item, res) and not plat.is_under(item, lay.root):
            q = _p(plat.norm(item))
            if q not in protect:
                protect.append(q)
    _gp, _ga = lay.guard_roots(profile)
    for q in _gp:
        if q not in protect:
            protect.append(q)
    allow = list(_ga)
    for extra in protect:
        if plat.is_under(lay.root, extra) and extra != _p(lay.root):
            errors.append(f"refusing: project {lay.root.as_posix()} is inside read-only input path {extra}")

    inputs = {
        "profile": {"id": profile["id"], "path": _p(profile["_path"])},
        "session": {"n": n, "nn": nn, "title": si["title"], "stem": si["stem"], "date": date,
                    "duration_min": duration_min, "derived": bool(derived_row), "order": me_order},
        "project": _p(lay.root),
        "resources_dir": _p(res),
        "session_dir": _p(session_dir),
        "transcript": transcript,
        "speech": {"primary": primary, "sources": sources},
        "no_speech": no_speech,
        "recording": _p(rec) if rec else None,
        "recording_candidates": rec_candidates,
        "whisper": whisper,
        "frames": frames,
        "video_action": action,
        "slides": slides,
        "slides_detail": slides_detail,
        "handouts": handouts,
        "course_docs": course_docs,
        "data": extras["data"],
        "exercises": extras["exercises"],
        "references": extras["references"],
        "unassigned": unassigned,
        "extra_files": extra_files,
        "existing_notes": existing_notes,
        "note_paths": note_paths,
        "prior_sessions": prior_sessions,
        "classwork": classwork,
        "instructor_code": instructor_code,
        "prior_notes": prior,
        "tiers": tiers,
        "paths": paths,
        "protect": protect,
        "allow": allow,
        "warnings": warnings,
    }
    if rec:
        inputs["recording_bytes"] = rec.stat().st_size
    prev = (state.get("sessions") or {}).get(nn)
    inputs["fingerprints"] = ledger.fingerprints(inputs, prev)
    inputs["inputs_hash"] = ledger.inputs_hash(inputs["fingerprints"])
    return inputs, errors


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="project dir (default: discovered by walking up from cwd)")
    ap.add_argument("--profile", help="profile id, alias or .md path (default: <project>/.notes-plugin-internal/profile.md)")
    ap.add_argument("--session", required=True, help="session number (2a -> 2)")
    ap.add_argument("--tiers", choices=["both", "basic", "advanced"], default="both")
    ap.add_argument("--video", choices=VIDEO_MODES, default="auto")
    ap.add_argument("--allow-no-speech", action="store_true",
                    help="proceed with documents only when there is no transcript, Whisper output or recording")
    ap.add_argument("--data-dir")
    ap.add_argument("--dry-run", action="store_true", help="resolve and print; write nothing")
    ap.add_argument("--json", metavar="OUT", help="also write inputs.json here")
    a = ap.parse_args(argv)

    try:
        for m in layout.migrate_legacy(layout.find_project(explicit=a.project, migrate=False)):
            print(m)
    except layout.ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        inputs, errors = resolve(a)
    except (prof.ProfileError, layout.ProjectError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return 2

    ddir = plat.resolve_data_dir(a.data_dir)
    sess = Path(inputs["session_dir"])
    target = sess / "inputs.json"
    if not a.dry_run:
        try:
            guard.assert_writable(target, ddir)
        except guard.GuardError as e:
            print(e, file=sys.stderr)
            return 2
        plat.write_json(target, inputs)
    if a.json:
        guard.assert_writable(a.json, ddir)
        plat.write_json(a.json, inputs)

    s = inputs["session"]
    print(f"{inputs['profile']['id']} session {s['n']}: {s['title']!r}  date {s['date']}  {s['duration_min']} min")
    t_ = inputs["transcript"]
    print(f"  transcript : {t_['path'] + '  (' + format(t_['bytes'], ',') + ' bytes, ' + t_['format'] + ')' if t_ else '-'}")
    print(f"  speech     : {inputs['speech']['primary'] or 'NONE'}  <- " + (", ".join(x['kind'] for x in inputs['speech']['sources']) or "-"))
    print(f"  recording  : {inputs['recording'] or '-'}" + (f"  ({inputs['recording_bytes']:,} bytes)" if inputs["recording"] else ""))
    w = inputs["whisper"]
    print(f"  whisper    : {w['path'] + '  [' + w['source'] + ']' if w else '-'}")
    f = inputs["frames"]
    print(f"  frames     : {f['dir'] + '  (' + str(f['count']) + ' frames, ' + f['source'] + ')' if f else '-'}")
    print(f"  video      : {inputs['video_action']}")
    print(f"  slides     : {len(inputs['slides'])}   handouts: {len(inputs['handouts'])}   classwork: {inputs['classwork']['dir'] if inputs['classwork'] else '-'}")
    print(f"  data: {len(inputs['data'])}   exercises: {len(inputs['exercises'])}   references: {len(inputs['references'])}")
    print(f"  course docs: {len(inputs['course_docs'])}   existing notes: "
          + (", ".join(f"{k}={Path(v).name}" for k, v in inputs['existing_notes'].items() if v) or "-"))
    print("  note paths : " + (", ".join(f"{k}={Path(v).name}" for k, v in inputs['note_paths'].items() if v)
                               or "- (first generation: choose the slug)"))
    for u in inputs["unassigned"]:
        cands = ", ".join(f"s{c['n']}:{c['score']}" for c in u["candidates"]) or "no candidates"
        print(f"  UNASSIGNED : {Path(u['path']).name}  [{u['kind']}; {cands}]")
    ic = inputs["instructor_code"]
    print(f"  code       : {ic['url'] if ic else '-'}")
    print(f"  prior notes: {len(inputs['prior_notes']['external'])} external, "
          f"{sum(len(v) for v in inputs['prior_notes']['project'].values())} in project")
    print(f"  tiers      : {', '.join(inputs['tiers'])}")
    for w_ in inputs["warnings"]:
        print(f"  warning: {w_}")
    print("dry run: nothing written" if a.dry_run else f"inputs.json -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
