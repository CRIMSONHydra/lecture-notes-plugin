# Figures — rules, library, per-note script convention

Two kinds of figure exist. **Class visuals** (section 8): crops of the board drawings and annotated slides
the class produced, embedded from the keyframes. **Generated figures** (sections 1 to 7): redraws and new
pictures made with `notefig`. `tiers.<tier>.figures` is `none | optional | expected | class | class+generated`:
`class` allows class visuals only, `class+generated` both (the default for Basic and Advanced);
`none` means ASCII only (the profile opted out of pictures). Class visuals are **never capped**: every digest V-row appears.
Only the **figure-author** agent writes figure scripts and generated PNGs and runs the class-frame cropper;
writers plan them (`.notes-plugin-internal/work/meta/<tier>/<stem>.figplan.yaml`, `<stem>.visuals.json`) and place image
lines with draft captions.

## 1. When to draw

Draw a figure when **the picture is the explanation**, not as decoration.

| Good candidates | Bad candidates |
| --- | --- |
| memory layouts, stack frames, struct padding, cache lines (as strips with addresses) | anything a table says better |
| anything on a grid or geometric (pixels, matrices, kernels, grids of cells) | a bulleted list drawn as boxes |
| before/after, trade-off curves, latency ladders, scaling, failure modes | a logo, a stock-photo style schematic |
| timelines and traces with real numbers from the note's worked example | a figure with no number the note works by hand |

Small grids, byte layouts and call stacks may stay ASCII in a `text` fence: cheaper and always correct.

## 2. Rules

- **Generated figures are PNG only** (class photos may be JPEG, section 8). Chrome turns SVG and web-font
  text into Type 3 fonts that crash some PDF viewers; a raster has no glyphs. Matplotlib Agg with the
  bundled DejaVu Sans (no system fonts needed).
- **Deterministic.** Seed every random draw (`np.random.default_rng(<fixed seed>)`). A re-run must produce
  byte-identical files (`save()` strips the Software metadata). The runner reports sha256s.
- **Reuse the note's own numbers.** If the note hand-works a 4x4 grid, the figure shows that grid. Compute
  displayed values in the script from the same inputs; do not retype remembered numbers.
- **Measured over remembered.** Prefer real data you can compute offline (a real struct's offsets, a real
  array). A figure that reports a measured number cannot be wrong.
- **Label schematics.** A curve or bar chart that shows the *shape* of a published or typical result rather
  than your data must say `SCHEMATIC` in capitals in the caption **and** on the axis or title
  (`notefig.schematic(ax)` stamps it). Latency ladders are always schematic (order-of-magnitude).
- **Captions are teaching, not labels.** Lead with a bold sentence saying what to take away, then what to
  look at. Written for the same reader as the note: no jargon the text has not defined.
- **Size:** width 7.4 in (`W`), height 2 to 4 in (`size_in: [7.4, 2.8]`). Wide and short: they scale to
  the 180 mm content width. Small text at 7 pt or larger.
- **No overlapping labels.** Run the layout audit (`--audit`) and fix every reported overlap.
- **Names:** `NN[a]-kebab-name` (regex `^\d{2}[a-z]?-[a-z0-9-]+$`); the file is `<name>.png`.
- Colour is a palette, not decoration: ink for text, one accent per idea, grey for "not the point".
  Do not rely on colour alone to carry meaning (label directly).
- No emoji or exotic glyphs in figure text. Maths via matplotlib mathtext (`$x_1$`) only if the tier's
  `math` is true.

## 3. The library (`notefig`, in `<plugin>/scripts/figures/notefig.py`)

Read `notefig.py` and `example.figures.py` before writing a script: their signatures win over this summary.

| Name | Purpose |
| --- | --- |
| `INK MUTED RULE BLUE ORANGE GREEN RED PURPLE TEAL GREY CYCLE` | palette constants |
| `W` | figure width in inches (7.4) |
| `@figure("NN-name")` | register a builder under the file name it produces |
| `save(fig, "NN-name")` | fit titles, optional layout audit, write PNG (dpi 180, deterministic) |
| `clean(ax, title=None)` | hide ticks/spines, optional title |
| `spines(ax, keep=("left","bottom"))` | show only chosen spines |
| `show(ax, img, ...)` | draw an image/array with a border |
| `grid_numbers(ax, M, fmt=..., highlight=...)` | small matrix as a grid with values printed in cells |
| `arrow(ax, xy_from, xy_to, ...)` | annotated arrow |
| `box(ax, xy, w, h, text, ...)` | labelled rounded box (diagrams, pipelines) |
| `memory_strip(ax, cells, addr0, step, ...)` | labelled byte/word cells with addresses: stacks, struct layouts, cache lines |
| `schematic(ax)` | stamp SCHEMATIC on an axis |
| `main(argv=None)` | CLI: figure names, `--list`, `--audit` |

Output directory comes from `$NOTEFIG_OUT`; the library path from `$NOTEFIG_PATH`. The runner sets both.

## 4. Per-note script convention

One script per note: `.notes-plugin-internal/work/meta/<tier>/<stem>.figures.py`, with a PEP-723 header, run through `uv`. Template:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "matplotlib"]
# ///
"""Figures for note 02 (stack frame). Numbers match the note's worked example."""
import os
import sys

sys.path.insert(0, os.environ["NOTEFIG_PATH"])
from notefig import *  # noqa: F401,F403  (figure, save, W, palette, helpers, main)
import matplotlib.pyplot as plt


@figure("02-stack-frame")
def stack_frame():
    fig, ax = plt.subplots(figsize=(W, 2.8))
    cells = [("ret addr", 8), ("saved rbp", 8), ("x = 5", 4), ("y = 3", 4)]
    memory_strip(ax, cells, addr0=0x7FFC1000, step=1)
    clean(ax, "Frame of add(5, 3): offsets from the frame pointer")
    save(fig, "02-stack-frame")


if __name__ == "__main__":
    raise SystemExit(main())
```

- Names in the script must equal the image names in the note. Every image line in the note must have a
  PNG (lint and the build fail on a missing file); do not register figures the note never references.
- The `cells` format of `memory_strip` above is illustrative: copy the real one from `example.figures.py`.
- Keep the script self-contained: no network, no files outside `$NOTEFIG_OUT`, no reading the project's files.
- Compute from the note's inputs (`checks.py` values) rather than hard-coding results.

## 5. Running

```bash
# <meta> = .notes-plugin-internal/work/meta/<tier>, <notes> = notes/<tier>
# list, then audit-build everything for this note
uv run "<plugin>/scripts/make_figures.py" --script "<meta>/02-x.figures.py" --out "<notes>/figures" --list
uv run "<plugin>/scripts/make_figures.py" --script "<meta>/02-x.figures.py" --out "<notes>/figures" --audit \
    --json "<meta>/02-x.figures.json"
# one figure only
uv run "<plugin>/scripts/make_figures.py" --script "<meta>/02-x.figures.py" --out "<notes>/figures" 02-stack-frame
```

Exit code is non-zero if any figure fails or is not produced. Re-run and compare sha256s to confirm
determinism.

## 6. figure-author checklist

1. Read the figplan, the note's text around each figure, and the example script.
2. Write the script; run with `--audit`; fix overlaps; **Read every PNG** you produced and look at it
   (clipped labels, empty regions, unreadable text, wrong numbers). At most 3 iterations.
3. Edit **only** image lines and their captions in the note: lead bold sentence, then what to look at;
   mark schematics.
4. Confirm the counts: figures present at least `min_figures`; each referenced file exists; sha256s stable.
5. Return the usual 150-word report with the figure names and any figure you dropped and why.

## 7. Caption examples (generated figures)

Good: `**The frame grows downward.** Each local sits at a fixed negative offset from the frame pointer, so the
compiler never needs to know the absolute address.`

Weak: `Stack frame of add().` (a label, not a caption.)

Schematic: `**Each level is roughly 10x slower (SCHEMATIC, order of magnitude).** The log axis is why a
cache miss dwarfs an add.`

## 8. Class visuals (board drawings and annotated slides from the recording)

The class drew on a board; the note shows that drawing. The digest's **Class visuals** table (section 11)
lists every one as a V-row: frame, crop box (fractions of the frame JPG), quality and a treatment.
The writer realises each row; nothing in the table is optional.

| Treatment | What the note contains | Use it when |
| --- | --- | --- |
| `embed` | the cropped class frame as a figure | the default for board drawings and annotated slides |
| `redraw` | a clean `notefig` figure | the board version is messy, partial or **wrong** |
| `both` | the class crop, then the redraw | messy but pedagogically central, or a drawing the class corrected later |
| `text` | no image: transcribe as code/text | terminals, editors, code and slide text |

Tier defaults: **Basic** shows every V-row as `embed` or `both`. **Advanced** shows every V-row, preferring
`redraw` or `both` where a redraw adds precision (axes, addresses, corrected values). A drawing that a
verified class correction affects is always shown **corrected**: caption it `**Redrawn, corrected.**` and say
what changed.

**Pipeline.** The writer writes two sidecars in `meta_dir`, then places image lines:

1. `<stem>.visuals.yaml` (input rows for the cropper), one row per `embed|both` V-row:

   ```yaml
   - {id: V01, frame: F092, crop: [0.0, 0.20, 1.0, 0.95], treatment: embed, slug: call-stack,
      nn: "02", time: "01:26:52"}
   ```

2. `<stem>.visuals.json` (what the note will contain: `{V01: {files: [...], treatment}}`, see `note-format.md`).
3. Image lines using the file names the cropper will produce, `figures/NN-class-<slug>.jpg`.

Then the orchestrator runs the batch cropper, and the figure-author checks the results:

```bash
uv run "<plugin>/scripts/class_figure.py" --from-visuals "<meta>/02-x.visuals.yaml" \
    --frames-dir "<frames_dir>" --session-dir "<session_dir>" --out-dir "<notes>/figures" --nn 02 --sidecar "<meta>/02-x.visuals.json"
# one frame by hand (adjust a crop): add --crop-frac x0,y0,x1,y1 and --no-whiten for dark screens
uv run "<plugin>/scripts/class_figure.py" --frame "<frames_dir>/092_01-26-52.jpg" \
    --out "<notes>/figures/02-class-call-stack.jpg" --crop-frac 0.0,0.2,1.0,0.95
```

The cropper takes its pixels from the native-resolution twin `hires/<same name>` next to the frame (or next to the slide's
`pages/`) when one exists, so crops are sharp; `--no-hires` forces the low-res frame the agent read.
The cropper is **content-aware**: it whitens a light board (background to white, grid removed, ink contrast
boosted), grows the requested box outward over any ink that crosses its edge (so a sentence is never cut),
trims a too-loose box to the ink plus about 3% padding, drops a narrow colour-palette strip at a screen edge
(only when a blank gutter separates it), caps the width at 1400 px and writes a deterministic JPEG (q 85);
`--json` reports `expanded_for_cut_ink`, `trimmed` and `palette_removed` per figure. **Read every output
image** anyway and check three things: no text or drawing cut at an edge, no UI chrome (palette, toolbar,
menu bar), not mostly blank. On a failure change the crop fractions (or use a later, fuller frame of the
same drawing, see the screen note "extended later by") and re-run; **at most 2 re-crops per figure**.
A drawing that still fails is switched to `redraw` and reported.

**Slide drawings.** A handwritten slide page can be a source too: the yaml row has `page: slides/<pdf-stem>/pages/NNN.png` (resolved via `--session-dir`) instead of `frame:`; treatment defaults to `embed`, caption `"**From the slides, p.N.** ..."`.

**Captions.** Class crop: `"**From class, 01:26:52.** what to look at"`. Redraw: `"**Redrawn.** ..."`,
corrected: `"**Redrawn, corrected.** the class drew X; the correct value is Y"`. The timestamp is the frame's
`hh:mm:ss` from the digest row. Lint warns when a class figure has no timestamp caption.

**Size.** No cap on the count. Each class figure is at most 1400 px wide at JPEG q 85, so about 30 class
figures keep a PDF under roughly 15 MB. A terminal or editor frame is transcribed as text, not shown as a picture.
