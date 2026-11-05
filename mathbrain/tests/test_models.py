from dataclasses import asdict, replace

import pytest

from math_brain.models import (
    Claim,
    ClaimKind,
    EdgeKind,
    EdgePolarity,
    Evidence,
    EvidenceKind,
    GraphEdge,
    RecordStatus,
    VerificationEvent,
    canonical_json,
    content_hash,
)


def test_claim_hash_is_stable_for_same_record() -> None:
    claim = Claim("Every finite field has prime-power order.", ClaimKind.THEOREM)
    assert claim.digest() == claim.digest()
    assert claim.digest().startswith("sha256:")


def test_claim_hash_changes_with_mathematical_content() -> None:
    claim = Claim("x = x", ClaimKind.THEOREM)
    changed = replace(claim, statement="x = -x")
    assert claim.digest() != changed.digest()


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})


def test_verified_transition_requires_evidence() -> None:
    with pytest.raises(ValueError, match="requires evidence"):
        VerificationEvent(
            claim_id="claim_1",
            from_status=RecordStatus.PROVISIONAL,
            to_status=RecordStatus.VERIFIED,
            evidence_ids=(),
            rationale="looked plausible",
            reviewer="reviewer",
        )

def test_evidence_requires_scope() -> None:
    with pytest.raises(ValueError, match="scope"):
        Evidence(
            claim_id="claim_1",
            kind=EvidenceKind.NUMERIC,
            summary="checked examples",
            scope="",
            verifier="test",
            passed=True,
        )


@pytest.mark.parametrize("result", ["false", "true", 0, 1, None])
def test_evidence_result_must_be_a_boolean(result: object) -> None:
    # a stored "false" read back as truthy was shown as a pass (account02/B19 auto57, R69 E2)
    with pytest.raises(TypeError, match="boolean"):
        Evidence(
            claim_id="claim_1",
            kind=EvidenceKind.HUMAN_PROOF_REVIEW,
            summary="read the proof",
            scope="the whole statement",
            verifier="reviewer",
            passed=result,  # type: ignore[arg-type]
        )


def test_edge_kind_and_polarity_cannot_contradict_each_other() -> None:
    warning = GraphEdge("claim", "counter", EdgeKind.REFUTES, "counterexample")
    support = GraphEdge("claim", "proof", EdgeKind.PROVES, "proof")
    assert warning.polarity is EdgePolarity.NEGATIVE
    assert warning.signed_weight == -1.0
    assert support.polarity is EdgePolarity.POSITIVE
    assert support.signed_weight == 1.0
    with pytest.raises(ValueError, match="must have negative polarity"):
        GraphEdge(
            "claim",
            "counter",
            EdgeKind.REFUTES,
            "counterexample",
            polarity=EdgePolarity.POSITIVE,
        )


def test_derived_polarity_preserves_schema_one_edge_hashes() -> None:
    edge = GraphEdge(
        "claim",
        "counter",
        EdgeKind.REFUTES,
        "counterexample",
        id="edge_1",
        created_at="2026-01-01T00:00:00+00:00",
    )
    legacy_payload = asdict(edge)
    legacy_payload.pop("polarity")
    assert edge.digest() == content_hash(legacy_payload)
