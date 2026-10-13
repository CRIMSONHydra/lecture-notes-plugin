"""Readers for the tables of a lecture digest that other checks need (stdlib + notefile).

``parse_inventory``: section 2 concept rows ``{id, name, depth, minutes, assessable, required}``.
``parse_visuals``: section 11 "Class visuals" rows ``{id, frame, frame_n, time, concept, what, quality, crop,
treatment, line}``; ``D`` rows (extra figure opportunities) are not visuals and are skipped.

Columns are located by header name and fall back to the documented positions.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import notefile
from .profile import split_front_matter

DEPTH_RANK = {"mentioned": 0, "explained": 1, "demoed": 2, "drilled": 3}
TREATMENTS = ("embed", "redraw", "both", "text")
QUALITIES = ("clean", "messy", "partial")
TS_RE = re.compile(r"\d+:\d{2}(?::\d{2})?")


def cells(row: str) -> list[str]:
    row = row.strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") and not row.endswith("\\|") else row
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def _tables(digest_path: Path, section_kw: tuple[str, ...]):
    """Yield ``(header_cells, [(line, row_cells)])`` for each table under an H2 whose text has a keyword."""
    text = Path(digest_path).read_text(encoding="utf-8-sig")
    _meta, body = split_front_matter(text)
    cur = ""
    for kind, t, info, ln in notefile.iter_blocks_ex(body):
        if kind == "heading" and info == 2:
            cur = re.sub(r"^\s*\d+[.)]?\s+", "", t).lower()
        elif kind == "table" and any(cur.startswith(k) for k in section_kw):
            lines = t.split("\n")
            if len(lines) >= 2:
                yield [c.lower() for c in cells(lines[0])], [(ln + 2 + i, cells(r)) for i, r in enumerate(lines[2:])]


def _col(header: list[str], names: tuple[str, ...], default: int) -> int:
    for i, h in enumerate(header):
        if any(h.startswith(n) for n in names):
            return i
    return default


def parse_inventory(digest_path: Path) -> list[dict]:
    rows: list[dict] = []
    for header, trs in _tables(digest_path, ("concept inventory",)):
        ci_depth = _col(header, ("depth",), 2)
        ci_min = _col(header, ("minutes", "min"), 3)
        ci_ass = _col(header, ("assessable",), 5)
        ci_name = _col(header, ("concept",), 1)
        for ln, c in trs:
            if c and re.fullmatch(r"C\d+", c[0]):
                depth = (c[ci_depth].lower().split()[0] if len(c) > ci_depth and c[ci_depth] else "")
                rows.append({
                    "id": c[0], "name": c[ci_name] if len(c) > ci_name else "", "depth": depth,
                    "minutes": c[ci_min] if len(c) > ci_min else "", "line": ln,
                    "assessable": len(c) > ci_ass and c[ci_ass].lower().startswith("y"),
                    # explained or deeper must be covered with an example (PLAN 12.1)
                    "required": DEPTH_RANK.get(depth, 1) >= 1,
                })
        break
    return rows


def parse_crop(s: str):
    """(x0,y0,x1,y1) fractions or None."""
    try:
        v = [float(x) for x in re.split(r"[\s,]+", s.strip("[]() ")) if x]
    except ValueError:
        return None
    if len(v) != 4 or not (0 <= v[0] < v[2] <= 1.0001 and 0 <= v[1] < v[3] <= 1.0001):
        return None
    return tuple(v)


def parse_visuals(digest_path: Path) -> list[dict]:
    rows: list[dict] = []
    for header, trs in _tables(digest_path, ("class visuals", "figure and diagram")):
        ci = {k: _col(header, n, d) for k, n, d in (
            ("frame", ("frame",), 1), ("concept", ("concept",), 2), ("what", ("what",), 3),
            ("quality", ("quality",), 4), ("crop", ("crop",), 5), ("treatment", ("treatment",), 6))}
        for ln, c in trs:
            if not (c and re.fullmatch(r"V\d+", c[0])):
                continue
            g = lambda k: c[ci[k]] if len(c) > ci[k] else ""  # noqa: E731
            fm = re.search(r"F?(\d+)", g("frame"))
            ts = TS_RE.search(g("frame"))
            rows.append({"id": c[0], "frame": g("frame"), "frame_n": int(fm.group(1)) if fm else None,
                         "time": ts.group(0) if ts else None, "concept": g("concept"), "what": g("what"),
                         "quality": g("quality").lower(), "crop_raw": g("crop"), "crop": parse_crop(g("crop")),
                         "treatment": g("treatment").lower().strip("` "), "line": ln})
    return rows
