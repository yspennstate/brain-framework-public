#!/usr/bin/env python3
"""Relation-aware, time-safe local activation over a small typed graph."""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    relation: str
    weight: float = 1.0
    confidence: float = 1.0
    polarity: int = 1
    event_ts: float = 0.0
    observed_ts: float = 0.0
    valid_from: float = 0.0
    valid_to: Optional[float] = None
    superseded: bool = False
    evidence_quality: str = "curated"
    model_version: str = "unknown"

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "Edge":
        return cls(
            src=str(row["src"]), dst=str(row["dst"]), relation=str(row["relation"]),
            weight=float(row.get("weight", 1.0)), confidence=float(row.get("confidence", 1.0)),
            polarity=1 if int(row.get("polarity", 1)) >= 0 else -1,
            event_ts=float(row.get("event_ts", 0.0)), observed_ts=float(row.get("observed_ts", 0.0)),
            valid_from=float(row.get("valid_from", row.get("event_ts", 0.0))),
            valid_to=None if row.get("valid_to") is None else float(row["valid_to"]),
            superseded=bool(row.get("superseded", False)),
            evidence_quality=str(row.get("evidence_quality", "curated")),
            model_version=str(row.get("model_version", "unknown")),
        )


@dataclass(frozen=True)
class ActivationResult:
    activations: Mapping[str, float]
    paths: Mapping[str, Tuple[Tuple[str, str, str], ...]]
    cutoff: float
    hops: int
    model_hash: str

    def path_to(self, node: str) -> Tuple[Tuple[str, str, str], ...]:
        return self.paths.get(node, ())


class TypedGraph:
    """A bounded local message-passing graph.

    Each relation is fan-out normalized independently, so a lexical hub cannot
    gain force merely by having thousands of edges.  Only edges known by the
    replay cutoff are eligible.  Two hops is the hard live maximum.
    """

    def __init__(self, edges: Iterable[Edge] = (), *, relation_gains: Optional[Mapping[str, float]] = None,
                 half_lives: Optional[Mapping[str, float]] = None, alpha: float = 0.55,
                 max_hops: int = 2, top_k: int = 96, cap: float = 8.0, model_version: str = "unknown"):
        self.edges = tuple(edges)
        self.relation_gains = dict(relation_gains or {})
        self.half_lives = dict(half_lives or {})
        self.alpha = max(0.0, min(1.0, float(alpha)))
        self.max_hops = max(1, min(2, int(max_hops)))
        self.top_k = max(4, min(2048, int(top_k)))
        self.cap = max(0.1, float(cap))
        self.model_version = str(model_version)
        self._by_src: Dict[str, List[Edge]] = defaultdict(list)
        for edge in self.edges:
            self._by_src[edge.src].append(edge)
        self.model_hash = self._digest()

    def _digest(self) -> str:
        rows = [
            (e.src, e.dst, e.relation, e.weight, e.confidence, e.polarity, e.event_ts,
             e.observed_ts, e.valid_from, e.valid_to, e.superseded, e.model_version)
            for e in self.edges
        ]
        payload = json.dumps({"edges": sorted(rows), "gains": self.relation_gains,
                              "half_lives": self.half_lives, "alpha": self.alpha,
                              "version": self.model_version}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TypedGraph":
        meta = dict(data.get("meta") or {})
        return cls(
            (Edge.from_mapping(row) for row in data.get("edges", [])),
            relation_gains=data.get("relation_gains") or {}, half_lives=data.get("half_lives") or {},
            alpha=float(meta.get("alpha", 0.55)), max_hops=int(meta.get("max_hops", 2)),
            top_k=int(meta.get("top_k", 96)), cap=float(meta.get("cap", 8.0)),
            model_version=str(meta.get("model_version", "unknown")),
        )

    @classmethod
    def from_json(cls, path: str) -> "TypedGraph":
        with open(path, encoding="utf-8") as handle:
            return cls.from_mapping(json.load(handle))

    def _eligible(self, edge: Edge, at_time: float, observed_cutoff: float) -> bool:
        return not edge.superseded and edge.event_ts <= at_time and edge.observed_ts <= observed_cutoff \
            and edge.valid_from <= at_time and (edge.valid_to is None or at_time < edge.valid_to)

    def _decay(self, edge: Edge, at_time: float) -> float:
        half_life = float(self.half_lives.get(edge.relation, 0.0) or 0.0)
        if half_life <= 0 or edge.event_ts <= 0:
            return 1.0
        age = max(0.0, at_time - edge.event_ts)
        return math.exp(-math.log(2.0) * age / half_life)

    def activate(self, seeds: Mapping[str, float] | Iterable[str], *, at_time: float,
                 observed_cutoff: Optional[float] = None, hops: Optional[int] = None,
                 top_k: Optional[int] = None) -> ActivationResult:
        cutoff = float(at_time if observed_cutoff is None else observed_cutoff)
        nhops = max(1, min(self.max_hops, int(self.max_hops if hops is None else hops)))
        keep = max(4, min(2048, int(self.top_k if top_k is None else top_k)))
        if isinstance(seeds, Mapping):
            current = {str(k): float(v) for k, v in seeds.items() if float(v) != 0.0}
        else:
            current = {str(node): 1.0 for node in seeds}
        total = dict(current)
        paths: Dict[str, Tuple[Tuple[str, str, str], ...]] = {node: () for node in current}

        for hop in range(1, nhops + 1):
            outgoing_norm: Dict[Tuple[str, str], float] = defaultdict(float)
            for src in current:
                for edge in self._by_src.get(src, ()):
                    if self._eligible(edge, at_time, cutoff):
                        outgoing_norm[(src, edge.relation)] += abs(edge.weight * edge.confidence)
            nxt: Dict[str, float] = defaultdict(float)
            best_path_score: Dict[str, float] = {}
            best_paths: Dict[str, Tuple[Tuple[str, str, str], ...]] = {}
            # ``current`` already contains attenuation from prior hops; apply
            # alpha once per traversed edge so an h-hop path is alpha**h.
            hop_gain = self.alpha
            for src, source_activation in current.items():
                for edge in self._by_src.get(src, ()):
                    if not self._eligible(edge, at_time, cutoff):
                        continue
                    denom = outgoing_norm.get((src, edge.relation), 0.0)
                    if denom <= 0:
                        continue
                    relation_gain = float(self.relation_gains.get(edge.relation, 1.0))
                    signed = float(edge.polarity) * source_activation * hop_gain * relation_gain
                    contribution = signed * edge.weight * edge.confidence * self._decay(edge, at_time) / denom
                    if not math.isfinite(contribution):
                        continue
                    nxt[edge.dst] += contribution
                    if abs(contribution) > best_path_score.get(edge.dst, -1.0):
                        best_path_score[edge.dst] = abs(contribution)
                        best_paths[edge.dst] = paths.get(src, ()) + ((src, edge.relation, edge.dst),)
            if not nxt:
                break
            ranked = sorted(nxt.items(), key=lambda item: (-abs(item[1]), item[0]))[:keep]
            current = {node: max(-self.cap, min(self.cap, value)) for node, value in ranked}
            for node, value in current.items():
                total[node] = max(-self.cap, min(self.cap, total.get(node, 0.0) + value))
                if node in best_paths:
                    paths[node] = best_paths[node]

        total = dict(sorted(total.items(), key=lambda item: (-abs(item[1]), item[0]))[:keep])
        return ActivationResult(total, {n: paths.get(n, ()) for n in total}, cutoff, nhops, self.model_hash)


def load_default_graph() -> TypedGraph:
    return TypedGraph.from_json(os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_graph.json"))
