"""Undo what approved commands left behind: run the classifier's cleanup plans, then sweep labelled Docker objects.

The classifier names a cleanup for everything an approved command creates; ``exec_classifier.record`` writes
it to ``<data>/exec-ledger/<run>.jsonl``. ``cleanup(run_id, agent_id)`` runs those plans once each, then
removes every Docker object labelled ``lecture-notes.run=<run_id>`` (the classifier adds that label when it
rewrites a docker command). A cleanup command is itself untrusted model output, so it runs only if it is a
plain removal: ``docker rm/rmi/stop/kill``, ``docker volume|network|image|container rm``, ``rm``/``rmdir``
of a path inside a temp or the project's work directory, or ``kill``/``Stop-Process`` of a pid. Anything
else is logged and skipped. The Docker CLI is called only when the ledger shows Docker was used and the
engine is already running, so a sweep never starts Docker Desktop.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from . import exec_classifier as ec, exec_policy as ep, platform as plat

_DOCKER_RM = re.compile(r"^docker(\.exe)?\s+((container|image|volume|network)\s+rm|rm|rmi|stop|kill)\b")


def _safe_path(p: str, roots: list[Path]) -> bool:
    try:
        rp = Path(os.path.expanduser(p)).resolve()
    except (OSError, ValueError):
        return False
    return any(rp != r and rp.is_relative_to(r) for r in roots)


def safe_cleanup(cmd: str, roots: list[Path]) -> bool:
    """True when ``cmd`` is a plain removal of something a notes run may have created."""
    if ep.floor(cmd) or re.search(r"[;&|`$<>]", cmd):
        return False
    try:
        words = shlex.split(cmd)
    except ValueError:
        return False
    if _DOCKER_RM.match(cmd):                  # only objects this job named; the student's own are never touched
        skip = 3 if words[1] in ("container", "image", "volume", "network") else 2
        targets = [a for a in words[skip:] if not a.startswith("-")]
        return bool(targets) and all(t.startswith("notes-") for t in targets)
    if not words:
        return False
    w, args = ep._norm(words[0]), words[1:]
    if w in ("rm", "rmdir", "remove-item", "del"):
        paths = [a for a in args if not a.startswith("-")]
        return bool(paths) and all(_safe_path(p, roots) for p in paths)
    if w in ("kill", "stop-process"):
        return all(re.fullmatch(r"-\w+|-?\d+|-id|-force", a, re.I) for a in args) and any(a.isdigit() for a in args)
    return False


def _roots(project: str | None) -> list[Path]:
    roots = [Path(tempfile.gettempdir()).resolve()]
    if project:
        roots.append((Path(project) / ".notes-plugin-internal" / "work").resolve())
    return roots


def _shell(cmd: str) -> list[str]:
    if plat.os_kind() == "windows" and ep._norm(cmd.split()[0]) in ("remove-item", "stop-process"):
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd]
    return shlex.split(cmd)


def _run(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, type(e).__name__


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


def cleanup(run_id: str, agent_id: str | None = None, project: str | None = None) -> list[str]:
    """Run pending cleanup plans for ``run_id`` (only ``agent_id``'s when given). Returns what was done."""
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
        for cmd in r.get("cleanup") or []:
            if cmd in done_cmds:
                continue
            done_cmds.add(cmd)
            if not safe_cleanup(cmd, roots):
                report.append(f"skipped (not a plain removal): {cmd}")
                log.append({"t": time.time(), "cleanup_cmd": cmd, "status": "skipped"})
                continue
            code, out = _run(_shell(cmd))
            gone = code == 0 or re.search(r"no such|not found|does not exist|cannot find", out, re.I)
            report.append(f"{'ok' if gone else 'FAILED'}: {cmd}")
            log.append({"t": time.time(), "cleanup_cmd": cmd, "status": "ok" if gone else "failed", "out": out})
    if used_docker and ec.docker_running():
        report += _docker_sweep(run_id)
    if log:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in log)
    return report
