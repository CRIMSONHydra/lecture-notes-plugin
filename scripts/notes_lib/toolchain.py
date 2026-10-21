"""What a command an agent types will actually resolve to on this machine, for the execution classifier.

Tools are often not where a plain command expects them: Python behind uv, pyenv or conda (with the Windows
Store stub on PATH), Node behind fnm, nvm, volta, mise or asdf, Java behind SDKMAN, Rust in ~/.cargo/bin, a
tool only inside a WSL distribution. ``describe()`` detects all of that **on the machine it runs on** and
turns it into short hints the classifier uses to rewrite a command so it runs. Nothing about one machine
is assumed on another; only how each manager is invoked is fixed (``RESOLVERS``).

PATH is read as an agent's shell sees it (this process's own uv environment is removed). Results are cached
per PATH for a day in ``<data>/toolchain.json``; probes are cheap local calls with short timeouts, and a
stopped WSL distribution is never booted except the default one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import platform as plat

PROBE = ["python", "python3", "py", "pip", "node", "npm", "npx", "pnpm", "yarn", "deno", "bun", "java", "javac",
         "mvn", "gradle", "kotlin", "go", "cargo", "rustc", "ruby", "gem", "dotnet", "gcc", "g++", "make", "git",
         "gh", "docker", "kubectl", "helm", "terraform", "curl", "wget", "bash", "pwsh", "powershell", "wsl",
         "uv", "uvx", "fnm", "nvm", "volta", "mise", "asdf", "pyenv", "conda", "rustup", "sdk"]
RESOLVE = ["python", "pip", "node", "npm", "npx", "pnpm", "yarn", "deno", "bun", "java", "javac", "mvn", "gradle",
           "kotlin", "go", "cargo", "rustc", "ruby", "gem", "dotnet", "terraform", "kubectl", "helm", "gh", "docker"]
WSL_TOOLS = ["python3", "node", "npm", "gh", "git", "docker", "kubectl", "helm", "terraform", "curl", "uv", "make",
             "go", "java", "cargo"]
NOT_INSTALLED_NOTE = ["gh", "docker", "git", "curl"]
TTL_S = 24 * 3600
_INTERNAL_DISTROS = re.compile(r"^(docker-desktop(-data)?|rancher-desktop(-data)?|podman-machine.*)$", re.I)


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


def _out(argv: list[str], timeout: float = 8) -> str | None:
    """stdout of a quick local probe, or None if it fails."""
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, WSL_UTF8="1", NO_COLOR="1"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    raw = r.stdout
    return (raw.decode("utf-16-le", "replace") if b"\x00" in raw else raw.decode("utf-8", "replace")).replace("\x00", "")


# ---- resolvers: (tool, ctx) -> invocation template with {tool}, or None -------------------------------------

def _fnm(tool: str, c: dict) -> str | None:
    if tool not in ("node", "npm", "npx", "corepack") or not c["found"].get("fnm"):
        return None
    listing = c.setdefault("_fnm", _out(["fnm", "list"]) or "")
    versions = re.findall(r"(v\d+\.\d+\.\d+)(.*)", listing)
    if not versions:
        return None
    default = next((v for v, rest in versions if "default" in rest), None)
    using = "default" if default else sorted(versions, key=lambda x: [int(n) for n in x[0][1:].split(".")])[-1][0]
    return f"fnm exec --using {using} {{tool}}"


def _volta(tool: str, c: dict) -> str | None:
    return "volta run {tool}" if tool in ("node", "npm", "npx", "pnpm", "yarn") and c["found"].get("volta") else None


def _nvm(tool: str, c: dict) -> str | None:
    sh = Path.home() / ".nvm" / "nvm.sh"
    if tool not in ("node", "npm", "npx", "corepack") or c["os"] == "windows" or not sh.exists():
        return None
    alias = "default" if (Path.home() / ".nvm" / "alias" / "default").exists() else "node"
    return f"bash -lc '. ~/.nvm/nvm.sh >/dev/null && nvm exec {alias} {{tool}} …'"


def _mise(tool: str, c: dict) -> str | None:
    if not c["found"].get("mise"):
        return None
    return "mise exec -- {tool}" if _out(["mise", "which", tool], 5) else None


def _asdf(tool: str, c: dict) -> str | None:
    shim = Path(os.environ.get("ASDF_DATA_DIR", Path.home() / ".asdf")) / "shims" / tool
    return f"{shim.as_posix()}" if shim.exists() else None


def _pyenv(tool: str, c: dict) -> str | None:
    if tool not in ("python", "pip") or not c["found"].get("pyenv"):
        return None
    return "pyenv exec {tool}" if _out(["pyenv", "which", tool], 5) else None


def _uv(tool: str, c: dict) -> str | None:
    if tool == "python" and c["found"].get("uv"):
        return "uv run python"
    if tool == "pip" and c["found"].get("uv"):
        return "uv pip"                                  # or `uv run --with PKG python …` for a one-off package
    return None


def _conda(tool: str, c: dict) -> str | None:
    conda = c["found"].get("conda") or os.environ.get("CONDA_EXE")
    return "conda run -n base {tool}" if tool in ("python", "pip") and conda else None


def _sdkman(tool: str, c: dict) -> str | None:
    init = Path(os.environ.get("SDKMAN_DIR", Path.home() / ".sdkman")) / "bin" / "sdkman-init.sh"
    if tool not in ("java", "javac", "mvn", "gradle", "kotlin") or not init.exists():
        return None
    cand = Path(init).parents[1] / "candidates"
    owner = {"javac": "java", "mvn": "maven"}.get(tool, tool)
    if not (cand / owner / "current").exists():
        return None
    return "bash -lc 'source \"$SDKMAN_DIR/bin/sdkman-init.sh\" 2>/dev/null || source ~/.sdkman/bin/sdkman-init.sh; {tool} …'"


def _known_dirs(tool: str, c: dict) -> str | None:
    home = Path.home()
    for d in (home / ".cargo" / "bin", home / "go" / "bin", home / ".bun" / "bin", home / ".deno" / "bin",
              home / ".local" / "bin", home / ".dotnet" / "tools", Path("/usr/local/go/bin"),
              Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        hit = shutil.which(tool, path=str(d))
        if hit:
            return Path(hit).as_posix()
    return None


RESOLVERS: list[tuple[str, Callable[[str, dict], str | None]]] = [
    ("uv", _uv), ("fnm", _fnm), ("volta", _volta), ("nvm", _nvm), ("mise", _mise), ("asdf", _asdf),
    ("pyenv", _pyenv), ("conda", _conda), ("sdkman", _sdkman), ("install dir", _known_dirs),
]


def _wsl(c: dict) -> dict:
    """``{"default": name, "distros": {name: [tools] or None (not probed: stopped)}}`` on Windows with WSL."""
    if c["os"] != "windows" or not c["found"].get("wsl"):
        return {}
    listing = _out(["wsl", "-l", "-v"], 10) or ""
    distros, default = {}, None
    for line in listing.splitlines()[1:]:
        m = re.match(r"^\s*(\*)?\s*(\S+)\s+(\S+)", line)
        if not m or _INTERNAL_DISTROS.match(m.group(2)):
            continue
        name, running = m.group(2), m.group(3).lower() == "running"
        default = name if m.group(1) else default
        distros[name] = running
    script = "for t in " + " ".join(WSL_TOOLS) + "; do command -v $t >/dev/null 2>&1 && echo $t; done"
    out = {}
    for name, running in distros.items():
        if name == default or running:                   # never boot a stopped non-default distro
            got = _out(["wsl", "-d", name, "-e", "sh", "-lc", script], 30)
            out[name] = [t for t in (got or "").split() if t in WSL_TOOLS]
        else:
            out[name] = None
    return {"default": default, "distros": out}


def _probe() -> dict:
    path = _agent_path()
    c: dict = {"os": plat.os_kind(), "found": {t: _which(t, path) for t in PROBE}}
    found = c["found"]
    hints: list[str] = []
    py_ok = any(found.get(t) not in (None, "stub") for t in ("python", "python3", "py"))
    groups: dict[str, list[str]] = {}
    resolved: set[str] = set()
    for tool in RESOLVE:
        on_path = found.get(tool) not in (None, "stub") or (tool == "python" and py_ok)
        if on_path and not (tool in ("python", "pip") and found.get("uv")):
            continue                                     # usable as typed (uv is still preferred for Python)
        for _name, res in RESOLVERS:
            how = res(tool, c)
            if how:
                groups.setdefault(how, []).append(tool)
                resolved.add(tool)
                break
    for how, tools in groups.items():
        if "{tool}" in how:
            hints.append(f"{', '.join(tools)}: not usable as typed here; run as `{how.replace('{tool}', '<tool>')}`")
        else:
            hints.append(f"{', '.join(tools)}: run as `{how}`")
    if found.get("python") == "stub" or found.get("python3") == "stub":
        hints.append("`python`/`python3` on PATH is the Microsoft Store placeholder, not an interpreter")
    wsl = _wsl(c)
    if wsl:
        covered = resolved | ({"python3"} if "python" in resolved or py_ok else set())
        host_missing = {t for t in WSL_TOOLS if (not found.get(t) or found.get(t) == "stub") and t not in covered}
        for name, tools in wsl["distros"].items():
            sel = "" if name == wsl["default"] else f"-d {name} "
            if tools is None:
                hints.append(f"WSL distribution {name} is stopped (not probed); avoid booting it")
            elif host_missing & set(tools):
                hints.append(f"WSL {name}{' (default)' if not sel else ''} has "
                             f"{', '.join(sorted(host_missing & set(tools)))}, which the Windows host lacks: run as "
                             f"`wsl {sel}<tool> …`")
    for t in NOT_INSTALLED_NOTE:
        in_wsl = any(t in (v or []) for v in (wsl.get("distros") or {}).values())
        if not found.get(t) and not in_wsl:
            hints.append(f"{t}: not installed")
    shell_hints = {"bash": [], "powershell": []}
    if c["os"] == "windows":
        shell_hints["bash"].append(
            "Bash here is Git Bash: it rewrites arguments that look like /paths (/mnt/c/…, /tmp/…) before passing "
            "them to Windows programs such as wsl.exe or docker.exe; prefix such commands with MSYS_NO_PATHCONV=1")
        shell_hints["powershell"].append("PowerShell: set variables as `$env:NAME='value'; cmd`; there is no `rm -rf`, "
                                         "use `Remove-Item -Recurse -Force`")
    return {"os": c["os"], "found": {k: v for k, v in found.items() if v}, "wsl": wsl, "hints": hints,
            "shell_hints": shell_hints}


def describe() -> dict:
    """``{os, found, wsl, hints, shell_hints, fingerprint}``; cached for a day per PATH."""
    cache = plat.default_data_dir() / "toolchain.json"
    path_key = hashlib.sha256(_agent_path().encode("utf-8")).hexdigest()[:16]
    try:
        doc = json.loads(cache.read_text(encoding="utf-8"))
        if doc.get("path_key") == path_key and time.time() - doc.get("t", 0) < TTL_S and doc.get("v") == 2:
            return doc["info"]
    except (OSError, ValueError, KeyError):
        pass
    info = _probe()
    info["fingerprint"] = hashlib.sha256(json.dumps([info["hints"], info["shell_hints"]]).encode("utf-8")).hexdigest()[:16]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"v": 2, "t": time.time(), "path_key": path_key, "info": info}),
                         encoding="utf-8", newline="\n")
    except OSError:
        pass
    return info


def hints_for(info: dict, shell: str) -> list[str]:
    """The hints that apply to a command typed in ``shell`` (any label containing "powershell" or "bash")."""
    kind = "powershell" if "powershell" in (shell or "").lower() else "bash"
    return list(info.get("hints", [])) + list((info.get("shell_hints") or {}).get(kind, []))
