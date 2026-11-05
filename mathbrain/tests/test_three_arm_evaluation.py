from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import replace

import pytest

from math_brain.models import canonical_json
from math_brain.three_arm_evaluation import (
    AnalysisReceiptSigner,
    AuditReceiptSigner,
    DifficultyBand,
    FrozenPacket,
    MathDomain,
    ProblemCase,
    ProblemSource,
    ScoringReceiptSigner,
    ScoringReceiptVerifier,
    StudyArm,
    StudyStage,
    ThreeArmConfig,
    TokenizerReceipt,
    build_three_arm_plan,
    summarize_three_arm,
)

_ALLOCATION_SEED = b"allocation-seed" * 4
_SCORING_SIGNER = ScoringReceiptSigner(b"scoring-authority-key" * 2)
_SCORING_VERIFIER = _SCORING_SIGNER.verifier()
_AUDIT_SIGNER = AuditReceiptSigner(b"audit-authority-key-material" * 2)
_AUDIT_VERIFIER = _AUDIT_SIGNER.verifier()
_ANALYSIS_SIGNER = AnalysisReceiptSigner(b"analysis-authority-key-material" * 2)
_ANALYSIS_VERIFIER = _ANALYSIS_SIGNER.verifier()


def _digest(seed: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


def _universe(*, stage: StudyStage = StudyStage.CONFIRMATORY):
    domains = tuple(MathDomain)
    rows: list[tuple[ProblemSource, MathDomain, DifficultyBand, int]] = []
    replicas = 4 if stage is StudyStage.CONFIRMATORY else 1
    for source in ProblemSource:
        for domain in domains:
            for difficulty in DifficultyBand:
                for replica in range(replicas):
                    rows.append((source, domain, difficulty, replica))

    packets = []
    for index, _row in enumerate(rows):
        packets.append(
            FrozenPacket(
                packet_id=f"packet-{index:03d}",
                origin_case_id=f"case-{index:03d}",
                text=f"method-{index:03d}",
                snapshot_manifest_digest=_digest("snapshot"),
                retrieval_policy_digest=_digest("retrieval-policy"),
                query_digest=_digest(f"query-{index}"),
                format_template_digest=_digest("format"),
                section_shape_digest=_digest("shape"),
                age_status_vector_digest=_digest("age-status"),
                source_digests=(_digest(f"source-{index}"),),
                relevance_keys=(f"case-key:{index:03d}",),
                tokenizer_receipts=(
                    TokenizerReceipt(
                        tokenizer_id="pinned-tokenizer",
                        tokenizer_artifact_digest=_digest("tokenizer"),
                        token_count=3,
                        token_ids_digest=_digest(f"token-ids-{index}"),
                    ),
                ),
            )
        )

    cases = []
    for index, (source, domain, difficulty, _replica) in enumerate(rows):
        sham_index = (index + 1) % len(rows)
        cases.append(
            ProblemCase(
                case_id=f"case-{index:03d}",
                source=source,
                domain=domain,
                difficulty=difficulty,
                independence_cluster_id=f"cluster-{index:03d}",
                problem_text=f"Solve the sealed problem number {index}.",
                scorer_spec_digest=_digest("scorer-int-rational"),
                brain_packet_id=f"packet-{index:03d}",
                sham_packet_id=f"packet-{sham_index:03d}",
                required_relevance_keys=(f"case-key:{index:03d}",),
            )
        )
    config = ThreeArmConfig(
        stage=stage,
        runtime_policy_digest=_digest("runtime"),
        prompt_template_digest=_digest("prompt"),
        response_schema_digest=_digest("schema"),
        scorer_manifest_digest=_digest("scorer-manifest"),
        chronology_policy_digest=_digest("external-timestamp-policy"),
        scoring_authority_commitment=_SCORING_VERIFIER.commitment,
        audit_authority_commitment=_AUDIT_VERIFIER.commitment,
        analysis_authority_commitment=_ANALYSIS_VERIFIER.commitment,
    )
    return cases, packets, config


def _plan(*, stage: StudyStage = StudyStage.CONFIRMATORY):
    cases, packets, config = _universe(stage=stage)
    return build_three_arm_plan(
        cases,
        packets,
        config=config,
        allocation_seed=_ALLOCATION_SEED,
    )


def _audit(plan, attempts, **changes):
    values = {
        "result_manifest_digest": _digest_result(attempts),
        "bank_manifest_digest": _digest("bank-manifest"),
        "runtime_manifest_digest": _digest("runtime-manifest"),
        "ledger_manifest_digest": _digest("ledger-manifest"),
        "report_digest": _digest("audit-report"),
        "external_chronology_verified": True,
        "packet_matching_verified": True,
        "contamination_clear": True,
        "brain_snapshot_read_only": True,
        "sterile_controls_verified": True,
        "ledger_complete": True,
        "schedule_integrity_verified": True,
        "scorer_integrity_verified": True,
        "cluster_independence_verified": True,
    }
    values.update(changes)
    return _AUDIT_SIGNER.mint(plan, **values)


def _digest_result(attempts) -> str:
    payload = canonical_json([attempt.commitment_record() for attempt in attempts]).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _attempts(plan, score, *, identity_verified: bool = True):
    case_index = {case.case_id: index for index, case in enumerate(plan.cases)}
    return tuple(
        _SCORING_SIGNER.mint(
            plan,
            task_id=assignment.task_id,
            prompt_digest=_digest(f"prompt-{assignment.task_id}"),
            started_record_digest=_digest(f"started-{assignment.task_id}"),
            terminal_record_digest=_digest(f"terminal-{assignment.task_id}"),
            ledger_manifest_digest=_digest("ledger-manifest"),
            transcript_digest=_digest(f"transcript-{assignment.task_id}"),
            response_digest=_digest(f"response-{assignment.task_id}"),
            model_evidence_digest=_digest(f"model-evidence-{assignment.task_id}"),
            executable_digest=_digest("solver-executable"),
            loaded_code_digest=_digest("loaded-evaluator-code"),
            parser_digest=_digest("bounded-answer-parser"),
            scorer_evidence_digest=_digest(f"score-{assignment.task_id}"),
            runtime_eligible=True,
            started_once_verified=True,
            correct=score(case_index[assignment.case_id], assignment.arm),
            served_model="served-model-receipt-name",
            effort="fixed-effort",
            model_identity_verified=identity_verified,
            scorer_blinded=True,
        )
        for assignment in plan.assignments
    )


def _remint(plan, attempt, **changes):
    values = {
        "task_id": attempt.task_id,
        "prompt_digest": attempt.prompt_digest,
        "started_record_digest": attempt.started_record_digest,
        "terminal_record_digest": attempt.terminal_record_digest,
        "ledger_manifest_digest": attempt.ledger_manifest_digest,
        "transcript_digest": attempt.transcript_digest,
        "response_digest": attempt.response_digest,
        "model_evidence_digest": attempt.model_evidence_digest,
        "executable_digest": attempt.executable_digest,
        "loaded_code_digest": attempt.loaded_code_digest,
        "parser_digest": attempt.parser_digest,
        "scorer_evidence_digest": attempt.scorer_evidence_digest,
        "runtime_eligible": attempt.runtime_eligible,
        "started_once_verified": attempt.started_once_verified,
        "correct": attempt.correct,
        "served_model": attempt.served_model,
        "effort": attempt.effort,
        "model_identity_verified": attempt.model_identity_verified,
        "scorer_blinded": attempt.scorer_blinded,
    }
    values.update(changes)
    return _SCORING_SIGNER.mint(plan, **values)


def _summarize(plan, attempts, *, audit=None):
    receipt = audit if audit is not None else _audit(plan, attempts)
    return summarize_three_arm(
        plan,
        attempts,
        audit=receipt,
        scoring_verifier=_SCORING_VERIFIER,
        audit_verifier=_AUDIT_VERIFIER,
        allocation_seed=_ALLOCATION_SEED,
        expected_plan_digest=plan.digest,
        expected_preregistration_digest=plan.public_precommitment_digest,
    )


def test_confirmatory_plan_has_one_balanced_fresh_triplet_per_case() -> None:
    plan = _plan()
    assert len(plan.cases) == 96
    assert len(plan.assignments) == 288
    by_case = defaultdict(list)
    for assignment in plan.assignments:
        by_case[assignment.case_id].append(assignment)
    for _case_id, assignments in by_case.items():
        assert {item.arm for item in assignments} == set(StudyArm)
        tasks = [plan.solver_task(item.task_id) for item in assignments]
        assert len({task.problem_text for task in tasks}) == 1
        assert next(
            task
            for task, item in zip(tasks, assignments, strict=True)
            if item.arm is StudyArm.EMPTY
        ).brain_context == ""

    order_counts = defaultdict(Counter)
    case_by_id = {case.case_id: case for case in plan.cases}
    for case_id, assignments in by_case.items():
        case = case_by_id[case_id]
        order = tuple(
            item.arm.value for item in sorted(assignments, key=lambda item: item.within_case_order)
        )
        order_counts[(case.source, case.domain)][order] += 1
    assert all(len(counts) == 6 and set(counts.values()) == {2} for counts in order_counts.values())

    cell_first_arms = defaultdict(Counter)
    cell_orders = defaultdict(set)
    for case_id, assignments in by_case.items():
        case = case_by_id[case_id]
        order = tuple(
            item.arm
            for item in sorted(assignments, key=lambda item: item.within_case_order)
        )
        cell = (case.source, case.domain, case.difficulty)
        cell_first_arms[cell][order[0]] += 1
        cell_orders[cell].add(order)
    assert all(sorted(counts.values()) == [1, 1, 2] for counts in cell_first_arms.values())
    assert all(len(orders) == 4 for orders in cell_orders.values())


def test_packet_matching_requires_derangement_disjointness_and_exact_receipts() -> None:
    cases, packets, config = _universe()
    bad_derangement = list(cases)
    bad_derangement[0] = replace(bad_derangement[0], sham_packet_id="packet-002")
    with pytest.raises(ValueError, match=r"differ|derangement|every packet"):
        build_three_arm_plan(
            bad_derangement,
            packets,
            config=config,
            allocation_seed=b"x" * 32,
        )

    changed_packets = list(packets)
    changed_receipt = replace(
        changed_packets[1].tokenizer_receipts[0],
        token_count=4,
    )
    changed_packets[1] = replace(
        changed_packets[1], tokenizer_receipts=(changed_receipt,)
    )
    with pytest.raises(ValueError, match="not exactly matched"):
        build_three_arm_plan(
            cases,
            changed_packets,
            config=config,
            allocation_seed=b"x" * 32,
        )


def test_duplicate_independence_cluster_and_tampered_plan_fail_closed() -> None:
    cases, packets, config = _universe()
    duplicate = list(cases)
    duplicate[1] = replace(
        duplicate[1], independence_cluster_id=duplicate[0].independence_cluster_id
    )
    with pytest.raises(ValueError, match="independence clusters"):
        build_three_arm_plan(
            duplicate,
            packets,
            config=config,
            allocation_seed=b"x" * 32,
        )

    plan = _plan()
    tampered_packet = replace(plan.packets[0], text="altered-000")
    with pytest.raises(ValueError, match="packet universe differs"):
        replace(plan, packets=(tampered_packet, *plan.packets[1:]))


def test_benefit_requires_brain_to_beat_both_controls_and_all_gates() -> None:
    plan = _plan()
    strong = _attempts(
        plan,
        lambda case_index, arm: arm is StudyArm.BRAIN
        or (arm is not StudyArm.BRAIN and case_index % 2 == 0),
    )
    summary = _summarize(plan, strong)
    assert summary.brain_rate == 1
    assert summary.sham_rate == 0.5
    assert summary.empty_rate == 0.5
    assert summary.brain_minus_sham.mean_delta == 0.5
    assert summary.brain_minus_empty.mean_delta == 0.5
    assert summary.failed_gates == ()
    assert summary.method_helped is True

    context_only = _attempts(
        plan,
        lambda case_index, arm: arm is not StudyArm.EMPTY or case_index % 2 == 0,
    )
    context_summary = _summarize(plan, context_only)
    assert context_summary.brain_minus_empty.mean_delta == 0.5
    assert context_summary.brain_minus_sham.mean_delta == 0
    assert "brain_minus_sham_minimum_effect" in context_summary.failed_gates
    assert context_summary.method_helped is False

    failed_audit = _summarize(
        plan, strong, audit=_audit(plan, strong, contamination_clear=False)
    )
    assert "integrity_audit" in failed_audit.failed_gates
    assert failed_audit.method_helped is False


def test_incomplete_duplicate_and_unverified_attempts_cannot_support_claim() -> None:
    plan = _plan()
    attempts = _attempts(
        plan,
        lambda case_index, arm: arm is StudyArm.BRAIN
        or (arm is not StudyArm.BRAIN and case_index % 2 == 0),
    )
    with pytest.raises(ValueError, match="exact ordered"):
        _summarize(plan, attempts[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        _summarize(plan, (*attempts[:-1], attempts[0]))

    unverified = _attempts(
        plan,
        lambda case_index, arm: arm is StudyArm.BRAIN
        or (arm is not StudyArm.BRAIN and case_index % 2 == 0),
        identity_verified=False,
    )
    summary = _summarize(plan, unverified)
    assert "served_model_identity" in summary.failed_gates
    assert summary.method_helped is False


def test_pilot_is_never_promoted_to_confirmatory_evidence() -> None:
    plan = _plan(stage=StudyStage.PILOT)
    attempts = _attempts(
        plan,
        lambda case_index, arm: arm is StudyArm.BRAIN
        or (arm is not StudyArm.BRAIN and case_index % 2 == 0),
    )
    summary = _summarize(plan, attempts)
    assert "confirmatory_stage" in summary.failed_gates
    assert "supported_stratum_regression" not in summary.failed_gates
    assert summary.worst_supported_stratum_brain_minus_empty is None
    assert summary.method_helped is False


def test_pilot_balances_every_arm_in_every_position_per_source_domain() -> None:
    cases, packets, config = _universe(stage=StudyStage.PILOT)
    for seed_index in range(32):
        plan = build_three_arm_plan(
            cases,
            packets,
            config=config,
            allocation_seed=hashlib.sha256(f"pilot-latin-{seed_index}".encode()).digest(),
        )
        case_by_id = {case.case_id: case for case in plan.cases}
        blocks = defaultdict(list)
        for assignment in plan.assignments:
            case = case_by_id[assignment.case_id]
            blocks[(case.source, case.domain)].append(assignment)

        assert len(blocks) == len(ProblemSource) * len(MathDomain)
        for assignments in blocks.values():
            by_case = defaultdict(list)
            for assignment in assignments:
                by_case[assignment.case_id].append(assignment)
            assert len(by_case) == len(DifficultyBand)
            orders = []
            for case_assignments in by_case.values():
                order = tuple(
                    item.arm
                    for item in sorted(case_assignments, key=lambda item: item.within_case_order)
                )
                orders.append(order)
            assert len(set(orders)) == 3
            for position in range(3):
                assert Counter(order[position] for order in orders) == Counter(
                    {arm: 1 for arm in StudyArm}
                )


def test_authenticated_receipts_reject_score_audit_and_order_tampering() -> None:
    plan = _plan()
    attempts = _attempts(
        plan,
        lambda case_index, arm: arm is StudyArm.BRAIN
        or (arm is not StudyArm.BRAIN and case_index % 2 == 0),
    )

    tampered = list(attempts)
    brain_index = next(
        index
        for index, assignment in enumerate(plan.assignments)
        if assignment.arm is StudyArm.BRAIN
    )
    object.__setattr__(tampered[brain_index], "correct", False)
    with pytest.raises(ValueError, match="signature"):
        _summarize(plan, tuple(tampered))

    attempts = _attempts(
        plan,
        lambda case_index, arm: arm is StudyArm.BRAIN
        or (arm is not StudyArm.BRAIN and case_index % 2 == 0),
    )
    failing_audit = _audit(plan, attempts, contamination_clear=False)
    object.__setattr__(failing_audit, "contamination_clear", True)
    with pytest.raises(ValueError, match="signature"):
        _summarize(plan, attempts, audit=failing_audit)

    with pytest.raises(ValueError, match="exact ordered"):
        _summarize(plan, tuple(reversed(attempts)))


def test_external_commitments_seed_and_authority_are_hard_requirements() -> None:
    plan = _plan()
    attempts = _attempts(plan, lambda case_index, arm: case_index % 2 == 0)
    audit = _audit(plan, attempts)
    common = {
        "audit": audit,
        "scoring_verifier": _SCORING_VERIFIER,
        "audit_verifier": _AUDIT_VERIFIER,
        "allocation_seed": _ALLOCATION_SEED,
        "expected_plan_digest": plan.digest,
        "expected_preregistration_digest": plan.public_precommitment_digest,
    }
    with pytest.raises(ValueError, match="externally expected"):
        summarize_three_arm(
            plan,
            attempts,
            **{**common, "expected_plan_digest": _digest("wrong-plan")},
        )
    with pytest.raises(ValueError, match="revealed seed"):
        summarize_three_arm(
            plan,
            attempts,
            **{**common, "allocation_seed": b"wrong-seed" * 4},
        )
    wrong_verifier = ScoringReceiptSigner(b"wrong-scoring-authority" * 2).verifier()
    with pytest.raises(ValueError, match="verifier differs"):
        summarize_three_arm(
            plan,
            attempts,
            **{**common, "scoring_verifier": wrong_verifier},
        )


def test_public_key_commitment_and_signature_cannot_be_split() -> None:
    plan = _plan()
    attempts = _attempts(plan, lambda case_index, arm: case_index % 2 == 0)
    audit = _audit(plan, attempts)
    local_verifier = ScoringReceiptVerifier(_SCORING_VERIFIER.public_key_bytes())
    attacker = ScoringReceiptSigner(b"attacker-private-key-material" * 2)
    # Recreate the original split-state attack: attach an attacker key object without
    # changing the bytes that define the externally pinned verifier commitment.
    with pytest.raises(AttributeError):
        object.__setattr__(local_verifier, "_public_key", attacker.verifier())
    forged = list(attempts)
    forged[0] = replace(
        forged[0],
        authority_signature=attacker._sign(forged[0].unsigned_record()),
    )
    with pytest.raises(ValueError, match="signature"):
        summarize_three_arm(
            plan,
            tuple(forged),
            audit=audit,
            scoring_verifier=local_verifier,
            audit_verifier=_AUDIT_VERIFIER,
            allocation_seed=_ALLOCATION_SEED,
            expected_plan_digest=plan.digest,
            expected_preregistration_digest=plan.public_precommitment_digest,
        )


def test_sham_harm_and_external_null_cannot_masquerade_as_general_benefit() -> None:
    plan = _plan()
    sham_harm = _attempts(
        plan,
        lambda case_index, arm: arm is StudyArm.BRAIN
        or (arm is StudyArm.EMPTY and case_index % 2 == 0),
    )
    sham_harm_summary = _summarize(plan, sham_harm)
    assert sham_harm_summary.brain_minus_sham.mean_delta == 1
    assert sham_harm_summary.brain_minus_empty.mean_delta == 0.5
    assert "sham_specificity_loss" in sham_harm_summary.failed_gates
    assert "source_sham_specificity_loss" in sham_harm_summary.failed_gates
    assert sham_harm_summary.method_helped is False

    def internal_only(case_index, arm):
        case = plan.cases[case_index]
        baseline = case_index % 2 == 0
        if case.source is ProblemSource.INTERNAL and arm is StudyArm.BRAIN:
            return True
        return baseline

    pooled_gain = _attempts(plan, internal_only)
    pooled_summary = _summarize(plan, pooled_gain)
    assert pooled_summary.brain_minus_empty.mean_delta == 0.25
    external = next(
        item for item in pooled_summary.source_summaries if item.source is ProblemSource.EXTERNAL
    )
    assert external.brain_minus_empty.mean_delta == 0
    assert "source_brain_minus_empty_minimum_effect" in pooled_summary.failed_gates
    assert "source_brain_minus_sham_minimum_effect" in pooled_summary.failed_gates
    assert pooled_summary.method_helped is False


def test_domain_and_pilot_composition_are_typed_and_exact() -> None:
    cases, _packets, _config = _universe()
    with pytest.raises(ValueError, match="MathDomain"):
        replace(cases[0], domain="calculus")

    pilot_cases, pilot_packets, pilot_config = _universe(stage=StudyStage.PILOT)
    with pytest.raises(ValueError, match="exactly 24"):
        build_three_arm_plan(
            pilot_cases[:-1],
            pilot_packets[:-1],
            config=pilot_config,
            allocation_seed=_ALLOCATION_SEED,
        )


def test_runtime_artifact_mismatch_fails_even_when_individually_signed() -> None:
    plan = _plan()
    attempts = list(
        _attempts(
            plan,
            lambda case_index, arm: arm is StudyArm.BRAIN
            or (arm is not StudyArm.BRAIN and case_index % 2 == 0),
        )
    )
    attempts[0] = _remint(
        plan,
        attempts[0],
        executable_digest=_digest("different-executable"),
        loaded_code_digest=_digest("different-loaded-code"),
        parser_digest=_digest("different-parser"),
    )
    attempts_tuple = tuple(attempts)
    summary = _summarize(plan, attempts_tuple, audit=_audit(plan, attempts_tuple))
    assert "runtime_artifact_consistency" in summary.failed_gates
    assert summary.method_helped is False


def test_final_decision_requires_analysis_signature_and_rejects_relabeling() -> None:
    plan = _plan()
    attempts = _attempts(plan, lambda case_index, arm: case_index % 2 == 0)
    summary = _summarize(plan, attempts)
    audit = _audit(plan, attempts)
    analysis_args = {
        "audit": audit,
        "scoring_verifier": _SCORING_VERIFIER,
        "audit_verifier": _AUDIT_VERIFIER,
        "allocation_seed": _ALLOCATION_SEED,
        "expected_plan_digest": plan.digest,
        "expected_preregistration_digest": plan.public_precommitment_digest,
    }
    signed = _ANALYSIS_SIGNER.analyze_and_sign(plan, attempts, **analysis_args)
    _ANALYSIS_VERIFIER.verify(plan, signed, attempts, **analysis_args)

    object.__setattr__(signed.summary, "failed_gates", ())
    object.__setattr__(signed.summary, "method_helped", True)
    with pytest.raises(ValueError, match="evidence replay"):
        _ANALYSIS_VERIFIER.verify(plan, signed, attempts, **analysis_args)

    # Even a holder of the analysis signing key cannot relabel a decision that the public
    # verifier independently replays from the signed attempt and audit evidence.
    honest = _ANALYSIS_SIGNER.analyze_and_sign(plan, attempts, **analysis_args)
    forged_summary = replace(summary, failed_gates=(), method_helped=True)
    unsigned_forgery = replace(honest, summary=forged_summary)
    signed_forgery = replace(
        unsigned_forgery,
        authority_signature=_ANALYSIS_SIGNER._sign(unsigned_forgery.unsigned_record()),
    )
    with pytest.raises(ValueError, match="evidence replay"):
        _ANALYSIS_VERIFIER.verify(plan, signed_forgery, attempts, **analysis_args)


def test_verifier_methods_cannot_be_shadowed_on_exact_instances() -> None:
    local_scoring = ScoringReceiptVerifier(_SCORING_VERIFIER.public_key_bytes())
    with pytest.raises(AttributeError):
        object.__setattr__(local_scoring, "verify", lambda *_args, **_kwargs: None)
    with pytest.raises(AttributeError):
        object.__setattr__(local_scoring, "_verify", lambda *_args, **_kwargs: None)


def test_started_and_terminal_ledger_records_must_be_disjoint() -> None:
    plan = _plan()
    attempts = list(_attempts(plan, lambda case_index, arm: case_index % 2 == 0))
    attempts[0] = _remint(
        plan,
        attempts[0],
        terminal_record_digest=attempts[0].started_record_digest,
    )
    attempts_tuple = tuple(attempts)
    with pytest.raises(ValueError, match="must be disjoint"):
        _summarize(plan, attempts_tuple, audit=_audit(plan, attempts_tuple))
