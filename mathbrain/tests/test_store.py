from pathlib import Path

import pytest

from math_brain.mistakes import MistakeKind, MistakeRecord, ReflexRecord
from math_brain.models import (
    Claim,
    ClaimKind,
    EdgeKind,
    EdgePolarity,
    Evidence,
    EvidenceKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    RecordStatus,
    VerificationEvent,
)
from math_brain.store import ResearchStore


def test_store_round_trip_and_append_only_status(tmp_path: Path) -> None:
    claim = Claim("For every integer n, n + 0 = n.", ClaimKind.THEOREM)
    evidence = Evidence(
        claim.id,
        EvidenceKind.LEAN_KERNEL,
        "Lean kernel accepted the theorem",
        "all integers",
        "lean-test",
        True,
    )
    event = VerificationEvent(
        claim.id,
        RecordStatus.UNVERIFIED,
        RecordStatus.PROVISIONAL,
        (evidence.id,),
        "kernel evidence exists but fidelity review remains",
        "test",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        store.add_evidence(evidence)
        store.add_verification_event(event)
        assert store.get_claim(claim.id) == claim
        assert store.evidence_for(claim.id) == [evidence]
        assert store.current_status(claim.id) is RecordStatus.PROVISIONAL


def test_store_rejects_duplicate_claim(tmp_path: Path) -> None:
    claim = Claim("x = x", ClaimKind.THEOREM)
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        with pytest.raises(ValueError, match="not appendable"):
            store.add_claim(claim)


def test_edge_requires_existing_nodes(tmp_path: Path) -> None:
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        with pytest.raises(Exception):
            store.add_edge(GraphEdge("missing-a", "missing-b", EdgeKind.DEPENDS_ON, "test"))


def test_signed_warning_edge_round_trips_without_losing_polarity(tmp_path: Path) -> None:
    edge = GraphEdge(
        "claim",
        "mistake",
        EdgeKind.FAILS_UNDER,
        "a domain assumption fails",
        id="edge_failure",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_node(GraphNode("claim", NodeKind.CLAIM, "claim"))
        store.add_node(GraphNode("mistake", NodeKind.MISTAKE, "mistake"))
        store.add_edge(edge)
        loaded = list(store.iter_edges())
    assert loaded == [edge]
    assert loaded[0].polarity is EdgePolarity.NEGATIVE


def test_structured_mistake_and_reflex_round_trip_without_promoting_truth(
    tmp_path: Path,
) -> None:
    claim = Claim("For x != 0, x / x = 1.", ClaimKind.THEOREM, id="claim_division")
    mistake = MistakeRecord(
        claim_id=claim.id,
        kind=MistakeKind.DOMAIN,
        failing_step="Used the identity at x = 0.",
        cause="The domain restriction was omitted.",
        repair_steps=("State x != 0 before cancelling x.",),
        prevention_action="Before cancellation, prove the factor is nonzero.",
        id="mistake_division",
    )
    reflex = ReflexRecord(
        activation_cues=("cancellation",),
        action_steps=("Name the cancelled factor and check its zero set.",),
        required_evidence=("a proof that the current branch excludes zero",),
        stop_condition="Do not cancel until the nonzero condition is proved.",
        source_mistake_ids=(mistake.id,),
        id="reflex_division",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        store.add_mistake(mistake)
        store.add_reflex(reflex)
        assert store.get_mistake(mistake.id) == mistake
        assert store.get_reflex(reflex.id) == reflex
        assert store.current_status(claim.id) is RecordStatus.UNVERIFIED
        edges = list(store.iter_edges())
    assert {edge.kind for edge in edges} == {EdgeKind.FAILS_UNDER, EdgeKind.TRIGGERS}
    assert all(edge.polarity is EdgePolarity.NEGATIVE for edge in edges)


def test_reflex_store_rejects_a_missing_source_mistake_atomically(tmp_path: Path) -> None:
    reflex = ReflexRecord(
        activation_cues=("cancellation",),
        action_steps=("Check the cancelled factor.",),
        required_evidence=("a nonzero proof",),
        stop_condition="Do not cancel without evidence.",
        source_mistake_ids=("missing",),
        id="reflex_missing",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        with pytest.raises(ValueError, match="not appendable"):
            store.add_reflex(reflex)
        assert store.get_reflex(reflex.id) is None
        assert all(node.id != reflex.id for node in store.iter_nodes())
