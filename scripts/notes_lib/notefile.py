"""Note parsing and statistics. ``yaml`` is imported lazily (only when a note has front matter).

``stats()`` implements the budget formula from PLAN.md section 1.3:
    minutes = prose_words/150 + code_lines*8 s + table_rows*15 s
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .profile import split_front_matter

H1_RE = re.compile(r"^(\d+[a-z]?)\s*[·\-–—]\s*(.*)$")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*(.*?)\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
SWEEP_LINE_RE = re.compile(r"^\s*<!--\s*(/?)sweep:([\w.:-]+)\s*-->\s*$")
SWEEP_ANY_RE = re.compile(r"<!--\s*(/?)sweep:([\w.:-]+)\s*-->")
_INLINE_MARK_RE = re.compile(r"<!--\s*/?(?:sweep|pending-ref):[\w.:-]+\s*-->")
_HTML_RE = re.compile(r"^\s*(</?[A-Za-z][\w-]*(\s[^>]*)?/?>|<!--)")
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]*)\)")
_NUMBERED_RE = re.compile(r"^\s*(?:§\s*)?\d+(?:\.\d+)*[.)]?\s+")
_GAPFILL_LABELS = ("gap fill", "beyond class")


@dataclass
class Note:
    path: Path
    meta: dict
    h1: str          # text of the first H1, "" if none
    num: str         # "02" / "02a" (from the H1, else the file name), "" if unknown
    title: str       # H1 without its "NN · " prefix
    body: str        # text after the front matter
    legacy: bool     # True when there is no YAML front matter
    raw: str = field(default="", repr=False)


def note_id(name) -> tuple[str, str]:
    """('02a', 'slug') from '02a-slug.md' (path or name). Non-matching -> ('', stem)."""
    stem = Path(str(name)).name
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    m = re.match(r"^(\d+[a-z]?)-(.+)$", stem)
    return (m.group(1), m.group(2)) if m else ("", stem)


def pdf_name(prefix: str, num: str, slug: str, tier: str) -> str:
    """'CPP-02-slug-basic.pdf'."""
    return f"{prefix}-{num}-{slug}-{tier}.pdf"


def parse_note(path) -> Note:
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    meta, body = split_front_matter(text)
    legacy = not meta
    h1 = ""
    for kind, t, info in iter_blocks(body):
        if kind == "heading" and info == 1:
            h1 = t
            break
    num, title = "", h1
    m = H1_RE.match(h1)
    if m:
        num, title = m.group(1), m.group(2).strip()
    if not num:
        num = note_id(p.name)[0]
    return Note(path=p, meta=meta, h1=h1, num=num, title=title, body=body, legacy=legacy, raw=text)


def iter_blocks_ex(body: str) -> Iterator[tuple[str, str, object, int]]:
    """Like iter_blocks but yields ``(kind, text, info, line_no)`` (1-based first line)."""
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fenced = False
    for k, ln in enumerate(lines):                       # sweep marker lines (PLAN 16.3) are not content
        if _FENCE_RE.match(ln):
            fenced = not fenced
        elif not fenced and SWEEP_LINE_RE.match(ln):
            lines[k] = ""
        elif not fenced and "<!--" in ln:                # inline sweep / pending-ref markers (PLAN 18) are not content
            lines[k] = _INLINE_MARK_RE.sub("", ln)
    i, n = 0, len(lines)
    para: list[str] = []
    para_start = 0

    def flush():
        nonlocal para
        if para:
            out = ("prose", "\n".join(para), None, para_start)
            para = []
            return out
        return None

    while i < n:
        line = lines[i]
        m = _FENCE_RE.match(line)
        if m:
            f = flush()
            if f:
                yield f
            fence, info = m.group(1), m.group(2)
            start = i + 1
            i += 1
            buf: list[str] = []
            while i < n:
                c = _FENCE_RE.match(lines[i])
                if c and c.group(1)[0] == fence[0] and len(c.group(1)) >= len(fence) and not c.group(2):
                    break
                buf.append(lines[i])
                i += 1
            i += 1  # closing fence (or EOF)
            yield ("code", "\n".join(buf), info, start)
            continue
        if not line.strip():
            f = flush()
            if f:
                yield f
            i += 1
            continue
        h = _HEADING_RE.match(line)
        if h:
            f = flush()
            if f:
                yield f
            yield ("heading", h.group(2), len(h.group(1)), i + 1)
            i += 1
            continue
        if "|" in line and i + 1 < n and "|" in lines[i + 1] and _TABLE_DELIM_RE.match(lines[i + 1]) \
                and "-" in lines[i + 1]:
            f = flush()
            if f:
                yield f
            start = i + 1
            buf = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                buf.append(lines[i])
                i += 1
            yield ("table", "\n".join(buf), None, start)
            continue
        if line.lstrip().startswith(">"):
            f = flush()
            if f:
                yield f
            start = i + 1
            buf = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            yield ("quote", "\n".join(buf), None, start)
            continue
        if _HTML_RE.match(line):
            f = flush()
            if f:
                yield f
            start = i + 1
            buf = []
            while i < n and lines[i].strip() and (_HTML_RE.match(lines[i]) or buf):
                buf.append(lines[i])
                i += 1
                if lines[i - 1].lstrip().startswith("</") and lines[i - 1].rstrip().endswith(">"):
                    break
            yield ("html", "\n".join(buf), None, start)
            continue
        if not para:
            para_start = i + 1
        para.append(line)
        i += 1
    f = flush()
    if f:
        yield f


def iter_blocks(body: str) -> Iterator[tuple[str, str, object]]:
    """Fence-aware block iterator: ``(kind, text, info)``.

    kind: prose | code | table | heading | quote | html.
    info: code -> the fence info string (e.g. "cpp {run}"); heading -> level (int); else None.
    quote text has the leading ``>`` markers removed; heading text has the ``#`` markers removed.
    """
    for kind, text, info, _ln in iter_blocks_ex(body):
        yield kind, text, info


def minutes(prose: float, code: float, rows: float) -> float:
    """Reading/revision time: prose/150 + code lines * 8 s + table rows * 15 s."""
    return prose / 150.0 + code * 8.0 / 60.0 + rows * 15.0 / 60.0


def clean_inline(text: str) -> str:
    """Strip markdown syntax so what remains is what a reader reads."""
    t = _IMG_RE.sub(" ", text)
    t = _LINK_RE.sub(r"\1", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[*_`~]+", "", t)
    t = re.sub(r"^\s*(?:[-+*]|\d+[.)])\s+", "", t, flags=re.M)
    return t


def count_words(text: str) -> int:
    """Whitespace tokens that contain at least one letter or digit, after clean_inline()."""
    return sum(1 for tok in clean_inline(text).split() if re.search(r"\w", tok))


def _heading_key(text: str) -> str:
    t = clean_inline(text)
    t = _NUMBERED_RE.sub("", t).strip()
    return t.lower()


def stats(body: str, closing_sections=("Going Deeper", "Further Study", "Sources")) -> dict:
    """Budget statistics for a note body (front matter already removed).

    prose_words = words of prose, list items, callouts and headings; it excludes fenced code,
    tables, front matter and the closing sections (from a heading starting with one of
    ``closing_sections`` to the next heading of the same or higher level). code_lines counts
    every line inside fences and table_rows every table line (header and delimiter included);
    both exclude closing sections. This is the definition that reproduces the PLAN's calibration
    on the cpp notes 01-03 (22.5/31.7/31.9 min; this gives 22.2/30.4/31.5).
    ``links``/``figures`` count everywhere. ``gapfill_words`` are the words of callouts labelled
    "Gap fill" / "Beyond class" (they are included in prose_words).
    """
    closing = [c.lower() for c in closing_sections or ()]
    prose = code = rows = figs = links = gap = 0
    in_closing, closing_level = False, 0
    for kind, text, info in iter_blocks(body):
        if kind == "heading":
            lvl = int(info)  # type: ignore[arg-type]
            key = _heading_key(text)
            if in_closing and lvl <= closing_level:
                in_closing = False
            if not in_closing and any(key.startswith(c) for c in closing):
                in_closing, closing_level = True, lvl
            if not in_closing:
                prose += count_words(text)
            continue
        if kind in ("prose", "quote", "html", "table"):
            figs += len(_IMG_RE.findall(text))
            links += len(_LINK_RE.findall(text))
        if in_closing:
            continue
        if kind == "code":
            code += len(text.split("\n"))
        elif kind == "table":
            rows += len(text.split("\n"))
        elif kind in ("prose", "quote"):
            w = count_words(text)
            prose += w
            if kind == "quote":
                lead = clean_inline(text).strip().lower()
                if any(lead.startswith(lbl) for lbl in _GAPFILL_LABELS):
                    gap += w
        # html blocks (details/summary wrappers) carry no countable prose
    return {
        "prose_words": prose,
        "code_lines": code,
        "table_rows": rows,
        "figures": figs,
        "links": links,
        "gapfill_words": gap,
        "minutes": round(minutes(prose, code, rows), 1),
    }
