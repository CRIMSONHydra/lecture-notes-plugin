"""KaTeX and Mermaid, fetched once, cached under ``<data>/cache/assets/`` and inlined.

* KaTeX 0.16.11: the npm tarball (``package/dist``); fonts are inlined into the CSS as woff2 data URIs
  so nothing is loaded over file:// (which Chrome can block).
* Mermaid 10.9.1: ``mermaid.min.js`` from jsDelivr.

Both downloads are sha256-pinned.  ``offline=True`` never touches the network: an empty cache
raises :class:`AssetError` with a clear message.
"""
from __future__ import annotations

import base64
import hashlib
import io
import re
import tarfile
import time
import urllib.request
from pathlib import Path

KATEX_VERSION = "0.16.11"
KATEX_URL = f"https://registry.npmjs.org/katex/-/katex-{KATEX_VERSION}.tgz"
KATEX_SHA256 = "8c5494c5f7d8e8bf73ff3fe64d4bd09a340e4878b7eec9bbd9ef8d8f3e24193f"

MERMAID_VERSION = "10.9.1"
MERMAID_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
MERMAID_SHA256 = "61b335a46df05a7ce1c98378f60e5f3e77a7fb608a1056997e8a649304a936d6"

_UA = {"User-Agent": "lecture-notes-plugin/0.1 (+asset cache)"}


class AssetError(RuntimeError):
    """An asset is not cached and could not (or must not) be downloaded."""


def assets_dir(data_dir) -> Path:
    return Path(data_dir) / "cache" / "assets"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download(url: str, expect_sha: str, what: str, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            got = _sha(data)
            if got != expect_sha:
                raise AssetError(f"{what}: sha256 mismatch for {url} (got {got}, pinned {expect_sha})")
            return data
        except AssetError:
            raise
        except Exception as e:  # network trouble: retry
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise AssetError(f"{what}: could not download {url}: {last}")


def _offline_msg(what: str, d: Path) -> str:
    return (f"{what} is not cached under {d} and --offline was given. Run one build online "
            f"first (this note uses {what}); it is downloaded once and cached.")


# ---------------------------------------------------------------- KaTeX ---
_KATEX_FILES = ("katex.min.js", "contrib/auto-render.min.js", "katex.min.css")


def _katex_dir(data_dir) -> Path:
    return assets_dir(data_dir) / f"katex-{KATEX_VERSION}"


def _katex_cached(d: Path) -> bool:
    return (d / ".ok").is_file() and all((d / f).is_file() for f in _KATEX_FILES) and (d / "fonts").is_dir()


def _fetch_katex(d: Path) -> None:
    blob = _download(KATEX_URL, KATEX_SHA256, f"KaTeX {KATEX_VERSION}")
    d.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        for m in tf.getmembers():
            if not m.isfile() or not m.name.startswith("package/dist/"):
                continue
            rel = m.name[len("package/dist/"):]
            if rel not in _KATEX_FILES and not (rel.startswith("fonts/") and rel.endswith(".woff2")):
                continue
            dest = d / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            f = tf.extractfile(m)
            if f is not None:
                dest.write_bytes(f.read())
    (d / ".ok").write_text(KATEX_SHA256 + "\n", encoding="utf-8", newline="\n")


_FONT_SRC_RE = re.compile(r'src:url\(fonts/([^)]+?)\.woff2\) format\("woff2"\)(?:,url\([^)]*\)\s*format\("[^"]*"\))*')


def _inline_katex_css(d: Path) -> str:
    css = (d / "katex.min.css").read_text(encoding="utf-8")

    def sub(m: re.Match) -> str:
        font = d / "fonts" / f"{m.group(1)}.woff2"
        b64 = base64.b64encode(font.read_bytes()).decode("ascii")
        return f'src:url(data:font/woff2;base64,{b64}) format("woff2")'

    return _FONT_SRC_RE.sub(sub, css)


def get_katex(data_dir, offline: bool = False) -> dict:
    """``{"css": inlined CSS, "js": katex.min.js, "autorender": auto-render.min.js}``."""
    d = _katex_dir(data_dir)
    if not _katex_cached(d):
        if offline:
            raise AssetError(_offline_msg(f"KaTeX {KATEX_VERSION}", d))
        _fetch_katex(d)
    derived = d / "katex.inline.css"
    if not derived.is_file():
        derived.write_text(_inline_katex_css(d), encoding="utf-8", newline="\n")
    return {
        "css": derived.read_text(encoding="utf-8"),
        "js": (d / "katex.min.js").read_text(encoding="utf-8"),
        "autorender": (d / "contrib" / "auto-render.min.js").read_text(encoding="utf-8"),
    }


# -------------------------------------------------------------- Mermaid ---
def _mermaid_path(data_dir) -> Path:
    return assets_dir(data_dir) / f"mermaid-{MERMAID_VERSION}" / "mermaid.min.js"


def get_mermaid(data_dir, offline: bool = False) -> str:
    """The text of ``mermaid.min.js`` (sha256-verified)."""
    p = _mermaid_path(data_dir)
    if p.is_file():
        data = p.read_bytes()
        if _sha(data) == MERMAID_SHA256:
            return data.decode("utf-8")
        p.unlink()                      # corrupt cache: fall through to a re-fetch
    if offline:
        raise AssetError(_offline_msg(f"Mermaid {MERMAID_VERSION}", p.parent))
    data = _download(MERMAID_URL, MERMAID_SHA256, f"Mermaid {MERMAID_VERSION}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return data.decode("utf-8")


def script_safe(js: str) -> str:
    """Make JS safe to inline in a <script> element."""
    return js.replace("</script", "<\\/script").replace("<!--", "<\\!--")
