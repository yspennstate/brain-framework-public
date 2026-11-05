#!/usr/bin/env python3
"""Build a fresh-history, text-only, secret-redacted GitHub snapshot.

Raw transcripts and binaries remain in the private S3 vault. This copy is the
portable operational brain: code, memories, instructions, research text, and the
current uncommitted coordination modules, without inheriting contaminated Git
history.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Callable, Iterable


REMOTE_URL = "https://github.com/<github-user>/ai-brain-resilience-backup.git"
MAX_FILE_BYTES = 30 * 1024 * 1024
INCLUDED_PREFIXES = (
    "01_ai_consciousness/",
    "02_agent_infrastructure/",
    "03_operational_memos_and_scripts/",
    "10_ai_interaction_history/",
    "12_cognitive_architecture/",
    "manifests/",
)
EXCLUDED_PREFIXES = ("11_raw_session_transcripts/",)
EXACT_SENSITIVE_NAMES = {
    ".env", ".npmrc", "auth.json", "credentials", "credentials.json",
    "id_rsa", "id_ed25519", "known_hosts", "<sensitive-export>.csv",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".dpapi")

PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP |ENCRYPTED )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,}")),
    ("ai_api_key", re.compile(r"sk-(?:ant-)?[A-Za-z0-9_\-]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("telegram_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,60}\b")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("hosted_model_key", re.compile(r"\b(?:rpa_|rpk_|hf_|gsk_)[A-Za-z0-9_\-]{16,}\b")),
    ("authorization_bearer", re.compile(r"\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=\-]{12,}", re.I)),
    ("generic_bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{20,}", re.I)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\b")),
    ("long_api_key", re.compile(r"(?<![/\w.])[A-Za-z0-9]{40,}(?![\w.])")),
    ("plain_password", re.compile(
        r"\b(?:password|passwd|pwd)\b(?:\s+(?:is|was|equals))?\s*[:=]?\s+"
        r"(?:['\"][^'\"\r\n]{6,128}['\"]|[A-Za-z0-9!@#$%^&*_\-]{6,64}(?:\s+[A-Za-z0-9!@#$%^&*_\-]{2,32}){0,3})",
        re.I,
    )),
    ("two_factor_code", re.compile(r"\bcode\b(?:\s+(?:is|was))?\s*[:=]?\s*\d{6,8}\b", re.I)),
    ("ssn", re.compile(r"(?:\bssn\b|social security(?:\s+number)?)(?:\s+(?:is|was|#))?\s*[:=]?\s*\d{3}-?\d{2}-?\d{4}\b", re.I)),
    ("secret_assignment", re.compile(
        r"(?:aws[_-]?secret[_-]?access[_-]?key|api[_-]?key|secret|token|passwd|password|"
        r"access[_-]?key|credential|client[_-]?secret)['\"]?\s*[:=]\s*"
        r"(?:['\"][A-Za-z0-9_\-/+=]{16,}['\"]|[A-Za-z0-9_\-/+=]{16,})", re.I)),
]
FAKE = re.compile(
    r"0123456789|abcdefghij|([A-Za-z0-9])\1{5,}|your[_-]?(token|key|secret)|"
    r"placeholder|redact|EXAMPLE|CHANGEME|<[^>]{1,20}>", re.I,
)


class SnapshotError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode != 0:
        raise SnapshotError(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stderr[-3000:]}")
    return result


def _git(cwd: Path, *args: str) -> str:
    return _run(["git", *args], cwd).stdout.strip()


def _git_bytes(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if result.returncode != 0:
        raise SnapshotError(result.stderr.decode("utf-8", "replace")[-3000:])
    return result.stdout


def _status_hash(source: Path) -> tuple[str, int]:
    raw = _git_bytes(source, "status", "--porcelain=v2", "-z", "--untracked-files=all")
    return hashlib.sha256(raw).hexdigest(), raw.count(b"\0")


def source_paths(source: Path) -> list[str]:
    raw = _git_bytes(source, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = sorted({item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item})
    return paths


def _fingerprint_record(digest: object, relative: str, classification: str,
                        size: int | None = None, content_sha256: str | None = None) -> None:
    record = json.dumps(
        [relative, classification, size, content_sha256],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    digest.update(record + b"\n")


def source_content_fingerprint(source: Path) -> str:
    """Hash export-relevant paths, classifications, and source bytes."""

    digest = hashlib.sha256()
    for relative in source_paths(source):
        normalized = relative.replace("\\", "/")
        if normalized.startswith(EXCLUDED_PREFIXES):
            _fingerprint_record(digest, normalized, "raw_transcript")
            continue
        if not operational_brain_path(normalized):
            _fingerprint_record(digest, normalized, "outside_operational_brain")
            continue
        if sensitive_path(normalized):
            _fingerprint_record(digest, normalized, "sensitive_path")
            continue
        src = source / Path(relative)
        try:
            if src.is_symlink() or not src.is_file():
                _fingerprint_record(digest, normalized, "missing_or_changed")
                continue
            size = src.stat().st_size
            if size > MAX_FILE_BYTES:
                _fingerprint_record(digest, normalized, "oversize", size)
                continue
            raw = src.read_bytes()
        except (FileNotFoundError, OSError):
            _fingerprint_record(digest, normalized, "missing_or_changed")
            continue
        raw_hash = hashlib.sha256(raw).hexdigest()
        if b"\0" in raw:
            _fingerprint_record(digest, normalized, "binary", len(raw), raw_hash)
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            _fingerprint_record(digest, normalized, "binary", len(raw), raw_hash)
            continue
        _fingerprint_record(digest, normalized, "included", len(raw), raw_hash)
    return digest.hexdigest()


def sensitive_path(relative: str) -> bool:
    posix = PurePosixPath(relative.replace("\\", "/"))
    low_parts = [part.lower() for part in posix.parts]
    name = low_parts[-1] if low_parts else ""
    if name in EXACT_SENSITIVE_NAMES or name.endswith(SENSITIVE_SUFFIXES):
        return True
    if name.endswith(".env") or ("password" in name and name.endswith((".txt", ".csv", ".json"))):
        return True
    if ".aws" in low_parts or ".ssh" in low_parts:
        return True
    return False


def operational_brain_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    return "/" not in normalized or normalized.startswith(INCLUDED_PREFIXES)


def redact(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for label, pattern in PATTERNS:
        def replacement(match: re.Match[str]) -> str:
            token = match.group(0)
            if label == "long_api_key" and not (any(ch.islower() for ch in token) and any(ch.isupper() for ch in token) and any(ch.isdigit() for ch in token)):
                return token
            if FAKE.search(token):
                return token
            counts[label] = counts.get(label, 0) + 1
            return f"<<REDACTED:{label}>>"

        text = pattern.sub(replacement, text)
    return text, counts


def _atomic_text(path: Path, text: str) -> None:
    encoded = text.encode("utf-8")
    if path.is_file():
        try:
            if path.read_bytes() == encoded:
                return
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with tmp.open("wb") as handle:
        handle.write(encoded)
    os.replace(tmp, path)


def export(source: Path, destination: Path, state_manifest: Path) -> dict[str, object]:
    source = source.resolve()
    destination = destination.resolve()
    if not (source / ".git").is_dir() or destination == source or source in destination.parents:
        raise SnapshotError("unsafe source/destination relationship")
    destination.mkdir(parents=True, exist_ok=True)
    source_head_before = _git(source, "rev-parse", "HEAD")
    before_hash, before_entries = _status_hash(source)
    captured_content = hashlib.sha256()
    included: list[str] = []
    excluded = {"outside_operational_brain": 0, "raw_transcripts": 0, "sensitive_path": 0, "oversize": 0, "binary": 0, "missing_or_changed": 0}
    redactions: dict[str, int] = {}
    total_bytes = 0

    for relative in source_paths(source):
        normalized = relative.replace("\\", "/")
        if normalized.startswith(EXCLUDED_PREFIXES):
            _fingerprint_record(captured_content, normalized, "raw_transcript")
            excluded["raw_transcripts"] += 1
            continue
        if not operational_brain_path(normalized):
            _fingerprint_record(captured_content, normalized, "outside_operational_brain")
            excluded["outside_operational_brain"] += 1
            continue
        if sensitive_path(normalized):
            _fingerprint_record(captured_content, normalized, "sensitive_path")
            excluded["sensitive_path"] += 1
            continue
        src = source / Path(relative)
        try:
            if src.is_symlink() or not src.is_file():
                _fingerprint_record(captured_content, normalized, "missing_or_changed")
                excluded["missing_or_changed"] += 1
                continue
            size = src.stat().st_size
            if size > MAX_FILE_BYTES:
                _fingerprint_record(captured_content, normalized, "oversize", size)
                excluded["oversize"] += 1
                continue
            raw = src.read_bytes()
        except (FileNotFoundError, OSError):
            _fingerprint_record(captured_content, normalized, "missing_or_changed")
            excluded["missing_or_changed"] += 1
            continue
        raw_hash = hashlib.sha256(raw).hexdigest()
        if b"\0" in raw:
            _fingerprint_record(captured_content, normalized, "binary", len(raw), raw_hash)
            excluded["binary"] += 1
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            _fingerprint_record(captured_content, normalized, "binary", len(raw), raw_hash)
            excluded["binary"] += 1
            continue
        _fingerprint_record(captured_content, normalized, "included", len(raw), raw_hash)
        clean, counts = redact(text)
        for key, count in counts.items():
            redactions[key] = redactions.get(key, 0) + count
        target = destination / Path(relative)
        _atomic_text(target, clean)
        included.append(normalized)
        total_bytes += len(clean.encode("utf-8"))

    old_paths: set[str] = set()
    if state_manifest.is_file():
        try:
            payload = json.loads(state_manifest.read_text(encoding="utf-8"))
            old_paths = set(payload.get("included_paths", []))
        except (OSError, json.JSONDecodeError, TypeError):
            old_paths = set()
    current = set(included)
    for stale in sorted(old_paths - current, reverse=True):
        candidate = (destination / Path(stale)).resolve()
        if destination not in candidate.parents:
            raise SnapshotError(f"unsafe stale path: {stale}")
        if candidate.is_file():
            candidate.unlink()
    for candidate in destination.rglob("*"):
        if not candidate.is_file() or ".git" in candidate.relative_to(destination).parts:
            continue
        relative = candidate.relative_to(destination).as_posix()
        if relative not in current and relative != "SANITIZATION_REPORT.json":
            resolved = candidate.resolve()
            if destination not in resolved.parents:
                raise SnapshotError(f"unsafe destination path: {relative}")
            candidate.unlink()
    for directory, dirs, files in os.walk(destination, topdown=False):
        path = Path(directory)
        if path == destination or ".git" in path.parts:
            continue
        if not dirs and not files:
            with contextlib.suppress(OSError):
                path.rmdir()

    after_hash, after_entries = _status_hash(source)
    source_head_after = _git(source, "rev-parse", "HEAD")
    captured_content_sha256 = captured_content.hexdigest()
    after_content_sha256 = source_content_fingerprint(source)
    changed = bool(
        source_head_before != source_head_after
        or before_hash != after_hash
        or captured_content_sha256 != after_content_sha256
    )
    report: dict[str, object] = {
        "schema_version": 2,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_head": source_head_after,
        "source_head_before": source_head_before,
        "source_status_sha256_before": before_hash,
        "source_status_sha256_after": after_hash,
        "source_content_sha256_captured": captured_content_sha256,
        "source_content_sha256_after": after_content_sha256,
        "source_changed_during_export": changed,
        "source_dirty_entries_before": before_entries,
        "source_dirty_entries_after": after_entries,
        "included_files": len(included),
        "included_bytes": total_bytes,
        "excluded": excluded,
        "redactions": redactions,
        "policy": {
            "included_prefixes": list(INCLUDED_PREFIXES),
            "excluded_prefixes": list(EXCLUDED_PREFIXES),
            "max_file_bytes": MAX_FILE_BYTES,
            "binary_files": "excluded; exact copy remains in private locked S3",
            "ignored_files": "excluded via git ls-files --exclude-standard",
        },
    }
    _atomic_text(destination / "SANITIZATION_REPORT.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    state_manifest.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(state_manifest, json.dumps({"included_paths": included}, ensure_ascii=False, sort_keys=True) + "\n")
    return report


def _remote_tip(repo: Path) -> str | None:
    result = _run(["git", "ls-remote", "--heads", "origin", "refs/heads/main"], repo)
    line = result.stdout.strip()
    return line.split()[0] if line else None


def _scan_staged(repo: Path, scanner: Path) -> None:
    result = _run(["python", str(scanner), "--staged"], repo, check=False)
    if result.returncode != 0:
        raise SnapshotError("independent staged secret gate failed:\n" + (result.stdout + result.stderr)[-5000:])


def _scan_history(repo: Path) -> dict[str, int]:
    """Fail closed if any reachable historical blob matches the current detectors."""

    objects = _git(repo, "rev-list", "--objects", "--all")
    object_ids = list(dict.fromkeys(line.split(maxsplit=1)[0] for line in objects.splitlines() if line))
    findings: dict[str, int] = {}
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"], cwd=repo,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    stderr = b""
    try:
        for object_id in object_ids:
            process.stdin.write((object_id + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline().decode("ascii", "replace").strip().split()
            if len(header) < 3 or header[1] == "missing":
                raise SnapshotError("could not read sanitized Git history")
            size = int(header[2])
            raw = process.stdout.read(size)
            process.stdout.read(1)
            if header[1] != "blob" or size > MAX_FILE_BYTES or b"\0" in raw:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            _, counts = redact(text)
            for label, count in counts.items():
                findings[label] = findings.get(label, 0) + count
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=30)
        if process.stderr is not None:
            stderr = process.stderr.read()
            process.stderr.close()
        process.stdout.close()
    if process.returncode != 0:
        raise SnapshotError("Git history scan failed: " + stderr.decode("utf-8", "replace")[-1000:])
    if findings:
        summary = ", ".join(f"{key}={value}" for key, value in sorted(findings.items()))
        raise SnapshotError("sanitized Git history fails the current secret policy: " + summary)
    return findings


def _verify_private_remote(repo: Path) -> None:
    authenticated = _run(["git", "ls-remote", "origin"], repo, check=False)
    if authenticated.returncode != 0:
        raise SnapshotError("authenticated Git access cannot see the GitHub backup repository")
    result = _run([
        "curl.exe", "-L", "-sS", "-o", "NUL", "-w", "%{http_code}",
        "https://api.github.com/repos/<github-user>/ai-brain-resilience-backup",
    ], repo, check=False)
    if result.returncode != 0 or result.stdout.strip() != "404":
        raise SnapshotError("GitHub backup repository is not verifiably private")


def _push(repo: Path, sha: str, before_push: Callable[[], None] | None = None) -> None:
    for attempt, delay in enumerate((0, 5, 15), start=1):
        if delay:
            import time
            time.sleep(delay)
        if before_push is not None:
            before_push()
        result = _run(["git", "push", "origin", f"{sha}:refs/heads/main"], repo, check=False)
        if result.returncode == 0:
            return
    raise SnapshotError(f"sanitized GitHub push failed after {attempt} attempts: {result.stderr[-3000:]}")


def update_repository(
    source: Path, destination: Path, state_manifest: Path, scanner: Path,
    before_push: Callable[[], None] | None = None,
) -> tuple[str, dict[str, object]]:
    report = export(source, destination, state_manifest)
    if report.get("source_changed_during_export"):
        raise SnapshotError("source paths or bytes changed during sanitized export; retrying is required")
    git_dir = destination / ".git"
    fresh = not git_dir.is_dir()
    if fresh:
        _run(["git", "init", "-b", "main"], destination)
        _run(["git", "config", "user.name", "<github-user>"], destination)
        _run(["git", "config", "user.email", "<email>"], destination)
        _run(["git", "remote", "add", "origin", REMOTE_URL], destination)
        if _remote_tip(destination) is not None:
            raise SnapshotError("fresh sanitized repository requires an empty GitHub remote")

        roots = sorted({path.name for path in destination.iterdir() if path.name != ".git"})
        root_files = [name for name in roots if (destination / name).is_file()]
        root_dirs = [name for name in roots if (destination / name).is_dir()]
        groups: list[list[str]] = []
        if root_files:
            groups.append(root_files)
        groups.extend([[name] for name in root_dirs])
        for index, group in enumerate(groups, start=1):
            _run(["git", "add", "--", *group], destination)
            quiet = _run(["git", "diff", "--cached", "--quiet"], destination, check=False)
            if quiet.returncode == 0:
                continue
            _scan_staged(destination, scanner)
            label = "root files" if len(group) > 1 else group[0]
            _run(["git", "commit", "-m", f"Seed sanitized brain snapshot: {label}"], destination)
            sha = _git(destination, "rev-parse", "HEAD")
            print(f"sanitized seed {index}/{len(groups)} {label} {sha[:12]}", flush=True)
    else:
        configured = _git(destination, "remote", "get-url", "origin")
        if configured != REMOTE_URL:
            raise SnapshotError(f"unexpected sanitized remote: {configured}")
        _run(["git", "add", "-A"], destination)
        quiet = _run(["git", "diff", "--cached", "--quiet"], destination, check=False)
        if quiet.returncode != 0:
            _scan_staged(destination, scanner)
            source_head = str(report["source_head"])
            _run(["git", "commit", "-m", f"Refresh sanitized brain snapshot from {source_head[:12]}"], destination)
        sha = _git(destination, "rev-parse", "HEAD")
        tip = _remote_tip(destination)
        if tip and tip != sha:
            local_tip = _run(["git", "cat-file", "-e", f"{tip}^{{commit}}"], destination, check=False)
            ancestor = _run(["git", "merge-base", "--is-ancestor", tip, sha], destination, check=False) if local_tip.returncode == 0 else None
            if local_tip.returncode != 0 or ancestor is None or ancestor.returncode != 0:
                raise SnapshotError("sanitized GitHub remote diverged; refusing to force-push")
    _scan_history(destination)
    _verify_private_remote(destination)
    sha = _git(destination, "rev-parse", "HEAD")
    if _remote_tip(destination) != sha:
        _push(destination, sha, before_push)

    sha = _git(destination, "rev-parse", "HEAD")
    if _remote_tip(destination) != sha:
        raise SnapshotError("sanitized GitHub SHA verification failed")
    return sha, report
