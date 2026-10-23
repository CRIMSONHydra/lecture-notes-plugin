"""Markdown -> HTML for the PDF builder.

Covers: chapter discovery and ``§N.k`` heading numbering, fences (pygments, ``{run}`` info strings,
wide-code font shrink), KaTeX math (dollarmath, only when the tier allows it), Mermaid fences,
figures and captions, callouts, glyph stand-ins (no Type 3 emoji), tables, link rewriting, the
front-matter meta block, and the cover / contents / document shell.
"""
from __future__ import annotations

import html as htmllib
import re
from dataclasses import dataclass, field
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin
from pygments import highlight as pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from notes_lib import notefile
from notes_lib.profile import split_front_matter

from . import layout
from .assets import script_safe
from .css import CODE_BASE_PX, FONT_SANS, TIER_KICKER, TIER_LABEL

esc = lambda s: htmllib.escape(s, quote=False)       # noqa: E731
esc_attr = htmllib.escape


# =====================================================================
# 1. Chapters: discovery, section numbering
# =====================================================================
FENCE_RE = re.compile(r"^\s*(```|~~~)")
AUTHORED_RE = re.compile(r"^\s*\d+(?:\.\d+)*\.\s+|^\s*\d+\.\d+\s+")
H1_PREFIX_RE = re.compile(r"^\d+[a-z]?\s*[·\-–—]\s*")
NOTE_NAME_RE = re.compile(r"^(\d+[a-z]?)-(.+)\.md$", re.I)


@dataclass
class Chapter:
    num: str                 # "00" (index), "02", "02a"
    chnum: str               # "0", "2", "2a"  (section ids are chnum.k[.m])
    path: Path
    md: str                  # numbered body, front matter and H1 removed
    title: str
    sections: list[dict]
    meta: dict
    legacy: bool
    is_index: bool
    slug: str
    pdf_stem: str            # CPP-02-slug-basic
    gh_map: dict = field(default_factory=dict)      # github-style anchor -> section id
    label: str = ""          # "Index" | "Note 02"
    prev: "Chapter | None" = field(default=None, repr=False)    # neighbours in COURSE order (PLAN 13.5)
    next: "Chapter | None" = field(default=None, repr=False)


def iter_lines(md: str):
    """Yield (line, in_fence).  Headings inside code fences are not headings."""
    fence = None
    for line in md.split("\n"):
        m = FENCE_RE.match(line)
        if m:
            tok = m.group(1)
            if fence is None:
                fence = tok
                yield line, True
                continue
            if line.strip().startswith(fence):
                fence = None
            yield line, True
            continue
        yield line, fence is not None


def gh_anchor(text: str) -> str:
    """GitHub-style heading anchor."""
    t = re.sub(r"[^\w\- ]", "", text.lower().strip())
    return t.replace(" ", "-")


def strip_md(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]*)\*", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\$([^$]*)\$", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def number_headings(raw: str, chnum: str) -> tuple[str, str, list[dict], dict]:
    """Prefix every H2/H3 with ``§chnum.n[.m]`` and drop the H1 (the chapter header replaces it).

    Returns ``(md, h1_title, sections, gh_map)``.
    """
    out: list[str] = []
    title = ""
    sections: list[dict] = []
    gh_map: dict = {}
    n = m = 0
    seen_h1 = False
    for line, in_fence in iter_lines(raw):
        if in_fence or not line.startswith("#"):
            out.append(line)
            continue
        if line.startswith("# ") and not seen_h1:
            seen_h1 = True
            title = H1_PREFIX_RE.sub("", strip_md(line[2:]))
            out.append("")
            continue
        if line.startswith("## "):
            n += 1
            m = 0
            orig = re.sub(r"\s+#+\s*$", "", line[3:]).strip()
            plain_authored = strip_md(orig)
            text = AUTHORED_RE.sub("", orig)                  # drop the author's own "1. "
            sid = f"{chnum}.{n}"
            sections.append({"sid": sid, "text": strip_md(text), "level": 2})
            gh_map.setdefault(gh_anchor(plain_authored), sid)
            gh_map.setdefault(gh_anchor(strip_md(text)), sid)
            out.append(f"## §{sid} {text}")
            continue
        if line.startswith("### "):
            m += 1
            orig = re.sub(r"\s+#+\s*$", "", line[4:]).strip()
            plain_authored = strip_md(orig)
            text = AUTHORED_RE.sub("", orig)
            sid = f"{chnum}.{n}.{m}" if n else f"{chnum}.0.{m}"
            sections.append({"sid": sid, "text": strip_md(text), "level": 3})
            gh_map.setdefault(gh_anchor(plain_authored), sid)
            gh_map.setdefault(gh_anchor(strip_md(text)), sid)
            out.append(f"### §{sid} {text}")
            continue
        out.append(line)
    return "\n".join(out), title, sections, gh_map


def discover(src_dir: Path, prefix: str, tier: str, warn=None, order: dict | None = None) -> list[Chapter]:
    """All chapters of a tier's ``src/`` (README first as the Index), in COURSE order.

    ``order`` maps session number -> course-order value (``profile.session_orders``); without it the order is
    by number. Two files with the same note number are listed once (the first by name; ``warn`` says so)."""
    chapters: list[Chapter] = []
    files = []
    seen_nums: dict[str, str] = {}
    order = order or {}

    def key(p: Path):
        m = re.match(r"(\d+)([a-z]?)", NOTE_NAME_RE.match(p.name).group(1).lower())
        k = int(m.group(1))
        return (order.get(k, float(k)), k, m.group(2))

    for p in sorted(src_dir.glob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        if not NOTE_NAME_RE.match(p.name):
            if warn:
                warn(f"ignored {p.name}: file name is not NN[a]-slug.md")
            continue
        num = NOTE_NAME_RE.match(p.name).group(1).lower()
        num = str(int(re.match(r"\d+", num).group(0))).zfill(2) + re.sub(r"^\d+", "", num)
        if num in seen_nums:
            if warn:
                warn(f"duplicate note number {num}: {p.name} ignored, {seen_nums[num]} is used "
                     "(one note per session and tier; delete the extra)")
            continue
        seen_nums[num] = p.name
        files.append(p)
    files.sort(key=key)
    readme = next((p for p in src_dir.glob("*.md") if p.name.lower() == "readme.md"), None)
    if readme:
        files.insert(0, readme)
    nums = {NOTE_NAME_RE.match(p.name).group(1).lower() for p in files if NOTE_NAME_RE.match(p.name)}
    for path in files:
        raw = path.read_text(encoding="utf-8-sig")
        meta, body = split_front_matter(raw)
        is_index = path.name.lower() == "readme.md"
        if is_index:
            num = "00"
            chnum = "0i" if "00" in nums else "0"
            slug = "index"
        else:
            nm = NOTE_NAME_RE.match(path.name)
            num, slug = nm.group(1).lower(), nm.group(2)
            digits = re.match(r"\d+", num).group(0)
            chnum = str(int(digits)) + num[len(digits):]
            if len(digits) < 2:
                num = digits.zfill(2) + num[len(digits):]
        md, title, sections, gh_map = number_headings(body, chnum)
        if is_index:
            title = title if title and title.lower() != "index" else "Course index and class corrections"
        elif not title:
            title = str(meta.get("title") or slug.replace("-", " ").title())
        stem = f"{prefix}-00-index-{tier}" if is_index else notefile.pdf_name(prefix, num, slug, tier)[:-4]
        chapters.append(Chapter(
            num=num, chnum=chnum, path=path, md=md, title=title, sections=sections, meta=meta or {},
            legacy=not meta, is_index=is_index, slug=slug, pdf_stem=stem, gh_map=gh_map,
            label="Index" if is_index else f"Note {num}",
        ))
    notes_only = [c for c in chapters if not c.is_index]
    for i, c in enumerate(notes_only):
        c.prev = notes_only[i - 1] if i else None
        c.next = notes_only[i + 1] if i + 1 < len(notes_only) else None
    return chapters


# =====================================================================
# 2. Fences
# =====================================================================
_NO_HL = {"", "text", "txt", "plain", "output", "console-output"}


def highlight_code(code: str, lang: str):
    """Pygments HTML (nowrap) or None when the language is plain/unknown."""
    if lang in _NO_HL:
        return None
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        return None
    except Exception:
        return None
    try:
        return pyg_highlight(code, lexer, HtmlFormatter(nowrap=True)).rstrip("\n")
    except Exception:
        return None


def fence_lang(info: str) -> str:
    """Language from a fence info string: ``cpp {run}`` -> ``cpp``; ``{run}`` alone -> ``text``."""
    info = (info or "").strip()
    if not info or info.startswith("{"):
        return "text"
    return re.split(r"[\s{]", info, 1)[0].lower() or "text"


def longest_line(code: str) -> int:
    return max((len(l.expandtabs(4)) for l in code.split("\n")), default=0)


# =====================================================================
# 3. Callouts, glyphs, links, html whitelist
# =====================================================================
CALLOUTS: list[tuple[str, str]] = [
    ("class correction", "correction"), ("transcript correction", "correction"),
    ("class nuance", "correction"), ("correction", "correction"),
    ("reconstructed", "recon"), ("gotcha", "gotcha"), ("ub", "ub"),
    ("quiz tip", "tip"), ("exam tip", "tip"), ("interview tip", "tip"), ("tip", "tip"),
    ("hft reality", "reality"), ("market reality", "reality"), ("production reality", "reality"),
    ("deployment reality", "reality"), ("reality", "reality"),
    ("gap fill", "gapfill"), ("beyond class", "gapfill"),
    ("repo note", "repo"), ("builds on", "buildson"), ("recall", "buildson"), ("revisited", "buildson"),
    ("maths", "maths"), ("math", "maths"), ("paper", "paper"), ("jargon", "jargon"),
    ("the deck", "note"), ("note", "note"),
    ("sources", "meta"), ("source", "meta"), ("instructor code", "meta"), ("revision time", "meta"),
    ("study time", "meta"), ("verified", "meta"), ("session", "meta"), ("topics", "meta"),
    ("sets up", "meta"), ("slides", "meta"),
]


def callout_class(lead: str, extra: list[tuple[str, str]]) -> str:
    """Class for a callout label like ``Class correction (high):``; '' when it is no callout."""
    t = re.sub(r"\([^)]*\)", " ", lead.lower())
    t = re.sub(r"[:—\-]+\s*$", "", t.strip().rstrip(":")).strip()
    t = re.sub(r"\s+", " ", t)
    for key, cls in list(extra) + CALLOUTS:
        if t == key or t.startswith(key + " "):
            return cls
    return ""


SEV = {
    "\U0001f534": "sev-r", "\U0001f7e0": "sev-o", "\U0001f7e1": "sev-y", "\U0001f7e2": "sev-g",
}


def _svg(paths: str, color: str) -> str:
    return ('<svg class="gl" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" fill="none" '
            f'stroke="{color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{paths}</svg>')


def _blk(shapes: str) -> str:
    return ('<svg class="gl blk" viewBox="0 0 10 16" preserveAspectRatio="none" '
            f'xmlns="http://www.w3.org/2000/svg" fill="currentColor">{shapes}</svg>')


TICK = _svg('<path d="M3 8.6 L6.4 12 L13 4.6"/>', "#2e7d4f")
_STAR = ('<svg class="gl" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" fill="#c07f10"><path d="M8 1 L10 6 L15.5 6.2 L11.2 9.6 L12.8 15 L8 11.8 L3.2 15 L4.8 9.6 L0.5 6.2 L6 6 Z"/></svg>')
CROSS = _svg('<path d="M4 4 L12 12 M12 4 L4 12"/>', "#c0392b")
ARROW2 = ('<svg class="gl" viewBox="0 0 20 16" xmlns="http://www.w3.org/2000/svg" fill="none" '
          'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
          '<path d="M1 6.2 H12 M1 9.8 H12 M11 3 L15.5 8 L11 13"/></svg>')

# Colour-emoji and bitmap-fallback glyphs become Type 3 fonts in Chrome's PDF; swap them for
# vector marks.  Applied to flowing text, tables, headings and code blocks alike.
GLYPHS: dict[str, str] = {
    "✅": TICK, "✓": TICK, "✔": TICK, "✔️": TICK,
    "❌": CROSS, "✗": CROSS, "✘": CROSS,
    "⏳": '<span class="mk-pend">&#9675;</span>',
    "⭐": _STAR, "★": _STAR,
    "\U0001f6a9": '<span class="sym sym-bad">▲</span>',
    "⚠️": '<span class="sym sym-bad">▲</span>',
    "⚠": '<span class="sym sym-bad">▲</span>',
    "⇒": ARROW2,
    "▔": _blk('<rect x="0" y="0" width="10" height="2"/>'),
    "▮": _blk('<rect x="2.5" y="1" width="5" height="14"/>'),
    "▚": _blk('<rect x="0" y="0" width="5" height="8"/><rect x="5" y="8" width="5" height="8"/>'),
}
for _ch, _cls in SEV.items():
    GLYPHS[_ch] = f'<span class="sev {_cls}"></span>'
_GLYPH_RE = re.compile("|".join(re.escape(k) for k in sorted(GLYPHS, key=len, reverse=True)))
_EMOJI_RANGE_RE = re.compile("[\U0001f300-\U0001faff☀-⛿✀-➿️]")
_PRE_RE = re.compile(r"<pre\b.*?</pre>", re.S)


def replace_glyphs(h: str, unmapped: set) -> str:
    """Swap emoji and bitmap-fallback glyphs for vector stand-ins (mermaid blocks are left alone)."""
    def flow(s: str) -> str:
        rest = _GLYPH_RE.sub("", re.sub(r"<[^>]+>", "", s))
        s = _GLYPH_RE.sub(lambda m: GLYPHS[m.group(0)], s)
        for m in _EMOJI_RANGE_RE.finditer(rest):
            if ord(m.group(0)) != 0xFE0F:
                unmapped.add(f"U+{ord(m.group(0)):04X}")
        return s

    out, pos = [], 0
    for m in _PRE_RE.finditer(h):
        out.append(flow(h[pos:m.start()]))
        blk = m.group(0)
        out.append(blk if blk.startswith('<pre class="mermaid"') else flow(blk))
        pos = m.end()
    out.append(flow(h[pos:]))
    return "".join(out)


_ALLOWED_TAGS = {"details", "summary", "br", "sub", "sup"}
_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9-]*)(?:\s[^<>]*)?/?>")


def sanitize_html(s: str) -> str:
    """Raw HTML: only details/summary/br/sub/sup survive (attributes dropped); the rest is shown as text."""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)

    def rep(m: re.Match) -> str:
        name = m.group(1).lower()
        if name in _ALLOWED_TAGS:
            return f"</{name}>" if m.group(0).startswith("</") else f"<{name}>"
        return esc(m.group(0))

    return _TAG_RE.sub(rep, s)


def caption_html(text: str, env: dict) -> str:
    """Captions live in a title= attribute, so re-apply the little markdown they use."""
    s = esc(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    if env.get("math_on"):
        def m(mm):
            env["math"] += 1
            return "\\(" + mm.group(1) + "\\)"
        s = re.sub(r"\$([^$]+)\$", m, s)
    return s


def resolve_link(href: str, ctx: dict) -> tuple[str | None, str]:
    """(new href | None for no href, extra class)."""
    if re.match(r"^(https?:|mailto:)", href, re.I):
        return href, ""
    base, _, frag = href.partition("#")
    ch: Chapter = ctx["chapter"]
    if not base:
        if frag:
            sid = ch.gh_map.get(frag)
            if sid:
                return f"#sec-{sid}", ""
        return href, ""
    name = Path(base.replace("\\", "/")).name
    low = name.lower()
    if low.endswith(".md"):
        target: Chapter | None = ctx["by_file"].get(low)
        if target is None:
            return None, "deadlink"
        if ctx["mode"] == "compendium":
            sid = target.gh_map.get(frag) if frag else None
            return (f"#sec-{sid}" if sid else f"#ch{target.chnum}"), ""
        if target is ch:
            sid = target.gh_map.get(frag) if frag else None
            return (f"#sec-{sid}" if sid else href), ""
        return f"{target.pdf_stem}.pdf", ""
    if base.startswith("figures/"):
        return href, ""
    return None, "deadlink"


# =====================================================================
# 4. The markdown-it instance
# =====================================================================
def make_md(tier: str, math: bool, extra_callouts: dict | None = None) -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True, "typographer": False, "linkify": False})
    md.enable(["table", "strikethrough"])
    extra = [(str(k).lower().strip(), re.sub(r"[^a-z0-9-]", "", str(v).lower()))
             for k, v in (extra_callouts or {}).items()]
    base_px = CODE_BASE_PX[tier]

    if math:
        md.use(dollarmath_plugin, double_inline=True)

        def m_inline(self, toks, i, o, env):
            env["math"] += 1
            return f'<span class="minline">\\({esc(toks[i].content)}\\)</span>'

        def m_block(self, toks, i, o, env):
            env["math"] += 1
            return f'<div class="mblock">\\[{esc(toks[i].content)}\\]</div>'

        md.add_render_rule("math_inline", m_inline)
        md.add_render_rule("math_inline_double", m_block)
        md.add_render_rule("math_block", m_block)
        md.add_render_rule("math_block_label", m_block)

    # ---- fences ----
    def r_fence(self, toks, i, o, env):
        tok = toks[i]
        lang = fence_lang(tok.info)
        code = tok.content.rstrip("\n")
        if lang == "mermaid":
            env["mermaid"] += 1
            return f'<pre class="mermaid">{esc(code)}</pre>\n'
        hl = highlight_code(code, lang)
        body = hl if hl is not None else esc(code)
        classes = ["code", "lang-" + re.sub(r"[^a-z0-9+-]", "", lang) .replace("+", "p")]
        if hl is not None:
            classes.append("hl")
        if code.count("\n") + 1 > 28:
            classes.append("long")
        style = ""
        longest = longest_line(code)
        if longest <= layout.MAX_SHRINK_COLS:
            avail = layout.CONTENT_PX - 28
            need = avail / (layout.MONO_EM * longest) if longest else base_px
            if need < base_px - 0.05:
                style = f' style="font-size:{max(5.6, need):.2f}px"'
        return f'<pre class="{" ".join(classes)}"{style}><code>{body}</code></pre>\n'

    md.add_render_rule("fence", r_fence)

    # ---- images / figures ----
    def r_image(self, toks, i, o, env):
        t = toks[i]
        src = t.attrGet("src") or ""
        alt = self.renderInlineAsText(t.children, o, env) if t.children else ""
        title = t.attrGet("title") or ""
        env["figures"].append(src)
        img = f'<img src="{esc_attr(src)}" alt="{esc_attr(alt)}">'
        if t.meta and t.meta.get("figure"):
            cap = f"<figcaption>{caption_html(title, env)}</figcaption>" if title else ""
            return f"<figure>{img}{cap}</figure>"
        return img

    md.add_render_rule("image", r_image)

    def rule_figures(state):
        toks = state.tokens
        for i in range(len(toks) - 2):
            if (toks[i].type == "paragraph_open" and toks[i + 1].type == "inline"
                    and toks[i + 2].type == "paragraph_close"):
                ch = toks[i + 1].children or []
                if len(ch) == 1 and ch[0].type == "image":
                    toks[i].hidden = True
                    toks[i + 2].hidden = True
                    ch[0].meta = {"figure": True}

    md.core.ruler.push("figures", rule_figures)

    # ---- links ----
    def r_link_open(self, toks, i, o, env):
        t = toks[i]
        ctx = env.get("link")
        href = t.attrGet("href") or ""
        if ctx:
            new, cls = resolve_link(href, ctx)
            if new is None:
                t.attrs = {k: v for k, v in t.attrs.items() if k != "href"}
            else:
                t.attrSet("href", new)
            if cls:
                t.attrJoin("class", cls)
        return self.renderToken(toks, i, o, env)

    md.add_render_rule("link_open", r_link_open)

    # ---- raw html whitelist ----
    md.add_render_rule("html_block", lambda self, toks, i, o, env: sanitize_html(toks[i].content))
    md.add_render_rule("html_inline", lambda self, toks, i, o, env: sanitize_html(toks[i].content))

    # ---- callouts ----
    def rule_callouts(state):
        toks = state.tokens
        n = len(toks)
        for i, t in enumerate(toks):
            if t.type != "blockquote_open":
                continue
            lead = ""
            if i + 2 < n and toks[i + 1].type == "paragraph_open" and toks[i + 2].type == "inline":
                ch = toks[i + 2].children or []
                while ch and ch[0].type == "text" and not ch[0].content.strip():
                    ch = ch[1:]                    # markdown-it emits an empty text token before a leading **
                if ch and ch[0].type == "strong_open":
                    parts = []
                    for c in ch[1:]:
                        if c.type == "strong_close":
                            break
                        parts.append(c.content)
                    lead = "".join(parts)
            cls = callout_class(lead, extra) if lead else ""
            if not cls:
                continue
            t.attrSet("class", f"cal {cls}")
            if cls == "meta":                      # one field per source line: keep them on separate lines
                depth = 0
                for j in range(i, n):
                    if toks[j].type == "blockquote_open":
                        depth += 1
                    elif toks[j].type == "blockquote_close":
                        depth -= 1
                        if depth == 0:
                            break
                    elif toks[j].type == "inline":
                        for c in toks[j].children or []:
                            if c.type == "softbreak":
                                c.type = "hardbreak"
                                c.tag = "br"

    md.core.ruler.push("callouts", rule_callouts)

    # ---- heading ids ----
    def rule_heading_ids(state):
        toks = state.tokens
        for i, t in enumerate(toks):
            if t.type == "heading_open" and t.tag in ("h2", "h3") and i + 1 < len(toks):
                text = toks[i + 1].content
                m = re.match(r"§\s?(\S+)\s", text + " ")
                t.attrSet("id", "sec-" + m.group(1) if m else gh_anchor(strip_md(text)))

    md.core.ruler.push("heading_ids", rule_heading_ids)
    return md


# =====================================================================
# 5. Post-processing: tables
# =====================================================================
_SECNO = re.compile(r'(<h([23]) id="sec-[^"]+">)§(\S+) ')


def mark_secno(h: str) -> str:
    """Wrap a heading's ``§N.k`` in ``<span class="secno">`` so it can take its own colour. The text is unchanged,
    so the PDF still reads ``§N.k Title`` for the audit and the bookmarks."""
    return _SECNO.sub(lambda m: f'{m.group(1)}<span class="secno">§{m.group(3)}</span> ', h)


def fix_tables(h: str) -> str:
    """Wrap tables in ``.tw`` and give very wide ones a smaller font."""
    def repl(m: re.Match) -> str:
        tbl = m.group(0)
        head = tbl.split("</thead>")[0]
        ncols = len(re.findall(r"<th[ >]", head)) or 1
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tbl, re.S)
        longest = max((len(re.sub(r"<[^>]+>", "", c).strip()) for c in cells), default=0)
        cls = ""
        if ncols >= 8 or longest > 150:
            cls = ' class="tiny"'
        elif ncols >= 6:
            cls = ' class="small"'
        return '<div class="tw">' + tbl.replace("<table>", f"<table{cls}>", 1) + "</div>"

    return re.sub(r"<table>.*?</table>", repl, h, flags=re.S)


# =====================================================================
# 6. Chapter, cover, contents, document shell
# =====================================================================
META_LABELS = [
    ("session", "Session"), ("date", "Date"), ("duration_min", "Duration"), ("sources", "Sources"),
    ("instructor_code", "Instructor code"), ("revision_minutes", "Revision time"),
    ("prerequisites", "Prerequisites"), ("companion_of", "Companion of"),
    ("verified_as_of", "Verified as of"),
]


def meta_block(ch: Chapter, md: MarkdownIt, env: dict, tier: str, counterpart: tuple[str, str] | None) -> str:
    """The visible meta block, rendered from front matter (legacy notes have none)."""
    meta = ch.meta
    if not meta and not counterpart and not (ch.prev or ch.next):
        return ""

    def inline(v) -> str:
        return md.renderInline(str(v), env)

    rows: list[tuple[str, str]] = []
    for key, label in META_LABELS:
        v = meta.get(key)
        if v in (None, "", []):
            continue
        if key == "duration_min":
            val = inline(f"{v} min")
        elif key == "revision_minutes":
            val = inline(f"~{v} min")
        elif isinstance(v, (list, tuple)):
            val = "<br>".join(inline(x) for x in v)
        else:
            val = inline(v)
        rows.append((label, val))
    if counterpart:
        href, label = counterpart
        rows.append(("Also available", f'<span class="counter"><a href="{esc_attr(href)}">{esc(label)}</a></span>'))
    ctx = (env.get("link") or {})
    for label, other in (("Previous", ch.prev), ("Next", ch.next)):
        if other is None:
            continue
        href = f"#ch{other.chnum}" if ctx.get("mode") == "compendium" else f"{other.pdf_stem}.pdf"
        rows.append((label, f'<a href="{esc_attr(href)}">{esc(other.num)} · {esc(other.title)}</a>'))
    if not rows:
        return ""
    body = "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in rows)
    return f'<dl class="metablock">{body}</dl>'


_INLINE_MARK_RE = re.compile(r"<!--\s*/?(?:sweep|pending-ref):[\w.:-]+\s*-->")
_MARK_LINE_RE = re.compile(r"^\s*<!--\s*/?(?:sweep|pending-ref):[\w.:-]+\s*-->\s*$")


def strip_inline_markers(text: str) -> str:
    """Drop ``sweep`` / ``pending-ref`` marker comments that share a line with prose (PLAN 18).

    CommonMark treats a line that STARTS with ``<!--`` as a raw HTML block, so an inline marker at the start of a
    paragraph would turn the whole line into unrendered text. Own-line markers are left alone (they are harmless
    HTML blocks); code fences are never touched.
    """
    out, fenced = [], False
    for ln in text.split("\n"):
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", ln):
            fenced = not fenced
        out.append(ln if fenced or "<!--" not in ln or _MARK_LINE_RE.match(ln) else _INLINE_MARK_RE.sub("", ln))
    return "\n".join(out)


_DETAILS_OPEN_RE = re.compile(r"^(\s*)(<details>\s*(?:<summary>.*?</summary>)?)(.*)$")


def open_details_blocks(text: str) -> str:
    """Give a ``<details>`` that starts its own line blank lines inside, so its answer is rendered as markdown.

    A line that starts with ``<details>`` opens a CommonMark HTML block, which runs to the next blank line with
    no markdown parsed: ``**Answer.**`` came out with its asterisks. Splitting the opening and closing tags onto
    their own lines, with blank lines between, makes the answer an ordinary paragraph inside the block. A
    ``<details>`` in the middle of a line (after the question text) is inline HTML and already fine.
    """
    out: list[str] = []
    fenced, ind = False, None
    for ln in text.split("\n"):
        if re.match(r"^\s{0,3}(`{3,}|~{3,})", ln):
            fenced = not fenced
        if fenced:
            out.append(ln)
            continue
        m = _DETAILS_OPEN_RE.match(ln) if ind is None else None
        if m:
            ind = m.group(1)
            out += [ind + m.group(2), ""]
            ln = ind + m.group(3).strip()
            if not m.group(3).strip():
                continue
        if ind is not None and ln.rstrip().endswith("</details>"):
            body = ln.rstrip()[:-len("</details>")]
            if body.strip():
                out += [body.rstrip(), ""]
            out.append(ind + "</details>")
            ind = None
            continue
        out.append(ln)
    return "\n".join(out)


def render_chapter(ch: Chapter, md: MarkdownIt, ctx: dict, tier: str,
                   counterpart: tuple[str, str] | None = None) -> tuple[str, dict]:
    """(section html, info). ``info``: math, mermaid, figures[], unmapped[]."""
    env = {"math": 0, "mermaid": 0, "figures": [], "unmapped": set(),
           "link": dict(ctx, chapter=ch), "math_on": bool(ctx.get("math"))}
    h = md.render(open_details_blocks(strip_inline_markers(ch.md)), env)
    h = mark_secno(fix_tables(h))
    h = replace_glyphs(h, env["unmapped"])
    h = h.replace("<details>", "<details open>")
    meta_html = meta_block(ch, md, env, tier, counterpart)
    meta_html = replace_glyphs(meta_html, env["unmapped"])
    head = (
        f'<section class="chapter" id="ch{ch.chnum}">'
        f'<header class="chead"><div class="chno"><span>{esc(ch.label)}</span>'
        f'<span class="tier">{esc(TIER_KICKER[tier])}</span></div>'
        f'<div class="ctitle">{esc(ch.title)}</div></header>{meta_html}{h}</section>'
    )
    info = {
        "math": env["math"], "mermaid": env["mermaid"],
        "figures": list(dict.fromkeys(env["figures"])),
        "unmapped": sorted(env["unmapped"]),
    }
    return head, info


def build_cover(profile: dict, tier: str, chapters: list[Chapter]) -> str:
    rows = []
    for ch in chapters:
        label = "Index" if ch.is_index else ch.num
        rows.append(f"<tr><td>{esc(label)}</td><td>{esc(ch.title)}</td>"
                    f"<td>{len(ch.sections)} sections</td></tr>")
    if tier == "basic":
        lo, hi = (profile.get("tiers", {}).get("basic", {}).get("minutes") or [20, 45])[:2]
        sub = "Class revision notes"
        blurb = (f"Revision material, not a textbook: one note per lecture, each re-readable in "
                 f"{lo}–{hi} minutes.")
    else:
        sub = "Deep-dive notes"
        blurb = ("From first principles to production reality: worked examples, generated figures "
                 "and cited sources.")
    cover = profile.get("cover") or {}                # optional profile override of the cover text
    sub, blurb = cover.get("subtitle") or sub, cover.get("blurb") or blurb
    inst = profile.get("institution") or ""
    meta = esc(inst) + ("<br>" if inst else "") + esc(blurb)
    return (
        '<section class="cover"><div class="cv-rule"></div>'
        f'<div class="cv-kicker">{esc(profile.get("short") or profile["title"])}</div>'
        f'<div class="cv-tier">{esc(cover.get("label") or TIER_KICKER[tier])}</div>'
        f'<h1 class="cv-title">{esc(profile["title"])}</h1>'
        f'<div class="cv-sub">{esc(sub)}</div>'
        f'<div class="cv-meta">{meta}</div>'
        '<div class="cv-box"><div class="cv-box-h">In this document</div>'
        f'<table class="cv-tbl">{"".join(rows)}</table></div>'
        '<div class="cv-foot">Section numbers are stable across the PDF, the markdown notes and the '
        'contents: section 2.4 is the same section everywhere. Search the PDF for the section sign '
        'followed by 2.4 to jump to its heading, or for 2.4 alone to find every reference to it.</div>'
        '</section>'
    )


def build_contents(chapters: list[Chapter]) -> str:
    parts = ['<section class="chapter frontsec" id="contents">'
             '<header class="chead"><div class="chno"><span>Navigation</span></div>'
             '<div class="ctitle">Contents</div></header>']
    for ch in chapters:
        parts.append(
            f'<div class="toc-ch"><a href="#ch{ch.chnum}">'
            f'<span class="toc-chn">{esc(ch.label)}</span>'
            f'<span class="toc-t">{esc(ch.title)}</span></a>'
            f'<span class="pn" data-ref="c:{ch.chnum}">{layout.PLACEHOLDER}</span></div>'
        )
        for s in ch.sections:
            if s["level"] != 2:
                continue
            parts.append(
                f'<div class="toc-s"><a href="#sec-{s["sid"]}">'
                # deliberately NO section sign here: "§2.4" must occur only in the real heading,
                # so page resolution (SEC_RE) cannot match the contents page itself
                f'<span class="toc-sn">{s["sid"]}</span>'
                f'<span class="toc-t">{esc(s["text"])}</span></a>'
                f'<span class="pn" data-ref="s:{s["sid"]}">{layout.PLACEHOLDER}</span></div>'
            )
    parts.append("</section>")
    return "".join(parts)


_READY_JS = r"""
window.__ready = false; window.__mermaid_errors = 0; window.__js_errors = [];
(async function () {
  try {
    if (window.renderMathInElement) {
      renderMathInElement(document.body, {
        delimiters: [
          {left: "\\[", right: "\\]", display: true},
          {left: "\\(", right: "\\)", display: false}
        ],
        throwOnError: false, strict: false, trust: true, errorColor: "#cc0000"
      });
    }
  } catch (e) { window.__js_errors.push("katex: " + e); }
  try {
    if (window.mermaid) {
      mermaid.initialize({
        startOnLoad: false, theme: "neutral", securityLevel: "loose",
        flowchart: { htmlLabels: true, curve: "basis", useMaxWidth: false,
                     nodeSpacing: 34, rankSpacing: 40, padding: 6 },
        themeVariables: { fontFamily: "@@MFONT@@", fontSize: "14px" }
      });
      const MAXW = 660, MAXH = 880;
      const nodes = Array.prototype.slice.call(document.querySelectorAll("pre.mermaid"));
      for (const n of nodes) {
        try {
          await mermaid.run({ nodes: [n] });
          const svg = n.querySelector("svg");
          if (!svg || svg.getAttribute("aria-roledescription") === "error" ||
              n.querySelector(".error-icon, .error-text")) { window.__mermaid_errors++; continue; }
          // Mermaid sizes SVGs to their content, which can far exceed one page: scale down (never up)
          let w, h;
          const vb = svg.viewBox && svg.viewBox.baseVal;
          if (vb && vb.width) { w = vb.width; h = vb.height; }
          else { const b = svg.getBBox(); w = b.width; h = b.height; }
          const s = Math.min(MAXW / w, MAXH / h, 1);
          svg.removeAttribute("style");
          svg.setAttribute("width", (w * s).toFixed(1) + "px");
          svg.setAttribute("height", (h * s).toFixed(1) + "px");
          svg.style.maxWidth = "none";
        } catch (e) { window.__mermaid_errors++; }
      }
    }
  } catch (e) { window.__js_errors.push("mermaid: " + e); }
  try { await document.fonts.ready; } catch (e) {}
  window.__ready = true;
})();
"""


def wrap_document(title: str, body: str, css: str, *, katex: dict | None = None,
                  mermaid_js: str | None = None) -> str:
    """The self-contained HTML document. KaTeX / Mermaid are inlined only when passed."""
    head = [f"<meta charset='utf-8'><title>{esc(title)}</title><style>{css}</style>"]
    if katex:
        head.append(f"<style>{katex['css']}</style>")
    scripts = []
    if katex:
        scripts.append(f"<script>{script_safe(katex['js'])}</script>")
        scripts.append(f"<script>{script_safe(katex['autorender'])}</script>")
    if mermaid_js:
        scripts.append(f"<script>{script_safe(mermaid_js)}</script>")
    if katex or mermaid_js:
        mfont = FONT_SANS.replace('"', "").replace("'", "")
        scripts.append(f"<script>{_READY_JS.replace('@@MFONT@@', mfont)}</script>")
    return (f"<!doctype html><html lang='en'><head>{''.join(head)}</head>"
            f"<body>{body}{''.join(scripts)}</body></html>")
