"""Read-only guard for a project's ``resources/`` (and external prior notes). Stdlib only.

``<data>/guard.json`` lists *protect* roots (``resources_dir``, ``prior_notes_external``: never
written) and *allow* roots (``notes/``, ``pdf/``, ``.notes-plugin-internal/`` of the project). Semantics of :func:`check_path`: the most specific
(longest) matching root wins, so an allowed output dir nested inside a protected repo
stays writable while the rest of the repo stays blocked, and vice versa.

The guard expires after ``hours`` (default 6) so a crashed run cannot block writes forever.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import platform as plat

GUARD_FILE = "guard.json"


class GuardError(PermissionError):
    """Raised by assert_writable() for a path inside a protected root."""


def _file(data_dir: str | os.PathLike | None) -> Path:
    d = Path(data_dir) if data_dir else plat.default_data_dir()
    return d / GUARD_FILE


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def set_guard(data_dir, protect, allow, hours: float = 6, run_id: str = "") -> Path:
    """Activate the guard. Returns the path of ``<data>/guard.json``."""
    now = time.time()
    doc = {
        "run_id": run_id or "",
        "created": _iso(now),
        "expires": _iso(now + float(hours) * 3600),
        "expires_epoch": now + float(hours) * 3600,
        "protect": [plat.norm(p).as_posix() for p in (protect or [])],
        "allow": [plat.norm(p).as_posix() for p in (allow or [])],
    }
    f = _file(data_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(doc, indent=2), encoding="utf-8", newline="\n")
    return f


def clear_guard(data_dir) -> bool:
    """Remove the guard. True if one existed."""
    f = _file(data_dir)
    try:
        f.unlink()
        return True
    except FileNotFoundError:
        return False


def load_guard(data_dir) -> dict | None:
    """The active guard, or None if missing, unreadable or expired."""
    f = _file(data_dir)
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
    """None if ``path`` may be written, else the human-readable refusal message."""
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


def assert_writable(path, data_dir=None) -> None:
    """Raise GuardError if an active guard forbids writing ``path``. No guard -> no-op."""
    msg = check_path(path, load_guard(data_dir))
    if msg:
        raise GuardError(msg)
