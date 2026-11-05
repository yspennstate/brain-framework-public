"""Sealed, paired brain-on/brain-off evaluation primitives.

This module deliberately treats solver output, runtime metadata, and verifier reports as
untrusted records.  A benefit decision can be made only from a complete pre-registered design;
timeouts, malformed responses, and missing responses remain in its denominator.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import tomllib
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any

from .benchmark import (
    SOLVER_VALID_FAULTS,
    BenchmarkItem,
    Solution,
    SolverView,
    certify_item,
    evaluate_expression,
    replay_repair,
    validate_solution,
)
from .challenge_bank import (
    ChallengeCase,
    TraceVariant as ChallengeTraceVariant,
    assignment_variant as challenge_assignment_variant,
    certify_case,
    opaque_case_id,
    opaque_view_digest,
)
from .codex_runner import (
    DEFAULT_BASE_INSTRUCTIONS,
    DEFAULT_DEVELOPER_INSTRUCTIONS,
    CodexRunResult,
    ExecutableProvenance,
    app_server_command_tail_policy_digest,
    solver_process_isolation_policy_digest,
)
from .models import canonical_json


RUN_SPEC_SCHEMA = "org.math-research-brain/evaluation-run-spec"
RUN_SPEC_SCHEMA_VERSION = 3
RESPONSE_SCHEMA = "org.math-research-brain/evaluation-batch-response"
RESPONSE_SCHEMA_VERSION = 1
RECEIPT_SCHEMA = "org.math-research-brain/verifier-receipt"
RECEIPT_SCHEMA_VERSION = 1
EXECUTABLE_IMAGE_BINDING_POLICY = "windows_deny_write_delete_lease_v1"
REQUIRE_STANDARD_POPEN = True

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_CONFIG_TEXT_BYTES = 16_384
_MAX_CONTEXT_BYTES = 1_000_000
_MAX_CASES = 10_000


def _utf8(value: str, label: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8 text") from exc


def _text(
    value: Any,
    label: str,
    *,
    max_bytes: int = _MAX_CONFIG_TEXT_BYTES,
    allow_empty: bool = False,
    allow_layout_controls: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError(f"{label} must use NFC-normalized Unicode")
    encoded = _utf8(value, label)
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds the {max_bytes}-byte budget")
    if not allow_empty and not value.strip():
        raise ValueError(f"{label} must not be empty")
    for character in value:
        category = unicodedata.category(character)
        if allow_layout_controls and character in "\n\r\t":
            continue
        if category in {"Cc", "Cf", "Cs"}:
            raise ValueError(f"{label} contains a control, bidi-format, or surrogate character")
    return value


def _integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _digest(value: Any, label: str = "digest") -> str:
    if not isinstance(value, str) or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be exactly sha256: followed by 64 lowercase hex digits")
    return value


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_text(value: str, label: str) -> str:
    return _digest_bytes(_utf8(value, label))


def _digest_json(value: Any) -> str:
    return _digest_text(canonical_json(value), "canonical JSON")


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} keys must be strings")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label} keys differ; missing={missing}, extra={extra}")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


class Condition(StrEnum):
    BRAIN_ON = "brain_on"
    BRAIN_OFF = "brain_off"


class Variant(StrEnum):
    CLEAN = "clean"
    FAULTY = "faulty"


class Verdict(StrEnum):
    VALID = "valid"
    INVALID = "invalid"


class AttemptStatus(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    MALFORMED = "malformed"
    TOOL_VIOLATION = "tool_violation"
    MISSING = "missing"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class IdentityStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"


class IsolationClassification(StrEnum):
    INSTRUCTION_BLINDED_EVENT_AUDITED = "instruction_blinded_event_audited"


class RetrievalDisposition(StrEnum):
    PACKET = "packet"
    ABSTAINED = "abstained"
    EMPTY_SHAM = "empty_sham"


class VerifierKind(StrEnum):
    REPAIR = "repair"
    WITNESS = "witness"


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    balanced_validity: float
    localization: float
    repair: float
    witness: float
    error_family: float

    def __post_init__(self) -> None:
        values = (
            self.balanced_validity,
            self.localization,
            self.repair,
            self.witness,
            self.error_family,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("score weights must be finite and nonnegative")
        if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("score weights must sum to exactly one within 1e-12")

    def to_dict(self) -> dict[str, float]:
        return {
            "balanced_validity": self.balanced_validity,
            "error_family": self.error_family,
            "localization": self.localization,
            "repair": self.repair,
            "witness": self.witness,
        }


@dataclass(frozen=True, slots=True)
class BenefitGateConfig:
    minimum_primary_delta: float
    minimum_nonnegative_pairs: int
    maximum_clean_specificity_loss: float
    maximum_domain_tier_regression: float
    maximum_randomization_p: float

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_primary_delta <= 1:
            raise ValueError("minimum primary delta must be in [0, 1]")
        _integer(self.minimum_nonnegative_pairs, "minimum nonnegative pairs", minimum=1)
        if not 0 <= self.maximum_clean_specificity_loss <= 1:
            raise ValueError("maximum clean specificity loss must be in [0, 1]")
        if not 0 <= self.maximum_domain_tier_regression <= 1:
            raise ValueError("maximum tier regression must be in [0, 1]")
        if not 0 < self.maximum_randomization_p <= 1:
            raise ValueError("maximum randomization p must be in (0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "maximum_clean_specificity_loss": self.maximum_clean_specificity_loss,
            "maximum_domain_tier_regression": self.maximum_domain_tier_regression,
            "maximum_randomization_p": self.maximum_randomization_p,
            "minimum_nonnegative_pairs": self.minimum_nonnegative_pairs,
            "minimum_primary_delta": self.minimum_primary_delta,
        }


@dataclass(frozen=True, slots=True)
class InferenceConfig:
    bootstrap_repetitions: int
    confidence_level: float
    randomization_alternative: str
    maximum_exact_pairs: int

    def __post_init__(self) -> None:
        _integer(self.bootstrap_repetitions, "bootstrap repetitions", minimum=100)
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence level must be in (0, 1)")
        if self.randomization_alternative != "greater":
            raise ValueError("the pre-registered randomization alternative must be 'greater'")
        _integer(self.maximum_exact_pairs, "maximum exact pairs", minimum=10)
        if self.maximum_exact_pairs > 20:
            raise ValueError("exact sign-flip enumeration is capped at 20 pairs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bootstrap_repetitions": self.bootstrap_repetitions,
            "confidence_level": self.confidence_level,
            "maximum_exact_pairs": self.maximum_exact_pairs,
            "randomization_alternative": self.randomization_alternative,
        }


# A configuration may lower the solver-response budgets but not lift them past these (scheduled audit
# B17-R79-EVALUATION-RESPONSE-LIMITS-UNBOUNDED-20260929: only depth had a ceiling). The shipped
# configurations use 512 KiB, 12,000 nodes, 16 KiB strings and 10**12.
RESPONSE_BYTES_CEILING = 8 * 1024 * 1024
RESPONSE_NODES_CEILING = 1_000_000
RESPONSE_INTEGER_CEILING = 10**18


@dataclass(frozen=True, slots=True)
class ResponseLimits:
    maximum_bytes: int
    maximum_depth: int
    maximum_nodes: int
    maximum_string_bytes: int
    maximum_integer_abs: int

    def __post_init__(self) -> None:
        _integer(self.maximum_bytes, "maximum response bytes", minimum=1)
        _integer(self.maximum_depth, "maximum response depth", minimum=2)
        _integer(self.maximum_nodes, "maximum response nodes", minimum=10)
        _integer(self.maximum_string_bytes, "maximum response string bytes", minimum=1)
        _integer(self.maximum_integer_abs, "maximum response integer magnitude", minimum=1)
        if self.maximum_depth > 64:
            raise ValueError("maximum response depth may not exceed 64")
        if self.maximum_bytes > RESPONSE_BYTES_CEILING:
            raise ValueError(f"maximum response bytes may not exceed {RESPONSE_BYTES_CEILING}")
        if self.maximum_nodes > RESPONSE_NODES_CEILING:
            raise ValueError(f"maximum response nodes may not exceed {RESPONSE_NODES_CEILING}")
        if self.maximum_integer_abs > RESPONSE_INTEGER_CEILING:
            raise ValueError("maximum response integer magnitude may not exceed 10**18")
        # a string or a node takes at least one byte of the response
        if self.maximum_string_bytes > self.maximum_bytes:
            raise ValueError("maximum response string bytes may not exceed maximum response bytes")
        if self.maximum_nodes > self.maximum_bytes:
            raise ValueError("maximum response nodes may not exceed maximum response bytes")

    def to_dict(self) -> dict[str, int]:
        return {
            "maximum_bytes": self.maximum_bytes,
            "maximum_depth": self.maximum_depth,
            "maximum_integer_abs": self.maximum_integer_abs,
            "maximum_nodes": self.maximum_nodes,
            "maximum_string_bytes": self.maximum_string_bytes,
        }


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    requested_model: str
    requested_provider: str
    requested_reasoning_effort: str
    require_accepted_config: bool
    require_served_identity: bool
    allow_provider_model_fallback: bool
    executable_provenance: ExecutableProvenance | None = None

    def __post_init__(self) -> None:
        _text(self.requested_model, "requested model", max_bytes=128)
        _text(self.requested_provider, "requested provider", max_bytes=128)
        _text(self.requested_reasoning_effort, "requested reasoning effort", max_bytes=64)
        if not isinstance(self.require_accepted_config, bool):
            raise ValueError("require_accepted_config must be a boolean")
        if not isinstance(self.require_served_identity, bool):
            raise ValueError("require_served_identity must be a boolean")
        if not self.require_accepted_config or not self.require_served_identity:
            raise ValueError("benefit claims require accepted config and served identity evidence")
        if not isinstance(self.allow_provider_model_fallback, bool):
            raise ValueError("allow_provider_model_fallback must be a boolean")
        if self.allow_provider_model_fallback:
            raise ValueError("provider model fallback must be disabled")
        if self.executable_provenance is not None and type(
            self.executable_provenance
        ) is not ExecutableProvenance:
            raise ValueError("runtime executable provenance must be exact and immutable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_provider_model_fallback": self.allow_provider_model_fallback,
            "executable_provenance": (
                self.executable_provenance.to_dict()
                if self.executable_provenance is not None
                else None
            ),
            "requested_model": self.requested_model,
            "requested_provider": self.requested_provider,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "require_accepted_config": self.require_accepted_config,
            "require_served_identity": self.require_served_identity,
        }


@dataclass(frozen=True, slots=True)
class IsolationPolicy:
    classification: IsolationClassification
    require_complete_event_audit: bool
    maximum_tool_calls: int
    maximum_followups: int
    maximum_policy_violations: int

    def __post_init__(self) -> None:
        if self.classification is not IsolationClassification.INSTRUCTION_BLINDED_EVENT_AUDITED:
            raise ValueError("only the honest instruction-blinded/event-audited label is allowed")
        if self.require_complete_event_audit is not True:
            raise ValueError("a complete event audit is required")
        for label, value in (
            ("maximum tool calls", self.maximum_tool_calls),
            ("maximum followups", self.maximum_followups),
            ("maximum policy violations", self.maximum_policy_violations),
        ):
            if _integer(value, label, minimum=0) != 0:
                raise ValueError(f"{label} must be zero")

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "maximum_followups": self.maximum_followups,
            "maximum_policy_violations": self.maximum_policy_violations,
            "maximum_tool_calls": self.maximum_tool_calls,
            "require_complete_event_audit": self.require_complete_event_audit,
        }


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    schema_version: int
    design_mode: str
    pair_count: int
    timeout_seconds: int
    max_output_tokens: int
    network_allowed: bool
    brain_writable: bool
    cross_case_memory: bool
    sham_retrieval_for_brain_off: bool
    invalid_probability_threshold: float
    tie_rule: str
    weights: ScoreWeights
    benefit_gate: BenefitGateConfig
    inference: InferenceConfig
    response_limits: ResponseLimits
    runtime: RuntimePolicy
    isolation: IsolationPolicy

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ValueError("evaluation config schema_version must be 2")
        if self.design_mode != "full":
            raise ValueError("only the full paired design may support a benefit claim")
        _integer(self.pair_count, "pair count", minimum=10)
        if self.pair_count % 2:
            raise ValueError("pair count must be even for clean/faulty rotation")
        if self.pair_count > self.inference.maximum_exact_pairs:
            raise ValueError("pair count exceeds exact randomization-test budget")
        _integer(self.timeout_seconds, "timeout seconds", minimum=1)
        _integer(self.max_output_tokens, "max output tokens", minimum=1)
        for label, value in (
            ("network_allowed", self.network_allowed),
            ("brain_writable", self.brain_writable),
            ("cross_case_memory", self.cross_case_memory),
            ("sham_retrieval_for_brain_off", self.sham_retrieval_for_brain_off),
        ):
            if not isinstance(value, bool):
                raise ValueError(f"{label} must be a boolean")
        if self.network_allowed or self.brain_writable or self.cross_case_memory:
            raise ValueError("evaluation isolation controls must remain disabled")
        if not self.sham_retrieval_for_brain_off:
            raise ValueError("brain-off must retain the pre-registered empty sham retrieval")
        if not 0 < self.invalid_probability_threshold < 1:
            raise ValueError("invalid probability threshold must be in (0, 1)")
        if self.tie_rule != "invalid_at_or_above":
            raise ValueError("tie_rule must be invalid_at_or_above")
        if self.benefit_gate.minimum_nonnegative_pairs > self.pair_count:
            raise ValueError("minimum nonnegative pairs exceeds pair count")

    def to_dict(self) -> dict[str, Any]:
        return {
            "benefit_gate": self.benefit_gate.to_dict(),
            "brain_writable": self.brain_writable,
            "cross_case_memory": self.cross_case_memory,
            "design_mode": self.design_mode,
            "inference": self.inference.to_dict(),
            "invalid_probability_threshold": self.invalid_probability_threshold,
            "isolation": self.isolation.to_dict(),
            "max_output_tokens": self.max_output_tokens,
            "network_allowed": self.network_allowed,
            "pair_count": self.pair_count,
            "response_limits": self.response_limits.to_dict(),
            "runtime": self.runtime.to_dict(),
            "schema_version": self.schema_version,
            "sham_retrieval_for_brain_off": self.sham_retrieval_for_brain_off,
            "tie_rule": self.tie_rule,
            "timeout_seconds": self.timeout_seconds,
            "weights": self.weights.to_dict(),
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.to_dict())

    @property
    def scorer_digest(self) -> str:
        return _digest_json(
            {
                "tie_rule": self.tie_rule,
                "invalid_probability_threshold": self.invalid_probability_threshold,
                "weights": self.weights.to_dict(),
            }
        )

    @classmethod
    def from_toml(cls, path: str | Path) -> EvaluationConfig:
        source = Path(path)
        try:
            raw = tomllib.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"invalid evaluation config: {exc}") from exc
        value = _mapping(raw, "evaluation config")
        _strict_keys(
            value,
            {
                "benefit_gate",
                "brain_writable",
                "cross_case_memory",
                "design_mode",
                "inference",
                "invalid_probability_threshold",
                "isolation",
                "max_output_tokens",
                "network_allowed",
                "pair_count",
                "primary_score_weights",
                "response_limits",
                "runtime",
                "schema_version",
                "sham_retrieval_for_brain_off",
                "tie_rule",
                "timeout_seconds",
            },
            "evaluation config",
        )
        weights = _mapping(value["primary_score_weights"], "primary score weights")
        _strict_keys(
            weights,
            {
                "balanced_validity_accuracy",
                "error_family",
                "exact_first_error_localization",
                "verified_repair",
                "verified_witness",
            },
            "primary score weights",
        )
        gate = _mapping(value["benefit_gate"], "benefit gate")
        _strict_keys(
            gate,
            {
                "maximum_clean_specificity_loss",
                "maximum_domain_tier_regression",
                "maximum_randomization_p",
                "minimum_nonnegative_pairs",
                "minimum_primary_delta",
            },
            "benefit gate",
        )
        inference = _mapping(value["inference"], "inference")
        _strict_keys(
            inference,
            {
                "bootstrap_repetitions",
                "confidence_level",
                "maximum_exact_pairs",
                "randomization_alternative",
            },
            "inference",
        )
        limits = _mapping(value["response_limits"], "response limits")
        _strict_keys(
            limits,
            {
                "maximum_bytes",
                "maximum_depth",
                "maximum_integer_abs",
                "maximum_nodes",
                "maximum_string_bytes",
            },
            "response limits",
        )
        runtime = _mapping(value["runtime"], "runtime")
        runtime_keys = {
            "allow_provider_model_fallback",
            "requested_model",
            "requested_provider",
            "requested_reasoning_effort",
            "require_accepted_config",
            "require_served_identity",
        }
        actual_runtime_keys = frozenset(runtime)
        allowed_runtime_keys = {
            frozenset(runtime_keys),
            frozenset(runtime_keys | {"executable_provenance"}),
        }
        if actual_runtime_keys not in allowed_runtime_keys:
            missing = sorted(runtime_keys - set(actual_runtime_keys))
            extra = sorted(
                set(actual_runtime_keys) - runtime_keys - {"executable_provenance"}
            )
            raise ValueError(f"runtime keys differ; missing={missing}, extra={extra}")
        executable_provenance = None
        if "executable_provenance" in runtime:
            executable = _mapping(
                runtime["executable_provenance"],
                "runtime executable provenance",
            )
            _strict_keys(
                executable,
                {"canonical_path", "sha256_digest", "size_bytes"},
                "runtime executable provenance",
            )
            executable_provenance = ExecutableProvenance(
                canonical_path=_text(
                    executable["canonical_path"],
                    "runtime executable canonical path",
                    max_bytes=4_096,
                ),
                size_bytes=_integer(
                    executable["size_bytes"],
                    "runtime executable size",
                    minimum=1,
                ),
                sha256_digest=_digest(
                    executable["sha256_digest"],
                    "runtime executable digest",
                ),
            )
        isolation = _mapping(value["isolation"], "isolation")
        _strict_keys(
            isolation,
            {
                "classification",
                "maximum_followups",
                "maximum_policy_violations",
                "maximum_tool_calls",
                "require_complete_event_audit",
            },
            "isolation",
        )
        try:
            classification = IsolationClassification(isolation["classification"])
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported isolation classification") from exc
        return cls(
            schema_version=_integer(value["schema_version"], "schema version"),
            design_mode=_text(value["design_mode"], "design mode", max_bytes=32),
            pair_count=_integer(value["pair_count"], "pair count"),
            timeout_seconds=_integer(value["timeout_seconds"], "timeout seconds"),
            max_output_tokens=_integer(value["max_output_tokens"], "max output tokens"),
            network_allowed=value["network_allowed"],
            brain_writable=value["brain_writable"],
            cross_case_memory=value["cross_case_memory"],
            sham_retrieval_for_brain_off=value["sham_retrieval_for_brain_off"],
            invalid_probability_threshold=_number(
                value["invalid_probability_threshold"], "invalid probability threshold"
            ),
            tie_rule=_text(value["tie_rule"], "tie rule", max_bytes=64),
            weights=ScoreWeights(
                balanced_validity=_number(
                    weights["balanced_validity_accuracy"], "balanced validity weight"
                ),
                localization=_number(
                    weights["exact_first_error_localization"], "localization weight"
                ),
                repair=_number(weights["verified_repair"], "repair weight"),
                witness=_number(weights["verified_witness"], "witness weight"),
                error_family=_number(weights["error_family"], "error-family weight"),
            ),
            benefit_gate=BenefitGateConfig(
                minimum_primary_delta=_number(
                    gate["minimum_primary_delta"], "minimum primary delta"
                ),
                minimum_nonnegative_pairs=_integer(
                    gate["minimum_nonnegative_pairs"], "minimum nonnegative pairs"
                ),
                maximum_clean_specificity_loss=_number(
                    gate["maximum_clean_specificity_loss"],
                    "maximum clean specificity loss",
                ),
                maximum_domain_tier_regression=_number(
                    gate["maximum_domain_tier_regression"],
                    "maximum domain tier regression",
                ),
                maximum_randomization_p=_number(
                    gate["maximum_randomization_p"], "maximum randomization p"
                ),
            ),
            inference=InferenceConfig(
                bootstrap_repetitions=_integer(
                    inference["bootstrap_repetitions"], "bootstrap repetitions"
                ),
                confidence_level=_number(
                    inference["confidence_level"], "confidence level"
                ),
                randomization_alternative=_text(
                    inference["randomization_alternative"],
                    "randomization alternative",
                    max_bytes=32,
                ),
                maximum_exact_pairs=_integer(
                    inference["maximum_exact_pairs"], "maximum exact pairs"
                ),
            ),
            response_limits=ResponseLimits(
                maximum_bytes=_integer(limits["maximum_bytes"], "maximum bytes"),
                maximum_depth=_integer(limits["maximum_depth"], "maximum depth"),
                maximum_nodes=_integer(limits["maximum_nodes"], "maximum nodes"),
                maximum_string_bytes=_integer(
                    limits["maximum_string_bytes"], "maximum string bytes"
                ),
                maximum_integer_abs=_integer(
                    limits["maximum_integer_abs"], "maximum integer magnitude"
                ),
            ),
            runtime=RuntimePolicy(
                requested_model=_text(
                    runtime["requested_model"], "requested model", max_bytes=128
                ),
                requested_provider=_text(
                    runtime["requested_provider"], "requested provider", max_bytes=128
                ),
                requested_reasoning_effort=_text(
                    runtime["requested_reasoning_effort"],
                    "requested reasoning effort",
                    max_bytes=64,
                ),
                require_accepted_config=runtime["require_accepted_config"],
                require_served_identity=runtime["require_served_identity"],
                allow_provider_model_fallback=runtime["allow_provider_model_fallback"],
                executable_provenance=executable_provenance,
            ),
            isolation=IsolationPolicy(
                classification=classification,
                require_complete_event_audit=isolation["require_complete_event_audit"],
                maximum_tool_calls=_integer(
                    isolation["maximum_tool_calls"], "maximum tool calls"
                ),
                maximum_followups=_integer(
                    isolation["maximum_followups"], "maximum followups"
                ),
                maximum_policy_violations=_integer(
                    isolation["maximum_policy_violations"],
                    "maximum policy violations",
                ),
            ),
        )


def _expected_codex_request_policy(config: EvaluationConfig) -> dict[str, Any]:
    return {
        "base_instructions": DEFAULT_BASE_INSTRUCTIONS,
        "developer_instructions": DEFAULT_DEVELOPER_INSTRUCTIONS,
        "fixed_thread_policy": {
            "allow_provider_model_fallback": False,
            "approval_policy": "never",
            "dynamic_tools": [],
            "environments": [],
            "ephemeral": True,
            "experimental_raw_events": True,
            "sandbox": "read-only",
        },
        "fixed_turn_policy": {
            "approval_policy": "never",
            "environments": [],
        },
        "requested_model": config.runtime.requested_model,
        "requested_provider": config.runtime.requested_provider,
        "requested_reasoning_effort": config.runtime.requested_reasoning_effort,
        "timeout_seconds": config.timeout_seconds,
    }


def _sealed_runtime_policy(config: EvaluationConfig) -> dict[str, Any]:
    return {
        "codex_launch_policy": {
            "command_tail_policy_digest": app_server_command_tail_policy_digest(
                config.runtime.requested_model,
                config.runtime.requested_reasoning_effort,
            ),
            "executable_image_binding_policy": EXECUTABLE_IMAGE_BINDING_POLICY,
            "executable_provenance": (
                config.runtime.executable_provenance.to_dict()
                if config.runtime.executable_provenance is not None
                else None
            ),
            "require_standard_popen": REQUIRE_STANDARD_POPEN,
            "solver_process_isolation_policy_digest": (
                solver_process_isolation_policy_digest()
            ),
        },
        "codex_request_policy": _expected_codex_request_policy(config),
        "evaluation_runtime": config.runtime.to_dict(),
        "isolation": config.isolation.to_dict(),
        "max_output_tokens": config.max_output_tokens,
        "max_output_tokens_transport_enforced": False,
    }


class HashSampler:
    """Version-independent SHA-256 counter sampler with rejection sampling."""

    def __init__(self, seed: bytes) -> None:
        if not isinstance(seed, bytes) or not seed:
            raise ValueError("sampler seed must be nonempty bytes")
        self.seed = seed
        self.counter = 0

    def _word(self) -> int:
        block = hashlib.sha256(self.seed + self.counter.to_bytes(16, "big")).digest()
        self.counter += 1
        return int.from_bytes(block[:8], "big")

    def below(self, upper: int) -> int:
        if isinstance(upper, bool) or not isinstance(upper, int) or upper <= 0:
            raise ValueError("upper bound must be a positive integer")
        limit = (1 << 64) - ((1 << 64) % upper)
        while True:
            word = self._word()
            if word < limit:
                return word % upper


def _derived_seed(seed: str, label: str) -> bytes:
    return hashlib.sha256(
        b"math-brain-evaluation-seed-v2\x00"
        + _utf8(seed, "design seed")
        + b"\x00"
        + _utf8(label, "seed label")
    ).digest()


def _permutation(values: Sequence[Any], *, seed: str, label: str) -> tuple[Any, ...]:
    result = list(values)
    sampler = HashSampler(_derived_seed(seed, label))
    for index in range(len(result) - 1, 0, -1):
        target = sampler.below(index + 1)
        result[index], result[target] = result[target], result[index]
    return tuple(result)


@dataclass(frozen=True, slots=True)
class DesignSeeds:
    order: str
    variant: str
    pair: str
    condition: str
    bootstrap: str

    def __post_init__(self) -> None:
        values = (self.order, self.variant, self.pair, self.condition, self.bootstrap)
        for label, value in zip(
            ("order", "variant", "pair", "condition", "bootstrap"), values, strict=True
        ):
            _text(value, f"{label} seed", max_bytes=1024)
        if len(set(values)) != len(values):
            raise ValueError("design seeds must be separately derived values")

    def commitments(self) -> SeedCommitments:
        return SeedCommitments(
            order=_digest_bytes(_derived_seed(self.order, "commitment")),
            variant=_digest_bytes(_derived_seed(self.variant, "commitment")),
            pair=_digest_bytes(_derived_seed(self.pair, "commitment")),
            condition=_digest_bytes(_derived_seed(self.condition, "commitment")),
            bootstrap=_digest_bytes(_derived_seed(self.bootstrap, "commitment")),
        )


@dataclass(frozen=True, slots=True)
class SeedCommitments:
    order: str
    variant: str
    pair: str
    condition: str
    bootstrap: str

    def __post_init__(self) -> None:
        for label, value in self.to_dict().items():
            _digest(value, f"{label} seed commitment")

    def to_dict(self) -> dict[str, str]:
        return {
            "bootstrap": self.bootstrap,
            "condition": self.condition,
            "order": self.order,
            "pair": self.pair,
            "variant": self.variant,
        }


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    item: BenchmarkItem | ChallengeCase
    domain: str
    difficulty: str
    runtime_salt: bytes | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _text(self.domain, "case domain", max_bytes=128)
        _text(self.difficulty, "case difficulty", max_bytes=128)
        if type(self.item) is BenchmarkItem:
            if self.runtime_salt is not None:
                raise ValueError("benchmark evaluation cases cannot carry a runtime salt")
        elif type(self.item) is ChallengeCase:
            if not isinstance(self.runtime_salt, bytes) or len(self.runtime_salt) < 16:
                raise ValueError("challenge evaluation cases require a private runtime salt")
            if self.domain != self.item.domain or self.difficulty != self.item.difficulty.value:
                raise ValueError("challenge evaluation strata must match the sealed case")
            # Exercise the exact opaque boundary during construction.
            opaque_case_id(self.item, self.runtime_salt)
        else:
            raise ValueError(
                "evaluation cases require exact BenchmarkItem or ChallengeCase values"
            )

    @classmethod
    def from_challenge(cls, item: ChallengeCase, *, runtime_salt: bytes) -> EvaluationCase:
        if type(item) is not ChallengeCase:
            raise ValueError("challenge evaluation factory requires an exact ChallengeCase")
        return cls(
            item=item,
            domain=item.domain,
            difficulty=item.difficulty.value,
            runtime_salt=runtime_salt,
        )

    @property
    def case_id(self) -> str:
        if type(self.item) is BenchmarkItem:
            return self.item.item_id
        assert self.runtime_salt is not None
        return opaque_case_id(self.item, self.runtime_salt)

    @property
    def item_digest(self) -> str:
        if type(self.item) is BenchmarkItem:
            return self.item.digest()
        assert self.runtime_salt is not None
        return _digest_bytes(
            b"math-brain/evaluation/challenge-item/v1\x00"
            + len(self.runtime_salt).to_bytes(8, "big")
            + self.runtime_salt
            + self.item.digest().encode("ascii")
        )

    @property
    def family(self) -> str:
        return (
            self.item.generation.family
            if type(self.item) is BenchmarkItem
            else self.item.family
        )

    def certify(self) -> None:
        if type(self.item) is BenchmarkItem:
            certify_item(self.item)
        else:
            certify_case(self.item)

    def solver_view(self, variant: Variant) -> SolverView:
        use_faulty = variant is Variant.FAULTY
        if type(self.item) is BenchmarkItem:
            return BenchmarkItem.solver_view(self.item, use_faulty=use_faulty)
        return ChallengeCase.solver_view(self.item, use_faulty=use_faulty)

    def solver_view_digest(self, view: SolverView) -> str:
        if type(self.item) is BenchmarkItem:
            return _digest_json(view.to_dict())
        assert self.runtime_salt is not None
        return opaque_view_digest(self.item, view, self.runtime_salt)

    @property
    def fault_line(self) -> int:
        return (
            self.item.fault.step_number
            if type(self.item) is BenchmarkItem
            else self.item.fault_line
        )

    @property
    def error_family(self) -> str:
        return (
            self.item.fault.kind.value
            if type(self.item) is BenchmarkItem
            else self.item.error_family
        )

    def universe_record(self) -> dict[str, str]:
        return {
            "case_id": self.case_id,
            "difficulty": self.difficulty,
            "domain": self.domain,
            "family": self.family,
            "item_digest": self.item_digest,
        }


@dataclass(frozen=True, slots=True)
class RetrievalPacket:
    """The exact pre-dispatch context packet or a pre-dispatch abstention."""

    case_id: str
    packet: str | None
    abstention_reason: str | None

    def __post_init__(self) -> None:
        _text(self.case_id, "retrieval-packet case id", max_bytes=128)
        if self.packet is None:
            _text(
                self.abstention_reason,
                "retrieval abstention reason",
                max_bytes=4096,
                allow_layout_controls=True,
            )
        else:
            _text(
                self.packet,
                "retrieval packet",
                max_bytes=_MAX_CONTEXT_BYTES,
                allow_layout_controls=True,
            )
            if self.abstention_reason is not None:
                raise ValueError("a retrieval packet cannot also claim abstention")

    @property
    def disposition(self) -> RetrievalDisposition:
        return (
            RetrievalDisposition.ABSTAINED
            if self.packet is None
            else RetrievalDisposition.PACKET
        )

    @property
    def packet_digest(self) -> str | None:
        return None if self.packet is None else _digest_text(self.packet, "retrieval packet")

    @property
    def abstention_reason_digest(self) -> str | None:
        return (
            _digest_text(self.abstention_reason, "retrieval abstention reason")
            if self.abstention_reason is not None
            else None
        )

    def to_sealed_dict(self) -> dict[str, Any]:
        return {
            "abstention_reason_digest": self.abstention_reason_digest,
            "case_id": self.case_id,
            "disposition": self.disposition.value,
            "packet_digest": self.packet_digest,
        }


@dataclass(frozen=True, slots=True)
class PairDispatch:
    pair_id: int
    dispatch_index: int
    condition_order: tuple[Condition, Condition]

    def __post_init__(self) -> None:
        _integer(self.pair_id, "pair id", minimum=0)
        _integer(self.dispatch_index, "pair dispatch index", minimum=0)
        if len(self.condition_order) != 2 or set(self.condition_order) != set(Condition):
            raise ValueError("condition order must contain each condition exactly once")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_order": [condition.value for condition in self.condition_order],
            "dispatch_index": self.dispatch_index,
            "pair_id": self.pair_id,
        }


@dataclass(frozen=True, slots=True)
class Assignment:
    case_id: str
    case_index: int
    item_digest: str
    pair_id: int
    condition: Condition
    variant: Variant
    order_index: int
    domain: str
    difficulty: str
    family: str
    view: SolverView
    view_digest: str

    def __post_init__(self) -> None:
        _text(self.case_id, "case id", max_bytes=128)
        _integer(self.case_index, "case index", minimum=0)
        _digest(self.item_digest, "assignment item digest")
        _integer(self.pair_id, "assignment pair id", minimum=0)
        if not isinstance(self.condition, Condition) or not isinstance(self.variant, Variant):
            raise ValueError("assignment condition and variant must use declared enums")
        _integer(self.order_index, "assignment order index", minimum=0)
        _text(self.domain, "assignment domain", max_bytes=128)
        _text(self.difficulty, "assignment difficulty", max_bytes=128)
        _text(self.family, "assignment family", max_bytes=128)
        if type(self.view) is not SolverView:
            raise ValueError("assignment view must be an exact hardened SolverView")
        _digest(self.view_digest, "assignment view digest")

    def to_sealed_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_index": self.case_index,
            "condition": self.condition.value,
            "difficulty": self.difficulty,
            "domain": self.domain,
            "family": self.family,
            "item_digest": self.item_digest,
            "order_index": self.order_index,
            "pair_id": self.pair_id,
            "variant": self.variant.value,
            "view_digest": self.view_digest,
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.to_sealed_dict())


@dataclass(frozen=True, slots=True)
class RunSpec:
    design_case_ids: tuple[str, ...]
    required_pair_ids: tuple[int, ...]
    benchmark_manifest_digest: str
    design_universe_digest: str
    assignment_digest: str
    condition_free_view_digest: str
    solver_prompt_digest: str
    brain_on_context_digest: str
    brain_off_context_digest: str
    treatment_digest: str
    config_digest: str
    seed_commitments: SeedCommitments
    requested_model: str
    requested_provider: str
    requested_reasoning_effort: str
    runtime_policy_digest: str
    pair_dispatch_digest: str
    schema: str = RUN_SPEC_SCHEMA
    schema_version: int = RUN_SPEC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != RUN_SPEC_SCHEMA or self.schema_version != RUN_SPEC_SCHEMA_VERSION:
            raise ValueError("unsupported evaluation run-spec schema")
        if not self.design_case_ids or len(self.design_case_ids) > _MAX_CASES:
            raise ValueError("run spec requires between 1 and 10,000 cases")
        if len(set(self.design_case_ids)) != len(self.design_case_ids):
            raise ValueError("run-spec case ids must be unique")
        for case_id in self.design_case_ids:
            _text(case_id, "run-spec case id", max_bytes=128)
        if len(self.required_pair_ids) < 10:
            raise ValueError("run spec requires at least ten matched pairs")
        if self.required_pair_ids != tuple(range(len(self.required_pair_ids))):
            raise ValueError("required pair ids must be the sealed consecutive universe")
        for label, value in (
            ("benchmark manifest", self.benchmark_manifest_digest),
            ("design universe", self.design_universe_digest),
            ("assignment", self.assignment_digest),
            ("condition-free view", self.condition_free_view_digest),
            ("solver prompt", self.solver_prompt_digest),
            ("brain-on context", self.brain_on_context_digest),
            ("brain-off context", self.brain_off_context_digest),
            ("treatment", self.treatment_digest),
            ("config", self.config_digest),
            ("runtime policy", self.runtime_policy_digest),
            ("pair dispatch", self.pair_dispatch_digest),
        ):
            _digest(value, f"{label} digest")
        if not isinstance(self.seed_commitments, SeedCommitments):
            raise ValueError("run spec requires five separate seed commitments")
        _text(self.requested_model, "run-spec requested model", max_bytes=128)
        _text(self.requested_provider, "run-spec requested provider", max_bytes=128)
        _text(
            self.requested_reasoning_effort,
            "run-spec requested reasoning effort",
            max_bytes=64,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_digest": self.assignment_digest,
            "benchmark_manifest_digest": self.benchmark_manifest_digest,
            "brain_off_context_digest": self.brain_off_context_digest,
            "brain_on_context_digest": self.brain_on_context_digest,
            "condition_free_view_digest": self.condition_free_view_digest,
            "config_digest": self.config_digest,
            "design_case_ids": list(self.design_case_ids),
            "design_universe_digest": self.design_universe_digest,
            "pair_dispatch_digest": self.pair_dispatch_digest,
            "requested_model": self.requested_model,
            "requested_provider": self.requested_provider,
            "requested_reasoning_effort": self.requested_reasoning_effort,
            "required_pair_ids": list(self.required_pair_ids),
            "runtime_policy_digest": self.runtime_policy_digest,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "seed_commitments": self.seed_commitments.to_dict(),
            "solver_prompt_digest": self.solver_prompt_digest,
            "treatment_digest": self.treatment_digest,
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class SolverBatchCase:
    case_id: str
    view_digest: str
    view: SolverView
    payload_digest: str

    def __post_init__(self) -> None:
        _text(self.case_id, "solver batch case id", max_bytes=128)
        _digest(self.view_digest, "solver batch view digest")
        _digest(self.payload_digest, "solver batch payload digest")
        if type(self.view) is not SolverView:
            raise ValueError("solver batch case requires an exact SolverView")
        if not hmac.compare_digest(self.payload_digest, _digest_json(self.view.to_dict())):
            raise ValueError("solver batch payload digest does not match its payload")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "solver_view": self.view.to_dict(),
            "view_digest": self.view_digest,
        }


def _pack_digest(pair_id: int, cases: Sequence[SolverBatchCase]) -> str:
    return _digest_json(
        {
            "cases": [
                {
                    "case_id": case.case_id,
                    "order_index": index,
                    "view_digest": case.view_digest,
                }
                for index, case in enumerate(cases)
            ],
            "pair_id": pair_id,
        }
    )


def _case_context_digest(
    cases: Sequence[SolverBatchCase],
    contexts: Sequence[str],
) -> str:
    if len(cases) != len(contexts):
        raise ValueError("case-context digest requires aligned cases and contexts")
    return _digest_json(
        [
            {
                "case_id": case.case_id,
                "context_digest": _digest_text(context, "case context"),
                "view_digest": case.view_digest,
            }
            for case, context in zip(cases, contexts, strict=True)
        ]
    )


def _treatment_digest(
    condition: Condition,
    *,
    solver_prompt_digest: str,
    context_digest: str,
    config_digest: str,
) -> str:
    return _digest_json(
        {
            "condition": condition.value,
            "config_digest": config_digest,
            "context_digest": context_digest,
            "solver_prompt_digest": solver_prompt_digest,
        }
    )


def _dispatch_nonce(
    seed: str,
    *,
    run_spec_digest: str,
    pair_id: int,
    condition: Condition,
) -> str:
    return _digest_bytes(
        b"math-brain/evaluation/dispatch-nonce/v1\x00"
        + _derived_seed(
            seed,
            f"{run_spec_digest}:{pair_id}:{condition.value}",
        )
    )


@dataclass(frozen=True, slots=True)
class SolverBatch:
    pair_id: int
    condition: Condition
    cases: tuple[SolverBatchCase, ...]
    solver_prompt: str
    solver_prompt_digest: str
    config_digest: str
    case_contexts: tuple[str, ...]
    pack_digest: str
    context_digest: str
    treatment_digest: str
    run_spec_digest: str
    dispatch_nonce: str

    def __post_init__(self) -> None:
        _integer(self.pair_id, "solver batch pair id", minimum=0)
        if not isinstance(self.condition, Condition):
            raise ValueError("solver batch condition must be declared")
        if not self.cases:
            raise ValueError("solver batch must contain at least one case")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("solver batch case ids must be unique")
        _text(
            self.solver_prompt,
            "solver prompt",
            max_bytes=_MAX_CONTEXT_BYTES,
            allow_layout_controls=True,
        )
        _digest(self.solver_prompt_digest, "solver batch prompt digest")
        _digest(self.config_digest, "solver batch config digest")
        if self.solver_prompt_digest != _digest_text(self.solver_prompt, "solver prompt"):
            raise ValueError("solver batch prompt digest does not match its prompt")
        if len(self.case_contexts) != len(self.cases):
            raise ValueError("solver batch must bind one context to every ordered case")
        for context in self.case_contexts:
            _text(
                context,
                "case brain context",
                max_bytes=_MAX_CONTEXT_BYTES,
                allow_empty=True,
                allow_layout_controls=True,
            )
        _digest(self.pack_digest, "solver batch pack digest")
        _digest(self.context_digest, "solver batch context digest")
        _digest(self.treatment_digest, "solver batch treatment digest")
        _digest(self.run_spec_digest, "solver batch run-spec digest")
        _digest(self.dispatch_nonce, "solver batch dispatch nonce")
        expected_pack = _pack_digest(self.pair_id, self.cases)
        if not hmac.compare_digest(self.pack_digest, expected_pack):
            raise ValueError("solver batch pack digest does not match its ordered cases")
        expected_context = _case_context_digest(self.cases, self.case_contexts)
        if not hmac.compare_digest(self.context_digest, expected_context):
            raise ValueError("solver batch context digest does not match its ordered contexts")
        expected_treatment = _treatment_digest(
            self.condition,
            solver_prompt_digest=self.solver_prompt_digest,
            context_digest=self.context_digest,
            config_digest=self.config_digest,
        )
        if not hmac.compare_digest(self.treatment_digest, expected_treatment):
            raise ValueError("solver batch treatment digest does not match its sealed inputs")

    @property
    def digest(self) -> str:
        return _digest_json(
            {
                "pack_digest": self.pack_digest,
                "context_digest": self.context_digest,
                "config_digest": self.config_digest,
                "dispatch_nonce": self.dispatch_nonce,
                "pair_id": self.pair_id,
                "run_spec_digest": self.run_spec_digest,
                "solver_prompt_digest": self.solver_prompt_digest,
                "treatment_digest": self.treatment_digest,
            }
        )

    def to_solver_payload(self) -> dict[str, Any]:
        """Return the complete safe payload; it contains no variant or answer-key fields."""

        return {
            "batch_digest": self.digest,
            "cases": [
                {**case.to_dict(), "brain_context": context}
                for case, context in zip(self.cases, self.case_contexts, strict=True)
            ],
            "context_digest": self.context_digest,
            "dispatch_nonce": self.dispatch_nonce,
            "pack_digest": self.pack_digest,
            "response_schema": batch_output_schema(self),
            "run_spec_digest": self.run_spec_digest,
            "solver_prompt": self.solver_prompt,
            "solver_prompt_digest": self.solver_prompt_digest,
            "treatment_digest": self.treatment_digest,
        }


def batch_output_schema(batch: SolverBatch) -> dict[str, Any]:
    """Return the app-server ``outputSchema``; independent parsing is still mandatory."""

    if not isinstance(batch, SolverBatch):
        raise ValueError("output schema requires a sealed SolverBatch")
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    nullable_line = {
        "anyOf": [
            {"minimum": 1, "type": "integer"},
            {"type": "null"},
        ]
    }
    repair = {
        "anyOf": [
            {
                "additionalProperties": False,
                "properties": {"replacement_line": {"type": "string"}},
                "required": ["replacement_line"],
                "type": "object",
            },
            {"type": "null"},
        ]
    }
    bounded_integer = {
        "maximum": 10**12,
        "minimum": -(10**12),
        "type": "integer",
    }
    typed_payload = {
        "anyOf": [
            {"type": "boolean"},
            bounded_integer,
            {"type": "string"},
            {
                "items": bounded_integer,
                "maxItems": 256,
                "type": "array",
            },
            {
                "items": {"type": "string"},
                "maxItems": 256,
                "type": "array",
            },
            {
                "additionalProperties": False,
                "properties": {
                    "nullity": bounded_integer,
                    "rank": bounded_integer,
                },
                "required": ["nullity", "rank"],
                "type": "object",
            },
        ]
    }
    witness = {
        "anyOf": [
            {
                "additionalProperties": False,
                "properties": {
                    "left_value": {"type": "string"},
                    "right_value": {"type": "string"},
                },
                "required": ["left_value", "right_value"],
                "type": "object",
            },
            {
                "additionalProperties": False,
                "properties": {"typed_payload": typed_payload},
                "required": ["typed_payload"],
                "type": "object",
            },
            {"type": "null"},
        ]
    }
    prefix_items = []
    for case in batch.cases:
        prefix_items.append(
            {
                "additionalProperties": False,
                "properties": {
                    "case_id": {"const": case.case_id, "type": "string"},
                    "error_family": nullable_string,
                    "first_invalid_line": nullable_line,
                    "probability_invalid": {
                        "maximum": 1,
                        "minimum": 0,
                        "type": "number",
                    },
                    "repair": repair,
                    "view_digest": {"const": case.view_digest, "type": "string"},
                    "witness": witness,
                },
                "required": [
                    "case_id",
                    "error_family",
                    "first_invalid_line",
                    "probability_invalid",
                    "repair",
                    "view_digest",
                    "witness",
                ],
                "type": "object",
            }
        )
    generic_answer_item = {
        "additionalProperties": False,
        "properties": {
            "case_id": {"type": "string"},
            "error_family": nullable_string,
            "first_invalid_line": nullable_line,
            "probability_invalid": {
                "maximum": 1,
                "minimum": 0,
                "type": "number",
            },
            "repair": repair,
            "view_digest": {"type": "string"},
            "witness": witness,
        },
        "required": [
            "case_id",
            "error_family",
            "first_invalid_line",
            "probability_invalid",
            "repair",
            "view_digest",
            "witness",
        ],
        "type": "object",
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "answers": {
                "items": generic_answer_item,
                "maxItems": len(prefix_items),
                "minItems": len(prefix_items),
                "prefixItems": prefix_items,
                "type": "array",
            },
            "schema": {"const": RESPONSE_SCHEMA, "type": "string"},
            "schema_version": {"const": RESPONSE_SCHEMA_VERSION, "type": "integer"},
        },
        "required": ["answers", "schema", "schema_version"],
        "type": "object",
    }


def codex_prompt_for_batch(batch: SolverBatch) -> str:
    """Render the exact request artifact retained by ``CodexRunResult``."""

    if not isinstance(batch, SolverBatch):
        raise ValueError("Codex prompt rendering requires a sealed SolverBatch")
    return canonical_json(batch.to_solver_payload())


def codex_output_schema_json(batch: SolverBatch) -> bytes:
    """Render the exact canonical app-server output-schema bytes."""

    return json.dumps(
        batch_output_schema(batch),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class EvaluationPlan:
    config: EvaluationConfig
    seeds: DesignSeeds
    cases: tuple[EvaluationCase, ...]
    assignments: tuple[Assignment, ...]
    pair_dispatch: tuple[PairDispatch, ...]
    retrieval_packets: tuple[RetrievalPacket, ...]
    solver_prompt: str
    spec: RunSpec

    def __post_init__(self) -> None:
        if not isinstance(self.config, EvaluationConfig) or not isinstance(self.seeds, DesignSeeds):
            raise ValueError("evaluation plan requires validated config and seeds")
        if len(self.cases) != len(self.spec.design_case_ids):
            raise ValueError("plan case universe differs from run spec")
        if tuple(case.case_id for case in self.cases) != self.spec.design_case_ids:
            raise ValueError("plan case ordering differs from run spec")
        if tuple(packet.case_id for packet in self.retrieval_packets) != (
            self.spec.design_case_ids
        ):
            raise ValueError("plan retrieval packets differ from the case universe")
        if self.spec.required_pair_ids != tuple(range(self.config.pair_count)):
            raise ValueError("plan pair universe differs from config")
        expected_count = len(self.cases) * self.config.pair_count * len(Condition)
        if len(self.assignments) != expected_count:
            raise ValueError("plan assignment cardinality is incomplete")
        if len({assignment.digest for assignment in self.assignments}) != expected_count:
            raise ValueError("plan contains duplicate assignments")
        if len(self.pair_dispatch) != self.config.pair_count:
            raise ValueError("plan pair dispatch is incomplete")
        if {entry.pair_id for entry in self.pair_dispatch} != set(self.spec.required_pair_ids):
            raise ValueError("plan pair dispatch ids are incomplete")
        if {entry.dispatch_index for entry in self.pair_dispatch} != set(
            self.spec.required_pair_ids
        ):
            raise ValueError("plan pair dispatch indices are incomplete")
        if not hmac.compare_digest(self.spec.config_digest, self.config.digest):
            raise ValueError("plan config digest is stale")
        if self.spec.seed_commitments != self.seeds.commitments():
            raise ValueError("plan seed commitments are stale")
        if self.spec.design_universe_digest != _digest_json(
            [case.universe_record() for case in self.cases]
        ):
            raise ValueError("plan design-universe seal is stale")
        if self.spec.solver_prompt_digest != _digest_text(
            self.solver_prompt, "solver prompt"
        ):
            raise ValueError("plan solver-prompt seal is stale")
        if (
            self.spec.requested_model != self.config.runtime.requested_model
            or self.spec.requested_provider != self.config.runtime.requested_provider
            or self.spec.requested_reasoning_effort
            != self.config.runtime.requested_reasoning_effort
            or self.spec.runtime_policy_digest
            != _digest_json(_sealed_runtime_policy(self.config))
        ):
            raise ValueError("plan runtime-policy seal is stale")
        if self.spec.brain_on_context_digest != _digest_json(
            [packet.to_sealed_dict() for packet in self.retrieval_packets]
        ):
            raise ValueError("plan brain-on retrieval seal is stale")
        empty_sham_records = [
            {
                "case_id": case.case_id,
                "disposition": RetrievalDisposition.EMPTY_SHAM.value,
                "packet_digest": _digest_text("", "empty sham"),
            }
            for case in self.cases
        ]
        if self.spec.brain_off_context_digest != _digest_json(empty_sham_records):
            raise ValueError("plan brain-off sham seal is stale")
        if not hmac.compare_digest(
            self.spec.assignment_digest,
            _digest_json([assignment.to_sealed_dict() for assignment in self.assignments]),
        ):
            raise ValueError("plan assignment seal is stale")
        if not hmac.compare_digest(
            self.spec.pair_dispatch_digest,
            _digest_json([entry.to_dict() for entry in self.pair_dispatch]),
        ):
            raise ValueError("plan pair-dispatch seal is stale")
        _validate_assignment_design(self)
        pack_records, treatment_records = _plan_pack_and_treatment_records(self)
        if self.spec.condition_free_view_digest != _digest_json(pack_records):
            raise ValueError("plan condition-free view seal is stale")
        if self.spec.treatment_digest != _digest_json(treatment_records):
            raise ValueError("plan treatment seal is stale")

    def assignments_for(self, pair_id: int, condition: Condition) -> tuple[Assignment, ...]:
        if pair_id not in self.spec.required_pair_ids or not isinstance(condition, Condition):
            raise ValueError("unknown pair or condition")
        values = tuple(
            sorted(
                (
                    assignment
                    for assignment in self.assignments
                    if assignment.pair_id == pair_id and assignment.condition is condition
                ),
                key=lambda assignment: assignment.order_index,
            )
        )
        if len(values) != len(self.cases):
            raise ValueError("plan batch cardinality is incomplete")
        return values

    def batch(self, pair_id: int, condition: Condition) -> SolverBatch:
        assignments = self.assignments_for(pair_id, condition)
        cases = tuple(
            SolverBatchCase(
                case_id=assignment.case_id,
                view_digest=assignment.view_digest,
                view=assignment.view,
                payload_digest=_digest_json(assignment.view.to_dict()),
            )
            for assignment in assignments
        )
        packet_by_case = {packet.case_id: packet for packet in self.retrieval_packets}
        contexts = tuple(
            (
                packet_by_case[assignment.case_id].packet or ""
                if condition is Condition.BRAIN_ON
                else ""
            )
            for assignment in assignments
        )
        context_digest = _case_context_digest(cases, contexts)
        return SolverBatch(
            pair_id=pair_id,
            condition=condition,
            cases=cases,
            solver_prompt=self.solver_prompt,
            solver_prompt_digest=self.spec.solver_prompt_digest,
            config_digest=self.spec.config_digest,
            case_contexts=contexts,
            pack_digest=_pack_digest(pair_id, cases),
            context_digest=context_digest,
            treatment_digest=_treatment_digest(
                condition,
                solver_prompt_digest=self.spec.solver_prompt_digest,
                context_digest=context_digest,
                config_digest=self.spec.config_digest,
            ),
            run_spec_digest=self.spec.digest,
            dispatch_nonce=_dispatch_nonce(
                self.seeds.condition,
                run_spec_digest=self.spec.digest,
                pair_id=pair_id,
                condition=condition,
            ),
        )


def _plan_pack_and_treatment_records(
    plan: EvaluationPlan,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    packets = {packet.case_id: packet for packet in plan.retrieval_packets}
    packs: list[dict[str, Any]] = []
    treatments: list[dict[str, Any]] = []
    for pair_id in plan.spec.required_pair_ids:
        assignments = plan.assignments_for(pair_id, Condition.BRAIN_ON)
        cases = tuple(
            SolverBatchCase(
                case_id=assignment.case_id,
                view_digest=assignment.view_digest,
                view=assignment.view,
                payload_digest=_digest_json(assignment.view.to_dict()),
            )
            for assignment in assignments
        )
        packs.append({"pack_digest": _pack_digest(pair_id, cases), "pair_id": pair_id})
        for condition in Condition:
            contexts = tuple(
                (
                    packets[assignment.case_id].packet or ""
                    if condition is Condition.BRAIN_ON
                    else ""
                )
                for assignment in assignments
            )
            treatments.append(
                {
                    "condition": condition.value,
                    "pair_id": pair_id,
                    "treatment_digest": _treatment_digest(
                        condition,
                        solver_prompt_digest=plan.spec.solver_prompt_digest,
                        context_digest=_case_context_digest(cases, contexts),
                        config_digest=plan.spec.config_digest,
                    ),
                }
            )
    return packs, treatments


def _validate_assignment_design(plan: EvaluationPlan) -> None:
    expected_keys = {
        (pair_id, condition, case.case_id)
        for pair_id in plan.spec.required_pair_ids
        for condition in Condition
        for case in plan.cases
    }
    observed: dict[tuple[int, Condition, str], Assignment] = {}
    for assignment in plan.assignments:
        _bound_case(plan, assignment)
        key = (assignment.pair_id, assignment.condition, assignment.case_id)
        if key in observed:
            raise ValueError(f"duplicate assignment key: {key}")
        observed[key] = assignment
    if set(observed) != expected_keys:
        raise ValueError("assignment universe has missing or unexpected rows")
    half = plan.config.pair_count // 2
    for case in plan.cases:
        variants = []
        for pair_id in plan.spec.required_pair_ids:
            on = observed[(pair_id, Condition.BRAIN_ON, case.case_id)]
            off = observed[(pair_id, Condition.BRAIN_OFF, case.case_id)]
            if on.variant is not off.variant:
                raise ValueError("matched conditions received different variants")
            if on.view_digest != off.view_digest or on.order_index != off.order_index:
                raise ValueError("matched conditions received different ordered views")
            variants.append(on.variant)
        if variants.count(Variant.CLEAN) != half or variants.count(Variant.FAULTY) != half:
            raise ValueError("each case must be clean/faulty balanced across pairs")
    strata: dict[tuple[str, str, str], list[EvaluationCase]] = defaultdict(list)
    for case in plan.cases:
        strata[(case.domain, case.difficulty, case.family)].append(case)
    for pair_id in plan.spec.required_pair_ids:
        for cases in strata.values():
            clean = sum(
                observed[(pair_id, Condition.BRAIN_ON, case.case_id)].variant is Variant.CLEAN
                for case in cases
            )
            faulty = len(cases) - clean
            if abs(clean - faulty) > 1:
                raise ValueError("variants are not balanced within pair/stratum")


def build_full_plan(
    cases: Sequence[EvaluationCase],
    *,
    config: EvaluationConfig,
    seeds: DesignSeeds,
    benchmark_manifest_digest: str,
    solver_prompt: str,
    brain_on_packets: Mapping[str, str | None],
    abstention_reasons: Mapping[str, str] | None = None,
) -> EvaluationPlan:
    """Build and seal a full matched design from exact certified case types."""

    if not isinstance(config, EvaluationConfig) or not isinstance(seeds, DesignSeeds):
        raise ValueError("validated config and design seeds are required")
    materialized = tuple(cases)
    if not 1 <= len(materialized) <= _MAX_CASES:
        raise ValueError("full design requires between 1 and 10,000 cases")
    if not all(isinstance(case, EvaluationCase) for case in materialized):
        raise ValueError("full design cases must be EvaluationCase values")
    fuzz_only_faults = sorted(
        {
            case.item.fault.kind.value
            for case in materialized
            if type(case.item) is BenchmarkItem
            and case.item.fault.kind not in SOLVER_VALID_FAULTS
        }
    )
    if fuzz_only_faults:
        raise ValueError(
            "scored benchmark plans reject fuzz-only fault kinds: "
            + ", ".join(fuzz_only_faults)
        )
    if len({case.case_id for case in materialized}) != len(materialized):
        raise ValueError("full design case ids must be unique")
    _digest(benchmark_manifest_digest, "benchmark manifest digest")
    prompt = _text(
        solver_prompt,
        "solver prompt",
        max_bytes=_MAX_CONTEXT_BYTES,
        allow_layout_controls=True,
    )
    if not isinstance(brain_on_packets, Mapping):
        raise ValueError("brain_on_packets must be a case-id mapping")
    case_ids = {case.case_id for case in materialized}
    if set(brain_on_packets) != case_ids:
        raise ValueError("brain_on_packets must cover the exact case universe")
    reasons = {} if abstention_reasons is None else dict(abstention_reasons)
    if not set(reasons) <= case_ids:
        raise ValueError("abstention reasons contain an unknown case")
    retrieval_packets = tuple(
        RetrievalPacket(
            case_id=case.case_id,
            packet=brain_on_packets[case.case_id],
            abstention_reason=(
                reasons.get(case.case_id)
                if brain_on_packets[case.case_id] is None
                else None
            ),
        )
        for case in materialized
    )
    for case in materialized:
        case.certify()

    pair_ids = tuple(range(config.pair_count))
    pair_order = _permutation(pair_ids, seed=seeds.pair, label="pair-dispatch")
    pair_rank = {pair_id: rank for rank, pair_id in enumerate(pair_order)}
    dispatch = []
    for index, pair_id in enumerate(pair_order):
        condition_order = _permutation(
            tuple(Condition), seed=seeds.condition, label=f"condition:{pair_id}"
        )
        dispatch.append(PairDispatch(pair_id, index, condition_order))

    strata: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for case_index, case in enumerate(materialized):
        strata[(case.domain, case.difficulty, case.family)].append(case_index)
    variant_by_case_pair: dict[tuple[int, int], Variant] = {}
    half = config.pair_count // 2
    for stratum, indices in sorted(strata.items()):
        label = canonical_json(list(stratum))
        ordered_indices = _permutation(indices, seed=seeds.variant, label=label)
        offset = HashSampler(_derived_seed(seeds.variant, f"offset:{label}")).below(
            config.pair_count
        )
        for position, case_index in enumerate(ordered_indices):
            for pair_id in pair_ids:
                # Alternating positions are half a cycle apart.  Thus every case is
                # clean in exactly half the pairs, while every pair is balanced to
                # within one item inside each stratum (including odd-sized strata).
                rotation = (
                    position * half + pair_rank[pair_id] + offset
                ) % config.pair_count
                variant_by_case_pair[(case_index, pair_id)] = (
                    Variant.CLEAN if rotation < half else Variant.FAULTY
                )
    for case_index, case in enumerate(materialized):
        if type(case.item) is ChallengeCase:
            for pair_id in pair_ids:
                challenge_variant = challenge_assignment_variant(case.item.index, pair_id)
                variant_by_case_pair[(case_index, pair_id)] = (
                    Variant.CLEAN
                    if challenge_variant is ChallengeTraceVariant.CLEAN
                    else Variant.FAULTY
                )

    assignments: list[Assignment] = []
    for pair_entry in sorted(dispatch, key=lambda entry: entry.dispatch_index):
        ordered_case_indices = _permutation(
            tuple(range(len(materialized))),
            seed=seeds.order,
            label=f"case-order:{pair_entry.pair_id}",
        )
        for condition in pair_entry.condition_order:
            for order_index, case_index in enumerate(ordered_case_indices):
                case = materialized[case_index]
                variant = variant_by_case_pair[(case_index, pair_entry.pair_id)]
                view = case.solver_view(variant)
                assignments.append(
                    Assignment(
                        case_id=case.case_id,
                        case_index=case_index,
                        item_digest=case.item_digest,
                        pair_id=pair_entry.pair_id,
                        condition=condition,
                        variant=variant,
                        order_index=order_index,
                        domain=case.domain,
                        difficulty=case.difficulty,
                        family=case.family,
                        view=view,
                        view_digest=case.solver_view_digest(view),
                    )
                )
    assignment_tuple = tuple(assignments)
    prompt_digest = _digest_text(prompt, "solver prompt")
    on_context_digest = _digest_json(
        [packet.to_sealed_dict() for packet in retrieval_packets]
    )
    off_context_digest = _digest_json(
        [
            {
                "case_id": case.case_id,
                "disposition": RetrievalDisposition.EMPTY_SHAM.value,
                "packet_digest": _digest_text("", "empty sham"),
            }
            for case in materialized
        ]
    )
    packs = []
    treatments = []
    packet_by_case = {packet.case_id: packet for packet in retrieval_packets}
    for pair_id in pair_ids:
        on_assignments = sorted(
            (
                assignment
                for assignment in assignment_tuple
                if assignment.pair_id == pair_id
                and assignment.condition is Condition.BRAIN_ON
            ),
            key=lambda assignment: assignment.order_index,
        )
        batch_cases = tuple(
            SolverBatchCase(
                case_id=assignment.case_id,
                view_digest=assignment.view_digest,
                view=assignment.view,
                payload_digest=_digest_json(assignment.view.to_dict()),
            )
            for assignment in on_assignments
        )
        packs.append({"pack_digest": _pack_digest(pair_id, batch_cases), "pair_id": pair_id})
        for condition in Condition:
            contexts = tuple(
                (
                    packet_by_case[assignment.case_id].packet or ""
                    if condition is Condition.BRAIN_ON
                    else ""
                )
                for assignment in on_assignments
            )
            context_digest = _case_context_digest(batch_cases, contexts)
            treatments.append(
                {
                    "condition": condition.value,
                    "pair_id": pair_id,
                    "treatment_digest": _treatment_digest(
                        condition,
                        solver_prompt_digest=prompt_digest,
                        context_digest=context_digest,
                        config_digest=config.digest,
                    ),
                }
            )
    spec = RunSpec(
        design_case_ids=tuple(case.case_id for case in materialized),
        required_pair_ids=pair_ids,
        benchmark_manifest_digest=benchmark_manifest_digest,
        design_universe_digest=_digest_json(
            [case.universe_record() for case in materialized]
        ),
        assignment_digest=_digest_json(
            [assignment.to_sealed_dict() for assignment in assignment_tuple]
        ),
        condition_free_view_digest=_digest_json(packs),
        solver_prompt_digest=prompt_digest,
        brain_on_context_digest=on_context_digest,
        brain_off_context_digest=off_context_digest,
        treatment_digest=_digest_json(treatments),
        config_digest=config.digest,
        seed_commitments=seeds.commitments(),
        requested_model=config.runtime.requested_model,
        requested_provider=config.runtime.requested_provider,
        requested_reasoning_effort=config.runtime.requested_reasoning_effort,
        runtime_policy_digest=_digest_json(_sealed_runtime_policy(config)),
        pair_dispatch_digest=_digest_json([entry.to_dict() for entry in dispatch]),
    )
    return EvaluationPlan(
        config=config,
        seeds=seeds,
        cases=materialized,
        assignments=assignment_tuple,
        pair_dispatch=tuple(dispatch),
        retrieval_packets=retrieval_packets,
        solver_prompt=prompt,
        spec=spec,
    )


@dataclass(frozen=True, slots=True)
class RepairProposal:
    replacement_line: str

    def __post_init__(self) -> None:
        _text(self.replacement_line, "replacement line", max_bytes=16_384)

    def to_dict(self) -> dict[str, str]:
        return {"replacement_line": self.replacement_line}


@dataclass(frozen=True, slots=True)
class WitnessProposal:
    left_value: str | None = None
    right_value: str | None = None
    typed_payload_json: str | None = None

    def __post_init__(self) -> None:
        legacy = self.left_value is not None or self.right_value is not None
        typed = self.typed_payload_json is not None
        if legacy == typed:
            raise ValueError("witness must use exactly one declared representation")
        if legacy:
            _text(self.left_value, "witness left value", max_bytes=1024)
            _text(self.right_value, "witness right value", max_bytes=1024)
            return
        encoded = _text(
            self.typed_payload_json,
            "typed witness payload",
            max_bytes=16_384,
            allow_layout_controls=True,
        )
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError as exc:
            raise ValueError("typed witness payload must be canonical JSON") from exc
        if value is None:
            raise ValueError("typed witness payload cannot be null")
        canonical = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if encoded != canonical:
            raise ValueError("typed witness payload must be canonical JSON")

    @classmethod
    def from_typed_payload(cls, value: Any) -> WitnessProposal:
        if value is None:
            raise ValueError("typed witness payload cannot be null")
        try:
            encoded = json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("typed witness payload must be finite JSON") from exc
        return cls(typed_payload_json=encoded)

    @property
    def typed_payload(self) -> Any:
        if self.typed_payload_json is None:
            return None
        return json.loads(self.typed_payload_json)

    def to_dict(self) -> dict[str, Any]:
        if self.typed_payload_json is not None:
            return {"typed_payload": self.typed_payload}
        return {"left_value": self.left_value, "right_value": self.right_value}


@dataclass(frozen=True, slots=True)
class SolverAnswer:
    case_id: str
    view_digest: str
    probability_invalid: float
    first_invalid_line: int | None
    repair: RepairProposal | None
    error_family: str | None
    witness: WitnessProposal | None
    invalid_probability_threshold: float

    def __post_init__(self) -> None:
        _text(self.case_id, "answer case id", max_bytes=128)
        _digest(self.view_digest, "answer view digest")
        probability = _number(self.probability_invalid, "probability_invalid")
        if not 0 <= probability <= 1:
            raise ValueError("probability_invalid must be in [0, 1]")
        if not 0 < self.invalid_probability_threshold < 1:
            raise ValueError("invalid probability threshold must be in (0, 1)")
        if self.verdict is Verdict.VALID:
            if any(
                value is not None
                for value in (
                    self.first_invalid_line,
                    self.repair,
                    self.error_family,
                    self.witness,
                )
            ):
                raise ValueError("a VALID answer requires null line, repair, family, and witness")
        else:
            _integer(self.first_invalid_line, "first invalid line", minimum=1)
            if not isinstance(self.error_family, str):
                raise ValueError("an INVALID answer requires an error family")
            _text(self.error_family, "error family", max_bytes=128)
            if self.repair is not None and not isinstance(self.repair, RepairProposal):
                raise ValueError("repair must be a RepairProposal or null")
            if self.witness is not None and not isinstance(self.witness, WitnessProposal):
                raise ValueError("witness must be a WitnessProposal or null")

    @property
    def verdict(self) -> Verdict:
        return (
            Verdict.INVALID
            if self.probability_invalid >= self.invalid_probability_threshold
            else Verdict.VALID
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "error_family": self.error_family,
            "first_invalid_line": self.first_invalid_line,
            "probability_invalid": self.probability_invalid,
            "repair": None if self.repair is None else self.repair.to_dict(),
            "view_digest": self.view_digest,
            "witness": None if self.witness is None else self.witness.to_dict(),
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.to_dict())


class _ParseFailure(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _duplicate_key_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _ParseFailure("duplicate_key", "duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise _ParseFailure("nonfinite_number", "nonfinite JSON number")


def _bounded_json_tree(value: Any, limits: ResponseLimits) -> None:
    stack = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > limits.maximum_nodes:
            raise _ParseFailure("node_budget", "response exceeds the JSON node budget")
        if depth > limits.maximum_depth:
            raise _ParseFailure("depth_budget", "response exceeds the JSON depth budget")
        if isinstance(current, dict):
            for key, child in current.items():
                try:
                    _text(key, "response key", max_bytes=limits.maximum_string_bytes)
                except ValueError as exc:
                    message = str(exc)
                    code = (
                        "unicode_policy"
                        if "Unicode" in message or "control" in message or "UTF-8" in message
                        else "string_budget"
                    )
                    raise _ParseFailure(code, message) from exc
                stack.append((child, depth + 1))
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            try:
                _text(
                    current,
                    "response string",
                    max_bytes=limits.maximum_string_bytes,
                    allow_empty=True,
                )
            except ValueError as exc:
                message = str(exc)
                code = (
                    "unicode_policy"
                    if "Unicode" in message or "control" in message or "UTF-8" in message
                    else "string_budget"
                )
                raise _ParseFailure(code, message) from exc
        elif isinstance(current, bool) or current is None:
            continue
        elif isinstance(current, int):
            if abs(current) > limits.maximum_integer_abs:
                raise _ParseFailure("integer_budget", "response integer exceeds its budget")
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise _ParseFailure("nonfinite_number", "response contains a nonfinite number")
        else:
            raise _ParseFailure("json_type", "response contains an unsupported JSON value")


def _strict_response_json(raw: Any, limits: ResponseLimits) -> tuple[Mapping[str, Any], str]:
    if not isinstance(raw, str):
        raise _ParseFailure("response_type", "solver response must be a string")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _ParseFailure("unicode_policy", "solver response is not valid UTF-8 text") from exc
    if len(encoded) > limits.maximum_bytes:
        raise _ParseFailure("byte_budget", "solver response exceeds its byte budget")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_duplicate_key_hook,
            parse_constant=_reject_constant,
        )
    except _ParseFailure:
        raise
    except json.JSONDecodeError as exc:
        raise _ParseFailure("invalid_json", f"invalid solver JSON: {exc.msg}") from exc
    except RecursionError as exc:
        raise _ParseFailure("depth_budget", "solver JSON exceeds parser nesting") from exc
    _bounded_json_tree(value, limits)
    if not isinstance(value, dict):
        raise _ParseFailure("root_schema", "solver response must be one JSON object")
    return value, _digest_bytes(encoded)


def _raw_digest(raw: Any) -> str:
    if isinstance(raw, str):
        return _digest_bytes(raw.encode("utf-8", errors="backslashreplace"))
    return _digest_bytes(repr(raw).encode("utf-8", errors="backslashreplace"))


def _answer_from_mapping(
    value: Any,
    *,
    threshold: float,
) -> SolverAnswer:
    expected = {
        "case_id",
        "error_family",
        "first_invalid_line",
        "probability_invalid",
        "repair",
        "view_digest",
        "witness",
    }
    try:
        answer = _mapping(value, "solver answer")
        _strict_keys(answer, expected, "solver answer")
        probability = _number(answer["probability_invalid"], "probability_invalid")
        if not 0 <= probability <= 1:
            raise ValueError("probability_invalid must be in [0, 1]")
        line = answer["first_invalid_line"]
        if line is not None:
            line = _integer(line, "first invalid line", minimum=1)
        repair_raw = answer["repair"]
        repair = None
        if repair_raw is not None:
            repair_map = _mapping(repair_raw, "repair")
            _strict_keys(repair_map, {"replacement_line"}, "repair")
            repair = RepairProposal(repair_map["replacement_line"])
        witness_raw = answer["witness"]
        witness = None
        if witness_raw is not None:
            witness_map = _mapping(witness_raw, "witness")
            if set(witness_map) == {"left_value", "right_value"}:
                witness = WitnessProposal(
                    left_value=witness_map["left_value"],
                    right_value=witness_map["right_value"],
                )
            elif set(witness_map) == {"typed_payload"}:
                witness = WitnessProposal.from_typed_payload(witness_map["typed_payload"])
            else:
                _strict_keys(witness_map, {"left_value", "right_value"}, "witness")
        family = answer["error_family"]
        if family is not None:
            family = _text(family, "error family", max_bytes=128)
        return SolverAnswer(
            case_id=_text(answer["case_id"], "answer case id", max_bytes=128),
            view_digest=_digest(answer["view_digest"], "answer view digest"),
            probability_invalid=probability,
            first_invalid_line=line,
            repair=repair,
            error_family=family,
            witness=witness,
            invalid_probability_threshold=threshold,
        )
    except ValueError as exc:
        raise _ParseFailure("answer_schema", str(exc)) from exc


@dataclass(frozen=True, slots=True)
class CaseAttempt:
    assignment_digest: str
    case_id: str
    view_digest: str
    status: AttemptStatus
    answer: SolverAnswer | None
    failure_code: str | None

    def __post_init__(self) -> None:
        _digest(self.assignment_digest, "attempt assignment digest")
        _text(self.case_id, "attempt case id", max_bytes=128)
        _digest(self.view_digest, "attempt view digest")
        if not isinstance(self.status, AttemptStatus):
            raise ValueError("attempt status must be declared")
        if self.status is AttemptStatus.SUCCESS:
            if not isinstance(self.answer, SolverAnswer) or self.failure_code is not None:
                raise ValueError("successful attempts require an answer and no failure code")
        elif self.answer is not None or not isinstance(self.failure_code, str):
            raise ValueError("unsuccessful attempts require a stable failure code and no answer")
        if self.failure_code is not None:
            _text(self.failure_code, "attempt failure code", max_bytes=128)


@dataclass(frozen=True, slots=True)
class BatchResult:
    pair_id: int
    condition: Condition
    batch_digest: str
    pack_digest: str
    treatment_digest: str
    raw_response_digest: str
    attempts: tuple[CaseAttempt, ...]
    latency_seconds: float
    input_tokens: int
    output_tokens: int
    unblinded: bool

    def __post_init__(self) -> None:
        _integer(self.pair_id, "batch result pair id", minimum=0)
        if not isinstance(self.condition, Condition):
            raise ValueError("batch result condition must be declared")
        for label, value in (
            ("batch", self.batch_digest),
            ("pack", self.pack_digest),
            ("treatment", self.treatment_digest),
            ("raw response", self.raw_response_digest),
        ):
            _digest(value, f"{label} digest")
        if not self.attempts:
            raise ValueError("batch result must retain every expected case")
        if len({attempt.assignment_digest for attempt in self.attempts}) != len(self.attempts):
            raise ValueError("batch result contains duplicate assignment attempts")
        if not math.isfinite(self.latency_seconds) or self.latency_seconds < 0:
            raise ValueError("latency must be finite and nonnegative")
        _integer(self.input_tokens, "input tokens", minimum=0)
        _integer(self.output_tokens, "output tokens", minimum=0)
        if not isinstance(self.unblinded, bool):
            raise ValueError("unblinded must be a boolean")


def _result_for_failure(
    batch: SolverBatch,
    *,
    status: AttemptStatus,
    code: str,
    raw_digest: str,
    latency_seconds: float,
    input_tokens: int,
    output_tokens: int,
    unblinded: bool,
    assignments: Sequence[Assignment],
) -> BatchResult:
    return BatchResult(
        pair_id=batch.pair_id,
        condition=batch.condition,
        batch_digest=batch.digest,
        pack_digest=batch.pack_digest,
        treatment_digest=batch.treatment_digest,
        raw_response_digest=raw_digest,
        attempts=tuple(
            CaseAttempt(
                assignment_digest=assignment.digest,
                case_id=assignment.case_id,
                view_digest=assignment.view_digest,
                status=status,
                answer=None,
                failure_code=code,
            )
            for assignment in assignments
        ),
        latency_seconds=latency_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        unblinded=unblinded,
    )


def parse_batch_response(
    plan: EvaluationPlan,
    *,
    pair_id: int,
    condition: Condition,
    raw: Any,
    latency_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> BatchResult:
    """Parse a solver response without throwing for solver-caused malformed output."""

    batch = plan.batch(pair_id, condition)
    assignments = plan.assignments_for(pair_id, condition)
    try:
        value, raw_digest = _strict_response_json(raw, plan.config.response_limits)
        try:
            _strict_keys(value, {"answers", "schema", "schema_version"}, "batch response")
        except ValueError as exc:
            raise _ParseFailure("root_schema", str(exc)) from exc
        if not isinstance(value["schema"], str) or value["schema"] != RESPONSE_SCHEMA:
            raise _ParseFailure("root_schema", "unsupported batch response schema")
        try:
            schema_version = _integer(value["schema_version"], "response schema version")
        except ValueError as exc:
            raise _ParseFailure("root_schema", str(exc)) from exc
        if schema_version != RESPONSE_SCHEMA_VERSION:
            raise _ParseFailure("root_schema", "unsupported batch response schema")
        answers_raw = value["answers"]
        if not isinstance(answers_raw, list):
            raise _ParseFailure("root_schema", "answers must be an array")
        if len(answers_raw) > len(assignments):
            raise _ParseFailure("cardinality", "response has more answers than assigned cases")
        answers = tuple(
            _answer_from_mapping(
                answer,
                threshold=plan.config.invalid_probability_threshold,
            )
            for answer in answers_raw
        )
        if len({answer.case_id for answer in answers}) != len(answers):
            raise _ParseFailure("duplicate_case", "response repeats a case id")
        expected_by_id = {assignment.case_id: assignment for assignment in assignments}
        if any(answer.case_id not in expected_by_id for answer in answers):
            raise _ParseFailure("unexpected_case", "response contains an unassigned case id")
        expected_filtered_order = [
            assignment.case_id
            for assignment in assignments
            if assignment.case_id in {answer.case_id for answer in answers}
        ]
        if [answer.case_id for answer in answers] != expected_filtered_order:
            raise _ParseFailure("answer_order", "response answers are out of assigned order")
        for answer in answers:
            expected = expected_by_id[answer.case_id]
            if not hmac.compare_digest(answer.view_digest, expected.view_digest):
                raise _ParseFailure("view_digest", "response view digest differs from assignment")
    except _ParseFailure as exc:
        return _result_for_failure(
            batch,
            status=AttemptStatus.MALFORMED,
            code=exc.code,
            raw_digest=_raw_digest(raw),
            latency_seconds=latency_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            unblinded=True,
            assignments=assignments,
        )
    answers_by_id = {answer.case_id: answer for answer in answers}
    attempts = []
    for assignment in assignments:
        answer = answers_by_id.get(assignment.case_id)
        attempts.append(
            CaseAttempt(
                assignment_digest=assignment.digest,
                case_id=assignment.case_id,
                view_digest=assignment.view_digest,
                status=AttemptStatus.SUCCESS if answer is not None else AttemptStatus.MISSING,
                answer=answer,
                failure_code=None if answer is not None else "missing_answer",
            )
        )
    return BatchResult(
        pair_id=pair_id,
        condition=condition,
        batch_digest=batch.digest,
        pack_digest=batch.pack_digest,
        treatment_digest=batch.treatment_digest,
        raw_response_digest=raw_digest,
        attempts=tuple(attempts),
        latency_seconds=latency_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        unblinded=True,
    )


def failed_batch(
    plan: EvaluationPlan,
    *,
    pair_id: int,
    condition: Condition,
    status: AttemptStatus,
    evidence_digest: str,
    unblinded: bool,
    latency_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> BatchResult:
    """Record a non-response attempt; infrastructure failures are never solver failures."""

    if status not in {
        AttemptStatus.TIMEOUT,
        AttemptStatus.TOOL_VIOLATION,
        AttemptStatus.MISSING,
        AttemptStatus.INFRASTRUCTURE_FAILURE,
    }:
        raise ValueError("failed_batch requires a declared non-response status")
    _digest(evidence_digest, "failure evidence digest")
    batch = plan.batch(pair_id, condition)
    return _result_for_failure(
        batch,
        status=status,
        code=status.value,
        raw_digest=evidence_digest,
        latency_seconds=latency_seconds,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        unblinded=unblinded,
        assignments=plan.assignments_for(pair_id, condition),
    )


def replace_pre_unblinding_infrastructure_failure(
    failed: BatchResult,
    replacement: BatchResult,
) -> BatchResult:
    if failed.unblinded:
        raise ValueError("an infrastructure failure may be retried only before unblinding")
    if {attempt.status for attempt in failed.attempts} != {
        AttemptStatus.INFRASTRUCTURE_FAILURE
    }:
        raise ValueError("only a pure infrastructure failure may be replaced")
    if (
        failed.pair_id,
        failed.condition,
        failed.batch_digest,
        failed.pack_digest,
        failed.treatment_digest,
    ) != (
        replacement.pair_id,
        replacement.condition,
        replacement.batch_digest,
        replacement.pack_digest,
        replacement.treatment_digest,
    ):
        raise ValueError("infrastructure retry differs from the sealed batch")
    return replacement


@dataclass(frozen=True, slots=True)
class GroundTruth:
    case_id: str
    item_digest: str
    variant: Variant
    first_invalid_line: int | None
    error_family: str | None

    def __post_init__(self) -> None:
        _text(self.case_id, "ground-truth case id", max_bytes=128)
        _digest(self.item_digest, "ground-truth item digest")
        if not isinstance(self.variant, Variant):
            raise ValueError("ground-truth variant must be declared")
        if self.variant is Variant.CLEAN:
            if self.first_invalid_line is not None or self.error_family is not None:
                raise ValueError("clean ground truth cannot contain a fault")
        else:
            _integer(self.first_invalid_line, "ground-truth invalid line", minimum=1)
            _text(self.error_family, "ground-truth error family", max_bytes=128)


@dataclass(frozen=True, slots=True)
class VerifierReceipt:
    kind: VerifierKind
    assignment_digest: str
    view_digest: str
    answer_digest: str
    scorer_digest: str
    validator_digest: str
    evidence_digest: str
    passed: bool
    seal_digest: str
    schema: str = RECEIPT_SCHEMA
    schema_version: int = RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema != RECEIPT_SCHEMA or self.schema_version != RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported verifier receipt schema")
        if not isinstance(self.kind, VerifierKind) or not isinstance(self.passed, bool):
            raise ValueError("verifier receipt kind and result must be typed")
        for label, value in (
            ("assignment", self.assignment_digest),
            ("view", self.view_digest),
            ("answer", self.answer_digest),
            ("scorer", self.scorer_digest),
            ("validator", self.validator_digest),
            ("evidence", self.evidence_digest),
            ("seal", self.seal_digest),
        ):
            _digest(value, f"receipt {label} digest")
        expected = _digest_json(self._seal_payload())
        if not hmac.compare_digest(expected, self.seal_digest):
            raise ValueError("verifier receipt seal does not match its immutable fields")

    def _seal_payload(self) -> dict[str, Any]:
        return {
            "answer_digest": self.answer_digest,
            "assignment_digest": self.assignment_digest,
            "evidence_digest": self.evidence_digest,
            "kind": self.kind.value,
            "passed": self.passed,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "scorer_digest": self.scorer_digest,
            "validator_digest": self.validator_digest,
            "view_digest": self.view_digest,
        }

    @classmethod
    def create(
        cls,
        *,
        kind: VerifierKind,
        assignment: Assignment,
        answer: SolverAnswer,
        scorer_digest: str,
        validator_digest: str,
        evidence_digest: str,
        passed: bool,
    ) -> VerifierReceipt:
        payload = {
            "answer_digest": answer.digest,
            "assignment_digest": assignment.digest,
            "evidence_digest": evidence_digest,
            "kind": kind.value,
            "passed": passed,
            "schema": RECEIPT_SCHEMA,
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "scorer_digest": scorer_digest,
            "validator_digest": validator_digest,
            "view_digest": assignment.view_digest,
        }
        return cls(
            kind=kind,
            assignment_digest=assignment.digest,
            view_digest=assignment.view_digest,
            answer_digest=answer.digest,
            scorer_digest=scorer_digest,
            validator_digest=validator_digest,
            evidence_digest=evidence_digest,
            passed=passed,
            seal_digest=_digest_json(payload),
        )


def _bound_case(plan: EvaluationPlan, assignment: Assignment) -> EvaluationCase:
    try:
        case = plan.cases[assignment.case_index]
    except IndexError as exc:
        raise ValueError("assignment case index is outside the sealed universe") from exc
    if case.case_id != assignment.case_id:
        raise ValueError("assignment case id differs from sealed universe")
    if not hmac.compare_digest(case.item_digest, assignment.item_digest):
        raise ValueError("assignment item digest differs from sealed universe")
    if (
        assignment.domain != case.domain
        or assignment.difficulty != case.difficulty
        or assignment.family != case.family
    ):
        raise ValueError("assignment stratum metadata differs from sealed universe")
    expected_view = case.solver_view(assignment.variant)
    if expected_view != assignment.view:
        raise ValueError("assignment view was not produced by the sealed exact case")
    expected_view_digest = case.solver_view_digest(expected_view)
    if not hmac.compare_digest(expected_view_digest, assignment.view_digest):
        raise ValueError("assignment view digest differs from the sealed exact case")
    return case


def _fraction_text(value: Fraction) -> str:
    return (
        str(value.numerator)
        if value.denominator == 1
        else f"{value.numerator}/{value.denominator}"
    )


def verify_repair(
    plan: EvaluationPlan,
    assignment: Assignment,
    answer: SolverAnswer,
) -> VerifierReceipt:
    case = _bound_case(plan, assignment)
    item = case.item
    if type(item) is ChallengeCase:
        validator_digest = _digest_json(
            {
                "algorithm": "challenge-exact-repair-and-typed-replay-v1",
                "case_schema": item.schema,
                "verifier": item.witness.verifier.value,
            }
        )
        passed = False
        evidence: dict[str, Any] = {
            "answer_digest": answer.digest,
            "item_digest": case.item_digest,
            "reason": "preconditions_not_met",
        }
        if (
            assignment.variant is Variant.FAULTY
            and answer.verdict is Verdict.INVALID
            and answer.first_invalid_line == item.fault_line
            and answer.repair is not None
        ):
            passed, replay_digest = ChallengeCase.verify_repair(
                item, answer.repair.replacement_line
            )
            evidence = {
                "answer_digest": answer.digest,
                "item_digest": case.item_digest,
                "passed": passed,
                "replay_digest": replay_digest,
            }
        return VerifierReceipt.create(
            kind=VerifierKind.REPAIR,
            assignment=assignment,
            answer=answer,
            scorer_digest=plan.config.scorer_digest,
            validator_digest=validator_digest,
            evidence_digest=_digest_json(evidence),
            passed=passed,
        )
    assert type(item) is BenchmarkItem
    validator_digest = _digest_json(
        {
            "algorithm": "exact-line-repair-and-two-validator-replay-v1",
            "validators": list(item.validators),
        }
    )
    passed = False
    evidence: dict[str, Any] = {
        "answer_digest": answer.digest,
        "item_digest": item.digest(),
        "reason": "preconditions_not_met",
    }
    if (
        assignment.variant is Variant.FAULTY
        and answer.verdict is Verdict.INVALID
        and answer.first_invalid_line == item.fault.step_number
        and answer.repair is not None
    ):
        line_index = item.fault.step_number - 1
        expected_line = item.clean_solution.steps[line_index].text
        if hmac.compare_digest(answer.repair.replacement_line, expected_line):
            steps = list(item.faulty_solution.steps)
            steps[line_index] = item.clean_solution.steps[line_index]
            candidate = Solution(tuple(steps), item.faulty_solution.conclusion)
            reports = validate_solution(item, candidate)
            replayed = replay_repair(item)
            passed = (
                candidate == replayed
                and candidate == item.clean_solution
                and len(reports) == len(item.validators)
                and all(report.passed for report in reports)
            )
            evidence = {
                "answer_digest": answer.digest,
                "candidate_digest": candidate.digest(),
                "expected_clean_digest": item.clean_solution.digest(),
                "exact_replacement_match": True,
                "item_digest": item.digest(),
                "passed": passed,
                "reports": [
                    {
                        "failing_steps": list(report.failing_steps),
                        "passed": report.passed,
                        "validator": report.validator,
                    }
                    for report in reports
                ],
                "replay_digest": replayed.digest(),
            }
        else:
            evidence["reason"] = "replacement_line_differs_from_hidden_clean_step"
    return VerifierReceipt.create(
        kind=VerifierKind.REPAIR,
        assignment=assignment,
        answer=answer,
        scorer_digest=plan.config.scorer_digest,
        validator_digest=validator_digest,
        evidence_digest=_digest_json(evidence),
        passed=passed,
    )


def verify_witness(
    plan: EvaluationPlan,
    assignment: Assignment,
    answer: SolverAnswer,
) -> VerifierReceipt:
    case = _bound_case(plan, assignment)
    item = case.item
    if type(item) is ChallengeCase:
        validator_digest = _digest_json(
            {
                "algorithm": "challenge-typed-witness-replay-v1",
                "case_schema": item.schema,
                "verifier": item.witness.verifier.value,
            }
        )
        passed = False
        evidence: dict[str, Any] = {
            "answer_digest": answer.digest,
            "item_digest": case.item_digest,
            "reason": "preconditions_not_met",
        }
        if (
            assignment.variant is Variant.FAULTY
            and answer.verdict is Verdict.INVALID
            and answer.first_invalid_line == item.fault_line
            and answer.witness is not None
            and answer.witness.typed_payload_json is not None
        ):
            passed, replay_digest = ChallengeCase.verify_witness(
                item, answer.witness.typed_payload
            )
            evidence = {
                "answer_digest": answer.digest,
                "item_digest": case.item_digest,
                "passed": passed,
                "replay_digest": replay_digest,
            }
        return VerifierReceipt.create(
            kind=VerifierKind.WITNESS,
            assignment=assignment,
            answer=answer,
            scorer_digest=plan.config.scorer_digest,
            validator_digest=validator_digest,
            evidence_digest=_digest_json(evidence),
            passed=passed,
        )
    assert type(item) is BenchmarkItem
    validator_digest = _digest_json(
        {"algorithm": "independent-relation-side-evaluation-v1", "mathir": item.schema}
    )
    passed = False
    evidence: dict[str, Any] = {
        "answer_digest": answer.digest,
        "item_digest": item.digest(),
        "reason": "preconditions_not_met",
    }
    if (
        assignment.variant is Variant.FAULTY
        and answer.verdict is Verdict.INVALID
        and answer.first_invalid_line == item.fault.step_number
        and answer.witness is not None
        and answer.witness.typed_payload_json is None
    ):
        relation = item.faulty_solution.steps[item.fault.step_number - 1].assertion
        left = evaluate_expression(relation.left, item.problem.witness_map)
        right = evaluate_expression(relation.right, item.problem.witness_map)
        left_text = _fraction_text(left)
        right_text = _fraction_text(right)
        passed = (
            left != right
            and answer.witness.left_value is not None
            and answer.witness.right_value is not None
            and hmac.compare_digest(answer.witness.left_value, left_text)
            and hmac.compare_digest(answer.witness.right_value, right_text)
        )
        evidence = {
            "answer_digest": answer.digest,
            "faulty_relation_digest": _digest_json(relation.to_dict()),
            "independently_evaluated_left": left_text,
            "independently_evaluated_right": right_text,
            "item_digest": item.digest(),
            "passed": passed,
        }
    return VerifierReceipt.create(
        kind=VerifierKind.WITNESS,
        assignment=assignment,
        answer=answer,
        scorer_digest=plan.config.scorer_digest,
        validator_digest=validator_digest,
        evidence_digest=_digest_json(evidence),
        passed=passed,
    )


@dataclass(frozen=True, slots=True)
class Observation:
    assignment: Assignment
    truth: GroundTruth
    status: AttemptStatus
    answer: SolverAnswer | None
    repair_receipt: VerifierReceipt | None
    witness_receipt: VerifierReceipt | None
    failure_code: str | None
    raw_response_digest: str
    latency_seconds: float
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if not isinstance(self.assignment, Assignment) or not isinstance(self.truth, GroundTruth):
            raise ValueError("observation requires sealed assignment and ground truth")
        if self.truth.case_id != self.assignment.case_id:
            raise ValueError("observation truth differs from assignment")
        if self.truth.item_digest != self.assignment.item_digest:
            raise ValueError("observation truth item differs from assignment")
        if self.truth.variant is not self.assignment.variant:
            raise ValueError("observation truth variant differs from assignment")
        _digest(self.raw_response_digest, "observation response digest")
        if self.status is AttemptStatus.SUCCESS:
            if not isinstance(self.answer, SolverAnswer) or self.failure_code is not None:
                raise ValueError("successful observation requires an answer")
            if self.answer.case_id != self.assignment.case_id:
                raise ValueError("observation answer case differs from assignment")
            if not hmac.compare_digest(self.answer.view_digest, self.assignment.view_digest):
                raise ValueError("observation answer view differs from assignment")
            if self.answer.verdict is Verdict.VALID:
                if self.repair_receipt is not None or self.witness_receipt is not None:
                    raise ValueError("VALID answers cannot carry forged verifier credit")
            else:
                if not isinstance(self.repair_receipt, VerifierReceipt) or not isinstance(
                    self.witness_receipt, VerifierReceipt
                ):
                    raise ValueError("INVALID answers require both immutable verifier receipts")
                for kind, receipt in (
                    (VerifierKind.REPAIR, self.repair_receipt),
                    (VerifierKind.WITNESS, self.witness_receipt),
                ):
                    if receipt.kind is not kind:
                        raise ValueError("verifier receipt kind differs from its slot")
                    if (
                        receipt.assignment_digest != self.assignment.digest
                        or receipt.view_digest != self.assignment.view_digest
                        or receipt.answer_digest != self.answer.digest
                    ):
                        raise ValueError("verifier receipt is not bound to this observation")
        else:
            if (
                self.answer is not None
                or self.repair_receipt is not None
                or self.witness_receipt is not None
            ):
                raise ValueError("unsuccessful observations cannot carry solver or verifier credit")
            if not isinstance(self.failure_code, str):
                raise ValueError("unsuccessful observations require a stable failure code")
        if not math.isfinite(self.latency_seconds) or self.latency_seconds < 0:
            raise ValueError("observation latency must be finite and nonnegative")
        _integer(self.input_tokens, "observation input tokens", minimum=0)
        _integer(self.output_tokens, "observation output tokens", minimum=0)


@dataclass(frozen=True, slots=True)
class RuntimeEvidence:
    agent_id: str
    pair_id: int
    condition: Condition
    requested_model: str
    requested_provider: str
    requested_reasoning_effort: str
    accepted_model: str | None
    accepted_provider: str | None
    accepted_reasoning_effort: str | None
    accepted_config_digest: str | None
    allow_provider_model_fallback: bool | None
    served_model: str | None
    served_reasoning_effort: str | None
    response_attestation_digest: str | None
    response_attestation_source: str | None
    final_assistant_end_turn: bool
    executable_provenance: ExecutableProvenance | None = None
    executable_lock_enforced: bool | None = None
    standard_popen_used: bool | None = None
    command_tail_policy_digest: str | None = None
    reroute_notice_model: str | None = None
    batch_digest: str | None = None
    pack_digest: str | None = None
    treatment_digest: str | None = None
    raw_response_digest: str | None = None
    transcript_digest: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    artifact: CodexRunResult | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _text(self.agent_id, "runtime agent id", max_bytes=256)
        _integer(self.pair_id, "runtime pair id", minimum=0)
        if not isinstance(self.condition, Condition):
            raise ValueError("runtime condition must be declared")
        _text(self.requested_model, "runtime requested model", max_bytes=128)
        _text(self.requested_provider, "runtime requested provider", max_bytes=128)
        _text(
            self.requested_reasoning_effort,
            "runtime requested reasoning effort",
            max_bytes=64,
        )
        for label, value, maximum in (
            ("accepted model", self.accepted_model, 128),
            ("accepted provider", self.accepted_provider, 128),
            ("accepted reasoning effort", self.accepted_reasoning_effort, 64),
            ("served model", self.served_model, 128),
            ("served reasoning effort", self.served_reasoning_effort, 64),
            ("attestation source", self.response_attestation_source, 128),
            ("reroute model", self.reroute_notice_model, 128),
        ):
            if value is not None:
                _text(value, label, max_bytes=maximum)
        if self.accepted_config_digest is not None:
            _digest(self.accepted_config_digest, "accepted config digest")
        if self.response_attestation_digest is not None:
            _digest(self.response_attestation_digest, "response attestation digest")
        if self.executable_provenance is not None and type(
            self.executable_provenance
        ) is not ExecutableProvenance:
            raise ValueError("runtime executable provenance must be exact and immutable")
        for label, value in (
            ("executable lock", self.executable_lock_enforced),
            ("standard Popen", self.standard_popen_used),
        ):
            if value is not None and type(value) is not bool:
                raise ValueError(f"runtime {label} evidence must be bool or null")
        if self.command_tail_policy_digest is not None:
            _digest(self.command_tail_policy_digest, "command-tail policy digest")
        binding_values = (
            self.batch_digest,
            self.pack_digest,
            self.treatment_digest,
            self.raw_response_digest,
            self.transcript_digest,
            self.thread_id,
            self.turn_id,
        )
        if any(value is not None for value in binding_values) and not all(
            value is not None for value in binding_values
        ):
            raise ValueError("runtime artifact binding must be complete or absent")
        for label, value in (
            ("batch", self.batch_digest),
            ("pack", self.pack_digest),
            ("treatment", self.treatment_digest),
            ("raw response", self.raw_response_digest),
            ("transcript", self.transcript_digest),
        ):
            if value is not None:
                _digest(value, f"runtime {label} digest")
        for label, value in (("thread", self.thread_id), ("turn", self.turn_id)):
            if value is not None:
                _text(value, f"runtime {label} id", max_bytes=256)
        if self.allow_provider_model_fallback is not None and not isinstance(
            self.allow_provider_model_fallback, bool
        ):
            raise ValueError("allow_provider_model_fallback evidence must be bool or null")
        if not isinstance(self.final_assistant_end_turn, bool):
            raise ValueError("final_assistant_end_turn must be a boolean")
        served_fields = (
            self.served_model,
            self.served_reasoning_effort,
            self.response_attestation_digest,
            self.response_attestation_source,
        )
        if any(value is not None for value in served_fields) and not all(
            value is not None for value in served_fields
        ):
            raise ValueError("served identity requires a complete response-side attestation")
        if self.response_attestation_digest is None and (
            self.served_model is not None or self.served_reasoning_effort is not None
        ):
            raise ValueError("request context or self-report cannot populate served identity")
        if self.response_attestation_source is not None and self.response_attestation_source != (
            "app_server_response_model_verification"
        ):
            raise ValueError("served identity source is not authoritative")
        if self.accepted_config_digest is not None:
            if (
                self.accepted_model is None
                or self.accepted_provider is None
                or self.accepted_reasoning_effort is None
                or self.allow_provider_model_fallback is None
            ):
                raise ValueError("accepted config digest requires all accepted runtime fields")
            expected_accepted = accepted_runtime_config_digest(
                model=self.accepted_model,
                provider=self.accepted_provider,
                reasoning_effort=self.accepted_reasoning_effort,
                allow_provider_model_fallback=self.allow_provider_model_fallback,
            )
            if not hmac.compare_digest(self.accepted_config_digest, expected_accepted):
                raise ValueError("accepted config digest is not bound to its runtime fields")
        if self.artifact is not None:
            if type(self.artifact) is not CodexRunResult:
                raise ValueError("runtime artifact must be an exact CodexRunResult")
            if not all(value is not None for value in binding_values):
                raise ValueError("runtime Codex artifact requires complete slot bindings")
            audit = self.artifact.audit
            expected_reroute = audit.reroutes[-1][1] if audit.reroutes else None
            if (
                self.agent_id != self.artifact.protocol_execution_id
                or self.requested_model != self.artifact.requested_model
                or self.requested_provider != self.artifact.requested_provider
                or self.requested_reasoning_effort != self.artifact.requested_effort
                or self.accepted_model != audit.accepted_model
                or self.accepted_provider != audit.accepted_provider
                or self.accepted_reasoning_effort != audit.accepted_effort
                or self.allow_provider_model_fallback is not False
                or self.transcript_digest != self.artifact.transcript_hash
                or self.thread_id != self.artifact.thread_id
                or self.turn_id != self.artifact.turn_id
                or self.reroute_notice_model != expected_reroute
                or self.executable_provenance != self.artifact.executable_provenance
                or self.executable_lock_enforced
                is not self.artifact.executable_lock_enforced
                or self.standard_popen_used is not self.artifact.standard_popen_used
                or self.command_tail_policy_digest
                != self.artifact.command_tail_policy_digest
                or self.final_assistant_end_turn
                != (audit.final_message_count == 1 and self.artifact.output_text is not None)
            ):
                raise ValueError("runtime evidence differs from its Codex artifact")
            expected_raw = _raw_digest(self.artifact.output_text)
            if self.raw_response_digest != expected_raw:
                raise ValueError("runtime response digest differs from Codex output bytes")
            if not audit.model_identity_verified and any(
                value is not None
                for value in (
                    self.served_model,
                    self.served_reasoning_effort,
                    self.response_attestation_digest,
                    self.response_attestation_source,
                )
            ):
                raise ValueError("unattested Codex artifact cannot claim served identity")

    @property
    def accepted_config_complete(self) -> bool:
        return (
            self.accepted_model == self.requested_model
            and self.accepted_provider == self.requested_provider
            and self.accepted_reasoning_effort == self.requested_reasoning_effort
            and self.accepted_config_digest is not None
            and self.allow_provider_model_fallback is False
        )

    @property
    def artifact_derived(self) -> bool:
        return self.artifact is not None

    @property
    def identity_status(self) -> IdentityStatus:
        if self.reroute_notice_model is not None:
            return IdentityStatus.CONTRADICTED
        if self.artifact is None or not self.artifact.audit.model_identity_verified:
            return IdentityStatus.UNVERIFIED
        if self.response_attestation_digest is None:
            return IdentityStatus.UNVERIFIED
        if (
            self.final_assistant_end_turn
            and self.served_model == self.requested_model
            and self.served_reasoning_effort == self.requested_reasoning_effort
        ):
            return IdentityStatus.VERIFIED
        return IdentityStatus.CONTRADICTED


def accepted_runtime_config_digest(
    *,
    model: str,
    provider: str,
    reasoning_effort: str,
    allow_provider_model_fallback: bool,
) -> str:
    """Hash the accepted thread-start fields separately from served identity."""

    _text(model, "accepted model", max_bytes=128)
    _text(provider, "accepted provider", max_bytes=128)
    _text(reasoning_effort, "accepted reasoning effort", max_bytes=64)
    if not isinstance(allow_provider_model_fallback, bool):
        raise ValueError("accepted fallback setting must be a boolean")
    return _digest_json(
        {
            "allow_provider_model_fallback": allow_provider_model_fallback,
            "model": model,
            "provider": provider,
            "reasoning_effort": reasoning_effort,
        }
    )


def _artifact_tool_violation_count(artifact: CodexRunResult) -> int:
    prefixes = (
        "forbidden_",
        "unknown_item:",
        "unknown_raw_item:",
        "server_request:",
    )
    return sum(reason.startswith(prefixes) for reason in artifact.audit.reasons)


def _artifact_followup_count(artifact: CodexRunResult) -> int:
    turn_starts = sum(
        message.get("method") == "turn/started" for message in artifact.messages
    )
    return max(0, turn_starts - 1)


@dataclass(frozen=True, slots=True)
class IsolationEvidence:
    agent_id: str
    pair_id: int
    condition: Condition
    classification: IsolationClassification
    event_stream_digest: str | None
    audit_complete: bool
    tool_call_count: int
    followup_count: int
    policy_violation_count: int
    batch_digest: str | None = None
    transcript_digest: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    artifact: CodexRunResult | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _text(self.agent_id, "isolation agent id", max_bytes=256)
        _integer(self.pair_id, "isolation pair id", minimum=0)
        if not isinstance(self.condition, Condition):
            raise ValueError("isolation condition must be declared")
        if self.classification is not IsolationClassification.INSTRUCTION_BLINDED_EVENT_AUDITED:
            raise ValueError("evaluation agents may never be labeled capability-isolated")
        if not isinstance(self.audit_complete, bool):
            raise ValueError("audit_complete must be a boolean")
        if self.event_stream_digest is not None:
            _digest(self.event_stream_digest, "event stream digest")
        if self.audit_complete and self.event_stream_digest is None:
            raise ValueError("complete event audit requires an event-stream digest")
        _integer(self.tool_call_count, "tool call count", minimum=0)
        _integer(self.followup_count, "followup count", minimum=0)
        _integer(self.policy_violation_count, "policy violation count", minimum=0)
        binding_values = (
            self.batch_digest,
            self.transcript_digest,
            self.thread_id,
            self.turn_id,
        )
        if any(value is not None for value in binding_values) and not all(
            value is not None for value in binding_values
        ):
            raise ValueError("isolation artifact binding must be complete or absent")
        if self.batch_digest is not None:
            _digest(self.batch_digest, "isolation batch digest")
        if self.transcript_digest is not None:
            _digest(self.transcript_digest, "isolation transcript digest")
        for label, value in (("thread", self.thread_id), ("turn", self.turn_id)):
            if value is not None:
                _text(value, f"isolation {label} id", max_bytes=256)
        if self.artifact is not None:
            if type(self.artifact) is not CodexRunResult:
                raise ValueError("isolation artifact must be an exact CodexRunResult")
            if not all(value is not None for value in binding_values):
                raise ValueError("isolation Codex artifact requires complete slot bindings")
            if (
                self.agent_id != self.artifact.protocol_execution_id
                or self.event_stream_digest != self.artifact.transcript_hash
                or self.transcript_digest != self.artifact.transcript_hash
                or self.thread_id != self.artifact.thread_id
                or self.turn_id != self.artifact.turn_id
                or self.audit_complete is not True
                or self.tool_call_count != _artifact_tool_violation_count(self.artifact)
                or self.followup_count != _artifact_followup_count(self.artifact)
                or self.policy_violation_count != len(self.artifact.audit.reasons)
            ):
                raise ValueError("isolation evidence differs from its Codex artifact")

    @property
    def artifact_derived(self) -> bool:
        return self.artifact is not None

    def passes(self, policy: IsolationPolicy) -> bool:
        return (
            self.classification is policy.classification
            and self.audit_complete
            and self.event_stream_digest is not None
            and self.artifact_derived
            and self.artifact is not None
            and self.artifact.audit.eligible
            and self.tool_call_count <= policy.maximum_tool_calls
            and self.followup_count <= policy.maximum_followups
            and self.policy_violation_count <= policy.maximum_policy_violations
        )


def codex_evidence_for_batch(
    plan: EvaluationPlan,
    *,
    pair_id: int,
    condition: Condition,
    artifact: CodexRunResult,
) -> tuple[RuntimeEvidence, IsolationEvidence]:
    """Derive slot evidence from one self-checking raw Codex artifact."""

    if not isinstance(plan, EvaluationPlan) or type(artifact) is not CodexRunResult:
        raise ValueError("Codex evidence requires a sealed plan and exact run artifact")
    batch = plan.batch(pair_id, condition)
    if artifact.prompt_text != codex_prompt_for_batch(batch):
        raise ValueError("Codex prompt artifact differs from the sealed batch")
    if artifact.output_schema_json != codex_output_schema_json(batch):
        raise ValueError("Codex output-schema artifact differs from the sealed batch")
    if artifact.request_policy_material != _expected_codex_request_policy(plan.config):
        raise ValueError("Codex request policy differs from the sealed runtime policy")
    expected_tail_digest = app_server_command_tail_policy_digest(
        plan.config.runtime.requested_model,
        plan.config.runtime.requested_reasoning_effort,
    )
    if artifact.command_tail_policy_digest != expected_tail_digest:
        raise ValueError("Codex command-tail policy differs from the sealed plan")
    if (
        artifact.solver_process_isolation_policy_digest
        != solver_process_isolation_policy_digest()
    ):
        raise ValueError("Codex solver-process isolation policy differs from the sealed plan")
    expected_executable = plan.config.runtime.executable_provenance
    if (
        expected_executable is not None
        and artifact.executable_provenance != expected_executable
    ):
        raise ValueError("Codex executable provenance differs from the sealed plan")
    audit = artifact.audit
    accepted_digest = None
    if all(
        isinstance(value, str)
        for value in (audit.accepted_model, audit.accepted_provider, audit.accepted_effort)
    ):
        accepted_digest = accepted_runtime_config_digest(
            model=audit.accepted_model,
            provider=audit.accepted_provider,
            reasoning_effort=audit.accepted_effort,
            allow_provider_model_fallback=False,
        )
    reroute = audit.reroutes[-1][1] if audit.reroutes else None
    runtime = RuntimeEvidence(
        agent_id=artifact.protocol_execution_id,
        pair_id=pair_id,
        condition=condition,
        requested_model=artifact.requested_model,
        requested_provider=artifact.requested_provider,
        requested_reasoning_effort=artifact.requested_effort,
        accepted_model=audit.accepted_model,
        accepted_provider=audit.accepted_provider,
        accepted_reasoning_effort=audit.accepted_effort,
        accepted_config_digest=accepted_digest,
        allow_provider_model_fallback=False,
        served_model=None,
        served_reasoning_effort=None,
        response_attestation_digest=None,
        response_attestation_source=None,
        final_assistant_end_turn=(
            audit.final_message_count == 1 and artifact.output_text is not None
        ),
        executable_provenance=artifact.executable_provenance,
        executable_lock_enforced=artifact.executable_lock_enforced,
        standard_popen_used=artifact.standard_popen_used,
        command_tail_policy_digest=artifact.command_tail_policy_digest,
        reroute_notice_model=reroute,
        batch_digest=batch.digest,
        pack_digest=batch.pack_digest,
        treatment_digest=batch.treatment_digest,
        raw_response_digest=_raw_digest(artifact.output_text),
        transcript_digest=artifact.transcript_hash,
        thread_id=artifact.thread_id,
        turn_id=artifact.turn_id,
        artifact=artifact,
    )
    isolation = IsolationEvidence(
        agent_id=artifact.protocol_execution_id,
        pair_id=pair_id,
        condition=condition,
        classification=IsolationClassification.INSTRUCTION_BLINDED_EVENT_AUDITED,
        event_stream_digest=artifact.transcript_hash,
        audit_complete=True,
        tool_call_count=_artifact_tool_violation_count(artifact),
        followup_count=_artifact_followup_count(artifact),
        policy_violation_count=len(audit.reasons),
        batch_digest=batch.digest,
        transcript_digest=artifact.transcript_hash,
        thread_id=artifact.thread_id,
        turn_id=artifact.turn_id,
        artifact=artifact,
    )
    return runtime, isolation


def parse_codex_artifact_response(
    plan: EvaluationPlan,
    *,
    pair_id: int,
    condition: Condition,
    artifact: CodexRunResult,
) -> tuple[BatchResult, RuntimeEvidence, IsolationEvidence]:
    """Parse and bind one first-attempt Codex artifact without caller timing claims."""

    runtime, isolation = codex_evidence_for_batch(
        plan,
        pair_id=pair_id,
        condition=condition,
        artifact=artifact,
    )
    result = parse_batch_response(
        plan,
        pair_id=pair_id,
        condition=condition,
        raw=artifact.output_text,
        latency_seconds=float(artifact.elapsed_seconds),
        input_tokens=0,
        output_tokens=0,
    )
    return result, runtime, isolation


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    """Audit record for the exact per-case packet or explicit retrieval abstention."""

    assignment_digest: str
    case_id: str
    view_digest: str
    pair_id: int
    condition: Condition
    treatment_digest: str
    disposition: RetrievalDisposition
    packet_digest: str | None
    abstention_reason_digest: str | None

    def __post_init__(self) -> None:
        _digest(self.assignment_digest, "retrieval assignment digest")
        _text(self.case_id, "retrieval case id", max_bytes=128)
        _digest(self.view_digest, "retrieval view digest")
        _integer(self.pair_id, "retrieval pair id", minimum=0)
        if not isinstance(self.condition, Condition):
            raise ValueError("retrieval condition must be declared")
        _digest(self.treatment_digest, "retrieval treatment digest")
        if not isinstance(self.disposition, RetrievalDisposition):
            raise ValueError("retrieval disposition must be declared")
        if self.packet_digest is not None:
            _digest(self.packet_digest, "retrieval packet digest")
        if self.abstention_reason_digest is not None:
            _digest(self.abstention_reason_digest, "retrieval abstention reason digest")
        if self.condition is Condition.BRAIN_OFF:
            if (
                self.disposition is not RetrievalDisposition.EMPTY_SHAM
                or self.packet_digest != _digest_text("", "empty sham")
                or self.abstention_reason_digest is not None
            ):
                raise ValueError("brain-off retrieval evidence must be the hashed empty sham")
        elif self.disposition is RetrievalDisposition.PACKET:
            if self.packet_digest is None or self.abstention_reason_digest is not None:
                raise ValueError("retrieval packet requires only a packet digest")
        elif self.disposition is RetrievalDisposition.ABSTAINED:
            if self.packet_digest is not None or self.abstention_reason_digest is None:
                raise ValueError("retrieval abstention requires only a reason digest")
        else:
            raise ValueError("brain-on retrieval may not claim an empty-sham disposition")


def retrieval_evidence_for_plan(
    plan: EvaluationPlan,
) -> tuple[RetrievalEvidence, ...]:
    """Expose immutable evidence for the contexts already sealed into the plan."""

    packet_by_case = {packet.case_id: packet for packet in plan.retrieval_packets}
    records = []
    for assignment in plan.assignments:
        treatment = plan.batch(assignment.pair_id, assignment.condition).treatment_digest
        if assignment.condition is Condition.BRAIN_OFF:
            disposition = RetrievalDisposition.EMPTY_SHAM
            packet_digest = _digest_text("", "empty sham")
            reason_digest = None
        else:
            packet = packet_by_case[assignment.case_id]
            disposition = packet.disposition
            packet_digest = packet.packet_digest
            reason_digest = packet.abstention_reason_digest
        records.append(
            RetrievalEvidence(
                assignment_digest=assignment.digest,
                case_id=assignment.case_id,
                view_digest=assignment.view_digest,
                pair_id=assignment.pair_id,
                condition=assignment.condition,
                treatment_digest=treatment,
                disposition=disposition,
                packet_digest=packet_digest,
                abstention_reason_digest=reason_digest,
            )
        )
    return tuple(records)


@dataclass(frozen=True, slots=True)
class EvaluationRun:
    plan: EvaluationPlan
    observations: tuple[Observation, ...]
    runtime_evidence: tuple[RuntimeEvidence, ...]
    isolation_evidence: tuple[IsolationEvidence, ...]
    retrieval_evidence: tuple[RetrievalEvidence, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan, EvaluationPlan):
            raise ValueError("evaluation run requires a sealed plan")
        expected = tuple(assignment.digest for assignment in self.plan.assignments)
        observed = tuple(observation.assignment.digest for observation in self.observations)
        if observed != expected:
            raise ValueError("run observations must exactly match sealed assignment order")
        if len(set(observed)) != len(observed):
            raise ValueError("run contains duplicate observations")
        expected_slots = {
            (pair_id, condition)
            for pair_id in self.plan.spec.required_pair_ids
            for condition in Condition
        }
        for label, evidence in (
            ("runtime", self.runtime_evidence),
            ("isolation", self.isolation_evidence),
        ):
            slots = [(item.pair_id, item.condition) for item in evidence]
            if len(set(slots)) != len(slots):
                raise ValueError(f"run contains duplicate {label} evidence")
            if not set(slots) <= expected_slots:
                raise ValueError(f"run contains {label} evidence for an unknown slot")
        runtime_by_slot = {
            (item.pair_id, item.condition): item for item in self.runtime_evidence
        }
        isolation_by_slot = {
            (item.pair_id, item.condition): item for item in self.isolation_evidence
        }
        observations_by_slot: dict[tuple[int, Condition], list[Observation]] = defaultdict(list)
        for observation in self.observations:
            observations_by_slot[
                (observation.assignment.pair_id, observation.assignment.condition)
            ].append(observation)
        for slot, runtime in runtime_by_slot.items():
            if not runtime.artifact_derived:
                continue
            batch = self.plan.batch(*slot)
            if (
                runtime.batch_digest != batch.digest
                or runtime.pack_digest != batch.pack_digest
                or runtime.treatment_digest != batch.treatment_digest
                or runtime.artifact is None
                or runtime.artifact.prompt_text != codex_prompt_for_batch(batch)
                or runtime.artifact.output_schema_json != codex_output_schema_json(batch)
                or runtime.artifact.request_policy_material
                != _expected_codex_request_policy(self.plan.config)
                or runtime.artifact.command_tail_policy_digest
                != app_server_command_tail_policy_digest(
                    self.plan.config.runtime.requested_model,
                    self.plan.config.runtime.requested_reasoning_effort,
                )
                or runtime.artifact.solver_process_isolation_policy_digest
                != solver_process_isolation_policy_digest()
                or (
                    self.plan.config.runtime.executable_provenance is not None
                    and runtime.artifact.executable_provenance
                    != self.plan.config.runtime.executable_provenance
                )
            ):
                raise ValueError("runtime artifact is not bound to the sealed batch")
            response_digests = {
                observation.raw_response_digest
                for observation in observations_by_slot[slot]
            }
            if response_digests != {runtime.raw_response_digest}:
                raise ValueError("runtime artifact is not bound to the scored raw response")
            if {
                observation.latency_seconds
                for observation in observations_by_slot[slot]
            } != {float(runtime.artifact.elapsed_seconds)} or {
                (observation.input_tokens, observation.output_tokens)
                for observation in observations_by_slot[slot]
            } != {(0, 0)}:
                raise ValueError("runtime artifact is not bound to scored timing and token evidence")
        for slot, isolation in isolation_by_slot.items():
            if not isolation.artifact_derived:
                continue
            runtime = runtime_by_slot.get(slot)
            if (
                runtime is None
                or not runtime.artifact_derived
                or runtime.agent_id != isolation.agent_id
                or runtime.batch_digest != isolation.batch_digest
                or runtime.transcript_digest != isolation.transcript_digest
                or runtime.thread_id != isolation.thread_id
                or runtime.turn_id != isolation.turn_id
                or runtime.artifact is not isolation.artifact
            ):
                raise ValueError("isolation artifact differs from slot runtime evidence")
        retrieval_digests = [item.assignment_digest for item in self.retrieval_evidence]
        if len(set(retrieval_digests)) != len(retrieval_digests):
            raise ValueError("run contains duplicate retrieval evidence")
        if not set(retrieval_digests) <= set(expected):
            raise ValueError("run contains retrieval evidence for an unknown assignment")
        validate_matched(self.observations)

    @property
    def infrastructure_complete(self) -> bool:
        return all(
            observation.status is not AttemptStatus.INFRASTRUCTURE_FAILURE
            for observation in self.observations
        )


def _ground_truth(case: EvaluationCase, assignment: Assignment) -> GroundTruth:
    if assignment.variant is Variant.CLEAN:
        return GroundTruth(
            case_id=assignment.case_id,
            item_digest=assignment.item_digest,
            variant=assignment.variant,
            first_invalid_line=None,
            error_family=None,
        )
    return GroundTruth(
        case_id=assignment.case_id,
        item_digest=assignment.item_digest,
        variant=assignment.variant,
        first_invalid_line=case.fault_line,
        error_family=case.error_family,
    )


def finalize_run(
    plan: EvaluationPlan,
    *,
    batch_results: Iterable[BatchResult],
    runtime_evidence: Iterable[RuntimeEvidence] = (),
    isolation_evidence: Iterable[IsolationEvidence] = (),
    retrieval_evidence: Iterable[RetrievalEvidence] = (),
) -> EvaluationRun:
    """Materialize one and only one observation for every sealed assignment."""

    results: dict[tuple[int, Condition], BatchResult] = {}
    for result in batch_results:
        if not isinstance(result, BatchResult):
            raise ValueError("batch_results must contain BatchResult values")
        key = (result.pair_id, result.condition)
        if key in results:
            raise ValueError(f"duplicate batch result: {key}")
        expected_batch = plan.batch(*key)
        if (
            result.batch_digest != expected_batch.digest
            or result.pack_digest != expected_batch.pack_digest
            or result.treatment_digest != expected_batch.treatment_digest
        ):
            raise ValueError("batch result does not match the sealed plan")
        expected_assignments = plan.assignments_for(*key)
        if tuple(attempt.assignment_digest for attempt in result.attempts) != tuple(
            assignment.digest for assignment in expected_assignments
        ):
            raise ValueError("batch result attempts differ from sealed assignment order")
        for assignment, attempt in zip(expected_assignments, result.attempts, strict=True):
            if (
                attempt.case_id != assignment.case_id
                or not hmac.compare_digest(attempt.view_digest, assignment.view_digest)
            ):
                raise ValueError("batch result attempt identity differs from sealed assignment")
        results[key] = result

    observations_by_digest: dict[str, Observation] = {}
    for pair_id in plan.spec.required_pair_ids:
        for condition in Condition:
            key = (pair_id, condition)
            result = results.get(key)
            if result is None:
                result = failed_batch(
                    plan,
                    pair_id=pair_id,
                    condition=condition,
                    status=AttemptStatus.MISSING,
                    evidence_digest=_digest_json(
                        {
                            "condition": condition.value,
                            "pair_id": pair_id,
                            "reason": "missing_batch",
                        }
                    ),
                    unblinded=False,
                )
            assignments = plan.assignments_for(pair_id, condition)
            for assignment, attempt in zip(assignments, result.attempts, strict=True):
                case = _bound_case(plan, assignment)
                repair_receipt = None
                witness_receipt = None
                if attempt.answer is not None and attempt.answer.verdict is Verdict.INVALID:
                    repair_receipt = verify_repair(plan, assignment, attempt.answer)
                    witness_receipt = verify_witness(plan, assignment, attempt.answer)
                observation = Observation(
                    assignment=assignment,
                    truth=_ground_truth(case, assignment),
                    status=attempt.status,
                    answer=attempt.answer,
                    repair_receipt=repair_receipt,
                    witness_receipt=witness_receipt,
                    failure_code=attempt.failure_code,
                    raw_response_digest=result.raw_response_digest,
                    latency_seconds=result.latency_seconds,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
                if assignment.digest in observations_by_digest:
                    raise ValueError("duplicate observation materialization")
                observations_by_digest[assignment.digest] = observation
    ordered = tuple(observations_by_digest[assignment.digest] for assignment in plan.assignments)
    return EvaluationRun(
        plan=plan,
        observations=ordered,
        runtime_evidence=tuple(runtime_evidence),
        isolation_evidence=tuple(isolation_evidence),
        retrieval_evidence=tuple(retrieval_evidence),
    )


@dataclass(frozen=True, slots=True)
class ObservationScore:
    validity: float
    localization: float | None
    repair: float
    witness: float
    family: float | None
    brier: float


def _receipt_credit(
    observation: Observation,
    receipt: VerifierReceipt | None,
    kind: VerifierKind,
    scorer_digest: str,
    plan: EvaluationPlan | None,
) -> float:
    if receipt is None:
        return 0.0
    if (
        receipt.kind is not kind
        or receipt.assignment_digest != observation.assignment.digest
        or observation.answer is None
        or receipt.answer_digest != observation.answer.digest
        or receipt.view_digest != observation.assignment.view_digest
        or receipt.scorer_digest != scorer_digest
    ):
        raise ValueError("verifier receipt binding differs from scoring context")
    # A receipt is an immutable replay record, not an authority token.  Its seal is
    # deliberately reproducible and therefore cannot authenticate a caller assertion.
    # Credit is granted only after replaying the hidden verifier against the sealed plan.
    if plan is None:
        return 0.0
    expected = (
        verify_repair(plan, observation.assignment, observation.answer)
        if kind is VerifierKind.REPAIR
        else verify_witness(plan, observation.assignment, observation.answer)
    )
    if receipt != expected:
        raise ValueError("verifier receipt differs from fresh sealed-plan replay")
    return float(expected.passed)


def score_observation(
    observation: Observation,
    config: EvaluationConfig,
    *,
    plan: EvaluationPlan | None = None,
) -> ObservationScore:
    truth_invalid = observation.truth.variant is Variant.FAULTY
    if observation.status is not AttemptStatus.SUCCESS or observation.answer is None:
        return ObservationScore(
            validity=0.0,
            localization=0.0 if truth_invalid else None,
            repair=0.0,
            witness=0.0,
            family=0.0 if truth_invalid else None,
            brier=1.0,
        )
    answer = observation.answer
    predicted_invalid = answer.verdict is Verdict.INVALID
    validity = float(truth_invalid == predicted_invalid)
    brier = (answer.probability_invalid - float(truth_invalid)) ** 2
    if truth_invalid:
        localization = float(
            predicted_invalid
            and answer.first_invalid_line == observation.truth.first_invalid_line
        )
        family = float(
            predicted_invalid and answer.error_family == observation.truth.error_family
        )
    else:
        localization = None
        family = None
    return ObservationScore(
        validity=validity,
        localization=localization,
        repair=(
            _receipt_credit(
                observation,
                observation.repair_receipt,
                VerifierKind.REPAIR,
                config.scorer_digest,
                plan,
            )
            if truth_invalid and predicted_invalid
            else 0.0
        ),
        witness=(
            _receipt_credit(
                observation,
                observation.witness_receipt,
                VerifierKind.WITNESS,
                config.scorer_digest,
                plan,
            )
            if truth_invalid and predicted_invalid
            else 0.0
        ),
        family=family,
        brier=brier,
    )


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return math.fsum(materialized) / len(materialized) if materialized else 0.0


def _macro_mean(rows: Iterable[tuple[tuple[str, ...], float]]) -> float:
    groups: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for key, value in rows:
        groups[key].append(value)
    return _mean(_mean(values) for values in groups.values())


@dataclass(frozen=True, slots=True)
class AggregateScore:
    primary: float
    balanced_validity: float
    localization: float
    repair: float
    witness: float
    family: float
    clean_specificity: float
    brier: float
    observation_count: int


def aggregate(
    observations: Iterable[Observation],
    config: EvaluationConfig,
    *,
    plan: EvaluationPlan | None = None,
) -> AggregateScore:
    items = list(observations)
    if not items:
        raise ValueError("at least one observation is required")
    logical_rows = [
        (
            item.assignment.pair_id,
            item.assignment.condition,
            item.assignment.case_id,
        )
        for item in items
    ]
    if len(set(logical_rows)) != len(logical_rows):
        raise ValueError("aggregate rejects duplicated favorable or unfavorable rows")
    scored = [
        (item, score_observation(item, config, plan=plan))
        for item in items
    ]
    clean_validity = _macro_mean(
        ((item.assignment.domain, item.assignment.difficulty), score.validity)
        for item, score in scored
        if item.truth.variant is Variant.CLEAN
    )
    faulty_validity = _macro_mean(
        (
            (
                item.assignment.domain,
                item.assignment.difficulty,
                item.truth.error_family or "",
            ),
            score.validity,
        )
        for item, score in scored
        if item.truth.variant is Variant.FAULTY
    )
    balanced_validity = (clean_validity + faulty_validity) / 2
    localization = _macro_mean(
        (
            (
                item.assignment.domain,
                item.assignment.difficulty,
                item.truth.error_family or "",
            ),
            score.localization,
        )
        for item, score in scored
        if score.localization is not None
    )
    repair = _macro_mean(
        (
            (
                item.assignment.domain,
                item.assignment.difficulty,
                item.truth.error_family or "",
            ),
            score.repair,
        )
        for item, score in scored
        if item.truth.variant is Variant.FAULTY
    )
    witness = _macro_mean(
        (
            (
                item.assignment.domain,
                item.assignment.difficulty,
                item.truth.error_family or "",
            ),
            score.witness,
        )
        for item, score in scored
        if item.truth.variant is Variant.FAULTY
    )
    family = _macro_mean(
        (
            (
                item.assignment.domain,
                item.assignment.difficulty,
                item.truth.error_family or "",
            ),
            score.family,
        )
        for item, score in scored
        if score.family is not None
    )
    weights = config.weights
    primary = (
        weights.balanced_validity * balanced_validity
        + weights.localization * localization
        + weights.repair * repair
        + weights.witness * witness
        + weights.error_family * family
    )
    return AggregateScore(
        primary=primary,
        balanced_validity=balanced_validity,
        localization=localization,
        repair=repair,
        witness=witness,
        family=family,
        clean_specificity=clean_validity,
        brier=_macro_mean(
            (
                (
                    item.assignment.domain,
                    item.assignment.difficulty,
                    item.truth.variant.value,
                    item.truth.error_family or "",
                ),
                score.brier,
            )
            for item, score in scored
        ),
        observation_count=len(items),
    )


def validate_matched(observations: Iterable[Observation]) -> None:
    groups: dict[tuple[int, str], dict[Condition, Observation]] = defaultdict(dict)
    for item in observations:
        key = (item.assignment.pair_id, item.truth.case_id)
        if item.assignment.condition in groups[key]:
            raise ValueError(f"duplicate condition for pair/case: {key}")
        groups[key][item.assignment.condition] = item
    for key, conditions in groups.items():
        if set(conditions) != set(Condition):
            raise ValueError(f"unmatched pair/case: {key}")
        on = conditions[Condition.BRAIN_ON]
        off = conditions[Condition.BRAIN_OFF]
        if (
            on.truth != off.truth
            or on.assignment.view_digest != off.assignment.view_digest
            or on.assignment.order_index != off.assignment.order_index
            or on.assignment.domain != off.assignment.domain
            or on.assignment.difficulty != off.assignment.difficulty
        ):
            raise ValueError(f"conditions differ on matched sealed inputs: {key}")


def pair_deltas(
    observations: Iterable[Observation],
    config: EvaluationConfig,
    *,
    plan: EvaluationPlan | None = None,
) -> dict[int, float]:
    items = list(observations)
    validate_matched(items)
    by_pair: dict[int, dict[Condition, list[Observation]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in items:
        by_pair[item.assignment.pair_id][item.assignment.condition].append(item)
    return {
        pair_id: aggregate(
            conditions[Condition.BRAIN_ON], config, plan=plan
        ).primary
        - aggregate(
            conditions[Condition.BRAIN_OFF], config, plan=plan
        ).primary
        for pair_id, conditions in sorted(by_pair.items())
    }


@dataclass(frozen=True, slots=True)
class InferenceResult:
    method: str
    estimand: str
    observed_mean_delta: float
    randomization_p: float
    bootstrap_interval: tuple[float, float]
    bootstrap_repetitions: int
    pair_count: int


def _paired_bootstrap_interval(
    deltas: Sequence[float],
    *,
    config: EvaluationConfig,
    seed: str,
) -> tuple[float, float]:
    if len(deltas) < 2:
        raise ValueError("paired bootstrap requires at least two matched pairs")
    sampler = HashSampler(_derived_seed(seed, "fixed-case-paired-bootstrap"))
    samples = []
    for _ in range(config.inference.bootstrap_repetitions):
        samples.append(_mean(deltas[sampler.below(len(deltas))] for _ in deltas))
    samples.sort()
    tail = (1 - config.inference.confidence_level) / 2
    low_index = max(0, math.floor(tail * len(samples)))
    high_index = min(len(samples) - 1, math.ceil((1 - tail) * len(samples)) - 1)
    return samples[low_index], samples[high_index]


def _exact_sign_flip_p(deltas: Sequence[float]) -> float:
    if not deltas:
        raise ValueError("sign-flip test requires paired deltas")
    fractions = tuple(Fraction(str(value)) for value in deltas)
    observed_sum = sum(fractions, Fraction())
    extreme = 0
    total = 1 << len(fractions)
    for mask in range(total):
        candidate = sum(
            (value if mask & (1 << index) else -value)
            for index, value in enumerate(fractions)
        )
        if candidate >= observed_sum:
            extreme += 1
    return extreme / total


def paired_inference(run: EvaluationRun) -> InferenceResult:
    config = run.plan.config
    deltas_by_pair = pair_deltas(run.observations, config, plan=run.plan)
    if tuple(deltas_by_pair) != run.plan.spec.required_pair_ids:
        raise ValueError("inference pair universe differs from run spec")
    deltas = tuple(deltas_by_pair.values())
    if len(deltas) > config.inference.maximum_exact_pairs:
        raise ValueError("pair count exceeds exact sign-flip enumeration budget")
    interval = _paired_bootstrap_interval(
        deltas,
        config=config,
        seed=run.plan.seeds.bootstrap,
    )
    return InferenceResult(
        method="exact_paired_sign_flip_plus_fixed_case_paired_bootstrap",
        estimand=(
            "mean brain-on minus brain-off score across the preregistered pairs "
            "for this fixed sealed case universe"
        ),
        observed_mean_delta=_mean(deltas),
        randomization_p=_exact_sign_flip_p(deltas),
        bootstrap_interval=interval,
        bootstrap_repetitions=config.inference.bootstrap_repetitions,
        pair_count=len(deltas),
    )


@dataclass(frozen=True, slots=True)
class BenefitDecision:
    helped: bool
    primary_delta: float
    pooled_primary_delta: float
    inference: InferenceResult
    nonnegative_pairs: int
    specificity_loss: float
    worst_tier_regression: float
    failed_gates: tuple[str, ...]

    @property
    def status(self) -> str:
        return benefit_status(self.failed_gates)

    @property
    def ineligible_gates(self) -> tuple[str, ...]:
        return tuple(gate for gate in self.failed_gates if gate not in OUTCOME_GATES)


# helped=False used to mean either "the brain did not help" or "this run cannot say" (scheduled audit
# B08-R79-STRICT-BENEFIT-VERDICT-UNREACHABLE-20260929). These are the gates a run fails because of the effect
# it measured; every other gate, including any added later, says the evidence cannot support a verdict.
# On the current app-server transport two evidence gates fail whatever a run does: the output budget is sealed
# as unenforced (the request can neither carry nor attest max_output_tokens), and the runner never verifies
# the served model (its events carry no response-side attestation). Until a transport attests both, every
# strict verdict is "ineligible", and the paired statistics are descriptive only.
OUTCOME_GATES = frozenset(
    {
        "primary_delta",
        "bootstrap_lower_bound",
        "exact_randomization_p",
        "nonnegative_pairs",
        "clean_specificity",
        "tier_regression",
    }
)
BENEFIT_STATUSES = ("helped", "not_shown", "ineligible")


def benefit_status(failed_gates: tuple[str, ...]) -> str:
    """helped: every gate passed; not_shown: the evidence held and the effect missed a gate;
    ineligible: an evidence gate failed, so the run cannot say whether the brain helped."""

    if not failed_gates:
        return "helped"
    if all(gate in OUTCOME_GATES for gate in failed_gates):
        return "not_shown"
    return "ineligible"


def decide_benefit(run: EvaluationRun) -> BenefitDecision:
    """Apply only the executable, config-hashed pre-registered gates."""

    config = run.plan.config
    if run.plan.spec.config_digest != config.digest:
        raise ValueError("run config differs from sealed run spec")
    on = [
        item
        for item in run.observations
        if item.assignment.condition is Condition.BRAIN_ON
    ]
    off = [
        item
        for item in run.observations
        if item.assignment.condition is Condition.BRAIN_OFF
    ]
    on_score = aggregate(on, config, plan=run.plan)
    off_score = aggregate(off, config, plan=run.plan)
    pooled_delta = on_score.primary - off_score.primary
    inference = paired_inference(run)
    # The independent experimental unit is the fresh matched pair.  The gate and
    # attached inference therefore use the same preregistered mean-pair estimand.
    delta = inference.observed_mean_delta
    deltas = pair_deltas(run.observations, config, plan=run.plan)
    nonnegative = sum(value >= 0 for value in deltas.values())
    specificity_loss = off_score.clean_specificity - on_score.clean_specificity
    tiers: dict[tuple[str, str], dict[Condition, list[Observation]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in run.observations:
        tiers[(item.assignment.domain, item.assignment.difficulty)][
            item.assignment.condition
        ].append(item)
    regressions = [
        aggregate(conditions[Condition.BRAIN_OFF], config, plan=run.plan).primary
        - aggregate(conditions[Condition.BRAIN_ON], config, plan=run.plan).primary
        for conditions in tiers.values()
        if set(conditions) == set(Condition)
    ]
    worst_regression = max(regressions, default=0.0)
    failed = []
    gate = config.benefit_gate
    if len(deltas) < 10 or tuple(deltas) != run.plan.spec.required_pair_ids:
        failed.append("full_pair_universe")
    if delta < gate.minimum_primary_delta:
        failed.append("primary_delta")
    if inference.bootstrap_interval[0] <= 0:
        failed.append("bootstrap_lower_bound")
    if inference.randomization_p > gate.maximum_randomization_p:
        failed.append("exact_randomization_p")
    if nonnegative < gate.minimum_nonnegative_pairs:
        failed.append("nonnegative_pairs")
    if specificity_loss > gate.maximum_clean_specificity_loss:
        failed.append("clean_specificity")
    if worst_regression > gate.maximum_domain_tier_regression:
        failed.append("tier_regression")
    if not run.infrastructure_complete:
        failed.append("infrastructure_incomplete")

    expected_slots = {
        (pair_id, condition)
        for pair_id in run.plan.spec.required_pair_ids
        for condition in Condition
    }
    runtime_by_slot = {
        (item.pair_id, item.condition): item for item in run.runtime_evidence
    }
    if set(runtime_by_slot) != expected_slots:
        failed.append("runtime_evidence_incomplete")
    else:
        if any(not item.artifact_derived for item in run.runtime_evidence):
            failed.append("runtime_artifact_binding")
        artifacts = [
            item.artifact for item in run.runtime_evidence if item.artifact is not None
        ]
        if (
            len({item.agent_id for item in run.runtime_evidence}) != len(expected_slots)
            or len({artifact.protocol_execution_id for artifact in artifacts})
            != len(expected_slots)
            or len(
                {
                    (
                        artifact.runtime_directory_device,
                        artifact.runtime_directory_inode,
                    )
                    for artifact in artifacts
                }
            )
            != len(expected_slots)
        ):
            failed.append("agents_not_unique")
        if len(artifacts) == len(expected_slots):
            failed.append("output_token_budget_unenforced")
        if any(
            item.requested_model != run.plan.spec.requested_model
            or item.requested_provider != run.plan.spec.requested_provider
            or item.requested_reasoning_effort
            != run.plan.spec.requested_reasoning_effort
            or not item.accepted_config_complete
            for item in run.runtime_evidence
        ):
            failed.append("accepted_runtime_config")
        if any(
            item.identity_status is not IdentityStatus.VERIFIED
            for item in run.runtime_evidence
        ):
            failed.append("served_identity")
        expected_tail_digest = app_server_command_tail_policy_digest(
            config.runtime.requested_model,
            config.runtime.requested_reasoning_effort,
        )
        if any(
            item.command_tail_policy_digest != expected_tail_digest
            for item in run.runtime_evidence
        ):
            failed.append("command_tail_policy")
        if EXECUTABLE_IMAGE_BINDING_POLICY != "windows_deny_write_delete_lease_v1":
            failed.append("unsupported_executable_image_binding_policy")
        if any(
            item.executable_lock_enforced is not True
            for item in run.runtime_evidence
        ):
            failed.append("executable_image_binding_unenforced")
        if any(
            item.standard_popen_used is not True for item in run.runtime_evidence
        ):
            failed.append("nonstandard_process_factory")
        expected_solver_policy = solver_process_isolation_policy_digest()
        if len(artifacts) != len(expected_slots) or any(
            artifact.solver_process_isolation_policy_digest != expected_solver_policy
            or artifact.codex_home_initial_auth_only is not True
            or artifact.codex_home_cleanup_verified is not True
            or artifact.credential_leak_scan_passed is not True
            for artifact in artifacts
        ):
            failed.append("solver_process_isolation")
        if len(artifacts) == len(expected_slots) and len(
            {
                (
                    artifact.codex_home_directory_device,
                    artifact.codex_home_directory_inode,
                )
                for artifact in artifacts
            }
        ) != len(expected_slots):
            failed.append("codex_homes_not_unique")
        expected_executable = config.runtime.executable_provenance
        if expected_executable is None:
            failed.append("executable_provenance_unsealed")
        elif any(
            item.executable_provenance != expected_executable
            for item in run.runtime_evidence
        ):
            failed.append("executable_provenance")

    isolation_by_slot = {
        (item.pair_id, item.condition): item for item in run.isolation_evidence
    }
    if set(isolation_by_slot) != expected_slots:
        failed.append("isolation_evidence_incomplete")
    else:
        isolation_failed = False
        for slot, item in isolation_by_slot.items():
            runtime = runtime_by_slot.get(slot)
            if runtime is None or item.agent_id != runtime.agent_id:
                isolation_failed = True
                break
            if not item.passes(config.isolation):
                isolation_failed = True
                break
        if isolation_failed:
            failed.append("isolation_policy")
    retrieval_by_assignment = {
        item.assignment_digest: item for item in run.retrieval_evidence
    }
    expected_assignment_digests = {
        assignment.digest for assignment in run.plan.assignments
    }
    if set(retrieval_by_assignment) != expected_assignment_digests:
        failed.append("retrieval_evidence_incomplete")
    else:
        retrieval_failed = False
        for assignment in run.plan.assignments:
            item = retrieval_by_assignment[assignment.digest]
            batch = run.plan.batch(assignment.pair_id, assignment.condition)
            if (
                item.case_id != assignment.case_id
                or item.view_digest != assignment.view_digest
                or item.pair_id != assignment.pair_id
                or item.condition is not assignment.condition
                or item.treatment_digest != batch.treatment_digest
            ):
                retrieval_failed = True
                break
        if retrieval_failed:
            failed.append("retrieval_evidence_binding")
    return BenefitDecision(
        helped=not failed,
        primary_delta=delta,
        pooled_primary_delta=pooled_delta,
        inference=inference,
        nonnegative_pairs=nonnegative,
        specificity_loss=specificity_loss,
        worst_tier_regression=worst_regression,
        failed_gates=tuple(dict.fromkeys(failed)),
    )


__all__ = [
    "AggregateScore",
    "Assignment",
    "AttemptStatus",
    "BatchResult",
    "BenefitDecision",
    "BenefitGateConfig",
    "CaseAttempt",
    "Condition",
    "DesignSeeds",
    "EvaluationCase",
    "EvaluationConfig",
    "EvaluationPlan",
    "EvaluationRun",
    "GroundTruth",
    "HashSampler",
    "IdentityStatus",
    "InferenceConfig",
    "InferenceResult",
    "IsolationClassification",
    "IsolationEvidence",
    "IsolationPolicy",
    "Observation",
    "ObservationScore",
    "PairDispatch",
    "RepairProposal",
    "ResponseLimits",
    "RetrievalDisposition",
    "RetrievalEvidence",
    "RetrievalPacket",
    "RunSpec",
    "RuntimeEvidence",
    "RuntimePolicy",
    "ScoreWeights",
    "SeedCommitments",
    "SolverAnswer",
    "SolverBatch",
    "SolverBatchCase",
    "Variant",
    "Verdict",
    "VerifierKind",
    "VerifierReceipt",
    "WitnessProposal",
    "accepted_runtime_config_digest",
    "aggregate",
    "batch_output_schema",
    "benefit_status",
    "build_full_plan",
    "codex_evidence_for_batch",
    "codex_output_schema_json",
    "codex_prompt_for_batch",
    "decide_benefit",
    "failed_batch",
    "finalize_run",
    "pair_deltas",
    "paired_inference",
    "parse_codex_artifact_response",
    "parse_batch_response",
    "replace_pre_unblinding_infrastructure_failure",
    "retrieval_evidence_for_plan",
    "score_observation",
    "validate_matched",
    "verify_repair",
    "verify_witness",
]
