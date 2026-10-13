# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "numpy", "pyyaml"]
# ///
"""Turn a class video frame (a JPG from the keyframe pipeline) into a note figure (PLAN 12.2).

    uv run class_figure.py --frame JPG --out FILE [--crop-frac x0,y0,x1,y1] [--whiten|--no-whiten]
                           [--max-width 1600] [--min-width 700] [--format png|jpg] [--no-hires] [--json OUT]
                           [--visual-id V01 --treatment embed --sidecar <stem>.visuals.json]
    uv run class_figure.py --from-visuals <stem>.visuals.yaml --frames-dir D --out-dir DIR
                           [--nn 02] [--sidecar <stem>.visuals.json] [--json OUT]

What it does, in order (all deterministic - same input, same bytes):
  0. pick the source: the native-resolution twin ``<frames dir>/hires/<same name>`` (keyframes.py) or
     ``<slides out>/hires/NNN.png`` (extract_pdf.py --hires-dpi) when it exists, else the low-res frame the
     agent read (``--no-hires`` forces that).  Crop fractions are relative, so they apply to either unchanged,
     and the whole crop GEOMETRY (content-aware growth, palette removal, margin/canvas trims) is decided on the
     low-res frame the agent checked, then mapped onto the twin: same region as approved, only sharper;
  1. crop to ``--crop-frac`` (fractions of the frame: x0,y0,x1,y1; default the whole frame);
  2. trim uniform margins (letterbox bars, empty board edge), then enlarge crops narrower than
     ``--min-width`` (bicubic) so thin pen strokes survive the later steps - only a small low-res source
     is ever enlarged; a native crop is already wide enough;
  3. detect the background: a light board (whiteboard, annotated slide) or a dark screen (terminal, IDE);
  4. light board + ``--whiten`` (the default for light): flatten the background to pure white, drop the
     faint grid lines, and give the ink full contrast while keeping its colour (blue/red/green pen);
     dark screens are NEVER whitened unless you force ``--whiten`` - transcribe terminals as text instead;
  5. cap the width at ``--max-width`` (never enlarges past ``--min-width``), write PNG or JPEG (q=90, 4:4:4).

Output format follows the ``--out`` suffix unless ``--format`` overrides it. Class photos as ``.jpg`` keep
the PDF small; the figure name convention is ``NN-class-<slug>.jpg``.

Batch mode reads a YAML (or JSON) list of rows ``{id, frame, crop, treatment, slug, [nn], [time]}`` - or a
mapping ``{visuals: [...]}`` / ``{V01: {...}}`` - and renders every row whose treatment is ``embed`` or
``both``. ``frame`` is a frame number (``67``, ``F067``, ``067``) or a JPG file name in ``--frames-dir``;
Instead of ``frame`` a row may name a slide page, ``page: slides/L2/pages/003.png`` (relative to
``--session-dir``, or absolute): scanned/handwritten slides are whitened the same way. ``--frame`` itself accepts
any image (keyframe JPG, page PNG, photo). ``crop`` is ``[x0,y0,x1,y1]`` or ``"x0,y0,x1,y1"`` (fractions). ``--sidecar`` records
``{V01: {files: [...], treatment}}`` (merged into an existing file).  ``--json`` reports per figure which source
was used (``source``: hires|lowres, ``source_file``, ``source_size`` = px of the whole frame).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, hires as hr, platform as plat  # noqa: E402

plat.utf8_stdio()

JPEG_Q = 90
LIGHT_MEDIAN = 150           # median luminance above this = a light board (strokes are the minority)
INK_T0, INK_T1 = 0.24, 0.40  # ink strength: below T0 is background/grid, above T1 is full ink
INK_GAIN = 1.45              # contrast boost for ink pixels


def parse_crop(s) -> tuple[float, float, float, float] | None:
    if s in (None, "", "-", "none"):
        return None
    vals = [float(x) for x in (re.split(r"[\s,]+", s.strip("[] ")) if isinstance(s, str) else s)]
    if len(vals) != 4:
        raise ValueError(f"crop needs 4 fractions x0,y0,x1,y1, got {s!r}")
    x0, y0, x1, y1 = vals
    if not (0 <= x0 < x1 <= 1.0001 and 0 <= y0 < y1 <= 1.0001):
        raise ValueError(f"crop {vals} must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
    return x0, y0, min(x1, 1.0), min(y1, 1.0)


def luminance(a: np.ndarray) -> np.ndarray:
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def trim_box(img: Image.Image, pad: int = 10) -> tuple[int, int, int, int]:
    """Box (x0,y0,x1,y1) that cuts edge rows/columns that are (almost) one flat colour, keeping ``pad`` px of
    margin; the whole image when there is nothing to cut (or the cut would eat most of the picture)."""
    a = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = a.shape
    whole = (0, 0, w, h)
    if h < 40 or w < 40:
        return whole
    rows = np.where(a.std(axis=1) > 6.0)[0]
    cols = np.where(a.std(axis=0) > 6.0)[0]
    if rows.size == 0 or cols.size == 0:
        return whole
    y0, y1 = max(0, rows[0] - pad), min(h, rows[-1] + 1 + pad)
    x0, x1 = max(0, cols[0] - pad), min(w, cols[-1] + 1 + pad)
    if (y1 - y0) < h * 0.25 or (x1 - x0) < w * 0.25:      # would eat most of the picture: leave it
        return whole
    return int(x0), int(y0), int(x1), int(y1)


def trim_uniform(img: Image.Image, pad: int = 10) -> Image.Image:
    """Cut edge rows/columns that are (almost) one flat colour, keeping ``pad`` px of margin."""
    b = trim_box(img, pad)
    return img if b == (0, 0, *img.size) else img.crop(b)


def canvas_box(img: Image.Image, inset: int = 3):
    """Box (x0,y0,x1,y1) of the bright canvas, or None when the image is not a letterboxed board."""
    lum = luminance(np.asarray(img.convert("RGB"), dtype=np.float32))
    h, w = lum.shape
    bright = lum > 200
    rows = np.where(bright.mean(axis=1) > 0.5)[0]
    cols = np.where(bright.mean(axis=0) > 0.5)[0]
    if rows.size < 20 or cols.size < 20:
        return None
    y0, y1 = rows[0] + inset, rows[-1] + 1 - inset
    x0, x1 = cols[0] + inset, cols[-1] + 1 - inset
    if y1 - y0 < h * 0.3 or x1 - x0 < w * 0.3 or (y1 - y0 >= h and x1 - x0 >= w):
        return None
    return int(x0), int(y0), int(x1), int(y1)


def crop_to_canvas(img: Image.Image, inset: int = 3) -> Image.Image:
    """Crop to the bright canvas: the outer rows/columns where most pixels are near-white. Drops the dark
    letterbox/app chrome around a whiteboard (and 1-3 px dark edge lines)."""
    b = canvas_box(img, inset)
    return img.crop(b) if b else img


def detect_background(img: Image.Image) -> str:
    """'light' when a sizeable part of the crop is near-white board/paper, else 'dark'."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    lum = luminance(a)
    return "light" if float(np.median(lum)) > LIGHT_MEDIAN else "dark"


def whiten(img: Image.Image) -> Image.Image:
    """Flatten a light board to pure white; ink keeps its colour and gains contrast."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    lum = luminance(a)
    board = lum > 200
    # background colour per channel = a high percentile of the bright (board) pixels
    ref = a[board] if board.sum() > 100 else a.reshape(-1, 3)
    bg = np.percentile(ref, 90, axis=0)
    bg = np.maximum(bg, 128.0)
    norm = np.clip(a / bg, 0.0, 1.0)                 # white-balanced: background -> 1.0
    d = 1.0 - norm                                   # darkness per channel
    s = d.max(axis=2)                                # ink strength (a saturated pen is dark in some channel)
    t = np.clip((s - INK_T0) / (INK_T1 - INK_T0), 0.0, 1.0)
    m = t * t * (3 - 2 * t)                          # smoothstep: grid and JPEG noise -> 0, ink -> 1
    out = 1.0 - np.clip(d * INK_GAIN, 0.0, 1.0) * m[..., None]
    return Image.fromarray(np.rint(out * 255.0).astype(np.uint8), "RGB")


# ---- content-aware crop (PLAN 16.1) ----------------------------------------------------------------------
ANALYSIS_W = 480          # the ink analysis runs on a mask at most this wide
PALETTE_SCAN = 0.16       # the palette lives in the outer part of the canvas
PALETTE_MAX = 0.09        # a palette strip (dots + icons) is narrower than this fraction of the canvas width
EXPAND_CAP = 2.5          # never grow the requested box by more than this factor in area
PAD_FRAC = 0.03
NOFLAGS = {"expanded_for_cut_ink": False, "trimmed": False, "palette_removed": False, "expansion_capped": False}


def _pool(m: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return m.copy()
    h, w = m.shape
    hh, ww = -(-h // k) * k, -(-w // k) * k
    p = np.zeros((hh, ww), dtype=bool)
    p[:h, :w] = m
    return p.reshape(hh // k, k, ww // k, k).any(axis=(1, 3))


def _dilate(m: np.ndarray, rx: int, ry: int | None = None) -> np.ndarray:
    """Box dilation: ``rx`` px horizontally (joins the words of a line), ``ry`` vertically (keeps lines apart)."""
    ry = rx if ry is None else ry
    out = m.copy()
    h, w = m.shape
    for d in range(1, ry + 1):
        out[d:, :] |= m[:h - d, :]
        out[:h - d, :] |= m[d:, :]
    m2 = out.copy()
    for d in range(1, rx + 1):
        out[:, d:] |= m2[:, :w - d]
        out[:, :w - d] |= m2[:, d:]
    return out


def _label(m: np.ndarray):
    """8-connected labelling (no scipy): returns (labels, n) with labels 1..n, 0 = background."""
    h, w = m.shape
    lab = np.zeros((h, w), dtype=np.int32)
    mm = m.tolist()
    ll = [[0] * w for _ in range(h)]
    n = 0
    ys, xs = np.nonzero(m)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if ll[y0][x0]:
            continue
        n += 1
        ll[y0][x0] = n
        stack = [(y0, x0)]
        while stack:
            y, x = stack.pop()
            for yy in (y - 1, y, y + 1):
                if 0 <= yy < h:
                    row, lrow = mm[yy], ll[yy]
                    for xx in (x - 1, x, x + 1):
                        if 0 <= xx < w and row[xx] and not lrow[xx]:
                            lrow[xx] = n
                            stack.append((yy, xx))
    return np.array(ll, dtype=np.int32).reshape(h, w), n


def _runs(flags: np.ndarray, gap: int):
    """Contiguous True runs in a 1-D array, merging runs separated by <= gap False items."""
    out: list[list[int]] = []
    for i in np.nonzero(flags)[0].tolist():
        if out and i - out[-1][1] <= gap + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def _bboxes(lab: np.ndarray, n: int):
    """Per-label bounding boxes [(x0, y0, x1, y1)] (inclusive) for labels 1..n, index 0 unused."""
    ys, xs = np.nonzero(lab)
    ids = lab[ys, xs]
    big = 1 << 30
    x0, y0 = np.full(n + 1, big), np.full(n + 1, big)
    x1, y1 = np.full(n + 1, -1), np.full(n + 1, -1)
    np.minimum.at(x0, ids, xs)
    np.minimum.at(y0, ids, ys)
    np.maximum.at(x1, ids, xs)
    np.maximum.at(y1, ids, ys)
    cnt = np.bincount(ids, minlength=n + 1)
    return [(int(x0[i]), int(y0[i]), int(x1[i]), int(y1[i]), int(cnt[i])) for i in range(n + 1)]


def find_palette(ink: np.ndarray, sat: np.ndarray):
    """Detect a drawing-app colour palette at the left/right edge of the canvas mask.

    Signature: >= 3 small, roundish, *saturated* blobs in one vertical column inside the outer PALETTE_SCAN of
    the canvas, at regular vertical spacing; the ink band around them (dots + icon column) is narrower than
    PALETTE_MAX of the width and separated from the other ink in the same rows by a blank gutter. Returns
    ``{side, p0, p1, comps}`` (mask px; ``comps`` = boxes of every small component lying wholly inside the
    band, near the palette's rows) or None. Anything not separated by a blank gutter is never touched."""
    h, w = ink.shape
    band = max(8, int(PALETTE_SCAN * w))
    gap = max(2, round(0.010 * w))                 # columns closer than this belong to one run
    chain_gap = max(3, round(0.03 * w))            # the dot column and the icon column beside it
    gutter_min = max(4, round(0.012 * w))
    for side in ("right", "left"):
        off = w - band if side == "right" else 0
        sub = ink[:, off:off + band]
        lab, n = _label(sub)
        if n < 3:
            continue
        bb = _bboxes(lab, n)
        dots = []
        for i in range(1, n + 1):
            x0, y0, x1, y1, cnt = bb[i]
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            if bw <= 0.05 * w and bh <= 0.05 * w and max(bw, bh) <= 2.2 * min(bw, bh) \
                    and sat[:, off:off + band][y0:y1 + 1, x0:x1 + 1][lab[y0:y1 + 1, x0:x1 + 1] == i].any():
                dots.append(((x0 + x1) / 2 + off, (y0 + y1) / 2, x0 + off, y0, x1 + off, y1))
        best = None
        for d in dots:
            col = sorted((e for e in dots if abs(e[0] - d[0]) <= 0.02 * w), key=lambda e: e[1])
            if len(col) < 3 or (best and len(col) <= len(best)):
                continue
            diffs = np.diff([e[1] for e in col])
            med = float(np.median(diffs))
            if med > 0 and float((np.abs(diffs - med) <= 0.6 * med).mean()) >= 0.6:
                best = col
        if not best:
            continue
        gx0, gx1 = min(e[2] for e in best), max(e[4] for e in best)
        r0, r1 = min(e[3] for e in best), max(e[5] for e in best)
        mr = max(2, int(0.03 * h))
        rows = ink[max(0, r0 - mr):r1 + mr + 1, :]
        runs = _runs(rows.any(axis=0), gap)
        mine = next((r for r in runs if r[0] <= gx1 and r[1] >= gx0), None)
        if mine is None:
            continue
        idx = runs.index(mine)
        p0, p1 = mine
        step = 1 if side == "right" else -1             # toward the edge: the icon column of the palette
        j = idx + step
        while 0 <= j < len(runs) and ((runs[j][0] - p1 - 1) if side == "right" else (p0 - runs[j][1] - 1)) <= chain_gap:
            p0, p1 = min(p0, runs[j][0]), max(p1, runs[j][1])
            j += step
        if (p1 - p0 + 1) >= PALETTE_MAX * w:
            continue
        inner = idx - step
        nxt = runs[inner] if 0 <= inner < len(runs) else None
        if nxt is not None:
            g = (mine[0] - nxt[1] - 1) if side == "right" else (nxt[0] - mine[1] - 1)
            if g < gutter_min:
                continue
        if (side == "right" and p1 < w - 1 - 0.1 * w) or (side == "left" and p0 > 0.1 * w):
            continue
        comps = []
        for i in range(1, n + 1):
            x0, y0, x1, y1, cnt = bb[i]
            if x0 + off >= p0 and x1 + off <= p1 and y0 >= r0 - 0.2 * h and y1 <= r1 + 0.2 * h:
                comps.append((x0 + off, y0, x1 + off, y1))
        return {"side": side, "p0": int(p0), "p1": int(p1), "comps": comps}
    return None


def content_box(img: Image.Image, box):
    """Content-aware version of ``box`` (x0,y0,x1,y1 px of ``img``, a LIGHT board): expand to whole ink
    components that the edge cuts, drop a palette strip, trim blank margins. Returns (box, flags)."""
    flags = dict(NOFLAGS)
    rgb = img.convert("RGB")
    W, H = rgb.size
    # the board: the bright canvas of the whole frame (letterboxed board or slide page); on a busy desktop
    # (wallpaper around a board window) it is looked for in the requested box grown by 15%
    cb = canvas_box(rgb)
    win = (0, 0, W, H)
    if cb is None and float((luminance(np.asarray(rgb, dtype=np.float32)) > 200).mean()) < 0.6:
        gx, gy = round(0.15 * W), round(0.15 * H)
        win = (max(0, box[0] - gx), max(0, box[1] - gy), min(W, box[2] + gx), min(H, box[3] + gy))
        sub = rgb.crop(win)
        cb = canvas_box(sub)
        if cb is None:
            if float((luminance(np.asarray(sub, dtype=np.float32)) > 200).mean()) < 0.6:
                return tuple(box), flags, []              # no clear light canvas: leave the crop alone
            cb = (0, 0, sub.size[0], sub.size[1])
    elif cb is None:
        cb = (0, 0, W, H)
    cx0, cy0, cx1, cy1 = cb[0] + win[0], cb[1] + win[1], cb[2] + win[0], cb[3] + win[1]
    canvas = rgb.crop((cx0, cy0, cx1, cy1))
    a = np.asarray(canvas, dtype=np.float32)
    ink_px = np.asarray(whiten(canvas), dtype=np.uint8).min(axis=2) < 200
    sat_px = ink_px & ((a.max(axis=2) - a.min(axis=2)) > 70)
    ch, cw = ink_px.shape
    k = max(1, cw // ANALYSIS_W)
    m, sat = _pool(ink_px, k), _pool(sat_px, k)
    mw = m.shape[1]
    bx0, by0 = max(box[0], cx0) - cx0, max(box[1], cy0) - cy0
    bx1, by1 = min(box[2], cx1) - cx0, min(box[3], cy1) - cy0
    if bx1 - bx0 < 8 or by1 - by0 < 8:
        return tuple(box), flags, []
    req_area = (bx1 - bx0) * (by1 - by0)
    # 1. palette strip: clip the box to the gutter and blank the strip out of the ink mask
    pal = find_palette(m, sat)
    lo_x, hi_x = 0, cw
    paint: list[tuple[int, int, int, int]] = []
    if pal:
        p0, p1 = pal["p0"], pal["p1"]
        for x0_, y0_, x1_, y1_ in pal["comps"]:
            m[y0_:y1_ + 1, x0_:x1_ + 1] = False            # the strip's own dots and icons leave the mask
            paint.append((x0_ * k, y0_ * k, (x1_ + 1) * k, (y1_ + 1) * k))
        if pal["side"] == "right":
            rest = np.nonzero(m[:, p0:].any(axis=0))[0]    # other ink that still reaches into the strip columns
            hi_x = (p0 + int(rest.max()) + 1) * k if rest.size else p0 * k
            paint = [q for q in paint if q[0] < hi_x]
        else:
            rest = np.nonzero(m[:, :p1 + 1].any(axis=0))[0]
            lo_x = int(rest.min()) * k if rest.size else (p1 + 1) * k
            paint = [q for q in paint if q[2] > lo_x]
        if bx1 > hi_x or bx0 < lo_x:
            flags["palette_removed"] = True
        bx0, bx1 = max(bx0, lo_x), min(bx1, hi_x)
    # 2. expand for cut ink (dilated components, each pulled in whole)
    rx, ry = max(4, round(0.0125 * W / k)), max(2, round(0.004 * W / k))      # relative to the FRAME: word gaps
    lab, n = _label(_dilate(m, rx, ry))
    comps = []
    keep = _bboxes(np.where(m, lab, 0), n)
    for x0_, y0_, x1_, y1_, cnt in keep[1:]:
        if cnt >= 6:
            comps.append((x0_ * k, y0_ * k, (x1_ + 1) * k, (y1_ + 1) * k))
    comps.sort()
    cap = req_area * EXPAND_CAP
    cur = [bx0, by0, bx1, by1]
    for _ in range(len(comps) + 1):
        changed = False
        for c in comps:
            inter = c[0] < cur[2] and c[2] > cur[0] and c[1] < cur[3] and c[3] > cur[1]
            inside = c[0] >= cur[0] and c[2] <= cur[2] and c[1] >= cur[1] and c[3] <= cur[3]
            if not inter or inside:
                continue
            nb = [max(lo_x, min(cur[0], c[0])), max(0, min(cur[1], c[1])),
                  min(hi_x, max(cur[2], c[2])), min(ch, max(cur[3], c[3]))]
            if (nb[2] - nb[0]) * (nb[3] - nb[1]) > cap:
                flags["expansion_capped"] = True
                continue
            if nb != cur:
                cur, changed = nb, True
                flags["expanded_for_cut_ink"] = True
        if not changed:
            break
    ex0, ey0, ex1, ey1 = max(0, cur[0]), max(0, cur[1]), min(cw, cur[2]), min(ch, cur[3])
    # 3. trim to the ink inside the expanded box, then pad by ~3% with blank canvas
    sub = m[ey0 // k:-(-ey1 // k), ex0 // k:-(-ex1 // k)]
    final = [ex0, ey0, ex1, ey1]
    if sub.any():
        ys, xs = np.nonzero(sub)
        ox, oy = ex0 // k * k, ey0 // k * k
        tx0, ty0 = ox + int(xs.min()) * k, oy + int(ys.min()) * k
        tx1, ty1 = ox + (int(xs.max()) + 1) * k, oy + (int(ys.max()) + 1) * k
        pw, ph = max(4, round(PAD_FRAC * (tx1 - tx0))), max(4, round(PAD_FRAC * (ty1 - ty0)))
        final = [max(lo_x, tx0 - pw), max(0, ty0 - ph), min(hi_x, tx1 + pw), min(ch, ty1 + ph)]
    area = (final[2] - final[0]) * (final[3] - final[1])
    ref = (cur[2] - cur[0]) * (cur[3] - cur[1]) if flags["expanded_for_cut_ink"] else req_area
    flags["trimmed"] = area < 0.85 * ref
    off = (cx0, cy0, cx0, cy0)
    return (tuple(f + o for f, o in zip(final, off)),
            flags, [tuple(f + o for f, o in zip(q, off)) for q in paint])


def render(frame: Path, out: Path, crop=None, do_whiten: bool | None = None, max_width: int = 1600,
           min_width: int = 700, fmt: str | None = None, use_hires: bool = True) -> dict:
    lowres = Path(frame)
    twin = hr.find_for(lowres) if use_hires else None
    frame = twin or lowres
    # The crop GEOMETRY (crop box, content-aware growth, margin and canvas trims) is decided on the low-res
    # frame the agent read and checked; its final box is then mapped onto the native-resolution twin, so a
    # figure shows exactly the region that was approved, only sharper. Without a twin both are the same image.
    lo = Image.open(lowres)
    lo.load()
    lo = lo.convert("RGB")
    if twin:
        img = Image.open(twin)
        img.load()
        img = img.convert("RGB")
    else:
        img = lo
    src_w, src_h = img.size
    lw, lh = lo.size
    sx, sy = src_w / lw, src_h / lh
    box = None
    req = (0, 0, lw, lh)
    if crop:
        x0, y0, x1, y1 = crop
        box = (round(x0 * lw), round(y0 * lh), round(x1 * lw), round(y1 * lh))
        req = box
    flags = dict(NOFLAGS)
    tb = trim_box(lo.crop(req))
    probe_box = (req[0] + tb[0], req[1] + tb[1], req[0] + tb[2], req[1] + tb[3])
    bgkind = detect_background(lo.crop(probe_box))
    smart = bgkind == "light" and do_whiten is not False
    paint: list = []
    if smart:
        stage1, flags, paint = content_box(lo, req)
        final_box = stage1
    else:
        stage1, final_box = probe_box, req
    g = lo.crop(stage1).copy()
    if paint:                                         # palette dots that sit beside a stroke: paint them out
        from PIL import ImageDraw
        bgc = tuple(int(v) for v in np.percentile(np.asarray(g).reshape(-1, 3), 90, axis=0))
        dr = ImageDraw.Draw(g)
        for q in paint:
            dr.rectangle((q[0] - stage1[0] - 2, q[1] - stage1[1] - 2, q[2] - stage1[0] + 1, q[3] - stage1[1] + 1),
                         fill=bgc)
    t = trim_box(g)                                   # (relative to stage1)
    g = g.crop(t)
    rel = [t[0], t[1], t[2], t[3]]
    if smart:
        cb = canvas_box(g)
        if cb:
            rel = [rel[0] + cb[0], rel[1] + cb[1], rel[0] + cb[2], rel[1] + cb[3]]
    # map onto the (possibly higher-resolution) source: stage-1 crop first, paint, then the trims
    hb = (round(stage1[0] * sx), round(stage1[1] * sy), round(stage1[2] * sx), round(stage1[3] * sy))
    img = img.crop(hb).copy()
    if paint:
        from PIL import ImageDraw
        bgc = tuple(int(v) for v in np.percentile(np.asarray(img).reshape(-1, 3), 90, axis=0))
        dr = ImageDraw.Draw(img)
        for q in paint:
            dr.rectangle((round((q[0] - stage1[0] - 2) * sx), round((q[1] - stage1[1] - 2) * sy),
                          round((q[2] - stage1[0] + 1) * sx), round((q[3] - stage1[1] + 1) * sy)), fill=bgc)
    iw, ih = img.size
    img = img.crop((max(0, round(rel[0] * sx)), max(0, round(rel[1] * sy)),
                    min(iw, round(rel[2] * sx)), min(ih, round(rel[3] * sy))))
    w, h = img.size
    enlarged = 1.0
    if w < min_width:                                 # enlarge BEFORE whitening: smoother strokes
        enlarged = round(min(min_width / w, 2.5), 3)
        img = img.resize((round(w * enlarged), round(h * enlarged)), Image.BICUBIC)
    whitened = (bgkind == "light") if do_whiten is None else bool(do_whiten)
    if whitened:
        img = whiten(img)
        img = trim_uniform(img)
    w, h = img.size
    if w > max_width:
        img = img.resize((max_width, max(1, round(h * max_width / w))), Image.LANCZOS)
    ext = ("." + fmt.lower().lstrip(".")) if fmt else (out.suffix.lower() or ".jpg")
    ext = ".jpg" if ext == ".jpeg" else ext
    if ext not in (".png", ".jpg"):
        raise ValueError(f"format must be png or jpg, got {ext!r}")
    out = out.with_suffix(ext)
    guard.assert_writable(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if ext == ".png":
        img.save(out, "PNG", optimize=True)
    else:
        img.save(out, "JPEG", quality=JPEG_Q, subsampling=0, optimize=True, progressive=False)
    return {"frame": plat.norm(frame).as_posix(), "out": out.as_posix(), "format": ext[1:],
            "background": bgkind, "whitened": whitened, "source_size": [src_w, src_h],
            "geometry_size": [lw, lh],
            "source": "hires" if twin else "lowres", "source_file": plat.norm(frame).as_posix(),
            "enlarged": enlarged,
            "crop_px": list(box) if box else None,
            "requested_box": list(req), "final_box": [int(v) for v in final_box], **flags, "size": list(img.size), "bytes": out.stat().st_size}


# ---- batch ----------------------------------------------------------------------------------------------
def load_rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        doc = json.loads(text)
    else:
        import yaml
        doc = yaml.safe_load(text)
    if isinstance(doc, dict) and isinstance(doc.get("visuals"), (list, dict)):
        doc = doc["visuals"]
    if isinstance(doc, dict):
        doc = [{"id": k, **(v or {})} for k, v in doc.items()]
    if not isinstance(doc, list):
        raise ValueError(f"{path}: expected a list of visuals")
    return [r for r in doc if isinstance(r, dict)]


def find_frame(frames_dir: Path, ref) -> Path:
    s = str(ref).strip()
    p = Path(s)
    if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
        q = p if p.is_absolute() else frames_dir / p.name
        if q.is_file():
            return q
        raise FileNotFoundError(f"frame {s!r} not found in {frames_dir}")
    m = re.fullmatch(r"[Ff]?0*(\d+)", s)
    if not m:
        raise FileNotFoundError(f"cannot read a frame reference from {s!r}")
    k = int(m.group(1))
    for f in sorted(frames_dir.glob("*.jpg")):
        fm = re.match(r"(\d+)_", f.name)
        if fm and int(fm.group(1)) == k:
            return f
    raise FileNotFoundError(f"no frame {k:03d}_*.jpg in {frames_dir}")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "figure"


def update_sidecar(path: Path, vid: str, files: list[str], treatment: str) -> None:
    guard.assert_writable(path)
    doc = {}
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError:
            doc = {}
    cur = doc.get(vid) or {}
    merged = list(dict.fromkeys([*(cur.get("files") or []), *files]))
    doc[vid] = {"files": merged, "treatment": treatment}
    plat.write_json(path, doc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--frame")
    ap.add_argument("--out")
    ap.add_argument("--crop-frac", default=None, help="x0,y0,x1,y1 as fractions of the frame")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--whiten", dest="whiten", action="store_true", default=None)
    g.add_argument("--no-whiten", dest="whiten", action="store_false")
    ap.add_argument("--max-width", type=int, default=1600)
    ap.add_argument("--no-hires", action="store_true",
                    help="crop from the low-res frame even when a hires/ twin exists")
    ap.add_argument("--min-width", type=int, default=700)
    ap.add_argument("--format", choices=["png", "jpg"], default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--visual-id", default=None, help="record this figure in --sidecar under this V id")
    ap.add_argument("--treatment", default="embed")
    ap.add_argument("--sidecar", default=None, help="<stem>.visuals.json to update")
    ap.add_argument("--from-visuals", default=None, help="batch: YAML/JSON list of visuals")
    ap.add_argument("--frames-dir", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--session-dir", default=None, help="base for `page:` rows (slides/<pdf>/pages/NNN.png)")
    ap.add_argument("--nn", default=None, help="session number for NN-class-<slug> names (batch)")
    a = ap.parse_args(argv)

    results: list[dict] = []
    try:
        if a.from_visuals:
            if not a.out_dir:
                ap.error("--from-visuals needs --out-dir (and --frames-dir for `frame:` rows)")
            frames_dir, out_dir = plat.norm(a.frames_dir or "."), plat.norm(a.out_dir)
            for i, row in enumerate(load_rows(plat.norm(a.from_visuals)), 1):
                treat = str(row.get("treatment", "embed")).lower()
                if treat not in ("embed", "both"):
                    continue
                vid = str(row.get("id") or f"V{i:02d}")
                nn = str(row.get("nn") or a.nn or "00").zfill(2)
                fmt = str(row.get("format") or a.format or "jpg")
                slug = slugify(row.get("slug") or row.get("what") or vid)
                if row.get("page"):
                    pg = Path(str(row["page"]))
                    pg = pg if pg.is_absolute() else plat.norm(a.session_dir or ".") / pg
                    if not pg.is_file():
                        raise FileNotFoundError(f"slide page {pg} not found (pass --session-dir)")
                    src_img = pg
                else:
                    src_img = find_frame(frames_dir, row["frame"])
                r = render(src_img, out_dir / f"{nn}-class-{slug}.{fmt}",
                           parse_crop(row.get("crop")), row.get("whiten", a.whiten), a.max_width, a.min_width, fmt,
                           not a.no_hires)
                r.update({"id": vid, "treatment": treat, "time": row.get("time")})
                results.append(r)
                if a.sidecar:
                    update_sidecar(plat.norm(a.sidecar), vid, [Path(r["out"]).name], treat)
        else:
            if not (a.frame and a.out):
                ap.error("give --frame and --out (or --from-visuals)")
            r = render(plat.norm(a.frame), plat.norm(a.out), parse_crop(a.crop_frac), a.whiten,
                       a.max_width, a.min_width, a.format, not a.no_hires)
            if a.sidecar and a.visual_id:
                update_sidecar(plat.norm(a.sidecar), a.visual_id, [Path(r["out"]).name], a.treatment)
            results.append(r)
    except (OSError, ValueError, KeyError, FileNotFoundError, guard.GuardError) as e:
        print(f"class_figure: {e}", file=sys.stderr)
        return 2
    for r in results:
        print(f"{Path(r['out']).name}: {r['size'][0]}x{r['size'][1]} {r['format']} {r['bytes'] // 1024} KB "
              f"background={r['background']} whitened={r['whitened']} src={r['source']} "
              f"{r['source_size'][0]}x{r['source_size'][1]}" + (f" ({r['id']})" if r.get("id") else ""))
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {"ok": True, "figures": results})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
