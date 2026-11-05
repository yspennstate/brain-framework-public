"""Pinned, outcome-blind MathNet-v0 bank construction for the three-arm study.

This module intentionally has no Hugging Face dependency.  Acquisition must resolve the
dataset revision, hash the exact 56-shard materialization into an independently verified
artifact manifest, and hand this layer its exact official-order row stream.  The builder
then fails closed on schema drift, commits all 27,817 normalized rows, and keeps official
solution text out of the bank.  Ticket ranking is gold-free *conditional on* a frozen
scoreability filter; pool membership is honestly gold-gated and must be fixed before any
study outcome.  The resulting bank is private: only
:meth:`MathNetBankV3.public_commitment_payload` is safe to publish by default.

The public MathNet release is not contamination-clean and its answer annotations are
LLM-assisted.  Terminal-box agreement is only optional corroboration; every selected
answer requires a blinded source audit.  The pinned release also needs a separate signed
language audit for null-language candidates, a domain/difficulty and duplicate-group
manifest, and a rights manifest.  This module is therefore an under-construction bank
primitive, not confirmatory readiness by itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .exact_answer_v3 import ExactRationalV3, parse_exact_rational_v3
from .models import canonical_json

MATHNET_HF_REPOSITORY_V3 = "ShadenA/MathNet"
MATHNET_HF_REVISION_V3 = "33e6b3bc254e6f0f0c1479b4252f5e9dd551d56c"
MATHNET_HF_CONFIG_V3 = "all"
MATHNET_HF_SPLIT_V3 = "train"
MATHNET_HF_ROW_COUNT_V3 = 27_817

MATHNET_HF_COLUMNS_V3 = (
    "id",
    "problem_markdown",
    "solutions_markdown",
    "images",
    "country",
    "competition",
    "topics_flat",
    "language",
    "problem_type",
    "final_answer",
)
MATHNET_HF_FEATURE_KINDS_V3 = (
    ("id", "nullable<string>"),
    ("problem_markdown", "nullable<string>"),
    ("solutions_markdown", "nullable<list<string>>"),
    ("images", "nullable<list<struct<bytes:binary,path:string>>>"),
    ("country", "nullable<string>"),
    ("competition", "nullable<string>"),
    ("topics_flat", "nullable<list<string>>"),
    ("language", "nullable<string>"),
    ("problem_type", "nullable<string>"),
    ("final_answer", "nullable<string>"),
)

MATHNET_BANK_SCHEMA_V3 = "org.math-research-brain/mathnet-private-bank"
MATHNET_BANK_SCHEMA_VERSION_V3 = 1
MATHNET_SOLVER_VIEW_SCHEMA_V3 = "org.math-research-brain/mathnet-solver-view"
MATHNET_SOLVER_ASSIGNMENT_SCHEMA_V3 = (
    "org.math-research-brain/mathnet-solver-assignment"
)
MATHNET_PUBLIC_SCHEMA_VERSION_V3 = 1
MATHNET_RESPONSE_CONTRACT_V3 = (
    "Put the final answer on the final non-empty line exactly as:\n"
    "FINAL_ANSWER: <integer-or-fraction>"
)

MAX_PROMPT_BYTES_V3 = 32 * 1024
MAX_SOLUTION_BYTES_V3 = 128 * 1024
MAX_SOLUTIONS_TOTAL_BYTES_V3 = 256 * 1024
MAX_SOLUTIONS_V3 = 8
MAX_TOPICS_V3 = 32
MAX_OUTPUT_BYTES_V3 = 64 * 1024
MAX_PUBLIC_JSON_BYTES_V3 = 96 * 1024
MAX_SOURCE_PROMPT_BYTES_V3 = 1024 * 1024
MAX_SOURCE_SOLUTION_BYTES_V3 = 1024 * 1024
MAX_SOURCE_SOLUTIONS_TOTAL_BYTES_V3 = 4 * 1024 * 1024
MAX_SOURCE_SOLUTIONS_V3 = 64
MAX_SOURCE_TOPICS_V3 = 256
MAX_SOURCE_IMAGES_V3 = 64

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_FULL_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
_SOLVER_ID_RE = re.compile(r"mathnet-v3-[0-9a-f]{64}\Z")
_FINAL_LINE_RE = re.compile(r"FINAL_ANSWER: (?P<answer>\S+)\Z", re.ASCII)

_SOURCE_BINDING_DOMAIN = "org.math-research-brain/mathnet-source-binding/v3"
_LEAF_TEXT_DOMAIN = "org.math-research-brain/mathnet-leaf-text/v3"
_ROW_DOMAIN = "org.math-research-brain/mathnet-row/v3"
_PUBLIC_SELECTION_DOMAIN = "org.math-research-brain/mathnet-public-selection/v3"
_SOURCE_CLUSTER_DOMAIN = "org.math-research-brain/mathnet-source-cluster/v3"
_PRIVATE_GOLD_DOMAIN = "org.math-research-brain/mathnet-private-gold/v3"
_CANDIDATE_DOMAIN = "org.math-research-brain/mathnet-candidate/v3"
_BANK_ENTRY_DOMAIN = "org.math-research-brain/mathnet-bank-entry/v3"
_BANK_DOMAIN = "org.math-research-brain/mathnet-bank/v3"
_ORDERED_SOURCE_ROWS_DOMAIN = b"math-brain/mathnet-v3/ordered-source-rows/v1\x00"
_SEED_COMMITMENT_DOMAIN = b"math-brain/mathnet-v3/selection-seed/v1\x00"
_ROW_TICKET_DOMAIN = b"math-brain/mathnet-v3/row-ticket/v1\x00"
_CLUSTER_TICKET_DOMAIN = b"math-brain/mathnet-v3/cluster-ticket/v1\x00"
_SOLVER_ID_DOMAIN = b"math-brain/mathnet-v3/solver-id/v1\x00"

_IMAGE_OR_FIGURE_RE = re.compile(
    r"!\[[^\]]*\]\(|<\s*img\b|\\includegraphics\b|"
    r"\b(?:figure|diagram|picture|photograph|illustration)\b|"
    r"\bshown\s+(?:above|below)\b|\bon\s+the\s+(?:left|right)\b",
    re.IGNORECASE,
)
_ROLE_LINE_RE = re.compile(
    r"(?im)^\s*(?:system|assistant|developer|user)\s*:|"
    r"<\|(?:system|assistant|developer|user)[^>]*\|>|\[/?INST\]"
)
_INJECTION_RE = re.compile(
    r"\b(?:ignore|disregard|override|forget)\s+(?:all\s+)?"
    r"(?:previous|prior|above)\b|"
    r"\byou\s+are\s+(?:chatgpt|an?\s+ai|a\s+language\s+model)\b",
    re.IGNORECASE,
)
_TERMINAL_TAIL_RE = re.compile(r"(?:\$|\\\]|\\\))?[.,;:]?\Z", re.ASCII)


def _digest(value: object, *, domain: str) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json({"domain": domain, "payload": value}).encode("utf-8")
    ).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _validate_seed(seed: object, label: str) -> bytes:
    if type(seed) is not bytes or not 32 <= len(seed) <= 64:
        raise ValueError(f"{label} must be 32 to 64 immutable bytes")
    return seed


def _seed_commitment(seed: bytes) -> str:
    _validate_seed(seed, "MathNet selection seed")
    return "sha256:" + hashlib.sha256(_SEED_COMMITMENT_DOMAIN + seed).hexdigest()


def _utf8_size(value: object, label: str, *, maximum: int) -> int:
    if type(value) is not str:
        raise ValueError(f"{label} must be exact text")
    if len(value) > maximum:
        return maximum + 1
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8 text") from exc


def _strict_keys(raw: object, expected: tuple[str, ...], label: str) -> dict[str, Any]:
    if type(raw) is not dict or not all(type(key) is str for key in raw):
        raise ValueError(f"{label} must be an exact object")
    if tuple(raw) != expected:
        raise ValueError(f"{label} fields must be exactly {expected!r} in pinned order")
    return raw


def _integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an exact integer in [{minimum}, {maximum}]")
    return value


def _text_leaf_digest(value: str) -> str:
    return _digest(value, domain=_LEAF_TEXT_DOMAIN)


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_canonical_json(raw: object, label: str) -> object:
    if type(raw) is not str:
        raise ValueError(f"{label} JSON must be exact text")
    if _utf8_size(raw, f"{label} JSON", maximum=MAX_PUBLIC_JSON_BYTES_V3) > (
        MAX_PUBLIC_JSON_BYTES_V3
    ):
        raise ValueError(f"{label} JSON exceeds its byte budget")
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError(f"{label} JSON is invalid: {exc}") from exc
    if raw != canonical_json(parsed):
        raise ValueError(f"{label} JSON must use canonical encoding")
    return parsed


class MathNetSourceAuthorityV3(StrEnum):
    OFFICIAL_BYTE_VERIFIED = "official_byte_verified"
    SYNTHETIC_TEST = "synthetic_test"


MATHNET_SYNTHETIC_REPOSITORY_V3 = "synthetic/MathNet-v3-fixture"
MATHNET_SYNTHETIC_REVISION_V3 = "0" * 40
MATHNET_SYNTHETIC_CONFIG_V3 = "synthetic-all"
MATHNET_SYNTHETIC_SPLIT_V3 = "synthetic-train"
MATHNET_SYNTHETIC_MANIFEST_DIGEST_V3 = _digest(
    {
        "purpose": "unit-tests-only",
        "row_count": MATHNET_HF_ROW_COUNT_V3,
        "schema": [list(item) for item in MATHNET_HF_FEATURE_KINDS_V3],
    },
    domain="org.math-research-brain/mathnet-synthetic-source/v3",
)
_SOURCE_BINDING_TOKEN_V3 = object()


@dataclass(frozen=True, slots=True, init=False)
class MathNetSourceBindingV3:
    """Typed source identity; direct construction is intentionally unavailable."""

    authority: MathNetSourceAuthorityV3
    repository_id: str
    revision: str
    config_name: str
    split: str
    row_count: int
    ordered_feature_kinds: tuple[tuple[str, str], ...]
    source_artifact_manifest_digest: str
    content_digest: str = field(init=False)

    def __new__(cls, token: object = None) -> MathNetSourceBindingV3:
        if token is not _SOURCE_BINDING_TOKEN_V3:
            raise TypeError("MathNet source bindings require a typed source factory")
        return object.__new__(cls)

    @classmethod
    def _derive(
        cls,
        *,
        authority: MathNetSourceAuthorityV3,
        repository_id: str,
        revision: str,
        config_name: str,
        split: str,
        row_count: int,
        ordered_feature_kinds: tuple[tuple[str, str], ...],
        source_artifact_manifest_digest: str,
    ) -> MathNetSourceBindingV3:
        instance = object.__new__(cls)
        object.__setattr__(instance, "authority", authority)
        object.__setattr__(instance, "repository_id", repository_id)
        object.__setattr__(instance, "revision", revision)
        object.__setattr__(instance, "config_name", config_name)
        object.__setattr__(instance, "split", split)
        object.__setattr__(instance, "row_count", row_count)
        object.__setattr__(instance, "ordered_feature_kinds", ordered_feature_kinds)
        object.__setattr__(
            instance,
            "source_artifact_manifest_digest",
            source_artifact_manifest_digest,
        )
        instance._validate_fields()
        object.__setattr__(
            instance,
            "content_digest",
            _digest(instance._payload(), domain=_SOURCE_BINDING_DOMAIN),
        )
        return instance

    def __post_init__(self) -> None:  # pragma: no cover - init is deliberately disabled
        raise TypeError("MathNet source bindings require a typed source factory")

    def _validate_fields(self) -> None:
        if type(self) is not MathNetSourceBindingV3:
            raise ValueError("MathNet source-binding subclasses are forbidden")
        if type(self.authority) is not MathNetSourceAuthorityV3:
            raise ValueError("MathNet source authority must be exact")
        if self.authority is MathNetSourceAuthorityV3.OFFICIAL_BYTE_VERIFIED:
            expected_identity = (
                MATHNET_HF_REPOSITORY_V3,
                MATHNET_HF_REVISION_V3,
                MATHNET_HF_CONFIG_V3,
                MATHNET_HF_SPLIT_V3,
            )
        else:
            expected_identity = (
                MATHNET_SYNTHETIC_REPOSITORY_V3,
                MATHNET_SYNTHETIC_REVISION_V3,
                MATHNET_SYNTHETIC_CONFIG_V3,
                MATHNET_SYNTHETIC_SPLIT_V3,
            )
        observed_identity = (
            self.repository_id,
            self.revision,
            self.config_name,
            self.split,
        )
        if not all(type(item) is str for item in observed_identity) or (
            observed_identity != expected_identity
        ):
            raise ValueError("MathNet source identity differs from its typed authority")
        if type(self.row_count) is not int or self.row_count != MATHNET_HF_ROW_COUNT_V3:
            raise ValueError("MathNet source row count differs from the pinned schema")
        if type(self.ordered_feature_kinds) is not tuple or (
            self.ordered_feature_kinds != MATHNET_HF_FEATURE_KINDS_V3
        ):
            raise ValueError("MathNet source feature schema differs from the pin")
        if _FULL_COMMIT_RE.fullmatch(self.revision) is None:
            raise ValueError("MathNet revision must be a full lowercase commit ID")
        if not all(
            type(item) is tuple
            and len(item) == 2
            and all(type(part) is str for part in item)
            for item in self.ordered_feature_kinds
        ):
            raise ValueError("MathNet feature kinds must be exact string pairs")
        _validate_digest(
            self.source_artifact_manifest_digest,
            "MathNet source artifact-manifest digest",
        )
        if self.authority is MathNetSourceAuthorityV3.SYNTHETIC_TEST and (
            self.source_artifact_manifest_digest != MATHNET_SYNTHETIC_MANIFEST_DIGEST_V3
        ):
            raise ValueError("synthetic MathNet source has a non-synthetic manifest")

    def _payload(self) -> dict[str, object]:
        return {
            "authority": self.authority.value,
            "config_name": self.config_name,
            "ordered_feature_kinds": [list(item) for item in self.ordered_feature_kinds],
            "repository_id": self.repository_id,
            "revision": self.revision,
            "row_count": self.row_count,
            "source_artifact_manifest_digest": self.source_artifact_manifest_digest,
            "split": self.split,
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetSourceBindingV3:
            raise ValueError("MathNet source-binding subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet source-binding digest")
        expected = _digest(self._payload(), domain=_SOURCE_BINDING_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet source-binding digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def synthetic_mathnet_source_binding_v3() -> MathNetSourceBindingV3:
    """Return the only binding exposed to synthetic unit-test row streams."""

    return MathNetSourceBindingV3._derive(
        authority=MathNetSourceAuthorityV3.SYNTHETIC_TEST,
        repository_id=MATHNET_SYNTHETIC_REPOSITORY_V3,
        revision=MATHNET_SYNTHETIC_REVISION_V3,
        config_name=MATHNET_SYNTHETIC_CONFIG_V3,
        split=MATHNET_SYNTHETIC_SPLIT_V3,
        row_count=MATHNET_HF_ROW_COUNT_V3,
        ordered_feature_kinds=MATHNET_HF_FEATURE_KINDS_V3,
        source_artifact_manifest_digest=MATHNET_SYNTHETIC_MANIFEST_DIGEST_V3,
    )


def _official_mathnet_source_binding_v3(
    source_artifact_manifest_digest: str,
) -> MathNetSourceBindingV3:
    """Acquisition-internal binding; downstream official APIs require its wrapper type."""

    _validate_digest(
        source_artifact_manifest_digest,
        "MathNet source artifact-manifest digest",
    )
    return MathNetSourceBindingV3._derive(
        authority=MathNetSourceAuthorityV3.OFFICIAL_BYTE_VERIFIED,
        repository_id=MATHNET_HF_REPOSITORY_V3,
        revision=MATHNET_HF_REVISION_V3,
        config_name=MATHNET_HF_CONFIG_V3,
        split=MATHNET_HF_SPLIT_V3,
        row_count=MATHNET_HF_ROW_COUNT_V3,
        ordered_feature_kinds=MATHNET_HF_FEATURE_KINDS_V3,
        source_artifact_manifest_digest=source_artifact_manifest_digest,
    )


@dataclass(frozen=True, slots=True)
class MathNetPrivateGoldV3:
    """Private provisional answer evidence; never sufficient without a blind gold audit."""

    answer: ExactRationalV3 = field(repr=False)
    final_answer_text_digest: str = field(repr=False)
    solution_text_digests: tuple[str, ...] = field(repr=False)
    confirming_solution_indices: tuple[int, ...] = field(repr=False)
    confirmation_rule: str = "annotation-only-pending-blind-audit-v1"
    content_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetPrivateGoldV3:
            raise ValueError("MathNet private-gold subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._payload(), domain=_PRIVATE_GOLD_DOMAIN),
        )

    def _validate_fields(self) -> None:
        if type(self.answer) is not ExactRationalV3:
            raise ValueError("MathNet private gold requires an exact rational")
        self.answer.validate_current()
        _validate_digest(self.final_answer_text_digest, "MathNet final-answer text digest")
        if type(self.solution_text_digests) is not tuple or not (
            1 <= len(self.solution_text_digests) <= MAX_SOLUTIONS_V3
        ):
            raise ValueError("MathNet solution digests must be a bounded exact tuple")
        for digest in self.solution_text_digests:
            _validate_digest(digest, "MathNet solution text digest")
        if type(self.confirming_solution_indices) is not tuple:
            raise ValueError("MathNet confirming solution indices must be an exact tuple")
        if not all(
            type(index) is int and 0 <= index < len(self.solution_text_digests)
            for index in self.confirming_solution_indices
        ):
            raise ValueError("MathNet confirming solution index is invalid")
        if tuple(sorted(set(self.confirming_solution_indices))) != (
            self.confirming_solution_indices
        ):
            raise ValueError("MathNet confirming solution indices must be unique and ordered")
        if self.confirmation_rule not in {
            "annotation-only-pending-blind-audit-v1",
            "terminal-boxed-exact-v1",
        }:
            raise ValueError("MathNet confirmation rule is unsupported")
        if (
            self.confirmation_rule == "terminal-boxed-exact-v1"
            and not self.confirming_solution_indices
        ):
            raise ValueError("terminal-boxed confirmation requires at least one solution")
        if (
            self.confirmation_rule == "annotation-only-pending-blind-audit-v1"
            and self.confirming_solution_indices
        ):
            raise ValueError("annotation-only evidence cannot claim confirming solutions")

    def _payload(self) -> dict[str, object]:
        return {
            "answer": self.answer.to_dict(),
            "confirmation_rule": self.confirmation_rule,
            "confirming_solution_indices": list(self.confirming_solution_indices),
            "final_answer_text_digest": self.final_answer_text_digest,
            "solution_text_digests": list(self.solution_text_digests),
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetPrivateGoldV3:
            raise ValueError("MathNet private-gold subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet private-gold digest")
        expected = _digest(self._payload(), domain=_PRIVATE_GOLD_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet private-gold digest is stale")

    def _private_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def _public_selection_payload(
    *,
    source_ordinal: int,
    source_id: str,
    country: str,
    competition: str,
    language: str,
    topics_flat: tuple[str, ...],
    problem_markdown: str,
) -> dict[str, object]:
    return {
        "competition": competition,
        "country": country,
        "language": language,
        "problem_text_digest": _text_leaf_digest(problem_markdown),
        "source_id": source_id,
        "source_ordinal": source_ordinal,
        "topics_flat": list(topics_flat),
    }


def _row_payload_from_candidate(candidate: MathNetCandidateV3) -> dict[str, object]:
    return {
        "competition": candidate.competition,
        "country": candidate.country,
        "final_answer_text_digest": candidate.gold.final_answer_text_digest,
        "images_count": 0,
        "language": candidate.language,
        "problem_markdown": candidate.problem_markdown,
        "problem_type": "final answer only",
        "solution_text_digests": list(candidate.gold.solution_text_digests),
        "source_binding_digest": candidate.source_binding_digest,
        "source_id": candidate.source_id,
        "source_ordinal": candidate.source_ordinal,
        "topics_flat": list(candidate.topics_flat),
    }


@dataclass(frozen=True, slots=True)
class MathNetCandidateV3:
    """One eligible private row with gold-free sampling coordinates."""

    source_binding_digest: str
    source_ordinal: int
    source_id: str
    country: str
    competition: str
    language: str
    topics_flat: tuple[str, ...]
    problem_markdown: str = field(repr=False)
    row_digest: str
    public_selection_digest: str
    source_cluster_digest: str
    gold: MathNetPrivateGoldV3 = field(repr=False)
    content_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetCandidateV3:
            raise ValueError("MathNet candidate subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._private_payload(), domain=_CANDIDATE_DOMAIN),
        )

    def _validate_fields(self) -> None:
        _validate_digest(self.source_binding_digest, "MathNet candidate source-binding digest")
        _integer(
            self.source_ordinal,
            "MathNet canonical source ordinal",
            minimum=0,
            maximum=MATHNET_HF_ROW_COUNT_V3 - 1,
        )
        for value, label, maximum in (
            (self.source_id, "MathNet source ID", 1024),
            (self.country, "MathNet country", 1024),
            (self.competition, "MathNet competition", 2048),
            (self.language, "MathNet language", 128),
        ):
            if _utf8_size(value, label, maximum=maximum) > maximum or not value:
                raise ValueError(f"{label} is empty or exceeds its byte budget")
        if self.language != "English":
            raise ValueError("MathNet candidate language must be English")
        if type(self.topics_flat) is not tuple or len(self.topics_flat) > MAX_TOPICS_V3:
            raise ValueError("MathNet candidate topics must be a bounded exact tuple")
        if not all(
            type(topic) is str
            and bool(topic)
            and _utf8_size(topic, "MathNet topic", maximum=1024) <= 1024
            for topic in self.topics_flat
        ):
            raise ValueError("MathNet candidate contains an invalid topic")
        if (
            _utf8_size(
                self.problem_markdown,
                "MathNet problem markdown",
                maximum=MAX_PROMPT_BYTES_V3,
            )
            > MAX_PROMPT_BYTES_V3
            or not self.problem_markdown.strip()
        ):
            raise ValueError("MathNet candidate problem is empty or over budget")
        if _IMAGE_OR_FIGURE_RE.search(self.problem_markdown):
            raise ValueError("MathNet candidate refers to non-text visual material")
        if _ROLE_LINE_RE.search(self.problem_markdown) or _INJECTION_RE.search(
            self.problem_markdown
        ):
            raise ValueError("MathNet candidate contains unsafe prompt markup")
        for digest, label in (
            (self.row_digest, "MathNet row digest"),
            (self.public_selection_digest, "MathNet public-selection digest"),
            (self.source_cluster_digest, "MathNet source-cluster digest"),
        ):
            _validate_digest(digest, label)
        if type(self.gold) is not MathNetPrivateGoldV3:
            raise ValueError("MathNet candidate requires exact private gold")
        self.gold.validate_current()

        expected_public = _digest(
            _public_selection_payload(
                source_ordinal=self.source_ordinal,
                source_id=self.source_id,
                country=self.country,
                competition=self.competition,
                language=self.language,
                topics_flat=self.topics_flat,
                problem_markdown=self.problem_markdown,
            ),
            domain=_PUBLIC_SELECTION_DOMAIN,
        )
        expected_cluster = _digest(
            {"competition": self.competition, "country": self.country},
            domain=_SOURCE_CLUSTER_DOMAIN,
        )
        expected_row = _digest(_row_payload_from_candidate(self), domain=_ROW_DOMAIN)
        if not hmac.compare_digest(self.public_selection_digest, expected_public):
            raise ValueError("MathNet candidate public-selection digest is stale")
        if not hmac.compare_digest(self.source_cluster_digest, expected_cluster):
            raise ValueError("MathNet candidate source-cluster digest is stale")
        if not hmac.compare_digest(self.row_digest, expected_row):
            raise ValueError("MathNet candidate row digest is stale")

    def _private_payload(self) -> dict[str, object]:
        return {
            "competition": self.competition,
            "country": self.country,
            "gold": self.gold._private_dict(),
            "language": self.language,
            "problem_markdown": self.problem_markdown,
            "public_selection_digest": self.public_selection_digest,
            "row_digest": self.row_digest,
            "source_binding_digest": self.source_binding_digest,
            "source_cluster_digest": self.source_cluster_digest,
            "source_id": self.source_id,
            "source_ordinal": self.source_ordinal,
            "topics_flat": list(self.topics_flat),
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetCandidateV3:
            raise ValueError("MathNet candidate subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet candidate digest")
        expected = _digest(self._private_payload(), domain=_CANDIDATE_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet candidate digest is stale")

    def _private_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._private_payload() | {"content_digest": self.content_digest}


class MathNetEligibilityCodeV3(StrEnum):
    ELIGIBLE = "eligible"
    NON_ENGLISH = "non_english"
    LANGUAGE_AUDIT_REQUIRED = "language_audit_required"
    MISSING_SOURCE_METADATA = "missing_source_metadata"
    NON_TEXT_ONLY = "non_text_only"
    UNSAFE_PROMPT_MARKUP = "unsafe_prompt_markup"
    UNSUPPORTED_PROBLEM_TYPE = "unsupported_problem_type"
    MISSING_ANSWER = "missing_answer"
    MISSING_SOLUTION_EVIDENCE = "missing_solution_evidence"
    UNSUPPORTED_ANSWER = "unsupported_answer"
    MISSING_SOLUTION_CONFIRMATION = "missing_solution_confirmation"
    SOLUTION_DISAGREEMENT = "solution_disagreement"
    TEXT_BUDGET_EXCEEDED = "text_budget_exceeded"


@dataclass(frozen=True, slots=True)
class MathNetEligibilityDecisionV3:
    """A content-free rejection reason or one fully validated candidate."""

    code: MathNetEligibilityCodeV3
    source_ordinal: int
    opaque_source_digest: str
    candidate: MathNetCandidateV3 | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetEligibilityDecisionV3:
            raise ValueError("MathNet eligibility-decision subclasses are forbidden")
        if type(self.code) is not MathNetEligibilityCodeV3:
            raise ValueError("MathNet eligibility code must be exact")
        _integer(
            self.source_ordinal,
            "MathNet eligibility source ordinal",
            minimum=0,
            maximum=MATHNET_HF_ROW_COUNT_V3 - 1,
        )
        _validate_digest(self.opaque_source_digest, "MathNet opaque source digest")
        if self.code is MathNetEligibilityCodeV3.ELIGIBLE:
            if type(self.candidate) is not MathNetCandidateV3:
                raise ValueError("eligible MathNet decision must carry one candidate")
            self.candidate.validate_current()
            if self.candidate.source_ordinal != self.source_ordinal:
                raise ValueError("MathNet decision and candidate ordinals differ")
        elif self.candidate is not None:
            raise ValueError("ineligible MathNet decision must not carry content")


@dataclass(frozen=True, slots=True)
class _MathNetRowFieldsV3:
    source_id: str
    country: str | None
    competition: str | None
    problem_markdown: str
    solutions_markdown: tuple[str, ...]
    topics_flat: tuple[str, ...]
    language: str | None
    images_count: int
    problem_type: str | None
    final_answer: str | None
    text_budget_exceeded: bool


def _read_mathnet_row_v3(raw: object) -> _MathNetRowFieldsV3:
    row = _strict_keys(raw, MATHNET_HF_COLUMNS_V3, "MathNet row")
    for field_name in ("id", "problem_markdown"):
        if type(row[field_name]) is not str:
            raise ValueError(f"MathNet {field_name} must be exact text")
    for field_name in (
        "country",
        "competition",
        "language",
        "problem_type",
        "final_answer",
    ):
        if type(row[field_name]) not in (str, type(None)):
            raise ValueError(f"MathNet {field_name} must be exact text or null")
    for field_name in ("solutions_markdown", "topics_flat", "images"):
        if type(row[field_name]) is not list:
            raise ValueError(f"MathNet {field_name} must be an exact array")

    source_id = row["id"]
    country = row["country"]
    competition = row["competition"]
    problem_markdown = row["problem_markdown"]
    language = row["language"]
    problem_type = row["problem_type"]
    final_answer = row["final_answer"]
    if not source_id:
        raise ValueError("MathNet source ID must be nonempty")
    for value, label, maximum in ((source_id, "MathNet source ID", 1024),):
        if _utf8_size(value, label, maximum=maximum) > maximum:
            raise ValueError(f"{label} exceeds its structural byte cap")
    for value, label, maximum in (
        (country, "MathNet country", 1024),
        (competition, "MathNet competition", 2048),
        (language, "MathNet language", 128),
    ):
        if value is not None and (
            not value or _utf8_size(value, label, maximum=maximum) > maximum
        ):
            raise ValueError(f"{label} is empty or exceeds its structural byte cap")

    prompt_source_size = _utf8_size(
        problem_markdown,
        "MathNet problem markdown",
        maximum=MAX_SOURCE_PROMPT_BYTES_V3,
    )
    if prompt_source_size > MAX_SOURCE_PROMPT_BYTES_V3:
        raise ValueError("MathNet problem exceeds the absolute source byte cap")
    if not problem_markdown.strip():
        raise ValueError("MathNet problem markdown must be nonempty")
    budget_exceeded = prompt_source_size > MAX_PROMPT_BYTES_V3
    if problem_type is not None:
        budget_exceeded |= (
            _utf8_size(problem_type, "MathNet problem type", maximum=256) > 256
        )
    if final_answer is not None:
        budget_exceeded |= (
            _utf8_size(final_answer, "MathNet final answer", maximum=1024) > 1024
        )

    raw_solutions = row["solutions_markdown"]
    if len(raw_solutions) > MAX_SOURCE_SOLUTIONS_V3:
        raise ValueError("MathNet row exceeds the absolute solution-count cap")
    if not all(type(solution) is str for solution in raw_solutions):
        raise ValueError("MathNet solutions must contain exact text only")
    solutions = tuple(raw_solutions)
    solution_sizes = tuple(
        _utf8_size(
            solution,
            "MathNet solution",
            maximum=MAX_SOURCE_SOLUTION_BYTES_V3,
        )
        for solution in solutions
    )
    if any(size > MAX_SOURCE_SOLUTION_BYTES_V3 for size in solution_sizes) or (
        sum(solution_sizes) > MAX_SOURCE_SOLUTIONS_TOTAL_BYTES_V3
    ):
        raise ValueError("MathNet solutions exceed an absolute source byte cap")
    budget_exceeded |= len(solutions) > MAX_SOLUTIONS_V3
    budget_exceeded |= any(size > MAX_SOLUTION_BYTES_V3 for size in solution_sizes)
    budget_exceeded |= sum(solution_sizes) > MAX_SOLUTIONS_TOTAL_BYTES_V3

    raw_topics = row["topics_flat"]
    if len(raw_topics) > MAX_SOURCE_TOPICS_V3:
        raise ValueError("MathNet row exceeds the absolute topic-count cap")
    if not all(type(topic) is str for topic in raw_topics):
        raise ValueError("MathNet topics must contain exact text only")
    topics = tuple(raw_topics)
    for topic in topics:
        if not topic or _utf8_size(topic, "MathNet topic", maximum=4096) > 4096:
            raise ValueError("MathNet topic is empty or exceeds its absolute byte cap")
        if _utf8_size(topic, "MathNet topic", maximum=1024) > 1024:
            budget_exceeded = True
    budget_exceeded |= len(topics) > MAX_TOPICS_V3
    if len(row["images"]) > MAX_SOURCE_IMAGES_V3:
        raise ValueError("MathNet row exceeds the absolute image-count cap")

    return _MathNetRowFieldsV3(
        source_id=source_id,
        country=country,
        competition=competition,
        problem_markdown=problem_markdown,
        solutions_markdown=solutions,
        topics_flat=topics,
        language=language,
        images_count=len(row["images"]),
        problem_type=problem_type,
        final_answer=final_answer,
        text_budget_exceeded=bool(budget_exceeded),
    )


def _opaque_source_digest_v3(
    *, source_binding: MathNetSourceBindingV3, source_ordinal: int, source_id: str
) -> str:
    return _digest(
        {
            "source_binding_digest": source_binding.content_digest,
            "source_id_digest": _text_leaf_digest(source_id),
            "source_ordinal": source_ordinal,
        },
        domain="org.math-research-brain/mathnet-opaque-source/v3",
    )


def _private_row_payload_v3(
    fields: _MathNetRowFieldsV3,
    *,
    source_binding_digest: str,
    source_ordinal: int,
    final_answer_text_digest: str,
    solution_text_digests: tuple[str, ...],
) -> dict[str, object]:
    return {
        "competition": fields.competition,
        "country": fields.country,
        "final_answer_text_digest": final_answer_text_digest,
        "images_count": fields.images_count,
        "language": fields.language,
        "problem_markdown": fields.problem_markdown,
        "problem_type": fields.problem_type,
        "solution_text_digests": list(solution_text_digests),
        "source_binding_digest": source_binding_digest,
        "source_id": fields.source_id,
        "source_ordinal": source_ordinal,
        "topics_flat": list(fields.topics_flat),
    }


def _source_row_commitment_v3(
    fields: _MathNetRowFieldsV3,
    *,
    source_binding: MathNetSourceBindingV3,
    source_ordinal: int,
) -> str:
    """Commit every normalized source field without retaining large source text."""

    return _digest(
        {
            "competition": fields.competition,
            "country": fields.country,
            "final_answer_text_digest": (
                None
                if fields.final_answer is None
                else _text_leaf_digest(fields.final_answer)
            ),
            "images_count": fields.images_count,
            "language": fields.language,
            "problem_text_digest": _text_leaf_digest(fields.problem_markdown),
            "problem_type": fields.problem_type,
            "solution_text_digests": [
                _text_leaf_digest(item) for item in fields.solutions_markdown
            ],
            "source_artifact_manifest_digest": (
                source_binding.source_artifact_manifest_digest
            ),
            "source_binding_digest": source_binding.content_digest,
            "source_id": fields.source_id,
            "source_ordinal": source_ordinal,
            "topics_flat": list(fields.topics_flat),
        },
        domain="org.math-research-brain/mathnet-source-row/v3",
    )


def _initial_source_rows_state_v3(source_binding: MathNetSourceBindingV3) -> bytes:
    return hashlib.sha256(
        _ORDERED_SOURCE_ROWS_DOMAIN + source_binding.content_digest.encode("ascii")
    ).digest()


def _advance_source_rows_state_v3(
    state: bytes, *, source_ordinal: int, row_commitment: str
) -> bytes:
    _validate_digest(row_commitment, "MathNet source-row commitment")
    return hashlib.sha256(
        _ORDERED_SOURCE_ROWS_DOMAIN
        + state
        + source_ordinal.to_bytes(8, "big")
        + row_commitment.encode("ascii")
    ).digest()


def canonical_mathnet_row_digest_v3(
    raw: object,
    *,
    source_binding: MathNetSourceBindingV3,
    source_ordinal: int,
) -> str:
    """Commit an exact text-only row without retaining official solution text."""

    if type(source_binding) is not MathNetSourceBindingV3:
        raise ValueError("MathNet row digest requires an exact source binding")
    source_binding.validate_current()
    _integer(
        source_ordinal,
        "MathNet row-digest source ordinal",
        minimum=0,
        maximum=source_binding.row_count - 1,
    )
    fields = _read_mathnet_row_v3(raw)
    if fields.images_count != 0:
        raise ValueError("MathNet row digest accepts text-only rows")
    final_digest = _text_leaf_digest(fields.final_answer or "")
    solution_digests = tuple(_text_leaf_digest(item) for item in fields.solutions_markdown)
    return _digest(
        _private_row_payload_v3(
            fields,
            source_binding_digest=source_binding.content_digest,
            source_ordinal=source_ordinal,
            final_answer_text_digest=final_digest,
            solution_text_digests=solution_digests,
        ),
        domain=_ROW_DOMAIN,
    )


def _terminal_boxed_answer_v3(solution: str) -> tuple[bool, ExactRationalV3 | None]:
    positions = tuple(
        (solution.rfind(marker), marker) for marker in (r"\boxed{", r"\fbox{")
    )
    start, marker = max(positions, key=lambda item: item[0])
    if start < 0:
        return False, None
    depth = 1
    close_index: int | None = None
    for index in range(start + len(marker), len(solution)):
        character = solution[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                close_index = index
                break
    if close_index is None:
        return False, None
    tail = solution[close_index + 1 :].strip(" \t\r\n")
    if _TERMINAL_TAIL_RE.fullmatch(tail) is None:
        return False, None
    token = solution[start + len(marker) : close_index]
    try:
        return True, parse_exact_rational_v3(token)
    except ValueError:
        return True, None


def _ineligible_v3(
    code: MathNetEligibilityCodeV3,
    *,
    source_binding: MathNetSourceBindingV3,
    source_ordinal: int,
    source_id: str,
) -> MathNetEligibilityDecisionV3:
    return MathNetEligibilityDecisionV3(
        code=code,
        source_ordinal=source_ordinal,
        opaque_source_digest=_opaque_source_digest_v3(
            source_binding=source_binding,
            source_ordinal=source_ordinal,
            source_id=source_id,
        ),
    )


@dataclass(frozen=True, slots=True)
class _MathNetScalarScreenV3:
    """Private shared screen result; null language is never rewritten or promoted."""

    rejection_code: MathNetEligibilityCodeV3 | None
    gold: MathNetPrivateGoldV3 | None = field(default=None, repr=False)
    final_answer_text_digest: str | None = field(default=None, repr=False)
    solution_text_digests: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if type(self) is not _MathNetScalarScreenV3:
            raise ValueError("MathNet scalar-screen subclasses are forbidden")
        if self.rejection_code is None:
            if type(self.gold) is not MathNetPrivateGoldV3:
                raise ValueError("eligible MathNet scalar screen requires private gold")
            self.gold.validate_current()
            _validate_digest(
                self.final_answer_text_digest,
                "MathNet screened final-answer digest",
            )
            if type(self.solution_text_digests) is not tuple or not (
                1 <= len(self.solution_text_digests) <= MAX_SOLUTIONS_V3
            ):
                raise ValueError("eligible MathNet screen requires solution digests")
            for digest in self.solution_text_digests:
                _validate_digest(digest, "MathNet screened solution digest")
            if (
                self.final_answer_text_digest != self.gold.final_answer_text_digest
                or self.solution_text_digests != self.gold.solution_text_digests
            ):
                raise ValueError("MathNet scalar screen and private-gold digests differ")
        elif (
            type(self.rejection_code) is not MathNetEligibilityCodeV3
            or self.gold is not None
            or self.final_answer_text_digest is not None
            or self.solution_text_digests
        ):
            raise ValueError("ineligible MathNet scalar screen cannot retain private gold")


def _screen_mathnet_row_fields_for_pilot_v3(
    fields: _MathNetRowFieldsV3,
    *,
    permit_null_language_audit: bool,
) -> _MathNetScalarScreenV3:
    """Apply every frozen scalar-answer screen without rewriting raw language metadata."""

    if type(fields) is not _MathNetRowFieldsV3:
        raise ValueError("MathNet scalar screen requires exact row fields")
    if type(permit_null_language_audit) is not bool:
        raise ValueError("MathNet null-language audit policy must be exact boolean")

    def reject(code: MathNetEligibilityCodeV3) -> _MathNetScalarScreenV3:
        return _MathNetScalarScreenV3(rejection_code=code)

    if fields.country is None or fields.competition is None:
        return reject(MathNetEligibilityCodeV3.MISSING_SOURCE_METADATA)
    if fields.language is None and not permit_null_language_audit:
        return reject(MathNetEligibilityCodeV3.LANGUAGE_AUDIT_REQUIRED)
    if fields.text_budget_exceeded:
        return reject(MathNetEligibilityCodeV3.TEXT_BUDGET_EXCEEDED)
    if fields.language is not None and fields.language != "English":
        return reject(MathNetEligibilityCodeV3.NON_ENGLISH)
    if fields.images_count != 0:
        return reject(MathNetEligibilityCodeV3.NON_TEXT_ONLY)
    if _IMAGE_OR_FIGURE_RE.search(fields.problem_markdown):
        return reject(MathNetEligibilityCodeV3.NON_TEXT_ONLY)
    if _ROLE_LINE_RE.search(fields.problem_markdown) or _INJECTION_RE.search(
        fields.problem_markdown
    ):
        return reject(MathNetEligibilityCodeV3.UNSAFE_PROMPT_MARKUP)
    if fields.problem_type != "final answer only":
        return reject(MathNetEligibilityCodeV3.UNSUPPORTED_PROBLEM_TYPE)
    if not fields.solutions_markdown:
        return reject(MathNetEligibilityCodeV3.MISSING_SOLUTION_EVIDENCE)
    if fields.final_answer is None or not fields.final_answer.strip():
        return reject(MathNetEligibilityCodeV3.MISSING_ANSWER)
    try:
        answer = parse_exact_rational_v3(fields.final_answer)
    except ValueError:
        return reject(MathNetEligibilityCodeV3.UNSUPPORTED_ANSWER)

    confirming: list[int] = []
    recognized_unparseable = False
    recognized_answers: list[ExactRationalV3] = []
    for index, solution in enumerate(fields.solutions_markdown):
        recognized, parsed = _terminal_boxed_answer_v3(solution)
        if not recognized:
            continue
        if parsed is None:
            recognized_unparseable = True
            continue
        confirming.append(index)
        recognized_answers.append(parsed)
    if any(parsed != answer for parsed in recognized_answers):
        return reject(MathNetEligibilityCodeV3.SOLUTION_DISAGREEMENT)
    confirmation_rule = (
        "terminal-boxed-exact-v1"
        if confirming and not recognized_unparseable
        else "annotation-only-pending-blind-audit-v1"
    )
    confirming_indices = tuple(confirming) if confirmation_rule == "terminal-boxed-exact-v1" else ()

    final_digest = _text_leaf_digest(fields.final_answer)
    solution_digests = tuple(_text_leaf_digest(item) for item in fields.solutions_markdown)
    gold = MathNetPrivateGoldV3(
        answer=answer,
        final_answer_text_digest=final_digest,
        solution_text_digests=solution_digests,
        confirming_solution_indices=confirming_indices,
        confirmation_rule=confirmation_rule,
    )
    return _MathNetScalarScreenV3(
        rejection_code=None,
        gold=gold,
        final_answer_text_digest=final_digest,
        solution_text_digests=solution_digests,
    )


def prefilter_mathnet_row_v3(
    raw: object,
    *,
    source_binding: MathNetSourceBindingV3,
    source_ordinal: int,
) -> MathNetEligibilityDecisionV3:
    """Apply the frozen, outcome-blind scalar-answer eligibility filter."""

    if type(source_binding) is not MathNetSourceBindingV3:
        raise ValueError("MathNet prefilter requires an exact source binding")
    source_binding.validate_current()
    _integer(
        source_ordinal,
        "MathNet prefilter source ordinal",
        minimum=0,
        maximum=source_binding.row_count - 1,
    )
    fields = _read_mathnet_row_v3(raw)

    def reject(code: MathNetEligibilityCodeV3) -> MathNetEligibilityDecisionV3:
        return _ineligible_v3(
            code,
            source_binding=source_binding,
            source_ordinal=source_ordinal,
            source_id=fields.source_id,
        )

    screen = _screen_mathnet_row_fields_for_pilot_v3(
        fields,
        permit_null_language_audit=False,
    )
    if screen.rejection_code is not None:
        return reject(screen.rejection_code)
    if (
        fields.country is None
        or fields.competition is None
        or fields.language != "English"
        or type(screen.gold) is not MathNetPrivateGoldV3
        or screen.final_answer_text_digest is None
    ):
        raise ValueError("eligible MathNet screen lost exact source or gold fields")
    gold = screen.gold
    final_digest = screen.final_answer_text_digest
    solution_digests = screen.solution_text_digests
    public_digest = _digest(
        _public_selection_payload(
            source_ordinal=source_ordinal,
            source_id=fields.source_id,
            country=fields.country,
            competition=fields.competition,
            language=fields.language,
            topics_flat=fields.topics_flat,
            problem_markdown=fields.problem_markdown,
        ),
        domain=_PUBLIC_SELECTION_DOMAIN,
    )
    cluster_digest = _digest(
        {"competition": fields.competition, "country": fields.country},
        domain=_SOURCE_CLUSTER_DOMAIN,
    )
    row_digest = _digest(
        _private_row_payload_v3(
            fields,
            source_binding_digest=source_binding.content_digest,
            source_ordinal=source_ordinal,
            final_answer_text_digest=final_digest,
            solution_text_digests=solution_digests,
        ),
        domain=_ROW_DOMAIN,
    )
    candidate = MathNetCandidateV3(
        source_binding_digest=source_binding.content_digest,
        source_ordinal=source_ordinal,
        source_id=fields.source_id,
        country=fields.country,
        competition=fields.competition,
        language=fields.language,
        topics_flat=fields.topics_flat,
        problem_markdown=fields.problem_markdown,
        row_digest=row_digest,
        public_selection_digest=public_digest,
        source_cluster_digest=cluster_digest,
        gold=gold,
    )
    return MathNetEligibilityDecisionV3(
        code=MathNetEligibilityCodeV3.ELIGIBLE,
        source_ordinal=source_ordinal,
        opaque_source_digest=_opaque_source_digest_v3(
            source_binding=source_binding,
            source_ordinal=source_ordinal,
            source_id=fields.source_id,
        ),
        candidate=candidate,
    )


def _ticket(seed: bytes, *, domain: bytes, parts: tuple[str, ...]) -> str:
    _validate_seed(seed, "MathNet ticket seed")
    payload = domain + b"\x00".join(part.encode("ascii") for part in parts)
    return "hmac-sha256:" + hmac.new(seed, payload, hashlib.sha256).hexdigest()


def _validate_ticket(value: object, label: str) -> str:
    if type(value) is not str or _HMAC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical HMAC-SHA-256 ticket")
    return value


@dataclass(frozen=True, slots=True)
class MathNetBankEntryV3:
    bank_index: int
    cluster_ticket: str = field(repr=False)
    row_ticket: str = field(repr=False)
    candidate: MathNetCandidateV3 = field(repr=False)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetBankEntryV3:
            raise ValueError("MathNet bank-entry subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._private_payload(), domain=_BANK_ENTRY_DOMAIN),
        )

    def _validate_fields(self) -> None:
        _integer(
            self.bank_index,
            "MathNet bank index",
            minimum=0,
            maximum=MATHNET_HF_ROW_COUNT_V3 - 1,
        )
        _validate_ticket(self.cluster_ticket, "MathNet cluster ticket")
        _validate_ticket(self.row_ticket, "MathNet row ticket")
        if type(self.candidate) is not MathNetCandidateV3:
            raise ValueError("MathNet bank entry requires an exact candidate")
        self.candidate.validate_current()

    def _private_payload(self) -> dict[str, object]:
        return {
            "bank_index": self.bank_index,
            "candidate": self.candidate._private_dict(),
            "cluster_ticket": self.cluster_ticket,
            "row_ticket": self.row_ticket,
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetBankEntryV3:
            raise ValueError("MathNet bank-entry subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet bank-entry digest")
        expected = _digest(self._private_payload(), domain=_BANK_ENTRY_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet bank-entry digest is stale")

    def _private_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._private_payload() | {"content_digest": self.content_digest}


def _eligibility_counts_dict_v3(
    counts: tuple[tuple[str, int], ...],
) -> dict[MathNetEligibilityCodeV3, int]:
    if type(counts) is not tuple or len(counts) != len(MathNetEligibilityCodeV3):
        raise ValueError("MathNet eligibility counts must cover every frozen code")
    result: dict[MathNetEligibilityCodeV3, int] = {}
    for expected_code, item in zip(MathNetEligibilityCodeV3, counts, strict=True):
        if (
            type(item) is not tuple
            or len(item) != 2
            or type(item[0]) is not str
            or item[0] != expected_code.value
            or type(item[1]) is not int
            or item[1] < 0
        ):
            raise ValueError("MathNet eligibility counts are not canonical")
        result[expected_code] = item[1]
    return result


@dataclass(frozen=True, slots=True)
class MathNetBankV3:
    """Private pinned bank; its digest needs an external chronology anchor."""

    source_binding: MathNetSourceBindingV3
    sample_size: int
    selection_seed_commitment: str
    ordered_source_rows_digest: str
    eligibility_counts: tuple[tuple[str, int], ...]
    entries: tuple[MathNetBankEntryV3, ...] = field(repr=False)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetBankV3:
            raise ValueError("MathNet bank subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._private_payload(), domain=_BANK_DOMAIN),
        )

    def _validate_fields(self) -> None:
        if type(self.source_binding) is not MathNetSourceBindingV3:
            raise ValueError("MathNet bank requires an exact source binding")
        self.source_binding.validate_current()
        _integer(
            self.sample_size,
            "MathNet sample size",
            minimum=1,
            maximum=self.source_binding.row_count,
        )
        _validate_digest(self.selection_seed_commitment, "MathNet seed commitment")
        _validate_digest(
            self.ordered_source_rows_digest,
            "MathNet ordered source-rows digest",
        )
        counts = _eligibility_counts_dict_v3(self.eligibility_counts)
        if sum(counts.values()) != self.source_binding.row_count:
            raise ValueError("MathNet eligibility counts do not cover the pinned source")
        if counts[MathNetEligibilityCodeV3.ELIGIBLE] < self.sample_size:
            raise ValueError("MathNet bank has fewer eligible rows than selected entries")
        if type(self.entries) is not tuple or len(self.entries) != self.sample_size:
            raise ValueError("MathNet bank entries do not match the sample size")
        if not all(type(entry) is MathNetBankEntryV3 for entry in self.entries):
            raise ValueError("MathNet bank contains an invalid entry")
        for entry in self.entries:
            entry.validate_current()
            if entry.candidate.source_binding_digest != self.source_binding.content_digest:
                raise ValueError("MathNet bank candidate belongs to another source binding")
        if tuple(entry.bank_index for entry in self.entries) != tuple(range(self.sample_size)):
            raise ValueError("MathNet bank indices must be contiguous")
        ordering = tuple(
            (entry.cluster_ticket, entry.candidate.source_cluster_digest)
            for entry in self.entries
        )
        if ordering != tuple(sorted(ordering)):
            raise ValueError("MathNet bank entries are not in canonical ticket order")
        unique_fields = (
            tuple(entry.candidate.source_cluster_digest for entry in self.entries),
            tuple(entry.candidate.source_id for entry in self.entries),
            tuple(entry.candidate.row_digest for entry in self.entries),
        )
        if any(len(set(values)) != len(values) for values in unique_fields):
            raise ValueError("MathNet bank repeats a source cluster or row")

    def _private_payload(self) -> dict[str, object]:
        return {
            "eligibility_counts": [list(item) for item in self.eligibility_counts],
            "entries": [entry._private_dict() for entry in self.entries],
            "sample_size": self.sample_size,
            "schema": MATHNET_BANK_SCHEMA_V3,
            "schema_version": MATHNET_BANK_SCHEMA_VERSION_V3,
            "selection_seed_commitment": self.selection_seed_commitment,
            "ordered_source_rows_digest": self.ordered_source_rows_digest,
            "source_binding": self.source_binding.to_dict(),
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetBankV3:
            raise ValueError("MathNet bank subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet bank digest")
        expected = _digest(self._private_payload(), domain=_BANK_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet bank digest is stale")

    def validate_expected_digest(self, expected_digest: object) -> None:
        expected = _validate_digest(expected_digest, "expected MathNet bank digest")
        self.validate_current()
        if not hmac.compare_digest(expected, self.content_digest):
            raise ValueError("MathNet bank differs from its external commitment")

    def public_commitment_payload(self) -> dict[str, object]:
        """Return a prompt- and gold-free object suitable for external anchoring."""

        self.validate_current()
        return {
            "bank_digest": self.content_digest,
            "eligibility_counts": [list(item) for item in self.eligibility_counts],
            "sample_size": self.sample_size,
            "schema": MATHNET_BANK_SCHEMA_V3,
            "schema_version": MATHNET_BANK_SCHEMA_VERSION_V3,
            "selection_seed_commitment": self.selection_seed_commitment,
            "ordered_source_rows_digest": self.ordered_source_rows_digest,
            "source_binding_digest": self.source_binding.content_digest,
        }

    def public_commitment_digest(self) -> str:
        return _digest(
            self.public_commitment_payload(),
            domain="org.math-research-brain/mathnet-public-bank-commitment/v3",
        )


def _source_iterator_v3(rows: Iterable[object]) -> Any:
    if type(rows) in (str, bytes, dict):
        raise ValueError("MathNet source rows must be a bounded iterable of exact objects")
    try:
        return iter(rows)
    except TypeError as exc:
        raise ValueError("MathNet source rows must be iterable") from exc


def _immutable_row_projection_v3(fields: _MathNetRowFieldsV3) -> dict[str, object]:
    """Copy one row immediately so a reused mutable iterator cannot alter it later."""

    return {
        "id": fields.source_id,
        "problem_markdown": fields.problem_markdown,
        "solutions_markdown": list(fields.solutions_markdown),
        "images": [None] * fields.images_count,
        "country": fields.country,
        "competition": fields.competition,
        "topics_flat": list(fields.topics_flat),
        "language": fields.language,
        "problem_type": fields.problem_type,
        "final_answer": fields.final_answer,
    }


def _build_mathnet_bank_v3(
    rows: Iterable[object],
    *,
    source_binding: MathNetSourceBindingV3,
    sample_size: int,
    selection_seed: bytes,
) -> MathNetBankV3:
    """Stream the exact official row order into a one-per-event private bank.

    The source artifact manifest and selection-seed commitment must be externally frozen
    before this gold-gated scan.  Ticket ranking is gold-free conditional on the frozen
    eligibility rule; pool membership itself depends on answer scoreability.
    """

    if type(source_binding) is not MathNetSourceBindingV3:
        raise ValueError("MathNet bank builder requires an exact source binding")
    source_binding.validate_current()
    _integer(
        sample_size,
        "MathNet requested sample size",
        minimum=1,
        maximum=source_binding.row_count,
    )
    seed = _validate_seed(selection_seed, "MathNet selection seed")
    iterator = _source_iterator_v3(rows)

    counts: Counter[MathNetEligibilityCodeV3] = Counter()
    cluster_winners: dict[str, tuple[str, MathNetCandidateV3]] = {}
    source_ids: set[str] = set()
    source_rows_state = _initial_source_rows_state_v3(source_binding)
    for source_ordinal in range(source_binding.row_count):
        try:
            raw = next(iterator)
        except StopIteration as exc:
            raise ValueError("MathNet source row count differs from the pinned release") from exc
        fields = _read_mathnet_row_v3(raw)
        if fields.source_id in source_ids:
            raise ValueError("MathNet source IDs must be unique")
        source_ids.add(fields.source_id)
        source_rows_state = _advance_source_rows_state_v3(
            source_rows_state,
            source_ordinal=source_ordinal,
            row_commitment=_source_row_commitment_v3(
                fields,
                source_binding=source_binding,
                source_ordinal=source_ordinal,
            ),
        )
        decision = prefilter_mathnet_row_v3(
            _immutable_row_projection_v3(fields),
            source_binding=source_binding,
            source_ordinal=source_ordinal,
        )
        counts[decision.code] += 1
        if decision.code is not MathNetEligibilityCodeV3.ELIGIBLE:
            continue
        candidate = decision.candidate
        if type(candidate) is not MathNetCandidateV3:  # defensive narrowing
            raise ValueError("eligible MathNet decision lost its candidate")
        row_ticket = _ticket(
            seed,
            domain=_ROW_TICKET_DOMAIN,
            parts=(candidate.source_cluster_digest, candidate.public_selection_digest),
        )
        current = cluster_winners.get(candidate.source_cluster_digest)
        if current is None or (row_ticket, candidate.public_selection_digest) < (
            current[0],
            current[1].public_selection_digest,
        ):
            cluster_winners[candidate.source_cluster_digest] = (row_ticket, candidate)

    try:
        next(iterator)
    except StopIteration:
        pass
    else:
        raise ValueError("MathNet source contains more rows than the pinned release")

    if len(cluster_winners) < sample_size:
        raise ValueError("MathNet eligible source clusters cannot fill the requested bank")
    ranked: list[tuple[str, str, str, MathNetCandidateV3]] = []
    for cluster_digest, (row_ticket, candidate) in cluster_winners.items():
        cluster_ticket = _ticket(
            seed,
            domain=_CLUSTER_TICKET_DOMAIN,
            parts=(cluster_digest,),
        )
        ranked.append((cluster_ticket, cluster_digest, row_ticket, candidate))
    ranked.sort(key=lambda item: (item[0], item[1]))
    selected = ranked[:sample_size]
    entries = tuple(
        MathNetBankEntryV3(
            bank_index=index,
            cluster_ticket=cluster_ticket,
            row_ticket=row_ticket,
            candidate=candidate,
        )
        for index, (cluster_ticket, _cluster_digest, row_ticket, candidate) in enumerate(selected)
    )
    canonical_counts = tuple((code.value, counts[code]) for code in MathNetEligibilityCodeV3)
    return MathNetBankV3(
        source_binding=source_binding,
        sample_size=sample_size,
        selection_seed_commitment=_seed_commitment(seed),
        ordered_source_rows_digest="sha256:" + source_rows_state.hex(),
        eligibility_counts=canonical_counts,
        entries=entries,
    )


def _verify_mathnet_bank_v3(
    bank: MathNetBankV3,
    rows: Iterable[object],
    *,
    source_binding: MathNetSourceBindingV3,
    selection_seed: bytes,
) -> None:
    """Rebuild and compare the complete private bank, not just stored tickets."""

    if type(bank) is not MathNetBankV3:
        raise ValueError("MathNet bank verifier requires an exact bank")
    bank.validate_current()
    if type(source_binding) is not MathNetSourceBindingV3:
        raise ValueError("MathNet bank verifier requires an exact source binding")
    source_binding.validate_current()
    if source_binding != bank.source_binding:
        raise ValueError("MathNet verification source differs from the bank source")
    rebuilt = _build_mathnet_bank_v3(
        rows,
        source_binding=source_binding,
        sample_size=bank.sample_size,
        selection_seed=selection_seed,
    )
    if not hmac.compare_digest(rebuilt.content_digest, bank.content_digest) or rebuilt != bank:
        raise ValueError("MathNet bank does not replay from the pinned source and seed")


def build_synthetic_mathnet_bank_v3(
    rows: Iterable[object],
    *,
    sample_size: int,
    selection_seed: bytes,
) -> MathNetBankV3:
    """Build a test-only bank that is structurally marked as synthetic."""

    return _build_mathnet_bank_v3(
        rows,
        source_binding=synthetic_mathnet_source_binding_v3(),
        sample_size=sample_size,
        selection_seed=selection_seed,
    )


def verify_synthetic_mathnet_bank_v3(
    bank: MathNetBankV3,
    rows: Iterable[object],
    *,
    selection_seed: bytes,
) -> None:
    """Replay only a synthetic bank; official wrappers are rejected."""

    if type(bank) is not MathNetBankV3:
        raise ValueError("synthetic MathNet verification requires an exact private bank")
    bank.validate_current()
    if bank.source_binding.authority is not MathNetSourceAuthorityV3.SYNTHETIC_TEST:
        raise ValueError("synthetic verification cannot accept an official MathNet bank")
    _verify_mathnet_bank_v3(
        bank,
        rows,
        source_binding=synthetic_mathnet_source_binding_v3(),
        selection_seed=selection_seed,
    )


@dataclass(frozen=True, slots=True)
class MathNetSolverViewV3:
    problem_markdown: str
    response_contract: str = MATHNET_RESPONSE_CONTRACT_V3
    schema: str = MATHNET_SOLVER_VIEW_SCHEMA_V3
    schema_version: int = MATHNET_PUBLIC_SCHEMA_VERSION_V3
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetSolverViewV3:
            raise ValueError("MathNet solver-view subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(
                self._payload(),
                domain="org.math-research-brain/mathnet-solver-view/v3",
            ),
        )

    def _validate_fields(self) -> None:
        if (
            _utf8_size(
                self.problem_markdown,
                "MathNet solver problem",
                maximum=MAX_PROMPT_BYTES_V3,
            )
            > MAX_PROMPT_BYTES_V3
            or not self.problem_markdown.strip()
        ):
            raise ValueError("MathNet solver problem is empty or over budget")
        if self.response_contract != MATHNET_RESPONSE_CONTRACT_V3:
            raise ValueError("MathNet response contract is unsupported")
        if self.schema != MATHNET_SOLVER_VIEW_SCHEMA_V3:
            raise ValueError("MathNet solver-view schema is unsupported")
        if type(self.schema_version) is not int or (
            self.schema_version != MATHNET_PUBLIC_SCHEMA_VERSION_V3
        ):
            raise ValueError("MathNet solver-view schema version is unsupported")

    def _payload(self) -> dict[str, object]:
        return {
            "problem_markdown": self.problem_markdown,
            "response_contract": self.response_contract,
            "schema": self.schema,
            "schema_version": self.schema_version,
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetSolverViewV3:
            raise ValueError("MathNet solver-view subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet solver-view digest")
        expected = _digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-solver-view/v3",
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet solver-view digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}

    @classmethod
    def from_dict(cls, raw: object) -> MathNetSolverViewV3:
        value = _strict_keys(
            raw,
            (
                "content_digest",
                "problem_markdown",
                "response_contract",
                "schema",
                "schema_version",
            ),
            "MathNet solver view",
        )
        view = cls(
            problem_markdown=value["problem_markdown"],
            response_contract=value["response_contract"],
            schema=value["schema"],
            schema_version=value["schema_version"],
        )
        supplied = _validate_digest(value["content_digest"], "serialized solver-view digest")
        if not hmac.compare_digest(supplied, view.content_digest):
            raise ValueError("serialized MathNet solver-view digest is incorrect")
        return view


@dataclass(frozen=True, slots=True)
class MathNetSolverAssignmentV3:
    solver_id: str
    view: MathNetSolverViewV3
    schema: str = MATHNET_SOLVER_ASSIGNMENT_SCHEMA_V3
    schema_version: int = MATHNET_PUBLIC_SCHEMA_VERSION_V3
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetSolverAssignmentV3:
            raise ValueError("MathNet solver-assignment subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(
                self._payload(),
                domain="org.math-research-brain/mathnet-solver-assignment/v3",
            ),
        )

    def _validate_fields(self) -> None:
        if type(self.solver_id) is not str or _SOLVER_ID_RE.fullmatch(self.solver_id) is None:
            raise ValueError("MathNet solver ID is not canonical")
        if type(self.view) is not MathNetSolverViewV3:
            raise ValueError("MathNet assignment requires an exact solver view")
        self.view.validate_current()
        if self.schema != MATHNET_SOLVER_ASSIGNMENT_SCHEMA_V3:
            raise ValueError("MathNet solver-assignment schema is unsupported")
        if type(self.schema_version) is not int or (
            self.schema_version != MATHNET_PUBLIC_SCHEMA_VERSION_V3
        ):
            raise ValueError("MathNet solver-assignment schema version is unsupported")

    def _payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "solver_id": self.solver_id,
            "view": self.view.to_dict(),
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetSolverAssignmentV3:
            raise ValueError("MathNet solver-assignment subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(
            self.content_digest,
            "MathNet solver-assignment digest",
        )
        expected = _digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-solver-assignment/v3",
        )
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet solver-assignment digest is stale")

    def validate_expected_digest(self, expected_digest: object) -> None:
        expected = _validate_digest(expected_digest, "expected solver-assignment digest")
        self.validate_current()
        if not hmac.compare_digest(expected, self.content_digest):
            raise ValueError("MathNet solver assignment differs from its sealed manifest")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: object) -> MathNetSolverAssignmentV3:
        value = _strict_keys(
            raw,
            ("content_digest", "schema", "schema_version", "solver_id", "view"),
            "MathNet solver assignment",
        )
        assignment = cls(
            solver_id=value["solver_id"],
            view=MathNetSolverViewV3.from_dict(value["view"]),
            schema=value["schema"],
            schema_version=value["schema_version"],
        )
        supplied = _validate_digest(
            value["content_digest"],
            "serialized solver-assignment digest",
        )
        if not hmac.compare_digest(supplied, assignment.content_digest):
            raise ValueError("serialized MathNet solver-assignment digest is incorrect")
        return assignment

    @classmethod
    def from_json(cls, raw: object) -> MathNetSolverAssignmentV3:
        return cls.from_dict(_load_canonical_json(raw, "MathNet solver assignment"))


def render_mathnet_solver_assignment_v3(
    candidate: MathNetCandidateV3,
    *,
    run_nonce: bytes,
) -> MathNetSolverAssignmentV3:
    if type(candidate) is not MathNetCandidateV3:
        raise ValueError("MathNet rendering requires an exact candidate")
    candidate.validate_current()
    nonce = _validate_seed(run_nonce, "MathNet run nonce")
    view = MathNetSolverViewV3(problem_markdown=candidate.problem_markdown)
    solver_id = "mathnet-v3-" + hmac.new(
        nonce,
        _SOLVER_ID_DOMAIN + view.content_digest.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return MathNetSolverAssignmentV3(
        solver_id=solver_id,
        view=view,
    )


def parse_mathnet_solver_output_v3(raw: object) -> ExactRationalV3:
    if type(raw) is not str:
        raise ValueError("MathNet solver output must be exact text")
    if _utf8_size(raw, "MathNet solver output", maximum=MAX_OUTPUT_BYTES_V3) > (
        MAX_OUTPUT_BYTES_V3
    ):
        raise ValueError("MathNet solver output exceeds its byte budget")
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        raise ValueError("MathNet solver output has no nonempty line")
    final_line = lines[-1]
    match = _FINAL_LINE_RE.fullmatch(final_line)
    if match is None:
        raise ValueError("MathNet final line violates the response contract")
    return parse_exact_rational_v3(match.group("answer"))


class MathNetVerificationStatusV3(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    INVALID_FORMAT = "invalid_format"


_MATHNET_PARSER_SPEC_DIGEST_V3 = _digest(
    {
        "answer_grammar": "bounded-canonical-integer-or-rational-v3",
        "final_line": "FINAL_ANSWER: <integer-or-fraction>",
        "output_byte_cap": MAX_OUTPUT_BYTES_V3,
    },
    domain="org.math-research-brain/mathnet-parser-spec/v3",
)


@dataclass(frozen=True, slots=True, init=False)
class MathNetVerificationV3:
    """Derived private score trace; still requires an authenticated scoring receipt."""

    status: MathNetVerificationStatusV3
    parsed_answer: ExactRationalV3 | None
    raw_output_digest: str
    gold_content_digest: str
    parser_spec_digest: str

    @classmethod
    def _derive(
        cls,
        *,
        gold: MathNetPrivateGoldV3,
        raw: object,
        status: MathNetVerificationStatusV3,
        parsed_answer: ExactRationalV3 | None,
    ) -> MathNetVerificationV3:
        instance = object.__new__(cls)
        object.__setattr__(instance, "status", status)
        object.__setattr__(instance, "parsed_answer", parsed_answer)
        object.__setattr__(
            instance,
            "raw_output_digest",
            _digest(
                {
                    "is_exact_text": type(raw) is str,
                    "text": raw if type(raw) is str else None,
                },
                domain="org.math-research-brain/mathnet-raw-output/v3",
            ),
        )
        object.__setattr__(instance, "gold_content_digest", gold.content_digest)
        object.__setattr__(
            instance,
            "parser_spec_digest",
            _MATHNET_PARSER_SPEC_DIGEST_V3,
        )
        instance._validate_fields()
        return instance

    def _validate_fields(self) -> None:
        if type(self) is not MathNetVerificationV3:
            raise ValueError("MathNet verification subclasses are forbidden")
        if type(self.status) is not MathNetVerificationStatusV3:
            raise ValueError("MathNet verification status must be exact")
        if self.status is MathNetVerificationStatusV3.INVALID_FORMAT:
            if self.parsed_answer is not None:
                raise ValueError("invalid-format verification cannot carry an answer")
        elif type(self.parsed_answer) is not ExactRationalV3:
            raise ValueError("valid-format verification requires an exact answer")
        _validate_digest(self.raw_output_digest, "MathNet raw-output digest")
        _validate_digest(self.gold_content_digest, "MathNet verification gold digest")
        if self.parser_spec_digest != _MATHNET_PARSER_SPEC_DIGEST_V3:
            raise ValueError("MathNet verification parser spec is unsupported")

    def validate_against(self, gold: MathNetPrivateGoldV3, raw: object) -> None:
        self._validate_fields()
        expected = verify_mathnet_solver_output_v3(gold, raw)
        if self != expected:
            raise ValueError("MathNet verification differs from deterministic replay")


def verify_mathnet_solver_output_v3(
    gold: MathNetPrivateGoldV3,
    raw: object,
) -> MathNetVerificationV3:
    if type(gold) is not MathNetPrivateGoldV3:
        raise ValueError("MathNet output verification requires exact private gold")
    gold.validate_current()
    try:
        parsed = parse_mathnet_solver_output_v3(raw)
    except ValueError:
        return MathNetVerificationV3._derive(
            gold=gold,
            raw=raw,
            status=MathNetVerificationStatusV3.INVALID_FORMAT,
            parsed_answer=None,
        )
    return MathNetVerificationV3._derive(
        gold=gold,
        raw=raw,
        status=(
            MathNetVerificationStatusV3.CORRECT
            if parsed == gold.answer
            else MathNetVerificationStatusV3.INCORRECT
        ),
        parsed_answer=parsed,
    )
