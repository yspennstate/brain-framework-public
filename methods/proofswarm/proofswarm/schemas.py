"""Proof-ticket and message schemas for local, append-auditable runs."""
from __future__ import annotations
import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Verdict(str, Enum):
    OPEN = "open"
    LEAN_VERIFIED = "lean_verified"
    CERTIFICATE_VERIFIED = "certificate_verified"
    DISPROVEN = "disproven"
    REFUTED_BY_REFEREE = "refuted_by_referee"
    NEEDS_HUMAN = "needs_human"
    # v2 paper-review verdicts
    LATEX_REVIEWED = "latex_reviewed"        # informal review passes
    LATEX_REFUTED = "latex_refuted"          # informal review found a real issue
    LATEX_OPEN = "latex_open"                # ambiguity, needs human


class Evidence(BaseModel):
    kind: Literal["lean_proof", "numerical_certificate", "informal_argument", "counterexample", "referee_finding"]
    content: str
    sha256: Optional[str] = None
    produced_by: str
    produced_at: str = Field(default_factory=_now)
    cost_usd: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProofTicket(BaseModel):
    """A single claim being attacked. Settled only when verdict is *_VERIFIED or DISPROVEN."""
    ticket_id: str
    problem_id: str
    statement_informal: str
    statement_lean: Optional[str] = None
    assumptions: list[str] = Field(default_factory=list)
    parents: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    verdict: Verdict = Verdict.OPEN
    evidence: list[Evidence] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    cost_usd: float = 0.0
    store_revision: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    def add_evidence(self, ev: Evidence) -> None:
        self.evidence.append(ev)
        self.cost_usd += ev.cost_usd
        self.updated_at = _now()

    def log(self, agent: str, action: str, detail: str = "") -> None:
        self.history.append({"ts": _now(), "agent": agent, "action": action, "detail": detail})
        self.updated_at = _now()


class LeanVerifyRequest(BaseModel):
    statement: str
    proof: str
    imports: list[str] = Field(default_factory=lambda: ["Mathlib"])
    timeout_seconds: int = 120


class LeanVerifyResult(BaseModel):
    ok: bool
    elaborated: bool
    sorry_count: int
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float
    olean_sha256: Optional[str] = None
    source_sha256: Optional[str] = None


def _bound_content_digest(evidence: Evidence) -> str | None:
    """Return an evidence digest only when it exactly binds the stored content."""

    digest = hashlib.sha256(evidence.content.encode("utf-8")).hexdigest()
    if evidence.sha256 != digest or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return None
    return digest


def _has_bound_independent_verification(
    ticket: ProofTicket,
    *,
    subject_kind: str,
    metadata_key: str,
    subject_digest_key: str,
    mechanical_producer: str,
) -> bool:
    """Require exact subject evidence plus a distinct, hash-bound verification record."""

    subject_digests = {
        digest
        for evidence in ticket.evidence
        if evidence.kind == subject_kind
        if (digest := _bound_content_digest(evidence)) is not None
    }
    if not subject_digests:
        return False
    statement_digest = hashlib.sha256(ticket.statement_informal.encode("utf-8")).hexdigest()
    trusted_producers = {"human": "human", "mechanical": mechanical_producer}
    for evidence in ticket.evidence:
        verification = evidence.metadata.get(metadata_key, {})
        record_digest = _bound_content_digest(evidence)
        if not isinstance(verification, dict) or record_digest is None:
            continue
        verifier_kind = verification.get("verifier_kind")
        method = verification.get("verification_method")
        if (
            evidence.kind == "referee_finding"
            and evidence.produced_by == trusted_producers.get(verifier_kind)
            and verification.get("verified") is True
            and verification.get(subject_digest_key) in subject_digests
            and verification.get("verification_record_sha256") == record_digest
            and verification.get("statement_sha256") == statement_digest
            and verification.get("ticket_id") == ticket.ticket_id
            and verification.get("problem_id") == ticket.problem_id
            and isinstance(method, str)
            and bool(method.strip())
        ):
            return True
    return False


def gate_verdict(ticket: ProofTicket, requested: Verdict) -> tuple[Verdict, str]:
    """Prevent a model-authored verdict from promoting unverified evidence."""

    if requested is Verdict.LEAN_VERIFIED:
        kernel_digests: list[str] = []
        for evidence in ticket.evidence:
            verify = evidence.metadata.get("verify", {})
            digest = verify.get("source_sha256")
            if (
                evidence.kind == "lean_proof"
                and verify.get("ok") is True
                and verify.get("elaborated") is True
                and verify.get("sorry_count") == 0
                and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
                and evidence.sha256 == digest
                and hashlib.sha256(evidence.content.encode("utf-8")).hexdigest() == digest
            ):
                kernel_digests.append(digest)
        if not kernel_digests:
            return Verdict.OPEN, "lean_verified denied: no exact-source kernel evidence"

        informal_digest = hashlib.sha256(ticket.statement_informal.encode("utf-8")).hexdigest()
        for evidence in ticket.evidence:
            fidelity = evidence.metadata.get("statement_fidelity", {})
            review_digest = hashlib.sha256(evidence.content.encode("utf-8")).hexdigest()
            if (
                evidence.kind == "referee_finding"
                and evidence.produced_by == "human"
                and fidelity.get("verified") is True
                and fidelity.get("reviewer_kind") == "human"
                and fidelity.get("informal_sha256") == informal_digest
                and fidelity.get("lean_source_sha256") in kernel_digests
                and fidelity.get("review_sha256") == review_digest
                and evidence.sha256 == review_digest
            ):
                return requested, "kernel evidence and bound human fidelity review present"
        return Verdict.NEEDS_HUMAN, (
            "exact Lean source compiled; independent informal-statement fidelity review pending"
        )
    if requested is Verdict.CERTIFICATE_VERIFIED:
        if _has_bound_independent_verification(
            ticket,
            subject_kind="numerical_certificate",
            metadata_key="certificate_verification",
            subject_digest_key="certificate_evidence_sha256",
            mechanical_producer="local_certificate_verifier",
        ):
            return requested, "exact certificate and independent bound verification present"
        return Verdict.OPEN, "certificate_verified denied: no independently bound certificate"
    if requested is Verdict.DISPROVEN:
        if _has_bound_independent_verification(
            ticket,
            subject_kind="counterexample",
            metadata_key="counterexample_verification",
            subject_digest_key="counterexample_evidence_sha256",
            mechanical_producer="local_counterexample_verifier",
        ):
            return requested, "exact counterexample and independent bound verification present"
        return Verdict.OPEN, "disproven denied: no independently bound counterexample"
    return requested, "non-promoting verdict"


class NumericalSearchRequest(BaseModel):
    family: Literal["bernoulli_convolution", "self_similar_ifs", "rational_atomic",
                    "twist_map_fixed_points"]
    parameter_grid: dict[str, list[Any]]
    invariance_tolerance: float = 1e-12
    max_seconds: int = 300


class NumericalSearchResult(BaseModel):
    candidates_checked: int
    counterexamples_found: list[dict[str, Any]]
    sup_norm_violations: list[float]
    certificate_sha256: str
