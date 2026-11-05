#!/usr/bin/env python3
"""Brain wake integrity, incident deduplication, and owner-alert escalation.

The old wake scripts could notice a stale or truncated brain and print the warning only
inside the waking agent's terminal.  This module turns the condition into a lifecycle:

1. inspect every required wake source and every node-document manifest;
2. bind the rendered wake length/hash to a receipt;
3. keep an atomic, deduplicated incident ledger;
4. put an explicit owner-alert obligation in the agent's context; and
5. attempt one rate-limited Windows notification without ever allocating a console.

A notification attempt is recorded as an attempt, never as proven delivery or owner
acknowledgement.  A healthy later check resolves the matching machine condition, but it
does not rewrite incident history.  Stdlib only; all paths and clocks have test seams.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


STATE_SCHEMA = "brain-selfcare-state-v1"
SNAPSHOT_SCHEMA = "brain-wake-health-snapshot-v1"
# A successful child-process exit proves only that Windows accepted the notification attempt.
# It is not delivery or owner acknowledgement, so retry while the incident remains active instead
# of suppressing the desktop lane for most of a working day.
DEFAULT_ALERT_COOLDOWN_SECONDS = 30 * 60
FAILED_ALERT_RETRY_SECONDS = 5 * 60
ALERT_LEASE_SECONDS = 120
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_TEXT_PROBE_BYTES = 8 * 1024 * 1024
MAX_SESSION_RECEIPTS_PER_SCOPE = 128
SESSION_KEY_MARKER = ":session:"
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    summary: str
    detail: str = ""
    subject: str = ""
    scope: str = "wake"

    @property
    def fingerprint(self) -> str:
        # Summary prose may improve without creating a new incident.  Code + subject is the
        # stable machine condition; scope keeps independent producers from resolving each other.
        seed = "%s\x1f%s\x1f%s" % (self.scope, self.code, self.subject)
        return hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:24]


@dataclass
class HealthSnapshot:
    runtime: str
    scope: str
    checked_at: str
    findings: list[Finding] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    rendered_chars: int | None = None
    rendered_sha256: str = ""
    budget_chars: int | None = None
    truncated: bool = False
    # A one-way binding to the host session proves that a healthy receipt belongs to this
    # waking agent, rather than to an older Codex process that happened to use the same scope.
    session_fingerprint: str = ""


@dataclass
class IncidentReport:
    snapshot: HealthSnapshot
    state_path: str = ""
    alert_due: list[str] = field(default_factory=list)
    alert_attempted: bool = False
    alert_attempt_succeeded: bool = False
    state_error: str = ""


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _finite_now(value: float | None = None) -> float:
    value = time.time() if value is None else float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("now must be a finite non-negative epoch")
    return value


def session_fingerprint(value: object) -> str:
    """Return a privacy-preserving exact session binding, or empty when unavailable."""
    raw = str(value or "").strip()
    return hashlib.sha256(raw.encode("utf-8", "strict")).hexdigest() if raw else ""


def check_key(runtime: str, scope: str) -> str:
    return "%s:%s" % (runtime, scope)


def session_check_key(runtime: str, scope: str, fingerprint: str) -> str:
    """Return a privacy-safe receipt key for one exact host session."""
    if not _HEX64.fullmatch(str(fingerprint or "")):
        raise ValueError("session fingerprint must be 64 lowercase hex characters")
    return check_key(runtime, scope) + SESSION_KEY_MARKER + fingerprint


def _home(value: str | os.PathLike[str] | None = None) -> Path:
    return Path(value) if value is not None else Path(
        os.environ.get("USERPROFILE") or str(Path.home()))


def _mem(home: Path, value: str | os.PathLike[str] | None = None) -> Path:
    if value is not None:
        return Path(value)
    configured = os.environ.get("BRAIN_MEM_DIR")
    return (Path(configured) if configured else
            home / ".claude" / "projects" / "{{BRAIN_SLUG}}" / "memory")


def _dreaming(home: Path, value: str | os.PathLike[str] | None = None) -> Path:
    return Path(value) if value is not None else home / ".claude" / "dreaming"


def _state_dir(home: Path, value: str | os.PathLike[str] | None = None) -> Path:
    configured = os.environ.get("BRAIN_SELFCARE_STATE_DIR")
    return Path(value or configured or (home / ".claude" / "brain_health"))


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _regular_info(path: Path) -> os.stat_result:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or _is_reparse(info):
        raise OSError("not a regular non-link file")
    return info


def _sha256(path: Path, limit: int = MAX_TEXT_PROBE_BYTES) -> tuple[str, int]:
    info = _regular_info(path)
    if info.st_size > limit:
        # Hashing the 1.3MB MEMORY.md is useful; hashing an accidentally huge source at every
        # agent wake is not.  The size is still observed and the condition is loud.
        raise OSError("file is %d bytes; health hash limit is %d" % (info.st_size, limit))
    raw = path.read_bytes()
    if len(raw) != info.st_size:
        raise OSError("file changed while read")
    raw.decode("utf-8")
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _read_rendered_file(path: str | os.PathLike[str] | Path) -> str:
    """Read one exact UTF-8 wake snapshot after regular-file and size verification."""
    path = Path(path)
    info = _regular_info(path)
    if info.st_size > MAX_TEXT_PROBE_BYTES:
        raise ValueError("rendered wake snapshot is too large")
    raw = path.read_bytes()
    if len(raw) != info.st_size:
        raise ValueError("rendered wake snapshot changed while being read")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise ValueError("rendered wake snapshot is not strict UTF-8") from exc


def _observe_file(path: Path, label: str, now: float, *,
                  stale_hours: float | None = None, min_bytes: int = 1,
                  scope: str = "wake") -> tuple[dict, list[Finding]]:
    findings: list[Finding] = []
    observation = {"label": label, "path": str(path), "exists": False}
    try:
        digest, size = _sha256(path)
        info = _regular_info(path)
        age_hours = max(0.0, (now - info.st_mtime) / 3600.0)
        observation.update({"exists": True, "bytes": size, "sha256": digest,
                            "age_hours": round(age_hours, 3)})
        if size < min_bytes:
            findings.append(Finding(
                "required-source-empty", "critical",
                "%s is empty or implausibly short" % label,
                "%s has %d bytes; minimum is %d" % (path, size, min_bytes),
                str(path), scope))
        if stale_hours is not None and age_hours > stale_hours:
            findings.append(Finding(
                "required-source-stale", "warning",
                "%s is %.1f hours old" % (label, age_hours),
                "freshness ceiling is %.1f hours: %s" % (stale_hours, path),
                str(path), scope))
    except Exception as exc:
        observation["error"] = "%s: %s" % (type(exc).__name__, exc)
        findings.append(Finding(
            "required-source-unreadable", "critical",
            "%s is missing, unsafe, non-UTF-8, or unreadable" % label,
            "%s: %s" % (path, observation["error"]), str(path), scope))
    return observation, findings


def _latest_dream(mem: Path) -> Path | None:
    """Newest dream journal BY WRITE TIME, not by filename.

    Same-day journals carry type suffixes (nap, rem, rem_connector, sws), so lexical name
    order is not recency: 'sws' sorts after 'rem' whatever the clock said. On 2026-08-17 that
    made the wake pick dream_2026_08_15_sws.md (written 03:51) over dream_2026_08_15_rem.md
    (written 21:12) and report the latest dream as 47.2h old when the true newest was ~30h - a
    17-hour pessimistic phantom that cost the owner a CRITICAL-looking alert. run_dream.ps1
    already had this right ("lexical name order does not represent recency"); this is the same
    rule, applied to the checker. Files that vanish mid-glob are skipped rather than fatal.
    """
    root = mem / "dreams"
    try:
        rows = []
        for path in root.glob("dream_*.md"):
            try:
                rows.append((path.stat().st_mtime, path))
            except OSError:
                continue
        if not rows:
            return None
        return max(rows, key=lambda row: row[0])[1]
    except OSError:
        return None


def _compiled_gut_receipt(dreaming: Path, scope: str) -> tuple[dict, list[Finding]]:
    """Verify that overlay, hook TSV, and human-readable gut are one compiled generation."""
    receipt_path = dreaming / "instincts_compile_receipt.json"
    observation = {"label": "compiled gut receipt", "path": str(receipt_path), "ok": False}
    try:
        raw = _read_rendered_file(receipt_path)
        value = json.loads(raw)
        if (not isinstance(value, dict)
                or value.get("schema") != "brain-instincts-compile-receipt-v1"
                or not isinstance(value.get("files"), dict)):
            raise ValueError("compiled gut receipt schema mismatch")
        expected_names = ("instincts_learned.json", "instincts.tsv", "INSTINCTS.md")
        hashes = {}
        for name in expected_names:
            expected = str(value["files"].get(name) or "")
            if not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("receipt hash for %s is malformed" % name)
            actual, size = _sha256(dreaming / name)
            hashes[name] = {"sha256": actual, "bytes": size}
            if actual != expected:
                raise ValueError("%s does not match its compile receipt" % name)
        observation.update({"ok": True, "files": hashes,
                            "compiled_at": value.get("compiled_at"),
                            "reflex_count": value.get("reflex_count")})
        return observation, []
    except Exception as exc:
        observation["error"] = "%s: %s" % (type(exc).__name__, exc)
        return observation, [Finding(
            "compiled-gut-receipt-invalid", "critical",
            "the compiled gut is missing a valid three-file generation receipt",
            observation["error"], str(receipt_path), scope)]


def _mirror_findings(home: Path, scope: str) -> tuple[list[dict], list[Finding]]:
    runtime = home / ".claude" / "dreaming"
    canonical = (home / "brain-repo" /
                 "12_cognitive_architecture" / "dreaming")
    observations = []
    findings = []
    pairs = [
        (name, runtime / name, canonical / name)
        for name in (
            "brain.py", "bm25.py", "spread_recall.py", "codex_wake.py",
            "brain_node_documents.py", "brain_selfcare.py",
            "codex_brain_wake_guard.py", "codex_stop_guard.py",
            "codex_warning_acceptance.py", "hook_liveness.py", "instincts.py",
        )
    ]
    pairs.append((
        "wake_brain.ps1",
        home / ".claude" / "wake_brain.ps1",
        home / "brain-repo" / "12_cognitive_architecture" /
        "wake_brain.ps1",
    ))
    for name, live, source in pairs:
        if not live.exists() and not source.exists():
            continue
        row = {"label": "runtime-mirror:" + name, "runtime": str(live),
               "canonical": str(source)}
        try:
            live_sha, live_size = _sha256(live)
            source_sha, source_size = _sha256(source)
            row.update({"runtime_sha256": live_sha, "canonical_sha256": source_sha,
                        "runtime_bytes": live_size, "canonical_bytes": source_size})
            if live_sha != source_sha:
                findings.append(Finding(
                    "runtime-canonical-drift", "critical",
                    "live %s differs from its canonical brain source" % name,
                    "runtime sha256 %s; canonical sha256 %s" % (live_sha, source_sha),
                    name, scope))
        except Exception as exc:
            row["error"] = "%s: %s" % (type(exc).__name__, exc)
            findings.append(Finding(
                "runtime-canonical-unverifiable", "critical",
                "cannot verify live/canonical identity for %s" % name,
                row["error"], name, scope))
        observations.append(row)
    return observations, findings


def check_wake_health(*, runtime: str, scope: str = "wake",
                      home: str | os.PathLike[str] | None = None,
                      mem: str | os.PathLike[str] | None = None,
                      dreaming: str | os.PathLike[str] | None = None,
                      now: float | None = None,
                      rendered_text: str | None = None,
                      rendered_chars: int | None = None,
                      budget_chars: int | None = None,
                      truncated: bool = False,
                      digest_error: str = "",
                      check_mirrors: bool = True,
                      session_id: object = "") -> HealthSnapshot:
    """Inspect source completeness and bind it to one rendered-wake receipt."""
    now = _finite_now(now)
    home_path = _home(home)
    mem_path = _mem(home_path, mem)
    dreaming_path = _dreaming(home_path, dreaming)
    observations: list[dict] = []
    findings: list[Finding] = []

    specs = (
        (home_path / ".claude" / "brain_startup.md", "brain startup identity", None, 80),
        (mem_path / "MEMORY.md", "memory index", None, 80),
        (mem_path / "AUTOBIOGRAPHY.md", "autobiography", None, 40),
        (mem_path / "MODEL_OF_PARTNER.md", "model of the owner", 30.0, 40),
        (mem_path / "STATE_OF_MIND.md", "state of mind", 30.0, 40),
        (dreaming_path / "instincts.tsv", "compiled gut", None, 20),
    )
    for path, label, stale, minimum in specs:
        observation, rows = _observe_file(
            path, label, now, stale_hours=stale, min_bytes=minimum, scope=scope)
        observations.append(observation)
        findings.extend(rows)

    dream = _latest_dream(mem_path)
    if dream is None:
        findings.append(Finding(
            "dream-journal-missing", "warning", "no dream journal is in sight",
            "expected memory/dreams/dream_*.md", str(mem_path / "dreams"), scope))
        observations.append({"label": "latest dream", "exists": False,
                             "path": str(mem_path / "dreams")})
    else:
        observation, rows = _observe_file(
            dream, "latest dream", now, stale_hours=30.0, min_bytes=40, scope=scope)
        observations.append(observation)
        findings.extend(rows)

    # Authored reflexes newer than the compiled gut means the compile pass failed or never ran.
    learned = dreaming_path / "instincts_learned.json"
    compiled = dreaming_path / "instincts.tsv"
    try:
        learned_info = _regular_info(learned)
        compiled_info = _regular_info(compiled)
        if learned_info.st_mtime > compiled_info.st_mtime + 300:
            findings.append(Finding(
                "compiled-gut-stale", "critical",
                "the learned reflex overlay is newer than the compiled gut",
                "run instincts.py and read its traceback; learned=%s compiled=%s" %
                (_iso(learned_info.st_mtime), _iso(compiled_info.st_mtime)),
                str(compiled), scope))
    except OSError:
        # Required compiled-file failure is already reported above.  A missing optional learned
        # overlay is not itself a fault.
        pass

    gut_receipt, gut_receipt_findings = _compiled_gut_receipt(dreaming_path, scope)
    observations.append(gut_receipt)
    findings.extend(gut_receipt_findings)

    try:
        import brain_node_documents as node_docs
        attachment_rows = node_docs.verify_all(mem_path)
        observations.append({"label": "node documents", "bundles": attachment_rows})
        for row in attachment_rows:
            if not row.get("ok"):
                findings.append(Finding(
                    "node-document-integrity", "critical",
                    "attached documents for %s failed integrity verification" % row.get("node"),
                    str(row.get("error") or "unknown attachment error"),
                    str(row.get("node") or "unknown"), scope))
        # This separately enforces the global wake budget; per-node verification alone cannot.
        node_docs.render_wake_documents(mem_path)
    except ImportError as exc:
        findings.append(Finding(
            "node-document-reader-missing", "critical",
            "the node-document reader is unavailable", str(exc),
            "brain_node_documents.py", scope))
    except Exception as exc:
        findings.append(Finding(
            "wake-document-integrity", "critical",
            "wake-attached documents cannot be rendered completely",
            "%s: %s" % (type(exc).__name__, exc), "wake-documents", scope))

    if check_mirrors:
        rows, mirror_issues = _mirror_findings(home_path, scope)
        observations.extend(rows)
        findings.extend(mirror_issues)

    if rendered_text is not None:
        actual_chars = len(rendered_text)
        if rendered_chars is not None and rendered_chars != actual_chars:
            findings.append(Finding(
                "wake-receipt-length-mismatch", "critical",
                "wake receipt length does not match the rendered text",
                "declared=%s actual=%s" % (rendered_chars, actual_chars),
                runtime, scope))
        rendered_chars = actual_chars
        rendered_sha = hashlib.sha256(rendered_text.encode("utf-8")).hexdigest()
    else:
        rendered_sha = ""
    if rendered_chars is not None and rendered_chars < 1:
        findings.append(Finding(
            "wake-output-empty", "critical", "the rendered wake is empty",
            "runtime=%s" % runtime, runtime, scope))
    if truncated:
        findings.append(Finding(
            "wake-output-truncated", "critical",
            "the wake renderer dropped part of the brain notes",
            "rendered_chars=%s budget_chars=%s" % (rendered_chars, budget_chars),
            runtime, scope))
    if (budget_chars is not None and rendered_chars is not None and
            rendered_chars > budget_chars):
        findings.append(Finding(
            "wake-output-over-budget", "critical",
            "the wake output exceeds the host's safe inline budget",
            "rendered_chars=%d budget_chars=%d" % (rendered_chars, budget_chars),
            runtime, scope))
    if digest_error:
        findings.append(Finding(
            "wake-render-exception", "critical", "the wake renderer raised an exception",
            digest_error[:1000], runtime, scope))

    # One condition should appear once even if two probes reached it.
    unique = {finding.fingerprint: finding for finding in findings}
    ordered = sorted(unique.values(), key=lambda finding: (
        SEVERITY_ORDER.get(finding.severity, 9), finding.code, finding.subject))
    return HealthSnapshot(
        runtime=runtime,
        scope=scope,
        checked_at=_iso(now),
        findings=ordered,
        observations=observations,
        rendered_chars=rendered_chars,
        rendered_sha256=rendered_sha,
        budget_chars=budget_chars,
        truncated=truncated,
        session_fingerprint=session_fingerprint(session_id),
    )


@contextlib.contextmanager
def _state_lock(path: Path, timeout_seconds: float = 2.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    deadline = time.monotonic() + timeout_seconds
    locked = False
    try:
        while time.monotonic() < deadline:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                time.sleep(0.01)
        if not locked:
            raise TimeoutError("brain self-care state lock timed out")
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _empty_state() -> dict:
    return {"schema": STATE_SCHEMA, "incidents": {}, "checks": {}}


def _read_state(path: Path) -> dict:
    if not path.exists():
        return _empty_state()
    info = _regular_info(path)
    if info.st_size > MAX_STATE_BYTES:
        raise ValueError("brain self-care state is too large")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != STATE_SCHEMA:
        raise ValueError("brain self-care state schema mismatch")
    if not isinstance(data.get("incidents"), dict) or not isinstance(data.get("checks"), dict):
        raise ValueError("brain self-care state shape mismatch")
    return data


def _atomic_json(path: Path, value: dict) -> None:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    tmp = path.with_name(".%s.tmp-%d-%d" % (path.name, os.getpid(), time.time_ns()))
    fd = None
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            tmp.unlink()
        except OSError:
            pass


def _prune_session_receipts(checks: dict, base_key: str) -> None:
    """Bound compact per-session receipts without disturbing other wake scopes."""
    prefix = base_key + SESSION_KEY_MARKER
    keys = [key for key in checks if isinstance(key, str) and key.startswith(prefix)]
    if len(keys) <= MAX_SESSION_RECEIPTS_PER_SCOPE:
        return

    def order(key: str) -> tuple[float, str]:
        receipt = checks.get(key)
        try:
            epoch = float(receipt.get("recorded_epoch", 0)) if isinstance(receipt, dict) else 0.0
        except (TypeError, ValueError):
            epoch = 0.0
        return (epoch if math.isfinite(epoch) else 0.0, key)

    for key in sorted(keys, key=order)[:-MAX_SESSION_RECEIPTS_PER_SCOPE]:
        checks.pop(key, None)


def record_snapshot(snapshot: HealthSnapshot, *,
                    state_dir: str | os.PathLike[str] | Path,
                    now: float | None = None,
                    alert_cooldown_seconds: float = DEFAULT_ALERT_COOLDOWN_SECONDS,
                    claimant: str = "") -> IncidentReport:
    """Update incidents and reserve a deduplicated *desktop-attempt* lease.

    ``alert_due`` never means delivered or acknowledged.  Every waking agent still receives the
    owner-alert obligation from :func:`format_agent_notice` while a finding remains active.
    """
    now = _finite_now(now)
    root = Path(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "state.json"
    report = IncidentReport(snapshot=snapshot, state_path=str(state_path))
    current = {finding.fingerprint: finding for finding in snapshot.findings}
    try:
        with _state_lock(root / "state.lock"):
            state = _read_state(state_path)
            incidents = state["incidents"]
            # Resolve only this exact producer scope.  One healthy Codex digest must not resolve
            # a concurrent Claude hook's over-budget receipt (or vice versa).
            for fingerprint, incident in incidents.items():
                if (incident.get("status") == "active" and
                        incident.get("scope") == snapshot.scope and
                        incident.get("runtime") == snapshot.runtime and
                        fingerprint not in current):
                    incident["status"] = "resolved"
                    incident["resolved_at"] = _iso(now)
            for fingerprint, finding in current.items():
                incident = incidents.get(fingerprint) or {
                    "fingerprint": fingerprint,
                    "first_seen": _iso(now),
                    "count": 0,
                }
                incident.update({
                    "status": "active",
                    "last_seen": _iso(now),
                    "count": int(incident.get("count", 0)) + 1,
                    "runtime": snapshot.runtime,
                    "scope": snapshot.scope,
                    "code": finding.code,
                    "severity": finding.severity,
                    "summary": finding.summary,
                    "detail": finding.detail,
                    "subject": finding.subject,
                })
                incident.pop("resolved_at", None)
                last_attempt = float(incident.get("last_alert_attempt_epoch", 0) or 0)
                last_success = bool(incident.get("last_alert_attempt_succeeded", False))
                cooldown = (alert_cooldown_seconds if last_success else
                            min(alert_cooldown_seconds, FAILED_ALERT_RETRY_SECONDS))
                lease_until = float(incident.get("alert_lease_until_epoch", 0) or 0)
                if now - last_attempt >= cooldown and lease_until <= now:
                    report.alert_due.append(fingerprint)
                    incident["alert_lease_until_epoch"] = now + ALERT_LEASE_SECONDS
                    incident["alert_claimed_by"] = claimant or (snapshot.runtime + ":" + str(os.getpid()))
                incidents[fingerprint] = incident
            base_key = check_key(snapshot.runtime, snapshot.scope)
            receipt = {
                "schema": SNAPSHOT_SCHEMA,
                "checked_at": snapshot.checked_at,
                "finding_fingerprints": sorted(current),
                "rendered_chars": snapshot.rendered_chars,
                "rendered_sha256": snapshot.rendered_sha256,
                "budget_chars": snapshot.budget_chars,
                "truncated": snapshot.truncated,
                "session_fingerprint": snapshot.session_fingerprint,
                "observations": snapshot.observations,
            }
            # Keep the historical latest-per-scope entry for dashboards and pre-upgrade guards,
            # but never make concurrent Codex agents fight over it.  A compact hash-keyed receipt
            # for each exact session remains valid when another session wakes moments later.
            state["checks"][base_key] = receipt
            if snapshot.session_fingerprint:
                per_session = dict(receipt)
                per_session["observations"] = []
                per_session["recorded_epoch"] = now
                state["checks"][session_check_key(
                    snapshot.runtime, snapshot.scope, snapshot.session_fingerprint)] = per_session
                _prune_session_receipts(state["checks"], base_key)
            state["updated_at"] = _iso(now)
            _atomic_json(state_path, state)
    except Exception as exc:
        report.state_error = "%s: %s" % (type(exc).__name__, exc)
        # State loss must bias toward alerting, never toward silence.
        report.alert_due = sorted(current)
    return report


def mark_alert_attempt(report: IncidentReport, success: bool, *,
                       now: float | None = None) -> None:
    now = _finite_now(now)
    report.alert_attempted = True
    report.alert_attempt_succeeded = bool(success)
    if not report.state_path or report.state_error or not report.alert_due:
        return
    path = Path(report.state_path)
    try:
        with _state_lock(path.parent / "state.lock"):
            state = _read_state(path)
            for fingerprint in report.alert_due:
                incident = state["incidents"].get(fingerprint)
                if not incident:
                    continue
                incident["last_alert_attempt_epoch"] = now
                incident["last_alert_attempt_at"] = _iso(now)
                incident["last_alert_attempt_succeeded"] = bool(success)
                incident.pop("alert_lease_until_epoch", None)
                incident.pop("alert_claimed_by", None)
            state["updated_at"] = _iso(now)
            _atomic_json(path, state)
    except Exception as exc:
        report.state_error = (report.state_error + "; " if report.state_error else "") + \
            "alert receipt write failed: %s: %s" % (type(exc).__name__, exc)


def _desktop_alert(report: IncidentReport) -> bool:
    """Attempt a local owner-visible notification with no console allocation."""
    if not report.alert_due or os.name != "nt" or os.environ.get(
            "BRAIN_SELFCARE_DESKTOP_ALERT", "1") == "0":
        return False
    top = report.snapshot.findings[:3]
    message = "Brain wake degraded: " + "; ".join(finding.summary for finding in top)
    message = re.sub(r"[\r\n]+", " ", message)[:480]
    # PowerShell is launched synchronously with CREATE_NO_WINDOW.  This cannot create the
    # PseudoConsoleWindow flash that an Interactive scheduled powershell.exe action creates.
    script = (
        "Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Warning;"
        "$n.BalloonTipTitle='Brain self-care alert';"
        "$n.BalloonTipText=$env:BRAIN_SELFCARE_ALERT_TEXT;"
        "$n.Visible=$true;$n.ShowBalloonTip(10000);"
        "Start-Sleep -Milliseconds 350;$n.Dispose()"
    )
    env = os.environ.copy()
    env["BRAIN_SELFCARE_ALERT_TEXT"] = message
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, creationflags=flags, timeout=4, check=False)
        return result.returncode == 0
    except Exception:
        return False


def unattended_claude_session(env: dict | None = None) -> bool:
    """True when this Claude Code session was started with -p (a test arm, a batch worker, the nightly dream): no one
    reads its replies as they come. Positive evidence only - Claude Code marks the session's environment
    (interactive: CLAUDE_CODE_ENTRYPOINT=cli with CLAUDE_CODE_SESSION_ATTENDED=1; claude -p:
    CLAUDE_CODE_ENTRYPOINT=sdk-cli with CLAUDE_CODE_SESSION_ATTENDED=0) - so a runtime that sets neither variable
    keeps the full owner alert."""
    env = os.environ if env is None else env
    attended = str(env.get("CLAUDE_CODE_SESSION_ATTENDED", "")).strip()
    if attended == "1":
        return False
    if attended == "0":
        return True
    return str(env.get("CLAUDE_CODE_ENTRYPOINT", "")).strip().lower() in UNATTENDED_ENTRYPOINTS


UNATTENDED_ENTRYPOINTS = {"sdk-cli"}
_OWNER_ALERT_HEAD = [
    "--- BRAIN SELF-CARE: OWNER ALERT REQUIRED NOW ---",
    "This agent could not prove a fully healthy brain wake. Tell the owner in the first",
    "user-visible update before relying on the affected notes; self-care for the brain is",
    "part of the task, not terminal noise.",
]
_UNATTENDED_HEAD = [
    "--- BRAIN SELF-CARE (unattended session) ---",
    "This wake could not prove a fully healthy brain. This session runs without a reader (claude -p), so nothing it",
    "writes reaches the owner as it is written: do not address the owner in your output - the desktop alert and the",
    "incident ledger carry this. If your task is maintaining the brain, the findings below are yours; otherwise do",
    "your task and treat the affected notes with the doubt the findings name.",
]


def unattended_notice(notice: str) -> str:
    """The same findings for a session nobody reads live, without the instruction to tell the owner in the first
    update. Headless test workers obeyed it and put a brain-health note addressed to the owner inside their answers,
    where the owner never reads it and a grader does."""
    lines = notice.split("\n")
    if lines[:len(_OWNER_ALERT_HEAD)] == _OWNER_ALERT_HEAD:
        lines = _UNATTENDED_HEAD + lines[len(_OWNER_ALERT_HEAD):]
    lines = [line for line in lines if not line.startswith("Desktop notification is currently rate-limited")]
    return "\n".join(lines)


def format_agent_notice(report: IncidentReport, max_chars: int | None = None) -> str:
    findings = report.snapshot.findings
    if not findings and not report.state_error:
        return ""
    # The desktop lane is an unverified attempt, not a delivery receipt.  Therefore its cooldown
    # must never erase the obligation in the waking agent's actual conversational context.
    lines = [
        "--- BRAIN SELF-CARE: OWNER ALERT REQUIRED NOW ---",
        "This agent could not prove a fully healthy brain wake. Tell the owner in the first",
        "user-visible update before relying on the affected notes; self-care for the brain is",
        "part of the task, not terminal noise.",
    ]
    if findings:
        for finding in findings[:8]:
            detail = (" - " + finding.detail) if finding.detail else ""
            lines.append("  - [%s/%s] %s%s" %
                         (finding.severity.upper(), finding.code, finding.summary, detail))
        if len(findings) > 8:
            lines.append("  - ... %d more finding(s) are in the incident ledger" % (len(findings) - 8))
    if report.state_error:
        lines.append("  - [CRITICAL/incident-ledger] %s" % report.state_error)
    if findings and not report.alert_due:
        lines.append(
            "Desktop notification is currently rate-limited or leased; that is not proof of "
            "owner delivery, so the conversational alert above remains mandatory.")
    if report.alert_attempted:
        lines.append("Desktop notification attempt: %s (attempt only; delivery/ack unverified)." %
                     ("accepted by Windows" if report.alert_attempt_succeeded else "failed"))
    if report.state_path:
        lines.append("Incident ledger: %s" % report.state_path)
    rendered = "\n".join(lines)
    if max_chars is None or len(rendered) <= max_chars:
        return rendered
    if max_chars < 400:
        raise ValueError("notice budget must be at least 400 characters")
    ledger = next((line for line in reversed(lines) if line.startswith("Incident ledger:")), "")
    suffix = "\n  - [... details truncated here; read the incident ledger ...]"
    if ledger:
        suffix += "\n" + ledger
    keep = max_chars - len(suffix)
    return rendered[:max(1, keep)].rstrip() + suffix


def check_report_notify(*, runtime: str, scope: str = "wake",
                        home: str | os.PathLike[str] | None = None,
                        mem: str | os.PathLike[str] | None = None,
                        dreaming: str | os.PathLike[str] | None = None,
                        state_dir: str | os.PathLike[str] | None = None,
                        now: float | None = None,
                        rendered_text: str | None = None,
                        rendered_chars: int | None = None,
                        budget_chars: int | None = None,
                        truncated: bool = False,
                        digest_error: str = "",
                        check_mirrors: bool = True,
                        desktop_alert: bool = True,
                        write_state: bool = True,
                        claimant: str = "",
                        session_id: object = "") -> IncidentReport:
    now = _finite_now(now)
    home_path = _home(home)
    snapshot = check_wake_health(
        runtime=runtime, scope=scope, home=home_path, mem=mem, dreaming=dreaming,
        now=now, rendered_text=rendered_text, rendered_chars=rendered_chars,
        budget_chars=budget_chars, truncated=truncated, digest_error=digest_error,
        check_mirrors=check_mirrors, session_id=session_id)
    if write_state:
        report = record_snapshot(
            snapshot, state_dir=_state_dir(home_path, state_dir), now=now,
            claimant=claimant)
    else:
        report = IncidentReport(snapshot=snapshot,
                                alert_due=[finding.fingerprint for finding in snapshot.findings])
    if desktop_alert and report.alert_due:
        success = _desktop_alert(report)
        if write_state:
            mark_alert_attempt(report, success, now=now)
        else:
            report.alert_attempted = True
            report.alert_attempt_succeeded = success
    return report


def status(state_dir: str | os.PathLike[str] | Path) -> dict:
    path = Path(state_dir) / "state.json"
    state = _read_state(path)
    active = [incident for incident in state["incidents"].values()
              if incident.get("status") == "active"]
    return {"schema": STATE_SCHEMA, "active": sorted(
        active, key=lambda row: (SEVERITY_ORDER.get(row.get("severity"), 9),
                                 row.get("first_seen", ""))),
            "active_count": len(active), "state_path": str(path),
            "updated_at": state.get("updated_at")}


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--runtime", required=True)
    check.add_argument("--scope", default="wake")
    check.add_argument("--rendered-chars", type=int)
    check.add_argument("--rendered-file", default="")
    check.add_argument("--budget-chars", type=int)
    check.add_argument("--truncated", action="store_true")
    check.add_argument("--digest-error", default="")
    check.add_argument("--no-desktop", action="store_true")
    check.add_argument("--no-mirrors", action="store_true")
    check.add_argument("--no-write", action="store_true")
    check.add_argument("--strict", action="store_true")
    check.add_argument("--notice-chars", type=int)
    check.add_argument("--session-id", default="")
    show = sub.add_parser("status")
    show.add_argument("--state-dir", default="")
    args = parser.parse_args(argv)
    if args.command == "status":
        home = _home()
        print(json.dumps(status(_state_dir(home, args.state_dir or None)),
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    try:
        rendered_text = _read_rendered_file(args.rendered_file) if args.rendered_file else None
    except Exception as exc:
        print("--- BRAIN SELF-CARE: OWNER ALERT REQUIRED NOW ---")
        print("Rendered wake receipt could not be read (%s: %s). Tell the owner now." %
              (type(exc).__name__, exc))
        return 2
    report = check_report_notify(
        runtime=args.runtime, scope=args.scope,
        rendered_text=rendered_text, rendered_chars=args.rendered_chars,
        budget_chars=args.budget_chars,
        truncated=args.truncated, digest_error=args.digest_error,
        check_mirrors=not args.no_mirrors, desktop_alert=not args.no_desktop,
        write_state=not args.no_write, session_id=args.session_id)
    notice = format_agent_notice(report, max_chars=args.notice_chars)
    if notice and args.runtime == "claude" and unattended_claude_session():
        notice = unattended_notice(notice)
    if notice:
        print(notice)
    elif not args.strict:
        print("BRAIN SELF-CARE: PASS - required wake sources and attachments verified")
    return 2 if args.strict and (report.snapshot.findings or report.state_error) else 0


if __name__ == "__main__":
    raise SystemExit(_cli())
