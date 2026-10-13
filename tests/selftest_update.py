# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf", "imageio-ffmpeg"]
# ///
"""Self-test for PLAN section 13 (ongoing use: ledger, plan_updates, order, no duplicates, migration).

    uv run tests/selftest_update.py [-k SUBSTRING]

Fixture projects are built at test time under <plugin>/test-output/_b1/ from small real files copied
read-only from the cpp subject repo (transcripts, one PDF, a whisper file) plus zero-byte media. No LLM runs:
notes are stubbed by writing markdown directly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import traceback
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
# optional read-only real subject projects for extra regression tests; every test that needs one skips when absent
SUBJ_CPP = os.environ.get("NOTES_TEST_CPP_ROOT", "/nonexistent/cpp-subject")
SUBJ_FDE = os.environ.get("NOTES_TEST_FDE_ROOT", "/nonexistent/fde-subject")
SUBJ_CV = os.environ.get("NOTES_TEST_CV_ROOT", "/nonexistent/cv-subject")
SCRIPTS = PLUGIN / "scripts"
FIX = PLUGIN / "tests" / "fixtures"
WORK = PLUGIN / "test-output" / "_b1"
DATA = WORK / "_data"
CPP_ROOT = Path(SUBJ_CPP)
RES = CPP_ROOT / "resources"
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
sys.path.insert(0, str(SCRIPTS))

S1 = "Getting_started_with_C++_-_Class_Scaler_Academy"
S2 = "Pointers_and_References_-_Class_Scaler_Academy"
S3 = "Capstone_Project_-_Class_Scaler_Academy"
S4 = "Fast_Orderbooks_-_Class_Scaler_Academy"
INTERNAL = ".notes-plugin-internal"

PROFILE = f"""---
schema: 1
id: cpp
title: "High-Performance C++ [HFT]"
short: "HP C++"
prefix: CPP
sessions:
  # the course table (a comment that must survive appends)
  - {{n: 1, title: "Getting started with C++", stem: "{S1}", date: 2026-08-19}}
  - {{n: 2, title: "Pointers and References", stem: "{S2}", date: 2026-08-22}}
  - {{n: 3, title: "Capstone Project", stem: "{S3}", date: 2026-10-05}}
asr:
  language: en
---

# fixture profile
"""

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: got {got!r}, want {want!r}"


def ok(cond, label=""):
    assert cond, label


def run(script, *args, timeout=900):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env.pop("LECTURE_NOTES_DATA", None)
    return subprocess.run([UV, "run", "--quiet", "--script", str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                          timeout=timeout)


def rmtree(p: Path) -> None:
    def onerr(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if p.exists():
        shutil.rmtree(p, onerror=onerr)


NOTE = """---
title: "{t}"
session: {n}
tier: basic
profile: cpp
date: 2026-08-19
duration_min: 120
sources: []
instructor_code: null
revision_minutes: 20
verified_as_of: 2026-10-06
---

# {nn} · {t}

## TL;DR

- stub note for the ledger tests.
"""


def stub_note(proj: Path, tier: str, nn: str, slug: str, title: str = "Stub") -> Path:
    p = proj / "notes" / tier / f"{nn}-{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(NOTE.format(t=title, n=int(nn), nn=nn), encoding="utf-8", newline="\n")
    return p


def mk_project(name: str, sessions_files=True) -> Path:
    proj = WORK / name
    rmtree(proj)
    (proj / INTERNAL).mkdir(parents=True)
    (proj / INTERNAL / "profile.md").write_text(PROFILE, encoding="utf-8", newline="\n")
    (proj / "resources" / "videos").mkdir(parents=True)
    res = proj / "resources"
    if sessions_files:
        for stem in (S1, S2, S3):
            shutil.copy2(RES / f"{stem}.txt", res / f"{stem}.txt")
        (res / "videos" / f"{S1}.mp4").write_bytes(b"")                      # zero-byte placeholder
        shutil.copy2(RES / "Pointers_and_References_2_-_Class_Scaler_Academy.whisper.json",
                     res / f"{S1}.whisper.json")                              # archived whisper (any real small json)
        shutil.copytree(FIX / "ingest" / "mini-project" / "resources" / "frames" / "Intro_to_Widgets_-_Class",
                        res / "frames" / S1)
    return proj


def plan(proj: Path, *extra, tiers="basic"):
    js = proj / INTERNAL / "plan.json"
    r = run("plan_updates.py", "--project", proj, "--tiers", tiers, "--json", js, "--data-dir", DATA, *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(js.read_text(encoding="utf-8")), r


def row(pl, nn):
    return next(x for x in pl["sessions"] if x["nn"] == nn)


@test
def t1_adopt_then_plan():
    proj = mk_project("proj")
    stub_note(proj, "basic", "01", "getting-started")
    r = run("ledger.py", "adopt", "--project", proj, "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    st = json.loads((proj / INTERNAL / "state.json").read_text(encoding="utf-8"))
    ent = st["sessions"]["01"]
    eq(ent["tiers"]["basic"]["note"], "notes/basic/01-getting-started.md", "note path")
    eq(ent["slug"], "getting-started", "slug")
    ok(ent["inputs"]["transcript"]["sha256"] and ent["inputs"]["whisper"] and ent["inputs"]["frames"]["count"], "fingerprints")
    ok(st["schema"] == 1 and "plugin_version" in st, "state header")
    pl, _ = plan(proj)
    eq(row(pl, "01")["status"], "up-to-date", pl)
    eq(row(pl, "02")["status"], "new")
    eq(row(pl, "02")["action"], "write")
    eq(row(pl, "03")["status"], "new")
    eq(pl["order"], ["01", "02", "03"], "course order")
    # resolve_inputs exposes the fixed note path
    r = run("resolve_inputs.py", "--project", proj, "--session", "1", "--dry-run", "--data-dir", DATA)
    eq(r.returncode, 0, r.stderr)
    r = run("resolve_inputs.py", "--project", proj, "--session", "1", "--json", proj / "i1.json", "--dry-run", "--data-dir", DATA)
    inp = json.loads((proj / "i1.json").read_text(encoding="utf-8"))
    eq(Path(inp["note_paths"]["basic"]).name, "01-getting-started.md", "note_paths.basic")
    eq(inp["note_paths"]["advanced"], None, "note_paths.advanced")
    ok(inp["inputs_hash"] and "transcript" in inp["fingerprints"], "inputs.json fingerprints")


@test
def t2_slide_added_and_t3_video_deleted():
    proj = mk_project("proj2")
    stub_note(proj, "basic", "01", "getting-started")
    run("ledger.py", "adopt", "--project", proj, "--data-dir", DATA)
    shutil.copy2(RES / "L8.pdf", proj / "resources" / "L1.pdf")           # a per-session slide for session 1
    pl, _ = plan(proj)
    r1 = row(pl, "01")
    eq(r1["status"], "changed", r1)
    ok(any("slides: L1.pdf added" in x for x in r1["reasons"]), r1["reasons"])
    eq(r1["action"], "revise")
    (proj / "resources" / "L1.pdf").unlink()
    eq(row(plan(proj)[0], "01")["status"], "up-to-date", "back to up-to-date")
    # a deleted (archived) recording never marks the session changed
    ok((proj / "resources" / "videos" / f"{S1}.mp4").exists(), "recording present")
    (proj / "resources" / "videos" / f"{S1}.mp4").unlink()
    r1 = row(plan(proj)[0], "01")
    eq(r1["status"], "up-to-date", r1)
    # editing the transcript does trigger
    t = proj / "resources" / f"{S1}.txt"
    t.write_text(t.read_text(encoding="utf-8") + "\nOne more sentence.\n", encoding="utf-8")
    r1 = row(plan(proj)[0], "01")
    eq(r1["status"], "changed", r1)
    ok(any(x.startswith("transcript:") and "modified" in x for x in r1["reasons"]), r1["reasons"])


@test
def t4_new_lecture_appended_with_order():
    proj = mk_project("proj4")
    stub_note(proj, "basic", "01", "getting-started")
    run("ledger.py", "adopt", "--project", proj, "--data-dir", DATA)
    shutil.copy2(RES / f"{S4}.txt", proj / "resources" / f"{S4}.txt")      # an unknown lecture, header date 2026-10-04
    pl, r = plan(proj)
    prof_txt = (proj / INTERNAL / "profile.md").read_text(encoding="utf-8")
    ok("# the course table (a comment that must survive appends)" in prof_txt, "comment kept")
    ok(re.search(r'- \{n: 4, title: "Fast Orderbooks", stem: "' + re.escape(S4) + r'", date: 2026-10-04, order: 2\.5\}', prof_txt), prof_txt)
    ok(prof_txt.count("n: 1,") == 1 and prof_txt.count("n: 3,") == 1, "existing rows untouched")
    r4 = row(pl, "04")
    eq(r4["status"], "new", r4)
    eq(r4["order"], 2.5, r4)
    eq(pl["order"], ["01", "02", "04", "03"], "course order puts 04 between 02 and 03")
    eq(len(pl["derived_added"]), 1, "derived_added")
    before = prof_txt
    plan(proj)
    eq((proj / INTERNAL / "profile.md").read_text(encoding="utf-8"), before, "second run appends nothing")


@test
def t5_duplicate_lint():
    proj = mk_project("proj5", sessions_files=False)
    a = stub_note(proj, "basic", "02", "first-slug")
    b = stub_note(proj, "basic", "02", "second-slug")
    r = run("lint_note.py", a, "--project", proj, "--tier", "basic", "--data-dir", DATA)
    eq(r.returncode, 1, r.stdout + r.stderr)
    ok("dup-session" in r.stdout, r.stdout)
    b.unlink()
    r = run("lint_note.py", a, "--project", proj, "--tier", "basic", "--data-dir", DATA)
    ok("dup-session" not in r.stdout, r.stdout)
    # the build lists a duplicated session once
    b = stub_note(proj, "basic", "02", "second-slug")
    r = run("build_pdf.py", "--project", proj, "--tier", "basic", "--html-only", "--data-dir", DATA)
    ok("duplicate note number 02" in r.stdout, r.stdout)
    eq(len(list((proj / INTERNAL / "work" / "build" / "basic").glob("CPP-02-*.html"))), 1, "one html for 02")


@test
def t6_prune():
    proj = mk_project("proj6", sessions_files=False)
    src = FIX / "render" / "basic" / "src"
    nb = proj / "notes" / "basic"
    nb.mkdir(parents=True)
    for f in src.glob("*.md"):
        shutil.copy2(f, nb / f.name)
    pdf = proj / "pdf" / "basic"
    pdf.mkdir(parents=True)
    cur = "CPP-02-the-stack-frame-basic.pdf"
    for n in (cur, "CPP-02-old-slug-basic.pdf", "keep-me.pdf", "CPP-05-gone-basic.pdf", "CPP-Compendium-basic.pdf"):
        (pdf / n).write_bytes(b"%PDF-1.4 dummy")
    (proj / "pdf" / "advanced").mkdir(parents=True)
    (proj / "pdf" / "advanced" / "CPP-02-old-slug-advanced.pdf").write_bytes(b"%PDF-1.4 dummy")
    figs = nb / "figures"
    figs.mkdir()
    for n in ("02-orphan.png", "03-other-session.png", "shared.png"):
        (figs / n).write_bytes(b"png")
    # scoped to note 02: only 02's stale PDF and 02's unreferenced figure go
    r = run("build_pdf.py", "--project", proj, "--tier", "basic", "--html-only", "--prune", "--notes", "02", "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    left = sorted(p.name for p in pdf.glob("*.pdf"))
    eq(left, sorted([cur, "keep-me.pdf", "CPP-05-gone-basic.pdf", "CPP-Compendium-basic.pdf"]), "scoped prune")
    ok(not (figs / "02-orphan.png").exists() and (figs / "03-other-session.png").exists(), "figures scoped")
    # full run: the removed note's PDF goes too; other tier, non-pattern files, index/compendium stay
    (pdf / "CPP-02-old-slug-basic.pdf").write_bytes(b"x")
    r = run("build_pdf.py", "--project", proj, "--tier", "basic", "--html-only", "--prune", "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    eq(sorted(p.name for p in pdf.glob("*.pdf")), sorted([cur, "keep-me.pdf", "CPP-Compendium-basic.pdf"]), "full prune")
    ok((figs / "shared.png").exists() and (figs / "03-other-session.png").exists(), "no-note session figure and shared kept")
    ok((proj / "pdf" / "advanced" / "CPP-02-old-slug-advanced.pdf").exists(), "other tier untouched")
    # without --prune nothing is deleted
    (pdf / "CPP-02-old-slug-basic.pdf").write_bytes(b"x")
    run("build_pdf.py", "--project", proj, "--tier", "basic", "--html-only", "--data-dir", DATA)
    ok((pdf / "CPP-02-old-slug-basic.pdf").exists(), "no prune by default in build_pdf")


def legacy_project(name: str) -> Path:
    proj = mk_project(name, sessions_files=False)
    (proj / INTERNAL).rename(proj / ".notes")
    (proj / ".notes" / "work" / "sessions" / "01").mkdir(parents=True)
    (proj / ".notes" / "work" / "sessions" / "01" / "digest.md").write_text("digest", encoding="utf-8")
    (proj / ".gitignore").write_text("resources/\n.notes/work/\n", encoding="utf-8")
    return proj


@test
def t7_migration():
    proj = legacy_project("mig-resolve")
    r = run("resolve_inputs.py", "--project", proj, "--session", "1", "--allow-no-speech", "--dry-run", "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok("migrated .notes/ -> .notes-plugin-internal/" in r.stdout, r.stdout)
    ok(not (proj / ".notes").exists() and (proj / INTERNAL / "profile.md").is_file(), "renamed")
    ok((proj / INTERNAL / "work" / "sessions" / "01" / "digest.md").is_file(), "work kept")
    ok((proj / INTERNAL / "README.md").read_text(encoding="utf-8").startswith("Managed by the notes plugin."), "README")
    eq((proj / ".gitignore").read_text(encoding="utf-8").splitlines(), ["resources/", ".notes-plugin-internal/work/"], ".gitignore")
    r = run("resolve_inputs.py", "--project", proj, "--session", "1", "--allow-no-speech", "--dry-run", "--data-dir", DATA)
    ok("migrated" not in r.stdout, "idempotent")
    # doctor, plan_updates and init_project migrate too
    for script, extra in (("doctor.py", ["--no-network"]), ("plan_updates.py", []), ("init_project.py", ["--profile", str(proj / INTERNAL / "profile.md")])):
        p = legacy_project("mig-" + script.split(".")[0])
        args = ["--project", p, "--data-dir", DATA, *extra]
        if script == "init_project.py":
            args = ["--project", p, "--data-dir", DATA, "--profile", str(PLUGIN / "profiles" / "cpp.md")]
        r = run(script, *args)
        ok(not (p / ".notes").exists() and (p / INTERNAL / "profile.md").is_file(), f"{script} migrated: {r.stdout[-300:]}{r.stderr[-300:]}")
    # both directories present -> refuse
    p = legacy_project("mig-both")
    (p / INTERNAL).mkdir()
    r = run("resolve_inputs.py", "--project", p, "--session", "1", "--allow-no-speech", "--dry-run", "--data-dir", DATA)
    ok(r.returncode == 2 and "both" in r.stderr, r.stdout + r.stderr)
    # a copy of the real cpp project's legacy folder (profile only; the live tree is never touched)
    real = CPP_ROOT / ".notes" / "profile.md"
    if real.is_file():
        p = WORK / "mig-real"
        rmtree(p)
        (p / ".notes").mkdir(parents=True)
        shutil.copy2(real, p / ".notes" / "profile.md")
        (p / "resources").mkdir()
        (p / ".gitignore").write_text("resources/\n.notes/work/\n", encoding="utf-8")
        r = run("plan_updates.py", "--project", p, "--data-dir", DATA)
        eq(r.returncode, 0, r.stdout + r.stderr)
        ok((p / INTERNAL / "profile.md").is_file() and not (p / ".notes").exists(), "real profile copy migrated")


@test
def t8_course_order():
    proj = mk_project("order", sessions_files=False)
    prof = (proj / INTERNAL / "profile.md")
    txt = PROFILE.split("sessions:")[0] + (
        "sessions:\n"
        '  - {n: 12, title: "Low latency hacks", date: 2026-10-01}\n'
        '  - {n: 13, title: "Capstone Project", date: 2026-10-05}\n'
        '  - {n: 14, title: "Fast Orderbooks", date: 2026-10-04, order: 12.5}\n'
        "---\n\n# x\n")
    prof.write_text(txt, encoding="utf-8")
    sys.path.insert(0, str(SCRIPTS))
    from notes_lib import profile as P  # noqa: E402
    om = P.session_orders({"sessions": [{"n": 12, "date": "2026-10-01"}, {"n": 13, "date": "2026-10-05"},
                                        {"n": 14, "date": "2026-10-04", "order": 12.5}]})
    eq(sorted(om, key=lambda k: om[k]), [12, 14, 13], "session_orders")
    # no explicit order: dates decide among the dated sessions, undated keep their slot
    om2 = P.session_orders({"sessions": [{"n": 1, "date": "2026-01-02"}, {"n": 2}, {"n": 3, "date": "2026-01-01"}]})
    eq(sorted(om2, key=lambda k: om2[k]), [3, 2, 1], "date refinement")
    src = FIX / "render" / "basic" / "src"
    nb = proj / "notes" / "basic"
    nb.mkdir(parents=True)
    for nn, slug in (("12", "low-latency"), ("13", "capstone"), ("14", "fast-orderbooks")):
        shutil.copy2(src / "01-ticks-and-the-order-book.md", nb / f"{nn}-{slug}.md")
        t = (nb / f"{nn}-{slug}.md").read_text(encoding="utf-8")
        (nb / f"{nn}-{slug}.md").write_text(re.sub(r"^# .*$", f"# {nn} · Note {nn}", t, count=1, flags=re.M), encoding="utf-8")
    # index: course order
    r = run("update_index.py", "--project", proj, "--tier", "basic", "--legacy", "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    readme = (nb / "README.md").read_text(encoding="utf-8")
    pos = [readme.index(f"| {n} |") for n in (12, 14, 13)]
    ok(pos == sorted(pos), f"session table order {pos}")
    notes_tbl = readme.split("## The notes")[1]
    pos = [notes_tbl.index(f"| {n} |") for n in (12, 14, 13)]
    ok(pos == sorted(pos), f"notes table order {pos}")
    # compendium + prev/next
    r = run("build_pdf.py", "--project", proj, "--tier", "basic", "--html-only", "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    bd = proj / INTERNAL / "work" / "build" / "basic"
    comp = (bd / "CPP-Compendium-basic.html").read_text(encoding="utf-8")
    ids = [comp.index(f'id="ch{n}"') for n in (12, 14, 13)]
    ok(ids == sorted(ids), f"compendium chapter order {ids}")
    n13 = (bd / "CPP-13-capstone-basic.html").read_text(encoding="utf-8")
    ok("CPP-14-fast-orderbooks-basic.pdf" in n13 and "Previous" in n13, "13's previous is 14")
    n14 = (bd / "CPP-14-fast-orderbooks-basic.html").read_text(encoding="utf-8")
    ok("CPP-13-capstone-basic.pdf" in n14 and "CPP-12-low-latency-basic.pdf" in n14, "14: previous 12, next 13")
    # prior notes in course order
    def prior(n):
        js = proj / f"in{n}.json"
        r = run("resolve_inputs.py", "--project", proj, "--session", str(n), "--allow-no-speech", "--dry-run",
                "--json", js, "--data-dir", DATA)
        eq(r.returncode, 0, r.stdout + r.stderr)
        d = json.loads(js.read_text(encoding="utf-8"))
        return [Path(x).name[:2] for x in d["prior_notes"]["project"]["basic"]], d
    eq(prior(12)[0], [], "12 has no prior notes")
    eq(prior(14)[0], ["12"], "14 comes after 12 only")
    p13, d13 = prior(13)
    eq(p13, ["12", "14"], "13 comes after 12 and 14 (14 < 13 in course order)")
    eq(d13["prior_sessions"], ["12", "14"], "prior_sessions")


@test
def t9_ledger_cli():
    proj = mk_project("proj9")
    n1 = stub_note(proj, "basic", "01", "old-slug")
    (proj / "notes" / "basic" / "figures").mkdir()
    (proj / "notes" / "basic" / "figures" / "01-old-slug-fig.png").write_bytes(b"png")
    n1.write_text(n1.read_text(encoding="utf-8") + "\n![f](figures/01-old-slug-fig.png)\n", encoding="utf-8")
    (proj / "pdf" / "basic").mkdir(parents=True)
    (proj / "pdf" / "basic" / "CPP-01-old-slug-basic.pdf").write_bytes(b"%PDF")
    qa = proj / "qa.json"
    qa.write_text(json.dumps({"ok": False}), encoding="utf-8")
    r = run("ledger.py", "record", "--project", proj, "--session", "1", "--tier", "basic", "--status", "ok",
            "--qa-json", qa, "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    st = json.loads((proj / INTERNAL / "state.json").read_text(encoding="utf-8"))
    eq(st["sessions"]["01"]["tiers"]["basic"]["status"], "partial", "ok needs qa ok")
    pl, _ = plan(proj)
    eq(row(pl, "01")["status"], "incomplete", row(pl, "01"))
    eq(row(pl, "01")["action"], "revise")
    qa.write_text(json.dumps({"ok": True}), encoding="utf-8")
    run("ledger.py", "record", "--project", proj, "--session", "1", "--tier", "basic", "--status", "ok",
        "--qa-json", qa, "--mode", "revise", "--data-dir", DATA)
    eq(row(plan(proj)[0], "01")["status"], "up-to-date")
    # rename-slug
    r = run("ledger.py", "rename-slug", "--project", proj, "--session", "1", "--slug", "new-slug", "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    nb = proj / "notes" / "basic"
    ok((nb / "01-new-slug.md").is_file() and not (nb / "01-old-slug.md").exists(), "md renamed")
    ok((nb / "figures" / "01-new-slug-fig.png").is_file(), "figure renamed")
    ok("figures/01-new-slug-fig.png" in (nb / "01-new-slug.md").read_text(encoding="utf-8"), "reference rewritten")
    ok((proj / "pdf" / "basic" / "CPP-01-new-slug-basic.pdf").is_file(), "pdf renamed")
    st = json.loads((proj / INTERNAL / "state.json").read_text(encoding="utf-8"))
    eq(st["sessions"]["01"]["slug"], "new-slug")
    eq(st["sessions"]["01"]["tiers"]["basic"]["note"], "notes/basic/01-new-slug.md")
    eq(row(plan(proj)[0], "01")["status"], "up-to-date", "still up to date after rename")
    # clean keeps the digest and the manifest, drops html / chrome profile / code / pages
    bd = proj / INTERNAL / "work" / "build" / "basic"
    (bd / "chrome-profile").mkdir(parents=True)
    (bd / "x.html").write_text("<html>", encoding="utf-8")
    (bd / "manifest.json").write_text("{}", encoding="utf-8")
    sd = proj / INTERNAL / "work" / "sessions" / "01"
    (sd / "code").mkdir(parents=True)
    (sd / "code" / "a.cpp").write_text("x", encoding="utf-8")
    (sd / "slides" / "deck" / "pages").mkdir(parents=True)
    (sd / "slides" / "deck" / "pages" / "p1.png").write_bytes(b"png")
    (sd / "digest.md").write_text("d", encoding="utf-8")
    r = run("ledger.py", "clean", "--project", proj, "--session", "1", "--data-dir", DATA)
    eq(r.returncode, 0, r.stdout + r.stderr)
    ok(not (bd / "chrome-profile").exists() and not (bd / "x.html").exists() and (bd / "manifest.json").is_file(), "build cleaned")
    ok(not (sd / "code").exists() and not (sd / "slides" / "deck" / "pages").exists() and (sd / "digest.md").is_file(), "session cleaned")
    # work/ wiped entirely: the ledger survives and nothing is "changed" (frames/whisper live in resources here)
    shutil.rmtree(proj / INTERNAL / "work")
    eq(row(plan(proj)[0], "01")["status"], "up-to-date", "ledger survives wiping work/")


def make_synthetic_res() -> Path:
    """Small synthetic stand-ins for the real class files (transcripts with Scaler-style headers, a whisper JSON, a PDF)."""
    d = WORK / "_synth_res"
    rmtree(d)
    d.mkdir(parents=True)
    body = " ".join(["the instructor explains the idea and the cache line again."] * 40)
    for stem, title, start in ((S1, "Getting started with C++", "8/19/2026, 10:00:00 AM"),
                               (S2, "Pointers and References", "8/22/2026, 10:00:00 AM"),
                               (S3, "Capstone Project", "10/5/2026, 10:00:00 AM"),
                               (S4, "Fast Orderbooks", "10/4/2026, 10:00:00 AM")):
        (d / f"{stem}.txt").write_text(
            f"Course Name: Test Course\nLecture Title: {title}\nStart Time: {start}\nDuration: 120 minutes\n"
            f"Model use: test\n\n==================================================\n\n{body}\n",
            encoding="utf-8", newline="\n")
    (d / "Pointers_and_References_2_-_Class_Scaler_Academy.whisper.json").write_text('{"segments": []}', encoding="utf-8", newline="\n")
    (d / "L8.pdf").write_bytes(b"%PDF-1.4\n% synthetic slide\n")
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    a = ap.parse_args()
    global RES
    if not RES.is_dir():                       # no real subject project: use short synthetic inputs instead
        RES = make_synthetic_res()
    WORK.mkdir(parents=True, exist_ok=True)
    bad = []
    for fn in TESTS:
        if a.k and a.k not in fn.__name__:
            continue
        try:
            fn()
            print(f"  [PASS] {fn.__name__}", flush=True)
        except Exception:
            traceback.print_exc()
            print(f"  [FAIL] {fn.__name__}", flush=True)
            bad.append(fn.__name__)
    print(f"\n{len(TESTS) - len(bad)}/{len(TESTS)} passed" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
