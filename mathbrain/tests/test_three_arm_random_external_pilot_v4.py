from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from dataclasses import replace
from itertools import permutations

import pytest

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
from math_brain.three_arm_random_external_pilot_v4 import (
    AutomaticRetrievalDispositionV4,
    RandomExternalPilotCaseV4,
    RandomExternalPilotConfigV4,
    build_random_external_pilot_plan_v4,
    verify_random_external_pilot_plan_v4,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _base_config() -> ThreeArmConfig:
    return ThreeArmConfig(
        stage=StudyStage.PILOT,
        runtime_policy_digest=_digest("runtime"),
        prompt_template_digest=_digest("prompt"),
        response_schema_digest=_digest("response"),
        scorer_manifest_digest=_digest("scorer"),
        chronology_policy_digest=_digest("chronology"),
        scoring_authority_commitment=_digest("scoring-authority"),
        audit_authority_commitment=_digest("audit-authority"),
        analysis_authority_commitment=_digest("analysis-authority"),
    )


def _config() -> RandomExternalPilotConfigV4:
    return RandomExternalPilotConfigV4(
        authenticated_score_authority_commitment=_digest(
            "authenticated-score-authority"
        ),
        base=_base_config(),
        curated_lane_failure_evidence_digest=_digest("curated-failed"),
        external_selection_precommitment_digest=_digest("external-selection"),
        external_selection_precommit_anchor_evidence_digest=_digest(
            "external-selection-precommit-anchor-evidence"
        ),
        external_selection_capability_digest=_digest("external-selection-capability"),
        external_gold_audit_signed_report_digest=_digest("external-gold-signed-report"),
        internal_selection_precommitment_digest=_digest("internal-selection"),
        internal_selection_capability_digest=_digest("internal-selection-capability"),
        retrieval_protocol_digest=_digest("retrieval-protocol"),
        sham_matching_precommitment_digest=_digest("sham-match"),
        tokenizer_attestation_digest=_digest("tokenizer"),
    )


def _packet(index: int, *, token_count: int = 32) -> FrozenPacket:
    return FrozenPacket(
        packet_id=f"packet-{index:02d}",
        origin_case_id=(f"internal-{index:02d}" if index < 12 else f"external-{index - 12:02d}"),
        text=f"MEMORY PACKET {index:02d}: " + (chr(65 + index % 26) * 64),
        snapshot_manifest_digest=_digest("snapshot"),
        retrieval_policy_digest=_digest("retrieval-policy"),
        query_digest=_digest(f"query-{index}"),
        format_template_digest=_digest("packet-format"),
        section_shape_digest=_digest("packet-shape"),
        age_status_vector_digest=_digest("age-status"),
        source_digests=(_digest(f"packet-source-{index}"),),
        relevance_keys=(f"key-{index:02d}",),
        tokenizer_receipts=(
            TokenizerReceipt(
                tokenizer_id="proxy-tokenizer-v1",
                tokenizer_artifact_digest=_digest("tokenizer-artifact"),
                token_count=token_count,
                token_ids_digest=_digest(f"token-ids-{index}"),
            ),
        ),
    )


def _cases_and_packets(
    *, abstained: frozenset[int] = frozenset()
) -> tuple[list[RandomExternalPilotCaseV4], list[FrozenPacket]]:
    selected = [index for index in range(24) if index not in abstained]
    next_selected = {
        index: selected[(offset + 1) % len(selected)] for offset, index in enumerate(selected)
    }
    packets = [_packet(index) for index in selected]
    cases: list[RandomExternalPilotCaseV4] = []
    internal_cells = [
        (domain, difficulty) for domain in MathDomain for difficulty in DifficultyBand
    ]
    for index in range(24):
        internal = index < 12
        is_abstained = index in abstained
        domain, difficulty = internal_cells[index] if internal else (None, None)
        cases.append(
            RandomExternalPilotCaseV4(
                case_id=(f"internal-{index:02d}" if internal else f"external-{index - 12:02d}"),
                source=(ProblemSource.INTERNAL if internal else ProblemSource.EXTERNAL),
                domain=domain,
                difficulty=difficulty,
                independence_cluster_id=f"cluster-{index:02d}",
                problem_text=f"Solve exact problem number {index:02d}.",
                scorer_spec_digest=_digest("scorer-spec"),
                retrieval_disposition=(
                    AutomaticRetrievalDispositionV4.ABSTAINED
                    if is_abstained
                    else AutomaticRetrievalDispositionV4.SELECTED
                ),
                retrieval_receipt_digest=_digest(f"retrieval-receipt-{index}"),
                brain_packet_id=None if is_abstained else f"packet-{index:02d}",
                sham_packet_id=(None if is_abstained else f"packet-{next_selected[index]:02d}"),
                retrieval_abstention_reason_digest=(
                    _digest(f"abstention-{index}") if is_abstained else None
                ),
            )
        )
    return cases, packets


def _plan(*, abstained: frozenset[int] = frozenset()):
    cases, packets = _cases_and_packets(abstained=abstained)
    return build_random_external_pilot_plan_v4(
        cases,
        packets,
        config=_config(),
        allocation_seed=b"a" * 32,
    )


def test_builds_24_independent_cases_and_72_fresh_slots() -> None:
    plan = _plan()

    assert len(plan.cases) == 24
    assert len(plan.assignments) == 72
    assert len({case.independence_cluster_id for case in plan.cases}) == 24
    assert len({assignment.task_id for assignment in plan.assignments}) == 72
    assert Counter(case.source for case in plan.cases) == {
        ProblemSource.INTERNAL: 12,
        ProblemSource.EXTERNAL: 12,
    }
    assert all(
        case.domain is None and case.difficulty is None
        for case in plan.cases
        if case.source is ProblemSource.EXTERNAL
    )


def test_each_source_uses_every_arm_order_exactly_twice() -> None:
    plan = _plan()
    case_by_id = {case.case_id: case for case in plan.cases}
    grouped = defaultdict(list)
    for assignment in plan.assignments:
        grouped[assignment.case_id].append(assignment)
    counts = {source: Counter() for source in ProblemSource}
    for case_id, assignments in grouped.items():
        order = tuple(
            item.arm for item in sorted(assignments, key=lambda item: item.within_case_order)
        )
        counts[case_by_id[case_id].source][order] += 1
    expected = Counter({order: 2 for order in permutations(tuple(StudyArm))})
    assert counts == {
        ProblemSource.INTERNAL: expected,
        ProblemSource.EXTERNAL: expected,
    }


def test_public_precommitment_tells_the_unstratified_post_failure_truth() -> None:
    public = _plan().public_precommitment()

    assert public["curated_external_lane_pooled"] is False
    assert public["external_semantic_balance_claimed"] is False
    assert public["internal_semantic_balance"] == ("one_case_per_domain_difficulty_cell")
    assert public["relevance_audit_gates_selection"] is False
    assert public["source_service_order_balance"] == ("each_of_six_orders_exactly_twice")


def test_plan_binds_final_upstream_capabilities_not_only_precommitments() -> None:
    plan = _plan()
    config = plan.config.to_dict()

    assert config["authenticated_score_authority_commitment"] == _digest(
        "authenticated-score-authority"
    )
    assert config["external_selection_capability_digest"] == _digest(
        "external-selection-capability"
    )
    assert config["external_selection_precommit_anchor_evidence_digest"] == _digest(
        "external-selection-precommit-anchor-evidence"
    )
    assert config["external_gold_audit_signed_report_digest"] == _digest(
        "external-gold-signed-report"
    )
    assert config["internal_selection_capability_digest"] == _digest(
        "internal-selection-capability"
    )
    cases, packets = _cases_and_packets()
    changed = build_random_external_pilot_plan_v4(
        cases,
        packets,
        config=replace(
            plan.config,
            external_selection_capability_digest=_digest("alternate-capability"),
        ),
        allocation_seed=b"a" * 32,
    )
    assert changed.digest != plan.digest

    changed_anchor = build_random_external_pilot_plan_v4(
        cases,
        packets,
        config=replace(
            plan.config,
            external_selection_precommit_anchor_evidence_digest=_digest(
                "alternate-prebeacon-anchor-evidence"
            ),
        ),
        allocation_seed=b"a" * 32,
    )
    assert changed_anchor.digest != plan.digest


def test_authenticated_score_authority_cannot_alias_legacy_scoring_key() -> None:
    config = _config()

    with pytest.raises(ValueError, match="distinct from every legacy authority"):
        replace(
            config,
            authenticated_score_authority_commitment=(
                config.base.scoring_authority_commitment
            ),
        )


def test_automatic_retrieval_abstention_is_retained_as_itt_case() -> None:
    plan = _plan(abstained=frozenset({0, 12}))
    public = plan.public_precommitment()

    assert public["automatic_retrieval_abstained_case_count"] == 2
    assert public["automatic_retrieval_selected_case_count"] == 22
    for case in plan.cases:
        if case.retrieval_disposition is AutomaticRetrievalDispositionV4.ABSTAINED:
            assignments = [item for item in plan.assignments if item.case_id == case.case_id]
            assert {item.packet_id for item in assignments} == {None}
            assert {plan.solver_task(item.task_id).brain_context for item in assignments} == {""}


def test_random_external_case_rejects_fabricated_semantic_labels() -> None:
    cases, _packets = _cases_and_packets()
    external = cases[12]

    with pytest.raises(ValueError, match="must not invent semantic labels"):
        replace(external, domain=MathDomain.ALGEBRA)


def test_internal_case_requires_audited_semantic_labels() -> None:
    cases, _packets = _cases_and_packets()

    with pytest.raises(ValueError, match="require exact audited semantic labels"):
        replace(cases[0], domain=None)


def test_builder_rejects_duplicate_independence_cluster() -> None:
    cases, packets = _cases_and_packets()
    cases[1] = replace(cases[1], independence_cluster_id=cases[0].independence_cluster_id)

    with pytest.raises(ValueError, match="repeats a independence cluster"):
        build_random_external_pilot_plan_v4(
            cases,
            packets,
            config=_config(),
            allocation_seed=b"a" * 32,
        )


def test_builder_rejects_topup_or_partial_source_lane() -> None:
    cases, packets = _cases_and_packets()

    with pytest.raises(ValueError, match="exactly 24"):
        build_random_external_pilot_plan_v4(
            cases[:-1],
            packets[:-1],
            config=_config(),
            allocation_seed=b"a" * 32,
        )


def test_brain_and_sham_must_have_exact_matching_shape() -> None:
    cases, packets = _cases_and_packets()
    packets[1] = _packet(1, token_count=33)

    with pytest.raises(ValueError, match="not byte/token-shape matched"):
        build_random_external_pilot_plan_v4(
            cases,
            packets,
            config=_config(),
            allocation_seed=b"a" * 32,
        )


def test_brain_and_sham_must_not_reuse_a_source_digest() -> None:
    cases, packets = _cases_and_packets()
    packets[1] = replace(packets[1], source_digests=packets[0].source_digests)

    with pytest.raises(ValueError, match="reuse a source"):
        build_random_external_pilot_plan_v4(
            cases,
            packets,
            config=_config(),
            allocation_seed=b"a" * 32,
        )


def test_later_semantic_relevance_labels_do_not_gate_the_plan() -> None:
    cases, packets = _cases_and_packets()
    overlapping = [
        replace(packet, relevance_keys=("shared-post-selection-label",)) for packet in packets
    ]

    plan = build_random_external_pilot_plan_v4(
        cases,
        overlapping,
        config=_config(),
        allocation_seed=b"a" * 32,
    )

    assert len(plan.packets) == len(overlapping)


@pytest.mark.parametrize(
    ("field_name", "donor_index"),
    [("brain_packet_id", 0), ("sham_packet_id", 2)],
)
def test_selected_packet_mappings_must_be_one_to_one(
    field_name: str,
    donor_index: int,
) -> None:
    cases, packets = _cases_and_packets()
    cases[1] = replace(
        cases[1],
        **{field_name: getattr(cases[donor_index], field_name)},
    )

    with pytest.raises(ValueError, match="must be one-to-one"):
        build_random_external_pilot_plan_v4(
            cases,
            packets,
            config=_config(),
            allocation_seed=b"a" * 32,
        )


def test_config_cannot_claim_external_semantic_balance_or_confirmation() -> None:
    config = _config()

    with pytest.raises(ValueError, match="overclaims"):
        replace(config, external_semantic_balance_claimed=True)
    with pytest.raises(ValueError, match="overclaims"):
        replace(config, confirmatory_claim_authorized=True)


def test_cold_replay_requires_exact_seed_and_external_digest() -> None:
    plan = _plan()
    verify_random_external_pilot_plan_v4(
        plan,
        allocation_seed=b"a" * 32,
        expected_public_precommitment_digest=plan.public_precommitment_digest,
    )

    with pytest.raises(ValueError, match="does not rebuild"):
        verify_random_external_pilot_plan_v4(
            plan,
            allocation_seed=b"b" * 32,
            expected_public_precommitment_digest=plan.public_precommitment_digest,
        )
    with pytest.raises(ValueError, match="externally expected"):
        verify_random_external_pilot_plan_v4(
            plan,
            allocation_seed=b"a" * 32,
            expected_public_precommitment_digest=_digest("decoy-precommitment"),
        )


def test_same_seed_replays_and_different_seed_changes_assignment() -> None:
    cases, packets = _cases_and_packets()
    first = build_random_external_pilot_plan_v4(
        cases, packets, config=_config(), allocation_seed=b"a" * 32
    )
    replay = build_random_external_pilot_plan_v4(
        tuple(reversed(cases)),
        tuple(reversed(packets)),
        config=_config(),
        allocation_seed=b"a" * 32,
    )
    changed = build_random_external_pilot_plan_v4(
        cases, packets, config=_config(), allocation_seed=b"b" * 32
    )

    assert replay == first
    assert changed != first
    assert changed.case_universe_digest == first.case_universe_digest
    assert changed.schedule_digest != first.schedule_digest
