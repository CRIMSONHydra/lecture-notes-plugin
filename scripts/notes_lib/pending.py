"""Pending cross-note references (PLAN 18): deterministic detection shared by concept_map.py and lint_note.py.

Sessions are written in parallel, so a writer sometimes cites an earlier session whose note does not exist yet.
The rule for writers is a **pending marker** around a normal sentence::

    <!-- pending-ref:07 -->Session 7 (modelling the business workflow with data) built the order journey ...<!-- /pending-ref:07 -->

and never meta commentary about the note set ("has no note yet"). ``scan(text)`` finds

* ``marker``: every ``pending-ref:NN`` span, with its target session;
* ``legacy``: sentences that still make the forbidden claim ("has no note in this set yet", "note has not been
  written", ...), with the session numbers named in the sentence (or the one before it).

Both kinds carry ``line`` / ``end_line`` (1-based), the nearest preceding ``heading``, the exact source text
(``replaces``, usable as an Edit ``old_string``) and the ``targets``.
"""
from __future__ import annotations

import re

PENDING_ANY_RE = re.compile(r"<!--\s*(/?)pending-ref:(\d+[a-z]?)\s*-->")
PENDING_SPAN_RE = re.compile(r"<!--\s*pending-ref:(\d+[a-z]?)\s*-->(.*?)<!--\s*/pending-ref:\1\s*-->", re.S)
SWEEP_ANY_RE = re.compile(r"<!--\s*(/?)sweep:([\w.:-]+)\s*-->")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEAD_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")

# The forbidden meta commentary. Every alternative names a *note* (or the set/series), so "not written to RAM"
# and "notes written by agents" never match.
LEGACY_RE = re.compile(
    r"""(?ix)
      \b(?:has|have)\s+no\s+(?:(?:earlier|prior|previous|advanced|basic)\s+){0,2}notes?\b
    | \bno\s+notes?\s+(?:(?:in|for)\s+(?:this|the|it|them)\b[\w\s]{0,12}\s+)?(?:yet|exists?|written|available)\b
    | \bno\s+(?:earlier|prior|previous)\s+(?:\w+\s+)?notes?\s+(?:exists?|yet|to\s+link)\b
    | \bnotes?\s+(?:has|have)\s+(?:not|n't)\s+(?:yet\s+)?(?:been\s+)?(?:written|generated|created|published|made)\b
    | \bnotes?\s+(?:does|do)\s+not\s+(?:yet\s+)?exist\b
    | \bnotes?\s+(?:is|are)\s+not\s+(?:yet\s+)?(?:written|available)\b
    | \bnotes?\s+exists?\s+yet\b
    | \bnone\s+of\s+(?:those|these|them)\s+notes\b
    | \bdoes\s+not\s+exist\s+at\s+the\s+time\b
    | \bnot\s+(?:yet\s+)?been\s+written\s+at\s+the\s+time\b
    | \b(?:named|names?|naming)\b[^.;]{0,60}?\bwithout\s+(?:a\s+)?links?\b
    | \bnot\s+linked\s+(?:here\s+)?(?:because|since|yet)\b
    | \bnot\s+yet\s+linked\b
    | \bwill\s+link\s+(?:here|to\s+this)\s+(?:when|once|after)\b
    | \b(?:is\s+|are\s+)?named\s+(?:in\s+plain\s+text|by\s+session\s+number)\b
    | \bnot\s+linked\s+here;\s+they\s+are\s+named\b
    """)
_SESSION_RE = re.compile(r"\bsessions?\s+(\d{1,2}[a-z]?(?:\s*(?:,|and|&|or|/)\s*\d{1,2}[a-z]?)*)", re.I)
_ABBREV = ("e.g", "i.e", "etc", "vs", "cf")


def mask_code(text: str) -> str:
    """Blank fenced code blocks and the YAML front matter (keeping newlines, so offsets and lines stay valid)."""
    lines = text.split("\n")
    out: list[str] = []
    fenced = False
    in_fm = bool(lines) and lines[0].strip() == "---"
    for i, ln in enumerate(lines):
        if in_fm:
            out.append(re.sub(r"[^\n]", " ", ln))
            if i > 0 and ln.strip() == "---":
                in_fm = False
            continue
        if _FENCE_RE.match(ln):
            fenced = not fenced
            out.append(re.sub(r"[^\n]", " ", ln))
        elif fenced:
            out.append(re.sub(r"[^\n]", " ", ln))
        else:
            out.append(ln)
    return "\n".join(out)


def _headings(text: str) -> list[tuple[int, str]]:
    return [(i, m.group(2)) for i, ln in enumerate(text.split("\n"), 1) if (m := _HEAD_RE.match(ln))]


def _heading_at(heads: list[tuple[int, str]], line: int) -> str:
    cur = ""
    for ln, t in heads:
        if ln > line:
            break
        cur = t
    return cur


def _sessions_in(s: str) -> list[int]:
    out: list[int] = []
    for m in _SESSION_RE.finditer(s):
        for d in re.findall(r"\d{1,2}", m.group(1)):
            if int(d) not in out:
                out.append(int(d))
    return out


def _sentence_bounds(text: str, a: int, b: int) -> tuple[int, int]:
    """Start/end offsets of the sentence that contains text[a:b] (paragraph-limited)."""
    ps = text.rfind("\n\n", 0, a)
    ps = 0 if ps < 0 else ps + 2
    pe = text.find("\n\n", b)
    pe = len(text) if pe < 0 else pe
    start = ps
    for m in re.compile(r"[.!?][\"')\]*]*\s+(?=[A-Z\[`*(])").finditer(text, ps, min(len(text), a + 1)):
        k = m.start()
        if text[max(ps, k - 4):k + 1].lower().rstrip(".").endswith(_ABBREV) or m.end() > a:
            continue
        start = m.end()
    if start == ps:                                   # skip a quote marker, bullet or `**Note:**` label
        lead = re.match(r"(?:>\s*)*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*[^*\n]{1,40}:\*\*\s*)?", text[ps:a])
        if lead and ps + lead.end() <= a:
            start = ps + lead.end()
    end = pe
    for m in re.finditer(r"[.!?][\"')\]*]*(?=\s|$)", text[b:pe]):
        k = b + m.start()
        if text[max(a, k - 4):k + 1].lower().rstrip(".").endswith(_ABBREV):
            continue
        end = b + m.end()
        break
    return start, end


def scan(text: str) -> dict:
    """{"markers": [...], "legacy": [...], "problems": [...]} for one note's text (front matter included)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    masked = mask_code(text)
    heads = _headings(masked)
    line_of = lambda off: masked.count("\n", 0, off) + 1  # noqa: E731
    markers: list[dict] = []
    problems: list[str] = []
    spans: list[tuple[int, int]] = []
    for m in PENDING_SPAN_RE.finditer(masked):
        inner = text[m.start(2):m.end(2)]
        t = m.group(1)
        markers.append({"kind": "marker", "target_raw": t, "targets": [int(re.match(r"\d+", t).group())],
                        "line": line_of(m.start()), "end_line": line_of(m.end()),
                        "heading": _heading_at(heads, line_of(m.start())),
                        "text": " ".join(inner.split()), "replaces": text[m.start():m.end()]})
        spans.append((m.start(), m.end()))
    # unbalanced markers
    open_t, open_line = None, 0
    for m in PENDING_ANY_RE.finditer(masked):
        closing, t = bool(m.group(1)), m.group(2)
        ln = line_of(m.start())
        if not closing:
            if open_t is not None:
                problems.append(f"line {ln}: pending-ref:{t} opens inside pending-ref:{open_t}")
            open_t, open_line = t, ln
        else:
            if open_t != t:
                problems.append(f"line {ln}: /pending-ref:{t} has no matching opening marker")
            open_t = None
    if open_t is not None:
        problems.append(f"line {open_line}: pending-ref:{open_t} is never closed")
    # legacy stale phrases (outside the marker spans and outside sweep blocks)
    sweep_spans = []
    so = None
    for m in SWEEP_ANY_RE.finditer(masked):
        if not m.group(1):
            so = m.start()
        elif so is not None:
            sweep_spans.append((so, m.end()))
            so = None
    seen_sent: set[int] = set()
    legacy: list[dict] = []
    for m in LEGACY_RE.finditer(masked):
        if any(a <= m.start() < b for a, b in sweep_spans):
            continue
        ls = masked.rfind("\n", 0, m.start()) + 1
        if masked[ls:m.start()].lstrip().startswith("|"):          # table rows are reference lists, not claims
            continue
        s, e = _sentence_bounds(masked, m.start(), m.end())
        if s in seen_sent:
            continue
        seen_sent.add(s)
        sent = text[s:e]
        targets = _sessions_in(masked[s:m.end()]) or _sessions_in(masked[max(0, s - 400):s])
        # the sentence holds the target when it names a session before the phrase; otherwise the previous one does
        inside = any(a <= s and e <= b for a, b in spans)
        legacy.append({"kind": "legacy", "target_raw": "", "targets": targets,
                       "line": line_of(s), "end_line": line_of(e), "heading": _heading_at(heads, line_of(s)),
                       "text": " ".join(sent.split()), "replaces": sent, "in_marker": inside})
    return {"markers": markers, "legacy": legacy, "problems": problems}
