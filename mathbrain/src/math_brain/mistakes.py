"""Structured mathematical mistakes and executable review reflexes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from .models import (
    SCHEMA_VERSION,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    content_hash,
    new_id,
    utc_now,
)


class MistakeKind(StrEnum):
    ARITHMETIC = "arithmetic"
    DOMAIN = "domain"
    QUANTIFIER = "quantifier"
    SIGN = "sign"
    LIMIT_INTERCHANGE = "limit_interchange"
    UNJUSTIFIED_IMPLICATION = "unjustified_implication"
    HIDDEN_ASSUMPTION = "hidden_assumption"
    FORMALIZATION_MISMATCH = "formalization_mismatch"
    CITATION_MISMATCH = "citation_mismatch"
    NUMERICAL_INSTABILITY = "numerical_instability"
    OTHER = "other"


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_steps(name: str, values: tuple[str, ...]) -> None:
    if not values:
        raise ValueError(f"{name} must contain at least one specific step")
    normalized = [value.strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError(f"{name} must not contain empty steps")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicate steps")


@dataclass(frozen=True, slots=True)
class MistakeRecord:
    """A minimal failure site, its cause, repair, and reusable prevention action."""

    claim_id: str
    kind: MistakeKind
    failing_step: str
    cause: str
    repair_steps: tuple[str, ...]
    prevention_action: str
    affected_assumptions: tuple[str, ...] = ()
    counterexample: str | None = None
    id: str = field(default_factory=lambda: new_id("mistake"))
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name, value in (
            ("claim_id", self.claim_id),
            ("failing_step", self.failing_step),
            ("cause", self.cause),
            ("prevention_action", self.prevention_action),
        ):
            _require_text(name, value)
        _require_steps("repair_steps", self.repair_steps)
        if self.counterexample is not None:
            _require_text("counterexample", self.counterexample)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported mistake schema version: {self.schema_version}")

    def digest(self) -> str:
        return content_hash(asdict(self))

    def to_node(self) -> GraphNode:
        body_parts = [
            f"Cause: {self.cause}",
            *(f"Repair: {step}" for step in self.repair_steps),
            f"Prevention: {self.prevention_action}",
        ]
        if self.counterexample is not None:
            body_parts.append(f"Counterexample: {self.counterexample}")
        return GraphNode(
            id=self.id,
            kind=NodeKind.MISTAKE,
            title=f"{self.kind.value}: {self.failing_step}",
            body="\n".join(body_parts),
            metadata={
                "claim_id": self.claim_id,
                "mistake_kind": self.kind.value,
                "affected_assumptions": self.affected_assumptions,
            },
            created_at=self.created_at,
        )


@dataclass(frozen=True, slots=True)
class ReflexRecord:
    """A concrete action sequence activated by explicit mathematical cues."""

    activation_cues: tuple[str, ...]
    action_steps: tuple[str, ...]
    required_evidence: tuple[str, ...]
    stop_condition: str
    source_mistake_ids: tuple[str, ...]
    id: str = field(default_factory=lambda: new_id("reflex"))
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _require_steps("activation_cues", self.activation_cues)
        _require_steps("action_steps", self.action_steps)
        _require_steps("required_evidence", self.required_evidence)
        _require_steps("source_mistake_ids", self.source_mistake_ids)
        _require_text("stop_condition", self.stop_condition)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported reflex schema version: {self.schema_version}")

    def digest(self) -> str:
        return content_hash(asdict(self))

    def to_node(self) -> GraphNode:
        return GraphNode(
            id=self.id,
            kind=NodeKind.REFLEX,
            title="When " + "; ".join(self.activation_cues),
            body="\n".join(
                [
                    *(f"Action: {step}" for step in self.action_steps),
                    *(f"Evidence: {item}" for item in self.required_evidence),
                    f"Stop: {self.stop_condition}",
                ]
            ),
            metadata={"source_mistake_ids": self.source_mistake_ids},
            created_at=self.created_at,
        )


def memory_edges(mistake: MistakeRecord, reflex: ReflexRecord) -> tuple[GraphEdge, ...]:
    """Build typed warning edges without changing any claim's truth status."""

    if mistake.id not in reflex.source_mistake_ids:
        raise ValueError("reflex must cite the supplied mistake record")
    return (
        GraphEdge(
            source_id=mistake.claim_id,
            target_id=mistake.id,
            kind=EdgeKind.FAILS_UNDER,
            rationale="The recorded failure applies under the stated mathematical conditions",
            metadata={"mistake_kind": mistake.kind.value},
        ),
        GraphEdge(
            source_id=mistake.id,
            target_id=reflex.id,
            kind=EdgeKind.TRIGGERS,
            rationale="Replaying this mistake activates a specific verification reflex",
            metadata={"required_evidence": reflex.required_evidence},
        ),
    )
