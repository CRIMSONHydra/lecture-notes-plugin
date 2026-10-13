# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Prepare a Scaler (or .vtt/.srt) transcript for reading.

    uv run prep_transcript.py <transcript.txt> (--out DIR | --session N [--project DIR]) [--width 180] [--json OUT]

--session N writes to <project>/.notes-plugin-internal/work/sessions/NN/transcript/.

.vtt / .srt transcripts are converted to plain text (cue tags stripped, a new line after a pause) plus
timings.json: [{start, end, char_start}] with offsets into the body.

Writes in DIR:
  header.json            parsed `Key: Value` header (title, date candidates, duration, model ...)
  transcript.wrapped.txt body (header removed) wrapped to <= --width columns (replaces `fold`)
  junk_runs.json         spans of ASR junk ("you you you ...", "Thank you." x3, non-English markers)

Offsets in junk_runs.json are character offsets into the BODY (text after the `=====` rule,
leading whitespace stripped); `line_start`/`line_end` are 1-based lines in transcript.wrapped.txt.
"""
from __future__ import annotations

import argparse
import bisect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import guard, layout, platform as plat  # noqa: E402

from notes_lib.transcript import (  # noqa: E402,F401
    ISO_RE, KV_RE, RULE_RE, START_RE, cues_to_text, is_cue_file, parse_cues, parse_header, parse_start, split_header)


MARKERS = [
    "E aí", "Legenda", "Legendas", "Amara.org", "Sous-titr", "Untertitel",
    "Subtitles by", "Obrigado por assistir", "ご視聴", "字幕",
]
SCRIPT_RE = re.compile(
    r"[Ѐ-ӿ؀-ۿ぀-ヿ㐀-鿿가-힯฀-๿]{3,}"
)
REPEAT_MIN = 5
THANKS_MIN = 3
MERGE_GAP = 25



def wrap_with_offsets(body: str, width: int) -> tuple[list[str], list[int]]:
    """Greedy word wrap that keeps hard newlines. Returns (lines, char offset of each line start)."""
    lines: list[str] = []
    starts: list[int] = []
    pos = 0
    for src in body.split("\n"):
        cur: list[str] = []
        cur_len = 0
        cur_start = None
        for m in re.finditer(r"\S+", src):
            tok = m.group(0)
            while len(tok) > width:  # pathological token: hard split
                if cur:
                    lines.append(" ".join(cur))
                    starts.append(cur_start)  # type: ignore[arg-type]
                    cur, cur_len, cur_start = [], 0, None
                lines.append(tok[:width])
                starts.append(pos + m.start())
                tok = tok[width:]
            if not tok:
                continue
            add = len(tok) + (1 if cur else 0)
            if cur and cur_len + add > width:
                lines.append(" ".join(cur))
                starts.append(cur_start)  # type: ignore[arg-type]
                cur, cur_len, cur_start = [], 0, None
                add = len(tok)
            if not cur:
                cur_start = pos + m.start()
            cur.append(tok)
            cur_len += add
        if cur:
            lines.append(" ".join(cur))
            starts.append(cur_start)  # type: ignore[arg-type]
        elif not src.strip():
            lines.append("")
            starts.append(pos)
        pos += len(src) + 1
    return lines, starts


def _norm_tok(t: str) -> str:
    return re.sub(r"[^\w']", "", t).lower()


def find_junk(body: str) -> list[dict]:
    """Raw junk spans (start, end, kind, detail), merged when close together."""
    spans: list[tuple[int, int, str, str]] = []

    toks = [(m.start(), m.end(), _norm_tok(m.group(0))) for m in re.finditer(r"\S+", body)]
    i = 0
    while i < len(toks):
        if not toks[i][2]:
            i += 1
            continue
        j = i
        while j + 1 < len(toks) and toks[j + 1][2] == toks[i][2]:
            j += 1
        run = j - i + 1
        if run >= REPEAT_MIN:
            spans.append((toks[i][0], toks[j][1], "repeat-token", f"{toks[i][2]!r} x{run}"))
        i = j + 1

    for m in re.finditer(r"(?:\bthank you[.!]?\s*){%d,}" % THANKS_MIN, body, flags=re.I):
        n = len(re.findall(r"thank you", m.group(0), flags=re.I))
        spans.append((m.start(), m.start() + len(m.group(0).rstrip()), "thank-you", f"'Thank you.' x{n}"))

    for mk in MARKERS:
        for m in re.finditer(re.escape(mk), body):
            spans.append((m.start(), m.end(), "marker", mk))
    for m in SCRIPT_RE.finditer(body):
        spans.append((m.start(), m.end(), "non-latin-script", m.group(0)[:20]))

    spans.sort()
    merged: list[list] = []
    for s, e, kind, detail in spans:
        if merged and s <= merged[-1][1] + MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], e)
            if kind not in merged[-1][2]:
                merged[-1][2].append(kind)
            merged[-1][3].append(detail)
        else:
            merged.append([s, e, [kind], [detail]])
    return [{"start": s, "end": e, "kinds": k, "details": d} for s, e, k, d in merged]


def _hms(minutes: float) -> str:
    total = int(round(minutes * 60))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def build_junk_report(body: str, duration_min: float | None, line_starts: list[int]) -> dict:
    n = max(1, len(body))
    out = []
    for sp in find_junk(body):
        s, e = sp["start"], sp["end"]
        ls = bisect.bisect_right(line_starts, s)
        le = bisect.bisect_right(line_starts, max(s, e - 1))
        row = {
            "kinds": sp["kinds"],
            "start": s,
            "end": e,
            "chars": e - s,
            "line_start": max(1, ls),
            "line_end": max(1, le),
            "approx_min": None,
            "approx_hms": None,
            "text": re.sub(r"\s+", " ", body[s:e])[:100],
            "details": sp["details"][:6],
        }
        if duration_min:
            t = s / n * duration_min
            row["approx_min"] = round(t, 1)
            row["approx_hms"] = _hms(t)
        out.append(row)
    junk_chars = sum(r["chars"] for r in out)
    return {
        "body_chars": len(body),
        "duration_min": duration_min,
        "count": len(out),
        "junk_chars": junk_chars,
        "junk_share": round(junk_chars / n, 4),
        "approx_junk_min": round(junk_chars / n * duration_min, 1) if duration_min else None,
        "note": "approx_min = start/body_chars*duration_min (speech is not uniform; treat as +-5 min)",
        "spans": out,
    }


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript", help="transcript .txt")
    ap.add_argument("--out", help="output directory (default: <session>/transcript)")
    ap.add_argument("--session", help="session number (used with --project for the default --out)")
    ap.add_argument("--project", help="project dir (default: discovered from cwd)")
    ap.add_argument("--width", type=int, default=180)
    ap.add_argument("--json", metavar="OUT", help="also write the combined report here")
    ap.add_argument("--data-dir")
    a = ap.parse_args(argv)

    src = Path(a.transcript)
    if not src.is_file():
        print(f"error: transcript not found: {src}", file=sys.stderr)
        return 2
    try:
        out = layout.resolve_out(a.out, a.project, a.session, "transcript")
    except layout.ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ddir = plat.resolve_data_dir(a.data_dir)
    try:
        guard.assert_writable(out / "header.json", ddir)
    except guard.GuardError as e:
        print(e, file=sys.stderr)
        return 2

    text = src.read_text(encoding="utf-8-sig", errors="replace")
    timings = None
    if src.suffix.lower() in (".vtt", ".srt") or is_cue_file(text, src.suffix):
        cues = parse_cues(text)
        body, timings = cues_to_text(cues)
        hdr, _ = parse_header("")
        hdr["duration_min"] = int(round(cues[-1]["end"] / 60)) if cues else None
        hdr["format"] = src.suffix.lower().lstrip(".") or "vtt"
        hdr["cues"] = len(cues)
    else:
        hdr, body = parse_header(text)
    body = body.strip()
    hdr["source"] = src.as_posix()
    hdr["source_bytes"] = src.stat().st_size

    lines, starts = wrap_with_offsets(body, max(40, a.width))
    junk = build_junk_report(body, hdr["duration_min"], starts)
    junk["source"] = src.as_posix()

    out.mkdir(parents=True, exist_ok=True)
    plat.write_json(out / "header.json", hdr)
    (out / "transcript.wrapped.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    plat.write_json(out / "junk_runs.json", junk)
    if timings is not None:
        plat.write_json(out / "timings.json", timings)
    if a.json:
        guard.assert_writable(a.json, ddir)
        plat.write_json(a.json, {"header": hdr, "junk": junk, "lines": len(lines)})

    print(f"transcript : {src.name}  ({hdr['source_bytes']:,} bytes, {len(body):,} body chars)")
    print(f"title      : {hdr['lecture_title']!r}   duration: {hdr['duration_min']} min   model: {hdr['model']}")
    amb = {True: " (ambiguous m/d vs d/m)", False: " (unambiguous)", None: ""}[hdr["date_ambiguous"]]
    print(f"start      : {hdr['start_raw']!r} -> date {hdr['date']}{amb}")
    print(f"wrapped    : {len(lines)} lines, max {max((len(x) for x in lines), default=0)} cols -> {out / 'transcript.wrapped.txt'}")
    print(f"junk runs  : {junk['count']} spans, ~{junk['approx_junk_min']} min of audio -> {out / 'junk_runs.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
