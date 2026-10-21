"""Undo what approved commands left behind: carry out the classifier's cleanup plans, then sweep labelled Docker objects.

The classifier names a cleanup for everything an approved command creates; ``exec_classifier.record`` writes
it to ``<data>/exec-ledger/<run>.jsonl``. ``cleanup(run_id, agent_id)`` carries out each plan once per ledger
row, then removes the Docker objects labelled for the agent (SubagentStop) or the run (``guard.py clear``).
A cleanup command is untrusted model output, so it is parsed, never handed to a shell, and only two kinds
are carried out:

* Docker removals (``docker rm/rmi/stop/kill``, ``docker container|image|volume|network rm``) of objects
  named ``notes-*``. The object is looked up by exact name **and** the run's label before removal, so a
  student's own ``notes-db`` is never touched; flags in the plan are dropped, never executed.
* file or directory removals (``rm``, ``rmdir``, ``Remove-Item``, ``del``, ``rd``) of paths inside a temp
  directory whose top-level entry is named ``notes-*``, never inside a notes project (notes, PDFs and work
  files are the job's output), and only when the path came into being during this run (not before its first
  approval; see ``_created_after`` for why not "after the command's own approval").
  Done in Python, so it works on every OS; Git Bash paths (``/tmp/…``, ``/c/…``), WSL mounts (``/mnt/c/…``)
  and ``$env:TEMP`` are mapped; read-only files (a clone's ``.git/objects``) are made writable first.

``prefilter`` runs when a command is approved, before it executes: a removal of anything that already exists
is dropped then, because the command cannot have created it (for Docker, by ``docker inspect`` when the
engine is up).

A plan for something created inside WSL (``wsl -d NAME …``) runs in that distribution and may only touch
``/tmp/notes-…`` there. Docker is used only when the ledger shows Docker was used and the engine is already
running, so a sweep never starts Docker Desktop. Everything else is skipped and reported.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from . import exec_classifier as ec, exec_policy as ep, platform as plat

_DOCKER_OPS = {("rm",): "container", ("rmi",): "image", ("stop",): "container", ("kill",): "container",
               ("container", "rm"): "container", ("container", "stop"): "container", ("container", "kill"): "container",
               ("image", "rm"): "image", ("volume", "rm"): "volume", ("network", "rm"): "network"}
_RM_WORDS = {"rm", "rmdir", "remove-item", "del", "ri", "rd", "erase"}
_WIN_FLAG = re.compile(r"^/[a-zA-Z]$")
_NAME = re.compile(r"^notes-[\w.-]+$")
_WSL_TMP = re.compile(r"^/tmp/notes-[\w.-]+(/[\w.-]+)*$")


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


def _roots(project: str | None = None) -> list[Path]:
    """Where a cleanup may remove files: the temp directories (``project`` is kept for callers; nothing inside a
    notes project is ever removed)."""
    cands = [tempfile.gettempdir()]
    if plat.os_kind() != "windows":
        cands += ["/tmp", "/var/tmp"]
    out = []
    for c in cands:
        try:
            r = Path(c).resolve()
        except OSError:
            continue
        if r not in out:
            out.append(r)
    return out


def _owned_temp(p: Path, roots: list[Path]) -> bool:
    """Inside a temp root, and the top-level entry there is a ``notes-*`` name (what this job may create)."""
    try:
        rp = p.resolve()
    except (OSError, ValueError):
        return False
    for r in roots:
        if rp != r and rp.is_relative_to(r):
            return _NAME.match(rp.relative_to(r).parts[0]) is not None
    return False


def _in_notes_project(p: Path) -> bool:
    """True for anything inside a notes project: notes, PDFs and work files are the job's output, never leftovers."""
    try:
        rp = p.resolve()
    except (OSError, ValueError):
        return True
    return any((d / ".notes-plugin-internal").is_dir() for d in (rp, *rp.parents))


def _tokens(cmd: str) -> list[str] | None:
    if ep.floor(cmd) or re.search(r"[;&|`<>]|\$\(", cmd):
        return None
    if re.search(r"[A-Za-z]:\\|\\\\|\$env:\w+\\", cmd):              # Windows paths: keep their separators
        cmd = cmd.replace("\\", "/")
    try:
        return ep._words(cmd)
    except ValueError:
        return None


def plan(cmd: str, roots: list[Path], via_wsl: bool = False, distro: str | None = None) -> tuple | None:
    """How to carry out one cleanup command safely, or None when it is not a plain removal of a ``notes-*`` thing.

    ``("remove", [Path])`` for host paths, ``("wsl_rm", distro, [paths])`` inside WSL,
    ``("docker", kind, [names], distro)`` for Docker objects (removed by name and label, never by raw argv)."""
    words = _tokens(cmd)
    if not words:
        return None
    if ep._norm(words[0]) == "wsl":
        distro, words = ep.wsl_parts(words[1:])
        via_wsl = True
    if not words:
        return None
    w = ep._norm(words[0])
    if w == "docker":
        args = [a for a in words[1:] if not a.startswith("-")]
        op = tuple(args[:2]) if tuple(args[:2]) in _DOCKER_OPS else tuple(args[:1])
        targets = args[len(op):]
        if op in _DOCKER_OPS and targets and all(_NAME.match(t.split(":")[0]) for t in targets):
            return ("docker", _DOCKER_OPS[op], targets, distro if via_wsl else None)
        return None
    if w in _RM_WORDS:
        paths = [a for a in words[1:] if not a.startswith("-") and not _WIN_FLAG.match(a)]
        if not paths:
            return None
        if via_wsl:
            return ("wsl_rm", distro, paths) if all(_WSL_TMP.match(p) and ".." not in p for p in paths) else None
        hp = [_host_path(p) for p in paths]
        return ("remove", hp) if all(_owned_temp(p, roots) and not _in_notes_project(p) for p in hp) else None
    return None


def _expand(p: Path) -> list[Path]:
    """Glob-expand a plan path (the model may write ``notes-x/*``); a plain path expands to itself."""
    s = str(p)
    if any(ch in s for ch in "*?["):
        return [Path(x) for x in glob.glob(s)]
    return [p]


def _docker(argv: list[str], distro: str | None, timeout: int = 60) -> tuple[int, str]:
    if distro:
        argv = ["wsl", "-d", distro, "--"] + argv
    return _run(argv, timeout)


def _docker_ids(kind: str, name: str, label: str, distro: str | None) -> list[str]:
    """IDs of the Docker objects that carry exactly this name and the run's label."""
    if kind == "container":
        ls = ["docker", "ps", "-aq", "--filter", f"name=^/?{re.escape(name)}$", "--filter", label]
    elif kind == "image":
        ls = ["docker", "images", "-q", "--filter", f"reference={name}", "--filter", label]
    else:
        ls = ["docker", kind, "ls", "-q", "--filter", f"name=^{re.escape(name)}$", "--filter", label]
    code, out = _docker(ls, distro, 30)
    return sorted(set(out.split())) if code == 0 else []


def prefilter(cleanups: list[str]) -> list[str]:
    """Called at approval time, BEFORE the command runs: keep only plans that remove something which does not
    exist yet (the command will create it). A path or Docker object that already exists predates the command
    and is never the command's leftover, so its removal is dropped here and can never run later."""
    keep = []
    docker_up = None
    for cmd in cleanups or []:
        how = plan(cmd, _roots(None))
        if how is None:
            continue
        if how[0] == "remove" and any(x.exists() or x.is_symlink() for p in how[1] for x in _expand(p) + [p]):
            continue
        if how[0] == "docker":
            if docker_up is None:
                docker_up = ec.docker_running()
            if docker_up and how[3] is None and any(
                    _run(["docker", "inspect", "--type", how[1], t], 20)[0] == 0 for t in how[2]):
                continue
        keep.append(cmd)
    return keep


def safe_cleanup(cmd: str, roots: list[Path]) -> bool:
    """True when ``cmd`` is a plain removal of something a notes run may have created."""
    return plan(cmd, roots) is not None


def _run(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, type(e).__name__


def _created_after(p: Path, since: float) -> bool:
    """Whether ``p`` came into being at or after ``since`` (creation time where the OS has it, else mtime).

    ``since`` is the start of the run, not the approval of one command: NTFS gives a directory re-created
    under a name deleted within the last 15 s its *old* timestamps ("tunneling"), so a name a run re-uses
    (snippet scratch dirs) would look older than its latest approval. The first creation in a run is still
    inside the run."""
    try:
        st = p.lstat()
    except OSError:
        return False
    born = getattr(st, "st_birthtime", None) or (st.st_ctime if os.name == "nt" else st.st_mtime)
    return max(born, st.st_mtime) >= math.floor(since) - 1    # a second of slack for 1 s filesystems


def _rmtree(p: Path) -> None:
    def onexc(fn, path, exc):                                 # read-only files, e.g. a clone's .git/objects
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            fn(path)
        except OSError:
            raise exc
    shutil.rmtree(p, onexc=onexc)


def _remove(paths: list[Path], roots: list[Path], since: float) -> tuple[str, str]:
    """Remove the plan's paths. Returns ``(status, detail)`` with status ``ok``, ``nothing`` or ``failed``."""
    touched, skipped = 0, []
    for p in paths:
        for x in _expand(p):
            if not (_owned_temp(x, roots) and not _in_notes_project(x)):
                skipped.append(f"{x}: outside the allowed temp roots")
                continue
            if not (x.exists() or x.is_symlink()):
                continue
            if not _created_after(x, since):
                skipped.append(f"{x}: existed before this run started")
                continue
            try:
                if x.is_dir() and not x.is_symlink():
                    _rmtree(x)
                else:
                    x.unlink()
                touched += 1
            except OSError as e:
                return "failed", str(e)[:200]
    if skipped:
        return ("ok" if touched else "nothing"), "; ".join(skipped)[:300]
    return ("ok" if touched else "nothing"), ""


def _docker_remove(kind: str, names: list[str], run_id: str, distro: str | None) -> tuple[str, str]:
    label = f"label=lecture-notes.run={run_id}"
    ids: list[str] = []
    for n in names:
        ids += _docker_ids(kind, n, label, distro)
    if not ids:
        return "nothing", "no object with that name carries this run's label"
    rm = {"container": ["docker", "rm", "-f"], "image": ["docker", "rmi", "-f"], "volume": ["docker", "volume", "rm", "-f"],
          "network": ["docker", "network", "rm"]}[kind]
    code, out = _docker(rm + sorted(set(ids)), distro, 120)
    return ("ok" if code == 0 else "failed"), out


def _docker_sweep(run_id: str, agent_id: str | None = None) -> list[str]:
    """Remove every Docker object labelled for this agent (SubagentStop) or for the whole run (guard clear)."""
    done = []
    label = f"label=lecture-notes.agent={agent_id}" if agent_id else f"label=lecture-notes.run={run_id}"
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


def _wsl_of(command: str) -> tuple[bool, str | None]:
    """``(ran inside WSL, distribution)`` for the approved command (any segment, Bash or PowerShell form)."""
    for simple in ep._SPLIT.split(ep.strip_heredocs(command or "")):
        words = ep._unwrap(ep._words(simple.strip())) or []
        if words and ep._norm(words[0]) == "wsl":
            distro, inner = ep.wsl_parts(words[1:])
            if inner:
                return True, distro
    return False, None


def _rows(path: Path) -> list[dict]:
    """Ledger rows; a truncated or odd line is skipped, never fatal."""
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def cleanup(run_id: str, agent_id: str | None = None, project: str | None = None) -> list[str]:
    """Carry out pending cleanup plans for ``run_id`` (only ``agent_id``'s when given). Returns what was done.
    A plan runs once per ledger row; a failed removal is retried on the next call."""
    path = ec.ledger_path(run_id)
    rows = _rows(path)
    legacy_done = {r["cleanup_cmd"] for r in rows if "cleanup_cmd" in r}          # pre-1.2.3 ledgers
    done = {(d.get("row"), d.get("cmd")) for r in rows for d in [r.get("cleanup_done")]
            if isinstance(d, dict) and d.get("status") in ("ok", "nothing", "skipped")}
    roots = _roots(project)
    approvals = [float(r["t"]) for r in rows if "t" in r and "cleanup_cmd" not in r and "cleanup_done" not in r]
    run_start = min(approvals) if approvals else 0.0           # nothing older than the run is ever a leftover
    report, log, used_docker = [], [], False
    for r in rows:
        if "cleanup_cmd" in r or "cleanup_done" in r or (agent_id and r.get("agent") != agent_id):
            continue
        used_docker |= "docker" in (r.get("command") or "")
        in_wsl, distro = _wsl_of(r.get("command") or "")
        approved = float(r.get("t") or 0)
        for cmd in r.get("cleanup") or []:
            if cmd in legacy_done or (approved, cmd) in done:
                continue
            done.add((approved, cmd))
            how = plan(cmd, roots, via_wsl=in_wsl, distro=distro)
            if how is None:
                status, out = "skipped", "not a plain removal of this run's own notes-* objects"
            elif how[0] == "remove":
                status, out = _remove(how[1], roots, run_start)
            elif how[0] == "wsl_rm":
                code, out = _run(["wsl"] + (["-d", how[1]] if how[1] else []) + ["--", "rm", "-rf", *how[2]])
                status = "ok" if code == 0 else "failed"
            else:
                status, out = _docker_remove(how[1], how[2], run_id, how[3])
            report.append(f"{status}: {cmd}" + (f"  ({out})" if out and status != "ok" else ""))
            log.append({"cleanup_done": {"row": approved, "cmd": cmd, "status": status, "out": out, "t": time.time()}})
    if used_docker and ec.docker_running():
        report += _docker_sweep(run_id, agent_id)
    if log:
        try:
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.writelines(json.dumps(x, ensure_ascii=False) + "\n" for x in log)
        except OSError:
            pass
    return report
