"""Cross-process file lock (PLAN 16.2). Stdlib only, OS-agnostic.

An atomic lockfile (``O_CREAT|O_EXCL``) holding ``{pid, host, token, created}``. A waiter that finds a lock
whose owner PID is dead (same host), or whose age exceeds ``stale_after``, takes it over: the stale file is
first *renamed* to a unique name (only one waiter can win that rename), then the lock is retried. Used for the
GPU video pipeline (``<data>/locks/gpu.lock``) and the ledger (``<project>/.notes-plugin-internal/state.lock``).

    with FileLock(path, timeout=None, stale_after=None):   # blocks until acquired
        ...
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path


class LockTimeout(TimeoutError):
    pass


def pid_alive(pid: int) -> bool:
    """True if a process with this PID exists (never signals it)."""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes
        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return k32.GetLastError() == 5                # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == 259                  # STILL_ACTIVE
            return True
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_owner(path: Path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


class FileLock:
    def __init__(self, path, timeout: float | None = None, stale_after: float | None = None,
                 poll: float = 0.1, on_wait=None):
        self.path = Path(path)
        self.timeout, self.stale_after, self.poll, self.on_wait = timeout, stale_after, poll, on_wait
        self.token = uuid.uuid4().hex
        self.held = False
        self.waited = 0.0

    # ---- staleness ----
    def _stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False                                   # vanished: just retry
        owner = read_owner(self.path)
        if owner is None:
            return age > 2.0                               # half-written lockfile: give the writer a moment
        if owner.get("host") == socket.gethostname() and isinstance(owner.get("pid"), int):
            if not pid_alive(owner["pid"]):
                return True
        return self.stale_after is not None and age > self.stale_after

    def _break(self) -> None:
        tomb = self.path.with_name(f"{self.path.name}.stale.{uuid.uuid4().hex[:8]}")
        try:
            os.replace(self.path, tomb)                    # atomic: exactly one waiter wins
        except OSError:
            return
        try:
            tomb.unlink()
        except OSError:
            pass

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "token": self.token,
                       "created": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh)
        self.held = True
        return True

    def acquire(self) -> "FileLock":
        t0 = time.time()
        announced = False
        while True:
            if self.try_acquire():
                self.waited = time.time() - t0
                return self
            if self._stale():
                self._break()
                continue
            if not announced and self.on_wait:
                self.on_wait(read_owner(self.path))
                announced = True
            if self.timeout is not None and time.time() - t0 > self.timeout:
                raise LockTimeout(f"could not take {self.path} within {self.timeout}s "
                                  f"(held by {read_owner(self.path)})")
            time.sleep(self.poll)

    def release(self) -> None:
        """Remove our lockfile. On Windows a waiter that is reading it makes the unlink fail briefly: retry."""
        if not self.held:
            return
        self.held = False
        for _ in range(100):
            owner = read_owner(self.path)
            if owner is not None and owner.get("token") != self.token:
                return                                     # somebody else's lock now (ours was taken over)
            try:
                self.path.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.05)

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
