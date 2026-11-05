#!/usr/bin/env python3
"""Small cross-process append primitive for the brain's hook telemetry.

Many Claude and Codex hooks run at the same time.  ``open(..., "a")`` keeps
each process at the end of a file, but it does not make a Python text write a
cross-process record transaction on Windows.  The live ``gf_shadow_fires.tsv``
proved the difference: concurrent writers left truncated one-column fragments.

``append_text`` serializes cooperating writers by canonical target path.  On
Windows it uses a named kernel mutex (released automatically if a writer dies);
on POSIX it uses ``flock`` on a stable sidecar.  The payload is encoded once
and written while the lock is held.  Contention may drop telemetry after a
short timeout, but it can never delay or break the hook that called it.
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import os
import time

DEFAULT_TIMEOUT = 0.25
DEFAULT_MAX_BYTES = 1024 * 1024


class JournalLockUnavailable(TimeoutError):
    """The stable lock for one append journal could not be acquired."""


def _key(path: str) -> str:
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        n = os.write(fd, view[written:])
        if n <= 0:
            raise OSError("journal append made no progress")
        written += n


def _open_append(path: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
    return os.open(path, flags, 0o600)


@contextlib.contextmanager
def exclusive_append_lock(path: str, timeout: float = DEFAULT_TIMEOUT):
    """Hold the same stable lock used by every cooperating journal writer.

    A stable named mutex on Windows and a sidecar lock on POSIX continue to
    identify the journal after an atomic replacement.  Locking the POSIX data
    inode itself is insufficient: a waiting writer can otherwise append to the
    unlinked pre-replacement inode and silently lose its row.
    """
    if not isinstance(path, str) or not path:
        raise ValueError("journal path must be a non-empty string")
    wait_seconds = max(0.0, float(timeout))
    if os.name != "nt":
        import fcntl

        canonical = os.path.realpath(os.path.abspath(path))
        lock_path = canonical + ".append.lock"
        parent = os.path.dirname(lock_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        acquired = False
        deadline = time.monotonic() + wait_seconds
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        raise JournalLockUnavailable(lock_path)
                    time.sleep(0.005)
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
    kernel32.ReleaseMutex.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_bool

    handle = kernel32.CreateMutexW(None, False, "Local\\BrainAppend_" + _key(path))
    if not handle:
        raise OSError("could not create append mutex")
    acquired = False
    try:
        wait_ms = max(0, min(60_000, int(wait_seconds * 1000)))
        result = kernel32.WaitForSingleObject(handle, wait_ms)
        acquired = result in (0x00000000, 0x00000080)  # object or abandoned owner
        if not acquired:
            raise JournalLockUnavailable(path)
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


def _append_locked(path: str, payload: bytes) -> None:
    fd = _open_append(path)
    try:
        _write_all(fd, payload)
    finally:
        os.close(fd)


def append_text(path: str, text: str, timeout: float = DEFAULT_TIMEOUT,
                max_bytes: int = DEFAULT_MAX_BYTES) -> bool:
    """Append ``text`` as one cooperating-writer transaction.

    Returns ``False`` on contention, oversize input, or any I/O error.  This is
    deliberate: telemetry is secondary to the hook's real job.  Callers remain
    responsible for TSV/JSONL escaping and for including a final newline.
    """
    try:
        if not isinstance(path, str) or not path or not isinstance(text, str):
            return False
        payload = text.encode("utf-8")
        if len(payload) > max(0, int(max_bytes)):
            return False
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with exclusive_append_lock(path, timeout=timeout):
            _append_locked(path, payload)
        return True
    except Exception:
        return False
