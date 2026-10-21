# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Environment report for the `notes` plugin.

    uv run doctor.py [--project DIR] [--data-dir D] [--json OUT] [--clear-guard [RUN]] [--no-network]

Checks uv, a Chromium-family browser (needed for PDFs), GPU/Whisper/OCR acceleration hints,
GitHub reachability, C/C++ compilers, cached KaTeX/Mermaid assets, the read-only guard, and the
project (discovered from cwd or --project: profile, resources/, notes/, pdf/). A missing project is advisory.
Exit code 1 only if no browser is found; everything else is advisory.
"""
from __future__ import annotations

import argparse
import json
import os
import platform as stdlib_platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import chrome, guard, layout, layout_check, ledger, platform as plat, profile as prof  # noqa: E402

GPU_ADVICE = "switch the NVIDIA GPU on for ~70× realtime Whisper"


def _run_version(exe: str, arg: str = "--version") -> str | None:
    try:
        r = subprocess.run([exe, arg], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    out = (r.stdout or r.stderr or "").strip().splitlines()
    return out[0].strip() if out else None


def _version_token(line: str | None) -> str | None:
    if not line:
        return None
    toks = re.findall(r"\d+\.\d+(?:\.\d+)?", line)
    return toks[-1] if toks else None


def check_uv() -> dict:
    exe = shutil.which("uv")
    if not exe:
        cand = Path.home() / ".local" / "bin" / ("uv.exe" if plat.os_kind() == "windows" else "uv")
        exe = str(cand) if cand.is_file() else None
    if not exe:
        return {"ok": False, "path": None, "version": None,
                "hint": "install uv: https://docs.astral.sh/uv/getting-started/installation/"}
    ver = _run_version(exe)
    return {"ok": True, "path": Path(exe).as_posix(), "version": _version_token(ver)}


def check_chrome(data_dir: Path) -> dict:
    try:
        p = chrome.find_chrome(data_dir=data_dir)
        return {"ok": True, "path": p.as_posix(), "hint": None}
    except chrome.ChromeNotFound as e:
        return {"ok": False, "path": None, "hint": str(e)}


def check_compilers() -> dict:
    out = {}
    for name in ("g++", "clang++"):
        exe = shutil.which(name)
        if exe:
            out[name] = {"path": Path(exe).as_posix(), "version": _version_token(_run_version(exe)),
                         "banner": _run_version(exe)}
        else:
            out[name] = None
    return out


def check_github(timeout: float = 5.0) -> dict:
    """GET /rate_limit (does not consume quota). Falls back to raw.githubusercontent.com."""
    res = {"ok": False, "api": False, "raw": False, "rate_remaining": None, "rate_limit": None}
    try:
        req = urllib.request.Request("https://api.github.com/rate_limit",
                                     headers={"User-Agent": "lecture-notes-plugin-doctor"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            core = json.loads(r.read())["resources"]["core"]
            res.update(api=True, rate_remaining=core.get("remaining"), rate_limit=core.get("limit"))
    except Exception:
        pass
    try:
        req = urllib.request.Request("https://raw.githubusercontent.com/", method="HEAD",
                                     headers={"User-Agent": "lecture-notes-plugin-doctor"})
        urllib.request.urlopen(req, timeout=timeout)
        res["raw"] = True
    except urllib.error.HTTPError:
        res["raw"] = True  # a 4xx still proves the host is reachable
    except Exception:
        pass
    res["ok"] = res["api"] or res["raw"]
    return res


def check_assets(data_dir: Path) -> dict:
    d = data_dir / "cache" / "assets"
    found = {"katex": False, "mermaid": False, "dir": d.as_posix()}
    if d.is_dir():
        names = [p.name.lower() for p in d.iterdir()]
        found["katex"] = any("katex" in n for n in names)
        found["mermaid"] = any("mermaid" in n for n in names)
    return found


def whisper_fast(gpu: dict) -> dict:
    """Heuristic only: whether a fast Whisper backend is *likely* available (libraries not probed)."""
    if gpu["nvidia"] and plat.os_kind() in ("windows", "linux"):
        return {"backend": "cuda", "heuristic": True}
    if gpu["apple_silicon"]:
        return {"backend": "mlx", "heuristic": True}
    return {"backend": "none", "heuristic": True}


def ocr_accel() -> dict:
    kind = plat.os_kind()
    return {"provider": {"windows": "directml", "macos": "coreml"}.get(kind, "cpu"), "heuristic": True}


def check_guard(data_dir: Path) -> dict:
    gs = [{"run_id": g.get("run_id"), "created": g.get("created"), "expires": g.get("expires"),
           "protect": g.get("protect"), "allow": g.get("allow")} for g in guard.load_guards(data_dir)]
    if not gs:
        return {"active": False, "guards": []}
    return {"active": True, **gs[0], "guards": gs}          # top-level fields: the newest run's guard


def check_project(project=None, data_dir=None) -> dict:
    migrated: list[str] = []
    try:
        root = layout.find_project(explicit=project, migrate=False)
        migrated = layout.migrate_legacy(root)       # PLAN 13.1: .notes/ -> .notes-plugin-internal/
        lay = layout.Layout(root)
    except layout.ProjectError as e:
        return {"found": False, "hint": str(e)}
    out: dict = {"found": True, "root": lay.root.as_posix(), "profile": lay.profile_path.as_posix(),
                 "migrated": migrated, "legacy_dir_left": (lay.root / layout.LEGACY_INTERNAL).is_dir()
                 and (lay.root / layout.LEGACY_PROFILE_REL).is_file()}
    st = ledger.load(lay)
    out["ledger"] = {"exists": lay.state_path.is_file(), "sessions": len(st["sessions"])}
    try:
        p = prof.load_profile(None, data_dir=data_dir, project=lay.root)
    except prof.ProfileError as e:
        out.update(profile_ok=False, hint=str(e))
        return out
    res = lay.resources(p)
    gi = lay.root / ".gitignore"
    out.update(profile_ok=True, id=p["id"], resources=res.as_posix(), resources_exists=res.is_dir(),
               sessions=len(p.get("sessions") or []),
               notes={t: lay.notes(t).is_dir() for t in layout.TIERS},
               pdf={t: lay.pdf(t).is_dir() for t in layout.TIERS},
               gitignore_work=gi.is_file() and layout.GITIGNORE_LINE in [x.strip() for x in gi.read_text(encoding="utf-8-sig", errors="replace").splitlines()],
               prior_notes_external=[d for d in p.get("prior_notes_external") or []])
    try:
        out["outside_resources"] = layout_check.check(lay.root, p)       # PLAN 19: class files outside resources/
    except Exception:  # noqa: BLE001 - advisory only
        out["outside_resources"] = []
    return out


def build_report(data_dir: Path, network: bool = True, project=None) -> dict:
    gpu = plat.gpu_summary()
    rep = {
        "os": plat.os_kind(),
        "os_detail": stdlib_platform.platform(),
        "arch": stdlib_platform.machine(),
        "plugin_root": plat.plugin_root().as_posix(),
        "data_dir": data_dir.as_posix(),
        "uv": check_uv(),
        "chrome": check_chrome(data_dir),
        "gpu": {"nvidia": gpu["nvidia"], "name": gpu["nvidia_name"], "apple_silicon": gpu["apple_silicon"]},
        "whisper_fast": whisper_fast(gpu),
        "ocr_accel": ocr_accel(),
        "network": {"github": None},
        "compilers": check_compilers(),
        "assets": check_assets(data_dir),
        "guard": check_guard(data_dir),
        "project": check_project(project, data_dir),
        "advice": [],
    }
    if network:
        gh = check_github()
        rep["network"] = {"github": gh["ok"], "github_detail": gh}
    if not rep["chrome"]["ok"]:
        rep["advice"].append(rep["chrome"]["hint"])
    if not gpu["nvidia"] and plat.os_kind() in ("windows", "linux"):
        rep["advice"].append(GPU_ADVICE + " (no NVIDIA GPU is visible; Whisper would run on the CPU, ~1 h for a 2 h class)")
    pj = rep["project"]
    if not pj["found"]:
        rep["advice"].append(pj["hint"])
    elif not pj.get("profile_ok"):
        rep["advice"].append(f"project profile problem: {pj['hint']}")
    elif not pj["resources_exists"]:
        rep["advice"].append(f"resources dir missing: {pj['resources']}")
    if pj.get("outside_resources"):
        rep["advice"].append(f"{len(pj['outside_resources'])} class file(s) sit outside resources/ and are ignored: "
                             "run /notes:scan (it offers to move them)")
    if pj.get("migrated"):
        rep["advice"].append("migrated the legacy project folder: " + "; ".join(pj["migrated"]))
    if pj.get("legacy_dir_left"):
        rep["advice"].append("a stale .notes/ folder is still there next to .notes-plugin-internal/: "
                             "the plugin ignores it; delete it once you have checked nothing is needed")
    if rep["guard"]["active"]:
        rep["advice"].append("a read-only guard is active (a run is in progress, or it is stale): "
                             "clear it with --clear-guard" + (" RUN" if len(rep["guard"]["guards"]) > 1 else ""))
    return rep


def _mark(ok) -> str:
    return {True: "OK  ", False: "FAIL", None: "--  "}[ok]


def print_table(r: dict) -> None:
    rows = []
    rows.append(("os", _mark(True), f"{r['os']} / {r['arch']}  ({r['os_detail']})"))
    uv = r["uv"]
    rows.append(("uv", _mark(uv["ok"]), f"{uv['version']}  {uv['path']}" if uv["ok"] else uv["hint"]))
    c = r["chrome"]
    rows.append(("chrome", _mark(c["ok"]), c["path"] if c["ok"] else "NOT FOUND"))
    g = r["gpu"]
    rows.append(("gpu", _mark(True if (g["nvidia"] or g["apple_silicon"]) else None),
                 g["name"] if g["nvidia"] else ("Apple Silicon" if g["apple_silicon"] else "no NVIDIA GPU visible")))
    rows.append(("whisper (likely)", "--  ", r["whisper_fast"]["backend"]))
    rows.append(("ocr accel (likely)", "--  ", r["ocr_accel"]["provider"]))
    gh = r["network"]["github"]
    det = r["network"].get("github_detail") or {}
    rows.append(("github", _mark(gh),
                 "skipped" if gh is None else
                 (f"reachable, API quota {det.get('rate_remaining')}/{det.get('rate_limit')}" if gh else "unreachable")))
    for name, v in r["compilers"].items():
        rows.append((name, _mark(bool(v)) if v else "--  ", f"{v['version']}  {v['path']}" if v else "not found"))
    a = r["assets"]
    rows.append(("katex cache", _mark(a["katex"]) if a["katex"] else "--  ", "cached" if a["katex"] else "downloaded on first build"))
    rows.append(("mermaid cache", _mark(a["mermaid"]) if a["mermaid"] else "--  ", "cached" if a["mermaid"] else "downloaded on first use"))
    gd = r["guard"]
    rows.append(("guard", "--  ", "; ".join(f"ACTIVE until {g['expires']} (run {g.get('run_id') or '-'})"
                                         for g in gd["guards"]) if gd["active"] else "inactive"))
    pj = r["project"]
    if pj["found"]:
        rows.append(("project", _mark(bool(pj.get("profile_ok"))),
                     f"{pj['root']}  profile {pj.get('id', '?')}, resources "
                     f"{'ok' if pj.get('resources_exists') else 'MISSING'}" if pj.get("profile_ok") else pj["hint"]))
    else:
        rows.append(("project", "--  ", "none (run /notes:init in your subject folder)"))
    w = max(len(x[0]) for x in rows)
    print(f"notes plugin doctor  (data dir: {r['data_dir']})")
    for name, mk, text in rows:
        print(f"  {name:<{w}}  {mk}  {text}")
    for adv in r["advice"]:
        print(f"  ! {adv}")


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir")
    ap.add_argument("--project", help="project dir (default: discovered from cwd; absent is fine)")
    ap.add_argument("--json", metavar="OUT", help="write the report as JSON")
    ap.add_argument("--clear-guard", nargs="?", const=True, default=False, metavar="RUN",
                    help="remove a stale read-only guard (name the run when several are active)")
    ap.add_argument("--no-network", action="store_true", help="skip the GitHub reachability probe")
    a = ap.parse_args(argv)

    ddir = plat.resolve_data_dir(a.data_dir)
    if a.clear_guard is not False:
        active = guard.load_guards(ddir)
        if a.clear_guard is True and len(active) > 1:
            print("several runs are guarded; name the stale one with --clear-guard RUN:")
            for g in active:
                print(f"  {g.get('run_id') or '(anonymous)'}  expires {g.get('expires')}")
        else:
            run = None if a.clear_guard is True else a.clear_guard
            had = guard.clear_guard(ddir, run)
            print("guard cleared" if had else "no guard was active")
    rep = build_report(ddir, network=not a.no_network, project=a.project)
    print_table(rep)
    if a.json:
        guard.assert_writable(a.json, ddir)
        plat.write_json(a.json, rep)
        print(f"json -> {a.json}")
    return 0 if rep["chrome"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
