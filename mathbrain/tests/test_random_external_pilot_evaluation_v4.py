from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

import pytest

from math_brain.canonical_ed25519_v3 import CanonicalEd25519SignerV3
from math_brain.models import canonical_json
from math_brain.random_external_pilot_evaluation_v4 import (
    AttemptOutcomeV4,
    PilotInterpretationV4,
    RandomExternalAnalysisReceiptSignerV4,
    RandomExternalAuditReceiptSignerV4,
    random_external_result_manifest_digest_v4,
    summarize_random_external_pilot_v4,
)
from math_brain.random_external_scoring_authority_v4 import (
    RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4,
    AuthenticatedRandomExternalScoreV4,
    AuthenticatedRandomScoreVerifierV4,
    ClosedAttemptDispositionV4,
)
from math_brain.three_arm_evaluation import (
    DifficultyBand,
    FrozenPacket,
    MathDomain,
    ProblemSource,
    StudyArm,
    StudyStage,
    ThreeArmConfig,
    TokenizerReceipt,
)
from math_brain.three_arm_pilot_protocol_v3 import (
    EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3,
    EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3,
    EXACT_RATIONAL_SCORING_RULE_DIGEST_V3,
    SOLVER_PROMPT_TEMPLATE_DIGEST_V3,
    SOLVER_RESPONSE_SCHEMA_DIGEST_V3,
    ScoreDispositionV3,
)
from math_brain.three_arm_random_external_pilot_v4 import (
    AutomaticRetrievalDispositionV4,
    RandomExternalPilotCaseV4,
    RandomExternalPilotConfigV4,
    RandomExternalPilotPlanV4,
    build_random_external_pilot_plan_v4,
)

_ALLOCATION_SEED = b"allocation-seed-v4" + b"a" * 32


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Harness:
    plan: RandomExternalPilotPlanV4
    scoring: _TestScoreAuthority
    audit: RandomExternalAuditReceiptSignerV4
    analysis: RandomExternalAnalysisReceiptSignerV4


class _TestScoreAuthority:
    """Unit-test signer for evaluator inputs; production exposes no mint surface."""

    def __init__(self) -> None:
        self._signer = CanonicalEd25519SignerV3(
            b"S" * 32,
            domain=RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4,
        )

    @property
    def commitment(self) -> str:
        return self._signer.authority_commitment

    def verifier(self) -> AuthenticatedRandomScoreVerifierV4:
        return AuthenticatedRandomScoreVerifierV4(
            self._signer.verifier().public_key_bytes()
        )

    def mint(
        self,
        plan: RandomExternalPilotPlanV4,
        *,
        task_id: str,
        prompt_digest: str,
        started_record_digest: str,
        terminal_record_digest: str,
        ledger_manifest_digest: str,
        transcript_digest: str,
        response_digest: str,
        model_evidence_digest: str,
        executable_digest: str,
        loaded_code_digest: str,
        parser_digest: str,
        scorer_evidence_digest: str,
        outcome: AttemptOutcomeV4,
        runtime_eligible: bool,
        started_once_verified: bool,
        served_model: str,
        effort: str,
        model_identity_verified: bool,
        scorer_blinded: bool,
    ) -> AuthenticatedRandomExternalScoreV4:
        assignment = next(item for item in plan.assignments if item.task_id == task_id)
        case = next(item for item in plan.cases if item.case_id == assignment.case_id)
        response_id = "solvertaskv3_" + hashlib.sha256(task_id.encode()).hexdigest()
        score_id = "scorev3_" + hashlib.sha256((task_id + "score").encode()).hexdigest()
        parser_disposition = {
            AttemptOutcomeV4.CORRECT: ScoreDispositionV3.SCORED,
            AttemptOutcomeV4.INCORRECT: ScoreDispositionV3.SCORED,
            AttemptOutcomeV4.SOLVER_ABSTENTION: ScoreDispositionV3.ABSTAINED,
            AttemptOutcomeV4.INVALID_OUTPUT: ScoreDispositionV3.MALFORMED,
            AttemptOutcomeV4.PARSER_FAILURE: ScoreDispositionV3.MALFORMED,
            AttemptOutcomeV4.RUNNER_FAILURE: None,
        }[outcome]
        pending = "ed25519:" + "0" * 128
        draft = AuthenticatedRandomExternalScoreV4(
            plan_digest=plan.digest,
            plan_public_precommitment_digest=plan.public_precommitment_digest,
            task_map_public_precommitment_digest=_digest("task-map"),
            scoring_precommitment_digest=_digest("score-precommitment"),
            execution_public_record_digest=_digest("execution-record"),
            ledger_precommitment_digest=_digest("ledger-precommitment"),
            closed_attempt_manifest_digest=ledger_manifest_digest,
            ordinal=assignment.schedule_index,
            schedule_index=assignment.schedule_index,
            assignment_digest=assignment.digest,
            case_id=case.case_id,
            source=case.source,
            task_id=assignment.task_id,
            response_task_id=response_id,
            score_id=score_id,
            problem_digest=assignment.problem_digest,
            context_digest=assignment.context_digest,
            prompt_digest=prompt_digest,
            started_record_digest=started_record_digest,
            terminal_record_digest=terminal_record_digest,
            terminal_record_present=True,
            closed_attempt_disposition=ClosedAttemptDispositionV4.RETURNED,
            ledger_manifest_digest=ledger_manifest_digest,
            attempt_evidence_digest=_digest(f"attempt-{assignment.schedule_index}"),
            transcript_digest=transcript_digest,
            response_digest=response_digest,
            model_evidence_digest=model_evidence_digest,
            executable_digest=executable_digest,
            loaded_code_digest=loaded_code_digest,
            parser_digest=parser_digest,
            scorer_spec_digest=case.scorer_spec_digest,
            scorer_manifest_digest=plan.config.base.scorer_manifest_digest,
            scorer_evidence_digest=scorer_evidence_digest,
            runtime_policy_digest=plan.config.base.runtime_policy_digest,
            gold_authority_digest=_digest("gold-authority"),
            gold_case_binding_digest=_digest(f"gold-{case.case_id}"),
            scoring_rule_digest=EXACT_RATIONAL_SCORING_RULE_DIGEST_V3,
            score_result_digest=(
                None if parser_disposition is None else _digest(f"result-{task_id}")
            ),
            parsed_output_digest=(
                None
                if parser_disposition in {None, ScoreDispositionV3.MALFORMED}
                else _digest(f"parsed-{task_id}")
            ),
            submitted_answer=None,
            parser_disposition=parser_disposition,
            outcome=outcome,
            runtime_eligible=(runtime_eligible and parser_disposition is not None),
            started_once_verified=started_once_verified,
            served_model=served_model,
            effort=effort,
            model_identity_verified=model_identity_verified,
            scorer_blinded=scorer_blinded,
            authority_signature=pending,
        )
        return replace(
            draft,
            authority_signature=self._signer.sign(
                canonical_json(draft.unsigned_record()).encode("utf-8")
            ),
        )


def _packet(index: int) -> FrozenPacket:
    source = "internal" if index < 12 else "external"
    local = index if index < 12 else index - 12
    return FrozenPacket(
        packet_id=f"packet-{index:02d}",
        origin_case_id=f"{source}-{local:02d}",
        text=f"MEMORY {index:02d}: " + chr(65 + index % 26) * 64,
        snapshot_manifest_digest=_digest("snapshot"),
        retrieval_policy_digest=_digest("retrieval-policy"),
        query_digest=_digest(f"query-{index}"),
        format_template_digest=_digest("packet-format"),
        section_shape_digest=_digest("packet-shape"),
        age_status_vector_digest=_digest("packet-age-status"),
        source_digests=(_digest(f"memory-source-{index}"),),
        relevance_keys=(f"relevance-{index:02d}",),
        tokenizer_receipts=(
            TokenizerReceipt(
                tokenizer_id="offline-proxy-v1",
                tokenizer_artifact_digest=_digest("tokenizer-artifact"),
                token_count=32,
                token_ids_digest=_digest(f"token-ids-{index}"),
            ),
        ),
    )


def _harness() -> _Harness:
    scoring = _TestScoreAuthority()
    audit = RandomExternalAuditReceiptSignerV4(b"audit" * 8)
    analysis = RandomExternalAnalysisReceiptSignerV4(b"analysis" * 8)
    base = ThreeArmConfig(
        stage=StudyStage.PILOT,
        runtime_policy_digest=_digest("runtime-policy"),
        prompt_template_digest=SOLVER_PROMPT_TEMPLATE_DIGEST_V3,
        response_schema_digest=SOLVER_RESPONSE_SCHEMA_DIGEST_V3,
        scorer_manifest_digest=EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3,
        chronology_policy_digest=_digest("chronology-policy"),
        scoring_authority_commitment=_digest("legacy-score-authority"),
        audit_authority_commitment=audit.commitment,
        analysis_authority_commitment=analysis.commitment,
    )
    config = RandomExternalPilotConfigV4(
        authenticated_score_authority_commitment=scoring.commitment,
        base=base,
        curated_lane_failure_evidence_digest=_digest("curated-lane-failure"),
        external_selection_precommitment_digest=_digest("external-selection"),
        external_selection_precommit_anchor_evidence_digest=_digest(
            "external-selection-precommit-anchor-evidence"
        ),
        external_selection_capability_digest=_digest("external-selection-capability"),
        external_gold_audit_signed_report_digest=_digest("external-gold-signed-report"),
        internal_selection_precommitment_digest=_digest("internal-selection"),
        internal_selection_capability_digest=_digest("internal-selection-capability"),
        retrieval_protocol_digest=_digest("retrieval-protocol"),
        sham_matching_precommitment_digest=_digest("sham-matching"),
        tokenizer_attestation_digest=_digest("tokenizer-attestation"),
    )
    cells = [(domain, difficulty) for domain in MathDomain for difficulty in DifficultyBand]
    cases: list[RandomExternalPilotCaseV4] = []
    packets = [_packet(index) for index in range(24)]
    for index in range(24):
        internal = index < 12
        local = index if internal else index - 12
        source = ProblemSource.INTERNAL if internal else ProblemSource.EXTERNAL
        domain, difficulty = cells[local] if internal else (None, None)
        cases.append(
            RandomExternalPilotCaseV4(
                case_id=f"{source.value}-{local:02d}",
                source=source,
                domain=domain,
                difficulty=difficulty,
                independence_cluster_id=f"cluster-{index:02d}",
                problem_text=f"Solve the exact sealed problem {index:02d}.",
                scorer_spec_digest=_digest("scorer-spec"),
                retrieval_disposition=AutomaticRetrievalDispositionV4.SELECTED,
                retrieval_receipt_digest=_digest(f"retrieval-{index}"),
                brain_packet_id=f"packet-{index:02d}",
                sham_packet_id=f"packet-{((index // 12) * 12 + (local + 1) % 12):02d}",
                retrieval_abstention_reason_digest=None,
            )
        )
    plan = build_random_external_pilot_plan_v4(
        cases,
        packets,
        config=config,
        allocation_seed=_ALLOCATION_SEED,
    )
    return _Harness(plan=plan, scoring=scoring, audit=audit, analysis=analysis)


def _default_outcome(case_id: str, arm: StudyArm) -> AttemptOutcomeV4:
    local = int(case_id.rsplit("-", 1)[1])
    correct = {
        StudyArm.BRAIN: local < 10,
        StudyArm.SHAM: local < 6,
        StudyArm.EMPTY: local < 5,
    }[arm]
    return AttemptOutcomeV4.CORRECT if correct else AttemptOutcomeV4.INCORRECT


def _attempts(
    harness: _Harness,
    *,
    outcome_overrides: dict[tuple[str, StudyArm], AttemptOutcomeV4] | None = None,
    model_overrides: dict[int, str] | None = None,
    started_overrides: dict[int, str] | None = None,
    runtime_eligible_overrides: dict[int, bool] | None = None,
    model_identity_verified: bool = False,
) -> tuple[AuthenticatedRandomExternalScoreV4, ...]:
    outcome_overrides = outcome_overrides or {}
    model_overrides = model_overrides or {}
    started_overrides = started_overrides or {}
    runtime_eligible_overrides = runtime_eligible_overrides or {}
    result = []
    for index, assignment in enumerate(harness.plan.assignments):
        outcome = outcome_overrides.get(
            (assignment.case_id, assignment.arm),
            _default_outcome(assignment.case_id, assignment.arm),
        )
        result.append(
            harness.scoring.mint(
                harness.plan,
                task_id=assignment.task_id,
                prompt_digest=_digest(f"prompt-{index}"),
                started_record_digest=started_overrides.get(index, _digest(f"started-{index}")),
                terminal_record_digest=_digest(f"terminal-{index}"),
                ledger_manifest_digest=_digest("ledger-manifest"),
                transcript_digest=_digest(f"transcript-{index}"),
                response_digest=_digest(f"response-{index}"),
                model_evidence_digest=_digest(f"model-evidence-{index}"),
                executable_digest=_digest("executable"),
                loaded_code_digest=_digest("loaded-code"),
                parser_digest=EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3,
                scorer_evidence_digest=_digest(f"score-evidence-{index}"),
                outcome=outcome,
                runtime_eligible=runtime_eligible_overrides.get(index, True),
                started_once_verified=True,
                served_model=model_overrides.get(index, "desktop-subscription-model-unverified"),
                effort="high",
                model_identity_verified=model_identity_verified,
                scorer_blinded=True,
            )
        )
    return tuple(result)


def _audit_receipt(
    harness: _Harness,
    attempts: tuple[AuthenticatedRandomExternalScoreV4, ...],
    **overrides: bool,
):
    gates = {
        "curated_lane_segregation_verified": True,
        "external_precommit_anchor_order_verified": True,
        "external_beacon_round_replay_verified": True,
        "external_beacon_relay_agreement_verified": True,
        "external_beacon_randomness_hash_verified": True,
        "external_selection_cold_replay_verified": True,
        "external_gold_post_terminal_replay_verified": True,
        "internal_selection_cold_replay_verified": True,
        "automatic_retrieval_itt_verified": True,
        "external_semantic_labels_absent_verified": True,
        "packet_matching_verified": True,
        "contamination_clear": True,
        "brain_snapshot_read_only": True,
        "sterile_controls_verified": True,
        "ledger_complete": True,
        "schedule_integrity_verified": True,
        "scorer_integrity_verified": True,
        "cluster_independence_verified": True,
        "external_beacon_bls_verified": False,
        "served_tokenizer_identity_verified": False,
    }
    gates.update(overrides)
    return harness.audit.mint(
        harness.plan,
        result_manifest_digest=random_external_result_manifest_digest_v4(attempts),
        runtime_manifest_digest=_digest("runtime-manifest"),
        ledger_manifest_digest=_digest("ledger-manifest"),
        report_digest=_digest("audit-report"),
        **gates,
    )


def _summary(
    harness: _Harness,
    attempts: tuple[AuthenticatedRandomExternalScoreV4, ...],
    *,
    audit_overrides: dict[str, bool] | None = None,
):
    audit = _audit_receipt(harness, attempts, **(audit_overrides or {}))
    return summarize_random_external_pilot_v4(
        harness.plan,
        attempts,
        audit=audit,
        scoring_verifier=harness.scoring.verifier(),
        audit_verifier=harness.audit.verifier(),
        allocation_seed=_ALLOCATION_SEED,
        expected_plan_digest=harness.plan.digest,
        expected_preregistration_digest=harness.plan.public_precommitment_digest,
    )


def test_positive_signal_stays_an_engineering_proxy_not_a_method_helped_claim() -> None:
    harness = _harness()
    attempts = _attempts(harness)

    summary = _summary(harness, attempts)
    public = summary.to_dict()

    assert summary.analysis_valid is True
    assert summary.pilot_signal_threshold_met is True
    assert summary.interpretation is PilotInterpretationV4.POSITIVE_ENGINEERING_SIGNAL
    assert summary.brain_rate == 20 / 24
    assert summary.sham_rate == 12 / 24
    assert summary.empty_rate == 10 / 24
    assert summary.brain_minus_empty.positive_cases == 10
    assert summary.brain_minus_sham.positive_cases == 8
    assert summary.confirmatory_claim_authorized is False
    assert public["method_helped_confirmatory_claim"] is False
    assert public["claim_level"] == "engineering_proxy_descriptive"
    assert "engineering_proxy_only" in summary.limitations
    assert "served_model_identity_unverified" in summary.limitations
    assert "served_tokenizer_identity_unverified" in summary.limitations
    assert "external_beacon_bls_unverified" in summary.limitations


def test_raw_pairing_reports_external_null_labels_without_external_strata() -> None:
    harness = _harness()
    summary = _summary(harness, _attempts(harness))
    public = summary.to_dict()

    assert len(summary.paired_outcomes) == 24
    external = [item for item in summary.paired_outcomes if item.source is ProblemSource.EXTERNAL]
    assert len(external) == 12
    assert all(item.domain is None and item.difficulty is None for item in external)
    assert [item.source for item in summary.source_summaries] == list(ProblemSource)
    assert "strata" not in public
    assert "domain_summaries" not in public


@pytest.mark.parametrize(
    "disposition",
    [
        AttemptOutcomeV4.SOLVER_ABSTENTION,
        AttemptOutcomeV4.INVALID_OUTPUT,
        AttemptOutcomeV4.PARSER_FAILURE,
        AttemptOutcomeV4.RUNNER_FAILURE,
    ],
)
def test_every_noncorrect_terminal_disposition_is_retained_and_scores_zero(
    disposition: AttemptOutcomeV4,
) -> None:
    harness = _harness()
    attempts = _attempts(
        harness,
        outcome_overrides={("internal-00", StudyArm.BRAIN): disposition},
    )

    summary = _summary(harness, attempts)
    public = summary.to_dict()

    assert summary.attempt_count == 72
    assert summary.brain_rate == 19 / 24
    assert public["outcome_counts_by_arm"]["brain"][disposition.value] == 1
    row = next(item for item in summary.paired_outcomes if item.case_id == "internal-00")
    assert row.brain is disposition


def test_summary_rejects_reordered_attempt_universe() -> None:
    harness = _harness()
    canonical_attempts = _attempts(harness)
    audit = _audit_receipt(harness, canonical_attempts)
    attempts = list(canonical_attempts)
    attempts[0], attempts[1] = attempts[1], attempts[0]

    with pytest.raises(ValueError, match="exact ordered attempt universe"):
        summarize_random_external_pilot_v4(
            harness.plan,
            tuple(attempts),
            audit=audit,
            scoring_verifier=harness.scoring.verifier(),
            audit_verifier=harness.audit.verifier(),
            allocation_seed=_ALLOCATION_SEED,
            expected_plan_digest=harness.plan.digest,
            expected_preregistration_digest=harness.plan.public_precommitment_digest,
        )


def test_scoring_verifier_rejects_outcome_changed_after_signature() -> None:
    harness = _harness()
    attempt = _attempts(harness)[0]
    forged_outcome = (
        AttemptOutcomeV4.CORRECT
        if attempt.outcome is not AttemptOutcomeV4.CORRECT
        else AttemptOutcomeV4.INCORRECT
    )
    forged = replace(attempt, outcome=forged_outcome)

    assert forged.authority_signature == attempt.authority_signature
    assert canonical_json(forged.unsigned_record()) != canonical_json(attempt.unsigned_record())

    with pytest.raises(ValueError, match="signature is invalid"):
        harness.scoring.verifier().verify(harness.plan, forged)


def test_result_manifest_rejects_mixed_scoring_precommitments() -> None:
    harness = _harness()
    attempts = list(_attempts(harness))
    attempts[0] = replace(
        attempts[0],
        scoring_precommitment_digest=_digest("substituted-scoring-precommitment"),
    )

    with pytest.raises(ValueError, match="one exact precommitted campaign root"):
        random_external_result_manifest_digest_v4(tuple(attempts))


def test_summary_rejects_reused_started_record_even_when_each_receipt_is_signed() -> None:
    harness = _harness()
    shared = _digest("shared-started-record")
    attempts = _attempts(harness, started_overrides={0: shared, 1: shared})

    with pytest.raises(ValueError, match="reuse a STARTED"):
        _summary(harness, attempts)


def test_failed_core_audit_gate_invalidates_otherwise_positive_signal() -> None:
    harness = _harness()
    attempts = _attempts(harness)

    summary = _summary(
        harness,
        attempts,
        audit_overrides={"external_gold_post_terminal_replay_verified": False},
    )

    assert summary.analysis_valid is False
    assert summary.pilot_signal_threshold_met is False
    assert summary.interpretation is PilotInterpretationV4.CORE_INTEGRITY_INVALID
    assert summary.core_integrity_failures == ("integrity_audit",)


def test_audit_cannot_float_free_of_final_selection_capability() -> None:
    harness = _harness()
    attempts = _attempts(harness)
    audit = replace(
        _audit_receipt(harness, attempts),
        external_selection_capability_digest=_digest("substituted-capability"),
    )

    with pytest.raises(ValueError, match="sealed evidence universe"):
        summarize_random_external_pilot_v4(
            harness.plan,
            attempts,
            audit=audit,
            scoring_verifier=harness.scoring.verifier(),
            audit_verifier=harness.audit.verifier(),
            allocation_seed=_ALLOCATION_SEED,
            expected_plan_digest=harness.plan.digest,
            expected_preregistration_digest=harness.plan.public_precommitment_digest,
        )


def test_audit_cannot_float_free_of_prebeacon_anchor_evidence() -> None:
    harness = _harness()
    attempts = _attempts(harness)
    audit = replace(
        _audit_receipt(harness, attempts),
        external_selection_precommit_anchor_evidence_digest=_digest(
            "substituted-prebeacon-anchor-evidence"
        ),
    )

    with pytest.raises(ValueError, match="sealed evidence universe"):
        summarize_random_external_pilot_v4(
            harness.plan,
            attempts,
            audit=audit,
            scoring_verifier=harness.scoring.verifier(),
            audit_verifier=harness.audit.verifier(),
            allocation_seed=_ALLOCATION_SEED,
            expected_plan_digest=harness.plan.digest,
            expected_preregistration_digest=harness.plan.public_precommitment_digest,
        )


def test_audit_cannot_float_free_of_signed_external_gold_report() -> None:
    harness = _harness()
    attempts = _attempts(harness)
    audit = replace(
        _audit_receipt(harness, attempts),
        external_gold_audit_signed_report_digest=_digest("substituted-external-gold-signed-report"),
    )

    with pytest.raises(ValueError, match="sealed evidence universe"):
        summarize_random_external_pilot_v4(
            harness.plan,
            attempts,
            audit=audit,
            scoring_verifier=harness.scoring.verifier(),
            audit_verifier=harness.audit.verifier(),
            allocation_seed=_ALLOCATION_SEED,
            expected_plan_digest=harness.plan.digest,
            expected_preregistration_digest=harness.plan.public_precommitment_digest,
        )


def test_audit_cannot_overclaim_unimplemented_beacon_bls_authority() -> None:
    harness = _harness()
    attempts = _attempts(harness)

    with pytest.raises(ValueError, match="no BLS verifier"):
        replace(
            _audit_receipt(harness, attempts),
            external_beacon_bls_verified=True,
        )


def test_mixed_served_model_is_core_invalid_but_unverified_identity_alone_is_not() -> None:
    harness = _harness()
    consistent = _summary(harness, _attempts(harness))
    mixed_attempts = _attempts(harness, model_overrides={0: "different-model"})
    mixed = _summary(harness, mixed_attempts)

    assert consistent.analysis_valid is True
    assert "served_model_identity_unverified" in consistent.limitations
    assert mixed.analysis_valid is False
    assert "served_model_consistency" in mixed.core_integrity_failures


def test_request_and_self_report_labels_cannot_claim_served_model_identity() -> None:
    harness = _harness()

    with pytest.raises(ValueError, match="derived integrity flags"):
        _attempts(harness, model_identity_verified=True)


def test_expected_precommitment_mismatch_fails_before_analysis() -> None:
    harness = _harness()
    attempts = _attempts(harness)
    audit = _audit_receipt(harness, attempts)

    with pytest.raises(ValueError, match="expected precommitment"):
        summarize_random_external_pilot_v4(
            harness.plan,
            attempts,
            audit=audit,
            scoring_verifier=harness.scoring.verifier(),
            audit_verifier=harness.audit.verifier(),
            allocation_seed=_ALLOCATION_SEED,
            expected_plan_digest=harness.plan.digest,
            expected_preregistration_digest=_digest("wrong-precommitment"),
        )


def test_signed_summary_is_replayed_against_attempts_and_audit() -> None:
    harness = _harness()
    attempts = _attempts(harness)
    audit = _audit_receipt(harness, attempts)
    signed = harness.analysis.analyze_and_sign(
        harness.plan,
        attempts,
        audit=audit,
        scoring_verifier=harness.scoring.verifier(),
        audit_verifier=harness.audit.verifier(),
        allocation_seed=_ALLOCATION_SEED,
        expected_plan_digest=harness.plan.digest,
        expected_preregistration_digest=harness.plan.public_precommitment_digest,
    )

    harness.analysis.verifier().verify(
        harness.plan,
        signed,
        attempts,
        audit=audit,
        scoring_verifier=harness.scoring.verifier(),
        audit_verifier=harness.audit.verifier(),
        allocation_seed=_ALLOCATION_SEED,
        expected_plan_digest=harness.plan.digest,
        expected_preregistration_digest=harness.plan.public_precommitment_digest,
    )
    forged = replace(
        signed,
        authority_signature="ed25519:" + "0" * 128,
    )
    with pytest.raises(ValueError, match="signature is invalid"):
        harness.analysis.verifier().verify(
            harness.plan,
            forged,
            attempts,
            audit=audit,
            scoring_verifier=harness.scoring.verifier(),
            audit_verifier=harness.audit.verifier(),
            allocation_seed=_ALLOCATION_SEED,
            expected_plan_digest=harness.plan.digest,
            expected_preregistration_digest=harness.plan.public_precommitment_digest,
        )


def test_source_regression_is_an_explicit_signal_failure() -> None:
    harness = _harness()
    overrides = {
        (f"external-{local:02d}", StudyArm.BRAIN): AttemptOutcomeV4.INCORRECT for local in range(12)
    }
    attempts = _attempts(harness, outcome_overrides=overrides)

    summary = _summary(harness, attempts)

    assert "source_brain_minus_empty_regression" in summary.signal_threshold_failures
    assert summary.pilot_signal_threshold_met is False


def test_runtime_ineligible_attempt_remains_in_itt_but_invalidates_core_analysis() -> None:
    harness = _harness()
    attempts = _attempts(harness, runtime_eligible_overrides={0: False})

    summary = _summary(harness, attempts)

    assert summary.attempt_count == 72
    assert "runtime_eligibility" in summary.core_integrity_failures
    assert summary.analysis_valid is False
