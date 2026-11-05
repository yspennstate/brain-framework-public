"""Immutable content-addressed revisions: identity, the status rules, the object store and external proxies."""
import os

import pytest

from math_brain.models import ClaimKind, EdgeKind, RecordStatus
from math_brain.overlay import (
    ContentAddressedTextStore,
    MathNodeRevision,
    SourceProvenance,
    append_revision,
    bridge_edge,
    external_reference_node,
)
from math_brain.store import ResearchStore

PROV = SourceProvenance("textbook", "ed2", "thm 3.1", "test")


def _rev(**over):
    base = dict(namespace="probability", logical_name="chebyshev", kind=ClaimKind.THEOREM,
                title="Chebyshev inequality", statement="P(|X - EX| >= t) <= Var X / t^2 for t > 0.", provenance=PROV)
    base.update(over)
    return MathNodeRevision(**base)


def test_identity_is_the_content_and_not_the_ingestion_time() -> None:
    a = _rev(created_at="2026-01-01T00:00:00+00:00")
    b = _rev(created_at="2026-06-01T00:00:00+00:00")
    assert a.revision_id == b.revision_id and a.revision_id.startswith("claim_")
    assert a.render_markdown() == b.render_markdown()
    assert _rev(statement="P(|X - EX| >= t) <= Var X / t^2.").revision_id != a.revision_id
    assert a.logical_id == "math:probability:chebyshev"


@pytest.mark.parametrize("over, words", [
    (dict(namespace="Probability"), "normalized lowercase"),
    (dict(title=" "), "title and statement"),
    (dict(assumptions=("t > 0", "t > 0")), "duplicates"),
    (dict(status=RecordStatus.VERIFIED), "evidence ids"),
    (dict(status=RecordStatus.VERIFIED, evidence_ids=("ev1",)), "statement-fidelity"),
    (dict(status=RecordStatus.CONDITIONAL, evidence_ids=("ev1",)), "explicit conditions"),
    (dict(status=RecordStatus.REFUTED), "disposition reason"),
])
def test_status_and_shape_rules_refuse_bad_revisions(over, words) -> None:
    with pytest.raises(ValueError, match=words):
        _rev(**over)


def test_a_declared_status_never_becomes_the_claims_authority() -> None:
    rev = _rev(status=RecordStatus.REFUTED, disposition_reason="counterexample at t = 0")
    assert rev.to_claim().status is RecordStatus.UNVERIFIED
    node = rev.to_graph_node(_receipt())
    assert node.metadata["truth_status"] == "unverified" and node.metadata["declared_status"] == "refuted"


def _receipt():
    from math_brain.overlay import ArtifactReceipt
    return ArtifactReceipt("objects/sha256/00/x.md", "sha256:" + "0" * 64, 1)


def test_persist_is_idempotent_and_refuses_a_tampered_object(tmp_path) -> None:
    objects = ContentAddressedTextStore(tmp_path)
    first = objects.persist(_rev())
    assert objects.persist(_rev(created_at="2027-01-01T00:00:00+00:00")) == first
    path = tmp_path / first.relative_path
    assert path.read_text(encoding="utf-8").startswith("# Chebyshev inequality")
    path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(OSError, match="does not match"):
        objects.persist(_rev())


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_persist_refuses_to_write_through_a_linked_objects_folder(tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    root = tmp_path / "store"
    root.mkdir()
    (root / "objects").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError, match="link"):
        ContentAddressedTextStore(root).persist(_rev())
    assert not any(elsewhere.rglob("*.md"))


def test_append_writes_claim_node_and_supersession_edge(tmp_path) -> None:
    objects = ContentAddressedTextStore(tmp_path / "objects")
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        old = append_revision(store, objects, _rev())
        new = append_revision(store, objects, _rev(statement="P(|X - EX| >= t) <= Var X / t^2 for every t > 0.",
                                                   supersedes=(old.revision.revision_id,)))
        assert store.get_claim(new.revision.revision_id).status is RecordStatus.UNVERIFIED
        assert tuple(store.get_node(new.node.id).metadata["supersedes"]) == (old.revision.revision_id,)
    (edge,) = new.revision.supersession_edges()
    assert (edge.source_id, edge.target_id, edge.kind) == (new.node.id, old.node.id, EdgeKind.SUPERSEDES)


def test_external_proxies_are_deterministic_and_bridges_are_typed() -> None:
    a = external_reference_node("other_brain", "node 17", "Markov inequality")
    b = external_reference_node("other_brain", "node 17", "Markov inequality")
    assert a.id == b.id and a.id.startswith("external_")
    assert external_reference_node("other_brain", "node 18", "Markov inequality").id != a.id
    with pytest.raises(ValueError):
        external_reference_node("Other Brain", "node 17", "x")
    assert bridge_edge("claim_x", a.id, "same inequality").kind is EdgeKind.BRIDGES_TO
