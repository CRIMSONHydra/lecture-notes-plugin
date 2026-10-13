"""Work with whatever resources exist (PLAN section 11): inventory, classification, session assignment.

Stdlib only. Typical use::

    d = discover.discover(profile)            # profile from notes_lib.profile.load_profile (or a minimal dict)
    d.for_session(3)                          # items assigned to session 3
    d.course_docs()                           # syllabus + course-wide handouts
    d.unassigned()                            # never guessed: listed with their top-2 candidates
    d.sessions                                # profile table + derived sessions (rows carry ``derived: True``)

Scoring (per file x session, 0..1; the best component wins):

    1.00  exact stem match, or a profile ``paths.*`` pin with placeholders
    0.90 * sim   transcript header title ~ session title (token similarity >= 0.8)
    0.85  header date == session date (0.75 for a date in the file name)
    0.80  explicit session number (``L3``, ``Lec-3``, ``lecture 3``, ``session_03``, ``S03``, ``class 3``,
          ``day 3``, ``week 3``, leading ``03-``) -- suppressed for other sessions when a title match is strong
    0.70 * sim   file-name tokens ~ session title (sim >= 0.5)
    0.60 * sim   first-page text ~ session title (sim >= 0.8, only when a probe is supplied)

Multi-session documents (PLAN 16.4): ``Session_2_3``, ``Class 2-3``, ``L2&3``, ``sessions 4,5`` and ranges
(``2-4`` = 2, 3, 4) name several sessions; the file is assigned to *each* (``Item.ns``). Dates are masked first,
so a date is never read as a range. Numbers in parent folder names (``PlacementOps_Class11_Student_Pack/``)
count for the files inside when the file name has none.

Kinds added in 16.4: ``data`` (csv/json/xlsx/parquet/sql/...), ``exercise`` (notebooks, ``*_Solution.pdf``,
``*_Student.*``, files in a student/starter pack) and ``reference`` (``links.md`` and other non-transcript
``.md``/``.txt``). These three are assigned by explicit class number only; with none they are course-wide.

Assigned when best >= ASSIGN_MIN (0.5; handouts need HANDOUT_MIN 0.75, otherwise course-wide) and
best - runner_up >= MARGIN (0.15). Otherwise unassigned, with candidates. Files sharing a stem follow the
best-scoring one.
"""
from __future__ import annotations

import difflib
import fnmatch
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import transcript as tr

ASSIGN_MIN = 0.5
HANDOUT_MIN = 0.75
MARGIN = 0.15
DERIVE_BELOW = 0.3      # a speech file whose best score is under this matches "none" of the sessions

VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".ts", ".m4v"}
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".ogg", ".flac"}
CODE_EXT = {".cpp", ".cc", ".c", ".h", ".hpp", ".py", ".java", ".ipynb", ".js", ".rs", ".go", ".cs", ".kt",
            ".sql", ".sh", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".webp", ".gif"}
DOC_EXT = {".pdf", ".docx", ".pptx", ".md", ".txt", ".key", ".doc", ".ppt", ".odt", ".odp", ".rtf"}
IGNORE_NAMES = {"readme.md", "thumbs.db", "desktop.ini", ".ds_store"}
SYLLABUS_RE = re.compile(r"syllabus|curriculum|course[ _-]?(?:outline|plan|structure)|schedule", re.I)
SLIDE_HINT = {"slides", "slide", "deck", "presentation", "lecture", "lec", "session", "class", "day", "week"}
STOP = {"class", "scaler", "academy", "lecture", "lec", "session", "the", "and", "of", "a", "an", "to", "in",
        "part", "slides", "slide", "deck", "pdf", "v", "copy", "final", "new", "recording", "video",
        "transcript", "whisper", "for", "on", "with", "notes", "day", "week", "s", "l"}
_KW = r"(?:lectures?|lec|sessions?|classes|class|days?|weeks?|[ls])"
_SEP = r"(?:\s*(?:[-_,&+/\u2013\u2014]|and|to)\s*|\s+)"
# keyword + one number, then any "sep number" continuations: Class 2-3, Session_2_3, L2&3, sessions 4,5
NUMLIST_RE = re.compile(r"(?<![a-z0-9])" + _KW + r"[-_. ]?0*(\d{1,2})((?:" + _SEP + r"0*\d{1,2}(?![\d]))*)(?!\d)", re.I)
LEAD_RE = re.compile(r"^0*(\d{1,2})(?!\d)[-_. )]")
NUM_PATTERNS = [NUMLIST_RE, LEAD_RE]      # kept for strip_session_refs
DATE_RE = re.compile(r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")
DATE_MASKS = [re.compile(r"(?<!\d)\d{4}[-_.]\d{1,2}[-_.]\d{1,2}(?!\d)"),
              re.compile(r"(?<!\d)\d{1,2}[-_.]\d{1,2}[-_.](?:\d{4}|\d{2})(?!\d)"),
              re.compile(r"(?<!\d)\d{8}(?!\d)")]
EXERCISE_TOKENS = {"solution", "solutions", "student", "exercise", "exercises", "assignment", "homework",
                   "worksheet", "worksheets", "practice", "starter", "lab", "labs", "handson"}
BUNDLE_TOKENS = EXERCISE_TOKENS | {"pack", "kit"}
DATA_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".json", ".jsonl", ".ndjson", ".sql", ".db",
            ".sqlite", ".avro"}
REF_EXT = {".md", ".txt", ".pdf", ".docx", ".pptx", ".rtf", ".odt"}
EXTRA_KINDS = ("data", "exercise", "reference")


# ------------------------------------------------------------------ text helpers
def tokens(s: str) -> list[str]:
    """Lower-case alphanumeric tokens (letters and digits split), noise words removed."""
    s = re.sub(r"\(\d+\)", " ", s.lower())
    s = re.sub(r"\.whisper$", "", s)
    out = re.findall(r"[a-z]+|\d+", s)
    return [t for t in out if t not in STOP and not re.fullmatch(r"v\d+", t)]


def _sim_tokens(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = set(a), set(b)
    jac = len(sa & sb) / len(sa | sb)
    ratio = difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    sim = max(jac, ratio)
    na, nb = {t for t in a if t.isdigit()}, {t for t in b if t.isdigit()}
    if na != nb:
        sim *= 0.85
    return min(sim, 0.95)


def similarity(a: str, b: str) -> float:
    """Normalised token similarity of two titles/names, 0..1 (1.0 only for identical token lists)."""
    return _sim_tokens(tokens(a), tokens(b))


def mask_dates(name: str) -> str:
    """``name`` with date-looking runs blanked, so a date is never read as a session number or range."""
    for rx in DATE_MASKS:
        name = rx.sub(lambda m: " " * len(m.group(0)), name)
    return name


def strip_session_refs(name: str) -> str:
    """``name`` without keyword session numbers (``Lecture 3``, ``L3``, ``Class 2-3``, leading ``03-``)."""
    name = mask_dates(name)
    for rx in NUM_PATTERNS:
        name = rx.sub(" ", name)
    return name


def stem_key(stem: str) -> str:
    s = re.sub(r"\s*\(\d+\)\s*$", "", stem.strip())
    return re.sub(r"\.whisper$", "", s).casefold()


def _expand(first: int, rest: str) -> list[int]:
    """Numbers of ``first`` + continuations: a hyphen/dash/``to`` between two numbers is a range, any other
    separator a list item (``2-4`` = 2,3,4; ``2_4`` and ``2,4`` = 2,4)."""
    out = [first]
    prev = first
    for m in re.finditer(r"(\s*(?:[-\u2013\u2014]|to)\s*|\s*(?:[_,&+/]|and)\s*|\s+)0*(\d{1,2})", rest, re.I):
        sep, n = m.group(1), int(m.group(2))
        if re.fullmatch(r"\s*(?:[-\u2013\u2014]|to)\s*", sep, re.I) and prev < n <= prev + 20:
            out.extend(range(prev + 1, n + 1))
        else:
            out.append(n)
        prev = n
    return out


def keyword_numbers(name: str) -> list[int]:
    """Numbers introduced by a keyword (``Class 2-3``, ``L2&3``, ``Session_2_3``); dates are masked first."""
    name = mask_dates(name)
    nums: list[int] = []
    for m in NUMLIST_RE.finditer(name):
        for n in _expand(int(m.group(1)), m.group(2) or ""):
            if n not in nums:
                nums.append(n)
    return nums


def leading_numbers(name: str) -> list[int]:
    return [int(m.group(1)) for m in LEAD_RE.finditer(mask_dates(name))]


def explicit_numbers(name: str) -> list[int]:
    """Session numbers named in ``name`` (see the module docstring for the multi-session forms)."""
    nums = keyword_numbers(name)
    return nums + [n for n in leading_numbers(name) if n not in nums]


def name_dates(name: str) -> list[str]:
    import datetime as dt
    out = []
    for m in DATE_RE.finditer(name):
        y, mo, d = (int(x) for x in m.groups())
        try:
            dt.date(y, mo, d)
        except ValueError:
            continue
        out.append(f"{y:04d}-{mo:02d}-{d:02d}")
    return out


def pretty_title(stem: str) -> str:
    s = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    s = re.sub(r"[-_ ]*Class[-_ ]Scaler[-_ ]Academy.*$", "", s, flags=re.I)
    s = re.sub(r"\.whisper$", "", s)
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"\s*-\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip() or stem


# ------------------------------------------------------------------ probes (first page text)
def default_probe(path: Path) -> str:
    """First-page text of a .docx/.pptx (stdlib) or .pdf (only when PyMuPDF is importable). '' when unknown."""
    ext = path.suffix.lower()
    try:
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            paras = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
            lines = ["".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras[:30]]
            return "\n".join(x for x in lines if x.strip())
        if ext == ".pptx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("ppt/slides/slide1.xml").decode("utf-8", "replace")
            return "\n".join(re.findall(r"<a:t>([^<]*)</a:t>", xml))
        if ext == ".pdf":
            try:
                import pymupdf as fitz  # type: ignore
            except ImportError:
                try:
                    import fitz  # type: ignore
                except ImportError:
                    return ""
            with fitz.open(path) as doc:
                return doc[0].get_text() if len(doc) else ""
    except Exception:  # noqa: BLE001 - a probe must never break discovery
        return ""
    return ""


# ------------------------------------------------------------------ items
@dataclass
class Item:
    path: Path
    rel: str
    kind: str
    stem: str
    size: int = 0
    title: str | None = None          # transcript header title
    dates: list[str] = field(default_factory=list)
    numbers: list[int] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    fmt: str | None = None            # transcript format: txt | vtt | srt ; recording: video | audio
    pins: dict = field(default_factory=dict)     # n -> reason (profile patterns)
    course_pin: str | None = None
    warn: str | None = None
    duration_min: int | None = None
    # result
    scope: str = "unassigned"         # session | course | unassigned | ignored
    n: int | None = None              # first assigned session
    ns: list[int] = field(default_factory=list)   # every assigned session (multi-session documents)
    score: float = 0.0
    reason: str = ""
    candidates: list = field(default_factory=list)

    def to_json(self) -> dict:
        d = {"path": self.path.as_posix(), "rel": self.rel, "kind": self.kind, "scope": self.scope,
             "n": self.n, "sessions": list(self.ns), "score": round(self.score, 2), "reason": self.reason}
        if self.candidates:
            d["candidates"] = self.candidates
        if self.warn:
            d["warn"] = self.warn
        if self.fmt:
            d["format"] = self.fmt
        return d


def _read_head(p: Path, n: int = 6000) -> str:
    try:
        with open(p, "rb") as f:
            s = f.read(n).decode("utf-8", "replace")
        return s.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")       # CRLF / lone CR / BOM-proof
    except OSError:
        return ""


def _is_frames_dir(d: Path) -> bool:
    return (d / "index.md").is_file() and ((d / "frames.json").is_file() or any(d.glob("*.jpg")))


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower()))


def _classify_file(p: Path, rel: str, profile: dict, syl_pins: list[str]) -> tuple[str, dict]:
    """(kind, extras) for one file."""
    name, ext, low = p.name, p.suffix.lower(), p.name.lower()
    ex: dict = {}
    parents = rel.split("/")[:-1]
    in_bundle = any(_words(d) & BUNDLE_TOKENS for d in parents)
    if low.endswith(".whisper.json"):
        return "whisper", ex
    if ext in AUDIO_EXT:
        ex["fmt"] = "audio"
        return "recording", ex
    if ext in VIDEO_EXT:
        in_videos = "videos" in [x.lower() for x in rel.split("/")[:-1]]
        if ext == ".ts" and not (in_videos or p.stat().st_size >= 1 << 20):
            return "code", ex
        ex["fmt"] = "video"
        return "recording", ex
    if ext in (".vtt", ".srt"):
        ex["fmt"] = ext[1:]
        return "transcript", ex
    syl_hit = any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g) for g in syl_pins) or bool(SYLLABUS_RE.search(p.stem))
    if in_bundle and not syl_hit:                       # a student/starter pack: data stays data, docs are reference
        if ext in DATA_EXT:
            return "data", ex
        if ext in REF_EXT:
            return "reference", ex
        if ext not in IMAGE_EXT:
            return "exercise", ex
    if ext == ".ipynb":
        return "exercise", ex
    if ext in DATA_EXT and not syl_hit:
        return "data", ex
    if ext in (".txt", ".md"):
        head = _read_head(p)
        hdr, _ = tr.parse_header(head + "\n") if tr.RULE_RE.search(head) else ({"has_header": False}, "")
        if hdr.get("has_header") and hdr.get("lecture_title"):
            ex.update(fmt=ext[1:] if ext == ".md" else "txt", title=hdr["lecture_title"], hdr=hdr)
            return "transcript", ex
    if ext in DOC_EXT or ext in (".xlsx", ".csv"):
        if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(name, g) for g in syl_pins) or SYLLABUS_RE.search(p.stem):
            return "syllabus", ex
    if ext in DOC_EXT and (_words(p.stem) & EXERCISE_TOKENS):
        return "exercise", ex
    if ext == ".txt":
        return ("transcript", {**ex, "fmt": "txt"}) if p.stat().st_size >= 2048 else ("reference", ex)
    if ext == ".md":
        text = _read_head(p, 20000)
        heads = sum(1 for ln in text.splitlines() if ln.startswith("#"))
        if p.stat().st_size >= 8000 and heads <= max(2, len(text.splitlines()) // 40):
            return "transcript", {**ex, "fmt": "md"}
        return "reference", ex
    if ext == ".docx" or ext in (".doc", ".odt", ".rtf"):
        return "handout", ex
    if ext in (".pdf", ".pptx", ".key", ".ppt", ".odp"):
        if ext == ".key":
            ex["warn"] = "Keynote .key is unsupported: export it to PDF"
        toks = {t for t in re.findall(r"[a-z]+", p.stem.lower())}
        if ext in (".key", ".ppt", ".odp") or toks & SLIDE_HINT or explicit_numbers(p.stem):
            return "slides", ex
        return "handout", ex
    if ext in IMAGE_EXT:
        return "image", ex
    if ext in CODE_EXT:
        return ("exercise", ex) if (_words(p.stem) & EXERCISE_TOKENS) else ("code", ex)
    return "other", ex


def _pins(profile: dict, sessions: list[dict], resources: Path) -> tuple[dict, dict, dict]:
    """(per-session pins {path: {n: reason}}, course pins {path: reason}, classwork dirs {path: n})."""
    from . import profile as prof  # lazy: keeps ``yaml`` out of the import chain
    per: dict[str, dict] = {}
    course: dict[str, str] = {}
    cw: dict[str, int] = {}
    paths = profile.get("paths") or {}
    p2 = dict(profile)
    p2["_resources"] = resources
    for key in ("transcripts", "recordings", "whisper", "frames", "slides", "handouts", "classwork"):
        val = paths.get(key)
        if val in (None, "", []):
            continue
        pats = [val] if isinstance(val, str) else list(val)
        for s in sessions:
            fmt = {"n": s["n"], "nn": f"{int(s['n']):02d}", "stem": s.get("stem"), "title": s.get("title")}
            for pat in pats:
                try:
                    hits = prof.resolve_repo_paths({**p2, "paths": {key: pat}}, key, **fmt)
                except Exception:  # noqa: BLE001
                    continue
                for h in hits:
                    k = h.as_posix()
                    if key == "classwork":
                        cw[k] = int(s["n"])
                    elif "{" in pat:
                        per.setdefault(k, {})[int(s["n"])] = f"profile paths.{key}"
                    elif key in ("slides", "handouts"):
                        course[k] = f"profile paths.{key}"
    return per, course, cw


def inventory(profile: dict, resources: Path | None = None) -> list[Item]:
    """Classify every file under ``resources`` (README / dotfiles / frames internals skipped)."""
    res = Path(resources) if resources else Path(profile["_resources"])
    items: list[Item] = []
    if not res.is_dir():
        return items
    sessions = profile.get("sessions") or []
    per, course, cw = _pins(profile, sessions, res)
    syl = profile.get("paths", {}).get("syllabus")
    syl_pins = [syl] if isinstance(syl, str) else list(syl or [])
    cw_dirs = [Path(k) for k in cw]

    def walk(d: Path):
        for e in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if e.name.startswith(".") or e.name == "__pycache__":
                continue
            if e.is_dir():
                if e in cw_dirs or e.as_posix() in cw:
                    items.append(Item(e, e.relative_to(res).as_posix(), "classwork", e.name,
                                      pins={cw[e.as_posix()]: "profile paths.classwork"}))
                elif _is_frames_dir(e):
                    it = Item(e, e.relative_to(res).as_posix(), "frames", e.name)
                    it.tokens = tokens(e.name)
                    items.append(it)
                else:
                    walk(e)
                continue
            rel = e.relative_to(res).as_posix()
            if e.name.lower() in IGNORE_NAMES and (e.name.lower() != "readme.md" or "/" not in rel):
                continue
            try:
                size = e.stat().st_size
            except OSError:
                size = 0
            kind, ex = _classify_file(e, rel, profile, syl_pins)
            stem = e.name[: -len(".whisper.json")] if kind == "whisper" else e.stem
            it = Item(e, rel, kind, stem, size, title=ex.get("title"), fmt=ex.get("fmt"), warn=ex.get("warn"))
            hdr = ex.get("hdr")
            if hdr:
                it.duration_min = hdr.get("duration_min")
                it.dates = [c["date"] for c in hdr.get("start_candidates") or []]
            if kind == "transcript" and it.fmt in ("vtt", "srt"):
                try:
                    cues = tr.parse_cues(e.read_text(encoding="utf-8-sig", errors="replace"))
                    if cues:
                        it.duration_min = int(round(cues[-1]["end"] / 60))
                except OSError:
                    pass
            it.dates += [x for x in name_dates(stem) if x not in it.dates]
            it.numbers = keyword_numbers(stem)
            if not it.numbers:                           # nearest parent folder with a class number
                for part in reversed(rel.split("/")[:-1]):
                    it.numbers = keyword_numbers(part)
                    if it.numbers:
                        break
            if not it.numbers:
                it.numbers = leading_numbers(stem)
            it.tokens = tokens(strip_session_refs(stem))
            it.pins = dict(per.get(e.as_posix(), {}))
            if e.as_posix() in course:
                it.course_pin = course[e.as_posix()]
            items.append(it)

    walk(res)
    return items


# ------------------------------------------------------------------ scoring / assignment
def score_item(it: Item, sessions: list[dict]) -> list[tuple[int, float, str]]:
    """[(n, score, reason)] best first, over every session."""
    title_sim: dict[int, float] = {}
    for s in sessions:
        sims = [_sim_tokens(it.tokens, tokens(s.get("title") or ""))]
        if it.title:
            sims.append(_sim_tokens(tokens(it.title), tokens(s.get("title") or "")))
        title_sim[int(s["n"])] = max(sims)
    strong = {n for n, v in title_sim.items() if v >= 0.8}
    res: list[tuple[int, float, str]] = []
    for s in sessions:
        n = int(s["n"])
        comps: list[tuple[float, str]] = []
        stem_k = stem_key(it.stem)
        if s.get("stem") and stem_k == stem_key(str(s["stem"])):
            comps.append((1.0, "exact stem match"))
        if n in it.pins:
            comps.append((1.0, it.pins[n]))
        if it.title:
            sim = _sim_tokens(tokens(it.title), tokens(s.get("title") or ""))
            if sim >= 0.8:
                comps.append((0.9 * sim, f"header title ~ {s.get('title')!r}"))
        if s.get("date") and s["date"] in [c for c in it.dates]:
            via_header = bool(it.title)
            comps.append((0.85 if via_header else 0.75, f"date {s['date']}"))
        sim = title_sim[n]
        if sim >= 0.8 and not it.title:
            comps.append((0.9 * sim, "file name ~ title"))
        elif sim >= 0.2:
            comps.append((0.7 * sim, "file name ~ title"))
        if n in it.numbers and not (strong and n not in strong):
            comps.append((0.8, f"explicit session number {n}"))
        best = max(comps, default=(0.0, ""))
        res.append((n, best[0], best[1]))
    res.sort(key=lambda r: (-r[1], r[0]))
    return res


def _probe_scores(it: Item, sessions: list[dict], probe) -> list[tuple[int, float, str]]:
    text = probe(it.path) if probe else ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:6]
    out = []
    for s in sessions:
        best = max((_sim_tokens(tokens(ln), tokens(s.get("title") or "")) for ln in lines), default=0.0)
        out.append((int(s["n"]), 0.6 * best if best >= 0.8 else 0.0, "first page ~ title"))
    out.sort(key=lambda r: (-r[1], r[0]))
    return out


def _merge_scores(a, b):
    d = {n: (sc, why) for n, sc, why in a}
    for n, sc, why in b:
        if sc > d.get(n, (0, ""))[0]:
            d[n] = (sc, why)
    res = [(n, sc, why) for n, (sc, why) in d.items()]
    res.sort(key=lambda r: (-r[1], r[0]))
    return res


SPEECH = ("transcript", "whisper", "recording", "frames")


def _decide(it: Item, ranked) -> None:
    it.candidates = [{"n": n, "score": round(sc, 2)} for n, sc, _ in ranked[:2] if sc > 0]
    best = ranked[0] if ranked else (None, 0.0, "")
    group = [best] if best[0] is not None else []
    if best[0] is not None and len(it.numbers) > 1 and best[2].startswith("explicit session number"):
        group = [r for r in ranked if r[1] >= best[1] - 1e-9 and r[2].startswith("explicit session number")]
    outside = [r for r in ranked if r not in group]
    second = outside[0][1] if outside else 0.0
    it.score = best[1]
    need = HANDOUT_MIN if it.kind == "handout" else ASSIGN_MIN
    if best[0] is not None and best[1] >= need and best[1] - second >= MARGIN:
        it.scope = "session"
        it.ns = sorted(r[0] for r in group)
        it.n = it.ns[0]
        it.reason = best[2] if len(it.ns) == 1 else "explicit session numbers " + ", ".join(map(str, it.ns))
        return
    it.n, it.ns = None, []
    if it.kind == "handout":
        it.scope, it.reason = "course", it.course_pin or "handout without a session signal (course-wide)"
    else:
        it.scope = "unassigned"
        why = "ambiguous between sessions" if best[1] >= need else "no confident session match"
        it.reason = why


def _assign_by_number(it: Item, sessions: list[dict]) -> None:
    """data / exercise / reference: the class number(s) in the name or folder, else course-wide."""
    have = {int(s["n"]) for s in sessions}
    hit = [n for n in it.numbers if n in have]
    if it.pins:
        hit = sorted(it.pins)
    if hit:
        it.scope, it.ns, it.n, it.score = "session", sorted(hit), min(hit), 0.8
        it.reason = (f"class number {hit[0]} in the name or folder" if len(hit) == 1
                     else "class numbers " + ", ".join(map(str, sorted(hit))))
    else:
        it.scope, it.ns, it.n, it.score = "course", [], None, 0.0
        it.reason = ("class number " + ", ".join(map(str, it.numbers)) + " matches no session; course-wide"
                     if it.numbers else "no class number in the name: course-wide")


def assign(items: list[Item], sessions: list[dict], probe=None, only=None) -> None:
    """Fill scope/n/score/reason/candidates on ``items`` (or the subset ``only``)."""
    todo = [i for i in items if (only is None or i in only)]
    for it in todo:
        if it.kind == "syllabus":
            it.scope, it.reason = "course", "syllabus"
            continue
        if it.kind in EXTRA_KINDS:
            _assign_by_number(it, sessions)
            continue
        if it.course_pin and not it.pins and it.kind in ("handout", "slides"):
            it.scope, it.reason = "course", it.course_pin
            continue
        ranked = score_item(it, sessions) if sessions else []
        _decide(it, ranked)
        if it.scope != "session" and probe and it.kind in ("slides", "handout", "other") \
                and it.path.suffix.lower() in (".pdf", ".docx", ".pptx"):
            _decide(it, _merge_scores(ranked, _probe_scores(it, sessions, probe)))
    # files sharing a stem follow the best-scoring one
    groups: dict[str, list[Item]] = {}
    for it in todo:
        if it.kind in SPEECH:
            groups.setdefault(stem_key(it.stem), []).append(it)
    for members in groups.values():
        lead = max((m for m in members if m.scope == "session"), key=lambda m: m.score, default=None)
        if lead is None:
            continue
        for m in members:
            if m is not lead and (m.scope != "session" or m.score < lead.score) and m.n != lead.n:
                m.scope, m.n, m.ns, m.score = "session", lead.n, list(lead.ns), lead.score
                m.reason = f"shares stem with {lead.rel}"


# ------------------------------------------------------------------ derived sessions
def _cluster_key(it: Item) -> str:
    return " ".join(tokens(it.stem)) or stem_key(it.stem)


def derive_sessions(items: list[Item], sessions: list[dict]) -> list[dict]:
    """New session rows for speech files that match none of ``sessions`` (PLAN 11.3)."""
    lonely = [i for i in items if i.kind in SPEECH and i.scope == "unassigned" and i.score < DERIVE_BELOW]
    clusters: dict[str, list[Item]] = {}
    for it in lonely:
        clusters.setdefault(_cluster_key(it), []).append(it)
    rows = []
    for members in clusters.values():
        lead = next((m for m in members if m.kind == "transcript"), members[0])
        dates = [m.dates[0] for m in members if len(m.dates) == 1]
        nums = next((m.numbers for m in members if m.numbers), [])
        rows.append({"title": lead.title or pretty_title(lead.stem), "stem": lead.stem,
                     "date": dates[0] if dates else None, "_num": nums[0] if nums else None,
                     "_members": members})
    rows.sort(key=lambda r: (r["date"] is None, r["date"] or "", r["_num"] if r["_num"] is not None else 10 ** 6,
                             r["title"].lower()))
    taken = {int(s["n"]) for s in sessions}
    nums = [r["_num"] for r in rows]
    use_explicit = all(x is not None for x in nums) and len(set(nums)) == len(nums) and not (set(nums) & taken)
    nxt = max(taken, default=0) + 1
    out = []
    for r in rows:
        if use_explicit:
            n = r["_num"]
        else:
            n = nxt
            nxt += 1
        row = {"n": n, "title": r["title"], "stem": r["stem"], "derived": True}
        if r["date"]:
            row["date"] = r["date"]
        for m in r["_members"]:
            m.scope, m.n, m.ns, m.score, m.reason = "session", n, [n], 1.0, "derived session"
            m.candidates = []
        out.append(row)
    out.sort(key=lambda r: r["n"])
    return out


@dataclass
class Discovery:
    resources: Path
    items: list[Item]
    sessions: list[dict]          # profile rows + derived rows
    derived: list[dict]
    warnings: list[str] = field(default_factory=list)

    def for_session(self, n: int, kinds=None) -> list[Item]:
        out = [i for i in self.items if i.scope == "session" and int(n) in (i.ns or [i.n]) and (not kinds or i.kind in kinds)]
        # exact stem matches first, then by score, then by path (stable)
        row = next((s for s in self.sessions if int(s["n"]) == int(n)), {})
        out.sort(key=lambda i: (i.stem != row.get("stem"), -i.score, i.rel))
        return out

    def course_docs(self) -> list[dict]:
        """Syllabus and course-wide handouts / references (course-wide data and exercises travel as
        ``data[]`` / ``exercises[]`` in inputs.json instead)."""
        return [{"path": i.path.as_posix(), "kind": i.kind, "reason": i.reason}
                for i in self.items if i.scope == "course" and i.kind not in ("data", "exercise")]

    def extras(self, n: int) -> dict:
        """``{data, exercises, references}`` for session ``n``: assigned to it, plus the course-wide ones.
        Each row is ``{path, kind, sessions, reason}`` (``sessions`` is [] for a course-wide file)."""
        out: dict = {"data": [], "exercises": [], "references": []}
        key = {"data": "data", "exercise": "exercises", "reference": "references"}
        for i in self.items:
            if i.kind not in key:
                continue
            if (i.scope == "session" and int(n) in (i.ns or [i.n])) or i.scope == "course":
                out[key[i.kind]].append({"path": i.path.as_posix(), "kind": i.kind,
                                         "sessions": list(i.ns) if i.scope == "session" else [],
                                         "reason": i.reason})
        return out

    def unassigned(self) -> list[dict]:
        return [{"path": i.path.as_posix(), "kind": i.kind, "candidates": i.candidates}
                for i in self.items if i.scope == "unassigned"]

    def session_map(self) -> list[dict]:
        rows = []
        for s in self.sessions:
            n = int(s["n"])
            its = self.for_session(n)
            rows.append({"n": n, "title": s.get("title"), "stem": s.get("stem"), "date": s.get("date"),
                         "derived": bool(s.get("derived")),
                         "files": [{"kind": i.kind, "path": i.path.as_posix(), "score": round(i.score, 2),
                                    "reason": i.reason} for i in its]})
        return rows

    def to_json(self) -> dict:
        return {"resources": self.resources.as_posix(),
                "sessions": self.session_map(),
                "derived_sessions": [{k: v for k, v in s.items()} for s in self.derived],
                "course_docs": self.course_docs(),
                "unassigned": self.unassigned(),
                "files": [i.to_json() for i in self.items],
                "warnings": self.warnings}


def discover(profile: dict, resources: Path | None = None, probe="default") -> Discovery:
    """Inventory + assignment + derived sessions for a loaded profile (a minimal dict also works)."""
    res = Path(resources) if resources else Path(profile["_resources"])
    if probe == "default":
        probe = default_probe
    table = [dict(s) for s in (profile.get("sessions") or [])]
    items = inventory(profile, res)
    warnings: list[str] = []
    assign(items, table, probe)
    derived = derive_sessions(items, table)
    full = table + derived
    if derived:
        left = [i for i in items if i.scope == "unassigned" or i.kind in EXTRA_KINDS]
        assign(items, full, probe, only=left)
    for it in items:
        if it.warn:
            warnings.append(f"{it.rel}: {it.warn}")
    return Discovery(res, items, full, derived, warnings)


def match_session(sessions: list[dict], name: str) -> int | None:
    """Session number for a free-form name (used for prior notes without an NN prefix), or None."""
    it = Item(Path(name), name, "other", Path(name).stem)
    it.tokens = tokens(Path(name).stem)
    it.numbers = []
    ranked = score_item(it, sessions)
    if ranked and ranked[0][1] >= ASSIGN_MIN and ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0) >= MARGIN:
        return ranked[0][0]
    return None


# ------------------------------------------------------------------ writing derived sessions into a profile
def _yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def session_line(row: dict) -> str:
    parts = [f"n: {row['n']}", f"title: {_yaml_str(row['title'])}"]
    if row.get("stem"):
        parts.append(f"stem: {_yaml_str(row['stem'])}")
    if row.get("date"):
        parts.append(f"date: {row['date']}")
    if row.get("order") is not None:
        parts.append(f"order: {row['order']}")
    return "  - {" + ", ".join(parts) + "}"


def append_sessions_to_profile(text: str, rows: list[dict]) -> str:
    """Append ``rows`` to the front matter ``sessions:`` list (never rewrites existing rows)."""
    if not rows:
        return text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    if lines[0].lstrip("﻿").rstrip() != "---" or end is None:
        raise ValueError("profile has no YAML front matter")
    new = [session_line(r) for r in rows]
    start = next((i for i in range(1, end) if re.match(r"^sessions:\s*(\[\s*\])?\s*(#.*)?$", lines[i])), None)
    if start is None:
        lines[end:end] = ["sessions:"] + new
        return "\n".join(lines)
    if "[" in lines[start]:
        lines[start] = "sessions:"
    last = start
    for i in range(start + 1, end):
        ln = lines[i]
        if ln.strip() == "" or ln.lstrip().startswith("#"):
            continue
        if ln.startswith((" ", "\t", "-")):
            last = i
        else:
            break
    lines[last + 1:last + 1] = new
    return "\n".join(lines)


def clear_sessions(text: str) -> str:
    """The profile text without the rows of its front matter ``sessions:`` list (used for the blank template)."""
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
    start = next((i for i in range(1, end or 0) if re.match(r"^sessions:", lines[i])), None)
    if start is None:
        return text
    last = start
    for i in range(start + 1, end):
        ln = lines[i]
        if ln.strip() == "" or ln.lstrip().startswith("#"):
            continue
        if ln.startswith((" ", "\t", "-")):
            last = i
        else:
            break
    lines[start:last + 1] = ["sessions: []"]
    return "\n".join(lines)
