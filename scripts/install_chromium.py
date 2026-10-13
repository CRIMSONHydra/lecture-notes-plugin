# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright"]
# ///
"""Fallback when no Chrome/Edge/Chromium is installed: download Playwright's Chromium.

    uv run install_chromium.py [--data-dir D] [--no-install]

Runs `python -m playwright install chromium` (about 150 MB, cached per user), asks Playwright for
the binary path and writes it to <data>/chrome-path.txt, which notes_lib.chrome.find_chrome() reads.
On Linux you may additionally need system libraries: `playwright install-deps chromium` (needs sudo).
--no-install skips the download and only records an already-cached Chromium (useful offline).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notes_lib import chrome, guard, platform as plat  # noqa: E402


def main(argv=None) -> int:
    plat.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir")
    ap.add_argument("--no-install", action="store_true", help="do not download; only record the cached binary")
    a = ap.parse_args(argv)

    ddir = plat.resolve_data_dir(a.data_dir)
    target = ddir / "chrome-path.txt"
    try:
        guard.assert_writable(target, ddir)
    except guard.GuardError as e:
        print(e, file=sys.stderr)
        return 2

    if not a.no_install:
        print("installing Playwright Chromium (one-time download) ...", flush=True)
        r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
        if r.returncode != 0:
            print("error: `playwright install chromium` failed (see output above)", file=sys.stderr)
            return r.returncode or 1

    exe: Path | None = None
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        try:
            exe = Path(pw.chromium.executable_path)
        finally:
            pw.stop()
    except Exception as e:  # noqa: BLE001
        print(f"note: could not query Playwright for the path ({e}); scanning the cache instead", file=sys.stderr)
    if not exe or not exe.is_file():
        found = chrome.playwright_chromiums()
        exe = found[0] if found else None
    if not exe or not exe.is_file():
        print("error: no Playwright Chromium binary found", file=sys.stderr)
        print(chrome.INSTALL_HINT, file=sys.stderr)
        return 1

    target.write_text(exe.as_posix() + "\n", encoding="utf-8", newline="\n")
    print(f"chromium: {exe}")
    print(f"recorded in {target}")
    if plat.os_kind() == "linux":
        print("Linux: if Chromium fails to start, run `uv run --with playwright playwright install-deps chromium`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
