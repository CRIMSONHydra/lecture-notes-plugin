# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml", "pymupdf"]
# ///
"""Hard and soft checks for the C++ session-2 end-to-end run (PLAN section 5).

    uv run tests/e2e/check_e2e.py --project <e2e-project> --session 2 \
        --run-json run.json [--before B.json --after A.json] [--json OUT]

``--project`` is the fixed-layout test project (notes/<tier>/, pdf/<tier>/, .notes-plugin-internal/work/...).

Hard checks decide the exit code (1 on any failure). Soft checks (class corrections flagged, concept
coverage) print a score from an optional cpp-02-answer-key.md (not shipped; skipped when absent) and never fail the run.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent.parent
SCRIPTS = PLUGIN / "scripts"
sys.path.insert(0, str(SCRIPTS))
from notes_lib import layout, notefile  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    return bool(ok)


def run_script(script: str, *args: str, jsonp: Path | None = None) -> tuple[int, dict, str]:
    cmd = ["uv", "run", "--quiet", str(SCRIPTS / script), *args]
    if jsonp:
        cmd += ["--json", str(jsonp)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=PLUGIN)
    data = {}
    if jsonp and jsonp.is_file():
        try:
            data = json.loads(jsonp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return p.returncode, data, (p.stdout + p.stderr)


def section(text: str, num: int) -> str:
    m = re.search(rf"^##\s+{num}\b[^\n]*\n(.*?)(?=^##\s+\d+\b|\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def matches(item: dict, text: str) -> bool:
    hits = [bool(re.search(p, text, re.I | re.S)) for p in item["any"]]
    return all(hits) if item.get("all") else any(hits)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="the test project (contains .notes-plugin-internal/profile.md)")
    ap.add_argument("--session", default="2")
    ap.add_argument("--run-json", default=None)
    ap.add_argument("--before")
    ap.add_argument("--after")
    ap.add_argument("--json", default=None)
    ap.add_argument("--no-key", action="store_true", help="no answer key (session != 2): hard checks only")
    ap.add_argument("--today", default=date.today().isoformat())
    a = ap.parse_args(argv)

    lay = layout.Layout.for_project(a.project)
    out = lay.root
    nn = f"{int(a.session):02d}"
    sess = lay.session(nn)
    rep = lay.reports
    work = rep / "e2e-checks"
    work.mkdir(parents=True, exist_ok=True)
    key_file = HERE / "cpp-02-answer-key.md"      # not shipped (it quotes a real class); supply your own
    if a.no_key or not key_file.is_file():
        key = {"corrections": [], "concepts": []}
    else:
        key_text = key_file.read_text(encoding="utf-8")
        key = yaml.safe_load(re.search(r"```yaml\n(.*?)```", key_text, re.S).group(1))
    prefix = "CPP"

    print("== 1. expected files")
    must = [sess / "inputs.json", sess / "digest.md",
            sess / "research.md", rep / f"{nn}-report.md", rep / f"qa-basic-{nn}.json",
            rep / f"qa-advanced-{nn}.json"]
    for p in must:
        check(p.is_file(), f"exists {p.relative_to(out).as_posix()}")
    if any((sess / "code").glob("Lec-*/*")):
        check(True, "instructor code fetched")
    else:
        print("  [warn] no instructor code fetched (repo folder missing or GitHub 403): not a failure")
    shards = sorted((sess / "screen-notes").glob("shard-*.md"))
    check(len(shards) >= 1, f"screen-notes shards ({len(shards)})")
    pdfs = {}
    for tier in ("basic", "advanced"):
        d = lay.pdf(tier)
        note = sorted(d.glob(f"{prefix}-{nn}-*-{tier}.pdf"))
        check(bool(note), f"{tier} note PDF", note[0].name if note else "missing")
        check((d / f"{prefix}-00-index-{tier}.pdf").is_file(), f"{tier} index PDF")
        check((d / f"{prefix}-Compendium-{tier}.pdf").is_file(), f"{tier} compendium PDF")
        pdfs[tier] = note[0] if note else None

    inputs = json.loads((sess / "inputs.json").read_text(encoding="utf-8")) if (sess / "inputs.json").is_file() else {}
    fj = (inputs.get("frames") or {}).get("frames_json")
    if fj and shards:
        rc, d, o = run_script("check_screen_notes.py", str(sess / "screen-notes"), "--frames-json", fj,
                              jsonp=work / "screen.json")
        check(rc == 0, "check_screen_notes: all frames accounted for",
              f"{d.get('total')} frames, {len(d.get('read', []))} read, {len(d.get('skipped', []))} skipped")
    else:
        check(False, "check_screen_notes", "no frames.json in inputs or no shards")
    digest = sess / "digest.md"
    if digest.is_file():
        rc, d, o = run_script("check_digest.py", str(digest), jsonp=work / "digest.json")
        check(rc == 0, "check_digest clean", f"{len(d.get('errors', []))} errors, {len(d.get('warnings', []))} warnings")

    if a.no_key:
        sp = (inputs.get("speech") or {}).get("primary")
        check(sp == "whisper", "inputs speech.primary == whisper", str(sp))
        pj = (sess / "video" / "pipeline.json")
        pd = json.loads(pj.read_text(encoding="utf-8")) if pj.is_file() else {}
        check(pd.get("ok") is True and pd.get("steps", {}).get("keyframes", {}).get("status") == "ok", "video pipeline ran keyframes in-project", str({k: v.get("status") for k, v in pd.get("steps", {}).items()}))
        vdir = sess / "video"
        check((vdir / "frames" / "frames.json").is_file() or any(vdir.rglob("frames.json")), "keyframes ran in .notes-plugin-internal/work/sessions/NN/video/")
        if digest.is_file():
            check(re.search(r"whisper", digest.read_text(encoding="utf-8")[:6000], re.I) is not None and "transcript: null" in digest.read_text(encoding="utf-8")[:1500],
                  "digest records Whisper as the speech source (transcript: null)")
    print("== 2. audit_pdf")
    for tier in ("basic", "advanced"):
        d = lay.pdf(tier)
        if d.is_dir():
            rc, data, o = run_script("audit_pdf.py", str(d), "--project", str(out), jsonp=work / f"audit-{tier}.json")
            check(rc == 0, f"audit_pdf {tier}: 0 problems", "" if rc == 0 else o[-400:])

    print("== 3. qa json ok")
    for tier in ("basic", "advanced"):
        p = rep / f"qa-{tier}-{nn}.json"
        if p.is_file():
            q = json.loads(p.read_text(encoding="utf-8"))
            check(q.get("ok") is True, f"qa-{tier}-{nn}.json ok",
                  f"mech={len(q.get('mechanical_failures', []))} content={len(q.get('content_failures', []))}")

    notes = {}
    for tier in ("basic", "advanced"):
        c = sorted(lay.notes(tier).glob(f"{nn}-*.md"))
        notes[tier] = c[0] if c else None
        check(c != [], f"{tier} source note", c[0].name if c else "missing")

    summary = {}
    print("== 4. basic tier")
    b = notes["basic"]
    if b:
        rc, d, o = run_script("note_stats.py", str(b), "--project", str(out), "--tier", "basic",
                              jsonp=work / "stats-basic.json")
        st = d["notes"][0]["stats"]
        bud = d["notes"][0]["budget"]
        summary["basic"] = st
        check(1500 <= st["prose_words"] <= 2500, "basic prose words in 1500-2500",
              f"{st['prose_words']} (warn band to 2750: {st['prose_words'] <= 2750})")
        check(st["minutes"] <= 35, "basic computed minutes <= 35", f"{st['minutes']}")
        check(st["figures"] == 0, "basic: 0 figures")
        body = notefile.parse_note(b).body
        check(len(re.findall(r"^```text\s*$", body, re.M)) >= 1, "basic: >=1 ASCII ```text diagram")
        check(st["gapfill_words"] > 0 and bud["gapfill"]["status"] == "pass", "basic: gap fill present, share <= 20%",
              f"{st['gapfill_words']} words = {bud['gapfill']['value']}")
        rc, d, o = run_script("lint_note.py", str(b), "--project", str(out), "--tier", "basic",
                              "--digest", str(digest), "--meta-dir", str(lay.meta("basic")),
                              jsonp=work / "lint-basic.json")
        warns = [w for n in d.get("notes", []) for w in n["warnings"]]
        errs = [w for n in d.get("notes", []) for w in n["errors"]]
        check(rc == 0, "basic lint: 0 errors", "; ".join(e["msg"][:80] for e in errs[:3]))
        check(not any(w["code"] == "check-count" for w in warns), "basic: check-yourself has 5-8 questions")
        rc, d, o = run_script("check_snippets.py", str(b), "--project", str(out))
        check(rc == 0, "basic {run} snippets pass (or skipped w/ reason)",
              o.strip().splitlines()[-1] if o.strip() else "")
        txt = b.read_text(encoding="utf-8")
        check((f"Lec-{int(a.session)}/" in txt) if a.no_key else ("Lec-2/main.cpp" in txt and re.search(r"int\s+add\s*\(\s*int\s+x\s*,\s*int\s+y\s*\)", txt) is not None),
              "basic cites the instructor Lec-N code (+ add() for session 2)")

    print("== 5. advanced tier")
    v = notes["advanced"]
    research = sess / "research.md"
    if v:
        rc, d, o = run_script("check_citations.py", str(v), "--research", str(research), "--project", str(out),
                              jsonp=work / "cite.json")
        nd = (d.get("notes") or [{}])[0]
        summary["adv_sources"] = nd.get("urls")
        summary["adv_domains"] = nd.get("domain_list")
        check((nd.get("urls") or 0) >= 10, ">=10 distinct cited URLs", str(nd.get("urls")))
        check(len(nd.get("domain_list") or []) >= 4, ">=4 domains", ", ".join(nd.get("domain_list") or []))
        check(any(x.endswith("en.cppreference.com") for x in nd.get("domain_list") or []),
              "cites en.cppreference.com")
        un = len(nd.get("unknown_urls") or [])
        check(bool(nd.get("urls")) and un / nd["urls"] <= 0.20, "cited URLs in research G (<=20% unknown)",
              f"{un} unknown")
        note = notefile.parse_note(v)
        check(a.today in str(note.meta.get("verified_as_of", "")), "verified_as_of == run date",
              f"{note.meta.get('verified_as_of')} vs {a.today}")
        rc, st, o = run_script("note_stats.py", str(v), "--project", str(out), "--tier", "advanced",
                               jsonp=work / "stats-adv.json")
        s = st["notes"][0]["stats"]
        summary["advanced"] = s
        check(s["figures"] >= 2, ">=2 figures embedded", str(s["figures"]))
        rc, d, o = run_script("lint_note.py", str(v), "--project", str(out), "--tier", "advanced",
                              "--digest", str(digest), "--meta-dir", str(lay.meta("advanced")),
                              jsonp=work / "lint-adv.json")
        errs = [w for n in d.get("notes", []) for w in n["errors"]]
        cov = (d.get("notes") or [{}])[0].get("coverage") or {}
        check(rc == 0, "advanced lint: 0 errors (required sections, coverage sidecar)",
              "; ".join(e["msg"][:80] for e in errs[:3]))
        check(bool(cov.get("checked")) and not cov.get("missing") and not cov.get("no_example"),
              "coverage sidecar covers every digest concept",
              f"missing={cov.get('missing')} no_example={cov.get('no_example')}")
        man = lay.build("advanced") / "manifest.json"
        if man.is_file() and pdfs["advanced"]:
            ent = next((e for e in json.loads(man.read_text(encoding="utf-8")).get("pdfs", [])
                        if e["name"] == pdfs["advanced"].name), {})
            check((ent.get("images_expected") or 0) >= 2, "advanced PDF embeds >=2 figures (manifest)",
                  str(ent.get("images_expected")))
        txt = v.read_text(encoding="utf-8")
        check((f"Lec-{int(a.session)}/" in txt) if a.no_key else ("Lec-2/main.cpp" in txt and re.search(r"int\s+add\s*\(\s*int\s+x\s*,\s*int\s+y\s*\)", txt) is not None),
              "advanced cites the instructor Lec-N code (+ add() for session 2)")

    print("== 6. snapshot diff")
    if a.before and a.after:
        p = subprocess.run(["uv", "run", "--quiet", str(PLUGIN / "tests" / "snapshot.py"), "diff", a.before, a.after],
                           capture_output=True, text=True)
        check(p.returncode == 0, "cpp tree snapshot diff empty", p.stdout.strip()[-300:])
    else:
        print("  (skipped: pass --before/--after)")

    print("== soft checks (answer key)")
    soft = {}
    if digest.is_file():
        dt = digest.read_text(encoding="utf-8")
        s5 = section(dt, 5)
        found = [c["id"] for c in key["corrections"] if matches(c, s5)]
        soft["corrections_in_digest"] = found
        print(f"  digest section 5 flags {len(found)}/9: {found}")
        print(f"    missing: {[c['id'] for c in key['corrections'] if c['id'] not in found]}")
        inv = section(dt, 2) + section(dt, 3)
        cov_d = [c["id"] for c in key["concepts"] if matches(c, inv)]
        soft["concepts_in_digest"] = cov_d
        print(f"  digest concepts {len(cov_d)}/{len(key['concepts'])}; missing: "
              f"{[c['id'] for c in key['concepts'] if c['id'] not in cov_d]}")
    for tier in ("basic", "advanced"):
        if not notes[tier]:
            continue
        body = notes[tier].read_text(encoding="utf-8")
        callouts = "\n".join(re.findall(r"^>\s*\*\*Class correction[^\n]*(?:\n>[^\n]*)*", body, re.M | re.I))
        reds = [c["id"] for c in key["corrections"] if c.get("must_in_notes") and matches(c, callouts)]
        corr = [c["id"] for c in key["corrections"] if matches(c, callouts)]
        cov_n = [c["id"] for c in key["concepts"] if matches(c, body)]
        soft[tier] = {"red_in_callouts": reds, "corrections_in_callouts": corr, "concepts": cov_n}
        print(f"  {tier}: red corrections as callouts {reds} ({'OK' if len(reds) == 2 else 'INCOMPLETE'}); "
              f"corrections in callouts {len(corr)}/9 {corr}; concepts {len(cov_n)}/{len(key['concepts'])} "
              f"(missing {[c['id'] for c in key['concepts'] if c['id'] not in cov_n]})")

    print("== run.json")
    if a.run_json and Path(a.run_json).is_file():
        try:
            r = json.loads(Path(a.run_json).read_text(encoding="utf-8"))
            summary["run"] = {"cost_usd": r.get("total_cost_usd"), "duration_ms": r.get("duration_ms"),
                              "turns": r.get("num_turns"), "is_error": r.get("is_error"),
                              "usage": r.get("usage"), "modelUsage": r.get("modelUsage")}
            print(f"  cost ${r.get('total_cost_usd')}  wall {(r.get('duration_ms') or 0) / 60000:.1f} min  "
                  f"turns {r.get('num_turns')}  error={r.get('is_error')}")
            print(f"  usage {json.dumps(r.get('usage'))}")
        except Exception as e:  # noqa: BLE001
            print(f"  could not parse run.json: {e}")

    fails = [r for r in RESULTS if not r[0]]
    print(f"\nHARD CHECKS: {len(RESULTS) - len(fails)}/{len(RESULTS)} pass"
          + ("" if not fails else "  FAILED: " + "; ".join(f[1] for f in fails)))
    if a.json:
        Path(a.json).write_text(json.dumps({"results": RESULTS, "soft": soft, "summary": summary},
                                           indent=1, default=str), encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
