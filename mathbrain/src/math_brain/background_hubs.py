"""Leiden CPM communities and ID-only background activation (no foreground writes).

Independent implementation of Algorithm A.2 in Traag, Waltman & van Eck (2019),
https://arxiv.org/abs/1810.08473. Weighted undirected positive graph, recursive
node masses, fast local moves, stochastic constrained refinement, then aggregation
on the refined partition while retaining the unrefined partition. Two iterations
are a bounded experiment, not a claim of asymptotic subset optimality.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import json
import math
import random
from types import MappingProxyType
from typing import Iterable
from .models import GraphEdge

POSITIVE_RELATIONS = frozenset({
    "depends_on", "defines", "proves", "generalizes", "specializes", "analogous_to"
})


def _groups(part):
    groups = defaultdict(set)
    for v, c in enumerate(part):
        groups[c].add(v)
    return groups


def _normalise(part):
    labels = {}
    return [labels.setdefault(c, len(labels)) for c in part]


def _move_fast(adj, masses, part, gamma, rng):
    groups = _groups(part)
    totals = {c: sum(masses[v] for v in vs) for c, vs in groups.items()}
    order = list(range(len(adj)))
    rng.shuffle(order)
    queue, queued = deque(order), set(order)
    next_label = max(groups, default=-1) + 1
    while queue:
        v = queue.popleft()
        queued.remove(v)
        old = part[v]
        links = defaultdict(float)
        for u, w in adj[v].items():
            if u != v:
                links[part[u]] += w
        old_value = links[old] - gamma * masses[v] * (totals[old] - masses[v])
        best, gain = old, 0.0
        for c in sorted(set(links) | {next_label}):
            if c == old:
                continue
            delta = links[c] - gamma * masses[v] * totals.get(c, 0) - old_value
            if delta > gain + 1e-12:
                best, gain = c, delta
        if best == old:
            continue
        groups[old].remove(v)
        totals[old] -= masses[v]
        groups[best].add(v)
        totals[best] = totals.get(best, 0) + masses[v]
        part[v] = best
        if best == next_label:
            next_label += 1
        for u in sorted(adj[v]):
            if part[u] != best and u not in queued:
                queue.append(u)
                queued.add(u)
    return _normalise(part)


def _refine(adj, masses, coarse, gamma, theta, rng):
    part = list(range(len(adj)))
    groups = {v: {v} for v in part}
    totals = dict(enumerate(masses))
    for subset in _groups(coarse).values():
        total = sum(masses[v] for v in subset)
        order = sorted(subset)
        rng.shuffle(order)
        for v in order:
            # Lines37 and39: well-connected nodes still singleton at this level.
            own = part[v]
            if len(groups[own]) != 1:
                continue
            cut = sum(w for u, w in adj[v].items() if u in subset and u != v)
            if cut < gamma * masses[v] * (total - masses[v]) - 1e-12:
                continue
            links = defaultdict(float)
            for u, w in adj[v].items():
                if u in subset and u != v:
                    links[part[u]] += w
            choices = []
            for c in sorted(set(links) | {own}):
                if not groups[c]:
                    continue
                boundary = sum(w for u in groups[c] for t, w in adj[u].items()
                               if t in subset and t not in groups[c])
                if boundary < gamma * totals[c] * (total - totals[c]) - 1e-12:
                    continue
                delta = 0.0 if c == own else links[c] - gamma * masses[v] * totals[c]
                if delta >= -1e-12:
                    choices.append((c, max(0.0, delta)))
            if not choices:
                continue
            # Line41: exp(delta/theta); subtract max to avoid overflow.
            maximum = max(d for _, d in choices)
            weights = [math.exp((d - maximum) / theta) for _, d in choices]
            draw = rng.random() * sum(weights)
            chosen = choices[-1][0]
            for (c, _), weight in zip(choices, weights):
                draw -= weight
                if draw <= 0:
                    chosen = c
                    break
            if chosen != own:
                groups[own].remove(v)
                groups[chosen].add(v)
                totals[chosen] += masses[v]
                totals[own] = 0
                part[v] = chosen
    return _normalise(part)


def leiden_cpm(node_ids: Iterable[str], links: Iterable[tuple[str, str, float]], *,
               resolution=0.05, theta=0.01, seed=152, iterations=2):
    """Return canonical connected communities; fail closed on exhausted levels."""
    if not math.isfinite(resolution) or resolution <= 0:
        raise ValueError("resolution must be finite and positive")
    if not math.isfinite(theta) or theta <= 0 or iterations < 1:
        raise ValueError("positive theta and iterations required")
    ids = sorted(set(node_ids))
    positions = {v: i for i, v in enumerate(ids)}
    base = [defaultdict(float) for _ in ids]
    for a, b, w in sorted(links):
        if a not in positions or b not in positions or a == b:
            continue
        if not math.isfinite(w) or w <= 0:
            raise ValueError("Leiden requires finite positive weights")
        a, b = positions[a], positions[b]
        base[a][b] += w
        base[b][a] += w
    rng = random.Random(seed)
    initial = list(range(len(ids)))
    levels = 0
    for _ in range(iterations):
        adj, masses = base, [1] * len(ids)
        members = [{i} for i in range(len(ids))]
        part = initial[:]
        for _level in range(100):
            levels += 1
            part = _move_fast(adj, masses, part, resolution, rng)
            flat = {}
            for c, group in _groups(part).items():
                for v in group:
                    for original in members[v]:
                        flat[original] = c
            initial = _normalise([flat[v] for v in range(len(ids))])
            if len(set(part)) == len(adj):
                break
            refined = _refine(adj, masses, part, resolution, theta, rng)
            groups = _groups(refined)
            new_adj = [defaultdict(float) for _ in groups]
            new_members, new_masses, new_part = [], [], []
            for c in range(len(groups)):
                group = groups[c]
                new_members.append(set().union(*(members[v] for v in group)))
                new_masses.append(sum(masses[v] for v in group))
                new_part.append(part[min(group)])
            for a, neighbors in enumerate(adj):
                for b, w in neighbors.items():
                    if a < b and refined[a] != refined[b]:
                        x, y = refined[a], refined[b]
                        new_adj[x][y] += w
                        new_adj[y][x] += w
            # Internal self-loops are objective constants, irrelevant to all moves.
            adj, masses, members, part = new_adj, new_masses, new_members, _normalise(new_part)
        else:
            raise RuntimeError("Leiden level budget exhausted; no partition published")
    communities = tuple(sorted(tuple(ids[v] for v in sorted(group))
                               for group in _groups(initial).values()))
    for community in communities:
        todo = [positions[community[0]]]
        allowed = {positions[v] for v in community}
        seen = set(todo)
        while todo:
            for u in base[todo.pop()]:
                if u in allowed and u not in seen:
                    seen.add(u)
                    todo.append(u)
        if seen != allowed:
            raise RuntimeError("Disconnected Leiden community; no partition published")
    return communities, levels


@dataclass(frozen=True, slots=True)
class BackgroundCandidate:
    node_id: str
    activation: float
    seed_ids: tuple[str, ...]
    community_id: str
    direct: bool


def _projection(node_ids, edges):
    ids = set(node_ids)
    links = defaultdict(float)
    for edge in edges:
        kind = str(edge.kind)
        if kind not in POSITIVE_RELATIONS or edge.is_warning:
            continue
        a, b = edge.source_id, edge.target_id
        if a not in ids or b not in ids or a == b:
            continue
        key = tuple(sorted((a, b)))
        # Multiple statements/directions cannot manufacture stronger similarity.
        links[key] = max(links[key], edge.weight)
    return tuple((a, b, w) for (a, b), w in sorted(links.items()))


def _fingerprint(ids, links):
    data = json.dumps([sorted(ids), links], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CommunityIndex:
    node_ids: tuple[str, ...]
    communities: tuple[tuple[str, ...], ...]
    links: tuple[tuple[str, str, float], ...]
    fingerprint: str
    levels: int

    @classmethod
    def build(cls, node_ids, edges, **config):
        ids = tuple(sorted(set(node_ids)))
        links = _projection(ids, edges)
        communities, levels = leiden_cpm(ids, links, **config)
        return cls(ids, communities, links, _fingerprint(ids, links), levels)

    def activate(self, seeds, *, eligible_ids, edges, limit=5):
        """ID-only candidates; guarded current graph must match the frozen index."""
        if limit <= 0:
            raise ValueError("background limit must be positive")
        ids = set(self.node_ids) & set(eligible_ids)
        links = _projection(ids, edges)
        if _fingerprint(ids, links) != self.fingerprint:
            return ()
        membership = {v: c for c in self.communities for v in c}
        adjacency = defaultdict(dict)
        for a, b, weight in links:
            adjacency[a][b] = weight
            adjacency[b][a] = weight
        return _activate(seeds, ids, membership, adjacency, limit)

    def prepare(self, *, eligible_ids, edges):
        """Validate once against an immutable GraphEdge tuple for repeated queries.

        Serving still checks current eligible IDs. A different tuple takes the
        fully validated slow path, so changing even one edge cannot use this cache.
        """
        if not isinstance(edges, tuple) or not all(type(e) is GraphEdge for e in edges):
            raise TypeError('Prepared background requires an immutable GraphEdge tuple')
        ids = frozenset(self.node_ids) & frozenset(eligible_ids)
        links = _projection(ids, edges)
        if _fingerprint(ids, links) != self.fingerprint:
            raise ValueError('Cannot prepare a stale background index')
        membership = MappingProxyType({v: c for c in self.communities for v in c})
        adjacency = defaultdict(dict)
        for a, b, weight in links:
            adjacency[a][b] = weight
            adjacency[b][a] = weight
        adjacency = MappingProxyType({v: MappingProxyType(a) for v, a in adjacency.items()})
        return PreparedCommunityIndex(self, ids, edges, membership, adjacency)


@dataclass(frozen=True, slots=True)
class PreparedCommunityIndex:
    index: CommunityIndex
    eligible_ids: frozenset[str]
    edges: tuple[GraphEdge, ...]
    membership: object
    adjacency: object

    def activate(self, seeds, *, eligible_ids, edges, limit=5):
        if limit <= 0:
            raise ValueError('background limit must be positive')
        current = frozenset(self.index.node_ids) & frozenset(eligible_ids)
        if current != self.eligible_ids:
            return ()
        if edges is not self.edges:
            return self.index.activate(seeds, eligible_ids=eligible_ids, edges=edges, limit=limit)
        return _activate(seeds, current, self.membership, self.adjacency, limit)


def _activate(seeds, ids, membership, adjacency, limit):
        seeds = sorted(set(seeds) & ids)
        score, origins, direct = defaultdict(float), defaultdict(set), set()
        for seed in seeds:
            community = membership[seed]
            # Background hub mass is divided; community size cannot multiply it.
            share = 1.0 / max(1, len(community) - 1)
            for v in community:
                if v not in seeds:
                    score[v] += share
                    origins[v].add(seed)
            for v, weight in adjacency.get(seed, {}).items():
                if v not in seeds:
                    score[v] += weight * (1.25 if v in community else 1.0)
                    origins[v].add(seed)
                    direct.add(v)
        divisor = max(1, len(seeds))
        ranked = sorted(score, key=lambda v: (-score[v], v))[:limit]
        return tuple(BackgroundCandidate(v, score[v] / divisor, tuple(sorted(origins[v])),
                     hashlib.sha256("\n".join(membership[v]).encode()).hexdigest()[:16],
                     v in direct) for v in ranked)
