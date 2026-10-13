# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf", "imageio-ffmpeg"]
# ///
"""Self-test for PLAN section 11: work with whatever resources exist (discover, scan_resources, resolve_inputs).

    uv run tests/selftest_discover.py [-k SUBSTRING]

Fixture projects are built at test time under <plugin>/test-output/_s1/ (zero-byte placeholder media; small
real files are copied read-only from the cpp subject repo when it exists). The cpp repo is only read: a
`modified since the stamp` walk (excluding .git) proves it at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import traceback
import wave
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
FIXQ = PLUGIN / "tests" / "fixtures" / "qa"
FIXP = PLUGIN / "tests" / "fixtures" / "profiles"   # synthetic profiles
WORK = PLUGIN / "test-output" / "_s1"
# optional: a real subject project to also test against (read-only); set NOTES_TEST_CPP_ROOT to enable
CPP_ROOT = Path(os.environ.get("NOTES_TEST_CPP_ROOT", str(WORK / "_no_subject_project")))
CPP_RES = CPP_ROOT / "resources"
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
sys.path.insert(0, str(SCRIPTS))

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


# ------------------------------------------------------------------ fixture builders
def scaler(title, start="2026-09-15 10:00", minutes=60, words=400):
    body = " ".join(["the instructor explains the idea and the cache line again."] * (words // 10 + 1))
    return (f"Course Name: Widget Engineering\nLecture Title: {title}\nStart Time: {start}\n"
            f"Duration: {minutes} minutes\nModel use: test\n\n==================================================\n\n{body}\n")


def prose(words=500):
    return " ".join(["so today we look at pointers and references and how they behave."] * (words // 10 + 1)) + "\n"


def project(name, sessions=(), extra_fm="", files=None) -> Path:
    """Build WORK/name with a profile (sessions = [(n, title, stem|None, date|None)]) and resource files."""
    root = WORK / name
    rmtree(root)
    (root / ".notes-plugin-internal").mkdir(parents=True)
    rows = []
    for n, title, stem, date in sessions:
        parts = [f"n: {n}", f'title: "{title}"'] + ([f'stem: "{stem}"'] if stem else []) + ([f"date: {date}"] if date else [])
        rows.append("  - {" + ", ".join(parts) + "}")
    fm = ["schema: 1", "id: t", 'title: "Test Course"', 'short: "T"', "prefix: TT", extra_fm]
    if rows:
        fm.append("sessions:")
        fm += rows
    (root / ".notes-plugin-internal" / "profile.md").write_text("---\n" + "\n".join(x for x in fm if x) + "\n---\n\n# body\n", encoding="utf-8")
    res = root / "resources"
    res.mkdir()
    for rel, content in (files or {}).items():
        f = res / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            f.write_bytes(content)
        else:
            f.write_text(content, encoding="utf-8")
    return root


def resolve(root, n, *extra):
    out = WORK / f"{root.name}-s{n}.json"
    if out.exists():
        out.unlink()
    r = run("resolve_inputs.py", "--project", root, "--data-dir", WORK / "dd", "--session", n, "--json", out,
            "--dry-run", *extra)
    return r, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def scan(root, *extra):
    out = WORK / f"{root.name}-scan.json"
    r = run("scan_resources.py", "--project", root, "--data-dir", WORK / "dd", "--json", out, *extra)
    ok(r.returncode == 0, r.stdout + r.stderr)
    return json.loads(out.read_text(encoding="utf-8")), r.stdout


def assigned(sc, name):
    f = next(x for x in sc["files"] if x["rel"] == name or Path(x["path"]).name == name)
    return f


PR = [(2, "Memory Layout", "Memory_Layout_-_Class_Scaler_Academy", None),
      (3, "Memory Layout 2", "Memory_Layout_2_-_Class_Scaler_Academy", None)]


# ------------------------------------------------------------------ section 11.6 cases
@test
def t_a_transcript_only():
    root = project("a", [(1, "Intro to Widgets", "Intro_to_Widgets_-_Class", None)],
                   files={"Intro_to_Widgets_-_Class.txt": scaler("Intro to Widgets")})
    r, inp = resolve(root, 1)
    eq(r.returncode, 0, r.stderr)
    eq(inp["speech"], {"primary": "transcript", "sources": [{"kind": "transcript", "path": inp["transcript"]["path"]}]}, "speech")
    eq((inp["video_action"], inp["course_docs"], inp["unassigned"]), ("skip", [], []), "plain")
    eq(inp["existing_notes"], {"basic": None, "advanced": None}, "no notes yet")
    eq(inp["transcript"]["format"], "txt", "format")


@test
def t_b_recording_and_whisper_no_transcript():
    stem = "Heaps_and_Allocators_-_Class_Scaler_Academy"
    root = project("b", [(4, "Heaps and Allocators", stem, None)],
                   files={f"videos/{stem}.mp4": b"", f"{stem}.whisper.json": "{}"})
    r, inp = resolve(root, 4)
    eq(r.returncode, 0, r.stderr)
    eq(inp["speech"]["primary"], "whisper", "whisper primary")
    eq([s["kind"] for s in inp["speech"]["sources"]], ["whisper", "recording"], "sources")
    eq(inp["transcript"], None, "no transcript")
    eq(inp["video_action"], "run", "recording present, no frames: run (whisper reused by the pipeline)")
    # the recording alone is the only speech source -> Whisper is forced
    root2 = project("b2", [(4, "Heaps and Allocators", stem, None)], files={f"videos/{stem}.mp4": b""})
    r, inp = resolve(root2, 4)
    eq(r.returncode, 0, r.stderr)
    eq((inp["speech"]["primary"], inp["video_action"]), ("recording", "run-force-whisper"), "recording only")
    eq(resolve(root2, 4, "--video", "reuse")[1]["video_action"], "run-force-whisper", "reuse cannot help")
    # nothing at all -> exit 2, unless --allow-no-speech
    root3 = project("b3", [(4, "Heaps and Allocators", stem, None)], files={"deck.pdf": b""})
    r, _ = resolve(root3, 4)
    eq(r.returncode, 2, "no speech")
    ok("no speech source" in r.stderr and "--allow-no-speech" in r.stderr, r.stderr)
    r, inp = resolve(root3, 4, "--allow-no-speech")
    eq(r.returncode, 0, r.stderr)
    eq((inp["speech"]["primary"], inp["speech"]["sources"], inp["no_speech"]), (None, [], True), "documents only")


@test
def t_c_renamed_transcript_and_slides():
    root = project("c", PR, files={
        "Lecture 3 - pointers part 2.txt": prose(),
        "session3_slides.pdf": b"",
        "Memory_Layout_-_Class_Scaler_Academy.txt": scaler("Memory Layout"),
    })
    sc, _ = scan(root)
    eq(assigned(sc, "Lecture 3 - pointers part 2.txt")["n"], 3, "renamed transcript -> 3")
    eq(assigned(sc, "session3_slides.pdf")["n"], 3, "renamed slides -> 3")
    eq(assigned(sc, "Memory_Layout_-_Class_Scaler_Academy.txt")["n"], 2, "exact stem -> 2")
    r, inp = resolve(root, 3)
    eq(r.returncode, 0, r.stderr)
    ok(inp["transcript"]["path"].endswith("Lecture 3 - pointers part 2.txt"), inp["transcript"])
    eq([Path(x).name for x in inp["slides"]], ["session3_slides.pdf"], "slides")
    r, inp2 = resolve(root, 2)
    ok(inp2["transcript"]["path"].endswith("Memory_Layout_-_Class_Scaler_Academy.txt"), "session 2 keeps its own")
    eq(inp2["slides"], [], "no slides leak into 2")


@test
def t_d_syllabus_and_course_wide_docx():
    root = project("d", [(1, "Intro to Widgets", "Intro_to_Widgets_-_Class", None),
                         (2, "Widget Pools", "Widget_Pools_-_Class", None)],
                   files={"Intro_to_Widgets_-_Class.txt": scaler("Intro to Widgets"),
                          "Widget_Pools_-_Class.txt": scaler("Widget Pools", "2026-09-16 10:00"),
                          "Course Syllabus.pdf": b"", "Final Project Brief.docx": b""})
    for n in (1, 2):
        r, inp = resolve(root, n)
        eq(r.returncode, 0, r.stderr)
        got = {Path(x["path"]).name: x["kind"] for x in inp["course_docs"]}
        eq(got, {"Course Syllabus.pdf": "syllabus", "Final Project Brief.docx": "handout"}, f"course_docs s{n}")
        ok(all(x["reason"] for x in inp["course_docs"]), "reasons")
        eq(inp["unassigned"], [], "nothing unassigned")


@test
def t_e_audio_only():
    root = project("e", [(1, "Intro to Widgets", "Intro_to_Widgets_-_Class", None)],
                   files={"Intro_to_Widgets_-_Class.m4a": b""})
    r, inp = resolve(root, 1)
    eq(r.returncode, 0, r.stderr)
    eq((inp["speech"]["primary"], inp["video_action"]), ("recording", "whisper-only"), "audio-only")
    eq(inp["frames"], None, "no frames")
    ok(inp["recording"].endswith(".m4a"), "recording is the audio")
    # a Whisper output already there -> nothing to run
    root2 = project("e2", [(1, "Intro to Widgets", "Intro_to_Widgets_-_Class", None)],
                    files={"Intro_to_Widgets_-_Class.m4a": b"", "Intro_to_Widgets_-_Class.whisper.json": "{}"})
    eq(resolve(root2, 1)[1]["video_action"], "skip", "whisper exists")
    # pipeline.py: audio input never runs keyframes
    wav = WORK / "e-tone.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 1600)
    out = WORK / "e-pipeline"
    rmtree(out)
    r = run("video/pipeline.py", wav, "--out", out, "--whisper", "skip")
    pj = json.loads((out / "pipeline.json").read_text(encoding="utf-8"))
    eq(pj["steps"]["keyframes"]["status"], "skipped", "audio: keyframes skipped")
    ok(not (out / "frames").exists(), "no frames dir for audio")
    eq(r.returncode, 1, "audio-only without any Whisper is not ok")


@test
def t_f_ambiguous_file():
    root = project("f", PR, files={
        "Memory_Layout_-_Class_Scaler_Academy.txt": scaler("Memory Layout"),
        "layout slides.pdf": b""})
    sc, out = scan(root)
    f = assigned(sc, "layout slides.pdf")
    eq(f["scope"], "unassigned", "ambiguous stays unassigned")
    ok(len(f["candidates"]) == 2 and {c["n"] for c in f["candidates"]} == {2, 3}, f["candidates"])
    ok("UNASSIGNED" in out and "layout slides.pdf" in out, out)
    r, inp = resolve(root, 2)
    eq([Path(u["path"]).name for u in inp["unassigned"]], ["layout slides.pdf"], "inputs.unassigned")
    ok(len(inp["unassigned"][0]["candidates"]) == 2 and {"n", "score"} <= set(inp["unassigned"][0]["candidates"][0]), "candidate shape")
    ok(any("could not be assigned" in w for w in inp["warnings"]), "warned")
    eq(inp["slides"], [], "not guessed into slides")


@test
def t_g_new_subject_derived_sessions():
    root = project("g", [], files={
        "Beta_Lecture_-_Class.txt": scaler("Order Matching", "2026-09-16 10:00"),
        "Alpha_Lecture_-_Class.txt": scaler("Intro to Widgets", "2026-09-15 10:00"),
        "videos/Alpha_Lecture_-_Class.mp4": b"",
        "alpha_slides.pdf": b""})
    sc, _ = scan(root)
    eq([(s["n"], s["title"], s["derived"]) for s in sc["sessions"]],
       [(1, "Intro to Widgets", True), (2, "Order Matching", True)], "derived table ordered by header date")
    eq(sorted(f["kind"] for f in sc["sessions"][0]["files"]), ["recording", "transcript"], "recording follows its transcript")
    r, inp = resolve(root, 1)
    eq(r.returncode, 0, r.stderr)
    eq((inp["session"]["title"], inp["session"]["derived"], inp["speech"]["primary"]), ("Intro to Widgets", True, "transcript"), "resolve works unwritten")
    before = (root / ".notes-plugin-internal" / "profile.md").read_text(encoding="utf-8")
    sc, out = scan(root, "--write-sessions")
    after = (root / ".notes-plugin-internal" / "profile.md").read_text(encoding="utf-8")
    ok("sessions:" in after and 'title: "Intro to Widgets"' in after and "n: 2" in after, after)
    sc2, _ = scan(root, "--write-sessions")          # idempotent: nothing derived the second time
    eq([s["derived"] for s in sc2["sessions"]], [False, False], "now in the profile")
    eq((root / ".notes-plugin-internal" / "profile.md").read_text(encoding="utf-8"), after, "no rewrite on the second run")
    # init_project with files already present writes the table itself
    p2 = WORK / "g-init"
    rmtree(p2)
    shutil.copytree(root / "resources", p2 / "resources")
    r = run("init_project.py", "--project", p2, "--profile", "_template", "--data-dir", WORK / "dd")
    eq(r.returncode, 0, r.stderr)
    txt = (p2 / ".notes-plugin-internal" / "profile.md").read_text(encoding="utf-8")
    fm = txt.split("---")[1]
    ok('title: "Intro to Widgets"' in fm and "n: 2" in fm and "First session title" not in fm, fm[-600:])
    ok("session  1 'Intro to Widgets' [derived]" in r.stdout, r.stdout)


@test
def t_h_vtt_transcript():
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nWelcome to <c>widgets</c>.\n\n"
           "2\n00:00:04.500 --> 00:00:09.000\nToday: the free list.\n\n00:10:00.000 --> 00:10:05.000\nBye now.\n")
    root = project("h", [(1, "Intro to Widgets", "Intro_to_Widgets_-_Class", None)],
                   files={"Intro_to_Widgets_-_Class.vtt": vtt})
    r, inp = resolve(root, 1)
    eq(r.returncode, 0, r.stderr)
    eq((inp["transcript"]["format"], inp["speech"]["primary"], inp["session"]["duration_min"]), ("vtt", "transcript", 10), "vtt")
    out = WORK / "h-prep"
    rmtree(out)
    r = run("prep_transcript.py", root / "resources" / "Intro_to_Widgets_-_Class.vtt", "--out", out)
    eq(r.returncode, 0, r.stdout + r.stderr)
    body = (out / "transcript.wrapped.txt").read_text(encoding="utf-8")
    ok("Welcome to widgets. Today: the free list." in body and "<c>" not in body and "\nBye now." in body, body)
    tm = json.loads((out / "timings.json").read_text(encoding="utf-8"))
    eq((len(tm), tm[0]["start"], tm[2]["end"]), (3, 1.0, 605.0), "timings")
    # srt, too
    srt = "1\n00:00:01,000 --> 00:00:02,500\nHello srt\n\n2\n00:00:03,000 --> 00:00:04,000\nline two\n"
    root2 = project("h2", [(1, "Intro to Widgets", "Intro_to_Widgets_-_Class", None)], files={"Intro_to_Widgets_-_Class.srt": srt})
    eq(resolve(root2, 1)[1]["transcript"]["format"], "srt", "srt")


# ------------------------------------------------------------------ more behaviour
@test
def t_longest_recording_wins():
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    stem = "Intro_to_Widgets_-_Class"
    root = project("rl", [(1, "Intro to Widgets", stem, None)], files={f"{stem}.txt": scaler("Intro to Widgets", minutes=1)})
    (root / "resources" / "videos").mkdir()
    for name, secs in ((f"{stem}.mp4", 2), ("Intro to Widgets v2.mp4", 6)):
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=64x36:d={secs}",
                            "-pix_fmt", "yuv420p", str(root / "resources" / "videos" / name)], capture_output=True)
        eq(r.returncode, 0, r.stderr.decode(errors="replace"))
    r, inp = resolve(root, 1)
    eq(r.returncode, 0, r.stderr)
    eq(Path(inp["recording"]).name, "Intro to Widgets v2.mp4", "6 s clip beats the exact-stem 2 s clip")
    eq(sorted(round(c["duration_s"]) for c in inp["recording_candidates"]), [2, 6], "candidates with durations")
    ok(any("chose Intro to Widgets v2.mp4" in w for w in inp["warnings"]), inp["warnings"])
    ok(any("looks incomplete" in w for w in inp["warnings"]), "6 s vs a 1-minute header: incomplete")


@test
def t_fetch_code_root_fallback():
    git = shutil.which("git")
    if not git:
        print("      (skipped: no git)")
        return
    repo = WORK / "fake-remote"
    rmtree(repo)
    repo.mkdir(parents=True)
    (repo / "more_pointers.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    (repo / "Lec-1").mkdir()
    (repo / "Lec-1" / "a.cpp").write_text("int a;\n", encoding="utf-8")
    (repo / "Heaps and Allocators").mkdir()
    (repo / "Heaps and Allocators" / "pool.cpp").write_text("int pool;\n", encoding="utf-8")
    g = lambda *x: subprocess.run([git, "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *x],  # noqa: E731
                                  check=True, capture_output=True)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-q", "-m", "x")
    out = WORK / "code-fb"
    r = run("fetch_code.py", "--repo", "o/n", "--dir", "Lec-4", "--title", "Heaps and Allocators", "--out", out,
            "--force-clone", "--clone-url", repo.as_posix(), "--data-dir", WORK / "dd")
    eq(r.returncode, 0, r.stdout + r.stderr)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    eq((m["fallback"], m["relevance"]), ("root", "unverified"), "manifest marks")
    eq(sorted(f["path"] for f in m["fetched"]), ["Heaps and Allocators/pool.cpp", "more_pointers.cpp"], "fetched")
    ok(all(f["fallback"] == "root" and f["relevance"] == "unverified" for f in m["fetched"]), "per-file marks")
    # an existing session folder is not a fallback
    r = run("fetch_code.py", "--repo", "o/n", "--dir", "Lec-1", "--out", WORK / "code-ok", "--force-clone",
            "--clone-url", repo.as_posix(), "--data-dir", WORK / "dd")
    eq(r.returncode, 0, r.stderr)
    ok("fallback" not in json.loads((WORK / "code-ok" / "manifest.json").read_text(encoding="utf-8")), "no fallback")
    import fetch_code
    sel, folders = fetch_code.fallback_select(
        [{"type": "blob", "path": "x.cpp"}, {"type": "blob", "path": "a.exe"}, {"type": "tree", "path": "Lec-9"}], None)
    eq(([e["path"] for e in sel], folders), (["x.cpp"], []), "api-path selection")


@test
def t_digest_speech_primary():
    src = (FIXQ / "digest" / "digest-good.md").read_text(encoding="utf-8")
    good = src.replace("repo_moved_on: false", "speech_primary: whisper\nrepo_moved_on: false", 1)
    p = WORK / "digest-sp.md"
    p.write_text(good, encoding="utf-8")
    eq(run("check_digest.py", p).returncode, 0, "valid speech_primary")
    p.write_text(good.replace("speech_primary: whisper", "speech_primary: telepathy"), encoding="utf-8")
    r = run("check_digest.py", p)
    ok(r.returncode == 1 and "fm-speech" in r.stdout, r.stdout)


@test
def t_existing_notes_and_prior():
    root = project("n", [(1, "Intro to Widgets", "Intro_to_Widgets_-_Class", None),
                         (2, "Widget Pools", "Widget_Pools_-_Class", None)],
                   files={"Intro_to_Widgets_-_Class.txt": scaler("Intro to Widgets"),
                          "Widget_Pools_-_Class.txt": scaler("Widget Pools", "2026-09-16 10:00")})
    for rel in ("notes/basic/01-intro.md", "notes/basic/02-pools.md", "notes/advanced/02a-pools-deep.md"):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# x\n", encoding="utf-8")
    r, inp = resolve(root, 2)
    eq(r.returncode, 0, r.stderr)
    eq({k: (Path(v).name if v else None) for k, v in inp["existing_notes"].items()},
       {"basic": "02-pools.md", "advanced": None}, "a companion 02a is never the session's note")
    ok(any("02a-pools-deep.md" in w and "companion" in w for w in inp.get("warnings", [])), "companion reported as an anomaly")
    eq([Path(x).name for x in inp["prior_notes"]["project"]["basic"]], ["01-intro.md"], "prior = strictly earlier")
    eq(resolve(root, 1)[1]["existing_notes"], {"basic": str(root / "notes/basic/01-intro.md").replace("\\", "/"), "advanced": None}, "s1")


@test
def t_digest_null_transcript():
    src = (FIXQ / "digest" / "digest-good.md").read_text(encoding="utf-8")
    line = '  transcript: "resources/Memory_Layout_-_Class_Scaler_Academy.txt"'
    ok(line in src, "fixture line")
    p = WORK / "digest-null.md"
    p.write_text(src.replace(line, "  transcript: null"), encoding="utf-8")
    r = run("check_digest.py", p)
    eq(r.returncode, 0, r.stdout + r.stderr)


@test
def t_lint_dead_sibling_link():
    d = WORK / "lint"
    rmtree(d)
    (d / "notes").mkdir(parents=True)
    shutil.copytree(FIXQ / "tier-basic-good" / "basic" / "src", d / "notes" / "basic")
    note = next((d / "notes" / "basic").glob("02-*.md"))
    note.write_text(note.read_text(encoding="utf-8") + "\nNext: [pools](03-widget-pools.md).\n", encoding="utf-8")
    r = run("lint_note.py", note, "--profile", FIXQ / "profile-qa.md", "--tier", "basic")
    ok("dead-link" in r.stdout and "sibling note that does not exist" in r.stdout, r.stdout)
    ok("ERROR" not in r.stdout.split("dead-link")[0].split("\n")[-1], "a dead link is only a warning")


# ------------------------------------------------------------------ the real cpp project (read-only)
def cpp_project() -> Path | None:
    """A copy of the real subject's own profile (user-made; none ships with the plugin), or None."""
    src = next((q for q in (CPP_ROOT / ".notes-plugin-internal" / "profile.md", CPP_ROOT / ".notes" / "profile.md")
                if q.is_file()), None)
    if src is None:
        return None
    prof = src.read_text(encoding="utf-8")
    root = WORK / "real"
    rmtree(root)
    (root / ".notes-plugin-internal").mkdir(parents=True)
    prof = prof.replace("\ninstitution:", f'\nresources_dir: "{CPP_RES.as_posix()}"\ninstitution:', 1)
    (root / ".notes-plugin-internal" / "profile.md").write_text(prof, encoding="utf-8")
    return root


@test
def t_real_cpp_resources():
    if not CPP_RES.is_dir():
        print("      (skipped: cpp subject repo not present)")
        return
    root = cpp_project()
    if root is None:
        print("      (skipped: the real subject has no profile.md)")
        return
    sc, _ = scan(root)
    by = {Path(f["path"]).name: f for f in sc["files"]}
    eq(by["Memory_Layout_2_-_Class_Scaler_Academy.txt"]["n"], 3, "P&R 2 transcript -> 3 (not 2)")
    eq(by["Memory_Layout_-_Class_Scaler_Academy.txt"]["n"], 2, "P&R -> 2")
    syl = next(f for f in sc["files"] if f["kind"] == "syllabus")
    eq(syl["scope"], "course", "syllabus course-wide")
    ok(any(Path(c["path"]).name.startswith("Batch 2030") for c in sc["course_docs"]), "syllabus in course_docs")
    # nothing silently dropped: every file on disk is in the scan, or is README / dotfile / frames internals
    listed = {Path(f["path"]).as_posix() for f in sc["files"]}
    frames_dirs = [Path(f["path"]).as_posix() for f in sc["files"] if f["kind"] == "frames"]
    missing = []
    for f in sorted(CPP_RES.rglob("*")):
        if not f.is_file() or f.name.startswith(".") or f.name.lower() == "readme.md":
            continue
        q = f.as_posix()
        if q in listed or any(q.startswith(d + "/") for d in frames_dirs):
            continue
        missing.append(q)
    eq(missing, [], "files missing from the scan")
    ok(all(f["scope"] in ("session", "course", "unassigned") for f in sc["files"]), "every file has a scope")
    eq([f["rel"] for f in sc["files"] if f["scope"] == "unassigned"], [], "cpp has no unassigned files")

    # session 2: unchanged in substance
    r, inp = resolve(root, 2)
    eq(r.returncode, 0, r.stderr)
    eq((inp["session"]["n"], inp["session"]["title"], inp["session"]["date"], inp["session"]["duration_min"]),
       (2, "Memory Layout", "2026-08-22", 120), "session")
    eq(Path(inp["transcript"]["path"]).name, "Memory_Layout_-_Class_Scaler_Academy.txt", "transcript")
    eq(inp["whisper"]["source"], "resources", "whisper")
    eq((inp["frames"]["count"], inp["video_action"]), (149, "reuse"), "frames")
    eq([Path(x).name for x in inp["slides"]], ["L2.pdf"], "slides")
    eq([Path(h["path"]).name for h in inp["handouts"]], ["Capstone_ Build a Low-Latency Order Book.docx"], "handouts")
    eq(inp["instructor_code"]["dir"], "Lec-2", "instructor code")
    eq(inp["speech"]["primary"], "transcript", "speech primary")
    eq(inp["unassigned"], [], "unassigned")

    # session 3 is its own session; session 4 has no transcript: Whisper is primary
    r, inp3 = resolve(root, 3)
    eq(Path(inp3["transcript"]["path"]).name, "Memory_Layout_2_-_Class_Scaler_Academy.txt", "s3 transcript")
    r, inp4 = resolve(root, 4)
    eq(r.returncode, 0, r.stderr)
    eq((inp4["speech"]["primary"], inp4["transcript"]), ("whisper", None), "s4 whisper primary")
    eq(inp4["video_action"], "reuse", "s4 frames reused")


@test
def t_real_names_with_only_video_and_whisper():
    """Heaps_and_Allocators with only videos/<stem>.mp4 + <stem>.whisper.json (zero-byte stand-ins)."""
    stem = "Templates_-_Lecture"
    prof = (FIXP / "alpha.md").read_text(encoding="utf-8")
    root = WORK / "mm"
    rmtree(root)
    (root / ".notes-plugin-internal").mkdir(parents=True)
    (root / ".notes-plugin-internal" / "profile.md").write_text(prof, encoding="utf-8")
    (root / "resources" / "videos").mkdir(parents=True)
    (root / "resources" / "videos" / f"{stem}.mp4").write_bytes(b"")
    (root / "resources" / f"{stem}.whisper.json").write_text("{}", encoding="utf-8")
    r, inp = resolve(root, 3)
    eq(r.returncode, 0, r.stderr)
    eq(inp["speech"]["primary"], "whisper", "speech.primary")
    eq(inp["session"]["title"], "Templates", "title")


@test
def t_layout_check():
    """PLAN 19: class files outside resources/ are flagged with a destination, and --fix-layout moves them."""
    root = project("lay", [(1, "Intro to Widgets", None, None)],
                   files={"videos/ok_recording.mp4": b"", "Slides_L1.pdf": b"%PDF-1.4"})
    (root / "Intro_to_Widgets_-_Class_Scaler_Academy.txt").write_text(scaler("Intro to Widgets"), encoding="utf-8")
    (root / "Lecture_2_recording.mp4").write_bytes(b"")
    (root / "Downloads").mkdir()
    (root / "Downloads" / "Lecture 3 slides.pdf").write_bytes(b"%PDF-1.4")
    (root / "Class5_Student_Pack").mkdir()
    (root / "Class5_Student_Pack" / "exercise.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (root / "videos").mkdir()
    (root / "videos" / "L4.mp4").write_bytes(b"")
    (root / "notes").mkdir()
    (root / "notes" / "stray_Class_Scaler_Academy.txt").write_text(scaler("Intro to Widgets"), encoding="utf-8")
    # NOT flagged: user code, markers, unrelated files, tier folders, legacy notes
    (root / "Classwork" / "1").mkdir(parents=True)
    (root / "Classwork" / "1" / "main.cpp").write_text("int main(){}", encoding="utf-8")
    (root / "Classwork" / "lecture_9_demo.mp4").write_bytes(b"")
    (root / "mycode").mkdir()
    (root / "mycode" / "CMakeLists.txt").write_text("project(x)", encoding="utf-8")
    (root / "mycode" / "lecture_8.mp4").write_bytes(b"")
    (root / "CLAUDE.md").write_text("# project instructions\n", encoding="utf-8")
    (root / "README.md").write_text("# readme\n", encoding="utf-8")
    (root / "package.json").write_text("{}", encoding="utf-8")
    (root / "shopping.csv").write_text("a,b\n", encoding="utf-8")
    (root / "resume.pdf").write_bytes(b"%PDF-1.4")
    (root / "notes" / "basic").mkdir()
    (root / "notes" / "basic" / "01-intro.md").write_text("# Intro\n", encoding="utf-8")
    (root / "pdf" / "basic").mkdir(parents=True)
    (root / "pdf" / "basic" / "Lecture_1_slides.pdf").write_bytes(b"%PDF-1.4")
    (root / "legacy-notes").mkdir()
    (root / "legacy-notes" / "01-widgets.md").write_text("# Widgets\n\nsome notes\n", encoding="utf-8")

    sc, out = scan(root)
    found = {f["rel"]: f for f in sc["outside_resources"]}
    want = {
        "Intro_to_Widgets_-_Class_Scaler_Academy.txt": ("transcript", "resources/Intro_to_Widgets_-_Class_Scaler_Academy.txt"),
        "Lecture_2_recording.mp4": ("recording", "resources/videos/Lecture_2_recording.mp4"),
        "Downloads/Lecture 3 slides.pdf": ("slides", "resources/Lecture 3 slides.pdf"),
        "Class5_Student_Pack/exercise.csv": ("data", "resources/Class5_Student_Pack/exercise.csv"),
        "videos/L4.mp4": ("recording", "resources/videos/L4.mp4"),
        "notes/stray_Class_Scaler_Academy.txt": ("transcript", "resources/stray_Class_Scaler_Academy.txt"),
    }
    eq(sorted(found), sorted(want), "flagged set")
    for rel, (kind, dest) in want.items():
        eq((found[rel]["kind"], found[rel]["dest_rel"]), (kind, dest), rel)
        ok(found[rel]["reason"] and found[rel]["suggested_destination"].endswith(dest), rel)
    ok("Files outside resources/" in out, "block printed")

    # without --confirm: report only
    r = run("scan_resources.py", "--project", root, "--data-dir", WORK / "dd", "--fix-layout")
    ok(r.returncode == 0 and "nothing moved" in r.stdout, r.stdout + r.stderr)
    ok((root / "Lecture_2_recording.mp4").exists(), "not moved without --confirm")

    # a clash is refused, everything else moves
    (root / "resources" / "videos" / "L4.mp4").write_bytes(b"existing")
    r = run("scan_resources.py", "--project", root, "--data-dir", WORK / "dd", "--fix-layout", "--confirm")
    ok(r.returncode == 0, r.stdout + r.stderr)
    ok("REFUSED videos/L4.mp4" in r.stdout, r.stdout)
    eq((root / "resources" / "videos" / "L4.mp4").read_bytes(), b"existing", "clash never overwrites")
    ok((root / "videos" / "L4.mp4").exists(), "refused file stays put")
    ok((root / "resources" / "videos" / "Lecture_2_recording.mp4").exists(), "recording moved")
    ok((root / "resources" / "Lecture 3 slides.pdf").exists(), "slides moved flat")
    ok((root / "resources" / "Class5_Student_Pack" / "exercise.csv").exists(), "pack folder kept")
    ok((root / "resources" / "Intro_to_Widgets_-_Class_Scaler_Academy.txt").exists(), "transcript moved")
    sc2, _ = scan(root)
    eq([f["rel"] for f in sc2["outside_resources"]], ["videos/L4.mp4"], "only the clash remains")
    # untouched: code folders, tier folders, unrelated files
    for rel in ("Classwork/lecture_9_demo.mp4", "mycode/lecture_8.mp4", "resume.pdf", "shopping.csv",
                "pdf/basic/Lecture_1_slides.pdf", "legacy-notes/01-widgets.md"):
        ok((root / rel).exists(), rel + " must not be moved")
    # a clean project reports nothing
    clean = project("lay2", [(1, "Intro to Widgets", None, None)],
                    files={"Intro_to_Widgets_-_Class.txt": scaler("Intro to Widgets"), "videos/a.mp4": b""})
    sc3, out3 = scan(clean)
    eq(sc3["outside_resources"], [], "clean")
    ok("Files outside resources/" not in out3, "no block when clean")
    # the other entry points carry it in --json too
    pj = WORK / "lay-plan.json"
    r = run("plan_updates.py", "--project", root, "--data-dir", WORK / "dd", "--json", pj)
    ok(r.returncode == 0, r.stdout + r.stderr)
    eq([f["rel"] for f in json.loads(pj.read_text(encoding="utf-8"))["outside_resources"]], ["videos/L4.mp4"], "plan json")
    ok("Files outside resources/" in r.stdout, "plan prints block")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    a = ap.parse_args()
    rmtree(WORK)
    WORK.mkdir(parents=True)
    stamp = WORK / ".stamp"
    stamp.write_text("x")
    t_stamp = stamp.stat().st_mtime
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
    # the subject repo was only read
    if CPP_RES.is_dir() and len(CPP_ROOT.parents) > 2:
        touched = []
        for dp, dn, fns in os.walk(CPP_ROOT.parents[2]):
            dn[:] = [d for d in dn if d != ".git"]
            for f in fns:
                p = Path(dp) / f
                try:
                    if p.stat().st_mtime > t_stamp:
                        touched.append(p.as_posix())
                except OSError:
                    pass
        if touched:
            bad.append("subject-repo-untouched")
            print("FAIL  subject repo modified:", touched[:5])
        else:
            print("PASS  subject repo untouched (no file newer than the stamp, .git excluded)")
    print(f"\n{len(TESTS) - len(bad)}/{len(TESTS)} passed" + (f"; FAILED: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
