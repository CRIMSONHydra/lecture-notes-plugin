# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Validate a lecture digest against the schema in PLAN 3.5.

    uv run check_digest.py <digest.md> [--duration-min N] [--json OUT]

Optional front matter `speech_primary: transcript|whisper|recording|none` is validated when present;
`sources.transcript` may be null (Whisper-primary sessions).

Errors (exit 1):
  * front matter keys: profile, session, title_syllabus, title_as_taught, slug (kebab-case), date,
    duration_min, sources{transcript, whisper, frames_total, frames_read, instructor_code, slides,
    classwork}, repo_moved_on, audio_quality (good|patchy|poor), dropped_minutes, verified_as_of
  * the 13 numbered H2 sections, present, correctly numbered, in order, no extras
  * section 11 `Class visuals` (the old title "Figure and diagram opportunities" is still accepted): every
    `Vnn` row has a frame ref (`F067 (00:53:31)`), a known concept, quality clean|messy|partial, crop
    `x0,y0,x1,y1` as fractions (required unless treatment is `text`) and treatment embed|redraw|both|text
  * unique C/G/K/P/N/D/V ids; every `### Cnn` detail heading and every Gaps `C-ref` names an existing
    concept in the inventory
  * timeline: every row has a parseable `hh:mm-hh:mm` range, and the union of the timeline rows and the
    Reconstructed-passages ranges leaves no uncovered stretch longer than 10 minutes between 00:00 and
    the duration (a break or lost audio is explained by giving it a row)
Warnings: concepts without a detail heading; Gaps rows with no concept reference; frames_read > frames_total.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import digest as digest_lib, guard, notefile, platform as plat, profile as prof  # noqa: E402

plat.utf8_stdio()

FM_KEYS = ["profile", "session", "title_syllabus", "title_as_taught", "slug", "date", "duration_min",
           "sources", "repo_moved_on", "audio_quality", "dropped_minutes", "verified_as_of"]
SRC_KEYS = ["transcript", "whisper", "frames_total", "frames_read", "instructor_code", "slides", "classwork"]
SECTIONS = ["session timeline", "concept inventory", "concept detail", "gaps", "class correction",
            "reconstructed", "code and output", "claims and numbers", "vocabulary", "course links",
            "class visuals", "emphasis and assessment", "open questions"]
LEGACY_SECTION_11 = "figure and diagram"
GAP_MIN = 10.0
TS_RE = re.compile(r"\d+:\d{2}(?::\d{2})?")


def alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def parse_ts(s: str) -> float:
    parts = [int(x) for x in s.split(":")]
    return parts[0] * 60 + parts[1] + (parts[2] / 60 if len(parts) == 3 else 0)


def cells(row: str) -> list[str]:
    row = row.strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") else row
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def check(path: Path, duration_override: float | None) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []

    def E(code, msg, line=None):
        errors.append({"code": code, "msg": msg, "line": line})

    def W(code, msg, line=None):
        warnings.append({"code": code, "msg": msg, "line": line})

    try:
        meta, body = prof.split_front_matter(path.read_text(encoding="utf-8-sig"))
    except prof.ProfileError as e:
        E("front-matter", str(e))
        return {"path": path.as_posix(), "errors": errors, "warnings": warnings}
    if not meta:
        E("front-matter", "digest has no YAML front matter")
    for k in FM_KEYS:
        if k not in meta:
            E("fm-missing", f"front matter lacks `{k}`")
    src = meta.get("sources")
    if "sources" in meta:
        if not isinstance(src, dict):
            E("fm-missing", "front matter `sources` must be a mapping")
        else:
            for k in SRC_KEYS:
                if k not in src:
                    E("fm-missing", f"front matter `sources` lacks `{k}`")
            ft, fr = src.get("frames_total"), src.get("frames_read")
            if isinstance(ft, int) and isinstance(fr, int) and fr > ft:
                W("frames-count", f"frames_read ({fr}) exceeds frames_total ({ft})")
    slug = meta.get("slug")
    if "slug" in meta and not (isinstance(slug, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)):
        E("fm-slug", f"slug {slug!r} must be kebab-case [a-z0-9-]")
    if "audio_quality" in meta and meta["audio_quality"] not in ("good", "patchy", "poor"):
        E("fm-audio", f"audio_quality {meta['audio_quality']!r} must be good|patchy|poor")
    if "speech_primary" in meta and meta["speech_primary"] not in ("transcript", "whisper", "recording", "none"):
        E("fm-speech", f"speech_primary {meta['speech_primary']!r} must be transcript|whisper|recording|none")
    if "dropped_minutes" in meta and not isinstance(meta["dropped_minutes"], (int, float)):
        E("fm-dropped", "dropped_minutes must be a number")

    blocks = list(notefile.iter_blocks_ex(body))
    h2 = [(ln, t) for kind, t, info, ln in blocks if kind == "heading" and info == 2]
    # sections ---------------------------------------------------------------------------------------------
    pos: dict[int, int] = {}
    claimed: set[int] = set()
    for i, kw in enumerate(SECTIONS):
        for j, (ln, t) in enumerate(h2):
            m = re.match(r"^\s*(\d+)[.)]?\s+(.*)$", t)
            title = alnum(m.group(2) if m else t)
            if j not in claimed and (title.startswith(kw) or (i == 10 and title.startswith(LEGACY_SECTION_11))):
                pos[i] = j
                claimed.add(j)
                if not m or int(m.group(1)) != i + 1:
                    E("section-number", f"section {t!r} must be numbered {i + 1}", ln)
                break
        else:
            E("missing-section", f"section {i + 1} `{SECTIONS[i]}` is missing")
    order = [pos[i] for i in sorted(pos)]
    if order != sorted(order):
        E("section-order", "the 13 sections are not in the required order")
    for j, (ln, t) in enumerate(h2):
        if j not in claimed:
            E("extra-section", f"unexpected H2 {t!r} (the digest has exactly 13 sections)", ln)

    # tables by section ---------------------------------------------------------------------------------------
    cur = -1
    tables: dict[int, list[tuple[int, list[str]]]] = {}
    details: list[tuple[int, str]] = []
    for kind, t, info, ln in blocks:
        if kind == "heading" and info == 2:
            cur = next((i for i, kw in enumerate(SECTIONS)
                        if alnum(re.sub(r"^\s*\d+[.)]?\s+", "", t)).startswith(kw)
                        or (i == 10 and alnum(re.sub(r"^\s*\d+[.)]?\s+", "", t)).startswith(LEGACY_SECTION_11))), -1)
        elif kind == "heading" and info == 3 and cur == 2:
            m = re.match(r"^(C\d+)\b", t)
            if m:
                details.append((ln, m.group(1)))
        elif kind == "table":
            for k, row in enumerate(t.split("\n")[2:]):
                tables.setdefault(cur, []).append((ln + 2 + k, cells(row)))

    seen: dict[str, set[str]] = {}
    for sec, rows in tables.items():
        for ln, c in rows:
            m = re.fullmatch(r"([CGKPNDV])(\d+)", c[0]) if c else None
            if m:
                bucket = seen.setdefault(m.group(1), set())
                if c[0] in bucket:
                    E("dup-id", f"duplicate id {c[0]} (ids must be unique)", ln)
                bucket.add(c[0])
    concepts = {c[0] for _ln, c in tables.get(1, []) if c and re.fullmatch(r"C\d+", c[0])}
    if 1 in pos and not concepts:
        E("no-concepts", "the concept inventory has no `Cnn` rows")
    for ln, cid in details:
        if cid not in concepts:
            E("detail-unknown-concept", f"detail heading {cid} has no row in the concept inventory", ln)
    for cid in sorted(concepts - {d[1] for d in details}):
        W("detail-missing", f"concept {cid} has no `### {cid} — name` detail heading")
    for ln, c in tables.get(3, []):
        refs = re.findall(r"C\d+", c[1]) if len(c) > 1 else []
        if not refs:
            W("gap-no-concept", f"gap {c[0] if c else '?'} references no concept", ln)
        for r in refs:
            if r not in concepts:
                E("gap-unknown-concept", f"gap {c[0]} references {r}, which is not in the concept inventory", ln)

    # class visuals (section 11) ----------------------------------------------------------------------------------
    visuals = []
    if 10 in pos:
        for v in digest_lib.parse_visuals(path):
            visuals.append(v["id"])
            ln = v["line"]
            if v["frame_n"] is None:
                E("visual-frame", f"{v['id']}: frame column {v['frame']!r} needs a frame ref like `F067 (00:53:31)`", ln)
            if v["concept"] and v["concept"] not in ("-", "—") and not all(
                    cc in concepts for cc in re.findall(r"C\d+", v["concept"])):
                E("visual-concept", f"{v['id']} references {v['concept']!r}, not in the concept inventory", ln)
            if v["quality"] not in digest_lib.QUALITIES:
                E("visual-quality", f"{v['id']}: quality {v['quality']!r} must be clean|messy|partial", ln)
            if v["treatment"] not in digest_lib.TREATMENTS:
                E("visual-treatment", f"{v['id']}: treatment {v['treatment']!r} must be embed|redraw|both|text", ln)
            elif v["treatment"] != "text" and v["crop"] is None:
                E("visual-crop", f"{v['id']}: crop {v['crop_raw']!r} must be four fractions x0,y0,x1,y1 "
                  "(0 <= x0 < x1 <= 1, 0 <= y0 < y1 <= 1) unless the treatment is `text`", ln)
        if not visuals:
            W("no-visuals", "section 11 has no `Vnn` row: add every board drawing, diagram and annotated slide "
              "(or say in section 13 that the class showed none)")

    # timeline ------------------------------------------------------------------------------------------------------
    dur = duration_override if duration_override is not None else meta.get("duration_min")
    dur = float(dur) if isinstance(dur, (int, float)) else None
    spans: list[tuple[float, float]] = []
    rows = tables.get(0, [])
    if 0 in pos and not rows:
        E("timeline-empty", "the session timeline has no rows")
    for ln, c in rows:
        ts = TS_RE.findall(c[0]) if c else []
        if len(ts) < 2:
            E("timeline-bad-range", f"timeline row {c[0] if c else '?'!r} has no `hh:mm-hh:mm` range", ln)
            continue
        a, b = parse_ts(ts[0]), parse_ts(ts[1])
        if b < a:
            E("timeline-bad-range", f"timeline range {c[0]!r} ends before it starts", ln)
            continue
        spans.append((a, b))
    for _ln, c in tables.get(5, []):                 # reconstructed passages explain lost stretches
        ts = TS_RE.findall(c[0]) if c else []
        if len(ts) >= 2:
            a, b = parse_ts(ts[0]), parse_ts(ts[1])
            if b >= a:
                spans.append((a, b))
    gaps = []
    if spans and dur is not None:
        cursor = 0.0
        for a, b in sorted(spans):
            if a - cursor > GAP_MIN:
                gaps.append((cursor, a))
            cursor = max(cursor, b)
        if dur - cursor > GAP_MIN:
            gaps.append((cursor, dur))
    elif spans:
        cursor = 0.0
        for a, b in sorted(spans):
            if a - cursor > GAP_MIN and cursor > 0:
                gaps.append((cursor, a))
            cursor = max(cursor, b)
    fmt = lambda m: f"{int(m // 60):02d}:{int(round(m % 60)):02d}"  # noqa: E731
    for a, b in gaps:
        E("timeline-gap", f"timeline leaves {b - a:.0f} min unexplained ({fmt(a)}-{fmt(b)}); "
          f"add a row (break, lost audio...) or a Reconstructed-passages entry (limit {GAP_MIN:.0f} min)")
    return {"path": path.as_posix(), "errors": errors, "warnings": warnings, "concepts": sorted(concepts),
            "visuals": visuals, "timeline_rows": len(rows), "duration_min": dur, "gaps": [[a, b] for a, b in gaps]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("digest")
    ap.add_argument("--duration-min", type=float, default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    p = plat.norm(a.digest)
    if not p.is_file():
        print(f"check_digest: not found: {p}", file=sys.stderr)
        return 2
    r = check(p, a.duration_min)
    for tag, issues in (("ERROR", r["errors"]), ("warn ", r["warnings"])):
        for i in issues:
            print(f"  {tag} {i['code']:<22} " + (f"L{i['line']}: " if i.get("line") else "") + i["msg"])
    print(f"\ndigest {p.name}: {len(r['errors'])} error(s), {len(r['warnings'])} warning(s)")
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {"ok": not r["errors"], **r})
    return 1 if r["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
