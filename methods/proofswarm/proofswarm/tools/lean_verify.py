"""Exact-source local Lean 4 verification.

No HTTP, Cloud Run, credentials, or fallback path exists here.  The exact UTF-8
source whose digest is returned is the source passed to ``lake env lean``.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from proofswarm.config import LEAN_PROJECT_ROOT
from proofswarm.local_runtime import (
    LocalPolicyError,
    no_window_below_normal_flags,
    require_local_compute,
)
from proofswarm.schemas import LeanVerifyResult


MAX_LEAN_SOURCE_BYTES = 1024 * 1024
MAX_LEAN_IMPORTS = 64
MAX_LEAN_OUTPUT_BYTES = 8 * 1024 * 1024
COMPUTE_RECHECK_SECONDS = 1.0
_IMPORT_RE = re.compile(r"[A-Za-z0-9_.]{1,256}\Z")
_FORBIDDEN_TRUST_RE = re.compile(
    r"(?im)^\s*(?:#|axiom\b|axioms\b|constant\b|constants\b|unsafe\b|"
    r"initialize\b|builtin_initialize\b|foreign\b|extern\b|elab\b|"
    r"elab_rules\b|macro\b|macro_rules\b|syntax\b|run_cmd\b)|"
    r"\b(?:sorry|admit|native_decide|unsafeCast|ofReduceBool|run_tac)\b"
)
_ERROR_RE = re.compile(r"^.*?:(?:\d+:\d+:\s*)?error:\s*(.+)$", re.MULTILINE)


def compose_lean_source(
    statement: str,
    proof: str = "",
    *,
    imports: list[str] | None = None,
) -> str:
    """Compose one unambiguous source file without discarding either argument."""

    if not isinstance(statement, str) or not isinstance(proof, str):
        raise ValueError("Lean statement and proof must be text")
    requested_imports = ["Mathlib"] if imports is None else list(imports)
    if len(requested_imports) > MAX_LEAN_IMPORTS:
        raise ValueError("too many Lean imports")
    if any(
        not isinstance(item, str) or not _IMPORT_RE.fullmatch(item)
        for item in requested_imports
    ):
        raise ValueError("Lean imports must be dotted module identifiers")
    statement_text = statement.strip()
    proof_text = proof.strip()
    if not statement_text and not proof_text:
        raise ValueError("Lean source is empty")

    if proof_text:
        if proof_text.startswith("by ") or proof_text.startswith(":= by"):
            if not statement_text:
                raise ValueError("a tactic proof requires a submitted statement")
            placeholder = re.compile(r"(?s)(?::=\s*)?by\s+(?:sorry|admit)\s*\Z")
            if placeholder.search(statement_text) is None:
                raise ValueError("tactic proof requires a terminal by-sorry placeholder")
            replacement = proof_text if proof_text.startswith(":=") else f":= {proof_text}"
            body = placeholder.sub(replacement, statement_text)
        elif statement_text:
            raise ValueError(
                "a complete proof source cannot be paired with a second statement; "
                "submit the exact complete source as statement"
            )
        else:
            body = proof_text
    else:
        body = statement_text

    existing = set(re.findall(r"(?m)^\s*import\s+([A-Za-z0-9_.]+)\s*$", body))
    prefix = "\n".join(f"import {item}" for item in requested_imports if item not in existing)
    return ((prefix + "\n\n") if prefix else "") + body + "\n"


def _failure(error: str, *, started: float, source_sha256: str | None = None) -> LeanVerifyResult:
    return LeanVerifyResult(
        ok=False,
        elaborated=False,
        sorry_count=-1,
        errors=[error],
        duration_seconds=max(0.0, time.time() - started),
        source_sha256=source_sha256,
    )


def _project_is_provisioned(root: Path) -> bool:
    """Require an already-fetched local mathlib project; never download in a run."""

    return (
        (root / "lakefile.lean").is_file()
        and (root / "lean-toolchain").is_file()
        and (root / ".lake" / "packages" / "mathlib").is_dir()
    )


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort whole-tree termination without opening a visible console."""

    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
                creationflags=no_window_below_normal_flags(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _run_guarded_lean(
    command: list[str], *, cwd: Path, timeout_seconds: int
) -> tuple[int, str, str | None]:
    """Run Lean with bounded output and live owner/compute yielding."""

    require_local_compute()
    creationflags = no_window_below_normal_flags()
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        creationflags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
        creationflags=creationflags,
        **popen_kwargs,
    )
    if proc.stdout is None:  # defensive; PIPE above should always provide it
        _terminate_process_tree(proc)
        raise OSError("Lean output pipe was not created")

    captured = bytearray()
    overflow = threading.Event()

    def drain_output() -> None:
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                room = MAX_LEAN_OUTPUT_BYTES + 1 - len(captured)
                if room > 0:
                    captured.extend(chunk[:room])
                if len(captured) > MAX_LEAN_OUTPUT_BYTES:
                    overflow.set()
        except (OSError, ValueError):
            return

    reader = threading.Thread(target=drain_output, name="proofswarm-lean-output", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout_seconds
    next_compute_check = time.monotonic() + COMPUTE_RECHECK_SECONDS
    stop_reason: str | None = None
    try:
        while proc.poll() is None:
            now = time.monotonic()
            if overflow.is_set():
                stop_reason = "local Lean output exceeded the fixed 8 MiB bound"
                break
            if now >= deadline:
                stop_reason = f"local Lean timeout after {timeout_seconds}s"
                break
            if now >= next_compute_check:
                try:
                    require_local_compute()
                except LocalPolicyError as exc:
                    stop_reason = f"local Lean yielded to compute policy: {exc}"
                    break
                next_compute_check = now + COMPUTE_RECHECK_SECONDS
            time.sleep(0.05)
        if stop_reason is not None:
            _terminate_process_tree(proc)
        else:
            proc.wait(timeout=5)
        reader.join(timeout=5)
        if reader.is_alive():
            _terminate_process_tree(proc)
            stop_reason = stop_reason or "local Lean output pipe did not close"
            proc.stdout.close()
            reader.join(timeout=1)
        if overflow.is_set():
            stop_reason = stop_reason or "local Lean output exceeded the fixed 8 MiB bound"
        output = bytes(captured[:MAX_LEAN_OUTPUT_BYTES]).decode("utf-8", errors="replace")
        return int(proc.returncode if proc.returncode is not None else -1), output, stop_reason
    finally:
        if proc.poll() is None:
            _terminate_process_tree(proc)
        proc.stdout.close()


def lean_verify(
    statement: str,
    proof: str,
    imports: list[str] | None = None,
    timeout: int = 180,
    max_retries: int = 0,
) -> LeanVerifyResult:
    """Compile one exact source locally; retries are deliberately unsupported."""

    del max_retries
    started = time.time()
    try:
        source = compose_lean_source(statement, proof, imports=imports)
    except ValueError as exc:
        return _failure(str(exc), started=started)
    raw = source.encode("utf-8")
    source_sha256 = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_LEAN_SOURCE_BYTES:
        return _failure(
            "Lean source exceeds the fixed 1 MiB verification bound",
            started=started,
            source_sha256=source_sha256,
        )
    if _FORBIDDEN_TRUST_RE.search(source):
        return _failure(
            "submission contains a forbidden trust or metaprogramming escape",
            started=started,
            source_sha256=source_sha256,
        )
    try:
        require_local_compute()
    except LocalPolicyError as exc:
        return _failure(str(exc), started=started, source_sha256=source_sha256)

    root = LEAN_PROJECT_ROOT.resolve()
    if not _project_is_provisioned(root):
        return _failure(
            "local Lean/mathlib project is not provisioned; no download attempted",
            started=started,
            source_sha256=source_sha256,
        )
    lake = shutil.which("lake")
    if not lake:
        return _failure("local lake executable not found", started=started, source_sha256=source_sha256)

    timeout_seconds = max(1, min(int(timeout), 600))
    with NamedTemporaryFile(
        mode="wb", prefix=".proofswarm-", suffix=".lean", dir=root, delete=False
    ) as handle:
        source_path = Path(handle.name)
        handle.write(raw)
        handle.flush()
    try:
        try:
            returncode, combined, run_error = _run_guarded_lean(
                [lake, "env", "lean", str(source_path)],
                cwd=root,
                timeout_seconds=timeout_seconds,
            )
        except LocalPolicyError as exc:
            return _failure(str(exc), started=started, source_sha256=source_sha256)
        except OSError as exc:
            return _failure(
                f"local Lean launch failed: {type(exc).__name__}",
                started=started,
                source_sha256=source_sha256,
            )
    finally:
        source_path.unlink(missing_ok=True)

    if run_error is not None:
        return _failure(run_error, started=started, source_sha256=source_sha256)
    errors = [match.group(1).strip() for match in _ERROR_RE.finditer(combined)]
    for line in combined.splitlines():
        if "unsolved goals" in line.lower():
            errors.append(line.strip())
    sorry_count = max(
        len(re.findall(r"\b(?:sorry|admit)\b", source)),
        len(re.findall(r"declaration uses ['\"]sorry['\"]", combined)),
    )
    elaborated = returncode == 0 and not errors
    return LeanVerifyResult(
        ok=elaborated and sorry_count == 0,
        elaborated=elaborated,
        sorry_count=sorry_count,
        errors=errors[:20],
        duration_seconds=time.time() - started,
        source_sha256=source_sha256,
    )


def hash_lean_artifact(statement: str, proof: str) -> str:
    source = compose_lean_source(statement, proof)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
