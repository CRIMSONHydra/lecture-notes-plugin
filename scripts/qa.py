# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""One-shot, deterministic QA for a project tier: lint, stats, numbers, snippets, citations, figures,
index, PDF build, PDF audit. Every check runs as its own ``uv run`` subprocess; this script only
orchestrates and classifies.

    uv run qa.py --tier basic|advanced [--project DIR] [--profile REF] [--notes 02 02a ...]
                 [--digest D] [--research R] [--data-dir X] [--skip-build] [--legacy] [--json OUT]
                 [--no-prune] [--no-rollup] [--fast]
                 [--src DIR --pdf-dir DIR --build-dir DIR --meta-dir DIR]   (overrides, for tests)

Layout (``--project`` defaults to the project above cwd): read ``<project>/notes/<tier>/NN[a]-*.md`` and
``.notes-plugin-internal/work/meta/<tier>/<stem>.{coverage.json,checks.py}``; written: ``notes/<tier>/README.md`` (update_index),
``.notes-plugin-internal/work/build/<tier>/`` (snippets, qa logs, build_pdf work), ``pdf/<tier>/`` (build_pdf). Defaults when
not given: digest ``.notes-plugin-internal/work/sessions/NN/digest.md``, research ``.../research.md``, JSON report
``.notes-plugin-internal/work/reports/qa-<tier>[-NN].json``.

``--fast`` (PLAN 14.1, the inner fix loop, < 10 s): lint (which already flags code lines > 95/120 cols, wide
tables, missing figures, placeholders, ...) + stats + figure-file + visuals/coverage checks only. Skipped:
snippets, citations, index, PDF build and audit; verify_numbers runs only when a ``*.checks.py`` changed since
the last numbers run (hash kept in ``work/build/<tier>/qa/numbers.sha.json``). Report ``fast: true``;
``ok`` from a fast run does NOT replace the final full run. Full runs reuse the snippet cache
(``work/build/<tier>/snippets/cache.json``): unchanged snippets are not recompiled.

Research for citations: ``--research`` (one or more files), else the session's ``research-verify.md`` +
``research-deep.md`` (union of their section G), else a single ``research.md``.

Ongoing use (PLAN 13): the PDF build runs with ``--prune`` (stale PDFs of renamed/removed notes and
unreferenced ``NN-*`` figures are deleted; ``--no-prune`` disables), the tier is checked for two notes with the
same session number (``dup-session`` failure), and ``--no-rollup`` (batch mode, ``/notes:update``) skips the
index rebuild and the compendium: only the per-note PDFs of the selected notes are built, and the caller
rebuilds ``update_index.py`` + ``build_pdf.py --compendium-only --prune`` once at the end.

Scope: with ``--notes`` every check looks only at the named notes' own markdown, figures and PDFs. The PDF audit
is restricted to the PDFs this run built (``audit_pdf.py --only``, entries from this run's own build report, not
the shared ``manifest.json`` that parallel sessions rewrite), and ``build.pdfs`` lists only those. A foreign or
half-written PDF, the compendium or the index in the same ``pdf/<tier>/`` folder can never fail a per-note run.
Folder-wide checks (every PDF in the folder, compendium, index) run only when the rollup runs, i.e. without
``--no-rollup``, after the rollup has built them.

JSON (also printed as a summary)::

  {tier, ok, profile, src, notes:[{stem, num, lint:{errors,warnings,issues[]}, stats:{..., budget, status},
    numbers:{status,checks,failures[]}, snippets:{pass,fail,skipped,results[]}, citations:{status,...},
    figures:{referenced,present,missing[],unreferenced[]}, coverage:{...}|null}],
   index:{ok,...}, build:{ran,ok,pdfs[],compendium,...}, audit:{ran,problems,details[],warnings},
   mechanical_failures:[{check,code,note,line,msg}], content_failures:[...], warnings:[...], steps:[...]}

Mechanical = fixable by reformatting (section sign, wide code/tables, bad or missing figure paths,
placeholders, hand-numbered headings, PDF overflow/raw markdown, build errors). Content = needs the writer
(budget, coverage, citations, numbers, snippet mismatches, missing sections, front matter values).
Exit 0 iff there are no failures (warnings never fail).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, layout, notefile, platform as plat, profile as prof  # noqa: E402

plat.utf8_stdio()

NOTE_FILE_RE = re.compile(r"^(\d+[a-z]?)-.+\.md$")
IMG_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
FIG_RE = re.compile(r"figures/[^/\\]+\.(?:png|jpe?g)", re.I)
STEPS: list[dict] = []


def find_uv() -> str:
    uv = shutil.which("uv")
    if uv:
        return uv
    cand = Path.home() / ".local" / "bin" / ("uv.exe" if sys.platform.startswith("win") else "uv")
    if cand.exists():
        return str(cand)
    sys.exit("qa: `uv` not found on PATH (https://docs.astral.sh/uv/)")


UV = ""


def run_script(name: str, args: list, *, timeout: int = 900) -> subprocess.CompletedProcess:
    """`uv run --script scripts/<name> args...` (captured, utf-8). Records a step."""
    cmd = [UV, "run", "--quiet", "--script", str(HERE / name), *map(str, args)]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        r = subprocess.CompletedProcess(cmd, 124, e.stdout or "", f"timeout after {timeout}s")
    STEPS.append({"step": name, "returncode": r.returncode, "seconds": round(time.time() - t0, 1)})
    return r


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def tail(s: str, n: int = 12) -> str:
    lines = [ln for ln in (s or "").strip().splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def collect(obj, key: str) -> list[str]:
    """Every value under ``key`` anywhere in a JSON tree, as strings (lists flattened, ints as counts)."""
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                if isinstance(v, list):
                    out += [i if isinstance(i, str) else json.dumps(i, ensure_ascii=False) for i in v]
                elif isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                    out.append(f"{int(v)} {key}")
                elif isinstance(v, str) and v:
                    out.append(v)
            else:
                out += collect(v, key)
    elif isinstance(obj, list):
        for i in obj:
            out += collect(i, key)
    return out


def main(argv=None) -> int:
    global UV
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", default=None, help="project dir (default: discovered from cwd)")
    ap.add_argument("--profile", default=None, help="profile id/path (default: <project>/.notes-plugin-internal/profile.md)")
    ap.add_argument("--tier", required=True, choices=["basic", "advanced"])
    ap.add_argument("--src", default=None, help="notes dir override")
    ap.add_argument("--pdf-dir", default=None, help="PDF dir override")
    ap.add_argument("--build-dir", default=None, help="work dir override")
    ap.add_argument("--meta-dir", default=None, help="meta dir override")
    ap.add_argument("--notes", nargs="*", default=None, help="note numbers to check (02 02a); default all")
    ap.add_argument("--digest", default=None)
    ap.add_argument("--research", nargs="+", default=None,
                    help="research file(s); default: session research-verify.md + research-deep.md, else research.md")
    ap.add_argument("--fast", action="store_true", help="lint + stats + figures + coverage only (< 10 s)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--skip-build", action="store_true", help="do not run build_pdf / audit_pdf")
    ap.add_argument("--legacy", action="store_true", help="notes predate the front-matter contract")
    ap.add_argument("--no-prune", action="store_true", help="do not delete stale PDFs / unreferenced figures")
    ap.add_argument("--no-rollup", action="store_true",
                    help="batch mode: skip the index and compendium rebuild (per-note PDFs only)")
    ap.add_argument("--json", default=None, help="report path (default <project>/.notes-plugin-internal/work/reports/qa-<tier>[-NN].json)")
    a = ap.parse_args(argv)
    UV = find_uv()
    t_start = time.time()

    explicit = all((a.src, a.pdf_dir, a.build_dir, a.meta_dir)) and a.json
    try:
        lay = None if explicit and not a.project else layout.Layout.for_project(a.project)
    except layout.ProjectError as e:
        print(f"qa: {e}", file=sys.stderr)
        return 2
    profile = prof.load_or_exit(a.profile, data_dir=a.data_dir, project=lay.root if lay else None)
    cfg = prof.tier_cfg(profile, a.tier)
    src = plat.norm(a.src) if a.src else lay.notes(a.tier)
    pdfdir = plat.norm(a.pdf_dir) if a.pdf_dir else lay.pdf(a.tier)
    build = plat.norm(a.build_dir) if a.build_dir else lay.build(a.tier)
    meta = plat.norm(a.meta_dir) if a.meta_dir else lay.meta(a.tier)
    if a.json:
        outj = plat.norm(a.json)
    else:
        one = [re.sub(r"\D", "", t)[:2].zfill(2) for t in (a.notes or [])]
        suffix = f"-{one[0]}" if len(set(one)) == 1 and one else ""
        outj = lay.reports / f"qa-{a.tier}{suffix}.json"
    guard.assert_writable(outj)
    guard.assert_writable(build)
    qa_root = build / "qa"
    # per-note runs get their own scratch dir: parallel `--notes NN` runs of one tier must not overwrite each
    # other's lint-0.json / stats.json / numbers.json / build.json (only numbers.sha.json is shared, in qa_root)
    ntag = ""
    if a.notes:
        ntag = "n" + "-".join(sorted({re.sub(r"[^0-9a-z]", "", t.strip().lower()) for t in a.notes}))
    work = qa_root / ntag if ntag else qa_root
    work.mkdir(parents=True, exist_ok=True)
    mech: list[dict] = []
    cont: list[dict] = []
    warns: list[dict] = []

    def fail(kind, check, code, msg, note=None, line=None):
        (mech if kind == "mechanical" else cont).append(
            {"check": check, "code": code, "note": note, "line": line, "msg": msg})

    report: dict = {"tier": a.tier, "ok": False, "fast": bool(a.fast), "profile": profile["id"], "src": src.as_posix(),
                    "notes": [], "index": None, "build": {"ran": False, "ok": None, "pdfs": [],
                                                           "compendium": None},
                    "audit": {"ran": False, "problems": 0, "details": []},
                    "mechanical_failures": mech, "content_failures": cont, "warnings": warns, "steps": STEPS}

    def finish() -> int:
        report["ok"] = not mech and not cont
        report["duration_s"] = round(time.time() - t_start, 1)
        plat.write_json(outj, report)
        print(f"\nQA {a.tier}: {'OK' if report['ok'] else 'FAILED'} - {len(mech)} mechanical, "
              f"{len(cont)} content failure(s), {len(warns)} warning(s) [{report['duration_s']}s]")
        for label, items in (("mechanical", mech), ("content", cont)):
            for f in items[:40]:
                loc = f"{f['note']}" + (f":{f['line']}" if f.get("line") else "") if f.get("note") else ""
                print(f"  [{label}] {f['check']}/{f['code']} {loc} {f['msg'].splitlines()[0][:150]}")
        return 0 if report["ok"] else 1

    # ---- select notes -------------------------------------------------------------------------------------
    if not src.is_dir():
        fail("content", "setup", "no-src", f"{src} does not exist")
        return finish()
    wanted = None
    if a.notes:
        wanted = set()
        for t in a.notes:
            m = re.fullmatch(r"(\d+)([a-z]?)", t.strip().lower())
            wanted.add(f"{int(m.group(1)):02d}{m.group(2)}" if m else t.strip().lower())
            if m and m.group(2):
                wanted.add(f"{int(m.group(1)):02d}")      # a split note also selects the session's duplicate check
    files = []
    for p in sorted(src.glob("*.md")):
        m = NOTE_FILE_RE.match(p.name)
        if m and (wanted is None or m.group(1).lower() in wanted):
            files.append(p)
    if not files:
        fail("content", "setup", "no-notes", f"no notes matching {a.notes or 'all'} in {src}")
        return finish()
    # one note per (session, tier): two files with the same number are an error
    allnums: dict[str, list[str]] = {}
    for p in sorted(src.glob("*.md")):
        m = NOTE_FILE_RE.match(p.name)
        if m:
            digits = re.match(r"\d+", m.group(1)).group(0)
            # the number only: 10-x.md and 10a-x.md are the same session (a companion/split note is not allowed)
            allnums.setdefault(f"{int(digits):02d}", []).append(p.name)
            if m.group(1) != digits and (wanted is None or f"{int(digits):02d}" in wanted):
                fail("mechanical", "duplicates", "split-note",
                     f"{p.name}: a letter after the session number marks a companion/split note; one note per session "
                     "and tier: merge it into the main note and delete the file")
    for num, names in sorted(allnums.items()):
        if len(names) > 1 and (wanted is None or num in wanted):
            fail("mechanical", "duplicates", "dup-session",
                 f"{len(names)} notes share session number {num}: {', '.join(names)} (keep one; "
                 "ledger.py rename-slug changes a slug without duplicating)")
    stems = [p.stem for p in files]
    per: dict[str, dict] = {s: {"stem": s, "num": notefile.note_id(s)[0]} for s in stems}
    report["notes"] = [per[s] for s in stems]

    def sess_default(num: str, fname: str) -> Path | None:
        digits = re.match(r"\d+", num)
        if not digits:
            return None
        if lay is None:
            return None
        p = lay.session(int(digits.group(0))) / fname
        return p if p.is_file() else None

    digest_for = {p.stem: (plat.norm(a.digest) if a.digest else sess_default(per[p.stem]["num"], "digest.md"))
                  for p in files}
    def research_default(num: str) -> list[Path]:
        split = [x for x in (sess_default(num, "research-verify.md"), sess_default(num, "research-deep.md")) if x]
        if split:
            return split
        one = sess_default(num, "research.md")
        return [one] if one else []

    research_for = {p.stem: ([plat.norm(x) for x in a.research] if a.research else research_default(per[p.stem]["num"]))
                    for p in files}
    common = (["--project", str(lay.root)] if lay else []) + (["--profile", a.profile] if a.profile else [])         + (["--data-dir", a.data_dir] if a.data_dir else [])
    if lay is None and not a.profile:
        fail("content", "setup", "no-profile", "explicit-dir mode needs --profile")
        return finish()

    # ---- independent checks, in parallel ------------------------------------------------------------------
    jobs: dict[str, tuple] = {}
    groups: dict[str, list[Path]] = {}
    for p in files:
        groups.setdefault(str(digest_for[p.stem] or ""), []).append(p)
    for gi, (dg, ps) in enumerate(groups.items()):
        args = [*map(str, ps), *common, "--tier", a.tier, "--meta-dir", str(meta), "--json", str(work / f"lint-{gi}.json")]
        if dg:
            args += ["--digest", dg]
        if a.legacy:
            args.append("--legacy")
        jobs[f"lint-{gi}"] = ("lint_note.py", args, work / f"lint-{gi}.json")
    jobs["stats"] = ("note_stats.py", [*map(str, files), *common, "--tier", a.tier, "--json", str(work / "stats.json")],
                     work / "stats.json")
    checks = [meta / f"{s}.checks.py" for s in stems if (meta / f"{s}.checks.py").is_file()]
    nsha_path = qa_root / "numbers.sha.json"
    if a.fast and checks:
        import hashlib
        cur = {c.name: hashlib.sha256(c.read_bytes()).hexdigest() for c in checks}
        try:
            old = json.loads(nsha_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            old = {}
        checks = [c for c in checks if old.get(c.name) != cur[c.name]]
    if checks:
        jobs["numbers"] = ("verify_numbers.py", [*map(str, checks), "--json", str(work / "numbers.json")],
                           work / "numbers.json")
    if not a.fast:
        jobs["snippets"] = ("check_snippets.py", [*map(str, files), *common, "--work", str(build / "snippets"),
                                                      "--json", str(work / "snippets.json")],
                            work / "snippets.json")
    cit_notes = [p for p in files if a.tier == "advanced" and research_for[p.stem] and not a.fast]
    cit_groups: dict[tuple, list[Path]] = {}
    for p in cit_notes:
        cit_groups.setdefault(tuple(map(str, research_for[p.stem])), []).append(p)
    for gi, (rs, ps) in enumerate(cit_groups.items()):
        jobs[f"cit-{gi}"] = ("check_citations.py", [*map(str, ps), *common, "--research", *rs,
                                                      "--json", str(work / f"cit-{gi}.json")],
                             work / f"cit-{gi}.json")

    def go(item):
        key, (script, args, outp) = item
        outp.unlink(missing_ok=True)
        r = run_script(script, args)
        return key, r, read_json(outp)

    with ThreadPoolExecutor(max_workers=4) as ex:
        done = {k: (r, j) for k, r, j in ex.map(go, jobs.items())}

    def crashed(key: str, what: str) -> bool:
        r, j = done[key]
        if j is None:
            fail("content", what, "check-crashed", f"{what} produced no JSON (exit {r.returncode}): "
                 + tail(r.stderr or r.stdout, 6))
            return True
        return False

    # lint
    for gi in range(len(groups)):
        key = f"lint-{gi}"
        if crashed(key, "lint"):
            continue
        for nrec in done[key][1]["notes"]:
            st = per[nrec["stem"]]
            st["lint"] = {"errors": len(nrec["errors"]), "warnings": len(nrec["warnings"]),
                          "issues": [{"level": "error", **i} for i in nrec["errors"]]
                          + [{"level": "warning", **i} for i in nrec["warnings"]]}
            st["coverage"] = nrec.get("coverage")
            for i in nrec["errors"]:
                if i["code"] == "dup-session" or (i["code"] == "split-note" and re.match(r"\d+[a-z]-", nrec["stem"])):
                    continue          # reported once, tier-wide, by the duplicates check above
                fail(i.get("kind", "content"), "lint", i["code"], i["msg"], nrec["stem"], i.get("line"))
            for i in nrec["warnings"]:
                warns.append({"check": "lint", "code": i["code"], "note": nrec["stem"], "line": i.get("line"),
                              "msg": i["msg"]})
    # stats
    if not crashed("stats", "stats"):
        for nrec in done["stats"][1]["notes"]:
            b = nrec["budget"]
            per[nrec["stem"]]["stats"] = {**nrec["stats"], "budget": b, "status": nrec["status"]}
            w = b["words"]
            if w["status"] == "warn":
                what = "too thin" if w.get("too_thin") else "over the range"
                warns.append({"check": "stats", "code": "budget-words", "note": nrec["stem"], "line": None,
                              "msg": f"prose words {w['value']} {what} (range {w['range'][0]}-{w['range'][1]}"
                                     f", scaled x{w.get('scale', 1)} for the class length)"})
    # numbers
    for s in stems:
        per[s]["numbers"] = {"status": "none", "checks": 0, "failures": []}
    if "numbers" in done and not crashed("numbers", "numbers"):
        for fr in done["numbers"][1]["files"]:
            stem = Path(fr["path"]).name[: -len(".checks.py")]
            per[stem]["numbers"] = {"status": "fail" if fr["failures"] else "pass", "checks": fr["checks"],
                                    "failures": fr["failures"], "error": fr.get("error")}
            for lab in fr["failures"]:
                fail("content", "numbers", "claim-failed", f"claim check failed: {lab}", stem)
            if a.fast and not fr["failures"] and not fr.get("error"):       # remember only passing checks files
                import hashlib
                try:
                    o = json.loads(nsha_path.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    o = {}
                o[Path(fr["path"]).name] = hashlib.sha256(Path(fr["path"]).read_bytes()).hexdigest()
                plat.write_json(nsha_path, o)
    # snippets
    for s in stems:
        per[s]["snippets"] = {"pass": 0, "fail": 0, "skipped": 0, "results": []}
    if "snippets" in done and not crashed("snippets", "snippets"):
        for r in done["snippets"][1]["snippets"]:
            st = per[r["stem"]]["snippets"]
            st[r["status"]] += 1
            st["results"].append({k: r.get(k) for k in ("k", "line", "lang", "mode", "status", "reason", "exit_code")})
            if r["status"] == "fail":
                code = "snippet-compile" if "compile error" in r["reason"] else "snippet-mismatch"
                fail("content", "snippets", code, f"snippet #{r['k']} (line {r['line']}): {r['reason']}"
                     + (f" expected {r['expected']!r} got {r['actual']!r}" if r.get("expected") is not None else ""),
                     r["stem"], r["line"])
            elif r["status"] == "skipped":
                warns.append({"check": "snippets", "code": "skipped", "note": r["stem"], "line": r["line"],
                              "msg": r["reason"]})
    # citations
    for s in stems:
        per[s]["citations"] = {"status": "n/a"} if a.tier == "basic" else \
            {"status": "skipped", "reason": "no research file found (pass --research)"}
    for gi in range(len(cit_groups)):
        key = f"cit-{gi}"
        if crashed(key, "citations"):
            continue
        for nrec in done[key][1]["notes"]:
            per[nrec["stem"]]["citations"] = {
                "status": "fail" if nrec["errors"] else "pass", "urls": nrec["urls"], "domains": nrec["domains"],
                "unknown_urls": nrec["unknown_urls"], "errors": nrec["errors"], "warnings": nrec["warnings"]}
            for e in nrec["errors"]:
                fail("content", "citations", e["code"], e["msg"], nrec["stem"])
            for w in nrec["warnings"]:
                warns.append({"check": "citations", "code": w["code"], "note": nrec["stem"], "line": None,
                              "msg": w["msg"]})
    if a.tier == "advanced" and not a.fast:
        for p in files:
            if per[p.stem]["citations"]["status"] == "skipped":
                warns.append({"check": "citations", "code": "no-research", "note": p.stem, "line": None,
                              "msg": "citations not checked: no research.md given or found"})
    # figures (info; lint already fails a missing or malformed reference)
    figdir = src / "figures"
    used: set[str] = set()
    for p in files:
        text = p.read_text(encoding="utf-8-sig")
        refs = sorted({m.group(1) for m in IMG_RE.finditer(text)})
        local = [r for r in refs if FIG_RE.fullmatch(r)]
        used.update(Path(r).name for r in local)
        missing = [r for r in local if not (src / r).is_file()]
        per[p.stem]["figures"] = {"referenced": len(refs), "present": len(local) - len(missing),
                                  "missing": missing, "unreferenced": []}
        fplan = meta / f"{p.stem}.figplan.yaml"
        if fplan.is_file() and fplan.stat().st_size > 5 and not (meta / f"{p.stem}.figures.py").is_file():
            warns.append({"check": "figures", "code": "figplan-without-script", "note": p.stem, "line": None,
                          "msg": f"{fplan.name} exists but {p.stem}.figures.py does not"})
    if figdir.is_dir():
        stray = sorted(f.name for f in [*figdir.glob("*.png"), *figdir.glob("*.jpg"), *figdir.glob("*.jpeg")]
                       if f.name not in used)
        for s in stems:
            per[s]["figures"]["unreferenced"] = stray
        if stray and not wanted:
            warns.append({"check": "figures", "code": "unreferenced-figure", "note": None, "line": None,
                          "msg": f"figure(s) in figures/ that no note references: {', '.join(stray[:6])}"})

    # ---- index --------------------------------------------------------------------------------------------------
    ij = work / "index.json"
    ij.unlink(missing_ok=True)
    if a.fast:
        report["index"] = {"ok": True, "skipped": "--fast"}
    elif a.no_rollup:
        report["index"] = {"ok": True, "skipped": "--no-rollup"}
    else:
        r = run_script("update_index.py", [*common, "--tier", a.tier, "--src", str(src), "--json", str(ij)]
                       + (["--legacy"] if a.legacy else []))
        report["index"] = {"ok": r.returncode == 0, "readme": (src / "README.md").as_posix()}
        if r.returncode != 0:
            fail("mechanical", "index", "update-index-failed", tail(r.stderr or r.stdout, 6))

    # ---- PDF build + audit -------------------------------------------------------------------------------------
    if a.skip_build or a.fast:
        report["build"]["skipped"] = "--fast" if a.fast else "--skip-build"
        report["audit"]["skipped"] = "--fast" if a.fast else "--skip-build"
        return finish()
    bscript, ascript = HERE / "build_pdf.py", HERE / "audit_pdf.py"
    if not bscript.is_file() or not ascript.is_file():
        fail("mechanical", "build", "build-scripts-missing",
             f"{bscript.name if not bscript.is_file() else ascript.name} not found in {HERE}; use --skip-build")
        return finish()
    bj = work / "build.json"
    bj.unlink(missing_ok=True)
    bargs = [*common, "--tier", a.tier, "--src", str(src), "--pdf-dir", str(pdfdir), "--build-dir", str(build),
             "--json", str(bj)]
    if not a.no_prune:
        bargs.append("--prune")
    if a.no_rollup:
        bargs.append("--notes-only")
    if a.notes:
        bargs += ["--notes", *[notefile.note_id(s)[0] for s in stems], *([] if a.no_rollup else ["00"])]   # 00 = the README index
    r = run_script("build_pdf.py", bargs, timeout=1800)
    bjson = read_json(bj)
    # only the PDFs this run built (its own report), never whatever else changed in the folder meanwhile
    mine = [e for e in (bjson or {}).get("pdfs", []) if e.get("pdf")]
    pdfs = sorted(Path(e["pdf"]).as_posix() for e in mine)
    report["build"] = {"ran": True, "ok": r.returncode == 0, "returncode": r.returncode, "pdfs": pdfs,
                       "compendium": next((p for p in pdfs if "compendium" in Path(p).name.lower()), None),
                       "log_tail": tail(r.stderr + "\n" + r.stdout, 8)}
    if r.returncode != 0:
        blob = r.stdout + r.stderr
        miss = re.findall(r"MISSING FIGURE[^\n]*", blob)
        if miss:
            for m in miss[:10]:
                fail("mechanical", "build", "missing-figure", m.strip())
        else:
            fail("mechanical", "build", "build-failed", tail(blob, 8))
        return finish()
    aj = work / "audit.json"
    aj.unlink(missing_ok=True)
    aargs = [str(pdfdir), "--json", str(aj)]
    if a.notes:
        # per-note scope: audit exactly the PDFs this run built, with this run's own manifest entries.
        # (without --no-rollup that includes the compendium and index this run just built; the rest of the
        # folder is other sessions' business)
        names = [e["name"] for e in mine]
        if not names:
            fail("mechanical", "build", "no-pdf-built", "the build reported no PDF for the selected notes")
            return finish()
        aargs += ["--only", *names, "--manifest", str(bj)]
    elif (build / "manifest.json").is_file():
        aargs += ["--manifest", str(build / "manifest.json")]
    r = run_script("audit_pdf.py", aargs)
    ajson = read_json(aj)
    problems = collect(ajson, "problems") if ajson is not None else []
    if r.returncode != 0 and not problems:
        problems = [tail(r.stdout + r.stderr, 6) or f"audit_pdf exit {r.returncode}"]
    report["audit"] = {"ran": True, "problems": len(problems), "details": problems[:60],
                       "warnings": len(collect(ajson, "warnings")) if ajson is not None else 0}
    for pr in problems[:60]:
        fail("mechanical", "audit", "pdf-problem", pr)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
