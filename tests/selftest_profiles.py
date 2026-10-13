# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Self-test for reference docs and subject profile templates (fixed project layout).

    uv run tests/selftest_profiles.py [--fast] [-k SUBSTRING]

Checks (the source course repo is read-only; work files go to <plugin>/test-output/_r2/):
  * every template (cpp, fmi, cv, devops, _template) loads through notes_lib.profile.load_profile, and is
    portable: no repo_root/output keys, no absolute or machine-specific paths, paths relative to resources_dir
  * every profile body has the ten required H2 sections, in order
  * cpp: scripts/init_project.py scaffolds a temp project under test-output/_r2/, the session-2 transcript and
    L2.pdf are copied into its resources/, and resolve_inputs --dry-run finds them (skipped when the source
    course folder is not on this machine)
  * cpp: ASR table (>= 16 rows), 13-row session table matching the front matter, and NO session-2
    correction facts (the hidden E2E answer key)
  * the eight reference docs exist, are < 400 lines, and contain no section sign
  * the examples embedded in note-format / digest-template / screen-notes-template / research-template parse,
    and the digest and screen-notes examples pass check_digest.py / check_screen_notes.py when those exist

--fast runs scripts with this interpreter instead of `uv run --script`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
PROFILES = PLUGIN / "profiles"
REFERENCE = PLUGIN / "reference"
WORK = PLUGIN / "test-output" / "_r2"
SOURCE_RESOURCES = Path(SUBJ_CPP + "/resources")   # read-only source of real inputs
sys.path.insert(0, str(SCRIPTS))

from notes_lib import platform as plat, profile as prof  # noqa: E402

plat.utf8_stdio()
import yaml  # noqa: E402

FAST = False
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
SECTION_SIGN = chr(0xA7)
PROFILE_FILES = ["cpp", "fmi", "cv", "devops", "_template"]
REAL_PROFILES = ["cpp", "fmi", "cv", "devops"]
REFERENCE_DOCS = ["house-style", "tier-basic", "tier-advanced", "note-format", "figures",
                  "digest-template", "screen-notes-template", "research-template"]
BODY_SECTIONS = ["Role", "Reader", "Course", "Source handling", "Basic tier — subject rules",
                 "Advanced tier — subject rules", "Domain conventions", "ASR corrections", "Callouts",
                 "Known context"]
RESOLVE_CASES = [("cpp", 2), ("fmi", 1), ("cv", 2), ("devops", 1)]
FORBIDDEN_CPP = ["RIP", "_Z3addii"]          # session-2 answer key: must never appear in the cpp profile
RESULTS: list[tuple[str, str, str]] = []     # (name, PASS|FAIL|SKIP, detail)


class Skip(Exception):
    pass


def test(fn):
    fn._is_test = True
    return fn


def need(cond, label=""):
    assert cond, label


def eq(got, want, label=""):
    assert got == want, f"{label}: got {got!r}, want {want!r}"


def run_script(script: Path, *args, timeout=300):
    env = dict(os.environ)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env.pop("LECTURE_NOTES_DATA", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if FAST:
        cmd = [sys.executable, str(script), *map(str, args)]
    else:
        cmd = [UV, "run", "--quiet", "--script", str(script), *map(str, args)]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)


def body_sections(body: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"^## (.+?)\s*$", body, re.M)]


def section_text(body: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", body, re.M | re.S)
    assert m, f"section {title!r} missing"
    return m.group(1)


def table_rows(text: str) -> list[list[str]]:
    """Data rows of all markdown tables in text (header and separator rows dropped)."""
    rows = []
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s.startswith("|"):
            continue
        if re.match(r"^\|[\s:|-]+\|$", s):
            continue
        # a header row is the one directly above a separator row
        if i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        rows.append(cells)
    return rows


def fence_after(text: str, heading_regex: str, fence="````markdown") -> str:
    """Content of the first `fence`-opened block that appears after the heading."""
    m = re.search(heading_regex, text, re.M)
    assert m, f"heading /{heading_regex}/ not found"
    rest = text[m.end():]
    f = re.search(rf"^{re.escape(fence)}\n(.*?)\n````\s*$", rest, re.M | re.S)
    assert f, f"no {fence} block after /{heading_regex}/"
    return f.group(1)


def yaml_blocks(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^```yaml\n(.*?)\n```\s*$", text, re.M | re.S)]


# ------------------------------------------------------------------ profiles
@test
def t_profiles_load():
    for pid in PROFILE_FILES:
        p = prof.load_profile(str(PROFILES / f"{pid}.md"))
        need(p["id"], f"{pid}: id")
        need(p["paths"].get("transcripts"), f"{pid}: paths.transcripts")
        for tier in ("basic", "advanced"):
            c = prof.tier_cfg(p, tier)
            lo, hi = c["words"]
            need(0 < lo < hi, f"{pid}.{tier}: words {c['words']}")
        b = prof.tier_cfg(p, "basic")
        need(b["minutes"][0] < b["minutes"][1], f"{pid}: basic minutes")
        need(re.fullmatch(r"[A-Z0-9]+", p["prefix"]), f"{pid}: prefix")
        need(p["spelling"] in ("en-GB", "en-US", "any"), f"{pid}: spelling")
    # lookup by id and by alias works from the plugin's own profile dir
    eq(prof.load_profile("cpp")["id"], "cpp", "id lookup")
    eq(prof.load_profile("hft-cpp")["id"], "cpp", "alias lookup")
    eq(prof.load_profile("computer-vision")["id"], "cv", "alias lookup cv")


@test
def t_profile_templates_portable():
    for pid in PROFILE_FILES:
        txt = (PROFILES / f"{pid}.md").read_text(encoding="utf-8")
        meta, _ = prof.split_front_matter(txt)
        for gone in ("repo_root", "output"):
            need(gone not in meta, f"{pid}: front matter still has `{gone}`")
        need(not meta.get("resources_dir") or not re.match(r"([A-Za-z]:|/|~)", str(meta["resources_dir"])),
             f"{pid}: resources_dir must be relative (or omitted) in a template")
        need("prior_notes_external" not in meta or not meta["prior_notes_external"],
             f"{pid}: templates must not carry external prior-notes paths")
        front = txt.split("\n---\n", 1)[0]
        need(not re.search(r"[A-Za-z]:[/\\]", front), f"{pid}: absolute Windows path in front matter")
        need("Year 3/" not in front, f"{pid}: machine-specific path in front matter")
        for key, val in (meta.get("paths") or {}).items():
            for v in ([val] if isinstance(val, str) else val or []):
                need(not str(v).startswith(("resources/", "/", "~")) and not re.match(r"[A-Za-z]:", str(v)),
                     f"{pid}: paths.{key} must be relative to resources_dir, got {v!r}")


@test
def t_profile_body_sections():
    for pid in PROFILE_FILES:
        p = prof.load_profile(str(PROFILES / f"{pid}.md"))
        secs = body_sections(p["_body"])
        got = [s for s in secs if s in BODY_SECTIONS]
        eq(got, BODY_SECTIONS, f"{pid}: body sections (in order)")


@test
def t_profile_no_section_sign():
    for pid in PROFILE_FILES + ["README"]:
        txt = (PROFILES / f"{pid}.md").read_text(encoding="utf-8")
        need(SECTION_SIGN not in txt, f"{pid}.md contains the section sign")


@test
def t_profiles_load_without_sessions():
    """`sessions` is optional (derived from resources/): every template must load with the block removed."""
    tmp = WORK / "no-sessions"
    tmp.mkdir(parents=True, exist_ok=True)
    for pid in PROFILE_FILES:
        txt = (PROFILES / f"{pid}.md").read_text(encoding="utf-8")
        m = re.match(r"(---\r?\n)(.*?)(\r?\n---\r?\n)(.*)\Z", txt, re.S)
        need(m, f"{pid}: front matter not found")
        fm = re.sub(r"^sessions:.*?(?=^\S|\Z)", "", m.group(2) + "\n", flags=re.M | re.S).rstrip("\n")
        need("sessions:" not in fm or "\n  sessions:" in fm, f"{pid}: sessions block not removed")
        out = tmp / f"{pid}.md"
        out.write_text(m.group(1) + fm + m.group(3) + m.group(4), encoding="utf-8")
        p = prof.load_profile(str(out))
        eq(p["sessions"], [], f"{pid}: sessions without the block")
        need(p["id"], f"{pid}: id")


@test
def t_profile_sessions():
    for pid in REAL_PROFILES:
        p = prof.load_profile(pid)
        ns = [int(s["n"]) for s in p["sessions"]]
        need(ns == sorted(set(ns)), f"{pid}: session numbers must be unique and ascending: {ns}")
        for s in p["sessions"]:
            need(s.get("title"), f"{pid}: session {s['n']} needs a title")
            if s.get("date"):
                need(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["date"]), f"{pid}: date {s['date']}")
    cv = prof.load_profile("cv")
    eq(len(cv["sessions"]), 16, "cv: 16 sessions")
    info = prof.session_info(cv, "2a")
    eq(info["n"], 2, "session_info 2a")
    eq(info["date"], "2026-08-22", "cv session 2 date")


# ----------------------------------------------- project scaffold + resolve_inputs
def _scaffold_cpp(name: str) -> Path:
    """Fresh temp project with the cpp template (via init_project.py). Returns the project dir."""
    init = SCRIPTS / "init_project.py"
    if not init.is_file():
        raise Skip("scripts/init_project.py not written yet")
    proj = WORK / name
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    r = run_script(init, "--project", proj, "--profile", "cpp")
    need(r.returncode == 0, f"init_project exit {r.returncode}\n{r.stdout}\n{r.stderr}")
    return proj


@test
def t_init_scaffolds_project():
    proj = _scaffold_cpp("init")
    for rel in ("resources", "notes/basic", "notes/advanced", "pdf/basic", "pdf/advanced", ".notes-plugin-internal/profile.md"):
        need((proj / rel).exists(), f"init did not create {rel}")
    need((proj / "resources" / "README.md").is_file(), "resources/README.md missing")
    p = prof.load_profile(str(proj / ".notes-plugin-internal" / "profile.md"))
    eq(p["id"], "cpp", "scaffolded profile id")
    gi = proj / ".gitignore"
    need(gi.is_file() and ".notes-plugin-internal/work" in gi.read_text(encoding="utf-8"), ".gitignore lacks .notes-plugin-internal/work/")
    # a second init without --force must not overwrite the profile
    marker = proj / ".notes-plugin-internal" / "profile.md"
    marker.write_text(marker.read_text(encoding="utf-8") + "\n<!-- edited -->\n", encoding="utf-8")
    r = run_script(SCRIPTS / "init_project.py", "--project", proj, "--profile", "cpp")
    need(r.returncode != 0, "init overwrote an existing profile without --force")
    need("<!-- edited -->" in marker.read_text(encoding="utf-8"), "existing profile was modified")


@test
def t_resolve_dry_run_cpp_session2():
    script = SCRIPTS / "resolve_inputs.py"
    need(script.is_file(), "scripts/resolve_inputs.py missing")
    if not SOURCE_RESOURCES.is_dir():
        raise Skip(f"source course folder not found: {SOURCE_RESOURCES}")
    proj = _scaffold_cpp("resolve")
    p = prof.load_profile(str(proj / ".notes-plugin-internal" / "profile.md"))
    si = prof.session_info(p, 2)
    txt = SOURCE_RESOURCES / f"{si['stem']}.txt"
    need(txt.is_file(), f"source transcript missing: {txt}")
    shutil.copy2(txt, proj / "resources" / txt.name)
    slides = SOURCE_RESOURCES / "L2.pdf"
    need(slides.is_file(), f"source slides missing: {slides}")
    shutil.copy2(slides, proj / "resources" / "L2.pdf")
    out_json = WORK / "resolve-2.inputs.json"
    r = run_script(script, "--project", proj, "--session", 2, "--dry-run", "--json", out_json)
    need(r.returncode == 0, f"exit {r.returncode}\n{r.stdout}\n{r.stderr}")
    inputs = json.loads(out_json.read_text(encoding="utf-8"))
    t = inputs["transcript"]
    need(t and Path(t["path"]).is_file(), "transcript path missing")
    need(Path(t["path"]).as_posix().lower().startswith((proj / "resources").as_posix().lower()),
         "transcript not under <project>/resources")
    mism = [w for w in inputs["warnings"] if "differs from profile title" in w]
    need(not mism, f"profile title does not match the transcript header: {mism}")
    eq(inputs["session"]["date"], "2026-08-22", "cpp 2 date")
    need(inputs["instructor_code"] and inputs["instructor_code"]["dir"] == "Lec-2", "cpp 2 code dir")
    need(any(Path(x).name == "L2.pdf" for x in inputs["slides"]), f"L2.pdf not matched: {inputs['slides']}")
    eq(inputs["paths"]["basic_notes"].replace("\\", "/").rstrip("/").split("/")[-2:], ["notes", "basic"],
       "paths.basic_notes")
    need(not (proj / ".notes-plugin-internal" / "work" / "sessions" / "02" / "inputs.json").exists(), "--dry-run wrote inputs.json")


@test
def t_resolve_without_project_fails():
    script = SCRIPTS / "resolve_inputs.py"
    empty = WORK / "no-project"
    if empty.exists():
        shutil.rmtree(empty)
    empty.mkdir(parents=True)
    r = run_script(script, "--project", empty, "--session", 1, "--dry-run")
    need(r.returncode == 2, f"expected exit 2 outside a project, got {r.returncode}")


# ------------------------------------------------------------------------ cpp
@test
def t_cpp_asr_table():
    p = prof.load_profile("cpp")
    rows = table_rows(section_text(p["_body"], "ASR corrections"))
    need(len(rows) >= 16, f"cpp ASR table has {len(rows)} rows, need >= 16")
    blob = " | ".join(" ".join(r) for r in rows)
    for needle in ["hash include", "C out", "std C out", "end L", "name space", "asterisk", "ampersand",
                   "null putter", "dot O file", "G plus plus", "obj dump", "V table", "S T L", "reserve",
                   "unique putter", "L one", "seg fault", "G C", "nse", "you you you"]:
        need(needle in blob, f"cpp ASR table lacks {needle!r}")


@test
def t_cpp_session_table():
    p = prof.load_profile("cpp")
    rows = [r for r in table_rows(section_text(p["_body"], "Course")) if r and re.fullmatch(r"\d+", r[0])]
    eq([int(r[0]) for r in rows], list(range(1, 14)), "cpp session numbers in the Course table")
    for r, s in zip(rows, p["sessions"]):
        eq(r[1], s["title"], f"cpp session {r[0]} title (table vs front matter)")
        need(len(r) >= 3 and len(r[2]) > 10, f"cpp session {r[0]} needs core topics")
    for n in (1, 2, 3, 4, 5):
        need(prof.session_info(p, n)["stem"], f"cpp session {n} stem")
    for n in (1, 2, 3):
        need(prof.session_info(p, n)["date"], f"cpp session {n} date")


@test
def t_session_order_field():
    """`order` is optional and numeric; cpp's extra Fast Orderbooks (n 14) sorts between 12 and 13."""
    for pid in REAL_PROFILES:
        for s in prof.load_profile(pid)["sessions"]:
            if "order" in s:
                need(isinstance(s["order"], (int, float)) and not isinstance(s["order"], bool),
                     f"{pid}: session {s['n']} order must be a number")
    p = prof.load_profile("cpp")
    by_n = {int(s["n"]): s for s in p["sessions"]}
    eq(by_n[14].get("order"), 12.5, "cpp Fast Orderbooks order")
    ranked = sorted(p["sessions"], key=lambda s: (s.get("order", s["n"]), int(s["n"])))
    seq = [int(s["n"]) for s in ranked]
    need(seq.index(12) < seq.index(14) < seq.index(13), f"course order wrong: {seq}")


@test
def t_no_stale_internal_dir_name():
    """Docs, skills, agents and profiles must say .notes-plugin-internal/, never the legacy .notes/ (migration docs excepted)."""
    root = Path(__file__).resolve().parent.parent
    bad = []
    for sub_ in ("skills", "agents", "reference", "profiles"):
        for f in (root / sub_).rglob("*.md"):
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"(?<![\w-])\.notes(/|)", line) and "migrat" not in line.lower() and "legacy" not in line.lower():
                    bad.append(f"{f.relative_to(root)}:{i}")
    need(not bad, f"stale .notes/ mentions: {bad[:8]}")


@test
def t_cpp_has_no_answer_key():
    txt = (PROFILES / "cpp.md").read_text(encoding="utf-8")
    hits = [tok for tok in FORBIDDEN_CPP if tok in txt]
    need(not hits, f"cpp.md contains session-2 correction facts: {hits}")
    for tok in ["AT&T", "Intel syntax", "mangling", "endian", "buffered"]:
        need(tok.lower() not in txt.lower(), f"cpp.md contains answer-key-adjacent term {tok!r}")


@test
def t_cpp_budget_matches_instructor_rules():
    p = prof.load_profile("cpp")
    b = prof.tier_cfg(p, "basic")
    eq(b["words"], [3500, 7000], "cpp basic words (PLAN 12.1: whole class at class depth)")
    eq(b["minutes"], [60, 120], "cpp basic minutes (informational)")
    eq(b["figures"], "class+generated", "cpp basic figures (PLAN 12.2)")
    eq(p["video"]["delete_after_ingest"], "ask", "cpp video.delete_after_ingest")
    for pid in PROFILE_FILES:
        q = prof.load_profile(str(PROFILES / f"{pid}.md"))
        eq(prof.tier_cfg(q, "basic")["figures"], "class+generated", f"{pid} basic figures")
        eq(prof.tier_cfg(q, "basic")["words"], [3500, 7000], f"{pid} basic words")
    need("cpp" in p["snippets"] and "{src}" in p["snippets"]["cpp"]["compile"], "cpp snippets template")


# ------------------------------------------------------------------ reference
@test
def t_reference_docs():
    for name in REFERENCE_DOCS:
        f = REFERENCE / f"{name}.md"
        need(f.is_file(), f"reference/{name}.md missing")
        txt = f.read_text(encoding="utf-8")
        n = len(txt.splitlines())
        need(n < 400, f"reference/{name}.md has {n} lines (limit 400)")
        need(SECTION_SIGN not in txt, f"reference/{name}.md contains the section sign")
        need(txt.lstrip().startswith("# "), f"reference/{name}.md must start with an H1")
        for ref in re.findall(r"`([a-z-]+\.md)`", txt):
            if ref in {f"{d}.md" for d in REFERENCE_DOCS}:
                need((REFERENCE / ref).is_file(), f"{name}: broken doc reference {ref}")


@test
def t_note_format_examples():
    txt = (REFERENCE / "note-format.md").read_text(encoding="utf-8")
    blocks = yaml_blocks(txt)
    need(len(blocks) >= 3, "note-format.md: expected yaml examples")
    meta, _ = prof.split_front_matter(blocks[0] + "\n\nbody")
    for k in ["title", "session", "tier", "profile", "date", "duration_min", "sources", "instructor_code",
              "revision_minutes", "verified_as_of"]:
        need(k in meta, f"basic front-matter example lacks {k}")
    adv = yaml.safe_load(blocks[1])
    need("prerequisites" in adv, "advanced front-matter example keys")
    need("companion_of" not in adv, "no companion_of in the example: one note per session and tier")
    # the minimal-note example at the end must itself have valid front matter
    note = fence_after(txt, r"^## 11\. A minimal Basic note")
    m2, body2 = prof.split_front_matter(note)
    eq(m2["tier"], "basic", "minimal note tier")
    need("# 03 · Pointers and Raw Bytes" in body2, "minimal note H1")
    # label -> class table lists the built-ins the plan requires
    for label in ["Note", "Gotcha", "UB", "Class correction", "Reconstructed", "Gap fill", "Maths", "Paper",
                  "Jargon"]:
        need(label in txt, f"callout label {label} missing from note-format.md")


@test
def t_figures_doc_api_names():
    txt = (REFERENCE / "figures.md").read_text(encoding="utf-8")
    for name in ["figure", "save", "clean", "spines", "show", "grid_numbers", "arrow", "box",
                 "memory_strip", "schematic", "main", "NOTEFIG_PATH", "NOTEFIG_OUT", "make_figures.py",
                 "SCHEMATIC", "7.4"]:
        need(name in txt, f"figures.md lacks {name}")


# ------------------------------------------------------------------- templates
def _digest_example() -> str:
    txt = (REFERENCE / "digest-template.md").read_text(encoding="utf-8")
    return fence_after(txt, r"^## 4\. Worked example")


DIGEST_TITLES = ["Session timeline", "Concept inventory", "Concept detail", "Gaps",
                 "Class-correction candidates", "Reconstructed passages", "Code and output inventory",
                 "Claims and numbers ledger", "Vocabulary", "Course links",
                 "Class visuals", "Emphasis and assessment signals",
                 "Open questions and ambiguities"]


@test
def t_digest_example_structure():
    ex = _digest_example()
    meta, body = prof.split_front_matter(ex)
    for k in ["profile", "session", "title_syllabus", "title_as_taught", "slug", "date", "duration_min",
              "sources", "repo_moved_on", "audio_quality", "dropped_minutes", "verified_as_of"]:
        need(k in meta, f"digest example front matter lacks {k}")
    for k in ["transcript", "whisper", "frames_total", "frames_read", "instructor_code", "slides",
              "classwork"]:
        need(k in meta["sources"], f"digest sources lacks {k}")
    need(re.fullmatch(r"[a-z0-9-]+", meta["slug"]), "slug must be kebab-case")
    h2 = [(int(m.group(1)), m.group(2).strip()) for m in re.finditer(r"^## (\d+)\. (.+)$", body, re.M)]
    eq([n for n, _ in h2], list(range(1, 14)), "digest H2 numbers")
    eq([t for _, t in h2], DIGEST_TITLES, "digest H2 titles")
    ids = {k: re.findall(rf"^\| ({k}\d+) \|", body, re.M) for k in "CGKPNVD"}
    for k, v in ids.items():
        need(len(v) == len(set(v)), f"digest ids {k}: {v}")
        if k != "D":                       # D rows (extra figure opportunities) are optional
            need(v, f"digest ids {k}: none")
    # Class visuals rows: ID | Frame | Concept | What | Quality | Crop | Treatment
    vrows = re.findall(r"^\| V\d+ \|", body, re.M)
    good = re.findall(r"^\| (V\d+) \| F\d+ \(\d\d:\d\d:\d\d\) \| (C\d+) \| .+ \| (?:clean|messy|partial) \| "
                      r"([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+) \| (?:embed|redraw|both|text) \|\s*$", body, re.M)
    eq(len(good), len(vrows), "every V-row has frame(time), concept, quality, crop fractions, treatment")
    for vid, cid, x0, y0, x1, y1 in good:
        need(cid in set(ids["C"]), f"{vid} references unknown concept {cid}")
        a, b, c, d = (float(x0), float(y0), float(x1), float(y1))
        need(0 <= a < c <= 1 and 0 <= b < d <= 1, f"{vid}: crop fractions out of range")
    cids = set(ids["C"])
    for m in re.finditer(r"^\| (G\d+) \| (C\d+) \|", body, re.M):
        need(m.group(2) in cids, f"{m.group(1)} references unknown concept {m.group(2)}")
    need(set(re.findall(r"^### (C\d+) —", body, re.M)) == cids, "concept detail headings vs inventory")
    # timeline covers the whole duration with no gap over 10 minutes
    spans = re.findall(r"^\| (\d\d):(\d\d)–(\d\d):(\d\d) \|", body, re.M)
    need(spans, "timeline rows")
    cur = 0
    for a, b, c, d in spans:
        s, e = int(a) * 60 + int(b), int(c) * 60 + int(d)
        need(s - cur <= 10, f"timeline gap > 10 min before {a}:{b}")
        cur = max(cur, e)
    need(cur >= meta["duration_min"], f"timeline ends at {cur} < duration {meta['duration_min']}")


@test
def t_digest_example_passes_check_digest():
    script = SCRIPTS / "check_digest.py"
    if not script.is_file():
        raise Skip("scripts/check_digest.py does not exist yet (WP4); structural checks above cover the example")
    f = WORK / "digest-example.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_digest_example() + "\n", encoding="utf-8")
    r = run_script(script, f)
    need(r.returncode == 0, f"check_digest rejected the template example (exit {r.returncode}):\n"
                            f"{r.stdout}\n{r.stderr}")


def _screen_example() -> str:
    txt = (REFERENCE / "screen-notes-template.md").read_text(encoding="utf-8")
    return fence_after(txt, r"^## 5\. Complete minimal example")


@test
def t_screen_notes_example():
    ex = _screen_example()
    meta, body = prof.split_front_matter(ex)
    lo, hi = (int(x) for x in str(meta["frames"]).split("-"))
    read = set(meta["read"])
    skipped = {s["n"]: s["reason"] for s in meta["skipped"]}
    need(all(skipped.values()), "every skipped frame needs a reason")
    eq(read | set(skipped), set(range(lo, hi + 1)), "read + skipped must cover the whole frame range")
    need(not (read & set(skipped)), "a frame cannot be both read and skipped")
    heads = {int(m.group(1)) for m in re.finditer(r"^## F(\d+) · ", body, re.M)}
    eq(heads, read, "## F### headings vs front-matter read list")
    script = SCRIPTS / "check_screen_notes.py"
    if not script.is_file():
        raise Skip("scripts/check_screen_notes.py does not exist yet (WP4); structural check passed")
    d = WORK / "screen-notes"
    d.mkdir(parents=True, exist_ok=True)
    (d / "shard-1.md").write_text(ex + "\n", encoding="utf-8")
    fj = WORK / "frames.json"
    fj.write_text(json.dumps([{"n": n} for n in range(lo, hi + 1)]), encoding="utf-8")
    r = run_script(script, d, "--frames-json", fj)
    need(r.returncode == 0, f"check_screen_notes rejected the example (exit {r.returncode}):\n"
                            f"{r.stdout}\n{r.stderr}")


@test
def t_retired_rules_are_gone():
    """PLAN 12: 'never embed frames', 'no images' and the 20-35 minute basic budget are retired."""
    bad = [r"never embed", r"no images", r"no PNG", r"No figures", r"frames are input only",
           r"20[-\u2013]35 min", r"1,500[-\u2013]2,500", r"re-readable inside the profile"]
    files = [*REFERENCE.glob("*.md"), *PROFILES.glob("*.md"), *(PLUGIN / "agents").glob("*.md"),
             *(PLUGIN / "skills").glob("*/SKILL.md"), PLUGIN / "README.md"]
    hits = []
    for f in files:
        txt = f.read_text(encoding="utf-8")
        for pat in bad:
            if re.search(pat, txt, re.I):
                hits.append(f"{f.name}: /{pat}/")
    need(not hits, "retired rules still present: " + "; ".join(hits))


@test
def t_visuals_docs_and_skills():
    figs = (REFERENCE / "figures.md").read_text(encoding="utf-8")
    for k in ["class_figure.py", "--from-visuals", "visuals.json", "visuals.yaml", "From class",
              "Redrawn, corrected", "embed", "redraw", "both"]:
        need(k in figs, f"figures.md lacks {k}")
    fmt = (REFERENCE / "note-format.md").read_text(encoding="utf-8")
    need("visuals.json" in fmt and "figures/*.jpg" in fmt, "note-format.md lacks the visuals sidecar / jpg")
    sk = PLUGIN / "skills" / "archive-videos" / "SKILL.md"
    need(sk.is_file(), "skills/archive-videos/SKILL.md missing")
    meta, body = prof.split_front_matter(sk.read_text(encoding="utf-8"))
    eq(meta.get("name"), "archive-videos", "archive-videos skill name")
    need("archive_video.py" in body and "--confirm" in body and "AskUserQuestion" in body,
         "archive-videos skill body")
    lec = (PLUGIN / "skills" / "lecture" / "SKILL.md").read_text(encoding="utf-8")
    need(len(lec.splitlines()) < 500, "lecture SKILL.md must stay under 500 lines")
    need("run_in_background: false" in lec, "lecture SKILL.md lacks run_in_background: false")
    flow = (REFERENCE / "lecture-flow.md").read_text(encoding="utf-8")   # the per-session flow lives here
    for k in ["class_figure.py", "archive_video.py", "AskUserQuestion", "run_in_background: false"]:
        need(k in flow, f"lecture-flow.md lacks {k}")
    for f in (PLUGIN / "agents").glob("*.md"):
        meta, _ = prof.split_front_matter(f.read_text(encoding="utf-8"))
        eq(meta.get("omitClaudeMd"), True, f"{f.name}: omitClaudeMd")
        need(meta.get("model") and meta.get("tools"), f"{f.name}: model/tools")


@test
def t_research_example():
    txt = (REFERENCE / "research-template.md").read_text(encoding="utf-8")
    ex = fence_after(txt, r"^## 5\. Minimal example")
    meta, body = prof.split_front_matter(ex)
    for k in ["session", "profile", "mode", "verified_as_of", "searches", "fetched"]:
        need(k in meta, f"research example lacks {k}")
    need(meta["mode"] in ("verify", "full"), "mode")
    secs = re.findall(r"^## ([A-G])\. ", body, re.M)
    eq(secs, ["A", "B", "G"], "verify mode writes only A, B, G")
    g_urls = set(re.findall(r"^\| (https://\S+) \|", section_text_letter(body, "G"), re.M))
    used = set(re.findall(r"https://[^\s|)]+", section_text_letter(body, "A") + section_text_letter(body, "B")))
    used = {u.rstrip(".,") for u in used}
    need(used <= g_urls, f"URLs used but not in the source register: {used - g_urls}")


def section_text_letter(body: str, letter: str) -> str:
    m = re.search(rf"^## {letter}\. .*?$(.*?)(?=^## [A-G]\. |\Z)", body, re.M | re.S)
    assert m, f"part {letter} missing"
    return m.group(1)


@test
def t_template_profile_is_complete():
    txt = (PROFILES / "_template.md").read_text(encoding="utf-8")
    meta, _ = prof.split_front_matter(txt)
    for k in ["schema", "id", "aliases", "title", "short", "prefix", "institution", "spelling",
              "paths", "sessions", "asr", "snippets", "tiers", "research", "callouts", "branding", "models"]:
        need(k in meta, f"_template.md front matter lacks {k}")
    need("tiers" in meta and {"basic", "advanced"} <= set(meta["tiers"]), "template tiers")
    readme = (PROFILES / "README.md").read_text(encoding="utf-8")
    for k in meta:
        need(f"`{k}`" in readme or f"`{k}." in readme, f"profiles/README.md does not document `{k}`")


# ------------------------------------------------------------ agents and skills
AGENTS = PLUGIN / "agents"
SKILLS = PLUGIN / "skills"
EXPECTED_AGENTS = ["frame-reader", "digest-builder", "researcher", "basic-writer", "advanced-writer",
                   "figure-author", "note-qa", "session-runner", "sweep-analyst", "sweep-editor"]
EXPECTED_SKILLS = ["lecture", "update", "sweep", "init", "scan", "build", "doctor", "archive-videos"]


def _front(path: Path) -> tuple[dict, str, str]:
    txt = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", txt, re.S)
    assert m, f"{path.name}: no front matter"
    meta = yaml.safe_load(m.group(1))     # a stray ': ' inside an unquoted description fails here
    assert isinstance(meta, dict), f"{path.name}: front matter is not a mapping"
    return meta, m.group(2), txt


@test
def t_agents_front_matter():
    for name in EXPECTED_AGENTS:
        f = AGENTS / f"{name}.md"
        need(f.is_file(), f"agents/{name}.md missing")
        meta, body, _ = _front(f)
        eq(meta.get("name"), name, f"{name}: name")
        need(isinstance(meta.get("description"), str) and meta["description"].strip(), f"{name}: description")
        need(meta.get("model"), f"{name}: model")
        need(meta.get("tools"), f"{name}: tools")
        eq(meta.get("omitClaudeMd"), True, f"{name}: omitClaudeMd")
        need(body.strip(), f"{name}: empty body")
    for f in AGENTS.glob("*.md"):
        need(f.stem in EXPECTED_AGENTS, f"unexpected agent {f.name}: add it to EXPECTED_AGENTS")


@test
def t_agent_tools_match_roles():
    tools = lambda n: {t.strip() for t in str(_front(AGENTS / f"{n}.md")[0]["tools"]).split(",")}
    need({"Agent", "Bash"} <= tools("session-runner"), "session-runner needs Agent and Bash")
    eq(_front(AGENTS / "session-runner.md")[0]["model"], "sonnet", "session-runner model")
    need("Edit" in tools("sweep-editor") and "Write" not in tools("sweep-editor"), "sweep-editor is Edit-only")
    need("Write" in tools("sweep-analyst") and "Edit" not in tools("sweep-analyst"), "sweep-analyst writes JSON only")


@test
def t_skills_front_matter():
    for name in EXPECTED_SKILLS:
        f = SKILLS / name / "SKILL.md"
        need(f.is_file(), f"skills/{name}/SKILL.md missing")
        meta, _, txt = _front(f)
        eq(meta.get("name"), name, f"{name}: name")
        need(isinstance(meta.get("description"), str) and meta["description"].strip(), f"{name}: description")
        n = len(txt.splitlines())
        need(n < 500, f"skills/{name}/SKILL.md has {n} lines (limit 500)")
        need(meta.get("allowed-tools"), f"{name}: allowed-tools")
    for d in SKILLS.iterdir():
        if d.is_dir():
            need(d.name in EXPECTED_SKILLS, f"unexpected skill {d.name}: add it to EXPECTED_SKILLS")


@test
def t_skill_argument_hints():
    hint = lambda n: str(_front(SKILLS / n / "SKILL.md")[0].get("argument-hint", ""))
    need("--parallel K" in hint("update"), "update argument-hint lacks --parallel K")
    for flag in ("--tier", "--sessions", "--dry-run", "--yes"):
        need(flag in hint("sweep"), f"sweep argument-hint lacks {flag}")
    need("--from-syllabus" in hint("init"), "init argument-hint lacks --from-syllabus")


@test
def t_lecture_flow_single_source():
    flow = REFERENCE / "lecture-flow.md"
    need(flow.is_file(), "reference/lecture-flow.md missing")
    txt = flow.read_text(encoding="utf-8")
    need(SECTION_SIGN not in txt, "lecture-flow.md contains the section sign")
    for step in ("Step 0.", "Step 4.", "Step 9.", "Step 11.", "Step 13.", "Step 15."):
        need(step in txt, f"lecture-flow.md lacks {step}")
    for who in ("skills/lecture/SKILL.md", "agents/session-runner.md"):
        need(who.split("/")[0] in ("skills", "agents"), who)
        body = (PLUGIN / who).read_text(encoding="utf-8")
        need("reference/lecture-flow.md" in body, f"{who} does not reference reference/lecture-flow.md")
    # the lecture skill is a thin wrapper: the per-session steps live only in the flow doc
    lec = (SKILLS / "lecture" / "SKILL.md").read_text(encoding="utf-8")
    need("## Step 7" not in lec and len(lec.splitlines()) < 80, "lecture skill must stay a thin wrapper")
    need("notes:session-runner" in (SKILLS / "update" / "SKILL.md").read_text(encoding="utf-8"),
         "update skill must dispatch notes:session-runner")


@test
def t_update_and_sweep_rules():
    upd = (SKILLS / "update" / "SKILL.md").read_text(encoding="utf-8")
    for needle in ("run_in_background: false", "gpu.lock", "--no-rollup", "never end the turn", "failed",
                   "delete_after_ingest: always", "ONE message", "--parallel"):
        need(needle in upd, f"update skill lacks: {needle}")
    sw = (SKILLS / "sweep" / "SKILL.md").read_text(encoding="utf-8")
    for needle in ("notes:sweep-analyst", "notes:sweep-editor", "<!-- sweep:ID -->", "--mode sweep",
                   "--yes", "concept_map.py", "run_in_background: false"):
        need(needle in sw, f"sweep skill lacks: {needle}")
    ed = (AGENTS / "sweep-editor.md").read_text(encoding="utf-8")
    need("<!-- sweep:ID -->" in ed and "<!-- /sweep:ID -->" in ed, "sweep-editor marker format")


@test
def t_corrections_calibration_docs():
    basic = (REFERENCE / "tier-basic.md").read_text(encoding="utf-8")
    need("Minor slips in class" in basic and "| said | actual |" not in "" and "reading time" in basic,
         "tier-basic.md lacks the minor-slips table or the reading-time criterion")
    style = (REFERENCE / "house-style.md").read_text(encoding="utf-8")
    need("Repo note" in style and "not a correction" in style, "house-style.md lacks the Repo note rule")
    need("Minor slips in class" in style, "house-style.md lacks the Minor slips rule")
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")
    for needle in ("/notes:sweep", "--parallel", "--from-syllabus", "Repo note", "reading time"):
        need(needle in readme, f"README.md lacks {needle}")


@test
def t_figure_author_verification():
    t = (AGENTS / "figure-author.md").read_text(encoding="utf-8")
    for needle in ("No cut text or drawing", "No UI chrome", "Not mostly blank", "At most 2 re-crops"):
        need(needle in t, f"figure-author lacks: {needle}")


@test
def t_writers_use_data_exercises():
    for n in ("digest-builder", "basic-writer", "advanced-writer"):
        t = (AGENTS / f"{n}.md").read_text(encoding="utf-8")
        need("exercises" in t and "references" in t, f"{n} ignores exercises/references")
    need("Repo note" in (AGENTS / "basic-writer.md").read_text(encoding="utf-8"), "basic-writer: Repo note")
    need("Minor slips" in (AGENTS / "basic-writer.md").read_text(encoding="utf-8"), "basic-writer: Minor slips")


# ----------------------------------------------------------------------- main
def main(argv=None) -> int:
    global FAST
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true", help="run scripts with this interpreter, not uv")
    ap.add_argument("-k", default="", help="only tests whose name contains this substring")
    a = ap.parse_args(argv)
    FAST = a.fast
    WORK.mkdir(parents=True, exist_ok=True)

    tests = [(n, f) for n, f in sorted(globals().items(), key=lambda kv: _order(kv[1]))
             if getattr(f, "_is_test", False) and a.k in n]
    for name, fn in tests:
        try:
            fn()
            RESULTS.append((name, "PASS", ""))
        except Skip as e:
            RESULTS.append((name, "SKIP", str(e)))
        except AssertionError as e:
            RESULTS.append((name, "FAIL", str(e)))
        except Exception as e:  # noqa: BLE001
            RESULTS.append((name, "FAIL", f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
    width = max((len(n) for n, _, _ in RESULTS), default=10)
    for name, status, detail in RESULTS:
        print(f"{status:<4}  {name:<{width}}  {detail.splitlines()[0] if detail else ''}")
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    for name, _, detail in fails:
        print(f"\n--- {name}\n{detail}")
    skips = sum(1 for r in RESULTS if r[1] == "SKIP")
    print(f"\n{len(RESULTS) - len(fails) - skips} passed, {len(fails)} failed, {skips} skipped "
          f"(of {len(RESULTS)})")
    return 1 if fails else 0


def _order(f) -> int:
    try:
        return f.__code__.co_firstlineno
    except AttributeError:
        return 10**9


if __name__ == "__main__":
    sys.exit(main())
