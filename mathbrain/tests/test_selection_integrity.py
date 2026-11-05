"""The candidate gate: a selection survives only if every card it keeps reads back as its complete current note."""
from types import SimpleNamespace

import pytest

from math_brain.current_note import NoteReadError
from math_brain.selection_integrity import SelectionUnavailable, consistent_hits


class _Node(SimpleNamespace):
    def digest(self):
        return "d-" + self.id


def _hit(nid, kind="claim", metadata=None):
    return SimpleNamespace(node=_Node(id=nid, kind=kind, metadata=metadata or {}))


def _reader(refused=(), changed=(), partial=(), fatal=None, roots=None):
    def read(database, nid, artifact_root=None):
        if roots is not None:
            roots[nid] = artifact_root
        if fatal and nid == fatal:
            raise NoteReadError("read_failed_or_inconsistent")
        if nid in refused:
            raise NoteReadError("artifact_digest_mismatch")
        return {"current_id": nid + "_v2" if nid in changed else nid, "computed_node_digest": "d-" + nid,
                "body_complete": nid not in partial, "artifact": {"verification": "NOT_REQUESTED"}}
    return read


def _ids(hits):
    return [h.node.id for h in hits]


def test_consistent_cards_pass_in_order_and_evidence_is_skipped(tmp_path) -> None:
    hits = [_hit("a"), _hit("e", kind="evidence"), _hit("b")]
    diag = []
    out = consistent_hits(hits, tmp_path / "brain.sqlite3", deadline=float("inf"), reader=_reader(), diagnostics=diag)
    assert _ids(out) == ["a", "b"]
    assert [d["status"] for d in diag] == ["CONSISTENT", "CONSISTENT"]


def test_refusal_modes_drop_first_and_all(tmp_path) -> None:
    db = tmp_path / "brain.sqlite3"
    hits = [_hit("a"), _hit("b"), _hit("c")]
    later = _reader(refused={"b"})
    assert _ids(consistent_hits(hits, db, deadline=float("inf"), reader=later)) == ["a", "c"]
    assert _ids(consistent_hits(hits, db, deadline=float("inf"), reader=later, on_refusal="first")) == ["a", "c"]
    with pytest.raises(SelectionUnavailable, match="selected_card_refused"):
        consistent_hits(hits, db, deadline=float("inf"), reader=later, on_refusal="all")
    head = _reader(refused={"a"})
    assert _ids(consistent_hits(hits, db, deadline=float("inf"), reader=head)) == ["b", "c"]
    with pytest.raises(SelectionUnavailable, match="selected_card_refused"):
        consistent_hits(hits, db, deadline=float("inf"), reader=head, on_refusal="first")
    with pytest.raises(ValueError):
        consistent_hits(hits, db, deadline=float("inf"), reader=head, on_refusal="maybe")


def test_store_level_failures_refuse_whatever_the_mode(tmp_path) -> None:
    with pytest.raises(SelectionUnavailable, match="read_failed_or_inconsistent"):
        consistent_hits([_hit("a"), _hit("b")], tmp_path / "x", deadline=float("inf"),
                        reader=_reader(fatal="b"), on_refusal="drop")


def test_a_card_changed_since_ranking_or_partial_refuses(tmp_path) -> None:
    db = tmp_path / "brain.sqlite3"
    with pytest.raises(SelectionUnavailable, match="candidate_changed_since_ranking"):
        consistent_hits([_hit("a")], db, deadline=float("inf"), reader=_reader(changed={"a"}))
    with pytest.raises(SelectionUnavailable, match="incomplete_note_contract"):
        consistent_hits([_hit("a")], db, deadline=float("inf"), reader=_reader(partial={"a"}))


def test_the_deadline_refuses_instead_of_returning_a_partial_pool(tmp_path) -> None:
    ticks = iter([0.0, 0.0, 5.0])
    with pytest.raises(SelectionUnavailable, match="integrity_budget_exhausted"):
        consistent_hits([_hit("a"), _hit("b")], tmp_path / "x", deadline=1.0, reader=_reader(),
                        clock=lambda: next(ticks))
    roots = {}
    with pytest.raises(SelectionUnavailable, match="integrity_budget_exhausted"):
        consistent_hits([_hit("a")], tmp_path / "x", deadline=1.0, reader=_reader(roots=roots), clock=lambda: 5.0)
    assert roots == {}, "no note is read once the budget is spent"


def test_artifacts_are_checked_only_when_the_objects_folder_exists(tmp_path) -> None:
    db = tmp_path / "brain.sqlite3"
    hits = [_hit("a", metadata={"artifact_digest": "x"}), _hit("b")]
    roots = {}
    consistent_hits(hits, db, deadline=float("inf"), reader=_reader(roots=roots))
    assert roots == {"a": None, "b": None}
    (tmp_path / "objects").mkdir()
    consistent_hits(hits, db, deadline=float("inf"), reader=_reader(roots=roots))
    assert roots == {"a": tmp_path / "objects", "b": None}


def _overlay_store(tmp_path):
    from math_brain.models import ClaimKind
    from math_brain.overlay import ContentAddressedTextStore, MathNodeRevision, SourceProvenance, append_revision
    from math_brain.store import ResearchStore

    # laid out as tools/integrate_drafts.py builds it: the object store's root is <store>/objects, so the files sit
    # at objects/objects/sha256/.. and every artifact path reads "objects/sha256/.." relative to that root
    db = tmp_path / "brain.sqlite3"
    rev = MathNodeRevision("probability", "chebyshev", ClaimKind.THEOREM, "Chebyshev inequality",
                           "P(|X - EX| >= t) <= Var X / t^2 for t > 0.",
                           SourceProvenance("textbook", "ed2", "thm 3.1", "test"))
    with ResearchStore(db) as store:
        stored = append_revision(store, ContentAddressedTextStore(tmp_path / "objects"), rev)
    return db, stored


def test_a_real_store_with_its_objects_verifies_the_artifact(tmp_path) -> None:
    db, stored = _overlay_store(tmp_path)
    hit = SimpleNamespace(node=stored.node)
    diag = []
    assert consistent_hits([hit], db, deadline=float("inf"), diagnostics=diag) == [hit]
    assert diag[-1]["artifact"] == "HASH_MATCHED"
    (tmp_path / "objects" / stored.artifact.relative_path).write_text("tampered\n", encoding="utf-8")
    diag = []
    assert consistent_hits([hit], db, deadline=float("inf"), diagnostics=diag) == []
    assert diag[-1]["status"] == "REFUSED" and diag[-1]["reason"] == "artifact_digest_mismatch"
