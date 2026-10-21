# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Lint notes against the note-format contract (PLAN 3.4) and the tier config of the profile.

    uv run lint_note.py <note.md>... --tier basic|advanced [--project DIR] [--profile REF]
                        [--digest D] [--meta-dir M] [--legacy] [--json OUT]

Errors (exit 1): front-matter keys; the section sign; H1 format/count; required sections; code over 120
columns; tables over 9 columns; images that are not ``figures/*.png|jpg|jpeg``, missing or external; ``{run}``
without an output block; Mermaid when ``diagrams: ascii``; images when ``figures: none``; placeholders
(TODO, TBD, XXX, lorem, [citation needed]); gap-fill share over the cap (basic); coverage sidecar missing
a digest concept of depth >= explained (each needs >= 1 example; basic must cover >= 95%, advanced 100%);
a digest V-row (Class visuals, treatment other than ``text``) with no figure in the note (PLAN 12.2; needs
``<stem>.visuals.json`` in the meta dir).
Warnings: code 96-120 columns; hand-numbered headings; raw HTML other than details/summary/br/sub/sup;
banned phrases (advanced); Going Deeper over 8 bullets or paragraphs over 40 words; check-yourself count
out of range; dead ``.md`` links; fewer figures than ``min_figures``; ``$..$`` math when ``math: false``;
a class figure (``*-class-*``) whose caption lacks ``From class, hh:mm:ss``; a taught concept whose section
is a one-liner with no code, table or figure ("thin coverage", basic); a yellow-severity `Class correction`
callout in basic ("move it to `## Minor slips in class`", PLAN 16.5).
Sweep markers (PLAN 16.3): `<!-- sweep:ID -->` ... `<!-- /sweep:ID -->` lines are invisible to every prose/word
count and HTML check; errors for unbalanced/nested/duplicate markers (`sweep-marker`) and for a link inside a
sweep block or a Builds on / Revisited / Recall callout whose note or section does not exist (`builds-on-link`).
Pending references (PLAN 18): `<!-- pending-ref:NN -->...<!-- /pending-ref:NN -->` is the writer's way to cite a session
whose note does not exist yet (unbalanced markers are errors, `pending-marker`). Warnings: a pending-ref whose note now exists
(`pending-ref-resolvable`) and any sentence that says a note is absent ("has no note yet", "named without links", ...,
`stale-note-ref`); `/notes:sweep` turns both into links.
Basic `## Minor slips in class` must be a two-column `| said | actual |` table (`minor-slips-format`).

``--legacy`` is for notes written before the contract (no front matter): it skips the front-matter and
heading-number checks and downgrades the section sign to a warning.

--meta-dir defaults to <project>/.notes-plugin-internal/work/meta/<tier> for a note at <project>/notes/<tier>/.

Every issue carries ``kind``: ``mechanical`` (a formatting fix, safe to automate) or ``content`` (needs the
writer), which qa.py uses to classify failures.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import digest as digest_lib, guard, layout, notefile, pending as pending_lib, platform as plat, process_talk, profile as prof  # noqa: E402

plat.utf8_stdio()

BASIC_KEYS = ["title", "session", "tier", "profile", "date", "duration_min", "sources",
              "instructor_code", "revision_minutes", "verified_as_of"]
ADV_EXTRA = ["prerequisites"]
NULLABLE = {"instructor_code", "companion_of"}
SEP_H1 = re.compile(r"^\d+[a-z]?\s*·\s*\S")
FILENAME_RE = re.compile(r"^\d+[a-z]?-[a-z0-9][a-z0-9-]*\.md$")
IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"([^\"]*)\")?\s*\)")
FIG_OK_RE = re.compile(r"figures/[^/\\]+\.(?:png|jpe?g)", re.I)
CLASS_FIG_RE = re.compile(r"-(?:class|slide)-", re.I)
CAPTION_TS_RE = re.compile(r"from class[^.]*\d{1,2}:\d{2}|from the slides?,?\s*p\.?\s*\d+", re.I)
THIN_WORDS = 45
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
CODE_SPAN_RE = re.compile(r"`+[^`\n]*`+")
PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD|XXX)\b|(?i:\blorem\b|\[citation needed\])")
PLACEHOLDER_CASE = re.compile(r"\b(TODO|TBD|XXX)\b")
HTML_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b[^>]*>")
HTML_NAMES = {"div", "span", "p", "img", "a", "b", "i", "u", "s", "font", "center", "table", "thead", "tbody",
              "tr", "td", "th", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code", "script",
              "style", "iframe", "video", "audio", "svg", "em", "strong", "hr", "section", "article", "nav",
              "aside", "figure", "figcaption", "blockquote", "kbd", "mark", "small", "big", "link", "meta",
              "input", "button", "form", "label", "canvas", "object", "embed", "details", "summary", "br",
              "sub", "sup"}
HTML_ALLOWED = {"details", "summary", "br", "sub", "sup"}
MATH_RE = re.compile(r"(?<!\\)\$\$.+?\$\$|(?<![\\$])\$[^\s$][^$\n]*?(?<![\s\\])\$(?!\d)", re.S)
BANNED_DEFAULT = [r"as you know", r"recall that", r"obviously", r"it is well known", r"it's well known",
                  r"(?:is|are|it's|that's|this is)\s+(?:just\s+)?standard\b",
                  r"standard\s+(?:practice|trick|technique|approach|procedure|knowledge)\b"]
MECHANICAL = {"dup-session", "split-note", "section-sign", "h1-format", "h1-count", "h1-number", "filename", "code-wide", "code-wide-warn",
              "table-wide", "image-missing", "image-external", "image-format", "placeholder",
              "heading-number", "html-disallowed", "dead-link", "fm-tier", "process-talk"}


class Lint:
    def __init__(self, path: Path, line_off: int):
        self.path = path
        self.off = line_off
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def _add(self, bucket, code, msg, line):
        bucket.append({"code": code, "line": (line + self.off) if line else None, "msg": msg,
                       "kind": "mechanical" if code in MECHANICAL else "content"})

    def err(self, code, msg, line=None, raw_line=False):
        self._add(self.errors, code, msg, None if line is None else (line - self.off if raw_line else line))

    def warn(self, code, msg, line=None, raw_line=False):
        self._add(self.warnings, code, msg, None if line is None else (line - self.off if raw_line else line))


def flat_blocks(body: str):
    """Top-level blocks plus blocks nested in blockquotes: ``(kind, text, info, line, nested)``."""
    for kind, text, info, line in notefile.iter_blocks_ex(body):
        yield kind, text, info, line, False
        if kind == "quote":
            for k2, t2, i2, l2 in notefile.iter_blocks_ex(text):
                if k2 in ("code", "table"):
                    yield k2, t2, i2, line + l2 - 1, True


def strip_code(text: str) -> str:
    return CODE_SPAN_RE.sub(" ", text)


def heading_key(text: str) -> str:
    return notefile._heading_key(text)


def table_cols(first_row: str) -> int:
    row = CODE_SPAN_RE.sub("x", first_row).strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return len(re.split(r"(?<!\\)\|", row))


def cell_split(row: str) -> list[str]:
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


def section_lines(body_lines: list[str], headings: list[tuple[int, int, str]], prefix: str):
    """Lines (1-based start line, text) of the section whose heading key starts with ``prefix``."""
    for i, (ln, lvl, text) in enumerate(headings):
        if heading_key(text).startswith(prefix.lower()):
            end = len(body_lines) + 1
            for ln2, lvl2, _ in headings[i + 1:]:
                if lvl2 <= lvl:
                    end = ln2
                    break
            return ln + 1, body_lines[ln:end - 1]
    return None, []


def list_items(lines: list[str], start: int) -> list[tuple[int, str]]:
    """Split section lines into list items / paragraphs: [(line, text)]."""
    items: list[tuple[int, list[str]]] = []
    cur = None
    in_fence = False
    for i, ln in enumerate(lines):
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", ln):
            in_fence = not in_fence
            cur = None
            continue
        if in_fence:
            continue
        if not ln.strip():
            cur = None
            continue
        if re.match(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+", ln):
            cur = [start + i, [ln]]
            items.append(cur)  # type: ignore[arg-type]
        elif cur is not None:
            cur[1].append(ln)
        else:
            cur = [start + i, [ln]]
            items.append(cur)  # type: ignore[arg-type]
    return [(n, " ".join(ls)) for n, ls in items]  # type: ignore[misc]


def count_questions(lines: list[str]) -> int:
    in_fence, num, bul, h3, bold = False, 0, 0, 0, 0
    for ln in lines:
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", ln):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if re.match(r"^\s{0,3}\d+[.)]\s+\S", ln):
            num += 1
        elif re.match(r"^\s{0,3}[-*+]\s+\S", ln):
            bul += 1
        elif re.match(r"^\s{0,3}###\s+\S", ln):
            h3 += 1
        elif re.match(r"^\s{0,3}\*\*Q\d+", ln):
            bold += 1
    return num or bul or h3 or bold


SWEEP_RE = notefile.SWEEP_ANY_RE
XREF_LEADS = ("builds on", "revisited", "recall")
SLIP_SEV = "\U0001f7e1"


def gh_anchor(text: str) -> str:
    return re.sub(r"[^\w\- ]", "", text.lower().strip()).replace(" ", "-")


def heading_anchors(path: Path) -> set[str]:
    """GitHub-style anchors (with and without a hand/auto section number) of every heading in a note."""
    out: set[str] = set()
    try:
        text = notefile.parse_note(path).body
    except (OSError, prof.ProfileError):
        return out
    for kind, t, _info in notefile.iter_blocks(text):
        if kind == "heading":
            plain = notefile.clean_inline(t)
            out.add(gh_anchor(plain))
            out.add(gh_anchor(notefile._NUMBERED_RE.sub("", plain).strip()))
    return out


def check_sweep(L: Lint, path: Path, raw: str) -> None:
    """Balanced, unnested, unique sweep markers; links inside them and in Builds-on callouts must resolve."""
    lines = raw.replace("\r\n", "\n").split("\n")
    open_id, open_line, seen = None, 0, {}
    blocks: list[tuple[int, int]] = []                  # (first line, last line) of each sweep block
    fenced = False
    for i, ln in enumerate(lines, 1):
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", ln):
            fenced = not fenced
        if fenced:
            continue
        for m in SWEEP_RE.finditer(ln):
            closing, sid = bool(m.group(1)), m.group(2)
            if not closing:
                if open_id is not None:
                    L.err("sweep-marker", f"sweep:{sid} opens inside sweep:{open_id} (markers cannot nest)", i, raw_line=True)
                if sid in seen:
                    L.err("sweep-marker", f"duplicate sweep id {sid!r} (first at line {seen[sid]}): a re-run must "
                          "replace, not append", i, raw_line=True)
                seen.setdefault(sid, i)
                open_id, open_line = sid, i
            else:
                if open_id != sid:
                    L.err("sweep-marker", f"closing marker /sweep:{sid} does not match the open "
                          f"{open_id!r}" if open_id else f"closing marker /sweep:{sid} has no opening marker",
                          i, raw_line=True)
                else:
                    blocks.append((open_line, i))
                open_id = None
    if open_id is not None:
        L.err("sweep-marker", f"sweep:{open_id} is never closed", open_line, raw_line=True)
    # link targets in sweep blocks (own-line or inline) and in Builds-on style callouts
    checked: set[tuple[int, str]] = set()
    cache: dict[Path, set[str]] = {}
    texts: list[tuple[int, str]] = []                    # (line, text) to check
    for kind, text, _info, ln in notefile.iter_blocks_ex("\n".join(lines)):
        lead = re.sub(r"[*_\s]+", " ", text.strip().lower()).strip()
        if kind == "quote" and lead.startswith(XREF_LEADS):
            texts.append((ln, text))
    flat = "\n".join(lines)
    for m in re.finditer(r"<!--\s*sweep:([\w.:-]+)\s*-->(.*?)<!--\s*/sweep:\1\s*-->", flat, re.S):
        texts.append((flat.count("\n", 0, m.start()) + 1, m.group(2)))
    for ln, text in texts:
        for m in LINK_RE.finditer(text):
            tgt = m.group(1)
            if re.match(r"^[a-z][a-z0-9+.-]*:", tgt, re.I) or (ln, tgt) in checked:
                continue
            checked.add((ln, tgt))
            f, _, frag = tgt.partition("#")
            dest = path if not f else path.parent / unquote(f.split("?")[0])
            if f and not f.lower().endswith(".md"):
                continue
            if not dest.is_file():
                L.err("builds-on-link", f"cross-reference target {tgt!r} does not exist (no such note)", ln, raw_line=True)
            elif frag:
                anchors = cache.setdefault(dest, heading_anchors(dest))
                if gh_anchor(unquote(frag)) not in anchors and not frag.startswith("sec-"):
                    L.err("builds-on-link", f"cross-reference {tgt!r}: no heading with that anchor in {dest.name}", ln, raw_line=True)


def check_pending(L: Lint, path: Path, raw: str) -> None:
    """Pending cross-references (PLAN 18): stale meta commentary and ``pending-ref`` markers that can now be resolved."""
    res = pending_lib.scan(raw)
    for p in res["problems"]:
        m = re.match(r"line (\d+): (.*)", p)
        L.err("pending-marker", m.group(2) if m else p, int(m.group(1)) if m else None, raw_line=True)
    d = path.parent
    exists = lambda n: any(d.glob(f"{n:02d}-*.md"))  # noqa: E731
    for it in res["markers"]:
        if all(exists(t) for t in it["targets"]):
            L.warn("pending-ref-resolvable", f"pending-ref:{it['target_raw']} points at a note that now exists "
                   f"(session {it['targets'][0]}); run /notes:sweep to turn it into a link", it["line"], raw_line=True)
    for it in res["legacy"]:
        tgt = ", ".join(map(str, it["targets"])) or "?"
        have = bool(it["targets"]) and all(exists(t) for t in it["targets"])
        L.warn("stale-note-ref", "claims a note is absent (" + it["text"][:60].rstrip() + "...): "
               + (f"session {tgt} now has a note, run /notes:sweep (resolve-pending)" if have else
                  "never comment on the note set; wrap the reference in <!-- pending-ref:NN -->...<!-- /pending-ref:NN -->"),
               it["line"], raw_line=True)


def check_process_talk(L: Lint, raw: str) -> None:
    """Prose about how the note was made (agents, the execution policy, "this machine", the digest, the inputs):
    a student does not need it. Reword to talk about the subject only."""
    for it in process_talk.scan(raw):
        L.err("process-talk", f"talks about how the note was made ({it['phrase']}): reword for the reader. Output "
              "that was not run is introduced as 'Output:' or 'Expected output:' (optionally '(from the class "
              "demo)'), without saying whether or why it was run; a platform difference is stated as a fact about "
              "the platform. Drop a sentence that is only about the pipeline.", it["line"], raw_line=True)


def check_minor_slips(L: Lint, body: str, headings, tier: str) -> None:
    """Basic tier: `## Minor slips in class` is a `| said | actual |` table; yellow corrections live there."""
    if tier != "basic":
        return
    lines = body.replace("\r\n", "\n").split("\n")
    for ln, lvl, t in headings:
        if heading_key(t).startswith("minor slips in class"):
            sec_start, sec = section_lines(lines, headings, "minor slips in class")
            rows = [r for r in sec if r.strip().startswith("|")]
            if len(rows) < 3:
                L.err("minor-slips-format", "`Minor slips in class` needs a table: | said | actual | + one row per slip", ln)
                break
            hdr = [c.lower() for c in cell_split(rows[0])]
            if hdr != ["said", "actual"]:
                L.err("minor-slips-format", f"`Minor slips in class` header must be `| said | actual |`, got {rows[0].strip()!r}", ln)
            for r in rows[2:]:
                cells = cell_split(r)
                if len(cells) != 2 or not all(cells):
                    L.err("minor-slips-format", f"slip row must have exactly two non-empty cells (said | actual): {r.strip()[:60]!r}", ln)
                    break
            prose = [x for x in sec if x.strip() and not x.strip().startswith("|")]
            if prose:
                L.warn("minor-slips-format", "`Minor slips in class` should hold only the table (one line per slip)", ln)
            break
    for kind, text, _info, ln in notefile.iter_blocks_ex(body):
        if kind == "quote":
            first = text.strip().split("\n")[0]
            if re.match(r"^\W*class correction", first, re.I) and SLIP_SEV in first:
                L.warn("minor-slip-inline", "a yellow (imprecise aside) Class correction belongs in the "
                       "`## Minor slips in class` table, not inline (basic tier)", ln)



def lint_note(path: Path, cfg: dict, tier: str, *, legacy: bool, digest: Path | None,
              meta_dir: Path | None, banned: list[str]) -> dict:
    try:
        note = notefile.parse_note(path)
    except prof.ProfileError as e:
        L = Lint(path, 0)
        L.err("fm-invalid", f"front matter is not valid YAML: {e}")
        return {"path": path.as_posix(), "stem": path.stem, "num": "", "errors": L.errors, "warnings": [],
                "coverage": None}
    raw, body = note.raw, note.body
    off = raw[: len(raw) - len(body)].count("\n") if raw.endswith(body) else 0
    L = Lint(path, off)
    meta = note.meta
    stem = path.stem

    # filename + front matter ---------------------------------------------------------------------------
    if not FILENAME_RE.match(path.name):
        L.err("filename", f"file name {path.name!r} must match NN-kebab-slug.md")
    elif re.match(r"\d+[a-z]-", path.name):
        L.err("split-note", f"{path.name!r}: a letter after the session number marks a companion/split note; "
              "one note per session and tier, so merge it into the main note and delete the file")
    if not legacy:
        if note.legacy:
            L.err("fm-missing", "no YAML front matter (use --legacy for pre-contract notes)")
        else:
            keys = BASIC_KEYS + (ADV_EXTRA if tier == "advanced" else [])
            if meta.get("companion_of") not in (None, "", []):
                L.err("split-note", f"front matter `companion_of` ({meta.get('companion_of')!r}): companion notes are "
                      "not allowed; one note per session and tier, so merge it into the main note")
            for k in keys:
                if k not in meta:
                    L.err("fm-missing", f"front matter lacks `{k}`")
                elif meta[k] in (None, "", []) and k not in NULLABLE and k not in ("sources",):
                    L.err("fm-missing", f"front matter `{k}` is empty")
            if "sources" in meta and not isinstance(meta["sources"], list):
                L.err("fm-missing", "front matter `sources` must be a list")
            if meta.get("tier") not in (None, tier):
                L.err("fm-tier", f"front matter tier {meta.get('tier')!r} but linting as {tier!r}")

    # one note per (session, tier): another file in the folder with the same note number is an error ------
    mine = re.match(r"(\d+)[a-z]?-", path.name)
    if mine:
        key = int(mine.group(1))                    # the number only: 10 and 10a are the same session
        twins = [f.name for f in sorted(path.parent.glob("*.md")) if f != path and (m2 := re.match(r"(\d+)[a-z]?-", f.name))
                 and int(m2.group(1)) == key]
        if twins:
            L.err("dup-session", f"another note has the same session number {mine.group(1)}: "
                  f"{', '.join(twins)} (one note per session and tier; delete or merge the extra)")

    # section sign ----------------------------------------------------------------------------------------
    emit = L.warn if legacy else L.err
    hits = [i + 1 for i, ln in enumerate(raw.split("\n")) if "§" in ln]
    for i in hits[:20]:
        emit("section-sign", "the section sign never appears in a note (write \"see 2.4\")", i, raw_line=True)
    if len(hits) > 20:
        emit("section-sign", f"... and {len(hits) - 20} more lines with the section sign", None)

    check_sweep(L, path, raw)
    check_pending(L, path, raw)
    check_process_talk(L, raw)

    # headings ----------------------------------------------------------------------------------------------
    blocks = list(notefile.iter_blocks_ex(body))
    headings = [(ln, int(info), text) for kind, text, info, ln in blocks if kind == "heading"]
    h1s = [(ln, t) for ln, lvl, t in headings if lvl == 1]
    if len(h1s) != 1:
        L.err("h1-count", f"expected exactly one H1, found {len(h1s)}", h1s[1][0] if len(h1s) > 1 else None)
    if h1s:
        h1 = h1s[0][1]
        if not SEP_H1.match(h1):
            L.err("h1-format", f"H1 {h1!r} must look like `NN · Title`", h1s[0][0])
        else:
            fnum = notefile.note_id(path.name)[0]
            if fnum and note.num and fnum != note.num:
                L.err("h1-number", f"H1 number {note.num} does not match the file name ({fnum})", h1s[0][0])
    h2 = [(ln, t) for ln, lvl, t in headings if lvl == 2]
    for req in cfg.get("required_sections", []):
        if not any(heading_key(t).startswith(req.lower()) for _, t in h2):
            L.err("missing-section", f"required section `{req}` is missing")
    if not legacy:
        for ln, lvl, t in headings:
            if lvl in (2, 3) and re.match(r"^\s*\d+(?:\.\d+)*[.)]?\s+\S", t):
                L.warn("heading-number", f"heading {t!r} is hand-numbered; the builder numbers sections", ln)

    check_minor_slips(L, body, headings, tier)

    # blocks ------------------------------------------------------------------------------------------------
    n_img = 0
    figures_none = cfg.get("figures") == "none"
    flagged_fig = False
    embedded: list[str] = []
    code_starts = {ln: (txt, info) for kind, txt, info, ln, nested in flat_blocks(body) if kind == "code"}
    diagrams = cfg.get("diagrams", "ascii")
    for kind, text, info, ln, nested in flat_blocks(body):
        if kind == "code":
            lang, attrs = (str(info).split() + [""])[0].lower(), set(re.findall(r"[\w-]+", " ".join(
                re.findall(r"\{([^}]*)\}", str(info)))))
            for i, line in enumerate(text.split("\n")):
                w = len(line.expandtabs(4))
                if w > 120:
                    L.err("code-wide", f"code line is {w} columns (max 120, aim for 95)", ln + 1 + i)
                elif w > 95:
                    L.warn("code-wide-warn", f"code line is {w} columns (aim for 95)", ln + 1 + i)
            if lang == "mermaid" and diagrams == "ascii":
                L.err("mermaid-disallowed", "Mermaid is not allowed (profile says diagrams: ascii); "
                      "draw the diagram in a ```text fence", ln)
            if "run" in attrs and not nested:
                end = ln + len(text.split("\n")) + 1
                found = False
                for d in (1, 2, 3):
                    nxt = code_starts.get(end + d)
                    if nxt:
                        found = str(nxt[1]).split()[:1] == ["text"]
                        break
                if not found:
                    L.err("run-no-output", "`{run}` fence is not followed by a ```text output block "
                          "within 2 lines", ln)
        elif kind == "table":
            first = text.split("\n")[0]
            cols = table_cols(first)
            if cols > 9:
                L.err("table-wide", f"table has {cols} columns (max 9)", ln)
        if nested or kind in ("code",):
            continue
        # prose-like blocks from here on
        scan = text if kind != "heading" else text
        plain = strip_code(scan)
        for m in IMG_RE.finditer(scan):
            n_img += 1
            src = m.group(1)
            embedded.append(unquote(src).split("/")[-1])
            if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//)", src, re.I):
                L.err("image-external", f"external image {src!r}: figures must be local figures/*.png or *.jpg", ln)
            elif not FIG_OK_RE.fullmatch(src):
                L.err("image-format", f"image {src!r} must be a local figures/*.png, *.jpg or *.jpeg", ln)
            else:
                if CLASS_FIG_RE.search(src) and not CAPTION_TS_RE.search(m.group(2) or ""):
                    L.warn("class-caption", f"class figure {src!r} has no `**From class, hh:mm:ss.**` (or `**From the slides, p.N.**`) caption", ln)
                if figures_none and not flagged_fig:
                    L.err("figures-disallowed", "figures are not allowed in this profile/tier "
                          "(figures: none); use an ASCII diagram", ln)
                    flagged_fig = True
                if not (path.parent / unquote(src)).is_file():
                    L.err("image-missing", f"figure file {src!r} does not exist next to the note", ln)
        for m in LINK_RE.finditer(scan):
            tgt = m.group(1)
            t0 = tgt.split("#")[0].split("?")[0]
            if t0.lower().endswith(".md") and not re.match(r"^[a-z][a-z0-9+.-]*:", t0, re.I):
                if not (path.parent / unquote(t0)).is_file():
                    num, _slug = notefile.note_id(Path(unquote(t0)).name)
                    if num:
                        L.warn("dead-link", f"link target {tgt!r} is a sibling note that does not exist in this "
                               f"project yet (session {num} has no note here): write that note, or link to the "
                               "topic in prose instead", ln)
                    else:
                        L.warn("dead-link", f"link target {tgt!r} does not exist", ln)
        if kind in ("prose", "quote", "table", "heading"):
            m = PLACEHOLDER_CASE.search(plain) or re.search(r"\blorem\b|\[citation needed\]", plain, re.I)
            if m:
                L.err("placeholder", f"placeholder text left in the note: {m.group(0)!r}", ln)
        if kind in ("prose", "quote", "html", "table"):
            for m in HTML_TAG_RE.finditer(plain):
                name = m.group(1).lower()
                if name in HTML_NAMES and name not in HTML_ALLOWED:
                    L.warn("html-disallowed", f"raw HTML <{name}> is not allowed "
                           "(only details/summary/br/sub/sup)", ln)
                    break
            if not cfg.get("math", False) and kind != "table":
                if MATH_RE.search(re.sub(r"\\\$", "", plain)):
                    L.warn("math-disallowed", "`$...$` math but this tier has math: false "
                           "(write a literal dollar as \\$)", ln)
            if tier == "advanced":
                low = plain.lower()
                for pat in banned:
                    mm = re.search(pat, low)
                    if mm:
                        L.warn("banned-phrase", f"banned phrase {mm.group(0)!r} (assume nothing, explain)", ln)
    for fm_key in ("title", "verified_as_of"):             # placeholders in front matter text
        if isinstance(meta.get(fm_key), str) and PLACEHOLDER_RE.search(meta[fm_key]):
            L.err("placeholder", f"placeholder in front matter `{fm_key}`")

    # figures count -----------------------------------------------------------------------------------------
    if tier == "advanced" and cfg.get("figures") in ("expected", "class+generated", "generated", "class"):
        need = int(cfg.get("min_figures", 2))
        if n_img < need:
            L.warn("min-figures", f"{n_img} figure(s) embedded, profile expects at least {need}")

    # sections: Going Deeper, Check yourself -----------------------------------------------------------------
    body_lines = body.replace("\r\n", "\n").split("\n")
    if tier == "basic":
        start, lines = section_lines(body_lines, headings, "going deeper")
        if start:
            items = list_items(lines, start)
            bullets = [it for it in items if re.match(r"^\s{0,3}(?:[-*+]|\d+[.)])\s", body_lines[it[0] - 1])]
            cap = int(cfg.get("going_deeper_max", 8))
            if len(bullets) > cap:
                L.warn("going-deeper-long", f"Going Deeper has {len(bullets)} bullets (max {cap})", start)
            for n, txt in items:
                if notefile.count_words(txt) > 40:
                    L.warn("going-deeper-long", "a Going Deeper item is over 40 words "
                           "(links, not exposition)", n)
                    break
    start, lines = section_lines(body_lines, headings, "check yourself")
    if start:
        lo, hi = cfg.get("check_yourself", [5, 8])
        n = count_questions(lines)
        if not lo <= n <= hi:
            L.warn("check-count", f"Check yourself has {n} questions (expected {lo} to {hi})", start)

    # gap-fill share ------------------------------------------------------------------------------------------
    st = notefile.stats(body, cfg.get("closing_sections", []))
    if tier == "basic" and st["prose_words"]:
        share = st["gapfill_words"] / st["prose_words"]
        cap = float(cfg.get("gapfill_max_share", 0.30))
        if share > cap:
            L.err("gapfill-share", f"gap-fill callouts are {share:.0%} of prose "
                  f"({st['gapfill_words']}/{st['prose_words']} words); cap is {cap:.0%}")

    # digest checks: coverage sidecar + class visuals --------------------------------------------------------------
    coverage = None
    if digest is not None:
        coverage = {"checked": True, "sidecar": None, "missing": [], "no_example": [], "required": 0,
                    "covered": 0, "share": None, "thin": [], "visuals_sidecar": None, "visuals_missing": []}
        inv = [c for c in digest_lib.parse_inventory(digest) if c["required"]]
        lay, _t = layout.infer_from_note(path)
        mdir = meta_dir or (lay.meta(tier) if lay else path.parent.parent / "meta")
        side = mdir / f"{stem}.coverage.json"
        coverage["sidecar"] = side.as_posix()
        cov: dict = {}
        if not inv:
            L.warn("coverage-digest", f"no concept of depth explained or deeper found in {digest.name}")
        elif not side.is_file():
            L.err("coverage-missing", f"coverage sidecar {side.name} not found in {mdir.as_posix()}")
        else:
            try:
                cov = json.loads(side.read_text(encoding="utf-8-sig"))
            except ValueError as e:
                cov = {}
                L.err("coverage-missing", f"{side.name} is not valid JSON: {e}")
            covered = 0
            for c in inv:
                ent = cov.get(c["id"])
                if not ent:
                    coverage["missing"].append(c["id"])
                    (L.err if tier == "advanced" else L.warn)(
                        "coverage-missing", f"concept {c['id']} ({c['name']}) has no entry in {side.name}")
                elif int((ent or {}).get("worked_examples", 0)) < 1:
                    coverage["no_example"].append(c["id"])
                    (L.err if tier == "advanced" else L.warn)(
                        "coverage-examples", f"concept {c['id']} has no example (code, worked number or figure): "
                        "worked_examples must be >= 1")
                else:
                    covered += 1
            coverage["required"], coverage["covered"] = len(inv), covered
            coverage["share"] = round(covered / len(inv), 3) if inv else None
            need_share = 1.0 if tier == "advanced" else 0.95
            if inv and covered / len(inv) + 1e-9 < need_share:
                L.err("coverage-share", f"{covered}/{len(inv)} taught concepts are covered with an example "
                      f"({covered / len(inv):.0%}); {tier} needs {need_share:.0%}. Missing: "
                      + ", ".join(coverage["missing"] + coverage["no_example"]))
            if tier == "basic" and cov:
                body_lines2 = body.replace("\r\n", "\n").split("\n")
                keyed = [(ln, lvl, heading_key(t)) for ln, lvl, t in headings]
                for c in inv:
                    secs = (cov.get(c["id"]) or {}).get("sections") or []
                    words = code = figs = rows = found = 0
                    for sname in secs:
                        k = heading_key(str(sname))
                        i = next((j for j, (_ln, _lv, hk) in enumerate(keyed) if hk == k), None)
                        if i is None:
                            L.warn("coverage-section", f"{c['id']}: coverage section {sname!r} is not a heading "
                                   f"in the note ({side.name})")
                            continue
                        ln0, lvl0, _hk = keyed[i]
                        end = next((ln for ln, lv, _h in keyed[i + 1:] if lv <= lvl0), len(body_lines2) + 1)
                        st2 = notefile.stats("\n".join(body_lines2[ln0 - 1:end - 1]), ())
                        words += st2["prose_words"]
                        code += st2["code_lines"]
                        figs += st2["figures"]
                        rows += st2["table_rows"]
                        found += 1
                    if found and words < THIN_WORDS and not (code or figs or rows):
                        coverage["thin"].append(c["id"])
                        L.warn("thin-coverage", f"concept {c['id']} ({c['name']}) is a one-liner "
                               f"({words} words, no code, table or figure): give it its own subsection "
                               "(what was taught, the class example, code + output, the gotcha)")
        # class visuals: every V-row except `text` needs a figure in the note
        vis = [v for v in digest_lib.parse_visuals(digest) if v["treatment"] != "text"]
        vside = mdir / f"{stem}.visuals.json"
        coverage["visuals_sidecar"] = vside.as_posix()
        if vis:
            vj = None
            if vside.is_file():
                try:
                    vj = json.loads(vside.read_text(encoding="utf-8-sig"))
                except ValueError as e:
                    L.err("visuals-missing", f"{vside.name} is not valid JSON: {e}")
            else:
                L.err("visuals-missing", f"visuals sidecar {vside.name} not found in {mdir.as_posix()} "
                      f"(the digest has {len(vis)} class visual(s) to place)")
            if isinstance(vj, dict):
                emb = set(embedded)
                for v in vis:
                    ent = vj.get(v["id"]) or {}
                    files = [Path(str(f)).name for f in (ent.get("files") or [])]
                    if not files:
                        coverage["visuals_missing"].append(v["id"])
                        L.err("visual-missing", f"class visual {v['id']} ({v['what'][:60]}) has no figure: "
                              f"embed the class frame, redraw it, or both (treatment {v['treatment']})")
                    elif not all(f in emb for f in files):
                        coverage["visuals_missing"].append(v["id"])
                        L.err("visual-unreferenced", f"class visual {v['id']}: {', '.join(f for f in files if f not in emb)} "
                              "is in the sidecar but not embedded in the note")
                    elif tier == "basic" and ent.get("treatment") == "redraw":
                        L.warn("visual-redraw-only", f"class visual {v['id']}: basic shows the class version "
                               "(embed or both), not only a redraw")
    return {"path": path.as_posix(), "stem": stem, "num": note.num, "errors": L.errors,
            "warnings": L.warnings, "coverage": coverage}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("notes", nargs="+")
    ap.add_argument("--project", default=None, help="project dir (default: discovered from cwd)")
    ap.add_argument("--profile", default=None, help="profile id/path (default: <project>/.notes-plugin-internal/profile.md)")
    ap.add_argument("--tier", required=True, choices=["basic", "advanced"])
    ap.add_argument("--digest", default=None)
    ap.add_argument("--meta-dir", default=None)
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    profile = prof.load_or_exit(a.profile, data_dir=a.data_dir, project=a.project, notes=a.notes)
    cfg = prof.tier_cfg(profile, a.tier)
    banned = [re.escape(p.lower()) for p in cfg.get("banned_phrases", [])] or BANNED_DEFAULT
    digest = plat.norm(a.digest) if a.digest else None
    if digest is not None and not digest.is_file():
        print(f"lint_note: digest not found: {digest}", file=sys.stderr)
        return 2
    meta_dir = plat.norm(a.meta_dir) if a.meta_dir else None
    results = [lint_note(plat.norm(n), cfg, a.tier, legacy=a.legacy, digest=digest, meta_dir=meta_dir,
                         banned=banned) for n in a.notes]
    ne = sum(len(r["errors"]) for r in results)
    nw = sum(len(r["warnings"]) for r in results)
    for r in results:
        print(f"{Path(r['path']).name}: {len(r['errors'])} error(s), {len(r['warnings'])} warning(s)")
        for tag, issues in (("ERROR", r["errors"]), ("warn ", r["warnings"])):
            for i in issues:
                print(f"  {tag} {i['code']:<18} " + (f"L{i['line']}: " if i["line"] else "") + i["msg"])
    print(f"\nlint: {ne} error(s), {nw} warning(s) in {len(results)} note(s)")
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {"ok": ne == 0, "tier": a.tier, "error_count": ne, "warning_count": nw,
                                 "notes": results})
    return 1 if ne else 0


if __name__ == "__main__":
    raise SystemExit(main())
