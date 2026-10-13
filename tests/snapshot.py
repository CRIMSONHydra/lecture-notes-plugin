# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Filesystem snapshot + diff, used to prove a run wrote nothing into a read-only tree.

    uv run snapshot.py take <root> --out F.json [--exclude NAME ...]
    uv run snapshot.py diff A.json B.json          # exit 1 on any difference

Records path (relative, posix), size, mtime_ns and (files under 64 MB) sha256. Directories named
`.git` are excluded by default: an editor's background fetch rewrites FETCH_HEAD and friends.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

BIG = 64 * 1024 * 1024
DEFAULT_EXCLUDE = {".git"}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def take(root: Path, exclude: set[str]) -> dict:
    files = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in exclude]
        for n in fn:
            p = Path(dp) / n
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            e = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
            if st.st_size < BIG:
                try:
                    e["sha256"] = sha(p)
                except OSError:
                    pass
            files[rel] = e
    return {"root": root.as_posix(), "exclude": sorted(exclude), "count": len(files), "files": files}


def diff(a: dict, b: dict) -> list[str]:
    fa, fb = a["files"], b["files"]
    out = [f"ADDED    {k}" for k in sorted(fb.keys() - fa.keys())]
    out += [f"REMOVED  {k}" for k in sorted(fa.keys() - fb.keys())]
    for k in sorted(fa.keys() & fb.keys()):
        if fa[k] != fb[k]:
            out.append(f"CHANGED  {k}  {fa[k]} -> {fb[k]}")
    return out


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("take")
    t.add_argument("root")
    t.add_argument("--out", required=True)
    t.add_argument("--exclude", action="append", default=[])
    d = sub.add_parser("diff")
    d.add_argument("a")
    d.add_argument("b")
    a = ap.parse_args(argv)
    if a.cmd == "take":
        snap = take(Path(a.root), DEFAULT_EXCLUDE | set(a.exclude))
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(snap, indent=1), encoding="utf-8")
        print(f"snapshot: {snap['count']} files under {a.root} -> {a.out}")
        return 0
    A = json.loads(Path(a.a).read_text(encoding="utf-8"))
    B = json.loads(Path(a.b).read_text(encoding="utf-8"))
    lines = diff(A, B)
    for ln in lines:
        print(ln)
    print(f"diff: {len(lines)} difference(s)" if lines else "diff: identical")
    return 1 if lines else 0


if __name__ == "__main__":
    raise SystemExit(main())
