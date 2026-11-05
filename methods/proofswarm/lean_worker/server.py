"""FastAPI shim that wraps the Lean 4 toolchain for the ProofSwarm verifier.

POST /verify with {statement, proof, imports, timeout_seconds} → returns LeanVerifyResult.

Strategy: write the exact submitted Lean source to a unique temporary file, run
`lake env lean` on it, and parse the kernel output. We trust ONLY:
  - exit code 0
  - sorry count == 0 (parsed from `lake env lean --print-sorry-count` if available, else regex)
  - no fatal `error:` lines in stderr.
"""
from __future__ import annotations
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import FastAPI
from pydantic import BaseModel, Field

try:
    from source_policy import (
        MAX_LEAN_IMPORTS,
        MAX_LEAN_SOURCE_BYTES,
        compose_source,
        forbidden_source_reason,
    )
except ModuleNotFoundError:  # source-tree import during local tests
    from lean_worker.source_policy import (
        MAX_LEAN_IMPORTS,
        MAX_LEAN_SOURCE_BYTES,
        compose_source,
        forbidden_source_reason,
    )


LEAN_PROJECT_ROOT = Path("/lean")
SUBMISSION_DIR = LEAN_PROJECT_ROOT / "ProofSwarm"


class LeanVerifyRequest(BaseModel):
    statement: str = Field(max_length=MAX_LEAN_SOURCE_BYTES)
    proof: str = Field(max_length=MAX_LEAN_SOURCE_BYTES)
    imports: list[str] = Field(default_factory=lambda: ["Mathlib"], max_length=MAX_LEAN_IMPORTS)
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class LeanVerifyResult(BaseModel):
    ok: bool
    elaborated: bool
    sorry_count: int
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float
    olean_sha256: str | None = None
    source_sha256: str | None = None


app = FastAPI(title="proofswarm-lean", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "lean": _lean_version()}


@app.post("/verify", response_model=LeanVerifyResult)
def verify(req: LeanVerifyRequest) -> LeanVerifyResult:
    t0 = time.time()
    try:
        src = _compose_source(req)
    except ValueError as exc:
        return LeanVerifyResult(
            ok=False, elaborated=False, sorry_count=-1,
            errors=[str(exc)], duration_seconds=time.time() - t0,
        )
    raw = src.encode("utf-8")
    source_sha = hashlib.sha256(raw).hexdigest()
    policy_error = forbidden_source_reason(src)
    if policy_error is not None:
        return LeanVerifyResult(
            ok=False, elaborated=False, sorry_count=_count_sorries(src, ""),
            errors=[policy_error],
            duration_seconds=time.time() - t0, source_sha256=source_sha,
        )
    if not _project_is_provisioned(LEAN_PROJECT_ROOT):
        return LeanVerifyResult(
            ok=False, elaborated=False, sorry_count=-1,
            errors=["local Lean/mathlib project is not provisioned; no download attempted"],
            duration_seconds=time.time() - t0, source_sha256=source_sha,
        )
    SUBMISSION_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="wb", prefix="Submission-", suffix=".lean", dir=SUBMISSION_DIR, delete=False
    ) as handle:
        submission_path = Path(handle.name)
        handle.write(raw)
    try:
        try:
            proc = subprocess.run(
                ["lake", "env", "lean", str(submission_path)],
                cwd=str(LEAN_PROJECT_ROOT),
                capture_output=True, text=True, shell=False,
                timeout=max(1, min(req.timeout_seconds, 600)),
                creationflags=_subprocess_flags(),
            )
        except subprocess.TimeoutExpired:
            return LeanVerifyResult(
                ok=False, elaborated=False, sorry_count=-1,
                errors=[f"timeout after {req.timeout_seconds}s"],
                duration_seconds=time.time() - t0, source_sha256=source_sha,
            )
        except OSError as exc:
            return LeanVerifyResult(
                ok=False, elaborated=False, sorry_count=-1,
                errors=[f"local Lean launch failed: {type(exc).__name__}"],
                duration_seconds=time.time() - t0, source_sha256=source_sha,
            )
    finally:
        submission_path.unlink(missing_ok=True)
    duration = time.time() - t0
    stderr = proc.stderr or ""
    stdout = proc.stdout or ""
    combined = stdout + "\n" + stderr
    errors = _extract_errors(combined)
    sorry_count = _count_sorries(src, combined)
    elaborated = proc.returncode == 0 and not errors
    return LeanVerifyResult(
        ok=elaborated and sorry_count == 0,
        elaborated=elaborated,
        sorry_count=sorry_count,
        errors=errors[:20],
        duration_seconds=duration,
        source_sha256=source_sha,
    )


def _compose_source(req: LeanVerifyRequest) -> str:
    return compose_source(req.statement, req.proof, req.imports)


_ERROR_RE = re.compile(r"^.*?:(?:\d+:\d+:\s*)?error:\s*(.+)$", re.MULTILINE)


def _extract_errors(out: str) -> list[str]:
    errs = [m.group(1).strip() for m in _ERROR_RE.finditer(out)]
    # Also surface any `unsolved goals` lines.
    for line in out.splitlines():
        if "unsolved goals" in line.lower():
            errs.append(line.strip())
    return errs


def _count_sorries(src: str, out: str) -> int:
    # Static count from source — `sorry`s the user wrote.
    src_count = len(re.findall(r"\b(?:sorry|admit)\b", src))
    # Dynamic count from Lean's "declaration uses 'sorry'" warnings.
    dyn = len(re.findall(r"declaration uses 'sorry'", out))
    return max(src_count, dyn)


def _project_is_provisioned(root: Path) -> bool:
    return (
        (root / "lakefile.lean").is_file()
        and (root / "lean-toolchain").is_file()
        and (root / ".lake" / "packages" / "mathlib").is_dir()
    )


def _subprocess_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
        getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    )


def _lean_version() -> str:
    try:
        return subprocess.check_output(
            ["lean", "--version"], text=True, timeout=5,
            creationflags=_subprocess_flags(),
        ).strip()
    except Exception as e:
        return f"unknown ({e})"
