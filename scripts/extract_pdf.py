# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf"]
# ///
"""PDF -> per-page text (+ optional PNG) and outline. Replaces pdftotext/poppler.

    uv run extract_pdf.py <pdf> (--out DIR | --session N [--project DIR]) [--png] [--dpi 80] [--hires-dpi 200]
                          [--pages 1-5,9] [--json OUT]

--session N writes to <project>/.notes-plugin-internal/work/sessions/NN/slides/<pdf-stem>/.
Writes DIR/pages/NNN.txt (and NNN.png with --png) for each selected page, plus DIR/outline.json.
With --png it also writes DIR/hires/NNN.png at --hires-dpi (default 200; 0 = off): the agent reads the
small ``pages/NNN.png`` (token budget), ``class_figure.py`` crops figures from ``hires/`` when it exists.
Also used to look at *built* PDFs: the Read tool cannot render PDFs here, but it can read PNGs.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import guard, layout, platform as plat  # noqa: E402

SPARSE_CHARS = 25  # fewer extracted characters than this -> probably an image-only page


def parse_pages(spec: str | None, total: int) -> list[int]:
    """'1-5,9' / '3-' / '-4' -> sorted 1-based page numbers within 1..total."""
    if not spec:
        return list(range(1, total + 1))
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            lo = int(m.group(1)) if m.group(1) else 1
            hi = int(m.group(2)) if m.group(2) else total
            out.update(range(lo, hi + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ValueError(f"bad page spec {part!r}")
    return sorted(p for p in out if 1 <= p <= total)


# ---------------------------------------------------------------- Office files (stdlib only)
import posixpath
import re as _re
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


def _runs(el, ns) -> str:
    out = []
    for n in el.iter():
        if n.tag == ns + "t" and n.text:
            out.append(n.text)
        elif n.tag in (ns + "tab",):
            out.append("\t")
        elif n.tag in (ns + "br", ns + "cr"):
            out.append("\n")
    return "".join(out)


def _docx_text(z: zipfile.ZipFile) -> tuple[str, list]:
    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    lines: list[str] = []
    outline: list[dict] = []

    def para(p) -> str:
        txt = _runs(p, W).strip()
        ppr = p.find(W + "pPr")
        if ppr is None or not txt:
            return txt
        st = ppr.find(W + "pStyle")
        style = st.get(W + "val", "") if st is not None else ""
        m = _re.match(r"(?i)heading\s*(\d)", style) or (_re.match(r"(?i)title", style) and [None, "1"])
        if m:
            lvl = int(m[1])
            outline.append({"level": lvl, "title": txt, "page": 1})
            return "#" * lvl + " " + txt
        if ppr.find(W + "numPr") is not None:
            return "- " + txt
        return txt

    for el in (body if body is not None else []):
        if el.tag == W + "p":
            lines.append(para(el))
        elif el.tag == W + "tbl":
            for tr in el.iter(W + "tr"):
                cells = [" ".join(_runs(c, W).split()) for c in tr.findall(W + "tc")]
                lines.append("\t".join(cells))
            lines.append("")
    return "\n".join(lines) + "\n", outline


def _num(name: str) -> int:
    m = _re.search(r"(\d+)\.xml$", name)
    return int(m.group(1)) if m else 0


def _pptx_slides(z: zipfile.ZipFile) -> list[str]:
    names = sorted((n for n in z.namelist() if _re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=_num)
    out = []
    for n in names:
        root = ET.fromstring(z.read(n))
        paras = [_runs(p, A).strip() for p in root.iter(A + "p")]
        text = "\n".join(x for x in paras if x)
        nn = f"ppt/notesSlides/notesSlide{_num(n)}.xml"
        if nn in z.namelist():
            notes = [_runs(p, A).strip() for p in ET.fromstring(z.read(nn)).iter(A + "p")]
            notes = [x for x in notes if x and not x.isdigit()]
            if notes:
                text += "\n\n[notes]\n" + "\n".join(notes)
        out.append(text + "\n")
    return out


def office_main(a, src: Path, out: Path, ddir: Path) -> int:
    if a.png:
        print("warning: --png is a no-op for Office files (no rendering); embedded images go to media/",
              file=sys.stderr)
    try:
        z = zipfile.ZipFile(src)
    except zipfile.BadZipFile as e:
        print(f"error: cannot open {src}: {e}", file=sys.stderr)
        return 1
    kind = src.suffix.lower()[1:]
    outline: list = []
    if kind == "docx":
        text, outline = _docx_text(z)
        pages = [text]
    else:
        pages = _pptx_slides(z)
    sel = parse_pages(a.pages, len(pages))
    width = max(3, len(str(len(pages))))
    pdir = out / "pages"
    pdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for pno in sel:
        t = pages[pno - 1]
        name = f"{pno:0{width}d}"
        (pdir / f"{name}.txt").write_text(t, encoding="utf-8", newline="\n")
        rows.append({"page": pno, "txt": f"pages/{name}.txt", "chars": len(t.strip()), "images": 0, "png": None})
    media = []
    for n in z.namelist():
        if _re.match(r"(word|ppt)/media/[^/]+$", n):
            dest = out / "media" / posixpath.basename(n)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(n))
            media.append(f"media/{dest.name}")
    report = {"source": src.as_posix(), "kind": kind, "pages_total": len(pages), "extracted": len(rows),
              "metadata": {}, "outline": outline, "pages": rows, "media": media}
    plat.write_json(out / "outline.json", report)
    if a.json:
        guard.assert_writable(a.json, ddir)
        plat.write_json(a.json, report)
    unit = "slides" if kind == "pptx" else "document (1 text file)"
    print(f"{src.name}: {unit}, {len(pages)} page(s), extracted {len(rows)} -> {pdir}")
    print(f"outline: {len(outline)} headings; media: {len(media)} image(s) -> {out / 'media'}")
    return 0


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", help=".pdf, .docx or .pptx")
    ap.add_argument("--out", help="output directory (default: <session>/slides/<pdf-stem>)")
    ap.add_argument("--session", help="session number (used with --project for the default --out)")
    ap.add_argument("--project", help="project dir (default: discovered from cwd)")
    ap.add_argument("--png", action="store_true", help="also render each page to PNG")
    ap.add_argument("--dpi", type=int, default=80)
    ap.add_argument("--hires-dpi", type=int, default=200,
                    help="with --png: also write DIR/hires/NNN.png at this dpi for figure crops (default 200; 0 = off)")
    ap.add_argument("--pages", help="e.g. 1-5,9 (1-based; default all)")
    ap.add_argument("--json", metavar="OUT")
    ap.add_argument("--data-dir")
    a = ap.parse_args(argv)

    src = Path(a.pdf)
    if not src.is_file():
        print(f"error: pdf not found: {src}", file=sys.stderr)
        return 2
    try:
        out = layout.resolve_out(a.out, a.project, a.session, f"slides/{src.stem}")
    except layout.ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ddir = plat.resolve_data_dir(a.data_dir)
    try:
        guard.assert_writable(out / "outline.json", ddir)
    except guard.GuardError as e:
        print(e, file=sys.stderr)
        return 2

    if src.suffix.lower() in (".docx", ".pptx"):
        return office_main(a, src, out, ddir)
    try:
        import pymupdf as fitz  # PyMuPDF
    except ImportError:
        print("error: PyMuPDF missing; run with `uv run` so the PEP-723 dependency installs", file=sys.stderr)
        return 2
    try:
        doc = fitz.open(str(src))
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot open {src}: {e}", file=sys.stderr)
        return 1
    total = doc.page_count
    try:
        pages = parse_pages(a.pages, total)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not pages:
        print(f"error: no pages selected (document has {total})", file=sys.stderr)
        return 2

    width = max(3, len(str(total)))
    pdir = out / "pages"
    pdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for pno in pages:
        page = doc[pno - 1]
        text = page.get_text("text")
        name = f"{pno:0{width}d}"
        (pdir / f"{name}.txt").write_text(text, encoding="utf-8", newline="\n")
        row = {"page": pno, "txt": f"pages/{name}.txt", "chars": len(text.strip()),
               "images": len(page.get_images(full=True)), "png": None}
        if row["chars"] < SPARSE_CHARS:
            row["note"] = "little/no text layer; use --png to look at it"
        if a.png:
            pix = page.get_pixmap(dpi=a.dpi)
            pix.save(str(pdir / f"{name}.png"))
            row["png"] = f"pages/{name}.png"
            row["png_px"] = [pix.width, pix.height]
            if a.hires_dpi and a.hires_dpi > a.dpi:
                hp = out / "hires" / f"{name}.png"
                hp.parent.mkdir(parents=True, exist_ok=True)
                hpix = page.get_pixmap(dpi=a.hires_dpi)
                hpix.save(str(hp))
                row["hires"] = f"hires/{name}.png"
                row["hires_px"] = [hpix.width, hpix.height]
        rows.append(row)

    toc = [{"level": lv, "title": t, "page": p} for lv, t, p in doc.get_toc()]
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    report = {"source": src.as_posix(), "pages_total": total, "extracted": len(rows),
              "metadata": meta, "outline": toc, "pages": rows}
    plat.write_json(out / "outline.json", report)
    if a.json:
        guard.assert_writable(a.json, ddir)
        plat.write_json(a.json, report)

    sparse = [r["page"] for r in rows if r.get("note")]
    print(f"{src.name}: {total} pages, extracted {len(rows)} -> {pdir}")
    print(f"outline: {len(toc)} entries; png: {'yes @' + str(a.dpi) + 'dpi' if a.png else 'no'}"
          + (f"; hires: {a.hires_dpi}dpi -> {out / 'hires'}" if a.png and a.hires_dpi > a.dpi else ""))
    if sparse and len(sparse) == len(rows):
        print(f"warning: none of the {len(rows)} extracted pages has a text layer (scanned or handwritten "
              "slides): the PNGs (--png) are the content; Read them as images")
    elif sparse:
        print(f"warning: pages with almost no text (image-only?): {sparse[:20]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
