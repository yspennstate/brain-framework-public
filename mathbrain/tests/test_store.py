import sqlite3
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
from math_brain.store import ClaimBatchItem, ResearchStore


def test_store_round_trip_and_direct_status_insertion_is_disabled(tmp_path: Path) -> None:
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
        with pytest.raises(RuntimeError, match="authenticated derived-status path"):
            store.add_verification_event(event)
        assert store.get_claim(claim.id) == claim
        assert store.evidence_for(claim.id) == [evidence]
        assert store.current_status(claim.id) is RecordStatus.UNVERIFIED


def test_store_rejects_duplicate_claim(tmp_path: Path) -> None:
    claim = Claim("x = x", ClaimKind.THEOREM)
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        with pytest.raises(ValueError, match="not appendable"):
            store.add_claim(claim)


def test_store_rejects_caller_authored_initial_status(tmp_path: Path) -> None:
    claim = Claim(
        "A caller-authored green claim",
        ClaimKind.THEOREM,
        status=RecordStatus.VERIFIED,
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store, pytest.raises(
        ValueError, match="must start unverified"
    ):
        store.add_claim(claim)


def test_edge_requires_existing_nodes(tmp_path: Path) -> None:
    with ResearchStore(tmp_path / "brain.sqlite3") as store, pytest.raises(
        sqlite3.IntegrityError
    ):
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


def test_custom_claim_node_preserves_overlay_metadata(tmp_path: Path) -> None:
    claim = Claim("A scoped theorem", ClaimKind.THEOREM, id="claim_scoped")
    node = GraphNode(
        id=claim.id,
        kind=NodeKind.CLAIM,
        title="Scoped theorem revision",
        body=claim.statement,
        metadata={
            "logical_id": "math:example:scoped-theorem",
            "truth_status": "unverified",
            "artifact_digest": "sha256:" + "0" * 64,
        },
        created_at=claim.created_at,
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim, node=node)
        stored = {item.id: item for item in store.iter_nodes()}[claim.id]

    assert stored.metadata == node.metadata
    assert stored.title == node.title


def test_custom_claim_edges_are_atomic_when_a_revision_target_is_missing(
    tmp_path: Path,
) -> None:
    claim = Claim("A new revision", ClaimKind.THEOREM, id="claim_new")
    node = GraphNode(claim.id, NodeKind.CLAIM, "New revision")
    edge = GraphEdge(
        claim.id,
        "claim_missing",
        EdgeKind.SUPERSEDES,
        "supersedes a prior immutable revision",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        with pytest.raises(ValueError, match="not appendable"):
            store.add_claim(claim, node=node, edges=(edge,))
        assert store.get_claim(claim.id) is None
        assert all(item.id != claim.id for item in store.iter_nodes())


def test_claim_batch_rolls_back_every_claim_when_the_final_edge_conflicts(
    tmp_path: Path,
) -> None:
    first_anchor = GraphNode("anchor_a", NodeKind.CLAIM, "Anchor A")
    second_anchor = GraphNode("anchor_b", NodeKind.CLAIM, "Anchor B")
    existing_edge = GraphEdge(
        first_anchor.id,
        second_anchor.id,
        EdgeKind.DEPENDS_ON,
        "existing dependency",
        id="edge_reserved",
    )
    first = Claim("First batched claim", ClaimKind.LEMMA, id="claim_batch_first")
    second = Claim("Second batched claim", ClaimKind.THEOREM, id="claim_batch_second")
    colliding_edge = GraphEdge(
        first.id,
        second.id,
        EdgeKind.DEPENDS_ON,
        "this insert reaches the database after both claims",
        id=existing_edge.id,
    )

    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_node(first_anchor)
        store.add_node(second_anchor)
        store.add_edge(existing_edge)
        with pytest.raises(ValueError, match="not appendable"):
            store.add_claim_batch(
                (ClaimBatchItem(first), ClaimBatchItem(second)),
                edges=(colliding_edge,),
            )
        assert store.get_claim(first.id) is None
        assert store.get_claim(second.id) is None
        assert {node.id for node in store.iter_nodes()} == {
            first_anchor.id,
            second_anchor.id,
        }
        assert list(store.iter_edges()) == [existing_edge]


def test_default_claim_node_carries_statement_and_assumptions_in_the_body(
    tmp_path: Path,
) -> None:
    statement = (
        "The passage-time variance obeys a constant-A doubling bound at one scale "
        "ratio, and iterating that bound from a fixed base scale yields a polynomial "
        "improvement over the linear certificate at every larger dyadic scale."
    )
    claim = Claim(
        statement,
        ClaimKind.PROOF_ATTEMPT,
        assumptions=(
            "bounded non-degenerate weights on [a,b]",
            "the implication holds uniformly along the segment (NOT available)",
        ),
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        node = next(n for n in store.iter_nodes() if n.id == claim.id)
    assert len(node.title) <= 140
    # the title is a word-boundary prefix of the statement, not a mid-token cut
    assert statement.startswith(node.title)
    assert statement[len(node.title)] == " "
    assert statement in node.body
    assert (
        "Assumes: bounded non-degenerate weights on [a,b]; "
        "the implication holds uniformly along the segment (NOT available)" in node.body
    )


def test_default_claim_node_without_assumptions_has_no_assumes_clause(
    tmp_path: Path,
) -> None:
    claim = Claim("Every finite group has a composition series.", ClaimKind.THEOREM)
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        node = next(n for n in store.iter_nodes() if n.id == claim.id)
    assert node.title == "Every finite group has a composition series."
    assert node.body == "Every finite group has a composition series."
    assert "Assumes:" not in node.body


# task 462 (2026-08-09, fixed 2026-09-06): an edge kind this build does not define is a named,
# counted refusal, or an explicit opt-in skip with a warning - never a bare ValueError mid-iteration
# and never silent tolerance.
def _store_with_unknown_edge(path: Path, unknown_kind: str = "future_kind") -> ResearchStore:
    store = ResearchStore(path)
    store.add_node(GraphNode("a", NodeKind.CLAIM, "a"))
    store.add_node(GraphNode("b", NodeKind.CLAIM, "b"))
    store.add_node(GraphNode("c", NodeKind.CLAIM, "c"))
    store.add_edge(GraphEdge("a", "b", EdgeKind.DEPENDS_ON, "known", id="edge_known"))
    store.connection.execute(
        """INSERT INTO edges(id, source_id, target_id, kind, rationale, weight,
                             metadata_json, digest, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("edge_future", "b", "c", unknown_kind, "written by a newer build", 1.0, "{}",
         "digest_future", "2099-01-01T00:00:00Z"),
    )
    store.connection.commit()
    return store


def test_unknown_edge_kind_is_a_named_counted_refusal(tmp_path: Path) -> None:
    from math_brain.store import UnknownEdgeKindError

    with _store_with_unknown_edge(tmp_path / "brain.sqlite3") as store:
        assert store.unknown_edge_kinds() == {"future_kind": 1}
        with pytest.raises(UnknownEdgeKindError) as excinfo:
            list(store.iter_edges())
        assert excinfo.value.kind == "future_kind"
        assert excinfo.value.count == 1
        assert "skip_unknown=True" in str(excinfo.value)
        assert "--skip-unknown-edges" in str(excinfo.value)
        with pytest.raises(UnknownEdgeKindError):
            store.neighbors(["b"])
        # filtering by a known kind never touches the unknown rows
        assert [e.id for e in store.iter_edges(kind=EdgeKind.DEPENDS_ON)] == ["edge_known"]


def test_skip_unknown_edges_warns_once_per_kind_and_keeps_known_edges(tmp_path: Path) -> None:
    import warnings

    from math_brain.store import UnknownEdgeKindWarning

    with _store_with_unknown_edge(tmp_path / "brain.sqlite3") as store:
        store.connection.execute(
            """INSERT INTO edges(id, source_id, target_id, kind, rationale, weight,
                                 metadata_json, digest, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("edge_future_2", "a", "c", "future_kind", "second row", 1.0, "{}",
             "digest_future_2", "2099-01-01T00:00:01Z"),
        )
        store.connection.commit()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            edges = list(store.iter_edges(skip_unknown=True))
        assert [e.id for e in edges] == ["edge_known"]
        skips = [w for w in caught if issubclass(w.category, UnknownEdgeKindWarning)]
        assert len(skips) == 1
        assert "2 edge rows" in str(skips[0].message)
        assert "'future_kind'" in str(skips[0].message)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            near = store.neighbors(["b"], skip_unknown=True)
        assert [e.id for e in near] == ["edge_known"]
        assert len([w for w in caught if issubclass(w.category, UnknownEdgeKindWarning)]) == 1


def test_store_refuses_a_newer_schema_version(tmp_path: Path) -> None:
    from math_brain.store import STORE_SCHEMA_VERSION, StoreSchemaError

    path = tmp_path / "brain.sqlite3"
    with ResearchStore(path) as store:
        store.connection.execute(
            "INSERT INTO schema_meta(version) VALUES (?)", (STORE_SCHEMA_VERSION + 1,)
        )
        store.connection.commit()
    with pytest.raises(StoreSchemaError) as excinfo:
        ResearchStore(path)
    assert str(STORE_SCHEMA_VERSION + 1) in str(excinfo.value)


def test_recall_cli_skip_unknown_edges_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import warnings

    from math_brain.cli import main
    from math_brain.store import UnknownEdgeKindError

    path = tmp_path / "brain.sqlite3"
    _store_with_unknown_edge(path).close()
    with pytest.raises(UnknownEdgeKindError):
        main(["recall", str(path), "a"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert main(["recall", str(path), "a", "--skip-unknown-edges"]) == 0
    import json

    # recall prints {"hits": [...], "background": [...]} in this build
    printed = json.loads(capsys.readouterr().out)
    assert isinstance(printed["hits"], list) and isinstance(printed["background"], list)
