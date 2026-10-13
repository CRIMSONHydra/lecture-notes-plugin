# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf"]
# ///
"""Self-test for notes_lib + ingest/platform/guard/init scripts. Hermetic: no network, no real subject project.

    uv run tests/selftest_ingest.py [--fast] [-k SUBSTRING]

Scripts are run through `uv run --script` (so their PEP-723 headers are exercised) unless --fast,
which uses this interpreter. Work dir: <plugin>/test-output/_r1/ingest/ (recreated each run).
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socketserver
import stat
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
FIX = PLUGIN / "tests" / "fixtures" / "ingest"
WORK = PLUGIN / "test-output" / "_r1" / "ingest"
PROJ = WORK / "proj"
sys.path.insert(0, str(SCRIPTS))

from notes_lib import chrome, guard, layout, notefile, platform as plat, profile  # noqa: E402

plat.utf8_stdio()
FAST = False
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
RESULTS: list[tuple[str, bool, str]] = []


def run(script: str, *args, env: dict | None = None, stdin: str | None = None, check_uv: bool | None = None):
    """Run scripts/<script> -> CompletedProcess (utf-8)."""
    e = dict(os.environ)
    e.pop("CLAUDE_PLUGIN_DATA", None)
    e.pop("LECTURE_NOTES_DATA", None)
    e["PYTHONIOENCODING"] = "utf-8"
    e.update(env or {})
    path = str(SCRIPTS / script)
    if FAST and check_uv is not True:
        cmd = [sys.executable, path, *map(str, args)]
    else:
        cmd = [UV, "run", "--quiet", "--script", path, *map(str, args)]
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8", env=e, timeout=300)


def rmtree(p: Path) -> None:
    def onerr(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)

    if p.exists():
        shutil.rmtree(p, onerror=onerr)


def test(fn):
    fn._is_test = True
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: got {got!r}, want {want!r}"


def truthy(cond, label=""):
    assert cond, label


# ---------------------------------------------------------------- notes_lib.platform
@test
def t_platform():
    kind = plat.os_kind()
    truthy(kind in ("windows", "macos", "linux"), kind)
    eq(plat.plugin_root().resolve(), PLUGIN.resolve(), "plugin_root")
    root = WORK / "Some Root"
    truthy(plat.is_under(root / "a" / "b.txt", root), "child")
    truthy(plat.is_under(root, root), "self")
    truthy(not plat.is_under(WORK / "Some Root2" / "x", root), "sibling prefix must not match")
    if kind == "windows":
        truthy(plat.is_under(str(root).upper() + "\\X.TXT", str(root).lower()), "case-insensitive on Windows")
        eq(plat.norm("/c/Windows"), Path("C:/Windows").resolve(), "msys path")
    d1 = plat.default_data_dir()
    truthy(d1.name == "lecture-notes" or os.environ.get("CLAUDE_PLUGIN_DATA") or os.environ.get("LECTURE_NOTES_DATA"), str(d1))
    old = {k: os.environ.get(k) for k in ("LECTURE_NOTES_DATA", "CLAUDE_PLUGIN_DATA")}
    try:
        os.environ["CLAUDE_PLUGIN_DATA"] = str(WORK / "pd")
        eq(plat.default_data_dir(), (WORK / "pd").resolve(), "CLAUDE_PLUGIN_DATA")
        os.environ["LECTURE_NOTES_DATA"] = str(WORK / "ln")
        eq(plat.default_data_dir(), (WORK / "ln").resolve(), "LECTURE_NOTES_DATA wins")
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    made = plat.resolve_data_dir(str(WORK / "rd" / "x"))
    truthy(made.is_dir(), "resolve_data_dir mkdir")
    eq(plat.which_any(["definitely-not-a-binary-xyz"]), None, "which_any miss")
    eq(plat.which_any([]), None, "which_any empty")
    g = plat.gpu_summary()
    eq(sorted(g), ["apple_silicon", "nvidia", "nvidia_name"], "gpu keys")


# ---------------------------------------------------------------- notes_lib.chrome
@test
def t_chrome():
    fake = WORK / "fakechrome" / ("chrome.exe" if os.name == "nt" else "chrome")
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("x")
    eq(chrome.find_chrome(explicit=fake), fake, "explicit")
    try:
        chrome.find_chrome(explicit=WORK / "nope" / "chrome")
        raise AssertionError("explicit missing must raise")
    except chrome.ChromeNotFound as e:
        truthy("install_chromium.py" in str(e), "hint mentions install_chromium")
    old = os.environ.get("LECTURE_NOTES_CHROME")
    try:
        os.environ["LECTURE_NOTES_CHROME"] = str(fake)
        eq(chrome.find_chrome(data_dir=WORK / "empty-data"), fake, "env")
        del os.environ["LECTURE_NOTES_CHROME"]
        dd = WORK / "data-with-path"
        dd.mkdir(parents=True, exist_ok=True)
        (dd / "chrome-path.txt").write_text(str(fake) + "\n")
        eq(chrome.find_chrome(data_dir=dd), fake, "chrome-path.txt")
    finally:
        os.environ.pop("LECTURE_NOTES_CHROME", None)
        if old:
            os.environ["LECTURE_NOTES_CHROME"] = old
    args = chrome.chrome_args(fake, WORK / "udd", 9333)
    truthy(args[0] == "--headless=new", "headless first")
    for want in ("--remote-debugging-port=9333", "--no-first-run", "--no-default-browser-check", "--disable-gpu",
                 "--disable-extensions", "--hide-scrollbars", "--remote-allow-origins=*",
                 "--allow-file-access-from-files"):
        truthy(want in args, want)
    truthy(any(a.startswith("--user-data-dir=") for a in args), "user-data-dir")
    truthy(str(fake) not in args, "executable is not part of the flags")
    shell = chrome.chrome_args(Path("x/chrome-headless-shell"), WORK / "u", 1)
    truthy("--headless=new" not in shell, "headless-shell omits --headless=new")
    truthy("--headless=new" not in chrome.chrome_args(Path("x/headless_shell"), WORK / "u", 1), "headless_shell")
    truthy(isinstance(chrome.INSTALL_HINT, str) and "install_chromium" in chrome.INSTALL_HINT, "INSTALL_HINT")


# ---------------------------------------------------------------- notes_lib.profile
def make_mini_profile() -> Path:
    """Tiny project under WORK/proj (resources/, legacy-notes/) -> its .notes-plugin-internal/profile.md."""
    if not PROJ.exists():
        shutil.copytree(FIX / "mini-project", PROJ)
        res = PROJ / "resources"
        (res / "Classwork" / "1" / "widget.exe").write_bytes(b"MZ\x00\x00")
        (res / "videos").mkdir(parents=True, exist_ok=True)
        (res / "videos" / "Intro_to_Widgets_-_Class.mp4").write_bytes(b"\x00" * 2048)
    text = (FIX / "profile-mini.md").read_text(encoding="utf-8").replace("@LEGACY@", (PROJ / "legacy-notes").as_posix())
    out = PROJ / ".notes-plugin-internal" / "profile.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


@test
def t_profile():
    pm = make_mini_profile()
    fm, body = profile.split_front_matter("---\na: 1\nb: [x, y]\n---\nhello\n")
    eq(fm, {"a": 1, "b": ["x", "y"]}, "front matter")
    eq(body, "hello\n", "body")
    eq(profile.split_front_matter("no front matter"), ({}, "no front matter"), "none")

    p = profile.load_profile(pm, data_dir=WORK / "data")
    eq(p["id"], "mini", "id")
    eq(p["_project"], PROJ.resolve(), "_project from .notes-plugin-internal/profile.md")
    eq(p["_resources"], (PROJ / "resources").resolve(), "_resources")
    eq(p["resources_dir"], "resources", "resources_dir default")
    eq((p["paths"]["transcripts"], p["paths"]["recordings"], p["paths"]["frames"], p["paths"]["whisper"]),
       ("{stem}.txt", "videos/{stem}.mp4", "frames/{stem}", "{stem}.whisper.json"), "paths defaults")
    eq(p["paths"]["slides"], ["L{n}.pdf", "L{n}_.pdf"], "paths override")
    eq(p["paths"]["handouts"], [], "handouts default")
    eq(p["_path"], pm.resolve(), "_path")
    truthy("Body text the agents would read." in p["_body"], "_body")
    eq(p["tiers"]["basic"]["words"], [100, 200], "override replaces list")
    eq(p["tiers"]["basic"]["minutes"], [60, 120], "default kept for sibling key")
    eq(p["tiers"]["basic"]["gapfill_max_share"], 0.3, "default share")
    eq(p["tiers"]["advanced"]["min_sources"], 8, "advanced default")
    truthy("companion_split_words" not in p["tiers"]["advanced"], "no companion split setting (one note per session)")
    truthy("Interview questions" not in p["tiers"]["advanced"]["required_sections"], "no interview default")
    eq(p["branding"], {"accent": "#1f4e79", "accent2": "#a3521a"}, "branding default")
    truthy("output" not in p and "repo_root" not in p, "no output/repo_root keys")
    eq(p["prior_notes_external"], [(PROJ / "legacy-notes").as_posix()], "prior_notes_external")
    eq(p["asr"]["language"], "en", "asr default")
    eq(p["sessions"][1]["date"], "2026-08-22", "yaml date -> iso string")
    eq(profile.tier_cfg(p, "basic")["words"], [100, 200], "tier_cfg")

    si = profile.session_info(p, "2a")
    eq((si["n"], si["nn"], si["title"], si["stem"], si["date"]), (2, "02", "Dates and Junk", "Dates_and_Junk_-_Class", "2026-08-22"), "2a")
    eq(profile.session_info(p, 9)["title"], None, "unknown session")

    # required keys
    bad = WORK / "bad-profile.md"
    bad.write_text("---\nschema: 1\nid: x\n---\nbody\n", encoding="utf-8")
    try:
        profile.load_profile(bad)
        raise AssertionError("missing keys must raise")
    except profile.ProfileError as e:
        truthy("title" in str(e) and "short" in str(e) and "prefix" in str(e) and "repo_root" not in str(e), str(e))

    # resolve_repo_paths: placeholders, globs, alternatives
    p["paths"]["slides"] = ["L{n}.pdf", "Intro_*.txt"]
    got = profile.resolve_repo_paths(p, "slides", **profile.session_info(p, 1))
    eq([x.name for x in got], ["Intro_to_Widgets_-_Class.txt"], "alternatives + glob (L1.pdf absent)")
    got = profile.resolve_repo_paths(p, "transcripts", **profile.session_info(p, 1))
    eq([x.name for x in got], ["Intro_to_Widgets_-_Class.txt"], "transcripts")
    eq(profile.resolve_repo_paths(p, "transcripts", **profile.session_info(p, 4)), [], "None stem -> skipped")
    eq(profile.resolve_repo_paths(p, "recordings", **profile.session_info(p, 1))[0].name,
       "Intro_to_Widgets_-_Class.mp4", "recordings")

    # find_profile lookup order
    ddir = WORK / "fp-data"
    (ddir / "profiles").mkdir(parents=True, exist_ok=True)
    shutil.copy(pm, ddir / "profiles" / "mini.md")
    eq(profile.find_profile("mini", data_dir=ddir, cwd=WORK / "nocwd"), (ddir / "profiles" / "mini.md").resolve(), "data dir")
    eq(profile.find_profile("w++", data_dir=ddir, cwd=WORK / "nocwd"), (ddir / "profiles" / "mini.md").resolve(), "alias scan")
    eq(profile.find_profile(str(pm), data_dir=ddir), pm.resolve(), "explicit path")
    sub = PROJ / "resources" / "deeper"
    sub.mkdir(parents=True, exist_ok=True)
    eq(profile.find_profile(None, data_dir=ddir, cwd=sub), pm.resolve(), "no ref -> walks up to .notes-plugin-internal/profile.md")
    eq(profile.find_profile("mini", data_dir=ddir, cwd=sub), pm.resolve(), "project profile beats data dir")
    eq(profile.find_profile("project", project=PROJ), pm.resolve(), "explicit project")
    try:
        profile.find_profile(None, data_dir=ddir, cwd=WORK / "nocwd")
        raise AssertionError("no project must raise")
    except profile.ProfileError:
        pass
    try:
        profile.find_profile("does-not-exist", data_dir=ddir, cwd=WORK / "nocwd")
        raise AssertionError("unknown profile must raise")
    except profile.ProfileError:
        pass


# ---------------------------------------------------------------- notes_lib.layout
@test
def t_layout():
    pm = make_mini_profile()
    sub = PROJ / "resources" / "deeper"
    sub.mkdir(parents=True, exist_ok=True)
    eq(layout.find_project(sub), PROJ.resolve(), "walk up")
    eq(layout.find_project(explicit=PROJ), PROJ.resolve(), "explicit")
    for bad in (lambda: layout.find_project(WORK / "nocwd-xyz"), lambda: layout.find_project(explicit=WORK / "nope-dir")):
        try:
            bad()
            raise AssertionError("must raise ProjectError")
        except layout.ProjectError as e:
            truthy("init" in str(e), str(e))
    lay = layout.Layout.for_project(start=sub)
    p = profile.load_profile(None, project=lay.root)
    root = PROJ.resolve()
    eq(lay.profile_path, root / ".notes-plugin-internal" / "profile.md", "profile_path")
    eq(lay.work, root / ".notes-plugin-internal" / "work", "work")
    eq(lay.resources(p), root / "resources", "resources")
    eq(lay.resources({"resources_dir": str(WORK / "ext-res")}), (WORK / "ext-res").resolve(), "absolute resources_dir")
    eq(lay.notes("basic"), root / "notes" / "basic", "notes")
    eq(lay.figures("advanced"), root / "notes" / "advanced" / "figures", "figures")
    eq(lay.pdf("basic"), root / "pdf" / "basic", "pdf")
    eq(lay.session(2), root / ".notes-plugin-internal" / "work" / "sessions" / "02", "session(int)")
    eq(lay.session("02"), lay.session(2), "session(str)")
    eq(lay.meta("basic"), root / ".notes-plugin-internal" / "work" / "meta" / "basic", "meta")
    eq(lay.build("advanced"), root / ".notes-plugin-internal" / "work" / "build" / "advanced", "build")
    eq(lay.reports, root / ".notes-plugin-internal" / "work" / "reports", "reports")
    lay.ensure()
    for d in (lay.figures("basic"), lay.figures("advanced"), lay.pdf("basic"), lay.pdf("advanced"), lay.reports):
        truthy(d.is_dir(), str(d))
    protect, allow = lay.guard_roots(p)
    eq(protect, [(root / "resources").as_posix(), (PROJ / "legacy-notes").resolve().as_posix(), root.as_posix()], "protect")
    eq(allow, [(root / x).as_posix() for x in ("notes/basic", "notes/advanced", "pdf/basic", "pdf/advanced", ".notes-plugin-internal")], "allow")
    eq(layout.infer_from_note(lay.notes("basic") / "01-x.md")[1], "basic", "infer tier")
    eq(layout.infer_from_note(WORK / "x" / "01-x.md"), (None, None), "infer none")
    eq(layout.resolve_out(None, PROJ, 3, "code"), lay.session(3) / "code", "resolve_out default")
    eq(layout.resolve_out(WORK / "o", PROJ, None, "code"), (WORK / "o").resolve(), "resolve_out explicit")


# ---------------------------------------------------------------- init_project
@test
def t_init_project():
    dst = WORK / "initproj"
    r = run("init_project.py", "--project", dst, "--profile", FIX / "profile-min.md")
    eq(r.returncode, 0, r.stderr + r.stdout)
    for rel in (".notes-plugin-internal/profile.md", ".notes-plugin-internal/work/build/basic", ".notes-plugin-internal/work/meta/advanced", ".notes-plugin-internal/work/reports",
                "resources/README.md", "resources/videos", "notes/basic/figures", "notes/advanced/figures",
                "pdf/basic", "pdf/advanced", ".gitignore"):
        truthy((dst / rel).exists(), rel)
    eq(profile.load_profile(None, project=dst)["id"], "cpp", "scaffolded profile loads")
    truthy(".notes-plugin-internal/work/" in (dst / ".gitignore").read_text(encoding="utf-8").splitlines(), ".gitignore line")
    # second run: refuses without --force, keeps the profile
    prof_file = dst / ".notes-plugin-internal" / "profile.md"
    prof_file.write_text(prof_file.read_text(encoding="utf-8") + "\n<!-- mine -->\n", encoding="utf-8")
    r = run("init_project.py", "--project", dst, "--profile", FIX / "profile-min.md")
    eq(r.returncode, 2, "refuses to overwrite")
    truthy("--force" in r.stderr and "<!-- mine -->" in prof_file.read_text(encoding="utf-8"), r.stderr)
    r = run("init_project.py", "--project", dst, "--profile", FIX / "profile-min.md", "--force", "--id", "cpp2")
    eq(r.returncode, 0, r.stderr)
    eq(profile.load_profile(None, project=dst)["id"], "cpp2", "--force --id")
    eq((dst / ".gitignore").read_text(encoding="utf-8").count(".notes-plugin-internal/work/"), 1, "gitignore line not duplicated")
    # profile by path; existing .gitignore content kept
    d2 = WORK / "initproj2"
    d2.mkdir(parents=True)
    (d2 / ".gitignore").write_text("*.o", encoding="utf-8")
    r = run("init_project.py", "--project", d2, "--profile", FIX / "profile-min.md")
    eq(r.returncode, 0, r.stderr)
    eq((d2 / ".gitignore").read_text(encoding="utf-8").splitlines(), ["*.o", ".notes-plugin-internal/work/"], "gitignore appended")
    r = run("init_project.py", "--project", WORK / "initproj3", "--profile", "no-such-profile")
    truthy("--from-claude-md" in r.stderr and "--from-syllabus" in r.stderr, r.stderr)
    eq(r.returncode, 2, "unknown profile")


# ---------------------------------------------------------------- notes_lib.notefile
@test
def t_notefile():
    eq(notefile.note_id("02a-foo-bar.md"), ("02a", "foo-bar"), "note_id")
    eq(notefile.note_id("E:/x/02-pointers.md"), ("02", "pointers"), "note_id path")
    eq(notefile.note_id("README.md"), ("", "README"), "note_id readme")
    eq(notefile.pdf_name("CPP", "02", "pointers", "basic"), "CPP-02-pointers-basic.pdf", "pdf_name")
    eq(notefile.pdf_name("CPP", "00", "index", "advanced"), "CPP-00-index-advanced.pdf", "pdf_name index")
    eq(round(notefile.minutes(150, 0, 0), 4), 1.0, "minutes prose")
    eq(round(notefile.minutes(0, 15, 0), 4), 2.0, "minutes code (8 s/line)")
    eq(round(notefile.minutes(0, 0, 4), 4), 1.0, "minutes table (15 s/row)")

    n = notefile.parse_note(FIX / "note-sample.md")
    eq((n.legacy, n.meta["session"], n.h1, n.num, n.title), (False, 1, "01 · Sample note", "01", "Sample note"), "parse sample")
    truthy(n.body.lstrip().startswith("# 01"), "body excludes front matter")
    lg = notefile.parse_note(FIX / "note-legacy.md")
    eq((lg.legacy, lg.num, lg.title), (True, "02a", "Legacy note without front matter"), "parse legacy")

    kinds = [k for k, _t, _i in notefile.iter_blocks(n.body)]
    eq(kinds, ["heading", "heading", "prose", "quote", "code", "code", "table", "prose", "prose", "heading", "prose"], "block kinds")
    blocks = list(notefile.iter_blocks(n.body))
    eq(blocks[4][2], "cpp {run}", "code info")
    eq(blocks[0][2], 1, "heading level")
    # fence-aware: a '# not a heading' line inside a fence stays code
    inner = list(notefile.iter_blocks("```bash\n# comment\n| x | y |\n```\n\ntext\n"))
    eq([k for k, _t, _i in inner], ["code", "prose"], "fence-aware")

    s = notefile.stats(n.body, ["Going Deeper"])
    eq(s["prose_words"], 20, "prose_words")
    eq(s["code_lines"], 4, "code_lines")
    eq(s["table_rows"], 4, "table_rows")
    eq(s["figures"], 1, "figures")
    eq(s["links"], 2, "links (closing included)")
    eq(s["gapfill_words"], 6, "gapfill_words")
    eq(s["minutes"], 1.7, "minutes")
    s2 = notefile.stats(n.body, [])
    truthy(s2["prose_words"] > s["prose_words"], "closing section counted when not declared closing")
    eq(set(s), {"prose_words", "code_lines", "table_rows", "figures", "links", "gapfill_words", "minutes"}, "stats keys")
    # section numbers and case in closing headings
    sl = notefile.stats("# T\n\nfour words are here\n\n## 9. going deeper (optional)\n\nignored ignored\n")
    eq(sl["prose_words"], 5, "closing heading: case-insensitive, numbered")


# ---------------------------------------------------------------- notes_lib.guard (+ CLIs)
@test
def t_guard_lib():
    dd = WORK / "gdata"
    repo, out = WORK / "grepo", WORK / "grepo" / "out"
    guard.set_guard(dd, [repo], [out], hours=1, run_id="r1")
    g = guard.load_guard(dd)
    eq(g["run_id"], "r1", "run_id")
    truthy(guard.check_path(repo / "notes" / "x.md", g), "protected blocked")
    eq(guard.check_path(out / "x.md", g), None, "allow nested in protect wins (more specific)")
    eq(guard.check_path(WORK / "elsewhere" / "x.md", g), None, "outside both")
    msg = guard.check_path(repo / "a.md", g)
    truthy(msg.startswith("notes guard: ") and "is inside protected" in msg and "/notes:doctor --clear-guard" in msg, msg)
    # protect nested in allow stays protected
    guard.set_guard(dd, [repo / "notes"], [repo], hours=1)
    g = guard.load_guard(dd)
    truthy(guard.check_path(repo / "notes" / "a.md", g), "specific protect beats broad allow")
    eq(guard.check_path(repo / "other.md", g), None, "broad allow")
    try:
        guard.assert_writable(repo / "notes" / "a.md", dd)
        raise AssertionError("assert_writable must raise")
    except guard.GuardError:
        pass
    truthy(issubclass(guard.GuardError, PermissionError), "GuardError is a PermissionError")
    guard.assert_writable(repo / "other.md", dd)
    # expiry
    f = guard.set_guard(dd, [repo], [], hours=1)
    doc = json.loads(f.read_text(encoding="utf-8"))
    doc["expires_epoch"] = time.time() - 5
    f.write_text(json.dumps(doc), encoding="utf-8")
    eq(guard.load_guard(dd), None, "expired -> None")
    guard.assert_writable(repo / "x.md", dd)
    guard.set_guard(dd, [repo], [])
    truthy(guard.clear_guard(dd), "clear returns True")
    eq(guard.load_guard(dd), None, "cleared")
    truthy(not guard.clear_guard(dd), "clear again -> False")


@test
def t_guard_cli_and_hook():
    dd = WORK / "hook-data"
    repo, out = WORK / "hrepo", WORK / "hout"
    env = {"CLAUDE_PLUGIN_DATA": str(dd)}
    r = run("guard.py", "status", "--data-dir", dd)
    eq((r.returncode, json.loads(r.stdout)["active"]), (0, False), "status inactive")
    r = run("guard.py", "set", "--data-dir", dd, "--protect", repo, "--allow", out, "--hours", "1", "--run", "t1")
    eq(r.returncode, 0, r.stderr)
    r = run("guard.py", "status", "--data-dir", dd)
    st = json.loads(r.stdout)
    eq((st["active"], st["run_id"]), (True, "t1"), "status active")

    def hook(path_key, path, **extra):
        evt = {"tool_name": "Write", "tool_input": {path_key: str(path)}, **extra}
        return run("guard_hook.py", env=env, stdin=json.dumps(evt), check_uv=True)

    r = hook("file_path", repo / "notes" / "x.md")
    eq(r.returncode, 2, f"hook blocks inside protected ({r.stderr})")
    truthy("read-only" in r.stderr and "/notes:doctor --clear-guard" in r.stderr, r.stderr)
    eq(hook("file_path", out / "x.md").returncode, 0, "hook allows output")
    eq(hook("file_path", WORK / "unrelated" / "x.md").returncode, 0, "hook allows unrelated")
    eq(hook("notebook_path", repo / "a.ipynb").returncode, 2, "notebook_path honoured")
    eq(run("guard_hook.py", env=env, stdin=json.dumps({"tool_name": "Write", "cwd": str(repo),
       "tool_input": {"file_path": "rel.md"}})).returncode, 2, "relative path resolved against cwd")
    eq(run("guard_hook.py", env=env, stdin=json.dumps({"tool_name": "Write", "tool_input": {}})).returncode, 0, "no path")
    r = run("guard_hook.py", env=env, stdin="this is not json")
    eq(r.returncode, 0, "fail-open on garbage")
    truthy((dd / "guard-errors.log").is_file(), "error logged")
    r = run("guard.py", "clear", "--data-dir", dd)
    eq(r.returncode, 0, "clear")
    # --project derives protect (resources_dir + prior_notes_external) and allow (notes/ pdf/ .notes-plugin-internal/)
    make_mini_profile()
    r = run("guard.py", "set", "--data-dir", dd, "--project", PROJ)
    eq(r.returncode, 0, r.stderr)
    gj = json.loads(run("guard.py", "status", "--data-dir", dd).stdout)
    eq(sorted(Path(x).name for x in gj["protect"]), ["legacy-notes", PROJ.name, "resources"], "project protect")
    eq(sorted(Path(x).name for x in gj["allow"]), [".notes-plugin-internal", "advanced", "advanced", "basic", "basic"], "project allow")
    eq(hook("file_path", PROJ / "resources" / "x.txt").returncode, 2, "resources protected")
    eq(hook("file_path", PROJ / "legacy-notes" / "x.md").returncode, 2, "external prior notes protected")
    for ok in ("notes/basic/01-a.md", "pdf/basic/a.pdf", ".notes-plugin-internal/work/sessions/01/digest.md", ".notes-plugin-internal/profile.md"):
        eq(hook("file_path", PROJ / ok).returncode, 0, f"allowed {ok}")
    run("guard.py", "clear", "--data-dir", dd)
    eq(hook("file_path", repo / "notes" / "x.md").returncode, 0, "no guard -> allowed")
    # doctor --clear-guard path
    run("guard.py", "set", "--data-dir", dd, "--protect", repo)
    r = run("doctor.py", "--data-dir", dd, "--clear-guard", "--no-network")
    truthy("guard cleared" in r.stdout, r.stdout)
    eq(notes_guard_active(dd), False, "doctor cleared guard")


def notes_guard_active(dd) -> bool:
    return guard.load_guard(dd) is not None


@test
def t_scripts_self_guard():
    """Scripts refuse to write into a protected root."""
    dd = WORK / "sg-data"
    repo = WORK / "sg-repo"
    guard.set_guard(dd, [repo], [])
    r = run("prep_transcript.py", FIX / "mini-project" / "resources" / "Intro_to_Widgets_-_Class.txt",
            "--out", repo / "t", "--data-dir", dd)
    eq(r.returncode, 2, f"prep_transcript guarded ({r.stdout}{r.stderr})")
    truthy(not (repo / "t").exists(), "nothing written")
    r = run("fetch_code.py", "--repo", "o/n", "--dir", "Lec-1", "--out", repo / "code", "--data-dir", dd)
    eq(r.returncode, 2, "fetch_code guarded")
    guard.clear_guard(dd)
    # default outputs land under .notes-plugin-internal/work/sessions/NN/ ; resources/ is protected, .notes-plugin-internal is not
    make_mini_profile()
    lay = layout.Layout(PROJ.resolve())
    guard.set_guard(dd, *lay.guard_roots(profile.load_profile(None, project=lay.root)))
    r = run("prep_transcript.py", PROJ / "resources" / "Intro_to_Widgets_-_Class.txt", "--session", "1",
            "--project", PROJ, "--data-dir", dd)
    eq(r.returncode, 0, r.stderr + r.stdout)
    truthy((lay.session(1) / "transcript" / "header.json").is_file(), "prep_transcript default out")
    r = run("prep_transcript.py", PROJ / "resources" / "Intro_to_Widgets_-_Class.txt", "--out", PROJ / "resources" / "t",
            "--data-dir", dd)
    eq(r.returncode, 2, "writing into resources/ refused")
    r = run("prep_transcript.py", PROJ / "resources" / "Intro_to_Widgets_-_Class.txt", "--data-dir", dd)
    eq(r.returncode, 2, "no --out and no --session -> usage error")
    guard.clear_guard(dd)


# ---------------------------------------------------------------- prep_transcript
@test
def t_prep_transcript():
    out = WORK / "prep1"
    src = FIX / "mini-project" / "resources" / "Intro_to_Widgets_-_Class.txt"
    r = run("prep_transcript.py", src, "--out", out, "--width", "100")
    eq(r.returncode, 0, r.stderr + r.stdout)
    h = json.loads((out / "header.json").read_text(encoding="utf-8"))
    eq((h["course"], h["lecture_title"], h["duration_min"], h["model"], h["generator"]),
       ("Widget Engineering", "Intro to Widgets", 60, "test, model-x", "Test Extension"), "header fields")
    eq(h["date_ambiguous"], True, "3/4/2026 is ambiguous")
    eq(sorted(c["date"] for c in h["start_candidates"]), ["2026-03-04", "2026-04-03"], "candidates")
    eq(h["date"], None, "no date when ambiguous")
    truthy("@" not in json.dumps(h["fields"]), "email redacted")
    lines = (out / "transcript.wrapped.txt").read_text(encoding="utf-8").splitlines()
    truthy(len(lines) > 3 and max(len(x) for x in lines) <= 100, f"wrapped <= width (max {max(len(x) for x in lines)})")
    truthy(not any("Course Name" in x for x in lines), "header not in wrapped body")
    j = json.loads((out / "junk_runs.json").read_text(encoding="utf-8"))
    kinds = {k for sp in j["spans"] for k in sp["kinds"]}
    truthy({"repeat-token", "thank-you", "marker"} <= kinds, f"junk kinds {kinds}")
    first = j["spans"][0]
    eq((first["start"], first["line_start"]), (0, 1), "first junk span at body start")
    truthy(all(sp["approx_min"] is not None and 0 <= sp["approx_min"] <= 60 for sp in j["spans"]), "approx time in range")

    out2 = WORK / "prep2"
    r = run("prep_transcript.py", FIX / "mini-project" / "resources" / "Dates_and_Junk_-_Class.txt", "--out", out2)
    h2 = json.loads((out2 / "header.json").read_text(encoding="utf-8"))
    eq((h2["date"], h2["date_ambiguous"], h2["duration_min"]), ("2026-08-22", False, 120), "unambiguous m/d")
    eq(json.loads((out2 / "junk_runs.json").read_text(encoding="utf-8"))["count"], 0, "no junk")
    out3 = WORK / "prep3"
    r = run("prep_transcript.py", FIX / "mini-project" / "resources" / "No_Header_-_Class.txt", "--out", out3)
    eq(r.returncode, 0, "missing header allowed")
    h3 = json.loads((out3 / "header.json").read_text(encoding="utf-8"))
    eq((h3["has_header"], h3["duration_min"]), (False, None), "no header")
    r = run("prep_transcript.py", WORK / "missing.txt", "--out", WORK / "prep4")
    eq(r.returncode, 2, "missing file")


# ---------------------------------------------------------------- resolve_inputs
@test
def t_resolve_inputs():
    pm = make_mini_profile()
    dd = WORK / "ri-data"
    lay = layout.Layout(PROJ.resolve())
    base = ["--project", PROJ, "--data-dir", dd]
    r = run("resolve_inputs.py", *base, "--session", "1")
    eq(r.returncode, 0, r.stderr + r.stdout)
    inp = json.loads((lay.session(1) / "inputs.json").read_text(encoding="utf-8"))
    eq(inp["profile"]["id"], "mini", "profile id (project profile by default)")
    eq(Path(inp["profile"]["path"]), pm.resolve(), "profile path")
    eq((inp["session"]["n"], inp["session"]["nn"], inp["session"]["title"], inp["session"]["duration_min"]),
       (1, "01", "Intro to Widgets", 60), "session")
    eq(inp["session"]["date"], None, "ambiguous date not guessed")
    truthy(any("ambiguous" in w for w in inp["warnings"]), f"ambiguity warning {inp['warnings']}")
    truthy(inp["transcript"]["path"].endswith("Intro_to_Widgets_-_Class.txt") and inp["transcript"]["bytes"] > 100, "transcript")
    truthy(inp["recording"].endswith(".mp4"), "recording")
    eq(inp["whisper"]["source"], "resources", "whisper")
    eq((inp["frames"]["count"], inp["frames"]["source"]), (2, "resources"), "frames")
    eq(inp["video_action"], "reuse", "auto reuses existing frames")
    eq(inp["slides"], [], "slides empty")
    eq(inp["classwork"]["files"], ["widget.cpp"], "classwork files (exe ignored)")
    eq(inp["instructor_code"]["dir"], "Lec-1", "instructor dir")
    eq(inp["instructor_code"]["repo"], "someone/widgets", "instructor repo")
    eq(inp["prior_notes"]["external"], [], "no prior notes before session 1")
    eq(inp["tiers"], ["basic", "advanced"], "tiers")
    P = inp["paths"]
    eq(P["basic_notes"], lay.notes("basic").as_posix(), "paths.basic_notes")
    eq(P["advanced_figures"], lay.figures("advanced").as_posix(), "paths.advanced_figures")
    eq(P["basic_pdf"], lay.pdf("basic").as_posix(), "paths.basic_pdf")
    eq(P["basic_meta"], lay.meta("basic").as_posix(), "paths.basic_meta")
    eq(P["advanced_build"], lay.build("advanced").as_posix(), "paths.advanced_build")
    eq(P["session"], lay.session(1).as_posix(), "paths.session")
    eq(P["session_video"], (lay.session(1) / "video").as_posix(), "video output dir")
    eq(P["digest"], (lay.session(1) / "digest.md").as_posix(), "digest")
    eq(P["reports"], lay.reports.as_posix(), "reports")
    eq(inp["project"], lay.root.as_posix(), "project")
    eq(inp["resources_dir"], (lay.root / "resources").as_posix(), "resources_dir")
    eq(inp["protect"], [(lay.root / "resources").as_posix(), (PROJ / "legacy-notes").resolve().as_posix(),
                        lay.root.as_posix()],
       "protect = resources + external prior notes")
    eq(sorted(Path(x).name for x in inp["allow"]), [".notes-plugin-internal", "advanced", "advanced", "basic", "basic"], "allow")
    truthy("repo_root" not in inp and "out_root" not in inp, "old keys gone")

    # discovery from cwd (walk up) and --profile override
    r = subprocess.run([UV, "run", "--quiet", "--script", str(SCRIPTS / "resolve_inputs.py"), "--session", "2", "--dry-run",
                        "--data-dir", str(dd)], cwd=str(PROJ / "resources"), capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    eq(r.returncode, 0, "cwd discovery: " + r.stderr)
    r = subprocess.run([UV, "run", "--quiet", "--script", str(SCRIPTS / "resolve_inputs.py"), "--session", "2", "--dry-run",
                        "--data-dir", str(dd)], cwd=str(WORK), capture_output=True, text=True,
                       encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    eq(r.returncode, 2, "no project -> exit 2")
    truthy("init" in r.stderr, r.stderr)
    r = run("resolve_inputs.py", *base, "--session", "1", "--profile", FIX / "profile-mini.md", "--dry-run")
    eq(r.returncode, 0, "--profile path override: " + r.stderr)

    # video modes
    def vid(mode, sess="1"):
        r = run("resolve_inputs.py", *base, "--session", sess, "--video", mode, "--dry-run")
        eq(r.returncode, 0, r.stderr)
        r = run("resolve_inputs.py", *base, "--session", sess, "--video", mode, "--json", WORK / "vid.json", "--dry-run")
        return json.loads((WORK / "vid.json").read_text(encoding="utf-8"))["video_action"]

    eq(vid("skip"), "skip", "video skip")
    eq(vid("run"), "run", "video run")
    eq(vid("frames-only"), "frames-only", "video frames-only")
    r = run("resolve_inputs.py", *base, "--session", "1", "--tiers", "basic", "--json", WORK / "tiers.json", "--dry-run")
    eq(json.loads((WORK / "tiers.json").read_text(encoding="utf-8"))["tiers"], ["basic"], "tiers=basic")

    # session 2: prior notes strictly earlier; unambiguous date from the profile; no recording/frames
    r = run("resolve_inputs.py", *base, "--session", "2", "--json", WORK / "s2.json", "--dry-run")
    eq(r.returncode, 0, r.stderr)
    s2 = json.loads((WORK / "s2.json").read_text(encoding="utf-8"))
    eq([Path(x).name for x in s2["prior_notes"]["external"]], ["01-widgets.md"], "prior notes < n only")
    eq((s2["recording"], s2["frames"], s2["video_action"], s2["classwork"]), (None, None, "skip", None), "no video")
    truthy(any("no keyframes" in w for w in s2["warnings"]), "warns about missing keyframes")
    eq(s2["session"]["date"], "2026-08-22", "date from profile")
    # a session-local video dir (pipeline output) is picked up when resources has none
    vdir = lay.session(2) / "video"
    (vdir / "frames").mkdir(parents=True, exist_ok=True)
    (vdir / "frames" / "index.md").write_text("# idx\n", encoding="utf-8")
    (vdir / "whisper.json").write_text("{}", encoding="utf-8")
    run("resolve_inputs.py", *base, "--session", "2", "--json", WORK / "s2v.json", "--dry-run")
    s2v = json.loads((WORK / "s2v.json").read_text(encoding="utf-8"))
    eq((s2v["frames"]["source"], s2v["whisper"]["source"], s2v["video_action"]), ("session", "session", "reuse"), "session video reused")

    eq(run("resolve_inputs.py", *base, "--session", "3", "--dry-run").returncode, 0, "no header still resolves")
    eq(run("resolve_inputs.py", *base, "--session", "4", "--dry-run").returncode, 2, "no transcript -> exit 2")
    # dry-run writes nothing; real run writes only under .notes-plugin-internal/work
    eq((lay.session(3) / "inputs.json").exists(), False, "dry-run wrote nothing")
    run("resolve_inputs.py", *base, "--session", "3")
    truthy((lay.session(3) / "inputs.json").is_file(), "inputs.json under .notes-plugin-internal/work/sessions/03")
    eq(run("resolve_inputs.py", "--project", WORK / "no-such-project", "--session", "1", "--dry-run").returncode, 2, "bad project")
    eq(run("resolve_inputs.py", *base, "--session", "1", "--profile", "nope-profile").returncode, 2, "unknown profile")
    # project's own earlier notes are discovered
    lay.notes("basic").mkdir(parents=True, exist_ok=True)
    (lay.notes("basic") / "01-first.md").write_text("# 01 \u00b7 First\n", encoding="utf-8")
    run("resolve_inputs.py", *base, "--session", "2", "--json", WORK / "s2b.json", "--dry-run")
    s2b = json.loads((WORK / "s2b.json").read_text(encoding="utf-8"))
    eq([Path(x).name for x in s2b["prior_notes"]["project"]["basic"]], ["01-first.md"], "project prior notes")
    # guard: resolve_inputs refuses to write inside protected resources/ (never does by construction), notes/ is fine
    guard.set_guard(dd, *lay.guard_roots(profile.load_profile(None, project=lay.root)))
    eq(run("resolve_inputs.py", *base, "--session", "1").returncode, 0, "guard allows .notes-plugin-internal/work")
    guard.clear_guard(dd)


# ---------------------------------------------------------------- fetch_code (local fake GitHub)
class _FakeGH(http.server.BaseHTTPRequestHandler):
    FILES = {
        "Lec-2/main.cpp": b"int add(int x, int y) { return x + y; }\n",
        "Lec-2/main": b"\x7fELF\x00\x00\x00binary",
        "Lec-2/main.o": b"\x00obj",
        "Lec-2/.demo.cpp.swp": b"swap",
        "Lec-2/big.txt": b"x" * 4096,
        "Lec-2/sub/notes.txt": b"nested\n",
        "Lec-1/a.cpp": b"outside\n",
    }
    hits: list[str] = []

    def log_message(self, *a):  # silence
        pass

    def _send(self, code, body, ctype="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _FakeGH.hits.append(self.path)
        if self.path.startswith("/repos/o/n/git/trees/"):
            tree = [{"path": "Lec-1", "type": "tree", "sha": "t1"}, {"path": "Lec-2", "type": "tree", "sha": "t2"},
                    {"path": "Lec-2/sub", "type": "tree", "sha": "t3"}]
            for p, b in self.FILES.items():
                tree.append({"path": p, "type": "blob", "sha": "s" + str(len(b)), "size": len(b)})
            self._send(200, json.dumps({"sha": "rootsha", "tree": tree, "truncated": False}).encode(), "application/json")
        elif self.path.startswith("/o/n/HEAD/"):
            from urllib.parse import unquote
            key = unquote(self.path[len("/o/n/HEAD/"):])
            if key in self.FILES:
                self._send(200, self.FILES[key])
            else:
                self._send(404, b"nope")
        else:
            self._send(404, b"nope")


@test
def t_fetch_code():
    srv = socketserver.TCPServer(("127.0.0.1", 0), _FakeGH)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{port}"
        out = WORK / "fc"
        r = run("fetch_code.py", "--repo", "o/n", "--dir", "Lec-2", "--out", out, "--max-kb", "1",
                "--api-base", base, "--raw-base", base)
        eq(r.returncode, 0, r.stderr + r.stdout)
        got = sorted(p.relative_to(out / "Lec-2").as_posix() for p in (out / "Lec-2").rglob("*") if p.is_file())
        eq(got, ["main.cpp", "sub/notes.txt"], "fetched files")
        eq((out / "Lec-2" / "main.cpp").read_bytes(), _FakeGH.FILES["Lec-2/main.cpp"], "bytes preserved")
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        eq(sorted(f["path"] for f in m["fetched"]), ["Lec-2/main.cpp", "Lec-2/sub/notes.txt"], "manifest fetched")
        reasons = {s["path"]: s["reason"] for s in m["skipped"]}
        truthy("binary" in reasons["Lec-2/main"], reasons)
        truthy("ignored" in reasons["Lec-2/main.o"], reasons)
        truthy("ignored" in reasons["Lec-2/.demo.cpp.swp"], reasons)
        truthy("oversize" in reasons["Lec-2/big.txt"], reasons)
        eq("Lec-1/a.cpp" in reasons, False, "other folders untouched")
        eq((m["tree_sha"], m["dir_sha"]), ("rootsha", "t2"), "tree shas")
        # extra ignore glob + a missing folder
        out2 = WORK / "fc2"
        r = run("fetch_code.py", "--repo", "o/n", "--dir", "Lec-2", "--out", out2, "--max-kb", "64", "--ignore", "*.txt",
                "--api-base", base, "--raw-base", base)
        eq(r.returncode, 0, r.stderr)
        eq(sorted(p.name for p in (out2 / "Lec-2").rglob("*") if p.is_file()), ["main.cpp"], "--ignore *.txt")
        r = run("fetch_code.py", "--repo", "o/n", "--dir", "Lec-9", "--out", WORK / "fc3", "--api-base", base, "--raw-base", base)
        eq(r.returncode, 1, "missing folder -> exit 1")
        truthy("Lec-1" in r.stderr and "Lec-2" in r.stderr, "lists available folders: " + r.stderr)
        make_mini_profile()
        r = run("fetch_code.py", "--repo", "o/n", "--dir", "Lec-2", "--session", "2", "--project", PROJ, "--max-kb", "1",
                "--api-base", base, "--raw-base", base)
        eq(r.returncode, 0, r.stderr)
        truthy((layout.Layout(PROJ).session(2) / "code" / "Lec-2" / "main.cpp").is_file(), "fetch_code default out")
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- extract_pdf
@test
def t_extract_pdf():
    import pymupdf

    pdf = WORK / "deck.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 100), f"Slide {i + 1}: order book basics", fontsize=18)
    doc.set_toc([[1, "Intro", 1], [1, "Details", 3]])
    doc.save(str(pdf))
    doc.close()
    out = WORK / "pdfout"
    r = run("extract_pdf.py", pdf, "--out", out, "--png", "--dpi", "50", "--pages", "1-2")
    eq(r.returncode, 0, r.stderr + r.stdout)
    eq(sorted(p.name for p in (out / "pages").iterdir()), ["001.png", "001.txt", "002.png", "002.txt"], "page files")
    truthy("Slide 1" in (out / "pages" / "001.txt").read_text(encoding="utf-8"), "text extracted")
    eq((out / "pages" / "001.png").read_bytes()[:4], b"\x89PNG", "png")
    import struct
    wh = lambda f: struct.unpack(">II", f.read_bytes()[16:24])[0]  # noqa: E731  (PNG IHDR width)
    truthy((out / "hires" / "001.png").is_file() and (out / "hires" / "002.png").is_file(), "hires pages written")
    eq(sorted(p.name for p in (out / "hires").iterdir()), ["001.png", "002.png"], "hires holds only the png pages")
    truthy(wh(out / "hires" / "001.png") > 3 * wh(out / "pages" / "001.png"), "hires (200 dpi) is wider than 50 dpi")
    eq(wh(out / "hires" / "001.png"), round(595.0 * 200 / 72), "hires width = A4 at 200 dpi")
    out_h = WORK / "pdfout-hd"
    r = run("extract_pdf.py", pdf, "--out", out_h, "--png", "--dpi", "50", "--hires-dpi", "120", "--pages", "1")
    eq(r.returncode, 0, r.stderr)
    eq(wh(out_h / "hires" / "001.png"), round(595.0 * 120 / 72), "--hires-dpi 120")
    out_n = WORK / "pdfout-nohd"
    r = run("extract_pdf.py", pdf, "--out", out_n, "--png", "--dpi", "50", "--hires-dpi", "0", "--pages", "1")
    truthy(r.returncode == 0 and not (out_n / "hires").exists(), "--hires-dpi 0 writes no hires dir")
    o = json.loads((out / "outline.json").read_text(encoding="utf-8"))
    eq([e["title"] for e in o["outline"]], ["Intro", "Details"], "outline")
    eq(o["pages_total"], 3, "pages_total")
    out2 = WORK / "pdfout2"
    r = run("extract_pdf.py", pdf, "--out", out2, "--pages", "3-")
    eq(sorted(p.name for p in (out2 / "pages").iterdir()), ["003.txt"], "open-ended range, no png")
    eq(run("extract_pdf.py", WORK / "nope.pdf", "--out", WORK / "pdfout3").returncode, 2, "missing pdf")
    # default out: <session>/slides/<stem>
    make_mini_profile()
    r = run("extract_pdf.py", pdf, "--session", "2", "--project", PROJ)
    eq(r.returncode, 0, r.stderr)
    truthy((layout.Layout(PROJ).session(2) / "slides" / "deck" / "pages" / "001.txt").is_file(), "extract_pdf default out")
    eq(run("extract_pdf.py", pdf).returncode, 2, "no out, no session")


def _office(path: Path, files: dict) -> Path:
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return path


@test
def t_office_and_handouts():
    ns_w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    ns_a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    docx = _office(WORK / "h.docx", {
        "word/document.xml": f"<w:document {ns_w}><w:body>"
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Order Book</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr/></w:pPr><w:r><w:t>bids</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>px</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>qty</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        "</w:body></w:document>",
        "word/media/image1.png": b"PNGDATA"})
    r = run("extract_pdf.py", docx, "--out", WORK / "dx", "--png")
    eq(r.returncode, 0, r.stderr)
    t = (WORK / "dx" / "pages" / "001.txt").read_text(encoding="utf-8")
    truthy("# Order Book" in t and "- bids" in t and "px	qty" in t, t)
    truthy((WORK / "dx" / "media" / "image1.png").is_file() and "no-op" in r.stderr, "media + png warning")
    sl = lambda n, x: f"<p:sld xmlns:p='x' {ns_a}><a:p><a:r><a:t>{x}</a:t></a:r></a:p></p:sld>"
    pptx = _office(WORK / "h.pptx", {"ppt/slides/slide10.xml": sl(10, "ten"), "ppt/slides/slide2.xml": sl(2, "two"),
        "ppt/notesSlides/notesSlide2.xml": f"<p:notes xmlns:p='x' {ns_a}><a:p><a:r><a:t>say hi</a:t></a:r></a:p></p:notes>"})
    r = run("extract_pdf.py", pptx, "--out", WORK / "px", "--json", WORK / "px.json")
    eq(r.returncode, 0, r.stderr)
    eq((WORK / "px" / "pages" / "001.txt").read_text(encoding="utf-8").split(), ["two", "[notes]", "say", "hi"], "slide order+notes")
    eq((WORK / "px" / "pages" / "002.txt").read_text(encoding="utf-8").strip(), "ten", "numeric order")
    eq(json.loads((WORK / "px.json").read_text(encoding="utf-8"))["pages_total"], 2, "json")
    # resolve_inputs: kinds + handouts
    pm = make_mini_profile()
    proj = WORK / "proj-h"
    shutil.copytree(PROJ, proj)
    shutil.copy(pptx, proj / "resources" / "L1.pptx")
    shutil.copy(docx, proj / "resources" / "Handout.docx")
    pm2 = proj / ".notes-plugin-internal" / "profile.md"
    pm2.write_text(pm.read_text(encoding="utf-8").replace("  slides:", "  handouts: Handout.docx" + chr(10) + "  slides:")
                   .replace('["L{n}.pdf", ', '["L{n}.pdf", "L{n}.pptx", '), encoding="utf-8")
    r = run("resolve_inputs.py", "--project", proj, "--session", "1", "--json", WORK / "ih.json", "--dry-run")
    eq(r.returncode, 0, r.stderr)
    d = json.loads((WORK / "ih.json").read_text(encoding="utf-8"))
    eq([x["kind"] for x in d["slides_detail"]], ["pptx"], "slides kind")
    eq([(Path(x["path"]).name, x["kind"]) for x in d["handouts"]], [("Handout.docx", "docx")], "handouts")
    eq(len(d["slides"]), 1, "slides stay path strings")


# ---------------------------------------------------------------- doctor / install_chromium
@test
def t_doctor():
    dd = WORK / "doc-data"
    out = WORK / "doctor.json"
    r = run("doctor.py", "--data-dir", dd, "--json", out, "--no-network")
    truthy(r.returncode in (0, 1), r.stderr)
    d = json.loads(out.read_text(encoding="utf-8"))
    for k in ("os", "arch", "uv", "chrome", "gpu", "whisper_fast", "ocr_accel", "network", "compilers", "assets", "guard", "project"):
        truthy(k in d, f"doctor key {k}")
    for k in ("path", "ok", "hint"):
        truthy(k in d["chrome"], f"chrome.{k}")
    for k in ("nvidia", "name", "apple_silicon"):
        truthy(k in d["gpu"], f"gpu.{k}")
    truthy("g++" in d["compilers"] and "clang++" in d["compilers"], "compilers")
    truthy("katex" in d["assets"] and "mermaid" in d["assets"], "assets")
    eq(r.returncode, 0 if d["chrome"]["ok"] else 1, "exit code reflects chrome only")
    make_mini_profile()
    out_p = WORK / "doctor-proj.json"
    run("doctor.py", "--data-dir", dd, "--json", out_p, "--no-network", "--project", PROJ)
    dp = json.loads(out_p.read_text(encoding="utf-8"))["project"]
    eq((dp["found"], dp["profile_ok"], dp["id"], dp["resources_exists"]), (True, True, "mini", True), "doctor project")
    eq(dp["prior_notes_external"], [(PROJ / "legacy-notes").as_posix()], "doctor external prior notes")
    if not d["gpu"]["nvidia"] and d["os"] in ("windows", "linux"):
        truthy("switch the NVIDIA GPU on" in r.stdout, "GPU advice printed")
    # an explicit chrome-path.txt in the data dir is honoured
    fake = WORK / "fakechrome" / ("chrome.exe" if os.name == "nt" else "chrome")
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("x")
    (dd / "chrome-path.txt").write_text(str(fake) + "\n")
    out2 = WORK / "doctor2.json"
    run("doctor.py", "--data-dir", dd, "--json", out2, "--no-network", env={"LECTURE_NOTES_CHROME": ""})
    d2 = json.loads(out2.read_text(encoding="utf-8"))
    truthy(d2["chrome"]["ok"], "chrome ok")


@test
def t_install_chromium_help():
    # Only the CLI surface: the real install is a ~150 MB download and is not exercised here.
    r = run("install_chromium.py", "--help")
    eq(r.returncode, 0, r.stderr[-500:])
    truthy("--no-install" in r.stdout, "help text")


# ---------------------------------------------------------------- runner
def main() -> int:
    global FAST
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="run scripts with this interpreter instead of `uv run`")
    ap.add_argument("-k", default="", help="only tests whose name contains this")
    a = ap.parse_args()
    FAST = a.fast
    rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    tests = [(n, f) for n, f in sorted(globals().items()) if getattr(f, "_is_test", False) and a.k in n]
    for name, fn in tests:
        t0 = time.time()
        try:
            fn()
            RESULTS.append((name, True, ""))
            print(f"PASS  {name}  ({time.time() - t0:.1f}s)")
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc(limit=6)
            RESULTS.append((name, False, f"{e}"))
            print(f"FAIL  {name}  ({time.time() - t0:.1f}s)\n      {e}\n{tb}")
    bad = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} passed" + (f"; FAILED: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
