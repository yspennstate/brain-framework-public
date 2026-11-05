from __future__ import annotations

import hashlib
import hmac
import json
import types
from collections import Counter
from dataclasses import fields, replace
from typing import Any

import pytest

from math_brain import external_blind_gold_audit_v4 as external_gold
from math_brain import internal_pilot_selection_v3 as selection_module
from math_brain import mathnet_body_card_review_v3 as review
from math_brain import mathnet_random_external_lane_v4 as random_lane
from math_brain.canonical_ed25519_v3 import (
    CanonicalEd25519SignerV3,
    CanonicalEd25519VerifierV3,
)
from math_brain.exact_answer_v3 import ExactRationalV3
from math_brain.internal_pilot_author_v3 import (
    InternalAuthorOutputV3,
    InternalAuthorSlotV3,
    build_internal_author_precommitment_v3,
    make_internal_author_public_commitment_v3,
)
from math_brain.internal_pilot_selection_v3 import (
    INTERNAL_AUTHOR_EXECUTION_COLLECTOR_SIGNING_DOMAIN_V3,
    InternalAuditAttemptOpeningV3,
    InternalAuditPrecommitmentEntryV3,
    InternalAuditPrecommitmentV3,
    InternalAuditRoleV3,
    InternalAuthorExecutionEvidenceV3,
    InternalAuthorExecutionSlotEvidenceV3,
    InternalAuthorFailureKindV3,
    InternalAuthorTerminalStateV3,
    InternalBodyAuditDecisionV3,
    InternalBodyAuditUniverseV3,
    InternalBodyLanguageV3,
    InternalDuplicateAuditDecisionV3,
    InternalDuplicateAuditUniverseV3,
    InternalDuplicateVerdictV3,
    InternalExternalSelectedCaseV3,
    InternalExternalSelectionV3,
    InternalGoldAuditDecisionV3,
    InternalGoldAuditUniverseV3,
    InternalGoldStatusV3,
    InternalPilotSelectionStatusV3,
    InternalRelevanceAuditDecisionV3,
    InternalRelevanceAuditUniverseV3,
    InternalRelevanceStatusV3,
    InternalReviewerSlotV3,
    adapt_internal_random_external_selection_v3,
    bind_internal_audit_evidence_v3,
    build_internal_audit_attempt_opening_v3,
    build_internal_audit_input_commitment_v3,
    build_internal_audit_projection_payload_v3,
    build_internal_author_execution_evidence_v3,
    build_internal_author_execution_plan_v3,
    build_internal_author_execution_slot_evidence_v3,
    build_internal_duplicate_edge_universe_v3,
    internal_audit_signing_domain_v3,
    select_internal_pilot_bank_v3,
    verify_internal_external_gold_audit_v3,
    verify_internal_pilot_selection_v3,
    verify_internal_random_external_selection_v3,
)
from math_brain.models import canonical_json
from math_brain.three_arm_evaluation import DifficultyBand, MathDomain

AUTHOR_SEED = bytes(range(32))
AUTHOR_KEY = b"author-commitment-key-v3-0000001"
EVIDENCE_KEY = b"selection-evidence-key-v3-000001"
DUPLICATE_EDGE_KEY = b"duplicate-edge-key-v3-00000000001"


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def domain_digest(value: object, domain: str) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            domain.encode("ascii") + b"\x00" + canonical_json(value).encode("utf-8")
        ).hexdigest()
    )


def hmac_commitment(label: str) -> str:
    return (
        "hmac-sha256:" + hmac.new(EVIDENCE_KEY, label.encode("utf-8"), hashlib.sha256).hexdigest()
    )


def card(index: int) -> str:
    return "pilot-card:" + hashlib.sha256(f"card-{index}".encode()).hexdigest()


def item_id(index: int) -> str:
    return "pilot-curation:" + hashlib.sha256(f"item-{index}".encode()).hexdigest()


def external_case(index: int) -> str:
    return "mathnet-random-v4:" + hashlib.sha256(f"external-{index}".encode()).hexdigest()


def external_problem(index: int) -> str:
    return (
        f"Cold-replayed random MathNet problem {index + 1}: determine the exact integer y "
        f"satisfying y + {index + 3} = {2 * index + 9}."
    )


def external_selected_case(
    selection_index: int,
    source_index: int,
) -> InternalExternalSelectedCaseV3:
    return InternalExternalSelectedCaseV3(
        selection_index=selection_index,
        case_id=external_case(source_index),
        problem_text=external_problem(source_index),
        problem_text_digest="sha256:"
        + hashlib.sha256(external_problem(source_index).encode("utf-8")).hexdigest(),
        source_cluster_digest=digest(f"random source cluster {source_index}"),
        public_selection_digest=digest(f"random public selection {source_index}"),
        candidate_content_digest=digest(f"random candidate content {source_index}"),
        private_gold_commitment=digest(f"random private gold {source_index}"),
        source_row_content_digest=digest(f"random selected row {source_index}"),
    )


def make_projection(monkeypatch: pytest.MonkeyPatch):
    cards = sorted(
        (
            {
                "card_id": card(index),
                "guidance": f"Use exact verification method {index + 1} carefully.",
                "title": f"Method {index + 1}",
            }
            for index in range(review.BODY_CARD_FROZEN_CARD_COUNT_V3)
        ),
        key=lambda value: value["card_id"],
    )
    items = sorted(
        (
            {
                "problem_text": f"Synthetic frozen contest problem {index + 1}.",
                "review_item_id": item_id(index),
            }
            for index in range(review.BODY_CARD_FROZEN_ITEM_COUNT_V3)
        ),
        key=lambda value: value["review_item_id"],
    )
    value = {
        "cards": cards,
        "content_digest": digest("internal selector frozen projection"),
        "engineering_upper_bound_only": True,
        "gold_present": False,
        "items": items,
        "schema": review.BODY_CARD_PROJECTION_SCHEMA_V3,
        "schema_version": review.BODY_CARD_REVIEW_VERSION_V3,
        "source_metadata_present": False,
    }
    raw = (canonical_json(value) + "\n").encode("utf-8")
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_RAW_DIGEST_V3",
        "sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_CONTENT_DIGEST_V3",
        value["content_digest"],
    )
    return review.load_frozen_body_card_projection_v3(raw)


def output_for(slot: InternalAuthorSlotV3, index: int) -> InternalAuthorOutputV3:
    return InternalAuthorOutputV3(
        candidate_id=slot.candidate_id,
        problem_text=(
            f"For internal candidate {slot.candidate_id}, let x be the unique integer "
            f"satisfying x - {index + 1} = 0. Determine the exact value of x."
        ),
        author_answer=ExactRationalV3(index + 1, 1),
        verification=(
            "Move the known integer to the other side of the equation.",
            "Substitution verifies the resulting exact integer.",
        ),
    )


def load_external_selection(
    monkeypatch: pytest.MonkeyPatch,
    cases: tuple[InternalExternalSelectedCaseV3, ...],
) -> tuple[InternalExternalSelectionV3, types.SimpleNamespace]:
    verified_bank_digest = digest("real-module random verified bank")
    source_rows = tuple(
        random_lane.MathNetRandomExternalSelectedCaseV4(
            selection_index=index,
            case_id=random_lane._case_id_v4(
                verified_bank_digest=verified_bank_digest,
                selection_index=index,
                public_selection_digest=case.public_selection_digest,
            ),
            problem_text=case.problem_text,
            problem_text_digest=case.problem_text_digest,
            source_cluster_digest=case.source_cluster_digest,
            public_selection_digest=case.public_selection_digest,
            candidate_content_digest=case.candidate_content_digest,
            private_gold_commitment=case.private_gold_commitment,
        )
        for index, case in enumerate(cases)
    )
    coordinate = hashlib.sha256(
        canonical_json([row.to_dict() for row in source_rows]).encode("utf-8")
    ).hexdigest()
    precommitment_bytes = canonical_json(
        {"coordinate": coordinate, "record": "real-module-random-precommitment-v4"}
    ).encode("utf-8")
    beacon_receipt_bytes = canonical_json(
        {"coordinate": coordinate, "record": "real-module-random-beacon-receipt-v4"}
    ).encode("utf-8")
    precommitment_record_sha256 = "sha256:" + hashlib.sha256(precommitment_bytes).hexdigest()
    beacon_receipt_record_sha256 = "sha256:" + hashlib.sha256(beacon_receipt_bytes).hexdigest()
    selection_digest = random_lane._selection_digest_v4(source_rows)
    projection_digest = random_lane._projection_digest_from_rows_v4(source_rows)
    private_vault_digest = domain_digest(
        [row.to_dict() for row in source_rows],
        "org.math-research-brain/test-real-random-private-vault/v4",
    )
    source_manifest_digest = digest("real-module random source manifest")
    selector_code_manifest_digest = digest("real-module random selector code")
    gold_input = object.__new__(random_lane.MathNetRandomExternalGoldAuditInputV4)
    gold_values: dict[str, object] = {
        "items": tuple(
            random_lane.MathNetRandomExternalGoldAuditItemV4(
                case_id=row.case_id,
                problem_text=row.problem_text,
                problem_text_digest=row.problem_text_digest,
            )
            for row in source_rows
        ),
        "selection_digest": selection_digest,
        "projection_digest": projection_digest,
        "precommitment_record_sha256": precommitment_record_sha256,
        "beacon_receipt_record_sha256": beacon_receipt_record_sha256,
        "source_manifest_digest": source_manifest_digest,
        "selector_code_manifest_digest": selector_code_manifest_digest,
        "verified_bank_digest": verified_bank_digest,
        "private_gold_vault_record_sha256": private_vault_digest,
        "_mint_marker": random_lane._GOLD_INPUT_TOKEN_V4,
    }
    for name, value in gold_values.items():
        object.__setattr__(gold_input, name, value)
    gold_input._validate_fields()
    object.__setattr__(
        gold_input,
        "content_digest",
        domain_digest(
            gold_input._payload(),
            "org.math-research-brain/mathnet-random-external-gold-input/v4",
        ),
    )
    capability = object.__new__(random_lane.ColdReplayMathNetRandomSelectionV4)
    capability_values: dict[str, object] = {
        "selected_rows": source_rows,
        "gold_audit_input": gold_input,
        "selection_digest": selection_digest,
        "precommitment_content_digest": digest("real-module random precommitment content"),
        "precommitment_record_sha256": precommitment_record_sha256,
        "beacon_receipt_content_digest": digest("real-module random beacon receipt content"),
        "beacon_receipt_record_sha256": beacon_receipt_record_sha256,
        "beacon_round_spec_digest": digest("real-module random beacon round spec"),
        "beacon_round_number": 123456,
        "beacon_anchor_payload_sha256": digest("real-module random beacon anchor"),
        "source_manifest_digest": source_manifest_digest,
        "source_runtime_evidence_digest": digest("real-module random source runtime"),
        "selector_code_manifest_digest": selector_code_manifest_digest,
        "verified_bank_digest": verified_bank_digest,
        "verified_bank_public_commitment_digest": digest("real-module random public bank"),
        "selection_seed_commitment": digest("real-module random selection seed"),
        "private_gold_vault_record_sha256": private_vault_digest,
        "confirmatory_source_gate": False,
        "_mint_marker": random_lane._SELECTION_TOKEN_V4,
    }
    for name, value in capability_values.items():
        object.__setattr__(capability, name, value)
    capability._validate_fields()
    object.__setattr__(
        capability,
        "content_digest",
        domain_digest(
            capability._payload(),
            "org.math-research-brain/mathnet-random-external-capability/v4",
        ),
    )
    capability.validate_current()
    source = object()

    def reveal_and_cold_replay_mathnet_random_external_lane_v4(
        observed_precommitment: bytes,
        observed_receipt: bytes,
        *,
        expected_precommitment_record_sha256: str,
        expected_beacon_receipt_record_sha256: str,
        source: object,
    ) -> random_lane.ColdReplayMathNetRandomSelectionV4:
        if (
            observed_precommitment != precommitment_bytes
            or observed_receipt != beacon_receipt_bytes
        ):
            raise ValueError("real-module replay records differ")
        if expected_precommitment_record_sha256 != precommitment_record_sha256:
            raise ValueError("real-module replay precommitment anchor differs")
        if expected_beacon_receipt_record_sha256 != beacon_receipt_record_sha256:
            raise ValueError("real-module replay receipt anchor differs")
        if source is not replay.source:
            raise ValueError("real-module replay source differs")
        return replay.capability

    replay = types.SimpleNamespace(
        capability=capability,
        external_precommitment_bytes=precommitment_bytes,
        external_beacon_receipt_bytes=beacon_receipt_bytes,
        expected_external_precommitment_record_sha256=(precommitment_record_sha256),
        expected_external_beacon_receipt_record_sha256=(beacon_receipt_record_sha256),
        expected_external_random_lane_capability_digest=capability.content_digest,
        external_source=source,
        source=source,
    )
    monkeypatch.setattr(
        random_lane,
        "reveal_and_cold_replay_mathnet_random_external_lane_v4",
        reveal_and_cold_replay_mathnet_random_external_lane_v4,
    )
    return (
        adapt_internal_random_external_selection_v3(
            precommitment_bytes,
            beacon_receipt_bytes,
            expected_precommitment_record_sha256=(capability.precommitment_record_sha256),
            expected_beacon_receipt_record_sha256=(capability.beacon_receipt_record_sha256),
            expected_selection_capability_digest=capability.content_digest,
            source=source,
        ),
        replay,
    )


def adapt_external_replay(replay: types.SimpleNamespace) -> InternalExternalSelectionV3:
    return adapt_internal_random_external_selection_v3(
        replay.external_precommitment_bytes,
        replay.external_beacon_receipt_bytes,
        expected_precommitment_record_sha256=(replay.expected_external_precommitment_record_sha256),
        expected_beacon_receipt_record_sha256=(
            replay.expected_external_beacon_receipt_record_sha256
        ),
        expected_selection_capability_digest=(
            replay.expected_external_random_lane_capability_digest
        ),
        source=replay.external_source,
    )


def verify_external_replay(
    selection: InternalExternalSelectionV3,
    replay: types.SimpleNamespace,
) -> None:
    verify_internal_random_external_selection_v3(
        selection,
        replay.external_precommitment_bytes,
        replay.external_beacon_receipt_bytes,
        expected_precommitment_record_sha256=(replay.expected_external_precommitment_record_sha256),
        expected_beacon_receipt_record_sha256=(
            replay.expected_external_beacon_receipt_record_sha256
        ),
        expected_selection_capability_digest=(
            replay.expected_external_random_lane_capability_digest
        ),
        source=replay.external_source,
    )


def forge_nominal_external_selection(
    selection: InternalExternalSelectionV3,
) -> InternalExternalSelectionV3:
    forged = object.__new__(InternalExternalSelectionV3)
    for data_field in fields(InternalExternalSelectionV3):
        if data_field.name == "_content_digest":
            continue
        value = getattr(selection, data_field.name)
        if data_field.name == "confirmatory_source_gate":
            value = not value
        object.__setattr__(forged, data_field.name, value)
    forged.validate_current()
    return forged


def load_external_gold_report(
    selection: InternalExternalSelectionV3,
    replay: types.SimpleNamespace,
) -> types.SimpleNamespace:
    anchor_evidence_digest = digest("external selection precommit anchor evidence")
    capability = replay.capability
    random_binding = external_gold.adapt_random_external_selection_v4(
        capability,
        capability.gold_audit_input,
    )
    if tuple(
        (case.item_id, case.problem_text, case.problem_text_digest) for case in random_binding.cases
    ) != tuple(
        (case.case_id, case.problem_text, case.problem_text_digest) for case in selection.cases
    ):
        raise AssertionError("real random and internal projections differ")
    evidence_values: dict[str, object] = {
        "selection": random_binding,
        "precommitment_content_digest": capability.precommitment_content_digest,
        "precommitment_record_sha256": capability.precommitment_record_sha256,
        "beacon_receipt_content_digest": capability.beacon_receipt_content_digest,
        "beacon_receipt_record_sha256": capability.beacon_receipt_record_sha256,
        "beacon_round_spec_digest": capability.beacon_round_spec_digest,
        "beacon_round_number": capability.beacon_round_number,
        "beacon_anchor_payload_sha256": capability.beacon_anchor_payload_sha256,
        "external_selection_precommit_anchor_evidence_digest": (anchor_evidence_digest),
        "source_manifest_digest": capability.source_manifest_digest,
        "source_runtime_evidence_digest": capability.source_runtime_evidence_digest,
        "selector_code_manifest_digest": capability.selector_code_manifest_digest,
        "verified_bank_digest": capability.verified_bank_digest,
        "verified_bank_public_commitment_digest": (
            capability.verified_bank_public_commitment_digest
        ),
        "selection_seed_commitment": capability.selection_seed_commitment,
        "confirmatory_source_gate": capability.confirmatory_source_gate,
    }
    draft_evidence = external_gold.RandomExternalColdReplayEvidenceV4(
        **evidence_values,
        content_digest=digest("temporary real random replay evidence"),
    )
    replay_evidence = replace(
        draft_evidence,
        content_digest=domain_digest(
            draft_evidence._payload(),
            "org.math-research-brain/random-external-cold-replay-evidence/v4",
        ),
    )
    replay_evidence.validate_current()
    report_signer = CanonicalEd25519SignerV3(
        hashlib.sha256(b"selector-test-external-gold-report-authority").digest(),
        domain=external_gold.EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
    )
    report = external_gold.ExternalGoldAuditReportV4(external_gold._AUDIT_REPORT_TOKEN_V4)
    admitted = tuple(
        external_gold.ExternalGoldAuditedCaseV4(
            item_id=case.case_id,
            problem_text=case.problem_text,
            source_answer=ExactRationalV3(index + 1, 1),
            gold_commitment=(
                "hmac-sha256:"
                + hmac.new(
                    EVIDENCE_KEY,
                    f"real-external-gold-{index}".encode("ascii"),
                    hashlib.sha256,
                ).hexdigest()
            ),
        )
        for index, case in enumerate(selection.cases)
    )
    verifier = report_signer.verifier()
    report_values: dict[str, object] = {
        "status": external_gold.ExternalGoldAuditStatusV4.FEASIBLE,
        "admitted": admitted,
        "random_replay_evidence": replay_evidence,
        "failure_codes": (),
        "audited_selected_count": 12,
        "admitted_count": 12,
        "selection_digest": random_binding.selection_digest,
        "projection_digest": random_binding.projection_digest,
        "random_lane_capability_digest": random_binding.random_lane_capability_digest,
        "random_gold_audit_input_digest": random_binding.random_gold_audit_input_digest,
        "private_gold_vault_record_sha256": (random_binding.private_gold_vault_record_sha256),
        "signed_pair_digest": digest("real external gold signed pair"),
        "public_precommitment_digest": digest("real external gold public precommitment"),
        "ledger_history_digest": digest("real external gold ledger history"),
        "post_terminal_vault_digest": digest("real external gold post-terminal vault"),
        "selection_commitment": hmac_commitment("real external gold selection commitment"),
        "vault_commitment": hmac_commitment("real external gold vault commitment"),
        "reviewer_a_receipt_digest": digest("real external gold reviewer A receipt"),
        "reviewer_b_receipt_digest": digest("real external gold reviewer B receipt"),
        "result_commitment": hmac_commitment("real external gold result"),
        "report_signing_authority_commitment": verifier.authority_commitment,
        "report_signing_key_fingerprint": verifier.key_fingerprint,
        "report_signing_public_key_hex": verifier.public_key_bytes().hex(),
        "confirmatory_claim_authorized": False,
        "served_model_identity_verified": False,
        "provider_independence_verified": False,
        "epistemic_independence_verified": False,
        "reviewer_origin_verified": False,
        "_mint_marker": external_gold._AUDIT_REPORT_TOKEN_V4,
    }
    for name, value in report_values.items():
        object.__setattr__(report, name, value)
    object.__setattr__(
        report,
        "content_digest",
        domain_digest(
            report._content_payload(),
            "org.math-research-brain/external-gold-audit-report/v4",
        ),
    )
    object.__setattr__(
        report,
        "report_signature",
        report_signer.sign(report._signing_record()),
    )
    signed_report_digest = domain_digest(
        {
            "content_digest": report.content_digest,
            "report_signature": report.report_signature,
            "report_signing_authority_commitment": (report.report_signing_authority_commitment),
            "report_signing_key_fingerprint": (report.report_signing_key_fingerprint),
            "report_signing_public_key_hex": report.report_signing_public_key_hex,
        },
        "org.math-research-brain/signed-external-gold-audit-report/v4",
    )
    object.__setattr__(report, "signed_report_digest", signed_report_digest)
    report.validate_current()
    return types.SimpleNamespace(
        external_gold_audit_report=report,
        expected_external_gold_report_verifier=verifier,
        expected_external_gold_audit_signed_report_digest=signed_report_digest,
        expected_external_selection_precommit_anchor_evidence_digest=(anchor_evidence_digest),
    )


def clone_external_gold_report(
    report: external_gold.ExternalGoldAuditReportV4,
    **overrides: object,
) -> external_gold.ExternalGoldAuditReportV4:
    clone = external_gold.ExternalGoldAuditReportV4(external_gold._AUDIT_REPORT_TOKEN_V4)
    for data_field in fields(external_gold.ExternalGoldAuditReportV4):
        object.__setattr__(
            clone,
            data_field.name,
            overrides.get(data_field.name, getattr(report, data_field.name)),
        )
    return clone


def resign_external_gold_report(
    report: external_gold.ExternalGoldAuditReportV4,
    signer: CanonicalEd25519SignerV3,
) -> external_gold.ExternalGoldAuditReportV4:
    replacement = clone_external_gold_report(report)
    verifier = signer.verifier()
    object.__setattr__(
        replacement,
        "report_signing_authority_commitment",
        verifier.authority_commitment,
    )
    object.__setattr__(
        replacement,
        "report_signing_key_fingerprint",
        verifier.key_fingerprint,
    )
    object.__setattr__(
        replacement,
        "report_signing_public_key_hex",
        verifier.public_key_bytes().hex(),
    )
    object.__setattr__(
        replacement,
        "content_digest",
        domain_digest(
            replacement._content_payload(),
            "org.math-research-brain/external-gold-audit-report/v4",
        ),
    )
    object.__setattr__(
        replacement,
        "report_signature",
        signer.sign(replacement._signing_record()),
    )
    object.__setattr__(
        replacement,
        "signed_report_digest",
        domain_digest(
            {
                "content_digest": replacement.content_digest,
                "report_signature": replacement.report_signature,
                "report_signing_authority_commitment": (
                    replacement.report_signing_authority_commitment
                ),
                "report_signing_key_fingerprint": (replacement.report_signing_key_fingerprint),
                "report_signing_public_key_hex": (replacement.report_signing_public_key_hex),
            },
            "org.math-research-brain/signed-external-gold-audit-report/v4",
        ),
    )
    replacement.validate_current()
    return replacement


def signer_for(
    role: InternalAuditRoleV3,
    slot: InternalReviewerSlotV3,
    *,
    seed_label: str | None = None,
) -> CanonicalEd25519SignerV3:
    label = seed_label or f"{role.value}-{slot.value}"
    seed = hashlib.sha256(("selector-reviewer-" + label).encode()).digest()
    return CanonicalEd25519SignerV3(
        seed,
        domain=internal_audit_signing_domain_v3(role, slot),
    )


def collector_signer(label: str = "default") -> CanonicalEd25519SignerV3:
    return CanonicalEd25519SignerV3(
        hashlib.sha256(("selector-author-execution-collector-" + label).encode()).digest(),
        domain=INTERNAL_AUTHOR_EXECUTION_COLLECTOR_SIGNING_DOMAIN_V3,
    )


def role_prefix(role: InternalAuditRoleV3) -> str:
    return role.value


def projection_payload(
    bundle: dict[str, Any],
    role: InternalAuditRoleV3,
    reviewer_slot: InternalReviewerSlotV3 = InternalReviewerSlotV3.A,
    *,
    audit_input_commitment: str | None = None,
    duplicate_edges: object | None = None,
    duplicate_edge_key: bytes | None = None,
) -> bytes:
    input_commitment = (
        bundle["audit_precommitment"].audit_input_commitment
        if audit_input_commitment is None
        else audit_input_commitment
    )
    return build_internal_audit_projection_payload_v3(
        role,
        reviewer_slot,
        audit_input_commitment=input_commitment,
        author_outputs=bundle["author_outputs"],
        card_projection=bundle["card_projection"],
        external_selection=bundle["external_selection"],
        duplicate_edges=(bundle["duplicate_edges"] if duplicate_edges is None else duplicate_edges),
        external_precommitment_bytes=bundle["external_precommitment_bytes"],
        external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
        external_gold_audit_report=bundle["external_gold_audit_report"],
        expected_external_gold_report_verifier=(bundle["expected_external_gold_report_verifier"]),
        expected_external_gold_audit_signed_report_digest=(
            bundle["expected_external_gold_audit_signed_report_digest"]
        ),
        expected_external_selection_precommit_anchor_evidence_digest=(
            bundle["expected_external_selection_precommit_anchor_evidence_digest"]
        ),
        expected_external_precommitment_record_sha256=(
            bundle["expected_external_precommitment_record_sha256"]
        ),
        expected_external_beacon_receipt_record_sha256=(
            bundle["expected_external_beacon_receipt_record_sha256"]
        ),
        expected_external_random_lane_capability_digest=(
            bundle["expected_external_random_lane_capability_digest"]
        ),
        external_source=bundle["external_source"],
        duplicate_edge_key=(
            bundle["duplicate_edge_key"] if duplicate_edge_key is None else duplicate_edge_key
        ),
    )


def projection_artifact(
    bundle: dict[str, Any],
    role: InternalAuditRoleV3,
    reviewer_slot: InternalReviewerSlotV3 = InternalReviewerSlotV3.A,
    *,
    audit_input_commitment: str | None = None,
) -> bytes:
    input_commitment = (
        bundle["audit_precommitment"].audit_input_commitment
        if audit_input_commitment is None
        else audit_input_commitment
    )
    raw_payload = projection_payload(
        bundle,
        role,
        reviewer_slot,
        audit_input_commitment=input_commitment,
    )
    carries_cards = role is InternalAuditRoleV3.RELEVANCE
    value: dict[str, object] = {
        "audit_input_commitment": input_commitment,
        "author_count": len(bundle["author_outputs"]),
        "card_projection_content_digest": (
            bundle["card_projection"].content_digest if carries_cards else None
        ),
        "card_projection_raw_file_digest": (
            bundle["card_projection"].raw_file_digest if carries_cards else None
        ),
        "duplicate_edge_key_commitment": (
            bundle["duplicate_edges"].edge_key_commitment
            if role is InternalAuditRoleV3.DUPLICATE
            else None
        ),
        "external_binding_digest": (
            bundle["external_selection"].content_digest
            if role is InternalAuditRoleV3.DUPLICATE
            else None
        ),
        "external_gold_audit_signed_report_digest": (
            bundle["expected_external_gold_audit_signed_report_digest"]
        ),
        "external_projection_digest": (
            bundle["external_selection"].projection_digest
            if role is InternalAuditRoleV3.DUPLICATE
            else None
        ),
        "external_random_lane_capability_digest": (
            bundle["external_selection"].random_lane_capability_digest
            if role is InternalAuditRoleV3.DUPLICATE
            else None
        ),
        "external_selection_digest": (
            bundle["external_selection"].selection_digest
            if role is InternalAuditRoleV3.DUPLICATE
            else None
        ),
        "projection_payload_digest": "sha256:" + hashlib.sha256(raw_payload).hexdigest(),
        "reviewer_slot": reviewer_slot.value,
        "role": role.value,
        "schema": "org.math-research-brain/internal-pilot-audit-projection-binding",
        "schema_version": 1,
    }
    return canonical_json(value).encode("utf-8")


def audit_run_id(
    role: InternalAuditRoleV3,
    slot: InternalReviewerSlotV3,
) -> str:
    return (
        "audit-run-v3:"
        + hashlib.sha256(f"audit-run-{role.value}-{slot.value}".encode()).hexdigest()
    )


def response_artifact(
    bundle: dict[str, Any], role: InternalAuditRoleV3, slot: InternalReviewerSlotV3
) -> bytes:
    universe = bundle[f"{role_prefix(role)}_{slot.value}"]
    return canonical_json(universe.to_private_dict()).encode("utf-8")


def attempt_opening(
    bundle: dict[str, Any],
    role: InternalAuditRoleV3,
    slot: InternalReviewerSlotV3,
    *,
    audit_input_commitment: str | None = None,
    projection_override: bytes | None = None,
    projection_payload_override: bytes | None = None,
) -> InternalAuditAttemptOpeningV3:
    raw_projection = (
        projection_artifact(
            bundle,
            role,
            slot,
            audit_input_commitment=audit_input_commitment,
        )
        if projection_override is None
        else projection_override
    )
    raw_payload = (
        projection_payload(
            bundle,
            role,
            slot,
            audit_input_commitment=audit_input_commitment,
        )
        if projection_payload_override is None
        else projection_payload_override
    )
    return build_internal_audit_attempt_opening_v3(
        role,
        slot,
        run_id=audit_run_id(role, slot),
        projection_artifact=raw_projection,
        projection_payload=raw_payload,
        response_artifact=response_artifact(bundle, role, slot),
    )


def rebind_role(
    bundle: dict[str, Any],
    role: InternalAuditRoleV3,
    *,
    seed_overrides: dict[InternalReviewerSlotV3, str] | None = None,
    projection_overrides: dict[InternalReviewerSlotV3, bytes] | None = None,
) -> None:
    prefix = role_prefix(role)
    for slot, suffix in (
        (InternalReviewerSlotV3.A, "a"),
        (InternalReviewerSlotV3.B, "b"),
    ):
        universe = bundle[f"{prefix}_{suffix}"]
        raw_projection = (
            projection_overrides[slot]
            if projection_overrides and slot in projection_overrides
            else projection_artifact(bundle, role, slot)
        )
        raw_payload = projection_payload(bundle, role, slot)
        opening = attempt_opening(
            bundle,
            role,
            slot,
            projection_override=raw_projection,
            projection_payload_override=raw_payload,
        )
        evidence = bind_internal_audit_evidence_v3(
            universe,
            role=role,
            projection_artifact=raw_projection,
            projection_payload=raw_payload,
            attempt_opening=opening,
            audit_precommitment=bundle["audit_precommitment"],
            signer=signer_for(
                role,
                slot,
                seed_label=None if seed_overrides is None else seed_overrides.get(slot),
            ),
            evidence_key=EVIDENCE_KEY,
        )
        bundle[f"{prefix}_{suffix}_evidence"] = evidence


def make_bundle(
    monkeypatch: pytest.MonkeyPatch,
    *,
    omitted: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    frozen_projection = make_projection(monkeypatch)
    precommitment = build_internal_author_precommitment_v3(AUTHOR_SEED)
    slot_index = {slot.candidate_id: index for index, slot in enumerate(precommitment.slots)}
    cell_index = {
        (slot.domain, slot.difficulty): index for index, slot in enumerate(precommitment.slots[::3])
    }
    slots = tuple(
        sorted(
            (slot for slot in precommitment.slots if slot.candidate_id not in omitted),
            key=lambda item: item.candidate_id,
        )
    )
    outputs = tuple(output_for(slot, slot_index[slot.candidate_id]) for slot in slots)
    output_by_id = {output.candidate_id: output for output in outputs}
    commitments = tuple(
        make_internal_author_public_commitment_v3(output, commitment_key=AUTHOR_KEY)
        for output in outputs
    )
    body = tuple(
        InternalBodyAuditDecisionV3(
            candidate_id=slot.candidate_id,
            language=InternalBodyLanguageV3.ENGLISH,
            domain=slot.domain,
            difficulty=slot.difficulty,
            single_exact_scalar=True,
        )
        for slot in slots
    )
    body_b_decisions = tuple(replace(decision) for decision in body)
    gold_a_decisions = tuple(
        InternalGoldAuditDecisionV3(
            candidate_id=slot.candidate_id,
            status=InternalGoldStatusV3.SOLVED,
            answer=output_by_id[slot.candidate_id].author_answer,
            verification="Reviewer A independently solves and substitutes the exact result.",
        )
        for slot in slots
    )
    gold_b_decisions = tuple(
        InternalGoldAuditDecisionV3(
            candidate_id=slot.candidate_id,
            status=InternalGoldStatusV3.SOLVED,
            answer=output_by_id[slot.candidate_id].author_answer,
            verification="Reviewer B independently derives and checks the exact result.",
        )
        for slot in slots
    )
    relevance = tuple(
        InternalRelevanceAuditDecisionV3(
            candidate_id=slot.candidate_id,
            status=InternalRelevanceStatusV3.RELEVANT,
            card_id=card(cell_index[(slot.domain, slot.difficulty)] % 6),
        )
        for slot in slots
    )
    relevance_b_decisions = tuple(replace(decision) for decision in relevance)
    external_selection, random_replay = load_external_selection(
        monkeypatch,
        tuple(external_selected_case(index, index) for index in range(12)),
    )
    gold_report = load_external_gold_report(external_selection, random_replay)
    duplicate_edges = build_internal_duplicate_edge_universe_v3(
        tuple(output.candidate_id for output in outputs),
        external_selection,
        external_precommitment_bytes=random_replay.external_precommitment_bytes,
        external_beacon_receipt_bytes=random_replay.external_beacon_receipt_bytes,
        external_gold_audit_report=gold_report.external_gold_audit_report,
        expected_external_gold_report_verifier=(gold_report.expected_external_gold_report_verifier),
        expected_external_gold_audit_signed_report_digest=(
            gold_report.expected_external_gold_audit_signed_report_digest
        ),
        expected_external_selection_precommit_anchor_evidence_digest=(
            gold_report.expected_external_selection_precommit_anchor_evidence_digest
        ),
        expected_external_precommitment_record_sha256=(
            random_replay.expected_external_precommitment_record_sha256
        ),
        expected_external_beacon_receipt_record_sha256=(
            random_replay.expected_external_beacon_receipt_record_sha256
        ),
        expected_external_random_lane_capability_digest=(
            random_replay.expected_external_random_lane_capability_digest
        ),
        external_source=random_replay.external_source,
        edge_key=DUPLICATE_EDGE_KEY,
    )
    duplicate_decisions = tuple(
        InternalDuplicateAuditDecisionV3(edge_id, InternalDuplicateVerdictV3.DISTINCT)
        for edge_id in sorted(edge.edge_id for edge in duplicate_edges.edges)
    )
    bundle: dict[str, Any] = {
        "precommitment": precommitment,
        "author_outputs": outputs,
        "author_commitments": commitments,
        "author_commitment_key": AUTHOR_KEY,
        "body_a": InternalBodyAuditUniverseV3(InternalReviewerSlotV3.A, body),
        "body_b": InternalBodyAuditUniverseV3(InternalReviewerSlotV3.B, body_b_decisions),
        "gold_a": InternalGoldAuditUniverseV3(InternalReviewerSlotV3.A, gold_a_decisions),
        "gold_b": InternalGoldAuditUniverseV3(InternalReviewerSlotV3.B, gold_b_decisions),
        "relevance_a": InternalRelevanceAuditUniverseV3(InternalReviewerSlotV3.A, relevance),
        "relevance_b": InternalRelevanceAuditUniverseV3(
            InternalReviewerSlotV3.B, relevance_b_decisions
        ),
        "card_projection": frozen_projection,
        "external_selection": external_selection,
        "external_gold_audit_report": gold_report.external_gold_audit_report,
        "expected_external_gold_report_verifier": (
            gold_report.expected_external_gold_report_verifier
        ),
        "expected_external_gold_audit_signed_report_digest": (
            gold_report.expected_external_gold_audit_signed_report_digest
        ),
        "expected_external_selection_precommit_anchor_evidence_digest": (
            gold_report.expected_external_selection_precommit_anchor_evidence_digest
        ),
        "external_precommitment_bytes": random_replay.external_precommitment_bytes,
        "external_beacon_receipt_bytes": random_replay.external_beacon_receipt_bytes,
        "expected_external_precommitment_record_sha256": (
            random_replay.expected_external_precommitment_record_sha256
        ),
        "expected_external_beacon_receipt_record_sha256": (
            random_replay.expected_external_beacon_receipt_record_sha256
        ),
        "external_source": random_replay.external_source,
        "expected_external_selection_digest": external_selection.selection_digest,
        "expected_external_projection_digest": external_selection.projection_digest,
        "expected_external_random_lane_capability_digest": (
            external_selection.random_lane_capability_digest
        ),
        "duplicate_edge_key": DUPLICATE_EDGE_KEY,
        "duplicate_edges": duplicate_edges,
        "duplicate_a": InternalDuplicateAuditUniverseV3(
            InternalReviewerSlotV3.A, duplicate_decisions
        ),
        "duplicate_b": InternalDuplicateAuditUniverseV3(
            InternalReviewerSlotV3.B, duplicate_decisions
        ),
        "evidence_key": EVIDENCE_KEY,
    }
    execution_plan = build_internal_author_execution_plan_v3(
        precommitment,
        run_id="author-run-v3:" + hashlib.sha256(b"selector-author-run").hexdigest(),
    )
    execution_records = tuple(
        build_internal_author_execution_slot_evidence_v3(
            precommitment,
            execution_plan,
            slot_index=index,
            terminal_state=(
                InternalAuthorTerminalStateV3.RETURNED
                if slot.candidate_id in output_by_id
                else InternalAuthorTerminalStateV3.FAILED
            ),
            response_artifact=(
                canonical_json(output_by_id[slot.candidate_id].to_private_dict()).encode("utf-8")
                if slot.candidate_id in output_by_id
                else None
            ),
            failure_kind=(
                None
                if slot.candidate_id in output_by_id
                else InternalAuthorFailureKindV3.RUNNER_ERROR
            ),
        )
        for index, slot in enumerate(precommitment.slots)
    )
    bundle["author_execution"] = build_internal_author_execution_evidence_v3(
        precommitment,
        execution_plan_artifact=execution_plan,
        slot_records=execution_records,
        collector_signer=collector_signer(),
    )
    bundle["expected_author_execution_manifest_digest"] = (
        "sha256:" + hashlib.sha256(bundle["author_execution"].manifest_bytes).hexdigest()
    )
    bundle["expected_author_execution_collector_public_key"] = (
        collector_signer().verifier().public_key_bytes()
    )
    audit_input_commitment = build_internal_audit_input_commitment_v3(
        precommitment,
        author_outputs=outputs,
        author_commitments=commitments,
        card_projection=frozen_projection,
        external_selection=external_selection,
        duplicate_edges=duplicate_edges,
        author_execution=bundle["author_execution"],
        external_precommitment_bytes=bundle["external_precommitment_bytes"],
        external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
        external_gold_audit_report=bundle["external_gold_audit_report"],
        expected_external_gold_report_verifier=(bundle["expected_external_gold_report_verifier"]),
        expected_external_gold_audit_signed_report_digest=(
            bundle["expected_external_gold_audit_signed_report_digest"]
        ),
        expected_external_selection_precommit_anchor_evidence_digest=(
            bundle["expected_external_selection_precommit_anchor_evidence_digest"]
        ),
        expected_external_precommitment_record_sha256=(
            bundle["expected_external_precommitment_record_sha256"]
        ),
        expected_external_beacon_receipt_record_sha256=(
            bundle["expected_external_beacon_receipt_record_sha256"]
        ),
        expected_external_random_lane_capability_digest=(
            bundle["expected_external_random_lane_capability_digest"]
        ),
        external_source=bundle["external_source"],
        duplicate_edge_key=bundle["duplicate_edge_key"],
        expected_author_execution_manifest_digest=(
            bundle["expected_author_execution_manifest_digest"]
        ),
        expected_author_execution_collector_public_key=(
            bundle["expected_author_execution_collector_public_key"]
        ),
        evidence_key=EVIDENCE_KEY,
    )
    bundle["audit_precommitment"] = InternalAuditPrecommitmentV3(
        entries=tuple(
            InternalAuditPrecommitmentEntryV3(
                role=role,
                reviewer_slot=slot,
                reviewer_public_key=signer_for(role, slot).verifier().public_key_bytes(),
                projection_digest=(
                    "sha256:"
                    + hashlib.sha256(
                        projection_artifact(
                            bundle,
                            role,
                            slot,
                            audit_input_commitment=audit_input_commitment,
                        )
                    ).hexdigest()
                ),
                attempt_precommitment_digest=(
                    "sha256:"
                    + hashlib.sha256(
                        attempt_opening(
                            bundle,
                            role,
                            slot,
                            audit_input_commitment=audit_input_commitment,
                        ).attempt_precommitment_artifact
                    ).hexdigest()
                ),
            )
            for role in InternalAuditRoleV3
            for slot in InternalReviewerSlotV3
        ),
        audit_input_commitment=audit_input_commitment,
    )
    bundle["expected_audit_precommitment_digest"] = bundle["audit_precommitment"].content_digest
    for role in InternalAuditRoleV3:
        rebind_role(bundle, role)
    return bundle


def select(bundle: dict[str, Any]):
    return select_internal_pilot_bank_v3(
        bundle["precommitment"],
        **{key: value for key, value in bundle.items() if key != "precommitment"},
    )


def verify(report: object, bundle: dict[str, Any]) -> None:
    verify_internal_pilot_selection_v3(
        report,
        bundle["precommitment"],
        **{key: value for key, value in bundle.items() if key != "precommitment"},
    )


def verify_external_gold_from_bundle(
    bundle: dict[str, Any],
    *,
    report: object | None = None,
    verifier: CanonicalEd25519VerifierV3 | None = None,
    signed_report_digest: str | None = None,
    anchor_evidence_digest: str | None = None,
) -> None:
    verify_internal_external_gold_audit_v3(
        bundle["external_gold_audit_report"] if report is None else report,
        (bundle["expected_external_gold_report_verifier"] if verifier is None else verifier),
        bundle["external_selection"],
        bundle["external_precommitment_bytes"],
        bundle["external_beacon_receipt_bytes"],
        expected_signed_report_digest=(
            bundle["expected_external_gold_audit_signed_report_digest"]
            if signed_report_digest is None
            else signed_report_digest
        ),
        expected_external_selection_precommit_anchor_evidence_digest=(
            bundle["expected_external_selection_precommit_anchor_evidence_digest"]
            if anchor_evidence_digest is None
            else anchor_evidence_digest
        ),
        expected_precommitment_record_sha256=(
            bundle["expected_external_precommitment_record_sha256"]
        ),
        expected_beacon_receipt_record_sha256=(
            bundle["expected_external_beacon_receipt_record_sha256"]
        ),
        expected_selection_capability_digest=(
            bundle["expected_external_random_lane_capability_digest"]
        ),
        source=bundle["external_source"],
    )


def rebuild_audit_input_commitment(
    bundle: dict[str, Any],
    *,
    duplicate_edges: object | None = None,
    duplicate_edge_key: bytes | None = None,
    author_execution: InternalAuthorExecutionEvidenceV3 | None = None,
    expected_manifest_digest: str | None = None,
    expected_collector_public_key: bytes | None = None,
) -> str:
    return build_internal_audit_input_commitment_v3(
        bundle["precommitment"],
        author_outputs=bundle["author_outputs"],
        author_commitments=bundle["author_commitments"],
        card_projection=bundle["card_projection"],
        external_selection=bundle["external_selection"],
        duplicate_edges=(bundle["duplicate_edges"] if duplicate_edges is None else duplicate_edges),
        author_execution=(
            bundle["author_execution"] if author_execution is None else author_execution
        ),
        external_precommitment_bytes=bundle["external_precommitment_bytes"],
        external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
        external_gold_audit_report=bundle["external_gold_audit_report"],
        expected_external_gold_report_verifier=(bundle["expected_external_gold_report_verifier"]),
        expected_external_gold_audit_signed_report_digest=(
            bundle["expected_external_gold_audit_signed_report_digest"]
        ),
        expected_external_selection_precommit_anchor_evidence_digest=(
            bundle["expected_external_selection_precommit_anchor_evidence_digest"]
        ),
        expected_external_precommitment_record_sha256=(
            bundle["expected_external_precommitment_record_sha256"]
        ),
        expected_external_beacon_receipt_record_sha256=(
            bundle["expected_external_beacon_receipt_record_sha256"]
        ),
        expected_external_random_lane_capability_digest=(
            bundle["expected_external_random_lane_capability_digest"]
        ),
        external_source=bundle["external_source"],
        duplicate_edge_key=(
            bundle["duplicate_edge_key"] if duplicate_edge_key is None else duplicate_edge_key
        ),
        expected_author_execution_manifest_digest=(
            bundle["expected_author_execution_manifest_digest"]
            if expected_manifest_digest is None
            else expected_manifest_digest
        ),
        expected_author_execution_collector_public_key=(
            bundle["expected_author_execution_collector_public_key"]
            if expected_collector_public_key is None
            else expected_collector_public_key
        ),
        evidence_key=bundle["evidence_key"],
    )


def rebuild_duplicate_edges(
    bundle: dict[str, Any],
    *,
    edge_key: bytes,
):
    return build_internal_duplicate_edge_universe_v3(
        tuple(output.candidate_id for output in bundle["author_outputs"]),
        bundle["external_selection"],
        external_precommitment_bytes=bundle["external_precommitment_bytes"],
        external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
        external_gold_audit_report=bundle["external_gold_audit_report"],
        expected_external_gold_report_verifier=(bundle["expected_external_gold_report_verifier"]),
        expected_external_gold_audit_signed_report_digest=(
            bundle["expected_external_gold_audit_signed_report_digest"]
        ),
        expected_external_selection_precommit_anchor_evidence_digest=(
            bundle["expected_external_selection_precommit_anchor_evidence_digest"]
        ),
        expected_external_precommitment_record_sha256=(
            bundle["expected_external_precommitment_record_sha256"]
        ),
        expected_external_beacon_receipt_record_sha256=(
            bundle["expected_external_beacon_receipt_record_sha256"]
        ),
        expected_external_random_lane_capability_digest=(
            bundle["expected_external_random_lane_capability_digest"]
        ),
        external_source=bundle["external_source"],
        edge_key=edge_key,
    )


def cell_candidates(bundle: dict[str, Any], cell_index: int) -> tuple[str, ...]:
    domain, difficulty = tuple(
        (slot.domain, slot.difficulty) for slot in bundle["precommitment"].slots[::3]
    )[cell_index]
    return tuple(
        sorted(
            slot.candidate_id
            for slot in bundle["precommitment"].slots
            if slot.domain is domain and slot.difficulty is difficulty
        )
    )


def replace_decisions(universe: Any, replacements: dict[str, Any]):
    return replace(
        universe,
        decisions=tuple(
            replacements.get(decision.candidate_id, decision) for decision in universe.decisions
        ),
    )


def replace_cards(universe: Any, replacements: dict[str, str]):
    return replace(
        universe,
        decisions=tuple(
            replace(decision, card_id=replacements.get(decision.candidate_id, decision.card_id))
            for decision in universe.decisions
        ),
    )


def disable_body_candidates(bundle: dict[str, Any], candidate_ids: set[str]) -> None:
    for key in ("body_a", "body_b"):
        universe = bundle[key]
        bundle[key] = replace_decisions(
            universe,
            {
                candidate_id: replace(
                    next(
                        decision
                        for decision in universe.decisions
                        if decision.candidate_id == candidate_id
                    ),
                    single_exact_scalar=False,
                )
                for candidate_id in candidate_ids
            },
        )
    rebind_role(bundle, InternalAuditRoleV3.BODY)


def duplicate_edge_id(bundle: dict[str, Any], left: str, right: str) -> str:
    for edge in bundle["duplicate_edges"].edges:
        if {edge.left.node_id, edge.right.node_id} == {left, right}:
            return edge.edge_id
    raise AssertionError("edge not found")


def set_duplicate_verdicts(
    bundle: dict[str, Any],
    edge_ids: set[str],
    *,
    reviewer: str = "a",
    verdict: InternalDuplicateVerdictV3 = InternalDuplicateVerdictV3.DUPLICATE,
) -> None:
    key = f"duplicate_{reviewer}"
    universe = bundle[key]
    bundle[key] = replace(
        universe,
        decisions=tuple(
            replace(decision, verdict=verdict) if decision.edge_id in edge_ids else decision
            for decision in universe.decisions
        ),
    )
    rebind_role(bundle, InternalAuditRoleV3.DUPLICATE)


def resign_execution(
    evidence: InternalAuthorExecutionEvidenceV3,
    *,
    execution_plan_artifact: bytes | None = None,
    manifest_bytes: bytes | None = None,
    ledger_snapshot_artifact: bytes | None = None,
    slot_records: tuple[InternalAuthorExecutionSlotEvidenceV3, ...] | None = None,
    signer: CanonicalEd25519SignerV3 | None = None,
) -> InternalAuthorExecutionEvidenceV3:
    current_signer = collector_signer() if signer is None else signer
    unsigned = InternalAuthorExecutionEvidenceV3.__new__(InternalAuthorExecutionEvidenceV3)
    object.__setattr__(
        unsigned,
        "execution_plan_artifact",
        (
            evidence.execution_plan_artifact
            if execution_plan_artifact is None
            else execution_plan_artifact
        ),
    )
    object.__setattr__(
        unsigned,
        "slot_records",
        evidence.slot_records if slot_records is None else slot_records,
    )
    object.__setattr__(
        unsigned,
        "ledger_snapshot_artifact",
        (
            evidence.ledger_snapshot_artifact
            if ledger_snapshot_artifact is None
            else ledger_snapshot_artifact
        ),
    )
    object.__setattr__(
        unsigned,
        "manifest_bytes",
        evidence.manifest_bytes if manifest_bytes is None else manifest_bytes,
    )
    object.__setattr__(
        unsigned,
        "collector_public_key",
        current_signer.verifier().public_key_bytes(),
    )
    object.__setattr__(unsigned, "collector_signature", "ed25519:" + "0" * 128)
    signature = current_signer.sign(unsigned.canonical_record())
    return InternalAuthorExecutionEvidenceV3(
        execution_plan_artifact=unsigned.execution_plan_artifact,
        slot_records=unsigned.slot_records,
        ledger_snapshot_artifact=unsigned.ledger_snapshot_artifact,
        manifest_bytes=unsigned.manifest_bytes,
        collector_public_key=unsigned.collector_public_key,
        collector_signature=signature,
    )


def forge_slot_ledger_digest(
    record: InternalAuthorExecutionSlotEvidenceV3,
    forged_ledger_digest: str,
) -> InternalAuthorExecutionSlotEvidenceV3:
    started = json.loads(record.started_record_artifact)
    started["ledger_digest"] = forged_ledger_digest
    started_raw = canonical_json(started).encode("utf-8")
    started_digest = "sha256:" + hashlib.sha256(started_raw).hexdigest()
    selection_raw: bytes | None = None
    selection_digest: str | None = None
    if record.selection_record_artifact is not None:
        selected = json.loads(record.selection_record_artifact)
        selected["ledger_digest"] = forged_ledger_digest
        selected["started_record_digest"] = started_digest
        selection_raw = canonical_json(selected).encode("utf-8")
        selection_digest = "sha256:" + hashlib.sha256(selection_raw).hexdigest()
    terminal = json.loads(record.terminal_record_artifact)
    terminal["ledger_digest"] = forged_ledger_digest
    terminal["started_record_digest"] = started_digest
    terminal["selection_record_digest"] = selection_digest
    return InternalAuthorExecutionSlotEvidenceV3(
        started_record_artifact=started_raw,
        selection_record_artifact=selection_raw,
        terminal_record_artifact=canonical_json(terminal).encode("utf-8"),
        response_artifact=record.response_artifact,
    )


def test_complete_bank_selects_lexicographic_first_and_cold_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    report = select(bundle)

    assert report.status is InternalPilotSelectionStatusV3.FEASIBLE
    assert report.successful_author_count == 36
    assert report.eligible_candidate_count == 36
    assert report.selected_count == 12
    assert tuple(item.candidate_id for item in report.selected) == tuple(
        cell_candidates(bundle, index)[0] for index in range(12)
    )
    assert max(Counter(item.card_id for item in report.selected).values()) == 2
    verify(report, bundle)
    assert report == select(bundle)


def test_public_projection_is_hmac_only_and_hides_every_private_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    report = select(bundle)
    public = report.to_public_dict()
    encoded = canonical_json(public)

    assert all(
        value.startswith("hmac-sha256:") for value in public["evidence_commitments"].values()
    )
    assert public["selection_digest"].startswith("hmac-sha256:")
    assert public["served_model_identity_verified"] is False
    assert public["chronology_authenticated_in_artifact"] is False
    assert public["outside_snapshot_absence_verified"] is False
    assert public["provider_independence_verified"] is False
    assert public["epistemic_independence_verified"] is False
    assert "pilot-card:" not in encoded
    assert "pilot-curation:" not in encoded
    assert "mathnet-random-v4:" not in encoded
    assert "numerator" not in encoded and "denominator" not in encoded
    assert "problem_text" not in encoded and "verification" not in encoded
    for output in bundle["author_outputs"]:
        assert output.candidate_id not in encoded
        assert output.problem_text not in encoded
    for case in bundle["external_selection"].cases:
        assert case.case_id not in encoded
        assert case.problem_text not in encoded


def test_one_duplicate_reviewer_is_enough_to_close_greedy_trap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    first_cell = cell_candidates(bundle, 0)
    last_cell = cell_candidates(bundle, 11)
    last_survivor = last_cell[0]
    disable_body_candidates(bundle, set(last_cell[1:]))
    edge_id = duplicate_edge_id(bundle, first_cell[0], last_survivor)
    set_duplicate_verdicts(bundle, {edge_id}, reviewer="a")

    report = select(bundle)
    assert report.status is InternalPilotSelectionStatusV3.FEASIBLE
    assert report.selected[0].candidate_id == first_cell[1]
    assert report.selected[-1].candidate_id == last_survivor


def test_internal_external_duplicate_edges_make_candidates_ineligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    first_cell = cell_candidates(bundle, 0)
    external = bundle["duplicate_edges"].external_case_ids[0]
    edges = {duplicate_edge_id(bundle, candidate_id, external) for candidate_id in first_cell}
    set_duplicate_verdicts(bundle, edges, reviewer="a")

    report = select(bundle)
    assert report.status is InternalPilotSelectionStatusV3.INFEASIBLE
    assert report.eligible_candidate_count == 33
    assert report.selected == ()


def test_connected_component_closure_blocks_transitive_internal_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    candidates = (*cell_candidates(bundle, 0), *cell_candidates(bundle, 1))
    edges = {
        duplicate_edge_id(bundle, candidates[0], candidate_id) for candidate_id in candidates[1:]
    }
    set_duplicate_verdicts(bundle, edges, reviewer="a")
    report = select(bundle)
    assert report.status is InternalPilotSelectionStatusV3.INFEASIBLE


def test_duplicate_external_selected_cases_fail_the_whole_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    external = bundle["duplicate_edges"].external_case_ids
    edge_id = duplicate_edge_id(bundle, external[0], external[1])
    set_duplicate_verdicts(bundle, {edge_id}, reviewer="b")
    assert select(bundle).status is InternalPilotSelectionStatusV3.INFEASIBLE


def test_duplicate_universe_rejects_undeclared_nodes_and_missing_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    universe = bundle["duplicate_edges"]
    original_ids = universe.internal_candidate_ids
    rogue_id = "authorv3_" + "f" * 64
    assert rogue_id not in original_ids
    with pytest.raises(ValueError, match="endpoint lies outside"):
        replace(
            universe,
            internal_candidate_ids=tuple(sorted((rogue_id, *original_ids[1:]))),
        )

    first_edge = universe.edges[0]
    repeated_pair = type(first_edge)(
        edge_id=(
            "duplicate-edge-v3:" + hashlib.sha256(b"attacker repeated duplicate pair").hexdigest()
        ),
        left=first_edge.left,
        right=first_edge.right,
    )
    with pytest.raises(ValueError, match="canonical full pair universe"):
        replace(universe, edges=(*universe.edges[:-1], repeated_pair))


@pytest.mark.parametrize("boundary", ("audit_projection", "audit_input"))
def test_public_prerun_builders_reject_attacker_keyed_duplicate_universe(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    bundle = make_bundle(monkeypatch)
    attacker_edges = rebuild_duplicate_edges(
        bundle,
        edge_key=b"attacker-duplicate-edge-key-v3-000001",
    )
    attacker_edges.__post_init__()
    assert attacker_edges.edge_key_commitment != bundle["duplicate_edges"].edge_key_commitment

    with pytest.raises(ValueError, match="deterministic keyed replay"):
        if boundary == "audit_projection":
            projection_payload(
                bundle,
                InternalAuditRoleV3.DUPLICATE,
                duplicate_edges=attacker_edges,
            )
        else:
            rebuild_audit_input_commitment(
                bundle,
                duplicate_edges=attacker_edges,
            )


def test_random_external_adapter_rejects_legacy_records_and_tampered_case_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    with pytest.raises(ValueError, match="canonical JSON"):
        adapt_internal_random_external_selection_v3(
            bundle["card_projection"],
            bundle["external_beacon_receipt_bytes"],
            expected_precommitment_record_sha256=(
                bundle["expected_external_precommitment_record_sha256"]
            ),
            expected_beacon_receipt_record_sha256=(
                bundle["expected_external_beacon_receipt_record_sha256"]
            ),
            expected_selection_capability_digest=(
                bundle["expected_external_random_lane_capability_digest"]
            ),
            source=bundle["external_source"],
        )

    original_cases = bundle["external_selection"].cases
    with pytest.raises(ValueError, match="problem-body digest is stale"):
        replace(
            original_cases[0],
            problem_text_digest=digest("substituted-external-problem"),
        )


def test_internal_external_wrapper_has_no_mint_and_nominal_validation_is_not_replay_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = tuple(external_selected_case(index, index) for index in range(12))
    selection, replay = load_external_selection(monkeypatch, cases)

    assert not hasattr(InternalExternalSelectionV3, "_derive")
    assert "_INTERNAL_EXTERNAL_SELECTION_TOKEN_V3" not in vars(selection_module)
    with pytest.raises(TypeError, match="strict random-v4 adapter"):
        InternalExternalSelectionV3()

    original_digest = selection.content_digest
    object.__setattr__(
        replay.capability,
        "source_manifest_digest",
        digest("post-adaptation source substitution"),
    )
    assert selection.content_digest == original_digest
    with pytest.raises(ValueError, match="gold input differs at source_manifest_digest"):
        verify_external_replay(selection, replay)


@pytest.mark.parametrize(
    "boundary",
    (
        "replay_verifier",
        "duplicate_edges",
        "audit_projection",
        "audit_input",
        "selector",
        "report_verifier",
    ),
)
def test_every_public_external_authority_boundary_rejects_a_self_valid_nominal_forgery(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    bundle = make_bundle(monkeypatch)
    genuine = bundle["external_selection"]
    report = select(bundle) if boundary == "report_verifier" else None
    forged = forge_nominal_external_selection(genuine)
    forged.validate_current()
    assert forged.content_digest != genuine.content_digest
    bundle["external_selection"] = forged

    expected_error = (
        "wrapper differs from fresh cold replay"
        if boundary == "replay_verifier"
        else "external-gold replay lineage differs"
    )
    with pytest.raises(ValueError, match=expected_error):
        if boundary == "replay_verifier":
            verify_internal_random_external_selection_v3(
                forged,
                bundle["external_precommitment_bytes"],
                bundle["external_beacon_receipt_bytes"],
                expected_precommitment_record_sha256=(
                    bundle["expected_external_precommitment_record_sha256"]
                ),
                expected_beacon_receipt_record_sha256=(
                    bundle["expected_external_beacon_receipt_record_sha256"]
                ),
                expected_selection_capability_digest=(
                    bundle["expected_external_random_lane_capability_digest"]
                ),
                source=bundle["external_source"],
            )
        elif boundary == "duplicate_edges":
            build_internal_duplicate_edge_universe_v3(
                tuple(output.candidate_id for output in bundle["author_outputs"]),
                forged,
                external_precommitment_bytes=bundle["external_precommitment_bytes"],
                external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
                external_gold_audit_report=bundle["external_gold_audit_report"],
                expected_external_gold_report_verifier=(
                    bundle["expected_external_gold_report_verifier"]
                ),
                expected_external_gold_audit_signed_report_digest=(
                    bundle["expected_external_gold_audit_signed_report_digest"]
                ),
                expected_external_selection_precommit_anchor_evidence_digest=(
                    bundle["expected_external_selection_precommit_anchor_evidence_digest"]
                ),
                expected_external_precommitment_record_sha256=(
                    bundle["expected_external_precommitment_record_sha256"]
                ),
                expected_external_beacon_receipt_record_sha256=(
                    bundle["expected_external_beacon_receipt_record_sha256"]
                ),
                expected_external_random_lane_capability_digest=(
                    bundle["expected_external_random_lane_capability_digest"]
                ),
                external_source=bundle["external_source"],
                edge_key=bundle["duplicate_edge_key"],
            )
        elif boundary == "audit_projection":
            projection_payload(bundle, InternalAuditRoleV3.DUPLICATE)
        elif boundary == "audit_input":
            build_internal_audit_input_commitment_v3(
                bundle["precommitment"],
                author_outputs=bundle["author_outputs"],
                author_commitments=bundle["author_commitments"],
                card_projection=bundle["card_projection"],
                external_selection=forged,
                duplicate_edges=bundle["duplicate_edges"],
                author_execution=bundle["author_execution"],
                external_precommitment_bytes=bundle["external_precommitment_bytes"],
                external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
                external_gold_audit_report=bundle["external_gold_audit_report"],
                expected_external_gold_report_verifier=(
                    bundle["expected_external_gold_report_verifier"]
                ),
                expected_external_gold_audit_signed_report_digest=(
                    bundle["expected_external_gold_audit_signed_report_digest"]
                ),
                expected_external_selection_precommit_anchor_evidence_digest=(
                    bundle["expected_external_selection_precommit_anchor_evidence_digest"]
                ),
                expected_external_precommitment_record_sha256=(
                    bundle["expected_external_precommitment_record_sha256"]
                ),
                expected_external_beacon_receipt_record_sha256=(
                    bundle["expected_external_beacon_receipt_record_sha256"]
                ),
                expected_external_random_lane_capability_digest=(
                    bundle["expected_external_random_lane_capability_digest"]
                ),
                external_source=bundle["external_source"],
                duplicate_edge_key=bundle["duplicate_edge_key"],
                expected_author_execution_manifest_digest=(
                    bundle["expected_author_execution_manifest_digest"]
                ),
                expected_author_execution_collector_public_key=(
                    bundle["expected_author_execution_collector_public_key"]
                ),
                evidence_key=bundle["evidence_key"],
            )
        elif boundary == "selector":
            select(bundle)
        else:
            verify(report, bundle)


@pytest.mark.parametrize(
    "boundary",
    (
        "gold_verifier",
        "duplicate_edges",
        "audit_projection",
        "audit_input",
        "selector",
        "report_verifier",
    ),
)
def test_every_public_gold_authority_boundary_rejects_an_attacker_signed_replacement(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    bundle = make_bundle(monkeypatch)
    selection_report = select(bundle) if boundary == "report_verifier" else None
    genuine = bundle["external_gold_audit_report"]
    replacement = resign_external_gold_report(
        genuine,
        CanonicalEd25519SignerV3(
            hashlib.sha256(b"attacker external gold report signer").digest(),
            domain=external_gold.EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
        ),
    )
    assert replacement.signed_report_digest != genuine.signed_report_digest
    bundle["external_gold_audit_report"] = replacement
    bundle["expected_external_gold_audit_signed_report_digest"] = replacement.signed_report_digest

    with pytest.raises(ValueError, match="signer differs from campaign authority"):
        if boundary == "gold_verifier":
            verify_internal_external_gold_audit_v3(
                replacement,
                bundle["expected_external_gold_report_verifier"],
                bundle["external_selection"],
                bundle["external_precommitment_bytes"],
                bundle["external_beacon_receipt_bytes"],
                expected_signed_report_digest=(
                    bundle["expected_external_gold_audit_signed_report_digest"]
                ),
                expected_external_selection_precommit_anchor_evidence_digest=(
                    bundle["expected_external_selection_precommit_anchor_evidence_digest"]
                ),
                expected_precommitment_record_sha256=(
                    bundle["expected_external_precommitment_record_sha256"]
                ),
                expected_beacon_receipt_record_sha256=(
                    bundle["expected_external_beacon_receipt_record_sha256"]
                ),
                expected_selection_capability_digest=(
                    bundle["expected_external_random_lane_capability_digest"]
                ),
                source=bundle["external_source"],
            )
        elif boundary == "duplicate_edges":
            build_internal_duplicate_edge_universe_v3(
                tuple(output.candidate_id for output in bundle["author_outputs"]),
                bundle["external_selection"],
                external_precommitment_bytes=bundle["external_precommitment_bytes"],
                external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
                external_gold_audit_report=replacement,
                expected_external_gold_report_verifier=(
                    bundle["expected_external_gold_report_verifier"]
                ),
                expected_external_gold_audit_signed_report_digest=(
                    bundle["expected_external_gold_audit_signed_report_digest"]
                ),
                expected_external_selection_precommit_anchor_evidence_digest=(
                    bundle["expected_external_selection_precommit_anchor_evidence_digest"]
                ),
                expected_external_precommitment_record_sha256=(
                    bundle["expected_external_precommitment_record_sha256"]
                ),
                expected_external_beacon_receipt_record_sha256=(
                    bundle["expected_external_beacon_receipt_record_sha256"]
                ),
                expected_external_random_lane_capability_digest=(
                    bundle["expected_external_random_lane_capability_digest"]
                ),
                external_source=bundle["external_source"],
                edge_key=bundle["duplicate_edge_key"],
            )
        elif boundary == "audit_projection":
            projection_payload(bundle, InternalAuditRoleV3.DUPLICATE)
        elif boundary == "audit_input":
            build_internal_audit_input_commitment_v3(
                bundle["precommitment"],
                author_outputs=bundle["author_outputs"],
                author_commitments=bundle["author_commitments"],
                card_projection=bundle["card_projection"],
                external_selection=bundle["external_selection"],
                duplicate_edges=bundle["duplicate_edges"],
                author_execution=bundle["author_execution"],
                external_precommitment_bytes=bundle["external_precommitment_bytes"],
                external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
                external_gold_audit_report=replacement,
                expected_external_gold_report_verifier=(
                    bundle["expected_external_gold_report_verifier"]
                ),
                expected_external_gold_audit_signed_report_digest=(
                    bundle["expected_external_gold_audit_signed_report_digest"]
                ),
                expected_external_selection_precommit_anchor_evidence_digest=(
                    bundle["expected_external_selection_precommit_anchor_evidence_digest"]
                ),
                expected_external_precommitment_record_sha256=(
                    bundle["expected_external_precommitment_record_sha256"]
                ),
                expected_external_beacon_receipt_record_sha256=(
                    bundle["expected_external_beacon_receipt_record_sha256"]
                ),
                expected_external_random_lane_capability_digest=(
                    bundle["expected_external_random_lane_capability_digest"]
                ),
                external_source=bundle["external_source"],
                duplicate_edge_key=bundle["duplicate_edge_key"],
                expected_author_execution_manifest_digest=(
                    bundle["expected_author_execution_manifest_digest"]
                ),
                expected_author_execution_collector_public_key=(
                    bundle["expected_author_execution_collector_public_key"]
                ),
                evidence_key=bundle["evidence_key"],
            )
        elif boundary == "selector":
            select(bundle)
        else:
            verify(selection_report, bundle)


def test_random_external_adapter_rejects_reordered_and_nonexact_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = tuple(external_selected_case(index, index) for index in range(12))
    _selection, replay = load_external_selection(monkeypatch, cases)
    capability = replay.capability
    source_rows = capability.selected_rows

    object.__setattr__(capability, "selected_rows", tuple(reversed(source_rows)))
    with pytest.raises(ValueError, match="reordered or noncontiguous"):
        adapt_external_replay(replay)

    object.__setattr__(capability, "selected_rows", source_rows)
    object.__setattr__(capability, "selected_rows", (object(), *source_rows[1:]))
    with pytest.raises(ValueError, match="non-nominal selected row"):
        adapt_external_replay(replay)


def test_random_external_adapter_rejects_duplicate_clusters_and_stale_openings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = tuple(external_selected_case(index, index) for index in range(12))
    duplicate_cluster_cases = (
        cases[0],
        replace(cases[1], source_cluster_digest=cases[0].source_cluster_digest),
        *cases[2:],
    )
    with pytest.raises(ValueError, match="repeats a selected source cluster"):
        load_external_selection(monkeypatch, duplicate_cluster_cases)

    _selection, replay = load_external_selection(monkeypatch, cases)
    capability = replay.capability
    object.__setattr__(
        capability.selected_rows[0],
        "problem_text_digest",
        digest("stale problem-body opening"),
    )
    with pytest.raises(ValueError, match="problem-text digest is stale"):
        adapt_external_replay(replay)

    _selection, replay = load_external_selection(monkeypatch, cases)
    capability = replay.capability
    object.__setattr__(
        capability,
        "source_manifest_digest",
        "hmac-sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="gold input differs at source_manifest_digest"):
        adapt_external_replay(replay)


def test_different_valid_random_twelve_cannot_replace_externally_selected_bank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    alternate, alternate_replay = load_external_selection(
        monkeypatch,
        tuple(
            external_selected_case(selection_index, selection_index + 12)
            for selection_index in range(12)
        ),
    )
    bundle["external_selection"] = alternate
    bundle["external_precommitment_bytes"] = alternate_replay.external_precommitment_bytes
    bundle["external_beacon_receipt_bytes"] = alternate_replay.external_beacon_receipt_bytes
    bundle["expected_external_precommitment_record_sha256"] = (
        alternate_replay.expected_external_precommitment_record_sha256
    )
    bundle["expected_external_beacon_receipt_record_sha256"] = (
        alternate_replay.expected_external_beacon_receipt_record_sha256
    )
    bundle["external_source"] = alternate_replay.external_source
    with pytest.raises(ValueError, match="external-gold replay cases differ"):
        select(bundle)


def test_random_external_source_digests_are_separate_external_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    for field, message in (
        (
            "expected_external_precommitment_record_sha256",
            "precommitment differs from its external anchor",
        ),
        (
            "expected_external_beacon_receipt_record_sha256",
            "beacon receipt differs from its external anchor",
        ),
        (
            "expected_external_gold_audit_signed_report_digest",
            "signed external-gold report differs",
        ),
        (
            "expected_external_selection_precommit_anchor_evidence_digest",
            "another precommit anchor evidence",
        ),
        ("expected_external_selection_digest", "external selection"),
        ("expected_external_projection_digest", "external projection"),
        (
            "expected_external_random_lane_capability_digest",
            "external capability anchor",
        ),
    ):
        original = bundle[field]
        bundle[field] = digest(f"substituted {field}")
        with pytest.raises(ValueError, match=message):
            select(bundle)
        bundle[field] = original


def test_external_gold_report_must_verify_under_the_precommitted_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    bundle["expected_external_gold_report_verifier"] = CanonicalEd25519SignerV3(
        hashlib.sha256(b"substituted-selector-test-external-gold-authority").digest(),
        domain="org.math-research-brain/test-external-gold-report/v4",
    ).verifier()

    with pytest.raises(ValueError, match="signer differs from campaign authority"):
        select(bundle)


def test_real_random_and_gold_modules_bind_every_external_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    report = bundle["external_gold_audit_report"]
    assert selection_module._random_external_lane_module_v3() is random_lane
    assert selection_module._external_gold_audit_module_v3() is external_gold
    assert type(report) is external_gold.ExternalGoldAuditReportV4
    assert (
        type(report.random_replay_evidence.selection)
        is external_gold.RandomExternalGoldSelectionBindingV4
    )
    assert (
        report.signed_report_digest == (bundle["expected_external_gold_audit_signed_report_digest"])
    )
    verify_external_gold_from_bundle(bundle)

    wrong_verifier = CanonicalEd25519SignerV3(
        hashlib.sha256(b"wrong real external gold verifier").digest(),
        domain=external_gold.EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
    ).verifier()
    with pytest.raises(ValueError, match="signer differs from campaign authority"):
        verify_external_gold_from_bundle(bundle, verifier=wrong_verifier)

    with pytest.raises(ValueError, match="signed external-gold report differs"):
        verify_external_gold_from_bundle(
            bundle,
            signed_report_digest=digest("wrong signed external gold report"),
        )

    wrong_content = clone_external_gold_report(
        report,
        content_digest=digest("wrong external gold report content"),
    )
    with pytest.raises(ValueError, match="content digest is stale"):
        verify_external_gold_from_bundle(bundle, report=wrong_content)

    wrong_vault = clone_external_gold_report(
        report,
        private_gold_vault_record_sha256=digest("wrong external gold vault"),
    )
    with pytest.raises(ValueError, match="differs from fresh random replay"):
        verify_external_gold_from_bundle(bundle, report=wrong_vault)

    unsigned = clone_external_gold_report(
        report,
        report_signature="ed25519:" + "0" * 128,
    )
    with pytest.raises(ValueError, match="signature"):
        verify_external_gold_from_bundle(bundle, report=unsigned)

    with pytest.raises(ValueError, match="another precommit anchor evidence"):
        verify_external_gold_from_bundle(
            bundle,
            anchor_evidence_digest=digest("wrong external selection anchor"),
        )


def test_regex_valid_card_outside_exact_frozen_twenty_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    first_cell = cell_candidates(bundle, 0)
    outside = card(999)
    assert outside not in bundle["card_projection"].card_by_id
    overrides = {candidate_id: outside for candidate_id in first_cell}
    for key in ("relevance_a", "relevance_b"):
        bundle[key] = replace_cards(bundle[key], overrides)
    rebind_role(bundle, InternalAuditRoleV3.RELEVANCE)

    report = select(bundle)
    assert report.status is InternalPilotSelectionStatusV3.INFEASIBLE
    assert report.eligible_candidate_count == 33


def test_signed_evidence_must_bind_exact_decisions_and_same_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    first = bundle["body_a"].decisions[0]
    bundle["body_a"] = replace_decisions(
        bundle["body_a"],
        {first.candidate_id: replace(first, single_exact_scalar=False)},
    )
    with pytest.raises(ValueError, match=r"raw reviewer response|does not bind"):
        select(bundle)

    bundle = make_bundle(monkeypatch)
    changed_projection = canonical_json({"role": "body", "schema": "different-projection"}).encode()
    with pytest.raises(ValueError, match="projection differs from the frozen precommitment"):
        rebind_role(
            bundle,
            InternalAuditRoleV3.BODY,
            projection_overrides={InternalReviewerSlotV3.A: changed_projection},
        )


def test_reviewer_presentations_are_slot_specific_but_semantically_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    left = bundle["duplicate_a_evidence"].projection_payload
    right = bundle["duplicate_b_evidence"].projection_payload
    assert left != right
    left_record = json.loads(left)
    right_record = json.loads(right)
    assert left_record["reviewer_slot"] == "a"
    assert right_record["reviewer_slot"] == "b"
    assert left_record["presentation_nonce"] != right_record["presentation_nonce"]
    assert left_record["presentation_mapping"] != right_record["presentation_mapping"]
    assert left_record["semantic_universe_digest"] == right_record["semantic_universe_digest"]
    assert left_record["cards"] is None
    assert left_record["external_cases"] == [
        {
            "case_id": case.case_id,
            "problem_text": case.problem_text,
            "problem_text_digest": case.problem_text_digest,
        }
        for case in bundle["external_selection"].cases
    ]
    encoded_left = left.decode("utf-8")
    assert bundle["external_selection"].selection_digest not in encoded_left
    assert bundle["external_selection"].content_digest not in encoded_left
    assert bundle["external_selection"].random_lane_capability_digest not in encoded_left
    for case in bundle["external_selection"].cases:
        for hidden_commitment in (
            case.source_cluster_digest,
            case.public_selection_digest,
            case.candidate_content_digest,
            case.private_gold_commitment,
            case.source_row_content_digest,
        ):
            assert hidden_commitment not in encoded_left
    for hidden_lineage in (
        bundle["external_selection"].source_manifest_digest,
        bundle["external_selection"].source_runtime_evidence_digest,
        bundle["external_selection"].selector_code_manifest_digest,
        bundle["external_selection"].precommitment_record_sha256,
        bundle["external_selection"].beacon_receipt_record_sha256,
        bundle["external_selection"].verified_bank_digest,
        bundle["external_selection"].selection_seed_commitment,
        bundle["external_selection"].private_gold_vault_record_sha256,
    ):
        assert hidden_lineage not in encoded_left
    assert all(item.problem_text not in encoded_left for item in bundle["card_projection"].items)
    assert select(bundle).status is InternalPilotSelectionStatusV3.FEASIBLE


def test_projection_and_response_openings_cannot_be_substituted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    evidence = bundle["body_a_evidence"]
    tampered_projection = json.loads(evidence.projection_payload)
    tampered_projection["presentation_nonce"] = digest("substituted-presentation")
    with pytest.raises(ValueError, match="does not open to its raw payload"):
        replace(
            evidence,
            projection_payload=canonical_json(tampered_projection).encode("utf-8"),
        )

    tampered_binding = json.loads(evidence.projection_artifact)
    tampered_binding["external_gold_audit_signed_report_digest"] = digest(
        "substituted signed external-gold report"
    )
    with pytest.raises(ValueError, match="projection and payload bind different gold reports"):
        replace(
            evidence,
            projection_artifact=canonical_json(tampered_binding).encode("utf-8"),
        )

    opening = evidence.attempt_opening
    changed_plan = json.loads(opening.attempt_precommitment_artifact)
    changed_plan["prompt_template"] = "post-hoc substituted prompt"
    with pytest.raises(ValueError, match="no-retry protocol"):
        replace(
            opening,
            attempt_precommitment_artifact=canonical_json(changed_plan).encode("utf-8"),
        )

    swapped_response = bundle["body_b_evidence"].attempt_opening.response_artifact
    with pytest.raises(ValueError, match="outcome selection does not open"):
        replace(opening, response_artifact=swapped_response)


@pytest.mark.parametrize(
    ("role", "field", "value", "message"),
    [
        (
            InternalAuditRoleV3.RELEVANCE,
            "card_projection_content_digest",
            digest("substituted-card-projection"),
            "frozen card universe",
        ),
        (
            InternalAuditRoleV3.DUPLICATE,
            "duplicate_edge_key_commitment",
            hmac_commitment("substituted-edge-key"),
            "blinded edges",
        ),
        (
            InternalAuditRoleV3.DUPLICATE,
            "external_selection_digest",
            digest("substituted-external-selection"),
            "external selection",
        ),
        (
            InternalAuditRoleV3.DUPLICATE,
            "external_projection_digest",
            digest("substituted-external-projection"),
            "external projection",
        ),
        (
            InternalAuditRoleV3.DUPLICATE,
            "external_random_lane_capability_digest",
            digest("substituted-random-lane-capability"),
            "random-lane capability",
        ),
        (
            InternalAuditRoleV3.DUPLICATE,
            "external_binding_digest",
            digest("substituted-external-binding"),
            "external binding",
        ),
    ],
)
def test_even_reanchored_projection_bindings_must_match_exact_selector_inputs(
    monkeypatch: pytest.MonkeyPatch,
    role: InternalAuditRoleV3,
    field: str,
    value: str,
    message: str,
) -> None:
    bundle = make_bundle(monkeypatch)
    projection_by_slot: dict[InternalReviewerSlotV3, bytes] = {}
    for reviewer_slot in InternalReviewerSlotV3:
        projection = json.loads(projection_artifact(bundle, role, reviewer_slot))
        projection[field] = value
        projection_by_slot[reviewer_slot] = canonical_json(projection).encode("utf-8")
    precommitment = bundle["audit_precommitment"]
    entries = tuple(
        replace(
            entry,
            projection_digest=(
                "sha256:" + hashlib.sha256(projection_by_slot[entry.reviewer_slot]).hexdigest()
            ),
            attempt_precommitment_digest=(
                "sha256:"
                + hashlib.sha256(
                    attempt_opening(
                        bundle,
                        role,
                        entry.reviewer_slot,
                        audit_input_commitment=precommitment.audit_input_commitment,
                        projection_override=projection_by_slot[entry.reviewer_slot],
                    ).attempt_precommitment_artifact
                ).hexdigest()
            ),
        )
        if entry.role is role
        else entry
        for entry in precommitment.entries
    )
    bundle["audit_precommitment"] = replace(precommitment, entries=entries)
    bundle["expected_audit_precommitment_digest"] = bundle["audit_precommitment"].content_digest
    for current_role in InternalAuditRoleV3:
        rebind_role(
            bundle,
            current_role,
            projection_overrides=(
                {
                    InternalReviewerSlotV3.A: projection_by_slot[InternalReviewerSlotV3.A],
                    InternalReviewerSlotV3.B: projection_by_slot[InternalReviewerSlotV3.B],
                }
                if current_role is role
                else None
            ),
        )
    with pytest.raises(ValueError, match=message):
        select(bundle)


def test_signed_audit_artifacts_require_strict_canonical_object_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    opening = attempt_opening(
        bundle,
        InternalAuditRoleV3.BODY,
        InternalReviewerSlotV3.A,
    )
    for artifact, message in (
        (b'{"value":NaN}', "strict JSON"),
        (b'{"value":1,"value":1}', "strict JSON"),
        (b"[]", "object root"),
        (b'{"value": 1}', "canonical JSON"),
    ):
        with pytest.raises(ValueError, match=message):
            replace(opening, attempt_evidence_artifact=artifact)


def test_signed_attempt_evidence_is_bound_to_returned_first_attempt_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    opening = attempt_opening(
        bundle,
        InternalAuditRoleV3.BODY,
        InternalReviewerSlotV3.A,
    )
    for artifact_name, field, value in (
        (
            "started_record_artifact",
            "attempt_precommitment_digest",
            digest("post-hoc-attempt-plan"),
        ),
        ("started_record_artifact", "retry_count", 1),
        ("terminal_record_artifact", "terminal_state", "FAILED"),
        ("attempt_evidence_artifact", "reviewer_slot", "b"),
    ):
        record = json.loads(getattr(opening, artifact_name))
        record[field] = value
        with pytest.raises(ValueError, match=r"first attempt|raw records|terminal"):
            replace(opening, **{artifact_name: canonical_json(record).encode("utf-8")})


def test_external_audit_anchor_cannot_be_silently_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    bundle["expected_audit_precommitment_digest"] = digest("post-hoc replacement")
    with pytest.raises(ValueError, match="external anchor"):
        select(bundle)


def test_audit_precommitment_cross_binds_exact_author_execution_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    replacement_signer = collector_signer("replacement")
    bundle["author_execution"] = build_internal_author_execution_evidence_v3(
        bundle["precommitment"],
        execution_plan_artifact=bundle["author_execution"].execution_plan_artifact,
        slot_records=bundle["author_execution"].slot_records,
        collector_signer=replacement_signer,
    )
    bundle["expected_author_execution_manifest_digest"] = (
        "sha256:" + hashlib.sha256(bundle["author_execution"].manifest_bytes).hexdigest()
    )
    bundle["expected_author_execution_collector_public_key"] = (
        replacement_signer.verifier().public_key_bytes()
    )
    with pytest.raises(ValueError, match="frozen audit inputs"):
        select(bundle)


def test_distinct_role_names_cannot_mask_reused_signed_reviewer_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    with pytest.raises(ValueError, match="frozen reviewer authority"):
        rebind_role(
            bundle,
            InternalAuditRoleV3.GOLD,
            seed_overrides={InternalReviewerSlotV3.A: "body-a"},
        )


def test_audit_precommitment_rejects_reused_reviewer_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    precommitment = bundle["audit_precommitment"]
    body_a = precommitment.entry_for(
        InternalAuditRoleV3.BODY,
        InternalReviewerSlotV3.A,
    )
    entries = tuple(
        replace(entry, reviewer_public_key=body_a.reviewer_public_key)
        if (entry.role, entry.reviewer_slot) == (InternalAuditRoleV3.GOLD, InternalReviewerSlotV3.A)
        else entry
        for entry in precommitment.entries
    )
    with pytest.raises(ValueError, match="reuses a reviewer signing key"):
        replace(precommitment, entries=entries)


def test_authenticated_execution_manifest_must_prove_all_first_attempt_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    original = bundle["author_execution"]
    for field, value in (
        ("retry_count", 1),
        ("execute_first_attempt_count", 35),
        ("started_slot_count", 35),
        ("terminal_slot_count", 35),
        ("top_up_count", 1),
        ("remint_count", 1),
        ("orphan_record_count", 1),
        ("chronology_authenticated_in_artifact", True),
        ("outside_snapshot_absence_verified", True),
        ("model_identity_verified", True),
        ("provider_independence_verified", True),
        ("epistemic_independence_verified", True),
    ):
        manifest = json.loads(original.manifest_bytes)
        manifest[field] = value
        bundle["author_execution"] = resign_execution(
            original,
            manifest_bytes=canonical_json(manifest).encode("utf-8"),
        )
        bundle["expected_author_execution_manifest_digest"] = (
            "sha256:" + hashlib.sha256(bundle["author_execution"].manifest_bytes).hexdigest()
        )
        with pytest.raises(ValueError, match="exact ledger replay"):
            select(bundle)


def test_duplicate_or_invented_slot_openings_cannot_fake_the_36_slot_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    records = list(bundle["author_execution"].slot_records)
    records[1] = records[0]
    bundle["author_execution"] = resign_execution(
        bundle["author_execution"],
        slot_records=tuple(records),
    )
    with pytest.raises(ValueError, match="frozen slot order"):
        select(bundle)

    bundle = make_bundle(monkeypatch)
    plan = json.loads(bundle["author_execution"].execution_plan_artifact)
    plan["slots"][0]["prompt_digest"] = digest("substituted-author-prompt")
    bundle["author_execution"] = resign_execution(
        bundle["author_execution"],
        execution_plan_artifact=canonical_json(plan).encode("utf-8"),
    )
    with pytest.raises(ValueError, match="execution plan differs from the frozen slots"):
        select(bundle)

    bundle = make_bundle(monkeypatch)
    records = list(bundle["author_execution"].slot_records)
    records[0] = forge_slot_ledger_digest(records[0], digest("invented-unique-ledger"))
    bundle["author_execution"] = resign_execution(
        bundle["author_execution"],
        slot_records=tuple(records),
    )
    with pytest.raises(ValueError, match="frozen slot order"):
        select(bundle)


def test_each_author_started_record_forbids_retry_predecessor_topup_and_remint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    record = bundle["author_execution"].slot_records[0]
    for field, value in (
        ("attempt_ordinal", 1),
        ("retry_count", 1),
        ("predecessor_terminal_digest", digest("invented-predecessor")),
        ("top_up", True),
        ("remint", True),
    ):
        started = json.loads(record.started_record_artifact)
        started[field] = value
        with pytest.raises(ValueError, match="first-attempt gate"):
            replace(
                record,
                started_record_artifact=canonical_json(started).encode("utf-8"),
            )


def test_failed_author_terminal_forbids_selected_object_and_requires_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = make_bundle(monkeypatch)
    burned_id = full["precommitment"].slots[0].candidate_id
    bundle = make_bundle(monkeypatch, omitted=frozenset({burned_id}))
    failed = bundle["author_execution"].slot_records[0]
    with pytest.raises(ValueError, match="must not select an outcome"):
        replace(failed, response_artifact=b"{}")
    terminal = json.loads(failed.terminal_record_artifact)
    terminal["failure_kind"] = "invented_failure"
    with pytest.raises(ValueError, match="failure taxonomy"):
        replace(
            failed,
            terminal_record_artifact=canonical_json(terminal).encode("utf-8"),
        )


def test_extra_orphan_snapshot_record_is_rejected_even_when_collector_resigns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    snapshot = json.loads(bundle["author_execution"].ledger_snapshot_artifact)
    snapshot["extra_record_count"] = 1
    snapshot["orphan_record_digests"] = [digest("invented-orphan-record")]
    bundle["author_execution"] = resign_execution(
        bundle["author_execution"],
        ledger_snapshot_artifact=canonical_json(snapshot).encode("utf-8"),
    )
    with pytest.raises(ValueError, match="extra or orphan records"):
        select(bundle)


def test_swapped_returned_raw_response_is_not_a_successful_author_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    evidence = bundle["author_execution"]
    records = list(evidence.slot_records)
    wrong_slot_response = json.loads(records[1].response_artifact)
    wrong_slot_response["verification"][0] += " This keeps the object digest unique."
    records[0] = build_internal_author_execution_slot_evidence_v3(
        bundle["precommitment"],
        evidence.execution_plan_artifact,
        slot_index=0,
        terminal_state=InternalAuthorTerminalStateV3.RETURNED,
        response_artifact=canonical_json(wrong_slot_response).encode("utf-8"),
    )
    bundle["author_execution"] = build_internal_author_execution_evidence_v3(
        bundle["precommitment"],
        execution_plan_artifact=evidence.execution_plan_artifact,
        slot_records=tuple(records),
        collector_signer=collector_signer(),
    )
    bundle["expected_author_execution_manifest_digest"] = (
        "sha256:" + hashlib.sha256(bundle["author_execution"].manifest_bytes).hexdigest()
    )
    with pytest.raises(ValueError, match="strict raw-response parsing"):
        select(bundle)


def test_prerun_audit_input_rejects_attacker_resigned_and_incomplete_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    attacker_signer = collector_signer("attacker")
    attacker_resigned = resign_execution(
        bundle["author_execution"],
        signer=attacker_signer,
    )
    attacker_resigned.__post_init__()
    with pytest.raises(ValueError, match="uncommitted collector key"):
        rebuild_audit_input_commitment(
            bundle,
            author_execution=attacker_resigned,
        )

    incomplete = resign_execution(
        bundle["author_execution"],
        slot_records=bundle["author_execution"].slot_records[:-1],
    )
    incomplete.__post_init__()
    with pytest.raises(ValueError, match="exactly 36 exact slot openings"):
        rebuild_audit_input_commitment(
            bundle,
            author_execution=incomplete,
        )


def test_author_execution_manifest_and_collector_are_external_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    bundle["expected_author_execution_manifest_digest"] = digest("replacement-manifest")
    with pytest.raises(ValueError, match="external anchor"):
        select(bundle)

    bundle = make_bundle(monkeypatch)
    bundle["expected_author_execution_collector_public_key"] = (
        collector_signer("attacker").verifier().public_key_bytes()
    )
    with pytest.raises(ValueError, match="uncommitted collector key"):
        select(bundle)


def test_execution_manifest_is_required_and_bound_to_successful_authors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    bundle["author_execution"] = None
    with pytest.raises(ValueError, match="execution evidence"):
        select(bundle)

    bundle = make_bundle(monkeypatch)
    bundle["author_outputs"] = bundle["author_outputs"][:-1]
    bundle["author_commitments"] = bundle["author_commitments"][:-1]
    with pytest.raises(ValueError, match="strict raw-response parsing"):
        select(bundle)


def test_card_cap_backtracking_reserves_last_shared_card_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    cells = [cell_candidates(bundle, index) for index in range(12)]
    disable_body_candidates(bundle, set(cells[-1][1:]))
    shared = card(12)
    overrides: dict[str, str] = {}
    for cell_index, candidates in enumerate(cells):
        for candidate_id in candidates:
            overrides[candidate_id] = card(cell_index)
    overrides[cells[0][0]] = shared
    overrides[cells[1][0]] = shared
    overrides[cells[1][1]] = card(13)
    overrides[cells[-1][0]] = shared
    for key in ("relevance_a", "relevance_b"):
        bundle[key] = replace_cards(bundle[key], overrides)
    rebind_role(bundle, InternalAuditRoleV3.RELEVANCE)

    report = select(bundle)
    assert report.status is InternalPilotSelectionStatusV3.FEASIBLE
    assert report.selected[1].candidate_id == cells[1][1]
    assert Counter(item.card_id for item in report.selected)[shared] == 2


def test_missing_review_universe_and_gold_disagreement_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    bundle["body_a"] = replace(bundle["body_a"], decisions=bundle["body_a"].decisions[:-1])
    rebind_role(bundle, InternalAuditRoleV3.BODY)
    with pytest.raises(ValueError, match="incomplete"):
        select(bundle)

    bundle = make_bundle(monkeypatch)
    first_cell = cell_candidates(bundle, 0)
    universe = bundle["gold_b"]
    bundle["gold_b"] = replace_decisions(
        universe,
        {
            candidate_id: replace(
                next(
                    decision
                    for decision in universe.decisions
                    if decision.candidate_id == candidate_id
                ),
                answer=ExactRationalV3(999_999, 1),
            )
            for candidate_id in first_cell
        },
    )
    rebind_role(bundle, InternalAuditRoleV3.GOLD)
    assert select(bundle).status is InternalPilotSelectionStatusV3.INFEASIBLE


def test_body_cell_disagreement_never_migrates_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    first_cell = cell_candidates(bundle, 0)
    for key in ("body_a", "body_b"):
        universe = bundle[key]
        bundle[key] = replace_decisions(
            universe,
            {
                candidate_id: replace(
                    next(
                        decision
                        for decision in universe.decisions
                        if decision.candidate_id == candidate_id
                    ),
                    domain=MathDomain.NUMBER_THEORY,
                    difficulty=DifficultyBand.EASY,
                )
                for candidate_id in first_cell
            },
        )
    rebind_role(bundle, InternalAuditRoleV3.BODY)
    report = select(bundle)
    assert report.status is InternalPilotSelectionStatusV3.INFEASIBLE
    assert report.eligible_candidate_count == 33


def test_burned_author_remains_absent_but_execution_gate_stays_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = make_bundle(monkeypatch)
    burned_id = cell_candidates(full, 0)[-1]
    bundle = make_bundle(monkeypatch, omitted=frozenset({burned_id}))
    report = select(bundle)

    assert report.status is InternalPilotSelectionStatusV3.FEASIBLE
    assert report.precommitted_author_count == 36
    assert report.successful_author_count == 35
    assert report.burned_author_count == 1
    manifest = json.loads(bundle["author_execution"].manifest_bytes)
    assert manifest["started_slot_count"] == manifest["terminal_slot_count"] == 36
    assert manifest["retry_count"] == 0


def test_canonical_order_and_deterministic_edge_replay_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    assert bundle["duplicate_edges"] == build_internal_duplicate_edge_universe_v3(
        tuple(output.candidate_id for output in bundle["author_outputs"]),
        bundle["external_selection"],
        external_precommitment_bytes=bundle["external_precommitment_bytes"],
        external_beacon_receipt_bytes=bundle["external_beacon_receipt_bytes"],
        external_gold_audit_report=bundle["external_gold_audit_report"],
        expected_external_gold_report_verifier=(bundle["expected_external_gold_report_verifier"]),
        expected_external_gold_audit_signed_report_digest=(
            bundle["expected_external_gold_audit_signed_report_digest"]
        ),
        expected_external_selection_precommit_anchor_evidence_digest=(
            bundle["expected_external_selection_precommit_anchor_evidence_digest"]
        ),
        expected_external_precommitment_record_sha256=(
            bundle["expected_external_precommitment_record_sha256"]
        ),
        expected_external_beacon_receipt_record_sha256=(
            bundle["expected_external_beacon_receipt_record_sha256"]
        ),
        expected_external_random_lane_capability_digest=(
            bundle["expected_external_random_lane_capability_digest"]
        ),
        external_source=bundle["external_source"],
        edge_key=DUPLICATE_EDGE_KEY,
    )
    bundle["author_outputs"] = tuple(reversed(bundle["author_outputs"]))
    with pytest.raises(ValueError, match="canonical sorted order"):
        select(bundle)

    bundle = make_bundle(monkeypatch)
    bundle["duplicate_edge_key"] = b"wrong-duplicate-edge-key-00000001"
    with pytest.raises(ValueError, match="deterministic keyed replay"):
        select(bundle)


def test_replay_detects_report_and_signed_evidence_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = make_bundle(monkeypatch)
    report = select(bundle)
    verify(report, bundle)

    tampered_report = replace(report, selection_digest="hmac-sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="deterministic replay"):
        verify(tampered_report, bundle)

    first = bundle["relevance_a"].decisions[0]
    bundle["relevance_a"] = replace_decisions(
        bundle["relevance_a"],
        {first.candidate_id: replace(first, card_id=card(1))},
    )
    with pytest.raises(ValueError, match=r"raw reviewer response|does not bind"):
        verify(report, bundle)
