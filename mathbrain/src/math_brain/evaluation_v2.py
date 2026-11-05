"""Hard, sealed v2 evaluation primitives.

The v1 study remains reproducible and deliberately keeps its ten-pair rotation and
component-weighted score.  This module defines a separate v2 design with a harder binary
estimand: a case succeeds only when the complete mathematical decision succeeds.  Solver
payloads contain opaque identifiers and salted view digests; truth, assignment variants,
control provenance, and verifier metadata stay evaluator-side.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
import threading
import types
import unicodedata
import weakref
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from itertools import islice
from typing import Any, Protocol

from .benchmark import SolverView
from .evaluation import Condition, Variant, Verdict
from .models import canonical_json

V2_PAIR_COUNT = 20
V2_MIN_CLAIM_CASE_COUNT = 40
V2_MAX_CASE_COUNT = 512
V2_FAULTY_PREVALENCE_LOWER = Fraction(35, 100)
V2_FAULTY_PREVALENCE_UPPER = Fraction(65, 100)
V2_SOLVER_BATCH_SCHEMA = "org.math-research-brain/evaluation-v2-solver-batch"
V2_SOLVER_BATCH_SCHEMA_VERSION = 1
V2_SUMMARY_SCHEMA = "org.math-research-brain/evaluation-v2-aggregate-summary"
V2_SUMMARY_SCHEMA_VERSION = 4

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_TEXT_BYTES = 256_000
_MAX_CONTEXT_UNIVERSE_BYTES = 16_000_000
_MAX_CASE_VIEW_UNIVERSE_BYTES = 64_000_000
_MAX_RELEVANCE_KEYS = 64
_MAX_CANDIDATE_JSON_BYTES = 1_000_000
_MAX_CANDIDATE_JSON_DEPTH = 32
_MAX_CANDIDATE_CONTAINER_ITEMS = 4096
_MAX_CANDIDATE_JSON_NODES = 32_768
_MAX_CANDIDATE_INTEGER_BITS = 4096
_MAX_OUTCOME_UNIVERSE_CANDIDATE_BYTES = 64_000_000
_MAX_OUTCOME_UNIVERSE_CANDIDATE_NODES = 2_097_152
_VERIFIER_REPLAY_CAPABILITY_KEY = os.urandom(32)
_ASSIGNMENT_STATE_KEY = os.urandom(32)
_PLAN_LOOKUP_ANCHOR_KEY = os.urandom(32)
_PLAN_LOOKUP_ANCHORS: dict[int, Any] = {}
_PLAN_LOOKUP_ANCHORS_LOCK = threading.RLock()


def _text(value: Any, label: str, *, max_bytes: int = 4096, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be a string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{label} must use canonical NFC Unicode")
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _digest(value: Any, label: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be sha256: followed by 64 lowercase hex digits")
    return value


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_json(value: Any) -> str:
    return _digest_bytes(canonical_json(value).encode("utf-8"))


def _seed(value: Any, label: str) -> bytes:
    if type(value) is not bytes or len(value) < 32:
        raise ValueError(f"{label} must be exact bytes containing at least 32 bytes")
    return value


def _rank(seed: bytes, domain: str, *parts: object) -> bytes:
    payload = bytearray(b"math-brain/evaluation-v2/rank\x00")
    for value in (domain, *parts):
        encoded = str(value).encode("utf-8")
        payload.extend(len(encoded).to_bytes(4, "big"))
        payload.extend(encoded)
    return hmac.new(seed, bytes(payload), hashlib.sha256).digest()


def _seed_commitment(seed: bytes, domain: str, universe_digest: str) -> str:
    return _digest_bytes(
        b"math-brain/evaluation-v2/seed-commitment\x00"
        + domain.encode("ascii")
        + b"\x00"
        + universe_digest.encode("ascii")
        + b"\x00"
        + seed
    )


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer at least {minimum}")
    return value


def _finite_float(value: Any, label: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite float")
    return value


def _bounded_tuple(values: Iterable[Any], label: str, *, maximum: int) -> tuple[Any, ...]:
    """Consume at most maximum + 1 items so an untrusted iterable cannot exhaust memory."""

    try:
        result = tuple(islice(iter(values), maximum + 1))
    except TypeError as exc:
        raise ValueError(f"{label} must be iterable") from exc
    if len(result) > maximum:
        raise ValueError(f"{label} may not exceed {maximum} items")
    return result


def _localization(value: Any, label: str) -> str | int:
    if type(value) is int:
        return _integer(value, label, minimum=1)
    if type(value) is str:
        return _text(value, label, max_bytes=256)
    raise ValueError(f"{label} must be a positive line number or root path")


@dataclass(frozen=True, slots=True)
class V2RenderedView:
    """Strict, bounded solver-visible prompt and proposed proof/trace."""

    prompt: str
    numbered_solution: tuple[str, ...]
    schema: str = "org.math-research-brain/evaluation-v2-rendered-view"
    schema_version: int = 1

    def __post_init__(self) -> None:
        _text(self.schema, "rendered-view source schema", max_bytes=128)
        version = _integer(self.schema_version, "rendered-view source schema version", minimum=1)
        if version > 1000:
            raise ValueError("rendered-view source schema version is implausibly large")
        _text(self.prompt, "rendered-view prompt", max_bytes=128_000)
        if type(self.numbered_solution) is not tuple:
            raise ValueError("rendered-view solution lines must be a canonical tuple")
        if not self.numbered_solution or len(self.numbered_solution) > 256:
            raise ValueError("rendered view requires between 1 and 256 solution lines")
        for line in self.numbered_solution:
            _text(line, "rendered-view solution line", max_bytes=16_384)
        if len(canonical_json(self.to_dict()).encode("utf-8")) > 512_000:
            raise ValueError("canonical rendered view exceeds the 512,000-byte budget")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> V2RenderedView:
        """Typed adapter for hard-case renderers; extra/private keys are rejected."""

        if type(value) is not dict:
            raise ValueError("rendered-view adapter input must be an object")
        expected = {"numbered_solution", "prompt", "schema", "schema_version"}
        if set(value) != expected:
            raise ValueError("rendered-view adapter requires exactly four public fields")
        lines = value["numbered_solution"]
        if type(lines) not in {list, tuple}:
            raise ValueError("rendered-view numbered_solution must be a sequence")
        return cls(
            prompt=value["prompt"],
            numbered_solution=_bounded_tuple(lines, "rendered-view numbered_solution", maximum=256),
            schema=value["schema"],
            schema_version=value["schema_version"],
        )

    @classmethod
    def from_solver_view(cls, view: SolverView) -> V2RenderedView:
        if type(view) is not SolverView:
            raise ValueError("rendered-view adapter requires an exact hardened SolverView")
        return cls.from_mapping(view.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "numbered_solution": list(self.numbered_solution),
            "prompt": self.prompt,
            "schema": self.schema,
            "schema_version": self.schema_version,
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.to_dict())


class V2HardCaseAdapter(Protocol):
    """Small structural interface the hard-case generator can expose evaluator-side."""

    case_id: str
    domain: str
    difficulty: str
    mechanism: str
    template_id: str
    clean_solver_view: SolverView
    faulty_solver_view: SolverView
    root_localization: str | int
    error_family: str
    repair_verifier_digest: str
    witness_verifier_digest: str

    def verify_repair(self, proposal: Any) -> tuple[bool, str]: ...

    def verify_witness(self, payload: Any) -> tuple[bool, str]: ...


@dataclass(frozen=True, slots=True)
class V2CaseSpec:
    """Evaluator-only truth plus the exact clean and faulty solver views."""

    case_id: str
    domain: str
    difficulty: str
    mechanism: str
    template_id: str
    clean_view: V2RenderedView
    faulty_view: V2RenderedView
    root_localization: str | int
    error_family: str
    repair_verifier_digest: str
    witness_verifier_digest: str

    def __post_init__(self) -> None:
        _text(self.case_id, "case id", max_bytes=128)
        _text(self.domain, "case domain", max_bytes=128)
        _text(self.difficulty, "case difficulty", max_bytes=128)
        _text(self.mechanism, "case fault mechanism", max_bytes=128)
        _text(self.template_id, "case template id", max_bytes=128)
        if (
            type(self.clean_view) is not V2RenderedView
            or type(self.faulty_view) is not V2RenderedView
        ):
            raise ValueError("case views must be exact bounded V2RenderedView records")
        if hmac.compare_digest(self.clean_view.digest, self.faulty_view.digest):
            raise ValueError("clean and faulty views must have different canonical bytes")
        if self.clean_view.prompt != self.faulty_view.prompt:
            raise ValueError("clean and faulty views must share the exact problem prompt")
        _localization(self.root_localization, "root localization")
        _text(self.error_family, "error family", max_bytes=128)
        _digest(self.repair_verifier_digest, "case repair verifier digest")
        _digest(self.witness_verifier_digest, "case witness verifier digest")
        if hmac.compare_digest(self.repair_verifier_digest, self.witness_verifier_digest):
            raise ValueError("repair replay and independent witness require distinct verifiers")

    @classmethod
    def from_hard_case(cls, case: V2HardCaseAdapter) -> V2CaseSpec:
        """Adapt a hard case without copying evaluator truth into its solver view."""

        def enum_text(value: Any, label: str) -> str:
            candidate = getattr(value, "value", value)
            return _text(candidate, label, max_bytes=128)

        required = (
            "case_id",
            "domain",
            "difficulty",
            "mechanism",
            "template_id",
            "clean_solver_view",
            "faulty_solver_view",
            "root_localization",
            "error_family",
            "repair_verifier_digest",
            "witness_verifier_digest",
        )
        if any(not hasattr(case, name) for name in required):
            raise ValueError("hard-case adapter is missing required evaluator metadata")
        return cls(
            case_id=case.case_id,
            domain=enum_text(case.domain, "hard-case domain"),
            difficulty=enum_text(case.difficulty, "hard-case difficulty"),
            mechanism=enum_text(case.mechanism, "hard-case mechanism"),
            template_id=enum_text(case.template_id, "hard-case template"),
            clean_view=V2RenderedView.from_solver_view(case.clean_solver_view),
            faulty_view=V2RenderedView.from_solver_view(case.faulty_solver_view),
            root_localization=case.root_localization,
            error_family=enum_text(case.error_family, "hard-case error family"),
            repair_verifier_digest=case.repair_verifier_digest,
            witness_verifier_digest=case.witness_verifier_digest,
        )

    @property
    def clean_view_digest(self) -> str:
        return self.clean_view.digest

    @property
    def faulty_view_digest(self) -> str:
        return self.faulty_view.digest

    @property
    def required_relevance_keys(self) -> tuple[str, str, str, str]:
        return tuple(
            sorted(
                (
                    f"domain:{self.domain}",
                    f"family:{self.error_family}",
                    f"mechanism:{self.mechanism}",
                    f"template:{self.template_id}",
                )
            )
        )

    @property
    def strata(self) -> tuple[tuple[str, str, str], ...]:
        return (
            ("domain_difficulty", self.domain, self.difficulty),
            ("family_difficulty", self.error_family, self.difficulty),
            ("mechanism_difficulty", self.mechanism, self.difficulty),
            ("template_difficulty", self.template_id, self.difficulty),
        )

    def to_commitment_record(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "clean_view_digest": self.clean_view_digest,
            "difficulty": self.difficulty,
            "domain": self.domain,
            "error_family": self.error_family,
            "faulty_view_digest": self.faulty_view_digest,
            "mechanism": self.mechanism,
            "repair_verifier_digest": self.repair_verifier_digest,
            "root_localization": self.root_localization,
            "template_id": self.template_id,
            "witness_verifier_digest": self.witness_verifier_digest,
        }


@dataclass(frozen=True, slots=True)
class V2ContextCard:
    """One relevant card; the control plan permutes whole cards without editing them."""

    case_id: str
    text: str
    relevance_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.case_id, "context case id", max_bytes=128)
        _text(self.text, "context text", max_bytes=_MAX_TEXT_BYTES, allow_empty=True)
        if type(self.relevance_keys) is not tuple:
            raise ValueError("relevance keys must be a canonical tuple")
        if not self.relevance_keys:
            raise ValueError("a context card requires at least one relevance key")
        if len(self.relevance_keys) > _MAX_RELEVANCE_KEYS:
            raise ValueError(f"a context card may not exceed {_MAX_RELEVANCE_KEYS} relevance keys")
        for key in self.relevance_keys:
            _text(key, "relevance key", max_bytes=128)
        if tuple(sorted(set(self.relevance_keys))) != self.relevance_keys:
            raise ValueError("relevance keys must be unique and sorted")

    @property
    def byte_length(self) -> int:
        return len(self.text.encode("utf-8"))

    @property
    def text_digest(self) -> str:
        return _digest_bytes(self.text.encode("utf-8"))

    def to_commitment_record(self) -> dict[str, Any]:
        return {
            "byte_length": self.byte_length,
            "case_id": self.case_id,
            "relevance_keys": list(self.relevance_keys),
            "text_digest": self.text_digest,
        }


def _normalized_cases(cases: Sequence[V2CaseSpec]) -> tuple[V2CaseSpec, ...]:
    result = _bounded_tuple(cases, "v2 case universe", maximum=V2_MAX_CASE_COUNT)
    if len(result) < 8:
        raise ValueError("v2 needs at least eight cases to vary prevalence within 35-65%")
    if len(result) > V2_MAX_CASE_COUNT:
        raise ValueError(f"v2 case universe may not exceed {V2_MAX_CASE_COUNT}")
    if any(type(case) is not V2CaseSpec for case in result):
        raise ValueError("v2 cases must be exact V2CaseSpec records")
    for case in result:
        _revalidate_case(case)
    if len({case.case_id for case in result}) != len(result):
        raise ValueError("v2 case ids must be unique")
    view_units = tuple((case.clean_view_digest, case.faulty_view_digest) for case in result)
    if len(set(view_units)) != len(view_units):
        raise ValueError("v2 case universe rejects exact clean/faulty solver-view clones")
    view_bytes = 0
    for case in result:
        view_bytes += len(canonical_json(case.clean_view.to_dict()).encode("utf-8"))
        view_bytes += len(canonical_json(case.faulty_view.to_dict()).encode("utf-8"))
        if view_bytes > _MAX_CASE_VIEW_UNIVERSE_BYTES:
            raise ValueError("v2 case-view universe exceeds the 64,000,000-byte budget")
    return tuple(sorted(result, key=lambda case: case.case_id))


def _normalized_contexts(contexts: Sequence[V2ContextCard]) -> tuple[V2ContextCard, ...]:
    result = _bounded_tuple(contexts, "v2 context universe", maximum=V2_MAX_CASE_COUNT)
    if any(type(card) is not V2ContextCard for card in result):
        raise ValueError("v2 contexts must be exact V2ContextCard records")
    for card in result:
        _revalidate_context(card)
    if len({card.case_id for card in result}) != len(result):
        raise ValueError("v2 context case ids must be unique")
    context_bytes = 0
    for card in result:
        context_bytes += card.byte_length
        context_bytes += sum(len(key.encode("utf-8")) for key in card.relevance_keys)
        if context_bytes > _MAX_CONTEXT_UNIVERSE_BYTES:
            raise ValueError("v2 context universe exceeds the 16,000,000-byte budget")
    return tuple(sorted(result, key=lambda card: card.case_id))


def _validate_case_context_bindings(
    cases: Sequence[V2CaseSpec], contexts: Sequence[V2ContextCard]
) -> None:
    case_by_id = {case.case_id: case for case in cases}
    context_by_id = {card.case_id: card for card in contexts}
    if set(case_by_id) != set(context_by_id):
        raise ValueError("every case must have exactly one relevant context card")
    for case_id, case in case_by_id.items():
        actual = set(context_by_id[case_id].relevance_keys)
        missing = set(case.required_relevance_keys) - actual
        if missing:
            raise ValueError(
                f"context {case_id!r} omits required canonical relevance keys: {sorted(missing)}"
            )


def _design_universe_digest(cases: Sequence[V2CaseSpec]) -> str:
    return _digest_json([case.to_commitment_record() for case in _normalized_cases(cases)])


def _context_universe_digest(contexts: Sequence[V2ContextCard]) -> str:
    return _digest_json([card.to_commitment_record() for card in _normalized_contexts(contexts)])


def _opaque_solver_case_id(seed: bytes, pair_id: int, case_id: str) -> str:
    return _digest_bytes(
        b"math-brain/evaluation-v2/opaque-case\x00"
        + seed
        + pair_id.to_bytes(2, "big")
        + case_id.encode("utf-8")
    )


def _assignment_payload_records(
    cases: Sequence[V2CaseSpec], seed: bytes
) -> tuple[dict[str, Any], ...]:
    """Materialize the condition-free assignment payload committed before dispatch."""

    normalized = _normalized_cases(cases)
    matrix = _fault_matrix(len(normalized), seed)
    records: list[dict[str, Any]] = []
    for pair_id in range(V2_PAIR_COUNT):
        order = sorted(
            range(len(normalized)),
            key=lambda case_index: _rank(seed, "case-order", pair_id, case_index),
        )
        for order_index, case_index in enumerate(order):
            case = normalized[case_index]
            variant = Variant.FAULTY if matrix[case_index][pair_id] else Variant.CLEAN
            view = case.faulty_view if variant is Variant.FAULTY else case.clean_view
            records.append(
                {
                    "case_id": case.case_id,
                    "order_index": order_index,
                    "pair_id": pair_id,
                    "solver_case_id": _opaque_solver_case_id(seed, pair_id, case.case_id),
                    "variant": variant.value,
                    "view_digest": view.digest,
                }
            )
    return tuple(records)


@dataclass(frozen=True, slots=True)
class V2DesignCommitment:
    """Commit the complete evaluator universe and both independent design seeds."""

    design_universe_digest: str
    context_universe_digest: str
    assignment_seed_commitment: str
    control_seed_commitment: str
    receipt_authority_digest: str
    gate_config_digest: str
    assignment_payload_digest: str
    active_control_payload_digest: str

    def __post_init__(self) -> None:
        for label, value in (
            ("design universe", self.design_universe_digest),
            ("context universe", self.context_universe_digest),
            ("assignment seed commitment", self.assignment_seed_commitment),
            ("control seed commitment", self.control_seed_commitment),
            ("receipt authority", self.receipt_authority_digest),
            ("gate config", self.gate_config_digest),
            ("assignment payload", self.assignment_payload_digest),
            ("active-control payload", self.active_control_payload_digest),
        ):
            _digest(value, label)

    @property
    def digest(self) -> str:
        return _digest_json(
            {
                "assignment_seed_commitment": self.assignment_seed_commitment,
                "assignment_payload_digest": self.assignment_payload_digest,
                "active_control_payload_digest": self.active_control_payload_digest,
                "context_universe_digest": self.context_universe_digest,
                "control_seed_commitment": self.control_seed_commitment,
                "design_universe_digest": self.design_universe_digest,
                "pair_count": V2_PAIR_COUNT,
                "prevalence_bounds": ["35/100", "65/100"],
                "receipt_authority_digest": self.receipt_authority_digest,
                "gate_config_digest": self.gate_config_digest,
            }
        )


def precommit_v2_design(
    cases: Sequence[V2CaseSpec],
    contexts: Sequence[V2ContextCard],
    *,
    assignment_seed: bytes,
    control_seed: bytes,
    receipt_authority_digest: str,
    gate: V2GateConfig | None = None,
) -> V2DesignCommitment:
    """Create the object that must be persisted before any solver is dispatched."""

    normalized_cases = _normalized_cases(cases)
    normalized_contexts = _normalized_contexts(contexts)
    _validate_case_context_bindings(normalized_cases, normalized_contexts)
    assignment = _seed(assignment_seed, "assignment seed")
    control = _seed(control_seed, "control seed")
    authority_digest = _digest(receipt_authority_digest, "receipt authority digest")
    gate_config = V2GateConfig() if gate is None else gate
    if type(gate_config) is not V2GateConfig:
        raise ValueError("v2 precommitment gate must be an exact V2GateConfig")
    _revalidate_gate(gate_config)
    design_digest = _design_universe_digest(normalized_cases)
    context_digest = _context_universe_digest(normalized_contexts)
    joint = _digest_json(
        {
            "context_universe_digest": context_digest,
            "design_universe_digest": design_digest,
            "gate_config_digest": gate_config.digest,
        }
    )
    assignment_seed_commitment = _seed_commitment(assignment, "assignment", joint)
    control_seed_commitment = _seed_commitment(control, "control", joint)
    assignment_payload_digest = _digest_json(
        _assignment_payload_records(normalized_cases, assignment)
    )
    active_control_payload_digest = _build_active_control(
        normalized_contexts,
        control,
        control_seed_commitment,
    ).digest
    return V2DesignCommitment(
        design_universe_digest=design_digest,
        context_universe_digest=context_digest,
        assignment_seed_commitment=assignment_seed_commitment,
        control_seed_commitment=control_seed_commitment,
        receipt_authority_digest=authority_digest,
        gate_config_digest=gate_config.digest,
        assignment_payload_digest=assignment_payload_digest,
        active_control_payload_digest=active_control_payload_digest,
    )


@dataclass(frozen=True, slots=True)
class V2ControlBinding:
    target_case_id: str
    source_case_id: str
    relevant_digest: str
    control_digest: str
    byte_length: int

    def __post_init__(self) -> None:
        _text(self.target_case_id, "control target", max_bytes=128)
        _text(self.source_case_id, "control source", max_bytes=128)
        if self.target_case_id == self.source_case_id:
            raise ValueError("active control must be a derangement")
        _digest(self.relevant_digest, "relevant context digest")
        _digest(self.control_digest, "control context digest")
        if hmac.compare_digest(self.relevant_digest, self.control_digest):
            raise ValueError("active control text must differ from its relevant context")
        _integer(self.byte_length, "control byte length")

    def to_sealed_dict(self) -> dict[str, Any]:
        return {
            "byte_length": self.byte_length,
            "control_digest": self.control_digest,
            "relevant_digest": self.relevant_digest,
            "source_case_id": self.source_case_id,
            "target_case_id": self.target_case_id,
        }


@dataclass(frozen=True, slots=True)
class V2ActiveControlPlan:
    bindings: tuple[V2ControlBinding, ...]
    control_seed_commitment: str

    def __post_init__(self) -> None:
        _digest(self.control_seed_commitment, "control seed commitment")
        if type(self.bindings) is not tuple or any(
            type(binding) is not V2ControlBinding for binding in self.bindings
        ):
            raise ValueError("active-control bindings must be exact records in a tuple")
        targets = [binding.target_case_id for binding in self.bindings]
        sources = [binding.source_case_id for binding in self.bindings]
        if not targets or len(set(targets)) != len(targets):
            raise ValueError("control targets must be a non-empty unique universe")
        if targets != sorted(targets):
            raise ValueError("control bindings must use canonical target-case order")
        if set(targets) != set(sources):
            raise ValueError("control sources must be a permutation of the target universe")

    @property
    def digest(self) -> str:
        return _digest_json(
            {
                "bindings": [binding.to_sealed_dict() for binding in self.bindings],
                "control_seed_commitment": self.control_seed_commitment,
                "matching": "exact_utf8_length_text_distinct_relevance_disjoint_derangement",
            }
        )


def _build_active_control(
    contexts: tuple[V2ContextCard, ...], seed: bytes, control_seed_commitment: str
) -> V2ActiveControlPlan:
    by_id = {card.case_id: card for card in contexts}
    candidates: dict[str, tuple[str, ...]] = {}
    for target in contexts:
        allowed = [
            source.case_id
            for source in contexts
            if source.case_id != target.case_id
            and source.byte_length == target.byte_length
            and not hmac.compare_digest(source.text_digest, target.text_digest)
            and set(source.relevance_keys).isdisjoint(target.relevance_keys)
        ]
        allowed.sort(key=lambda source_id: _rank(seed, "control-edge", target.case_id, source_id))
        if not allowed:
            raise ValueError(
                f"case {target.case_id!r} has no text-distinct exact-length "
                "relevance-disjoint control"
            )
        candidates[target.case_id] = tuple(allowed)

    source_owner: dict[str, str] = {}

    def augment(target_id: str, seen: set[str]) -> bool:
        for source_id in candidates[target_id]:
            if source_id in seen:
                continue
            seen.add(source_id)
            prior = source_owner.get(source_id)
            if prior is None or augment(prior, seen):
                source_owner[source_id] = target_id
                return True
        return False

    target_order = sorted(
        by_id,
        key=lambda target_id: (
            len(candidates[target_id]),
            _rank(seed, "control-target", target_id),
        ),
    )
    for target_id in target_order:
        if not augment(target_id, set()):
            raise ValueError("no relevance-disjoint exact-length control derangement exists")
    target_to_source = {target: source for source, target in source_owner.items()}
    bindings = []
    for target_id in sorted(by_id):
        source_id = target_to_source[target_id]
        target = by_id[target_id]
        source = by_id[source_id]
        if not set(target.relevance_keys).isdisjoint(source.relevance_keys):
            raise AssertionError("control matcher violated relevance disjointness")
        if target.byte_length != source.byte_length:
            raise AssertionError("control matcher violated length equality")
        bindings.append(
            V2ControlBinding(
                target_case_id=target_id,
                source_case_id=source_id,
                relevant_digest=target.text_digest,
                control_digest=source.text_digest,
                byte_length=target.byte_length,
            )
        )
    return V2ActiveControlPlan(tuple(bindings), control_seed_commitment)


def _target_faulty_counts(case_count: int, seed: bytes) -> tuple[int, ...]:
    lower_numerator = V2_FAULTY_PREVALENCE_LOWER.numerator * case_count
    lower = (lower_numerator + V2_FAULTY_PREVALENCE_LOWER.denominator - 1) // (
        V2_FAULTY_PREVALENCE_LOWER.denominator
    )
    upper = (
        V2_FAULTY_PREVALENCE_UPPER.numerator * case_count // V2_FAULTY_PREVALENCE_UPPER.denominator
    )
    low_mid = case_count // 2
    high_mid = case_count - low_mid
    amplitude = min(low_mid - lower, upper - high_mid)
    if amplitude < 1:
        raise ValueError("case count cannot support variable 35-65% batch prevalence")

    profile = []
    for index in range(V2_PAIR_COUNT // 2):
        deviation = 1 + index % amplitude
        profile.extend((low_mid - deviation, high_mid + deviation))
    pair_order = sorted(
        range(V2_PAIR_COUNT), key=lambda pair_id: _rank(seed, "prevalence-pair", pair_id)
    )
    result = [0] * V2_PAIR_COUNT
    for pair_id, target in zip(pair_order, profile, strict=True):
        result[pair_id] = target
    if sum(result) != case_count * (V2_PAIR_COUNT // 2):
        raise AssertionError("faulty prevalence profile lost per-case balance")
    if min(result) < lower or max(result) > upper or len(set(result)) < 2:
        raise AssertionError("faulty prevalence profile violates v2 bounds")
    return tuple(result)


def _fault_matrix(case_count: int, seed: bytes) -> tuple[tuple[bool, ...], ...]:
    """Realize equal row degrees and a variable, precommitted column-degree profile."""

    targets = _target_faulty_counts(case_count, seed)
    remaining = [V2_PAIR_COUNT // 2] * case_count
    faulty_pairs: list[set[int]] = [set() for _ in range(case_count)]
    column_order = sorted(
        range(V2_PAIR_COUNT),
        key=lambda pair_id: (-targets[pair_id], _rank(seed, "matrix-column", pair_id)),
    )
    # This is the bipartite Havel-Hakimi construction.  At each step, the next
    # column connects to the rows with greatest residual degree; seeded hashes
    # break only mathematically interchangeable ties.
    for pair_id in column_order:
        rows = sorted(
            range(case_count),
            key=lambda case_index: (
                -remaining[case_index],
                _rank(seed, "matrix-row", pair_id, case_index),
            ),
        )
        selected = [index for index in rows if remaining[index] > 0][: targets[pair_id]]
        if len(selected) != targets[pair_id]:
            raise AssertionError("prevalence degree sequence was not realizable")
        for case_index in selected:
            remaining[case_index] -= 1
            faulty_pairs[case_index].add(pair_id)
    if any(remaining):
        raise AssertionError("assignment construction did not give every case ten faults")
    matrix = tuple(
        tuple(pair_id in faulty_pairs[case_index] for pair_id in range(V2_PAIR_COUNT))
        for case_index in range(case_count)
    )
    if any(sum(row) != V2_PAIR_COUNT // 2 for row in matrix):
        raise AssertionError("assignment row is not exactly balanced")
    if tuple(sum(row[pair_id] for row in matrix) for pair_id in range(V2_PAIR_COUNT)) != targets:
        raise AssertionError("assignment column prevalence differs from its commitment")
    return matrix


@dataclass(frozen=True, slots=True, init=False)
class V2Assignment:
    case_id: str
    design_commitment_digest: str
    solver_case_id: str
    pair_id: int
    condition: Condition
    variant: Variant
    order_index: int
    view: V2RenderedView
    view_digest: str
    _state_seal: bytes = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        case_id: str,
        design_commitment_digest: str,
        solver_case_id: str,
        pair_id: int,
        condition: Condition,
        variant: Variant,
        order_index: int,
        view: V2RenderedView,
        view_digest: str,
    ) -> None:
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "design_commitment_digest", design_commitment_digest)
        object.__setattr__(self, "solver_case_id", solver_case_id)
        object.__setattr__(self, "pair_id", pair_id)
        object.__setattr__(self, "condition", condition)
        object.__setattr__(self, "variant", variant)
        object.__setattr__(self, "order_index", order_index)
        object.__setattr__(self, "view", view)
        object.__setattr__(self, "view_digest", view_digest)
        self._validate_fields()
        object.__setattr__(self, "_state_seal", _assignment_state_seal(self))
        self.__post_init__()

    def _validate_fields(self) -> None:
        _text(self.case_id, "assignment case id", max_bytes=128)
        _digest(self.design_commitment_digest, "assignment design commitment")
        _digest(self.solver_case_id, "opaque solver case id")
        pair_id = _integer(self.pair_id, "v2 pair id")
        if pair_id >= V2_PAIR_COUNT:
            raise ValueError("v2 pair id must be in [0, 19]")
        if type(self.condition) is not Condition or type(self.variant) is not Variant:
            raise ValueError("v2 assignment condition and variant must use declared enums")
        _integer(self.order_index, "v2 order index")
        if type(self.view) is not V2RenderedView:
            raise ValueError("v2 assignment must carry an exact bounded rendered view")
        _digest(self.view_digest, "assignment view digest")
        if not hmac.compare_digest(self.view_digest, self.view.digest):
            raise ValueError("assignment view digest differs from its canonical rendered bytes")

    def __post_init__(self) -> None:
        self._validate_fields()
        try:
            state_seal = object.__getattribute__(self, "_state_seal")
        except AttributeError as exc:
            raise ValueError("assignment state is not sealed") from exc
        expected = _assignment_state_seal(self)
        if type(state_seal) is not bytes or not hmac.compare_digest(state_seal, expected):
            raise ValueError("assignment state changed after binding")

    @property
    def matched_input_digest(self) -> str:
        """Condition-free identity used to prove exact matched inputs."""

        return _digest_json(
            {
                "design_commitment_digest": self.design_commitment_digest,
                "order_index": self.order_index,
                "pair_id": self.pair_id,
                "solver_case_id": self.solver_case_id,
                "view_digest": self.view_digest,
            }
        )

    @property
    def digest(self) -> str:
        return _digest_json(
            {
                "condition": self.condition.value,
                "matched_input_digest": self.matched_input_digest,
            }
        )


def _assignment_state_seal(assignment: V2Assignment) -> bytes:
    payload = canonical_json(
        {
            "case_id": assignment.case_id,
            "condition": assignment.condition.value,
            "design_commitment_digest": assignment.design_commitment_digest,
            "order_index": assignment.order_index,
            "pair_id": assignment.pair_id,
            "solver_case_id": assignment.solver_case_id,
            "variant": assignment.variant.value,
            "view_digest": assignment.view_digest,
            "view_record_digest": assignment.view.digest,
        }
    ).encode("utf-8")
    return hmac.new(
        _ASSIGNMENT_STATE_KEY,
        b"math-brain/evaluation-v2/assignment-state\x00"
        + id(assignment).to_bytes(16, "big")
        + payload,
        hashlib.sha256,
    ).digest()


@dataclass(frozen=True, slots=True)
class V2SolverCase:
    solver_case_id: str
    view: V2RenderedView
    view_digest: str
    order_index: int
    brain_context: str

    def __post_init__(self) -> None:
        _digest(self.solver_case_id, "solver case id")
        if type(self.view) is not V2RenderedView:
            raise ValueError("solver case must carry an exact bounded rendered view")
        _digest(self.view_digest, "solver view digest")
        if not hmac.compare_digest(self.view_digest, self.view.digest):
            raise ValueError("solver view digest differs from its canonical rendered bytes")
        _integer(self.order_index, "solver order index")
        _text(
            self.brain_context,
            "solver brain context",
            max_bytes=_MAX_TEXT_BYTES,
            allow_empty=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "brain_context": self.brain_context,
            "case_id": self.solver_case_id,
            "order_index": self.order_index,
            "solver_view": self.view.to_dict(),
            "view_digest": self.view_digest,
        }


@dataclass(frozen=True, slots=True)
class V2SolverBatch:
    pair_id: int
    condition: Condition
    cases: tuple[V2SolverCase, ...]
    matched_cases_digest: str
    treatment_digest: str

    def __post_init__(self) -> None:
        pair_id = _integer(self.pair_id, "solver batch pair id")
        if pair_id >= V2_PAIR_COUNT:
            raise ValueError("v2 pair id must be in [0, 19]")
        if type(self.condition) is not Condition:
            raise ValueError("solver batch condition must use the declared enum")
        if type(self.cases) is not tuple or any(
            type(case) is not V2SolverCase for case in self.cases
        ):
            raise ValueError("solver batch cases must be exact records in a tuple")
        if not self.cases:
            raise ValueError("solver batch cannot be empty")
        if tuple(case.order_index for case in self.cases) != tuple(range(len(self.cases))):
            raise ValueError("solver cases must be in their exact consecutive sealed order")
        if len({case.solver_case_id for case in self.cases}) != len(self.cases):
            raise ValueError("solver batch case ids must be unique")
        _digest(self.matched_cases_digest, "matched cases digest")
        _digest(self.treatment_digest, "treatment digest")
        expected = _digest_json(
            [
                {
                    "case_id": case.solver_case_id,
                    "order_index": case.order_index,
                    "view_digest": case.view_digest,
                }
                for case in self.cases
            ]
        )
        if not hmac.compare_digest(self.matched_cases_digest, expected):
            raise ValueError("matched cases digest does not bind the ordered solver inputs")

    def to_solver_payload(self) -> dict[str, Any]:
        """Return the condition-blind projection.  No truth/control provenance is serialized."""

        _revalidate_solver_batch(self)
        return {
            "batch_digest": self.treatment_digest,
            "cases": [case.to_dict() for case in self.cases],
            "matched_cases_digest": self.matched_cases_digest,
            "schema": V2_SOLVER_BATCH_SCHEMA,
            "schema_version": V2_SOLVER_BATCH_SCHEMA_VERSION,
        }


@dataclass(frozen=True, slots=True, weakref_slot=True)
class V2EvaluationPlan:
    cases: tuple[V2CaseSpec, ...]
    contexts: tuple[V2ContextCard, ...]
    commitment: V2DesignCommitment
    active_control: V2ActiveControlPlan
    gate: V2GateConfig
    assignments: tuple[V2Assignment, ...]
    assignment_matrix_digest: str

    def __post_init__(self) -> None:
        if type(self.cases) is not tuple or type(self.contexts) is not tuple:
            raise ValueError("plan cases and contexts must be exact canonical tuples")
        if type(self.commitment) is not V2DesignCommitment:
            raise ValueError("plan commitment must be an exact V2DesignCommitment")
        _revalidate_commitment(self.commitment)
        if type(self.active_control) is not V2ActiveControlPlan:
            raise ValueError("plan active control must be an exact V2ActiveControlPlan")
        _revalidate_active_control(self.active_control)
        cases = _normalized_cases(self.cases)
        contexts = _normalized_contexts(self.contexts)
        if cases != self.cases or contexts != self.contexts:
            raise ValueError("plan cases and contexts must use canonical case-id order")
        if _design_universe_digest(cases) != self.commitment.design_universe_digest:
            raise ValueError("plan cases differ from their precommitment")
        if _context_universe_digest(contexts) != self.commitment.context_universe_digest:
            raise ValueError("plan contexts differ from their precommitment")
        if type(self.gate) is not V2GateConfig:
            raise ValueError("plan gates differ from their precommitment")
        _revalidate_gate(self.gate)
        if not hmac.compare_digest(self.gate.digest, self.commitment.gate_config_digest):
            raise ValueError("plan gates differ from their precommitment")
        if len(cases) > self.gate.maximum_case_count:
            raise ValueError("plan case universe exceeds its sealed maximum")
        if {case.case_id for case in cases} != {card.case_id for card in contexts}:
            raise ValueError("plan case and context universes differ")
        _validate_case_context_bindings(cases, contexts)
        _digest(self.assignment_matrix_digest, "assignment matrix digest")
        if not hmac.compare_digest(
            self.active_control.control_seed_commitment,
            self.commitment.control_seed_commitment,
        ):
            raise ValueError("active-control seed commitment differs from the design")
        if not hmac.compare_digest(
            self.active_control.digest,
            self.commitment.active_control_payload_digest,
        ):
            raise ValueError("active-control payload differs from its precommitment")
        expected_count = len(cases) * V2_PAIR_COUNT * len(Condition)
        if type(self.assignments) is not tuple or any(
            type(assignment) is not V2Assignment for assignment in self.assignments
        ):
            raise ValueError("plan assignments must be exact records in a tuple")
        if len(self.assignments) != expected_count:
            raise ValueError("plan must contain both conditions for every pair/case cell")
        for assignment in self.assignments:
            _revalidate_assignment(assignment)
            if not hmac.compare_digest(
                assignment.design_commitment_digest,
                self.commitment.digest,
            ):
                raise ValueError("plan assignment differs from its design commitment")
        canonical_assignments = tuple(
            sorted(
                self.assignments,
                key=lambda item: (item.pair_id, item.condition.value, item.order_index),
            )
        )
        if canonical_assignments != self.assignments:
            raise ValueError("plan assignments must use canonical pair/condition/order order")
        if len({assignment.digest for assignment in self.assignments}) != expected_count:
            raise ValueError("plan assignment digests must be unique")

        case_by_id = {case.case_id: case for case in cases}
        by_cell: dict[tuple[int, str], dict[Condition, V2Assignment]] = defaultdict(dict)
        for assignment in self.assignments:
            case = case_by_id.get(assignment.case_id)
            if case is None:
                raise ValueError("plan assignment refers to a case outside the sealed universe")
            expected_view = (
                case.faulty_view if assignment.variant is Variant.FAULTY else case.clean_view
            )
            if assignment.view != expected_view or not hmac.compare_digest(
                assignment.view_digest, expected_view.digest
            ):
                raise ValueError("plan assignment view differs from the committed case variant")
            key = (assignment.pair_id, assignment.case_id)
            if assignment.condition in by_cell[key]:
                raise ValueError("plan duplicates a pair/case/condition assignment")
            by_cell[key][assignment.condition] = assignment
        expected_cells = {
            (pair_id, case.case_id) for pair_id in range(V2_PAIR_COUNT) for case in cases
        }
        if set(by_cell) != expected_cells:
            raise ValueError("plan assignment universe is incomplete")
        for key, conditions in by_cell.items():
            if set(conditions) != set(Condition):
                raise ValueError(f"plan cell {key} lacks a matched condition")
            on = conditions[Condition.BRAIN_ON]
            off = conditions[Condition.BRAIN_OFF]
            if (
                on.variant is not off.variant
                or on.order_index != off.order_index
                or on.solver_case_id != off.solver_case_id
                or on.view != off.view
                or on.view_digest != off.view_digest
                or on.matched_input_digest != off.matched_input_digest
            ):
                raise ValueError(f"matched conditions differ on sealed inputs: {key}")

        assignment_records = tuple(
            {
                "case_id": assignment.case_id,
                "order_index": assignment.order_index,
                "pair_id": assignment.pair_id,
                "solver_case_id": assignment.solver_case_id,
                "variant": assignment.variant.value,
                "view_digest": assignment.view_digest,
            }
            for assignment in sorted(
                (conditions[Condition.BRAIN_ON] for conditions in by_cell.values()),
                key=lambda item: (item.pair_id, item.order_index, item.case_id),
            )
        )
        assignment_payload_digest = _digest_json(assignment_records)
        if not hmac.compare_digest(
            self.assignment_matrix_digest, assignment_payload_digest
        ) or not hmac.compare_digest(
            assignment_payload_digest, self.commitment.assignment_payload_digest
        ):
            raise ValueError("assignment payload differs from its precommitment")

        for case in cases:
            variants = {
                by_cell[(pair_id, case.case_id)][Condition.BRAIN_ON].variant
                for pair_id in range(V2_PAIR_COUNT)
            }
            faulty_count = sum(
                by_cell[(pair_id, case.case_id)][Condition.BRAIN_ON].variant is Variant.FAULTY
                for pair_id in range(V2_PAIR_COUNT)
            )
            if variants != set(Variant) or faulty_count != V2_PAIR_COUNT // 2:
                raise ValueError("every case must be clean ten times and faulty ten times")
        faulty_counts = self.faulty_counts_by_pair
        lower_numerator = V2_FAULTY_PREVALENCE_LOWER.numerator * len(cases)
        lower = (lower_numerator + V2_FAULTY_PREVALENCE_LOWER.denominator - 1) // (
            V2_FAULTY_PREVALENCE_LOWER.denominator
        )
        upper = (
            V2_FAULTY_PREVALENCE_UPPER.numerator
            * len(cases)
            // V2_FAULTY_PREVALENCE_UPPER.denominator
        )
        if min(faulty_counts) < lower or max(faulty_counts) > upper:
            raise ValueError("pair prevalence is outside the sealed 35-65% interval")
        if len(set(faulty_counts)) < 2:
            raise ValueError("v2 requires variable rather than fixed 50% batch prevalence")
        if {binding.target_case_id for binding in self.active_control.bindings} != {
            case.case_id for case in cases
        }:
            raise ValueError("active-control universe differs from plan cases")
        context_by_id = {card.case_id: card for card in contexts}
        for binding in self.active_control.bindings:
            target = context_by_id[binding.target_case_id]
            source = context_by_id.get(binding.source_case_id)
            if source is None:
                raise ValueError("active-control source is outside the context universe")
            if not hmac.compare_digest(binding.relevant_digest, target.text_digest):
                raise ValueError("active-control relevant digest differs from its target")
            if not hmac.compare_digest(binding.control_digest, source.text_digest):
                raise ValueError("active-control digest differs from its source")
            if hmac.compare_digest(binding.relevant_digest, binding.control_digest):
                raise ValueError("active control text must differ from its relevant context")
            if (
                binding.byte_length != target.byte_length
                or source.byte_length != target.byte_length
            ):
                raise ValueError("active control must preserve exact UTF-8 byte length")
            if not set(target.relevance_keys).isdisjoint(source.relevance_keys):
                raise ValueError("active control must be relevance-disjoint")
        _register_plan_lookup_anchor(self)

    @property
    def faulty_counts_by_pair(self) -> tuple[int, ...]:
        return tuple(
            sum(
                assignment.condition is Condition.BRAIN_ON
                and assignment.pair_id == pair_id
                and assignment.variant is Variant.FAULTY
                for assignment in self.assignments
            )
            for pair_id in range(V2_PAIR_COUNT)
        )

    @property
    def digest(self) -> str:
        return _digest_json(
            {
                "active_control_digest": self.active_control.digest,
                "assignment_matrix_digest": self.assignment_matrix_digest,
                "commitment_digest": self.commitment.digest,
                "gate_config_digest": self.gate.digest,
                "pair_count": V2_PAIR_COUNT,
            }
        )

    def _assignments_for_validated(
        self, pair_id: int, condition: Condition
    ) -> tuple[V2Assignment, ...]:
        pair = _integer(pair_id, "v2 pair id")
        if pair >= V2_PAIR_COUNT:
            raise ValueError("v2 pair id must be in [0, 19]")
        if type(condition) is not Condition:
            raise ValueError("v2 condition must use the declared enum")
        result = tuple(
            sorted(
                (
                    assignment
                    for assignment in self.assignments
                    if assignment.pair_id == pair and assignment.condition is condition
                ),
                key=lambda assignment: assignment.order_index,
            )
        )
        if len(result) != len(self.cases):
            raise AssertionError("sealed v2 pair is incomplete")
        return result

    def assignments_for(self, pair_id: int, condition: Condition) -> tuple[V2Assignment, ...]:
        anchor = _validate_plan_lookup_anchor(self)
        if type(self.assignments) is not tuple or any(
            type(assignment) is not V2Assignment for assignment in self.assignments
        ):
            raise ValueError("plan assignments must be exact records in a tuple")
        if type(self.cases) is not tuple or any(
            type(case) is not V2CaseSpec for case in self.cases
        ):
            raise ValueError("plan cases must be exact records in a tuple")
        for assignment in self.assignments:
            if (
                type(assignment.case_id) is not str
                or type(assignment.pair_id) is not int
                or type(assignment.condition) is not Condition
            ):
                raise ValueError("plan assignment lookup fields must use exact scalar types")
        result = self._assignments_for_validated(pair_id, condition)
        cases: dict[str, V2CaseSpec] = {}
        for case_index, case in enumerate(self.cases):
            _validate_lookup_case_record(anchor, case_index, case)
            if type(case.case_id) is not str:
                raise ValueError("plan case ids must be exact strings")
            if case.case_id in cases:
                raise ValueError("plan case ids must remain unique")
            cases[case.case_id] = case
        if (
            tuple(assignment.order_index for assignment in result) != tuple(range(len(self.cases)))
            or len({assignment.case_id for assignment in result}) != len(self.cases)
            or {assignment.case_id for assignment in result} != set(cases)
            or len({assignment.solver_case_id for assignment in result}) != len(self.cases)
        ):
            raise ValueError("plan assignment lookup must preserve the exact ordered case universe")
        for assignment in result:
            _revalidate_assignment(assignment)
            case = cases.get(assignment.case_id)
            if case is None:
                raise ValueError("plan assignment refers outside the case universe")
            expected_view = (
                case.faulty_view if assignment.variant is Variant.FAULTY else case.clean_view
            )
            if assignment.view != expected_view:
                raise ValueError("plan assignment view differs from the committed case variant")
        return result

    def variant_for(self, case_id: str, pair_id: int) -> Variant:
        anchor = _validate_plan_lookup_anchor(self)
        _text(case_id, "v2 case id", max_bytes=128)
        pair = _integer(pair_id, "v2 pair id")
        if pair >= V2_PAIR_COUNT:
            raise ValueError("v2 pair id must be in [0, 19]")
        if type(self.assignments) is not tuple or any(
            type(assignment) is not V2Assignment for assignment in self.assignments
        ):
            raise ValueError("plan assignments must be exact records in a tuple")
        if type(self.cases) is not tuple or any(
            type(case) is not V2CaseSpec for case in self.cases
        ):
            raise ValueError("plan cases must be exact records in a tuple")
        case_matches: list[tuple[int, V2CaseSpec]] = []
        for case_index, case in enumerate(self.cases):
            if type(case.case_id) is not str:
                raise ValueError("plan case ids must be exact strings")
            if case.case_id == case_id:
                case_matches.append((case_index, case))
        if len(case_matches) != 1:
            raise ValueError("case id is not in the sealed v2 universe")
        case_index, case = case_matches[0]
        _validate_lookup_case_record(anchor, case_index, case)

        matches = []
        for assignment in self.assignments:
            if (
                type(assignment.case_id) is not str
                or type(assignment.pair_id) is not int
                or type(assignment.condition) is not Condition
            ):
                raise ValueError("plan assignment lookup fields must use exact scalar types")
            if assignment.case_id == case_id and assignment.pair_id == pair:
                matches.append(assignment)
        if len(matches) != len(Condition) or {item.condition for item in matches} != set(Condition):
            raise ValueError("sealed v2 pair/case cell is incomplete")
        for assignment in matches:
            _revalidate_assignment(assignment)
            expected_view = (
                case.faulty_view if assignment.variant is Variant.FAULTY else case.clean_view
            )
            if assignment.view != expected_view:
                raise ValueError("plan assignment view differs from the committed case variant")
        on = next(item for item in matches if item.condition is Condition.BRAIN_ON)
        off = next(item for item in matches if item.condition is Condition.BRAIN_OFF)
        if (
            on.variant is not off.variant
            or on.order_index != off.order_index
            or on.solver_case_id != off.solver_case_id
            or on.view_digest != off.view_digest
            or on.matched_input_digest != off.matched_input_digest
        ):
            raise ValueError("matched conditions differ on sealed inputs")
        return on.variant

    def solver_batch(self, pair_id: int, condition: Condition) -> V2SolverBatch:
        _revalidate_plan(self)
        assignments = self._assignments_for_validated(pair_id, condition)
        context_by_id = {card.case_id: card for card in self.contexts}
        control_by_target = {
            binding.target_case_id: binding.source_case_id
            for binding in self.active_control.bindings
        }
        solver_cases = []
        for assignment in assignments:
            context_id = (
                assignment.case_id
                if condition is Condition.BRAIN_ON
                else control_by_target[assignment.case_id]
            )
            solver_cases.append(
                V2SolverCase(
                    solver_case_id=assignment.solver_case_id,
                    view=assignment.view,
                    view_digest=assignment.view_digest,
                    order_index=assignment.order_index,
                    brain_context=context_by_id[context_id].text,
                )
            )
        matched_digest = _digest_json(
            [
                {
                    "case_id": case.solver_case_id,
                    "order_index": case.order_index,
                    "view_digest": case.view_digest,
                }
                for case in solver_cases
            ]
        )
        treatment_digest = _digest_json(
            {
                "contexts": [
                    _digest_bytes(case.brain_context.encode("utf-8")) for case in solver_cases
                ],
                "matched_cases_digest": matched_digest,
                "plan_digest": self.digest,
            }
        )
        return V2SolverBatch(
            pair_id=pair_id,
            condition=condition,
            cases=tuple(solver_cases),
            matched_cases_digest=matched_digest,
            treatment_digest=treatment_digest,
        )


@dataclass(frozen=True, slots=True)
class _V2PlanLookupAnchor:
    # INVARIANT: a public lookup may only read the exact outer container objects that
    # survived this plan's full precommitment validation.  The returned/queried nested
    # records are authenticated below; other cells are fully revalidated only at
    # dispatch, verification, and scoring boundaries.  Per-assignment seals alone
    # cannot distinguish a valid cell transplanted from another sealed plan.
    plan_ref: weakref.ReferenceType[V2EvaluationPlan] = field(repr=False, compare=False)
    assignments: tuple[V2Assignment, ...] = field(repr=False)
    cases: tuple[V2CaseSpec, ...] = field(repr=False)
    commitment: V2DesignCommitment = field(repr=False)
    commitment_digest: str
    assignment_matrix_digest: str
    case_record_digests: tuple[str, ...] = field(repr=False)
    seal: bytes = field(repr=False)


def _plan_lookup_anchor_seal(
    *,
    plan_identity: int,
    assignments: tuple[V2Assignment, ...],
    cases: tuple[V2CaseSpec, ...],
    commitment: V2DesignCommitment,
    commitment_digest: str,
    assignment_matrix_digest: str,
    case_record_digests: tuple[str, ...],
) -> bytes:
    payload = (
        b"math-brain/evaluation-v2/plan-lookup-anchor\x00"
        + plan_identity.to_bytes(16, "big")
        + id(assignments).to_bytes(16, "big")
        + id(cases).to_bytes(16, "big")
        + id(commitment).to_bytes(16, "big")
        + commitment_digest.encode("ascii")
        + assignment_matrix_digest.encode("ascii")
        + len(case_record_digests).to_bytes(4, "big")
        + b"".join(digest.encode("ascii") for digest in case_record_digests)
    )
    return hmac.new(_PLAN_LOOKUP_ANCHOR_KEY, payload, hashlib.sha256).digest()


def _register_plan_lookup_anchor(plan: V2EvaluationPlan) -> None:
    plan_identity = id(plan)
    with _PLAN_LOOKUP_ANCHORS_LOCK:
        existing = _PLAN_LOOKUP_ANCHORS.get(plan_identity)
        if existing is not None and existing.plan_ref() is plan:
            _validate_plan_lookup_anchor(plan)
            return

        commitment_digest = plan.commitment.digest
        assignment_matrix_digest = _digest(
            plan.assignment_matrix_digest,
            "plan lookup assignment matrix digest",
        )
        case_record_digests = tuple(_lookup_case_record_digest(case) for case in plan.cases)

        def release(reference: weakref.ReferenceType[V2EvaluationPlan]) -> None:
            with _PLAN_LOOKUP_ANCHORS_LOCK:
                current = _PLAN_LOOKUP_ANCHORS.get(plan_identity)
                if current is not None and current.plan_ref is reference:
                    _PLAN_LOOKUP_ANCHORS.pop(plan_identity, None)

        reference = weakref.ref(plan, release)
        _PLAN_LOOKUP_ANCHORS[plan_identity] = _V2PlanLookupAnchor(
            plan_ref=reference,
            assignments=plan.assignments,
            cases=plan.cases,
            commitment=plan.commitment,
            commitment_digest=commitment_digest,
            assignment_matrix_digest=assignment_matrix_digest,
            case_record_digests=case_record_digests,
            seal=_plan_lookup_anchor_seal(
                plan_identity=plan_identity,
                assignments=plan.assignments,
                cases=plan.cases,
                commitment=plan.commitment,
                commitment_digest=commitment_digest,
                assignment_matrix_digest=assignment_matrix_digest,
                case_record_digests=case_record_digests,
            ),
        )


def _validate_plan_lookup_anchor(plan: V2EvaluationPlan) -> _V2PlanLookupAnchor:
    if type(plan) is not V2EvaluationPlan:
        raise ValueError("plan lookup requires an exact V2EvaluationPlan")
    if (
        type(plan.assignments) is not tuple
        or type(plan.cases) is not tuple
        or type(plan.commitment) is not V2DesignCommitment
    ):
        raise ValueError("plan lookup containers must retain their exact sealed types")
    _revalidate_commitment(plan.commitment)
    _digest(plan.assignment_matrix_digest, "plan lookup assignment matrix digest")
    with _PLAN_LOOKUP_ANCHORS_LOCK:
        anchor = _PLAN_LOOKUP_ANCHORS.get(id(plan))
        if anchor is None or anchor.plan_ref() is not plan:
            raise ValueError("plan lookup anchor is missing")
        expected_seal = _plan_lookup_anchor_seal(
            plan_identity=id(plan),
            assignments=anchor.assignments,
            cases=anchor.cases,
            commitment=anchor.commitment,
            commitment_digest=anchor.commitment_digest,
            assignment_matrix_digest=anchor.assignment_matrix_digest,
            case_record_digests=anchor.case_record_digests,
        )
        if type(anchor.seal) is not bytes or not hmac.compare_digest(
            anchor.seal,
            expected_seal,
        ):
            raise ValueError("plan lookup anchor authentication failed")
        if (
            plan.assignments is not anchor.assignments
            or plan.cases is not anchor.cases
            or plan.commitment is not anchor.commitment
            or not hmac.compare_digest(plan.commitment.digest, anchor.commitment_digest)
            or not hmac.compare_digest(
                plan.assignment_matrix_digest,
                anchor.assignment_matrix_digest,
            )
        ):
            raise ValueError("plan lookup containers differ from their sealed anchor")
        return anchor


def _lookup_case_record_digest(case: V2CaseSpec) -> str:
    _revalidate_case(case)
    return _digest_json(case.to_commitment_record())


def _validate_lookup_case_record(
    anchor: _V2PlanLookupAnchor,
    case_index: int,
    case: V2CaseSpec,
) -> None:
    if (
        type(case_index) is not int
        or not 0 <= case_index < len(anchor.cases)
        or len(anchor.case_record_digests) != len(anchor.cases)
        or case is not anchor.cases[case_index]
    ):
        raise ValueError("plan lookup case differs from its sealed position")
    current_digest = _lookup_case_record_digest(case)
    expected_digest = anchor.case_record_digests[case_index]
    if type(expected_digest) is not str or not hmac.compare_digest(
        current_digest,
        expected_digest,
    ):
        raise ValueError("plan lookup case record differs from its sealed anchor")


def build_v2_plan(
    cases: Sequence[V2CaseSpec],
    contexts: Sequence[V2ContextCard],
    *,
    assignment_seed: bytes,
    control_seed: bytes,
    commitment: V2DesignCommitment,
    gate: V2GateConfig | None = None,
) -> V2EvaluationPlan:
    """Reveal committed seeds and materialize the exact twenty-pair design."""

    if type(commitment) is not V2DesignCommitment:
        raise ValueError("v2 plan requires an exact design commitment")
    _revalidate_commitment(commitment)
    normalized_cases = _normalized_cases(cases)
    normalized_contexts = _normalized_contexts(contexts)
    assignment = _seed(assignment_seed, "assignment seed")
    control = _seed(control_seed, "control seed")
    gate_config = V2GateConfig() if gate is None else gate
    if type(gate_config) is not V2GateConfig:
        raise ValueError("v2 plan gate must be an exact V2GateConfig")
    _revalidate_gate(gate_config)
    expected_commitment = precommit_v2_design(
        normalized_cases,
        normalized_contexts,
        assignment_seed=assignment,
        control_seed=control,
        receipt_authority_digest=commitment.receipt_authority_digest,
        gate=gate_config,
    )
    if not hmac.compare_digest(commitment.digest, expected_commitment.digest):
        raise ValueError("revealed v2 design does not match the persisted precommitment")

    assignment_records = _assignment_payload_records(normalized_cases, assignment)
    case_by_id = {case.case_id: case for case in normalized_cases}
    assignments = []
    for record in assignment_records:
        pair_id = record["pair_id"]
        order_index = record["order_index"]
        case = case_by_id[record["case_id"]]
        variant = Variant(record["variant"])
        source_view = case.faulty_view if variant is Variant.FAULTY else case.clean_view
        for condition in Condition:
            assignments.append(
                V2Assignment(
                    case_id=case.case_id,
                    design_commitment_digest=commitment.digest,
                    solver_case_id=record["solver_case_id"],
                    pair_id=pair_id,
                    condition=condition,
                    variant=variant,
                    order_index=order_index,
                    view=source_view,
                    view_digest=source_view.digest,
                )
            )
    assignments.sort(key=lambda item: (item.pair_id, item.condition.value, item.order_index))
    return V2EvaluationPlan(
        cases=normalized_cases,
        contexts=normalized_contexts,
        commitment=commitment,
        active_control=_build_active_control(
            normalized_contexts, control, commitment.control_seed_commitment
        ),
        gate=gate_config,
        assignments=tuple(assignments),
        assignment_matrix_digest=_digest_json(assignment_records),
    )


def assignment_variant_v2(plan: V2EvaluationPlan, case_id: str, pair_id: int) -> Variant:
    """Generalized v2 variant API; unlike v1, pair ids 0 through 19 are valid."""

    if type(plan) is not V2EvaluationPlan:
        raise ValueError("v2 assignment lookup requires an exact sealed plan")
    return plan.variant_for(case_id, pair_id)


class V2VerifierKind(StrEnum):
    REPAIR = "repair"
    INDEPENDENT_WITNESS = "independent_witness"


def _verifier_replay_origin_payload(
    replay: Callable[[Any], tuple[bool, str]],
) -> bytes:
    if type(replay) is types.FunctionType:
        function = replay
        bound_self: Any | None = None
    elif type(replay) is types.MethodType and type(replay.__func__) is types.FunctionType:
        function = replay.__func__
        bound_self = replay.__self__
    else:
        raise ValueError("bound verifier replay must be an exact Python function or bound method")

    bound_digest = ""
    if bound_self is not None:
        try:
            candidate = object.__getattribute__(bound_self, "digest")
        except AttributeError:
            candidate = None
        if type(candidate) is types.MethodType and candidate.__self__ is bound_self:
            candidate = candidate()
        if candidate is None:
            raise ValueError("bound verifier method state must expose a canonical digest")
        bound_digest = _digest(candidate, "bound verifier state digest")
    return (
        id(function).to_bytes(16, "big")
        + id(function.__code__).to_bytes(16, "big")
        + id(function.__globals__).to_bytes(16, "big")
        + (0 if bound_self is None else id(bound_self)).to_bytes(16, "big")
        + bound_digest.encode("ascii")
    )


def _verifier_replay_identity_payload(
    replay: Callable[[Any], tuple[bool, str]],
) -> bytes:
    return id(replay).to_bytes(16, "big") + _verifier_replay_origin_payload(replay)


def _same_verifier_replay_origin(
    left: Callable[[Any], tuple[bool, str]],
    right: Callable[[Any], tuple[bool, str]],
) -> bool:
    if type(left) is types.FunctionType and type(right) is types.FunctionType:
        return left is right
    if type(left) is types.MethodType and type(right) is types.MethodType:
        return left.__func__ is right.__func__ and left.__self__ is right.__self__
    return False


def _verifier_replay_capability_seal(
    verifier_identity: int,
    kind: V2VerifierKind,
    verifier_digest: str,
    replay: Callable[[Any], tuple[bool, str]],
) -> bytes:
    payload = (
        b"math-brain/evaluation-v2/verifier-replay-capability\x00"
        + verifier_identity.to_bytes(16, "big")
        + b"\x00"
        + kind.value.encode("ascii")
        + b"\x00"
        + verifier_digest.encode("ascii")
        + b"\x00"
        + _verifier_replay_identity_payload(replay)
    )
    return hmac.new(_VERIFIER_REPLAY_CAPABILITY_KEY, payload, hashlib.sha256).digest()


@dataclass(frozen=True, slots=True, init=False)
class V2BoundVerifier:
    """A precommitted verifier identity paired with its evaluator-only replay callback."""

    kind: V2VerifierKind
    verifier_digest: str
    replay: Callable[[Any], tuple[bool, str]] = field(repr=False, compare=False)
    _replay_capability_seal: bytes = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        kind: V2VerifierKind,
        verifier_digest: str,
        replay: Callable[[Any], tuple[bool, str]],
    ) -> None:
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "verifier_digest", verifier_digest)
        object.__setattr__(self, "replay", replay)
        if type(kind) is not V2VerifierKind:
            raise ValueError("bound verifier kind must use the declared enum")
        _digest(verifier_digest, "bound verifier digest")
        if not callable(replay):
            raise ValueError("bound verifier replay must be callable")
        _verifier_replay_identity_payload(replay)
        object.__setattr__(
            self,
            "_replay_capability_seal",
            _verifier_replay_capability_seal(
                id(self),
                kind,
                verifier_digest,
                replay,
            ),
        )
        self.__post_init__()

    def __post_init__(self) -> None:
        if type(self.kind) is not V2VerifierKind:
            raise ValueError("bound verifier kind must use the declared enum")
        _digest(self.verifier_digest, "bound verifier digest")
        if not callable(self.replay):
            raise ValueError("bound verifier replay must be callable")
        try:
            replay_seal = object.__getattribute__(self, "_replay_capability_seal")
        except AttributeError as exc:
            raise ValueError("bound verifier replay capability is not sealed") from exc
        expected = _verifier_replay_capability_seal(
            id(self),
            self.kind,
            self.verifier_digest,
            self.replay,
        )
        if type(replay_seal) is not bytes or not hmac.compare_digest(replay_seal, expected):
            raise ValueError("bound verifier replay capability changed after binding")

    def verify(self, candidate: Any) -> tuple[bool, str]:
        # Never run a callback merely because a once-frozen record still looks callable.
        self.__post_init__()
        result = self.replay(candidate)
        self.__post_init__()
        if type(result) is not tuple or len(result) != 2 or type(result[0]) is not bool:
            raise ValueError("evaluator verifier must return (bool, evidence_digest)")
        _digest(result[1], "evaluator verifier evidence digest")
        return result

    @property
    def replay_origin_digest(self) -> str:
        self.__post_init__()
        return (
            "sha256:"
            + hmac.new(
                _VERIFIER_REPLAY_CAPABILITY_KEY,
                b"math-brain/evaluation-v2/verifier-replay-origin\x00"
                + _verifier_replay_origin_payload(self.replay),
                hashlib.sha256,
            ).hexdigest()
        )


@dataclass(frozen=True, slots=True)
class V2HardCaseBinding:
    """Evaluator spec plus the hard case's two recomputing verifier adapters."""

    case: V2CaseSpec
    repair: V2BoundVerifier
    witness: V2BoundVerifier

    def __post_init__(self) -> None:
        if type(self.case) is not V2CaseSpec:
            raise ValueError("hard-case binding requires an exact V2CaseSpec")
        if type(self.repair) is not V2BoundVerifier or type(self.witness) is not V2BoundVerifier:
            raise ValueError("hard-case binding requires exact bound verifier records")
        if self.repair.kind is not V2VerifierKind.REPAIR:
            raise ValueError("hard-case repair binding has the wrong verifier kind")
        if self.witness.kind is not V2VerifierKind.INDEPENDENT_WITNESS:
            raise ValueError("hard-case witness binding has the wrong verifier kind")
        if not hmac.compare_digest(
            self.repair.verifier_digest, self.case.repair_verifier_digest
        ) or not hmac.compare_digest(
            self.witness.verifier_digest, self.case.witness_verifier_digest
        ):
            raise ValueError("hard-case verifier binding differs from case commitments")
        if _same_verifier_replay_origin(self.repair.replay, self.witness.replay):
            raise ValueError("repair and witness require distinct replay callbacks")

    @classmethod
    def from_hard_case(cls, hard_case: V2HardCaseAdapter) -> V2HardCaseBinding:
        case = V2CaseSpec.from_hard_case(hard_case)
        if not callable(getattr(hard_case, "verify_repair", None)) or not callable(
            getattr(hard_case, "verify_witness", None)
        ):
            raise ValueError("hard-case binding requires recomputing repair and witness methods")
        return cls(
            case=case,
            repair=V2BoundVerifier(
                V2VerifierKind.REPAIR,
                case.repair_verifier_digest,
                hard_case.verify_repair,
            ),
            witness=V2BoundVerifier(
                V2VerifierKind.INDEPENDENT_WITNESS,
                case.witness_verifier_digest,
                hard_case.verify_witness,
            ),
        )


@dataclass(frozen=True, slots=True)
class V2VerifierReceipt:
    kind: V2VerifierKind
    assignment_digest: str
    case_digest: str
    accepted: bool
    candidate_digest: str
    solver_answer_digest: str
    verifier_digest: str
    replay_origin_digest: str
    evidence_digest: str
    independent: bool
    authority_digest: str
    seal: str

    def __post_init__(self) -> None:
        if type(self.kind) is not V2VerifierKind:
            raise ValueError("v2 verifier receipt kind must use the declared enum")
        _digest(self.assignment_digest, "receipt assignment digest")
        _digest(self.case_digest, "receipt case digest")
        if type(self.accepted) is not bool or type(self.independent) is not bool:
            raise ValueError("receipt accepted and independent fields must be booleans")
        _digest(self.candidate_digest, "receipt candidate digest")
        _digest(self.solver_answer_digest, "receipt solver-answer digest")
        _digest(self.verifier_digest, "receipt verifier digest")
        _digest(self.replay_origin_digest, "receipt replay-origin digest")
        _digest(self.evidence_digest, "receipt evidence digest")
        _digest(self.authority_digest, "receipt authority digest")
        _digest(self.seal, "receipt seal")
        if self.kind is V2VerifierKind.REPAIR and self.independent:
            raise ValueError("repair replay is not the independent-witness receipt")
        if self.kind is V2VerifierKind.INDEPENDENT_WITNESS and not self.independent:
            raise ValueError("witness credit requires an explicitly independent verifier")

    def unsigned_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "assignment_digest": self.assignment_digest,
            "authority_digest": self.authority_digest,
            "case_digest": self.case_digest,
            "candidate_digest": self.candidate_digest,
            "solver_answer_digest": self.solver_answer_digest,
            "evidence_digest": self.evidence_digest,
            "independent": self.independent,
            "kind": self.kind.value,
            "replay_origin_digest": self.replay_origin_digest,
            "verifier_digest": self.verifier_digest,
        }


@dataclass(frozen=True, slots=True)
class V2ReceiptAuthority:
    """Evaluator-only MAC authority; solver output cannot manufacture verifier credit."""

    secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _seed(self.secret, "receipt authority secret")

    @property
    def digest(self) -> str:
        return _digest_bytes(b"math-brain/evaluation-v2/receipt-authority\x00" + self.secret)

    def _seal(self, unsigned: Mapping[str, Any]) -> str:
        payload = canonical_json(unsigned).encode("utf-8")
        return (
            "sha256:"
            + hmac.new(
                self.secret,
                b"math-brain/evaluation-v2/verifier-receipt\x00" + payload,
                hashlib.sha256,
            ).hexdigest()
        )

    def issue(
        self,
        assignment: V2Assignment,
        case: V2CaseSpec,
        *,
        kind: V2VerifierKind,
        candidate: Any,
        solver_answer_digest: str,
        verifier: V2BoundVerifier,
    ) -> V2VerifierReceipt:
        """Run the committed evaluator verifier and authenticate its replay result."""

        if type(assignment) is not V2Assignment:
            raise ValueError("receipt issuance requires an exact sealed assignment")
        _revalidate_authority(self)
        _revalidate_assignment(assignment)
        if assignment.variant is Variant.CLEAN:
            raise ValueError("verifier receipts are forbidden for clean assignments")
        if type(case) is not V2CaseSpec:
            raise ValueError("receipt issuance requires an exact truth case")
        _revalidate_case(case)
        if case.case_id != assignment.case_id:
            raise ValueError("receipt issuance truth differs from its sealed assignment")
        if type(kind) is not V2VerifierKind or type(verifier) is not V2BoundVerifier:
            raise ValueError("receipt issuance requires a declared kind and bound verifier")
        _revalidate_bound_verifier(verifier)
        candidate_json = _canonical_candidate_json(candidate, "verifier candidate")
        candidate_digest = _digest_bytes(candidate_json.encode("utf-8"))
        answer_digest = _digest(solver_answer_digest, "solver-answer digest")
        assignment_digest = assignment.digest
        assignment_state_digest = _digest_json(
            {
                "case_id": assignment.case_id,
                "condition": assignment.condition.value,
                "design_commitment_digest": assignment.design_commitment_digest,
                "order_index": assignment.order_index,
                "pair_id": assignment.pair_id,
                "solver_case_id": assignment.solver_case_id,
                "variant": assignment.variant.value,
                "view_digest": assignment.view_digest,
                "view_record_digest": assignment.view.digest,
            }
        )
        case_state_digest = _digest_json(case.to_commitment_record())
        authority_digest = self.digest
        verifier_digest = (
            case.repair_verifier_digest
            if kind is V2VerifierKind.REPAIR
            else case.witness_verifier_digest
        )
        if verifier.kind is not kind or not hmac.compare_digest(
            verifier.verifier_digest, verifier_digest
        ):
            raise ValueError("bound verifier differs from the case precommitment")
        accepted, evidence_digest = verifier.verify(candidate)
        # Replay is evaluator code, but it is still an execution boundary.  Refuse
        # credit if it mutates any object or candidate that the receipt is binding.
        _revalidate_authority(self)
        _revalidate_assignment(assignment)
        _revalidate_case(case)
        _revalidate_bound_verifier(verifier)
        if not hmac.compare_digest(
            candidate_json,
            _canonical_candidate_json(candidate, "verifier candidate after replay"),
        ):
            raise ValueError("bound verifier mutated its candidate during replay")
        current_verifier_digest = (
            case.repair_verifier_digest
            if kind is V2VerifierKind.REPAIR
            else case.witness_verifier_digest
        )
        if (
            not hmac.compare_digest(assignment.digest, assignment_digest)
            or not hmac.compare_digest(
                _digest_json(
                    {
                        "case_id": assignment.case_id,
                        "condition": assignment.condition.value,
                        "design_commitment_digest": assignment.design_commitment_digest,
                        "order_index": assignment.order_index,
                        "pair_id": assignment.pair_id,
                        "solver_case_id": assignment.solver_case_id,
                        "variant": assignment.variant.value,
                        "view_digest": assignment.view_digest,
                        "view_record_digest": assignment.view.digest,
                    }
                ),
                assignment_state_digest,
            )
            or not hmac.compare_digest(
                _digest_json(case.to_commitment_record()),
                case_state_digest,
            )
            or not hmac.compare_digest(self.digest, authority_digest)
            or not hmac.compare_digest(current_verifier_digest, verifier_digest)
            or not hmac.compare_digest(verifier.verifier_digest, verifier_digest)
        ):
            raise ValueError("bound verifier mutated sealed evaluator state during replay")
        independent = kind is V2VerifierKind.INDEPENDENT_WITNESS
        unsigned = {
            "accepted": accepted,
            "assignment_digest": assignment_digest,
            "authority_digest": authority_digest,
            "case_digest": case_state_digest,
            "candidate_digest": candidate_digest,
            "evidence_digest": evidence_digest,
            "independent": independent,
            "kind": kind.value,
            "replay_origin_digest": verifier.replay_origin_digest,
            "solver_answer_digest": answer_digest,
            "verifier_digest": verifier_digest,
        }
        return V2VerifierReceipt(
            kind=kind,
            assignment_digest=assignment_digest,
            case_digest=case_state_digest,
            accepted=accepted,
            candidate_digest=candidate_digest,
            solver_answer_digest=answer_digest,
            verifier_digest=verifier_digest,
            replay_origin_digest=verifier.replay_origin_digest,
            evidence_digest=evidence_digest,
            independent=independent,
            authority_digest=authority_digest,
            seal=self._seal(unsigned),
        )

    def verifies(self, receipt: V2VerifierReceipt) -> bool:
        try:
            _revalidate_authority(self)
            _revalidate_receipt(receipt)
        except ValueError:
            return False
        return bool(
            type(receipt) is V2VerifierReceipt
            and hmac.compare_digest(receipt.authority_digest, self.digest)
            and hmac.compare_digest(receipt.seal, self._seal(receipt.unsigned_dict()))
        )


def _canonical_candidate_json_with_nodes(value: Any, label: str) -> tuple[str, int]:
    """Validate one exact, bounded JSON value before hashing or verifier replay."""

    prefix = f"{label} must be strict canonical JSON data"
    nodes = 0

    def fail(reason: str) -> None:
        raise ValueError(f"{prefix}: {reason}")

    def visit(item: Any, depth: int) -> int:
        nonlocal nodes
        if depth > _MAX_CANDIDATE_JSON_DEPTH:
            fail(f"nesting exceeds {_MAX_CANDIDATE_JSON_DEPTH}")
        nodes += 1
        if nodes > _MAX_CANDIDATE_JSON_NODES:
            fail(f"node count exceeds {_MAX_CANDIDATE_JSON_NODES}")

        item_type = type(item)
        if item is None or item_type is bool:
            return len(canonical_json(item).encode("utf-8"))
        if item_type is int:
            if item.bit_length() > _MAX_CANDIDATE_INTEGER_BITS:
                fail(f"integer exceeds {_MAX_CANDIDATE_INTEGER_BITS} bits")
            return len(canonical_json(item).encode("utf-8"))
        if item_type is float:
            if not math.isfinite(item):
                fail("floating-point values must be finite")
            return len(canonical_json(item).encode("utf-8"))
        if item_type is str:
            try:
                _text(
                    item,
                    f"{label} string",
                    max_bytes=_MAX_TEXT_BYTES,
                    allow_empty=True,
                )
            except ValueError as exc:
                fail(str(exc))
            return len(canonical_json(item).encode("utf-8"))
        if item_type is list:
            if len(item) > _MAX_CANDIDATE_CONTAINER_ITEMS:
                fail(f"array exceeds {_MAX_CANDIDATE_CONTAINER_ITEMS} items")
            byte_count = 2 + max(0, len(item) - 1)
            for child in item:
                byte_count += visit(child, depth + 1)
                if byte_count > _MAX_CANDIDATE_JSON_BYTES:
                    fail(f"canonical encoding exceeds {_MAX_CANDIDATE_JSON_BYTES} bytes")
            return byte_count
        if item_type is dict:
            if len(item) > _MAX_CANDIDATE_CONTAINER_ITEMS:
                fail(f"object exceeds {_MAX_CANDIDATE_CONTAINER_ITEMS} items")
            byte_count = 2 + max(0, len(item) - 1)
            for key, child in item.items():
                if type(key) is not str:
                    fail("object keys must be exact strings")
                try:
                    _text(
                        key,
                        f"{label} object key",
                        max_bytes=_MAX_TEXT_BYTES,
                        allow_empty=True,
                    )
                except ValueError as exc:
                    fail(str(exc))
                byte_count += len(canonical_json(key).encode("utf-8")) + 1
                byte_count += visit(child, depth + 1)
                if byte_count > _MAX_CANDIDATE_JSON_BYTES:
                    fail(f"canonical encoding exceeds {_MAX_CANDIDATE_JSON_BYTES} bytes")
            return byte_count
        fail("only exact dict/list/null/bool/int/finite-float/string values are accepted")
        raise AssertionError("unreachable")

    measured_bytes = visit(value, 0)
    try:
        candidate_json = canonical_json(value)
        actual_bytes = len(candidate_json.encode("utf-8"))
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeEncodeError) as exc:
        raise ValueError(prefix) from exc
    if actual_bytes != measured_bytes:
        raise AssertionError("candidate JSON byte accounting differs from canonical encoding")
    if actual_bytes > _MAX_CANDIDATE_JSON_BYTES:
        fail(f"canonical encoding exceeds {_MAX_CANDIDATE_JSON_BYTES} bytes")
    return candidate_json, nodes


def _canonical_candidate_json(value: Any, label: str) -> str:
    return _canonical_candidate_json_with_nodes(value, label)[0]


def _candidate_digest_v2(candidate: Any | None) -> str | None:
    if candidate is None:
        return None
    return _digest_bytes(_canonical_candidate_json(candidate, "solver candidate").encode("utf-8"))


def solver_answer_digest_v2(
    *,
    verdict: Verdict,
    root_localization: str | int | None,
    error_family: str | None,
    repair_candidate: Any | None,
    witness_candidate: Any | None,
) -> str:
    if type(verdict) is not Verdict:
        raise ValueError("solver-answer digest requires the declared verdict enum")
    if root_localization is not None:
        _localization(root_localization, "solver-answer root localization")
    if error_family is not None:
        _text(error_family, "solver-answer error family", max_bytes=128)
    return _digest_json(
        {
            "error_family": error_family,
            "repair_candidate_digest": _candidate_digest_v2(repair_candidate),
            "root_localization": root_localization,
            "verdict": verdict.value,
            "witness_candidate_digest": _candidate_digest_v2(witness_candidate),
        }
    )


@dataclass(frozen=True, slots=True)
class V2Outcome:
    assignment_digest: str
    solver_case_id: str
    verdict: Verdict
    root_localization: str | int | None
    error_family: str | None
    repair_candidate: Any | None
    witness_candidate: Any | None
    solver_answer_digest: str
    repair_receipt: V2VerifierReceipt | None
    witness_receipt: V2VerifierReceipt | None

    def __post_init__(self) -> None:
        _digest(self.assignment_digest, "outcome assignment digest")
        _digest(self.solver_case_id, "outcome solver case id")
        if type(self.verdict) is not Verdict:
            raise ValueError("v2 outcome verdict must use the declared enum")
        _digest(self.solver_answer_digest, "outcome solver-answer digest")
        expected_answer_digest = solver_answer_digest_v2(
            verdict=self.verdict,
            root_localization=self.root_localization,
            error_family=self.error_family,
            repair_candidate=self.repair_candidate,
            witness_candidate=self.witness_candidate,
        )
        if not hmac.compare_digest(self.solver_answer_digest, expected_answer_digest):
            raise ValueError("outcome solver-answer digest differs from its exact parsed fields")
        if self.verdict is Verdict.VALID:
            if any(
                value is not None
                for value in (
                    self.root_localization,
                    self.error_family,
                    self.repair_candidate,
                    self.witness_candidate,
                    self.repair_receipt,
                    self.witness_receipt,
                )
            ):
                raise ValueError("an exact VALID outcome cannot carry fault claims or receipts")
        else:
            _localization(self.root_localization, "outcome root localization")
            _text(self.error_family, "outcome error family", max_bytes=128)
            if (
                self.repair_receipt is not None
                and type(self.repair_receipt) is not V2VerifierReceipt
            ):
                raise ValueError("repair receipt must be an exact V2VerifierReceipt")
            if (
                self.witness_receipt is not None
                and type(self.witness_receipt) is not V2VerifierReceipt
            ):
                raise ValueError("witness receipt must be an exact V2VerifierReceipt")
        for label, candidate, receipt in (
            ("repair", self.repair_candidate, self.repair_receipt),
            ("witness", self.witness_candidate, self.witness_receipt),
        ):
            if receipt is None:
                continue
            candidate_digest = _candidate_digest_v2(candidate)
            if candidate_digest is None:
                raise ValueError(f"{label} receipt requires its raw parsed candidate")
            if not hmac.compare_digest(receipt.candidate_digest, candidate_digest):
                raise ValueError(f"{label} receipt candidate differs from the parsed answer")
            if not hmac.compare_digest(receipt.solver_answer_digest, self.solver_answer_digest):
                raise ValueError(f"{label} receipt differs from the sealed solver answer")

    @classmethod
    def finalize(
        cls,
        assignment: V2Assignment,
        case: V2CaseSpec,
        *,
        verdict: Verdict,
        root_localization: str | int | None,
        error_family: str | None,
        repair_candidate: Any | None,
        witness_candidate: Any | None,
        receipt_authority: V2ReceiptAuthority,
        repair_verifier: V2BoundVerifier | None = None,
        witness_verifier: V2BoundVerifier | None = None,
    ) -> V2Outcome:
        """Bind exact parsed solver fields to independently replayed verifier receipts."""

        if type(assignment) is not V2Assignment or type(case) is not V2CaseSpec:
            raise ValueError("outcome finalizer requires exact assignment and case records")
        _revalidate_assignment(assignment)
        _revalidate_case(case)
        if assignment.case_id != case.case_id:
            raise ValueError("outcome finalizer case differs from its assignment")
        if type(receipt_authority) is not V2ReceiptAuthority:
            raise ValueError("outcome finalizer requires an evaluator receipt authority")
        _revalidate_authority(receipt_authority)
        if repair_verifier is not None:
            _revalidate_bound_verifier(repair_verifier)
        if witness_verifier is not None:
            _revalidate_bound_verifier(witness_verifier)
        if (
            repair_verifier is not None
            and witness_verifier is not None
            and _same_verifier_replay_origin(repair_verifier.replay, witness_verifier.replay)
        ):
            raise ValueError("repair and witness require distinct replay callbacks")
        answer_digest = solver_answer_digest_v2(
            verdict=verdict,
            root_localization=root_localization,
            error_family=error_family,
            repair_candidate=repair_candidate,
            witness_candidate=witness_candidate,
        )
        repair_receipt = None
        witness_receipt = None
        if assignment.variant is Variant.CLEAN and (
            repair_verifier is not None or witness_verifier is not None
        ):
            raise ValueError("verifier issuance is forbidden for clean assignments")
        if assignment.variant is Variant.FAULTY and verdict is Verdict.INVALID:
            if repair_verifier is not None:
                if repair_candidate is None:
                    raise ValueError("repair verifier requires the exact parsed candidate")
                repair_receipt = receipt_authority.issue(
                    assignment,
                    case,
                    kind=V2VerifierKind.REPAIR,
                    candidate=repair_candidate,
                    solver_answer_digest=answer_digest,
                    verifier=repair_verifier,
                )
            if witness_verifier is not None:
                if witness_candidate is None:
                    raise ValueError("witness verifier requires the exact parsed candidate")
                witness_receipt = receipt_authority.issue(
                    assignment,
                    case,
                    kind=V2VerifierKind.INDEPENDENT_WITNESS,
                    candidate=witness_candidate,
                    solver_answer_digest=answer_digest,
                    verifier=witness_verifier,
                )
        return cls(
            assignment_digest=assignment.digest,
            solver_case_id=assignment.solver_case_id,
            verdict=verdict,
            root_localization=root_localization,
            error_family=error_family,
            repair_candidate=repair_candidate,
            witness_candidate=witness_candidate,
            solver_answer_digest=answer_digest,
            repair_receipt=repair_receipt,
            witness_receipt=witness_receipt,
        )


def _outcome_candidate_metrics_v2(outcome: V2Outcome) -> tuple[int, int]:
    byte_count = 0
    node_count = 0
    for label, candidate in (
        ("repair", outcome.repair_candidate),
        ("witness", outcome.witness_candidate),
    ):
        if candidate is None:
            continue
        candidate_json, nodes = _canonical_candidate_json_with_nodes(
            candidate,
            f"{label} outcome candidate",
        )
        byte_count += len(candidate_json.encode("utf-8"))
        node_count += nodes
    return byte_count, node_count


def _bounded_outcome_universe_v2(
    values: Iterable[Any],
    *,
    maximum: int,
) -> tuple[V2Outcome, ...]:
    """Stream, validate, and cap the aggregate candidate payload before retention."""

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError("v2 outcome universe must be iterable") from exc
    supplied: list[V2Outcome] = []
    candidate_bytes = 0
    candidate_nodes = 0
    for index, outcome in enumerate(islice(iterator, maximum + 1)):
        if index >= maximum:
            raise ValueError(f"v2 outcome universe may not exceed {maximum} items")
        if type(outcome) is not V2Outcome:
            raise ValueError("v2 outcomes must be exact parsed V2Outcome records")
        _revalidate_outcome(outcome)
        item_bytes, item_nodes = _outcome_candidate_metrics_v2(outcome)
        candidate_bytes += item_bytes
        candidate_nodes += item_nodes
        if candidate_bytes > _MAX_OUTCOME_UNIVERSE_CANDIDATE_BYTES:
            raise ValueError("v2 outcome candidate universe exceeds the 64,000,000-byte budget")
        if candidate_nodes > _MAX_OUTCOME_UNIVERSE_CANDIDATE_NODES:
            raise ValueError("v2 outcome candidate universe exceeds the 2,097,152-node budget")
        supplied.append(outcome)

    # Later iterator steps may execute arbitrary code and mutate an earlier yielded
    # record. Revalidate and recount the retained current state after consumption.
    final_bytes = 0
    final_nodes = 0
    for outcome in supplied:
        _revalidate_outcome(outcome)
        item_bytes, item_nodes = _outcome_candidate_metrics_v2(outcome)
        final_bytes += item_bytes
        final_nodes += item_nodes
        if final_bytes > _MAX_OUTCOME_UNIVERSE_CANDIDATE_BYTES:
            raise ValueError("v2 outcome candidate universe exceeds the 64,000,000-byte budget")
        if final_nodes > _MAX_OUTCOME_UNIVERSE_CANDIDATE_NODES:
            raise ValueError("v2 outcome candidate universe exceeds the 2,097,152-node budget")
    return tuple(supplied)


def _receipt_passes(
    receipt: V2VerifierReceipt | None,
    assignment: V2Assignment,
    kind: V2VerifierKind,
    authority: V2ReceiptAuthority,
    candidate_digest: str | None,
    solver_answer_digest: str,
    expected_case_digest: str,
    expected_verifier_digest: str,
) -> bool:
    return bool(
        receipt is not None
        and authority.verifies(receipt)
        and receipt.kind is kind
        and receipt.accepted
        and hmac.compare_digest(receipt.assignment_digest, assignment.digest)
        and hmac.compare_digest(receipt.case_digest, expected_case_digest)
        and candidate_digest is not None
        and hmac.compare_digest(receipt.candidate_digest, candidate_digest)
        and hmac.compare_digest(receipt.solver_answer_digest, solver_answer_digest)
        and hmac.compare_digest(receipt.verifier_digest, expected_verifier_digest)
        and (not receipt.independent if kind is V2VerifierKind.REPAIR else receipt.independent)
    )


def full_case_success_v2(
    assignment: V2Assignment,
    case: V2CaseSpec,
    outcome: V2Outcome,
    *,
    receipt_authority: V2ReceiptAuthority,
) -> bool:
    """Return one or zero; no component or partial credit exists in v2."""

    if type(assignment) is not V2Assignment or type(case) is not V2CaseSpec:
        raise ValueError("v2 scoring requires exact sealed assignment and case records")
    if type(outcome) is not V2Outcome:
        raise ValueError("v2 scoring requires an exact parsed outcome")
    if type(receipt_authority) is not V2ReceiptAuthority:
        raise ValueError("v2 scoring requires the precommitted evaluator receipt authority")
    _revalidate_assignment(assignment)
    _revalidate_case(case)
    _revalidate_outcome(outcome)
    _revalidate_authority(receipt_authority)
    return _full_case_success_validated(
        assignment,
        case,
        outcome,
        receipt_authority=receipt_authority,
    )


def _full_case_success_validated(
    assignment: V2Assignment,
    case: V2CaseSpec,
    outcome: V2Outcome,
    *,
    receipt_authority: V2ReceiptAuthority,
) -> bool:
    if assignment.case_id != case.case_id:
        raise ValueError("assignment and truth case ids differ")
    if not hmac.compare_digest(outcome.assignment_digest, assignment.digest):
        raise ValueError("outcome is not bound to this condition-specific assignment")
    if not hmac.compare_digest(outcome.solver_case_id, assignment.solver_case_id):
        raise ValueError("outcome opaque case identity differs from its assignment")
    if assignment.variant is Variant.CLEAN:
        return outcome.verdict is Verdict.VALID
    if outcome.verdict is not Verdict.INVALID:
        return False
    repair = outcome.repair_receipt
    witness = outcome.witness_receipt
    return bool(
        outcome.root_localization == case.root_localization
        and outcome.error_family == case.error_family
        and _receipt_passes(
            repair,
            assignment,
            V2VerifierKind.REPAIR,
            receipt_authority,
            _candidate_digest_v2(outcome.repair_candidate),
            outcome.solver_answer_digest,
            _digest_json(case.to_commitment_record()),
            case.repair_verifier_digest,
        )
        and _receipt_passes(
            witness,
            assignment,
            V2VerifierKind.INDEPENDENT_WITNESS,
            receipt_authority,
            _candidate_digest_v2(outcome.witness_candidate),
            outcome.solver_answer_digest,
            _digest_json(case.to_commitment_record()),
            case.witness_verifier_digest,
        )
        and repair is not None
        and witness is not None
        and not hmac.compare_digest(repair.verifier_digest, witness.verifier_digest)
        and not hmac.compare_digest(
            repair.replay_origin_digest,
            witness.replay_origin_digest,
        )
    )


@dataclass(frozen=True, slots=True)
class V2GateConfig:
    minimum_mean_case_delta: float = 0.04
    minimum_strictly_positive_cases: int = 12
    maximum_exact_case_randomization_p: float = 0.05
    minimum_mean_pair_delta: float = 0.04
    minimum_strictly_positive_pairs: int = 12
    maximum_exact_pair_randomization_p: float = 0.05
    minimum_mean_faulty_case_delta: float = 0.04
    minimum_strictly_positive_faulty_cases: int = 12
    maximum_exact_faulty_case_randomization_p: float = 0.05
    minimum_mean_faulty_pair_delta: float = 0.04
    minimum_strictly_positive_faulty_pairs: int = 12
    maximum_exact_faulty_pair_randomization_p: float = 0.05
    case_interval_confidence: float = 0.95
    case_bootstrap_repetitions: int = 4096
    maximum_clean_specificity_loss: float = 0.02
    maximum_faulty_populated_stratum_regression: float = 0.05
    minimum_populated_stratum_cases: int = 8
    minimum_difficulty_stratum_cases: int = 8
    minimum_claim_case_count: int = V2_MIN_CLAIM_CASE_COUNT
    maximum_case_count: int = V2_MAX_CASE_COUNT
    minimum_brain_off_success_for_valid_benchmark: float = 0.30
    maximum_brain_off_success_for_valid_benchmark: float = 0.85
    minimum_faulty_brain_off_success_for_valid_benchmark: float = 0.30
    maximum_faulty_brain_off_success_for_valid_benchmark: float = 0.85
    minimum_difficulty_stratum_brain_off_success: float = 0.30
    maximum_difficulty_stratum_brain_off_success: float = 0.85
    minimum_faulty_stratum_brain_off_success: float = 0.30
    maximum_faulty_stratum_brain_off_success: float = 0.85

    def __post_init__(self) -> None:
        for label, value in (
            ("minimum mean case delta", self.minimum_mean_case_delta),
            ("maximum exact case randomization p", self.maximum_exact_case_randomization_p),
            ("minimum mean pair delta", self.minimum_mean_pair_delta),
            ("maximum exact pair randomization p", self.maximum_exact_pair_randomization_p),
            ("minimum mean faulty-case delta", self.minimum_mean_faulty_case_delta),
            (
                "maximum exact faulty-case randomization p",
                self.maximum_exact_faulty_case_randomization_p,
            ),
            ("minimum mean faulty-pair delta", self.minimum_mean_faulty_pair_delta),
            (
                "maximum exact faulty-pair randomization p",
                self.maximum_exact_faulty_pair_randomization_p,
            ),
            ("case interval confidence", self.case_interval_confidence),
            ("maximum clean specificity loss", self.maximum_clean_specificity_loss),
            (
                "maximum faulty populated-stratum regression",
                self.maximum_faulty_populated_stratum_regression,
            ),
            (
                "minimum brain-off benchmark success",
                self.minimum_brain_off_success_for_valid_benchmark,
            ),
            (
                "maximum brain-off benchmark success",
                self.maximum_brain_off_success_for_valid_benchmark,
            ),
            (
                "minimum faulty brain-off benchmark success",
                self.minimum_faulty_brain_off_success_for_valid_benchmark,
            ),
            (
                "maximum faulty brain-off benchmark success",
                self.maximum_faulty_brain_off_success_for_valid_benchmark,
            ),
            (
                "minimum difficulty-stratum brain-off success",
                self.minimum_difficulty_stratum_brain_off_success,
            ),
            (
                "maximum difficulty-stratum brain-off success",
                self.maximum_difficulty_stratum_brain_off_success,
            ),
            (
                "minimum faulty-stratum brain-off success",
                self.minimum_faulty_stratum_brain_off_success,
            ),
            (
                "maximum faulty-stratum brain-off success",
                self.maximum_faulty_stratum_brain_off_success,
            ),
        ):
            _finite_float(value, label)
        for label, value in (
            ("mean case delta", self.minimum_mean_case_delta),
            ("mean pair delta", self.minimum_mean_pair_delta),
            ("mean faulty-case delta", self.minimum_mean_faulty_case_delta),
            ("mean faulty-pair delta", self.minimum_mean_faulty_pair_delta),
        ):
            if not 0.04 <= value <= 1:
                raise ValueError(f"minimum v2 {label} cannot be weaker than 0.04")
        for label, value in (
            ("strictly positive cases", self.minimum_strictly_positive_cases),
            (
                "strictly positive faulty cases",
                self.minimum_strictly_positive_faulty_cases,
            ),
        ):
            positive = _integer(value, f"minimum {label}", minimum=1)
            if not 12 <= positive <= V2_MAX_CASE_COUNT:
                raise ValueError(f"{label} gate must require at least twelve cases")
        for label, value in (
            ("strictly positive pairs", self.minimum_strictly_positive_pairs),
            ("strictly positive faulty pairs", self.minimum_strictly_positive_faulty_pairs),
        ):
            positive = _integer(value, f"minimum {label}", minimum=1)
            if not 12 <= positive <= V2_PAIR_COUNT:
                raise ValueError(f"{label} gate must require twelve through twenty pairs")
        for label, value in (
            ("case", self.maximum_exact_case_randomization_p),
            ("pair", self.maximum_exact_pair_randomization_p),
            ("faulty-case", self.maximum_exact_faulty_case_randomization_p),
            ("faulty-pair", self.maximum_exact_faulty_pair_randomization_p),
        ):
            if not 0 < value <= 0.05:
                raise ValueError(
                    f"exact {label} randomization gate cannot be weaker than p <= 0.05"
                )
        if self.case_interval_confidence != 0.95:
            raise ValueError("the required case-cluster interval confidence is exactly 0.95")
        repetitions = _integer(
            self.case_bootstrap_repetitions,
            "case-cluster bootstrap repetitions",
            minimum=4096,
        )
        if repetitions != 4096:
            raise ValueError("the sealed v2 bootstrap uses exactly 4,096 repetitions")
        if not 0 <= self.maximum_clean_specificity_loss <= 0.02:
            raise ValueError("clean-specificity gate cannot be weaker than 0.02")
        if not 0 <= self.maximum_faulty_populated_stratum_regression <= 0.05:
            raise ValueError("faulty populated-stratum gate cannot be weaker than 0.05")
        _integer(
            self.minimum_populated_stratum_cases,
            "minimum populated-stratum cases",
            minimum=8,
        )
        _integer(
            self.minimum_difficulty_stratum_cases,
            "minimum difficulty-stratum cases",
            minimum=8,
        )
        minimum_cases = _integer(
            self.minimum_claim_case_count,
            "minimum claim case count",
            minimum=V2_MIN_CLAIM_CASE_COUNT,
        )
        maximum_cases = _integer(
            self.maximum_case_count,
            "maximum case count",
            minimum=minimum_cases,
        )
        if maximum_cases > V2_MAX_CASE_COUNT:
            raise ValueError(f"maximum case count may not exceed {V2_MAX_CASE_COUNT}")
        if self.minimum_strictly_positive_cases > maximum_cases:
            raise ValueError("strict-positive case gate exceeds the sealed case maximum")
        if self.minimum_strictly_positive_faulty_cases > maximum_cases:
            raise ValueError("strict-positive faulty-case gate exceeds the sealed case maximum")
        if self.minimum_populated_stratum_cases > maximum_cases:
            raise ValueError("populated-stratum support exceeds the sealed case maximum")
        if self.minimum_difficulty_stratum_cases > maximum_cases:
            raise ValueError("difficulty-stratum support exceeds the sealed case maximum")
        if not 0.30 <= self.minimum_brain_off_success_for_valid_benchmark < 1:
            raise ValueError("brain-off difficulty floor cannot be weaker than 0.30")
        if not 0 < self.maximum_brain_off_success_for_valid_benchmark <= 0.85:
            raise ValueError("brain-off ceiling cannot be weaker than 0.85")
        if (
            self.minimum_brain_off_success_for_valid_benchmark
            >= self.maximum_brain_off_success_for_valid_benchmark
        ):
            raise ValueError("brain-off difficulty floor must be below its ceiling")
        if not 0.30 <= self.minimum_faulty_brain_off_success_for_valid_benchmark < 1:
            raise ValueError("faulty brain-off difficulty floor cannot be weaker than 0.30")
        if not 0 < self.maximum_faulty_brain_off_success_for_valid_benchmark <= 0.85:
            raise ValueError("faulty brain-off ceiling cannot be weaker than 0.85")
        if (
            self.minimum_faulty_brain_off_success_for_valid_benchmark
            >= self.maximum_faulty_brain_off_success_for_valid_benchmark
        ):
            raise ValueError("faulty brain-off difficulty floor must be below its ceiling")
        if not 0.30 <= self.minimum_difficulty_stratum_brain_off_success < 1:
            raise ValueError("difficulty-stratum floor cannot be weaker than 0.30")
        if not 0 < self.maximum_difficulty_stratum_brain_off_success <= 0.85:
            raise ValueError("difficulty-stratum ceiling cannot be weaker than 0.85")
        if (
            self.minimum_difficulty_stratum_brain_off_success
            >= self.maximum_difficulty_stratum_brain_off_success
        ):
            raise ValueError("difficulty-stratum floor must be below its ceiling")
        if not 0.30 <= self.minimum_faulty_stratum_brain_off_success < 1:
            raise ValueError("faulty-stratum difficulty floor cannot be weaker than 0.30")
        if not 0 < self.maximum_faulty_stratum_brain_off_success <= 0.85:
            raise ValueError("faulty-stratum ceiling cannot be weaker than 0.85")
        if (
            self.minimum_faulty_stratum_brain_off_success
            >= self.maximum_faulty_stratum_brain_off_success
        ):
            raise ValueError("faulty-stratum difficulty floor must be below its ceiling")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_bootstrap_repetitions": self.case_bootstrap_repetitions,
            "case_interval_confidence": self.case_interval_confidence,
            "maximum_exact_case_randomization_p": self.maximum_exact_case_randomization_p,
            "maximum_exact_faulty_case_randomization_p": (
                self.maximum_exact_faulty_case_randomization_p
            ),
            "maximum_exact_pair_randomization_p": self.maximum_exact_pair_randomization_p,
            "maximum_exact_faulty_pair_randomization_p": (
                self.maximum_exact_faulty_pair_randomization_p
            ),
            "maximum_brain_off_success_for_valid_benchmark": (
                self.maximum_brain_off_success_for_valid_benchmark
            ),
            "maximum_clean_specificity_loss": self.maximum_clean_specificity_loss,
            "maximum_case_count": self.maximum_case_count,
            "maximum_difficulty_stratum_brain_off_success": (
                self.maximum_difficulty_stratum_brain_off_success
            ),
            "maximum_faulty_populated_stratum_regression": (
                self.maximum_faulty_populated_stratum_regression
            ),
            "maximum_faulty_brain_off_success_for_valid_benchmark": (
                self.maximum_faulty_brain_off_success_for_valid_benchmark
            ),
            "maximum_faulty_stratum_brain_off_success": (
                self.maximum_faulty_stratum_brain_off_success
            ),
            "minimum_brain_off_success_for_valid_benchmark": (
                self.minimum_brain_off_success_for_valid_benchmark
            ),
            "minimum_claim_case_count": self.minimum_claim_case_count,
            "minimum_faulty_brain_off_success_for_valid_benchmark": (
                self.minimum_faulty_brain_off_success_for_valid_benchmark
            ),
            "minimum_faulty_stratum_brain_off_success": (
                self.minimum_faulty_stratum_brain_off_success
            ),
            "minimum_difficulty_stratum_brain_off_success": (
                self.minimum_difficulty_stratum_brain_off_success
            ),
            "minimum_difficulty_stratum_cases": self.minimum_difficulty_stratum_cases,
            "minimum_mean_case_delta": self.minimum_mean_case_delta,
            "minimum_mean_faulty_case_delta": self.minimum_mean_faulty_case_delta,
            "minimum_mean_pair_delta": self.minimum_mean_pair_delta,
            "minimum_mean_faulty_pair_delta": self.minimum_mean_faulty_pair_delta,
            "minimum_populated_stratum_cases": self.minimum_populated_stratum_cases,
            "minimum_strictly_positive_cases": self.minimum_strictly_positive_cases,
            "minimum_strictly_positive_faulty_cases": (self.minimum_strictly_positive_faulty_cases),
            "minimum_strictly_positive_pairs": self.minimum_strictly_positive_pairs,
            "minimum_strictly_positive_faulty_pairs": (self.minimum_strictly_positive_faulty_pairs),
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.to_dict())


# Frozen records are not a trust boundary: ``object.__setattr__`` can still alter
# them after construction.  Every evaluator-facing consumer must walk the exact
# nested record graph again before it hashes, dispatches, verifies, or scores it.
def _revalidate_view(view: V2RenderedView) -> None:
    if type(view) is not V2RenderedView:
        raise ValueError("rendered view must be an exact V2RenderedView")
    view.__post_init__()


def _revalidate_case(case: V2CaseSpec) -> None:
    if type(case) is not V2CaseSpec:
        raise ValueError("case must be an exact V2CaseSpec")
    _revalidate_view(case.clean_view)
    _revalidate_view(case.faulty_view)
    case.__post_init__()


def _revalidate_context(card: V2ContextCard) -> None:
    if type(card) is not V2ContextCard:
        raise ValueError("context must be an exact V2ContextCard")
    card.__post_init__()


def _revalidate_commitment(commitment: V2DesignCommitment) -> None:
    if type(commitment) is not V2DesignCommitment:
        raise ValueError("commitment must be an exact V2DesignCommitment")
    commitment.__post_init__()


def _revalidate_active_control(control: V2ActiveControlPlan) -> None:
    if type(control) is not V2ActiveControlPlan:
        raise ValueError("active control must be an exact V2ActiveControlPlan")
    if type(control.bindings) is not tuple or any(
        type(binding) is not V2ControlBinding for binding in control.bindings
    ):
        raise ValueError("active-control bindings must be exact records in a tuple")
    for binding in control.bindings:
        binding.__post_init__()
    control.__post_init__()


def _revalidate_gate(gate: V2GateConfig) -> None:
    if type(gate) is not V2GateConfig:
        raise ValueError("gate must be an exact V2GateConfig")
    gate.__post_init__()


def _revalidate_assignment(assignment: V2Assignment) -> None:
    if type(assignment) is not V2Assignment:
        raise ValueError("assignment must be an exact V2Assignment")
    _revalidate_view(assignment.view)
    assignment.__post_init__()


def _revalidate_bound_verifier(verifier: V2BoundVerifier) -> None:
    if type(verifier) is not V2BoundVerifier:
        raise ValueError("bound verifier must be an exact V2BoundVerifier")
    verifier.__post_init__()


def _revalidate_receipt(receipt: V2VerifierReceipt) -> None:
    if type(receipt) is not V2VerifierReceipt:
        raise ValueError("receipt must be an exact V2VerifierReceipt")
    receipt.__post_init__()


def _revalidate_outcome(outcome: V2Outcome) -> None:
    if type(outcome) is not V2Outcome:
        raise ValueError("outcome must be an exact V2Outcome")
    if outcome.repair_receipt is not None:
        _revalidate_receipt(outcome.repair_receipt)
    if outcome.witness_receipt is not None:
        _revalidate_receipt(outcome.witness_receipt)
    outcome.__post_init__()


def _revalidate_authority(authority: V2ReceiptAuthority) -> None:
    if type(authority) is not V2ReceiptAuthority:
        raise ValueError("authority must be an exact V2ReceiptAuthority")
    authority.__post_init__()


def _revalidate_solver_case(case: V2SolverCase) -> None:
    if type(case) is not V2SolverCase:
        raise ValueError("solver case must be an exact V2SolverCase")
    _revalidate_view(case.view)
    case.__post_init__()


def _revalidate_solver_batch(batch: V2SolverBatch) -> None:
    if type(batch) is not V2SolverBatch:
        raise ValueError("solver batch must be an exact V2SolverBatch")
    if type(batch.cases) is not tuple or any(
        type(case) is not V2SolverCase for case in batch.cases
    ):
        raise ValueError("solver batch cases must be exact records in a tuple")
    for case in batch.cases:
        _revalidate_solver_case(case)
    batch.__post_init__()


def _revalidate_plan(plan: V2EvaluationPlan) -> None:
    if type(plan) is not V2EvaluationPlan:
        raise ValueError("plan must be an exact V2EvaluationPlan")
    plan.__post_init__()


@dataclass(frozen=True, slots=True)
class V2CeilingDiagnosis:
    brain_off_success: float
    remaining_headroom: float
    brain_off_errors: int
    brain_off_total: int
    minimum_acceptable_success: float
    maximum_acceptable_success: float
    benchmark_too_hard: bool
    benchmark_too_easy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_too_easy": self.benchmark_too_easy,
            "benchmark_too_hard": self.benchmark_too_hard,
            "brain_off_errors": self.brain_off_errors,
            "brain_off_success": self.brain_off_success,
            "brain_off_total": self.brain_off_total,
            "maximum_acceptable_success": self.maximum_acceptable_success,
            "minimum_acceptable_success": self.minimum_acceptable_success,
            "remaining_headroom": self.remaining_headroom,
        }


@dataclass(frozen=True, slots=True)
class V2AggregateSummary:
    design_commitment_digest: str
    plan_digest: str
    gate_config_digest: str
    result_universe_digest: str
    brain_on_success: float
    brain_off_success: float
    mean_case_delta: float
    median_case_delta: float
    exact_case_randomization_p: float
    case_interval: tuple[float, float]
    strictly_positive_cases: int
    tied_cases: int
    negative_cases: int
    mean_pair_delta: float
    median_pair_delta: float
    exact_pair_randomization_p: float
    pair_interval: tuple[float, float]
    strictly_positive_pairs: int
    tied_pairs: int
    negative_pairs: int
    mean_faulty_case_delta: float
    median_faulty_case_delta: float
    exact_faulty_case_randomization_p: float
    faulty_case_interval: tuple[float, float]
    strictly_positive_faulty_cases: int
    tied_faulty_cases: int
    negative_faulty_cases: int
    mean_faulty_pair_delta: float
    median_faulty_pair_delta: float
    exact_faulty_pair_randomization_p: float
    faulty_pair_interval: tuple[float, float]
    strictly_positive_faulty_pairs: int
    tied_faulty_pairs: int
    negative_faulty_pairs: int
    clean_success_brain_on: float
    clean_success_brain_off: float
    faulty_success_brain_on: float
    faulty_success_brain_off: float
    clean_specificity_loss: float
    worst_faulty_populated_stratum_regression: float
    adequately_populated_stratum_count: int
    insufficient_stratum_count: int
    minimum_stratum_case_support: int
    minimum_populated_stratum_brain_off_success_observed: float
    maximum_populated_stratum_brain_off_success_observed: float
    minimum_faulty_populated_stratum_brain_off_success_observed: float
    maximum_faulty_populated_stratum_brain_off_success_observed: float
    adequately_populated_difficulty_stratum_count: int
    insufficient_difficulty_stratum_count: int
    minimum_difficulty_stratum_case_support: int
    minimum_difficulty_stratum_brain_off_success_observed: float
    maximum_difficulty_stratum_brain_off_success_observed: float
    case_count: int
    observation_count: int
    pair_count: int
    minimum_faulty_prevalence: float
    maximum_faulty_prevalence: float
    distinct_faulty_prevalences: int
    ceiling: V2CeilingDiagnosis
    faulty_ceiling: V2CeilingDiagnosis
    helped: bool
    failed_gates: tuple[str, ...]
    schema: str = V2_SUMMARY_SCHEMA
    schema_version: int = V2_SUMMARY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != V2_SUMMARY_SCHEMA:
            raise ValueError("aggregate summary must use the exact v2 summary schema")
        if type(self.schema_version) is not int or self.schema_version != V2_SUMMARY_SCHEMA_VERSION:
            raise ValueError("aggregate summary must use the exact v2 summary schema version")
        _digest(self.design_commitment_digest, "summary design commitment")
        _digest(self.plan_digest, "summary plan")
        _digest(self.gate_config_digest, "summary gate config")
        _digest(self.result_universe_digest, "summary result universe")

    def to_dict(self) -> dict[str, Any]:
        """Serialize aggregates only: no case, assignment, receipt, or pair-level rows."""

        self.__post_init__()
        return {
            "brain_off_success": self.brain_off_success,
            "brain_on_success": self.brain_on_success,
            "case_count": self.case_count,
            "ceiling": self.ceiling.to_dict(),
            "clean_success_brain_off": self.clean_success_brain_off,
            "clean_success_brain_on": self.clean_success_brain_on,
            "clean_specificity_loss": self.clean_specificity_loss,
            "distinct_faulty_prevalences": self.distinct_faulty_prevalences,
            "design_commitment_digest": self.design_commitment_digest,
            "exact_case_randomization_p": self.exact_case_randomization_p,
            "exact_faulty_case_randomization_p": (self.exact_faulty_case_randomization_p),
            "exact_pair_randomization_p": self.exact_pair_randomization_p,
            "exact_faulty_pair_randomization_p": (self.exact_faulty_pair_randomization_p),
            "failed_gates": list(self.failed_gates),
            "faulty_case_interval": list(self.faulty_case_interval),
            "faulty_pair_interval": list(self.faulty_pair_interval),
            "faulty_success_brain_off": self.faulty_success_brain_off,
            "faulty_success_brain_on": self.faulty_success_brain_on,
            "helped": self.helped,
            "gate_config_digest": self.gate_config_digest,
            "maximum_faulty_prevalence": self.maximum_faulty_prevalence,
            "mean_case_delta": self.mean_case_delta,
            "mean_faulty_case_delta": self.mean_faulty_case_delta,
            "mean_pair_delta": self.mean_pair_delta,
            "mean_faulty_pair_delta": self.mean_faulty_pair_delta,
            "median_case_delta": self.median_case_delta,
            "median_faulty_case_delta": self.median_faulty_case_delta,
            "median_pair_delta": self.median_pair_delta,
            "median_faulty_pair_delta": self.median_faulty_pair_delta,
            "minimum_faulty_prevalence": self.minimum_faulty_prevalence,
            "negative_cases": self.negative_cases,
            "negative_faulty_cases": self.negative_faulty_cases,
            "negative_pairs": self.negative_pairs,
            "negative_faulty_pairs": self.negative_faulty_pairs,
            "observation_count": self.observation_count,
            "case_interval": list(self.case_interval),
            "pair_interval": list(self.pair_interval),
            "pair_count": self.pair_count,
            "plan_digest": self.plan_digest,
            "adequately_populated_stratum_count": (self.adequately_populated_stratum_count),
            "insufficient_stratum_count": self.insufficient_stratum_count,
            "minimum_stratum_case_support": self.minimum_stratum_case_support,
            "minimum_populated_stratum_brain_off_success_observed": (
                self.minimum_populated_stratum_brain_off_success_observed
            ),
            "maximum_populated_stratum_brain_off_success_observed": (
                self.maximum_populated_stratum_brain_off_success_observed
            ),
            "minimum_faulty_populated_stratum_brain_off_success_observed": (
                self.minimum_faulty_populated_stratum_brain_off_success_observed
            ),
            "maximum_faulty_populated_stratum_brain_off_success_observed": (
                self.maximum_faulty_populated_stratum_brain_off_success_observed
            ),
            "adequately_populated_difficulty_stratum_count": (
                self.adequately_populated_difficulty_stratum_count
            ),
            "insufficient_difficulty_stratum_count": (self.insufficient_difficulty_stratum_count),
            "minimum_difficulty_stratum_case_support": (
                self.minimum_difficulty_stratum_case_support
            ),
            "minimum_difficulty_stratum_brain_off_success_observed": (
                self.minimum_difficulty_stratum_brain_off_success_observed
            ),
            "maximum_difficulty_stratum_brain_off_success_observed": (
                self.maximum_difficulty_stratum_brain_off_success_observed
            ),
            "schema": self.schema,
            "schema_version": self.schema_version,
            "result_universe_digest": self.result_universe_digest,
            "faulty_ceiling": self.faulty_ceiling.to_dict(),
            "strictly_positive_cases": self.strictly_positive_cases,
            "strictly_positive_faulty_cases": self.strictly_positive_faulty_cases,
            "strictly_positive_pairs": self.strictly_positive_pairs,
            "strictly_positive_faulty_pairs": self.strictly_positive_faulty_pairs,
            "tied_cases": self.tied_cases,
            "tied_faulty_cases": self.tied_faulty_cases,
            "tied_pairs": self.tied_pairs,
            "tied_faulty_pairs": self.tied_faulty_pairs,
            "worst_faulty_populated_stratum_regression": (
                self.worst_faulty_populated_stratum_regression
            ),
        }


def _rate(successes: int, total: int) -> Fraction:
    if total <= 0:
        raise AssertionError("aggregate denominator must be positive")
    return Fraction(successes, total)


def _median(values: Sequence[Fraction]) -> Fraction:
    if not values:
        raise ValueError("paired median requires at least one delta")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _exact_one_sided_sign_flip_p(values: Sequence[Fraction]) -> Fraction:
    """Exact cluster-level sign-flip tail; zero-effect clusters are inert ties."""

    if not values:
        raise ValueError("exact paired test requires at least one delta")
    nonzero = tuple(value for value in values if value)
    if not nonzero:
        return Fraction(1)
    observed = sum(nonzero, Fraction())
    distribution: Counter[Fraction] = Counter({Fraction(): 1})
    for value in nonzero:
        updated: Counter[Fraction] = Counter()
        for subtotal, multiplicity in distribution.items():
            updated[subtotal + value] += multiplicity
            updated[subtotal - value] += multiplicity
        distribution = updated
    extreme = sum(
        multiplicity for candidate, multiplicity in distribution.items() if candidate >= observed
    )
    return Fraction(extreme, 1 << len(nonzero))


def _paired_bootstrap_interval_v2(
    values: Sequence[Fraction],
    *,
    plan_digest: str,
    repetitions: int,
    confidence: float,
    domain: str,
) -> tuple[Fraction, Fraction]:
    if len(values) < 2:
        raise ValueError("paired bootstrap requires at least two deltas")
    seed = bytes.fromhex(_digest(plan_digest, "bootstrap plan digest").removeprefix("sha256:"))
    samples = []
    for repetition in range(repetitions):
        total = Fraction()
        for draw in range(len(values)):
            index = int.from_bytes(
                _rank(seed, "case-cluster-bootstrap", domain, repetition, draw), "big"
            ) % len(values)
            total += values[index]
        samples.append(total / len(values))
    samples.sort()
    tail = (1 - confidence) / 2
    low_index = max(0, math.floor(tail * len(samples)))
    high_index = min(len(samples) - 1, math.ceil((1 - tail) * len(samples)) - 1)
    return samples[low_index], samples[high_index]


def summarize_v2(
    plan: V2EvaluationPlan,
    outcomes: Iterable[V2Outcome],
    *,
    receipt_authority: V2ReceiptAuthority,
    gate: V2GateConfig | None = None,
) -> V2AggregateSummary:
    """Validate the complete sealed universe and emit an aggregate-only decision."""

    if type(plan) is not V2EvaluationPlan:
        raise ValueError("v2 summary requires an exact sealed evaluation plan")
    _revalidate_plan(plan)
    if type(receipt_authority) is not V2ReceiptAuthority:
        raise ValueError("v2 summary requires an exact receipt authority")
    _revalidate_authority(receipt_authority)
    if not hmac.compare_digest(receipt_authority.digest, plan.commitment.receipt_authority_digest):
        raise ValueError("v2 summary receipt authority differs from the precommitment")
    config = plan.gate if gate is None else gate
    if type(config) is not V2GateConfig:
        raise ValueError("v2 summary gate must be an exact V2GateConfig")
    _revalidate_gate(config)
    if not hmac.compare_digest(config.digest, plan.commitment.gate_config_digest):
        raise ValueError("v2 summary gate differs from the precommitment")
    supplied = _bounded_outcome_universe_v2(outcomes, maximum=len(plan.assignments))
    # Consuming an arbitrary outcome iterable can run user code.  Revalidate the
    # complete sealed evaluator state again after that execution boundary.
    _revalidate_plan(plan)
    _revalidate_authority(receipt_authority)
    _revalidate_gate(config)
    if not hmac.compare_digest(receipt_authority.digest, plan.commitment.receipt_authority_digest):
        raise ValueError("v2 summary receipt authority differs from the precommitment")
    if not hmac.compare_digest(config.digest, plan.commitment.gate_config_digest):
        raise ValueError("v2 summary gate differs from the precommitment")
    by_digest = {outcome.assignment_digest: outcome for outcome in supplied}
    if len(by_digest) != len(supplied):
        raise ValueError("v2 summary rejects duplicate outcomes")
    expected = {assignment.digest for assignment in plan.assignments}
    if set(by_digest) != expected:
        raise ValueError("v2 summary requires the exact complete sealed assignment universe")
    result_universe_records = [
        {
            "assignment_digest": outcome.assignment_digest,
            "repair_receipt_seal": (
                None if outcome.repair_receipt is None else outcome.repair_receipt.seal
            ),
            "solver_answer_digest": outcome.solver_answer_digest,
            "solver_case_id": outcome.solver_case_id,
            "verdict": outcome.verdict.value,
            "witness_receipt_seal": (
                None if outcome.witness_receipt is None else outcome.witness_receipt.seal
            ),
        }
        for outcome in sorted(supplied, key=lambda item: item.assignment_digest)
    ]
    result_universe_digest = _digest_bytes(
        b"math-brain/evaluation-v2/result-universe/v1\x00"
        + plan.commitment.digest.encode("ascii")
        + b"\x00"
        + canonical_json(result_universe_records).encode("utf-8")
    )

    cases = {case.case_id: case for case in plan.cases}
    successes: dict[Condition, int] = defaultdict(int)
    totals: dict[Condition, int] = defaultdict(int)
    variant_successes: dict[tuple[Condition, Variant], int] = defaultdict(int)
    variant_totals: dict[tuple[Condition, Variant], int] = defaultdict(int)
    case_successes: dict[tuple[str, Condition], int] = defaultdict(int)
    case_totals: dict[tuple[str, Condition], int] = defaultdict(int)
    case_variant_successes: dict[tuple[str, Condition, Variant], int] = defaultdict(int)
    case_variant_totals: dict[tuple[str, Condition, Variant], int] = defaultdict(int)
    pair_successes: dict[tuple[int, Condition], int] = defaultdict(int)
    pair_totals: dict[tuple[int, Condition], int] = defaultdict(int)
    faulty_pair_successes: dict[tuple[int, Condition], int] = defaultdict(int)
    faulty_pair_totals: dict[tuple[int, Condition], int] = defaultdict(int)
    faulty_stratum_successes: dict[tuple[tuple[str, str, str], Condition], int] = defaultdict(int)
    faulty_stratum_totals: dict[tuple[tuple[str, str, str], Condition], int] = defaultdict(int)
    stratum_successes: dict[tuple[tuple[str, str, str], Condition], int] = defaultdict(int)
    stratum_totals: dict[tuple[tuple[str, str, str], Condition], int] = defaultdict(int)
    difficulty_successes: dict[tuple[str, Condition], int] = defaultdict(int)
    difficulty_totals: dict[tuple[str, Condition], int] = defaultdict(int)
    for assignment in plan.assignments:
        case = cases[assignment.case_id]
        passed = _full_case_success_validated(
            assignment,
            case,
            by_digest[assignment.digest],
            receipt_authority=receipt_authority,
        )
        successes[assignment.condition] += int(passed)
        totals[assignment.condition] += 1
        variant_successes[(assignment.condition, assignment.variant)] += int(passed)
        variant_totals[(assignment.condition, assignment.variant)] += 1
        case_successes[(case.case_id, assignment.condition)] += int(passed)
        case_totals[(case.case_id, assignment.condition)] += 1
        case_variant_successes[(case.case_id, assignment.condition, assignment.variant)] += int(
            passed
        )
        case_variant_totals[(case.case_id, assignment.condition, assignment.variant)] += 1
        pair_successes[(assignment.pair_id, assignment.condition)] += int(passed)
        pair_totals[(assignment.pair_id, assignment.condition)] += 1
        difficulty_successes[(case.difficulty, assignment.condition)] += int(passed)
        difficulty_totals[(case.difficulty, assignment.condition)] += 1
        for stratum in case.strata:
            stratum_successes[(stratum, assignment.condition)] += int(passed)
            stratum_totals[(stratum, assignment.condition)] += 1
        if assignment.variant is Variant.FAULTY:
            faulty_pair_successes[(assignment.pair_id, assignment.condition)] += int(passed)
            faulty_pair_totals[(assignment.pair_id, assignment.condition)] += 1
            for stratum in case.strata:
                faulty_stratum_successes[(stratum, assignment.condition)] += int(passed)
                faulty_stratum_totals[(stratum, assignment.condition)] += 1

    on_rate = _rate(successes[Condition.BRAIN_ON], totals[Condition.BRAIN_ON])
    off_rate = _rate(successes[Condition.BRAIN_OFF], totals[Condition.BRAIN_OFF])
    case_deltas = []
    faulty_case_deltas = []
    for case in plan.cases:
        case_on = _rate(
            case_successes[(case.case_id, Condition.BRAIN_ON)],
            case_totals[(case.case_id, Condition.BRAIN_ON)],
        )
        case_off = _rate(
            case_successes[(case.case_id, Condition.BRAIN_OFF)],
            case_totals[(case.case_id, Condition.BRAIN_OFF)],
        )
        faulty_on = _rate(
            case_variant_successes[(case.case_id, Condition.BRAIN_ON, Variant.FAULTY)],
            case_variant_totals[(case.case_id, Condition.BRAIN_ON, Variant.FAULTY)],
        )
        faulty_off = _rate(
            case_variant_successes[(case.case_id, Condition.BRAIN_OFF, Variant.FAULTY)],
            case_variant_totals[(case.case_id, Condition.BRAIN_OFF, Variant.FAULTY)],
        )
        case_deltas.append(case_on - case_off)
        faulty_case_deltas.append(faulty_on - faulty_off)

    positive_cases = sum(delta > 0 for delta in case_deltas)
    tied_cases = sum(delta == 0 for delta in case_deltas)
    negative_cases = sum(delta < 0 for delta in case_deltas)
    mean_case_delta = sum(case_deltas, Fraction()) / len(case_deltas)
    median_case_delta = _median(case_deltas)
    case_randomization_p = _exact_one_sided_sign_flip_p(case_deltas)
    case_interval = _paired_bootstrap_interval_v2(
        case_deltas,
        plan_digest=plan.digest,
        repetitions=config.case_bootstrap_repetitions,
        confidence=config.case_interval_confidence,
        domain="all-cases",
    )
    positive_faulty_cases = sum(delta > 0 for delta in faulty_case_deltas)
    tied_faulty_cases = sum(delta == 0 for delta in faulty_case_deltas)
    negative_faulty_cases = sum(delta < 0 for delta in faulty_case_deltas)
    mean_faulty_case_delta = sum(faulty_case_deltas, Fraction()) / len(faulty_case_deltas)
    median_faulty_case_delta = _median(faulty_case_deltas)
    faulty_case_randomization_p = _exact_one_sided_sign_flip_p(faulty_case_deltas)
    faulty_case_interval = _paired_bootstrap_interval_v2(
        faulty_case_deltas,
        plan_digest=plan.digest,
        repetitions=config.case_bootstrap_repetitions,
        confidence=config.case_interval_confidence,
        domain="faulty-cases",
    )

    pair_deltas = []
    faulty_pair_deltas = []
    for pair_id in range(V2_PAIR_COUNT):
        pair_on = _rate(
            pair_successes[(pair_id, Condition.BRAIN_ON)],
            pair_totals[(pair_id, Condition.BRAIN_ON)],
        )
        pair_off = _rate(
            pair_successes[(pair_id, Condition.BRAIN_OFF)],
            pair_totals[(pair_id, Condition.BRAIN_OFF)],
        )
        faulty_pair_on = _rate(
            faulty_pair_successes[(pair_id, Condition.BRAIN_ON)],
            faulty_pair_totals[(pair_id, Condition.BRAIN_ON)],
        )
        faulty_pair_off = _rate(
            faulty_pair_successes[(pair_id, Condition.BRAIN_OFF)],
            faulty_pair_totals[(pair_id, Condition.BRAIN_OFF)],
        )
        pair_deltas.append(pair_on - pair_off)
        faulty_pair_deltas.append(faulty_pair_on - faulty_pair_off)

    positive_pairs = sum(delta > 0 for delta in pair_deltas)
    tied_pairs = sum(delta == 0 for delta in pair_deltas)
    negative_pairs = sum(delta < 0 for delta in pair_deltas)
    mean_pair_delta = sum(pair_deltas, Fraction()) / len(pair_deltas)
    median_pair_delta = _median(pair_deltas)
    pair_randomization_p = _exact_one_sided_sign_flip_p(pair_deltas)
    pair_interval = _paired_bootstrap_interval_v2(
        pair_deltas,
        plan_digest=plan.digest,
        repetitions=config.case_bootstrap_repetitions,
        confidence=config.case_interval_confidence,
        domain="all-pairs",
    )
    positive_faulty_pairs = sum(delta > 0 for delta in faulty_pair_deltas)
    tied_faulty_pairs = sum(delta == 0 for delta in faulty_pair_deltas)
    negative_faulty_pairs = sum(delta < 0 for delta in faulty_pair_deltas)
    mean_faulty_pair_delta = sum(faulty_pair_deltas, Fraction()) / len(faulty_pair_deltas)
    median_faulty_pair_delta = _median(faulty_pair_deltas)
    faulty_pair_randomization_p = _exact_one_sided_sign_flip_p(faulty_pair_deltas)
    faulty_pair_interval = _paired_bootstrap_interval_v2(
        faulty_pair_deltas,
        plan_digest=plan.digest,
        repetitions=config.case_bootstrap_repetitions,
        confidence=config.case_interval_confidence,
        domain="faulty-pairs",
    )

    clean_on = _rate(
        variant_successes[(Condition.BRAIN_ON, Variant.CLEAN)],
        variant_totals[(Condition.BRAIN_ON, Variant.CLEAN)],
    )
    clean_off = _rate(
        variant_successes[(Condition.BRAIN_OFF, Variant.CLEAN)],
        variant_totals[(Condition.BRAIN_OFF, Variant.CLEAN)],
    )
    faulty_off = _rate(
        variant_successes[(Condition.BRAIN_OFF, Variant.FAULTY)],
        variant_totals[(Condition.BRAIN_OFF, Variant.FAULTY)],
    )
    clean_specificity_loss = max(Fraction(), clean_off - clean_on)
    stratum_cases: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for case in plan.cases:
        for stratum in case.strata:
            stratum_cases[stratum].add(case.case_id)
    sufficiently_populated = sorted(
        stratum
        for stratum, case_ids in stratum_cases.items()
        if len(case_ids) >= config.minimum_populated_stratum_cases
    )
    insufficient_strata = sorted(set(stratum_cases) - set(sufficiently_populated))
    stratum_regressions = []
    for stratum in sufficiently_populated:
        on = _rate(
            faulty_stratum_successes[(stratum, Condition.BRAIN_ON)],
            faulty_stratum_totals[(stratum, Condition.BRAIN_ON)],
        )
        off = _rate(
            faulty_stratum_successes[(stratum, Condition.BRAIN_OFF)],
            faulty_stratum_totals[(stratum, Condition.BRAIN_OFF)],
        )
        stratum_regressions.append(max(Fraction(), off - on))
    worst_stratum_regression = max(stratum_regressions, default=Fraction())
    populated_stratum_off_rates = tuple(
        _rate(
            stratum_successes[(stratum, Condition.BRAIN_OFF)],
            stratum_totals[(stratum, Condition.BRAIN_OFF)],
        )
        for stratum in sufficiently_populated
    )
    minimum_populated_stratum_rate = min(populated_stratum_off_rates, default=Fraction())
    maximum_populated_stratum_rate = max(populated_stratum_off_rates, default=Fraction(1))
    faulty_populated_stratum_off_rates = tuple(
        _rate(
            faulty_stratum_successes[(stratum, Condition.BRAIN_OFF)],
            faulty_stratum_totals[(stratum, Condition.BRAIN_OFF)],
        )
        for stratum in sufficiently_populated
    )
    minimum_faulty_populated_stratum_rate = min(
        faulty_populated_stratum_off_rates,
        default=Fraction(),
    )
    maximum_faulty_populated_stratum_rate = max(
        faulty_populated_stratum_off_rates,
        default=Fraction(1),
    )

    difficulty_cases: dict[str, set[str]] = defaultdict(set)
    for case in plan.cases:
        difficulty_cases[case.difficulty].add(case.case_id)
    sufficiently_populated_difficulties = sorted(
        difficulty
        for difficulty, case_ids in difficulty_cases.items()
        if len(case_ids) >= config.minimum_difficulty_stratum_cases
    )
    insufficient_difficulties = sorted(
        set(difficulty_cases) - set(sufficiently_populated_difficulties)
    )
    difficulty_off_rates = tuple(
        _rate(
            difficulty_successes[(difficulty, Condition.BRAIN_OFF)],
            difficulty_totals[(difficulty, Condition.BRAIN_OFF)],
        )
        for difficulty in sorted(difficulty_cases)
    )
    minimum_difficulty_rate = min(difficulty_off_rates)
    maximum_difficulty_rate = max(difficulty_off_rates)

    too_hard = float(off_rate) < config.minimum_brain_off_success_for_valid_benchmark
    too_easy = float(off_rate) > config.maximum_brain_off_success_for_valid_benchmark
    ceiling = V2CeilingDiagnosis(
        brain_off_success=float(off_rate),
        remaining_headroom=float(1 - off_rate),
        brain_off_errors=totals[Condition.BRAIN_OFF] - successes[Condition.BRAIN_OFF],
        brain_off_total=totals[Condition.BRAIN_OFF],
        minimum_acceptable_success=config.minimum_brain_off_success_for_valid_benchmark,
        maximum_acceptable_success=config.maximum_brain_off_success_for_valid_benchmark,
        benchmark_too_hard=too_hard,
        benchmark_too_easy=too_easy,
    )
    faulty_too_hard = (
        float(faulty_off) < config.minimum_faulty_brain_off_success_for_valid_benchmark
    )
    faulty_too_easy = (
        float(faulty_off) > config.maximum_faulty_brain_off_success_for_valid_benchmark
    )
    faulty_total = variant_totals[(Condition.BRAIN_OFF, Variant.FAULTY)]
    faulty_ceiling = V2CeilingDiagnosis(
        brain_off_success=float(faulty_off),
        remaining_headroom=float(1 - faulty_off),
        brain_off_errors=(faulty_total - variant_successes[(Condition.BRAIN_OFF, Variant.FAULTY)]),
        brain_off_total=faulty_total,
        minimum_acceptable_success=(config.minimum_faulty_brain_off_success_for_valid_benchmark),
        maximum_acceptable_success=(config.maximum_faulty_brain_off_success_for_valid_benchmark),
        benchmark_too_hard=faulty_too_hard,
        benchmark_too_easy=faulty_too_easy,
    )
    failed = []
    if float(mean_case_delta) < config.minimum_mean_case_delta:
        failed.append("primary_mean_case_delta")
    # The twenty repeated pair runs are measurements within a case cluster, not n=20.
    if positive_cases < config.minimum_strictly_positive_cases:
        failed.append("strictly_positive_cases")
    if float(case_randomization_p) > config.maximum_exact_case_randomization_p:
        failed.append("exact_case_randomization_p")
    if case_interval[0] <= 0:
        failed.append("case_interval_lower_bound")
    if median_case_delta <= 0:
        failed.append("median_case_delta")
    # Cases and collection pairs are crossed dependence axes. Requiring both
    # cluster directions prevents a handful of shared batch shocks from being
    # replicated across every case and misreported as case-level precision.
    if float(mean_pair_delta) < config.minimum_mean_pair_delta:
        failed.append("primary_mean_pair_delta")
    if positive_pairs < config.minimum_strictly_positive_pairs:
        failed.append("strictly_positive_pairs")
    if float(pair_randomization_p) > config.maximum_exact_pair_randomization_p:
        failed.append("exact_pair_randomization_p")
    if pair_interval[0] <= 0:
        failed.append("pair_interval_lower_bound")
    if median_pair_delta <= 0:
        failed.append("median_pair_delta")
    if float(mean_faulty_case_delta) < config.minimum_mean_faulty_case_delta:
        failed.append("faulty_mean_case_delta")
    if positive_faulty_cases < config.minimum_strictly_positive_faulty_cases:
        failed.append("strictly_positive_faulty_cases")
    if float(faulty_case_randomization_p) > config.maximum_exact_faulty_case_randomization_p:
        failed.append("exact_faulty_case_randomization_p")
    if faulty_case_interval[0] <= 0:
        failed.append("faulty_case_interval_lower_bound")
    if median_faulty_case_delta <= 0:
        failed.append("median_faulty_case_delta")
    if float(mean_faulty_pair_delta) < config.minimum_mean_faulty_pair_delta:
        failed.append("faulty_mean_pair_delta")
    if positive_faulty_pairs < config.minimum_strictly_positive_faulty_pairs:
        failed.append("strictly_positive_faulty_pairs")
    if float(faulty_pair_randomization_p) > config.maximum_exact_faulty_pair_randomization_p:
        failed.append("exact_faulty_pair_randomization_p")
    if faulty_pair_interval[0] <= 0:
        failed.append("faulty_pair_interval_lower_bound")
    if median_faulty_pair_delta <= 0:
        failed.append("median_faulty_pair_delta")
    if float(clean_specificity_loss) > config.maximum_clean_specificity_loss:
        failed.append("clean_specificity_loss")
    if float(worst_stratum_regression) > config.maximum_faulty_populated_stratum_regression:
        failed.append("faulty_populated_stratum_regression")
    if insufficient_strata:
        failed.append("insufficient_stratum_support")
    if float(minimum_populated_stratum_rate) < config.minimum_difficulty_stratum_brain_off_success:
        failed.append("populated_stratum_difficulty_floor")
    if float(maximum_populated_stratum_rate) > config.maximum_difficulty_stratum_brain_off_success:
        failed.append("populated_stratum_difficulty_ceiling")
    if (
        float(minimum_faulty_populated_stratum_rate)
        < config.minimum_faulty_stratum_brain_off_success
    ):
        failed.append("faulty_populated_stratum_difficulty_floor")
    if (
        float(maximum_faulty_populated_stratum_rate)
        > config.maximum_faulty_stratum_brain_off_success
    ):
        failed.append("faulty_populated_stratum_difficulty_ceiling")
    if insufficient_difficulties:
        failed.append("insufficient_difficulty_stratum_support")
    if float(minimum_difficulty_rate) < config.minimum_difficulty_stratum_brain_off_success:
        failed.append("difficulty_stratum_floor")
    if float(maximum_difficulty_rate) > config.maximum_difficulty_stratum_brain_off_success:
        failed.append("difficulty_stratum_ceiling")
    if len(plan.cases) < config.minimum_claim_case_count:
        failed.append("claim_case_universe")
    if too_hard:
        failed.append("benchmark_floor")
    if too_easy:
        failed.append("benchmark_ceiling")
    if faulty_too_hard:
        failed.append("faulty_benchmark_floor")
    if faulty_too_easy:
        failed.append("faulty_benchmark_ceiling")

    faulty_counts = plan.faulty_counts_by_pair
    case_count = len(plan.cases)

    def variant_rate(condition: Condition, variant: Variant) -> float:
        return float(
            _rate(
                variant_successes[(condition, variant)],
                variant_totals[(condition, variant)],
            )
        )

    return V2AggregateSummary(
        design_commitment_digest=plan.commitment.digest,
        plan_digest=plan.digest,
        gate_config_digest=config.digest,
        result_universe_digest=result_universe_digest,
        brain_on_success=float(on_rate),
        brain_off_success=float(off_rate),
        mean_case_delta=float(mean_case_delta),
        median_case_delta=float(median_case_delta),
        exact_case_randomization_p=float(case_randomization_p),
        case_interval=(float(case_interval[0]), float(case_interval[1])),
        strictly_positive_cases=positive_cases,
        tied_cases=tied_cases,
        negative_cases=negative_cases,
        mean_pair_delta=float(mean_pair_delta),
        median_pair_delta=float(median_pair_delta),
        exact_pair_randomization_p=float(pair_randomization_p),
        pair_interval=(float(pair_interval[0]), float(pair_interval[1])),
        strictly_positive_pairs=positive_pairs,
        tied_pairs=tied_pairs,
        negative_pairs=negative_pairs,
        mean_faulty_case_delta=float(mean_faulty_case_delta),
        median_faulty_case_delta=float(median_faulty_case_delta),
        exact_faulty_case_randomization_p=float(faulty_case_randomization_p),
        faulty_case_interval=(
            float(faulty_case_interval[0]),
            float(faulty_case_interval[1]),
        ),
        strictly_positive_faulty_cases=positive_faulty_cases,
        tied_faulty_cases=tied_faulty_cases,
        negative_faulty_cases=negative_faulty_cases,
        mean_faulty_pair_delta=float(mean_faulty_pair_delta),
        median_faulty_pair_delta=float(median_faulty_pair_delta),
        exact_faulty_pair_randomization_p=float(faulty_pair_randomization_p),
        faulty_pair_interval=(
            float(faulty_pair_interval[0]),
            float(faulty_pair_interval[1]),
        ),
        strictly_positive_faulty_pairs=positive_faulty_pairs,
        tied_faulty_pairs=tied_faulty_pairs,
        negative_faulty_pairs=negative_faulty_pairs,
        clean_success_brain_on=float(clean_on),
        clean_success_brain_off=float(clean_off),
        faulty_success_brain_on=variant_rate(Condition.BRAIN_ON, Variant.FAULTY),
        faulty_success_brain_off=variant_rate(Condition.BRAIN_OFF, Variant.FAULTY),
        clean_specificity_loss=float(clean_specificity_loss),
        worst_faulty_populated_stratum_regression=float(worst_stratum_regression),
        adequately_populated_stratum_count=len(sufficiently_populated),
        insufficient_stratum_count=len(insufficient_strata),
        minimum_stratum_case_support=config.minimum_populated_stratum_cases,
        minimum_populated_stratum_brain_off_success_observed=float(minimum_populated_stratum_rate),
        maximum_populated_stratum_brain_off_success_observed=float(maximum_populated_stratum_rate),
        minimum_faulty_populated_stratum_brain_off_success_observed=float(
            minimum_faulty_populated_stratum_rate
        ),
        maximum_faulty_populated_stratum_brain_off_success_observed=float(
            maximum_faulty_populated_stratum_rate
        ),
        adequately_populated_difficulty_stratum_count=len(sufficiently_populated_difficulties),
        insufficient_difficulty_stratum_count=len(insufficient_difficulties),
        minimum_difficulty_stratum_case_support=(config.minimum_difficulty_stratum_cases),
        minimum_difficulty_stratum_brain_off_success_observed=float(minimum_difficulty_rate),
        maximum_difficulty_stratum_brain_off_success_observed=float(maximum_difficulty_rate),
        case_count=len(plan.cases),
        observation_count=len(supplied),
        pair_count=V2_PAIR_COUNT,
        minimum_faulty_prevalence=min(faulty_counts) / case_count,
        maximum_faulty_prevalence=max(faulty_counts) / case_count,
        distinct_faulty_prevalences=len(set(faulty_counts)),
        ceiling=ceiling,
        faulty_ceiling=faulty_ceiling,
        helped=not failed,
        failed_gates=tuple(failed),
    )


__all__ = [
    "V2_FAULTY_PREVALENCE_LOWER",
    "V2_FAULTY_PREVALENCE_UPPER",
    "V2_MAX_CASE_COUNT",
    "V2_MIN_CLAIM_CASE_COUNT",
    "V2_PAIR_COUNT",
    "V2ActiveControlPlan",
    "V2AggregateSummary",
    "V2BoundVerifier",
    "V2CaseSpec",
    "V2CeilingDiagnosis",
    "V2ContextCard",
    "V2DesignCommitment",
    "V2EvaluationPlan",
    "V2GateConfig",
    "V2HardCaseAdapter",
    "V2HardCaseBinding",
    "V2Outcome",
    "V2ReceiptAuthority",
    "V2RenderedView",
    "V2VerifierKind",
    "V2VerifierReceipt",
    "assignment_variant_v2",
    "build_v2_plan",
    "full_case_success_v2",
    "precommit_v2_design",
    "solver_answer_digest_v2",
    "summarize_v2",
]
