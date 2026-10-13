"""Page-number resolution, contents substitution, PDF outline/metadata and link fix-up (PyMuPDF)."""
from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path

import pymupdf

from .layout import PLACEHOLDER, SEC_RE


BIG_PT = 14.5          # chapter titles (.ctitle) are set at >= 15 pt, body and sub-headings below this


def resolve_pages(pdf: Path, chapters, skip_pages: int):
    """Walk the PDF in order and match section numbers (and chapter titles) in order.

    Monotonic matching means a cross-reference in a later note can never steal an earlier
    section's page.  Returns ``(pages, ypos, starts)``:

    * ``pages``  ``"s:<sid>"`` / ``"c:<chnum>"`` -> 1-based page (``c:`` = first section's page)
    * ``ypos``   ``"s:<sid>"`` -> y (points from the top) of the heading line
    * ``starts`` ``chnum`` -> ``(page, y)`` of the chapter title, for the chapter bookmark
    """
    doc = pymupdf.open(pdf)
    pages: list[list[tuple[str, float, float]]] = []
    for i in range(doc.page_count):
        lines: list[tuple[str, float, float]] = []
        for b in doc[i].get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                spans = l["spans"]
                size = max((s["size"] for s in spans), default=0.0)
                lines.append(("".join(s["text"] for s in spans), l["bbox"][1], size))
        pages.append(lines)
    doc.close()

    found: dict[str, int] = {}
    ypos: dict[str, float] = {}
    starts: dict[str, tuple[int, float]] = {}
    cursor = skip_pages
    title_cursor = skip_pages
    for ch in chapters:
        # chapter title: first big line at/after the previous chapter whose text starts the title
        probe = re.sub(r"\s+", " ", ch.title).strip()[:24].lower()
        for p in range(title_cursor, len(pages)):
            hit = next(((p, y) for text, y, size in pages[p]
                        if size >= BIG_PT and re.sub(r"\s+", " ", text).strip().lower().startswith(probe)), None)
            if hit:
                starts[ch.chnum] = (hit[0] + 1, hit[1])
                title_cursor = hit[0]
                cursor = max(cursor, hit[0])
                break
        for s in ch.sections:
            hit = None
            y = 0.0
            for p in range(cursor, len(pages)):
                for text, y0, _size in pages[p]:
                    mm = SEC_RE.match(text)
                    if mm and mm.group(1) == s["sid"]:
                        hit, y = p, y0
                        break
                if hit is not None:
                    break
            if hit is None:
                continue
            found[f"s:{s['sid']}"] = hit + 1
            ypos[f"s:{s['sid']}"] = y
            cursor = hit
    for ch in chapters:
        firsts = [found[f"s:{s['sid']}"] for s in ch.sections if f"s:{s['sid']}" in found]
        if ch.chnum in starts:
            found[f"c:{ch.chnum}"] = starts[ch.chnum][0]
        elif firsts:
            found[f"c:{ch.chnum}"] = min(firsts)
            starts[ch.chnum] = (min(firsts), 0.0)
    return found, ypos, starts


def substitute(html: str, pages: dict[str, int]) -> str:
    """Fill the fixed-width ``...`` contents placeholders with real page numbers."""
    def repl(mm: re.Match) -> str:
        val = pages.get(mm.group(1))
        return mm.group(0).replace(PLACEHOLDER, str(val) if val else "—")

    return re.sub(r'<span class="pn" data-ref="([^"]+)">' + PLACEHOLDER + "</span>", repl, html)


def _relativise_links(doc: "pymupdf.Document", build_dir: Path) -> int:
    """Make file links relative again.

    Chrome resolves an ``href`` against the HTML file (which lives in ``build/``) and bakes that
    absolute path into the PDF.  Strip the build dir back off so ``CPP-03-x.pdf`` and
    ``../advanced/x.pdf`` survive as written and work wherever the PDFs are moved.
    """
    base = os.path.normcase(os.path.abspath(build_dir))
    n = 0
    for page in doc:
        for link in page.get_links():
            kind = link.get("kind")
            target = None
            if kind == pymupdf.LINK_LAUNCH and link.get("file"):
                target = link["file"]
            elif kind == pymupdf.LINK_URI and str(link.get("uri", "")).lower().startswith("file:"):
                target = urllib.parse.unquote(urllib.parse.urlparse(link["uri"]).path)
            if not target:
                continue
            cleaned = target.lstrip("/") if re.match(r"^/[A-Za-z]:", target) else target
            rel = os.path.relpath(os.path.abspath(cleaned), base).replace("\\", "/")
            new = {"kind": pymupdf.LINK_LAUNCH, "from": link["from"], "file": rel}
            try:
                page.delete_link(link)
                page.insert_link(new)
                n += 1
            except Exception:
                pass
    return n


def finalize_pdf(pdf: Path, chapters, pages: dict[str, int], ypos: dict[str, float],
                 starts: dict[str, tuple[int, float]], *, title: str, metadata: dict,
                 contents_page: int | None, build_dir: Path) -> tuple[int, int]:
    """Write the outline + metadata, relativise file links, save in place. -> (pages, bookmarks)."""
    doc = pymupdf.open(pdf)
    _relativise_links(doc, build_dir)
    npages = doc.page_count
    toc: list[list] = []
    if contents_page:
        toc.append([1, "Contents", contents_page])
    for ch in chapters:
        p, y = starts.get(ch.chnum, (pages.get(f"c:{ch.chnum}", 1), 0.0))
        toc.append([1, f"{ch.label} — {ch.title}", p, {"kind": 1, "to": pymupdf.Point(0, max(0.0, y - 8))}])
        for s in ch.sections:
            sp = pages.get(f"s:{s['sid']}")
            if sp:
                y = ypos.get(f"s:{s['sid']}", 0.0)
                toc.append([s["level"], f"§{s['sid']}  {s['text']}", sp,
                            {"kind": 1, "to": pymupdf.Point(0, max(0.0, y - 8))}])
    # levels must start at 1 and never jump by more than one
    prev = 0
    for e in toc:
        e[0] = min(e[0], prev + 1)
        prev = e[0]
    try:
        doc.set_toc(toc)
    except Exception:
        doc.set_toc([e[:3] for e in toc])
    doc.set_metadata({"title": title, **metadata})
    tmp = pdf.with_suffix(".tmp.pdf")
    doc.save(tmp, deflate=True, garbage=3)
    doc.close()
    os.replace(tmp, pdf)
    return npages, len(toc)
