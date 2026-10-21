"""Print CSS for both tiers.

Basic    = the cpp look: sans body (9.4 pt), compact, ASCII-friendly.
Advanced = the CV look: serif body, sans headings, denser type (the CV notes are set at a
           10.2 px root, i.e. 7.65 pt, which is what the existing CV PDFs use).

Both are branded from the profile (``branding.accent`` / ``accent2``) and use cross-OS font stacks.
``system-ui`` is deliberately not used: it can resolve to variable fonts that Chrome embeds badly.
"""
from __future__ import annotations

import re

from .layout import PAGE_MARGIN_CSS

FONT_SANS = '"Segoe UI","Helvetica Neue",Helvetica,Arial,"DejaVu Sans","Liberation Sans",sans-serif'
FONT_MONO = 'Consolas,"Cascadia Mono",Menlo,"DejaVu Sans Mono","Liberation Mono",monospace'
FONT_SERIF = 'Cambria,Georgia,"DejaVu Serif",serif'

# tier tokens used by markdown.py (code sizing) -- keep in sync with the CSS below
CODE_BASE_PX = {"basic": 7.7 * 96 / 72, "advanced": 8.9}

TIER_LABEL = {"basic": "Basic", "advanced": "Advanced"}
TIER_KICKER = {"basic": "BASIC · Revision", "advanced": "ADVANCED · Deep dive"}

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _color(v, default: str) -> str:
    v = str(v or "").strip()
    return v if _COLOR_RE.match(v) else default


# ---------------------------------------------------------------- shared ---
COMMON = r"""
@page { size: A4; margin: @@PAGE_MARGIN@@; }
:root{
  --ink:#14181d; --muted:#586374; --rule:#d2d8e1; --rule2:#e7ebf2;
  --accent:@@ACCENT@@; --accent2:@@ACCENT2@@; --warn:#b4331f; --ok:#1c6b3f;
  --code-bg:#f6f8fa; --code-bd:#e1e7ee;
}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact; print-color-adjust:exact}
body{margin:0; color:var(--ink); background:#fff; text-rendering:optimizeLegibility}
a{color:var(--accent); text-decoration:none}
a.deadlink{color:inherit}
hr{border:0;border-top:1px solid var(--rule2);margin:1.2em 0}
sup,sub{line-height:0}
img{max-width:100%}

/* ---- cover (compendium) ---- */
.cover{height:262mm;display:flex;flex-direction:column;justify-content:center;
  page-break-after:always;padding:0 4mm;font-family:@@SANS@@}
.cv-rule{height:5px;background:var(--accent);width:74mm;margin-bottom:9mm}
.cv-rule::after{content:'';display:block;height:5px;width:26mm;margin-left:48mm;background:var(--accent2)}
.cv-kicker{font-size:9pt;letter-spacing:.20em;text-transform:uppercase;color:var(--muted);margin-bottom:5mm}
.cv-tier{display:inline-block;font-size:8pt;letter-spacing:.16em;text-transform:uppercase;font-weight:650;
  color:var(--accent2);border:1px solid var(--accent2);padding:.6mm 2.4mm;margin-bottom:7mm;align-self:flex-start}
.cv-title{font-size:38pt;line-height:1.04;margin:0;font-weight:680;letter-spacing:-.018em}
.cv-sub{font-size:18pt;color:var(--accent);margin-top:3mm;font-weight:600}
.cv-meta{margin-top:6mm;color:var(--muted);font-size:9.5pt;line-height:1.65;max-width:125mm}
.cv-box{margin-top:11mm;border:1px solid var(--rule);border-left:3px solid var(--accent);
  padding:5mm 6mm;background:#fbfcfd;max-width:152mm}
.cv-box-h{font-size:8pt;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin-bottom:2.5mm}
.cv-tbl{width:100%;border-collapse:collapse;font-size:9pt}
.cv-tbl td{padding:1.4mm 0;border:0;vertical-align:top}
.cv-tbl td:first-child{white-space:nowrap;width:1%;font-family:@@MONO@@;color:var(--accent)}
.cv-tbl td:nth-child(2){padding-left:6mm;width:100%}
.cv-tbl td:last-child{text-align:right;white-space:nowrap;color:var(--muted);font-size:8pt}
.cv-foot{margin-top:auto;padding-top:10mm;color:var(--muted);font-size:8pt;max-width:150mm}

/* ---- chapters ---- */
.chapter{page-break-before:always}
.chapter:first-of-type{page-break-before:auto}
.chead{margin:0 0 5mm;padding-bottom:3mm;border-bottom:2.5px solid var(--accent);
  display:flex;flex-direction:column;gap:1.5mm;font-family:@@SANS@@}
.chno{font-size:8.6pt;text-transform:uppercase;color:var(--accent);font-weight:680;letter-spacing:.08em;
  display:flex;justify-content:space-between;align-items:baseline}
.chno .tier{font-size:7.4pt;letter-spacing:.14em;color:var(--accent2);font-weight:650}
.ctitle{font-size:19pt;font-weight:660;line-height:1.14;letter-spacing:-.012em}
h1{font-family:@@SANS@@}

/* ---- front-matter meta block ---- */
dl.metablock{margin:0 0 4.5mm;padding:2mm 3.2mm;display:grid;grid-template-columns:max-content 1fr;
  gap:.7mm 4mm;font-family:@@SANS@@;font-size:8.1pt;line-height:1.4;color:#48525f;
  background:#f7f9fb;border-left:3px solid #c3ccd6;break-inside:avoid}
dl.metablock dt{font-weight:650;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-size:7pt;
  padding-top:.25mm}
dl.metablock dd{margin:0}
dl.metablock dd code{font-size:7.6pt}
dl.metablock dd p{margin:0}
dl.metablock .counter a{font-weight:650}

/* ---- code highlighting (pygments, scoped) ---- */
.hl .c,.hl .c1,.hl .cm,.hl .cs,.hl .ch{color:#6b7a88;font-style:italic}
.hl .cp,.hl .cpf{color:#8a5a12;font-weight:600;font-style:normal}
.hl .k,.hl .kn,.hl .kc,.hl .kd,.hl .kp,.hl .kr{color:#0b4f9c;font-weight:600}
.hl .kt{color:#0f6b6b}
.hl .s,.hl .s1,.hl .s2,.hl .sb,.hl .sc,.hl .sd,.hl .sh,.hl .si,.hl .sx,.hl .sr,.hl .ss{color:#a03b28}
.hl .se{color:#a03b28;font-weight:600}
.hl .m,.hl .mi,.hl .mf,.hl .mh,.hl .mo,.hl .mb,.hl .il{color:#7a3fa0}
.hl .nb,.hl .bp{color:#0b7285}
.hl .nf,.hl .fm,.hl .nd{color:#8a5a00}
.hl .nc,.hl .nn{color:#0f5132;font-weight:600}
.hl .o,.hl .ow{color:#41505e}
.hl .gp{color:#8a96a3}
.hl .err{color:inherit;background:none;border:0}

/* ---- callouts: blockquote.cal.<class> ---- */
blockquote{margin:2.6mm 0;padding:1.9mm 3mm;border-left:3px solid var(--rule);background:#fafbfc;
  break-inside:avoid}
blockquote p{margin:.25em 0}
blockquote blockquote{margin:.4em 0}
blockquote.cal.note{border-left-color:var(--accent);background:#f1f6fa}
blockquote.cal.gotcha{border-left-color:#c8801a;background:#fdf8f0}
blockquote.cal.ub{border-left-color:#b4331f;background:#fdf2f0}
blockquote.cal.tip{border-left-color:var(--ok);background:#f2f9f5}
blockquote.cal.reality{border-left-color:#5c4b9a;background:#f7f5fc}
blockquote.cal.correction{border-left-color:var(--warn);background:#fdf3f1}
blockquote.cal.recon{border-left-color:#8a5a12;background:#fbf7f0;border-left-style:dashed}
blockquote.cal.maths{border-left-color:var(--accent);background:#f1f5f9}
blockquote.cal.paper{border-left-color:var(--accent);background:#f6f8fb}
blockquote.cal.jargon{border-left-color:#0b7285;background:#eff8fa}
blockquote.cal.gapfill{border-left-color:#0e7c86;background:#effafb}
blockquote.cal.repo{border-left-color:#c3ccd6;background:#f7f9fb;color:#48525f;font-size:8.5pt;border-left-style:dotted}
blockquote.cal.buildson{border-left-color:#6b8fa8;background:#f4f8fb;font-size:9pt;border-left-style:dotted}
blockquote.cal.meta{border-left-color:#c3ccd6;background:#f7f9fb;color:#48525f;font-size:8.5pt}

/* ---- details (answers); the builder forces them open ---- */
details{margin:.4em 0 .9em;padding:1.6mm 2.6mm;background:#f4f8f4;border:.6px solid #d4e4d4;
  border-left:3px solid #2e7d4f;break-inside:avoid}
details>summary{font-family:@@SANS@@;font-weight:650;font-size:7.8pt;letter-spacing:.07em;
  text-transform:uppercase;color:#2e7d4f;margin-bottom:1mm;list-style:none}
details>summary::-webkit-details-marker{display:none}
details p:first-of-type{margin-top:.15em}
details p:last-child{margin-bottom:.15em}

/* ---- math ---- */
.mblock{margin:.6em 0;text-align:center;break-inside:avoid}
.katex{font-size:1.03em}
.katex-display{margin:.45em 0 !important}
.katex-display>.katex{white-space:normal}

/* ---- mermaid ---- */
pre.mermaid{background:none;border:0;border-left:0;padding:0;text-align:center;margin:1em 0;
  break-inside:avoid;white-space:normal;font-family:@@SANS@@}
pre.mermaid svg{max-width:100%;height:auto}
pre.mermaid .nodeLabel,pre.mermaid .edgeLabel,pre.mermaid .label{font-family:@@SANS@@ !important}

/* ---- figures ---- */
figure{margin:.9em 0;padding:0;text-align:center;break-inside:avoid}
figure img{max-width:100%;max-height:215mm;width:auto;height:auto;display:block;margin:0 auto;border:1px solid var(--rule);
  border-radius:2px;background:#fff}
figcaption{margin-top:.35em;font-family:@@SANS@@;font-size:8pt;line-height:1.42;color:var(--muted);
  text-align:left;padding:0 2px}
figcaption b,figcaption strong{color:var(--ink)}

/* ---- glyph stand-ins (keep the PDF free of Type 3 colour-emoji glyphs) ---- */
.sev{display:inline-block;width:.62em;height:.62em;border-radius:50%;vertical-align:-.03em;
  margin-right:.12em;border:.4px solid rgba(0,0,0,.18)}
.sev-r{background:#c0392b}.sev-o{background:#e08e0b}.sev-y{background:#e9c33c}
.sym{font-family:"Segoe UI Symbol","Segoe UI","DejaVu Sans",sans-serif;font-weight:600}
.sym-ok{color:#137a45}.sym-bad{color:#b4331f}.sym-star{color:#c07f10}
.mk-pend{color:#9aa3ad}
svg.gl{display:inline-block;width:.78em;height:.78em;vertical-align:-.08em;color:inherit}
pre svg.gl{width:1ch;height:1em;vertical-align:-.18em}
svg.gl.blk{width:1ch;height:1.02em;vertical-align:-.2em}

/* ---- contents ---- */
.frontsec .chead{border-bottom-color:var(--accent2)}
.toc-ch,.toc-s{display:flex;align-items:baseline;gap:.4em;break-inside:avoid;font-family:@@SANS@@}
.toc-ch{margin-top:3mm;font-weight:640;font-size:9.8pt}
.toc-s{font-size:8.7pt;color:#3c4652;padding-left:7mm}
.toc-ch a,.toc-s a{display:flex;gap:.55em;flex:1;min-width:0;color:inherit;
  border-bottom:1px dotted var(--rule);align-items:baseline}
.toc-chn{color:var(--accent);white-space:nowrap}
.toc-sn{color:var(--muted);font-family:@@MONO@@;font-size:7.9pt;min-width:10mm;flex-shrink:0}
.toc-t{overflow:hidden}
.pn{display:inline-block;min-width:6.4mm;text-align:right;font-variant-numeric:tabular-nums;
  font-family:@@MONO@@;font-size:8pt;color:var(--muted);flex-shrink:0}
"""

# ----------------------------------------------------------------- basic ---
BASIC = r"""
body{font-family:@@SANS@@;font-size:9.4pt;line-height:1.5}
p{margin:.42em 0}
strong,b{font-weight:650}
h2{font-family:@@SANS@@;font-size:12.4pt;font-weight:650;margin:6.5mm 0 2mm;padding-bottom:1.2mm;
  border-bottom:1px solid var(--rule);break-after:avoid;line-height:1.24}
h3{font-family:@@SANS@@;font-size:10.4pt;font-weight:650;margin:4.5mm 0 1.4mm;color:#233748;
  break-after:avoid;line-height:1.28}
h4{font-family:@@SANS@@;font-size:9.5pt;font-weight:650;margin:3.2mm 0 1mm;break-after:avoid}

.tw{margin:2.6mm 0}
table{border-collapse:collapse;width:100%;font-size:8.4pt;line-height:1.4}
table.small{font-size:7.9pt}
table.tiny{font-size:7.2pt}
thead{display:table-header-group}
tr{break-inside:avoid;page-break-inside:avoid}
th{background:#e9eff5;text-align:left;font-weight:650;border:1px solid var(--rule);
  padding:1.5mm 2mm;vertical-align:top}
td{border:1px solid var(--rule);padding:1.5mm 2mm;vertical-align:top;word-break:normal;
  overflow-wrap:break-word}
td:first-child,th:first-child{hyphens:none}
tbody tr:nth-child(even){background:#fafbfc}

pre.code{background:var(--code-bg);border:1px solid var(--code-bd);border-left:2.5px solid #9db4cb;
  padding:2.4mm 3mm;margin:2.6mm 0;font-family:@@MONO@@;font-size:7.7pt;line-height:1.42;
  white-space:pre;overflow:hidden;break-inside:avoid}
pre.code.long{break-inside:auto;orphans:3;widows:3}
pre.code code{font:inherit;background:none;border:0;padding:0;white-space:pre}
code{font-family:@@MONO@@;font-size:8.4pt;background:#eef2f6;border:1px solid #e1e7ee;
  border-radius:2px;padding:0 .2em}
pre.code.lang-text{border-left-color:#c5cfda;background:#f8f9fa}
pre.code.lang-bash,pre.code.lang-sh,pre.code.lang-console{border-left-color:#8aa08b}

ul,ol{margin:.42em 0;padding-left:1.35em}
li{margin:.16em 0}
li>ul,li>ol{margin:.1em 0}
ol>li{margin:.28em 0}
figcaption{font-size:8pt}
"""

# -------------------------------------------------------------- advanced ---
ADVANCED = r"""
html{font-size:10.2px}
body{font-family:@@SERIF@@;font-size:1rem;line-height:1.52;hyphens:none;word-wrap:break-word}
p{margin:.52em 0}
strong{font-weight:650;color:#0f1a26}
.chead{margin-bottom:12px}
.ctitle{font-size:20.5px;line-height:1.2;font-weight:700;letter-spacing:-.015em;color:#12233a}
.chno{font-size:8.4px}
h2,h3,h4,h5,h6{font-family:@@SANS@@;font-weight:650;color:#12233a;line-height:1.25;
  break-after:avoid-page;margin:1.35em 0 .5em}
h2{font-size:14.4px;border-bottom:1px solid var(--rule);padding-bottom:3px;margin-top:1.7em;
  letter-spacing:-.01em}
h3{font-size:12.1px}
h4{font-size:10.9px;color:#2c3c50}
h5,h6{font-size:10.2px;color:var(--muted)}
h2+h3,h3+h4{margin-top:.7em}
a{border-bottom:.4px solid #b9c9da}
.toc-ch a,.toc-s a{border-bottom:1px dotted var(--rule)}
.cover a,figure a{border-bottom:0}

ul,ol{margin:.5em 0;padding-left:1.35em}
li{margin:.2em 0}
li>p{margin:.2em 0}

code{font-family:@@MONO@@;font-size:.86em;background:#f4f6f8;padding:.08em .2em;border-radius:2.5px;
  border:.4px solid #e3e7ec;white-space:pre-wrap;word-break:break-word}
/* the side padding of an inline code box is not counted when Chrome breaks a line at it, so it can hang past
   the margin; keep padding + border under the audit's 3pt slack (at .3em it was 3.2pt) */
pre.code{font-family:@@MONO@@;background:#f4f6f8;border:.6px solid #e1e6ec;border-left:2.5px solid #b8c6d4;
  border-radius:3px;padding:7px 9px;margin:.7em 0;font-size:8.9px;line-height:1.42;
  overflow:hidden;white-space:pre;break-inside:avoid-page}
pre.code code{background:none;border:0;padding:0;font-size:inherit;white-space:pre}
pre.code[style]{line-height:1.3}
pre.code.long{break-inside:auto;orphans:3;widows:3}

.tw{margin:0}
table{border-collapse:collapse;width:100%;margin:.75em 0;font-size:9.1px;font-family:@@SANS@@;
  break-inside:auto}
table.small{font-size:8.4px}
table.tiny{font-size:7.6px}
th,td{border:.5px solid #dbe0e6;padding:3.2px 5px;text-align:left;vertical-align:top;
  overflow-wrap:break-word}
thead th{background:#eef2f6;font-weight:650;color:#12233a}
tbody tr:nth-child(even){background:#fafbfc}
tr{break-inside:avoid-page}
thead{display:table-header-group}
table code{font-size:.92em;padding:0 .18em}

blockquote{margin:.75em 0;padding:7px 11px;border-radius:0 3px 3px 0;break-inside:avoid-page}
blockquote p{margin:.28em 0}
details{border-radius:0 3px 3px 0}
details>summary{font-size:8.6px}
figcaption{font-size:8.1px}
dl.metablock{font-size:8.6px}
dl.metablock dt{font-size:7.4px}
"""


# ------------------------------------------------------------------ colour ---
# One colour layer over both tiers. Colour carries meaning, so the eye can sort a page at a glance: section
# headings in the accent, each callout kind its own hue (the same hue on its border, tint and label), table
# heads tinted, and a code block's left edge telling its language. Tints stay light enough to print in grey.
COLOR = r"""
:root{
  --accent-t:color-mix(in srgb, var(--accent) 9%, #fff);
  --accent-t2:color-mix(in srgb, var(--accent) 22%, #fff);
  --accent2-t:color-mix(in srgb, var(--accent2) 10%, #fff);
}
h2{color:var(--accent);border-bottom:0;border-left:4px solid var(--accent);padding:1.2mm 0 1.2mm 2.6mm;
  background:linear-gradient(to right, var(--accent-t) 0%, var(--accent-t) 55%, #fff 100%);border-radius:0 2px 2px 0}
h3{color:color-mix(in srgb, var(--accent) 78%, #000)}
h3::before{content:'';display:inline-block;width:.42em;height:.42em;margin-right:.45em;border-radius:1px;
  background:var(--accent2);vertical-align:.12em}
h4{color:var(--accent2)}
.secno{color:var(--accent2);font-weight:700;margin-right:.1em}
h2 .secno{color:var(--accent2)}
strong,b{color:color-mix(in srgb, var(--accent) 55%, #10161d)}

/* tables: accent header, faint accent banding */
thead th,th{background:var(--accent-t2);color:color-mix(in srgb, var(--accent) 85%, #000);
  border-bottom:1.5px solid var(--accent)}
tbody tr:nth-child(even){background:var(--accent-t)}

/* callouts: border, tint and label share one hue per kind */
blockquote.cal{border-left-width:4px;border-radius:0 3px 3px 0}
blockquote.cal>p:first-child>strong:first-child{text-transform:none;letter-spacing:.01em}
blockquote.cal.note{border-left-color:#2b6cb0;background:#eaf2fb}
blockquote.cal.note>p:first-child>strong:first-child{color:#1f5592}
blockquote.cal.gotcha{border-left-color:#d97706;background:#fff4e2}
blockquote.cal.gotcha>p:first-child>strong:first-child{color:#a85600}
blockquote.cal.ub{border-left-color:#c53030;background:#fdeaea}
blockquote.cal.ub>p:first-child>strong:first-child{color:#a52222}
blockquote.cal.tip{border-left-color:#2f855a;background:#e6f6ec}
blockquote.cal.tip>p:first-child>strong:first-child{color:#22673f}
blockquote.cal.reality{border-left-color:#6b46c1;background:#f1ecfc}
blockquote.cal.reality>p:first-child>strong:first-child{color:#553099}
blockquote.cal.correction{border-left-color:#c53030;background:#fdecea}
blockquote.cal.correction>p:first-child>strong:first-child{color:#a52222}
blockquote.cal.recon{border-left-color:#b7791f;background:#fbf3e4}
blockquote.cal.recon>p:first-child>strong:first-child{color:#8a5a12}
blockquote.cal.jargon{border-left-color:#0b7285;background:#e6f5f8}
blockquote.cal.jargon>p:first-child>strong:first-child{color:#0b5d6c}
blockquote.cal.gapfill{border-left-color:#0e8f8f;background:#e4f6f5}
blockquote.cal.gapfill>p:first-child>strong:first-child{color:#0b6e6e}
blockquote.cal.maths,blockquote.cal.paper{border-left-color:var(--accent);background:var(--accent-t)}
blockquote.cal.repo{border-left-color:#8a9bb0;background:#f3f6f9}
blockquote.cal.buildson{border-left-color:#4a7fa8;background:#eef5fa}
blockquote.cal.buildson>p:first-child>strong:first-child{color:#2f5f86}

/* details (answers) */
details{background:#eef8f1;border-left-color:#2f855a}

/* code: the language colour (--lc) marks the left edge and tints the block */
pre.code{--lc:#7f95ad;border-color:color-mix(in srgb, var(--lc) 22%, #fff);border-left:4.5px solid var(--lc);
  background:color-mix(in srgb, var(--lc) 6%, #fcfdfe)}
pre.code.lang-bash,pre.code.lang-sh,pre.code.lang-shell,pre.code.lang-console,pre.code.lang-zsh{--lc:#2f855a}
pre.code.lang-powershell,pre.code.lang-ps1,pre.code.lang-pwsh,pre.code.lang-bat,pre.code.lang-cmd{--lc:#2b6cb0}
pre.code.lang-yaml,pre.code.lang-yml{--lc:#805ad5}
pre.code.lang-python,pre.code.lang-py{--lc:#3b6ea5}
pre.code.lang-dockerfile,pre.code.lang-docker{--lc:#0b7285}
pre.code.lang-json,pre.code.lang-toml,pre.code.lang-ini{--lc:#d97706}
pre.code.lang-hcl,pre.code.lang-terraform,pre.code.lang-tf{--lc:#6b46c1}
pre.code.lang-javascript,pre.code.lang-js,pre.code.lang-typescript,pre.code.lang-ts{--lc:#b7791f}
pre.code.lang-go,pre.code.lang-java,pre.code.lang-c,pre.code.lang-cpp,pre.code.lang-rust{--lc:#c05621}
pre.code.lang-nginx,pre.code.lang-conf,pre.code.lang-xml,pre.code.lang-html{--lc:#9c4221}
pre.code.lang-text{--lc:#a0aec0;background:#f7f8fa}
/* tokens the base sheet leaves plain: YAML/HTML keys, attributes, variables, constants, diff lines */
.hl .nt{color:#0b4f9c;font-weight:600}
.hl .na{color:#6b2fa0}
.hl .nv,.hl .vg,.hl .vi,.hl .vc{color:#a14a00}
.hl .no,.hl .nl{color:#0b7285}
.hl .l,.hl .ld{color:#22673f}
.hl .gi{color:#22673f;background:#e6f6ec}
.hl .gd{color:#a52222;background:#fdeaea}
.hl .gh,.hl .gu{color:#553099;font-weight:600}
code{background:var(--accent-t);border-color:color-mix(in srgb, var(--accent) 14%, #fff)}
pre.code code{background:none;border:0}

/* chapter head */
.chead{border-bottom-width:3px;background:linear-gradient(to bottom, #fff 0%, var(--accent-t) 100%);
  padding:2.4mm 3mm 3mm;border-radius:3px 3px 0 0}
.ctitle{color:color-mix(in srgb, var(--accent) 80%, #000)}
"""


def css_for(profile: dict, tier: str) -> str:
    """The full print CSS for ``tier`` ('basic' | 'advanced'), branded from the profile."""
    if tier not in ("basic", "advanced"):
        raise ValueError(f"unknown tier {tier!r}")
    b = profile.get("branding") or {}
    accent = _color(b.get("accent"), "#1f4e79")
    accent2 = _color(b.get("accent2"), "#a3521a")
    css = COMMON + (BASIC if tier == "basic" else ADVANCED) + COLOR
    return (css.replace("@@PAGE_MARGIN@@", PAGE_MARGIN_CSS)
               .replace("@@ACCENT2@@", accent2).replace("@@ACCENT@@", accent)
               .replace("@@SANS@@", FONT_SANS).replace("@@MONO@@", FONT_MONO)
               .replace("@@SERIF@@", FONT_SERIF))


def header_template(running: str, tag: str) -> str:
    """Chrome header template: running title left, ``<short> . Basic|Advanced`` right."""
    import html as _h
    sans = FONT_SANS.replace('"', "'")
    return (
        f'<div style="font-size:7pt;font-family:{sans};color:#9aa4b0;width:100%;'
        'padding:0 15mm;display:flex;justify-content:space-between">'
        f'<span>{_h.escape(running)}</span><span>{_h.escape(tag)}</span></div>'
    )


def footer_template() -> str:
    """Chrome footer template: ``pageNumber / totalPages``, right aligned."""
    sans = FONT_SANS.replace('"', "'")
    return (
        f'<div style="font-size:7.5pt;font-family:{sans};color:#7d8894;width:100%;'
        'padding:0 15mm;display:flex;justify-content:flex-end">'
        '<span class="pageNumber"></span><span>&nbsp;/&nbsp;</span><span class="totalPages"></span></div>'
    )
