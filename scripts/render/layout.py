"""Page geometry shared by the renderer (build_pdf) and the audit (audit_pdf)."""
from __future__ import annotations

import re

MM_PER_IN = 25.4
PT_PER_MM = 72.0 / MM_PER_IN
PX_PER_MM = 96.0 / MM_PER_IN

PAGE_W_MM = 210.0            # A4
PAGE_H_MM = 297.0
PAGE_W_PT = PAGE_W_MM * PT_PER_MM
PAGE_H_PT = PAGE_H_MM * PT_PER_MM

MARGIN_MM = 15.0             # left/right margin, and the audit's content edge
SLACK_PT = 3.0               # sub-point rounding in Chrome's layout; hairline overhang is ignored

# @page margins in the CSS (top leaves room for the running header, bottom for the page number)
PAGE_MARGIN_CSS = "16mm 15mm 15mm 15mm"

# usable text width in CSS px: A4 minus the two side margins
CONTENT_PX = (PAGE_W_MM - 2 * MARGIN_MM) * PX_PER_MM          # ~680

# Page.printToPDF margins in inches; preferCSSPageSize wins when the CSS sets @page margins,
# these are the fallback (ported from the cpp builder).
PRINT_MARGINS_IN = {"marginTop": 0.55, "marginBottom": 0.5, "marginLeft": 0.59, "marginRight": 0.59}

PLACEHOLDER = "···"          # fixed-width contents page-number stand-in "..."

# A heading renders as "§2.4 Pass by value".  The section sign at the start of the line and
# whitespace right after the number stop "§2.1" matching "§2.10" and stop prose like
# "see §2.4" being mistaken for the heading.  Chapter ids may carry a companion letter: 2a.3
SEC_RE = re.compile(r"^\s*§\s?(\d+[a-z]?(?:\.\d+)*)\s+\S")

# mono advance width / font size, used to decide how far to shrink an over-wide code line
MONO_EM = 0.605
# A code block wider than this is NOT shrunk: it overflows on purpose so the audit catches it
# (the note lint also errors above 120 columns).
MAX_SHRINK_COLS = 120
