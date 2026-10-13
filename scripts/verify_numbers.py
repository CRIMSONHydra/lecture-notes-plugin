# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "sympy"]
# ///
"""Run per-note claim checks (``<stem>.checks.py``) and report a CV-style RESULT line.

    uv run verify_numbers.py [<meta dir | file.checks.py>...] [--project DIR --tier T] [--json OUT]

With no paths and --tier: every *.checks.py in <project>/.notes-plugin-internal/work/meta/<tier>/.

Each checks file is executed with ``runpy`` after these names are injected:

    ok(cond, label, detail="")             record a claim; prints OK / FAIL
    close(got, want, tol=1e-6, rel=None, label="")
                                           numeric claim; |got-want| <= tol (absolute) or, if ``rel`` is
                                           given, <= rel*|want|. Arrays are compared element-wise.
    section(title)                         print a heading
    np, math, Fraction, Decimal            for the arithmetic (``import sympy`` works too)

A file that raises counts as one failure (label ``<exception>``). Exit 1 on any failure.
Pattern ported from the CV ``verify_numbers.py`` (ok/FAILS/RESULT), generalised to per-note files.
"""
from __future__ import annotations

import argparse
import math
import runpy
import sys
import traceback
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, layout, platform as plat  # noqa: E402

plat.utf8_stdio()

import numpy as np  # noqa: E402


class Ctx:
    """Per-file recorder: the injected helpers close over one of these."""

    def __init__(self, name: str):
        self.name = name
        self.checks = 0
        self.failures: list[str] = []

    def ok(self, cond, label="", detail=""):
        self.checks += 1
        passed = bool(cond)
        if not passed:
            self.failures.append(label or "(unlabelled)")
        print(f"{'OK  ' if passed else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))
        return passed

    def close(self, got, want, tol=1e-6, rel=None, label=""):
        try:
            g, w = np.asarray(got, dtype=float), np.asarray(want, dtype=float)
            if rel is not None:
                good = bool(np.all(np.abs(g - w) <= rel * np.abs(w)))
                how = f"rel {rel:g}"
            else:
                good = bool(np.all(np.abs(g - w) <= tol))
                how = f"tol {tol:g}"
        except Exception as e:                      # non-numeric input is a failed claim, not a crash
            good, how = False, f"not comparable ({type(e).__name__})"
        return self.ok(good, label or "close", f"got {_short(got)}, want {_short(want)} ({how})")

    def section(self, title):
        print(f"\n=== {title} ===")


def _short(v) -> str:
    s = np.array2string(np.asarray(v), precision=6, separator=",") if hasattr(v, "__len__") else repr(v)
    return s if len(s) <= 80 else s[:77] + "..."


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        pp = plat.norm(p)
        if pp.is_dir():
            files += sorted(pp.glob("*.checks.py"))
        elif pp.is_file():
            files.append(pp)
        else:
            raise FileNotFoundError(f"no such file or directory: {p}")
    return files


def run_file(path: Path) -> dict:
    ctx = Ctx(path.name)
    print(f"\n##### {path.name}")
    err = None
    try:
        runpy.run_path(str(path), init_globals={
            "ok": ctx.ok, "close": ctx.close, "section": ctx.section,
            "np": np, "math": math, "Fraction": Fraction, "Decimal": Decimal,
        }, run_name="__checks__")
    except SystemExit as e:
        if e.code not in (None, 0):
            err = f"SystemExit({e.code})"
    except BaseException:                           # noqa: BLE001 - any failure in a checks file is a result
        err = traceback.format_exc(limit=3).strip().splitlines()[-1]
        print("ERROR " + err)
    if err:
        ctx.failures.append("<exception>")
    return {"path": path.as_posix(), "checks": ctx.checks, "failures": ctx.failures, "error": err}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", help="a meta dir (runs every *.checks.py in it) or checks files")
    ap.add_argument("--project", default=None, help="project dir (default: discovered from cwd)")
    ap.add_argument("--tier", choices=layout.TIERS, default=None, help="with no paths: the tier's meta dir")
    ap.add_argument("--json", default=None, help="write a JSON report here")
    a = ap.parse_args(argv)
    paths = list(a.paths)
    if not paths:
        if not a.tier:
            print("verify_numbers: give paths, or --tier [--project DIR]", file=sys.stderr)
            return 2
        try:
            paths = [str(layout.Layout.for_project(a.project).meta(a.tier))]
        except layout.ProjectError as e:
            print(f"verify_numbers: {e}", file=sys.stderr)
            return 2
    try:
        files = collect(paths)
    except FileNotFoundError as e:
        print(f"verify_numbers: {e}", file=sys.stderr)
        return 2
    results = [run_file(f) for f in files]
    fails = [(Path(r["path"]).name, lab) for r in results for lab in r["failures"]]
    total = sum(r["checks"] for r in results)
    print("\n" + "=" * 60)
    if not files:
        print("RESULT: NO CHECK FILES (nothing to verify)")
    elif fails:
        print(f"RESULT: {len(fails)} FAILURES: " + ", ".join(f"{f}:{lab}" for f, lab in fails))
    else:
        print(f"RESULT: ALL CHECKS PASSED ({total} checks in {len(files)} file(s))")
    print("=" * 60)
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {"ok": not fails, "files": results, "total_checks": total,
                                 "failed": [{"file": f, "label": lab} for f, lab in fails]})
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
