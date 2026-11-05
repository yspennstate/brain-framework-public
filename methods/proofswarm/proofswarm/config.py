"""Local-only ProofSwarm configuration.

The former GCP proof of concept used one backend variable for both inference and
ticket storage.  That selector collision could route ``firestore`` runs into
Vertex.  Current policy separates the two selectors and accepts only ``local``.
"""
from __future__ import annotations

import os
from pathlib import Path

from proofswarm.local_runtime import LocalPolicyError, validate_loopback_base_url

# Derived, so this tree carries no single machine's username.
_HOME = os.path.expanduser("~")


def _local_only(name: str, value: str) -> str:
    normalized = value.strip().lower()
    if normalized != "local":
        raise LocalPolicyError(
            f"{name}={value!r} is denied: ProofSwarm is local-only and has no cloud fallback"
        )
    return normalized


def _local_absolute_path(name: str, value: str) -> Path:
    """Accept only absolute paths on a non-network filesystem."""

    text = value.strip()
    if not text or "\0" in text:
        raise LocalPolicyError(f"{name} must be a nonempty local absolute path")
    if text.startswith(("\\\\", "//")):
        raise LocalPolicyError(f"{name} cannot use a UNC or network path")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise LocalPolicyError(f"{name} must be an absolute local path")
    resolved = path.resolve(strict=False)
    if os.name == "nt":
        import ctypes

        drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(str(resolved.anchor)))  # type: ignore[attr-defined]
        if drive_type == 4:  # DRIVE_REMOTE
            raise LocalPolicyError(f"{name} cannot use a mapped network drive")
    return resolved


# Reject the historical shared selector before either inference or storage is used.
_legacy_backend = os.getenv("PROOFSWARM_BACKEND")
if _legacy_backend is not None:
    _local_only("PROOFSWARM_BACKEND (legacy)", _legacy_backend)

INFERENCE_BACKEND = _local_only(
    "PROOFSWARM_INFERENCE_BACKEND",
    os.getenv("PROOFSWARM_INFERENCE_BACKEND", "local"),
)
STORAGE_BACKEND = _local_only(
    "PROOFSWARM_STORAGE_BACKEND",
    os.getenv("PROOFSWARM_STORAGE_BACKEND", "local"),
)

# Compatibility for old imports.  It can only ever be ``local``.
BACKEND = INFERENCE_BACKEND
LOCAL_BASE_URL = validate_loopback_base_url(
    os.getenv("PROOFSWARM_LOCAL_URL", "http://127.0.0.1:11434/v1")
)
LOCAL_MODEL = os.getenv("PROOFSWARM_LOCAL_MODEL", "qwen2.5-coder").strip()
if not LOCAL_MODEL or len(LOCAL_MODEL) > 256 or any(ord(char) < 32 for char in LOCAL_MODEL):
    raise LocalPolicyError("PROOFSWARM_LOCAL_MODEL must be 1-256 printable characters")

# All roles currently share one locally installed model.  A later local router
# may map roles to multiple local models without changing the cloud-denial gate.
AGENT_MODELS = {
    "navigator": LOCAL_MODEL,
    "formalizer": LOCAL_MODEL,
    "prover": LOCAL_MODEL,
    "counterexample": LOCAL_MODEL,
    "referee": LOCAL_MODEL,
}

PER_PROBLEM_BUDGET_USD = 0.0
PER_AGENT_TURN_BUDGET_USD = 0.0
LOCAL_TICKET_DIR = _local_absolute_path(
    "PROOFSWARM_LOCAL_TICKETS",
    os.getenv("PROOFSWARM_LOCAL_TICKETS",
              os.path.join(_HOME, "proofswarm", "traces", "tickets"))
)
TRACE_DIR = _local_absolute_path(
    "PROOFSWARM_TRACE_DIR",
    os.getenv("PROOFSWARM_TRACE_DIR", os.path.join(_HOME, "proofswarm", "traces"))
)
CALIBRATION_DIR = _local_absolute_path(
    "PROOFSWARM_CALIBRATION_DIR",
    os.getenv("PROOFSWARM_CALIBRATION_DIR", str(TRACE_DIR / "calibration"))
)
LEAN_PROJECT_ROOT = _local_absolute_path(
    "PROOFSWARM_LEAN_PROJECT",
    os.getenv("PROOFSWARM_LEAN_PROJECT", os.path.join(_HOME, "proofswarm", "lean_worker"))
)
