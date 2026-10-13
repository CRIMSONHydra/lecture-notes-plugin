# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "imageio-ffmpeg", "numpy", "pymupdf", "rapidocr>=3",
#     "onnxruntime-directml; sys_platform == 'win32'",
#     "onnxruntime; sys_platform != 'win32'",
# ]
# ///
"""
Keyframes from a lecture recording, deduplicated for the note-writing agent.

Everything here runs locally -- bundled ffmpeg (CPU decode: NVDEC measured 4x slower on these mostly
static screens), numpy, RapidOCR on the best available ONNX Runtime provider (auto order: DirectML
-> CUDA -> CoreML -> CPU; DirectML verified on Windows, CoreML/CUDA unverified until run on that OS;
any failure falls back to the CPU and the provider actually used is recorded).  No Claude tokens
are spent.  Tokens are spent later, when the note agent *reads* the output, so the job is to
keep as little image area as possible while losing nothing that was on screen.

Pipeline
--------
  1. Survey   ffmpeg decodes one 320x180 grey thumbnail per second.  Pixels that change in > 8 % of
              seconds are the webcam tile and get masked; columns/rows that are never lit are
              letterbox or the empty participant strip and get cropped.
  2. States   A screen state is captured only at the moment it is about to be *destroyed* (erased,
              scrolled, covered, switched away from), and only if it stayed on screen >= 3 s.
              Additions -- typing, drawing, appended output -- never trigger a frame: the later
              state contains them.
  3. Prune    Pixel subset test: a state whose ink is ~all still present in another is dropped.
  4. Grab     Native-resolution colour crops of the survivors, for OCR.
  5. Cover    OCR splits each frame into text (phrases, position-free) and drawing (pen strokes
              outside the text boxes).  Greedy set cover keeps the frame that adds the most unseen
              content, repeatedly, until no frame adds a line or two of text or a small sketch.
              A drawing counts as seen only if ONE kept frame shows the same strokes, in place or
              scrolled.  A dragged window, a vim mode change, a tab toggled back add nothing and
              are dropped; their on-screen times are credited to the frame that covers them.
  6. Package  A frame kept for a small new region is saved as a *detail crop* of that region;
              frames whose smallest text is large (handwriting) are saved downscaled.  Outputs:
              index.md (read first) · NNN_hh-mm-ss.jpg (what the agent reads) · frames.pdf (the
              same, captioned, for a human) · frames.json · meta.json · transcript.md when a
              Whisper file was given.  Nothing is written outside --out.
  7. Hires    The same frame again at scale 1.0 (JPEG q:v 2) in --out/hires/<same file name>: the
              source for note figures (class_figure.py prefers it).  Agents never read hires/ -- the
              JPGs above are the token budget.  --no-hires skips it.

Run:  uv run keyframes.py <video> --out DIR [--whisper whisper.json] [--transcript lecture.txt]
      uv run keyframes.py <video> --out DIR --keep-cache      # re-runs skip decoding and OCR
      uv run keyframes.py <video> --out DIR --ocr-device cpu  # skip the GPU for OCR
      uv run keyframes.py <video> --out DIR --hires-only      # only (re)grab hires/ for an existing
                                                              # DIR from its frames.json; idempotent
"""
from __future__ import annotations

import argparse
import os
import concurrent.futures as cf
import json
import pathlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

import imageio_ffmpeg
import numpy as np
import pymupdf

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from notes_lib import hires as hr  # noqa: E402

# line-buffered so a backgrounded run still shows its progress live
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

TW, TH = 320, 180      # survey thumbnail
INK = 24               # grey-level delta that counts as a changed / ink pixel
STROKE = 60            # local contrast of a pen stroke or glyph; a canvas grid or JPEG noise is < 40
LIT = 40               # brighter than this = not letterbox black
CAM_FREQ = 0.08        # webcam pixels change in 13-29 % of seconds, screen content in < 5 %
CAM_WINDOW = 300       # ...and per 5 min, by cell: a tile that comes and goes is diluted below
CELL, CAM_CELL = 20, 0.5   # that over a whole class, yet keeps the screen from ever looking still
STILL = 0.003          # < 0.3 % of the screen changed since the previous second = screen is still
KEEP_LOST = 0.003      # losing < 0.3 % of the screen (cursor, watermark hop) is not destruction
LOST_REL = 0.03        # pixel prune: a state is contained in another if < 3 % of its ink is lost
TEXT_UNIT = 4          # 4 unseen OCR phrases (a line or two of code or output) ...
PX_UNIT = 0.0025       # ... or 0.25 % of the screen in unseen pen strokes = worth one more frame
MIN_SCORE = 0.75       # OCR phrases below this confidence are not text (handwriting, noise)
DETAIL_PAD = 24        # survey px of context around a detail crop (~100 source px)
DETAIL_MAX = 0.5       # a detail crop bigger than half the frame is not worth it: keep it whole
TEXT_PX = 22           # smallest OCR box height worth keeping at 1:1 -- terminal text is 20-23 px
LEAD = 15              # grab: seek this far early so decoding passes a keyframe (TS GOP ~8 s)

# The platform's floating watermark gets OCR'd in most frames; it is neither content nor text size
WATERMARK = re.compile(r"\W*scaler\W*\S?\W*", re.I)
# OCR reads the same glyph differently between frames; fold the usual confusions together
CONFUSABLE = str.maketrans({"o": "0", "θ": "0", "i": "1", "l": "1", "|": "1"})


def hms(t: float) -> str:
    t = int(t)
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"


# ==========================================================================
# 1. Survey
# ==========================================================================
def probe(video: pathlib.Path) -> tuple[tuple[int, int], float]:
    gen = imageio_ffmpeg.read_frames(str(video))
    meta = next(gen)
    gen.close()
    return meta["source_size"], float(meta["duration"])


def survey(video: pathlib.Path) -> np.ndarray:
    cmd = [FFMPEG, "-v", "error", "-i", str(video), "-an", "-sn",
           "-vf", f"fps=1,scale={TW}:{TH},format=gray", "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (TW * TH)
    return np.frombuffer(raw, np.uint8, count=n * TW * TH).reshape(n, TH, TW)


def layout(A: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Webcam mask and content box (x0, y0, x1, y1), both in survey coordinates."""
    n = len(A)
    changes = np.zeros((TH, TW), np.int64)
    lit = np.zeros((TH, TW), np.int64)
    for i in range(0, n, 512):
        changes += (np.abs(np.diff(A[i:i + 513].astype(np.int16), axis=0)) > INK).sum(0)
        lit += (A[i:i + 512] > LIT).sum(0)
    vol = changes / max(1, n - 1) > CAM_FREQ
    cam = np.zeros((TH, TW), bool)
    if vol.any():
        (y0, x0), (y1, x1) = np.argwhere(vol).min(0), np.argwhere(vol).max(0)
        if (y1 - y0 + 1) * (x1 - x0 + 1) < 0.25 * TH * TW:    # one compact tile = the webcam
            cam[max(0, y0 - 3):y1 + 4, max(0, x0 - 3):x1 + 4] = True
        else:                                                 # scattered: mask pixel by pixel
            cam = vol
    litf = np.where(cam, 0, lit / n)
    cols, rows = np.flatnonzero(litf.max(0) > 0.02), np.flatnonzero(litf.max(1) > 0.02)
    if not len(cols):
        return cam, (0, 0, TW, TH)
    return cam, (int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1)


class Screen:
    """Survey thumbnails restricted to the content box, webcam masked out."""

    def __init__(self, A: np.ndarray, cam: np.ndarray, box: tuple[int, int, int, int],
                 size: tuple[int, int]):
        x0, y0, x1, y1 = box
        self.box = box
        self.W, self.H = size
        self.sx, self.sy = size[0] / TW, size[1] / TH       # survey px -> source px
        self.R = A[:, y0:y1, x0:x1]
        self.valid = ~cam[y0:y1, x0:x1]
        self.M = int(self.valid.sum())
        self._ink: dict[int, np.ndarray] = {}
        # Per 5-min window, mask CELL x CELL cells that change in most seconds: a webcam tile
        # moves every second (50-100 %), screen content -- even a scrolled board -- in < 20 %.
        h, w = self.valid.shape
        gh, gw = -(-h // CELL), -(-w // CELL)
        self.local = []
        for i in range(0, len(self.R), CAM_WINDOW):
            blk = self.R[i:i + CAM_WINDOW + 1].astype(np.int16)
            mask = self.valid.copy()
            if len(blk) > 1:
                ch = np.zeros((len(blk) - 1, gh * CELL, gw * CELL), bool)
                ch[:, :h, :w] = np.abs(np.diff(blk, axis=0)) > INK
                busy = ch.reshape(-1, gh, CELL, gw, CELL).any(axis=(2, 4)).mean(0) > CAM_CELL
                busy = np.kron(_near(busy), np.ones((CELL, CELL), bool))[:h, :w]
                mask &= ~busy
            self.local.append(mask)

    def valid_at(self, a: int) -> np.ndarray:
        return self.local[a // CAM_WINDOW]

    def changed(self, a: int, b: int) -> np.ndarray:
        return ((np.abs(self.R[a].astype(np.int16) - self.R[b]) > INK)
                & self.valid_at(a) & self.valid_at(b))

    def ink(self, a: int) -> np.ndarray:
        """Pixels that differ from the frame's background colour: text, strokes, panels."""
        if a not in self._ink:
            img = self.R[a]
            mask = self.valid_at(a)
            bg = np.bincount(img[mask]).argmax() if mask.any() else 0
            self._ink[a] = (np.abs(img.astype(np.int16) - bg) > INK) & mask
            if len(self._ink) > 64:
                self._ink.pop(next(iter(self._ink)))
        return self._ink[a]

    def lost(self, a: int, b: int) -> float:
        """Ink of state a that is gone in state b, as a fraction of the screen."""
        return (self.changed(a, b) & self.ink(a)).sum() / self.M

    def contained(self, a: int, b: int) -> bool:
        """Is (nearly) all of a's ink still there, unchanged, in b?"""
        ink = self.ink(a)
        n = ink.sum()
        return n == 0 or (self.changed(a, b) & ink).sum() / n < LOST_REL

    def to_src(self, x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        """Content-box survey rectangle -> source-pixel (x, y, w, h), even-sized."""
        bx, by = self.box[:2]
        X, Y = round((bx + x0) * self.sx), round((by + y0) * self.sy)
        w = min(round((x1 - x0) * self.sx), self.W - X) // 2 * 2
        h = min(round((y1 - y0) * self.sy), self.H - Y) // 2 * 2
        return X, Y, w, h

    def to_survey(self, quad, crop: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """OCR quad (pixels of a crop) -> content-box survey rectangle (x0, y0, x1, y1)."""
        q = np.asarray(quad, float)
        xs = (q[:, 0] + crop[0]) / self.sx - self.box[0]
        ys = (q[:, 1] + crop[1]) / self.sy - self.box[1]
        return int(xs.min()), int(ys.min()), int(np.ceil(xs.max())), int(np.ceil(ys.max()))


# ==========================================================================
# 2-3. States and pixel pruning
# ==========================================================================
@dataclass
class Frame:
    t: int                                        # second captured: just before it was destroyed
    seen: list[tuple[int, int]]                   # every (first, last) second it was on screen
    crop: tuple[int, int, int, int] = (0, 0, 0, 0)   # source px x, y, w, h: content, trimmed
    path: pathlib.Path | None = None
    lines: list[str] = field(default_factory=list)   # confident OCR phrases
    phrases: list[tuple[str, list]] = field(default_factory=list)   # (atom key, quad)
    atoms: set[str] = field(default_factory=set)
    heights: list[tuple[float, float, float]] = field(default_factory=list)  # (cx, cy, h) src px
    edges: np.ndarray | None = None               # every stroke, content-box survey px
    reach: np.ndarray | None = None               # edges dilated by one pixel
    drawing: np.ndarray | None = None             # strokes outside OCR text boxes
    gain: tuple[int, int] = (0, 0)                # (unseen phrases, unseen stroke px) when chosen
    detail_of: Frame | None = None                # a detail crop: the rest of the screen is there
    out: tuple[int, int, int, int] = (0, 0, 0, 0)    # saved region, source px
    scale: float = 1.0
    size: tuple[int, int] = (0, 0)                # saved image size
    n: int = 0


def states(S: Screen, destroy: float, min_dwell: int) -> list[Frame]:
    n = len(S.R)
    moved = np.ones(n)
    for t in range(1, n):
        moved[t] = S.changed(t - 1, t).sum() / S.M
    # Two references: `last` (the previous still second) catches a screen being replaced -- save
    # `last`, the fullest version of it; `peak` (held once small losses begin) catches slow erosion,
    # e.g. lines deleted one by one -- save `peak`, the version before erosion started.  Checking
    # only a held `peak` misses everything typed after the hold began.
    out: list[Frame] = []
    peak = last = first = dwell = 0
    started = False
    for t in map(int, np.flatnonzero(moved < STILL)):
        if not started:
            peak = last = first = t
            dwell, started = 1, True
            continue
        replaced = S.lost(last, t) >= destroy
        eroded = not replaced and S.lost(peak, t) >= destroy
        if replaced or eroded:
            if dwell >= min_dwell:
                keep = last if replaced else peak
                out.append(Frame(keep, [(first, keep)]))
            peak = last = first = t
            dwell = 1
            continue
        dwell += 1
        if S.lost(peak, t) < KEEP_LOST:
            peak = t
        last = t
    if started and dwell >= min_dwell:
        out.append(Frame(last, [(first, last)]))
    return out


def prune(S: Screen, frames: list[Frame]) -> list[Frame]:
    kept: list[Frame] = []
    for f in frames:
        host = next((k for k in kept if S.contained(f.t, k.t)), None)
        if host:
            host.seen += f.seen
            continue
        for k in [k for k in kept if S.contained(k.t, f.t)]:
            f.seen += k.seen
            kept.remove(k)
        kept.append(f)
    return sorted(kept, key=lambda f: f.t)


# ==========================================================================
# 4. Grab
# ==========================================================================
def trim(S: Screen, f: Frame) -> tuple[int, int, int, int]:
    """Source-pixel crop: the content box, tightened to this frame's own ink."""
    ink = S.ink(f.t)
    ys, xs = np.nonzero(ink)
    h, w = ink.shape
    if not len(xs):
        return S.to_src(0, 0, w, h)
    return S.to_src(max(0, xs.min() - 3), max(0, ys.min() - 3),
                    min(w, xs.max() + 4), min(h, ys.max() + 4))


def grab(video: pathlib.Path, t: int, region: tuple[int, int, int, int],
         path: pathlib.Path, scale: float = 1.0) -> tuple[int, int]:
    """One frame at second t, cropped (and optionally downscaled) -> JPEG.  Returns its size."""
    x, y, w, h = region
    ow, oh = max(2, round(w * scale) // 2 * 2), max(2, round(h * scale) // 2 * 2)
    vf = f"crop={w}:{h}:{x}:{y}" + (f",scale={ow}:{oh}:flags=area" if (ow, oh) != (w, h) else "")
    for at in (t, t - 1):              # the very last second can sit past the final frame
        lead = min(LEAD, at)
        subprocess.run([FFMPEG, "-v", "fatal", "-y", "-ss", str(at - lead), "-i", str(video),
                        "-ss", str(lead), "-frames:v", "1", "-vf", vf, "-q:v", "3", str(path)],
                       check=True)
        if path.exists():
            return ow, oh
    raise RuntimeError(f"ffmpeg wrote no frame at {hms(t)}")


# ==========================================================================
# 5. OCR + greedy cover
# ==========================================================================
# ONNX Runtime execution providers RapidOCR can drive, in auto order:
# (label, ORT provider name, RapidOCR EngineConfig.onnxruntime switch).  DirectML runs the OCR
# models on any DX12 GPU (2.3x faster than CPU here, same text); CUDA and CoreML are the same
# idea on NVIDIA / Apple (unverified until run on that OS).  Linux ships the CPU-only onnxruntime
# on purpose: the OCR GPU saves only ~2-4 min per class.
OCR_PROVIDERS = (("DirectML", "DmlExecutionProvider", "use_dml"),
                 ("CUDA", "CUDAExecutionProvider", "use_cuda"),
                 ("CoreML", "CoreMLExecutionProvider", "use_coreml"))
CPU_EP = "CPUExecutionProvider"


def ocr_candidates(device: str, avail: list[str]) -> list[tuple[str, str | None]]:
    """Providers to try, best first: [(label, RapidOCR switch or None for CPU)].  The CPU is always
    last, so a failed GPU never ends the run.  `device`: auto | gpu (same order) | cpu."""
    gpu = [(lab, sw) for lab, ep, sw in OCR_PROVIDERS if ep in avail] if device != "cpu" else []
    return [*gpu, ("CPU", None)]


def ocr_engine(switch: str | None, cache_dir: pathlib.Path):
    from rapidocr import RapidOCR
    params: dict = {"Global.use_cls": False, "Global.log_level": "error"}
    if switch:
        params[f"EngineConfig.onnxruntime.{switch}"] = True
    if switch == "use_coreml":     # RapidOCR's default cache dir is /tmp/RapidOCR: keep it under --out
        params["EngineConfig.onnxruntime.coreml_ep_cfg.ModelCacheDirectory"] = str(cache_dir / "coreml")
    return RapidOCR(params=params)


def engine_providers(eng) -> list[str] | None:
    """The providers ONNX Runtime actually attached to the detector (it can quietly fall back)."""
    try:
        return list(eng.text_det.session.session.get_providers())
    except Exception:
        return None


def ocr_image(eng, path: pathlib.Path) -> dict:
    r = eng(str(path))
    return {"txts": list(r.txts or ()), "scores": [float(s) for s in (r.scores or ())],
            "boxes": [] if r.boxes is None else np.asarray(r.boxes).tolist()}


def _why(e: Exception) -> str:
    msg = str(e).strip().splitlines()
    return f"{type(e).__name__}: {msg[-1][:110] if msg else ''}"


def ocr(frames: list[Frame], cache: pathlib.Path, device: str = "auto") -> dict:
    """Fill the text fields of each frame, caching raw OCR results by candidate file name.
    Returns the provider record {"label", "actual", "requested", "failed", "cached"}."""
    store = json.loads(cache.read_text(encoding="utf-8-sig")) if cache.exists() else {}
    prov = store.get("__provider__") or {"label": "unknown"}
    todo = [f for f in frames if f.path.name not in store]
    if not frames:
        return {"label": "none", "cached": False}
    if not todo:
        prov = {**prov, "cached": True}
        print(f"      ocr: all {len(frames)} images cached (provider when run: {prov['label']})")
    else:
        import onnxruntime
        avail = onnxruntime.get_available_providers()
        failed: list[str] = []
        eng, first, prov = None, None, None
        for label, switch in ocr_candidates(device, avail):
            try:                              # an engine can build fine and still die on its first image
                eng = ocr_engine(switch, cache.parent)
                first = ocr_image(eng, todo[0].path)
            except Exception as e:
                failed.append(f"{label}: {_why(e)}")
                print(f"      ocr {label} failed ({failed[-1]}) -- falling back")
                eng = None
                continue
            actual = engine_providers(eng)
            label_used = label
            if switch and actual and actual[0] == CPU_EP:     # asked for a GPU, ORT gave the CPU
                failed.append(f"{label}: onnxruntime attached {CPU_EP} instead")
                label_used = "CPU"
            prov = {"label": label_used, "actual": actual[0] if actual else None, "requested": label,
                    "failed": failed, "available": avail, "cached": False}
            break
        if eng is None or prov is None:
            raise RuntimeError("OCR failed on every provider: " + "; ".join(failed))
        suffix = f" [{prov['actual']}]" if prov["actual"] else ""
        print(f"      ocr on {prov['label']}{suffix}: {len(todo)} images")
        for i, f in enumerate(todo, 1):
            try:
                rec = first if i == 1 else ocr_image(eng, f.path)
            except Exception as e:            # a GPU lost mid-run: finish on the CPU
                if prov["label"] == "CPU":
                    raise
                failed.append(f"{prov['label']} (image {i}): {_why(e)}")
                print(f"      ocr {prov['label']} failed mid-run ({failed[-1]}) -- continuing on CPU")
                eng = ocr_engine(None, cache.parent)
                prov = {**prov, "label": "CPU", "actual": (engine_providers(eng) or [None])[0],
                        "failed": failed}
                rec = ocr_image(eng, f.path)
            store[f.path.name] = rec
            if i % 25 == 0:
                print(f"      ocr {i}/{len(todo)}")
        store["__provider__"] = prov
        cache.write_text(json.dumps(store), encoding="utf-8", newline="\n")
    for f in frames:
        rec = store[f.path.name]
        X, Y = f.crop[:2]
        good = [(t, b) for t, s, b in zip(rec["txts"], rec["scores"], rec["boxes"], strict=False)
                if s >= MIN_SCORE and not WATERMARK.fullmatch(t.strip())]
        f.lines = [t for t, _ in good]
        f.phrases = [(re.sub(r"\s+", "", t.lower()).translate(CONFUSABLE), b) for t, b in good]
        f.atoms = {k for k, _ in f.phrases if len(k) >= 2}
        f.heights = [(X + sum(p[0] for p in b) / 4, Y + sum(p[1] for p in b) / 4,
                      max(p[1] for p in b) - min(p[1] for p in b)) for _, b in good]
    return prov


def shown(f: Frame) -> list[int]:
    """Indexes of f.lines / f.heights whose text lies inside the saved region."""
    x, y, w, h = f.out
    return [i for i, (cx, cy, _) in enumerate(f.heights) if x <= cx < x + w and y <= cy < y + h]


def scale_for(f: Frame) -> float:
    """Downscale only as far as the smallest text inside the saved region allows: terminal text
    (20-23 px) stays 1:1, big handwriting goes down to half.  No readable text: 0.75."""
    hs = [f.heights[i][2] for i in shown(f)]
    if len(hs) < 3:
        return 0.75
    return min(1.0, max(0.5, TEXT_PX / float(np.percentile(hs, 10))))


def _near(m: np.ndarray) -> np.ndarray:
    """3x3 dilation: a stroke counts as present if the other frame has one within a pixel."""
    h, w = m.shape
    p = np.pad(m, 1)
    return np.logical_or.reduce([p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)])


def _shifted(m: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """out[y, x] = m[y + dy, x + dx], False outside."""
    h, w = m.shape
    out = np.zeros_like(m)
    ys, yd = (slice(dy, h), slice(0, h - dy)) if dy >= 0 else (slice(0, h + dy), slice(-dy, h))
    xs, xd = (slice(dx, w), slice(0, w - dx)) if dx >= 0 else (slice(0, w + dx), slice(-dx, w))
    out[yd, xd] = m[ys, xs]
    return out


def _lags(a: np.ndarray, b: np.ndarray, k: int = 2) -> set[int]:
    """0 plus the k offsets d (within half the length) where profile a[i + d] best matches b[i]."""
    a, b = a - a.mean(), b - b.mean()
    corr = np.correlate(a, b, "full")            # corr[j] pairs a[i + j - (len(b) - 1)] with b[i]
    d = np.arange(len(corr)) - (len(b) - 1)
    ok = np.abs(d) <= len(b) // 2
    return {0, *(int(x) for x in d[ok][np.argsort(corr[ok])[-k:]])}


def strokes(S: Screen, f: Frame) -> None:
    """f.edges: every stroke on screen; f.drawing: the strokes outside OCR text boxes (diagrams,
    handwriting).  Edges, not ink: a terminal's dark panel is 'ink' across a third of the screen,
    and dragging it around would look like a new diagram every time."""
    a = S.R[f.t].astype(np.int16)
    h, w = a.shape
    p = np.pad(a, 1, mode="edge")
    local = sum(p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)) / 9
    f.edges = (np.abs(a - local) > STROKE) & S.valid_at(f.t)
    f.reach = _near(f.edges)
    f.drawing = f.edges.copy()
    for _, quad in f.phrases:
        x0, y0, x1, y1 = S.to_survey(quad, f.crop)
        f.drawing[max(0, y0 - 1):y1 + 1, max(0, x0 - 1):x1 + 1] = False


def unseen(f: Frame, c: Frame) -> tuple[int, int, int]:
    """(stroke px of f's drawing that c does not show, dy, dx) -- in place, or scrolled/panned by
    the offset that best lines their stroke profiles up: the whiteboard moves, drawings don't."""
    dys = _lags(c.edges.sum(1), f.drawing.sum(1))
    dxs = _lags(c.edges.sum(0), f.drawing.sum(0))
    return min((int((f.drawing & ~_shifted(c.reach, dy, dx)).sum()), dy, dx)
               for dy in dys for dx in dxs)


def cover(S: Screen, frames: list[Frame], text_unit: float, px_unit: float) -> list[Frame]:
    """Greedy set cover.  Text is position-free (a phrase seen anywhere is seen); a drawing is
    covered only by a single chosen frame that shows the same strokes -- a union of masks from
    different screens would soon cover the whole page and hide new diagrams."""
    unit_px = px_unit * S.M
    seen_atoms: set[str] = set()
    best: dict[int, tuple[int, Frame | None, int, int]] = {
        id(f): (int(f.drawing.sum()), None, 0, 0) for f in frames}

    def gain(f: Frame) -> float:
        return len(f.atoms - seen_atoms) / text_unit + best[id(f)][0] / unit_px

    def cost(f: Frame, c: Frame) -> float:
        return len(f.atoms - c.atoms) / text_unit + unseen(f, c)[0] / unit_px

    left, chosen = list(frames), []
    while left:
        f = max(left, key=gain)
        if gain(f) < 1:
            break
        px, c, dy, dx = best[id(f)]
        f.gain = (len(f.atoms - seen_atoms), px)
        if c is not None:                        # what exactly is new here, and where?
            new = f.drawing & ~_shifted(c.reach, dy, dx)
            for key, quad in f.phrases:
                if key not in seen_atoms:
                    x0, y0, x1, y1 = S.to_survey(quad, f.crop)
                    new[max(0, y0):y1 + 1, max(0, x0):x1 + 1] = True
            detail(S, f, new & S.valid_at(f.t), min(chosen, key=lambda k: cost(f, k)))
        chosen.append(f)
        left.remove(f)
        seen_atoms |= f.atoms
        for g in left:
            u = unseen(g, f)
            if u[0] < best[id(g)][0]:
                best[id(g)] = (u[0], f, u[1], u[2])
    for g in left if chosen else ():   # credit a dropped frame's screen time to its best cover
        min(chosen, key=lambda k: cost(g, k)).seen += g.seen
    return sorted(chosen, key=lambda k: k.t)


def detail(S: Screen, f: Frame, new: np.ndarray, context: Frame) -> None:
    """If f's new content sits in a small region, save only that region (plus context)."""
    h, w = new.shape
    p = np.pad(new, 1).astype(np.uint8)
    crowd = sum(p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3))
    ys, xs = np.nonzero(new & (crowd >= 3))      # strokes and glyphs, not isolated noise specks
    if not len(xs):
        return
    x0, x1 = max(0, xs.min() - DETAIL_PAD), min(w, xs.max() + 1 + DETAIL_PAD)
    y0, y1 = max(0, ys.min() - DETAIL_PAD), min(h, ys.max() + 1 + DETAIL_PAD)
    X, Y, W, H = S.to_src(x0, y0, x1, y1)
    cx, cy, cw, ch = f.crop                      # stay inside the frame's own trimmed crop
    X, Y = max(X, cx), max(Y, cy)
    W, H = (min(X + W, cx + cw) - X) // 2 * 2, (min(Y + H, cy + ch) - Y) // 2 * 2
    if W > 0 and H > 0 and W * H <= DETAIL_MAX * cw * ch:
        f.out, f.detail_of = (X, Y, W, H), context


# ==========================================================================
# 6. Speech and packaging
# ==========================================================================
def load_speech(duration: float, whisper: pathlib.Path | None = None,
                transcript: pathlib.Path | None = None):
    """-> (label, said(t) function, whisper segments or None).  Whisper JSON (exact timing) wins;
    else the plain-text class transcript, placed by proportional position; else nothing."""
    if whisper and whisper.exists():
        segs = json.loads(whisper.read_text(encoding="utf-8-sig"))["segments"]

        def said(t: int) -> str:            # enough words to find the spot in the class transcript
            words = " ".join(s["text"].strip() for s in segs
                             if s["end"] > t - 25 and s["start"] < t + 5).split()
            return " ".join(words[-30:])
        return "Whisper segments (exact timing)", said, segs
    if whisper:
        print(f"      warning: --whisper {whisper} does not exist -- ignoring it")
    if transcript and transcript.exists():
        body = re.split(r"\n=+\s*\n", transcript.read_text(encoding="utf-8-sig"), maxsplit=1)[-1].strip()

        def said(t: int) -> str:
            i = int(len(body) * t / duration)
            words = body[max(0, i - 300):i + 50].split()[1:-1]
            return "≈ " + " ".join(words[-30:]) if words else ""
        return ("≈ proportional position in the class transcript (no timestamps) — a "
                "neighbourhood hint, not alignment"), said, None
    if transcript:
        print(f"      warning: --transcript {transcript} does not exist -- ignoring it")
    return "none found", (lambda t: ""), None


def spans(f: Frame) -> list[list[int]]:
    """Every stretch f's content was on screen, merging gaps of under 5 s."""
    merged: list[list[int]] = []
    for a, b in sorted(set(f.seen)):
        if merged and a <= merged[-1][1] + 5:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def seen_str(f: Frame) -> str:
    return ", ".join(hms(a) if b - a < 5 else f"{hms(a)}–{hms(b)}" for a, b in spans(f))


def kind(f: Frame) -> str:
    return f"detail - rest of screen in Frame {f.detail_of.n:03d}" if f.detail_of else "full screen"


def latin1(s: str) -> str:
    s = s.replace("≈", "~").replace("–", "-").replace("—", "-").replace("…", "...")
    return s.encode("latin-1", "replace").decode("latin-1")


def write_pdf(frames: list[Frame], path: pathlib.Path, duration: float, said) -> None:
    """All frames, one captioned page each -- for a human to flip through.  The agent reads the
    JPGs: the Read tool needs poppler to render PDF pages, and a JPG is exactly what was saved."""
    doc = pymupdf.open()
    for f in frames:
        w, h = f.size
        pw = max(w, 480)
        cap = (f"Frame {f.n:03d}  |  {hms(f.t)}  |  {100 * f.t / duration:.0f}% of class  |  "
               f"{kind(f)}  |  on screen {seen_str(f)}\n{said(f.t)}")
        page = doc.new_page(width=pw, height=h + 46)
        page.insert_textbox(pymupdf.Rect(6, 4, pw - 6, 44), latin1(cap), fontsize=8, fontname="helv")
        page.insert_image(pymupdf.Rect(0, 46, w, 46 + h), filename=str(f.path))
    doc.save(path, garbage=3, deflate=True)


def write_transcript(segs: list[dict], frames: list[Frame], path: pathlib.Path) -> None:
    marks = sorted((a, f.n) for f in frames for a, _ in spans(f))
    names = {f.n: f.path.name for f in frames}
    lines = ["# Timestamped transcript (Whisper)", "",
             "`▶ Frame NNN` marks the moment that screen appears; see `index.md` for the file.", ""]
    para: list[str] = []
    para_t = None
    mi, last = 0, 0

    def flush():
        if para:
            lines.append(f"**[{hms(para_t)}]** " + " ".join(para))
            lines.append("")

    for s in segs:
        while mi < len(marks) and marks[mi][0] <= s["start"]:
            n = marks[mi][1]
            mi += 1
            if n == last:                  # the same screen again, with nothing in between
                continue
            flush()
            para, para_t, last = [], None, n
            lines.append(f"> ▶ Frame {n:03d} — `{names[n]}`")
            lines.append("")
        if para_t is None:
            para_t = s["start"]
        para.append(s["text"].strip())
        if s["end"] - para_t >= 30:
            flush()
            para, para_t = [], None
    flush()
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def provider_str(prov: dict) -> str:
    """'DirectML [DmlExecutionProvider]', 'CPU (cached)', ..."""
    s = prov.get("label", "unknown")
    if prov.get("actual") and prov["actual"] != CPU_EP:
        s += f" [{prov['actual']}]"
    return s + (" (cached)" if prov.get("cached") else "")


def write_index(video, frames, out, duration, size, S, label, said, stats, prov) -> None:
    W, H = size
    x0, y0, x1, y1 = S.box
    tokens = sum(f.size[0] * f.size[1] for f in frames) / 750
    details = sum(1 for f in frames if f.detail_of)
    L = [
        f"# Keyframes — {video.stem}", "",
        f"- **Source:** `{video.name}` · {hms(duration)} · {W}×{H}, content box "
        f"{round((x1 - x0) * S.sx)}×{round((y1 - y0) * S.sy)}",
        f"- **Kept {len(frames)} frames** ({details} of them detail crops), ≈{tokens / 1000:.0f}k "
        f"image tokens — from {stats['states']} stable screens → {stats['pruned']} after pixel "
        f"pruning → {len(frames)} after content cover",
        f"- **Speech column:** {label}",
        f"- **OCR:** RapidOCR on {provider_str(prov)}",
        "- **Read** the JPGs named below with the Read tool, 10–20 per message in parallel — "
        "they are exactly what was saved. `frames.pdf` is the same set with captions, for a "
        "human to flip through.",
        "- `hires/` holds a native-resolution twin of every frame (same file name) for note figures: "
        "**do not Read it** (token cost); `class_figure.py` uses it on its own.",
        "- Each frame is the screen **just before it changed**, so it holds everything added "
        "while it was up. A **detail** frame shows only the part that was new; the rest of that "
        "screen is in the frame it names. *On screen* lists every stretch that content was "
        "visible, including the near-duplicates that were dropped.", "",
    ]
    for f in frames:
        text = " ".join(f.lines[i] for i in shown(f))      # only what the saved image shows
        hint = (text[:160] + "…") if len(text) > 160 else text
        L += [f"## {f.n:03d} · {hms(f.t)} · `{f.path.name}`", "",
              f"- {kind(f)} · {f.size[0]}×{f.size[1]}"
              + (f" (×{f.scale:.2f})" if f.scale < 1 else ""),
              f"- on screen: {seen_str(f)}",
              f"- text: {hint or '— (drawing / no text)'}",
              f"- said: {said(f.t) or '—'}", ""]
    (out / "index.md").write_text("\n".join(L), encoding="utf-8", newline="\n")


def write_frames_json(out: pathlib.Path, frames: list[Frame], prov: dict, said, hires_res) -> None:
    """frames.json stays a plain list (what the downstream scripts and the older outputs use); the OCR
    provider rides on every entry and in full in meta.json.  Written once early (t + region only, so
    the hires regrab can read it) and again at the end with everything."""
    rows = []
    for f in frames:
        row = {"n": f.n, "t": f.t, "file": f.path.name, "region": f.out, "scale": round(f.scale, 2),
               "detail_of": f.detail_of.n if f.detail_of else None, "seen": sorted(set(f.seen)),
               "gain": {"phrases": f.gain[0], "stroke_px": f.gain[1]},
               "text": [f.lines[i] for i in shown(f)], "said": said(f.t) if said else "",
               "ocr_provider": prov.get("label")}
        if hires_res is not None and f.path.name not in {x["file"] for x in hires_res["failed"]}:
            row["hires"] = f"{hr.HIRES_DIR}/{f.path.name}"
        rows.append(row)
    (out / "frames.json").write_text(json.dumps(rows, indent=1), encoding="utf-8", newline="\n")


# ==========================================================================
# main
# ==========================================================================
def main() -> None:
    if os.environ.get("NOTES_NO_VIDEO", "").strip() not in ("", "0"):
        print("video processing is disabled (NOTES_NO_VIDEO is set): nothing was run", flush=True)
        sys.exit(4)
    ap = argparse.ArgumentParser(description="Deduplicated lecture keyframes for the note agent.")
    ap.add_argument("video", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True,
                    help="output directory (everything is written here, nothing elsewhere)")
    ap.add_argument("--whisper", type=pathlib.Path,
                    help="Whisper JSON from transcribe.py: exact speech per frame + transcript.md")
    ap.add_argument("--transcript", type=pathlib.Path,
                    help="plain-text class transcript: proportional speech hints when no --whisper")
    ap.add_argument("--ocr-device", choices=("auto", "gpu", "cpu"), default="auto",
                    help="OCR provider: auto/gpu = DirectML > CUDA > CoreML (if present) > CPU, "
                         "cpu = CPU only; a failing GPU always falls back to the CPU")
    ap.add_argument("--no-pdf", action="store_true", help="skip frames.pdf (the human-facing copy)")
    ap.add_argument("--destroy", type=float, default=0.02,
                    help="share of the screen that must be lost to capture a state (0.02)")
    ap.add_argument("--min-dwell", type=int, default=3, help="seconds a state must stay up (3)")
    ap.add_argument("--text-unit", type=float, default=TEXT_UNIT,
                    help=f"unseen OCR phrases worth one frame ({TEXT_UNIT})")
    ap.add_argument("--px-unit", type=float, default=PX_UNIT,
                    help=f"unseen pen strokes, as a share of the screen, worth one frame ({PX_UNIT})")
    ap.add_argument("--keep-cache", action="store_true",
                    help="keep survey + OCR cache so a re-run skips decoding and OCR")
    ap.add_argument("--no-hires", action="store_true",
                    help="do not write the native-resolution copies in <out>/hires/")
    ap.add_argument("--hires-only", action="store_true",
                    help="only (re)grab <out>/hires/ for an EXISTING --out from its frames.json "
                         "(t + region per frame) and the video; no selection, no OCR; skips copies "
                         "that exist")
    a = ap.parse_args()

    video = a.video.resolve()
    if not video.is_file():
        sys.exit(f"error: no such video: {video}")
    out = a.out.resolve()
    if a.hires_only:
        if not (out / "frames.json").is_file():
            sys.exit(f"error: --hires-only needs an existing frames dir (no frames.json in {out})")
        res = hr.regrab(video, out)
        sys.exit(1 if res["failed"] else 0)
    cache = out / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    size, duration = probe(video)
    sv, stamp_file = cache / "survey.npy", cache / "stamp"
    stamp = f"{video.stat().st_size}-{video.stat().st_mtime_ns}"
    if sv.exists() and stamp_file.exists() and stamp_file.read_text() == stamp:
        A = np.load(sv)
    else:
        for stale in [*cache.glob("cand_*.jpg"), cache / "ocr.json"]:    # another video's leftovers
            stale.unlink(missing_ok=True)
        A = survey(video)
        np.save(sv, A)
        stamp_file.write_text(stamp, encoding="utf-8", newline="\n")
    print(f"[1/6] survey   {len(A)} s of video, {size[0]}x{size[1]}  ({time.time() - t0:.0f}s)")

    cam, box = layout(A)
    S = Screen(A, cam, box, size)
    frames = states(S, a.destroy, a.min_dwell)
    n_states = len(frames)
    print(f"[2/6] states   content box {box} (survey px), webcam masked {int(cam.sum())} px, "
          f"{n_states} stable screens  ({time.time() - t0:.0f}s)")

    frames = prune(S, frames)
    print(f"[3/6] prune    {len(frames)} after pixel subset test  ({time.time() - t0:.0f}s)")

    def fetch(f: Frame) -> None:
        f.crop = trim(S, f)
        f.path = cache / f"cand_{f.t:05d}.jpg"
        if not f.path.exists():
            grab(video, f.t, f.crop, f.path)

    with cf.ThreadPoolExecutor(4) as ex:
        list(ex.map(fetch, frames))
    print(f"[4/6] grab     {len(frames)} native-resolution crops for OCR  ({time.time() - t0:.0f}s)")

    prov = ocr(frames, cache / "ocr.json", a.ocr_device)
    print(f"      ocr provider: {provider_str(prov)}  ({time.time() - t0:.0f}s)")
    for f in frames:
        strokes(S, f)
    n_pruned = len(frames)
    frames = cover(S, frames, a.text_unit, a.px_unit)
    print(f"[5/6] cover    {len(frames)} frames add new content  ({time.time() - t0:.0f}s)")

    for old in [*out.glob("[0-9][0-9][0-9]_*.jpg"), *out.glob("frames*.pdf"),
                out / "index.md", out / "frames.json", out / "transcript.md", out / "meta.json"]:
        old.unlink(missing_ok=True)
    shutil.rmtree(out / hr.HIRES_DIR, ignore_errors=True)       # frame numbers change: stale copies lie
    for i, f in enumerate(frames, 1):
        f.n = i
        if not f.detail_of:
            f.out = f.crop
        f.scale = scale_for(f)

    def save(f: Frame) -> None:
        path = out / f"{f.n:03d}_{hms(f.t).replace(':', '-')}.jpg"
        f.size = grab(video, f.t, f.out, path, f.scale)
        f.path = path

    with cf.ThreadPoolExecutor(4) as ex:
        list(ex.map(save, frames))
    hires_res = None
    if frames and not a.no_hires:
        # native-resolution twin of every saved frame (same region, scale 1.0): the figure source
        write_frames_json(out, frames, {}, None, None)   # regrab reads t + region from frames.json
        hires_res = hr.regrab(video, out)
    label, said, segs = load_speech(duration, a.whisper, a.transcript)
    if frames and not a.no_pdf:        # PyMuPDF refuses to save an empty document
        write_pdf(frames, out / "frames.pdf", duration, said)
    write_index(video, frames, out, duration, size, S, label, said,
                {"states": n_states, "pruned": n_pruned}, prov)
    if segs:
        write_transcript(segs, frames, out / "transcript.md")
    # frames.json stays a plain list (what the downstream scripts and the older outputs use);
    # the OCR provider rides on every entry and in full in meta.json
    write_frames_json(out, frames, prov, said, hires_res)
    tokens = sum(f.size[0] * f.size[1] for f in frames) / 750
    details = sum(1 for f in frames if f.detail_of)
    (out / "meta.json").write_text(json.dumps({
        "video": video.name, "video_bytes": video.stat().st_size, "duration_s": round(duration, 1),
        "source_size": list(size), "content_box": list(box), "states": n_states, "pruned": n_pruned,
        "frames": len(frames), "detail_crops": details, "image_tokens_k": round(tokens / 1000, 1),
        "ocr_provider": prov.get("label"), "ocr": prov, "ocr_device_arg": a.ocr_device,
        "speech": ("whisper" if label.startswith("Whisper") else "transcript" if label.startswith("≈") else "none"),
        "speech_label": label, "whisper": str(a.whisper) if label.startswith("Whisper") else None,
        "transcript": str(a.transcript) if a.transcript and a.transcript.exists() else None,
        "params": {"destroy": a.destroy, "min_dwell": a.min_dwell, "text_unit": a.text_unit,
                   "px_unit": a.px_unit},
        "pdf": bool(frames and not a.no_pdf),
        "hires": ({"dir": hr.HIRES_DIR, "frames": hires_res["total"] - len(hires_res["failed"]),
                   "failed": len(hires_res["failed"]), "scale": 1.0, "jpeg_q": hr.QUALITY}
                  if hires_res else None), "elapsed_s": round(time.time() - t0)},
        indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    if not a.keep_cache:
        shutil.rmtree(cache, ignore_errors=True)
    print(f"[6/6] package  {len(frames)} frames ({details} detail "
          f"crops), ≈{tokens / 1000:.0f}k image tokens, ocr={prov.get('label')} -> {out}  "
          f"({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
