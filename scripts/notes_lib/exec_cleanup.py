"""Undo what approved commands left behind: run the classifier's cleanup plans, then sweep labelled Docker objects.

The classifier names a cleanup for everything an approved command creates; ``exec_classifier.record`` writes
it to ``<data>/exec-ledger/<run>.jsonl``. ``cleanup(run_id, agent_id)`` carries out each plan once, then
removes every Docker object labelled ``lecture-notes.run=<run_id>``. A cleanup command is untrusted model
output, so it is parsed, never handed to a shell, and only two kinds are carried out:

* Docker removals (``docker rm/rmi/stop/kill``, ``docker container|image|volume|network rm``) of objects
  named ``notes-*``;
* file or directory removals (``rm``, ``rmdir``, ``Remove-Item``, ``del``) of paths inside the system temp
  directory, never inside a notes project (notes, PDFs and work files are the job's output). These are done in
  Python, so they work on every OS. Git Bash paths (``/tmp/…``, ``/c/…``) and WSL mounts (``/mnt/c/…``) are
  mapped to real paths.

``prefilter`` runs when a command is approved, before it executes: a removal of anything that already exists
is dropped then, because the command cannot have created it.

A plan for something created inside WSL (``wsl …`` prefix, or a command that ran through ``wsl``) runs
through ``wsl`` and may only touch ``/tmp/…`` there. Docker is used only when the ledger shows Docker was
used and the engine is already running, so a sweep never starts Docker Desktop. Everything else is skipped
and reported.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import exec_classifier as ec, exec_policy as ep, platform as plat

_DOCKER_OPS = {("rm",), ("rmi",), ("stop",), ("kill",), ("container", "rm"), ("image", "rm"), ("volume", "rm"),
               ("network", "rm"), ("container", "stop"), ("container", "kill")}
_RM_WORDS = {"rm", "rmdir", "remove-item", "del", "ri", "rd"}


def _host_path(p: str) -> Path:
    """A path as written in a cleanup command, mapped to this OS (Git Bash and WSL-mount forms on Windows)."""
    p = os.path.expandvars(os.path.expanduser(p.strip("\"'")))
    p = re.sub(r"^\$env:(TEMP|TMP)", lambda m: tempfile.gettempdir().replace("\\", "/"), p, flags=re.I)
    if plat.os_kind() == "windows":
        m = re.match(r"^/(?:mnt/)?([a-zA-Z])(/.*)?$", p)
        if m:
            p = f"{m.group(1).upper()}:{m.group(2) or '/'}"
        elif p == "/tmp" or p.startswith("/tmp/"):
            p = tempfile.gettempdir() + p[4:]
    return Path(p)


def _inside(p: Path, roots: list[Path]) -> bool:
    try:
        rp = p.resolve()
    except (OSError, ValueError):
        return False
    return any(rp != r and rp.is_relative_to(r) for r in roots)


def _in_notes_project(p: Path) -> bool:
    """True for anything inside a notes project: notes, PDFs and work files are the job's output, never leftovers."""
    try:
        rp = p.resolve()
    except (OSError, ValueError):
        return True
    return any((d / ".notes-plugin-internal").is_dir() for d in (rp, *rp.parents))


def plan(cmd: str, roots: list[Path], via_wsl: bool = False) -> tuple[str, list] | None:
    """How to carry out one cleanup command safely, or None when it is not a plain removal."""
    if ep.floor(cmd) or re.search(r"[;&|`<>]|\$\(", cmd):
        return None
    try:
        words = shlex.split(cmd)
    except ValueError:
        return None
    if words and ep._norm(words[0]) == "wsl":
        words = ep._wsl_inner(words[1:])
        via_wsl = True
    if not words:
        return None
    w = ep._norm(words[0])
    if w == "docker":
        args = [a for a in words[1:] if not a.startswith("-")]
        op = tuple(args[:2]) if tuple(args[:2]) in _DOCKER_OPS else tuple(args[:1])
        targets = args[len(op):]
        if op in _DOCKER_OPS and targets and all(t.startswith("notes-") for t in targets):
            return ("argv", (["wsl", "--"] if via_wsl else []) + words)
        return None
    if w in _RM_WORDS:
        paths = [a for a in words[1:] if not a.startswith("-")]
        if not paths:
            return None
        if via_wsl:                                    # inside WSL: /tmp only
            ok = all(re.fullmatch(r"/tmp/[\w.-][\w./-]*", p) and ".." not in p for p in paths)
            return ("argv", ["wsl", "--", "rm", "-rf", *paths]) if ok else None
        hp = [_host_path(p) for p in paths]
        return ("remove", hp) if all(_inside(p, roots) and not _in_notes_project(p) for p in hp) else None
    return None


def prefilter(cleanups: list[str]) -> list[str]:
    """Called at approval time, BEFORE the command runs: keep only plans that remove something which does not
    exist yet (the command will create it). A path that already exists predates the command and is never
    the command's leftover, so its removal is dropped here and can never run later."""
    keep = []
    for cmd in cleanups or []:
        how = plan(cmd, _roots(None))
        if how is None:
            continue
        if how[0] == "remove" and any(p.exists() or p.is_symlink() for p in how[1]):
            continue
        keep.append(cmd)
    return keep


def safe_cleanup(cmd: str, roots: list[Path]) -> bool:
    """True when ``cmd`` is a plain removal of something a notes run may have created."""
    return plan(cmd, roots) is not None


def _roots(project: str | None = None) -> list[Path]:
    """Where a cleanup may remove files: the system temp directory only (``project`` is kept for callers;
    nothing inside a notes project is ever removed)."""
    return [Path(tempfile.gettempdir()).resolve()]


def _run(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, type(e).__name__


def _remove(paths: list[Path]) -> tuple[bool, str]:
    for p in paths:
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            elif p.exists() or p.is_symlink():
                p.unlink()
        except OSError as e:
            return False, str(e)[:200]
    return True, ""


def _docker_sweep(run_id: str) -> list[str]:
    done = []
    label = f"label=lecture-notes.run={run_id}"
    for kind, ls, rm in (("container", ["docker", "ps", "-aq", "--filter", label], ["docker", "rm", "-f"]),
                         ("volume", ["docker", "volume", "ls", "-q", "--filter", label], ["docker", "volume", "rm", "-f"]),
                         ("network", ["docker", "network", "ls", "-q", "--filter", label], ["docker", "network", "rm"]),
                         ("image", ["docker", "images", "-q", "--filter", label], ["docker", "rmi", "-f"])):
        code, out = _run(ls, 30)
        ids = sorted(set(out.split())) if code == 0 else []
        if ids:
            code, _ = _run(rm + ids, 120)
            done.append(f"docker {kind}s {' '.join(i[:12] for i in ids)}: {'removed' if code == 0 else 'FAILED'}")
    return done


def _ran_in_wsl(command: str) -> bool:
    try:
        words = shlex.split(command or "")
    except ValueError:
        return False
    while words and re.match(r"^[A-Za-z_]\w*=", words[0]):
        words = words[1:]
    return bool(words) and ep._norm(words[0]) == "wsl"


def cleanup(run_id: str, agent_id: str | None = None, project: str | None = None) -> list[str]:
    """Carry out pending cleanup plans for ``run_id`` (only ``agent_id``'s when given). Returns what was done."""
    path = ec.ledger_path(run_id)
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    done_cmds = {r["cleanup_cmd"] for r in rows if "cleanup_cmd" in r}
    roots = _roots(project)
    report, log, used_docker = [], [], False
    for r in rows:
        if "cleanup_cmd" in r or (agent_id and r.get("agent") != agent_id):
            continue
        used_docker |= "docker" in (r.get("command") or "")
        in_wsl = _ran_in_wsl(r.get("command") or "")
        for cmd in r.get("cleanup") or []:
            if cmd in done_cmds:
                continue
            done_cmds.add(cmd)
            how = plan(cmd, roots, via_wsl=in_wsl)
            if how is None:
                report.append(f"skipped (not a plain removal of this run's objects): {cmd}")
                log.append({"t": time.time(), "cleanup_cmd": cmd, "status": "skipped"})
                continue
            if how[0] == "remove":
                ok, out = _remove(how[1])
            else:
                code, out = _run(how[1])
                ok = code == 0 or bool(re.search(r"no such|not found|does not exist|cannot find", out, re.I))
            report.append(f"{'ok' if ok else 'FAILED'}: {cmd}")
            log.append({"t": time.time(), "cleanup_cmd": cmd, "status": "ok" if ok else "failed", "out": out})
    if used_docker and ec.docker_running():
        report += _docker_sweep(run_id)
    if log:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in log)
    return report
