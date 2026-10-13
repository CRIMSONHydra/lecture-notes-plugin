# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""Compile and run the ``{run}`` / ``{run nocheck}`` / ``{compile}`` fences of a note and compare output.

    uv run check_snippets.py <note.md>... [--project DIR] [--profile REF] [--work DIR] [--timeout 10] [--json OUT]

Fences (info string ``<lang> {attrs}``):

    ```cpp {run}          compile, run, compare stdout with the ```text block within 2 lines after it
    ```cpp {run nocheck}  compile and run, never compare (platform-dependent output)
    ```cpp {compile}      compile only

Compile/run command lines come from the profile's ``snippets.<lang>`` table (``ext``, ``compile``,
``run``; placeholders ``{src} {exe} {dir}``). Snippets are written to ``<tier>/build/snippets/<stem>/<k>.<ext>``
(or ``<DIR>/<stem>/`` with --work). In the expected block, lines starting with ``$ `` are commands and are
dropped; CRLF and trailing spaces are normalised. A non-zero exit status is recorded, not a mismatch.
A missing toolchain (or no template for the language) is ``skipped`` with a reason, never a failure.
Cache (PLAN 14.1): a PASSING snippet is remembered in ``<work>/cache.json`` keyed by the hash of its
code, expected output, mode and the profile's compile/run template; an unchanged snippet is never
recompiled (``cached`` in the JSON, ``CACHE`` in the log). Failures and skips are never cached.
``--no-cache`` disables it.
Exit 1 if any snippet fails to compile, times out or mismatches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from notes_lib import guard, layout, notefile, platform as plat, profile as prof  # noqa: E402

plat.utf8_stdio()

ATTR_RE = re.compile(r"\{([^}]*)\}")
EXE_EXT = ".exe" if os.name == "nt" else ".bin"


def parse_info(info: str) -> tuple[str, set[str]]:
    info = (info or "").strip()
    lang = info.split()[0].lower() if info and not info.startswith("{") else ""
    attrs: set[str] = set()
    for m in ATTR_RE.finditer(info):
        attrs.update(m.group(1).split())
    return lang, attrs


def normalise(text: str) -> str:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if not ln.lstrip().startswith("$ ")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def split_cmd(template: str, **vals: str) -> list[str]:
    quoted = {k: shlex.quote(Path(v).as_posix()) for k, v in vals.items()}
    return shlex.split(template.format(**quoted), posix=True)


def extract(note: notefile.Note) -> list[dict]:
    """Snippet fences of a note with their expected-output block (if any)."""
    blocks = [b for b in notefile.iter_blocks_ex(note.body) if b[0] == "code"]
    starts = {b[3]: b for b in blocks}
    off = note.raw[: len(note.raw) - len(note.body)].count(chr(10)) if note.raw.endswith(note.body) else 0
    out, k = [], 0
    for kind, text, info, line in blocks:
        lang, attrs = parse_info(info)
        if not ({"run", "compile"} & attrs):
            continue
        k += 1
        mode = "compile" if "compile" in attrs and "run" not in attrs else \
               ("run-nocheck" if "nocheck" in attrs else "run")
        end = line + len(text.split("\n")) + 1            # closing fence line
        expected = None
        if mode == "run":
            for d in (1, 2, 3):
                nxt = starts.get(end + d)
                if nxt:
                    if parse_info(nxt[2])[0] == "text":
                        expected = nxt[1]
                    break
        out.append({"k": k, "line": line + off, "lang": lang, "mode": mode, "code": text, "expected": expected})
    return out


def cache_key(sn: dict, table: dict) -> str:
    tmpl = table.get(sn["lang"]) or {}
    blob = json.dumps([sn["lang"], sn["mode"], sn["code"], sn["expected"], tmpl.get("compile"), tmpl.get("run"),
                       tmpl.get("ext")], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tail(s: str, n: int = 12) -> str:
    return "\n".join(s.strip().splitlines()[:n])


def check_one(sn: dict, snipdir: Path, table: dict, timeout: float) -> dict:
    res = {"k": sn["k"], "line": sn["line"], "lang": sn["lang"], "mode": sn["mode"],
           "status": "pass", "reason": "", "exit_code": None, "expected": None, "actual": None}
    tmpl = table.get(sn["lang"])
    if not tmpl:
        res.update(status="skipped", reason=f"profile has no snippets.{sn['lang'] or '?'} template")
        return res
    ext = tmpl.get("ext", f".{sn['lang']}")
    src = snipdir / f"{sn['k']}{ext}"
    exe = snipdir / f"{sn['k']}{EXE_EXT}"
    guard.assert_writable(src)
    src.write_text(sn["code"] + "\n", encoding="utf-8", newline="\n")
    res["src"] = src.as_posix()
    vals = {"src": str(src), "exe": str(exe), "dir": str(snipdir)}
    try:
        cmds = []
        if tmpl.get("compile"):
            cmds.append(("compile", split_cmd(tmpl["compile"], **vals)))
        if sn["mode"] != "compile":
            cmds.append(("run", split_cmd(tmpl.get("run") or "{exe}", **vals)))
    except (KeyError, ValueError) as e:
        res.update(status="fail", reason=f"bad snippets.{sn['lang']} template: {e}")
        return res
    first = cmds[0][1]                  # the compiler (or the interpreter when there is no compile step)
    if shutil.which(first[0]) is None and not Path(first[0]).exists():
        res.update(status="skipped", reason=f"toolchain not found: `{first[0]}` is not on PATH")
        return res
    cwd = str(snipdir)
    for what, argv in cmds:
        try:
            r = subprocess.run(argv, cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL,
                               timeout=max(timeout * 3, 30) if what == "compile" else timeout)
        except subprocess.TimeoutExpired:
            res.update(status="fail", reason=f"{what} timed out")
            return res
        except OSError as e:
            res.update(status="skipped", reason=f"cannot execute `{argv[0]}`: {e}")
            return res
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        if what == "compile":
            if r.returncode != 0:
                res.update(status="fail", reason="compile error:\n" + tail(err or out))
                return res
            continue
        res["exit_code"] = r.returncode
        res["actual"] = normalise(out)
        if sn["mode"] == "run":
            if sn["expected"] is None:
                res.update(status="fail", reason="{run} fence has no ```text output block within 2 lines")
                return res
            want = normalise(sn["expected"])
            res["expected"] = want
            if res["actual"] != want and normalise(out + err) != want:
                res.update(status="fail", reason="output mismatch")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("notes", nargs="+")
    ap.add_argument("--project", default=None, help="project dir (default: discovered from cwd)")
    ap.add_argument("--profile", default=None, help="profile id/path (default: <project>/.notes-plugin-internal/profile.md)")
    ap.add_argument("--work", default=None, help="snippet work dir (default <project>/.notes-plugin-internal/work/build/<tier>/snippets)")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--no-cache", action="store_true", help="recompile every snippet")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    profile = prof.load_or_exit(a.profile, data_dir=a.data_dir, project=a.project, notes=a.notes)
    table = profile.get("snippets") or {}
    results: list[dict] = []
    for npath in a.notes:
        note = notefile.parse_note(plat.norm(npath))
        stem = note.path.stem
        lay, tier = layout.infer_from_note(note.path)
        base = plat.norm(a.work) if a.work else (lay.build(tier) if lay else note.path.parent.parent / "build") / "snippets"
        snipdir = base / stem
        snippets = extract(note)
        cpath = base / "cache.json"
        try:
            cache = json.loads(cpath.read_text(encoding="utf-8-sig")) if not a.no_cache else {}
        except (OSError, ValueError):
            cache = {}
        dirty = False
        if snippets:
            guard.assert_writable(snipdir)
            snipdir.mkdir(parents=True, exist_ok=True)
        for sn in snippets:
            key = cache_key(sn, table)
            hit = cache.get(key)
            if hit and hit.get("status") == "pass":
                r = dict(hit, k=sn["k"], line=sn["line"], cached=True)
            else:
                r = check_one(sn, snipdir, table, a.timeout)
                r["cached"] = False
                if r["status"] == "pass" and not a.no_cache:
                    cache[key] = {k: v for k, v in r.items() if k not in ("k", "line", "src", "cached")}
                    dirty = True
            r.update(note=note.path.as_posix(), stem=stem)
            results.append(r)
            tag = "CACHE" if r["cached"] else {"pass": "PASS", "fail": "FAIL", "skipped": "SKIP"}[r["status"]]
            print(f"{tag} {stem} #{r['k']} (line {r['line']}, {r['lang']} {r['mode']})"
                  + (f": {r['reason'].splitlines()[0]}" if r["reason"] else ""))
            if r["status"] == "fail" and r["expected"] is not None and r["actual"] is not None:
                print(f"     expected: {r['expected']!r}\n     actual:   {r['actual']!r}")
            elif r["status"] == "fail" and "\n" in r["reason"]:
                print("     " + r["reason"].split("\n", 1)[1].replace("\n", "\n     "))
        if dirty:
            # parallel runs share this cache: merge what others wrote meanwhile, then replace atomically
            try:
                cur = json.loads(cpath.read_text(encoding="utf-8-sig"))
                cache = {**cur, **cache} if isinstance(cur, dict) else cache
            except (OSError, ValueError):
                pass
            tmp = cpath.with_name(f"cache.{os.getpid()}.tmp")
            plat.write_json(tmp, cache)
            os.replace(tmp, cpath)
    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "fail", "skipped")}
    print(f"\nsnippets: {counts['pass']} pass, {counts['fail']} fail, {counts['skipped']} skipped")
    if a.json:
        guard.assert_writable(plat.norm(a.json))
        plat.write_json(a.json, {"ok": counts["fail"] == 0, "counts": counts, "snippets": results})
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
