import pytest

from math_brain.mistakes import MistakeKind, MistakeRecord, ReflexRecord, memory_edges
from math_brain.models import EdgePolarity, NodeKind


CREATED_AT = "2026-01-01T00:00:00+00:00"


def mistake() -> MistakeRecord:
    return MistakeRecord(
        claim_id="claim_limit",
        kind=MistakeKind.LIMIT_INTERCHANGE,
        failing_step="Moved a limit through an integral without a domination argument.",
        cause="Pointwise convergence was mistaken for dominated convergence.",
        repair_steps=(
            "State a dominating integrable function.",
            "Verify domination on the full measure space.",
        ),
        prevention_action=(
            "Before interchanging limit and integral, name the theorem and check every hypothesis."
        ),
        affected_assumptions=("measurability", "integrable domination"),
        counterexample="Use a concentrating sequence with constant integral.",
        id="mistake_limit",
        created_at=CREATED_AT,
    )


def reflex() -> ReflexRecord:
    return ReflexRecord(
        activation_cues=("limit inside an integral", "exchange of convergence operations"),
        action_steps=(
            "Write the exact interchange theorem before continuing.",
            "Check measurability, convergence mode, and domination separately.",
        ),
        required_evidence=("a theorem-hypothesis checklist", "an independent boundary-case check"),
        stop_condition="Abstain from the interchange until every hypothesis has evidence.",
        source_mistake_ids=("mistake_limit",),
        id="reflex_limit",
        created_at=CREATED_AT,
    )


def test_structured_mistake_and_reflex_are_hashable_graph_records() -> None:
    first = mistake()
    second = mistake()
    action = reflex()
    assert first.digest() == second.digest()
    assert first.to_node().kind is NodeKind.MISTAKE
    assert action.to_node().kind is NodeKind.REFLEX
    assert "Stop:" in action.to_node().body


def test_mistake_memory_builds_explicit_signed_warning_edges() -> None:
    edges = memory_edges(mistake(), reflex())
    assert len(edges) == 2
    assert all(edge.is_warning for edge in edges)
    assert all(edge.polarity is EdgePolarity.NEGATIVE for edge in edges)
    assert all(edge.signed_weight < 0 for edge in edges)


def test_reflex_requires_actions_evidence_and_a_stop_condition() -> None:
    with pytest.raises(ValueError, match="required_evidence"):
        ReflexRecord(
            activation_cues=("division",),
            action_steps=("Check the denominator.",),
            required_evidence=(),
            stop_condition="Do not divide until nonzero is proved.",
            source_mistake_ids=("mistake_division",),
        )
    with pytest.raises(ValueError, match="stop_condition"):
        ReflexRecord(
            activation_cues=("division",),
            action_steps=("Check the denominator.",),
            required_evidence=("a nonzero proof",),
            stop_condition="",
            source_mistake_ids=("mistake_division",),
        )


def test_reflex_cannot_be_linked_to_an_uncited_mistake() -> None:
    uncited_reflex = ReflexRecord(
        activation_cues=("division",),
        action_steps=("Check the denominator.",),
        required_evidence=("a nonzero proof",),
        stop_condition="Do not divide until nonzero is proved.",
        source_mistake_ids=("mistake_division",),
    )
    with pytest.raises(ValueError, match="must cite"):
        memory_edges(mistake(), uncited_reflex)
