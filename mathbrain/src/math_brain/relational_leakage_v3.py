"""Grouped solver-view audit for relational shortcut leakage.

The older shortcut checks scored each candidate line in isolation.  This audit
instead joins the public alias legend to the aliases used by every candidate
line.  Labels are used only to fit models on non-held-out generator groups and
to score their predictions; held-out features come exclusively from the prompt
and numbered trace supplied to a solver.

The implementation is intentionally standard-library and deterministic.  It is
not a claim that one classifier exhausts the shortcut surface.  Passing it is a
necessary release condition for a successor hard bank, never sufficient proof
that the bank is leak-free.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations, pairwise

from .models import canonical_json

RELATIONAL_LEAKAGE_SCHEMA_V3 = "org.math-research-brain/relational-leakage-report"
RELATIONAL_LEAKAGE_SCHEMA_VERSION_V3 = 3
RELATIONAL_SOLVER_FEATURE_PROFILE_V3 = "solver-public-coefficient-prompt-v6"
RELATIONAL_SOLVER_VIEW_SCHEMA_V3 = "org.math-research-brain/relational-solver-view"
RELATIONAL_SOLVER_VIEW_SCHEMA_VERSION_V3 = 1
RELATIONAL_GROUP_MANIFEST_SCHEMA_V3 = (
    "org.math-research-brain/relational-group-manifest"
)
RELATIONAL_GROUP_MANIFEST_SCHEMA_VERSION_V3 = 1
DEFAULT_MAXIMUM_FAULT_LINE_ACCURACY_V3 = 0.15
DEFAULT_MAXIMUM_MECHANISM_ACCURACY_V3 = 0.55
MAX_RELATIONAL_CANDIDATE_LINES_V3 = 64
MAX_RELATIONAL_GEOMETRY_ROWS_V3 = 16
MAX_RELATIONAL_PROMPT_BYTES_V3 = 256 * 1024
MAX_RELATIONAL_LINE_BYTES_V3 = 16 * 1024
MAX_RELATIONAL_NUMBER_CHARS_V3 = 256
MAX_RELATIONAL_GROUPS_V3 = 128
MAX_RELATIONAL_CASES_PER_GROUP_V3 = 4096
MAX_RELATIONAL_AFFINE_TOKENS_V3 = 4096
MAX_RELATIONAL_AFFINE_DEPTH_V3 = 64
MAX_RELATIONAL_AFFINE_TERMS_V3 = 64
MAX_RELATIONAL_RATIONAL_BITS_V3 = 4096
MIN_RELATIONAL_BOUND_FEATURE_SUPPORT_V3 = 8
MIN_RELATIONAL_COMPLETE_PROMPT_SUPPORT_V3 = 4

_BOUND_FEATURE_PREFIXES_V3 = (
    "bound-affine-",
    "bound-line-",
    "bound-target-",
    "ordered-candidate-surface::",
    "ordered-declaration-sequence::",
    "ordered-exact-coefficient-row::",
    "ordered-legend-surface::",
)

_ALIAS_PATTERN = r"v-[0-9]{2}(?![A-Za-z0-9_])"
_ALIAS_RE = re.compile(_ALIAS_PATTERN)
_ALIAS_LIKE_RE = re.compile(r"\bv-[A-Za-z0-9_]+")
_TARGET_RE = re.compile(rf"\bclaim ({_ALIAS_PATTERN})\b")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:/\d+)?")
_SPACE_RE = re.compile(r"\s+")
_DECLARATION_RE = re.compile(r"=\s*([-+]?\d+(?:/\d+)?)\s*;\s*basis\b")
_PUBLIC_TOKEN_RE = re.compile(
    rf"{_ALIAS_PATTERN}|[-+]?\d+(?:/\d+)?|[A-Za-z][A-Za-z0-9_-]{{0,63}}|[^\s]"
)
_AFFINE_TOKEN_RE = re.compile(rf"{_ALIAS_PATTERN}|\d+(?:/\d+)?|[()+\-*/]")
_ROOT_HINT_RE = re.compile(
    r"(?i)\b(?:fault|invalid|root)(?:[-_ ]?(?:line|position))?\s*[:=]\s*(\d{1,3})\b"
)
_MECHANISM_HINT_RE = re.compile(
    r"(?i)\bmechanism\s*[:=]\s*([A-Za-z][A-Za-z0-9_-]{0,63})\b"
)


def _plain_text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be a nonempty plain string")
    return value


def _bounded_rate(value: object, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{label} must be a finite rate")
    return float(value)


def _canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validated_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _validate_candidate_lines(
    candidate_lines: object,
) -> tuple[tuple[int, str], ...]:
    if (
        type(candidate_lines) is not tuple
        or not 2 <= len(candidate_lines) <= MAX_RELATIONAL_CANDIDATE_LINES_V3
    ):
        raise ValueError("relational solver-view candidate count is outside its cap")
    line_numbers: list[int] = []
    for item in candidate_lines:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("candidate lines must be exact (number, text) tuples")
        line_number, line = item
        if type(line_number) is not int or line_number <= 0:
            raise ValueError("candidate line numbers must be positive exact integers")
        _plain_text(line, "candidate line")
        if len(line.encode("utf-8")) > MAX_RELATIONAL_LINE_BYTES_V3:
            raise ValueError("relational candidate line exceeds its byte cap")
        line_numbers.append(line_number)
    if len(set(line_numbers)) != len(line_numbers) or line_numbers != sorted(line_numbers):
        raise ValueError("candidate line numbers must be unique and increasing")
    return candidate_lines


@dataclass(frozen=True, slots=True)
class RelationalSolverViewV3:
    """The prompt and candidate-line capability currently exposed to extraction."""

    prompt: str
    candidate_lines: tuple[tuple[int, str], ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RelationalSolverViewV3:
            raise ValueError("relational solver-view subclasses are forbidden")
        _plain_text(self.prompt, "relational solver-view prompt")
        if len(self.prompt.encode("utf-8")) > MAX_RELATIONAL_PROMPT_BYTES_V3:
            raise ValueError("relational solver-view prompt exceeds its byte cap")
        _validate_candidate_lines(self.candidate_lines)
        object.__setattr__(
            self,
            "content_digest",
            _canonical_digest(
                {
                    "candidate_lines": [list(item) for item in self.candidate_lines],
                    "prompt": self.prompt,
                    "schema": RELATIONAL_SOLVER_VIEW_SCHEMA_V3,
                    "schema_version": RELATIONAL_SOLVER_VIEW_SCHEMA_VERSION_V3,
                }
            ),
        )
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RelationalSolverViewV3:
            raise ValueError("relational solver-view subclasses are forbidden")
        _plain_text(self.prompt, "relational solver-view prompt")
        if len(self.prompt.encode("utf-8")) > MAX_RELATIONAL_PROMPT_BYTES_V3:
            raise ValueError("relational solver-view prompt exceeds its byte cap")
        _validate_candidate_lines(self.candidate_lines)
        supplied = _validated_digest(self.content_digest, "relational solver-view digest")
        expected = _canonical_digest(
            {
                "candidate_lines": [list(item) for item in self.candidate_lines],
                "prompt": self.prompt,
                "schema": RELATIONAL_SOLVER_VIEW_SCHEMA_V3,
                "schema_version": RELATIONAL_SOLVER_VIEW_SCHEMA_VERSION_V3,
            }
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("relational solver-view digest does not bind its public bytes")


def _feature_payload(
    *,
    solver_view_digest: str,
    line_features: tuple[tuple[int, frozenset[str]], ...],
    case_features: frozenset[str],
) -> dict[str, object]:
    return {
        "case_features": sorted(case_features),
        "feature_profile": RELATIONAL_SOLVER_FEATURE_PROFILE_V3,
        "line_features": [
            [line_number, sorted(features)] for line_number, features in line_features
        ],
        "solver_view_digest": solver_view_digest,
    }


def _model_feature_payload(
    *,
    line_features: tuple[tuple[int, frozenset[str]], ...],
    case_features: frozenset[str],
) -> dict[str, object]:
    return {
        "case_features": sorted(case_features),
        "feature_profile": RELATIONAL_SOLVER_FEATURE_PROFILE_V3,
        "line_features": [
            [line_number, sorted(features)] for line_number, features in line_features
        ],
    }


@dataclass(frozen=True, slots=True)
class RelationalSolverFeaturesV3:
    """Immutable public-only feature bundle actually consumed by the models."""

    solver_view_digest: str
    line_features: tuple[tuple[int, frozenset[str]], ...]
    case_features: frozenset[str]
    model_content_digest: str = field(init=False)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RelationalSolverFeaturesV3:
            raise ValueError("relational solver-feature subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "model_content_digest",
            _canonical_digest(
                _model_feature_payload(
                    line_features=self.line_features,
                    case_features=self.case_features,
                )
            ),
        )
        object.__setattr__(
            self,
            "content_digest",
            _canonical_digest(
                _feature_payload(
                    solver_view_digest=self.solver_view_digest,
                    line_features=self.line_features,
                    case_features=self.case_features,
                )
            ),
        )
        self.validate_current()

    def _validate_fields(self) -> None:
        _validated_digest(self.solver_view_digest, "relational feature solver view")
        if type(self.line_features) is not tuple or len(self.line_features) < 2:
            raise ValueError("relational features require at least two line rows")
        numbers: list[int] = []
        for item in self.line_features:
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("relational line features must be exact pairs")
            line_number, features = item
            if type(line_number) is not int or line_number <= 0:
                raise ValueError("relational feature line numbers must be positive integers")
            if type(features) is not frozenset or not all(
                type(feature) is str and feature for feature in features
            ):
                raise ValueError("relational line feature rows must be exact string frozensets")
            numbers.append(line_number)
        if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
            raise ValueError("relational feature line numbers must be unique and increasing")
        if type(self.case_features) is not frozenset or not all(
            type(feature) is str and feature for feature in self.case_features
        ):
            raise ValueError("relational case features must be an exact string frozenset")

    def validate_current(self) -> None:
        if type(self) is not RelationalSolverFeaturesV3:
            raise ValueError("relational solver-feature subclasses are forbidden")
        self._validate_fields()
        supplied_model = _validated_digest(
            self.model_content_digest, "relational model-feature digest"
        )
        expected_model = _canonical_digest(
            _model_feature_payload(
                line_features=self.line_features,
                case_features=self.case_features,
            )
        )
        if not hmac.compare_digest(supplied_model, expected_model):
            raise ValueError("relational model-feature digest is inconsistent")
        supplied = _validated_digest(self.content_digest, "relational solver-feature digest")
        expected = _canonical_digest(
            _feature_payload(
                solver_view_digest=self.solver_view_digest,
                line_features=self.line_features,
                case_features=self.case_features,
            )
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("relational feature digest does not bind its public features")

    def features_for_line(self, line_number: int) -> frozenset[str]:
        if type(line_number) is not int:
            raise ValueError("relational feature lookup requires an exact line number")
        for candidate_number, features in self.line_features:
            if candidate_number == line_number:
                return features
        raise ValueError("relational feature lookup line is absent")


def _group_entry_payload(
    *,
    group: str,
    group_binding_digest: str,
    case_binding_digests: tuple[str, ...],
    solver_view_digests: tuple[str, ...],
) -> dict[str, object]:
    return {
        "cases": [
            {
                "case_binding_digest": case_digest,
                "solver_view_digest": view_digest,
            }
            for case_digest, view_digest in zip(
                case_binding_digests, solver_view_digests, strict=True
            )
        ],
        "group": group,
        "group_binding_digest": group_binding_digest,
    }


@dataclass(frozen=True, slots=True)
class RelationalGroupManifestEntryV3:
    """One preregistered generator group and its exact case membership."""

    group: str
    group_binding_digest: str
    case_binding_digests: tuple[str, ...]
    solver_view_digests: tuple[str, ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RelationalGroupManifestEntryV3:
            raise ValueError("relational group-entry subclasses are forbidden")
        object.__setattr__(
            self,
            "content_digest",
            _canonical_digest(
                _group_entry_payload(
                    group=self.group,
                    group_binding_digest=self.group_binding_digest,
                    case_binding_digests=self.case_binding_digests,
                    solver_view_digests=self.solver_view_digests,
                )
            ),
        )
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RelationalGroupManifestEntryV3:
            raise ValueError("relational group-entry subclasses are forbidden")
        _plain_text(self.group, "relational manifest group")
        _validated_digest(
            self.group_binding_digest, "relational manifest group binding"
        )
        if (
            type(self.case_binding_digests) is not tuple
            or not 1
            <= len(self.case_binding_digests)
            <= MAX_RELATIONAL_CASES_PER_GROUP_V3
            or not all(type(value) is str for value in self.case_binding_digests)
        ):
            raise ValueError("relational manifest group has an invalid case tuple")
        for digest in self.case_binding_digests:
            _validated_digest(digest, "relational manifest case binding")
        if (
            type(self.solver_view_digests) is not tuple
            or len(self.solver_view_digests) != len(self.case_binding_digests)
            or not all(type(value) is str for value in self.solver_view_digests)
        ):
            raise ValueError("relational manifest group has an invalid solver-view tuple")
        for digest in self.solver_view_digests:
            _validated_digest(digest, "relational manifest solver-view binding")
        if tuple(sorted(self.case_binding_digests)) != self.case_binding_digests:
            raise ValueError("relational manifest case bindings must be canonical")
        if len(set(self.case_binding_digests)) != len(self.case_binding_digests):
            raise ValueError("relational manifest case bindings must be unique")
        supplied = _validated_digest(
            self.content_digest, "relational manifest group-entry digest"
        )
        expected = _canonical_digest(
            _group_entry_payload(
                group=self.group,
                group_binding_digest=self.group_binding_digest,
                case_binding_digests=self.case_binding_digests,
                solver_view_digests=self.solver_view_digests,
            )
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("relational manifest group-entry digest is inconsistent")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            **_group_entry_payload(
                group=self.group,
                group_binding_digest=self.group_binding_digest,
                case_binding_digests=self.case_binding_digests,
                solver_view_digests=self.solver_view_digests,
            ),
            "content_digest": self.content_digest,
        }

    @classmethod
    def from_dict(cls, value: object) -> RelationalGroupManifestEntryV3:
        if type(value) is not dict or set(value) != {
            "cases",
            "content_digest",
            "group",
            "group_binding_digest",
        }:
            raise ValueError("relational manifest group-entry fields are invalid")
        cases = value["cases"]
        if (
            type(cases) is not list
            or not all(
                type(item) is dict
                and set(item) == {"case_binding_digest", "solver_view_digest"}
                for item in cases
            )
        ):
            raise ValueError("serialized relational group cases must be an exact array")
        entry = cls(
            group=_plain_text(value["group"], "serialized relational group"),
            group_binding_digest=_validated_digest(
                value["group_binding_digest"],
                "serialized relational group binding",
            ),
            case_binding_digests=tuple(item["case_binding_digest"] for item in cases),
            solver_view_digests=tuple(item["solver_view_digest"] for item in cases),
        )
        supplied = _validated_digest(
            value["content_digest"], "serialized relational group-entry digest"
        )
        if not hmac.compare_digest(supplied, entry.content_digest):
            raise ValueError("serialized relational group-entry digest is incorrect")
        return entry


def _group_manifest_payload(
    entries: tuple[RelationalGroupManifestEntryV3, ...],
) -> dict[str, object]:
    return {
        "entries": [entry.to_dict() for entry in entries],
        "schema": RELATIONAL_GROUP_MANIFEST_SCHEMA_V3,
        "schema_version": RELATIONAL_GROUP_MANIFEST_SCHEMA_VERSION_V3,
    }


@dataclass(frozen=True, slots=True)
class RelationalGroupManifestV3:
    """Typed group-split authority intended for external commitment."""

    entries: tuple[RelationalGroupManifestEntryV3, ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RelationalGroupManifestV3:
            raise ValueError("relational group-manifest subclasses are forbidden")
        object.__setattr__(
            self,
            "content_digest",
            _canonical_digest(_group_manifest_payload(self.entries)),
        )
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RelationalGroupManifestV3:
            raise ValueError("relational group-manifest subclasses are forbidden")
        if (
            type(self.entries) is not tuple
            or not 2 <= len(self.entries) <= MAX_RELATIONAL_GROUPS_V3
            or not all(
                type(entry) is RelationalGroupManifestEntryV3 for entry in self.entries
            )
        ):
            raise ValueError("relational group manifest has an invalid entry tuple")
        for entry in self.entries:
            entry.validate_current()
        if tuple(entry.group for entry in self.entries) != tuple(
            sorted(entry.group for entry in self.entries)
        ):
            raise ValueError("relational group manifest entries must be canonical")
        if len({entry.group for entry in self.entries}) != len(self.entries):
            raise ValueError("relational group manifest names must be unique")
        if len({entry.group_binding_digest for entry in self.entries}) != len(self.entries):
            raise ValueError("relational group manifest bindings must be unique")
        cases = [case for entry in self.entries for case in entry.case_binding_digests]
        if len(cases) < 4 or len(set(cases)) != len(cases):
            raise ValueError("relational group manifest cases must be globally unique")
        views = [view for entry in self.entries for view in entry.solver_view_digests]
        if len(set(views)) != len(views):
            raise ValueError("relational group manifest solver views must be globally unique")
        supplied = _validated_digest(
            self.content_digest, "relational group-manifest digest"
        )
        expected = _canonical_digest(_group_manifest_payload(self.entries))
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("relational group-manifest digest is inconsistent")

    def validate_expected_digest(self, expected_manifest_digest: str) -> None:
        self.validate_current()
        expected = _validated_digest(
            expected_manifest_digest, "expected relational group-manifest digest"
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("relational group manifest does not match external commitment")

    def validate_population(
        self, examples: Sequence[RelationalLeakageExampleV3]
    ) -> None:
        self.validate_current()
        observed: dict[str, tuple[str, list[tuple[str, str]]]] = {}
        for example in examples:
            if type(example) is not RelationalLeakageExampleV3:
                raise ValueError("relational group population requires exact examples")
            prior = observed.setdefault(
                example.group, (example.group_binding_digest, [])
            )
            if prior[0] != example.group_binding_digest:
                raise ValueError("relational group population changes a group binding")
            prior[1].append(
                (example.case_binding_digest, example.solver_view.content_digest)
            )
        expected = {
            entry.group: (
                entry.group_binding_digest,
                tuple(
                    zip(
                        entry.case_binding_digests,
                        entry.solver_view_digests,
                        strict=True,
                    )
                ),
            )
            for entry in self.entries
        }
        canonical_observed = {
            group: (binding, tuple(sorted(cases)))
            for group, (binding, cases) in observed.items()
        }
        if canonical_observed != expected:
            raise ValueError("relational population does not match committed group manifest")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            **_group_manifest_payload(self.entries),
            "content_digest": self.content_digest,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> RelationalGroupManifestV3:
        if type(value) is not dict or set(value) != {
            "content_digest",
            "entries",
            "schema",
            "schema_version",
        }:
            raise ValueError("relational group-manifest fields are invalid")
        if value["schema"] != RELATIONAL_GROUP_MANIFEST_SCHEMA_V3:
            raise ValueError("relational group-manifest schema is unsupported")
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise ValueError("relational group-manifest version is unsupported")
        entries = value["entries"]
        if type(entries) is not list:
            raise ValueError("serialized relational manifest entries must be an exact array")
        manifest = cls(
            entries=tuple(
                RelationalGroupManifestEntryV3.from_dict(entry) for entry in entries
            )
        )
        supplied = _validated_digest(
            value["content_digest"], "serialized relational group-manifest digest"
        )
        if not hmac.compare_digest(supplied, manifest.content_digest):
            raise ValueError("serialized relational group-manifest digest is incorrect")
        return manifest


@dataclass(frozen=True, slots=True)
class RelationalLeakageExampleV3:
    """One labeled example whose features are restricted to a solver view."""

    group: str
    group_binding_digest: str
    case_binding_digest: str
    family: str
    mechanism: str
    fault_line: int
    solver_view: RelationalSolverViewV3

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        """Recheck the complete labeled boundary after possible low-level mutation."""

        if type(self) is not RelationalLeakageExampleV3:
            raise ValueError("relational leakage-example subclasses are forbidden")
        _plain_text(self.group, "relational example group")
        _validated_digest(self.group_binding_digest, "relational group binding")
        _validated_digest(self.case_binding_digest, "relational case binding")
        _plain_text(self.family, "relational example family")
        _plain_text(self.mechanism, "relational example mechanism")
        if type(self.solver_view) is not RelationalSolverViewV3:
            raise ValueError("relational example requires an exact solver view")
        self.solver_view.validate_current()
        if type(self.fault_line) is not int:
            raise ValueError("relational example fault line must be an exact integer")
        line_numbers = [line_number for line_number, _ in self.solver_view.candidate_lines]
        if self.fault_line not in line_numbers:
            raise ValueError("fault line must be one of the solver-visible candidates")

    @property
    def prompt(self) -> str:
        return self.solver_view.prompt

    @property
    def candidate_lines(self) -> tuple[tuple[int, str], ...]:
        return self.solver_view.candidate_lines


def build_relational_group_manifest_v3(
    entries: Iterable[RelationalGroupManifestEntryV3],
) -> RelationalGroupManifestV3:
    """Freeze exact label-free group records for commitment before fitting."""

    values = tuple(entries)
    if not all(type(item) is RelationalGroupManifestEntryV3 for item in values):
        raise ValueError("relational group manifest requires exact label-free entries")
    return RelationalGroupManifestV3(
        entries=tuple(sorted(values, key=lambda entry: entry.group))
    )


@dataclass(frozen=True, slots=True)
class RelationalLeakageFoldV3:
    group: str
    total_cases: int
    fault_line_correct: int
    mechanism_correct: int

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RelationalLeakageFoldV3:
            raise ValueError("relational leakage-fold subclasses are forbidden")
        _plain_text(self.group, "relational fold group")
        if type(self.total_cases) is not int or self.total_cases <= 0:
            raise ValueError("relational fold case count must be positive")
        for name in ("fault_line_correct", "mechanism_correct"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= self.total_cases:
                raise ValueError(f"relational fold {name} is outside its case count")

    @property
    def fault_line_accuracy(self) -> float:
        return round(self.fault_line_correct / self.total_cases, 6)

    @property
    def mechanism_accuracy(self) -> float:
        return round(self.mechanism_correct / self.total_cases, 6)

    def to_dict(self) -> dict[str, int | float | str]:
        self.validate_current()
        return {
            "fault_line_accuracy": self.fault_line_accuracy,
            "fault_line_correct": self.fault_line_correct,
            "group": self.group,
            "mechanism_accuracy": self.mechanism_accuracy,
            "mechanism_correct": self.mechanism_correct,
            "total_cases": self.total_cases,
        }


@dataclass(frozen=True, slots=True)
class RelationalLeakageReportV3:
    total_cases: int
    fault_line_correct: int
    mechanism_correct: int
    group_manifest_digest: str
    input_digest: str
    solver_feature_digest: str
    maximum_fault_line_accuracy: float
    maximum_mechanism_accuracy: float
    folds: tuple[RelationalLeakageFoldV3, ...]

    def __post_init__(self) -> None:
        self.validate_current()

    def validate_current(self) -> None:
        if type(self) is not RelationalLeakageReportV3:
            raise ValueError("relational leakage-report subclasses are forbidden")
        if type(self.total_cases) is not int or self.total_cases <= 0:
            raise ValueError("relational report case count must be positive")
        for name in ("fault_line_correct", "mechanism_correct"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= self.total_cases:
                raise ValueError(f"relational report {name} is outside its case count")
        _validated_digest(
            self.group_manifest_digest, "relational report group-manifest digest"
        )
        _validated_digest(self.input_digest, "relational report input digest")
        _validated_digest(
            self.solver_feature_digest, "relational report solver-feature digest"
        )
        _bounded_rate(self.maximum_fault_line_accuracy, "maximum fault-line accuracy")
        _bounded_rate(self.maximum_mechanism_accuracy, "maximum mechanism accuracy")
        if (
            self.maximum_fault_line_accuracy != DEFAULT_MAXIMUM_FAULT_LINE_ACCURACY_V3
            or self.maximum_mechanism_accuracy != DEFAULT_MAXIMUM_MECHANISM_ACCURACY_V3
        ):
            raise ValueError("relational release thresholds are fixed and cannot be relaxed")
        if type(self.folds) is not tuple or len(self.folds) < 2:
            raise ValueError("relational report requires at least two held-out groups")
        if not all(type(fold) is RelationalLeakageFoldV3 for fold in self.folds):
            raise ValueError("relational report contains an invalid fold")
        for fold in self.folds:
            fold.validate_current()
        if tuple(fold.group for fold in self.folds) != tuple(
            sorted(fold.group for fold in self.folds)
        ):
            raise ValueError("relational report folds must use canonical group order")
        if len({fold.group for fold in self.folds}) != len(self.folds):
            raise ValueError("relational report fold groups must be unique")
        if sum(fold.total_cases for fold in self.folds) != self.total_cases:
            raise ValueError("relational folds do not cover the report population")
        if sum(fold.fault_line_correct for fold in self.folds) != self.fault_line_correct:
            raise ValueError("relational folds do not reproduce fault-line correctness")
        if sum(fold.mechanism_correct for fold in self.folds) != self.mechanism_correct:
            raise ValueError("relational folds do not reproduce mechanism correctness")

    @property
    def fault_line_accuracy(self) -> float:
        return round(self.fault_line_correct / self.total_cases, 6)

    @property
    def mechanism_accuracy(self) -> float:
        return round(self.mechanism_correct / self.total_cases, 6)

    @property
    def worst_group_fault_line_accuracy(self) -> float:
        return max(fold.fault_line_accuracy for fold in self.folds)

    @property
    def worst_group_mechanism_accuracy(self) -> float:
        return max(fold.mechanism_accuracy for fold in self.folds)

    @property
    def release_gate_passed(self) -> bool:
        self.validate_current()
        return (
            self.fault_line_correct / self.total_cases <= self.maximum_fault_line_accuracy
            and self.mechanism_correct / self.total_cases <= self.maximum_mechanism_accuracy
            and all(
                fold.fault_line_correct / fold.total_cases
                <= self.maximum_fault_line_accuracy
                and fold.mechanism_correct / fold.total_cases
                <= self.maximum_mechanism_accuracy
                for fold in self.folds
            )
        )

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return {
            "fault_line_accuracy": self.fault_line_accuracy,
            "fault_line_correct": self.fault_line_correct,
            "folds": [fold.to_dict() for fold in self.folds],
            "group_manifest_digest": self.group_manifest_digest,
            "input_digest": self.input_digest,
            "maximum_fault_line_accuracy": self.maximum_fault_line_accuracy,
            "maximum_mechanism_accuracy": self.maximum_mechanism_accuracy,
            "mechanism_accuracy": self.mechanism_accuracy,
            "mechanism_correct": self.mechanism_correct,
            "release_gate_passed": self.release_gate_passed,
            "schema": RELATIONAL_LEAKAGE_SCHEMA_V3,
            "schema_version": RELATIONAL_LEAKAGE_SCHEMA_VERSION_V3,
            "solver_feature_digest": self.solver_feature_digest,
            "solver_feature_profile": RELATIONAL_SOLVER_FEATURE_PROFILE_V3,
            "split": "leave-generator-group-out-v1",
            "total_cases": self.total_cases,
            "worst_group_fault_line_accuracy": self.worst_group_fault_line_accuracy,
            "worst_group_mechanism_accuracy": self.worst_group_mechanism_accuracy,
        }


def _normalized_semantics(description: str) -> str:
    def replace_number(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith("-"):
            return "-#"
        if token.startswith("+"):
            return "+#"
        return "#"

    lowered = _NUMBER_RE.sub(replace_number, description.lower())
    return _SPACE_RE.sub(" ", lowered).strip()


def _validate_alias_tokens(text: str, label: str) -> None:
    for token in _ALIAS_LIKE_RE.findall(text):
        if _ALIAS_RE.fullmatch(token) is None:
            raise ValueError(f"{label} contains a malformed public alias")


def _raw_legend(prompt: str) -> dict[str, str]:
    _validate_alias_tokens(prompt, "solver prompt")
    try:
        raw = prompt.split("Case-permuted alias key: ", 1)[1].split(". Decide", 1)[0]
    except IndexError as exc:
        raise ValueError("solver prompt omits the canonical alias legend") from exc
    result: dict[str, str] = {}
    for entry in raw.split("; "):
        alias, separator, description = entry.partition(" means ")
        if not separator or re.fullmatch(_ALIAS_PATTERN, alias) is None:
            raise ValueError("solver prompt contains a malformed alias legend entry")
        if alias in result:
            raise ValueError("solver prompt contains a duplicate alias legend entry")
        result[alias] = _SPACE_RE.sub(
            " ", _plain_text(description, "alias description").lower()
        ).strip()
    if len(result) < 2:
        raise ValueError("solver prompt alias legend is too small for a relational audit")
    return result


def _legend(prompt: str) -> dict[str, str]:
    return {
        alias: _normalized_semantics(description)
        for alias, description in _raw_legend(prompt).items()
    }


def _basis_and_target(line: str, legend: Mapping[str, str]) -> tuple[str, Counter[str]]:
    _validate_alias_tokens(line, "candidate line")
    target_matches = tuple(_TARGET_RE.finditer(line))
    if len(target_matches) != 1:
        if not target_matches:
            raise ValueError("candidate line omits a solver-visible claim target")
        raise ValueError("candidate line contains multiple claim targets")
    if line.count("; basis ") != 1:
        raise ValueError("candidate line must contain exactly one solver-visible basis")
    target_match = target_matches[0]
    if target_match is None:  # pragma: no cover - narrowed above for static readers
        raise ValueError("candidate line omits a solver-visible claim target")
    try:
        basis = line.split("; basis ", 1)[1].rsplit(".", 1)[0]
    except IndexError as exc:
        raise ValueError("candidate line omits a solver-visible basis") from exc
    target = target_match.group(1)
    sources = Counter(_ALIAS_RE.findall(basis))
    unknown = ({target} | set(sources)) - set(legend)
    if unknown:
        raise ValueError("candidate line uses an alias missing from the public legend")
    if not sources:
        raise ValueError("candidate line basis contains no public alias")
    return target, sources


def _basis_text(line: str) -> str:
    try:
        return line.split("; basis ", 1)[1].rsplit(".", 1)[0]
    except IndexError as exc:
        raise ValueError("candidate line omits a solver-visible basis") from exc


def _fraction_text(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _number_bucket(token: str) -> str:
    if len(token) > MAX_RELATIONAL_NUMBER_CHARS_V3:
        raise ValueError("solver-visible number exceeds its character cap")
    try:
        value = Fraction(token)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("solver-visible number is not a finite rational") from exc
    if abs(value) <= 16 and value.denominator <= 16:
        return f"exact::{_fraction_text(value)}"
    sign = "negative" if value < 0 else "zero" if value == 0 else "positive"
    magnitude = max(abs(value.numerator).bit_length(), value.denominator.bit_length())
    return f"shape::{sign}::bits::{min(magnitude, 64)}::den::{min(value.denominator, 32)}"


def _semantic_token(description: str) -> str:
    return _canonical_digest({"public_semantics": description})


def _public_tokens(text: str, legend: Mapping[str, str]) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _PUBLIC_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _ALIAS_RE.fullmatch(token):
            if token not in legend:
                raise ValueError("public text uses an alias missing from the legend")
            tokens.append(f"semantic::{_semantic_token(legend[token])}")
        elif _NUMBER_RE.fullmatch(token):
            tokens.append(f"number::{_number_bucket(token)}")
        else:
            tokens.append(token.lower())
    return tuple(tokens)


def _public_exact_tokens(
    text: str, raw_legend: Mapping[str, str]
) -> tuple[str, ...]:
    """Tokenize public text while retaining exact legend and number surfaces.

    Alias spellings remain deliberately absent: their exact public descriptions
    replace them, so the representation is invariant to a bijective alias rename
    while still retaining associations that numeric normalization would erase.
    """

    tokens: list[str] = []
    for match in _PUBLIC_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _ALIAS_RE.fullmatch(token):
            if token not in raw_legend:
                raise ValueError("public text uses an alias missing from the legend")
            tokens.append(f"semantic-exact::{_semantic_token(raw_legend[token])}")
        elif _NUMBER_RE.fullmatch(token):
            tokens.append(_canonical_digest({"public_number_surface": token}))
        else:
            tokens.append(token.lower())
    return tuple(tokens)


def _token_features(prefix: str, tokens: tuple[str, ...]) -> set[str]:
    features = {f"{prefix}::token::{token}" for token in tokens}
    for width in (2, 3):
        features.update(
            f"{prefix}::ngram::{width}::{'|'.join(tokens[index:index + width])}"
            for index in range(len(tokens) - width + 1)
        )
    return features


def _prompt_surface_tokens(
    prompt: str, legend: Mapping[str, str]
) -> tuple[str, ...]:
    """Bound prompt features to the tag/preamble and decision scaffold.

    Legend semantics already enter the relational projections below.  Repeating
    the full legend and long JSON contract as n-grams made grouped replay needlessly
    expensive without adding a distinct public channel.
    """

    preamble, separator, remainder = prompt.partition("Case-permuted alias key: ")
    if not separator:
        raise ValueError("solver prompt omits the canonical alias legend")
    _, decision_separator, decision = remainder.partition(". Decide")
    scaffold = decision.split("Return one JSON object", 1)[0] if decision_separator else ""
    return _public_tokens(f"{preamble} Decide{scaffold}", legend)[:96]


def _complete_prompt_features(
    prompt: str,
    *,
    legend: Mapping[str, str],
    raw_legend: Mapping[str, str],
) -> set[str]:
    """Cover every non-legend prompt token without an unbounded vocabulary.

    The complete ordered legend is modeled separately below.  Removing only
    those already-bound bytes keeps a long legend from dominating this sketch;
    the preamble, decision text, JSON contract, and any trailer all remain.
    """

    preamble, separator, remainder = prompt.partition("Case-permuted alias key: ")
    if not separator:
        raise ValueError("solver prompt omits the canonical alias legend")
    _, decision_separator, decision_tail = remainder.partition(". Decide")
    if not decision_separator:
        raise ValueError("solver prompt omits the canonical decision scaffold")
    prompt_surface = f"{preamble}. Decide{decision_tail}"
    normalized_tokens = _public_tokens(prompt_surface, legend)
    exact_tokens = _public_exact_tokens(prompt_surface, raw_legend)
    features = {
        f"prompt-complete::token::{token}"
        for token in set(normalized_tokens) | set(exact_tokens)
    }
    tail = normalized_tokens[-24:]
    for width in (2, 3):
        features.update(
            f"prompt-complete::tail-ngram::{width}::"
            + "|".join(tail[index : index + width])
            for index in range(len(tail) - width + 1)
        )
    return features


class _NonAffineExpression(Exception):
    """Internal signal for unsupported, malformed, or nonlinear public syntax."""


@dataclass(frozen=True, slots=True)
class _AffineForm:
    constant: Fraction
    terms: tuple[tuple[str, Fraction], ...]
    affine: bool = True
    coefficient_geometry_eligible: bool = True


def _bounded_rational(value: Fraction) -> Fraction:
    if (
        abs(value.numerator).bit_length() > MAX_RELATIONAL_RATIONAL_BITS_V3
        or value.denominator.bit_length() > MAX_RELATIONAL_RATIONAL_BITS_V3
    ):
        raise _NonAffineExpression
    return value


def _bounded_affine(form: _AffineForm) -> _AffineForm:
    if len(form.terms) > MAX_RELATIONAL_AFFINE_TERMS_V3:
        raise _NonAffineExpression
    _bounded_rational(form.constant)
    for _, coefficient in form.terms:
        _bounded_rational(coefficient)
    return form


def _scale_affine(form: _AffineForm, scalar: Fraction) -> _AffineForm:
    scalar = _bounded_rational(scalar)
    return _bounded_affine(
        _AffineForm(
            constant=_bounded_rational(form.constant * scalar),
            terms=tuple(
                (alias, _bounded_rational(coefficient * scalar))
                for alias, coefficient in form.terms
            ),
            affine=form.affine,
            coefficient_geometry_eligible=form.coefficient_geometry_eligible,
        )
    )


class _AffineParser:
    """Bounded recursive-descent parser for the renderer's exact affine grammar."""

    __slots__ = ("cursor", "legend", "tokens")

    def __init__(self, basis: str, legend: Mapping[str, str]) -> None:
        expression = _SPACE_RE.sub("", basis)
        matches = tuple(_AFFINE_TOKEN_RE.finditer(expression))
        tokens = tuple(match.group(0) for match in matches)
        if (
            not expression
            or "".join(tokens) != expression
            or len(tokens) > MAX_RELATIONAL_AFFINE_TOKENS_V3
        ):
            raise _NonAffineExpression
        self.tokens = tokens
        self.legend = legend
        self.cursor = 0

    def parse(self) -> _AffineForm:
        result = self._expression(0)
        if self.cursor != len(self.tokens):
            raise _NonAffineExpression
        return _bounded_affine(result)

    def _peek(self) -> str | None:
        return self.tokens[self.cursor] if self.cursor < len(self.tokens) else None

    def _take(self) -> str:
        token = self._peek()
        if token is None:
            raise _NonAffineExpression
        self.cursor += 1
        return token

    def _expression(self, depth: int) -> _AffineForm:
        result = self._product(depth)
        while self._peek() in {"+", "-"}:
            operator = self._take()
            right = self._product(depth)
            sign = 1 if operator == "+" else -1
            result = _bounded_affine(
                _AffineForm(
                    constant=_bounded_rational(result.constant + sign * right.constant),
                    terms=(
                        *result.terms,
                        *((alias, sign * coefficient) for alias, coefficient in right.terms),
                    ),
                    affine=result.affine and right.affine,
                    coefficient_geometry_eligible=(
                        result.coefficient_geometry_eligible
                        and right.coefficient_geometry_eligible
                    ),
                )
            )
        return result

    def _product(self, depth: int) -> _AffineForm:
        result = self._factor(depth)
        while self._peek() in {"*", "/"}:
            operator = self._take()
            right = self._factor(depth)
            if operator == "/":
                if right.terms:
                    result = _bounded_affine(
                        _AffineForm(
                            constant=Fraction(0),
                            terms=(*result.terms, *right.terms),
                            affine=False,
                            coefficient_geometry_eligible=False,
                        )
                    )
                elif right.constant == 0:
                    raise _NonAffineExpression
                else:
                    result = _scale_affine(result, Fraction(1, 1) / right.constant)
            elif result.terms and right.terms:
                result = _bounded_affine(
                    _AffineForm(
                        constant=_bounded_rational(result.constant * right.constant),
                        terms=(*result.terms, *right.terms),
                        affine=False,
                        coefficient_geometry_eligible=(
                            result.coefficient_geometry_eligible
                            and right.coefficient_geometry_eligible
                        ),
                    )
                )
            elif result.terms:
                result = _scale_affine(result, right.constant)
            elif right.terms:
                result = _scale_affine(right, result.constant)
            else:
                result = _AffineForm(
                    constant=_bounded_rational(result.constant * right.constant),
                    terms=(),
                    affine=result.affine and right.affine,
                    coefficient_geometry_eligible=(
                        result.coefficient_geometry_eligible
                        and right.coefficient_geometry_eligible
                    ),
                )
        return result

    def _factor(self, depth: int) -> _AffineForm:
        if depth > MAX_RELATIONAL_AFFINE_DEPTH_V3:
            raise _NonAffineExpression
        token = self._peek()
        if token in {"+", "-"}:
            sign = -1 if self._take() == "-" else 1
            return _scale_affine(self._factor(depth + 1), Fraction(sign))
        if token == "(":
            self._take()
            result = self._expression(depth + 1)
            if self._take() != ")":
                raise _NonAffineExpression
            return result
        if token is None or token == ")":
            raise _NonAffineExpression
        token = self._take()
        if _ALIAS_RE.fullmatch(token):
            if token not in self.legend:
                raise ValueError("coefficient term uses an alias missing from the public legend")
            return _AffineForm(constant=Fraction(0), terms=((token, Fraction(1)),))
        if len(token) > MAX_RELATIONAL_NUMBER_CHARS_V3:
            raise _NonAffineExpression
        try:
            value = Fraction(token)
        except (ValueError, ZeroDivisionError) as exc:
            raise _NonAffineExpression from exc
        if token != _fraction_text(value):
            raise _NonAffineExpression
        return _AffineForm(constant=_bounded_rational(value), terms=())


def _canonical_affine_terms(
    basis: str, legend: Mapping[str, str]
) -> tuple[tuple[int, str, Fraction], ...] | None:
    try:
        form = _AffineParser(basis, legend).parse()
    except _NonAffineExpression:
        return None
    if not form.terms or not form.affine:
        return None
    return tuple(
        (position, alias, coefficient)
        for position, (alias, coefficient) in enumerate(form.terms)
    )


def _canonical_coefficient_terms(
    basis: str, legend: Mapping[str, str]
) -> tuple[tuple[int, str, Fraction], ...] | None:
    """Return ordered displayed alias coefficients, including nonlinear products."""

    try:
        form = _AffineParser(basis, legend).parse()
    except _NonAffineExpression:
        return None
    if not form.terms or not form.coefficient_geometry_eligible:
        return None
    return tuple(
        (position, alias, coefficient)
        for position, (alias, coefficient) in enumerate(form.terms)
    )


def _declared_value(line: str) -> Fraction | None:
    match = _DECLARATION_RE.search(line)
    if match is None:
        return None
    try:
        return Fraction(match.group(1))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("candidate declaration contains a malformed rational") from exc


def _canonical_cycle(values: tuple[int, ...]) -> tuple[int, ...]:
    if not values:
        return ()
    return min(values[index:] + values[:index] for index in range(len(values)))


def _declaration_ordinal_features(
    view: RelationalSolverViewV3, declared_rank: Mapping[int, int]
) -> set[str]:
    ranks = tuple(
        declared_rank[line_number]
        for line_number, _ in view.candidate_lines
        if line_number in declared_rank
    )
    if len(ranks) < 2:
        return {f"declaration-ordinal::count::{len(ranks)}"}

    def comparison(left: int, right: int) -> int:
        return (right > left) - (right < left)

    linear = tuple(comparison(left, right) for left, right in pairwise(ranks))
    cyclic = (*linear, comparison(ranks[-1], ranks[0]))
    features = {
        f"declaration-ordinal::count::{len(ranks)}",
        "declaration-ordinal::canonical-rank-cycle::"
        + ",".join(str(value) for value in _canonical_cycle(ranks)),
        "declaration-ordinal::canonical-direction-cycle::"
        + ",".join(str(value) for value in _canonical_cycle(cyclic)),
    }
    for label, value in (("down", -1), ("equal", 0), ("up", 1)):
        features.add(
            f"declaration-ordinal::linear-{label}-count::{linear.count(value)}"
        )
        features.add(
            f"declaration-ordinal::cyclic-{label}-count::{cyclic.count(value)}"
        )
    return features


def _plane_support(
    points: tuple[tuple[Fraction, Fraction, Fraction], ...]
) -> tuple[int, set[int], int]:
    if len(points) < 4:
        return 0, set(), 0
    maximum = 0
    maximum_supports: set[frozenset[int]] = set()
    for first_index, second_index, third_index in combinations(range(len(points)), 3):
        first = points[first_index]
        second = points[second_index]
        third = points[third_index]
        left = tuple(second[index] - first[index] for index in range(3))
        right = tuple(third[index] - first[index] for index in range(3))
        normal = (
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        )
        if normal == (0, 0, 0):
            continue
        support = {
            point_index
            for point_index, point in enumerate(points)
            if sum(
                normal[coordinate] * (point[coordinate] - first[coordinate])
                for coordinate in range(3)
            )
            == 0
        }
        if len(support) > maximum:
            maximum = len(support)
            maximum_supports = {frozenset(support)}
        elif len(support) == maximum:
            maximum_supports.add(frozenset(support))
    if maximum >= 4 and len(maximum_supports) == 1:
        outliers = set(range(len(points))) - set(next(iter(maximum_supports)))
    else:
        outliers = set()
    return maximum, outliers, len(maximum_supports)


def _coefficient_geometry(
    view: RelationalSolverViewV3, legend: Mapping[str, str]
) -> tuple[dict[int, set[str]], set[str]]:
    if len(view.candidate_lines) > MAX_RELATIONAL_GEOMETRY_ROWS_V3:
        return {}, {"coefficient-geometry::skipped::row-cap"}
    rows: list[tuple[int, tuple[tuple[int, str, Fraction], ...]]] = []
    for line_number, line in view.candidate_lines:
        basis = _basis_text(line)
        terms = _canonical_coefficient_terms(basis, legend)
        if terms is None or len(terms) != 3:
            return {}, set()
        rows.append((line_number, terms))
    line_features: dict[int, set[str]] = {line_number: set() for line_number, _ in rows}
    case_features: set[str] = set()

    position_points = tuple(
        tuple(term[2] for term in terms) for _, terms in rows
    )
    position_support, position_outliers, position_ties = _plane_support(position_points)
    case_features.add(f"position-plane::maximum-support::{position_support}")
    case_features.add(f"position-plane::outlier-count::{len(position_outliers)}")
    case_features.add(f"position-plane::maximum-consensus-count::{position_ties}")
    for row_index, (line_number, _) in enumerate(rows):
        line_features[line_number].add(
            f"position-plane::outlier-candidate::{row_index in position_outliers}"
        )

    semantic_sets = [{legend[term[1]] for term in terms} for _, terms in rows]
    if semantic_sets and all(
        len(semantics) == 3 and semantics == semantic_sets[0] for semantics in semantic_sets
    ):
        semantic_order = tuple(sorted(semantic_sets[0]))
        semantic_points = tuple(
            tuple(
                next(
                    coefficient
                    for _, alias, coefficient in terms
                    if legend[alias] == wanted
                )
                for wanted in semantic_order
            )
            for _, terms in rows
        )
        semantic_support, semantic_outliers, semantic_ties = _plane_support(semantic_points)
        case_features.add(f"semantic-plane::maximum-support::{semantic_support}")
        case_features.add(f"semantic-plane::outlier-count::{len(semantic_outliers)}")
        case_features.add(f"semantic-plane::maximum-consensus-count::{semantic_ties}")
        for row_index, (line_number, _) in enumerate(rows):
            line_features[line_number].add(
                f"semantic-plane::outlier-candidate::{row_index in semantic_outliers}"
            )
    return line_features, case_features


def _line_features(
    view: RelationalSolverViewV3,
    line_number: int,
    line: str,
    *,
    legend: Mapping[str, str],
    raw_legend: Mapping[str, str],
    geometry: Mapping[int, set[str]],
    declared_rank: Mapping[int, int],
) -> frozenset[str]:
    if type(view) is not RelationalSolverViewV3:
        raise ValueError("relational line features require an exact solver view")
    target, sources = _basis_and_target(line, legend)
    target_semantics = legend[target]
    features = {
        f"target::{target_semantics}",
        f"target-exact::{_semantic_token(raw_legend[target])}",
        f"line-number::{line_number}",
        *geometry.get(line_number, set()),
    }
    for alias, count in sources.items():
        source_semantics = legend[alias]
        source_exact = _semantic_token(raw_legend[alias])
        count_bucket = min(count, 4)
        features.add(f"source::{source_semantics}::count::{count_bucket}")
        features.add(f"source-exact::{source_exact}::count::{count_bucket}")
        features.add(
            f"target-source::{target_semantics}::{source_semantics}::count::{count_bucket}"
        )
        features.add(
            "target-source-exact::"
            f"{_semantic_token(raw_legend[target])}::{source_exact}::count::{count_bucket}"
        )
    basis = _basis_text(line)
    terms = _canonical_coefficient_terms(basis, legend)
    for position, source_alias, coefficient in terms or ():
        source_semantics = legend[source_alias]
        source_exact = _semantic_token(raw_legend[source_alias])
        coefficient_text = _fraction_text(coefficient)
        semantic = _semantic_token(source_semantics)
        sign = "negative" if coefficient < 0 else "zero" if coefficient == 0 else "positive"
        features.update(
            {
                f"coefficient::exact::{coefficient_text}",
                f"coefficient::sign::{sign}",
                f"coefficient-source::{semantic}::exact::{coefficient_text}",
                f"coefficient-source-exact::{source_exact}::exact::{coefficient_text}",
                f"coefficient-position::{position}::exact::{coefficient_text}",
                f"coefficient-source-position::{semantic}::{position}::{coefficient_text}",
                "coefficient-source-exact-position::"
                f"{source_exact}::{position}::{coefficient_text}",
            }
        )
    declaration = _declared_value(line)
    if declaration is not None:
        features.add(f"declaration::number::{_number_bucket(_fraction_text(declaration))}")
        features.add(f"declaration::rank::{declared_rank[line_number]}")
    prompt_root = _ROOT_HINT_RE.search(view.prompt)
    if prompt_root is not None:
        features.add(f"prompt-root-hint-match::{int(prompt_root.group(1)) == line_number}")
    features.update(_token_features("line", _public_tokens(line, legend)))
    return frozenset(features)


def _case_features(
    view: RelationalSolverViewV3,
    *,
    legend: Mapping[str, str],
    raw_legend: Mapping[str, str],
    geometry_features: set[str],
    declared_rank: Mapping[int, int],
) -> frozenset[str]:
    if type(view) is not RelationalSolverViewV3:
        raise ValueError("relational case features require an exact solver view")
    aggregate: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    coefficient_counts: Counter[tuple[str, str]] = Counter()
    ordered_rows: Counter[tuple[tuple[str, str], ...]] = Counter()
    line_surfaces: Counter[str] = Counter()
    exact_ordered_rows: Counter[str] = Counter()
    bound_features: set[str] = set()
    ordered_candidate_rows: list[dict[str, object]] = []
    declaration_sequence: list[list[object]] = []
    for line_number, line in view.candidate_lines:
        basis = _basis_text(line)
        normalized_line_tokens = _public_tokens(line, legend)
        exact_line_tokens = _public_exact_tokens(line, raw_legend)
        basis_surface = _canonical_digest(
            {"ordered_basis_tokens": _public_tokens(basis, legend)}
        )
        surface = _canonical_digest(
            {
                "exact_line_tokens": exact_line_tokens,
                "normalized_line_tokens": normalized_line_tokens,
            }
        )
        line_surfaces[basis_surface] += 1
        target, sources = _basis_and_target(line, legend)
        target_semantics = legend[target]
        target_semantic_token = _semantic_token(target_semantics)
        target_exact_token = _semantic_token(raw_legend[target])
        declaration = _declared_value(line)
        declaration_record = None
        if declaration is not None:
            declaration_record = [
                _fraction_text(declaration),
                declared_rank[line_number],
            ]
            declaration_sequence.append([line_number, *declaration_record])
        ordered_candidate_rows.append(
            {
                "declaration": declaration_record,
                "line_number": line_number,
                "surface": surface,
                "target_exact": target_exact_token,
                "target_normalized": target_semantic_token,
            }
        )
        bound_features.update(
            {
                f"bound-line-surface::{line_number}::{surface}",
                f"bound-target-surface::{target_semantic_token}::{surface}",
                f"bound-target-exact-surface::{target_exact_token}::{surface}",
            }
        )
        for alias, count in sources.items():
            source_semantics = legend[alias]
            aggregate[source_semantics] += count
            pair_counts[(target_semantics, source_semantics)] += count
        ordered_terms: list[tuple[str, str]] = []
        exact_terms: list[tuple[str, str, str]] = []
        terms = _canonical_coefficient_terms(basis, legend)
        for _, source_alias, coefficient in terms or ():
            source_semantics = legend[source_alias]
            semantic = _semantic_token(source_semantics)
            exact_semantic = _semantic_token(raw_legend[source_alias])
            coefficient_text = _fraction_text(coefficient)
            coefficient_counts[
                (semantic, coefficient_text)
            ] += 1
            ordered_terms.append((semantic, coefficient_text))
            exact_terms.append((semantic, exact_semantic, coefficient_text))
        if ordered_terms:
            ordered_rows[tuple(ordered_terms)] += 1
            exact_row = _canonical_digest({"ordered_exact_terms": exact_terms})
            exact_ordered_rows[exact_row] += 1
            bound_features.update(
                {
                    f"bound-affine-line::{line_number}::{exact_row}",
                    f"bound-affine-target::{target_semantic_token}::{exact_row}",
                    f"bound-affine-target-exact::{target_exact_token}::{exact_row}",
                }
            )
    features = {
        f"aggregate-source::{description}::count::{min(count, 32)}"
        for description, count in aggregate.items()
    }
    features.update(
        f"aggregate-target-source::{target}::{source}::count::{min(count, 8)}"
        for (target, source), count in pair_counts.items()
    )
    features.update(
        f"aggregate-coefficient-source::{source}::{coefficient}::count::{min(count, 16)}"
        for (source, coefficient), count in coefficient_counts.items()
    )
    features.update(
        "ordered-coefficient-row::"
        + "|".join(f"{source}={coefficient}" for source, coefficient in row)
        + f"::count::{min(count, 16)}"
        for row, count in ordered_rows.items()
    )
    features.update(
        f"ordered-basis-surface::{surface}::count::{min(count, 16)}"
        for surface, count in line_surfaces.items()
    )
    features.update(
        f"ordered-exact-coefficient-row::{row}::count::{min(count, 16)}"
        for row, count in exact_ordered_rows.items()
    )
    features.update(bound_features)
    features.update(_declaration_ordinal_features(view, declared_rank))
    features.add(
        "ordered-candidate-surface::"
        + _canonical_digest({"ordered_candidate_rows": ordered_candidate_rows})
    )
    features.add(
        "ordered-declaration-sequence::"
        + _canonical_digest({"declarations": declaration_sequence})
    )
    features.add(
        "ordered-legend-surface::"
        + _canonical_digest(
            {
                "entries": [
                    [
                        _semantic_token(legend[alias]),
                        _semantic_token(description),
                    ]
                    for alias, description in raw_legend.items()
                ]
            }
        )
    )
    features.update(geometry_features)
    features.update(
        "legend-exact::"
        f"{_semantic_token(legend[alias])}::{_semantic_token(description)}"
        for alias, description in raw_legend.items()
    )
    mechanism_hint = _MECHANISM_HINT_RE.search(view.prompt)
    if mechanism_hint is not None:
        features.add(f"prompt-mechanism-hint::{mechanism_hint.group(1).lower()}")
    features.update(_token_features("prompt", _prompt_surface_tokens(view.prompt, legend)))
    features.update(
        _complete_prompt_features(
            view.prompt,
            legend=legend,
            raw_legend=raw_legend,
        )
    )
    return frozenset(features)


def extract_relational_solver_features_v3(
    view: RelationalSolverViewV3,
) -> RelationalSolverFeaturesV3:
    """Extract the model cache using solver-visible bytes and no labeled object."""

    if type(view) is not RelationalSolverViewV3:
        raise ValueError("relational feature extraction requires an exact solver view")
    view.validate_current()
    raw_legend = _raw_legend(view.prompt)
    legend = {
        alias: _normalized_semantics(description)
        for alias, description in raw_legend.items()
    }
    declarations = {
        line_number: value
        for line_number, line in view.candidate_lines
        if (value := _declared_value(line)) is not None
    }
    ordered_values = sorted(set(declarations.values()))
    declared_rank = {
        line_number: ordered_values.index(value) for line_number, value in declarations.items()
    }
    geometry, geometry_features = _coefficient_geometry(view, legend)
    line_features = tuple(
        (
            line_number,
            _line_features(
                view,
                line_number,
                line,
                legend=legend,
                raw_legend=raw_legend,
                geometry=geometry,
                declared_rank=declared_rank,
            ),
        )
        for line_number, line in view.candidate_lines
    )
    return RelationalSolverFeaturesV3(
        solver_view_digest=view.content_digest,
        line_features=line_features,
        case_features=_case_features(
            view,
            legend=legend,
            raw_legend=raw_legend,
            geometry_features=geometry_features,
            declared_rank=declared_rank,
        ),
    )


def _solver_feature_population_digest(
    features: Sequence[RelationalSolverFeaturesV3],
) -> str:
    for bundle in features:
        if type(bundle) is not RelationalSolverFeaturesV3:
            raise ValueError("solver-feature population contains a noncanonical bundle")
        bundle.validate_current()
    return _canonical_digest(
        {
            "feature_profile": RELATIONAL_SOLVER_FEATURE_PROFILE_V3,
            "features": sorted(bundle.model_content_digest for bundle in features),
        }
    )


def _input_digest(
    examples: Sequence[RelationalLeakageExampleV3],
    *,
    group_manifest_digest: str,
) -> str:
    group_manifest_digest = _validated_digest(
        group_manifest_digest, "relational input group-manifest digest"
    )
    records = sorted(
        (
            {
                "case_binding_digest": example.case_binding_digest,
                "family": example.family,
                "fault_line": example.fault_line,
                "group": example.group,
                "group_binding_digest": example.group_binding_digest,
                "mechanism": example.mechanism,
                "solver_view": {
                    "candidate_lines": [list(item) for item in example.candidate_lines],
                    "prompt": example.prompt,
                },
            }
            for example in examples
        ),
        key=lambda item: item["case_binding_digest"],
    )
    return _canonical_digest(
        {
            "feature_profile": RELATIONAL_SOLVER_FEATURE_PROFILE_V3,
            "group_manifest_digest": group_manifest_digest,
            "records": records,
            "schema": RELATIONAL_LEAKAGE_SCHEMA_V3,
            "schema_version": RELATIONAL_LEAKAGE_SCHEMA_VERSION_V3,
        }
    )


def relational_leakage_input_digest_v3(
    examples: Iterable[RelationalLeakageExampleV3],
    *,
    group_manifest: RelationalGroupManifestV3,
) -> str:
    """Commit the complete labeled population before fitting the release audit."""

    values = tuple(examples)
    if not values or not all(type(item) is RelationalLeakageExampleV3 for item in values):
        raise ValueError("relational input commitment requires exact examples")
    for example in values:
        example.validate_current()
    if type(group_manifest) is not RelationalGroupManifestV3:
        raise ValueError("relational input commitment requires an exact group manifest")
    group_manifest.validate_population(values)
    return _input_digest(
        values,
        group_manifest_digest=group_manifest.content_digest,
    )


@dataclass(frozen=True, slots=True)
class _BernoulliModel:
    base_score: float
    weights: Mapping[str, float]

    def score(self, features: frozenset[str]) -> float:
        return self.base_score + sum(self.weights.get(feature, 0.0) for feature in features)


def _fit_binary_presence_model(
    positive: Sequence[frozenset[str]], negative: Sequence[frozenset[str]]
) -> _BernoulliModel:
    if not positive or not negative:
        raise ValueError("relational classifier requires both positive and negative rows")
    positive_counts = Counter(feature for row in positive for feature in row)
    negative_counts = Counter(feature for row in negative for feature in row)
    base_score = 0.0
    weights: dict[str, float] = {}
    # This support index is fit from the non-held-out fold only.  Held-out
    # surfaces never decide which associations the model is allowed to use.
    supported_features = {
        feature
        for feature in set(positive_counts) | set(negative_counts)
        if positive_counts[feature] + negative_counts[feature]
        >= (
            MIN_RELATIONAL_BOUND_FEATURE_SUPPORT_V3
            if feature.startswith(_BOUND_FEATURE_PREFIXES_V3)
            else MIN_RELATIONAL_COMPLETE_PROMPT_SUPPORT_V3
            if feature.startswith("prompt-complete::")
            else 1
        )
    }
    for feature in sorted(supported_features):
        p_positive = (positive_counts[feature] + 1.0) / (len(positive) + 2.0)
        p_negative = (negative_counts[feature] + 1.0) / (len(negative) + 2.0)
        absent_log_odds = math.log1p(-p_positive) - math.log1p(-p_negative)
        base_score += absent_log_odds
        weights[feature] = (
            math.log(p_positive) - math.log(p_negative) - absent_log_odds
        )
    return _BernoulliModel(base_score, weights)


def _fit_fault_line_model(
    examples: Sequence[RelationalLeakageExampleV3],
    feature_cache: Sequence[RelationalSolverFeaturesV3],
    index_by_identity: Mapping[int, int],
) -> _BernoulliModel:
    positive: list[frozenset[str]] = []
    negative: list[frozenset[str]] = []
    for example in examples:
        example_index = index_by_identity[id(example)]
        bundle = feature_cache[example_index]
        for line_number, _ in example.candidate_lines:
            target = positive if line_number == example.fault_line else negative
            target.append(bundle.features_for_line(line_number))
    return _fit_binary_presence_model(positive, negative)


def evaluate_grouped_relational_leakage_v3(
    examples: Iterable[RelationalLeakageExampleV3],
    *,
    group_manifest: RelationalGroupManifestV3,
    expected_group_manifest_digest: str,
    expected_input_digest: str,
) -> RelationalLeakageReportV3:
    """Fit on non-held groups and predict from held-out solver views only."""

    if type(group_manifest) is not RelationalGroupManifestV3:
        raise ValueError("relational audit requires an exact group manifest")
    group_manifest.validate_expected_digest(expected_group_manifest_digest)
    values = tuple(examples)
    if len(values) < 4 or not all(type(item) is RelationalLeakageExampleV3 for item in values):
        raise ValueError("relational audit requires at least four exact examples")
    for example in values:
        example.validate_current()
    group_manifest.validate_population(values)
    expected_input_digest = _validated_digest(
        expected_input_digest, "expected relational input digest"
    )
    observed_input_digest = _input_digest(
        values,
        group_manifest_digest=group_manifest.content_digest,
    )
    if not hmac.compare_digest(observed_input_digest, expected_input_digest):
        raise ValueError("relational inputs do not match the committed manifest digest")
    groups = [entry.group for entry in group_manifest.entries]
    if len(groups) < 2:
        raise ValueError("relational audit requires at least two generator groups")
    if len({example.mechanism for example in values}) < 2:
        raise ValueError("relational audit requires at least two mechanisms")

    case_bindings = [example.case_binding_digest for example in values]
    solver_views = [example.solver_view.content_digest for example in values]
    if len(set(case_bindings)) != len(case_bindings):
        raise ValueError("relational audit case bindings must be globally unique")
    if len(set(solver_views)) != len(solver_views):
        raise ValueError("relational audit solver views must be globally unique")
    group_to_binding: dict[str, str] = {}
    binding_to_group: dict[str, str] = {}
    for example in values:
        prior_binding = group_to_binding.setdefault(example.group, example.group_binding_digest)
        prior_group = binding_to_group.setdefault(example.group_binding_digest, example.group)
        if prior_binding != example.group_binding_digest or prior_group != example.group:
            raise ValueError("relational group names and bindings must form a bijection")

    index_by_identity = {id(example): index for index, example in enumerate(values)}
    if len(index_by_identity) != len(values):
        raise ValueError("relational examples must be distinct objects")
    feature_cache = tuple(
        extract_relational_solver_features_v3(example.solver_view) for example in values
    )
    solver_feature_digest = _solver_feature_population_digest(feature_cache)
    group_by_case = {
        case_digest: entry.group
        for entry in group_manifest.entries
        for case_digest in entry.case_binding_digests
    }

    folds: list[RelationalLeakageFoldV3] = []
    for held_group in groups:
        training = tuple(
            example
            for example in values
            if group_by_case[example.case_binding_digest] != held_group
        )
        held_out = tuple(
            example
            for example in values
            if group_by_case[example.case_binding_digest] == held_group
        )
        if not training or not held_out:
            raise ValueError("relational audit produced an empty training or held-out fold")
        mechanisms = sorted({example.mechanism for example in training})
        if len(mechanisms) < 2:
            raise ValueError("every training fold must contain at least two mechanisms")
        if not {example.mechanism for example in held_out} <= set(mechanisms):
            raise ValueError("held-out mechanisms must all occur in the training fold")

        fault_model = _fit_fault_line_model(training, feature_cache, index_by_identity)
        mechanism_models: dict[str, _BernoulliModel] = {}
        for mechanism in mechanisms:
            positive = [
                feature_cache[index_by_identity[id(example)]].case_features
                for example in training
                if example.mechanism == mechanism
            ]
            negative = [
                feature_cache[index_by_identity[id(example)]].case_features
                for example in training
                if example.mechanism != mechanism
            ]
            mechanism_models[mechanism] = _fit_binary_presence_model(positive, negative)

        fault_line_correct = 0
        mechanism_correct = 0
        for example in held_out:
            example_index = index_by_identity[id(example)]
            bundle = feature_cache[example_index]
            predicted_line = max(
                (line_number for line_number, _ in example.candidate_lines),
                key=lambda line_number: (
                    fault_model.score(bundle.features_for_line(line_number)),
                    -line_number,
                ),
            )
            predicted_mechanism = max(
                mechanisms,
                key=lambda mechanism: (
                    mechanism_models[mechanism].score(bundle.case_features),
                    mechanism,
                ),
            )
            fault_line_correct += predicted_line == example.fault_line
            mechanism_correct += predicted_mechanism == example.mechanism
        folds.append(
            RelationalLeakageFoldV3(
                group=held_group,
                total_cases=len(held_out),
                fault_line_correct=fault_line_correct,
                mechanism_correct=mechanism_correct,
            )
        )

    return RelationalLeakageReportV3(
        total_cases=len(values),
        fault_line_correct=sum(fold.fault_line_correct for fold in folds),
        mechanism_correct=sum(fold.mechanism_correct for fold in folds),
        group_manifest_digest=group_manifest.content_digest,
        input_digest=observed_input_digest,
        solver_feature_digest=solver_feature_digest,
        maximum_fault_line_accuracy=DEFAULT_MAXIMUM_FAULT_LINE_ACCURACY_V3,
        maximum_mechanism_accuracy=DEFAULT_MAXIMUM_MECHANISM_ACCURACY_V3,
        folds=tuple(folds),
    )


__all__ = [
    "DEFAULT_MAXIMUM_FAULT_LINE_ACCURACY_V3",
    "DEFAULT_MAXIMUM_MECHANISM_ACCURACY_V3",
    "RELATIONAL_GROUP_MANIFEST_SCHEMA_V3",
    "RELATIONAL_GROUP_MANIFEST_SCHEMA_VERSION_V3",
    "RELATIONAL_LEAKAGE_SCHEMA_V3",
    "RELATIONAL_LEAKAGE_SCHEMA_VERSION_V3",
    "RELATIONAL_SOLVER_FEATURE_PROFILE_V3",
    "RelationalGroupManifestEntryV3",
    "RelationalGroupManifestV3",
    "RelationalLeakageExampleV3",
    "RelationalLeakageFoldV3",
    "RelationalLeakageReportV3",
    "RelationalSolverFeaturesV3",
    "RelationalSolverViewV3",
    "build_relational_group_manifest_v3",
    "evaluate_grouped_relational_leakage_v3",
    "extract_relational_solver_features_v3",
    "relational_leakage_input_digest_v3",
]
