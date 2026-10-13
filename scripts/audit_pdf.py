# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf"]
# ///
"""Layout / font / figure / math audit for the built PDFs.

    uv run audit_pdf.py [<pdf|dir>...] [--project DIR --tier T] [--manifest M] [--only NAME...] [--json OUT]
                        [--margin-mm 15]

Problems (exit 1):
  * text (or an image) painted past the right content edge (+3 pt slack)
  * leftover ``...`` contents placeholders (page-number substitution failed)
  * Type 3 fonts (colour/bitmap glyph fallbacks that break older PDF.js viewers); offending chars listed
  * zero bookmarks on a PDF of more than 2 pages
  * fewer embedded images than the manifest's ``images_expected``
  * KaTeX / Mermaid errors recorded in the manifest

Warnings: sparse pages (< 220 chars and no image covering 30 % of the page; a compendium's cover is
exempt), raw-markdown leftovers (a code fence, ``](`` or more than 3 ``**`` on a page).

``--only NAME...`` restricts the audit to the PDFs with these file names (found in the given dirs): the
per-note scope used by ``qa.py --notes ... --no-rollup``, so a sibling note's (or the compendium's) PDF in the same
folder can neither fail nor be touched by the check. A named PDF that is absent is itself a problem. ``--manifest``
may be a ``build_pdf.py --json`` report (same ``pdfs`` entries): the entries of *this* build, not the shared,
concurrently-rewritten ``manifest.json``.

With no inputs, audits ``<project>/pdf/<tier>/`` (every tier unless --tier). The manifest is
``<project>/.notes-plugin-internal/work/build/<tier>/manifest.json`` written by build_pdf.py; if ``--manifest`` is omitted it
is derived from the PDF's location (``<project>/pdf/<tier>/x.pdf``), falling back to ``../build/manifest.json``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402

from notes_lib import layout as projlayout, platform as plat  # noqa: E402
from render import layout  # noqa: E402

plat.utf8_stdio()

SPARSE_CHARS = 220


def type3_fonts(doc: "pymupdf.Document") -> list[dict]:
    """Type 3 fonts with the characters they carry (ported from the CV builder)."""
    seen: dict[int, dict] = {}
    for pno in range(doc.page_count):
        for ent in doc[pno].get_fonts(full=True):
            if ent[2] != "Type3":
                continue
            xref = ent[0]
            if xref in seen:
                seen[xref]["pages"].append(pno + 1)
                continue
            chars: list[str] = []
            tu = doc.xref_get_key(xref, "ToUnicode")
            if tu and tu[0] == "xref":
                txt = doc.xref_stream(int(tu[1].split()[0])).decode("latin-1")
                for _a, b in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", txt):
                    if len(b) <= 6:
                        try:
                            ch = chr(int(b, 16))
                            if ch != "ÿ":          # the .notdef placeholder
                                chars.append(f"{ch} U+{ord(ch):04X}")
                        except Exception:
                            pass
            seen[xref] = {"name": ent[3], "pages": [pno + 1], "chars": chars}
    return list(seen.values())


def audit(path: Path, entry: dict | None, margin_mm: float) -> dict:
    doc = pymupdf.open(path)
    n = doc.page_count
    right_limit = doc[0].rect.width - margin_mm * layout.PT_PER_MM + layout.SLACK_PT
    is_comp = "compendium" in path.name.lower() or (entry or {}).get("kind") == "compendium"

    overflows: list[tuple[int, float, str]] = []
    placeholders: list[int] = []
    sparse: list[tuple[int, int]] = []
    rawmd: list[tuple[int, str]] = []
    images = 0

    for i, page in enumerate(doc, start=1):
        text = page.get_text("text")
        if layout.PLACEHOLDER in text:
            placeholders.append(i)
        images += len(page.get_images(full=True))
        stripped = len(text.strip())
        if stripped < SPARSE_CHARS and not (is_comp and i == 1):
            area = page.rect.width * page.rect.height
            big_img = any((r.width * r.height) >= 0.30 * area
                          for r in (pymupdf.Rect(info["bbox"]) for info in page.get_image_info()))
            if not big_img:
                sparse.append((i, stripped))
        if "```" in text:
            rawmd.append((i, "code fence"))
        if "](" in text:
            rawmd.append((i, "](  link syntax"))
        if text.count("**") > 3:
            rawmd.append((i, "**bold** markers"))
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") == 1:
                x1 = block["bbox"][2]
                if x1 > right_limit:
                    overflows.append((i, round(x1 - right_limit, 1), "<image>"))
                continue
            for line in block.get("lines", []):
                for span in line["spans"]:
                    x1 = span["bbox"][2]
                    if x1 > right_limit and span["text"].strip():
                        overflows.append((i, round(x1 - right_limit, 1), span["text"].strip()[:70]))

    t3 = type3_fonts(doc)
    bookmarks = len(doc.get_toc())
    doc.close()

    problems: list[str] = []
    details: dict = {}
    if overflows:
        problems.append(f"RIGHT-MARGIN OVERFLOW: {len(overflows)} span(s)")
        details["overflow"] = [{"page": p, "over_pt": o, "text": t} for p, o, t in overflows[:50]]
    if placeholders:
        problems.append(f"CONTENTS PLACEHOLDERS still present on pages {placeholders}")
        details["placeholders"] = placeholders
    if t3:
        problems.append(f"TYPE 3 FONTS: {len(t3)} font(s) on pages "
                        f"{sorted({p for f in t3 for p in f['pages']})}")
        details["type3"] = t3
    if n > 2 and bookmarks == 0:
        problems.append(f"NO BOOKMARKS on a {n}-page PDF")
    if entry and entry.get("overflow_blocks"):
        ob = entry["overflow_blocks"]
        problems.append(f"RIGHT-MARGIN OVERFLOW: {len(ob)} code/table block(s) wider than the page "
                        f"(render probe), e.g. {ob[0].get('t', '')[:50]!r} +{ob[0].get('px')}px")
        details["overflow_blocks"] = ob
    if entry:
        exp = int(entry.get("images_expected") or 0)
        if images < exp:
            problems.append(f"IMAGES: {images} embedded, {exp} expected")
        if entry.get("katex_errors"):
            problems.append(f"KATEX ERRORS: {entry['katex_errors']} (see manifest)")
        if entry.get("mermaid_errors"):
            problems.append(f"MERMAID ERRORS: {entry['mermaid_errors']} (see manifest)")
    warnings: list[str] = []
    if sparse:
        warnings.append(f"sparse pages (<{SPARSE_CHARS} chars): {[p for p, _ in sparse]} -- check for stranded headings")
    if rawmd:
        warnings.append("raw-markdown leftovers: " + ", ".join(f"p{p} {w}" for p, w in rawmd[:8]))
    return {
        "file": path.name, "path": path.resolve().as_posix(), "pages": n, "bookmarks": bookmarks,
        "images": images, "images_expected": (entry or {}).get("images_expected"),
        "problems": problems, "warnings": warnings, "details": details,
        "manifest": bool(entry),
    }


def collect(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for a in inputs:
        p = plat.norm(a)
        if p.is_dir():
            files += sorted(p.glob("*.pdf"))
        elif p.is_file():
            files.append(p)
        else:
            print(f"not found: {a}", file=sys.stderr)
    return files


def default_manifest(pdf: Path) -> Path:
    """<project>/.notes-plugin-internal/work/build/<tier>/manifest.json for <project>/pdf/<tier>/x.pdf, else ../build/manifest.json."""
    pdf = plat.norm(pdf)
    d = pdf.parent
    if d.name in projlayout.TIERS and d.parent.name == "pdf":
        cand = projlayout.Layout(d.parent.parent).build(d.name) / "manifest.json"
        if cand.is_file():
            return cand
    return d.parent / "build" / "manifest.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit built PDFs (layout, Type 3, images, math, bookmarks).")
    ap.add_argument("inputs", nargs="*", help="PDF files and/or directories of PDFs (default: the project's pdf/<tier>/)")
    ap.add_argument("--project", help="project dir (default: discovered from cwd) -- used when no inputs are given")
    ap.add_argument("--tier", choices=("basic", "advanced"), help="with no inputs: only this tier")
    ap.add_argument("--manifest", help="build/manifest.json (default: ../build/manifest.json beside the PDFs)")
    ap.add_argument("--only", nargs="+", metavar="NAME", help="audit only the PDFs with these file names")
    ap.add_argument("--json", metavar="OUT", help="write the report as JSON")
    ap.add_argument("--margin-mm", type=float, default=layout.MARGIN_MM)
    args = ap.parse_args(argv)

    inputs = list(args.inputs)
    if not inputs:
        try:
            lay = projlayout.Layout.for_project(args.project)
        except projlayout.ProjectError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        inputs = [str(lay.pdf(t)) for t in ([args.tier] if args.tier else projlayout.TIERS) if lay.pdf(t).is_dir()]
    files = collect(inputs)
    missing_only: list[str] = []
    if args.only:
        want = {n.lower() for n in args.only}
        files = [f for f in files if f.name.lower() in want]
        missing_only = sorted(want - {f.name.lower() for f in files})
    if not files and not missing_only:
        print("no PDFs to audit", file=sys.stderr)
        return 2

    manifests: dict[Path, dict] = {}

    def entries_for(pdf: Path) -> dict:
        mp = plat.norm(args.manifest) if args.manifest else default_manifest(pdf)
        if mp not in manifests:
            try:
                doc = json.loads(mp.read_text(encoding="utf-8-sig"))
                manifests[mp] = {e["name"]: e for e in doc.get("pdfs", [])}
            except Exception:
                manifests[mp] = {}
        return manifests[mp]

    reports = []
    total = 0
    for f in files:
        try:
            r = audit(f, entries_for(f).get(f.name), args.margin_mm)
        except Exception as e:                   # unreadable / half-written PDF: a problem, never a crash
            r = {"file": f.name, "path": f.resolve().as_posix(), "pages": 0, "bookmarks": 0, "images": 0,
                 "images_expected": None, "problems": [f"UNREADABLE PDF: {type(e).__name__}: {str(e)[:120]}"],
                 "warnings": [], "details": {}, "manifest": False}
        reports.append(r)
        total += len(r["problems"])
        mark = "" if r["manifest"] else "  (no manifest entry: image/math checks skipped)"
        print(f"\n{r['file']}  --  {r['pages']} pages, {r['bookmarks']} bookmarks, {r['images']} images{mark}")
        if not r["problems"]:
            print("  clean")
        for p in r["problems"]:
            print(f"  PROBLEM: {p}")
        for o in r["details"].get("overflow", [])[:12]:
            print(f"    p{o['page']:<3} +{o['over_pt']:>6}pt  {o['text']!r}")
        for t in r["details"].get("type3", []):
            print(f"    Type 3 font {t['name']} pages {t['pages']}: {t['chars'] or '(unknown glyph)'}")
        for w in r["warnings"]:
            print(f"  warning: {w}")
    for n in missing_only:
        total += 1
        reports.append({"file": n, "path": None, "pages": 0, "bookmarks": 0, "images": 0, "images_expected": None,
                        "problems": [f"PDF NOT FOUND: {n}"], "warnings": [], "details": {}, "manifest": False})
        print(f"\n{n}\n  PROBLEM: PDF NOT FOUND")
    print(f"\n{'FAIL' if total else 'OK'} -- {total} problem(s) across {len(reports)} file(s)")
    if args.json:
        plat.write_json(args.json, {"ok": total == 0, "problems": total,
                                    "warnings": sum(len(r["warnings"]) for r in reports), "files": reports})
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
