"""Transcript header + cue parsing shared by prep_transcript, resolve_inputs and discover. Stdlib only."""
from __future__ import annotations

import re

RULE_RE = re.compile(r"\n=+[ \t]*\r?\n")
KV_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 _/+\-]{0,40}?)\s*:\s*(.*?)\s*$")
START_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{4})[,\s]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM|am|pm)?"
)
ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T\s]+(\d{1,2}):(\d{2}))?")


def _valid(y: int, mo: int, d: int) -> bool:
    import datetime as dt

    try:
        dt.date(y, mo, d)
        return True
    except ValueError:
        return False


def parse_start(raw: str) -> dict:
    """Date candidates for a `Start Time` string: m/d vs d/m, unambiguous when only one is valid."""
    out: dict = {"candidates": [], "date": None, "time": None, "ambiguous": None}
    m = START_RE.search(raw or "")
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mm = int(m.group(4)), int(m.group(5))
        ap = (m.group(7) or "").upper()
        if ap == "PM" and hh < 12:
            hh += 12
        elif ap == "AM" and hh == 12:
            hh = 0
        out["time"] = f"{hh:02d}:{mm:02d}"
        cands = []
        if _valid(y, a, b):
            cands.append({"format": "m/d/Y", "date": f"{y:04d}-{a:02d}-{b:02d}"})
        if _valid(y, b, a) and a != b:
            cands.append({"format": "d/m/Y", "date": f"{y:04d}-{b:02d}-{a:02d}"})
        out["candidates"] = cands
        if len(cands) == 1:
            out["date"], out["ambiguous"] = cands[0]["date"], False
        elif len(cands) > 1:
            out["ambiguous"] = True
        return out
    m = ISO_RE.search(raw or "")
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid(y, mo, d):
            out["candidates"] = [{"format": "iso", "date": f"{y:04d}-{mo:02d}-{d:02d}"}]
            out["date"], out["ambiguous"] = out["candidates"][0]["date"], False
            if m.group(4):
                out["time"] = f"{int(m.group(4)):02d}:{m.group(5)}"
    return out


def split_header(text: str) -> tuple[dict, str]:
    """(raw key/value fields, body). No `====` rule -> ({}, whole text)."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")      # CRLF, LF and lone CR alike
    m = RULE_RE.search(text)
    if not m or m.start() > 4000:
        return {}, text
    head, body = text[: m.start()], text[m.end():]
    fields: dict = {}
    for line in head.splitlines():
        kv = KV_RE.match(line)
        if kv:
            fields[kv.group(1).strip()] = kv.group(2).strip()
    if not fields:
        return {}, text
    return fields, body


def parse_header(text: str) -> tuple[dict, str]:
    """(header dict as written to header.json, body text)."""
    fields, body = split_header(text)
    low = {k.lower(): v for k, v in fields.items()}
    dur = None
    if "duration" in low:
        m = re.search(r"(\d+(?:\.\d+)?)", low["duration"])
        if m:
            dur = int(round(float(m.group(1))))
    start_raw = low.get("start time") or low.get("start") or low.get("date") or ""
    start = parse_start(start_raw) if start_raw else {"candidates": [], "date": None, "time": None, "ambiguous": None}
    safe = {k: ("<redacted>" if "@" in v else v) for k, v in fields.items()}
    hdr = {
        "has_header": bool(fields),
        "course": low.get("course name") or low.get("course"),
        "lecture_title": low.get("lecture title") or low.get("title"),
        "start_raw": start_raw or None,
        "start_candidates": start["candidates"],
        "date": start["date"],
        "date_ambiguous": start["ambiguous"],
        "start_time": start["time"],
        "duration_min": dur,
        "generator": low.get("downloaded via") or low.get("generator"),
        "model": low.get("model use") or low.get("model"),
        "fields": safe,
    }
    return hdr, body


CUE_TIME_RE = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0


def is_cue_file(text: str, suffix: str = "") -> bool:
    """True for WebVTT / SubRip content (by header, suffix, or the first cue timestamp)."""
    t = text.lstrip("﻿")
    return (t.startswith("WEBVTT") or suffix.lower() in (".vtt", ".srt")) and bool(CUE_TIME_RE.search(t[:200000]))


def parse_cues(text: str) -> list[dict]:
    """[{start, end, text}] from WebVTT or SubRip text (tags stripped, repeated rolling-caption lines merged)."""
    cues: list[dict] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.lstrip("\ufeff")):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            m = CUE_TIME_RE.search(ln)
            if not m:
                continue
            g = m.groups()
            body = " ".join(TAG_RE.sub("", x).strip() for x in lines[i + 1:]).strip()
            body = re.sub(r"\s+", " ", body)
            if body:
                cues.append({"start": _secs(*g[0:4]), "end": _secs(*g[4:8]), "text": body})
            break
    out: list[dict] = []
    for c in cues:  # auto-captions repeat the previous line as a prefix
        if out and c["text"] == out[-1]["text"]:
            out[-1]["end"] = max(out[-1]["end"], c["end"])
        elif out and c["text"].startswith(out[-1]["text"]) and c["start"] <= out[-1]["end"] + 0.05:
            out[-1].update(text=c["text"], end=c["end"])
        else:
            out.append(dict(c))
    return out


def cues_to_text(cues: list[dict], gap: float = 2.5) -> tuple[str, list[dict]]:
    """(plain text, timings). A new line starts after a pause > ``gap`` seconds.

    timings: [{start, end, char_start}] with offsets into the returned text.
    """
    parts: list[str] = []
    timings: list[dict] = []
    pos = 0
    prev_end = None
    for c in cues:
        sep = ""
        if parts:
            sep = "\n" if (prev_end is not None and c["start"] - prev_end > gap) else " "
        parts.append(sep + c["text"])
        pos += len(sep)
        timings.append({"start": round(c["start"], 3), "end": round(c["end"], 3), "char_start": pos})
        pos += len(c["text"])
        prev_end = c["end"]
    return "".join(parts), timings
