"""Native-resolution copies of keyframes (PLAN 17, "Figure resolution").

keyframes.py saves the frames the *agent* reads downscaled (0.5-1.0x) to keep image tokens low.  Class
figures are cropped from those frames, so they came out blurry.  The fix is two copies of every saved
frame: the token-budget JPG (unchanged) and ``<frames dir>/hires/<same file name>`` -- same crop region,
scale 1.0, JPEG q:v 2.  Agents never read ``hires/``; ``class_figure.py`` prefers it.

``regrab`` rebuilds only the hires copies of an existing frames dir from its ``frames.json`` (per frame:
``t`` second, ``region`` = source-px x,y,w,h, ``file``) plus the video, without re-running selection.
Idempotent: a copy that exists is skipped.  CPU ffmpeg only (the bundled imageio-ffmpeg) - no GPU lock.
``imageio_ffmpeg`` is imported lazily, so this module stays importable stdlib-only (status/paths).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import subprocess
from pathlib import Path

HIRES_DIR = "hires"
LEAD = 15          # seek this far early so decoding passes a keyframe (same as keyframes.grab)
QUALITY = 2        # ffmpeg -q:v for JPEG (2 = visually lossless); PNG names are written as PNG


def hires_dir(frames_dir: Path) -> Path:
    return Path(frames_dir) / HIRES_DIR


def load_records(frames_dir: Path) -> list[dict]:
    """The per-frame records of frames.json (a list, or {"frames": [...]})."""
    try:
        doc = json.loads((Path(frames_dir) / "frames.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    if isinstance(doc, dict):
        doc = doc.get("frames", [])
    return [r for r in doc if isinstance(r, dict) and r.get("file")] if isinstance(doc, list) else []


def status(frames_dir: Path) -> dict:
    """{"total", "have", "missing": [file names]} for the hires copies of a frames dir."""
    recs = load_records(frames_dir)
    hd = hires_dir(frames_dir)
    missing = [r["file"] for r in recs if not ((hd / r["file"]).is_file() and (hd / r["file"]).stat().st_size > 0)]
    return {"total": len(recs), "have": len(recs) - len(missing), "missing": missing}


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def grab(video: Path, t: float, region, path: Path, ffmpeg: str | None = None) -> bool:
    """One frame at second ``t``, cropped to ``region`` (x, y, w, h source px), scale 1.0 -> ``path``."""
    x, y, w, h = (int(v) for v in region)
    if w < 2 or h < 2:
        return False
    ffmpeg = ffmpeg or _ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".part" + path.suffix)
    t = int(t)
    q = ["-q:v", str(QUALITY)] if path.suffix.lower() in (".jpg", ".jpeg") else []
    for at in (t, t - 1):                 # the very last second can sit past the final frame
        if at < 0:
            break
        lead = min(LEAD, at)
        tmp.unlink(missing_ok=True)
        subprocess.run([ffmpeg, "-v", "fatal", "-y", "-ss", str(at - lead), "-i", str(video), "-ss", str(lead),
                        "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y}", *q, str(tmp)], check=True)
        if tmp.is_file() and tmp.stat().st_size > 0:
            tmp.replace(path)
            return True
    tmp.unlink(missing_ok=True)
    return False


def regrab(video: Path, frames_dir: Path, workers: int = 4, log=print) -> dict:
    """Write every missing ``hires/<file>`` of ``frames_dir`` from ``video``.  Returns
    ``{"total", "made", "skipped", "failed": [{"file", "why"}]}``; ``hires/hires.json`` records the run."""
    frames_dir, video = Path(frames_dir), Path(video)
    recs = load_records(frames_dir)
    hd = hires_dir(frames_dir)
    out = {"total": len(recs), "made": 0, "skipped": 0, "failed": []}
    ffmpeg = _ffmpeg()
    todo = []
    for r in recs:
        p = hd / r["file"]
        if p.is_file() and p.stat().st_size > 0:
            out["skipped"] += 1
        elif not (isinstance(r.get("region"), (list, tuple)) and len(r["region"]) == 4 and "t" in r):
            out["failed"].append({"file": r["file"], "why": "frames.json record has no t/region"})
        else:
            todo.append(r)

    def one(r: dict):
        try:
            ok = grab(video, r["t"], r["region"], hd / r["file"], ffmpeg)
            return r, None if ok else "ffmpeg wrote no frame"
        except Exception as e:  # noqa: BLE001
            return r, f"{type(e).__name__}: {str(e)[:100]}"

    if todo:
        hd.mkdir(parents=True, exist_ok=True)
        with cf.ThreadPoolExecutor(max(1, workers)) as ex:
            for r, why in ex.map(one, todo):
                if why:
                    out["failed"].append({"file": r["file"], "why": why})
                else:
                    out["made"] += 1
    if recs and (hd.is_dir()):
        (hd / "hires.json").write_text(json.dumps({
            "video": video.name, "quality": QUALITY, "scale": 1.0, "frames": out["total"],
            "have": status(frames_dir)["have"], "failed": out["failed"]}, indent=1), encoding="utf-8", newline="\n")
    log(f"hires: {out['made']} made, {out['skipped']} already there, {len(out['failed'])} failed "
        f"of {out['total']} -> {hd}")
    return out


def find_for(path: Path) -> Path | None:
    """The hires twin of a frame / page image, if one exists.

    ``<D>/NNN_hh-mm-ss.jpg`` -> ``<D>/hires/<same name>``;  a slide page ``<out>/pages/NNN.png`` ->
    ``<out>/hires/NNN.png`` (extract_pdf.py --hires-dpi)."""
    path = Path(path)
    if path.parent.name == HIRES_DIR:
        return None
    cands = [path.parent / HIRES_DIR / path.name]
    if path.parent.name == "pages":
        cands.append(path.parent.parent / HIRES_DIR / path.name)
    return next((c for c in cands if c.is_file() and c.stat().st_size > 0), None)
