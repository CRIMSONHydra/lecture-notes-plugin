# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "matplotlib"]
# ///
"""Reference per-note figure script: copy this, rename it ``<stem>.figures.py`` and replace the bodies.

Conventions (see reference/figures.md):
  * one ``@nf.figure("NN[a]-kebab-name")`` builder per PNG; the name is the file name and the name
    used in the note's ``![alt](figures/NN[a]-kebab-name.png "**Lead.** caption")``;
  * the numbers drawn are the numbers the note works by hand (here: ``add(12, 13)`` at -O0);
  * illustrative values get ``nf.schematic(ax)``; measured values do not;
  * width ``nf.W`` (7.4 in), height 2 to 4 in; PNG only; no randomness except ``nf.rng(seed)``;
  * run through ``scripts/make_figures.py --script <this> --out <dir> [--audit]``.

Run by hand (from anywhere)::

    NOTEFIG_OUT=./figures uv run example.figures.py [--list] [--audit] [name ...]
"""
import os
import sys

sys.path.insert(0, os.environ.get("NOTEFIG_PATH") or os.path.dirname(os.path.abspath(__file__)))

import notefig as nf  # noqa: E402
from notefig import BLUE, GREEN, GREY, INK, MUTED, ORANGE, RED, np, plt  # noqa: E402


@nf.figure("02-stack-frame")
def stack_frame():
    """Stack frame of ``int add(int x, int y)`` at -O0 on x86-64 System V: addresses fall downwards."""
    fig, ax = plt.subplots(figsize=(nf.W, 2.9))
    nf.clean(ax)
    cells = [("return address  (8 B)", 8), ("saved RBP  (8 B)", 8), ("x = 12  (4 B)", 4), ("y = 13  (4 B)", 4)]
    colors = ["#e9eef5", "#fbeedd", "#e6f2ea", "#e6f2ea"]
    # lowest address = y; one unit = 4 bytes, so the 8-byte cells are twice as tall
    nf.memory_strip(ax, cells, addr0=0x7FFE0FF8, step=4, x0=1.0, cell_w=3.4, cell_h=0.92, vertical=True,
                    grows="down", addr_fmt="0x{:08x}", colors=colors, fontsize=7.6, highlight=[1],
                    hl_color=ORANGE)
    # offsets from RBP, centred on each cell (cell centres: 4.60, 2.76, 1.38, 0.46)
    for yc, lab in ((4.60, "rbp+8"), (2.76, "rbp"), (1.38, "rbp-4"), (0.46, "rbp-8")):
        ax.text(4.6, yc, lab, va="center", ha="left", fontsize=7.4, color=MUTED, family="DejaVu Sans Mono")
    # RBP points at the saved-RBP cell
    nf.arrow(ax, (7.5, 2.76), (5.7, 2.76), color=ORANGE, lw=1.3)
    ax.text(7.6, 2.76, "RBP after\npush rbp;\nmov rbp, rsp", va="center", ha="left", fontsize=7.2,
            color=ORANGE)
    # growth direction, left of the address column
    nf.arrow(ax, (-2.1, 5.0), (-2.1, 0.3), color=INK, lw=1.2)
    ax.text(-2.4, 2.65, "stack grows to LOWER addresses", rotation=90, va="center", ha="right",
            fontsize=7.4, color=INK)
    ax.set_xlim(-3.6, 10.4)
    ax.set_ylim(-0.3, 5.7)
    ax.set_title("Frame of add(12, 13): the caller's RBP and the return address sit above the locals", pad=4)
    nf.schematic(ax, "ILLUSTRATIVE ADDRESSES", loc="upper right")
    nf.save(fig, "02-stack-frame")


@nf.figure("02-latency-ladder")
def latency_ladder():
    """Order-of-magnitude access latencies, log scale. Illustrative: no specific machine is implied."""
    fig, ax = plt.subplots(figsize=(nf.W, 2.5))
    levels = ["register", "L1 cache", "L2 cache", "L3 cache", "main memory"]
    ns = np.array([0.3, 1.0, 4.0, 15.0, 80.0])
    colors = [GREEN, GREEN, BLUE, BLUE, RED]
    y = np.arange(len(levels))[::-1]
    ax.barh(y, ns, color=colors, height=0.62)
    ax.set_xscale("log")
    ax.set_xlim(0.1, 400)
    ax.set_yticks(y, levels)
    nf.spines(ax, keep=("left", "bottom"))
    for yy, v in zip(y, ns):
        ax.text(v * 1.18, yy, f"~{v:g} ns", va="center", fontsize=7.4, color=INK)
    ax.set_xlabel("access latency, nanoseconds (log scale)")
    ax.set_title("Each level down the hierarchy is roughly 4 to 10 times slower", pad=4)
    ax.axvline(1.0, color=GREY, lw=0.6, ls=":")
    nf.schematic(ax, "SCHEMATIC - order of magnitude", loc="upper right")
    nf.save(fig, "02-latency-ladder")


if __name__ == "__main__":
    raise SystemExit(nf.main())
