# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pillow", "pymupdf"]
# ///
"""Self-test for the figures + QA toolkit on tiny fixed-layout projects.

    uv run tests/selftest_qa.py [-k SUBSTRING] [--skip-build]

Work dir: <plugin>/test-output/_r1/qa/ (recreated). Legacy checks copy the cpp notes 01-03 from the
read-only legacy repo into a project's notes/basic (skipped if the repo is absent). Full qa.py build/audit runs only
if scripts/build_pdf.py and scripts/audit_pdf.py exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import traceback
import zlib
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
# optional read-only real subject projects for extra regression tests; every test that needs one skips when absent
SUBJ_CPP = os.environ.get("NOTES_TEST_CPP_ROOT", "/nonexistent/cpp-subject")
SUBJ_FDE = os.environ.get("NOTES_TEST_FDE_ROOT", "/nonexistent/fde-subject")
SUBJ_CV = os.environ.get("NOTES_TEST_CV_ROOT", "/nonexistent/cv-subject")
SCRIPTS = PLUGIN / "scripts"
FIX = PLUGIN / "tests" / "fixtures" / "qa"
WORK = PLUGIN / "test-output" / "_r1" / "qa"
CPP_NOTES = Path(SUBJ_CPP + "/notes")
sys.path.insert(0, str(SCRIPTS))
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
PROFILE = FIX / "profile-qa.md"
RESULTS: list[tuple[str, bool, str]] = []


def run(script: str, *args, timeout=900):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([UV, "run", "--quiet", "--script", str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout)


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def png(p: Path):
    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                  + ch(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + ch(b"IEND", b""))


def tier_copy(name: str, dest: str, profile: Path = PROFILE) -> Path:
    """Fixture tier dir (<tier>/src, <tier>/meta) -> a tiny project: notes/<tier>/, .notes-plugin-internal/work/meta/<tier>/."""
    proj = WORK / dest
    (proj / ".notes-plugin-internal").mkdir(parents=True)
    shutil.copy2(profile, proj / ".notes-plugin-internal" / "profile.md")
    for tier in ("basic", "advanced"):
        if (FIX / name / tier / "src").is_dir():
            shutil.copytree(FIX / name / tier / "src", proj / "notes" / tier)
            if (FIX / name / tier / "meta").is_dir():
                shutil.copytree(FIX / name / tier / "meta", proj / ".notes-plugin-internal" / "work" / "meta" / tier)
    return proj


TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def figures_deterministic():
    ex = SCRIPTS / "figures" / "example.figures.py"
    a, b = WORK / "figA", WORK / "figB"
    r1 = run("make_figures.py", "--script", ex, "--out", a, "--audit", "--json", WORK / "f1.json")
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert "no overlapping labels" in r1.stdout
    r2 = run("make_figures.py", "--script", ex, "--out", b, "--json", WORK / "f2.json")
    assert r2.returncode == 0
    names = sorted(p.name for p in a.glob("*.png"))
    assert names == ["02-latency-ladder.png", "02-stack-frame.png"], names
    for n in names:
        assert sha(a / n) == sha(b / n), f"{n} not byte-deterministic"
    assert run("make_figures.py", "--script", ex, "--out", a, "--list").stdout.split() == \
        ["02-stack-frame", "02-latency-ladder"]
    assert run("make_figures.py", "--script", ex, "--out", a, "nope").returncode == 2
    assert len(jload(WORK / "f1.json")["built"]) == 2
    proj = tier_copy("tier-adv", "figproj")
    r3 = run("make_figures.py", "--script", ex, "--project", proj, "--tier", "advanced")
    assert r3.returncode == 0 and (proj / "notes" / "advanced" / "figures" / "02-stack-frame.png").is_file(), r3.stdout + r3.stderr
    assert run("make_figures.py", "--script", ex).returncode == 2


@test
def verify_numbers():
    good = run("verify_numbers.py", FIX / "meta-good", "--json", WORK / "vg.json")
    assert good.returncode == 0 and "ALL CHECKS PASSED" in good.stdout, good.stdout
    bad = run("verify_numbers.py", FIX / "meta-bad" / "02-demo.checks.py", "--json", WORK / "vb.json")
    assert bad.returncode == 1
    for lab in ("add-result-WRONG", "third-WRONG", "ram-latency-WRONG"):
        assert lab in bad.stdout, lab
    assert "add-result-fine:" not in bad.stdout.split("RESULT")[-1]
    proj = tier_copy("tier-basic-good", "vnproj")
    pv = run("verify_numbers.py", "--project", proj, "--tier", "basic")
    assert pv.returncode == 0 and "ALL CHECKS PASSED" in pv.stdout, pv.stdout
    ex = run("verify_numbers.py", FIX / "meta-bad" / "03-raises.checks.py")
    assert ex.returncode == 1 and "<exception>" in ex.stdout


@test
def snippets():
    sn = FIX / "snippets"
    w = WORK / "snip"
    ok = run("check_snippets.py", sn / "snip-ok.md", "--profile", PROFILE, "--work", w, "--json", WORK / "s1.json")
    assert ok.returncode == 0 and jload(WORK / "s1.json")["counts"] == {"pass": 2, "fail": 0, "skipped": 0}, ok.stdout
    wr = run("check_snippets.py", sn / "snip-wrong.md", "--profile", PROFILE, "--work", w)
    assert wr.returncode == 1 and "output mismatch" in wr.stdout
    md = run("check_snippets.py", sn / "snip-modes.md", "--profile", PROFILE, "--work", w)
    assert md.returncode == 0 and "2 pass" in md.stdout
    ce = run("check_snippets.py", sn / "snip-compile-error.md", "--profile", PROFILE, "--work", w)
    assert ce.returncode == 1 and "compile error" in ce.stdout
    proj = tier_copy("tier-basic-good", "snproj")
    pn = run("check_snippets.py", proj / "notes" / "basic" / "02-stack-frames.md")        # profile + work dir from the project
    assert pn.returncode == 0 and (proj / ".notes-plugin-internal" / "work" / "build" / "basic" / "snippets").is_dir(), pn.stdout + pn.stderr
    nc = run("check_snippets.py", sn / "snip-ok.md", "--profile", FIX / "profile-nocc.md", "--work", w)
    assert nc.returncode == 0 and "2 skipped" in nc.stdout, nc.stdout


@test
def budget_thresholds():
    """PLAN 12.1: warn-only word budget ("too thin" under the minimum), minutes informational,
    range scaled by clamp(duration_min / 120, 0.5, 1.5), gap-fill still fails over the cap."""
    import note_stats as ns
    cfg = {"words": [1000, 2000], "minutes": [20, 35], "gapfill_max_share": 0.3}
    st = lambda w, m, g=0: {"prose_words": w, "minutes": m, "gapfill_words": g}  # noqa: E731
    assert ns.budget(st(1500, 30), cfg, "basic")["status"] == "pass"
    b = ns.budget(st(900, 30), cfg, "basic")
    assert b["words"]["status"] == "warn" and b["words"]["too_thin"], b
    b = ns.budget(st(5000, 30), cfg, "basic")
    assert b["words"]["status"] == "warn" and b["words"]["over"] and b["status"] == "warn", b   # never fail
    assert ns.budget(st(1500, 400), cfg, "basic")["minutes"]["status"] == "pass"                   # informational
    assert ns.budget(st(1000, 30, 350), cfg, "basic")["gapfill"]["status"] == "fail"
    assert ns.budget(st(1000, 30, 300), cfg, "basic")["gapfill"]["status"] == "pass"
    assert ns.budget(st(9000, 30), cfg, "advanced")["status"] == "pass"
    assert ns.budget(st(500, 30), cfg, "advanced")["words"]["status"] == "warn"
    # duration scaling: a 60 min class halves the range, a 240 min class is clamped to x1.5
    b = ns.budget(st(600, 30), cfg, "basic", 60)
    assert b["words"]["range"] == [500, 1000] and b["words"]["status"] == "pass", b
    b = ns.budget(st(1400, 30), cfg, "basic", 240)
    assert b["words"]["range"] == [1500, 3000] and b["words"]["too_thin"], b
    assert ns.budget(st(1500, 30), cfg, "basic", None)["words"]["scale"] == 1.0


@test
def visuals_and_coverage():
    """PLAN 12.1/12.2: V-rows need a figure (visuals.json + embedded), jpg figures are legal, coverage share,
    class-figure caption warning, thin coverage."""
    prof = WORK / "profile-vis.md"
    prof.write_text(PROFILE.read_text(encoding="utf-8").replace("figures: none", "figures: class+generated"),
                    encoding="utf-8")
    dg_txt = (FIX / "digest/digest-good.md").read_text(encoding="utf-8")
    dg = WORK / "digest-vis.md"
    dg.write_text(dg_txt.replace("| V01 | F002 (00:25:10) | C01 | editor with `main.cpp` and the add function | clean | 0.00,0.00,1.00,1.00 | text |",
                                 "| V01 | F005 (01:05:10) | C01 | board: frame of add | clean | 0.1,0.1,0.9,0.9 | embed |\n"
                                 "| V02 | F002 (00:25:10) | C01 | editor | clean | 0.0,0.0,1.0,1.0 | text |"), encoding="utf-8")
    assert run("check_digest.py", dg).returncode == 0, run("check_digest.py", dg).stdout
    bad = WORK / "digest-badcrop.md"
    bad.write_text(dg.read_text(encoding="utf-8").replace("0.1,0.1,0.9,0.9", "0.9,0.1,0.1,0.9"), encoding="utf-8")
    r = run("check_digest.py", bad)
    assert r.returncode == 1 and "visual-crop" in r.stdout, r.stdout
    p = tier_copy("tier-basic-good", "vis", profile=prof)
    note = p / "notes/basic/02-stack-frames.md"
    meta = p / ".notes-plugin-internal/work/meta/basic"

    def lint():
        out = WORK / "vis-lint.json"
        r = run("lint_note.py", note, "--project", p, "--tier", "basic", "--digest", dg, "--json", out)
        j = jload(out)["notes"][0]
        return r, {i["code"] for i in j["errors"]}, {i["code"] for i in j["warnings"]}, j
    r, errs, warns, _ = lint()
    assert "visuals-missing" in errs, errs
    (meta / "02-stack-frames.visuals.json").write_text('{"V01": {"files": ["02-class-frame.jpg"], "treatment": "embed"}, '
                                                      '"V02": {"files": [], "treatment": "text"}}', encoding="utf-8")
    r, errs, warns, _ = lint()
    assert "visual-unreferenced" in errs, errs
    fig = p / "notes/basic/figures"
    fig.mkdir(parents=True, exist_ok=True)
    (fig / "02-class-frame.jpg").write_bytes(bytes([0xff, 0xd8, 0x78, 0xff, 0xd9]))
    txt = note.read_text(encoding="utf-8")
    note.write_text(txt.replace("## Cheat sheet", "![the frame](figures/02-class-frame.jpg \"**Frame.** no time\")\n\n## Cheat sheet"),
                    encoding="utf-8")
    r, errs, warns, _ = lint()
    assert not ({"visual-missing", "visual-unreferenced", "visuals-missing", "image-format"} & errs), errs
    assert "class-caption" in warns, warns
    note.write_text(txt.replace("## Cheat sheet", "![the frame](figures/02-class-frame.jpg \"**From class, 01:05:10.** the frame\")\n\n## Cheat sheet"),
                    encoding="utf-8")
    r, errs, warns, _ = lint()
    assert "class-caption" not in warns and r.returncode == 0, (errs, warns, r.stdout)
    # a real JPEG goes through the whole PDF build + audit (images_expected counts jpg figures too)
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (900, 500), "white")
    ImageDraw.Draw(im).line([(40, 40), (860, 460)], fill="black", width=5)
    im.save(fig / "02-class-frame.jpg", quality=85)
    full = WORK / "vis-qa.json"
    r = run("qa.py", "--project", p, "--tier", "basic", "--digest", dg, "--json", full, timeout=1800)
    j = jload(full)
    assert j["build"]["ran"] and j["audit"]["ran"] and j["audit"]["problems"] == 0, (j["build"], j["audit"], r.stdout)
    assert j["ok"], r.stdout
    # V-row with an empty files list -> visual-missing
    (meta / "02-stack-frames.visuals.json").write_text('{"V01": {"files": [], "treatment": "embed"}}', encoding="utf-8")
    assert "visual-missing" in lint()[1]
    # coverage share: C02 without an example drops basic below 95%
    (meta / "02-stack-frames.visuals.json").write_text('{"V01": {"files": ["02-class-frame.jpg"], "treatment": "embed"}}', encoding="utf-8")
    cov = meta / "02-stack-frames.coverage.json"
    good = cov.read_text(encoding="utf-8")
    cov.write_text(good.replace('"C02": {"sections": ["The frame of add"], "worked_examples": 1}',
                                '"C02": {"sections": ["The frame of add"], "worked_examples": 0}'), encoding="utf-8")
    r, errs, warns, _ = lint()
    assert "coverage-share" in errs and "coverage-examples" in warns, (errs, warns)
    # thin coverage: C02 mapped to a one-line section
    cov.write_text(good, encoding="utf-8")
    t2 = note.read_text(encoding="utf-8")
    note.write_text(t2.replace("## Cheat sheet", "## Stale stack\n\nIt is UB.\n\n## Cheat sheet"), encoding="utf-8")
    cov.write_text(good.replace('"C02": {"sections": ["The frame of add"]', '"C02": {"sections": ["Stale stack"]'), encoding="utf-8")
    assert "thin-coverage" in lint()[2]


def legacy_dir() -> Path | None:
    d = WORK / "legacy"
    if not d.exists():
        if not CPP_NOTES.is_dir():
            return None
        (d / ".notes-plugin-internal").mkdir(parents=True)
        shutil.copy2(PLUGIN / "tests" / "fixtures" / "ingest" / "profile-cpp-min.md", d / ".notes-plugin-internal" / "profile.md")
        (d / "notes" / "basic").mkdir(parents=True)
        for n in ("01", "02", "03"):
            for f in CPP_NOTES.glob(f"{n}-*.md"):
                shutil.copy(f, d / "notes" / "basic" / f.name)
    return d


@test
def legacy_stats_and_index():
    d = legacy_dir()
    if d is None:
        print("    (cpp repo absent: skipped)")
        return
    nd = d / "notes" / "basic"
    n2 = next(nd.glob("02-*.md"), None)
    if n2 is None:
        print("    (the hand-written legacy notes 01-03 are gone from the cpp repo: skipped)")
        return
    r = run("note_stats.py", n2, "--project", d, "--tier", "basic", "--json", WORK / "st.json")
    st = jload(WORK / "st.json")["notes"][0]["stats"]
    assert abs(st["prose_words"] - 1537) / 1537 < 0.05, st
    assert abs(st["code_lines"] - 124) <= 3, st
    assert 29 <= st["minutes"] <= 33, st
    r = run("update_index.py", "--project", d, "--tier", "basic", "--legacy", "--json", WORK / "ix.json")
    assert r.returncode == 0, r.stderr
    readme = (nd / "README.md").read_bytes()
    assert b"\xc2\xa7" not in readme
    rows = [c for n in jload(WORK / "ix.json")["notes"] if n["num"] == "02" for c in n["corrections"]]
    assert len(rows) == 7, len(rows)
    assert sorted(c["sev"] for c in rows) == sorted("🔴🟠🟠🟠🟠🟡🟡"), [c["sev"] for c in rows]
    run("update_index.py", "--project", d, "--tier", "basic", "--legacy")
    assert (nd / "README.md").read_bytes() == readme, "index not deterministic"
    lint = run("lint_note.py", *sorted(nd.glob("0*.md")), "--project", d, "--tier", "basic", "--legacy")
    assert lint.returncode == 0, lint.stdout


@test
def lint_flags_seeded_errors():
    t = tier_copy("tier-basic-bad", "lint-bad")
    r = run("lint_note.py", t / "notes/basic/02-bad-note.md", t / "notes/basic/03-bad-h1.md", "--profile", PROFILE,
            "--tier", "basic", "--digest", FIX / "digest/digest-good.md", "--json", WORK / "lint.json")
    assert r.returncode == 1
    codes = {i["code"] for n in jload(WORK / "lint.json")["notes"] for i in n["errors"]}
    want = {"fm-missing", "section-sign", "missing-section", "placeholder", "figures-disallowed", "image-missing",
            "image-external", "image-format", "run-no-output", "code-wide", "mermaid-disallowed", "table-wide",
            "gapfill-share", "coverage-missing", "h1-count", "h1-format"}
    assert want <= codes, want - codes
    wcodes = {i["code"] for n in jload(WORK / "lint.json")["notes"] for i in n["warnings"]}
    assert {"heading-number", "dead-link", "html-disallowed", "code-wide-warn", "going-deeper-long",
            "check-count"} <= wcodes, wcodes
    g = tier_copy("tier-basic-good", "lint-good")
    r = run("lint_note.py", g / "notes/basic/02-stack-frames.md", "--profile", PROFILE, "--tier", "basic",
            "--digest", FIX / "digest/digest-good.md")
    assert r.returncode == 0 and "0 warning" in r.stdout, r.stdout


@test
def screen_notes():
    fj = FIX / "screen-notes" / "frames.json"
    assert run("check_screen_notes.py", FIX / "screen-notes", "--frames-json", fj).returncode == 0
    d = WORK / "sn-bad"
    shutil.copytree(FIX / "screen-notes", d)
    p = d / "shard-1.md"
    txt = p.read_text(encoding="utf-8")
    a, b = txt.index("## F002"), txt.index("## F003")
    p.write_text(txt[:a] + txt[b:], encoding="utf-8")
    r = run("check_screen_notes.py", d, "--frames-json", fj)
    assert r.returncode == 1 and "F002" in r.stdout, r.stdout


@test
def digest():
    g = FIX / "digest" / "digest-good.md"
    assert run("check_digest.py", g).returncode == 0
    txt = g.read_text(encoding="utf-8")
    lines = txt.split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("## 9."))
    j = next(k for k, ln in enumerate(lines) if ln.startswith("## 10."))
    p1 = WORK / "dg-nosec.md"
    p1.write_text("\n".join(lines[:i] + lines[j:]), encoding="utf-8")
    r = run("check_digest.py", p1)
    assert r.returncode == 1 and "missing-section" in r.stdout
    p2 = WORK / "dg-gap.md"
    p2.write_text("\n".join(ln for ln in lines if not ln.startswith("| 00:45")), encoding="utf-8")
    r = run("check_digest.py", p2)
    assert r.returncode == 1 and "timeline-gap" in r.stdout and "15 min" in r.stdout, r.stdout


@test
def citations():
    a = tier_copy("tier-adv", "cit")
    for n in ("02-stack-frame", "02-latency-ladder"):
        png(a / "notes/advanced/figures" / f"{n}.png")
    note = a / "notes/advanced/02-adv-demo.md"
    res = FIX / "digest" / "research-good.md"
    assert run("check_citations.py", note, "--research", res, "--profile", PROFILE).returncode == 0
    thin = WORK / "research-thin.md"
    thin.write_text("\n".join(ln for ln in res.read_text(encoding="utf-8").split("\n")
                              if not any(k in ln for k in ("godbolt.org |", "agner.org/optimize", "gitlab.com/x86"))),
                    encoding="utf-8")
    r = run("check_citations.py", note, "--research", thin, "--profile", PROFILE)
    assert r.returncode == 1 and "unknown-urls" in r.stdout


@test
def qa_end_to_end():
    skip_build = "--skip-build" in sys.argv or not ((SCRIPTS / "build_pdf.py").is_file()
                                                    and (SCRIPTS / "audit_pdf.py").is_file())
    dg = FIX / "digest" / "digest-good.md"
    g = tier_copy("tier-basic-good", "qa-good")
    r = run("qa.py", "--project", g, "--tier", "basic", "--digest", dg, "--skip-build", "--json", WORK / "qa-good.json")
    j = jload(WORK / "qa-good.json")
    assert r.returncode == 0 and j["ok"], r.stdout
    assert j["notes"][0]["snippets"]["pass"] == 1 and j["notes"][0]["numbers"]["status"] == "pass"
    assert (g / "notes" / "basic" / "README.md").is_file(), "index written to notes/basic/README.md"
    assert (g / ".notes-plugin-internal" / "work" / "build" / "basic" / "qa").is_dir(), "qa work dir under .notes-plugin-internal/work/build/<tier>"
    # digest + report defaults come from the layout (.notes-plugin-internal/work/sessions/02/digest.md, .../reports/qa-basic-02.json)
    d2 = g / ".notes-plugin-internal" / "work" / "sessions" / "02"
    d2.mkdir(parents=True)
    shutil.copy2(dg, d2 / "digest.md")
    r = run("qa.py", "--project", g, "--tier", "basic", "--notes", "02", "--skip-build")
    rep = g / ".notes-plugin-internal" / "work" / "reports" / "qa-basic-02.json"
    assert r.returncode == 0 and rep.is_file(), r.stdout + r.stderr
    assert jload(rep)["notes"][0]["coverage"] is not None, "digest picked up from sessions/02"
    b = tier_copy("tier-basic-bad", "qa-bad")
    r = run("qa.py", "--project", b, "--tier", "basic", "--digest", dg, "--skip-build", "--json", WORK / "qa-bad.json")
    j = jload(WORK / "qa-bad.json")
    assert r.returncode == 1 and not j["ok"]
    assert {f["code"] for f in j["mechanical_failures"]} >= {"section-sign", "code-wide", "table-wide", "image-missing"}
    assert {f["code"] for f in j["content_failures"]} >= {"missing-section", "gapfill-share", "coverage-missing"}
    a = tier_copy("tier-adv", "qa-adv")
    for n in ("02-stack-frame", "02-latency-ladder"):
        png(a / "notes/advanced/figures" / f"{n}.png")
    r = run("qa.py", "--project", a, "--tier", "advanced", "--digest", dg,
            "--research", FIX / "digest/research-good.md", "--skip-build", "--json", WORK / "qa-adv.json")
    j = jload(WORK / "qa-adv.json")
    assert r.returncode == 0 and j["ok"], r.stdout
    assert j["notes"][0]["citations"]["status"] == "pass"
    assert run("qa.py", "--project", WORK / "no-such", "--tier", "basic").returncode == 2
    if skip_build:
        print("    (build_pdf/audit_pdf absent or --skip-build: PDF path not tested)")
        return
    f = tier_copy("tier-basic-good", "qa-full")
    r = run("qa.py", "--project", f, "--tier", "basic", "--digest", dg, "--json", WORK / "qa-full.json", timeout=1800)
    j = jload(WORK / "qa-full.json")
    assert j["build"]["ran"] and j["audit"]["ran"], j["build"]
    assert r.returncode == 0 and j["ok"], r.stdout + json.dumps(j["build"])[:600]
    assert j["build"]["compendium"] and len(j["build"]["pdfs"]) >= 2 and j["audit"]["problems"] == 0
    assert all("/pdf/basic/" in x for x in j["build"]["pdfs"]), j["build"]["pdfs"]


@test
def one_note_per_session():
    """A companion NNa-*.md next to NN-*.md, or `companion_of` in front matter, is a mechanical failure (hard rule:
    one note per session and tier). The letter never makes it a separate session."""
    dg = FIX / "digest" / "digest-good.md"
    g = tier_copy("tier-basic-good", "qa-split")
    nd = g / "notes" / "basic"
    src = (nd / "02-stack-frames.md").read_text(encoding="utf-8")
    (nd / "02a-stack-frames-more.md").write_text(src.replace("# 02 ·", "# 02a ·"), encoding="utf-8")
    r = run("qa.py", "--project", g, "--tier", "basic", "--notes", "02", "--digest", dg, "--skip-build", "--no-rollup",
            "--json", WORK / "qa-split.json")
    j = jload(WORK / "qa-split.json")
    codes = {f["code"] for f in j["mechanical_failures"]}
    assert r.returncode == 1 and not j["ok"] and {"dup-session", "split-note"} <= codes, (codes, r.stdout)
    # the companion on its own is also refused (it is never "the note for session 02")
    r = run("lint_note.py", nd / "02a-stack-frames-more.md", "--profile", PROFILE, "--tier", "basic",
            "--json", WORK / "lint-split.json")
    lc = {i["code"] for n in jload(WORK / "lint-split.json")["notes"] for i in n["errors"]}
    assert r.returncode == 1 and {"split-note", "dup-session"} <= lc, lc
    # the companion_of key alone (a single file) fails lint
    (nd / "02a-stack-frames-more.md").unlink()
    (nd / "02-stack-frames.md").write_text(src.replace("\nverified_as_of:", '\ncompanion_of: "02"\nverified_as_of:', 1),
                                           encoding="utf-8")
    r = run("lint_note.py", nd / "02-stack-frames.md", "--profile", PROFILE, "--tier", "basic",
            "--json", WORK / "lint-co.json")
    lc = {i["code"] for n in jload(WORK / "lint-co.json")["notes"] for i in n["errors"]}
    assert "split-note" in lc, (lc, r.stdout)
    # and the clean single note passes
    (nd / "02-stack-frames.md").write_text(src, encoding="utf-8")
    r = run("qa.py", "--project", g, "--tier", "basic", "--notes", "02", "--digest", dg, "--skip-build", "--no-rollup")
    assert r.returncode == 0, r.stdout


@test
def qa_per_note_scope():
    """--notes NN --no-rollup is scoped to the note: a broken or overflowing PDF of another session in the same
    pdf/<tier>/ folder, a poisoned shared manifest.json and a stray compendium never fail it (parallel /notes:update)."""
    if "--skip-build" in sys.argv:
        print("    (--skip-build: scope test needs the PDF build)")
        return
    import pymupdf
    dg = FIX / "digest" / "digest-good.md"
    f = tier_copy("tier-basic-good", "qa-scope")
    pdfd = f / "pdf" / "basic"
    pdfd.mkdir(parents=True)
    (pdfd / "FDE-03-other-session-basic.pdf").write_bytes(b"%PDF-1.4 half-written garbage")        # unreadable
    doc = pymupdf.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((560, 100), "text that runs far past the right margin", fontsize=11)         # overflow
    doc.save(pdfd / "FDE-04-overflowing-basic.pdf")
    doc.close()
    shutil.copy2(pdfd / "FDE-04-overflowing-basic.pdf", pdfd / "FDE-Compendium-basic.pdf")        # foreign compendium
    mdir = f / ".notes-plugin-internal" / "work" / "build" / "basic"
    mdir.mkdir(parents=True)
    (mdir / "manifest.json").write_text(json.dumps({"pdfs": [
        {"name": "FDE-04-overflowing-basic.pdf", "images_expected": 999, "kind": "note"}]}), encoding="utf-8")
    # the folder-wide audit does fail on that folder ...
    wide = run("audit_pdf.py", pdfd, "--manifest", mdir / "manifest.json")
    assert wide.returncode == 1 and "OVERFLOW" in wide.stdout and "IMAGES: " in wide.stdout, wide.stdout
    # ... but the per-note QA run does not look at it
    out = WORK / "qa-scope.json"
    r = run("qa.py", "--project", f, "--tier", "basic", "--notes", "02", "--no-rollup", "--digest", dg,
            "--json", out, timeout=1800)
    j = jload(out)
    assert r.returncode == 0 and j["ok"], r.stdout + json.dumps(j["audit"])[:800]
    assert j["build"]["ran"] and j["audit"]["ran"] and j["audit"]["problems"] == 0, (j["build"], j["audit"])
    assert len(j["build"]["pdfs"]) == 1 and "-02-" in j["build"]["pdfs"][0], j["build"]["pdfs"]
    assert j["index"].get("skipped") == "--no-rollup"
    # scratch files of a per-note run live in their own dir, so parallel `--notes NN` runs cannot overwrite each other
    assert (mdir / "qa" / "n02" / "build.json").is_file() and (mdir / "qa" / "n02" / "lint-0.json").is_file()
    assert not (mdir / "qa" / "build.json").exists() and not (mdir / "qa" / "lint-0.json").exists()
    # --only names an absent PDF -> a problem, not silence
    assert run("audit_pdf.py", pdfd, "--only", "FDE-09-nope-basic.pdf").returncode == 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ap.add_argument("--skip-build", action="store_true")
    a = ap.parse_args()
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    for fn in TESTS:
        if a.k and a.k not in fn.__name__:
            continue
        try:
            fn()
            RESULTS.append((fn.__name__, True, ""))
            print(f"PASS {fn.__name__}")
        except Exception:
            RESULTS.append((fn.__name__, False, traceback.format_exc()))
            print(f"FAIL {fn.__name__}\n{traceback.format_exc()}")
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
