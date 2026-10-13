# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf", "imageio-ffmpeg"]
# ///
"""The ledger ``<project>/.notes-plugin-internal/state.json`` (PLAN 13.2): record, adopt, inspect, rename, clean.

    uv run ledger.py record --project D --session N --tier basic|advanced --status ok|failed|partial
                            [--qa-json F] [--mode write|revise|sweep]
    uv run ledger.py adopt  --project D [--tiers both|basic|advanced]
    uv run ledger.py show   --project D [--session N] [--json]
    uv run ledger.py forget --project D --session N
    uv run ledger.py rename-slug --project D --session N --slug NEW
    uv run ledger.py clean  --project D (--session N | --all)

record        snapshot the session's CURRENT content fingerprints (transcript, whisper, frames, slides,
              handouts, classwork, code tree sha), note/pdf paths and the status of this run. ``ok`` needs
              the qa report (--qa-json) to say ``ok: true``, otherwise it is stored as ``partial``.
adopt         create entries for notes in notes/<tier>/NN-*.md that have none (fingerprints = current inputs).
rename-slug   rename notes/<tier>/NN-<old>.md -> NN-<new>.md, figures ``NN-<old>-*`` -> ``NN-<new>-*``
              (references rewritten), the PDF, the work/meta/<tier>/NN-<old>.* sidecars; ledger updated.
              Regenerate the index (update_index.py) and the compendium afterwards.
clean         PLAN 13.7 post-run cleanup of regenerable heavy copies: work/build/<tier>/ HTML + Chrome profiles
              (manifest.json and qa/ are kept), work/sessions/NN/slides/*/pages/*.png, work/sessions/NN/code/.
              (--keep-work is the caller's choice: just do not call this.)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import guard, layout, ledger, notefile, platform as plat, profile as prof  # noqa: E402
import resolve_inputs  # noqa: E402


def fresh_inputs(lay, profile, n, a, cache=None):
    ns = SimpleNamespace(project=str(lay.root), profile=getattr(a, "profile", None), session=str(n), tiers="both",
                         video="auto", allow_no_speech=True, data_dir=getattr(a, "data_dir", None))
    return resolve_inputs.resolve(ns, cache if cache is not None else {})


def cmd_record(a, lay, profile) -> int:
    n = int(a.session)
    nn = f"{n:02d}"
    saved = {}
    ij = lay.session(nn) / "inputs.json"
    if ij.is_file():
        try:
            saved = json.loads(ij.read_text(encoding="utf-8-sig"))
        except ValueError:
            saved = {}
    if a.mode == "sweep":                      # a sweep never re-resolves inputs (PLAN 16.3)
        inputs, errors = {"session": {"nn": nn, "n": n}}, []
    else:
        inputs, errors = fresh_inputs(lay, profile, n, a)
    if errors and saved:
        inputs = saved
    elif errors:
        print("error: " + "; ".join(errors), file=sys.stderr)
        return 2
    qa_ok = None
    if a.qa_json:
        try:
            qa_ok = bool(json.loads(Path(a.qa_json).read_text(encoding="utf-8-sig")).get("ok"))
        except (OSError, ValueError) as e:
            print(f"error: cannot read --qa-json: {e}", file=sys.stderr)
            return 2
    mode = a.mode or ("revise" if (saved.get("existing_notes") or {}).get(a.tier) else "write")
    if not a.mode and not saved:
        mode = "revise" if (inputs.get("existing_notes") or {}).get(a.tier) else "write"
    guard.assert_writable(lay.state_path, plat.resolve_data_dir(a.data_dir))
    status = a.status
    if status == "ok" and qa_ok is False:
        status = "partial"
        print("qa is not ok: recorded as partial")
    ent = ledger.record(lay, profile, inputs, a.tier, status, qa_ok=qa_ok, mode=mode)
    t = ent["tiers"][a.tier]
    if mode == "sweep":
        print(f"recorded sweep of session {nn} {a.tier} (inputs_hash unchanged)")
        return 0
    print(f"recorded session {nn} {a.tier}: {t['status']} (note {t['note']}, inputs {ent['inputs_hash'][:12]})")
    return 0


def cmd_adopt(a, lay, profile) -> int:
    tiers = list(layout.TIERS) if a.tiers == "both" else [a.tiers]
    state = ledger.load(lay)
    cache: dict = {}
    done = []
    for t in tiers:
        for nn, files in sorted(ledger.note_files(lay, t).items()):
            if not ledger.primary_notes(files):
                continue
            if ((state["sessions"].get(nn) or {}).get("tiers") or {}).get(t):
                continue
            inputs, errors = fresh_inputs(lay, profile, int(nn), a, cache)
            if errors:
                print(f"warning: session {nn}: {'; '.join(errors)}", file=sys.stderr)
                continue
            ledger.record(lay, profile, inputs, t, "ok", mode="write", adopted=True, state=state, save_it=False)
            done.append(f"{nn}/{t}")
    if done:
        guard.assert_writable(lay.state_path, plat.resolve_data_dir(a.data_dir))
        with ledger.locked(lay):
            ledger.save(lay, state)
    print(f"adopted {len(done)} note(s): {', '.join(done) or '-'}")
    return 0


def cmd_show(a, lay, profile) -> int:
    st = ledger.load(lay)
    rows = st["sessions"]
    if a.session:
        rows = {k: v for k, v in rows.items() if int(k) == int(a.session)}
    if a.json:
        print(json.dumps({**st, "sessions": rows}, indent=2, ensure_ascii=False))
        return 0
    print(f"ledger {lay.state_path.as_posix()} (updated {st.get('updated')})")
    for nn, e in sorted(rows.items()):
        print(f"  {nn} {e.get('title')!r} order {e.get('order')} slug {e.get('slug')} inputs {str(e.get('inputs_hash'))[:12]}")
        for t, te in (e.get("tiers") or {}).items():
            print(f"      {t:<8} {te.get('status'):<8} qa_ok={te.get('qa_ok')} {te.get('mode')} {te.get('note')} "
                  f"@ {te.get('generated_at')}")
    if not rows:
        print("  (no sessions recorded)")
    return 0


def cmd_forget(a, lay, profile) -> int:
    ok = ledger.forget(lay, a.session)
    print("forgotten" if ok else "no such session in the ledger")
    return 0


def _rewrite_refs(md: Path, old_prefix: str, new_prefix: str) -> int:
    text = md.read_text(encoding="utf-8-sig")
    new = text.replace(f"figures/{old_prefix}", f"figures/{new_prefix}")
    if new != text:
        md.write_text(new, encoding="utf-8", newline="\n")
    return text.count(f"figures/{old_prefix}")


def cmd_rename_slug(a, lay, profile) -> int:
    n = int(a.session)
    nn = f"{n:02d}"
    slug = a.slug.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        print("error: --slug must be kebab-case [a-z0-9-]", file=sys.stderr)
        return 2
    ddir = plat.resolve_data_dir(a.data_dir)
    state = ledger.load(lay)
    ent = state["sessions"].get(nn) or {}
    moved = 0
    for t in layout.TIERS:
        prim = ledger.primary_notes(ledger.note_files(lay, t).get(nn, []))
        if not prim:
            continue
        old = prim[0]
        old_slug = notefile.note_id(old.name)[1]
        if old_slug == slug:
            continue
        new = old.with_name(f"{nn}-{slug}.md")
        if new.exists():
            print(f"error: {new} already exists", file=sys.stderr)
            return 1
        for d in (old, new):
            guard.assert_writable(d, ddir)
        old.rename(new)
        moved += 1
        figs = lay.figures(t)
        if figs.is_dir():
            for f in sorted(figs.glob(f"{nn}-{old_slug}-*")):
                nf = f.with_name(f.name.replace(f"{nn}-{old_slug}-", f"{nn}-{slug}-", 1))
                if not nf.exists():
                    f.rename(nf)
            _rewrite_refs(new, f"{nn}-{old_slug}-", f"{nn}-{slug}-")
        op = lay.pdf(t) / notefile.pdf_name(profile["prefix"], nn, old_slug, t)
        np_ = lay.pdf(t) / notefile.pdf_name(profile["prefix"], nn, slug, t)
        if op.is_file():
            guard.assert_writable(np_, ddir)
            if np_.exists():
                np_.unlink()
            op.rename(np_)
        md = lay.meta(t)
        if md.is_dir():
            for f in sorted(md.glob(f"{nn}-{old_slug}.*")):
                f.rename(f.with_name(f.name.replace(f"{nn}-{old_slug}.", f"{nn}-{slug}.", 1)))
        te = (ent.get("tiers") or {}).get(t)
        if te:
            te["note"] = ledger.rel(lay, new)
            te["pdf"] = ledger.rel(lay, np_)
        print(f"{t}: {old.name} -> {new.name}")
    if ent:
        ent["slug"] = slug
        guard.assert_writable(lay.state_path, ddir)
        with ledger.locked(lay):
            ledger.save(lay, state)
    print(f"renamed {moved} note(s); now run update_index.py and build_pdf.py --compendium-only --prune per tier")
    return 0 if moved or ent else 1


def cmd_clean(a, lay, profile) -> int:
    freed = 0
    removed = 0

    def rm(p: Path):
        nonlocal freed, removed
        try:
            if p.is_dir():
                freed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                shutil.rmtree(p, ignore_errors=True)
            else:
                freed += p.stat().st_size
                p.unlink()
            removed += 1
        except OSError:
            pass

    for t in layout.TIERS:
        b = lay.build(t)
        if not b.is_dir():
            continue
        for item in sorted(b.iterdir()):
            if item.is_file() and item.suffix.lower() in (".html", ".htm"):
                rm(item)
            elif item.is_dir() and item.name not in ("qa",):
                rm(item)
    nums = [int(a.session)] if a.session else sorted(
        int(p.name) for p in (lay.work / "sessions").glob("*") if p.name.isdigit())
    for n in nums:
        sd = lay.session(n)
        slides = sd / "slides"
        if slides.is_dir():
            for pg in slides.glob("*/pages"):
                rm(pg)
        if (sd / "code").is_dir():
            rm(sd / "code")
    print(f"cleaned {removed} item(s), {freed / 1e6:.1f} MB freed (kept digest, research, screen-notes, "
          "inputs.json, logs, reports, manifests)")
    return 0


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--project")
        sp.add_argument("--profile")
        sp.add_argument("--data-dir")

    sp = sub.add_parser("record")
    common(sp)
    sp.add_argument("--session", required=True)
    sp.add_argument("--tier", required=True, choices=list(layout.TIERS))
    sp.add_argument("--status", required=True, choices=["ok", "failed", "partial"])
    sp.add_argument("--qa-json")
    sp.add_argument("--mode", choices=["write", "revise", "sweep"])
    sp = sub.add_parser("adopt")
    common(sp)
    sp.add_argument("--tiers", choices=["both", "basic", "advanced"], default="both")
    sp = sub.add_parser("show")
    common(sp)
    sp.add_argument("--session")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("forget")
    common(sp)
    sp.add_argument("--session", required=True)
    sp = sub.add_parser("rename-slug")
    common(sp)
    sp.add_argument("--session", required=True)
    sp.add_argument("--slug", required=True)
    sp = sub.add_parser("clean")
    common(sp)
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--session")
    g.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    try:
        root = layout.find_project(explicit=a.project, migrate=False)
        for m in layout.migrate_legacy(root):
            print(m, file=sys.stderr)
        lay = layout.Layout(root)
        profile = prof.load_profile(a.profile, data_dir=plat.resolve_data_dir(a.data_dir), project=root)
    except (layout.ProjectError, prof.ProfileError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    fn = {"record": cmd_record, "adopt": cmd_adopt, "show": cmd_show, "forget": cmd_forget,
          "rename-slug": cmd_rename_slug, "clean": cmd_clean}[a.cmd]
    try:
        return fn(a, lay, profile)
    except guard.GuardError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
