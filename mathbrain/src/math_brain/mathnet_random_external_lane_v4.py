"""Causal MathNet-random external lane for the engineering three-arm pilot.

This lane is intentionally separate from the failed body/card reviewer lane.  It never
accepts a body/card artifact, reviewer decision, replacement, top-up, or caller-provided
selection seed.  A precommitment fixes the exact pinned MathNet source, the exact selector
code bytes, one official ``MathNetBankV3`` selection of twelve source clusters, and one
future drand Quicknet round.  The only seed is derived from that round's randomness.

Cold replay requires canonical, externally committed precommitment and beacon-receipt
bytes.  It rebuilds and then independently replays the official pinned bank before minting
the nominal selection capability.  The capability and its gold-audit projection contain
problem bodies and commitments, but no source answer, solution, path, reviewer label,
domain label, or difficulty label.  A deterministic private vault record is committed and
discarded; it can be reproduced only by another complete official-source replay.

The receipt checks two fixed HTTPS relay payloads agree and verifies drand's
``randomness = SHA256(signature)`` relation.  This module deliberately does *not* implement
BLS verification or prove relay origin, wall-clock chronology, Git-object association, or
independence.  Those authority flags remain false, so this is engineering evidence only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .exact_answer_v3 import ExactRationalV3
from .mathnet_acquisition_v3 import (
    MATHNET_REPOSITORY_V3,
    MATHNET_REVISION_V3,
    MATHNET_ROW_ADAPTER_DIGEST_V3,
    PreparedPinnedMathNetSourceV3,
    VerifiedPinnedMathNetBankV3,
    build_pinned_mathnet_bank_v3,
    verify_pinned_mathnet_bank_v3,
)
from .mathnet_bank_v3 import (
    MAX_PROMPT_BYTES_V3,
    MathNetBankV3,
    MathNetCandidateV3,
    MathNetPrivateGoldV3,
    MathNetSourceAuthorityV3,
)
from .models import canonical_json

MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4 = 1
MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4 = 12
MATHNET_RANDOM_EXTERNAL_LANE_ID_V4 = (
    "mathnet-random-autoretrieval-post-body-card-failure-v4"
)
MATHNET_RANDOM_EXTERNAL_SELECTION_POLICY_V4 = (
    "mathnet-bank-v3-one-per-source-cluster-ticket-rank-sample-12"
)
MATHNET_RANDOM_EXTERNAL_PRECOMMITMENT_SCHEMA_V4 = (
    "org.math-research-brain/mathnet-random-external-precommitment"
)
MATHNET_RANDOM_EXTERNAL_BEACON_RECEIPT_SCHEMA_V4 = (
    "org.math-research-brain/mathnet-random-external-beacon-receipt"
)
MATHNET_RANDOM_EXTERNAL_GOLD_INPUT_SCHEMA_V4 = (
    "org.math-research-brain/mathnet-random-external-gold-input"
)
MATHNET_RANDOM_EXTERNAL_PRIVATE_VAULT_SCHEMA_V4 = (
    "org.math-research-brain/mathnet-random-external-private-gold-vault"
)

DRAND_QUICKNET_CHAIN_HASH_V4 = (
    "52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971"
)
DRAND_QUICKNET_PUBLIC_KEY_V4 = (
    "83cf0f2896adee7eb8b5f01fcad3912212c437e0073e911fb90022d3e760183c"
    "8c4b450b6a0a6c3ac6a5776a2d1064510d1fec758c921cc22b0e17e63aaf4bc"
    "b5ed66304de9cf809bd274ca73bab4af5a6e9c76a4bc09e76eae8991ef5ece45a"
)
DRAND_QUICKNET_GENESIS_SEED_V4 = (
    "f477d5c89f21a17c863a7f937c6a6d15859414d2be09cd448d4279af331c5d3e"
)
DRAND_QUICKNET_GENESIS_TIME_V4 = 1_692_803_367
DRAND_QUICKNET_PERIOD_SECONDS_V4 = 3
DRAND_QUICKNET_SCHEME_V4 = "bls-unchained-g1-rfc9380"
DRAND_QUICKNET_BEACON_ID_V4 = "quicknet"
DRAND_QUICKNET_RELAY_BASE_URLS_V4 = (
    "https://api.drand.sh",
    "https://api2.drand.sh",
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_HEX_32_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_HEX_G1_SIGNATURE_RE = re.compile(r"[0-9a-f]{96}\Z", re.ASCII)
_CASE_ID_RE = re.compile(r"mathnet-random-v4:[0-9a-f]{64}\Z", re.ASCII)
_IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z", re.ASCII)
_UTC_RE = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
_MAX_RECORD_BYTES_V4 = 2 * 1024 * 1024
_MAX_RELAY_RESPONSE_BYTES_V4 = 256 * 1024
_MAX_ANCHOR_EVIDENCE_BYTES_V4 = 2 * 1024 * 1024

_CODE_FILE_LABELS_V4 = (
    "src/math_brain/mathnet_acquisition_v3.py",
    "src/math_brain/mathnet_bank_v3.py",
    "src/math_brain/exact_answer_v3.py",
    "src/math_brain/models.py",
    "src/math_brain/mathnet_random_external_lane_v4.py",
)
_LEGACY_BODY_CARD_MODULES_V4 = frozenset(
    {
        "math_brain.external_blind_gold_audit_v3",
        "math_brain.mathnet_body_card_review_signed_pair_v3",
        "math_brain.mathnet_body_card_review_v3",
    }
)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _domain_digest(value: object, *, domain: str) -> str:
    material = domain.encode("ascii") + b"\x00" + canonical_json(value).encode("utf-8")
    return _sha256(material)


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _strict_text(value: object, label: str, *, maximum: int = 2048) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{label} must be bounded exact text")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{label} contains a control character")
    return value


def _strict_utc(value: object, label: str) -> datetime:
    if type(value) is not str or _UTC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be canonical UTC seconds")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"{label} is not a real UTC timestamp") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError(f"{label} is not canonical UTC")
    return parsed


def _exact_mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys or any(type(key) is not str for key in value):
        raise ValueError(f"{label} has unexpected fields")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("MathNet random external JSON contains a duplicate key")
        result[key] = value
    return result


def _strict_json(raw: object, *, label: str, canonical: bool) -> object:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_RECORD_BYTES_V4:
        raise ValueError(f"{label} must be bounded immutable bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value is forbidden: {item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is not strict JSON") from exc
    if canonical and raw != canonical_json(value).encode("utf-8"):
        raise ValueError(f"{label} must use canonical encoding")
    return value


def _record_sha256(raw: object, expected: object, label: str) -> str:
    if type(raw) is not bytes:
        raise ValueError(f"{label} must be immutable bytes")
    expected_digest = _digest(expected, f"expected {label}")
    observed = _sha256(raw)
    if not hmac.compare_digest(observed, expected_digest):
        raise ValueError(f"{label} differs from its external commitment")
    return observed


def _reject_legacy_body_card_artifact(value: object, label: str) -> None:
    if type(value).__module__ in _LEGACY_BODY_CARD_MODULES_V4:
        raise ValueError(
            f"{label} rejects failed body/card and reviewer artifacts; this is a distinct lane"
        )


def _module_paths_v4() -> tuple[tuple[str, Path], ...]:
    module_dir = Path(__file__).resolve().parent
    return (
        (_CODE_FILE_LABELS_V4[0], module_dir / "mathnet_acquisition_v3.py"),
        (_CODE_FILE_LABELS_V4[1], module_dir / "mathnet_bank_v3.py"),
        (_CODE_FILE_LABELS_V4[2], module_dir / "exact_answer_v3.py"),
        (_CODE_FILE_LABELS_V4[3], module_dir / "models.py"),
        (_CODE_FILE_LABELS_V4[4], module_dir / "mathnet_random_external_lane_v4.py"),
    )


@dataclass(frozen=True, slots=True)
class MathNetRandomSelectorCodeManifestV4:
    """Exact local source-byte commitment; Git-object association remains unverified."""

    protocol_git_commit: str
    module_sha256s: tuple[tuple[str, str], ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(
                self._payload(),
                domain="org.math-research-brain/mathnet-random-selector-code/v4",
            ),
        )

    def _validate_fields(self) -> None:
        if type(self) is not MathNetRandomSelectorCodeManifestV4:
            raise ValueError("MathNet random selector code-manifest subclasses are forbidden")
        if type(self.protocol_git_commit) is not str or _COMMIT_RE.fullmatch(
            self.protocol_git_commit
        ) is None:
            raise ValueError("selector protocol commit must be a full lowercase Git object id")
        if type(self.module_sha256s) is not tuple or len(self.module_sha256s) != len(
            _CODE_FILE_LABELS_V4
        ):
            raise ValueError("selector code manifest must bind every exact module")
        observed_labels: list[str] = []
        for item in self.module_sha256s:
            if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
                raise ValueError("selector code manifest entries must be exact pairs")
            observed_labels.append(item[0])
            _digest(item[1], "selector module bytes")
        if tuple(observed_labels) != _CODE_FILE_LABELS_V4:
            raise ValueError("selector code manifest module order or identity differs")

    def _payload(self) -> dict[str, object]:
        return {
            "git_object_association_verified": False,
            "module_sha256s": [list(item) for item in self.module_sha256s],
            "protocol_git_commit": self.protocol_git_commit,
        }

    def validate_current(self) -> None:
        self._validate_fields()
        expected = _domain_digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-random-selector-code/v4",
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("selector code manifest digest is stale")

    def validate_current_files(self) -> None:
        self.validate_current()
        observed = tuple((label, _sha256(path.read_bytes())) for label, path in _module_paths_v4())
        if observed != self.module_sha256s:
            raise ValueError("current selector source bytes differ from the precommitment")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}

    @classmethod
    def from_dict(cls, value: object) -> MathNetRandomSelectorCodeManifestV4:
        raw = _exact_mapping(
            value,
            {
                "content_digest",
                "git_object_association_verified",
                "module_sha256s",
                "protocol_git_commit",
            },
            "selector code manifest",
        )
        if raw["git_object_association_verified"] is not False:
            raise ValueError("Git-object association is not established by protocol v4")
        entries = raw["module_sha256s"]
        if type(entries) is not list:
            raise ValueError("selector module commitments must be an exact list")
        parsed = cls(
            protocol_git_commit=raw["protocol_git_commit"],
            module_sha256s=tuple(
                tuple(item) if type(item) is list else ()  # type: ignore[arg-type]
                for item in entries
            ),
        )
        if not hmac.compare_digest(
            parsed.content_digest,
            _digest(raw["content_digest"], "selector code manifest"),
        ):
            raise ValueError("selector code manifest content digest differs")
        return parsed


def freeze_current_mathnet_random_selector_code_v4(
    *, protocol_git_commit: str
) -> MathNetRandomSelectorCodeManifestV4:
    return MathNetRandomSelectorCodeManifestV4(
        protocol_git_commit=protocol_git_commit,
        module_sha256s=tuple(
            (label, _sha256(path.read_bytes())) for label, path in _module_paths_v4()
        ),
    )


@dataclass(frozen=True, slots=True)
class DrandQuicknetRoundSpecV4:
    """One exact future Quicknet round and all consensus parameters needed to identify it."""

    round_number: int
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(
                self._payload(),
                domain="org.math-research-brain/drand-quicknet-round-spec/v4",
            ),
        )

    @property
    def scheduled_unix(self) -> int:
        return DRAND_QUICKNET_GENESIS_TIME_V4 + (
            (self.round_number - 1) * DRAND_QUICKNET_PERIOD_SECONDS_V4
        )

    @property
    def scheduled_utc(self) -> str:
        return datetime.fromtimestamp(self.scheduled_unix, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    @property
    def relay_urls(self) -> tuple[str, ...]:
        return tuple(
            f"{base}/v2/chains/{DRAND_QUICKNET_CHAIN_HASH_V4}/rounds/{self.round_number}"
            for base in DRAND_QUICKNET_RELAY_BASE_URLS_V4
        )

    def _validate_fields(self) -> None:
        if type(self) is not DrandQuicknetRoundSpecV4:
            raise ValueError("drand round-spec subclasses are forbidden")
        if type(self.round_number) is not int or not 1 <= self.round_number <= 2**63 - 1:
            raise ValueError("drand round number must be an exact positive integer")

    def _payload(self) -> dict[str, object]:
        return {
            "beacon_id": DRAND_QUICKNET_BEACON_ID_V4,
            "chain_hash": DRAND_QUICKNET_CHAIN_HASH_V4,
            "genesis_seed": DRAND_QUICKNET_GENESIS_SEED_V4,
            "genesis_time": DRAND_QUICKNET_GENESIS_TIME_V4,
            "period_seconds": DRAND_QUICKNET_PERIOD_SECONDS_V4,
            "public_key": DRAND_QUICKNET_PUBLIC_KEY_V4,
            "relay_urls": list(self.relay_urls),
            "round_number": self.round_number,
            "scheduled_unix": self.scheduled_unix,
            "scheduled_utc": self.scheduled_utc,
            "scheme": DRAND_QUICKNET_SCHEME_V4,
        }

    def validate_current(self) -> None:
        self._validate_fields()
        expected = _domain_digest(
            self._payload(),
            domain="org.math-research-brain/drand-quicknet-round-spec/v4",
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("drand round-spec digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}

    @classmethod
    def from_dict(cls, value: object) -> DrandQuicknetRoundSpecV4:
        raw = _exact_mapping(
            value,
            {
                "beacon_id",
                "chain_hash",
                "content_digest",
                "genesis_seed",
                "genesis_time",
                "period_seconds",
                "public_key",
                "relay_urls",
                "round_number",
                "scheduled_unix",
                "scheduled_utc",
                "scheme",
            },
            "drand round spec",
        )
        parsed = cls(round_number=raw["round_number"])
        expected = parsed.to_dict()
        if raw != expected:
            raise ValueError("drand round spec differs from frozen Quicknet parameters")
        return parsed


_PRECOMMITMENT_TOKEN_V4 = object()


@dataclass(frozen=True, slots=True, init=False)
class MathNetRandomExternalPrecommitmentV4:
    """Canonical pre-beacon commitment for one, and only one, twelve-case selection."""

    claimed_created_utc: str
    source_manifest_digest: str
    source_runtime_evidence_digest: str
    selector_code_manifest: MathNetRandomSelectorCodeManifestV4
    beacon_round_spec: DrandQuicknetRoundSpecV4
    content_digest: str
    _mint_marker: object = field(repr=False, compare=False)

    def __new__(cls, token: object = None) -> MathNetRandomExternalPrecommitmentV4:
        if token is not _PRECOMMITMENT_TOKEN_V4:
            raise TypeError("MathNet random precommitments require the typed builder or parser")
        return object.__new__(cls)

    @classmethod
    def _derive(
        cls,
        *,
        claimed_created_utc: str,
        source_manifest_digest: str,
        source_runtime_evidence_digest: str,
        selector_code_manifest: MathNetRandomSelectorCodeManifestV4,
        beacon_round_spec: DrandQuicknetRoundSpecV4,
    ) -> MathNetRandomExternalPrecommitmentV4:
        instance = cls(_PRECOMMITMENT_TOKEN_V4)
        object.__setattr__(instance, "claimed_created_utc", claimed_created_utc)
        object.__setattr__(instance, "source_manifest_digest", source_manifest_digest)
        object.__setattr__(
            instance,
            "source_runtime_evidence_digest",
            source_runtime_evidence_digest,
        )
        object.__setattr__(instance, "selector_code_manifest", selector_code_manifest)
        object.__setattr__(instance, "beacon_round_spec", beacon_round_spec)
        object.__setattr__(instance, "_mint_marker", _PRECOMMITMENT_TOKEN_V4)
        instance._validate_fields()
        object.__setattr__(
            instance,
            "content_digest",
            _domain_digest(
                instance._payload(),
                domain="org.math-research-brain/mathnet-random-external-precommitment/v4",
            ),
        )
        return instance

    def _payload(self) -> dict[str, object]:
        return {
            "authority": {
                "beacon_bls_signature_verified": False,
                "chronology_verified": False,
                "git_object_association_verified": False,
                "independence_verified": False,
                "model_identity_verified": False,
            },
            "beacon_round_spec": self.beacon_round_spec.to_dict(),
            "claimed_created_utc": self.claimed_created_utc,
            "constraints": {
                "failed_body_card_artifact_consumption_allowed": False,
                "replacement_budget": 0,
                "reroll_budget": 0,
                "reselection_budget": 0,
                "selection_attempt_limit": 1,
                "topup_budget": 0,
            },
            "engineering_only": True,
            "lane_id": MATHNET_RANDOM_EXTERNAL_LANE_ID_V4,
            "labels": {
                "domain_labels_present": False,
                "difficulty_labels_present": False,
            },
            "sample_size": MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4,
            "schema": MATHNET_RANDOM_EXTERNAL_PRECOMMITMENT_SCHEMA_V4,
            "schema_version": MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4,
            "selection_policy": MATHNET_RANDOM_EXTERNAL_SELECTION_POLICY_V4,
            "selector_code_manifest": self.selector_code_manifest.to_dict(),
            "source": {
                "manifest_digest": self.source_manifest_digest,
                "repository_id": MATHNET_REPOSITORY_V3,
                "revision": MATHNET_REVISION_V3,
                "row_adapter_digest": MATHNET_ROW_ADAPTER_DIGEST_V3,
                "runtime_evidence_digest": self.source_runtime_evidence_digest,
            },
        }

    def _validate_fields(self) -> None:
        if type(self) is not MathNetRandomExternalPrecommitmentV4 or self._mint_marker is not (
            _PRECOMMITMENT_TOKEN_V4
        ):
            raise ValueError("MathNet random precommitment is not nominal")
        created = _strict_utc(self.claimed_created_utc, "precommitment claimed creation")
        _digest(self.source_manifest_digest, "precommitment source manifest")
        _digest(self.source_runtime_evidence_digest, "precommitment source runtime")
        if type(self.selector_code_manifest) is not MathNetRandomSelectorCodeManifestV4:
            raise ValueError("precommitment requires the exact selector code manifest")
        self.selector_code_manifest.validate_current()
        if type(self.beacon_round_spec) is not DrandQuicknetRoundSpecV4:
            raise ValueError("precommitment requires the exact Quicknet round spec")
        self.beacon_round_spec.validate_current()
        scheduled = datetime.fromtimestamp(self.beacon_round_spec.scheduled_unix, tz=UTC)
        if created >= scheduled:
            raise ValueError("precommitment's claimed creation must precede the beacon round")

    def validate_current(self) -> None:
        self._validate_fields()
        expected = _domain_digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-random-external-precommitment/v4",
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("MathNet random precommitment digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}

    def canonical_record(self) -> bytes:
        return canonical_json(self.to_dict()).encode("utf-8")

    @property
    def record_sha256(self) -> str:
        return _sha256(self.canonical_record())

    @classmethod
    def from_canonical_record(cls, raw: bytes) -> MathNetRandomExternalPrecommitmentV4:
        value = _strict_json(raw, label="MathNet random precommitment", canonical=True)
        parsed = _exact_mapping(
            value,
            {
                "authority",
                "beacon_round_spec",
                "claimed_created_utc",
                "constraints",
                "content_digest",
                "engineering_only",
                "labels",
                "lane_id",
                "sample_size",
                "schema",
                "schema_version",
                "selection_policy",
                "selector_code_manifest",
                "source",
            },
            "MathNet random precommitment",
        )
        authority = _exact_mapping(
            parsed["authority"],
            {
                "beacon_bls_signature_verified",
                "chronology_verified",
                "git_object_association_verified",
                "independence_verified",
                "model_identity_verified",
            },
            "precommitment authority",
        )
        if any(value is not False for value in authority.values()):
            raise ValueError("precommitment cannot claim unverified authority")
        constraints = _exact_mapping(
            parsed["constraints"],
            {
                "failed_body_card_artifact_consumption_allowed",
                "replacement_budget",
                "reroll_budget",
                "reselection_budget",
                "selection_attempt_limit",
                "topup_budget",
            },
            "precommitment constraints",
        )
        if constraints != {
            "failed_body_card_artifact_consumption_allowed": False,
            "replacement_budget": 0,
            "reroll_budget": 0,
            "reselection_budget": 0,
            "selection_attempt_limit": 1,
            "topup_budget": 0,
        }:
            raise ValueError("precommitment permits a reroll, top-up, reselection, or replacement")
        labels = _exact_mapping(
            parsed["labels"],
            {"domain_labels_present", "difficulty_labels_present"},
            "precommitment labels",
        )
        if labels != {"domain_labels_present": False, "difficulty_labels_present": False}:
            raise ValueError("random external lane cannot invent domain or difficulty labels")
        source = _exact_mapping(
            parsed["source"],
            {
                "manifest_digest",
                "repository_id",
                "revision",
                "row_adapter_digest",
                "runtime_evidence_digest",
            },
            "precommitment source",
        )
        if (
            parsed["engineering_only"] is not True
            or parsed["lane_id"] != MATHNET_RANDOM_EXTERNAL_LANE_ID_V4
            or parsed["sample_size"] != MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4
            or parsed["schema"] != MATHNET_RANDOM_EXTERNAL_PRECOMMITMENT_SCHEMA_V4
            or parsed["schema_version"] != MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4
            or parsed["selection_policy"] != MATHNET_RANDOM_EXTERNAL_SELECTION_POLICY_V4
            or source["repository_id"] != MATHNET_REPOSITORY_V3
            or source["revision"] != MATHNET_REVISION_V3
            or source["row_adapter_digest"] != MATHNET_ROW_ADAPTER_DIGEST_V3
        ):
            raise ValueError("precommitment changes the frozen lane identity or source")
        instance = cls._derive(
            claimed_created_utc=parsed["claimed_created_utc"],
            source_manifest_digest=source["manifest_digest"],
            source_runtime_evidence_digest=source["runtime_evidence_digest"],
            selector_code_manifest=MathNetRandomSelectorCodeManifestV4.from_dict(
                parsed["selector_code_manifest"]
            ),
            beacon_round_spec=DrandQuicknetRoundSpecV4.from_dict(
                parsed["beacon_round_spec"]
            ),
        )
        if not hmac.compare_digest(
            instance.content_digest,
            _digest(parsed["content_digest"], "precommitment content"),
        ):
            raise ValueError("precommitment content digest differs")
        return instance


def build_mathnet_random_external_precommitment_v4(
    source: PreparedPinnedMathNetSourceV3,
    *,
    claimed_created_utc: str,
    selector_code_manifest: MathNetRandomSelectorCodeManifestV4,
    beacon_round_spec: DrandQuicknetRoundSpecV4,
) -> MathNetRandomExternalPrecommitmentV4:
    for value, label in (
        (source, "precommitment source"),
        (selector_code_manifest, "selector code manifest"),
        (beacon_round_spec, "beacon round spec"),
    ):
        _reject_legacy_body_card_artifact(value, label)
    if type(source) is not PreparedPinnedMathNetSourceV3:
        raise ValueError("precommitment requires an exact prepared official MathNet source")
    source.validate_current()
    if type(selector_code_manifest) is not MathNetRandomSelectorCodeManifestV4:
        raise ValueError("precommitment requires an exact selector code manifest")
    selector_code_manifest.validate_current_files()
    if type(beacon_round_spec) is not DrandQuicknetRoundSpecV4:
        raise ValueError("precommitment requires an exact future Quicknet round")
    beacon_round_spec.validate_current()
    return MathNetRandomExternalPrecommitmentV4._derive(
        claimed_created_utc=claimed_created_utc,
        source_manifest_digest=source.manifest.content_digest,
        source_runtime_evidence_digest=source.runtime_evidence.content_digest,
        selector_code_manifest=selector_code_manifest,
        beacon_round_spec=beacon_round_spec,
    )


@dataclass(frozen=True, slots=True)
class DrandRelayByteReceiptV4:
    relay_url: str
    response_base64: str = field(repr=False)
    response_sha256: str

    def __post_init__(self) -> None:
        if type(self) is not DrandRelayByteReceiptV4:
            raise ValueError("drand relay-receipt subclasses are forbidden")
        split = urlsplit(self.relay_url)
        if (
            split.scheme != "https"
            or not split.hostname
            or split.username is not None
            or split.password is not None
            or split.fragment
        ):
            raise ValueError("drand relay receipt requires an exact HTTPS URL")
        _digest(self.response_sha256, "drand relay response")

        if type(self.response_base64) is not str:
            raise ValueError("drand relay response base64 must be exact text")
        try:
            raw = base64.b64decode(self.response_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("drand relay response base64 is invalid") from exc
        if not raw or len(raw) > _MAX_RELAY_RESPONSE_BYTES_V4:
            raise ValueError("drand relay response bytes exceed their bound")
        if base64.b64encode(raw).decode("ascii") != self.response_base64:
            raise ValueError("drand relay response base64 is not canonical")
        if not hmac.compare_digest(self.response_sha256, _sha256(raw)):
            raise ValueError("drand relay response digest is stale")

    @property
    def response_bytes(self) -> bytes:
        self.__post_init__()
        return base64.b64decode(self.response_base64, validate=True)

    def to_dict(self) -> dict[str, str]:
        self.__post_init__()
        return {
            "relay_url": self.relay_url,
            "response_base64": self.response_base64,
            "response_sha256": self.response_sha256,
        }


def _parse_drand_response_v4(raw: object, spec: DrandQuicknetRoundSpecV4) -> dict[str, object]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_RELAY_RESPONSE_BYTES_V4:
        raise ValueError("drand relay response must be bounded immutable bytes")
    value = _strict_json(raw, label="drand relay response", canonical=False)
    response = _exact_mapping(
        value,
        {"randomness", "round", "signature"},
        "drand relay response",
    )
    if type(response["round"]) is not int or response["round"] != spec.round_number:
        raise ValueError("drand relay returned another round")
    randomness = response["randomness"]
    signature = response["signature"]
    if type(randomness) is not str or _HEX_32_RE.fullmatch(randomness) is None:
        raise ValueError("drand randomness must be exact lowercase 32-byte hex")
    if type(signature) is not str or _HEX_G1_SIGNATURE_RE.fullmatch(signature) is None:
        raise ValueError("drand Quicknet signature must be exact lowercase G1 hex")
    if hashlib.sha256(bytes.fromhex(signature)).hexdigest() != randomness:
        raise ValueError("drand randomness is not SHA-256 of the returned signature")
    return response


def _relay_observation_v4(
    precommitment: MathNetRandomExternalPrecommitmentV4,
    relay_responses: tuple[tuple[str, bytes], ...],
    observed_at_claimed_utc: str,
) -> tuple[dict[str, object], tuple[DrandRelayByteReceiptV4, ...]]:
    precommitment.validate_current()
    spec = precommitment.beacon_round_spec
    if type(relay_responses) is not tuple or len(relay_responses) != len(spec.relay_urls):
        raise ValueError("beacon receipt requires both exact Quicknet relay byte responses")
    if not all(
        type(item) is tuple
        and len(item) == 2
        and type(item[0]) is str
        and type(item[1]) is bytes
        for item in relay_responses
    ):
        raise ValueError("Quicknet relay observations must be exact URL/bytes pairs")
    if tuple(item[0] for item in relay_responses) != spec.relay_urls:
        raise ValueError("Quicknet relay URLs differ from the precommitted order")
    parsed = tuple(_parse_drand_response_v4(item[1], spec) for item in relay_responses)
    semantic = tuple(
        (item["round"], item["randomness"], item["signature"]) for item in parsed
    )
    if len(set(semantic)) != 1:
        raise ValueError("Quicknet relays disagree on round, randomness, or signature")
    observed = _strict_utc(observed_at_claimed_utc, "beacon observation claim")
    scheduled = datetime.fromtimestamp(spec.scheduled_unix, tz=UTC)
    if observed < scheduled:
        raise ValueError("claimed beacon observation precedes the scheduled round")
    receipts = tuple(
        DrandRelayByteReceiptV4(
            relay_url=url,
            response_base64=base64.b64encode(raw).decode("ascii"),
            response_sha256=_sha256(raw),
        )
        for url, raw in relay_responses
    )
    first = parsed[0]
    observation = {
        "beacon_spec_digest": spec.content_digest,
        "drand_randomness": first["randomness"],
        "drand_signature": first["signature"],
        "observed_at_claimed_utc": observed_at_claimed_utc,
        "precommitment_content_digest": precommitment.content_digest,
        "precommitment_record_sha256": precommitment.record_sha256,
        "relay_receipts": [item.to_dict() for item in receipts],
        "round_number": spec.round_number,
    }
    return observation, receipts


def render_mathnet_random_beacon_anchor_payload_v4(
    precommitment: MathNetRandomExternalPrecommitmentV4,
    *,
    relay_responses: tuple[tuple[str, bytes], ...],
    observed_at_claimed_utc: str,
) -> bytes:
    _reject_legacy_body_card_artifact(precommitment, "beacon anchor precommitment")
    if type(precommitment) is not MathNetRandomExternalPrecommitmentV4:
        raise ValueError("beacon anchor requires the exact random-lane precommitment")
    observation, _receipts = _relay_observation_v4(
        precommitment,
        relay_responses,
        observed_at_claimed_utc,
    )
    payload = {
        "observation": observation,
        "schema": "org.math-research-brain/mathnet-random-beacon-anchor-payload",
        "schema_version": MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4,
    }
    return canonical_json(payload).encode("utf-8")


_BEACON_RECEIPT_TOKEN_V4 = object()


@dataclass(frozen=True, slots=True, init=False)
class ExternallyAnchoredDrandBeaconReceiptV4:
    """Two-relay byte receipt with a generic external anchor and no BLS authority claim."""

    precommitment_content_digest: str
    precommitment_record_sha256: str
    beacon_spec_digest: str
    round_number: int
    drand_randomness: str
    drand_signature: str
    relay_receipts: tuple[DrandRelayByteReceiptV4, ...]
    observed_at_claimed_utc: str
    anchor_system: str
    anchor_locator: str
    anchor_payload_sha256: str
    anchor_evidence_sha256: str
    anchor_claimed_utc: str
    content_digest: str
    _mint_marker: object = field(repr=False, compare=False)

    def __new__(cls, token: object = None) -> ExternallyAnchoredDrandBeaconReceiptV4:
        if token is not _BEACON_RECEIPT_TOKEN_V4:
            raise TypeError("beacon receipts require the exact builder or canonical parser")
        return object.__new__(cls)

    @classmethod
    def _derive(cls, **values: object) -> ExternallyAnchoredDrandBeaconReceiptV4:
        instance = cls(_BEACON_RECEIPT_TOKEN_V4)
        for name in (
            "precommitment_content_digest",
            "precommitment_record_sha256",
            "beacon_spec_digest",
            "round_number",
            "drand_randomness",
            "drand_signature",
            "relay_receipts",
            "observed_at_claimed_utc",
            "anchor_system",
            "anchor_locator",
            "anchor_payload_sha256",
            "anchor_evidence_sha256",
            "anchor_claimed_utc",
        ):
            object.__setattr__(instance, name, values[name])
        object.__setattr__(instance, "_mint_marker", _BEACON_RECEIPT_TOKEN_V4)
        instance._validate_fields()
        object.__setattr__(
            instance,
            "content_digest",
            _domain_digest(
                instance._payload(),
                domain="org.math-research-brain/mathnet-random-beacon-receipt/v4",
            ),
        )
        return instance

    def _payload(self) -> dict[str, object]:
        return {
            "anchor": {
                "anchor_claimed_utc": self.anchor_claimed_utc,
                "anchor_evidence_sha256": self.anchor_evidence_sha256,
                "anchor_locator": self.anchor_locator,
                "anchor_payload_sha256": self.anchor_payload_sha256,
                "anchor_system": self.anchor_system,
                "external_anchor_authority_verified": False,
            },
            "authority": {
                "beacon_bls_signature_verified": False,
                "chronology_verified": False,
                "provider_origin_verified": False,
                "randomness_hash_verified": True,
                "relay_agreement_verified": True,
            },
            "beacon_spec_digest": self.beacon_spec_digest,
            "drand_randomness": self.drand_randomness,
            "drand_signature": self.drand_signature,
            "observed_at_claimed_utc": self.observed_at_claimed_utc,
            "precommitment_content_digest": self.precommitment_content_digest,
            "precommitment_record_sha256": self.precommitment_record_sha256,
            "relay_receipts": [item.to_dict() for item in self.relay_receipts],
            "round_number": self.round_number,
            "schema": MATHNET_RANDOM_EXTERNAL_BEACON_RECEIPT_SCHEMA_V4,
            "schema_version": MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4,
        }

    def _validate_fields(self) -> None:
        if type(self) is not ExternallyAnchoredDrandBeaconReceiptV4 or self._mint_marker is not (
            _BEACON_RECEIPT_TOKEN_V4
        ):
            raise ValueError("beacon receipt is not nominal")
        for value, label in (
            (self.precommitment_content_digest, "receipt precommitment content"),
            (self.precommitment_record_sha256, "receipt precommitment record"),
            (self.beacon_spec_digest, "receipt beacon spec"),
            (self.anchor_payload_sha256, "receipt anchor payload"),
            (self.anchor_evidence_sha256, "receipt anchor evidence"),
        ):
            _digest(value, label)
        if type(self.round_number) is not int or self.round_number < 1:
            raise ValueError("receipt round number is invalid")
        if type(self.drand_randomness) is not str or _HEX_32_RE.fullmatch(
            self.drand_randomness
        ) is None:
            raise ValueError("receipt randomness is invalid")
        if type(self.drand_signature) is not str or _HEX_G1_SIGNATURE_RE.fullmatch(
            self.drand_signature
        ) is None:
            raise ValueError("receipt signature is invalid")
        if hashlib.sha256(bytes.fromhex(self.drand_signature)).hexdigest() != (
            self.drand_randomness
        ):
            raise ValueError("receipt randomness/signature relation is stale")
        if type(self.relay_receipts) is not tuple or len(self.relay_receipts) != len(
            DRAND_QUICKNET_RELAY_BASE_URLS_V4
        ):
            raise ValueError("receipt must bind both relay byte responses")
        for receipt in self.relay_receipts:
            if type(receipt) is not DrandRelayByteReceiptV4:
                raise ValueError("receipt contains a non-nominal relay receipt")
            receipt.__post_init__()
        spec = DrandQuicknetRoundSpecV4(round_number=self.round_number)
        if not hmac.compare_digest(self.beacon_spec_digest, spec.content_digest):
            raise ValueError("receipt beacon spec differs from the exact Quicknet round")
        if tuple(receipt.relay_url for receipt in self.relay_receipts) != spec.relay_urls:
            raise ValueError("receipt relay URLs differ from the exact Quicknet round")
        parsed_responses = tuple(
            _parse_drand_response_v4(receipt.response_bytes, spec)
            for receipt in self.relay_receipts
        )
        semantic = tuple(
            (item["round"], item["randomness"], item["signature"])
            for item in parsed_responses
        )
        if len(set(semantic)) != 1 or semantic[0] != (
            self.round_number,
            self.drand_randomness,
            self.drand_signature,
        ):
            raise ValueError("receipt relay bytes do not establish the recorded agreement")
        _strict_utc(self.observed_at_claimed_utc, "receipt observation claim")
        observed = _strict_utc(self.observed_at_claimed_utc, "receipt observation claim")
        anchored = _strict_utc(self.anchor_claimed_utc, "receipt anchor claim")
        if anchored < observed:
            raise ValueError("claimed external anchor precedes the beacon observation")
        if type(self.anchor_system) is not str or _IDENTIFIER_RE.fullmatch(
            self.anchor_system
        ) is None:
            raise ValueError("anchor system identifier is invalid")
        split = urlsplit(self.anchor_locator)
        if (
            type(self.anchor_locator) is not str
            or len(self.anchor_locator.encode("utf-8")) > 2048
            or split.scheme != "https"
            or not split.hostname
            or split.username is not None
            or split.password is not None
            or split.fragment
        ):
            raise ValueError("external anchor locator must be an exact HTTPS URL")
        anchor_payload = {
            "observation": {
                "beacon_spec_digest": self.beacon_spec_digest,
                "drand_randomness": self.drand_randomness,
                "drand_signature": self.drand_signature,
                "observed_at_claimed_utc": self.observed_at_claimed_utc,
                "precommitment_content_digest": self.precommitment_content_digest,
                "precommitment_record_sha256": self.precommitment_record_sha256,
                "relay_receipts": [item.to_dict() for item in self.relay_receipts],
                "round_number": self.round_number,
            },
            "schema": "org.math-research-brain/mathnet-random-beacon-anchor-payload",
            "schema_version": MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4,
        }
        if not hmac.compare_digest(
            self.anchor_payload_sha256,
            _sha256(canonical_json(anchor_payload).encode("utf-8")),
        ):
            raise ValueError("receipt anchor payload digest is stale")

    def validate_current(self) -> None:
        self._validate_fields()
        expected = _domain_digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-random-beacon-receipt/v4",
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("beacon receipt content digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}

    def canonical_record(self) -> bytes:
        return canonical_json(self.to_dict()).encode("utf-8")

    @property
    def record_sha256(self) -> str:
        return _sha256(self.canonical_record())

    @classmethod
    def from_canonical_record(cls, raw: bytes) -> ExternallyAnchoredDrandBeaconReceiptV4:
        value = _strict_json(raw, label="MathNet random beacon receipt", canonical=True)
        parsed = _exact_mapping(
            value,
            {
                "anchor",
                "authority",
                "beacon_spec_digest",
                "content_digest",
                "drand_randomness",
                "drand_signature",
                "observed_at_claimed_utc",
                "precommitment_content_digest",
                "precommitment_record_sha256",
                "relay_receipts",
                "round_number",
                "schema",
                "schema_version",
            },
            "MathNet random beacon receipt",
        )
        if (
            parsed["schema"] != MATHNET_RANDOM_EXTERNAL_BEACON_RECEIPT_SCHEMA_V4
            or parsed["schema_version"] != MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4
        ):
            raise ValueError("beacon receipt schema is unsupported")
        authority = _exact_mapping(
            parsed["authority"],
            {
                "beacon_bls_signature_verified",
                "chronology_verified",
                "provider_origin_verified",
                "randomness_hash_verified",
                "relay_agreement_verified",
            },
            "beacon receipt authority",
        )
        if authority != {
            "beacon_bls_signature_verified": False,
            "chronology_verified": False,
            "provider_origin_verified": False,
            "randomness_hash_verified": True,
            "relay_agreement_verified": True,
        }:
            raise ValueError("beacon receipt overclaims authority")
        anchor = _exact_mapping(
            parsed["anchor"],
            {
                "anchor_claimed_utc",
                "anchor_evidence_sha256",
                "anchor_locator",
                "anchor_payload_sha256",
                "anchor_system",
                "external_anchor_authority_verified",
            },
            "beacon receipt anchor",
        )
        if anchor["external_anchor_authority_verified"] is not False:
            raise ValueError("generic anchor receipt cannot claim external authority")
        relay_raw = parsed["relay_receipts"]
        if type(relay_raw) is not list:
            raise ValueError("beacon relay receipts must be an exact list")
        relays = tuple(
            DrandRelayByteReceiptV4(
                relay_url=_exact_mapping(
                    item,
                    {"relay_url", "response_base64", "response_sha256"},
                    "drand relay receipt",
                )["relay_url"],
                response_base64=item["response_base64"],
                response_sha256=item["response_sha256"],
            )
            for item in relay_raw
        )
        instance = cls._derive(
            precommitment_content_digest=parsed["precommitment_content_digest"],
            precommitment_record_sha256=parsed["precommitment_record_sha256"],
            beacon_spec_digest=parsed["beacon_spec_digest"],
            round_number=parsed["round_number"],
            drand_randomness=parsed["drand_randomness"],
            drand_signature=parsed["drand_signature"],
            relay_receipts=relays,
            observed_at_claimed_utc=parsed["observed_at_claimed_utc"],
            anchor_system=anchor["anchor_system"],
            anchor_locator=anchor["anchor_locator"],
            anchor_payload_sha256=anchor["anchor_payload_sha256"],
            anchor_evidence_sha256=anchor["anchor_evidence_sha256"],
            anchor_claimed_utc=anchor["anchor_claimed_utc"],
        )
        if not hmac.compare_digest(
            instance.content_digest,
            _digest(parsed["content_digest"], "beacon receipt content"),
        ):
            raise ValueError("beacon receipt content digest differs")
        return instance


def build_externally_anchored_drand_beacon_receipt_v4(
    precommitment: MathNetRandomExternalPrecommitmentV4,
    *,
    relay_responses: tuple[tuple[str, bytes], ...],
    observed_at_claimed_utc: str,
    anchor_payload_bytes: bytes,
    anchor_system: str,
    anchor_locator: str,
    anchor_evidence_bytes: bytes,
    anchor_claimed_utc: str,
) -> ExternallyAnchoredDrandBeaconReceiptV4:
    _reject_legacy_body_card_artifact(precommitment, "beacon receipt precommitment")
    if type(precommitment) is not MathNetRandomExternalPrecommitmentV4:
        raise ValueError("beacon receipt requires the exact random-lane precommitment")
    expected_anchor = render_mathnet_random_beacon_anchor_payload_v4(
        precommitment,
        relay_responses=relay_responses,
        observed_at_claimed_utc=observed_at_claimed_utc,
    )
    if type(anchor_payload_bytes) is not bytes or not hmac.compare_digest(
        anchor_payload_bytes,
        expected_anchor,
    ):
        raise ValueError("external anchor payload differs from the exact beacon observation")
    if (
        type(anchor_evidence_bytes) is not bytes
        or not anchor_evidence_bytes
        or len(anchor_evidence_bytes) > _MAX_ANCHOR_EVIDENCE_BYTES_V4
    ):
        raise ValueError("external anchor evidence must be bounded immutable bytes")
    observation, relays = _relay_observation_v4(
        precommitment,
        relay_responses,
        observed_at_claimed_utc,
    )
    return ExternallyAnchoredDrandBeaconReceiptV4._derive(
        precommitment_content_digest=precommitment.content_digest,
        precommitment_record_sha256=precommitment.record_sha256,
        beacon_spec_digest=precommitment.beacon_round_spec.content_digest,
        round_number=precommitment.beacon_round_spec.round_number,
        drand_randomness=observation["drand_randomness"],
        drand_signature=observation["drand_signature"],
        relay_receipts=relays,
        observed_at_claimed_utc=observed_at_claimed_utc,
        anchor_system=anchor_system,
        anchor_locator=anchor_locator,
        anchor_payload_sha256=_sha256(anchor_payload_bytes),
        anchor_evidence_sha256=_sha256(anchor_evidence_bytes),
        anchor_claimed_utc=anchor_claimed_utc,
    )


def _validate_precommitment_receipt_pair_v4(
    precommitment: MathNetRandomExternalPrecommitmentV4,
    receipt: ExternallyAnchoredDrandBeaconReceiptV4,
) -> None:
    precommitment.validate_current()
    receipt.validate_current()
    spec = precommitment.beacon_round_spec
    if (
        receipt.precommitment_content_digest != precommitment.content_digest
        or receipt.precommitment_record_sha256 != precommitment.record_sha256
        or receipt.beacon_spec_digest != spec.content_digest
        or receipt.round_number != spec.round_number
        or tuple(item.relay_url for item in receipt.relay_receipts) != spec.relay_urls
    ):
        raise ValueError("beacon receipt differs from the exact precommitted round")
    scheduled = datetime.fromtimestamp(spec.scheduled_unix, tz=UTC)
    if _strict_utc(receipt.observed_at_claimed_utc, "beacon observation claim") < scheduled:
        raise ValueError("beacon receipt claims an observation before the scheduled round")


def derive_mathnet_random_selection_seed_v4(
    precommitment: MathNetRandomExternalPrecommitmentV4,
    receipt: ExternallyAnchoredDrandBeaconReceiptV4,
) -> bytes:
    """Derive the sole selector seed; failed-lane artifacts and anchor evidence are excluded."""

    _validate_precommitment_receipt_pair_v4(precommitment, receipt)
    payload = {
        "beacon_spec_digest": precommitment.beacon_round_spec.content_digest,
        "drand_randomness": receipt.drand_randomness,
        "lane_id": MATHNET_RANDOM_EXTERNAL_LANE_ID_V4,
        "round_number": receipt.round_number,
        "sample_size": MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4,
        "selection_attempt_index": 0,
        "selection_policy": MATHNET_RANDOM_EXTERNAL_SELECTION_POLICY_V4,
        "selector_code_manifest_digest": precommitment.selector_code_manifest.content_digest,
        "source_manifest_digest": precommitment.source_manifest_digest,
    }
    return hashlib.sha256(
        b"org.math-research-brain/mathnet-random-external-selection-seed/v4\x00"
        + canonical_json(payload).encode("utf-8")
    ).digest()


def _case_id_v4(
    *,
    verified_bank_digest: str,
    selection_index: int,
    public_selection_digest: str,
) -> str:
    suffix = _domain_digest(
        {
            "public_selection_digest": public_selection_digest,
            "selection_index": selection_index,
            "verified_bank_digest": verified_bank_digest,
        },
        domain="org.math-research-brain/mathnet-random-external-case-id/v4",
    ).removeprefix("sha256:")
    return "mathnet-random-v4:" + suffix


@dataclass(frozen=True, slots=True)
class MathNetRandomExternalSelectedCaseV4:
    """Selector-facing exact row: body plus opaque source/candidate/gold commitments."""

    selection_index: int
    case_id: str
    problem_text: str = field(repr=False)
    problem_text_digest: str
    source_cluster_digest: str
    public_selection_digest: str
    candidate_content_digest: str
    private_gold_commitment: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(
                self._payload(),
                domain="org.math-research-brain/mathnet-random-external-selected-case/v4",
            ),
        )

    def _validate_fields(self) -> None:
        if type(self) is not MathNetRandomExternalSelectedCaseV4:
            raise ValueError("MathNet random selected-case subclasses are forbidden")
        if type(self.selection_index) is not int or not (
            0 <= self.selection_index < MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4
        ):
            raise ValueError("MathNet random selection index is invalid")
        if type(self.case_id) is not str or _CASE_ID_RE.fullmatch(self.case_id) is None:
            raise ValueError("MathNet random case id is invalid")
        if (
            type(self.problem_text) is not str
            or not self.problem_text.strip()
            or len(self.problem_text.encode("utf-8")) > MAX_PROMPT_BYTES_V3
        ):
            raise ValueError("MathNet random problem body is blank or exceeds its byte budget")
        expected_problem_digest = _sha256(self.problem_text.encode("utf-8"))
        if not hmac.compare_digest(
            self.problem_text_digest,
            expected_problem_digest,
        ):
            raise ValueError("MathNet random problem-text digest is stale")
        for value, label in (
            (self.source_cluster_digest, "selected source cluster"),
            (self.public_selection_digest, "selected public projection"),
            (self.candidate_content_digest, "selected candidate"),
            (self.private_gold_commitment, "selected private-gold commitment"),
        ):
            _digest(value, label)

    def _payload(self) -> dict[str, object]:
        return {
            "candidate_content_digest": self.candidate_content_digest,
            "case_id": self.case_id,
            "private_gold_commitment": self.private_gold_commitment,
            "problem_text": self.problem_text,
            "problem_text_digest": self.problem_text_digest,
            "public_selection_digest": self.public_selection_digest,
            "selection_index": self.selection_index,
            "source_cluster_digest": self.source_cluster_digest,
        }

    def validate_current(self) -> None:
        self._validate_fields()
        expected = _domain_digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-random-external-selected-case/v4",
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("MathNet random selected-case digest is stale")

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def _selection_digest_v4(rows: tuple[MathNetRandomExternalSelectedCaseV4, ...]) -> str:
    return _domain_digest(
        [row.to_dict() for row in rows],
        domain="org.math-research-brain/mathnet-random-external-selection/v4",
    )


@dataclass(frozen=True, slots=True)
class MathNetRandomExternalGoldAuditItemV4:
    """Body-only audit row; order is meaningful and no source metadata is present."""

    case_id: str
    problem_text: str = field(repr=False)
    problem_text_digest: str

    def __post_init__(self) -> None:
        if type(self) is not MathNetRandomExternalGoldAuditItemV4:
            raise ValueError("MathNet random gold-input item subclasses are forbidden")
        if type(self.case_id) is not str or _CASE_ID_RE.fullmatch(self.case_id) is None:
            raise ValueError("MathNet random gold-input case id is invalid")
        if (
            type(self.problem_text) is not str
            or not self.problem_text.strip()
            or len(self.problem_text.encode("utf-8")) > MAX_PROMPT_BYTES_V3
        ):
            raise ValueError("MathNet random gold-input body is invalid")
        expected = _sha256(self.problem_text.encode("utf-8"))
        if not hmac.compare_digest(self.problem_text_digest, expected):
            raise ValueError("MathNet random gold-input body digest is stale")

    def to_dict(self) -> dict[str, str]:
        self.__post_init__()
        return {
            "case_id": self.case_id,
            "problem_text": self.problem_text,
            "problem_text_digest": self.problem_text_digest,
        }


_GOLD_INPUT_TOKEN_V4 = object()


@dataclass(frozen=True, slots=True, init=False)
class MathNetRandomExternalGoldAuditInputV4:
    """Replay-minted body-only input; it contains no bank, gold, labels, or raw vault."""

    items: tuple[MathNetRandomExternalGoldAuditItemV4, ...] = field(repr=False)
    selection_digest: str
    projection_digest: str
    precommitment_record_sha256: str
    beacon_receipt_record_sha256: str
    source_manifest_digest: str
    selector_code_manifest_digest: str
    verified_bank_digest: str
    private_gold_vault_record_sha256: str
    content_digest: str
    _mint_marker: object = field(repr=False, compare=False)

    def __new__(cls) -> MathNetRandomExternalGoldAuditInputV4:
        raise TypeError("MathNet random gold input can only be minted inside cold replay")

    def _payload(self) -> dict[str, object]:
        return {
            "authority": {
                "chronology_verified": False,
                "independence_verified": False,
                "model_identity_verified": False,
            },
            "beacon_receipt_record_sha256": self.beacon_receipt_record_sha256,
            "items": [item.to_dict() for item in self.items],
            "lane_id": MATHNET_RANDOM_EXTERNAL_LANE_ID_V4,
            "precommitment_record_sha256": self.precommitment_record_sha256,
            "private_gold_vault_record_sha256": self.private_gold_vault_record_sha256,
            "projection_digest": self.projection_digest,
            "schema": MATHNET_RANDOM_EXTERNAL_GOLD_INPUT_SCHEMA_V4,
            "schema_version": MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4,
            "selection_digest": self.selection_digest,
            "selector_code_manifest_digest": self.selector_code_manifest_digest,
            "source_manifest_digest": self.source_manifest_digest,
            "verified_bank_digest": self.verified_bank_digest,
        }

    def _validate_fields(self) -> None:
        if type(self) is not MathNetRandomExternalGoldAuditInputV4 or self._mint_marker is not (
            _GOLD_INPUT_TOKEN_V4
        ):
            raise ValueError("MathNet random gold input is not replay-minted")
        if type(self.items) is not tuple or len(self.items) != (
            MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4
        ):
            raise ValueError("MathNet random gold input must contain exactly twelve bodies")
        if not all(type(item) is MathNetRandomExternalGoldAuditItemV4 for item in self.items):
            raise ValueError("MathNet random gold input contains a non-nominal row")
        for item in self.items:
            item.__post_init__()
        if len({item.case_id for item in self.items}) != len(self.items):
            raise ValueError("MathNet random gold input repeats a case id")
        for value, label in (
            (self.selection_digest, "gold input selection"),
            (self.projection_digest, "gold input projection"),
            (self.precommitment_record_sha256, "gold input precommitment"),
            (self.beacon_receipt_record_sha256, "gold input beacon receipt"),
            (self.source_manifest_digest, "gold input source manifest"),
            (self.selector_code_manifest_digest, "gold input selector code"),
            (self.verified_bank_digest, "gold input verified bank"),
            (self.private_gold_vault_record_sha256, "gold input private vault"),
        ):
            _digest(value, label)
        expected_projection = _domain_digest(
            [
                {
                    "case_id": item.case_id,
                    "problem_text_digest": item.problem_text_digest,
                }
                for item in self.items
            ],
            domain="org.math-research-brain/mathnet-random-external-gold-projection/v4",
        )
        if not hmac.compare_digest(self.projection_digest, expected_projection):
            raise ValueError("MathNet random gold projection digest is stale")

    def validate_current(self) -> None:
        self._validate_fields()
        expected = _domain_digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-random-external-gold-input/v4",
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("MathNet random gold-input digest is stale")

    @property
    def selected_item_ids(self) -> tuple[str, ...]:
        self.validate_current()
        return tuple(item.case_id for item in self.items)

    def canonical_record(self) -> bytes:
        self.validate_current()
        return canonical_json(self._payload() | {"content_digest": self.content_digest}).encode(
            "utf-8"
        )


_SELECTION_TOKEN_V4 = object()


@dataclass(frozen=True, slots=True, init=False)
class ColdReplayMathNetRandomSelectionV4:
    """Nominal twelve-case selection minted only after two complete official bank passes."""

    selected_rows: tuple[MathNetRandomExternalSelectedCaseV4, ...] = field(repr=False)
    gold_audit_input: MathNetRandomExternalGoldAuditInputV4 = field(repr=False)
    selection_digest: str
    precommitment_content_digest: str
    precommitment_record_sha256: str
    beacon_receipt_content_digest: str
    beacon_receipt_record_sha256: str
    beacon_round_spec_digest: str
    beacon_round_number: int
    beacon_anchor_payload_sha256: str
    source_manifest_digest: str
    source_runtime_evidence_digest: str
    selector_code_manifest_digest: str
    verified_bank_digest: str
    verified_bank_public_commitment_digest: str
    selection_seed_commitment: str
    private_gold_vault_record_sha256: str
    confirmatory_source_gate: bool
    content_digest: str
    _mint_marker: object = field(repr=False, compare=False)

    def __new__(cls) -> ColdReplayMathNetRandomSelectionV4:
        raise TypeError("MathNet random selection can only be minted inside official cold replay")

    def _payload(self) -> dict[str, object]:
        return {
            "authority": {
                "beacon_bls_signature_verified": False,
                "cold_replay_verified": True,
                "external_chronology_authority_verified": False,
                "future_round_claim_order_checked": True,
                "independence_verified": False,
                "model_identity_verified": False,
                "randomness_hash_verified": True,
                "two_relay_agreement_verified": True,
            },
            "beacon_anchor_payload_sha256": self.beacon_anchor_payload_sha256,
            "beacon_receipt_content_digest": self.beacon_receipt_content_digest,
            "beacon_receipt_record_sha256": self.beacon_receipt_record_sha256,
            "beacon_round_number": self.beacon_round_number,
            "beacon_round_spec_digest": self.beacon_round_spec_digest,
            "confirmatory_source_gate": self.confirmatory_source_gate,
            "engineering_only": True,
            "gold_audit_input_digest": self.gold_audit_input.content_digest,
            "lane_id": MATHNET_RANDOM_EXTERNAL_LANE_ID_V4,
            "precommitment_content_digest": self.precommitment_content_digest,
            "precommitment_record_sha256": self.precommitment_record_sha256,
            "private_gold_vault_record_sha256": self.private_gold_vault_record_sha256,
            "selected_rows": [row.to_dict() for row in self.selected_rows],
            "selection_digest": self.selection_digest,
            "selection_seed_commitment": self.selection_seed_commitment,
            "selector_code_manifest_digest": self.selector_code_manifest_digest,
            "source_manifest_digest": self.source_manifest_digest,
            "source_runtime_evidence_digest": self.source_runtime_evidence_digest,
            "verified_bank_digest": self.verified_bank_digest,
            "verified_bank_public_commitment_digest": (
                self.verified_bank_public_commitment_digest
            ),
        }

    def _validate_fields(self) -> None:
        if type(self) is not ColdReplayMathNetRandomSelectionV4 or (
            self._mint_marker is not _SELECTION_TOKEN_V4
        ):
            raise ValueError("MathNet random selection capability is not cold-replay minted")
        if type(self.selected_rows) is not tuple or len(self.selected_rows) != (
            MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4
        ):
            raise ValueError("MathNet random capability must hold exactly twelve selected rows")
        if not all(type(row) is MathNetRandomExternalSelectedCaseV4 for row in self.selected_rows):
            raise ValueError("MathNet random capability contains a non-nominal selected row")
        for row in self.selected_rows:
            row.validate_current()
        if tuple(row.selection_index for row in self.selected_rows) != tuple(
            range(MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4)
        ):
            raise ValueError("MathNet random selected rows are reordered or noncontiguous")
        for values, label in (
            ((row.case_id for row in self.selected_rows), "case id"),
            ((row.source_cluster_digest for row in self.selected_rows), "source cluster"),
            ((row.candidate_content_digest for row in self.selected_rows), "candidate"),
        ):
            material = tuple(values)
            if len(set(material)) != len(material):
                raise ValueError(f"MathNet random capability repeats a selected {label}")
        for row in self.selected_rows:
            expected_id = _case_id_v4(
                verified_bank_digest=self.verified_bank_digest,
                selection_index=row.selection_index,
                public_selection_digest=row.public_selection_digest,
            )
            if not hmac.compare_digest(row.case_id, expected_id):
                raise ValueError("MathNet random case id differs from its exact bank coordinate")
        expected_selection = _selection_digest_v4(self.selected_rows)
        if not hmac.compare_digest(self.selection_digest, expected_selection):
            raise ValueError("MathNet random selection digest is stale")
        if type(self.gold_audit_input) is not MathNetRandomExternalGoldAuditInputV4:
            raise ValueError("MathNet random capability requires its exact body-only gold input")
        self.gold_audit_input.validate_current()
        expected_items = tuple(
            (row.case_id, row.problem_text, row.problem_text_digest)
            for row in self.selected_rows
        )
        observed_items = tuple(
            (item.case_id, item.problem_text, item.problem_text_digest)
            for item in self.gold_audit_input.items
        )
        if expected_items != observed_items:
            raise ValueError("MathNet random capability and gold projection bodies differ")
        for field_name in (
            "selection_digest",
            "precommitment_record_sha256",
            "beacon_receipt_record_sha256",
            "source_manifest_digest",
            "selector_code_manifest_digest",
            "verified_bank_digest",
            "private_gold_vault_record_sha256",
        ):
            if getattr(self.gold_audit_input, field_name) != getattr(self, field_name):
                raise ValueError(f"MathNet random gold input differs at {field_name}")
        for value, label in (
            (self.selection_digest, "capability selection"),
            (self.precommitment_content_digest, "capability precommitment content"),
            (self.precommitment_record_sha256, "capability precommitment record"),
            (self.beacon_receipt_content_digest, "capability beacon content"),
            (self.beacon_receipt_record_sha256, "capability beacon record"),
            (self.beacon_round_spec_digest, "capability beacon spec"),
            (self.beacon_anchor_payload_sha256, "capability beacon anchor"),
            (self.source_manifest_digest, "capability source manifest"),
            (self.source_runtime_evidence_digest, "capability source runtime"),
            (self.selector_code_manifest_digest, "capability selector code"),
            (self.verified_bank_digest, "capability verified bank"),
            (self.verified_bank_public_commitment_digest, "capability public bank"),
            (self.selection_seed_commitment, "capability selection seed"),
            (self.private_gold_vault_record_sha256, "capability private vault"),
        ):
            _digest(value, label)
        if type(self.beacon_round_number) is not int or self.beacon_round_number < 1:
            raise ValueError("capability beacon round is invalid")
        if type(self.confirmatory_source_gate) is not bool:
            raise ValueError("capability source gate must be an exact boolean")

    def validate_current(self) -> None:
        self._validate_fields()
        expected = _domain_digest(
            self._payload(),
            domain="org.math-research-brain/mathnet-random-external-capability/v4",
        )
        if not hmac.compare_digest(self.content_digest, expected):
            raise ValueError("MathNet random capability digest is stale")

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        payload = self._payload().copy()
        payload.pop("selected_rows")
        return payload | {"content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class MathNetRandomPrivateGoldEntryV4:
    """Evaluator-private exact gold row, never part of the reviewer projection."""

    case_id: str
    problem_text_digest: str
    candidate_content_digest: str
    source_cluster_digest: str
    private_gold: MathNetPrivateGoldV3 = field(repr=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetRandomPrivateGoldEntryV4:
            raise ValueError("MathNet random private-vault entry subclasses are forbidden")
        if type(self.case_id) is not str or _CASE_ID_RE.fullmatch(self.case_id) is None:
            raise ValueError("MathNet random private-vault case id is invalid")
        for value, label in (
            (self.problem_text_digest, "private-vault problem text"),
            (self.candidate_content_digest, "private-vault candidate"),
            (self.source_cluster_digest, "private-vault source cluster"),
        ):
            _digest(value, label)
        if type(self.private_gold) is not MathNetPrivateGoldV3:
            raise ValueError("MathNet random private vault requires exact MathNet gold")
        self.private_gold.validate_current()

    def to_private_dict(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "candidate_content_digest": self.candidate_content_digest,
            "case_id": self.case_id,
            "private_gold": self.private_gold._private_dict(),
            "problem_text_digest": self.problem_text_digest,
            "source_cluster_digest": self.source_cluster_digest,
        }


def _private_gold_from_dict_v4(value: object) -> MathNetPrivateGoldV3:
    raw = _exact_mapping(
        value,
        {
            "answer",
            "confirmation_rule",
            "confirming_solution_indices",
            "content_digest",
            "final_answer_text_digest",
            "solution_text_digests",
        },
        "MathNet random private gold",
    )
    answer_raw = _exact_mapping(
        raw["answer"],
        {"denominator", "numerator"},
        "MathNet random exact answer",
    )
    solution_digests = raw["solution_text_digests"]
    confirming = raw["confirming_solution_indices"]
    if type(solution_digests) is not list or type(confirming) is not list:
        raise ValueError("MathNet random private-gold arrays must be exact lists")
    result = MathNetPrivateGoldV3(
        answer=ExactRationalV3(
            numerator=answer_raw["numerator"],
            denominator=answer_raw["denominator"],
        ),
        final_answer_text_digest=raw["final_answer_text_digest"],
        solution_text_digests=tuple(solution_digests),
        confirming_solution_indices=tuple(confirming),
        confirmation_rule=raw["confirmation_rule"],
    )
    if result._private_dict() != raw:
        raise ValueError("MathNet random private-gold record differs from its exact digest")
    return result


def _selected_material_v4(
    verified_bank: VerifiedPinnedMathNetBankV3,
) -> tuple[
    tuple[MathNetRandomExternalSelectedCaseV4, ...],
    tuple[MathNetRandomPrivateGoldEntryV4, ...],
]:
    if type(verified_bank) is not VerifiedPinnedMathNetBankV3:
        raise ValueError("MathNet random selection requires an exact official bank wrapper")
    verified_bank.validate_current()
    bank = verified_bank.bank
    if type(bank) is not MathNetBankV3:
        raise ValueError("MathNet random official wrapper contains no exact MathNet bank")
    bank.validate_current()
    if (
        bank.sample_size != MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4
        or bank.source_binding.authority is not MathNetSourceAuthorityV3.OFFICIAL_BYTE_VERIFIED
    ):
        raise ValueError("MathNet random lane requires twelve official source-cluster winners")
    rows: list[MathNetRandomExternalSelectedCaseV4] = []
    vault: list[MathNetRandomPrivateGoldEntryV4] = []
    for index, entry in enumerate(bank.entries):
        candidate = entry.candidate
        if type(candidate) is not MathNetCandidateV3:
            raise ValueError("MathNet random bank contains a non-nominal candidate")
        candidate.validate_current()
        case_id = _case_id_v4(
            verified_bank_digest=verified_bank.content_digest,
            selection_index=index,
            public_selection_digest=candidate.public_selection_digest,
        )
        problem_digest = _sha256(candidate.problem_markdown.encode("utf-8"))
        row = MathNetRandomExternalSelectedCaseV4(
            selection_index=index,
            case_id=case_id,
            problem_text=candidate.problem_markdown,
            problem_text_digest=problem_digest,
            source_cluster_digest=candidate.source_cluster_digest,
            public_selection_digest=candidate.public_selection_digest,
            candidate_content_digest=candidate.content_digest,
            private_gold_commitment=candidate.gold.content_digest,
        )
        rows.append(row)
        vault.append(
            MathNetRandomPrivateGoldEntryV4(
                case_id=case_id,
                problem_text_digest=problem_digest,
                candidate_content_digest=candidate.content_digest,
                source_cluster_digest=candidate.source_cluster_digest,
                private_gold=candidate.gold,
            )
        )
    return tuple(rows), tuple(vault)


def _projection_digest_from_rows_v4(
    rows: tuple[MathNetRandomExternalSelectedCaseV4, ...],
) -> str:
    return _domain_digest(
        [
            {"case_id": row.case_id, "problem_text_digest": row.problem_text_digest}
            for row in rows
        ],
        domain="org.math-research-brain/mathnet-random-external-gold-projection/v4",
    )


def _private_vault_record_v4(
    entries: tuple[MathNetRandomPrivateGoldEntryV4, ...],
    *,
    selection_digest: str,
    projection_digest: str,
    source_manifest_digest: str,
    selector_code_manifest_digest: str,
    verified_bank_digest: str,
) -> bytes:
    if type(entries) is not tuple or len(entries) != MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4:
        raise ValueError("MathNet random private vault requires exactly twelve entries")
    for entry in entries:
        if type(entry) is not MathNetRandomPrivateGoldEntryV4:
            raise ValueError("MathNet random private vault contains a non-nominal entry")
        entry.__post_init__()
    payload = {
        "entries": [entry.to_private_dict() for entry in entries],
        "lane_id": MATHNET_RANDOM_EXTERNAL_LANE_ID_V4,
        "projection_digest": projection_digest,
        "schema": MATHNET_RANDOM_EXTERNAL_PRIVATE_VAULT_SCHEMA_V4,
        "schema_version": MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4,
        "selection_digest": selection_digest,
        "selector_code_manifest_digest": selector_code_manifest_digest,
        "source_manifest_digest": source_manifest_digest,
        "verified_bank_digest": verified_bank_digest,
    }
    content_digest = _domain_digest(
        payload,
        domain="org.math-research-brain/mathnet-random-external-private-vault/v4",
    )
    return canonical_json(payload | {"content_digest": content_digest}).encode("utf-8")


def _cold_replay_material_v4(
    precommitment_bytes: bytes,
    beacon_receipt_bytes: bytes,
    *,
    expected_precommitment_record_sha256: str,
    expected_beacon_receipt_record_sha256: str,
    source: PreparedPinnedMathNetSourceV3,
) -> tuple[ColdReplayMathNetRandomSelectionV4, bytes]:
    for value, label in (
        (precommitment_bytes, "cold-replay precommitment"),
        (beacon_receipt_bytes, "cold-replay beacon receipt"),
        (source, "cold-replay source"),
    ):
        _reject_legacy_body_card_artifact(value, label)
    _record_sha256(
        precommitment_bytes,
        expected_precommitment_record_sha256,
        "MathNet random precommitment record",
    )
    _record_sha256(
        beacon_receipt_bytes,
        expected_beacon_receipt_record_sha256,
        "MathNet random beacon-receipt record",
    )
    precommitment = MathNetRandomExternalPrecommitmentV4.from_canonical_record(
        precommitment_bytes
    )
    receipt = ExternallyAnchoredDrandBeaconReceiptV4.from_canonical_record(
        beacon_receipt_bytes
    )
    precommitment.selector_code_manifest.validate_current_files()
    _validate_precommitment_receipt_pair_v4(precommitment, receipt)
    if type(source) is not PreparedPinnedMathNetSourceV3:
        raise ValueError("cold replay requires an exact prepared official MathNet source")
    source.validate_current()
    if (
        source.manifest.content_digest != precommitment.source_manifest_digest
        or source.runtime_evidence.content_digest
        != precommitment.source_runtime_evidence_digest
    ):
        raise ValueError("cold-replay source differs from the exact precommitment")
    seed = derive_mathnet_random_selection_seed_v4(precommitment, receipt)
    verified_bank = build_pinned_mathnet_bank_v3(
        source,
        sample_size=MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4,
        selection_seed=seed,
    )
    if type(verified_bank) is not VerifiedPinnedMathNetBankV3:
        raise ValueError("official MathNet builder returned a non-nominal bank wrapper")
    verified_bank.validate_current()
    if verified_bank.manifest.content_digest != precommitment.source_manifest_digest:
        raise ValueError("rebuilt MathNet bank differs from the precommitted source")
    verify_pinned_mathnet_bank_v3(
        source,
        verified_bank,
        selection_seed=seed,
    )
    rows, vault_entries = _selected_material_v4(verified_bank)
    selection_digest = _selection_digest_v4(rows)
    projection_digest = _projection_digest_from_rows_v4(rows)
    vault_raw = _private_vault_record_v4(
        vault_entries,
        selection_digest=selection_digest,
        projection_digest=projection_digest,
        source_manifest_digest=precommitment.source_manifest_digest,
        selector_code_manifest_digest=precommitment.selector_code_manifest.content_digest,
        verified_bank_digest=verified_bank.content_digest,
    )
    vault_sha256 = _sha256(vault_raw)
    gold_input = object.__new__(MathNetRandomExternalGoldAuditInputV4)
    gold_items = tuple(
        MathNetRandomExternalGoldAuditItemV4(
            case_id=row.case_id,
            problem_text=row.problem_text,
            problem_text_digest=row.problem_text_digest,
        )
        for row in rows
    )
    gold_values: tuple[tuple[str, object], ...] = (
        ("items", gold_items),
        ("selection_digest", selection_digest),
        ("projection_digest", projection_digest),
        ("precommitment_record_sha256", precommitment.record_sha256),
        ("beacon_receipt_record_sha256", receipt.record_sha256),
        ("source_manifest_digest", precommitment.source_manifest_digest),
        ("selector_code_manifest_digest", precommitment.selector_code_manifest.content_digest),
        ("verified_bank_digest", verified_bank.content_digest),
        ("private_gold_vault_record_sha256", vault_sha256),
        ("_mint_marker", _GOLD_INPUT_TOKEN_V4),
    )
    for name, value in gold_values:
        object.__setattr__(gold_input, name, value)
    gold_input._validate_fields()
    object.__setattr__(
        gold_input,
        "content_digest",
        _domain_digest(
            gold_input._payload(),
            domain="org.math-research-brain/mathnet-random-external-gold-input/v4",
        ),
    )

    bank = verified_bank.bank
    if type(bank) is not MathNetBankV3:
        raise ValueError("MathNet random cold replay lost its exact bank")
    selection = object.__new__(ColdReplayMathNetRandomSelectionV4)
    selection_values: tuple[tuple[str, object], ...] = (
        ("selected_rows", rows),
        ("gold_audit_input", gold_input),
        ("selection_digest", selection_digest),
        ("precommitment_content_digest", precommitment.content_digest),
        ("precommitment_record_sha256", precommitment.record_sha256),
        ("beacon_receipt_content_digest", receipt.content_digest),
        ("beacon_receipt_record_sha256", receipt.record_sha256),
        ("beacon_round_spec_digest", precommitment.beacon_round_spec.content_digest),
        ("beacon_round_number", precommitment.beacon_round_spec.round_number),
        ("beacon_anchor_payload_sha256", receipt.anchor_payload_sha256),
        ("source_manifest_digest", precommitment.source_manifest_digest),
        ("source_runtime_evidence_digest", precommitment.source_runtime_evidence_digest),
        ("selector_code_manifest_digest", precommitment.selector_code_manifest.content_digest),
        ("verified_bank_digest", verified_bank.content_digest),
        ("verified_bank_public_commitment_digest", bank.public_commitment_digest()),
        ("selection_seed_commitment", bank.selection_seed_commitment),
        ("private_gold_vault_record_sha256", vault_sha256),
        ("confirmatory_source_gate", verified_bank.confirmatory_source_gate),
        ("_mint_marker", _SELECTION_TOKEN_V4),
    )
    for name, value in selection_values:
        object.__setattr__(selection, name, value)
    selection._validate_fields()
    object.__setattr__(
        selection,
        "content_digest",
        _domain_digest(
            selection._payload(),
            domain="org.math-research-brain/mathnet-random-external-capability/v4",
        ),
    )
    return selection, vault_raw


def reveal_and_cold_replay_mathnet_random_external_lane_v4(
    precommitment_bytes: bytes,
    beacon_receipt_bytes: bytes,
    *,
    expected_precommitment_record_sha256: str,
    expected_beacon_receipt_record_sha256: str,
    source: PreparedPinnedMathNetSourceV3,
) -> ColdReplayMathNetRandomSelectionV4:
    """Rebuild and replay the official twelve-case bank, then discard private vault bytes."""

    selection, _private_vault_raw = _cold_replay_material_v4(
        precommitment_bytes,
        beacon_receipt_bytes,
        expected_precommitment_record_sha256=expected_precommitment_record_sha256,
        expected_beacon_receipt_record_sha256=expected_beacon_receipt_record_sha256,
        source=source,
    )
    return selection


def rebuild_mathnet_random_external_gold_vault_record_v4(
    precommitment_bytes: bytes,
    beacon_receipt_bytes: bytes,
    *,
    expected_precommitment_record_sha256: str,
    expected_beacon_receipt_record_sha256: str,
    source: PreparedPinnedMathNetSourceV3,
    capability: ColdReplayMathNetRandomSelectionV4,
    gold_input: MathNetRandomExternalGoldAuditInputV4,
) -> bytes:
    """Reproduce the exact committed private vault by another complete official replay."""

    if type(capability) is not ColdReplayMathNetRandomSelectionV4:
        raise ValueError("private-vault rebuild requires the exact cold-replayed capability")
    capability.validate_current()
    if type(gold_input) is not MathNetRandomExternalGoldAuditInputV4:
        raise ValueError("private-vault rebuild requires the exact body-only gold input")
    gold_input.validate_current()
    replayed, raw = _cold_replay_material_v4(
        precommitment_bytes,
        beacon_receipt_bytes,
        expected_precommitment_record_sha256=expected_precommitment_record_sha256,
        expected_beacon_receipt_record_sha256=expected_beacon_receipt_record_sha256,
        source=source,
    )
    if replayed.content_digest != capability.content_digest or (
        replayed.gold_audit_input.content_digest != gold_input.content_digest
    ):
        raise ValueError("private-vault replay differs from the accepted selection capability")
    if _sha256(raw) != capability.private_gold_vault_record_sha256:
        raise ValueError("reproduced private vault differs from its cold-replay commitment")
    return raw


def external_gold_audit_projection_v4(
    capability: ColdReplayMathNetRandomSelectionV4,
    gold_input: MathNetRandomExternalGoldAuditInputV4,
) -> tuple[dict[str, str], ...]:
    """Return exactly three body-only fields per case for the future blind audit."""

    _reject_legacy_body_card_artifact(capability, "gold-audit capability")
    _reject_legacy_body_card_artifact(gold_input, "gold-audit input")
    if type(capability) is not ColdReplayMathNetRandomSelectionV4:
        raise ValueError("gold projection requires the exact random-lane capability")
    capability.validate_current()
    if type(gold_input) is not MathNetRandomExternalGoldAuditInputV4:
        raise ValueError("gold projection requires the exact random-lane gold input")
    gold_input.validate_current()
    if capability.gold_audit_input.content_digest != gold_input.content_digest:
        raise ValueError("gold projection input differs from the capability")
    return tuple(item.to_dict() for item in gold_input.items)


def open_external_gold_vault_entries_after_replay_v4(
    raw: bytes,
    capability: ColdReplayMathNetRandomSelectionV4,
    gold_input: MathNetRandomExternalGoldAuditInputV4,
) -> tuple[MathNetRandomPrivateGoldEntryV4, ...]:
    """Strictly open the committed vault; the audit layer must enforce terminal timing."""

    if type(capability) is not ColdReplayMathNetRandomSelectionV4:
        raise ValueError("private-vault open requires the exact random-lane capability")
    capability.validate_current()
    if type(gold_input) is not MathNetRandomExternalGoldAuditInputV4:
        raise ValueError("private-vault open requires the exact random-lane gold input")
    gold_input.validate_current()
    if capability.gold_audit_input.content_digest != gold_input.content_digest:
        raise ValueError("private-vault gold input differs from the capability")
    if _sha256(raw) != capability.private_gold_vault_record_sha256 or (
        capability.private_gold_vault_record_sha256
        != gold_input.private_gold_vault_record_sha256
    ):
        raise ValueError("private-vault bytes differ from the replay commitment")
    value = _strict_json(raw, label="MathNet random private vault", canonical=True)
    record = _exact_mapping(
        value,
        {
            "content_digest",
            "entries",
            "lane_id",
            "projection_digest",
            "schema",
            "schema_version",
            "selection_digest",
            "selector_code_manifest_digest",
            "source_manifest_digest",
            "verified_bank_digest",
        },
        "MathNet random private vault",
    )
    payload = dict(record)
    supplied_content = payload.pop("content_digest")
    expected_content = _domain_digest(
        payload,
        domain="org.math-research-brain/mathnet-random-external-private-vault/v4",
    )
    if not hmac.compare_digest(
        _digest(supplied_content, "private-vault content"),
        expected_content,
    ):
        raise ValueError("private-vault semantic digest is stale")
    expected_lineage = {
        "lane_id": MATHNET_RANDOM_EXTERNAL_LANE_ID_V4,
        "projection_digest": gold_input.projection_digest,
        "schema": MATHNET_RANDOM_EXTERNAL_PRIVATE_VAULT_SCHEMA_V4,
        "schema_version": MATHNET_RANDOM_EXTERNAL_LANE_VERSION_V4,
        "selection_digest": capability.selection_digest,
        "selector_code_manifest_digest": capability.selector_code_manifest_digest,
        "source_manifest_digest": capability.source_manifest_digest,
        "verified_bank_digest": capability.verified_bank_digest,
    }
    for key, expected in expected_lineage.items():
        if record[key] != expected:
            raise ValueError(f"private-vault lineage differs at {key}")
    entries_raw = record["entries"]
    if type(entries_raw) is not list or len(entries_raw) != MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4:
        raise ValueError("private vault must contain exactly twelve ordered entries")
    entries: list[MathNetRandomPrivateGoldEntryV4] = []
    for raw_entry in entries_raw:
        item = _exact_mapping(
            raw_entry,
            {
                "candidate_content_digest",
                "case_id",
                "private_gold",
                "problem_text_digest",
                "source_cluster_digest",
            },
            "MathNet random private-vault entry",
        )
        entries.append(
            MathNetRandomPrivateGoldEntryV4(
                case_id=item["case_id"],
                problem_text_digest=item["problem_text_digest"],
                candidate_content_digest=item["candidate_content_digest"],
                source_cluster_digest=item["source_cluster_digest"],
                private_gold=_private_gold_from_dict_v4(item["private_gold"]),
            )
        )
    result = tuple(entries)
    observed_bindings = tuple(
        (
            item.case_id,
            item.problem_text_digest,
            item.candidate_content_digest,
            item.source_cluster_digest,
            item.private_gold.content_digest,
        )
        for item in result
    )
    expected_bindings = tuple(
        (
            row.case_id,
            row.problem_text_digest,
            row.candidate_content_digest,
            row.source_cluster_digest,
            row.private_gold_commitment,
        )
        for row in capability.selected_rows
    )
    if observed_bindings != expected_bindings:
        raise ValueError("private-vault entries differ from the selected case commitments")
    return result


__all__ = [
    "DRAND_QUICKNET_BEACON_ID_V4",
    "DRAND_QUICKNET_CHAIN_HASH_V4",
    "DRAND_QUICKNET_GENESIS_SEED_V4",
    "DRAND_QUICKNET_GENESIS_TIME_V4",
    "DRAND_QUICKNET_PERIOD_SECONDS_V4",
    "DRAND_QUICKNET_PUBLIC_KEY_V4",
    "DRAND_QUICKNET_RELAY_BASE_URLS_V4",
    "DRAND_QUICKNET_SCHEME_V4",
    "MATHNET_RANDOM_EXTERNAL_LANE_ID_V4",
    "MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4",
    "MATHNET_RANDOM_EXTERNAL_SELECTION_POLICY_V4",
    "ColdReplayMathNetRandomSelectionV4",
    "DrandQuicknetRoundSpecV4",
    "DrandRelayByteReceiptV4",
    "ExternallyAnchoredDrandBeaconReceiptV4",
    "MathNetRandomExternalGoldAuditInputV4",
    "MathNetRandomExternalGoldAuditItemV4",
    "MathNetRandomExternalPrecommitmentV4",
    "MathNetRandomExternalSelectedCaseV4",
    "MathNetRandomPrivateGoldEntryV4",
    "MathNetRandomSelectorCodeManifestV4",
    "build_externally_anchored_drand_beacon_receipt_v4",
    "build_mathnet_random_external_precommitment_v4",
    "derive_mathnet_random_selection_seed_v4",
    "external_gold_audit_projection_v4",
    "freeze_current_mathnet_random_selector_code_v4",
    "open_external_gold_vault_entries_after_replay_v4",
    "rebuild_mathnet_random_external_gold_vault_record_v4",
    "render_mathnet_random_beacon_anchor_payload_v4",
    "reveal_and_cold_replay_mathnet_random_external_lane_v4",
]
