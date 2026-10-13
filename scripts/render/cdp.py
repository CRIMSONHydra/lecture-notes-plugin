"""Headless Chrome over the DevTools protocol (CDP).

One Chrome per invocation, one target (tab) per document.  CDP rather than ``--print-to-pdf``
because the running header and the ``page / total`` footer need ``displayHeaderFooter``.

The browser binary and its flags come from ``notes_lib.chrome`` (``find_chrome`` / ``chrome_args``);
the user-data dir lives under the caller's work dir (never system temp, so a snap-confined
Chromium on Linux can still write to it).
"""
from __future__ import annotations

import base64
import json
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket  # websocket-client

from notes_lib import chrome as chromelib

from .layout import PRINT_MARGINS_IN


class ChromeError(RuntimeError):
    """Chrome would not start, died, timed out, or the page reported an unrecoverable problem."""


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Session:
    """A CDP websocket (browser-level or page-level). Events are buffered, not dropped."""

    def __init__(self, ws_url: str, timeout: float = 600):
        try:
            self.ws = websocket.create_connection(ws_url, timeout=timeout, max_size=None)
        except Exception as e:
            raise ChromeError(f"cannot connect to DevTools at {ws_url}: {e}") from e
        self.mid = 0
        self.events: list[dict] = []

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def _recv(self, timeout: float) -> dict:
        self.ws.settimeout(max(0.05, timeout))
        return json.loads(self.ws.recv())

    def send(self, method: str, params: dict | None = None, timeout: float = 300) -> dict:
        self.mid += 1
        mid = self.mid
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise ChromeError(f"{method}: timed out after {timeout:.0f}s")
            try:
                msg = self._recv(left)
            except websocket.WebSocketTimeoutException as e:
                raise ChromeError(f"{method}: timed out after {timeout:.0f}s") from e
            except Exception as e:
                raise ChromeError(f"{method}: connection lost ({e})") from e
            if msg.get("id") == mid:
                if "error" in msg:
                    raise ChromeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def wait_event(self, name: str, timeout: float) -> dict:
        deadline = time.time() + timeout
        seen = 0
        while True:
            while seen < len(self.events):
                ev = self.events[seen]
                seen += 1
                if ev.get("method") == name:
                    return ev
            left = deadline - time.time()
            if left <= 0:
                raise ChromeError(f"timed out waiting for {name}")
            try:
                msg = self._recv(left)
            except websocket.WebSocketTimeoutException as e:
                raise ChromeError(f"timed out waiting for {name}") from e
            except Exception as e:
                raise ChromeError(f"connection lost while waiting for {name} ({e})") from e
            if "method" in msg:
                self.events.append(msg)

    def eval(self, expr: str, *, await_promise: bool = False, timeout: float = 120):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": await_promise}, timeout=timeout)
        if "exceptionDetails" in r:
            d = r["exceptionDetails"]
            raise ChromeError("page script error: " + str(d.get("exception", {}).get("description") or d.get("text")))
        return r.get("result", {}).get("value")


_PROBE_JS = """JSON.stringify({
  katex: document.querySelectorAll('.katex-error, .katex [style*="cc0000"], .katex [style*="204, 0, 0"]').length,
  mermaid: (window.__mermaid_errors|0),
  mermaid_total: document.querySelectorAll('pre.mermaid').length,
  js: (window.__js_errors||[]),
  overflow: Array.from(document.querySelectorAll('pre.code, .tw')).filter(function(e){return e.scrollWidth>e.clientWidth+2}).map(function(e){return {t:(e.textContent||'').trim().slice(0,60),px:e.scrollWidth-e.clientWidth}})
})"""


class Browser:
    """Context manager owning one headless Chrome."""

    def __init__(self, chrome: str | Path, work_dir: str | Path):
        self.chrome = Path(chrome)
        self.work = Path(work_dir).resolve()
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.profile: Path | None = None
        self.browser: _Session | None = None

    # -- lifecycle -----------------------------------------------------
    def __enter__(self) -> "Browser":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        self.port = free_port()
        self.work.mkdir(parents=True, exist_ok=True)
        self.profile = self.work / f"chromeprof{self.port}"
        self.profile.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.chrome), *chromelib.chrome_args(self.chrome, self.profile, self.port), "about:blank"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ChromeError(f"cannot launch {self.chrome}: {e}") from e
        ws_url = None
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise ChromeError(f"Chrome exited at start-up (code {self.proc.returncode}): {self.chrome}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=2) as r:
                    ws_url = json.load(r)["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(0.25)
        if not ws_url:
            self.close()
            raise ChromeError("Chrome DevTools endpoint did not come up within 60 s")
        self.browser = _Session(ws_url)

    def close(self) -> None:
        if self.browser:
            self.browser.close()
            self.browser = None
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except Exception:
                self.proc.kill()
            self.proc = None
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
            self.profile = None

    # -- printing ------------------------------------------------------
    def print_html(self, html_path: str | Path, pdf_path: str | Path, *, header: str, footer: str,
                   js: bool = False, ready_timeout: float = 60, load_timeout: float = 240) -> dict:
        """Load ``html_path``, wait for it (and its JS when ``js``), print to ``pdf_path``.

        Returns ``{"katex_errors", "mermaid_errors", "mermaid_total", "js_errors": [...]}``.
        """
        assert self.browser is not None, "Browser not started"
        tid = self.browser.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        page = _Session(f"ws://127.0.0.1:{self.port}/devtools/page/{tid}")
        try:
            page.send("Page.enable")
            page.send("Runtime.enable")
            nav = page.send("Page.navigate", {"url": Path(html_path).resolve().as_uri()})
            if nav.get("errorText"):
                raise ChromeError(f"cannot load {html_path}: {nav['errorText']}")
            page.wait_event("Page.loadEventFired", load_timeout)
            page.eval("document.fonts.ready.then(()=>true)", await_promise=True, timeout=60)
            if js:
                deadline = time.time() + ready_timeout
                while True:
                    if page.eval("window.__ready===true"):
                        break
                    if time.time() > deadline:
                        raise ChromeError(f"page did not signal window.__ready within {ready_timeout:.0f}s "
                                          f"(KaTeX/Mermaid script hung?): {html_path}")
                    time.sleep(0.25)
            probe = json.loads(page.eval(_PROBE_JS) or "{}")
            time.sleep(0.2)                       # let layout settle after the last paint
            res = page.send("Page.printToPDF", {
                "printBackground": True, "preferCSSPageSize": True,
                "displayHeaderFooter": True,
                "headerTemplate": header, "footerTemplate": footer,
                **PRINT_MARGINS_IN,
                "transferMode": "ReturnAsStream",
            }, timeout=600)
            handle = res["stream"]
            chunks: list[bytes] = []
            while True:
                r = page.send("IO.read", {"handle": handle, "size": 1 << 20})
                data = r["data"]
                chunks.append(base64.b64decode(data) if r.get("base64Encoded") else data.encode())
                if r.get("eof"):
                    break
            page.send("IO.close", {"handle": handle})
            Path(pdf_path).write_bytes(b"".join(chunks))
            errs = list(probe.get("js") or [])
            for ev in page.events:
                m = ev.get("method")
                if m == "Runtime.exceptionThrown":
                    d = ev["params"]["exceptionDetails"]
                    errs.append("exception: " + str(d.get("exception", {}).get("description") or d.get("text")))
                elif m == "Runtime.consoleAPICalled" and ev["params"].get("type") == "error":
                    args = ev["params"].get("args", [])
                    errs.append("console.error: " + " ".join(str(a.get("value", a.get("description", ""))) for a in args))
            return {
                "katex_errors": int(probe.get("katex", 0)),
                "mermaid_errors": int(probe.get("mermaid", 0)),
                "mermaid_total": int(probe.get("mermaid_total", 0)),
                "overflow_blocks": list(probe.get("overflow") or []),
                "js_errors": errs,
            }
        finally:
            page.close()
            try:
                self.browser.send("Target.closeTarget", {"targetId": tid}, timeout=15)
            except Exception:
                pass
