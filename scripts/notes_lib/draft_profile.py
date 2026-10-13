"""Deterministic skeleton of a profile for a subject that has none (PLAN 16.4). Stdlib only (PyMuPDF optional).

``extract_facts`` gathers what can be read without judgement: the syllabus text (course title hints, institution
line, assessment percentages, a session map), every transcript header (course name, lecture title, start date
candidates, duration), the discovery result (file kinds, which sessions the files fall into) and the code
languages seen (by extension). ``skeleton`` turns those facts into profile text from ``profiles/_template.md``.
The agent then reviews both: the facts JSON lists every assumption in ``assumptions[]``.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from . import discover, transcript as tr

SESSION_ROW_RE = re.compile(
    r"^\s*(?:(?:session|class|lecture|lec|day|week)\s*)?(\d{1,2})\s*[—–:.\-)]\s*(\S.*?)\s*$", re.I)
CLASS_TITLE_RE = re.compile(r"^\s*(?:session|class|lecture|lec)\s*(\d{1,2})\s*[—–:.\-]+\s*(\S.*?)\s*$", re.I)
BARE_NUM_RE = re.compile(r"^\s*(\d{1,2})\s*$")
PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
ASSESS_WORDS = ("assignment", "capstone", "project", "quiz", "exam", "end-term", "end term", "evaluation", "viva",
                "midterm", "mid-term", "presentation", "homework")
LANG_BY_EXT = {".py": "python", ".ipynb": "python", ".sql": "sql", ".js": "javascript", ".ts": "typescript",
               ".java": "java", ".cpp": "cpp", ".cc": "cpp", ".c": "c", ".h": "c", ".go": "go", ".rs": "rust",
               ".sh": "bash", ".cs": "csharp", ".kt": "kotlin"}
SNIPPET_TMPL = {
    "python": '  python: {ext: .py, compile: null, run: "uv run {src}"}',
    "javascript": '  javascript: {ext: .js, compile: null, run: "node {src}"}',
    "bash": '  bash: {ext: .sh, compile: null, run: "bash {src}"}',
    "cpp": '  cpp: {ext: .cpp, compile: "g++ -std=c++20 -O2 {src} -o {exe}", run: "{exe}"}',
}


def read_text_any(path: Path, limit: int = 200_000) -> str:
    """Text of a syllabus: .txt/.md as is, .pdf via PyMuPDF (when importable), .docx via stdlib. '' on failure."""
    ext = Path(path).suffix.lower()
    try:
        if ext in (".txt", ".md"):
            return Path(path).read_text(encoding="utf-8-sig", errors="replace")[:limit]
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            paras = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
            return "\n".join("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras)[:limit]
        if ext == ".pdf":
            try:
                import pymupdf as fitz  # type: ignore
            except ImportError:
                import fitz  # type: ignore
            with fitz.open(path) as doc:
                return "\n".join(pg.get_text() for pg in doc)[:limit]
    except Exception:  # noqa: BLE001
        return ""
    return ""


def syllabus_facts(text: str, filename: str) -> dict:
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    rows: dict[int, str] = {}
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        m = CLASS_TITLE_RE.match(ln)
        if m:
            n, title = int(m.group(1)), m.group(2)
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            # a title wrapped onto the next line (PDF cell wrap): short, not a bullet / number / date / row
            wrap = (nxt and not re.match(r"^([•\-*]|\d|assignment|capstone|credits|for )", nxt, re.I)
                    and not CLASS_TITLE_RE.match(nxt) and len(nxt) < 45 and not nxt.endswith(".")
                    and (nxt[:1].islower() or (len(ln) >= 36 and len(nxt) <= 30 and not nxt.startswith("(")
                                               and len(nxt.split()) <= 4)))
            if wrap:
                title = f"{title} {nxt}"
                i += 1
            rows.setdefault(n, re.sub(r"\s+", " ", title).strip())
        i += 1
    assess = []
    for ln in lines:
        if PCT_RE.search(ln) and len(ln) < 160 and any(w in ln.lower() for w in ASSESS_WORDS):
            assess.append(ln.strip())
    pcts: list[dict] = []
    for k, ln in enumerate(lines):          # table layouts: the component word and the % sit on nearby lines
        low = ln.strip().lower()
        if low in ("assignment", "capstone", "project", "quiz", "exam", "end-term", "evaluation"):
            for nb in lines[k + 1:k + 8]:
                m = PCT_RE.search(nb)
                if m and len(nb.strip()) <= 6:
                    pcts.append({"component": ln.strip(), "weight": m.group(0)})
                    break
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename)
    parts = [x.strip() for x in re.split(r"\s+-\s+", stem) if x.strip()]
    inst = next((p for p in parts if re.search(r"batch|term|sem|year", p, re.I)), None)
    if inst:
        inst = re.sub(r"\s*syllabus", "", inst, flags=re.I).strip(" -")
    title_hint = next((p for p in reversed(parts) if not re.search(r"syllabus|batch", p, re.I)), None)
    return {"file": filename, "session_rows": [{"n": n, "title": t} for n, t in sorted(rows.items())],
            "assessment_lines": assess[:12], "assessment_weights": pcts[:12],
            "institution_hint": inst, "title_hint": title_hint, "chars": len(text)}


def transcript_facts(disc) -> list[dict]:
    out = []
    for it in disc.items:
        if it.kind != "transcript":
            continue
        try:
            head = it.path.read_text(encoding="utf-8-sig", errors="replace")[:6000]
            hdr, _ = tr.parse_header(head + "\n") if tr.RULE_RE.search(head) else ({"has_header": False}, "")
        except OSError:
            hdr = {"has_header": False}
        out.append({"file": it.rel, "course": hdr.get("course"), "title": hdr.get("lecture_title") or it.title,
                    "date": hdr.get("date"), "date_candidates": [c["date"] for c in hdr.get("start_candidates") or []],
                    "duration_min": hdr.get("duration_min"), "assigned_sessions": list(it.ns)})
    return out


def code_languages(disc) -> dict[str, int]:
    langs: dict[str, int] = {}
    for it in disc.items:
        if it.kind in ("code", "exercise", "data") or it.path.suffix.lower() in (".ipynb", ".sql"):
            lang = LANG_BY_EXT.get(it.path.suffix.lower())
            if lang:
                langs[lang] = langs.get(lang, 0) + 1
    return dict(sorted(langs.items(), key=lambda kv: (-kv[1], kv[0])))


def slug_id(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:24] or "course"


def facts_for(resources: Path, syllabus: Path | None, profile: dict | None = None) -> tuple[dict, "discover.Discovery"]:
    """(facts dict, discovery). ``profile`` supplies resources_dir/paths pins for discovery (a minimal dict works)."""
    prof = dict(profile or {})
    prof.setdefault("paths", {})
    prof.setdefault("sessions", [])
    sy_rows: list[dict] = []
    sy = None
    if syllabus:
        sy = syllabus_facts(read_text_any(syllabus), Path(syllabus).name)
        sy_rows = [{"n": r["n"], "title": r["title"]} for r in sy["session_rows"]]
        if not prof["sessions"]:
            prof["sessions"] = sy_rows
    disc = discover.discover(prof, resources)
    tfs = transcript_facts(disc)
    courses = sorted({t["course"] for t in tfs if t.get("course")})
    langs = code_languages(disc)
    kinds: dict[str, int] = {}
    for it in disc.items:
        kinds[it.kind] = kinds.get(it.kind, 0) + 1
    assumptions = []
    if not sy:
        assumptions.append("no syllabus given: the session table comes from transcript headers and file names")
    elif not sy["session_rows"]:
        assumptions.append("the syllabus text had no recognisable `Class N - Title` rows: sessions come from transcripts")
    if len(courses) > 1:
        assumptions.append(f"transcript headers name several courses: {courses}")
    if not courses:
        assumptions.append("no transcript header names the course: the title is a guess from the syllabus file name")
    amb = [t["file"] for t in tfs if len(t["date_candidates"]) > 1]
    if amb:
        assumptions.append(f"{len(amb)} transcript start date(s) are ambiguous (m/d vs d/m): dates are left to the "
                           "profile `date:` fields; confirm them")
    if disc.unassigned():
        assumptions.append(f"{len(disc.unassigned())} file(s) could not be assigned to a session")
    title = (courses[0] if courses else (sy or {}).get("title_hint")) or "Course"
    facts = {
        "schema": 1, "resources": resources.as_posix(), "syllabus": sy,
        "course_title_candidates": [c for c in [*courses, (sy or {}).get("title_hint")] if c],
        "title": title, "institution_hint": (sy or {}).get("institution_hint"),
        "transcripts": tfs, "file_kinds": kinds, "code_languages": langs,
        "sessions": disc.session_map() and [{"n": r["n"], "title": r["title"], "derived": r["derived"],
                                              "files": [f"{f['kind']}:{Path(f['path']).name}" for f in r["files"]]}
                                             for r in disc.session_map()],
        "course_wide": disc.course_docs(), "unassigned": disc.unassigned(), "assumptions": assumptions,
        "tier_rules": {"snippets": "compile/run steps only for detected code languages: "
                                   + (", ".join(langs) if langs else "none (non-programming subject)"),
                       "figures": "class+generated",
                       "research_sources": "agent: choose primary sources, vendor docs and case studies "
                                           "that fit the subject; none are guessed here"},
    }
    return facts, disc


def _set_line(text: str, key: str, value: str) -> str:
    return re.sub(rf"(?m)^{key}:\s*.*$", lambda _m: f"{key}: {value}", text, count=1)


def skeleton(template_text: str, facts: dict, disc, new_id: str | None, resources_dir: str | None,
             syllabus_name: str | None) -> str:
    """Profile text: the blank template with the facts written in. Existing template comments stay."""
    t = template_text
    title = facts["title"]
    words = re.findall(r"[A-Za-z0-9]+", title)
    prefix = ("".join(w[0] for w in words if w[0].isalpha())[:4] or "CRS").upper()
    pid = new_id or slug_id(title)
    t = _set_line(t, "id", pid)
    t = _set_line(t, "title", json.dumps(title, ensure_ascii=False))
    t = _set_line(t, "short", json.dumps(f"{prefix} · {title}"[:48], ensure_ascii=False))
    t = _set_line(t, "prefix", prefix)
    if facts.get("institution_hint"):
        t = _set_line(t, "institution", json.dumps(facts["institution_hint"], ensure_ascii=False))
    if resources_dir:
        t = re.sub(r"(?m)^#?\s*resources_dir:.*$", f"resources_dir: {json.dumps(resources_dir)}", t, count=1)
    # paths: pin the syllabus only; every other default stays
    pstart = re.search(r"(?m)^paths:.*$", t)
    if pstart:
        rest = t[pstart.end():]
        m = re.search(r"(?m)^(?=[A-Za-z#])", rest)
        end = pstart.end() + (m.start() if m else len(rest))
        block = "paths:\n" + (f"  syllabus: {json.dumps(syllabus_name)}\n" if syllabus_name else "  {}\n")
        t = t[:pstart.start()] + block + t[end:]
    # sessions: the discovered table (syllabus rows matched to transcripts, plus derived ones)
    t = discover.clear_sessions(t)
    rows = []
    for r in disc.sessions:
        row = {"n": int(r["n"]), "title": r.get("title") or f"Session {r['n']}"}
        mapped = next((x for x in disc.session_map() if x["n"] == row["n"]), {})
        tr_file = next((f for f in mapped.get("files", []) if f["kind"] == "transcript"), None)
        if tr_file:
            row["stem"] = Path(tr_file["path"]).stem
        d = r.get("date")
        if d:
            row["date"] = d
        rows.append(row)
    t = discover.append_sessions_to_profile(t, rows)
    # snippets: only for code languages that exist
    langs = [lg for lg in facts["code_languages"] if lg in SNIPPET_TMPL]
    sm = re.search(r"(?m)^snippets:.*$", t)
    if sm:
        rest = t[sm.end():]
        m = re.search(r"(?m)^(?=[A-Za-z])", rest)
        end = sm.end() + (m.start() if m else len(rest))
        body = "snippets:\n" + "".join(SNIPPET_TMPL[lg] + "\n" for lg in langs) if langs else "snippets: {}\n"
        t = t[:sm.start()] + body + t[end:]
    t = re.sub(r"(?m)^(\s*)preferred_sources:.*$", r"\1preferred_sources: []   # agent: primary sources for this subject", t, count=1)
    return t
