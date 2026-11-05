"""Strict discovery-only import boundary for local ProofSwarm tickets."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .models import Claim, Evidence, EvidenceKind, content_hash

MAX_PROOFSWARM_TICKET_BYTES = 8 * 1024 * 1024
MAX_PROOFSWARM_EVIDENCE_ITEMS = 10_000
MAX_PROOFSWARM_TEXT_BYTES = 2 * 1024 * 1024
MAX_PROOFSWARM_JSON_DEPTH = 64
MAX_PROOFSWARM_JSON_NODES = 200_000

_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BARE_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "ticket_id",
        "problem_id",
        "statement_informal",
        "statement_lean",
        "assumptions",
        "parents",
        "children",
        "verdict",
        "evidence",
        "history",
        "confidence",
        "cost_usd",
        "store_revision",
        "created_at",
        "updated_at",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {
        "kind",
        "content",
        "sha256",
        "produced_by",
        "produced_at",
        "cost_usd",
        "metadata",
    }
)
_VERDICTS = frozenset(
    {
        "open",
        "lean_verified",
        "certificate_verified",
        "disproven",
        "refuted_by_referee",
        "needs_human",
        "latex_reviewed",
        "latex_refuted",
        "latex_open",
    }
)
_EVIDENCE_KINDS = frozenset(
    {
        "lean_proof",
        "numerical_certificate",
        "informal_argument",
        "counterexample",
        "referee_finding",
    }
)


def _reject_constant(value: str) -> None:
    raise ValueError(f"ProofSwarm ticket contains non-finite JSON number {value!r}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ProofSwarm ticket contains a duplicate JSON object key")
        result[key] = value
    return result


def _parse_ticket(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise TypeError("ProofSwarm ticket must be exact bytes")
    if not raw or len(raw) > MAX_PROOFSWARM_TICKET_BYTES:
        raise ValueError("ProofSwarm ticket violates the fixed byte bound")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("ProofSwarm ticket must be strict UTF-8") from exc
    try:
        value = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_without_duplicates,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("ProofSwarm ticket is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("ProofSwarm ticket must be a JSON object")
    _validate_json_shape(value)
    return value


def _validate_json_shape(value: object) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_PROOFSWARM_JSON_NODES or depth > MAX_PROOFSWARM_JSON_DEPTH:
            raise ValueError("ProofSwarm ticket exceeds the JSON structure budget")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif not isinstance(current, (str, int, Decimal, bool, type(None))):
            raise ValueError("ProofSwarm ticket contains an unsupported JSON value")


def _require_exact_fields(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"ProofSwarm {label} fields do not match the pinned schema")


def _required_text(value: object, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ProofSwarm {label} must be nonempty text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError(f"ProofSwarm {label} must be valid Unicode text") from exc
    if len(encoded) > maximum:
        raise ValueError(f"ProofSwarm {label} exceeds its fixed byte bound")
    return value


def _identifier(value: object, label: str) -> str:
    text = _required_text(value, label, maximum=256)
    if not _ID_RE.fullmatch(text):
        raise ValueError(f"ProofSwarm {label} is not normalized")
    return text


def _timestamp(value: object, label: str) -> str:
    text = _required_text(value, label, maximum=128)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"ProofSwarm {label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"ProofSwarm {label} must include a UTC offset")
    return text


def _text_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_PROOFSWARM_EVIDENCE_ITEMS:
        raise ValueError(f"ProofSwarm {label} must be a bounded list")
    return tuple(_required_text(item, label) for item in value)


def _identifier_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_PROOFSWARM_EVIDENCE_ITEMS:
        raise ValueError(f"ProofSwarm {label} must be a bounded list")
    return tuple(_identifier(item, label) for item in value)


def _require_zero_cost(value: object, label: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, Decimal))
        or not Decimal(value).is_finite()
        or Decimal(value) != 0
    ):
        raise ValueError(f"ProofSwarm {label} must have zero marginal API cost")


@dataclass(frozen=True, slots=True)
class ProofSwarmImportCandidate:
    """A parsed ticket that remains categorically ineligible for promotion."""

    ticket_id: str
    problem_id: str
    verdict: str
    ticket_digest: str
    statement_digest: str
    source_tree_digest: str
    evidence_digests: tuple[str, ...]
    store_revision: int
    discovery_evidence: Evidence

    @property
    def promotion_eligible(self) -> bool:
        return False

    def digest(self) -> str:
        return content_hash(
            {
                "ticket_id": self.ticket_id,
                "problem_id": self.problem_id,
                "verdict": self.verdict,
                "ticket_digest": self.ticket_digest,
                "statement_digest": self.statement_digest,
                "source_tree_digest": self.source_tree_digest,
                "evidence_digests": self.evidence_digests,
                "store_revision": self.store_revision,
                "discovery_evidence_digest": self.discovery_evidence.digest(),
                "promotion_eligible": False,
            }
        )


def import_proofswarm_ticket(
    raw: bytes,
    *,
    claim: Claim,
    observed_source_tree_digest: str,
    expected_source_tree_digest: str,
) -> ProofSwarmImportCandidate:
    """Parse one pinned local ticket and return discovery evidence only.

    A ticket verdict, including ``lean_verified``, is model/runtime report data.
    This boundary never creates an attestation or receipt and always marks the
    resulting evidence as failed for promotion purposes. A trusted verifier must
    independently rerun the exact artifact before ordinary authority gates apply.
    """

    if not isinstance(claim, Claim):
        raise TypeError("ProofSwarm import requires a Claim")
    for label, digest in (
        ("observed source tree", observed_source_tree_digest),
        ("expected source tree", expected_source_tree_digest),
    ):
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"ProofSwarm {label} digest must be SHA-256")
    if observed_source_tree_digest != expected_source_tree_digest:
        raise ValueError("ProofSwarm source tree differs from the pinned integration boundary")

    ticket = _parse_ticket(raw)
    _require_exact_fields(ticket, _TOP_LEVEL_FIELDS, "ticket")
    ticket_id = _identifier(ticket["ticket_id"], "ticket ID")
    problem_id = _identifier(ticket["problem_id"], "problem ID")
    statement = _required_text(
        ticket["statement_informal"],
        "informal statement",
        maximum=MAX_PROOFSWARM_TEXT_BYTES,
    )
    if statement != claim.statement:
        raise ValueError("ProofSwarm ticket statement does not exactly match the claim")
    formal_statement = ticket["statement_lean"]
    if formal_statement is not None:
        _required_text(
            formal_statement,
            "formal statement",
            maximum=MAX_PROOFSWARM_TEXT_BYTES,
        )
    _text_list(ticket["assumptions"], "assumptions")
    _identifier_list(ticket["parents"], "parent IDs")
    _identifier_list(ticket["children"], "child IDs")

    verdict = ticket["verdict"]
    if not isinstance(verdict, str) or verdict not in _VERDICTS:
        raise ValueError("ProofSwarm ticket verdict is outside the pinned schema")
    _require_zero_cost(ticket["cost_usd"], "ticket")
    confidence = ticket["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, Decimal))
        or not Decimal(confidence).is_finite()
        or not Decimal(0) <= Decimal(confidence) <= Decimal(1)
    ):
        raise ValueError("ProofSwarm confidence must be a finite probability")
    revision = ticket["store_revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("ProofSwarm store revision must be a nonnegative integer")
    created_at = _timestamp(ticket["created_at"], "creation time")
    updated_at = _timestamp(ticket["updated_at"], "update time")
    if datetime.fromisoformat(updated_at) < datetime.fromisoformat(created_at):
        raise ValueError("ProofSwarm update time precedes creation time")
    history = ticket["history"]
    if (
        not isinstance(history, list)
        or len(history) > MAX_PROOFSWARM_EVIDENCE_ITEMS
        or any(not isinstance(item, dict) for item in history)
    ):
        raise ValueError("ProofSwarm history must be a bounded list of objects")

    rows = ticket["evidence"]
    if not isinstance(rows, list) or len(rows) > MAX_PROOFSWARM_EVIDENCE_ITEMS:
        raise ValueError("ProofSwarm evidence must be a bounded list")
    evidence_digests: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("ProofSwarm evidence rows must be objects")
        _require_exact_fields(row, _EVIDENCE_FIELDS, "evidence")
        if not isinstance(row["kind"], str) or row["kind"] not in _EVIDENCE_KINDS:
            raise ValueError("ProofSwarm evidence kind is outside the pinned schema")
        content = _required_text(
            row["content"],
            "evidence content",
            maximum=MAX_PROOFSWARM_TEXT_BYTES,
        )
        content_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        claimed_digest = row["sha256"]
        if claimed_digest is not None and (
            not isinstance(claimed_digest, str)
            or not _BARE_SHA256_RE.fullmatch(claimed_digest)
            or claimed_digest != content_digest
        ):
            raise ValueError("ProofSwarm evidence digest does not bind its exact content")
        _required_text(row["produced_by"], "evidence producer", maximum=256)
        _timestamp(row["produced_at"], "evidence time")
        _require_zero_cost(row["cost_usd"], "evidence")
        if not isinstance(row["metadata"], dict):
            raise ValueError("ProofSwarm evidence metadata must be an object")
        evidence_digests.append("sha256:" + content_digest)

    ticket_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    discovery = Evidence(
        claim_id=claim.id,
        kind=EvidenceKind.DISCOVERY_REPORT,
        summary=f"Untrusted ProofSwarm ticket {ticket_id} ({verdict})",
        scope="exact claim text; discovery-only import",
        verifier="proofswarm-local-import-v1",
        passed=False,
        artifact_hash=ticket_digest,
        limitations=(
            "Ticket verdict and metadata are report data, not verification authority.",
            "No authenticated authority attestation or receipt was produced.",
            "The exact artifact must be independently rerun before promotion.",
        ),
        id=f"evidence_proofswarm_{ticket_digest.removeprefix('sha256:')}",
        created_at=updated_at,
    )
    return ProofSwarmImportCandidate(
        ticket_id=ticket_id,
        problem_id=problem_id,
        verdict=verdict,
        ticket_digest=ticket_digest,
        statement_digest="sha256:"
        + hashlib.sha256(statement.encode("utf-8")).hexdigest(),
        source_tree_digest=observed_source_tree_digest,
        evidence_digests=tuple(evidence_digests),
        store_revision=revision,
        discovery_evidence=discovery,
    )
