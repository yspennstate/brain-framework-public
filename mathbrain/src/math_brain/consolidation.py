"""Quarantined SWS/REM-style replay that can propose, but never establish, truth."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import combinations
from typing import Iterable, NoReturn

from .mistakes import MistakeRecord, ReflexRecord
from .models import EdgeKind, GraphEdge, GraphNode, NodeKind, content_hash
from .retrieval import GuardExclusion, RetrievalGuard, guard_nodes, index_signals


class ConsolidationTaskKind(StrEnum):
    ORPHAN_REVIEW = "orphan_review"
    CONTRADICTION_REVIEW = "contradiction_review"
    MISSING_EVIDENCE = "missing_evidence"
    HIGH_DEGREE_REVIEW = "high_degree_review"
    REFLEX_REHEARSAL = "reflex_rehearsal"
    ASSOCIATION_REVIEW = "association_review"


class ConsolidationPhase(StrEnum):
    SWS_REPLAY = "sws_replay"
    REM_ASSOCIATION = "rem_association"


class ProposalStatus(StrEnum):
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class ConsolidationTask:
    kind: ConsolidationTaskKind
    node_ids: tuple[str, ...]
    reason: str
    priority: int


@dataclass(frozen=True, slots=True)
class ConsolidationProposal:
    """A review-only hypothesis with no conversion path to verification evidence."""

    phase: ConsolidationPhase
    kind: ConsolidationTaskKind
    node_ids: tuple[str, ...]
    hypothesis: str
    required_actions: tuple[str, ...]
    priority: int
    status: ProposalStatus = ProposalStatus.QUARANTINED
    can_promote_truth: bool = False
    id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.status is not ProposalStatus.QUARANTINED:
            raise ValueError("consolidation proposals must remain quarantined")
        if self.can_promote_truth:
            raise ValueError("consolidation proposals cannot promote truth")
        if not self.node_ids or len(set(self.node_ids)) != len(self.node_ids):
            raise ValueError("proposal node ids must be nonempty and unique")
        if not self.hypothesis.strip():
            raise ValueError("proposal hypothesis is required")
        if not self.required_actions or any(
            not action.strip() for action in self.required_actions
        ):
            raise ValueError("proposal requires at least one specific review action")
        if not 0 <= self.priority <= 100:
            raise ValueError("proposal priority must be in [0, 100]")
        object.__setattr__(self, "id", "proposal_" + self.digest().removeprefix("sha256:")[:24])

    def _payload(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "kind": self.kind.value,
            "node_ids": self.node_ids,
            "hypothesis": self.hypothesis,
            "required_actions": self.required_actions,
            "priority": self.priority,
            "status": self.status.value,
            "can_promote_truth": self.can_promote_truth,
        }

    def digest(self) -> str:
        return content_hash(self._payload())

    def as_verification_evidence(self) -> NoReturn:
        raise PermissionError(
            "a consolidation proposal is not evidence; run the ordinary verification gates"
        )


@dataclass(frozen=True, slots=True)
class ConsolidationCycle:
    proposals: tuple[ConsolidationProposal, ...]
    exclusions: tuple[GuardExclusion, ...]
    input_digests: tuple[str, ...]

    @property
    def can_promote_truth(self) -> bool:
        return False


def propose_tasks(
    nodes: Iterable[GraphNode], edges: Iterable[GraphEdge]
) -> list[ConsolidationTask]:
    node_list = list(nodes)
    edge_list = list(edges)
    degree: Counter[str] = Counter()
    evidence_links: Counter[str] = Counter()
    tasks: list[ConsolidationTask] = []
    for edge in edge_list:
        degree[edge.source_id] += 1
        degree[edge.target_id] += 1
        if edge.kind is EdgeKind.VERIFIED_BY:
            evidence_links[edge.source_id] += 1
        if edge.kind in {EdgeKind.REFUTES, EdgeKind.FAILS_UNDER}:
            tasks.append(
                ConsolidationTask(
                    ConsolidationTaskKind.CONTRADICTION_REVIEW,
                    tuple(sorted((edge.source_id, edge.target_id))),
                    f"negative edge {edge.kind.value} requires explicit resolution",
                    100,
                )
            )
        elif edge.kind is EdgeKind.TRIGGERS:
            tasks.append(
                ConsolidationTask(
                    ConsolidationTaskKind.REFLEX_REHEARSAL,
                    (edge.source_id, edge.target_id),
                    "warning trigger should rehearse its concrete review action",
                    80,
                )
            )
    for node in node_list:
        if degree[node.id] == 0:
            tasks.append(
                ConsolidationTask(
                    ConsolidationTaskKind.ORPHAN_REVIEW,
                    (node.id,),
                    "node is unreachable through the association graph",
                    40,
                )
            )
        if node.kind is NodeKind.CLAIM and evidence_links[node.id] == 0:
            tasks.append(
                ConsolidationTask(
                    ConsolidationTaskKind.MISSING_EVIDENCE,
                    (node.id,),
                    "claim has no verification evidence edge",
                    70,
                )
            )
        if degree[node.id] >= 20:
            tasks.append(
                ConsolidationTask(
                    ConsolidationTaskKind.HIGH_DEGREE_REVIEW,
                    (node.id,),
                    "high-degree hub may dominate retrieval and needs edge-quality review",
                    30,
                )
            )
    return sorted(tasks, key=lambda task: (-task.priority, task.kind.value, task.node_ids))


def _task_actions(task: ConsolidationTask) -> tuple[str, ...]:
    if task.kind is ConsolidationTaskKind.CONTRADICTION_REVIEW:
        return (
            "State the two incompatible scopes and assumptions side by side.",
            "Construct a counterexample or independent proof that resolves the conflict.",
            "Record ordinary verification evidence before changing either claim's status.",
        )
    if task.kind is ConsolidationTaskKind.MISSING_EVIDENCE:
        return (
            "Decompose the claim into explicit proof or computation obligations.",
            "Run an independent verifier appropriate to each obligation's scope.",
        )
    if task.kind is ConsolidationTaskKind.ORPHAN_REVIEW:
        return (
            "Confirm the node's provenance and mathematical scope.",
            "Either propose a typed edge for human review or document why it stays isolated.",
        )
    if task.kind is ConsolidationTaskKind.REFLEX_REHEARSAL:
        return (
            "Load the reflex's ordered action steps and stop condition.",
            "Execute the steps on a synthetic replay and collect the required evidence.",
            "Revise or retire the reflex if it fails to catch the recorded mistake.",
        )
    return (
        "Audit every incident edge for type, direction, scope, and evidence.",
        "Remove no edge until an ordinary review records the replacement rationale.",
    )


def _meaningful_math_tokens(node: GraphNode) -> frozenset[str]:
    signals = index_signals(node.title + " " + node.body)
    return frozenset(
        token
        for token in signals.math_tokens
        if not token.startswith("v") and not token.startswith("punct:")
    )


def _rem_candidates(
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
    *,
    max_proposals: int,
) -> list[ConsolidationProposal]:
    if max_proposals == 0 or len(nodes) < 2:
        return []
    connected = {
        frozenset((edge.source_id, edge.target_id))
        for edge in edges
    }
    signals = {node.id: index_signals(node.title + " " + node.body) for node in nodes}
    math_tokens = {node.id: _meaningful_math_tokens(node) for node in nodes}
    token_postings: dict[str, list[str]] = defaultdict(list)
    fingerprint_postings: dict[str, list[str]] = defaultdict(list)
    for node in sorted(nodes, key=lambda item: item.id):
        for token in math_tokens[node.id]:
            token_postings[token].append(node.id)
        for fingerprint in signals[node.id].formula_fingerprints:
            fingerprint_postings[fingerprint].append(node.id)

    # REM association is deliberately bounded. Very common formula features are
    # skipped rather than expanding into a quadratic candidate cloud.
    posting_cap = 64
    candidate_budget = max(200, max_proposals * 100)
    candidate_pairs: set[tuple[str, str]] = set()
    feature_groups = [
        *(sorted(fingerprint_postings.items())),
        *(sorted(token_postings.items())),
    ]
    budget_exhausted = False
    for _, posting in feature_groups:
        if not 1 < len(posting) <= posting_cap:
            continue
        for left_id, right_id in combinations(posting, 2):
            pair = (left_id, right_id)
            if frozenset(pair) in connected:
                continue
            candidate_pairs.add(pair)
            if len(candidate_pairs) >= candidate_budget:
                budget_exhausted = True
                break
        if budget_exhausted:
            break

    candidates: list[tuple[float, str, str, tuple[str, ...]]] = []
    for left_id, right_id in sorted(candidate_pairs):
        shared_fingerprints = set(signals[left_id].formula_fingerprints).intersection(
            signals[right_id].formula_fingerprints
        )
        shared_tokens = math_tokens[left_id].intersection(math_tokens[right_id])
        if not shared_fingerprints and len(shared_tokens) < 2:
            continue
        denominator = max(1, len(math_tokens[left_id].union(math_tokens[right_id])))
        similarity = len(shared_tokens) / denominator + 2.0 * len(shared_fingerprints)
        reasons = tuple(sorted(shared_tokens))
        candidates.append((similarity, left_id, right_id, reasons))

    proposals: list[ConsolidationProposal] = []
    for _, left_id, right_id, shared_tokens in sorted(
        candidates, key=lambda item: (-item[0], item[1], item[2])
    )[:max_proposals]:
        token_summary = ", ".join(shared_tokens[:4]) or "an alpha-normalized formula"
        proposals.append(
            ConsolidationProposal(
                phase=ConsolidationPhase.REM_ASSOCIATION,
                kind=ConsolidationTaskKind.ASSOCIATION_REVIEW,
                node_ids=(left_id, right_id),
                hypothesis=(
                    "Shared formula structure may indicate an analogy; it does not establish one "
                    f"(signals: {token_summary})."
                ),
                required_actions=(
                    "State a precise candidate relationship with domains and quantifiers.",
                    "Search for the smallest counterexample before attempting a proof.",
                    "If the relationship survives, verify it independently before adding an edge.",
                ),
                priority=25,
            )
        )
    return proposals


def consolidate(
    nodes: Iterable[GraphNode],
    edges: Iterable[GraphEdge],
    *,
    mistakes: Iterable[MistakeRecord] = (),
    reflexes: Iterable[ReflexRecord] = (),
    guard: RetrievalGuard | None = None,
    max_rem_proposals: int = 25,
) -> ConsolidationCycle:
    """Generate deterministic, quarantined replay proposals without mutating inputs."""

    if max_rem_proposals < 0:
        raise ValueError("max_rem_proposals must be nonnegative")
    all_nodes = tuple(nodes)
    all_edges = tuple(edges)
    eligible_nodes, exclusions = guard_nodes(all_nodes, guard)
    eligible_ids = {node.id for node in eligible_nodes}
    eligible_edges = tuple(
        edge
        for edge in all_edges
        if edge.source_id in eligible_ids and edge.target_id in eligible_ids
    )

    proposals = [
        ConsolidationProposal(
            phase=ConsolidationPhase.SWS_REPLAY,
            kind=task.kind,
            node_ids=task.node_ids,
            hypothesis=task.reason,
            required_actions=_task_actions(task),
            priority=task.priority,
        )
        for task in propose_tasks(eligible_nodes, eligible_edges)
    ]

    mistake_map = {mistake.id: mistake for mistake in mistakes}
    for reflex in sorted(reflexes, key=lambda item: item.id):
        source_mistakes = [
            mistake_map[mistake_id]
            for mistake_id in reflex.source_mistake_ids
            if mistake_id in mistake_map
            and mistake_map[mistake_id].claim_id in eligible_ids
        ]
        for mistake in sorted(source_mistakes, key=lambda item: item.id):
            proposals.append(
                ConsolidationProposal(
                    phase=ConsolidationPhase.SWS_REPLAY,
                    kind=ConsolidationTaskKind.REFLEX_REHEARSAL,
                    node_ids=(mistake.claim_id, mistake.id, reflex.id),
                    hypothesis="Replaying a documented failure may prevent its recurrence.",
                    required_actions=(
                        *reflex.action_steps,
                        *(f"Collect evidence: {item}" for item in reflex.required_evidence),
                        f"Stop condition: {reflex.stop_condition}",
                    ),
                    priority=80,
                )
            )

    proposals.extend(
        _rem_candidates(
            eligible_nodes,
            eligible_edges,
            max_proposals=max_rem_proposals,
        )
    )
    ordered = tuple(
        sorted(
            proposals,
            key=lambda proposal: (
                -proposal.priority,
                proposal.phase.value,
                proposal.kind.value,
                proposal.node_ids,
                proposal.id,
            ),
        )
    )
    input_digests = tuple(
        sorted([*(node.digest() for node in all_nodes), *(edge.digest() for edge in all_edges)])
    )
    return ConsolidationCycle(ordered, exclusions, input_digests)
