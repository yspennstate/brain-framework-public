from __future__ import annotations

import hashlib
import inspect
from dataclasses import fields, replace
from pathlib import Path

import pytest

import math_brain.domain_free_retrieval_v4 as retrieval_v4
from math_brain.brain_corpus_v3 import (
    BRAIN_CARD_COUNT_V3,
    BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3,
    BRAIN_RETRIEVAL_POLICY_SHA256_V3,
    BrainCorpusManifestV3,
    acquire_frozen_brain_corpus_v3,
)
from math_brain.domain_free_retrieval_v4 import (
    DOMAIN_FREE_CASE_MANIFEST_SCHEMA_V4,
    DOMAIN_FREE_NORMALIZATION_SHA256_V4,
    DOMAIN_FREE_V3_ADAPTER_SIGNATURE_SHA256_V4,
    DomainFreePacketDraftV4,
    DomainFreeRetrievalAbstentionV4,
    DomainFreeRetrievalDispositionV4,
    DomainFreeRetrievalExecutionManifestV4,
    DomainFreeRetrievalPolicyV4,
    DomainFreeRetrievalPreparationV4,
    RetrievalBatchCardinalityV4,
    capture_current_domain_free_retrieval_execution_manifest_v4,
    cold_replay_domain_free_retrieval_v4,
    prepare_domain_free_retrieval_v4,
)
from math_brain.models import canonical_json
from math_brain.three_arm_evaluation import ProblemSource

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXECUTION_MANIFEST = capture_current_domain_free_retrieval_execution_manifest_v4(
    git_commit_sha1="1" * 40,
    git_tree_sha1="2" * 40,
)


def _digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_text(label: str) -> str:
    return _digest_bytes(label.encode("utf-8"))


def _manifest_bytes(rows: list[dict[str, object]]) -> bytes:
    return canonical_json(
        {
            "cases": rows,
            "schema": DOMAIN_FREE_CASE_MANIFEST_SCHEMA_V4,
        }
    ).encode("utf-8")


def _row(case_id: str, problem_text: str, source: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "problem_text": problem_text,
        "source": source,
    }


@pytest.fixture(scope="module")
def corpus() -> BrainCorpusManifestV3:
    return acquire_frozen_brain_corpus_v3(REPOSITORY_ROOT)


@pytest.fixture(scope="module")
def policy() -> DomainFreeRetrievalPolicyV4:
    return DomainFreeRetrievalPolicyV4(EXECUTION_MANIFEST.content_digest)


def _prepare(
    rows: list[dict[str, object]],
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> tuple[bytes, DomainFreeRetrievalPreparationV4]:
    raw = _manifest_bytes(rows)
    return raw, prepare_domain_free_retrieval_v4(
        raw,
        expected_case_manifest_digest=_digest_bytes(raw),
        execution_manifest=EXECUTION_MANIFEST,
        expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
        corpus=corpus,
        expected_corpus_digest=corpus.content_digest,
        policy=policy,
        expected_policy_digest=policy.content_digest,
    )


def test_external_cases_require_no_semantic_labels_and_source_never_affects_ranking(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    problem = "A polynomial identity holds at many distinct samples; establish the identity."
    _external_raw, external = _prepare(
        [_row("same-case", problem, "external")],
        corpus,
        policy,
    )
    _internal_raw, internal = _prepare(
        [_row("same-case", problem, "internal")],
        corpus,
        policy,
    )

    assert external.cases[0].source is ProblemSource.EXTERNAL
    assert external.source_counts == {"external": 1, "internal": 0}
    assert external.receipts[0].query_digest == internal.receipts[0].query_digest
    assert external.receipts[0].considered_hits == internal.receipts[0].considered_hits
    assert external.receipts[0].disposition is internal.receipts[0].disposition
    assert external.receipts[0].selected_source_digests == (
        internal.receipts[0].selected_source_digests
    )
    assert external.content_digest != internal.content_digest

    forbidden = _row("labeled-external", problem, "external") | {"domain": None}
    raw = _manifest_bytes([forbidden])
    with pytest.raises(ValueError, match="only id, problem, and source"):
        prepare_domain_free_retrieval_v4(
            raw,
            expected_case_manifest_digest=_digest_bytes(raw),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=policy.content_digest,
        )


def test_case_manifest_is_exact_canonical_json_in_case_id_order(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    unsorted = _manifest_bytes(
        [
            _row("z-case", "The and or but.", "external"),
            _row("a-case", "The and or but.", "internal"),
        ]
    )
    with pytest.raises(ValueError, match="sorted by case id"):
        prepare_domain_free_retrieval_v4(
            unsorted,
            expected_case_manifest_digest=_digest_bytes(unsorted),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=policy.content_digest,
        )

    canonical = _manifest_bytes([_row("a-case", "The and or but.", "external")])
    noncanonical = canonical.replace(b'"cases":', b'"cases": ')
    with pytest.raises(ValueError, match="not canonical JSON"):
        prepare_domain_free_retrieval_v4(
            noncanonical,
            expected_case_manifest_digest=_digest_bytes(noncanonical),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=policy.content_digest,
        )


def test_deterministic_cold_replay_uses_raw_inputs_not_a_nominal_preparation(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    raw, prepared = _prepare(
        [
            _row(
                "external-polynomial",
                "Distinct samples establish a polynomial identity.",
                "external",
            ),
            _row(
                "internal-overlap",
                "Use inclusion-exclusion to correct double counting in a union.",
                "internal",
            ),
        ],
        corpus,
        policy,
    )
    replayed = cold_replay_domain_free_retrieval_v4(
        raw,
        expected_case_manifest_digest=_digest_bytes(raw),
        execution_manifest=EXECUTION_MANIFEST,
        expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
        corpus=corpus,
        expected_corpus_digest=corpus.content_digest,
        policy=policy,
        expected_policy_digest=policy.content_digest,
        expected_preparation_digest=prepared.content_digest,
    )
    assert replayed == prepared
    assert replayed.receipt_universe_digest == prepared.receipt_universe_digest
    assert replayed.draft_universe_digest == prepared.draft_universe_digest
    assert "preparation" not in inspect.signature(cold_replay_domain_free_retrieval_v4).parameters


def test_abstention_is_retained_as_itt_and_zero_selection_is_valid(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    raw, prepared = _prepare(
        [
            _row("external-stopwords", "The and or this.", "external"),
            _row("internal-stopwords", "With the and for.", "internal"),
        ],
        corpus,
        policy,
    )
    assert len(prepared.cases) == 2
    assert len(prepared.receipts) == 2
    assert not prepared.drafts
    assert prepared.abstained_count == 2
    assert prepared.cardinality is RetrievalBatchCardinalityV4.ZERO_SELECTED_ITT
    assert all(
        receipt.disposition is DomainFreeRetrievalDispositionV4.ABSTAINED
        for receipt in prepared.receipts
    )
    assert all(not receipt.salient_terms for receipt in prepared.receipts)
    assert all(
        receipt.abstention_reason is DomainFreeRetrievalAbstentionV4.NO_SALIENT_TERMS
        for receipt in prepared.receipts
    )
    assert (
        cold_replay_domain_free_retrieval_v4(
            raw,
            expected_case_manifest_digest=_digest_bytes(raw),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=policy.content_digest,
            expected_preparation_digest=prepared.content_digest,
        )
        == prepared
    )


def test_non_stopword_residual_is_no_candidate_match_not_no_salient_terms(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    _raw, prepared = _prepare(
        [_row("external-residual", "The and or but.", "external")],
        corpus,
        policy,
    )
    receipt = prepared.receipts[0]

    assert receipt.salient_terms == ("but",)
    assert receipt.disposition is DomainFreeRetrievalDispositionV4.ABSTAINED
    assert receipt.abstention_reason is DomainFreeRetrievalAbstentionV4.NO_CANDIDATE_MATCH
    assert prepared.cardinality is RetrievalBatchCardinalityV4.ZERO_SELECTED_ITT
    assert not prepared.drafts


def test_single_selected_case_is_reported_infeasible_without_top_up(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    _raw, prepared = _prepare(
        [
            _row(
                "a-selected",
                "A polynomial identity holds at many distinct samples; establish it.",
                "external",
            ),
            _row("b-abstained", "The and or but.", "internal"),
        ],
        corpus,
        policy,
    )
    assert prepared.selected_count == 1
    assert prepared.abstained_count == 1
    assert len(prepared.drafts) == 1
    assert prepared.cardinality is (RetrievalBatchCardinalityV4.SINGLETON_WHOLE_LANE_INFEASIBLE)
    assert prepared.drafts[0].case_id == "a-selected"


def test_multiple_raw_drafts_expose_materializer_lineage_but_no_packet_authority(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    _raw, prepared = _prepare(
        [
            _row(
                "a-polynomial",
                "A polynomial identity holds at many distinct samples; establish it.",
                "external",
            ),
            _row(
                "b-overlap",
                "Use inclusion-exclusion to correct double counting in the union of sets.",
                "internal",
            ),
        ],
        corpus,
        policy,
    )
    assert prepared.selected_count == 2
    assert prepared.cardinality is (
        RetrievalBatchCardinalityV4.MULTIPLE_SELECTED_MATERIALIZER_INPUT
    )
    for draft in prepared.drafts:
        record = draft.materializer_record()
        assert draft.render_fragment.count("\n") == 1
        assert draft.render_fragment not in canonical_json(record)
        assert record["solver_dispatch_authorized"] is False
        assert record["relevance_review_gates_selection"] is False
        assert draft.source_digests
        assert draft.render_fragment_digest == record["render_fragment_digest"]
        assert draft.render_fragment_byte_count == record["render_fragment_byte_count"]


def test_ambiguous_top_two_is_an_abstention_not_a_digest_tiebreak(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    _raw, prepared = _prepare(
        [
            _row(
                "ambiguous",
                "Can a cancellation be made with a coprime factor?",
                "external",
            )
        ],
        corpus,
        policy,
    )
    receipt = prepared.receipts[0]
    assert receipt.disposition is DomainFreeRetrievalDispositionV4.ABSTAINED
    assert receipt.abstention_reason is (
        DomainFreeRetrievalAbstentionV4.AMBIGUOUS_SELECTION_BOUNDARY
    )
    assert len(receipt.considered_hits) == 2
    assert float.fromhex(receipt.selection_boundary_margin_hex) < 0.5
    assert not receipt.selected_source_digests
    assert not prepared.drafts


def test_tied_top_two_order_is_index_only_and_considers_the_full_candidate_set(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_limits: list[tuple[int, int]] = []
    original_retrieve = retrieval_v4.retrieve

    def recording_retrieve(*args: object, **kwargs: object) -> object:
        observed_limits.append((kwargs["limit"], kwargs["seed_limit"]))  # type: ignore[arg-type]
        return original_retrieve(*args, **kwargs)

    monkeypatch.setattr(retrieval_v4, "retrieve", recording_retrieve)
    _raw, prepared = _prepare(
        [
            _row(
                "ambiguous-index-order",
                "Can a cancellation be made with a coprime factor?",
                "external",
            )
        ],
        corpus,
        policy,
    )
    receipt = prepared.receipts[0]
    card_by_id = {card.record_id: card for card in corpus.cards}
    selected_cards = tuple(card_by_id[hit.record_id] for hit in receipt.considered_hits)
    changed_non_index_cards = tuple(
        replace(
            card,
            family="changed_family",
            guidance="Changed non-index guidance without a numeral.",
        )
        for card in selected_cards
    )

    assert observed_limits == [(len(corpus.cards), len(corpus.cards))]
    assert receipt.considered_index_keys == tuple(
        retrieval_v4._index_only_tie_key_v4(card) for card in selected_cards
    )
    assert receipt.considered_index_keys == tuple(
        retrieval_v4._index_only_tie_key_v4(card) for card in changed_non_index_cards
    )
    assert tuple(
        (card.title, card.retrieval_cues)
        for card in sorted(
            selected_cards,
            key=retrieval_v4._index_only_tie_key_v4,
        )
    ) == tuple(
        (card.title, card.retrieval_cues)
        for card in sorted(
            changed_non_index_cards,
            key=retrieval_v4._index_only_tie_key_v4,
        )
    )


def test_manifest_and_problem_substitution_fail_external_commitments(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    raw, prepared = _prepare(
        [_row("case", "Distinct samples establish a polynomial identity.", "external")],
        corpus,
        policy,
    )
    changed = _manifest_bytes(
        [_row("case", "Distinct samples might establish an identity.", "external")]
    )
    with pytest.raises(ValueError, match="external commitment"):
        prepare_domain_free_retrieval_v4(
            changed,
            expected_case_manifest_digest=_digest_bytes(raw),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=policy.content_digest,
        )
    with pytest.raises(ValueError, match="external preparation commitment"):
        cold_replay_domain_free_retrieval_v4(
            changed,
            expected_case_manifest_digest=_digest_bytes(changed),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=policy.content_digest,
            expected_preparation_digest=prepared.content_digest,
        )


def test_corpus_and_policy_substitution_fail_closed(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    raw = _manifest_bytes(
        [_row("case", "Distinct samples establish a polynomial identity.", "internal")]
    )
    with pytest.raises(ValueError, match="execution manifest differs"):
        prepare_domain_free_retrieval_v4(
            raw,
            expected_case_manifest_digest=_digest_bytes(raw),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=_digest_text("substituted-execution"),
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=policy.content_digest,
        )
    with pytest.raises(ValueError, match="corpus differs"):
        prepare_domain_free_retrieval_v4(
            raw,
            expected_case_manifest_digest=_digest_bytes(raw),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=_digest_text("substituted-corpus"),
            policy=policy,
            expected_policy_digest=policy.content_digest,
        )
    with pytest.raises(ValueError, match="policy differs"):
        prepare_domain_free_retrieval_v4(
            raw,
            expected_case_manifest_digest=_digest_bytes(raw),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=_digest_text("substituted-policy"),
        )

    _baseline_raw, baseline = _prepare(
        [_row("case", "Distinct samples establish a polynomial identity.", "internal")],
        corpus,
        policy,
    )
    substituted_manifest = capture_current_domain_free_retrieval_execution_manifest_v4(
        git_commit_sha1="3" * 40,
        git_tree_sha1="4" * 40,
    )
    substituted_policy = DomainFreeRetrievalPolicyV4(substituted_manifest.content_digest)
    with pytest.raises(ValueError, match="external preparation commitment"):
        cold_replay_domain_free_retrieval_v4(
            raw,
            expected_case_manifest_digest=_digest_bytes(raw),
            execution_manifest=substituted_manifest,
            expected_execution_manifest_digest=substituted_manifest.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=substituted_policy,
            expected_policy_digest=substituted_policy.content_digest,
            expected_preparation_digest=baseline.content_digest,
        )


def test_forged_nominal_preparation_cannot_enter_the_replay_boundary(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    raw, prepared = _prepare(
        [_row("case", "The and or but.", "external")],
        corpus,
        policy,
    )
    forged = object.__new__(DomainFreeRetrievalPreparationV4)
    object.__setattr__(forged, "content_digest", prepared.content_digest)
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        cold_replay_domain_free_retrieval_v4(
            raw,
            expected_case_manifest_digest=_digest_bytes(raw),
            execution_manifest=EXECUTION_MANIFEST,
            expected_execution_manifest_digest=EXECUTION_MANIFEST.content_digest,
            corpus=corpus,
            expected_corpus_digest=corpus.content_digest,
            policy=policy,
            expected_policy_digest=policy.content_digest,
            expected_preparation_digest=prepared.content_digest,
            preparation=forged,  # type: ignore[call-arg]
        )


def test_no_relevance_gate_tokenizer_padding_or_frozen_packet_fields_exist(
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    policy_record = policy.commitment_record()
    assert policy_record["relevance_review_gates_selection"] is False
    assert policy_record["domain_or_difficulty_fields_used"] is False
    assert policy_record["family_or_gold_fields_used"] is False
    assert policy_record["source_affects_query_or_ranking"] is False
    assert policy_record["base_retrieval_policy_digest"] == (BRAIN_RETRIEVAL_POLICY_SHA256_V3)
    assert policy_record["derived_index_universe_digest"] == (
        BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3
    )
    assert policy_record["normalization_digest"] == (DOMAIN_FREE_NORMALIZATION_SHA256_V4)
    assert policy_record["execution_manifest_digest"] == EXECUTION_MANIFEST.content_digest
    assert policy_record["tie_order"] == "normalized_title_and_retrieval_cues_digest"
    assert policy_record["candidate_scan"] == ("full_frozen_index_before_index_only_top_two")
    assert policy_record["candidate_scan_limit"] == BRAIN_CARD_COUNT_V3
    assert policy_record["v3_adapter_signature_digest"] == (
        DOMAIN_FREE_V3_ADAPTER_SIGNATURE_SHA256_V4
    )
    assert policy_record["index_fields"] == ["title", "retrieval_cues"]

    all_fields = {item.name.casefold() for item in fields(DomainFreePacketDraftV4)}
    forbidden_fragments = {
        "tokenizer",
        "padding",
        "frozen_packet",
        "relevance_key",
        "domain",
        "difficulty",
        "family",
        "gold",
        "answer",
        "reviewer",
    }
    assert not {
        name for name in all_fields if any(fragment in name for fragment in forbidden_fragments)
    }
    assert "relevance" not in inspect.signature(prepare_domain_free_retrieval_v4).parameters


def test_private_v3_helper_signature_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(retrieval_v4, "_graph_nodes_v3", lambda corpus: ())
    with pytest.raises(ValueError, match="private v3 retrieval-helper signature drifted"):
        DomainFreeRetrievalPolicyV4(_digest_text("v4-module-source-pin"))


def test_execution_manifest_catches_same_signature_semantic_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert type(EXECUTION_MANIFEST) is DomainFreeRetrievalExecutionManifestV4
    record = EXECUTION_MANIFEST.commitment_record()
    assert [item["relative_path"] for item in record["artifacts"]] == [
        "src/math_brain/domain_free_retrieval_v4.py",
        "src/math_brain/brain_corpus_v3.py",
        "src/math_brain/retrieval.py",
    ]
    assert record["git_commit_sha1"] == "1" * 40
    assert record["git_tree_sha1"] == "2" * 40
    prepare_parameters = inspect.signature(prepare_domain_free_retrieval_v4).parameters
    replay_parameters = inspect.signature(cold_replay_domain_free_retrieval_v4).parameters
    assert prepare_parameters["execution_manifest"].default is inspect.Parameter.empty
    assert (
        prepare_parameters["expected_execution_manifest_digest"].default is inspect.Parameter.empty
    )
    assert replay_parameters["execution_manifest"].default is inspect.Parameter.empty
    assert (
        replay_parameters["expected_execution_manifest_digest"].default is inspect.Parameter.empty
    )
    original_reader = retrieval_v4._execution_source_bytes_v4

    def same_signature_semantic_drift(relative_path: str) -> bytes:
        raw = original_reader(relative_path)
        if relative_path == "src/math_brain/retrieval.py":
            return raw + b"\n# semantic drift with unchanged call signatures\n"
        return raw

    monkeypatch.setattr(
        retrieval_v4,
        "_execution_source_bytes_v4",
        same_signature_semantic_drift,
    )
    with pytest.raises(ValueError, match=r"execution artifact changed: .*retrieval\.py"):
        EXECUTION_MANIFEST.validate_current()


def test_preparation_validate_current_rejects_receipt_and_draft_transplants(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    _raw, prepared = _prepare(
        [
            _row(
                "a-polynomial",
                "A polynomial identity holds at many distinct samples; establish it.",
                "external",
            ),
            _row(
                "b-overlap",
                "Use inclusion-exclusion to correct double counting in the union of sets.",
                "internal",
            ),
        ],
        corpus,
        policy,
    )
    first_case = prepared.cases[0]
    first_receipt = prepared.receipts[0]
    receipt_transplants = {
        "problem_digest": prepared.cases[1].problem_digest,
        "query_digest": retrieval_v4.DomainFreeRetrievalQueryV4(
            first_case.case_id,
            prepared.cases[1].problem_text,
        ).content_digest,
        "source": prepared.cases[1].source,
    }
    for field_name, transplanted_value in receipt_transplants.items():
        transplanted_receipt = replace(
            first_receipt,
            **{field_name: transplanted_value},
        )
        with pytest.raises(ValueError, match="problem, source, or query differs from case"):
            replace(
                prepared,
                receipts=(transplanted_receipt, *prepared.receipts[1:]),
            )

    first_draft = prepared.drafts[0]
    draft_lineage_transplants: dict[str, object] = {
        "case_manifest_digest": _digest_text("other-case-manifest"),
        "corpus_digest": _digest_text("other-corpus"),
        "policy_digest": _digest_text("other-policy"),
        "query_digest": _digest_text("other-query"),
        "retrieval_receipt_digest": _digest_text("other-receipt"),
        "source_digests": (_digest_text("other-source"),),
        "source_manifest_digest": _digest_text("other-source-manifest"),
    }
    for field_name, transplanted_value in draft_lineage_transplants.items():
        wrong_lineage = replace(first_draft, **{field_name: transplanted_value})
        with pytest.raises(ValueError, match="draft lineage differs"):
            replace(prepared, drafts=(wrong_lineage, *prepared.drafts[1:]))

    wrong_case = replace(first_draft, case_id="a-not-selected")
    with pytest.raises(ValueError, match="drafts differ from selected receipts"):
        replace(prepared, drafts=(wrong_case, *prepared.drafts[1:]))

    wrong_render = replace(
        first_draft,
        render_fragment="Transplanted title\nTransplanted guidance",
    )
    with pytest.raises(ValueError, match="not the selected title plus guidance"):
        replace(prepared, drafts=(wrong_render, *prepared.drafts[1:]))


def test_render_draft_and_public_commitment_leak_no_paths_or_hidden_labels(
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> None:
    _raw, prepared = _prepare(
        [
            _row(
                "EXTERNAL-OPAQUE-001",
                "A polynomial identity holds at many distinct samples; establish it.",
                "external",
            ),
            _row(
                "INTERNAL-OPAQUE-001",
                "Use inclusion-exclusion to correct double counting in a union.",
                "internal",
            ),
        ],
        corpus,
        policy,
    )
    public_text = canonical_json(prepared.public_commitment()) + repr(prepared)
    assert str(REPOSITORY_ROOT) not in public_text
    assert "C:\\Users" not in public_text
    assert "owner" not in public_text.casefold()
    assert "problem_text" not in public_text
    assert "render_fragment" not in public_text
    for draft, receipt in zip(prepared.drafts, prepared.receipts, strict=True):
        selected = {card.record_id: card for card in corpus.cards}[receipt.selected_record_ids[0]]
        assert draft.render_fragment == selected.title + "\n" + selected.guidance
        assert selected.family not in draft.render_fragment
        assert selected.record_id not in draft.render_fragment
        assert selected.semantic_digest not in draft.render_fragment
        assert receipt.case_id not in draft.render_fragment
        assert "C:\\" not in draft.render_fragment
        assert "/home/" not in draft.render_fragment
