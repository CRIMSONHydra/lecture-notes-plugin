# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf", "pyyaml", "markdown-it-py>=3", "mdit-py-plugins", "pygments", "websocket-client"]
# ///
"""Self-test for the renderer (build_pdf + audit_pdf) on tiny fixed-layout projects.

    uv run tests/selftest_render.py [--no-legacy] [-k SUBSTRING]

Checks: synthetic fixtures (both tiers), negative fixtures, legacy cpp regression, legacy CV
regression (the last two only when those read-only repos exist), offline build.  Work dir:
<plugin>/test-output/_r1/render/ (recreated each run).  Legacy sources are only ever READ.
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
from pathlib import Path

import pymupdf

PLUGIN = Path(__file__).resolve().parents[1]
# optional read-only real subject projects for extra regression tests; every test that needs one skips when absent
SUBJ_CPP = os.environ.get("NOTES_TEST_CPP_ROOT", "/nonexistent/cpp-subject")
SUBJ_FDE = os.environ.get("NOTES_TEST_FDE_ROOT", "/nonexistent/fde-subject")
SUBJ_CV = os.environ.get("NOTES_TEST_CV_ROOT", "/nonexistent/cv-subject")
SCRIPTS = PLUGIN / "scripts"
FIX = PLUGIN / "tests" / "fixtures" / "render"
WORK = PLUGIN / "test-output" / "_r1" / "render"
DATA = PLUGIN / "test-output" / "_r1" / "render-data"
CPP = Path(SUBJ_CPP)
CV = Path(SUBJ_CV)
UV = shutil.which("uv") or "uv"
RESULTS: list[tuple[str, bool, str]] = []
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    return bool(ok)


def run(script: str, *args, timeout=900):
    cmd = [UV, "run", "--quiet", "--script", str(SCRIPTS / script), *map(str, args)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env.pop("LECTURE_NOTES_DATA", None)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)


def bdir(proj: Path, tier: str) -> Path:
    return proj / ".notes-plugin-internal" / "work" / "build" / tier


def pdir(proj: Path, tier: str) -> Path:
    return proj / "pdf" / tier


def build(proj: Path, tier: str, *extra):
    js = proj / ".notes-plugin-internal" / f"{tier}.build.json"
    r = run("build_pdf.py", "--project", proj, "--tier", tier, "--data-dir", DATA, "--json", js, *extra)
    return r, (json.loads(js.read_text(encoding="utf-8")) if js.is_file() else {})


def audit(proj: Path, tier: str):
    js = proj / ".notes-plugin-internal" / f"{tier}.audit.json"
    r = run("audit_pdf.py", "--project", proj, "--tier", tier, "--json", js)       # manifest derived from the layout
    return r, (json.loads(js.read_text(encoding="utf-8")) if js.is_file() else {})


def rmtree(p: Path) -> None:
    def onerr(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    shutil.rmtree(p, onerror=onerr, ignore_errors=False) if p.exists() else None


def make_png(path: Path, w: int, h: int, seed: int) -> None:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.set_rect(pix.irect, (246, 248, 250))
    for i in range(14):
        x0, y0 = (i * 97 + seed * 31) % (w - 140), (i * 53 + seed * 17) % (h - 90)
        pix.set_rect(pymupdf.IRect(x0, y0, x0 + 120, y0 + 70), ((40 * i + seed) % 220, (90 + 30 * i) % 220, 160))
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))


def stage(name: str, fixture: str, profile: str, tier: str, figures=()) -> Path:
    """A tiny project: .notes-plugin-internal/profile.md, notes/<tier>/ (fixture sources + figures), pdf/, work dirs."""
    proj = WORK / name
    (proj / ".notes-plugin-internal").mkdir(parents=True)
    shutil.copy2(FIX / profile, proj / ".notes-plugin-internal" / "profile.md")
    shutil.copytree(FIX / fixture / "src", proj / "notes" / tier)
    for k, f in enumerate(figures):
        make_png(proj / "notes" / tier / "figures" / f, 1480, 560, k + 1)
    return proj


def contents_matches_outline(pdf: Path) -> tuple[bool, str]:
    doc = pymupdf.open(pdf)
    toc = doc.get_toc()
    want = {}
    for lvl, title, page in toc:
        m = re.match(r"\u00a7(\d+[a-z]?(?:\.\d+)+)\s", title)
        if m and lvl == 2:
            want[m.group(1)] = page
    first_ch = min(want.values()) if want else doc.page_count
    got = {}
    for pno in range(1, first_ch):                       # page 1 is the cover
        rows: dict[int, list] = {}
        for x0, y0, x1, y1, w, *_ in doc[pno].get_text("words"):
            rows.setdefault(round(y0 / 2), []).append((x0, w))
        for ws in rows.values():
            toks = [w for _, w in sorted(ws)]
            if re.fullmatch(r"\d+[a-z]?(?:\.\d+)+", toks[0]) and toks[-1].isdigit():
                got[toks[0]] = int(toks[-1])
    doc.close()
    bad = {k: (v, got.get(k)) for k, v in want.items() if got.get(k) != v}
    return (not bad and bool(want)), f"{len(want)} sections, mismatches: {bad}" if bad else f"{len(want)} sections match"


def pdfs(proj: Path, tier: str) -> dict[str, dict]:
    m = json.loads((bdir(proj, tier) / "manifest.json").read_text(encoding="utf-8"))
    return {e["name"]: e for e in m["pdfs"]}


def t_synthetic(sel):
    print("\n== synthetic fixtures ==")
    for tier, fixture, profile, figs in (
        ("basic", "basic", "profile-basic.md", ()),
        ("advanced", "advanced", "profile-advanced.md", ("02-brief-pairs.png", "02-ransac-line.png")),
    ):
        proj = stage(f"syn-{tier}", fixture, profile, tier, figs)
        r, js = build(proj, tier)
        check(f"{tier}: build exit 0", r.returncode == 0, (r.stdout + r.stderr)[-400:] if r.returncode else "")
        check(f"{tier}: outputs land in pdf/{tier}/ and .notes-plugin-internal/work/build/{tier}/",
              any(pdir(proj, tier).glob("*.pdf")) and (bdir(proj, tier) / "manifest.json").is_file()
              and not (proj / "notes" / tier / "build").exists())
        a, aj = audit(proj, tier)
        check(f"{tier}: audit 0 problems (derived manifest)", a.returncode == 0 and aj.get("problems") == 0, a.stdout[-600:] if a.returncode else "")
        ents = pdfs(proj, tier) if (bdir(proj, tier) / "manifest.json").is_file() else {}
        comp = ents.get(f"{'CPP' if tier == 'basic' else 'CV'}-Compendium-{tier}.pdf", {})
        check(f"{tier}: compendium + index + notes built", len(ents) == 4, str(sorted(ents)))
        if tier == "advanced":
            check("advanced: images_expected == 2 and met", comp.get("images_expected") == 2 and
                  all(f["images"] >= (f["images_expected"] or 0) for f in aj.get("files", [])))
            check("advanced: katex/mermaid errors 0, js used",
                  all(e["katex_errors"] == 0 and e["mermaid_errors"] == 0 for e in ents.values())
                  and any(e["js_used"] for e in ents.values()))
        t3 = [f for f in aj.get("files", []) if f["details"].get("type3")]
        check(f"{tier}: no Type 3 fonts", not t3)
        ok, d = contents_matches_outline(pdir(proj, tier) / comp.get("name", "x.pdf")) if comp else (False, "no compendium")
        check(f"{tier}: compendium contents page numbers == outline pages", ok, d)
        if tier == "advanced":
            n02a = next((e for n, e in ents.items() if "02a" in n), None)
            check("advanced: companion 02a built with section ids 2a.k", bool(n02a) and any(
                re.match(r"\u00a72a\.\d", t[1]) for t in pymupdf.open(n02a["pdf"]).get_toc()))
    # both tiers in one project; explicit-dir overrides; bad project
    proj = WORK / "syn-basic"
    shutil.copytree(FIX / "advanced" / "src", proj / "notes" / "advanced")
    for k, f in enumerate(("02-brief-pairs.png", "02-ransac-line.png")):
        make_png(proj / "notes" / "advanced" / "figures" / f, 1480, 560, k + 1)
    r, _ = build(proj, "advanced", "--notes-only")
    check("two tiers share one project: advanced build ok", r.returncode == 0, (r.stdout + r.stderr)[-300:] if r.returncode else "")
    r = run("build_pdf.py", "--profile", FIX / "profile-basic.md", "--tier", "basic", "--src", proj / "notes" / "basic",
            "--pdf-dir", WORK / "ovr" / "pdf", "--build-dir", WORK / "ovr" / "build", "--notes-only", "--data-dir", DATA)
    check("explicit --src/--pdf-dir/--build-dir (no project)", r.returncode == 0 and any((WORK / "ovr" / "pdf").glob("*.pdf")),
          (r.stdout + r.stderr)[-300:] if r.returncode else "")
    r = run("build_pdf.py", "--tier", "basic", "--project", WORK / "no-such", "--data-dir", DATA)
    check("missing project -> exit 2", r.returncode == 2, (r.stderr + r.stdout)[-200:])


def t_negative(sel):
    print("\n== negative fixtures ==")
    proj = stage("neg-wide", "negative/wide-code", "profile-basic.md", "basic")
    r, _ = build(proj, "basic")
    a, aj = audit(proj, "basic")
    check("wide code: build ok but audit exits 1 with overflow", r.returncode == 0 and a.returncode == 1
          and "OVERFLOW" in a.stdout.upper(), a.stdout[-300:])
    proj = stage("neg-missing", "negative/missing-figure", "profile-advanced.md", "advanced")
    r, js = build(proj, "advanced")
    check("missing figure: build exit 1 with MISSING FIGURE", r.returncode == 1 and "MISSING FIGURE" in r.stdout,
          r.stdout[-300:])
    proj = stage("neg-math", "negative/bad-math", "profile-advanced.md", "advanced")
    r, js = build(proj, "advanced")
    a, aj = audit(proj, "advanced")
    check("bad math/mermaid: manifest counts errors, audit exits 1",
          a.returncode == 1 and "KATEX ERRORS" in a.stdout and "MERMAID ERRORS" in a.stdout, a.stdout[-400:])


def t_legacy_cpp(sel):
    print("\n== legacy cpp regression ==")
    notes = CPP / "notes"
    if not notes.is_dir() or not list(notes.glob("0*.md")):       # the hand-written notes 01-03 were replaced by notes/<tier>/
        print(f"  [SKIP] legacy cpp: no hand-written notes in {notes}")
        return
    proj = WORK / "legacy-cpp"
    (proj / ".notes-plugin-internal").mkdir(parents=True)
    shutil.copy2(FIX / "profile-basic.md", proj / ".notes-plugin-internal" / "profile.md")
    (proj / "notes" / "basic").mkdir(parents=True)
    for f in notes.glob("*.md"):
        shutil.copy2(f, proj / "notes" / "basic" / f.name)
    r, js = build(proj, "basic")
    check("legacy cpp: build exit 0", r.returncode == 0, (r.stdout + r.stderr)[-400:] if r.returncode else "")
    a, aj = audit(proj, "basic")
    check("legacy cpp: audit clean", a.returncode == 0, a.stdout[-500:] if a.returncode else "")
    ents = pdfs(proj, "basic")
    n02 = next((e for n, e in ents.items() if n.startswith("CPP-02-")), {})
    comp = ents.get("CPP-Compendium-basic.pdf", {})
    check("legacy cpp: CPP-02 has 7+-1 pages and >=13 bookmarks",
          6 <= n02.get("pages", 0) <= 8 and n02.get("bookmarks", 0) >= 13, f"{n02.get('pages')} pages, {n02.get('bookmarks')} bookmarks")
    check("legacy cpp: compendium 24+-2 pages and >=46 bookmarks",
          22 <= comp.get("pages", 0) <= 26 and comp.get("bookmarks", 0) >= 46, f"{comp.get('pages')} pages, {comp.get('bookmarks')} bookmarks")
    if comp:
        check("legacy cpp: contents == outline", *contents_matches_outline(Path(comp["pdf"])))


def t_legacy_cv(sel):
    print("\n== legacy CV regression ==")
    src = CV / "note-src"
    note = src / "02-feature-matching-and-classical-cv.md"
    if not note.is_file():
        print(f"  [SKIP] legacy CV: no source notes in {src}")
        return None
    proj = WORK / "legacy-cv"
    (proj / ".notes-plugin-internal").mkdir(parents=True)
    shutil.copy2(FIX / "profile-advanced.md", proj / ".notes-plugin-internal" / "profile.md")
    nd = proj / "notes" / "advanced"
    (nd / "figures").mkdir(parents=True)
    shutil.copy2(note, nd / note.name)
    for p in sorted(set(re.findall(r"\((figures/[^\s)]+)", note.read_text(encoding="utf-8")))):
        shutil.copy2(src / p, nd / p)
    r, js = build(proj, "advanced", "--notes-only")
    check("legacy CV: build exit 0", r.returncode == 0, (r.stdout + r.stderr)[-400:] if r.returncode else "")
    a, aj = audit(proj, "advanced")
    check("legacy CV: audit clean", a.returncode == 0, a.stdout[-500:] if a.returncode else "")
    e = next(iter(pdfs(proj, "advanced").values()), {})
    f = aj.get("files", [{}])[0]
    check("legacy CV: 8 images", e.get("images_expected") == 8 and f.get("images", 0) >= 8, f"{e.get('images_expected')} expected, {f.get('images')} embedded")
    check("legacy CV: 2 mermaid rendered, 0 KaTeX errors", e.get("mermaid_errors") == 0 and e.get("katex_errors") == 0)
    check("legacy CV: 24+-4 pages and >=40 bookmarks", 20 <= e.get("pages", 0) <= 28 and e.get("bookmarks", 0) >= 40,
          f"{e.get('pages')} pages, {e.get('bookmarks')} bookmarks")


def t_offline(sel):
    print("\n== offline ==")
    figs = ("02-brief-pairs.png", "02-ransac-line.png")
    proj = stage("offline", "advanced", "profile-advanced.md", "advanced", figs)
    r, js = build(proj, "advanced", "--offline")
    check("offline: build with cached assets succeeds", r.returncode == 0, (r.stdout + r.stderr)[-300:] if r.returncode else "")
    empty = WORK / "empty-data"
    proj2 = stage("offline-empty", "advanced", "profile-advanced.md", "advanced", figs)
    r = run("build_pdf.py", "--project", proj2, "--tier", "advanced", "--data-dir", empty, "--offline")
    check("offline: empty cache -> clear error, exit 1", r.returncode == 1 and "--offline" in (r.stderr + r.stdout),
          (r.stderr + r.stdout)[-200:])


def t_style(sel):
    """Callouts get their colour class, headings their section-number span, and the colour layer is in both sheets."""
    print("\n== style ==")
    sys.path.insert(0, str(SCRIPTS))
    from render import css as css_mod, markdown as md_mod
    md = md_mod.make_md("basic", False, {"Cost reality": "reality"})
    out = md.render("> **Note:** a\n\n> **Class correction (\U0001f7e0 high):** b\n\n> **Cost reality:** c\n\n> plain\n")
    check("style: callouts carry their class",
          all(f'class="cal {c}"' in out for c in ("note", "correction", "reality")) and "<blockquote>\n<p>plain" in out,
          out[:300])
    h = md_mod.mark_secno('<h2 id="sec-3.2">§3.2 Loops</h2><h3 id="sec-3.2.1">§3.2.1 for</h3>')
    check("style: section numbers wrapped, text unchanged",
          h.count('<span class="secno">') == 2 and re.sub(r"<[^>]+>", "", h) == "§3.2 Loops§3.2.1 for", h)
    det = ("1. Q one?\n   <details><summary>Answer</summary>**Bold one.**\n   more.</details>\n\n"
           "2. Q two? <details><summary>Answer</summary>**Bold two.**</details>\n\n"
           "3. Q three?\n   <details><summary>Answer</summary>\n\n   **Bold three.**\n\n   </details>\n")
    out = md.render(md_mod.open_details_blocks(det))
    check("style: details answers render their markdown in every layout",
          "**" not in out and all(f"<strong>Bold {w}.</strong>" in out for w in ("one", "two", "three")), out[:500])
    check("style: details fix leaves code fences alone",
          md_mod.open_details_blocks("```\n<details>x</details>\n```") == "```\n<details>x</details>\n```")
    for tier in ("basic", "advanced"):
        sheet = css_mod.css_for({"branding": {"accent": "#0b5394", "accent2": "#c55a11"}}, tier)
        check(f"style: colour layer present ({tier})",
              "blockquote.cal.gotcha>p:first-child>strong:first-child" in sheet and "pre.code.lang-yaml" in sheet
              and ".secno" in sheet and "@@" not in sheet)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-legacy", action="store_true")
    ap.add_argument("-k", default="")
    args = ap.parse_args()
    rmtree(WORK)
    WORK.mkdir(parents=True)
    tests = [("style", t_style), ("synthetic", t_synthetic), ("negative", t_negative), ("legacy_cpp", t_legacy_cpp),
             ("legacy_cv", t_legacy_cv), ("offline", t_offline)]
    for name, fn in tests:
        if args.k and args.k not in name:
            continue
        if args.no_legacy and name.startswith("legacy"):
            continue
        try:
            fn(args.k)
        except Exception as e:  # keep going; report
            import traceback
            traceback.print_exc()
            check(f"{name}: no exception", False, repr(e))
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} checks passed" + (f"; FAILED: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
