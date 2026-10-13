# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run a per-note figure script (``<stem>.figures.py``) and check what it produced.

    uv run make_figures.py --script F (--out DIR | --tier T [--project DIR]) [--list] [--audit] [names ...] [--json OUT]

Sets ``NOTEFIG_PATH=<plugin>/scripts/figures`` (so the script can ``import notefig``) and
``NOTEFIG_OUT=DIR`` (default DIR: <project>/notes/<tier>/figures), runs ``uv run --script F [--audit] [names]`` and verifies that every registered
(or requested) name produced a non-empty ``DIR/<name>.png``. Reports each PNG's sha256 so a second run
can be compared for byte-determinism. The figure script's exit code passes through (1 a builder raised,
2 unknown name, 4 layout-audit hits with --audit); a missing PNG is exit 1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, layout, platform as plat  # noqa: E402

plat.utf8_stdio()


def find_uv() -> str:
    uv = shutil.which("uv")
    if uv:
        return uv
    cand = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    if cand.exists():
        return str(cand)
    sys.exit("make_figures: `uv` not found on PATH (https://docs.astral.sh/uv/)")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_script(uv: str, script: Path, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([uv, "run", "--quiet", "--script", str(script), *args], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--script", required=True, help="the <stem>.figures.py to run")
    ap.add_argument("--out", help="directory the PNGs are written to (default: <project>/notes/<tier>/figures)")
    ap.add_argument("--tier", choices=layout.TIERS, help="with --project: default --out is notes/<tier>/figures")
    ap.add_argument("--project", help="project dir (default: discovered from cwd)")
    ap.add_argument("--list", action="store_true", help="list the figure names the script registers")
    ap.add_argument("--audit", action="store_true", help="run the layout audit (overlapping labels)")
    ap.add_argument("--json", default=None, help="write a JSON report here")
    ap.add_argument("names", nargs="*", help="build only these figures")
    a = ap.parse_args(argv)

    script = plat.norm(a.script)
    try:
        out = plat.norm(a.out) if a.out else layout.Layout.for_project(a.project).figures(a.tier or "basic") if a.tier else None
    except layout.ProjectError as e:
        print(f"make_figures: {e}", file=sys.stderr)
        return 2
    if out is None:
        print("make_figures: give --out DIR or --tier T", file=sys.stderr)
        return 2
    if not script.is_file():
        print(f"make_figures: script not found: {script}", file=sys.stderr)
        return 2
    uv = find_uv()
    env = dict(os.environ)
    env["NOTEFIG_PATH"] = str(HERE / "figures")
    env["NOTEFIG_OUT"] = str(out)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("MPLBACKEND", "Agg")

    if a.list:
        r = run_script(uv, script, ["--list"], env)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        return r.returncode

    guard.assert_writable(out)
    if a.json:
        guard.assert_writable(plat.norm(a.json))
    out.mkdir(parents=True, exist_ok=True)

    if a.names:
        expected = list(a.names)
        listing = None
    else:
        listing = run_script(uv, script, ["--list"], env)
        if listing.returncode != 0:
            sys.stderr.write(listing.stdout + listing.stderr)
            print("make_figures: the script failed to register its figures (see above)", file=sys.stderr)
            return listing.returncode or 1
        expected = [ln.strip() for ln in listing.stdout.splitlines() if ln.strip()]

    stamp = {p.name: p.stat().st_mtime_ns for p in out.glob("*.png")}
    args = (["--audit"] if a.audit else []) + list(a.names)
    r = run_script(uv, script, args, env)
    sys.stdout.write(r.stdout)
    if r.stderr.strip():
        sys.stderr.write(r.stderr)

    built, missing = [], []
    for name in expected:
        p = out / f"{name}.png"
        if p.is_file() and p.stat().st_size > 0:
            built.append({"name": name, "png": p.as_posix(), "bytes": p.stat().st_size,
                          "sha256": sha256(p), "rewritten": stamp.get(p.name) != p.stat().st_mtime_ns})
        else:
            missing.append(name)

    audit_hits: list[str] = []
    m = re.search(r"LAYOUT AUDIT - \d+ problem", r.stdout)
    if m:
        audit_hits = [ln.strip() for ln in r.stdout[m.end():].splitlines()[1:] if ln.strip()]

    rc = r.returncode
    if rc == 0 and missing:
        rc = 1
    report = {
        "script": script.as_posix(), "out": out.as_posix(), "registered": expected,
        "built": built, "missing": missing,
        "audit": {"enabled": a.audit, "hits": audit_hits}, "exit": rc, "ok": rc == 0,
    }
    for b in built:
        print(f"  sha256 {b['sha256'][:16]}  {b['name']}.png")
    if missing:
        print("MISSING PNG: " + ", ".join(missing), file=sys.stderr)
    if a.json:
        plat.write_json(a.json, report)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
