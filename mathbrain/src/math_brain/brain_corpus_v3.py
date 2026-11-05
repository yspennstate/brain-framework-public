"""Frozen legacy review-card corpus and salient retrieval for study-v3 packets.

The engineering intervention is a strict projection of twenty generic review cards from
one committed Git blob.  It is deliberately *not* unrestricted access to the owner's full
brain.  Git objects are read by full object ID with replacement refs disabled; the dirty
working tree is never consulted.  The mixed legacy challenge config is projected into an
exact card-only type so problem banks, gold, and development metadata are unrepresentable.

Raw-prose retrieval produced a real stopword-only false hit during design.  This module
therefore indexes only card titles and preregistered cues, filters the solver-visible query
through a frozen stopword list, requires explicit title/cue overlap, and abstains unless the
top hit clears both a support floor and a positive boundary margin.  Lexical routing is not
semantic relevance authority: every nonempty result remains an unreviewed proposal that
cannot be rendered for a solver until a separate blinded audit and signed preparation root.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import subprocess
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .models import GraphNode, NodeKind, canonical_json
from .retrieval import RetrievalGuard, retrieve, tokenize
from .three_arm_evaluation import MathDomain

BRAIN_SOURCE_COMMIT_V3 = "128c81b6d1d134e912429cd1df37d15f0c686474"
BRAIN_SOURCE_TREE_V3 = "eebebb36c0971dc0b5b4e0d4eac6f8f24046ddbc"
BRAIN_SOURCE_PATH_V3 = "config/challenge_bank.json"
BRAIN_SOURCE_BLOB_V3 = "14f14a231cd3a1525aadfe3bc7dbb1d58650612b"
BRAIN_SOURCE_BLOB_BYTES_V3 = 11_242
BRAIN_SOURCE_BLOB_SHA256_V3 = (
    "sha256:17716e51e348aa703882d463fb486bc4a4bf1fbc75223a5cd93dcb67c36e4877"
)
BRAIN_SOURCE_BLOB_CANONICAL_SHA256_V3 = (
    "sha256:80ec1afdbfa47027290c18fd6d9c77d65b65fa5164b75745e551853a2c0fb81c"
)
BRAIN_SOURCE_COMMIT_BYTES_V3 = 274
BRAIN_SOURCE_COMMIT_PAYLOAD_SHA256_V3 = (
    "sha256:d4047862395524335b7fd9ead09030ceebd2bc8d9128b29804ee6cc70ba72931"
)
BRAIN_SOURCE_COMMIT_CANONICAL_SHA256_V3 = (
    "sha256:5a0f996a2ca482e351d1a26746c3463869846cf0691720400217ca1d1b3f3da5"
)
BRAIN_CARD_ARRAY_SHA256_V3 = (
    "sha256:f60ad5285e55ecab750d23ea0a69264f5bb508fcbed3e5fa9d9ff70dad0fadf8"
)
BRAIN_LEGACY_CARD_UNIVERSE_SHA256_V3 = (
    "sha256:a2522e6539380f3b6ae319918498fcad980ecd6bad54a7608cf2cbbea841ff23"
)
BRAIN_SEMANTIC_CARD_UNIVERSE_SHA256_V3 = (
    "sha256:b8c180a9a3c6e96fa1d02f7978160b07931c6ff9fa90f78838f4852ad33a16e6"
)
BRAIN_CARD_RECORD_UNIVERSE_SHA256_V3 = (
    "sha256:9d558cdee5f0b0129f88f3c7e7d39e57c791ca213926227bf79bc5e3b0465c2b"
)
BRAIN_CARD_COUNT_V3 = 20
BRAIN_RETRIEVAL_BLOB_V3 = "9635be38aa75fecf6a8c562bdcc70ef27dc7b7b1"
BRAIN_RETRIEVAL_PATH_V3 = "src/math_brain/retrieval.py"
BRAIN_RETRIEVAL_BLOB_BYTES_V3 = 26_301
BRAIN_RETRIEVAL_PAYLOAD_SHA256_V3 = (
    "sha256:4df41b69c5658ec28b7ef324aaa01f729503dbf5c5dfe2401518d9e3c82b58de"
)
BRAIN_RETRIEVAL_CANONICAL_SHA256_V3 = (
    "sha256:5a3f357a57d980bfccbfa7c6ced797a04d318bb58c864e88a4cf40e09fcf9ec1"
)
BRAIN_SOURCE_RIGHTS_STATE_V3 = "local_analysis_only"

_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OID_RE = re.compile(r"[0-9a-f]{40}\Z")
_SAFE_FAMILY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_CASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CARD_FIELDS_V3 = ("card_id", "family", "title", "guidance", "retrieval_cues")
_CREATED_AT_SENTINEL_V3 = "1970-01-01T00:00:00+00:00"
_MAX_GIT_OUTPUT_BYTES_V3 = 2 * 1024 * 1024

_OBJECT_COMMITMENT_DOMAIN = "org.math-research-brain/git-object/v3"
_SOURCE_MANIFEST_DOMAIN = "org.math-research-brain/brain-source-snapshot/v3"
_CARD_LOCATOR_DOMAIN = "org.math-research-brain/brain-card-locator/v3"
_LEGACY_CARD_DOMAIN = "org.math-research-brain/legacy-card-record/v3"
_SEMANTIC_CARD_DOMAIN = "org.math-research-brain/brain-method-card-semantic/v3"
_CARD_RECORD_DOMAIN = "org.math-research-brain/brain-method-card-record/v3"
_INDEX_NODE_DOMAIN = "org.math-research-brain/brain-index-node/v3"
_CORPUS_DOMAIN = "org.math-research-brain/brain-corpus/v3"


def _digest(value: object, *, domain: str) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json({"domain": domain, "payload": value}).encode("utf-8")
    ).hexdigest()


def _payload_sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_object_sha256(object_type: str, payload: bytes) -> str:
    header = object_type.encode("ascii") + b" " + str(len(payload)).encode("ascii") + b"\0"
    return _payload_sha256(header + payload)


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _bounded_text(value: object, label: str, *, maximum: int) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be exact text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if not value.strip() or len(encoded) > maximum:
        raise ValueError(f"{label} is empty or exceeds its byte budget")
    return value


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError("brain source JSON contains a duplicate or invalid object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"brain source JSON contains forbidden constant {value!r}")


def _load_strict_json_v3(raw: bytes) -> object:
    if type(raw) is not bytes or len(raw) > _MAX_GIT_OUTPUT_BYTES_V3:
        raise ValueError("brain source blob is not bounded exact bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("brain source blob is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("brain source blob is not strict JSON") from exc


def _run_git_v3(repository_root: Path, *arguments: str) -> bytes:
    if not isinstance(repository_root, Path) or not repository_root.is_absolute():
        raise ValueError("brain repository root must be an absolute Path")
    if any(type(argument) is not str or not argument for argument in arguments):
        raise ValueError("brain Git arguments must be nonempty exact text")
    command = ["git", "--no-replace-objects", *arguments]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        completed = subprocess.run(
            command,
            cwd=repository_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=20,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("failed to read the pinned brain Git object") from exc
    if completed.returncode != 0:
        raise ValueError("pinned brain Git object command failed")
    if len(completed.stdout) > _MAX_GIT_OUTPUT_BYTES_V3:
        raise ValueError("pinned brain Git object exceeds its byte budget")
    if len(completed.stderr) > 64 * 1024:
        raise ValueError("pinned brain Git command emitted excessive diagnostics")
    return completed.stdout


@dataclass(frozen=True, slots=True)
class GitObjectCommitmentV3:
    object_type: str
    oid: str
    byte_count: int
    payload_sha256: str
    canonical_object_sha256: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not GitObjectCommitmentV3:
            raise ValueError("Git-object commitment subclasses are forbidden")
        if type(self.object_type) is not str or self.object_type not in {"blob", "commit"}:
            raise ValueError("Git-object type is unsupported")
        if type(self.oid) is not str or _OID_RE.fullmatch(self.oid) is None:
            raise ValueError("Git object requires a full lowercase OID")
        if type(self.byte_count) is not int or self.byte_count <= 0:
            raise ValueError("Git-object byte count must be positive")
        _validate_digest(self.payload_sha256, "Git-object payload digest")
        _validate_digest(self.canonical_object_sha256, "canonical Git-object digest")
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._payload(), domain=_OBJECT_COMMITMENT_DOMAIN),
        )

    def _payload(self) -> dict[str, object]:
        return {
            "byte_count": self.byte_count,
            "canonical_object_sha256": self.canonical_object_sha256,
            "object_type": self.object_type,
            "oid": self.oid,
            "payload_sha256": self.payload_sha256,
        }

    def validate_current(self) -> None:
        if type(self) is not GitObjectCommitmentV3:
            raise ValueError("Git-object commitment subclasses are forbidden")
        supplied = _validate_digest(self.content_digest, "Git-object commitment digest")
        expected = _digest(self._payload(), domain=_OBJECT_COMMITMENT_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("Git-object commitment digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class BrainSourceSnapshotManifestV3:
    repository_label: str
    commit: GitObjectCommitmentV3
    root_tree_oid: str
    source_path: str
    source_mode: str
    source_blob: GitObjectCommitmentV3
    projection_selector: str
    projected_card_count: int
    projected_card_array_sha256: str
    retrieval_source_path: str
    retrieval_source_mode: str
    retrieval_source_blob: GitObjectCommitmentV3
    rights_state: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not BrainSourceSnapshotManifestV3:
            raise ValueError("brain-source manifest subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._payload(), domain=_SOURCE_MANIFEST_DOMAIN),
        )

    def _validate_fields(self) -> None:
        if type(self.repository_label) is not str or self.repository_label != (
            "math-research-brain"
        ):
            raise ValueError("brain-source repository label is unsupported")
        if type(self.commit) is not GitObjectCommitmentV3:
            raise ValueError("brain-source manifest requires an exact commit")
        self.commit.validate_current()
        if (
            self.commit.object_type != "commit"
            or self.commit.oid != BRAIN_SOURCE_COMMIT_V3
            or self.commit.byte_count != BRAIN_SOURCE_COMMIT_BYTES_V3
            or self.commit.payload_sha256 != BRAIN_SOURCE_COMMIT_PAYLOAD_SHA256_V3
            or self.commit.canonical_object_sha256
            != BRAIN_SOURCE_COMMIT_CANONICAL_SHA256_V3
        ):
            raise ValueError("brain-source commit differs from the frozen pin")
        if type(self.root_tree_oid) is not str or self.root_tree_oid != BRAIN_SOURCE_TREE_V3:
            raise ValueError("brain-source root tree differs from the frozen pin")
        if (
            type(self.source_path) is not str
            or self.source_path != BRAIN_SOURCE_PATH_V3
            or type(self.source_mode) is not str
            or self.source_mode != "100644"
        ):
            raise ValueError("brain-source projection path or mode is unsupported")
        if type(self.source_blob) is not GitObjectCommitmentV3:
            raise ValueError("brain-source manifest requires an exact source blob")
        self.source_blob.validate_current()
        if (
            self.source_blob.object_type != "blob"
            or self.source_blob.oid != BRAIN_SOURCE_BLOB_V3
            or self.source_blob.byte_count != BRAIN_SOURCE_BLOB_BYTES_V3
            or self.source_blob.payload_sha256 != BRAIN_SOURCE_BLOB_SHA256_V3
            or self.source_blob.canonical_object_sha256
            != BRAIN_SOURCE_BLOB_CANONICAL_SHA256_V3
        ):
            raise ValueError("brain-source blob differs from the frozen pin")
        if type(self.projection_selector) is not str or self.projection_selector != (
            "/reference_cards"
        ):
            raise ValueError("brain-source projection selector is unsupported")
        if type(self.projected_card_count) is not int or self.projected_card_count != (
            BRAIN_CARD_COUNT_V3
        ):
            raise ValueError("brain-source card count differs from the pin")
        if type(self.projected_card_array_sha256) is not str or (
            self.projected_card_array_sha256 != BRAIN_CARD_ARRAY_SHA256_V3
        ):
            raise ValueError("brain-source card-array digest differs from the pin")
        if (
            type(self.retrieval_source_path) is not str
            or self.retrieval_source_path != BRAIN_RETRIEVAL_PATH_V3
            or type(self.retrieval_source_mode) is not str
            or self.retrieval_source_mode != "100644"
        ):
            raise ValueError("brain retrieval path or mode differs from the frozen pin")
        if type(self.retrieval_source_blob) is not GitObjectCommitmentV3:
            raise ValueError("brain-source manifest requires an exact retrieval blob")
        self.retrieval_source_blob.validate_current()
        if (
            self.retrieval_source_blob.object_type != "blob"
            or self.retrieval_source_blob.oid != BRAIN_RETRIEVAL_BLOB_V3
            or self.retrieval_source_blob.byte_count != BRAIN_RETRIEVAL_BLOB_BYTES_V3
            or self.retrieval_source_blob.payload_sha256
            != BRAIN_RETRIEVAL_PAYLOAD_SHA256_V3
            or self.retrieval_source_blob.canonical_object_sha256
            != BRAIN_RETRIEVAL_CANONICAL_SHA256_V3
        ):
            raise ValueError("brain retrieval implementation differs from the frozen pin")
        if type(self.rights_state) is not str or self.rights_state != (
            BRAIN_SOURCE_RIGHTS_STATE_V3
        ):
            raise ValueError("brain-source rights state is unsupported")

    def _payload(self) -> dict[str, object]:
        return {
            "commit": self.commit.to_dict(),
            "projected_card_array_sha256": self.projected_card_array_sha256,
            "projected_card_count": self.projected_card_count,
            "projection_selector": self.projection_selector,
            "repository_label": self.repository_label,
            "retrieval_source_blob": self.retrieval_source_blob.to_dict(),
            "retrieval_source_mode": self.retrieval_source_mode,
            "retrieval_source_path": self.retrieval_source_path,
            "rights_state": self.rights_state,
            "root_tree_oid": self.root_tree_oid,
            "source_blob": self.source_blob.to_dict(),
            "source_mode": self.source_mode,
            "source_path": self.source_path,
        }

    def validate_current(self) -> None:
        if type(self) is not BrainSourceSnapshotManifestV3:
            raise ValueError("brain-source manifest subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "brain-source manifest digest")
        expected = _digest(self._payload(), domain=_SOURCE_MANIFEST_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("brain-source manifest digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class BrainMethodCardV3:
    source_ordinal: int
    source_locator_digest: str
    legacy_source_record_digest: str
    family: str
    title: str
    guidance: str = field(repr=False)
    retrieval_cues: tuple[str, ...]
    semantic_digest: str = field(init=False)
    record_id: str = field(init=False)
    content_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self) is not BrainMethodCardV3:
            raise ValueError("brain-method-card subclasses are forbidden")
        self._validate_fields()
        semantic = _digest(self._semantic_payload(), domain=_SEMANTIC_CARD_DOMAIN)
        object.__setattr__(self, "semantic_digest", semantic)
        object.__setattr__(self, "record_id", "method-card:" + semantic.removeprefix("sha256:"))
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._record_payload(), domain=_CARD_RECORD_DOMAIN),
        )

    def _validate_fields(self) -> None:
        if type(self.source_ordinal) is not int or not 0 <= self.source_ordinal < (
            BRAIN_CARD_COUNT_V3
        ):
            raise ValueError("brain card source ordinal is invalid")
        _validate_digest(self.source_locator_digest, "brain card source-locator digest")
        _validate_digest(
            self.legacy_source_record_digest,
            "brain card legacy-record digest",
        )
        if type(self.family) is not str or _SAFE_FAMILY_RE.fullmatch(self.family) is None:
            raise ValueError("brain card family is invalid")
        _bounded_text(self.title, "brain card title", maximum=256)
        _bounded_text(self.guidance, "brain card guidance", maximum=2048)
        if any(marker in self.title or marker in self.guidance for marker in ("\0", "\r", "\n")):
            raise ValueError("brain card rendered text must be exactly two single lines")
        if type(self.retrieval_cues) is not tuple or not 1 <= len(self.retrieval_cues) <= 12:
            raise ValueError("brain card retrieval cues must be a bounded exact tuple")
        for cue in self.retrieval_cues:
            _bounded_text(cue, "brain card retrieval cue", maximum=256)
            if any(marker in cue for marker in ("\0", "\r", "\n")):
                raise ValueError("brain card retrieval cues must be single-line text")
        if len(set(self.retrieval_cues)) != len(self.retrieval_cues):
            raise ValueError("brain card retrieval cues must be unique")
        semantic_text = " ".join((self.family, self.title, self.guidance, *self.retrieval_cues))
        if any(character.isdigit() for character in semantic_text):
            raise ValueError("brain card semantic text contains a problem-specific numeral")

    def _semantic_payload(self) -> dict[str, object]:
        return {
            "family": self.family,
            "guidance": self.guidance,
            "retrieval_cues": list(self.retrieval_cues),
            "status": "provisional_fallible",
            "title": self.title,
        }

    def _record_payload(self) -> dict[str, object]:
        return {
            "legacy_source_record_digest": self.legacy_source_record_digest,
            "semantic": self._semantic_payload(),
            "source_locator_digest": self.source_locator_digest,
            "source_ordinal": self.source_ordinal,
        }

    def validate_current(self) -> None:
        if type(self) is not BrainMethodCardV3:
            raise ValueError("brain-method-card subclasses are forbidden")
        self._validate_fields()
        expected_semantic = _digest(self._semantic_payload(), domain=_SEMANTIC_CARD_DOMAIN)
        if not hmac.compare_digest(self.semantic_digest, expected_semantic):
            raise ValueError("brain card semantic digest is stale")
        expected_id = "method-card:" + expected_semantic.removeprefix("sha256:")
        if self.record_id != expected_id:
            raise ValueError("brain card record ID is stale")
        expected_content = _digest(self._record_payload(), domain=_CARD_RECORD_DOMAIN)
        if not hmac.compare_digest(self.content_digest, expected_content):
            raise ValueError("brain card content digest is stale")

    def private_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._record_payload() | {
            "content_digest": self.content_digest,
            "record_id": self.record_id,
            "semantic_digest": self.semantic_digest,
        }


def _project_brain_method_cards_v3(parsed: object) -> tuple[BrainMethodCardV3, ...]:
    if type(parsed) is not dict:
        raise ValueError("brain source JSON root must be an exact object")
    raw_cards = parsed.get("reference_cards")
    if type(raw_cards) is not list or len(raw_cards) != BRAIN_CARD_COUNT_V3:
        raise ValueError("brain source must contain exactly twenty reference cards")
    array_digest = _payload_sha256(canonical_json(raw_cards).encode("utf-8"))
    if not hmac.compare_digest(array_digest, BRAIN_CARD_ARRAY_SHA256_V3):
        raise ValueError("brain source reference-card array differs from the frozen pin")

    projected: list[BrainMethodCardV3] = []
    for ordinal, raw in enumerate(raw_cards):
        if type(raw) is not dict or tuple(raw) != _CARD_FIELDS_V3:
            raise ValueError("brain source card fields differ from the exact projection")
        expected_legacy_id = f"C{ordinal + 1:02d}"
        if type(raw["card_id"]) is not str or raw["card_id"] != expected_legacy_id:
            raise ValueError("brain source card IDs are not complete and ordered")
        cues = raw["retrieval_cues"]
        if type(cues) is not list or not all(type(cue) is str for cue in cues):
            raise ValueError("brain source retrieval cues must be an exact text array")
        legacy_record_digest = _digest(raw, domain=_LEGACY_CARD_DOMAIN)
        locator_digest = _digest(
            {
                "card_id_digest": _payload_sha256(raw["card_id"].encode("ascii")),
                "selector": "/reference_cards",
                "source_blob_oid": BRAIN_SOURCE_BLOB_V3,
                "source_ordinal": ordinal,
            },
            domain=_CARD_LOCATOR_DOMAIN,
        )
        projected.append(
            BrainMethodCardV3(
                source_ordinal=ordinal,
                source_locator_digest=locator_digest,
                legacy_source_record_digest=legacy_record_digest,
                family=raw["family"],
                title=raw["title"],
                guidance=raw["guidance"],
                retrieval_cues=tuple(cues),
            )
        )
    if len({item.semantic_digest for item in projected}) != BRAIN_CARD_COUNT_V3:
        raise ValueError("brain source contains duplicate semantic method cards")
    return tuple(projected)


def _index_node_payload_v3(card: BrainMethodCardV3) -> dict[str, object]:
    card.validate_current()
    return {
        "body": " ".join(card.retrieval_cues),
        "created_at": _CREATED_AT_SENTINEL_V3,
        "id": card.record_id,
        "kind": NodeKind.REFLEX.value,
        "metadata": {
            "content_role": "method_card",
            "privacy_status": "eligible",
            "source_digest": card.semantic_digest,
            "split": "reference",
        },
        "title": card.title,
    }


@dataclass(frozen=True, slots=True)
class BrainCorpusManifestV3:
    source_manifest: BrainSourceSnapshotManifestV3
    cards: tuple[BrainMethodCardV3, ...] = field(repr=False)
    index_node_universe_digest: str
    empty_edge_universe_digest: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not BrainCorpusManifestV3:
            raise ValueError("brain-corpus manifest subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._private_payload(), domain=_CORPUS_DOMAIN),
        )

    def _validate_fields(self) -> None:
        if type(self.source_manifest) is not BrainSourceSnapshotManifestV3:
            raise ValueError("brain corpus requires an exact source manifest")
        self.source_manifest.validate_current()
        if type(self.cards) is not tuple or len(self.cards) != BRAIN_CARD_COUNT_V3:
            raise ValueError("brain corpus requires exactly twenty cards")
        if not all(type(card) is BrainMethodCardV3 for card in self.cards):
            raise ValueError("brain corpus cards must be exact records")
        for card in self.cards:
            card.validate_current()
        if tuple(sorted(self.cards, key=lambda card: card.semantic_digest)) != self.cards:
            raise ValueError("brain corpus cards must use semantic-digest order")
        if len({card.semantic_digest for card in self.cards}) != BRAIN_CARD_COUNT_V3:
            raise ValueError("brain corpus semantic cards must be unique")
        if {card.source_ordinal for card in self.cards} != set(range(BRAIN_CARD_COUNT_V3)):
            raise ValueError("brain corpus source ordinals are incomplete")
        source_order = tuple(sorted(self.cards, key=lambda card: card.source_ordinal))
        legacy_universe = _digest(
            [card.legacy_source_record_digest for card in source_order],
            domain="org.math-research-brain/legacy-card-universe/v3",
        )
        if not hmac.compare_digest(
            legacy_universe,
            BRAIN_LEGACY_CARD_UNIVERSE_SHA256_V3,
        ):
            raise ValueError("brain corpus legacy-card universe differs from the pin")
        semantic_universe = _digest(
            [card.semantic_digest for card in self.cards],
            domain="org.math-research-brain/brain-semantic-card-universe/v3",
        )
        if not hmac.compare_digest(
            semantic_universe,
            BRAIN_SEMANTIC_CARD_UNIVERSE_SHA256_V3,
        ):
            raise ValueError("brain corpus semantic-card universe differs from the pin")
        record_universe = _digest(
            [card.content_digest for card in self.cards],
            domain="org.math-research-brain/brain-card-record-universe/v3",
        )
        if not hmac.compare_digest(
            record_universe,
            BRAIN_CARD_RECORD_UNIVERSE_SHA256_V3,
        ):
            raise ValueError("brain corpus card-record universe differs from the pin")
        expected_nodes = _digest(
            [_index_node_payload_v3(card) for card in self.cards],
            domain="org.math-research-brain/brain-index-universe/v3",
        )
        if self.index_node_universe_digest != expected_nodes:
            raise ValueError("brain corpus index-node universe digest is stale")
        expected_edges = _digest([], domain="org.math-research-brain/brain-edge-universe/v3")
        if self.empty_edge_universe_digest != expected_edges:
            raise ValueError("brain corpus must have an exactly empty edge universe")

    def _private_payload(self) -> dict[str, object]:
        return {
            "cards": [card.private_dict() for card in self.cards],
            "empty_edge_universe_digest": self.empty_edge_universe_digest,
            "index_node_universe_digest": self.index_node_universe_digest,
            "source_manifest": self.source_manifest.to_dict(),
        }

    def validate_current(self) -> None:
        if type(self) is not BrainCorpusManifestV3:
            raise ValueError("brain-corpus manifest subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "brain-corpus manifest digest")
        expected = _digest(self._private_payload(), domain=_CORPUS_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("brain-corpus manifest digest is stale")

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        return {
            "card_count": len(self.cards),
            "content_digest": self.content_digest,
            "empty_edge_universe_digest": self.empty_edge_universe_digest,
            "index_node_universe_digest": self.index_node_universe_digest,
            "rights_state": self.source_manifest.rights_state,
            "source_manifest_digest": self.source_manifest.content_digest,
        }


def build_brain_corpus_manifest_v3(
    source_manifest: BrainSourceSnapshotManifestV3,
    cards: Iterable[BrainMethodCardV3],
) -> BrainCorpusManifestV3:
    if type(source_manifest) is not BrainSourceSnapshotManifestV3:
        raise ValueError("brain corpus builder requires an exact source manifest")
    if type(cards) in (str, bytes, dict):
        raise ValueError("brain corpus cards must be an iterable of exact records")
    materialized = tuple(cards)
    if not all(type(card) is BrainMethodCardV3 for card in materialized):
        raise ValueError("brain corpus cards must be exact records")
    ordered = tuple(sorted(materialized, key=lambda card: card.semantic_digest))
    return BrainCorpusManifestV3(
        source_manifest=source_manifest,
        cards=ordered,
        index_node_universe_digest=_digest(
            [_index_node_payload_v3(card) for card in ordered],
            domain="org.math-research-brain/brain-index-universe/v3",
        ),
        empty_edge_universe_digest=_digest(
            [],
            domain="org.math-research-brain/brain-edge-universe/v3",
        ),
    )


def acquire_frozen_brain_corpus_v3(repository_root: Path) -> BrainCorpusManifestV3:
    """Read only pinned Git objects and build the exact card-only corpus."""

    if not isinstance(repository_root, Path) or not repository_root.is_absolute():
        raise ValueError("brain repository root must be an absolute Path")
    repository_root = repository_root.resolve(strict=True)
    top_level = _run_git_v3(repository_root, "rev-parse", "--show-toplevel")
    try:
        observed_root = Path(top_level.decode("utf-8").strip()).resolve(strict=True)
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("brain Git top-level path is invalid") from exc
    if os.path.normcase(str(observed_root)) != os.path.normcase(str(repository_root)):
        raise ValueError("brain repository root is not the exact Git top level")

    commit_type = _run_git_v3(repository_root, "cat-file", "-t", BRAIN_SOURCE_COMMIT_V3)
    if commit_type != b"commit\n":
        raise ValueError("brain source pin is not a commit object")
    commit_payload = _run_git_v3(
        repository_root,
        "cat-file",
        "commit",
        BRAIN_SOURCE_COMMIT_V3,
    )
    commit = GitObjectCommitmentV3(
        object_type="commit",
        oid=BRAIN_SOURCE_COMMIT_V3,
        byte_count=len(commit_payload),
        payload_sha256=_payload_sha256(commit_payload),
        canonical_object_sha256=_canonical_object_sha256("commit", commit_payload),
    )
    if not commit_payload.startswith(f"tree {BRAIN_SOURCE_TREE_V3}\n".encode("ascii")):
        raise ValueError("brain source commit names another root tree")

    tree_row = _run_git_v3(
        repository_root,
        "ls-tree",
        BRAIN_SOURCE_COMMIT_V3,
        "--",
        BRAIN_SOURCE_PATH_V3,
    )
    expected_tree_row = (
        f"100644 blob {BRAIN_SOURCE_BLOB_V3}\t{BRAIN_SOURCE_PATH_V3}\n".encode("ascii")
    )
    if tree_row != expected_tree_row:
        raise ValueError("brain source path, mode, or blob differs from the frozen pin")
    blob_type = _run_git_v3(repository_root, "cat-file", "-t", BRAIN_SOURCE_BLOB_V3)
    if blob_type != b"blob\n":
        raise ValueError("brain source pin is not a blob object")
    blob_payload = _run_git_v3(
        repository_root,
        "cat-file",
        "blob",
        BRAIN_SOURCE_BLOB_V3,
    )
    blob = GitObjectCommitmentV3(
        object_type="blob",
        oid=BRAIN_SOURCE_BLOB_V3,
        byte_count=len(blob_payload),
        payload_sha256=_payload_sha256(blob_payload),
        canonical_object_sha256=_canonical_object_sha256("blob", blob_payload),
    )
    parsed = _load_strict_json_v3(blob_payload)
    cards = _project_brain_method_cards_v3(parsed)

    retrieval_tree_row = _run_git_v3(
        repository_root,
        "ls-tree",
        BRAIN_SOURCE_COMMIT_V3,
        "--",
        BRAIN_RETRIEVAL_PATH_V3,
    )
    expected_retrieval_tree_row = (
        f"100644 blob {BRAIN_RETRIEVAL_BLOB_V3}\t{BRAIN_RETRIEVAL_PATH_V3}\n".encode(
            "ascii"
        )
    )
    if retrieval_tree_row != expected_retrieval_tree_row:
        raise ValueError("brain retrieval path, mode, or blob differs from the frozen pin")
    retrieval_blob_type = _run_git_v3(
        repository_root,
        "cat-file",
        "-t",
        BRAIN_RETRIEVAL_BLOB_V3,
    )
    if retrieval_blob_type != b"blob\n":
        raise ValueError("brain retrieval pin is not a blob object")
    retrieval_blob_payload = _run_git_v3(
        repository_root,
        "cat-file",
        "blob",
        BRAIN_RETRIEVAL_BLOB_V3,
    )
    retrieval_blob = GitObjectCommitmentV3(
        object_type="blob",
        oid=BRAIN_RETRIEVAL_BLOB_V3,
        byte_count=len(retrieval_blob_payload),
        payload_sha256=_payload_sha256(retrieval_blob_payload),
        canonical_object_sha256=_canonical_object_sha256(
            "blob",
            retrieval_blob_payload,
        ),
    )
    source = BrainSourceSnapshotManifestV3(
        repository_label="math-research-brain",
        commit=commit,
        root_tree_oid=BRAIN_SOURCE_TREE_V3,
        source_path=BRAIN_SOURCE_PATH_V3,
        source_mode="100644",
        source_blob=blob,
        projection_selector="/reference_cards",
        projected_card_count=len(cards),
        projected_card_array_sha256=_payload_sha256(
            canonical_json(parsed["reference_cards"]).encode("utf-8")
        ),
        retrieval_source_path=BRAIN_RETRIEVAL_PATH_V3,
        retrieval_source_mode="100644",
        retrieval_source_blob=retrieval_blob,
        rights_state=BRAIN_SOURCE_RIGHTS_STATE_V3,
    )
    return build_brain_corpus_manifest_v3(source, cards)


_SALIENT_STOPWORDS_V3 = tuple(
    sorted(
        {
            "a",
            "about",
            "all",
            "an",
            "and",
            "answer",
            "are",
            "as",
            "at",
            "be",
            "before",
            "both",
            "by",
            "calculate",
            "can",
            "compute",
            "determine",
            "did",
            "do",
            "does",
            "each",
            "eight",
            "eleven",
            "every",
            "fifteen",
            "find",
            "first",
            "five",
            "following",
            "for",
            "four",
            "fourteen",
            "from",
            "given",
            "has",
            "have",
            "how",
            "if",
            "in",
            "into",
            "is",
            "it",
            "its",
            "let",
            "many",
            "may",
            "must",
            "nine",
            "number",
            "numbers",
            "of",
            "on",
            "one",
            "only",
            "or",
            "point",
            "points",
            "problem",
            "prove",
            "question",
            "second",
            "seven",
            "seventeen",
            "show",
            "six",
            "sixteen",
            "solve",
            "such",
            "suppose",
            "ten",
            "test",
            "than",
            "that",
            "the",
            "their",
            "them",
            "then",
            "there",
            "these",
            "they",
            "third",
            "thirteen",
            "this",
            "three",
            "to",
            "twelve",
            "twenty",
            "two",
            "use",
            "using",
            "was",
            "we",
            "what",
            "when",
            "where",
            "which",
            "with",
            "would",
            "your",
        }
    )
)
_SALIENT_PHRASE_FEATURES_V3 = (
    ("if and only if", ("biconditional", "equivalence")),
)
_SALIENT_COMPOUND_RE_V3 = re.compile(r"[-'\N{RIGHT SINGLE QUOTATION MARK}\N{EN DASH}\N{EM DASH}]+")
_METHOD_CARD_ID_RE_V3 = re.compile(r"method-card:[0-9a-f]{64}\Z")

_STOPWORD_DOMAIN_V3 = "org.math-research-brain/salient-stopwords/v3"
_POLICY_DOMAIN_V3 = "org.math-research-brain/brain-retrieval-policy/v3"
_QUERY_DOMAIN_V3 = "org.math-research-brain/solver-visible-packet-query/v3"
_SALIENT_QUERY_DOMAIN_V3 = "org.math-research-brain/salient-query-terms/v3"
_OVERLAP_DOMAIN_V3 = "org.math-research-brain/brain-explicit-overlap/v3"
_TRACE_DOMAIN_V3 = "org.math-research-brain/brain-score-trace/v3"
_HIT_RECEIPT_DOMAIN_V3 = "org.math-research-brain/brain-hit-receipt/v3"
_RETRIEVAL_RECEIPT_DOMAIN_V3 = "org.math-research-brain/brain-retrieval-receipt/v3"
_PACKET_FORMAT_DOMAIN_V3 = "org.math-research-brain/brain-packet-format/v3"
_PACKET_SHAPE_DOMAIN_V3 = "org.math-research-brain/brain-packet-shape/v3"
_PACKET_STATUS_DOMAIN_V3 = "org.math-research-brain/brain-packet-status/v3"
_PACKET_CANDIDATE_DOMAIN_V3 = "org.math-research-brain/brain-packet-candidate/v3"

BRAIN_SALIENT_STOPWORDS_SHA256_V3 = _digest(
    list(_SALIENT_STOPWORDS_V3),
    domain=_STOPWORD_DOMAIN_V3,
)
BRAIN_PACKET_FORMAT_SHA256_V3 = _digest(
    {
        "encoding": "utf-8",
        "renderer": "title-newline-guidance",
        "trailing_newline": False,
        "version": 3,
    },
    domain=_PACKET_FORMAT_DOMAIN_V3,
)
BRAIN_PACKET_SECTION_SHAPE_SHA256_V3 = _digest(
    ["title_line", "guidance_line"],
    domain=_PACKET_SHAPE_DOMAIN_V3,
)
BRAIN_PACKET_STATUS_VECTOR_SHA256_V3 = _digest(
    ["provisional_fallible"],
    domain=_PACKET_STATUS_DOMAIN_V3,
)
BRAIN_RETRIEVAL_POLICY_SHA256_V3 = (
    "sha256:51b9b35da598ccccee6e0cdf16295718dcc09e2463b17ec108b5f240b5ecc57e"
)
BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3 = (
    "sha256:1bedf37b54a4aaa656afb02ec9d7e4fbe2ed8d19543095d0f69ee8f7adff25d9"
)


def _canonical_nonnegative_float_hex_v3(value: object, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be canonical float.hex text")
    try:
        parsed = float.fromhex(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be canonical float.hex text") from exc
    if (
        not math.isfinite(parsed)
        or math.copysign(1.0, parsed) < 0
        or parsed.hex() != value
    ):
        raise ValueError(f"{label} must be finite, nonnegative canonical float.hex text")
    return value


@dataclass(frozen=True, slots=True)
class BrainRetrievalPolicyV3:
    policy_id: str = "salient-title-cue-top-one-v3"
    normalization_id: str = "nfkc-casefold-compound-split-alpha-unique-sorted-v3"
    stopword_digest: str = BRAIN_SALIENT_STOPWORDS_SHA256_V3
    legacy_retrieval_blob_oid: str = BRAIN_RETRIEVAL_BLOB_V3
    legacy_retrieval_payload_sha256: str = BRAIN_RETRIEVAL_PAYLOAD_SHA256_V3
    index_fields: tuple[str, ...] = ("title", "retrieval_cues")
    minimum_term_characters: int = 2
    maximum_term_utf8_bytes: int = 64
    maximum_salient_terms: int = 512
    considered_limit: int = 2
    selected_limit: int = 1
    graph_depth: int = 0
    support_floor_hex: str = (2.0).hex()
    selection_boundary_margin_hex: str = (0.5).hex()
    minimum_explicit_overlap_terms: int = 2
    explicit_title_or_cue_overlap_required: bool = True
    in_process_execution_identity_attested: bool = False
    blinded_relevance_audit_required: bool = True
    solver_dispatch_authorized: bool = False
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not BrainRetrievalPolicyV3:
            raise ValueError("brain retrieval policy subclasses are forbidden")
        self._validate_fields()
        content = _digest(self._payload(), domain=_POLICY_DOMAIN_V3)
        if not hmac.compare_digest(content, BRAIN_RETRIEVAL_POLICY_SHA256_V3):
            raise ValueError("brain retrieval policy differs from the frozen commitment")
        object.__setattr__(self, "content_digest", content)

    def _validate_fields(self) -> None:
        exact_values: tuple[tuple[object, object, str], ...] = (
            (self.policy_id, "salient-title-cue-top-one-v3", "policy id"),
            (
                self.normalization_id,
                "nfkc-casefold-compound-split-alpha-unique-sorted-v3",
                "normalization id",
            ),
            (self.stopword_digest, BRAIN_SALIENT_STOPWORDS_SHA256_V3, "stopword digest"),
            (
                self.legacy_retrieval_blob_oid,
                BRAIN_RETRIEVAL_BLOB_V3,
                "legacy retrieval blob",
            ),
            (
                self.legacy_retrieval_payload_sha256,
                BRAIN_RETRIEVAL_PAYLOAD_SHA256_V3,
                "legacy retrieval payload",
            ),
            (self.index_fields, ("title", "retrieval_cues"), "index fields"),
            (self.minimum_term_characters, 2, "minimum term characters"),
            (self.maximum_term_utf8_bytes, 64, "maximum term bytes"),
            (self.maximum_salient_terms, 512, "maximum salient terms"),
            (self.considered_limit, 2, "considered limit"),
            (self.selected_limit, 1, "selected limit"),
            (self.graph_depth, 0, "graph depth"),
            (self.support_floor_hex, (2.0).hex(), "support floor"),
            (
                self.selection_boundary_margin_hex,
                (0.5).hex(),
                "selection-boundary margin",
            ),
            (self.minimum_explicit_overlap_terms, 2, "minimum explicit overlap"),
            (
                self.explicit_title_or_cue_overlap_required,
                True,
                "explicit-overlap requirement",
            ),
            (
                self.in_process_execution_identity_attested,
                False,
                "in-process execution-identity state",
            ),
            (
                self.blinded_relevance_audit_required,
                True,
                "blinded relevance-audit requirement",
            ),
            (
                self.solver_dispatch_authorized,
                False,
                "solver-dispatch authorization",
            ),
        )
        for observed, expected, label in exact_values:
            if type(observed) is not type(expected) or observed != expected:
                raise ValueError(f"brain retrieval {label} differs from the frozen policy")
        _validate_digest(self.stopword_digest, "brain retrieval stopword digest")
        _validate_digest(
            self.legacy_retrieval_payload_sha256,
            "brain retrieval implementation digest",
        )
        _canonical_nonnegative_float_hex_v3(self.support_floor_hex, "support floor")
        _canonical_nonnegative_float_hex_v3(
            self.selection_boundary_margin_hex,
            "selection-boundary margin",
        )

    def _payload(self) -> dict[str, object]:
        return {
            "considered_limit": self.considered_limit,
            "blinded_relevance_audit_required": self.blinded_relevance_audit_required,
            "explicit_title_or_cue_overlap_required": (
                self.explicit_title_or_cue_overlap_required
            ),
            "graph_depth": self.graph_depth,
            "index_fields": list(self.index_fields),
            "in_process_execution_identity_attested": (
                self.in_process_execution_identity_attested
            ),
            "legacy_retrieval_blob_oid": self.legacy_retrieval_blob_oid,
            "legacy_retrieval_payload_sha256": self.legacy_retrieval_payload_sha256,
            "maximum_salient_terms": self.maximum_salient_terms,
            "maximum_term_utf8_bytes": self.maximum_term_utf8_bytes,
            "minimum_explicit_overlap_terms": self.minimum_explicit_overlap_terms,
            "minimum_term_characters": self.minimum_term_characters,
            "normalization_id": self.normalization_id,
            "phrase_features": [
                [phrase, list(features)]
                for phrase, features in _SALIENT_PHRASE_FEATURES_V3
            ],
            "policy_id": self.policy_id,
            "selected_limit": self.selected_limit,
            "selection_boundary_margin_hex": self.selection_boundary_margin_hex,
            "stopword_digest": self.stopword_digest,
            "support_floor_hex": self.support_floor_hex,
            "solver_dispatch_authorized": self.solver_dispatch_authorized,
        }

    def validate_current(self) -> None:
        if type(self) is not BrainRetrievalPolicyV3:
            raise ValueError("brain retrieval policy subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "brain retrieval policy digest")
        expected = _digest(self._payload(), domain=_POLICY_DOMAIN_V3)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("brain retrieval policy digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class SolverVisiblePacketQueryV3:
    case_id: str
    domain: MathDomain
    problem_text: str = field(repr=False)
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not SolverVisiblePacketQueryV3:
            raise ValueError("solver-visible query subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._payload(), domain=_QUERY_DOMAIN_V3),
        )

    def _validate_fields(self) -> None:
        if type(self.case_id) is not str or _SAFE_CASE_ID_RE.fullmatch(self.case_id) is None:
            raise ValueError("solver-visible query case id is invalid")
        if type(self.domain) is not MathDomain:
            raise ValueError("solver-visible query domain must be exact public metadata")
        _bounded_text(self.problem_text, "solver-visible problem text", maximum=128_000)
        if "\0" in self.problem_text:
            raise ValueError("solver-visible problem text contains a null byte")

    @property
    def problem_text_digest(self) -> str:
        self._validate_fields()
        return _payload_sha256(self.problem_text.encode("utf-8"))

    def _payload(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "domain": self.domain.value,
            "problem_text_byte_count": len(self.problem_text.encode("utf-8")),
            "problem_text_digest": self.problem_text_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not SolverVisiblePacketQueryV3:
            raise ValueError("solver-visible query subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "solver-visible query digest")
        expected = _digest(self._payload(), domain=_QUERY_DOMAIN_V3)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("solver-visible query digest is stale")

    def commitment_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def _phrase_space_v3(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = _SALIENT_COMPOUND_RE_V3.sub(" ", normalized)
    return " ".join(normalized.split())


def _salient_terms_v3(
    text: str,
    policy: BrainRetrievalPolicyV3,
) -> tuple[str, ...]:
    policy.validate_current()
    normalized = _phrase_space_v3(text)
    normalized_tokens = tokenize(normalized)
    terms = {
        term
        for term in normalized_tokens
        if len(term) >= policy.minimum_term_characters
        and len(term.encode("utf-8")) <= policy.maximum_term_utf8_bytes
        and term.isalpha()
        and term not in _SALIENT_STOPWORDS_V3
    }
    for phrase, features in _SALIENT_PHRASE_FEATURES_V3:
        phrase_tokens = tuple(phrase.split())
        if any(
            normalized_tokens[index : index + len(phrase_tokens)] == phrase_tokens
            for index in range(len(normalized_tokens) - len(phrase_tokens) + 1)
        ):
            terms.update(features)
    return tuple(sorted(terms))


def _card_index_phrases_v3(
    card: BrainMethodCardV3,
    policy: BrainRetrievalPolicyV3,
) -> tuple[tuple[str, ...], ...]:
    card.validate_current()
    return tuple(
        _salient_terms_v3(phrase, policy)
        for phrase in (card.title, *card.retrieval_cues)
    )


def _best_explicit_phrase_overlap_v3(
    query_terms: tuple[str, ...],
    card: BrainMethodCardV3,
    policy: BrainRetrievalPolicyV3,
) -> tuple[str, ...]:
    query_set = set(query_terms)
    overlaps = tuple(
        tuple(sorted(query_set.intersection(phrase_terms)))
        for phrase_terms in _card_index_phrases_v3(card, policy)
    )
    return min(
        enumerate(overlaps),
        key=lambda item: (-len(item[1]), item[1], item[0]),
    )[1]


def _graph_nodes_v3(
    corpus: BrainCorpusManifestV3,
    policy: BrainRetrievalPolicyV3,
) -> tuple[GraphNode, ...]:
    corpus.validate_current()
    policy.validate_current()
    synthetic_features = {
        feature
        for _phrase, features in _SALIENT_PHRASE_FEATURES_V3
        for feature in features
    }
    nodes: list[GraphNode] = []
    for card in corpus.cards:
        payload = _index_node_payload_v3(card)
        card_features = sorted(
            synthetic_features.intersection(
                term
                for phrase_terms in _card_index_phrases_v3(card, policy)
                for term in phrase_terms
            )
        )
        body = payload["body"]
        if card_features:
            body += " " + " ".join(card_features)
        nodes.append(
            GraphNode(
                id=payload["id"],
                kind=NodeKind.REFLEX,
                title=payload["title"],
                body=body,
                metadata=dict(payload["metadata"]),
                created_at=payload["created_at"],
            )
        )
    return tuple(nodes)


def _derived_index_universe_digest_v3(nodes: tuple[GraphNode, ...]) -> str:
    return _digest(
        [
            {
                "body": node.body,
                "created_at": node.created_at,
                "id": node.id,
                "kind": node.kind.value,
                "metadata": node.metadata,
                "title": node.title,
            }
            for node in nodes
        ],
        domain="org.math-research-brain/brain-derived-index-universe/v3",
    )


def _score_trace_digest_v3(trace: Iterable[object]) -> str:
    rows: list[dict[str, object]] = []
    for item in trace:
        channel = getattr(item, "channel", None)
        amount = getattr(item, "amount", None)
        detail = getattr(item, "detail", None)
        source_node_id = getattr(item, "source_node_id", None)
        edge_id = getattr(item, "edge_id", None)
        edge_kind = getattr(item, "edge_kind", None)
        edge_polarity = getattr(item, "edge_polarity", None)
        path = getattr(item, "path", None)
        if (
            not hasattr(channel, "value")
            or type(amount) is not float
            or not math.isfinite(amount)
            or amount < 0
            or type(detail) is not str
            or type(path) is not tuple
        ):
            raise ValueError("brain retrieval returned a malformed score trace")
        rows.append(
            {
                "amount_hex": amount.hex(),
                "channel": channel.value,
                "detail": detail,
                "edge_id": edge_id,
                "edge_kind": None if edge_kind is None else edge_kind.value,
                "edge_polarity": (
                    None if edge_polarity is None else edge_polarity.value
                ),
                "path": list(path),
                "source_node_id": source_node_id,
            }
        )
    return _digest(rows, domain=_TRACE_DOMAIN_V3)


@dataclass(frozen=True, slots=True)
class BrainHitReceiptV3:
    rank: int
    record_id: str
    semantic_digest: str
    lexical_score_hex: str
    symbol_score_hex: str
    formula_score_hex: str
    graph_score_hex: str
    warning_score_hex: str
    support_score_hex: str
    total_score_hex: str
    explicit_phrase_overlap_count: int
    explicit_phrase_overlap_digest: str
    score_trace_digest: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not BrainHitReceiptV3:
            raise ValueError("brain hit receipt subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._payload(), domain=_HIT_RECEIPT_DOMAIN_V3),
        )

    def _validate_fields(self) -> None:
        if type(self.rank) is not int or not 1 <= self.rank <= 2:
            raise ValueError("brain hit rank must be one or two")
        if type(self.record_id) is not str or _METHOD_CARD_ID_RE_V3.fullmatch(
            self.record_id
        ) is None:
            raise ValueError("brain hit record id is invalid")
        _validate_digest(self.semantic_digest, "brain hit semantic digest")
        if self.record_id != "method-card:" + self.semantic_digest.removeprefix("sha256:"):
            raise ValueError("brain hit record id does not match its semantic digest")
        component_names = (
            "lexical_score_hex",
            "symbol_score_hex",
            "formula_score_hex",
            "graph_score_hex",
            "warning_score_hex",
        )
        components = tuple(
            float.fromhex(
                _canonical_nonnegative_float_hex_v3(
                    getattr(self, name),
                    name.replace("_", " "),
                )
            )
            for name in component_names
        )
        support = _canonical_nonnegative_float_hex_v3(
            self.support_score_hex,
            "brain hit support score",
        )
        total = _canonical_nonnegative_float_hex_v3(
            self.total_score_hex,
            "brain hit total score",
        )
        if support != sum(components[:4]).hex():
            raise ValueError("brain hit support score differs from its components")
        if total != sum(components).hex():
            raise ValueError("brain hit total score differs from its components")
        if any(value != 0.0 for value in components[1:]):
            raise ValueError("brain v3 permits only the lexical score channel")
        if (
            type(self.explicit_phrase_overlap_count) is not int
            or not 0 <= self.explicit_phrase_overlap_count <= 64
        ):
            raise ValueError("brain hit explicit-overlap count is invalid")
        _validate_digest(
            self.explicit_phrase_overlap_digest,
            "brain hit explicit-overlap digest",
        )
        _validate_digest(self.score_trace_digest, "brain hit score-trace digest")

    def _payload(self) -> dict[str, object]:
        return {
            "explicit_phrase_overlap_count": self.explicit_phrase_overlap_count,
            "explicit_phrase_overlap_digest": self.explicit_phrase_overlap_digest,
            "formula_score_hex": self.formula_score_hex,
            "graph_score_hex": self.graph_score_hex,
            "lexical_score_hex": self.lexical_score_hex,
            "rank": self.rank,
            "record_id": self.record_id,
            "score_trace_digest": self.score_trace_digest,
            "semantic_digest": self.semantic_digest,
            "support_score_hex": self.support_score_hex,
            "symbol_score_hex": self.symbol_score_hex,
            "total_score_hex": self.total_score_hex,
            "warning_score_hex": self.warning_score_hex,
        }

    def validate_current(self) -> None:
        if type(self) is not BrainHitReceiptV3:
            raise ValueError("brain hit receipt subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "brain hit receipt digest")
        expected = _digest(self._payload(), domain=_HIT_RECEIPT_DOMAIN_V3)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("brain hit receipt digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


class BrainRetrievalDispositionV3(StrEnum):
    PROPOSAL = "unreviewed_proposal"
    ABSTAINED = "abstained"


class BrainRetrievalAbstentionV3(StrEnum):
    NO_SALIENT_TERMS = "no_salient_terms"
    SALIENT_TERM_BUDGET_EXCEEDED = "salient_term_budget_exceeded"
    NO_CANDIDATE_MATCH = "no_candidate_match"
    MISSING_EXPLICIT_PHRASE_OVERLAP = "missing_explicit_phrase_overlap"
    BELOW_SUPPORT_FLOOR = "below_support_floor"
    AMBIGUOUS_SELECTION_BOUNDARY = "ambiguous_selection_boundary"


@dataclass(frozen=True, slots=True)
class BrainRetrievalReceiptV3:
    case_id: str
    domain: MathDomain
    query_digest: str
    salient_query_digest: str
    salient_term_count: int
    corpus_digest: str
    policy_digest: str
    derived_index_universe_digest: str
    considered_hits: tuple[BrainHitReceiptV3, ...]
    disposition: BrainRetrievalDispositionV3
    abstention_reason: BrainRetrievalAbstentionV3 | None
    selection_boundary_margin_hex: str
    selected_record_ids: tuple[str, ...]
    selected_semantic_digests: tuple[str, ...]
    in_process_execution_identity_attested: bool
    blinded_relevance_audit_required: bool
    solver_dispatch_authorized: bool
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not BrainRetrievalReceiptV3:
            raise ValueError("brain retrieval receipt subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._payload(), domain=_RETRIEVAL_RECEIPT_DOMAIN_V3),
        )

    def _validate_fields(self) -> None:
        if type(self.case_id) is not str or _SAFE_CASE_ID_RE.fullmatch(self.case_id) is None:
            raise ValueError("brain retrieval receipt case id is invalid")
        if type(self.domain) is not MathDomain:
            raise ValueError("brain retrieval receipt domain must be exact")
        for label, value in (
            ("query digest", self.query_digest),
            ("salient query digest", self.salient_query_digest),
            ("corpus digest", self.corpus_digest),
            ("policy digest", self.policy_digest),
            ("derived index-universe digest", self.derived_index_universe_digest),
        ):
            _validate_digest(value, label)
        if (
            type(self.salient_term_count) is not int
            or not 0 <= self.salient_term_count <= 32_768
        ):
            raise ValueError("brain retrieval salient-term count is invalid")
        if type(self.considered_hits) is not tuple or len(self.considered_hits) > 2:
            raise ValueError("brain retrieval may consider at most two hits")
        if not all(type(hit) is BrainHitReceiptV3 for hit in self.considered_hits):
            raise ValueError("brain retrieval considered hits must be exact receipts")
        for hit in self.considered_hits:
            hit.validate_current()
        if tuple(hit.rank for hit in self.considered_hits) != tuple(
            range(1, len(self.considered_hits) + 1)
        ):
            raise ValueError("brain retrieval considered-hit ranks are not contiguous")
        if len({hit.record_id for hit in self.considered_hits}) != len(
            self.considered_hits
        ):
            raise ValueError("brain retrieval considered hits are not unique")
        _canonical_nonnegative_float_hex_v3(
            self.selection_boundary_margin_hex,
            "brain retrieval selection-boundary margin",
        )
        support_order = tuple(
            (float.fromhex(hit.support_score_hex), hit.record_id)
            for hit in self.considered_hits
        )
        if support_order != tuple(
            sorted(support_order, key=lambda item: (-item[0], item[1]))
        ):
            raise ValueError("brain retrieval hits are not in canonical support order")
        expected_margin = (
            0.0
            if not support_order
            else max(
                0.0,
                support_order[0][0]
                - (support_order[1][0] if len(support_order) > 1 else 0.0),
            )
        )
        if self.selection_boundary_margin_hex != expected_margin.hex():
            raise ValueError("brain retrieval boundary margin differs from its hits")
        if type(self.disposition) is not BrainRetrievalDispositionV3:
            raise ValueError("brain retrieval disposition must be exact")
        if self.abstention_reason is not None and type(self.abstention_reason) is not (
            BrainRetrievalAbstentionV3
        ):
            raise ValueError("brain retrieval abstention reason must be exact")
        if type(self.selected_record_ids) is not tuple or type(
            self.selected_semantic_digests
        ) is not tuple:
            raise ValueError("brain retrieval selections must be exact tuples")
        if self.in_process_execution_identity_attested is not False:
            raise ValueError("brain retrieval has no in-process execution identity authority")
        if self.blinded_relevance_audit_required is not True:
            raise ValueError("brain retrieval proposals require blinded relevance audit")
        if self.solver_dispatch_authorized is not False:
            raise ValueError("an unaudited brain retrieval cannot authorize solver dispatch")
        expected_reason: BrainRetrievalAbstentionV3 | None
        if self.salient_term_count == 0:
            if self.considered_hits:
                raise ValueError("no-salient-terms receipt cannot contain hits")
            expected_reason = BrainRetrievalAbstentionV3.NO_SALIENT_TERMS
        elif self.salient_term_count > 512:
            if self.considered_hits:
                raise ValueError("term-budget receipt cannot contain hits")
            expected_reason = (
                BrainRetrievalAbstentionV3.SALIENT_TERM_BUDGET_EXCEEDED
            )
        elif not self.considered_hits:
            expected_reason = BrainRetrievalAbstentionV3.NO_CANDIDATE_MATCH
        else:
            top = self.considered_hits[0]
            if top.explicit_phrase_overlap_count < 2:
                expected_reason = (
                    BrainRetrievalAbstentionV3.MISSING_EXPLICIT_PHRASE_OVERLAP
                )
            elif float.fromhex(top.support_score_hex) < 2.0:
                expected_reason = BrainRetrievalAbstentionV3.BELOW_SUPPORT_FLOOR
            elif expected_margin < 0.5:
                expected_reason = (
                    BrainRetrievalAbstentionV3.AMBIGUOUS_SELECTION_BOUNDARY
                )
            else:
                expected_reason = None
        expected_disposition = (
            BrainRetrievalDispositionV3.PROPOSAL
            if expected_reason is None
            else BrainRetrievalDispositionV3.ABSTAINED
        )
        if self.disposition is not expected_disposition or (
            self.abstention_reason is not expected_reason
        ):
            raise ValueError("brain retrieval disposition violates the frozen decision tree")
        if expected_disposition is BrainRetrievalDispositionV3.PROPOSAL:
            top = self.considered_hits[0]
            if self.selected_record_ids != (top.record_id,) or (
                self.selected_semantic_digests != (top.semantic_digest,)
            ):
                raise ValueError("brain packet selection differs from the top hit")
        elif self.selected_record_ids or self.selected_semantic_digests:
            raise ValueError("brain retrieval abstention cannot select a record")
        for record_id in self.selected_record_ids:
            if type(record_id) is not str or _METHOD_CARD_ID_RE_V3.fullmatch(
                record_id
            ) is None:
                raise ValueError("brain selected record id is invalid")
        for semantic_digest in self.selected_semantic_digests:
            _validate_digest(semantic_digest, "brain selected semantic digest")

    def _payload(self) -> dict[str, object]:
        return {
            "abstention_reason": (
                None if self.abstention_reason is None else self.abstention_reason.value
            ),
            "case_id": self.case_id,
            "considered_hits": [hit.to_dict() for hit in self.considered_hits],
            "corpus_digest": self.corpus_digest,
            "derived_index_universe_digest": self.derived_index_universe_digest,
            "disposition": self.disposition.value,
            "domain": self.domain.value,
            "policy_digest": self.policy_digest,
            "query_digest": self.query_digest,
            "salient_query_digest": self.salient_query_digest,
            "salient_term_count": self.salient_term_count,
            "selected_record_ids": list(self.selected_record_ids),
            "selected_semantic_digests": list(self.selected_semantic_digests),
            "selection_boundary_margin_hex": self.selection_boundary_margin_hex,
            "in_process_execution_identity_attested": (
                self.in_process_execution_identity_attested
            ),
            "blinded_relevance_audit_required": self.blinded_relevance_audit_required,
            "solver_dispatch_authorized": self.solver_dispatch_authorized,
        }

    def validate_current(self) -> None:
        if type(self) is not BrainRetrievalReceiptV3:
            raise ValueError("brain retrieval receipt subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "brain retrieval receipt digest")
        expected = _digest(self._payload(), domain=_RETRIEVAL_RECEIPT_DOMAIN_V3)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("brain retrieval receipt digest is stale")

    def evaluator_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class BrainPacketCandidateV3:
    """Unreviewed evaluator proposal; never direct solver-dispatch authority."""

    case_id: str
    text: str = field(repr=False)
    corpus_digest: str
    retrieval_policy_digest: str
    query_digest: str
    retrieval_receipt_digest: str
    format_template_digest: str
    section_shape_digest: str
    age_status_vector_digest: str
    source_digests: tuple[str, ...]
    relevance_keys: tuple[str, ...]
    in_process_execution_identity_attested: bool
    blinded_relevance_audit_required: bool
    solver_dispatch_authorized: bool
    packet_id: str = field(init=False)
    content_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self) is not BrainPacketCandidateV3:
            raise ValueError("brain packet candidate subclasses are forbidden")
        self._validate_fields()
        content = _digest(self._payload(), domain=_PACKET_CANDIDATE_DOMAIN_V3)
        object.__setattr__(self, "content_digest", content)
        object.__setattr__(
            self,
            "packet_id",
            "brain-packet-proposal:" + content.removeprefix("sha256:"),
        )

    @property
    def text_digest(self) -> str:
        return _payload_sha256(self.text.encode("utf-8"))

    @property
    def byte_length(self) -> int:
        return len(self.text.encode("utf-8"))

    def _validate_fields(self) -> None:
        if type(self.case_id) is not str or _SAFE_CASE_ID_RE.fullmatch(self.case_id) is None:
            raise ValueError("brain packet candidate case id is invalid")
        _bounded_text(self.text, "brain packet candidate text", maximum=4096)
        if self.text.count("\n") != 1 or "\r" in self.text or "\0" in self.text:
            raise ValueError("brain packet candidate must contain exactly two lines")
        for label, value in (
            ("corpus digest", self.corpus_digest),
            ("retrieval policy digest", self.retrieval_policy_digest),
            ("query digest", self.query_digest),
            ("retrieval receipt digest", self.retrieval_receipt_digest),
            ("format template digest", self.format_template_digest),
            ("section-shape digest", self.section_shape_digest),
            ("age/status-vector digest", self.age_status_vector_digest),
        ):
            _validate_digest(value, label)
        if self.format_template_digest != BRAIN_PACKET_FORMAT_SHA256_V3:
            raise ValueError("brain packet format differs from the frozen renderer")
        if self.section_shape_digest != BRAIN_PACKET_SECTION_SHAPE_SHA256_V3:
            raise ValueError("brain packet section shape differs from the frozen renderer")
        if self.age_status_vector_digest != BRAIN_PACKET_STATUS_VECTOR_SHA256_V3:
            raise ValueError("brain packet status vector differs from the frozen renderer")
        if type(self.source_digests) is not tuple or len(self.source_digests) != 1:
            raise ValueError("brain packet candidate requires exactly one source digest")
        if tuple(sorted(set(self.source_digests))) != self.source_digests:
            raise ValueError("brain packet source digests must be sorted and unique")
        _validate_digest(self.source_digests[0], "brain packet source digest")
        if type(self.relevance_keys) is not tuple or len(self.relevance_keys) != 1:
            raise ValueError("brain packet candidate requires exactly one relevance key")
        if tuple(sorted(set(self.relevance_keys))) != self.relevance_keys:
            raise ValueError("brain packet relevance keys must be sorted and unique")
        if _SAFE_FAMILY_RE.fullmatch(self.relevance_keys[0]) is None:
            raise ValueError("brain packet relevance key is invalid")
        if self.in_process_execution_identity_attested is not False:
            raise ValueError("brain packet proposal has no in-process code identity authority")
        if self.blinded_relevance_audit_required is not True:
            raise ValueError("brain packet proposal requires blinded relevance audit")
        if self.solver_dispatch_authorized is not False:
            raise ValueError("an unaudited brain packet cannot authorize solver dispatch")

    def _payload(self) -> dict[str, object]:
        return {
            "age_status_vector_digest": self.age_status_vector_digest,
            "byte_length": self.byte_length,
            "case_id": self.case_id,
            "corpus_digest": self.corpus_digest,
            "format_template_digest": self.format_template_digest,
            "in_process_execution_identity_attested": (
                self.in_process_execution_identity_attested
            ),
            "query_digest": self.query_digest,
            "relevance_keys": list(self.relevance_keys),
            "blinded_relevance_audit_required": self.blinded_relevance_audit_required,
            "retrieval_policy_digest": self.retrieval_policy_digest,
            "retrieval_receipt_digest": self.retrieval_receipt_digest,
            "section_shape_digest": self.section_shape_digest,
            "source_digests": list(self.source_digests),
            "solver_dispatch_authorized": self.solver_dispatch_authorized,
            "text_digest": self.text_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not BrainPacketCandidateV3:
            raise ValueError("brain packet candidate subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "brain packet candidate digest")
        expected = _digest(self._payload(), domain=_PACKET_CANDIDATE_DOMAIN_V3)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("brain packet candidate digest is stale")
        expected_id = "brain-packet-proposal:" + expected.removeprefix("sha256:")
        if self.packet_id != expected_id:
            raise ValueError("brain packet candidate id is stale")

    def commitment_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {
            "content_digest": self.content_digest,
            "packet_id": self.packet_id,
        }


def _brain_hit_receipt_v3(
    *,
    rank: int,
    hit: object,
    card: BrainMethodCardV3,
    query_terms: tuple[str, ...],
    policy: BrainRetrievalPolicyV3,
) -> BrainHitReceiptV3:
    card.validate_current()
    node = getattr(hit, "node", None)
    if getattr(node, "id", None) != card.record_id:
        raise ValueError("brain retrieval hit does not bind the expected method card")
    component_names = (
        "lexical_score",
        "symbol_score",
        "formula_score",
        "graph_score",
        "warning_score",
    )
    components: list[float] = []
    for name in component_names:
        value = getattr(hit, name, None)
        if type(value) is not float or not math.isfinite(value) or value < 0:
            raise ValueError("brain retrieval returned a malformed score component")
        components.append(value)
    if any(value != 0.0 for value in components[1:]):
        raise ValueError("brain v3 salient retrieval unexpectedly used a nonlexical channel")
    support_score = getattr(hit, "support_score", None)
    total_score = getattr(hit, "total_score", None)
    if type(support_score) is not float or type(total_score) is not float:
        raise ValueError("brain retrieval returned malformed aggregate scores")
    if support_score.hex() != sum(components[:4]).hex() or (
        total_score.hex() != sum(components).hex()
    ):
        raise ValueError("brain retrieval aggregate scores differ from their components")
    explicit_overlap = _best_explicit_phrase_overlap_v3(query_terms, card, policy)
    return BrainHitReceiptV3(
        rank=rank,
        record_id=card.record_id,
        semantic_digest=card.semantic_digest,
        lexical_score_hex=components[0].hex(),
        symbol_score_hex=components[1].hex(),
        formula_score_hex=components[2].hex(),
        graph_score_hex=components[3].hex(),
        warning_score_hex=components[4].hex(),
        support_score_hex=support_score.hex(),
        total_score_hex=total_score.hex(),
        explicit_phrase_overlap_count=len(explicit_overlap),
        explicit_phrase_overlap_digest=_digest(
            list(explicit_overlap),
            domain=_OVERLAP_DOMAIN_V3,
        ),
        score_trace_digest=_score_trace_digest_v3(getattr(hit, "trace", ())),
    )


def _retrieval_receipt_v3(
    *,
    query: SolverVisiblePacketQueryV3,
    corpus: BrainCorpusManifestV3,
    policy: BrainRetrievalPolicyV3,
    derived_index_universe_digest: str,
    salient_terms: tuple[str, ...],
    hits: tuple[BrainHitReceiptV3, ...],
    disposition: BrainRetrievalDispositionV3,
    abstention_reason: BrainRetrievalAbstentionV3 | None,
    boundary_margin: float,
    selected_card: BrainMethodCardV3 | None,
) -> BrainRetrievalReceiptV3:
    if type(boundary_margin) is not float or not math.isfinite(boundary_margin):
        raise ValueError("brain retrieval boundary margin is malformed")
    return BrainRetrievalReceiptV3(
        case_id=query.case_id,
        domain=query.domain,
        query_digest=query.content_digest,
        salient_query_digest=_digest(
            list(salient_terms),
            domain=_SALIENT_QUERY_DOMAIN_V3,
        ),
        salient_term_count=len(salient_terms),
        corpus_digest=corpus.content_digest,
        policy_digest=policy.content_digest,
        derived_index_universe_digest=derived_index_universe_digest,
        considered_hits=hits,
        disposition=disposition,
        abstention_reason=abstention_reason,
        selection_boundary_margin_hex=boundary_margin.hex(),
        selected_record_ids=(
            () if selected_card is None else (selected_card.record_id,)
        ),
        selected_semantic_digests=(
            () if selected_card is None else (selected_card.semantic_digest,)
        ),
        in_process_execution_identity_attested=False,
        blinded_relevance_audit_required=True,
        solver_dispatch_authorized=False,
    )


def _render_method_card_v3(card: BrainMethodCardV3) -> str:
    card.validate_current()
    rendered = card.title + "\n" + card.guidance
    if rendered.count("\n") != 1:
        raise ValueError("brain packet renderer produced a malformed section shape")
    return rendered


def retrieve_brain_packet_candidate_v3(
    corpus: BrainCorpusManifestV3,
    policy: BrainRetrievalPolicyV3,
    query: SolverVisiblePacketQueryV3,
) -> tuple[BrainRetrievalReceiptV3, BrainPacketCandidateV3 | None]:
    """Construct one receipt and an optional unreviewed two-line routing proposal."""

    if type(corpus) is not BrainCorpusManifestV3:
        raise ValueError("brain packet retrieval requires an exact corpus")
    if type(policy) is not BrainRetrievalPolicyV3:
        raise ValueError("brain packet retrieval requires an exact policy")
    if type(query) is not SolverVisiblePacketQueryV3:
        raise ValueError("brain packet retrieval requires an exact solver-visible query")
    corpus.validate_current()
    policy.validate_current()
    query.validate_current()
    nodes = _graph_nodes_v3(corpus, policy)
    if len(nodes) != BRAIN_CARD_COUNT_V3 or len({node.id for node in nodes}) != len(nodes):
        raise ValueError("brain packet retrieval did not build the exact node universe")
    derived_index_universe_digest = _derived_index_universe_digest_v3(nodes)
    if not hmac.compare_digest(
        derived_index_universe_digest,
        BRAIN_DERIVED_INDEX_UNIVERSE_SHA256_V3,
    ):
        raise ValueError("brain derived index universe differs from the frozen commitment")

    salient_terms = _salient_terms_v3(query.problem_text, policy)
    if not salient_terms:
        receipt = _retrieval_receipt_v3(
            query=query,
            corpus=corpus,
            policy=policy,
            derived_index_universe_digest=derived_index_universe_digest,
            salient_terms=salient_terms,
            hits=(),
            disposition=BrainRetrievalDispositionV3.ABSTAINED,
            abstention_reason=BrainRetrievalAbstentionV3.NO_SALIENT_TERMS,
            boundary_margin=0.0,
            selected_card=None,
        )
        return receipt, None
    if len(salient_terms) > policy.maximum_salient_terms:
        receipt = _retrieval_receipt_v3(
            query=query,
            corpus=corpus,
            policy=policy,
            derived_index_universe_digest=derived_index_universe_digest,
            salient_terms=salient_terms,
            hits=(),
            disposition=BrainRetrievalDispositionV3.ABSTAINED,
            abstention_reason=(
                BrainRetrievalAbstentionV3.SALIENT_TERM_BUDGET_EXCEEDED
            ),
            boundary_margin=0.0,
            selected_card=None,
        )
        return receipt, None

    guard = RetrievalGuard(
        evaluation_split="study_v3_evaluation",
        allowed_index_splits=("reference",),
        active_item_id=query.case_id,
        active_source_digest=query.content_digest,
    )
    result = retrieve(
        " ".join(salient_terms),
        nodes=nodes,
        edges=(),
        limit=policy.considered_limit,
        seed_limit=policy.considered_limit,
        graph_depth=policy.graph_depth,
        decay=0.0,
        min_support_score=0.0,
        min_margin=0.0,
        guard=guard,
    )
    if result.exclusions:
        raise ValueError("brain packet retrieval guard excluded a frozen card")
    ranked_hits = tuple(
        sorted(
            result.hits,
            key=lambda item: (-item.support_score, item.node.id),
        )[: policy.considered_limit]
    )
    card_by_id = {card.record_id: card for card in corpus.cards}
    hit_receipts = tuple(
        _brain_hit_receipt_v3(
            rank=rank,
            hit=hit,
            card=card_by_id[hit.node.id],
            query_terms=salient_terms,
            policy=policy,
        )
        for rank, hit in enumerate(ranked_hits, start=1)
    )
    if not hit_receipts:
        receipt = _retrieval_receipt_v3(
            query=query,
            corpus=corpus,
            policy=policy,
            derived_index_universe_digest=derived_index_universe_digest,
            salient_terms=salient_terms,
            hits=(),
            disposition=BrainRetrievalDispositionV3.ABSTAINED,
            abstention_reason=BrainRetrievalAbstentionV3.NO_CANDIDATE_MATCH,
            boundary_margin=0.0,
            selected_card=None,
        )
        return receipt, None

    top = hit_receipts[0]
    top_support = float.fromhex(top.support_score_hex)
    runner_up_support = (
        float.fromhex(hit_receipts[1].support_score_hex)
        if len(hit_receipts) > 1
        else 0.0
    )
    boundary_margin = max(0.0, top_support - runner_up_support)
    abstention_reason: BrainRetrievalAbstentionV3 | None = None
    if top.explicit_phrase_overlap_count < policy.minimum_explicit_overlap_terms:
        abstention_reason = (
            BrainRetrievalAbstentionV3.MISSING_EXPLICIT_PHRASE_OVERLAP
        )
    elif top_support < float.fromhex(policy.support_floor_hex):
        abstention_reason = BrainRetrievalAbstentionV3.BELOW_SUPPORT_FLOOR
    elif boundary_margin < float.fromhex(policy.selection_boundary_margin_hex):
        abstention_reason = (
            BrainRetrievalAbstentionV3.AMBIGUOUS_SELECTION_BOUNDARY
        )
    if abstention_reason is not None:
        receipt = _retrieval_receipt_v3(
            query=query,
            corpus=corpus,
            policy=policy,
            derived_index_universe_digest=derived_index_universe_digest,
            salient_terms=salient_terms,
            hits=hit_receipts,
            disposition=BrainRetrievalDispositionV3.ABSTAINED,
            abstention_reason=abstention_reason,
            boundary_margin=boundary_margin,
            selected_card=None,
        )
        return receipt, None

    selected_card = card_by_id[top.record_id]
    receipt = _retrieval_receipt_v3(
        query=query,
        corpus=corpus,
        policy=policy,
        derived_index_universe_digest=derived_index_universe_digest,
        salient_terms=salient_terms,
        hits=hit_receipts,
        disposition=BrainRetrievalDispositionV3.PROPOSAL,
        abstention_reason=None,
        boundary_margin=boundary_margin,
        selected_card=selected_card,
    )
    candidate = BrainPacketCandidateV3(
        case_id=query.case_id,
        text=_render_method_card_v3(selected_card),
        corpus_digest=corpus.content_digest,
        retrieval_policy_digest=policy.content_digest,
        query_digest=query.content_digest,
        retrieval_receipt_digest=receipt.content_digest,
        format_template_digest=BRAIN_PACKET_FORMAT_SHA256_V3,
        section_shape_digest=BRAIN_PACKET_SECTION_SHAPE_SHA256_V3,
        age_status_vector_digest=BRAIN_PACKET_STATUS_VECTOR_SHA256_V3,
        source_digests=(selected_card.semantic_digest,),
        relevance_keys=(selected_card.family,),
        in_process_execution_identity_attested=False,
        blinded_relevance_audit_required=True,
        solver_dispatch_authorized=False,
    )
    return receipt, candidate


def render_solver_visible_brain_packet_v3(candidate: BrainPacketCandidateV3) -> str:
    """Fail closed until a separate blinded audit and signed preparation manifest exist."""

    if type(candidate) is not BrainPacketCandidateV3:
        raise ValueError("solver-visible renderer requires an exact packet candidate")
    candidate.validate_current()
    raise ValueError("an unreviewed brain packet proposal is not solver-dispatch authorized")


def recompute_brain_packet_candidate_v3(
    corpus: BrainCorpusManifestV3,
    policy: BrainRetrievalPolicyV3,
    query: SolverVisiblePacketQueryV3,
    receipt: BrainRetrievalReceiptV3,
    candidate: BrainPacketCandidateV3 | None,
) -> bool:
    """Check deterministic recomputation; this deliberately supplies no chronology."""

    if type(receipt) is not BrainRetrievalReceiptV3:
        raise ValueError("brain packet recomputation requires an exact receipt")
    if candidate is not None and type(candidate) is not BrainPacketCandidateV3:
        raise ValueError("brain packet recomputation requires an exact proposal or abstention")
    receipt.validate_current()
    if candidate is not None:
        candidate.validate_current()
    recomputed_receipt, recomputed_candidate = retrieve_brain_packet_candidate_v3(
        corpus,
        policy,
        query,
    )
    if not hmac.compare_digest(
        receipt.content_digest,
        recomputed_receipt.content_digest,
    ):
        raise ValueError("brain retrieval receipt fails deterministic recomputation")
    if (candidate is None) is not (recomputed_candidate is None):
        raise ValueError("brain packet proposal disposition fails recomputation")
    if candidate is not None and recomputed_candidate is not None and (
        not hmac.compare_digest(
            candidate.content_digest,
            recomputed_candidate.content_digest,
        )
    ):
        raise ValueError("brain packet proposal fails deterministic recomputation")
    return True
