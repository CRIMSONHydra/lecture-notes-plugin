"""What a command an agent types will actually resolve to on this machine, for the execution classifier.

Tools are often not where a plain command expects them. Python may live behind uv, with the Windows Store
stub on PATH. Node may sit behind fnm, nvm or volta, and be missing from a fresh non-interactive shell.
`gh` may exist only inside WSL. Git Bash rewrites arguments that look like `/paths`. ``describe()`` turns
this into short hints the classifier uses to rewrite a command so it runs. It is computed from PATH as an
agent's shell sees it (this process's own uv environment is removed) and cached for a day in
``<data>/toolchain.json``. The WSL probe runs at most once a day.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import platform as plat

TOOLS = ["python", "python3", "py", "pip", "node", "npm", "npx", "pnpm", "yarn", "java", "go", "cargo", "dotnet",
         "gcc", "g++", "make", "git", "gh", "docker", "kubectl", "helm", "terraform", "curl", "wget", "bash",
         "pwsh", "powershell", "wsl", "uv", "uvx", "fnm", "nvm", "volta", "pyenv", "conda"]
WSL_TOOLS = ["python3", "node", "npm", "gh", "git", "docker", "kubectl", "helm", "terraform", "curl", "uv", "make"]
TTL_S = 24 * 3600


def _agent_path() -> str:
    """PATH without the directories of the Python environment running this code (uv's script env)."""
    own = {Path(sys.prefix).resolve(), Path(sys.executable).resolve().parent}
    keep = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        try:
            rd = Path(d).resolve()
        except (OSError, ValueError):
            continue
        if d and not any(rd == o or rd.is_relative_to(o) for o in own) and "environments-v2" not in d:
            keep.append(d)
    return os.pathsep.join(keep)


def _which(tool: str, path: str) -> str | None:
    hit = shutil.which(tool, path=path)
    if hit and "windowsapps" in hit.lower() and Path(hit).stem.lower().startswith(("python", "py")):
        return "stub"                                      # the Microsoft Store placeholder, not a Python
    return hit


def _wsl_tools() -> list[str]:
    script = "for t in " + " ".join(WSL_TOOLS) + "; do command -v $t >/dev/null 2>&1 && echo $t; done"
    try:
        r = subprocess.run(["wsl", "-e", "sh", "-lc", script], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
        return [t for t in r.stdout.split() if t in WSL_TOOLS] if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def _probe() -> dict:
    path = _agent_path()
    found = {t: _which(t, path) for t in TOOLS}
    kind = plat.os_kind()
    hints = []
    py_ok = any(found.get(t) not in (None, "stub") for t in ("python", "python3", "py"))
    if found.get("uv"):
        hints.append("Python: run it as `uv run python …` (`uv run --with PKG python …` for packages); never a "
                     "bare `python`/`pip`" + (" (on PATH it is only the Microsoft Store stub)" if not py_ok else ""))
    elif not py_ok:
        hints.append("Python: no usable interpreter on PATH")
    if not found.get("node"):
        if found.get("fnm"):
            hints.append("Node: not on PATH in a fresh shell; run node/npm/npx as `fnm exec --using default node …`")
        elif found.get("volta"):
            hints.append("Node: run through volta: `volta run node …`")
        elif (Path.home() / ".nvm" / "nvm.sh").exists():
            hints.append("Node: behind nvm; run as `bash -lc '. ~/.nvm/nvm.sh && node …'`")
        else:
            hints.append("Node: not installed")
    wsl = []
    if kind == "windows" and found.get("wsl"):
        wsl = _wsl_tools()
        missing_host = [t for t in wsl if not found.get(t)]
        if missing_host:
            hints.append(f"WSL has {', '.join(missing_host)} which the Windows host lacks: run them as `wsl <tool> …`")
        hints.append("Bash here is Git Bash: it rewrites arguments that look like /paths (e.g. /mnt/c/…, /tmp/…) "
                     "before passing them to Windows programs such as wsl.exe or docker.exe; prefix such commands "
                     "with MSYS_NO_PATHCONV=1")
    for t in ("gh", "docker", "git", "curl"):
        if not found.get(t) and t not in wsl:
            hints.append(f"{t}: not installed")
    return {"os": kind, "found": {k: v for k, v in found.items() if v}, "wsl_tools": wsl, "hints": hints}


def describe() -> dict:
    """``{os, found, wsl_tools, hints, fingerprint}``; cached for a day per PATH."""
    cache = plat.default_data_dir() / "toolchain.json"
    path_key = hashlib.sha256(_agent_path().encode("utf-8")).hexdigest()[:16]
    try:
        doc = json.loads(cache.read_text(encoding="utf-8"))
        if doc.get("path_key") == path_key and time.time() - doc.get("t", 0) < TTL_S:
            return doc["info"]
    except (OSError, ValueError, KeyError):
        pass
    info = _probe()
    info["fingerprint"] = hashlib.sha256(json.dumps(info["hints"]).encode("utf-8")).hexdigest()[:16]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"t": time.time(), "path_key": path_key, "info": info}), encoding="utf-8",
                         newline="\n")
    except OSError:
        pass
    return info
