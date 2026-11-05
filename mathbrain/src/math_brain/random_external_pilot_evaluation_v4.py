"""Authenticated scoring and descriptive analysis for the v4 random-external pilot.

This module is intentionally separate from :mod:`three_arm_evaluation`.  The older
evaluator requires semantic labels for every case and can issue a confirmatory
``method_helped`` decision.  Neither property is true for the post-failure v4 lane:
random MathNet cases have no audited domain/difficulty labels, and the run remains an
engineering proxy even if its preregistered descriptive signal threshold is met.

The statistical unit is one problem/independence cluster.  Every runner, parser, invalid
output, and solver-abstention disposition remains in the ordered first-attempt universe
and scores zero.  The module reports all 24 paired triplets, exact one-sided sign tests,
and deterministic cluster bootstrap intervals without inventing external strata.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .models import canonical_json
from .random_external_scoring_authority_v4 import (
    AttemptOutcomeV4 as AuthenticatedAttemptOutcomeV4,
)
from .random_external_scoring_authority_v4 import (
    AuthenticatedRandomExternalScoreV4,
    AuthenticatedRandomScoreVerifierV4,
)
from .three_arm_evaluation import (
    ContrastSummary,
    DifficultyBand,
    MathDomain,
    ProblemSource,
    SourceSummary,
    StudyArm,
)
from .three_arm_random_external_pilot_v4 import (
    RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4,
    RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4,
    AutomaticRetrievalDispositionV4,
    RandomExternalPilotPlanV4,
    verify_random_external_pilot_plan_v4,
)

RANDOM_EXTERNAL_EVALUATION_SCHEMA_V4 = (
    "org.math-research-brain/random-external-three-arm-evaluation/v4"
)
RANDOM_EXTERNAL_SIGNED_SUMMARY_SCHEMA_V4 = (
    "org.math-research-brain/random-external-three-arm-signed-summary/v4"
)
V4_MAX_SHAM_SPECIFICITY_LOSS = 0.02
V4_EMPTY_RATE_FLOOR = 0.15
V4_EMPTY_RATE_CEILING = 0.85

_DIGEST_PREFIX = "sha256:"
_DIGEST_LENGTH = len(_DIGEST_PREFIX) + 64
_SIGNATURE_PREFIX = "ed25519:"
_SIGNATURE_LENGTH = len(_SIGNATURE_PREFIX) + 128
_RANK_DOMAIN = b"math-brain/random-external-evaluation-v4/rank\x00"


def _sha256(raw: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(raw).hexdigest()


def _digest_json(value: object) -> str:
    return _sha256(canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != _DIGEST_LENGTH
        or not value.startswith(_DIGEST_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be canonical sha256:<lowercase hex>")
    return value


def _signature(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != _SIGNATURE_LENGTH
        or not value.startswith(_SIGNATURE_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[8:])
    ):
        raise ValueError(f"{label} must be canonical ed25519:<lowercase hex>")
    return value


def _text(value: object, label: str, *, maximum: int) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be exact text")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error
    if not value.strip() or len(raw) > maximum:
        raise ValueError(f"{label} is empty or exceeds its byte budget")
    return value


def _seed(value: object, label: str) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise ValueError(f"{label} must contain at least 256 bits")
    return value


def _rank(seed: bytes, role: str, *parts: object) -> bytes:
    material = bytearray(_RANK_DOMAIN)
    for part in (role, *parts):
        encoded = str(part).encode("utf-8")
        material.extend(len(encoded).to_bytes(4, "big"))
        material.extend(encoded)
    return hmac.new(seed, bytes(material), hashlib.sha256).digest()


def _sorted_unique(values: tuple[str, ...], label: str) -> None:
    if type(values) is not tuple or any(type(value) is not str for value in values):
        raise ValueError(f"{label} must be an exact text tuple")
    if tuple(sorted(set(values))) != values:
        raise ValueError(f"{label} must be unique and sorted")


class _ReceiptVerifierV4:
    __slots__ = ("_domain", "_public_bytes")

    def __init__(self, public_key_bytes: bytes, *, domain: str) -> None:
        if type(public_key_bytes) is not bytes or len(public_key_bytes) != 32:
            raise ValueError("Ed25519 public key must contain exactly 32 bytes")
        self._public_bytes = bytes(public_key_bytes)
        self._domain = _text(domain, "v4 receipt domain", maximum=128)

    @property
    def commitment(self) -> str:
        return _sha256(
            b"math-brain/random-external-v4/verification-key\x00"
            + self._domain.encode("ascii")
            + b"\x00"
            + self._public_bytes
        )

    def public_key_bytes(self) -> bytes:
        return self._public_bytes

    def _message(self, record: Mapping[str, Any]) -> bytes:
        return (
            b"math-brain/random-external-v4/receipt\x00"
            + self._domain.encode("ascii")
            + b"\x00"
            + canonical_json(record).encode("utf-8")
        )

    def _verify(self, record: Mapping[str, Any], signature: str) -> None:
        _signature(signature, "v4 authority signature")
        try:
            Ed25519PublicKey.from_public_bytes(self._public_bytes).verify(
                bytes.fromhex(signature.removeprefix(_SIGNATURE_PREFIX)),
                self._message(record),
            )
        except (InvalidSignature, ValueError) as error:
            raise ValueError("v4 Ed25519 receipt signature is invalid") from error


class _ReceiptSignerV4:
    __slots__ = ("_domain", "_private_key", "_verifier")

    verifier_type: type[_ReceiptVerifierV4] = _ReceiptVerifierV4

    def __init__(self, key_material: bytes, *, domain: str) -> None:
        seed = _seed(key_material, "v4 receipt signing key material")
        private_bytes = hashlib.sha256(
            b"math-brain/random-external-v4/signing-key\x00"
            + domain.encode("ascii")
            + b"\x00"
            + seed
        ).digest()
        self._private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
        self._domain = _text(domain, "v4 receipt signer domain", maximum=128)
        self._verifier = self.verifier_type(self._private_key.public_key().public_bytes_raw())

    @property
    def commitment(self) -> str:
        return self._verifier.commitment

    def verifier(self) -> _ReceiptVerifierV4:
        return self._verifier

    def _sign(self, record: Mapping[str, Any]) -> str:
        return _SIGNATURE_PREFIX + self._private_key.sign(self._verifier._message(record)).hex()


# Production evaluation has no legacy scoring type or signer surface.
AttemptOutcomeV4 = AuthenticatedAttemptOutcomeV4


@dataclass(frozen=True, slots=True)
class RandomExternalIntegrityAuditV4:
    """Independent audit over the immutable selector, runner, and scorer evidence."""

    plan_digest: str
    preregistration_digest: str
    result_manifest_digest: str
    case_universe_digest: str
    packet_manifest_digest: str
    runtime_manifest_digest: str
    scorer_manifest_digest: str
    ledger_manifest_digest: str
    report_digest: str
    curated_lane_failure_evidence_digest: str
    external_selection_precommitment_digest: str
    external_selection_precommit_anchor_evidence_digest: str
    external_selection_capability_digest: str
    external_gold_audit_signed_report_digest: str
    internal_selection_precommitment_digest: str
    internal_selection_capability_digest: str
    retrieval_protocol_digest: str
    sham_matching_precommitment_digest: str
    tokenizer_attestation_digest: str
    curated_lane_segregation_verified: bool
    external_precommit_anchor_order_verified: bool
    external_beacon_round_replay_verified: bool
    external_beacon_relay_agreement_verified: bool
    external_beacon_randomness_hash_verified: bool
    external_selection_cold_replay_verified: bool
    external_gold_post_terminal_replay_verified: bool
    internal_selection_cold_replay_verified: bool
    automatic_retrieval_itt_verified: bool
    external_semantic_labels_absent_verified: bool
    packet_matching_verified: bool
    contamination_clear: bool
    brain_snapshot_read_only: bool
    sterile_controls_verified: bool
    ledger_complete: bool
    schedule_integrity_verified: bool
    scorer_integrity_verified: bool
    cluster_independence_verified: bool
    external_beacon_bls_verified: bool
    served_tokenizer_identity_verified: bool
    authority_signature: str

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalIntegrityAuditV4:
            raise ValueError("v4 integrity-audit subclasses are forbidden")
        for label, value in (
            ("audit plan digest", self.plan_digest),
            ("audit preregistration digest", self.preregistration_digest),
            ("audit result manifest digest", self.result_manifest_digest),
            ("audit case universe digest", self.case_universe_digest),
            ("audit packet manifest digest", self.packet_manifest_digest),
            ("audit runtime manifest digest", self.runtime_manifest_digest),
            ("audit scorer manifest digest", self.scorer_manifest_digest),
            ("audit ledger manifest digest", self.ledger_manifest_digest),
            ("audit report digest", self.report_digest),
            ("audit curated failure evidence", self.curated_lane_failure_evidence_digest),
            (
                "audit external selection precommitment",
                self.external_selection_precommitment_digest,
            ),
            (
                "audit external selection precommit anchor evidence",
                self.external_selection_precommit_anchor_evidence_digest,
            ),
            (
                "audit external selection capability",
                self.external_selection_capability_digest,
            ),
            (
                "audit external gold signed report",
                self.external_gold_audit_signed_report_digest,
            ),
            (
                "audit internal selection precommitment",
                self.internal_selection_precommitment_digest,
            ),
            (
                "audit internal selection capability",
                self.internal_selection_capability_digest,
            ),
            ("audit retrieval protocol", self.retrieval_protocol_digest),
            ("audit sham precommitment", self.sham_matching_precommitment_digest),
            ("audit tokenizer attestation", self.tokenizer_attestation_digest),
        ):
            _digest(value, label)
        if any(type(value) is not bool for value in self.all_boolean_values):
            raise ValueError("v4 integrity audit gates must be booleans")
        if self.external_beacon_bls_verified is not False:
            raise ValueError("v4 has no BLS verifier and cannot claim beacon BLS authority")
        _signature(self.authority_signature, "v4 audit authority signature")

    @property
    def core_boolean_values(self) -> tuple[bool, ...]:
        return (
            self.curated_lane_segregation_verified,
            self.external_precommit_anchor_order_verified,
            self.external_beacon_round_replay_verified,
            self.external_beacon_relay_agreement_verified,
            self.external_beacon_randomness_hash_verified,
            self.external_selection_cold_replay_verified,
            self.external_gold_post_terminal_replay_verified,
            self.internal_selection_cold_replay_verified,
            self.automatic_retrieval_itt_verified,
            self.external_semantic_labels_absent_verified,
            self.packet_matching_verified,
            self.contamination_clear,
            self.brain_snapshot_read_only,
            self.sterile_controls_verified,
            self.ledger_complete,
            self.schedule_integrity_verified,
            self.scorer_integrity_verified,
            self.cluster_independence_verified,
        )

    @property
    def all_boolean_values(self) -> tuple[bool, ...]:
        return (
            *self.core_boolean_values,
            self.external_beacon_bls_verified,
            self.served_tokenizer_identity_verified,
        )

    @property
    def passed(self) -> bool:
        """Core engineering validity; BLS/tokenizer attestations are limitations."""

        return all(self.core_boolean_values)

    def unsigned_record(self) -> dict[str, Any]:
        return {
            "automatic_retrieval_itt_verified": self.automatic_retrieval_itt_verified,
            "brain_snapshot_read_only": self.brain_snapshot_read_only,
            "case_universe_digest": self.case_universe_digest,
            "cluster_independence_verified": self.cluster_independence_verified,
            "contamination_clear": self.contamination_clear,
            "curated_lane_failure_evidence_digest": self.curated_lane_failure_evidence_digest,
            "curated_lane_segregation_verified": self.curated_lane_segregation_verified,
            "external_beacon_bls_verified": self.external_beacon_bls_verified,
            "external_beacon_randomness_hash_verified": (
                self.external_beacon_randomness_hash_verified
            ),
            "external_beacon_relay_agreement_verified": (
                self.external_beacon_relay_agreement_verified
            ),
            "external_beacon_round_replay_verified": self.external_beacon_round_replay_verified,
            "external_precommit_anchor_order_verified": (
                self.external_precommit_anchor_order_verified
            ),
            "external_gold_post_terminal_replay_verified": (
                self.external_gold_post_terminal_replay_verified
            ),
            "external_gold_audit_signed_report_digest": (
                self.external_gold_audit_signed_report_digest
            ),
            "external_selection_cold_replay_verified": self.external_selection_cold_replay_verified,
            "external_selection_precommit_anchor_evidence_digest": (
                self.external_selection_precommit_anchor_evidence_digest
            ),
            "external_selection_capability_digest": self.external_selection_capability_digest,
            "external_selection_precommitment_digest": self.external_selection_precommitment_digest,
            "external_semantic_labels_absent_verified": (
                self.external_semantic_labels_absent_verified
            ),
            "internal_selection_cold_replay_verified": self.internal_selection_cold_replay_verified,
            "internal_selection_capability_digest": self.internal_selection_capability_digest,
            "internal_selection_precommitment_digest": self.internal_selection_precommitment_digest,
            "ledger_complete": self.ledger_complete,
            "ledger_manifest_digest": self.ledger_manifest_digest,
            "packet_manifest_digest": self.packet_manifest_digest,
            "packet_matching_verified": self.packet_matching_verified,
            "plan_digest": self.plan_digest,
            "preregistration_digest": self.preregistration_digest,
            "report_digest": self.report_digest,
            "result_manifest_digest": self.result_manifest_digest,
            "retrieval_protocol_digest": self.retrieval_protocol_digest,
            "runtime_manifest_digest": self.runtime_manifest_digest,
            "schedule_integrity_verified": self.schedule_integrity_verified,
            "scorer_integrity_verified": self.scorer_integrity_verified,
            "scorer_manifest_digest": self.scorer_manifest_digest,
            "served_tokenizer_identity_verified": self.served_tokenizer_identity_verified,
            "sham_matching_precommitment_digest": self.sham_matching_precommitment_digest,
            "sterile_controls_verified": self.sterile_controls_verified,
            "tokenizer_attestation_digest": self.tokenizer_attestation_digest,
        }

    def commitment_record(self) -> dict[str, Any]:
        return {**self.unsigned_record(), "authority_signature": self.authority_signature}


class RandomExternalAuditReceiptVerifierV4(_ReceiptVerifierV4):
    __slots__ = ()

    def __init__(self, public_key_bytes: bytes) -> None:
        super().__init__(public_key_bytes, domain="random-external-integrity-audit-v4")

    def verify(
        self,
        plan: RandomExternalPilotPlanV4,
        audit: RandomExternalIntegrityAuditV4,
        *,
        result_manifest_digest: str,
        expected_preregistration_digest: str,
    ) -> None:
        if type(plan) is not RandomExternalPilotPlanV4:
            raise ValueError("v4 audit verification requires the exact plan")
        if type(audit) is not RandomExternalIntegrityAuditV4:
            raise ValueError("v4 audit verification requires the exact audit")
        plan.__post_init__()
        audit.__post_init__()
        if self.commitment != plan.config.base.audit_authority_commitment:
            raise ValueError("v4 audit verifier differs from the precommitment")
        config = plan.config
        if not all(
            (
                audit.plan_digest == plan.digest,
                audit.preregistration_digest == expected_preregistration_digest,
                audit.result_manifest_digest == result_manifest_digest,
                audit.case_universe_digest == plan.case_universe_digest,
                audit.packet_manifest_digest == plan.packet_universe_digest,
                audit.scorer_manifest_digest == config.base.scorer_manifest_digest,
                audit.curated_lane_failure_evidence_digest
                == config.curated_lane_failure_evidence_digest,
                audit.external_selection_precommitment_digest
                == config.external_selection_precommitment_digest,
                audit.external_selection_precommit_anchor_evidence_digest
                == config.external_selection_precommit_anchor_evidence_digest,
                audit.external_selection_capability_digest
                == config.external_selection_capability_digest,
                audit.external_gold_audit_signed_report_digest
                == config.external_gold_audit_signed_report_digest,
                audit.internal_selection_precommitment_digest
                == config.internal_selection_precommitment_digest,
                audit.internal_selection_capability_digest
                == config.internal_selection_capability_digest,
                audit.retrieval_protocol_digest == config.retrieval_protocol_digest,
                audit.sham_matching_precommitment_digest
                == config.sham_matching_precommitment_digest,
                audit.tokenizer_attestation_digest == config.tokenizer_attestation_digest,
            )
        ):
            raise ValueError("v4 audit differs from its sealed evidence universe")
        self._verify(audit.unsigned_record(), audit.authority_signature)


class RandomExternalAuditReceiptSignerV4(_ReceiptSignerV4):
    __slots__ = ()
    verifier_type = RandomExternalAuditReceiptVerifierV4

    def __init__(self, key_material: bytes) -> None:
        super().__init__(key_material, domain="random-external-integrity-audit-v4")

    def verifier(self) -> RandomExternalAuditReceiptVerifierV4:
        verifier = super().verifier()
        assert type(verifier) is RandomExternalAuditReceiptVerifierV4
        return verifier

    def mint(
        self,
        plan: RandomExternalPilotPlanV4,
        *,
        result_manifest_digest: str,
        runtime_manifest_digest: str,
        ledger_manifest_digest: str,
        report_digest: str,
        curated_lane_segregation_verified: bool,
        external_precommit_anchor_order_verified: bool,
        external_beacon_round_replay_verified: bool,
        external_beacon_relay_agreement_verified: bool,
        external_beacon_randomness_hash_verified: bool,
        external_selection_cold_replay_verified: bool,
        external_gold_post_terminal_replay_verified: bool,
        internal_selection_cold_replay_verified: bool,
        automatic_retrieval_itt_verified: bool,
        external_semantic_labels_absent_verified: bool,
        packet_matching_verified: bool,
        contamination_clear: bool,
        brain_snapshot_read_only: bool,
        sterile_controls_verified: bool,
        ledger_complete: bool,
        schedule_integrity_verified: bool,
        scorer_integrity_verified: bool,
        cluster_independence_verified: bool,
        external_beacon_bls_verified: bool,
        served_tokenizer_identity_verified: bool,
    ) -> RandomExternalIntegrityAuditV4:
        if type(plan) is not RandomExternalPilotPlanV4:
            raise ValueError("v4 audit receipt requires the exact plan")
        if self.commitment != plan.config.base.audit_authority_commitment:
            raise ValueError("v4 audit authority differs from the precommitment")
        pending = _SIGNATURE_PREFIX + "0" * 128
        config = plan.config
        draft = RandomExternalIntegrityAuditV4(
            plan_digest=plan.digest,
            preregistration_digest=plan.public_precommitment_digest,
            result_manifest_digest=result_manifest_digest,
            case_universe_digest=plan.case_universe_digest,
            packet_manifest_digest=plan.packet_universe_digest,
            runtime_manifest_digest=runtime_manifest_digest,
            scorer_manifest_digest=config.base.scorer_manifest_digest,
            ledger_manifest_digest=ledger_manifest_digest,
            report_digest=report_digest,
            curated_lane_failure_evidence_digest=config.curated_lane_failure_evidence_digest,
            external_selection_precommitment_digest=config.external_selection_precommitment_digest,
            external_selection_precommit_anchor_evidence_digest=(
                config.external_selection_precommit_anchor_evidence_digest
            ),
            external_selection_capability_digest=config.external_selection_capability_digest,
            external_gold_audit_signed_report_digest=(
                config.external_gold_audit_signed_report_digest
            ),
            internal_selection_precommitment_digest=config.internal_selection_precommitment_digest,
            internal_selection_capability_digest=config.internal_selection_capability_digest,
            retrieval_protocol_digest=config.retrieval_protocol_digest,
            sham_matching_precommitment_digest=config.sham_matching_precommitment_digest,
            tokenizer_attestation_digest=config.tokenizer_attestation_digest,
            curated_lane_segregation_verified=curated_lane_segregation_verified,
            external_precommit_anchor_order_verified=(external_precommit_anchor_order_verified),
            external_beacon_round_replay_verified=external_beacon_round_replay_verified,
            external_beacon_relay_agreement_verified=external_beacon_relay_agreement_verified,
            external_beacon_randomness_hash_verified=(external_beacon_randomness_hash_verified),
            external_selection_cold_replay_verified=external_selection_cold_replay_verified,
            external_gold_post_terminal_replay_verified=external_gold_post_terminal_replay_verified,
            internal_selection_cold_replay_verified=internal_selection_cold_replay_verified,
            automatic_retrieval_itt_verified=automatic_retrieval_itt_verified,
            external_semantic_labels_absent_verified=external_semantic_labels_absent_verified,
            packet_matching_verified=packet_matching_verified,
            contamination_clear=contamination_clear,
            brain_snapshot_read_only=brain_snapshot_read_only,
            sterile_controls_verified=sterile_controls_verified,
            ledger_complete=ledger_complete,
            schedule_integrity_verified=schedule_integrity_verified,
            scorer_integrity_verified=scorer_integrity_verified,
            cluster_independence_verified=cluster_independence_verified,
            external_beacon_bls_verified=external_beacon_bls_verified,
            served_tokenizer_identity_verified=served_tokenizer_identity_verified,
            authority_signature=pending,
        )
        return replace(draft, authority_signature=self._sign(draft.unsigned_record()))


@dataclass(frozen=True, slots=True)
class PairedCaseOutcomeV4:
    case_id: str
    independence_cluster_id: str
    source: ProblemSource
    domain: MathDomain | None
    difficulty: DifficultyBand | None
    retrieval_disposition: AutomaticRetrievalDispositionV4
    brain: AttemptOutcomeV4
    sham: AttemptOutcomeV4
    empty: AttemptOutcomeV4

    def __post_init__(self) -> None:
        if type(self) is not PairedCaseOutcomeV4:
            raise ValueError("v4 paired-outcome subclasses are forbidden")
        _text(self.case_id, "paired case id", maximum=128)
        _text(self.independence_cluster_id, "paired independence cluster", maximum=128)
        if type(self.source) is not ProblemSource:
            raise ValueError("paired source must be exact")
        if self.source is ProblemSource.INTERNAL:
            if type(self.domain) is not MathDomain or type(self.difficulty) is not DifficultyBand:
                raise ValueError("internal paired outcome requires semantic labels")
        elif self.domain is not None or self.difficulty is not None:
            raise ValueError("external paired outcome cannot invent semantic labels")
        if type(self.retrieval_disposition) is not AutomaticRetrievalDispositionV4:
            raise ValueError("paired retrieval disposition must be exact")
        if any(
            type(value) is not AttemptOutcomeV4 for value in (self.brain, self.sham, self.empty)
        ):
            raise ValueError("paired attempt outcomes must be exact")

    def to_dict(self) -> dict[str, Any]:
        return {
            "brain": self.brain.value,
            "case_id": self.case_id,
            "difficulty": None if self.difficulty is None else self.difficulty.value,
            "domain": None if self.domain is None else self.domain.value,
            "empty": self.empty.value,
            "independence_cluster_id": self.independence_cluster_id,
            "retrieval_disposition": self.retrieval_disposition.value,
            "sham": self.sham.value,
            "source": self.source.value,
        }


class PilotInterpretationV4(StrEnum):
    CORE_INTEGRITY_INVALID = "core_integrity_invalid"
    POSITIVE_ENGINEERING_SIGNAL = "positive_engineering_signal"
    NO_POSITIVE_ENGINEERING_SIGNAL = "no_positive_engineering_signal"


@dataclass(frozen=True, slots=True)
class RandomExternalPilotSummaryV4:
    plan_digest: str
    preregistration_digest: str
    result_universe_digest: str
    scoring_authority_commitment: str
    audit_authority_commitment: str
    audit_report_digest: str
    audit_signature: str
    case_count: int
    attempt_count: int
    served_model: str
    effort: str
    brain_rate: float
    sham_rate: float
    empty_rate: float
    brain_minus_sham: ContrastSummary
    brain_minus_empty: ContrastSummary
    sham_minus_empty: ContrastSummary
    source_summaries: tuple[SourceSummary, ...]
    paired_outcomes: tuple[PairedCaseOutcomeV4, ...]
    retrieval_selected_case_count: int
    retrieval_abstained_case_count: int
    core_integrity_failures: tuple[str, ...]
    signal_threshold_failures: tuple[str, ...]
    limitations: tuple[str, ...]
    analysis_valid: bool
    pilot_signal_threshold_met: bool
    interpretation: PilotInterpretationV4
    engineering_proxy_only: bool
    confirmatory_claim_authorized: bool

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalPilotSummaryV4:
            raise ValueError("v4 summary subclasses are forbidden")
        for label, value in (
            ("summary plan digest", self.plan_digest),
            ("summary preregistration digest", self.preregistration_digest),
            ("summary result universe digest", self.result_universe_digest),
            ("summary scoring authority", self.scoring_authority_commitment),
            ("summary audit authority", self.audit_authority_commitment),
            ("summary audit report", self.audit_report_digest),
        ):
            _digest(value, label)
        _signature(self.audit_signature, "summary audit signature")
        if self.case_count != RANDOM_EXTERNAL_PILOT_CASE_COUNT_V4:
            raise ValueError("v4 summary requires exactly 24 cases")
        if self.attempt_count != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4:
            raise ValueError("v4 summary requires exactly 72 attempts")
        _text(self.served_model, "summary served model", maximum=128)
        _text(self.effort, "summary effort", maximum=64)
        for label, value in (
            ("brain rate", self.brain_rate),
            ("sham rate", self.sham_rate),
            ("empty rate", self.empty_rate),
        ):
            if type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{label} must be a finite probability")
        if type(self.source_summaries) is not tuple or tuple(
            item.source for item in self.source_summaries
        ) != tuple(ProblemSource):
            raise ValueError("v4 summary requires canonical internal/external summaries")
        if type(self.paired_outcomes) is not tuple or len(self.paired_outcomes) != self.case_count:
            raise ValueError("v4 summary requires every paired case outcome")
        if any(type(item) is not PairedCaseOutcomeV4 for item in self.paired_outcomes):
            raise ValueError("v4 paired outcomes must be exact records")
        if tuple(item.case_id for item in self.paired_outcomes) != tuple(
            sorted(item.case_id for item in self.paired_outcomes)
        ):
            raise ValueError("v4 paired outcomes must be canonically ordered")
        if (
            len({item.case_id for item in self.paired_outcomes}) != self.case_count
            or len({item.independence_cluster_id for item in self.paired_outcomes})
            != self.case_count
        ):
            raise ValueError("v4 paired outcomes must preserve independent cases")
        if (
            self.retrieval_selected_case_count + self.retrieval_abstained_case_count
            != self.case_count
        ):
            raise ValueError("v4 retrieval counts must cover all cases")
        for values, label in (
            (self.core_integrity_failures, "core integrity failures"),
            (self.signal_threshold_failures, "signal threshold failures"),
            (self.limitations, "limitations"),
        ):
            _sorted_unique(values, label)
        if type(self.analysis_valid) is not bool or self.analysis_valid != (
            not self.core_integrity_failures
        ):
            raise ValueError("v4 analysis validity must equal its core gates")
        expected_signal = self.analysis_valid and not self.signal_threshold_failures
        if type(self.pilot_signal_threshold_met) is not bool or (
            self.pilot_signal_threshold_met != expected_signal
        ):
            raise ValueError("v4 pilot signal must equal its sealed threshold gates")
        expected_interpretation = (
            PilotInterpretationV4.CORE_INTEGRITY_INVALID
            if not self.analysis_valid
            else (
                PilotInterpretationV4.POSITIVE_ENGINEERING_SIGNAL
                if self.pilot_signal_threshold_met
                else PilotInterpretationV4.NO_POSITIVE_ENGINEERING_SIGNAL
            )
        )
        if self.interpretation is not expected_interpretation:
            raise ValueError("v4 interpretation differs from its evidence gates")
        if (
            self.engineering_proxy_only is not True
            or self.confirmatory_claim_authorized is not False
        ):
            raise ValueError("v4 summary cannot overclaim confirmatory authority")

    def _outcome_counts(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for arm in StudyArm:
            counter = Counter(getattr(item, arm.value).value for item in self.paired_outcomes)
            result[arm.value] = {
                outcome.value: counter[outcome.value] for outcome in AttemptOutcomeV4
            }
        return result

    def to_dict(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "analysis_valid": self.analysis_valid,
            "attempt_count": self.attempt_count,
            "audit_authority_commitment": self.audit_authority_commitment,
            "audit_report_digest": self.audit_report_digest,
            "audit_signature": self.audit_signature,
            "brain_minus_empty": self.brain_minus_empty.to_dict(),
            "brain_minus_sham": self.brain_minus_sham.to_dict(),
            "brain_rate": self.brain_rate,
            "case_count": self.case_count,
            "claim_level": "engineering_proxy_descriptive",
            "confirmatory_claim_authorized": self.confirmatory_claim_authorized,
            "core_integrity_failures": list(self.core_integrity_failures),
            "effort": self.effort,
            "empty_rate": self.empty_rate,
            "engineering_proxy_only": self.engineering_proxy_only,
            "interpretation": self.interpretation.value,
            "limitations": list(self.limitations),
            "method_helped_confirmatory_claim": False,
            "outcome_counts_by_arm": self._outcome_counts(),
            "paired_outcomes": [item.to_dict() for item in self.paired_outcomes],
            "pilot_signal_threshold_met": self.pilot_signal_threshold_met,
            "plan_digest": self.plan_digest,
            "preregistration_digest": self.preregistration_digest,
            "result_universe_digest": self.result_universe_digest,
            "retrieval_abstained_case_count": self.retrieval_abstained_case_count,
            "retrieval_selected_case_count": self.retrieval_selected_case_count,
            "schema": RANDOM_EXTERNAL_EVALUATION_SCHEMA_V4,
            "scoring_authority_commitment": self.scoring_authority_commitment,
            "served_model": self.served_model,
            "sham_minus_empty": self.sham_minus_empty.to_dict(),
            "sham_rate": self.sham_rate,
            "signal_threshold_failures": list(self.signal_threshold_failures),
            "source_summaries": [item.to_dict() for item in self.source_summaries],
        }


@dataclass(frozen=True, slots=True)
class SignedRandomExternalPilotSummaryV4:
    summary: RandomExternalPilotSummaryV4
    analysis_authority_commitment: str
    authority_signature: str

    def __post_init__(self) -> None:
        if type(self) is not SignedRandomExternalPilotSummaryV4:
            raise ValueError("v4 signed-summary subclasses are forbidden")
        if type(self.summary) is not RandomExternalPilotSummaryV4:
            raise ValueError("v4 signed summary requires the exact summary")
        self.summary.__post_init__()
        _digest(self.analysis_authority_commitment, "v4 analysis authority")
        _signature(self.authority_signature, "v4 analysis signature")

    def unsigned_record(self) -> dict[str, Any]:
        return {
            "analysis_authority_commitment": self.analysis_authority_commitment,
            "record_type": RANDOM_EXTERNAL_SIGNED_SUMMARY_SCHEMA_V4,
            "summary": self.summary.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned_record(), "authority_signature": self.authority_signature}


def _exact_one_sided_sign_p(deltas: Sequence[int]) -> float:
    nonzero = [value for value in deltas if value]
    if not nonzero:
        return 1.0
    positive = sum(value > 0 for value in nonzero)
    numerator = sum(math.comb(len(nonzero), count) for count in range(positive, len(nonzero) + 1))
    return numerator / (2 ** len(nonzero))


def _bootstrap_interval(
    deltas: Sequence[int], *, seed: bytes, domain: str, repetitions: int
) -> tuple[float, float]:
    if len(deltas) < 2:
        raise ValueError("v4 paired bootstrap requires at least two clusters")
    generator = random.Random(int.from_bytes(_rank(seed, "paired-bootstrap", domain), "big"))
    means = [
        sum(generator.choices(deltas, k=len(deltas))) / len(deltas)
        for _repetition in range(repetitions)
    ]
    means.sort()
    return (
        means[math.floor(0.025 * (repetitions - 1))],
        means[math.ceil(0.975 * (repetitions - 1))],
    )


def _contrast(
    deltas: Sequence[int], *, seed: bytes, domain: str, repetitions: int
) -> ContrastSummary:
    lower, upper = _bootstrap_interval(deltas, seed=seed, domain=domain, repetitions=repetitions)
    counts = Counter(deltas)
    return ContrastSummary(
        mean_delta=sum(deltas) / len(deltas),
        exact_one_sided_p=_exact_one_sided_sign_p(deltas),
        interval_lower=lower,
        interval_upper=upper,
        positive_cases=counts[1],
        tied_cases=counts[0],
        negative_cases=counts[-1],
    )


def _source_summary(
    source: ProblemSource,
    cases: Sequence[PairedCaseOutcomeV4],
    *,
    bootstrap_seed: bytes,
    repetitions: int,
) -> SourceSummary:
    brain = [item.brain.score for item in cases]
    sham = [item.sham.score for item in cases]
    empty = [item.empty.score for item in cases]
    brain_sham = [left - right for left, right in zip(brain, sham, strict=True)]
    brain_empty = [left - right for left, right in zip(brain, empty, strict=True)]
    sham_empty = [left - right for left, right in zip(sham, empty, strict=True)]
    return SourceSummary(
        source=source,
        case_count=len(cases),
        brain_rate=sum(brain) / len(brain),
        sham_rate=sum(sham) / len(sham),
        empty_rate=sum(empty) / len(empty),
        brain_minus_sham=_contrast(
            brain_sham,
            seed=bootstrap_seed,
            domain=f"{source.value}-brain-minus-sham",
            repetitions=repetitions,
        ),
        brain_minus_empty=_contrast(
            brain_empty,
            seed=bootstrap_seed,
            domain=f"{source.value}-brain-minus-empty",
            repetitions=repetitions,
        ),
        sham_minus_empty=_contrast(
            sham_empty,
            seed=bootstrap_seed,
            domain=f"{source.value}-sham-minus-empty",
            repetitions=repetitions,
        ),
    )


def _assert_authenticated_score_campaign_roots_v4(
    attempts: tuple[AuthenticatedRandomExternalScoreV4, ...],
) -> None:
    if (
        type(attempts) is not tuple
        or len(attempts) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        or any(type(item) is not AuthenticatedRandomExternalScoreV4 for item in attempts)
    ):
        raise ValueError("v4 score campaign requires exactly 72 authenticated scores")
    for item in attempts:
        item.__post_init__()
    if tuple(item.ordinal for item in attempts) != tuple(
        range(RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4)
    ) or len({item.task_id for item in attempts}) != len(attempts):
        raise ValueError("v4 score campaign order or task identity is invalid")
    shared_roots = (
        "plan_digest",
        "plan_public_precommitment_digest",
        "task_map_public_precommitment_digest",
        "scoring_precommitment_digest",
        "execution_public_record_digest",
        "ledger_precommitment_digest",
        "closed_attempt_manifest_digest",
        "ledger_manifest_digest",
        "loaded_code_digest",
        "parser_digest",
        "scorer_manifest_digest",
        "runtime_policy_digest",
        "scoring_rule_digest",
    )
    # Model and effort labels are signed row evidence, not shared roots: the frozen
    # analysis contract requires mixed values to survive into explicit core failures.
    if any(len({getattr(item, name) for item in attempts}) != 1 for name in shared_roots):
        raise ValueError("v4 scores do not share one exact precommitted campaign root")


def summarize_random_external_pilot_v4(
    plan: RandomExternalPilotPlanV4,
    attempts: Iterable[AuthenticatedRandomExternalScoreV4],
    *,
    audit: RandomExternalIntegrityAuditV4,
    scoring_verifier: AuthenticatedRandomScoreVerifierV4,
    audit_verifier: RandomExternalAuditReceiptVerifierV4,
    allocation_seed: bytes,
    expected_plan_digest: str,
    expected_preregistration_digest: str,
) -> RandomExternalPilotSummaryV4:
    """Cold-replay the exact 72-slot ITT universe and emit the descriptive result."""

    expected_plan = _digest(expected_plan_digest, "externally expected v4 plan")
    expected_preregistration = _digest(
        expected_preregistration_digest,
        "externally expected v4 preregistration",
    )
    if type(plan) is not RandomExternalPilotPlanV4:
        raise ValueError("v4 summary requires the exact random-external plan")
    verify_random_external_pilot_plan_v4(
        plan,
        allocation_seed=allocation_seed,
        expected_public_precommitment_digest=expected_preregistration,
    )
    if plan.digest != expected_plan:
        raise ValueError("v4 plan differs from the externally expected digest")
    if type(scoring_verifier) is not AuthenticatedRandomScoreVerifierV4:
        raise ValueError("v4 summary requires the exact authenticated-score verifier")
    if type(audit_verifier) is not RandomExternalAuditReceiptVerifierV4:
        raise ValueError("v4 summary requires the exact audit verifier")
    materialized = tuple(attempts)
    if len(materialized) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4 or any(
        type(item) is not AuthenticatedRandomExternalScoreV4 for item in materialized
    ):
        raise ValueError("v4 summary rejects legacy attempts and requires 72 exact scores")
    if tuple(item.task_id for item in materialized) != tuple(
        assignment.task_id for assignment in plan.assignments
    ):
        raise ValueError("v4 summary requires the exact ordered attempt universe")
    if len({item.task_id for item in materialized}) != len(materialized):
        raise ValueError("v4 summary rejects duplicate attempts")
    for attempt in materialized:
        scoring_verifier.verify(plan, attempt)
    _assert_authenticated_score_campaign_roots_v4(materialized)
    started_records = {item.started_record_digest for item in materialized}
    terminal_records = {item.terminal_record_digest for item in materialized}
    if len(started_records) != len(materialized):
        raise ValueError("v4 attempts reuse a STARTED record")
    if len(terminal_records) != len(materialized):
        raise ValueError("v4 attempts reuse a terminal record")
    if not started_records.isdisjoint(terminal_records):
        raise ValueError("v4 STARTED and terminal record universes overlap")
    ledger_manifests = {item.ledger_manifest_digest for item in materialized}
    if len(ledger_manifests) != 1:
        raise ValueError("v4 attempts disagree on the ledger manifest")
    result_digest = _digest_json([attempt.commitment_record() for attempt in materialized])
    audit_verifier.verify(
        plan,
        audit,
        result_manifest_digest=result_digest,
        expected_preregistration_digest=expected_preregistration,
    )
    if audit.ledger_manifest_digest != next(iter(ledger_manifests)):
        raise ValueError("v4 audit differs from the attempt ledger manifest")

    attempt_by_task = {item.task_id: item for item in materialized}
    outcomes: dict[str, dict[StudyArm, AttemptOutcomeV4]] = defaultdict(dict)
    for assignment in plan.assignments:
        outcomes[assignment.case_id][assignment.arm] = attempt_by_task[assignment.task_id].outcome
    if any(set(values) != set(StudyArm) for values in outcomes.values()):
        raise ValueError("v4 every case must retain one complete scored triplet")
    paired = tuple(
        PairedCaseOutcomeV4(
            case_id=case.case_id,
            independence_cluster_id=case.independence_cluster_id,
            source=case.source,
            domain=case.domain,
            difficulty=case.difficulty,
            retrieval_disposition=case.retrieval_disposition,
            brain=outcomes[case.case_id][StudyArm.BRAIN],
            sham=outcomes[case.case_id][StudyArm.SHAM],
            empty=outcomes[case.case_id][StudyArm.EMPTY],
        )
        for case in plan.cases
    )
    brain = [item.brain.score for item in paired]
    sham = [item.sham.score for item in paired]
    empty = [item.empty.score for item in paired]
    brain_sham_deltas = [left - right for left, right in zip(brain, sham, strict=True)]
    brain_empty_deltas = [left - right for left, right in zip(brain, empty, strict=True)]
    sham_empty_deltas = [left - right for left, right in zip(sham, empty, strict=True)]
    bootstrap_seed = bytes.fromhex(plan.schedule_digest.removeprefix(_DIGEST_PREFIX))
    repetitions = plan.config.base.bootstrap_repetitions
    brain_minus_sham = _contrast(
        brain_sham_deltas,
        seed=bootstrap_seed,
        domain="brain-minus-sham",
        repetitions=repetitions,
    )
    brain_minus_empty = _contrast(
        brain_empty_deltas,
        seed=bootstrap_seed,
        domain="brain-minus-empty",
        repetitions=repetitions,
    )
    sham_minus_empty = _contrast(
        sham_empty_deltas,
        seed=bootstrap_seed,
        domain="sham-minus-empty",
        repetitions=repetitions,
    )
    source_summaries = tuple(
        _source_summary(
            source,
            tuple(item for item in paired if item.source is source),
            bootstrap_seed=bootstrap_seed,
            repetitions=repetitions,
        )
        for source in ProblemSource
    )

    models = {item.served_model for item in materialized}
    efforts = {item.effort for item in materialized}
    core_failures: list[str] = []
    if not audit.passed:
        core_failures.append("integrity_audit")
    if not all(item.runtime_eligible for item in materialized):
        core_failures.append("runtime_eligibility")
    if not all(item.started_once_verified for item in materialized):
        core_failures.append("one_start_integrity")
    if any(
        len({getattr(item, field_name) for item in materialized}) != 1
        for field_name in ("executable_digest", "loaded_code_digest", "parser_digest")
    ):
        core_failures.append("runtime_artifact_consistency")
    if len(models) != 1:
        core_failures.append("served_model_consistency")
    if len(efforts) != 1:
        core_failures.append("effort_consistency")
    if not all(item.scorer_blinded for item in materialized):
        core_failures.append("scorer_blindness")

    signal_failures: list[str] = []
    for label, contrast in (
        ("brain_minus_empty", brain_minus_empty),
        ("brain_minus_sham", brain_minus_sham),
    ):
        if contrast.mean_delta < plan.config.base.minimum_effect:
            signal_failures.append(f"{label}_minimum_effect")
        if contrast.exact_one_sided_p > plan.config.base.alpha:
            signal_failures.append(f"{label}_exact_p")
        if contrast.interval_lower <= 0:
            signal_failures.append(f"{label}_interval_lower")
    if sham_minus_empty.mean_delta < -V4_MAX_SHAM_SPECIFICITY_LOSS:
        signal_failures.append("sham_specificity_loss")
    if any(item.brain_minus_empty.mean_delta < 0 for item in source_summaries):
        signal_failures.append("source_brain_minus_empty_regression")
    if any(item.brain_minus_sham.mean_delta < 0 for item in source_summaries):
        signal_failures.append("source_brain_minus_sham_regression")
    empty_rate = sum(empty) / len(empty)
    if not V4_EMPTY_RATE_FLOOR <= empty_rate <= V4_EMPTY_RATE_CEILING:
        signal_failures.append("overall_difficulty")
    if any(
        not V4_EMPTY_RATE_FLOOR <= item.empty_rate <= V4_EMPTY_RATE_CEILING
        for item in source_summaries
    ):
        signal_failures.append("source_difficulty")

    limitations = {
        "confirmatory_claim_not_authorized",
        "engineering_proxy_only",
        "external_semantic_labels_unavailable",
    }
    if not audit.external_beacon_bls_verified:
        limitations.add("external_beacon_bls_unverified")
    if not audit.served_tokenizer_identity_verified:
        limitations.add("served_tokenizer_identity_unverified")
    if not plan.config.served_model_identity_verified or not all(
        item.model_identity_verified for item in materialized
    ):
        limitations.add("served_model_identity_unverified")
    core_tuple = tuple(sorted(set(core_failures)))
    signal_tuple = tuple(sorted(set(signal_failures)))
    analysis_valid = not core_tuple
    signal_met = analysis_valid and not signal_tuple
    interpretation = (
        PilotInterpretationV4.CORE_INTEGRITY_INVALID
        if not analysis_valid
        else (
            PilotInterpretationV4.POSITIVE_ENGINEERING_SIGNAL
            if signal_met
            else PilotInterpretationV4.NO_POSITIVE_ENGINEERING_SIGNAL
        )
    )
    selected_count = sum(
        item.retrieval_disposition is AutomaticRetrievalDispositionV4.SELECTED for item in paired
    )
    return RandomExternalPilotSummaryV4(
        plan_digest=plan.digest,
        preregistration_digest=expected_preregistration,
        result_universe_digest=result_digest,
        scoring_authority_commitment=scoring_verifier.commitment,
        audit_authority_commitment=audit_verifier.commitment,
        audit_report_digest=audit.report_digest,
        audit_signature=audit.authority_signature,
        case_count=len(plan.cases),
        attempt_count=len(materialized),
        served_model=next(iter(models)) if len(models) == 1 else "mixed_or_unverified",
        effort=next(iter(efforts)) if len(efforts) == 1 else "mixed",
        brain_rate=sum(brain) / len(brain),
        sham_rate=sum(sham) / len(sham),
        empty_rate=empty_rate,
        brain_minus_sham=brain_minus_sham,
        brain_minus_empty=brain_minus_empty,
        sham_minus_empty=sham_minus_empty,
        source_summaries=source_summaries,
        paired_outcomes=paired,
        retrieval_selected_case_count=selected_count,
        retrieval_abstained_case_count=len(paired) - selected_count,
        core_integrity_failures=core_tuple,
        signal_threshold_failures=signal_tuple,
        limitations=tuple(sorted(limitations)),
        analysis_valid=analysis_valid,
        pilot_signal_threshold_met=signal_met,
        interpretation=interpretation,
        engineering_proxy_only=True,
        confirmatory_claim_authorized=False,
    )


class RandomExternalAnalysisReceiptVerifierV4(_ReceiptVerifierV4):
    __slots__ = ()

    def __init__(self, public_key_bytes: bytes) -> None:
        super().__init__(public_key_bytes, domain="random-external-summary-v4")

    def verify(
        self,
        plan: RandomExternalPilotPlanV4,
        signed: SignedRandomExternalPilotSummaryV4,
        attempts: Iterable[AuthenticatedRandomExternalScoreV4],
        *,
        audit: RandomExternalIntegrityAuditV4,
        scoring_verifier: AuthenticatedRandomScoreVerifierV4,
        audit_verifier: RandomExternalAuditReceiptVerifierV4,
        allocation_seed: bytes,
        expected_plan_digest: str,
        expected_preregistration_digest: str,
    ) -> None:
        if type(signed) is not SignedRandomExternalPilotSummaryV4:
            raise ValueError("v4 final result must be an exact signed summary")
        signed.__post_init__()
        if self.commitment != plan.config.base.analysis_authority_commitment:
            raise ValueError("v4 analysis verifier differs from the precommitment")
        if signed.analysis_authority_commitment != self.commitment:
            raise ValueError("v4 signed summary names another analysis authority")
        expected = summarize_random_external_pilot_v4(
            plan,
            tuple(attempts),
            audit=audit,
            scoring_verifier=scoring_verifier,
            audit_verifier=audit_verifier,
            allocation_seed=allocation_seed,
            expected_plan_digest=expected_plan_digest,
            expected_preregistration_digest=expected_preregistration_digest,
        )
        if signed.summary != expected:
            raise ValueError("v4 signed summary differs from cold evidence replay")
        self._verify(signed.unsigned_record(), signed.authority_signature)


class RandomExternalAnalysisReceiptSignerV4(_ReceiptSignerV4):
    __slots__ = ()
    verifier_type = RandomExternalAnalysisReceiptVerifierV4

    def __init__(self, key_material: bytes) -> None:
        super().__init__(key_material, domain="random-external-summary-v4")

    def verifier(self) -> RandomExternalAnalysisReceiptVerifierV4:
        verifier = super().verifier()
        assert type(verifier) is RandomExternalAnalysisReceiptVerifierV4
        return verifier

    def analyze_and_sign(
        self,
        plan: RandomExternalPilotPlanV4,
        attempts: Iterable[AuthenticatedRandomExternalScoreV4],
        *,
        audit: RandomExternalIntegrityAuditV4,
        scoring_verifier: AuthenticatedRandomScoreVerifierV4,
        audit_verifier: RandomExternalAuditReceiptVerifierV4,
        allocation_seed: bytes,
        expected_plan_digest: str,
        expected_preregistration_digest: str,
    ) -> SignedRandomExternalPilotSummaryV4:
        if self.commitment != plan.config.base.analysis_authority_commitment:
            raise ValueError("v4 analysis authority differs from the precommitment")
        summary = summarize_random_external_pilot_v4(
            plan,
            tuple(attempts),
            audit=audit,
            scoring_verifier=scoring_verifier,
            audit_verifier=audit_verifier,
            allocation_seed=allocation_seed,
            expected_plan_digest=expected_plan_digest,
            expected_preregistration_digest=expected_preregistration_digest,
        )
        pending = _SIGNATURE_PREFIX + "0" * 128
        draft = SignedRandomExternalPilotSummaryV4(
            summary=summary,
            analysis_authority_commitment=self.commitment,
            authority_signature=pending,
        )
        return replace(draft, authority_signature=self._sign(draft.unsigned_record()))


def random_external_result_manifest_digest_v4(
    attempts: Sequence[AuthenticatedRandomExternalScoreV4],
) -> str:
    """Canonical digest needed by the independent audit signer."""

    if type(attempts) not in {list, tuple} or len(attempts) != (
        RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
    ):
        raise ValueError("v4 result manifest requires exactly 72 attempts")
    if any(type(item) is not AuthenticatedRandomExternalScoreV4 for item in attempts):
        raise ValueError("v4 result manifest rejects legacy and nominal scored attempts")
    materialized = tuple(attempts)
    _assert_authenticated_score_campaign_roots_v4(materialized)
    return _digest_json([item.commitment_record() for item in materialized])


__all__ = [
    "RANDOM_EXTERNAL_EVALUATION_SCHEMA_V4",
    "RANDOM_EXTERNAL_SIGNED_SUMMARY_SCHEMA_V4",
    "AttemptOutcomeV4",
    "AuthenticatedRandomExternalScoreV4",
    "AuthenticatedRandomScoreVerifierV4",
    "PairedCaseOutcomeV4",
    "PilotInterpretationV4",
    "RandomExternalAnalysisReceiptSignerV4",
    "RandomExternalAnalysisReceiptVerifierV4",
    "RandomExternalAuditReceiptSignerV4",
    "RandomExternalAuditReceiptVerifierV4",
    "RandomExternalIntegrityAuditV4",
    "RandomExternalPilotSummaryV4",
    "SignedRandomExternalPilotSummaryV4",
    "random_external_result_manifest_digest_v4",
    "summarize_random_external_pilot_v4",
]
