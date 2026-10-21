# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Self-test for PLAN 14 (speed): qa --fast, snippet cache, two-file research, timing report."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
# optional read-only real subject projects for extra regression tests; every test that needs one skips when absent
SUBJ_CPP = os.environ.get("NOTES_TEST_CPP_ROOT", "/nonexistent/cpp-subject")
SUBJ_FDE = os.environ.get("NOTES_TEST_FDE_ROOT", "/nonexistent/fde-subject")
SUBJ_CV = os.environ.get("NOTES_TEST_CV_ROOT", "/nonexistent/cv-subject")
SCRIPTS = PLUGIN / "scripts"
FIX = PLUGIN / "tests" / "fixtures" / "qa"
WORK = PLUGIN / "test-output" / "_b1" / "speed"
PROFILE = FIX / "profile-qa.md"
UV = shutil.which("uv") or "uv"
INT = ".notes-plugin-internal"


def run(script, *args, timeout=900):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.setdefault("LECTURE_NOTES_CLASSIFIER_CMD",                     # no model calls in tests
                   f'"{Path(sys.executable).as_posix()}" "{(Path(__file__).parent / "fixtures" / "classifier_stub.py").as_posix()}"')
    return subprocess.run([UV, "run", "--quiet", "--script", str(SCRIPTS / script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


def proj(name, tier="basic", fixture="tier-basic-good"):
    p = WORK / name
    (p / INT).mkdir(parents=True)
    shutil.copy2(PROFILE, p / INT / "profile.md")
    shutil.copytree(FIX / fixture / tier / "src", p / "notes" / tier)
    if (FIX / fixture / tier / "meta").is_dir():
        shutil.copytree(FIX / fixture / tier / "meta", p / INT / "work" / "meta" / tier)
    return p


def t_cache():
    w = WORK / "snip"
    sn = FIX / "snippets" / "snip-ok.md"
    r1 = run("check_snippets.py", sn, "--profile", PROFILE, "--work", w, "--json", WORK / "s1.json")
    r2 = run("check_snippets.py", sn, "--profile", PROFILE, "--work", w, "--json", WORK / "s2.json")
    j1, j2 = (json.loads((WORK / f"s{i}.json").read_text(encoding="utf-8")) for i in (1, 2))
    assert r1.returncode == 0 and not any(s["cached"] for s in j1["snippets"]), r1.stdout
    assert r2.returncode == 0 and all(s["cached"] for s in j2["snippets"]) and "CACHE" in r2.stdout, r2.stdout
    assert (w / "cache.json").is_file()
    # a changed snippet is recompiled
    note = WORK / "snip-changed.md"
    note.write_text(sn.read_text(encoding="utf-8").replace("return 0;", "return 0; // changed", 1), encoding="utf-8")
    r3 = run("check_snippets.py", note, "--profile", PROFILE, "--work", w, "--json", WORK / "s3.json")
    j3 = json.loads((WORK / "s3.json").read_text(encoding="utf-8"))
    assert r3.returncode == 0 and not all(s["cached"] for s in j3["snippets"]), r3.stdout


def t_research_two_files():
    p = proj("cit", "advanced", "tier-adv")
    note = p / "notes/advanced/02-adv-demo.md"
    res = (FIX / "digest" / "research-good.md").read_text(encoding="utf-8").split("\n")
    drop = ("godbolt.org |", "agner.org/optimize", "gitlab.com/x86")
    a = WORK / "research-verify.md"
    b = WORK / "research-deep.md"
    a.write_text("\n".join(x for x in res if not any(k in x for k in drop)), encoding="utf-8")
    b.write_text("## G. Source register\n\n| URL |\n| --- |\n" + "\n".join(x for x in res if any(k in x for k in drop)),
                 encoding="utf-8")
    assert run("check_citations.py", note, "--research", a, "--profile", PROFILE).returncode == 1
    r = run("check_citations.py", note, "--research", a, b, "--profile", PROFILE)
    assert r.returncode == 0, r.stdout
    # qa discovers research-verify.md + research-deep.md in the session dir
    sd = p / INT / "work" / "sessions" / "02"
    sd.mkdir(parents=True)
    shutil.copy2(a, sd / "research-verify.md")
    shutil.copy2(b, sd / "research-deep.md")
    for n in ("02-stack-frame", "02-latency-ladder"):
        (p / "notes/advanced/figures").mkdir(exist_ok=True)
        shutil.copy2(next((PLUGIN / "tests/fixtures").rglob("*.png"), None) or a, p / "notes/advanced/figures" / f"{n}.png") \
            if False else None
    r = run("qa.py", "--project", p, "--tier", "advanced", "--skip-build", "--digest", FIX / "digest" / "digest-good.md",
            "--json", WORK / "qa-cit.json")
    j = json.loads((WORK / "qa-cit.json").read_text(encoding="utf-8"))
    assert j["notes"][0]["citations"]["status"] in ("pass", "fail") and j["notes"][0]["citations"]["status"] != "skipped", j["notes"][0]["citations"]


def t_fast_seeded():
    p = proj("seed")
    f = p / "notes/basic/02-stack-frames.md"
    t = f.read_text(encoding="utf-8")
    seeds = ["\n```cpp\n// " + "x" * 130 + "\nint a%d;\n```\n" % i for i in range(10)]
    seeds += [f"\nTODO fix this {i}\n" for i in range(10)]
    seeds += [f"\n![fig](figures/missing-{i}.png)\n" for i in range(10)]
    f.write_text(t + "".join(seeds), encoding="utf-8")
    t0 = time.time()
    r = run("qa.py", "--project", p, "--tier", "basic", "--fast", "--digest", FIX / "digest" / "digest-good.md",
            "--json", WORK / "fast.json")
    dt = time.time() - t0
    j = json.loads((WORK / "fast.json").read_text(encoding="utf-8"))
    assert j["fast"] and j["build"].get("skipped") == "--fast" and r.returncode == 1, r.stdout
    mech = j["mechanical_failures"]
    assert len(mech) >= 30, len(mech)
    codes = {m["code"] for m in mech}
    assert {"code-wide", "placeholder"} <= codes or len(codes) >= 2, codes
    assert dt < 10, f"--fast took {dt:.1f}s"
    print(f"    fast run: {dt:.1f}s, {len(mech)} mechanical failures in one pass")
    # a big real note, timing only
    for cand in (Path(os.environ.get("NOTES_TEST_E2E_NOTES", "/nonexistent/e2e/notes/advanced")), Path(SUBJ_CPP + "/notes/advanced")):
        notes = sorted(cand.glob("02-*.md")) if cand.is_dir() else []
        if notes:
            q = proj("big", "advanced", "tier-adv")
            shutil.rmtree(q / "notes/advanced")
            (q / "notes/advanced").mkdir()
            shutil.copy2(notes[0], q / "notes/advanced" / notes[0].name)
            t0 = time.time()
            run("qa.py", "--project", q, "--tier", "advanced", "--fast", "--json", WORK / "big.json")
            dt = time.time() - t0
            print(f"    --fast on {notes[0].name} ({notes[0].stat().st_size // 1024} KB): {dt:.1f}s")
            assert dt < 10, dt
            break


def t_timing():
    p = proj("tim")
    base = "2026-10-06T10:00:00+00:00"
    marks = [("digest", "start", "10:00:00"), ("digest", "end", "10:13:00"),
             ("writer-basic", "start", "10:13:00"), ("writer-basic", "end", "10:19:00"),
             ("qa-basic", "start", "10:19:00"), ("qa-basic", "end", "10:21:00")]
    for st, ev, hms in marks:
        r = run("timing.py", "mark", "--project", p, "--session", "2", "--stage", st, ev, "--at", f"2026-10-06T{hms}+00:00")
        assert r.returncode == 0, r.stderr
    assert (p / INT / "work/sessions/02/timings.jsonl").is_file()
    r = run("timing.py", "report", "--project", p, "--session", "2", "--json", WORK / "tim.json")
    assert r.returncode == 0, r.stderr
    j = json.loads((WORK / "tim.json").read_text(encoding="utf-8"))
    assert j["flagged"] == ["digest"], j["flagged"]          # 12 min > 1.5 x 8
    assert "over 1.5x budget" in r.stdout and "total basic" in r.stdout and "wall clock" in r.stdout, r.stdout
    assert j["tier_totals_min"]["basic"] == 21.0 and j["wall_min"] == 21.0, j


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    bad = []
    for fn in (t_cache, t_research_two_files, t_fast_seeded, t_timing):
        try:
            fn()
            print(f"  [PASS] {fn.__name__}", flush=True)
        except Exception:
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {fn.__name__}")
            bad.append(fn.__name__)
    print(f"\n{4 - len(bad)}/4 passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
