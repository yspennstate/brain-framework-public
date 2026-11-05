"""Domain-free automatic retrieval for the random-external v4 pilot.

The production input is canonical JSON bytes whose only per-case fields are
``case_id``, ``problem_text``, and provenance ``source``.  Source is retained for
coverage accounting but is deliberately absent from the query and ranking surface.

Retrieval reuses the frozen v3 title-plus-retrieval-cue index, salient normalization,
stopwords, phrase features, score channels, and causal abstention thresholds.  It never
reads a domain, difficulty, family, answer, gold record, reviewer decision, or outcome.
Selected material is only a raw evaluator-side render fragment (title plus guidance).
It is not padded, tokenizer-attested, a :class:`FrozenPacket`, or solver-dispatchable.

``DomainFreeRetrievalPreparationV4`` is an integrity record, not an authority token.
Python nominal types can be forged with reflection.  A consumer obtains authority only
by calling :func:`cold_replay_domain_free_retrieval_v4` with the raw canonical case
manifest, an externally committed code/runtime execution manifest, the exact frozen
corpus and policy, and an externally expected preparation digest.  The execution
manifest pins the final commit/tree, the Git blob and SHA-256 identities of this module
and both retrieval dependencies, and the Python/Unicode runtime.  The cold path rebuilds
the complete result and never accepts a preparation object as evidence about itself.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from . import brain_corpus_v3 as _brain_corpus_v3_module
from . import retrieval as _retrieval_module
from .brain_corpus_v3 import (
    BRAIN_CARD_COUNT_V3,
    BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3,
    BRAIN_PACKET_FORMAT_SHA256_V3,
    BRAIN_PACKET_SECTION_SHAPE_SHA256_V3,
    BRAIN_PACKET_STATUS_VECTOR_SHA256_V3,
    BRAIN_RETRIEVAL_PAYLOAD_SHA256_V3,
    BRAIN_RETRIEVAL_POLICY_SHA256_V3,
    BRAIN_SALIENT_STOPWORDS_SHA256_V3,
    BrainCorpusManifestV3,
    BrainHitReceiptV3,
    BrainMethodCardV3,
    BrainRetrievalPolicyV3,
    _brain_hit_receipt_v3,
    _derived_index_universe_digest_v3,
    _graph_nodes_v3,
    _render_method_card_v3,
    _salient_terms_v3,
)
from .models import canonical_json
from .retrieval import RetrievalGuard, index_signals, retrieve
from .three_arm_evaluation import ProblemSource

DOMAIN_FREE_CASE_MANIFEST_SCHEMA_V4 = (
    "org.math-research-brain/domain-free-retrieval-case-manifest/v4"
)
DOMAIN_FREE_RETRIEVAL_SCHEMA_V4 = "org.math-research-brain/domain-free-retrieval/v4"
DOMAIN_FREE_RETRIEVAL_POLICY_ID_V4 = "domain-free-salient-title-cue-top-one-v4"

_DIGEST_PREFIX = "sha256:"
_DIGEST_LENGTH = len(_DIGEST_PREFIX) + 64
_MAX_CASES = 24
_MAX_MANIFEST_BYTES = 4_000_000
_MAX_PROBLEM_BYTES = 128_000
_MAX_RENDER_BYTES = 4096
_SAFE_CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PATH_LEAK = re.compile(
    r"(?:[A-Za-z]:[\\/]|/(?:home|Users|srv|var|run)/|\\\\[^\\\s]+\\)",
    re.IGNORECASE,
)

_POLICY_DOMAIN = "org.math-research-brain/domain-free-retrieval-policy/v4"
_NORMALIZATION_DOMAIN = "org.math-research-brain/domain-free-normalization/v4"
_QUERY_DOMAIN = "org.math-research-brain/domain-free-retrieval-query/v4"
_SALIENT_DOMAIN = "org.math-research-brain/domain-free-salient-query/v4"
_RECEIPT_DOMAIN = "org.math-research-brain/domain-free-retrieval-receipt/v4"
_DRAFT_DOMAIN = "org.math-research-brain/domain-free-render-draft/v4"
_PREPARATION_DOMAIN = "org.math-research-brain/domain-free-preparation/v4"
_RECEIPT_UNIVERSE_DOMAIN = "org.math-research-brain/domain-free-receipt-universe/v4"
_DRAFT_UNIVERSE_DOMAIN = "org.math-research-brain/domain-free-draft-universe/v4"
_EXECUTION_MANIFEST_DOMAIN = "org.math-research-brain/domain-free-retrieval-execution-manifest/v4"
_INDEX_ONLY_TIE_DOMAIN = "org.math-research-brain/domain-free-index-only-tie/v4"

_EXECUTION_SOURCE_PATHS = (
    "src/math_brain/domain_free_retrieval_v4.py",
    "src/math_brain/brain_corpus_v3.py",
    "src/math_brain/retrieval.py",
)
_GIT_SHA1 = re.compile(r"[0-9a-f]{40}\Z")

_EXPECTED_PRIVATE_V3_SIGNATURES = {
    "_brain_hit_receipt_v3": (
        "(*, rank: 'int', hit: 'object', card: 'BrainMethodCardV3', "
        "query_terms: 'tuple[str, ...]', policy: 'BrainRetrievalPolicyV3') -> "
        "'BrainHitReceiptV3'"
    ),
    "_derived_index_universe_digest_v3": ("(nodes: 'tuple[GraphNode, ...]') -> 'str'"),
    "_graph_nodes_v3": (
        "(corpus: 'BrainCorpusManifestV3', policy: 'BrainRetrievalPolicyV3') -> "
        "'tuple[GraphNode, ...]'"
    ),
    "_render_method_card_v3": "(card: 'BrainMethodCardV3') -> 'str'",
    "_salient_terms_v3": ("(text: 'str', policy: 'BrainRetrievalPolicyV3') -> 'tuple[str, ...]'"),
}


def _sha256_bytes(raw: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(raw).hexdigest()


def _domain_digest(domain: str, value: object) -> str:
    return _sha256_bytes(domain.encode("ascii") + b"\0" + canonical_json(value).encode("utf-8"))


def _git_blob_sha1(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw, usedforsecurity=False).hexdigest()


def _validate_git_sha1(value: object, label: str) -> str:
    if type(value) is not str or _GIT_SHA1.fullmatch(value) is None:
        raise ValueError(f"{label} must be canonical lowercase Git SHA-1")
    return value


def _runtime_identity_v4() -> dict[str, str]:
    cache_tag = sys.implementation.cache_tag
    if type(cache_tag) is not str or not cache_tag:
        raise ValueError("Python cache tag is unavailable")
    return {
        "python_cache_tag": cache_tag,
        "python_implementation": sys.implementation.name,
        "python_version": ".".join(str(value) for value in sys.version_info[:3]),
        "unicode_version": unicodedata.unidata_version,
    }


def _execution_source_bytes_v4(relative_path: str) -> bytes:
    module_paths = {
        _EXECUTION_SOURCE_PATHS[0]: Path(__file__).resolve(),
        _EXECUTION_SOURCE_PATHS[1]: Path(_brain_corpus_v3_module.__file__).resolve(),
        _EXECUTION_SOURCE_PATHS[2]: Path(_retrieval_module.__file__).resolve(),
    }
    if relative_path not in module_paths:
        raise ValueError("execution artifact path is outside the exact retrieval surface")
    return module_paths[relative_path].read_bytes()


def _private_v3_adapter_signature_digest_v4() -> str:
    helpers = {
        "_brain_hit_receipt_v3": _brain_hit_receipt_v3,
        "_derived_index_universe_digest_v3": _derived_index_universe_digest_v3,
        "_graph_nodes_v3": _graph_nodes_v3,
        "_render_method_card_v3": _render_method_card_v3,
        "_salient_terms_v3": _salient_terms_v3,
    }
    observed = {name: str(inspect.signature(helper)) for name, helper in helpers.items()}
    if observed != _EXPECTED_PRIVATE_V3_SIGNATURES:
        raise ValueError("private v3 retrieval-helper signature drifted")
    return _domain_digest(
        "org.math-research-brain/domain-free-v3-adapter-signatures/v4",
        observed,
    )


def _validate_digest(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != _DIGEST_LENGTH
        or not value.startswith(_DIGEST_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{label} must be canonical sha256:<lowercase hex>")
    return value


def _validate_text(
    value: object,
    label: str,
    *,
    maximum_bytes: int,
    allow_newline: bool = True,
) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be exact text")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error
    if not value.strip() or len(raw) > maximum_bytes or "\0" in value or "\r" in value:
        raise ValueError(f"{label} is empty, malformed, or exceeds its byte budget")
    if not allow_newline and "\n" in value:
        raise ValueError(f"{label} must be a single line")
    return value


def _normalization_digest_v4() -> str:
    base = BrainRetrievalPolicyV3()
    base.validate_current()
    return _domain_digest(
        _NORMALIZATION_DOMAIN,
        {
            "base_retrieval_implementation_digest": (BRAIN_RETRIEVAL_PAYLOAD_SHA256_V3),
            "base_retrieval_policy_digest": base.content_digest,
            "maximum_salient_terms": base.maximum_salient_terms,
            "maximum_term_utf8_bytes": base.maximum_term_utf8_bytes,
            "minimum_term_characters": base.minimum_term_characters,
            "normalization_id": base.normalization_id,
            "stopword_digest": BRAIN_SALIENT_STOPWORDS_SHA256_V3,
        },
    )


DOMAIN_FREE_NORMALIZATION_SHA256_V4 = _normalization_digest_v4()
DOMAIN_FREE_V3_ADAPTER_SIGNATURE_SHA256_V4 = _private_v3_adapter_signature_digest_v4()


def _canonical_manifest(
    raw: object,
    expected_digest: object,
) -> tuple[dict[str, object], str]:
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_MANIFEST_BYTES:
        raise ValueError("case manifest must be bounded exact bytes")
    expected = _validate_digest(expected_digest, "expected case-manifest digest")
    observed = _sha256_bytes(raw)
    if not hmac.compare_digest(observed, expected):
        raise ValueError("case manifest bytes differ from the external commitment")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("case manifest is not valid UTF-8") from error
    try:
        parsed = json.loads(
            text,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {token}")
            ),
        )
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("case manifest is not strict JSON") from error
    if type(parsed) is not dict:
        raise ValueError("case manifest root must be an exact object")
    if canonical_json(parsed).encode("utf-8") != raw:
        raise ValueError("case manifest bytes are not canonical JSON")
    return parsed, observed


@dataclass(frozen=True, slots=True)
class DomainFreeRetrievalExecutionArtifactV4:
    """One exact source artifact in the retrieval execution boundary."""

    relative_path: str
    git_blob_sha1: str
    content_sha256: str

    def __post_init__(self) -> None:
        if type(self) is not DomainFreeRetrievalExecutionArtifactV4:
            raise ValueError("execution-artifact subclasses are forbidden")
        self._validate_fields()

    def _validate_fields(self) -> None:
        if type(self.relative_path) is not str or self.relative_path not in (
            _EXECUTION_SOURCE_PATHS
        ):
            raise ValueError("execution artifact path is not on the exact allowlist")
        _validate_git_sha1(self.git_blob_sha1, "execution artifact Git blob")
        _validate_digest(self.content_sha256, "execution artifact content")

    def validate_current(self) -> None:
        self._validate_fields()
        raw = _execution_source_bytes_v4(self.relative_path)
        if not hmac.compare_digest(self.git_blob_sha1, _git_blob_sha1(raw)) or not (
            hmac.compare_digest(self.content_sha256, _sha256_bytes(raw))
        ):
            raise ValueError(f"execution artifact changed: {self.relative_path}")

    def commitment_record(self) -> dict[str, str]:
        self._validate_fields()
        return {
            "content_sha256": self.content_sha256,
            "git_blob_sha1": self.git_blob_sha1,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True, slots=True)
class DomainFreeRetrievalExecutionManifestV4:
    """Externally committed code/runtime identity for one retrieval execution."""

    git_commit_sha1: str
    git_tree_sha1: str
    artifacts: tuple[DomainFreeRetrievalExecutionArtifactV4, ...]
    python_implementation: str
    python_version: str
    python_cache_tag: str
    unicode_version: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not DomainFreeRetrievalExecutionManifestV4:
            raise ValueError("execution-manifest subclasses are forbidden")
        self._validate_fields(check_current=False)
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_EXECUTION_MANIFEST_DOMAIN, self._payload()),
        )

    def _validate_fields(self, *, check_current: bool) -> None:
        _validate_git_sha1(self.git_commit_sha1, "execution Git commit")
        _validate_git_sha1(self.git_tree_sha1, "execution Git tree")
        if (
            type(self.artifacts) is not tuple
            or tuple(artifact.relative_path for artifact in self.artifacts)
            != _EXECUTION_SOURCE_PATHS
        ):
            raise ValueError("execution manifest must contain the exact ordered source surface")
        if not all(
            type(artifact) is DomainFreeRetrievalExecutionArtifactV4 for artifact in self.artifacts
        ):
            raise ValueError("execution manifest artifacts must be exact records")
        for artifact in self.artifacts:
            artifact._validate_fields()
        for value, label in (
            (self.python_implementation, "Python implementation"),
            (self.python_version, "Python version"),
            (self.python_cache_tag, "Python cache tag"),
            (self.unicode_version, "Unicode version"),
        ):
            _validate_text(value, label, maximum_bytes=128, allow_newline=False)
        if check_current:
            expected_runtime = _runtime_identity_v4()
            observed_runtime = {
                "python_cache_tag": self.python_cache_tag,
                "python_implementation": self.python_implementation,
                "python_version": self.python_version,
                "unicode_version": self.unicode_version,
            }
            if observed_runtime != expected_runtime:
                raise ValueError("execution Python or Unicode runtime changed")
            for artifact in self.artifacts:
                artifact.validate_current()

    def _payload(self) -> dict[str, object]:
        return {
            "artifacts": [artifact.commitment_record() for artifact in self.artifacts],
            "git_commit_sha1": self.git_commit_sha1,
            "git_tree_sha1": self.git_tree_sha1,
            "python_cache_tag": self.python_cache_tag,
            "python_implementation": self.python_implementation,
            "python_version": self.python_version,
            "unicode_version": self.unicode_version,
        }

    def validate_current(self) -> None:
        if type(self) is not DomainFreeRetrievalExecutionManifestV4:
            raise ValueError("execution-manifest subclasses are forbidden")
        self._validate_fields(check_current=True)
        supplied = _validate_digest(self.content_digest, "retrieval execution manifest")
        expected = _domain_digest(_EXECUTION_MANIFEST_DOMAIN, self._payload())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("retrieval execution-manifest digest is stale")

    def commitment_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def capture_current_domain_free_retrieval_execution_manifest_v4(
    *,
    git_commit_sha1: str,
    git_tree_sha1: str,
) -> DomainFreeRetrievalExecutionManifestV4:
    """Capture bytes; callers must source commit/tree from their frozen precommit."""

    runtime = _runtime_identity_v4()
    artifacts = tuple(
        DomainFreeRetrievalExecutionArtifactV4(
            relative_path=relative_path,
            git_blob_sha1=_git_blob_sha1(raw),
            content_sha256=_sha256_bytes(raw),
        )
        for relative_path in _EXECUTION_SOURCE_PATHS
        for raw in (_execution_source_bytes_v4(relative_path),)
    )
    return DomainFreeRetrievalExecutionManifestV4(
        git_commit_sha1=git_commit_sha1,
        git_tree_sha1=git_tree_sha1,
        artifacts=artifacts,
        **runtime,
    )


@dataclass(frozen=True, slots=True)
class DomainFreeRetrievalPolicyV4:
    """Exact v4 wrapper around the frozen v3 causal lexical policy.

    ``execution_manifest_digest`` is supplied by the public precommitment and binds
    the complete source/runtime manifest.  Every other policy choice is fixed.
    """

    execution_manifest_digest: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not DomainFreeRetrievalPolicyV4:
            raise ValueError("domain-free retrieval policy subclasses are forbidden")
        _validate_digest(self.execution_manifest_digest, "v4 retrieval execution manifest")
        _private_v3_adapter_signature_digest_v4()
        self._validate_base_policy()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_POLICY_DOMAIN, self._payload()),
        )

    @staticmethod
    def _validate_base_policy() -> BrainRetrievalPolicyV3:
        base = BrainRetrievalPolicyV3()
        base.validate_current()
        if base.content_digest != BRAIN_RETRIEVAL_POLICY_SHA256_V3:
            raise ValueError("frozen v3 retrieval policy commitment changed")
        return base

    def _payload(self) -> dict[str, object]:
        base = self._validate_base_policy()
        return {
            "automatic_preoutcome_selection": True,
            "base_retrieval_implementation_digest": (BRAIN_RETRIEVAL_PAYLOAD_SHA256_V3),
            "base_retrieval_policy_digest": BRAIN_RETRIEVAL_POLICY_SHA256_V3,
            "candidate_scan": "full_frozen_index_before_index_only_top_two",
            "candidate_scan_limit": BRAIN_CARD_COUNT_V3,
            "considered_limit": base.considered_limit,
            "derived_index_universe_digest": (BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3),
            "domain_or_difficulty_fields_used": False,
            "family_or_gold_fields_used": False,
            "execution_manifest_digest": self.execution_manifest_digest,
            "index_fields": list(base.index_fields),
            "minimum_explicit_overlap_terms": base.minimum_explicit_overlap_terms,
            "normalization_digest": DOMAIN_FREE_NORMALIZATION_SHA256_V4,
            "normalization_id": base.normalization_id,
            "policy_id": DOMAIN_FREE_RETRIEVAL_POLICY_ID_V4,
            "relevance_review_gates_selection": False,
            "selected_limit": base.selected_limit,
            "selection_boundary_margin_hex": base.selection_boundary_margin_hex,
            "solver_dispatch_authorized": False,
            "source_affects_query_or_ranking": False,
            "stopword_digest": BRAIN_SALIENT_STOPWORDS_SHA256_V3,
            "support_floor_hex": base.support_floor_hex,
            "tie_order": "normalized_title_and_retrieval_cues_digest",
            "v3_adapter_signature_digest": (DOMAIN_FREE_V3_ADAPTER_SIGNATURE_SHA256_V4),
        }

    def validate_current(self) -> None:
        if type(self) is not DomainFreeRetrievalPolicyV4:
            raise ValueError("domain-free retrieval policy subclasses are forbidden")
        _validate_digest(self.execution_manifest_digest, "v4 retrieval execution manifest")
        _private_v3_adapter_signature_digest_v4()
        supplied = _validate_digest(self.content_digest, "v4 retrieval policy")
        expected = _domain_digest(_POLICY_DOMAIN, self._payload())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("domain-free retrieval policy digest is stale")

    def commitment_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def _index_signals_record_v4(text: str) -> dict[str, list[str]]:
    signals = index_signals(text)
    return {
        "formula_fingerprints": list(signals.formula_fingerprints),
        "lexical_tokens": list(signals.lexical_tokens),
        "math_tokens": list(signals.math_tokens),
    }


def _index_only_tie_key_v4(card: BrainMethodCardV3) -> str:
    """Order equal-score cards without consulting IDs, family, or guidance."""

    if type(card) is not BrainMethodCardV3:
        raise ValueError("index-only tie key requires an exact method card")
    card.validate_current()
    return _domain_digest(
        _INDEX_ONLY_TIE_DOMAIN,
        {
            "retrieval_cues": [_index_signals_record_v4(cue) for cue in card.retrieval_cues],
            "title": _index_signals_record_v4(card.title),
        },
    )


@dataclass(frozen=True, slots=True)
class DomainFreeRetrievalCaseV4:
    case_id: str
    problem_text: str = field(repr=False)
    source: ProblemSource

    def __post_init__(self) -> None:
        if type(self) is not DomainFreeRetrievalCaseV4:
            raise ValueError("domain-free retrieval case subclasses are forbidden")
        if type(self.case_id) is not str or _SAFE_CASE_ID.fullmatch(self.case_id) is None:
            raise ValueError("domain-free retrieval case id is invalid")
        _validate_text(
            self.problem_text,
            "domain-free problem text",
            maximum_bytes=_MAX_PROBLEM_BYTES,
        )
        if type(self.source) is not ProblemSource:
            raise ValueError("domain-free case source must be exact provenance")

    @property
    def problem_digest(self) -> str:
        self.__post_init__()
        return _sha256_bytes(self.problem_text.encode("utf-8"))

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "case_id": self.case_id,
            "problem_byte_count": len(self.problem_text.encode("utf-8")),
            "problem_digest": self.problem_digest,
            "source": self.source.value,
        }


@dataclass(frozen=True, slots=True)
class DomainFreeRetrievalQueryV4:
    """The complete ranking input; provenance source is intentionally absent."""

    case_id: str
    problem_text: str = field(repr=False)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not DomainFreeRetrievalQueryV4:
            raise ValueError("domain-free query subclasses are forbidden")
        if type(self.case_id) is not str or _SAFE_CASE_ID.fullmatch(self.case_id) is None:
            raise ValueError("domain-free query case id is invalid")
        _validate_text(
            self.problem_text,
            "domain-free query problem",
            maximum_bytes=_MAX_PROBLEM_BYTES,
        )
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_QUERY_DOMAIN, self._payload()),
        )

    @property
    def problem_digest(self) -> str:
        return _sha256_bytes(self.problem_text.encode("utf-8"))

    def _payload(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "problem_byte_count": len(self.problem_text.encode("utf-8")),
            "problem_digest": self.problem_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not DomainFreeRetrievalQueryV4:
            raise ValueError("domain-free query subclasses are forbidden")
        supplied = _validate_digest(self.content_digest, "domain-free query")
        expected = _domain_digest(_QUERY_DOMAIN, self._payload())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("domain-free query digest is stale")

    def commitment_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


class DomainFreeRetrievalDispositionV4(StrEnum):
    SELECTED = "selected_raw_draft"
    ABSTAINED = "abstained_itt"


class DomainFreeRetrievalAbstentionV4(StrEnum):
    NO_SALIENT_TERMS = "no_salient_terms"
    SALIENT_TERM_BUDGET_EXCEEDED = "salient_term_budget_exceeded"
    NO_CANDIDATE_MATCH = "no_candidate_match"
    MISSING_EXPLICIT_PHRASE_OVERLAP = "missing_explicit_phrase_overlap"
    BELOW_SUPPORT_FLOOR = "below_support_floor"
    AMBIGUOUS_SELECTION_BOUNDARY = "ambiguous_selection_boundary"


@dataclass(frozen=True, slots=True)
class DomainFreeRetrievalReceiptV4:
    case_id: str
    source: ProblemSource
    problem_digest: str
    case_manifest_digest: str
    corpus_digest: str
    source_manifest_digest: str
    policy_digest: str
    normalization_digest: str
    derived_index_universe_digest: str
    query_digest: str
    salient_terms: tuple[str, ...]
    salient_query_digest: str
    considered_hits: tuple[BrainHitReceiptV3, ...]
    considered_index_keys: tuple[str, ...]
    disposition: DomainFreeRetrievalDispositionV4
    abstention_reason: DomainFreeRetrievalAbstentionV4 | None
    selection_boundary_margin_hex: str
    selected_record_ids: tuple[str, ...]
    selected_source_digests: tuple[str, ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not DomainFreeRetrievalReceiptV4:
            raise ValueError("domain-free receipt subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_RECEIPT_DOMAIN, self._payload()),
        )

    def _validate_fields(self) -> None:
        if type(self.case_id) is not str or _SAFE_CASE_ID.fullmatch(self.case_id) is None:
            raise ValueError("domain-free receipt case id is invalid")
        if type(self.source) is not ProblemSource:
            raise ValueError("domain-free receipt source must be exact provenance")
        for value, label in (
            (self.problem_digest, "receipt problem"),
            (self.case_manifest_digest, "receipt case manifest"),
            (self.corpus_digest, "receipt corpus"),
            (self.source_manifest_digest, "receipt source manifest"),
            (self.policy_digest, "receipt policy"),
            (self.normalization_digest, "receipt normalization"),
            (self.derived_index_universe_digest, "receipt index universe"),
            (self.query_digest, "receipt query"),
            (self.salient_query_digest, "receipt salient query"),
        ):
            _validate_digest(value, label)
        if self.normalization_digest != DOMAIN_FREE_NORMALIZATION_SHA256_V4:
            raise ValueError("receipt normalization differs from the frozen policy")
        if self.derived_index_universe_digest != (BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3):
            raise ValueError("receipt index universe differs from the frozen pin")
        if type(self.salient_terms) is not tuple or len(self.salient_terms) > 32_768:
            raise ValueError("receipt salient terms must be an exact bounded tuple")
        if tuple(sorted(set(self.salient_terms))) != self.salient_terms:
            raise ValueError("receipt salient terms must be unique and sorted")
        for term in self.salient_terms:
            _validate_text(
                term,
                "receipt salient term",
                maximum_bytes=64,
                allow_newline=False,
            )
        if self.salient_query_digest != _domain_digest(_SALIENT_DOMAIN, list(self.salient_terms)):
            raise ValueError("receipt salient-query digest is stale")
        if type(self.considered_hits) is not tuple or len(self.considered_hits) > 2:
            raise ValueError("receipt may retain at most two considered hits")
        if not all(type(hit) is BrainHitReceiptV3 for hit in self.considered_hits):
            raise ValueError("receipt considered hits must be exact frozen records")
        for hit in self.considered_hits:
            hit.validate_current()
        if type(self.considered_index_keys) is not tuple or len(self.considered_index_keys) != len(
            self.considered_hits
        ):
            raise ValueError("receipt requires one index-only key per considered hit")
        for key in self.considered_index_keys:
            _validate_digest(key, "receipt index-only tie key")
        if len(set(self.considered_index_keys)) != len(self.considered_index_keys):
            raise ValueError("receipt index-only tie keys must be unique")
        if tuple(hit.rank for hit in self.considered_hits) != tuple(
            range(1, len(self.considered_hits) + 1)
        ):
            raise ValueError("receipt hit ranks must be contiguous")
        support_order = tuple(
            (float.fromhex(hit.support_score_hex), index_key)
            for hit, index_key in zip(
                self.considered_hits,
                self.considered_index_keys,
                strict=True,
            )
        )
        if support_order != tuple(sorted(support_order, key=lambda item: (-item[0], item[1]))):
            raise ValueError("receipt hits are not in canonical support order")
        try:
            boundary = float.fromhex(self.selection_boundary_margin_hex)
        except (TypeError, ValueError) as error:
            raise ValueError("receipt boundary margin is malformed") from error
        if (
            not math.isfinite(boundary)
            or boundary < 0
            or boundary.hex() != (self.selection_boundary_margin_hex)
        ):
            raise ValueError("receipt boundary margin is not canonical")
        expected_boundary = (
            0.0
            if not support_order
            else max(
                0.0,
                support_order[0][0] - (support_order[1][0] if len(support_order) > 1 else 0.0),
            )
        )
        if boundary.hex() != expected_boundary.hex():
            raise ValueError("receipt boundary margin differs from its hits")
        if type(self.disposition) is not DomainFreeRetrievalDispositionV4:
            raise ValueError("receipt disposition must be exact")
        if self.abstention_reason is not None and type(self.abstention_reason) is not (
            DomainFreeRetrievalAbstentionV4
        ):
            raise ValueError("receipt abstention reason must be exact")
        if (
            type(self.selected_record_ids) is not tuple
            or type(self.selected_source_digests) is not tuple
        ):
            raise ValueError("receipt selected records must be exact tuples")
        for value in self.selected_source_digests:
            _validate_digest(value, "selected source")
        expected_reason: DomainFreeRetrievalAbstentionV4 | None
        base = BrainRetrievalPolicyV3()
        if not self.salient_terms:
            expected_reason = DomainFreeRetrievalAbstentionV4.NO_SALIENT_TERMS
        elif len(self.salient_terms) > base.maximum_salient_terms:
            expected_reason = DomainFreeRetrievalAbstentionV4.SALIENT_TERM_BUDGET_EXCEEDED
        elif not self.considered_hits:
            expected_reason = DomainFreeRetrievalAbstentionV4.NO_CANDIDATE_MATCH
        else:
            top = self.considered_hits[0]
            if top.explicit_phrase_overlap_count < base.minimum_explicit_overlap_terms:
                expected_reason = DomainFreeRetrievalAbstentionV4.MISSING_EXPLICIT_PHRASE_OVERLAP
            elif float.fromhex(top.support_score_hex) < float.fromhex(base.support_floor_hex):
                expected_reason = DomainFreeRetrievalAbstentionV4.BELOW_SUPPORT_FLOOR
            elif expected_boundary < float.fromhex(base.selection_boundary_margin_hex):
                expected_reason = DomainFreeRetrievalAbstentionV4.AMBIGUOUS_SELECTION_BOUNDARY
            else:
                expected_reason = None
        expected_disposition = (
            DomainFreeRetrievalDispositionV4.SELECTED
            if expected_reason is None
            else DomainFreeRetrievalDispositionV4.ABSTAINED
        )
        if self.disposition is not expected_disposition or self.abstention_reason is not (
            expected_reason
        ):
            raise ValueError("receipt disposition violates the frozen decision tree")
        if expected_disposition is DomainFreeRetrievalDispositionV4.SELECTED:
            top = self.considered_hits[0]
            if self.selected_record_ids != (top.record_id,) or (
                self.selected_source_digests != (top.semantic_digest,)
            ):
                raise ValueError("receipt selection differs from its top hit")
        elif self.selected_record_ids or self.selected_source_digests:
            raise ValueError("retrieval abstention cannot select a source")

    def _payload(self) -> dict[str, object]:
        return {
            "abstention_reason": (
                None if self.abstention_reason is None else self.abstention_reason.value
            ),
            "case_id": self.case_id,
            "case_manifest_digest": self.case_manifest_digest,
            "considered_hits": [hit.to_dict() for hit in self.considered_hits],
            "considered_index_keys": list(self.considered_index_keys),
            "corpus_digest": self.corpus_digest,
            "derived_index_universe_digest": self.derived_index_universe_digest,
            "disposition": self.disposition.value,
            "normalization_digest": self.normalization_digest,
            "policy_digest": self.policy_digest,
            "problem_digest": self.problem_digest,
            "query_digest": self.query_digest,
            "relevance_review_gates_selection": False,
            "salient_query_digest": self.salient_query_digest,
            "salient_terms": list(self.salient_terms),
            "selected_record_ids": list(self.selected_record_ids),
            "selected_source_digests": list(self.selected_source_digests),
            "selection_boundary_margin_hex": self.selection_boundary_margin_hex,
            "solver_dispatch_authorized": False,
            "source": self.source.value,
            "source_affects_query_or_ranking": False,
            "source_manifest_digest": self.source_manifest_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not DomainFreeRetrievalReceiptV4:
            raise ValueError("domain-free receipt subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "domain-free receipt")
        expected = _domain_digest(_RECEIPT_DOMAIN, self._payload())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("domain-free receipt digest is stale")

    def evaluator_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class DomainFreePacketDraftV4:
    """Raw materializer input; deliberately not a solver-ready packet."""

    case_id: str
    render_fragment: str = field(repr=False)
    case_manifest_digest: str
    corpus_digest: str
    source_manifest_digest: str
    policy_digest: str
    query_digest: str
    retrieval_receipt_digest: str
    format_template_digest: str
    section_shape_digest: str
    age_status_vector_digest: str
    source_digests: tuple[str, ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not DomainFreePacketDraftV4:
            raise ValueError("domain-free packet-draft subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_DRAFT_DOMAIN, self._payload()),
        )

    @property
    def render_fragment_digest(self) -> str:
        return _sha256_bytes(self.render_fragment.encode("utf-8"))

    @property
    def render_fragment_byte_count(self) -> int:
        return len(self.render_fragment.encode("utf-8"))

    def _validate_fields(self) -> None:
        if type(self.case_id) is not str or _SAFE_CASE_ID.fullmatch(self.case_id) is None:
            raise ValueError("packet-draft case id is invalid")
        _validate_text(
            self.render_fragment,
            "packet-draft render fragment",
            maximum_bytes=_MAX_RENDER_BYTES,
        )
        if self.render_fragment.count("\n") != 1:
            raise ValueError("packet draft must retain the exact title-guidance shape")
        if _PATH_LEAK.search(self.render_fragment) is not None:
            raise ValueError("packet draft contains a filesystem-path surface")
        for value, label in (
            (self.case_manifest_digest, "draft case manifest"),
            (self.corpus_digest, "draft corpus"),
            (self.source_manifest_digest, "draft source manifest"),
            (self.policy_digest, "draft policy"),
            (self.query_digest, "draft query"),
            (self.retrieval_receipt_digest, "draft retrieval receipt"),
            (self.format_template_digest, "draft format template"),
            (self.section_shape_digest, "draft section shape"),
            (self.age_status_vector_digest, "draft status vector"),
        ):
            _validate_digest(value, label)
        if self.format_template_digest != BRAIN_PACKET_FORMAT_SHA256_V3:
            raise ValueError("packet draft format differs from the frozen renderer")
        if self.section_shape_digest != BRAIN_PACKET_SECTION_SHAPE_SHA256_V3:
            raise ValueError("packet draft shape differs from the frozen renderer")
        if self.age_status_vector_digest != BRAIN_PACKET_STATUS_VECTOR_SHA256_V3:
            raise ValueError("packet draft status differs from the frozen renderer")
        if type(self.source_digests) is not tuple or len(self.source_digests) != 1:
            raise ValueError("packet draft requires exactly one source digest")
        _validate_digest(self.source_digests[0], "packet-draft source")

    def _payload(self) -> dict[str, object]:
        return {
            "age_status_vector_digest": self.age_status_vector_digest,
            "case_id": self.case_id,
            "case_manifest_digest": self.case_manifest_digest,
            "corpus_digest": self.corpus_digest,
            "format_template_digest": self.format_template_digest,
            "policy_digest": self.policy_digest,
            "query_digest": self.query_digest,
            "relevance_review_gates_selection": False,
            "render_fragment_byte_count": self.render_fragment_byte_count,
            "render_fragment_digest": self.render_fragment_digest,
            "retrieval_receipt_digest": self.retrieval_receipt_digest,
            "section_shape_digest": self.section_shape_digest,
            "solver_dispatch_authorized": False,
            "source_digests": list(self.source_digests),
            "source_manifest_digest": self.source_manifest_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not DomainFreePacketDraftV4:
            raise ValueError("domain-free packet-draft subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "domain-free packet draft")
        expected = _domain_digest(_DRAFT_DOMAIN, self._payload())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("domain-free packet-draft digest is stale")

    def materializer_record(self) -> dict[str, object]:
        """Return lineage only; the raw fragment stays on this evaluator-side object."""

        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


class RetrievalBatchCardinalityV4(StrEnum):
    ZERO_SELECTED_ITT = "zero_selected_itt"
    MULTIPLE_SELECTED_MATERIALIZER_INPUT = "multiple_selected_materializer_input"
    SINGLETON_WHOLE_LANE_INFEASIBLE = "singleton_whole_lane_infeasible"


@dataclass(frozen=True, slots=True)
class DomainFreeRetrievalPreparationV4:
    """Deterministic integrity record; never sufficient authority by nominal type."""

    execution_manifest: DomainFreeRetrievalExecutionManifestV4 = field(repr=False)
    corpus: BrainCorpusManifestV3 = field(repr=False)
    policy: DomainFreeRetrievalPolicyV4 = field(repr=False)
    cases: tuple[DomainFreeRetrievalCaseV4, ...] = field(repr=False)
    receipts: tuple[DomainFreeRetrievalReceiptV4, ...] = field(repr=False)
    drafts: tuple[DomainFreePacketDraftV4, ...] = field(repr=False)
    case_manifest_digest: str
    corpus_digest: str
    source_manifest_digest: str
    policy_digest: str
    execution_manifest_digest: str
    normalization_digest: str
    derived_index_universe_digest: str
    receipt_universe_digest: str
    draft_universe_digest: str
    cardinality: RetrievalBatchCardinalityV4
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not DomainFreeRetrievalPreparationV4:
            raise ValueError("domain-free preparation subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_PREPARATION_DOMAIN, self._payload()),
        )

    @property
    def selected_count(self) -> int:
        return len(self.drafts)

    @property
    def abstained_count(self) -> int:
        return len(self.cases) - self.selected_count

    @property
    def source_counts(self) -> dict[str, int]:
        counts = Counter(case.source.value for case in self.cases)
        return {source.value: counts[source.value] for source in ProblemSource}

    @property
    def source_selected_counts(self) -> dict[str, int]:
        source_by_case = {case.case_id: case.source.value for case in self.cases}
        counts = Counter(source_by_case[draft.case_id] for draft in self.drafts)
        return {source.value: counts[source.value] for source in ProblemSource}

    def _validate_fields(self) -> None:
        if type(self.execution_manifest) is not DomainFreeRetrievalExecutionManifestV4:
            raise ValueError("preparation requires the exact execution manifest")
        if type(self.corpus) is not BrainCorpusManifestV3:
            raise ValueError("preparation requires the exact frozen corpus")
        if type(self.policy) is not DomainFreeRetrievalPolicyV4:
            raise ValueError("preparation requires the exact v4 policy")
        self.execution_manifest.validate_current()
        self.corpus.validate_current()
        self.policy.validate_current()
        if type(self.cases) is not tuple or not 1 <= len(self.cases) <= _MAX_CASES:
            raise ValueError("preparation requires a bounded exact case tuple")
        if not all(type(case) is DomainFreeRetrievalCaseV4 for case in self.cases):
            raise ValueError("preparation cases must be exact records")
        for case in self.cases:
            case.__post_init__()
        case_ids = tuple(case.case_id for case in self.cases)
        if tuple(sorted(set(case_ids))) != case_ids:
            raise ValueError("preparation cases must be unique and sorted")
        if type(self.receipts) is not tuple or len(self.receipts) != len(self.cases):
            raise ValueError("preparation requires one exact receipt per case")
        if not all(type(receipt) is DomainFreeRetrievalReceiptV4 for receipt in self.receipts):
            raise ValueError("preparation receipts must be exact records")
        for receipt in self.receipts:
            receipt.validate_current()
        if tuple(receipt.case_id for receipt in self.receipts) != case_ids:
            raise ValueError("preparation receipt order differs from the case manifest")
        case_by_id = {case.case_id: case for case in self.cases}
        card_by_id = {card.record_id: card for card in self.corpus.cards}
        for receipt in self.receipts:
            case = case_by_id[receipt.case_id]
            expected_query = DomainFreeRetrievalQueryV4(case.case_id, case.problem_text)
            if (
                receipt.source is not case.source
                or receipt.problem_digest != case.problem_digest
                or receipt.query_digest != expected_query.content_digest
            ):
                raise ValueError("preparation receipt problem, source, or query differs from case")
            try:
                expected_index_keys = tuple(
                    _index_only_tie_key_v4(card_by_id[hit.record_id])
                    for hit in receipt.considered_hits
                )
            except KeyError as error:
                raise ValueError("preparation receipt names a card outside its corpus") from error
            if receipt.considered_index_keys != expected_index_keys or any(
                hit.semantic_digest != card_by_id[hit.record_id].semantic_digest
                for hit in receipt.considered_hits
            ):
                raise ValueError("preparation receipt hit identity differs from its corpus")
        if type(self.drafts) is not tuple or not all(
            type(draft) is DomainFreePacketDraftV4 for draft in self.drafts
        ):
            raise ValueError("preparation drafts must be an exact tuple")
        for draft in self.drafts:
            draft.validate_current()
        if tuple(draft.case_id for draft in self.drafts) != tuple(
            sorted(draft.case_id for draft in self.drafts)
        ):
            raise ValueError("preparation drafts must retain canonical case order")
        draft_by_case = {draft.case_id: draft for draft in self.drafts}
        if len(draft_by_case) != len(self.drafts):
            raise ValueError("preparation repeats a packet draft")
        receipt_by_case = {receipt.case_id: receipt for receipt in self.receipts}
        expected_selected = {
            receipt.case_id
            for receipt in self.receipts
            if receipt.disposition is DomainFreeRetrievalDispositionV4.SELECTED
        }
        if set(draft_by_case) != expected_selected:
            raise ValueError("preparation drafts differ from selected receipts")
        for case_id, draft in draft_by_case.items():
            receipt = receipt_by_case[case_id]
            if (
                draft.retrieval_receipt_digest != receipt.content_digest
                or draft.query_digest != receipt.query_digest
                or draft.source_digests != receipt.selected_source_digests
                or draft.case_manifest_digest != self.case_manifest_digest
                or draft.corpus_digest != self.corpus_digest
                or draft.source_manifest_digest != self.source_manifest_digest
                or draft.policy_digest != self.policy_digest
                or draft.case_manifest_digest != receipt.case_manifest_digest
                or draft.corpus_digest != receipt.corpus_digest
                or draft.source_manifest_digest != receipt.source_manifest_digest
                or draft.policy_digest != receipt.policy_digest
            ):
                raise ValueError("preparation draft lineage differs from its receipt")
            selected_card = card_by_id[receipt.selected_record_ids[0]]
            if draft.render_fragment != _render_method_card_v3(selected_card):
                raise ValueError("preparation draft is not the selected title plus guidance")
        for value, label in (
            (self.case_manifest_digest, "preparation case manifest"),
            (self.corpus_digest, "preparation corpus"),
            (self.source_manifest_digest, "preparation source manifest"),
            (self.policy_digest, "preparation policy"),
            (self.execution_manifest_digest, "preparation execution manifest"),
            (self.normalization_digest, "preparation normalization"),
            (self.derived_index_universe_digest, "preparation index universe"),
            (self.receipt_universe_digest, "preparation receipt universe"),
            (self.draft_universe_digest, "preparation draft universe"),
        ):
            _validate_digest(value, label)
        if (
            self.execution_manifest_digest != self.execution_manifest.content_digest
            or self.corpus_digest != self.corpus.content_digest
            or self.source_manifest_digest != self.corpus.source_manifest.content_digest
            or self.policy_digest != self.policy.content_digest
            or self.policy.execution_manifest_digest != self.execution_manifest_digest
        ):
            raise ValueError("preparation execution, corpus, or policy object is transplanted")
        common_receipt_values = {
            (
                receipt.case_manifest_digest,
                receipt.corpus_digest,
                receipt.source_manifest_digest,
                receipt.policy_digest,
                receipt.normalization_digest,
                receipt.derived_index_universe_digest,
            )
            for receipt in self.receipts
        }
        expected_common = {
            (
                self.case_manifest_digest,
                self.corpus_digest,
                self.source_manifest_digest,
                self.policy_digest,
                self.normalization_digest,
                self.derived_index_universe_digest,
            )
        }
        if common_receipt_values != expected_common:
            raise ValueError("preparation receipt lineage is not uniform")
        expected_receipts = _domain_digest(
            _RECEIPT_UNIVERSE_DOMAIN,
            [receipt.evaluator_record() for receipt in self.receipts],
        )
        if self.receipt_universe_digest != expected_receipts:
            raise ValueError("preparation receipt-universe digest is stale")
        expected_drafts = _domain_digest(
            _DRAFT_UNIVERSE_DOMAIN,
            [draft.materializer_record() for draft in self.drafts],
        )
        if self.draft_universe_digest != expected_drafts:
            raise ValueError("preparation draft-universe digest is stale")
        expected_cardinality = (
            RetrievalBatchCardinalityV4.ZERO_SELECTED_ITT
            if not self.drafts
            else (
                RetrievalBatchCardinalityV4.SINGLETON_WHOLE_LANE_INFEASIBLE
                if len(self.drafts) == 1
                else RetrievalBatchCardinalityV4.MULTIPLE_SELECTED_MATERIALIZER_INPUT
            )
        )
        if type(self.cardinality) is not RetrievalBatchCardinalityV4 or (
            self.cardinality is not expected_cardinality
        ):
            raise ValueError("preparation cardinality does not match selected drafts")

    def _payload(self) -> dict[str, object]:
        return {
            "abstained_count": self.abstained_count,
            "automatic_retrieval_itt": True,
            "cardinality": self.cardinality.value,
            "case_manifest_digest": self.case_manifest_digest,
            "cases": [case.commitment_record() for case in self.cases],
            "corpus_digest": self.corpus_digest,
            "derived_index_universe_digest": self.derived_index_universe_digest,
            "draft_universe_digest": self.draft_universe_digest,
            "execution_manifest_digest": self.execution_manifest_digest,
            "normalization_digest": self.normalization_digest,
            "policy_digest": self.policy_digest,
            "receipt_universe_digest": self.receipt_universe_digest,
            "relevance_review_gates_selection": False,
            "schema": DOMAIN_FREE_RETRIEVAL_SCHEMA_V4,
            "selected_count": self.selected_count,
            "solver_dispatch_authorized": False,
            "source_counts": self.source_counts,
            "source_manifest_digest": self.source_manifest_digest,
            "source_selected_counts": self.source_selected_counts,
        }

    def validate_current(self) -> None:
        if type(self) is not DomainFreeRetrievalPreparationV4:
            raise ValueError("domain-free preparation subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "domain-free preparation")
        expected = _domain_digest(_PREPARATION_DOMAIN, self._payload())
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("domain-free preparation digest is stale")

    def public_commitment(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"preparation_digest": self.content_digest}


def _parse_cases_v4(
    case_manifest_bytes: bytes,
    expected_case_manifest_digest: str,
) -> tuple[tuple[DomainFreeRetrievalCaseV4, ...], str]:
    parsed, digest = _canonical_manifest(
        case_manifest_bytes,
        expected_case_manifest_digest,
    )
    if set(parsed) != {"cases", "schema"} or parsed["schema"] != (
        DOMAIN_FREE_CASE_MANIFEST_SCHEMA_V4
    ):
        raise ValueError("case manifest fields or schema differ from v4")
    raw_cases = parsed["cases"]
    if type(raw_cases) is not list or not 1 <= len(raw_cases) <= _MAX_CASES:
        raise ValueError("case manifest requires one to twenty-four cases")
    cases: list[DomainFreeRetrievalCaseV4] = []
    for raw in raw_cases:
        if type(raw) is not dict or set(raw) != {"case_id", "problem_text", "source"}:
            raise ValueError("case manifest rows may contain only id, problem, and source")
        try:
            source = ProblemSource(raw["source"])
        except (TypeError, ValueError) as error:
            raise ValueError("case manifest source is invalid") from error
        cases.append(
            DomainFreeRetrievalCaseV4(
                case_id=raw["case_id"],
                problem_text=raw["problem_text"],
                source=source,
            )
        )
    result = tuple(cases)
    case_ids = tuple(case.case_id for case in result)
    if tuple(sorted(set(case_ids))) != case_ids:
        raise ValueError("case manifest rows must be unique and sorted by case id")
    return result, digest


def _receipt_v4(
    *,
    case: DomainFreeRetrievalCaseV4,
    case_manifest_digest: str,
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
    query: DomainFreeRetrievalQueryV4,
    salient_terms: tuple[str, ...],
    considered_hits: tuple[BrainHitReceiptV3, ...],
    disposition: DomainFreeRetrievalDispositionV4,
    abstention_reason: DomainFreeRetrievalAbstentionV4 | None,
    boundary_margin: float,
    selected_card: BrainMethodCardV3 | None,
) -> DomainFreeRetrievalReceiptV4:
    card_by_id = {card.record_id: card for card in corpus.cards}
    considered_index_keys = tuple(
        _index_only_tie_key_v4(card_by_id[hit.record_id]) for hit in considered_hits
    )
    return DomainFreeRetrievalReceiptV4(
        case_id=case.case_id,
        source=case.source,
        problem_digest=case.problem_digest,
        case_manifest_digest=case_manifest_digest,
        corpus_digest=corpus.content_digest,
        source_manifest_digest=corpus.source_manifest.content_digest,
        policy_digest=policy.content_digest,
        normalization_digest=DOMAIN_FREE_NORMALIZATION_SHA256_V4,
        derived_index_universe_digest=BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3,
        query_digest=query.content_digest,
        salient_terms=salient_terms,
        salient_query_digest=_domain_digest(_SALIENT_DOMAIN, list(salient_terms)),
        considered_hits=considered_hits,
        considered_index_keys=considered_index_keys,
        disposition=disposition,
        abstention_reason=abstention_reason,
        selection_boundary_margin_hex=boundary_margin.hex(),
        selected_record_ids=(() if selected_card is None else (selected_card.record_id,)),
        selected_source_digests=(() if selected_card is None else (selected_card.semantic_digest,)),
    )


def _retrieve_one_v4(
    *,
    case: DomainFreeRetrievalCaseV4,
    case_manifest_digest: str,
    corpus: BrainCorpusManifestV3,
    policy: DomainFreeRetrievalPolicyV4,
) -> tuple[DomainFreeRetrievalReceiptV4, DomainFreePacketDraftV4 | None]:
    base_policy = BrainRetrievalPolicyV3()
    query = DomainFreeRetrievalQueryV4(case.case_id, case.problem_text)
    nodes = _graph_nodes_v3(corpus, base_policy)
    derived_digest = _derived_index_universe_digest_v3(nodes)
    if not hmac.compare_digest(
        derived_digest,
        BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3,
    ):
        raise ValueError("derived title-cue index differs from the frozen commitment")
    salient_terms = _salient_terms_v3(query.problem_text, base_policy)
    if not salient_terms:
        return (
            _receipt_v4(
                case=case,
                case_manifest_digest=case_manifest_digest,
                corpus=corpus,
                policy=policy,
                query=query,
                salient_terms=salient_terms,
                considered_hits=(),
                disposition=DomainFreeRetrievalDispositionV4.ABSTAINED,
                abstention_reason=DomainFreeRetrievalAbstentionV4.NO_SALIENT_TERMS,
                boundary_margin=0.0,
                selected_card=None,
            ),
            None,
        )
    if len(salient_terms) > base_policy.maximum_salient_terms:
        return (
            _receipt_v4(
                case=case,
                case_manifest_digest=case_manifest_digest,
                corpus=corpus,
                policy=policy,
                query=query,
                salient_terms=salient_terms,
                considered_hits=(),
                disposition=DomainFreeRetrievalDispositionV4.ABSTAINED,
                abstention_reason=(DomainFreeRetrievalAbstentionV4.SALIENT_TERM_BUDGET_EXCEEDED),
                boundary_margin=0.0,
                selected_card=None,
            ),
            None,
        )
    result = retrieve(
        " ".join(salient_terms),
        nodes=nodes,
        edges=(),
        limit=BRAIN_CARD_COUNT_V3,
        seed_limit=BRAIN_CARD_COUNT_V3,
        graph_depth=base_policy.graph_depth,
        decay=0.0,
        min_support_score=0.0,
        min_margin=0.0,
        guard=RetrievalGuard(
            evaluation_split="study_v4_evaluation",
            allowed_index_splits=("reference",),
            active_item_id=query.case_id,
            active_source_digest=query.content_digest,
        ),
    )
    if result.exclusions:
        raise ValueError("domain-free retrieval guard excluded a frozen card")
    card_by_id = {card.record_id: card for card in corpus.cards}
    index_key_by_id = {
        hit.node.id: _index_only_tie_key_v4(card_by_id[hit.node.id]) for hit in result.hits
    }
    if len(set(index_key_by_id.values())) != len(index_key_by_id):
        raise ValueError("normalized title-cue identities collide; tie order is undefined")
    ranked_hits = tuple(
        sorted(
            result.hits,
            key=lambda hit: (-hit.support_score, index_key_by_id[hit.node.id]),
        )[: base_policy.considered_limit]
    )
    considered_hits = tuple(
        _brain_hit_receipt_v3(
            rank=rank,
            hit=hit,
            card=card_by_id[hit.node.id],
            query_terms=salient_terms,
            policy=base_policy,
        )
        for rank, hit in enumerate(ranked_hits, start=1)
    )
    if not considered_hits:
        return (
            _receipt_v4(
                case=case,
                case_manifest_digest=case_manifest_digest,
                corpus=corpus,
                policy=policy,
                query=query,
                salient_terms=salient_terms,
                considered_hits=(),
                disposition=DomainFreeRetrievalDispositionV4.ABSTAINED,
                abstention_reason=DomainFreeRetrievalAbstentionV4.NO_CANDIDATE_MATCH,
                boundary_margin=0.0,
                selected_card=None,
            ),
            None,
        )
    top = considered_hits[0]
    top_support = float.fromhex(top.support_score_hex)
    runner_up_support = (
        float.fromhex(considered_hits[1].support_score_hex) if len(considered_hits) > 1 else 0.0
    )
    boundary_margin = max(0.0, top_support - runner_up_support)
    reason: DomainFreeRetrievalAbstentionV4 | None = None
    if top.explicit_phrase_overlap_count < base_policy.minimum_explicit_overlap_terms:
        reason = DomainFreeRetrievalAbstentionV4.MISSING_EXPLICIT_PHRASE_OVERLAP
    elif top_support < float.fromhex(base_policy.support_floor_hex):
        reason = DomainFreeRetrievalAbstentionV4.BELOW_SUPPORT_FLOOR
    elif boundary_margin < float.fromhex(base_policy.selection_boundary_margin_hex):
        reason = DomainFreeRetrievalAbstentionV4.AMBIGUOUS_SELECTION_BOUNDARY
    if reason is not None:
        return (
            _receipt_v4(
                case=case,
                case_manifest_digest=case_manifest_digest,
                corpus=corpus,
                policy=policy,
                query=query,
                salient_terms=salient_terms,
                considered_hits=considered_hits,
                disposition=DomainFreeRetrievalDispositionV4.ABSTAINED,
                abstention_reason=reason,
                boundary_margin=boundary_margin,
                selected_card=None,
            ),
            None,
        )
    selected_card = card_by_id[top.record_id]
    receipt = _receipt_v4(
        case=case,
        case_manifest_digest=case_manifest_digest,
        corpus=corpus,
        policy=policy,
        query=query,
        salient_terms=salient_terms,
        considered_hits=considered_hits,
        disposition=DomainFreeRetrievalDispositionV4.SELECTED,
        abstention_reason=None,
        boundary_margin=boundary_margin,
        selected_card=selected_card,
    )
    return receipt, DomainFreePacketDraftV4(
        case_id=case.case_id,
        render_fragment=_render_method_card_v3(selected_card),
        case_manifest_digest=case_manifest_digest,
        corpus_digest=corpus.content_digest,
        source_manifest_digest=corpus.source_manifest.content_digest,
        policy_digest=policy.content_digest,
        query_digest=query.content_digest,
        retrieval_receipt_digest=receipt.content_digest,
        format_template_digest=BRAIN_PACKET_FORMAT_SHA256_V3,
        section_shape_digest=BRAIN_PACKET_SECTION_SHAPE_SHA256_V3,
        age_status_vector_digest=BRAIN_PACKET_STATUS_VECTOR_SHA256_V3,
        source_digests=(selected_card.semantic_digest,),
    )


def prepare_domain_free_retrieval_v4(
    case_manifest_bytes: bytes,
    *,
    expected_case_manifest_digest: str,
    execution_manifest: DomainFreeRetrievalExecutionManifestV4,
    expected_execution_manifest_digest: str,
    corpus: BrainCorpusManifestV3,
    expected_corpus_digest: str,
    policy: DomainFreeRetrievalPolicyV4,
    expected_policy_digest: str,
) -> DomainFreeRetrievalPreparationV4:
    """Build a pre-outcome integrity record from exact raw inputs."""

    if type(execution_manifest) is not DomainFreeRetrievalExecutionManifestV4:
        raise ValueError("domain-free retrieval requires the exact execution manifest")
    if type(corpus) is not BrainCorpusManifestV3:
        raise ValueError("domain-free retrieval requires the exact frozen corpus")
    if type(policy) is not DomainFreeRetrievalPolicyV4:
        raise ValueError("domain-free retrieval requires the exact v4 policy")
    execution_manifest.validate_current()
    corpus.validate_current()
    policy.validate_current()
    if not hmac.compare_digest(
        execution_manifest.content_digest,
        _validate_digest(
            expected_execution_manifest_digest,
            "expected retrieval execution manifest",
        ),
    ):
        raise ValueError("retrieval execution manifest differs from the external commitment")
    if policy.execution_manifest_digest != execution_manifest.content_digest:
        raise ValueError("retrieval policy is not bound to the execution manifest")
    if not hmac.compare_digest(
        corpus.content_digest,
        _validate_digest(expected_corpus_digest, "expected corpus"),
    ):
        raise ValueError("frozen corpus differs from the external commitment")
    if not hmac.compare_digest(
        policy.content_digest,
        _validate_digest(expected_policy_digest, "expected v4 policy"),
    ):
        raise ValueError("retrieval policy differs from the external commitment")
    cases, case_manifest_digest = _parse_cases_v4(
        case_manifest_bytes,
        expected_case_manifest_digest,
    )
    receipts: list[DomainFreeRetrievalReceiptV4] = []
    drafts: list[DomainFreePacketDraftV4] = []
    for case in cases:
        receipt, draft = _retrieve_one_v4(
            case=case,
            case_manifest_digest=case_manifest_digest,
            corpus=corpus,
            policy=policy,
        )
        receipts.append(receipt)
        if draft is not None:
            drafts.append(draft)
    receipt_tuple = tuple(receipts)
    draft_tuple = tuple(drafts)
    cardinality = (
        RetrievalBatchCardinalityV4.ZERO_SELECTED_ITT
        if not draft_tuple
        else (
            RetrievalBatchCardinalityV4.SINGLETON_WHOLE_LANE_INFEASIBLE
            if len(draft_tuple) == 1
            else RetrievalBatchCardinalityV4.MULTIPLE_SELECTED_MATERIALIZER_INPUT
        )
    )
    return DomainFreeRetrievalPreparationV4(
        execution_manifest=execution_manifest,
        corpus=corpus,
        policy=policy,
        cases=cases,
        receipts=receipt_tuple,
        drafts=draft_tuple,
        case_manifest_digest=case_manifest_digest,
        corpus_digest=corpus.content_digest,
        source_manifest_digest=corpus.source_manifest.content_digest,
        policy_digest=policy.content_digest,
        execution_manifest_digest=execution_manifest.content_digest,
        normalization_digest=DOMAIN_FREE_NORMALIZATION_SHA256_V4,
        derived_index_universe_digest=BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3,
        receipt_universe_digest=_domain_digest(
            _RECEIPT_UNIVERSE_DOMAIN,
            [receipt.evaluator_record() for receipt in receipt_tuple],
        ),
        draft_universe_digest=_domain_digest(
            _DRAFT_UNIVERSE_DOMAIN,
            [draft.materializer_record() for draft in draft_tuple],
        ),
        cardinality=cardinality,
    )


def cold_replay_domain_free_retrieval_v4(
    case_manifest_bytes: bytes,
    *,
    expected_case_manifest_digest: str,
    execution_manifest: DomainFreeRetrievalExecutionManifestV4,
    expected_execution_manifest_digest: str,
    corpus: BrainCorpusManifestV3,
    expected_corpus_digest: str,
    policy: DomainFreeRetrievalPolicyV4,
    expected_policy_digest: str,
    expected_preparation_digest: str,
) -> DomainFreeRetrievalPreparationV4:
    """Fresh-rebuild the complete preparation and compare an external digest.

    No nominal preparation object is accepted.  This raw-input replay is the only
    authority boundary supplied by this module.
    """

    rebuilt = prepare_domain_free_retrieval_v4(
        case_manifest_bytes,
        expected_case_manifest_digest=expected_case_manifest_digest,
        execution_manifest=execution_manifest,
        expected_execution_manifest_digest=expected_execution_manifest_digest,
        corpus=corpus,
        expected_corpus_digest=expected_corpus_digest,
        policy=policy,
        expected_policy_digest=expected_policy_digest,
    )
    expected = _validate_digest(
        expected_preparation_digest,
        "expected domain-free preparation",
    )
    if not hmac.compare_digest(rebuilt.content_digest, expected):
        raise ValueError("cold replay differs from the external preparation commitment")
    return rebuilt


__all__ = [
    "DOMAIN_FREE_CASE_MANIFEST_SCHEMA_V4",
    "DOMAIN_FREE_NORMALIZATION_SHA256_V4",
    "DOMAIN_FREE_RETRIEVAL_POLICY_ID_V4",
    "DOMAIN_FREE_RETRIEVAL_SCHEMA_V4",
    "DOMAIN_FREE_V3_ADAPTER_SIGNATURE_SHA256_V4",
    "DomainFreePacketDraftV4",
    "DomainFreeRetrievalAbstentionV4",
    "DomainFreeRetrievalCaseV4",
    "DomainFreeRetrievalDispositionV4",
    "DomainFreeRetrievalExecutionArtifactV4",
    "DomainFreeRetrievalExecutionManifestV4",
    "DomainFreeRetrievalPolicyV4",
    "DomainFreeRetrievalPreparationV4",
    "DomainFreeRetrievalQueryV4",
    "DomainFreeRetrievalReceiptV4",
    "RetrievalBatchCardinalityV4",
    "capture_current_domain_free_retrieval_execution_manifest_v4",
    "cold_replay_domain_free_retrieval_v4",
    "prepare_domain_free_retrieval_v4",
]
