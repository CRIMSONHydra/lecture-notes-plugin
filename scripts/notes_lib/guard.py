"""Read-only guard for a project's ``resources/`` (and external prior notes). Stdlib only.

Each run has its own guard file, ``<data>/guards/<run>.json``, listing *protect* roots (``resources_dir``,
``prior_notes_external``: never written) and *allow* roots (``notes/``, ``pdf/``, ``.notes-plugin-internal/``
of the project). Runs in different projects (or sessions) therefore never overwrite or clear each other's
guard. A pre-1.2.6 ``<data>/guard.json`` is still honoured until it expires.

Semantics of :func:`check_path`: the most specific (longest) matching root wins, so an allowed output dir
nested inside a protected repo stays writable while the rest of the repo stays blocked, and vice versa. A
path is writable only if *every* active guard allows it (:func:`check_all`); one guard's allow roots never
open another guard's protected roots.

Each guard expires after ``hours`` (default 6) so a crashed run cannot block writes forever.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import platform as plat

GUARD_FILE = "guard.json"       # legacy single guard (pre-1.2.6): read until it expires, never written
GUARD_DIR = "guards"            # one <run>.json per guarded run


class GuardError(PermissionError):
    """Raised by assert_writable() for a path inside a protected root."""


def _dir(data_dir: str | os.PathLike | None) -> Path:
    return Path(data_dir) if data_dir else plat.default_data_dir()


def _key(run_id: str) -> str:
    """File stem for a run id (``default`` for an anonymous guard)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", run_id or "").strip("._")[:120] or "default"


def guard_file(data_dir, run_id: str = "") -> Path:
    """Where the guard of ``run_id`` lives."""
    return _dir(data_dir) / GUARD_DIR / f"{_key(run_id)}.json"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def set_guard(data_dir, protect, allow, hours: float = 6, run_id: str = "", session_id: str = "") -> Path:
    """Activate the guard of ``run_id``. Returns the path of its file; other runs' guards are untouched.

    ``session_id`` is the Claude Code session that owns the run. The skills put it in the run id
    (``update-<session>``, ``<profile>-<nn>-<session>``), so it is read from there when not given; the
    execution hook uses it to leave other sessions' commands alone."""
    now = time.time()
    m = _UUID.search(run_id or "")
    doc = {
        "run_id": run_id or "",
        "session_id": (session_id or (m.group(0) if m else "")).lower(),
        "created": _iso(now),
        "created_epoch": now,
        "expires": _iso(now + float(hours) * 3600),
        "expires_epoch": now + float(hours) * 3600,
        "protect": [plat.norm(p).as_posix() for p in (protect or [])],
        "allow": [plat.norm(p).as_posix() for p in (allow or [])],
    }
    f = guard_file(data_dir, run_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.stem}.{os.getpid()}.tmp")             # atomic: a half-written guard would read as "none"
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8", newline="\n")
    os.replace(tmp, f)
    return f


def _files(data_dir) -> list[Path]:
    d = _dir(data_dir)
    files = [d / GUARD_FILE] if (d / GUARD_FILE).is_file() else []
    gd = d / GUARD_DIR
    return files + (sorted(gd.glob("*.json")) if gd.is_dir() else [])


def _read(f: Path) -> dict | None:
    """The guard in ``f``, or None if unreadable or expired."""
    try:
        doc = json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    exp = doc.get("expires_epoch")
    if isinstance(exp, (int, float)) and time.time() > exp:
        return None
    return doc


def _active(data_dir) -> list[tuple[Path, dict]]:
    out = [(f, g) for f in _files(data_dir) for g in [_read(f)] if g]
    out.sort(key=lambda fg: fg[1].get("created_epoch") or 0, reverse=True)
    return out


def load_guards(data_dir) -> list[dict]:
    """Every active guard, newest first (empty when none)."""
    return [g for _, g in _active(data_dir)]


def load_guard(data_dir, run_id: str | None = None) -> dict | None:
    """The active guard of ``run_id``; without a run id, the newest active guard. None if there is none."""
    for _, g in _active(data_dir):
        if run_id is None or (g.get("run_id") or "") == run_id:
            return g
    return None


def clear_guard(data_dir, run_id: str | None = None) -> bool:
    """Remove the guard of ``run_id`` (every guard file, active or expired, when ``run_id`` is None).
    True if an active guard was removed."""
    had = False
    for f in _files(data_dir):
        g = _read(f)
        if run_id is not None:
            if f.name == GUARD_FILE:
                if not g or (g.get("run_id") or "") != run_id:
                    continue
            elif f != guard_file(data_dir, run_id):
                continue
        try:
            f.unlink()
            had = had or g is not None
        except FileNotFoundError:
            pass
    return had


def _best(path, roots) -> tuple[int, str | None]:
    """Longest root in ``roots`` that contains ``path`` -> (length, root)."""
    best_len, best = -1, None
    for r in roots or []:
        if plat.is_under(path, r):
            n = len(plat.norm(r).as_posix().rstrip("/"))
            if n > best_len:
                best_len, best = n, r
    return best_len, best


def check_path(path, guard) -> str | None:
    """None if ``guard`` lets ``path`` be written, else the human-readable refusal message."""
    if not guard:
        return None
    p_len, p_root = _best(path, guard.get("protect"))
    if p_root is None:
        return None
    a_len, a_root = _best(path, guard.get("allow"))
    if a_root is not None and a_len >= p_len:
        return None
    return (
        f"notes guard: {plat.norm(path).as_posix()} is inside protected {p_root} "
        "(resources and prior notes are read-only). Clear with /notes:doctor --clear-guard if stale."
    )


def check_all(path, guards) -> str | None:
    """None if every guard in ``guards`` lets ``path`` be written, else the first refusal."""
    for g in guards or []:
        msg = check_path(path, g)
        if msg:
            return msg
    return None


def scope(guard) -> str | None:
    """The folder a guard watches over: the deepest common parent of its protect and allow roots."""
    roots = [plat.norm(r) for r in (guard.get("protect") or []) + (guard.get("allow") or [])]
    if not roots:
        return None
    try:
        common = Path(os.path.commonpath([str(r) for r in roots]))
    except ValueError:                                           # different drives
        return None
    return None if common.parent == common else common.as_posix()   # a bare drive or "/" is no scope


def owned_by_session(guards, session_id: str | None) -> list[dict]:
    """The guards this session may own. A guard or an event without a session id is ambiguous, so it counts."""
    sid = str(session_id or "").lower()
    return [g for g in guards or [] if not g.get("session_id") or not sid or g.get("session_id") == sid]


def covering(guards, cwd) -> list[dict]:
    """The guards whose scope contains ``cwd``."""
    if not cwd:
        return []
    return [g for g in guards or [] if (s := scope(g)) and plat.is_under(cwd, s)]


def assert_writable(path, data_dir=None) -> None:
    """Raise GuardError if an active guard forbids writing ``path``. No guard -> no-op."""
    msg = check_all(path, load_guards(data_dir))
    if msg:
        raise GuardError(msg)
