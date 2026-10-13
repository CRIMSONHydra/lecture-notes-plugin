# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Fetch one folder of an instructor's GitHub repo into a local copy + manifest.

    uv run fetch_code.py --repo OWNER/NAME --dir Lec-2 (--out DIR | --session N [--project DIR]) [--ref HEAD]
                         [--ignore GLOB ...] [--max-kb 1024] [--json OUT]

One `git/trees/<ref>?recursive=1` API call lists the repo; each file is then downloaded from
raw.githubusercontent.com (raw does not count against the API rate limit). `GITHUB_TOKEN` is used
if set. On HTTP 403/429 from the API it falls back to
`git clone --depth 1 --filter=blob:none --sparse`.

Fallback: when <dir> does not exist (no `Lec-4/`; the session files sit at the repo root), loose source
files at the repo root plus top-level folders whose name matches --title are fetched into `DIR/<dir>/root/...`
and marked `{"fallback": "root", "relevance": "unverified"}` on the manifest and on every fetched[] entry
(`last_commit` is not available from the tree / shallow clone; the digest-builder decides relevance).

Files land in `DIR/<dir>/...`; `DIR/manifest.json` records fetched[], skipped[{path,reason}],
tree_sha. Skipped: ignore globs, binaries (NUL byte in the first 8 KB), files over --max-kb.
(--max-kb defaults to 1024 here, not 512: the Lec-2 `main.preprocessed` is ~760 KB and is useful.)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import discover, guard, layout, platform as plat  # noqa: E402

SOURCE_EXT = {".cpp", ".cc", ".c", ".h", ".hpp", ".py", ".java", ".js", ".ts", ".rs", ".go", ".cs", ".kt",
              ".sql", ".sh", ".ipynb", ".txt", ".md"}
SKIP_ROOT = {"readme.md", "license", "license.md", "license.txt", ".gitignore", ".gitattributes"}
FALLBACK_MARK = {"fallback": "root", "relevance": "unverified"}


def fallback_select(entries: list[dict], title: str | None) -> tuple[list[dict], list[str]]:
    """Blobs to fetch when the session folder is missing: loose source files at the repo root, plus every blob
    under a top-level folder whose name matches ``title`` (discover similarity >= 0.5). -> (entries, folders)."""
    folders = []
    if title:
        for e in entries:
            if e["type"] == "tree" and "/" not in e["path"] and discover.similarity(e["path"], title) >= 0.5:
                folders.append(e["path"])
    sel = []
    for e in entries:
        if e["type"] != "blob":
            continue
        top = e["path"].split("/")[0]
        if "/" not in e["path"]:
            name = e["path"].lower()
            if name in SKIP_ROOT or PurePosixPath(name).suffix not in SOURCE_EXT:
                continue
            sel.append(e)
        elif top in folders:
            sel.append(e)
    return sel, folders

DEFAULT_IGNORE = [
    "*.o", "*.obj", "*.out", "*.exe", "*.class", "*.swp", ".*.swp", "*.swo", "*.pyc",
    "*.a", "*.so", "*.dll", "*.dylib", ".DS_Store", "*.pdb", "*.ilk",
]
UA = "lecture-notes-plugin/0.1 (fetch_code.py)"


class RateLimited(RuntimeError):
    pass


def http_get(url: str, *, token: str | None = None, accept: str | None = None, timeout: int = 30) -> bytes:
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise RateLimited(f"HTTP {e.code} from {url}") from e
        raise


def ignored(rel: str, globs: list[str]) -> str | None:
    name = PurePosixPath(rel).name
    for g in globs:
        if fnmatch.fnmatch(name, g) or fnmatch.fnmatch(rel, g):
            return f"ignored by glob {g!r}"
    return None


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _rmtree(path: Path) -> None:
    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)


def via_api(a, globs: list[str], out_dir: Path) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or None
    ref_q = urllib.parse.quote(a.ref, safe="")
    url = f"{a.api_base.rstrip('/')}/repos/{a.repo}/git/trees/{ref_q}?recursive=1"
    tree = json.loads(http_get(url, token=token, accept="application/vnd.github+json"))
    entries = tree.get("tree", [])
    prefix = a.dir.strip("/") + "/"
    top_dirs = sorted({e["path"].split("/")[0] for e in entries if e["type"] == "tree" and "/" not in e["path"]})
    dir_entry = next((e for e in entries if e["path"] == a.dir.strip("/") and e["type"] == "tree"), None)
    manifest = {
        "repo": a.repo, "ref": a.ref, "dir": a.dir, "method": "api",
        "tree_sha": tree.get("sha"), "dir_sha": dir_entry["sha"] if dir_entry else None,
        "truncated": bool(tree.get("truncated")), "available_dirs": top_dirs,
        "fetched": [], "skipped": [],
    }
    if dir_entry:
        selected = [e for e in entries if e["type"] == "blob" and e["path"].startswith(prefix)]
    else:
        selected, folders = fallback_select(entries, getattr(a, "title", None))
        if not selected:
            manifest["error"] = f"folder {a.dir!r} not found in {a.repo}@{a.ref}"
            return manifest
        manifest.update(FALLBACK_MARK, fallback_folders=folders, last_commit="not available (tree only)",
                        note=f"no {a.dir!r} folder: loose root files / title-matched folders; relevance unverified")
    max_bytes = int(a.max_kb * 1024)
    for e in selected:
        path = e["path"]
        rel = path[len(prefix):] if dir_entry else "root/" + path
        why = ignored(rel, globs)
        if why:
            manifest["skipped"].append({"path": path, "reason": why})
            continue
        size = int(e.get("size") or 0)
        if size > max_bytes:
            manifest["skipped"].append(
                {"path": path, "reason": f"oversize ({size / 1024:.0f} KB > {a.max_kb:g} KB)"})
            continue
        raw_url = "{}/{}/{}/{}".format(
            a.raw_base.rstrip("/"), a.repo, ref_q, "/".join(urllib.parse.quote(s) for s in path.split("/")))
        data = http_get(raw_url)
        if is_binary(data):
            manifest["skipped"].append({"path": path, "reason": "binary (NUL byte in first 8 KB)"})
            continue
        dest = out_dir / a.dir.strip("/") / Path(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        manifest["fetched"].append({"path": path, "local": dest.relative_to(out_dir).as_posix(),
                                    "bytes": len(data), "sha": e.get("sha"),
                                    **({} if dir_entry else FALLBACK_MARK)})
    return manifest


def via_clone(a, globs: list[str], out_dir: Path) -> dict:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git not found for the clone fallback")
    tmp = Path(tempfile.mkdtemp(prefix=".clone-", dir=out_dir))
    try:
        cmd = [git, "clone", "-c", "core.autocrlf=false", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse"]
        if a.ref not in ("HEAD", ""):
            cmd += ["--branch", a.ref]
        url = a.clone_url or f"https://github.com/{a.repo}.git"
        subprocess.run(cmd + [url, str(tmp / "repo")], check=True, capture_output=True, text=True, timeout=300)
        repo = tmp / "repo"
        subprocess.run([git, "-C", str(repo), "sparse-checkout", "set", a.dir.strip("/")],
                       check=True, capture_output=True, text=True, timeout=300)
        sha = subprocess.run([git, "-C", str(repo), "rev-parse", f"HEAD:{a.dir.strip('/')}"],
                             capture_output=True, text=True).stdout.strip() or None
        root_sha = subprocess.run([git, "-C", str(repo), "rev-parse", "HEAD^{tree}"],
                                  capture_output=True, text=True).stdout.strip() or None
        manifest = {"repo": a.repo, "ref": a.ref, "dir": a.dir, "method": "git-clone",
                    "tree_sha": root_sha, "dir_sha": sha, "truncated": False, "available_dirs": [],
                    "fetched": [], "skipped": []}
        src_dir = repo / a.dir.strip("/")
        fallback = not src_dir.is_dir()
        if fallback:
            ls = subprocess.run([git, "-C", str(repo), "ls-tree", "HEAD"], capture_output=True, text=True).stdout
            ents = []
            for ln in ls.splitlines():
                meta, _, name = ln.partition("\t")
                ents.append({"type": "tree" if meta.split()[1] == "tree" else "blob", "path": name.strip('"')})
            _sel, folders = fallback_select(ents, getattr(a, "title", None))
            if folders:
                subprocess.run([git, "-C", str(repo), "sparse-checkout", "set", *folders],
                               check=True, capture_output=True, text=True, timeout=300)
            files = [(repo / e["path"]) for e in _sel if "/" not in e["path"]] + \
                    [f for d in folders for f in sorted((repo / d).rglob("*")) if f.is_file()]
            if not files:
                manifest["error"] = f"folder {a.dir!r} not found in {a.repo}@{a.ref}"
                return manifest
            manifest.update(FALLBACK_MARK, fallback_folders=folders, last_commit="not available (shallow clone)",
                            note=f"no {a.dir!r} folder: loose root files / title-matched folders; relevance unverified")
        else:
            files = sorted(p for p in src_dir.rglob("*") if p.is_file())
        max_bytes = int(a.max_kb * 1024)
        for f in files:
            if fallback:
                rel = "root/" + f.relative_to(repo).as_posix()
                path = f.relative_to(repo).as_posix()
            else:
                rel = f.relative_to(src_dir).as_posix()
                path = f"{a.dir.strip('/')}/{rel}"
            why = ignored(rel, globs)
            if why:
                manifest["skipped"].append({"path": path, "reason": why})
                continue
            size = f.stat().st_size
            if size > max_bytes:
                manifest["skipped"].append({"path": path, "reason": f"oversize ({size / 1024:.0f} KB > {a.max_kb:g} KB)"})
                continue
            data = f.read_bytes()
            if is_binary(data):
                manifest["skipped"].append({"path": path, "reason": "binary (NUL byte in first 8 KB)"})
                continue
            dest = out_dir / a.dir.strip("/") / Path(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            manifest["fetched"].append({"path": path, "local": dest.relative_to(out_dir).as_posix(),
                                        "bytes": len(data), "sha": None,
                                        **(FALLBACK_MARK if fallback else {})})
        return manifest
    finally:
        _rmtree(tmp)


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="OWNER/NAME")
    ap.add_argument("--dir", required=True, help="folder in the repo, e.g. Lec-2")
    ap.add_argument("--out", help="local directory (the folder is placed at OUT/<dir>); default <session>/code")
    ap.add_argument("--session", help="session number (used with --project for the default --out)")
    ap.add_argument("--project", help="project dir (default: discovered from cwd)")
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--title", default=None,
                    help="session title: when --dir is missing, folders whose name matches it are fetched too")
    ap.add_argument("--ignore", nargs="*", default=[], metavar="GLOB",
                    help="extra globs to skip (added to the built-in artefact list)")
    ap.add_argument("--max-kb", type=float, default=1024.0)
    ap.add_argument("--json", metavar="OUT", help="also write the manifest here")
    ap.add_argument("--data-dir")
    ap.add_argument("--api-base", default="https://api.github.com", help=argparse.SUPPRESS)
    ap.add_argument("--raw-base", default="https://raw.githubusercontent.com", help=argparse.SUPPRESS)
    ap.add_argument("--clone-url", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--force-clone", action="store_true", help="skip the API; use the git sparse clone")
    a = ap.parse_args(argv)

    try:
        out_dir = layout.resolve_out(a.out, a.project, a.session, "code")
    except layout.ProjectError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    ddir = plat.resolve_data_dir(a.data_dir)
    try:
        guard.assert_writable(out_dir / "manifest.json", ddir)
    except guard.GuardError as e:
        print(e, file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    globs = DEFAULT_IGNORE + list(a.ignore or [])

    try:
        if a.force_clone:
            raise RateLimited("--force-clone")
        manifest = via_api(a, globs, out_dir)
    except RateLimited as e:
        print(f"note: GitHub API refused ({e}); falling back to git sparse clone", file=sys.stderr)
        try:
            manifest = via_clone(a, globs, out_dir)
        except Exception as e2:  # noqa: BLE001
            print(f"error: clone fallback failed: {e2}", file=sys.stderr)
            return 1
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    manifest["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plat.write_json(out_dir / "manifest.json", manifest)
    if a.json:
        guard.assert_writable(a.json, ddir)
        plat.write_json(a.json, manifest)

    if manifest.get("error"):
        print(f"error: {manifest['error']}; folders in repo: {', '.join(manifest['available_dirs']) or '?'}",
              file=sys.stderr)
        return 1
    if manifest.get("fallback"):
        print(f"note: {a.dir!r} not in the repo: fetched {len(manifest['fetched'])} root/title-matched file(s), "
              "marked relevance=unverified", file=sys.stderr)
    print(f"{a.repo}@{a.ref}  {a.dir}  via {manifest['method']}  tree {str(manifest['dir_sha'])[:10]}")
    for f in manifest["fetched"]:
        print(f"  fetched  {f['path']:<40} {f['bytes']:>9,} B")
    for s in manifest["skipped"]:
        print(f"  skipped  {s['path']:<40} {s['reason']}")
    print(f"manifest -> {out_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
