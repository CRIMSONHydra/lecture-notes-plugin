# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Self-test for scripts/notes_lib/toolchain.py: hints are detected per machine, never assumed.

Simulates machines this one is not (macOS with pyenv, Linux with nvm and SDKMAN, Windows with fnm but no
default, a stopped WSL distribution) by replacing the PATH lookup, the probe runner, the OS and HOME.

    uv run tests/selftest_toolchain.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from notes_lib import toolchain as t  # noqa: E402

REAL = (t._which, t._out, t.plat.os_kind, os.environ.get("HOME"), os.environ.get("USERPROFILE"))
bad: list[tuple] = []


def machine(os_kind: str, on_path: dict, outs: dict, home_files: tuple[str, ...] | list[str] = ()) -> dict:
    home = Path(tempfile.mkdtemp(prefix="notes-tc-home-"))
    for rel in home_files:
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\n", encoding="utf-8")
        p.chmod(0o755)
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
    t._which = lambda tool, path: on_path.get(tool)
    t._out = lambda argv, timeout=8: next((v for k, v in outs.items() if " ".join(argv).startswith(k)), None)
    t.plat.os_kind = lambda: os_kind
    try:
        return t._probe()
    finally:
        t._which, t._out, t.plat.os_kind = REAL[0], REAL[1], REAL[2]
        for k, v in (("HOME", REAL[3]), ("USERPROFILE", REAL[4])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def expect(name: str, info: dict, has: list[str], lacks: list[str], shell: str = "bash") -> None:
    text = "\n".join(t.hints_for(info, shell))
    for h in has:
        if h not in text:
            bad.append((name, "missing hint", h, text))
    for h in lacks:
        if h in text:
            bad.append((name, "unexpected hint", h, text))


# 1. Windows like the author's: uv, fnm with a default, Store stub, gh only in WSL, docker-desktop distro
win = machine("windows", {"uv": "C:/u/uv.exe", "fnm": "C:/f/fnm.exe", "python3": "stub", "git": "C:/g/git.exe",
                          "docker": "C:/d/docker.exe", "curl": "C:/g/curl.exe", "wsl": "C:/w/wsl.exe"},
              {"fnm list": "* v24.12.0 default\n* system\n",
               "wsl -l -v": "  NAME STATE VERSION\n* Ubuntu Running 2\n  docker-desktop Running 2\n  Old Stopped 2\n",
               "wsl -d Ubuntu": "python3\ngh\nterraform\n"})
expect("windows", win, ["uv run python", "fnm exec --using default <tool>", "Microsoft Store placeholder",
                        "has gh, terraform", "wsl <tool>", "Old is stopped", "MSYS_NO_PATHCONV=1"],
       ["docker-desktop", "python3, ", "gh: not installed"], shell="bash (Git Bash on Windows)")
expect("windows/powershell", win, ["$env:NAME", "fnm exec"], ["MSYS_NO_PATHCONV"], shell="powershell")
if win["wsl"]["distros"].get("Old") is not None:
    bad.append(("windows", "a stopped non-default distro must not be probed", win["wsl"]))

# 2. Windows with fnm but no default alias: use the newest installed version
nodef = machine("windows", {"fnm": "C:/f/fnm.exe", "python": "C:/p/python.exe"},
                {"fnm list": "* v20.11.1\n* v22.3.0\n* system\n"})
expect("fnm-no-default", nodef, ["fnm exec --using v22.3.0 <tool>"], ["--using default", "uv run"])

# 3. macOS: pyenv Python, Node on PATH, Homebrew-free, no WSL anywhere
mac = machine("macos", {"node": "/usr/local/bin/node", "npm": "/usr/local/bin/npm", "pyenv": "/opt/pyenv",
                        "git": "/usr/bin/git", "curl": "/usr/bin/curl", "gh": "/opt/homebrew/bin/gh",
                        "docker": "/usr/local/bin/docker"},
              {"pyenv which python": "/Users/x/.pyenv/versions/3.12.4/bin/python\n"})
expect("macos", mac, ["pyenv exec <tool>"], ["fnm", "WSL", "MSYS", "Store placeholder", "node, npm", "not installed"])

# 4. Linux: nvm without a default alias, SDKMAN Java, cargo in ~/.cargo/bin, no gh at all
lin = machine("linux", {"python3": "/usr/bin/python3", "git": "/usr/bin/git", "curl": "/usr/bin/curl"}, {},
              [".nvm/nvm.sh", ".sdkman/bin/sdkman-init.sh", ".sdkman/candidates/java/current/bin/java",
               ".cargo/bin/cargo" + (".exe" if sys.platform == "win32" else "")])   # which() needs PATHEXT on Windows
expect("linux", lin, ["nvm exec node <tool>", "sdkman-init.sh", ".cargo/bin/cargo", "gh: not installed"],
       ["fnm", "WSL", "MSYS", "nvm exec default"])

# 5. mise and asdf
mise = machine("linux", {"mise": "/usr/bin/mise", "git": "/usr/bin/git", "curl": "/usr/bin/curl"},
               {"mise which go": "/home/x/.local/share/mise/installs/go/1.22/bin/go\n"}, [".asdf/shims/ruby"])
expect("mise/asdf", mise, ["mise exec -- <tool>", ".asdf/shims/ruby"], ["fnm"])

# the real machine: whatever it is, describe() must work and be cacheable
os.environ["LECTURE_NOTES_DATA"] = tempfile.mkdtemp(prefix="notes-tc-data-")
real = t.describe()
if not real.get("fingerprint") or t.describe() != real:
    bad.append(("describe", "must return a stable cached result", real))

for b in bad:
    print(*b, sep=" | ")
print("FAIL" if bad else "OK", f"{len(bad)} failures" if bad else "6 machines")
sys.exit(1 if bad else 0)
