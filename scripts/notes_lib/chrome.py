"""Chrome / Edge / Chromium discovery and headless launch flags. Stdlib only."""
from __future__ import annotations

import glob
import os
import re
import shutil
import sys
from pathlib import Path

from . import platform as plat


class ChromeNotFound(RuntimeError):
    """No usable Chromium-family browser could be located."""


def _install_hint() -> str:
    script = (plat.plugin_root() / "scripts" / "install_chromium.py").as_posix()
    return (
        "Install Google Chrome, Microsoft Edge or Chromium, or run "
        f'`uv run "{script}"` to download a Playwright Chromium '
        "(on Linux you may also need `playwright install-deps`). "
        "You can also point to a browser with --chrome PATH or $LECTURE_NOTES_CHROME."
    )


INSTALL_HINT: str = _install_hint()

_PATH_NAMES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "microsoft-edge-stable", "brave-browser", "brave",
    "chrome", "chrome.exe", "msedge", "msedge.exe",
]


def _windows_candidates() -> list[str]:
    roots: list[str] = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(var)
        if v and v not in roots:
            roots.append(v)
    if not roots:
        roots = [r"C:\Program Files", r"C:\Program Files (x86)"]
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    out: list[str] = []
    for r in roots:
        out.append(os.path.join(r, "Google", "Chrome", "Application", "chrome.exe"))
    out.append(os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"))
    for r in roots:
        out.append(os.path.join(r, "Microsoft", "Edge", "Application", "msedge.exe"))
    out.append(os.path.join(local, "Chromium", "Application", "chrome.exe"))
    for r in roots + [local]:
        out.append(os.path.join(r, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"))
    return out


def _macos_candidates() -> list[str]:
    apps = [Path("/Applications"), Path.home() / "Applications"]
    rels = [
        "Google Chrome.app/Contents/MacOS/Google Chrome",
        "Chromium.app/Contents/MacOS/Chromium",
        "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "Brave Browser.app/Contents/MacOS/Brave Browser",
    ]
    return [str(a / r) for r in rels for a in apps]


def _linux_candidates() -> list[str]:
    names = [
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
        "microsoft-edge", "microsoft-edge-stable", "brave-browser",
    ]
    dirs = ["/usr/bin", "/usr/local/bin", "/snap/bin", "/opt/google/chrome", "/opt/brave.com/brave"]
    return [os.path.join(d, n) for n in names for d in dirs]


def os_candidates() -> list[str]:
    kind = plat.os_kind()
    if kind == "windows":
        return _windows_candidates()
    if kind == "macos":
        return _macos_candidates()
    return _linux_candidates()


def _playwright_roots() -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        roots.append(Path(env))
    kind = plat.os_kind()
    if kind == "windows":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        roots.append(Path(local) / "ms-playwright")
    elif kind == "macos":
        roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    else:
        roots.append(Path.home() / ".cache" / "ms-playwright")
    return roots


def playwright_chromiums() -> list[Path]:
    """Chromium binaries in the Playwright cache, newest build first.

    Full Chromium is preferred over the headless shell.
    """
    full_pats = [
        "chromium-*/*/chrome.exe", "chromium-*/*/chrome",
        "chromium-*/*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/*/*.app/Contents/MacOS/Chromium",
    ]
    shell_pats = [
        "chromium_headless_shell-*/*/chrome-headless-shell.exe",
        "chromium_headless_shell-*/*/chrome-headless-shell",
        "chromium_headless_shell-*/*/headless_shell",
        "chromium_headless_shell-*/*/headless_shell.exe",
    ]

    def build(p: str) -> int:
        m = re.search(r"-(\d+)[\\/]", p.replace("\\", "/") + "/")
        return int(m.group(1)) if m else 0

    found: list[str] = []
    for pats in (full_pats, shell_pats):
        batch: list[str] = []
        for root in _playwright_roots():
            if not root.is_dir():
                continue
            for pat in pats:
                batch.extend(glob.glob(str(root / pat)))
        batch = sorted({b for b in batch if os.path.isfile(b)}, key=build, reverse=True)
        found.extend(batch)
    return [Path(f) for f in found]


def _ok(p: str | os.PathLike | None) -> Path | None:
    if not p:
        return None
    pp = Path(os.path.expanduser(str(p).strip().strip('"')))
    return pp if pp.is_file() else None


def find_chrome(explicit: str | os.PathLike | None = None,
                data_dir: str | os.PathLike | None = None) -> Path:
    """Locate a Chromium-family browser.

    Order: explicit -> $LECTURE_NOTES_CHROME -> <data>/chrome-path.txt -> OS candidates
    -> PATH names -> Playwright cache -> raise ChromeNotFound.
    """
    if explicit:
        hit = _ok(explicit)
        if hit:
            return hit
        raise ChromeNotFound(f"--chrome {explicit!s} does not exist. {INSTALL_HINT}")

    hit = _ok(os.environ.get("LECTURE_NOTES_CHROME"))
    if hit:
        return hit

    ddir = Path(data_dir) if data_dir else plat.default_data_dir()
    pf = ddir / "chrome-path.txt"
    if pf.is_file():
        try:
            hit = _ok(pf.read_text(encoding="utf-8-sig").strip().splitlines()[0])
        except (OSError, IndexError):
            hit = None
        if hit:
            return hit

    for c in os_candidates():
        hit = _ok(c)
        if hit:
            return hit

    for n in _PATH_NAMES:
        w = shutil.which(n)
        hit = _ok(w)
        if hit:
            return hit

    pw = playwright_chromiums()
    if pw:
        return pw[0]

    raise ChromeNotFound("No Chrome/Edge/Chromium found. " + INSTALL_HINT)


def chrome_args(chrome: str | os.PathLike, user_data_dir: str | os.PathLike, port: int) -> list[str]:
    """Command-line FLAGS for a headless, CDP-controlled instance.

    The returned list does NOT include the executable: launch with
    ``subprocess.Popen([str(chrome), *chrome_args(chrome, udd, port)])``.
    ``chrome`` is only inspected to decide whether ``--headless=new`` applies.
    """
    name = Path(os.fspath(chrome)).name.lower()
    args: list[str] = []
    if "headless_shell" not in name and "headless-shell" not in name:
        args.append("--headless=new")
    args += [
        f"--remote-debugging-port={int(port)}",
        f"--user-data-dir={os.fspath(user_data_dir)}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-extensions",
        "--hide-scrollbars",
        "--remote-allow-origins=*",
        "--allow-file-access-from-files",
    ]
    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")
    return args
