# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf", "pillow", "numpy", "markdown-it-py>=3", "mdit-py-plugins", "pygments"]
# ///
"""Self-test for PLAN 16 (improvement round): content-aware crops, GPU/ledger locks, concept map, sweep markers,
multi-session discovery with data/exercise/reference kinds, FDE dry run, corrections calibration, stats.

    uv run tests/selftest_improve.py [-k SUBSTRING]

Fixtures live under <plugin>/test-output/_i1/. The cpp and FDE subject folders are only READ (real-frame crop
regression, real FDE scan); both tests skip with a notice when the folders are absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
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
WORK = PLUGIN / "test-output" / "_i1"
CPP = Path(SUBJ_CPP + "")
FRAMES = CPP / "resources" / "frames" / "Memory_Layout_-_Class_Scaler_Academy"
VISUALS = CPP / ".notes" / "work" / "meta" / "basic" / "02-first-program-assembly-stack-frame-endianness.visuals.yaml"
FDE = Path(SUBJ_FDE)
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
sys.path.insert(0, str(SCRIPTS))
TESTS = []
SKIPS = []


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
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 16.1 crops
def board(size=(800, 500), box=(80, 20, 720, 480)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, (0, 0, 0))
    b = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), (252, 252, 252))
    im.paste(b, box[:2])
    return im, ImageDraw.Draw(im)


def bars(d, x0, y, n=6, w=40, gap=14, h=8, fill=(15, 15, 15)):
    """A 'sentence': n word-sized bars on one line. Returns its x-extent."""
    x = x0
    for _ in range(n):
        d.rectangle([x, y, x + w, y + h], fill=fill)
        x += w + gap
    return x0, x - gap


def cf(frame, out, *args):
    r = run("class_figure.py", "--frame", frame, "--out", out, "--json", Path(out).with_suffix(".json"), *args)
    eq(r.returncode, 0, r.stdout + r.stderr)
    return jload(Path(out).with_suffix(".json"))["figures"][0]


@test
def t_crop_expands_for_cut_sentence():
    d = WORK / "crop1"
    rmtree(d)
    d.mkdir(parents=True)
    im, dr = board()
    bars(dr, 150, 240)                                         # sentence from x=150 to x=474
    dr.rectangle([150, 120, 240, 160], outline=(20, 20, 20), width=4)          # a box well inside the crop
    im.save(d / "f.png")
    # the requested crop ends at frame x=320: it cuts the sentence in the middle of a word
    j = cf(d / "f.png", d / "o.png", "--crop-frac", "0.10,0.20,0.40,0.70")
    ok(j["expanded_for_cut_ink"], f"flag: {j}")
    fb = j["final_box"]
    ok(fb[0] <= 150 and fb[2] >= 460, f"sentence 150..460 not fully inside final box {fb}")
    ok(fb[2] > j["requested_box"][2], "box did not grow")
    ok(not j["palette_removed"], "no palette here")
    # without cut ink nothing grows: a crop that already holds the sentence is not expanded
    j2 = cf(d / "f.png", d / "o2.png", "--crop-frac", "0.10,0.20,0.80,0.70")
    ok(not j2["expanded_for_cut_ink"], f"expanded though nothing was cut: {j2}")
    # determinism: same input, same bytes
    cf(d / "f.png", d / "o3.png", "--crop-frac", "0.10,0.20,0.40,0.70")
    eq(sha(d / "o.png"), sha(d / "o3.png"), "deterministic")


@test
def t_crop_trims_loose_margins():
    d = WORK / "crop2"
    rmtree(d)
    d.mkdir(parents=True)
    im, dr = board()
    bars(dr, 200, 240, n=3)
    im.save(d / "f.png")
    j = cf(d / "f.png", d / "o.png", "--crop-frac", "0.05,0.05,0.95,0.95")   # far too loose
    ok(j["trimmed"], f"not trimmed: {j}")
    rb, fb = j["requested_box"], j["final_box"]
    ok((fb[2] - fb[0]) * (fb[3] - fb[1]) < 0.5 * (rb[2] - rb[0]) * (rb[3] - rb[1]), f"box barely shrank {rb} -> {fb}")
    ok(fb[0] <= 200 and fb[2] >= 200 + 3 * 54 - 14, "trim cut into the ink")


def palette(dr, x, y0=150, n=6, step=30):
    cols = [(0, 60, 220), (200, 0, 160), (200, 20, 20), (90, 0, 150), (20, 20, 20), (0, 150, 60)]
    for i in range(n):
        cy = y0 + i * step
        dr.ellipse([x, cy, x + 14, cy + 14], fill=cols[i % len(cols)])
        dr.rectangle([x + 22, cy + 3, x + 28, cy + 11], outline=(150, 150, 150))      # small grey icon


@test
def t_palette_strip_removed_only_across_a_gutter():
    d = WORK / "pal"
    rmtree(d)
    d.mkdir(parents=True)
    im, dr = board()
    bars(dr, 120, 200, n=5)
    dr.rectangle([120, 300, 330, 380], outline=(20, 20, 20), width=5)
    palette(dr, 640)                                           # right edge of the 640 px canvas, clear gutter
    im.save(d / "f.png")
    j = cf(d / "f.png", d / "o.png")
    ok(j["palette_removed"], f"palette not removed: {j}")
    ok(j["final_box"][2] < 80 + 640 - 40, f"box still reaches the palette: {j['final_box']}")
    a = _px(d / "o.png")
    sat = ((a.max(axis=2) - a.min(axis=2)) > 100)
    ok(sat.sum() < 30, f"coloured palette blobs remain in the output ({sat.sum()} px)")
    # the same dots glued to the drawing (no blank gutter) are NEVER removed
    im2, dr2 = board()
    bars(dr2, 120, 200, n=5)
    dr2.rectangle([120, 300, 560, 380], outline=(20, 20, 20), width=5)
    dr2.line([(560, 130), (560, 420)], fill=(20, 20, 20), width=4)
    palette(dr2, 566)                                          # touches the drawing: no gutter
    im2.save(d / "g.png")
    j2 = cf(d / "g.png", d / "g_o.png")
    ok(not j2["palette_removed"], f"removed something that is not separated by a gutter: {j2}")


def _px(p):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.int32)


@test
def t_crop_real_frame_cut_sentence_and_palette():
    if not (FRAMES / "043_00-37-23.jpg").is_file():
        SKIPS.append("real-frame crop (cpp repo absent)")
        return
    d = WORK / "real"
    rmtree(d)
    d.mkdir(parents=True)
    frame = FRAMES / "043_00-37-23.jpg"              # "From class, 00:37:23": the numbered statements + stack board
    # the crop as the cpp visuals yaml requested it for V04
    crop = "0.10,0.12,0.90,0.98"
    if VISUALS.is_file():
        for ln in VISUALS.read_text(encoding="utf-8").splitlines():
            if "F043" in ln and "crop:" in ln:
                crop = ln.split("crop:")[1].split("]")[0].strip(" [").replace(" ", "")
    j = cf(frame, d / "v04.jpg", "--crop-frac", crop)
    fb = j["final_box"]
    # independent truth: where the sentence "When you started a" lies in the source (dark pixels, bottom rows)
    from PIL import Image
    import numpy as np
    src = np.asarray(Image.open(frame).convert("L"), dtype=np.int32)
    rows = src[300:318, 60:455] < 110
    cols = np.nonzero(rows.any(axis=0))[0] + 60
    ok(cols.size > 30, "could not locate the sentence in the source frame")
    ok(fb[0] <= cols.min() and fb[2] >= cols.max(), f"sentence x {cols.min()}..{cols.max()} cut by final box {fb}")
    ok(j["palette_removed"], f"colour palette not removed: {j}")
    a = _px(d / "v04.jpg")
    sat = ((a.max(axis=2) - a.min(axis=2)) > 120)
    ok(sat.sum() < 40, f"coloured palette pixels remain: {sat.sum()}")


# ------------------------------------------------------------------ 16.2 locks
LOCK_WORKER = """
import sys, time
sys.path.insert(0, {scripts!r})
from notes_lib.lock import FileLock
lock, counter, n = {lock!r}, {counter!r}, {n}
for _ in range(n):
    with FileLock(lock, timeout=60):
        v = int(open(counter).read() or 0)
        time.sleep(0.002)
        open(counter, "w").write(str(v + 1))
"""


@test
def t_file_lock_serialises_processes():
    d = WORK / "lock"
    rmtree(d)
    d.mkdir(parents=True)
    counter = d / "counter.txt"
    counter.write_text("0")
    code = LOCK_WORKER.format(scripts=str(SCRIPTS), lock=str(d / "x.lock"), counter=str(counter), n=15)
    procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(4)]
    for p in procs:
        eq(p.wait(timeout=120), 0, "worker")
    eq(int(counter.read_text()), 60, "lost updates: the lock did not serialise")
    ok(not (d / "x.lock").exists(), "lockfile left behind")


@test
def t_file_lock_recovers_stale_owner():
    from notes_lib.lock import FileLock, LockTimeout, pid_alive
    d = WORK / "lock2"
    rmtree(d)
    d.mkdir(parents=True)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    time.sleep(0.2)
    ok(not pid_alive(dead.pid), "dead pid reported alive")
    ok(pid_alive(os.getpid()), "own pid reported dead")
    lock = d / "gpu.lock"
    lock.write_text(json.dumps({"pid": dead.pid, "host": socket.gethostname(), "token": "x", "created": "t"}))
    t0 = time.time()
    with FileLock(lock, timeout=10):
        ok(time.time() - t0 < 5, "stale lock was not taken over quickly")
    # a live owner is respected (times out), unreadable junk is taken over after a grace period
    lock.write_text(json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "token": "y", "created": "t"}))
    try:
        FileLock(lock, timeout=0.5).acquire()
        raise AssertionError("took a lock held by a live process")
    except LockTimeout:
        pass
    lock.unlink()
    lock.write_text("{half written")
    old = time.time() - 30
    os.utime(lock, (old, old))
    with FileLock(lock, timeout=10):
        pass


@test
def t_pipeline_waits_for_the_gpu_lock():
    from notes_lib.lock import FileLock
    d = WORK / "gpu"
    rmtree(d)
    (d / "locks").mkdir(parents=True)
    vid = d / "clip.mp4"
    vid.write_bytes(b"not a video")
    held = FileLock(d / "locks" / "gpu.lock", timeout=5).acquire()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([UV, "run", "--quiet", "--script", str(SCRIPTS / "video" / "pipeline.py"), str(vid),
                          "--out", str(d / "out"), "--lock-dir", str(d / "locks"), "--whisper", "force"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", env=env)
    lines: list[str] = []
    import threading
    th = threading.Thread(target=lambda: [lines.append(x) for x in p.stdout], daemon=True)
    th.start()
    deadline = time.time() + 60
    while time.time() < deadline and not any("[gpu-lock] waiting" in x for x in lines):
        time.sleep(0.3)
    ok(any("[gpu-lock] waiting" in x for x in lines), "pipeline did not report waiting: " + "".join(lines)[-400:])
    time.sleep(1.5)
    ok(p.poll() is None and not any("[gpu-lock] acquired" in x for x in lines), "pipeline ran while the lock was held")
    held.release()
    deadline = time.time() + 60
    while time.time() < deadline and not any("[gpu-lock] acquired" in x for x in lines):
        time.sleep(0.3)
    ok(any("[gpu-lock] acquired" in x for x in lines), "pipeline never got the lock after release")
    p.kill()
    p.wait(timeout=30)
    th.join(timeout=5)


def ledger_project(name):
    root = WORK / name
    rmtree(root)
    (root / ".notes-plugin-internal").mkdir(parents=True)
    return root


@test
def t_ledger_parallel_writes_and_sweep_mode():
    from notes_lib import layout, ledger
    root = ledger_project("ledger")
    code = """
import sys
sys.path.insert(0, {scripts!r})
from pathlib import Path
from notes_lib import layout, ledger
lay = layout.Layout(Path({root!r}))
for k in range({a}, {b}):
    nn = f"{{k:02d}}"
    inputs = {{"session": {{"n": k, "nn": nn, "title": "T"}}, "fingerprints": {{"transcript": {{"sha256": "h"}}}},
              "inputs_hash": "hash" + nn, "paths": {{}}, "note_paths": {{}}}}
    ledger.record(lay, {{}}, inputs, "basic", "ok", qa_ok=True)
"""
    procs = [subprocess.Popen([sys.executable, "-c", code.format(scripts=str(SCRIPTS), root=str(root), a=a, b=a + 4)])
             for a in (1, 5, 9, 13)]
    for p in procs:
        eq(p.wait(timeout=120), 0, "recorder")
    lay = layout.Layout(root)
    st = ledger.load(lay)
    eq(sorted(st["sessions"]), [f"{k:02d}" for k in range(1, 17)], "a parallel write was lost")
    ok(not (root / ".notes-plugin-internal" / "state.lock").exists(), "state.lock left behind")
    # mode=sweep stamps the tier but never touches the inputs hash or the status
    before = jload(lay.state_path)["sessions"]["03"]
    ledger.record(lay, {}, {"session": {"n": 3, "nn": "03", "title": "T"}}, "basic", "ok", mode="sweep")
    after = jload(lay.state_path)["sessions"]["03"]
    eq(after["inputs_hash"], before["inputs_hash"], "sweep changed inputs_hash")
    eq(after["tiers"]["basic"]["inputs_hash"], before["tiers"]["basic"]["inputs_hash"], "sweep changed tier hash")
    eq(after["tiers"]["basic"]["generated_at"], before["tiers"]["basic"]["generated_at"], "sweep re-stamped generation")
    ok("swept_at" in after["tiers"]["basic"], "sweep not recorded")
    r = run("ledger.py", "record", "--help")
    ok("sweep" in r.stdout, "ledger record --mode must accept sweep")


@test
def t_failed_session_is_retried():
    from notes_lib import layout, ledger
    root = ledger_project("failed")
    lay = layout.Layout(root)
    inputs = {"session": {"n": 2, "nn": "02", "title": "T"}, "fingerprints": {}, "inputs_hash": "h", "paths": {},
              "note_paths": {}}
    ledger.record(lay, {}, inputs, "basic", "failed")
    st = ledger.load(lay)
    eq(st["sessions"]["02"]["tiers"]["basic"]["status"], "failed", "failure not recorded")
    src = (SCRIPTS / "plan_updates.py").read_text(encoding="utf-8")
    ok('"incomplete"' in src and "failed" in src, "plan_updates must treat failed/partial as incomplete")


# ------------------------------------------------------------------ 16.3 concept map + sweep
DIGEST = """---
profile: x
session: {n}
title_as_taught: "{title}"
slug: {slug}
date: 2026-08-2{n}
---

# Digest

## 2. Concept inventory

| ID | Concept | Depth | Minutes | Emphasis | Assessable | Sources |
| --- | --- | --- | --- | --- | --- | --- |
{concepts}

## 5. Class-correction candidates

| ID | Said | Likely actual | Severity | Confidence | How to verify |
| --- | --- | --- | --- | --- | --- |
| K01 | "a said {n}" | an actual {n} | \U0001f7e0 | high | docs |

## 9. Vocabulary

| Term | First ts | Definition as given |
| --- | --- | --- |
{vocab}

## 10. Course links

- Back: session 1 set up the stack frame.
- Deferred: move semantics.

## 11. Class visuals

| ID | Frame | Concept | What it shows | Quality | Crop | Treatment |
| --- | --- | --- | --- | --- | --- | --- |
| V01 | F001 00:01:00 | C01 | board | clean | 0,0,1,1 | embed |
"""


def sweep_project():
    root = WORK / "sweep"
    rmtree(root)
    (root / ".notes-plugin-internal").mkdir(parents=True)
    (root / "resources").mkdir()
    (root / ".notes-plugin-internal" / "profile.md").write_text(
        "---\nschema: 1\nid: sw\ntitle: Sweep\nshort: SW\nprefix: SW\nsessions:\n"
        "  - {n: 1, title: First}\n  - {n: 2, title: Second}\n  - {n: 3, title: Third}\n"
        "tiers:\n  basic: {words: [100, 9000], required_sections: [\"TL;DR\"], closing_sections: [\"Going Deeper\"]}\n"
        "  advanced: {words: [100, 9000], required_sections: [\"TL;DR\"], closing_sections: [\"Further Study\"]}\n---\n",
        encoding="utf-8")
    spec = {1: ("Stack frame", "`std::cout`"), 2: ("Stack frame", "`std::cout`"), 3: ("Heap", "RAII")}
    for n, (c, term) in spec.items():
        s = root / ".notes-plugin-internal" / "work" / "sessions" / f"{n:02d}"
        s.mkdir(parents=True)
        (s / "digest.md").write_text(DIGEST.format(
            n=n, title=f"Title {n}", slug=f"title-{n}",
            concepts=f"| C01 | {c} | explained | 5 | high | y | t |\n| C02 | Only {n} | mentioned | 1 | low | n | t |",
            vocab=f"| {term} | 00:01 | def {n} |"), encoding="utf-8")
    for t in ("basic", "advanced"):
        d = root / "notes" / t
        d.mkdir(parents=True)
        for n in (1, 2, 3):
            (d / f"{n:02d}-title-{n}.md").write_text(note_text(n, t), encoding="utf-8")
    return root


def note_text(n, tier, extra=""):
    return (f"---\ntitle: T{n}\nsession: {n}\ntier: {tier}\nprofile: sw\ndate: 2026-08-2{n}\nduration_min: 60\n"
            f"sources: []\ninstructor_code: null\nrevision_minutes: 20\nverified_as_of: 2026-10-06\n---\n\n"
            f"# {n:02d} \u00b7 Title {n}\n\n## TL;DR\n\n- point one {n}\n\n## The stack frame\n\nBody text for note {n}.\n"
            f"{extra}\n## Going Deeper\n\n- a link\n")


@test
def t_concept_map_deterministic_inventory():
    root = sweep_project()
    out = root / ".notes-plugin-internal" / "work" / "course" / "inventory.json"
    r = run("concept_map.py", "--project", root)
    eq(r.returncode, 0, r.stdout + r.stderr)
    inv = jload(out)
    eq([s["n"] for s in inv["sessions"]], [1, 2, 3], "sessions")
    s1 = inv["sessions"][0]
    eq([c["id"] for c in s1["concepts"]], ["C01", "C02"], "concept ids")
    eq(s1["concepts"][0]["depth"], "explained", "depth")
    eq(s1["corrections"][0]["severity"], "\U0001f7e0", "correction severity")
    ok(s1["vocabulary"] and s1["vocabulary"][0]["term"].startswith("`std::cout`"), "vocabulary")
    eq(s1["visuals"][0]["id"], "V01", "visuals")
    ok(any("move semantics" in x.lower() for x in s1["links"]), "course links")
    ok("basic" in s1["notes"] and s1["notes"]["basic"]["outline"][0] == "TL;DR", f"note outline {s1['notes']}")
    keys = {(x["kind"], x["key"]): [r["n"] for r in x["sessions"]] for x in inv["shared_exact"]}
    eq(keys.get(("concept", "stack frame")), [1, 2], "exact concept repeat")
    eq(keys.get(("term", "std::cout")), [1, 2], "exact term repeat")
    ok(("concept", "heap") not in keys, "a single-session concept is not a repeat")
    h1 = sha(out)
    run("concept_map.py", "--project", root)
    eq(sha(out), h1, "deterministic")
    r = run("concept_map.py", "--project", root, "--sessions", "3")
    eq([s["n"] for s in jload(out)["sessions"]], [3], "--sessions subset")


@test
def t_sweep_markers_lint_and_stats():
    root = sweep_project()
    prof = root / ".notes-plugin-internal" / "profile.md"
    n2 = root / "notes" / "basic" / "02-title-2.md"
    block = ("\n<!-- sweep:S-02-001 -->\n> **Builds on:** session 1, [the stack frame](01-title-1.md#the-stack-frame) "
             "- reused here.\n<!-- /sweep:S-02-001 -->\n")
    n2.write_text(note_text(2, "basic", block), encoding="utf-8")

    def lint(p, tier="basic"):
        r = run("lint_note.py", p, "--tier", tier, "--project", root, "--json", root / "lint.json")
        return jload(root / "lint.json")["notes"][0], r

    res, _ = lint(n2)
    codes = [e["code"] for e in res["errors"] + res["warnings"]]
    ok("sweep-marker" not in codes and "builds-on-link" not in codes, f"valid markers flagged: {codes}")
    ok("html-disallowed" not in codes, "marker lines hit the HTML allowlist check")
    # marker lines are invisible to the prose/word counts
    from notes_lib import notefile
    plain = notefile.parse_note(root / "notes" / "basic" / "01-title-1.md").body
    with_marks = "## X\n\n<!-- sweep:A -->\n> **Builds on:** a b c\n<!-- /sweep:A -->\n\nTail words here.\n"
    no_marks = "## X\n\n> **Builds on:** a b c\n\nTail words here.\n"
    eq(notefile.stats(with_marks, ())["prose_words"], notefile.stats(no_marks, ())["prose_words"], "word count")
    ok(plain, "fixture note empty")
    # unbalanced / duplicate / nested / broken links
    cases = {
        "unclosed": "\n<!-- sweep:S-X -->\n> **Builds on:** a\n",
        "dup": block + block.replace("S-02-001", "S-02-001"),
        "nested": "\n<!-- sweep:A -->\n<!-- sweep:B -->\nx\n<!-- /sweep:B -->\n<!-- /sweep:A -->\n",
        "stray-close": "\n<!-- /sweep:Z -->\n",
    }
    for name, extra in cases.items():
        n2.write_text(note_text(2, "basic", extra), encoding="utf-8")
        res, _ = lint(n2)
        ok(any(e["code"] == "sweep-marker" for e in res["errors"]), f"{name}: not reported: {res['errors']}")
    bad = block.replace("01-title-1.md#the-stack-frame", "01-title-1.md#no-such-heading")
    n2.write_text(note_text(2, "basic", bad), encoding="utf-8")
    res, _ = lint(n2)
    ok(any(e["code"] == "builds-on-link" for e in res["errors"]), "dangling section anchor not caught")
    gone = block.replace("01-title-1.md#the-stack-frame", "09-nothing.md")
    n2.write_text(note_text(2, "basic", gone), encoding="utf-8")
    res, _ = lint(n2)
    ok(any(e["code"] == "builds-on-link" for e in res["errors"]), "missing note not caught")
    ok(prof.is_file())


@test
def t_sweep_plan_apply_is_idempotent():
    root = sweep_project()
    plan = {"edits": [
        {"id": "S-02-001", "note": "02-title-2.md", "tier": "basic", "anchor": "The stack frame", "type": "builds-on",
         "text": "> **Builds on:** session 1, [the stack frame](01-title-1.md#the-stack-frame).", "rationale": "r"},
        {"id": "S-03-001", "note": "03-title-3.md", "tier": "basic", "anchor": "TL;DR", "type": "builds-on",
         "text": "> **Builds on:** session 2.", "rationale": "r"},
        {"id": "S-02-A01", "note": "02-title-2.md", "tier": "advanced", "anchor": "The stack frame", "type": "alias",
         "text": "> **Recall:** the same idea as session 1.", "rationale": "r"}]}
    pj = root / "plan.json"
    pj.write_text(json.dumps(plan), encoding="utf-8")
    r = run("sweep_check.py", "--project", root, "--plan", pj)
    eq(r.returncode, 1, "an unapplied plan must be reported\n" + r.stdout)
    r = run("sweep_check.py", "--project", root, "--plan", pj, "--apply")
    eq(r.returncode, 0, r.stdout + r.stderr)
    snap = {p.name + p.parent.name: sha(p) for t in ("basic", "advanced") for p in (root / "notes" / t).glob("*.md")}
    r = run("sweep_check.py", "--project", root, "--plan", pj, "--apply")
    eq(r.returncode, 0, r.stdout + r.stderr)
    snap2 = {p.name + p.parent.name: sha(p) for t in ("basic", "advanced") for p in (root / "notes" / t).glob("*.md")}
    eq(snap, snap2, "second apply changed the notes")
    txt = (root / "notes" / "basic" / "02-title-2.md").read_text(encoding="utf-8")
    eq(txt.count("<!-- sweep:S-02-001 -->"), 1, "duplicated block")
    r = run("sweep_check.py", "--project", root, "--plan", pj)
    eq(r.returncode, 0, "check after apply\n" + r.stdout)
    # an updated text replaces the block, never appends
    plan["edits"][0]["text"] = "> **Builds on:** session 1 (revised)."
    pj.write_text(json.dumps(plan), encoding="utf-8")
    run("sweep_check.py", "--project", root, "--plan", pj, "--apply")
    txt = (root / "notes" / "basic" / "02-title-2.md").read_text(encoding="utf-8")
    ok("(revised)" in txt and txt.count("<!-- sweep:S-02-001 -->") == 1 and "the stack frame](01-title-1.md" not in txt, "not replaced")
    # lint of the applied notes: clean
    r = run("lint_note.py", root / "notes" / "basic" / "02-title-2.md", "--tier", "basic", "--project", root,
            "--json", root / "l.json")
    ok(not [e for e in jload(root / "l.json")["notes"][0]["errors"] if e["code"] in ("sweep-marker", "builds-on-link")],
       "applied notes fail the sweep lint")
    # a duplicate ID across two notes is a problem
    n3 = root / "notes" / "basic" / "03-title-3.md"
    n3.write_text(n3.read_text(encoding="utf-8").replace("S-03-001", "S-02-001"), encoding="utf-8")
    r = run("sweep_check.py", "--project", root)
    eq(r.returncode, 1, "id shared by two notes")


# ------------------------------------------------------------------ 18 pending references
PENDING_EXTRA = {
    # note 1: a marker for session 3 (its note exists in the fixture) and one for session 9 (it does not)
    1: ("\nThe cost returns in <!-- pending-ref:03 -->Session 3 (Heap) measures it in nanoseconds,\n"
        "which this note only previews.<!-- /pending-ref:03 --> Later,\n"
        "<!-- pending-ref:09 -->session 9 (Capstone) uses it end to end.<!-- /pending-ref:09 -->\n"),
    # note 2: legacy stale phrases (one wrapped across lines, one inside a blockquote) and two false positives
    2: ("\nSession 3 (Heap) has no note in this set yet, so what it supplies is restated here: the heap\n"
        "grows on demand.\n\n> **Note:** session 1's note has not been written at the\n> time of writing.\n\n"
        "The buffer is not written to RAM until flush, and the agent notes written by hand are kept.\n"),
}


def pending_project():
    root = sweep_project()
    for t in ("basic", "advanced"):
        for n, extra in PENDING_EXTRA.items():
            (root / "notes" / t / f"{n:02d}-title-{n}.md").write_text(note_text(n, t, extra), encoding="utf-8")
    return root


@test
def t_pending_refs_inventory_resolve_and_lint():
    root = pending_project()
    out = root / ".notes-plugin-internal" / "work" / "course" / "inventory.json"
    r = run("concept_map.py", "--project", root)
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok("pending references: 8" in r.stdout, r.stdout)
    pend = jload(out)["pending"]
    b = [p for p in pend if p["tier"] == "basic"]
    eq([(p["id"], p["kind"], p["resolvable"]) for p in b],
       [("resolve-pending-03-from-01", "marker", True), ("resolve-pending-09-from-01", "marker", False),
        ("resolve-pending-03-from-02", "legacy", True), ("resolve-pending-01-from-02", "legacy", True)], "basic items")
    m3, m9, l3, l1 = b
    ok(m3["replaces"].startswith("<!-- pending-ref:03 -->") and m3["replaces"].endswith("<!-- /pending-ref:03 -->"), "marker span")
    eq(m3["target_notes"], {"3": "03-title-3.md"}, "target note")
    eq(m9["missing"], [9], "missing target")
    ok(l3["replaces"].startswith("Session 3 (Heap) has no note") and l3["replaces"].endswith("heap\ngrows on demand."), l3["replaces"])
    eq(l3["heading"], "The stack frame", "heading")
    ok(l1["replaces"].startswith("session 1's note has not been written"), l1["replaces"])
    ok(not any("RAM" in p["text"] or "agent notes" in p["text"] for p in pend), "false positive")
    h1 = sha(out)
    run("concept_map.py", "--project", root)
    eq(sha(out), h1, "inventory must be byte-stable")
    run("concept_map.py", "--project", root, "--sessions", "9")
    eq({p["from"] for p in jload(out)["pending"]}, {1}, "--sessions 9 keeps only items that touch session 9")
    run("concept_map.py", "--project", root)
    # lint: stale phrases and resolvable markers are warnings, an unclosed marker is an error
    n1, n2 = (root / "notes" / "basic" / f"0{k}-title-{k}.md" for k in (1, 2))
    r = run("lint_note.py", n1, n2, "--tier", "basic", "--project", root, "--json", root / "l.json")
    docs = {Path(d["path"]).name: d for d in jload(root / "l.json")["notes"]}
    w1 = [(w["code"], w["line"]) for w in docs[n1.name]["warnings"] if w["code"].startswith(("pending", "stale"))]
    eq([c for c, _ in w1], ["pending-ref-resolvable"], f"only the session-3 marker is resolvable: {w1}")
    eq(sorted(w["code"] for w in docs[n2.name]["warnings"] if w["code"] == "stale-note-ref"), ["stale-note-ref"] * 2, "stale phrases")
    ok(not [e for d in docs.values() for e in d["errors"] if e["code"] in ("pending-marker", "sweep-marker")], "marker errors")
    n1.write_text(n1.read_text(encoding="utf-8").replace("<!-- /pending-ref:09 -->", ""), encoding="utf-8")
    run("lint_note.py", n1, "--tier", "basic", "--project", root, "--json", root / "l.json")
    ok(any(e["code"] == "pending-marker" for e in jload(root / "l.json")["notes"][0]["errors"]), "unclosed pending-ref")
    n1.write_text(note_text(1, "basic", PENDING_EXTRA[1]), encoding="utf-8")
    # resolve: the plan the analyst would write, applied twice
    plan = {"edits": []}
    for p in b:
        if not p["resolvable"]:
            continue
        link = f"[session {p['targets'][0]}: Title {p['targets'][0]}]({p['target_notes'][str(p['targets'][0])]}#the-stack-frame)"
        plan["edits"].append({"id": p["id"], "note": p["note"], "tier": "basic", "anchor": p["heading"], "type": "resolve-pending",
                              "replaces": p["replaces"], "text": f"{link} measures it in nanoseconds; the heap grows on demand.",
                              "rationale": "r"})
    pj = root / "plan.json"
    pj.write_text(json.dumps(plan), encoding="utf-8")
    r = run("sweep_check.py", "--project", root, "--tier", "basic", "--plan", pj)
    eq(r.returncode, 1, "unresolved pending items must be reported\n" + r.stdout)
    r = run("sweep_check.py", "--project", root, "--tier", "basic", "--plan", pj, "--apply")
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok("pending-ref:09" not in r.stdout and "target note still missing" in r.stdout, "the unresolvable marker is listed:\n" + r.stdout)
    t1 = n1.read_text(encoding="utf-8")
    t2 = n2.read_text(encoding="utf-8")
    ok("<!-- sweep:resolve-pending-03-from-01 -->[session 3: Title 3](03-title-3.md#the-stack-frame)" in t1, t1)
    ok("pending-ref:03" not in t1 and "<!-- pending-ref:09 -->" in t1, "03 resolved, 09 untouched")
    ok("has no note" not in t2 and "has not been written" not in t2 and t2.count("<!-- sweep:resolve-pending-03-from-02 -->") == 1, t2)
    ok("flush, and the agent notes written by hand" in t2, "false-positive text must be untouched")
    snap = {p.name: sha(p) for p in (root / "notes" / "basic").glob("*.md")}
    r = run("sweep_check.py", "--project", root, "--tier", "basic", "--plan", pj, "--apply")
    eq(r.returncode, 0, r.stdout + r.stderr)
    eq(snap, {p.name: sha(p) for p in (root / "notes" / "basic").glob("*.md")}, "second apply changed the notes")
    plan["edits"][0]["text"] = plan["edits"][0]["text"] + " (revised)"
    pj.write_text(json.dumps(plan), encoding="utf-8")
    run("sweep_check.py", "--project", root, "--tier", "basic", "--plan", pj, "--apply")
    t1 = n1.read_text(encoding="utf-8")
    ok(t1.count("(revised)") == 1 and t1.count("<!-- sweep:resolve-pending-03-from-01 -->") == 1, "re-run must replace")
    # after the sweep: lint clean of pending warnings, the inventory keeps only the waiting marker
    r = run("lint_note.py", n1, n2, "--tier", "basic", "--project", root, "--json", root / "l.json")
    for d in jload(root / "l.json")["notes"]:
        bad = [x["code"] for x in d["errors"] + d["warnings"]
               if x["code"] in ("pending-marker", "sweep-marker", "builds-on-link", "pending-ref-resolvable", "stale-note-ref")]
        eq(bad, [], f"{d['path']} after the sweep")
    run("concept_map.py", "--project", root)
    left = [p for p in jload(out)["pending"] if p["tier"] == "basic"]
    eq([(p["id"], p["resolvable"]) for p in left], [("resolve-pending-09-from-01", False)], "only the waiting marker is left")
    # a resolved sentence whose link would dangle is caught by the lint
    n2.write_text(t2.replace("03-title-3.md#the-stack-frame", "03-title-3.md#nope"), encoding="utf-8")
    run("lint_note.py", n2, "--tier", "basic", "--project", root, "--json", root / "l.json")
    ok(any(e["code"] == "builds-on-link" for e in jload(root / "l.json")["notes"][0]["errors"]), "dangling anchor inside an inline span")
    # inline markers do not turn a paragraph into an HTML block or add words
    from notes_lib import notefile
    a = "A line with <!-- sweep:X -->[s](a.md) two words<!-- /sweep:X --> end.\n\nNext."
    b2 = "A line with [s](a.md) two words end.\n\nNext."
    eq([k for k, *_ in notefile.iter_blocks_ex(a)], ["prose", "prose"], "inline marker made an html block")
    eq(notefile.stats(a, ())["prose_words"], notefile.stats(b2, ())["prose_words"], "inline markers counted as words")
    starts = "<!-- pending-ref:07 -->Session 7 opens this line.<!-- /pending-ref:07 --> Rest of it."
    eq([k for k, *_ in notefile.iter_blocks_ex(starts)], ["prose"], "marker at line start")
    # the renderer: a paragraph that starts with an inline marker is still markdown (not a raw HTML block)
    from render import markdown as rmd
    md = rmd.make_md("basic", False, None)
    src = ("<!-- sweep:X -->See **bold** and `code` here.<!-- /sweep:X --> Tail.\n\n"
           "> <!-- sweep:Y -->Quote **bold**.<!-- /sweep:Y -->\n\n<!-- sweep:Z -->\nown line\n<!-- /sweep:Z -->\n")

    def env():
        return {"math": 0, "mermaid": 0, "figures": [], "unmapped": set(), "link": {}, "math_on": False}
    ok("<strong>bold</strong>" not in md.render(src, env()), "premise: an unstripped line-start marker blocks markdown")
    html = md.render(rmd.strip_inline_markers(src), env())
    ok(html.count("<strong>bold</strong>") == 2 and "<code>code</code>" in html and "<!--" not in html, html)
    ok("own line" in html, "own-line marker blocks stay as they were")


@test
def t_sweep_callout_classes_in_renderer():
    from render import markdown as rmd
    for label, cls in (("Repo note", "repo"), ("Builds on", "buildson"), ("Recall", "buildson"),
                       ("Revisited", "buildson")):
        eq(rmd.callout_class(label, []), cls, label)
    eq(rmd.callout_class("Class correction (\U0001f7e0 high)", []), "correction", "corrections unchanged")
    ok(rmd.callout_class("Repo note", []) != "correction", "repo note must not look like a correction")
    from render import css
    sheet = css.__dict__.get("CSS") or "".join(v for v in css.__dict__.values() if isinstance(v, str))
    ok("blockquote.cal.repo" in sheet and "blockquote.cal.buildson" in sheet, "callout styles missing from the CSS")


# ------------------------------------------------------------------ 16.4 discovery
def scaler_txt(title, start="9/20/2026, 5:45:00 PM", minutes=120):
    body = " ".join(["we map the workflow and the data sources today."] * 60)
    return (f"Course Name: Field Engineering\nLecture Title: {title}\nStart Time: {start}\nDuration: {minutes} minutes\n"
            f"Model use: t\n\n==================================================\n\n{body}\n")


def fde_like():
    root = WORK / "fdelike"
    rmtree(root)
    res = root / "resources"
    res.mkdir(parents=True)
    (root / ".notes-plugin-internal").mkdir()
    titles = {1: "Mindset and Discovery", 2: "Workflow Mapping", 3: "Business KPIs", 4: "Client Data",
              5: "Fetching Data", 6: "Profiling and Cleaning"}
    for n, t in titles.items():
        (res / f"Class_{n}_\u2014_{t.replace(' ', '_')}_-_Class_Scaler_Academy.txt").write_text(
            scaler_txt(f"Class {n} \u2014 {t}", f"9/{n + 10}/2026, 5:45:00 PM"), encoding="utf-8")
    (res / "FDE_Session1.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "FDE_Session_2_3_vshare.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "FDE_Class4.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "Notes Class 2-3.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "L2&3 recap.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "slides sessions 4,5.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "Sessions 4-6 handbook.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "FlashEats_Class5_Solution.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "FlashEats_Class6_Student.ipynb").write_text("{}", encoding="utf-8")
    (res / "manifest_class6.json").write_text("{}", encoding="utf-8")
    (res / "FlashEats.json").write_text("{}", encoding="utf-8")
    (res / "links.md").write_text("# Links\n\n- https://example.com\n", encoding="utf-8")
    (res / "Syllabus Term 1.pdf").write_bytes(b"%PDF-1.4\n")
    (res / "2026-09-15 recording notes.pdf").write_bytes(b"%PDF-1.4\n")        # a date, not a range
    pack = res / "Pack_Class4_Student_Pack"
    (pack / "data").mkdir(parents=True)
    (pack / "data" / "orders.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (pack / "starter").mkdir()
    (pack / "starter" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (pack / "README.md").write_text("# pack\n", encoding="utf-8")
    (root / ".notes-plugin-internal" / "profile.md").write_text(
        "---\nschema: 1\nid: fl\ntitle: Field\nshort: FL\nprefix: FL\nsessions: []\npaths:\n  syllabus: \"Syllabus*.pdf\"\n---\n",
        encoding="utf-8")
    return root


def scan(root):
    r = run("scan_resources.py", "--project", root, "--json", root / "scan.json")
    eq(r.returncode, 0, r.stdout + r.stderr)
    doc = jload(root / "scan.json")
    return {Path(f["path"]).name: f for f in doc["files"]}, doc


@test
def t_discover_multi_session_and_new_kinds():
    from notes_lib import discover as d
    eq(d.explicit_numbers("FDE_Session_2_3_vshare"), [2, 3], "Session_2_3")
    eq(d.explicit_numbers("Class 2-3"), [2, 3], "Class 2-3")
    eq(d.explicit_numbers("L2&3"), [2, 3], "L2&3")
    eq(d.explicit_numbers("sessions 4,5"), [4, 5], "sessions 4,5")
    eq(d.explicit_numbers("Sessions 2-4"), [2, 3, 4], "range")
    eq(d.explicit_numbers("2026-09-15 Class"), [], "a date is never a range")
    eq(d.explicit_numbers("Class 12-09-2026"), [], "a day-first date is never a range")
    eq(d.explicit_numbers("Class_10_\u2014_Agentic"), [10], "single")
    eq(d.explicit_numbers("Lecture 3 - pointers part 2"), [3], "title number is not a session")
    root = fde_like()
    files, doc = scan(root)
    eq(files["FDE_Session_2_3_vshare.pdf"]["sessions"], [2, 3], "multi-session pdf")
    eq(files["Notes Class 2-3.pdf"]["sessions"], [2, 3], "Class 2-3")
    eq(files["L2&3 recap.pdf"]["sessions"], [2, 3], "L2&3")
    eq(files["slides sessions 4,5.pdf"]["sessions"], [4, 5], "sessions 4,5")
    eq(files["Sessions 4-6 handbook.pdf"]["sessions"], [4, 5, 6], "range 4-6")
    eq(files["FDE_Session1.pdf"]["sessions"], [1], "Session1")
    eq(files["FDE_Class4.pdf"]["sessions"], [4], "Class4")
    for name, kind, ss in (("FlashEats_Class5_Solution.pdf", "exercise", [5]), ("FlashEats_Class6_Student.ipynb", "exercise", [6]),
                           ("manifest_class6.json", "data", [6]), ("FlashEats.json", "data", []),
                           ("links.md", "reference", []), ("orders.csv", "data", [4]), ("app.py", "exercise", [4])):
        f = files[name]
        eq((f["kind"], f["sessions"]), (kind, ss), name)
    eq(files["links.md"]["scope"], "course", "links.md course-wide")
    eq(files["Syllabus Term 1.pdf"]["kind"], "syllabus", "syllabus kind")
    eq(files["2026-09-15 recording notes.pdf"]["sessions"], [], "date file must not be assigned by a range")
    ok(all(f["scope"] != "unassigned" or f["candidates"] is not None for f in files.values()), "unassigned without reason")
    # all six transcripts are assigned (derived table) and the sessions carry their documents
    tr = [f for f in files.values() if f["kind"] == "transcript"]
    eq(len(tr), 6, "transcripts")
    ok(all(f["sessions"] for f in tr), "a transcript is unassigned")
    # inputs.json carries data[] / exercises[] / references[] with {path, kind, sessions, reason}
    r = run("resolve_inputs.py", "--session", 4, "--project", root, "--dry-run", "--allow-no-speech")
    eq(r.returncode, 0, r.stdout + r.stderr)
    r = run("resolve_inputs.py", "--session", 5, "--project", root)
    eq(r.returncode, 0, r.stdout + r.stderr)
    inp = jload(root / ".notes-plugin-internal" / "work" / "sessions" / "05" / "inputs.json")
    for key in ("data", "exercises", "references"):
        ok(key in inp, f"inputs.json lacks {key}[]")
    ex = {Path(x["path"]).name: x for x in inp["exercises"]}
    ok("FlashEats_Class5_Solution.pdf" in ex and "FlashEats_Class6_Student.ipynb" not in ex, f"exercises {list(ex)}")
    eq(sorted(ex["FlashEats_Class5_Solution.pdf"]), ["kind", "path", "reason", "sessions"], "row keys")
    ok(any(Path(x["path"]).name == "FlashEats.json" and x["sessions"] == [] for x in inp["data"]), "course-wide data")
    ok(any(Path(x["path"]).name == "links.md" for x in inp["references"]), "links.md for every session")
    ok(any(Path(x["path"]).name == "links.md" for x in inp["course_docs"]), "links.md in course_docs")


@test
def t_init_from_syllabus_draft_profile():
    root = fde_like()
    rmtree(root / ".notes-plugin-internal")
    syl = root / "resources" / "Syllabus Term 1.txt"
    syl.write_text("Session \nNumber\nSession Title\n1\nClass 1 \u2014 Mindset and Discovery\nTopics here\n"
                   "2\nClass 2 \u2014 Workflow Mapping\nmore\n3\nClass 3 \u2014 Business KPIs\n"
                   "Assignment 1\n20%\nCapstone\n45%\n7\nClass 7 \u2014 Extra Session Only In Syllabus\n", encoding="utf-8")
    r = run("init_project.py", "--project", root, "--from-syllabus", syl.name, "--json", root / "init.json")
    eq(r.returncode, 0, r.stdout + r.stderr)
    text = (root / ".notes-plugin-internal" / "profile.md").read_text(encoding="utf-8")
    ok("title: \"Field Engineering\"" in text, "course title not taken from the transcript headers")
    ok("Extra Session Only In Syllabus" in text, "syllabus-only session lost")
    ok("n: 4" in text and "Client Data" in text or "Class 4" in text, "transcript-only sessions not appended")
    ok("snippets: {}" in text or "python:" in text, "snippets block")
    facts = jload(root / ".notes-plugin-internal" / "work" / "init" / "facts.json")
    ok(facts["syllabus"]["session_rows"][0]["n"] == 1 and len(facts["syllabus"]["session_rows"]) == 4, "syllabus rows")
    ok(facts["assumptions"], "assumptions must be listed for the agent")
    ok(any(w["weight"] == "45%" for w in facts["syllabus"]["assessment_weights"]), "assessment weights")
    ok("figures: class+generated" in text, "generic figure rule")
    # the draft profile loads and scans
    r = run("scan_resources.py", "--project", root)
    eq(r.returncode, 0, r.stdout + r.stderr)


@test
def t_real_fde_dry_run():
    res = FDE / "resources"
    if not res.is_dir():
        SKIPS.append("real FDE scan (folder absent)")
        return
    root = WORK / "fde"
    rmtree(root)
    (root / ".notes-plugin-internal").mkdir(parents=True)
    (root / ".notes-plugin-internal" / "profile.md").write_text(
        "---\nschema: 1\nid: fde\ntitle: FDE\nshort: FDE\nprefix: FDE\nresources_dir: \"" + res.as_posix() + "\"\n"
        "sessions: []\n---\n", encoding="utf-8")
    before = sorted((p.as_posix(), p.stat().st_mtime_ns) for p in res.rglob("*") if p.is_file())
    files, doc = scan(root)
    tr = {n: f for n, f in files.items() if f["kind"] == "transcript"}
    eq(len(tr), 11, "FDE transcripts")
    got = sorted(n for f in tr.values() for n in f["sessions"])
    eq(got, list(range(1, 12)), "each transcript is its own session 1..11")
    ok(files["FDE_Session_2_3_vshare.pdf"]["sessions"] == [2, 3], "FDE_Session_2_3_vshare.pdf must serve sessions 2 and 3")
    eq(files["FDE_Session1.pdf"]["sessions"], [1], "session 1 pdf")
    eq(files["FDE_Class4.pdf"]["sessions"], [4], "class 4 pdf")
    eq(files["FDE_CLass9.pdf"]["sessions"], [9], "class 9 pdf")
    eq(files["FlashEats_Class5_Solution.pdf"]["sessions"], [5], "class 5 solution")
    eq(files["FlashEats_Class6_Student.ipynb"]["sessions"], [6], "class 6 notebook")
    eq(files["manifest_class6.json"]["sessions"], [6], "class 6 manifest")
    eq(files["links.md"]["scope"], "course", "links.md")
    eq(files["links.md"]["kind"], "reference", "links.md kind")
    syl = [f for f in files.values() if f["kind"] == "syllabus"]
    eq(len(syl), 1, "syllabus")
    eq(syl[0]["scope"], "course", "syllabus course-wide")
    pack = [f for f in files.values() if "PlacementOps_Class11_Student_Pack" in f["path"]]
    ok(len(pack) >= 20 and all(f["sessions"] == [11] for f in pack), "PlacementOps pack must belong to class 11")
    for f in files.values():
        ok(f["scope"] != "unassigned", f"unassigned without a reason: {f['path']} {f.get('reason')}")
    ok(not doc["unassigned"], f"unassigned: {doc['unassigned']}")
    after = sorted((p.as_posix(), p.stat().st_mtime_ns) for p in res.rglob("*") if p.is_file())
    eq(before, after, "FDE resources were modified")


# ------------------------------------------------------------------ 16.5 corrections calibration, stats
def basic_note(extra, slips=None):
    body = (f"---\ntitle: T\nsession: 2\ntier: basic\nprofile: sw\ndate: 2026-08-22\nduration_min: 120\nsources: []\n"
            f"instructor_code: null\nrevision_minutes: 20\nverified_as_of: 2026-10-06\n---\n\n# 02 \u00b7 Title\n\n## TL;DR\n\n"
            f"- one\n\n## Body\n\nText here.\n\n{extra}\n")
    if slips is not None:
        body += f"\n## Minor slips in class\n\n{slips}\n"
    return body


def lint_codes(root, text, tier="basic", name="02-title.md"):
    d = root / "notes" / tier
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(text.replace("tier: basic", f"tier: {tier}"), encoding="utf-8")
    run("lint_note.py", p, "--tier", tier, "--project", root, "--json", root / "lint.json")
    res = jload(root / "lint.json")["notes"][0]
    return {e["code"] for e in res["errors"]}, {w["code"] for w in res["warnings"]}


@test
def t_minor_slips_table_and_yellow_callouts():
    root = sweep_project()
    good = "| said | actual |\n| --- | --- | \n| \"RBP is reference base pointer\" | base pointer |\n| \"x86 and Intel\" | two syntaxes |"
    e, w = lint_codes(root, basic_note("", good))
    ok("minor-slips-format" not in e | w, f"valid table flagged: {e} {w}")
    e, w = lint_codes(root, basic_note("", "| wrong | header |\n| --- | --- |\n| a | b |"))
    ok("minor-slips-format" in e, "wrong header must be an error")
    e, w = lint_codes(root, basic_note("", "- just a bullet"))
    ok("minor-slips-format" in e, "a list instead of a table must be an error")
    e, w = lint_codes(root, basic_note("", "| said | actual |\n| --- | --- |\n| a | b | c |"))
    ok("minor-slips-format" in e, "three cells must be an error")
    yellow = "> **Class correction (\U0001f7e1 low):** said x -> actually y."
    e, w = lint_codes(root, basic_note(yellow))
    ok("minor-slip-inline" in w, "yellow correction in basic must warn")
    red = "> **Class correction (\U0001f534 high):** said x -> actually y."
    e, w = lint_codes(root, basic_note(red))
    ok("minor-slip-inline" not in w, "red corrections stay inline without a warning")
    e, w = lint_codes(root, basic_note(yellow), tier="advanced", name="02-title.md")
    ok("minor-slip-inline" not in w, "advanced keeps all corrections inline")


@test
def t_repo_note_never_a_correction():
    import update_index as ui
    root = sweep_project()
    text = basic_note("> **Repo note:** `Lec-2` gained `fix_pointers.cpp` after class.\n\n"
                      "> **Class correction (\U0001f7e0 high):** said a. Actually b.",
                      "| said | actual |\n| --- | --- |\n| slip one | fixed one |")
    p = root / "notes" / "basic" / "02-title.md"
    p.write_text(text, encoding="utf-8")
    info = ui.analyse(p, "basic", ["Going Deeper"])
    sev = [c["sev"] for c in info["corrections"]]
    eq(len(info["corrections"]), 2, f"corrections {info['corrections']}")
    ok("\U0001f7e1" in sev and "\U0001f7e0" in sev, f"minor slip rows join the list as yellow: {sev}")
    ok(not any("fix_pointers" in (c["said"] + c["actual"]) for c in info["corrections"]), "Repo note counted as a correction")
    # and the lint treats it as plain prose: no warnings about it
    e, w = lint_codes(root, text)
    ok("minor-slip-inline" not in w, "repo note flagged")


@test
def t_note_stats_reading_minutes_and_complexity():
    root = sweep_project()
    prof = root / ".notes-plugin-internal" / "profile.md"
    from notes_lib import notefile
    plain_body = "## TL;DR\n\n" + " ".join(["The cache holds recent lines so the CPU waits less."] * 40) + "\n"
    easy = basic_note("")
    easy = easy.replace("Text here.", " ".join(["Short clear sentence about the stack."] * 30))
    hard = basic_note("")
    jargon = "The TLB and the MMU interact with the NUMA node and the SIMD unit while the ROB retires uops. " * 12
    hard = hard.replace("Text here.", jargon)
    pe, ph = root / "notes" / "basic" / "02-easy.md", root / "notes" / "basic" / "02-hard.md"
    pe.write_text(easy, encoding="utf-8")
    ph.write_text(hard, encoding="utf-8")
    r = run("note_stats.py", pe, "--tier", "basic", "--project", root, "--json", root / "s1.json")
    s1 = jload(root / "s1.json")["notes"][0]["budget"]
    ok(s1["minutes"]["reading"] >= s1["minutes"]["value"], "reading minutes")
    ok(not s1["complexity"]["harder_than_class"], f"easy note flagged: {s1['complexity']}")
    r = run("note_stats.py", ph, "--tier", "basic", "--project", root, "--json", root / "s2.json")
    s2 = jload(root / "s2.json")["notes"][0]["budget"]
    ok(s2["complexity"]["harder_than_class"], f"jargon-dense note not flagged: {s2['complexity']}")
    ok("TLB" in s2["complexity"]["top_undefined"], "top undefined terms")
    # terms defined in an earlier note (bold-defined) stop counting
    ph.rename(root / "notes" / "basic" / "05-hard.md")
    (root / "notes" / "basic" / "02-easy.md").write_text(
        basic_note("").replace("Text here.", "**TLB** (translation lookaside buffer) caches page walks; **MMU**, **NUMA**, **SIMD** and **ROB** too."),
        encoding="utf-8")
    r = run("note_stats.py", root / "notes" / "basic" / "05-hard.md", "--tier", "basic", "--project", root,
            "--json", root / "s3.json")
    s3 = jload(root / "s3.json")["notes"][0]["budget"]
    ok(s3["complexity"]["jargon_per_100w"] < s2["complexity"]["jargon_per_100w"], "earlier vocabulary ignored")
    stats = notefile.stats(plain_body, ())
    ok(stats["minutes"] > 0 and prof.is_file())
    # figures cost 10 s each
    from note_stats import reading_minutes
    eq(reading_minutes({"minutes": 1.0, "figures": 6}), 2.0, "figures at 10 s")


@test
def t_classfigure_and_scripts_have_help():
    for s in ("class_figure.py", "concept_map.py", "sweep_check.py", "init_project.py", "note_stats.py", "lint_note.py"):
        r = run(s, "--help")
        eq(r.returncode, 0, f"{s} --help\n{r.stderr}")


@test
def t_batch_status_summarises_parallel_sessions():
    from notes_lib import layout, ledger
    root = ledger_project("batch")
    lay = layout.Layout(root)
    for k, status in ((2, "ok"), (3, "failed"), (4, "ok")):
        inputs = {"session": {"n": k, "nn": f"{k:02d}", "title": f"T{k}"}, "fingerprints": {}, "inputs_hash": "h",
                  "paths": {}, "note_paths": {}}
        ledger.record(lay, {}, inputs, "basic", status)
    base = "2026-10-06T10:00:00+00:00"
    spans = {2: (0, 20), 3: (1, 12), 4: (2, 25)}                      # minutes after base: they overlap
    for k, (a, b) in spans.items():
        for ev, off in (("start", a), ("end", b)):
            t = f"2026-10-06T10:{off:02d}:00+00:00"
            r = run("timing.py", "mark", "--project", root, "--session", k, "--stage", "digest", ev, "--at", t,
                    "--note", "cost=0.50" if ev == "end" else "")
            eq(r.returncode, 0, r.stderr)
    r = run("batch_status.py", "--project", root, "--json", root / "b.json")
    eq(r.returncode, 1, "a failed session must exit 1: " + r.stdout)
    doc = jload(root / "b.json")
    sm = doc["summary"]
    eq(sm["failed"], ["03"], "failed")
    eq(sm["retry"], ["03"], "retry")
    eq(sm["batch_wall_min"], 25.0, "batch wall clock")
    eq(sm["sum_session_wall_min"], 20.0 + 11.0 + 23.0, "sum of per-session wall")
    ok(sm["speedup"] > 2.0, f"speed-up {sm['speedup']}")
    eq(sm["cost_total"], 1.5, "cost")
    ok("to retry" in r.stdout and "03" in r.stdout, "report must name the sessions to retry")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    bad = []
    ran = 0
    for fn in TESTS:
        if a.k not in fn.__name__:
            continue
        ran += 1
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {fn.__name__}  ({time.time() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            bad.append(fn.__name__)
            print(f"FAIL  {fn.__name__}  ({time.time() - t0:.1f}s)\n      {e}\n{traceback.format_exc(limit=6)}")
    for s in SKIPS:
        print(f"SKIP  {s}")
    print(f"\n{ran - len(bad)}/{ran} passed" + (f"; FAILED: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
