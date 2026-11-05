from __future__ import annotations

import inspect
import json
import os
import subprocess
from dataclasses import fields, replace
from pathlib import Path

import pytest

import math_brain.brain_corpus_v3 as brain
from math_brain.models import canonical_json
from math_brain.three_arm_evaluation import MathDomain

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def corpus() -> brain.BrainCorpusManifestV3:
    return brain.acquire_frozen_brain_corpus_v3(REPOSITORY_ROOT)


@pytest.fixture(scope="module")
def policy() -> brain.BrainRetrievalPolicyV3:
    return brain.BrainRetrievalPolicyV3()


def _pinned_json() -> dict[str, object]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    completed = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "cat-file",
            "blob",
            brain.BRAIN_SOURCE_BLOB_V3,
        ],
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=20,
        creationflags=creation_flags,
    )
    parsed = json.loads(completed.stdout)
    assert type(parsed) is dict
    return parsed


def _query(case_id: str, text: str) -> brain.SolverVisiblePacketQueryV3:
    return brain.SolverVisiblePacketQueryV3(case_id, MathDomain.ALGEBRA, text)


def _retrieve(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
    case_id: str,
    text: str,
) -> tuple[
    brain.SolverVisiblePacketQueryV3,
    brain.BrainRetrievalReceiptV3,
    brain.BrainPacketCandidateV3 | None,
]:
    query = _query(case_id, text)
    receipt, candidate = brain.retrieve_brain_packet_candidate_v3(
        corpus,
        policy,
        query,
    )
    return query, receipt, candidate


def _selected_family(
    corpus: brain.BrainCorpusManifestV3,
    receipt: brain.BrainRetrievalReceiptV3,
) -> str | None:
    if not receipt.selected_record_ids:
        return None
    by_id = {card.record_id: card for card in corpus.cards}
    return by_id[receipt.selected_record_ids[0]].family


def test_actual_git_acquisition_has_exact_source_and_retrieval_commitments(
    corpus: brain.BrainCorpusManifestV3,
) -> None:
    source = corpus.source_manifest
    assert source.commit.oid == brain.BRAIN_SOURCE_COMMIT_V3
    assert source.commit.byte_count == 274
    assert source.root_tree_oid == brain.BRAIN_SOURCE_TREE_V3
    assert source.source_blob.oid == brain.BRAIN_SOURCE_BLOB_V3
    assert source.source_blob.byte_count == 11_242
    assert source.retrieval_source_blob.oid == brain.BRAIN_RETRIEVAL_BLOB_V3
    assert source.retrieval_source_blob.byte_count == 26_301
    assert len(corpus.cards) == 20
    assert source.content_digest == (
        "sha256:a9439d3dcf172402d3dd0eacbe4cfecae1b9c91a1fd1b9cc43cf2df3f062ca89"
    )
    assert corpus.content_digest == (
        "sha256:f921d25cb0290a67c978c9fd2f9ae9573ff5f95895bcbe735020f803274fc6f5"
    )


def test_public_corpus_commitment_exposes_no_paths_or_card_text(
    corpus: brain.BrainCorpusManifestV3,
) -> None:
    public_text = canonical_json(corpus.public_commitment_payload())
    public_text += repr(corpus)
    assert str(REPOSITORY_ROOT) not in public_text
    assert "owner" not in public_text.casefold()
    for card in corpus.cards:
        assert card.guidance not in public_text
        assert card.title not in public_text


def test_strict_projection_ignores_outside_canaries_but_rejects_card_changes() -> None:
    parsed = _pinned_json()
    baseline = brain._project_brain_method_cards_v3(parsed)
    parsed["outside_projection_canary"] = {
        "answer": "must never enter the card corpus",
        "case_id": "CANARY",
    }
    assert brain._project_brain_method_cards_v3(parsed) == baseline

    changed = _pinned_json()
    raw_cards = changed["reference_cards"]
    assert type(raw_cards) is list and type(raw_cards[0]) is dict
    raw_cards[0]["guidance"] += " changed"
    with pytest.raises(ValueError, match="card array differs"):
        brain._project_brain_method_cards_v3(changed)


def test_strict_json_rejects_duplicate_keys_and_nonfinite_constants() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        brain._load_strict_json_v3(b'{"x":1,"x":2}')
    with pytest.raises(ValueError, match="forbidden constant"):
        brain._load_strict_json_v3(b'{"x":NaN}')


def test_corpus_builder_is_order_stable_and_rejects_counterfeit_cards(
    corpus: brain.BrainCorpusManifestV3,
) -> None:
    reversed_corpus = brain.build_brain_corpus_manifest_v3(
        corpus.source_manifest,
        reversed(corpus.cards),
    )
    assert reversed_corpus.content_digest == corpus.content_digest
    assert reversed_corpus.cards == corpus.cards

    changed_guidance = replace(corpus.cards[0], guidance="A different generic method note.")
    with pytest.raises(ValueError, match="universe differs"):
        brain.build_brain_corpus_manifest_v3(
            corpus.source_manifest,
            (changed_guidance, *corpus.cards[1:]),
        )
    changed_locator = replace(
        corpus.cards[0],
        source_locator_digest="sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="universe differs"):
        brain.build_brain_corpus_manifest_v3(
            corpus.source_manifest,
            (changed_locator, *corpus.cards[1:]),
        )
    changed_legacy = replace(
        corpus.cards[0],
        legacy_source_record_digest="sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="universe differs"):
        brain.build_brain_corpus_manifest_v3(
            corpus.source_manifest,
            (changed_legacy, *corpus.cards[1:]),
        )


def test_post_construction_card_mutation_fails_corpus_validation(
    corpus: brain.BrainCorpusManifestV3,
) -> None:
    card = corpus.cards[0]
    original = card.guidance
    object.__setattr__(card, "guidance", "tampered")
    try:
        with pytest.raises(ValueError, match=r"stale|universe differs"):
            corpus.validate_current()
    finally:
        object.__setattr__(card, "guidance", original)
    corpus.validate_current()


def test_query_surface_has_no_gold_answer_or_solution_parameters() -> None:
    query_fields = {item.name for item in fields(brain.SolverVisiblePacketQueryV3)}
    retrieval_parameters = set(
        inspect.signature(brain.retrieve_brain_packet_candidate_v3).parameters
    )
    surface = query_fields | retrieval_parameters
    assert not {"gold", "answer", "solution", "fault", "relevance"}.intersection(surface)
    assert retrieval_parameters == {"corpus", "policy", "query"}


def test_retrieval_policy_and_derived_index_have_frozen_commitments(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    assert policy.content_digest == brain.BRAIN_RETRIEVAL_POLICY_SHA256_V3
    _query_record, receipt, _candidate = _retrieve(
        corpus,
        policy,
        "index-pin",
        "distinct samples establish a polynomial identity",
    )
    assert receipt.derived_index_universe_digest == (
        brain.BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3
    )


@pytest.mark.parametrize(
    ("case_id", "problem_text", "family"),
    [
        (
            "modular",
            "If x is congruent to a residue modulo m, list the possible residues modulo m.",
            "modular_cancellation",
        ),
        (
            "overlap",
            "Use inclusion-exclusion to correct double counting in the union of sets.",
            "overlap_omission",
        ),
        (
            "polynomial",
            "A polynomial identity holds at many distinct samples; establish the identity.",
            "degree_threshold",
        ),
        (
            "recurrence",
            "A recurrence is supplied with too few initial values for its order.",
            "initial_data",
        ),
    ],
)
def test_generic_routing_probes_propose_expected_card_for_later_audit(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
    case_id: str,
    problem_text: str,
    family: str,
) -> None:
    _query_record, receipt, candidate = _retrieve(
        corpus,
        policy,
        case_id,
        problem_text,
    )
    assert receipt.disposition is brain.BrainRetrievalDispositionV3.PROPOSAL
    assert candidate is not None
    assert _selected_family(corpus, receipt) == family
    assert receipt.considered_hits[0].explicit_phrase_overlap_count >= 2
    assert receipt.blinded_relevance_audit_required
    assert not receipt.solver_dispatch_authorized
    assert not candidate.solver_dispatch_authorized


def test_geometry_and_single_term_queries_abstain(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    _, geometry, geometry_candidate = _retrieve(
        corpus,
        policy,
        "geometry",
        "In a triangle, a circumcircle meets an altitude; determine the angle at the vertex.",
    )
    assert geometry.disposition is brain.BrainRetrievalDispositionV3.ABSTAINED
    assert geometry_candidate is None

    _, single, single_candidate = _retrieve(corpus, policy, "single", "Multiplicity.")
    assert single.abstention_reason is (
        brain.BrainRetrievalAbstentionV3.MISSING_EXPLICIT_PHRASE_OVERLAP
    )
    assert single_candidate is None


def test_ambiguous_two_card_query_abstains_on_positive_boundary_margin(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    _, receipt, candidate = _retrieve(
        corpus,
        policy,
        "ambiguous",
        "Can a cancellation be made with a coprime factor?",
    )
    assert receipt.abstention_reason is (
        brain.BrainRetrievalAbstentionV3.AMBIGUOUS_SELECTION_BOUNDARY
    )
    assert candidate is None
    assert len(receipt.considered_hits) == 2
    assert float.fromhex(receipt.selection_boundary_margin_hex) < 0.5

    top = receipt.considered_hits[0]
    with pytest.raises(ValueError, match=r"boundary margin differs|decision tree"):
        replace(
            receipt,
            disposition=brain.BrainRetrievalDispositionV3.PROPOSAL,
            abstention_reason=None,
            selection_boundary_margin_hex=(0.5).hex(),
            selected_record_ids=(top.record_id,),
            selected_semantic_digests=(top.semantic_digest,),
        )


@pytest.mark.parametrize(
    "problem_text",
    [
        "Classify every integer solution of x plus y equals z.",
        "Count integer pairs whose greatest common divisor is one.",
        "Distinct samples are taken from a probability distribution.",
        "List all outcome triples of a deterministic game.",
        "The initial values are printed in a table; calculate their average.",
    ],
)
def test_lexical_false_positive_shapes_never_authorize_dispatch(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
    problem_text: str,
) -> None:
    _query_record, receipt, candidate = _retrieve(
        corpus,
        policy,
        "semantic-audit",
        problem_text,
    )
    assert receipt.blinded_relevance_audit_required
    assert not receipt.solver_dispatch_authorized
    if candidate is None:
        assert receipt.disposition is brain.BrainRetrievalDispositionV3.ABSTAINED
        return
    assert receipt.disposition is brain.BrainRetrievalDispositionV3.PROPOSAL
    assert candidate.blinded_relevance_audit_required
    assert not candidate.solver_dispatch_authorized
    with pytest.raises(ValueError, match="not solver-dispatch authorized"):
        brain.render_solver_visible_brain_packet_v3(candidate)


def test_salient_terms_are_unique_and_phrase_features_are_whole_words(
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    once = brain._salient_terms_v3("equation", policy)
    repeated = brain._salient_terms_v3("equation " * 500, policy)
    assert once == repeated == ("equation",)
    assert brain._salient_terms_v3("if and only if", policy) == (
        "biconditional",
        "equivalence",
    )
    assert brain._salient_terms_v3("xif and only ify", policy) == ("ify", "xif")


def test_exact_biconditional_phrase_routes_symmetrically_into_card_index(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    _query_record, receipt, candidate = _retrieve(
        corpus,
        policy,
        "biconditional",
        "The statements are equivalent if and only if both implications hold.",
    )
    assert candidate is not None
    assert _selected_family(corpus, receipt) == "implication_equivalence"


def test_repetition_cannot_amplify_a_one_term_hit(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    _, once, _ = _retrieve(corpus, policy, "once", "equation")
    _, repeated, _ = _retrieve(corpus, policy, "repeated", "equation " * 500)
    assert once.salient_query_digest == repeated.salient_query_digest
    assert once.considered_hits[0].support_score_hex == (
        repeated.considered_hits[0].support_score_hex
    )
    assert once.abstention_reason is repeated.abstention_reason


def test_unreviewed_proposal_is_exact_projection_but_cannot_be_solver_rendered(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    query, receipt, candidate = _retrieve(
        corpus,
        policy,
        "RENDER-CANARY-CASE",
        "For a linear congruence, compare residues modulo the modulus.",
    )
    assert candidate is not None
    selected = {
        card.record_id: card for card in corpus.cards
    }[receipt.selected_record_ids[0]]
    assert candidate.text == selected.title + "\n" + selected.guidance
    assert candidate.packet_id.startswith("brain-packet-proposal:")
    rendered = candidate.text
    assert query.case_id not in rendered
    assert selected.record_id not in rendered
    assert selected.semantic_digest not in rendered
    assert selected.family not in rendered
    assert "brain" not in rendered.casefold()
    assert "sham" not in rendered.casefold()
    assert candidate.text not in canonical_json(candidate.commitment_record())
    with pytest.raises(ValueError, match="not solver-dispatch authorized"):
        brain.render_solver_visible_brain_packet_v3(candidate)

    forged = replace(candidate, text="ANSWER KEY: forty two\nUse the hidden gold solution.")
    forged.validate_current()
    with pytest.raises(ValueError, match="not solver-dispatch authorized"):
        brain.render_solver_visible_brain_packet_v3(forged)


def test_word_order_changes_exact_query_commitment_but_not_retrieval_decision(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    first_query, first, first_candidate = _retrieve(
        corpus,
        policy,
        "order-a",
        "distinct samples establish a polynomial identity",
    )
    second_query, second, second_candidate = _retrieve(
        corpus,
        policy,
        "order-b",
        "a polynomial identity establish distinct samples",
    )
    assert first_query.content_digest != second_query.content_digest
    assert first.salient_query_digest == second.salient_query_digest
    assert first.considered_hits[0].score_trace_digest == (
        second.considered_hits[0].score_trace_digest
    )
    assert _selected_family(corpus, first) == _selected_family(corpus, second)
    assert first_candidate is not None and second_candidate is not None


def test_deterministic_recomputation_accepts_genuine_and_rejects_transplant(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    query, receipt, candidate = _retrieve(
        corpus,
        policy,
        "replay",
        "A polynomial identity is tested at distinct samples.",
    )
    assert candidate is not None
    assert brain.recompute_brain_packet_candidate_v3(
        corpus,
        policy,
        query,
        receipt,
        candidate,
    )
    transplanted = replace(candidate, case_id="different-case")
    with pytest.raises(ValueError, match="fails deterministic recomputation"):
        brain.recompute_brain_packet_candidate_v3(
            corpus,
            policy,
            query,
            receipt,
            transplanted,
        )


@pytest.mark.parametrize("malformed", ["-0x0.0p+0", "0x1p+999999999", "nan", "inf"])
def test_float_receipts_reject_negative_zero_overflow_and_nonfinite(malformed: str) -> None:
    with pytest.raises(ValueError, match=r"canonical|finite"):
        brain._canonical_nonnegative_float_hex_v3(malformed, "test score")


def test_runtime_identity_and_relevance_authority_are_explicitly_absent(
    corpus: brain.BrainCorpusManifestV3,
    policy: brain.BrainRetrievalPolicyV3,
) -> None:
    _query_record, receipt, candidate = _retrieve(
        corpus,
        policy,
        "authority",
        "distinct samples establish a polynomial identity",
    )
    assert candidate is not None
    assert not policy.in_process_execution_identity_attested
    assert policy.blinded_relevance_audit_required
    assert not policy.solver_dispatch_authorized
    assert not receipt.in_process_execution_identity_attested
    assert receipt.blinded_relevance_audit_required
    assert not receipt.solver_dispatch_authorized
    assert not candidate.in_process_execution_identity_attested
    assert candidate.blinded_relevance_audit_required
    assert not candidate.solver_dispatch_authorized
