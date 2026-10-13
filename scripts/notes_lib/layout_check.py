"""Find class inputs dropped OUTSIDE ``resources/`` (PLAN section 19). Stdlib only.

Discovery only looks inside ``resources/``, so a transcript in the project root, a ``videos/`` folder at the
root or slides in a ``Downloads``-style subfolder are silently ignored. :func:`check` walks the project and
returns, for every file that *looks like a class input*, ``{path, rel, kind, suggested_destination, reason}``.
:func:`fix` moves exactly those files (never overwriting; a name clash refuses that file).

Conservative by design - a file is flagged only on a positive signal, using the same vocabulary as
:mod:`notes_lib.discover`:

* recording   a video/audio extension (a ``.ts`` only when it is in a ``videos/`` folder or >= 1 MB, as discover)
* transcript  ``.vtt`` / ``.srt``; a ``.txt``/``.md`` whose head parses as a transcript header, or whose name is
              Scaler-style (``..._-_Class_Scaler_Academy``, ``transcript``, ``whisper``)
* slides / syllabus / handout   ``.pdf .pptx .docx .key ...`` that discover would classify as slides or syllabus,
              or whose name matches a profile session title
* data / exercise   only with a class number in the name (or its stray parent folder) or a session-title match

Never walked: ``.git``, ``.notes-plugin-internal``, ``resources`` (the configured one), ``notes/<tier>``,
``pdf/<tier>``, dot-folders, ``node_modules``, virtualenvs, caches, build output, well-known user code folders
(``Classwork``, ``src``, ...) and any folder that carries a project marker (``pyproject.toml``,
``package.json``, ``CMakeLists.txt``, ``Makefile``, ...). Inside ``notes/`` and ``pdf/`` (their own top level) only
recordings and transcripts are flagged. Recordings anywhere under ``resources/`` are fine: discover walks it
recursively, so nothing under ``resources/`` is ever flagged.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import discover, layout, platform as plat, transcript as tr

SKIP_DIRS = {".git", ".hg", ".svn", layout.INTERNAL, layout.LEGACY_INTERNAL, "node_modules", "__pycache__",
             ".venv", "venv", "env", ".tox", ".idea", ".vscode", "site-packages", "build", "dist", "target",
             "out", "bin", "obj", "classwork", "class-work", "class_work", "code", "src", "include", "lib",
             "test-output", "graphify-out", "figures"}
PROJECT_MARKERS = ("pyproject.toml", "package.json", "CMakeLists.txt", "Makefile", "Cargo.toml", "go.mod",
                   "pom.xml", "build.gradle", "requirements.txt", "setup.py", "Gemfile")
IGNORE_FILES = discover.IGNORE_NAMES | {"claude.md", "license", "license.md", "readme.md", "changelog.md",
                                        "contributing.md", ".gitignore", ".gitattributes"}
GENERIC_FOLDERS = {"downloads", "download", "videos", "video", "recordings", "recording", "slides", "transcripts",
                   "transcript", "handouts", "files", "class", "classes", "lectures", "lecture", "misc", "new",
                   "documents", "docs", "desktop", "temp", "tmp", "data", "inputs", "input"}
SCALER_NAME_RE = re.compile(r"Scaler[_ -]*Academy|transcript|whisper", re.I)
MAX_ENTRIES = 20000
MAX_DEPTH = 6
TOP_ONLY = ("notes", "pdf")     # their own top level only: recordings and transcripts only


class LayoutError(RuntimeError):
    pass


def _is_code_dir(d: Path) -> bool:
    try:
        return any((d / m).exists() for m in PROJECT_MARKERS) or any(d.glob("*.sln"))
    except OSError:
        return False


def _title_match(stem: str, sessions: list[dict]) -> bool:
    toks = discover.tokens(discover.strip_session_refs(stem))
    if not toks:
        return False
    return any(discover.similarity(stem, str(s.get("title") or "")) >= discover.ASSIGN_MIN
               for s in sessions if s.get("title")) or any(
        discover.stem_key(stem) == discover.stem_key(str(s["stem"])) for s in sessions if s.get("stem"))


def _is_transcript(p: Path) -> bool:
    ext, low = p.suffix.lower(), p.name.lower()
    if ext in (".vtt", ".srt"):
        return True
    if ext not in (".txt", ".md"):
        return False
    head = discover._read_head(p)  # noqa: SLF001
    if tr.RULE_RE.search(head):
        hdr, _ = tr.parse_header(head + "\n")
        if hdr.get("has_header") and hdr.get("lecture_title"):
            return True
    return bool(SCALER_NAME_RE.search(p.stem)) and (p.stat().st_size >= 512 or ext == ".md")


def classify_outside(p: Path, rel: str, sessions: list[dict], strict: bool) -> tuple[str, str] | None:
    """(kind, reason) when ``p`` looks like a class input, else None. ``strict``: recordings/transcripts only."""
    ext, low = p.suffix.lower(), p.name.lower()
    if low in IGNORE_FILES:
        return None
    parents = [x.lower() for x in rel.split("/")[:-1]]
    if ext in discover.AUDIO_EXT:
        return "recording", "audio recording"
    if ext in discover.VIDEO_EXT:
        try:
            big = p.stat().st_size >= 1 << 20
        except OSError:
            big = False
        if ext == ".ts" and not ("videos" in parents or big):
            return None                                  # a TypeScript file, not a recording
        return "recording", "video recording"
    if low.endswith(".whisper.json"):
        return "whisper", "Whisper output"
    if _is_transcript(p):
        return "transcript", "transcript header or Scaler-style name" if ext != ".vtt" and ext != ".srt" else "subtitle file"
    if strict:
        return None
    stem = p.stem
    parent_nums = []
    for part in reversed(rel.split("/")[:-1]):
        parent_nums = discover.keyword_numbers(part)
        if parent_nums:
            break
    numbered = bool(discover.explicit_numbers(stem)) or bool(parent_nums)
    syl = bool(discover.SYLLABUS_RE.search(stem))
    matched = _title_match(stem, sessions)
    if ext in (".pdf", ".pptx", ".docx", ".key", ".ppt", ".odp", ".doc", ".odt", ".rtf"):
        toks = set(re.findall(r"[a-z]+", stem.lower()))
        if syl:
            return "syllabus", "syllabus-like name"
        if ext in (".key", ".ppt", ".odp") or (ext in (".pdf", ".pptx") and (toks & discover.SLIDE_HINT or numbered)):
            return "slides", "slide-like name or class number"
        if matched:
            return "handout", "name matches a session title"
        return None
    if ext in discover.DATA_EXT or ext == ".ipynb":
        if numbered or matched:
            return "data", "class number or session title in the name"
        return None
    if ext in (".md", ".txt") and (syl or numbered and discover.EXERCISE_TOKENS & set(re.findall(r"[a-z]+", stem.lower()))):
        return "reference", "course document by name"
    return None


def _destination(res_name: str, kind: str, p: Path, rel: str) -> str:
    """Project-relative destination: recordings -> resources/videos/, the rest -> resources/ (a stray folder
    that carries a class number or a student/starter-pack word keeps its name, as discover reads parent folders)."""
    if kind in ("recording",):
        return f"{res_name}/videos/{p.name}"
    parts = rel.split("/")[:-1]
    keep = None
    for part in reversed(parts):
        if part.lower() in GENERIC_FOLDERS:
            continue
        if discover.keyword_numbers(part) or (discover._words(part) & discover.BUNDLE_TOKENS):  # noqa: SLF001
            keep = part
        break
    return f"{res_name}/{keep}/{p.name}" if keep else f"{res_name}/{p.name}"


def check(project, profile: dict | None = None) -> list[dict]:
    """Class inputs outside ``resources/``: ``[{path, rel, kind, suggested_destination, reason}]`` (posix paths;
    ``suggested_destination`` is absolute and ``dest_rel`` project-relative). Never raises on unreadable entries."""
    root = plat.norm(project)
    lay = layout.Layout(root)
    res = lay.resources(profile)
    sessions = (profile or {}).get("sessions") or []
    prior = [plat.norm(x) for x in lay.prior_dirs(profile)]
    try:
        res_rel = res.relative_to(root).as_posix()
    except ValueError:
        res_rel = "resources"
    out: list[dict] = []
    seen = 0

    def skip_dir(d: Path, depth: int) -> bool:
        name = d.name.lower()
        if name.startswith(".") or name in SKIP_DIRS or depth >= MAX_DEPTH:
            return True
        if plat.norm(d) == plat.norm(res) or any(plat.norm(d) == x for x in prior):
            return True
        if d.parent == root and name in ("notes", "pdf"):
            return False
        return _is_code_dir(d)

    def walk(d: Path, depth: int, strict: bool) -> None:
        nonlocal seen
        try:
            entries = sorted(d.iterdir(), key=lambda x: x.name.lower())
        except OSError:
            return
        for e in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                return
            try:
                if e.is_symlink():
                    continue
                if e.is_dir():
                    if d == root and e.name.lower() in TOP_ONLY:
                        # only the top level of notes/ and pdf/ (never notes|pdf/<tier>/)
                        for f in sorted(e.iterdir(), key=lambda x: x.name.lower()):
                            if f.is_file():
                                _consider(f, True)
                        continue
                    if skip_dir(e, depth):
                        continue
                    walk(e, depth + 1, strict)
                elif e.is_file():
                    _consider(e, strict)
            except OSError:
                continue

    def _consider(f: Path, strict: bool) -> None:
        rel = f.relative_to(root).as_posix()
        hit = classify_outside(f, rel, sessions, strict)
        if not hit:
            return
        kind, why = hit
        dest = _destination(res_rel, kind, f, rel)
        out.append({"path": f.as_posix(), "rel": rel, "kind": kind,
                    "suggested_destination": (root / dest).as_posix(), "dest_rel": dest,
                    "reason": f"{why}; discovery only reads {res_rel}/"})

    walk(root, 0, False)
    return out


def _assert_exempt(src: Path, dst: Path, root: Path, res: Path) -> None:
    """The narrow exemption: this module only moves a file from the project (outside resources/) INTO resources/."""
    if not plat.is_under(dst, res):
        raise LayoutError(f"internal: refusing to write {dst} (outside resources/)")
    if plat.is_under(src, res) or not plat.is_under(src, root):
        raise LayoutError(f"internal: refusing to move {src} (not a project file outside resources/)")


def fix(project, findings: list[dict], profile: dict | None = None) -> list[dict]:
    """Move each finding to its suggested destination. Never overwrites: an existing destination (or two files
    wanting the same one) refuses that file. Returns ``[{path, destination, status, message}]`` with status
    ``moved`` or ``refused``."""
    root = plat.norm(project)
    res = layout.Layout(root).resources(profile)
    results: list[dict] = []
    claimed: set[str] = set()
    for f in findings:
        src, dst = Path(f["path"]), Path(f["suggested_destination"])
        r = {"path": src.as_posix(), "destination": dst.as_posix(), "status": "refused", "message": ""}
        results.append(r)
        try:
            _assert_exempt(src, dst, root, res)
            key = plat.norm(dst).as_posix().casefold()
            if dst.exists() or key in claimed:
                r["message"] = "destination already exists: not overwritten, rename or move it by hand"
                continue
            if not src.is_file():
                r["message"] = "source is gone"
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            claimed.add(key)
            r["status"], r["message"] = "moved", "ok"
        except (OSError, LayoutError) as e:
            r["message"] = str(e)
    return results


def print_block(findings: list[dict], out=None) -> None:
    """The shared 'Files outside resources/' block printed by init / scan / update."""
    import sys
    out = out or sys.stdout
    if not findings:
        return
    print(f"Files outside resources/ ({len(findings)}): discovery ignores these", file=out)
    for f in findings:
        print(f"  {f['kind']:<10} {f['rel']}  ->  {f['dest_rel']}", file=out)
    print("  move them: uv run scan_resources.py --project <DIR> --fix-layout --confirm", file=out)


def print_fix(results: list[dict], root, out=None) -> None:
    import sys
    out = out or sys.stdout
    root = plat.norm(root)

    def rel(p: str) -> str:
        try:
            return Path(p).relative_to(root).as_posix()
        except ValueError:
            return p
    for r in results:
        if r["status"] == "moved":
            print(f"moved   {rel(r['path'])} -> {rel(r['destination'])}", file=out)
        else:
            print(f"REFUSED {rel(r['path'])} -> {rel(r['destination'])}: {r['message']}", file=out)
