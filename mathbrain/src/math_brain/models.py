"""Versioned, hashable records used by the trusted core."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 1


class RecordStatus(StrEnum):
    UNVERIFIED = "unverified"
    PROVISIONAL = "provisional"
    CONDITIONAL = "conditional"
    VERIFIED = "verified"
    GAP_FOUND = "gap_found"
    REFUTED = "refuted"
    RETRACTED = "retracted"
    UNKNOWN = "unknown"
    # statuses written into the store by campaign landings (7 Oct 2026: 34 cards); without them get_claim refused
    # these cards, so the current-note path could never serve them, the 25 replacement cards among them included
    VERIFIED_REPLACEMENT = "verified_replacement"
    DERIVED_IN_CAMPAIGN = "derived_in_campaign"
    DERIVED_IN_CAMPAIGN_REFEREED = "derived_in_campaign_refereed"


# Read-only: a stored claim may carry one of these (campaign landings wrote them straight into the claims table), and
# get_claim must read it with its digest intact; no new revision may declare one (overlay.MathNodeRevision refuses).
LEGACY_READ_ONLY_STATUSES = frozenset({
    RecordStatus.VERIFIED_REPLACEMENT,
    RecordStatus.DERIVED_IN_CAMPAIGN,
    RecordStatus.DERIVED_IN_CAMPAIGN_REFEREED,
})


class ClaimKind(StrEnum):
    CONCEPT = "concept"
    DEFINITION = "definition"
    LEMMA = "lemma"
    THEOREM = "theorem"
    PROOF_ATTEMPT = "proof_attempt"
    CONJECTURE = "conjecture"
    COMPUTATION = "computation"
    HEURISTIC = "heuristic"
    EXAMPLE = "example"
    COUNTEREXAMPLE = "counterexample"


class EvidenceKind(StrEnum):
    DISCOVERY_REPORT = "discovery_report"
    COMPILED = "compiled"
    NUMERIC = "numeric"
    SYMBOLIC = "symbolic"
    EXHAUSTIVE_FINITE = "exhaustive_finite"
    CERTIFICATE_CHECK = "certificate_check"
    HUMAN_PROOF_REVIEW = "human_proof_review"
    LEAN_KERNEL = "lean_kernel"
    FORMALIZATION_FIDELITY = "formalization_fidelity"
    CITATION = "citation"
    INDEPENDENT_REPRODUCTION = "independent_reproduction"
    COUNTEREXAMPLE = "counterexample"


class EvidenceAuthorityRole(StrEnum):
    AUTOMATED_VERIFIER = "automated_verifier"
    HUMAN_REVIEWER = "human_reviewer"
    INDEPENDENT_REPRODUCER = "independent_reproducer"


class EvidenceAuthorityMode(StrEnum):
    LOCAL_AUTOMATED = "local_automated"
    LOCAL_HUMAN_REVIEW = "local_human_review"
    LOCAL_INDEPENDENT_REPRODUCTION = "local_independent_reproduction"


class NodeKind(StrEnum):
    CLAIM = "claim"
    EVIDENCE = "evidence"
    SOURCE = "source"
    ERROR_PATTERN = "error_pattern"
    MISTAKE = "mistake"
    REFLEX = "reflex"
    OPEN_QUESTION = "open_question"
    EXTERNAL_REFERENCE = "external_reference"


class EdgeKind(StrEnum):
    DEPENDS_ON = "depends_on"
    DEFINES = "defines"
    PROVES = "proves"
    REFUTES = "refutes"
    GENERALIZES = "generalizes"
    SPECIALIZES = "specializes"
    ANALOGOUS_TO = "analogous_to"
    CITES = "cites"
    VERIFIED_BY = "verified_by"
    FAILS_UNDER = "fails_under"
    TRIGGERS = "triggers"
    SUPERSEDES = "supersedes"
    SEMANTIC_ASSOCIATION = "semantic_association"
    BRIDGES_TO = "bridges_to"


class EdgePolarity(StrEnum):
    """The epistemic sign of an edge, separate from its typed meaning."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


NEGATIVE_EDGE_KINDS = frozenset(
    {
        EdgeKind.REFUTES,
        EdgeKind.FAILS_UNDER,
        EdgeKind.TRIGGERS,
    }
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Claim:
    statement: str
    kind: ClaimKind
    assumptions: tuple[str, ...] = ()
    domain: str = ""
    status: RecordStatus = RecordStatus.UNVERIFIED
    id: str = field(default_factory=lambda: new_id("claim"))
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("claim statement must not be empty")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported claim schema version: {self.schema_version}")

    def digest(self) -> str:
        return content_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class Evidence:
    claim_id: str
    kind: EvidenceKind
    summary: str
    scope: str
    verifier: str
    passed: bool
    artifact_hash: str | None = None
    limitations: tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("evidence"))
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name, value in (
            ("claim_id", self.claim_id),
            ("summary", self.summary),
            ("scope", self.scope),
            ("verifier", self.verifier),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if type(self.passed) is not bool:
            raise TypeError("evidence result must be a boolean")

    def digest(self) -> str:
        return content_hash(asdict(self))


_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HMAC_SHA256_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_AUTHORITY_ID_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,127}\Z")
_NONCE_RE = re.compile(r"[A-Za-z0-9._:-]{16,256}\Z")

_MODE_FOR_ROLE = {
    EvidenceAuthorityRole.AUTOMATED_VERIFIER: EvidenceAuthorityMode.LOCAL_AUTOMATED,
    EvidenceAuthorityRole.HUMAN_REVIEWER: EvidenceAuthorityMode.LOCAL_HUMAN_REVIEW,
    EvidenceAuthorityRole.INDEPENDENT_REPRODUCER: (
        EvidenceAuthorityMode.LOCAL_INDEPENDENT_REPRODUCTION
    ),
}


@dataclass(frozen=True, slots=True)
class EvidenceAttestation:
    """Typed local/free authority record that must exist behind a signed receipt."""

    evidence_id: str
    claim_id: str
    claim_digest: str
    evidence_digest: str
    evidence_kind: EvidenceKind
    authority_key_id: str
    authority_role: EvidenceAuthorityRole
    authority_mode: EvidenceAuthorityMode
    nonce: str
    artifact_hash: str
    request_digest: str
    result_digest: str
    verifier_digest: str
    toolchain_digest: str
    source_digest: str
    environment_digest: str
    passed: bool
    marginal_cost_microusd: int = 0
    network_used: bool = False
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name, value in (
            ("evidence_id", self.evidence_id),
            ("claim_id", self.claim_id),
            ("created_at", self.created_at),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"evidence attestation {name} is required")
        if not isinstance(self.evidence_kind, EvidenceKind):
            raise TypeError("evidence attestation kind must be an EvidenceKind")
        if not isinstance(self.authority_role, EvidenceAuthorityRole):
            raise TypeError("evidence attestation role must be an EvidenceAuthorityRole")
        if not isinstance(self.authority_mode, EvidenceAuthorityMode):
            raise TypeError("evidence attestation mode must be an EvidenceAuthorityMode")
        if _MODE_FOR_ROLE[self.authority_role] is not self.authority_mode:
            raise ValueError("evidence attestation mode does not match its authority role")
        if not _AUTHORITY_ID_RE.fullmatch(self.authority_key_id):
            raise ValueError("evidence attestation authority key id must be normalized")
        if not _NONCE_RE.fullmatch(self.nonce):
            raise ValueError("evidence attestation nonce must be an opaque bounded token")
        for name, value in (
            ("claim_digest", self.claim_digest),
            ("evidence_digest", self.evidence_digest),
            ("artifact_hash", self.artifact_hash),
            ("request_digest", self.request_digest),
            ("result_digest", self.result_digest),
            ("verifier_digest", self.verifier_digest),
            ("toolchain_digest", self.toolchain_digest),
            ("source_digest", self.source_digest),
            ("environment_digest", self.environment_digest),
        ):
            if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
                raise ValueError(f"evidence attestation {name} must be a SHA-256 digest")
        if type(self.passed) is not bool:
            raise TypeError("evidence attestation result must be a boolean")
        if type(self.network_used) is not bool:
            raise TypeError("evidence attestation network flag must be a boolean")
        if self.network_used:
            raise ValueError("promotion authority must be produced without network access")
        if (
            not isinstance(self.marginal_cost_microusd, int)
            or isinstance(self.marginal_cost_microusd, bool)
            or self.marginal_cost_microusd != 0
        ):
            raise ValueError("promotion authority must have zero marginal API cost")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported evidence attestation schema: {self.schema_version}"
            )

    def digest(self) -> str:
        return content_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    """Authenticated binding between an evidence row and its verifying runtime."""

    evidence_id: str
    claim_id: str
    claim_digest: str
    evidence_digest: str
    attestation_digest: str
    evidence_kind: EvidenceKind
    authority_key_id: str
    authority_role: EvidenceAuthorityRole
    nonce: str
    artifact_hash: str
    verifier_digest: str
    toolchain_digest: str
    source_digest: str
    mac: str
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name, value in (
            ("evidence_id", self.evidence_id),
            ("claim_id", self.claim_id),
            ("created_at", self.created_at),
        ):
            if not value.strip():
                raise ValueError(f"evidence receipt {name} is required")
        if not isinstance(self.evidence_kind, EvidenceKind):
            raise TypeError("evidence receipt kind must be an EvidenceKind")
        if not isinstance(self.authority_role, EvidenceAuthorityRole):
            raise TypeError("evidence receipt role must be an EvidenceAuthorityRole")
        if not _AUTHORITY_ID_RE.fullmatch(self.authority_key_id):
            raise ValueError("evidence receipt authority key id must be normalized")
        if not _NONCE_RE.fullmatch(self.nonce):
            raise ValueError("evidence receipt nonce must be an opaque bounded token")
        for name, value in (
            ("claim_digest", self.claim_digest),
            ("evidence_digest", self.evidence_digest),
            ("attestation_digest", self.attestation_digest),
            ("artifact_hash", self.artifact_hash),
            ("verifier_digest", self.verifier_digest),
            ("toolchain_digest", self.toolchain_digest),
            ("source_digest", self.source_digest),
        ):
            if not _SHA256_RE.fullmatch(value):
                raise ValueError(f"evidence receipt {name} must be a SHA-256 digest")
        if not _HMAC_SHA256_RE.fullmatch(self.mac):
            raise ValueError("evidence receipt MAC must be HMAC-SHA-256")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported evidence receipt schema: {self.schema_version}")

    def authorization_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "claim_id": self.claim_id,
            "claim_digest": self.claim_digest,
            "evidence_digest": self.evidence_digest,
            "attestation_digest": self.attestation_digest,
            "evidence_kind": self.evidence_kind.value,
            "authority_key_id": self.authority_key_id,
            "authority_role": self.authority_role.value,
            "nonce": self.nonce,
            "artifact_hash": self.artifact_hash,
            "verifier_digest": self.verifier_digest,
            "toolchain_digest": self.toolchain_digest,
            "source_digest": self.source_digest,
            "created_at": self.created_at,
        }

    def digest(self) -> str:
        return content_hash({**self.authorization_payload(), "mac": self.mac})


@dataclass(frozen=True, slots=True)
class VerificationEvent:
    claim_id: str
    from_status: RecordStatus
    to_status: RecordStatus
    evidence_ids: tuple[str, ...]
    rationale: str
    reviewer: str
    id: str = field(default_factory=lambda: new_id("verification"))
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.rationale.strip() or not self.reviewer.strip():
            raise ValueError("rationale and reviewer are required")
        if self.to_status is RecordStatus.VERIFIED and not self.evidence_ids:
            raise ValueError("verified promotion requires evidence")

    def digest(self) -> str:
        return content_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    kind: NodeKind
    title: str
    body: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.title.strip():
            raise ValueError("graph node id and title are required")

    def digest(self) -> str:
        return content_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source_id: str
    target_id: str
    kind: EdgeKind
    rationale: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("edge"))
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)
    polarity: EdgePolarity | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.target_id.strip():
            raise ValueError("edge endpoints are required")
        if self.source_id == self.target_id:
            raise ValueError("self edges are not allowed")
        if not self.rationale.strip():
            raise ValueError("edge rationale is required")
        if not 0 < self.weight <= 1:
            raise ValueError("edge weight must be in (0, 1]")
        expected_polarity = (
            EdgePolarity.NEGATIVE
            if self.kind in NEGATIVE_EDGE_KINDS
            else EdgePolarity.POSITIVE
        )
        if self.polarity is None:
            object.__setattr__(self, "polarity", expected_polarity)
        elif self.polarity is not expected_polarity:
            raise ValueError(
                f"{self.kind.value} edges must have {expected_polarity.value} polarity"
            )

    @property
    def signed_weight(self) -> float:
        """Return a negative weight for warning/refutation edges."""

        sign = -1.0 if self.polarity is EdgePolarity.NEGATIVE else 1.0
        return sign * self.weight

    @property
    def is_warning(self) -> bool:
        return self.polarity is EdgePolarity.NEGATIVE

    def digest(self) -> str:
        payload = asdict(self)
        # Polarity is a validated projection of ``kind``. Excluding the redundant
        # field preserves hashes written by schema-version-1 stores.
        payload.pop("polarity")
        return content_hash(payload)
