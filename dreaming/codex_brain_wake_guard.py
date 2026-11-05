#!/usr/bin/env python3
"""Fast Codex hook guard for session-bound brain-wake receipts.

Codex currently exposes UserPromptSubmit and Stop hooks, but no supported SessionStart
hook.  The first-prompt lane therefore tells the agent to run ``codex_wake.py`` when this
exact session has no receipt, and the Stop lane prevents an answer from leaving before a
fixable missing/stale receipt is replaced.  A degraded or unreadable receipt is fail-loud:
the answer may proceed only after it explicitly tells the owner that brain self-care is
degraded.  Receipts are keyed by a one-way session fingerprint so concurrent Codex agents
cannot invalidate each other by overwriting a single global "latest wake" slot.

This reader is deliberately small, bounded, read-only, and stdlib-only.  It never treats an
older session's healthy receipt as evidence for the current agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


STATE_SCHEMA = "brain-selfcare-state-v1"
SNAPSHOT_SCHEMA = "brain-wake-health-snapshot-v1"
CHECK_KEY = "codex:wake:codex"
SESSION_KEY_MARKER = ":session:"
MAX_STATE_BYTES = 4 * 1024 * 1024
MAX_CONTEXT_CHARS = 3800
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DISCLOSURE = re.compile(
    r"(?:brain\s+self[- ]care|brain\s+wake)[^\n.]{0,180}"
    r"(?:alert|degrad|unhealth|unverif|failed|missing|could\s+not\s+prove|unavailable)",
    re.I,
)


@dataclass(frozen=True)
class ReceiptStatus:
    kind: str
    state_path: str
    detail: str = ""
    findings: tuple[str, ...] = field(default_factory=tuple)


def session_id(payload: Mapping[str, Any]) -> str:
    return str(payload.get("session_id") or payload.get("thread_id")
               or payload.get("session") or payload.get("transcript_path") or "").strip()


def session_fingerprint(value: object) -> str:
    raw = str(value or "").strip()
    return hashlib.sha256(raw.encode("utf-8", "strict")).hexdigest() if raw else ""


def session_check_key(fingerprint: str) -> str:
    if not _HEX64.fullmatch(str(fingerprint or "")):
        raise ValueError("session fingerprint must be 64 lowercase hex characters")
    return CHECK_KEY + SESSION_KEY_MARKER + fingerprint


def default_state_path() -> Path:
    configured = os.environ.get("BRAIN_SELFCARE_STATE_DIR")
    home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    return Path(configured) / "state.json" if configured else \
        home / ".claude" / "brain_health" / "state.json"


def _regular_json(path: Path) -> Mapping[str, Any]:
    info = os.lstat(path)
    reparse = bool(getattr(info, "st_file_attributes", 0) &
                   getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or reparse:
        raise OSError("state is not a regular non-link file")
    if info.st_size > MAX_STATE_BYTES:
        raise ValueError("state exceeds %d-byte guard" % MAX_STATE_BYTES)
    raw = path.read_bytes()
    if len(raw) != info.st_size:
        raise OSError("state changed while read")
    value = json.loads(raw.decode("utf-8", "strict"))
    if not isinstance(value, Mapping) or value.get("schema") != STATE_SCHEMA:
        raise ValueError("state schema mismatch")
    if not isinstance(value.get("checks"), Mapping) or not isinstance(
            value.get("incidents"), Mapping):
        raise ValueError("state shape mismatch")
    return value


def inspect_receipt(payload: Mapping[str, Any], *,
                    state_path: str | os.PathLike[str] | Path | None = None) -> ReceiptStatus:
    path = Path(state_path) if state_path is not None else default_state_path()
    current = session_fingerprint(session_id(payload))
    if not current:
        return ReceiptStatus(
            "unbound", str(path),
            "the Codex hook payload has no stable session identifier, so the wake cannot be bound")
    # ``Path.exists`` follows links and would misclassify a broken link as a harmless absent
    # ledger.  lexists keeps every link/reparse case on the strict-reader path.
    if not os.path.lexists(path):
        return ReceiptStatus("missing", str(path), "no brain self-care state exists yet")
    try:
        state = _regular_json(path)
        # Prefer this exact session's durable receipt.  The base key remains a migration
        # fallback for ledgers written before per-session receipts were introduced.
        receipt = state["checks"].get(session_check_key(current))
        if receipt is None:
            receipt = state["checks"].get(CHECK_KEY)
        if receipt is None:
            return ReceiptStatus("missing", str(path), "no Codex wake receipt exists")
        if not isinstance(receipt, Mapping) or receipt.get("schema") != SNAPSHOT_SCHEMA:
            raise ValueError("Codex wake receipt schema mismatch")
        stored = str(receipt.get("session_fingerprint") or "")
        if not stored:
            return ReceiptStatus(
                "unbound", str(path),
                "the latest Codex wake receipt is not bound to any host session")
        if not _HEX64.fullmatch(stored):
            raise ValueError("Codex wake session fingerprint is malformed")
        if stored != current:
            return ReceiptStatus(
                "stale", str(path),
                "the latest Codex wake receipt belongs to a different session")
        rendered_chars = receipt.get("rendered_chars")
        rendered_sha = str(receipt.get("rendered_sha256") or "")
        if isinstance(rendered_chars, bool) or not isinstance(rendered_chars, int) \
                or rendered_chars < 1 or not _HEX64.fullmatch(rendered_sha):
            raise ValueError("Codex wake receipt does not bind a non-empty rendered digest")
        fingerprints = receipt.get("finding_fingerprints")
        if not isinstance(fingerprints, list) or any(
                not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{24}", item)
                for item in fingerprints):
            raise ValueError("Codex wake finding list is malformed")
        summaries = []
        incidents = state["incidents"]
        for fingerprint in fingerprints[:6]:
            incident = incidents.get(fingerprint)
            if isinstance(incident, Mapping):
                code = str(incident.get("code") or "brain-wake-finding")[:80]
                summary = re.sub(r"[\r\n]+", " ", str(incident.get("summary") or ""))[:240]
                summaries.append("%s: %s" % (code, summary) if summary else code)
            else:
                summaries.append("unresolved finding %s" % fingerprint)
        if bool(receipt.get("truncated")) and not fingerprints:
            summaries.append("wake-output-truncated")
        if fingerprints or bool(receipt.get("truncated")):
            return ReceiptStatus(
                "degraded", str(path),
                "this session's wake receipt contains %d active finding(s)" % len(fingerprints),
                tuple(summaries))
        return ReceiptStatus("healthy", str(path), "session-bound wake receipt verified")
    except Exception as exc:
        return ReceiptStatus(
            "corrupt", str(path), "%s: %s" % (type(exc).__name__, str(exc)[:500]))


def _wake_command() -> str:
    home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    return r'C:\Python314\python.exe -B "%s"' % (
        home / ".claude" / "dreaming" / "codex_wake.py")


def _bounded(lines: list[str], status: ReceiptStatus) -> str:
    pointer = "Receipt ledger: %s" % status.state_path[:500]
    text = "\n".join(lines + [pointer])
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    suffix = "\n[details bounded; inspect the receipt ledger]\n" + pointer
    return text[:MAX_CONTEXT_CHARS - len(suffix)].rstrip() + suffix


def prompt_output(payload: Mapping[str, Any], *,
                  state_path: str | os.PathLike[str] | Path | None = None
                  ) -> dict[str, Any] | None:
    status = inspect_receipt(payload, state_path=state_path)
    if status.kind == "healthy":
        return None
    if status.kind in ("missing", "stale"):
        lines = [
            "--- BRAIN WAKE REQUIRED FOR THIS CODEX SESSION ---",
            "This exact session has no verified complete brain-wake receipt (%s)." % status.detail,
            "Before substantive work, run: %s" % _wake_command(),
            "Do not rely on an older session's receipt. If the wake prints OWNER ALERT REQUIRED NOW,",
            "tell the owner in the first user-visible update before relying on the affected notes.",
        ]
    else:
        lines = [
            "--- BRAIN SELF-CARE: OWNER ALERT REQUIRED NOW ---",
            "This Codex session cannot prove a healthy complete brain wake (%s)." % status.detail,
            "Tell the owner in the first user-visible update and do not silently rely on the affected notes.",
        ]
        lines.extend("  - " + item for item in status.findings)
        if status.kind == "unbound":
            lines.append("Run the wake command if possible: %s" % _wake_command())
    return {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": _bounded(lines, status),
    }}


def stop_block_reason(payload: Mapping[str, Any], *,
                      state_path: str | os.PathLike[str] | Path | None = None) -> str:
    """Return a correction reason, or empty when this answer may leave the session."""
    status = inspect_receipt(payload, state_path=state_path)
    text = str(payload.get("last_assistant_message") or payload.get("text") or "")
    if status.kind == "healthy":
        return ""
    if status.kind in ("missing", "stale"):
        return (
            "[brain_wake_receipt] This exact Codex session still lacks a verified complete "
            "brain-wake receipt (%s). Run %s, read its complete output, and then answer. "
            "An older session's receipt is not evidence for this one." %
            (status.detail, _wake_command())
        )
    if _DISCLOSURE.search(text):
        return ""
    details = "; ".join(status.findings[:4]) or status.detail
    return (
        "[brain_selfcare_owner_alert] This session's brain wake is %s (%s). The outgoing "
        "answer must plainly tell the owner that brain self-care is degraded/unverified and "
        "what remains affected; do not leave this only in hook or terminal output." %
        (status.kind, details)
    )
