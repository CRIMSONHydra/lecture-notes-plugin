# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf", "imageio-ffmpeg", "pillow", "numpy"]
# ///
"""Self-test for PLAN 12.3 (archive_video.py) and 12.2 (class_figure.py).

    uv run tests/selftest_archive.py [-k SUBSTRING]

Fixture projects live under <plugin>/test-output/_a1/. The video test uses a tiny generated clip (bundled
ffmpeg) plus fake ingest outputs; class_figure is run on synthetic board/terminal images and, when the cpp
repo exists, on real frames (read-only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import traceback
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
# optional read-only real subject projects for extra regression tests; every test that needs one skips when absent
SUBJ_CPP = os.environ.get("NOTES_TEST_CPP_ROOT", "/nonexistent/cpp-subject")
SUBJ_FDE = os.environ.get("NOTES_TEST_FDE_ROOT", "/nonexistent/fde-subject")
SUBJ_CV = os.environ.get("NOTES_TEST_CV_ROOT", "/nonexistent/cv-subject")
SCRIPTS = PLUGIN / "scripts"
WORK = PLUGIN / "test-output" / "_a1" / "selftest"
CPP_FRAMES = Path(SUBJ_CPP + "/resources/frames/Pointers_and_References_-_Class_Scaler_Academy")
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: got {got!r}, want {want!r}"


def ok(cond, label=""):
    assert cond, label


def run(script, *args, timeout=600):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env.pop("LECTURE_NOTES_DATA", None)
    return subprocess.run([UV, "run", "--quiet", "--script", str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                          timeout=timeout)


def rmtree(p: Path):
    def onerr(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if p.exists():
        shutil.rmtree(p, onerror=onerr)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ archive_video fixtures
STEM = "Clip_-_Class_Scaler_Academy"
STEM3 = "Other_-_Class_Scaler_Academy"


def make_clip(path: Path):
    import imageio_ffmpeg
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=white:s=160x90:d=2:r=5", "-pix_fmt", "yuv420p", str(path)],
                       capture_output=True, text=True)
    ok(r.returncode == 0 and path.is_file(), r.stderr)


def fake_ingest(root: Path, nn: str, rec: Path, *, frames=True, whisper=True, wrong_meta=False):
    vd = root / ".notes-plugin-internal" / "work" / "sessions" / nn / "video"
    if frames:
        fd = vd / "frames"
        fd.mkdir(parents=True, exist_ok=True)
        (fd / "index.md").write_text("# frames\n", encoding="utf-8")
        (fd / "001_00-00-01.jpg").write_bytes(b"\xff\xd8fakejpg\xff\xd9")
        (fd / "transcript.md").write_text("speech\n", encoding="utf-8")
        (fd / "frames.json").write_text(json.dumps([{"n": 1, "t": 1.0, "file": "001_00-00-01.jpg", "region": [0, 0, 160, 90]}]), encoding="utf-8")
        (fd / "meta.json").write_text(json.dumps({"video": ("Wrong.mp4" if wrong_meta else rec.name),
                                                  "video_bytes": rec.stat().st_size, "frames": 1}), encoding="utf-8")
    if whisper:
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "whisper.json").write_text(json.dumps({"video": rec.name, "model": "x", "duration": 2.0,
                                                     "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]}),
                                         encoding="utf-8")


def project() -> tuple[Path, Path, Path]:
    root = WORK / "proj"
    rmtree(root)
    (root / ".notes-plugin-internal").mkdir(parents=True)
    (root / ".notes-plugin-internal" / "profile.md").write_text(
        "---\nschema: 1\nid: t\ntitle: Test\nshort: T\nprefix: TT\nsessions:\n"
        f'  - {{n: 2, title: "Clip", stem: "{STEM}"}}\n  - {{n: 3, title: "Other", stem: "{STEM3}"}}\n---\n\n# body\n',
        encoding="utf-8")
    rec = root / "resources" / "videos" / f"{STEM}.mp4"
    rec3 = root / "resources" / "videos" / f"{STEM3}.mp4"
    make_clip(rec)
    make_clip(rec3)
    fake_ingest(root, "02", rec)
    return root, rec, rec3


def av(root, *args):
    return run("archive_video.py", "--project", root, "--data-dir", WORK / "dd", *args)


def resolve(root, n):
    out = WORK / f"resolve-{n}.json"
    out.unlink(missing_ok=True)
    r = run("resolve_inputs.py", "--project", root, "--data-dir", WORK / "dd", "--session", n, "--json", out,
            "--dry-run")
    ok(r.returncode == 0, r.stdout + r.stderr)
    return json.loads(out.read_text(encoding="utf-8"))


@test
def t_archive_flow():
    root, rec, rec3 = project()
    res = root / "resources"
    # 1. dry run: validates, plans, writes nothing
    r = av(root, "--session", 2, "--dry-run")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok("would" in r.stdout or "needs --confirm" in r.stdout, r.stdout)
    ok(not (res / "frames").exists() and rec.is_file(), "dry run wrote something")
    # 2. archive without --confirm: copies, keeps the recording
    r = av(root, "--session", 2)
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok((res / "frames" / STEM / "index.md").is_file() and (res / f"{STEM}.whisper.json").is_file(), "archive missing")
    ok(rec.is_file(), "recording deleted without --confirm")
    ok("--confirm" in r.stdout, r.stdout)
    # 3. second run: identical archive skipped
    r = av(root, "--session", 2)
    ok(r.stdout.count("skipped-identical") == 2, r.stdout)
    # 4. a different archive is refused without --force
    (res / "frames" / STEM / "transcript.md").write_text("tampered\n", encoding="utf-8")
    r = av(root, "--session", 2)
    eq(r.returncode, 1, r.stdout)
    ok("DIFFERENT" in r.stdout and rec.is_file(), r.stdout)
    ok((res / "frames" / STEM / "transcript.md").read_text(encoding="utf-8") == "tampered\n", "overwritten without --force")
    r = av(root, "--session", 2, "--force")
    eq(r.returncode, 0, r.stdout + r.stderr)
    eq((res / "frames" / STEM / "transcript.md").read_text(encoding="utf-8"), "speech\n", "force replaced")
    # 5. --confirm deletes, prints bytes
    size = rec.stat().st_size
    r = av(root, "--session", 2, "--confirm", "--json", WORK / "av.json")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok(not rec.exists(), "recording still there")
    ok(f"{size:,}" in r.stdout, r.stdout)
    eq(json.loads((WORK / "av.json").read_text(encoding="utf-8"))["freed_bytes"], size, "freed")
    # 6. re-resolve: reuse, archived frames + whisper, no recording
    inp = resolve(root, 2)
    eq(inp["video_action"], "reuse", "video_action")
    eq(inp["recording"], None, "recording")
    ok(Path(inp["frames"]["dir"]).as_posix().endswith(f"resources/frames/{STEM}"), inp["frames"])
    ok(inp["whisper"]["path"].endswith(f"{STEM}.whisper.json") and inp["whisper"]["source"] == "resources", inp["whisper"])
    # 7. nothing else in resources/ was touched
    other = [p.relative_to(res).as_posix() for p in res.rglob("*") if p.is_file()
             and not p.relative_to(res).as_posix().startswith(("frames/", f"{STEM}.whisper"))]
    eq(sorted(other), [f"videos/{STEM3}.mp4"], "resources/ otherwise untouched")
    # 8. already archived and no recording -> nothing to do, exit 0
    r = av(root, "--session", 2, "--confirm")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok("no-recording" in r.stdout, r.stdout)


@test
def t_archive_validate_and_all():
    root, rec, rec3 = project()
    res = root / "resources"
    # session 3: no ingest at all -> explicit session fails, --all skips it
    r = av(root, "--session", 3, "--confirm")
    eq(r.returncode, 1, r.stdout)
    ok(rec3.is_file(), "deleted an un-ingested recording")
    # mismatched meta.json -> refused, recording kept
    fake_ingest(root, "03", rec3, wrong_meta=True)
    r = av(root, "--session", 3, "--confirm")
    eq(r.returncode, 1, r.stdout + r.stderr)
    ok("meta.json is for" in r.stdout and rec3.is_file(), r.stdout)
    # a recording that changed size since ingest -> refused
    shutil.rmtree(root / ".notes-plugin-internal/work/sessions/03/video/frames")
    fake_ingest(root, "03", rec3)
    meta = root / ".notes-plugin-internal/work/sessions/03/video/frames/meta.json"
    m = json.loads(meta.read_text(encoding="utf-8"))
    m["video_bytes"] += 1
    meta.write_text(json.dumps(m), encoding="utf-8")
    r = av(root, "--session", 3, "--confirm")
    eq(r.returncode, 1, r.stdout)
    ok("video_bytes" in r.stdout and rec3.is_file(), r.stdout)
    # whisper with no segments -> refused
    shutil.rmtree(root / ".notes-plugin-internal/work/sessions/03/video/frames")
    fake_ingest(root, "03", rec3)
    wj = root / ".notes-plugin-internal/work/sessions/03/video/whisper.json"
    wj.write_text(json.dumps({"video": rec3.name, "segments": []}), encoding="utf-8")
    r = av(root, "--session", 3, "--confirm")
    eq(r.returncode, 1, r.stdout)
    ok(rec3.is_file(), "deleted with empty whisper")
    # --all: session 2 complete, session 3 incomplete -> 2 deleted, 3 skipped, exit 0
    r = av(root, "--all", "--confirm")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok(not rec.exists() and rec3.is_file(), r.stdout)
    ok("skipped:" in r.stdout, r.stdout)
    # fixing session 3 (valid whisper) then --all handles it, and the recording is gone
    fake_ingest(root, "03", rec3)
    r = av(root, "--all", "--confirm")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok(not rec3.exists() and (root / "resources" / "frames" / STEM3 / "index.md").is_file(), r.stdout)


@test
def t_archive_dry_run_confirm_deletes_nothing():
    root, rec, _ = project()
    r = av(root, "--all", "--dry-run", "--confirm")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok(rec.is_file() and not (root / "resources" / "frames").exists(), "dry run changed resources/")


@test
def t_archive_hires():
    """PLAN 17: the archive gets hires/ before the recording is deleted; no hires -> the recording stays."""
    root, rec, _ = project()
    r = av(root, "--session", 2, "--dry-run", "--confirm")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok("hires: 0/1 present; would regrab 1" in r.stdout, r.stdout)
    r = av(root, "--session", 2, "--confirm")
    eq(r.returncode, 0, r.stdout + r.stderr)
    h = root / "resources" / "frames" / STEM / "hires" / "001_00-00-01.jpg"
    ok(h.is_file() and not rec.exists(), "hires copy made, then the recording deleted: " + r.stdout)
    from PIL import Image
    eq(Image.open(h).size, (160, 90), "hires copy is the frame's region at scale 1.0")
    # an archive without regions cannot be regrabbed: the recording must survive
    root, rec, _ = project()
    fj = root / ".notes-plugin-internal/work/sessions/02/video/frames/frames.json"
    fj.write_text(json.dumps([{"n": 1, "t": 1.0, "file": "001_00-00-01.jpg"}]), encoding="utf-8")
    r = av(root, "--session", 2, "--confirm")
    eq(r.returncode, 1, r.stdout + r.stderr)
    ok(rec.is_file() and "hires-missing" in r.stdout, "recording was deleted without hires: " + r.stdout)
    r = av(root, "--session", 2, "--confirm", "--no-hires")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok(not rec.exists(), "--no-hires waives the requirement")
    # re-running over an archive that now has hires/ is still 'identical' (hires/ is not part of identity)
    root, rec, _ = project()
    av(root, "--session", 2)
    r = av(root, "--session", 2)
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok("skipped-identical" in r.stdout, r.stdout)


# ------------------------------------------------------------------ class_figure
def synth_board(path: Path):
    """A white board with faint grid, black + blue strokes, wrapped in black letterbox bars."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (800, 500), (0, 0, 0))
    board = Image.new("RGB", (640, 460), (252, 252, 252))
    d = ImageDraw.Draw(board)
    for x in range(0, 640, 20):
        d.line([(x, 0), (x, 460)], fill=(215, 222, 236), width=1)
    for y in range(0, 460, 20):
        d.line([(0, y), (640, y)], fill=(215, 222, 236), width=1)
    d.line([(60, 100), (300, 140)], fill=(15, 15, 15), width=4)
    d.line([(60, 200), (400, 210)], fill=(40, 40, 40), width=1)           # a thin pen line
    d.ellipse([350, 250, 450, 330], outline=(20, 70, 220), width=3)       # blue ink
    im.paste(board, (80, 20))
    im.save(path, "JPEG", quality=88)


def synth_terminal(path: Path):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (800, 500), (30, 8, 28))
    d = ImageDraw.Draw(im)
    for i in range(12):
        d.rectangle([40, 30 + i * 36, 40 + 300 + (i % 3) * 80, 44 + i * 36], fill=(235, 235, 235))
    im.save(path, "JPEG", quality=88)


def px(path: Path):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.int32)


@test
def t_class_figure_synthetic():
    d = WORK / "cf"
    rmtree(d)
    d.mkdir(parents=True)
    synth_board(d / "board.jpg")
    synth_terminal(d / "term.jpg")
    r = run("class_figure.py", "--frame", d / "board.jpg", "--out", d / "b.jpg", "--json", d / "b.json")
    eq(r.returncode, 0, r.stdout + r.stderr)
    j = json.loads((d / "b.json").read_text(encoding="utf-8"))["figures"][0]
    eq((j["background"], j["whitened"]), ("light", True), "detect")
    a = px(d / "b.jpg")
    h, w, _ = a.shape
    ok(w <= 1400 and w >= 400, f"width {w}")
    ok(w < 800 * 1.6, "letterbox bars were not trimmed")
    lum = a.mean(axis=2)
    ok((lum > 245).mean() > 0.9, f"background not white enough: {(lum > 245).mean():.2f}")
    ok(lum.min() < 60, "black ink lost")
    # blue ink survives with its hue (blue channel >> red)
    blue = (a[..., 2] - a[..., 0]) > 90
    ok(blue.sum() > 100, f"coloured ink destroyed ({blue.sum()} px)")
    # the thin line is still visible (some px clearly darker than the background in its rows)
    # no grid left: pixels in the faint-blue grid range are nearly absent
    grid = ((a[..., 0] > 195) & (a[..., 0] < 232) & ((a[..., 2] - a[..., 0]) > 6) & ((a[..., 2] - a[..., 0]) < 30))
    ok(grid.mean() < 0.01, f"grid remains: {grid.mean():.3f}")
    # determinism
    run("class_figure.py", "--frame", d / "board.jpg", "--out", d / "b2.jpg")
    eq(sha(d / "b.jpg"), sha(d / "b2.jpg"), "deterministic")
    # dark screen is left alone
    r = run("class_figure.py", "--frame", d / "term.jpg", "--out", d / "t.png", "--json", d / "t.json")
    eq(r.returncode, 0, r.stdout + r.stderr)
    j = json.loads((d / "t.json").read_text(encoding="utf-8"))["figures"][0]
    eq((j["background"], j["whitened"], j["format"]), ("dark", False, "png"), "dark")
    t = px(d / "t.png")
    ok(t.mean() < 120, f"terminal was whitened (mean {t.mean():.0f})")
    # crop fractions + errors
    r = run("class_figure.py", "--frame", d / "board.jpg", "--out", d / "c.jpg", "--crop-frac", "0.1,0.1,0.5,0.5",
            "--max-width", 300, "--json", d / "c.json")
    eq(r.returncode, 0, r.stderr)
    ok(json.loads((d / "c.json").read_text(encoding="utf-8"))["figures"][0]["size"][0] <= 300, "max-width")
    r = run("class_figure.py", "--frame", d / "board.jpg", "--out", d / "x.jpg", "--crop-frac", "0.9,0.1,0.1,0.5")
    eq(r.returncode, 2, "bad crop")
    r = run("class_figure.py", "--frame", d / "nope.jpg", "--out", d / "x.jpg")
    eq(r.returncode, 2, "missing frame")


@test
def t_class_figure_batch():
    d = WORK / "cfb"
    rmtree(d)
    (d / "frames").mkdir(parents=True)
    synth_board(d / "frames" / "007_00-12-00.jpg")
    synth_terminal(d / "frames" / "008_00-13-00.jpg")
    (d / "vis.yaml").write_text(
        "- {id: V01, frame: F007, crop: [0.0, 0.0, 1.0, 1.0], treatment: embed, slug: Stack Frame}\n"
        "- {id: V02, frame: 8, treatment: text, slug: terminal}\n"
        "- {id: V03, frame: 008_00-13-00.jpg, treatment: both, slug: term-photo, whiten: false}\n"
        "- {id: V04, page: slides/L2/pages/003.png, treatment: embed, slug: slide-three}\n", encoding="utf-8")
    (d / "slides/L2/pages").mkdir(parents=True)
    from PIL import Image
    Image.open(d / "frames" / "007_00-12-00.jpg").save(d / "slides/L2/pages/003.png")
    r = run("class_figure.py", "--from-visuals", d / "vis.yaml", "--frames-dir", d / "frames", "--out-dir", d / "out",
            "--nn", 2, "--sidecar", d / "s.visuals.json", "--session-dir", d)
    eq(r.returncode, 0, r.stdout + r.stderr)
    eq(sorted(p.name for p in (d / "out").iterdir()), ["02-class-slide-three.jpg", "02-class-stack-frame.jpg", "02-class-term-photo.jpg"], "outputs")
    side = json.loads((d / "s.visuals.json").read_text(encoding="utf-8"))
    eq(side, {"V01": {"files": ["02-class-stack-frame.jpg"], "treatment": "embed"},
              "V03": {"files": ["02-class-term-photo.jpg"], "treatment": "both"},
              "V04": {"files": ["02-class-slide-three.jpg"], "treatment": "embed"}}, "sidecar")


@test
def t_class_figure_hires_preferred():
    """PLAN 17: a hires twin (frames/hires/<name>, slides/<deck>/hires/NNN.png) is cropped instead of the
    downscaled frame, with the same crop fractions; a 1280-wide source gives a > 700 px figure."""
    from PIL import Image, ImageDraw
    d = WORK / "cfh"
    rmtree(d)
    (d / "frames" / "hires").mkdir(parents=True)
    (d / "slides/L2/pages").mkdir(parents=True)
    (d / "slides/L2/hires").mkdir(parents=True)

    def board(w, h):
        k = w / 1280
        im = Image.new("RGB", (w, h), (0, 0, 0))
        bd = Image.new("RGB", (round(1100 * k), round(660 * k)), (252, 252, 252))
        dr = ImageDraw.Draw(bd)
        dr.line([(60 * k, 100 * k), (700 * k, 160 * k)], fill=(15, 15, 15), width=max(1, round(4 * k)))
        dr.ellipse([600 * k, 300 * k, 820 * k, 460 * k], outline=(20, 70, 220), width=max(1, round(3 * k)))
        im.paste(bd, (round(90 * k), round(30 * k)))
        return im
    board(1280, 720).save(d / "frames" / "hires" / "007_00-12-00.jpg", quality=95)
    board(512, 288).save(d / "frames" / "007_00-12-00.jpg", quality=85)        # what the agent reads
    board(1280, 720).save(d / "slides/L2/hires/003.png")
    board(512, 288).save(d / "slides/L2/pages/003.png")
    (d / "vis.yaml").write_text(
        "- {id: V01, frame: F007, crop: [0.1, 0.1, 0.9, 0.9], treatment: embed, slug: frame}\n"
        "- {id: V02, page: slides/L2/pages/003.png, crop: [0.1, 0.1, 0.9, 0.9], treatment: embed, slug: page}\n",
        encoding="utf-8")
    args = ["--from-visuals", d / "vis.yaml", "--frames-dir", d / "frames", "--session-dir", d, "--nn", 2]
    r = run("class_figure.py", *args, "--out-dir", d / "out", "--json", d / "o.json")
    eq(r.returncode, 0, r.stdout + r.stderr)
    figs = {f["id"]: f for f in json.loads((d / "o.json").read_text(encoding="utf-8"))["figures"]}
    for v in ("V01", "V02"):
        eq((figs[v]["source"], figs[v]["source_size"]), ("hires", [1280, 720]), f"{v} source")
        ok(figs[v]["size"][0] > 700 and figs[v]["enlarged"] == 1.0, f"{v} width {figs[v]['size']}")
    r = run("class_figure.py", *args, "--out-dir", d / "lo", "--json", d / "l.json", "--no-hires")
    eq(r.returncode, 0, r.stdout + r.stderr)
    lo = {f["id"]: f for f in json.loads((d / "l.json").read_text(encoding="utf-8"))["figures"]}
    eq((lo["V01"]["source"], lo["V01"]["source_size"]), ("lowres", [512, 288]), "--no-hires")
    ok(lo["V01"]["enlarged"] > 1.0, "the low-res source is the one that gets enlarged")
    ok(figs["V01"]["size"][0] > lo["V01"]["size"][0], f"hires not wider: {figs['V01']['size']} vs {lo['V01']['size']}")
    # the same crop lands on the same content: the figures have (about) the same aspect ratio
    a1, a2 = (figs["V01"]["size"][0] / figs["V01"]["size"][1]), (lo["V01"]["size"][0] / lo["V01"]["size"][1])
    ok(abs(a1 - a2) < 0.03, f"aspect {a1:.3f} vs {a2:.3f}")


@test
def t_class_figure_real_frames():
    if not CPP_FRAMES.is_dir():
        print("      (skipped: cpp frames not present)")
        return
    d = WORK / "cfr"
    rmtree(d)
    d.mkdir(parents=True)
    board = next(CPP_FRAMES.glob("123_*.jpg"))
    term = next(CPP_FRAMES.glob("022_*.jpg"))
    r = run("class_figure.py", "--frame", board, "--out", d / "board.jpg", "--crop-frac", "0.10,0.10,0.84,0.92",
            "--json", d / "b.json")
    eq(r.returncode, 0, r.stderr)
    eq(json.loads((d / "b.json").read_text(encoding="utf-8"))["figures"][0]["whitened"], True, "board whitened")
    a = px(d / "board.jpg").mean(axis=2)
    ok((a > 245).mean() > 0.7, "real board not white")
    r = run("class_figure.py", "--frame", term, "--out", d / "term.jpg", "--json", d / "t.json")
    eq(json.loads((d / "t.json").read_text(encoding="utf-8"))["figures"][0]["whitened"], False, "terminal untouched")
    ok(px(d / "term.jpg").mean() < 120, "real terminal was whitened")


@test
def t_init_existing_folder():
    """PLAN 12.4: init in an existing project touches only .notes-plugin-internal/, notes/<tier>/, pdf/<tier>/ and .gitignore."""
    root = WORK / "existing"
    rmtree(root)
    for rel, txt in (("notes/01-intro.md", "# hand note"), ("notes/README.md", "idx"), ("pdf/build.py", "x=1"),
                     ("CLAUDE.md", "rules"), ("resources/Intro.txt", "hello"), (".gitignore", "resources/" + chr(10))):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(txt, encoding="utf-8")
    before = {p.relative_to(root).as_posix(): sha(p) for p in root.rglob("*") if p.is_file()}
    r = run("init_project.py", "--project", root, "--profile", "cpp", "--data-dir", WORK / "dd")
    eq(r.returncode, 0, r.stdout + r.stderr)
    for rel, h in before.items():
        if rel == ".gitignore":
            continue
        eq(sha(root / rel), h, f"{rel} was modified")
    ok(not (root / "resources" / "README.md").exists() and not (root / "resources" / "videos").exists(),
       "wrote into resources/")
    ok((root / "notes/basic").is_dir() and (root / "pdf/advanced").is_dir() and (root / ".notes-plugin-internal/profile.md").is_file(), "tier dirs")
    gi = (root / ".gitignore").read_text(encoding="utf-8").split()
    ok(gi == ["resources/", ".notes-plugin-internal/work/"], gi)
    new = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
                 and p.relative_to(root).as_posix() not in before)
    ok(all(x.startswith(".notes-plugin-internal/") or x == ".gitattributes" for x in new), new)   # init also adds .gitattributes
    # guard roots: tier subfolders + .notes-plugin-internal writable, the rest of the project protected
    sys_path = str(SCRIPTS)
    sys.path.insert(0, sys_path)
    from notes_lib import guard as g, layout as lo, profile as pr
    lay = lo.Layout(root)
    prof_ = pr.load_profile(None, project=root)
    prof_["prior_notes_external"] = ["notes"]
    protect, allow = lay.guard_roots(prof_)
    gd = {"protect": protect, "allow": allow}
    ok(g.check_path(root / "notes/basic/02-x.md", gd) is None, "notes/basic writable")
    ok(g.check_path(root / "pdf/advanced/x.pdf", gd) is None, "pdf/advanced writable")
    ok(g.check_path(root / ".notes-plugin-internal/work/x.json", gd) is None, ".notes writable")
    for rel in ("notes/01-intro.md", "pdf/build.py", "CLAUDE.md", "resources/Intro.txt", "video/x.py"):
        ok(g.check_path(root / rel, gd) is not None, f"{rel} must be protected")


@test
def t_class_figure_canvas_border():
    """A light canvas inside a dark frame: no dark border survives (also after a crop that includes it)."""
    from PIL import Image, ImageDraw
    d = WORK / "cfc"
    rmtree(d)
    d.mkdir(parents=True)
    im = Image.new("RGB", (640, 400), (10, 10, 10))
    ImageDraw.Draw(im).rectangle([40, 30, 600, 370], fill=(250, 250, 250))
    ImageDraw.Draw(im).line([(100, 100), (500, 250)], fill=(0, 0, 0), width=5)
    im.save(d / "f.jpg", quality=90)
    for crop in ([], ["--crop-frac", "0.0,0.0,1.0,1.0"]):
        r = run("class_figure.py", "--frame", d / "f.jpg", "--out", d / "o.jpg", *crop)
        eq(r.returncode, 0, r.stderr)
        a = px(d / "o.jpg").mean(axis=2)
        edge = [a[0, :], a[-1, :], a[:, 0], a[:, -1]]
        ok(all(e.min() > 200 for e in edge), "dark border survived")
        ok(a.min() < 60, "ink lost")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    a = ap.parse_args()
    rmtree(WORK)
    WORK.mkdir(parents=True)
    bad = []
    for fn in TESTS:
        if a.k not in fn.__name__:
            continue
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {fn.__name__}  ({time.time() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            bad.append(fn.__name__)
            print(f"FAIL  {fn.__name__}  ({time.time() - t0:.1f}s)\n      {e}\n{traceback.format_exc(limit=5)}")
    print(f"\n{len(TESTS) - len(bad)}/{len(TESTS)} passed" + (f"; FAILED: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
