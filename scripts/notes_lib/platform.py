"""OS detection, data directory, path helpers. Stdlib only.

NOTE: this module is named ``platform`` inside the ``notes_lib`` package. It never
shadows the stdlib module because scripts import it as ``notes_lib.platform``.
"""
from __future__ import annotations

import os
import platform as _stdlib_platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


def utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr (Windows consoles default to cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def os_kind() -> str:
    """'windows' | 'macos' | 'linux'."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def plugin_root() -> Path:
    """The plugin root directory (parent of ``scripts/``)."""
    return Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    """Persistent data dir.

    $LECTURE_NOTES_DATA > $CLAUDE_PLUGIN_DATA > per-OS default. Not created here.
    """
    for var in ("LECTURE_NOTES_DATA", "CLAUDE_PLUGIN_DATA"):
        v = os.environ.get(var)
        if v:
            return norm(v)
    kind = os_kind()
    if kind == "windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "lecture-notes"
    if kind == "macos":
        return Path.home() / "Library" / "Application Support" / "lecture-notes"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "lecture-notes"


def resolve_data_dir(arg: str | os.PathLike | None) -> Path:
    """`--data-dir` value (or the default) as an existing directory."""
    d = norm(arg) if arg else default_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


_MSYS_RE = re.compile(r"^/([a-zA-Z])(/.*)?$")


def norm(p: str | os.PathLike) -> Path:
    """Absolute, ``~``-expanded, symlink-resolved, normalised Path (need not exist).

    On Windows an MSYS/Git-Bash style ``/e/foo`` is read as ``E:/foo``.
    """
    s = os.fspath(p)
    if os_kind() == "windows":
        m = _MSYS_RE.match(s)
        if m and not Path(s).exists():
            s = f"{m.group(1).upper()}:{m.group(2) or '/'}"
    s = os.path.expanduser(s)
    try:
        return Path(s).resolve()
    except OSError:
        return Path(os.path.abspath(s))


def _key(p: str | os.PathLike) -> str:
    return os.path.normcase(str(norm(p)))


def is_under(path: str | os.PathLike, root: str | os.PathLike) -> bool:
    """True if ``path`` is ``root`` or inside it. Case-insensitive on Windows."""
    a, b = _key(path), _key(root)
    if a == b:
        return True
    return a.startswith(b.rstrip("\\/") + os.sep)


def which_any(names) -> str | None:
    """First of ``names`` found on PATH, else None."""
    for n in names:
        hit = shutil.which(n)
        if hit:
            return hit
    return None


def gpu_summary() -> dict:
    """{"nvidia": bool, "nvidia_name": str|None, "apple_silicon": bool}.

    Uses ``nvidia-smi -L`` only; no heavy imports.
    """
    out = {"nvidia": False, "nvidia_name": None, "apple_silicon": False}
    if sys.platform == "darwin" and _stdlib_platform.machine().lower() in ("arm64", "aarch64"):
        out["apple_silicon"] = True
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            r = subprocess.run([smi, "-L"], capture_output=True, text=True, timeout=8)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    m = re.match(r"\s*GPU\s+\d+:\s*(.+?)(?:\s*\(UUID:.*)?\s*$", line)
                    if m:
                        out["nvidia"] = True
                        out["nvidia_name"] = m.group(1).strip()
                        break
        except Exception:
            pass
    return out


def write_json(path: str | os.PathLike, obj, *, indent: int = 2) -> Path:
    """Write ``obj`` as UTF-8 JSON (LF newlines), creating parent dirs. Returns the Path.

    Callers that must honour the guard call ``guard.assert_writable(path)`` first.
    """
    import json

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=indent, ensure_ascii=False, default=str)
        fh.write("\n")
    return p
