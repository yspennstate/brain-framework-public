"""Fail-closed runtime policy for the free local ProofSwarm.

ProofSwarm never starts a model server and never falls back to a remote endpoint.
Every real model or Lean call rechecks the owner's compute governor first.
"""
from __future__ import annotations

import ctypes
import ipaddress
import json
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

# Derived, so this tree carries no single machine's username.
_HOME = os.path.expanduser("~")


COMPUTE_STATE_PATH = Path(_HOME) / ".claude" / "compute" / "compute_state.json"
MODEL_SLOT_PATH = Path(_HOME) / ".claude" / "compute" / "proofswarm-local-model.lock"
MAX_COMPUTE_STATE_AGE_SECONDS = 60.0
_ALLOWED_OPEN_MODES = frozenset({"open", "owner_away_open"})


class LocalPolicyError(RuntimeError):
    """The requested action is outside ProofSwarm's local safety boundary."""


def validate_loopback_base_url(value: str) -> str:
    """Return a normalized local URL or reject it before any network request."""

    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise LocalPolicyError("local model URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise LocalPolicyError("local model URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise LocalPolicyError("local model URL must not contain a query or fragment")
    host = (parsed.hostname or "").casefold()
    is_loopback = host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise LocalPolicyError("ProofSwarm model endpoints must be loopback-only")
    return candidate


def read_compute_state(path: str | Path = COMPUTE_STATE_PATH) -> dict[str, Any]:
    """Read a fresh governor snapshot; missing or stale telemetry fails closed."""

    state_path = Path(path)
    try:
        age = time.time() - state_path.stat().st_mtime
        if age < -5 or age > MAX_COMPUTE_STATE_AGE_SECONDS:
            raise LocalPolicyError("compute governor state is stale")
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except LocalPolicyError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise LocalPolicyError("compute governor state is unavailable or invalid") from exc
    if not isinstance(payload, dict):
        raise LocalPolicyError("compute governor state is not an object")
    stamp = payload.get("ts")
    if not isinstance(stamp, str):
        raise LocalPolicyError("compute governor payload timestamp is missing")
    try:
        payload_time = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise LocalPolicyError("compute governor payload timestamp is invalid") from exc
    current = datetime.now(payload_time.tzinfo) if payload_time.tzinfo else datetime.now()
    payload_age = (current - payload_time).total_seconds()
    if payload_age < -5 or payload_age > MAX_COMPUTE_STATE_AGE_SECONDS:
        raise LocalPolicyError("compute governor payload is stale")
    return payload


def require_local_compute(path: str | Path = COMPUTE_STATE_PATH) -> dict[str, Any]:
    """Allow local proof compute only while the owner is away and mode is open."""

    state = read_compute_state(path)
    if state.get("owner_active") is True or state.get("owner_away") is not True:
        raise LocalPolicyError("owner is active; ProofSwarm yields")
    if state.get("status") == "distress":
        raise LocalPolicyError("compute governor reports distress")
    if state.get("mode") not in _ALLOWED_OPEN_MODES:
        raise LocalPolicyError("compute governor is not in an open mode")
    if int(state.get("commit_avail_mb", 0)) < 8192:
        raise LocalPolicyError("less than 8 GB of commit headroom remains")
    if int(state.get("mem_avail_mb", 0)) < 4096:
        raise LocalPolicyError("less than 4 GB of physical memory remains")
    return state


def no_window_below_normal_flags() -> int:
    """Creation flags for quiet, owner-subordinate Windows console children."""

    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | 0x00004000


@contextmanager
def exclusive_local_model_slot(path: str | Path = MODEL_SLOT_PATH) -> Iterator[None]:
    """Hold one machine-wide nonblocking slot for a local inference request."""

    slot_path = Path(path)
    slot_path.parent.mkdir(parents=True, exist_ok=True)
    handle = slot_path.open("a+b")
    acquired = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise LocalPolicyError(
                "another ProofSwarm process owns the local model slot; run stopped"
            ) from exc
        yield
    finally:
        try:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # Closing the handle releases any surviving OS lock.  Do not let a
            # best-effort explicit unlock hide the original worker failure.
            pass
        finally:
            handle.close()


def set_process_below_normal() -> None:
    """Best-effort demotion of the current ProofSwarm process on Windows."""

    if os.name != "nt":
        return
    try:
        handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        ctypes.windll.kernel32.SetPriorityClass(handle, 0x00004000)  # type: ignore[attr-defined]
    except Exception:
        # The governor independently enforces priority; inability to demote here
        # must not create a second launcher or a visible helper process.
        return
