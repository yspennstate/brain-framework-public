"""Causal binding between calibration preregistration and scored public inputs.

``calibration_plan_v3`` commits opaque candidate digests without knowing how a
generator renders a case.  ``calibration_v2`` scores rendered cases after a
brain-off outcome has been collected.  This module is the narrow bridge between
those boundaries: it defines the exact outcome-free rendering digests, builds the
ticket-sorted manifest, and proves that the later outcome-bearing cases are the
same prompt/trace/coordinate records.

The pre-outcome type intentionally has no response, prediction, correctness, or
oracle receipt field.  Digests prove byte identity, not chronology or semantic
independence; external timestamping and separately controlled verifier replay are
still required.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from .calibration_plan_v3 import (
    MAX_CALIBRATION_CASES,
    MAX_VERIFIER_IMPLEMENTATIONS,
    CalibrationPlan,
    ManifestRow,
    MaterializedCaseBinding,
    SelectionManifest,
    preregistration_commitment,
    selection_ticket,
    validate_materialization,
)
from .calibration_v2 import (
    DEFAULT_CONFIG,
    CalibrationCase,
    CalibrationConfig,
    calibration_input_document,
    case_set_digest,
)
from .models import canonical_json

SOLVER_VIEW_SCHEMA = "org.math-research-brain/calibration-solver-view"
SOLVER_VIEW_SCHEMA_VERSION = 1
OUTCOME_FREE_CASE_SCHEMA = "org.math-research-brain/calibration-outcome-free-case"
OUTCOME_FREE_CASE_SCHEMA_VERSION = 2
OUTCOME_BINDING_SCHEMA = "org.math-research-brain/calibration-outcome-binding"
OUTCOME_BINDING_SCHEMA_VERSION = 2

MAX_LABEL_BYTES = 512
MAX_CASE_ID_BYTES = 512
MAX_PROMPT_BYTES = 4_096
MAX_TRACE_LINE_BYTES = 2_048
MAX_TRACE_LINES = 12
MAX_ELIGIBLE_LINES = 10
MAX_VERIFIER_ID_BYTES = 256
MAX_OUTCOME_BINDING_JSON_BYTES = 2_000_000


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _text(
    value: Any,
    label: str,
    *,
    max_bytes: int,
    trimmed: bool = True,
) -> str:
    if type(value) is not str or not value or not value.strip():
        raise ValueError(f"{label} must be a nonempty plain string")
    if trimmed and value != value.strip():
        raise ValueError(f"{label} must be whitespace-trimmed")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{label} must use Unicode NFC normalization")
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    return value


def _integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label, max_bytes=71)
    if len(text) != 71 or not text.startswith("sha256:"):
        raise ValueError(f"{label} must be a lowercase sha256: digest")
    suffix = text[7:]
    if suffix != suffix.lower():
        raise ValueError(f"{label} must be a lowercase sha256: digest")
    try:
        bytes.fromhex(suffix)
    except ValueError as exc:
        raise ValueError(f"{label} must be a lowercase sha256: digest") from exc
    return text


def _bounded_tuple(values: Iterable[Any], *, limit: int, label: str) -> tuple[Any, ...]:
    if type(values) in (str, bytes):
        raise ValueError(f"{label} must be an iterable of records, not text")
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{label} must be iterable") from exc
    result = tuple(islice(iterator, limit + 1))
    if len(result) > limit:
        raise ValueError(f"{label} exceeds the {limit}-item budget")
    return result


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{label} must be an array")
    return value


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not all(type(key) is str for key in value):
        raise ValueError(f"{label} keys must be plain strings")
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"invalid {label} keys; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_canonical_json(raw: str, *, label: str, max_bytes: int) -> Any:
    if type(raw) is not str:
        raise ValueError(f"{label} must be text")
    try:
        size = len(raw.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if size > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} is not valid bounded JSON") from exc
    if raw != canonical_json(parsed):
        raise ValueError(f"{label} must use exact canonical UTF-8 JSON bytes")
    return parsed


def _exact_plan(plan: Any) -> CalibrationPlan:
    if type(plan) is not CalibrationPlan:
        raise ValueError("protocol requires an exact CalibrationPlan")
    rebuilt = CalibrationPlan.from_dict(plan.to_dict())
    if rebuilt != plan or not hmac.compare_digest(rebuilt.digest(), plan.digest()):
        raise ValueError("calibration plan is not its canonical immutable value")
    return rebuilt


def _exact_manifest(manifest: Any, plan: CalibrationPlan) -> SelectionManifest:
    if type(manifest) is not SelectionManifest:
        raise ValueError("protocol requires an exact SelectionManifest")
    rebuilt = SelectionManifest.from_dict(manifest.to_dict())
    if rebuilt != manifest or not hmac.compare_digest(rebuilt.digest(), manifest.digest()):
        raise ValueError("selection manifest is not its canonical immutable value")
    rebuilt.validate_against(plan)
    return rebuilt


@dataclass(frozen=True, slots=True)
class PreregistrationCandidate:
    """Complete rendered case before any solver or verifier outcome is read."""

    stable_case_id: str
    generator_counter: int
    family_index: int
    family_label: str
    template_index: int
    template_label: str
    layout_index: int
    layout_label: str
    domain_index: int
    domain_label: str
    fault_index: int
    fault_label: str
    generator_cell_index: int
    generator_cell_label: str
    template_fingerprint: str
    generator_cell_signature: str
    verifier_implementation_digests: tuple[str, ...]
    verifier_identities: tuple[VerifierIdentityBinding, ...]
    executed_verifier_implementation_digests: tuple[str, ...]
    semantic_program_digest: str
    prompt: str
    faulty_trace: tuple[str, ...]
    eligible_fault_lines: tuple[int, ...]
    fault_line: int

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if type(self) is not PreregistrationCandidate:
            raise ValueError("preregistration candidate subclasses are forbidden")
        _text(
            self.stable_case_id,
            "stable case id",
            max_bytes=MAX_CASE_ID_BYTES,
        )
        for name in (
            "generator_counter",
            "family_index",
            "template_index",
            "layout_index",
            "domain_index",
            "fault_index",
            "generator_cell_index",
        ):
            if _integer(getattr(self, name), name.replace("_", " ")) < 0:
                raise ValueError(f"{name.replace('_', ' ')} must be non-negative")
        for name in (
            "family_label",
            "template_label",
            "layout_label",
            "domain_label",
            "fault_label",
            "generator_cell_label",
        ):
            _text(getattr(self, name), name.replace("_", " "), max_bytes=MAX_LABEL_BYTES)
        _sha256(self.template_fingerprint, "template fingerprint")
        _sha256(self.generator_cell_signature, "generator-cell signature")
        _sha256(self.semantic_program_digest, "semantic program digest")

        verifiers = self.verifier_implementation_digests
        if type(verifiers) is not tuple or not 2 <= len(verifiers) <= MAX_VERIFIER_IMPLEMENTATIONS:
            raise ValueError("candidate verifier implementation digests have invalid cardinality")
        for digest in verifiers:
            _sha256(digest, "verifier implementation digest")
        if tuple(sorted(set(verifiers))) != verifiers:
            raise ValueError("candidate verifier implementation digests must be sorted and unique")
        identities = self.verifier_identities
        if type(identities) is not tuple or len(identities) != len(verifiers):
            raise ValueError(
                "candidate verifier identities must cover every reviewed implementation"
            )
        if not all(type(item) is VerifierIdentityBinding for item in identities):
            raise ValueError("candidate verifier identities must be exact binding records")
        rebuilt_identities = tuple(
            VerifierIdentityBinding.from_dict(item.to_dict()) for item in identities
        )
        if rebuilt_identities != identities:
            raise ValueError("candidate verifier identities are not canonical immutable values")
        if tuple(sorted(identities, key=lambda item: item.implementation_digest)) != identities:
            raise ValueError(
                "candidate verifier identities must be ordered by implementation digest"
            )
        if tuple(item.implementation_digest for item in identities) != verifiers:
            raise ValueError("candidate verifier identities do not match reviewed implementations")
        if len({item.verifier_id for item in identities}) != len(identities):
            raise ValueError("candidate verifier ids must be unique")

        executed = self.executed_verifier_implementation_digests
        if type(executed) is not tuple or not 2 <= len(executed) <= len(verifiers):
            raise ValueError("candidate must preregister at least two executed verifiers")
        for digest in executed:
            _sha256(digest, "executed verifier implementation digest")
        if tuple(sorted(set(executed))) != executed:
            raise ValueError("executed verifier implementation digests must be sorted and unique")
        if not set(executed).issubset(verifiers):
            raise ValueError("executed verifier implementations are outside the reviewed plan set")

        _text(self.prompt, "solver prompt", max_bytes=MAX_PROMPT_BYTES, trimmed=False)
        trace = self.faulty_trace
        if type(trace) is not tuple or not 6 <= len(trace) <= MAX_TRACE_LINES:
            raise ValueError("candidate faulty trace must contain between 6 and 12 lines")
        for index, line in enumerate(trace, 1):
            _text(
                line,
                f"candidate trace line {index}",
                max_bytes=MAX_TRACE_LINE_BYTES,
                trimmed=False,
            )

        lines = self.eligible_fault_lines
        if type(lines) is not tuple or not 4 <= len(lines) <= MAX_ELIGIBLE_LINES:
            raise ValueError("candidate eligible lines must contain between 4 and 10 entries")
        for line in lines:
            value = _integer(line, "eligible fault line")
            if not 2 <= value < len(trace):
                raise ValueError("candidate eligible fault lines must be interior trace lines")
        if tuple(sorted(set(lines))) != lines:
            raise ValueError("candidate eligible fault lines must be sorted and unique")
        fault_line = _integer(self.fault_line, "fault line")
        if fault_line not in lines:
            raise ValueError("candidate fault line must be declared eligible")

    @property
    def fault_rank(self) -> int:
        self._validate()
        return self.eligible_fault_lines.index(self.fault_line)

    def solver_view_payload(self) -> dict[str, Any]:
        """Return the only candidate fields that may be sent to a blind solver."""

        self._validate()
        return {
            "eligible_fault_lines": list(self.eligible_fault_lines),
            "faulty_trace": list(self.faulty_trace),
            "prompt": self.prompt,
            "schema": SOLVER_VIEW_SCHEMA,
            "schema_version": SOLVER_VIEW_SCHEMA_VERSION,
            "stable_case_id": self.stable_case_id,
        }

    def solver_view_digest(self) -> str:
        return _digest(self.solver_view_payload())

    def outcome_free_payload(self) -> dict[str, Any]:
        """Return complete evaluator-side content with no observed solver outcome."""

        self._validate()
        return {
            "domain_index": self.domain_index,
            "domain_label": self.domain_label,
            "eligible_fault_lines": list(self.eligible_fault_lines),
            "executed_verifier_implementation_digests": list(
                self.executed_verifier_implementation_digests
            ),
            "family_index": self.family_index,
            "family_label": self.family_label,
            "fault_index": self.fault_index,
            "fault_label": self.fault_label,
            "fault_line": self.fault_line,
            "generator_cell_index": self.generator_cell_index,
            "generator_cell_label": self.generator_cell_label,
            "generator_cell_signature": self.generator_cell_signature,
            "generator_counter": self.generator_counter,
            "layout_index": self.layout_index,
            "layout_label": self.layout_label,
            "schema": OUTCOME_FREE_CASE_SCHEMA,
            "schema_version": OUTCOME_FREE_CASE_SCHEMA_VERSION,
            "semantic_program_digest": self.semantic_program_digest,
            "solver_view_digest": self.solver_view_digest(),
            "stable_case_id": self.stable_case_id,
            "template_fingerprint": self.template_fingerprint,
            "template_index": self.template_index,
            "template_label": self.template_label,
            "verifier_implementation_digests": list(self.verifier_implementation_digests),
            "verifier_identities": [item.to_dict() for item in self.verifier_identities],
        }

    def outcome_free_content_digest(self) -> str:
        return _digest(self.outcome_free_payload())


@dataclass(frozen=True, slots=True)
class PreregisteredCorpus:
    """Manifest-ordered outcome-free corpus and its public commitment."""

    manifest: SelectionManifest
    materialized_bindings: tuple[MaterializedCaseBinding, ...]
    candidates: tuple[PreregistrationCandidate, ...]
    commitment: str

    def validate_against(self, plan: CalibrationPlan) -> None:
        canonical_plan = _exact_plan(plan)
        canonical_manifest = _exact_manifest(self.manifest, canonical_plan)
        if type(self.materialized_bindings) is not tuple or type(self.candidates) is not tuple:
            raise ValueError("preregistered corpus collections must be exact tuples")
        if len(self.candidates) != canonical_plan.case_count:
            raise ValueError("preregistered candidate count does not match plan")
        if len(self.materialized_bindings) != canonical_plan.case_count:
            raise ValueError("materialized binding count does not match plan")
        if not all(type(case) is MaterializedCaseBinding for case in self.materialized_bindings):
            raise ValueError("corpus contains a noncanonical materialized binding")
        if not all(type(case) is PreregistrationCandidate for case in self.candidates):
            raise ValueError("corpus contains a noncanonical preregistration candidate")
        validate_materialization(
            canonical_plan,
            canonical_manifest,
            self.materialized_bindings,
        )
        for row, binding, candidate in zip(
            canonical_manifest.rows,
            self.materialized_bindings,
            self.candidates,
            strict=True,
        ):
            candidate._validate()
            _validate_candidate_against_plan(candidate, canonical_plan)
            if binding.stable_case_id != candidate.stable_case_id:
                raise ValueError("candidate order does not match materialized manifest order")
            expected = _materialized_binding(row.case_index, row.selection_ticket, candidate)
            if expected != binding:
                raise ValueError("materialized binding does not match exact rendered candidate")
        identity_maps = {candidate.verifier_identities for candidate in self.candidates}
        if len(identity_maps) != 1:
            raise ValueError(
                "preregistered corpus must use one verifier identity mapping for every case"
            )
        expected_commitment = preregistration_commitment(
            canonical_plan,
            canonical_manifest,
            canonical_manifest.seed,
        )
        if not hmac.compare_digest(_sha256(self.commitment, "commitment"), expected_commitment):
            raise ValueError("preregistered corpus commitment does not match plan and manifest")


def _validate_candidate_against_plan(
    candidate: PreregistrationCandidate,
    plan: CalibrationPlan,
) -> None:
    candidate._validate()
    bounds = {
        "family": (candidate.family_index, plan.axis_counts.family),
        "template": (candidate.template_index, plan.axis_counts.template),
        "layout": (candidate.layout_index, plan.axis_counts.layout),
        "domain": (candidate.domain_index, plan.axis_counts.domain),
        "fault": (candidate.fault_index, plan.axis_counts.fault),
        "generator cell": (
            candidate.generator_cell_index,
            plan.axis_counts.generator_cell,
        ),
    }
    for label, (index, upper) in bounds.items():
        if index >= upper:
            raise ValueError(f"candidate {label} index is outside plan bounds")
    if len(candidate.eligible_fault_lines) not in plan.eligible_arities:
        raise ValueError("candidate eligible arity is not preregistered")
    if not hmac.compare_digest(
        candidate.template_fingerprint,
        plan.position_blind_template_fingerprints[candidate.template_index],
    ):
        raise ValueError("candidate template fingerprint does not match plan")
    if not hmac.compare_digest(
        candidate.generator_cell_signature,
        plan.generator_cell_signatures[candidate.generator_cell_index],
    ):
        raise ValueError("candidate generator-cell signature does not match plan")
    if candidate.verifier_implementation_digests != plan.verifier_implementation_digests:
        raise ValueError("candidate verifier implementation digests do not match plan")


def _materialized_binding(
    case_index: int,
    ticket: str,
    candidate: PreregistrationCandidate,
) -> MaterializedCaseBinding:
    return MaterializedCaseBinding(
        case_index=case_index,
        stable_case_id=candidate.stable_case_id,
        generator_counter=candidate.generator_counter,
        outcome_free_content_digest=candidate.outcome_free_content_digest(),
        semantic_program_digest=candidate.semantic_program_digest,
        solver_view_digest=candidate.solver_view_digest(),
        selection_ticket=ticket,
        family_label=candidate.family_label,
        template_label=candidate.template_label,
        layout_label=candidate.layout_label,
        domain_label=candidate.domain_label,
        fault_label=candidate.fault_label,
        generator_cell_label=candidate.generator_cell_label,
        template_fingerprint=candidate.template_fingerprint,
        generator_cell_signature=candidate.generator_cell_signature,
        verifier_implementation_digests=candidate.verifier_implementation_digests,
        eligible_fault_lines=candidate.eligible_fault_lines,
        fault_line=candidate.fault_line,
    )


def build_preregistered_corpus(
    plan: CalibrationPlan,
    seed: str,
    candidates: Iterable[PreregistrationCandidate],
) -> PreregisteredCorpus:
    """Ticket, order, materialize, and validate an outcome-free candidate corpus."""

    canonical_plan = _exact_plan(plan)
    values = _bounded_tuple(
        candidates,
        limit=MAX_CALIBRATION_CASES,
        label="preregistration candidates",
    )
    if len(values) != canonical_plan.case_count:
        raise ValueError("candidate count does not match calibration plan")
    if not all(type(candidate) is PreregistrationCandidate for candidate in values):
        raise ValueError("preregistration accepts only exact PreregistrationCandidate values")
    for candidate in values:
        _validate_candidate_against_plan(candidate, canonical_plan)
    counters = tuple(candidate.generator_counter for candidate in values)
    if set(counters) != set(range(canonical_plan.case_count)):
        raise ValueError("candidate counters must be the complete canonical range")

    ticketed = []
    for candidate in values:
        ticket = selection_ticket(
            canonical_plan,
            seed,
            generator_counter=candidate.generator_counter,
            stable_case_id=candidate.stable_case_id,
            outcome_free_content_digest=candidate.outcome_free_content_digest(),
            semantic_program_digest=candidate.semantic_program_digest,
            solver_view_digest=candidate.solver_view_digest(),
        )
        ticketed.append((ticket, candidate))
    ticketed.sort(key=lambda pair: pair[0])

    rows: list[ManifestRow] = []
    bindings: list[MaterializedCaseBinding] = []
    ordered_candidates: list[PreregistrationCandidate] = []
    for case_index, (ticket, candidate) in enumerate(ticketed):
        binding = _materialized_binding(case_index, ticket, candidate)
        bindings.append(binding)
        ordered_candidates.append(candidate)
        rows.append(
            ManifestRow(
                case_index=case_index,
                coordinate_digest=binding.coordinate_digest(),
                stable_case_id=candidate.stable_case_id,
                generator_counter=candidate.generator_counter,
                outcome_free_content_digest=candidate.outcome_free_content_digest(),
                semantic_program_digest=candidate.semantic_program_digest,
                solver_view_digest=candidate.solver_view_digest(),
                selection_ticket=ticket,
                family_index=candidate.family_index,
                template_index=candidate.template_index,
                layout_index=candidate.layout_index,
                domain_index=candidate.domain_index,
                fault_index=candidate.fault_index,
                generator_cell_index=candidate.generator_cell_index,
                eligible_arity=len(candidate.eligible_fault_lines),
                fault_rank=candidate.fault_rank,
            )
        )
    manifest = SelectionManifest.build(canonical_plan, seed, rows)
    corpus = PreregisteredCorpus(
        manifest=manifest,
        materialized_bindings=tuple(bindings),
        candidates=tuple(ordered_candidates),
        commitment=preregistration_commitment(canonical_plan, manifest, seed),
    )
    corpus.validate_against(canonical_plan)
    return corpus


@dataclass(frozen=True, slots=True)
class VerifierIdentityBinding:
    """Reviewed implementation digest paired with its v2 receipt identifier."""

    implementation_digest: str
    verifier_id: str

    def __post_init__(self) -> None:
        if type(self) is not VerifierIdentityBinding:
            raise ValueError("verifier identity binding subclasses are forbidden")
        _sha256(self.implementation_digest, "verifier implementation digest")
        _text(self.verifier_id, "verifier id", max_bytes=MAX_VERIFIER_ID_BYTES)

    def to_dict(self) -> dict[str, str]:
        return {
            "implementation_digest": self.implementation_digest,
            "verifier_id": self.verifier_id,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> VerifierIdentityBinding:
        value = _mapping(raw, "verifier identity binding")
        expected = {"implementation_digest", "verifier_id"}
        _strict_keys(value, expected, "verifier identity binding")
        return cls(
            implementation_digest=value["implementation_digest"],
            verifier_id=value["verifier_id"],
        )


@dataclass(frozen=True, slots=True)
class CalibrationOutcomeLink:
    case_index: int
    stable_case_id: str
    selection_ticket: str
    calibration_case_digest: str

    def __post_init__(self) -> None:
        if type(self) is not CalibrationOutcomeLink:
            raise ValueError("calibration outcome link subclasses are forbidden")
        if _integer(self.case_index, "case index") < 0:
            raise ValueError("case index must be non-negative")
        _text(self.stable_case_id, "stable case id", max_bytes=MAX_CASE_ID_BYTES)
        _sha256(self.selection_ticket, "selection ticket")
        _sha256(self.calibration_case_digest, "calibration case digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibration_case_digest": self.calibration_case_digest,
            "case_index": self.case_index,
            "selection_ticket": self.selection_ticket,
            "stable_case_id": self.stable_case_id,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> CalibrationOutcomeLink:
        value = _mapping(raw, "calibration outcome link")
        expected = {
            "calibration_case_digest",
            "case_index",
            "selection_ticket",
            "stable_case_id",
        }
        _strict_keys(value, expected, "calibration outcome link")
        return cls(**{name: value[name] for name in expected})


@dataclass(frozen=True, slots=True)
class CalibrationOutcomeBinding:
    """Post-outcome artifact binding one v2 input to one v3 commitment."""

    plan_digest: str
    manifest_digest: str
    preregistration_commitment: str
    materialization_digest: str
    calibration_config_digest: str
    calibration_case_set_digest: str
    calibration_input_digest: str
    verifier_identities: tuple[VerifierIdentityBinding, ...]
    case_links: tuple[CalibrationOutcomeLink, ...]
    schema: str = OUTCOME_BINDING_SCHEMA
    schema_version: int = OUTCOME_BINDING_SCHEMA_VERSION
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not CalibrationOutcomeBinding:
            raise ValueError("calibration outcome binding subclasses are forbidden")
        if self.schema != OUTCOME_BINDING_SCHEMA:
            raise ValueError("unsupported calibration outcome binding schema")
        if (
            _integer(self.schema_version, "outcome binding schema version")
            != OUTCOME_BINDING_SCHEMA_VERSION
        ):
            raise ValueError("unsupported calibration outcome binding schema version")
        for name in (
            "plan_digest",
            "manifest_digest",
            "preregistration_commitment",
            "materialization_digest",
            "calibration_config_digest",
            "calibration_case_set_digest",
            "calibration_input_digest",
        ):
            _sha256(getattr(self, name), name.replace("_", " "))
        identities = self.verifier_identities
        if (
            type(identities) is not tuple
            or not 2 <= len(identities) <= MAX_VERIFIER_IMPLEMENTATIONS
        ):
            raise ValueError("outcome binding has invalid verifier identity cardinality")
        if not all(type(item) is VerifierIdentityBinding for item in identities):
            raise ValueError("outcome binding requires exact verifier identity records")
        rebuilt_identities = tuple(
            VerifierIdentityBinding.from_dict(item.to_dict()) for item in identities
        )
        if rebuilt_identities != identities:
            raise ValueError("outcome binding verifier identities are not canonical values")
        if tuple(sorted(identities, key=lambda item: item.implementation_digest)) != identities:
            raise ValueError("verifier identities must be ordered by implementation digest")
        if len({item.implementation_digest for item in identities}) != len(identities):
            raise ValueError("verifier implementation digests must be unique")
        if len({item.verifier_id for item in identities}) != len(identities):
            raise ValueError("verifier ids must be unique")
        links = self.case_links
        if type(links) is not tuple or not 1 <= len(links) <= MAX_CALIBRATION_CASES:
            raise ValueError("outcome binding has invalid case-link cardinality")
        if not all(type(link) is CalibrationOutcomeLink for link in links):
            raise ValueError("outcome binding requires exact calibration outcome links")
        rebuilt_links = tuple(CalibrationOutcomeLink.from_dict(link.to_dict()) for link in links)
        if rebuilt_links != links:
            raise ValueError("outcome binding case links are not canonical values")
        if tuple(link.case_index for link in links) != tuple(range(len(links))):
            raise ValueError("outcome links must have contiguous manifest indices")
        if len({link.stable_case_id for link in links}) != len(links):
            raise ValueError("outcome link case ids must be unique")
        if len({link.calibration_case_digest for link in links}) != len(links):
            raise ValueError("outcome link case digests must be unique")
        object.__setattr__(self, "content_digest", _digest(self.content_payload()))

    def content_payload(self) -> dict[str, Any]:
        return {
            "calibration_case_set_digest": self.calibration_case_set_digest,
            "calibration_config_digest": self.calibration_config_digest,
            "calibration_input_digest": self.calibration_input_digest,
            "case_links": [link.to_dict() for link in self.case_links],
            "manifest_digest": self.manifest_digest,
            "materialization_digest": self.materialization_digest,
            "plan_digest": self.plan_digest,
            "preregistration_commitment": self.preregistration_commitment,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "verifier_identities": [item.to_dict() for item in self.verifier_identities],
        }

    def to_dict(self) -> dict[str, Any]:
        rebuilt_identities = tuple(
            VerifierIdentityBinding.from_dict(item.to_dict()) for item in self.verifier_identities
        )
        rebuilt_links = tuple(
            CalibrationOutcomeLink.from_dict(link.to_dict()) for link in self.case_links
        )
        if rebuilt_identities != self.verifier_identities or rebuilt_links != self.case_links:
            raise ValueError("outcome binding nested records are not canonical values")
        payload = self.content_payload()
        if not hmac.compare_digest(_digest(payload), self.content_digest):
            raise ValueError("outcome binding content changed after construction")
        return {**payload, "content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> CalibrationOutcomeBinding:
        value = _mapping(raw, "calibration outcome binding")
        expected = {
            "calibration_case_set_digest",
            "calibration_config_digest",
            "calibration_input_digest",
            "case_links",
            "content_digest",
            "manifest_digest",
            "materialization_digest",
            "plan_digest",
            "preregistration_commitment",
            "schema",
            "schema_version",
            "verifier_identities",
        }
        _strict_keys(value, expected, "calibration outcome binding")
        raw_identities = _list(value["verifier_identities"], "verifier identities")
        if len(raw_identities) > MAX_VERIFIER_IMPLEMENTATIONS:
            raise ValueError("outcome binding contains too many verifier identities")
        raw_links = _list(value["case_links"], "calibration outcome links")
        if len(raw_links) > MAX_CALIBRATION_CASES:
            raise ValueError("outcome binding contains too many case links")
        binding = cls(
            plan_digest=value["plan_digest"],
            manifest_digest=value["manifest_digest"],
            preregistration_commitment=value["preregistration_commitment"],
            materialization_digest=value["materialization_digest"],
            calibration_config_digest=value["calibration_config_digest"],
            calibration_case_set_digest=value["calibration_case_set_digest"],
            calibration_input_digest=value["calibration_input_digest"],
            verifier_identities=tuple(
                VerifierIdentityBinding.from_dict(item) for item in raw_identities
            ),
            case_links=tuple(CalibrationOutcomeLink.from_dict(item) for item in raw_links),
            schema=value["schema"],
            schema_version=value["schema_version"],
        )
        supplied = _sha256(value["content_digest"], "outcome binding content digest")
        if not hmac.compare_digest(supplied, binding.content_digest):
            raise ValueError("outcome binding content digest does not match canonical content")
        return binding

    @classmethod
    def from_json(cls, raw: str) -> CalibrationOutcomeBinding:
        return cls.from_dict(
            _load_canonical_json(
                raw,
                label="calibration outcome binding",
                max_bytes=MAX_OUTCOME_BINDING_JSON_BYTES,
            )
        )


def _materialization_digest(bindings: tuple[MaterializedCaseBinding, ...]) -> str:
    return _digest(
        {
            "coordinate_payloads": [binding.coordinate_payload() for binding in bindings],
            "schema": "org.math-research-brain/calibration-materialization-set",
            "schema_version": 1,
        }
    )


def _exact_calibration_case(case: Any) -> CalibrationCase:
    if type(case) is not CalibrationCase:
        raise ValueError("outcome binding accepts only exact CalibrationCase values")
    rebuilt = CalibrationCase.from_dict(case.to_dict())
    if rebuilt != case or not hmac.compare_digest(rebuilt.content_digest, case.content_digest):
        raise ValueError("calibration case is not its canonical immutable value")
    return rebuilt


def _validate_outcome_case(
    candidate: PreregistrationCandidate,
    case: CalibrationCase,
) -> None:
    expected = {
        "case_id": candidate.stable_case_id,
        "family": candidate.family_label,
        "template_id": candidate.template_label,
        "generator_cell": candidate.generator_cell_label,
        "domain": candidate.domain_label,
        "fault_kind": candidate.fault_label,
        "prompt": candidate.prompt,
        "faulty_trace": candidate.faulty_trace,
        "eligible_fault_lines": candidate.eligible_fault_lines,
        "fault_line": candidate.fault_line,
    }
    for field_name, expected_value in expected.items():
        if getattr(case, field_name) != expected_value:
            raise ValueError(
                f"calibration {field_name.replace('_', ' ')} does not match preregistration"
            )
    identity_by_digest = {
        identity.implementation_digest: identity.verifier_id
        for identity in candidate.verifier_identities
    }
    expected_verifier_ids = tuple(
        sorted(
            identity_by_digest[digest]
            for digest in candidate.executed_verifier_implementation_digests
        )
    )
    receipt_ids = tuple(sorted(receipt.verifier_id for receipt in case.oracles))
    if receipt_ids != expected_verifier_ids:
        raise ValueError("calibration oracle ids do not match reviewed verifier identities")


def bind_calibration_outcomes(
    plan: CalibrationPlan,
    corpus: PreregisteredCorpus,
    cases: Iterable[CalibrationCase],
    verifier_identities: Iterable[VerifierIdentityBinding],
    *,
    config: CalibrationConfig = DEFAULT_CONFIG,
) -> CalibrationOutcomeBinding:
    """Bind scored v2 cases to exact pre-outcome renderings and the v3 commitment."""

    canonical_plan = _exact_plan(plan)
    if type(corpus) is not PreregisteredCorpus:
        raise ValueError("outcome binding requires an exact PreregisteredCorpus")
    corpus.validate_against(canonical_plan)
    if type(config) is not CalibrationConfig:
        raise ValueError("outcome binding requires an exact CalibrationConfig")
    rebuilt_config = CalibrationConfig.from_dict(config.to_dict())
    if rebuilt_config != config or not hmac.compare_digest(
        config.digest(), DEFAULT_CONFIG.digest()
    ):
        raise ValueError("outcome binding requires the canonical sealed calibration config")

    identities = _bounded_tuple(
        verifier_identities,
        limit=MAX_VERIFIER_IMPLEMENTATIONS,
        label="verifier identity bindings",
    )
    if not all(type(item) is VerifierIdentityBinding for item in identities):
        raise ValueError("verifier identities must be exact VerifierIdentityBinding values")
    rebuilt_identities = tuple(
        VerifierIdentityBinding.from_dict(item.to_dict()) for item in identities
    )
    if rebuilt_identities != identities:
        raise ValueError("verifier identities are not canonical immutable values")
    identities = tuple(sorted(rebuilt_identities, key=lambda item: item.implementation_digest))
    if tuple(item.implementation_digest for item in identities) != (
        canonical_plan.verifier_implementation_digests
    ):
        raise ValueError("verifier identities do not cover the exact preregistered implementations")
    if len({item.verifier_id for item in identities}) != len(identities):
        raise ValueError("verifier ids must be unique")
    outcome_values = _bounded_tuple(
        cases,
        limit=MAX_CALIBRATION_CASES,
        label="calibration outcome cases",
    )
    if len(outcome_values) != canonical_plan.case_count:
        raise ValueError("outcome case count does not match preregistered plan")
    canonical_cases = tuple(_exact_calibration_case(case) for case in outcome_values)
    by_id = {case.case_id: case for case in canonical_cases}
    if len(by_id) != len(canonical_cases):
        raise ValueError("calibration outcome case ids must be unique")

    links: list[CalibrationOutcomeLink] = []
    for row, candidate in zip(corpus.manifest.rows, corpus.candidates, strict=True):
        if candidate.verifier_identities != identities:
            raise ValueError(
                "post-outcome verifier identity mapping does not match preregistration"
            )
        try:
            case = by_id[candidate.stable_case_id]
        except KeyError as exc:
            raise ValueError("preregistered case is absent from calibration outcomes") from exc
        _validate_outcome_case(candidate, case)
        links.append(
            CalibrationOutcomeLink(
                case_index=row.case_index,
                stable_case_id=row.stable_case_id,
                selection_ticket=row.selection_ticket,
                calibration_case_digest=case.content_digest,
            )
        )
    if set(by_id) != {candidate.stable_case_id for candidate in corpus.candidates}:
        raise ValueError("calibration outcomes contain an unpreregistered case")

    input_document = calibration_input_document(canonical_cases, rebuilt_config)
    input_digest = _sha256(input_document["content_digest"], "calibration input digest")
    return CalibrationOutcomeBinding(
        plan_digest=canonical_plan.digest(),
        manifest_digest=corpus.manifest.digest(),
        preregistration_commitment=corpus.commitment,
        materialization_digest=_materialization_digest(corpus.materialized_bindings),
        calibration_config_digest=rebuilt_config.digest(),
        calibration_case_set_digest=case_set_digest(canonical_cases),
        calibration_input_digest=input_digest,
        verifier_identities=identities,
        case_links=tuple(links),
    )


def verify_calibration_outcome_binding(
    binding: CalibrationOutcomeBinding,
    plan: CalibrationPlan,
    corpus: PreregisteredCorpus,
    cases: Iterable[CalibrationCase],
    verifier_identities: Iterable[VerifierIdentityBinding],
    *,
    config: CalibrationConfig = DEFAULT_CONFIG,
) -> None:
    """Strictly reload and recompute a published post-outcome binding."""

    if type(binding) is not CalibrationOutcomeBinding:
        raise ValueError("verification requires an exact CalibrationOutcomeBinding")
    reloaded = CalibrationOutcomeBinding.from_dict(binding.to_dict())
    if reloaded != binding:
        raise ValueError("outcome binding is not its canonical immutable value")
    expected = bind_calibration_outcomes(
        plan,
        corpus,
        cases,
        verifier_identities,
        config=config,
    )
    if expected != reloaded or not hmac.compare_digest(
        expected.content_digest,
        reloaded.content_digest,
    ):
        raise ValueError("outcome binding does not match deterministic recomputation")


__all__ = [
    "OUTCOME_BINDING_SCHEMA",
    "OUTCOME_FREE_CASE_SCHEMA",
    "SOLVER_VIEW_SCHEMA",
    "CalibrationOutcomeBinding",
    "CalibrationOutcomeLink",
    "PreregisteredCorpus",
    "PreregistrationCandidate",
    "VerifierIdentityBinding",
    "bind_calibration_outcomes",
    "build_preregistered_corpus",
    "verify_calibration_outcome_binding",
]
