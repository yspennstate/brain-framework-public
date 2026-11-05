"""Scope-aware status promotion for mathematical claims."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .models import (
    Claim,
    ClaimKind,
    Evidence,
    EvidenceAttestation,
    EvidenceAuthorityMode,
    EvidenceAuthorityRole,
    EvidenceKind,
    EvidenceReceipt,
    RecordStatus,
    VerificationEvent,
    canonical_json,
)
from .store import ResearchStore

_AUTHORITY_ID_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,127}\Z")
_ROLE_FOR_KIND = {
    EvidenceKind.COMPILED: EvidenceAuthorityRole.AUTOMATED_VERIFIER,
    EvidenceKind.NUMERIC: EvidenceAuthorityRole.AUTOMATED_VERIFIER,
    EvidenceKind.SYMBOLIC: EvidenceAuthorityRole.AUTOMATED_VERIFIER,
    EvidenceKind.EXHAUSTIVE_FINITE: EvidenceAuthorityRole.AUTOMATED_VERIFIER,
    EvidenceKind.CERTIFICATE_CHECK: EvidenceAuthorityRole.AUTOMATED_VERIFIER,
    EvidenceKind.LEAN_KERNEL: EvidenceAuthorityRole.AUTOMATED_VERIFIER,
    EvidenceKind.COUNTEREXAMPLE: EvidenceAuthorityRole.AUTOMATED_VERIFIER,
    EvidenceKind.HUMAN_PROOF_REVIEW: EvidenceAuthorityRole.HUMAN_REVIEWER,
    EvidenceKind.FORMALIZATION_FIDELITY: EvidenceAuthorityRole.HUMAN_REVIEWER,
    EvidenceKind.CITATION: EvidenceAuthorityRole.HUMAN_REVIEWER,
    EvidenceKind.INDEPENDENT_REPRODUCTION: (
        EvidenceAuthorityRole.INDEPENDENT_REPRODUCER
    ),
}
_MODE_FOR_ROLE = {
    EvidenceAuthorityRole.AUTOMATED_VERIFIER: EvidenceAuthorityMode.LOCAL_AUTOMATED,
    EvidenceAuthorityRole.HUMAN_REVIEWER: EvidenceAuthorityMode.LOCAL_HUMAN_REVIEW,
    EvidenceAuthorityRole.INDEPENDENT_REPRODUCER: (
        EvidenceAuthorityMode.LOCAL_INDEPENDENT_REPRODUCTION
    ),
}


@dataclass(frozen=True, slots=True, repr=False)
class EvidenceKeyPolicy:
    key: bytes = field(repr=False)
    role: EvidenceAuthorityRole
    allowed_kinds: frozenset[EvidenceKind]

    def __post_init__(self) -> None:
        if not isinstance(self.key, bytes) or len(self.key) < 32:
            raise ValueError("evidence authority key must contain at least 32 bytes")
        if not isinstance(self.role, EvidenceAuthorityRole):
            raise TypeError("evidence authority role must be an EvidenceAuthorityRole")
        if not isinstance(self.allowed_kinds, frozenset) or not self.allowed_kinds:
            raise ValueError("evidence authority requires immutable allowed kinds")
        if any(not isinstance(kind, EvidenceKind) for kind in self.allowed_kinds):
            raise TypeError("evidence authority kinds must be EvidenceKind values")
        if any(kind not in _ROLE_FOR_KIND for kind in self.allowed_kinds):
            raise ValueError("discovery-only evidence cannot be an authority kind")
        if any(_ROLE_FOR_KIND[kind] is not self.role for kind in self.allowed_kinds):
            raise ValueError("evidence authority role does not match an allowed kind")


def evidence_authorization_mac(receipt: EvidenceReceipt, key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("evidence authorization key must contain at least 32 bytes")
    payload = canonical_json(receipt.authorization_payload()).encode("utf-8")
    return "hmac-sha256:" + hmac.new(key, payload, hashlib.sha256).hexdigest()


class EvidenceVerifier:
    """Verify receipts against runtime-owned keys and fixed authority roles."""

    __slots__ = ("_policies",)

    def __init__(self, policies: Mapping[str, EvidenceKeyPolicy]) -> None:
        if not policies:
            raise ValueError("evidence verifier requires trusted key policies")
        copied: dict[str, EvidenceKeyPolicy] = {}
        key_fingerprints: set[bytes] = set()
        for key_id, policy in policies.items():
            if not _AUTHORITY_ID_RE.fullmatch(key_id):
                raise ValueError("evidence authority key id must be normalized")
            if not isinstance(policy, EvidenceKeyPolicy):
                raise TypeError("evidence verifier values must be EvidenceKeyPolicy")
            fingerprint = hashlib.sha256(policy.key).digest()
            if fingerprint in key_fingerprints:
                raise ValueError("evidence authorities must use distinct key material")
            key_fingerprints.add(fingerprint)
            copied[key_id] = policy
        self._policies = copied

    def verify(
        self,
        claim: Claim,
        evidence: Evidence,
        attestation: EvidenceAttestation,
        receipt: EvidenceReceipt,
    ) -> bool:
        if not isinstance(claim, Claim) or not isinstance(evidence, Evidence):
            return False
        if not isinstance(attestation, EvidenceAttestation) or not isinstance(
            receipt, EvidenceReceipt
        ):
            return False
        policy = self._policies.get(receipt.authority_key_id)
        if (
            policy is None
            or evidence.artifact_hash is None
            or not isinstance(evidence.kind, EvidenceKind)
            or evidence.kind not in _ROLE_FOR_KIND
        ):
            return False
        if (
            evidence.claim_id != claim.id
            or attestation.evidence_id != evidence.id
            or attestation.claim_id != claim.id
            or attestation.claim_digest != claim.digest()
            or attestation.evidence_digest != evidence.digest()
            or attestation.evidence_kind is not evidence.kind
            or attestation.authority_key_id != receipt.authority_key_id
            or attestation.authority_role is not policy.role
            or attestation.authority_mode is not _MODE_FOR_ROLE[policy.role]
            or attestation.nonce != receipt.nonce
            or attestation.artifact_hash != evidence.artifact_hash
            or attestation.verifier_digest != receipt.verifier_digest
            or attestation.toolchain_digest != receipt.toolchain_digest
            or attestation.source_digest != receipt.source_digest
            or attestation.passed is not evidence.passed
            or attestation.network_used
            or attestation.marginal_cost_microusd != 0
            or receipt.evidence_id != evidence.id
            or receipt.claim_id != evidence.claim_id
            or receipt.claim_digest != claim.digest()
            or receipt.evidence_digest != evidence.digest()
            or receipt.attestation_digest != attestation.digest()
            or receipt.evidence_kind is not evidence.kind
            or receipt.artifact_hash != evidence.artifact_hash
            or receipt.authority_role is not policy.role
            or evidence.kind not in policy.allowed_kinds
            or _ROLE_FOR_KIND[evidence.kind] is not policy.role
        ):
            return False
        expected = evidence_authorization_mac(receipt, policy.key)
        return hmac.compare_digest(expected, receipt.mac)


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    allowed: bool
    target: RecordStatus
    rationale: str
    supporting_evidence_ids: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()


def _passed_kinds(evidence: Iterable[Evidence]) -> dict[EvidenceKind, list[Evidence]]:
    result: dict[EvidenceKind, list[Evidence]] = {}
    for item in evidence:
        if item.passed:
            result.setdefault(item.kind, []).append(item)
    return result


_LEAN_ROUTE = (EvidenceKind.LEAN_KERNEL, EvidenceKind.FORMALIZATION_FIDELITY)
_DECLARATIONS_PREFIX = "formal declarations: "

FormalLineage = tuple[str, str, frozenset[str]]


def formal_lineage(evidence: Evidence, receipt: EvidenceReceipt) -> FormalLineage:
    """The formal generation a Lean-route record speaks for: the source tree and toolchain its authenticated receipt
    is bound to, and the declarations its evidence names (both Lean authorities write them as
    "formal declarations: a, b"; any other scope counts as one opaque name).

    A Lean kernel record and a fidelity record make one VERIFIED route only when they share this; until 30 Sep 2026
    the route took the first record of each kind, so a kernel run on one source tree and a review of another could
    promote a claim together (account04/B11, B11-R79-CROSS-GENERATION-FORMAL-EVIDENCE-PROMOTES-20260929-01). The
    lakefile and the exact Lean result are not in the receipts and are not joined."""

    scope = evidence.scope
    if scope.startswith(_DECLARATIONS_PREFIX):
        declarations = frozenset(
            part.strip() for part in scope[len(_DECLARATIONS_PREFIX) :].split(",")
        )
    else:
        declarations = frozenset((scope,))
    return (receipt.source_digest, receipt.toolchain_digest, declarations)


def _lineage_map(
    evidence: Iterable[Evidence], receipts: Mapping[str, EvidenceReceipt]
) -> dict[str, FormalLineage]:
    return {
        item.id: formal_lineage(item, receipts[item.id])
        for item in evidence
        if item.kind in _LEAN_ROUTE and item.id in receipts
    }


def _joined_lean_pair(
    kinds: Mapping[EvidenceKind, list[Evidence]],
    lineage: Mapping[str, FormalLineage] | None,
) -> tuple[Evidence, Evidence] | None:
    """The first kernel record, in evidence order, with a fidelity record of the same formal lineage."""

    if not lineage:
        return None
    for kernel in kinds[EvidenceKind.LEAN_KERNEL]:
        key = lineage.get(kernel.id)
        if key is None:
            continue
        for fidelity in kinds[EvidenceKind.FORMALIZATION_FIDELITY]:
            if lineage.get(fidelity.id) == key:
                return kernel, fidelity
    return None


def decide_promotion(
    claim: Claim,
    evidence: Iterable[Evidence],
    *,
    target: RecordStatus,
    lineage: Mapping[str, FormalLineage] | None = None,
) -> PromotionDecision:
    """Evaluate policy only; this function does not authenticate or persist evidence.

    The Lean route (kernel plus formalization fidelity) needs `lineage`, the formal lineage of each record from its
    authenticated receipt (formal_lineage); without it that route cannot be satisfied."""

    relevant = [item for item in evidence if item.claim_id == claim.id]
    kinds = _passed_kinds(relevant)

    counterexamples = kinds.get(EvidenceKind.COUNTEREXAMPLE, [])
    if target is RecordStatus.REFUTED:
        if counterexamples:
            return PromotionDecision(
                True,
                target,
                "a verified counterexample refutes the claim",
                tuple(item.id for item in counterexamples),
            )
        return PromotionDecision(
            False,
            target,
            "refutation requires a verified counterexample",
            missing=("counterexample",),
        )

    if counterexamples:
        return PromotionDecision(
            False,
            target,
            "claim has a verified counterexample and cannot be promoted",
            tuple(item.id for item in counterexamples),
        )

    passed = [
        item for item in relevant if item.passed and item.kind in _ROLE_FOR_KIND
    ]
    if target is RecordStatus.PROVISIONAL:
        if passed:
            return PromotionDecision(
                True,
                target,
                "at least one scoped check passed; claim remains provisional",
                tuple(item.id for item in passed),
            )
        return PromotionDecision(
            False,
            target,
            "provisional status requires scoped evidence",
            missing=("passed evidence",),
        )

    if target is not RecordStatus.VERIFIED:
        return PromotionDecision(False, target, f"unsupported promotion target: {target.value}")

    if claim.kind is ClaimKind.CONJECTURE:
        return PromotionDecision(
            False,
            target,
            "a proved conjecture must be restated as a theorem before verification",
        )
    if claim.kind is ClaimKind.HEURISTIC:
        return PromotionDecision(
            False,
            target,
            "heuristics may be supported but not promoted to verified theorems",
        )

    required_alternatives: list[tuple[EvidenceKind, ...]]
    if claim.kind in {ClaimKind.THEOREM, ClaimKind.LEMMA}:
        required_alternatives = [
            (EvidenceKind.LEAN_KERNEL, EvidenceKind.FORMALIZATION_FIDELITY),
            (EvidenceKind.HUMAN_PROOF_REVIEW, EvidenceKind.INDEPENDENT_REPRODUCTION),
        ]
    elif claim.kind is ClaimKind.COMPUTATION:
        required_alternatives = [
            (EvidenceKind.NUMERIC, EvidenceKind.INDEPENDENT_REPRODUCTION),
            (EvidenceKind.SYMBOLIC, EvidenceKind.INDEPENDENT_REPRODUCTION),
        ]
    elif claim.kind is ClaimKind.DEFINITION:
        required_alternatives = [(EvidenceKind.FORMALIZATION_FIDELITY,)]
    else:
        required_alternatives = [
            (EvidenceKind.SYMBOLIC, EvidenceKind.INDEPENDENT_REPRODUCTION),
            (EvidenceKind.EXHAUSTIVE_FINITE, EvidenceKind.INDEPENDENT_REPRODUCTION),
        ]

    lean_route_unjoined = False
    for alternative in required_alternatives:
        if not all(kind in kinds for kind in alternative):
            continue
        if alternative == _LEAN_ROUTE:
            pair = _joined_lean_pair(kinds, lineage)
            if pair is None:
                lean_route_unjoined = True
                continue
            supporters = tuple(item.id for item in pair)
        else:
            supporters = tuple(kinds[kind][0].id for kind in alternative)
        return PromotionDecision(
            True,
            target,
            "evidence satisfies one complete verification route",
            supporters,
        )

    missing_options = tuple(
        " + ".join(kind.value for kind in alternative if kind not in kinds)
        or "one formal lineage for " + " + ".join(kind.value for kind in alternative)
        for alternative in required_alternatives
    )
    return PromotionDecision(
        False,
        target,
        "formal evidence lineage mismatch: no Lean kernel and fidelity records share a source tree, "
        "toolchain and declarations"
        if lean_route_unjoined
        else "no complete verification route covers the claim",
        missing=missing_options,
    )


@dataclass(frozen=True, slots=True)
class PromotionApplication:
    decision: PromotionDecision
    event: VerificationEvent | None = None


_ALLOWED_TRANSITIONS = {
    RecordStatus.UNVERIFIED: frozenset(
        {RecordStatus.PROVISIONAL, RecordStatus.VERIFIED, RecordStatus.REFUTED}
    ),
    RecordStatus.PROVISIONAL: frozenset(
        {RecordStatus.VERIFIED, RecordStatus.REFUTED}
    ),
    RecordStatus.VERIFIED: frozenset({RecordStatus.REFUTED}),
}


def register_authenticated_evidence(
    store: ResearchStore,
    evidence: Evidence,
    attestation: EvidenceAttestation,
    receipt: EvidenceReceipt,
    verifier: EvidenceVerifier,
    *,
    link: bool = True,
) -> None:
    """Verify then atomically persist evidence and its authentication receipt."""

    if not isinstance(store, ResearchStore):
        raise TypeError("store must be a ResearchStore")
    if not isinstance(verifier, EvidenceVerifier):
        raise TypeError("verifier must be an EvidenceVerifier")
    claim = store.get_claim(evidence.claim_id)
    if claim is None:
        raise KeyError(evidence.claim_id)
    if not verifier.verify(claim, evidence, attestation, receipt):
        raise ValueError("evidence authorization receipt is invalid")
    store._add_authenticated_evidence(evidence, attestation, receipt, link=link)


def _authenticated_evidence(
    store: ResearchStore,
    claim: Claim,
    verifier: EvidenceVerifier,
) -> tuple[list[Evidence], dict[str, EvidenceReceipt]]:
    evidence_rows = store.evidence_for(claim.id)
    evidence_by_id = {item.id: item for item in evidence_rows}
    attestations = store.evidence_attestations_for(claim.id)
    receipts = store.evidence_receipts_for(claim.id)
    if set(attestations) != set(receipts):
        raise ValueError("persisted evidence authority records are incomplete")
    authenticated: list[Evidence] = []
    for evidence in evidence_rows:
        receipt = receipts.get(evidence.id)
        if receipt is None:
            continue
        attestation = attestations[evidence.id]
        if not verifier.verify(claim, evidence, attestation, receipt):
            raise ValueError("persisted evidence authorization receipt is invalid")
        authenticated.append(evidence)
    if set(receipts) - set(evidence_by_id):
        raise ValueError("persisted evidence receipt has no evidence row")
    return authenticated, receipts


def _validate_transition(from_status: RecordStatus, target: RecordStatus) -> str | None:
    if target not in _ALLOWED_TRANSITIONS.get(from_status, frozenset()):
        return f"unsupported status transition: {from_status.value} -> {target.value}"
    return None


def _recompute_current_status(
    store: ResearchStore,
    claim_id: str,
    verifier: EvidenceVerifier,
) -> RecordStatus:
    claim = store.get_claim(claim_id)
    if claim is None:
        raise KeyError(claim_id)
    if claim.status is not RecordStatus.UNVERIFIED:
        raise ValueError("persisted claim has an invalid caller-authored initial status")
    authenticated, receipts = _authenticated_evidence(store, claim, verifier)
    evidence_by_id = {item.id: item for item in authenticated}
    status = claim.status
    for event in store.verification_events_for(claim_id):
        if event.from_status is not status:
            raise ValueError("persisted verification history has a broken status chain")
        transition_error = _validate_transition(status, event.to_status)
        if transition_error is not None:
            raise ValueError(transition_error)
        try:
            selected = [evidence_by_id[evidence_id] for evidence_id in event.evidence_ids]
        except KeyError as exc:
            raise ValueError(
                "persisted verification event references unauthenticated evidence"
            ) from exc
        decision = decide_promotion(
            claim,
            selected,
            target=event.to_status,
            lineage=_lineage_map(selected, receipts),
        )
        if (
            not decision.allowed
            or decision.supporting_evidence_ids != event.evidence_ids
            or decision.rationale != event.rationale
        ):
            raise ValueError("persisted verification event fails deterministic recomputation")
        if event.to_status is RecordStatus.VERIFIED and len(event.evidence_ids) > 1:
            authorities = {
                receipts[evidence_id].authority_key_id
                for evidence_id in event.evidence_ids
            }
            if len(authorities) != len(event.evidence_ids):
                raise ValueError("verified route does not use independent authorities")
        status = event.to_status
    return status


def recompute_current_status(
    store: ResearchStore,
    claim_id: str,
    verifier: EvidenceVerifier,
) -> RecordStatus:
    """Replay authenticated evidence and every derived event before trusting status."""

    if store.transaction().in_transaction:
        return _recompute_current_status(store, claim_id, verifier)
    store.transaction().execute("BEGIN")
    try:
        return _recompute_current_status(store, claim_id, verifier)
    finally:
        store.transaction().rollback()


def derive_and_record_status(
    store: ResearchStore,
    claim_id: str,
    *,
    target: RecordStatus,
    verifier: EvidenceVerifier,
    reviewer: str,
) -> PromotionApplication:
    """Recompute, decide, and append one status event under a single write lock."""

    if not reviewer.strip():
        raise ValueError("derived status reviewer label is required")
    with store.immediate_transaction():
        claim = store.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        current = _recompute_current_status(store, claim_id, verifier)
        transition_error = _validate_transition(current, target)
        if transition_error is not None:
            return PromotionApplication(
                PromotionDecision(False, target, transition_error)
            )
        authenticated, receipts = _authenticated_evidence(store, claim, verifier)
        decision = decide_promotion(
            claim,
            authenticated,
            target=target,
            lineage=_lineage_map(authenticated, receipts),
        )
        if not decision.allowed:
            return PromotionApplication(decision)
        if target is RecordStatus.VERIFIED and len(decision.supporting_evidence_ids) > 1:
            authorities = {
                receipts[evidence_id].authority_key_id
                for evidence_id in decision.supporting_evidence_ids
            }
            if len(authorities) != len(decision.supporting_evidence_ids):
                return PromotionApplication(
                    PromotionDecision(
                        False,
                        target,
                        "verified route requires independently authenticated authorities",
                        decision.supporting_evidence_ids,
                        missing=("distinct evidence authorities",),
                    )
                )
        event = VerificationEvent(
            claim_id=claim.id,
            from_status=current,
            to_status=target,
            evidence_ids=decision.supporting_evidence_ids,
            rationale=decision.rationale,
            reviewer=reviewer,
        )
        store._add_derived_verification_event(event)
        return PromotionApplication(decision, event)
