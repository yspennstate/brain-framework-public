import pytest

from math_brain.consolidation import (
    ConsolidationPhase,
    ConsolidationProposal,
    ConsolidationTaskKind,
    ProposalStatus,
    consolidate,
    propose_tasks,
)
from math_brain.mistakes import MistakeKind, MistakeRecord, ReflexRecord
from math_brain.models import EdgeKind, GraphEdge, GraphNode, NodeKind
from math_brain.retrieval import RetrievalGuard


CREATED_AT = "2026-01-01T00:00:00+00:00"


def test_consolidation_surfaces_orphans_missing_evidence_and_refutations() -> None:
    claim = GraphNode("claim", NodeKind.CLAIM, "claim")
    counterexample = GraphNode("counter", NodeKind.EVIDENCE, "counterexample")
    orphan = GraphNode("orphan", NodeKind.OPEN_QUESTION, "orphan")
    edge = GraphEdge("claim", "counter", EdgeKind.REFUTES, "counterexample")
    kinds = {task.kind for task in propose_tasks([claim, counterexample, orphan], [edge])}
    assert ConsolidationTaskKind.CONTRADICTION_REVIEW in kinds
    assert ConsolidationTaskKind.MISSING_EVIDENCE in kinds
    assert ConsolidationTaskKind.ORPHAN_REVIEW in kinds


def test_trigger_edge_requests_rehearsal_instead_of_a_contradiction_review() -> None:
    mistake = GraphNode("mistake", NodeKind.MISTAKE, "division by zero")
    reflex = GraphNode("reflex", NodeKind.REFLEX, "prove denominator nonzero")
    edge = GraphEdge("mistake", "reflex", EdgeKind.TRIGGERS, "activate the check")
    tasks = propose_tasks([mistake, reflex], [edge])
    kinds = {task.kind for task in tasks}
    assert ConsolidationTaskKind.REFLEX_REHEARSAL in kinds
    assert ConsolidationTaskKind.CONTRADICTION_REVIEW not in kinds


def _mistake_and_reflex() -> tuple[MistakeRecord, ReflexRecord]:
    mistake = MistakeRecord(
        claim_id="claim",
        kind=MistakeKind.DOMAIN,
        failing_step="Divided by x without establishing x != 0.",
        cause="The denominator's domain restriction was dropped.",
        repair_steps=("Split the proof into x = 0 and x != 0 cases.",),
        prevention_action="Before division, prove the denominator is nonzero.",
        id="mistake",
        created_at=CREATED_AT,
    )
    reflex = ReflexRecord(
        activation_cues=("division by an expression",),
        action_steps=("Name the denominator and derive its nonzero condition.",),
        required_evidence=("a proof of the nonzero condition in the current branch",),
        stop_condition="Do not divide until the condition is established.",
        source_mistake_ids=("mistake",),
        id="reflex",
        created_at=CREATED_AT,
    )
    return mistake, reflex


def _guarded_nodes() -> list[GraphNode]:
    return [
        GraphNode(
            "claim",
            NodeKind.CLAIM,
            "Square nonnegativity",
            r"$x^2 \ge 0$",
            metadata={"split": "train", "item_id": "train-1"},
            created_at=CREATED_AT,
        ),
        GraphNode(
            "analogy",
            NodeKind.CLAIM,
            "Another square",
            "$y² ≥ 0$",
            metadata={"split": "reference", "item_id": "reference-1"},
            created_at=CREATED_AT,
        ),
        GraphNode(
            "answer",
            NodeKind.EVIDENCE,
            "Hidden exact answer",
            r"$z^2 \ge 0$",
            metadata={"split": "test", "content_role": "answer_key", "item_id": "test-9"},
            created_at=CREATED_AT,
        ),
    ]


def test_sws_and_rem_proposals_remain_quarantined_and_non_evidentiary() -> None:
    nodes = _guarded_nodes()
    mistake, reflex = _mistake_and_reflex()
    original_digests = tuple(node.digest() for node in nodes)
    cycle = consolidate(
        nodes,
        [],
        mistakes=[mistake],
        reflexes=[reflex],
        guard=RetrievalGuard(evaluation_split="test", active_item_id="test-9"),
    )
    assert {proposal.phase for proposal in cycle.proposals} == {
        ConsolidationPhase.SWS_REPLAY,
        ConsolidationPhase.REM_ASSOCIATION,
    }
    assert all(proposal.status is ProposalStatus.QUARANTINED for proposal in cycle.proposals)
    assert all(not proposal.can_promote_truth for proposal in cycle.proposals)
    assert not cycle.can_promote_truth
    assert tuple(node.digest() for node in nodes) == original_digests
    with pytest.raises(PermissionError, match="not evidence"):
        cycle.proposals[0].as_verification_evidence()


def test_consolidation_guard_excludes_test_answer_from_every_proposal() -> None:
    mistake, reflex = _mistake_and_reflex()
    crossing_edge = GraphEdge(
        "claim",
        "answer",
        EdgeKind.ANALOGOUS_TO,
        "an edge that crosses into the held-out split",
    )
    cycle = consolidate(
        _guarded_nodes(),
        [crossing_edge],
        mistakes=[mistake],
        reflexes=[reflex],
        guard=RetrievalGuard(evaluation_split="test", active_item_id="test-9"),
    )
    assert {item.node_id for item in cycle.exclusions} == {"answer"}
    assert all("answer" not in proposal.node_ids for proposal in cycle.proposals)
    rehearsal = next(
        proposal
        for proposal in cycle.proposals
        if proposal.kind is ConsolidationTaskKind.REFLEX_REHEARSAL
    )
    assert any("denominator" in action for action in rehearsal.required_actions)
    association = next(
        proposal
        for proposal in cycle.proposals
        if proposal.kind is ConsolidationTaskKind.ASSOCIATION_REVIEW
    )
    assert association.node_ids == ("analogy", "claim")


def test_consolidation_is_deterministic_under_input_reordering() -> None:
    nodes = _guarded_nodes()[:2]
    first = consolidate(nodes, [])
    second = consolidate(reversed(nodes), reversed([]))
    assert first == second
    assert [proposal.id for proposal in first.proposals] == [
        proposal.id for proposal in second.proposals
    ]


def test_proposal_constructor_rejects_any_truth_promotion_capability() -> None:
    with pytest.raises(ValueError, match="cannot promote truth"):
        ConsolidationProposal(
            phase=ConsolidationPhase.REM_ASSOCIATION,
            kind=ConsolidationTaskKind.ASSOCIATION_REVIEW,
            node_ids=("a", "b"),
            hypothesis="The nodes might be related.",
            required_actions=("Try to disprove the proposed relationship.",),
            priority=10,
            can_promote_truth=True,
        )
