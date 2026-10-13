# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf", "imageio-ffmpeg"]
# ///
"""Archive a recording's extracted frames + Whisper into resources/, then (optionally) delete the video.

    uv run archive_video.py [--project D] (--session N | --all) [--confirm] [--dry-run] [--force]
                            [--allow-no-whisper] [--no-hires] [--data-dir D] [--json OUT]

PLAN 12.3. Per session with a recording ``resources/.../<stem>.mp4``:

  1. VALIDATE the ingest of THIS recording. Frames (not needed for audio-only recordings): ``index.md`` and
     ``frames.json`` with >= 1 frame (or ``meta.json`` says the video is blank), and ``meta.json`` naming
     this recording (same stem; same byte size when recorded). Whisper: valid JSON with >= 1 segment (or
     ``"no_speech": true``) from this recording. Sources are the session's ``.notes-plugin-internal/work/sessions/NN/video/``
     outputs, or an archive that already lives in ``resources/`` (a legacy archive without ``meta.json`` is
     accepted with a warning: nothing proves whose it is).
  2. ARCHIVE: copy the frames dir to ``resources/frames/<stem>/`` and the Whisper file to
     ``resources/<stem>.whisper.json``. An identical archive is skipped; a different one is refused
     without ``--force``.
  3. HIRES: make sure the archived frames dir holds ``hires/`` (native-resolution twins of every frame,
     the source of sharp note figures). Missing copies are regrabbed from the recording now - once the video
     is gone they cannot be. A recording whose hires copies could not all be produced is NEVER deleted
     (``--no-hires`` waives this, for a frames dir that has no per-frame regions).
  4. DELETE the recording ONLY with ``--confirm`` (never with ``--dry-run``), printing the bytes freed.

``--all`` handles every session that has a recording: sessions whose ingest is incomplete are skipped with
a reason (they do not fail the run); with ``--session`` a problem exits 1. ``--allow-no-whisper`` accepts a
frames-only ingest (the class had a transcript, so Whisper was never needed).

This is the ONLY script allowed to write into resources/: its writes are limited to
``resources/frames/<stem>/``, ``resources/<stem>.whisper.json`` and deleting the one recording
(see ``_assert_exempt``). The guard hook keeps blocking the Write/Edit tools there.

Exit 0 ok; 1 a requested session could not be archived/deleted; 2 usage/project error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import discover, guard, hires as hr, layout, platform as plat, profile as prof  # noqa: E402
import resolve_inputs  # noqa: E402

plat.utf8_stdio()


class ArchiveError(RuntimeError):
    pass


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _assert_exempt(path: Path, res: Path, stem: str, recording: Path) -> None:
    """The narrow exemption: only these three resources/ targets may ever be written or removed."""
    ok = (plat.is_under(path, res / "frames" / stem)
          or plat.norm(path) in (plat.norm(res / f"{stem}.whisper.json"),
                                 plat.norm(res / f"{stem}.whisper.json.part"), plat.norm(recording))
          or plat.is_under(path, res / "frames" / f"{stem}.partial"))
    if not ok:
        raise ArchiveError(f"internal: refusing to touch {path} (outside the archive exemption)")


# ---- validation -----------------------------------------------------------------------------------------
def validate_frames(d: Path, rec: Path, *, legacy_ok: bool) -> tuple[list[str], list[str], dict]:
    """(errors, warnings, info) for a frames dir against the recording ``rec``."""
    errs: list[str] = []
    warns: list[str] = []
    info: dict = {}
    if not (d / "index.md").is_file():
        errs.append(f"{d.as_posix()}: index.md missing")
    fj = _load_json(d / "frames.json") if (d / "frames.json").is_file() else None
    if fj is None:
        errs.append(f"{d.as_posix()}: frames.json missing or unreadable")
        count = 0
    else:
        count = len(fj) if isinstance(fj, list) else len(fj.get("frames", [])) if isinstance(fj, dict) else 0
    info["frames"] = count
    meta = _load_json(d / "meta.json") if (d / "meta.json").is_file() else None
    blank = bool(isinstance(meta, dict) and (meta.get("blank") is True or meta.get("frames") == 0))
    if count < 1 and not blank:
        errs.append(f"{d.as_posix()}: frames.json lists no frames and meta.json does not say the video is blank")
    if isinstance(meta, dict):
        if Path(str(meta.get("video", ""))).stem != rec.stem:
            errs.append(f"{d.as_posix()}: meta.json is for {meta.get('video')!r}, not {rec.name!r}")
        vb = meta.get("video_bytes")
        if isinstance(vb, int) and vb != rec.stat().st_size:
            errs.append(f"{d.as_posix()}: meta.json video_bytes {vb} != recording size {rec.stat().st_size}")
    elif legacy_ok:
        warns.append(f"{d.as_posix()}: no meta.json (legacy archive), ownership by recording not verified")
    else:
        errs.append(f"{d.as_posix()}: meta.json missing (cannot tell which recording these frames are from)")
    return errs, warns, info


def validate_whisper(p: Path, rec: Path) -> tuple[list[str], dict]:
    errs: list[str] = []
    d = _load_json(p)
    if not isinstance(d, dict) or not isinstance(d.get("segments"), list):
        return [f"{p.as_posix()}: not a valid Whisper JSON (no segments list)"], {}
    if not d["segments"] and d.get("no_speech") is not True:
        errs.append(f"{p.as_posix()}: 0 segments and the recording is not marked no_speech")
    if d.get("video") and Path(str(d["video"])).stem != rec.stem:
        errs.append(f"{p.as_posix()}: Whisper is for {d['video']!r}, not {rec.name!r}")
    return errs, {"segments": len(d["segments"])}


# ---- archive --------------------------------------------------------------------------------------------
def _tree(d: Path, *, skip_hires: bool = False) -> dict[str, tuple[int, str]]:
    """Relative path -> (size, sha) of every file; ``skip_hires`` leaves out hires/ (regenerable from the
    video, so a frames dir with and without it is still the same archive)."""
    return {f.relative_to(d).as_posix(): (f.stat().st_size, _sha(f)) for f in sorted(d.rglob("*"))
            if f.is_file() and not (skip_hires and f.relative_to(d).parts[0] == hr.HIRES_DIR)}


def archive_frames(src: Path, dst: Path, res: Path, stem: str, rec: Path, *, force: bool, dry: bool) -> str:
    """-> 'same-place' | 'skipped-identical' | 'copied' | 'replaced'. Raises ArchiveError on a differing archive."""
    if plat.norm(src) == plat.norm(dst):
        return "same-place"
    existed = dst.exists()
    if existed:
        if _tree(src, skip_hires=True) == _tree(dst, skip_hires=True):
            return "skipped-identical"
        if not force:
            raise ArchiveError(f"{dst.as_posix()} already holds a DIFFERENT archive; compare it, then re-run "
                               "with --force to replace it")
    if dry:
        return "replaced" if existed else "copied"
    tmp = res / "frames" / f"{stem}.partial"
    _assert_exempt(tmp, res, stem, rec)
    _assert_exempt(dst, res, stem, rec)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp)
    if _tree(tmp) != _tree(src):
        shutil.rmtree(tmp, ignore_errors=True)
        raise ArchiveError(f"copy of {src.as_posix()} did not verify")
    if existed:
        shutil.rmtree(dst)
    os.replace(tmp, dst)
    return "replaced" if existed else "copied"


def archive_whisper(src: Path, dst: Path, res: Path, stem: str, rec: Path, *, force: bool, dry: bool) -> str:
    if plat.norm(src) == plat.norm(dst):
        return "same-place"
    existed = dst.exists()
    if existed:
        if _sha(src) == _sha(dst):
            return "skipped-identical"
        if not force:
            raise ArchiveError(f"{dst.as_posix()} already exists and DIFFERS; re-run with --force to replace it")
    if dry:
        return "replaced" if existed else "copied"
    _assert_exempt(dst, res, stem, rec)
    tmp = dst.with_name(dst.name + ".part")
    _assert_exempt(tmp, res, stem, rec)
    shutil.copyfile(src, tmp)
    if _sha(tmp) != _sha(src):
        tmp.unlink(missing_ok=True)
        raise ArchiveError(f"copy of {src.as_posix()} did not verify")
    os.replace(tmp, dst)
    return "replaced" if existed else "copied"


# ---- per session ------------------------------------------------------------------------------------------
def process(lay: layout.Layout, profile: dict, n: int, args, explicit: bool) -> dict:
    ns = argparse.Namespace(project=str(lay.root), profile=args.profile, session=str(n), tiers="both",
                            video="auto", allow_no_speech=True, data_dir=args.data_dir)
    inputs, _errs = resolve_inputs.resolve(ns)
    res = lay.resources(profile)
    rec = Path(inputs["recording"]) if inputs.get("recording") else None
    out: dict = {"session": n, "recording": rec.as_posix() if rec else None, "status": "", "steps": [],
                 "errors": [], "warnings": [], "freed_bytes": 0, "bytes": rec.stat().st_size if rec else 0}
    if rec is None or not rec.is_file():
        out["status"] = "no-recording"
        out["steps"].append("no recording in resources/: nothing to archive or delete")
        return out
    if not plat.is_under(rec, res):
        out["status"] = "refused"
        out["errors"].append(f"{rec.as_posix()} is outside resources/: refusing to touch it")
        return out
    stem = rec.stem
    audio_only = rec.suffix.lower() in discover.AUDIO_EXT
    sess_video = lay.session(f"{n:02d}") / "video"
    f_dst, w_dst = res / "frames" / stem, res / f"{stem}.whisper.json"

    # pick the sources: the fresh session outputs, else an archive that already sits in resources/
    f_src = None
    if not audio_only:
        for cand, legacy in ((sess_video / "frames", False), (f_dst, True)):
            if (cand / "index.md").is_file():
                e, w, info = validate_frames(cand, rec, legacy_ok=legacy)
                out["warnings"] += w
                if not e:
                    f_src, out["frames"] = cand, info["frames"]
                    break
                out["errors"] += e
        if f_src is None and not out["errors"]:
            out["errors"].append(f"no extracted frames for {rec.name}: run the video pipeline first "
                                 f"(looked in {sess_video / 'frames'} and {f_dst})")
    w_src = None
    for cand in (sess_video / "whisper.json", w_dst):
        if cand.is_file():
            e, info = validate_whisper(cand, rec)
            if not e:
                w_src, out["segments"] = cand, info["segments"]
                break
            out["errors"] += e
    if w_src is None and not args.allow_no_whisper:
        if not any("Whisper" in x or "whisper" in x for x in out["errors"]):
            out["errors"].append(f"no valid Whisper output for {rec.name} (use --allow-no-whisper only if the "
                                 "class has a transcript and a frames-only ingest was intended)")
    elif w_src is None:
        out["warnings"].append("no Whisper output: continuing because --allow-no-whisper")
    if (f_src is None and not audio_only) or (w_src is None and not args.allow_no_whisper):
        out["status"] = "not-ingested"
        out["errors"] = list(dict.fromkeys(out["errors"]))
        return out
    out["steps"].append("validated: " + (f"{out.get('frames', 0)} frames" if f_src else "audio-only")
                        + (f", {out.get('segments', 0)} whisper segments" if w_src else ""))

    try:
        if f_src is not None:
            r = archive_frames(f_src, f_dst, res, stem, rec, force=args.force, dry=args.dry_run)
            out["steps"].append(f"frames -> resources/frames/{stem}/: {r}")
        if w_src is not None:
            r = archive_whisper(w_src, w_dst, res, stem, rec, force=args.force, dry=args.dry_run)
            out["steps"].append(f"whisper -> resources/{w_dst.name}: {r}")
    except ArchiveError as e:
        out["errors"].append(str(e))
        out["status"] = "archive-refused"
        return out

    if f_src is not None and not args.no_hires:
        hs = hr.status(f_dst if f_dst.is_dir() else f_src)
        if args.dry_run:
            out["steps"].append(f"hires: {hs['have']}/{hs['total']} present"
                                + (f"; would regrab {len(hs['missing'])} from the recording" if hs["missing"] else ""))
        elif hs["missing"]:
            _assert_exempt(f_dst / hr.HIRES_DIR, res, stem, rec)
            rg = hr.regrab(rec, f_dst, log=lambda m: None)
            hs = hr.status(f_dst)
            out["steps"].append(f"hires -> resources/frames/{stem}/hires: {hs['have']}/{hs['total']}"
                                f" ({rg['made']} regrabbed)")
        else:
            out["steps"].append(f"hires: {hs['have']}/{hs['total']} present in resources/frames/{stem}/hires")
        if hs["missing"] and not args.dry_run:
            out["errors"].append(f"hires copies missing for {len(hs['missing'])} of {hs['total']} frames "
                                 f"(e.g. {hs['missing'][0]}): not deleting the recording (--no-hires waives this)")
            out["status"] = "hires-missing"
            return out

    size = rec.stat().st_size
    if args.dry_run:
        out["status"] = "dry-run"
        out["steps"].append(f"would delete {rec.as_posix()} ({size:,} bytes)" if args.confirm
                            else f"delete needs --confirm ({size:,} bytes would be freed)")
    elif not args.confirm:
        out["status"] = "archived"
        out["steps"].append(f"recording kept: pass --confirm to delete {rec.name} ({size:,} bytes)")
    else:
        # last look before the irreversible step: the archive must really be there
        if (f_src is not None and not (f_dst / "index.md").is_file()) or (w_src is not None and not w_dst.is_file()):
            out["errors"].append("archive not found after copying: not deleting the recording")
            out["status"] = "archive-refused"
            return out
        _assert_exempt(rec, res, stem, rec)
        rec.unlink()
        out["freed_bytes"] = size
        out["status"] = "deleted"
        out["steps"].append(f"deleted {rec.as_posix()}: freed {size:,} bytes")
    return out


def session_numbers(lay: layout.Layout, profile: dict) -> list[int]:
    res = lay.resources(profile)
    if not res.is_dir():
        return []
    d = discover.discover(profile, res)
    return sorted({int(it.n) for it in d.items if it.kind == "recording" and it.scope == "session"
                   and it.n is not None})


def main(argv=None) -> int:
    if os.environ.get("NOTES_NO_VIDEO", "").strip() not in ("", "0"):
        print("archive skipped: video processing is disabled (NOTES_NO_VIDEO is set); recordings kept")
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", default=None, help="project dir (default: discovered from cwd)")
    ap.add_argument("--profile", default=None)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", help="session number (2a -> 2)")
    g.add_argument("--all", action="store_true", help="every session that has a recording")
    ap.add_argument("--confirm", action="store_true", help="really delete the recording (after archiving)")
    ap.add_argument("--dry-run", action="store_true", help="validate and show the plan; write nothing")
    ap.add_argument("--force", action="store_true", help="replace a DIFFERENT existing archive")
    ap.add_argument("--allow-no-whisper", action="store_true", help="accept a frames-only ingest")
    ap.add_argument("--no-hires", action="store_true",
                    help="do not require hires/ copies in the archive (frames.json without regions)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    try:
        lay = layout.Layout.for_project(a.project)
        profile = prof.load_profile(a.profile, data_dir=a.data_dir, project=lay.root)
        if a.all:
            ns = session_numbers(lay, profile)
        else:
            ns = [prof._session_n(a.session)]
    except (layout.ProjectError, prof.ProfileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    results = []
    bad = 0
    for n in ns:
        try:
            r = process(lay, profile, n, a, explicit=not a.all)
        except (OSError, ArchiveError) as e:
            r = {"session": n, "status": "error", "errors": [str(e)], "steps": [], "warnings": [],
                 "freed_bytes": 0, "recording": None, "bytes": 0}
        results.append(r)
        print(f"session {n:02d}: {r['status']}" + (f"  [{Path(r['recording']).name}]" if r.get("recording") else ""))
        for s in r["steps"]:
            print(f"  - {s}")
        for w in r["warnings"]:
            print(f"  warning: {w}")
        for e in r["errors"]:
            print(f"  {'error' if not a.all else 'skipped'}: {e}")
        if r["errors"] and not a.all and r["status"] != "no-recording":
            bad += 1
        elif r["status"] == "no-recording" and not a.all:
            pass
    freed = sum(r["freed_bytes"] for r in results)
    if not ns:
        print("no sessions with a recording found")
    print(f"\n{'dry run: ' if a.dry_run else ''}{len(results)} session(s); freed {freed:,} bytes"
          + ("" if a.confirm or a.dry_run else "  (recordings kept; --confirm deletes them)"))
    if a.json:
        guard.assert_writable(plat.norm(a.json), a.data_dir)
        plat.write_json(a.json, {"ok": bad == 0, "confirm": a.confirm, "dry_run": a.dry_run, "freed_bytes": freed,
                                 "sessions": results})
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
