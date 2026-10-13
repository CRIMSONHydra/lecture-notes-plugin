"""Subject-profile lookup and loading. ``yaml`` is imported lazily (callers declare pyyaml).

A profile is a markdown file: YAML front matter (machine fields) + prose body (agents read it).
See PLAN.md section 3.3 for the schema.
"""
from __future__ import annotations

import copy
import datetime as _dt
import glob
import os
import re
from pathlib import Path

from . import layout, platform as plat


class ProfileError(ValueError):
    """Profile missing, malformed or lacking required keys."""


DEFAULTS: dict = {
    "aliases": [],
    "institution": "",
    "spelling": "any",
    "resources_dir": "resources",
    "paths": {
        "transcripts": "{stem}.txt",
        "recordings": "videos/{stem}.mp4",
        "slides": [],
        "handouts": [],
        "frames": "frames/{stem}",
        "whisper": "{stem}.whisper.json",
        "classwork": None,
    },
    "prior_notes_external": [],
    "instructor_code": None,
    "sessions": [],
    "asr": {"language": "en", "hotwords": ""},
    "snippets": {},
    "tiers": {
        "basic": {
            "words": [3500, 7000],       # ~2 h class; scaled +-50% by duration_min/120 (note_stats)
            "minutes": [60, 120],        # informational only (never fails)
            "figures": "class+generated",
            "diagrams": "ascii",
            "math": False,
            "check_yourself": [5, 8],
            "going_deeper_max": 8,
            "gapfill_max_share": 0.30,
            "required_sections": ["TL;DR", "Check yourself"],
            "closing_sections": ["Going Deeper", "Further Study"],
        },
        "advanced": {
            "words": [6000, 15000],
            "figures": "class+generated",
            "min_figures": 2,
            "diagrams": "mermaid-ok",
            "math": True,
            "min_sources": 8,
            "min_domains": 4,
            "check_yourself": [6, 10],
            "required_sections": [
                "What you need before this note", "TL;DR", "One-screen summary",
                "New words in this note", "Check yourself", "Hands-on",
                "Further Study", "Sources",
            ],
            "closing_sections": ["Further Study", "Sources"],
        },
    },
    "video": {"delete_after_ingest": "ask"},
    "research": {"preferred_sources": [], "required_domains": []},
    "callouts": {},
    "branding": {"accent": "#1f4e79", "accent2": "#a3521a"},
    "models": {},
}

REQUIRED = ("schema", "id", "title", "short", "prefix")
_FM_RE = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)\Z", re.S)
_ID_RE = re.compile(r"^[a-z0-9-]+$")
_PREFIX_RE = re.compile(r"^[A-Z0-9]+$")


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise ProfileError("pyyaml is required: declare `pyyaml` in the script's PEP-723 dependencies") from e
    return yaml


def split_front_matter(text: str) -> tuple[dict, str]:
    """(front-matter dict, body). No front matter -> ({}, text)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text.lstrip("﻿")
    try:
        meta = _yaml().safe_load(m.group(1)) or {}
    except ProfileError:
        raise
    except Exception as e:
        raise ProfileError(f"invalid YAML front matter: {e}") from e
    if not isinstance(meta, dict):
        raise ProfileError("front matter is not a mapping")
    return meta, m.group(2)


def _read_fm(path: Path) -> dict:
    try:
        meta, _ = split_front_matter(path.read_text(encoding="utf-8-sig"))
    except OSError:
        return {}
    except ProfileError:
        return {}
    return meta


def _matches(meta: dict, ref: str) -> bool:
    r = ref.strip().lower()
    names = [str(meta.get("id", "")).lower()] + [str(a).lower() for a in (meta.get("aliases") or [])]
    return r in names


def _profile_dirs(data_dir) -> list[Path]:
    dd = Path(data_dir) if data_dir else plat.default_data_dir()
    return [dd / "profiles", plat.plugin_root() / "profiles"]


def _project_profile(project=None, cwd=None) -> Path | None:
    """<project>/.notes-plugin-internal/profile.md for an explicit project, else discovered by walking up from cwd."""
    if project:
        p = plat.norm(project) / layout.PROFILE_REL
        return p if p.is_file() else None
    try:
        return layout.find_project(start=cwd) / layout.PROFILE_REL
    except layout.ProjectError:
        return None


def find_profile(ref, *, data_dir=None, cwd=None, project=None) -> Path:
    """Locate a profile file.

    0. ``ref`` empty / "auto" / "project": the project's ``.notes-plugin-internal/profile.md`` (``project`` or walk up from cwd).
    1. ``ref`` is an existing file path.
    2. ``ref`` matches the id/alias of the project's ``.notes-plugin-internal/profile.md``.
    3. ``<data>/profiles/<ref>.md``   4. ``<plugin>/profiles/<ref>.md`` (templates)
    5. scan both profile dirs for a matching ``aliases`` entry.
    """
    ref_s = str(ref).strip() if ref is not None else ""
    searched: list[str] = []
    proj_prof = _project_profile(project, cwd)

    if not ref_s or ref_s.lower() in ("auto", "project"):
        if proj_prof and proj_prof.is_file():
            return proj_prof.resolve()
        where = (plat.norm(project) / layout.PROFILE_REL) if project else "<cwd or a parent>/.notes-plugin-internal/profile.md"
        raise ProfileError(f"no project profile found at {where} (run /notes:init, or pass --profile)")

    cand = Path(os.path.expanduser(ref_s))
    if cand.is_file():
        return cand.resolve()
    searched.append(str(cand))

    if proj_prof and proj_prof.is_file() and _matches(_read_fm(proj_prof), ref_s):
        return proj_prof.resolve()
    searched.append(str(proj_prof) if proj_prof else "<project>/.notes-plugin-internal/profile.md")

    dirs = _profile_dirs(data_dir)
    if re.fullmatch(r"[A-Za-z0-9_.+-]+", ref_s):
        for d in dirs:
            p = d / f"{ref_s}.md"
            searched.append(str(p))
            if p.is_file():
                return p.resolve()
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            if p.name.startswith("_") or p.name.lower() == "readme.md":
                continue
            if _matches(_read_fm(p), ref_s):
                return p.resolve()
        searched.append(f"{d}{os.sep}*.md (aliases)")
    raise ProfileError(f"profile {ref_s!r} not found; looked in: " + "; ".join(searched))


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _iso_date(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.date().isoformat() if isinstance(v, _dt.datetime) else v.isoformat()
    return str(v)


def load_profile(ref=None, *, data_dir=None, cwd=None, project=None) -> dict:
    """Load + validate a profile, deep-merged with DEFAULTS.

    Adds ``_path`` (Path of the file), ``_body`` (markdown after the front matter), ``_project``
    (project root or None) and ``_resources`` (resolved ``resources_dir`` or None).
    ``sessions[].date`` values are normalised to ``YYYY-MM-DD`` strings.
    """
    path = find_profile(ref, data_dir=data_dir, cwd=cwd, project=project)
    meta, body = split_front_matter(path.read_text(encoding="utf-8-sig"))
    if not meta:
        raise ProfileError(f"{path}: no YAML front matter")
    missing = [k for k in REQUIRED if meta.get(k) in (None, "")]
    if meta.get("paths") is not None and not isinstance(meta["paths"], dict):
        missing.append("paths (must be a mapping)")
    if missing:
        raise ProfileError(f"{path}: missing required key(s): {', '.join(missing)}")
    if meta["schema"] != 1:
        raise ProfileError(f"{path}: unsupported schema {meta['schema']!r} (expected 1)")
    if not _ID_RE.match(str(meta["id"])):
        raise ProfileError(f"{path}: id {meta['id']!r} must match [a-z0-9-]+")
    if not _PREFIX_RE.match(str(meta["prefix"])):
        raise ProfileError(f"{path}: prefix {meta['prefix']!r} must match [A-Z0-9]+")
    prof = _merge(DEFAULTS, meta)
    prof["id"] = str(prof["id"])
    prof["resources_dir"] = os.path.expanduser(str(prof.get("resources_dir") or "resources"))
    prof["prior_notes_external"] = [os.path.expanduser(str(d)) for d in (prof.get("prior_notes_external") or [])]
    proj = None
    if project:
        proj = plat.norm(project)
    elif path.name == "profile.md" and path.parent.name in (layout.INTERNAL, layout.LEGACY_INTERNAL):
        proj = path.parent.parent
    else:
        try:
            proj = layout.find_project(start=cwd)
        except layout.ProjectError:
            proj = None
    prof["_project"] = proj
    prof["_resources"] = layout.Layout(proj).resources(prof) if proj else (
        plat.norm(prof["resources_dir"]) if os.path.isabs(prof["resources_dir"]) else None)
    sessions = []
    for s in prof.get("sessions") or []:
        if not isinstance(s, dict) or "n" not in s:
            raise ProfileError(f"{path}: every sessions[] entry needs `n`")
        s = dict(s)
        s["date"] = _iso_date(s.get("date"))
        sessions.append(s)
    prof["sessions"] = sessions
    prof["_path"] = path
    prof["_body"] = body
    return prof


def _session_n(n) -> int:
    m = re.match(r"\s*(\d+)", str(n))
    if not m:
        raise ProfileError(f"bad session number {n!r}")
    return int(m.group(1))


def session_info(profile: dict, n) -> dict:
    """{"n","nn","title","stem","date"} for session ``n`` ("2a" -> 2).

    A session missing from the profile yields title/stem/date = None (not an error).
    """
    num = _session_n(n)
    row = next((s for s in profile.get("sessions", []) if int(s.get("n", -1)) == num), {})
    return {
        "n": num,
        "nn": f"{num:02d}",
        "title": row.get("title"),
        "stem": row.get("stem"),
        "date": _iso_date(row.get("date")),
    }


def tier_cfg(profile: dict, tier: str) -> dict:
    """Effective config of a tier ('basic' | 'advanced')."""
    if tier not in ("basic", "advanced"):
        raise ProfileError(f"unknown tier {tier!r} (basic|advanced)")
    return copy.deepcopy(profile["tiers"][tier])


class _Skip(Exception):
    pass


def _fmt(pattern: str, fmt: dict) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in fmt:
            raise ProfileError(f"unknown placeholder {{{key}}} in path pattern {pattern!r}")
        v = fmt[key]
        if v is None or v == "":
            raise _Skip()
        return glob.escape(str(v))

    return re.sub(r"\{(\w+)\}", sub, pattern)


def resolve_repo_paths(profile: dict, key: str, **fmt) -> list[Path]:
    """Existing paths for ``paths.<key>`` under ``resources_dir`` (alias: :func:`resolve_resource_paths`).

    Placeholders ``{n} {nn} {stem} {title}`` come from ``fmt`` (pass ``**session_info(...)``);
    a pattern whose placeholder value is None is skipped. Patterns may contain globs; an absolute
    pattern is used as-is. A list value means alternatives, tried in order.
    Result: de-duplicated, existing paths only.
    """
    paths = profile.get("paths") or {}
    k = key[len("paths."):] if key.startswith("paths.") else key
    val = paths.get(k)
    if val in (None, "", []):
        return []
    patterns = [val] if isinstance(val, str) else list(val)
    root = profile.get("_resources")
    if root is None:
        raise ProfileError("resources_dir is relative but the project is unknown (pass project=...)")
    root = plat.norm(root)
    out: list[Path] = []
    seen: set[str] = set()
    for pat in patterns:
        try:
            pattern = _fmt(str(pat), fmt)
        except _Skip:
            continue
        if os.path.isabs(pattern):
            hits = glob.glob(pattern, recursive=True)
        else:
            hits = [os.path.join(str(root), h) for h in glob.glob(pattern, root_dir=str(root), recursive=True)]
        for h in sorted(hits):
            p = plat.norm(h)
            kk = os.path.normcase(str(p))
            if kk not in seen and p.exists():
                seen.add(kk)
                out.append(p)
    return out


resolve_resource_paths = resolve_repo_paths


def load_or_exit(ref=None, *, data_dir=None, project=None, cwd=None, notes=None) -> dict:
    """CLI helper: :func:`load_profile`, but prints the problem and exits 2 instead of raising.

    ``notes``: note paths; when ``project`` is None and the first one is ``<project>/notes/<tier>/x.md``,
    that project is used (so a script run from anywhere finds the right ``.notes-plugin-internal/profile.md``).
    """
    import sys

    if project is None and notes:
        lay, _t = layout.infer_from_note(notes[0])
        if lay is not None and lay.profile_path.is_file():
            project = lay.root
    try:
        return load_profile(ref, data_dir=data_dir, cwd=cwd, project=project)
    except (ProfileError, layout.ProjectError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)


def with_derived_sessions(profile: dict, resources=None) -> tuple[dict, list[dict]]:
    """(profile copy whose ``sessions`` also holds the sessions derived from ``resources/``, derived rows).

    A profile with no ``sessions`` (new subject), or files matching none of them, still yields a usable
    table (PLAN 11.3). Derived rows carry ``derived: True``; nothing is written.
    """
    from . import discover  # lazy

    root = resources or profile.get("_resources")
    if not root or not Path(root).is_dir():
        return profile, []
    d = discover.discover(profile, Path(root))
    if not d.derived:
        return profile, []
    out = dict(profile)
    out["sessions"] = list(profile.get("sessions") or []) + [dict(r) for r in d.derived]
    return out, d.derived


# ------------------------------------------------------------------ course order (PLAN 13.5)
def session_orders(profile: dict) -> dict[int, float]:
    """``{n: order}``. A session's explicit ``order`` wins; otherwise its order is ``n``, except that the
    dated sessions without an explicit ``order`` are re-sorted by date among their own slots (so a
    session numbered 14 held between 12 and 13 sorts there). Undated sessions keep their slot; ties go by n."""
    rows = profile.get("sessions") or []
    out: dict[int, float] = {}
    free = []
    for s in rows:
        n = int(s["n"])
        o = s.get("order")
        if isinstance(o, (int, float)) and not isinstance(o, bool):
            out[n] = float(o)
        else:
            free.append((n, _iso_date(s.get("date"))))
            out[n] = float(n)
    dated = sorted((n, d) for n, d in free if d)
    if len(dated) > 1:
        slots = [n for n, _d in dated]
        by_date = sorted(dated, key=lambda x: (x[1], x[0]))
        for slot, (n, _d) in zip(slots, by_date):
            out[n] = float(slot)
    return out


def order_of(omap: dict, n) -> float:
    return omap.get(int(n), float(int(n)))


def course_sessions(profile: dict) -> list[dict]:
    """Profile session rows in course order (each row gains ``order``)."""
    om = session_orders(profile)
    rows = [dict(s, order=om[int(s["n"])]) for s in profile.get("sessions") or []]
    rows.sort(key=lambda s: (s["order"], int(s["n"])))
    return rows


def note_sort_key(omap: dict, num: str) -> tuple:
    """Sort key for a note number such as ``02`` / ``02a`` (course order, then companion letter)."""
    m = re.match(r"(\d+)([a-z]?)", str(num))
    if not m:
        return (float("inf"), 10 ** 6, str(num))
    n = int(m.group(1))
    return (order_of(omap, n), n, m.group(2))


def derive_order(profile: dict, date: str | None) -> float | None:
    """Explicit ``order`` for a new session dated ``date``: midway between its dated neighbours in course
    order (after the last one when later than all). None when it has no date."""
    date = _iso_date(date)
    if not date:
        return None
    om = session_orders(profile)
    dated = sorted(((_iso_date(s.get("date")), om[int(s["n"])]) for s in profile.get("sessions") or []
                    if _iso_date(s.get("date"))), key=lambda x: (x[0], x[1]))
    before = [o for d, o in dated if d <= date]
    after = [o for d, o in dated if d > date]
    if before and after:
        a, b = max(before), min(after)
        return round((a + b) / 2, 4) if b > a else round(a + 0.5, 4)
    if before:
        return round(max(om.values()) + 1.0, 4) if om else 1.0
    if after:
        return round(min(om.values()) - 1.0, 4)
    return None
