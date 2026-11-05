"""Leiden CPM communities and the ID-only background activation built on them."""
import pytest

from math_brain.background_hubs import CommunityIndex, _projection, leiden_cpm
from math_brain.models import EdgeKind, GraphEdge


def _edge(a, b, kind=EdgeKind.DEPENDS_ON, weight=1.0):
    return GraphEdge(a, b, kind, "test link", weight=weight)


def _two_triangles():
    left = [("a1", "a2"), ("a2", "a3"), ("a1", "a3")]
    right = [("b1", "b2"), ("b2", "b3"), ("b1", "b3")]
    edges = [_edge(x, y) for x, y in left + right] + [_edge("a3", "b1", weight=0.05)]
    ids = ["a1", "a2", "a3", "b1", "b2", "b3"]
    return ids, tuple(edges)


def test_two_triangles_joined_by_a_weak_bridge_split_in_two() -> None:
    ids, edges = _two_triangles()
    communities, levels = leiden_cpm(ids, _projection(ids, edges), resolution=0.5)
    assert communities == (("a1", "a2", "a3"), ("b1", "b2", "b3"))
    assert levels >= 1
    again, _ = leiden_cpm(ids, _projection(ids, edges), resolution=0.5)
    assert again == communities


def test_bad_parameters_and_weights_are_refused() -> None:
    with pytest.raises(ValueError):
        leiden_cpm(["a", "b"], [("a", "b", 1.0)], resolution=0)
    with pytest.raises(ValueError):
        leiden_cpm(["a", "b"], [("a", "b", 1.0)], theta=0)
    with pytest.raises(ValueError):
        leiden_cpm(["a", "b"], [("a", "b", -1.0)])
    with pytest.raises(ValueError):
        leiden_cpm(["a", "b"], [("a", "b", float("nan"))])


def test_projection_drops_warnings_and_non_positive_kinds_and_keeps_the_max() -> None:
    edges = [
        _edge("x", "y", weight=0.4),
        _edge("y", "x", EdgeKind.PROVES, weight=0.9),
        _edge("x", "z", EdgeKind.REFUTES),
        _edge("x", "z", EdgeKind.CITES),
        _edge("y", "outside"),
    ]
    assert _projection(["x", "y", "z"], edges) == (("x", "y", 0.9),)


def test_activation_names_community_peers_and_never_the_seed() -> None:
    ids, edges = _two_triangles()
    index = CommunityIndex.build(ids, edges, resolution=0.5)
    hits = index.activate(["a1"], eligible_ids=ids, edges=edges, limit=5)
    names = [h.node_id for h in hits]
    assert "a1" not in names
    assert set(names[:2]) == {"a2", "a3"}
    assert all(h.direct and h.seed_ids == ("a1",) for h in hits if h.node_id in ("a2", "a3"))
    assert len(index.activate(["a1"], eligible_ids=ids, edges=edges, limit=1)) == 1
    with pytest.raises(ValueError):
        index.activate(["a1"], eligible_ids=ids, edges=edges, limit=0)


def test_a_changed_graph_gets_no_background_from_a_stale_index() -> None:
    ids, edges = _two_triangles()
    index = CommunityIndex.build(ids, edges, resolution=0.5)
    changed = edges + (_edge("a1", "b3"),)
    assert index.activate(["a1"], eligible_ids=ids, edges=changed) == ()
    assert index.activate(["a1"], eligible_ids=ids[:-1], edges=edges) == ()


def test_prepared_index_matches_the_slow_path_and_refuses_stale_input() -> None:
    ids, edges = _two_triangles()
    index = CommunityIndex.build(ids, edges, resolution=0.5)
    prepared = index.prepare(eligible_ids=ids, edges=edges)
    slow = index.activate(["b2"], eligible_ids=ids, edges=edges)
    assert prepared.activate(["b2"], eligible_ids=ids, edges=edges) == slow
    changed = edges + (_edge("a1", "b3"),)
    assert prepared.activate(["b2"], eligible_ids=ids, edges=changed) == ()
    assert prepared.activate(["b2"], eligible_ids=ids[:-1], edges=edges) == ()
    with pytest.raises(TypeError):
        index.prepare(eligible_ids=ids, edges=list(edges))
    with pytest.raises(ValueError):
        index.prepare(eligible_ids=ids, edges=changed)
