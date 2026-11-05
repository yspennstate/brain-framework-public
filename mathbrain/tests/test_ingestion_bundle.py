"""Bundle ingestion: an order-independent manifest, refusals before anything is written, and a dark release gate."""
import pytest

from math_brain.ingestion import (
    BundleEndpoint,
    MathBundleEntry,
    MathKnowledgeBundle,
    MathRelationSpec,
    ingest_bundle,
)
from math_brain.models import ClaimKind, EdgeKind, RecordStatus
from math_brain.overlay import ContentAddressedTextStore, MathNodeRevision, SourceProvenance
from math_brain.store import ResearchStore

PROV = SourceProvenance("textbook", "ed2", "ch 3", "test")
CREATED = "2026-11-05T00:00:00+00:00"


def _entry(key, statement, **over):
    rev = MathNodeRevision("probability", key, ClaimKind.THEOREM, key.title(), statement, PROV, **over)
    return MathBundleEntry(key, rev)


A = _entry("markov", "P(X >= a) <= EX / a for X >= 0 and a > 0.")
B = _entry("chebyshev", "P(|X - EX| >= t) <= Var X / t^2 for t > 0.")
DEP = MathRelationSpec(BundleEndpoint.entry("chebyshev"), BundleEndpoint.entry("markov"), EdgeKind.DEPENDS_ON,
                       "apply Markov to (X - EX)^2")
CITE = MathRelationSpec(BundleEndpoint.entry("markov"), BundleEndpoint.existing("claim_elsewhere"), EdgeKind.CITES,
                        "standard statement")


def _bundle(entries=(A, B), relations=(DEP,), **over):
    return MathKnowledgeBundle("tail_bounds", tuple(entries), relations=tuple(relations), created_at=CREATED, **over)


def test_the_digest_ignores_the_order_of_entries_and_relations() -> None:
    one = _bundle((A, B), (DEP, CITE))
    two = _bundle((B, A), (CITE, DEP))
    assert one.bundle_digest == two.bundle_digest
    assert _bundle((A,), ()).bundle_digest != one.bundle_digest


@pytest.mark.parametrize("build, words", [
    (lambda: _bundle(entries=()), "at least one entry"),
    (lambda: _bundle(entries=(A, MathBundleEntry("markov", B.revision))), "duplicate entry keys"),
    (lambda: _bundle(entries=(A, _entry("chebyshev", "x is 1.", status=RecordStatus.REFUTED,
                                        disposition_reason="counterexample"))), "unverified revisions only"),
    (lambda: _bundle(relations=(MathRelationSpec(BundleEndpoint.entry("chebyshev"), BundleEndpoint.entry("jensen"),
                                                 EdgeKind.DEPENDS_ON, "r"),)), "unknown bundle key"),
    (lambda: _bundle(relations=(MathRelationSpec(BundleEndpoint.existing("claim_x"), BundleEndpoint.entry("markov"),
                                                 EdgeKind.CITES, "r"),)), "originate at a bundle entry"),
    (lambda: _bundle(relations=(DEP, MathRelationSpec(DEP.source, DEP.target, DEP.kind, "again"))),
     "duplicate typed relations"),
])
def test_bad_bundles_are_refused_at_construction(build, words) -> None:
    with pytest.raises(ValueError, match=words):
        build()


@pytest.mark.parametrize("kwargs, words", [
    (dict(kind=EdgeKind.SUPERSEDES), "not a bundle relation"),
    (dict(target=BundleEndpoint.entry("chebyshev")), "cannot point to itself"),
    (dict(weight=0.0), "weight"),
    (dict(rationale=" "), "rationale"),
])
def test_bad_relations_are_refused(kwargs, words) -> None:
    base = dict(source=BundleEndpoint.entry("chebyshev"), target=BundleEndpoint.entry("markov"),
                kind=EdgeKind.DEPENDS_ON, rationale="r")
    base.update(kwargs)
    with pytest.raises(ValueError, match=words):
        MathRelationSpec(**base)


def test_without_an_approved_release_nothing_is_written(tmp_path) -> None:
    objects_root = tmp_path / "objects"
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        with pytest.raises(ValueError, match="release gate is dark"):
            ingest_bundle(store, ContentAddressedTextStore(objects_root), _bundle(), release_verifier=None)
        assert list(store.iter_nodes()) == []
    assert not any(objects_root.rglob("*.md"))
