# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "matplotlib"]
# ///
"""notefig: the figure style library for per-note figure scripts (port of the CV make_figures.py style).

A per-note script (``<stem>.figures.py``) does::

    import os, sys
    sys.path.insert(0, os.environ["NOTEFIG_PATH"])
    import notefig as nf
    from notefig import INK, BLUE, plt

    @nf.figure("02-stack-frame")
    def stack_frame():
        fig, ax = plt.subplots(figsize=(nf.W, 2.8))
        ...
        nf.save(fig, "02-stack-frame")

    if __name__ == "__main__":
        raise SystemExit(nf.main())

``scripts/make_figures.py`` sets ``NOTEFIG_PATH`` (this directory) and ``NOTEFIG_OUT`` (where PNGs go)
and runs the script through ``uv run --script``.

Rules the figures follow, so the PDFs stay portable:

* PNG only. Chrome turns SVG/WOFF text into Type 3 fonts; a raster figure has no glyphs at all.
* Every random draw is seeded (use ``rng()``). A rebuilt note must be byte-identical, so ``save()`` strips
  the matplotlib version from the PNG metadata (``metadata={"Software": None}``).
* Numbers shown in a figure are the numbers the surrounding note works by hand.
* Anything illustrative rather than measured is stamped with ``schematic(ax)``.
* Width ``W`` = 7.4 in (about 1330 px at 180 dpi); height 2 to 4 in.

CLI of ``main(argv=None)``: ``--list`` (registered names), ``--audit`` (layout audit: overlapping labels
and text that overflows its ``box``; exit 4 if any hit), ``--out DIR`` (default ``$NOTEFIG_OUT`` or
``./figures``), ``NAME ...`` (build only these). Exit: 0 ok, 1 a builder raised, 2 unknown name, 4 audit hits.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import textwrap
import warnings

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle, FancyArrowPatch, FancyBboxPatch  # noqa: E402

__all__ = [
    "INK", "MUTED", "RULE", "BLUE", "ORANGE", "GREEN", "RED", "PURPLE", "TEAL", "GREY", "CYCLE", "W",
    "np", "plt", "Rectangle", "FancyArrowPatch", "FancyBboxPatch",
    "figure", "save", "clean", "spines", "show", "grid_numbers", "arrow", "box", "memory_strip",
    "schematic", "rng", "main", "REGISTRY", "outdir",
]

# ------------------------------------------------------------------ style ---
INK, MUTED, RULE = "#1a1d21", "#6b7480", "#c9ced5"
BLUE, ORANGE, GREEN, RED = "#2f6fb0", "#d9822b", "#2e7d4f", "#c0392b"
PURPLE, TEAL, GREY = "#7b5aa6", "#218a8a", "#9aa3ad"
CYCLE = [BLUE, ORANGE, GREEN, RED, PURPLE, TEAL]

matplotlib.rcParams.update({
    "figure.dpi": 180,
    "savefig.dpi": 180,
    "font.family": "DejaVu Sans",
    "font.size": 8.4,
    "axes.titlesize": 9.0,
    "axes.labelsize": 8.4,
    "axes.edgecolor": RULE,
    "axes.labelcolor": INK,
    "axes.titlecolor": INK,
    "axes.linewidth": 0.7,
    "axes.grid": False,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": 7.4,
    "ytick.labelsize": 7.4,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "legend.frameon": False,
    "legend.fontsize": 7.6,
    "lines.linewidth": 1.5,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.04,
})

W = 7.4          # figure width in inches -> ~1330 px at dpi 180
REGISTRY: dict = {}
NAME_RE = re.compile(r"^\d{2}[a-z]?-[a-z0-9-]+$")
AUDIT = False
AUDIT_HITS: list[str] = []
_BOXES: list = []            # (ax, patch, text) recorded by box() for the overflow audit
_FITTING_NAME = [""]
_MATH = re.compile(r"\$[^$]*\$")


def outdir() -> pathlib.Path:
    """Where PNGs are written: ``$NOTEFIG_OUT`` or ``./figures``. Created on demand."""
    d = pathlib.Path(os.environ.get("NOTEFIG_OUT") or "figures")
    d.mkdir(parents=True, exist_ok=True)
    return d


def rng(seed: int = 0) -> np.random.Generator:
    """The only sanctioned source of randomness: seeded, so rebuilds are byte-identical."""
    return np.random.default_rng(seed)


def figure(name):
    """Register a figure builder under the filename it produces (``NN[a]-kebab-name``)."""
    if not NAME_RE.match(name):
        raise ValueError(f"figure name {name!r} must match {NAME_RE.pattern}")

    def deco(fn):
        if name in REGISTRY:
            raise ValueError(f"figure {name!r} registered twice")
        REGISTRY[name] = fn
        return fn
    return deco


# ---------------------------------------------------------- title fitting ---
def _fit_titles(fig, name=""):
    """Shrink (then, if needed, re-wrap) any subplot title wider than its own panel.

    Multi-panel strips are narrow, and a two-line title easily runs into its neighbour's. Fitting here
    means a title can be written for clarity without being hand-tuned to the panel width.
    """
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    sized: dict[float, list] = {}                  # original size -> the titles that had it
    for ax in fig.axes:
        if ax.title.get_text():
            sized.setdefault(round(ax.title.get_fontsize(), 2), []).append(ax.title)

    for ax in fig.axes:
        t = ax.title
        text = t.get_text()
        if not text:
            continue
        avail = ax.get_window_extent(renderer=r).width * 1.04
        if t.get_window_extent(renderer=r).width <= avail:
            continue

        spans: list[str] = []                      # hide $...$ so wrapping never splits maths

        def stash(m):
            spans.append(m.group(0))
            return f"\x00{len(spans) - 1}\x00"

        masked = _MATH.sub(stash, text)
        restore = lambda s: re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], s)  # noqa: E731

        # Shrink BEFORE wrapping: wrapping first turns a deliberate two-line title into five ragged ones.
        fs = t.get_fontsize()
        while t.get_window_extent(renderer=r).width > avail and fs > 5.9:
            fs = max(5.9, fs - 0.3)
            t.set_fontsize(fs)

        if t.get_window_extent(renderer=r).width > avail:
            for width in range(34, 7, -2):
                cand = "\n".join(
                    "\n".join(textwrap.wrap(ln, width)) if len(ln) > width else ln
                    for ln in masked.split("\n"))
                t.set_text(restore(cand))
                if t.get_window_extent(renderer=r).width <= avail:
                    break

        # A title needing four or more lines is too long for its panel: cut it at source and move the
        # detail into the note's caption.
        if AUDIT and t.get_text().count("\n") >= 3:
            AUDIT_HITS.append(
                f"{_FITTING_NAME[0]}: title wrapped to {t.get_text().count(chr(10)) + 1} lines "
                f"- shorten at source: {text[:60]!r}")

    # One long title would otherwise leave a strip of panels with visibly different title sizes.
    for titles in sized.values():
        smallest = min(t.get_fontsize() for t in titles)
        for t in titles:
            t.set_fontsize(smallest)


def _audit_layout(fig, name):
    """Flag text that genuinely renders on top of other text, and box text wider than its box.

    Checked: titles, axis labels and every ``ax.text``/annotation against each other (and titles/labels
    against painted panels). Touching by a hairline (3 px) is not an overlap.
    """
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    items = []                                     # (label, bbox) for every visible text artist
    for i, ax in enumerate(fig.axes):
        for art, kind in ((ax.title, "title"), (ax.yaxis.label, "y-label"),
                          (ax.xaxis.label, "x-label")):
            if not art.get_text():
                continue
            try:
                bb = art.get_window_extent(renderer=r)
            except Exception:
                continue
            if bb.width <= 0 or bb.height <= 0:
                continue
            items.append((f"ax{i} {kind} {art.get_text()[:34]!r}", bb))
        for art in ax.texts:                       # free text: labels, annotations, box text
            if not art.get_visible() or not art.get_text().strip():
                continue
            try:
                bb = art.get_window_extent(renderer=r)
            except Exception:
                continue
            if bb.width > 0 and bb.height > 0:
                items.append((f"ax{i} text {art.get_text()[:34]!r}", bb))
        if ax.axison:                              # frameless axes only host free text
            items.append((f"ax{i} panel", ax.get_window_extent(renderer=r)))

    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            (na, ba), (nb, bbx) = items[a], items[b]
            if na.endswith("panel") and (nb.endswith("panel") or " text " in nb):
                continue                           # a panel holds its own free text by design
            if nb.endswith("panel") and " text " in na:
                continue
            ox = min(ba.x1, bbx.x1) - max(ba.x0, bbx.x0)
            oy = min(ba.y1, bbx.y1) - max(ba.y0, bbx.y0)
            if ox > 3 and oy > 3:
                AUDIT_HITS.append(f"{name}: {na}  OVERLAPS  {nb}  ({ox:.0f}x{oy:.0f}px)")

    for ax, patch, txt in _BOXES:                  # text must sit inside the box that carries it
        if ax.figure is not fig or not txt.get_text():
            continue
        tb = txt.get_window_extent(renderer=r)
        pb = patch.get_window_extent(renderer=r)
        over_x = max(pb.x0 - tb.x0, tb.x1 - pb.x1)
        over_y = max(pb.y0 - tb.y0, tb.y1 - pb.y1)
        if over_x > 2 or over_y > 2:
            AUDIT_HITS.append(f"{name}: box text {txt.get_text()[:30]!r} OVERFLOWS its box "
                              f"({max(over_x, 0):.0f}px wide, {max(over_y, 0):.0f}px high)")


def save(fig, name):
    """Fit titles, audit if enabled, write ``<NOTEFIG_OUT>/<name>.png`` byte-deterministically."""
    if not NAME_RE.match(name):
        raise ValueError(f"figure name {name!r} must match {NAME_RE.pattern}")
    _FITTING_NAME[0] = name
    _fit_titles(fig, name)
    if AUDIT:
        _audit_layout(fig, name)
    path = outdir() / f"{name}.png"
    fig.savefig(path, dpi=180, format="png", metadata={"Software": None})
    plt.close(fig)
    _BOXES[:] = [b for b in _BOXES if b[0].figure is not fig]
    print(f"  {name}.png  ({path.stat().st_size / 1024:.0f} KB)")


# --------------------------------------------------------------- helpers ----
def clean(ax, title=None, pad=4):
    """Hide ticks and spines (a canvas for free drawing)."""
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    if title:
        ax.set_title(title, pad=pad)


def spines(ax, keep=("left", "bottom")):
    for k, s in ax.spines.items():
        s.set_visible(k in keep)


def show(ax, img, title=None, cmap="gray", vmin=None, vmax=None, border=True):
    """Show an image array without axes."""
    if img.ndim == 2 and vmin is None and vmax is None:
        vmin, vmax = (0, 255) if img.dtype == np.uint8 else (float(img.min()), float(img.max()))
    ax.imshow(img, cmap=cmap if img.ndim == 2 else None, vmin=vmin, vmax=vmax, interpolation="nearest")
    clean(ax, title)
    if border:
        for s in ax.spines.values():
            s.set_visible(True); s.set_color(RULE); s.set_linewidth(0.6)


def grid_numbers(ax, M, fmt="{:.0f}", fontsize=7.2, color=None, cmap="gray",
                 vmin=None, vmax=None, title=None, highlight=None, hl_color=RED):
    """Draw a small matrix as a heat-mapped grid with the value printed in each cell."""
    M = np.asarray(M, dtype=float)
    if vmin is None:
        vmin = M.min()
    if vmax is None:
        vmax = M.max()
    ax.imshow(M, cmap=cmap, vmin=vmin, vmax=vmax, interpolation="nearest")
    # Label colour comes from the luminance the cell is ACTUALLY painted (not the raw value), and a
    # constant matrix has no ordering at all: both used to render white-on-white.
    cm = plt.get_cmap(cmap) if isinstance(cmap, str) else cmap
    rng_ = (vmax - vmin) or 1.0
    for (i, j), v in np.ndenumerate(M):
        r, g, b, _ = cm(float(np.clip((v - vmin) / rng_, 0, 1)))
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        c = color or ("white" if lum < 0.55 else INK)
        ax.text(j, i, fmt.format(v), ha="center", va="center", fontsize=fontsize, color=c)
    ax.set_xticks(np.arange(-.5, M.shape[1], 1), minor=True)
    ax.set_yticks(np.arange(-.5, M.shape[0], 1), minor=True)
    ax.grid(which="minor", color=RULE, linewidth=0.5)
    ax.tick_params(which="both", length=0)
    ax.set_xticks([]); ax.set_yticks([])
    if highlight is not None:
        r0, c0, h, w = highlight
        ax.add_patch(Rectangle((c0 - .5, r0 - .5), w, h, fill=False, ec=hl_color, lw=1.8))
    if title:
        ax.set_title(title, pad=4)


def arrow(ax, xy_from, xy_to, color=INK, lw=1.1, style="-|>", ms=7, **kw):
    ax.add_patch(FancyArrowPatch(xy_from, xy_to, arrowstyle=style, mutation_scale=ms,
                                 color=color, lw=lw, shrinkA=0, shrinkB=0, **kw))


# ------------------------------------------------------------- new helpers --
def box(ax, xy, w, h, text="", fc="white", ec=INK, tc=INK, fontsize=7.4, lw=0.9,
        rounding=0.0, ha="center", va="center", weight="normal", family=None, zorder=2):
    """A labelled rectangle in data coordinates; ``xy`` is the lower-left corner.

    Returns the patch. Text is centred (or left/right aligned with ``ha``) and registered with the
    ``--audit`` overflow check, so a label wider than its box is reported rather than shipped.
    """
    x, y = xy
    if rounding:
        patch = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={rounding}",
                               fc=fc, ec=ec, lw=lw, zorder=zorder)
    else:
        patch = Rectangle((x, y), w, h, fc=fc, ec=ec, lw=lw, zorder=zorder)
    ax.add_patch(patch)
    txt = None
    if text:
        tx = {"center": x + w / 2, "left": x + 0.04 * w, "right": x + 0.96 * w}[ha]
        kw = {"family": family} if family else {}
        txt = ax.text(tx, y + h / 2, text, ha=ha, va=va, color=tc, fontsize=fontsize,
                      fontweight=weight, zorder=zorder + 1, **kw)
        _BOXES.append((ax, patch, txt))
    return patch


def memory_strip(ax, cells, addr0=0x7FFC_0000, step=4, *, x0=0.0, y0=0.0, cell_w=1.0, cell_h=0.9,
                 vertical=False, grows="down", addr_fmt="0x{:08x}", colors=None, fontsize=7.2,
                 highlight=None, hl_color=RED, addr_every=1):
    """Labelled memory cells with their addresses: stack frames, struct layouts, arrays.

    ``cells``: list of labels, or of ``(label, nbytes)`` where ``nbytes`` is a multiple of ``step``
    and widens the cell proportionally. ``addr0`` is the lowest address; each ``step`` bytes is one
    unit. Horizontal strips run low -> high left to right. Vertical strips put the HIGH address at
    the top (``grows="down"``: the stack picture, first cell highest) or the low address at the top
    (``grows="up"``). Addresses are printed against each cell's low edge, every ``addr_every`` cells.
    ``colors``: optional per-cell fill; ``highlight``: indices outlined in ``hl_color``.
    Returns the list of cell patches in the order given. Set the axes limits yourself
    (``ax.set_xlim/ylim``) or call ``ax.autoscale_view`` after; helper returns ``(xmax, ymax)`` too.
    """
    items = [(c, step) if isinstance(c, str) else (c[0], int(c[1])) for c in cells]
    n_units = [max(1, nb // step) for _, nb in items]
    patches = []
    # offsets in bytes from the lowest address, in the order the cells are listed
    if vertical and grows == "down":
        total = sum(nb for _, nb in items)
        offs, acc = [], total
        for _, nb in items:
            acc -= nb
            offs.append(acc)
    else:
        offs, acc = [], 0
        for _, nb in items:
            offs.append(acc)
            acc += nb
    xmax = ymax = 0.0
    for k, ((label, nb), units, off) in enumerate(zip(items, n_units, offs)):
        u = off / step
        fc = colors[k] if colors else "white"
        if vertical:
            top_down = grows == "down"
            pos = u * cell_h if top_down else -(u + units) * cell_h
            patch = box(ax, (x0, y0 + pos), cell_w, units * cell_h, label, fc=fc, ec=INK,
                        fontsize=fontsize)
            ymax = max(ymax, y0 + pos + units * cell_h)
            xmax = max(xmax, x0 + cell_w)
            if k % addr_every == 0:
                ax.text(x0 - 0.06 * cell_w, y0 + pos + units * cell_h / 2,
                        addr_fmt.format(addr0 + off), ha="right", va="center",
                        fontsize=fontsize - 0.6, color=MUTED, family="DejaVu Sans Mono")
        else:
            patch = box(ax, (x0 + u * cell_w, y0), units * cell_w, cell_h, label, fc=fc, ec=INK,
                        fontsize=fontsize)
            xmax = max(xmax, x0 + (u + units) * cell_w)
            ymax = max(ymax, y0 + cell_h)
            if k % addr_every == 0:
                ax.text(x0 + u * cell_w + 0.02 * cell_w, y0 - 0.12 * cell_h,
                        addr_fmt.format(addr0 + off), ha="left", va="top", rotation=0,
                        fontsize=fontsize - 0.8, color=MUTED, family="DejaVu Sans Mono")
        patches.append(patch)
    for k in highlight or ():
        patches[k].set_edgecolor(hl_color)
        patches[k].set_linewidth(1.8)
        patches[k].set_zorder(2.5)                 # above neighbouring edges, below the label text
    return patches, (xmax, ymax)


def schematic(ax, text="SCHEMATIC - illustrative, not measured", loc="upper right", color=RED,
              fontsize=6.6):
    """Stamp an axes whose numbers are illustrative so no reader mistakes it for a measurement."""
    pos = {"upper right": (0.99, 0.985, "right", "top"), "upper left": (0.01, 0.985, "left", "top"),
           "lower right": (0.99, 0.015, "right", "bottom"), "lower left": (0.01, 0.015, "left", "bottom")}
    x, y, ha, va = pos[loc]
    return ax.text(x, y, text, transform=ax.transAxes, ha=ha, va=va, fontsize=fontsize, color=color,
                   fontweight="bold",
                   bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=color, lw=0.7), zorder=10)


# ------------------------------------------------------------------- main ---
def main(argv=None):
    global AUDIT
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--out" in argv:                            # convenience for running a script by hand
        i = argv.index("--out")
        os.environ["NOTEFIG_OUT"] = argv[i + 1]
        del argv[i:i + 2]
    if "--list" in argv:
        for k in REGISTRY:
            print(k)
        return 0
    AUDIT = "--audit" in argv
    names = [a for a in argv if not a.startswith("-")]
    todo = names or list(REGISTRY)
    missing = [n for n in todo if n not in REGISTRY]
    if missing:
        print("unknown figure(s):", ", ".join(missing))
        return 2
    print(f"Writing {len(todo)} figures to {outdir()}")
    failed = []
    for n in todo:
        try:
            REGISTRY[n]()
        except Exception as exc:                   # keep going: report every broken figure at once
            failed.append((n, f"{type(exc).__name__}: {exc}"))
            plt.close("all")
            print(f"  FAILED {n}: {type(exc).__name__}: {exc}")
    print(f"\n{len(todo) - len(failed)}/{len(todo)} figures built")
    if AUDIT:
        if AUDIT_HITS:
            print(f"\nLAYOUT AUDIT - {len(AUDIT_HITS)} problem(s):")
            for h in AUDIT_HITS:
                print(f"  {h}")
        else:
            print("\nLAYOUT AUDIT - no overlapping labels")
    if failed:
        return 1
    return 4 if (AUDIT and AUDIT_HITS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
