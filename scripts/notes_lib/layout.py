"""Fixed project layout (PLAN section 10). Stdlib only; the single source of every path.

    <project>/resources/            inputs, read-only to the plugin
    <project>/notes/<tier>/         markdown sources (+ figures/)
    <project>/pdf/<tier>/           the deliverables
    <project>/.notes-plugin-internal/profile.md   subject profile (the one user-editable file in there)
    <project>/.notes-plugin-internal/state.json   the ledger (PLAN 13.2; survives wiping work/)
    <project>/.notes-plugin-internal/work/        regenerable intermediates (git-ignored)

Projects created before PLAN 13 used ``.notes/``; :func:`migrate_legacy` renames it.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import platform as plat

TIERS = ("basic", "advanced")
INTERNAL = ".notes-plugin-internal"
LEGACY_INTERNAL = ".notes"
PROFILE_REL = Path(INTERNAL) / "profile.md"
LEGACY_PROFILE_REL = Path(LEGACY_INTERNAL) / "profile.md"
GITIGNORE_LINE = f"{INTERNAL}/work/"
GITATTRIBUTES_HEADER = "# notes plugin: generated text is LF on every OS; binaries are never converted"
GITATTRIBUTES_LINES = (
    "notes/**/*.md text=auto eol=lf",
    ".notes-plugin-internal/** text=auto eol=lf",
    "*.pdf binary",
    "*.png binary",
    "*.jpg binary",
    "*.mp4 binary",
)
INTERNAL_README = (
    "Managed by the notes plugin. Not meant for reading or editing. The one user-editable file is "
    "`profile.md` (course facts, session list, settings). `state.json` is the plugin's ledger "
    "- don't edit it. `work/` is regenerable scratch, git-ignored.\n")


class ProjectError(RuntimeError):
    """No project could be located (run /notes:init)."""


def ensure_gitattributes(root) -> list[str]:
    """Merge the plugin's ``.gitattributes`` lines into ``<root>/.gitattributes`` without touching any other line.
    Idempotent: a line already present (ignoring surrounding blanks) is not added again; nothing is written when
    nothing is missing. Written with LF endings. Returns the messages (empty when there was nothing to do)."""
    root = plat.norm(root)
    ga = root / ".gitattributes"
    cur = ga.read_text(encoding="utf-8-sig") if ga.is_file() else ""
    have = {ln.strip() for ln in cur.splitlines()}
    missing = [ln for ln in GITATTRIBUTES_LINES if ln not in have]
    if not missing:
        return []
    add = ([] if GITATTRIBUTES_HEADER in have else [GITATTRIBUTES_HEADER]) + missing
    new = cur + ("" if not cur or cur.endswith("\n") else "\n") + "\n".join(add) + "\n"
    ga.write_text(new, encoding="utf-8", newline="\n")
    return [f"{'updated' if cur else 'wrote'} .gitattributes ({len(missing)} line(s) added)"]


def _has_legacy(d: Path) -> bool:
    return (d / LEGACY_PROFILE_REL).is_file() and not (d / PROFILE_REL).is_file()


def migrate_legacy(root, quiet: bool = True) -> list[str]:
    """Rename ``<root>/.notes/`` -> ``<root>/.notes-plugin-internal/`` when only the legacy profile exists.

    Also fixes the ``.gitignore`` line and writes the internal README. Returns the messages (empty when
    there was nothing to do). Raises ProjectError when both directories exist (refuse: resolve by hand)."""
    root = plat.norm(root)
    old, new = root / LEGACY_INTERNAL, root / INTERNAL
    if not (old / "profile.md").is_file():
        return []
    if (new / "profile.md").is_file():
        return []      # already current; a stale .notes/ is left alone (doctor reports it)
    if new.exists():
        raise ProjectError(f"both {old} and {new} exist: refusing to migrate; merge them by hand "
                           f"(the profile belongs in {INTERNAL}/profile.md)")
    old.rename(new)
    msgs = [f"migrated {LEGACY_INTERNAL}/ -> {INTERNAL}/ in {root.as_posix()}"]
    msgs += ensure_gitattributes(root)
    gi = root / ".gitignore"
    if gi.is_file():
        lines = gi.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        legacy_line = f"{LEGACY_INTERNAL}/work/"
        out, hit = [], False
        for ln in lines:
            if ln.strip() == legacy_line:
                if not hit and GITIGNORE_LINE not in [x.strip() for x in lines]:
                    out.append(GITIGNORE_LINE)
                hit = True
            else:
                out.append(ln)
        if hit:
            gi.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
            msgs.append(f".gitignore: {legacy_line} -> {GITIGNORE_LINE}")
    rd = new / "README.md"
    if not rd.exists():
        rd.write_text(INTERNAL_README, encoding="utf-8", newline="\n")
        msgs.append(f"wrote {INTERNAL}/README.md")
    if not quiet:
        for m in msgs:
            print(m, file=sys.stderr)
    return msgs


def find_project(start=None, explicit=None, migrate: bool = True) -> Path:
    """``explicit`` (must exist), else walk up from ``start`` (default cwd) to the first dir containing
    ``.notes-plugin-internal/profile.md`` (or the legacy ``.notes/profile.md``, which is migrated unless
    ``migrate=False``). Raises ProjectError when nothing is found."""
    if explicit:
        p = plat.norm(explicit)
        if not p.is_dir():
            raise ProjectError(f"project directory does not exist: {p} (run /notes:init)")
        if migrate:
            migrate_legacy(p, quiet=False)
        return p
    cur = plat.norm(start if start else Path.cwd())
    for d in (cur, *cur.parents):
        if (d / PROFILE_REL).is_file():
            return d
        if _has_legacy(d):
            if migrate:
                migrate_legacy(d, quiet=False)
            return d
    raise ProjectError(f"no project found from {cur}: no {INTERNAL}/profile.md in it or any parent "
                       "(run /notes:init, or pass --project DIR)")


@dataclass
class Layout:
    root: Path

    @classmethod
    def for_project(cls, explicit=None, start=None) -> "Layout":
        return cls(find_project(start=start, explicit=explicit))

    # ---- fixed locations ----
    @property
    def profile_path(self) -> Path:
        return self.root / PROFILE_REL

    @property
    def internal(self) -> Path:
        return self.root / INTERNAL

    @property
    def state_path(self) -> Path:
        return self.internal / "state.json"

    @property
    def work(self) -> Path:
        return self.internal / "work"

    @property
    def reports(self) -> Path:
        return self.work / "reports"

    def resources(self, profile: dict | None = None) -> Path:
        """``profile['resources_dir']`` (absolute, or relative to the project); default ``resources``."""
        rd = (profile or {}).get("resources_dir") or "resources"
        rd = os.path.expanduser(str(rd))
        return plat.norm(rd) if os.path.isabs(rd) else plat.norm(self.root / rd)

    def notes(self, tier: str) -> Path:
        return self.root / "notes" / tier

    def figures(self, tier: str) -> Path:
        return self.notes(tier) / "figures"

    def pdf(self, tier: str) -> Path:
        return self.root / "pdf" / tier

    def session(self, nn) -> Path:
        s = f"{int(nn):02d}" if str(nn).isdigit() else str(nn)
        return self.work / "sessions" / s

    def meta(self, tier: str) -> Path:
        return self.work / "meta" / tier

    def build(self, tier: str) -> Path:
        return self.work / "build" / tier

    def ensure(self, tiers=TIERS) -> None:
        for t in tiers:
            for d in (self.figures(t), self.pdf(t), self.meta(t), self.build(t)):
                d.mkdir(parents=True, exist_ok=True)
        (self.work / "sessions").mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)

    # ---- guard roots ----
    def prior_dirs(self, profile: dict | None = None) -> list[Path]:
        """``prior_notes_external`` entries; a relative entry is relative to the project."""
        out = []
        for d in (profile or {}).get("prior_notes_external") or []:
            d = os.path.expanduser(str(d))
            out.append(plat.norm(d) if os.path.isabs(d) else plat.norm(self.root / d))
        return out

    def guard_roots(self, profile: dict | None = None) -> tuple[list[str], list[str]]:
        """(protect, allow) per PLAN 12.4. The whole project is protected (hand notes, old PDFs,
        CLAUDE.md, ...) except the tier subfolders ``notes/<t>/``, ``pdf/<t>/`` and ``.notes-plugin-internal/``
        (longest root wins); ``resources_dir`` and external prior notes are protected too."""
        protect = [self.resources(profile).as_posix()]
        for d in self.prior_dirs(profile):
            if d.as_posix() not in protect:
                protect.append(d.as_posix())
        protect.append(self.root.as_posix())
        allow = [(self.root / "notes" / t).as_posix() for t in TIERS]
        allow += [(self.root / "pdf" / t).as_posix() for t in TIERS]
        allow.append((self.root / INTERNAL).as_posix())
        return protect, allow


def resolve_out(out, project, session, sub: str) -> Path:
    """Explicit ``out`` wins; else ``<project>/.notes-plugin-internal/work/sessions/NN/<sub>`` (needs ``session``)."""
    if out:
        return plat.norm(out)
    if session in (None, ""):
        raise ProjectError("give --out DIR, or --session N (with --project DIR or run inside a project)")
    return Layout.for_project(project).session(session) / sub


def infer_from_note(note_path) -> tuple["Layout | None", str | None]:
    """(Layout, tier) when ``note_path`` is ``<project>/notes/<tier>/x.md``, else (None, None)."""
    d = plat.norm(note_path).parent
    if d.name in TIERS and d.parent.name == "notes":
        root = d.parent.parent
        if _has_legacy(root):
            migrate_legacy(root, quiet=False)
        return Layout(root), d.name
    return None, None
