from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from itertools import product
from pathlib import Path

import pytest

from math_brain.brain_corpus_v3 import (
    BRAIN_PACKET_FORMAT_SHA256_V3,
    BrainCorpusManifestV3,
    acquire_frozen_brain_corpus_v3,
)
from math_brain.domain_free_retrieval_v4 import (
    DOMAIN_FREE_CASE_MANIFEST_SCHEMA_V4,
    DomainFreeRetrievalPolicyV4,
    DomainFreeRetrievalPreparationV4,
    capture_current_domain_free_retrieval_execution_manifest_v4,
    prepare_domain_free_retrieval_v4,
)
from math_brain.models import canonical_json
from math_brain.random_external_sham_materialization_v4 import (
    RandomExternalCaseDescriptorV4,
    RandomExternalMaterializationError,
    RandomExternalMaterializationReplayV4,
    RandomExternalShamInfeasible,
    bind_random_external_materialization_to_plan_v4,
    build_random_external_sham_materialization_v4,
    cold_replay_random_external_sham_materialization_v4,
)
from math_brain.random_external_tokenizer_attestation_v4 import PacketPaddingPolicyV4
from math_brain.three_arm_evaluation import (
    DifficultyBand,
    MathDomain,
    ProblemSource,
    StudyStage,
    ThreeArmConfig,
)
from math_brain.three_arm_random_external_pilot_v4 import (
    AutomaticRetrievalDispositionV4,
    RandomExternalPilotConfigV4,
    build_random_external_pilot_plan_v4,
)
from math_brain.three_arm_tokenizer_attestation_v3 import (
    OfflineTokenizerBackendV3,
    TokenizerBackendPairV3,
    TokenizerEvidenceClassV3,
    TokenizerSpecV3,
    TokenizerSuiteManifestV3,
    bind_tokenizer_suite_v3,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXECUTION_MANIFEST = capture_current_domain_free_retrieval_execution_manifest_v4(
    git_commit_sha1="3" * 40,
    git_tree_sha1="4" * 40,
)
MATCHING_SEED = b"v4-source-disjoint-matching-seed" * 2


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _byte_primary(text: str) -> list[int]:
    return list(text.encode("utf-8"))


def _byte_verifier(text: str) -> tuple[int, ...]:
    return tuple(text.encode("utf-8"))


@pytest.fixture(scope="module")
def corpus() -> BrainCorpusManifestV3:
    return acquire_frozen_brain_corpus_v3(REPOSITORY_ROOT)


@pytest.fixture(scope="module")
def retrieval_policy() -> DomainFreeRetrievalPolicyV4:
    return DomainFreeRetrievalPolicyV4(EXECUTION_MANIFEST.content_digest)


@pytest.fixture(scope="module")
def tokenizer_suite():
    spec = TokenizerSpecV3(
        tokenizer_id="byte-v4-materializer-test",
        encoding_name="byte-v4-materializer-encoding",
        vocabulary_artifact_digest=_digest("byte-v4-materializer-vocabulary"),
        encode_options_digest=_digest("byte-v4-materializer-options"),
        primary_implementation_id="byte-v4-materializer-primary",
        primary_implementation_artifact_digest=_digest("byte-primary-artifact"),
        verifier_implementation_id="byte-v4-materializer-verifier",
        verifier_implementation_artifact_digest=_digest("byte-verifier-artifact"),
    )
    pair = TokenizerBackendPairV3(
        primary=OfflineTokenizerBackendV3(
            tokenizer_id=spec.tokenizer_id,
            implementation_id=spec.primary_implementation_id,
            implementation_artifact_digest=spec.primary_implementation_artifact_digest,
            vocabulary_artifact_digest=spec.vocabulary_artifact_digest,
            encode_options_digest=spec.encode_options_digest,
            encoder=_byte_primary,
        ),
        verifier=OfflineTokenizerBackendV3(
            tokenizer_id=spec.tokenizer_id,
            implementation_id=spec.verifier_implementation_id,
            implementation_artifact_digest=spec.verifier_implementation_artifact_digest,
            vocabulary_artifact_digest=spec.vocabulary_artifact_digest,
            encode_options_digest=spec.encode_options_digest,
            encoder=_byte_verifier,
        ),
    )
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="desktop-app-requested-model",
        evidence_class=TokenizerEvidenceClassV3.ENGINEERING_PROXY,
        tokenizers=(spec,),
        engineering_proxy_basis_digest=_digest("offline-only-proxy-evidence"),
    )
    return bind_tokenizer_suite_v3(
        manifest,
        (pair,),
        expected_manifest_digest=manifest.digest,
    )


@pytest.fixture(scope="module")
def padding_policy() -> PacketPaddingPolicyV4:
    return PacketPaddingPolicyV4(
        base_format_template_digest=BRAIN_PACKET_FORMAT_SHA256_V3,
        minimum_padding_bytes=0,
        maximum_padding_bytes=256,
        padding_patterns=("\t", " "),
        maximum_candidates=514,
    )


def _rows(selected_queries: tuple[str, ...]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for index in range(24):
        source = "external" if index < 12 else "internal"
        problem = (
            selected_queries[index]
            if index < len(selected_queries)
            else f"The and or this with for but {index}."
        )
        result.append(
            {
                "case_id": f"case-{index:02d}",
                "problem_text": problem,
                "source": source,
            }
        )
    return result


def _prepare(
    selected_queries: tuple[str, ...],
    corpus: BrainCorpusManifestV3,
    retrieval_policy: DomainFreeRetrievalPolicyV4,
) -> tuple[bytes, DomainFreeRetrievalPreparationV4]:
    raw = canonical_json(
        {
            "cases": _rows(selected_queries),
            "schema": DOMAIN_FREE_CASE_MANIFEST_SCHEMA_V4,
        }
    ).encode("utf-8")
    preparation = prepare_domain_free_retrieval_v4(
        raw,
        expected_case_manifest_digest=_digest_bytes(raw),
        execution_manifest=EXECUTION_MANIFEST,
        expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
        corpus=corpus,
        expected_corpus_digest=corpus.content_digest,
        policy=retrieval_policy,
        expected_policy_digest=retrieval_policy.content_digest,
    )
    return raw, preparation


def _descriptors() -> tuple[RandomExternalCaseDescriptorV4, ...]:
    internal_cells = iter(product(tuple(MathDomain), tuple(DifficultyBand)))
    result: list[RandomExternalCaseDescriptorV4] = []
    for index in range(24):
        if index < 12:
            source = ProblemSource.EXTERNAL
            domain = None
            difficulty = None
        else:
            source = ProblemSource.INTERNAL
            domain, difficulty = next(internal_cells)
        result.append(
            RandomExternalCaseDescriptorV4(
                case_id=f"case-{index:02d}",
                source=source,
                domain=domain,
                difficulty=difficulty,
                independence_cluster_id=f"cluster-{index:02d}",
                scorer_spec_digest=_digest("exact-rational-scorer-v4"),
            )
        )
    return tuple(result)


def _selected_two() -> tuple[str, ...]:
    return (
        "A polynomial identity holds at many distinct samples; establish the identity.",
        "Use inclusion-exclusion to correct double counting in the union of sets.",
    )


def _base_config() -> ThreeArmConfig:
    return ThreeArmConfig(
        stage=StudyStage.PILOT,
        runtime_policy_digest=_digest("runtime-policy"),
        prompt_template_digest=_digest("prompt-template"),
        response_schema_digest=_digest("response-schema"),
        scorer_manifest_digest=_digest("scorer-manifest"),
        chronology_policy_digest=_digest("chronology-policy"),
        scoring_authority_commitment=_digest("score-authority"),
        audit_authority_commitment=_digest("audit-authority"),
        analysis_authority_commitment=_digest("analysis-authority"),
    )


def _plan_config(materialization, *, sham_digest: str | None = None):
    return RandomExternalPilotConfigV4(
        base=_base_config(),
        curated_lane_failure_evidence_digest=_digest("curated-lane-terminal-failure"),
        external_selection_precommitment_digest=_digest("external-selection-precommitment"),
        external_selection_precommit_anchor_evidence_digest=_digest("github-anchor-evidence"),
        external_selection_capability_digest=_digest("external-selection-cold-replay"),
        external_gold_audit_signed_report_digest=_digest("external-gold-signed-report"),
        internal_selection_precommitment_digest=_digest("internal-selection-precommitment"),
        internal_selection_capability_digest=_digest("internal-selection-cold-replay"),
        retrieval_protocol_digest=materialization.retrieval_protocol_digest,
        sham_matching_precommitment_digest=(
            materialization.sham_matching_precommitment_digest
            if sham_digest is None
            else sham_digest
        ),
        tokenizer_attestation_digest=materialization.tokenizer_attestation_digest,
    )


def _cold_replay(
    raw: bytes,
    preparation: DomainFreeRetrievalPreparationV4,
    materialization,
    *,
    corpus: BrainCorpusManifestV3,
    retrieval_policy: DomainFreeRetrievalPolicyV4,
    tokenizer_suite,
    padding_policy: PacketPaddingPolicyV4,
    matching_seed: bytes = MATCHING_SEED,
):
    return cold_replay_random_external_sham_materialization_v4(
        raw,
        expected_case_manifest_digest=_digest_bytes(raw),
        retrieval_execution_manifest=EXECUTION_MANIFEST,
        expected_retrieval_execution_manifest_digest=(EXECUTION_MANIFEST.content_digest),
        corpus=corpus,
        expected_corpus_digest=corpus.content_digest,
        retrieval_policy=retrieval_policy,
        expected_retrieval_policy_digest=retrieval_policy.content_digest,
        expected_retrieval_preparation_digest=preparation.content_digest,
        descriptors=_descriptors(),
        tokenizer_suite=tokenizer_suite,
        packet_padding_policy=padding_policy,
        matching_seed=matching_seed,
        expected_materialization_digest=materialization.content_digest,
    )


def test_selected_packets_are_exact_source_disjoint_bijective_derangement(
    corpus,
    retrieval_policy,
    tokenizer_suite,
    padding_policy,
) -> None:
    _raw, preparation = _prepare(_selected_two(), corpus, retrieval_policy)
    assert preparation.selected_count == 2
    materialization = build_random_external_sham_materialization_v4(
        preparation,
        _descriptors(),
        tokenizer_suite=tokenizer_suite,
        packet_padding_policy=padding_policy,
        matching_seed=MATCHING_SEED,
    )

    assert materialization.selected_count == 2
    assert materialization.abstained_count == 22
    assert len(materialization.assignments) == 2
    packet_by_id = {item.packet_id: item for item in materialization.packets}
    assert {item.brain_packet_id for item in materialization.assignments} == set(packet_by_id)
    assert {item.sham_packet_id for item in materialization.assignments} == set(packet_by_id)
    for assignment in materialization.assignments:
        brain = packet_by_id[assignment.brain_packet_id]
        sham = packet_by_id[assignment.sham_packet_id]
        assert brain.origin_case_id == assignment.case_id
        assert brain.origin_case_id != sham.origin_case_id
        assert brain.matching_key == sham.matching_key
        assert not set(brain.source_digests) & set(sham.source_digests)
        assert brain.text_digest != sham.text_digest
    abstained = [
        case
        for case in materialization.cases
        if case.retrieval_disposition is AutomaticRetrievalDispositionV4.ABSTAINED
    ]
    assert len(abstained) == 22
    assert all(case.brain_packet_id is None and case.sham_packet_id is None for case in abstained)
    public = materialization.public_commitment()
    assert public["automatic_retrieval_itt"] is True
    assert public["confirmatory_claim_authorized"] is False
    assert public["served_model_identity_verified"] is False


def test_zero_selected_is_valid_and_singleton_is_terminally_infeasible(
    corpus,
    retrieval_policy,
    tokenizer_suite,
    padding_policy,
) -> None:
    _zero_raw, zero_preparation = _prepare((), corpus, retrieval_policy)
    zero = build_random_external_sham_materialization_v4(
        zero_preparation,
        _descriptors(),
        tokenizer_suite=tokenizer_suite,
        packet_padding_policy=padding_policy,
        matching_seed=MATCHING_SEED,
    )
    assert zero.packets == ()
    assert zero.assignments == ()
    assert all(
        case.retrieval_disposition is AutomaticRetrievalDispositionV4.ABSTAINED
        for case in zero.cases
    )
    with pytest.raises(RandomExternalMaterializationError, match="frozen retrieval renderer"):
        build_random_external_sham_materialization_v4(
            zero_preparation,
            _descriptors(),
            tokenizer_suite=tokenizer_suite,
            packet_padding_policy=replace(
                padding_policy,
                base_format_template_digest=_digest("unrelated-empty-lane-format"),
            ),
            matching_seed=MATCHING_SEED,
        )

    _one_raw, one_preparation = _prepare((_selected_two()[0],), corpus, retrieval_policy)
    assert one_preparation.selected_count == 1
    with pytest.raises(RandomExternalShamInfeasible, match="one selected packet"):
        build_random_external_sham_materialization_v4(
            one_preparation,
            _descriptors(),
            tokenizer_suite=tokenizer_suite,
            packet_padding_policy=padding_policy,
            matching_seed=MATCHING_SEED,
        )


def test_same_source_selected_packets_fail_instead_of_reusing_a_card_as_sham(
    corpus,
    retrieval_policy,
    tokenizer_suite,
    padding_policy,
) -> None:
    same_card_queries = (
        "A polynomial identity holds at many distinct samples; establish it for item one.",
        "Distinct samples establish a polynomial identity for item two.",
    )
    _raw, preparation = _prepare(same_card_queries, corpus, retrieval_policy)
    assert preparation.selected_count == 2
    assert len({draft.source_digests for draft in preparation.drafts}) == 1

    with pytest.raises(RandomExternalShamInfeasible, match="source-disjoint sham packet"):
        build_random_external_sham_materialization_v4(
            preparation,
            _descriptors(),
            tokenizer_suite=tokenizer_suite,
            packet_padding_policy=padding_policy,
            matching_seed=MATCHING_SEED,
        )


def test_raw_cold_replay_mints_capability_and_binds_exact_plan_digests(
    corpus,
    retrieval_policy,
    tokenizer_suite,
    padding_policy,
) -> None:
    raw, preparation = _prepare(_selected_two(), corpus, retrieval_policy)
    materialization = build_random_external_sham_materialization_v4(
        preparation,
        _descriptors(),
        tokenizer_suite=tokenizer_suite,
        packet_padding_policy=padding_policy,
        matching_seed=MATCHING_SEED,
    )
    replay = _cold_replay(
        raw,
        preparation,
        materialization,
        corpus=corpus,
        retrieval_policy=retrieval_policy,
        tokenizer_suite=tokenizer_suite,
        padding_policy=padding_policy,
    )
    plan = build_random_external_pilot_plan_v4(
        materialization.cases,
        materialization.packets,
        config=_plan_config(materialization),
        allocation_seed=b"v4-allocation-seed-for-materialization" * 2,
    )
    binding = bind_random_external_materialization_to_plan_v4(replay, plan)

    assert replay.materialization.content_digest == materialization.content_digest
    assert plan.packet_universe_digest == materialization.packet_universe_digest
    assert binding.public_commitment()["confirmatory_claim_authorized"] is False
    assert binding.public_commitment()["served_model_identity_verified"] is False
    assert binding.public_commitment()["sham_matching_precommitment_digest"] == (
        plan.config.sham_matching_precommitment_digest
    )
    with pytest.raises(RandomExternalMaterializationError, match="raw-input replay"):
        RandomExternalMaterializationReplayV4(
            materialization=materialization,
            _seal=object(),
        )
    object.__setattr__(binding, "content_digest", _digest("tampered-plan-binding"))
    with pytest.raises(RandomExternalMaterializationError, match="digest is stale"):
        binding.validate_current()
    parameters = inspect.signature(cold_replay_random_external_sham_materialization_v4).parameters
    assert "preparation" not in parameters
    assert "materialization" not in parameters
    assert not any("verified" in name for name in parameters)


def test_cold_replay_rejects_wrong_seed_and_plan_binding_rejects_transplanted_digest(
    corpus,
    retrieval_policy,
    tokenizer_suite,
    padding_policy,
) -> None:
    raw, preparation = _prepare(_selected_two(), corpus, retrieval_policy)
    materialization = build_random_external_sham_materialization_v4(
        preparation,
        _descriptors(),
        tokenizer_suite=tokenizer_suite,
        packet_padding_policy=padding_policy,
        matching_seed=MATCHING_SEED,
    )
    with pytest.raises(RandomExternalMaterializationError, match="cold replay differs"):
        _cold_replay(
            raw,
            preparation,
            materialization,
            corpus=corpus,
            retrieval_policy=retrieval_policy,
            tokenizer_suite=tokenizer_suite,
            padding_policy=padding_policy,
            matching_seed=b"wrong-source-disjoint-matching-seed" * 2,
        )
    replay = _cold_replay(
        raw,
        preparation,
        materialization,
        corpus=corpus,
        retrieval_policy=retrieval_policy,
        tokenizer_suite=tokenizer_suite,
        padding_policy=padding_policy,
    )
    transplanted_plan = build_random_external_pilot_plan_v4(
        materialization.cases,
        materialization.packets,
        config=_plan_config(materialization, sham_digest=_digest("wrong-sham-digest")),
        allocation_seed=b"v4-allocation-seed-for-materialization" * 2,
    )
    with pytest.raises(RandomExternalMaterializationError, match="plan differs"):
        bind_random_external_materialization_to_plan_v4(replay, transplanted_plan)


def test_case_descriptors_cannot_invent_external_labels_or_duplicate_clusters() -> None:
    descriptors = list(_descriptors())
    with pytest.raises(RandomExternalMaterializationError, match="must not invent"):
        replace(
            descriptors[0],
            domain=MathDomain.ALGEBRA,
            difficulty=DifficultyBand.EASY,
        )
    descriptors[1] = replace(
        descriptors[1],
        independence_cluster_id=descriptors[0].independence_cluster_id,
    )
    with pytest.raises(RandomExternalMaterializationError, match="independence cluster"):
        # Validation happens before any retrieval/tokenizer work.
        from math_brain.random_external_sham_materialization_v4 import _normalized_descriptors

        _normalized_descriptors(descriptors)
