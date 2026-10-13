# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pillow", "numpy", "pymupdf"]
# ///
"""Self-test: line-ending agnostic (CRLF, LF, lone CR, BOM) reads, normalised fingerprints, LF writes, .gitattributes.

    uv run tests/selftest_eol.py [-k SUBSTRING]

Everything is built at test time under <plugin>/test-output/_e1/ from short synthetic text.
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
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
WORK = PLUGIN / "test-output" / "_e1"
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv"))
sys.path.insert(0, str(SCRIPTS))

from notes_lib import ledger, layout, notefile, pending, profile as prof, transcript as tr  # noqa: E402
import class_figure  # noqa: E402
import sweep_check  # noqa: E402

TESTS = []
CRLF, LF, CR = "crlf", "lf", "cr"


def test(fn):
    TESTS.append(fn)
    return fn


def eq(got, want, label=""):
    assert got == want, f"{label}: got {got!r}, want {want!r}"


def ok(cond, label=""):
    assert cond, label


def rmtree(p: Path):
    def onerr(func, path, _):
        os.chmod(path, stat.S_IWRITE)
        func(path)
    if p.exists():
        shutil.rmtree(p, onerror=onerr)


def variants(text: str) -> dict[str, bytes]:
    """The same text as LF, CRLF, lone CR, and LF with a UTF-8 BOM."""
    lf = text.replace("\r\n", "\n")
    return {LF: lf.encode(), CRLF: lf.replace("\n", "\r\n").encode(), CR: lf.replace("\n", "\r").encode(),
            "bom": b"\xef\xbb\xbf" + lf.encode()}


def put(dirpath: Path, name: str, data: bytes) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / name
    p.write_bytes(data)
    return p


def run(script, *args, timeout=300):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env.pop("LECTURE_NOTES_DATA", None)
    return subprocess.run([UV, "run", "--quiet", "--script", str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


TRANSCRIPT = ("Course Name: Widget Engineering\nLecture Title: Intro to Widgets\nStart Time: 3/4/2026, 9:30:00 AM\n"
              "Duration: 60 minutes\nModel use: test\n\n==================================================\n\n"
              "So today we talk about widgets and the free list.\nThen the pool.\n")

PROFILE = ('---\nschema: 1\nid: t\ntitle: "Test Course"\nshort: "T"\nprefix: TT\nsessions:\n'
           '  - {n: 1, title: "Intro to Widgets", stem: "Intro_to_Widgets_-_Class", date: 2026-03-04}\n---\n\n# body\n\n'
           'Some prose.\n')

NOTE = ('---\nprofile: t\nsession: 1\ntier: basic\ntitle: "Widgets"\nslug: widgets\ndate: 2026-03-04\n'
        'sources: ["Intro_to_Widgets_-_Class.txt"]\nverified_as_of: 2026-10-01\n---\n\n# TT-01: Widgets\n\n'
        '## TL;DR\n\n- one\n\n<!-- sweep:s1 -->\n> **Builds on:** session 0.\n<!-- /sweep:s1 -->\n\n'
        '```text\nline <!-- sweep:fenced -->\n```\n\nText with <!-- pending-ref:2 -->see session 2<!-- /pending-ref:2 --> here.\n')

VISUALS = ('visuals:\n  - {id: V1, frame: F001, box: [0.1, 0.1, 0.9, 0.9], caption: "a board"}\n'
           '  - {id: V2, frame: F002, caption: "another"}\n')


@test
def t_transcript_header():
    want = tr.parse_header(TRANSCRIPT.replace("\r\n", "\n"))
    ok(want[0]["has_header"] and want[0]["lecture_title"] == "Intro to Widgets", "baseline header")
    for k, data in variants(TRANSCRIPT).items():
        got = tr.parse_header(data.decode("utf-8-sig", "replace"))
        eq(got, want, f"parse_header({k}, str)")
        # discover reads the head as bytes: CRLF / lone CR / BOM must classify the same
        p = put(WORK / "tr", f"Intro_to_Widgets_-_Class_{k}.txt", data)
        from notes_lib import discover
        head = discover._read_head(p)
        ok(tr.RULE_RE.search(head), f"discover head rule ({k})")
        hdr, _ = tr.parse_header(head + "\n")
        eq(hdr["lecture_title"], "Intro to Widgets", f"discover header title ({k})")
        kind, ex = discover._classify_file(p, p.name, {}, [])
        eq(kind, "transcript", f"discover kind ({k})")


@test
def t_profile_and_note_and_visuals():
    base = {}
    for k, data in variants(PROFILE).items():
        root = WORK / f"prof-{k}"
        rmtree(root)
        put(root / ".notes-plugin-internal", "profile.md", data)
        (root / "resources").mkdir()
        p = prof.load_profile(None, data_dir=WORK / "dd", project=root)
        d = {x: y for x, y in p.items() if not x.startswith("_")}
        base.setdefault("profile", d)
        eq(d, base["profile"], f"profile {k}")
        eq(p["_body"].strip(), "# body\n\nSome prose.", f"profile body {k}")
    notes = {}
    for k, data in variants(NOTE).items():
        f = put(WORK / f"note-{k}", "01-widgets.md", data)
        n = notefile.parse_note(f)
        sig = (n.meta, n.body.strip(), n.h1)
        notes.setdefault("n", sig)
        eq(sig, notes["n"], f"note {k}")
        text = f.read_text(encoding="utf-8-sig")
        seen, probs = sweep_check.scan_text(text)
        notes.setdefault("sweep", (seen, probs))
        eq((seen, probs), notes["sweep"], f"sweep markers {k}")
        eq(sorted(seen), ["s1"], f"sweep ids {k} (the fenced one is ignored)")
        # byte-level text API too: scan_text/pending.scan accept CRLF and lone CR text directly
        raw_text = data.decode("utf-8-sig")
        eq(sweep_check.scan_text(raw_text), notes["sweep"], f"scan_text raw {k}")
        sc = pending.scan(raw_text)
        notes.setdefault("pending", json.dumps(sc, sort_keys=True, default=str))
        eq(json.dumps(sc, sort_keys=True, default=str), notes["pending"], f"pending.scan {k}")
    vis = {}
    for k, data in variants(VISUALS).items():
        f = put(WORK / f"vis-{k}", "visuals.yaml", data)
        rows = class_figure.load_rows(f)
        vis.setdefault("rows", rows)
        eq(rows, vis["rows"], f"visuals yaml {k}")
        eq([r["id"] for r in rows], ["V1", "V2"], "visual ids")


@test
def t_fingerprint_normalised_and_backward_compatible():
    fps = {}
    for k, data in variants(TRANSCRIPT).items():
        f = put(WORK / f"fp-{k}", "Intro_to_Widgets_-_Class.txt", data)
        fps[k] = ledger.file_fp(f)
    eq({k: v["sha256"] for k, v in fps.items()}, {k: fps[LF]["sha256"] for k in fps}, "same normalised sha for all variants")
    ok("raw_sha256" not in fps[LF], "LF file: raw == normalised, nothing extra stored")
    ok(fps[CRLF]["raw_sha256"] != fps[CRLF]["sha256"], "CRLF file remembers its raw hash")
    eq(ledger.sha256_norm(WORK / "fp-crlf" / "Intro_to_Widgets_-_Class.txt"),
       ledger.sha256_norm(WORK / "fp-cr" / "Intro_to_Widgets_-_Class.txt"), "CR == CRLF")
    # inputs_hash is identical for CRLF and LF
    def fpset(k):
        return {"transcript": fps[k]}
    eq(ledger.inputs_hash(fpset(CRLF)), ledger.inputs_hash(fpset(LF)), "inputs_hash")
    # binaries are hashed raw (a PDF with CRLF bytes inside is a different file)
    b1 = put(WORK / "fp-bin", "a.pdf", b"%PDF-1.4\r\nx\r\n")
    b2 = put(WORK / "fp-bin", "b.pdf", b"%PDF-1.4\nx\n")
    ok(ledger.file_fp(b1)["sha256"] != ledger.file_fp(b2)["sha256"], "binary not normalised")
    # a chunk-boundary CR/LF pair and a BOM only change nothing
    big = ("x" * 70 + "\r\n") * 30000
    eq(ledger.sha256_norm(put(WORK / "fp-big", "t.txt", big.encode())),
       ledger.sha256_norm(put(WORK / "fp-big", "u.txt", big.replace("\r\n", "\n").encode())), "big file")

    # backward compatible: a ledger written with the OLD raw hash stays up to date, and is upgraded silently
    crlf_file = WORK / "fp-crlf" / "Intro_to_Widgets_-_Class.txt"
    old = {"transcript": {"path": crlf_file.as_posix(), "size": 1, "mtime_ns": 1,
                          "sha256": ledger.sha256_file(crlf_file)}}          # what the old code stored: raw bytes
    new = {"transcript": ledger.file_fp(crlf_file)}
    reasons, trig = ledger.diff(old, new)
    eq((reasons, trig), ([], False), "legacy raw hash accepted")
    ok(ledger.upgrade(old, new), "upgrade reports a change")
    eq(old["transcript"]["sha256"], new["transcript"]["sha256"], "upgraded to the normalised hash")
    eq(ledger.diff(old, new), ([], False), "still up to date after the upgrade")
    ok(not ledger.upgrade(old, new), "second upgrade is a no-op")
    # a genuinely changed file is still detected, with either kind of old hash
    changed = put(WORK / "fp-chg", "Intro_to_Widgets_-_Class.txt", (TRANSCRIPT + "one more line\n").replace("\n", "\r\n").encode())
    n2 = {"transcript": ledger.file_fp(changed)}
    eq(ledger.diff({"transcript": {"path": "x", "sha256": ledger.sha256_file(crlf_file)}}, n2)[1], True, "changed (legacy)")
    eq(ledger.diff(new, n2)[1], True, "changed (normalised)")
    # slides/handouts lists and classwork follow the same rule
    cw = WORK / "fp-cw"
    put(cw, "main.cpp", b"int main() {\r\n  return 0;\r\n}\r\n")
    inputs = {"classwork": {"dir": cw.as_posix(), "files": ["main.cpp"]}}
    fpc = ledger.fingerprints(inputs)
    legacy_files = {"main.cpp": ledger.sha256_file(cw / "main.cpp")}
    legacy = {"classwork": {"dir": cw.as_posix(), "count": 1, "files": legacy_files, "sha256": "legacy"}}
    eq(ledger.diff(legacy, fpc), ([], False), "classwork legacy raw hash accepted")
    ok(ledger.upgrade(legacy, fpc), "classwork upgraded")
    eq(ledger.diff(legacy, fpc), ([], False), "classwork stays up to date")
    sl = put(WORK / "fp-sl", "L1.md", b"# slides\r\n")
    olds = {"slides": [{"path": sl.as_posix(), "sha256": ledger.sha256_file(sl)}]}
    news = ledger.fingerprints({"slides": [sl.as_posix()]})
    eq(ledger.diff(olds, news), ([], False), "slides legacy accepted")


@test
def t_plan_updates_does_not_regenerate_crlf_checkout():
    """A ledger recorded (old format, raw hash) from a CRLF checkout stays up-to-date on LF and CRLF."""
    for k in (CRLF, LF):
        root = WORK / f"plan-{k}"
        rmtree(root)
        put(root / ".notes-plugin-internal", "profile.md", PROFILE.encode())
        res = root / "resources"
        crlf_bytes = variants(TRANSCRIPT)[CRLF]
        t = put(res, "Intro_to_Widgets_-_Class.txt", crlf_bytes)
        # a built note + pdf so the session counts as generated
        put(root / "notes" / "basic", "01-widgets.md", NOTE.encode())
        put(root / "pdf" / "basic", "TT-01-widgets-basic.pdf", b"%PDF-1.4\n")
        put(root / "notes" / "advanced", "01-widgets.md", NOTE.replace("tier: basic", "tier: advanced").encode())
        put(root / "pdf" / "advanced", "TT-01-widgets-advanced.pdf", b"%PDF-1.4\n")
        raw = ledger.sha256_file(t)
        fp = {"path": t.as_posix(), "size": t.stat().st_size, "mtime_ns": t.stat().st_mtime_ns, "sha256": raw}
        ent = {"n": 1, "title": "Intro to Widgets", "slug": "widgets", "inputs": {"transcript": fp}, "inputs_hash": "old",
               "tiers": {tt: {"note": f"notes/{tt}/01-widgets.md", "pdf": f"pdf/{tt}/TT-01-widgets-{tt}.pdf", "status": "ok",
                              "inputs": {"transcript": fp}, "inputs_hash": "old"} for tt in ("basic", "advanced")}}
        state = {"schema": 1, "plugin_version": "0", "updated": "x", "course_docs": {}, "sessions": {"01": ent}}
        (root / ".notes-plugin-internal" / "state.json").write_text(json.dumps(state), encoding="utf-8", newline="\n")
        if k == LF:
            # the same transcript checked out with LF endings on another OS
            t.write_bytes(variants(TRANSCRIPT)[LF])
        out = WORK / f"plan-{k}.json"
        r = run("plan_updates.py", "--project", root, "--data-dir", WORK / "dd", "--json", out, "--no-write-sessions")
        ok(r.returncode == 0, r.stdout + r.stderr)
        plan = json.loads(out.read_text(encoding="utf-8"))
        row = plan["sessions"][0]
        eq(row["status"], "up-to-date", f"{k}: status ({row['reasons']})")
        saved = json.loads((root / ".notes-plugin-internal" / "state.json").read_text(encoding="utf-8"))
        want = ledger.sha256_norm(t)
        eq(saved["sessions"]["01"]["tiers"]["basic"]["inputs"]["transcript"]["sha256"], want, f"{k}: ledger upgraded silently")
        # and a real edit is still seen
        t.write_bytes(variants(TRANSCRIPT + "A new sentence.\n")[CRLF])
        r = run("plan_updates.py", "--project", root, "--data-dir", WORK / "dd", "--json", out, "--no-write-sessions")
        eq(json.loads(out.read_text(encoding="utf-8"))["sessions"][0]["status"], "changed", f"{k}: real edit detected")


@test
def t_init_writes_gitattributes_idempotently_and_lf_only():
    root = WORK / "init"
    rmtree(root)
    root.mkdir(parents=True)
    r = run("init_project.py", "--project", root, "--profile", "_template", "--data-dir", WORK / "dd")
    ok(r.returncode == 0, r.stdout + r.stderr)
    ga = root / ".gitattributes"
    ok(ga.is_file(), ".gitattributes written")
    text = ga.read_bytes().decode()
    ok("\r" not in text, "LF only")
    for line in layout.GITATTRIBUTES_LINES:
        ok(line in text.splitlines(), line)
    ok("notes/**/*.md text=auto eol=lf" in text and "*.pdf binary" in text, "required lines")
    # idempotent: a second init (--force) and ensure_gitattributes add nothing
    before = ga.read_bytes()
    eq(layout.ensure_gitattributes(root), [], "no-op")
    r = run("init_project.py", "--project", root, "--profile", "_template", "--data-dir", WORK / "dd", "--force")
    ok(r.returncode == 0, r.stdout + r.stderr)
    eq(ga.read_bytes(), before, "unchanged after a re-run")
    # merge without clobbering a user's own lines (CRLF file, one of our lines already present)
    root2 = WORK / "init2"
    rmtree(root2)
    root2.mkdir(parents=True)
    (root2 / ".gitattributes").write_bytes(b"*.psd filter=lfs\r\n*.pdf binary\r\n# mine\r\n")
    msgs = layout.ensure_gitattributes(root2)
    ok(msgs and "updated" in msgs[0], msgs)
    lines = (root2 / ".gitattributes").read_text(encoding="utf-8").splitlines()
    ok(lines[:3] == ["*.psd filter=lfs", "*.pdf binary", "# mine"], lines)
    eq(lines.count("*.pdf binary"), 1, "existing line not duplicated")
    ok(all(x in lines for x in layout.GITATTRIBUTES_LINES), "all plugin lines present")
    ok(b"\r" not in (root2 / ".gitattributes").read_bytes(), "rewritten with LF")
    eq(layout.ensure_gitattributes(root2), [], "second merge is a no-op")
    # legacy migration path gives an existing project the file too
    root3 = WORK / "init3"
    rmtree(root3)
    put(root3 / ".notes", "profile.md", PROFILE.encode())
    msgs = layout.migrate_legacy(root3)
    ok(any("migrated" in m for m in msgs) and any(".gitattributes" in m for m in msgs), msgs)
    ok((root3 / ".gitattributes").is_file(), "migration wrote .gitattributes")


@test
def t_all_text_writes_are_lf():
    """The scripts' own files: no write_text/open('w') without newline='\\n' (static check) and an LF write round-trip."""
    import re
    bad = []
    for p in SCRIPTS.rglob("*.py"):
        if ".mypy_cache" in p.parts:
            continue
        src = p.read_text(encoding="utf-8-sig")
        for m in re.finditer(r"write_text\((?:[^()]|\([^()]*(?:\([^()]*\)[^()]*)*\))*\)", src):
            if "newline=" not in m.group(0):
                bad.append(f"{p.relative_to(SCRIPTS).as_posix()}: {m.group(0)[:60]!r}")
        for m in re.finditer(r"open\([^\n]*,\s*[\"'][wa][\"'][^\n]*\)", src):
            if "newline=" not in m.group(0) and "encoding" in m.group(0):
                bad.append(f"{p.relative_to(SCRIPTS).as_posix()}: {m.group(0)[:60]!r}")
    # render/assets caches vendor JS/CSS (not user text); everything else must be LF
    bad = [b for b in bad if not b.startswith("render/assets.py")]
    eq(bad, [], "writes without newline=")
    from notes_lib import platform as plat
    p = WORK / "wj" / "x.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    plat.write_json(p, {"a": 1})
    ok(b"\r" not in p.read_bytes(), "write_json is LF")


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
